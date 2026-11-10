"""Voer één expliciet goedgekeurde job veilig van cache tot vervanging uit."""

from __future__ import annotations

import asyncio
import json
import logging
import os
import shutil
import signal
import sqlite3
from collections import deque
from collections.abc import Callable
from contextlib import suppress
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from app import noodstop
from app.bestandsmetadata import MetadataError, capture_metadata
from app.bronnen import SourceError, validate_registered_source
from app.config import Settings
from app.cpubudget import from_database as cpu_budget_from_database
from app.db import Database
from app.hostlimits import effective_memory, oom_kill_count
from app.integraties import refresh_after_replacement
from app.jobstatus import ACTIVE_SQL, STATUS_WACHT_OP_COMMIT
from app.kwaliteit import capture_comparison_frames, measure_quality
from app.media import commandos
from app.media.beslissen import STANDAARD, Besluit
from app.media.probe import probe
from app.mediaguard.handoff import stage_for_commit
from app.onderhoud import append_job_log, record_failure
from app.verificatie import VerificationResult, verify_result
from app.vervanging import ReplacementError, atomic_replace

LOGGER = logging.getLogger(__name__)


class ExecutionError(RuntimeError):
    """Een job is veilig gestopt; de bron is niet vervangen."""


OOM_MESSAGE = ("Encode gestopt: te weinig werkgeheugen (het systeem stopte ffmpeg). "
               "MediaGuard start minder encodes tegelijk en probeert het later opnieuw.")


def _killed_for_memory(returncode: int | None, oom_before: int) -> bool:
    if returncode not in {-signal.SIGKILL, 137} or _SHUTDOWN_REQUESTED:
        return False
    if oom_kill_count() > oom_before:
        return True
    memory = effective_memory()
    return memory.total > 0 and memory.available < memory.total * 0.05


MAX_RESTART_INTERRUPTIONS = 3


def cap_restart_loop(database: Database, job_id: int) -> bool:
    """Na ``MAX_RESTART_INTERRUPTIONS`` onderbrekingen door een (nette) engineherstart:
    'mislukt' met de echte aantallen in plaats van stil opnieuw in de wachtrij.

    Een nette stop zette de job altijd terug op 'wacht'; de begrenzing in het weesherstel
    zag die job daarna niet meer (alleen 'bezig'), zodat een job 116 keer opnieuw kon
    beginnen (rapport 3, R2)."""

    row = database.fetch_one(
        "SELECT j.pogingen,(SELECT COUNT(*) FROM job_poging p WHERE p.job_id=j.id "
        "AND p.foutsoort='ServiceStop') AS onderbroken FROM job j WHERE j.id=?",
        (job_id,),
    )
    if row is None or int(row["onderbroken"] or 0) < MAX_RESTART_INTERRUPTIONS:
        return False
    database.execute(
        "UPDATE job SET worker_id=NULL,proces_pid=NULL,cache_reservering=0 WHERE id=?",
        (job_id,),
    )
    record_failure(
        database, job_id,
        f"{int(row['onderbroken'])}× onderbroken doordat de AV1-engine herstartte "
        f"({int(row['pogingen'] or 0)} pogingen in totaal), zonder dat de encode afkwam. "
        "Bekijk het joblog; MediaGuard probeert het later één keer automatisch opnieuw.",
        "HerstartLus",
    )
    return True


def _handle_stall(database: Database, job_id: int, decision: Besluit,
                  exc: EncodeStalled) -> bool:
    """Vastloper vastleggen en de volgende stap kiezen (rapport 6, N2)."""
    from app import vastlopers

    stats = vastlopers.record(database, job_id, percent=exc.percent, diagnose=exc.diagnose)
    if stats["lane_count"] >= vastlopers.LANE_THRESHOLD:
        append_job_log(database, job_id, "waarschuwing",
                       vastlopers.lane_notice(database, stats["lane"]) or "")
    if stats["source_count"] >= 2:
        route, reason = vastlopers.plan_reroute(database, job_id, decision)
        if route == "opgeven":
            record_failure(database, job_id, f"{exc} — {reason}", vastlopers.GIVE_UP_TYPE)
            return False
        vastlopers.apply_reroute(database, job_id, decision, route, reason)
        append_job_log(database, job_id, "waarschuwing",
                       f"{vastlopers.REROUTE_REASON}: {reason}")
        _finish_attempt(database, job_id, "afgebroken", error=str(exc),
                        error_type="VastloperRouteWissel")
        with suppress(Exception):
            from app.planner import wake_planner

            wake_planner()
        return False
    record_failure(database, job_id, str(exc), "EncodeStalled")
    return False


def requeue_for_planned_stop(database: Database, job_id: int) -> None:
    """Geplande stop of update (verbeterpunt 30): terug in de wachtrij. De poging blijft in
    de geschiedenis als 'GeplandeStop' en telt niet mee voor de herstartlus-grens."""
    from app.geplandestop import PLANNED_STOP_ERROR, PLANNED_STOP_TYPE

    database.execute(
        """
        UPDATE job SET status='wacht',fout=?,foutsoort=NULL,proces_pid=NULL,worker_id=NULL,
                       cache_reservering=0,percent=0,klaar_op=NULL,noodstop_gepauzeerd=0
        WHERE id=?
        """,
        (PLANNED_STOP_ERROR, job_id),
    )
    _finish_attempt(database, job_id, "afgebroken", error=PLANNED_STOP_ERROR,
                    error_type=PLANNED_STOP_TYPE)


def requeue_for_emergency(database: Database, job_id: int) -> None:
    """Noodstop tijdens een stap of een herstart terwijl hij actief is: terug in de wachtrij
    zonder dat het als poging of onderbreking telt (rapport 6, N1)."""

    attempt = database.fetch_one("SELECT pogingen FROM job WHERE id=?", (job_id,))
    database.execute(
        """
        UPDATE job SET status='wacht',fout=?,foutsoort=NULL,proces_pid=NULL,worker_id=NULL,
                       cache_reservering=0,percent=0,klaar_op=NULL,noodstop_gepauzeerd=0,
                       pogingen=MAX(0,pogingen-1)
        WHERE id=?
        """,
        ("gepauzeerd (noodstop); start opnieuw zodra de noodstop is opgeheven", job_id),
    )
    if attempt is not None:
        database.execute(
            "DELETE FROM job_poging WHERE job_id=? AND poging=? AND status='bezig'",
            (job_id, int(attempt["pogingen"] or 0)),
        )


class ProcessInterrupted(RuntimeError):
    """Het mediaproces kreeg extern SIGTERM, bijvoorbeeld bij een herstart."""


class JobAborted(RuntimeError):
    """De gebruiker of MediaGuard vroeg om afbreken; geen encodefout."""


class EncodeTooSlow(ExecutionError):
    """Gemeten snelheid na 10 min: de rest duurt langer dan max_cpu_uren (gebruikersrapport,
    een 4K-film hield de enige CPU-plek ± 40 uur bezet)."""


class EncodePreempted(RuntimeError):
    """Lange CPU-job maakt overdag plaats voor kortere; gaat 's nachts verder."""


class EncodeStalled(ExecutionError):
    """Geen voortgang meer (rapport 6, N2); draagt de diagnose voor het joblog."""

    def __init__(self, message: str, *, percent: float, diagnose: str) -> None:
        super().__init__(message)
        self.percent = percent
        self.diagnose = diagnose


class QualityUnmeasured(ExecutionError):
    """Geen enkele kwaliteitsmeting lukte: het resultaat wordt niet geplaatst (verbeterpunt
    29). Vroeger ging het dan zonder waarschuwing door."""


class GpuHdrFallback(RuntimeError):
    """HDR10 via de GPU leverde geen gelijke metadata op: één keer opnieuw via de CPU."""


CPU_FALLBACK_PRESET_2160 = 10
GPU_HDR_FALLBACK_REDEN = "HDR-metadata na GPU-encode niet gelijk — opnieuw via CPU"
# Verificatiefouten die bij een GPU-HDR10-encode een CPU-herkansing rechtvaardigen.
_HDR_VERIFY_MARKERS = ("HDR10 mastering display", "kleur", "container- en bitstreamkleur")


def _gpu_hdr_fallback_allowed(row: Any, decision: Besluit) -> bool:
    return bool(decision.herstel_hdr_na and decision.encoder == "av1_qsv"
                and not int(row["gpu_hdr_teruggevallen"] or 0))


def requeue_hdr_on_cpu(database: Database, job_id: int, decision: Besluit, reason: str) -> None:
    """Zet dezelfde job terug in de wachtrij op de CPU (libsvtav1, HDR behouden), één keer."""

    cpu = decision.model_copy(update={
        "baan": "cpu", "encoder": "libsvtav1", "behoud_hdr": True, "herstel_hdr_na": False,
        "reden": f"{GPU_HDR_FALLBACK_REDEN} ({reason})"[:500],
    })
    row = database.fetch_one("SELECT besluit_id,mg_inst FROM job WHERE id=?", (job_id,))
    if row is None:
        return
    try:
        inst = json.loads(str(row["mg_inst"] or "{}"))
    except ValueError:
        inst = {}
    if not isinstance(inst, dict):
        inst = {}
    inst["gpu_voor_hdr10"] = False
    # Snellere SVT-preset voor deze CPU-herkansing (rapport 4, S2): een 50 GB-HDR-remux
    # haalde met preset 8 maar 0,06–0,15×; de GPU-poging was al het "snelle" pad.
    inst["preset_2160"] = max(int(inst.get("preset_2160") or 0), CPU_FALLBACK_PRESET_2160)
    with database._write_lock, database.connection() as connection:  # noqa: SLF001
        connection.execute("BEGIN IMMEDIATE")
        connection.execute(
            "UPDATE besluit SET baan='cpu',encoder='libsvtav1',reden=?,payload=? WHERE id=?",
            (cpu.reden, cpu.model_dump_json(), int(row["besluit_id"])),
        )
        connection.execute(
            """
            UPDATE job SET status='wacht',baan='cpu',mg_inst=?,gpu_hdr_teruggevallen=1,
                           fout=?,foutsoort=NULL,proces_pid=NULL,worker_id=NULL,
                           cache_reservering=0,percent=0,klaar_op=NULL,
                           resultaat_pad=NULL,resultaat_grootte=NULL
            WHERE id=?
            """,
            (json.dumps(inst, ensure_ascii=False), GPU_HDR_FALLBACK_REDEN, job_id),
        )
        connection.commit()


def _bitrate_floor_problem(source: dict[str, Any], result: dict[str, Any],
                           decision: Besluit) -> tuple[str, int, str] | None:
    """(klasse, ondergrens, melding) als een AV1-resultaat onder de ondergrens zit.

    Alleen bij een echte AV1-encode op de CPU (de GPU heeft een eigen VBR-minimum) en
    alleen als de bron zelf ruim boven de grens zat; een lage bron kan niet hoger uitkomen."""
    if decision.actie != "hercoderen" or decision.encoder not in {"libsvtav1", "libaom-av1"}:
        return None
    floor = commandos.bitrate_floor_kbps(source)
    result_kbps = int(result.get("kbps") or 0)
    source_kbps = int(source.get("kbps") or 0)
    if (floor <= 0 or result_kbps <= 0 or source_kbps < floor * 1.2 or result_kbps >= floor
            or float(source.get("duration") or 0) < FLOOR_MIN_DURATION):
        # korte clips (trailers, testbestanden) hebben geen representatieve bitrate
        return None
    klasse = commandos.resolution_class(int(source.get("height") or 0))
    soort = "HDR/DV" if commandos.is_hdr_source(source) else "SDR"
    message = (f"Videobitrate {nl_number(result_kbps / 1000)} Mbit/s onder de ondergrens "
               f"{nl_number(floor / 1000)} Mbit/s voor {klasse}p {soort}")
    return klasse, floor, message


FLOOR_MIN_DURATION = 300.0


class QualityFloorRetry(RuntimeError):
    """CPU-resultaat onder de bitrate-ondergrens: één herkansing met doelbitrate op de grens."""


QUALITY_FLOOR_REDEN = "Beeldkwaliteit onder de ondergrens — opnieuw met hogere bitrate"


class QualityVmafRetry(RuntimeError):
    """VMAF onder de grens: één herkansing met meer bits (GPU) of lagere CRF (CPU)."""


QUALITY_VMAF_REDEN = "VMAF te laag — opnieuw met hogere kwaliteit"
VMAF_RETRY_FACTOR = 1.5
CPU_RETRY_CRF_STEP = 6


def requeue_quality_vmaf(database: Database, job_id: int, decision: Besluit,
                         factor: float, reason: str) -> str:
    """Zelfde job één keer opnieuw: GPU met bitrate × 1,5 (tot het plafond); kan de GPU niet
    hoger, dan via de CPU; CPU-encodes met een CRF die 6 lager ligt. Geeft de route terug."""
    row = database.fetch_one("SELECT besluit_id,mg_inst FROM job WHERE id=?", (job_id,))
    if row is None:
        return "geen"
    try:
        inst = json.loads(str(row["mg_inst"] or "{}"))
    except ValueError:
        inst = {}
    if not isinstance(inst, dict):
        inst = {}
    route = "cpu"
    if decision.encoder == "av1_qsv":
        new_factor = max(1.0, factor) * VMAF_RETRY_FACTOR
        if new_factor <= VMAF_STEER_FACTOR[1]:
            inst["gpu_rate_factor"] = round(new_factor, 3)
            route = "gpu"
        elif not decision.dolby_vision:
            cpu = decision.model_copy(update={
                "baan": "cpu", "encoder": "libsvtav1", "behoud_hdr": True,
                "herstel_hdr_na": False, "reden": f"{QUALITY_VMAF_REDEN} (via CPU)"[:500]})
            database.execute(
                "UPDATE besluit SET baan='cpu',encoder='libsvtav1',reden=?,payload=? WHERE id=?",
                (cpu.reden, cpu.model_dump_json(), int(row["besluit_id"])))
            inst["gpu_voor_hdr10"] = False
            inst["gpu_voor_sdr_1080"] = False
            inst["gpu_voor_sdr_4k"] = False
    if route == "cpu":
        merged = {**STANDAARD, **inst}
        for klasse in ("720", "1080", "2160"):
            for key in (f"crf_{klasse}", f"crf_{klasse}_hdr"):
                if key in merged:
                    inst[key] = max(10, int(merged[key]) - CPU_RETRY_CRF_STEP)
    database.execute(
        """
        UPDATE job SET status='wacht',baan=?,mg_inst=?,kwaliteit_herkansd=1,fout=?,
                       foutsoort=NULL,proces_pid=NULL,worker_id=NULL,cache_reservering=0,
                       percent=0,klaar_op=NULL,resultaat_pad=NULL,resultaat_grootte=NULL
        WHERE id=?
        """,
        ("gpu" if route == "gpu" else "cpu", json.dumps(inst, ensure_ascii=False),
         f"{QUALITY_VMAF_REDEN} ({reason})"[:500], job_id),
    )
    return route


ANIME_SVT_PARAMS = ["tune=0", "enable-tf=0"]  # vlakke tekenfilmvlakken: geen temporele vervaging
REROUTE_REDEN = "Omgeleid naar de CPU"


def reroute_to_cpu(database: Database, job_id: int, decision: Besluit, source: str,
                   reason: str) -> str:
    """GPU haalt de kwaliteit niet (gebruikersrapport, anime): dezelfde job via de CPU
    (SVT-AV1, voor anime met tekenfilmvriendelijke instellingen) in plaats van een volledige
    GPU-encode die toch wordt afgekeurd. Telt als 'omgeleid', niet als fout. Kan de CPU het
    niet overnemen (DV 8.1 → HDR10 bestaat alleen op de GPU), dan wordt de job overgeslagen.
    Geeft 'cpu' of 'overgeslagen' terug."""
    from app import routeleer

    count = routeleer.record_gpu_rejection(database, source, reason)
    row = database.fetch_one("SELECT besluit_id,mg_inst,omgeleid FROM job WHERE id=?", (job_id,))
    if row is None:
        return "geen"
    if decision.dv_naar_hdr10 or decision.dolby_vision or int(row["omgeleid"] or 0):
        database.execute(
            "UPDATE job SET status='overgeslagen',fout=?,foutsoort='KwaliteitGpu',klaar_op=?,"
            "proces_pid=NULL,resultaat_pad=NULL,resultaat_grootte=NULL WHERE id=?",
            (f"{reason}; geen andere route — origineel blijft staan"[:500],
             datetime.now(UTC).isoformat(), job_id))
        return "overgeslagen"
    try:
        inst = json.loads(str(row["mg_inst"] or "{}"))
    except ValueError:
        inst = {}
    if not isinstance(inst, dict):
        inst = {}
    inst.update({"gpu_voor_hdr10": False, "gpu_voor_sdr_1080": False, "gpu_voor_sdr_4k": False})
    if routeleer.is_anime(source):
        inst["svt_extra"] = list(ANIME_SVT_PARAMS)
    note = (f" (reeks {count}× afgekeurd op de GPU: de rest gaat direct via de CPU)"
            if count >= routeleer.GPU_REJECT_LIMIT else "")
    cpu = decision.model_copy(update={
        "baan": "cpu", "encoder": "libsvtav1", "behoud_hdr": True, "herstel_hdr_na": False,
        "reden": f"{REROUTE_REDEN}: {reason}{note}"[:500]})
    database.execute(
        "UPDATE besluit SET baan='cpu',encoder='libsvtav1',reden=?,payload=? WHERE id=?",
        (cpu.reden, cpu.model_dump_json(), int(row["besluit_id"])))
    database.execute(
        """
        UPDATE job SET status='wacht',baan='cpu',mg_inst=?,omgeleid=1,fout=?,foutsoort=NULL,
                       proces_pid=NULL,worker_id=NULL,cache_reservering=0,percent=0,
                       klaar_op=NULL,resultaat_pad=NULL,resultaat_grootte=NULL
        WHERE id=?
        """,
        (json.dumps(inst, ensure_ascii=False), f"{REROUTE_REDEN}: {reason}{note}"[:500], job_id))
    return "cpu"


def reroute_failed_gpu_quality_jobs(database: Database, limit: int = 200) -> int:
    """Eenmalig/periodiek: GPU-jobs die eerder op de kwaliteitsmeting 'mislukt' raakten (vóór
    de omleiding bestond) krijgen alsnog één kans via de CPU, en tellen mee voor het leren per
    reeks. Geeft het aantal omgeleide jobs terug."""
    rows = database.fetch_all(
        """
        SELECT j.id,f.pad,b.payload FROM job j
        JOIN bestand f ON f.id=j.bestand_id JOIN besluit b ON b.id=j.besluit_id
        WHERE j.status='mislukt' AND j.baan='gpu' AND COALESCE(j.omgeleid,0)=0
          AND j.mg_bron IS NOT NULL
          AND (j.fout LIKE 'Kwaliteit te laag%' OR j.fout LIKE '%VMAF%')
        ORDER BY j.id LIMIT ?
        """, (limit,))
    moved = 0
    for row in rows:
        try:
            decision = Besluit.model_validate_json(str(row["payload"] or "{}"))
        except ValueError:
            continue
        if decision.dolby_vision or decision.dv_naar_hdr10:
            continue
        if reroute_to_cpu(database, int(row["id"]), decision, str(row["pad"]),
                          "GPU haalde VMAF niet (eerdere poging)") == "cpu":
            moved += 1
    if moved:
        LOGGER.info("%s eerder afgekeurde GPU-encodes opnieuw via de CPU", moved)
    return moved


class GpuQualityReroute(RuntimeError):
    """De GPU haalt de kwaliteit niet: job gaat via de CPU (geen fout)."""


def requeue_quality_floor(database: Database, job_id: int, klasse: str, floor: int,
                          reason: str) -> None:
    """Zelfde job opnieuw in de wachtrij met een doelbitrate op de ondergrens (één keer)."""

    row = database.fetch_one("SELECT mg_inst FROM job WHERE id=?", (job_id,))
    if row is None:
        return
    try:
        inst = json.loads(str(row["mg_inst"] or "{}"))
    except ValueError:
        inst = {}
    if not isinstance(inst, dict):
        inst = {}
    inst[f"doel_bitrate_{klasse}_kbps"] = int(floor * 1.1)
    database.execute(
        """
        UPDATE job SET status='wacht',mg_inst=?,kwaliteit_herkansd=1,fout=?,foutsoort=NULL,
                       proces_pid=NULL,worker_id=NULL,cache_reservering=0,percent=0,
                       klaar_op=NULL,resultaat_pad=NULL,resultaat_grootte=NULL
        WHERE id=?
        """,
        (json.dumps(inst, ensure_ascii=False), f"{QUALITY_FLOOR_REDEN} ({reason})"[:500],
         job_id),
    )


def _abort_requested(database: Database, job_id: int) -> bool:
    row = database.fetch_one("SELECT afbreken_gevraagd FROM job WHERE id=?", (job_id,))
    return row is not None and bool(int(row[0] or 0))


def _step_failed(database: Database, job_id: int, message: str) -> Exception:
    """Een mislukte stap na een afbreekverzoek is een afbreking, geen fout. Anders een fout
    met de echte oorzaak (exitcode en laatste foutregel), niet alleen 'encoderproces faalde'
    (verbeterpunt 28)."""

    if _abort_requested(database, job_id):
        return JobAborted()
    failure = LAST_PROCESS_FAILURE.pop(job_id, None)
    return ExecutionError(describe_process_failure(message, failure) if failure else message)


# Laatste mislukte mediaproces per job: programma, exitcode en de laatste uitvoerregels.
LAST_PROCESS_FAILURE: dict[int, dict[str, Any]] = {}
_ERROR_LINE_MARKERS = ("error", "fail", "invalid", "cannot", "can't", "unable", "not found",
                       "denied", "fatal", "corrupt", "no such", "unknown", "not permitted",
                       "out of memory", "no space")
# (zoektermen in de foutregels, Nederlandse oorzaak) — eerste treffer wint.
_FAILURE_CAUSES: tuple[tuple[tuple[str, ...], str], ...] = (
    (("no space left on device", "enospc", "disk quota exceeded"),
     "geen ruimte meer op de cacheschijf"),
    (("cannot allocate memory", "out of memory", "enomem", "failed to allocate"),
     "te weinig werkgeheugen"),
    (("permission denied", "operation not permitted", "read-only file system"),
     "geen rechten op een bestand of map"),
    (("input/output error", "i/o error"), "lees- of schrijffout op de schijf"),
    (("invalid data found when processing input", "moov atom not found",
      "ebml header parsing failed", "error while decoding", "corrupt", "invalid nal unit",
      "error splitting the input", "truncat"),
     "de bron is beschadigd of onleesbaar"),
    (("unknown encoder", "encoder not found", "unrecognized option", "option not found",
      "no such filter", "error setting option", "unknown decoder"),
     "ffmpeg mist een onderdeel of kent een optie niet"),
    (("device creation failed", "failed to initialise vaapi", "failed to initialize vaapi",
      "error creating a mfx session", "mfx_err", "error initializing an internal mfx",
      "failed to create a va", "/dev/dri", "hardware device", "error during encoding: qsv",
      "error submitting the frame for encoding"),
     "de GPU weigerde de encode (QSV/VA-API)"),
    (("no such file or directory",), "een bestand ontbreekt"),
)
_SIGNAL_CAUSES = {signal.SIGSEGV: "het programma crashte (segmentatiefout)",
                  signal.SIGABRT: "het programma brak zichzelf af",
                  signal.SIGKILL: "het programma werd hard gestopt",
                  signal.SIGBUS: "het programma crashte (busfout)"}


def describe_process_failure(step: str, failure: dict[str, Any]) -> str:
    """'encoderproces faalde (ffmpeg, exitcode 1): de bron is beschadigd of onleesbaar —
    "Invalid data found when processing input"' in plaats van alleen de stapnaam."""

    tool = str(failure.get("tool") or "mediaproces")
    code = failure.get("returncode")
    lines = [str(x).strip() for x in failure.get("lines") or [] if str(x).strip()]
    errors = [x for x in lines if any(m in x.lower() for m in _ERROR_LINE_MARKERS)]
    cause = None
    for markers, text in _FAILURE_CAUSES:
        if any(m in line.lower() for line in errors for m in markers):
            cause = text
            break
    if cause is None and isinstance(code, int) and code < 0:
        with suppress(ValueError):
            cause = _SIGNAL_CAUSES.get(signal.Signals(-code))
        cause = cause or f"het programma stopte door signaal {-code}"
    code_text = f"exitcode {code}" if code is not None else "geen exitcode"
    message = f"{step} ({tool}, {code_text})"
    if cause:
        message += f": {cause}"
    last = (errors or lines)[-1:] if (errors or lines) else []
    if last:
        message += f" — laatste melding: \"{last[0][:240]}\""
    elif not cause:
        message += ": geen foutmelding in het log; zie het joblog"
    return message[:900]


VerificationHook = Callable[[VerificationResult], VerificationResult]

# Gezet door de planner bij een nette stop (SIGTERM van systemd/docker). Daarna
# start geen nieuw mediaproces meer en telt een door SIGTERM gestopt proces als
# onderbroken (job terug in de wachtrij) in plaats van als encodefout.
_SHUTDOWN_REQUESTED = False
# FFmpeg vangt SIGTERM/SIGINT zelf af, meldt dit en stopt met exitcode 255.
_SIGNAL_EXIT_MARKERS = ("received signal 15", "received signal 2")
# mkvmerge: 1 = waarschuwingen maar volledige uitvoer; 2 = fout.
_TOOL_OK_CODES = {"mkvmerge": {0, 1}}


def request_shutdown() -> None:
    global _SHUTDOWN_REQUESTED
    _SHUTDOWN_REQUESTED = True


def shutdown_requested() -> bool:
    return _SHUTDOWN_REQUESTED


def reset_shutdown() -> None:
    """Alleen voor tests en een nieuwe app-levenscyclus in hetzelfde proces."""

    global _SHUTDOWN_REQUESTED
    _SHUTDOWN_REQUESTED = False


def terminate_process_group(pid: int, sig: signal.Signals = signal.SIGTERM) -> bool:
    """Stuur een signaal naar de eigen sessie van een mediaproces; weg = geen fout."""

    try:
        os.killpg(pid, sig)
    except (ProcessLookupError, PermissionError):
        return False
    if sig in (signal.SIGTERM, signal.SIGINT):
        # Een door de noodstop bevroren proces (SIGSTOP) verwerkt SIGTERM pas na SIGCONT.
        with suppress(ProcessLookupError, PermissionError):
            os.killpg(pid, signal.SIGCONT)
    return True


def _promote_partial(partial: Path, result: Path) -> None:
    """Flush een volledig encoderresultaat en maak het lokaal zichtbaar."""

    with partial.open("rb") as handle:
        os.fsync(handle.fileno())
    partial.replace(result)


def _kwaliteitsadvies(
    source_size: int,
    result_size: int,
    source_probe: dict[str, Any],
    profile: dict[str, Any],
    decision: Besluit,
) -> str:
    """Geef een herhaalbaar, begrensd advies zonder ooit zelf profieldata te wijzigen."""

    if decision.actie == "opschonen":
        return (
            "Resultaat was groter dan de bron. Advies: zo is het goed; de bron is "
            "veilig behouden en alleen de tijdelijke remux is verwijderd."
        )
    hoogte = int(source_probe.get("height") or 0)
    klasse = "2160" if hoogte > 1200 else "1080" if hoogte > 800 else "720"
    target = int(profile.get(f"doel_bitrate_{klasse}_kbps") or 0)
    if target > 0 and result_size > source_size:
        verhouding = source_size / max(result_size, 1)
        voorstel = max(500, int(target * verhouding * 0.9))
        return (
            f"Doelbitrate {target} kbps leverde geen besparing op. "
            f"AI-advies: probeer ongeveer {voorstel} kbps of laat 0 (CRF) staan; "
            "deze job is niet automatisch opnieuw gestart."
        )
    if int(source_probe.get("dv_profile") or 0):
        return (
            "DV-resultaat werd groter dan de bron. Advies: zo is het goed; "
            "behoud de bron of verlaag de doelbitrate handmatig voordat je opnieuw test."
        )
    return (
        "De gekozen kwaliteitsinstelling leverde geen kleinere video op. "
        "Advies: verhoog CRF met 2–3 of laat de bron ongewijzigd; automatisch overslaan is veilig."
    )


def _finish_attempt(
    database: Database,
    job_id: int,
    status: str,
    *,
    error: str | None = None,
    error_type: str | None = None,
) -> None:
    row = database.fetch_one("SELECT pogingen FROM job WHERE id=?", (job_id,))
    if row is None or int(row["pogingen"]) < 1:
        return
    database.execute(
        """
        UPDATE job_poging
        SET status=?,fout=COALESCE(?,fout),foutsoort=COALESCE(?,foutsoort),klaar_op=?
        WHERE job_id=? AND poging=?
        """,
        (
            status,
            error,
            error_type,
            datetime.now(UTC).isoformat(),
            job_id,
            int(row["pogingen"]),
        ),
    )


DEFAULT_ENCODE_NICE = 10
PHASE_LABEL = {
    "analyse": "Bron analyseren", "dv_voorbewerking": "Dolby Vision voorbewerken",
    "encode": "Encoderen", "opschonen": "Sporen opschonen",
    "proefstuk": "Proefstuk van 60 s (verwachte besparing)",
    "hdr_terugzetten": "HDR-metadata terugzetten", "verifieren": "Resultaat controleren",
    "kwaliteit_meten": "Kwaliteit meten (3 steekproeven)",
    "klaarzetten": "Klaarzetten voor MediaGuard", "vervangen": "Origineel vervangen",
}


def _low_io_priority(arguments: list[str]) -> list[str]:
    """Mediaprocessen met lage I/O-prioriteit (best-effort, laagste niveau), zodat lezen
    van een 60 GB-bron de web-UI en de database niet laat haperen."""
    # alleen als het programma zelf bestaat: anders moet de gewone "programma ontbreekt"-fout
    # blijven komen, niet een vage exitcode van ionice
    if not arguments or Path(arguments[0]).name in {"ionice", "nice"} or not shutil.which(
            arguments[0]):
        return arguments
    prefix: list[str] = []
    # nice via de opdrachtregel: geldt vanaf de eerste instructie voor álle threads.
    # os.setpriority() ná de start raakt in Linux alleen de hoofdthread; threads die
    # ffmpeg/SVT/QSV al hadden gemaakt bleven op prioriteit 0 (rapport 3, R2).
    if shutil.which("nice"):
        prefix += ["nice", "-n", str(DEFAULT_ENCODE_NICE)]
    if shutil.which("ionice"):
        prefix += ["ionice", "-c", "2", "-n", "7"]
    return [*prefix, *arguments]


def _apply_lane_nice(database: Database, job_id: int, pid: int) -> None:
    """Pas de per-baan nice-waarde (worker_config.nice) toe op het mediaproces.

    Direct na de start, vóór FFmpeg/SVT zijn encodethreads aanmaakt; die erven
    de prioriteit. Alleen 0..19 (lager zetten vereist CAP_SYS_NICE).
    """

    row = database.fetch_one(
        "SELECT w.nice FROM job j JOIN worker_config w ON w.baan=j.baan WHERE j.id=?",
        (job_id,),
    )
    nice = int(row["nice"] or 0) if row is not None else 0
    if nice == 0:
        # Standaard iets lager dan de web-UI en de API: encodes mogen alle vrije rekenkracht
        # gebruiken, maar pagina's en levenscontroles blijven vlot (rapport 2, N1/N25).
        nice = DEFAULT_ENCODE_NICE
    if 0 < nice <= 19:
        with suppress(OSError):
            os.setpriority(os.PRIO_PROCESS, pid, nice)


def _progress_number(value: str, suffix: str = "") -> float | None:
    text = value.removesuffix(suffix).strip()
    if text in {"", "N/A"}:
        return None
    try:
        return float(text)
    except ValueError:
        return None


def create_job_from_decision(database: Database, decision_id: int) -> int:
    row = database.fetch_one(
        """
        SELECT b.*,f.id AS bestand_id,f.nlink
        FROM besluit b JOIN bestand f ON f.id=b.bestand_id WHERE b.id=?
        """,
        (decision_id,),
    )
    if row is None:
        raise ExecutionError(f"Besluit {decision_id} bestaat niet")
    if row["status"] != "goedgekeurd":
        raise ExecutionError("Besluit moet eerst expliciet zijn goedgekeurd")
    if row["actie"] not in {"hercoderen", "opschonen"}:
        raise ExecutionError(f"Actie {row['actie']} is geen encodejob")
    if int(row["nlink"]) > 1 and not int(row["hardlink_bevestigd"]):
        raise ExecutionError("Hardlink is niet per bestand bevestigd")
    existing = database.fetch_one("SELECT id FROM job WHERE besluit_id=?", (decision_id,))
    if existing is not None:
        return int(existing["id"])
    active_for_file = database.fetch_one(
        f"SELECT id FROM job WHERE bestand_id=? AND status IN {ACTIVE_SQL} ORDER BY id LIMIT 1",
        (int(row["bestand_id"]),),
    )
    if active_for_file is not None:
        # De unieke index is de laatste racebeveiliging; deze expliciete route
        # houdt herhaalde UI-goedkeuring idempotent en begrijpelijk.
        database.execute(
            "UPDATE besluit SET status='ingepland' WHERE id=?", (decision_id,)
        )
        return int(active_for_file["id"])
    try:
        job_id = database.execute_insert(
            """
            INSERT INTO job(besluit_id,bestand_id,baan,status,prioriteit)
            VALUES (?, ?, ?, 'wacht', 0)
            """,
            (decision_id, int(row["bestand_id"]), str(row["baan"] or "cpu")),
        )
    except sqlite3.IntegrityError as exc:
        active_for_file = database.fetch_one(
            f"SELECT id FROM job WHERE bestand_id=? AND status IN {ACTIVE_SQL} "
            "ORDER BY id LIMIT 1",
            (int(row["bestand_id"]),),
        )
        if active_for_file is None:
            raise ExecutionError("Job kon niet uniek worden ingepland") from exc
        return int(active_for_file["id"])
    database.execute("UPDATE besluit SET status='ingepland' WHERE id=?", (decision_id,))
    return job_id


# Fase per lopende job (analyse, encode, verificatie, ...) voor de live-voortgang in de UI.
# Alleen in het geheugen: het live-kanaal (app/live.py) draait in hetzelfde proces, en na een
# herstart bestaat de lopende encode toch niet meer.
JOB_PHASES: dict[int, tuple[str, str]] = {}
# Onderdeel binnen een fase ("Proefstuk 2/3", "VMAF meten 1/3"), rapport 7 A4.
JOB_PHASE_DETAIL: dict[int, str] = {}


def _set_phase(database: Database, job_id: int, phase: str) -> None:
    """Nieuwe fase: voortgang opnieuw vanaf 'onbekend' (NULL), niet vanaf het vorige
    percentage. De UI toont dan een lopende balk tot de eerste meting binnen is."""
    JOB_PHASES[job_id] = (phase, datetime.now(UTC).isoformat())
    JOB_PHASE_DETAIL.pop(job_id, None)
    # Elke fase staat in het joblog: ook vóór de eerste ffmpeg-regel (een 60 GB-bron
    # analyseren duurt minuten) is zichtbaar wat er gebeurt.
    with suppress(sqlite3.OperationalError):
        append_job_log(database, job_id, "info", f"Fase: {PHASE_LABEL.get(phase, phase)}")
    with suppress(sqlite3.OperationalError):
        database.execute(
            "UPDATE job SET percent=NULL,fps=NULL,speed=NULL,eta_seconden=NULL WHERE id=?",
            (job_id,),
        )


def _set_detail(database: Database, job_id: int, text: str,
                percent: float | None = None) -> None:
    """Zichtbare stap binnen een fase, met (optioneel) een percentage voor de balk."""
    JOB_PHASE_DETAIL[job_id] = text
    with suppress(sqlite3.OperationalError):
        append_job_log(database, job_id, "info", text)
    if percent is not None:
        with suppress(sqlite3.OperationalError):
            database.execute("UPDATE job SET percent=? WHERE id=?",
                             (round(max(0.0, min(100.0, percent)), 1), job_id))


ABORT_CHECK_SECONDS = 2.0
PROGRESS_WRITE_SECONDS = 5.0
PROGRESS_LOG_SECONDS = 30.0


def _fmt_eta(seconds: int | None) -> str:
    if seconds is None:
        return "onbekend"
    hours, rest = divmod(int(seconds), 3600)
    return f"{hours}u {rest // 60}m" if hours else f"{rest // 60}m {rest % 60}s"


def progress_log_line(percent: float, speed: float | None, eta: int | None) -> str:
    """Leesbare voortgangsregel voor het joblog (elke ~30 s), zodat te zien is of een
    lange 4K-encode loopt of hangt."""
    speed_text = f"{speed:.2f}".replace(".", ",") + "×" if speed is not None else "—"
    return (f"Voortgang {nl_percent(percent)} · snelheid {speed_text} · "
            f"nog {_fmt_eta(eta)}")


# Fasen waarin een ETA en job-percentage misleiden: het proces draait op een stukje van de
# bron (proefstuk, kwaliteitsmeting), niet op het hele bestand.
NO_ETA_PHASES = frozenset({"proefstuk", "kwaliteit_meten"})


async def _run_process(
    database: Database,
    job_id: int,
    arguments: list[str],
    log_path: Path,
    duration: float,
) -> bool:
    if _SHUTDOWN_REQUESTED:
        raise ProcessInterrupted("mediaproces niet gestart: service- of systeemstop")
    if noodstop.active():
        # Tussen twee stappen van een job (bv. na het proefstuk): niet aan de volgende
        # stap beginnen; de job gaat terug in de wachtrij zonder dat het een poging kost.
        raise ProcessInterrupted("noodstop actief: volgende stap niet gestart")
    oom_before = oom_kill_count()
    from app.cpuconfine import confine_prefix, encode_percent, job_part

    # Harde CPU-grens: elk mediaproces (en al zijn threads) op de encodekernen (rapport 5, T1);
    # bij meer CPU-plekken krijgt elke plek zijn eigen deel van die kernen (rapport 6, N2).
    process = await asyncio.create_subprocess_exec(
        *(confine_prefix(encode_percent(database), job_part(database, job_id))
          if arguments and shutil.which(arguments[0]) else []),
        *_low_io_priority(arguments),
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.STDOUT,
        start_new_session=True,
    )
    await asyncio.to_thread(
        database.execute,
        "UPDATE job SET proces_pid=?,commando=? WHERE id=?",
        (process.pid, json.dumps(arguments), job_id),
    )
    _apply_lane_nice(database, job_id, process.pid)
    loop = asyncio.get_running_loop()
    last_progress_write = loop.time()
    last_abort_check = 0.0
    last_progress_log = loop.time()
    last_database_warning = 0.0
    progress: dict[str, str] = {}
    best_percent = 0.0
    signal_exit = False
    # Vastloper-detectie (rapport 4, S2): geen voortgang in STALL_SECONDS = stoppen met een
    # duidelijke oorzaak in plaats van uren 'bezig' zonder resultaat.
    watch_stall = Path(arguments[0]).name == "ffmpeg" and "-progress" in arguments
    last_out_us = -1.0
    last_advance = loop.time()
    frozen = False
    recent_lines: deque[str] = deque(maxlen=STALL_TAIL_LINES)
    cpu_mark = _group_cpu_seconds(process.pid)
    stall_extensions = 0
    # Gebruikersrapport (DV op 0,0 %): stappen zonder -progress (dovi_tool, mkvmerge,
    # ffmpeg-extractie) worden bewaakt op gelezen/geschreven bytes; die tellen ook als
    # activiteit voor ffmpeg-encodes die nog geen beeld hebben weggeschreven.
    watch_io = not watch_stall
    io_mark = _group_io_bytes(process.pid)
    started_at = loop.time()
    lane, source_bytes = "cpu", 0
    with suppress(Exception):
        info_row = database.fetch_one(
            "SELECT j.baan,f.grootte FROM job j JOIN bestand f ON f.id=j.bestand_id "
            "WHERE j.id=?", (job_id,))
        if info_row is not None:
            lane, source_bytes = str(info_row["baan"] or "cpu"), int(info_row["grootte"] or 0)
    last_waiting_check = 0.0
    last_io_check = loop.time()
    waiting_cpu = 0
    # Snelheidsmeting voor 'te traag': eigen meting vanaf (tijd, positie). Na een noodstop
    # opnieuw beginnen: ffmpeg rekent de bevroren tijd mee in zijn 'speed', waardoor een
    # gezonde encode na een lange noodstop als 'te traag' werd overgeslagen (verbeterpunt 28).
    slow_mark: tuple[float, float] | None = None
    frozen_total = 0.0
    frozen_since = 0.0
    LAST_PROCESS_FAILURE.pop(job_id, None)

    async def db_call(function: Any, *args: Any) -> Any:
        """SQLite buiten de event loop. Onder schijfdruk (zes 4K-bronnen tegelijk) kan
        een commit seconden duren; de API, /livez en de andere encodes mogen daar niet
        op wachten. Een tijdelijk SQLite-probleem doodt de encode nooit."""
        nonlocal last_database_warning
        try:
            return await asyncio.to_thread(function, *args)
        except sqlite3.OperationalError as exc:
            now = loop.time()
            if now - last_database_warning >= 60:
                LOGGER.warning("Job %s blijft encoderen tijdens tijdelijk SQLite-probleem: %s",
                               job_id, exc)
                last_database_warning = now
            return None

    with log_path.open("a", encoding="utf-8") as log_handle:
        while True:
            try:
                line_bytes = (
                    await asyncio.wait_for(process.stdout.readline(), timeout=0.25)
                    if process.stdout
                    else b""
                )
            except TimeoutError:
                line_bytes = b""
            if line_bytes:
                line = line_bytes.decode("utf-8", errors="replace").rstrip()
                log_handle.write(line + "\n")
                if any(marker in line for marker in _SIGNAL_EXIT_MARKERS):
                    signal_exit = True
                key, separator, value = line.partition("=")
                is_progress = bool(separator) and " " not in key and key.replace(
                    "_", "").isalnum()
                if line and not is_progress:
                    recent_lines.append(line)
                if line and not is_progress and not line.startswith(
                        ("frame=", "out_time_", "fps=", "speed=")):
                    # Het volledige proceslog staat al veilig op schijf.
                    await db_call(append_job_log, database, job_id, "info", line)
                if separator and key in {"out_time_ms", "fps", "speed", "bitrate", "total_size"}:
                    progress[key] = value
            elif process.returncode is not None:
                # Een snel falend proces kan al gestopt zijn terwijl er nog
                # fatale regels in de pipe staan. Lees tot EOF, anders blijft
                # precies de nuttigste laatste fout uit het joblog.
                break
            now = loop.time()
            # Noodstop (rapport 6, N1): bevriezen met SIGSTOP, na opheffen SIGCONT. Zolang hij
            # bevroren is, staat de vastloperbewaking stil (geen 'geen voortgang'-fout).
            if noodstop.active() and process.returncode is None:
                if not frozen:
                    frozen = terminate_process_group(process.pid, signal.SIGSTOP)
                    if frozen:
                        frozen_since = now
                        await db_call(database.execute,
                                      "UPDATE job SET noodstop_gepauzeerd=1 WHERE id=?",
                                      (job_id,))
                        await db_call(append_job_log, database, job_id, "waarschuwing",
                                      "Gepauzeerd door de noodstop; gaat verder na opheffen")
                last_advance = now
            elif frozen:
                terminate_process_group(process.pid, signal.SIGCONT)
                frozen = False
                last_advance = now
                frozen_total += max(0.0, now - frozen_since)
                slow_mark = None  # snelheid opnieuw meten vanaf nu
                await db_call(database.execute,
                              "UPDATE job SET noodstop_gepauzeerd=0 WHERE id=?", (job_id,))
                await db_call(append_job_log, database, job_id, "info",
                              "Noodstop opgeheven; encode gaat verder")
            if now - last_abort_check >= ABORT_CHECK_SECONDS:
                last_abort_check = now
                abort = await db_call(
                    database.fetch_one, "SELECT afbreken_gevraagd FROM job WHERE id=?",
                    (job_id,))
                if abort is not None and int(abort[0]):
                    # Het proces kan net zelf gestopt zijn (race): dan is er niets te
                    # signaleren en blijft het gewoon een afbreking.
                    terminate_process_group(process.pid, signal.SIGTERM)
                    with suppress(TimeoutError):
                        await asyncio.wait_for(process.wait(), timeout=10)
                    if process.returncode is None:
                        terminate_process_group(process.pid, signal.SIGKILL)
                        await process.wait()
                    return False
            if now - last_progress_write >= PROGRESS_WRITE_SECONDS and progress:
                out_microseconds = _progress_number(progress.get("out_time_ms", ""))
                speed = _progress_number(progress.get("speed", ""), "x")
                bitrate = _progress_number(progress.get("bitrate", ""), "kbits/s")
                fps = _progress_number(progress.get("fps", ""))
                if out_microseconds is not None and out_microseconds > last_out_us:
                    last_out_us = out_microseconds
                    last_advance = now
                    cpu_mark = _group_cpu_seconds(process.pid)
                    stall_extensions = 0
                if out_microseconds is not None:
                    out_seconds = out_microseconds / 1_000_000
                    percent = min(100.0, out_seconds / duration * 100) if duration > 0 else 0.0
                    # binnen één fase nooit terug (ffmpeg meldt soms kort een lagere out_time)
                    percent = best_percent = max(best_percent, percent)
                    eta = (
                        int(max(0.0, duration - out_seconds) / speed)
                        if speed is not None and speed > 0
                        else None
                    )
                    current = JOB_PHASES.get(job_id)
                    if current is not None and current[0] in NO_ETA_PHASES:
                        # Proefstuk/meten (rapport 8, B5): geen ETA en geen job-percentage
                        # (gaf "0,0 % · nog 453 uur"); de UI toont fase_detail met deelstap.
                        eta, percent_shown = None, None
                    else:
                        percent_shown = percent
                    await db_call(
                        database.execute,
                        "UPDATE job SET percent=?,fps=?,speed=?,bitrate_kbps=?,eta_seconden=? "
                        "WHERE id=?",
                        (percent_shown, fps, speed, bitrate, eta, job_id),
                    )
                    if now - last_progress_log >= PROGRESS_LOG_SECONDS:
                        last_progress_log = now
                        text = progress_log_line(percent, speed, eta)
                        log_handle.write(text + "\n")
                        log_handle.flush()
                        await db_call(append_job_log, database, job_id, "info", text)
                last_progress_write = now
            if now - last_io_check >= PROGRESS_WRITE_SECONDS:
                last_io_check = now
                io_now = await asyncio.to_thread(_group_io_bytes, process.pid)
                if io_now > io_mark + IO_ACTIVITY_BYTES:
                    io_mark = io_now
                    last_advance = now
                    if watch_io and source_bytes > 0:
                        done_gb, total_gb = io_now / 1024**3, source_bytes / 1024**3
                        await db_call(_set_detail, database, job_id,
                                      f"Voorbewerking: {nl_number(done_gb)} van "
                                      f"{nl_number(total_gb)} GB verwerkt",
                                      min(99.0, 100.0 * io_now / max(source_bytes, 1)))
            current_phase = (JOB_PHASES.get(job_id) or ("",))[0]
            out_us = _progress_number(progress.get("out_time_ms", "")) if progress else None
            if (lane == "cpu" and current_phase == "encode" and process.returncode is None
                    and not frozen and out_us is not None and slow_mark is None):
                slow_mark = (now, out_us / 1_000_000)
            if (lane == "cpu" and current_phase == "encode" and process.returncode is None
                    and not frozen and slow_mark is not None and out_us is not None
                    and now - slow_mark[0] >= SLOW_CHECK_SECONDS):
                # eigen meting over de tijd dat het proces echt liep (zonder noodstop)
                speed_now = (out_us / 1_000_000 - slow_mark[1]) / (now - slow_mark[0])
                if speed_now > 0 and duration > 0:
                    remaining = max(0.0, duration - out_us / 1_000_000) / speed_now
                    from app import afstelling

                    limit_h = float(afstelling.get("max_cpu_uren"))
                    if remaining > limit_h * 3600:
                        message = (f"te traag voor de CPU: nog ± {nl_number(remaining / 3600)} "
                                   f"uur bij {nl_number(speed_now)}× (grens {nl_number(limit_h)} "
                                   "uur) — overgeslagen, origineel blijft")
                        await _stop_group(process)
                        raise EncodeTooSlow(message)
                    if now - last_waiting_check >= 300:
                        last_waiting_check = now
                        waiting = await db_call(
                            database.fetch_one,
                            "SELECT COUNT(*) FROM job WHERE baan='cpu' AND status='wacht' "
                            "AND COALESCE(alleen_nacht,0)=0", ())
                        waiting_cpu = int(waiting[0]) if waiting is not None else 0
                    from app.planner import in_night_window

                    ran = now - started_at - frozen_total
                    if (afstelling.get("lange_jobs_alleen_nacht") and not in_night_window()
                            and ran < PREEMPT_MAX_ELAPSED
                            and remaining + ran
                            > float(afstelling.get("lange_job_uren")) * 3600
                            and waiting_cpu >= PREEMPT_MIN_WAITING):
                        message = (f"lange encode (nog ± {nl_number(remaining / 3600)} uur) "
                                   f"maakt overdag plaats voor {waiting_cpu} kortere jobs; gaat "
                                   "'s nachts verder")
                        await _stop_group(process)
                        raise EncodePreempted(message)
            if ((watch_stall or watch_io) and process.returncode is None
                    and now - last_advance >= stall_seconds()):
                window = now - last_advance
                cpu_now = _group_cpu_seconds(process.pid)
                busy_cores = (cpu_now - cpu_mark) / window if window > 0 else 0.0
                if busy_cores >= STALL_BUSY_CORES and stall_extensions < STALL_MAX_EXTENSIONS:
                    # Het proces rekent nog (bv. 4K-HDR met lange lookahead op een gedeelde
                    # kernenset) maar heeft nog geen nieuw beeld weggeschreven: dat is traag,
                    # niet vast. Langer wachten in plaats van het werk weggooien.
                    stall_extensions += 1
                    last_advance = now
                    cpu_mark = cpu_now
                    await db_call(
                        append_job_log, database, job_id, "waarschuwing",
                        f"Geen nieuwe uitvoer in {int(window // 60)} min, maar het proces "
                        f"rekent nog (≈ {nl_number(busy_cores)} kernen bezig) — wacht langer "
                        f"({stall_extensions}/{STALL_MAX_EXTENSIONS})")
                    continue
                diagnose = _stall_diagnose(process.pid, window, busy_cores, recent_lines)
                terminate_process_group(process.pid, signal.SIGTERM)
                with suppress(TimeoutError):
                    await asyncio.wait_for(process.wait(), timeout=10)
                if process.returncode is None:
                    terminate_process_group(process.pid, signal.SIGKILL)
                    await process.wait()
                minutes = int(stall_seconds() // 60)
                message = (f"Geen voortgang in {minutes} minuten — encode gestopt "
                           f"(bleef op {nl_number(best_percent)} % staan)")
                with suppress(sqlite3.OperationalError):
                    append_job_log(database, job_id, "fout", message)
                    append_job_log(database, job_id, "fout", "Diagnose vastloper:\n" + diagnose)
                raise EncodeStalled(message, percent=best_percent, diagnose=diagnose)
            if process.returncode is None:
                await asyncio.sleep(0)
        await process.wait()
        returncode = process.returncode
        # 255 = ffmpeg na SIGTERM; -SIGKILL = docker stop/update na de wachttijd
        if returncode in {-signal.SIGTERM, -signal.SIGINT} or (
            returncode == 255 and (signal_exit or _SHUTDOWN_REQUESTED)
        ) or (returncode == -signal.SIGKILL and _SHUTDOWN_REQUESTED):
            raise ProcessInterrupted("mediaproces onderbroken door service- of systeemstop")
        if _killed_for_memory(returncode, oom_before):
            with suppress(sqlite3.OperationalError):
                append_job_log(database, job_id, "fout", OOM_MESSAGE)
            raise ExecutionError(OOM_MESSAGE)
        ok = returncode in _TOOL_OK_CODES.get(Path(arguments[0]).name, {0})
        if not ok:
            LAST_PROCESS_FAILURE[job_id] = {"tool": Path(arguments[0]).name,
                                            "returncode": returncode,
                                            "lines": list(recent_lines)[-STALL_TAIL_LINES:]}
        return ok


SAMPLE_SECONDS = 60
STALL_SECONDS_DEFAULT = 900
STALL_TAIL_LINES = 50
# Gebruikt het proces in het vastloopvenster gemiddeld minstens zoveel kernen, dan rekent
# het nog; hooguit zoveel keer een extra venster wachten voordat het toch stopt.
STALL_BUSY_CORES = 0.5
STALL_MAX_EXTENSIONS = 2
IO_ACTIVITY_BYTES = 1024 * 1024  # meer dan 1 MB gelezen/geschreven = er gebeurt iets
SLOW_CHECK_SECONDS = 600  # pas na 10 min is de snelheid betrouwbaar
PREEMPT_MAX_ELAPSED = 3600  # alleen plaatsmaken als er nog weinig werk verloren gaat
PREEMPT_MIN_WAITING = 5


def _group_io_bytes(pid: int) -> int:
    """Gelezen + geschreven bytes (rchar/wchar) van het mediaproces en al zijn kinderen."""
    try:
        import psutil

        root = psutil.Process(pid)
        total = 0
        for proc in [root, *root.children(recursive=True)]:
            with suppress(psutil.Error, AttributeError):
                io = proc.io_counters()
                total += int(getattr(io, "read_chars", 0) or io.read_bytes) + int(
                    getattr(io, "write_chars", 0) or io.write_bytes)
        return total
    except Exception:  # noqa: BLE001
        return 0


async def _stop_group(process: asyncio.subprocess.Process) -> None:
    terminate_process_group(process.pid, signal.SIGTERM)
    with suppress(TimeoutError):
        await asyncio.wait_for(process.wait(), timeout=10)
    if process.returncode is None:
        terminate_process_group(process.pid, signal.SIGKILL)
        await process.wait()


def _group_cpu_seconds(pid: int) -> float:
    """CPU-tijd (user+system) van het mediaproces en al zijn kinderen."""
    try:
        import psutil

        root = psutil.Process(pid)
        total = 0.0
        for proc in [root, *root.children(recursive=True)]:
            with suppress(psutil.Error):
                times = proc.cpu_times()
                total += float(times.user) + float(times.system)
        return total
    except Exception:  # noqa: BLE001 - geen meting = als 'niet bezig' behandelen
        return 0.0


def _stall_diagnose(pid: int, window: float, busy_cores: float,
                    lines: deque[str]) -> str:
    """Wat deed het proces toen het vastliep? Voor het joblog (rapport 6, N2)."""
    parts = [f"venster {int(window)} s zonder nieuwe uitvoer; gemiddeld "
             f"{nl_number(busy_cores)} kernen bezig"]
    try:
        import psutil

        proc = psutil.Process(pid)
        memory = proc.memory_info()
        parts.append(f"status {proc.status()}, geheugen {memory.rss // (1024 ** 2)} MiB, "
                     f"threads {proc.num_threads()}")
        with suppress(Exception):
            parts.append(f"kernen {proc.cpu_affinity()}")
        vm = psutil.virtual_memory()
        parts.append(f"systeem: {vm.available // (1024 ** 3)} GiB beschikbaar van "
                     f"{vm.total // (1024 ** 3)} GiB")
    except Exception:  # noqa: BLE001
        parts.append("procesgegevens niet leesbaar")
    # ffmpeg-regels eerst, samenvatting als laatste: het joblog bewaart het eind van een
    # lange regel, en de samenvatting is het belangrijkste deel.
    tail = [f"laatste {len(lines)} regels van ffmpeg:", *lines] if lines else []
    return "\n".join([*tail, *parts])


def stall_seconds() -> float:
    """Na zoveel seconden zonder voortgang stopt een encode (MG_STALL_SECONDS, standaard 15 min)."""
    from app import afstelling

    return max(60.0, float(afstelling.get("vastloper_minuten")) * 60)

SAMPLE_MIN_DURATION = 900  # korter dan 15 min: gewoon het hele bestand doen
SAMPLE_MARGIN = 5.0  # alleen overslaan als de proef duidelijk onder het minimum blijft


VMAF_MINIMUM_DEFAULT = 93.0


def vmaf_minimum() -> float:
    """Ondergrens voor de VMAF-meting (MG_VMAF_MIN, standaard 93)."""
    from app import afstelling

    return float(afstelling.get("vmaf_minimum"))


def quality_rejection(quality: dict[str, Any]) -> str | None:
    """Reden om af te keuren, of None. Getoetst op het gemiddelde én de slechtste
    steekproef (die mag hooguit 3 VMAF-punten of 0,02 SSIM onder de grens zitten)."""
    from app import afstelling

    score, worst = quality.get("score"), quality.get("minimum")
    if score is None:
        return None
    if quality.get("metric") == "vmaf":
        limit, slack, label = float(afstelling.get("vmaf_minimum")), 3.0, "VMAF"
    else:
        limit, slack, label = float(afstelling.get("ssim_minimum")), 0.02, "SSIM"
    if score < limit or (worst is not None and worst < limit - slack):
        def fmt(value: float) -> str:
            return nl_number(value) if label == "VMAF" else f"{value:.3f}".replace(".", ",")

        return (f"Kwaliteit te laag: {label} {fmt(float(score))} (slechtste steekproef "
                f"{fmt(float(worst if worst is not None else score))}) onder "
                f"{fmt(limit)} — origineel blijft staan")
    return None


def nl_number(value: float) -> str:
    return f"{value:.1f}".replace(".", ",")


def nl_percent(value: float) -> str:
    return f"{value:.1f}".replace(".", ",") + " %"


def sample_arguments(arguments: list[str], start: float, output: str) -> list[str]:
    """Hetzelfde commando op een stukje van SAMPLE_SECONDS vanaf ``start``."""
    out = list(arguments)
    position = out.index("-i")
    out[position:position] = ["-ss", f"{start:.0f}"]
    out[-1:] = ["-t", str(SAMPLE_SECONDS), output]
    return out


def hdr_metadata_native(source_probe: dict[str, Any], result_path: Path) -> bool:
    """Draagt het GPU-resultaat al dezelfde HDR10-metadata als de bron?"""
    from app.verificatie import _content_light_equal

    if not source_probe.get("is_hdr10") or not source_probe.get("mastering_display"):
        return False
    result = probe(str(result_path))
    if not result or not result.get("is_hdr10"):
        return False
    return (result.get("mastering_display") == source_probe.get("mastering_display")
            and _content_light_equal(source_probe.get("content_light"),
                                     result.get("content_light")))


def native_hdr_used(database: Database) -> None:
    """Teller voor Systeem: hoe vaak de GPU-runtime de HDR-metadata zelf meedroeg."""
    with suppress(sqlite3.OperationalError):
        database.execute(
            "INSERT INTO staat(sleutel,waarde,tijd) VALUES ('hdr_native_gpu','1',?) "
            "ON CONFLICT(sleutel) DO UPDATE SET waarde=CAST(waarde AS INTEGER)+1,"
            "tijd=excluded.tijd", (datetime.now(UTC).isoformat(),))


VMAF_STEER_SAMPLES = 3  # proefstukken in totaal als de bitrate bijgestuurd moet worden
VMAF_STEER_BAND = (1.0, 2.0)  # doel −1 … doel +2 is goed genoeg
VMAF_STEER_FACTOR = (0.4, 4.0)


def steer_factor(factor: float, score: float, target: float) -> float | None:
    """Nieuwe bitratefactor na een VMAF-meting op het proefstuk, of None als hij goed is."""
    low, high = target - VMAF_STEER_BAND[0], target + VMAF_STEER_BAND[1]
    if low <= score <= high:
        return None
    if score < low:
        step = 1.6 if score < target - 5 else 1.35
    else:
        step = 0.6 if score > target + 4 else 0.75
    new = max(VMAF_STEER_FACTOR[0], min(VMAF_STEER_FACTOR[1], factor * step))
    return None if abs(new - factor) < 1e-6 else new


SAMPLE_EDGE_FRACTION = 0.08  # intro/aftiteling overslaan bij het kiezen van een proefstuk
SAMPLE_CANDIDATES = (0.15, 0.50, 0.80)


async def packet_windows(source: Path, duration: float, window: float = SAMPLE_SECONDS,
                         timeout: float = 120.0) -> dict[int, int] | None:
    """Videobytes per venster van ``window`` seconden (snelle ffprobe-pakketscan, geen
    decodering). None als dat niet lukt; dan wordt de oude vaste positie gebruikt."""
    if duration <= 0:
        return None
    try:
        process = await asyncio.create_subprocess_exec(
            "ffprobe", "-v", "error", "-select_streams", "v:0",
            "-show_entries", "packet=pts_time,size", "-of", "csv=p=0", str(source),
            stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.DEVNULL)
    except OSError:
        return None
    try:
        stdout, _ = await asyncio.wait_for(process.communicate(), timeout=timeout)
    except TimeoutError:
        with suppress(ProcessLookupError):
            process.kill()
        return None
    buckets: dict[int, int] = {}
    for line in stdout.decode("ascii", errors="ignore").splitlines():
        pts, _, size = line.partition(",")
        try:
            buckets[int(float(pts) // window)] = buckets.get(int(float(pts) // window), 0) \
                + int(size)
        except ValueError:
            continue
    return buckets or None


def pick_sample_starts(duration: float, buckets: dict[int, int] | None,
                       window: float = SAMPLE_SECONDS) -> list[tuple[float, int | None]]:
    """Proefstukposities (start, bronbytes in dat venster): eerst het zwaarste venster
    (meeste videobytes = drukste beeld, rapport 8 B2: een rustige intro gaf de GPU te
    weinig bits), daarna 15/50/80 %. Zonder pakketscan: de vaste posities."""
    edge = duration * SAMPLE_EDGE_FRACTION
    last = max(0.0, duration - window)

    def clamp(start: float) -> float:
        return min(max(start, edge), max(edge, last - edge))

    out: list[tuple[float, int | None]] = []
    if buckets:
        usable = {k: v for k, v in buckets.items()
                  if k * window >= edge and (k + 1) * window <= duration - edge}
        if usable:
            index = max(usable, key=lambda k: usable[k])
            out.append((float(index * window), usable[index]))
    for fraction in SAMPLE_CANDIDATES:
        start = clamp(duration * fraction)
        key = int(start // window)
        if all(abs(start - s) >= window for s, _b in out):
            out.append((start, (buckets or {}).get(key)))
    return out


async def _sample_phase(
    database: Database, job_id: int, build: Callable[[float], list[str]], work: Path,
    source: Path, source_size: int, duration: float, minimum: float, log_path: Path,
    *, steer: bool, start_factor: float = 1.0,
) -> tuple[str | None, float, str | None]:
    """Proefstuk(ken) vóór de hele encode.

    - Rapport 2 (N5): blijft de verwachte besparing duidelijk onder het minimum, dan wordt
      het hele bestand niet omgezet.
    - Rapport 7 §4.4 (alleen GPU): het proefstuk wordt met VMAF gemeten en de bitrate gaat
      in hooguit drie stappen naar de doelwaarde (``vmaf_doel``). Geeft (overslaanreden,
      bitratefactor, omleidreden) terug. Bij elke twijfel (proef mislukt, geen VMAF) factor 1.
    - Gebruikersrapport (anime): haalt ook het laatste proefstuk de doelwaarde niet (bitrate
      op het plafond of nog te lage score), dan géén volledige GPU-encode die toch wordt
      afgekeurd, maar direct een andere route (omleidreden)."""
    from app import afstelling
    from app.kwaliteit import compute_vmaf, measure_prefix, vmaf_available

    arguments = build(start_factor)
    if (minimum <= 0 and not steer) or duration < SAMPLE_MIN_DURATION or "-i" not in arguments:
        return None, start_factor, None
    steer = steer and await vmaf_available()
    total = VMAF_STEER_SAMPLES if steer else 1
    target = float(afstelling.get("vmaf_doel"))
    _set_phase(database, job_id, "proefstuk")
    _set_detail(database, job_id, "Proefstuk kiezen (drukste stuk zoeken)")
    starts = pick_sample_starts(duration, await packet_windows(source, duration))
    start, window_bytes = starts[0] if starts else (duration / 3, None)
    append_job_log(database, job_id, "info",
                   f"Proefstuk vanaf {int(start // 60)}:{int(start % 60):02d}"
                   + (" (drukste stuk van de bron)" if window_bytes else ""))
    sample = work / "proefstuk.mkv"
    factor, measured_factor, size = start_factor, start_factor, 0
    last_score: float | None = None
    for attempt in range(1, total + 1):
        _set_detail(database, job_id, f"Proefstuk {attempt}/{total}")
        try:
            ok = await _run_process(database, job_id,
                                    sample_arguments(build(factor), start, str(sample)),
                                    log_path, float(SAMPLE_SECONDS))
        except ExecutionError:
            ok = False
        if not ok or not sample.is_file() or sample.stat().st_size <= 0:
            with suppress(OSError):
                sample.unlink()
            break
        size, measured_factor = sample.stat().st_size, factor
        if not steer:
            break
        _set_detail(database, job_id, f"VMAF meten {attempt}/{total}",
                    100.0 * attempt / (total + 1))
        score = await compute_vmaf(sample, source, start, float(SAMPLE_SECONDS),
                                   result_start_second=0.0,
                                   prefix=measure_prefix(database, job_id))
        if score is None:
            break
        last_score = score
        nxt = steer_factor(factor, score, target)
        append_job_log(database, job_id, "info",
                       f"Proefstuk {attempt}: VMAF {nl_number(score)} (doel {nl_number(target)})"
                       + (f"; bitrate × {nl_number(nxt / factor)}" if nxt and attempt < total
                          else ""))
        if nxt is None or attempt == total:
            break
        factor = nxt
    with suppress(OSError):
        sample.unlink()
    if size <= 0:
        return None, start_factor, None
    reroute = None
    if steer and last_score is not None and last_score < target - VMAF_STEER_BAND[0]:
        reroute = (f"GPU haalt de kwaliteit niet: proefstuk VMAF {nl_number(last_score)} "
                   f"(doel {nl_number(target)}) bij de hoogst zinvolle bitrate")
        append_job_log(database, job_id, "waarschuwing", reroute + " — andere route")
        return None, measured_factor, reroute
    if window_bytes:
        # besparing in hetzelfde venster: het drukste stuk is niet representatief voor de
        # gemiddelde bitrate, de verhouding resultaat/bron daarbinnen wel
        saving = 100.0 * (1 - size / max(window_bytes, 1))
    else:
        projected = size * duration / SAMPLE_SECONDS
        saving = 100.0 * (1 - projected / max(source_size, 1))
    append_job_log(database, job_id, "info",
                   f"Proefstuk: verwachte besparing ± {nl_percent(saving)} "
                   f"(minimum {nl_percent(minimum)})")
    if minimum > 0 and saving < minimum - SAMPLE_MARGIN:
        return (f"proefstuk van {SAMPLE_SECONDS} s: verwachte besparing ± {nl_percent(saving)} "
                f"onder minimum {nl_percent(minimum)}; origineel behouden, niet volledig "
                "omgezet"), measured_factor, None
    return None, measured_factor, None


async def _sample_saving_check(
    database: Database, job_id: int, arguments: list[str], work: Path, source_size: int,
    duration: float, minimum: float, log_path: Path,
) -> str | None:
    """Oude ingang (CPU/zonder sturing): alleen de besparingscheck."""
    skip, _factor, _reroute = await _sample_phase(
        database, job_id, lambda _f: arguments, work,
        Path(arguments[arguments.index("-i") + 1]) if "-i" in arguments else work, source_size,
        duration, minimum, log_path, steer=False)
    return skip


def _clear_leftover_cache(database: Database, settings: Settings, job_id: int,
                          row: Any) -> None:
    """Een cachemap van een eerdere poging (onverwachte fout, harde stop) blokkeerde de job
    voorgoed ('File exists'). Nooit hergebruiken, wel opruimen als er aantoonbaar geen
    mediaproces meer voor deze job loopt (verbeterpunt 28)."""

    from app.wachtrijherstel import (
        QueueRecoveryError,
        media_process_alive,
        remove_job_temporary_output,
    )

    if media_process_alive(int(row["proces_pid"] or 0)):
        raise ExecutionError("een eerdere poging van deze job draait nog; later opnieuw")
    try:
        removed = remove_job_temporary_output(settings, job_id)
    except QueueRecoveryError as exc:
        raise ExecutionError(f"achtergebleven cache kon niet veilig weg: {exc}") from exc
    if removed:
        append_job_log(database, job_id, "waarschuwing",
                       "Achtergebleven cache van een eerdere poging opgeruimd")


async def run_job(
    database: Database,
    settings: Settings,
    job_id: int,
    *,
    worker_id: str = "cli",
    verification_hook: VerificationHook | None = None,
    storage_validator: Callable[[], None] | None = None,
    command_override: list[str] | None = None,
    already_claimed: bool = False,
) -> bool:
    """Draai een job; iedere uitzondering wordt veilig als mislukking vastgelegd."""

    if storage_validator is None:
        storage_validator = settings.validate_storage
    storage_validator()
    if not already_claimed:
        started_at = datetime.now(UTC).isoformat()
        claimed = database.execute(
            """
            UPDATE job SET status='bezig',worker_id=?,gestart_op=?,pogingen=pogingen+1
            WHERE id=? AND status='wacht'
            """,
            (worker_id, started_at, job_id),
        )
        if claimed != 1:
            raise ExecutionError("Job kon niet atomisch worden geclaimd")
        attempt = database.fetch_one("SELECT pogingen FROM job WHERE id=?", (job_id,))
        if attempt is not None:
            database.execute(
                """
                INSERT INTO job_poging(job_id,poging,status,gestart_op)
                VALUES (?,?,'bezig',?)
                ON CONFLICT(job_id,poging) DO UPDATE SET
                    status='bezig',gestart_op=excluded.gestart_op,klaar_op=NULL
                """,
                (job_id, int(attempt["pogingen"]), started_at),
            )
    _set_phase(database, job_id, "analyse")

    row = database.fetch_one(
        """
        SELECT j.*,b.payload,b.actie,b.hardlink_bevestigd,
               p.id AS profiel_id,p.inst AS profiel_inst,
               f.pad,f.grootte,f.mtime,
               f.nlink,f.library_id,l.pad AS library_pad,l.verwacht_device,
               l.bron_type,l.bron_uri
        FROM job j
        JOIN besluit b ON b.id=j.besluit_id
        JOIN bestand f ON f.id=j.bestand_id
        JOIN library l ON l.id=f.library_id
        JOIN profiel p ON p.id=l.profiel_id
        WHERE j.id=?
        """,
        (job_id,),
    )
    if row is None:
        raise ExecutionError(f"Job {job_id} bestaat niet")
    source = Path(str(row["pad"]))
    try:
        current_device = validate_registered_source(
            Path(str(row["library_pad"])),
            source_type=str(row["bron_type"]),
            source_uri=str(row["bron_uri"]) if row["bron_uri"] else None,
            expected_device=(
                int(row["verwacht_device"]) if row["verwacht_device"] is not None else None
            ),
            media_roots=settings.media_roots if settings.container_mode else (),
        )
    except SourceError as exc:
        raise ExecutionError(str(exc)) from exc
    if current_device != row["verwacht_device"]:
        database.execute(
            "UPDATE library SET verwacht_device=? WHERE id=?",
            (current_device, int(row["library_id"])),
        )
    cache_dir = settings.cache_root / str(job_id)
    work_dir = settings.work_root / str(job_id)
    log_dir = settings.config_root / "logs"
    if cache_dir.exists() or cache_dir.is_symlink():
        _clear_leftover_cache(database, settings, job_id, row)
    cache_dir.mkdir(parents=True, exist_ok=False)
    work_dir.mkdir(parents=True, exist_ok=True)
    log_dir.mkdir(parents=True, exist_ok=True)
    # De jobmap is al uniek. Vaste korte namen voorkomen ENAMETOOLONG bij
    # volkomen geldige bronbestanden waarvan de naam dicht bij NAME_MAX zit.
    partial = cache_dir / "resultaat.mkv.deel"
    result_path = cache_dir / "resultaat.mkv"
    try:
        source_stat = source.stat()
        if source_stat.st_size != int(row["grootte"]) or source_stat.st_mtime != float(
            row["mtime"]
        ):
            raise ExecutionError("bron is sinds de scan gewijzigd")
        # Handoff-modus muteert de bron nooit; een hardlink raakt daardoor niet
        # stuk. Core ziet nlink in source_snapshot en beslist bij de commit.
        if source_stat.st_nlink > 1 and settings.replacement_mode != "handoff":
            feature = database.fetch_one(
                "SELECT ingeschakeld FROM functie WHERE sleutel='hardlink_verbreken'"
            )
            policy = database.fetch_one("SELECT hardlink_modus FROM instelling WHERE id=1")
            if (
                not int(row["hardlink_bevestigd"])
                or feature is None
                or not int(feature["ingeschakeld"])
                or policy is None
                or policy["hardlink_modus"] != "verbreek_na_bevestiging"
            ):
                raise ExecutionError("hardlink is vóór uitvoering veilig gestopt")
        if source_stat.st_dev != current_device:
            raise ExecutionError("library staat niet meer op het verwachte device")
        # ffprobe/getfacl kunnen op grote of trage bronnen lang duren: niet op de
        # eventloop (die bedient API, WebSocket en de stdout-pompen van andere jobs).
        metadata = await asyncio.to_thread(capture_metadata, source)
        source_probe = await asyncio.to_thread(probe, str(source))
        if source_probe is None:
            raise ExecutionError("bron is niet meer leesbaar")
        decision = Besluit.model_validate_json(str(row["payload"]))
        # HDR10+ blijft overgeslagen tenzij het besluit een expliciete,
        # gecontroleerde HDR10+-route draagt (behouden of expliciete fallback).
        if bool(source_probe.get("hdr10plus")) and not decision.hdr10plus_route:
            database.execute(
                "UPDATE job SET status='overgeslagen',fout=?,klaar_op=? WHERE id=?",
                ("HDR10+ wordt altijd overgeslagen", datetime.now(UTC).isoformat(), job_id),
            )
            _finish_attempt(database, job_id, "overgeslagen")
            shutil.rmtree(cache_dir, ignore_errors=True)
            return False
        profile_raw: Any = json.loads(str(row["profiel_inst"]))
        if not isinstance(profile_raw, dict):
            raise ExecutionError("profielinstellingen zijn ongeldig")
        override_raw: Any = json.loads(str(row["ai_encode_override"] or "{}"))
        if not isinstance(override_raw, dict):
            raise ExecutionError("AI-retryinstellingen zijn ongeldig")
        adjustment = str(row["ai_retry_aanpassing"] or "geen")
        valid_override = override_raw == {}
        if adjustment == "cpu_geheugen_veilig":
            valid_override = len(override_raw) == 1 and next(iter(override_raw), "") in {
                "svt_threads_2160", "svt_threads_1080", "svt_threads_720"
            } and next(iter(override_raw.values()), None) == 1
        elif adjustment == "gpu_geheugen_veilig":
            valid_override = override_raw == {"gpu_extra_hw_frames": 16}
        elif adjustment == "doelbitrate_verlagen":
            key = next(iter(override_raw), "")
            value = next(iter(override_raw.values()), None)
            valid_override = (
                len(override_raw) == 1
                and key in {
                    "doel_bitrate_2160_kbps",
                    "doel_bitrate_1080_kbps",
                    "doel_bitrate_720_kbps",
                }
                and isinstance(value, int)
                and not isinstance(value, bool)
                and 500 <= value <= 200_000
            )
        if not valid_override:
            raise ExecutionError("AI-retryinstellingen vallen buiten de veilige allowlist")
        # MediaGuard: per-job policy uit core (vertaald naar profielsleutels bij
        # het aanmaken van de job). Leeg voor native jobs.
        mg_inst_raw: Any = json.loads(str(row["mg_inst"] or "{}"))
        if not isinstance(mg_inst_raw, dict):
            raise ExecutionError("MediaGuard-jobpolicy is ongeldig")
        profile = {**STANDAARD, **profile_raw, **mg_inst_raw, **override_raw}
        # CPU-budget uit MediaGuard (Instellingen → Systeem & prestaties)
        profile["cpu_budget_threads"] = cpu_budget_from_database(database)[
            "threads_per_encode"]
        database.execute(
            """
            UPDATE job SET bron_probe=?,bron_grootte=?,bron_mtime=?,bron_bestandsmetadata=?,
                           bron_device=?,bron_inode=? WHERE id=?
            """,
            (
                json.dumps(source_probe, ensure_ascii=False),
                source_stat.st_size,
                source_stat.st_mtime,
                metadata.to_json(),
                source_stat.st_dev,
                source_stat.st_ino,
                job_id,
            ),
        )
        quality_frames = await capture_comparison_frames(
            database,
            settings,
            job_id,
            source,
            None,
            float(source_probe["duration"]),
        )
        encode_source = source
        command_probe = source_probe
        if decision.dv7_omzetten:
            if shutil.which("dovi_tool") is None:
                raise ExecutionError("dovi_tool ontbreekt; profiel 7 wordt niet half uitgevoerd")
            steps, prepared = commandos.dv7_naar_81(
                str(source), str(source_probe.get("fps") or "24000/1001"), str(work_dir)
            )
            _set_phase(database, job_id, "dv_voorbewerking")
            for number, step in enumerate(steps, start=1):
                append_job_log(database, job_id, "info",
                               f"Dolby Vision-voorbewerking {number}/{len(steps)}: "
                               f"{Path(step[0]).name}")
                if not await _run_process(
                    database,
                    job_id,
                    step,
                    log_dir / f"job-{job_id}.log",
                    float(source_probe["duration"]),
                ):
                    raise _step_failed(
                        database, job_id, "Dolby Vision-profiel-7-voorbewerking faalde"
                    )
            encode_source = Path(prepared)
            # mkvmerge -D haalt alle bronvideosporen weg en zet 8.1 vooraan:
            # bouw het commando met de spoorindexen van het voorbewerkte bestand.
            prepared_probe = await asyncio.to_thread(probe, prepared)
            if prepared_probe is None:
                raise ExecutionError("voorbewerkt Dolby Vision-bestand is niet leesbaar")
            decision = commandos.remap_sporen(decision, source_probe, prepared_probe)
            command_probe = {
                **source_probe,
                **{
                    key: prepared_probe.get(key)
                    for key in ("audio", "subs", "video_indices", "attached_pics", "container")
                },
            }

        try:
            base_factor = float(profile.get("gpu_rate_factor") or 1.0)
        except (TypeError, ValueError):
            base_factor = 1.0
        gpu_state = {"factor": base_factor}

        def gpu_command(factor: float, *, hw: bool | None = None,
                        lookahead: bool | None = None) -> list[str]:
            built = commandos.encode_gpu(
                command_probe, decision, profile, str(encode_source), str(partial),
                render_node=str(settings.render_node), variant=settings.gpu_variant,
                hw_decode=commandos.hw_decode_supported(command_probe) if hw is None else hw,
                lookahead=lookahead, rate_factor=factor)
            return [*built[:-1], "-f", "matroska", built[-1]]

        if command_override is not None:
            arguments = command_override
        elif decision.actie == "opschonen":
            arguments = commandos.opschonen(
                command_probe, decision, str(encode_source), str(partial)
            )
        elif decision.encoder == "libaom-av1":
            arguments = commandos.encode_cpu_aom(
                command_probe, decision, profile, str(encode_source), str(partial)
            )
        elif decision.encoder == "av1_qsv":
            arguments = gpu_command(base_factor)
        else:
            arguments = commandos.encode_cpu(
                command_probe, decision, profile, str(encode_source), str(partial)
            )
        if (command_override is None and arguments and Path(arguments[0]).name == "ffmpeg"
                and "matroska" not in arguments):
            # Een .deel-suffix is bewust niet als mediasuffix herkenbaar; geef
            # de muxer daarom expliciet op en hernoem pas na encode-succes.
            arguments = [*arguments[:-1], "-f", "matroska", arguments[-1]]
        # Geen proefstuk voor libaom (0,03×: 60 s proef = ruim een half uur rekenwerk en
        # een voortgang die van 66 % naar 0 % springt; rapport 5, T2).
        if (decision.actie == "hercoderen" and command_override is None and row["mg_policy"]
                and decision.encoder != "libaom-av1"
                and arguments and Path(arguments[0]).name == "ffmpeg"):
            policy = json.loads(str(row["mg_policy"]) or "{}")
            from app import afstelling

            is_gpu = decision.encoder == "av1_qsv"
            fixed = list(arguments)
            skip, factor, reroute = await _sample_phase(
                database, job_id, gpu_command if is_gpu else (lambda _f: fixed), cache_dir,
                encode_source, source_stat.st_size, float(source_probe["duration"]),
                float(policy.get("min_saving_percent") or 0), log_dir / f"job-{job_id}.log",
                steer=is_gpu and bool(afstelling.get("gpu_vmaf_sturing")),
                start_factor=base_factor if is_gpu else 1.0)
            if is_gpu and factor != base_factor:
                gpu_state["factor"] = factor
                arguments = gpu_command(factor)
            if is_gpu and reroute is not None:
                raise GpuQualityReroute(reroute)
            if skip is not None:
                database.execute(
                    "UPDATE job SET status='overgeslagen',fout=?,foutsoort='GeenBesparing',"
                    "klaar_op=?,proces_pid=NULL,resultaat_pad=NULL WHERE id=?",
                    (skip, datetime.now(UTC).isoformat(), job_id))
                _finish_attempt(database, job_id, "overgeslagen")
                shutil.rmtree(cache_dir, ignore_errors=True)
                return False
        _set_phase(database, job_id, "opschonen" if decision.actie == "opschonen" else "encode")
        succeeded = await _run_process(
            database,
            job_id,
            arguments,
            log_dir / f"job-{job_id}.log",
            float(source_probe["duration"]),
        )
        if (not succeeded and command_override is None and decision.encoder == "av1_qsv"
                and "-look_ahead_depth" in arguments):
            # Lookahead/extbrc (rapport 7 §4.4) kent niet elke driver: zelfde poging zonder,
            # en voor deze sessie niet meer proberen.
            commandos.mark_lookahead_broken()
            append_job_log(database, job_id, "waarschuwing",
                           "Vooruitkijken (lookahead) werd door de GPU-driver geweigerd; opnieuw "
                           "met de gewone instelling (staat voor deze sessie uit).")
            partial.unlink(missing_ok=True)
            arguments = gpu_command(gpu_state["factor"], lookahead=False)
            succeeded = await _run_process(
                database, job_id, arguments, log_dir / f"job-{job_id}.log",
                float(source_probe["duration"]))
        if (not succeeded and command_override is None and decision.encoder == "av1_qsv"
                and "-hwaccel" in arguments):
            # Hardwaredecode is op echte hardware niet overal getest: faalt hij, dan
            # dezelfde poging opnieuw met CPU-decode en voor deze sessie niet meer proberen.
            commandos.mark_hw_decode_broken()
            append_job_log(database, job_id, "waarschuwing",
                           "Bron uitpakken op de GPU mislukte; opnieuw met uitpakken op de "
                           "processor (hardwaredecode staat voor deze sessie uit).")
            partial.unlink(missing_ok=True)
            arguments = gpu_command(gpu_state["factor"], hw=False)
            succeeded = await _run_process(
                database, job_id, arguments, log_dir / f"job-{job_id}.log",
                float(source_probe["duration"]))
        if not succeeded:
            failure = _step_failed(database, job_id, "encoderproces faalde")
            if isinstance(failure, ExecutionError) and _gpu_hdr_fallback_allowed(row, decision):
                raise GpuHdrFallback("GPU-encode van HDR10 faalde")
            raise failure
        if not partial.is_file():
            raise ExecutionError("encoder meldde succes maar resultaat ontbreekt")
        # Een meer-gigabytebestand kan bij fsync lang wachten op het lokale
        # journal. Houd de API-eventloop ook tijdens die duurzame flush vrij.
        await asyncio.to_thread(_promote_partial, partial, result_path)

        if decision.herstel_hdr_na and await asyncio.to_thread(
                hdr_metadata_native, source_probe, result_path):
            # Rapport 7 §4.2: met een oneVPL-runtime ≥ 2.11 schrijft av1_qsv mastering display
            # en content light zelf in de bitstream. Dan is de mkvmerge-stap overbodig; de
            # verificatie hieronder blijft de rechter.
            append_job_log(database, job_id, "info",
                           "HDR10-metadata zat al in de AV1-bitstream (GPU-runtime); "
                           "terugzetten met mkvmerge niet nodig")
            native_hdr_used(database)
        elif decision.herstel_hdr_na:
            restored = cache_dir / "resultaat-hdr.mkv"
            _set_phase(database, job_id, "hdr_terugzetten")
            hdr_command = commandos.herstel_hdr(
                source_probe.get("md_ruw"),
                source_probe.get("content_light"),
                str(result_path),
                str(restored),
            )
            if not await _run_process(
                database,
                job_id,
                hdr_command,
                log_dir / f"job-{job_id}.log",
                float(source_probe["duration"]),
            ):
                failure = _step_failed(database, job_id, "HDR10-metadata terugmuxen faalde")
                if isinstance(failure, ExecutionError) and _gpu_hdr_fallback_allowed(
                        row, decision):
                    raise GpuHdrFallback("HDR10-metadata terugzetten faalde")
                raise failure
            restored.replace(result_path)

        database.execute("UPDATE job SET status='verifieren' WHERE id=?", (job_id,))
        _set_phase(database, job_id, "verifieren")
        verification = await asyncio.to_thread(
            verify_result, source_probe, result_path, source_stat.st_size, decision
        )
        if verification_hook is not None:
            verification = verification_hook(verification)
        result_probe = verification.result_probe or {}
        database.execute(
            """
            INSERT OR REPLACE INTO verificatie(
                job_id,hdr_bron,hdr_resultaat,dv_bron,dv_resultaat,md_gelijk,
                audio_bron,audio_resultaat,attachments_bron,attachments_resultaat,
                geslaagd,toelichting
            ) VALUES (?,?,?,?,?,?,?,?,?,?,?,?)
            """,
            (
                job_id,
                int(bool(source_probe.get("is_hdr10"))),
                int(bool(result_probe.get("is_hdr10"))),
                int(source_probe.get("dv_profile") or 0),
                int(result_probe.get("dv_profile") or 0),
                int(verification.mastering_equal),
                len(source_probe.get("audio") or []),
                len(result_probe.get("audio") or []),
                int(source_probe.get("attachments") or 0),
                int(result_probe.get("attachments") or 0),
                int(verification.passed),
                verification.reason,
            ),
        )
        if (
            not verification.passed
            and decision.actie == "opschonen"
            and verification.reason == "resultaat is groter dan de bron"
        ):
            # Opschonen is alleen zinvol bij echte ruimtewinst. Een groter
            # resultaat is geen mediastoring: de bron blijft intact en de
            # tijdelijke remux wordt zonder retry verwijderd.
            advice = _kwaliteitsadvies(
                source_stat.st_size,
                result_path.stat().st_size,
                source_probe,
                profile,
                decision,
            )
            database.execute(
                """
                UPDATE job SET status='overgeslagen',fout=?,foutsoort='GeenBesparing',
                               resultaat_pad=NULL,resultaat_grootte=NULL,klaar_op=?,
                               proces_pid=NULL,kwaliteitsadvies=?,kwaliteitsadvies_status='advies'
                WHERE id=?
                """,
                (
                    "opschonen veilig overgeslagen: geen besparing",
                    datetime.now(UTC).isoformat(),
                    advice,
                    job_id,
                ),
            )
            _finish_attempt(database, job_id, "overgeslagen")
            shutil.rmtree(cache_dir, ignore_errors=True)
            return False
        if (
            not verification.passed
            and _gpu_hdr_fallback_allowed(row, decision)
            and any(marker in verification.reason for marker in _HDR_VERIFY_MARKERS)
        ):
            raise GpuHdrFallback(verification.reason)
        if not verification.passed:
            if verification.reason == "resultaat is groter dan de bron":
                advice = _kwaliteitsadvies(
                    source_stat.st_size,
                    result_path.stat().st_size,
                    source_probe,
                    profile,
                    decision,
                )
                database.execute(
                    "UPDATE job SET kwaliteitsadvies=?,kwaliteitsadvies_status='advies',"
                    "resultaat_grootte=? WHERE id=?",
                    (advice, result_path.stat().st_size, job_id),
                )
                if decision.actie == "hercoderen":
                    # Gebruikersrapport (Midsommar): groter dan de bron is geen storing maar
                    # 'geen winst'. Overslaan (geen fout) en onthouden voor vergelijkbare bronnen.
                    from app import routeleer

                    message = ("Geen winst — origineel blijft: resultaat is groter dan de bron "
                               f"({result_path.stat().st_size / 1024**3:.1f} GB tegen "
                               f"{source_stat.st_size / 1024**3:.1f} GB)").replace(".", ",")
                    routeleer.record_no_gain(database, source_probe, message)
                    database.execute(
                        "UPDATE job SET status='overgeslagen',fout=?,foutsoort='GeenBesparing',"
                        "klaar_op=?,proces_pid=NULL,resultaat_pad=NULL WHERE id=?",
                        (message, datetime.now(UTC).isoformat(), job_id))
                    append_job_log(database, job_id, "info", message)
                    _finish_attempt(database, job_id, "overgeslagen")
                    shutil.rmtree(cache_dir, ignore_errors=True)
                    return False
            raise ExecutionError(f"verificatie mislukt: {verification.reason}")

        floor_problem = _bitrate_floor_problem(source_probe, result_probe, decision)
        if floor_problem is not None:
            klasse, floor, message = floor_problem
            append_job_log(database, job_id, "waarschuwing", message)
            if not int(row["kwaliteit_herkansd"] or 0):
                raise QualityFloorRetry(f"{klasse}|{floor}|{message}")
            database.execute(
                "UPDATE job SET status='overgeslagen',fout=?,foutsoort='KwaliteitsOndergrens',"
                "klaar_op=?,proces_pid=NULL,resultaat_pad=NULL,resultaat_grootte=NULL "
                "WHERE id=?",
                (f"{message}; ook na herkansing — origineel blijft staan",
                 datetime.now(UTC).isoformat(), job_id))
            _finish_attempt(database, job_id, "overgeslagen")
            shutil.rmtree(cache_dir, ignore_errors=True)
            return False

        _set_phase(database, job_id, "kwaliteit_meten")
        quality = await measure_quality(
            database, settings, job_id, source, result_path, float(source_probe["duration"]),
            on_sample=lambda done, total: _set_detail(
                database, job_id, f"Kwaliteit meten {done}/{total}", 100.0 * (done - 1) / total))
        stills = quality.get("stills") or []
        quality_frames = {
            "bron": stills[0]["bron"] if stills else None,
            "resultaat": stills[0]["resultaat"] if stills else None,
            "vmaf": quality["score"] if quality.get("metric") == "vmaf" else None,
        }
        database.execute(
            "UPDATE job SET vmaf_score=?,vergelijking=? WHERE id=?",
            (quality_frames["vmaf"], json.dumps(quality, ensure_ascii=False), job_id))
        if quality.get("unmeasured"):
            # Niet kunnen meten is geen 'goed': origineel houden en duidelijk melden.
            reasons = "; ".join(quality.get("problems") or []) or "geen reden bekend"
            message = (f"Kwaliteitsmeting mislukt: geen van de {quality.get('attempted') or 0} "
                       f"steekproeven kon worden gemeten ({reasons[:400]}). Het resultaat "
                       "wordt niet geplaatst; het origineel blijft staan")
            append_job_log(database, job_id, "fout", message)
            raise QualityUnmeasured(message)
        if quality.get("fallback"):
            append_job_log(database, job_id, "waarschuwing", str(quality["fallback"]))
        if 0 < int(quality.get("measured") or 0) < int(quality.get("attempted") or 0):
            append_job_log(database, job_id, "waarschuwing",
                           f"Kwaliteit: {quality['measured']} van {quality['attempted']} "
                           "steekproeven gemeten; "
                           + "; ".join(quality.get("problems") or [])[:300])
        rejection = quality_rejection(quality)
        if rejection is not None:
            # Kwaliteitspoort (rapport 3 R9, rapport 5 M4): onder de grens nooit vervangen.
            append_job_log(database, job_id, "fout", rejection)
            if decision.encoder == "av1_qsv" and decision.actie == "hercoderen":
                # Gebruikersrapport (anime): een GPU-afkeuring telt mee voor de reeks; was er
                # al een herkansing met meer bits, dan via de CPU in plaats van 'mislukt'.
                if int(row["kwaliteit_herkansd"] or 0):
                    raise GpuQualityReroute(rejection)
                from app import routeleer

                routeleer.record_gpu_rejection(database, str(source), rejection)
            if not int(row["kwaliteit_herkansd"] or 0) and decision.actie == "hercoderen":
                # Rapport 8, B2: één automatische herkansing met meer bits (GPU) of een
                # lagere CRF (CPU) voordat het origineel definitief blijft staan.
                raise QualityVmafRetry(f"{gpu_state['factor']}|{rejection}")
            raise ExecutionError(rejection + "; ook na herkansing" if int(
                row["kwaliteit_herkansd"] or 0) else rejection)
        if quality.get("score") is not None:
            label = "VMAF" if quality["metric"] == "vmaf" else "SSIM"
            shown = (nl_number(float(quality["score"])) if label == "VMAF"
                     else f"{float(quality['score']):.3f}".replace(".", ","))
            append_job_log(database, job_id, "info",
                           f"Kwaliteit gemeten: {label} {shown} "
                           f"({len(quality.get('samples') or [])} steekproeven)")
        if quality_frames["bron"] or quality_frames["resultaat"]:
            database.execute(
                """
                INSERT INTO kwaliteitsmeting(
                    job_id,profiel_id,soort,start_seconde,duur_seconden,
                    bron_frame,resultaat_frame,vmaf_score,gemaakt_op
                ) VALUES (?,?,'vergelijking',?,0,?,?,?,?)
                """,
                (
                    job_id,
                    int(row["profiel_id"]),
                    float(source_probe["duration"]) / 2,
                    quality_frames["bron"],
                    quality_frames["resultaat"],
                    quality_frames["vmaf"],
                    datetime.now(UTC).isoformat(),
                ),
            )

        if settings.replacement_mode == "handoff":
            # P2: de encoder stopt hier. Core's recovery-worker is de enige
            # writer op bibliotheekpaden en meldt committed/rejected terug.
            _set_phase(database, job_id, "klaarzetten")
            outcome = await asyncio.to_thread(
                stage_for_commit,
                database,
                settings,
                job_id,
                source,
                source_stat,
                result_path,
                source_probe,
                verification,
                decision,
            )
            _finish_attempt(database, job_id, outcome)
            shutil.rmtree(cache_dir, ignore_errors=True)
            return outcome == STATUS_WACHT_OP_COMMIT

        # Een resultaat van meerdere gigabytes hashen en naar hetzelfde
        # libraryfilesystem kopiëren kan op SMB minuten duren. Dit is gewone
        # blokkerende bestands-I/O en mag daarom niet de FastAPI-eventloop (en
        # daarmee dashboard, pauzeknoppen en AI-controller) stilzetten.
        _set_phase(database, job_id, "vervangen")
        await asyncio.to_thread(
            atomic_replace, database, job_id, source, result_path, metadata
        )
        final_stat = source.stat()
        final_size = final_stat.st_size
        completed_at = datetime.now(UTC).isoformat()
        database.execute(
            """
            UPDATE job SET status='klaar',resultaat_pad=?,resultaat_grootte=?,percent=100,
                           klaar_op=?,proces_pid=NULL WHERE id=?
            """,
            (str(source), final_size, completed_at, job_id),
        )
        database.execute(
            """
            UPDATE bestand
            SET grootte=?,mtime=?,nlink=?,device=?,inode=?,probe=?,gescand_op=?,
                aanwezig=1,stabiel_vanaf=?,scan_status='gereed'
            WHERE id=?
            """,
            (
                final_size,
                final_stat.st_mtime,
                final_stat.st_nlink,
                final_stat.st_dev,
                final_stat.st_ino,
                json.dumps(result_probe, ensure_ascii=False),
                completed_at,
                completed_at,
                int(row["bestand_id"]),
            ),
        )
        database.execute(
            """
            INSERT INTO besparing(job_id,library_id,bytes_voor,bytes_na,dag)
            VALUES (?,?,?,?,date('now'))
            """,
            (job_id, int(row["library_id"]), source_stat.st_size, final_size),
        )
        await refresh_after_replacement(database, settings, source, job_id)
        _finish_attempt(database, job_id, "klaar")
        shutil.rmtree(cache_dir)
        return True
    except JobAborted:
        database.execute(
            """
            UPDATE job SET status='afgebroken',fout='door gebruiker afgebroken',klaar_op=?,
                           proces_pid=NULL
            WHERE id=?
            """,
            (datetime.now(UTC).isoformat(), job_id),
        )
        _finish_attempt(database, job_id, "afgebroken")
        shutil.rmtree(cache_dir, ignore_errors=True)
        return False
    except QualityFloorRetry as exc:
        shutil.rmtree(cache_dir, ignore_errors=True)
        klasse, floor_text, message = str(exc).split("|", 2)
        requeue_quality_floor(database, job_id, klasse, int(floor_text), message)
        _finish_attempt(database, job_id, "afgebroken", error=message,
                        error_type="KwaliteitsHerkansing")
        with suppress(Exception):
            from app.planner import wake_planner

            wake_planner()
        return False
    except QualityVmafRetry as exc:
        shutil.rmtree(cache_dir, ignore_errors=True)
        factor_text, message = str(exc).split("|", 1)
        route = requeue_quality_vmaf(database, job_id, decision, float(factor_text), message)
        append_job_log(database, job_id, "waarschuwing",
                       f"{QUALITY_VMAF_REDEN}: herkansing via {route.upper()}")
        _finish_attempt(database, job_id, "afgebroken", error=message,
                        error_type="KwaliteitsHerkansing")
        with suppress(Exception):
            from app.planner import wake_planner

            wake_planner()
        return False
    except GpuQualityReroute as exc:
        shutil.rmtree(cache_dir, ignore_errors=True)
        route = reroute_to_cpu(database, job_id, decision, str(source), str(exc))
        append_job_log(database, job_id, "waarschuwing",
                       f"{REROUTE_REDEN}: {exc}" if route == "cpu"
                       else f"{exc}; geen andere route — origineel blijft staan")
        _finish_attempt(database, job_id, "afgebroken" if route == "cpu" else "overgeslagen",
                        error=str(exc), error_type="Omgeleid" if route == "cpu" else None)
        with suppress(Exception):
            from app.planner import wake_planner

            wake_planner()
        return False
    except EncodeTooSlow as exc:
        shutil.rmtree(cache_dir, ignore_errors=True)
        database.execute(
            "UPDATE job SET status='overgeslagen',fout=?,foutsoort='TeTraag',klaar_op=?,"
            "proces_pid=NULL,worker_id=NULL,resultaat_pad=NULL,resultaat_grootte=NULL "
            "WHERE id=?", (str(exc)[:500], datetime.now(UTC).isoformat(), job_id))
        append_job_log(database, job_id, "waarschuwing", str(exc))
        _finish_attempt(database, job_id, "overgeslagen", error=str(exc), error_type="TeTraag")
        with suppress(Exception):
            from app.planner import wake_planner

            wake_planner()
        return False
    except EncodePreempted as exc:
        shutil.rmtree(cache_dir, ignore_errors=True)
        database.execute(
            "UPDATE job SET status='wacht',alleen_nacht=1,fout=?,foutsoort=NULL,proces_pid=NULL,"
            "worker_id=NULL,cache_reservering=0,percent=0,klaar_op=NULL,resultaat_pad=NULL,"
            "resultaat_grootte=NULL WHERE id=?", (str(exc)[:500], job_id))
        append_job_log(database, job_id, "info", str(exc))
        _finish_attempt(database, job_id, "afgebroken", error=str(exc), error_type="Uitgesteld")
        with suppress(Exception):
            from app.planner import wake_planner

            wake_planner()
        return False
    except EncodeStalled as exc:
        shutil.rmtree(cache_dir, ignore_errors=True)
        return _handle_stall(database, job_id, decision, exc)
    except GpuHdrFallback as exc:
        shutil.rmtree(cache_dir, ignore_errors=True)
        requeue_hdr_on_cpu(database, job_id, decision, str(exc))
        _finish_attempt(database, job_id, "afgebroken", error=f"{GPU_HDR_FALLBACK_REDEN}: {exc}",
                        error_type="GpuHdrTerugval")
        with suppress(Exception):
            from app.planner import wake_planner

            wake_planner()
        return False
    except ProcessInterrupted as exc:
        # Een beheerherstart of nette systeemstop is geen encodefout. Verwijder
        # alleen de onvolledige cache en zet dezelfde goedgekeurde job terug in
        # de wachtrij; bij een echte stroomuitval doet weesherstel dit na boot.
        shutil.rmtree(cache_dir, ignore_errors=True)
        if noodstop.active():
            requeue_for_emergency(database, job_id)
            return False
        from app.geplandestop import planned_stop

        if planned_stop(settings):
            # container-stop/update: geen herstartlus (verbeterpunt 30)
            requeue_for_planned_stop(database, job_id)
            return False
        database.execute(
            """
            UPDATE job SET status='wacht',fout=?,foutsoort=NULL,proces_pid=NULL,
                           worker_id=NULL,cache_reservering=0,percent=0,klaar_op=NULL
            WHERE id=?
            """,
            (str(exc), job_id),
        )
        _finish_attempt(
            database,
            job_id,
            "afgebroken",
            error=str(exc),
            error_type="ServiceStop",
        )
        cap_restart_loop(database, job_id)
        return False
    except FileNotFoundError as exc:
        shutil.rmtree(cache_dir, ignore_errors=True)
        if source.exists():
            # Een ontbrekend hulpprogramma (mkvmerge, getfacl, HDR10+-ffmpeg) of
            # tijdelijk bestand is een echte fout, geen verdwenen bron.
            record_failure(
                database, job_id, f"bestand of programma ontbreekt: {exc}", "FileNotFoundError"
            )
            return False
        # Een scanner kan een inmiddels door Radarr/Sonarr verplaatst bestand
        # nog als job hebben. Dat is geen encodefout en mag geen automatische
        # retry-lus veroorzaken; de volgende scan maakt de catalogus recht.
        message = f"bron niet meer aanwezig; volgende scan controleert opnieuw: {exc}"
        database.execute(
            """
            UPDATE job SET status='overgeslagen',fout=?,foutsoort='BronNietBeschikbaar',
                           klaar_op=?,proces_pid=NULL,cache_reservering=0
            WHERE id=?
            """,
            (message[:1000], datetime.now(UTC).isoformat(), job_id),
        )
        _finish_attempt(
            database,
            job_id,
            "overgeslagen",
            error=message,
            error_type="BronNietBeschikbaar",
        )
        return False
    except ReplacementError as exc:
        # Direct-modus: vervangherstel (vervanging.py) beheert de cache zelf.
        # Handoff raakt de bron nooit aan; daar is de cache alleen nog afval.
        if settings.replacement_mode == "handoff":
            shutil.rmtree(cache_dir, ignore_errors=True)
        record_failure(database, job_id, str(exc), type(exc).__name__)
        return False
    except (ExecutionError, MetadataError, OSError, ValueError) as exc:
        # Een volledig .deel-resultaat van tientallen GB mag niet blijven staan.
        shutil.rmtree(cache_dir, ignore_errors=True)
        record_failure(database, job_id, str(exc), type(exc).__name__)
        return False
    except Exception as exc:
        # Onverwachte fout (programmeerfout, databasefout, …): ook dan geen tientallen GB
        # cache laten staan (verbeterpunt 28). In direct-modus tijdens het vervangen beslist
        # het vervangherstel (vervang_transactie) over cache en bron; daar niets weghalen.
        LOGGER.exception("Onverwachte fout in job %s", job_id)
        replacing = settings.replacement_mode != "handoff" and (
            JOB_PHASES.get(job_id) or ("",))[0] == "vervangen"
        if not replacing:
            shutil.rmtree(cache_dir, ignore_errors=True)
        record_failure(
            database, job_id,
            f"onverwachte fout ({type(exc).__name__}): {str(exc)[:400] or 'geen details'}; "
            + ("het vervangherstel controleert de bron bij de volgende start" if replacing
               else "het origineel is niet aangeraakt"),
            type(exc).__name__)
        return False
    finally:
        LAST_PROCESS_FAILURE.pop(job_id, None)
        shutil.rmtree(work_dir, ignore_errors=True)
        database.execute("UPDATE job SET cache_reservering=0 WHERE id=?", (job_id,))
