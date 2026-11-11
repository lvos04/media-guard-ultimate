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
from mediaguard.mediaserver import servers as M
from mediaguard.mediaserver import service as S
from mediaguard.mediaserver import subtitles as T
from mediaguard.mediaserver.clients import MediaServerError, valid_key
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


AUTH = {"emby": "X-Emby-Token", "jellyfin": "Authorization: MediaBrowser", "plex": "X-Plex-Token"}


def _results(ctx: AppContext, server_id: int) -> dict[str, int]:
    row = ctx.db.fetch_one(
        "SELECT SUM(done_at IS NULL) AS open, SUM(result='ok') AS ok, "
        "SUM(result='transcode') AS transcode, SUM(result='failed') AS failed, "
        "SUM(result='not_found') AS not_found, SUM(nl_subtitles='visible') AS nl_visible, "
        "SUM(nl_subtitles='missing') AS nl_missing FROM mediaserver_server_results "
        "WHERE server_id=?", (server_id,))
    keys = ("open", "ok", "transcode", "failed", "not_found", "nl_visible", "nl_missing")
    return {k: int(row[k] or 0) for k in keys} if row else dict.fromkeys(keys, 0)


def server_entry(ctx: AppContext, srv: M.ServerConf) -> dict[str, Any]:
    """Eén element van ``servers[]`` (contract 1.3 §2.3 regel 2). Nooit sessies, titels,
    gebruikers of apparaten: alleen soort, adres, verbonden, kijkpauze ja/nee en aantallen."""
    contact = M.sget(ctx, srv, "contact", {}) or {}
    watching = M.sget(ctx, srv, "watching", {}) or {}
    return {
        "server_id": srv.id, "kind": srv.kind, "label": srv.label, "url": srv.url,
        "url_source": srv.url_source, "key_set": srv.key_set, "enabled": srv.enabled,
        "followed": srv.followed, "legacy": srv.legacy, "auth": AUTH.get(srv.kind),
        "connected": M.connected(ctx, srv),
        "contact": {"at": contact.get("at"), "ok": contact.get("ok"),
                    "last_ok_at": contact.get("last_ok_at"),
                    "error": contact.get("error")} if contact else None,
        "hold": {"active": bool(srv.followed and watching.get("active"))},
        "path_map": (M.sget(ctx, srv, "path_map", {}) or {}).get("map") or [],
        "results": _results(ctx, srv.id),
    }


def _top(ctx: AppContext) -> dict[str, Any]:
    """De bovenste velden beschrijven de eerste ingestelde server (contract 1.3 §2.3 regel 2):
    server 1 zoals in 1.2.2; staat die uit (losgemaakt), dan de eerste extra server."""
    cfg = S.configured(ctx)
    first = next((x for x in M.extras(ctx) if x.enabled), None) if cfg["kind"] == "off" else None
    if first is None:
        return {"kind": cfg["kind"], "label": S.LABEL.get(cfg["kind"], "Uit"), "url": cfg["url"],
                "url_source": cfg["source"], "key_set": cfg["key_set"],
                "connected": ctx.state_get("mediaserver_connected"),
                "path_map": (ctx.state_get("mediaserver_path_map", {}) or {}).get("map") or []}
    return {"kind": first.kind, "label": first.label, "url": first.url,
            "url_source": first.url_source, "key_set": first.key_set,
            "connected": M.sget(ctx, first, "connected"),
            "path_map": (M.sget(ctx, first, "path_map", {}) or {}).get("map") or []}


def _test_rows(ctx: AppContext) -> list[dict[str, Any]]:
    tests = ctx.db.fetch_dicts(
        "SELECT transaction_id, path, result, detail_nl, done_at, encoding_job_id FROM "
        "mediaserver_pending WHERE result IS NOT NULL AND result!='skipped' "
        "ORDER BY done_at DESC LIMIT 10")
    labels = {x.id: x.label for x in M.all_servers(ctx)}
    for t in tests:
        per = ctx.db.fetch_dicts(
            "SELECT server_id, result, detail_nl, nl_subtitles FROM mediaserver_server_results "
            "WHERE transaction_id=? AND result IS NOT NULL ORDER BY server_id",
            (t["transaction_id"],))
        t["servers"] = [{**x, "label": labels.get(int(x["server_id"]), "Mediaserver")}
                        for x in per]
    return tests


@router.get("/status")
def status(_: Principal = Depends(require_viewer),
           ctx: AppContext = Depends(get_app_context)) -> dict[str, Any]:
    top = _top(ctx)
    pending = ctx.db.fetch_one(
        "SELECT SUM(done_at IS NULL) AS open, SUM(result='ok') AS ok, "
        "SUM(result='transcode') AS transcode, SUM(result='failed') AS failed "
        "FROM mediaserver_pending")
    actions = ctx.db.fetch_dicts(
        "SELECT ts, kind, path, ok, detail_nl, server_id FROM mediaserver_actions "
        "ORDER BY id DESC LIMIT 20")
    tests = _test_rows(ctx)
    sub_bazarr = T.bazarr(ctx)
    sub_missing = T.missing(ctx, limit=0)["total"]
    return {
        "kind": top["kind"], "label": top["label"],
        "url": top["url"], "url_source": top["url_source"], "key_set": top["key_set"],
        "setting_kind": ctx.config.value("mediaserver.kind"),
        "setting_url": ctx.config.value("mediaserver.url"),
        "connected": top["connected"],
        "discovery": ctx.state_get("mediaserver_discovery"),
        "path_map": top["path_map"],
        # Δ-MG-DUAL: één element per ingestelde server (server 1 = de bestaande koppeling)
        "servers": [server_entry(ctx, x) for x in M.all_servers(ctx)],
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
    """Zonder ``server_id`` (of met 1): de bestaande koppeling, precies als in 1.2.2. Met een
    ``server_id`` vanaf 2: die extra server. ``add`` maakt een extra server (Δ-MG-DUAL) en raakt
    de bestaande koppeling nooit; ``remove`` verwijdert een extra server."""
    model_config = ConfigDict(extra="forbid")
    kind: Literal["auto", "emby", "jellyfin", "plex", "off"] | None = None
    url: str | None = Field(default=None, max_length=300)
    api_key: str | None = Field(default=None, max_length=300)
    clear_key: bool = False
    features: dict[str, Any] | None = None
    server_id: int | None = Field(default=None, ge=1)
    add: bool = False
    remove: bool = False
    label: str | None = Field(default=None, max_length=60)
    enabled: bool | None = None


def _set(ctx: AppContext, key: str, value: Any, actor: str) -> None:
    try:
        ctx.config.set("global", "", key, value, actor)
    except (KeyError, ValueError) as exc:
        raise HTTPException(422, str(exc)) from exc


def _url(raw: str) -> str:
    url = raw.strip().rstrip("/")
    if url and not url.startswith(("http://", "https://")):
        url = "http://" + url
    if url and (any(c.isspace() for c in url) or "@" in url.split("//", 1)[1].split("/", 1)[0]):
        raise HTTPException(422, "Ongeldig adres: geen spaties en geen gebruikersnaam in het "
                                 "adres.")
    return url


def _key(raw: str | None) -> str | None:
    key = (raw or "").strip()
    if key and not valid_key(key):
        raise HTTPException(422, "Ongeldige API-sleutel: alleen letters, cijfers en leestekens, "
                                 "zonder spaties, aanhalingstekens of komma's.")
    return key or None


def _features_set(ctx: AppContext, features: dict[str, Any] | None, actor: str) -> None:
    for key, value in (features or {}).items():
        if key not in FEATURE_KEYS:
            raise HTTPException(422, f"Onbekende instelling: {key}")
        _set(ctx, f"mediaserver.{key}", value, actor)


def _legacy_config(ctx: AppContext, body: ServerConfig, actor: str) -> bool:
    """De bestaande koppeling (server 1), zoals in 1.2.2. Geeft terug of er getest moet worden."""
    if body.remove:
        raise HTTPException(422, "De bestaande koppeling verwijder je niet: maak hem los (Uit). "
                                 "Adres en sleutel blijven dan bewaard.")
    kind = body.kind
    if body.enabled is False and kind is None:
        kind = "off"
    elif body.enabled is True and kind is None and ctx.config.value("mediaserver.kind") == "off":
        kind = ctx.state_get("mediaserver_kind_before_off") or "auto"
    if kind is not None:
        before = ctx.config.value("mediaserver.kind")
        if kind == "off" and before != "off":
            ctx.state_set("mediaserver_kind_before_off", before)
        _set(ctx, "mediaserver.kind", kind, actor)
        if kind == "off":
            M.drop_open_work(ctx, M.LEGACY_ID, "losgemaakt")
    if body.url is not None:
        _set(ctx, "mediaserver.url", _url(body.url), actor)
        ctx.state_set("mediaserver_path_map", {})
    key = _key(body.api_key)
    if body.clear_key:
        ctx.secrets.delete(S.SECRET_KEY)
    elif key:
        ctx.secrets.set(S.SECRET_KEY, key, actor)
    return bool(key or body.url is not None or kind)


def _extra_config(ctx: AppContext, body: ServerConfig, actor: str) -> tuple[int | None, bool]:
    """Een extra server toevoegen, wijzigen, los- of vastmaken, of verwijderen. Raakt de
    bestaande koppeling nooit (Δ-MG-DUAL: de Emby-koppeling onderbreekt op geen enkel moment)."""
    if body.kind in ("auto", "off"):
        raise HTTPException(422, "Kies voor een extra server Emby, Jellyfin (Jelly Leon) of Plex; "
                                 "uitzetten doe je met 'enabled'.")
    key = _key(body.api_key)
    if body.add:
        if body.server_id is not None or body.remove:
            raise HTTPException(422, "'add' gaat niet samen met 'server_id' of 'remove'.")
        if body.kind is None or not (body.url or "").strip():
            raise HTTPException(422, "Een nieuwe mediaserver heeft een soort en een adres nodig.")
        try:
            sid = M.add(ctx, body.kind, _url(body.url or ""), body.label, actor)
        except ValueError as exc:
            raise HTTPException(422, str(exc)) from exc
        if key:
            ctx.secrets.set(M.secret_name(sid), key, actor)
        return sid, True
    sid = int(body.server_id or 0)
    if not M.exists(ctx, sid):
        raise HTTPException(404, "Deze mediaserver bestaat niet (meer).")
    if body.remove:
        M.remove(ctx, sid)
        return None, False
    try:
        M.update(ctx, sid, kind=body.kind, url=_url(body.url) if body.url is not None else None,
                 label=body.label, enabled=body.enabled)
    except ValueError as exc:
        raise HTTPException(422, str(exc)) from exc
    if body.enabled is False:
        M.drop_open_work(ctx, sid, "losgemaakt")
    if body.clear_key:
        ctx.secrets.delete(M.secret_name(sid))
    elif key:
        ctx.secrets.set(M.secret_name(sid), key, actor)
    return sid, bool(key or body.url is not None or body.kind or body.enabled)


@router.post("/config")
async def set_config(body: ServerConfig, principal: Principal = Depends(require_admin),
                     ctx: AppContext = Depends(get_app_context)) -> dict[str, Any]:
    legacy = not body.add and body.server_id in (None, M.LEGACY_ID)
    target: int | None = M.LEGACY_ID
    if legacy:
        do_test = _legacy_config(ctx, body, principal.actor)
    else:
        target, do_test = _extra_config(ctx, body, principal.actor)
    _features_set(ctx, body.features, principal.actor)
    audit(ctx.db, actor=principal.actor, action="mediaserver.configured",
          target_type="settings", target_id="mediaserver" if legacy else f"mediaserver:{target}",
          new_value={"server_id": target if not body.remove else body.server_id,
                     "add": body.add, "remove": body.remove, "kind": body.kind,
                     "url": body.url, "label": body.label, "enabled": body.enabled,
                     "key_changed": bool(body.api_key) or body.clear_key,
                     "features": body.features})
    result = await S.test_connection(ctx, target) if do_test and target is not None else None
    return {"test": result, "server_id": target, "status": status(principal, ctx)}


@router.post("/test")
async def test(server_id: int | None = Query(None, ge=1),
               _: Principal = Depends(require_operator),
               ctx: AppContext = Depends(get_app_context)) -> dict[str, Any]:
    """Verbinding testen: zonder ``server_id`` de bestaande koppeling (zoals in 1.2.2)."""
    return await S.test_connection(ctx, server_id)


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
