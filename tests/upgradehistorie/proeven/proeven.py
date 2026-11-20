"""Herhaalbare lokale regressieproeven voor S-P3-620. Elke run ruimt zijn testdata op."""

import asyncio
import errno
import hashlib
import importlib.util
import json
import os
import re
import secrets
import shutil
import sqlite3
import sys
import tempfile
from collections import Counter
from concurrent.futures import ThreadPoolExecutor
from contextlib import closing, contextmanager
from pathlib import Path
from threading import Barrier
from unittest.mock import patch

sys.dont_write_bytecode = True
KETEN = Path(os.environ["TMPDIR"])
REPO = Path(os.environ["MG_REPO"])
GESCHIEDENIS = Path(os.environ.get("REPO", REPO))
KERN = REPO / "apps" / "core"
sys.path[:0] = [str(KERN), str(KERN / "tests"), str(REPO / "scripts")]

import httpx
import upgradebewijs
from fastapi.testclient import TestClient
from mediaguard import aio, schema
from mediaguard import db as database
from mediaguard.api.events import stream
from mediaguard.app import build_context, create_app
from mediaguard.config import Settings
from mediaguard.events import EventBus
from mediaguard.jobs import JobContext
from starlette.requests import Request
from test_connector_lezen import database_snapshot

spec = importlib.util.spec_from_file_location(
    "oude_taakcode", Path(os.environ["HISTORIE"]) / "150/apps/core/mediaguard/jobs.py"
)
oud_jobs = importlib.util.module_from_spec(spec)
sys.modules[spec.name] = oud_jobs
spec.loader.exec_module(oud_jobs)
enqueue = oud_jobs.enqueue
from mediaguard.maintenance import preupdate
from mediaguard.maintenance.housekeeping import prune, setting_defs
from mediaguard.mediaserver import service
from mediaguard.mediaserver.api import register_settings

BASIS = "29b9af2e55385d1f9f9c536d55dd3f42d8e92368"
STAP = "151_outbox_event_identity"
FIXTURE = REPO / "tests" / "fixtures" / "aio" / "core-1.2.2.sql"
ROUTE = "/connector/v1/events"


def instellingen(map_):
    return Settings(
        data_dir=map_ / "core",
        staging_dir=map_ / "staging",
        quarantine_dir=map_ / "quarantaine",
        start_background=False,
    )


def basis(werk):
    """Maak een verzegelde nulmeting met alleen migratie 151 nog te gaan."""
    oud = werk / "migraties-voor-151"
    shutil.copytree(
        Path(os.environ["HISTORIE"]) / "150/apps/core/mediaguard/migrations", oud
    )
    origineel = instellingen(werk / "opbouw").db_path
    upgradebewijs.herstel(FIXTURE, origineel)
    conf = instellingen(werk / "live")
    with (
        patch.object(database, "MIGRATIONS_DIR", oud),
        closing(database.Database(origineel)) as db,
    ):
        db.initialize()
        db.backup_to(conf.db_path)
    return conf, oud


@contextmanager
def web(conf):
    app = create_app(conf)
    with closing(app.state.ctx.db), TestClient(app) as client:
        meta = json.loads(FIXTURE.with_suffix(".json").read_text())
        assert (
            client.post(
                "/api/v1/auth/login",
                json={
                    "username": meta["user"],
                    "password": meta["password"],
                },
            ).status_code
            == 200
        )
        yield app.state.ctx, client


def domeinen(ctx):
    return {
        naam: rijen
        for (naam, _), rijen in database_snapshot(ctx).items()
        if naam not in ("sqlite_sequence", "schema_migrations")
    }


class Kijkproef:
    """Twee synthetische gebruikers en een werkende sessie op een gelogd testtransport."""

    def __init__(self):
        self.sleutel = secrets.token_urlsafe()
        self.gegevens = {
            f"proef-{n}": {
                "Policy": {"IsAdministrator": n == 1},
                "Played": n == 1,
                "PlaybackPositionTicks": n * 120000000,
                "IsFavorite": n == 2,
                "Rating": n + 5,
                "AudioStreamIndex": n,
                "SubtitleStreamIndex": n + 1,
            }
            for n in (1, 2)
        }
        self.verzoeken = []
        self.transport = httpx.MockTransport(self.antwoord)

    def antwoord(self, verzoek):
        self.verzoeken.append((verzoek.method, verzoek.url.path))
        assert verzoek.url.host == "emby.invalid"
        assert verzoek.headers.get("X-Emby-Token") == self.sleutel
        if verzoek.method != "GET":
            return httpx.Response(405)
        if verzoek.url.path == "/emby/Sessions":
            return httpx.Response(
                200, json=[{"UserName": "proef-1", "NowPlayingItem": {"Name": "proef"}}]
            )
        if verzoek.url.path == "/emby/Users":
            return httpx.Response(
                200,
                json=[
                    {"Id": u, "Policy": d["Policy"]} for u, d in self.gegevens.items()
                ],
            )
        for gebruiker, record in self.gegevens.items():
            if verzoek.url.path == f"/emby/Users/{gebruiker}/Items":
                return httpx.Response(
                    200,
                    json={
                        "Items": [
                            {
                                "Id": "proef",
                                "UserData": {
                                    k: v for k, v in record.items() if k != "Policy"
                                },
                            }
                        ]
                    },
                )
        return httpx.Response(404)

    def lees(self):
        with httpx.Client(
            transport=self.transport,
            base_url="http://emby.invalid/emby/",
            headers={"X-Emby-Token": self.sleutel},
        ) as client:
            users = client.get("Users")
            users.raise_for_status()
            stand = {}
            for user in users.json():
                data = client.get("Users/" + user["Id"] + "/Items")
                data.raise_for_status()
                stand[user["Id"]] = {
                    "Policy": user["Policy"],
                    **data.json()["Items"][0]["UserData"],
                }
            return stand

    def koppel(self, ctx):
        ctx.services["mediaserver_transport"] = self.transport
        server = service.server(ctx)
        assert server is not None
        assert [s.user for s in asyncio.run(server.sessions())] == ["proef-1"]
        return server


def behoud(werk):
    conf, oud = basis(werk)
    kijk = Kijkproef()
    with patch.object(database, "MIGRATIONS_DIR", oud):
        ctx = build_context(conf)
        with closing(ctx.db):
            register_settings()
            ctx.config.set("global", "", "mediaserver.kind", "emby", "proef")
            ctx.config.set(
                "global", "", "mediaserver.url", "http://emby.invalid", "proef"
            )
            ctx.secrets.set(service.SECRET_KEY, kijk.sleutel, "proef")
            taak = enqueue(ctx.db, "proef.voortgang")
            JobContext(taak, "proef.voortgang", {}, None, None, ctx).progress(0.25)
            bestaande_client = kijk.koppel(ctx)
            nul = kijk.lees()
            assert nul == kijk.gegevens
            voor = domeinen(ctx)
            assert len(voor["outbox_events"]) == 2 and len(voor["users"]) == 4

    # Pauzeer de toepassingscode na DROP en vóór RENAME. Een tweede lezer ziet de oude stand.
    # Eén synthetische onderbreking laat de open transactie terugrollen.
    delen = database._split_sql
    midden = []

    def onderbreek(sql):
        for opdracht in delen(sql):
            if "ALTER TABLE outbox_events_new RENAME" in opdracht:
                with closing(
                    sqlite3.connect(f"{conf.db_path.as_uri()}?mode=ro", uri=True)
                ) as lezer:
                    assert (
                        lezer.execute("SELECT count(*) FROM outbox_events").fetchone()[
                            0
                        ]
                        == 2
                    )
                assert [s.user for s in asyncio.run(bestaande_client.sessions())] == [
                    "proef-1"
                ]
                midden.append(kijk.lees())
                raise InterruptedError("lokale onderbreking tussen DROP en RENAME")
            yield opdracht

    with (
        closing(database.Database(conf.db_path)) as db,
        patch.object(database, "_split_sql", onderbreek),
    ):
        try:
            db.initialize()
        except InterruptedError:
            pass
        else:
            raise AssertionError("onderbreking niet bereikt")
        assert midden == [nul]
        assert (
            db.fetch_one("SELECT 1 FROM schema_migrations WHERE version=?", (STAP,))
            is None
        )
        assert (
            db.fetch_one("SELECT 1 FROM sqlite_schema WHERE name='outbox_events_new'")
            is None
        )

    sets = list(conf.data_dir.glob("backups/voor-update/*/meta.json"))
    assert len(sets) == 1
    archief = sets[0].parent
    meta = json.loads(sets[0].read_text())
    kopie = archief / "mediaguard.db"
    sha = hashlib.sha256(kopie.read_bytes()).hexdigest()
    assert (
        meta["to_schema"] == max(f.stem for f in database.MIGRATIONS_DIR.glob("*.sql"))
        and sha == meta["files"][0]["sha256"]
        and not preupdate.verify(archief)
    )
    print("SHA-256 vóór migratie gecontroleerd:", sha)

    ctx = build_context(conf)
    with closing(ctx.db):
        na = domeinen(ctx)
        for t, data in voor.items():
            cols = {k for row in data for k, _ in row}
            assert (
                Counter(
                    tuple((k, v) for k, v in row if k in cols)
                    for row in na[t].elements()
                )
                == data
            ), t
        assert ctx.db.fetch_one("PRAGMA integrity_check")[
            0
        ] == "ok" and not ctx.db.fetch_all("PRAGMA foreign_key_check")
        kijk.koppel(ctx)
        assert kijk.lees() == nul
    with web(conf) as (ctx, client):
        kijk.koppel(ctx)
        assert client.get(f"/api/v1/jobs/{taak}").json()["progress"] == 0.25
        JobContext(taak, "proef.voortgang", {}, None, None, ctx).progress(0.75)
        aantal = len(kijk.verzoeken)
        r = client.get(ROUTE)
        assert r.status_code == 200 and len(r.json()["events"]) == 2
        assert len(kijk.verzoeken) == aantal
    with web(conf) as (_, client):
        assert client.get(f"/api/v1/jobs/{taak}").json()["progress"] == 0.75

    terug = instellingen(werk / "terug")
    terug.data_dir.mkdir(parents=True)
    shutil.copy2(kopie, terug.db_path)
    shutil.copy2(conf.secret_key_path, terug.secret_key_path)
    with patch.object(database, "MIGRATIONS_DIR", oud):
        ctx = build_context(terug)
        with closing(ctx.db):
            assert domeinen(ctx) == voor
            kijk.koppel(ctx)
            assert kijk.lees() == nul
    ctx = build_context(terug)
    with closing(ctx.db):
        weer = domeinen(ctx)
        (uitgifte_1,) = na.pop("outbox_retention").elements()
        (uitgifte_2,) = weer.pop("outbox_retention").elements()
        r1, r2 = dict(uitgifte_1), dict(uitgifte_2)
        k1, k2 = r1.pop("cursor_key"), r2.pop("cursor_key")
        assert k1 == k2 == "core/outbox.key"
        extern1 = (conf.data_dir.parent / "secrets" / k1).read_bytes()
        extern2 = (terug.data_dir.parent / "secrets" / k2).read_bytes()
        assert len(extern1) == len(extern2) == 32 and extern1 != extern2
        (h1,) = na.pop("outbox_cursor_history").elements()
        (h2,) = weer.pop("outbox_cursor_history").elements()
        a, b = dict(h1), dict(h2)
        v1, v2 = a.pop("witness"), b.pop("witness")
        assert a == b == {"revision": 0, "last_position": None}
        assert len(v1) == len(v2) == 16 and v1 != v2
        (g1,) = na.pop("outbox_generation").elements()
        (g2,) = weer.pop("outbox_generation").elements()
        een, twee = dict(g1), dict(g2)
        assert een["singleton"] == twee["singleton"] == 1
        assert een.keys() == twee.keys() == {"singleton", "generation"}
        assert len(een["generation"]) == len(twee["generation"]) == 16
        assert een["generation"] != twee["generation"]
        # Beide upgrades maken voor het eerst een eigen generatie voor het back-updoel.
        assert "offsite_target_generation" not in voor
        generaties = []
        for meting in (na, weer):
            (rij,) = meting.pop("offsite_target_generation").elements()
            doel = dict(rij)
            generatie = doel.pop("generation")
            assert doel == {"id": 1} and re.fullmatch(r"[0-9a-f]{32}", generatie)
            generaties.append(generatie)
        assert generaties[0] != generaties[1]
        assert r1 == r2 and weer == na and all(m == "GET" for m, _ in kijk.verzoeken)
    print(
        f"{len(voor)} oorspronkelijke domeintabellen gelijk; {sum(bool(v) for v in voor.values())} gevuld"
    )
    print(
        "2/2 gebruikers gelijk vóór, tijdens, na en na terugval; 0 schrijfverzoeken; taak 25% -> 75% na herstart"
    )


def archieffout(werk):
    conf, _ = basis(werk)
    vervangen = os.replace
    pogingen = []

    def opslagfout(bron, doel):
        if Path(doel).name == "mediaguard.db" and Path(doel).parent.name.endswith(
            ".part"
        ):
            pogingen.append(1)
            raise OSError(errno.ENOSPC, "gesimuleerde volle archiefopslag")
        return vervangen(bron, doel)

    env = {}
    with patch.object(os, "replace", opslagfout):
        try:
            aio.before_services(env, conf.data_dir.parent, None)
            with (
                patch.dict(os.environ, env),
                closing(database.Database(conf.db_path)) as db,
            ):
                if not env.get(schema.AIO_RECOVERY_ENV):
                    db.initialize()
        except (OSError, RuntimeError, sqlite3.DatabaseError):
            if not pogingen:
                raise
    assert pogingen
    with closing(
        sqlite3.connect(f"{conf.db_path.as_uri()}?mode=ro", uri=True)
    ) as lezer:
        toegepast = lezer.execute(
            "SELECT count(*) FROM schema_migrations WHERE version=?", (STAP,)
        ).fetchone()[0]
    sets = sum(
        not preupdate.verify(p.parent)
        for p in conf.data_dir.glob("backups/voor-update/*/meta.json")
    )
    print(
        f"Archiefpogingen mislukt={len(pogingen)}; herstelsets={sets}; migratie_151_toegepast={toegepast}"
    )
    return 1 if toegepast and not sets else 0


def terugvalcursor(werk):
    conf, vorig_schema = basis(werk)
    # Bewaar de stand vóór 151. De eerste kandidaat geeft daarna een cursor uit.
    terug = instellingen(werk / "snapshot")
    with (
        patch.object(database, "MIGRATIONS_DIR", vorig_schema),
        closing(database.Database(conf.db_path)) as db,
    ):
        db.initialize()
        db.backup_to(terug.db_path)
    with web(conf) as (ctx, client):
        ctx.events.publish("library.synced", {})
        eerste = client.get(ROUTE)
        assert eerste.status_code == 200
        cursor = eerste.json()["next_cursor"]
        oud = eerste.json()["events"][-1]
    # Open de snapshot met het vorige schema, publiceer ander werk en upgrade opnieuw.
    with patch.object(database, "MIGRATIONS_DIR", vorig_schema):
        vorig = build_context(terug)
        with closing(vorig.db):
            assert (
                vorig.db.fetch_one(
                    "SELECT 1 FROM schema_migrations WHERE version=?", (STAP,)
                )
                is None
            )
            vorig.events.publish("recovery.committed", {})
    with web(terug) as (_, client):
        hervat = client.get(ROUTE, params={"cursor": cursor})
        vers = client.get(ROUTE)
        assert vers.status_code == 200
        nieuw = vers.json()["events"][-1]
        assert oud["event_type"] != nieuw["event_type"]
        gelijk = (oud["event_id"], oud["source_event_id"]) == (
            nieuw["event_id"],
            nieuw["source_event_id"],
        )
        zicht = len(hervat.json()["events"]) if hervat.status_code == 200 else None
    print(f"Twee gebeurtenissen: {oud['event_type']} -> {nieuw['event_type']}")
    uuid_gelijk = "ja" if oud["event_id"] == nieuw["event_id"] else "nee"
    print(
        f"Bron-ID {oud['source_event_id']} -> {nieuw['source_event_id']}; zelfde UUID={uuid_gelijk}; oude cursor HTTP {hervat.status_code}; ontvangen={zicht}"
    )
    return 1 if gelijk or (hervat.status_code == 200 and zicht == 0) else 0


def oude_client(werk):
    conf, oud = basis(werk)
    with patch.object(database, "MIGRATIONS_DIR", oud):
        ctx = build_context(conf)
        with closing(ctx.db):
            ctx.events.publish("library.synced", {})
            laatst = ctx.db.fetch_one("SELECT max(id) FROM outbox_events")[0]
            # De vorige versie ruimt vervallen gebeurtenissen op terwijl de client weg is.
            ctx.registry.register(*setting_defs())
            ctx.db.execute("UPDATE outbox_events SET ts=?", ("2000-01-01T00:00:00Z",))
            assert prune(ctx)["events"] == laatst
    ctx = build_context(conf)
    with closing(ctx.db):
        ctx.events.publish("recovery.committed", {})
        nieuw = ctx.db.fetch_one("SELECT max(id) FROM outbox_events")[0]

        async def eerste_bericht():
            request = Request(
                {"type": "http", "headers": [(b"last-event-id", str(laatst).encode())]}
            )
            sse = await stream(request, None, ctx)
            try:
                return await anext(sse.body_iterator)
            finally:
                await sse.body_iterator.aclose()

        bericht = asyncio.run(eerste_bericht()).splitlines()[0]
    print(
        f"Laatste oude SSE-ID={laatst}; nieuwe publicatie={nieuw}; eerste SSE-regel={bericht}"
    )
    return 1 if bericht.startswith("retry:") else 0


def gelijktijdig(werk):
    conf, _ = basis(werk)
    start = Barrier(3, timeout=15)

    def schrijf(onderwerp):
        with closing(database.Database(conf.db_path)) as db:
            bus = EventBus(db)
            start.wait()
            for n in range(12):
                bus.publish(onderwerp, {"nummer": n})

    with web(conf) as (ctx, client):
        ctx.db.execute("UPDATE outbox_events SET ts=?", ("2000-01-01T00:00:00Z",))
        eerste = client.get(ROUTE, params={"limit": 1})
        assert eerste.status_code == 200
        cursor = eerste.json()["next_cursor"]
        with ThreadPoolExecutor(max_workers=2) as pool:
            schrijvers = [
                pool.submit(schrijf, onderwerp)
                for onderwerp in ("library.synced", "recovery.committed")
            ]
            start.wait()
            assert prune(ctx)["events"] == 2
            for schrijver in schrijvers:
                schrijver.result()
        oud = client.get(ROUTE, params={"cursor": cursor})
        assert oud.status_code == 409 and oud.json()["code"] == "cursor_expired"
        assert oud.json()["details"] == {"restart": True}
        pagina = client.get(ROUTE)
        assert pagina.status_code == 200 and client.get(ROUTE).json() == pagina.json()
        events = pagina.json()["events"]
        ids = [int(e["source_event_id"]) for e in events]
        assert ids == sorted(set(ids)) and min(ids) > 2
        assert len(events) == len({e["event_id"] for e in events}) == 24
    print(
        "2 schrijvers, 24 unieke events, 2 verwijderingen; oude cursor HTTP 409; herlezing 24/24 gelijk"
    )


if __name__ == "__main__":
    proeven = {
        "behoud": behoud,
        "archieffout": archieffout,
        "terugvalcursor": terugvalcursor,
        "oude-client": oude_client,
        "gelijktijdig": gelijktijdig,
    }
    with tempfile.TemporaryDirectory(
        prefix="r-harde-eis-proef-", dir=KETEN
    ) as tijdelijk:
        sys.exit(proeven[sys.argv[1]](Path(tijdelijk)))
