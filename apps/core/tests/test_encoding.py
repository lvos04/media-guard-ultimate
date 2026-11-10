"""Encoder-bridge (core-kant): plan met poorten, jobs, verificatie, handoff, canary."""

from __future__ import annotations

import httpx
import pytest
from test_encoding_support import install

from mediaguard import locks
from mediaguard.encoding import service as enc
from mediaguard.encoding.client import EncoderClient, EncoderUnavailable
from mediaguard.identity.engine import EpisodeRef, IdentityResult
from mediaguard.identity.service import save
from mediaguard.recovery import executor
from mediaguard.recovery import service as recovery

SRC = b"H264-SOURCE" * 5000


@pytest.fixture
def fx(client, ctx, tmp_path):
    fake = install(ctx, ctx.settings.staging_dir)
    src = tmp_path / "library" / "Show" / "Season 01" / "Show - S01E01.mkv"
    src.parent.mkdir(parents=True)
    src.write_bytes(SRC)
    fake.add_source(src, codec="h264", height=1080, audio=[("jpn", ""), ("eng", "")])
    ctx.settings.quarantine_dir.mkdir(parents=True, exist_ok=True)
    ctx.config.set("global", "", "recovery.min_free_gb", 0, "test")
    # de meeste tests hier bekijken de handmatige route; Full Auto is de standaard
    ctx.config.set("global", "", "encoding.auto_commit_after_verify", False, "test")
    return fake, src


def job_body(src, **kw):
    return {"path": str(src), "instance_id": "sonarr-anime", "media_kind": "episode_file",
            "provider_id": 991, "series_id": 12, "episode_ids": [345],
            "media_type": "anime"} | kw


async def test_client_token_and_unavailable(ctx, fx):
    fake, _ = fx
    caps = await EncoderClient("http://enc", "test-internal-token",
                               transport=ctx.services["encoder_transport"]).capabilities()
    assert caps["dovi_tool"] is True
    bad = EncoderClient("http://enc", "fout", transport=ctx.services["encoder_transport"])
    with pytest.raises(Exception, match="401"):
        await bad.health()
    fake.unavailable = True
    with pytest.raises(EncoderUnavailable):
        await enc.get_encoder_client(ctx).health()


def test_plan_gates_and_approval(client, ctx, fx):
    fake, src = fx
    r = client.post("/api/v1/encoding/plan", json=job_body(src)).json()
    assert r["approved_for_execution"] is False
    failed = {g["name"] for g in r["gates"] if not g["passed"]}
    assert failed == {"recovery_ready"}
    assert r["encoder_plan"]["actie"] == "hercoderen" and r["risk_class"] == "laag"
    assert r["audio"]["verdict"]["tier"] == "dual"
    recovery.mark_heartbeat(ctx)
    ok = client.post("/api/v1/encoding/plan", json=job_body(src)).json()
    assert ok["approved_for_execution"] is True
    assert client.get(f"/api/v1/encoding/plan/{ok['id']}").json()["approved_for_execution"]
    ctx.state_set("emergency_stop", {"active": True})
    sim = client.post(f"/api/v1/encoding/plan/{ok['id']}/simulate").json()
    assert sim["after"]["approved_for_execution"] is False
    assert client.post(f"/api/v1/encoding/plan/{ok['id']}/execute").status_code == 409
    ctx.state_set("emergency_stop", {"active": False})
    ex = client.post(f"/api/v1/encoding/plan/{ok['id']}/execute")
    assert ex.status_code == 200 and ex.json()["plan_id"] == ok["id"]


def test_plan_encoder_unavailable_never_approved(client, fx, ctx):
    fake, src = fx
    recovery.mark_heartbeat(ctx)
    fake.unavailable = True
    r = client.post("/api/v1/encoding/plan", json=job_body(src)).json()
    assert r["approved_for_execution"] is False and r["errors"]
    assert client.post("/api/v1/encoding/jobs", json=job_body(src)).status_code == 503


def test_identity_and_lock_gates(client, ctx, fx):
    _, src = fx
    save(ctx, instance_id="sonarr-anime", kind="episode_file", provider_id=991, path=None,
         result=IdentityResult("uncertain", 0.3, False, "zwak", [EpisodeRef(1, 1)], [], []),
         source="test", actor="t")
    r = client.post("/api/v1/encoding/jobs", json=job_body(src))
    assert r.status_code == 409 and r.json()["detail"]["code"] == "IDENTITY"
    save(ctx, instance_id="sonarr-anime", kind="episode_file", provider_id=991, path=None,
         result=IdentityResult("verified", 1, False, "ok", [], [], []), source="test",
         actor="t")
    locks.acquire(ctx.db, locks.media_resource("sonarr-anime", "episode_file", 991),
                  "import:x", "import")
    r = client.post("/api/v1/encoding/jobs", json=job_body(src))
    assert r.status_code == 409 and r.json()["detail"]["code"] == "LOCKED"


async def test_full_flow_manual_activation_commit_and_canary(client, ctx, fx):
    fake, src = fx
    recovery.mark_heartbeat(ctx)
    r = client.post("/api/v1/encoding/jobs", json=job_body(src))
    assert r.status_code == 200, r.text
    job = r.json()
    req = fake.jobs[1]["request"]
    # effectieve policy van dít bestand gaat naar de encoder
    # profiel blijft in core (de echte encoder weigert onbekende velden: extra=forbid)
    assert "profile_id" not in req["policy"]
    assert job["policy"]["profile_id"] == "anime-dual-first"
    assert req["policy"]["audio_policy"]["keep_all"] is True
    assert req["policy"]["dv_mode"] == "keep_original"  # geen DV: DV-route n.v.t.
    assert req["policy"]["dv_allowed_profiles"] == []
    assert req["media_ref"] == {"instance_id": "sonarr-anime", "kind": "episode_file",
                                "provider_id": 991}
    synced = await enc.sync_job(ctx, job["id"])
    assert synced["status"] == "awaiting_activation"  # Full Auto hier uitgezet
    assert synced["core_verification"]["passed"]
    assert client.post(f"/api/v1/encoding/jobs/{job['id']}/activate",
                       json={}).status_code == 422
    act = client.post(f"/api/v1/encoding/jobs/{job['id']}/activate", json={"confirm": True})
    assert act.status_code == 200 and act.json()["status"] == "handed_off"
    txn = await executor.run_transaction(ctx, act.json()["transaction_id"])
    assert txn["state"] == "committed" and src.read_bytes() == fake.output_bytes
    assert (await enc.sync_job(ctx, job["id"]))["status"] == "committed"
    assert enc.canary_passed(ctx) is False  # 1 van 3
    listed = client.get("/api/v1/encoding/jobs?status=committed").json()
    assert [j["id"] for j in listed] == [job["id"]]


async def test_full_auto_after_canary(client, ctx, fx):
    fake, src = fx
    recovery.mark_heartbeat(ctx)
    ctx.config.set("global", "", "encoding.auto_commit_after_verify", True, "t")
    ctx.config.set("global", "", "encoding.canary_required", 0, "t")
    job = client.post("/api/v1/encoding/jobs", json=job_body(src)).json()
    assert (await enc.sync_job(ctx, job["id"]))["status"] == "handed_off"
    assert await enc.sync_active(ctx) == 1


async def test_lost_track_rejected_original_intact(client, ctx, fx):
    fake, src = fx
    recovery.mark_heartbeat(ctx)
    fake.transform = lambda p: {**p, "codec": "av1", "audio": p["audio"][:1]}
    job = client.post("/api/v1/encoding/jobs", json=job_body(src)).json()
    synced = await enc.sync_job(ctx, job["id"])
    assert synced["status"] == "rejected"
    assert any(c["name"] == "audio_tracks" and not c["passed"]
               for c in synced["core_verification"]["checks"])
    assert any(p.endswith("/rejected") for _m, p, _b in fake.calls)
    assert src.read_bytes() == SRC
    r = client.post(f"/api/v1/encoding/jobs/{job['id']}/activate", json={"confirm": True})
    assert r.status_code == 409


async def test_encoder_reported_failure_and_cancel(client, ctx, fx):
    fake, src = fx
    fake.verification_passed = False
    job = client.post("/api/v1/encoding/jobs", json=job_body(src)).json()
    assert (await enc.sync_job(ctx, job["id"]))["status"] == "rejected"
    fake.polls_until_done = 99
    job2 = client.post("/api/v1/encoding/jobs", json=job_body(src)).json()
    assert (await enc.sync_job(ctx, job2["id"]))["status"] == "running"
    c = client.post(f"/api/v1/encoding/jobs/{job2['id']}/cancel")
    assert c.status_code == 200 and c.json()["status"] == "cancelled"
    assert fake.jobs[2]["status"] == "cancelled"


def test_capabilities_and_benchmarks(client, fx):
    assert client.get("/api/v1/encoding/capabilities").json()["available"]
    _, src = fx
    r = client.post("/api/v1/encoding/benchmarks", json={
        "path": str(src), "samples": [{"start": 60, "duration": 10}],
        "settings": [{"encoder": "libsvtav1", "crf": 30, "preset": 6}]})
    assert r.status_code == 200 and r.json()["result"]["results"][0]["vmaf"] == 95.1
    assert client.get("/api/v1/encoding/benchmarks").json()[0]["status"] == "done"


def test_dv_skip_recorded_without_encoder_job(client, ctx, fx, tmp_path):
    fake, _ = fx
    dv5 = tmp_path / "dv5.mkv"
    dv5.write_bytes(SRC)
    fake.add_source(dv5, dv_profile=5)
    r = client.post("/api/v1/encoding/jobs", json={"path": str(dv5)}).json()
    assert r["status"] == "skipped" and "DV 5" in r["skip_reason"]
    assert not fake.jobs


async def test_cache_floor_setting_is_pushed_to_encoder(client, ctx, fx):
    fake, _ = fx
    fake.cache_min_free_gb = 2  # bv. ingesteld via de oude encoder-UI
    # Nooit in core ingesteld → core overschrijft de encoderwaarde niet.
    assert (await enc.push_encoder_settings(ctx))["pushed"] is False
    assert fake.cache_min_free_gb == 2
    r = client.patch("/api/v1/settings/scoped", json={"changes": [
        {"scope_type": "global", "scope_id": "", "key": "encoding.cache_min_free_gb",
         "value": 300}]})
    assert r.status_code == 200, r.text
    first = await enc.push_encoder_settings(ctx)
    assert first["pushed"] is True and fake.cache_min_free_gb == 300
    assert ("PUT", "/settings/storage", {"cache_min_free_gb": 300}) in fake.calls
    # Idempotent: geen tweede PUT zolang de waarde gelijk blijft.
    assert (await enc.push_encoder_settings(ctx))["pushed"] is False
    # Encoder offline → niet als gepusht gemarkeerd; volgende poll probeert opnieuw.
    ctx.config.set("global", "", "encoding.cache_min_free_gb", 200, "t")
    fake.unavailable = True
    assert (await enc.push_encoder_settings(ctx))["pushed"] is False
    fake.unavailable = False
    assert (await enc.push_encoder_settings(ctx))["pushed"] is True
    assert fake.cache_min_free_gb == 200
    view = client.get("/api/v1/encoding/storage").json()
    assert view["available"] is True and view["encoder"]["cache_min_free_gb"] == 200
    assert view["setting"]["value"] == 200 and view["last_push"]["value"] == 200
    # Te lage bodem wordt door core al geweigerd.
    bad = client.patch("/api/v1/settings/scoped", json={"changes": [
        {"scope_type": "global", "scope_id": "", "key": "encoding.cache_min_free_gb",
         "value": 0}]})
    assert bad.status_code == 422
    fake.cache_free_gb = 100
    caps = client.get("/api/v1/encoding/capabilities").json()
    assert caps["available"] is True
    assert client.post("/api/v1/encoding/storage/push").json()["pushed"] is True


# -- review-bevindingen (bridge/core) --------------------------------------------------------
async def _ready_job(client, ctx, fx):
    fake, src = fx
    recovery.mark_heartbeat(ctx)
    job = client.post("/api/v1/encoding/jobs", json=job_body(src)).json()
    assert (await enc.sync_job(ctx, job["id"]))["status"] == "awaiting_activation"
    return fake, src, job


async def test_temporary_abort_keeps_verified_encode(client, ctx, fx):
    """Een noodstop/slot tijdens activatie mag uren encodewerk niet weggooien."""
    fake, src, job = await _ready_job(client, ctx, fx)
    act = client.post(f"/api/v1/encoding/jobs/{job['id']}/activate", json={"confirm": True})
    assert act.status_code == 200
    ctx.state_set("emergency_stop", {"active": True})
    txn = await executor.run_transaction(ctx, act.json()["transaction_id"])
    ctx.state_set("emergency_stop", {"active": False})
    assert txn["state"] == "aborted"
    synced = await enc.sync_job(ctx, job["id"])
    assert synced["status"] == "awaiting_activation" and "uitgesteld" in synced["error"]
    assert not any(p.endswith("/rejected") for _m, p, _b in fake.calls)
    assert src.read_bytes() == SRC
    # later gewoon opnieuw activeren
    again = client.post(f"/api/v1/encoding/jobs/{job['id']}/activate", json={"confirm": True})
    assert again.status_code == 200, again.text
    txn = await executor.run_transaction(ctx, again.json()["transaction_id"])
    assert txn["state"] == "committed" and src.read_bytes() == fake.output_bytes


async def test_double_activate_creates_one_transaction(client, ctx, fx):
    _fake, _src, job = await _ready_job(client, ctx, fx)
    first = client.post(f"/api/v1/encoding/jobs/{job['id']}/activate", json={"confirm": True})
    second = client.post(f"/api/v1/encoding/jobs/{job['id']}/activate", json={"confirm": True})
    assert first.status_code == 200 and second.status_code == 409
    n = ctx.db.fetch_one("SELECT COUNT(*) AS n FROM replacement_transactions")["n"]
    assert n == 1


async def test_committed_notice_is_idempotent_after_lost_response(client, ctx, fx):
    """Antwoord op /committed verloren → retry krijgt 409; job mag niet eeuwig hangen."""
    fake, _src, job = await _ready_job(client, ctx, fx)
    act = client.post(f"/api/v1/encoding/jobs/{job['id']}/activate", json={"confirm": True})
    await executor.run_transaction(ctx, act.json()["transaction_id"])
    fake.jobs[1]["status"] = "committed"  # encoder verwerkte het al
    orig = fake.handler

    def conflict(request):
        if request.url.path.endswith("/committed"):
            return httpx.Response(409, json={"detail": "job staat niet op awaiting_commit"})
        return orig(request)

    ctx.services["encoder_transport"] = httpx.MockTransport(conflict)
    assert (await enc.sync_job(ctx, job["id"]))["status"] == "committed"


async def test_cancel_not_marked_when_encoder_unreachable(client, ctx, fx):
    fake, src = fx
    fake.polls_until_done = 99
    job = client.post("/api/v1/encoding/jobs", json=job_body(src)).json()
    await enc.sync_job(ctx, job["id"])
    fake.unavailable = True
    r = client.post(f"/api/v1/encoding/jobs/{job['id']}/cancel")
    assert r.status_code in (409, 503)
    assert enc.get_job(ctx, job["id"])["status"] == "running"


async def test_encoder_forgot_job_marks_failed(client, ctx, fx):
    fake, src = fx
    fake.polls_until_done = 99
    job = client.post("/api/v1/encoding/jobs", json=job_body(src)).json()
    fake.jobs.clear()
    assert (await enc.sync_job(ctx, job["id"]))["status"] == "failed"


def test_gateway_blocks_bridge_routes(client):
    r = client.post("/api/v1/encoder/mediaguard/v1/jobs/1/committed")
    assert r.status_code == 403


# -- AV1-autopilot: MediaGuard kiest, de encoder voert uit ----------------------------------
async def test_autopilot_submits_worthwhile_files_within_depth(client, ctx, fx, tmp_path):
    from mediaguard.encoding import autopilot
    from mediaguard.optimization.provider import LibraryItem, StaticProvider

    fake, src = fx
    recovery.mark_heartbeat(ctx)
    others = []
    for n in (2, 3):
        p = tmp_path / "library" / "Show" / "Season 01" / f"Show - S01E0{n}.mkv"
        p.write_bytes(SRC)
        fake.add_source(p, codec="h264", height=1080, audio=[("jpn", ""), ("eng", "")])
        others.append(p)

    def item(path, pid, **kw):
        return LibraryItem(instance_id="sonarr-anime", media_kind="episode_file",
                           provider_id=str(pid), title="Show", path=str(path), size=8 * 10**9,
                           media_type="anime", series_id=12, episode_ids=[pid], codec="h264",
                           height=1080, bitrate_kbps=20000, **kw)

    ctx.services["library_provider"] = StaticProvider([
        item(src, 991), item(others[0], 992), item(others[1], 993),
        LibraryItem(instance_id="sonarr-anime", media_kind="episode_file", provider_id="994",
                    path=str(tmp_path / "x.mkv"), codec="av1", height=1080,
                    bitrate_kbps=3000, size=10**9, media_type="anime")])
    ctx.config.set("global", "", "encoding.autopilot_queue_depth", 2, "t")
    ctx.config.set("global", "", "encoding.canary_required", 0, "t")
    # vóór de overstap: niets
    ctx.config.set("global", "", "library.write_enabled", False, "t")
    assert (await autopilot.tick(ctx))["submitted"] == 0
    ctx.config.set("global", "", "library.write_enabled", True, "t")
    first = await autopilot.tick(ctx)
    assert first["submitted"] == 2 and len(fake.jobs) == 2
    assert all(j["request"]["source_path"] != str(tmp_path / "x.mkv")
               for j in fake.jobs.values())  # AV1 blijft staan
    # wachtrij vol → geen nieuwe; daarna nooit hetzelfde bestand opnieuw
    assert (await autopilot.tick(ctx))["submitted"] == 0
    for row in ctx.db.fetch_all("SELECT id FROM encoding_jobs"):
        enc._update(ctx, int(row["id"]), status="committed")
    third = await autopilot.tick(ctx)
    assert third["submitted"] == 1 and len(fake.jobs) == 3
    assert len({j["request"]["source_path"] for j in fake.jobs.values()}) == 3
    status = client.get("/api/v1/encoding/autopilot").json()
    assert status["enabled"] and status["blocked"] is None


def test_set_and_forget_defaults():
    from mediaguard.settings_registry import REGISTRY

    assert REGISTRY.get_def("encoding.auto_commit_after_verify").default is True
    assert REGISTRY.get_def("encoding.autopilot_enabled").default is True
    assert REGISTRY.get_def("dv.allow_av1").default is False  # DV blijft standaard onaangeroerd


async def test_autopilot_careful_start_one_at_a_time(client, ctx, fx):
    from mediaguard.encoding import autopilot
    from mediaguard.optimization.provider import LibraryItem, StaticProvider

    fake, src = fx
    ctx.services["library_provider"] = StaticProvider([
        LibraryItem(instance_id="sonarr-anime", media_kind="episode_file", provider_id=str(n),
                    path=str(src) if n == 1 else f"/nergens/{n}.mkv", size=8 * 10**9,
                    media_type="anime", codec="h264", height=1080, bitrate_kbps=20000)
        for n in (1, 2, 3)])
    ctx.config.set("global", "", "encoding.autopilot_queue_depth", 5, "t")
    result = await autopilot.tick(ctx)
    assert result["submitted"] == 1 and len(fake.jobs) == 1  # canary nog niet gehaald


async def test_acl_refusing_chmod_does_not_fail_activation(client, ctx, fx, monkeypatch):
    """Praktijk (TrueNAS, aclmode=restricted): chmod op het .mg-replace-…part-bestand gaf
    'PermissionError: [Errno 1] Operation not permitted' en de geverifieerde encode
    (54,8 → 9 GB) werd weggegooid."""
    import os as _os

    from mediaguard.recovery import fsops

    fake, src, job = await _ready_job(client, ctx, fx)
    real_chmod = _os.chmod

    def deny(path, *a, **k):
        if ".mg-replace-" in str(path):
            raise PermissionError(1, "Operation not permitted", str(path))
        return real_chmod(path, *a, **k)

    monkeypatch.setattr(fsops.os, "chmod", deny)
    act = client.post(f"/api/v1/encoding/jobs/{job['id']}/activate", json={"confirm": True})
    txn = await executor.run_transaction(ctx, act.json()["transaction_id"])
    assert txn["state"] == "committed" and src.read_bytes() == fake.output_bytes


async def test_permission_problem_keeps_encode_and_retries_later(client, ctx, fx, monkeypatch):
    """Rechten-/ruimteprobleem bij vervangen: encode blijft, reden in gewoon Nederlands,
    geen /rejected naar de encoder, en niet elke 10 minuten opnieuw."""
    from mediaguard.recovery import fsops

    fake, src, job = await _ready_job(client, ctx, fx)

    def no_write(*_a, **_k):
        raise PermissionError(13, "Permission denied", str(src.parent / ".mg-replace-x.part"))

    monkeypatch.setattr(fsops, "copy_with_hash", no_write)
    act = client.post(f"/api/v1/encoding/jobs/{job['id']}/activate", json={"confirm": True})
    txn = await executor.run_transaction(ctx, act.json()["transaction_id"])
    assert txn["state"] == "aborted"
    synced = await enc.sync_job(ctx, job["id"])
    assert synced["status"] == "awaiting_activation"
    assert "Geen schrijfrechten" in synced["error"] and "568" in synced["error"]
    assert not any(p.endswith("/rejected") for _m, p, _b in fake.calls)
    assert (synced["decision"] or {}).get("retry_after")
    assert src.read_bytes() == SRC


def test_autopilot_retries_environment_rejections_once_a_day(ctx):
    from mediaguard import attention
    from mediaguard.encoding import autopilot

    old = "2020-01-01T00:00:00+00:00"
    for n, err in enumerate(["PermissionError: [Errno 1] Operation not permitted: '/x'",
                             "Verificatie mislukt: bron behouden"]):
        ctx.db.execute(
            "INSERT INTO encoding_jobs(source_path,status,error,trial_only,policy_json,"
            "created_at,updated_at) VALUES (?,?,?,0,'{}',?,?)",
            (f"/m/{n}.mkv", "rejected", err, old, old))
    known = autopilot._known_paths(ctx)
    assert "/m/0.mkv" not in known  # opslagprobleem: mag opnieuw
    assert "/m/1.mkv" not in known  # inhoudelijk: één keer opnieuw na een dag
    # tweede inhoudelijke afwijzing: stoppen en tonen in Problemen
    ctx.db.execute(
        "INSERT INTO encoding_jobs(source_path,status,error,trial_only,policy_json,"
        "created_at,updated_at) VALUES ('/m/1.mkv','rejected','Verificatie mislukt',0,'{}',?,?)",
        (old, old))
    assert "/m/1.mkv" in autopilot._known_paths(ctx)
    assert autopilot.retry_states(ctx)["/m/1.mkv"]["state"] == "final"
    items = {i["key"]: i for i in attention._encoding(ctx)}
    assert items["encoding.failed_final"]["rows"][0]["title"] == "1.mkv"


def test_autopilot_retries_interrupted_encodes_with_backoff(ctx):
    """Praktijk: encode mislukt tijdens een app-update; die moet vanzelf opnieuw."""
    from datetime import UTC, datetime, timedelta

    from mediaguard.encoding import autopilot

    def add(path, status, err, minutes_ago):
        at = (datetime.now(UTC) - timedelta(minutes=minutes_ago)).isoformat(timespec="seconds")
        ctx.db.execute(
            "INSERT INTO encoding_jobs(source_path,status,error,trial_only,policy_json,"
            "created_at,updated_at) VALUES (?,?,?,0,'{}',?,?)", (path, status, err, at, at))

    assert autopilot.classify_failure("failed", "ffmpeg exitcode 255 (SIGTERM)") == "transient"
    assert autopilot.classify_failure("failed", "De encoder kent deze job niet meer") \
        == "transient"
    assert autopilot.classify_failure("failed", "bron onleesbaar") == "deterministic"
    assert autopilot.classify_failure("cancelled", autopilot.USER_CANCEL) is None
    add("/u/a.mkv", "failed", "AV1-engine is niet bereikbaar", 5)
    assert autopilot.retry_states(ctx)["/u/a.mkv"]["state"] == "wait"  # eerst 15 min
    add("/u/b.mkv", "failed", "onderbroken door backendherstart", 20)
    assert "/u/b.mkv" not in autopilot._known_paths(ctx)
    for minutes in (300, 200, 100, 90):  # 4 onderbrekingen = 3 keer opnieuw geprobeerd
        add("/u/c.mkv", "failed", "time-out", minutes)
    assert autopilot.retry_states(ctx)["/u/c.mkv"]["state"] == "final"
    add("/u/d.mkv", "cancelled", autopilot.USER_CANCEL, 999)  # 'Origineel houden'
    add("/u/e.mkv", "failed", "time-out", 999)
    add("/u/e.mkv", "committed", None, 10)  # later wel gelukt: nooit opnieuw
    known = autopilot._known_paths(ctx)
    assert {"/u/a.mkv", "/u/c.mkv", "/u/d.mkv", "/u/e.mkv"} <= known


async def test_replacement_uses_no_metadata_ops(client, ctx, fx, monkeypatch):
    """Rapport 4 nov (B1): vervangen moet werken zoals een Radarr-import — aanmaken,
    schrijven, hernoemen. Geen chmod/chown/utime op het tijdelijke bestand."""
    import os as _os

    fake, src, job = await _ready_job(client, ctx, fx)

    def forbidden(*_a, **_k):
        raise AssertionError("metadata-bewerking tijdens vervangen")

    for name in ("chmod", "chown", "fchmod", "fchown", "utime"):
        monkeypatch.setattr(_os, name, forbidden)
    act = client.post(f"/api/v1/encoding/jobs/{job['id']}/activate", json={"confirm": True})
    txn = await executor.run_transaction(ctx, act.json()["transaction_id"])
    assert txn["state"] == "committed" and src.read_bytes() == fake.output_bytes


async def test_eperm_names_the_failing_operation(client, ctx, fx, monkeypatch):
    """De melding noemt de exacte stap die EPERM gaf (hier: aanmaken van het .part-bestand)."""
    import os as _os

    from mediaguard.recovery import fsops

    fake, src, job = await _ready_job(client, ctx, fx)
    real_open = _os.open

    def deny(path, flags, *a, **k):
        if ".mg-replace-" in str(path) and flags & _os.O_CREAT:
            raise PermissionError(1, "Operation not permitted", str(path))
        return real_open(path, flags, *a, **k)

    monkeypatch.setattr(fsops.os, "open", deny)
    act = client.post(f"/api/v1/encoding/jobs/{job['id']}/activate", json={"confirm": True})
    txn = await executor.run_transaction(ctx, act.json()["transaction_id"])
    assert txn["state"] == "aborted"
    row = ctx.db.fetch_one("SELECT detail_json FROM replacement_journal WHERE transaction_id=? "
                           "AND step='abort' ORDER BY id DESC LIMIT 1", (txn["id"],))
    assert '"op": "create"' in row["detail_json"]
    synced = await enc.sync_job(ctx, job["id"])
    assert "bij 'aanmaken'" in synced["error"] and synced["status"] == "awaiting_activation"
    assert src.read_bytes() == SRC


async def test_write_probe_blocks_autopilot_and_requeues(client, ctx, fx, monkeypatch, tmp_path):
    from mediaguard import attention
    from mediaguard.encoding import autopilot
    from mediaguard.optimization.provider import LibraryItem, StaticProvider
    from mediaguard.recovery import fsops, probe

    fake, src = fx
    root = tmp_path / "library"
    ctx.db.execute("INSERT INTO lib_items(instance_id,kind,provider_id,title,"
                   "root_folder_path,path,synced_at) VALUES "
                   "('sonarr-anime','series',1,'Show',?,?,'x')",
                   (str(root), str(root / "Show")))
    real_replace = _os_replace = fsops.os.replace

    def deny(a, b, *x, **k):
        if ".mg-probe-" in str(a):
            raise PermissionError(1, "Operation not permitted", str(a))
        return _os_replace(a, b, *x, **k)

    monkeypatch.setattr(fsops.os, "replace", deny)
    state = probe.run(ctx)
    bad = [r for r in state["roots"] if r["path"] == str(root)][0]
    assert not bad["ok"] and bad["step"] == "replace" and bad["step_nl"] == "vervangen"
    assert not list(root.glob(".mg-probe-*"))  # niets achtergelaten
    assert any(i["key"] == "storage.write_probe" for i in attention.collect_local(ctx))
    health = client.get("/api/v1/admin/health").json()
    assert health["components"]["library_write"]["ok"] is False
    assert any(s.get("kind") == "library" and not s["ok"] for s in health["storage"])
    # autopilot biedt niets uit die map aan
    recovery.mark_heartbeat(ctx)
    ctx.config.set("global", "", "library.write_enabled", True, "t")
    ctx.services["library_provider"] = StaticProvider([LibraryItem(
        instance_id="sonarr-anime", media_kind="episode_file", provider_id="1", path=str(src),
        size=8 * 10**9, media_type="anime", codec="h264", height=1080, bitrate_kbps=20000)])
    assert (await autopilot.tick(ctx))["submitted"] == 0 and not fake.jobs
    # uitgestelde activatie onder die map
    jid = ctx.db.execute_insert(
        "INSERT INTO encoding_jobs(source_path,status,error,trial_only,policy_json,"
        "decision_json,created_at,updated_at) VALUES (?,'awaiting_activation','Activatie "
        "uitgesteld: EPERM',0,'{}','{\"retry_after\": \"2999-01-01T00:00:00+00:00\"}',?,?)",
        (str(src), "2026-01-01", "2999-01-01"))
    monkeypatch.setattr(fsops.os, "replace", real_replace)
    state = probe.run(ctx)
    assert state["ok"] and state["requeued"] == 1
    row = ctx.db.fetch_one("SELECT decision_json, updated_at FROM encoding_jobs WHERE id=?",
                           (jid,))
    assert "retry_after" not in row["decision_json"] and row["updated_at"] < "2999"
    assert client.get("/api/v1/storage/probe").json()["failing"] == []
