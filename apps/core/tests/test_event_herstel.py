"""Negatieve proeven voor migratie 151, hervatten en het contract van de eventroute."""

from __future__ import annotations

import errno
import json
import os
import sqlite3
from contextlib import aclosing, closing, contextmanager
from pathlib import Path
from types import SimpleNamespace

import pytest
from fastapi import Request
from fastapi.testclient import TestClient
from test_connector_events import PATH, expired, page, publish, row_position
from test_connector_lezen import error, key

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
        assert common == repeated
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
        request = Request({"type": "http", "headers": [(b"last-event-id", str(last).encode())]})
        response = await stream(request, None, SimpleNamespace(db=db, events=bus))
        topics = []
        async with aclosing(response.body_iterator) as chunks:
            async for chunk in chunks:
                if chunk.startswith("retry:"):
                    break
                topics.extend(
                    json.loads(s[6:])["topic"] for s in chunk.splitlines() if s.startswith("data: ")
                )
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

    async def replay(last):
        request = Request({"type": "http", "headers": [(b"last-event-id", last.encode())]})
        response = await stream(request, None, ctx)
        receipts = []
        async with aclosing(response.body_iterator) as chunks:
            async for chunk in chunks:
                if chunk.startswith("retry:"):
                    break
                receipts.append(chunk.splitlines()[0].removeprefix("id: "))
        return receipts

    receipts = await replay("999")
    assert len(set(receipts)) == len(receipts) == 503
    assert await replay(receipts[499]) == receipts[500:]


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

    async def frames(db, last_id):
        request = Request({"type": "http", "headers": [(b"last-event-id", last_id.encode())]})
        response = await stream(request, None, SimpleNamespace(db=db, events=EventBus(db)))
        result = []
        async with aclosing(response.body_iterator) as chunks:
            async for chunk in chunks:
                if chunk.startswith("retry:"):
                    break
                result.append(chunk)
        return result

    def schrijf_en_upgrade(db):
        with monkeypatch.context() as old:
            old.setattr(database, "MIGRATIONS_DIR", old_schema)
            assert db.fetch_one("SELECT COUNT(*) FROM outbox_events")[0] == 1
            db.execute("INSERT INTO outbox_events(ts,topic,payload) VALUES (?,?,?)",
                       (STAMP, "recovery.committed", "{}"))
        db.initialize()

    with closing(database.Database(path)) as db:
        schrijf_en_upgrade(db)
        eerste = await frames(db, "0")
        assert len(eerste) == 2 and "recovery.committed" in eerste[-1]
        last_id = eerste[-1].splitlines()[0].removeprefix("id: ")
        assert not await frames(db, last_id)
    with closing(sqlite3.connect(snapshot)) as saved, closing(sqlite3.connect(path)) as live:
        saved.backup(live)
    with closing(database.Database(path)) as db:
        schrijf_en_upgrade(db)
        herhaald = await frames(db, last_id)
        assert sum("recovery.committed" in frame for frame in herhaald) == 1
