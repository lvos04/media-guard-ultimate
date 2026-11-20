"""Gepagineerde projecties van de bestaande MediaGuard-leesbronnen."""

from __future__ import annotations

import asyncio
import base64
import binascii
import json
import re
import time
from bisect import bisect_right
from datetime import UTC, datetime
from typing import Annotated, Any, Literal, get_args
from urllib.parse import urlencode

from fastapi import APIRouter, Depends, Query, Request
from fastapi.responses import JSONResponse
from pydantic import AwareDatetime, BaseModel, ConfigDict, Field

from mediaguard import attention
from mediaguard.activity.api import CACHE_SECONDS, activity_cached
from mediaguard.api.system import status_counts
from mediaguard.auth import Principal, require_viewer
from mediaguard.context import AppContext, get_app_context
from mediaguard.db import utcnow
from mediaguard.encoding import resources
from mediaguard.encoding.client import EncoderError
from mediaguard.instances.registry import INSTANCE_ID_RE
from mediaguard.notify import offsite
from mediaguard.read_observation import instant, observation_read
from mediaguard.secrets_store import redact
from mediaguard.storage_forecast import POOL_STATE, pool_observed_at
from mediaguard.wanted.engine import open_wanted_page

from . import backups
from .download_ids import content_hash, download_ids
from .errors import DATABASE_UNAVAILABLE, ConnectorError, failure, query_faults
from .events import MAX_SQLITE_ID

router = APIRouter(prefix="/connector/v1")
ItemType = Literal["wanted", "status", "downloads", "av1_lanes", "incidents", "backups"]
ITEM_TYPES: tuple[ItemType, ...] = get_args(ItemType)
MAX_AGE = 900
PAGE_LIMIT = 200
MAX_LIMIT_DIGITS = 18
CURSOR_PATTERN = r"^[A-Za-z0-9_-]{1,256}$"


class Item(BaseModel):
    model_config = ConfigDict(extra="forbid")

    id: str
    observed_at: AwareDatetime | None
    deep_link: str


class WantedItem(Item):
    instance_id: str | None
    kind: Literal["episode", "movie"]
    reason: Literal["missing", "cutoff"]
    status: Literal["wanted", "searching", "unavailable"]


class SystemItem(Item):
    emergency_stop_active: bool
    emergency_stop_since: AwareDatetime | None
    jobs_queued: int
    jobs_running: int
    jobs_failed: int
    unread_notifications: int


DownloadStatus = Literal[
    "downloading", "stalled", "queued", "paused", "completed", "failed",
    "warning", "processing", "delay", "downloadclientunavailable", "unknown",
]
DOWNLOAD_STATUSES = frozenset(get_args(DownloadStatus))


class DownloadItem(Item):
    status: DownloadStatus
    progress: Annotated[float, Field(ge=0, le=100)] | None
    awaiting_import: bool


class LaneItem(Item):
    lane: Literal["cpu", "gpu"]
    running: int
    slots: int
    desired_slots: int
    paused: bool


class IncidentItem(Item):
    owner_app: Literal["mediaguard"]
    cause_code: str
    instance_id: str | None
    severity: Literal["error", "warning", "info"]
    count: int


class BackupItem(Item, backups.BackupReport):
    pass


SnapshotItem = SystemItem | DownloadItem | LaneItem | IncidentItem | BackupItem


class ItemPage(BaseModel):
    type: ItemType
    items: list[WantedItem | SnapshotItem] = Field(max_length=PAGE_LIMIT)
    status: Literal["ok", "unknown"]
    reason_code: str | None
    snapshot_id: str | None
    observed_at: AwareDatetime | None
    total: int | None
    next_cursor: str | None


def _stale(observed: datetime) -> bool:
    return (datetime.now(UTC) - observed).total_seconds() > MAX_AGE


def _instance_id(value: Any) -> str | None:
    if isinstance(value, str) and INSTANCE_ID_RE.fullmatch(value) and redact(value) == value:
        return value
    return None


def _cursor(kind: ItemType, last: str, snapshot: str | None) -> str:
    value = json.dumps([kind, last, snapshot], separators=(",", ":")).encode()
    return base64.urlsafe_b64encode(value).decode().rstrip("=")


def _position(kind: ItemType, cursor: str) -> tuple[str, str | None]:
    decoded = base64.urlsafe_b64decode(cursor + "=" * (-len(cursor) % 4))
    value = json.loads(decoded)
    if not isinstance(value, list) or len(value) != 3:
        raise ValueError
    selected, last, snapshot = value
    if selected != kind or not isinstance(last, str) or kind in ("status", "backups"):
        raise ValueError
    if kind == "wanted":
        if snapshot is not None or not re.fullmatch(r"[1-9][0-9]{0,18}", last):
            raise ValueError
        if int(last) > MAX_SQLITE_ID:
            raise ValueError
    elif not (
        isinstance(snapshot, str) and re.fullmatch(r"[0-9a-f]{64}", snapshot)
        and re.fullmatch(r"[0-9a-f]{64}", last)
    ):
        raise ValueError
    if cursor != _cursor(kind, last, snapshot):
        raise ValueError
    return last, snapshot


def _wanted(ctx: AppContext, after: int, limit: int) -> ItemPage:
    summary, rows = open_wanted_page(ctx, after, limit + 1)
    items = []
    reason = None
    oldest, newest = instant(summary["oldest"]), instant(summary["newest"])
    now = datetime.now(UTC)
    if summary["missing"] or oldest is None or newest is None:
        reason = "meting_ontbreekt"
    elif _stale(oldest):
        reason = "meting_verouderd"
    elif newest > now:
        reason = "meettijd_ongeldig"
    for r in rows[:limit]:
        observed = instant(r["observed"])
        if observed is None or _stale(observed) or observed > now:
            continue
        iid = _instance_id(r["instance_id"])
        filters = {"reason": r["reason"]}
        if iid is not None:
            filters["instance"] = iid
        items.append(WantedItem(
            id=f"wanted:{r['id']}", observed_at=observed,
            deep_link="/ontbrekend?" + urlencode(filters), instance_id=iid,
            kind=r["kind"], reason=r["reason"], status=r["status"],
        ))
    return ItemPage(
        type="wanted", items=items, total=None if reason else summary["total"],
        status="unknown" if reason else "ok", reason_code=reason,
        observed_at=None if reason == "meting_ontbreekt" else oldest,
        snapshot_id=None,
        next_cursor=_cursor("wanted", str(rows[limit - 1]["id"]), None)
        if len(rows) > limit else None,
    )


def _system(ctx: AppContext, measured: datetime) -> SystemItem:
    value = status_counts(ctx)
    stop = value["emergency_stop"]
    return SystemItem(
        id="status:installation", observed_at=measured, deep_link="/",
        emergency_stop_active=bool(stop.get("active")),
        emergency_stop_since=instant(stop.get("since")) if stop.get("active") else None,
        jobs_queued=value["jobs"].get("queued", 0), jobs_running=value["jobs"].get("running", 0),
        jobs_failed=value["jobs"]["failed"], unread_notifications=value["unread_notifications"],
    )


def _downloads(ctx: AppContext, value: dict[str, Any], measured: datetime) -> list[DownloadItem]:
    rows = sorted(value["downloads"], key=lambda d: (str(d.get("instance_id")), str(d.get("id"))))
    items = []
    for row, identity in zip(rows, download_ids(ctx, rows, measured), strict=True):
        status = row.get("status")
        progress = row.get("progress")
        items.append(DownloadItem(
            id=identity, observed_at=measured, deep_link="/activiteit?tab=downloads",
            status=status if isinstance(status, str) and status in DOWNLOAD_STATUSES else "unknown",
            progress=progress if type(progress) in (int, float) and 0 <= progress <= 100 else None,
            awaiting_import=bool(row.get("awaiting_import")),
        ))
    return items


async def _lanes(ctx: AppContext, measured: datetime) -> list[LaneItem]:
    configured = resources.desired(ctx)
    lanes = {}
    workers = await resources.workers(ctx)
    if not isinstance(workers, list) or any(not isinstance(row, dict) for row in workers):
        raise EncoderError("AV1-werkermeting ontbreekt")
    for row in workers:
        lane = row.get("baan")
        if lane not in ("cpu", "gpu") or lane in lanes:
            raise EncoderError("Onbekende of dubbele AV1-baan")
        lanes[lane] = LaneItem(
            id=content_hash(["av1_lanes", lane]), observed_at=measured, deep_link="/av1",
            lane=lane, desired_slots=configured[f"{lane}_workers"],
            **resources.lane_counts(row, require_measurement=True),
        )
    if set(lanes) != {"cpu", "gpu"}:
        raise EncoderError("AV1-werkermeting ontbreekt")
    return list(lanes.values())


INCIDENT_LINKS = frozenset({
    "/", "/aanvragen", "/ai", "/anime", "/av1", "/bibliotheek", "/dolby-vision",
    "/inrichten?tab=downloads", "/inrichten?tab=seerr", "/instellingen#veiligheid",
    "/instellingen#beveiliging", "/instellingen/beveiliging", "/instellingen/geheimen",
    "/ontbrekend", "/quarantaine", "/systeem", "/systeem#back-ups", "/systeem#herstelbundel",
    "/systeem#opslag", "/systeem#updates", "/activiteit?tab=problemen",
})
INCIDENT_CODES = frozenset({
    "backup.bundle_failed", "backup.bundle_missing", "backup.failed",
    "backup.rollback_pending", "delivery.stalled",
    "downloads.sab_category_missing", "dv.verified", "encoder.offline",
    "encoding.awaiting_activation", "encoding.conflict", "encoding.failed_final",
    "encoding.gpu_idle", "encoding.stalled", "identity.ai_consent", "identity.replace_failed",
    "identity.review", "library.locked", "maintenance.failed", "notices.whats_new",
    "quarantine.check_failed",
    "quarantine.full", "recovery.not_ready", "routing.review", "security.secret_key_mismatch",
    "security.tunnel_untrusted", "security.twofa_off", "seerr.repair_test_failed",
    "seerr.sync_failed", "settings.unsafe", "storage.full_soon", "storage.pool_degraded",
    "storage.typo", "storage.write_probe", "system.emergency_stop", "system.update_available",
    "tasks.stale",
})


def _incident(row: dict[str, Any], measured: datetime) -> IncidentItem:
    key = str(row["key"])
    instance = _instance_id(row.get("instance_id"))
    head, _, tail = key.rpartition(".")
    if instance and tail == instance and head in ("instance.shadow", "instance.suspended"):
        code = head
    else:
        instance = None
        if key in INCIDENT_CODES or (
            head in ("encoder.idle", "encoder.paused", "encoder.stalls")
            and tail in ("cpu", "gpu")
        ):
            code = key
        elif head == "supervisor.restarts":
            code = head
        else:
            code = "incident.recorded"
    href = row.get("href")
    link = href if isinstance(href, str) and href in INCIDENT_LINKS else "/activiteit?tab=problemen"
    if instance:
        link = f"/instellingen/instanties#{instance}"
    severity = row.get("severity")
    identity = ["incidents", key]
    if key == "storage.full_soon" and row.get("source_identity"):
        identity.append(row["source_identity"])
    return IncidentItem(
        id=content_hash(identity),
        observed_at=instant(row["observed_at"]) if "observed_at" in row else measured,
        deep_link=link,
        owner_app="mediaguard", cause_code=code, instance_id=instance,
        severity=severity if severity in ("error", "warning", "info") else "info",
        count=row.get("count", 1),
    )


class Snapshot(BaseModel):
    id: str
    observed_at: AwareDatetime | None
    collected_at: float
    rows: list[SnapshotItem]
    reason: str | None
    target_generation: str | None = None
    rollback_request: tuple[bool, bool] | None = None


async def _observe(ctx: AppContext, principal: Principal, kind: ItemType,
                   previous: Snapshot | None) -> Snapshot:
    measured = instant(utcnow())
    assert measured is not None
    rows: list[SnapshotItem] = []
    reason = None
    token = observation_read.set(True)
    try:
        if kind == "status":
            rows.append(_system(ctx, measured))
        elif kind == "downloads":
            value = await activity_cached(ctx)
            meta = ctx.services.get("activity_observation")
            measured = instant(meta[1]) if meta and meta[0] is value else None
            if measured is None:
                reason = "meting_ontbreekt"
            elif value["errors"] or not value["summary"].get("download_clients_ok", True):
                reason = "bron_onbereikbaar"
            elif not _stale(measured) and measured <= datetime.now(UTC):
                rows.extend(await asyncio.to_thread(_downloads, ctx, value, measured))
        elif kind == "av1_lanes":
            rows.extend(await _lanes(ctx, measured))
        elif kind == "backups":
            measured = datetime.now(UTC)
            report = await backups.observe(ctx, measured)
            rows.append(BackupItem(
                id="backups", observed_at=measured, deep_link="/systeem#back-ups",
                **report.model_dump(),
            ))
        else:
            source, complete = await attention.collect_observation(ctx)
            visible = attention.for_role(source, principal.role)
            incidents = {item.id: item for item in (_incident(r, measured) for r in visible)}
            rows.extend(incidents.values())
            if not complete:
                reason = "bron_onbereikbaar"
            # Bij een gezonde pool ontbreekt een incidentrij. Ook een lege
            # incidentlijst kan alleen actueel zijn bij een recente bronmeting.
            pool_rows = [r for r in visible if r["key"] == "storage.pool_degraded"]
            if pool_rows:
                pool_at = instant(pool_rows[0].get("observed_at"))
                reachable = pool_rows[0].get("source_reachable")
            else:
                pool = ctx.state_get(POOL_STATE, {}) or {}
                pool_at = instant(pool_observed_at(pool)) if pool else measured
                reachable = not pool or pool.get("reachable") is True
            if pool_at is None:
                measured, reason = None, "meting_ontbreekt"
            elif pool_at > datetime.now(UTC):
                measured, reason = pool_at, "meettijd_ongeldig"
            else:
                measured = min(measured, pool_at)
                if not reachable:
                    reason = "bron_onbereikbaar"
    except (EncoderError, TimeoutError, OSError, ValueError, TypeError):
        reason = "bron_onbereikbaar"
        measured = previous.observed_at if previous else None
    finally:
        observation_read.reset(token)

    if kind == "downloads" and reason == "bron_onbereikbaar":
        # Een mislukte ronde maakt een eerdere succesvolle waarneming nooit verser.
        measured = previous.observed_at if previous else None
    if measured is not None:
        if _stale(measured):
            rows.clear()
            reason = "meting_verouderd"
        elif measured > datetime.now(UTC):
            rows.clear()
            reason = "meettijd_ongeldig"
    rows.sort(key=lambda row: row.id)
    snapshot_id = content_hash([
        kind, measured.isoformat() if measured else None,
        reason, [row.model_dump(mode="json") for row in rows],
    ])
    return Snapshot(id=snapshot_id, observed_at=measured, collected_at=time.monotonic(),
                    rows=rows, reason=reason)


async def _snapshot(ctx: AppContext, principal: Principal, kind: ItemType) -> Snapshot:
    # Elke geautoriseerde rol ziet hetzelfde herstelrapport. Deel de waarneming.
    role = "viewer" if kind == "backups" else principal.role
    slot = f"connector_items:{kind}:{role}"
    cache_seconds = backups.CACHE_SECONDS if kind == "backups" else CACHE_SECONDS

    def cache_state() -> tuple[str | None, tuple[bool, bool] | None]:
        if kind == "backups":
            return offsite.generation(ctx), backups.rollback_request(ctx.settings.data_dir)
        return None, None

    def fresh(previous: Snapshot | None,
              state: tuple[str | None, tuple[bool, bool] | None]) -> bool:
        return (previous is not None
                and (previous.target_generation, previous.rollback_request) == state
                and time.monotonic() - previous.collected_at < cache_seconds)

    current = cache_state()
    previous = ctx.services.get(slot)
    if fresh(previous, current):
        return previous
    lock = ctx.services.setdefault(f"{slot}:lock:{id(asyncio.get_running_loop())}", asyncio.Lock())
    async with lock:
        current = cache_state()
        previous = ctx.services.get(slot)
        if fresh(previous, current):
            return previous
        result = await _observe(ctx, principal, kind, previous)
        result.target_generation, result.rollback_request = current
        ctx.services[slot] = result
        return result


def _snapshot_page(kind: ItemType, snapshot: Snapshot, last: str, limit: int) -> ItemPage:
    rows = snapshot.rows
    reason = snapshot.reason
    if snapshot.observed_at and _stale(snapshot.observed_at):
        rows = []
        reason = "meting_verouderd"
    start = bisect_right(rows, last, key=lambda row: row.id)
    page = rows[start:start + limit]
    return ItemPage(
        type=kind, items=page, observed_at=snapshot.observed_at, snapshot_id=snapshot.id,
        total=None if reason else len(rows), status="unknown" if reason else "ok",
        reason_code=reason, next_cursor=_cursor(kind, page[-1].id, snapshot.id)
        if start + limit < len(rows) else None,
    )


@router.get(
    "/items", response_model=ItemPage,
    responses={
        200: {"headers": {"ETag": {"schema": {"type": "string"}}}},
        401: {"model": ConnectorError, "description": "Aanmelding ontbreekt of is ongeldig."},
        403: {"model": ConnectorError, "description": "Onvoldoende rechten."},
        409: {"model": ConnectorError, "description": "snapshot_changed: begin zonder cursor."},
        413: {"model": ConnectorError, "description": "Verzoek groter dan 1 MiB."},
        422: {"model": ConnectorError, "description": "Onbekend type of ongeldige invoer."},
        500: {"model": ConnectorError, "description": "Interne leesfout."},
        503: DATABASE_UNAVAILABLE,
    },
)
async def get_items(
    request: Request,
    principal: Annotated[Principal, Depends(require_viewer)],
    ctx: Annotated[AppContext, Depends(get_app_context)],
    type_: Annotated[ItemType, Query(alias="type")],
    cursor: Annotated[str | None, Query(pattern=CURSOR_PATTERN, max_length=256)] = None,
    limit: Annotated[int, Query(
        ge=1, le=10**MAX_LIMIT_DIGITS - 1,
        description="Een waarde boven 200 levert hooguit 200 items.",
    )] = 200,
) -> JSONResponse:
    """Lees eigen MediaGuard-data met vaste velden en een cursor per type."""
    faults = query_faults(request, "type", "cursor", "limit")
    raw_limit = request.query_params.get("limit", "200")
    if not re.fullmatch(rf"[0-9]{{1,{MAX_LIMIT_DIGITS}}}", raw_limit):
        faults.append({"veld": ["query", "limit"], "type": "ongeldig_geheel_getal"})
    if faults:
        return failure(422, "validation_error", "Ongeldige invoer", {"fouten": faults})
    limit = min(limit, PAGE_LIMIT)
    last = ""
    snapshot_id = None
    if cursor is not None:
        try:
            last, snapshot_id = _position(type_, cursor)
        except (ValueError, binascii.Error, RecursionError):
            return failure(422, "validation_error", "Ongeldige invoer", {
                "fouten": [{"veld": ["query", "cursor"], "type": "ongeldige_cursor"}],
            })

    if type_ == "wanted":
        page = _wanted(ctx, int(last or 0), limit)
    else:
        if snapshot_id is None:
            snapshot = await _snapshot(ctx, principal, type_)
        else:
            snapshot = ctx.services.get(f"connector_items:{type_}:{principal.role}")
            if (
                not snapshot or snapshot.id != snapshot_id
                or time.monotonic() - snapshot.collected_at > MAX_AGE
                or snapshot.observed_at is None
                or _stale(snapshot.observed_at)
            ):
                return failure(409, "snapshot_changed", "Begin opnieuw zonder cursor.",
                               {"restart": True})
            if not any(row.id == last for row in snapshot.rows):
                return failure(422, "validation_error", "Ongeldige invoer", {
                    "fouten": [{"veld": ["query", "cursor"], "type": "ongeldige_cursor"}],
                })
        page = _snapshot_page(type_, snapshot, last, limit)
    body = page.model_dump(mode="json")
    return JSONResponse(body, headers={"ETag": '"' + content_hash(body) + '"'})
