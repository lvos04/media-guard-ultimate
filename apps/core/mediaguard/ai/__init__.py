"""Optionele, budgetbegrensde AI Decision Advisor (DEEL S).

Integratie-API voor andere modules (allemaal veilig aan te roepen, ook als AI uit staat):

- :func:`request_advice` — alleen bij een expliciete needs_advice-reasoncode van een
  deterministische classifier. Gooit nooit; geeft een ticket terug.
- :func:`register_evidence` — meld een lokaal evidence-object aan (ID + korte samenvatting).
- :func:`register_evidence_provider` — of laat bestaan controleren in je eigen tabellen.
- :func:`register_action` — koppel een niet-destructieve vervolgactie (bv. request_search).
"""

from mediaguard.ai.actions import ActionRequest, register_action
from mediaguard.ai.evidence import register_evidence, register_evidence_provider
from mediaguard.ai.service import analyze_case, request_advice, request_advice_and_analyze

__all__ = [
    "ActionRequest",
    "analyze_case",
    "register_action",
    "register_evidence",
    "register_evidence_provider",
    "request_advice",
    "request_advice_and_analyze",
]
