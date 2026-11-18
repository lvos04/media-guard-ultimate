"""SQLite-back-ups met los hashmanifest en herstelcontrole op een tijdelijke kopie."""

from __future__ import annotations

import hashlib
import os
import shutil
import sqlite3
import tempfile
from contextlib import ExitStack, closing
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from app.config import Settings
from app.db import Database


class BackupError(RuntimeError):
    """Een back-up is niet aantoonbaar compleet of herstelbaar."""


def create_backup(database: Database, settings: Settings) -> dict[str, Any]:
    with ExitStack() as work:
        with database.startup_lock():
            database.require_writable()
            original = database.path.stat()
            target = settings.backup_root / (
                "app-" + datetime.now(UTC).strftime("%Y%m%dT%H%M%S%fZ") + ".db"
            )
            stage = Path(work.enter_context(tempfile.TemporaryDirectory(dir=settings.backup_root)))
            copy = stage / target.name
            with database.connection(write=False) as source, closing(sqlite3.connect(copy)) as dest:
                source.backup(dest)
        copy.chmod(0o640)
        digest = hashlib.sha256(copy.read_bytes()).hexdigest()
        manifest = target.with_suffix(target.suffix + ".sha256")
        staged_manifest = stage / manifest.name
        staged_manifest.write_text(f"{digest}  {target.name}\n", encoding="ascii")
        staged_manifest.chmod(0o640)
        with database.connection() as conn, ExitStack() as incomplete:
            if not os.path.samestat(original, database.path.stat()):
                raise BackupError("De live database is tijdens de back-up vervangen")
            if target.exists() or manifest.exists():
                raise BackupError("Een back-up met deze naam bestaat al")
            cursor = conn.execute(
                "INSERT INTO backup_status(pad,grootte,sha256,gemaakt_op) VALUES (?,?,?,?)",
                (str(target), copy.stat().st_size, digest, datetime.now(UTC).isoformat()),
            )
            for source_path, final_path in ((copy, target), (staged_manifest, manifest)):
                source_path.rename(final_path)
                incomplete.callback(final_path.unlink, missing_ok=True)
            conn.commit()
            incomplete.pop_all()
        return {"id": cursor.lastrowid, "pad": str(target), "sha256": digest}


def verify_backup(database: Database, settings: Settings, backup_id: int) -> dict[str, Any]:
    with database.startup_lock():
        database.require_writable()
        original = database.path.stat()
        row = database.fetch_one("SELECT * FROM backup_status WHERE id=?", (backup_id,))
        if row is None:
            raise BackupError("Back-up bestaat niet")
        path = Path(str(row["pad"]))
        manifest = path.with_suffix(path.suffix + ".sha256")
        expected_line = f"{row['sha256']}  {path.name}"
        if not path.is_file() or not manifest.is_file():
            raise BackupError("Back-up of hashmanifest ontbreekt")
        if manifest.read_text(encoding="ascii").strip() != expected_line:
            raise BackupError("Hashmanifest wijkt af")
        if hashlib.sha256(path.read_bytes()).hexdigest() != row["sha256"]:
            raise BackupError("SHA-256 van de back-up wijkt af")
        with tempfile.TemporaryDirectory(dir=settings.work_root, prefix="herstelcontrole-") as name:
            restored = Path(name) / "hersteld.db"
            shutil.copy2(path, restored)
            with closing(sqlite3.connect(restored)) as connection:
                check = str(connection.execute("PRAGMA integrity_check").fetchone()[0])
                connection.execute("SELECT COUNT(*) FROM schema_versie").fetchone()
        if check != "ok":
            raise BackupError(f"SQLite-integriteitscontrole: {check}")
        checked = datetime.now(UTC).isoformat()
    with database.connection() as conn, conn:
        if not os.path.samestat(original, database.path.stat()):
            raise BackupError("De live database is tijdens de herstelcontrole vervangen")
        updated = conn.execute(
            "UPDATE backup_status SET gecontroleerd_op=? WHERE id=? AND pad=? AND sha256=?",
            (checked, backup_id, str(path), row["sha256"]),
        )
        if not updated.rowcount:
            raise BackupError("De gecontroleerde back-up staat niet meer in de catalogus")
    return {"id": backup_id, "herstelbaar": True, "gecontroleerd_op": checked}


def enforce_retention(database: Database, keep: int = 8) -> int:
    query = "SELECT id,pad FROM backup_status ORDER BY gemaakt_op DESC,id DESC"
    rows = database.fetch_all(query)
    removed = 0
    for row in rows[keep:]:
        with database.connection() as conn, conn:
            # Een parallelle retentie of terugzetting kan de selectie veranderd hebben.
            if row not in conn.execute(query).fetchall()[keep:]:
                continue
            path = Path(row["pad"])
            path.unlink(missing_ok=True)
            path.with_suffix(path.suffix + ".sha256").unlink(missing_ok=True)
            conn.execute("DELETE FROM backup_status WHERE id=?", (row["id"],))
            removed += 1
    return removed
