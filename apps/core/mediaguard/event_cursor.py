"""Lees en toets een outboxpagina met haar generatie en retentiestand in één SQLite-selectie."""

from __future__ import annotations

import hmac
import re
import sqlite3
from dataclasses import dataclass
from typing import Literal

from mediaguard.db import Database
from mediaguard.event_keys import read_key

MAX_SQLITE_ID = 2**63 - 1
_NUMBER = r"(?:0|[1-9][0-9]{0,18})"
_POSITION = rf"{_NUMBER}\.{_NUMBER}"
SIGNED = rf"v3\.{_POSITION}\.[a-f0-9]{{32}}\.[a-f0-9]{{64}}"
CURSOR_PATTERN = rf"^(?:v1\.{_POSITION}|v2\.{_POSITION}\.[a-f0-9]{{64}}|{SIGNED})$"


class ExpiredCursor(ValueError):
    """De aangeleverde positie kan niet meer worden hervat."""


class CursorUnavailable(ValueError):
    """De actuele sleutel of retentiestand is onbruikbaar."""


@dataclass(repr=False)
class EventBatch:
    rows: list[sqlite3.Row]
    key: bytes
    generation: bytes
    channel: Literal["poll", "sse"]
    revision: int
    witness: bytes
    position: int
    anchor: bytes | None

    def sign(self, position: int, revision: int, birth: bytes | None, witness: bytes) -> str:
        body = f"v3.{position}.{revision}.{self.generation.hex()}"
        message = f"{self.channel}/{body}/".encode("ascii") + witness + (birth or b"")
        return f"{body}.{hmac.digest(self.key, message, 'sha256').hex()}"

    def receipt(self, row: sqlite3.Row) -> str:
        return self.sign(row["id"], self.revision, row["birth"], self.witness)

    def next_cursor(self) -> str:
        if self.rows:
            return self.receipt(self.rows[-1])
        return self.sign(self.position, self.revision, self.anchor, self.witness)


def read_batch(db: Database, cursor: str | None, *, channel: Literal["poll", "sse"],
               limit: int, through: int = MAX_SQLITE_ID, newest: bool = False) -> EventBatch:
    position = revision = 0
    if cursor is not None:
        if not re.fullmatch(SIGNED, cursor):
            raise ExpiredCursor
        _, last_id, revision_text, _, _ = cursor.split(".")
        position, revision = int(last_id), int(revision_text)
        if max(position, revision) > MAX_SQLITE_ID:
            raise ExpiredCursor

    # Alleen SSE leest de oorspronkelijke payload. De connector selecteert enkel envelopevelden.
    fields = "e.instance_id, e.payload," if channel == "sse" else ""
    order = "DESC" if newest else "ASC"
    rows = db.fetch_all(
        f"""SELECT r.deleted_through, r.revision, r.cursor_key, g.generation,
                   h.witness, old.witness AS past, a.birth AS anchor,
                   EXISTS(SELECT 1 FROM outbox_deletions
                          WHERE revision > ? AND event_id >= ?) AS lost,
                   COALESCE((SELECT seq FROM sqlite_sequence WHERE name='outbox_events'), 0)
                       AS issued_through,
                   p.*, s.birth
            FROM outbox_retention r JOIN outbox_generation g USING (singleton)
            LEFT JOIN outbox_cursor_history h ON h.revision=r.revision
            LEFT JOIN outbox_cursor_history old ON old.revision=?
            LEFT JOIN outbox_source a ON a.event_id=?
            LEFT JOIN (SELECT e.id, e.ts, {fields} e.topic FROM outbox_events e
                       WHERE e.id>? AND e.id<=? ORDER BY e.id {order} LIMIT ?) p ON TRUE
            LEFT JOIN outbox_source s ON s.event_id=p.id
            WHERE r.singleton=1 ORDER BY p.id""",
        (revision, position, revision, position, position, through, limit),
    )
    if not rows:
        raise CursorUnavailable
    state = rows[0]
    key = read_key(db.path.parent, state["cursor_key"])
    if key is None or any(not isinstance(value, bytes) or len(value) != 16
                          for value in (state["witness"], state["generation"])):
        raise CursorUnavailable
    batch = EventBatch(
        [row for row in rows if row["id"] is not None], key, state["generation"], channel,
        state["revision"], state["witness"], position if cursor else state["deleted_through"],
        state["anchor"],
    )
    if cursor is not None and (
        state["lost"] or position > state["issued_through"] or revision > state["revision"]
        or state["past"] is None or not hmac.compare_digest(
            cursor, batch.sign(position, revision, state["anchor"], state["past"]),
        )
    ):
        raise ExpiredCursor
    return batch
