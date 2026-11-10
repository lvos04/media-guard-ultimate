"""Uitvoerschema (S3) en strikte lokale validatie van modelantwoorden.

Het schema dat naar de provider gaat beperkt enums al per casus; de lokale validatie
is leidend en strenger (lengtes, bestaan van evidence, actie per taaktype, geen extra
velden). Alles wat niet exact klopt wordt afgewezen — er wordt niets 'gerepareerd'.
"""

from __future__ import annotations

import json
from collections.abc import Iterable
from typing import Any

from mediaguard.ai.constants import (
    MAX_EVIDENCE_IDS,
    MAX_REASON_CODES,
    MAX_SUMMARY_CHARS,
    REASON_CODES,
    TASK_ACTIONS,
    TASK_TYPES,
    UNCERTAINTY,
)

FIELDS: tuple[str, ...] = (
    "case_id", "task_type", "suggested_action", "reason_codes", "evidence_ids",
    "reasoning_summary", "uncertainty", "requires_manual_review",
)


def output_schema(case_id: str, task_type: str, evidence_ids: Iterable[str]) -> dict[str, Any]:
    """JSON-schema voor één casus (strict-compatibel voor beide providers)."""
    ev = sorted(set(evidence_ids))
    evidence_items: dict[str, Any] = {"type": "string"}
    if ev:
        evidence_items = {"type": "string", "enum": ev}
    return {
        "type": "object",
        "additionalProperties": False,
        "required": list(FIELDS),
        "properties": {
            "case_id": {"type": "string", "enum": [case_id]},
            "task_type": {"type": "string", "enum": [task_type]},
            "suggested_action": {"type": "string", "enum": list(TASK_ACTIONS[task_type])},
            "reason_codes": {"type": "array", "items": {"type": "string",
                                                         "enum": list(REASON_CODES)}},
            "evidence_ids": {"type": "array", "items": evidence_items},
            "reasoning_summary": {"type": "string"},
            "uncertainty": {"type": "string", "enum": list(UNCERTAINTY)},
            "requires_manual_review": {"type": "boolean"},
        },
    }


def parse_json_text(text: str) -> Any:
    """Strikt: de volledige tekst moet één JSON-object zijn (code-fences toegestaan)."""
    stripped = text.strip()
    if stripped.startswith("```"):
        stripped = stripped.strip("`")
        if stripped.lower().startswith("json"):
            stripped = stripped[4:]
        stripped = stripped.strip()
    return json.loads(stripped)


def validate_advice(data: Any, *, case_id: str, task_type: str,
                    case_evidence_ids: Iterable[str],
                    evidence_exists: Any = None) -> tuple[dict[str, Any] | None, list[str]]:
    """Valideert een modelantwoord. Geeft (voorstel, []) of (None, [redenen])."""
    errors: list[str] = []
    if not isinstance(data, dict):
        return None, ["antwoord is geen JSON-object"]
    extra = set(data) - set(FIELDS)
    missing = set(FIELDS) - set(data)
    if extra:
        errors.append(f"onbekende velden: {', '.join(sorted(map(str, extra)))}")
    if missing:
        errors.append(f"ontbrekende velden: {', '.join(sorted(missing))}")
    if errors:
        return None, errors

    if data["case_id"] != case_id:
        errors.append("case_id komt niet overeen")
    if data["task_type"] not in TASK_TYPES or data["task_type"] != task_type:
        errors.append("task_type komt niet overeen")
    action = data["suggested_action"]
    if not isinstance(action, str) or action not in TASK_ACTIONS.get(task_type, ()):
        errors.append(f"onbekende of niet-toegestane actie: {str(action)[:60]}")

    codes = data["reason_codes"]
    if not isinstance(codes, list) or not codes or len(codes) > MAX_REASON_CODES:
        errors.append(f"reason_codes: 1–{MAX_REASON_CODES} codes verwacht")
    else:
        unknown = [c for c in codes if not isinstance(c, str) or c not in REASON_CODES]
        if unknown:
            errors.append(f"onbekende reasoncodes: {', '.join(str(c)[:60] for c in unknown)}")
        if len(set(map(str, codes))) != len(codes):
            errors.append("dubbele reasoncodes")

    allowed_ev = set(case_evidence_ids)
    ev = data["evidence_ids"]
    if not isinstance(ev, list) or len(ev) > MAX_EVIDENCE_IDS:
        errors.append(f"evidence_ids: lijst met max {MAX_EVIDENCE_IDS} items verwacht")
    else:
        bad = [e for e in ev if not isinstance(e, str) or e not in allowed_ev]
        if bad:
            errors.append("onbestaande evidence-ID's: " + ", ".join(str(e)[:60] for e in bad))
        elif evidence_exists is not None:
            missing_local = [e for e in ev if not evidence_exists(e)]
            if missing_local:
                errors.append("evidence-ID's bestaan niet lokaal: " + ", ".join(missing_local))

    summary = data["reasoning_summary"]
    if not isinstance(summary, str) or not summary.strip():
        errors.append("reasoning_summary ontbreekt")
    elif len(summary) > MAX_SUMMARY_CHARS:
        errors.append(f"reasoning_summary langer dan {MAX_SUMMARY_CHARS} tekens")

    if data["uncertainty"] not in UNCERTAINTY:
        errors.append("ongeldige uncertainty")
    if not isinstance(data["requires_manual_review"], bool):
        errors.append("requires_manual_review moet true/false zijn")

    if errors:
        return None, errors
    return {k: data[k] for k in FIELDS}, []
