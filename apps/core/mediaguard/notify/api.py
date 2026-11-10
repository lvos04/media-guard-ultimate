"""API voor meldingen buiten de browser, overzichten en de bundelkopie buiten de NAS."""

from __future__ import annotations

import asyncio
import contextlib
from typing import Any

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field

from mediaguard import taskwatch
from mediaguard.audit import audit
from mediaguard.auth import Principal, require_admin, require_viewer
from mediaguard.context import AppContext, get_app_context
from mediaguard.notify import channels as ch
from mediaguard.notify import offsite, service

router = APIRouter(prefix="/api/v1/notify", tags=["notify"])


def register_settings() -> None:
    service.register_settings()
    offsite.register_settings()


async def startup(ctx: AppContext) -> None:
    ctx.services["notify_channels_ready"] = True
    with contextlib.suppress(Exception):
        service.ensure_default_ntfy(ctx)
    if ctx.settings.start_background and ctx.settings.role in ("core", "all"):
        ctx.services["notify_task"] = taskwatch.spawn(
            ctx, "notify", service.loop, label_nl="Meldingen versturen", every=60)


async def shutdown(ctx: AppContext) -> None:
    task = ctx.services.pop("notify_task", None)
    if task is not None:
        task.cancel()
        with contextlib.suppress(asyncio.CancelledError):
            await task


# -- kanalen ------------------------------------------------------------------------------------
@router.get("/overview")
def overview(_: Principal = Depends(require_admin),
             ctx: AppContext = Depends(get_app_context)) -> dict[str, Any]:
    service.ensure_default_ntfy(ctx)
    return {
        "channels": [service.public_channel(ctx, c) for c in service.list_channels(ctx)],
        "kinds": [{"kind": k, "label_nl": ch.KIND_LABELS_NL[k],
                   "fields": [{"name": n, "secret": s, "required": r}
                              for n, s, r in ch.FIELDS[k]]} for k in ch.KINDS],
        "categories": [{"key": c, "label_nl": service.CATEGORY_LABELS_NL[c],
                        "value": service.preference(ctx, c),
                        "default": service.DEFAULT_PREF[c]} for c in service.CATEGORIES],
        "choices": [{"value": v, "label_nl": service.CHOICE_LABELS_NL[v]}
                    for v in service.CHOICES],
        "digest": {"daily": bool(ctx.config.value("notify.digest.daily")),
                   "time": str(ctx.config.value("notify.digest.time")),
                   "weekly": bool(ctx.config.value("notify.digest.weekly")),
                   "weekday": int(ctx.config.value("notify.digest.weekday")),
                   "timezone": str(ctx.config.value("notify.timezone"))},
        "rate_per_hour": int(ctx.config.value("notify.rate_per_hour")),
        "base_url": str(ctx.config.value("notify.base_url") or ""),
        # opslagoverzicht als melding (storage_ledger)
        "storage": {"daily": bool(ctx.config.value("storage.ledger_notify_daily")),
                    "weekly": bool(ctx.config.value("storage.ledger_notify_weekly")),
                    "growth_alert_gb": int(ctx.config.value("storage.growth_alert_gb"))},
    }


class ChannelBody(BaseModel):
    kind: str
    name: str = Field(default="", max_length=60)
    config: dict[str, Any] = Field(default_factory=dict)
    secrets: dict[str, str] = Field(default_factory=dict)
    enabled: bool = True


@router.post("/channels")
def add_channel(body: ChannelBody, principal: Principal = Depends(require_admin),
                ctx: AppContext = Depends(get_app_context)) -> dict[str, Any]:
    try:
        channel = service.create_channel(ctx, body.kind, body.name, body.config, body.secrets,
                                         enabled=body.enabled)
    except ValueError as exc:
        raise HTTPException(422, str(exc)) from exc
    audit(ctx.db, actor=principal.actor, action="notify.channel_added", target_type="notify",
          target_id=str(channel["id"]), new_value={"kind": body.kind})
    return service.public_channel(ctx, channel)


class ChannelPatch(BaseModel):
    name: str | None = Field(default=None, max_length=60)
    enabled: bool | None = None
    config: dict[str, Any] | None = None
    secrets: dict[str, str] | None = None


@router.patch("/channels/{channel_id}")
def patch_channel(channel_id: int, body: ChannelPatch,
                  principal: Principal = Depends(require_admin),
                  ctx: AppContext = Depends(get_app_context)) -> dict[str, Any]:
    try:
        channel = service.update_channel(ctx, channel_id, name=body.name, enabled=body.enabled,
                                         config=body.config, secret_values=body.secrets)
    except KeyError as exc:
        raise HTTPException(404, "Kanaal niet gevonden") from exc
    except ValueError as exc:
        raise HTTPException(422, str(exc)) from exc
    audit(ctx.db, actor=principal.actor, action="notify.channel_changed", target_type="notify",
          target_id=str(channel_id))
    return service.public_channel(ctx, channel)


@router.delete("/channels/{channel_id}")
def remove_channel(channel_id: int, principal: Principal = Depends(require_admin),
                   ctx: AppContext = Depends(get_app_context)) -> dict[str, bool]:
    try:
        service.delete_channel(ctx, channel_id)
    except KeyError as exc:
        raise HTTPException(404, "Kanaal niet gevonden") from exc
    audit(ctx.db, actor=principal.actor, action="notify.channel_removed", target_type="notify",
          target_id=str(channel_id))
    return {"deleted": True}


@router.post("/channels/{channel_id}/test")
async def test_channel(channel_id: int, _: Principal = Depends(require_admin),
                       ctx: AppContext = Depends(get_app_context)) -> dict[str, Any]:
    try:
        return await service.send_test(ctx, channel_id)
    except KeyError as exc:
        raise HTTPException(404, "Kanaal niet gevonden") from exc


class PrefsBody(BaseModel):
    categories: dict[str, str] = Field(default_factory=dict)
    digest_daily: bool | None = None
    digest_time: str | None = Field(default=None, pattern=r"^([01]\d|2[0-3]):[0-5]\d$")
    digest_weekly: bool | None = None
    digest_weekday: int | None = Field(default=None, ge=1, le=7)
    rate_per_hour: int | None = Field(default=None, ge=1, le=200)
    base_url: str | None = Field(default=None, max_length=300)
    storage_daily: bool | None = None
    storage_weekly: bool | None = None
    storage_growth_alert_gb: int | None = Field(default=None, ge=0, le=10000)


@router.put("/preferences")
def set_preferences(body: PrefsBody, principal: Principal = Depends(require_admin),
                    ctx: AppContext = Depends(get_app_context)) -> dict[str, Any]:
    for cat, value in body.categories.items():
        if cat not in service.CATEGORIES or value not in service.CHOICES:
            raise HTTPException(422, f"Ongeldige keuze voor {cat}")
        ctx.config.set("global", "", f"notify.pref.{cat}", value, principal.actor)
    if body.base_url and not body.base_url.startswith(("http://", "https://")):
        raise HTTPException(422, "Het adres moet met http:// of https:// beginnen")
    updates: list[tuple[str, Any]] = [("notify.digest.daily", body.digest_daily),
                                   ("notify.digest.time", body.digest_time),
                                   ("notify.digest.weekly", body.digest_weekly),
                                   ("notify.digest.weekday", body.digest_weekday),
                                   ("notify.rate_per_hour", body.rate_per_hour),
                                   ("notify.base_url", body.base_url),
                                   ("storage.ledger_notify_daily", body.storage_daily),
                                   ("storage.ledger_notify_weekly", body.storage_weekly),
                                   ("storage.growth_alert_gb", body.storage_growth_alert_gb)]
    for key, value in updates:
        if value is not None:
            ctx.config.set("global", "", key, value, principal.actor)
    return overview(principal, ctx)


@router.get("/outbox")
def get_outbox(limit: int = 30, _: Principal = Depends(require_admin),
               ctx: AppContext = Depends(get_app_context)) -> dict[str, Any]:
    return {"items": service.outbox(ctx, max(1, min(limit, 200)))}


# -- overzichten ("Wat is er gebeurd") --------------------------------------------------------
@router.get("/digests")
def digests(limit: int = 30, _: Principal = Depends(require_viewer),
            ctx: AppContext = Depends(get_app_context)) -> dict[str, Any]:
    return {"today": service.today_so_far(ctx),
            "items": service.history(ctx, max(1, min(limit, 120)))}


@router.post("/digests/send-now")
def digest_now(principal: Principal = Depends(require_admin),
               ctx: AppContext = Depends(get_app_context)) -> dict[str, Any]:
    """Stuur nu een overzicht van de afgelopen 24 uur (handig om de kanalen te testen)."""
    today = service.today_so_far(ctx)
    channels = service.list_channels(ctx, enabled_only=True)
    for channel in channels:
        service.enqueue(ctx, int(channel["id"]), "digest", "MediaGuard-overzicht (proef)",
                        today["text_nl"], "/activiteit?tab=gebeurd", priority="low")
    service.wake(ctx)
    audit(ctx.db, actor=principal.actor, action="notify.digest_test", target_type="notify")
    return {"queued": len(channels), "text_nl": today["text_nl"]}


# -- herstelbundel buiten de NAS ----------------------------------------------------------------
@router.get("/offsite")
def offsite_status(_: Principal = Depends(require_admin),
                   ctx: AppContext = Depends(get_app_context)) -> dict[str, Any]:
    out = offsite.status(ctx)
    out["kinds"] = [{"value": k, "label_nl": offsite.KIND_LABELS_NL[k]} for k in offsite.KINDS]
    out["email_channels"] = [{"id": c["id"], "name": c["name"]}
                             for c in service.list_channels(ctx) if c["kind"] == "email"]
    return out


class OffsiteBody(BaseModel):
    kind: str | None = None
    path: str | None = Field(default=None, max_length=500)
    webdav_url: str | None = Field(default=None, max_length=500)
    webdav_user: str | None = Field(default=None, max_length=200)
    webdav_password: str | None = Field(default=None, max_length=500)
    s3_endpoint: str | None = Field(default=None, max_length=500)
    s3_region: str | None = Field(default=None, max_length=60)
    s3_bucket: str | None = Field(default=None, max_length=200)
    s3_prefix: str | None = Field(default=None, max_length=200)
    s3_access_key: str | None = Field(default=None, max_length=200)
    s3_secret_key: str | None = Field(default=None, max_length=500)
    email_channel: int | None = Field(default=None, ge=0)
    keep: int | None = Field(default=None, ge=1, le=100)


@router.put("/offsite")
def offsite_set(body: OffsiteBody, principal: Principal = Depends(require_admin),
                ctx: AppContext = Depends(get_app_context)) -> dict[str, Any]:
    data = body.model_dump(exclude_none=True)
    if "kind" in data and data["kind"] not in offsite.KINDS:
        raise HTTPException(422, "Onbekend doel")
    for secret_field in ("webdav_password", "s3_secret_key"):
        value = data.pop(secret_field, None)
        if value is not None:
            if value:
                ctx.secrets.set(offsite.secret_name(secret_field), value, actor=principal.actor)
            else:
                ctx.secrets.delete(offsite.secret_name(secret_field))
    for key, value in data.items():
        ctx.config.set("global", "", f"backup.offsite.{key}", value, principal.actor)
    audit(ctx.db, actor=principal.actor, action="backup.offsite.configured",
          target_type="backup", new_value={"kind": data.get("kind")})
    return offsite_status(principal, ctx)


@router.post("/offsite/run")
async def offsite_run(principal: Principal = Depends(require_admin),
                      ctx: AppContext = Depends(get_app_context)) -> dict[str, Any]:
    """Nu kopiëren (en controleren); ook de testknop in de app."""
    return await offsite.run(ctx, actor=principal.actor)
