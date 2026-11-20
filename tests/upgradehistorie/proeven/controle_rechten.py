"""Gerichte controles op het aanwijzen van de herstelset, SSE en sleutelopslag."""

import asyncio
import os
import sqlite3
from contextlib import aclosing, closing
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock

import httpx
import pytest
from fastapi import Request
from mediaguard import aio, bootstrap, event_keys
from mediaguard.api.events import stream
from mediaguard.maintenance import preupdate
from sse_proef import sse
from test_connector_events import PATH, expired, page, publish
from test_connector_lezen import key
from test_event_herstel import controleer_sse_reset


def encoderstand(pad):
    with closing(sqlite3.connect(pad.as_uri() + "?mode=ro", uri=True)) as db:
        (voortgang,) = db.execute("SELECT percent FROM job").fetchone()
        (bewijs,) = db.execute(
            "SELECT COUNT(*) FROM auditlog WHERE onderwerp='hersteltaak'"
        ).fetchone()
        return int(voortgang), bewijs


@pytest.mark.parametrize("moment", ["voor_snapshot", "na_eerste_set"])
def test_archiefmelding_volgt_de_laatste_encodertransactie(
    settings, old_schema, tmp_path, monkeypatch, moment
):
    config = tmp_path / "installatie"
    config.mkdir()
    core, encoder = config / "core", config / "encode"
    settings.data_dir.rename(core)
    encoder.mkdir()
    monkeypatch.syspath_prepend(str(Path(os.environ["MG_REPO"]) / "services/encoder"))
    from app.db import Database as Encoder

    enc = Encoder(
        SimpleNamespace(
            config_root=encoder,
            database_path=encoder / "app.db",
            backup_root=tmp_path / "encoderbackups",
        )
    )
    enc.initialize()
    with enc.connection() as db:
        profiel = db.execute(
            "INSERT INTO profiel(naam,inst) VALUES ('proef','{}')"
        ).lastrowid
        lib = db.execute(
            "INSERT INTO library(naam,pad,profiel_id,actief) VALUES (?,?,?,0)",
            ("proef", "/fixture", profiel),
        ).lastrowid
        bestand = db.execute(
            "INSERT INTO bestand(library_id,pad,grootte,mtime,categorie,gescand_op) "
            "VALUES (?, 'proef', 1, 0, 'video', CURRENT_TIMESTAMP)",
            (lib,),
        ).lastrowid
        besluit = db.execute(
            "INSERT INTO besluit(bestand_id,actie,reden,payload,gemaakt_op) "
            "VALUES (?, 'hercoderen', 'proef', '{}', CURRENT_TIMESTAMP)",
            (bestand,),
        ).lastrowid
        taak = db.execute(
            "INSERT INTO job(besluit_id,bestand_id,baan,status,percent) "
            "VALUES (?,?,'cpu','bezig',25)",
            (besluit, bestand),
        ).lastrowid
        db.commit()
    assert encoderstand(enc.path) == (25, 0)
    maak_set = preupdate._make_set
    publicaties = []

    def schrijf_voortgang_en_audit():
        with closing(sqlite3.connect(enc.path, timeout=0)) as ander, ander:
            ander.execute("UPDATE job SET percent=75 WHERE id=?", (taak,))
            ander.execute(
                "INSERT INTO auditlog(soort,onderwerp,oud,nieuw,tijd) "
                "VALUES ('voortgang','hersteltaak','25','75',CURRENT_TIMESTAMP)"
            )

    def maak_met_geplande_schrijver(*args, **kwargs):
        archief = maak_set(*args, **kwargs)
        publicaties.append(archief)
        if len(publicaties) == 1 and moment == "na_eerste_set":
            # Een oude encoderschrijver gebruikt alleen het SQLite-slot. Laat zijn
            # gewone commit direct na de eerste archiefpublicatie aan bod komen.
            schrijf_voortgang_en_audit()
        return archief

    env = {}
    with monkeypatch.context() as tijdstip:
        if moment == "voor_snapshot":
            schrijf_voortgang_en_audit()
        tijdstip.setattr(preupdate, "_make_set", maak_met_geplande_schrijver)
        meldingen = aio.before_services(env, config, None, migrate_core=True)
    gemeld = Path(env["MG_PRE_UPDATE_SET"])
    assert any(gemeld.name in regel for regel in meldingen)
    with closing(sqlite3.connect(core / "mediaguard.db")) as db:
        assert db.execute(
            "SELECT 1 FROM schema_migrations WHERE version LIKE '156_%'"
        ).fetchone()
    sets = [p.parent for p in gemeld.parent.glob("*/meta.json")]
    assert all(not preupdate.verify(p) for p in sets)
    actueel = encoderstand(enc.path)
    assert actueel == (75, 1)
    juiste_sets = [p for p in sets if encoderstand(p / "encode/app.db") == actueel]
    preupdate.stage(core, gemeld, actor="lokale review")
    terug = preupdate.apply_staged(core)
    assert terug["ok"]
    stand = encoderstand(enc.path)
    assert encoderstand(Path(terug["previous"]) / "encode__app.db") == actueel
    meting = (
        f"Voor migratie {actueel[0]}% en {actueel[1]} auditrij. "
        f"Na terugzetten van de gemelde set {stand[0]}% en {stand[1]} auditrij. "
        f"Actuele geverifieerde sets {len(juiste_sets)}/{len(sets)}."
    )
    if stand != actueel:
        if juiste_sets:
            preupdate.stage(core, juiste_sets[0], actor="lokale review")
            assert (
                preupdate.apply_staged(core)["ok"] and encoderstand(enc.path) == actueel
            )
            meting += " De andere set herstelde 75% en 1 auditrij."
        pytest.fail(meting, pytrace=False)
    print(meting)


@pytest.mark.parametrize("bron", ["sessie", "API-sleutel", "outbox"])
async def test_sse_aanmelding_bij_tijdelijke_databaseuitval(
    app, client, ctx, tmp_path, monkeypatch, bron
):
    route = "/api/v1/events/stream"
    publish(ctx)
    headers = (
        {"Cookie": client.build_request("GET", PATH).headers["cookie"]}
        if bron == "sessie"
        else key(client)
    )
    status, frames = await sse(app, headers, "0")
    assert status == 200 and len(frames) == 2
    methode, selectie = {
        "sessie": ("fetch_one", "FROM sessions"),
        "API-sleutel": ("fetch_one", "FROM api_keys"),
        "outbox": ("fetch_all", "FROM outbox_retention"),
    }[bron]
    normaal = getattr(ctx.db, methode)
    lock_db = tmp_path / "bezet.db"
    geraakt = []

    def lees(sql, *args, **kwargs):
        if selectie in sql:
            geraakt.append(True)
            with closing(sqlite3.connect(lock_db, timeout=0)) as apart:
                apart.execute("SELECT COUNT(*) FROM sqlite_schema").fetchone()
        return normaal(sql, *args, **kwargs)

    with closing(sqlite3.connect(lock_db)) as bezet:
        bezet.execute("BEGIN EXCLUSIVE")
        monkeypatch.setattr(ctx.db, methode, lees)
        vervoer = httpx.ASGITransport(app=app, raise_app_exceptions=False)
        async with httpx.AsyncClient(
            transport=vervoer, base_url="http://fixture.invalid"
        ) as bezoek:
            antwoord = await asyncio.wait_for(
                bezoek.get(route, headers=headers), timeout=6
            )
    assert geraakt == [True]
    assert "id: " not in antwoord.text and "data: " not in antwoord.text
    inhoud = (
        antwoord.json()
        if antwoord.headers.get("content-type", "").startswith("application/json")
        else {}
    )
    retry = antwoord.headers.get("Retry-After")
    meting = (
        f"{bron}: HTTP {antwoord.status_code}, database_unavailable="
        f"{inhoud.get('code') == 'database_unavailable'}, Retry-After={retry or 'ontbreekt'}, 0 events."
    )
    if (
        antwoord.status_code != 503
        or inhoud.get("code") != "database_unavailable"
        or not retry
    ):
        pytest.fail(meting, pytrace=False)
    print(meting)


async def test_sleutelrechten_bij_bestaande_sleutel(client, ctx, monkeypatch):
    publish(ctx)
    geldig = page(client)["next_cursor"]
    kop = key(client)
    _, frames = await sse(client.app, kop, "0")
    sse_positie = frames[1].splitlines()[0].removeprefix("id: ")
    bestand = event_keys.key_path(ctx.db.path.parent)
    assert bestand.stat().st_mode & 0o777 == 0o600
    assert bestand.parent.stat().st_mode & 0o777 == 0o700
    bestand.chmod(0o644)
    bestand.parent.chmod(0o755)
    monkeypatch.setenv("MG_UID", str(os.geteuid()))
    monkeypatch.setenv("MG_GID", str(os.getegid()))
    bootstrap.run(bestand.parent.parent, [], [])
    papier = client.get(PATH)
    hervat = client.get(PATH, params={"cursor": geldig})
    status, _ = await sse(client.app, kop, sse_positie)
    sm = bestand.stat().st_mode & 0o777
    dm = bestand.parent.stat().st_mode & 0o777
    meting = (
        f"Na bootstrap map {dm:04o}, bestand {sm:04o}, polling {papier.status_code}, "
        f"hervatten {hervat.status_code}, SSE {status}."
    )
    if (dm, sm) != (0o700, 0o600) and (
        papier.is_success or hervat.is_success or status == 200
    ):
        pytest.fail(meting, pytrace=False)
    print(meting)


async def test_live_reset_als_de_sleutel_wegvalt(ctx, client):
    publish(ctx)
    eerste = page(client)
    _, herlezing = await sse(client.app, key(client), eerste["next_cursor"])
    controleer_sse_reset(herlezing[0])
    positie = herlezing[1].splitlines()[0].removeprefix("id: ")
    expired(client.get(PATH, params={"cursor": positie}))
    verzoek = Request(
        {"type": "http", "headers": [(b"last-event-id", positie.encode())]},
        receive=AsyncMock(return_value={"type": "http.request", "body": b""}),
    )
    antwoord = await stream(verzoek, None, ctx)
    async with aclosing(antwoord.body_iterator) as berichten:
        assert (await anext(berichten)).startswith("retry:")
        event_keys.key_path(ctx.db.path.parent).unlink()
        ctx.events.publish("recovery.committed", {})
        controleer_sse_reset(await anext(berichten))
        with pytest.raises(StopAsyncIteration):
            await anext(berichten)
