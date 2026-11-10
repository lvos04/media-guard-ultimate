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

import httpx
from fastapi import APIRouter, Depends

from mediaguard.auth import Principal, require_viewer
from mediaguard.context import AppContext, get_app_context
from mediaguard.db import utcnow

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


async def truenas_health(ctx: AppContext) -> dict[str, Any] | None:
    """Poolstatus via de TrueNAS-API, alleen als URL en API-sleutel zijn ingesteld."""
    base = str(ctx.config.value("recovery.truenas_url") or "").rstrip("/")
    key = ctx.secrets.get("truenas:apikey")
    if not base or not key:
        return None
    try:
        async with httpx.AsyncClient(
                base_url=base, timeout=10.0,
                verify=bool(ctx.config.value("recovery.truenas_verify_tls")),
                transport=ctx.services.get("truenas_transport"),
                headers={"Authorization": f"Bearer {key}"}) as client:
            resp = await client.get("/api/v2.0/pool")
            resp.raise_for_status()
            pools = resp.json() or []
    except (httpx.HTTPError, ValueError) as exc:
        return {"ok": False, "error_nl": f"TrueNAS niet bereikbaar: {type(exc).__name__}",
                "pools": []}
    out = []
    for p in pools:
        scan = p.get("scan") or {}
        out.append({"name": p.get("name"), "status": p.get("status"),
                    "healthy": bool(p.get("healthy")),
                    "scrub_state": scan.get("state"), "scrub_errors": scan.get("errors"),
                    "scrub_end": (scan.get("end_time") or {}).get("$date")
                    if isinstance(scan.get("end_time"), dict) else scan.get("end_time")})
    return {"ok": all(p["healthy"] for p in out), "pools": out}


POOL_STATE = "truenas_pool"


def record_pool(ctx: AppContext, health: dict[str, Any] | None) -> dict[str, Any]:
    """Bewaar de laatste poolmeting. Niet bereikbaar = laatst bekende toestand houden
    (een tijdelijk onbereikbare API heft een opruimpauze nooit op)."""
    prev = ctx.state_get(POOL_STATE, {}) or {}
    now = utcnow()
    if health is None:  # niet ingesteld: niets te bewaken
        state: dict[str, Any] = {}
    elif health.get("error_nl"):
        state = {**prev, "reachable": False, "error_nl": health["error_nl"],
                 "checked_at": now}
    else:
        degraded = [{"name": p.get("name"), "status": p.get("status")}
                    for p in health.get("pools") or [] if not p.get("healthy")]
        state = {"reachable": True, "checked_at": now, "degraded": degraded,
                 "pools": [{"name": p.get("name"), "status": p.get("status"),
                            "healthy": bool(p.get("healthy"))}
                           for p in health.get("pools") or []],
                 "degraded_since": (prev.get("degraded_since") or now) if degraded else None}
    if state != prev:
        ctx.state_set(POOL_STATE, state)
    return state


async def refresh_pool(ctx: AppContext) -> dict[str, Any]:
    return record_pool(ctx, await truenas_health(ctx))


def pool_degraded(ctx: AppContext) -> list[dict[str, Any]]:
    """Pools die bij de laatste meting niet gezond waren (leeg = gezond of niet ingesteld)."""
    try:
        state = ctx.state_get(POOL_STATE, {}) or {}
    except Exception:  # noqa: BLE001 - nooit het opruimen laten crashen op de meting
        return []
    return [p for p in state.get("degraded") or [] if isinstance(p, dict)]


def pool_names_nl(pools: list[dict[str, Any]]) -> str:
    return ", ".join(f"{p.get('name') or '?'} ({p.get('status') or 'onbekend'})" for p in pools)


@router.get("/forecast")
async def storage_forecast(_: Principal = Depends(require_viewer),
                           ctx: AppContext = Depends(get_app_context)) -> dict[str, Any]:
    data = forecast(ctx)
    data["truenas"] = await truenas_health(ctx)
    try:
        record_pool(ctx, data["truenas"])
    except Exception:  # noqa: BLE001 - weergave gaat voor
        LOGGER.exception("poolstatus bewaren mislukt")
    return data
