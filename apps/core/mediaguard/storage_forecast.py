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

import fcntl
import io
import logging
import os
import sqlite3
import stat
from contextlib import ExitStack
from datetime import UTC, datetime, timedelta
from typing import Any
from uuid import uuid4

import httpx
from fastapi import APIRouter, Depends

from mediaguard import hub_pool, pool_workers
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
_POOL_PIPE = "truenas_pool_pipe"
_DISCONNECTED = "truenas_pool_disconnected"
_REVOKED = b"!"  # komt nooit voor in een hexadecimale opdracht-ID
POOL_MAX_AGE = hub_pool.MAX_AGE


def pool_observed_at(state: dict[str, Any]) -> str | None:
    """Tijd van de laatste geslaagde meting, ook na een latere mislukte controle."""
    if state.get("source") == "hub" or state.get("reachable") is not True:
        return state.get("observed_at")
    return state.get("checked_at")


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


def _request_pipe(ctx: AppContext, *, create: bool = False) -> io.FileIO:
    # De opdracht en intrekkingen bestaan alleen in het kernelgeheugen. Na het
    # sluiten van de laatste werker is het kanaal leeg.
    path = ctx.db.path.with_suffix(".pool-fifo")
    flags = os.O_RDWR | os.O_NONBLOCK | os.O_NOFOLLOW
    try:
        fd = os.open(path, flags)
    except FileNotFoundError:
        if not create:
            raise
        os.mkfifo(path, 0o600)
        fd = os.open(path, flags)
    stream = io.FileIO(fd, "r+")
    try:
        if not stat.S_ISFIFO(os.fstat(fd).st_mode):
            raise OSError("Ongeldig kanaal voor de poolmeting")
    except BaseException:
        stream.close()
        raise
    return stream


def _worker_lock(ctx: AppContext, mode: int, *, create: bool = True) -> io.FileIO:
    path = ctx.db.path.with_suffix(".pool-workers")
    flags = os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK
    fd = os.open(path, flags | os.O_CREAT if create else flags, 0o600)
    lock = io.FileIO(fd, "r")
    try:
        if not stat.S_ISREG(os.fstat(fd).st_mode):
            raise OSError("Ongeldig slotbestand voor de meetwerkers")
        fcntl.flock(fd, mode)
    except BaseException:
        lock.close()
        raise
    return lock


def _directory_worker_lock(ctx: AppContext, held: ExitStack, *, probe: bool) -> tuple[int, int]:
    # Een OFD-recordslot op de datamap blijft bestaan zolang deze werker de descriptor
    # houdt. Het staat los van het flock-datamapslot voor SQLite en de kern.
    fd = os.open(ctx.db.path.parent, os.O_RDONLY | os.O_DIRECTORY)
    held.callback(os.close, fd)
    return fd, pool_workers.record_lock(fd, 0, probe=probe)


def _workers_connected(ctx: AppContext, *, new_measurement: bool = False) -> bool:
    try:
        with (_worker_lock(ctx, fcntl.LOCK_EX | fcntl.LOCK_NB, create=new_measurement),
              ExitStack() as held):
            fd, conflict = _directory_worker_lock(ctx, held, probe=True)
            return conflict == fcntl.F_UNLCK and pool_workers.all_reported(
                ctx, fd, new_measurement=new_measurement)
    except BlockingIOError:
        # Het gedeelde slot van een andere werker pauzeert de ronde. Deze lezer
        # kan zijn eigen meetfouten nog steeds via zijn bevestigde kanaal melden.
        LOGGER.warning("Een slot voor de poolwerkers is bezet, opruimen is gepauzeerd")
        return False
    except (OSError, sqlite3.Error, ValueError, TypeError, KeyError) as exc:
        if vote := ctx.services.get(pool_workers.SERVICE):
            vote.withdraw()
        LOGGER.warning("Register of slot van de poolwerkers niet beschikbaar (%s)",
                       type(exc).__name__)
        return False


def _block_disconnected_worker(ctx: AppContext) -> None:
    if vote := ctx.services.get(pool_workers.SERVICE):
        vote.withdraw()
    if _DISCONNECTED not in ctx.services:
        with ExitStack() as held:
            try:
                held.enter_context(_worker_lock(ctx, fcntl.LOCK_SH | fcntl.LOCK_NB))
            except OSError:
                # Ook bij een onleesbaar werkerslot zien andere processen de blokkade.
                _directory_worker_lock(ctx, held, probe=False)
            block = held.pop_all()
            if ctx.services.setdefault(_DISCONNECTED, block) is not block:
                block.close()


def _hold_request_pipe(ctx: AppContext) -> io.FileIO:
    # Onder het datamapslot, ook bij de start van een werker vóór de eerste meting.
    stream = ctx.services.get(_POOL_PIPE)
    path = ctx.db.path.with_suffix(".pool-fifo")
    if stream is None or stream.closed or _DISCONNECTED in ctx.services or (
            os.fstat(stream.fileno()).st_nlink == 0
            or not os.path.samestat(os.fstat(stream.fileno()), path.lstat())):
        ctx.services[_POOL_PIPE] = _request_pipe(ctx, create=True)
        if stream is not None:
            stream.close()
    vote = ctx.services.get(pool_workers.SERVICE) or pool_workers.register(ctx)
    vote.report(ctx, ctx.services[_POOL_PIPE])
    if lock := ctx.services.pop(_DISCONNECTED, None):
        lock.close()
    return ctx.services[_POOL_PIPE]


def _revoke_pool_request(ctx: AppContext) -> None:
    # Eén atomaire, niet-blokkerende toevoeging. Ook als een lezer de oude ID
    # even in handen heeft, kan het terugschrijven ervan dit teken niet uitwissen.
    # Een volle FIFO bevat al meer dan één ID en geeft dus evenmin toestemming.
    try:
        stream = ctx.services.get(_POOL_PIPE)
        if stream is not None and not stream.closed:
            written = stream.write(_REVOKED)
            linked = os.fstat(stream.fileno()).st_nlink != 0
        else:
            with _request_pipe(ctx) as stream:
                written = stream.write(_REVOKED)
                linked = True
        if written != len(_REVOKED) or not linked:
            raise OSError("Intrekking niet gedeeld via het meetkanaal")
    except (OSError, ValueError):
        try:
            _block_disconnected_worker(ctx)
        finally:
            ctx.db.execute("DELETE FROM system_state WHERE key=?", (POOL_REQUEST,))


def _current_request(ctx: AppContext, request_id: str) -> bool:
    with ctx.db.startup_lock(), _request_pipe(ctx) as stream:
        _hold_request_pipe(ctx)  # ook een directe lezer kan later zonder padtoegang intrekken
        current = stream.read()
        if not current or len(current) != 32 or _REVOKED in current:
            return False
        if stream.read(1):
            return False  # een andere werker trok de opdracht tijdens het lezen in
        if stream.write(current) != len(current):
            raise OSError("Poolopdracht niet leesbaar voor andere werkers")
        return current == request_id.encode("ascii")


def _start_pool_request(ctx: AppContext, *, skip_unconfigured: bool = False) -> str | None:
    _revoke_pool_request(ctx)
    try:
        if (skip_unconfigured and not ctx.secrets.exists("truenas:apikey")
                and not hub_pool.origin(ctx) and not ctx.state_get(POOL_STATE)):
            vote = ctx.services.get(pool_workers.SERVICE)
            if _DISCONNECTED in ctx.services or (vote is not None and vote.ready is None):
                with ctx.db.startup_lock():
                    _hold_request_pipe(ctx)
            return None
        with ctx.db.startup_lock():
            stream = _hold_request_pipe(ctx)
            stream.read()
            request_id = uuid4().hex
            with ctx.db.transaction():
                ctx.state_set(POOL_REQUEST, request_id)
                if _workers_connected(ctx, new_measurement=True):
                    ctx.state_set(pool_workers.CLEANUP_REQUEST, request_id)
            if stream.write(request_id.encode("ascii")) != len(request_id):
                raise OSError("Nieuwe poolopdracht niet gedeeld")
            return request_id
    except Exception:
        _revoke_pool_request(ctx)
        raise


def record_pool(ctx: AppContext, health: dict[str, Any] | None, *,
                request_id: str | None = None, checked_at: str | None = None) -> dict[str, Any]:
    """Alleen de nieuwste meetopdracht mag publiceren, ook vanuit een andere werker."""
    request_id = request_id or _start_pool_request(ctx)
    with ctx.db.transaction():
        prev = ctx.state_get(POOL_STATE, {}) or {}
        if (request_id is None or ctx.state_get(POOL_REQUEST) != request_id
                or not _current_request(ctx, request_id)):
            return prev
        state = {}
        if health is not None:
            same_source = health.get("source") == prev.get("source")
            state = _pool_state(prev if same_source else {}, health, checked_at or utcnow())
            state["request_id"] = request_id
            if health.get("source") == "hub":
                state["source"] = "hub"
                if state["reachable"]:
                    state["observed_at"] = health["observed_at"]
                    state["geldig_tot"] = health["geldig_tot"]
        if state != prev:
            ctx.state_set(POOL_STATE, state)
        return state


async def _measure_pool(ctx: AppContext) -> tuple[dict[str, Any] | None, dict[str, Any]]:
    request_id = _start_pool_request(ctx, skip_unconfigured=True)
    if request_id is None:
        return None, {}
    checked_at = utcnow()
    try:
        health = await truenas_health(ctx)
        if health is None:
            if not ctx.secrets.exists("truenas:apikey"):
                health = await hub_pool.read(ctx)
            elif hub_pool.origin(ctx):
                health = {"ok": False, "pools": [],
                          "error_nl": "De directe poolstatus is onbekend."}
    except BaseException:  # ook bij annulering vervalt deze meetopdracht
        _revoke_pool_request(ctx)
        raise
    try:
        state = record_pool(ctx, health, request_id=request_id, checked_at=checked_at)
    except Exception:  # noqa: BLE001 - de prognose kan het antwoord nog tonen
        _revoke_pool_request(ctx)
        LOGGER.exception("poolstatus bewaren mislukt")
        state = {}
    if (health and health.get("source") == "hub" and
            (state.get("request_id") != request_id or not _fresh_for_cleanup(state))):
        # Een tijdens het bewaren ingetrokken of verlopen antwoord geeft geen groen op de prognose.
        health = hub_pool.unknown()
    return health, state


async def startup(ctx: AppContext) -> None:
    try:
        with ctx.db.startup_lock():
            _hold_request_pipe(ctx)
    except (OSError, sqlite3.Error):
        try:
            try:
                _block_disconnected_worker(ctx)
            finally:
                _revoke_pool_request(ctx)
        except (OSError, sqlite3.Error):
            LOGGER.exception("Opruimtoestemming bij de start niet vastgelegd")
        LOGGER.exception("Kanaal voor de poolmeting niet beschikbaar bij de start")


async def shutdown(ctx: AppContext) -> None:
    if vote := ctx.services.pop(pool_workers.SERVICE, None):
        try:
            vote.close(ctx)
        except OSError:
            LOGGER.exception("Afsluiting van de meetwerker niet bevestigd")
    for key in (_POOL_PIPE, _DISCONNECTED):
        if stream := ctx.services.pop(key, None):
            stream.close()


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


def _fresh_for_cleanup(state: dict[str, Any]) -> bool:
    if state.get("source") == "hub":
        return hub_pool.fresh(state.get("observed_at"), state.get("geldig_tot"))
    measured = pool_observed_at(state)
    if not isinstance(measured, str):
        return False
    try:
        instant = datetime.fromisoformat(measured)
        return (instant.tzinfo is not None
                and timedelta() <= datetime.now(UTC) - instant <= POOL_MAX_AGE)
    except (ValueError, OverflowError):
        return False


def pool_cleanup_pause_reason(ctx: AppContext, *, quarantine: bool = False) -> str | None:
    """Bij een ingestelde sleutel is alleen een verse, gezonde poolmeting voldoende."""
    unknown = "De TrueNAS-poolstatus is onbekend doordat een recente, geldige meting ontbreekt."
    unknown_worker = ("De TrueNAS-poolstatus is onbekend. De inschrijving of meldbaarheid van "
                      "een poolwerker is nog niet bevestigd.")
    vote = ctx.services.get(pool_workers.SERVICE)
    try:
        if vote is not None and vote.unregistered:
            return unknown_worker
        direct = ctx.secrets.exists("truenas:apikey")
        via_hub = not direct and bool(hub_pool.origin(ctx))
        if via_hub:
            unknown = hub_pool.UNKNOWN
        if not direct and not via_hub:
            with ctx.db.startup_lock():
                if vote is not None and vote.ready is None:
                    _hold_request_pipe(ctx)
                if not _workers_connected(ctx, new_measurement=True):
                    return unknown_worker
            # De quarantaine respecteerde een laatst bekende zieke pool ook na ontkoppeling.
            if quarantine:
                previous = ctx.state_get(POOL_STATE, {}) or {}
                if (isinstance(previous, dict) and previous.get("source") != "hub"
                        and (degraded := degraded_pools(previous))):
                    return f"TrueNAS-pool {pool_names_nl(degraded)} is niet gezond."
            return None
        if direct:
            config = SettingsService(ctx.db, ctx.registry)
            available = ctx.secrets.get("truenas:apikey") and config.value("recovery.truenas_url")
        else:
            available = ctx.secrets.has(hub_pool.SECRET)
        if not available:
            _revoke_pool_request(ctx)
            return unknown
        state = ctx.state_get(POOL_STATE, {})
        request_id = ctx.state_get(POOL_REQUEST)
        with ctx.db.startup_lock():
            current = isinstance(request_id, str) and _current_request(ctx, request_id)
            if not _workers_connected(ctx):
                _revoke_pool_request(ctx)
                return unknown_worker
            if not current or ctx.state_get(pool_workers.CLEANUP_REQUEST) != request_id:
                return unknown
    except Exception:  # noqa: BLE001 - een onleesbare meting geeft geen toestemming
        try:
            _revoke_pool_request(ctx)
        except Exception:  # een werker zonder lees- én schrijfrecht mag evenmin opruimen
            LOGGER.exception("intrekking van de poolopdracht mislukt")
        LOGGER.exception("poolstatus voor opruimen lezen mislukt")
        return unknown_worker if vote is not None and vote.ready is None else unknown
    if (not isinstance(state, dict) or state.get("reachable") is not True
            or (state.get("source") == "hub") != via_hub or not request_id
            or state.get("request_id") != request_id):
        return unknown
    if not _fresh_for_cleanup(state):
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
    try:
        health, _ = await _measure_pool(ctx)
    except (OSError, sqlite3.Error):
        LOGGER.exception("Poolmeting voor de opslagprognose niet beschikbaar")
        health = None
        if ctx.secrets.exists("truenas:apikey"):
            health = {"ok": False, "pools": [], "error_nl": "Geen geldige poolmeting beschikbaar"}
        elif hub_pool.origin(ctx):
            health = hub_pool.unknown()
    data = forecast(ctx)
    data["truenas"] = health
    return data
