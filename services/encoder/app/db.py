"""Kleine SQLite-laag met genummerde, herhaalbare migraties."""

from __future__ import annotations

import fcntl
import logging
import os
import re
import sqlite3
import threading
from collections.abc import Callable, Iterator, Sequence
from contextlib import ExitStack, closing, contextmanager, nullcontext, suppress
from datetime import UTC, datetime
from importlib.util import find_spec
from pathlib import Path
from typing import Any

from app.config import Settings

SCHEMA_REASON = "schema_nieuwer"
SCHEMA_MESSAGE = ("database is van een nieuwere MediaGuard-versie; zet de bijpassende versie "
                  "of de set van vóór de update terug")
SCHEMA_EXIT = 79

LOGGER = logging.getLogger(__name__)
MIGRATION_NAME = re.compile(r"^(?P<version>\d{3})_[a-z0-9_]+\.sql$")
_locked = threading.local()


class MigrationError(RuntimeError):
    """Een migratiebestand is ongeldig of kon niet atomisch worden toegepast."""


class SchemaTooNew(MigrationError):
    def __init__(self) -> None:
        super().__init__(SCHEMA_MESSAGE)


@contextmanager
def _folder_lock(folder: Path) -> Iterator[None]:
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


def _core_migrations() -> Path:
    if configured := os.environ.get("MG_SCHEMA_CORE_MIGRATIONS"):
        return Path(configured)
    core = find_spec("mediaguard")
    if core is not None and core.origin is not None:
        return Path(core.origin).parent / "migrations"
    return Path(__file__).resolve().parents[3] / "apps" / "core" / "mediaguard" / "migrations"


def _applied_versions(path: Path, table: str, column: str) -> set[int | str]:
    try:
        before = _generation(path)
    except FileNotFoundError:
        return set()
    uri = f"{path.resolve().as_uri()}?mode=ro"
    sidecars = tuple(path.with_name(path.name + tail) for tail in ("-wal", "-shm", "-journal"))
    if not any(p.exists() for p in sidecars):
        # Bij een ongewijzigd hoofdbestand zonder WAL zijn hulpbestanden overbodig.
        with suppress(OSError, sqlite3.DatabaseError):
            with closing(sqlite3.connect(uri + "&immutable=1", uri=True, timeout=30)) as db:
                versions = _versions_in(db, table, column)
            if not any(p.exists() for p in sidecars) and _generation(path) == before:
                return versions
    with closing(sqlite3.connect(uri, uri=True, timeout=30)) as db:
        return _versions_in(db, table, column)


def _generation(path: Path) -> tuple[int, int, int, int, int]:
    info = path.stat()
    return info.st_dev, info.st_ino, info.st_size, info.st_mtime_ns, info.st_ctime_ns


def _versions_in(db: sqlite3.Connection, table: str, column: str) -> set[int | str]:
    exists = db.execute(
        "SELECT 1 FROM sqlite_master WHERE type='table' AND name=?", (table,),
    ).fetchone()
    if not exists:
        return set()
    return {row[0] for row in db.execute(f"SELECT {column} FROM {table}")}  # noqa: S608


class Database:
    """Eén SQLite-database; iedere verbinding zet dezelfde veiligheids-PRAGMA's."""

    def __init__(self, settings: Settings, migrations_dir: Path | None = None) -> None:
        self.settings = settings
        self.path = settings.database_path
        self.migrations_dir = migrations_dir or Path(__file__).with_name("migrations")
        self.read_only = False

    def _core_database(self) -> Path:
        return self.settings.config_root.parent / "core" / "mediaguard.db"

    def connect(self) -> sqlite3.Connection:
        self.require_writable()
        connection = sqlite3.connect(self.path, timeout=30, check_same_thread=False)
        connection.row_factory = sqlite3.Row
        connection.execute("PRAGMA foreign_keys=ON")
        connection.execute("PRAGMA busy_timeout=30000")
        return connection

    @contextmanager
    def connection(self, *, write: bool = True) -> Iterator[sqlite3.Connection]:
        """Eén gecontroleerde schrijftransactie, of een alleen-lezende verbinding."""
        lock = self.startup_lock() if write else nullcontext()
        with lock, closing(self.connect()) as conn:
            if write:
                conn.execute("BEGIN IMMEDIATE")
                if self.check_schema(conn):
                    raise SchemaTooNew()
                # executescript mag de getoetste transactie niet impliciet committen.
                conn.autocommit = False
            else:
                conn.execute("PRAGMA query_only=ON")
            yield conn

    @contextmanager
    def exclusive_connection(self) -> Iterator[sqlite3.Connection]:
        """Behoud het SQLite-slot bij bewerkingen die buiten een transactie moeten."""
        with self.startup_lock(), closing(self.connect()) as conn:
            conn.execute("PRAGMA locking_mode=EXCLUSIVE")
            conn.execute("BEGIN EXCLUSIVE")
            if self.check_schema(conn):
                raise SchemaTooNew()
            conn.commit()
            yield conn  # SQLite laat het exclusieve slot pas bij close() los.

    @contextmanager
    def startup_lock(self) -> Iterator[None]:
        # Zelfde volgorde en mappen als kern en AIO-bewaker.
        with ExitStack() as locks:
            for folder in (self._core_database().parent, self.path.parent):
                if folder.is_dir():
                    locks.enter_context(_folder_lock(folder))
            yield

    def check_schema(self, connection: sqlite3.Connection | None = None) -> bool:
        if self.read_only:
            return True
        have = (_versions_in(connection, "schema_versie", "versie") if connection is not None
                else _applied_versions(self.path, "schema_versie", "versie"))
        core = _applied_versions(self._core_database(), "schema_migrations", "version")
        if (have - {version for version, _ in self.migration_files()}
                or core - {file.stem for file in _core_migrations().glob("*.sql")}):
            self.read_only = True
        return self.read_only

    def require_writable(self) -> None:
        if self.check_schema():
            raise SchemaTooNew()

    def initialize(self, *, prepare: Callable[[], None] | None = None) -> None:
        if not self.path.parent.is_dir():
            with self.startup_lock():
                if self.check_schema():
                    return
                self.path.parent.mkdir(parents=True, exist_ok=True)
        with self.startup_lock():
            if self.check_schema():
                return
            if prepare is not None:
                prepare()
            try:
                self._initialize()
            except SchemaTooNew:
                self.read_only = True

    def _initialize(self) -> None:
        with closing(self.connect()) as connection:
            mode = connection.execute("PRAGMA journal_mode").fetchone()[0]
        if mode != "wal":
            with self.exclusive_connection() as connection:
                connection.execute("PRAGMA journal_mode=WAL")
        with self.connection() as connection:
            connection.execute(
                "CREATE TABLE IF NOT EXISTS schema_versie ("
                "versie INTEGER PRIMARY KEY, naam TEXT NOT NULL, toegepast_op TEXT NOT NULL)"
            )
            connection.commit()
        self._apply_migrations()

    def migration_files(self) -> list[tuple[int, Path]]:
        if not self.migrations_dir.is_dir():
            raise MigrationError("Migratiemap van de AV1-engine ontbreekt")
        migrations: list[tuple[int, Path]] = []
        for path in sorted(self.migrations_dir.glob("*.sql")):
            match = MIGRATION_NAME.match(path.name)
            if match is None:
                raise MigrationError(f"Ongeldige migratienaam: {path.name}")
            migrations.append((int(match.group("version")), path))
        versions = [version for version, _ in migrations]
        if len(versions) != len(set(versions)):
            raise MigrationError("Dubbele migratieversie gevonden")
        return migrations

    def applied_versions(self) -> set[int]:
        with closing(self.connect()) as connection:
            rows = connection.execute("SELECT versie FROM schema_versie").fetchall()
        return {int(row["versie"]) for row in rows}

    def _backup_before_migration(self) -> None:
        if not self.path.exists() or self.path.stat().st_size == 0:
            return
        backup_dir = self.settings.backup_root / "voor-migratie"
        backup_dir.mkdir(parents=True, exist_ok=True)
        timestamp = datetime.now(UTC).strftime("%Y%m%dT%H%M%SZ")
        target = backup_dir / f"app-{timestamp}.db"
        # SQLite backup voorkomt een inconsistente kopie terwijl WAL actief is.
        with self.connection(write=False) as source, sqlite3.connect(target) as destination:
            source.backup(destination)
        target.chmod(0o640)

    def apply_migrations(self) -> None:
        with self.startup_lock():
            self.require_writable()
            self._apply_migrations()

    def _apply_migrations(self) -> None:
        files = self.migration_files()
        with self.connection() as connection:
            applied = _versions_in(connection, "schema_versie", "versie")
            pending = [(v, path) for v, path in files if v not in applied]
            if pending:
                self._backup_before_migration()
        for version, path in pending:
            sql = path.read_text(encoding="utf-8")
            try:
                with self.connection() as connection:
                    if connection.execute("SELECT 1 FROM schema_versie WHERE versie=?",
                                          (version,)).fetchone():
                        continue
                    connection.executescript(sql)
                    connection.execute(
                        "INSERT INTO schema_versie(versie, naam, toegepast_op) VALUES (?,?,?)",
                        (version, path.name, datetime.now(UTC).isoformat()),
                    )
                    connection.commit()
            except sqlite3.Error as exc:
                raise MigrationError(f"Migratie {path.name} mislukt: {exc}") from exc
            LOGGER.info("Migratie %s toegepast", path.name)

    def execute(self, sql: str, parameters: Sequence[Any] = ()) -> int:
        if sql.strip().upper() == "VACUUM":
            with self.exclusive_connection() as connection:
                return connection.execute(sql, parameters).rowcount
        with self.connection() as connection:
            cursor = connection.execute(sql, parameters)
            connection.commit()
            return cursor.rowcount

    def execute_insert(self, sql: str, parameters: Sequence[Any] = ()) -> int:
        with self.connection() as connection:
            cursor = connection.execute(sql, parameters)
            connection.commit()
            if cursor.lastrowid is None:
                raise sqlite3.DatabaseError("INSERT leverde geen id op")
            return int(cursor.lastrowid)

    def fetch_one(self, sql: str, parameters: Sequence[Any] = ()) -> sqlite3.Row | None:
        with closing(self.connect()) as connection:
            row: sqlite3.Row | None = connection.execute(sql, parameters).fetchone()
            return row

    def fetch_all(self, sql: str, parameters: Sequence[Any] = ()) -> list[sqlite3.Row]:
        with closing(self.connect()) as connection:
            return list(connection.execute(sql, parameters).fetchall())

    def reachable(self) -> bool:
        try:
            with closing(self.connect()) as connection:
                return int(connection.execute("SELECT 1").fetchone()[0]) == 1
        except sqlite3.Error:
            return False
