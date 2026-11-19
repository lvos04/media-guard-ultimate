"""Toegestane resultaattellers en behoud van historische jobuitkomsten."""

from __future__ import annotations

import sqlite3
from collections.abc import Sequence
from typing import Any

from mediaguard.audit import AUDIT_INSERT
from mediaguard.db import dumps, loads

COUNT_FIELDS = ("episodes", "movies", "failed_items")


def object_value(raw: str | None) -> dict[str, Any]:
    try:
        value = loads(raw)
    except (ValueError, TypeError, RecursionError):
        return {}
    return value if isinstance(value, dict) else {}


def counts(value: dict[str, Any]) -> dict[str, int | None]:
    result = {}
    for key in COUNT_FIELDS:
        count = value.get(key)
        result[key] = count if type(count) is int and 0 <= count <= 2**63 - 1 else None
    return result


def keep_cancelled_outcomes(conn: sqlite3.Connection, ids: Sequence[int]) -> None:
    """Bewaar een nog aantoonbare annulering vóór herplannen of verwijderen van de jobrij."""
    marks = ",".join("?" for _ in ids)
    cancelled = conn.execute(
        "SELECT id,started_at,finished_at,correlation_id FROM jobs "
        f"WHERE id IN ({marks}) AND status='cancelled' AND finished_at IS NOT NULL", ids,
    )
    for job in cancelled:
        target = str(job["id"])
        outcomes = conn.execute(
            "SELECT new_value FROM audit_events a WHERE target_type='job' AND target_id=:target "
            "AND ts=:end AND action='job.finished' AND NOT EXISTS "
            "(SELECT 1 FROM connector_audit_evidence p WHERE p.id=a.id) "
            "UNION ALL SELECT json_extract(package,'$.response') FROM connector_audit_evidence "
            "WHERE job_target=:target AND ts=:end "
            "AND json_extract(package,'$.audit.action')='job.finished'",
            {"target": target, "end": job["finished_at"]},
        )
        if any(object_value(row[0]).get("status") == "cancelled" for row in outcomes):
            continue
        # De oude kern legde de annulerende versie niet vast.
        conn.execute(AUDIT_INSERT, (
            job["finished_at"], "system", "job.finished", "job", target, None,
            job["correlation_id"], None, dumps({"started_at": job["started_at"]}),
            dumps({"status": "cancelled"}), None, None,
        ))
