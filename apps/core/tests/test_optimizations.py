"""Prestaties en set-and-forget-automatisering (optimalisatieronde na 0.2.7)."""

from __future__ import annotations

import logging
import sqlite3
from datetime import UTC, datetime, timedelta

from test_arr_fakes import (
    FakeArr,
    b1ctx,  # noqa: F401
    make_episode,
    make_series,
    wire,
)

from mediaguard import jobs
from mediaguard.db import Database
from mediaguard.library.sync import sync_instance


def _sonarr(n_series: int = 3) -> FakeArr:
    fake = FakeArr("sonarr")
    for sid in range(1, n_series + 1):
        eps = [make_episode(sid * 100 + e, sid, 1, e, aired_days=-30) for e in (1, 2)]
        fake.add_series(make_series(sid, f"Serie {sid}", 1000 + sid), eps)
    return fake


# -- instellingen-cache --------------------------------------------------------------------
def test_settings_cache_invalidates_on_set_and_sql(ctx):
    assert ctx.config.value("jobs.max_concurrency") == 4
    ctx.config.set("global", "", "jobs.max_concurrency", 6, "t")
    assert ctx.config.value("jobs.max_concurrency") == 6  # lokale set leegt de cache
    ctx.db.execute("DELETE FROM settings_values WHERE key='jobs.max_concurrency'")
    assert ctx.config.value("jobs.max_concurrency") == 6  # andere schrijver: tot TTL
    ctx.config.invalidate()
    assert ctx.config.value("jobs.max_concurrency") == 4
    # lijsten uit de cache mogen niet via een aanroeper gewijzigd worden
    ctx.config.set("global", "", "dv.allowed_profiles", ["8.1"], "t")
    ctx.config.value("dv.allowed_profiles").append("7_fel")
    assert ctx.config.value("dv.allowed_profiles") == ["8.1"]


def test_settings_cache_avoids_queries(ctx):
    calls = {"n": 0}
    real = ctx.db.fetch_all

    def counting(sql, params=()):  # type: ignore[no-untyped-def]
        if "settings_values" in sql:
            calls["n"] += 1
        return real(sql, params)

    ctx.config.invalidate()
    ctx.db.fetch_all = counting  # type: ignore[method-assign]
    try:
        for _ in range(200):
            ctx.config.value("backfill.priority", {"instance": "sonarr-series",
                                                   "series": "sonarr-series:1"})
    finally:
        ctx.db.fetch_all = real  # type: ignore[method-assign]
    assert calls["n"] <= 1


# -- database -----------------------------------------------------------------------------
def test_synchronous_per_role_and_backup_before_migration(tmp_path):
    core = Database(tmp_path / "a.db")
    rec = Database(tmp_path / "a.db", synchronous="FULL")
    core.initialize()
    assert core.fetch_one("PRAGMA synchronous")[0] == 1   # NORMAL
    assert rec.fetch_one("PRAGMA synchronous")[0] == 2    # FULL (journaal)
    # simuleer een update met een nieuwe migratie: eerst een intacte back-up
    core.execute("DELETE FROM schema_migrations WHERE version='065_performance'")
    core.execute("DROP INDEX idx_lib_episodes_inst_air")
    core.execute("DROP INDEX idx_oi_status")
    core.execute("DROP INDEX idx_stuck_import_seen")
    core.execute("DROP TABLE stuck_import_actions")
    core.execute("ALTER TABLE lib_items DROP COLUMN sync_fingerprint")
    core.close()
    again = Database(tmp_path / "a.db")
    again.initialize()
    # een complete set (map met kopie + metadata), geen los bestand meer
    sets = [p for p in (tmp_path / "backups" / "voor-update").iterdir() if p.is_dir()]
    assert len(sets) == 1 and sets[0].name.startswith("voor-update-")
    import json

    meta = json.loads((sets[0] / "meta.json").read_text())
    assert meta["to_schema"] == "065_performance" and meta["integrity_ok"]
    check = sqlite3.connect(sets[0] / "mediaguard.db")
    assert check.execute("PRAGMA integrity_check").fetchone()[0] == "ok"
    check.close()


# -- jobs --------------------------------------------------------------------------------
def test_failed_jobs_resolve_and_ignore(client, ctx):
    db = ctx.db
    old = (datetime.now(UTC) - timedelta(days=10)).isoformat(timespec="seconds")
    now = datetime.now(UTC).isoformat(timespec="seconds")
    for i, (when, target) in enumerate(((old, "1"), (now, "2"), (now, "3"))):
        db.execute("INSERT INTO jobs(job_type,status,instance_id,target_id,created_at,"
                   "finished_at,error) VALUES ('library.sync','failed','sonarr-series',?,?,?,"
                   "'ConnectError: weg')", (target, when, when))
        del i
    assert client.get("/api/v1/status").json()["jobs"]["failed"] == 2  # alleen recent
    # later geslaagd voor doel 2 → die fout telt niet meer
    jobs.resolve_earlier_failures(db, {"job_type": "library.sync",
                                       "instance_id": "sonarr-series", "target_id": "2"})
    assert client.get("/api/v1/status").json()["jobs"]["failed"] == 1
    third = db.fetch_one("SELECT id FROM jobs WHERE target_id='3'")["id"]
    assert client.post(f"/api/v1/jobs/{third}/ignore").json()["ignored"] is True
    status = client.get("/api/v1/status").json()["jobs"]
    assert status["failed"] == 0 and status["failed_all"] == 3
    # tijdelijke fouten worden 's nachts opnieuw geprobeerd (max 3 keer)
    db.execute("UPDATE jobs SET resolved_at=NULL")
    assert jobs.retry_transient_failures(db) == 3
    assert db.fetch_one("SELECT COUNT(*) AS n FROM jobs WHERE status='queued'")["n"] == 3


def test_recovery_role_only_runs_recovery_jobs():
    assert jobs.allowed_for_role("library.sync", "core")
    assert not jobs.allowed_for_role("library.sync", "recovery")
    assert jobs.allowed_for_role("library.sync", "all")


async def test_only_one_library_sync_at_a_time(ctx):
    runner = jobs.JobRunner(ctx)
    for iid in ("sonarr-series", "sonarr-anime"):
        jobs.enqueue(ctx.db, "library.sync", {}, instance_id=iid)
    first = runner._claim_next()
    assert first is not None
    runner._types[int(first["id"])] = "library.sync"
    runner._running[int(first["id"])] = None  # type: ignore[assignment]
    assert runner._claim_next() is None  # tweede sync wacht


# -- bibliotheeksync ----------------------------------------------------------------------
async def test_sync_only_refetches_changed_series(b1ctx):  # noqa: F811
    fake = wire(b1ctx, "sonarr-series", _sonarr(3))
    first = await sync_instance(b1ctx, "sonarr-series")
    assert first["full"] and first["fetched_series"] == 3 and first["episodes"] == 6
    before = {r["provider_id"]: r["synced_at"] for r in b1ctx.db.fetch_all(
        "SELECT provider_id, synced_at FROM lib_episodes")}
    calls = len(fake.calls_to("GET", "/api/v3/episode"))
    second = await sync_instance(b1ctx, "sonarr-series")
    assert not second["full"] and second["fetched_series"] == 0
    assert len(fake.calls_to("GET", "/api/v3/episode")) == calls  # niets opgehaald
    # een nieuwe aflevering in serie 2 → alleen die serie opnieuw
    fake.add_series(make_series(2, "Serie 2", 1002),
                    [make_episode(201, 2, 1, 1, aired_days=-30),
                     make_episode(202, 2, 1, 2, aired_days=-30),
                     make_episode(203, 2, 1, 3, aired_days=-1)])
    third = await sync_instance(b1ctx, "sonarr-series")
    assert third["fetched_series"] == 1 and third["episodes"] == 3
    after = {r["provider_id"]: r["synced_at"] for r in b1ctx.db.fetch_all(
        "SELECT provider_id, synced_at FROM lib_episodes")}
    assert after[101] == before[101]          # ongewijzigde rij niet herschreven
    assert 203 in after
    # aflevering verdwenen in Sonarr → ook uit de cache
    del fake.episodes[202], fake.episodes[203]
    await sync_instance(b1ctx, "sonarr-series", full=True)
    left = {r["provider_id"] for r in b1ctx.db.fetch_all(
        "SELECT provider_id FROM lib_episodes WHERE series_provider_id=2")}
    assert left == {201}


async def test_wanted_refresh_skipped_without_new_sync(b1ctx):  # noqa: F811
    from mediaguard.wanted.engine import refresh_wanted_if_changed

    wire(b1ctx, "sonarr-series", _sonarr(2))
    await sync_instance(b1ctx, "sonarr-series")
    assert refresh_wanted_if_changed(b1ctx, "sonarr-series") is None
    await sync_instance(b1ctx, "sonarr-series")
    # sync ververst zelf al; daarna weer niets te doen
    assert refresh_wanted_if_changed(b1ctx, "sonarr-series") is None


async def test_library_list_stats_scoped_to_page(b1ctx):  # noqa: F811
    from mediaguard.library.queries import kpis, list_items, series_stats

    wire(b1ctx, "sonarr-series", _sonarr(5))
    await sync_instance(b1ctx, "sonarr-series")
    full = series_stats(b1ctx, "sonarr-series")
    page = series_stats(b1ctx, "sonarr-series", series_ids=[2, 3])
    assert set(page) == {("sonarr-series", 2), ("sonarr-series", 3)}
    assert page[("sonarr-series", 2)] == full[("sonarr-series", 2)]
    items = list_items(b1ctx, instance_id="sonarr-series", page_size=2)["items"]
    assert all(i["stats"]["total"] == 2 for i in items)
    k1 = kpis(b1ctx, "sonarr-series")
    assert kpis(b1ctx, "sonarr-series") is k1  # gecachet


# -- vastgelopen imports -------------------------------------------------------------------
async def test_stuck_import_removed_blocklisted_after_grace(b1ctx):  # noqa: F811
    from mediaguard.wanted import stuck

    fake = wire(b1ctx, "sonarr-series", _sonarr(1))
    fake.queue = [
        {"id": 7, "downloadId": "SAB_1", "title": "Serie.1.S01E01.1080p", "status": "completed",
         "trackedDownloadState": "importBlocked", "trackedDownloadStatus": "warning",
         "statusMessages": [{"messages": ["Sample file"]}]},
        {"id": 8, "downloadId": "SAB_2", "title": "Serie.1.S01E02.1080p", "status": "completed",
         "trackedDownloadState": "importBlocked",
         "statusMessages": [{"messages": ["MediaGuard: MEDIAGUARD_UNAVAILABLE"]}]},
        {"id": 9, "downloadId": "SAB_3", "title": "Bezig", "status": "downloading",
         "trackedDownloadState": "downloading"},
    ]
    first = await stuck.check_instance(b1ctx, "sonarr-series")
    assert first == {"checked": 1, "fixed": 0}  # nog binnen de wachttijd
    b1ctx.db.execute("UPDATE stuck_import_actions SET first_seen_at=?",
                     ((datetime.now(UTC) - timedelta(hours=7)).isoformat(timespec="seconds"),))
    second = await stuck.check_instance(b1ctx, "sonarr-series")
    assert second["fixed"] == 1
    deletes = fake.calls_to("DELETE", "/api/v3/queue/7")
    assert deletes and deletes[-1][2].get("blocklist") == "true"
    assert not fake.calls_to("DELETE", "/api/v3/queue/8")  # tijdelijke weigering blijft
    from mediaguard.activity.api import _recent

    assert any(r["kind"] == "stuck_fixed" for r in _recent(b1ctx))


async def test_stuck_import_disabled_or_shadow_does_nothing(b1ctx):  # noqa: F811
    from mediaguard.wanted import stuck

    fake = wire(b1ctx, "sonarr-series", _sonarr(1))
    fake.queue = [{"id": 7, "downloadId": "SAB_1", "title": "x", "status": "completed",
                   "trackedDownloadState": "importBlocked"}]
    b1ctx.db.execute("UPDATE instances SET shadow_mode=1 WHERE id='sonarr-series'")
    assert await stuck.check_instance(b1ctx, "sonarr-series") == {"checked": 0, "fixed": 0}


# -- onderhoud -----------------------------------------------------------------------------
def test_housekeeping_prunes_old_rows_only(ctx):
    from mediaguard.maintenance import housekeeping

    old = (datetime.now(UTC) - timedelta(days=60)).isoformat(timespec="seconds")
    new = datetime.now(UTC).isoformat(timespec="seconds")
    db = ctx.db
    for when in (old, new):
        jid = db.execute_insert("INSERT INTO jobs(job_type,status,created_at,finished_at) "
                                "VALUES ('library.sync','succeeded',?,?)", (when, when))
        db.execute("INSERT INTO job_logs(job_id,ts,level,message) VALUES (?,?,?,?)",
                   (jid, when, "info", "x"))
        db.execute("INSERT INTO outbox_events(ts,topic,payload) VALUES (?,?,?)",
                   (when, "t", "{}"))
        db.execute("INSERT INTO hook_decisions(ts,instance_id,hook,decision) VALUES "
                   "(?,?,?,?)", (when, "sonarr-series", "pre_grab", "accept"))
    db.execute("INSERT INTO jobs(job_type,status,created_at,finished_at) VALUES "
               "('backfill.run','failed',?,?)", (old, old))  # onopgelost: blijft
    out = housekeeping.prune(ctx)
    assert out["jobs"] == 1 and out["events"] == 1 and out["hook_decisions"] == 1
    assert db.fetch_one("SELECT COUNT(*) AS n FROM job_logs")["n"] == 1
    assert db.fetch_one("SELECT COUNT(*) AS n FROM jobs WHERE status='failed'")["n"] == 1
    result = housekeeping.run_nightly(ctx)
    assert "pruned" in result and ctx.state_get("maintenance.last_nightly")["at"]
    assert not housekeeping.due_now(ctx)  # vandaag al gedaan


def test_backup_health_component(client, ctx):
    health = client.get("/api/v1/admin/health").json()["components"]
    assert health["backup"]["ok"] is False  # nog geen automatische back-up
    ctx.state_set("backup.last_auto", datetime.now(UTC).isoformat(timespec="seconds"))
    assert client.get("/api/v1/admin/health").json()["components"]["backup"]["ok"] is True


# -- kleine dingen ------------------------------------------------------------------------
def test_schema_etag_and_theme_in_me(client):
    r = client.get("/api/v1/settings/schema")
    assert r.status_code == 200 and r.headers["etag"]
    again = client.get("/api/v1/settings/schema", headers={"If-None-Match": r.headers["etag"]})
    assert again.status_code == 304
    assert client.get("/api/v1/auth/me").json()["theme"] in ("dark", "light", "system")


def test_access_log_filter_keeps_errors_and_writes():
    from mediaguard.__main__ import _QuietAccess

    f = _QuietAccess()

    def rec(method: str, status: int) -> logging.LogRecord:
        return logging.LogRecord("uvicorn.access", logging.INFO, "", 0,
                                 '%s - "%s %s HTTP/%s" %d',
                                 ("1.2.3.4", method, "/x", "1.1", status), None)

    assert not f.filter(rec("GET", 200))
    assert f.filter(rec("GET", 500)) and f.filter(rec("POST", 200))


def test_health_messages_dutch():
    from mediaguard.instances.health_nl import summarize

    text = summarize([{"source": "IndexerRssCheck", "message": "No indexers available"},
                      {"source": "Onbekend", "message": "Something else"}])
    assert "RSS" in text and "Something else" in text


async def test_activity_cached_and_titles_from_library(b1ctx):  # noqa: F811
    from mediaguard.activity.api import activity

    fake = wire(b1ctx, "sonarr-series", _sonarr(1))
    await sync_instance(b1ctx, "sonarr-series")
    fake.queue = [{"id": 5, "downloadId": "X", "title": "Serie.1.S01E02", "seriesId": 1,
                   "episodeId": 102, "status": "downloading", "size": 100, "sizeleft": 25,
                   "trackedDownloadState": "downloading"}]
    first = await activity(ctx=b1ctx)
    row = next(d for d in first["downloads"] if d["instance_id"] == "sonarr-series")
    assert row["title"] == "Serie 1 · S01E02" and row["progress"] == 75.0
    queue_calls = fake.calls_to("GET", "/api/v3/queue")
    assert all("includeSeries" not in c[2] for c in queue_calls)
    again = await activity(ctx=b1ctx)
    assert again["downloads"] is first["downloads"]
    assert len(fake.calls_to("GET", "/api/v3/queue")) == len(queue_calls)


async def test_autopilot_scans_in_batches_with_cursor(client, ctx):
    from mediaguard.encoding import autopilot

    for i in range(1, 6):
        ctx.db.execute(
            "INSERT INTO lib_files(instance_id,kind,provider_id,owner_provider_id,path,size,"
            "media_info_json,synced_at) VALUES ('radarr-films','movie',?,?,?,?,?, 'x')",
            (i, i, f"/nergens/{i}.mkv", 10**9 * i,
             '{"videoCodec": "av1"}' if i == 5 else '{"videoCodec": "x264"}'))
    autopilot.SCAN_BATCH, old = 2, autopilot.SCAN_BATCH
    try:
        autopilot._pick_candidates(ctx, 1)
        assert ctx.state_get("encoding_autopilot_cursor")["offset"] == 2
        autopilot._pick_candidates(ctx, 1)
        assert ctx.state_get("encoding_autopilot_cursor")["offset"] == 4
        # AV1-bestand telt niet mee: einde bereikt → meteen weer vanaf de grootste
        autopilot._pick_candidates(ctx, 1)
        assert ctx.state_get("encoding_autopilot_cursor")["offset"] == 2
    finally:
        autopilot.SCAN_BATCH = old
