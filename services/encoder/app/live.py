"""Eén WebSocket met live ticks, toestandsovergangen en gerichte logtail."""

from __future__ import annotations

import asyncio
import json
from datetime import UTC, datetime
from typing import Any

from fastapi import FastAPI, WebSocket, WebSocketDisconnect

from app.api import _worker_rows
from app.config import Settings
from app.db import Database
from app.jobstatus import DONE_SQL, DONE_STATUSES
from app.systeem import GpuMonitor, system_metrics
from app.uitvoerder import JOB_PHASE_DETAIL, JOB_PHASES


def _queue_counts(database: Database) -> dict[str, int]:
    # klaar_op is UTC; "vandaag" is de lokale kalenderdag van de gebruiker.
    local_midnight = datetime.now().astimezone().replace(
        hour=0, minute=0, second=0, microsecond=0
    )
    row = database.fetch_one(
        f"""
        SELECT SUM(CASE WHEN status='wacht' THEN 1 ELSE 0 END) AS wachtend,
               SUM(CASE WHEN status IN ('bezig','verifieren') THEN 1 ELSE 0 END) AS bezig,
               SUM(CASE WHEN status='wacht_op_commit' THEN 1 ELSE 0 END) AS wacht_op_commit,
               SUM(CASE WHEN status IN {DONE_SQL} AND datetime(klaar_op)>=datetime(?)
                        THEN 1 ELSE 0 END) AS klaar_vandaag,
               SUM(CASE WHEN status='mislukt' THEN 1 ELSE 0 END) AS fouten
        FROM job
        """,
        (local_midnight.astimezone(UTC).isoformat(),),
    )
    names = ("wachtend", "bezig", "wacht_op_commit", "klaar_vandaag", "fouten")
    if row is None:
        return dict.fromkeys(names, 0)
    return {name: int(row[name] or 0) for name in names}


def _current_size(settings: Settings, job_id: int) -> int:
    directory = settings.cache_root / str(job_id)
    if not directory.is_dir():
        return 0
    try:
        return sum(path.stat().st_size for path in directory.iterdir() if path.is_file())
    except OSError:
        return 0


def _slot_of(worker_id: object) -> int | None:
    suffix = str(worker_id or "").rpartition("-")[2]
    return int(suffix) if suffix.isdigit() else None


def lane_summary(lane: dict[str, Any], busy: int) -> dict[str, Any]:
    """Eén regel per baan in gewone taal, zonder het woord "worker"."""

    name = "Videokaart (GPU)" if lane["baan"] == "gpu" else "Processor (CPU)"
    configured = int(lane.get("aantal") or 0)
    paused = bool(int(lane.get("gepauzeerd") or 0))
    reason = str(lane.get("pauze_reden") or "")
    for prefix in ("MediaGuard:", "automatisch:", "tijdelijk:"):
        if reason.startswith(prefix):
            reason = reason[len(prefix):].strip()
    if paused:
        text = reason or "gepauzeerd"
        if busy:
            text += f" (rondt {busy} encode{'s' if busy != 1 else ''} af)"
    elif configured == 0:
        text = "uitgeschakeld"
    else:
        text = f"{busy} van {configured} tegelijk bezig"
    from app import noodstop
    from app.planner import LANE_BLOCK_REASON

    if noodstop.active():
        # Noodstop (rapport 6, N1): lopende encodes zijn bevroren, er start niets nieuws.
        text = "Noodstop actief — " + (f"{busy} encode{'s' if busy != 1 else ''} gepauzeerd"
                                       if busy else "er start niets")
        return {"baan": lane["baan"], "naam": name, "aantal": configured, "bezig": busy,
                "gepauzeerd": True, "status": text, "wacht_reden": "noodstop actief",
                "noodstop": True}
    blocked = LANE_BLOCK_REASON.get(str(lane["baan"])) if not paused else None
    if blocked and busy < configured:
        text += f" — wacht: {blocked}"
    return {"baan": lane["baan"], "naam": name, "aantal": configured, "bezig": busy,
            "gepauzeerd": paused, "status": text, "wacht_reden": blocked}


def tick(database: Database, settings: Settings, gpu: GpuMonitor) -> dict[str, Any]:
    """Live-stand. ``workers`` bevat per baan één item per ingestelde worker (``aantal``):
    actieve jobs met hun fase en voortgang, en lege plekken met de reden waarom ze wachten.
    Zo kan de UI elke worker een eigen voortgangsbalk geven."""
    workers = _worker_rows(database)
    running = {
        int(row["id"]): dict(row)
        for row in database.fetch_all(
            "SELECT id,status,gestart_op,worker_id,noodstop_gepauzeerd FROM job "
            "WHERE status IN ('bezig','verifieren')"
        )
    }
    for stale in set(JOB_PHASES) - set(running):
        JOB_PHASES.pop(stale, None)
        JOB_PHASE_DETAIL.pop(stale, None)
    active: list[dict[str, Any]] = []
    lanes: list[dict[str, Any]] = []
    for lane in workers:
        jobs = lane.pop("actieve_jobs")
        lanes.append(lane_summary(lane, len(jobs)))
        lane.pop("historie")
        configured = int(lane.get("aantal") or 0)
        used: set[int] = set()
        for job in jobs:
            extra = running.get(int(job["job_id"]), {})
            phase = JOB_PHASES.get(int(job["job_id"]))
            job["status"] = extra.get("status")
            job["gestart_op"] = extra.get("gestart_op")
            job["worker_id"] = extra.get("worker_id")
            job["fase"] = phase[0] if phase else None
            job["fase_gestart_op"] = phase[1] if phase else None
            job["fase_detail"] = JOB_PHASE_DETAIL.get(int(job["job_id"]))
            job["baan"] = lane["baan"]
            job["grootte_nu"] = _current_size(settings, int(job["job_id"]))
            job["gepauzeerd"] = lane["gepauzeerd"]
            job["noodstop_gepauzeerd"] = bool(int(extra.get("noodstop_gepauzeerd") or 0))
            job["aantal"] = configured
            slot = _slot_of(job.get("worker_id"))
            if slot is None or slot in used:
                slot = next(i for i in range(1, len(jobs) + configured + 2) if i not in used)
            used.add(slot)
            job["slot"] = slot
            active.append(job)
        free = [i for i in range(1, max(configured, len(jobs)) + 1) if i not in used]
        if configured == 0 and not jobs:
            free = [1]
        for slot in free:
            active.append(
                {
                    "baan": lane["baan"],
                    "slot": slot,
                    "aantal": configured,
                    "job_id": None,
                    "uitgeschakeld": configured == 0,
                    "gepauzeerd": lane["gepauzeerd"],
                    "pauze_reden": lane["pauze_reden"],
                }
            )
    active.sort(key=lambda item: (str(item["baan"]) != "gpu", int(item["slot"])))
    return {
        "type": "tick",
        "tijd": datetime.now(UTC).isoformat(),
        "workers": active,
        # Eén statusregel per baan (de UI hoeft een pauzereden niet per plek te herhalen).
        "banen": sorted(lanes, key=lambda item: str(item["baan"]) != "gpu"),
        "systeem": system_metrics(database, settings, gpu),
        "wachtrij": _queue_counts(database),
        "noodstop": _noodstop_state(),
    }


def _noodstop_state() -> dict[str, Any]:
    from app import noodstop

    return noodstop.state()


# Alleen jobs/scans die nog kunnen veranderen worden elke seconde gelezen (geen volledige
# tabelscan per seconde per open telefoon meer).
_OPEN_JOB = ("wacht", "bezig", "verifieren", "wacht_op_commit")


def _job_states(
    database: Database, previous: dict[int, tuple[str, int]] | None = None
) -> dict[int, tuple[str, int]]:
    ids = list(previous or {})
    marks = ",".join("?" for _ in ids) or "NULL"
    open_marks = ",".join("?" for _ in _OPEN_JOB)
    rows = database.fetch_all(
        "SELECT id,status,COALESCE(bron_grootte,0)-COALESCE(resultaat_grootte,0) AS besparing "
        f"FROM job WHERE status IN ({open_marks}) OR id IN ({marks})",
        (*_OPEN_JOB, *ids),
    )
    return {int(row["id"]): (str(row["status"]), int(row["besparing"] or 0)) for row in rows}


def _scan_states(
    database: Database, previous: dict[int, tuple[str, int, int]] | None = None
) -> dict[int, tuple[str, int, int]]:
    ids = list(previous or {})
    marks = ",".join("?" for _ in ids) or "NULL"
    rows = database.fetch_all(
        "SELECT id,status,library_id,gevonden FROM scan_run "
        f"WHERE status='bezig' OR id IN ({marks})",
        tuple(ids),
    )
    return {
        int(row["id"]): (str(row["status"]), int(row["library_id"]), int(row["gevonden"]))
        for row in rows
    }


async def _send_events(
    websocket: WebSocket,
    database: Database,
    previous_jobs: dict[int, tuple[str, int]],
    previous_scans: dict[int, tuple[str, int, int]],
) -> tuple[dict[int, tuple[str, int]], dict[int, tuple[str, int, int]]]:
    jobs = _job_states(database, previous_jobs)
    for job_id, (job_status, saving) in jobs.items():
        old_status = previous_jobs.get(job_id, (job_status, 0))[0]
        if old_status != job_status and job_status in DONE_STATUSES:
            await websocket.send_json(
                {"type": "job_klaar", "job_id": job_id, "besparing_bytes": saving}
            )
        elif old_status != job_status and job_status == "mislukt":
            row = database.fetch_one("SELECT fout FROM job WHERE id=?", (job_id,))
            await websocket.send_json(
                {
                    "type": "job_mislukt",
                    "job_id": job_id,
                    "reden": str(row["fout"] if row is not None else "onbekend"),
                }
            )
    scans = _scan_states(database, previous_scans)
    for scan_id, (scan_status, library_id, found) in scans.items():
        old_status = previous_scans.get(scan_id, (scan_status, library_id, found))[0]
        if old_status != scan_status and scan_status == "klaar":
            await websocket.send_json(
                {
                    "type": "scan_klaar",
                    "library_id": library_id,
                    "nieuw": found,
                    "gewijzigd": 0,
                }
            )
    # afgeronde jobs/scans vallen na één ronde uit de volglijst
    jobs = {k: v for k, v in jobs.items() if v[0] in _OPEN_JOB}
    scans = {k: v for k, v in scans.items() if v[0] == "bezig"}
    return jobs, scans


_LOG_CHUNK = 64 * 1024


def _log_size(path: Any) -> int:
    try:
        return int(path.stat().st_size)
    except OSError:
        return 0


async def _send_logs(
    websocket: WebSocket,
    settings: Settings,
    subscriptions: set[int],
    offsets: dict[int, int],
) -> None:
    """Alleen nieuwe regels (byte-offset), niet elke seconde het hele logbestand."""

    for job_id in subscriptions:
        path = settings.config_root / "logs" / f"job-{job_id}.log"
        if not path.is_file():
            continue
        start = offsets.get(job_id, _log_size(path))
        size = _log_size(path)
        if size < start:  # log opnieuw begonnen (nieuwe poging)
            start = 0
        if size == start:
            continue
        with path.open("rb") as handle:
            handle.seek(start)
            chunk = handle.read(_LOG_CHUNK)
        complete = chunk.rfind(b"\n") + 1
        if complete == 0 and len(chunk) < _LOG_CHUNK:
            continue  # alleen een halve regel: volgende ronde
        complete = complete or len(chunk)
        lines = chunk[:complete].decode("utf-8", errors="replace").splitlines()
        for line in lines[:250]:
            await websocket.send_json(
                {
                    "type": "log",
                    "job_id": job_id,
                    "niveau": "info",
                    "tekst": line,
                    "tijd": datetime_now(),
                }
            )
        offsets[job_id] = start + complete


async def _send_audits(websocket: WebSocket, database: Database, last_id: int) -> int:
    rows = database.fetch_all(
        "SELECT id,onderwerp,nieuw FROM auditlog WHERE soort='functie' AND id>? ORDER BY id",
        (last_id,),
    )
    for row in rows:
        await websocket.send_json(
            {
                "type": "melding",
                "soort": "functie",
                "titel": f"Onderdeel {row['onderwerp']} gewijzigd",
                "actie": str(row["nieuw"]),
            }
        )
        last_id = int(row["id"])
    return last_id


ACTIVE_CLIENTS = 0
_TICK_CACHE: dict[str, Any] = {"at": 0.0, "payload": None}


def clients_connected() -> bool:
    return ACTIVE_CLIENTS > 0


def _shared_tick(
    database: Database, settings: Settings, gpu: GpuMonitor, now: float
) -> dict[str, Any]:
    """Eén berekening per seconde voor alle open tabbladen samen."""

    if _TICK_CACHE["payload"] is None or now - float(_TICK_CACHE["at"]) >= 0.9:
        _TICK_CACHE["payload"] = tick(database, settings, gpu)
        _TICK_CACHE["at"] = now
    return dict(_TICK_CACHE["payload"])


def _signature(payload: dict[str, Any]) -> str:
    return json.dumps(
        {"w": payload.get("workers"), "q": payload.get("wachtrij")}, sort_keys=True, default=str
    )


def datetime_now() -> str:
    return datetime.now(UTC).isoformat()


def register_websocket(application: FastAPI) -> None:
    @application.websocket("/ws")
    async def websocket_endpoint(websocket: WebSocket) -> None:
        await websocket.accept()
        global ACTIVE_CLIENTS
        ACTIVE_CLIENTS += 1
        database: Database = application.state.database
        settings: Settings = application.state.settings
        gpu: GpuMonitor = application.state.gpu_monitor
        subscriptions: set[int] = set()
        offsets: dict[int, int] = {}
        job_states = _job_states(database)
        scan_states = _scan_states(database)
        audit = database.fetch_one("SELECT COALESCE(MAX(id),0) AS id FROM auditlog")
        last_audit_id = int(audit["id"]) if audit is not None else 0
        loop = asyncio.get_running_loop()
        next_tick = 0.0
        last_sent = 0.0
        last_signature = ""
        try:
            while True:
                # Hooguit één tick per seconde; als er niets verandert hooguit elke 2 s.
                if loop.time() >= next_tick:
                    now = loop.time()
                    next_tick = now + 1.0
                    payload = await asyncio.to_thread(_shared_tick, database, settings, gpu, now)
                    signature = _signature(payload)
                    if signature != last_signature or now - last_sent >= 2.0:
                        await websocket.send_json(payload)
                        last_sent, last_signature = now, signature
                    job_states, scan_states = await _send_events(
                        websocket, database, job_states, scan_states
                    )
                    await _send_logs(websocket, settings, subscriptions, offsets)
                    last_audit_id = await _send_audits(websocket, database, last_audit_id)
                try:
                    raw = await asyncio.wait_for(
                        websocket.receive_text(), timeout=max(0.05, next_tick - loop.time())
                    )
                except TimeoutError:
                    continue
                try:
                    message = json.loads(raw)
                except json.JSONDecodeError:
                    continue  # geen JSON: negeren i.p.v. de verbinding te verbreken
                action = message.get("actie") if isinstance(message, dict) else None
                raw_job_id = message.get("job_id") if isinstance(message, dict) else None
                if action == "volg" and isinstance(raw_job_id, int):
                    subscriptions.add(raw_job_id)
                    path = settings.config_root / "logs" / f"job-{raw_job_id}.log"
                    offsets[raw_job_id] = _log_size(path)
                elif action == "stop_volgen" and isinstance(raw_job_id, int):
                    subscriptions.discard(raw_job_id)
                    offsets.pop(raw_job_id, None)
        except WebSocketDisconnect:
            return
        finally:
            ACTIVE_CLIENTS -= 1


__all__ = ["register_websocket", "tick"]
