"""Instellingen van de AI Adviseur (allemaal via de UI te beheren, Nederlandse labels)."""

from __future__ import annotations

from typing import TYPE_CHECKING, Any
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from mediaguard.ai.constants import (
    ACTION_LABELS_NL,
    AUTO_ELIGIBLE_ACTIONS,
    DEFAULT_AUTO_ACTIONS,
    MODE_LABELS_NL,
    MODES,
    PROVIDER_LABELS_NL,
    TASK_LABELS_NL,
    TASK_TYPES,
)
from mediaguard.settings_registry import REGISTRY, SettingDef

if TYPE_CHECKING:
    from mediaguard.context import AppContext

CATEGORY = "AI Adviseur"
PRICE_NOTE_NL = (
    "Prijzen van de standaardmodellen (Claude en OpenAI) worden met MediaGuard-updates "
    "meegeleverd (stand nov 2026); je hoeft niets in te vullen. Kosten worden berekend uit "
    "het werkelijke tokengebruik dat de API teruggeeft. Alleen voor een eigen, onbekend "
    "model vul je hier zelf een prijs in."
)
PRICES_AS_OF = "2026-11"

# Meegeleverde prijzen (Anthropic API, USD per miljoen tokens). Een eigen waarde in de
# prijstabel gaat altijd voor; ontbreekt een model daar, dan geldt deze tabel.
DEFAULT_PRICE_TABLE: dict[str, dict[str, Any]] = {
    "anthropic:claude-haiku-5-5": {"input_per_mtok": 0.10, "output_per_mtok": 0.50,
                                   "currency": "USD"},
    "anthropic:claude-sonnet-5-5": {"input_per_mtok": 2.00, "output_per_mtok": 10.00,
                                    "currency": "USD"},
    "anthropic:claude-opus-5-5": {"input_per_mtok": 4.00, "output_per_mtok": 20.00,
                                  "currency": "USD"},
    "anthropic:claude-fable-5-1": {"input_per_mtok": 10.00, "output_per_mtok": 50.00,
                                   "currency": "USD"},
    # OpenAI, Standard-tarief, korte context (bron: developers.openai.com/api/docs/pricing,
    # gecontroleerd 2026-11-04). Batch/Flex is de helft; Fast/Ultrafast niet gebruikt.
    "openai:gpt-6-astra": {"input_per_mtok": 10.00, "output_per_mtok": 50.00,
                           "currency": "USD"},
    "openai:gpt-6.1-sol": {"input_per_mtok": 2.00, "output_per_mtok": 10.00,
                           "currency": "USD"},
    "openai:gpt-6.1-terra": {"input_per_mtok": 1.00, "output_per_mtok": 5.00,
                             "currency": "USD"},
    "openai:gpt-6-luna": {"input_per_mtok": 0.10, "output_per_mtok": 0.50,
                          "currency": "USD"},
}

DEFAULT_ANTHROPIC_MODELS = ("claude-haiku-5-5", "claude-sonnet-5-5")
DEFAULT_OPENAI_MODELS = ("gpt-6-luna", "gpt-6.1-terra")

DEFS: tuple[SettingDef, ...] = (
    SettingDef("ai.provider", "enum", "off", CATEGORY, "AI-provider", "AI provider",
               help_nl="Claude of OpenAI via de officiële betaalde API. Een Claude- of "
                       "ChatGPT-abonnement is géén API-tegoed.",
               choices=("off", "anthropic", "openai"),
               choice_labels_nl=dict(PROVIDER_LABELS_NL)),
    SettingDef("ai.mode", "enum", "disabled", CATEGORY, "AI-beleid", "AI mode",
               help_nl="Automatisch (veilig): met sleutel en toestemming beslist de AI zelf "
                       "bij twijfel. Alleen niet-destructieve acties worden uitgevoerd (na de "
                       "automatische schaduwfase en benchmark); anders houdt MediaGuard de "
                       "veilige standaard aan. Er wacht nooit iets op jouw akkoord; je kunt "
                       "achteraf overrulen. Schaduw = alleen loggen, Uit = geen AI.",
               choices=MODES, choice_labels_nl=dict(MODE_LABELS_NL),
               effect_nl="Inschakelen vereist API-sleutel, toestemming en een geslaagde "
                         "testverbinding."),
    SettingDef("ai.anthropic.model", "string", "claude-haiku-5-5", CATEGORY,
               "Claude-model", "Claude model",
               help_nl="Kies uit de live modellijst. Goedkoop: claude-haiku-5-5; "
                       "sterker: claude-sonnet-5-5."),
    SettingDef("ai.openai.model", "string", "", CATEGORY, "OpenAI-model", "OpenAI model",
               help_nl="Kies een model uit de live modellijst van je OpenAI-account."),
    SettingDef("ai.anthropic.base_url", "string", "https://api.anthropic.com", CATEGORY,
               "Claude API-adres", "Claude API base URL", advanced=True),
    SettingDef("ai.openai.base_url", "string", "https://api.openai.com", CATEGORY,
               "OpenAI API-adres", "OpenAI API base URL", advanced=True),
    SettingDef("ai.anthropic.effort", "enum", "low", CATEGORY, "Claude denkniveau (effort)",
               "Claude effort", choices=("low", "medium", "high"),
               choice_labels_nl={"low": "Laag (goedkoopst)", "medium": "Middel",
                                 "high": "Hoog"}, advanced=True),
    SettingDef("ai.task_types", "list", ["anime_identity", "missing_diagnosis",
                                         "release_explanation"], CATEGORY,
               "Doelcases voor AI", "AI task types", choices=TASK_TYPES,
               choice_labels_nl=dict(TASK_LABELS_NL)),
    SettingDef("ai.task_models", "json", {}, CATEGORY, "Model per taaktype",
               "Model per task type",
               help_nl="Optioneel: {\"missing_diagnosis\": \"claude-sonnet-5-5\"}. Leeg = "
                       "providermodel.", advanced=True),
    SettingDef("ai.budget.currency", "enum", "EUR", CATEGORY, "Budgetvaluta",
               "Budget currency", choices=("EUR", "USD"),
               choice_labels_nl={"EUR": "Euro (€)", "USD": "Dollar ($)"}),
    SettingDef("ai.budget.monthly_limit", "float", 5.0, CATEGORY, "Maandbudget",
               "Monthly budget", minimum=0, maximum=10000, unit="valuta",
               help_nl="Harde limiet per kalendermaand (tijdzone uit Systeem). Vooraf wordt "
                       "conservatief gereserveerd zodat de limiet niet overschreden wordt."),
    SettingDef("ai.budget.per_analysis_limit", "float", 0.05, CATEGORY,
               "Kostenplafond per analyse", "Per-analysis cost cap", minimum=0, maximum=100,
               unit="valuta"),
    SettingDef("ai.budget.max_calls_per_day", "int", 25, CATEGORY, "Maximaal analyses per dag",
               "Max calls per day", minimum=0, maximum=10000),
    SettingDef("ai.budget.usd_to_eur", "float", 0.95, CATEGORY, "Wisselkoers USD→EUR",
               "USD to EUR rate", minimum=0.01, maximum=10,
               help_nl="Voor omrekening van dollarprijzen naar het eurobudget. Controleer "
                       "regelmatig; kies liever iets te hoog (conservatief)."),
    SettingDef("ai.price_table", "json", DEFAULT_PRICE_TABLE, CATEGORY,
               "Prijstabel per model", "Price table per model", help_nl=PRICE_NOTE_NL),
    SettingDef("ai.max_output_tokens", "int", 1500, CATEGORY, "Max antwoordtokens",
               "Max output tokens", minimum=200, maximum=16000),
    SettingDef("ai.max_input_tokens", "int", 8000, CATEGORY, "Max invoertokens (schatting)",
               "Max input tokens", minimum=500, maximum=100000),
    SettingDef("ai.timeout_seconds", "int", 30, CATEGORY, "Time-out per call",
               "Timeout per call", minimum=3, maximum=300, unit="s"),
    SettingDef("ai.max_retries", "int", 1, CATEGORY, "Herhaalpogingen per analyse",
               "Retries per analysis", minimum=0, maximum=3),
    SettingDef("ai.retry_budget_per_day", "int", 10, CATEGORY, "Retrybudget per dag",
               "Retry budget per day", minimum=0, maximum=1000),
    SettingDef("ai.cache_ttl_days", "int", 30, CATEGORY, "Cache-bewaartijd identieke cases",
               "Case cache TTL", minimum=0, maximum=365, unit="dagen"),
    SettingDef("ai.retention_days", "int", 90, CATEGORY, "Bewaartermijn AI-cases",
               "AI case retention", minimum=1, maximum=3650, unit="dagen"),
    SettingDef("ai.bounded_auto.allowed_actions", "list", list(DEFAULT_AUTO_ACTIONS),
               CATEGORY, "Automatisch toegestane acties", "Auto-allowed actions",
               choices=AUTO_ELIGIBLE_ACTIONS,
               choice_labels_nl={a: ACTION_LABELS_NL[a] for a in AUTO_ELIGIBLE_ACTIONS},
               help_nl="Alleen niet-destructieve, omkeerbare acties zijn kiesbaar."),
    SettingDef("ai.bounded_auto.min_shadow_cases", "int", 20, CATEGORY,
               "Minimum schaduwcases vóór automatisch", "Min shadow cases", minimum=0,
               maximum=10000),
    SettingDef("ai.bounded_auto.min_benchmark_accuracy", "float", 0.8, CATEGORY,
               "Minimale benchmarkscore", "Min benchmark accuracy", minimum=0, maximum=1),
    SettingDef("ai.bounded_auto.min_improvement", "float", 0.1, CATEGORY,
               "Minimale verbetering t.o.v. regels", "Min improvement over baseline",
               minimum=0, maximum=1),
    SettingDef("ai.bounded_auto.max_actions_per_day", "int", 10, CATEGORY,
               "Max AI-acties per dag", "Max AI actions per day", minimum=0, maximum=1000),
    SettingDef("ai.action_timeout_seconds", "int", 30, CATEGORY, "Time-out AI-actie",
               "AI action timeout", minimum=1, maximum=600, unit="s", advanced=True),
)


def register() -> None:
    REGISTRY.register(*DEFS)


def get(ctx: AppContext, key: str) -> Any:
    return ctx.config.value(key)


def timezone(ctx: AppContext) -> ZoneInfo:
    try:
        name = ctx.config.value("system.timezone")
    except KeyError:
        name = "Europe/Amsterdam"
    try:
        return ZoneInfo(str(name))
    except (ZoneInfoNotFoundError, ValueError):
        return ZoneInfo("Europe/Amsterdam")


def provider_model(ctx: AppContext, provider: str, task_type: str | None = None) -> str:
    if task_type:
        overrides = get(ctx, "ai.task_models") or {}
        if isinstance(overrides, dict) and isinstance(overrides.get(task_type), str) \
                and overrides[task_type].strip():
            return str(overrides[task_type]).strip()
    return str(get(ctx, f"ai.{provider}.model") or "").strip()


def validate_price_table(value: Any) -> dict[str, dict[str, Any]]:
    if not isinstance(value, dict):
        raise ValueError("ai.price_table: verwacht een object {\"provider:model\": {...}}")
    out: dict[str, dict[str, Any]] = {}
    for key, entry in value.items():
        if not isinstance(key, str) or ":" not in key:
            raise ValueError(f"ai.price_table: sleutel '{key}' moet 'provider:model' zijn")
        if not isinstance(entry, dict):
            raise ValueError(f"ai.price_table[{key}]: verwacht object")
        clean: dict[str, Any] = {}
        for field in ("input_per_mtok", "output_per_mtok"):
            num = entry.get(field)
            if isinstance(num, bool) or not isinstance(num, int | float) or num < 0:
                raise ValueError(f"ai.price_table[{key}].{field}: verwacht getal ≥ 0")
            clean[field] = float(num)
        currency = entry.get("currency", "USD")
        if currency not in ("USD", "EUR"):
            raise ValueError(f"ai.price_table[{key}].currency: USD of EUR")
        clean["currency"] = currency
        out[key] = clean
    return out


def validate_task_models(value: Any) -> dict[str, str]:
    if not isinstance(value, dict):
        raise ValueError("ai.task_models: verwacht een object")
    for key, model in value.items():
        if key not in TASK_TYPES:
            raise ValueError(f"ai.task_models: onbekend taaktype {key}")
        if not isinstance(model, str) or len(model) > 200:
            raise ValueError(f"ai.task_models[{key}]: verwacht modelnaam")
    return dict(value)
