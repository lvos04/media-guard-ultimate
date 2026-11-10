"""Harde CPU-grens voor alle encodeprocessen (rapport 5, T1/T7).

Een threadbudget is een hint: SVT-AV1, libaom en de FFmpeg-decoder maken samen meer
threads dan gevraagd, waardoor twee CPU-encodes de machine urenlang op ± 90 % hielden
(limiet 75 %) en daarmee ook de GPU-baan blokkeerden.

Nu draait elk mediaproces op een vaste set van ⌊limiet × cores⌋ processorkernen
(CPU-affiniteit via ``taskset``, die alle threads van het proces erven). Samen kunnen de
encodes zo nooit boven de limiet komen. De web-UI, de API en MediaGuard zelf draaien
buiten die set en houden de overige kernen.

Een cgroup-v2-subgroep met ``cpu.max`` zou hetzelfde doen, maar kan in een TrueNAS-
container meestal niet worden aangemaakt (alleen-lezen cgroupfs en de "geen interne
processen"-regel); affiniteit werkt altijd.
"""

from __future__ import annotations

import math
import os
import shutil
from typing import Any

MIN_FREE_CORES = 1  # altijd minstens één kern vrij voor web/API, ook bij 100 %


def available_cpus() -> list[int]:
    try:
        return sorted(os.sched_getaffinity(0))
    except (AttributeError, OSError):
        return list(range(os.cpu_count() or 1))


def encode_cpu_set(percent: int, cpus: list[int] | None = None) -> list[int]:
    """De kernen waarop encodes mogen draaien: de hoogste ⌊percent × n⌋ kernen.

    Kern 0 (en de laagste nummers) blijven vrij: daar draaien interrupts en het systeem.
    """
    cpus = sorted(cpus if cpus is not None else available_cpus())
    if not cpus:
        return []
    percent = max(1, min(int(percent or 100), 100))
    count = max(1, math.floor(len(cpus) * percent / 100))
    if len(cpus) > MIN_FREE_CORES:
        count = min(count, len(cpus) - MIN_FREE_CORES)
    return cpus[-count:]


def cpu_list_text(cpus: list[int]) -> str:
    """[2,3,4,7] -> '2-4,7' (taskset-notatie)."""
    parts: list[str] = []
    start = prev = None
    for cpu in sorted(cpus):
        if start is None:
            start = prev = cpu
        elif cpu == (prev or 0) + 1:
            prev = cpu
        else:
            parts.append(f"{start}-{prev}" if start != prev else str(start))
            start = prev = cpu
    if start is not None:
        parts.append(f"{start}-{prev}" if start != prev else str(start))
    return ",".join(parts)


def confinement_available() -> bool:
    return shutil.which("taskset") is not None and len(available_cpus()) > 1


def partition(cpus: list[int], index: int, count: int) -> list[int]:
    """Deel ``index`` (0-gebaseerd) van ``count`` aaneengesloten, zo gelijk mogelijke delen.

    Twee CPU-encodes op dezelfde kernenset kregen een oneerlijke verdeling: de encode met de
    meeste threads wint, de tweede (plek cpu-2) kwam soms een kwartier lang niet vooruit
    (rapport 6, N2). Elke CPU-plek krijgt daarom zijn eigen kernen."""
    if count <= 1 or len(cpus) < count:
        return list(cpus)
    index = max(0, min(index, count - 1))
    size, rest = divmod(len(cpus), count)
    start = index * size + min(index, rest)
    return list(cpus[start:start + size + (1 if index < rest else 0)])


def confine_prefix(percent: int, part: tuple[int, int] | None = None) -> list[str]:
    """``taskset -c <set>`` vóór het mediacommando, of niets als dat niet kan.

    ``part`` = (plek-index, aantal CPU-plekken): dan alleen het eigen deel van de set."""
    from app import afstelling

    if not afstelling.get("cpu_harde_grens") or not confinement_available():
        return []
    cpus = encode_cpu_set(percent)
    if not cpus:
        return []
    if part is not None:
        cpus = partition(cpus, part[0], part[1])
    if len(cpus) >= len(available_cpus()):
        return []
    return ["taskset", "-c", cpu_list_text(cpus)]


def job_part(database: Any, job_id: int) -> tuple[int, int] | None:
    """Plek-index en aantal plekken voor een CPU-job (``cpu-2`` → (1, 2)), anders None."""
    try:
        row = database.fetch_one("SELECT baan,worker_id FROM job WHERE id=?", (job_id,))
        lane = database.fetch_one("SELECT aantal FROM worker_config WHERE baan='cpu'")
    except Exception:  # noqa: BLE001
        return None
    if row is None or str(row["baan"] or "") != "cpu" or lane is None:
        return None
    count = int(lane["aantal"] or 1)
    worker = str(row["worker_id"] or "")
    if count <= 1 or not worker.startswith("cpu-"):
        return None
    try:
        slot = int(worker.split("-", 1)[1]) - 1
    except ValueError:
        return None
    return (slot % count, count)


def encode_percent(database: Any) -> int:
    """De ingestelde CPU-limiet (MediaGuards waarde wint in containermodus)."""
    try:
        row = database.fetch_one(
            "SELECT cpu_max_percent,av1_cpu_procent FROM instelling WHERE id=1")
    except Exception:  # noqa: BLE001 - zonder instellingen geen extra grens
        return 100
    if row is None:
        return 100
    value = row["av1_cpu_procent"] if row["av1_cpu_procent"] is not None else row[
        "cpu_max_percent"]
    return max(10, min(int(value or 100), 100))


def status(database: Any) -> dict[str, Any]:
    percent = encode_percent(database)
    cpus = encode_cpu_set(percent)
    active = bool(confine_prefix(percent))
    return {"actief": active, "procent": percent, "kernen": len(cpus),
            "kernen_totaal": len(available_cpus()), "set": cpu_list_text(cpus),
            "uitleg": (f"Encodes draaien op {len(cpus)} van {len(available_cpus())} kernen "
                       f"({percent} %); de rest blijft vrij voor de app en andere diensten"
                       if active else "Geen harde CPU-grens mogelijk (taskset ontbreekt)")}
