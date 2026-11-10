"""Verbeterpunt 6: de GPU staat stil terwijl de wachtrij vol CPU-werk staat (rapport 6, 7, 8).

Per job de baan bewaren, de wachtrij per baan begrenzen, CPU-werk overslaan zolang de CPU vol
zit en een melding als de GPU > 30 min leeg staat terwijl er werk is."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

import pytest
from test_encoding_support import install

from mediaguard.db import utcnow
from mediaguard.encoding import lanes
from mediaguard.encoding import service as enc
from mediaguard.optimization.provider import LibraryItem, StaticProvider
from mediaguard.recovery import service as recovery

SRC = b"H264-SOURCE" * 5000


@pytest.fixture
def setup(client, ctx, tmp_path):
    fake = install(ctx, ctx.settings.staging_dir)
    recovery.mark_heartbeat(ctx)
    ctx.config.set("global", "", "library.write_enabled", True, "t")
    ctx.config.set("global", "", "encoding.canary_required", 0, "t")
    ctx.config.set("global", "", "resources.encodes_cpu", 1, "t")
    ctx.config.set("global", "", "resources.encodes_gpu", 4, "t")
    # bestanden met 'dv' in de naam kiest de (nep-)encoder voor de CPU, zoals DV/HDR-
    # herkansingen in het echt; de rest gaat naar de GPU
    fake.lane_for = lambda body: "cpu" if "dv" in str(body["source_path"]) else "gpu"
    return fake, tmp_path


def _items(fake, tmp_path, names_sizes):
    out = []
    for n, (name, size) in enumerate(names_sizes, start=1):
        p = tmp_path / "library" / f"{name}.mkv"
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_bytes(SRC)
        fake.add_source(p, codec="h264", height=1080)
        out.append(LibraryItem(instance_id="sonarr-anime", media_kind="episode_file",
                               provider_id=str(n), title=name, path=str(p), size=size,
                               media_type="anime", series_id=12, episode_ids=[n],
                               codec="h264", height=1080, bitrate_kbps=20000))
    return out


async def test_cpu_work_cannot_fill_the_queue_gpu_still_gets_work(ctx, setup):
    """Zes grote CPU-bestanden staan vooraan (grootste eerst). Vroeger vulden die de hele
    rij (1 CPU + 4 GPU + 1 = 6) en kreeg de GPU niets."""
    from mediaguard.encoding import autopilot

    fake, tmp_path = setup
    cpu = [(f"dv{n}", 60 * 10**9 - n) for n in range(6)]
    gpu = [(f"gpu{n}", 5 * 10**9 - n) for n in range(3)]
    ctx.services["library_provider"] = StaticProvider(_items(fake, tmp_path, cpu + gpu))
    result = await autopilot.tick(ctx)
    by_lane = {"cpu": 0, "gpu": 0}
    for job in fake.jobs.values():
        by_lane[job["lane"]] += 1
    assert by_lane == {"cpu": 2, "gpu": 3}  # CPU: 1 plek + 1 op voorraad
    assert result["lanes"]["cpu"]["lane_full"] >= 1
    # core bewaart de baan per job
    rows = ctx.db.fetch_dicts("SELECT source_path, lane FROM encoding_jobs")
    assert {r["lane"] for r in rows if "gpu" in r["source_path"]} == {"gpu"}
    assert {r["lane"] for r in rows if "dv" in r["source_path"]} == {"cpu"}
    # geweigerd wegens volle baan: geen mislukte job, wel even overgeslagen
    assert not ctx.db.fetch_all("SELECT id FROM encoding_jobs WHERE status='failed'")
    skips = ctx.db.fetch_dicts("SELECT path, code, reason_nl FROM encoding_autopilot_skips")
    assert skips and all(s["code"] == "LANE_FULL" for s in skips)
    assert "CPU-baan zit vol" in skips[0]["reason_nl"]
    # volgende ronde: CPU vol, GPU heeft ruimte maar er is geen GPU-werk meer → niets
    again = await autopilot.tick(ctx)
    assert again["submitted"] == 0 and len(fake.jobs) == 5


async def test_lane_full_refused_by_encoder_leaves_no_job_row(ctx, setup):
    fake, tmp_path = setup
    item = _items(fake, tmp_path, [("gpu-a", 10**9)])[0]
    with pytest.raises(enc.GateError) as err:
        await enc.submit(ctx, path=item.path, instance_id="sonarr-anime",
                         media_kind="episode_file", provider_id=1, series_id=12,
                         avoid_lanes=["gpu"])
    assert err.value.code == "LANE_FULL" and err.value.lane == "gpu"
    assert not ctx.db.fetch_all("SELECT id FROM encoding_jobs")
    assert not fake.jobs


async def test_lane_known_in_core_skips_encoder_call(ctx, setup):
    """encoder=cpu (DV met behoud, of zo ingesteld): core weet de baan al en vraagt de
    encoder niets."""
    fake, tmp_path = setup
    ctx.config.set("global", "", "encoding.encoder", "cpu", "t")
    item = _items(fake, tmp_path, [("film", 10**9)])[0]
    with pytest.raises(enc.GateError) as err:
        await enc.submit(ctx, path=item.path, avoid_lanes=["cpu"])
    assert err.value.code == "LANE_FULL" and err.value.lane == "cpu"
    assert not [c for c in fake.calls if c[1] == "/jobs"]
    assert not ctx.db.fetch_all("SELECT id FROM encoding_jobs")


async def test_gpu_zero_means_cpu_route(ctx, setup):
    """'GPU-encodes tegelijk = 0' = de GPU niet gebruiken: core stuurt encoder=cpu, anders
    bleef het bestand op een GPU-baan zonder plekken staan."""
    fake, tmp_path = setup
    fake.lane_for = fake._default_lane  # zoals de echte encoder: policy cpu → CPU-baan
    ctx.config.set("global", "", "resources.encodes_gpu", 0, "t")
    item = _items(fake, tmp_path, [("film", 10**9)])[0]
    job = await enc.submit(ctx, path=item.path)
    assert job["policy"]["encoder"] == "cpu" and job["lane"] == "cpu"
    assert lanes.lane_caps(ctx) == {"cpu": 2, "gpu": 0}


async def test_sync_follows_gpu_to_cpu_fallback(ctx, setup):
    fake, tmp_path = setup
    fake.polls_until_done = 99
    item = _items(fake, tmp_path, [("gpu-b", 10**9)])[0]
    job = await enc.submit(ctx, path=item.path)
    assert job["lane"] == "gpu"
    fake.jobs[1]["lane"] = "cpu"  # HDR-metadata na GPU niet gelijk → opnieuw via CPU
    synced = await enc.sync_job(ctx, job["id"])
    assert synced["lane"] == "cpu"
    assert lanes.busy_by_lane(ctx)["cpu"] == 1


def test_budget_counts_per_lane_and_respects_total(ctx):
    budget = lanes.LaneBudget(caps={"cpu": 2, "gpu": 5}, busy={"cpu": 6, "gpu": 0}, depth=7)
    # zes oude CPU-jobs boven de CPU-grens nemen de GPU-ruimte niet in (automatisch)
    assert budget.full_lanes() == ["cpu"]
    budget.depth = 20
    assert budget.free_total() == 5
    budget.take("gpu")
    assert budget.free("gpu") == 4
    budget.depth = 7  # ingestelde totale diepte blijft een harde grens
    assert budget.free_total() == 0


def test_lane_buffer_setting_registered_and_safe_default():
    from mediaguard.settings_registry import REGISTRY

    d = REGISTRY.get_def("encoding.autopilot_lane_buffer")
    assert d.default == 1 and d.minimum == 0 and d.help_nl


async def test_runner_defers_lane_full_items_instead_of_blocking(ctx, monkeypatch):
    """Optimalisatieplan: een bestand dat alleen op de volle CPU kan, blijft gepland en
    houdt de AV1-items erachter (voor de GPU) niet tegen."""
    from mediaguard.db import dumps, utcnow
    from mediaguard.optimization import runner

    now = utcnow()
    plan = ctx.db.execute_insert(
        "INSERT INTO optimization_plans(status,filters_json,summary_json,forecast_json,"
        "item_count,created_at,created_by,updated_at) VALUES ('running','{}','{}','{}',0,?,"
        "'t',?)", (now, now))
    ids = {}
    for n, path in enumerate(("/m/dv1.mkv", "/m/dv2.mkv", "/m/gpu1.mkv")):
        ids[path] = ctx.db.execute_insert(
            "INSERT INTO optimization_items(plan_id,priority,instance_id,media_kind,"
            "provider_id,title,path,size,action,bucket,reason_nl,est_bytes,"
            "est_saving_bytes,context_json,status) VALUES (?,5,'sonarr-anime','episode_file',"
            "?,?,?,?, 'av1','av1','r',0,0,?,'planned')",
            (plan, str(n), path, path, 10**9, dumps({"series_id": 1})))
    ctx.config.set("global", "", "resources.encodes_cpu", 1, "t")
    ctx.config.set("global", "", "resources.encodes_gpu", 1, "t")
    # CPU-baan bezet door een lopende job
    ctx.db.execute_insert(
        "INSERT INTO encoding_jobs(source_path,status,policy_json,created_at,updated_at,lane) "
        "VALUES ('/m/oud.mkv','running','{}',?,?,'cpu')", (now, now))
    monkeypatch.setattr(runner, "in_window", lambda _ctx: True)
    monkeypatch.setattr(runner, "limits_state", lambda _ctx: {
        "searches_last_hour": 0, "bytes_today": 0, "active_encodes": 1,
        "active_downloads": 0, "staging_free_bytes": 10**15})
    calls = []

    async def fake_submit(_ctx, **kw):
        calls.append((kw["path"], kw["avoid_lanes"]))
        if "dv" in kw["path"] and "cpu" in kw["avoid_lanes"]:
            raise enc.GateError("LANE_FULL", lanes.lane_full_message("cpu"), lane="cpu")
        return {"id": 9, "status": "queued", "lane": "gpu"}

    monkeypatch.setattr(runner.enc, "submit", fake_submit)
    result = await runner.run_batch(ctx, plan)
    status = {r["path"]: r["status"] for r in ctx.db.fetch_dicts(
        "SELECT path, status FROM optimization_items")}
    assert status["/m/gpu1.mkv"] == "encode_submitted"
    assert status["/m/dv1.mkv"] == status["/m/dv2.mkv"] == "planned"  # niet 'blocked'
    assert result["processed"] == 1
    # volgende batch: de CPU-bestanden staan even opzij, geen nieuwe probes
    calls.clear()
    await runner.run_batch(ctx, plan)
    assert not [c for c in calls if "dv" in c[0]]


def _idle_setup(ctx, *, candidates=5, cpu_full=1, gpu_sent=0, gpu_left=None):
    ctx.config.set("global", "", "library.write_enabled", True, "t")
    ctx.config.set("global", "", "encoding.canary_required", 0, "t")
    ctx.config.set("global", "", "resources.encodes_gpu", 4, "t")
    state = {"candidates": candidates, "lanes": {"cpu": {"lane_full": cpu_full},
                                                 "gpu": {"submitted": gpu_sent}}}
    if gpu_left is not None:
        state["gpu_left"] = gpu_left
    ctx.state_set("encoding_autopilot", state)


def _notes(ctx):
    return ctx.db.fetch_dicts("SELECT title, count FROM notifications WHERE dedupe_key=?",
                              (lanes.GPU_IDLE_KEY,))


def test_gpu_idle_alert_after_30_minutes_once(client, ctx):
    from mediaguard import attention

    _idle_setup(ctx)
    t0 = datetime.now(UTC)
    assert lanes.check_gpu_idle(ctx, t0) is None  # begint te tellen
    assert lanes.check_gpu_idle(ctx, t0 + timedelta(minutes=20)) is None
    idle = lanes.check_gpu_idle(ctx, t0 + timedelta(minutes=31))
    assert idle and idle["severity"] == "warning" and not idle["cpu_only"]
    assert "GPU staat al 31 min leeg" in idle["detail_nl"]
    lanes.check_gpu_idle(ctx, t0 + timedelta(minutes=45))
    notes = _notes(ctx)
    assert len(notes) == 1 and notes[0]["count"] == 1  # één melding per stilstand
    ctx.state_set(lanes.GPU_IDLE_STATE, {"since": (t0 - timedelta(minutes=40)).isoformat()})
    keys = {i["key"] for i in attention._encoding(ctx)}
    assert lanes.GPU_IDLE_KEY in keys
    # GPU krijgt weer werk → teller weg
    now = datetime.now(UTC).isoformat()
    ctx.db.execute_insert(
        "INSERT INTO encoding_jobs(source_path,status,policy_json,created_at,updated_at,lane) "
        "VALUES ('/m/g.mkv','queued','{}',?,?,'gpu')", (now, now))
    assert lanes.check_gpu_idle(ctx, t0 + timedelta(minutes=60)) is None
    assert ctx.state_get(lanes.GPU_IDLE_STATE) is None


def test_gpu_idle_benign_pauses_and_no_work_do_not_alert(client, ctx):
    t0 = datetime.now(UTC)
    _idle_setup(ctx, candidates=0)
    lanes.check_gpu_idle(ctx, t0)
    assert lanes.check_gpu_idle(ctx, t0 + timedelta(hours=2)) is None  # geen werk
    _idle_setup(ctx)
    ctx.state_set("emergency_stop", {"active": True})
    lanes.check_gpu_idle(ctx, t0)
    assert lanes.check_gpu_idle(ctx, t0 + timedelta(hours=2)) is None
    ctx.state_set("emergency_stop", {"active": False})
    ctx.state_set("mediaserver_hold_active", True)  # iemand kijkt
    assert lanes.check_gpu_idle(ctx, t0 + timedelta(hours=2)) is None
    ctx.state_set("mediaserver_hold_active", False)
    ctx.config.set("global", "", "resources.encodes_gpu", 0, "t")  # GPU bewust uit
    assert lanes.check_gpu_idle(ctx, t0 + timedelta(hours=2)) is None
    assert not _notes(ctx)


def test_gpu_idle_only_cpu_work_is_info_without_push(client, ctx):
    _idle_setup(ctx, candidates=3, cpu_full=3, gpu_left=0)
    t0 = datetime.now(UTC)
    lanes.check_gpu_idle(ctx, t0)
    idle = lanes.check_gpu_idle(ctx, t0 + timedelta(minutes=40))
    assert idle and idle["cpu_only"] and idle["severity"] == "info"
    assert "moeten via de CPU" in idle["detail_nl"]
    assert not _notes(ctx)


def test_autopilot_status_shows_lanes(client, ctx):
    r = client.get("/api/v1/encoding/autopilot").json()
    assert set(r["lanes"]) >= {"cpu", "gpu", "depth"}
    assert "gpu_idle" in r


def test_gpu_idle_when_set_queue_is_full_of_cpu_work(client, ctx):
    """Met een vaste (te kleine) totale wachtrij vol CPU-werk krijgt de GPU niets: ook dat
    is een melding waard, met wat je eraan kunt doen."""
    _idle_setup(ctx, candidates=0, cpu_full=0)
    ctx.state_set("encoding_autopilot", {"waiting": "encoder heeft genoeg werk"})
    now = datetime.now(UTC).isoformat()
    for n in range(3):
        ctx.db.execute_insert(
            "INSERT INTO encoding_jobs(source_path,status,policy_json,created_at,updated_at,"
            "lane) VALUES (?,'queued','{}',?,?,'cpu')", (f"/m/dv{n}.mkv", now, now))
    t0 = datetime.now(UTC)
    lanes.check_gpu_idle(ctx, t0)
    idle = lanes.check_gpu_idle(ctx, t0 + timedelta(minutes=35))
    assert idle and idle["queue_full"] and not idle["cpu_only"]
    assert "wachtrij zit vol met CPU-werk" in idle["detail_nl"]
    assert len(_notes(ctx)) == 1


async def test_dv_heavy_library_gives_info_not_a_push(client, ctx, setup):
    """Een bibliotheek vol CPU-werk (HDR10+, Dolby Vision) bij een volle CPU: meer
    kandidaten dan de ronde mag weigeren (12), maar geen enkele voor de GPU. Dat is uitleg,
    geen pushmelding."""
    from mediaguard.encoding import autopilot

    fake, tmp_path = setup
    now = utcnow()
    for n in range(2):  # CPU-baan vol (1 plek + 1 voorraad)
        ctx.db.execute_insert(
            "INSERT INTO encoding_jobs(source_path,status,policy_json,created_at,updated_at,"
            "lane) VALUES (?,'queued','{}',?,?,'cpu')", (f"/m/oud{n}.mkv", now, now))
    items = _items(fake, tmp_path, [(f"dv{n}", 50 * 10**9 - n) for n in range(20)])
    for it in items:
        it.dynamic_range = "HDR10Plus"  # gaat (net als Dolby Vision) meestal via de CPU
    ctx.services["library_provider"] = StaticProvider(items)
    result = await autopilot.tick(ctx)
    assert result["submitted"] == 0
    assert result["lanes"]["cpu"]["lane_full"] == lanes.MAX_LANE_FULL_PER_ROUND
    assert result["candidates"] > lanes.MAX_LANE_FULL_PER_ROUND
    assert result["gpu_left"] == 0
    ctx.state_set("encoding_autopilot", result)
    t0 = datetime.now(UTC)
    lanes.check_gpu_idle(ctx, t0)
    idle = lanes.check_gpu_idle(ctx, t0 + timedelta(minutes=45))
    assert idle and idle["cpu_only"] and idle["severity"] == "info"
    assert not _notes(ctx)


async def test_untried_gpu_work_still_warns(client, ctx, setup):
    """Lagen er na de weigeringen nog bestanden voor de GPU, dan is een lege GPU wél een
    probleem (melding)."""
    from mediaguard.encoding import autopilot

    fake, tmp_path = setup
    now = utcnow()
    for n in range(2):
        ctx.db.execute_insert(
            "INSERT INTO encoding_jobs(source_path,status,policy_json,created_at,updated_at,"
            "lane) VALUES (?,'queued','{}',?,?,'cpu')", (f"/m/oud{n}.mkv", now, now))
    # de bibliotheek meldt geen DV, maar de encoder kiest toch de CPU (bv. omgeleid)
    items = _items(fake, tmp_path, [(f"dv{n}", 50 * 10**9 - n) for n in range(20)])
    ctx.services["library_provider"] = StaticProvider(items)
    result = await autopilot.tick(ctx)
    assert result["gpu_left"] > 0
    ctx.state_set("encoding_autopilot", result)
    t0 = datetime.now(UTC)
    lanes.check_gpu_idle(ctx, t0)
    idle = lanes.check_gpu_idle(ctx, t0 + timedelta(minutes=45))
    assert idle and not idle["cpu_only"] and idle["severity"] == "warning"
    assert "voor de GPU nog niet aangeboden" in idle["detail_nl"]
    assert len(_notes(ctx)) == 1


def test_waiting_for_placement_does_not_reset_the_idle_timer(client, ctx):
    """Een GPU-resultaat dat op plaatsing wacht, wordt elke 10 min bijgewerkt ('activatie
    uitgesteld'). Dat is geen GPU-werk en mag de stilstandteller niet steeds resetten."""
    _idle_setup(ctx)
    t0 = datetime.now(UTC) - timedelta(hours=1)
    job = ctx.db.execute_insert(
        "INSERT INTO encoding_jobs(source_path,status,policy_json,created_at,updated_at,lane) "
        "VALUES ('/m/klaar.mkv','awaiting_activation','{}',?,?,'gpu')",
        (t0.isoformat(), t0.isoformat()))
    lanes.check_gpu_idle(ctx, t0)
    later = t0 + timedelta(minutes=40)
    ctx.db.execute("UPDATE encoding_jobs SET updated_at=? WHERE id=?",
                   ((later - timedelta(minutes=2)).isoformat(), job))
    idle = lanes.check_gpu_idle(ctx, later)
    assert idle and idle["minutes"] >= 40
