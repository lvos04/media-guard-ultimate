"""Hulpfuncties voor de AI-tests (geen echte API-calls; HTTP via respx-mocks)."""

from __future__ import annotations

import json
from typing import Any

import httpx

from mediaguard.ai import service
from mediaguard.ai.constants import TASK_ACTIONS

ANTHROPIC_URL = "https://api.anthropic.com/v1/messages"
OPENAI_URL = "https://api.openai.com/v1/chat/completions"
FAKE_KEY = "sk-ant-test-abcdefghijklmnopqrstuvwxyz0123"

DEFAULT_ACTION = {
    "anime_identity": "inspect_mapping",
    "missing_diagnosis": "request_search",
    "release_explanation": "inspect_mapping",
    "encode_advice": "compare_trials",
}
DEFAULT_CODES = {
    "anime_identity": ["CONFLICTING_EPISODE_MAP"],
    "missing_diagnosis": ["NO_RELEASE_FOUND"],
    "release_explanation": ["CONFLICTING_RELEASE_CLAIMS"],
    "encode_advice": ["ENCODE_TRIALS_INCONCLUSIVE"],
}


def schema_of(body: dict[str, Any]) -> dict[str, Any]:
    if "output_config" in body:
        return dict(body["output_config"]["format"]["schema"])
    return dict(body["response_format"]["json_schema"]["schema"])


def advice_for(body: dict[str, Any], **override: Any) -> dict[str, Any]:
    props = schema_of(body)["properties"]
    case_id = props["case_id"]["enum"][0]
    task_type = props["task_type"]["enum"][0]
    ev = props["evidence_ids"]["items"].get("enum", [])
    advice = {
        "case_id": case_id,
        "task_type": task_type,
        "suggested_action": DEFAULT_ACTION[task_type]
        if DEFAULT_ACTION[task_type] in TASK_ACTIONS[task_type] else "manual_review",
        "reason_codes": DEFAULT_CODES[task_type],
        "evidence_ids": ev[:2],
        "reasoning_summary": "Bewijs ev-1 en ev-2 spreken elkaar tegen; mapping controleren.",
        "uncertainty": "medium",
        "requires_manual_review": False,
    }
    advice.update(override)
    return advice


def anthropic_response(payload: Any, *, input_tokens: int = 1000, output_tokens: int = 200,
                       stop_reason: str = "end_turn",
                       content: list[dict[str, Any]] | None = None) -> httpx.Response:
    text = payload if isinstance(payload, str) else json.dumps(payload)
    return httpx.Response(200, json={
        "id": "msg_test", "type": "message", "role": "assistant", "model": "claude-haiku-5-5",
        "content": content if content is not None else [{"type": "text", "text": text}],
        "stop_reason": stop_reason,
        "usage": {"input_tokens": input_tokens, "output_tokens": output_tokens},
    })


def anthropic_handler(**override: Any):  # type: ignore[no-untyped-def]
    def handler(request: httpx.Request) -> httpx.Response:
        body = json.loads(request.content)
        return anthropic_response(advice_for(body, **override))
    return handler


def configure(ctx: Any, mode: str = "advisory", *, provider: str = "anthropic",
              model: str = "claude-haiku-5-5", tested: bool = True) -> None:
    """Zet AI direct in de DB aan (zonder HTTP), alsof de UI-flow is doorlopen."""
    ctx.services["ai_retry_delay"] = 0.0
    ctx.secrets.set(f"ai:{provider}:apikey", FAKE_KEY, "test")
    service.record_consent(ctx, provider, True, "user:test")
    ctx.config.set("global", "", "ai.provider", provider, "test")
    ctx.config.set("global", "", f"ai.{provider}.model", model, "test")
    if provider == "openai":
        table = dict(ctx.config.value("ai.price_table"))
        table[f"openai:{model}"] = {"input_per_mtok": 0.5, "output_per_mtok": 1.5,
                                    "currency": "USD"}
        ctx.config.set("global", "", "ai.price_table", table, "test")
    if tested:
        service._upsert_provider_config(ctx, provider, last_test_ok=1, last_test_model=model,
                                        last_test_at="2026-11-01T00:00:00+00:00")
    ctx.config.set("global", "", "ai.task_types",
                   ["anime_identity", "missing_diagnosis", "release_explanation",
                    "encode_advice"], "test")
    ctx.config.set("global", "", "ai.mode", mode, "test")


def anime_case() -> dict[str, Any]:
    return {
        "series": "Voorbeeld Anime",
        "file_path": "/mnt/tank/media/anime/Voorbeeld Anime/Season 02/"
                     "[SubsPlease] Voorbeeld - 13.mkv",
        "release_name": "[SubsPlease] Voorbeeld - 13 (1080p)",
        "candidates": {"scene": "S02E01", "tvdb": "S01E13", "absolute": 13},
    }
