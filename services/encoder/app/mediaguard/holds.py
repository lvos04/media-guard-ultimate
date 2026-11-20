"""Brongebonden pauzes op de twee encoderbanen."""

from __future__ import annotations

import json
import sqlite3
from time import time
from typing import Any

from app.db import Database

PLAYBACK_HOLD_PREFIX = "Kijken:"
PLATFORM_HOLD_PREFIX = "Platform:"
MAX_PLATFORM_SECONDS = 6 * 3600
MIN_CHANGE_SECONDS = 15 * 60
LANES = ("cpu", "gpu")
# De zichtbare reden blijft compatibel met eerdere releases. Overlappende redenen
# staan in de bestaande tabel voor duurzame encoderstaat, in dezelfde transactie.
LANE_SELECT = """
    SELECT w.*, s.waarde AS overige_redenen FROM worker_config w
    LEFT JOIN staat s ON s.sleutel='baanpauzes:' || w.baan
"""


class PlatformHoldConflict(ValueError):
    """Er is nog geen kwartier verstreken sinds de laatste wijziging."""


def _source(reason: str) -> str:
    if reason.startswith(PLAYBACK_HOLD_PREFIX):
        return "kijken"
    if reason.startswith("Jellyfin:"):
        return "jellyfin"
    if reason.startswith(PLATFORM_HOLD_PREFIX):
        return "platform"
    if reason.startswith(("automatisch: cache", "automatisch: opslag")):
        return "opslag"
    if reason.startswith("MediaGuard:"):
        return "capaciteit"
    if reason == "tijdelijk: wachtrijherstel":
        return "onderhoud"
    return "handmatig"


def lane_reasons(row: sqlite3.Row, now: float | None = None) -> dict[str, str]:
    reasons: dict[str, str] = {}
    if row["gepauzeerd"]:
        reason = str(row["pauze_reden"] or "handmatig gepauzeerd")
        reasons[_source(reason)] = reason
    if row["overige_redenen"]:
        reasons.update(json.loads(row["overige_redenen"]))
    expiry = row["platform_pauze_tot"]
    if expiry is not None and expiry > (time() if now is None else now):
        reasons["platform"] = str(row["platform_pauze_reden"])
    else:
        reasons.pop("platform", None)
    return reasons


def _visible(reasons: dict[str, str]) -> str | None:
    # De eerst zichtbare handmatige pauze blijft herkenbaar bij een opslagstoring.
    first = next(iter(reasons), None)
    if first == "handmatig":
        return reasons[first]
    for source in ("opslag", "capaciteit"):
        if source in reasons:
            return reasons[source]
    if first == "platform":
        return reasons.get("kijken") or reasons.get("jellyfin") or reasons[first]
    return reasons[first] if first else None


def pause_reason(row: sqlite3.Row) -> str | None:
    return _visible(lane_reasons(row))


def set_reason(conn: sqlite3.Connection, lane: str, source: str, reason: str | None) -> str | None:
    row = conn.execute(LANE_SELECT + " WHERE w.baan=?", (lane,)).fetchone()
    if row is None:
        return None
    reasons = lane_reasons(row)
    if reason is None:
        reasons.pop(source, None)
    else:
        reasons[source] = reason
    visible = _visible(reasons)
    others = {s: r for s, r in reasons.items() if r != visible and s != "platform"}
    key = f"baanpauzes:{lane}"
    if others:
        value = json.dumps(others, ensure_ascii=False)
        if value != row["overige_redenen"]:
            conn.execute("INSERT INTO staat(sleutel,waarde) VALUES (?,?) "
                         "ON CONFLICT(sleutel) DO UPDATE SET waarde=excluded.waarde", (key, value))
    elif row["overige_redenen"] is not None:
        conn.execute("DELETE FROM staat WHERE sleutel=?", (key,))
    if (bool(row["gepauzeerd"]), row["pauze_reden"]) != (visible is not None, visible):
        conn.execute("UPDATE worker_config SET gepauzeerd=?,pauze_reden=? WHERE baan=?",
                     (int(visible is not None), visible, lane))
    return visible


def set_hold(database: Database, source: str, reason: str | None,
             lanes: tuple[str, ...] = LANES) -> None:
    with database.connection() as conn, conn:
        for lane in lanes:
            set_reason(conn, lane, source, reason)


def apply_playback_hold(database: Database, active: bool, lanes: list[str],
                        reason: str) -> dict[str, Any]:
    out: dict[str, str] = {}
    with database.connection() as conn, conn:
        for row in conn.execute(LANE_SELECT).fetchall():
            lane = str(row["baan"])
            watching = active and lane in lanes
            if not watching and "kijken" not in lane_reasons(row):
                out[lane] = "ongewijzigd"
                continue
            text = f"{PLAYBACK_HOLD_PREFIX} {reason or 'iemand kijkt'}"[:200] if watching else None
            visible = set_reason(conn, lane, "kijken", text)
            if visible is None:
                out[lane] = "hervat"
            else:
                out[lane] = "wacht" if visible == text else "al gepauzeerd"
    return {"active": active, "lanes": out}


def expire_platform_hold(database: Database, row: sqlite3.Row) -> None:
    expiry = row["platform_pauze_tot"]
    if expiry is not None and expiry <= time():
        with database.connection() as conn, conn:
            for lane in LANES:
                changed = conn.execute(
                    "UPDATE worker_config SET platform_pauze_tot=NULL,platform_pauze_reden=NULL "
                    "WHERE baan=? AND platform_pauze_tot=?", (lane, expiry))
                if changed.rowcount:
                    set_reason(conn, lane, "platform", None)


def apply_platform_hold(database: Database, expires_at: int | None,
                        reason: str) -> dict[str, Any]:
    with database.connection() as conn, conn:
        now = time()
        row = conn.execute(LANE_SELECT + " WHERE w.baan='cpu'").fetchone()
        current = lane_reasons(row, now).get("platform")
        status = {"reason": current, "expires_at": row["platform_pauze_tot"] if current else None}
        command = json.dumps([expires_at, reason if expires_at else ""], ensure_ascii=False)
        last = conn.execute(
            "SELECT waarde,tijd FROM staat WHERE sleutel='platformopdracht'").fetchone()
        if last and last["waarde"] == command:
            return status
        # Een verlopen herlevering mag ook een latere, nog actieve reden niet opheffen.
        if expires_at is not None and expires_at <= now:
            return status
        if expires_at is None and not current:
            return status
        expiry = min(expires_at, int(now + MAX_PLATFORM_SECONDS)) if expires_at else None
        platform = f"{PLATFORM_HOLD_PREFIX} {reason}"[:200] if expiry else None
        if status == {"reason": platform, "expires_at": expiry}:
            return status
        if last and now - float(last["tijd"]) < MIN_CHANGE_SECONDS:
            raise PlatformHoldConflict("Wacht minstens 15 minuten tussen platformwijzigingen.")
        conn.execute("UPDATE worker_config SET platform_pauze_tot=?,platform_pauze_reden=?",
                     (expiry, platform))
        for lane in LANES:
            set_reason(conn, lane, "platform", platform)
        conn.execute("INSERT INTO staat(sleutel,waarde,tijd) VALUES ('platformopdracht',?,?) "
                     "ON CONFLICT(sleutel) DO UPDATE SET waarde=excluded.waarde,tijd=excluded.tijd",
                     (command, str(now)))
        return {"expires_at": expiry, "reason": platform}


def platform_status(database: Database) -> dict[str, Any]:
    row = database.fetch_one(LANE_SELECT + " WHERE w.baan='cpu'")
    reason = lane_reasons(row).get("platform") if row is not None else None
    return {"expires_at": row["platform_pauze_tot"] if row is not None and reason else None,
            "reason": reason}
