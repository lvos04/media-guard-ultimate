"""Live status via Server-Sent Events, met replay vanaf Last-Event-ID (veilig reconnecten)."""

from __future__ import annotations

import asyncio
import json
import sqlite3
from collections.abc import AsyncIterator

from fastapi import APIRouter, Depends, Request
from fastapi.responses import JSONResponse, StreamingResponse

from mediaguard.auth import Principal, require_viewer
from mediaguard.connector.errors import (
    DATABASE_UNAVAILABLE,
    ConnectorError,
    cursor_expired,
    database_failure,
    temporary_database_error,
)
from mediaguard.context import AppContext, get_app_context
from mediaguard.db import loads
from mediaguard.event_cursor import CursorUnavailable, EventBatch, ExpiredCursor, read_batch

router = APIRouter(prefix="/api/v1/events", tags=["events"])


def _reset() -> str:
    return f"event: reset\nid:\ndata: {cursor_expired().body.decode()}\n\n"


def _latest(ctx: AppContext) -> int:
    return ctx.db.fetch_one("SELECT COALESCE(MAX(id), 0) FROM outbox_events")[0]


@router.get("/stream", response_model=None, response_class=StreamingResponse, responses={
    200: {"content": {"text/event-stream": {"schema": {"type": "string"}}}},
    409: {"model": ConnectorError, "description": "De cursorsleutel is onbruikbaar."},
    503: DATABASE_UNAVAILABLE,
})
async def stream(request: Request, _: Principal = Depends(require_viewer),
                 ctx: AppContext = Depends(get_app_context)) -> StreamingResponse | JSONResponse:
    """Stuur bij een verlopen Last-Event-ID eerst een reset en herlees daarna de outbox."""
    last_id = request.headers.get("Last-Event-ID") or request.query_params.get("last_id")
    cursor = last_id
    reset = False
    try:
        end = _latest(ctx)
        try:
            first = read_batch(ctx.db, cursor, channel="sse", through=end,
                               limit=500 if last_id else 1, newest=not last_id)
        except ExpiredCursor:
            reset = True
            cursor = None
            first = read_batch(ctx.db, None, channel="sse", through=end, limit=500)
    except CursorUnavailable:
        return cursor_expired()
    except sqlite3.OperationalError as exc:
        if response := database_failure("/api/v1/events/stream", exc):
            return response
        raise

    async def replay(through: int, batch: EventBatch | None = None,
                     live_id: int | None = None) -> AsyncIterator[str]:
        nonlocal cursor
        while True:
            try:
                if batch is None:
                    batch = read_batch(ctx.db, cursor, channel="sse", through=through, limit=500)
            except ExpiredCursor:
                cursor = None
                yield _reset()
                continue
            except sqlite3.OperationalError as exc:
                if not temporary_database_error(exc):
                    raise
                yield ": keepalive\n\n"
                await asyncio.sleep(1)
                continue
            for row in batch.rows:
                data = {"topic": row["topic"], "instance_id": row["instance_id"],
                        "payload": loads(row["payload"]), "ts": row["ts"]}
                if row["id"] == live_id:
                    data["id"] = live_id
                cursor = batch.receipt(row)
                yield f"id: {cursor}\ndata: {json.dumps(data)}\n\n"
            if len(batch.rows) < 500 or batch.rows[-1]["id"] >= through:
                cursor = batch.next_cursor()
                return
            batch = None

    queue = ctx.events.subscribe()

    async def gen() -> AsyncIterator[str]:
        nonlocal cursor
        try:
            if reset:
                yield _reset()
            if last_id:
                async for frame in replay(end, first):
                    yield frame
            else:
                cursor = first.next_cursor()
            yield "retry: 3000\n\n"
            while not await request.is_disconnected():
                try:
                    event = await asyncio.wait_for(queue.get(), timeout=15)
                except TimeoutError:
                    try:
                        # Ook events van andere processen en een gemiste wachtrijmelding ophalen.
                        async for frame in replay(_latest(ctx)):
                            yield frame
                    except sqlite3.OperationalError as exc:
                        if not temporary_database_error(exc):
                            raise
                    yield ": keepalive\n\n"
                    continue
                if "id" in event:
                    async for frame in replay(event["id"], live_id=event["id"]):
                        yield frame
                else:
                    yield f"data: {json.dumps(event, default=str)}\n\n"
        except CursorUnavailable:
            # De headers zijn al verstuurd. Wis de browserpositie vóór het opnieuw verbinden.
            yield _reset()
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
