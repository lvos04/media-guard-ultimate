"""API voor de Android-app (docs/APP_API.md §4): compacte status, dagoverzicht, bediening en
koppelen via QR. Werkt met een API-sleutel (rol operator) of de gewone sessie."""

from __future__ import annotations

import asyncio
import contextlib
import logging
import secrets
from datetime import UTC, date, datetime, timedelta
from typing import Any, Literal
from zoneinfo import ZoneInfo

from fastapi import APIRouter, Depends, HTTPException, Query, Request
from pydantic import BaseModel, Field

from mediaguard import __version__
from mediaguard.audit import audit
from mediaguard.auth import (
    ROLE_RANK,
    Principal,
    require_admin,
    require_operator,
    require_viewer,
)
from mediaguard.context import AppContext, get_app_context
from mediaguard.db import loads, utcnow
from mediaguard.settings_registry import REGISTRY, SettingDef

LOGGER = logging.getLogger(__name__)
router = APIRouter(prefix="/api/v1/app", tags=["app"])
TZ = ZoneInfo("Europe/Amsterdam")
APP_KEY_NAME = "android-app"
RECENT_LABEL = "Laatste 24 uur"
PHONE_CHANNEL_NAME = "Telefoon (ntfy)"


def register_settings() -> None:
    REGISTRY.register(
        SettingDef("app.public_url", "string", "", "Koppelingen", "Adres voor de app",
                   "Public URL for the app",
                   help_nl="Het adres waarop de Android-app MediaGuard bereikt, bijvoorbeeld "
                           "je Cloudflare-tunnel (https://mediaguard.voorbeeld.nl). Leeg = het "
                           "adres waarmee je deze pagina nu opent. Wordt in de QR-code gezet "
                           "bij 'App koppelen'."),
    )


def _name(path: Any) -> str:
    return str(path or "").rsplit("/", 1)[-1]


def _size(blob: Any, key: str = "size") -> int:
    data = loads(blob, {}) if isinstance(blob, str) else (blob or {})
    try:
        return int((data or {}).get(key) or 0)
    except (TypeError, ValueError):
        return 0


# -- status ----------------------------------------------------------------------------------
def _clean_reason(reason: Any) -> str | None:
    text = str(reason or "").strip()
    for prefix in ("automatisch:", "MediaGuard:", "tijdelijk:"):
        if text.startswith(prefix):
            text = text[len(prefix):].strip()
    return text or None


async def _encoder(ctx: AppContext, stop_active: bool = False) -> dict[str, Any]:
    """Banen met echte bezetting (bezig/plekken) en lopende encodes met baan en ETA, uit de
    engine-API ``GET /api/workers``; de titels en de jobstatus komen uit core."""
    from mediaguard.encoding.client import EncoderError, get_encoder_client

    lanes: list[dict[str, Any]] = []
    engine_jobs: dict[str, dict[str, Any]] = {}
    reachable = True
    try:
        workers = await asyncio.wait_for(get_encoder_client(ctx).engine("GET", "/workers"), 6)
        rows_w = [w for w in (workers if isinstance(workers, list) else []) if isinstance(w, dict)]
        for w in sorted(rows_w, key=lambda w: str(w.get("baan")) != "gpu"):
            lane = str(w.get("baan") or "")
            active = [j for j in (w.get("actieve_jobs") or []) if isinstance(j, dict)]
            paused = bool(w.get("gepauzeerd"))
            lanes.append({"lane": lane, "running": len(active),
                          "slots": int(w.get("aantal") or 0), "paused": paused,
                          "reason_nl": (_clean_reason(w.get("pauze_reden")) or "gepauzeerd")
                          if paused else None})
            for j in active:
                engine_jobs[str(j.get("job_id"))] = {
                    "lane": lane, "percent": j.get("percent"), "eta": j.get("eta_seconden"),
                    "path": j.get("bestand")}
    except (EncoderError, TimeoutError, OSError, ValueError, TypeError):
        reachable = False
    rows = ctx.db.fetch_dicts(
        "SELECT id, encoder_job_id, source_path, status, progress, updated_at FROM "
        "encoding_jobs WHERE status IN ('running','verifying') ORDER BY id")

    def phase(verifying: bool) -> str:
        if stop_active:
            return "Gepauzeerd (noodstop)"
        return "Controleren" if verifying else "Encoderen"

    running: list[dict[str, Any]] = []
    seen: set[str] = set()
    for r in rows:
        eid = str(r["encoder_job_id"] or "")
        seen.add(eid)
        e = engine_jobs.get(eid, {})
        pct = e.get("percent")
        percent = float(pct) if isinstance(pct, (int, float)) else float(r["progress"] or 0) * 100
        eta = e.get("eta")
        running.append({"job_id": r["id"], "engine_job_id": r["encoder_job_id"],
                        "title": _name(r["source_path"]), "lane": e.get("lane"),
                        "percent": round(percent, 1),
                        "eta_seconds": int(eta) if isinstance(eta, (int, float)) and eta > 0
                        and not stop_active else None,
                        "phase_nl": phase(r["status"] == "verifying")})
    # jobs die de engine draait maar core (nog) niet als lopend kent: toch tonen, anders
    # klopt "2 van 4 bezig" niet met de lijst eronder
    for eid, e in engine_jobs.items():
        if eid in seen:
            continue
        eta = e.get("eta")
        pct = e.get("percent")
        running.append({"job_id": None, "engine_job_id": eid, "title": _name(e.get("path")),
                        "lane": e.get("lane"),
                        "percent": round(float(pct), 1) if isinstance(pct, (int, float))
                        else None,
                        "eta_seconds": int(eta) if isinstance(eta, (int, float)) and eta > 0
                        and not stop_active else None,
                        "phase_nl": phase(False)})
    queued = ctx.db.fetch_one("SELECT COUNT(*) AS n FROM encoding_jobs WHERE status IN "
                              "('submitted','queued')")
    return {"reachable": reachable, "lanes": lanes, "running": running,
            "queued": int(queued["n"] if queued else 0)}


async def _downloads(ctx: AppContext) -> dict[str, Any]:
    try:
        from mediaguard.activity.api import activity_cached

        act = await asyncio.wait_for(activity_cached(ctx), 8)
    except Exception:  # noqa: BLE001 - downloads zijn extra
        return {"active": 0, "clients_ok": None, "items": []}
    items = []
    for d in (act.get("downloads") or [])[:20]:
        items.append({"title": d.get("title") or d.get("name"), "client": d.get("client"),
                      "percent": d.get("progress"), "eta_seconds": d.get("eta_seconds"),
                      "size": d.get("size"), "status_nl": d.get("status_nl")})
    summary = act.get("summary") or {}
    return {"active": int(summary.get("downloads") or len(items)),
            "clients_ok": summary.get("download_clients_ok"), "items": items}


def _savings(ctx: AppContext) -> int:
    total = 0
    for r in ctx.db.fetch_all("SELECT source_snapshot_json, staged_output_json FROM "
                              "encoding_jobs WHERE status='committed'"):
        total += max(0, _size(r["source_snapshot_json"]) - _size(r["staged_output_json"]))
    return total


def _allowed(role: str, needed: Any) -> bool:
    return ROLE_RANK.get(role, 0) >= ROLE_RANK.get(str(needed or "operator"), 2)


def _app_actions(actions: list[dict[str, Any]], role: str) -> tuple[list[dict[str, Any]], int]:
    """Knoppen uit 'Wacht op jou' die deze sleutel mag gebruiken (dezelfde endpoints als de
    web). Wat alleen een beheerder mag, telt mee als 'in de web'."""
    out, hidden = [], 0
    for a in actions or []:
        if str(a.get("method") or "post").lower() != "post" or not str(
                a.get("path") or "").startswith("/"):
            continue
        if not _allowed(role, a.get("role")):
            hidden += 1
            continue
        out.append({"label_nl": a.get("label_nl"), "method": "post", "path": a.get("path"),
                    "body": a.get("body") or {}, "confirm_nl": a.get("confirm_nl"),
                    "primary": bool(a.get("primary"))})
    return out, hidden


def _app_item(item: dict[str, Any], role: str) -> dict[str, Any]:
    from mediaguard.textfmt import plain_nl

    actions, hidden = _app_actions(item.get("actions") or [], role)
    rows = []
    for r in (item.get("rows") or [])[:5]:
        row_actions, row_hidden = _app_actions(r.get("actions") or [], role)
        hidden += row_hidden
        # zelfde opmaak als de web (plainText): geen ruwe ISO-tijden of dicts op de telefoon
        rows.append({"id": str(r.get("id")), "title": r.get("title"),
                     "detail": plain_nl(r.get("detail")) or None, "href": r.get("href"),
                     "actions": row_actions})
    return {"key": item.get("key"), "title_nl": item.get("title_nl"),
            "detail_nl": plain_nl(item.get("detail_nl")) or None,
            "severity": item.get("severity"),
            "href": item.get("href"), "count": int(item.get("count") or 1),
            "actions": actions, "rows": rows,
            "more_rows": max(0, len(item.get("rows") or []) - len(rows)),
            "admin_actions": hidden}


@router.get("/status")
async def status(principal: Principal = Depends(require_viewer),
                 ctx: AppContext = Depends(get_app_context)) -> dict[str, Any]:
    from mediaguard import attention

    stop = ctx.state_get("emergency_stop", {"active": False}) or {}
    active_stop = bool(stop.get("active"))
    # attention.collect: dezelfde lijst als de web, inclusief problemen met de AV1-engine
    items, encoder, downloads = await asyncio.gather(
        attention.collect(ctx), _encoder(ctx, active_stop), _downloads(ctx))
    items = attention.for_role(items, principal.role)  # namen alleen voor beheerders
    storage: dict[str, Any] = {}
    with contextlib.suppress(Exception):
        from mediaguard import storage_forecast

        fc = storage_forecast.forecast(ctx)
        storage = {"free_bytes": min((d.get("free") or 0) for d in fc.get("disks") or [{}]),
                   "days_left_nl": fc.get("soonest_nl")}
    return {"version": __version__, "generated_at": utcnow(),
            "emergency_stop": {"active": active_stop, "reason": stop.get("reason"),
                               "since": stop.get("since") if active_stop else None},
            "attention": {"count": len(items),
                          "errors": sum(1 for i in items if i.get("severity") == "error"),
                          "items": [_app_item(i, principal.role) for i in items[:20]]},
            "encoder": encoder, "downloads": downloads, "storage": storage,
            "savings_bytes": _savings(ctx)}


# -- dagoverzicht -----------------------------------------------------------------------------
def _bounds(day: date) -> tuple[str, str]:
    start = datetime(day.year, day.month, day.day, tzinfo=TZ).astimezone(UTC)
    end = start + timedelta(days=1)
    return (start.strftime("%Y-%m-%dT%H:%M:%SZ"), end.strftime("%Y-%m-%dT%H:%M:%SZ"))


def _replaced(ctx: AppContext, start: str, end: str) -> list[dict[str, Any]]:
    out = []
    for r in ctx.db.fetch_dicts(
            "SELECT id, source_path, source_snapshot_json, staged_output_json, "
            "core_verification_json, updated_at FROM encoding_jobs WHERE status='committed' "
            "AND updated_at>=? AND updated_at<? ORDER BY updated_at DESC", (start, end)):
        src, dst = _size(r["source_snapshot_json"]), _size(r["staged_output_json"])
        cv = loads(r["core_verification_json"], {}) or {}
        out.append({"job_id": r["id"], "title": _name(r["source_path"]), "from_size": src,
                    "to_size": dst, "saved": max(0, src - dst),
                    "vmaf": cv.get("vmaf") or (loads(r["staged_output_json"], {}) or {})
                    .get("vmaf"), "at": r["updated_at"]})
    return out


def _requests(ctx: AppContext, start: str, end: str) -> list[dict[str, Any]]:
    from mediaguard.delivery.store import STAGE_NL, public_state

    rows = ctx.db.fetch_dicts(
        "SELECT * FROM delivery_items WHERE source='seerr' AND ((requested_at>=? AND "
        "requested_at<?) OR (delivered_at>=? AND delivered_at<?)) ORDER BY updated_at DESC",
        (start, end, start, end))
    return [{"title": r["title"], "media_type": r["media_type"],
             "requested_at": r["requested_at"], "delivered_at": r["delivered_at"],
             "delivery_state": public_state(r),
             "delivery_state_nl": STAGE_NL.get(r["stage"], r["stage"])} for r in rows]


def _window(ctx: AppContext, start: str, end: str, label: str) -> dict[str, Any]:
    """Alles wat er tussen ``start`` en ``end`` (UTC) gebeurde, voor dag of laatste 24 uur."""
    from mediaguard import attention, storage_ledger

    imports = storage_ledger.imports(ctx, start, end)
    replaced = _replaced(ctx, start, end)
    downloaded = [{"title": i["title"], "label": i.get("episode"), "kind": i["kind"],
                   "kind_nl": i["kind_nl"], "size": i.get("size"),
                   "quality": i.get("quality"), "source_nl": i.get("source_nl"),
                   "at": i["at"], "href": i.get("href")} for i in imports]
    upgrades = [{"title": i["title"], "label": i.get("episode"),
                 "reason_nl": i.get("waarom") or "Upgrade", "at": i["at"]}
                for i in imports if i["kind"] == "upgrade"]
    added = sum(int(i.get("net_bytes") or 0) for i in imports)
    saved = sum(r["saved"] for r in replaced)
    eps = sum(1 for i in imports if i["media_kind"] == "episode")
    movies = sum(1 for i in imports if i["media_kind"] == "movie")
    parts = []
    if eps or movies:
        parts.append(", ".join(x for x in (
            f"{eps} aflevering{'en' if eps != 1 else ''}" if eps else "",
            f"{movies} film{'s' if movies != 1 else ''}" if movies else "") if x) + " erbij")
    if replaced:
        parts.append(f"{len(replaced)} omgezet naar AV1 ({storage_ledger.gb(saved)} bespaard)")
    quiet = "rustig, niets nieuws" if label == RECENT_LABEL else "rustige dag, niets nieuws"
    headline = f"{label}: " + ("; ".join(parts) if parts else quiet)
    failed = ctx.db.fetch_one(
        "SELECT COUNT(*) AS n FROM encoding_jobs WHERE status='failed' AND updated_at>=? AND "
        "updated_at<?", (start, end))
    items = attention.collect_local(ctx)
    return {"label_nl": label, "headline_nl": headline, "replaced": replaced,
            "downloaded": downloaded, "upgrades": upgrades,
            "requests": _requests(ctx, start, end),
            "attention": [{"title_nl": i.get("title_nl"), "severity": i.get("severity"),
                           "href": i.get("href")} for i in items],
            "storage": {"net_bytes": added - saved, "added_bytes": added,
                        "saved_bytes": saved, "unexplained_bytes": None},
            "encoder": {"done": len(replaced), "failed": int(failed["n"] if failed else 0),
                        "saved_bytes": saved}}


def _unexplained_between(ctx: AppContext, start_dt: datetime,
                         end_dt: datetime) -> int | None:
    """'Niet verklaard' voor een afgelopen periode: de gemeten daling van de vrije ruimte
    (uurlijkse metingen van het opslaglogboek) min de bekende oorzaken over precies hetzelfde
    venster, zoals ``storage_ledger.ledger`` dat voor 'nu' doet. None = niet gemeten."""
    from mediaguard import storage_ledger as sl

    if end_dt > datetime.now(UTC):
        return None
    a, b = sl.snapshot_near(ctx, start_dt), sl.snapshot_near(ctx, end_dt)
    if not a or not b:
        return None
    a_at, b_at = sl._parse(a.get("at")), sl._parse(b.get("at"))
    if a_at is None or b_at is None or b_at <= a_at:
        return None
    w = sl._parts(ctx, sl._iso(a_at), sl._iso(b_at))
    used = int(a["free"]) - int(b["free"])
    if a.get("downloads") is not None and b.get("downloads") is not None:
        downloads = int(b["downloads"]) - int(a["downloads"])
    else:
        downloads = -int(w["janitor_freed"])
    explained = (w["added"] + w["upgrades_net"] - w["av1_saved"] + w["q_in"] - w["q_out"]
                 + downloads)
    return int(used - explained)


@router.get("/summary")
def summary(date_: str | None = Query(None, alias="date"),
            _: Principal = Depends(require_viewer),
            ctx: AppContext = Depends(get_app_context)) -> dict[str, Any]:
    try:
        day = (date.fromisoformat(date_) if date_
               else (datetime.now(TZ) - timedelta(days=1)).date())
    except ValueError as exc:
        raise HTTPException(422, "Datum als JJJJ-MM-DD") from exc
    start, end = _bounds(day)
    label = "Vandaag" if day == datetime.now(TZ).date() else (
        "Gisteren" if day == (datetime.now(TZ) - timedelta(days=1)).date()
        else day.strftime("%d-%m-%Y"))
    data = {"date": day.isoformat(), **_window(ctx, start, end, label)}
    with contextlib.suppress(Exception):
        start_dt = datetime(day.year, day.month, day.day, tzinfo=TZ).astimezone(UTC)
        data["storage"]["unexplained_bytes"] = _unexplained_between(
            ctx, start_dt, start_dt + timedelta(days=1))
    return data


@router.get("/recent")
def recent(_: Principal = Depends(require_viewer),
           ctx: AppContext = Depends(get_app_context)) -> dict[str, Any]:
    """Rollend venster 'Laatste 24 uur' (tot nu), voor Overzicht en Ruimte in de app."""
    now = datetime.now(UTC)
    start = now - timedelta(hours=24)
    fmt = "%Y-%m-%dT%H:%M:%SZ"
    data = {"window": "24h", "from": start.strftime(fmt), "to": now.strftime(fmt),
            "date": None,
            **_window(ctx, start.strftime(fmt), (now + timedelta(seconds=1)).strftime(fmt),
                      RECENT_LABEL)}
    with contextlib.suppress(Exception):
        from mediaguard import storage_ledger

        recon = storage_ledger.ledger(ctx, "24h", now)["reconciliation"]
        if recon.get("unexplained") is not None:
            data["storage"]["unexplained_bytes"] = int(recon["unexplained"])
    return data


# -- bediening -------------------------------------------------------------------------------
class ControlBody(BaseModel):
    action: Literal["emergency_stop_on", "emergency_stop_off", "pause_lane", "resume_lane",
                    "run_autopilot", "retry_job", "search_item", "test_notification"]
    params: dict[str, Any] = Field(default_factory=dict)
    confirm: bool = False


CONFIRM_ACTIONS = {"emergency_stop_on", "emergency_stop_off", "pause_lane"}


@router.post("/control")
async def control(body: ControlBody, principal: Principal = Depends(require_operator),
                  ctx: AppContext = Depends(get_app_context)) -> dict[str, Any]:
    if body.action in CONFIRM_ACTIONS and not body.confirm:
        raise HTTPException(409, {"code": "CONFIRM_REQUIRED",
                                  "message": "Bevestig deze actie eerst"})
    p = body.params
    if body.action in ("emergency_stop_on", "emergency_stop_off"):
        from mediaguard.api.system import EmergencyBody, emergency_stop

        active = body.action == "emergency_stop_on"
        result = emergency_stop(EmergencyBody(active=active, reason=str(
            p.get("reason") or ("Via de app" if active else ""))[:500]), principal, ctx)
        msg = "Noodstop staat aan" if active else (result.get("resumed_nl") or
                                                   "Noodstop opgeheven")
        return {"ok": True, "message_nl": msg, "result": result}
    if body.action in ("pause_lane", "resume_lane"):
        lane = str(p.get("lane") or "")
        if lane not in ("cpu", "gpu"):
            raise HTTPException(422, "Kies baan 'cpu' of 'gpu'")
        from mediaguard.encoding.client import EncoderError, get_encoder_client

        pause = body.action == "pause_lane"
        try:
            await get_encoder_client(ctx).engine(
                "POST", f"/workers/{lane}/{'pauzeren' if pause else 'hervatten'}")
        except EncoderError as exc:
            raise HTTPException(502, f"AV1-engine weigert: {exc}") from exc
        audit(ctx.db, actor=principal.actor, action=f"app.{body.action}", target_type="lane",
              target_id=lane)
        return {"ok": True, "message_nl": f"{lane.upper()}-baan "
                f"{'gepauzeerd' if pause else 'hervat'}"}
    if body.action == "run_autopilot":
        from mediaguard.encoding.autopilot import run_now

        result = await run_now(principal, ctx)
        return {"ok": True, "message_nl": "AV1-autopilot heeft een ronde gedraaid",
                "result": result}
    if body.action == "retry_job":
        return await _retry(ctx, principal, p)
    if body.action == "test_notification":
        return await _test_notification(ctx, principal, p)
    item_id = p.get("item_id") or p.get("wanted_id")
    if item_id is None:
        raise HTTPException(422, "Geef item_id (ontbrekend item) mee")
    from mediaguard.wanted.api import search_now

    result = await search_now(int(item_id), None, principal, ctx)
    return {"ok": True, "message_nl": "Zoekopdracht gestart", "result": result}


async def _retry(ctx: AppContext, principal: Principal, p: dict[str, Any]) -> dict[str, Any]:
    from mediaguard.encoding.client import EncoderError, get_encoder_client

    job_id = p.get("job_id")
    row = ctx.db.fetch_one("SELECT id, encoder_job_id, status FROM encoding_jobs WHERE id=? OR "
                           "encoder_job_id=? ORDER BY id DESC LIMIT 1",
                           (job_id or -1, str(job_id or "")))
    if row is None or not row["encoder_job_id"]:
        raise HTTPException(404, "Job niet gevonden")
    try:
        await get_encoder_client(ctx).engine("POST", f"/jobs/{row['encoder_job_id']}/opnieuw")
    except EncoderError as exc:
        raise HTTPException(409, f"Opnieuw proberen lukt niet: {exc}") from exc
    ctx.db.execute("UPDATE encoding_jobs SET status='queued', error=NULL WHERE id=? AND "
                   "status IN ('failed','rejected')", (row["id"],))
    audit(ctx.db, actor=principal.actor, action="app.retry_job", target_type="encoding_job",
          target_id=str(row["id"]))
    return {"ok": True, "message_nl": "Encode wordt opnieuw geprobeerd"}


def _is_app(principal: Principal) -> bool:
    """De app-sleutel zelf (door een beheerder aangemaakt bij het koppelen)."""
    return principal.machine and principal.username.startswith(f"apikey:{APP_KEY_NAME}")


def _phone_access(principal: Principal) -> bool:
    """Telefoonkanaal aanzetten, testen of het onderwerp zien: alleen de app-sleutel en
    beheerders. Kanaalbeheer is admin-only; een andere operator (of een gelekte
    operator-sleutel) mag geen meldingen aanzetten of het geheime onderwerp lezen."""
    return principal.role == "admin" or _is_app(principal)


def _require_phone_access(principal: Principal) -> None:
    if not _phone_access(principal):
        raise HTTPException(403, "Alleen de gekoppelde app of een beheerder kan het "
                                 "telefoonkanaal gebruiken")


async def _test_notification(ctx: AppContext, principal: Principal,
                             p: dict[str, Any]) -> dict[str, Any]:
    """Testbericht naar het telefoonkanaal (ntfy) van de app, meteen (niet via de wachtrij)."""
    from mediaguard.notify import service

    _require_phone_access(principal)
    channel = _phone_channel(ctx, str(p.get("topic") or "") or None,
                             auto_only=principal.role != "admin")
    if channel is None:
        raise HTTPException(409, "Er is nog geen kanaal voor je telefoon. Koppel de app "
                                 "opnieuw.")
    result = await service.send_test(ctx, int(channel["id"]))
    audit(ctx.db, actor=principal.actor, action="app.test_notification", target_type="notify",
          target_id=str(channel["id"]), reason=None if result.get("ok") else
          str(result.get("error") or "")[:300])
    if not result.get("ok"):
        return {"ok": False, "ntfy": _ntfy_info(channel),
                "message_nl": f"Testbericht niet verstuurd: {result.get('error') or 'onbekend'}"}
    note = "" if channel.get("enabled") else ". Let op: dit kanaal staat uit in MediaGuard"
    return {"ok": True, "ntfy": _ntfy_info(channel),
            "message_nl": f"Testbericht verstuurd{note}"}


# -- koppelen --------------------------------------------------------------------------------
def _ntfy_info(channel: dict[str, Any]) -> dict[str, Any]:
    cfg = channel.get("config") or {}
    return {"server": cfg.get("server") or "https://ntfy.sh", "topic": cfg.get("topic"),
            "enabled": bool(channel.get("enabled"))}


def _phone_channel(ctx: AppContext, topic: str | None = None, *,
                   auto_only: bool = False) -> dict[str, Any] | None:
    """Het ntfy-kanaal van de telefoon: het kanaal met dit onderwerp, anders het automatisch
    aangemaakte, anders het eerste ntfy-kanaal met een onderwerp. ``auto_only``: alleen het
    automatisch aangemaakte telefoonkanaal, nooit een kanaal dat de beheerder zelf instelde."""
    from mediaguard.notify import service

    ntfy = [c for c in service.list_channels(ctx)
            if c["kind"] == "ntfy" and (c.get("config") or {}).get("topic")
            and (c.get("auto_created") or not auto_only)]
    if topic:
        for c in ntfy:
            if c["config"].get("topic") == topic:
                return c
    auto = [c for c in ntfy if c.get("auto_created")]
    return (auto or ntfy or [None])[0]


def _ensure_phone_channel(ctx: AppContext) -> dict[str, Any]:
    """Zorgt dat er een ntfy-kanaal voor de telefoon is (privé onderwerp). Het staat uit tot
    de app na het scannen laat weten dat hij meeleest (``POST /app/paired``)."""
    from mediaguard.notify import service

    channel = _phone_channel(ctx, auto_only=True)
    if channel is not None:
        return channel
    topic = "mediaguard-" + secrets.token_urlsafe(18).replace("-", "x").replace("_", "y")
    return service.create_channel(ctx, "ntfy", PHONE_CHANNEL_NAME,
                                  {"server": "https://ntfy.sh", "topic": topic}, {},
                                  enabled=False, auto_created=True)


@router.get("/pairing")
def pairing(request: Request, _: Principal = Depends(require_admin),
            ctx: AppContext = Depends(get_app_context)) -> dict[str, Any]:
    from mediaguard.security.net import is_https, secure_public_url

    configured = str(ctx.config.value("app.public_url") or "").strip().rstrip("/")
    if configured:
        url, source = configured, "setting"
    else:
        # https via de tunnel: cloudflared praat zelf http met de web-server, maar zet
        # X-Forwarded-Proto: https (alleen van een vertrouwde proxy overgenomen)
        proto = "https" if is_https(request) else "http"
        host = request.headers.get("x-forwarded-host") or request.headers.get("host") or ""
        url, source = f"{proto}://{host}".rstrip("/"), "request"
    # publiek adres altijd via https: de app-sleutel gaat nooit onversleuteld over internet
    url, upgraded = secure_public_url(url)
    ntfy = None
    with contextlib.suppress(Exception):
        channel = _phone_channel(ctx)
        if channel is not None:
            ntfy = _ntfy_info(channel)
    key = ctx.db.fetch_one("SELECT id, prefix, created_at FROM api_keys WHERE (name=? OR "
                           "name LIKE ?) AND revoked_at IS NULL ORDER BY id DESC LIMIT 1",
                           (APP_KEY_NAME, APP_KEY_NAME + " %"))
    return {"server_url": url, "server_url_source": source, "api_key_name": APP_KEY_NAME,
            "server_url_upgraded": upgraded,
            "server_url_note_nl": ("Publiek adres: de app gebruikt https:// zodat de sleutel "
                                   "versleuteld blijft." if upgraded else None),
            "existing_key": ({"id": key["id"], "prefix": key["prefix"],
                              "created_at": key["created_at"]} if key else None),
            "ntfy": ntfy, "device": ctx.state_get("app.device") or None,
            "qr_payload_example": {"v": 1, "url": url, "key": "<door de web-UI ingevuld>",
                                   **({"ntfy": ntfy} if ntfy else {})}}


@router.post("/pairing/key")
def pairing_key(request: Request, principal: Principal = Depends(require_admin),
                ctx: AppContext = Depends(get_app_context)) -> dict[str, Any]:
    """Nieuwe app-sleutel (rol operator) + QR-inhoud. Een eerdere actieve app-sleutel wordt
    ingetrokken: één telefoon tegelijk, en een verloren telefoon koppel je zo ook los.
    Sleutelnamen zijn uniek (ook ingetrokken), dus de naam krijgt een datumstempel.
    De QR-code bevat altijd het ntfy-kanaal van de telefoon (zo nodig nieuw aangemaakt)."""
    import json

    from mediaguard import apikeys
    from mediaguard.notify.service import qr_data_uri

    for row in ctx.db.fetch_all("SELECT id FROM api_keys WHERE (name=? OR name LIKE ?) AND "
                                "revoked_at IS NULL", (APP_KEY_NAME, APP_KEY_NAME + " %")):
        apikeys.revoke_key(ctx, int(row["id"]), actor=principal.actor)
    with contextlib.suppress(Exception):
        _ensure_phone_channel(ctx)
    stamp = datetime.now(TZ).strftime("%Y-%m-%d %H:%M")
    created = apikeys.create_key(ctx, name=f"{APP_KEY_NAME} {stamp} {secrets.token_hex(2)}",
                                 role="operator",
                                 expires_in_days=None, actor=principal.actor)
    info = pairing(request, principal, ctx)
    payload: dict[str, Any] = {"v": 1, "url": info["server_url"], "key": created["key"]}
    if info.get("ntfy"):
        payload["ntfy"] = info["ntfy"]
    text = json.dumps(payload, separators=(",", ":"))
    return {"key_id": created["id"], "key_prefix": created["prefix"], "qr_payload": payload,
            "qr_text": text, "qr_data_uri": qr_data_uri(text),
            "message_nl": "Scan deze code met de MediaGuard-app. De code toont de sleutel maar "
                          "één keer."}


class PairedBody(BaseModel):
    ntfy_topic: str | None = Field(default=None, max_length=200)
    app_version: str | None = Field(default=None, max_length=40)


@router.post("/paired")
def paired(body: PairedBody, principal: Principal = Depends(require_operator),
           ctx: AppContext = Depends(get_app_context)) -> dict[str, Any]:
    """De app meldt na het koppelen dat hij het telefoonkanaal leest: MediaGuard zet dat
    kanaal dan aan, zodat directe meldingen meteen naar de telefoon gaan (niet alleen het
    dagoverzicht). Antwoordt met het kanaal; bij handmatig koppelen krijgt de app zo ook
    het onderwerp. Alleen de app-sleutel en beheerders; de app krijgt alleen het automatisch
    aangemaakte telefoonkanaal (nooit een kanaal dat de beheerder zelf instelde of uitzette)."""
    from mediaguard.notify import service

    _require_phone_access(principal)
    channel = _phone_channel(ctx, body.ntfy_topic, auto_only=principal.role != "admin") \
        or _ensure_phone_channel(ctx)
    if not channel.get("enabled"):
        channel = service.update_channel(ctx, int(channel["id"]), enabled=True)
        audit(ctx.db, actor=principal.actor, action="notify.channel_changed",
              target_type="notify", target_id=str(channel["id"]),
              new_value={"enabled": True}, reason="app gekoppeld")
    ctx.state_set("app.device", {"app_version": body.app_version, "paired_at": utcnow(),
                                 "actor": principal.actor})
    audit(ctx.db, actor=principal.actor, action="app.paired", target_type="notify",
          target_id=str(channel["id"]), new_value={"app_version": body.app_version})
    return {"ok": True, "ntfy": _ntfy_info(channel),
            "message_nl": "Pushmeldingen naar deze telefoon staan aan"}
