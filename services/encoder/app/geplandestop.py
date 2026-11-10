"""Geplande stop (update of herstart van de container) herkennen (verbeterpunt 30).

De AIO-supervisor schrijft bij een nette stop van de container eerst het bestand
``<CONFIG_ROOT>/geplande-stop`` (met tijdstip) en stuurt pas daarna SIGTERM. Een encode
die daardoor wordt afgebroken, is geen fout en telt niet mee voor de herstartlus-grens
(drie onderbrekingen = 'HerstartLus'); die grens is er voor een engine die steeds crasht of
door de levenscontrole wordt herstart. Ook als de stop te lang duurde en de engine hard werd
gestopt, ziet de volgende start aan dit bestand dat het gepland was. De engine ruimt het
bestand bij de volgende start op.
"""

from __future__ import annotations

from contextlib import suppress
from datetime import UTC, datetime, timedelta
from pathlib import Path

from app.config import Settings

MARKER_NAME = "geplande-stop"
PLANNED_STOP_ERROR = "onderbroken door een geplande stop of update; begint opnieuw"
PLANNED_STOP_TYPE = "GeplandeStop"


def marker_path(settings: Settings) -> Path:
    return settings.config_root / MARKER_NAME


def planned_stop_at(settings: Settings) -> datetime | None:
    """Tijdstip van de aangekondigde stop, of None als er geen is."""

    path = marker_path(settings)
    try:
        text = path.read_text(encoding="utf-8").strip()
    except OSError:
        return None
    with suppress(ValueError):
        at = datetime.fromisoformat(text)
        return at if at.tzinfo else at.replace(tzinfo=UTC)
    with suppress(OSError):
        return datetime.fromtimestamp(path.stat().st_mtime, UTC)
    return None


# Een lopende encode die door de stop wordt afgebroken, ziet het bestand binnen seconden.
# Een ouder bestand (niet opgeruimd na een eerdere stop) telt daar niet: anders zou een
# echte herstartlus (levenscontrole) ongemerkt doorgaan.
FRESH_FOR = timedelta(minutes=15)


def planned_stop(settings: Settings, now: datetime | None = None) -> bool:
    """Is er nú een geplande stop aangekondigd (hooguit ``FRESH_FOR`` geleden)?"""

    at = planned_stop_at(settings)
    if at is None:
        return False
    return (now or datetime.now(UTC)) - at <= FRESH_FOR


def announce(settings: Settings) -> None:
    """Voor tests en een stop vanuit de engine zelf: zelfde bestand als de supervisor."""

    path = marker_path(settings)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(datetime.now(UTC).isoformat(timespec="seconds"), encoding="utf-8")


def clear(settings: Settings) -> None:
    with suppress(OSError):
        marker_path(settings).unlink(missing_ok=True)
