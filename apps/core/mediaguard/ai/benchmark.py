"""Benchmark-harnas (S5.11): gelabelde ambiguë anime- en missing-cases.

Voert de geconfigureerde provider uit tegen de gelabelde set, vergelijkt met de
deterministische baseline (wat de regels zonder AI zouden adviseren) en slaat het resultaat
op in ``ai_model_evaluations``. ``bounded_auto`` blijft vergrendeld tenzij de laatste
evaluatie de drempel én een meetbare verbetering haalt.

CLI: ``python -m mediaguard.ai.benchmark --provider anthropic [--model X] [--limit N]``
"""

from __future__ import annotations

import argparse
import asyncio
import json
from pathlib import Path
from typing import TYPE_CHECKING, Any

from mediaguard.ai import settings as ai_settings
from mediaguard.ai.privacy import minimize
from mediaguard.ai.prompt import build_prompt
from mediaguard.ai.schema import validate_advice
from mediaguard.ai.service import call_structured, enable_blockers
from mediaguard.audit import audit
from mediaguard.db import dumps, utcnow
from mediaguard.jobs import JobContext, job_handler

if TYPE_CHECKING:
    from mediaguard.context import AppContext

DEFAULT_PATH = Path(__file__).parent / "benchmark_cases_v1.json"


def load_cases(path: Path | None = None) -> dict[str, Any]:
    data: dict[str, Any] = json.loads((path or DEFAULT_PATH).read_text(encoding="utf-8"))
    return data


def score(answer: dict[str, Any] | None, expected: dict[str, Any]) -> bool:
    if not answer:
        return False
    action_ok = answer.get("suggested_action") in expected["actions"]
    codes_ok = bool(set(answer.get("reason_codes") or []) & set(expected["reason_codes"]))
    return bool(action_ok and codes_ok)


async def run_benchmark(ctx: AppContext, *, provider: str | None = None,
                        model: str | None = None, limit: int | None = None,
                        path: Path | None = None) -> dict[str, Any]:
    provider = provider or str(ai_settings.get(ctx, "ai.provider"))
    model = (model or ai_settings.provider_model(ctx, provider)).strip()
    blockers = enable_blockers(ctx, provider, model, require_test=False)
    if blockers:
        return {"ok": False, "blockers": blockers, "message_nl": " ".join(blockers)}
    suite = load_cases(path)
    cases = suite["cases"][:limit] if limit else suite["cases"]
    details: list[dict[str, Any]] = []
    correct = baseline_correct = schema_valid = 0
    total_cost = 0.0
    aborted: str | None = None
    for case in cases:
        payload, _ = minimize(case["payload"])
        ev = {e["id"]: {"kind": e["kind"], "summary": e["summary"]} for e in case["evidence"]}
        case_id = f"bench-{case['id']}"
        system, user, schema = build_prompt(case_id=case_id, task_type=case["task_type"],
                                            reason_code=case["reason_code"], payload=payload,
                                            evidence=ev)
        base_ok = score(case["baseline"], case["expected"])
        baseline_correct += int(base_ok)
        if aborted:
            details.append({"id": case["id"], "skipped": True, "baseline_correct": base_ok})
            continue
        outcome = await call_structured(ctx, provider=provider, model=model,
                                        task_type=case["task_type"], case_id=None,
                                        purpose="benchmark", system=system, user=user,
                                        schema=schema)
        total_cost += outcome.cost
        answer, errors = (None, [outcome.message_nl])
        if outcome.ok:
            answer, errors = validate_advice(outcome.data, case_id=case_id,
                                             task_type=case["task_type"],
                                             case_evidence_ids=ev.keys())
        elif (outcome.error_kind or "").startswith("budget_"):
            aborted = outcome.message_nl
        schema_valid += int(answer is not None)
        ok = score(answer, case["expected"])
        correct += int(ok)
        details.append({"id": case["id"], "task_type": case["task_type"], "correct": ok,
                        "baseline_correct": base_ok, "schema_valid": answer is not None,
                        "suggested_action": answer.get("suggested_action") if answer else None,
                        "reason_codes": answer.get("reason_codes") if answer else None,
                        "errors": errors if answer is None else []})
    total = len(cases)
    accuracy = correct / total if total else 0.0
    baseline = baseline_correct / total if total else 0.0
    threshold = float(ai_settings.get(ctx, "ai.bounded_auto.min_benchmark_accuracy"))
    min_imp = float(ai_settings.get(ctx, "ai.bounded_auto.min_improvement"))
    passed = (not aborted and total > 0 and schema_valid == total and accuracy >= threshold
              and accuracy - baseline >= min_imp)
    eval_id = ctx.db.execute_insert(
        "INSERT INTO ai_model_evaluations(ts,provider,model,benchmark_version,total,correct,"
        "schema_valid,accuracy,baseline_accuracy,improvement,threshold,passed,cost,details_json)"
        " VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
        (utcnow(), provider, model, suite["version"], total, correct, schema_valid,
         round(accuracy, 4), round(baseline, 4), round(accuracy - baseline, 4), threshold,
         int(passed), round(total_cost, 6), dumps(details)))
    return {"ok": True, "id": eval_id, "provider": provider, "model": model,
            "benchmark_version": suite["version"], "total": total, "correct": correct,
            "schema_valid": schema_valid, "accuracy": round(accuracy, 4),
            "baseline_accuracy": round(baseline, 4),
            "improvement": round(accuracy - baseline, 4), "threshold": threshold,
            "passed": passed, "cost": round(total_cost, 6), "aborted": aborted,
            "details": details,
            "message_nl": ("Benchmark gehaald: Beperkt automatisch kan ontgrendeld worden."
                           if passed else "Benchmark niet gehaald: Beperkt automatisch blijft "
                           "vergrendeld; AI blijft optionele uitleg/adviseur.")}


@job_handler("ai.benchmark")
async def _benchmark_job(jc: JobContext) -> dict[str, Any]:
    """Automatische benchmark (zero-interactie ontgrendeling van Beperkt automatisch)."""
    result = await run_benchmark(jc.app, provider=jc.payload.get("provider"),
                                 model=jc.payload.get("model"))
    audit(jc.app.db, actor="ai:auto", action="ai.benchmark.run", target_type="ai_provider",
          target_id=str(result.get("provider") or jc.payload.get("provider") or ""),
          reason=str(result.get("message_nl") or ""))
    return {"passed": bool(result.get("passed")), "ok": bool(result.get("ok"))}


def main() -> None:  # pragma: no cover - CLI
    from mediaguard.app import _load_modules, build_context
    from mediaguard.config import load_settings

    parser = argparse.ArgumentParser(description="MediaGuard AI-benchmark")
    parser.add_argument("--provider", choices=("anthropic", "openai"))
    parser.add_argument("--model")
    parser.add_argument("--limit", type=int)
    args = parser.parse_args()
    _load_modules()
    ctx = build_context(load_settings())
    result = asyncio.run(run_benchmark(ctx, provider=args.provider, model=args.model,
                                       limit=args.limit))
    result.pop("details", None)
    print(json.dumps(result, ensure_ascii=False, indent=2))


if __name__ == "__main__":  # pragma: no cover
    main()
