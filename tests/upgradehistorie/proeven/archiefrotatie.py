"""Controleer de werkelijk beschikbare herstelset bij snelle migratieherhalingen."""

import os
import sqlite3
import sys
import tempfile
import time
from contextlib import closing, nullcontext
from datetime import UTC, datetime
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, os.environ["WERK"])
import proeven
from mediaguard import aio
from mediaguard import db as database
from mediaguard.app import build_context
from mediaguard.jobs import JobContext
from mediaguard.maintenance import preupdate

seconde = datetime.now(UTC).replace(microsecond=0)


class Archiefklok(datetime):
    @classmethod
    def now(cls, tz=None):
        return seconde.astimezone(tz)


def voortgang(pad, taak):
    with closing(sqlite3.connect(f"{pad.as_uri()}?mode=ro", uri=True)) as lezer:
        return lezer.execute(
            "SELECT progress FROM jobs WHERE id=?", (taak,)
        ).fetchone()[0]


def afbreken(*args):
    raise InterruptedError(
        "synthetische onderbreking na de herstelset, vóór de eerste migratie"
    )


with tempfile.TemporaryDirectory(dir=os.environ["TMPDIR"]) as tijdelijk:
    conf, oud = proeven.basis(Path(tijdelijk))
    with patch.object(database, "MIGRATIONS_DIR", oud):
        ctx = build_context(conf)
        with closing(ctx.db):
            taak = proeven.enqueue(ctx.db, "proef.voortgang")
            JobContext(taak, "proef.voortgang", {}, None, None, ctx).progress(0.25)

    systeemklok = os.environ.get("PROEF_ARCHIEFKLOK") == "systeem"
    if systeemklok:
        # Begin vlak na een hele seconde. De archieffunctie gebruikt hier de echte klok.
        time.sleep(1.01 - time.time() % 1)
    start = time.monotonic()
    with (
        nullcontext()
        if systeemklok
        else patch.object(preupdate, "datetime", Archiefklok)
    ):
        for _ in range(6):
            with (
                closing(database.Database(conf.db_path)) as db,
                patch.object(db, "_apply_migration", afbreken),
            ):
                try:
                    db.initialize()
                except InterruptedError:
                    pass
                else:
                    raise AssertionError("onderbreking niet bereikt")
        with patch.object(database, "MIGRATIONS_DIR", oud):
            ctx = build_context(conf)
            with closing(ctx.db):
                JobContext(taak, "proef.voortgang", {}, None, None, ctx).progress(0.75)
        assert voortgang(conf.db_path, taak) == 0.75
        env = {}
        try:
            aio.before_services(env, conf.data_dir.parent, None)
            assert "MG_PRE_UPDATE_SET" in env
            set_bestaat = (Path(env["MG_PRE_UPDATE_SET"]) / "meta.json").is_file()
            with (
                patch.dict(os.environ, env),
                closing(database.Database(conf.db_path)) as db,
            ):
                db.initialize()
        except (RuntimeError, preupdate.SetError):
            with closing(sqlite3.connect(conf.db_path)) as db:
                assert not db.execute(
                    "SELECT 1 FROM schema_migrations WHERE version=?", (proeven.STAP,)
                ).fetchone()
            assert voortgang(conf.db_path, taak) == 0.75
            print("Rotatie stopte de migratie; het vorige schema toont nog 75%.")
            raise SystemExit(0)

    if systeemklok:
        print(
            f"Zes herhalingen en de volgende migratie met de gewone systeemklok: {round(1000 * (time.monotonic() - start))} ms."
        )
    with closing(sqlite3.connect(conf.db_path)) as db:
        assert db.execute(
            "SELECT 1 FROM schema_migrations WHERE version=?", (proeven.STAP,)
        ).fetchone()
    assert voortgang(conf.db_path, taak) == 0.75
    sets = sorted(
        p.parent for p in conf.data_dir.glob("backups/voor-update/*/meta.json")
    )
    assert sets and all(not preupdate.verify(s) for s in sets)
    actueel = [s for s in sets if voortgang(s / "mediaguard.db", taak) == 0.75]
    beschikbaar = (actueel or sets)[-1]
    preupdate.stage(conf.data_dir, beschikbaar, actor="lokale-herreview")
    terug = preupdate.apply_staged(conf.data_dir)
    assert terug and terug.get("ok")
    na = voortgang(conf.db_path, taak)
    opzij = Path(terug["previous"]) / f"{conf.data_dir.name}__{conf.db_path.name}"
    assert voortgang(opzij, taak) == 0.75
    for pad, verwacht in ((conf.db_path, 0), (opzij, 1)):
        with closing(sqlite3.connect(f"{pad.as_uri()}?mode=ro", uri=True)) as lezer:
            assert (
                lezer.execute(
                    "SELECT COUNT(*) FROM schema_migrations WHERE version=?",
                    (proeven.STAP,),
                ).fetchone()[0]
                == verwacht
            )
    print(
        "De apart bewaarde database met migratie 151 bevat 75%; de teruggezette database gebruikt het oude schema."
    )
    print(
        f"Supervisorpad bestaat={'ja' if set_bestaat else 'nee'}; migratie 151 toegepast; actuele sets {len(actueel)}/{len(sets)}; taak vóór migratie 75%, na terugval {na:.0%}."
    )
    raise SystemExit(int(not actueel or na != 0.75))
