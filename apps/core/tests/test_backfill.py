"""Backfill Scheduler: quota per instantie, backoff, dedup, 'niet beschikbaar' zonder eindeloze
lussen, seizoenszoekactie, RSS ongemoeid, handmatig zoeken, versoepelde retry."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

import pytest
from test_arr_fakes import (
    FakeArr,
    b1ctx,  # noqa: F401
    download_client,
    indexer,
    make_episode,
    make_release,
    make_series,
    wire,
)

from mediaguard.library.sync import sync_instance
from mediaguard.wanted import backfill
from mediaguard.wanted.backfill import QuotaExceeded, active_override, run_backfill


def sonarr_with_missing(n: int = 6, sid: int = 1, tvdb: int = 100,
                        api_key: str = "fake-api-key-123") -> FakeArr:
    fake = FakeArr("sonarr", api_key=api_key)
    fake.add_indexer(indexer(1, "NZBgeek"))
    fake.providers["downloadclient"][1] = download_client(1)
    eps = [make_episode(100 + i, sid, 1, i + 1, aired_days=-(i + 1) * 10) for i in range(n)]
    eps.append(make_episode(200, sid, 1, 99, aired_days=-5, monitored=False))  # bewust uit
    eps.append(make_episode(201, sid, 2, 1, aired_days=7))                      # toekomst
    eps.append(make_episode(202, sid, 2, 2, aired_days=-3, has_file=True, file_id=1))
    fake.add_series(make_series(sid, "Show", tvdb, seasons={1: True, 2: True}), eps)
    return fake


def _set(ctx, key, value, scope="global", scope_id=""):  # type: ignore[no-untyped-def]
    ctx.config.set(scope, scope_id, key, value, "test")


def _make_due(ctx, instance_id="sonarr-series"):  # type: ignore[no-untyped-def]
    past = (datetime.now(UTC) - timedelta(minutes=1)).isoformat(timespec="seconds")
    ctx.db.execute("UPDATE wanted_items SET next_search_at=? WHERE instance_id=? AND "
                   "next_search_at IS NOT NULL", (past, instance_id))


def _searched_episode_ids(fake: FakeArr) -> list[int]:
    out: list[int] = []
    for cmd in fake.commands.values():
        out += cmd["body"].get("episodeIds", [])
    return out


async def test_quota_batch_and_never_searches_unmonitored_or_future(b1ctx):  # noqa: F811
    fake = wire(b1ctx, "sonarr-series", sonarr_with_missing(6))
    await sync_instance(b1ctx, "sonarr-series")
    _set(b1ctx, "backfill.max_searches_per_hour", 3)
    _set(b1ctx, "backfill.season_search_threshold", 50)
    res = await run_backfill(b1ctx, "sonarr-series")
    assert res["issued"] == 3
    # hoogste prioriteit eerst: meest recente afleveringen
    assert sorted(_searched_episode_ids(fake)) == [100, 101, 102]
    res = await run_backfill(b1ctx, "sonarr-series")
    assert res["issued"] == 0 and res["plan"]["deferred"]["quota_or_batch"] == 3
    searched = set(_searched_episode_ids(fake))
    assert not searched & {200, 201, 202}
    # alleen zoekcommando's; RSS en indexers blijven ongemoeid
    assert {c["name"] for c in fake.commands.values()} == {"EpisodeSearch"}
    assert fake.calls_to("PUT", "/api/v3/indexer") == []
    q = backfill.quota_status(b1ctx, "sonarr-series")
    assert q["hour_used"] == 3 and q["remaining"] == 0


async def test_day_quota_and_batch_size(b1ctx):  # noqa: F811
    wire(b1ctx, "sonarr-series", sonarr_with_missing(6))
    await sync_instance(b1ctx, "sonarr-series")
    _set(b1ctx, "backfill.batch_size", 2)
    _set(b1ctx, "backfill.max_searches_per_day", 3)
    _set(b1ctx, "backfill.season_search_threshold", 50)
    assert (await run_backfill(b1ctx, "sonarr-series"))["issued"] == 2
    assert (await run_backfill(b1ctx, "sonarr-series"))["issued"] == 1
    assert (await run_backfill(b1ctx, "sonarr-series"))["issued"] == 0


async def test_backoff_then_unavailable_without_endless_loop(b1ctx):  # noqa: F811
    fake = wire(b1ctx, "sonarr-series", sonarr_with_missing(1))
    await sync_instance(b1ctx, "sonarr-series")
    _set(b1ctx, "backfill.unavailable_after_attempts", 3)
    _set(b1ctx, "backfill.base_backoff_hours", 24)
    fake.releases[("episode", 100)] = [make_release("Show.S01E01.480p", rejections=[
        "SDTV is not wanted in profile"])]
    delays = []
    for _ in range(3):
        res = await run_backfill(b1ctx, "sonarr-series")
        assert res["issued"] == 1 and res["evaluation"]["no_candidate"] == 1
        row = b1ctx.db.fetch_one("SELECT * FROM wanted_items WHERE provider_id=100")
        nxt = datetime.fromisoformat(row["next_search_at"])
        delays.append(round((nxt - datetime.now(UTC)).total_seconds() / 3600))
        # zonder nieuwe aanleiding wordt er niet opnieuw gezocht
        assert (await run_backfill(b1ctx, "sonarr-series"))["issued"] == 0
        _make_due(b1ctx)
    assert delays[:2] == [24, 48]
    row = b1ctx.db.fetch_one("SELECT * FROM wanted_items WHERE provider_id=100")
    assert row["status"] == "unavailable" and row["attempts"] == 3
    assert delays[2] == 30 * 24  # unavailable_retry_days
    # bij 'niet beschikbaar' zijn kandidaten + afwijzingsredenen vastgelegd
    cand = b1ctx.db.fetch_one("SELECT * FROM backfill_attempts WHERE wanted_id=? AND "
                              "status='candidates'", (row["id"],))
    assert cand is not None and "SDTV is not wanted" in cand["rejections_json"]
    assert row["last_reason_code"] == "rejected-quality"
    # 'keep monitoring' voorkomt 'unavailable' maar houdt backoff aan
    from mediaguard.wanted.engine import apply_action
    apply_action(b1ctx, row["id"], "keep_monitoring")
    _make_due(b1ctx)
    await run_backfill(b1ctx, "sonarr-series")
    row = b1ctx.db.fetch_one("SELECT * FROM wanted_items WHERE provider_id=100")
    assert row["status"] == "wanted"


async def test_grab_success_and_kpi(b1ctx):  # noqa: F811
    fake = wire(b1ctx, "sonarr-series", sonarr_with_missing(2))
    await sync_instance(b1ctx, "sonarr-series")
    _set(b1ctx, "backfill.season_search_threshold", 50)

    def on_command(cmd):  # type: ignore[no-untyped-def]
        for eid in cmd["body"].get("episodeIds", []):
            if eid == 100:
                fake.grab_event(episode_id=100, title="Show.S01E01.1080p.WEB-DL")
    fake.on_command = on_command
    res = await run_backfill(b1ctx, "sonarr-series")
    assert res["evaluation"] == {"grabbed": 1, "no_candidate": 1, "error": 0,
                                 "still_running": 0}
    grabbed = b1ctx.db.fetch_one("SELECT * FROM wanted_items WHERE provider_id=100")
    assert grabbed["last_outcome"] == "grabbed"
    from mediaguard.wanted.engine import wanted_kpis
    k = wanted_kpis(b1ctx, "sonarr-series")["backfill_30d"]
    assert k["grabbed"] == 1 and k["completed"] == 2 and k["success_rate"] == 0.5
    # bestand verschijnt → bij sync opgelost
    fake.episodes[100].update(hasFile=True, episodeFileId=9)
    await sync_instance(b1ctx, "sonarr-series")
    row = b1ctx.db.fetch_one("SELECT status, resolution FROM wanted_items WHERE provider_id=100")
    assert (row["status"], row["resolution"]) == ("resolved", "available")


async def test_dedup_queue_pending_and_busy(b1ctx):  # noqa: F811
    fake = wire(b1ctx, "sonarr-series", sonarr_with_missing(3))
    await sync_instance(b1ctx, "sonarr-series")
    _set(b1ctx, "backfill.season_search_threshold", 50)
    fake.queue.append({"id": 1, "episodeId": 100, "seriesId": 1, "status": "downloading",
                       "trackedDownloadState": "downloading", "size": 1, "sizeleft": 1})
    fake.command_final_status = "started"  # blijft lopen
    res = await run_backfill(b1ctx, "sonarr-series")
    assert res["skipped_in_queue"] == 1 and res["issued"] == 2
    assert 100 not in _searched_episode_ids(fake)
    assert res["evaluation"]["still_running"] == 2
    # Sonarr is nog bezig met een zoekopdracht → ronde overslaan (RSS/zoeken niet opstapelen)
    res = await run_backfill(b1ctx, "sonarr-series")
    assert res["skipped"] == "busy"
    # handmatig: openstaande pogingen worden niet gedupliceerd
    fake.command_final_status = "completed"
    for cmd in fake.commands.values():
        cmd["status"] = "completed"
    plan = backfill.plan(b1ctx, "sonarr-series")
    assert plan["deferred"].get("pending") == 2


async def test_season_search_for_fully_missing_season(b1ctx):  # noqa: F811
    fake = wire(b1ctx, "sonarr-series", sonarr_with_missing(5))
    await sync_instance(b1ctx, "sonarr-series")
    _set(b1ctx, "backfill.season_search_threshold", 4)
    res = await run_backfill(b1ctx, "sonarr-series")
    names = [c["name"] for c in fake.commands.values()]
    assert names == ["SeasonSearch"]
    assert res["issued"] == 5
    assert backfill.quota_status(b1ctx, "sonarr-series")["hour_used"] == 1


async def test_per_instance_quotas_are_independent(b1ctx):  # noqa: F811
    series = wire(b1ctx, "sonarr-series", sonarr_with_missing(2))
    anime = wire(b1ctx, "sonarr-anime", sonarr_with_missing(2, tvdb=100,
                                                           api_key="anime-key-999"))
    for iid in ("sonarr-series", "sonarr-anime"):
        await sync_instance(b1ctx, iid)
    _set(b1ctx, "backfill.season_search_threshold", 50)
    _set(b1ctx, "backfill.max_searches_per_hour", 1, "instance", "sonarr-series")
    _set(b1ctx, "backfill.max_searches_per_hour", 5, "media_type", "anime")
    assert (await run_backfill(b1ctx, "sonarr-series"))["issued"] == 1
    assert (await run_backfill(b1ctx, "sonarr-anime"))["issued"] == 2
    assert len(_searched_episode_ids(series)) == 1 and len(_searched_episode_ids(anime)) == 2
    assert backfill.quota_status(b1ctx, "sonarr-anime")["hour_max"] == 5
    # zelfde provider-ID's, maar wanted-items blijven gescheiden per instantie
    rows = b1ctx.db.fetch_all("SELECT instance_id, COUNT(*) AS n FROM wanted_items GROUP BY 1")
    assert {r["instance_id"]: r["n"] for r in rows} == {"sonarr-series": 2, "sonarr-anime": 2}


async def test_guards_suspended_emergency_disabled_excluded(b1ctx):  # noqa: F811
    fake = wire(b1ctx, "sonarr-series", sonarr_with_missing(2))
    await sync_instance(b1ctx, "sonarr-series")
    b1ctx.db.execute("UPDATE instances SET suspended=1 WHERE id='sonarr-series'")
    assert (await run_backfill(b1ctx, "sonarr-series"))["skipped"] == "suspended"
    b1ctx.db.execute("UPDATE instances SET suspended=0 WHERE id='sonarr-series'")
    b1ctx.state_set("emergency_stop", {"active": True})
    assert (await run_backfill(b1ctx, "sonarr-series"))["skipped"] == "emergency_stop"
    b1ctx.state_set("emergency_stop", {"active": False})
    _set(b1ctx, "backfill.enabled", False, "instance", "sonarr-series")
    assert (await run_backfill(b1ctx, "sonarr-series"))["skipped"] == "disabled"
    _set(b1ctx, "backfill.enabled", True, "instance", "sonarr-series")
    _set(b1ctx, "backfill.excluded", True, "series", "sonarr-series:1")
    res = await run_backfill(b1ctx, "sonarr-series")
    assert res["issued"] == 0 and res["plan"]["deferred"]["excluded"] == 2
    assert fake.commands == {}


async def test_manual_search_counts_quota(b1ctx):  # noqa: F811
    wire(b1ctx, "sonarr-series", sonarr_with_missing(2))
    await sync_instance(b1ctx, "sonarr-series")
    _set(b1ctx, "backfill.max_searches_per_hour", 1)
    ids = [r["id"] for r in b1ctx.db.fetch_all("SELECT id FROM wanted_items ORDER BY id")]
    res = await backfill.search_now(b1ctx, [ids[0]], "user:test")
    assert res["results"][0]["issued"] == 1
    with pytest.raises(QuotaExceeded):
        await backfill.search_now(b1ctx, [ids[1]], "user:test")


async def test_retry_relaxed_requires_confirmation_and_records_override(b1ctx):  # noqa: F811
    fake = wire(b1ctx, "sonarr-series", sonarr_with_missing(1))
    await sync_instance(b1ctx, "sonarr-series")
    wid = int(b1ctx.db.fetch_one("SELECT id FROM wanted_items")["id"])
    fake.releases[("episode", 100)] = [
        make_release("Show.S01E01.GERMAN.1080p", guid="g-lang", rejections=[
            "Language German is not wanted in profile"]),
        make_release("Show.S01E01.720p", guid="g-720", rejections=[
            "WEBDL-720p is not wanted in profile"]),
    ]
    preview = await backfill.retry_relaxed(b1ctx, wid, ["quality"], "user:t", confirm=False)
    assert preview["requires_confirmation"] and "Eenmalige uitzondering" in \
        preview["explanation_nl"]
    assert fake.grabs == [] and b1ctx.db.fetch_one(
        "SELECT COUNT(*) AS n FROM wanted_overrides")["n"] == 0
    with pytest.raises(ValueError):  # taal staat standaard niet in de toegestane lijst
        await backfill.retry_relaxed(b1ctx, wid, ["language"], "user:t", confirm=True)
    res = await backfill.retry_relaxed(b1ctx, wid, ["quality"], "user:t", confirm=True,
                                       reason="liever 720p dan niets")
    assert res["status"] == "grabbed" and fake.grabs == [{"guid": "g-720", "indexerId": 1}]
    override = b1ctx.db.fetch_one("SELECT * FROM wanted_overrides WHERE id=?",
                                  (res["override_id"],))
    assert override["created_by"] == "user:t" and override["active"] == 0
    audit = b1ctx.db.fetch_one("SELECT * FROM audit_events WHERE action='wanted.retry_relaxed'")
    assert audit["instance_id"] == "sonarr-series"
    # nog niet gebruikte override is opvraagbaar voor de policy/hooks-module
    fake.releases[("episode", 100)] = []
    res = await backfill.retry_relaxed(b1ctx, wid, ["size"], "user:t", confirm=True)
    assert res["status"] == "no_candidate"
    ov = active_override(b1ctx, "sonarr-series", "episode", 100)
    assert ov is not None and ov["relaxations"] == ["size"]


def test_backfill_api_plan_and_runs(client, ctx):
    fake = wire(ctx, "sonarr-series", sonarr_with_missing(3))
    client.post("/api/v1/instances/sonarr-series/sync?mode=inline")
    plan = client.post("/api/v1/wanted/backfill-plan", json={"instance_id": "sonarr-series"}
                       ).json()
    assert plan["dry_run"] and plan["plans"][0]["eligible"] == 3
    cmd = plan["plans"][0]["commands"][0]
    assert cmd["items"][0]["priority"] >= cmd["items"][-1]["priority"]
    assert fake.commands == {}  # dry-run verstuurt niets
    r = client.post("/api/v1/instances/sonarr-series/backfill", json={"dry_run": True}).json()
    assert r["dry_run"] is True and fake.commands == {}
    ctx.config.set("global", "", "backfill.command_wait_seconds", 0, "t")
    r = client.post("/api/v1/instances/sonarr-series/backfill", json={"mode": "inline"}).json()
    assert r["issued"] == 3
    runs = client.get("/api/v1/wanted/backfill/runs?instance_id=sonarr-series").json()
    assert runs[0]["issued"] == 3 and runs[0]["trigger"] == "manual-run"
    r = client.post("/api/v1/instances/sonarr-series/backfill").json()
    assert r["queued"] and r["job_id"]
    assert client.get("/api/v1/wanted/backfill/quota").json()["sonarr-series"]["hour_used"] == 3
    wid = client.get("/api/v1/wanted").json()["items"][0]["id"]
    ctx.config.set("global", "", "backfill.max_searches_per_hour", 3, "t")
    r = client.post(f"/api/v1/wanted/{wid}/search")
    assert r.status_code == 429 and "quota" in r.text
    r = client.post(f"/api/v1/wanted/{wid}/retry-relaxed", json={"relaxations": ["quality"]})
    assert r.status_code == 200 and r.json()["requires_confirmation"]
    schema = {s["key"]: s for s in client.get("/api/v1/settings/schema").json()["settings"]}
    assert schema["backfill.interval_hours"]["minimum"] == 6
    assert schema["backfill.interval_hours"]["maximum"] == 24
    assert "series" in schema["backfill.excluded"]["scopes"]


async def test_scheduler_enqueues_per_instance_idempotently(b1ctx):  # noqa: F811
    from mediaguard.wanted.api import _due_backfill

    first = _due_backfill(b1ctx)
    assert len(first) == 4
    assert _due_backfill(b1ctx) == first  # zelfde tijdsvak → geen dubbele jobs
    b1ctx.db.execute("UPDATE instances SET suspended=1 WHERE id='sonarr-anime'")
    assert len(_due_backfill(b1ctx)) == 3
