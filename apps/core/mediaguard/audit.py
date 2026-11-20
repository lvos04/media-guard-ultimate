"""Audit-tijdlijn: waarom, wanneer, door wie, oude/nieuwe waarde, undo-hint."""

from __future__ import annotations

import sqlite3
from typing import Any

from mediaguard import __version__
from mediaguard.db import Database, dumps, utcnow
from mediaguard.secrets_store import redact

_INSERT = (
    "INSERT INTO audit_events(id,ts,actor,action,target_type,target_id,instance_id,"
    "correlation_id,reason,old_value,new_value,undo_hint{column}) "
    "VALUES (max((SELECT coalesce(max(id),0) FROM audit_events),coalesce((SELECT "
    "CAST(value_json AS INTEGER) FROM system_state WHERE key='evidence:audit_floor'),0))+1,"
    "?,?,?,?,?,?,?,?,?,?,?{value})"
)
AUDIT_INSERT = _INSERT.format(column=",evidence_app_version", value=",?")


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
    timestamp: str | None = None,
) -> int:
    values = (
        timestamp or utcnow(),
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
    )
    try:
        return db.execute_insert(AUDIT_INSERT, (*values, __version__))
    except sqlite3.OperationalError as exc:
        # Een direct herstelde release van vóór migratie 152 heeft de oude auditkolommen.
        if (str(exc) != "table audit_events has no column named evidence_app_version"
                or db.fetch_one("SELECT 1 FROM schema_migrations WHERE version=?",
                                ("152_connector_evidence_version",))):
            raise
        return db.execute_insert(_INSERT.format(column="", value=""), values)
