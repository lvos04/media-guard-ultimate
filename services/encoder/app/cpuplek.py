"""Tweede CPU-plek voorzichtig weer toelaten (rapport 7, N2).

In rapport 6 liepen twee 4K-HDR-encodes naast elkaar steeds vast; met één CPU-plek is er
sindsdien niets meer vastgelopen. Sinds rapport 6 krijgt elke plek eigen kernen, maar dat is
op de echte NAS nog niet bewezen. Daarom, zonder interactie:

- Staat er in MediaGuard 2 (of meer) CPU-encodes ingesteld, dan draait de baan eerst met één.
- Na 20 geslaagde CPU-encodes zonder vastloper komt de tweede plek erbij.
- Loopt er daarna iets vast op de CPU-baan, dan gaat hij 24 uur terug naar één plek en begint
  de telling opnieuw.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from typing import Any

from app.jobstatus import DONE_SQL, STATUS_WACHT_OP_COMMIT

PROOF_COUNT = 20
BACKOFF_HOURS = 24


def _last_stall(database: Any) -> str | None:
    try:
        row = database.fetch_one("SELECT MAX(tijd) FROM vastloper WHERE baan='cpu'")
    except Exception:  # noqa: BLE001 - oude database zonder vastlopertabel
        return None
    return str(row[0]) if row is not None and row[0] else None


def successes_since(database: Any, since: str | None) -> int:
    row = database.fetch_one(
        f"SELECT COUNT(*) FROM job WHERE baan='cpu' AND (status IN {DONE_SQL} "
        f"OR status='{STATUS_WACHT_OP_COMMIT}') AND klaar_op IS NOT NULL AND klaar_op>?",
        (since or "",))
    return int(row[0]) if row is not None else 0


def status(database: Any, desired: int) -> dict[str, Any]:
    """Uitleg voor de UI: hoeveel plekken nu, en waarom."""
    from app import afstelling

    if desired <= 1 or not afstelling.get("cpu_tweede_plek_proef"):
        return {"plekken": desired, "uitleg_nl": None}
    last = _last_stall(database)
    if last is not None:
        try:
            recent = datetime.now(UTC) - datetime.fromisoformat(last) < timedelta(
                hours=BACKOFF_HOURS)
        except ValueError:
            recent = False
        if recent:
            return {"plekken": 1, "uitleg_nl": (
                f"Na een vastloper draait de CPU-baan {BACKOFF_HOURS} uur met één plek.")}
    done = successes_since(database, last)
    if done >= PROOF_COUNT:
        return {"plekken": desired, "uitleg_nl": None}
    return {"plekken": 1, "uitleg_nl": (
        f"Tweede CPU-plek komt erbij na {PROOF_COUNT} geslaagde encodes zonder vastloper "
        f"({done} van {PROOF_COUNT}).")}


def lane_cap(database: Any, desired: int) -> int:
    return int(status(database, desired)["plekken"])
