"""Samenwerkende locks met lease, zodat download/import/encode/recovery nooit tegelijk
op hetzelfde mediabestand of item werken (B2 punt 6, R2 punt 6)."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

from mediaguard.db import Database, utcnow


def media_resource(instance_id: str, kind: str, provider_id: int | str) -> str:
    """Lock-sleutel per (instance, type, provider-item). Native ID's zijn alleen uniek
    binnen een instantie (R2.4), dus instance_id zit er altijd in."""
    return f"{instance_id}:{kind}:{provider_id}"


def acquire(db: Database, resource: str, owner: str, purpose: str,
            ttl_seconds: int = 3600) -> bool:
    now = datetime.now(UTC)
    expires = (now + timedelta(seconds=ttl_seconds)).isoformat(timespec="seconds")
    with db.transaction() as conn:
        row = conn.execute("SELECT owner, expires_at FROM locks WHERE resource=?",
                           (resource,)).fetchone()
        if row is not None and row["owner"] != owner and row["expires_at"] > utcnow():
            return False
        conn.execute(
            "INSERT INTO locks(resource,owner,purpose,acquired_at,expires_at) VALUES (?,?,?,?,?)"
            " ON CONFLICT(resource) DO UPDATE SET owner=excluded.owner, purpose=excluded.purpose,"
            " acquired_at=excluded.acquired_at, expires_at=excluded.expires_at",
            (resource, owner, purpose, utcnow(), expires),
        )
    return True


def release(db: Database, resource: str, owner: str) -> None:
    db.execute("DELETE FROM locks WHERE resource=? AND owner=?", (resource, owner))


def holder(db: Database, resource: str) -> dict[str, str] | None:
    row = db.fetch_one("SELECT * FROM locks WHERE resource=? AND expires_at>?",
                       (resource, utcnow()))
    return dict(row) if row else None
