"""Kern van de AI Decision Advisor: trigger, cache, providercall, validatie, PolicyGuard.

Garantie naar andere modules: niets in dit bestand gooit een exceptie naar de aanroeper
van :func:`request_advice` of :func:`analyze_case`. Providerstoringen, time-outs en
budgetgrenzen leiden tot een casusstatus, nooit tot een blokkade van monitoring,
downloads of bestandstoegang (S2/S5.9).
"""

from __future__ import annotations

import asyncio
import contextlib
import hashlib
import json
import logging
import uuid
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
from typing import TYPE_CHECKING, Any

from mediaguard.ai import actions, budget, evidence, policy_guard
from mediaguard.ai import settings as ai_settings
from mediaguard.ai.constants import (
    CONSENT_TEXT_NL,
    CONSENT_VERSION,
    MODE_LABELS_NL,
    PROMPT_VERSION,
    PROVIDERS,
    SECRET_NAMES,
    TASK_LABELS_NL,
    TASK_TYPES,
    TRIGGER_REASON_CODES,
)
from mediaguard.ai.privacy import MinimizationReport, clean_text, minimize
from mediaguard.ai.prompt import build_prompt
from mediaguard.ai.providers import ADAPTERS, ProviderAdapter, ProviderError
from mediaguard.ai.schema import validate_advice
from mediaguard.audit import audit
from mediaguard.db import dumps, loads, utcnow
from mediaguard.jobs import JobContext, enqueue, job_handler

if TYPE_CHECKING:
    from mediaguard.context import AppContext

LOGGER = logging.getLogger(__name__)

OPEN_STATUSES = ("new", "queued", "analyzing", "skipped", "failed", "shadow_logged",
                 "awaiting_approval", "deferred", "invalid_output", "blocked")
# Afgehandeld zonder gebruiker: 'auto_resolved' = veilige standaard aangehouden.
DONE_STATUSES = ("approved", "rejected", "auto_executed", "auto_resolved")
STATUS_LABELS_NL = {
    "new": "Nieuw", "queued": "In wachtrij", "analyzing": "Wordt geanalyseerd",
    "skipped": "Niet geanalyseerd", "failed": "Mislukt (geen gevolgen)",
    "shadow_logged": "Schaduwvoorstel gelogd", "awaiting_approval": "Wacht op beoordeling",
    "invalid_output": "Modelantwoord afgewezen", "blocked": "Geblokkeerd door PolicyGuard",
    "auto_executed": "Automatisch uitgevoerd", "approved": "Geaccepteerd",
    "rejected": "Afgewezen",
    "auto_resolved": "Automatisch afgehandeld (veilige standaard)",
    "deferred": "Uitgesteld (dagmaximum AI-acties)",
}

# Wat MediaGuard aanhoudt als een AI-voorstel niet automatisch uitgevoerd mag worden: er
# wacht nooit iets op de gebruiker (zero-interactie), er wordt gewoon niets riskants gedaan.
SAFE_DEFAULT_NL = {
    "anime_identity": "de huidige koppeling blijft; de Identity Engine beslist zelf verder",
    "missing_diagnosis": "MediaGuard blijft volgens het normale schema automatisch zoeken",
    "release_explanation": "de bestaande keuze van Sonarr/Radarr blijft staan",
    "encode_advice": "geen profielwijziging; de baseline blijft gelden",
}


# ---------------------------------------------------------------------------------------
# status, toestemming, randvoorwaarden
# ---------------------------------------------------------------------------------------
def _ready_for_auto(ctx: AppContext) -> bool:
    provider = str(ai_settings.get(ctx, "ai.provider"))
    return provider in PROVIDERS and ctx.secrets.has(SECRET_NAMES[provider]) and \
        bool(consent_status(ctx, provider)["granted"])


def current_mode(ctx: AppContext) -> str:
    """Effectieve modus. Zero-interactie: 'Advies' wacht nooit meer op een klik en gedraagt
    zich als 'Beperkt automatisch'; met provider, sleutel en toestemming (en nooit bewust op
    'Uit' gezet) is de AI standaard automatisch (veilig)."""
    stored = str(ai_settings.get(ctx, "ai.mode"))
    if stored == "advisory":
        return "bounded_auto"
    if stored == "disabled" and ctx.config.raw("global", "", "ai.mode") is None and \
            _ready_for_auto(ctx):
        return "bounded_auto"
    return stored


def current_provider(ctx: AppContext) -> str:
    return str(ai_settings.get(ctx, "ai.provider"))


def consent_status(ctx: AppContext, provider: str) -> dict[str, Any]:
    row = ctx.db.fetch_one(
        "SELECT * FROM ai_consent_events WHERE provider=? ORDER BY id DESC LIMIT 1", (provider,))
    if row is None:
        return {"provider": provider, "granted": False, "ts": None, "actor": None,
                "version": None, "current_version": CONSENT_VERSION}
    return {"provider": provider,
            "granted": bool(row["granted"]) and row["consent_version"] == CONSENT_VERSION,
            "ts": row["ts"], "actor": row["actor"], "version": row["consent_version"],
            "current_version": CONSENT_VERSION}


def record_consent(ctx: AppContext, provider: str, granted: bool, actor: str) -> dict[str, Any]:
    if provider not in PROVIDERS:
        raise ValueError("Onbekende provider")
    ctx.db.execute(
        "INSERT INTO ai_consent_events(ts,provider,granted,consent_version,actor,text_nl) "
        "VALUES (?,?,?,?,?,?)", (utcnow(), provider, int(granted), CONSENT_VERSION, actor,
                                 CONSENT_TEXT_NL))
    audit(ctx.db, actor=actor, action="ai.consent." + ("grant" if granted else "revoke"),
          target_type="ai_provider", target_id=provider)
    if not granted and current_provider(ctx) == provider and current_mode(ctx) != "disabled":
        ctx.config.set("global", "", "ai.mode", "disabled", actor)
        audit(ctx.db, actor=actor, action="settings.change", target_type="setting",
              target_id="global::ai.mode", reason="toestemming ingetrokken",
              new_value="disabled")
    return consent_status(ctx, provider)


def provider_config(ctx: AppContext, provider: str) -> dict[str, Any]:
    row = ctx.db.fetch_one("SELECT * FROM ai_provider_configs WHERE provider=?", (provider,))
    if row is None:
        return {"provider": provider, "last_test_ok": False, "last_test_at": None,
                "last_test_model": None, "last_test_detail": None, "models": [],
                "models_fetched_at": None, "models_error": None}
    data = dict(row)
    data["last_test_ok"] = bool(data["last_test_ok"])
    data["models"] = loads(data.pop("models_json"), [])
    return data


def _upsert_provider_config(ctx: AppContext, provider: str, **fields: Any) -> None:
    ctx.db.execute("INSERT INTO ai_provider_configs(provider,updated_at) VALUES (?,?) "
                   "ON CONFLICT(provider) DO NOTHING", (provider, utcnow()))
    if fields:
        cols = ", ".join(f"{k}=?" for k in fields)
        ctx.db.execute(f"UPDATE ai_provider_configs SET {cols}, updated_at=? WHERE provider=?",
                       (*fields.values(), utcnow(), provider))


def make_adapter(ctx: AppContext, provider: str) -> ProviderAdapter:
    api_key = ctx.secrets.get(SECRET_NAMES[provider])
    base_url = str(ai_settings.get(ctx, f"ai.{provider}.base_url"))
    effort = str(ai_settings.get(ctx, "ai.anthropic.effort"))
    return ADAPTERS[provider](api_key, base_url, effort=effort)


def enable_blockers(ctx: AppContext, provider: str, model: str, *,
                    require_test: bool = True) -> list[str]:
    """Wat ontbreekt er nog voordat AI externe calls mag doen? (Nederlandse UI-tekst)"""
    out: list[str] = []
    if provider not in PROVIDERS:
        return ["Kies een provider (Claude of OpenAI)."]
    if not ctx.secrets.has(SECRET_NAMES[provider]):
        out.append(f"Geen API-sleutel ingesteld voor {provider}.")
    if not consent_status(ctx, provider)["granted"]:
        out.append("Toestemming voor externe metadataoverdracht ontbreekt.")
    if not model:
        out.append("Kies een model.")
    elif budget.price_for(ctx, provider, model) is None:
        out.append(f"Geen prijs in de prijstabel voor {provider}:{model} "
                   "(controleer actuele providerprijzen).")
    if require_test and model:
        cfg = provider_config(ctx, provider)
        if not (cfg["last_test_ok"] and cfg["last_test_model"] == model):
            out.append(f"Voer eerst een geslaagde testverbinding uit met model {model}.")
    return out


def auto_gate(ctx: AppContext, provider: str, model: str) -> dict[str, Any]:
    """Ontgrendeling van bounded_auto: schaduwfase + benchmark + feedback."""
    reasons: list[str] = []
    min_shadow = int(ai_settings.get(ctx, "ai.bounded_auto.min_shadow_cases"))
    shadow = ctx.db.fetch_one(
        "SELECT COUNT(*) AS n FROM ai_proposals p WHERE p.valid=1 AND p.from_cache=0 AND "
        "p.provider=? AND p.model=? AND (p.mode='shadow' OR NOT EXISTS ("
        "SELECT 1 FROM ai_policy_results r WHERE r.proposal_id=p.id AND "
        "r.decision='auto_execute'))", (provider, model))
    shadow_n = int(shadow["n"]) if shadow else 0
    if shadow_n < min_shadow:
        reasons.append(f"Schaduwfase onvoldoende: {shadow_n} van {min_shadow} geldige "
                       "schaduwvoorstellen.")
    ev = ctx.db.fetch_one(
        "SELECT * FROM ai_model_evaluations WHERE provider=? AND model=? ORDER BY id DESC "
        "LIMIT 1", (provider, model))
    evaluation = dict(ev) if ev else None
    if evaluation:
        evaluation.pop("details_json", None)
    min_acc = float(ai_settings.get(ctx, "ai.bounded_auto.min_benchmark_accuracy"))
    min_imp = float(ai_settings.get(ctx, "ai.bounded_auto.min_improvement"))
    if ev is None:
        reasons.append("Nog geen benchmark uitgevoerd voor dit model.")
    elif not ev["passed"] or ev["accuracy"] < min_acc or ev["improvement"] < min_imp:
        reasons.append(
            f"Laatste benchmark onvoldoende: score {ev['accuracy']:.2f} (minimaal "
            f"{min_acc:.2f}), verbetering t.o.v. regels {ev['improvement']:+.2f} (minimaal "
            f"{min_imp:.2f}).")
    fb = ctx.db.fetch_one(
        "SELECT COUNT(*) AS n, SUM(CASE WHEN f.verdict='incorrect' THEN 1 ELSE 0 END) AS bad "
        "FROM ai_feedback f JOIN ai_proposals p ON p.id=f.proposal_id "
        "WHERE p.provider=? AND p.model=?", (provider, model))
    fb_n, fb_bad = (int(fb["n"] or 0), int(fb["bad"] or 0)) if fb else (0, 0)
    if fb_n >= 5 and fb_bad / fb_n > 0.2:
        reasons.append(f"Te veel voorstellen als onjuist gemarkeerd ({fb_bad} van {fb_n}).")
    return {"unlocked": not reasons, "reasons": reasons, "shadow_cases": shadow_n,
            "min_shadow_cases": min_shadow, "evaluation": evaluation,
            "feedback": {"total": fb_n, "incorrect": fb_bad}}


def status(ctx: AppContext) -> dict[str, Any]:
    provider = current_provider(ctx)
    mode = current_mode(ctx)
    model = ai_settings.provider_model(ctx, provider) if provider in PROVIDERS else ""
    blockers = enable_blockers(ctx, provider, model) if provider in PROVIDERS else \
        ["Kies een provider (Claude of OpenAI)."]
    if mode == "disabled":
        message = "AI Adviseur staat uit. MediaGuard werkt volledig zonder AI."
    elif blockers:
        message = "AI Adviseur is ingesteld maar kan niet draaien: " + " ".join(blockers)
    else:
        message = f"AI Adviseur actief in modus '{MODE_LABELS_NL[mode]}'."
    gate = auto_gate(ctx, provider, model) if provider in PROVIDERS and model else \
        {"unlocked": False, "reasons": ["Geen provider/model gekozen."]}
    active = mode != "disabled" and not blockers
    idle = None
    if active:
        # waarom '0 analyses vandaag'? (rapport 4 nov, M17)
        since = utcnow()[:10]
        row = ctx.db.fetch_one("SELECT COUNT(*) AS n FROM ai_cases WHERE created_at>=?",
                               (since,))
        if row is not None and int(row["n"]) == 0:
            idle = ("Vandaag geen twijfelgevallen gevonden. MediaGuard vraagt de AI alleen om "
                    "advies bij echte twijfel (tegenstrijdig identiteitsbewijs, lastige "
                    "releasekeuzes); al het andere, zoals ontbrekende items onderzoeken, gaat "
                    "zonder AI en kost dus niets.")
    return {"mode": mode, "provider": provider, "model": model, "active": active,
            "blockers": blockers, "message_nl": message, "idle_reason_nl": idle,
            "prices_as_of": ai_settings.PRICES_AS_OF, "bounded_auto": gate}


# ---------------------------------------------------------------------------------------
# providercall met budget en retries
# ---------------------------------------------------------------------------------------
@dataclass
class CallOutcome:
    ok: bool
    data: Any = None
    error_kind: str | None = None
    message_nl: str = ""
    cost: float = 0.0
    input_tokens: int = 0
    output_tokens: int = 0
    latency_ms: int = 0
    attempts: int = 0
    model: str = ""
    errors: list[str] = field(default_factory=list)


async def call_structured(ctx: AppContext, *, provider: str, model: str, task_type: str | None,
                          case_id: str | None, purpose: str, system: str, user: str,
                          schema: dict[str, Any]) -> CallOutcome:
    """Eén gestructureerde providercall incl. reservering, afrekening en begrensde retries.
    Gooit nooit."""
    out = CallOutcome(ok=False, model=model)
    try:
        if not ctx.secrets.has(SECRET_NAMES[provider]):
            out.error_kind, out.message_nl = "no_key", "Geen API-sleutel ingesteld."
            return out
        adapter = make_adapter(ctx, provider)
        max_out = int(ai_settings.get(ctx, "ai.max_output_tokens"))
        timeout = float(ai_settings.get(ctx, "ai.timeout_seconds"))
        max_retries = int(ai_settings.get(ctx, "ai.max_retries"))
        retry_budget = int(ai_settings.get(ctx, "ai.retry_budget_per_day"))
        in_est = budget.estimate_input_tokens(system, user, json.dumps(schema))
        if in_est > int(ai_settings.get(ctx, "ai.max_input_tokens")):
            out.error_kind = "payload_too_large"
            out.message_nl = (f"Casus te groot (~{in_est} tokens geschat); verhoog max "
                              "invoertokens of verklein de casus.")
            return out
        delay = float(ctx.services.get("ai_retry_delay", 1.0))
        for attempt in range(1, max_retries + 2):
            if attempt > 1 and budget.retries_today(ctx) >= retry_budget:
                out.errors.append("retrybudget van vandaag op")
                break
            try:
                usage_id, _ = budget.reserve(
                    ctx, provider=provider, model=model, task_type=task_type, case_id=case_id,
                    purpose=purpose, input_tokens_est=in_est, max_output_tokens=max_out,
                    already_spent_this_analysis=out.cost, attempt=attempt)
            except budget.BudgetExceeded as exc:
                out.error_kind, out.message_nl = f"budget_{exc.code}", exc.message_nl
                return out
            out.attempts = attempt
            try:
                resp = await adapter.complete(model=model, system=system, user=user,
                                              schema=schema, max_output_tokens=max_out,
                                              timeout=timeout)
            except ProviderError as exc:
                out.cost += budget.settle(
                    ctx, usage_id, provider=provider, model=model,
                    input_tokens=exc.input_tokens, output_tokens=exc.output_tokens,
                    billed_possible=exc.billed_possible, error=f"{exc.kind}: {exc}")
                out.error_kind, out.message_nl = exc.kind, _provider_error_nl(exc)
                out.errors.append(f"poging {attempt}: {exc.kind}")
                if exc.retryable and attempt <= max_retries:
                    await asyncio.sleep(delay * attempt)
                    continue
                return out
            except Exception as exc:  # noqa: BLE001 - nooit doorgeven aan aanroeper
                out.cost += budget.settle(ctx, usage_id, provider=provider, model=model,
                                          input_tokens=None, output_tokens=None,
                                          billed_possible=True, error=type(exc).__name__)
                out.error_kind, out.message_nl = "internal", "Interne fout bij providercall."
                return out
            out.cost += budget.settle(ctx, usage_id, provider=provider, model=model,
                                      input_tokens=resp.input_tokens,
                                      output_tokens=resp.output_tokens, billed_possible=True,
                                      latency_ms=resp.latency_ms)
            out.ok, out.data = True, resp.data
            out.input_tokens, out.output_tokens = resp.input_tokens, resp.output_tokens
            out.latency_ms, out.model = resp.latency_ms, resp.model or model
            out.error_kind, out.message_nl = None, ""
            return out
        return out
    except Exception:  # noqa: BLE001
        LOGGER.exception("AI-call mislukt")
        out.error_kind, out.message_nl = "internal", "Interne fout bij providercall."
        return out


def _provider_error_nl(exc: ProviderError) -> str:
    return {
        "timeout": "Provider reageerde niet op tijd (time-out).",
        "network": "Provider niet bereikbaar (netwerkfout).",
        "rate_limited": "Provider meldt rate limit of quotum op.",
        "server": "Providerstoring (serverfout).",
        "auth": "API-sleutel geweigerd door de provider.",
        "bad_request": "Provider weigerde het verzoek (controleer model-ID).",
        "refusal": "Model weigerde de analyse.",
        "truncated": "Antwoord afgekapt; verhoog max antwoordtokens.",
        "invalid_response": "Antwoord van provider had geen geldig formaat.",
    }.get(exc.kind, "Onbekende providerfout.") + f" ({exc})"


# ---------------------------------------------------------------------------------------
# trigger-API voor andere modules
# ---------------------------------------------------------------------------------------
def _payload_hash(task_type: str, payload: dict[str, Any], evidence_ids: list[str]) -> str:
    canonical = dumps({"t": task_type, "p": payload, "e": sorted(set(evidence_ids))})
    return hashlib.sha256(canonical.encode()).hexdigest()


def request_advice(ctx: AppContext, task_type: str, case_payload: dict[str, Any],
                   evidence_ids: list[str] | tuple[str, ...], reason_code: str, *,
                   target_type: str | None = None, target_id: str | int | None = None,
                   instance_id: str | None = None, source_module: str | None = None
                   ) -> dict[str, Any]:
    """Trigger-API (S2). Alleen aanroepen wanneer een deterministische classifier een
    expliciete needs_advice-reasoncode afgeeft. Synchroon, snel (alleen DB), gooit nooit.

    Geeft een ticket: ``{"case_id", "status", "detail", "cached"}``. Bij status ``queued``
    wordt de analyse als job ``ai.analyze`` uitgevoerd; ondubbelzinnige gevallen horen deze
    functie niet aan te roepen (en codes buiten TRIGGER_REASON_CODES worden genegeerd).
    """
    try:
        if task_type not in TASK_TYPES:
            return {"case_id": None, "status": "ignored", "cached": False,
                    "detail": f"onbekend taaktype {task_type!r}"}
        if reason_code not in TRIGGER_REASON_CODES[task_type]:
            return {"case_id": None, "status": "ignored", "cached": False,
                    "detail": f"geen needs_advice-reasoncode voor {task_type}: {reason_code!r}"}
        payload, report = minimize(case_payload or {})
        ev_ids = [str(e)[:200] for e in (evidence_ids or [])][:50]
        phash = _payload_hash(task_type, payload, ev_ids)
        # ook een zelf afgehandelde casus hergebruiken: geen dubbele casussen/kosten
        reuse = (*OPEN_STATUSES, "auto_resolved")
        existing = ctx.db.fetch_one(
            f"SELECT id, status FROM ai_cases WHERE payload_hash=? AND status IN "
            f"({','.join('?' * len(reuse))}) ORDER BY created_at DESC LIMIT 1",
            (phash, *reuse))
        if existing is not None and existing["status"] not in ("skipped", "failed"):
            return {"case_id": existing["id"], "status": existing["status"], "cached": False,
                    "detail": "bestaande casus hergebruikt"}
        if existing is not None:
            case_id = str(existing["id"])
            ctx.db.execute("UPDATE ai_cases SET updated_at=? WHERE id=?", (utcnow(), case_id))
        else:
            case_id = uuid.uuid4().hex
            now = utcnow()
            ctx.db.execute(
                "INSERT INTO ai_cases(id,task_type,reason_code,status,payload_json,payload_hash,"
                "minimization_json,evidence_ids_json,target_type,target_id,instance_id,"
                "source_module,created_at,updated_at) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
                (case_id, task_type, reason_code, "new", dumps(payload), phash, dumps(report),
                 dumps(ev_ids), target_type, None if target_id is None else str(target_id),
                 instance_id, source_module, now, now))
        reason = _skip_reason(ctx, task_type)
        if reason:
            _set_status(ctx, case_id, "skipped", reason)
            return {"case_id": case_id, "status": "skipped", "cached": False, "detail": reason}
        _set_status(ctx, case_id, "queued", "wacht op analyse")
        enqueue(ctx.db, "ai.analyze", {"case_id": case_id}, instance_id=instance_id,
                target_type="ai_case", target_id=case_id, priority=200,
                idempotency_key=f"ai.analyze:{case_id}:{utcnow()[:13]}", max_attempts=1,
                created_by="ai")
        ctx.events.publish("ai.case.created", {"case_id": case_id, "task_type": task_type},
                           instance_id=instance_id)
        return {"case_id": case_id, "status": "queued", "cached": False,
                "detail": "analyse ingepland"}
    except Exception:  # noqa: BLE001 - trigger mag de aanroeper nooit breken
        LOGGER.exception("request_advice mislukt")
        return {"case_id": None, "status": "error", "cached": False,
                "detail": "interne fout; AI-advies overgeslagen"}


async def request_advice_and_analyze(ctx: AppContext, task_type: str,
                                     case_payload: dict[str, Any], evidence_ids: list[str],
                                     reason_code: str, **kwargs: Any) -> dict[str, Any]:
    """Zelfde als request_advice, maar analyseert direct (voor UI/tests)."""
    ticket = request_advice(ctx, task_type, case_payload, evidence_ids, reason_code, **kwargs)
    if ticket.get("status") == "queued" and ticket.get("case_id"):
        result = await analyze_case(ctx, str(ticket["case_id"]))
        ticket = {**ticket, "status": result.get("status"), "detail": result.get("detail")}
    return ticket


def _skip_reason(ctx: AppContext, task_type: str) -> str | None:
    mode = current_mode(ctx)
    if mode == "disabled":
        return "AI staat uit (beleid 'Uit')."
    if task_type not in (ai_settings.get(ctx, "ai.task_types") or []):
        return f"Taaktype '{TASK_LABELS_NL[task_type]}' staat niet aan als doelcase."
    provider = current_provider(ctx)
    model = ai_settings.provider_model(ctx, provider, task_type) if provider in PROVIDERS else ""
    blockers = enable_blockers(ctx, provider, model, require_test=False)
    return " ".join(blockers) if blockers else None


def _set_status(ctx: AppContext, case_id: str, status_: str, detail: str | None) -> None:
    ctx.db.execute("UPDATE ai_cases SET status=?, status_detail=?, updated_at=? WHERE id=?",
                   (status_, detail and detail[:500], utcnow(), case_id))
    ctx.events.publish("ai.case.updated", {"case_id": case_id, "status": status_},
                       persist=False)


# ---------------------------------------------------------------------------------------
# analyse
# ---------------------------------------------------------------------------------------
def _case_row(ctx: AppContext, case_id: str) -> dict[str, Any] | None:
    row = ctx.db.fetch_one("SELECT * FROM ai_cases WHERE id=?", (case_id,))
    return dict(row) if row else None


def prompt_parts(ctx: AppContext, case: dict[str, Any]) -> dict[str, Any]:
    ev_ids = loads(case["evidence_ids_json"], [])
    known = evidence.lookup(ctx, ev_ids)
    system, user, schema = build_prompt(
        case_id=case["id"], task_type=case["task_type"], reason_code=case["reason_code"],
        payload=loads(case["payload_json"], {}), evidence=known)
    return {"system": system, "user": user, "schema": schema, "known_evidence": known,
            "unknown_evidence": [e for e in ev_ids if e not in known]}


def _cache_key(provider: str, model: str, case: dict[str, Any],
               known: dict[str, dict[str, Any]]) -> str:
    raw = dumps({"v": PROMPT_VERSION, "p": provider, "m": model, "t": case["task_type"],
                 "r": case["reason_code"], "h": case["payload_hash"], "e": known})
    return hashlib.sha256(raw.encode()).hexdigest()


def _day_start_utc(ctx: AppContext) -> str:
    tz = ai_settings.timezone(ctx)
    local = budget.now_utc(ctx).astimezone(tz)
    start = local.replace(hour=0, minute=0, second=0, microsecond=0)
    return start.astimezone(UTC).isoformat(timespec="seconds")


async def analyze_case(ctx: AppContext, case_id: str, *, force: bool = False,
                       actor: str = "system") -> dict[str, Any]:
    """Analyseert een casus volgens de huidige modus. Gooit nooit."""
    try:
        return await _analyze(ctx, case_id, force=force, actor=actor)
    except Exception:  # noqa: BLE001
        LOGGER.exception("AI-analyse mislukt")
        with contextlib.suppress(Exception):
            _set_status(ctx, case_id, "failed", "interne fout; geen gevolgen voor workflow")
        return {"case_id": case_id, "status": "failed", "detail": "interne fout"}


async def _analyze(ctx: AppContext, case_id: str, *, force: bool, actor: str) -> dict[str, Any]:
    case = _case_row(ctx, case_id)
    if case is None:
        return {"case_id": case_id, "status": "not_found", "detail": "casus bestaat niet"}
    if case["status"] in DONE_STATUSES and not force:
        return {"case_id": case_id, "status": case["status"], "detail": "casus is afgehandeld"}
    reason = _skip_reason(ctx, case["task_type"])
    mode = current_mode(ctx)
    provider = current_provider(ctx)
    model = ai_settings.provider_model(ctx, provider, case["task_type"]) \
        if provider in PROVIDERS else ""
    if reason is None:
        test_blockers = enable_blockers(ctx, provider, model)
        reason = " ".join(test_blockers) if test_blockers else None
    if reason:
        _set_status(ctx, case_id, "skipped", reason)
        return {"case_id": case_id, "status": "skipped", "detail": reason}

    _purge_old(ctx)
    parts = prompt_parts(ctx, case)
    known = parts["known_evidence"]
    cache_key = _cache_key(provider, model, case, known)
    ttl_days = int(ai_settings.get(ctx, "ai.cache_ttl_days"))
    cached = None
    if not force and ttl_days > 0:
        since = (budget.now_utc(ctx) - timedelta(days=ttl_days)).isoformat(timespec="seconds")
        cached = ctx.db.fetch_one(
            "SELECT proposal_json FROM ai_proposals WHERE cache_key=? AND valid=1 AND "
            "created_at>=? ORDER BY id DESC LIMIT 1", (cache_key, since))
    ctx.db.execute("UPDATE ai_cases SET analysis_count=analysis_count+1, last_analyzed_at=? "
                   "WHERE id=?", (utcnow(), case_id))
    if cached is not None:
        proposal = loads(cached["proposal_json"])
        proposal["case_id"] = case_id
        budget.record_cache_hit(ctx, provider=provider, model=model,
                                task_type=case["task_type"], case_id=case_id)
        return await _apply_proposal(ctx, case, proposal, provider=provider, model=model,
                                     mode=mode, cache_key=cache_key, from_cache=True, actor=actor)

    _set_status(ctx, case_id, "analyzing", None)
    outcome = await call_structured(
        ctx, provider=provider, model=model, task_type=case["task_type"], case_id=case_id,
        purpose="analysis", system=parts["system"], user=parts["user"], schema=parts["schema"])
    if not outcome.ok:
        status_ = "skipped" if (outcome.error_kind or "").startswith("budget_") else "failed"
        _set_status(ctx, case_id, status_, outcome.message_nl)
        return {"case_id": case_id, "status": status_, "detail": outcome.message_nl,
                "cost": outcome.cost}
    proposal, errors = validate_advice(
        outcome.data, case_id=case_id, task_type=case["task_type"],
        case_evidence_ids=known.keys(), evidence_exists=lambda e: evidence.exists(ctx, e))
    if proposal is None:
        ctx.db.execute(
            "INSERT INTO ai_proposals(case_id,provider,model,mode,cache_key,from_cache,valid,"
            "proposal_json,rejection_reasons_json,created_at) VALUES (?,?,?,?,?,?,?,?,?,?)",
            (case_id, provider, model, mode, cache_key, 0, 0, None, dumps(errors), utcnow()))
        _set_status(ctx, case_id, "invalid_output",
                    "Modelantwoord afgewezen: " + "; ".join(errors))
        return {"case_id": case_id, "status": "invalid_output", "detail": errors,
                "cost": outcome.cost}
    return await _apply_proposal(ctx, case, proposal, provider=provider, model=model, mode=mode,
                                 cache_key=cache_key, from_cache=False, actor=actor)


async def _apply_proposal(ctx: AppContext, case: dict[str, Any], proposal: dict[str, Any], *,
                          provider: str, model: str, mode: str, cache_key: str,
                          from_cache: bool, actor: str) -> dict[str, Any]:
    case_id = case["id"]
    proposal = dict(proposal)
    proposal["reasoning_summary"] = clean_text(proposal["reasoning_summary"],
                                               MinimizationReport(), limit=800)
    proposal_id = ctx.db.execute_insert(
        "INSERT INTO ai_proposals(case_id,provider,model,mode,cache_key,from_cache,valid,"
        "proposal_json,rejection_reasons_json,created_at) VALUES (?,?,?,?,?,?,?,?,?,?)",
        (case_id, provider, model, mode, cache_key, int(from_cache), 1, dumps(proposal), "[]",
         utcnow()))
    gate: dict[str, Any] = auto_gate(ctx, provider, model) if mode == "bounded_auto" else \
        {"unlocked": False, "reasons": []}
    if mode == "bounded_auto" and not gate["unlocked"]:
        maybe_schedule_benchmark(ctx, provider, model, gate)
    decision = policy_guard.evaluate(
        ctx, mode=mode, proposal=proposal, task_type=case["task_type"],
        auto_unlocked=bool(gate["unlocked"]), auto_lock_reasons=list(gate["reasons"]),
        actions_today=policy_guard.actions_executed_today(ctx, _day_start_utc(ctx)))
    policy_id = ctx.db.execute_insert(
        "INSERT INTO ai_policy_results(proposal_id,case_id,mode,decision,action,executable,"
        "reasons_json,decided_by,created_at) VALUES (?,?,?,?,?,?,?,?,?)",
        (proposal_id, case_id, mode, decision.decision, decision.action,
         int(decision.executable), dumps(decision.reasons), "policy_guard", utcnow()))
    status_map = {"log_only": "shadow_logged", "await_approval": "awaiting_approval",
                  "blocked": "blocked", "auto_execute": "auto_executed"}
    new_status = status_map[decision.decision]
    detail = "; ".join(decision.reasons)
    if decision.decision == "auto_execute":
        request = actions.ActionRequest(
            case_id=case_id, task_type=case["task_type"], action=str(decision.action),
            instance_id=case["instance_id"], target_type=case["target_type"],
            target_id=case["target_id"], actor="ai:bounded_auto", via="bounded_auto")
        result = await actions.execute(
            ctx, request, float(ai_settings.get(ctx, "ai.action_timeout_seconds")))
        ctx.db.execute(
            "UPDATE ai_policy_results SET execution_status=?, execution_result_json=?, "
            "executed_at=? WHERE id=?",
            (result["status"], dumps(result), utcnow(), policy_id))
        audit(ctx.db, actor="ai:bounded_auto", action=f"ai.action.{decision.action}",
              target_type=case["target_type"], target_id=case["target_id"],
              instance_id=case["instance_id"], correlation_id=case_id,
              reason=f"AI-voorstel (case {case_id}) automatisch: {result['status']}",
              new_value=result)
        if result["status"] != "executed":
            new_status = "auto_resolved"
            detail = safe_default_detail(
                case["task_type"], [f"automatische uitvoering niet gelukt ({result['status']}"
                                    f"): {result.get('detail')}; niets gewijzigd"])
    elif decision.decision == "await_approval":
        new_status, detail = _resolve_without_user(ctx, case, proposal, decision, mode=mode,
                                                   gate=gate)
    _set_status(ctx, case_id, new_status, detail)
    if new_status in ("auto_resolved", "deferred"):
        audit(ctx.db, actor="ai:auto", action=f"ai.case.{new_status}",
              target_type="ai_case", target_id=case_id, instance_id=case["instance_id"],
              correlation_id=case_id, reason=detail[:300])
    return {"case_id": case_id, "status": new_status, "detail": detail,
            "proposal": proposal, "policy": decision.as_dict(), "from_cache": from_cache}


def maybe_schedule_benchmark(ctx: AppContext, provider: str, model: str,
                             gate: dict[str, Any]) -> bool:
    """Ontgrendeling zonder menselijke stap: zodra de schaduwfase vol is en er (nog) geen
    geslaagde, recente benchmark is, plant MediaGuard die zelf in (hooguit 1× per week per
    model; het budget blijft gelden). De drempels zelf blijven ongewijzigd."""
    if int(gate.get("shadow_cases") or 0) < int(gate.get("min_shadow_cases") or 0):
        return False
    ev = gate.get("evaluation")
    if ev and ev.get("passed"):
        return False
    week = budget.now_utc(ctx).strftime("%G-W%V")
    enqueue(ctx.db, "ai.benchmark", {"provider": provider, "model": model},
            target_type="ai_provider", target_id=provider,
            idempotency_key=f"ai-benchmark:{provider}:{model}:{week}", created_by="ai:auto")
    return True


def safe_default_detail(task_type: str, reasons: list[str]) -> str:
    default = SAFE_DEFAULT_NL.get(task_type, "er wordt niets gewijzigd")
    why = "; ".join(r for r in reasons if r)
    return f"Veilige standaard aangehouden: {default}." + (f" Reden: {why}" if why else "")


def _next_day_start(ctx: AppContext) -> datetime:
    tz = ai_settings.timezone(ctx)
    local = budget.now_utc(ctx).astimezone(tz)
    start = (local + timedelta(days=1)).replace(hour=0, minute=5, second=0, microsecond=0)
    return start.astimezone(UTC)


def _resolve_without_user(ctx: AppContext, case: dict[str, Any], proposal: dict[str, Any],
                          decision: policy_guard.PolicyDecision, *, mode: str,
                          gate: dict[str, Any]) -> tuple[str, str]:
    """Zero-interactie: wat vroeger op 'akkoord' wachtte, wordt nu zelf afgehandeld.

    Alleen het dagmaximum stond uitvoering in de weg → uitstellen tot morgen (automatisch).
    Al het andere (vergrendeld, niet op de allowlist, niet uitvoerbaar, hoge onzekerheid,
    noodstop) → veilige standaard: er gebeurt niets riskants en niets wacht op de gebruiker.
    Destructieve acties bestaan niet in het vocabulaire en worden dus nooit uitgevoerd."""
    without_cap = policy_guard.evaluate(
        ctx, mode="bounded_auto", proposal=proposal, task_type=case["task_type"],
        auto_unlocked=bool(gate.get("unlocked")),
        auto_lock_reasons=list(gate.get("reasons") or []), actions_today=0)
    if mode == "bounded_auto" and without_cap.decision == "auto_execute":
        run_at = _next_day_start(ctx)
        enqueue(ctx.db, "ai.analyze", {"case_id": case["id"]},
                target_type="ai_case", target_id=case["id"], run_after=run_at,
                idempotency_key=f"ai-deferred:{case['id']}:{run_at.date().isoformat()}",
                created_by="ai:auto")
        cap = int(ai_settings.get(ctx, "ai.bounded_auto.max_actions_per_day"))
        return "deferred", (f"Dagmaximum AI-acties bereikt ({cap}); MediaGuard voert dit "
                            f"morgen automatisch uit.")
    return "auto_resolved", safe_default_detail(case["task_type"], decision.reasons)


def resolve_waiting_cases(ctx: AppContext) -> int:
    """Oude 'wacht op akkoord'-cases (van vóór zero-interactie) zelf afhandelen."""
    rows = ctx.db.fetch_dicts(
        "SELECT id, task_type, instance_id FROM ai_cases WHERE status='awaiting_approval'")
    for r in rows:
        pol = _latest_policy(ctx, r["id"])
        action = pol["action"] if pol else None
        if action in actions.EXECUTABLE_ACTIONS and current_mode(ctx) == "bounded_auto":
            # opnieuw beoordelen; een gecacht voorstel kost niets en kan nu automatisch
            enqueue(ctx.db, "ai.analyze", {"case_id": r["id"]}, target_type="ai_case",
                    target_id=r["id"], idempotency_key=f"ai-migrate:{r['id']}",
                    created_by="ai:auto")
        detail = safe_default_detail(r["task_type"], ["wachtte op akkoord; MediaGuard wacht "
                                                      "niet meer op de gebruiker"])
        _set_status(ctx, r["id"], "auto_resolved", detail)
        audit(ctx.db, actor="ai:auto", action="ai.case.auto_resolved", target_type="ai_case",
              target_id=r["id"], instance_id=r["instance_id"], correlation_id=r["id"],
              reason=detail[:300])
    return len(rows)


def _purge_old(ctx: AppContext) -> None:
    days = int(ai_settings.get(ctx, "ai.retention_days"))
    cutoff = (budget.now_utc(ctx) - timedelta(days=days)).isoformat(timespec="seconds")
    ctx.db.execute("DELETE FROM ai_cases WHERE updated_at<? AND status NOT IN "
                   "('awaiting_approval','deferred')", (cutoff,))


# ---------------------------------------------------------------------------------------
# beoordeling (UI)
# ---------------------------------------------------------------------------------------
class ReviewError(Exception):
    def __init__(self, status_code: int, message_nl: str) -> None:
        super().__init__(message_nl)
        self.status_code = status_code
        self.message_nl = message_nl


def _latest_policy(ctx: AppContext, case_id: str) -> dict[str, Any] | None:
    row = ctx.db.fetch_one(
        "SELECT r.*, p.proposal_json FROM ai_policy_results r JOIN ai_proposals p "
        "ON p.id=r.proposal_id WHERE r.case_id=? ORDER BY r.id DESC LIMIT 1", (case_id,))
    return dict(row) if row else None


async def approve(ctx: AppContext, case_id: str, actor: str) -> dict[str, Any]:
    case = _case_row(ctx, case_id)
    if case is None:
        raise ReviewError(404, "Casus bestaat niet")
    pol = _latest_policy(ctx, case_id)
    if pol is None or case["status"] not in ("awaiting_approval", "auto_resolved",
                                             "deferred") or \
            pol["decision"] != "await_approval" or pol["execution_status"] not in ("none",
                                                                                   "unavailable",
                                                                                   "blocked",
                                                                                   "timeout",
                                                                                   "failed"):
        raise ReviewError(409, "Er is geen voorstel dat op goedkeuring wacht "
                               "(schaduwvoorstellen zijn alleen ter vergelijking).")
    action = pol["action"]
    result: dict[str, Any]
    if action in actions.EXECUTABLE_ACTIONS and actions.registered(ctx, action) is not None:
        if ctx.emergency_stopped():
            raise ReviewError(409, "Noodstop is actief; AI-acties worden niet uitgevoerd.")
        max_day = int(ai_settings.get(ctx, "ai.bounded_auto.max_actions_per_day"))
        if policy_guard.actions_executed_today(ctx, _day_start_utc(ctx)) >= max_day:
            raise ReviewError(429, f"Dagmaximum AI-acties bereikt ({max_day}).")
        request = actions.ActionRequest(
            case_id=case_id, task_type=case["task_type"], action=action,
            instance_id=case["instance_id"], target_type=case["target_type"],
            target_id=case["target_id"], actor=actor, via="approve")
        result = await actions.execute(
            ctx, request, float(ai_settings.get(ctx, "ai.action_timeout_seconds")))
    else:
        result = {"status": "acknowledged",
                  "detail": "Geaccepteerd; deze actie voert MediaGuard niet automatisch uit — "
                            "volg handmatig op via de betreffende instellingen of pagina."}
    ctx.db.execute(
        "UPDATE ai_policy_results SET execution_status=?, execution_result_json=?, "
        "decided_by=?, executed_at=? WHERE id=?",
        (result["status"], dumps(result), actor, utcnow(), pol["id"]))
    new_status = "approved" if result["status"] in ("executed", "acknowledged") \
        else "auto_resolved"
    _set_status(ctx, case_id, new_status, result.get("detail"))
    audit(ctx.db, actor=actor, action="ai.case.approve", target_type="ai_case",
          target_id=case_id, instance_id=case["instance_id"], correlation_id=case_id,
          reason=f"actie {action}: {result['status']}", new_value=result)
    return {"case_id": case_id, "status": new_status, "action": action, "result": result}


def reject(ctx: AppContext, case_id: str, actor: str, reason: str = "") -> dict[str, Any]:
    case = _case_row(ctx, case_id)
    if case is None:
        raise ReviewError(404, "Casus bestaat niet")
    if case["status"] in ("approved", "auto_executed", "rejected"):
        raise ReviewError(409, "Casus is al afgehandeld")  # auto_resolved mag overruled
    pol = _latest_policy(ctx, case_id)
    if pol is not None:
        ctx.db.execute("UPDATE ai_policy_results SET execution_status='rejected', decided_by=?,"
                       " executed_at=? WHERE id=?", (actor, utcnow(), pol["id"]))
    clean = clean_text(reason, MinimizationReport(), limit=500) if reason else ""
    _set_status(ctx, case_id, "rejected", clean or "Afgewezen door gebruiker")
    audit(ctx.db, actor=actor, action="ai.case.reject", target_type="ai_case",
          target_id=case_id, instance_id=case["instance_id"], reason=clean or None)
    return {"case_id": case_id, "status": "rejected"}


FEEDBACK_VERDICTS = ("correct", "incorrect", "partially_correct", "not_useful")


def feedback(ctx: AppContext, case_id: str, verdict: str, comment: str,
             actor: str) -> dict[str, Any]:
    case = _case_row(ctx, case_id)
    if case is None:
        raise ReviewError(404, "Casus bestaat niet")
    if verdict not in FEEDBACK_VERDICTS:
        raise ReviewError(422, "Ongeldig oordeel")
    prop = ctx.db.fetch_one("SELECT id FROM ai_proposals WHERE case_id=? AND valid=1 "
                            "ORDER BY id DESC LIMIT 1", (case_id,))
    clean = clean_text(comment, MinimizationReport(), limit=2000) if comment else None
    fid = ctx.db.execute_insert(
        "INSERT INTO ai_feedback(case_id,proposal_id,verdict,comment,actor,ts) "
        "VALUES (?,?,?,?,?,?)", (case_id, prop["id"] if prop else None, verdict, clean, actor,
                                 utcnow()))
    audit(ctx.db, actor=actor, action="ai.case.feedback", target_type="ai_case",
          target_id=case_id, reason=verdict)
    return {"id": fid, "case_id": case_id, "verdict": verdict}


# ---------------------------------------------------------------------------------------
# weergave
# ---------------------------------------------------------------------------------------
def case_summary(ctx: AppContext, row: dict[str, Any]) -> dict[str, Any]:
    cost = ctx.db.fetch_one(
        "SELECT COALESCE(SUM(cost),0) AS cost, SUM(CASE WHEN cost_source LIKE 'estimate%' "
        "THEN 1 ELSE 0 END) AS est FROM ai_usage WHERE case_id=?", (row["id"],))
    latest = ctx.db.fetch_one(
        "SELECT proposal_json, provider, model, from_cache FROM ai_proposals WHERE case_id=? "
        "AND valid=1 ORDER BY id DESC LIMIT 1", (row["id"],))
    prop = loads(latest["proposal_json"]) if latest else None
    return {
        "id": row["id"], "task_type": row["task_type"],
        "task_label_nl": TASK_LABELS_NL.get(row["task_type"], row["task_type"]),
        "reason_code": row["reason_code"], "status": row["status"],
        "status_label_nl": STATUS_LABELS_NL.get(row["status"], row["status"]),
        "status_detail": row["status_detail"], "target_type": row["target_type"],
        "target_id": row["target_id"], "instance_id": row["instance_id"],
        "source_module": row["source_module"], "created_at": row["created_at"],
        "updated_at": row["updated_at"], "analysis_count": row["analysis_count"],
        "suggested_action": prop["suggested_action"] if prop else None,
        "uncertainty": prop["uncertainty"] if prop else None,
        "model": latest["model"] if latest else None,
        "provider": latest["provider"] if latest else None,
        "cost": round(float(cost["cost"]), 6) if cost else 0.0,
        "cost_is_estimate": bool(cost and cost["est"]),
    }


def case_detail(ctx: AppContext, case_id: str) -> dict[str, Any] | None:
    row = _case_row(ctx, case_id)
    if row is None:
        return None
    data = case_summary(ctx, row)
    ev_ids = loads(row["evidence_ids_json"], [])
    known = evidence.lookup(ctx, ev_ids)
    proposals = []
    for p in ctx.db.fetch_dicts("SELECT * FROM ai_proposals WHERE case_id=? ORDER BY id",
                                (case_id,)):
        pols = ctx.db.fetch_dicts("SELECT * FROM ai_policy_results WHERE proposal_id=? "
                                  "ORDER BY id", (p["id"],))
        for pol in pols:
            pol["reasons"] = loads(pol.pop("reasons_json"), [])
            pol["execution_result"] = loads(pol.pop("execution_result_json"))
            pol["executable"] = bool(pol["executable"])
        proposals.append({
            "id": p["id"], "provider": p["provider"], "model": p["model"], "mode": p["mode"],
            "from_cache": bool(p["from_cache"]), "valid": bool(p["valid"]),
            "proposal": loads(p["proposal_json"]),
            "rejection_reasons": loads(p["rejection_reasons_json"], []),
            "created_at": p["created_at"], "policy_results": pols,
        })
    data.update({
        "why_ai_nl": (f"De deterministische classifier kon dit niet eenduidig oplossen en gaf "
                      f"needs_advice-code {row['reason_code']} "
                      f"({TASK_LABELS_NL.get(row['task_type'], row['task_type'])})."),
        "payload": loads(row["payload_json"], {}),
        "minimization": loads(row["minimization_json"], {}),
        "evidence": [{"evidence_id": e, "exists": e in known, **known.get(e, {})}
                     for e in ev_ids],
        "proposals": proposals,
        "feedback": ctx.db.fetch_dicts("SELECT * FROM ai_feedback WHERE case_id=? ORDER BY id",
                                       (case_id,)),
        "usage": ctx.db.fetch_dicts(
            "SELECT ts, provider, model, purpose, status, attempt, input_tokens, output_tokens,"
            " reserved_cost, cost, currency, cost_source, error FROM ai_usage WHERE case_id=? "
            "ORDER BY id", (case_id,)),
        # overrulen achteraf (zero-interactie: niets wacht hierop)
        "can_approve": row["status"] in ("awaiting_approval", "auto_resolved", "deferred"),
        "can_reject": row["status"] not in ("approved", "auto_executed", "rejected"),
    })
    return data


def preview(ctx: AppContext, case_id: str, provider: str | None = None) -> dict[str, Any] | None:
    case = _case_row(ctx, case_id)
    if case is None:
        return None
    provider = provider or current_provider(ctx)
    if provider not in PROVIDERS:
        provider = "anthropic"
    model = ai_settings.provider_model(ctx, provider, case["task_type"]) or "<kies een model>"
    parts = prompt_parts(ctx, case)
    adapter = make_adapter(ctx, provider)
    max_out = int(ai_settings.get(ctx, "ai.max_output_tokens"))
    prepared = adapter.prepare(model=model, system=parts["system"], user=parts["user"],
                               schema=parts["schema"], max_output_tokens=max_out)
    in_est = budget.estimate_input_tokens(parts["system"], parts["user"],
                                          json.dumps(parts["schema"]))
    price = budget.price_for(ctx, provider, model)
    reserve = round(budget.cost_of(ctx, price, in_est, max_out) * budget.SAFETY_MARGIN, 6) \
        if price else None
    return {
        "case_id": case_id, "provider": provider, "model": model, "method": "POST",
        "url": prepared.url, "headers": prepared.headers_public, "body": prepared.body,
        "minimization": loads(case["minimization_json"], {}),
        "evidence_not_found_locally": parts["unknown_evidence"],
        "estimated_input_tokens": in_est, "max_output_tokens": max_out,
        "reservation_estimate": reserve, "currency": budget.budget_currency(ctx),
        "consent": consent_status(ctx, provider),
        "note_nl": "Dit is exact het verzoek dat naar de provider gaat (API-sleutel "
                   "gemaskeerd). Geen bestanden, frames, audio, sleutels of absolute paden.",
    }


# ---------------------------------------------------------------------------------------
# testverbinding
# ---------------------------------------------------------------------------------------
TEST_CASE_ID = "mg-test-0001"
TEST_EVIDENCE = {
    "ev-test-xem": {"kind": "thexem_mapping",
                    "summary": "TheXEM: scene S02E01 = TVDB S01E13 (absoluut 13)"},
    "ev-test-release": {"kind": "release_name",
                        "summary": "[Group] Voorbeeldserie - 13 [1080p].mkv"},
}
TEST_PAYLOAD = {
    "series": "Voorbeeldserie (testcasus)",
    "candidate_numbers": {"scene": "S02E01", "tvdb": "S01E13", "absolute": 13},
    "deterministic_result": "uncertain",
}


async def test_provider(ctx: AppContext, provider: str, model: str | None,
                        actor: str) -> dict[str, Any]:
    """Echte gestructureerde testcall met een synthetische casus (geen gebruikersdata)."""
    model = (model or ai_settings.provider_model(ctx, provider)).strip()
    blockers = enable_blockers(ctx, provider, model, require_test=False)
    if blockers:
        return {"ok": False, "provider": provider, "model": model, "blockers": blockers,
                "message_nl": " ".join(blockers)}
    system, user, schema = build_prompt(
        case_id=TEST_CASE_ID, task_type="anime_identity", reason_code="CONFLICTING_EPISODE_MAP",
        payload=TEST_PAYLOAD, evidence=TEST_EVIDENCE)
    outcome = await call_structured(ctx, provider=provider, model=model, task_type=None,
                                    case_id=None, purpose="test", system=system, user=user,
                                    schema=schema)
    ok, detail, proposal = False, outcome.message_nl, None
    if outcome.ok:
        proposal, errors = validate_advice(outcome.data, case_id=TEST_CASE_ID,
                                           task_type="anime_identity",
                                           case_evidence_ids=TEST_EVIDENCE.keys())
        ok = proposal is not None
        detail = "Gestructureerd antwoord ontvangen en gevalideerd." if ok else \
            "Antwoord voldeed niet aan het schema: " + "; ".join(errors)
    _upsert_provider_config(ctx, provider, last_test_at=utcnow(), last_test_ok=int(ok),
                            last_test_model=model, last_test_detail=detail[:500],
                            last_test_latency_ms=outcome.latency_ms)
    audit(ctx.db, actor=actor, action="ai.provider.test", target_type="ai_provider",
          target_id=provider, reason=f"{model}: {'ok' if ok else 'mislukt'}")
    return {"ok": ok, "provider": provider, "model": model, "message_nl": detail,
            "proposal": proposal, "cost": round(outcome.cost, 6),
            "input_tokens": outcome.input_tokens, "output_tokens": outcome.output_tokens,
            "latency_ms": outcome.latency_ms}


async def refresh_models(ctx: AppContext, provider: str) -> dict[str, Any]:
    if not ctx.secrets.has(SECRET_NAMES[provider]):
        return {"ok": False, "models": [], "message_nl": "Geen API-sleutel ingesteld."}
    try:
        models = await make_adapter(ctx, provider).list_models(
            float(ai_settings.get(ctx, "ai.timeout_seconds")))
    except ProviderError as exc:
        _upsert_provider_config(ctx, provider, models_error=_provider_error_nl(exc)[:300])
        return {"ok": False, "models": provider_config(ctx, provider)["models"],
                "message_nl": _provider_error_nl(exc)}
    models.sort(key=lambda m: m["id"])
    _upsert_provider_config(ctx, provider, models_json=dumps(models),
                            models_fetched_at=utcnow(), models_error=None)
    return {"ok": True, "models": models, "message_nl": f"{len(models)} modellen gevonden."}


# ---------------------------------------------------------------------------------------
# job + opstart
# ---------------------------------------------------------------------------------------
@job_handler("ai.analyze")
async def _analyze_job(jc: JobContext) -> dict[str, Any]:
    result = await analyze_case(jc.app, str(jc.payload.get("case_id", "")))
    return {"status": result.get("status")}


def on_startup(ctx: AppContext) -> dict[str, Any]:
    recovered = budget.recover_stale_reservations(ctx)
    try:
        _purge_old(ctx)
    except Exception:  # noqa: BLE001
        LOGGER.exception("opschonen AI-cases mislukt")
    # cases die bij een crash 'analyzing' bleven: terug naar wachtrij/overgeslagen
    ctx.db.execute("UPDATE ai_cases SET status='failed', status_detail=? WHERE "
                   "status='analyzing'", ("onderbroken door herstart; opnieuw onderzoeken",))
    resolved = 0
    try:
        resolved = resolve_waiting_cases(ctx)
    except Exception:  # noqa: BLE001
        LOGGER.exception("oude AI-akkoordcases afhandelen mislukt")
    return {"recovered_reservations": recovered, "auto_resolved": resolved}
