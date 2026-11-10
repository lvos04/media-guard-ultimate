"""Werkelijke CPU-, geheugen- en temperatuurgrenzen van dit proces.

In een container (TrueNAS-app) ziet psutil de hele host: 64 GB RAM en 16 cores,
terwijl de cgroup misschien 8 GB en 4 cores toestaat. Encoderparalleliteit op
basis van hostwaarden laat de container dan door de OOM-killer stoppen. Deze
helpers nemen het minimum van host, cgroup v2 en CPU-affiniteit.
"""

from __future__ import annotations

import math
import os
from pathlib import Path
from typing import NamedTuple

import psutil

CGROUP_ROOT = Path("/sys/fs/cgroup")
# Alleen echte CPU-pakketsensoren; schijven/NVMe/acpitz geven anders vals alarm.
CPU_SENSORS = ("coretemp", "k10temp", "zenpower", "cpu_thermal")
CPU_SENSOR_LABELS = ("package id 0", "tctl", "tdie")


class Memory(NamedTuple):
    total: int
    available: int
    # deel van ``available`` dat uit de ZFS-cache (ARC) komt: die is op TrueNAS direct
    # vrij te maken, maar Linux telt hem als "gebruikt" (rapport 8, A5)
    zfs_reclaimable: int = 0


ARCSTATS = Path("/proc/spl/kstat/zfs/arcstats")


def zfs_reclaimable_bytes(path: Path = ARCSTATS) -> int:
    """ARC-grootte boven het minimum (c_min): dat geeft ZFS meteen vrij als een
    programma geheugen vraagt. 0 als arcstats niet leesbaar is (geen ZFS)."""
    values: dict[str, int] = {}
    try:
        for line in path.read_text(encoding="utf-8").splitlines():
            parts = line.split()
            if len(parts) == 3 and parts[0] in ("size", "c_min"):
                values[parts[0]] = int(parts[2])
    except (OSError, ValueError):
        return 0
    return max(0, values.get("size", 0) - values.get("c_min", 0))


def _read(path: Path) -> str | None:
    try:
        return path.read_text(encoding="ascii").strip()
    except OSError:
        return None


def cgroup_cpu_limit(root: Path = CGROUP_ROOT) -> float | None:
    """cpu.max "quota period" -> aantal cores; None = onbegrensd of geen cgroup v2."""

    raw = _read(root / "cpu.max")
    if not raw:
        return None
    parts = raw.split()
    if len(parts) != 2 or parts[0] == "max":
        return None
    try:
        quota, period = int(parts[0]), int(parts[1])
    except ValueError:
        return None
    return quota / period if quota > 0 and period > 0 else None


def effective_cpu_count(root: Path = CGROUP_ROOT) -> int:
    try:
        count = len(os.sched_getaffinity(0))
    except (AttributeError, OSError):
        count = os.cpu_count() or 1
    limit = cgroup_cpu_limit(root)
    if limit is not None:
        count = min(count, max(1, math.floor(limit)))
    return max(1, count)


def effective_memory(root: Path = CGROUP_ROOT, arcstats: Path = ARCSTATS) -> Memory:
    host = psutil.virtual_memory()
    total, available = int(host.total), int(host.available)
    zfs = min(zfs_reclaimable_bytes(arcstats), max(0, total - available))
    available += zfs
    raw_max = _read(root / "memory.max")
    raw_current = _read(root / "memory.current")
    if raw_max and raw_max != "max":
        try:
            limit = int(raw_max)
            used = int(raw_current) if raw_current else 0
        except ValueError:
            return Memory(total, available, zfs)
        if 0 < limit < total:
            # de ARC hoort bij de host, niet bij de container-cgroup
            total = limit
            available = min(available, max(0, limit - used))
            zfs = min(zfs, available)
    return Memory(total, available, zfs)


def cpu_temperature() -> float | None:
    """Pakkettemperatuur van de CPU (coretemp/k10temp), niet het maximum van alle sensoren."""

    try:
        sensors = psutil.sensors_temperatures()
    except (AttributeError, OSError):
        return None
    for chip in CPU_SENSORS:
        entries = sensors.get(chip) or []
        if not entries:
            continue
        for entry in entries:
            if (entry.label or "").strip().lower() in CPU_SENSOR_LABELS:
                return round(float(entry.current), 1)
        return round(max(float(entry.current) for entry in entries), 1)
    return None


def oom_kill_count(root: Path = CGROUP_ROOT) -> int:
    """Aantal processen dat de kernel in deze container stopte wegens geheugentekort."""
    raw = _read(root / "memory.events") or ""
    for line in raw.splitlines():
        key, _, value = line.partition(" ")
        if key == "oom_kill":
            try:
                return int(value)
            except ValueError:
                return 0
    return 0
