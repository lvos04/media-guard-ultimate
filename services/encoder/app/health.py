"""Eerlijke dependency- en QSV-diagnostiek voor de healthcheck."""

from __future__ import annotations

import asyncio
import os
import re
import shutil
from pathlib import Path
from typing import Any


async def _output(arguments: list[str], timeout: float = 15.0) -> tuple[int, str]:
    try:
        process = await asyncio.create_subprocess_exec(
            *arguments,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.STDOUT,
        )
        stdout, _ = await asyncio.wait_for(process.communicate(), timeout=timeout)
    except (FileNotFoundError, TimeoutError):
        return 127, ""
    return process.returncode or 0, stdout.decode("utf-8", errors="replace")


_DEPENDENCY_CACHE: dict[str, tuple[float, dict[str, Any]]] = {}
DEPENDENCY_CACHE_SECONDS = 300.0


async def cached_dependency_report(
    render_node: Path | None = None, max_age: float = DEPENDENCY_CACHE_SECONDS
) -> dict[str, Any]:
    """Zelfde rapport, maar hooguit eens per ``max_age`` s gemeten: het start vijf
    subprocessen (o.a. vainfo op de drukke GPU) en mag een API-aanroep onder volle
    belasting niet seconden laten duren."""
    import time

    key = str(render_node)
    hit = _DEPENDENCY_CACHE.get(key)
    now = time.monotonic()
    if hit is not None and now - hit[0] < max_age:
        return hit[1]
    report = await dependency_report(render_node)
    _DEPENDENCY_CACHE[key] = (now, report)
    return report


async def dependency_report(render_node: Path | None = None) -> dict[str, Any]:
    tools = {
        name: shutil.which(name) is not None
        for name in ("ffmpeg", "ffprobe", "mkvmerge", "dovi_tool")
    }
    version_code, version_output = await _output(["ffmpeg", "-version"])
    version_match = re.search(r"ffmpeg version\s+(\S+)", version_output)
    encoders_code, encoders_output = await _output(["ffmpeg", "-hide_banner", "-encoders"])
    dolby_code, dolby_output = await _output(["ffmpeg", "-hide_banner", "-h", "encoder=libsvtav1"])
    vpl_code, vpl_output = await _output(["ldconfig", "-p"])
    # MG_RENDER_NODE (TrueNAS: de doorgegeven GPU) geldt overal, niet alleen in de brug.
    if render_node is None:
        render_node = Path(os.environ.get("MG_RENDER_NODE", "").strip() or "/dev/dri/renderD128")
    vaapi_code, vaapi_output = await _output(
        ["vainfo", "--display", "drm", "--device", str(render_node)]
    )
    render_readable = render_node.exists() and os.access(render_node, os.R_OK)
    render_writable = render_node.exists() and os.access(render_node, os.W_OK)
    libsvtav1 = encoders_code == 0 and "libsvtav1" in encoders_output
    av1_qsv = encoders_code == 0 and "av1_qsv" in encoders_output
    dolbyvision = dolby_code == 0 and "dolbyvision" in dolby_output.lower()
    # Toegang is nog geen werkende QSV: "qsv_gereed" wordt pas waar na een geslaagde
    # echte proefencode (capabilities.py vult dat in waar het wordt getoond).
    gpu_access = av1_qsv and render_readable and render_writable
    return {
        "tools": tools,
        "ffmpeg_version": version_match.group(1) if version_match else None,
        "libsvtav1": libsvtav1,
        "av1_qsv": av1_qsv,
        "dolbyvision": dolbyvision,
        "onevpl": vpl_code == 0 and "libvpl.so" in vpl_output,
        "vaapi": vaapi_code == 0 and "VAProfile" in vaapi_output,
        "render_node": {
            "pad": str(render_node),
            "aanwezig": render_node.exists(),
            "leesbaar": render_readable,
            "schrijfbaar": render_writable,
        },
        "gpu_toegankelijk": gpu_access,
        "qsv_gereed": False,
        "media_stack_gereed": version_code == 0 and libsvtav1 and dolbyvision,
    }
