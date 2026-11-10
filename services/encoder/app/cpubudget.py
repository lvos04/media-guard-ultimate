"""CPU-budget voor AV1: hoeveel threads mag één CPU-encode gebruiken?

Het budget is ``cores × percentage``, eerlijk verdeeld over het aantal gelijktijdige
CPU-encodes. ``cores`` is cgroup-bewust (containerlimiet telt, niet de host), zodat
Sonarr/Radarr/Plex en TrueNAS zelf altijd ruimte houden.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

from app.hostlimits import effective_cpu_count

# Decodethreads per GPU-encode (zie commandos.GPU_DECODE_THREADS): die tellen mee in het
# CPU-budget, anders gaat de machine met 4 GPU-jobs + CPU-jobs ver boven de limiet.
GPU_DECODE_RESERVE = 4


def budget_threads(percent: int, cpu_workers: int, cores: int | None = None,
                   gpu_workers: int = 0, reserve: int = GPU_DECODE_RESERVE) -> int:
    cores = cores if cores is not None else effective_cpu_count()
    percent = max(1, min(int(percent or 100), 100))
    workers = max(1, int(cpu_workers or 1))
    total = int(cores * percent / 100)
    free = total - max(0, int(gpu_workers or 0)) * reserve
    return max(1, max(free, 0) // workers)


def from_database(database: Any) -> dict[str, int]:
    row = database.fetch_one("SELECT av1_cpu_procent FROM instelling WHERE id=1")
    lane = database.fetch_one("SELECT aantal FROM worker_config WHERE baan='cpu'")
    gpu = database.fetch_one(
        "SELECT w.aantal FROM worker_config w WHERE w.baan='gpu' AND w.gepauzeerd=0")
    percent = int(row["av1_cpu_procent"]) if row else 100
    workers = int(lane["aantal"]) if lane else 1
    # alleen als er echt een GPU is (zonder kaart draait de GPU-baan niets)
    gpu_workers = int(gpu["aantal"]) if gpu and Path("/dev/dri").is_dir() else 0
    cores = effective_cpu_count()
    # Met hardwaredecode pakt de Arc de bron zelf uit: per GPU-job blijft maar ± 1 thread
    # (demux/mux) over voor de CPU, in plaats van 4 (rapport 3, R1: "≈ 2 van 28 kernen").
    from app.media.commandos import HW_DECODE_RESERVE, hw_decode_enabled

    reserve = HW_DECODE_RESERVE if hw_decode_enabled() else GPU_DECODE_RESERVE
    from app.cpuconfine import confine_prefix, encode_cpu_set

    confined = bool(confine_prefix(percent))
    if confined:
        # Harde grens: de encodes delen precies de kernen van de encodeset.
        set_size = len(encode_cpu_set(percent))
        per_encode = budget_threads(100, workers, set_size, gpu_workers, reserve=reserve)
        budget_total = set_size
    else:
        per_encode = budget_threads(percent, workers, cores, gpu_workers, reserve=reserve)
        budget_total = max(1, int(cores * max(1, min(percent, 100)) / 100))
    return {"percent": percent, "cpu_workers": workers, "gpu_workers": gpu_workers,
            "cores": cores, "threads_per_encode": per_encode,
            "gpu_decode_threads": reserve,
            "harde_grens": confined,
            # dezelfde rekensom als 'Grenzen' (≈ 21 van 28): budget, waarvan de GPU-jobs
            # hun decodethreads krijgen en de rest eerlijk over de CPU-encodes gaat
            "budget_threads": budget_total,
            "gpu_reserved_threads": gpu_workers * reserve,
            "threads_total": min(cores, per_encode * max(1, workers)
                                 + gpu_workers * reserve)}


def effective_cpu_percent(database: Any, *, container_mode: bool) -> int:
    """Eén CPU-waarde. In containermodus wint wat MediaGuard doorzet (av1_cpu_procent);
    native blijft de eigen instelling (cpu_max_percent) gelden."""

    row = database.fetch_one("SELECT cpu_max_percent,av1_cpu_procent FROM instelling WHERE id=1")
    if row is None:
        return 100
    value = row["av1_cpu_procent"] if container_mode else row["cpu_max_percent"]
    return max(10, min(int(value or 100), 100))


def unify_cpu_percent(database: Any, *, container_mode: bool) -> None:
    """Containermodus: de oude losse waarde (bv. 90 naast 75) gelijktrekken."""

    if container_mode:
        database.execute(
            "UPDATE instelling SET cpu_max_percent=av1_cpu_procent WHERE id=1 "
            "AND av1_cpu_procent IS NOT NULL AND cpu_max_percent IS NOT av1_cpu_procent")
