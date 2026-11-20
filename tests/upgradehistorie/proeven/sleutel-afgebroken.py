"""Controleer de oude sleutel na een afgebroken migratie met intacte herstelset."""

import hashlib
import os
import subprocess
import sys
from contextlib import closing
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest.mock import patch

from mediaguard import db as database
from mediaguard.maintenance import preupdate


def stop_bij_156(self, conn, file):
    if file.stem == "156_outbox_cursor_history":
        raise InterruptedError("synthetische onderbreking vóór migratie 156")
    return uitvoeren(self, conn, file)


with TemporaryDirectory() as werk:
    root = Path(werk)
    pad = root / "core" / "mediaguard.db"
    historische_sql = (
        Path(os.environ["HISTORIE"]) / "154/apps/core/mediaguard/migrations"
    )
    with (
        patch.object(database, "MIGRATIONS_DIR", historische_sql),
        closing(database.Database(pad)) as db,
    ):
        db.initialize()
        (oud_materiaal,) = db.fetch_one("SELECT cursor_key FROM outbox_retention")
        assert isinstance(oud_materiaal, bytes) and len(oud_materiaal) == 32

    uitvoeren = database.Database._apply_migration
    with patch.object(database.Database, "_apply_migration", stop_bij_156):
        try:
            preupdate.snapshot_before_start(
                root,
                core_migrations=database.MIGRATIONS_DIR,
                encoder_migrations=None,
                app_version="proef",
                migrate_core=True,
            )
        except InterruptedError:
            pass
        else:
            raise AssertionError("Migratie had moeten stoppen")

    (archief,) = pad.parent.glob("backups/voor-update/voor-update-*")
    assert not preupdate.verify(archief)
    # Gebruik de echte oude lezer na uitval. De huidige losse kern bewaakt de
    # lopende supervisor-update, ook wanneer alleen zijn SQL-map is vervangen.
    lees = """
import hashlib, sys
from contextlib import closing
from pathlib import Path
from mediaguard import db
assert Path(db.__file__).resolve().parents[1] == Path(sys.argv[2])
with closing(db.Database(Path(sys.argv[1]))) as kern:
    kern.initialize()
    assert kern.fetch_one("SELECT 1 FROM schema_migrations WHERE version LIKE '156_%'") is None
    waarde, = kern.fetch_one("SELECT cursor_key FROM outbox_retention")
    assert hashlib.sha256(waarde).digest() == sys.stdin.buffer.read()
"""
    bron = historische_sql.parents[1]
    subprocess.run(
        [sys.executable, "-B", "-c", lees, str(pad), str(bron)],
        cwd=root,
        env=os.environ | {"PYTHONPATH": str(bron)},
        check=True,
        input=hashlib.sha256(oud_materiaal).digest(),
    )
    if oud_materiaal in (archief / "mediaguard.db").read_bytes():
        print(
            "Na afgebroken migratie staat de nog actieve oude sleutel ook onversleuteld in de nieuwe herstelset."
        )
        raise SystemExit(1)
    print("Na afgebroken migratie bevat de nieuwe herstelset geen sleutelmateriaal.")
