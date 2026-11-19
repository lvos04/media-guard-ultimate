"""Interne eigenaar van de begrensde platformpauze op beide AV1-banen."""

from __future__ import annotations

import asyncio
from time import time
from typing import TYPE_CHECKING, Any

from mediaguard.encoding.client import EncoderError, get_encoder_client

if TYPE_CHECKING:
    from mediaguard.context import AppContext

STATE_KEY = "encoding_platform_hold"
MAX_TTL_SECONDS = 6 * 3600
MIN_CHANGE_SECONDS = 15 * 60


class PlatformHoldError(ValueError):
    """De gevraagde wijziging is geweigerd."""


def _lock(ctx: AppContext) -> asyncio.Lock:
    return ctx.services.setdefault("encoding_platform_hold_lock", asyncio.Lock())


def _result(state: dict[str, Any], effective: Any = None) -> dict[str, Any]:
    expiry = state.get("expires_at")
    if expiry is not None and expiry <= time():
        expiry = None
    reason = f"Platform: {state['reason']}" if expiry is not None else None
    if not isinstance(effective, dict) or "expires_at" not in effective or (
        effective["expires_at"], effective.get("reason")
    ) != (expiry, reason):
        return {"status": "unknown", "reason": None, "expires_at": None}
    return {"status": "paused" if expiry is not None else "inactive",
            "reason": reason, "expires_at": expiry}


async def apply_hold(ctx: AppContext, active: bool, *, reason: str = "",
                     ttl_seconds: int = MAX_TTL_SECONDS) -> dict[str, Any]:
    """Bewaar een wijziging vóór de aanroep, ook als de bevestiging onderweg verloren gaat."""
    reason = reason.strip()[:190].rstrip()
    if active and (not reason or type(ttl_seconds) is not int or ttl_seconds <= 0):
        raise PlatformHoldError("Een platformpauze vraagt een reden en een positieve TTL.")
    async with _lock(ctx):
        now = time()
        last = ctx.state_get(STATE_KEY, {}).get("changed_at")
        if last is not None and now - last < MIN_CHANGE_SECONDS:
            raise PlatformHoldError("Wacht minstens 15 minuten tussen platformwijzigingen.")
        state = {
            "changed_at": now, "pending": True, "reason": reason if active else "",
            "expires_at": int(now + min(ttl_seconds, MAX_TTL_SECONDS)) if active else None,
        }
        ctx.state_set(STATE_KEY, state)
        return await _push(ctx, state)


async def _push(ctx: AppContext, state: dict[str, Any]) -> dict[str, Any]:
    try:
        effective = await get_encoder_client(ctx).set_platform_hold(
            state["reason"], state["expires_at"])
    except EncoderError:
        return _result(state)
    result = _result(state, effective)
    if result["status"] != "unknown":
        ctx.state_set(STATE_KEY, {**state, "pending": False})
    return result


async def sync(ctx: AppContext) -> None:
    """Laat de termijn aflopen en herhaal onbevestigde aanroepen met dezelfde eindtijd."""
    async with _lock(ctx):
        state = ctx.state_get(STATE_KEY, {})
        expiry = state.get("expires_at")
        if expiry is not None and expiry <= time():
            state = {**state, "reason": "", "expires_at": None, "pending": True}
            ctx.state_set(STATE_KEY, state)
        if state.get("pending"):
            await _push(ctx, state)


async def status(ctx: AppContext) -> dict[str, Any]:
    """Rapporteer alleen de vers bevestigde stand, ook bij een vastgelegde wijziging."""
    async with _lock(ctx):
        state = ctx.state_get(STATE_KEY, {})
        try:
            effective = await get_encoder_client(ctx).get_platform_hold()
        except EncoderError:
            return _result(state)
        return _result(state, effective)


def suppress_gpu_idle(ctx: AppContext) -> bool:
    """Laat een door de engine bevestigde pauze niet meetellen als GPU-stilstand."""
    state = ctx.state_get(STATE_KEY, {})
    expiry = state.get("expires_at")
    return not state.get("pending") and expiry is not None and expiry > time()
