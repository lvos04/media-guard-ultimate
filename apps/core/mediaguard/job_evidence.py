"""Gedeelde leesregels voor de toegestane resultaattellers van een bibliotheektaak."""

from __future__ import annotations

from typing import Any

from mediaguard.db import loads

COUNT_FIELDS = ("episodes", "movies", "failed_items")


def object_value(raw: str | None) -> dict[str, Any]:
    try:
        value = loads(raw)
    except (ValueError, TypeError, RecursionError):
        return {}
    return value if isinstance(value, dict) else {}


def counts(value: dict[str, Any]) -> dict[str, int | None]:
    result = {}
    for key in COUNT_FIELDS:
        count = value.get(key)
        result[key] = count if type(count) is int and 0 <= count <= 2**63 - 1 else None
    return result
