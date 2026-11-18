"""Geredigeerde polling van de bestaande outbox (platformcontract §5–§7)."""

from __future__ import annotations

import sqlite3
from datetime import UTC, datetime
from typing import Annotated, Any, Literal
from uuid import NAMESPACE_URL, UUID, uuid5

from fastapi import APIRouter, Depends, Query
from fastapi.responses import JSONResponse
from pydantic import AwareDatetime, BaseModel, ConfigDict, Field

from mediaguard.auth import Principal, require_viewer
from mediaguard.context import AppContext, get_app_context

from .errors import failure

router = APIRouter(prefix="/connector/v1")
CURSOR_PATTERN = r"^v1\.(0|[1-9][0-9]{0,18})\.(0|[1-9][0-9]{0,18})$"
MAX_SQLITE_ID = 2**63 - 1
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
    source_event_id: str
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


class ConnectorError(BaseModel):
    code: str
    message: str
    details: dict[str, Any]
    trace_id: UUID


def _envelope(row: sqlite3.Row) -> EventEnvelope:
    source_id = str(row["id"])
    event_type, ernst = TOPICS.get(row["topic"], ("media.event.recorded", "info"))
    timestamp = datetime.fromisoformat(row["ts"])
    if timestamp.tzinfo is None:
        timestamp = timestamp.replace(tzinfo=UTC)  # oude timestamps zonder offset waren UTC
    return EventEnvelope(
        event_id=uuid5(NAMESPACE_URL, f"mediaguard:{source_id}"),
        source_event_id=source_id,
        event_type=event_type,
        entity=EventEntity(id=event_type.split(".")[1]),
        timestamp_utc=timestamp.astimezone(UTC),
        trace_id=uuid5(NAMESPACE_URL, f"mediaguard:trace:{source_id}"),
        actor=EventActor(),
        payload=EventPayload(ernst=ernst),
    )


@router.get(
    "/events",
    response_model=EventPage,
    responses={
        401: {"model": ConnectorError, "description": "Aanmelding ontbreekt of is ongeldig."},
        403: {"model": ConnectorError, "description": "Onvoldoende rechten."},
        409: {"model": ConnectorError, "description": "cursor_expired: herstart zonder cursor."},
        422: {"model": ConnectorError, "description": "Ongeldige cursor of limiet."},
    },
)
def poll(
    _: Annotated[Principal, Depends(require_viewer)],
    ctx: Annotated[AppContext, Depends(get_app_context)],
    cursor: Annotated[str | None, Query(
        pattern=CURSOR_PATTERN, max_length=42, description="Cursor uit de vorige pagina.",
    )] = None,
    limit: Annotated[int, Query(ge=1, le=200)] = 200,
) -> EventPage | JSONResponse:
    """Lees een pagina op oplopend bron-ID. Zonder cursor begint de oudste bewaarde rij."""
    position, revision = 0, 0
    if cursor is not None:
        _, last_id, revision_text = cursor.split(".")
        position, revision = int(last_id), int(revision_text)
        if max(position, revision) > MAX_SQLITE_ID:
            return failure(422, "validation_error", "Ongeldige invoer", {
                "fouten": [{"veld": ["query", "cursor"], "type": "greater_than"}],
            })

    # Eén SELECT houdt de retentiestand en de pagina in dezelfde SQLite-momentopname.
    rows = ctx.db.fetch_all(
        """SELECT r.deleted_through, r.revision, p.id, p.ts, p.topic,
                  COALESCE((SELECT seq FROM sqlite_sequence WHERE name='outbox_events'), 0)
                      AS issued_through
           FROM outbox_retention AS r
           LEFT JOIN (SELECT id, ts, topic FROM outbox_events WHERE id > ? ORDER BY id LIMIT ?) AS p
               ON TRUE
           WHERE r.singleton = 1
           ORDER BY p.id""",
        (position, limit),
    )
    state = rows[0]
    if cursor is not None and (
        position > state["issued_through"] or revision > state["revision"]
        or (revision < state["revision"] and position <= state["deleted_through"])
    ):
        return failure(409, "cursor_expired", "De cursor is verlopen. Begin opnieuw zonder cursor.",
                       {"restart": True})

    events = [_envelope(row) for row in rows if row["id"] is not None]
    if events:
        position = rows[-1]["id"]
    elif cursor is None:
        position = state["deleted_through"]
    return EventPage(events=events, next_cursor=f"v1.{position}.{state['revision']}")
