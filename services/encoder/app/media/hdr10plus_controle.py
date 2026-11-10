"""HDR10+ (SMPTE ST 2094-40) teruglezen en per frame vergelijken.

Bron en resultaat worden gedecodeerd met de systeem-ffprobe (FFmpeg 8.0:
HEVC-SEI resp. AV1 ITU-T T.35-OBU via libdav1d → frame-side-data
"HDR Dynamic Metadata SMPTE2094-40 (HDR10+)"). We gebruiken bewust de
``default``-uitvoer: de JSON-schrijver van ffprobe voegt herhaalde sleutels
(maxscl[3], percentielen, bezier-ankers) samen en zou verschillen verbergen.

Vergeleken wordt per presentatietijdstempel in vensters (begin + 25/50/75 %):
ieder vergeleken frame moet in het resultaat HDR10+ dragen met exact dezelfde
waarden (dus ook per scène). Een volledige decode van een speelfilm is te duur
voor iedere job; de vensters zijn gedocumenteerd in AV1_DV_PLAYBACK_MATRIX.md.
"""

from __future__ import annotations

import subprocess
from pathlib import Path
from typing import Any

HDR10PLUS_TYPE = "HDR Dynamic Metadata SMPTE2094-40 (HDR10+)"
START_FRAMES = 120
WINDOW_FRAMES = 48
MIN_OVERLAP = 0.9

Frame = tuple[tuple[str, str], ...]


def _intervals(duration: float, offset: float = 0.0) -> list[str]:
    """Leesvensters; ``offset`` = starttijd van het videospoor in dat bestand."""

    intervals = [f"%+#{START_FRAMES}"]
    if duration > 60:
        intervals += [
            f"{offset + duration * f:.3f}%+#{WINDOW_FRAMES}" for f in (0.25, 0.5, 0.75)
        ]
    return intervals


def video_start_time(path: str | Path) -> float:
    """Starttijd van v:0. Een remux begint vaak op 0, de bron (TS/Blu-ray) niet."""

    try:
        result = subprocess.run(
            [
                "ffprobe",
                "-v",
                "quiet",
                "-select_streams",
                "v:0",
                "-show_entries",
                "stream=start_time",
                "-of",
                "default=nw=1:nk=1",
                str(path),
            ],
            capture_output=True,
            text=True,
            timeout=120,
        )
        return float(result.stdout.strip().splitlines()[0])
    except (OSError, subprocess.SubprocessError, ValueError, IndexError):
        return 0.0


def hdr10plus_frames(
    path: str | Path, interval: str, offset: float = 0.0
) -> dict[str, Frame | None]:
    """Tijdstempel (s, relatief aan ``offset``) → HDR10+-velden (volgorde behouden) of None.

    Zonder correctie voor de starttijd vallen bron- en resultaatframes van een
    bron met start_time ≠ 0 nooit op dezelfde tijdstempel en faalt de controle.
    """

    try:
        result = subprocess.run(
            [
                "ffprobe",
                "-v",
                "quiet",
                "-select_streams",
                "v:0",
                "-show_frames",
                "-read_intervals",
                interval,
                "-show_entries",
                "frame=best_effort_timestamp_time:side_data",
                "-of",
                "default",
                str(path),
            ],
            capture_output=True,
            text=True,
            timeout=900,
        )
    except (OSError, subprocess.SubprocessError):
        return {}
    frames: dict[str, Frame | None] = {}
    timestamp: str | None = None
    current: list[tuple[str, str]] | None = None
    in_hdr10plus = False
    found: Frame | None = None
    for line in result.stdout.splitlines():
        if line == "[FRAME]":
            timestamp, found = None, None
        elif line.startswith("best_effort_timestamp_time="):
            try:
                # +0.0 maakt van -0.000 gewoon 0.000 (zelfde sleutel als het resultaat)
                timestamp = f"{round(float(line.split('=', 1)[1]) - offset, 3) + 0.0:.3f}"
            except ValueError:
                timestamp = None
        elif line == f"side_data_type={HDR10PLUS_TYPE}":
            in_hdr10plus, current = True, []
        elif line == "[/SIDE_DATA]" and in_hdr10plus:
            found = tuple(current or [])
            in_hdr10plus, current = False, None
        elif in_hdr10plus and current is not None and "=" in line:
            key, _, value = line.partition("=")
            current.append((key, value))
        elif line == "[/FRAME]" and timestamp is not None:
            frames[timestamp] = found
    return frames


def controleer_hdr10plus(
    source: str | Path, result: str | Path, duration: float
) -> tuple[bool, str, dict[str, Any]]:
    compared = with_metadata = 0
    mismatches: list[str] = []
    source_offset = video_start_time(source)
    result_offset = video_start_time(result)
    for interval, source_interval, result_interval in zip(
        _intervals(duration),
        _intervals(duration, source_offset),
        _intervals(duration, result_offset),
        strict=True,
    ):
        bron = hdr10plus_frames(source, source_interval, source_offset)
        resultaat = hdr10plus_frames(result, result_interval, result_offset)
        common = sorted(set(bron) & set(resultaat), key=float)
        source_frames = [t for t in bron if bron[t] is not None]
        if source_frames and len(common) < MIN_OVERLAP * len(source_frames):
            mismatches.append(f"venster {interval}: te weinig gemeenschappelijke frames")
        for timestamp in common:
            if bron[timestamp] is None:
                continue
            compared += 1
            if resultaat[timestamp] is None:
                mismatches.append(f"{timestamp}s: HDR10+ ontbreekt")
            elif resultaat[timestamp] != bron[timestamp]:
                mismatches.append(f"{timestamp}s: HDR10+-waarden wijken af")
            else:
                with_metadata += 1
    details: dict[str, Any] = {
        "windows": _intervals(duration),
        "frames_compared": compared,
        "frames_identical": with_metadata,
        "mismatches": mismatches[:20],
        "method": "ffprobe default-writer, per presentatietijdstempel (vanaf starttijd)",
        "start_time": {"bron": source_offset, "resultaat": result_offset},
    }
    if compared == 0:
        return False, "HDR10+: geen vergelijkbare frames gevonden", details
    if mismatches:
        return False, f"HDR10+ dynamische metadata niet behouden: {mismatches[0]}", details
    return True, f"HDR10+ identiek op {with_metadata}/{compared} vergeleken frames", details
