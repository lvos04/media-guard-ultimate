"""Alleen-lezen voorcontrole voor SQLite-start en onderhoud."""

from __future__ import annotations

import fcntl
import os
import sqlite3
import threading
from collections.abc import Iterator
from contextlib import ExitStack, closing, contextmanager, suppress
from pathlib import Path

EXIT_CODE = 79
REASON = "schema_nieuwer"
MESSAGE = ("database is van een nieuwere MediaGuard-versie; zet de bijpassende versie "
           "of de set van vóór de update terug")
AIO_RECOVERY_ENV = "MG_AIO_SCHEMA_RECOVERY"
_locked = threading.local()


class SchemaTooNew(RuntimeError):
    def __init__(self) -> None:
        super().__init__(MESSAGE)


@contextmanager
def startup_lock(folder: Path) -> Iterator[None]:
    """Vergrendel de datamap zonder een nieuw bestand bij een geweigerde start."""
    folder = folder.resolve()
    folders: set[Path] = getattr(_locked, "folders", set())
    _locked.folders = folders
    if folder in folders:
        yield
        return
    fd = os.open(folder, os.O_RDONLY | os.O_DIRECTORY)
    try:
        fcntl.flock(fd, fcntl.LOCK_EX)
        folders.add(folder)
        try:
            yield
        finally:
            folders.remove(folder)
    finally:
        os.close(fd)


@contextmanager
def startup_locks(core: Path, encode: Path) -> Iterator[None]:
    """Alle ingangen nemen de bestaande mappen in dezelfde volgorde."""
    with ExitStack() as locks:
        for folder in (core, encode):
            if folder.is_dir():
                locks.enter_context(startup_lock(folder))
        yield


def encoder_dir(core: Path) -> Path:
    root = core.parent if core.name == "core" else core
    return root / "encode"


def applied_versions(path: Path, table: str, column: str, *,
                     timeout: float = 30) -> set[str | int]:
    """Een verse database of een database zonder migratietabel heeft nul versies."""
    try:
        before = _generation(path)
    except FileNotFoundError:
        return set()

    uri = f"{path.resolve().as_uri()}?mode=ro"
    sidecars = tuple(path.with_name(path.name + tail) for tail in ("-wal", "-shm", "-journal"))
    if not any(p.exists() for p in sidecars):
        # Een schoon hoofdbestand kan zonder SQLite-hulpbestanden gelezen worden.
        # Bij een gelijktijdige wijziging of een aanwezige WAL is SQLite-locking nodig.
        with suppress(OSError, sqlite3.DatabaseError):
            with closing(sqlite3.connect(uri + "&immutable=1", uri=True, timeout=timeout)) as db:
                versions = versions_in(db, table, column)
            if not any(p.exists() for p in sidecars) and _generation(path) == before:
                return versions
    with closing(sqlite3.connect(uri, uri=True, timeout=timeout)) as db:
        return versions_in(db, table, column)


def _generation(path: Path) -> tuple[int, int, int, int, int]:
    info = path.stat()
    return info.st_dev, info.st_ino, info.st_size, info.st_mtime_ns, info.st_ctime_ns


def versions_in(db: sqlite3.Connection, table: str, column: str) -> set[str | int]:
    exists = db.execute(
        "SELECT 1 FROM sqlite_master WHERE type='table' AND name=?", (table,),
    ).fetchone()
    if not exists:
        return set()
    return {row[0] for row in db.execute(f"SELECT {column} FROM {table}")}  # noqa: S608
