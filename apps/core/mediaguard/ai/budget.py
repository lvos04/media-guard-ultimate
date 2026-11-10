"""Budgetbewaking (DEEL S2): reserveren vóór de call, afrekenen met werkelijk gebruik.

- Alles staat in SQLite (``ai_usage`` + ``ai_budgets``): een herstart reset niets.
- Periodes (maand/dag) volgen ``system.timezone``.
- Reserveren gebeurt in één ``BEGIN IMMEDIATE``-transactie: parallelle analyses kunnen
  samen nooit boven de maandlimiet uitkomen.
- Openstaande reserveringen van een gecrashte run worden bij opstart conservatief als
  kosten geboekt (``settled_estimated``).
"""

from __future__ import annotations

import math
from collections.abc import Callable
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import TYPE_CHECKING, Any

from mediaguard.ai import settings as ai_settings
from mediaguard.db import utcnow

if TYPE_CHECKING:
    from mediaguard.context import AppContext

SAFETY_MARGIN = 1.25
CHARS_PER_TOKEN = 3.0  # conservatief (meestal ~4 tekens per token)
SPENT_STATUSES = ("settled", "settled_estimated")


class BudgetExceeded(Exception):
    def __init__(self, code: str, message_nl: str) -> None:
        super().__init__(message_nl)
        self.code = code
        self.message_nl = message_nl


@dataclass(frozen=True)
class Price:
    input_per_mtok: float
    output_per_mtok: float
    currency: str


def now_utc(ctx: AppContext) -> datetime:
    clock: Callable[[], datetime] | None = ctx.services.get("ai_clock")
    return clock() if clock else datetime.now(UTC)


def periods(ctx: AppContext) -> tuple[str, str, str]:
    tz = ai_settings.timezone(ctx)
    local = now_utc(ctx).astimezone(tz)
    return local.strftime("%Y-%m"), local.strftime("%Y-%m-%d"), str(tz.key)


def budget_currency(ctx: AppContext) -> str:
    return str(ai_settings.get(ctx, "ai.budget.currency"))


def price_for(ctx: AppContext, provider: str, model: str) -> Price | None:
    table = ai_settings.get(ctx, "ai.price_table") or {}
    entry = table.get(f"{provider}:{model}") if isinstance(table, dict) else None
    if not isinstance(entry, dict):
        # opgeslagen tabel van een oudere versie: meegeleverde prijs gebruiken (rapport M17)
        entry = ai_settings.DEFAULT_PRICE_TABLE.get(f"{provider}:{model}")
    if not isinstance(entry, dict):
        return None
    try:
        return Price(float(entry["input_per_mtok"]), float(entry["output_per_mtok"]),
                     str(entry.get("currency", "USD")))
    except (KeyError, TypeError, ValueError):
        return None


def _convert(ctx: AppContext, amount: float, from_currency: str) -> float:
    target = budget_currency(ctx)
    if from_currency == target:
        return amount
    rate = float(ai_settings.get(ctx, "ai.budget.usd_to_eur"))
    if from_currency == "USD" and target == "EUR":
        return amount * rate
    if from_currency == "EUR" and target == "USD":
        return amount / rate
    return amount


def cost_of(ctx: AppContext, price: Price, input_tokens: int, output_tokens: int) -> float:
    raw = input_tokens / 1e6 * price.input_per_mtok + output_tokens / 1e6 * price.output_per_mtok
    return round(_convert(ctx, raw, price.currency), 8)


def estimate_input_tokens(*texts: str) -> int:
    return int(math.ceil(sum(len(t) for t in texts) / CHARS_PER_TOKEN)) + 50


def _ensure_period_rows(ctx: AppContext, conn: Any, month: str, day: str, tz: str) -> None:
    limit = float(ai_settings.get(ctx, "ai.budget.monthly_limit"))
    currency = budget_currency(ctx)
    for key, ptype, lim in ((f"month:{month}", "month", limit), (f"day:{day}", "day", None)):
        conn.execute(
            "INSERT INTO ai_budgets(period_key,period_type,timezone,limit_amount,currency,"
            "updated_at) VALUES (?,?,?,?,?,?) ON CONFLICT(period_key) DO UPDATE SET "
            "limit_amount=COALESCE(excluded.limit_amount, ai_budgets.limit_amount), "
            "currency=excluded.currency",
            (key, ptype, tz, lim, currency, utcnow()),
        )


def month_totals(ctx: AppContext, month: str | None = None) -> dict[str, float]:
    month = month or periods(ctx)[0]
    row = ctx.db.fetch_one(
        "SELECT COALESCE(SUM(CASE WHEN status IN ('settled','settled_estimated') THEN cost END),0)"
        " AS spent, COALESCE(SUM(CASE WHEN status='reserved' THEN reserved_cost END),0) AS"
        " reserved FROM ai_usage WHERE period_month=?", (month,))
    return {"spent": float(row["spent"]) if row else 0.0,
            "reserved": float(row["reserved"]) if row else 0.0}


def calls_today(ctx: AppContext, day: str | None = None) -> int:
    day = day or periods(ctx)[1]
    row = ctx.db.fetch_one(
        "SELECT COUNT(*) AS n FROM ai_usage WHERE period_day=? AND status!='cache_hit' "
        "AND status!='refused'", (day,))
    return int(row["n"]) if row else 0


def reserve(ctx: AppContext, *, provider: str, model: str, task_type: str | None,
            case_id: str | None, purpose: str, input_tokens_est: int, max_output_tokens: int,
            already_spent_this_analysis: float = 0.0, attempt: int = 1) -> tuple[int, float]:
    """Reserveert conservatief. Geeft (usage_id, gereserveerd bedrag) of BudgetExceeded."""
    price = price_for(ctx, provider, model)
    if price is None:
        raise BudgetExceeded(
            "price_unknown",
            f"Geen prijs bekend voor {provider}:{model}. Vul de prijstabel in "
            "(AI Adviseur → Kosten) — controleer actuele providerprijzen.")
    reserved = round(cost_of(ctx, price, input_tokens_est, max_output_tokens) * SAFETY_MARGIN, 8)
    per_analysis = float(ai_settings.get(ctx, "ai.budget.per_analysis_limit"))
    monthly = float(ai_settings.get(ctx, "ai.budget.monthly_limit"))
    max_calls = int(ai_settings.get(ctx, "ai.budget.max_calls_per_day"))
    currency = budget_currency(ctx)
    if purpose == "analysis" and already_spent_this_analysis + reserved > per_analysis:
        raise BudgetExceeded(
            "per_analysis_limit",
            f"Geschatte kosten ({reserved:.4f} {currency}) overschrijden het plafond per "
            f"analyse ({per_analysis:.4f} {currency}). Verlaag max tokens of verhoog het "
            "plafond.")
    month, day, tz = periods(ctx)
    with ctx.db.transaction() as conn:
        _ensure_period_rows(ctx, conn, month, day, tz)
        row = conn.execute(
            "SELECT COALESCE(SUM(CASE WHEN status IN ('settled','settled_estimated') THEN cost"
            " END),0) AS spent, COALESCE(SUM(CASE WHEN status='reserved' THEN reserved_cost"
            " END),0) AS reserved FROM ai_usage WHERE period_month=?", (month,)).fetchone()
        committed = float(row["spent"]) + float(row["reserved"])
        if committed + reserved > monthly + 1e-12:
            raise BudgetExceeded(
                "monthly_limit",
                f"Maandbudget bereikt: {committed:.4f} van {monthly:.2f} {currency} gebruikt of "
                f"gereserveerd; deze call zou {reserved:.4f} {currency} reserveren.")
        n = conn.execute(
            "SELECT COUNT(*) AS n FROM ai_usage WHERE period_day=? AND status NOT IN "
            "('cache_hit','refused')", (day,)).fetchone()["n"]
        if int(n) >= max_calls:
            raise BudgetExceeded("daily_calls",
                                 f"Maximum aantal AI-calls per dag bereikt ({max_calls}).")
        cur = conn.execute(
            "INSERT INTO ai_usage(ts,period_month,period_day,provider,model,task_type,case_id,"
            "purpose,status,attempt,reserved_cost,currency) VALUES (?,?,?,?,?,?,?,?,?,?,?,?)",
            (utcnow(), month, day, provider, model, task_type, case_id, purpose, "reserved",
             attempt, reserved, currency))
        conn.execute("UPDATE ai_budgets SET calls=calls+1, updated_at=? WHERE period_key IN "
                     "(?,?)", (utcnow(), f"month:{month}", f"day:{day}"))
        if attempt > 1:
            conn.execute("UPDATE ai_budgets SET retries=retries+1 WHERE period_key IN (?,?)",
                         (f"month:{month}", f"day:{day}"))
        usage_id = int(cur.lastrowid or 0)
    return usage_id, reserved


def retries_today(ctx: AppContext) -> int:
    _, day, _ = periods(ctx)
    row = ctx.db.fetch_one("SELECT retries FROM ai_budgets WHERE period_key=?", (f"day:{day}",))
    return int(row["retries"]) if row else 0


def settle(ctx: AppContext, usage_id: int, *, provider: str, model: str,
           input_tokens: int | None, output_tokens: int | None, billed_possible: bool,
           error: str | None = None, latency_ms: int | None = None) -> float:
    """Rekent af. Met werkelijke tokens → providerkosten; zonder tokens maar mogelijk
    gefactureerd (time-out) → reservering als geschatte kosten; anders 0."""
    row = ctx.db.fetch_one("SELECT reserved_cost, period_month FROM ai_usage WHERE id=?",
                           (usage_id,))
    if row is None:
        return 0.0
    price = price_for(ctx, provider, model)
    if input_tokens is not None and output_tokens is not None and price is not None:
        cost, status, source = cost_of(ctx, price, input_tokens, output_tokens), "settled", \
            "provider_usage"
    elif billed_possible:
        cost, status, source = float(row["reserved_cost"]), "settled_estimated", "estimate"
    else:
        cost, status, source = 0.0, "settled", "no_charge"
    ctx.db.execute(
        "UPDATE ai_usage SET status=?, input_tokens=?, output_tokens=?, cost=?, cost_source=?,"
        " error=?, latency_ms=? WHERE id=?",
        (status, input_tokens or 0, output_tokens or 0, cost, source,
         (error or None) and error[:300], latency_ms, usage_id))
    _refresh_spent(ctx, str(row["period_month"]))
    return cost


def _refresh_spent(ctx: AppContext, month: str) -> None:
    totals = month_totals(ctx, month)
    ctx.db.execute("UPDATE ai_budgets SET spent=?, updated_at=? WHERE period_key=?",
                   (totals["spent"], utcnow(), f"month:{month}"))


def record_cache_hit(ctx: AppContext, *, provider: str, model: str, task_type: str,
                     case_id: str) -> None:
    month, day, tz = periods(ctx)
    with ctx.db.transaction() as conn:
        _ensure_period_rows(ctx, conn, month, day, tz)
        conn.execute(
            "INSERT INTO ai_usage(ts,period_month,period_day,provider,model,task_type,case_id,"
            "purpose,status,currency,cost_source) VALUES (?,?,?,?,?,?,?,?,?,?,?)",
            (utcnow(), month, day, provider, model, task_type, case_id, "analysis",
             "cache_hit", budget_currency(ctx), "cache"))
        conn.execute("UPDATE ai_budgets SET cache_hits=cache_hits+1 WHERE period_key IN (?,?)",
                     (f"month:{month}", f"day:{day}"))


def recover_stale_reservations(ctx: AppContext) -> int:
    """Na een crash/herstart: open reserveringen conservatief als kosten boeken."""
    rows = ctx.db.fetch_all("SELECT id, period_month FROM ai_usage WHERE status='reserved'")
    if not rows:
        return 0
    ctx.db.execute(
        "UPDATE ai_usage SET status='settled_estimated', cost=reserved_cost, "
        "cost_source='estimate_after_restart' WHERE status='reserved'")
    for month in {str(r["period_month"]) for r in rows}:
        _refresh_spent(ctx, month)
    return len(rows)


def usage_summary(ctx: AppContext) -> dict[str, Any]:
    month, day, tz = periods(ctx)
    totals = month_totals(ctx, month)
    monthly = float(ai_settings.get(ctx, "ai.budget.monthly_limit"))
    budget_row = ctx.db.fetch_one("SELECT * FROM ai_budgets WHERE period_key=?",
                                  (f"month:{month}",))
    day_row = ctx.db.fetch_one("SELECT * FROM ai_budgets WHERE period_key=?", (f"day:{day}",))

    def breakdown(column: str, where: str, param: str) -> list[dict[str, Any]]:
        return ctx.db.fetch_dicts(
            f"SELECT {column} AS key, COUNT(*) AS calls, "
            "SUM(CASE WHEN status='cache_hit' THEN 1 ELSE 0 END) AS cache_hits, "
            "SUM(input_tokens) AS input_tokens, SUM(output_tokens) AS output_tokens, "
            "ROUND(SUM(cost), 6) AS cost, "
            "SUM(CASE WHEN cost_source LIKE 'estimate%' THEN 1 ELSE 0 END) AS estimated "
            f"FROM ai_usage WHERE {where}=? GROUP BY {column} ORDER BY {column}", (param,))

    return {
        "currency": budget_currency(ctx),
        "timezone": tz,
        "month": month,
        "day": day,
        "monthly_limit": monthly,
        "spent": round(totals["spent"], 6),
        "reserved": round(totals["reserved"], 6),
        "remaining": round(max(0.0, monthly - totals["spent"] - totals["reserved"]), 6),
        "calls_today": calls_today(ctx, day),
        "max_calls_per_day": int(ai_settings.get(ctx, "ai.budget.max_calls_per_day")),
        "per_analysis_limit": float(ai_settings.get(ctx, "ai.budget.per_analysis_limit")),
        "cache_hits_month": int(budget_row["cache_hits"]) if budget_row else 0,
        "retries_today": int(day_row["retries"]) if day_row else 0,
        "by_provider": breakdown("provider || ':' || model", "period_month", month),
        "by_task": breakdown("COALESCE(task_type, purpose)", "period_month", month),
        "by_day": breakdown("period_day", "period_month", month),
        "price_note_nl": ai_settings.PRICE_NOTE_NL,
        "price_table": ai_settings.get(ctx, "ai.price_table"),
    }
