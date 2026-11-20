"""Volg bij terugval de herstelset die de echte alles-in-één-start heeft aangewezen."""

import os
import sqlite3
import subprocess
import sys
import tarfile
import tempfile
from contextlib import closing
from pathlib import Path
from unittest.mock import patch

if len(sys.argv) == 4 and sys.argv[1] == "schrijf":
    from types import SimpleNamespace

    from mediaguard.audit import audit
    from mediaguard.db import Database
    from mediaguard.events import EventBus
    from mediaguard.jobs import JobContext

    assert (
        Path(sys.modules["mediaguard.db"].__file__)
        .resolve()
        .is_relative_to(Path(os.environ["BRON_SCHRIJVER"]))
    )
    with closing(Database(Path(sys.argv[2]))) as db:
        bus = EventBus(db)
        taak = int(sys.argv[3])
        ctx = SimpleNamespace(db=db, events=bus)
        JobContext(taak, "proef.voortgang", {}, None, None, ctx).progress(0.75)
        nummer = audit(
            db,
            actor="lokale-terugvalproef",
            action="job.retry",
            target_type="job",
            target_id=taak,
        )
        bus.publish("recovery.committed", {})
        print(nummer)
    raise SystemExit(0)

sys.path.insert(0, os.environ["WERK"])
import proeven
from mediaguard import aio
from mediaguard import db as database
from mediaguard.app import build_context
from mediaguard.maintenance import preupdate


def lees(pad, taak):
    with closing(sqlite3.connect(pad.as_uri() + "?mode=ro", uri=True)) as conn:
        (procent,) = conn.execute(
            "SELECT progress FROM jobs WHERE id=?", (taak,)
        ).fetchone()
        (bewijs,) = conn.execute(
            "SELECT count(*) FROM outbox_events WHERE topic=?", ("recovery.committed",)
        ).fetchone()
    return (int(procent * 100), bewijs)


with tempfile.TemporaryDirectory(dir=os.environ["TMPDIR"]) as tijdelijk:
    conf, oud = proeven.basis(Path(tijdelijk))
    with patch.object(database, "MIGRATIONS_DIR", oud):
        ctx = build_context(conf)
        with closing(ctx.db):
            taak = proeven.enqueue(ctx.db, "proef.voortgang")
            proeven.JobContext(taak, "proef.voortgang", {}, None, None, ctx).progress(
                0.25
            )
    nul = lees(conf.db_path, taak)
    maak_set = preupdate._make_set
    sets = []

    echte_schrijver = sys.argv[1:] == ["historisch"]
    audit_ids = []
    if echte_schrijver:
        historie = Path(tijdelijk) / "schrijver"
        historie.mkdir()
        archief = historie / "bron.tar"
        with archief.open("wb") as doel:
            subprocess.run(
                [
                    "git",
                    "-C",
                    os.environ["REPO"],
                    "archive",
                    "3998bea1880229e8351b4b28e8f912275cdd15c1",
                    "apps/core/mediaguard",
                ],
                stdout=doel,
                check=True,
            )
        with tarfile.open(archief) as tar:
            tar.extractall(historie, filter="data")
        bron = str(historie / "apps/core")

    def met_schrijver(*args, **kwargs):
        resultaat = maak_set(*args, **kwargs)
        sets.append(resultaat)
        if len(sets) == 1 and echte_schrijver:
            uit = subprocess.check_output(
                [sys.executable, __file__, "schrijf", str(conf.db_path), str(taak)],
                env=os.environ | {"PYTHONPATH": bron, "BRON_SCHRIJVER": bron},
                text=True,
            )
            audit_ids.append(int(uit))
        elif len(sets) == 1:
            # Eén reeds draaiende oude schrijver na publicatie, vóór het SQLite-slot.
            with (
                closing(sqlite3.connect(conf.db_path, timeout=0)) as schrijver,
                schrijver,
            ):
                n = schrijver.execute(
                    "UPDATE jobs SET progress=0.75 WHERE id=?", (taak,)
                ).rowcount
                assert n == 1
                schrijver.execute(
                    "INSERT INTO outbox_events(ts,topic,payload) VALUES (?,?,?)",
                    (database.utcnow(), "recovery.committed", "{}"),
                )
        return resultaat

    env = {}
    with patch.object(preupdate, "_make_set", met_schrijver):
        regels = aio.before_services(env, conf.data_dir.parent, None, migrate_core=True)
    genoemd = Path(env["MG_PRE_UPDATE_SET"])
    assert any(genoemd.name in regel for regel in regels)
    assert genoemd == preupdate.find(genoemd.parent, genoemd.name)
    assert len(sets) == 2 and all(not preupdate.verify(pad) for pad in sets)
    assert lees(sets[0] / "mediaguard.db", taak) == nul
    actueel = (75, nul[1] + 1)
    assert lees(sets[1] / "mediaguard.db", taak) == lees(conf.db_path, taak) == actueel
    with closing(sqlite3.connect(conf.db_path)) as lezer:
        assert lezer.execute("SELECT max(version) FROM schema_migrations").fetchone()[
            0
        ] == max(f.stem for f in database.MIGRATIONS_DIR.glob("*.sql"))
    if echte_schrijver:
        (nummer,) = audit_ids
        with proeven.web(conf) as (_, client):
            antwoord = client.get(f"/connector/v1/evidence/audit:{nummer}")
            assert (
                antwoord.status_code == 200
                and antwoord.json()["audit"]["action"] == "job.retry"
            )
    preupdate.stage(conf.data_dir, genoemd, actor="terugvalproef")
    resultaat = preupdate.apply_staged(conf.data_dir)
    assert resultaat and resultaat["ok"]
    terug = lees(conf.db_path, taak)
    apart = Path(resultaat["previous"]) / "core__mediaguard.db"
    assert lees(apart, taak) == actueel
    if echte_schrijver:

        def audit_in(path):
            with closing(sqlite3.connect(path.as_uri() + "?mode=ro", uri=True)) as conn:
                return conn.execute(
                    "SELECT COUNT(*) FROM audit_events WHERE id=? AND actor=? AND action=?",
                    (nummer, "lokale-terugvalproef", "job.retry"),
                ).fetchone()[0]

        assert audit_in(sets[1] / "mediaguard.db") == audit_in(apart) == 1
        aanwezig = audit_in(conf.db_path)
        print(
            f"Oude schrijver: bewijs-API HTTP 200 bij 75%; gemelde set teruggezet: {terug[0]}%, "
            f"{aanwezig}/1 auditstuk, {terug[1] - nul[1]}/1 nieuw event; andere set en opzijgezette database bevatten het auditstuk."
        )
        raise SystemExit(int(terug != actueel or aanwezig != 1))
    print(
        f"Twee sets met geldige SHA-256; vóór migratie {actueel[0]}% en 1 nieuw bewijs-event; "
        f"terugval met de gemelde set {terug[0]}% en {terug[1] - nul[1]} nieuwe bewijs-events."
    )
    raise SystemExit(int(terug != actueel))
