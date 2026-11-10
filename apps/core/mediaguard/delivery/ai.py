"""Beslissingen zonder de gebruiker: de AI kiest tussen opties die allemaal aan de eisen
voldoen. Zonder AI (geen sleutel/toestemming/budget) beslist een vaste regel. De eisen
(kwaliteitsprofiel, talen, DV/HDR, ondertitels) worden nooit versoepeld: alleen opties die
er al aan voldoen worden aangeboden."""

from __future__ import annotations

import json
import logging
from dataclasses import dataclass
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from mediaguard.context import AppContext

LOGGER = logging.getLogger(__name__)

SYSTEM = (
    "Je bent de beslisser van MediaGuard, een mediabeheerder die zonder gebruiker werkt. "
    "Kies precies één van de gegeven opties. Alle opties voldoen al aan de eisen van de "
    "gebruiker; je mag geen andere optie bedenken of eisen versoepelen. Antwoord in JSON "
    "volgens het schema, met een korte Nederlandse uitleg.")
SCHEMA: dict[str, Any] = {
    "type": "object",
    "properties": {"choice": {"type": "string"}, "confidence": {"type": "number"},
                   "reason_nl": {"type": "string"}},
    "required": ["choice", "confidence", "reason_nl"],
    "additionalProperties": False,
}


@dataclass
class Decision:
    choice: str
    reason_nl: str
    by: str          # 'ai:<provider>' of 'regel'
    confidence: float = 1.0


async def _ask(ctx: AppContext, purpose: str, question: str, options: list[str],
               context: dict[str, Any]) -> Decision | None:
    from mediaguard.ai import service as ai
    from mediaguard.automation import ai_ready

    ready = ai_ready(ctx)
    if ready is None:
        return None
    provider, model = ready
    payload = {"vraag": question, "opties": options, "context": context}
    out = await ai.call_structured(
        ctx, provider=provider, model=model, task_type="anime_identity", case_id=None,
        purpose=f"delivery_{purpose}", system=SYSTEM,
        user=json.dumps(payload, ensure_ascii=False), schema=SCHEMA)
    if not out.ok or not isinstance(out.data, dict):
        return None
    choice = str(out.data.get("choice") or "")
    if choice not in options:
        return None
    return Decision(choice, f"AI ({model}): {out.data.get('reason_nl') or choice}",
                    f"ai:{provider}", float(out.data.get("confidence") or 0))


async def decide(ctx: AppContext, purpose: str, question: str, options: list[str],
                 context: dict[str, Any], *, fallback: str, fallback_reason_nl: str,
                 min_confidence: float = 0.6) -> Decision:
    """AI kiest; bij geen/onzeker antwoord de vaste regel. Gooit nooit."""
    if fallback not in options:
        raise ValueError("fallback moet een van de opties zijn")
    if len(options) > 1:
        try:
            got = await _ask(ctx, purpose, question, options, context)
        except Exception:  # noqa: BLE001 - AI mag de levering nooit blokkeren
            LOGGER.exception("AI-beslissing %s mislukt", purpose)
            got = None
        if got is not None and got.confidence >= min_confidence:
            return got
    return Decision(fallback, fallback_reason_nl, "regel")
