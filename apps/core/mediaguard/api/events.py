"""Live status via Server-Sent Events, met replay vanaf Last-Event-ID (veilig reconnecten)."""

from __future__ import annotations

import asyncio
import hmac
import json
import re
from collections.abc import AsyncIterator, Iterator

from fastapi import APIRouter, Depends, Request
from fastapi.responses import StreamingResponse

from mediaguard.auth import Principal, require_viewer
from mediaguard.connector.errors import cursor_expired
from mediaguard.context import AppContext, get_app_context
from mediaguard.db import loads
from mediaguard.events import source_id

router = APIRouter(prefix="/api/v1/events", tags=["events"])


def _resume_id(key: bytes, generation: bytes, position: int, birth: bytes | None) -> str:
    identity = source_id(position, generation, birth).encode("ascii")
    signature = hmac.digest(key, b"sse/" + identity, "sha256").hex()
    return f"v3.{position}.{generation.hex()}.{signature}"


def _resume_position(ctx: AppContext, key: bytes, generation: bytes, value: str) -> int | None:
    # Een oud numeriek ID kan na de overgang inmiddels een andere rij aanwijzen.
    match = re.fullmatch(r"v3\.([1-9][0-9]{0,18})\.[a-f0-9]{32}\.[a-f0-9]{64}", value)
    if match and (position := int(match[1])) <= 2**63 - 1:
        row = ctx.db.fetch_one(
            "SELECT e.id, s.birth FROM outbox_events e LEFT JOIN outbox_source s "
            "ON s.event_id=e.id WHERE e.id=?", (position,),
        )
        if row and hmac.compare_digest(value, _resume_id(key, generation, position, row["birth"])):
            return position
    return None


def _latest(ctx: AppContext) -> int:
    return ctx.db.fetch_one("SELECT COALESCE(MAX(id), 0) FROM outbox_events")[0]


def _replay(
    ctx: AppContext, key: bytes, generation: bytes, after: int, through: int,
) -> Iterator[str]:
    while after < through:
        rows = ctx.db.fetch_all(
            "SELECT e.*, s.birth FROM outbox_events e LEFT JOIN outbox_source s "
            "ON s.event_id=e.id WHERE e.id>? AND e.id<=? ORDER BY e.id LIMIT 500",
            (after, through),
        )
        if not rows:
            return
        for row in rows:
            after = row["id"]
            data = {"topic": row["topic"], "instance_id": row["instance_id"],
                    "payload": loads(row["payload"]), "ts": row["ts"]}
            receipt = _resume_id(key, generation, after, row["birth"])
            yield f"id: {receipt}\ndata: {json.dumps(data)}\n\n"


@router.get("/stream")
async def stream(request: Request, _: Principal = Depends(require_viewer),
                 ctx: AppContext = Depends(get_app_context)) -> StreamingResponse:
    """Stuur bij een verlopen Last-Event-ID eerst een reset en herlees daarna de outbox."""
    last_id = request.headers.get("Last-Event-ID") or request.query_params.get("last_id")
    key, generation = ctx.db.fetch_one(
        "SELECT cursor_key, generation FROM outbox_retention "
        "JOIN outbox_generation USING (singleton)"
    )
    initial = _resume_position(ctx, key, generation, last_id) if last_id else _latest(ctx)
    queue = ctx.events.subscribe()

    async def gen() -> AsyncIterator[str]:
        position = initial or 0
        try:
            if initial is None:
                # Een lege id-regel wist ook bij een lege outbox de oude browsercursor.
                yield f"event: reset\nid:\ndata: {cursor_expired().body.decode()}\n\n"
            if last_id:
                end = _latest(ctx)
                for frame in _replay(ctx, key, generation, position, end):
                    yield frame
                position = end
            yield "retry: 3000\n\n"
            while True:
                if await request.is_disconnected():
                    return
                try:
                    event = await asyncio.wait_for(queue.get(), timeout=15)
                except TimeoutError:
                    # Ook een volle wachtrij of een schrijver in een ander proces mag
                    # een bewaard event niet onzichtbaar maken.
                    end = _latest(ctx)
                    for frame in _replay(ctx, key, generation, position, end):
                        yield frame
                    position = end
                    yield ": keepalive\n\n"
                    continue
                prefix = ""
                if "id" in event:
                    target = event["id"]
                    if target <= position:
                        continue
                    for frame in _replay(ctx, key, generation, position, target - 1):
                        yield frame
                    mark = ctx.db.fetch_one(
                        "SELECT birth FROM outbox_source WHERE event_id=?", (target,),
                    )
                    receipt = _resume_id(key, generation, target, mark[0] if mark else None)
                    prefix = f"id: {receipt}\n"
                    position = target
                yield f"{prefix}data: {json.dumps(event, default=str)}\n\n"
        finally:
            ctx.events.unsubscribe(queue)

    return StreamingResponse(gen(), media_type="text/event-stream",
                             headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"})


@router.get("/recent")
def recent(limit: int = 100, _: Principal = Depends(require_viewer),
           ctx: AppContext = Depends(get_app_context)) -> list[dict[str, object]]:
    rows = ctx.db.fetch_dicts("SELECT * FROM outbox_events ORDER BY id DESC LIMIT ?",
                              (min(limit, 1000),))
    for row in rows:
        row["payload"] = loads(row["payload"])
    return rows
