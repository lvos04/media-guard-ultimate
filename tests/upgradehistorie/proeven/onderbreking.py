"""Hervat de gewone upgrade nadat uitsluitend het eigen bootstrap-proces wegviel."""

import os
import sqlite3
import subprocess
import sys
from contextlib import closing
from pathlib import Path
from unittest.mock import patch

from mediaguard import aio, bootstrap, db, event_keys
from mediaguard.maintenance import preupdate
from upgradebewijs import herstel

bron = Path(os.environ["PROEFBRON"])
assert Path(db.__file__).resolve() == bron / "apps/core/mediaguard/db.py"
root = Path(os.environ["TMPDIR"]) / "installatie"
pad = root / "core/mediaguard.db"
sleutelmap = root / "secrets"
sleutel = sleutelmap / event_keys.KEY_REF
os.environ.update(MG_UID=str(os.getuid()), MG_GID=str(os.getgid()))

historisch = sys.argv[-1] == "historisch"
if sys.argv[1] == "eenmalige-onderbreking":
    functie = "link" if historisch else "replace"
    publiceer = getattr(os, functie)

    def onderbreek_na_publicatie(src, dst, **kwargs):
        publiceer(src, dst, **kwargs)
        if Path(dst).name == sleutel.name:
            os._exit(81)

    with patch.object(os, functie, onderbreek_na_publicatie):
        bootstrap.run(sleutelmap, [], [])
    raise AssertionError("De geplande onderbreking is niet bereikt.")


def stand():
    with closing(sqlite3.connect(pad.as_uri() + "?mode=ro", uri=True)) as con:
        return (
            con.execute(
                "SELECT COUNT(*), MAX(version) FROM schema_migrations"
            ).fetchone(),
            con.execute("SELECT id, status, progress FROM jobs ORDER BY id").fetchall(),
            con.execute("SELECT id, topic FROM outbox_events ORDER BY id").fetchall(),
        )


herstel(Path(os.environ["REPO"]) / "tests/fixtures/aio/core-1.2.2.sql", pad)
voor = stand()
assert voor[1] and len(voor[2]) == 2
afgebroken = subprocess.run(
    [sys.executable, "-B", __file__, "eenmalige-onderbreking", sys.argv[-1]],
    check=False,
)
assert afgebroken.returncode == 81 and sleutel.stat().st_nlink == (
    2 if historisch else 1
)
assert stand() == voor
uitkomsten = []
for poging in range(2):
    bootstrap.run(sleutelmap, [], [])
    env = {}
    try:
        aio.before_services(env, root, None, migrate_core=True)
    except RuntimeError as exc:
        assert "geen zelfstandig regulier bestand" in str(exc), str(exc)
        na = stand()
        assert na[0][1] < "156_" and na[1:] == voor[1:]
        uitkomsten.append("geweigerd")
    else:
        na = stand()
        assert na[0][1] == max(f.stem for f in db.MIGRATIONS_DIR.glob("*.sql"))
        assert na[1:] == voor[1:]
        assert event_keys.read_key(pad.parent, event_keys.KEY_REF) is not None
        if poging == 0:
            assert not preupdate.verify(Path(env["MG_PRE_UPDATE_SET"]))
        uitkomsten.append("vrijgegeven")

gelukt = uitkomsten == ["vrijgegeven", "vrijgegeven"]
print(
    f"Na uitval bij sleutelpublicatie: {uitkomsten.count('vrijgegeven')}/2 herstarts vrijgegeven, "
    f"{uitkomsten.count('geweigerd')}/2 geweigerd; taken en 2/2 events op schijf behouden."
)
raise SystemExit(0 if gelukt else 1)
