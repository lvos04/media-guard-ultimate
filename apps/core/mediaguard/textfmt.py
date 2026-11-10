"""Centrale tekstopmaak voor gebruikersteksten: Nederlandse tijden/datums en het opruimen
van ruwe Python-structuren (rapport 3, R15) uit oudere opgeslagen meldingen en controles."""

from __future__ import annotations

import ast
import re
from datetime import UTC, datetime
from typing import Any
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

MONTHS_NL = ("jan", "feb", "mrt", "apr", "mei", "jun", "jul", "aug", "sep", "okt", "nov",
             "dec")
_DICT_RE = re.compile(r"\{(?:\s*'[^'{}]{1,40}'\s*:\s*\d+\s*,?)+\s*\}")
_ISO_RE = re.compile(
    r"\b(\d{4}-\d{2}-\d{2})T(\d{2}:\d{2})(?::\d{2}(?:\.\d+)?)?(Z|[+-]\d{2}:?\d{2})?")


def _tz() -> Any:
    try:
        return ZoneInfo("Europe/Amsterdam")
    except ZoneInfoNotFoundError:  # pragma: no cover - tzdata ontbreekt
        return UTC


def _parse(value: str | datetime | None) -> datetime | None:
    if value is None:
        return None
    if isinstance(value, datetime):
        dt = value
    else:
        try:
            dt = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
        except ValueError:
            return None
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=UTC)
    return dt.astimezone(_tz())


def time_nl(value: str | datetime | None) -> str:
    """'2026-11-05T06:30:00Z' → '07:30' (vandaag) of '4 nov 13:01' (andere dag)."""
    dt = _parse(value)
    if dt is None:
        return str(value or "")
    today = datetime.now(_tz()).date()
    if dt.date() == today:
        return dt.strftime("%H:%M")
    return datetime_nl(dt)


def datetime_nl(value: str | datetime | None) -> str:
    """'2026-11-04T12:01:25Z' → '4 nov 13:01'."""
    dt = _parse(value)
    if dt is None:
        return str(value or "")
    return f"{dt.day} {MONTHS_NL[dt.month - 1]} {dt:%H:%M}"


def _dict_nl(match: re.Match[str]) -> str:
    from mediaguard.recovery.release_check import languages_nl

    try:
        data = ast.literal_eval(match.group(0))
    except (ValueError, SyntaxError):
        return match.group(0)
    if not isinstance(data, dict):
        return match.group(0)
    return languages_nl({str(k): int(v) for k, v in data.items()})


def plain_nl(text: Any) -> str:
    """Ruwe dicts ({'ara': 1, 'ger': 2}) → 'Arabisch, Duits (2)'; ISO-tijden → '4 nov 13:01'."""
    if text is None:
        return ""
    out = _DICT_RE.sub(_dict_nl, str(text))
    return _ISO_RE.sub(lambda m: datetime_nl(m.group(0)), out)


def plain_fields(row: dict[str, Any], *keys: str) -> dict[str, Any]:
    """Maak de genoemde tekstvelden van een rij leesbaar (in-place + teruggeven)."""
    for key in keys:
        if isinstance(row.get(key), str):
            row[key] = plain_nl(row[key])
    return row
