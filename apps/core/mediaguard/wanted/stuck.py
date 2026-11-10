"""Vastgelopen imports zelf oplossen (set-and-forget).

Een download die al uren 'klaar, wacht op import' of 'import geblokkeerd' staat, komt
zonder ingrijpen nooit verder. Na de ingestelde wachttijd haalt MediaGuard hem uit de
wachtrij, zet de release op de blocklist en laat Sonarr/Radarr opnieuw zoeken; de actie
staat daarna in Activiteit. Tijdelijke weigeringen (MediaGuard even onbereikbaar,
bibliotheek nog vergrendeld tijdens de migratie) worden nooit zo afgehandeld.
"""

from __future__ import annotations

import logging
from datetime import UTC, datetime, timedelta
from typing import TYPE_CHECKING, Any

from mediaguard import emergency
from mediaguard.arr.client import ArrError
from mediaguard.audit import audit
from mediaguard.db import utcnow
from mediaguard.settings_registry import SettingDef

if TYPE_CHECKING:
    from mediaguard.context import AppContext

LOGGER = logging.getLogger(__name__)
STUCK_STATES = {"importpending", "importblocked", "importfailed", "failedpending"}
# weigeringen die vanzelf overgaan: niet weggooien. Ook niet als MediaGuard zelf de import
# uitstelt tot de identiteitscontrole (AI of review) klaar is (verbeterlijst 5).
TRANSIENT_MARKERS = ("MEDIAGUARD_UNAVAILABLE", "niet bereikbaar", "LIBRARY_LOCKED",
                     "vergrendeld", "shadow", "schaduw", "IDENTITY_REVIEW",
                     "IDENTITY_UNCERTAIN", "INSTANCE_SUSPENDED", emergency.REASON_CODE,
                     "Noodstop")


def setting_defs() -> list[SettingDef]:
    return [
        SettingDef("imports.stuck_auto_fix", "bool", True, "Downloaden",
                   "Vastgelopen downloads automatisch oplossen",
                   "Fix stuck downloads automatically",
                   help_nl="Staat een voltooide download te lang op 'wacht op import' of "
                           "'geblokkeerd', dan haalt MediaGuard hem weg, zet de release op de "
                           "blocklist en laat opnieuw zoeken.",
                   scopes=("global", "instance")),
        SettingDef("imports.stuck_after_hours", "int", 6, "Downloaden",
                   "Wachttijd vastgelopen download", "Stuck download grace period",
                   minimum=1, maximum=168, unit="uur", advanced=True,
                   scopes=("global", "instance")),
    ]


def _stuck(q: dict[str, Any]) -> tuple[bool, str]:
    state = str(q.get("trackedDownloadState") or "").lower()
    status = str(q.get("status") or "").lower()
    if state not in STUCK_STATES or status not in ("completed", "warning", "failed"):
        return False, ""
    messages = [m for s in q.get("statusMessages") or [] for m in s.get("messages") or []]
    text = " · ".join([str(q.get("errorMessage") or ""), *map(str, messages)]).strip(" ·")
    if any(m.lower() in text.lower() for m in TRANSIENT_MARKERS):
        return False, text
    return True, text


async def check_instance(ctx: AppContext, instance_id: str) -> dict[str, Any]:
    from mediaguard.instances.registry import arr_client, get_instance

    inst = get_instance(ctx, instance_id)
    if inst is None or inst.suspended or inst.archived:
        return {"checked": 0, "fixed": 0}
    if inst.shadow_mode:
        return {"checked": 0, "fixed": 0}  # proefmodus: nooit iets weggooien
    if emergency.skip(ctx, "vastgelopen downloads oplossen"):
        return {"checked": 0, "fixed": 0, "skipped": "noodstop"}
    chain = {"instance": instance_id}
    enabled = bool(ctx.config.value("imports.stuck_auto_fix", chain))
    hours = int(ctx.config.value("imports.stuck_after_hours", chain))
    async with arr_client(ctx, instance_id) as client:
        queue = await client.queue(all_pages=True)
        records = queue.get("records") if isinstance(queue, dict) else queue
        now = utcnow()
        seen: set[str] = set()
        fixed = 0
        for q in records or []:
            stuck, reason = _stuck(q)
            did = str(q.get("downloadId") or q.get("id"))
            if not stuck:
                continue
            seen.add(did)
            ctx.db.execute(
                "INSERT INTO stuck_import_actions(instance_id,queue_id,download_id,title,reason,"
                "first_seen_at) VALUES (?,?,?,?,?,?) ON CONFLICT(instance_id,download_id) DO "
                "UPDATE SET queue_id=excluded.queue_id, reason=excluded.reason",
                (instance_id, int(q.get("id") or 0), did, q.get("title"), reason[:500], now))
            row = ctx.db.fetch_one(
                "SELECT id, first_seen_at, action FROM stuck_import_actions WHERE "
                "instance_id=? AND download_id=?", (instance_id, did))
            if not enabled or row is None or row["action"] or emergency.active(ctx):
                continue
            since = datetime.fromisoformat(row["first_seen_at"])
            if datetime.now(UTC) - since < timedelta(hours=hours):
                continue
            try:
                await client.queue_remove(int(q["id"]), remove_from_client=True,
                                          blocklist=True)
                action, detail = "removed_blocklisted", "Weggehaald, op de blocklist gezet; " \
                    "Sonarr/Radarr zoekt opnieuw"
                fixed += 1
            except ArrError as exc:
                action, detail = "failed", f"Weghalen mislukt: {exc.message}"
            ctx.db.execute("UPDATE stuck_import_actions SET action=?, acted_at=?, detail=? "
                           "WHERE id=?", (action, utcnow(), detail, row["id"]))
            audit(ctx.db, actor="svc:stuck-imports", action=f"imports.stuck_{action}",
                  target_type="download", target_id=did, instance_id=instance_id,
                  reason=reason[:500], new_value={"title": q.get("title")})
        # niet meer in de wachtrij en nooit afgehandeld: vergeten
        if seen:
            marks = ",".join("?" for _ in seen)
            ctx.db.execute(f"DELETE FROM stuck_import_actions WHERE instance_id=? AND action "
                           f"IS NULL AND download_id NOT IN ({marks})", (instance_id, *seen))
        else:
            ctx.db.execute("DELETE FROM stuck_import_actions WHERE instance_id=? AND action IS "
                           "NULL", (instance_id,))
    return {"checked": len(seen), "fixed": fixed}
