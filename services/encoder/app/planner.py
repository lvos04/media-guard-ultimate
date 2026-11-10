"""Twee strikt gescheiden asyncio-banen met atomische SQLite-claims."""

from __future__ import annotations

import asyncio
import json
import logging
import os
import shutil
import signal
import sqlite3
from collections.abc import Callable
from contextlib import suppress
from datetime import UTC, datetime, time, timedelta
from pathlib import Path
from time import monotonic, sleep
from typing import Any

import psutil

from app.config import Settings
from app.cpubudget import effective_cpu_percent
from app.db import Database
from app.hostlimits import cpu_temperature, effective_memory
from app.onderhoud import record_failure, storage_guard
from app.uitvoerder import (
    MAX_RESTART_INTERRUPTIONS,
    request_shutdown,
    reset_shutdown,
    run_job,
    terminate_process_group,
)
from app.wachtrijherstel import QueueRecoveryError, remove_job_temporary_output

Lane = str
MachineGuard = Callable[[Lane], tuple[bool, str | None]]
StorageValidator = Callable[[], None]
LOGGER = logging.getLogger(__name__)
# Vrije-ruimtebewaking vóór een claim: hooguit zo vaak (s) en alleen als er werk wacht.
STORAGE_RECHECK_SECONDS = 10.0


def _temperature() -> float | None:
    # Alleen de CPU-pakketsensor: een warme schijf mag de CPU-baan niet pauzeren.
    return cpu_temperature()


# Bovenop de limiet: dan start er geen enkele nieuwe encode meer (ook niet op de GPU, want
# die decodeert op de CPU). Lopende encodes houden hun threadbudget.
CPU_OVERSHOOT_MARGIN = 10


# Geschat werkgeheugen per encode (GiB). SVT-AV1 met lookahead op 4K gebruikt
# meerdere GB; een GPU-job vooral voor het (software-)decoderen van de bron.
MEMORY_ESTIMATE_GIB = {("cpu", "2160"): 6.0, ("cpu", "1080"): 2.5, ("cpu", "720"): 1.2,
                       ("gpu", "2160"): 1.5, ("gpu", "1080"): 0.8, ("gpu", "720"): 0.5}
# Marge die vrij moet blijven na de start (GiB of fractie van het totaal, de grootste).
# Bewust t.o.v. MemAvailable en niet "gebruikt ≤ 80 % van totaal": op TrueNAS telt de
# ZFS-ARC als gebruikt, zodat er met 34 GB vrij toch niets startte (rapport 4, S1).
# Vroeger max(2 GiB, 5 % van het totaal): op een 202 GB-host was dat ongezien 10 GB extra,
# zodat 11,6 GB vrij − 6 GB nodig < 10 GB reserve en er urenlang niets startte
# (rapport 8, A5). Nu één zichtbare, instelbare reserve (afstelling geheugen_reserve_gb).
DEFAULT_MEMORY_RESERVE_GIB = 4.0
RAMP_UP_SECONDS = 180  # net gestarte encodes hebben hun geheugen nog niet in gebruik


def _resolution_class(probe_json: object) -> str:
    try:
        height = int((json.loads(str(probe_json)) or {}).get("height") or 0)
    except (TypeError, ValueError):
        height = 0
    return "2160" if height > 1200 else "1080" if height > 800 or height == 0 else "720"


def memory_estimate(lane: str, probe_json: object) -> int:
    return int(MEMORY_ESTIMATE_GIB[(lane, _resolution_class(probe_json))] * 1024**3)


def memory_block_reason(
    connection: Any, lane: str, probe_json: object, *, memory: Any = None
) -> str | None:
    """Reden om nu niet te starten als het werkgeheugen dan te vol raakt, anders None.

    Te veel tegelijk (bv. 2 CPU- + 4 GPU-encodes op 4K) liet de kernel processen
    stoppen, waarna alles opnieuw begon (rapport 2, N1)."""
    memory = memory or effective_memory()
    since = (datetime.now(UTC) - timedelta(seconds=RAMP_UP_SECONDS)).isoformat()
    ramping = 0
    for other in connection.execute(
        "SELECT j.baan,f.probe FROM job j JOIN bestand f ON f.id=j.bestand_id "
        "WHERE j.status='bezig' AND j.gestart_op>=?", (since,)
    ).fetchall():
        ramping += memory_estimate(str(other[0] or "cpu"), other[1])
    need = memory_estimate(lane, probe_json)
    try:
        from app import afstelling

        reserve_gib = float(afstelling.get("geheugen_reserve_gb"))
    except Exception:  # noqa: BLE001 - zonder afstelling de standaard
        reserve_gib = DEFAULT_MEMORY_RESERVE_GIB
    reserve = int(reserve_gib * 1024**3)
    if memory.total > 0 and memory.available - ramping - need < reserve:
        gib = 1024**3
        zfs = int(getattr(memory, "zfs_reclaimable", 0) or 0)
        text = f"wacht op werkgeheugen: vrij {memory.available / gib:.1f} GB"
        if zfs:
            text += f" (waarvan ZFS-cache {zfs / gib:.1f} GB)"
        text += f", nodig {need / gib:.1f} GB + reserve {reserve / gib:.1f} GB"
        if ramping:
            text += f" + {ramping / gib:.1f} GB voor net gestarte encodes"
        return text.replace(".", ",")
    return None


def confinement_active(database: Database) -> bool:
    from app.cpuconfine import confine_prefix, encode_percent

    try:
        return bool(confine_prefix(encode_percent(database)))
    except Exception:  # noqa: BLE001
        return False


def default_machine_guard(
    database: Database, lane: Lane, *, container_mode: bool = False
) -> tuple[bool, str | None]:
    confined = confinement_active(database)
    if lane != "cpu":
        # Met een harde CPU-grens (affiniteit) kunnen onze eigen CPU-encodes niet boven de
        # limiet komen; hun gebruik mag de GPU-baan dan niet stilleggen (rapport 5, T1).
        if confined:
            return True, None
        limit = effective_cpu_percent(database, container_mode=container_mode)
        cpu = psutil.cpu_percent(interval=None)
        if cpu >= min(100, limit + CPU_OVERSHOOT_MARGIN):
            return False, f"CPU-gebruik {cpu:.0f}% boven limiet {limit}%"
        return True, None
    setting = database.fetch_one(
        "SELECT cpu_max_percent,cpu_max_temperatuur FROM instelling WHERE id=1"
    )
    if setting is None:
        return False, "algemene instellingen ontbreken"
    limit = effective_cpu_percent(database, container_mode=container_mode)
    own_running = container_mode and database.fetch_one(
        "SELECT 1 FROM job WHERE baan='cpu' AND status IN ('bezig','verifieren') LIMIT 1"
    ) is not None
    cpu = psutil.cpu_percent(interval=None)
    # Containermodus: eigen encodes zijn al begrensd via het threadbudget (zelfde
    # percentage); hun eigen gebruik mag de volgende ingestelde encode niet blokkeren.
    # Met harde grens: de encodes zitten op hun eigen kernen; het totaalgebruik zegt dan
    # niets meer over ruimte voor de volgende encode (die deelt dezelfde kernen).
    if not confined and ((not own_running and cpu >= limit)
                         or cpu >= min(100, limit + CPU_OVERSHOOT_MARGIN)):
        return False, f"CPU-gebruik {cpu:.0f}% boven limiet {limit}%"
    temperature = _temperature()
    if temperature is not None and temperature >= int(setting["cpu_max_temperatuur"]):
        return False, f"CPU-temperatuur {temperature:.0f} °C boven limiet"
    return True, None


def _in_silence_window(start: str | None, end: str | None, now: datetime) -> bool:
    if not start or not end:
        return False
    start_time = time.fromisoformat(start)
    end_time = time.fromisoformat(end)
    current = now.timetz().replace(tzinfo=None)
    if start_time <= end_time:
        return start_time <= current < end_time
    return current >= start_time or current < end_time


def _orphan_process(pid: int, job_id: int, settings: Settings) -> bool:
    """Hoort deze PID aantoonbaar bij het mediaproces van deze job?

    Alleen een substring als "ffmpeg" is te zwak (PID-hergebruik); de eigen
    numerieke cache- of werkmap van de job moet in de opdrachtregel staan.
    """

    if pid <= 0 or not psutil.pid_exists(pid):
        return False
    try:
        command = psutil.Process(pid).cmdline()
    except (psutil.Error, OSError):
        return False
    name = Path(command[0]).name if command else ""
    if not (name.startswith("ffmpeg") or name in {"mkvmerge", "dovi_tool"}):
        return False
    markers = (str(settings.cache_root / str(job_id)), str(settings.work_root / str(job_id)))
    return any(marker in argument for argument in command for marker in markers)


def _stop_orphan(pid: int) -> None:
    """Stop de procesgroep van een verweesde encode (eigen sessie: pgid == pid)."""

    if not terminate_process_group(pid, signal.SIGTERM):
        return
    deadline = monotonic() + 10
    while monotonic() < deadline and psutil.pid_exists(pid):
        try:
            if psutil.Process(pid).status() == psutil.STATUS_ZOMBIE:
                return
        except psutil.Error:
            return
        sleep(0.2)
    terminate_process_group(pid, signal.SIGKILL)




def recover_orphaned_jobs(database: Database, settings: Settings) -> int:
    """Zet procesloze of verweesde jobs terug; half output is nooit resultaat.

    Bij opstart bestaan er nog geen plannertaken: een nog levend mediaproces van
    een vorige backend kan zijn voortgang nergens meer melden en houdt anders
    eeuwig een encodeslot bezet. Dat proces wordt gestopt en de job gaat terug
    in de wachtrij; de lopende poging wordt netjes afgesloten.
    """

    from app.geplandestop import clear, planned_stop_at

    # Was de vorige stop aangekondigd (update/herstart van de container), dan is een
    # achtergebleven 'bezig'-job geen crash: niet meetellen voor de herstartlus. Het
    # bestand geldt alleen voor deze ene start; ook bij een fout hieronder weg ermee.
    planned_at = planned_stop_at(settings)
    try:
        return _recover_orphaned_jobs(database, settings, planned_at)
    finally:
        if planned_at is not None:
            clear(settings)


def _recover_orphaned_jobs(database: Database, settings: Settings,
                           planned_at: datetime | None) -> int:
    from app import noodstop
    from app.geplandestop import PLANNED_STOP_ERROR, PLANNED_STOP_TYPE
    from app.uitvoerder import requeue_for_emergency

    recovered = 0
    emergency = bool(noodstop.load(database).get("active"))
    rows = database.fetch_all(
        "SELECT id,proces_pid,pogingen FROM job WHERE status IN ('bezig','verifieren')"
    )
    for row in rows:
        pid = int(row["proces_pid"] or 0)
        job_id = int(row["id"])
        if _orphan_process(pid, job_id, settings):
            LOGGER.warning("Verweesd mediaproces %s van job %s wordt gestopt", pid, job_id)
            _stop_orphan(pid)
        try:
            remove_job_temporary_output(settings, job_id)
        except QueueRecoveryError as exc:
            record_failure(database, job_id, str(exc), type(exc).__name__)
            continue
        if emergency:
            # Herstart terwijl de noodstop actief is: geen poging, geen 'onderbreking'.
            requeue_for_emergency(database, job_id)
            recovered += 1
            continue
        if planned_at is not None:
            database.execute(
                "UPDATE job SET status='wacht',worker_id=NULL,proces_pid=NULL,"
                "cache_reservering=0,percent=0,fout=? WHERE id=?",
                (PLANNED_STOP_ERROR, job_id))
            database.execute(
                "UPDATE job_poging SET status='afgebroken',klaar_op=?,fout=?,foutsoort=? "
                "WHERE job_id=? AND poging=? AND status='bezig'",
                (datetime.now(UTC).isoformat(), PLANNED_STOP_ERROR, PLANNED_STOP_TYPE,
                 job_id, int(row["pogingen"] or 0)))
            recovered += 1
            continue
        interrupted = database.fetch_one(
            "SELECT COUNT(*) FROM job_poging WHERE job_id=? AND foutsoort='ServiceStop'",
            (job_id,),
        )
        if interrupted is not None and int(interrupted[0]) + 1 >= MAX_RESTART_INTERRUPTIONS:
            # Geen eindeloze lus: na 3 onderbrekingen zonder resultaat is het een fout die
            # gezien moet worden (Fouten, Overzicht), niet nog een stille herstart.
            database.execute(
                "UPDATE job_poging SET status='afgebroken',klaar_op=?,"
                "fout='onderbroken door backendherstart',foutsoort='ServiceStop' "
                "WHERE job_id=? AND poging=? AND status='bezig'",
                (datetime.now(UTC).isoformat(), job_id, int(row["pogingen"] or 0)),
            )
            database.execute(
                "UPDATE job SET worker_id=NULL,proces_pid=NULL,cache_reservering=0 WHERE id=?",
                (job_id,),
            )
            record_failure(
                database, job_id,
                f"{int(interrupted[0]) + 1}× onderbroken doordat de AV1-engine herstartte "
                f"({int(row['pogingen'] or 0)} pogingen in totaal), zonder dat de encode afkwam. "
                "Bekijk het joblog; MediaGuard probeert het later één keer automatisch opnieuw.",
                "HerstartLus",
            )
            recovered += 1
            continue
        database.execute(
            """
            UPDATE job SET status='wacht',worker_id=NULL,proces_pid=NULL,
                           cache_reservering=0,percent=0,fout='hersteld na backendherstart'
            WHERE id=?
            """,
            (job_id,),
        )
        database.execute(
            """
            UPDATE job_poging SET status='afgebroken',klaar_op=?,
                   fout=COALESCE(fout,'onderbroken door backendherstart'),
                   foutsoort=COALESCE(foutsoort,'ServiceStop')
            WHERE job_id=? AND poging=? AND status='bezig'
            """,
            (datetime.now(UTC).isoformat(), job_id, int(row["pogingen"] or 0)),
        )
        recovered += 1
    # Een job met status 'wacht' kan bij een eerdere handmatige retry nog een
    # deelbestand hebben. Bij backendstart bestaan er nog geen plannertaken,
    # dus deze exacte numerieke jobmappen zijn aantoonbaar tijdelijk.
    waiting = database.fetch_all("SELECT id FROM job WHERE status='wacht' ORDER BY id")
    for row in waiting:
        job_id = int(row["id"])
        try:
            removed = remove_job_temporary_output(settings, job_id)
        except QueueRecoveryError as exc:
            record_failure(database, job_id, str(exc), type(exc).__name__)
            continue
        if removed:
            database.execute(
                "UPDATE job SET proces_pid=NULL,worker_id=NULL,cache_reservering=0,percent=0 "
                "WHERE id=? AND status='wacht'",
                (job_id,),
            )
    return recovered


_PLANNERS: list[Planner] = []


def release_canary_for_mediaguard(database: Database) -> None:
    """Containermodus: MediaGuard beslist (met eigen vrijgave- en recovery-controles), dus de
    canary-rem van de standalone-scanner geldt niet. Oude 'actief'-bibliotheken (import uit
    standalone, AI-reset) en jobs die daarop wachtten worden vrijgegeven."""
    with suppress(Exception):
        database.execute("UPDATE library SET canary_status='vrijgegeven' "
                         "WHERE canary_status='actief'")
        database.execute("UPDATE job SET fout=NULL WHERE status='wacht' "
                         "AND fout LIKE '%canary-batch%'")


# Hoogte uit de opgeslagen probe (bridge-jobs); onbekend telt als niet-4K.
UHD_SQL = "COALESCE(CAST(json_extract(f.probe,'$.height') AS INTEGER),0)>1200"
DEFAULT_MAX_UHD = {"gpu": 2, "cpu": 1}


def max_uhd_concurrent(lane: str) -> int:
    """Instelbaar via MG_MAX_4K_GPU / MG_MAX_4K_CPU (standaard 2)."""
    from app import afstelling

    return max(1, int(afstelling.get("max_4k_gpu" if lane == "gpu" else "max_4k_cpu")))


def in_night_window(now: datetime | None = None) -> bool:
    """Nachtvenster (lokale tijd, MG_NIGHT_WINDOW='01-07'): zware CPU-herkansingen van
    4K-HDR na een mislukte GPU-poging gaan overdag achteraan; 's nachts gewoon mee
    (rapport 4, S2). Is de CPU-baan verder leeg, dan starten ze ook overdag."""
    from app import afstelling

    start, end = int(afstelling.get("nacht_start")), int(afstelling.get("nacht_einde"))
    raw = os.environ.get("MG_NIGHT_WINDOW")  # noodoverride
    if raw:
        with suppress(ValueError):
            start, end = (int(x) for x in raw.split("-", 1))
    hour = (now or datetime.now()).hour
    return start <= hour < end if start <= end else hour >= start or hour < end


def pick_candidate(database: Database, lane: str, candidates: list[Any]) -> Any:
    """Eerste kandidaat, maar jobs met een geschatte duur boven de grens alleen in het
    nachtvenster (rapport 5, M9). Zijn er alleen lange jobs, dan start de eerste toch:
    een stilstaande baan helpt niemand."""
    from app import afstelling
    from app.duurschatting import cached_speeds, estimate_seconds

    if not candidates:
        return None
    if not afstelling.get("lange_jobs_alleen_nacht") or in_night_window():
        return candidates[0]
    limit = float(afstelling.get("lange_job_uren")) * 3600
    speeds = cached_speeds(database)
    for candidate in candidates:
        with suppress(IndexError, KeyError):
            if int(candidate["alleen_nacht"] or 0):
                continue  # maakte overdag plaats voor kortere jobs (gaat 's nachts verder)
        estimate = estimate_seconds(lane, candidate["encoder"], candidate["probe"], speeds)
        if estimate is None or estimate <= limit:
            return candidate
    return candidates[0]


# Laatste reden per baan waarom er níet gestart werd terwijl er werk wacht (rapport 4, S1):
# de live-stand toont die, zodat een lege baan met wachtende jobs altijd uitgelegd is.
LANE_BLOCK_REASON: dict[str, str | None] = {"cpu": None, "gpu": None}
# Sinds wanneer een baan om dezelfde soort reden niets kan starten (rapport 8, A5: 5,5 uur
# stil zonder melding). Sleutel = soort reden (tekst vóór de dubbele punt).
LANE_BLOCK_SINCE: dict[str, tuple[str, datetime] | None] = {"cpu": None, "gpu": None}
LANE_STALL_MINUTES = 30


def _reason_kind(reason: str) -> str:
    return reason.split(":", 1)[0].strip().lower()


def deliberate_block(reason: str | None) -> bool:
    """Bewuste of tijdelijke stilstand, geen storing (verbeterlijst 8): iemand kijkt in de
    mediaserver, een eigen pauze, stille uren of de noodstop. Daarvoor geen stilstandmelding
    en geen 'verminderde' engine."""
    text = str(reason or "").strip().lower()
    return (not text or text.startswith(("kijken:", "jellyfin:", "handmatig", "noodstop"))
            or "stiltevenster" in text or "iemand kijkt" in text)


def note_lane_block(lane: str, reason: str | None, now: datetime | None = None) -> None:
    LANE_BLOCK_REASON[lane] = reason
    if not reason:
        LANE_BLOCK_SINCE[lane] = None
        return
    kind = _reason_kind(reason)
    current = LANE_BLOCK_SINCE.get(lane)
    if current is None or current[0] != kind:
        LANE_BLOCK_SINCE[lane] = (kind, now or datetime.now(UTC))


def lane_stalls(database: Any, now: datetime | None = None) -> list[dict[str, Any]]:
    """Banen die langer dan LANE_STALL_MINUTES stilstaan met wachtend werk, om dezelfde
    reden en zonder lopende encode. Core maakt hier een aandachtspunt + melding van."""
    now = now or datetime.now(UTC)
    out: list[dict[str, Any]] = []
    for lane, entry in LANE_BLOCK_SINCE.items():
        reason = LANE_BLOCK_REASON.get(lane)
        if entry is None or not reason or deliberate_block(reason):
            continue
        minutes = (now - entry[1]).total_seconds() / 60
        if minutes < LANE_STALL_MINUTES:
            continue
        try:
            running = database.fetch_one(
                "SELECT COUNT(*) FROM job WHERE baan=? AND status IN ('bezig','verifieren')",
                (lane,))[0]
            waiting = database.fetch_one(
                "SELECT COUNT(*) FROM job WHERE baan=? AND status='wacht'", (lane,))[0]
        except Exception:  # noqa: BLE001
            continue
        if int(running) == 0 and int(waiting) > 0:
            out.append({"baan": lane, "reden": reason, "sinds": entry[1].isoformat(),
                        "minuten": int(minutes), "wachtend": int(waiting)})
    return out


def wake_planner() -> None:
    """Wek iedere draaiende planner (na een wijziging in de wachtrij of workers)."""

    for planner in list(_PLANNERS):
        planner.wake()


class Planner:
    """Plan maximaal het ingestelde aantal jobs zonder CPU/GPU te vermengen."""

    def __init__(
        self,
        database: Database,
        settings: Settings,
        *,
        storage_validator: StorageValidator | None = None,
        machine_guard: MachineGuard | None = None,
    ) -> None:
        self.database = database
        self.settings = settings
        self.storage_validator = storage_validator or settings.validate_storage
        self.machine_guard = machine_guard or (
            lambda lane: default_machine_guard(
                self.database, lane, container_mode=settings.container_mode)
        )
        self.tasks: dict[int, asyncio.Task[bool]] = {}
        self._stopping = False
        self._wake: asyncio.Event | None = None
        self._loop: asyncio.AbstractEventLoop | None = None
        self.next_lane: Lane = "cpu"
        self.last_storage_check: float | None = None
        if settings.container_mode:
            release_canary_for_mediaguard(database)
        from app import noodstop

        noodstop.load(database)  # bewaarde noodstop geldt ook na een herstart

    @property
    def stopping(self) -> bool:
        return self._stopping

    @stopping.setter
    def stopping(self, value: bool) -> None:
        self._stopping = value
        if value:
            self.wake()

    def wake(self) -> None:
        """Volgende tick nu (nieuwe job, gewijzigde workers, job klaar of stoppen).

        Thread-veilig: API-routes draaien deels in de threadpool."""

        loop, event = self._loop, self._wake
        if loop is None or event is None:
            return
        with suppress(RuntimeError):
            loop.call_soon_threadsafe(event.set)

    def _lane_config(self, lane: Lane) -> Any:
        return self.database.fetch_one("SELECT * FROM worker_config WHERE baan=?", (lane,))

    def _lane_available(self, lane: Lane) -> tuple[bool, str | None]:
        config = self._lane_config(lane)
        if config is None:
            return False, "workerconfiguratie ontbreekt"
        if int(config["gepauzeerd"]):
            return False, str(config["pauze_reden"] or "handmatig gepauzeerd")
        if _in_silence_window(config["stilte_start"], config["stilte_einde"], datetime.now()):
            return False, "stiltevenster actief"
        return self.machine_guard(lane)

    def recheck_storage(self, lane: Lane, *, force: bool = False) -> None:
        """Herbeoordeel de vrije-ruimtebodem vóór een claim (niet alleen elk uur).

        Zo wordt een automatische cachepauze opgeheven zodra er weer ruimte is, en
        gaat een baan direct op pauze als de bodem intussen is onderschreden.
        """

        now = monotonic()
        if (
            not force
            and self.last_storage_check is not None
            and now - self.last_storage_check < STORAGE_RECHECK_SECONDS
        ):
            return
        waiting = self.database.fetch_one(
            "SELECT 1 FROM job WHERE status='wacht' AND baan=? LIMIT 1", (lane,)
        )
        if waiting is None:
            return
        self.last_storage_check = now
        try:
            storage_guard(self.database, self.settings)
        except Exception:  # noqa: BLE001 - claimcontrole hieronder blijft fail-closed
            LOGGER.exception("Vrije-ruimtecontrole vóór claim mislukt")

    def claim_next(self, lane: Lane, worker_id: str) -> tuple[int | None, str | None]:
        from app import afstelling

        afstelling.refresh_if_stale(self.database)
        from app import routeleer

        routeleer.refresh_if_stale(self.database)
        from app import noodstop

        # Noodstop vanuit MediaGuard (rapport 6, N1): nooit een nieuwe job oppakken.
        if not noodstop.state_loaded():
            noodstop.load(self.database)
        stop_reason = noodstop.claim_reason()
        if stop_reason:
            return None, stop_reason
        self.recheck_storage(lane)
        allowed, reason = self._lane_available(lane)
        if not allowed:
            return None, reason
        try:
            self.storage_validator()
        except Exception as exc:
            return None, f"opslagcontrole: {exc}"
        with self.database.connection() as connection:
            connection.execute("BEGIN IMMEDIATE")
            active = int(
                connection.execute(
                    "SELECT COUNT(*) FROM job WHERE status IN ('bezig','verifieren')"
                ).fetchone()[0]
            )
            configured_limit = connection.execute(
                "SELECT max_gelijktijdige_encodes FROM instelling WHERE id=1"
            ).fetchone()
            effective_max = int(configured_limit[0]) if configured_limit is not None else 1
            if active >= effective_max:
                connection.rollback()
                return None, (
                    f"ingestelde totale encodegrens van {effective_max} actieve job(s) bereikt"
                )
            # Hooguit zoveel 4K-encodes tegelijk op deze baan: vier 4K-remuxen op één
            # Arc A310 delen het videogeheugen en de mediaengine en komen dan geen van alle
            # vooruit (rapport 3, R1). Kleinere bestanden mogen de vrije plekken vullen.
            uhd_cap = max_uhd_concurrent(lane)
            uhd_running = int(
                connection.execute(
                    "SELECT COUNT(*) FROM job j JOIN bestand f ON f.id=j.bestand_id "
                    "WHERE j.status IN ('bezig','verifieren') AND j.baan=? "
                    f"AND {UHD_SQL}",
                    (lane,),
                ).fetchone()[0]
            )
            uhd_filter = "" if uhd_running < uhd_cap else f" AND NOT {UHD_SQL}"
            # Snelste winst eerst: binnen dezelfde prioriteit eerst ≤1080p, daarna de
            # kleinste bestanden; 55 GB-remuxen komen achteraan.
            candidates = connection.execute(
                f"""
                SELECT j.id,f.grootte,f.library_id,l.canary_status,f.probe,b.encoder,
                       COALESCE(j.alleen_nacht,0) AS alleen_nacht
                FROM job j JOIN bestand f ON f.id=j.bestand_id
                JOIN library l ON l.id=f.library_id
                LEFT JOIN besluit b ON b.id=j.besluit_id
                WHERE j.status='wacht' AND j.baan=? AND l.actief=1{uhd_filter}
                ORDER BY (COALESCE(j.alleen_nacht,0)=1 AND ?=0) ASC,j.prioriteit DESC,
                         (COALESCE(j.gpu_hdr_teruggevallen,0)=1 AND ?=0) ASC,
                         {UHD_SQL} ASC,f.grootte ASC,j.id ASC LIMIT 25
                """,  # noqa: S608 - alleen vaste SQL-fragmenten
                (lane, int(in_night_window()), int(in_night_window())),
            ).fetchall()
            row = pick_candidate(self.database, lane, candidates)
            if row is None and uhd_filter:
                connection.rollback()
                return None, (f"{uhd_running} 4K-encode(s) tegelijk op de {lane.upper()} "
                              "(maximum); de volgende 4K-film wacht tot er een klaar is")
            if row is None:
                connection.rollback()
                return None, None
            canary = connection.execute(
                "SELECT ingeschakeld,configuratie FROM functie WHERE sleutel='canary_limiet'"
            ).fetchone()
            if (
                not self.settings.container_mode  # MediaGuard beslist zelf
                and canary is not None
                and int(canary["ingeschakeld"])
                and row["canary_status"] == "actief"
            ):
                configuration = json.loads(str(canary["configuratie"]))
                max_files = int(configuration.get("max_bestanden", 50))
                max_bytes = int(configuration.get("max_gb", 100)) * 1024**3
                max_today = int(configuration.get("max_jobs_per_dag", 10))
                completed = connection.execute(
                    """
                    SELECT COUNT(*) AS aantal,COALESCE(SUM(j.bron_grootte),0) AS bytes
                    FROM job j JOIN bestand f ON f.id=j.bestand_id
                    WHERE f.library_id=? AND j.status IN ('klaar','gecommit')
                    """,
                    (int(row["library_id"]),),
                ).fetchone()
                today = int(
                    connection.execute(
                        """
                        SELECT COUNT(*) FROM job j JOIN bestand f ON f.id=j.bestand_id
                        WHERE f.library_id=? AND date(j.gestart_op)=date('now')
                        """,
                        (int(row["library_id"]),),
                    ).fetchone()[0]
                )
                if (
                    int(completed["aantal"]) >= max_files
                    or int(completed["bytes"]) >= max_bytes
                    or today >= max_today
                ):
                    connection.execute(
                        "UPDATE job SET fout='wacht op expliciete vrijgave van "
                        "canary-batch' WHERE id=?",
                        (int(row["id"]),),
                    )
                    connection.commit()
                    return None, "canary-limiet bereikt"
            memory_reason = memory_block_reason(connection, lane, row["probe"])
            if memory_reason is not None:
                connection.execute(
                    "UPDATE job SET fout=? WHERE id=?", (memory_reason, int(row["id"])))
                connection.commit()
                return None, memory_reason
            reserve = int(int(row["grootte"]) * self.settings.cache_reserve_factor)
            reserved = int(
                connection.execute(
                    """
                    SELECT COALESCE(SUM(cache_reservering),0) FROM job
                    WHERE status IN ('bezig','verifieren')
                    """
                ).fetchone()[0]
            )
            setting = connection.execute(
                "SELECT cache_waarschuwing_gb,cache_pauze_gb FROM instelling WHERE id=1"
            ).fetchone()
            warning_gb = int(setting[0]) if setting is not None else 250
            pause_gb = int(setting[1]) if setting is not None else 150
            _effective_warning, effective_pause = self.settings.effective_cache_limits(
                warning_gb, pause_gb
            )
            pause_bytes = effective_pause * 1024**3
            free = shutil.disk_usage(self.settings.cache_root).free
            if free - reserved - reserve < pause_bytes:
                connection.execute(
                    "UPDATE job SET fout=? WHERE id=?",
                    (
                        "wacht op voldoende cacheruimte boven de vaste schijfbodem",
                        int(row["id"]),
                    ),
                )
                connection.commit()
                return None, "onvoldoende vrije, niet-gereserveerde cacheruimte"
            cursor = connection.execute(
                """
                UPDATE job SET status='bezig',worker_id=?,gestart_op=?,pogingen=pogingen+1,
                               cache_reservering=?,fout=NULL
                WHERE id=? AND status='wacht'
                """,
                (
                    worker_id,
                    datetime.now(UTC).isoformat(),
                    reserve,
                    int(row["id"]),
                ),
            )
            if cursor.rowcount != 1:
                connection.rollback()
                return None, "job werd al door een andere planner geclaimd"
            attempt = int(
                connection.execute("SELECT pogingen FROM job WHERE id=?", (int(row["id"]),))
                .fetchone()[0]
            )
            connection.execute(
                """
                INSERT INTO job_poging(job_id,poging,status,gestart_op)
                VALUES (?,?,'bezig',?)
                ON CONFLICT(job_id,poging) DO UPDATE SET
                    status='bezig',gestart_op=excluded.gestart_op,klaar_op=NULL
                """,
                (int(row["id"]), attempt, datetime.now(UTC).isoformat()),
            )
            connection.commit()
            return int(row["id"]), None

    async def _worker(self, job_id: int, worker_id: str) -> bool:
        try:
            return await run_job(
                self.database,
                self.settings,
                job_id,
                worker_id=worker_id,
                storage_validator=self.storage_validator,
                already_claimed=True,
            )
        except Exception as exc:
            # Dit is de laatste procesgrens. Ook een fout vóór de normale
            # uitvoerder-try (zoals een achtergebleven cachemap) mag de job
            # nooit stil als 'bezig' achterlaten.
            LOGGER.exception("Worker voor job %s faalde buiten de uitvoerder", job_id)
            record_failure(
                self.database, job_id,
                f"onverwachte fout vóór of na de encode ({type(exc).__name__}): "
                f"{str(exc)[:400] or 'geen details'}; het origineel is niet aangeraakt",
                type(exc).__name__)
            # Geen tientallen GB cache laten staan (verbeterpunt 28); alleen de eigen,
            # numerieke job-mappen en alleen als er geen mediaproces meer loopt.
            with suppress(Exception):
                row = self.database.fetch_one("SELECT proces_pid FROM job WHERE id=?",
                                              (job_id,))
                from app.wachtrijherstel import media_process_alive

                if row is None or not media_process_alive(int(row["proces_pid"] or 0)):
                    remove_job_temporary_output(self.settings, job_id)
            return False
        finally:
            self.database.execute(
                "UPDATE job SET cache_reservering=0,proces_pid=NULL WHERE id=?",
                (job_id,),
            )
            # plek vrij: meteen de volgende job laten claimen
            self.wake()

    def _remove_done(self) -> None:
        for job_id, task in list(self.tasks.items()):
            if task.done():
                try:
                    task.result()
                except asyncio.CancelledError:
                    pass
                except Exception:
                    LOGGER.exception("Worker voor job %s stopte onverwacht", job_id)
                del self.tasks[job_id]

    async def tick(self) -> None:
        self._remove_done()
        active_by_lane: dict[str, int] = {"cpu": 0, "gpu": 0}
        if self.tasks:
            placeholders = ",".join("?" for _ in self.tasks)
            rows = self.database.fetch_all(
                f"""
                SELECT baan,COUNT(*) AS aantal FROM job
                WHERE id IN ({placeholders}) GROUP BY baan
                """,
                tuple(self.tasks),
            )
            for row in rows:
                active_by_lane[str(row["baan"])] = int(row["aantal"])
        lane_order: tuple[Lane, Lane] = (
            ("cpu", "gpu") if self.next_lane == "cpu" else ("gpu", "cpu")
        )
        for lane in lane_order:
            config = self._lane_config(lane)
            desired = int(config["aantal"]) if config is not None else 1
            # Te vaak vastgelopen (rapport 6, N2): tijdelijk één plek op deze baan.
            from app import vastlopers

            desired = vastlopers.lane_cap(self.database, lane, desired)
            # Rapport 7: GPU-plekken volgens de eigen meting; tweede CPU-plek pas na bewijs.
            if lane == "gpu":
                from app import gpubench

                desired = gpubench.lane_cap(self.database, desired)
            else:
                from app import cpuplek

                desired = cpuplek.lane_cap(self.database, desired)
            for slot in range(active_by_lane[lane], desired):
                worker_id = f"{lane}-{slot + 1}"
                job_id, reason = self.claim_next(lane, worker_id)
                if job_id is None:
                    note_lane_block(lane, reason)
                    break
                note_lane_block(lane, None)
                # Bij een totale grens van één zou een vaste CPU-eerstvolgorde
                # de GPU onbeperkt laten wachten zolang er CPU-opruimjobs zijn.
                # Onthoud daarom uitsluitend na een geslaagde claim welke baan
                # de volgende kans krijgt; gepauzeerde of lege banen blokkeren
                # de andere baan niet.
                self.next_lane = "gpu" if lane == "cpu" else "cpu"
                self.tasks[job_id] = asyncio.create_task(self._worker(job_id, worker_id))

    def terminate_active(self) -> list[int]:
        """SIGTERM naar de procesgroep van iedere lopende encode van deze planner.

        Mediaprocessen draaien in een eigen sessie; docker/tini stuurt het
        stopsignaal alleen naar de backend. Zonder dit wacht de stop op uren
        lopende encodes en wordt de container uiteindelijk hard gekild.
        """

        active = [job_id for job_id, task in self.tasks.items() if not task.done()]
        if not active:
            return []
        placeholders = ",".join("?" for _ in active)
        rows = self.database.fetch_all(
            f"SELECT id,proces_pid FROM job WHERE id IN ({placeholders})", tuple(active)
        )
        signalled = []
        for row in rows:
            pid = int(row["proces_pid"] or 0)
            if pid > 0 and terminate_process_group(pid):
                signalled.append(int(row["id"]))
        return signalled

    async def run_forever(self, interval: float = 30.0) -> None:
        """Tick bij een wekker (job/workers/stop) en anders hooguit elke ``interval`` s.

        Vroeger elke 0,5 s een databasequery, ook als er niets te doen was; dat was het
        grootste stationaire CPU-verbruik van de container."""

        reset_shutdown()
        recover_orphaned_jobs(self.database, self.settings)
        self._loop = asyncio.get_running_loop()
        self._wake = asyncio.Event()
        _PLANNERS.append(self)
        try:
            await self._loop_until_stopped(interval)
        finally:
            with suppress(ValueError):
                _PLANNERS.remove(self)

    async def _loop_until_stopped(self, interval: float) -> None:
        assert self._wake is not None
        while not self.stopping:
            try:
                await self.tick()
            except sqlite3.OperationalError as exc:
                # Een scan of API-mutatie kan heel kort de SQLite-schrijver
                # bezetten. Dat is een tijdelijke wachtrijtoestand en mag de
                # permanente planner nooit definitief uitschakelen.
                LOGGER.warning("Planner wacht op SQLite en probeert opnieuw: %s", exc)
            except Exception:
                # De planner is een permanente veiligheidscomponent. Een fout
                # in één tick wordt gelogd; een volgende tick kan daarna weer
                # veilig atomisch claimen.
                LOGGER.exception("Planner-tick stopte onverwacht; nieuwe poging volgt")
            if self.stopping:
                break
            with suppress(TimeoutError):
                await asyncio.wait_for(self._wake.wait(), timeout=interval)
            self._wake.clear()
        request_shutdown()
        try:
            if self.tasks:
                signalled = self.terminate_active()
                if signalled:
                    LOGGER.info(
                        "Stop: mediaprocessen van job(s) %s onderbroken; ze gaan terug in de "
                        "wachtrij",
                        signalled,
                    )
                await asyncio.gather(*self.tasks.values(), return_exceptions=True)
        finally:
            reset_shutdown()

    async def stop(self) -> None:
        self.stopping = True
