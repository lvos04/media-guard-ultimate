"""In-proces eventbus + persistente outbox. De UI volgt live via SSE (/api/v1/events/stream)."""

from __future__ import annotations

import asyncio
import contextlib
from typing import Any

from mediaguard.db import Database, dumps, utcnow


class EventBus:
    def __init__(self, db: Database) -> None:
        self.db = db
        self._subscribers: set[asyncio.Queue[dict[str, Any]]] = set()

    def publish(self, topic: str, payload: dict[str, Any], *, instance_id: str | None = None,
                persist: bool = True) -> None:
        event = {"topic": topic, "instance_id": instance_id, "payload": payload, "ts": utcnow()}
        if persist:
            event["id"] = self.db.execute_insert(
                "INSERT INTO outbox_events(ts,topic,instance_id,payload) VALUES (?,?,?,?)",
                (event["ts"], topic, instance_id, dumps(payload)),
            )
        for queue in list(self._subscribers):
            with contextlib.suppress(asyncio.QueueFull):
                queue.put_nowait(event)

    def subscribe(self) -> asyncio.Queue[dict[str, Any]]:
        queue: asyncio.Queue[dict[str, Any]] = asyncio.Queue(maxsize=500)
        self._subscribers.add(queue)
        return queue

    def unsubscribe(self, queue: asyncio.Queue[dict[str, Any]]) -> None:
        self._subscribers.discard(queue)
