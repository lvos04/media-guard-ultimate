"""Geschatte duur van een encode vóórdat hij start (rapport 5, M9/T2/T11).

Uit de eigen geschiedenis: hoeveel seconden film per seconde rekentijd haalde deze baan met
deze encoder op deze resolutie (laatste 30 dagen)? Zonder genoeg metingen een vaste
startwaarde per (baan, encoder, resolutie). Een 4K-HDR10+-aflevering via libaom (0,03×)
krijgt zo vooraf "± 23 uur" in plaats van pas tijdens het encoderen.
"""

from __future__ import annotations

import json
from typing import Any

# Realtimefactor (film-seconden per wandklok-seconde) als er nog niets gemeten is.
DEFAULT_SPEED: dict[tuple[str, str, str], float] = {
    ("cpu", "libsvtav1", "2160"): 0.25, ("cpu", "libsvtav1", "1080"): 1.2,
    ("cpu", "libsvtav1", "720"): 2.5,
    ("cpu", "libaom-av1", "2160"): 0.03, ("cpu", "libaom-av1", "1080"): 0.12,
    ("cpu", "libaom-av1", "720"): 0.25,
    ("gpu", "av1_qsv", "2160"): 1.0, ("gpu", "av1_qsv", "1080"): 3.0,
    ("gpu", "av1_qsv", "720"): 5.0,
    ("gpu", "av1_vaapi", "2160"): 1.0, ("gpu", "av1_vaapi", "1080"): 3.0,
    ("gpu", "av1_vaapi", "720"): 5.0,
}
LANE_FALLBACK = {"cpu": 0.5, "gpu": 2.0}
# Jobs die langer dan dit duren gaan alleen in het nachtvenster (tenzij de baan anders
# stil zou staan).
LONG_JOB_SECONDS = 6 * 3600
MIN_SAMPLES = 2


def resolution_class(height: int) -> str:
    return "2160" if height > 1200 else "1080" if height > 800 else "720"


def _probe(raw: object) -> dict[str, Any]:
    if isinstance(raw, dict):
        return raw
    try:
        value = json.loads(str(raw or "{}"))
    except (TypeError, ValueError):
        return {}
    return value if isinstance(value, dict) else {}


def measured_speeds(database: Any) -> dict[tuple[str, str, str], float]:
    """Gemeten realtimefactor per (baan, encoder, resolutie) over de laatste 30 dagen."""
    rows = database.fetch_all(
        """
        SELECT j.baan,b.encoder,f.probe,
               (julianday(j.klaar_op)-julianday(j.gestart_op))*86400 AS seconden
        FROM job j JOIN bestand f ON f.id=j.bestand_id JOIN besluit b ON b.id=j.besluit_id
        WHERE j.status IN ('klaar','gecommit','wacht_op_commit') AND j.gestart_op IS NOT NULL
          AND j.klaar_op IS NOT NULL AND j.klaar_op>=datetime('now','-30 days')
        """
    )
    totals: dict[tuple[str, str, str], list[float]] = {}
    for row in rows:
        seconds = float(row["seconden"] or 0)
        probe = _probe(row["probe"])
        media = float(probe.get("duration") or 0)
        if seconds < 30 or media <= 0:
            continue
        key = (str(row["baan"] or "cpu"), str(row["encoder"] or ""),
               resolution_class(int(probe.get("height") or 0)))
        bucket = totals.setdefault(key, [0.0, 0.0, 0.0])
        bucket[0] += media
        bucket[1] += seconds
        bucket[2] += 1
    return {key: media / wall for key, (media, wall, n) in totals.items()
            if n >= MIN_SAMPLES and wall > 0}


def speed_for(lane: str, encoder: str | None, height: int,
              measured: dict[tuple[str, str, str], float] | None = None) -> float:
    key = (lane, str(encoder or ""), resolution_class(height))
    if measured and key in measured:
        return measured[key]
    if key in DEFAULT_SPEED:
        return DEFAULT_SPEED[key]
    return LANE_FALLBACK.get(lane, 0.5)


def estimate_seconds(lane: str, encoder: str | None, probe: object,
                     measured: dict[tuple[str, str, str], float] | None = None) -> int | None:
    info = _probe(probe)
    media = float(info.get("duration") or 0)
    if media <= 0:
        return None
    speed = speed_for(lane, encoder, int(info.get("height") or 0), measured)
    return int(media / max(speed, 0.001))


def duration_text(seconds: int | None) -> str:
    if seconds is None:
        return "onbekend"
    hours, rest = divmod(int(seconds), 3600)
    if hours >= 1:
        return f"± {hours} u {rest // 60:02d} m"
    return f"± {max(1, rest // 60)} min"


_SPEED_CACHE: dict[str, Any] = {"t": 0.0, "value": {}}
SPEED_CACHE_SECONDS = 600.0


def cached_speeds(database: Any) -> dict[tuple[str, str, str], float]:
    """Gemeten snelheden, hooguit eens per 10 minuten opnieuw berekend."""
    import time

    now = time.monotonic()
    if not _SPEED_CACHE["t"] or now - float(_SPEED_CACHE["t"]) >= SPEED_CACHE_SECONDS:
        try:
            _SPEED_CACHE["value"] = measured_speeds(database)
        except Exception:  # noqa: BLE001 - schatting is nooit een reden om niet te starten
            _SPEED_CACHE["value"] = {}
        _SPEED_CACHE["t"] = now
    return dict(_SPEED_CACHE["value"])
