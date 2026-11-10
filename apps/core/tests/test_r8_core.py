"""Rapport 8 (v1.1.1): plan voedt de GPU, herkansingen worden geadopteerd, tellers kloppen."""

from __future__ import annotations

import asyncio

from mediaguard.db import dumps, utcnow


def _plan(ctx) -> int:
    now = utcnow()
    return ctx.db.execute_insert(
        "INSERT INTO optimization_plans(status,created_at,updated_at) VALUES ('running',?,?)",
        (now, now))


def _opt_item(ctx, plan: int, action: str, prio: int, path: str) -> int:
    return ctx.db.execute_insert(
        "INSERT INTO optimization_items(plan_id,priority,instance_id,media_kind,provider_id,"
        "title,path,size,action,bucket,reason_nl,context_json) VALUES "
        "(?,?,'radarr-films','movie','1','t',?,1,?,'b','r',?)",
        (plan, prio, path, action, dumps({"movie_id": 1})))


def test_a7_av1_items_flow_while_downloads_are_full(ctx, monkeypatch):
    from mediaguard.optimization import runner

    plan = _plan(ctx)
    for i in range(6):  # eerste batch op prioriteit = alleen zoekitems
        _opt_item(ctx, plan, "quality-upgrade", 0, f"/m/s{i}.mkv")
    av1 = _opt_item(ctx, plan, "av1", 9, "/m/a.mkv")
    ctx.config.set("global", "", "optimization.batch_size", 3, "t")
    monkeypatch.setattr(runner, "in_window", lambda _ctx: True)
    monkeypatch.setattr(runner, "limits_state", lambda _ctx: {
        "searches_last_hour": 0, "bytes_today": 0, "active_encodes": 0,
        "active_downloads": 0, "staging_free_bytes": 10**15})

    async def busy(_ctx):
        return 99  # downloadlimiet vol

    async def fake_submit(_ctx, **_kw):
        return {"id": 42, "status": "submitted"}

    monkeypatch.setattr(runner, "live_active_downloads", busy)
    monkeypatch.setattr(runner.enc, "submit", fake_submit)
    res = asyncio.run(runner.run_batch(ctx, plan))
    row = ctx.db.fetch_one("SELECT status FROM optimization_items WHERE id=?", (av1,))
    assert row["status"] == "encode_submitted"
    assert res["processed"] == 1 and "max actieve downloads bereikt" in res["deferred"]


def test_a7_live_count_uses_clients(ctx, monkeypatch):
    from mediaguard.downloads import live
    from mediaguard.optimization import runner

    async def clients(_ctx):
        return ([{"status": "downloading", "category": "movies"},
                 {"status": "queued", "category": "movies"},
                 {"status": "paused", "category": "movies"},
                 {"status": "downloading", "category": "iets-anders"}], [])

    monkeypatch.setattr(live, "client_downloads", clients)
    monkeypatch.setattr("mediaguard.downloads.janitor.managed_categories",
                        lambda _ctx, _cfg: {"movies"})
    assert asyncio.run(runner.live_active_downloads(ctx)) == 2


class _FakeEngine:
    def __init__(self, jobs):
        self.jobs, self.rejected_ids = jobs, []

    async def awaiting_commit(self):
        return list(self.jobs)

    async def get_job(self, eid):
        return self.jobs[int(eid)]

    async def rejected(self, eid, reason=""):
        self.rejected_ids.append(int(eid))
        return {}

    async def committed(self, eid):
        return {}


def _enc_job(ctx, path: str, eid: str, status: str = "failed") -> int:
    now = utcnow()
    jid = ctx.db.execute_insert(
        "INSERT INTO encoding_jobs(source_path,status,error,encoder_job_id,policy_json,"
        "created_at,updated_at) VALUES (?,?,?,?,'{}',?,?)",
        (path, status, "Geen voortgang in 15 minuten", eid, now, now))
    ctx.db.execute("UPDATE encoding_jobs SET correlation_id=? WHERE id=?", (f"mg-enc-{jid}", jid))
    return jid


def test_a6_successful_engine_retry_is_adopted(ctx, monkeypatch, tmp_path):
    from mediaguard.encoding import service

    src = tmp_path / "bloemendaal.mkv"
    src.write_bytes(b"x" * 10)
    jid = _enc_job(ctx, str(src), "108")
    engine = _FakeEngine({108: {"status": "awaiting_commit", "correlation_id": f"mg-enc-{jid}",
                                "source_snapshot": {"size": 10}}})
    synced = []

    async def fake_sync(_ctx, job_id):
        synced.append(job_id)
        return {}

    monkeypatch.setattr(service, "get_encoder_client", lambda _ctx: engine)
    monkeypatch.setattr(service, "sync_job", fake_sync)
    res = asyncio.run(service.reconcile_engine_commits(ctx))
    row = ctx.db.fetch_one("SELECT status, error FROM encoding_jobs WHERE id=?", (jid,))
    assert res["adopted"] == 1 and synced == [jid]
    assert row["status"] == "verifying" and row["error"] is None


def test_a6_new_engine_id_relinked_and_changed_source_rejected(ctx, monkeypatch, tmp_path):
    from mediaguard.encoding import service

    src = tmp_path / "a.mkv"
    src.write_bytes(b"x" * 10)
    jid = _enc_job(ctx, str(src), "50")
    changed = tmp_path / "b.mkv"
    changed.write_bytes(b"y" * 3)
    jid2 = _enc_job(ctx, str(changed), "60")
    engine = _FakeEngine({
        51: {"status": "awaiting_commit", "correlation_id": f"mg-enc-{jid}",
             "source_snapshot": {"size": 10}},
        61: {"status": "awaiting_commit", "correlation_id": f"mg-enc-{jid2}",
             "source_snapshot": {"size": 99}}})

    async def fake_sync(_ctx, job_id):
        return {}

    monkeypatch.setattr(service, "get_encoder_client", lambda _ctx: engine)
    monkeypatch.setattr(service, "sync_job", fake_sync)
    res = asyncio.run(service.reconcile_engine_commits(ctx))
    assert res["adopted"] == 1 and engine.rejected_ids == [61]
    row = ctx.db.fetch_one("SELECT encoder_job_id, status FROM encoding_jobs WHERE id=?", (jid,))
    assert row["encoder_job_id"] == "51" and row["status"] == "verifying"
    assert ctx.db.fetch_one("SELECT status FROM encoding_jobs WHERE id=?",
                            (jid2,))["status"] == "failed"


def test_a5_idle_lane_becomes_attention_and_one_notification(ctx, monkeypatch):
    """Het aandachtspunt komt uit de engine; de melding komt van de bewaking op de
    achtergrond (verbeterlijst 1), niet meer als bijwerking van het openen van de app."""
    from mediaguard import attention, monitor
    from mediaguard.encoding import client as enc_client

    class Engine:
        async def health(self):
            return {"lanes_paused": [], "baan_stilstand": [
                {"baan": "cpu", "reden": "wacht op werkgeheugen: 11,6 GB vrij", "minuten": 95,
                 "wachtend": 8, "sinds": "2026-11-06T16:25:00+00:00"}]}

    monkeypatch.setattr(enc_client, "get_encoder_client", lambda _ctx: Engine())
    items = asyncio.run(attention._encoder(ctx))
    idle = [i for i in items if i["key"] == "encoder.idle.cpu"]
    assert idle and "1 u 35 min" in idle[0]["title_nl"] and "werkgeheugen" in idle[0]["detail_nl"]
    assert idle[0]["push"] is True
    assert ctx.db.fetch_one("SELECT COUNT(*) AS n FROM notifications")["n"] == 0
    monitor.sync_alerts(ctx, monitor._alerts(items, None))
    items = asyncio.run(attention._encoder(ctx))
    monitor.sync_alerts(ctx, monitor._alerts(items, None))  # tweede ronde: geen tweede melding
    n = ctx.db.fetch_one("SELECT COUNT(*) AS n FROM notifications WHERE title LIKE "
                         "'AV1 CPU-baan staat al%'")["n"]
    assert n == 1


def test_b3_summary_counts_client_transfers():
    from mediaguard.activity.api import _is_transferring

    assert _is_transferring({"status": "queued", "time_left": "0:09:00", "progress": 40})
    assert _is_transferring({"status": "downloading"})
    assert not _is_transferring({"status": "queued", "time_left": None})
    assert not _is_transferring({"status": "completed", "time_left": "0:00:01"})
    assert not _is_transferring({"status": "warning", "awaiting_import": True,
                                 "time_left": "0:00:00", "progress": 100})


def _prov_grab(ctx, did: str, title: str, indexer: str = "IndexerNL", minutes_ago: int = 8):
    from datetime import UTC, datetime, timedelta

    at = (datetime.now(UTC) - timedelta(minutes=minutes_ago)).isoformat(timespec="seconds")
    ctx.db.execute(
        "INSERT INTO release_provenance(instance_id,event_type,download_id,release_title,"
        "indexer,received_at,idempotency_key) VALUES ('radarr-films','Grab',?,?,?,?,?)",
        (did, title, indexer, at, f"g-{did}"))


def test_b4_same_release_not_grabbed_twice(ctx):
    from mediaguard.hooks.service import _recent_same_grab

    title = "De.Morgenstorm.2021.2160p.DV.HDR.H.265-GROEP"
    _prov_grab(ctx, "d1", title)
    assert _recent_same_grab(ctx, "radarr-films", title, "IndexerNL") is not None
    assert _recent_same_grab(ctx, "radarr-films", title, "AnderIndexer") is None
    ctx.state_set("download_outcomes", {"d1": {"outcome": "failed"}})
    assert _recent_same_grab(ctx, "radarr-films", title, "IndexerNL") is None
    _prov_grab(ctx, "d2", "Oud", minutes_ago=180)
    assert _recent_same_grab(ctx, "radarr-films", "Oud", "IndexerNL") is None


def test_b5_import_and_upgrade_same_file_is_one_line(ctx):
    from mediaguard.activity.api import _recent

    payload = dumps({"series": {"title": "Afvalkoning"},
                     "episodes": [{"seasonNumber": 1, "episodeNumber": 22}]})
    now = utcnow()
    for i, upg in enumerate((0, 1)):
        ctx.db.execute(
            "INSERT INTO import_events(instance_id,event_type,is_upgrade,file_path,"
            "idempotency_key,payload_json,received_at) VALUES ('sonarr-anime',?,?,?,?,?,?)",
            ("Download" if not upg else "Upgrade", upg, "/m/g.mkv", f"k{i}", payload, now))
    rows = [r for r in _recent(ctx) if "Afvalkoning" in str(r["title"])]
    assert len(rows) == 1 and rows[0]["kind"] == "upgrade"


def test_n10_purged_with_vanished_check_is_shown_as_error(ctx):
    from mediaguard.recovery import orphans

    now = utcnow()
    txn = ctx.db.execute_insert(
        "INSERT INTO replacement_transactions(kind,instance_id,library_path,final_path,"
        "staged_path,source_snapshot_json,state,created_at,updated_at) VALUES "
        "('replace','sonarr-anime','/m/t.mkv','/m/t.mkv','/s','{}','committed',?,?)",
        (now, now))
    item = ctx.db.execute_insert(
        "INSERT INTO quarantine_items(transaction_id,original_path,quarantine_path,method,"
        "status,size,created_at,instance_id) VALUES (?,?,?,'link','purged',1,?,'sonarr-anime')",
        (txn, "/m/t.mkv", "/data/.mediaguard-quarantine/txn-17/t.mkv", now))
    checks = [{"name": "probe", "passed": False,
               "detail": "ffprobe faalde: /data/.mediaguard-quarantine/txn-17/t.mkv: "
                         "No such file or directory"}]
    ctx.db.execute("INSERT INTO quarantine_release_checks(item_id,status,checks_json,"
                   "checked_at) VALUES (?, 'failed', ?, ?)", (item, dumps(checks), now))
    assert orphans.vanished_by_check({"status": "failed", "checks": checks})
    assert orphans.fix_vanished_history(ctx) == 1
    row = ctx.db.fetch_one("SELECT reason FROM quarantine_items WHERE id=?", (item,))
    assert row["reason"].startswith("Origineel verdwenen")
