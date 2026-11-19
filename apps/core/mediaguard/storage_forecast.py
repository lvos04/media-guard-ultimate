"""Opslagprognose per schijf (rapport 5, M6) — zonder gebruiker.

Uit de dagelijkse momentopnamen van de vrije ruimte (``library_space:<dag>``, R19) volgt per
schijf een trend (lineaire regressie over de laatste 30 dagen) en een schatting 'vol over ± N
dagen'. Onder de 60 dagen komt er een aandachtspunt. Als de TrueNAS-API is ingesteld
(Instellingen → Herstel: URL + geheim ``truenas:apikey``) komt de poolstatus erbij (gezond,
scrub, fouten); zonder instelling wordt die stap overgeslagen.

De bewaking (``mediaguard.monitor``) meet de pool elke ronde en bewaart de uitkomst
(``truenas_pool``). Is een pool niet gezond, dan komt er een melding en pauzeert het opruimen
van originelen in de quarantaine tot de pool weer gezond is (verbeterlijst 37).
"""

from __future__ import annotations

import logging
from datetime import UTC, datetime, timedelta
from typing import Any
from uuid import uuid4

import httpx
from fastapi import APIRouter, Depends

from mediaguard.auth import Principal, require_viewer
from mediaguard.context import AppContext, get_app_context
from mediaguard.db import utcnow
from mediaguard.settings_registry import SettingsService

LOGGER = logging.getLogger(__name__)
router = APIRouter(prefix="/api/v1/storage", tags=["storage"])
HISTORY_DAYS = 30
WARN_DAYS = 60
ERROR_DAYS = 14


def _history(ctx: AppContext, days: int = HISTORY_DAYS) -> list[tuple[str, dict[str, Any]]]:
    today = datetime.now(UTC).date()
    out = []
    for i in range(days, -1, -1):
        day = (today - timedelta(days=i)).isoformat()
        snap = ctx.state_get(f"library_space:{day}", None)
        if snap:
            out.append((day, snap))
    return out


def _slope(points: list[tuple[float, float]]) -> float | None:
    """Kleinste-kwadratenhelling (bytes per dag); None bij te weinig punten."""
    if len(points) < 3:
        return None
    n = len(points)
    mx = sum(x for x, _ in points) / n
    my = sum(y for _, y in points) / n
    den = sum((x - mx) ** 2 for x, _ in points)
    if den == 0:
        return None
    return sum((x - mx) * (y - my) for x, y in points) / den


def days_nl(days: float | None) -> str:
    if days is None:
        return "geen daling gemeten"
    if days < 1:
        return "minder dan een dag"
    if days < 14:
        return f"± {round(days)} dagen"
    if days < 120:
        return f"± {round(days / 7)} weken"
    if days < 730:
        return f"± {round(days / 30)} maanden"
    return "meer dan twee jaar"


def forecast(ctx: AppContext) -> dict[str, Any]:
    from mediaguard.library.api import free_space_snapshot

    hist = _history(ctx)
    now_snap = free_space_snapshot(ctx)
    disks: list[dict[str, Any]] = []
    for disk in now_snap.get("disks", []):
        path = disk["path"]
        pts: list[tuple[float, float]] = []
        series: list[dict[str, Any]] = []
        start: datetime | None = None
        for day, snap in hist:
            hit = next((d for d in snap.get("disks", []) if d.get("path") == path), None)
            if hit is None:
                continue
            at = datetime.fromisoformat(day).replace(tzinfo=UTC)
            start = start or at
            pts.append(((at - start).days, float(hit["free"])))
            series.append({"day": day, "free": hit["free"]})
        slope = _slope(pts)
        free = int(disk["free"])
        days_left = (free / -slope) if slope is not None and slope < 0 else None
        disks.append({"path": path, "free": free, "total": disk.get("total"),
                      "trend_per_day": round(slope) if slope is not None else None,
                      "days_left": round(days_left, 1) if days_left is not None else None,
                      "days_left_nl": days_nl(days_left), "history": series,
                      "enough_data": slope is not None})
    soonest = min((d["days_left"] for d in disks if d["days_left"] is not None), default=None)
    return {"disks": disks, "soonest_days": soonest, "soonest_nl": days_nl(soonest),
            "basis_nl": f"trend over de laatste {HISTORY_DAYS} dagen (dagelijkse meting)"}


def attention_items(ctx: AppContext) -> list[dict[str, Any]]:
    """'Schijf vol over ± N dagen' als dat binnen ``WARN_DAYS`` valt."""
    try:
        fc = forecast(ctx)
    except Exception:  # noqa: BLE001 - prognose is extra
        return []
    out = []
    for d in fc["disks"]:
        if d["days_left"] is None or d["days_left"] >= WARN_DAYS:
            continue
        tb = f"{d['free'] / 1024**4:.1f}".replace(".", ",")
        out.append({"path": d["path"], "days_left": d["days_left"],
                    "severity": "error" if d["days_left"] < ERROR_DAYS else "warning",
                    "title_nl": f"Schijf vol over {d['days_left_nl']}",
                    "detail_nl": f"{d['path']}: {tb} TB vrij, daalt ± "
                                 f"{abs(d['trend_per_day'] or 0) / 1024**3:.0f} GB per dag. "
                                 "MediaGuard ruimt zelf downloads en quarantaine op; extra "
                                 "opslag is nodig als dit blijft dalen."})
    return out


def _valid_pool(pool: Any) -> bool:
    return (isinstance(pool, dict) and isinstance(pool.get("name"), str)
            and bool(pool["name"].strip())
            and isinstance(pool.get("status"), str) and bool(pool["status"].strip())
            and isinstance(pool.get("healthy"), bool)
            and (pool.get("scan") is None or isinstance(pool["scan"], dict)))


async def truenas_health(ctx: AppContext) -> dict[str, Any] | None:
    """Poolstatus via de TrueNAS-API, alleen als URL en API-sleutel zijn ingesteld."""
    # Neem na de meetopdracht een nieuwe momentopname. De gewone instellingencache
    # kan nog het adres van vóór een wijziging door een andere werker bevatten.
    config = SettingsService(ctx.db, ctx.registry)
    base = str(config.value("recovery.truenas_url") or "").rstrip("/")
    key = ctx.secrets.get("truenas:apikey")
    if not base or not key:
        return None
    try:
        async with httpx.AsyncClient(
                base_url=base, timeout=10.0,
                verify=bool(config.value("recovery.truenas_verify_tls")),
                transport=ctx.services.get("truenas_transport"),
                headers={"Authorization": f"Bearer {key}"}) as client:
            resp = await client.get("/api/v2.0/pool")
            resp.raise_for_status()
            pools = resp.json()
    except (httpx.HTTPError, ValueError) as exc:
        return {"ok": False, "error_nl": f"TrueNAS niet bereikbaar: {type(exc).__name__}",
                "pools": []}
    out = []
    for p in pools if isinstance(pools, list) else []:
        if not _valid_pool(p):
            continue
        scan = p.get("scan") or {}
        out.append({"name": p.get("name"), "status": p.get("status"),
                    "healthy": p["healthy"],
                    "scrub_state": scan.get("state"), "scrub_errors": scan.get("errors"),
                    "scrub_end": (scan.get("end_time") or {}).get("$date")
                    if isinstance(scan.get("end_time"), dict) else scan.get("end_time")})
    if not out or len(out) != len(pools):
        return {"ok": False, "error_nl": "Ongeldige TrueNAS-poolmeting", "pools": out}
    return {"ok": all(p["healthy"] and p["status"] == "ONLINE" for p in out), "pools": out}


POOL_STATE = "truenas_pool"
# Migratie 071 trekt deze opdracht in zodra de ingestelde bron verandert.
POOL_REQUEST = "truenas_pool_request_v1"
POOL_MAX_AGE = timedelta(seconds=900)


def pool_observed_at(state: dict[str, Any]) -> str | None:
    """Tijd van de laatste geslaagde meting, ook na een latere mislukte controle."""
    return state.get("checked_at") if state.get("reachable") is True else state.get("observed_at")


def degraded_pools(state: dict[str, Any]) -> list[dict[str, Any]]:
    return [p for p in state.get("degraded") or [] if isinstance(p, dict)]


def _pool_state(prev: dict[str, Any], health: dict[str, Any], checked_at: str) -> dict[str, Any]:
    rows = health.get("pools")
    pools = [p for p in rows if _valid_pool(p)] if isinstance(rows, list) else []
    degraded = [{"name": p["name"], "status": p["status"]} for p in pools
                if not p["healthy"] or p["status"] != "ONLINE"]
    if (health.get("error_nl") or not pools or len(pools) != len(rows)
            or (health.get("ok") is False and not degraded)):
        # Ook in een onvolledig antwoord kan een pool aantoonbaar ziek zijn. Houd
        # eerdere incidenten vast tot een volledige meting ze kan afmelden.
        known = {p.get("name"): p for p in degraded_pools(prev)}
        known.update((p["name"], p) for p in degraded)
        return {**prev, "reachable": False, "checked_at": checked_at,
                "observed_at": pool_observed_at(prev), "degraded": list(known.values()),
                "error_nl": health.get("error_nl") or "Ongeldige TrueNAS-poolmeting"}
    return {"reachable": True, "checked_at": checked_at, "degraded": degraded,
            "pools": [{k: p[k] for k in ("name", "status", "healthy")} for p in pools],
            "degraded_since": (prev.get("degraded_since") or checked_at) if degraded else None}


def _start_pool_request(ctx: AppContext) -> str:
    request_id = uuid4().hex
    with ctx.db.startup_lock():
        try:
            ctx.state_set(POOL_REQUEST, request_id)
        except Exception:
            ctx.db.execute("DELETE FROM system_state WHERE key=?", (POOL_REQUEST,))
            raise
    return request_id


def record_pool(ctx: AppContext, health: dict[str, Any] | None, *,
                request_id: str | None = None, checked_at: str | None = None) -> dict[str, Any]:
    """Alleen de nieuwste meetopdracht mag publiceren, ook vanuit een andere werker."""
    request_id = request_id or _start_pool_request(ctx)
    with ctx.db.transaction():
        prev = ctx.state_get(POOL_STATE, {}) or {}
        if ctx.state_get(POOL_REQUEST) != request_id:
            return prev
        state = {} if health is None else {
            **_pool_state(prev, health, checked_at or utcnow()), "request_id": request_id}
        if state != prev:
            ctx.state_set(POOL_STATE, state)
        return state


async def _measure_pool(ctx: AppContext) -> tuple[dict[str, Any] | None, dict[str, Any]]:
    if not ctx.secrets.exists("truenas:apikey") and not ctx.state_get(POOL_STATE):
        return None, {}
    # Deze afzonderlijke commit trekt de oude toestemming in vóór het netwerkverzoek.
    # Bij annulering, een herstart of een schrijffout blijft de nieuwe opdracht open.
    request_id = _start_pool_request(ctx)
    checked_at = utcnow()
    health = await truenas_health(ctx)
    try:
        state = record_pool(ctx, health, request_id=request_id, checked_at=checked_at)
    except Exception:  # noqa: BLE001 - de prognose kan het antwoord nog tonen
        LOGGER.exception("poolstatus bewaren mislukt")
        state = {}
    return health, state


async def refresh_pool(ctx: AppContext) -> dict[str, Any]:
    _, state = await _measure_pool(ctx)
    return state


def pool_degraded(ctx: AppContext, *, state: dict[str, Any] | None = None) -> list[dict[str, Any]]:
    """Pools die bij de laatste meting niet gezond waren (leeg = gezond of niet ingesteld)."""
    if state is None:
        try:
            state = ctx.state_get(POOL_STATE, {}) or {}
        except Exception:  # noqa: BLE001 - nooit het opruimen laten crashen op de meting
            return []
    return degraded_pools(state) if isinstance(state, dict) else []


def pool_cleanup_pause_reason(ctx: AppContext, *, quarantine: bool = False) -> str | None:
    """Bij een ingestelde sleutel is alleen een verse, gezonde poolmeting voldoende."""
    unknown = "De TrueNAS-poolstatus is onbekend doordat een recente, geldige meting ontbreekt."
    try:
        if not ctx.secrets.exists("truenas:apikey"):
            # De quarantaine respecteerde een laatst bekende zieke pool ook na ontkoppeling.
            if quarantine and (degraded := pool_degraded(ctx)):
                return f"TrueNAS-pool {pool_names_nl(degraded)} is niet gezond."
            return None
        if not ctx.secrets.get("truenas:apikey") or not ctx.config.value("recovery.truenas_url"):
            return unknown
        state = ctx.state_get(POOL_STATE, {})
        request_id = ctx.state_get(POOL_REQUEST)
    except Exception:  # noqa: BLE001 - een onleesbare meting geeft geen toestemming
        LOGGER.exception("poolstatus voor opruimen lezen mislukt")
        return unknown
    if (not isinstance(state, dict) or state.get("reachable") is not True
            or not isinstance(request_id, str) or not request_id
            or state.get("request_id") != request_id):
        return unknown
    checked_at = pool_observed_at(state)
    if not isinstance(checked_at, str):
        return unknown
    try:
        measured = datetime.fromisoformat(checked_at)
    except ValueError:
        return unknown
    if measured.tzinfo is None or not timedelta() <= datetime.now(UTC) - measured <= POOL_MAX_AGE:
        return unknown
    if not isinstance(state.get("degraded"), list):
        return unknown
    if degraded := pool_degraded(ctx, state=state):
        return f"TrueNAS-pool {pool_names_nl(degraded)} is niet gezond."
    pools = state.get("pools")
    if state["degraded"] or not isinstance(pools, list) or not pools or not all(
            _valid_pool(p) and p["healthy"] is True and p["status"] == "ONLINE"
            for p in pools):
        return unknown
    return None


def pool_names_nl(pools: list[dict[str, Any]]) -> str:
    return ", ".join(f"{p.get('name') or '?'} ({p.get('status') or 'onbekend'})" for p in pools)


@router.get("/forecast")
async def storage_forecast(_: Principal = Depends(require_viewer),
                           ctx: AppContext = Depends(get_app_context)) -> dict[str, Any]:
    data = forecast(ctx)
    data["truenas"], _ = await _measure_pool(ctx)
    return data
