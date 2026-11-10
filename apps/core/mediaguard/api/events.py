"""Live status via Server-Sent Events, met replay vanaf Last-Event-ID (veilig reconnecten)."""

from __future__ import annotations

import asyncio
import json
from collections.abc import AsyncIterator

from fastapi import APIRouter, Depends, Request
from fastapi.responses import StreamingResponse

from mediaguard.auth import Principal, require_viewer
from mediaguard.context import AppContext, get_app_context
from mediaguard.db import loads

router = APIRouter(prefix="/api/v1/events", tags=["events"])


@router.get("/stream")
async def stream(request: Request, _: Principal = Depends(require_viewer),
                 ctx: AppContext = Depends(get_app_context)) -> StreamingResponse:
    last_id = request.headers.get("Last-Event-ID") or request.query_params.get("last_id")
    queue = ctx.events.subscribe()

    async def gen() -> AsyncIterator[str]:
        try:
            if last_id and last_id.isdigit():
                for row in ctx.db.fetch_all(
                    "SELECT * FROM outbox_events WHERE id>? ORDER BY id LIMIT 500", (int(last_id),)
                ):
                    data = {"topic": row["topic"], "instance_id": row["instance_id"],
                            "payload": loads(row["payload"]), "ts": row["ts"]}
                    yield f"id: {row['id']}\ndata: {json.dumps(data)}\n\n"
            yield "retry: 3000\n\n"
            while True:
                if await request.is_disconnected():
                    return
                try:
                    event = await asyncio.wait_for(queue.get(), timeout=15)
                except TimeoutError:
                    yield ": keepalive\n\n"
                    continue
                prefix = f"id: {event['id']}\n" if "id" in event else ""
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
