"""Uitgegeven leesposities bij retentie, terugzetten en tijdelijke uitval."""

from __future__ import annotations

import asyncio
import os
import sqlite3
from contextlib import aclosing, closing
from unittest.mock import AsyncMock

import pytest
from fastapi import Request
from test_connector_events import PATH, expired, page, publish
from test_event_herstel import STAMP, aangemeld, controleer_sse_reset, herstel, sse_herlezing

from mediaguard import db as database
from mediaguard.api.events import stream
from mediaguard.maintenance.housekeeping import prune


def test_lege_paginacursor_weigert_afgetakte_retentierevisie(settings, tmp_path):
    snapshot = tmp_path / "moment.db"
    with aangemeld(settings) as (ctx, client):
        publish(ctx)
        ctx.db.backup_to(snapshot)
        for _ in range(3):
            publish(ctx, age=20)
        ctx.db.execute("UPDATE outbox_events SET ts=?", (STAMP,))
        assert prune(ctx)["events"] == 4
        leeg = page(client)
        assert leeg["events"] == [] and page(client, cursor=leeg["next_cursor"]) == leeg
    herstel(snapshot, settings.db_path)
    with aangemeld(settings, nieuw=False) as (ctx, client):
        publish(ctx, topic="recovery.committed")
        for _ in range(4):
            publish(ctx, age=20)
        assert prune(ctx)["events"] == 4
        ctx.db.execute("UPDATE outbox_events SET ts=? WHERE id=1", (STAMP,))
        assert prune(ctx)["events"] == 1
        expired(client.get(PATH, params={"cursor": leeg["next_cursor"]}))
        assert [e["event_type"] for e in page(client)["events"]] == ["media.recovery.succeeded"]


@pytest.mark.parametrize("fout", ["verwijzing", "ontbreekt", "leeg", "kort", "lang"])
async def test_onbruikbare_sleutel_weigert_uitgifte_en_hervatten(ctx, client, anon_client, fout):
    publish(ctx)
    cursor = page(client)["next_cursor"]
    _, eerste = await sse_herlezing(ctx, "0")
    last_id = eerste.splitlines()[0].removeprefix("id: ")
    sleutel = ctx.db.path.parent / "secrets/core/outbox.key"
    if fout == "verwijzing":
        ctx.db.execute("UPDATE outbox_retention SET cursor_key=NULL")
    elif fout == "ontbreekt":
        sleutel.unlink(missing_ok=True)
    else:
        sleutel.parent.mkdir(parents=True, exist_ok=True)
        sleutel.write_bytes(os.urandom({"leeg": 0, "kort": 1, "lang": 33}[fout]))
    assert anon_client.get(PATH).status_code == 401
    expired(client.get(PATH))
    expired(client.get(PATH, params={"cursor": cursor}))
    for headers in ([], [(b"last-event-id", last_id.encode())]):
        request = Request({"type": "http", "headers": headers, "query_string": b""})
        response = await stream(request, None, ctx)
        assert response.status_code == 409
        assert b'"cursor_expired"' in response.body


def test_sleutelmateriaal_ontbreekt_in_databasekopie(settings, tmp_path, monkeypatch):
    versies = tmp_path / "voor-bestandssleutel"
    versies.mkdir()
    for sql in database.MIGRATIONS_DIR.glob("*.sql"):
        if sql.name < "156_":
            (versies / sql.name).symlink_to(sql)
    pad = settings.db_path
    with monkeypatch.context() as oud, closing(database.Database(pad)) as db:
        oud.setattr(database, "MIGRATIONS_DIR", versies)
        db.initialize()
        # Een eerder geïnstalleerde 153 bewaarde een echte ondertekeningssleutel als BLOB.
        oud_materiaal = os.urandom(32)
        db.execute("UPDATE outbox_retention SET cursor_key=?", (oud_materiaal,))
    with closing(database.Database(pad)) as db:
        db.initialize()
        kopie = tmp_path / "kopie.db"
        db.backup_to(kopie)
    for bestand in (pad, kopie):
        with closing(sqlite3.connect(bestand)) as lezer:
            soort, = lezer.execute("SELECT typeof(cursor_key) FROM outbox_retention").fetchone()
            assert soort == "text"
        if oud_materiaal in bestand.read_bytes():
            pytest.fail("De oude ondertekeningssleutel staat nog in de database", pytrace=False)
    sleutel = pad.parent / "secrets/core/outbox.key"
    assert sleutel.stat().st_mode & 0o777 == 0o600
    assert sleutel.parent.stat().st_mode & 0o777 == 0o700
    assert sleutel.stat().st_size == 32
    if sleutel.read_bytes() in kopie.read_bytes():
        pytest.fail("De nieuwe ondertekeningssleutel staat in de databasekopie", pytrace=False)


async def test_sse_reset_voor_herlezing_bij_een_nieuw_gat(ctx):
    publish(ctx)
    _, eerste = await sse_herlezing(ctx, "0")
    positie = eerste.splitlines()[0].removeprefix("id: ")
    publish(ctx, age=20)
    publish(ctx, topic="recovery.committed")
    assert prune(ctx)["events"] == 1
    frames = await sse_herlezing(ctx, positie)
    controleer_sse_reset(frames[0])
    assert len(frames) == 3 and "library.synced" in frames[1] and "recovery.committed" in frames[2]


async def test_sse_behoudt_positie_bij_een_tijdelijk_bezette_database(ctx, monkeypatch):
    async def timeout(awaitable, *, timeout):
        awaitable.close()
        raise TimeoutError

    request = Request({"type": "http", "headers": [], "query_string": b""},
                      receive=AsyncMock(return_value={"type": "http.request", "body": b""}))
    response = await stream(request, None, ctx)
    async with aclosing(response.body_iterator) as frames:
        assert (await anext(frames)).startswith("retry:")
        with monkeypatch.context() as storing:
            storing.setattr(asyncio, "wait_for", timeout)

            def bezet(*_):
                raise sqlite3.OperationalError("database is locked")

            storing.setattr(ctx.db, "fetch_one", bezet)
            storing.setattr(ctx.db, "fetch_all", bezet)
            assert await anext(frames) == ": keepalive\n\n"
        publish(ctx)
        assert "library.synced" in await anext(frames)


async def test_sse_tijdelijke_storing_na_de_eerste_500_bevestigde_rijen(ctx, monkeypatch):
    with ctx.db.transaction() as conn:
        conn.executemany("INSERT INTO outbox_events(ts,topic,payload) VALUES (?,?,?)",
                         [(STAMP, "library.synced", "{}")] * 501)
    request = Request({"type": "http", "headers": [(b"last-event-id", b"0")]})
    response = await stream(request, None, ctx)
    async with aclosing(response.body_iterator) as frames:
        controleer_sse_reset(await anext(frames))
        ids = [(await anext(frames)).splitlines()[0] for _ in range(500)]
        with monkeypatch.context() as storing:
            def bezet(*_):
                raise sqlite3.OperationalError("database is locked")

            storing.setattr(ctx.db, "fetch_all", bezet)
            assert await anext(frames) == ": keepalive\n\n"
        laatste = (await anext(frames)).splitlines()[0]
        assert len({*ids, laatste}) == 501
        assert (await anext(frames)).startswith("retry:")
    assert len(await sse_herlezing(ctx, ids[-1].removeprefix("id: "))) == 1


def test_gelijke_revisienummers_na_terugzetten_blijven_onderscheidbaar(settings, tmp_path):
    snapshot = tmp_path / "voor-retentie.db"
    with aangemeld(settings) as (ctx, client):
        publish(ctx, age=20)
        publish(ctx)
        ctx.db.backup_to(snapshot)
        assert prune(ctx)["events"] == 1
        origineel = page(client)
    herstel(snapshot, settings.db_path)
    with aangemeld(settings, nieuw=False) as (ctx, client):
        assert prune(ctx)["events"] == 1
        actueel = page(client)
        assert origineel["events"] == actueel["events"]
        assert origineel["next_cursor"].split(".")[:-1] == actueel["next_cursor"].split(".")[:-1]
        expired(client.get(PATH, params={"cursor": origineel["next_cursor"]}))
        assert page(client, cursor=actueel["next_cursor"])["events"] == []


async def test_open_stream_herleest_alle_bewaarde_rijen_na_reset(ctx):
    request = Request({"type": "http", "headers": [], "query_string": b""},
                      receive=AsyncMock(return_value={"type": "http.request", "body": b""}))
    response = await stream(request, None, ctx)
    async with aclosing(response.body_iterator) as frames:
        assert (await anext(frames)).startswith("retry:")
        for nr in range(1, 5):
            publish(ctx, age=20 if nr % 2 else 0, payload={"nr": nr})
        assert prune(ctx)["events"] == 2
        controleer_sse_reset(await anext(frames))
        tweede = await anext(frames)
        assert '"nr": 2' in tweede
        vierde = await anext(frames)
        assert '"nr": 4' in vierde
        positie = vierde.splitlines()[0].removeprefix("id: ")
    assert await sse_herlezing(ctx, positie) == []


async def test_reset_bij_openen_verliest_geen_rij_uit_nieuwere_momentopname(ctx, monkeypatch):
    # Een andere schrijver publiceert en schoont op zodra de lezer de stand opvraagt.
    # Zowel een losse grenslezing als een gecombineerde paginalezing is een mogelijk tijdstip.
    oud = ctx.db.fetch_one
    alle = ctx.db.fetch_all
    gewijzigd = False

    def wijzig():
        nonlocal gewijzigd
        if not gewijzigd:
            gewijzigd = True
            publish(ctx)
            publish(ctx, age=20)
            assert prune(ctx)["events"] == 1

    def grens(sql, *args):
        result = oud(sql, *args)
        if "MAX(id)" in sql:
            wijzig()
        return result

    def pagina(sql, *args):
        if "FROM outbox_retention" in sql:
            wijzig()
        return alle(sql, *args)

    request = Request({"type": "http", "headers": [(b"last-event-id", b"0")]},
                      receive=AsyncMock(return_value={"type": "http.request", "body": b""}))
    with monkeypatch.context() as patch:
        patch.setattr(ctx.db, "fetch_one", grens)
        patch.setattr(ctx.db, "fetch_all", pagina)
        response = await stream(request, None, ctx)
    async with aclosing(response.body_iterator) as frames:
        controleer_sse_reset(await anext(frames))
        assert gewijzigd and "library.synced" in await anext(frames)


@pytest.mark.parametrize("bron", ["sessions", "api_keys"])
def test_sse_aanmeldcontrole_bij_databaseslot(app, client, ctx, monkeypatch, bron):
    from fastapi.testclient import TestClient
    from test_connector_lezen import error, key

    headers = ({"Cookie": client.build_request("GET", PATH).headers["cookie"]}
               if bron == "sessions" else key(client))
    publish(ctx)
    normaal = ctx.db.fetch_one
    geraakt = []

    def bezet(sql, *args):
        if f"FROM {bron}" in sql:
            geraakt.append(bron)
            raise sqlite3.OperationalError("database is locked")
        return normaal(sql, *args)

    monkeypatch.setattr(ctx.db, "fetch_one", bezet)
    with TestClient(app, raise_server_exceptions=False) as bezoek:
        response = bezoek.get("/api/v1/events/stream", headers=headers)
    assert geraakt == [bron]
    assert error(response, 503, "database_unavailable")["details"] == {}
    assert int(response.headers["Retry-After"]) > 0
    assert "text/event-stream" not in response.headers["Content-Type"]


def test_te_ruime_sleutelrechten_weigeren_en_herstart_behoudt_cursor(ctx, client, monkeypatch):
    from mediaguard import bootstrap, event_keys

    publish(ctx)
    positie = page(client)["next_cursor"]
    bestand = event_keys.key_path(ctx.db.path.parent)
    bestand.chmod(0o644)
    expired(client.get(PATH, params={"cursor": positie}))
    bestand.chmod(0o600)
    bestand.parent.chmod(0o755)
    expired(client.get(PATH))
    bestand.chmod(0o644)
    monkeypatch.setenv("MG_UID", str(os.geteuid()))
    monkeypatch.setenv("MG_GID", str(os.getegid()))
    bootstrap.run(bestand.parent.parent, [], [])
    assert bestand.stat().st_mode & 0o777 == 0o600
    assert bestand.parent.stat().st_mode & 0o777 == 0o700
    assert page(client, cursor=positie)["events"] == []
