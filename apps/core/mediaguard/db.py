"""SQLite-WAL database met genummerde SQL-migraties.

Waarom SQLite en geen PostgreSQL: zie docs/DECISIONS.md (ADR-003). Eén gebruiker,
één host, eenvoudige back-up/restore en dezelfde aanpak als de bestaande AV1-engine.

Migratiebestanden: ``mediaguard/migrations/NNN_naam.sql``. Nummerreeksen per domein
staan in ``migrations/README.md`` zodat parallel werk niet botst.
"""

from __future__ import annotations

import contextlib
import hashlib
import json
import logging
import os
import sqlite3
import threading
import time
from collections.abc import Iterator, Sequence
from contextlib import contextmanager
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from mediaguard import event_keys, schema
from mediaguard.read_observation import utc_measurement

MIGRATIONS_DIR = Path(__file__).parent / "migrations"
# Zolang dit bestand bestaat (met een levend pid) werkt het proces de database bij; de
# AIO-supervisor herstart het dan niet, ook als de levenscontrole lang niet antwoordt.
MIGRATION_MARKER_GLOB = "migratie-bezig-*.json"
MIGRATION_LOCK_WAIT_S = 6 * 3600  # een ander proces migreert: zo lang geduld
MIGRATION_FAILED_FILE = "migratie-mislukt.json"  # laatste mislukte migratie (met de fout)
ANALYSIS_LIMIT = 1000
LOGGER = logging.getLogger(__name__)


def migration_marker(folder: Path, pid: int | None = None) -> Path:
    return folder / f"migratie-bezig-{pid or os.getpid()}.json"


def _pid_alive(pid: int) -> bool:
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return False
    except PermissionError:
        return True
    except OSError:
        return False
    return True


def active_migration(folder: Path, *, exclude_pid: int | None = None) -> dict[str, Any] | None:
    """Loopt er (in een ander proces) een databasemigratie? Markeringen van gestopte
    processen tellen niet (afgebroken migratie: die wordt bij de volgende start herhaald)."""
    exclude_pid = os.getpid() if exclude_pid is None else exclude_pid
    for marker in sorted(folder.glob(MIGRATION_MARKER_GLOB)):
        try:
            info = json.loads(marker.read_text(encoding="utf-8"))
            pid = int(info.get("pid") or 0)
        except (OSError, ValueError, TypeError, AttributeError):
            continue
        if pid and pid != exclude_pid and _pid_alive(pid):
            return {**info, "marker": str(marker)}
    return None


def migration_of(folder: Path, pid: int) -> dict[str, Any] | None:
    """De migratie die proces ``pid`` nu uitvoert (supervisor: niet herstarten)."""
    marker = migration_marker(folder, pid)
    try:
        info = json.loads(marker.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None
    return info if isinstance(info, dict) and int(info.get("pid") or 0) == pid else None


def failed_migration(folder: Path) -> dict[str, Any] | None:
    """De laatste migratie die met een fout stopte (wordt gewist zodra het wel lukt)."""
    try:
        info = json.loads((folder / MIGRATION_FAILED_FILE).read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None
    return info if isinstance(info, dict) else None


def stale_migrations(folder: Path) -> list[dict[str, Any]]:
    """Markeringen van processen die midden in een migratie stopten (voor de uitleg van een
    herstartlus)."""
    out = []
    for marker in sorted(folder.glob(MIGRATION_MARKER_GLOB)):
        try:
            info = json.loads(marker.read_text(encoding="utf-8"))
            pid = int(info.get("pid") or 0)
        except (OSError, ValueError, TypeError, AttributeError):
            continue
        if pid and not _pid_alive(pid):
            out.append({**info, "marker": str(marker)})
    return out


def utcnow() -> str:
    return datetime.now(UTC).isoformat(timespec="seconds")


def dumps(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, default=str)


def loads(value: str | bytes | None, default: Any = None) -> Any:
    if value is None or value == "":
        return default
    return json.loads(value)


def check_schema_versions(path: Path, *, connection: sqlite3.Connection | None = None) -> None:
    have = (schema.versions_in(connection, "schema_migrations", "version")
            if connection is not None else
            schema.applied_versions(path, "schema_migrations", "version"))
    if have - {file.stem for file in MIGRATIONS_DIR.glob("*.sql")}:
        raise schema.SchemaTooNew()


class Database:
    def __init__(self, path: Path, *, synchronous: str = "NORMAL") -> None:
        """``synchronous``: NORMAL is in WAL-modus corruptievrij en ~40× sneller per schrijf-
        actie (alleen de laatste commits kunnen bij stroomuitval verloren gaan). De
        recovery-rol (bibliotheekjournaal) gebruikt FULL."""
        self.path = path
        self.synchronous = "FULL" if synchronous.upper() == "FULL" else "NORMAL"
        self._lock = threading.RLock()
        self._local = threading.local()
        self.read_only = False

    # -- verbinding -----------------------------------------------------------------
    def _connect(self) -> sqlite3.Connection:
        self.require_writable()
        conn: sqlite3.Connection | None = getattr(self._local, "conn", None)
        if conn is None:
            self.path.parent.mkdir(parents=True, exist_ok=True)
            conn = sqlite3.connect(self.path, timeout=30, isolation_level=None,
                                   check_same_thread=False)
            conn.row_factory = sqlite3.Row
            conn.create_function("mg_utc_measurement", 1, utc_measurement, deterministic=True)
            conn.execute(f"PRAGMA synchronous={self.synchronous}")
            conn.execute("PRAGMA foreign_keys=ON")
            conn.execute("PRAGMA temp_store=MEMORY")
            conn.execute("PRAGMA busy_timeout=30000")
            self._local.conn = conn
        return conn

    def close(self) -> None:
        conn = getattr(self._local, "conn", None)
        if conn is not None:
            conn.close()
            self._local.conn = None

    def check_schema(self) -> bool:
        if not self.read_only:
            try:
                if os.environ.get(schema.AIO_RECOVERY_ENV):
                    raise schema.SchemaTooNew()
                check_schema_versions(self.path)
                from mediaguard.maintenance import preupdate

                preupdate.check_encoder_schema(schema.encoder_dir(self.path.parent))
            except schema.SchemaTooNew:
                self.read_only = True
        return self.read_only

    def require_writable(self) -> None:
        if self.check_schema():
            raise schema.SchemaTooNew()

    def startup_lock(self, *, blocking: bool = True) -> contextlib.AbstractContextManager[None]:
        return schema.startup_locks(
            self.path.parent, schema.encoder_dir(self.path.parent), blocking=blocking,
        )

    def initialize(self, *, pre_update_set: Path | None = None,
                   encoder_migrations: Path | None = None) -> Path | None:
        """Initialiseer de database en geef de werkelijk gebruikte herstelset terug."""
        if os.environ.get(schema.AIO_RECOVERY_ENV):
            self.check_schema()
            return
        folder = self.path.parent
        if not folder.is_dir():
            with self.startup_lock():
                if self.check_schema():
                    return
                folder.mkdir(parents=True, exist_ok=True)
        with self.startup_lock():
            if not self.check_schema():
                try:
                    return self._initialize(pre_update_set, encoder_migrations)
                except schema.SchemaTooNew:
                    self.read_only = True

    def _initialize(self, pre_update_set: Path | None,
                    encoder_migrations: Path | None) -> Path | None:
        from mediaguard.maintenance import preupdate

        folder = self.path.parent
        conn = self._connect()
        if conn.execute("PRAGMA journal_mode").fetchone()[0] != "wal":
            # De omschakeling mag alleen buiten een transactie. Een exclusief slot
            # op deze korte verbinding blijft na COMMIT staan tot de verbinding sluit.
            with contextlib.closing(sqlite3.connect(self.path, timeout=30)) as setup:
                setup.execute("PRAGMA locking_mode=EXCLUSIVE")
                setup.execute("BEGIN EXCLUSIVE")
                self._check_write_schema(setup)
                setup.commit()
                setup.execute("PRAGMA journal_mode=WAL")

        marker = migration_marker(folder)
        with self._migration_transaction(conn):
            for old in stale_migrations(folder):
                with contextlib.suppress(OSError):
                    Path(old["marker"]).unlink()
            # Na een containerherstart kan de afgebroken poging hetzelfde pid hebben.
            with contextlib.suppress(OSError):
                marker.unlink()
            conn.execute(
                "CREATE TABLE IF NOT EXISTS schema_migrations("
                "version TEXT PRIMARY KEY, applied_at TEXT NOT NULL)"
            )
            applied = {r["version"] for r in conn.execute("SELECT version FROM schema_migrations")}
        pending = [f for f in sorted(MIGRATIONS_DIR.glob("*.sql")) if f.stem not in applied]
        if not pending and encoder_migrations is None:
            with contextlib.suppress(OSError):
                (folder / MIGRATION_FAILED_FILE).unlink()
            return
        info = {"pid": os.getpid(), "since": utcnow(), "role": os.environ.get("MG_ROLE", ""),
                "from": max(applied) if applied else None,
                "to": pending[-1].stem if pending else None,
                "pending": len(pending)}
        with contextlib.suppress(OSError):
            marker.write_text(json.dumps(info), encoding="utf-8")
        try:
            # Bij een losse kern blijven de oude migratiestappen afzonderlijk bewaakt.
            # De eventidentiteit en haar opvolgers vormen één transactie vanaf schema 151.
            first_protected = next((f for f in pending if f.stem >= "151_"), None)
            used_set = None
            previous = max(applied, default="")
            with contextlib.ExitStack() as locks:
                supervised = pre_update_set is not None
                if applied:
                    engines = locks.enter_context(
                        preupdate.reserve_encoders(schema.encoder_dir(folder)))
                if supervised:
                    # Dezelfde schrijftransactie omvat de definitieve set en alle migraties,
                    # ook wanneer alleen de encoder achterloopt.
                    locks.enter_context(self._migration_transaction(conn))
                if applied:
                    with self._migration_transaction(conn):
                        data_version = conn.execute("PRAGMA data_version").fetchone()[0]
                        used_set = self._backup_before_migration(
                            previous if pending else None, info["to"],
                            required=True, archive=pre_update_set,
                        )
                if used_set is not None and (supervised or encoder_migrations is not None):
                    engines.origin = preupdate.record_commit(folder, used_set)
                for file in pending:
                    if applied and file == first_protected:
                        locks.enter_context(self._migration_transaction(conn))
                        # Eigen voorafgaande migraties wijzigen data_version op deze verbinding
                        # niet. Een andere SQLite-commit vergt een nieuwe poging met verse set.
                        if conn.execute("PRAGMA data_version").fetchone()[0] != data_version:
                            raise RuntimeError("de kern is na de herstelset gewijzigd")
                        previous = conn.execute(
                            "SELECT max(version) FROM schema_migrations"
                        ).fetchone()[0] or ""
                        if previous.startswith("155_") and any(
                            f.stem == "157_connector_evidence" for f in pending
                        ):
                            from mediaguard.connector.evidence import check_155_reader

                            check_155_reader(conn)
                    if file.stem == "157_connector_evidence":
                        from mediaguard.connector.evidence import freeze_existing

                        with self._migration_transaction(conn):
                            if not conn.execute("SELECT 1 FROM schema_migrations WHERE version=?",
                                                (file.stem,)).fetchone():
                                self._apply_migration(conn, file)
                                freeze_existing(conn, previous=previous)
                    else:
                        self._apply_migration(conn, file)
                if encoder_migrations is not None:
                    if used_set is None:
                        raise RuntimeError("de volledige herstelset voor de AV1-migratie ontbreekt")
                    preupdate.migrate_encoders(list(engines.encoders.values()), encoder_migrations)
                if used_set is not None and (supervised or encoder_migrations is not None):
                    engines.begin(folder, used_set, conn)
            if used_set is not None and (supervised or encoder_migrations is not None):
                preupdate.finish_commit(folder)
        except schema.SchemaTooNew:
            raise  # normale herstelweergave, geen mislukte migratie
        except BaseException as exc:
            # voor de uitleg van een herstartlus (supervisor): welke stap, welke fout
            with contextlib.suppress(OSError):
                (folder / MIGRATION_FAILED_FILE).write_text(json.dumps({
                    **info, "at": utcnow(),
                    "error": f"{type(exc).__name__}: {exc}"[:300]}), encoding="utf-8")
            raise
        else:
            with contextlib.suppress(OSError):
                (folder / MIGRATION_FAILED_FILE).unlink()
        finally:
            with contextlib.suppress(OSError):
                marker.unlink()
        return used_set

    def _check_write_schema(self, conn: sqlite3.Connection) -> None:
        try:
            check_schema_versions(self.path, connection=conn)
        except schema.SchemaTooNew:
            self.read_only = True
            raise

    @contextmanager
    def _migration_transaction(self, conn: sqlite3.Connection) -> Iterator[None]:
        if conn.in_transaction:
            self._check_write_schema(conn)
            yield
            return
        with self._lock, conn:
            _begin_immediate_waiting(conn)
            self._check_write_schema(conn)
            yield

    def _apply_migration(self, conn: sqlite3.Connection, file: Path) -> None:
        version = file.stem
        sql = file.read_text(encoding="utf-8")
        with self._migration_transaction(conn):
            # Core en recovery kunnen dezelfde migratie klaar hebben staan.
            if conn.execute("SELECT 1 FROM schema_migrations WHERE version=?",
                            (version,)).fetchone() is not None:
                return
            if version == "151_outbox_event_identity":
                conn.create_function("mg_legacy_event", 5, _legacy_event, deterministic=True)
            if version == "156_outbox_cursor_history":
                event_keys.ensure_key(event_keys.key_path(self.path.parent))
                row = conn.execute("SELECT cursor_key FROM outbox_retention WHERE singleton=1"
                                   ).fetchone()
                if row and isinstance(row[0], str) and row[0].startswith(event_keys.LEGACY_PREFIX):
                    event_keys.load_legacy(self.path.parent, row[0])
                    conn.execute("UPDATE outbox_retention SET cursor_key=? WHERE singleton=1",
                                 (event_keys.KEY_REF,))
            for statement in _split_sql(sql):
                conn.execute(statement)
            conn.execute(
                "INSERT INTO schema_migrations(version, applied_at) VALUES (?,?)",
                (version, utcnow()),
            )

    def _backup_before_migration(
        self, current: str | None, target: str | None, *, required: bool = False,
        archive: Path | None = None,
    ) -> Path | None:
        """Verifieer bij een beschermde migratie een actuele herstelset."""
        made = archive or os.environ.get("MG_PRE_UPDATE_SET")
        from mediaguard import __version__
        from mediaguard.maintenance import preupdate

        if not required and made and preupdate._read_meta(Path(made)) is not None:
            return Path(made)
        try:
            if required and made:
                path = Path(made)
                current_turn = (archive is not None
                                or preupdate.claim_snapshot(self.path.parent, path))
                if current_turn and preupdate.matches_sources(path, self.path, current, target):
                    return path
            encoder = schema.encoder_dir(self.path.parent)
            return preupdate.make_set(
                self.path.parent / "backups" / preupdate.FOLDER, self.path,
                encode_dir=encoder if required and encoder.is_dir() else None,
                from_schema=current, to_schema=target,
                from_version=preupdate._previous_version(self.path), to_version=__version__,
                reason="update" if required else "migratie", preserve=Path(made) if made else None,
            )
        except preupdate.SetError as exc:
            if exc.disk and not required:
                LOGGER.warning("back-up vóór migratie mislukt: %s", exc)
                return
            raise RuntimeError(f"back-up vóór migratie mislukt: {exc}") from exc
        except (OSError, sqlite3.Error) as exc:
            raise RuntimeError(f"back-up vóór migratie mislukt: {exc}") from exc

    def maintenance(self, *, vacuum: bool = False) -> None:
        """Dagelijks: statistieken bijwerken en de WAL inkorten; wekelijks ook VACUUM."""
        with self.startup_lock(), self._lock:
            conn = self._connect()
            # ANALYZE houdt het schrijfslot vast: per index hooguit ~1000 rijen bekijken, zodat
            # het bij een grote bibliotheek een fractie van een seconde duurt (rapport 31)
            conn.execute(f"PRAGMA analysis_limit={ANALYSIS_LIMIT}")
            conn.execute("PRAGMA optimize")
            conn.execute("PRAGMA wal_checkpoint(TRUNCATE)")
            if vacuum:
                conn.execute("VACUUM")

    def free_ratio(self) -> float:
        """Aandeel lege pagina's (na opruimen); bepaalt of VACUUM de moeite waard is."""
        row = self._connect().execute(
            "SELECT (SELECT freelist_count FROM pragma_freelist_count()) * 1.0 / "
            "MAX(1, (SELECT page_count FROM pragma_page_count()))").fetchone()
        return float(row[0] or 0) if row else 0.0

    # -- helpers --------------------------------------------------------------------
    @contextmanager
    def _write_transaction(self, *, blocking: bool = True) -> Iterator[sqlite3.Connection]:
        with self.startup_lock(blocking=blocking):
            if not self._lock.acquire(blocking=blocking):
                raise BlockingIOError
            try:
                conn = self._connect()
                if conn.in_transaction:
                    yield conn
                    return
                if not blocking:
                    conn.execute("PRAGMA busy_timeout=0")
                try:
                    conn.execute("BEGIN IMMEDIATE")
                finally:
                    if not blocking:
                        conn.execute("PRAGMA busy_timeout=30000")
                try:
                    # Ook een migratie die alleen het SQLite-slot gebruikt kan tijdens
                    # het wachten op BEGIN IMMEDIATE zijn vastgelegd.
                    self._check_write_schema(conn)
                    yield conn
                    conn.execute("COMMIT")
                except BaseException:
                    if conn.in_transaction:
                        conn.execute("ROLLBACK")
                    raise
            finally:
                self._lock.release()

    def transaction(self) -> contextlib.AbstractContextManager[sqlite3.Connection]:
        return self._write_transaction()

    @contextmanager
    def durable_transaction(self) -> Iterator[sqlite3.Connection]:
        """Leg het journaal op schijf vast vóór een afzonderlijke bestandsbewerking."""
        with self.startup_lock(), self._lock:
            conn = self._connect()
            if conn.in_transaction:
                raise RuntimeError("Een bestandsbewerking vereist een eigen duurzame boeking")
            previous = conn.execute("PRAGMA synchronous").fetchone()[0]
            if previous < 2:  # SQLite FULL
                conn.execute("PRAGMA synchronous=FULL")
            try:
                with self.transaction() as transaction:
                    yield transaction
            finally:
                if previous < 2:
                    conn.execute(f"PRAGMA synchronous={int(previous)}")

    def execute(self, sql: str, params: Sequence[Any] = ()) -> int:
        if sql.lstrip().upper().startswith(("PRAGMA", "VACUUM")):
            # SQLite staat sommige instellingen en onderhoud alleen buiten een transactie toe.
            with self.startup_lock(), self._lock:
                return self._connect().execute(sql, params).rowcount
        with self._write_transaction() as conn:
            return conn.execute(sql, params).rowcount

    def try_execute(self, sql: str, params: Sequence[Any] = ()) -> bool:
        """Schrijf alleen als de schrijfvergrendeling nu vrij is (nooit wachten). Voor
        logregels in tijdkritische paden (fork-hooks met 3 s time-out)."""
        try:
            with self._write_transaction(blocking=False) as conn:
                conn.execute(sql, params)
            return True
        except (BlockingIOError, sqlite3.OperationalError):
            return False  # database bezet door een ander proces: later opnieuw

    def execute_insert(self, sql: str, params: Sequence[Any] = ()) -> int:
        with self._write_transaction() as conn:
            cur = conn.execute(sql, params)
            return int(cur.lastrowid or 0)

    def fetch_one(self, sql: str, params: Sequence[Any] = ()) -> sqlite3.Row | None:
        cur = self._connect().execute(sql, params)
        row: sqlite3.Row | None = cur.fetchone()
        return row

    def fetch_all(self, sql: str, params: Sequence[Any] = ()) -> list[sqlite3.Row]:
        return list(self._connect().execute(sql, params).fetchall())

    def fetch_dicts(self, sql: str, params: Sequence[Any] = ()) -> list[dict[str, Any]]:
        return [dict(r) for r in self.fetch_all(sql, params)]

    def iter_dicts(self, sql: str, params: Sequence[Any] = ()) -> Iterator[dict[str, Any]]:
        with contextlib.closing(self._connect().execute(sql, params)) as rows:
            for row in rows:
                yield dict(row)

    def backup_to(self, target: Path) -> None:
        """Consistente online back-up via de SQLite backup-API (WAL-veilig).

        Via een eigen leesverbinding en in één stap (= één leestransactie, dus een
        consistente momentopname). Het startslot bewaakt de schemaversie tijdens de kopie."""
        with self.startup_lock():
            self.require_writable()
            self._backup_to(target)

    def _backup_to(self, target: Path) -> None:
        target.parent.mkdir(parents=True, exist_ok=True)
        src = sqlite3.connect(f"file:{self.path}?mode=ro", uri=True, timeout=30)
        dest = sqlite3.connect(target)
        try:
            src.backup(dest)
        finally:
            dest.close()
            src.close()


def _legacy_event(*fields: str | int | None) -> bytes:
    # Het aflevervlaggetje kan wijzigen en hoort daarom niet bij de identiteit.
    return b"\x00" + hashlib.sha256(dumps(fields).encode("utf-8")).digest()[:16]


def _begin_immediate_waiting(conn: sqlite3.Connection,
                             max_wait_s: float = MIGRATION_LOCK_WAIT_S) -> None:
    """BEGIN IMMEDIATE, ook als een ander proces lang bezig is (bv. een zware migratie in
    core terwijl recovery start). Gewoon geduld, geen fout: een fout gaf een herstartlus."""
    deadline = time.monotonic() + max_wait_s
    while True:
        try:
            conn.execute("BEGIN IMMEDIATE")
            return
        except sqlite3.OperationalError as exc:
            if "locked" not in str(exc) or time.monotonic() > deadline:
                raise
            LOGGER.info("database bezet (andere migratie?); wacht")


def _split_sql(sql: str) -> list[str]:
    """Splits op ';' aan regeleinde; triggers met BEGIN..END blijven heel."""
    statements: list[str] = []
    buffer: list[str] = []
    depth = 0
    for line in sql.splitlines():
        stripped = line.strip()
        if stripped.startswith("--") and not buffer:
            continue
        buffer.append(line)
        upper = stripped.upper()
        if upper.endswith("BEGIN") or " BEGIN" in upper and upper.startswith("CREATE TRIGGER"):
            depth += 1
        if upper in ("END;", "END"):
            depth = max(0, depth - 1)
        if stripped.endswith(";") and depth == 0:
            stmt = "\n".join(buffer).strip()
            if stmt.rstrip(";").strip():
                statements.append(stmt)
            buffer = []
    rest = "\n".join(buffer).strip()
    if rest.rstrip(";").strip():
        statements.append(rest)
    return statements
