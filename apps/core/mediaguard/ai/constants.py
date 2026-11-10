"""Vaste vocabulaires van de AI Decision Advisor (DEEL S3).

Alles wat het model mag teruggeven is hier expliciet opgesomd. Onbekende reason- of
actioncodes worden afgewezen; er bestaat bewust géén code voor destructieve acties.
"""

from __future__ import annotations

PROVIDERS: tuple[str, ...] = ("anthropic", "openai")
PROVIDER_LABELS_NL: dict[str, str] = {
    "off": "Uit",
    "anthropic": "Claude (Anthropic API)",
    "openai": "OpenAI API",
}
SECRET_NAMES: dict[str, str] = {
    "anthropic": "ai:anthropic:apikey",
    "openai": "ai:openai:apikey",
}

MODES: tuple[str, ...] = ("disabled", "shadow", "advisory", "bounded_auto")
MODE_LABELS_NL: dict[str, str] = {
    "disabled": "Uit",
    "shadow": "Schaduw (alleen loggen)",
    "advisory": "Advies (werkt nu automatisch: wacht nooit op een klik)",
    "bounded_auto": "Automatisch (veilig)",
}

TASK_TYPES: tuple[str, ...] = (
    "anime_identity", "missing_diagnosis", "release_explanation", "encode_advice",
)
TASK_LABELS_NL: dict[str, str] = {
    "anime_identity": "Onduidelijke anime-identiteit",
    "missing_diagnosis": "Hardnekkig ontbrekende aflevering",
    "release_explanation": "Tegenstrijdige releaseclaims",
    "encode_advice": "AV1-proefencodes interpreteren",
}

ACTIONS: tuple[str, ...] = (
    "inspect_mapping", "retry_later", "adjust_filter", "request_search", "compare_trials",
    "manual_review",
)
ACTION_LABELS_NL: dict[str, str] = {
    "inspect_mapping": "Mapping/metadata opnieuw ophalen en tonen",
    "retry_later": "Later opnieuw controleren",
    "adjust_filter": "Filter aanpassen (alleen handmatig via instellingen)",
    "request_search": "Voorzichtige, rate-limited zoekpoging",
    "compare_trials": "Extra proefencodes vergelijken met baseline",
    "manual_review": "Handmatige beoordeling",
}

# Acties die MediaGuard via een geregistreerde callback kán uitvoeren. Alle vier zijn
# niet-destructief en omkeerbaar/idempotent. adjust_filter en manual_review voert
# MediaGuard nooit zelf uit: goedkeuren betekent daar alleen 'genoteerd'.
EXECUTABLE_ACTIONS: tuple[str, ...] = (
    "inspect_mapping", "retry_later", "request_search", "compare_trials",
)
# Wat bounded_auto maximaal mag (subset die de gebruiker verder kan beperken).
AUTO_ELIGIBLE_ACTIONS: tuple[str, ...] = EXECUTABLE_ACTIONS
DEFAULT_AUTO_ACTIONS: tuple[str, ...] = ("inspect_mapping", "retry_later", "request_search")

# Toegestane acties per taaktype.
TASK_ACTIONS: dict[str, tuple[str, ...]] = {
    "anime_identity": ("inspect_mapping", "request_search", "retry_later", "manual_review"),
    "missing_diagnosis": ("request_search", "retry_later", "adjust_filter", "inspect_mapping",
                          "manual_review"),
    "release_explanation": ("inspect_mapping", "adjust_filter", "retry_later", "manual_review"),
    "encode_advice": ("compare_trials", "retry_later", "manual_review"),
}

# Expliciete needs_advice-reasoncodes die een deterministische classifier moet afgeven
# voordat AI überhaupt wordt overwogen (S2: alleen bij expliciete trigger).
TRIGGER_REASON_CODES: dict[str, tuple[str, ...]] = {
    "anime_identity": ("CONFLICTING_EPISODE_MAP", "ABSOLUTE_NUMBERING_AMBIGUOUS",
                       "ALIAS_CONFLICT", "SPECIAL_OVA_AMBIGUOUS", "MULTI_EPISODE_AMBIGUOUS"),
    "missing_diagnosis": ("PERSISTENT_MISSING_UNEXPLAINED", "MULTIPLE_ROOT_CAUSES",
                          "CANDIDATES_REJECTED_MIXED"),
    "release_explanation": ("CONFLICTING_RELEASE_CLAIMS", "LANGUAGE_METADATA_CONFLICT",
                            "UNUSUAL_SPECIAL"),
    "encode_advice": ("ENCODE_TRIALS_INCONCLUSIVE",),
}

# Reasoncodes die het model mag noemen (triggers + deterministische D2-categorieën).
DIAGNOSTIC_REASON_CODES: tuple[str, ...] = (
    "NOT_MONITORED", "NOT_AIRED_OR_BAD_DATE", "NO_RELEASE_FOUND", "INDEXER_CATEGORY_WRONG",
    "INDEXER_UNAVAILABLE", "INDEXER_RATE_LIMITED", "REJECTED_QUALITY", "REJECTED_LANGUAGE",
    "REJECTED_CUSTOM_FORMAT", "REJECTED_SIZE", "REJECTED_SOURCE", "DOWNLOAD_CLIENT_ISSUE",
    "QUEUE_STALLED", "IMPORT_REJECTED", "PATH_MAPPING_ISSUE", "ANIME_NUMBERING_MISMATCH",
    "SCENE_MAPPING_MISSING", "DVD_ORDER_MISMATCH", "FILE_NOT_LINKED", "ROOT_FOLDER_MISSING",
    "INTENTIONAL_EXCEPTION", "INSUFFICIENT_EVIDENCE", "MISSING_HASH_EVIDENCE",
    "RELEASE_GROUP_CLAIM_UNVERIFIED", "ENCODE_QUALITY_BELOW_TARGET",
    "ENCODE_SIZE_NOT_BENEFICIAL",
)
REASON_CODES: tuple[str, ...] = tuple(sorted(
    {c for codes in TRIGGER_REASON_CODES.values() for c in codes} | set(DIAGNOSTIC_REASON_CODES)
))

UNCERTAINTY: tuple[str, ...] = ("high", "medium", "low")

PROMPT_VERSION = "mg-ai-prompt-v1"
CONSENT_VERSION = "mg-ai-consent-v1"
CONSENT_TEXT_NL = (
    "Ik geef toestemming dat MediaGuard voor ambiguë gevallen compacte, geminimaliseerde "
    "metadata (titels, episode-mappings, reasoncodes, releasenamen, niet-gevoelige "
    "logregels en lokale evidence-ID's) naar de gekozen betaalde AI-API stuurt. Er worden "
    "nooit videobestanden, frames, audio, API-sleutels, absolute paden of "
    "Sonarr/Radarr-configuratie verstuurd. Ik kan deze toestemming altijd intrekken."
)

MAX_SUMMARY_CHARS = 800
MAX_REASON_CODES = 8
MAX_EVIDENCE_IDS = 20

# Verboden actienamen: documentatie + test dat register_action ze weigert.
FORBIDDEN_ACTION_EXAMPLES: tuple[str, ...] = (
    "delete_file", "replace_file", "set_identity_verdict", "change_required_language",
    "change_hdr_policy", "dv_activate", "snapshot_delete", "cutover", "quarantine_purge",
    "activate_encode_profile", "change_fork_settings",
)
