"""PolicyGuard: de lokale code — niet het taalmodel — beslist wat er met een voorstel gebeurt.

Beslissingen:
- ``log_only``       shadow: alleen loggen en vergelijken, nooit workflowwijziging.
- ``await_approval`` advisory (of bounded_auto die niet aan alle voorwaarden voldoet):
                     zichtbaar in de UI met Accepteer/Wijs af.
- ``auto_execute``   bounded_auto: alleen allowlisted, niet-destructieve, idempotente actie
                     waarvoor een uitvoerder bestaat, ná ontgrendeling (schaduwfase +
                     benchmark), binnen het dagelijkse actiemaximum, zonder noodstop.
- ``blocked``        al het andere.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Any

from mediaguard.ai import actions
from mediaguard.ai import settings as ai_settings
from mediaguard.ai.constants import AUTO_ELIGIBLE_ACTIONS, EXECUTABLE_ACTIONS, TASK_ACTIONS

if TYPE_CHECKING:
    from mediaguard.context import AppContext


@dataclass
class PolicyDecision:
    decision: str
    action: str | None
    executable: bool
    reasons: list[str] = field(default_factory=list)

    def as_dict(self) -> dict[str, Any]:
        return {"decision": self.decision, "action": self.action,
                "executable": self.executable, "reasons": self.reasons}


def actions_executed_today(ctx: AppContext, day_start_utc: str) -> int:
    row = ctx.db.fetch_one(
        "SELECT COUNT(*) AS n FROM ai_policy_results WHERE execution_status='executed' "
        "AND executed_at>=?", (day_start_utc,))
    return int(row["n"]) if row else 0


def evaluate(ctx: AppContext, *, mode: str, proposal: dict[str, Any], task_type: str,
             auto_unlocked: bool, auto_lock_reasons: list[str],
             actions_today: int) -> PolicyDecision:
    action = proposal.get("suggested_action")
    if action not in TASK_ACTIONS.get(task_type, ()):
        return PolicyDecision("blocked", None, False, ["actie niet toegestaan voor dit taaktype"])
    executable = action in EXECUTABLE_ACTIONS
    reasons: list[str] = []
    if task_type == "anime_identity":
        reasons.append("Identiteitsadvies is nooit een bindend verdict; de Identity Engine "
                       "blijft beslissen.")
    if task_type == "encode_advice":
        reasons.append("Encode-advies activeert nooit een profiel; alleen echte proefencodes "
                       "tegen de baseline beslissen.")
    if mode == "shadow":
        return PolicyDecision("log_only", action, executable,
                              [*reasons, "Schaduwmodus: alleen gelogd, geen workflowwijziging."])
    if mode == "advisory":
        reasons.append("Adviesmodus: wacht op goedkeuring in de UI.")
        return PolicyDecision("await_approval", action, executable, reasons)
    if mode != "bounded_auto":
        return PolicyDecision("blocked", action, False, ["AI staat uit"])

    # bounded_auto: elke niet-vervulde voorwaarde valt terug op goedkeuring
    blockers: list[str] = []
    if not auto_unlocked:
        blockers.extend(auto_lock_reasons or ["Beperkt automatisch is nog vergrendeld."])
    allowed = set(ai_settings.get(ctx, "ai.bounded_auto.allowed_actions") or [])
    if action not in AUTO_ELIGIBLE_ACTIONS or action not in allowed:
        blockers.append(f"Actie '{action}' staat niet op de automatische allowlist.")
    if actions.registered(ctx, str(action)) is None:
        blockers.append("Geen uitvoerder geregistreerd voor deze actie.")
    if proposal.get("requires_manual_review"):
        blockers.append("Model vraagt zelf om handmatige beoordeling.")
    if proposal.get("uncertainty") == "high":
        blockers.append("Onzekerheid is hoog.")
    if ctx.emergency_stopped():
        blockers.append("Noodstop is actief.")
    max_per_day = int(ai_settings.get(ctx, "ai.bounded_auto.max_actions_per_day"))
    if actions_today >= max_per_day:
        blockers.append(f"Dagmaximum AI-acties bereikt ({max_per_day}).")
    if blockers:
        return PolicyDecision("await_approval", action, executable, [*reasons, *blockers])
    return PolicyDecision("auto_execute", action, True,
                          [*reasons, "Allowlisted, niet-destructief en ontgrendeld."])
