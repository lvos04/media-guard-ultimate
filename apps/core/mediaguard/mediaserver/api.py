"""API, instellingen en achtergrondlus voor de mediaserver (Emby/Jellyfin/Plex) en
Nederlandse ondertitels (Bazarr/OpenSubtitles). Alles in te stellen in de app."""

from __future__ import annotations

import asyncio
import contextlib
import logging
from typing import Any, Literal

from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel, ConfigDict, Field

from mediaguard import taskwatch
from mediaguard.audit import audit
from mediaguard.auth import Principal, require_admin, require_operator, require_viewer
from mediaguard.context import AppContext, get_app_context
from mediaguard.db import utcnow
from mediaguard.mediaserver import service as S
from mediaguard.mediaserver import subtitles as T
from mediaguard.mediaserver.clients import MediaServerError
from mediaguard.settings_registry import REGISTRY, SettingDef

LOGGER = logging.getLogger(__name__)
router = APIRouter(prefix="/api/v1/mediaserver", tags=["mediaserver"])
CAT_M, CAT_S = "Mediaserver (Emby)", "Ondertitels"

FEATURE_KEYS = ("refresh_after_handoff", "playback_test", "playback_fail_action",
                "pause_while_watching", "skip_recent_watch", "recent_watch_hours")
SUB_KEYS = ("nl_auto", "opensubtitles_enabled", "opensubtitles_daily")


def register_settings() -> None:
    REGISTRY.register(
        SettingDef("mediaserver.kind", "enum", "auto", CAT_M, "Mediaserver", "Media server",
                   choices=S.KINDS,
                   choice_labels_nl={"auto": "Automatisch (Emby eerst)", "emby": "Emby",
                                     "jellyfin": "Jellyfin", "plex": "Plex", "off": "Uit"}),
        SettingDef("mediaserver.url", "string", "", CAT_M, "Adres van de mediaserver",
                   "Media server URL",
                   help_nl="Leeg = MediaGuard zoekt de server zelf op je netwerk."),
        SettingDef("mediaserver.refresh_after_handoff", "bool", True, CAT_M,
                   "Verversen na overdracht", "Refresh after handoff",
                   help_nl="Na elke AV1-vervanging of terugzetting ververst de mediaserver "
                           "precies dat bestand, zodat je meteen de nieuwe versie ziet."),
        SettingDef("mediaserver.playback_test", "bool", True, CAT_M, "Afspeeltest",
                   "Playback test",
                   help_nl="± 10 minuten na een overdracht vraagt MediaGuard de server of een "
                           "gangbare tv het nieuwe bestand kan afspelen."),
        SettingDef("mediaserver.playback_fail_action", "enum", "notify", CAT_M,
                   "Als de afspeeltest mislukt", "On failed playback test",
                   choices=("notify", "rollback"),
                   choice_labels_nl={"notify": "Alleen melden", "rollback": "Origineel "
                                     "terugzetten (als dat nog kan)"}),
        SettingDef("mediaserver.pause_while_watching", "enum", "both", CAT_M,
                   "AV1 pauzeren tijdens kijken", "Pause AV1 while watching",
                   choices=("both", "gpu", "off"),
                   choice_labels_nl={"both": "Nieuwe encodes wachten (CPU en GPU)",
                                     "gpu": "Alleen de GPU vrijhouden", "off": "Uit"},
                   help_nl="Lopende encodes draaien met lage prioriteit door; nieuwe wachten "
                           "tot niemand meer kijkt."),
        SettingDef("mediaserver.skip_recent_watch", "bool", True, CAT_M,
                   "Recent bekeken niet omzetten", "Skip recently watched",
                   help_nl="Wat iemand net bekijkt of de laatste uren keek, wordt pas later "
                           "naar AV1 omgezet."),
        SettingDef("mediaserver.recent_watch_hours", "int", 24, CAT_M,
                   "Recent bekeken = laatste (uur)", "Recently watched window (hours)",
                   minimum=1, maximum=24 * 14, unit="uur", advanced=True),
        SettingDef("subtitles.nl_auto", "bool", True, CAT_S,
                   "Nederlandse ondertitels automatisch zoeken", "Find Dutch subtitles",
                   help_nl="Via Bazarr (als die er is), anders via je OpenSubtitles-account."),
        SettingDef("subtitles.bazarr_url", "string", "", CAT_S, "Adres van Bazarr",
                   "Bazarr URL", help_nl="Leeg = MediaGuard zoekt Bazarr zelf."),
        SettingDef("subtitles.opensubtitles_enabled", "bool", False, CAT_S,
                   "OpenSubtitles gebruiken (zonder Bazarr)", "Use OpenSubtitles",
                   help_nl="Alleen als er geen Bazarr is. MediaGuard zet een .nl.srt naast het "
                           "bestand, overschrijft nooit iets."),
        SettingDef("subtitles.opensubtitles_daily", "int", 10, CAT_S,
                   "OpenSubtitles: maximaal per dag", "OpenSubtitles daily limit",
                   minimum=1, maximum=1000, advanced=True,
                   help_nl="Gratis accounts mogen 5–20 per dag downloaden."),
    )


# -- status ------------------------------------------------------------------------------------
def _features(ctx: AppContext) -> dict[str, Any]:
    return {k: ctx.config.value(f"mediaserver.{k}") for k in FEATURE_KEYS}


@router.get("/status")
def status(_: Principal = Depends(require_viewer),
           ctx: AppContext = Depends(get_app_context)) -> dict[str, Any]:
    cfg = S.configured(ctx)
    pending = ctx.db.fetch_one(
        "SELECT SUM(done_at IS NULL) AS open, SUM(result='ok') AS ok, "
        "SUM(result='transcode') AS transcode, SUM(result='failed') AS failed "
        "FROM mediaserver_pending")
    actions = ctx.db.fetch_dicts(
        "SELECT ts, kind, path, ok, detail_nl FROM mediaserver_actions ORDER BY id DESC "
        "LIMIT 20")
    tests = ctx.db.fetch_dicts(
        "SELECT transaction_id, path, result, detail_nl, done_at, encoding_job_id FROM "
        "mediaserver_pending WHERE result IS NOT NULL AND result!='skipped' "
        "ORDER BY done_at DESC LIMIT 10")
    sub_bazarr = T.bazarr(ctx)
    sub_missing = T.missing(ctx, limit=0)["total"]
    return {
        "kind": cfg["kind"], "label": S.LABEL.get(cfg["kind"], "Uit"),
        "url": cfg["url"], "url_source": cfg["source"], "key_set": cfg["key_set"],
        "setting_kind": ctx.config.value("mediaserver.kind"),
        "setting_url": ctx.config.value("mediaserver.url"),
        "connected": ctx.state_get("mediaserver_connected"),
        "discovery": ctx.state_get("mediaserver_discovery"),
        "path_map": (ctx.state_get("mediaserver_path_map", {}) or {}).get("map") or [],
        "features": _features(ctx),
        "hold": {"active": bool(ctx.state_get("mediaserver_hold_active")),
                 "reason": ctx.state_get("mediaserver_hold_reason")},
        "sessions": ctx.state_get("mediaserver_sessions"),
        "pending": {k: int(pending[k] or 0) for k in ("open", "ok", "transcode", "failed")}
        if pending else {},
        "tests": tests, "actions": actions,
        "key_help_nl": {
            "emby": "Emby: Instellingen (tandwiel) → Geavanceerd → API-sleutels → "
                    "Nieuwe sleutel (+) → naam 'MediaGuard' → OK. Kopieer de sleutel hierheen.",
            "jellyfin": "Jellyfin: Dashboard → API-sleutels → + → naam 'MediaGuard'.",
            "plex": "Plex: je X-Plex-Token (Plex-support: 'Finding an authentication token').",
        },
        "subtitles": {
            "nl_auto": bool(ctx.config.value("subtitles.nl_auto")),
            "nl_wanted": T.nl_wanted(ctx),
            "bazarr_url": ctx.config.value("subtitles.bazarr_url") or (
                sub_bazarr.url if sub_bazarr else ""),
            "bazarr_key_set": ctx.secrets.has(T.BAZARR_KEY),
            "bazarr_found": (ctx.state_get("mediaserver_discovery", {}) or {}).get("bazarr")
            or [],
            "opensubtitles_enabled": bool(ctx.config.value("subtitles.opensubtitles_enabled")),
            "opensubtitles_key_set": ctx.secrets.has(T.OS_KEY) and ctx.secrets.has(T.OS_USER),
            "opensubtitles_daily": int(ctx.config.value("subtitles.opensubtitles_daily")),
            "downloads_today": T.downloads_today(ctx),
            "source": "bazarr" if sub_bazarr else (
                "opensubtitles" if T.opensubtitles(ctx) else None),
            "missing": sub_missing,
            "last": ctx.state_get("subtitles_last"),
        },
    }


# -- instellen ---------------------------------------------------------------------------------
class ServerConfig(BaseModel):
    model_config = ConfigDict(extra="forbid")
    kind: Literal["auto", "emby", "jellyfin", "plex", "off"] | None = None
    url: str | None = Field(default=None, max_length=300)
    api_key: str | None = Field(default=None, max_length=300)
    clear_key: bool = False
    features: dict[str, Any] | None = None


def _set(ctx: AppContext, key: str, value: Any, actor: str) -> None:
    try:
        ctx.config.set("global", "", key, value, actor)
    except (KeyError, ValueError) as exc:
        raise HTTPException(422, str(exc)) from exc


@router.post("/config")
async def set_config(body: ServerConfig, principal: Principal = Depends(require_admin),
                     ctx: AppContext = Depends(get_app_context)) -> dict[str, Any]:
    if body.kind is not None:
        _set(ctx, "mediaserver.kind", body.kind, principal.actor)
    if body.url is not None:
        url = body.url.strip().rstrip("/")
        if url and not url.startswith(("http://", "https://")):
            url = "http://" + url
        _set(ctx, "mediaserver.url", url, principal.actor)
        ctx.state_set("mediaserver_path_map", {})
    if body.clear_key:
        ctx.secrets.delete(S.SECRET_KEY)
    elif body.api_key:
        ctx.secrets.set(S.SECRET_KEY, body.api_key.strip(), principal.actor)
    for key, value in (body.features or {}).items():
        if key not in FEATURE_KEYS:
            raise HTTPException(422, f"Onbekende instelling: {key}")
        _set(ctx, f"mediaserver.{key}", value, principal.actor)
    audit(ctx.db, actor=principal.actor, action="mediaserver.configured",
          target_type="settings", target_id="mediaserver",
          new_value={"kind": body.kind, "url": body.url, "key_changed": bool(body.api_key)
                     or body.clear_key, "features": body.features})
    result = await S.test_connection(ctx) if (body.api_key or body.url is not None
                                              or body.kind) else None
    return {"test": result, "status": status(principal, ctx)}


@router.post("/test")
async def test(_: Principal = Depends(require_operator),
               ctx: AppContext = Depends(get_app_context)) -> dict[str, Any]:
    return await S.test_connection(ctx)


@router.post("/discover")
async def discover(_: Principal = Depends(require_operator),
                   ctx: AppContext = Depends(get_app_context)) -> dict[str, Any]:
    return await S.discover(ctx)


@router.get("/playback/{encoding_job_id}")
def playback(encoding_job_id: int, _: Principal = Depends(require_viewer),
             ctx: AppContext = Depends(get_app_context)) -> dict[str, Any]:
    return {"result": S.playback_for_job(ctx, encoding_job_id)}


class SubtitleConfig(BaseModel):
    model_config = ConfigDict(extra="forbid")
    bazarr_url: str | None = Field(default=None, max_length=300)
    bazarr_api_key: str | None = Field(default=None, max_length=300)
    opensubtitles_api_key: str | None = Field(default=None, max_length=300)
    opensubtitles_username: str | None = Field(default=None, max_length=200)
    opensubtitles_password: str | None = Field(default=None, max_length=200)
    clear_bazarr_key: bool = False
    clear_opensubtitles: bool = False
    features: dict[str, Any] | None = None


@router.post("/subtitles/config")
async def subtitles_config(body: SubtitleConfig, principal: Principal = Depends(require_admin),
                           ctx: AppContext = Depends(get_app_context)) -> dict[str, Any]:
    if body.bazarr_url is not None:
        url = body.bazarr_url.strip().rstrip("/")
        if url and not url.startswith(("http://", "https://")):
            url = "http://" + url
        _set(ctx, "subtitles.bazarr_url", url, principal.actor)
    if body.clear_bazarr_key:
        ctx.secrets.delete(T.BAZARR_KEY)
    elif body.bazarr_api_key:
        ctx.secrets.set(T.BAZARR_KEY, body.bazarr_api_key.strip(), principal.actor)
    if body.clear_opensubtitles:
        for k in (T.OS_KEY, T.OS_USER, T.OS_PASS):
            ctx.secrets.delete(k)
    else:
        for k, v in ((T.OS_KEY, body.opensubtitles_api_key), (T.OS_USER,
                     body.opensubtitles_username), (T.OS_PASS, body.opensubtitles_password)):
            if v:
                ctx.secrets.set(k, v.strip(), principal.actor)
    for key, value in (body.features or {}).items():
        if key not in SUB_KEYS:
            raise HTTPException(422, f"Onbekende instelling: {key}")
        _set(ctx, f"subtitles.{key}", value, principal.actor)
    audit(ctx.db, actor=principal.actor, action="subtitles.configured",
          target_type="settings", target_id="subtitles",
          new_value={"bazarr_url": body.bazarr_url, "features": body.features})
    return {"test": await _subtitle_test(ctx), "status": status(principal, ctx)}


async def _subtitle_test(ctx: AppContext) -> dict[str, Any]:
    b = T.bazarr(ctx)
    if b is not None:
        try:
            info = await b.info()
        except MediaServerError as exc:
            return {"ok": False, "message_nl": f"Bazarr: {exc}"}
        return {"ok": True, "message_nl": f"Verbonden met Bazarr {info.get('version') or ''}"
                .strip()}
    o = T.opensubtitles(ctx)
    if o is not None:
        try:
            await o.login()
        except MediaServerError as exc:
            return {"ok": False, "message_nl": f"OpenSubtitles: {exc}"}
        return {"ok": True, "message_nl": "Ingelogd bij OpenSubtitles"}
    return {"ok": False, "message_nl": "Nog geen Bazarr-sleutel of OpenSubtitles-account."}


@router.post("/subtitles/test")
async def subtitles_test(_: Principal = Depends(require_operator),
                         ctx: AppContext = Depends(get_app_context)) -> dict[str, Any]:
    return await _subtitle_test(ctx)


@router.post("/subtitles/run")
async def subtitles_run(_: Principal = Depends(require_operator),
                        ctx: AppContext = Depends(get_app_context)) -> dict[str, Any]:
    return await T.tick(ctx)


@router.get("/subtitles/missing")
def subtitles_missing(limit: int = Query(100, ge=0, le=1000),
                      _: Principal = Depends(require_viewer),
                      ctx: AppContext = Depends(get_app_context)) -> dict[str, Any]:
    return T.missing(ctx, limit)


# -- achtergrond -------------------------------------------------------------------------------
async def _loop(ctx: AppContext) -> None:
    await asyncio.sleep(60)
    n = 0
    while True:
        try:
            if not S.configured(ctx)["url"] and n % 360 == 0:  # elke 3 u zoeken zolang nodig
                await S.discover(ctx)
            await S.tick(ctx)
            if n % 360 == 0:  # ondertitels elke 3 uur
                await T.tick(ctx)
            taskwatch.ok(ctx, "mediaserver")
        except Exception as exc:  # noqa: BLE001 - achtergrondlus mag nooit stoppen
            LOGGER.exception("mediaserver-ronde mislukt")
            taskwatch.fail(ctx, "mediaserver", exc)
        n += 1
        await asyncio.sleep(30)


async def startup(ctx: AppContext) -> None:
    ctx.state_set("mediaserver_started_at", utcnow())
    if ctx.settings.start_background and ctx.settings.role in ("core", "all"):
        ctx.services["mediaserver_task"] = taskwatch.spawn(
            ctx, "mediaserver", _loop, label_nl="Mediaserver (Emby) bijhouden", every=30)


async def shutdown(ctx: AppContext) -> None:
    task = ctx.services.pop("mediaserver_task", None)
    if task is not None:
        task.cancel()
        with contextlib.suppress(asyncio.CancelledError):
            await task
