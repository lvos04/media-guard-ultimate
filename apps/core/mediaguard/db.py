"""SQLite-WAL database met genummerde SQL-migraties.

Waarom SQLite en geen PostgreSQL: zie docs/DECISIONS.md (ADR-003). Eén gebruiker,
één host, eenvoudige back-up/restore en dezelfde aanpak als de bestaande AV1-engine.

Migratiebestanden: ``mediaguard/migrations/NNN_naam.sql``. Nummerreeksen per domein
staan in ``migrations/README.md`` zodat parallel werk niet botst.
"""

from __future__ import annotations

import contextlib
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


class Database:
    def __init__(self, path: Path, *, synchronous: str = "NORMAL") -> None:
        """``synchronous``: NORMAL is in WAL-modus corruptievrij en ~40× sneller per schrijf-
        actie (alleen de laatste commits kunnen bij stroomuitval verloren gaan). De
        recovery-rol (bibliotheekjournaal) gebruikt FULL."""
        self.path = path
        self.synchronous = "FULL" if synchronous.upper() == "FULL" else "NORMAL"
        self._lock = threading.RLock()
        self._local = threading.local()

    # -- verbinding -----------------------------------------------------------------
    def _connect(self) -> sqlite3.Connection:
        conn: sqlite3.Connection | None = getattr(self._local, "conn", None)
        if conn is None:
            self.path.parent.mkdir(parents=True, exist_ok=True)
            conn = sqlite3.connect(self.path, timeout=30, isolation_level=None,
                                   check_same_thread=False)
            conn.row_factory = sqlite3.Row
            conn.execute("PRAGMA journal_mode=WAL")
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

    def initialize(self) -> None:
        folder = self.path.parent
        for old in stale_migrations(folder):  # afgebroken eerdere poging
            with contextlib.suppress(OSError):
                Path(old["marker"]).unlink()
        marker = migration_marker(folder)
        # In een container krijgt een dienst bij elke start vaak hetzelfde pid: een markering
        # met ons pid is dan van een eerder (afgebroken) proces, niet van ons. Blijft hij
        # staan, dan denkt de supervisor dat we eeuwig migreren en herstart hij nooit meer.
        with contextlib.suppress(OSError):
            marker.unlink()
        conn = self._connect()
        conn.execute(
            "CREATE TABLE IF NOT EXISTS schema_migrations("
            "version TEXT PRIMARY KEY, applied_at TEXT NOT NULL)"
        )
        applied = {r["version"] for r in conn.execute("SELECT version FROM schema_migrations")}
        pending = [f for f in sorted(MIGRATIONS_DIR.glob("*.sql")) if f.stem not in applied]
        if not pending:
            with contextlib.suppress(OSError):
                (folder / MIGRATION_FAILED_FILE).unlink()
            return
        other = active_migration(folder)
        info = {"pid": os.getpid(), "since": utcnow(), "role": os.environ.get("MG_ROLE", ""),
                "from": max(applied) if applied else None, "to": pending[-1].stem,
                "pending": len(pending)}
        with contextlib.suppress(OSError):
            marker.write_text(json.dumps(info), encoding="utf-8")
        try:
            if applied and other is None:
                # automatische back-up vóór een update met databasewijzigingen (migreert
                # een ander proces al, dan heeft dat hem gemaakt: geen halfbijgewerkte kopie)
                self._backup_before_migration(max(applied), pending[-1].stem)
            for file in pending:
                self._apply_migration(conn, file)
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

    def _apply_migration(self, conn: sqlite3.Connection, file: Path) -> None:
        version = file.stem
        sql = file.read_text(encoding="utf-8")
        with self._lock:
            _begin_immediate_waiting(conn)
            try:
                # core en recovery starten allebei: wat het andere proces intussen al
                # toepaste, niet nog eens doen (anders 'table already exists' → herstartlus)
                if conn.execute("SELECT 1 FROM schema_migrations WHERE version=?",
                                (version,)).fetchone() is not None:
                    conn.execute("COMMIT")
                    return
                for statement in _split_sql(sql):
                    conn.execute(statement)
                conn.execute(
                    "INSERT INTO schema_migrations(version, applied_at) VALUES (?,?)",
                    (version, utcnow()),
                )
                conn.execute("COMMIT")
            except Exception:
                conn.execute("ROLLBACK")
                raise

    def _backup_before_migration(self, current: str, target: str) -> None:
        """Volledige set vóór de update (map met kopie + metadata). In de AIO maakt de
        supervisor die set al vóór de start van alle diensten, inclusief de database van de
        AV1-engine (``MG_PRE_UPDATE_SET``); dan is een tweede kopie hier overbodig."""
        made = os.environ.get("MG_PRE_UPDATE_SET")
        if made and (Path(made) / "meta.json").is_file():
            return
        from mediaguard import __version__
        from mediaguard.maintenance import preupdate

        try:
            preupdate.make_set(self.path.parent / "backups" / preupdate.FOLDER, self.path,
                               encode_dir=None, from_schema=current, to_schema=target,
                               from_version=preupdate._previous_version(self.path),
                               to_version=__version__)
        except preupdate.SetError as exc:
            if exc.disk:  # geen ruimte/rechten: migratie niet blokkeren, wel melden
                LOGGER.warning("back-up vóór migratie mislukt: %s", exc)
                return
            raise RuntimeError(f"back-up vóór migratie is niet intact: {exc}") from exc

    def maintenance(self, *, vacuum: bool = False) -> None:
        """Dagelijks: statistieken bijwerken en de WAL inkorten; wekelijks ook VACUUM."""
        conn = self._connect()
        with self._lock:
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
    def transaction(self) -> Iterator[sqlite3.Connection]:
        conn = self._connect()
        with self._lock:
            conn.execute("BEGIN IMMEDIATE")
            try:
                yield conn
                conn.execute("COMMIT")
            except BaseException:
                conn.execute("ROLLBACK")
                raise

    def execute(self, sql: str, params: Sequence[Any] = ()) -> int:
        with self._lock:
            cur = self._connect().execute(sql, params)
            return cur.rowcount

    def try_execute(self, sql: str, params: Sequence[Any] = ()) -> bool:
        """Schrijf alleen als de schrijfvergrendeling nu vrij is (nooit wachten). Voor
        logregels in tijdkritische paden (fork-hooks met 3 s time-out)."""
        if not self._lock.acquire(blocking=False):
            return False
        try:
            self._connect().execute(sql, params)
            return True
        except sqlite3.OperationalError:
            return False  # database bezet door een ander proces: later opnieuw
        finally:
            self._lock.release()

    def execute_insert(self, sql: str, params: Sequence[Any] = ()) -> int:
        with self._lock:
            cur = self._connect().execute(sql, params)
            return int(cur.lastrowid or 0)

    def fetch_one(self, sql: str, params: Sequence[Any] = ()) -> sqlite3.Row | None:
        cur = self._connect().execute(sql, params)
        row: sqlite3.Row | None = cur.fetchone()
        return row

    def fetch_all(self, sql: str, params: Sequence[Any] = ()) -> list[sqlite3.Row]:
        return list(self._connect().execute(sql, params).fetchall())

    def fetch_dicts(self, sql: str, params: Sequence[Any] = ()) -> list[dict[str, Any]]:
        return [dict(r) for r in self.fetch_all(sql, params)]

    def backup_to(self, target: Path) -> None:
        """Consistente online back-up via de SQLite backup-API (WAL-veilig).

        Via een eigen leesverbinding en in één stap (= één leestransactie, dus een
        consistente momentopname): in WAL-modus blokkeert lezen geen schrijvers, dus core,
        recovery en de hooks gaan tijdens een back-up van een grote database gewoon door."""
        target.parent.mkdir(parents=True, exist_ok=True)
        src = sqlite3.connect(f"file:{self.path}?mode=ro", uri=True, timeout=30)
        dest = sqlite3.connect(target)
        try:
            src.backup(dest)
        finally:
            dest.close()
            src.close()


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
