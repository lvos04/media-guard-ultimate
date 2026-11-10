"""GPU-plekken optimaliseren (rapport 7 §4.5).

Hoeveel encodes tegelijk haalt de Arc de hoogste totale snelheid? Dat hangt af van de kaart,
de driver, lookahead (videogeheugen) en de variant. In plaats van te gokken meet de engine het
zelf: 1, 2 en 4 korte proefencodes tegelijk op hetzelfde synthetische 1080p-beeld, en kiest
het kleinste aantal dat (vrijwel) de hoogste totale fps haalt. Het resultaat is een bovengrens
voor de planner; het in MediaGuard ingestelde aantal blijft het maximum.

Zonder interactie: de meting draait één keer na het vinden van de GPU, en opnieuw als de
'vingerafdruk' verandert (andere kaart/variant/driverversie of lookahead aan/uit), alleen als
de GPU-baan op dat moment niets doet.
"""

from __future__ import annotations

import asyncio
import json
import logging
import os
import re
import time
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from app.media import gpuvarianten

LOGGER = logging.getLogger(__name__)

STATE_KEY = "gpu_plekken"
CANDIDATES = (1, 2, 4)
CLIP_SECONDS = 8
CLIP_FPS = 24
CLIP_SIZE = "1920x1080"
GOOD_ENOUGH = 0.95  # het kleinste aantal plekken met ≥ 95 % van de hoogste totale fps
RUN_TIMEOUT = 180.0
_RUNNING = {"busy": False}


def _now() -> str:
    return datetime.now(UTC).isoformat()


def bench_command(variant: dict[str, Any], render_node: str, *, lookahead: bool) -> list[str]:
    """Eén proefencode: synthetisch 10-bit 1080p-beeld door av1_qsv, uitvoer weggegooid."""
    from app.media import commandos

    v = {**variant, "pix": "p010le"}
    extra = 48 if lookahead else 16
    args = ["ffmpeg", "-hide_banner", "-nostats", "-loglevel", "error", "-y",
            *gpuvarianten.hw_args(v, render_node),
            "-f", "lavfi", "-i",
            f"testsrc2=size={CLIP_SIZE}:rate={CLIP_FPS}:duration={CLIP_SECONDS}",
            "-vf", gpuvarianten.upload_filter(v, extra),
            *gpuvarianten.codec_args(v), "-b:v", "3000k", "-maxrate", "7500k",
            "-bufsize", "12000k"]
    if not gpuvarianten.is_vaapi(v):
        args += ["-preset", commandos.GPU_PRESETS["1080"]]
        if lookahead:
            args += commandos.lookahead_args("1080")
    return [*args, "-f", "null", "-"]


def _vram_bytes(pids: list[int]) -> int | None:
    """Videogeheugen van de proefprocessen uit /proc/<pid>/fdinfo (i915/xe, best effort)."""
    total = 0
    seen = False
    for pid in pids:
        directory = Path(f"/proc/{pid}/fdinfo")
        try:
            entries = list(directory.iterdir())
        except OSError:
            continue
        for entry in entries:
            try:
                text = entry.read_text(errors="replace")
            except OSError:
                continue
            if "drm-driver" not in text:
                continue
            for match in re.finditer(
                    r"^drm-(?:resident|total)-(?:local0|vram\d*):\s*(\d+)\s*(KiB|MiB)?",
                    text, re.M):
                value = int(match.group(1)) * (1024 if match.group(2) == "KiB" else
                                               1024 * 1024 if match.group(2) == "MiB" else 1)
                total += value
                seen = True
                break
    return total if seen else None


async def _run_parallel(command: list[str], count: int) -> dict[str, Any]:
    started = time.monotonic()
    processes = [await asyncio.create_subprocess_exec(
        *command, stdout=asyncio.subprocess.DEVNULL, stderr=asyncio.subprocess.PIPE)
        for _ in range(count)]
    peak_vram: int | None = None

    async def sample_vram() -> None:
        nonlocal peak_vram
        while any(p.returncode is None for p in processes):
            value = _vram_bytes([p.pid for p in processes if p.returncode is None])
            if value is not None:
                peak_vram = max(peak_vram or 0, value)
            await asyncio.sleep(0.5)

    sampler = asyncio.create_task(sample_vram())
    try:
        results = await asyncio.wait_for(
            asyncio.gather(*(p.communicate() for p in processes)), timeout=RUN_TIMEOUT)
    except TimeoutError:
        for process in processes:
            if process.returncode is None:
                process.kill()
        return {"plekken": count, "ok": False, "fout": "duurde te lang"}
    finally:
        sampler.cancel()
    elapsed = max(0.001, time.monotonic() - started)
    failed = [r for p, r in zip(processes, results, strict=True) if p.returncode != 0]
    if failed:
        tail = (failed[0][1] or b"").decode(errors="replace").strip().splitlines()[-1:] or [""]
        return {"plekken": count, "ok": False, "fout": tail[0][:200]}
    frames = count * CLIP_SECONDS * CLIP_FPS
    return {"plekken": count, "ok": True, "fps": round(frames / elapsed, 1),
            "seconden": round(elapsed, 1),
            "vram_mb": round(peak_vram / 1024 / 1024) if peak_vram else None}


def choose(results: list[dict[str, Any]]) -> int | None:
    good = [r for r in results if r.get("ok")]
    if not good:
        return None
    best = max(float(r["fps"]) for r in good)
    return min(int(r["plekken"]) for r in good if float(r["fps"]) >= best * GOOD_ENOUGH)


def fingerprint(settings: Any, ffmpeg_version: str | None, lookahead: bool) -> str:
    variant = settings.gpu_variant or gpuvarianten.STANDAARD
    return json.dumps({"node": str(settings.render_node), "variant": variant.get("id"),
                       "ffmpeg": ffmpeg_version, "lookahead": lookahead,
                       "driver": _driver_version()}, sort_keys=True)


def _driver_version() -> str | None:
    for path in ("/usr/lib/x86_64-linux-gnu/dri/iHD_drv_video.so",):
        try:
            return str(int(os.stat(path).st_mtime))
        except OSError:
            continue
    return None


def load(database: Any) -> dict[str, Any] | None:
    try:
        row = database.fetch_one("SELECT waarde FROM staat WHERE sleutel=?", (STATE_KEY,))
    except Exception:  # noqa: BLE001 - tabel nog niet aangemaakt
        return None
    if row is None:
        return None
    try:
        value = json.loads(str(row["waarde"]))
    except ValueError:
        return None
    return value if isinstance(value, dict) else None


def _save(database: Any, value: dict[str, Any]) -> None:
    database.execute(
        "INSERT INTO staat(sleutel,waarde,tijd) VALUES (?,?,?) ON CONFLICT(sleutel) DO "
        "UPDATE SET waarde=excluded.waarde,tijd=excluded.tijd",
        (STATE_KEY, json.dumps(value, ensure_ascii=False), _now()))


def lane_cap(database: Any, desired: int) -> int:
    """Bovengrens voor de GPU-baan uit de laatste meting (nooit boven het ingestelde aantal)."""
    from app import afstelling

    if desired <= 1 or not afstelling.get("gpu_plekken_auto"):
        return desired
    state = load(database) or {}
    best = state.get("beste")
    return min(desired, int(best)) if isinstance(best, int) and best >= 1 else desired


async def run(database: Any, settings: Any, *, configured: int,
              ffmpeg_version: str | None = None) -> dict[str, Any]:
    """Meet 1/2/4 plekken (≤ ingesteld aantal) en sla de keuze op."""
    from app.media import commandos

    if _RUNNING["busy"]:
        return {"bezig": True}
    _RUNNING["busy"] = True
    try:
        variant = settings.gpu_variant or gpuvarianten.STANDAARD
        lookahead = commandos.lookahead_enabled() and not gpuvarianten.is_vaapi(variant)
        command = bench_command(variant, str(settings.render_node), lookahead=lookahead)
        counts = [c for c in CANDIDATES if c <= max(1, configured)] or [1]
        results = []
        for count in counts:
            result = await _run_parallel(command, count)
            results.append(result)
            if not result.get("ok"):
                break
        best = choose(results)
        value = {"gemeten_op": _now(), "vingerafdruk": fingerprint(
            settings, ffmpeg_version, lookahead), "resultaten": results, "beste": best,
            "ingesteld": configured,
            "uitleg_nl": (f"Gemeten: {', '.join(_describe(r) for r in results)}. "
                          + (f"MediaGuard gebruikt {best} GPU-plek(ken)." if best else
                             "Meting mislukt; het ingestelde aantal blijft gelden."))}
        _save(database, value)
        LOGGER.info("GPU-plekken gemeten: %s", value["uitleg_nl"])
        return value
    finally:
        _RUNNING["busy"] = False


def _describe(result: dict[str, Any]) -> str:
    if not result.get("ok"):
        return f"{result['plekken']} tegelijk mislukt"
    vram = f", {result['vram_mb']} MB videogeheugen" if result.get("vram_mb") else ""
    return f"{result['plekken']} tegelijk {str(result['fps']).replace('.', ',')} fps{vram}"


def due(database: Any, settings: Any, gpu_enabled: bool, gpu_busy: bool,
        ffmpeg_version: str | None) -> bool:
    """Moet er (opnieuw) gemeten worden? Alleen met een werkende, stille GPU-baan."""
    from app import afstelling
    from app.media import commandos

    if not gpu_enabled or gpu_busy or not afstelling.get("gpu_plekken_auto"):
        return False
    variant = settings.gpu_variant or gpuvarianten.STANDAARD
    lookahead = commandos.lookahead_enabled() and not gpuvarianten.is_vaapi(variant)
    state = load(database)
    return state is None or state.get("vingerafdruk") != fingerprint(
        settings, ffmpeg_version, lookahead)
