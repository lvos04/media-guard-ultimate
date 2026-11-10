"""API + jobs voor automatische anime-routering."""

from __future__ import annotations

import asyncio
import contextlib
import logging
from typing import Any, Literal

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel

from mediaguard import emergency, jobs, taskwatch
from mediaguard.arr.client import ArrUnavailable
from mediaguard.auth import Principal, require_operator, require_viewer
from mediaguard.context import AppContext, get_app_context
from mediaguard.db import utcnow
from mediaguard.routing import service
from mediaguard.settings_registry import REGISTRY

LOGGER = logging.getLogger(__name__)
router = APIRouter(prefix="/api/v1/routing/anime", tags=["routing"])


def register_settings() -> None:
    REGISTRY.register(*service.setting_defs())


@jobs.job_handler("routing.anime_check")
async def _job_check(jc: jobs.JobContext) -> dict[str, Any]:
    emergency.wait_if_stopped(jc.app, "anime-routering")  # blijft staan tot na de noodstop
    return await service.evaluate(jc.app, jc.payload["source"], jc.payload["kind"],
                                  int(jc.payload["provider_id"]))


def enqueue_check(ctx: AppContext, source: str, kind: str, provider_id: int) -> int | None:
    """Aangeroepen vanuit de webhook (SeriesAdd/MovieAdded)."""
    if source not in service.DEFAULT_TARGETS:
        return None
    job_id = jobs.enqueue(ctx.db, "routing.anime_check",
                          {"source": source, "kind": kind, "provider_id": provider_id},
                          instance_id=source, target_type=kind, target_id=provider_id,
                          idempotency_key=f"routing.anime_check:{source}:{kind}:{provider_id}",
                          max_attempts=3, priority=40)
    runner = ctx.services.get("jobs")
    if runner is not None:
        runner.wake()
    return job_id


async def _sweep_loop(ctx: AppContext) -> None:
    # Sonarr/Radarr starten na core (alles-in-één): niet meteen op een dichte poort tikken
    await asyncio.sleep(90)
    while True:
        try:
            await service.sweep(ctx)
            last = ctx.state_get("anime_backlog_sweep", {}) or {}
            if str(last.get("day")) != utcnow()[:10] and not emergency.active(ctx):
                await service.backlog_sweep(ctx)
                ctx.state_set("anime_backlog_sweep", {"day": utcnow()[:10]})
            taskwatch.ok(ctx, "anime_routing")
        except ArrUnavailable as exc:
            LOGGER.warning("anime-routering: instantie niet bereikbaar (%s)", exc.message)
            taskwatch.ok(ctx, "anime_routing")  # de taak leeft; de instantie meldt zichzelf
        except Exception as exc:  # noqa: BLE001 - vangnet mag nooit stoppen
            LOGGER.exception("anime-routering sweep mislukt")
            taskwatch.fail(ctx, "anime_routing", exc)
        try:
            wait = _sweep_seconds(ctx)
        except Exception:  # noqa: BLE001 - instelling onleesbaar: elk uur
            wait = 3600.0
        await asyncio.sleep(wait)


def _sweep_seconds(ctx: AppContext) -> float:
    return 60 * max(1, int(ctx.config.value("routing.anime.sweep_minutes")))


async def startup(ctx: AppContext) -> None:
    if ctx.settings.start_background and ctx.settings.role == "core":
        ctx.services["anime_routing_task"] = taskwatch.spawn(
            ctx, "anime_routing", _sweep_loop, label_nl="Anime-routering", every=_sweep_seconds)


async def shutdown(ctx: AppContext) -> None:
    task = ctx.services.pop("anime_routing_task", None)
    if task is not None:
        task.cancel()
        with contextlib.suppress(asyncio.CancelledError):
            await task


@router.get("/actions")
def actions(status: str | None = None, _: Principal = Depends(require_viewer),
            ctx: AppContext = Depends(get_app_context)) -> list[dict[str, Any]]:
    return service.list_actions(ctx, status)


class EvaluateBody(BaseModel):
    source: Literal["sonarr-series", "radarr-films"]
    kind: Literal["series", "movie"]
    provider_id: int
    force: bool = False


@router.post("/evaluate")
async def evaluate(body: EvaluateBody, principal: Principal = Depends(require_operator),
                   ctx: AppContext = Depends(get_app_context)) -> dict[str, Any]:
    """'Verplaats nu' (force) of opnieuw beoordelen."""
    if body.force:
        emergency.require_clear(ctx, "verplaatsen")
    return await service.evaluate(ctx, body.source, body.kind, body.provider_id,
                                  actor=principal.actor, force=body.force)


@router.post("/actions/{action_id}/approve")
async def approve(action_id: int, principal: Principal = Depends(require_operator),
                  ctx: AppContext = Depends(get_app_context)) -> dict[str, Any]:
    row = ctx.db.fetch_one("SELECT * FROM anime_routing_actions WHERE id=?", (action_id,))
    if row is None:
        raise HTTPException(404, "Voorstel niet gevonden")
    emergency.require_clear(ctx, "verplaatsen")
    return await service.evaluate(ctx, row["source_instance"], row["kind"],
                                  int(row["provider_id"]), actor=principal.actor, force=True)


@router.post("/actions/{action_id}/keep")
def keep(action_id: int, principal: Principal = Depends(require_operator),
         ctx: AppContext = Depends(get_app_context)) -> dict[str, Any]:
    """'Hier laten': nooit meer automatisch verplaatsen."""
    row = ctx.db.fetch_one("SELECT * FROM anime_routing_actions WHERE id=?", (action_id,))
    if row is None:
        raise HTTPException(404, "Voorstel niet gevonden")
    service.exclude(ctx, row["source_instance"], row["kind"], row["external_id"],
                    principal.actor)
    ctx.db.execute("UPDATE anime_routing_actions SET status='dismissed' WHERE id=?",
                   (action_id,))
    return {"status": "dismissed"}


@router.post("/actions/{action_id}/undo")
async def undo(action_id: int, principal: Principal = Depends(require_operator),
               ctx: AppContext = Depends(get_app_context)) -> dict[str, Any]:
    emergency.require_clear(ctx, "terugzetten")
    try:
        return await service.undo(ctx, action_id, principal.actor)
    except ValueError as exc:
        raise HTTPException(409, str(exc)) from exc
