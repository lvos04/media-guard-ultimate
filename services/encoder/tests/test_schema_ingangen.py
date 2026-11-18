"""Losse AV1-ingangen controleren ook de MediaGuard-database."""

import shutil
import sqlite3
from dataclasses import replace

import pytest

from app.config import settings
from app.db import Database


@pytest.mark.parametrize("onbekend", ["core", "encode"])
@pytest.mark.parametrize("ingang", [
    "initialize", "migraties", "back-up", "verificatie", "retentie", "onderhoud", "logrotatie",
])
def test_losse_av1_ingangen_toetsen_beide_databases(tmp_path, ingang, onbekend) -> None:
    from contextlib import closing

    from app import backups
    from app.db import SchemaTooNew
    from app.onderhoud import rotate_logs, run_maintenance

    instance = replace(
        settings, config_root=tmp_path / "encode", pipeline_root=tmp_path,
        work_root=tmp_path / "work", backup_root=tmp_path / "backups",
        database_path=tmp_path / "encode" / "app.db", container_mode=True,
    )
    Database(instance).initialize()
    shutil.rmtree(instance.backup_root)
    core = tmp_path / "core" / "mediaguard.db"
    core.parent.mkdir()
    with closing(sqlite3.connect(core)) as conn, conn:
        conn.execute("CREATE TABLE schema_migrations (version TEXT PRIMARY KEY)")
        conn.execute("INSERT INTO schema_migrations VALUES (?)",
                     ("999_toekomst" if onbekend == "core" else "005_api_key_scopes",))
    if onbekend == "encode":
        with closing(sqlite3.connect(instance.database_path)) as conn, conn:
            conn.execute("INSERT INTO schema_versie VALUES (999, 'proef', 'proef')")
    db = Database(instance)
    if ingang == "initialize":
        db.initialize(prepare=lambda: pytest.fail("mapvoorbereiding aangeroepen"))
        assert db.read_only
    else:
        with pytest.raises(SchemaTooNew):
            if ingang == "migraties":
                db.apply_migrations()
            elif ingang == "back-up":
                backups.create_backup(db, instance)
            elif ingang == "verificatie":
                backups.verify_backup(db, instance, 1)
            elif ingang == "onderhoud":
                run_maintenance(db, instance)
            elif ingang == "logrotatie":
                rotate_logs(db, instance)
            else:
                backups.enforce_retention(db)
    assert not instance.backup_root.exists()
    assert not instance.work_root.exists()
    assert not (instance.config_root / "logs").exists()
