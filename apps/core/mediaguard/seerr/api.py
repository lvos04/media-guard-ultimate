"""Seerr-API: instellingen, verbindingstest, routetest, mappingrapport, cutover/rollback,
aanvragen. Zie docs/SEERR_COMPATIBILITY.md."""

from __future__ import annotations

import asyncio
import contextlib
import logging
from collections.abc import Awaitable
from typing import Any, Literal

from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel, Field

from mediaguard import jobs, taskwatch
from mediaguard.audit import audit
from mediaguard.auth import Principal, require_admin, require_operator, require_viewer
from mediaguard.context import AppContext, get_app_context
from mediaguard.db import loads
from mediaguard.instances.deps import require_instance
from mediaguard.instances.registry import list_instances, validate_url
from mediaguard.seerr import service
from mediaguard.seerr.client import SECRET_NAME, SeerrClient, SeerrError, seerr_client
from mediaguard.settings_registry import REGISTRY, SettingDef

LOGGER = logging.getLogger(__name__)
router = APIRouter(prefix="/api/v1/seerr", tags=["seerr"])


def register_settings() -> None:
    REGISTRY.register(
        SettingDef("seerr.url", "string", "", "Seerr", "Seerr-URL", "Seerr URL",
                   help_nl="Basis-URL van Seerr, bv. http://seerr:5055. De API-sleutel staat in "
                           "de secrets (Seerr → Instellingen)."),
        SettingDef("seerr.request_sync_interval_minutes", "int", 30, "Seerr",
                   "Interval aanvragen-synchronisatie", "Request sync interval", minimum=5,
                   maximum=1440, unit="min"),
    )


async def seerr_call[T](awaitable: Awaitable[T]) -> T:
    try:
        return await awaitable
    except SeerrError as exc:
        raise HTTPException(exc.http_status, exc.to_dict()) from exc
    except ValueError as exc:
        raise HTTPException(422, str(exc)) from exc


@jobs.job_handler("seerr.sync_requests")
async def _job_sync(jc: jobs.JobContext) -> dict[str, Any]:
    return await service.sync_requests(jc.app)


def _interval_seconds(ctx: AppContext) -> float:
    return 60 * max(1, int(ctx.config.value("seerr.request_sync_interval_minutes")))


async def _loop(ctx: AppContext) -> None:
    await asyncio.sleep(20)
    while True:
        try:
            if ctx.config.value("seerr.url") and ctx.secrets.has(SECRET_NAME):
                await service.sync_requests(ctx)
            taskwatch.ok(ctx, "seerr_sync")
        except Exception as exc:  # noqa: BLE001 - de sync mag nooit voorgoed stoppen
            LOGGER.exception("seerr-sync")
            taskwatch.fail(ctx, "seerr_sync", exc)
        try:
            wait = _interval_seconds(ctx)
        except Exception:  # noqa: BLE001 - instelling onleesbaar: standaard 30 min
            wait = 1800.0
        await asyncio.sleep(wait)


async def startup(ctx: AppContext) -> None:
    if ctx.settings.start_background and ctx.settings.role in ("core", "all"):
        ctx.services["seerr.task"] = taskwatch.spawn(
            ctx, "seerr_sync", _loop, label_nl="Seerr-aanvragen ophalen",
            every=_interval_seconds)


async def shutdown(ctx: AppContext) -> None:
    task = ctx.services.pop("seerr.task", None)
    if task is not None:
        task.cancel()
        with contextlib.suppress(asyncio.CancelledError, Exception):
            await task


@router.get("/settings")
def get_settings(_: Principal = Depends(require_viewer),
                 ctx: AppContext = Depends(get_app_context)) -> dict[str, Any]:
    return {"url": ctx.config.value("seerr.url"), "api_key_set": ctx.secrets.has(SECRET_NAME),
            "request_sync_interval_minutes":
                ctx.config.value("seerr.request_sync_interval_minutes")}


class SeerrSettingsBody(BaseModel):
    url: str = Field(max_length=500)
    api_key: str | None = Field(default=None, min_length=8, max_length=300)
    verify: bool = True


@router.put("/settings")
async def put_settings(body: SeerrSettingsBody, principal: Principal = Depends(require_admin),
                       ctx: AppContext = Depends(get_app_context)) -> dict[str, Any]:
    """Wordt pas opgeslagen nadat een echte verbindingstest slaagt (Q2.2)."""
    try:
        validate_url(body.url)
    except ValueError as exc:
        raise HTTPException(422, str(exc)) from exc
    key = body.api_key or ctx.secrets.get(SECRET_NAME)
    if not key:
        raise HTTPException(422, "API-sleutel vereist")
    status = None
    if body.verify:
        client = SeerrClient(body.url, key, transport=ctx.services.get("seerr_transport"),
                             retries=0)
        status = await seerr_call(client.status())
    old = ctx.config.value("seerr.url")
    ctx.config.set("global", "", "seerr.url", body.url.rstrip("/"), principal.actor)
    if body.api_key:
        ctx.secrets.set(SECRET_NAME, body.api_key, principal.actor)
    audit(ctx.db, actor=principal.actor, action="seerr.settings", target_type="seerr",
          old_value={"url": old}, new_value={"url": body.url, "api_key_changed":
                                             bool(body.api_key)})
    return {"saved": True, "seerr_version": (status or {}).get("version"),
            **get_settings(principal, ctx)}


@router.post("/test")
async def test(_: Principal = Depends(require_operator),
               ctx: AppContext = Depends(get_app_context)) -> dict[str, Any]:
    try:
        client = seerr_client(ctx)
    except SeerrError as exc:
        raise HTTPException(exc.http_status, exc.to_dict()) from exc
    status = await seerr_call(client.status())
    sonarr = await seerr_call(client.servers("sonarr"))
    radarr = await seerr_call(client.servers("radarr"))
    return {"ok": True, "version": status.get("version"), "commit": status.get("commitTag"),
            "sonarr_servers": [service._mask_server(s) for s in sonarr],
            "radarr_servers": [service._mask_server(s) for s in radarr]}


@router.post("/route-test")
async def route_test(_: Principal = Depends(require_operator),
                     ctx: AppContext = Depends(get_app_context)) -> dict[str, Any]:
    return await seerr_call(service.route_report(ctx, run_tests=True))


@router.get("/mapping")
async def mapping(_: Principal = Depends(require_viewer),
                  ctx: AppContext = Depends(get_app_context)) -> dict[str, Any]:
    """Alleen-lezen mappingrapport (geen verbindingstests vanuit Seerr)."""
    return await seerr_call(service.route_report(ctx, run_tests=False))


@router.get("/routing")
def routing(_: Principal = Depends(require_viewer),
            ctx: AppContext = Depends(get_app_context)) -> list[dict[str, Any]]:
    return [{"instance_id": i.id, "routing": service.routing_for(ctx, i.id, i.app)}
            for i in list_instances(ctx)]


class RoutingBody(BaseModel):
    seerr_kind: Literal["sonarr", "radarr"] | None = None
    seerr_server_id: int | None = None
    role: Literal["default", "anime", "4k", "4k-anime", "manual"] = "default"
    expected_is_default: bool | None = None
    expected_is_4k: bool | None = None
    expected_profile_id: int | None = None
    expected_root_folder: str | None = None
    notes: str | None = Field(default=None, max_length=500)


@router.put("/routing/{instance_id}")
def put_routing(instance_id: str, body: RoutingBody,
                principal: Principal = Depends(require_operator),
                ctx: AppContext = Depends(get_app_context)) -> dict[str, Any]:
    inst = require_instance(ctx, instance_id)
    old = service.routing_for(ctx, instance_id, inst["app"])
    data = body.model_dump()
    data["seerr_kind"] = data["seerr_kind"] or inst["app"]
    if data["seerr_kind"] != inst["app"]:
        raise HTTPException(422, "Seerr-servertype moet overeenkomen met de app")
    row = service.set_routing(ctx, instance_id, data, principal.actor)
    audit(ctx.db, actor=principal.actor, action="seerr.routing", target_type="instance",
          target_id=instance_id, instance_id=instance_id, old_value=old, new_value=row)
    return row


class CutoverBody(BaseModel):
    instance_id: str
    confirm: bool = False
    server_id: int | None = None
    create_if_missing: bool = False
    active_profile_id: int | None = None
    active_directory: str | None = None
    active_anime_profile_id: int | None = None
    active_anime_directory: str | None = None


@router.post("/cutover")
async def cutover(body: CutoverBody, principal: Principal = Depends(require_admin),
                  ctx: AppContext = Depends(get_app_context)) -> dict[str, Any]:
    """Werk de Seerr-server voor een instantie bij via Seerr's API. Zonder ``confirm``
    alleen een preview (diff); met ``confirm`` worden de oude waarden bewaard."""
    require_instance(ctx, body.instance_id, mutable=True)
    overrides = {"activeProfileId": body.active_profile_id,
                 "activeDirectory": body.active_directory,
                 "activeAnimeProfileId": body.active_anime_profile_id,
                 "activeAnimeDirectory": body.active_anime_directory}
    return await seerr_call(service.seerr_cutover(
        ctx, body.instance_id, principal.actor, confirm=body.confirm, server_id=body.server_id,
        create_if_missing=body.create_if_missing, overrides=overrides))


class RollbackBody(BaseModel):
    instance_id: str
    confirm: bool = False


@router.post("/cutover/rollback")
async def rollback(body: RollbackBody, principal: Principal = Depends(require_admin),
                   ctx: AppContext = Depends(get_app_context)) -> dict[str, Any]:
    require_instance(ctx, body.instance_id)
    return await seerr_call(service.seerr_rollback(ctx, body.instance_id, principal.actor,
                                                   body.confirm))


@router.get("/requests")
def requests(instance_id: str | None = None, link_status: str | None = None,
             media_status: int | None = None, page: int = Query(1, ge=1),
             page_size: int = Query(50, ge=1, le=500), _: Principal = Depends(require_viewer),
             ctx: AppContext = Depends(get_app_context)) -> dict[str, Any]:
    sql = "FROM seerr_requests WHERE 1=1"
    params: list[Any] = []
    for col, val in (("instance_id", instance_id), ("link_status", link_status),
                     ("media_status", media_status)):
        if val is not None:
            sql += f" AND {col}=?"
            params.append(val)
    total = ctx.db.fetch_one("SELECT COUNT(*) AS n " + sql, params)
    rows = ctx.db.fetch_dicts("SELECT * " + sql + " ORDER BY created_at DESC LIMIT ? OFFSET ?",
                              [*params, page_size, (page - 1) * page_size])
    for r in rows:
        r["seasons"] = loads(r.pop("seasons_json"), [])
        r["request_status_nl"] = service.REQUEST_STATUS_NL.get(r["request_status"], "?")
        r["media_status_nl"] = service.MEDIA_STATUS_NL.get(r["media_status"], "?")
    return {"page": page, "page_size": page_size, "total": int(total["n"]) if total else 0,
            "items": rows, "summary": service.request_summary(ctx)}


@router.post("/requests/sync")
async def requests_sync(_: Principal = Depends(require_operator),
                        ctx: AppContext = Depends(get_app_context)) -> dict[str, Any]:
    return await seerr_call(service.sync_requests(ctx))
