"""API en achtergrondlus van de leveringsgarantie (/api/v1/delivery)."""

from __future__ import annotations

import asyncio
import contextlib
import logging
from typing import Any

from fastapi import APIRouter, Depends, HTTPException

from mediaguard import taskwatch
from mediaguard.auth import Principal, require_operator, require_viewer
from mediaguard.context import AppContext, get_app_context
from mediaguard.db import utcnow
from mediaguard.delivery import engine, store
from mediaguard.settings_registry import REGISTRY, SettingDef

LOGGER = logging.getLogger(__name__)
router = APIRouter(prefix="/api/v1/delivery", tags=["delivery"])
CATEGORY = "Aanvragen"
INTERVAL_SECONDS = 600


def register_settings() -> None:
    REGISTRY.register(
        SettingDef("delivery.enabled", "bool", True, CATEGORY,
                   "Aanvragen automatisch leveren", "Deliver requests automatically",
                   help_nl="Volgt elke aanvraag (Seerr of nieuw in Sonarr/Radarr) tot hij "
                           "volgens de eisen in de bibliotheek staat en lost vastgelopen "
                           "stappen zelf op. Beslissingen neemt de AI binnen de eisen."),
    )


@router.get("/requests")
def requests(state: str | None = None, _: Principal = Depends(require_viewer),
             ctx: AppContext = Depends(get_app_context)) -> dict[str, Any]:
    return {"items": store.list_items(ctx, state=state), "counts": store.counts(ctx),
            "last_run": ctx.state_get("delivery", {}) or {}}


@router.get("/requests/{item_id}")
def request_detail(item_id: int, _: Principal = Depends(require_viewer),
                   ctx: AppContext = Depends(get_app_context)) -> dict[str, Any]:
    item = store.get(ctx, item_id)
    if item is None:
        raise HTTPException(404, "Aanvraag niet gevonden")
    return {**item, "events": store.events(ctx, item_id)}


@router.post("/requests/{item_id}/stop")
def request_stop(item_id: int, principal: Principal = Depends(require_operator),
                 ctx: AppContext = Depends(get_app_context)) -> dict[str, Any]:
    """Deze aanvraag niet meer volgen (verbeterlijst 11). Er wordt niets verwijderd."""
    from mediaguard.audit import audit

    item = store.get(ctx, item_id)
    if item is None:
        raise HTTPException(404, "Aanvraag niet gevonden")
    store.stop(ctx, item_id, "user", principal.actor)
    audit(ctx.db, actor=principal.actor, action="delivery.stopped",
          target_type=item["media_type"], target_id=str(item_id),
          instance_id=item.get("instance_id"), reason=item.get("title") or "",
          undo_hint="Kies 'Nu opnieuw' bij Aanvragen")
    return store.get(ctx, item_id) or {}


@router.post("/requests/{item_id}/retry")
async def request_retry(item_id: int, principal: Principal = Depends(require_operator),
                        ctx: AppContext = Depends(get_app_context)) -> dict[str, Any]:
    """'Nu opnieuw': weer volgen en meteen de volgende stap zetten (ook na Stop)."""
    from mediaguard.audit import audit

    if ctx.emergency_stopped():
        raise HTTPException(409, "Noodstop actief: MediaGuard doet nu niets")
    if store.get(ctx, item_id) is None:
        raise HTTPException(404, "Aanvraag niet gevonden")
    item = await engine.retry_now(ctx, item_id, principal.actor)
    audit(ctx.db, actor=principal.actor, action="delivery.retry",
          target_type=(item or {}).get("media_type") or "request", target_id=str(item_id),
          instance_id=(item or {}).get("instance_id"), reason=(item or {}).get("title") or "")
    return item or {}


@router.post("/requests/{item_id}/search")
async def search_now(item_id: int, principal: Principal = Depends(require_operator),
                     ctx: AppContext = Depends(get_app_context)) -> dict[str, Any]:
    """'Nu zoeken' op de aanvragenpagina (punt 26): de wachttijd tot de volgende poging
    overslaan en deze titel meteen een ronde geven. Dezelfde regels als de motor: eisen,
    zoekquotum, zoekpauzes en 'Niet meer zoeken' blijven gelden."""
    from mediaguard.arr.client import ArrError

    if ctx.emergency_stopped():
        raise HTTPException(409, "De noodstop staat aan: er wordt nu niet gezocht")
    row = ctx.db.fetch_one("SELECT * FROM delivery_items WHERE id=?", (item_id,))
    if row is None:
        raise HTTPException(404, "Aanvraag niet gevonden")
    if row["state"] == "delivered":
        raise HTTPException(409, "Deze aanvraag is al geleverd")
    if row["stopped_at"]:
        # gestopt (knop Stop, verwijderd in Seerr of uit Sonarr/Radarr): eerst 'Nu opnieuw'
        raise HTTPException(409, "Deze aanvraag is gestopt; kies 'Nu opnieuw' om hem weer "
                                 "te volgen")
    item = dict(row)
    before = int(item.get("attempts") or 0)
    now = utcnow()
    store.update(ctx, item_id, next_action_at=now)
    item["next_action_at"] = now
    store.event(ctx, item_id, str(item["stage"]), "nu_zoeken", principal.actor,
                "Nu zoeken gevraagd")
    try:
        await engine.process(ctx, item)
    except ArrError as exc:
        raise HTTPException(502, f"Sonarr/Radarr niet bereikbaar: {exc}") from exc
    fresh = store.get(ctx, item_id) or {}
    return {"searched": int(fresh.get("attempts") or 0) > before,
            "stage": fresh.get("stage"), "detail_nl": fresh.get("detail_nl"),
            "next_step_nl": fresh.get("next_step_nl"), "item": fresh}


@router.post("/run")
async def run_now(_: Principal = Depends(require_operator),
                  ctx: AppContext = Depends(get_app_context)) -> dict[str, Any]:
    result = await engine.run_once(ctx, force=True)
    ctx.state_set("delivery", {**result, "at": utcnow()})
    return result


async def _loop(ctx: AppContext) -> None:
    await asyncio.sleep(150)  # Sonarr/Radarr starten na core (alles-in-één)
    while True:
        try:
            result = await engine.run_once(ctx)
            ctx.state_set("delivery", {**result, "at": utcnow()})
            taskwatch.ok(ctx, "delivery")
        except Exception as exc:  # noqa: BLE001 - lus nooit laten sterven
            LOGGER.exception("leveringsronde mislukt")
            taskwatch.fail(ctx, "delivery", exc)
        await asyncio.sleep(INTERVAL_SECONDS)


async def startup(ctx: AppContext) -> None:
    if ctx.settings.start_background and ctx.settings.role in ("core", "all"):
        ctx.services["delivery_task"] = taskwatch.spawn(
            ctx, "delivery", _loop, label_nl="Aanvragen leveren", every=INTERVAL_SECONDS)


async def shutdown(ctx: AppContext) -> None:
    task = ctx.services.pop("delivery_task", None)
    if task is not None:
        task.cancel()
        with contextlib.suppress(asyncio.CancelledError):
            await task
