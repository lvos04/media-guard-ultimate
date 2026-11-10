"""R5-routes: instantiebeheer, health, suspend/resume, webhook, ownership.
De beheerroutes voor series/films/indexers/... staan in ``instances.manage``."""

from __future__ import annotations

import asyncio
import contextlib
import logging
from typing import Any, Literal

from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel, Field

from mediaguard import jobs, taskwatch
from mediaguard.arr.client import ArrClient, ArrError
from mediaguard.audit import audit
from mediaguard.auth import Principal, require_admin, require_operator, require_viewer
from mediaguard.context import AppContext, get_app_context
from mediaguard.db import loads, utcnow
from mediaguard.instances import manage
from mediaguard.instances.deps import arr_call, require_instance
from mediaguard.instances.registry import (
    apikey_secret,
    create_instance,
    hookkey_secret,
    instance_row,
    seed_instances,
    update_instance,
    validate_new,
)
from mediaguard.instances.service import (
    SuspendError,
    check_instance,
    health_loop,
    ownership_conflicts,
    ownership_records,
    register_webhook,
    resume_instance,
    simulate_ownership,
    suspend_instance,
)
from mediaguard.settings_registry import REGISTRY, SettingDef

LOGGER = logging.getLogger(__name__)
router = APIRouter(prefix="/api/v1", tags=["instances"])


def register_settings() -> None:
    REGISTRY.register(
        SettingDef("instances.health_interval_seconds", "int", 60, "Instanties",
                   "Interval gezondheidscontrole", "Health check interval",
                   help_nl="Hoe vaak MediaGuard elke Sonarr/Radarr-instantie controleert "
                           "(bereikbaarheid, API-sleutel, health-meldingen).",
                   minimum=15, maximum=3600, unit="s"),
    )


# -- startup -------------------------------------------------------------------------------
async def startup(ctx: AppContext) -> None:
    seed_instances(ctx)
    if ctx.settings.start_background and ctx.settings.role in ("core", "all"):
        ctx.services["instances.health_task"] = taskwatch.spawn(
            ctx, "instance_health", health_loop, label_nl="Sonarr/Radarr controleren",
            every=lambda c: int(c.config.value("instances.health_interval_seconds")))


async def shutdown(ctx: AppContext) -> None:
    task = ctx.services.pop("instances.health_task", None)
    if task is not None:
        task.cancel()
        with contextlib.suppress(asyncio.CancelledError, Exception):
            await task
    from mediaguard.instances.registry import close_pool

    await close_pool(ctx)


# -- weergave ------------------------------------------------------------------------------
PUBLIC_FIELDS = (
    "id", "app", "media_type", "name", "url", "public_url", "status", "suspended",
    "shadow_mode", "archived", "hook_fail_policy", "version", "upstream_app_name",
    "api_key_valid", "last_check_at", "last_status_change_at", "last_error", "suspended_at",
    "suspended_by", "webhook_notification_id", "webhook_registered_at",
    "default_quality_profile_id", "default_language_profile_id", "default_root_folder",
    "default_monitor", "default_monitor_new_items", "default_series_type",
    "default_minimum_availability", "download_category", "sort_order", "created_at",
    "updated_at", "archived_at",
)


def instance_view(ctx: AppContext, row: dict[str, Any], detail: bool = False) -> dict:
    iid = row["id"]
    out: dict[str, Any] = {k: row.get(k) for k in PUBLIC_FIELDS}
    for flag in ("suspended", "shadow_mode", "archived"):
        out[flag] = bool(out[flag])
    if out["api_key_valid"] is not None:
        out["api_key_valid"] = bool(out["api_key_valid"])
    out["api_key_set"] = ctx.secrets.has(apikey_secret(iid))
    out["hook_key_set"] = ctx.secrets.has(hookkey_secret(iid))
    out["health"] = loads(row.get("health_json"), [])
    out["webhook_last_test_at"] = (ctx.state_get(f"webhook_test:{iid}") or {}).get(
        "received_at")
    counts = ctx.db.fetch_one(
        "SELECT COUNT(*) AS n, SUM(CASE WHEN monitored=1 THEN 1 ELSE 0 END) AS monitored, "
        "SUM(CASE WHEN monitor_new_items='all' THEN 1 ELSE 0 END) AS future FROM lib_items "
        "WHERE instance_id=? AND removed_at IS NULL", (iid,))
    wanted = ctx.db.fetch_one("SELECT COUNT(*) AS n FROM wanted_items WHERE instance_id=? AND "
                              "status IN ('wanted','searching','unavailable')", (iid,))
    out["counts"] = {"items": int(counts["n"] or 0) if counts else 0,
                     "monitored": int(counts["monitored"] or 0) if counts else 0,
                     "future_monitoring": int(counts["future"] or 0) if counts else 0,
                     "wanted_open": int(wanted["n"] or 0) if wanted else 0}
    out["indexers"] = ctx.db.fetch_dicts(
        "SELECT indexer_id, name, protocol, enable_rss, enable_automatic_search, "
        "enable_interactive_search, priority FROM instance_indexer_assignments "
        "WHERE instance_id=? ORDER BY priority, name", (iid,))
    out["root_folders"] = ctx.db.fetch_dicts(
        "SELECT root_path, core_path, accessible, free_space, provider_root_folder_id FROM "
        "instance_root_mappings WHERE instance_id=? ORDER BY root_path", (iid,))
    seerr = ctx.db.fetch_one("SELECT * FROM instance_seerr_routing WHERE instance_id=?", (iid,))
    out["seerr_routing"] = dict(seerr) if seerr else None
    last_sync = ctx.db.fetch_one("SELECT started_at, finished_at, status FROM library_sync_runs "
                                 "WHERE instance_id=? ORDER BY id DESC LIMIT 1", (iid,))
    out["last_library_sync"] = dict(last_sync) if last_sync else None
    if detail:
        out["profiles"] = ctx.db.fetch_dicts(
            "SELECT kind, provider_id, name, data_json FROM instance_profiles WHERE "
            "instance_id=? ORDER BY kind, name", (iid,))
        for p in out["profiles"]:
            p["data"] = loads(p.pop("data_json"), {})
        cut = ctx.db.fetch_one("SELECT * FROM instance_cutover_state WHERE instance_id=?",
                               (iid,))
        out["cutover"] = None if cut is None else {
            **dict(cut), "seerr_previous_json": None, "seerr_applied_json": None,
            "has_seerr_rollback": bool(cut["seerr_previous_json"])}
        out["suspend_state"] = loads(row.get("suspend_state_json"), None)
    return out


@router.get("/instances")
def list_all(include_archived: bool = False, _: Principal = Depends(require_viewer),
             ctx: AppContext = Depends(get_app_context)) -> list[dict[str, Any]]:
    sql = "SELECT * FROM instances" + ("" if include_archived else " WHERE archived=0")
    return [instance_view(ctx, r) for r in ctx.db.fetch_dicts(sql + " ORDER BY sort_order, id")]


class NewInstance(BaseModel):
    id: str = Field(min_length=2, max_length=48)
    app: Literal["sonarr", "radarr"]
    media_type: Literal["tv", "anime", "movie", "anime_movie"]
    name: str = Field(min_length=1, max_length=100)
    url: str = Field(max_length=500)
    public_url: str | None = Field(default=None, max_length=500)
    api_key: str = Field(min_length=8, max_length=200)
    hook_fail_policy: Literal["open", "closed"] = "open"
    download_category: str | None = Field(default=None, max_length=100)
    verify: bool = True


async def _verify_connection(ctx: AppContext, iid: str, app: str, url: str,
                             api_key: str) -> dict[str, Any]:
    transports = ctx.services.get("arr_transports") or {}
    client = ArrClient(url, api_key, instance_id=iid, app=app, timeout=10, retries=0,
                       transport=transports.get(iid))
    try:
        status = await client.system_status()
    except ArrError as exc:
        raise HTTPException(422, {"code": exc.code, "message": "Verbindingstest mislukt; "
                                  "niets opgeslagen. " + exc.message}) from exc
    name = str(status.get("appName") or "").lower()
    if name and name != app:
        raise HTTPException(422, {"code": "WRONG_APP", "message":
                                  f"Op deze URL draait {status.get('appName')}, niet {app}."})
    return status


@router.post("/instances", status_code=201)
async def create(body: NewInstance, principal: Principal = Depends(require_admin),
                 ctx: AppContext = Depends(get_app_context)) -> dict[str, Any]:
    data = body.model_dump(exclude={"api_key", "verify"})
    try:
        validate_new(data)
    except ValueError as exc:
        raise HTTPException(422, str(exc)) from exc
    if instance_row(ctx, body.id) is not None:
        raise HTTPException(409, f"Instantie '{body.id}' bestaat al")
    if body.verify:
        await _verify_connection(ctx, body.id, body.app, body.url, body.api_key)
    create_instance(ctx, data, api_key=body.api_key, actor=principal.actor)
    audit(ctx.db, actor=principal.actor, action="instance.create", target_type="instance",
          target_id=body.id, instance_id=body.id, new_value=data)
    row = instance_row(ctx, body.id)
    assert row is not None
    return instance_view(ctx, row, detail=True)


@router.get("/instances/{instance_id}")
def detail(instance_id: str, _: Principal = Depends(require_viewer),
           ctx: AppContext = Depends(get_app_context)) -> dict[str, Any]:
    return instance_view(ctx, require_instance(ctx, instance_id), detail=True)


class PatchInstance(BaseModel):
    name: str | None = Field(default=None, max_length=100)
    url: str | None = Field(default=None, max_length=500)
    public_url: str | None = Field(default=None, max_length=500)
    api_key: str | None = Field(default=None, min_length=8, max_length=200)
    hook_fail_policy: Literal["open", "closed"] | None = None
    shadow_mode: bool | None = None
    archived: bool | None = None
    sort_order: int | None = None
    media_type: Literal["tv", "anime", "movie", "anime_movie"] | None = None
    default_quality_profile_id: int | None = None
    default_language_profile_id: int | None = None
    default_root_folder: str | None = None
    default_monitor: str | None = None
    default_monitor_new_items: Literal["all", "none"] | None = None
    default_series_type: Literal["standard", "anime", "daily"] | None = None
    default_minimum_availability: Literal["announced", "inCinemas", "released"] | None = None
    download_category: str | None = None
    verify: bool = True


@router.patch("/instances/{instance_id}")
async def patch(instance_id: str, body: PatchInstance,
                principal: Principal = Depends(require_operator),
                ctx: AppContext = Depends(get_app_context)) -> dict[str, Any]:
    row = require_instance(ctx, instance_id)
    changes = body.model_dump(exclude_unset=True, exclude={"api_key", "archived", "verify"})
    # public_url ook: Seerr krijgt dat adres samen met de API-sleutel van de instantie
    sensitive = body.api_key is not None or body.archived is not None or "url" in changes \
        or "public_url" in changes
    if sensitive and principal.role != "admin":
        raise HTTPException(403, "URL, API-sleutel en archiveren vereisen de rol 'admin'")
    if body.verify and (body.api_key is not None or "url" in changes):
        await _verify_connection(ctx, instance_id, row["app"], changes.get("url") or row["url"],
                                 body.api_key or ctx.secrets.get(apikey_secret(instance_id))
                                 or "")
    try:
        old = update_instance(ctx, instance_id, changes)
    except ValueError as exc:
        raise HTTPException(422, str(exc)) from exc
    if body.api_key is not None:
        ctx.secrets.set(apikey_secret(instance_id), body.api_key, principal.actor)
        old["api_key"] = "***"
        changes["api_key"] = "*** (gewijzigd)"
    if body.archived is not None and bool(row["archived"]) != body.archived:
        # archiveren raakt nooit media of de Sonarr/Radarr-configuratie aan
        ctx.db.execute("UPDATE instances SET archived=?, archived_at=?, updated_at=? WHERE id=?",
                       (int(body.archived), utcnow() if body.archived else None, utcnow(),
                        instance_id))
        old["archived"] = bool(row["archived"])
        changes["archived"] = body.archived
    if changes:
        audit(ctx.db, actor=principal.actor, action="instance.update", target_type="instance",
              target_id=instance_id, instance_id=instance_id, old_value=old, new_value=changes)
    new = instance_row(ctx, instance_id)
    assert new is not None
    return instance_view(ctx, new, detail=True)


@router.post("/instances/{instance_id}/test")
async def test(instance_id: str, principal: Principal = Depends(require_operator),
               ctx: AppContext = Depends(get_app_context)) -> dict[str, Any]:
    require_instance(ctx, instance_id)
    return await check_instance(ctx, instance_id)


class SuspendBody(BaseModel):
    reason: str = Field(default="", max_length=500)


@router.post("/instances/{instance_id}/suspend")
async def suspend(instance_id: str, body: SuspendBody | None = None,
                  principal: Principal = Depends(require_operator),
                  ctx: AppContext = Depends(get_app_context)) -> dict[str, Any]:
    require_instance(ctx, instance_id, mutable=True)
    try:
        return await arr_call(suspend_instance(ctx, instance_id, principal.actor,
                                               (body.reason if body else "")))
    except SuspendError as exc:
        raise HTTPException(502, {"message": str(exc), "details": exc.details}) from exc


@router.post("/instances/{instance_id}/resume")
async def resume(instance_id: str, principal: Principal = Depends(require_operator),
                 ctx: AppContext = Depends(get_app_context)) -> dict[str, Any]:
    require_instance(ctx, instance_id)
    try:
        return await arr_call(resume_instance(ctx, instance_id, principal.actor))
    except SuspendError as exc:
        raise HTTPException(502, {"message": str(exc), "details": exc.details}) from exc


class WebhookBody(BaseModel):
    rotate_key: bool = False


@router.post("/instances/{instance_id}/webhook")
async def webhook(instance_id: str, body: WebhookBody | None = None,
                  principal: Principal = Depends(require_admin),
                  ctx: AppContext = Depends(get_app_context)) -> dict[str, Any]:
    require_instance(ctx, instance_id, mutable=True)
    try:
        return await arr_call(register_webhook(ctx, instance_id, principal.actor,
                                               rotate=bool(body and body.rotate_key)))
    except SuspendError as exc:
        raise HTTPException(502, str(exc)) from exc


@router.get("/instances/{instance_id}/library")
def instance_library(instance_id: str, q: str | None = None, monitored: bool | None = None,
                     state: str | None = None, page: int = Query(1, ge=1),
                     page_size: int = Query(50, ge=1, le=500), sort: str = "title",
                     _: Principal = Depends(require_viewer),
                     ctx: AppContext = Depends(get_app_context)) -> dict[str, Any]:
    from mediaguard.library.queries import list_items

    require_instance(ctx, instance_id)
    return list_items(ctx, instance_id=instance_id, q=q, monitored=monitored, state=state,
                      page=page, page_size=page_size, sort=sort)


@router.get("/instances/{instance_id}/wanted")
def instance_wanted(instance_id: str, status: str | None = "open", reason: str | None = None,
                    q: str | None = None, page: int = Query(1, ge=1),
                    page_size: int = Query(50, ge=1, le=500), sort: str = "priority",
                    _: Principal = Depends(require_viewer),
                    ctx: AppContext = Depends(get_app_context)) -> dict[str, Any]:
    from mediaguard.wanted.engine import list_wanted

    require_instance(ctx, instance_id)
    return list_wanted(ctx, instance_id=instance_id, status=status, reason=reason, q=q,
                       page=page, page_size=page_size, sort=sort)


class BackfillBody(BaseModel):
    dry_run: bool = False
    mode: Literal["queue", "inline"] = "queue"
    wait_seconds: float | None = Field(default=None, ge=0, le=600)


@router.post("/instances/{instance_id}/backfill")
async def instance_backfill(instance_id: str, body: BackfillBody | None = None,
                            principal: Principal = Depends(require_operator),
                            ctx: AppContext = Depends(get_app_context)) -> dict[str, Any]:
    from mediaguard.wanted.backfill import run_backfill

    body = body or BackfillBody()
    require_instance(ctx, instance_id, mutable=not body.dry_run)
    if body.dry_run or body.mode == "inline":
        result = await arr_call(run_backfill(ctx, instance_id, trigger="manual-run",
                                             dry_run=body.dry_run,
                                             wait_seconds=body.wait_seconds,
                                             actor=principal.actor))
        return result
    job_id = jobs.enqueue(ctx.db, "backfill.run", {"trigger": "manual-run"},
                          instance_id=instance_id, created_by=principal.actor,
                          idempotency_key=f"backfill.manual:{instance_id}:{utcnow()[:16]}",
                          max_attempts=1)
    runner = ctx.services.get("jobs")
    if runner is not None:
        runner.wake()
    audit(ctx.db, actor=principal.actor, action="backfill.enqueue", target_type="instance",
          target_id=instance_id, instance_id=instance_id, new_value={"job_id": job_id})
    return {"queued": True, "job_id": job_id}


@router.post("/instances/{instance_id}/sync")
async def instance_sync(instance_id: str, mode: Literal["queue", "inline"] = "queue",
                        principal: Principal = Depends(require_operator),
                        ctx: AppContext = Depends(get_app_context)) -> dict[str, Any]:
    from mediaguard.library.sync import sync_instance

    require_instance(ctx, instance_id)
    if mode == "inline":
        return await arr_call(sync_instance(ctx, instance_id, full=True))
    job_id = jobs.enqueue(ctx.db, "library.sync", {"full": True}, instance_id=instance_id,
                          created_by=principal.actor, max_attempts=2,
                          idempotency_key=f"library.sync.manual:{instance_id}:{utcnow()[:16]}")
    runner = ctx.services.get("jobs")
    if runner is not None:
        runner.wake()
    return {"queued": True, "job_id": job_id}


# -- ownership ---------------------------------------------------------------------------------
@router.get("/ownership")
def ownership_list(canonical_id: str | None = None, _: Principal = Depends(require_viewer),
                   ctx: AppContext = Depends(get_app_context)) -> list[dict[str, Any]]:
    return ownership_records(ctx, canonical_id)


@router.get("/ownership/conflicts")
def conflicts(include_intentional: bool = False, _: Principal = Depends(require_viewer),
              ctx: AppContext = Depends(get_app_context)) -> dict[str, Any]:
    items = ownership_conflicts(ctx, include_intentional=include_intentional)
    return {"total": len(items), "conflicts": items}


class SimulateBody(BaseModel):
    kind: Literal["series", "movie"]
    tvdb_id: int | None = None
    tmdb_id: int | None = None
    imdb_id: str | None = None
    title: str | None = None
    is_anime: bool | None = None
    target_profile: str | None = None
    edition: str | None = None


@router.post("/ownership/simulate")
def simulate(body: SimulateBody, _: Principal = Depends(require_viewer),
             ctx: AppContext = Depends(get_app_context)) -> dict[str, Any]:
    try:
        return simulate_ownership(ctx, body.model_dump())
    except ValueError as exc:
        raise HTTPException(422, str(exc)) from exc


class OwnershipBody(BaseModel):
    media_type: Literal["tv", "anime", "movie", "anime_movie"]
    canonical_id: str = Field(pattern=r"^(tvdb|tmdb|imdb):[A-Za-z0-9]+$")
    instance_id: str
    target_profile: str = ""
    edition: str = ""
    provider_id: int | None = None
    root_path: str | None = None
    allow_multi: bool = False
    reason: str = Field(default="", max_length=500)


@router.post("/ownership", status_code=201)
def ownership_set(body: OwnershipBody, principal: Principal = Depends(require_operator),
                  ctx: AppContext = Depends(get_app_context)) -> dict[str, Any]:
    require_instance(ctx, body.instance_id)
    existing = ctx.db.fetch_one(
        "SELECT * FROM instance_ownership WHERE canonical_id=? AND media_type=? AND edition=? "
        "AND target_profile=?", (body.canonical_id, body.media_type, body.edition,
                                 body.target_profile))
    ctx.db.execute(
        "INSERT INTO instance_ownership(media_type,canonical_id,edition,target_profile,"
        "instance_id,provider_id,root_path,is_override,allow_multi,reason,created_at,created_by)"
        " VALUES (?,?,?,?,?,?,?,1,?,?,?,?) ON CONFLICT(canonical_id,media_type,edition,"
        "target_profile) DO UPDATE SET instance_id=excluded.instance_id, "
        "provider_id=excluded.provider_id, root_path=excluded.root_path, is_override=1, "
        "allow_multi=excluded.allow_multi, reason=excluded.reason",
        (body.media_type, body.canonical_id, body.edition, body.target_profile, body.instance_id,
         body.provider_id, body.root_path, int(body.allow_multi), body.reason, utcnow(),
         principal.actor))
    row = ctx.db.fetch_one(
        "SELECT * FROM instance_ownership WHERE canonical_id=? AND media_type=? AND edition=? "
        "AND target_profile=?", (body.canonical_id, body.media_type, body.edition,
                                 body.target_profile))
    assert row is not None
    audit(ctx.db, actor=principal.actor, action="ownership.set", target_type="ownership",
          target_id=row["id"], instance_id=body.instance_id,
          old_value=dict(existing) if existing else None, new_value=body.model_dump())
    return dict(row)


@router.delete("/ownership/{ownership_id}")
def ownership_delete(ownership_id: int, principal: Principal = Depends(require_operator),
                     ctx: AppContext = Depends(get_app_context)) -> dict[str, bool]:
    row = ctx.db.fetch_one("SELECT * FROM instance_ownership WHERE id=?", (ownership_id,))
    if row is None:
        raise HTTPException(404, "Ownership-registratie niet gevonden")
    ctx.db.execute("DELETE FROM instance_ownership WHERE id=?", (ownership_id,))
    audit(ctx.db, actor=principal.actor, action="ownership.delete", target_type="ownership",
          target_id=ownership_id, instance_id=row["instance_id"], old_value=dict(row))
    return {"deleted": True}


router.include_router(manage.router)
