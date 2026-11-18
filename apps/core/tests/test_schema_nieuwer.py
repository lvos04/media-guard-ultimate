"""Bescherm bestaande voortgang als de database bij een andere release hoort."""

from __future__ import annotations

import json
import sqlite3
from contextlib import closing
from pathlib import Path

import pytest
from fastapi.testclient import TestClient
from test_upgradebewijs import ub

from mediaguard import emergency
from mediaguard.app import create_app
from mediaguard.config import Settings
from mediaguard.db import Database

MELDING = ("database is van een nieuwere MediaGuard-versie; zet de bijpassende versie "
           "of de set van vóór de update terug")


@pytest.fixture
def nieuw_schema(tmp_path: Path) -> Path:
    path = tmp_path / "mediaguard.db"
    db = Database(path)
    db.initialize()
    db.close()
    with closing(sqlite3.connect(path)) as conn, conn:
        conn.execute("INSERT INTO schema_migrations VALUES ('001_andere_migratie', 'fixture')")
        # Ook een bekende migratie ontbreekt. Die mag nu niet worden toegepast.
        conn.execute("DELETE FROM schema_migrations WHERE version='005_api_key_scopes'")
    return path


def test_initialize_weigert_onbekende_migratie(nieuw_schema: Path) -> None:
    voor = ub.meet_db(nieuw_schema)
    db = Database(nieuw_schema)
    db.initialize()
    db.close()
    assert ub.meet_db(nieuw_schema) == voor
    assert not (nieuw_schema.parent / "backups").exists()
    assert db.read_only


@pytest.mark.parametrize("rol", ["core", "recovery"])
def test_herstelweergave_blokkeert_alle_routes_en_achtergrondwerk(
    nieuw_schema: Path, rol: str,
) -> None:
    voor = ub.meet_db(nieuw_schema)
    settings = Settings(role=rol, data_dir=nieuw_schema.parent, start_background=True)
    app = create_app(settings)
    assert emergency.active(app.state.ctx)
    with TestClient(app) as client:
        for verb, path in (
            ("GET", "/api/v1/auth/me"), ("POST", "/api/v1/auth/login"),
            ("POST", "/api/v1/backups"), ("POST", "/api/v1/backups/kopie/restore"),
            ("POST", "/api/v1/system/emergency-stop"), ("POST", "/api/v1/hooks/pre-grab"),
            ("GET", "/api/v1/onbekend"), ("GET", "/connector/v1/discover"),
        ):
            response = client.request(verb, path, json={} if verb == "POST" else None)
            assert response.status_code == 409
            assert response.json()["code"] == "schema_nieuwer"
            assert response.json()["message"] == MELDING
            assert response.json()["trace_id"]
        for path in ("/api/v1/health", "/healthz", "/readyz", "/connector/v1/health",
                     "/api/v1/health/summary", "/api/v1/admin/health"):
            response = client.get(path)
            assert response.status_code == 503
            assert response.json()["status"] == "down"
            assert response.json()["reason_code"] == "schema_nieuwer"
            if path == "/connector/v1/health":
                health = response.json()
                assert health["observed_at"]
                assert {check["name"]: check["status"] for check in health["checks"]} == {
                    "database": "down", "worker": "unknown", "jobs": "unknown",
                }
    assert ub.meet_db(nieuw_schema) == voor
    assert not (nieuw_schema.parent / "backups").exists()


def test_huidig_schema_start_gewoon(tmp_path: Path) -> None:
    app = create_app(Settings(data_dir=tmp_path, start_background=False))
    with TestClient(app) as client:
        assert client.get("/api/v1/health").json()["status"] == "ok"
        assert client.get("/api/v1/auth/setup-status").status_code == 200
    assert not app.state.ctx.db.read_only
    assert not emergency.active(app.state.ctx)
    app.state.ctx.db.close()


def test_noodtoegang_en_backups_weigeren(nieuw_schema: Path, capsys) -> None:
    from mediaguard import noodtoegang, schema
    from mediaguard.maintenance import api, preupdate

    voor = ub.meet_db(nieuw_schema)
    assert noodtoegang.main(["--db", str(nieuw_schema), "uitloggen"]) != 0
    assert MELDING in capsys.readouterr().err
    ctx = create_app(Settings(data_dir=nieuw_schema.parent)).state.ctx
    with pytest.raises(schema.SchemaTooNew):
        api.create_backup(ctx)
    assert api.run_cycle(ctx) == {}
    with pytest.raises(schema.SchemaTooNew):
        Database(nieuw_schema).backup_to(nieuw_schema.parent / "backups" / "kopie.db")
    with pytest.raises(schema.SchemaTooNew):
        preupdate.make_set(nieuw_schema.parent / "backups", nieuw_schema, encode_dir=None)
    assert not (nieuw_schema.parent / "backups").exists()
    assert ub.meet_db(nieuw_schema) == voor


def test_haakt_nieuwe_download_af_zonder_forklog(nieuw_schema: Path) -> None:
    from mediaguard.hookguard import REJECT, Guard

    voor = ub.meet_db(nieuw_schema)
    guard = Guard("localhost", 1, timeout=0.1, flag=None, db=nieuw_schema)
    code, body = guard.fallback("/api/v1/hooks/pre-grab")
    assert code == 200
    assert json.loads(body) == REJECT
    assert ub.meet_db(nieuw_schema) == voor


def test_bewaker_weigert_voorbereiding_en_snapshot(nieuw_schema: Path, monkeypatch) -> None:
    from mediaguard import aio, schema

    core = nieuw_schema.parent / "core"
    core.mkdir()
    db = core / nieuw_schema.name
    nieuw_schema.rename(db)
    voor = ub.meet_db(db)
    env: dict[str, str] = {}
    monkeypatch.setattr(aio, "prepare", lambda _env: pytest.fail("mapvoorbereiding aangeroepen"))
    assert aio.prepare_start(env, core.parent, None) == [MELDING]
    assert env[schema.AIO_RECOVERY_ENV] == "1"
    assert MELDING in aio.before_services({}, core.parent, None)
    specs = aio.build_specs(env)
    assert {spec.name for spec in specs} == {"core", "web", "hookguard"}
    assert not (core / "backups").exists()
    assert ub.meet_db(db) == voor


def test_onbekend_av1_schema_houdt_hele_aio_stil(tmp_path: Path, monkeypatch, capsys) -> None:
    from mediaguard import aio, noodtoegang, schema
    from mediaguard.maintenance import bundle

    env = {"MG_AIO_ENCODER_DIR": str(ub.ROOT / "services" / "encoder")}
    monkeypatch.setenv("MG_AIO_ENCODER_DIR", env["MG_AIO_ENCODER_DIR"])
    core = tmp_path / "core" / "mediaguard.db"
    db = Database(core)
    db.initialize()
    db.close()
    encoder = tmp_path / "encode" / "app.db"
    encoder.parent.mkdir()
    with closing(sqlite3.connect(encoder)) as conn, conn:
        conn.execute("CREATE TABLE schema_versie (versie INTEGER PRIMARY KEY)")
        conn.execute("INSERT INTO schema_versie VALUES (999)")
        conn.execute("CREATE TABLE backup_status (id INTEGER PRIMARY KEY)")
    voor = ub.meet(tmp_path, intern=True)
    monkeypatch.setattr(aio, "prepare", lambda _env: pytest.fail("mapvoorbereiding aangeroepen"))

    assert aio.prepare_start(env, tmp_path, None) == [MELDING]
    assert env[schema.AIO_RECOVERY_ENV] == "1"
    assert MELDING in aio.before_services(env.copy(), tmp_path, None)
    assert noodtoegang.main(["--db", str(core), "uitloggen"]) != 0
    assert MELDING in capsys.readouterr().err
    with pytest.raises(schema.SchemaTooNew):
        bundle.restore(tmp_path / "bundel", "fixture", tmp_path / "sleutel", tmp_path)
    assert ub.meet(tmp_path, intern=True) == voor
    assert not list(tmp_path.glob("*/backups"))


def test_bewaker_past_alleen_eerder_klaargezet_herstel_toe(tmp_path: Path, monkeypatch) -> None:
    from mediaguard import aio, schema
    from mediaguard.maintenance import preupdate

    core = tmp_path / "core" / "mediaguard.db"
    db = Database(core)
    db.initialize()
    db.close()
    voor = ub.meet_db(core, intern=True)
    sets = core.parent / "backups" / preupdate.FOLDER
    made = preupdate.make_set(sets, core, encode_dir=None)
    preupdate.stage(core.parent, made, actor="fixture")
    with closing(sqlite3.connect(core)) as conn, conn:
        conn.execute("INSERT INTO schema_migrations VALUES ('999_toekomstig', 'fixture')")
    nieuw = ub.meet_db(core, intern=True)

    monkeypatch.setattr(aio, "prepare", lambda _env: [])
    env: dict[str, str] = {}
    assert any("teruggezet" in regel for regel in aio.prepare_start(env, tmp_path, None))
    assert not env.get(schema.AIO_RECOVERY_ENV)
    assert aio.before_services(env, tmp_path, None) == []
    assert list(sets.iterdir()) == [made]
    assert ub.meet_db(core, intern=True) == voor
    eerdere_db, = (core.parent / "backups" / preupdate.ASIDE_FOLDER).glob("*/core__mediaguard.db")
    assert ub.meet_db(eerdere_db, intern=True) == nieuw
    result = preupdate.take_result(core.parent)
    assert result and result["ok"]


@pytest.mark.parametrize("ingang", ["core", "snapshot"])
def test_start_leest_schema_opnieuw_na_wachten_op_startslot(
    tmp_path: Path, monkeypatch, ingang: str,
) -> None:
    from concurrent.futures import ThreadPoolExecutor
    from threading import Event

    from mediaguard import schema
    from mediaguard.db import MIGRATIONS_DIR
    from mediaguard.maintenance import preupdate

    core = tmp_path / "core" / "mediaguard.db"
    db = Database(core)
    db.initialize()
    db.close()
    gelezen = Event()
    echt = schema.applied_versions

    def meet(*args, **kwargs):
        gelezen.set()
        return echt(*args, **kwargs)

    monkeypatch.setattr(schema, "applied_versions", meet)
    with ThreadPoolExecutor(max_workers=1) as pool:
        with schema.startup_lock(core.parent):
            if ingang == "core":
                toekomst = pool.submit(db.initialize)
            else:
                toekomst = pool.submit(preupdate.snapshot_before_start, tmp_path,
                                       core_migrations=MIGRATIONS_DIR,
                                       encoder_migrations=None, app_version="test")
            assert not gelezen.wait(timeout=0.15)
            with closing(sqlite3.connect(core)) as conn, conn:
                conn.execute("INSERT INTO schema_migrations VALUES ('999_toekomstig', 'fixture')")
                conn.execute("DELETE FROM schema_migrations WHERE version='005_api_key_scopes'")
            voor = ub.meet_db(core, intern=True)
        if ingang == "core":
            toekomst.result(timeout=2)
            assert db.read_only
        else:
            with pytest.raises(schema.SchemaTooNew):
                toekomst.result(timeout=2)
    assert ub.meet_db(core, intern=True) == voor
    assert not (core.parent / "backups").exists()


def test_bewaker_onderscheidt_ongezond_van_vastgelopen(tmp_path: Path, monkeypatch) -> None:
    import io
    import urllib.error
    import urllib.request

    from mediaguard import aio

    def antwoord(*_args, **_kwargs):
        body = io.BytesIO(b'{"status":"down","reason_code":"schema_nieuwer"}')
        raise urllib.error.HTTPError("http://localhost", 503, "onbeschikbaar", {}, body)

    monkeypatch.setattr(urllib.request, "urlopen", antwoord)
    def herstart(*_args, **_kwargs):
        pytest.fail("de bewaker behandelt schemaherstel als een vastgelopen proces")

    monkeypatch.setattr(aio.Supervisor, "_terminate", staticmethod(herstart))
    spec = aio.Spec("core", [], {}, health="http://localhost", grace=0)
    spec.proc = type("Draait", (), {"poll": lambda _self: None, "pid": 2})()
    vlag = tmp_path / "gezondheid"
    supervisor = aio.Supervisor([spec], None, None, health_path=vlag, schema_recovery=True)
    for _ in range(4):
        assert supervisor.check_health() == []
    assert spec.fails == 0
    assert supervisor.snapshot()["healthy"] is False
    supervisor.write_state()
    assert vlag.read_text() == "fail schema_nieuwer\n"
