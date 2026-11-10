"""AI Adviseur-API (DEEL S4). Alleen MediaGuard-routes; geen upstream/provider-routes."""

from __future__ import annotations

import importlib
import logging
from typing import Any, Literal

from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel, Field

from mediaguard.ai import benchmark, budget, service
from mediaguard.ai import settings as ai_settings
from mediaguard.ai.constants import (
    ACTION_LABELS_NL,
    CONSENT_TEXT_NL,
    MODE_LABELS_NL,
    PROVIDER_LABELS_NL,
    PROVIDERS,
    SECRET_NAMES,
    TASK_LABELS_NL,
)
from mediaguard.audit import audit
from mediaguard.auth import Principal, require_admin, require_operator, require_viewer
from mediaguard.context import AppContext, get_app_context

LOGGER = logging.getLogger(__name__)

router = APIRouter(prefix="/api/v1/ai", tags=["ai"])

# Modules die optioneel een ``ai_integration``-submodule met ``register_ai(ctx)`` leveren
# (registreert acties/evidence-providers). Afwezig = geen probleem.
INTEGRATION_MODULES = ("mediaguard.wanted", "mediaguard.identity", "mediaguard.encoding",
                       "mediaguard.library", "mediaguard.instances", "mediaguard.arr",
                       "mediaguard.seerr", "mediaguard.policy", "mediaguard.optimization")


def register_settings() -> None:
    ai_settings.register()


async def startup(ctx: AppContext) -> None:
    try:
        service.on_startup(ctx)
    except Exception:  # noqa: BLE001 - AI mag de opstart nooit blokkeren
        LOGGER.exception("AI-opstart mislukt; AI Adviseur blijft inactief")
    for base in INTEGRATION_MODULES:
        try:
            module = importlib.import_module(f"{base}.ai_integration")
        except ModuleNotFoundError:
            continue
        except Exception:  # noqa: BLE001
            LOGGER.exception("AI-integratie %s kon niet laden", base)
            continue
        hook = getattr(module, "register_ai", None)
        if callable(hook):
            try:
                hook(ctx)
            except Exception:  # noqa: BLE001
                LOGGER.exception("register_ai van %s mislukt", base)


# ---------------------------------------------------------------------------------------
# providers
# ---------------------------------------------------------------------------------------
def _provider_view(ctx: AppContext, provider: str) -> dict[str, Any]:
    cfg = service.provider_config(ctx, provider)
    model = ai_settings.provider_model(ctx, provider)
    defaults = list(ai_settings.DEFAULT_ANTHROPIC_MODELS) if provider == "anthropic" else []
    return {
        "id": provider, "label_nl": PROVIDER_LABELS_NL[provider],
        "selected": service.current_provider(ctx) == provider,
        "api_key_set": ctx.secrets.has(SECRET_NAMES[provider]),
        "api_key_secret_name": SECRET_NAMES[provider],
        "model": model, "suggested_models": defaults,
        "models": cfg["models"], "models_fetched_at": cfg["models_fetched_at"],
        "models_error": cfg["models_error"],
        "price_known": bool(model) and budget.price_for(ctx, provider, model) is not None,
        "consent": service.consent_status(ctx, provider),
        "last_test": {"ok": cfg["last_test_ok"], "at": cfg["last_test_at"],
                      "model": cfg["last_test_model"], "detail": cfg["last_test_detail"],
                      "latency_ms": cfg.get("last_test_latency_ms")},
        "blockers": service.enable_blockers(ctx, provider, model),
    }


@router.get("/providers")
async def providers(refresh: bool = Query(False, description="live modellijst ophalen"),
                    _: Principal = Depends(require_viewer),
                    ctx: AppContext = Depends(get_app_context)) -> dict[str, Any]:
    if refresh:
        for provider in PROVIDERS:
            await service.refresh_models(ctx, provider)
    return {
        "options": [{"id": "off", "label_nl": PROVIDER_LABELS_NL["off"]}] +
                   [{"id": p, "label_nl": PROVIDER_LABELS_NL[p]} for p in PROVIDERS],
        "providers": [_provider_view(ctx, p) for p in PROVIDERS],
        "status": service.status(ctx),
        "note_nl": "Een Claude- of ChatGPT-abonnement is geen API-tegoed: gebruik een "
                   "API-sleutel van de betaalde API.",
    }


def _check_provider(provider: str) -> None:
    if provider not in PROVIDERS:
        raise HTTPException(404, "Onbekende provider")


@router.get("/providers/{provider}/models")
async def provider_models(provider: str, _: Principal = Depends(require_viewer),
                          ctx: AppContext = Depends(get_app_context)) -> dict[str, Any]:
    _check_provider(provider)
    return await service.refresh_models(ctx, provider)


class TestBody(BaseModel):
    model: str | None = Field(default=None, max_length=200)


@router.post("/providers/{provider}/test")
async def provider_test(provider: str, body: TestBody | None = None,
                        principal: Principal = Depends(require_admin),
                        ctx: AppContext = Depends(get_app_context)) -> dict[str, Any]:
    _check_provider(provider)
    return await service.test_provider(ctx, provider, (body or TestBody()).model,
                                       principal.actor)


# ---------------------------------------------------------------------------------------
# instellingen
# ---------------------------------------------------------------------------------------
def _settings_view(ctx: AppContext) -> dict[str, Any]:
    defs = [d.public() for d in ctx.registry.all() if d.key.startswith("ai.")]
    return {
        "values": {d["key"]: ctx.config.value(d["key"]) for d in defs},
        "schema": defs,
        "status": service.status(ctx),
        "labels": {"modes": MODE_LABELS_NL, "tasks": TASK_LABELS_NL,
                   "actions": ACTION_LABELS_NL, "providers": PROVIDER_LABELS_NL},
        "price_note_nl": ai_settings.PRICE_NOTE_NL,
        "api_keys": {p: ctx.secrets.has(SECRET_NAMES[p]) for p in PROVIDERS},
    }


@router.get("/settings")
def get_settings(_: Principal = Depends(require_viewer),
                 ctx: AppContext = Depends(get_app_context)) -> dict[str, Any]:
    return _settings_view(ctx)


class SettingsPatch(BaseModel):
    values: dict[str, Any] = Field(default_factory=dict)
    # write-only: {"anthropic": "sk-ant-..."}; leeg string = verwijderen
    api_keys: dict[str, str] = Field(default_factory=dict)
    reason: str = Field(default="", max_length=500)


@router.patch("/settings")
def patch_settings(body: SettingsPatch, principal: Principal = Depends(require_admin),
                   ctx: AppContext = Depends(get_app_context)) -> dict[str, Any]:
    clean: dict[str, Any] = {}
    for key, value in body.values.items():
        if not key.startswith("ai."):
            raise HTTPException(422, f"{key} is geen AI-instelling")
        try:
            d = ctx.registry.get_def(key)
        except KeyError as exc:
            raise HTTPException(404, str(exc)) from exc
        try:
            if key == "ai.price_table":
                value = ai_settings.validate_price_table(value)
            elif key == "ai.task_models":
                value = ai_settings.validate_task_models(value)
            clean[key] = d.validate(value)
        except ValueError as exc:
            raise HTTPException(422, str(exc)) from exc
    for provider, key_value in body.api_keys.items():
        if provider not in PROVIDERS:
            raise HTTPException(422, f"Onbekende provider {provider}")
        if len(key_value) > 8192:
            raise HTTPException(422, "API-sleutel te lang")

    # Simuleer de eindtoestand om de inschakelvoorwaarden te controleren (alles-of-niets).
    def final(key: str) -> Any:
        return clean[key] if key in clean else ctx.config.value(key)

    mode = final("ai.mode")
    provider = final("ai.provider")
    if mode != "disabled":
        model = str(final(f"ai.{provider}.model") or "").strip() if provider in PROVIDERS else ""
        keys_after = {p: (bool(body.api_keys[p]) if p in body.api_keys
                          else ctx.secrets.has(SECRET_NAMES[p])) for p in PROVIDERS}
        blockers = []
        if provider not in PROVIDERS:
            blockers.append("Kies een provider (Claude of OpenAI).")
        else:
            if not keys_after[provider]:
                blockers.append(f"Geen API-sleutel ingesteld voor {provider}.")
            if not service.consent_status(ctx, provider)["granted"]:
                blockers.append("Toestemming voor externe metadataoverdracht ontbreekt.")
            table = final("ai.price_table") or {}
            if not model:
                blockers.append("Kies een model.")
            elif f"{provider}:{model}" not in table and \
                    f"{provider}:{model}" not in ai_settings.DEFAULT_PRICE_TABLE:
                blockers.append(f"Geen prijs in de prijstabel voor {provider}:{model}.")
            cfg = service.provider_config(ctx, provider)
            if model and not (cfg["last_test_ok"] and cfg["last_test_model"] == model):
                blockers.append(f"Voer eerst een geslaagde testverbinding uit met model {model}.")
            if provider in body.api_keys and cfg["last_test_ok"]:
                blockers.append("Nieuwe API-sleutel: test de verbinding opnieuw.")
        if mode == "bounded_auto" and provider in PROVIDERS and model:
            gate = service.auto_gate(ctx, provider, model)
            if not gate["unlocked"]:
                blockers.extend(gate["reasons"])
        if blockers:
            raise HTTPException(409, {"message": "AI kan nog niet in deze modus: "
                                                 + " ".join(blockers), "blockers": blockers})

    for provider_name, key_value in body.api_keys.items():
        if key_value:
            ctx.secrets.set(SECRET_NAMES[provider_name], key_value, principal.actor)
            audit(ctx.db, actor=principal.actor, action="secret.set", target_type="secret",
                  target_id=SECRET_NAMES[provider_name])
        else:
            ctx.secrets.delete(SECRET_NAMES[provider_name])
            audit(ctx.db, actor=principal.actor, action="secret.delete", target_type="secret",
                  target_id=SECRET_NAMES[provider_name])
        # nieuwe sleutel → testresultaat vervalt
        ctx.db.execute("UPDATE ai_provider_configs SET last_test_ok=0 WHERE provider=?",
                       (provider_name,))
    for key, value in clean.items():
        old = ctx.config.raw("global", "", key)
        ctx.config.set("global", "", key, value, principal.actor)
        audit(ctx.db, actor=principal.actor, action="settings.change", target_type="setting",
              target_id=f"global::{key}", reason=body.reason or None, old_value=old,
              new_value=value, undo_hint="Herstel de oude waarde in AI Adviseur")
    if clean:
        ctx.events.publish("settings.changed", {"changes": [{"key": k} for k in clean]})
    return _settings_view(ctx)


class ConsentBody(BaseModel):
    provider: Literal["anthropic", "openai"]
    granted: bool


@router.get("/consent")
def consent_get(_: Principal = Depends(require_viewer),
                ctx: AppContext = Depends(get_app_context)) -> dict[str, Any]:
    return {"text_nl": CONSENT_TEXT_NL,
            "providers": [service.consent_status(ctx, p) for p in PROVIDERS],
            "history": ctx.db.fetch_dicts(
                "SELECT id, ts, provider, granted, consent_version, actor FROM "
                "ai_consent_events ORDER BY id DESC LIMIT 50")}


@router.post("/consent")
def consent_post(body: ConsentBody, principal: Principal = Depends(require_admin),
                 ctx: AppContext = Depends(get_app_context)) -> dict[str, Any]:
    return service.record_consent(ctx, body.provider, body.granted, principal.actor)


# ---------------------------------------------------------------------------------------
# gebruik / cases
# ---------------------------------------------------------------------------------------
@router.get("/usage")
def usage(_: Principal = Depends(require_viewer),
          ctx: AppContext = Depends(get_app_context)) -> dict[str, Any]:
    return budget.usage_summary(ctx)


@router.get("/cases")
def cases(status: str | None = None, task_type: str | None = None,
          instance_id: str | None = None, limit: int = Query(100, ge=1, le=500),
          offset: int = Query(0, ge=0), _: Principal = Depends(require_viewer),
          ctx: AppContext = Depends(get_app_context)) -> dict[str, Any]:
    sql = "SELECT * FROM ai_cases WHERE 1=1"
    params: list[Any] = []
    groups = {"open": service.OPEN_STATUSES, "accepted": service.DONE_STATUSES,
              "done": service.DONE_STATUSES}
    if status in groups:  # filters van de AI-pagina (Open / Afgehandeld)
        statuses = groups[status]
        sql += f" AND status IN ({','.join('?' * len(statuses))})"
        params.extend(statuses)
        status = None
    for column, value in (("status", status), ("task_type", task_type),
                          ("instance_id", instance_id)):
        if value:
            sql += f" AND {column}=?"
            params.append(value)
    rows = ctx.db.fetch_dicts(sql + " ORDER BY updated_at DESC LIMIT ? OFFSET ?",
                              [*params, limit, offset])
    return {"items": [service.case_summary(ctx, r) for r in rows],
            "status": service.status(ctx)}


def _detail_or_404(ctx: AppContext, case_id: str) -> dict[str, Any]:
    detail = service.case_detail(ctx, case_id)
    if detail is None:
        raise HTTPException(404, "Casus bestaat niet")
    return detail


@router.get("/cases/{case_id}")
def case_get(case_id: str, _: Principal = Depends(require_viewer),
             ctx: AppContext = Depends(get_app_context)) -> dict[str, Any]:
    return _detail_or_404(ctx, case_id)


class AnalyzeBody(BaseModel):
    force: bool = Field(default=False, description="cache negeren ('Opnieuw onderzoeken')")


@router.post("/cases/{case_id}/analyze")
async def case_analyze(case_id: str, body: AnalyzeBody | None = None,
                       principal: Principal = Depends(require_operator),
                       ctx: AppContext = Depends(get_app_context)) -> dict[str, Any]:
    _detail_or_404(ctx, case_id)
    result = await service.analyze_case(ctx, case_id, force=(body or AnalyzeBody()).force,
                                        actor=principal.actor)
    return {"result": result, "case": _detail_or_404(ctx, case_id)}


class FeedbackBody(BaseModel):
    verdict: Literal["correct", "incorrect", "partially_correct", "not_useful"]
    comment: str = Field(default="", max_length=2000)


def _review(call: Any) -> Any:
    try:
        return call()
    except service.ReviewError as exc:
        raise HTTPException(exc.status_code, exc.message_nl) from exc


@router.post("/cases/{case_id}/feedback")
def case_feedback(case_id: str, body: FeedbackBody,
                  principal: Principal = Depends(require_operator),
                  ctx: AppContext = Depends(get_app_context)) -> dict[str, Any]:
    return dict(_review(lambda: service.feedback(ctx, case_id, body.verdict, body.comment,
                                                 principal.actor)))


@router.post("/cases/{case_id}/approve")
async def case_approve(case_id: str, principal: Principal = Depends(require_operator),
                       ctx: AppContext = Depends(get_app_context)) -> dict[str, Any]:
    try:
        return await service.approve(ctx, case_id, principal.actor)
    except service.ReviewError as exc:
        raise HTTPException(exc.status_code, exc.message_nl) from exc


class RejectBody(BaseModel):
    reason: str = Field(default="", max_length=500)


@router.post("/cases/{case_id}/reject")
def case_reject(case_id: str, body: RejectBody | None = None,
                principal: Principal = Depends(require_operator),
                ctx: AppContext = Depends(get_app_context)) -> dict[str, Any]:
    return dict(_review(lambda: service.reject(ctx, case_id, principal.actor,
                                               (body or RejectBody()).reason)))


@router.get("/preview/{case_id}")
def case_preview(case_id: str, provider: str | None = None,
                 _: Principal = Depends(require_viewer),
                 ctx: AppContext = Depends(get_app_context)) -> dict[str, Any]:
    if provider is not None:
        _check_provider(provider)
    data = service.preview(ctx, case_id, provider)
    if data is None:
        raise HTTPException(404, "Casus bestaat niet")
    return data


# ---------------------------------------------------------------------------------------
# benchmark
# ---------------------------------------------------------------------------------------
class BenchmarkBody(BaseModel):
    provider: Literal["anthropic", "openai"] | None = None
    model: str | None = Field(default=None, max_length=200)
    limit: int | None = Field(default=None, ge=1, le=500)


@router.post("/benchmark")
async def benchmark_run(body: BenchmarkBody, principal: Principal = Depends(require_admin),
                        ctx: AppContext = Depends(get_app_context)) -> dict[str, Any]:
    result = await benchmark.run_benchmark(ctx, provider=body.provider, model=body.model,
                                           limit=body.limit)
    audit(ctx.db, actor=principal.actor, action="ai.benchmark.run", target_type="ai_provider",
          target_id=result.get("provider"),
          reason=f"score {result.get('accuracy')} passed={result.get('passed')}")
    return result


@router.get("/evaluations")
def evaluations(limit: int = Query(50, ge=1, le=500), _: Principal = Depends(require_viewer),
                ctx: AppContext = Depends(get_app_context)) -> list[dict[str, Any]]:
    return ctx.db.fetch_dicts(
        "SELECT id, ts, provider, model, benchmark_version, total, correct, schema_valid, "
        "accuracy, baseline_accuracy, improvement, threshold, passed, cost FROM "
        "ai_model_evaluations ORDER BY id DESC LIMIT ?", (limit,))
