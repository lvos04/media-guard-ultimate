"""Optionele echte API-smoketest (kost een fractie van een cent).

Draait alleen als ANTHROPIC_API_KEY en/of OPENAI_API_KEY in de omgeving staan; anders
overgeslagen. OpenAI-model via OPENAI_TEST_MODEL (standaard gpt-5-mini) met prijs via
OPENAI_TEST_PRICE_IN/OUT ($/1M tokens).
"""

from __future__ import annotations

import os

import pytest

from mediaguard.ai import service


@pytest.mark.skipif(not os.environ.get("ANTHROPIC_API_KEY"), reason="geen ANTHROPIC_API_KEY")
async def test_real_anthropic_structured_answer(ctx):  # type: ignore[no-untyped-def]
    ctx.secrets.set("ai:anthropic:apikey", os.environ["ANTHROPIC_API_KEY"], "test")
    service.record_consent(ctx, "anthropic", True, "user:test")
    result = await service.test_provider(ctx, "anthropic", "claude-haiku-5-5", "user:test")
    assert result["ok"], result


@pytest.mark.skipif(not os.environ.get("OPENAI_API_KEY"), reason="geen OPENAI_API_KEY")
async def test_real_openai_structured_answer(ctx):  # type: ignore[no-untyped-def]
    model = os.environ.get("OPENAI_TEST_MODEL", "gpt-5-mini")
    table = dict(ctx.config.value("ai.price_table"))
    table[f"openai:{model}"] = {
        "input_per_mtok": float(os.environ.get("OPENAI_TEST_PRICE_IN", "1.0")),
        "output_per_mtok": float(os.environ.get("OPENAI_TEST_PRICE_OUT", "5.0")),
        "currency": "USD"}
    ctx.config.set("global", "", "ai.price_table", table, "test")
    ctx.secrets.set("ai:openai:apikey", os.environ["OPENAI_API_KEY"], "test")
    service.record_consent(ctx, "openai", True, "user:test")
    result = await service.test_provider(ctx, "openai", model, "user:test")
    assert result["ok"], result
