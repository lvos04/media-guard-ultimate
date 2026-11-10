"""Rapport 5 (core): releaseblokkade, verdwijnende downloads, bronstatistiek, vangnet,
opslagprognose, directe herkansing, leveringscheck, API-sleutelgebruik en migratiezin."""

import asyncio
import contextlib
from datetime import UTC, datetime, timedelta

import httpx

from mediaguard.downloads import failures, holds
from mediaguard.instances import registry


class FakeArr:
    app = "sonarr"

    def __init__(self, history=None):
        self.removed, self.posts, self.puts, self.history = [], [], [], history or []
        self.history_calls: list[int] = []
        self.profiles: list[dict] = []

    async def queue_remove(self, queue_id, *, remove_from_client=True, blocklist=False):
        self.removed.append((queue_id, remove_from_client, blocklist))

    async def history_for_movie(self, pid):
        self.history_calls.append(pid)
        return self.history

    async def history_for_episode(self, pid, _limit=30):
        self.history_calls.append(pid)
        return self.history

    async def get(self, path, **_):
        assert path == "releaseprofile"
        return self.profiles

    async def post(self, path, json=None, **_):
        self.posts.append(path)
        if path == "releaseprofile":
            self.profiles.append({**json, "id": 1})

    async def put(self, path, json=None, **_):
        self.puts.append(path)


def _patch(monkeypatch, fake):
    @contextlib.asynccontextmanager
    async def client(_ctx, _iid, **_kw):
        yield fake

    monkeypatch.setattr(registry, "arr_client", client)


def _sab_gone(ctx):
    from mediaguard.provisioning import store

    store.save(ctx, {"download_clients": [{"name": "SAB", "implementation": "Sabnzbd",
                                           "host": "sab", "port": 8080, "api_key": "k"}]}, "t")
    ctx.services["failure_lookup_transport"] = httpx.MockTransport(
        lambda r: httpx.Response(200, json={"queue": {"slots": []}, "history": {"slots": []}}))


def _episode_wanted(ctx, provider_id=101, series_id=7):
    return ctx.db.execute_insert(
        "INSERT INTO wanted_items(instance_id,kind,provider_id,item_provider_id,reason,status,"
        "title,last_reason_code,first_seen_at,updated_at) VALUES ('sonarr-anime','episode',?,"
        "?,'missing','wanted','Dr. Steen S01E05','import-failed',?,?)",
        (provider_id, series_id, "2026-11-04", "2026-11-04"))


def test_t5_episode_history_uses_episode_id_not_series_id(ctx, monkeypatch):
    """T5: vroeger werd de historie van 'aflevering = serie-ID' gelezen (verkeerde grab)."""
    _sab_gone(ctx)
    fake = FakeArr(history=[{"id": 55, "eventType": "grabbed", "date": "2026-11-04T12:01:25Z",
                             "downloadId": "SABnzbd_nzo_9", "sourceTitle": "Dr.Steen.S01E05",
                             "data": {"releaseGroup": "GRPB"}}])
    _patch(monkeypatch, fake)
    _episode_wanted(ctx, provider_id=101, series_id=7)
    asyncio.run(failures.handle_grabbed_without_import(ctx))
    assert fake.history_calls == [101]


def test_t5_three_vanished_pauses_item_and_notifies(ctx, monkeypatch):
    _sab_gone(ctx)
    for n in range(3):
        fake = FakeArr(history=[{"id": 60 + n, "eventType": "grabbed",
                                 "date": "2026-11-04T12:01:25Z",
                                 "downloadId": f"SABnzbd_nzo_{n}", "sourceTitle": f"R{n}",
                                 "data": {"releaseGroup": f"G{n}"}}])
        _patch(monkeypatch, fake)
        ctx.db.execute("DELETE FROM wanted_items")
        _episode_wanted(ctx)
        asyncio.run(failures.handle_grabbed_without_import(ctx))
    key = holds.item_key("sonarr-anime", "episode", 101)
    hold = holds.hold_for(ctx, key)
    assert hold and hold["kind"] == "vanished" and "verdwijnen" in hold["reason_nl"]
    note = ctx.db.fetch_one("SELECT title FROM notifications WHERE title LIKE "
                            "'Downloads verdwijnen steeds%'")
    assert note is not None
    # backfill slaat het item over zolang de pauze loopt
    from mediaguard.wanted.backfill import _held

    assert _held(ctx, {"instance_id": "sonarr-anime", "kind": "episode", "provider_id": 101})


def test_t4_refused_release_blocked_by_title_and_upgrade_paused(ctx, monkeypatch):
    fake = FakeArr()
    _patch(monkeypatch, fake)
    rows = [{"id": 7, "instance_id": "radarr-films", "awaiting_import": True,
             "title": "Jaar in Delft (2025)", "release": "Jaar.in.Delft.2025.REPACK.2160p",
             "messages": ["Not a Custom Format upgrade"], "download_id": "SABnzbd_nzo_1",
             "movie_id": 3, "state": "importblocked"}]
    asyncio.run(failures.handle_queue(ctx, rows))
    assert fake.removed == [(7, True, True)]
    prof = fake.profiles[0]
    assert prof["name"] == failures.REFUSED_PROFILE
    assert prof["ignored"] == [r"/^Jaar\.in\.Delft\.2025\.REPACK\.2160p$/i"]
    hold = holds.hold_for(ctx, holds.item_key("radarr-films", "movie", 3))
    assert hold and hold["kind"] == "refused"


def test_m7_source_health_success_rates(ctx):
    events = [
        {"eventType": "grabbed", "downloadId": "a", "date": "2026-11-05T10:00:00Z",
         "data": {"indexer": "NZBgeek", "releaseGroup": "GRPB"}},
        {"eventType": "downloadFolderImported", "downloadId": "a",
         "date": "2026-11-05T10:30:00Z"},
        {"eventType": "grabbed", "downloadId": "b", "date": "2026-11-05T11:00:00Z",
         "data": {"indexer": "NZBgeek", "releaseGroup": "GRPC"}},
        {"eventType": "downloadFailed", "downloadId": "b", "date": "2026-11-05T11:30:00Z"},
    ]
    now = datetime.now(UTC).isoformat()
    for e in events:
        e["date"] = now
    failures.record_outcomes(ctx, events)
    h = failures.source_health(ctx, 7)
    geek = next(r for r in h["indexer"] if r["name"] == "NZBgeek")
    assert geek["grabs"] == 2 and geek["imported"] == 1 and geek["success_rate"] == 0.5


def test_m7_downloads_health_endpoint(client):
    r = client.get("/api/v1/downloads/health")
    assert r.status_code == 200
    body = r.json()
    assert {"sources_7d", "sources_30d", "client_errors", "paused_items"} <= set(body)


def test_m5_safety_net_keeps_originals_in_direct_mode(ctx):
    from mediaguard.recovery import service

    ctx.config.set("global", "", "quarantine.mode", "immediate_after_check", "t")
    now = datetime.now(UTC)
    txn = ctx.db.execute_insert(
        "INSERT INTO replacement_transactions(kind,library_path,final_path,staged_path,"
        "source_snapshot_json,state,created_at,updated_at) VALUES ('replace','/a','/a','/s',"
        "'{}','committed',?,?)", (now.isoformat(), now.isoformat()))
    for age_h, size in ((1, 10), (60, 20)):
        created = (now - timedelta(hours=age_h)).isoformat(timespec="seconds")
        item = ctx.db.execute_insert(
            "INSERT INTO quarantine_items(transaction_id,original_path,quarantine_path,method,"
            "status,size,created_at) VALUES (?,?,?,'move','restorable',?,?)",
            (txn, f"/m/{age_h}.mkv", f"/q/{age_h}.mkv", size, created))
        ctx.db.execute("INSERT INTO quarantine_release_checks(item_id,status,checks_json,"
                       "checked_at,attempts) VALUES (?,'passed','[]',datetime('now'),1)",
                       (item,))
    picked = service.purge_candidates(ctx, now=now)
    assert [p["original_path"] for p in picked] == ["/m/60.mkv"]
    assert picked[0]["purge_reason"] == "vangnet verlopen"
    # vangnet uit: alles direct
    ctx.config.set("global", "", "quarantine.safety_net_hours", 0, "t")
    assert len(service.purge_candidates(ctx, now=now)) == 2


def test_m6_forecast_slope_and_attention(ctx, monkeypatch):
    from mediaguard import storage_forecast
    from mediaguard.library import api as lib_api

    today = datetime.now(UTC).date()
    for i in range(10, -1, -1):
        day = (today - timedelta(days=i)).isoformat()
        free = 30 * 1024**4 - (10 - i) * 600 * 1024**3  # 600 GB per dag minder
        ctx.state_set(f"library_space:{day}", {"disks": [{"path": "/data/Media", "free": free,
                                                          "total": 100 * 1024**4}]})
    monkeypatch.setattr(lib_api, "free_space_snapshot", lambda _ctx: {"disks": [
        {"path": "/data/Media", "free": 24 * 1024**4, "total": 100 * 1024**4}]})
    fc = storage_forecast.forecast(ctx)
    d = fc["disks"][0]
    assert d["enough_data"] and 35 < d["days_left"] < 50
    items = storage_forecast.attention_items(ctx)
    assert items and items[0]["severity"] == "warning" and "Schijf vol" in items[0]["title_nl"]


def test_t9_untitled_and_announced_movies_not_released():
    from mediaguard.delivery.engine import movie_released

    now = datetime.now(UTC)
    old = (now - timedelta(days=800)).isoformat()
    assert not movie_released({"title": "Spiegelpaleis 4", "_request_title":
                               "Untitled Spiegelpaleis 4", "inCinemas": old}, now)
    assert not movie_released({"title": "X", "status": "announced", "digitalRelease": old}, now)
    assert not movie_released({"title": "X", "year": now.year + 1, "digitalRelease": old}, now)
    assert movie_released({"title": "X", "status": "released", "digitalRelease": old}, now)


def test_t8_retry_due_paths_are_resubmitted_directly(ctx, monkeypatch, tmp_path):
    from mediaguard.encoding import autopilot
    from mediaguard.encoding import service as enc

    media = tmp_path / "Dromenvanger.mkv"
    media.write_bytes(b"x")
    calls = []
    monkeypatch.setattr(autopilot, "retry_states", lambda _ctx: {
        str(media): {"state": "retry", "kind": "environment", "job_id": 1,
                     "error": "PermissionError: [Errno 1]"}})
    monkeypatch.setattr(enc, "get_job", lambda _ctx, _id: {
        "instance_id": "radarr-films", "media_kind": "movie_file", "provider_id": "9",
        "decision": {"movie_id": 5, "refresh": {"command": "RefreshMovie"}}})

    async def submit(_ctx, **kw):
        calls.append(kw)
        return {"id": 2, "status": "submitted"}

    monkeypatch.setattr(enc, "submit", submit)
    done, skipped = asyncio.run(autopilot._resubmit_due(ctx, 2))
    assert done == 1 and skipped == 0
    assert calls[0]["path"] == str(media) and calls[0]["movie_id"] == 5


def test_t12_badge_counts_attention(client):
    r = client.get("/api/v1/notifications/count")
    assert r.status_code == 200
    body = r.json()
    assert body["badge"] == body["unread"] + body["attention"]


def test_m13_api_key_usage_summary(ctx):
    from mediaguard import apikeys
    from mediaguard.audit import audit

    apikeys.create_key(ctx, name="mso", role="operator", expires_in_days=None, actor="admin")
    audit(ctx.db, actor="apikey:mso", action="wanted.search_now", target_type="wanted",
          target_id=1)
    keys = apikeys.list_keys(ctx)
    mso = next(k for k in keys if k["name"] == "mso")
    assert mso["usage"]["actions_7d"] == 1
    assert mso["usage"]["by_action"][0]["action_nl"] == "Ontbrekend: nu zoeken"
    assert "key_hash" not in mso


def test_m14_blocking_sentence_names_titles(ctx, monkeypatch):
    from mediaguard.migration import autopilot, store

    monkeypatch.setattr(store, "latest_report", lambda _c, _r, _k, _t=None: {
        "summary": {"missing_titles": ["Dromenvanger", "De Zandloper", "Rode Haven"]}})
    text = autopilot._blocking_sentence(
        ctx, 1, [{"step": "scan", "scope": "radarr-films", "status": "blocked"}], 1)
    assert text.startswith("Nog nodig:") and "3 titels ontbreken" in text
    assert "Dromenvanger, De Zandloper, Rode Haven" in text
