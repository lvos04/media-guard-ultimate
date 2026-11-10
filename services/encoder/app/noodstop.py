"""Noodstop vanuit MediaGuard (rapport 6, N1).

MediaGuard (core) zet de noodstop door naar de engine. Zolang hij actief is:

- start de planner geen enkele nieuwe job;
- worden lopende mediaprocessen bevroren met SIGSTOP (geen werk verloren) en na het opheffen
  met SIGCONT hervat; de vastloperbewaking en de pogingenteller staan dan stil;
- blijft de stand bewaard in de database, zodat ook een herstarte engine niets start.

Een herstart terwijl de noodstop actief is telt niet als poging of 'onderbreking'.
"""

from __future__ import annotations

import logging
import threading
from datetime import UTC, datetime
from typing import Any

LOGGER = logging.getLogger(__name__)

REASON_PREFIX = "noodstop actief"
FOUTSOORT = "Noodstop"

_STATE: dict[str, Any] = {"active": False, "reason": "", "since": None, "loaded": False}
_LOCK = threading.Lock()


def _now() -> str:
    return datetime.now(UTC).isoformat()


def load(database: Any) -> dict[str, Any]:
    """Lees de bewaarde stand (bij opstart en als vangnet)."""
    try:
        row = database.fetch_one("SELECT actief,reden,sinds FROM noodstop WHERE id=1")
    except Exception:  # noqa: BLE001 - oude database zonder tabel: geen noodstop
        row = None
    with _LOCK:
        if row is not None:
            _STATE.update(active=bool(int(row["actief"] or 0)), reason=str(row["reden"] or ""),
                          since=row["sinds"])
        _STATE["loaded"] = True
        return dict(_STATE)


def state_loaded() -> bool:
    return bool(_STATE["loaded"])


def active() -> bool:
    return bool(_STATE["active"])


def state() -> dict[str, Any]:
    with _LOCK:
        return {"active": bool(_STATE["active"]), "reason": _STATE["reason"],
                "since": _STATE["since"]}


def set_state(database: Any, is_active: bool, reason: str = "") -> dict[str, Any]:
    """Zet de noodstop aan of uit. Idempotent: dezelfde stand opnieuw zetten verandert niets
    (core zet hem bij elke poll opnieuw door als vangnet na een herstart)."""
    with _LOCK:
        changed = bool(_STATE["active"]) != bool(is_active)
        since = _now() if changed and is_active else (_STATE["since"] if is_active else None)
        _STATE.update(active=bool(is_active), reason=reason if is_active else "", since=since,
                      loaded=True)
    database.execute(
        "UPDATE noodstop SET actief=?,reden=?,sinds=?,bijgewerkt=? WHERE id=1",
        (1 if is_active else 0, reason if is_active else None, since, _now()))
    if changed:
        LOGGER.warning("Noodstop %s%s", "AAN" if is_active else "uit",
                       f": {reason}" if is_active and reason else "")
        if not is_active:
            database.execute("UPDATE job SET noodstop_gepauzeerd=0 WHERE noodstop_gepauzeerd=1")
        try:
            from app.planner import wake_planner

            wake_planner()
        except Exception:  # noqa: BLE001
            pass
    return {**state(), "changed": changed, "paused_jobs": paused_count(database)}


def paused_count(database: Any) -> int:
    try:
        row = database.fetch_one("SELECT COUNT(*) FROM job WHERE noodstop_gepauzeerd=1")
    except Exception:  # noqa: BLE001
        return 0
    return int(row[0]) if row is not None else 0


def claim_reason() -> str | None:
    """Reden voor de planner om niets te starten, of None."""
    if not active():
        return None
    reason = str(_STATE["reason"] or "").strip()
    return f"{REASON_PREFIX}: geen nieuwe encodes" + (f" ({reason})" if reason else "")


def _reset_for_tests() -> None:
    with _LOCK:
        _STATE.update(active=False, reason="", since=None, loaded=False)
