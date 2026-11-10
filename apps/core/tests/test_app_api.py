"""Nieuwe API's voor web en app (docs/APP_API.md): seizoenen aan/uit, kwaliteit + custom
formats per bestand, waarom geüpgraded, Seerr-doorgeefluik, app-status/-overzicht/-bediening
en koppelen."""

from __future__ import annotations

import httpx
import pytest
from test_arr_fakes import (
    FakeArr,
    FakeSeerr,
    b1ctx,  # noqa: F401
    make_episode,
    make_episode_file,
    make_series,
    wire,
)

from mediaguard.db import dumps, utcnow
from mediaguard.library.sync import sync_instance
from mediaguard.upgrade_reasons import explain, quality_rank


def _sonarr() -> FakeArr:
    fake = FakeArr("sonarr")
    eps = [make_episode(1, 1, 1, 1, has_file=True, file_id=101),
           make_episode(2, 1, 1, 2, has_file=True, file_id=102),
           make_episode(3, 1, 2, 1, aired_days=-3),
           make_episode(4, 1, 2, 2, aired_days=-2)]
    f101 = make_episode_file(101, 1, 1)
    f101["customFormats"] = [{"id": 7, "name": "MediaGuard · Dual Audio"}]
    f101["customFormatScore"] = 2000
    fake.add_series(make_series(1, "Frieren", 424536, seasons={1: True, 2: True}), eps,
                    [f101, make_episode_file(102, 1, 1, cutoff_not_met=True)])
    fake.quality_profiles = [{"id": 1, "name": "MediaGuard · Anime Dual Audio",
                              "upgradeAllowed": True, "cutoff": 3, "cutoffFormatScore": 2200,
                              "minUpgradeFormatScore": 100,
                              "items": [{"quality": {"id": 3, "name": "WEBDL-1080p"},
                                         "allowed": True}],
                              "formatItems": [{"format": 7, "name": "MediaGuard · Dual Audio",
                                               "score": 2000}]}]
    return fake


@pytest.fixture
def series_env(b1ctx, client):  # noqa: F811
    fake = wire(b1ctx, "sonarr-series", _sonarr())
    import asyncio

    asyncio.run(sync_instance(b1ctx, "sonarr-series"))
    item = b1ctx.db.fetch_one("SELECT id FROM lib_items WHERE instance_id='sonarr-series' "
                              "AND provider_id=1")
    b1ctx.db.execute("UPDATE lib_items SET quality_profile_id=1 WHERE id=?", (item["id"],))
    return {"fake": fake, "item_id": int(item["id"]), "client": client, "ctx": b1ctx}


# -- 1. seizoenen ------------------------------------------------------------------------------
def test_season_off_and_on_with_search(series_env):
    c, fake, iid = series_env["client"], series_env["fake"], series_env["item_id"]
    r = c.put(f"/api/v1/library/{iid}/seasons/2", json={"monitored": False})
    assert r.status_code == 200, r.text
    assert r.json()["monitored"] is False and "niet meer gevolgd" in r.json()["message_nl"]
    assert not fake.episodes[3]["monitored"] and not fake.episodes[4]["monitored"]
    assert fake.episodes[1]["monitored"]  # seizoen 1 ongemoeid
    r = c.put(f"/api/v1/library/{iid}/seasons/2", json={"monitored": True, "search": True})
    assert r.json()["search_started"] and fake.episodes[3]["monitored"]
    assert any(cmd["name"] == "SeasonSearch" for cmd in fake.commands.values())
    assert c.put(f"/api/v1/library/{iid}/seasons/9", json={"monitored": True}).status_code == 404
    audit = series_env["ctx"].db.fetch_one(
        "SELECT * FROM audit_events WHERE action='library.season_monitor' ORDER BY id DESC")
    assert audit is not None


def test_episode_monitor(series_env):
    c, fake, iid = series_env["client"], series_env["fake"], series_env["item_id"]
    r = c.put(f"/api/v1/library/{iid}/episodes/2/monitor", json={"monitored": False})
    assert r.status_code == 200 and "S01E02" in r.json()["message_nl"]
    assert not fake.episodes[2]["monitored"]
    assert c.put(f"/api/v1/library/{iid}/episodes/999/monitor",
                 json={"monitored": True}).status_code in (404, 502)


def test_season_monitor_requires_operator(series_env, anon_client):
    r = anon_client.put(f"/api/v1/library/{series_env['item_id']}/seasons/1",
                        json={"monitored": False})
    assert r.status_code == 401


# -- 2. kwaliteit en waarom --------------------------------------------------------------------
def test_files_quality_and_custom_formats(series_env):
    c, iid = series_env["client"], series_env["item_id"]
    r = c.get(f"/api/v1/library/{iid}/files")
    assert r.status_code == 200, r.text
    data = r.json()
    assert data["profile"]["cutoff"] == "WEBDL-1080p" and data["profile"]["cutoff_score"] == 2200
    f1, f2 = data["files"]
    assert f1["label"] == "S01E01" and f1["score"] == 2000
    assert f1["custom_formats"] == [{"name": "MediaGuard · Dual Audio", "score": 2000}]
    assert f1["quality"]["name"] == "WEBDL-1080p"
    assert f1["quality_cutoff_met"] and not f1["score_cutoff_met"] and not f1["cutoff_met"]
    assert "score 2000 van 2200" in f1["summary_nl"]
    assert not f2["quality_cutoff_met"]


def test_quality_rank_and_explain():
    assert quality_rank("Bluray-2160p Remux") > quality_rank("WEBDL-2160p") \
        > quality_rank("Bluray-1080p") > quality_rank("WEBDL-1080p")
    r = explain({"quality": "WEBDL-1080p", "score": 1500, "custom_formats": ["Engelse dub"]},
                {"quality": "WEBDL-1080p", "score": 2000, "custom_formats": ["Dual Audio"]})
    assert r["cf_added"] == ["Dual Audio"] and r["cf_removed"] == ["Engelse dub"]
    assert "+Dual Audio" in r["reason_nl"] and "1500 → 2000" in r["reason_nl"]
    r = explain({"quality": "WEBDL-1080p", "score": 0}, {"quality": "Bluray-2160p", "score": 0})
    assert r["reason_nl"].startswith("Betere kwaliteit: WEBDL-1080p → Bluray-2160p")
    r = explain({"quality": "WEBDL-1080p", "score": 0},
                {"quality": "WEBDL-1080p", "score": 0, "repack": True})
    assert "REPACK" in r["reason_nl"]
    assert "Onbekend" in explain(None, {"quality": "x"})["reason_nl"]


def _ledger(ctx, hid, at, event, *, size=None, quality="WEBDL-1080p", score=None, cfs=None,
            item=1, owner=1, title=None):
    ctx.db.execute(
        "INSERT INTO storage_ledger_events(instance_id,history_id,at,event,media_kind,owner_id,"
        "item_id,size,quality,source_title,cf_score,custom_formats_json) "
        "VALUES (?,?,?,?,?,?,?,?,?,?,?,?)",
        ("sonarr-series", hid, at, event, "episode", owner, item, size, quality, title, score,
         dumps(cfs) if cfs else None))


def test_upgrade_history_with_reason_and_av1_flag(series_env):
    ctx, c, iid = series_env["ctx"], series_env["client"], series_env["item_id"]
    _ledger(ctx, 1, "2026-11-01T10:00:00Z", "imported", size=1_400_000_000, score=1500,
            cfs=["Engelse dub"], title="Frieren.S01E01.1080p-OLD")
    _ledger(ctx, 2, "2026-11-07T04:11:00Z", "deleted", size=300_000_000)
    _ledger(ctx, 3, "2026-11-07T04:12:00Z", "imported", size=1_600_000_000, score=2000,
            cfs=["Dual Audio"], title="Frieren.S01E01.1080p.Dual-GRPA")
    # het oude bestand was een AV1-versie van MediaGuard
    ctx.db.execute(
        "INSERT INTO encoding_jobs(instance_id,media_kind,provider_id,source_path,status,"
        "policy_json,created_at,updated_at) VALUES ('sonarr-series','episode','1','/tv/x.mkv',"
        "'committed','{}','2026-11-03T00:00:00Z','2026-11-03T00:00:00Z')")
    r = c.get(f"/api/v1/library/{iid}/upgrades?days=365")
    assert r.status_code == 200, r.text
    ups = r.json()["upgrades"]
    assert len(ups) == 1  # de eerste import is geen upgrade
    u = ups[0]
    assert u["cf_added"] == ["Dual Audio"] and u["old"]["score"] == 1500
    assert u["old"]["av1_by_mediaguard"] is True
    assert "+Dual Audio" in u["reason_nl"] and u["source_nl"]


def test_ledger_items_carry_waarom(series_env):
    from mediaguard import storage_ledger

    ctx = series_env["ctx"]
    _ledger(ctx, 11, "2026-11-01T10:00:00Z", "imported", size=1_000, score=100,
            cfs=["Engelse dub"])
    _ledger(ctx, 12, "2026-11-07T04:11:00Z", "deleted", size=1_000)
    _ledger(ctx, 13, "2026-11-07T04:12:00Z", "imported", size=2_000, quality="Bluray-1080p",
            score=100, cfs=["Engelse dub"])
    items = storage_ledger.imports(ctx, "2026-11-07T00:00:00Z", "2026-11-08T00:00:00Z")
    assert items and items[0]["kind"] == "upgrade"
    assert items[0]["waarom"].startswith("Betere kwaliteit: WEBDL-1080p → Bluray-1080p")


def test_record_history_stores_custom_formats(b1ctx):  # noqa: F811
    from mediaguard import storage_ledger

    storage_ledger.record_history(b1ctx, "sonarr-series", "sonarr", [{
        "id": 77, "eventType": "downloadFolderImported", "date": "2026-11-07T04:12:00Z",
        "episodeId": 5, "seriesId": 1, "customFormatScore": 2000,
        "customFormats": [{"id": 1, "name": "Dual Audio"}],
        "quality": {"quality": {"name": "WEBDL-1080p"},
                    "revision": {"version": 2, "isRepack": True}},
        "data": {"size": "1000"}}])
    row = b1ctx.db.fetch_one("SELECT * FROM storage_ledger_events WHERE history_id=77")
    assert row["cf_score"] == 2000 and "Dual Audio" in row["custom_formats_json"]
    assert row["is_repack"] == 1


# -- 3. Seerr ----------------------------------------------------------------------------------
@pytest.fixture
def seerr_env(b1ctx, client):  # noqa: F811
    seerr = FakeSeerr()
    b1ctx.services["seerr_transport"] = seerr.transport()
    r = client.put("/api/v1/seerr/settings", json={"url": "http://seerr:5055",
                                                   "api_key": seerr.api_key})
    assert r.status_code == 200, r.text
    seerr.catalog[("movie", 603)] = {"title": "The Matrix", "releaseDate": "1999-03-31",
                                     "posterPath": "/m.jpg", "overview": "Neo",
                                     "mediaInfo": {"status": 5}}
    seerr.catalog[("tv", 1399)] = {
        "name": "Game of Thrones", "firstAirDate": "2011-04-17", "posterPath": "/g.jpg",
        "mediaInfo": {"status": 4, "seasons": [{"seasonNumber": 1, "status": 5}]},
        "seasons": [{"seasonNumber": 0, "episodeCount": 3},
                    {"seasonNumber": 1, "episodeCount": 10, "name": "Seizoen 1"},
                    {"seasonNumber": 2, "episodeCount": 10, "name": "Seizoen 2"}]}
    return {"seerr": seerr, "client": client, "ctx": b1ctx}


def test_seerr_search_and_discover(seerr_env):
    c = seerr_env["client"]
    r = c.get("/api/v1/seerr/search", params={"q": "the matrix"})
    assert r.status_code == 200, r.text
    res = r.json()["results"]
    assert [x["title"] for x in res] == ["The Matrix"]  # persoon weggefilterd
    assert res[0]["status"] == "beschikbaar" and res[0]["status_nl"] == "Beschikbaar"
    assert res[0]["poster_url"].endswith("/w342/m.jpg") and res[0]["year"] == 1999
    d = c.get("/api/v1/seerr/discover/tv").json()["results"]
    assert d and d[0]["media_type"] == "tv" and d[0]["status"] == "gedeeltelijk"
    assert c.get("/api/v1/seerr/discover/trending").status_code == 200


def test_seerr_details_seasons_and_request(seerr_env):
    c, seerr, ctx = seerr_env["client"], seerr_env["seerr"], seerr_env["ctx"]
    d = c.get("/api/v1/seerr/media/tv/1399").json()
    assert [s["season_number"] for s in d["seasons"]] == [1, 2]  # specials niet
    assert d["seasons"][0]["status"] == "beschikbaar" and d["seasons"][1]["status"] == "niet"
    assert c.post("/api/v1/seerr/request", json={"media_type": "tv",
                                                 "tmdb_id": 1399}).status_code == 422
    r = c.post("/api/v1/seerr/request", json={"media_type": "tv", "tmdb_id": 1399,
                                              "seasons": [2]})
    assert r.status_code == 200, r.text
    assert r.json()["message_nl"] == "Aangevraagd: 1 seizoen"
    assert seerr.requests[-1]["seasons"] == [{"seasonNumber": 2}]
    seerr.request_status = 1
    r = c.post("/api/v1/seerr/request", json={"media_type": "movie", "tmdb_id": 603})
    assert "wacht op goedkeuring" in r.json()["message_nl"]
    # leverstatus van MediaGuard wordt samengevoegd
    now = utcnow()
    ctx.db.execute(
        "INSERT INTO delivery_items(item_key,source,seerr_request_ids_json,media_type,tmdb_id,"
        "title,stage,state,progress_json,created_at,updated_at) VALUES "
        "('movie:tmdb:603','seerr','[2]','movie',603,'The Matrix','searching','active',"
        "'{\"present\":0,\"total\":1}',?,?)", (now, now))
    rows = c.get("/api/v1/seerr/my-requests").json()["results"]
    movie = next(x for x in rows if x["media_type"] == "movie")
    assert movie["title"] == "The Matrix"  # opgehaald via details
    assert movie["delivery"]["state_nl"] == "Wordt gezocht"
    assert movie["seerr_status_nl"] == "Wacht op goedkeuring"


def test_seerr_not_configured(client, ctx):
    r = client.get("/api/v1/seerr/search", params={"q": "x"})
    assert r.status_code == 409 and "SEERR_NOT_CONFIGURED" in r.text


# -- 4. app ------------------------------------------------------------------------------------
def _engine_down(ctx) -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        raise httpx.ConnectError("weg")
    ctx.services["encoder_transport"] = httpx.MockTransport(handler)


def test_app_status_with_engine_down(client, ctx):
    _engine_down(ctx)
    r = client.get("/api/v1/app/status")
    assert r.status_code == 200, r.text
    s = r.json()
    assert s["encoder"]["reachable"] is False and s["emergency_stop"]["active"] is False
    assert "attention" in s and "downloads" in s and s["savings_bytes"] == 0


def test_app_summary_day(client, ctx):
    ctx.db.execute(
        "INSERT INTO encoding_jobs(instance_id,media_kind,provider_id,source_path,status,"
        "source_snapshot_json,staged_output_json,core_verification_json,policy_json,created_at,"
        "updated_at) VALUES ('sonarr-series','episode','1','/tv/Show S01E01.mkv','committed',"
        "'{\"size\": 2000000000}','{\"size\": 500000000}','{\"vmaf\": 96.1}','{}',"
        "'2026-11-06T22:00:00Z','2026-11-06T23:30:00Z')")
    r = client.get("/api/v1/app/summary", params={"date": "2026-11-07"})
    assert r.status_code == 200, r.text
    s = r.json()
    assert s["date"] == "2026-11-07"
    assert s["replaced"] and s["replaced"][0]["saved"] == 1_500_000_000
    assert s["replaced"][0]["vmaf"] == 96.1
    assert "omgezet naar AV1" in s["headline_nl"]
    assert client.get("/api/v1/app/summary", params={"date": "gisteren"}).status_code == 422


def test_app_control_emergency_stop_needs_confirm(client, ctx):
    r = client.post("/api/v1/app/control", json={"action": "emergency_stop_on"})
    assert r.status_code == 409 and "CONFIRM_REQUIRED" in r.text
    r = client.post("/api/v1/app/control", json={"action": "emergency_stop_on",
                                                 "params": {"reason": "test"},
                                                 "confirm": True})
    assert r.status_code == 200 and ctx.state_get("emergency_stop")["active"]
    r = client.post("/api/v1/app/control", json={"action": "emergency_stop_off",
                                                 "confirm": True})
    assert r.status_code == 200 and "hervat" in r.json()["message_nl"]


def test_app_control_lane_pause_calls_engine(client, ctx):
    seen = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append((request.method, request.url.path))
        return httpx.Response(200, json={"ok": True})
    ctx.services["encoder_transport"] = httpx.MockTransport(handler)
    r = client.post("/api/v1/app/control", json={"action": "pause_lane",
                                                 "params": {"lane": "gpu"}, "confirm": True})
    assert r.status_code == 200, r.text
    assert ("POST", "/api/workers/gpu/pauzeren") in seen
    r = client.post("/api/v1/app/control", json={"action": "resume_lane",
                                                 "params": {"lane": "gpu"}})
    assert r.status_code == 200 and ("POST", "/api/workers/gpu/hervatten") in seen
    assert client.post("/api/v1/app/control", json={
        "action": "pause_lane", "params": {"lane": "x"}, "confirm": True}).status_code == 422


def test_app_pairing_creates_key_and_qr(client, ctx):
    ctx.config.set("global", "", "app.public_url", "https://mg.example.nl/", "test")
    info = client.get("/api/v1/app/pairing").json()
    assert info["server_url"] == "https://mg.example.nl" and info["existing_key"] is None
    r = client.post("/api/v1/app/pairing/key")
    assert r.status_code == 200, r.text
    first = r.json()
    assert first["qr_payload"]["url"] == "https://mg.example.nl"
    assert first["qr_payload"]["key"].startswith("mgk_")
    # de sleutel werkt als operator voor de app-API (zonder sessie)
    _engine_down(ctx)
    from starlette.testclient import TestClient

    with TestClient(client.app) as app_client:
        r = app_client.post("/api/v1/app/control", json={"action": "run_autopilot"},
                            headers={"X-Api-Key": first["qr_payload"]["key"]})
        assert r.status_code != 401 and r.status_code != 403
    # opnieuw koppelen trekt de vorige sleutel in
    second = client.post("/api/v1/app/pairing/key").json()
    assert second["key_id"] != first["key_id"]
    old = ctx.db.fetch_one("SELECT revoked_at FROM api_keys WHERE id=?", (first["key_id"],))
    assert old["revoked_at"] is not None
    assert client.get("/api/v1/app/pairing").json()["existing_key"]["id"] == second["key_id"]


# -- 4b. app: echte cijfers, wacht-op-jou met knoppen, telefoonkanaal ----------------------------
def _app_key(client) -> str:
    return client.post("/api/v1/app/pairing/key").json()["qr_payload"]["key"]


def _engine(ctx, *, workers=None, health=None) -> list[tuple[str, str]]:
    seen: list[tuple[str, str]] = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append((request.method, request.url.path))
        if request.url.path == "/api/workers":
            return httpx.Response(200, json=workers or [])
        if request.url.path.endswith("/health"):
            return httpx.Response(200, json=health or {"lanes_paused": []})
        return httpx.Response(404, json={"detail": "x"})
    ctx.services["encoder_transport"] = httpx.MockTransport(handler)
    return seen


def test_app_status_lanes_have_real_counts_and_eta(client, ctx):
    now = utcnow()
    ctx.db.execute(
        "INSERT INTO encoding_jobs(encoder_job_id,instance_id,media_kind,provider_id,source_path,"
        "status,progress,policy_json,created_at,updated_at) VALUES ('55','sonarr-series',"
        "'episode','1','/tv/Show S01E01.mkv','running',0.1,'{}',?,?)", (now, now))
    _engine(ctx, workers=[
        {"baan": "cpu", "aantal": 1, "gepauzeerd": True,
         "pauze_reden": "automatisch: cache heeft nog 3 GB vrij", "actieve_jobs": []},
        {"baan": "gpu", "aantal": 4, "gepauzeerd": False, "pauze_reden": None,
         "actieve_jobs": [
             {"job_id": 55, "percent": 42.5, "eta_seconden": 900, "bestand": "/tv/x.mkv"},
             {"job_id": 77, "percent": 10, "eta_seconden": 0, "bestand": "/films/Film.mkv"}]}],
        health={"lanes_paused": [{"lane": "cpu", "reason": "automatisch: cache vol"}]})
    s = client.get("/api/v1/app/status").json()
    lanes = {x["lane"]: x for x in s["encoder"]["lanes"]}
    assert [x["lane"] for x in s["encoder"]["lanes"]] == ["gpu", "cpu"]
    assert lanes["gpu"]["running"] == 2 and lanes["gpu"]["slots"] == 4
    assert lanes["cpu"]["paused"] and lanes["cpu"]["reason_nl"] == "cache heeft nog 3 GB vrij"
    run = {r["engine_job_id"]: r for r in s["encoder"]["running"]}
    assert run["55"]["lane"] == "gpu" and run["55"]["eta_seconds"] == 900
    assert run["55"]["percent"] == 42.5 and run["55"]["title"] == "Show S01E01.mkv"
    # de engine draait een job die core niet als lopend kent: toch in de lijst
    assert run["77"]["title"] == "Film.mkv" and run["77"]["eta_seconds"] is None
    # problemen met de AV1-engine staan nu ook in 'Wacht op jou' van de app
    keys = [i["key"] for i in s["attention"]["items"]]
    assert "encoder.paused.cpu" in keys
    item = next(i for i in s["attention"]["items"] if i["key"] == "encoder.paused.cpu")
    assert item["detail_nl"] and item["href"] == "/av1"


def test_app_status_noodstop_pauses_eta(client, ctx):
    now = utcnow()
    ctx.db.execute(
        "INSERT INTO encoding_jobs(encoder_job_id,instance_id,media_kind,provider_id,source_path,"
        "status,progress,policy_json,created_at,updated_at) VALUES ('55','sonarr-series',"
        "'episode','1','/tv/a.mkv','running',0.5,'{}',?,?)", (now, now))
    _engine(ctx, workers=[{"baan": "gpu", "aantal": 4, "gepauzeerd": False, "actieve_jobs": [
        {"job_id": 55, "percent": 50, "eta_seconden": 600}]}])
    ctx.state_set("emergency_stop", {"active": True, "reason": "test", "since": now})
    s = client.get("/api/v1/app/status").json()
    assert s["emergency_stop"]["active"] is True
    r = s["encoder"]["running"][0]
    assert r["phase_nl"] == "Gepauzeerd (noodstop)" and r["eta_seconds"] is None


def test_app_status_attention_actions_follow_role(client, ctx):
    _engine_down(ctx)
    ctx.config.set("global", "", "library.write_enabled", False, "test")
    s = client.get("/api/v1/app/status").json()  # admin-sessie
    item = next(i for i in s["attention"]["items"] if i["key"] == "library.locked")
    assert item["actions"] and item["actions"][0]["path"] == "/recovery/library-write"
    assert item["actions"][0]["confirm_nl"] and item["admin_actions"] == 0
    key = _app_key(client)
    from starlette.testclient import TestClient

    with TestClient(client.app) as app_client:
        s = app_client.get("/api/v1/app/status", headers={"X-Api-Key": key}).json()
    item = next(i for i in s["attention"]["items"] if i["key"] == "library.locked")
    # de app-sleutel (operator) krijgt de beheerdersknop niet, wel de uitleg
    assert item["actions"] == [] and item["admin_actions"] == 1 and item["detail_nl"]


def test_app_recent_is_rolling_24_hours(client, ctx):
    from datetime import UTC, datetime, timedelta

    def at(hours: float) -> str:
        return (datetime.now(UTC) - timedelta(hours=hours)).strftime("%Y-%m-%dT%H:%M:%SZ")

    for path, hours in (("/tv/Nieuw S01E01.mkv", 2), ("/tv/Oud S01E01.mkv", 30)):
        ctx.db.execute(
            "INSERT INTO encoding_jobs(instance_id,media_kind,provider_id,source_path,status,"
            "source_snapshot_json,staged_output_json,policy_json,created_at,updated_at) VALUES "
            "('sonarr-series','episode','1',?,'committed','{\"size\": 2000}',"
            "'{\"size\": 500}','{}',?,?)", (path, at(hours), at(hours)))
    r = client.get("/api/v1/app/recent")
    assert r.status_code == 200, r.text
    s = r.json()
    assert s["window"] == "24h" and s["label_nl"] == "Laatste 24 uur"
    assert [x["title"] for x in s["replaced"]] == ["Nieuw S01E01.mkv"]
    assert s["headline_nl"].startswith("Laatste 24 uur: 1 omgezet naar AV1")
    assert "unexplained_bytes" in s["storage"]


def test_app_summary_unexplained_from_snapshots(client, ctx):
    from datetime import UTC, datetime, timedelta

    from mediaguard import storage_ledger
    from mediaguard.mobile import TZ

    day = (datetime.now(TZ) - timedelta(days=3)).date()
    start = datetime(day.year, day.month, day.day, tzinfo=TZ).astimezone(UTC)
    end = start + timedelta(days=1)
    gb = 1024**3
    storage_ledger.record_snapshot(ctx, free=100 * gb, downloads=5 * gb, at=start)
    storage_ledger.record_snapshot(ctx, free=90 * gb, downloads=6 * gb, at=end)
    mid = (start + timedelta(hours=12)).strftime("%Y-%m-%dT%H:%M:%SZ")
    ctx.db.execute(
        "INSERT INTO encoding_jobs(instance_id,media_kind,provider_id,source_path,status,"
        "source_snapshot_json,staged_output_json,policy_json,created_at,updated_at) VALUES "
        "('sonarr-series','episode','1','/tv/a.mkv','committed',?,?,'{}',?,?)",
        (dumps({"size": 3 * gb}), dumps({"size": 1 * gb}), mid, mid))
    s = client.get("/api/v1/app/summary", params={"date": day.isoformat()}).json()
    # 10 GB minder vrij; verklaard: AV1 −2 GB en downloads +1 GB → 11 GB niet verklaard
    assert s["storage"]["unexplained_bytes"] == 11 * gb
    # zonder metingen: onbekend (geen nep-0)
    other = (day - timedelta(days=5)).isoformat()
    s = client.get("/api/v1/app/summary", params={"date": other}).json()
    assert s["storage"]["unexplained_bytes"] is None


def test_app_pairing_enables_phone_channel(client, ctx):
    r = client.post("/api/v1/app/pairing/key").json()
    ntfy = r["qr_payload"]["ntfy"]
    assert ntfy["topic"].startswith("mediaguard-") and ntfy["enabled"] is False
    row = ctx.db.fetch_one("SELECT enabled FROM notify_channels WHERE kind='ntfy'")
    assert row["enabled"] == 0
    from starlette.testclient import TestClient

    key = r["qr_payload"]["key"]
    with TestClient(client.app) as app_client:
        res = app_client.post("/api/v1/app/paired", headers={"X-Api-Key": key},
                              json={"ntfy_topic": ntfy["topic"], "app_version": "1.2.1"})
        assert res.status_code == 200, res.text
        assert res.json()["ntfy"] == {**ntfy, "enabled": True}
        # handmatig gekoppeld (zonder onderwerp): de app krijgt het kanaal alsnog
        res = app_client.post("/api/v1/app/paired", headers={"X-Api-Key": key}, json={})
        assert res.json()["ntfy"]["topic"] == ntfy["topic"]
    row = ctx.db.fetch_one("SELECT enabled FROM notify_channels WHERE kind='ntfy'")
    assert row["enabled"] == 1
    assert client.get("/api/v1/app/pairing").json()["device"]["app_version"] is None
    assert ctx.db.fetch_one("SELECT 1 FROM audit_events WHERE action='app.paired'")


def test_app_test_notification_goes_to_phone_channel(client, ctx):
    sent: list[str] = []

    def rec(request: httpx.Request) -> httpx.Response:
        sent.append(str(request.url))
        return httpx.Response(200, json={"id": "x"})
    ctx.services["notify_transport"] = httpx.MockTransport(rec)
    ctx.db.execute("DELETE FROM notify_channels")  # nog geen telefoonkanaal
    r = client.post("/api/v1/app/control", json={"action": "test_notification"})
    assert r.status_code == 409
    topic = client.post("/api/v1/app/pairing/key").json()["qr_payload"]["ntfy"]["topic"]
    r = client.post("/api/v1/app/control", json={"action": "test_notification",
                                                 "params": {"topic": topic}})
    assert r.status_code == 200, r.text
    assert r.json()["ok"] is True and "staat uit" in r.json()["message_nl"]
    assert sent and topic in sent[0]


def test_phone_channel_only_for_app_key_and_admin(client, ctx):
    """Kanaalbeheer is admin-only. Een gewone operator (web-sessie of eigen sleutel) mag het
    telefoonkanaal niet aanzetten of testen en ziet het onderwerp niet. De app-sleutel krijgt
    alleen het automatisch aangemaakte kanaal: een kanaal dat de beheerder zelf instelde en
    uitzette, blijft uit."""
    from starlette.testclient import TestClient

    from mediaguard.notify import service

    ctx.db.execute("DELETE FROM notify_channels")
    own = service.create_channel(ctx, "ntfy", "Eigen ntfy",
                                 {"server": "https://ntfy.sh", "topic": "beheerder-geheim"}, {},
                                 enabled=False)
    assert client.post("/api/v1/auth/users", json={
        "username": "operator9", "password": "operator-wachtwoord", "role": "operator"}
    ).status_code == 200
    with TestClient(client.app) as op:
        r = op.post("/api/v1/auth/login", json={"username": "operator9",
                                                 "password": "operator-wachtwoord"})
        op.headers["X-CSRF-Token"] = r.json()["csrf_token"]
        r = op.post("/api/v1/app/paired", json={"ntfy_topic": "beheerder-geheim"})
        assert r.status_code == 403 and "beheerder-geheim" not in r.text
        assert "telefoonkanaal" in r.json()["detail"]
        r = op.post("/api/v1/app/control", json={"action": "test_notification",
                                                  "params": {"topic": "beheerder-geheim"}})
        assert r.status_code == 403 and "beheerder-geheim" not in r.text
    other = client.post("/api/v1/auth/api-keys", json={"name": "script", "role": "operator"})
    assert other.status_code == 200, other.text
    with TestClient(client.app) as c:
        assert c.post("/api/v1/app/paired", headers={"X-Api-Key": other.json()["key"]},
                      json={}).status_code == 403
    key = _app_key(client)
    with TestClient(client.app) as app_client:
        r = app_client.post("/api/v1/app/paired", headers={"X-Api-Key": key},
                            json={"ntfy_topic": "beheerder-geheim"})
        assert r.status_code == 200, r.text
        assert r.json()["ntfy"]["topic"] != "beheerder-geheim"
    assert not service.get_channel(ctx, int(own["id"]))["enabled"]


def test_revoked_app_key_does_not_block_new_key(client, ctx):
    _engine_down(ctx)
    old = _app_key(client)
    new = _app_key(client)  # trekt de oude in
    from starlette.testclient import TestClient

    with TestClient(client.app) as app_client:
        for _ in range(15):  # de telefoon pollt nog even met de oude sleutel
            r = app_client.get("/api/v1/auth/me", headers={"X-Api-Key": old})
            assert r.status_code == 401
        r = app_client.get("/api/v1/auth/me", headers={"X-Api-Key": new})
        assert r.status_code == 200, r.text
        # gokken blijft wel begrensd
        for i in range(10):
            app_client.get("/api/v1/auth/me", headers={"X-Api-Key": f"mgk_gok{i}"})
        r = app_client.get("/api/v1/auth/me", headers={"X-Api-Key": new})
        assert r.status_code == 429


def test_app_status_attention_text_is_plain(client, ctx, monkeypatch):
    """Uitleg en regels in de app zonder ruwe ISO-tijden (zoals plainText in de web)."""
    from mediaguard import attention

    async def fake_collect(_ctx):
        return [{"key": "x.test", "severity": "warning", "title_nl": "Test",
                 "detail_nl": "Vast sinds 2026-11-04T12:01:25Z", "href": "/av1", "count": 1,
                 "actions": [], "rows": [{"id": 7, "title": "a.mkv",
                                          "detail": "laatste poging 2026-11-04T12:01:25Z",
                                          "actions": []}]}]

    monkeypatch.setattr(attention, "collect", fake_collect)
    _engine_down(ctx)
    item = client.get("/api/v1/app/status").json()["attention"]["items"][0]
    assert "T12:01" not in item["detail_nl"] and "4 nov 13:01" in item["detail_nl"]
    assert item["rows"][0]["detail"] == "laatste poging 4 nov 13:01"
    assert item["rows"][0]["id"] == "7"
