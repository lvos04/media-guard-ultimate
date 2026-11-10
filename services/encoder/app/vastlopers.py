"""Vastlopers: encodes die 'geen voortgang' meer maken (rapport 6, N2).

- Elke vastloper wordt vastgelegd (baan, plek, encoder, percentage, diagnose).
- Loopt een baan in 12 uur 3 keer of vaker vast, dan draait die baan tijdelijk met één plek
  (de tweede plek deelt kernen en geheugen met de eerste; dat was het patroon in rapport 6).
- Loopt dezelfde bron twee keer vast, dan krijgt hij een andere route (GPU als dat kan,
  anders een snellere CPU-instelling); na een vastloper op die andere route geeft de engine
  het op met een duidelijke reden in plaats van een derde identieke poging.
"""

from __future__ import annotations

import json
import logging
from datetime import UTC, datetime, timedelta
from typing import Any

LOGGER = logging.getLogger(__name__)

LANE_WINDOW_HOURS = 12
LANE_THRESHOLD = 3
SOURCE_WINDOW_DAYS = 7
FAST_CPU_PRESET = 10
REROUTE_REASON = "Twee keer vastgelopen op dezelfde route — opnieuw via een andere route"
GIVE_UP_TYPE = "VastloperOpgegeven"


def _now() -> datetime:
    return datetime.now(UTC)


def record(database: Any, job_id: int, *, percent: float, diagnose: str) -> dict[str, Any]:
    row = database.fetch_one(
        "SELECT j.bestand_id,j.baan,j.worker_id,b.encoder FROM job j "
        "LEFT JOIN besluit b ON b.id=j.besluit_id WHERE j.id=?", (job_id,))
    lane = str(row["baan"]) if row is not None and row["baan"] else "cpu"
    database.execute_insert(
        "INSERT INTO vastloper(job_id,bestand_id,baan,worker_id,encoder,percent,tijd,diagnose) "
        "VALUES (?,?,?,?,?,?,?,?)",
        (job_id, row["bestand_id"] if row is not None else None, lane,
         row["worker_id"] if row is not None else None,
         row["encoder"] if row is not None else None, percent, _now().isoformat(),
         diagnose[-8000:]))
    source_count = 0
    if row is not None and row["bestand_id"] is not None:
        since = (_now() - timedelta(days=SOURCE_WINDOW_DAYS)).isoformat()
        source_count = int(database.fetch_one(
            "SELECT COUNT(*) FROM vastloper WHERE bestand_id=? AND tijd>=?",
            (row["bestand_id"], since))[0])
    return {"lane": lane, "source_count": source_count, "lane_count": lane_count(database, lane)}


def lane_count(database: Any, lane: str) -> int:
    since = (_now() - timedelta(hours=LANE_WINDOW_HOURS)).isoformat()
    try:
        row = database.fetch_one(
            "SELECT COUNT(*) FROM vastloper WHERE baan=? AND tijd>=?", (lane, since))
    except Exception:  # noqa: BLE001 - oude database
        return 0
    return int(row[0]) if row is not None else 0


def lane_cap(database: Any, lane: str, desired: int) -> int:
    """Aantal plekken dat de planner mag vullen: 1 zolang de baan te vaak vastliep."""
    if desired <= 1:
        return desired
    return 1 if lane_count(database, lane) >= LANE_THRESHOLD else desired


def lane_notice(database: Any, lane: str) -> str | None:
    count = lane_count(database, lane)
    if count < LANE_THRESHOLD:
        return None
    return (f"{count}× vastgelopen in {LANE_WINDOW_HOURS} uur — deze baan draait tijdelijk "
            "met één plek")


def summary(database: Any) -> dict[str, Any]:
    since = (_now() - timedelta(hours=LANE_WINDOW_HOURS)).isoformat()
    try:
        rows = database.fetch_all(
            "SELECT baan,worker_id,COUNT(*) AS n FROM vastloper WHERE tijd>=? "
            "GROUP BY baan,worker_id", (since,))
    except Exception:  # noqa: BLE001
        return {"per_plek": [], "teruggeschaald": []}
    return {
        "per_plek": [{"baan": r["baan"], "plek": r["worker_id"], "aantal": int(r["n"])}
                     for r in rows],
        "teruggeschaald": [lane for lane in ("cpu", "gpu")
                           if lane_count(database, lane) >= LANE_THRESHOLD],
    }


def _gpu_lane_usable(database: Any) -> bool:
    row = database.fetch_one("SELECT aantal,gepauzeerd FROM worker_config WHERE baan='gpu'")
    return row is not None and int(row["aantal"] or 0) > 0 and not int(row["gepauzeerd"] or 0)


def plan_reroute(database: Any, job_id: int, decision: Any) -> tuple[str, str]:
    """('gpu'|'cpu_snel'|'opgeven', uitleg) voor een bron die opnieuw vastliep."""
    row = database.fetch_one("SELECT route_wissel,baan FROM job WHERE id=?", (job_id,))
    if row is not None and int(row["route_wissel"] or 0):
        return "opgeven", ("liep ook op de andere route vast; MediaGuard probeert dit bestand "
                           "niet nog eens op dezelfde manier")
    gpu_ok = (not getattr(decision, "dolby_vision", False)
              and str(getattr(decision, "hdr10plus_route", "") or "") != "behouden"
              and str(row["baan"] if row is not None else "cpu") == "cpu"
              and _gpu_lane_usable(database))
    if gpu_ok:
        return "gpu", "opnieuw via de GPU (Intel Quick Sync)"
    return "cpu_snel", f"opnieuw op de CPU met een snellere instelling (preset {FAST_CPU_PRESET})"


def apply_reroute(database: Any, job_id: int, decision: Any, route: str, reason: str) -> None:
    row = database.fetch_one("SELECT besluit_id,mg_inst FROM job WHERE id=?", (job_id,))
    if row is None:
        return
    try:
        inst = json.loads(str(row["mg_inst"] or "{}"))
    except ValueError:
        inst = {}
    if not isinstance(inst, dict):
        inst = {}
    if route == "gpu":
        update = {"baan": "gpu", "encoder": "av1_qsv",
                  "herstel_hdr_na": bool(getattr(decision, "behoud_hdr", False)),
                  "reden": f"{REROUTE_REASON}: {reason}"[:500]}
        lane, encoder = "gpu", "av1_qsv"
        inst["gpu_voor_hdr10"] = True
    else:
        update = {"reden": f"{REROUTE_REASON}: {reason}"[:500]}
        lane, encoder = "cpu", str(getattr(decision, "encoder", "") or "libsvtav1")
        if encoder == "libaom-av1":
            # Gebruikersrapport (een 4K-film, 0,05× en ± 40 uur): de 'andere route' na
            # vastlopers moet snel zijn. libaom (alleen nodig om HDR10+ dynamisch te houden)
            # wordt SVT-AV1; de statische HDR10 blijft, de dynamische metadata vervalt.
            encoder = "libsvtav1"
            update.update({"encoder": "libsvtav1", "hdr10plus_route": "hdr10_fallback",
                           "reden": f"{REROUTE_REASON}: {reason}; HDR10+ wordt HDR10"[:500]})
        for klasse in ("2160", "1080", "720"):
            inst[f"preset_{klasse}"] = max(int(inst.get(f"preset_{klasse}") or 0),
                                           FAST_CPU_PRESET)
    new_decision = decision.model_copy(update=update)
    with database._write_lock, database.connection() as connection:  # noqa: SLF001
        connection.execute("BEGIN IMMEDIATE")
        connection.execute(
            "UPDATE besluit SET baan=?,encoder=?,reden=?,payload=? WHERE id=?",
            (lane, encoder, new_decision.reden, new_decision.model_dump_json(),
             int(row["besluit_id"])))
        connection.execute(
            """
            UPDATE job SET status='wacht',baan=?,mg_inst=?,route_wissel=1,fout=?,foutsoort=NULL,
                           proces_pid=NULL,worker_id=NULL,cache_reservering=0,percent=0,
                           klaar_op=NULL,resultaat_pad=NULL,resultaat_grootte=NULL
            WHERE id=?
            """,
            (lane, json.dumps(inst, ensure_ascii=False), f"{REROUTE_REASON}: {reason}"[:500],
             job_id))
        connection.commit()
    LOGGER.warning("Job %s liep twee keer vast; %s", job_id, reason)
