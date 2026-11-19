"""Leeswaarnemingen mogen geen afgeleide instelling of herstelactie opslaan."""

from contextvars import ContextVar
from datetime import UTC, datetime

observation_read = ContextVar("observation_read", default=False)


def instant(value: object) -> datetime | None:
    if not isinstance(value, str):
        return None
    try:
        dt = datetime.fromisoformat(value)
        # Oudere lokale SQLite-waarden zonder offset stonden ook in UTC.
        return dt.astimezone(UTC) if dt.tzinfo else dt.replace(tzinfo=UTC)
    except (ValueError, OverflowError):
        return None


def utc_measurement(value: object) -> str | None:
    measured = instant(value)
    return measured.isoformat(timespec="microseconds") if measured else None
