"""Negatieve proeven voor migratie 151, hervatten en het contract van de eventroute."""

from __future__ import annotations

import errno
import json
import os
import sqlite3
from contextlib import aclosing, closing, contextmanager
from pathlib import Path
from types import SimpleNamespace
from uuid import UUID

import pytest
from av1_fixture import maak_av1_v1
from fastapi import Request
from fastapi.testclient import TestClient
from test_connector_events import PATH, expired, page, publish, row_position
from test_connector_lezen import database_snapshot, error, key

from mediaguard import aio
from mediaguard import db as database
from mediaguard.api.events import stream
from mediaguard.app import create_app
from mediaguard.events import EventBus
from mediaguard.maintenance import preupdate
from mediaguard.maintenance.housekeeping import prune

STAMP = "2020-01-01T00:00:00Z"


@pytest.fixture
def old_schema(tmp_path, settings, monkeypatch):
    previous = tmp_path / "voor-151"
    previous.mkdir()
    for script in database.MIGRATIONS_DIR.glob("*.sql"):
        if script.name < "151_":
            (previous / script.name).symlink_to(script)
    with monkeypatch.context() as old, closing(database.Database(settings.db_path)) as db:
        old.setattr(database, "MIGRATIONS_DIR", previous)
        db.initialize()
        db.execute(
            "INSERT INTO outbox_events(ts,topic,payload) VALUES (?,?,?)",
            (STAMP, "library.synced", "{}"),
        )
    return previous


@pytest.mark.parametrize("via", ["supervisor", "kern"])
def test_archieffout_stopt_voor_migratie_151(settings, old_schema, monkeypatch, via):
    path = settings.db_path
    replace = os.replace
    attempts = []

    def full(src, dst):
        if Path(dst).name == "mediaguard.db" and Path(dst).parent.name.endswith(".part"):
            attempts.append(1)
            raise OSError(errno.ENOSPC, "archiefopslag vol")
        return replace(src, dst)

    # De supervisor kent de vaste appdatamap 'core'.
    config = path.parent.parent / "aio-data"
    core = config / "core"
    config.mkdir()
    path.parent.rename(core)
    path = core / path.name
    with monkeypatch.context() as blocked, closing(database.Database(path)) as db:
        blocked.setattr(os, "replace", full)
        with pytest.raises(RuntimeError, match="back-up"):
            if via == "kern":
                db.initialize()
            else:
                aio.before_services({}, config, None)
    assert attempts == [1]
    with closing(sqlite3.connect(path)) as reader:
        assert not reader.execute(
            "SELECT 1 FROM schema_migrations WHERE version='151_outbox_event_identity'"
        ).fetchone()
        assert reader.execute("SELECT topic FROM outbox_events").fetchall() == [("library.synced",)]
    assert not list(core.glob("backups/voor-update/*/meta.json"))
    with closing(database.Database(path)) as db:
        db.initialize()
        assert db.fetch_one(
            "SELECT 1 FROM schema_migrations WHERE version='151_outbox_event_identity'"
        )
    sets = list(core.glob("backups/voor-update/*/meta.json"))
    assert len(sets) == 1 and not preupdate.verify(sets[0].parent)


@contextmanager
def aangemeld(settings, *, nieuw=True):
    app = create_app(settings)
    with closing(app.state.ctx.db), TestClient(app) as client:
        route = "setup" if nieuw else "login"
        response = client.post(
            f"/api/v1/auth/{route}",
            json={
                "username": "admin",
                "password": "geheim-wachtwoord",
            },
        )
        assert response.status_code == 200
        yield app.state.ctx, client


def herstel(snapshot, path):
    with closing(sqlite3.connect(snapshot)) as saved, closing(sqlite3.connect(path)) as live:
        saved.backup(live)


@pytest.mark.parametrize("zonder_generatie", [False, True])
async def test_generatie_vanaf_151_en_bij_bestaande_installatie(
    settings, old_schema, monkeypatch, zonder_generatie,
):
    migraties = database.MIGRATIONS_DIR
    with closing(database.Database(settings.db_path)) as db:
        with monkeypatch.context() as oud:
            oud.setattr(database, "MIGRATIONS_DIR", old_schema)
            for versie in (151, 152, 153):
                script, = migraties.glob(f"{versie}_*.sql")
                (old_schema / script.name).symlink_to(script)
                db.initialize()
                if versie == 151:
                    eerste = db.fetch_one("SELECT generation FROM outbox_generation")[0]
                    assert len(eerste) == 16
            if zonder_generatie:
                # Schema van een installatie die 151 en 153 al vóór dit herstel toepaste.
                db.execute("DROP TABLE outbox_generation")
        db.execute("INSERT INTO encoding_jobs(source_path,status,policy_json,progress,created_at,"
                   "updated_at) VALUES(?, 'running', '{}', 0.42, ?, ?)",
                   (str(settings.staging_dir / "proef.mkv"), STAMP, STAMP))
        ctx = SimpleNamespace(db=db)
        voor = database_snapshot(ctx)
        db.initialize()
        na = database_snapshot(ctx)
        for (naam, sql), rijen in voor.items():
            if naam == "schema_migrations":
                continue
            if naam == "encoding_jobs":
                # Alleen de nieuwe revisiekolom van migratie 160 mag erbij komen.
                assert sql.endswith(")")
                sql = sql[:-1] + ", revision INTEGER NOT NULL DEFAULT 0)"
                rijen = {(*rij, ("revision", 0)): aantal for rij, aantal in rijen.items()}
            assert na[naam, sql] == rijen, naam
        generatie = db.fetch_one("SELECT generation FROM outbox_generation")[0]
        assert len(generatie) == 16
        assert (generatie != eerste) == zonder_generatie

    with aangemeld(settings) as (ctx, client):
        eerste_pagina = page(client)
        assert len(eerste_pagina["events"]) == 1
        assert generatie.hex() in eerste_pagina["next_cursor"].split(".")
        reset, event = await sse_herlezing(ctx, "1")
        controleer_sse_reset(reset)
        last_id = event.splitlines()[0].removeprefix("id: ")
        assert generatie.hex() in last_id.split(".")
        for cursor in ("v1.0.0", "v2.0.0." + "0" * 64):
            expired(client.get(PATH, params={"cursor": cursor}))
    with aangemeld(settings, nieuw=False) as (_, client):
        assert page(client) == eerste_pagina


def test_afzonderlijke_bytegelijke_publicaties_na_herstel(
    settings, old_schema, tmp_path, monkeypatch,
):
    snapshot = tmp_path / "lege-outbox.db"
    with closing(database.Database(settings.db_path)) as db:
        db.execute("DELETE FROM outbox_events")
        db.backup_to(snapshot)

    def publiceer_en_upgrade(vorige_cursor=None):
        with monkeypatch.context() as old, closing(database.Database(settings.db_path)) as db:
            old.setattr(database, "MIGRATIONS_DIR", old_schema)
            old.setattr("mediaguard.events.utcnow", lambda: STAMP)
            db.initialize()
            assert db.fetch_one("SELECT COUNT(*) FROM outbox_events")[0] == 0
            EventBus(db).publish("library.synced", {})
            rij = tuple(db.fetch_one("SELECT * FROM outbox_events"))
        with aangemeld(settings) as (_, client):
            if vorige_cursor:
                expired(client.get(PATH, params={"cursor": vorige_cursor}))
            pagina = page(client)
            assert page(client) == pagina
            assert len(pagina["events"]) == 1
            assert page(client, cursor=pagina["next_cursor"])["events"] == []
            return rij, pagina

    eerste_rij, eerste = publiceer_en_upgrade()
    herstel(snapshot, settings.db_path)
    tweede_rij, tweede = publiceer_en_upgrade(eerste["next_cursor"])
    assert eerste_rij == tweede_rij
    a, b = eerste["events"][0], tweede["events"][0]
    assert a["event_id"] != b["event_id"]
    assert a["source_event_id"] != b["source_event_id"]


def controleer_sse_reset(frame):
    regels = frame.splitlines()
    assert regels[0] == "event: reset"
    assert "id:" in regels
    body = json.loads(next(regel[6:] for regel in regels if regel.startswith("data: ")))
    assert set(body) == {"code", "message", "details", "trace_id"}
    assert body["code"] == "cursor_expired" and body["message"]
    assert body["details"] == {"restart": True}
    UUID(body["trace_id"])


async def sse_herlezing(ctx, last_id):
    request = Request({"type": "http", "headers": [(b"last-event-id", last_id.encode())]})
    response = await stream(request, None, ctx)
    frames = []
    async with aclosing(response.body_iterator) as chunks:
        async for chunk in chunks:
            if chunk.startswith("retry:"):
                break
            frames.append(chunk)
    return frames


def test_terugzetten_en_tweede_upgrade_geeft_geen_ids_opnieuw(settings, old_schema, monkeypatch):
    snapshot = settings.db_path.parent.parent / "snapshot.db"
    with closing(database.Database(settings.db_path)) as db:
        db.backup_to(snapshot)
    with aangemeld(settings) as (ctx, client):
        ctx.events.publish("library.synced", {})
        first = page(client)
        common, original = first["events"]

    herstel(snapshot, settings.db_path)
    with monkeypatch.context() as old, closing(database.Database(settings.db_path)) as db:
        old.setattr(database, "MIGRATIONS_DIR", old_schema)
        db.initialize()
        db.execute(
            "INSERT INTO outbox_events(ts,topic,payload) VALUES (?,?,?)",
            (STAMP, "recovery.committed", "{}"),
        )

    with aangemeld(settings) as (_, client):
        repeated, replacement = page(client)["events"]
        identity = ("source_event_id", "event_id", "trace_id")
        for field in identity:
            assert common[field] != repeated[field]
        assert common | {k: repeated[k] for k in identity} == repeated
        assert original["event_type"] != replacement["event_type"]
        assert original["source_event_id"] != replacement["source_event_id"]
        assert original["event_id"] != replacement["event_id"]
        expired(client.get(PATH, params={"cursor": first["next_cursor"]}))
        restart = page(client, limit=1)
        next_page = page(client, cursor=restart["next_cursor"])
        assert next_page["events"] == [replacement]
        assert not page(client, cursor=next_page["next_cursor"])["events"]


@pytest.mark.parametrize("keep_first", [False, True])
async def test_sse_na_verlies_van_oude_hoogste_rij(settings, old_schema, monkeypatch, keep_first):
    with monkeypatch.context() as old, closing(database.Database(settings.db_path)) as db:
        old.setattr(database, "MIGRATIONS_DIR", old_schema)
        db.execute(
            "INSERT INTO outbox_events(ts,topic,payload) VALUES (?,?,?)", (STAMP, "oud", "{}")
        )
        last = db.fetch_one("SELECT MAX(id) FROM outbox_events")[0]
        db.execute("DELETE FROM outbox_events WHERE id > ?", (int(keep_first),))
    with closing(database.Database(settings.db_path)) as db:
        db.initialize()
        bus = EventBus(db)
        bus.publish("recovery.committed", {})
        reset, *events = await sse_herlezing(SimpleNamespace(db=db, events=bus), str(last))
        controleer_sse_reset(reset)
        topics = [json.loads(frame.splitlines()[1].removeprefix("data: "))["topic"]
                  for frame in events]
        assert topics.count("recovery.committed") == 1


def test_nooit_uitgegeven_cursor_geeft_conflict(client, ctx):
    publish(ctx, age=20)
    publish(ctx, age=20)
    publish(ctx)
    old = page(client, limit=1)["next_cursor"]
    assert prune(ctx)["events"] == 2
    current = page(client)["next_cursor"]
    combined = ".".join(old.split(".")[:2] + current.split(".")[2:])
    expired(client.get(PATH, params={"cursor": combined}))


def test_oud_gat_en_latere_verwijdering_achter_de_cursor(client, ctx):
    first = publish(ctx)
    publish(ctx)
    publish(ctx, topic="recovery.committed")
    publish(ctx, age=20)
    assert prune(ctx)["events"] == 1
    start = page(client, limit=2)
    ctx.db.execute("UPDATE outbox_events SET ts=? WHERE id=?", (STAMP, row_position(first)))
    assert prune(ctx)["events"] == 1
    tail = page(client, cursor=start["next_cursor"])
    assert [event["event_type"] for event in tail["events"]] == ["media.recovery.succeeded"]


@pytest.mark.parametrize(
    "query",
    [
        "limiet=1",
        "limit=0&limit=1",
        "limit=201&limit=1",
        "limit=1&limit=1",
        "cursor=v1.0.0&cursor=v1.1.0",
        "limit=1.0",
    ],
)
def test_volledige_query_wordt_geweigerd(client, query):
    reply = client.get(PATH + "?" + query)
    assert error(reply, 422, "validation_error")["details"]["fouten"]


@pytest.mark.parametrize(
    ("method", "source"),
    [
        ("fetch_all", "FROM outbox_retention"),
        ("fetch_one", "FROM sessions"),
        ("fetch_one", "FROM api_keys"),
    ],
)
def test_tijdelijke_databaseuitval_weigert_lezen(
    client, anon_client, ctx, monkeypatch, method, source
):
    headers = key(client)
    publish(ctx)
    assert len(page(anon_client, headers)["events"]) == 1
    get = getattr(ctx.db, method)
    calls = []
    lock_db = ctx.db.path.parent / "bezet.db"

    def busy(query, *args):
        if source in query:
            calls.append(source)
            with closing(sqlite3.connect(lock_db, timeout=0)) as reader:
                reader.execute("SELECT name FROM sqlite_schema")
        return get(query, *args)

    with closing(sqlite3.connect(lock_db)) as writer:
        writer.execute("BEGIN EXCLUSIVE")
        monkeypatch.setattr(ctx.db, method, busy)
        reply = (
            client.get(PATH)
            if source == "FROM sessions"
            else anon_client.get(PATH, headers=headers)
        )
    assert calls == [source]
    assert error(reply, 503, "database_unavailable")["details"] == {}
    assert int(reply.headers["Retry-After"]) > 0
    assert "database is locked" not in reply.text


def test_dezelfde_publicatie_na_terugzetten_van_huidig_schema(settings, tmp_path):
    snapshot = tmp_path / "stand.db"
    sql = "INSERT INTO outbox_events(ts,topic,payload) VALUES (?,?,?)"
    values = (STAMP, "library.synced", "{}")
    with aangemeld(settings) as (ctx, client):
        ctx.db.execute(sql, values)
        before = page(client)
        ctx.db.backup_to(snapshot)
        ctx.db.execute(sql, values)
        original = page(client, cursor=before["next_cursor"])
    herstel(snapshot, settings.db_path)
    with aangemeld(settings, nieuw=False) as (ctx, client):
        ctx.db.execute(sql, values)
        assert page(client, limit=1) == before
        expired(client.get(PATH, params={"cursor": original["next_cursor"]}))
        new = page(client, cursor=before["next_cursor"])
        assert len(original["events"]) == len(new["events"]) == 1
        for field in ("source_event_id", "event_id"):
            assert original["events"][0][field] != new["events"][0][field]


async def test_sse_herhaalt_meer_dan_een_batch_en_hervat_precies(ctx):
    with ctx.db.transaction() as conn:
        conn.executemany(
            "INSERT INTO outbox_events(ts,topic,payload) VALUES (?,?,?)",
            [(STAMP, "library.synced", "{}")] * 503,
        )

    reset, *frames = await sse_herlezing(ctx, "999")
    controleer_sse_reset(reset)
    receipts = [frame.splitlines()[0].removeprefix("id: ") for frame in frames]
    assert len(set(receipts)) == len(receipts) == 503
    assert await sse_herlezing(ctx, receipts[499]) == frames[500:]


def test_beschadigde_set_of_oude_markering_geeft_geen_toestemming(
    old_schema,
    settings,
    tmp_path,
    monkeypatch,
):
    claimed = tmp_path / "vorige-set"
    claimed.mkdir()
    (claimed / "meta.json").write_text("{}")
    monkeypatch.setenv("MG_PRE_UPDATE_SET", str(claimed))
    copy_database = preupdate._copy_sqlite

    def corrupt(src, dest):
        info = copy_database(src, dest)
        dest.write_bytes(b"beschadigde proefkopie")
        return info

    monkeypatch.setattr(preupdate, "_copy_sqlite", corrupt)
    monkeypatch.setattr(database, "active_migration", lambda _: {"pid": 1})
    with closing(database.Database(settings.db_path)) as db:
        with pytest.raises(RuntimeError, match="back-up"):
            db.initialize()
        assert not db.fetch_one("SELECT 1 FROM schema_migrations WHERE version LIKE '151_%'")
        assert db.fetch_one("SELECT topic FROM outbox_events")[0] == "library.synced"
        assert not list(settings.data_dir.glob("backups/voor-update/*/meta.json"))


def test_onbekende_query_en_databasefout_blijven_gesloten(client, anon_client, ctx, monkeypatch):
    error(anon_client.get(PATH + "?limiet=1"), 401, "unauthorized")

    def fault(*_):
        raise sqlite3.OperationalError("interne synthetische oorzaak")

    monkeypatch.setattr(ctx.db, "fetch_all", fault)
    response = client.get(PATH)
    error(response, 500, "internal_error")
    assert "synthetische" not in response.text and "Retry-After" not in response.headers


def test_openapi_beschrijft_tijdelijke_uitval(app):
    unavailable = app.openapi()["paths"][PATH]["get"]["responses"]["503"]
    assert unavailable["headers"]["Retry-After"]["schema"]["minimum"] == 1
    assert unavailable["content"]["application/json"]["schema"]["$ref"].endswith("/ConnectorError")


@pytest.mark.parametrize("changed", [False, True])
def test_kern_hergebruikt_uitsluitend_een_actuele_supervisorset(settings, old_schema, changed,
                                                                monkeypatch):
    config = settings.data_dir.parent / "aio"
    config.mkdir()
    core = config / "core"
    settings.data_dir.rename(core)
    path = core / "mediaguard.db"
    env = {}
    aio.before_services(env, config, None)
    archive = Path(env["MG_PRE_UPDATE_SET"])
    assert preupdate.verify(archive) == []
    if changed:
        with monkeypatch.context() as old, closing(database.Database(path)) as db:
            old.setattr(database, "MIGRATIONS_DIR", old_schema)
            db.execute("INSERT INTO outbox_events(ts,topic,payload) VALUES (?,?,?)",
                       (STAMP, "recovery.committed", "{}"))
    monkeypatch.setenv("MG_PRE_UPDATE_SET", str(archive))
    with closing(database.Database(path)) as db:
        db.initialize()
    sets = sorted(p.parent for p in (core / "backups").glob("voor-update/*/meta.json"))
    assert len(sets) == 1 + int(changed)
    with closing(sqlite3.connect(sets[-1] / "mediaguard.db")) as saved:
        assert saved.execute("SELECT COUNT(*) FROM outbox_events").fetchone()[0] == 1 + int(changed)


def test_bezette_database_tijdens_schemacontrole_geeft_503(app, monkeypatch, tmp_path):
    path = tmp_path / "schema-bezet.db"

    def check():
        with closing(sqlite3.connect(path, timeout=0)) as reader:
            reader.execute("SELECT name FROM sqlite_schema")

    with (TestClient(app, raise_server_exceptions=False) as client,
          closing(sqlite3.connect(path)) as writer, monkeypatch.context() as patch):
        writer.execute("BEGIN EXCLUSIVE")
        patch.setattr(app.state.ctx.db, "check_schema", check)
        reply = client.get(PATH)
    assert error(reply, 503, "database_unavailable")["details"] == {}
    assert int(reply.headers["Retry-After"]) > 0
    assert "database is locked" not in reply.text


async def test_sse_hervat_na_tweede_upgrade_met_gelijke_oude_rij(settings, old_schema, monkeypatch):
    path = settings.db_path
    snapshot = path.parent / "voor-twee-publicaties.db"
    with closing(database.Database(path)) as db:
        db.backup_to(snapshot)

    def schrijf_en_upgrade(db):
        with monkeypatch.context() as old:
            old.setattr(database, "MIGRATIONS_DIR", old_schema)
            assert db.fetch_one("SELECT COUNT(*) FROM outbox_events")[0] == 1
            db.execute("INSERT INTO outbox_events(ts,topic,payload) VALUES (?,?,?)",
                       (STAMP, "recovery.committed", "{}"))
        db.initialize()

    with closing(database.Database(path)) as db:
        schrijf_en_upgrade(db)
        ctx = SimpleNamespace(db=db, events=EventBus(db))
        eerste = [frame for frame in await sse_herlezing(ctx, "0") if frame.startswith("id: ")]
        assert len(eerste) == 2 and "recovery.committed" in eerste[-1]
        last_id = eerste[-1].splitlines()[0].removeprefix("id: ")
        assert not await sse_herlezing(ctx, last_id)
    herstel(snapshot, path)
    with closing(database.Database(path)) as db:
        schrijf_en_upgrade(db)
        ctx = SimpleNamespace(db=db, events=EventBus(db))
        reset, *herhaald = await sse_herlezing(ctx, last_id)
        controleer_sse_reset(reset)
        assert len(herhaald) == 2 and herhaald[0] != eerste[0]
        assert sum("recovery.committed" in frame for frame in herhaald) == 1
        db.execute("DELETE FROM outbox_events")
        leeg, = await sse_herlezing(ctx, last_id)
        controleer_sse_reset(leeg)


def test_rotatie_bewaart_de_set_van_elke_nieuwe_poging(settings, old_schema, monkeypatch):
    from datetime import UTC, datetime

    class VasteKlok(datetime):
        @classmethod
        def now(cls, tz=None):
            return datetime(2026, 1, 1, tzinfo=UTC).astimezone(tz)

    monkeypatch.setattr(preupdate, "datetime", VasteKlok)
    pad = settings.db_path
    namen = set()

    def onderbreek(*_):
        raise InterruptedError("proefonderbreking na het archiveren")

    for _ in range(preupdate.KEEP_SETS + 2):
        with closing(database.Database(pad)) as db, monkeypatch.context() as poging:
            poging.setattr(db, "_apply_migration", onderbreek)
            with pytest.raises(InterruptedError):
                db.initialize()
        sets = {p.parent.name for p in settings.backup_dir.glob("voor-update/*/meta.json")}
        assert len(sets - namen) == 1
        namen.update(sets)

    with closing(sqlite3.connect(pad)) as db, db:
        db.execute("UPDATE outbox_events SET topic='recovery.committed'")
    with closing(database.Database(pad)) as db:
        db.initialize()
        assert db.fetch_one("SELECT 1 FROM schema_migrations WHERE version LIKE '151_%'")
    nieuwe, = [p.parent for p in settings.backup_dir.glob("voor-update/*/meta.json")
               if p.parent.name not in namen]
    assert not preupdate.verify(nieuwe)
    preupdate.stage(pad.parent, nieuwe, actor="proef")
    assert preupdate.apply_staged(pad.parent)["ok"]
    with closing(sqlite3.connect(pad)) as db:
        assert db.execute("SELECT topic FROM outbox_events").fetchone() == ("recovery.committed",)
        assert not db.execute(
            "SELECT 1 FROM schema_migrations WHERE version LIKE '151_%'"
        ).fetchone()


def test_verdwenen_gepubliceerde_set_blokkeert_151(settings, old_schema, monkeypatch):
    origineel = preupdate.prune_sets

    def verlies(folder, *args, **kwargs):
        origineel(folder, *args, **kwargs)
        bestand, = folder.glob("*/mediaguard.db")
        bestand.unlink()

    monkeypatch.setattr(preupdate, "prune_sets", verlies)
    with closing(database.Database(settings.db_path)) as db:
        with pytest.raises(RuntimeError, match="back-up"):
            db.initialize()
        assert not db.fetch_one("SELECT 1 FROM schema_migrations WHERE version LIKE '151_%'")
        assert db.fetch_one("SELECT topic FROM outbox_events")[0] == "library.synced"


@pytest.mark.parametrize("wijziging", ["wal", "extra", "verwijderd"])
def test_herstelset_volgt_ook_de_encoder(settings, old_schema, monkeypatch, wijziging):
    config = settings.data_dir.parent / "aio"
    core, encoder = config / "core", config / "encode"
    encoder.mkdir(parents=True)
    settings.data_dir.rename(core)
    pad = encoder / "app.db"
    with closing(sqlite3.connect(pad)) as schrijver:
        schrijver.execute("PRAGMA journal_mode=WAL")
        maak_av1_v1(schrijver, 25)
        env = {}
        aio.before_services(env, config, None)
        eerste = Path(env["MG_PRE_UPDATE_SET"])
        if wijziging == "wal":
            schrijver.execute("UPDATE job SET percent=75")
            schrijver.commit()
        elif wijziging == "extra":
            with closing(sqlite3.connect(encoder / "tweede.db")) as extra:
                schrijver.backup(extra)
        else:
            schrijver.close()
            pad.unlink()
        monkeypatch.setenv("MG_PRE_UPDATE_SET", str(eerste))
        with closing(database.Database(core / "mediaguard.db")) as db:
            db.initialize()
            assert db.fetch_one("SELECT 1 FROM schema_migrations WHERE version LIKE '151_%'")
    actueel, = [p.parent for p in eerste.parent.glob("*/meta.json") if p.parent != eerste]
    bewaard = {p.name for p in (actueel / "encode").glob("*.db")}
    assert bewaard == {p.name for p in encoder.glob("*.db")}
    assert not preupdate.verify(actueel)
    preupdate.stage(core, actueel, actor="proef")
    assert preupdate.apply_staged(core)["ok"]
    if wijziging != "verwijderd":
        with closing(sqlite3.connect(pad)) as db:
            stand = db.execute("SELECT percent FROM job").fetchone()[0]
            assert stand == (75 if wijziging == "wal" else 25)


def test_aio_migreert_voor_de_start_van_de_schrijvers(settings, old_schema):
    config = settings.data_dir.parent / "aio"
    config.mkdir()
    core = config / "core"
    settings.data_dir.rename(core)
    env = {}
    aio.before_services(env, config, None, migrate_core=True)
    archief = Path(env["MG_PRE_UPDATE_SET"])
    assert preupdate.verify(archief) == []
    for map_, verwacht in ((archief, 0), (core, 1)):
        with closing(sqlite3.connect(map_ / "mediaguard.db")) as db:
            assert db.execute("SELECT count(*) FROM schema_migrations WHERE version LIKE '151_%'"
                              ).fetchone()[0] == verwacht
            assert db.execute("SELECT topic FROM outbox_events").fetchone() == ("library.synced",)


@pytest.mark.parametrize("schrijver", ["kern", "encoder"])
def test_ander_sqlite_schrijfslot_blijft_bezet_tot_151(
    settings, old_schema, monkeypatch, schrijver,
):
    kern = settings.db_path
    encoder = settings.data_dir / "encode" / "app.db"
    encoder.parent.mkdir()
    with closing(sqlite3.connect(encoder)) as db, db:
        db.execute("CREATE TABLE job (percent INTEGER)")
        db.execute("INSERT INTO job VALUES (75)")
    meting = []
    doel = kern if schrijver == "kern" else encoder
    with closing(database.Database(kern)) as db:
        migreer = db._apply_migration

        def controle(conn, script):
            if script.name.startswith("151_"):
                with closing(sqlite3.connect(doel, timeout=0)) as ander:
                    with pytest.raises(sqlite3.OperationalError, match="locked"):
                        ander.execute("BEGIN IMMEDIATE")
                    meting.append(True)
            migreer(conn, script)

        monkeypatch.setattr(db, "_apply_migration", controle)
        db.initialize()
    assert meting == [True]
    with closing(sqlite3.connect(doel, timeout=0)) as vrij:
        vrij.execute("BEGIN IMMEDIATE")
    archief, = settings.backup_dir.glob("voor-update/*/encode/app.db")
    with closing(sqlite3.connect(archief)) as kopie:
        assert kopie.execute("SELECT percent FROM job").fetchone()[0] == 75


def test_gemelde_set_herstelt_laatste_kern_en_encodertransactie(settings, old_schema, monkeypatch):
    config = settings.data_dir.parent / "aio"
    core, encoder = config / "core", config / "encode"
    encoder.mkdir(parents=True)
    settings.data_dir.rename(core)
    kernel = core / "mediaguard.db"
    engine = encoder / "app.db"
    with closing(sqlite3.connect(kernel)) as db, db:
        db.execute("INSERT INTO jobs(job_type,status,progress,created_at) "
                   "VALUES ('herstelproef','running',0.25,?)", (STAMP,))
    with closing(sqlite3.connect(engine)) as db, db:
        maak_av1_v1(db, 25)

    make = preupdate._make_set
    sets = []

    def met_laatste_schrijver(*args, **kwargs):
        archive = make(*args, **kwargs)
        sets.append(archive)
        if len(sets) == 1:
            # De vorige release gebruikte alleen SQLite-sloten.
            with closing(sqlite3.connect(engine, timeout=0)) as db, db:
                db.execute("UPDATE job SET percent=75")
            with closing(sqlite3.connect(kernel, timeout=0)) as db, db:
                db.execute("UPDATE jobs SET progress=0.75 WHERE job_type='herstelproef'")
                db.execute("INSERT INTO audit_events(ts,actor,action) VALUES (?,?,?)",
                           (STAMP, "herstelproef", "job.retry"))
                db.execute("INSERT INTO outbox_events(ts,topic,payload) VALUES (?,?,?)",
                           (STAMP, "recovery.committed", "{}"))
        return archive

    monkeypatch.setattr(preupdate, "_make_set", met_laatste_schrijver)
    env = {}
    lines = aio.before_services(env, config, None, migrate_core=True)
    picked = Path(env["MG_PRE_UPDATE_SET"])
    assert any(picked.name in line for line in lines)
    assert len(sets) == 2 and not preupdate.verify(picked)
    preupdate.stage(core, picked, actor="herstelproef")
    assert preupdate.apply_staged(core)["ok"]
    with closing(sqlite3.connect(kernel)) as db, closing(sqlite3.connect(engine)) as enc:
        actual = (
            enc.execute("SELECT percent FROM job").fetchone()[0],
            db.execute("SELECT progress FROM jobs WHERE job_type='herstelproef'").fetchone()[0],
            db.execute("SELECT count(*) FROM audit_events WHERE actor=?",
                       ("herstelproef",)).fetchone()[0],
            db.execute("SELECT count(*) FROM outbox_events WHERE topic=?",
                       ("recovery.committed",)).fetchone()[0],
        )
    assert actual == (75, 0.75, 1, 1)


def test_terugzetmarkering_past_bij_de_vorige_supervisor(settings, old_schema):
    import re

    root = settings.data_dir.parent
    core = root / "core"
    settings.data_dir.rename(core)
    env = {}
    aio.before_services(env, root, None, migrate_core=True)
    archive = Path(env["MG_PRE_UPDATE_SET"])
    info = preupdate.stage(core, archive, actor="herstelproef")
    # De lezer van schema 155 accepteert een UTC-tijd met één numeriek achtervoegsel.
    assert re.fullmatch(r"voor-update-\d{8}T\d{6}Z(?:-\d+)?", info["set"])
    assert info["set"] == archive.name
    assert preupdate.apply_staged(core)["ok"]
    with closing(sqlite3.connect(core / "mediaguard.db")) as db:
        assert db.execute("SELECT max(version) FROM schema_migrations").fetchone()[0] < "151_"
        assert db.execute("SELECT topic FROM outbox_events").fetchone() == ("library.synced",)


@pytest.mark.parametrize("onderbroken", [False, True])
def test_oude_sleutel_alleen_bij_expliciet_terugzetten(tmp_path, monkeypatch, onderbroken):
    root = tmp_path / "installatie"
    pad = root / "core/mediaguard.db"
    oud = tmp_path / "schema-155"
    oud.mkdir()
    for sql in database.MIGRATIONS_DIR.glob("*.sql"):
        if sql.name < "156_":
            (oud / sql.name).symlink_to(sql)
    oud_materiaal = os.urandom(32)
    with monkeypatch.context() as versie, closing(database.Database(pad)) as db:
        versie.setattr(database, "MIGRATIONS_DIR", oud)
        db.initialize()
        db.execute("UPDATE outbox_retention SET cursor_key=?", (oud_materiaal,))
        db.execute("INSERT INTO jobs(job_type,status,progress,created_at) "
                   "VALUES ('herstelproef','running',0.75,?)", (STAMP,))
        db.execute("INSERT INTO audit_events(ts,actor,action) VALUES (?,?,?)",
                   (STAMP, "herstelproef", "job.retry"))
    with monkeypatch.context() as storing:
        if onderbroken:
            def onderbreek(*_):
                raise InterruptedError("onderbroken migratie")
            storing.setattr(database.Database, "_apply_migration", onderbreek)
            with pytest.raises(InterruptedError):
                preupdate.snapshot_before_start(root, core_migrations=database.MIGRATIONS_DIR,
                                               encoder_migrations=None, app_version="proef",
                                               migrate_core=True)
            with closing(sqlite3.connect(pad)) as db:
                actief = db.execute("SELECT cursor_key FROM outbox_retention").fetchone()[0]
                if actief != oud_materiaal:
                    pytest.fail("De actieve oude sleutel is gewijzigd", pytrace=False)
                versie = db.execute("SELECT max(version) FROM schema_migrations").fetchone()[0]
                assert versie < "156_"
        else:
            aio.before_services({}, root, None, migrate_core=True)
    folder = pad.parent / "backups" / preupdate.FOLDER
    archives = [folder / row["name"] for row in preupdate.list_sets(folder)]
    assert archives and all(not preupdate.verify(a) for a in archives)
    copy = preupdate.shutil.copyfile

    def afwijkende_kopie(src, dest):
        # Een geldig SQLite-bestand kan toch andere taakgegevens bevatten dan de gekozen set.
        copy(src, dest)
        with closing(sqlite3.connect(dest)) as db, db:
            db.execute("UPDATE jobs SET progress=0.25 WHERE job_type='herstelproef'")

    for archive in archives:
        if any(oud_materiaal in p.read_bytes() for p in archive.rglob("*") if p.is_file()):
            pytest.fail("De historische cursorsleutel staat in de herstelset", pytrace=False)
        kopie, = (root / "secrets/core").glob("outbox-historisch-*")
        kopie.chmod(0o644)
        assert preupdate.verify(archive)
        with pytest.raises(preupdate.SetError, match="cursorsleutel"):
            preupdate.stage(pad.parent, archive, actor="herstelproef")
        assert preupdate.pending(pad.parent) is None
        kopie.chmod(0o600)
        with monkeypatch.context() as storing:
            storing.setattr(preupdate.shutil, "copyfile", afwijkende_kopie)
            with pytest.raises(preupdate.SetError, match="checksum"):
                preupdate.stage(pad.parent, archive, actor="herstelproef")
        assert preupdate.pending(pad.parent) is None
        assert preupdate.main(["zet-klaar", "--kernmap", str(pad.parent),
                               "--set", archive.name]) == 0
        assert preupdate.apply_staged(pad.parent)["ok"]
        with closing(sqlite3.connect(pad)) as db:
            if db.execute("SELECT cursor_key FROM outbox_retention").fetchone()[0] != oud_materiaal:
                pytest.fail("De historische cursorsleutel ontbreekt na terugzetten", pytrace=False)
            assert db.execute("SELECT progress FROM jobs").fetchall() == [(0.75,)]
            assert db.execute("SELECT action FROM audit_events").fetchall() == [("job.retry",)]
