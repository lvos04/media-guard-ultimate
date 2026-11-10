"""Is er een nieuwere MediaGuard? (verbeterlijst 23)

Core kijkt hooguit één keer per dag op GitHub (``releases/latest`` van de publieke repo) of
er een nieuwere versie is. Er wordt nooit iets vanzelf bijgewerkt: de eigenaar krijgt per
nieuwe versie één melding, een punt in 'Wacht op jou' en het veld in
``GET /api/v1/system/update`` (ook gelezen door de Android-app, docs/APP_API.md). Uit te
zetten met ``updates.check_enabled`` (Instellingen › Systeem & back-ups → Updates).

De laatste uitkomst staat in ``system_state`` (sleutel ``update_check``), dus een herstart
of herstartlus leidt nooit tot extra verzoeken aan GitHub.
"""

from __future__ import annotations

import asyncio
import logging
import re
from datetime import UTC, datetime, timedelta
from typing import Any

import httpx
from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field

from mediaguard import __version__
from mediaguard.audit import audit
from mediaguard.auth import Principal, require_operator, require_viewer
from mediaguard.context import AppContext, get_app_context
from mediaguard.db import utcnow
from mediaguard.settings_registry import REGISTRY, SettingDef

LOGGER = logging.getLogger(__name__)
router = APIRouter(prefix="/api/v1/system/update", tags=["system"])

REPO = "lvos04/media-guard-ultimate"
LATEST_URL = f"https://api.github.com/repos/{REPO}/releases/latest"
RELEASES_URL = f"https://github.com/{REPO}/releases"
IMAGE = "ghcr.io/lvos04/mediaguard-core"
STATE_KEY = "update_check"
INTERVAL = timedelta(hours=24)   # hooguit één verzoek per dag, ook als het mislukt
MANUAL_MIN = timedelta(minutes=1)  # 'Nu controleren' niet vaker dan eens per minuut
LOOP_SLEEP_S = 3600
SETTING = "updates.check_enabled"
_APK = re.compile(r"^mediaguard-android-(\d+(?:\.\d+)*)\.apk$")


def register_settings() -> None:
    REGISTRY.register(SettingDef(
        SETTING, "bool", True, "Onderhoud", "Melden als er een nieuwe versie is",
        "Notify about new versions",
        help_nl="Kijkt één keer per dag op GitHub of er een nieuwe MediaGuard-versie is en "
                "meldt dat. Er wordt nooit vanzelf bijgewerkt: dat doe je zelf in TrueNAS.",
        help_en="Checks GitHub once a day for a new MediaGuard version. Nothing is updated "
                "automatically."))


# -- versies ----------------------------------------------------------------------------------
def parse_version(text: str | None) -> tuple[int, ...] | None:
    """'v1.2.10' → (1, 2, 10). Voorversies ('1.3.0-rc1') en onzin → None."""
    if not text:
        return None
    core = text.strip().removeprefix("v").removeprefix("V")
    if not re.fullmatch(r"\d+(\.\d+){0,3}", core):
        return None
    return tuple(int(p) for p in core.split("."))


def is_newer(candidate: str | None, current: str = __version__) -> bool:
    a, b = parse_version(candidate), parse_version(current)
    if a is None or b is None:
        return False
    width = max(len(a), len(b))
    return a + (0,) * (width - len(a)) > b + (0,) * (width - len(b))


def release_info(payload: Any) -> dict[str, Any]:
    """Het deel van een GitHub-release dat MediaGuard gebruikt."""
    if not isinstance(payload, dict):
        raise ValueError("onverwacht antwoord van GitHub")
    tag = str(payload.get("tag_name") or "")
    version = tag.strip().removeprefix("v").removeprefix("V")
    if payload.get("draft") or payload.get("prerelease") or parse_version(version) is None:
        raise ValueError(f"geen bruikbare release: {tag or '(zonder tag)'}")
    android = None
    for asset in payload.get("assets") or []:
        m = _APK.match(str(asset.get("name") or ""))
        url = str(asset.get("browser_download_url") or "")
        if m and url.startswith("https://"):
            android = {"latest": m.group(1), "url": url}
            break
    url = str(payload.get("html_url") or "")
    return {"latest": version,
            "url": url if url.startswith("https://") else f"{RELEASES_URL}/tag/{tag}",
            "published_at": payload.get("published_at"), "android": android}


# -- controle ---------------------------------------------------------------------------------
def _parse_ts(value: Any) -> datetime | None:
    try:
        ts = datetime.fromisoformat(str(value))
    except (TypeError, ValueError):
        return None
    return ts if ts.tzinfo else ts.replace(tzinfo=UTC)


def state(ctx: AppContext) -> dict[str, Any]:
    raw = ctx.state_get(STATE_KEY, {}) or {}
    return raw if isinstance(raw, dict) else {}


def due(ctx: AppContext, now: datetime | None = None) -> bool:
    if not ctx.config.value(SETTING):
        return False
    last = _parse_ts(state(ctx).get("attempted_at"))
    now = now or datetime.now(UTC)
    return last is None or now - last >= INTERVAL


async def fetch_latest(client: httpx.AsyncClient | None = None) -> dict[str, Any]:
    headers = {"Accept": "application/vnd.github+json",
               "User-Agent": f"MediaGuard/{__version__} (+{RELEASES_URL})",
               "X-GitHub-Api-Version": "2022-11-28"}
    own = client is None
    client = client or httpx.AsyncClient(timeout=httpx.Timeout(15, connect=8))
    try:
        resp = await client.get(LATEST_URL, headers=headers)
    finally:
        if own:
            await client.aclose()
    if resp.status_code == 404:
        raise ValueError("nog geen release gevonden op GitHub")
    if resp.status_code in (403, 429):
        raise ValueError("GitHub weigert tijdelijk (limiet bereikt)")
    resp.raise_for_status()
    return release_info(resp.json())


async def check(ctx: AppContext, *, client: httpx.AsyncClient | None = None) -> dict[str, Any]:
    """Eén controle; schrijft de uitkomst weg en meldt een nieuwe versie één keer."""
    prev = state(ctx)
    new: dict[str, Any] = {k: prev[k] for k in ("latest", "url", "published_at", "android",
                                                 "checked_at", "notified", "dismissed")
                           if k in prev}
    new["attempted_at"] = utcnow()
    try:
        info = await fetch_latest(client)
    except (httpx.HTTPError, ValueError) as exc:
        if isinstance(exc, httpx.HTTPStatusError):
            reason = f"GitHub gaf foutcode {exc.response.status_code}"
        elif isinstance(exc, httpx.HTTPError):
            reason = "GitHub niet bereikbaar"
        else:
            reason = str(exc)
        new["error_nl"] = f"Controleren lukte niet ({reason}). Morgen opnieuw."
        ctx.state_set(STATE_KEY, new)
        LOGGER.info("updatecontrole mislukt: %s", reason)
        return view(ctx)
    new.update(info, checked_at=new["attempted_at"])
    new.pop("error_nl", None)
    ctx.state_set(STATE_KEY, new)
    _maybe_notify(ctx)
    return view(ctx)


def resolve_if_current(ctx: AppContext) -> None:
    """Na het bijwerken: de melding 'MediaGuard x is uit' is dan afgehandeld."""
    from mediaguard.api.system import resolve_notifications

    st = state(ctx)
    notified = st.get("notified")
    if notified and not is_newer(notified):
        resolve_notifications(ctx, f"update:{notified}")
        ctx.state_set(STATE_KEY, {k: v for k, v in st.items() if k != "notified"})


def _maybe_notify(ctx: AppContext) -> None:
    from mediaguard.api.system import notify, resolve_notifications

    resolve_if_current(ctx)
    st = state(ctx)
    latest = st.get("latest")
    if not is_newer(latest) or not ctx.config.value(SETTING):
        return
    if st.get("notified") == latest:
        return
    notify(ctx, "info", f"MediaGuard {latest} is uit",
           f"Je draait {__version__}. Zet in TrueNAS de tag op {latest}-aio om bij te werken "
           "(Apps → mediaguard → Edit → Image → Tag). Maak eerst een snapshot van je appdata; "
           "terugdraaien staat in de README (10.2).",
           link="/systeem#updates", dedupe_key=f"update:{latest}")
    # een melding over een oudere versie is nu achterhaald
    old = st.get("notified")
    if old and old != latest:
        resolve_notifications(ctx, f"update:{old}")
    ctx.state_set(STATE_KEY, {**st, "notified": latest})


def view(ctx: AppContext) -> dict[str, Any]:
    """Antwoord van GET /api/v1/system/update (vorm: docs/APP_API.md)."""
    st = state(ctx)
    enabled = bool(ctx.config.value(SETTING))
    latest = st.get("latest")
    available = enabled and is_newer(latest)
    attempted = _parse_ts(st.get("attempted_at"))
    android = st.get("android") if isinstance(st.get("android"), dict) else None
    return {
        "enabled": enabled,
        "current": __version__,
        "latest": latest if enabled else None,
        "update_available": available,
        "url": st.get("url") if enabled else None,
        "published_at": st.get("published_at") if enabled else None,
        "image": f"{IMAGE}:{latest}-aio" if available else None,
        "checked_at": st.get("checked_at"),
        "next_check_at": ((attempted + INTERVAL).isoformat(timespec="seconds")
                          if enabled and attempted else None),
        "error_nl": st.get("error_nl") if enabled else None,
        "dismissed": bool(available and st.get("dismissed") == latest),
        "android": android if enabled and android else None,
    }


def attention_items(ctx: AppContext) -> list[dict[str, Any]]:
    """Punt voor 'Wacht op jou' zolang er een nieuwere versie is (tot je hem verbergt)."""
    v = view(ctx)
    if not v["update_available"] or v["dismissed"]:
        return []
    return [{"latest": v["latest"], "title_nl": f"MediaGuard {v['latest']} is uit",
             "detail_nl": f"Je draait {v['current']}. Bijwerken doe je zelf in TrueNAS: tag "
                          f"{v['latest']}-aio. Maak eerst een snapshot van je appdata."}]


# -- achtergrond ------------------------------------------------------------------------------
async def _loop(ctx: AppContext) -> None:
    await asyncio.sleep(120)  # eerst rustig opstarten
    from mediaguard import taskwatch

    while True:
        try:
            if due(ctx):
                await check(ctx)  # netwerkfouten vangt check() zelf af
            taskwatch.ok(ctx, "update_check")
        except Exception as exc:  # noqa: BLE001 - de controle mag nooit de lus stoppen
            LOGGER.exception("updatecontrole mislukt")
            taskwatch.fail(ctx, "update_check", exc)
        await asyncio.sleep(LOOP_SLEEP_S)


async def startup(ctx: AppContext) -> None:
    if ctx.settings.role not in ("core", "all"):
        return
    try:
        resolve_if_current(ctx)
    except Exception:  # noqa: BLE001 - mag het opstarten nooit blokkeren
        LOGGER.exception("updatemelding afhandelen mislukt")
    if ctx.settings.start_background:
        from mediaguard import taskwatch

        ctx.services["update_check_task"] = taskwatch.spawn(
            ctx, "update_check", _loop, label_nl="Nieuwe versie controleren", every=LOOP_SLEEP_S)


async def shutdown(ctx: AppContext) -> None:
    task = ctx.services.pop("update_check_task", None)
    if task is not None:
        task.cancel()


# -- API --------------------------------------------------------------------------------------
@router.get("")
def get_update(_: Principal = Depends(require_viewer),
               ctx: AppContext = Depends(get_app_context)) -> dict[str, Any]:
    return view(ctx)


@router.post("/check")
async def check_now(principal: Principal = Depends(require_operator),
                    ctx: AppContext = Depends(get_app_context)) -> dict[str, Any]:
    if not ctx.config.value(SETTING):
        raise HTTPException(409, "Controleren op nieuwe versies staat uit")
    last = _parse_ts(state(ctx).get("attempted_at"))
    if last and datetime.now(UTC) - last < MANUAL_MIN:
        return view(ctx)
    audit(ctx.db, actor=principal.actor, action="update.check")
    return await check(ctx)


class Dismiss(BaseModel):
    version: str = Field(min_length=1, max_length=32)


@router.post("/dismiss")
def dismiss(body: Dismiss, principal: Principal = Depends(require_operator),
            ctx: AppContext = Depends(get_app_context)) -> dict[str, Any]:
    """'Verbergen tot de volgende versie': het punt in 'Wacht op jou' verdwijnt."""
    st = state(ctx)
    if body.version != st.get("latest"):
        raise HTTPException(409, "Dat is niet de nieuwste versie")
    ctx.state_set(STATE_KEY, {**st, "dismissed": body.version})
    audit(ctx.db, actor=principal.actor, action="update.dismiss", target_type="version",
          target_id=body.version)
    return view(ctx)
