"""Library Optimization Engine (C3): classificatie, dry-run, limieten, export, B1-bron."""

from __future__ import annotations

import json
import shutil

import pytest
from test_encoding_support import FakeInstance, FakeRegistry, install

from mediaguard import jobs
from mediaguard.db import dumps, utcnow
from mediaguard.optimization import planner
from mediaguard.optimization.provider import DbLibraryProvider, LibraryItem, StaticProvider

GB = 1024**3


def items(tmp_path):
    f = tmp_path / "lib"
    f.mkdir(exist_ok=True)
    av1_src = f / "Show - S01E05.mkv"
    av1_src.write_bytes(b"H264" * 1000)
    common = {"instance_id": "sonarr-anime", "media_kind": "episode_file",
              "media_type": "anime", "series_id": 12, "path": str(av1_src), "size": 3 * GB,
              "audio_languages": ["en", "ja"]}
    return [
        LibraryItem(provider_id="1", title="=cmd|' /C calc'!A0", identity_status="mismatch",
                    identity_actionable=True, episode_ids=[1], **common),
        LibraryItem(provider_id="2", title="ontbreekt", has_file=False, episode_ids=[2],
                    **common),
        LibraryItem(provider_id="3", title="alleen japans", episode_ids=[3],
                    **(common | {"audio_languages": ["ja"]})),
        LibraryItem(provider_id="4", title="cutoff", cutoff_unmet=True, episode_ids=[4],
                    **common),
        LibraryItem(provider_id="5", title="h264 groot", codec="h264", height=1080,
                    bitrate_kbps=12000, episode_ids=[5], **common),
        LibraryItem(provider_id="6", title="al av1", codec="av1", height=1080, **common),
        LibraryItem(provider_id="7", title="onzeker", identity_status="uncertain", **common),
        LibraryItem(provider_id="8", title="hardlink", codec="h264", height=1080,
                    bitrate_kbps=12000, nlink=2, **common),
        LibraryItem(provider_id="9", title="niet gemonitord", monitored=False, **common),
        LibraryItem(provider_id="10", title="dv", codec="hevc", height=2160,
                    bitrate_kbps=40000, dynamic_range="DV HDR10", **common),
        LibraryItem(provider_id="11", title="klein", codec="h264", height=1080,
                    bitrate_kbps=3000, **common),
        LibraryItem(provider_id="12", title="h264 groot 2", codec="h264", height=1080,
                    bitrate_kbps=15000, episode_ids=[12], **common),
    ]


@pytest.fixture
def opt(client, ctx, tmp_path):
    ctx.services["library_provider"] = StaticProvider(items(tmp_path))
    reg = FakeRegistry(FakeInstance("sonarr-anime", "anime"))
    ctx.services["instance_registry"] = reg
    # één encode tegelijk (Systeem & prestaties: 1 CPU, geen GPU)
    ctx.config.set("global", "", "resources.encodes_gpu", 0, "test")
    return reg


def test_classification_and_distribution(client, opt):
    plan = client.post("/api/v1/optimization/dry-run", json={}).json()
    assert plan["status"] == "dry_run" and plan["item_count"] == 12
    by_id = {i["provider_id"]: i for i in client.get(
        f"/api/v1/optimization/plan?plan_id={plan['id']}").json()["items"]}
    expect = {"1": "redownload", "2": "redownload", "3": "language-upgrade",
              "4": "quality-upgrade", "5": "av1", "6": "keep", "7": "verify", "8": "skip",
              "9": "skip", "10": "keep", "11": "keep", "12": "av1"}
    assert {k: v["action"] for k, v in by_id.items()} == expect
    assert by_id["1"]["priority"] == 1 and by_id["5"]["priority"] == 5
    assert "blijft zoeken" in by_id["3"]["reason_nl"]
    assert plan["summary"] == {"behouden": 3, "vervangen": 0, "zoeken": 4, "AV1": 2,
                               "onzeker": 1, "overslaan": 2}
    fc = plan["forecast"]
    assert fc["estimated_savings_bytes"] == 2 * int(3 * GB * 40 / 100)
    assert fc["staging_needed_bytes"] == int(3 * GB * 1.1)
    first = client.get(f"/api/v1/optimization/plan?plan_id={plan['id']}").json()["items"][0]
    assert first["priority"] == 1


def test_dv_allowed_makes_dv_av1_candidate(client, ctx, opt):
    ctx.config.set("global", "", "dv.allow_av1", True, "t")
    plan = client.post("/api/v1/optimization/dry-run", json={}).json()
    by_id = {i["provider_id"]: i for i in planner.items(ctx, plan["id"])}
    assert by_id["10"]["action"] == "av1"


def test_filters(client, opt):
    plan = client.post("/api/v1/optimization/dry-run",
                       json={"filters": {"instance_ids": ["sonarr-series"]}}).json()
    assert plan["item_count"] == 0


async def test_run_requires_dry_run_confirmation_and_respects_limits(client, ctx, opt,
                                                                     tmp_path, monkeypatch):
    usage = shutil._ntuple_diskusage(10**13, 0, 10**13)  # type: ignore[attr-defined]
    monkeypatch.setattr("mediaguard.optimization.runner.shutil.disk_usage", lambda _p: usage)
    fake = install(ctx, ctx.settings.staging_dir)
    ctx.settings.staging_dir.mkdir(parents=True, exist_ok=True)
    for it in items(tmp_path):
        if it.provider_id == "5":
            fake.add_source(tmp_path / "lib" / "Show - S01E05.mkv", codec="h264",
                            height=1080)
    ctx.config.set("global", "", "optimization.max_searches_per_hour", 2, "t")
    ctx.config.set("global", "", "optimization.staging_reserve_gb", 0, "t")
    # standaard downloadt MediaGuard zelf (zonder interactie); hier de akkoord-route testen
    from mediaguard.optimization.planner import downloads_confirmed

    assert downloads_confirmed(ctx, 999)
    ctx.config.set("global", "", "optimization.downloads_need_confirmation", True, "t")
    plan = client.post("/api/v1/optimization/dry-run", json={}).json()
    assert client.post("/api/v1/optimization/run",
                       json={"plan_id": plan["id"]}).status_code == 422
    r = client.post("/api/v1/optimization/run", json={"plan_id": plan["id"], "confirm": True})
    assert r.status_code == 200, r.text
    assert client.post("/api/v1/optimization/run", json={"plan_id": plan["id"],
                                                         "confirm": True}).status_code == 409
    # rapport 2 / N16: downloaden is een apart akkoord; zonder akkoord geen zoekopdrachten
    await jobs.JobRunner(ctx).run_until_idle()
    assert not opt.arr.commands
    p0 = client.get(f"/api/v1/optimization/plan?plan_id={plan['id']}").json()
    assert p0["display"]["downloads"]["needs_confirmation"] and not p0["display"]["can_start"]
    assert client.post(f"/api/v1/optimization/plans/{plan['id']}/confirm-downloads",
                       json={"confirm": True}).status_code == 200
    ctx.db.execute("UPDATE jobs SET run_after=NULL WHERE job_type='optimize.run'")
    await jobs.JobRunner(ctx).run_until_idle()
    statuses = {i["provider_id"]: i["status"] for i in planner.items(ctx, plan["id"])}
    assert statuses["1"] == statuses["2"] == "search_sent"     # prio 1 en 2 eerst
    assert statuses["3"] == statuses["4"] == "planned"         # zoekquotum bereikt
    assert sorted([statuses["5"], statuses["12"]]) == ["encode_submitted", "planned"]
    assert statuses["7"] == "auto_identity"  # geen handmatige review meer
    assert opt.arr.commands[0] == ("EpisodeSearch", {"episodeIds": [1]})
    job = ctx.db.fetch_one("SELECT * FROM jobs WHERE job_type='optimize.run'")
    assert job["status"] == "retry_scheduled"
    p = client.get(f"/api/v1/optimization/plan?plan_id={plan['id']}").json()
    assert p["status"] == "running" and p["limits"]["searches_last_hour"] == 2

    pause = client.post(f"/api/v1/optimization/plans/{plan['id']}/pause")
    assert pause.json()["status"] == "paused"
    assert ctx.db.fetch_one("SELECT status FROM jobs WHERE id=?", (job["id"],))["status"] == \
        "cancelled"
    resume = client.post(f"/api/v1/optimization/plans/{plan['id']}/resume").json()
    assert resume["status"] == "running" and resume["job_id"] != job["id"]
    audit = {a["action"] for a in client.get("/api/v1/audit?action=optimization").json()}
    assert {"optimization.dry_run", "optimization.run", "optimization.pause",
            "optimization.resume"} <= audit


async def test_emergency_stop_and_window_block_run(client, ctx, opt):
    plan = client.post("/api/v1/optimization/dry-run", json={}).json()
    ctx.state_set("emergency_stop", {"active": True})
    assert client.post("/api/v1/optimization/run", json={
        "plan_id": plan["id"], "confirm": True}).status_code == 409
    ctx.state_set("emergency_stop", {"active": False})
    ctx.config.set("global", "", "optimization.window_enabled", True, "t")
    ctx.config.set("global", "", "optimization.window", {"start": "00:00", "end": "00:00"},
                   "t")
    client.post("/api/v1/optimization/run", json={"plan_id": plan["id"], "confirm": True})
    await jobs.JobRunner(ctx).run_until_idle()
    assert {i["status"] for i in planner.items(ctx, plan["id"])} <= {"planned",
                                                                     "not_applicable"}


def test_expired_dry_run(client, ctx, opt):
    plan = client.post("/api/v1/optimization/dry-run", json={}).json()
    ctx.db.execute("UPDATE optimization_plans SET created_at='2000-01-01T00:00:00+00:00' "
                   "WHERE id=?", (plan["id"],))
    r = client.post("/api/v1/optimization/run", json={"plan_id": plan["id"], "confirm": True})
    assert r.status_code == 409 and "verlopen" in r.json()["detail"]


def test_export_csv_json_and_injection_guard(client, opt):
    plan = client.post("/api/v1/optimization/dry-run", json={}).json()
    csv_text = client.get(f"/api/v1/optimization/plans/{plan['id']}/export?format=csv").text
    assert csv_text.splitlines()[0].startswith("id,priority,instance_id")
    assert "'=cmd" in csv_text and ",=cmd" not in csv_text
    data = json.loads(client.get(
        f"/api/v1/optimization/plans/{plan['id']}/export?format=json").text)
    assert len(data["items"]) == 12 and data["plan"]["id"] == plan["id"]
    dash = client.get("/api/v1/optimization/dashboard").json()
    assert dash["latest_plan"]["id"] == plan["id"]


def test_db_provider_reads_b1_cache_defensively(ctx, tmp_path):
    assert list(DbLibraryProvider().items(ctx, {})) == []  # lege cache: niets, geen crash
    now = utcnow()
    f = tmp_path / "x.mkv"
    f.write_bytes(b"x")
    ctx.db.execute("INSERT INTO instances(id,app,media_type,name,url,created_at,updated_at) "
                   "VALUES ('sonarr-anime','sonarr','anime','Anime','http://x',?,?)",
                   (now, now))
    ctx.db.execute("INSERT INTO lib_items(instance_id,kind,provider_id,title,monitored,"
                   "synced_at) VALUES ('sonarr-anime','series',12,'Show',1,?)", (now,))
    ctx.db.execute("INSERT INTO lib_episodes(instance_id,provider_id,series_provider_id,"
                   "season_number,episode_number,monitored,has_file,episode_file_id,synced_at)"
                   " VALUES ('sonarr-anime',345,12,1,5,1,1,991,?)", (now,))
    ctx.db.execute(
        "INSERT INTO lib_files(instance_id,kind,provider_id,owner_provider_id,path,size,"
        "quality_cutoff_not_met,media_info_json,synced_at) VALUES ('sonarr-anime','episode',"
        "991,12,?,?,0,?,?)",
        (str(f), 3 * GB, dumps({"videoCodec": "x264", "resolution": "1920x1080",
                                "videoBitrate": 12_000_000, "audioLanguages": "jpn/eng",
                                "subtitles": "dut", "videoDynamicRangeType": ""}), now))
    found = list(DbLibraryProvider().items(ctx, {}))
    assert len(found) == 1
    it = found[0]
    assert (it.media_type, it.series_id, it.episode_ids, it.season) == ("anime", 12, [345], 1)
    assert it.audio_languages == ["ja", "en"] and it.bitrate_kbps == 12000
    assert it.height == 1080 and it.nlink == 1
    assert planner.classify(ctx, it).action == "av1"


def test_video_kbps_estimate_matches_encoder_measure():
    """Rapport 4 nov (H3): Sonarr meldt videoBitrate 0 voor MKV; core schat dan zoals de
    encoder (grootte/speelduur min audio) i.p.v. 'onbekend → kandidaat'."""
    from mediaguard.optimization.provider import video_kbps

    assert video_kbps({"videoBitrate": 18_430_000}, 1) == 18430
    # 45 min, 6,4 GB, 2× 640 kbps audio → ~17,7 Mbit/s video
    est = video_kbps({"videoBitrate": 0, "runTime": "45:00", "audioBitrate": 640_000,
                      "audioStreamCount": 2}, 6_400_000_000)
    assert 17_000 < est < 18_500
    assert video_kbps({"videoBitrate": 0}, 10) is None


def test_encoder_policy_marks_mediaguard_decision(ctx):
    from mediaguard.dv import rules as dv_rules
    from mediaguard.dv.policy import DvPolicy
    from mediaguard.encoding.service import encoder_policy

    d = dv_rules.decide({"path": "/x.mkv"}, DvPolicy(allow=False, mode="keep_original",
                                                      allowed_profiles=[],
                                                      hdr10plus_mode="skip"), {})
    pol = encoder_policy(ctx, {}, d)
    assert pol["decided_by"] == "mediaguard" and pol["skip_thresholds"] is True


def test_plan_items_show_their_av1_encode(client, opt):
    plan = client.post("/api/v1/optimization/dry-run", json={}).json()
    ctx = client.app.state.ctx
    now = utcnow()
    job_id = ctx.db.execute_insert(
        "INSERT INTO encoding_jobs(source_path,status,encoder_job_id,trial_only,policy_json,"
        "created_at,updated_at) VALUES ('/m/x.mkv','queued','77',0,'{}',?,?)", (now, now))
    item = ctx.db.fetch_one("SELECT id FROM optimization_items WHERE plan_id=? AND bucket='AV1' "
                            "LIMIT 1", (plan["id"],))
    ctx.db.execute("UPDATE optimization_items SET status='encode_submitted',result_json=? "
                   "WHERE id=?", (dumps({"encoding_job_id": job_id}), item["id"]))
    rows = client.get(f"/api/v1/optimization/plan?plan_id={plan['id']}&bucket=AV1").json()["items"]
    mine = next(r for r in rows if r["id"] == item["id"])
    assert mine["encode"] == {"id": job_id, "status": "queued", "encoder_job_id": "77"}
    assert all(r["encode"] is None for r in rows if r["id"] != item["id"])
