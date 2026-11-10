"""Audit-tijdlijn: waarom, wanneer, door wie, oude/nieuwe waarde, undo-hint."""

from __future__ import annotations

from typing import Any

from mediaguard.db import Database, dumps, utcnow
from mediaguard.secrets_store import redact


def audit(
    db: Database,
    *,
    actor: str,
    action: str,
    target_type: str | None = None,
    target_id: str | int | None = None,
    instance_id: str | None = None,
    correlation_id: str | None = None,
    reason: str | None = None,
    old_value: Any = None,
    new_value: Any = None,
    undo_hint: str | None = None,
) -> int:
    return db.execute_insert(
        "INSERT INTO audit_events(ts,actor,action,target_type,target_id,instance_id,"
        "correlation_id,reason,old_value,new_value,undo_hint) VALUES (?,?,?,?,?,?,?,?,?,?,?)",
        (
            utcnow(),
            actor,
            action,
            target_type,
            None if target_id is None else str(target_id),
            instance_id,
            correlation_id,
            redact(reason) if reason else None,
            None if old_value is None else redact(dumps(old_value)),
            None if new_value is None else redact(dumps(new_value)),
            undo_hint,
        ),
    )
