"""Begrensde achtergrondtaken voor wekenlang draaien zonder stille vervuiling."""

from __future__ import annotations

import asyncio
import hashlib
import logging
import shutil
import sqlite3
from contextlib import suppress
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

from app.backups import create_backup, enforce_retention
from app.config import DEFAULT_CACHE_PAUSE_GB, DEFAULT_CACHE_WARNING_GB, Settings
from app.db import Database
from app.mediaguard.handoff import sweep_orphan_staging

MAX_DB_LOG_LINES = 500
MAX_SUCCESS_LOG_LINES = 200
LOGGER = logging.getLogger(__name__)


def append_job_log(database: Database, job_id: int, level: str, text: str) -> None:
    database.execute(
        "INSERT INTO logregel(job_id,niveau,tekst,tijd) VALUES (?,?,?,?)",
        (job_id, level, text[-4000:], datetime.now(UTC).isoformat()),
    )
    database.execute(
        """
        DELETE FROM logregel WHERE job_id=? AND id NOT IN (
            SELECT id FROM logregel WHERE job_id=? ORDER BY id DESC LIMIT ?
        )
        """,
        (job_id, job_id, MAX_DB_LOG_LINES),
    )


def record_failure(database: Database, job_id: int, message: str, error_type: str) -> int:
    signature = hashlib.sha256(f"{error_type}\0{message}".encode()).hexdigest()
    row = database.fetch_one(
        "SELECT laatste_fout_handtekening,zelfde_fout_pogingen FROM job WHERE id=?",
        (job_id,),
    )
    same = (
        int(row["zelfde_fout_pogingen"]) + 1
        if row is not None and row["laatste_fout_handtekening"] == signature
        else 1
    )
    database.execute(
        """
        UPDATE job SET status='mislukt',fout=?,foutsoort=?,klaar_op=?,proces_pid=NULL,
                       laatste_fout_handtekening=?,zelfde_fout_pogingen=?
        WHERE id=?
        """,
        (
            message,
            error_type,
            datetime.now(UTC).isoformat(),
            signature,
            same,
            job_id,
        ),
    )
    append_job_log(database, job_id, "error", message)
    attempt = database.fetch_one("SELECT pogingen,gestart_op FROM job WHERE id=?", (job_id,))
    if attempt is not None:
        attempt_number = max(1, int(attempt["pogingen"] or 0))
        database.execute(
            """
            INSERT INTO job_poging(
                job_id,poging,status,fout,foutsoort,fout_handtekening,gestart_op,klaar_op
            ) VALUES (?,?,'mislukt',?,?,?,?,?)
            ON CONFLICT(job_id,poging) DO UPDATE SET
                status='mislukt',fout=excluded.fout,foutsoort=excluded.foutsoort,
                fout_handtekening=excluded.fout_handtekening,
                klaar_op=excluded.klaar_op
            """,
            (
                job_id,
                attempt_number,
                message,
                error_type,
                signature,
                attempt["gestart_op"],
                datetime.now(UTC).isoformat(),
            ),
        )
    return same


def _trim_file(path: Path, maximum: int) -> None:
    lines = path.read_text(encoding="utf-8", errors="replace").splitlines(keepends=True)
    if len(lines) > maximum:
        path.write_text("".join(lines[-maximum:]), encoding="utf-8")


def rotate_logs(
    database: Database, settings: Settings, now: datetime | None = None
) -> dict[str, int]:
    current = now or datetime.now(UTC)
    log_dir = settings.config_root / "logs"
    log_dir.mkdir(parents=True, exist_ok=True)
    removed = 0
    trimmed = 0
    successful = {
        int(row["id"]) for row in database.fetch_all("SELECT id FROM job WHERE status='klaar'")
    }
    cutoff = current - timedelta(days=30)
    for path in log_dir.glob("job-*.log"):
        modified = datetime.fromtimestamp(path.stat().st_mtime, UTC)
        if modified < cutoff:
            path.unlink()
            removed += 1
            continue
        try:
            job_id = int(path.stem.removeprefix("job-"))
        except ValueError:
            continue
        before = path.stat().st_size
        if job_id in successful:
            _trim_file(path, MAX_SUCCESS_LOG_LINES)
            trimmed += int(path.stat().st_size < before)
    # Vangnet voor regels die buiten append_job_log zijn geschreven. Eén
    # vensterfunctie i.p.v. een gecorreleerde COUNT per regel (kwadratisch).
    database.execute(
        """
        DELETE FROM logregel WHERE id IN (
            SELECT id FROM (
                SELECT id,ROW_NUMBER() OVER (PARTITION BY job_id ORDER BY id DESC) AS rang
                FROM logregel
            ) WHERE rang > ?
        )
        """,
        (MAX_DB_LOG_LINES,),
    )
    return {"verwijderd": removed, "ingekort": trimmed}


# Opstartpauze bij ongeldige opslag; valt onder 'automatisch: opslag%' en wordt
# dus door storage_guard weer opgeheven. Iedere claim valideert de opslag zelf.
STORAGE_NOT_READY_PREFIX = "automatisch: opslag niet gereed: "
# Pauzes die de opslagbewaking zelf zet en weer opheft.
STORAGE_PAUSE_PREFIXES = ("automatisch: cache", "automatisch: opslag")


def cache_limits(database: Database, settings: Settings) -> dict[str, int]:
    """Ingestelde en effectieve cachegrenzen (GB); de effectieve bodem telt."""

    row = database.fetch_one(
        "SELECT cache_waarschuwing_gb,cache_pauze_gb FROM instelling WHERE id=1"
    )
    warning_gb = int(row["cache_waarschuwing_gb"]) if row else DEFAULT_CACHE_WARNING_GB
    pause_gb = int(row["cache_pauze_gb"]) if row else DEFAULT_CACHE_PAUSE_GB
    effective_warning, effective_pause = settings.effective_cache_limits(warning_gb, pause_gb)
    return {
        "configured_min_free_gb": pause_gb,
        "configured_warning_gb": warning_gb,
        "cache_min_free_gb": effective_pause,
        "cache_warning_gb": effective_warning,
    }


def lane_pause_state(database: Database) -> dict[str, dict[str, Any]]:
    """Per baan: gepauzeerd ja/nee + (Nederlandse) reden, rechtstreeks uit worker_config."""

    state: dict[str, dict[str, Any]] = {}
    for row in database.fetch_all("SELECT baan,gepauzeerd,pauze_reden FROM worker_config"):
        paused = bool(int(row["gepauzeerd"]))
        reason = str(row["pauze_reden"] or "") or None
        state[str(row["baan"])] = {
            "paused": paused,
            "reason": (reason or "handmatig gepauzeerd") if paused else None,
            "storage_pause": paused
            and bool(reason)
            and (
                str(reason).startswith("automatisch: cache")
                or str(reason).startswith("automatisch: opslag")
            ),
        }
    return state


def storage_status(database: Database, settings: Settings) -> dict[str, Any]:
    """Alleen-lezen rapport voor /health en /capabilities (geen pauzewijziging)."""

    limits = cache_limits(database, settings)
    try:
        free: int | None = shutil.disk_usage(settings.cache_root).free
    except OSError:
        free = None
    floor = int(limits["cache_min_free_gb"]) * 1024**3
    return {
        **limits,
        "cache_path": str(settings.cache_root),
        "free_bytes": free,
        "free_gb": round(free / 1024**3, 1) if free is not None else None,
        "below_floor": free is None or free < floor,
    }


def pause_overview(database: Database, settings: Settings) -> dict[str, Any]:
    lanes = lane_pause_state(database)
    return {
        "lanes": lanes,
        "lanes_paused": [
            {"lane": lane, "reason": info["reason"], "storage_pause": info["storage_pause"]}
            for lane, info in sorted(lanes.items())
            if info["paused"]
        ],
        "storage_guard": storage_status(database, settings),
    }


def storage_guard(database: Database, settings: Settings) -> dict[str, int | str]:
    try:
        free = shutil.disk_usage(settings.cache_root).free
    except OSError as exc:
        # Een handmatige of MediaGuard-pauze blijft staan met haar eigen reden.
        database.execute(
            "UPDATE worker_config SET gepauzeerd=1,pauze_reden=? "
            "WHERE gepauzeerd=0 OR pauze_reden LIKE 'automatisch:%'",
            (f"automatisch: opslag niet bereikbaar: {exc}",),
        )
        return {"vrij_bytes": 0, "status": f"automatisch gepauzeerd: {exc}"}
    limits = cache_limits(database, settings)
    effective_warning = int(limits["cache_warning_gb"])
    effective_pause = int(limits["cache_min_free_gb"])
    warning = effective_warning * 1024**3
    pause = effective_pause * 1024**3
    status = "voldoende"
    if free < pause:
        status = "automatisch gepauzeerd: opslag onder veilige vrije-ruimtebodem"
        database.execute(
            """
            UPDATE worker_config SET gepauzeerd=1,pauze_reden=?
            WHERE gepauzeerd=0 OR pauze_reden LIKE 'automatisch: cache%'
               OR pauze_reden LIKE 'automatisch: opslag%'
            """,
            (
                f"automatisch: cache heeft nog {free / 1024**3:.1f} GB vrij, onder de "
                f"pauzebodem van {effective_pause} GB; encoderen gepauzeerd tot er weer "
                "ruimte is",
            ),
        )
    elif free < warning:
        status = "waarschuwing: cache onder waarschuwingsgrens"
        database.execute(
            """
            UPDATE worker_config SET gepauzeerd=0,pauze_reden=NULL
            WHERE pauze_reden LIKE 'automatisch: cache%'
               OR pauze_reden LIKE 'automatisch: opslag%'
            """
        )
    else:
        database.execute(
            """
            UPDATE worker_config SET gepauzeerd=0,pauze_reden=NULL
            WHERE pauze_reden LIKE 'automatisch: cache%'
               OR pauze_reden LIKE 'automatisch: opslag%'
            """
        )
    return {"vrij_bytes": free, "status": status}


def weekly_database_maintenance(database: Database, now: datetime | None = None) -> bool:
    current = now or datetime.now(UTC)
    busy = database.fetch_one(
        "SELECT 1 FROM job WHERE status IN ('bezig','verifieren') LIMIT 1"
    )
    if busy is not None:
        # VACUUM houdt de hele database exclusief vast; voortgang, claims en de
        # brug zouden dan seconden tot minuten blokkeren. Volgend uur opnieuw.
        return False
    row = database.fetch_one(
        "SELECT laatst_op FROM onderhoud_status WHERE sleutel='sqlite_weekonderhoud'"
    )
    if row is not None and row["laatst_op"]:
        previous = datetime.fromisoformat(str(row["laatst_op"]))
        if current - previous < timedelta(days=7):
            return False
    database.execute("PRAGMA optimize")
    database.execute("VACUUM")
    database.execute(
        """
        INSERT INTO onderhoud_status(sleutel,laatst_op,resultaat)
        VALUES ('sqlite_weekonderhoud',?,'VACUUM en PRAGMA optimize geslaagd')
        ON CONFLICT(sleutel) DO UPDATE SET laatst_op=excluded.laatst_op,
                                           resultaat=excluded.resultaat
        """,
        (current.isoformat(),),
    )
    return True


# Cache-/werkmappen van jobs in een eindstatus zijn alleen nog afval. Een onverwachte fout
# liet er tientallen GB staan (verbeterpunt 28); 'wacht' ruimt het opstartherstel op.
CACHE_SWEEP_STATUSES = ("mislukt", "overgeslagen", "afgebroken", "klaar", "gecommit",
                        "geweigerd", "wacht_op_commit")


def sweep_finished_job_cache(database: Database, settings: Settings) -> list[int]:
    """Ruim per uur de cache- en werkmap op van jobs die klaar of gestopt zijn.

    Alleen numerieke mappen van bekende jobs, nooit als er nog een mediaproces van de job
    loopt of (direct-modus) een vervangtransactie openstaat. Onbekende inhoud blijft staan."""

    from app.wachtrijherstel import (
        QueueRecoveryError,
        media_process_alive,
        remove_job_temporary_output,
    )

    marks = ",".join("?" for _ in CACHE_SWEEP_STATUSES)
    removed: set[int] = set()
    for root in (settings.cache_root, settings.work_root):
        try:
            entries = [e for e in root.iterdir() if e.name.isdecimal()]
        except OSError:
            continue
        for entry in entries:
            job_id = int(entry.name)
            if job_id <= 0 or job_id in removed:
                continue
            row = database.fetch_one(
                f"SELECT proces_pid FROM job WHERE id=? AND status IN ({marks})",  # noqa: S608
                (job_id, *CACHE_SWEEP_STATUSES))
            if row is None or media_process_alive(int(row["proces_pid"] or 0)):
                continue
            with suppress(sqlite3.OperationalError):
                open_txn = database.fetch_one(
                    "SELECT 1 FROM vervang_transactie WHERE job_id=? AND status NOT IN "
                    "('hersteld','opgeruimd','vervangen') LIMIT 1", (job_id,))
                if open_txn is not None:
                    continue
            try:
                if remove_job_temporary_output(settings, job_id):
                    removed.add(job_id)
            except QueueRecoveryError as exc:
                LOGGER.warning("Cache van job %s niet opgeruimd: %s", job_id, exc)
    return sorted(removed)


def run_maintenance(database: Database, settings: Settings) -> None:
    rotate_logs(database, settings)
    if settings.replacement_mode == "handoff":
        removed = sweep_orphan_staging(database, settings)
        if removed:
            LOGGER.warning("Verweesde stagingmappen opgeruimd voor jobs: %s", removed)
    try:
        cleared = sweep_finished_job_cache(database, settings)
    except Exception:  # noqa: BLE001 - opruimen mag het onderhoud niet stoppen
        LOGGER.exception("Opruimen van achtergebleven jobcache mislukt")
        cleared = []
    if cleared:
        LOGGER.warning("Achtergebleven cache opgeruimd voor afgeronde jobs: %s", cleared)
    storage = storage_guard(database, settings)
    weekly_database_maintenance(database)
    automatic = database.fetch_one(
        "SELECT ingeschakeld FROM functie WHERE sleutel='automatische_backups'"
    )
    previous = database.fetch_one(
        "SELECT laatst_op FROM onderhoud_status WHERE sleutel='automatische_backup'"
    )
    now = datetime.now(UTC)
    last_backup = (
        datetime.fromisoformat(str(previous["laatst_op"]))
        if previous is not None and previous["laatst_op"]
        else None
    )
    if (
        automatic is not None
        and int(automatic["ingeschakeld"])
        and (last_backup is None or now - last_backup >= timedelta(days=1))
    ):
        create_backup(database, settings)
        enforce_retention(database)
        database.execute(
            """
            INSERT INTO onderhoud_status(sleutel,laatst_op,resultaat)
            VALUES ('automatische_backup',?,'back-up en retentie geslaagd')
            ON CONFLICT(sleutel) DO UPDATE SET laatst_op=excluded.laatst_op,
                                               resultaat=excluded.resultaat
            """,
            (now.isoformat(),),
        )
    database.execute(
        """
        INSERT INTO onderhoud_status(sleutel,laatst_op,resultaat)
        VALUES ('algemeen',?,?)
        ON CONFLICT(sleutel) DO UPDATE SET laatst_op=excluded.laatst_op,
                                           resultaat=excluded.resultaat
        """,
        (datetime.now(UTC).isoformat(), str(storage["status"])),
    )


async def maintenance_loop(database: Database, settings: Settings) -> None:
    while True:
        try:
            await asyncio.to_thread(run_maintenance, database, settings)
        except Exception:
            # Onderhoud mag de uurcyclus niet permanent verliezen door een
            # tijdelijk ontbrekende werkschijf of defecte back-upbestemming.
            LOGGER.exception("Onderhoudscyclus mislukt; volgende uur opnieuw")
        await asyncio.sleep(3600)
