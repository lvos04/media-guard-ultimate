"""De eigenaar bewaakt termijn, wijzigingsfrequentie en bevestiging van de engine."""

from __future__ import annotations

import asyncio
import json
from typing import Any

import httpx
import pytest

from mediaguard.context import AppContext
from mediaguard.encoding import platform_hold as P


@pytest.fixture
def engine(ctx: AppContext, monkeypatch: pytest.MonkeyPatch) -> dict[str, Any]:
    state: dict[str, Any] = {"now": 2_000_000_000, "offline": False, "writes": []}
    monkeypatch.setattr(P, "time", lambda: state["now"])

    async def respond(request: httpx.Request) -> httpx.Response:
        assert request.url.path.endswith("/settings/playback-hold")
        if state["offline"]:
            raise httpx.ConnectError("engine buiten bereik", request=request)
        if request.method == "PUT":
            body = json.loads(request.content)
            assert body["source"] == "platform"
            assert body["lanes"] == (["cpu", "gpu"] if body["active"] else [])
            state["writes"].append(body)
            state["expires_at"] = body["expires_at"]
            state["reason"] = f"Platform: {body['reason']}" if body["active"] else None
        expiry = state.get("expires_at")
        if expiry and expiry > state["now"]:
            return httpx.Response(200, json={"expires_at": expiry, "reason": state["reason"]})
        return httpx.Response(200, json={"expires_at": None, "reason": None})

    ctx.services["encoder_transport"] = httpx.MockTransport(respond)
    return state


async def test_ttl_begrensd_en_core_hervat(ctx: AppContext, engine: dict[str, Any]) -> None:
    end = engine["now"] + 6 * 3600
    result = await P.apply_hold(ctx, True, reason="back-up", ttl_seconds=7 * 3600)
    assert result == {"status": "paused", "reason": "Platform: back-up", "expires_at": end}
    engine["now"] = end - 1
    await P.sync(ctx)
    assert len(engine["writes"]) == 1
    engine["now"] = end
    await P.sync(ctx)
    assert [body["active"] for body in engine["writes"]] == [True, False]
    assert (await P.status(ctx))["status"] == "inactive"


async def test_gelijktijdige_wijziging_en_herstart(
    ctx: AppContext, engine: dict[str, Any],
) -> None:
    changes = await asyncio.gather(
        P.apply_hold(ctx, True, reason="back-up"),
        P.apply_hold(ctx, True, reason="scrub"),
        return_exceptions=True,
    )
    assert sum(isinstance(change, P.PlatformHoldError) for change in changes) == 1
    assert len(engine["writes"]) == 1
    fresh_ctx = AppContext(ctx.settings, ctx.db, ctx.secrets, ctx.events, registry=ctx.registry)
    fresh_ctx.services["encoder_transport"] = ctx.services["encoder_transport"]
    engine["now"] += 899
    with pytest.raises(P.PlatformHoldError, match="15 minuten"):
        await P.apply_hold(fresh_ctx, False)
    assert len(engine["writes"]) == 1
    engine["now"] += 1
    assert (await P.apply_hold(fresh_ctx, False))["status"] == "inactive"


async def test_engine_storing_en_herstel(ctx: AppContext, engine: dict[str, Any]) -> None:
    await P.apply_hold(ctx, True, reason="back-up", ttl_seconds=1200)
    engine["offline"] = True
    assert (await P.status(ctx))["status"] == "unknown"
    engine["now"] += 900
    assert (await P.apply_hold(ctx, False))["status"] == "unknown"
    engine["offline"] = False
    assert (await P.status(ctx))["status"] == "unknown"
    await P.sync(ctx)
    assert (await P.status(ctx))["status"] == "inactive"
    assert [body["active"] for body in engine["writes"]] == [True, False]


@pytest.mark.parametrize("reason, ttl", [("", 100), ("back-up", 0), ("back-up", -1)])
async def test_ongeldige_pauze_geweigerd(
    ctx: AppContext, engine: dict[str, Any], reason: str, ttl: int,
) -> None:
    with pytest.raises(P.PlatformHoldError):
        await P.apply_hold(ctx, True, reason=reason, ttl_seconds=ttl)
    assert engine["writes"] == []
