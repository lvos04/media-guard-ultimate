"""Geredigeerde polling van de bestaande outbox (platformcontract §5–§7)."""

from __future__ import annotations

import hmac
import re
import sqlite3
from datetime import UTC, datetime
from typing import Annotated, Any, Literal
from uuid import NAMESPACE_URL, UUID, uuid5

from fastapi import APIRouter, Depends, Query, Request
from fastapi.responses import JSONResponse
from pydantic import AwareDatetime, BaseModel, ConfigDict, Field

from mediaguard.auth import Principal, require_viewer
from mediaguard.context import AppContext, get_app_context
from mediaguard.events import source_id

from .errors import cursor_expired, failure

router = APIRouter(prefix="/connector/v1")
_NUMBER = r"(?:0|[1-9][0-9]{0,18})"
_POSITION = rf"{_NUMBER}\.{_NUMBER}"
CURSOR_PATTERN = (
    rf"^(?:v1\.{_POSITION}|v2\.{_POSITION}\.[a-f0-9]{{64}}"
    rf"|v3\.{_POSITION}\.[a-f0-9]{{32}}\.[a-f0-9]{{64}})$"
)
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


class ConnectorError(BaseModel):
    code: str
    message: str
    details: dict[str, Any]
    trace_id: UUID


def _envelope(row: sqlite3.Row) -> EventEnvelope:
    identifier = source_id(row["id"], row["generation"], row["birth"])
    event_type, ernst = TOPICS.get(row["topic"], ("media.event.recorded", "info"))
    timestamp = datetime.fromisoformat(row["ts"])
    if timestamp.tzinfo is None:
        timestamp = timestamp.replace(tzinfo=UTC)  # oude timestamps zonder offset waren UTC
    return EventEnvelope(
        event_id=uuid5(NAMESPACE_URL, f"mediaguard:{identifier}"),
        source_event_id=identifier,
        event_type=event_type,
        entity=EventEntity(id=event_type.split(".")[1]),
        timestamp_utc=timestamp.astimezone(UTC),
        trace_id=uuid5(NAMESPACE_URL, f"mediaguard:trace:{identifier}"),
        actor=EventActor(),
        payload=EventPayload(ernst=ernst),
    )


def _cursor(
    key: bytes, generation: bytes, position: int, revision: int, birth: bytes | None,
) -> str:
    body = f"v3.{position}.{revision}.{generation.hex()}"
    signature = hmac.digest(key, body.encode("ascii") + b"/" + (birth or b""), "sha256").hex()
    return f"{body}.{signature}"


@router.get(
    "/events",
    response_model=EventPage,
    responses={
        401: {"model": ConnectorError, "description": "Aanmelding ontbreekt of is ongeldig."},
        403: {"model": ConnectorError, "description": "Onvoldoende rechten."},
        409: {"model": ConnectorError, "description": (
            "cursor_expired bij retentie, een onbekende leespositie of een andere generatie. "
            "Herlees zonder cursor en verwerk de gebeurtenissen idempotent."
        )},
        422: {"model": ConnectorError, "description": "Onbekend, dubbel of ongeldig queryveld."},
        503: {"model": ConnectorError, "description": "De database is tijdelijk niet beschikbaar.",
              "headers": {"Retry-After": {"schema": {"type": "integer", "minimum": 1},
                                          "description": "Wachttijd in seconden."}}},
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
    params = request.query_params
    faults = []
    if params.keys() - {"cursor", "limit"}:
        faults.append({"veld": ["query"], "type": "onbekend_veld"})
    for field in ("cursor", "limit"):
        if len(params.getlist(field)) > 1:
            faults.append({"veld": ["query", field], "type": "dubbel_veld"})
    if not re.fullmatch(r"[0-9]{1,3}", params.get("limit", "200")):
        faults.append({"veld": ["query", "limit"], "type": "ongeldig_geheel_getal"})
    if faults:
        return failure(422, "validation_error", "Ongeldige invoer", {"fouten": faults})
    position, revision = 0, 0
    if cursor is not None:
        _, last_id, revision_text, *_ = cursor.split(".")
        position, revision = int(last_id), int(revision_text)
        if max(position, revision) > MAX_SQLITE_ID:
            return failure(422, "validation_error", "Ongeldige invoer", {
                "fouten": [{"veld": ["query", "cursor"], "type": "greater_than"}],
            })

    # Eén SELECT houdt de retentiestand en de pagina in dezelfde SQLite-momentopname.
    rows = ctx.db.fetch_all(
        """SELECT r.deleted_through, r.revision, r.cursor_key, g.generation,
                  p.id, p.ts, p.topic, s.birth,
                  (SELECT birth FROM outbox_source WHERE event_id = ?) AS anchor,
                  EXISTS(SELECT 1 FROM outbox_deletions
                         WHERE revision > ? AND event_id >= ?) AS lost,
                  COALESCE((SELECT seq FROM sqlite_sequence WHERE name='outbox_events'), 0)
                      AS issued_through
           FROM outbox_retention AS r
           JOIN outbox_generation AS g ON g.singleton = r.singleton
           LEFT JOIN (SELECT id, ts, topic FROM outbox_events WHERE id > ? ORDER BY id LIMIT ?) AS p
               ON TRUE
           LEFT JOIN outbox_source AS s ON s.event_id = p.id
           WHERE r.singleton = 1
           ORDER BY p.id""",
        (position, revision, position, position, limit),
    )
    state = rows[0]
    key, generation = state["cursor_key"], state["generation"]
    if cursor is not None and (
        not hmac.compare_digest(
            cursor, _cursor(key, generation, position, revision, state["anchor"]),
        )
        or position > state["issued_through"] or revision > state["revision"] or state["lost"]
    ):
        return cursor_expired()

    events = [_envelope(row) for row in rows if row["id"] is not None]
    birth = state["anchor"]
    if events:
        birth = rows[-1]["birth"]
        position = rows[-1]["id"]
    elif cursor is None:
        position = state["deleted_through"]
    next_cursor = _cursor(key, generation, position, state["revision"], birth)
    return EventPage(events=events, next_cursor=next_cursor)
