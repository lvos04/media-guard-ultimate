"""Alleen-lezen voorcontrole voor SQLite-start en onderhoud."""

from __future__ import annotations

import fcntl
import os
import sqlite3
from collections.abc import Iterator
from contextlib import closing, contextmanager
from pathlib import Path

EXIT_CODE = 79
REASON = "schema_nieuwer"
MESSAGE = ("database is van een nieuwere MediaGuard-versie; zet de bijpassende versie "
           "of de set van vóór de update terug")
AIO_RECOVERY_ENV = "MG_AIO_SCHEMA_RECOVERY"


class SchemaTooNew(RuntimeError):
    def __init__(self) -> None:
        super().__init__(MESSAGE)


@contextmanager
def startup_lock(folder: Path) -> Iterator[None]:
    """Vergrendel de datamap zonder een nieuw bestand bij een geweigerde start."""
    fd = os.open(folder, os.O_RDONLY | os.O_DIRECTORY)
    try:
        fcntl.flock(fd, fcntl.LOCK_EX)
        yield
    finally:
        os.close(fd)


def applied_versions(path: Path, table: str, column: str, *,
                     timeout: float = 30) -> set[str | int]:
    """Een verse database of een database zonder migratietabel heeft nul versies."""
    try:
        path.stat()
    except FileNotFoundError:
        return set()

    uri = f"{path.resolve().as_uri()}?mode=ro"
    with closing(sqlite3.connect(uri, uri=True, timeout=timeout)) as db:
        exists = db.execute(
            "SELECT 1 FROM sqlite_master WHERE type='table' AND name=?", (table,),
        ).fetchone()
        if not exists:
            return set()
        return {row[0] for row in db.execute(f"SELECT {column} FROM {table}")}  # noqa: S608
