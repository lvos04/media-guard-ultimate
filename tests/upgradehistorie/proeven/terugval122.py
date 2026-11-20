"""Kandidaat zet klaar, de herstelcode van de publieke 1.2.2-bron wisselt om."""

import os
import sqlite3
import subprocess
import sys
from contextlib import closing
from pathlib import Path

from mediaguard import __version__, aio
from mediaguard.maintenance import preupdate
from upgradebewijs import herstel

root = Path(os.environ["WERK"]) / "installatie"
core = root / "core"
db = core / "mediaguard.db"
bron = Path(os.environ["HISTORIE"]) / "122/apps/core"
repo = Path(os.environ["REPO"])


def stand():
    with closing(sqlite3.connect(db.as_uri() + "?mode=ro", uri=True)) as conn:
        return (
            conn.execute("SELECT id,progress FROM jobs ORDER BY id").fetchall(),
            conn.execute("SELECT * FROM outbox_events ORDER BY id").fetchall(),
            conn.execute(
                "SELECT version FROM schema_migrations ORDER BY version"
            ).fetchall(),
        )


if sys.argv[1:] == ["omwisselen"]:
    assert __version__ == "1.2.2" and Path(preupdate.__file__).resolve().is_relative_to(
        bron
    )
    naam = preupdate.pending(core)["set"]
    assert preupdate.find(core / "backups" / preupdate.FOLDER, naam)
    uit = preupdate.apply_staged(core)
    assert uit["ok"] and uit["set"] == naam
else:
    assert Path(preupdate.__file__).resolve().is_relative_to(repo / "apps/core")
    herstel(repo / "tests/fixtures/aio/core-1.2.2.sql", db)
    with closing(sqlite3.connect(db)) as writer, writer:
        writer.execute("UPDATE jobs SET progress=0.75")
    voor = stand()
    assert voor[0] and len(voor[1]) == 2 and voor[2]
    env = {}
    aio.before_services(env, root, None, migrate_core=True)
    archief = Path(env["MG_PRE_UPDATE_SET"])
    assert len(stand()[2]) > len(voor[2]) and not preupdate.verify(archief)
    assert preupdate.stage(core, archief, actor="lokale proef")["set"] == archief.name
    subprocess.run(
        [sys.executable, "-B", __file__, "omwisselen"],
        cwd=root,
        check=True,
        env=os.environ | {"PYTHONPATH": f"{bron}:{repo}/scripts"},
    )
    assert stand() == voor
    print(
        "Herstelcode 1.2.2 herkende de naam en voltooide de omwisseling; taken op 75%, twee events en het oude schema gelijk."
    )
