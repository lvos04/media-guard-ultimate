"""Regressietests voor bugs gevonden in de echte full-stack integratietest
(tests/integration/stack). Iedere test noemt de gevonden fout."""

from __future__ import annotations

import pytest

from mediaguard.app import create_app
from mediaguard.instances.registry import hookkey_secret, seed_instances
from mediaguard.instances.service import SuspendError, ensure_hook_key

ANIME_KEY = "anime-hook-key-from-env-0123456789"


def _pregrab(instance_id: str) -> dict:
    return {"instance_id": instance_id, "app": "sonarr", "search_type": "rss",
            "release": {"title": "Show.S01E01.1080p.WEB-DL-GRP", "resolution": 1080},
            "series": {"id": 1, "tvdb_id": 1, "title": "Show", "series_type": "standard"},
            "episodes": [{"id": 1, "season_number": 1, "episode_number": 1}],
            "parsed": {"season_number": 1, "episode_numbers": [1]}}


def test_hook_key_seeded_from_env_at_startup(settings, monkeypatch):
    """Bug: core kende de hook-sleutel van de fork (MEDIAGUARD_HOOK_KEY) niet bij de eerste
    start; iedere pre-grab-call gaf 401 → fail policy. MG_INSTANCE_<ID>_HOOKKEY seedt nu
    secret + hash bij het opstarten."""
    from fastapi.testclient import TestClient

    monkeypatch.setenv("MG_INSTANCE_SONARR_ANIME_HOOKKEY", ANIME_KEY)
    app = create_app(settings)
    with TestClient(app) as c:
        ctx = app.state.ctx
        assert ctx.secrets.get(hookkey_secret("sonarr-anime")) == ANIME_KEY
        assert ctx.hook_key_owner(ANIME_KEY) == "sonarr-anime"
        r = c.post("/api/v1/hooks/pre-grab", json=_pregrab("sonarr-anime"),
                   headers={"X-MediaGuard-Hook-Key": ANIME_KEY})
        assert r.status_code == 200, r.text
        # sleutel van de ene instantie geldt niet voor een andere
        r = c.post("/api/v1/hooks/pre-grab", json=_pregrab("sonarr-series"),
                   headers={"X-MediaGuard-Hook-Key": ANIME_KEY})
        assert r.status_code == 403


def test_env_hook_key_wins_and_rotation_refused(ctx, monkeypatch):
    """Env-sleutel is leidend (de fork kan niet anders); roteren via de UI zou de fork-hooks
    breken en wordt daarom geweigerd. Zonder env-sleutel blijft roteren werken."""
    ensure_hook_key(ctx, "sonarr-series")  # willekeurige sleutel (oude situatie)
    monkeypatch.setenv("MG_INSTANCE_SONARR_SERIES_HOOKKEY", "series-env-key-abcdefghijkl")
    seed_instances(ctx)
    assert ctx.secrets.get(hookkey_secret("sonarr-series")) == "series-env-key-abcdefghijkl"
    assert ctx.hook_key_owner("series-env-key-abcdefghijkl") == "sonarr-series"
    assert ensure_hook_key(ctx, "sonarr-series") == "series-env-key-abcdefghijkl"
    with pytest.raises(SuspendError):
        ensure_hook_key(ctx, "sonarr-series", rotate=True)
    monkeypatch.delenv("MG_INSTANCE_SONARR_SERIES_HOOKKEY")
    assert ensure_hook_key(ctx, "sonarr-series", rotate=True) != "series-env-key-abcdefghijkl"


def test_webhook_test_event_is_visible(client, ctx):
    """Bug: een Test-webhook van de fork werd stil weggegooid, dus 'webhook testen' was niet
    aantoonbaar. Nu: event in outbox + tijdstip op de instantie, geen import-event."""
    from mediaguard.instances.service import ensure_hook_key

    key = ensure_hook_key(ctx, "radarr-films")
    r = client.post("/api/v1/hooks/arr-webhook/radarr-films", json={"eventType": "Test"},
                    auth=("radarr-films", key))
    assert r.status_code == 200 and r.json()["event_type"] == "Test"
    inst = client.get("/api/v1/instances/radarr-films").json()
    assert inst["webhook_last_test_at"]
    recent = client.get("/api/v1/events/recent").json()
    assert any(e["topic"] == "arr.webhook" and e["instance_id"] == "radarr-films"
               for e in recent)
    assert ctx.db.fetch_one("SELECT COUNT(*) AS n FROM import_events")["n"] == 0


async def test_target_path_exists_matches_real_sonarr_filesystem_api():
    """Bug (gemeten tegen echte Sonarr 4.0.20): de rootmapcontrole vroeg het pad mét
    slot-slash op; Sonarr geeft dan voor een NIET-bestaand pad toch ``parent: "/"`` terug,
    waardoor een ontbrekende rootmap als 'bestaat' werd gezien."""
    import httpx

    from mediaguard.arr.client import ArrClient
    from mediaguard.migration.wizard import target_path_exists

    existing = {"/media/tv", "/media/empty"}

    def handler(request: httpx.Request) -> httpx.Response:
        path = request.url.params["path"]
        root_listing = {"directories": [{"path": "/media/"}], "files": []}
        if path.endswith("/"):  # echt gedrag: altijd 'parent', ook als het pad niet bestaat
            return httpx.Response(200, json={"parent": "/", "directories": [], "files": []})
        if path in existing:
            return httpx.Response(200, json={"parent": "/media/", "directories": [],
                                             "files": []})
        return httpx.Response(200, json=root_listing)

    client = ArrClient("http://sonarr:8989", "k" * 32, instance_id="t", app="sonarr",
                       transport=httpx.MockTransport(handler))
    async with client:
        assert await target_path_exists(client, "/media/tv/") is True
        assert await target_path_exists(client, "/media/empty") is True
        assert await target_path_exists(client, "/media/missing") is False
        assert await target_path_exists(client, "/media/missing/") is False


def test_season_monitor_also_monitors_its_episodes(client, ctx):
    """Bug (gemeten tegen echte Sonarr 4.0.20): 'seizoen monitoren' in MediaGuard ging via
    /seasonpass, dat alleen de seizoensvlag zet; de afleveringen bleven ongemonitord en er werd
    dus nooit iets gedownload. Nu via PUT /series (zoals de Sonarr-UI): afleveringen van het
    gewijzigde seizoen volgen, andere seizoenen houden hun uitzonderingen."""
    from test_arr_fakes import FakeArr, make_episode, make_series, wire

    fake = wire(ctx, "sonarr-series", FakeArr("sonarr"))
    fake.add_series(make_series(1, "Show", 100, seasons={1: False, 2: True}),
                    [make_episode(11, 1, 1, 1, monitored=False),
                     make_episode(12, 1, 1, 2, monitored=False),
                     make_episode(21, 1, 2, 1, monitored=False)])
    r = client.put("/api/v1/instances/sonarr-series/series/1/seasons/1", json={"monitored": True})
    assert r.status_code == 200, r.text
    assert fake.episodes[11]["monitored"] and fake.episodes[12]["monitored"]
    assert fake.calls_to("POST", "/api/v3/seasonpass") == []
    assert fake.episodes[21]["monitored"] is False  # ongewijzigd seizoen: uitzondering blijft
    r = client.put("/api/v1/instances/sonarr-series/series/1/seasons",
                   json={"seasons": {"1": True, "2": True}, "monitor_new_items": "all"})
    assert r.status_code == 200
    assert fake.series[1]["monitorNewItems"] == "all"


def test_shadow_copy_remaps_radarr_collection_root_folder(tmp_path):
    """Bug (echte Radarr 6.4-restore): Collections.RootFolderPath werd niet herschreven; het
    doel meldde 'Missing root folder for movie collection: /movies' → gereedheid NO-GO."""
    import sqlite3

    from mediaguard.migration.backupfile import _patch_db

    db = tmp_path / "radarr.db"
    conn = sqlite3.connect(db)
    conn.executescript(
        "CREATE TABLE RootFolders(Id INTEGER PRIMARY KEY, Path TEXT);"
        "CREATE TABLE Movies(Id INTEGER PRIMARY KEY, Path TEXT);"
        "CREATE TABLE Collections(Id INTEGER PRIMARY KEY, Title TEXT, RootFolderPath TEXT);"
        "INSERT INTO RootFolders VALUES (1,'/movies');"
        "INSERT INTO Movies VALUES (1,'/movies/Night of the Living Dead (1968)');"
        "INSERT INTO Collections VALUES (1,'Some Collection','/movies');")
    conn.commit()
    conn.close()
    state = _patch_db(db, [("/movies", "/media/movies")])
    conn = sqlite3.connect(db)
    assert conn.execute("SELECT RootFolderPath FROM Collections").fetchone()[0] == "/media/movies"
    assert state["remapped"]["Collections.RootFolderPath"] == 1


async def test_encoder_probe_wrapper_is_unwrapped():
    """Bug (echte mediaguard/encode-container): POST /api/mediaguard/v1/probe antwoordt met
    ``{path, probe:{codec, audio[], ...}, dv_route, inventory, stat}``. Core las codec/audio
    op het hoogste niveau → post-import-analyse meldde 'Geen audiospoor gevonden' en
    AV1-/DV-poorten zagen geen codec."""
    import httpx

    from mediaguard.audio import inventory
    from mediaguard.encoding.client import EncoderClient

    real = {"path": "/media/tv/x.mkv", "dv_route": None, "stat": {"size": 1},
            "probe": {"codec": "h264", "width": 320, "dv_profile": None,
                      "audio": [{"index": 1, "codec": "aac", "kanalen": 1, "taal": "eng",
                                 "titel": "", "commentaar": False}], "subs": []},
            "inventory": {"languages": ["eng"]}}
    client = EncoderClient("http://enc:8000", "t" * 30, transport=httpx.MockTransport(
        lambda req: httpx.Response(200, json=real)))
    probe = await client.probe("/media/tv/x.mkv")
    assert probe["codec"] == "h264" and probe["stat"] == {"size": 1}
    inv = inventory.from_encoder_probe(probe)
    assert [a.language for a in inv.audio] == ["en"]
    flat = {"codec": "hevc", "audio": []}
    client = EncoderClient("http://enc:8000", "t" * 30, transport=httpx.MockTransport(
        lambda req: httpx.Response(200, json=flat)))
    assert (await client.probe("/x"))["codec"] == "hevc"


async def test_encoder_job_policy_only_contract_fields():
    """Bug (echte encoder, extra=forbid): POST /jobs → 422 omdat core 'profile_id',
    'dv_route' en 'audio_policy.mode' meestuurde. Alleen contractvelden versturen."""
    import json as _json

    import httpx

    from mediaguard.encoding.client import EncoderClient

    seen = {}

    def handler(req: httpx.Request) -> httpx.Response:
        seen["body"] = _json.loads(req.content)
        return httpx.Response(200, json={"job_id": 1})

    client = EncoderClient("http://enc:8000", "t" * 30, transport=httpx.MockTransport(handler))
    await client.create_job({"source_path": "/media/tv/x.mkv", "policy": {
        "encoder": "auto", "target": "staging", "dv_mode": "keep_original",
        "dv_allowed_profiles": [], "dv_route": None, "hdr10plus": "allow",
        "audio_policy": {"keep_languages": ["eng"], "keep_all": True, "mode": "original"},
        "subtitle_policy": {"keep_all": True}, "min_saving_percent": 15,
        "profile_id": "standaard", "crf": 30}})
    pol = seen["body"]["policy"]
    assert "profile_id" not in pol and "dv_route" not in pol
    assert pol["audio_policy"] == {"keep_languages": ["eng"], "keep_all": True}
    assert pol["hdr10plus"] == "skip" and pol["crf"] == 30
    assert seen["body"]["source_path"] == "/media/tv/x.mkv"


def test_force_duplicate_add_is_excluded_from_anime_routing(client, ctx):
    """Bug (echte stack): een bewust dubbel toegevoegde anime-serie in sonarr-series
    (force_duplicate, R6.2) werd 1 s later door de automatische anime-routering uit
    sonarr-series verwijderd (SeriesDelete), omdat sonarr-anime hem al had."""
    from test_arr_fakes import FakeArr, wire

    from mediaguard.routing.service import _excluded

    wire(ctx, "sonarr-series", FakeArr("sonarr"))
    ctx.db.execute("UPDATE instances SET default_quality_profile_id=1, "
                   "default_root_folder='/tv/' WHERE id='sonarr-series'")
    client.post("/api/v1/instances/sonarr-series/series",
                json={"tvdb_id": 76885, "force_duplicate": True})
    assert _excluded(ctx, "sonarr-series", "series", 76885)
    assert not _excluded(ctx, "sonarr-series", "series", 81189)


def test_provisioning_root_defaults_match_truenas_compose():
    """Compose (TRaSH-indeling) en de standaard-rootmappen van provisioning moeten gelijk zijn:
    forks, core, encoder en recovery zien ${MG_DATA_ROOT} allemaal als /data, en
    MG_LIBRARY_ROOTS=/data omvat alle vier de rootmappen."""
    from pathlib import Path

    import yaml

    from mediaguard.instances.registry import DEFAULT_INSTANCES
    from mediaguard.provisioning.templates import DEFAULT_ROOT

    compose = Path(__file__).resolve().parents[3] / "deploy" / "truenas" / "compose.yaml"
    doc = yaml.safe_load(compose.read_text(encoding="utf-8"))
    svcs = doc["services"]

    def targets(svc: str) -> set[str]:
        # benoemde volumes met subpad (één pad per dataset in de compose)
        return {v["target"] for v in svcs[svc]["volumes"]
                if isinstance(v, dict) and v.get("source") == "mg_data"}

    for inst in DEFAULT_INSTANCES:
        root = DEFAULT_ROOT[inst["media_type"]]
        assert root.startswith("/data/media/")
        mounts = targets(f"mediaguard-{inst['id']}")
        assert mounts == {"/data"}, (inst["id"], mounts)   # downloads + media in één mount
    # core/recovery/encoder zien de hele data-dataset (bestaande indelingen van de gebruiker
    # werken zonder verplaatsen); dezelfde paden als in de forks
    for svc in ("mediaguard-core", "mediaguard-recovery", "mediaguard-encode"):
        assert targets(svc) == {"/data"}, svc
    lib_roots = svcs["mediaguard-core"]["environment"]["MG_LIBRARY_ROOTS"]
    assert lib_roots == "/data"
    assert all(r.startswith(lib_roots + "/") for r in DEFAULT_ROOT.values())
    assert svcs["mediaguard-encode"]["environment"]["MG_MEDIA_ROOTS"] == "/data"


async def test_provisioning_path_visible_matches_real_filesystem_api():
    """Zelfde valkuil als bij de migratie: Sonarr geeft voor een niet-bestaand pad mét
    slot-slash toch 'parent' terug; provisioning zag een ontbrekende rootmap als aanwezig."""
    import httpx

    from mediaguard.arr.client import ArrClient
    from mediaguard.provisioning.engine import path_visible

    def handler(request: httpx.Request) -> httpx.Response:
        path = request.url.params["path"]
        if path.endswith("/"):
            if path == "/data/media/":
                return httpx.Response(200, json={"parent": "/data/", "directories": [
                    {"path": "/data/media/tv/"}], "files": []})
            return httpx.Response(200, json={"parent": "/", "directories": [], "files": []})
        if path == "/data/media/tv":
            return httpx.Response(200, json={"parent": "/data/media/", "directories": [],
                                             "files": []})
        return httpx.Response(200, json={"directories": [{"path": "/data/"}], "files": []})

    client = ArrClient("http://s:8989", "k" * 32, instance_id="t", app="sonarr",
                       transport=httpx.MockTransport(handler), retries=0)
    async with client:
        assert await path_visible(client, "/data/media/tv") is True
        assert await path_visible(client, "/data/media/anime") is False


async def test_concurrent_webhook_registration_creates_one_connection(client, ctx):
    """Bug (stacktest): provisioning op de achtergrond en 'Webhook registreren' in de UI
    tegelijk → twee 'MediaGuard'-connections in Sonarr; daarna faalt elke test/update met
    'Name should be unique'. Nu per instantie geserialiseerd + dubbelen opgeruimd."""
    import asyncio

    from test_arr_fakes import FakeArr, wire

    from mediaguard.instances.service import register_webhook

    fake = wire(ctx, "sonarr-anime", FakeArr("sonarr", api_key="anime-key-999"))
    await asyncio.gather(*(register_webhook(ctx, "sonarr-anime", "test") for _ in range(4)))
    notes = list(fake.providers["notification"].values())
    assert len(notes) == 1, [n.get("id") for n in notes]
    # een al bestaand dubbel wordt bij de volgende registratie verwijderd
    dup = dict(notes[0], id=99)
    fake.providers["notification"][99] = dup
    await register_webhook(ctx, "sonarr-anime", "test")
    assert len(fake.providers["notification"]) == 1


def test_recovery_readiness_uses_quarantine_writability_seen_by_recovery(ctx, monkeypatch):
    """Bug (stacktest): readiness wordt in core berekend, maar core heeft /work/quarantine
    (terecht) alleen-lezen → 'quarantine_writable: False' → bij centrale quarantaine zou elke
    activatie geweigerd worden. De recovery-container meet het nu zelf (heartbeat)."""
    from mediaguard.recovery import service

    ctx.config.set("global", "", "recovery.quarantine_location", "central", "test")
    ctx.state_set(service.HEARTBEAT_KEY, {"ts": service.utcnow(), "pid": 1,
                                          "role": "recovery", "quarantine_writable": True})
    monkeypatch.setattr(service, "_quarantine_writable", lambda _ctx: False)  # core-kant: ro
    r = service.readiness(ctx)
    assert r["ready"] is True and r["quarantine_writable"] is True
    ctx.state_set(service.HEARTBEAT_KEY, {"ts": service.utcnow(), "pid": 1,
                                          "role": "recovery", "quarantine_writable": False})
    assert service.readiness(ctx)["ready"] is False


def test_no_runtime_hint_refers_to_removed_compose_switches():
    """ADR-011: MG_LIBRARY_MODE/MG_FORK_MEDIA_MODE en de *_APIKEY/*_HOOKKEY-variabelen bestaan
    niet meer in de compose. Meldingen aan de gebruiker mogen er niet meer naar verwijzen
    (de rootmap-hostactie noemde nog MG_FORK_MEDIA_MODE=rw)."""
    from pathlib import Path

    import mediaguard

    pkg = Path(mediaguard.__file__).resolve().parent
    offenders = [str(p.relative_to(pkg)) for p in pkg.rglob("*.py")
                 if any(v in p.read_text(encoding="utf-8")
                        for v in ("MG_FORK_MEDIA_MODE", "MG_LIBRARY_MODE"))]
    assert offenders == []


def test_compose_volume_mounts_never_copy_image_content():
    """Stacktest (zero-config, ADR-011): zonder ``nocopy`` kopieert Docker bij een lege
    volume-submap de inhoud én eigenaar van het image-pad erin. Het Sonarr/Radarr-image heeft
    /config van uid 1000 → de door init aangemaakte map werd 1000:1000 en de fork startte niet
    ("AppFolder /config is not writable")."""
    from pathlib import Path

    import yaml

    class Loader(yaml.SafeLoader):
        pass

    for tag in ("!reset", "!override"):   # compose-merge-tags in de dev-overlay
        Loader.add_constructor(tag, lambda loader, node: loader.construct_sequence(node))
    root = Path(__file__).resolve().parents[3] / "deploy"
    for f in ("truenas/compose.yaml", "dev/compose.dev.yaml"):
        doc = yaml.load((root / f).read_text(encoding="utf-8"), Loader=Loader)  # noqa: S506
        for name, svc in doc["services"].items():
            for v in svc.get("volumes") or []:
                if isinstance(v, dict) and v.get("type") == "volume":
                    assert (v.get("volume") or {}).get("nocopy") is True, (f, name, v)
