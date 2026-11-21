"""Geredigeerde polling van de bestaande outbox (platformcontract §5–§7)."""

from __future__ import annotations

import re
import sqlite3
from datetime import UTC, datetime
from typing import Annotated, Literal
from uuid import NAMESPACE_URL, UUID, uuid5

from fastapi import APIRouter, Depends, Query, Request
from fastapi.responses import JSONResponse
from pydantic import AwareDatetime, BaseModel, ConfigDict, Field

from mediaguard.auth import Principal, require_viewer
from mediaguard.context import AppContext, get_app_context
from mediaguard.event_cursor import (
    CURSOR_PATTERN,
    MAX_SQLITE_ID,
    CursorUnavailable,
    ExpiredCursor,
    read_batch,
)
from mediaguard.events import source_id

from .errors import DATABASE_UNAVAILABLE, ConnectorError, cursor_expired, failure, query_faults

router = APIRouter(prefix="/connector/v1")
Ernst = Literal["kritiek", "hoog", "middel", "laag", "info"]

# Witte lijst van brontopics. Er gaan geen velden uit de oorspronkelijke payload
# mee. Elk onderwerp krijgt een vaste identiteit binnen deze MediaGuard-bron.
TOPICS: dict[str, tuple[str, Ernst]] = {
    "ai.case.created": ("media.ai_case.created", "info"),
    "ai.case.updated": ("media.ai_case.changed", "info"),
    "analysis.post_import": ("media.analysis.requested", "info"),
    "arr.webhook": ("media.webhook.received", "info"),
    "backfill.run": ("media.backfill.executed", "info"),
    "dv.policy_changed": ("media.dv_policy.changed", "info"),
    "instance.resumed": ("media.instance.restored", "info"),
    "instance.status": ("media.instance.changed", "info"),
    "instance.suspended": ("media.instance.suspended", "laag"),
    "library.synced": ("media.library.synced", "info"),
    "mediaserver.refresh.requested": ("media.refresh.requested", "info"),
    "mediaserver.refresh.failed": ("media.refresh.failed", "hoog"),
    "migration.autopilot": ("media.migration.changed", "info"),
    "migration.step": ("media.migration.progressed", "info"),
    "policy.profile_changed": ("media.policy.changed", "info"),
    "provisioning.applied": ("media.provisioning.applied", "info"),
    "recovery.aborted": ("media.recovery.blocked", "hoog"),
    "recovery.committed": ("media.recovery.succeeded", "info"),
    "recovery.rolled_back": ("media.recovery.failed", "hoog"),
    "routing.anime_moved": ("media.routing.moved", "info"),
    "settings.changed": ("media.settings.changed", "info"),
    "system.emergency_stop": ("media.emergency_stop.changed", "middel"),
    "wanted.unavailable": ("media.wanted.blocked", "middel"),
}


class EventEntity(BaseModel):
    model_config = ConfigDict(extra="forbid", json_schema_serialization_defaults_required=True)

    type: Literal["media_component"] = "media_component"
    id: str


class EventActor(BaseModel):
    model_config = ConfigDict(extra="forbid", json_schema_serialization_defaults_required=True)

    type: Literal["system"] = "system"
    id: Literal["mediaguard"] = "mediaguard"


class EventPayload(BaseModel):
    model_config = ConfigDict(extra="forbid")

    ernst: Ernst


class EventEnvelope(BaseModel):
    model_config = ConfigDict(extra="forbid", json_schema_serialization_defaults_required=True)

    schema_version: Literal["1.0"] = "1.0"
    event_id: UUID
    source_app: Literal["mediaguard"] = "mediaguard"
    source_event_id: str = Field(description="Bron-ID binnen de generatie van de outbox.")
    event_type: str = Field(pattern=r"^media\.[a-z0-9_]+\.[a-z0-9_]+$", max_length=128)
    entity: EventEntity
    timestamp_utc: AwareDatetime
    trace_id: UUID
    correlation_id: UUID | None = None
    actor: EventActor
    classification: Literal["internal"] = "internal"
    payload: EventPayload
    payload_ref: None = None


class EventPage(BaseModel):
    events: list[EventEnvelope] = Field(max_length=200)
    next_cursor: str


def _envelope(row: sqlite3.Row) -> EventEnvelope:
    identifier = source_id(row["id"], row["generation"], row["birth"])
    event_type, ernst = TOPICS.get(row["topic"], ("media.event.recorded", "info"))
    timestamp = datetime.fromisoformat(row["ts"])
    if timestamp.tzinfo is None:
        timestamp = timestamp.replace(tzinfo=UTC)  # oude timestamps zonder offset waren UTC
    txn = row["refresh_transaction"]
    return EventEnvelope(
        event_id=uuid5(NAMESPACE_URL, f"mediaguard:{identifier}"),
        source_event_id=identifier,
        event_type=event_type,
        entity=EventEntity(id=event_type.split(".")[1]),
        timestamp_utc=timestamp.astimezone(UTC),
        trace_id=uuid5(NAMESPACE_URL, f"mediaguard:trace:{identifier}"),
        correlation_id=UUID(int=txn) if isinstance(txn, int) and 0 < txn <= MAX_SQLITE_ID else None,
        actor=EventActor(),
        payload=EventPayload(ernst=ernst),
    )


@router.get(
    "/events",
    response_model=EventPage,
    responses={
        401: {"model": ConnectorError, "description": "Aanmelding ontbreekt of is ongeldig."},
        403: {"model": ConnectorError, "description": "Onvoldoende rechten."},
        409: {"model": ConnectorError, "description": (
            "cursor_expired bij een onbruikbare sleutel, retentiegat of onbekende leespositie. "
            "Herlees zonder cursor en verwerk de gebeurtenissen idempotent."
        )},
        422: {"model": ConnectorError, "description": "Onbekend, dubbel of ongeldig queryveld."},
        503: DATABASE_UNAVAILABLE,
    },
)
def poll(
    request: Request,
    _: Annotated[Principal, Depends(require_viewer)],
    ctx: Annotated[AppContext, Depends(get_app_context)],
    cursor: Annotated[str | None, Query(
        pattern=CURSOR_PATTERN, max_length=140, description="Cursor uit de vorige pagina.",
    )] = None,
    limit: Annotated[int, Query(ge=1, le=200)] = 200,
) -> EventPage | JSONResponse:
    """Lees een pagina op oplopend bron-ID. Zonder cursor begint de oudste bewaarde rij."""
    faults = query_faults(request, "cursor", "limit")
    if not re.fullmatch(r"[0-9]{1,3}", request.query_params.get("limit", "200")):
        faults.append({"veld": ["query", "limit"], "type": "ongeldig_geheel_getal"})
    if faults:
        return failure(422, "validation_error", "Ongeldige invoer", {"fouten": faults})
    if cursor is not None:
        _, last_id, revision_text, *_ = cursor.split(".")
        if max(int(last_id), int(revision_text)) > MAX_SQLITE_ID:
            return failure(422, "validation_error", "Ongeldige invoer", {
                "fouten": [{"veld": ["query", "cursor"], "type": "greater_than"}],
            })

    try:
        batch = read_batch(ctx.db, cursor, channel="poll", limit=limit)
    except (ExpiredCursor, CursorUnavailable):
        return cursor_expired()
    return EventPage(events=[_envelope(row) for row in batch.rows], next_cursor=batch.next_cursor())
