"""Zelflerende routekeuze (gebruikersrapport: anime op de GPU half afgekeurd op VMAF).

Twee dingen worden onthouden in ``route_leer``:

- ``gpu_vmaf``: per serie (de map boven ``Season xx``) hoe vaak de GPU de VMAF-doelwaarde
  niet haalde. Vanaf ``GPU_REJECT_LIMIT`` keer binnen ``LEARN_DAYS`` dagen gaat de rest van
  die serie direct via de CPU. Na ``LEARN_DAYS`` dagen zonder nieuwe afkeuring vervalt het.
- ``geen_winst``: per bronband (codec, resolutieklasse, bitrateband van 2 Mbit/s) hoe vaak
  het AV1-resultaat groter werd dan de bron. Vanaf ``NO_GAIN_LIMIT`` keer worden zulke
  bronnen overgeslagen ("geen winst verwacht").

De beslissing (``build_plan``) is een pure functie zonder database; daarom staat de
geleerde stand in het geheugen en wordt hij ververst door de planner (``refresh``).
"""

from __future__ import annotations

import re
import time
from datetime import UTC, datetime, timedelta
from pathlib import PurePosixPath
from typing import Any

GPU_REJECT_LIMIT = 2
NO_GAIN_LIMIT = 2
LEARN_DAYS = 30
REFRESH_SECONDS = 60.0
BITRATE_BAND_KBPS = 2000
_SEASON_DIR = re.compile(r"^(season|seizoen|staffel|saison)\s*\d+$|^specials?$", re.I)

_CACHE: dict[str, dict[str, Any]] = {}
_LOADED_AT = {"t": 0.0}


def series_key(path: str | PurePosixPath) -> str:
    """De serie- of filmmap: bij afleveringen de map boven 'Season xx'."""
    parent = PurePosixPath(str(path)).parent
    if _SEASON_DIR.match(parent.name.strip()):
        parent = parent.parent
    return f"serie:{parent}"


def is_anime(path: str | PurePosixPath) -> bool:
    return any("anime" in part.lower() for part in PurePosixPath(str(path)).parts[:-1])


def band_key(info: dict[str, Any]) -> str | None:
    codec = str(info.get("codec") or "").lower()
    height = int(info.get("height") or 0)
    kbps = int(info.get("kbps") or 0)
    if not codec or not height or not kbps:
        return None
    klasse = "2160" if height > 1200 else "1080" if height > 800 else "720"
    hdr = "hdr" if info.get("is_hdr10") or info.get("dv_profile") else "sdr"
    band = kbps // BITRATE_BAND_KBPS
    return f"band:{codec}:{klasse}:{hdr}:{band}"


def _now() -> datetime:
    return datetime.now(UTC)


def _fresh(entry: dict[str, Any]) -> bool:
    try:
        last = datetime.fromisoformat(str(entry.get("laatste")))
    except (TypeError, ValueError):
        return False
    return _now() - last <= timedelta(days=LEARN_DAYS)


def refresh(database: Any) -> None:
    try:
        rows = database.fetch_all("SELECT sleutel,soort,aantal,laatste,reden FROM route_leer")
    except Exception:  # noqa: BLE001 - tabel nog niet aangemaakt
        return
    _CACHE.clear()
    for row in rows:
        _CACHE[str(row["sleutel"])] = {"soort": row["soort"], "aantal": int(row["aantal"]),
                                       "laatste": row["laatste"], "reden": row["reden"]}
    _LOADED_AT["t"] = time.monotonic()


def refresh_if_stale(database: Any) -> None:
    if time.monotonic() - _LOADED_AT["t"] >= REFRESH_SECONDS:
        refresh(database)


def _bump(database: Any, key: str, soort: str, reden: str) -> int:
    now = _now().isoformat()
    row = database.fetch_one("SELECT aantal,laatste FROM route_leer WHERE sleutel=?", (key,))
    count = 1
    if row is not None and _fresh({"laatste": row["laatste"]}):
        count = int(row["aantal"]) + 1
    database.execute(
        "INSERT INTO route_leer(sleutel,soort,aantal,eerste,laatste,reden) VALUES (?,?,?,?,?,?) "
        "ON CONFLICT(sleutel) DO UPDATE SET aantal=excluded.aantal,laatste=excluded.laatste,"
        "reden=excluded.reden",
        (key, soort, count, now, now, reden))
    refresh(database)
    return count


def record_gpu_rejection(database: Any, path: str, reason: str) -> int:
    """Telt een GPU-kwaliteitsafkeuring voor de serie; geeft het nieuwe aantal terug."""
    return _bump(database, series_key(path), "gpu_vmaf", reason[:300])


def record_no_gain(database: Any, info: dict[str, Any], reason: str) -> int:
    key = band_key(info)
    return _bump(database, key, "geen_winst", reason[:300]) if key else 0


def gpu_block_reason(path: str) -> str | None:
    """Reden om deze bron niet via de GPU te doen, of None."""
    from app import afstelling

    mode = str(afstelling.get("anime_route"))
    anime = is_anime(path)
    if anime and mode == "cpu":
        return "Anime gaat via de CPU (instelling 'Route voor anime')"
    if anime and mode == "gpu":
        return None
    entry = _CACHE.get(series_key(path))
    if entry and entry["soort"] == "gpu_vmaf" and entry["aantal"] >= GPU_REJECT_LIMIT \
            and _fresh(entry):
        label = "Anime-reeks" if anime else "Deze reeks"
        return (f"{label} gaat via de CPU: GPU haalde VMAF niet ({entry['aantal']}×); "
                f"opnieuw geprobeerd na {LEARN_DAYS} dagen")
    return None


def no_gain_reason(info: dict[str, Any]) -> str | None:
    key = band_key(info)
    entry = _CACHE.get(key) if key else None
    if entry and entry["aantal"] >= NO_GAIN_LIMIT and _fresh(entry):
        return ("geen winst verwacht: vergelijkbare bronnen (zelfde codec, resolutie en "
                f"bitrate) werden {entry['aantal']}× groter als AV1 — origineel blijft")
    return None


def summary() -> dict[str, Any]:
    items = [{"sleutel": k, **v} for k, v in _CACHE.items() if _fresh(v)]
    return {"series_via_cpu": [i for i in items if i["soort"] == "gpu_vmaf"
                               and i["aantal"] >= GPU_REJECT_LIMIT],
            "geen_winst_banden": [i for i in items if i["soort"] == "geen_winst"
                                  and i["aantal"] >= NO_GAIN_LIMIT]}
