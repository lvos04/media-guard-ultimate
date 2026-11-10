"""Noodstop: één regel voor alle automatische acties.

Tijdens een noodstop verwijdert, blokkeert of wijzigt MediaGuard niets uit zichzelf: geen
downloads weghalen of op de blocklist zetten, geen imports bevestigen of upgrades laten
importeren, niets opruimen of terugzetten, geen titels verplaatsen en geen profielen
aanpassen. Kijken, tellen en vastleggen gaat gewoon door; na het opheffen pakt de volgende
ronde alles vanzelf weer op.

Gebruik op elk instappunt:

- achtergrondronde: ``if emergency.skip(ctx, "downloadbewaking"): return ...``
- tussen stappen in een lus: ``if emergency.active(ctx): break``
- API-actie: ``emergency.require_clear(ctx, "importeren")`` (HTTP 409)
- job die later mag doorgaan: ``emergency.wait_if_stopped(ctx, "anime-routering")``
"""

from __future__ import annotations

import logging
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from mediaguard.context import AppContext

LOGGER = logging.getLogger(__name__)
REASON_CODE = "EMERGENCY_STOP"
RETRY_SECONDS = 300


def active(ctx: AppContext) -> bool:
    """Staat de noodstop aan? Alleen een databaseregel lezen: overal snel genoeg."""
    return ctx.emergency_stopped()


def message_nl(what: str) -> str:
    return f"Noodstop actief: {what} gepauzeerd"


def skip(ctx: AppContext, what: str) -> bool:
    """True = noodstop actief; deze automatische ronde of stap overslaan."""
    if not ctx.emergency_stopped():
        return False
    LOGGER.info("noodstop actief: %s overgeslagen", what)
    return True


def skipped(what: str, **extra: Any) -> dict[str, Any]:
    """Standaardresultaat van een overgeslagen ronde."""
    return {"skipped": "noodstop", "reason_nl": message_nl(what), **extra}


def require_clear(ctx: AppContext, what: str) -> None:
    """Voor een directe actie via de API: tijdens een noodstop HTTP 409."""
    if ctx.emergency_stopped():
        from fastapi import HTTPException

        raise HTTPException(409, message_nl(what))


def wait_if_stopped(ctx: AppContext, what: str) -> None:
    """Voor een job: tijdens een noodstop blijft hij staan (telt niet als poging) en gaat
    hij daarna vanzelf verder."""
    if ctx.emergency_stopped():
        from mediaguard import jobs

        raise jobs.Waiting(message_nl(what), RETRY_SECONDS)


__all__ = ["REASON_CODE", "active", "message_nl", "require_clear", "skip", "skipped",
           "wait_if_stopped"]
