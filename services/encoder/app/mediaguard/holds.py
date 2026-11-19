"""Kijk- en platformreden voor dezelfde bestaande baanpauze."""

from __future__ import annotations

import sqlite3
from time import time
from typing import Any

from app.db import Database

PLAYBACK_HOLD_PREFIX = "Kijken:"
PLATFORM_HOLD_PREFIX = "Platform:"
MAX_PLATFORM_SECONDS = 6 * 3600


def _platform_reason(row: sqlite3.Row, now: float) -> str | None:
    expiry = row["platform_pauze_tot"]
    if expiry is not None and expiry > now:
        return str(row["platform_pauze_reden"])
    return None


def pause_reason(row: sqlite3.Row, now: float | None = None) -> str | None:
    """Een baan kan na de eindtijd weer claimen, ook zonder een bericht van core."""
    current = str(row["pauze_reden"] or "")
    if row["gepauzeerd"] and not current.startswith(PLATFORM_HOLD_PREFIX):
        return current or "handmatig gepauzeerd"
    return _platform_reason(row, time() if now is None else now)


def apply_playback_hold(database: Database, active: bool, lanes: list[str],
                        reason: str) -> dict[str, Any]:
    """Kijken krijgt voorrang op platformwerk en behoudt andere baanpauzes."""
    out: dict[str, str] = {}
    with database.connection() as conn, conn:
        now = time()
        for row in conn.execute("SELECT * FROM worker_config"):
            lane = str(row["baan"])
            current = pause_reason(row, now)
            watching = (current or "").startswith(PLAYBACK_HOLD_PREFIX)
            if active and lane in lanes:
                if current is None or watching or current.startswith(PLATFORM_HOLD_PREFIX):
                    _set_pause(conn, lane,
                               f"{PLAYBACK_HOLD_PREFIX} {reason or 'iemand kijkt'}"[:200])
                    out[lane] = "wacht"
                else:
                    out[lane] = "al gepauzeerd"
            elif watching:
                platform = _platform_reason(row, now)
                _set_pause(conn, lane, platform)
                out[lane] = "al gepauzeerd" if platform else "hervat"
            else:
                out[lane] = "ongewijzigd"
    return {"active": active, "lanes": out}


def _set_pause(conn: sqlite3.Connection, lane: str, reason: str | None) -> None:
    conn.execute("UPDATE worker_config SET gepauzeerd=?,pauze_reden=? WHERE baan=?",
                 (int(reason is not None), reason, lane))


def expire_platform_hold(database: Database, row: sqlite3.Row) -> None:
    """Verwijder verlopen metadata en alleen de bijbehorende zichtbare reden."""
    expiry = row["platform_pauze_tot"]
    if expiry is None or expiry > time():
        return
    database.execute(
        """
        UPDATE worker_config
        SET gepauzeerd=CASE WHEN pauze_reden LIKE 'Platform:%' THEN 0 ELSE gepauzeerd END,
            pauze_reden=CASE WHEN pauze_reden LIKE 'Platform:%' THEN NULL ELSE pauze_reden END,
            platform_pauze_tot=NULL, platform_pauze_reden=NULL
        WHERE platform_pauze_tot=?
        """,
        (expiry,),
    )


def apply_platform_hold(database: Database, expires_at: int | None,
                        reason: str) -> dict[str, Any]:
    """Bewaar de eindtijd ook als de kijkpauze de platformreden tijdelijk afdekt."""
    with database.connection() as conn, conn:
        now = time()
        expiry = min(expires_at, int(now + MAX_PLATFORM_SECONDS)) if expires_at else None
        if expiry is None or expiry <= now:
            expiry = None
        platform = f"{PLATFORM_HOLD_PREFIX} {reason}"[:200] if expiry is not None else None
        conn.execute("UPDATE worker_config SET platform_pauze_tot=?, platform_pauze_reden=?",
                     (expiry, platform))
        conn.execute(
            "UPDATE worker_config SET gepauzeerd=?, pauze_reden=? "
            "WHERE pauze_reden LIKE 'Platform:%' OR (? AND gepauzeerd=0)",
            (int(platform is not None), platform, platform is not None),
        )
    return {"expires_at": expiry, "reason": platform}


def platform_status(database: Database) -> dict[str, Any]:
    row = database.fetch_one(
        "SELECT platform_pauze_tot, platform_pauze_reden FROM worker_config WHERE baan='cpu'")
    reason = _platform_reason(row, time()) if row is not None else None
    return {"expires_at": row["platform_pauze_tot"] if row is not None and reason else None,
            "reason": reason}
