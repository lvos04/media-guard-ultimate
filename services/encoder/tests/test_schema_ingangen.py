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


def test_wachtende_av1_schrijver_weigert_nieuw_schema(tmp_path, monkeypatch) -> None:
    from concurrent.futures import ThreadPoolExecutor
    from contextlib import closing
    from threading import Event

    from app.db import SchemaTooNew

    instance = replace(
        settings, config_root=tmp_path / "encode", pipeline_root=tmp_path,
        backup_root=tmp_path / "backups", database_path=tmp_path / "encode" / "app.db",
    )
    db = Database(instance)
    db.initialize()
    db.execute("CREATE TABLE proef (id INTEGER)")
    gestart, gelezen = Event(), Event()
    echte_toets = db.check_schema

    def toets():
        result = echte_toets()
        gelezen.set()
        return result

    def schrijf():
        gestart.set()
        with db.connection() as conn:
            conn.execute("INSERT INTO proef VALUES (1)")
            conn.commit()

    monkeypatch.setattr(db, "check_schema", toets)
    with ThreadPoolExecutor(max_workers=1) as pool:
        with db.startup_lock(), closing(sqlite3.connect(db.path)) as migratie:
            migratie.execute("BEGIN IMMEDIATE")
            migratie.execute("INSERT INTO schema_versie VALUES (999, 'proef', 'proef')")
            toekomst = pool.submit(schrijf)
            assert gestart.wait(2)
            gelezen.wait(0.2)
            assert not toekomst.done()
            migratie.commit()
        with pytest.raises(SchemaTooNew):
            toekomst.result(timeout=5)
    with closing(sqlite3.connect(db.path)) as conn:
        assert conn.execute("SELECT count(*) FROM proef").fetchone() == (0,)
