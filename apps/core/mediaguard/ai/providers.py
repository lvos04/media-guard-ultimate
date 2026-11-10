"""Provideronafhankelijke adapters (DEEL S2) — rechtstreeks via httpx, zonder SDK.

- ``AnthropicClaudeAdapter``: Messages API (``POST /v1/messages``) met gestructureerde
  uitvoer via ``output_config.format`` (JSON-schema). Geen tools: het model kan geen
  toolcalls doen. Modellen via ``GET /v1/models``.
- ``OpenAIAdapter``: Chat Completions met ``response_format: json_schema`` (strict).
  Modellen via ``GET /v1/models``.

Adapters gooien uitsluitend ``ProviderError``; de service vangt die altijd af.
"""

from __future__ import annotations

import json
import time
from dataclasses import dataclass
from typing import Any

import httpx

from mediaguard.secrets_store import redact

ANTHROPIC_VERSION = "2023-06-01"


class ProviderError(Exception):
    """kind: timeout | network | rate_limited | server | auth | bad_request | refusal |
    truncated | invalid_response. ``billed_possible``: kan de provider al kosten gerekend
    hebben (dan wordt de reservering conservatief als kosten geboekt)."""

    def __init__(self, kind: str, message: str, *, retryable: bool = False,
                 billed_possible: bool = False, input_tokens: int | None = None,
                 output_tokens: int | None = None) -> None:
        super().__init__(redact(message)[:500])
        self.kind = kind
        self.retryable = retryable
        self.billed_possible = billed_possible
        self.input_tokens = input_tokens
        self.output_tokens = output_tokens


@dataclass
class ProviderResponse:
    data: Any
    input_tokens: int
    output_tokens: int
    model: str
    latency_ms: int
    raw_text: str


@dataclass
class PreparedRequest:
    url: str
    headers_public: dict[str, str]
    body: dict[str, Any]


class ProviderAdapter:
    name = "base"

    def __init__(self, api_key: str | None, base_url: str, *, effort: str = "low") -> None:
        self.api_key = api_key or ""
        self.base_url = base_url.rstrip("/")
        self.effort = effort

    # -- te implementeren -------------------------------------------------------------
    def prepare(self, *, model: str, system: str, user: str, schema: dict[str, Any],
                max_output_tokens: int) -> PreparedRequest:
        raise NotImplementedError

    def _auth_headers(self) -> dict[str, str]:
        raise NotImplementedError

    def _parse(self, payload: dict[str, Any]) -> tuple[str, int, int, str]:
        raise NotImplementedError

    # -- gedeeld ----------------------------------------------------------------------
    async def complete(self, *, model: str, system: str, user: str, schema: dict[str, Any],
                       max_output_tokens: int, timeout: float) -> ProviderResponse:
        prepared = self.prepare(model=model, system=system, user=user, schema=schema,
                                max_output_tokens=max_output_tokens)
        headers = {**prepared.headers_public, **self._auth_headers()}
        started = time.monotonic()
        try:
            async with httpx.AsyncClient(timeout=timeout) as client:
                resp = await client.post(prepared.url, headers=headers, json=prepared.body)
        except httpx.TimeoutException as exc:
            raise ProviderError("timeout", f"time-out na {timeout}s", retryable=True,
                                billed_possible=True) from exc
        except httpx.HTTPError as exc:
            raise ProviderError("network", f"netwerkfout: {type(exc).__name__}",
                                retryable=True) from exc
        latency = int((time.monotonic() - started) * 1000)
        _raise_for_status(resp)
        try:
            payload = resp.json()
        except ValueError as exc:
            raise ProviderError("invalid_response", "antwoord is geen JSON",
                                billed_possible=True) from exc
        if not isinstance(payload, dict):
            raise ProviderError("invalid_response", "onverwacht antwoordformaat",
                                billed_possible=True)
        text, in_tok, out_tok, used_model = self._parse(payload)
        try:
            from mediaguard.ai.schema import parse_json_text

            data = parse_json_text(text)
        except (ValueError, json.JSONDecodeError) as exc:
            raise ProviderError("invalid_response", "modeluitvoer is geen geldig JSON",
                                input_tokens=in_tok, output_tokens=out_tok) from exc
        return ProviderResponse(data=data, input_tokens=in_tok, output_tokens=out_tok,
                                model=used_model or model, latency_ms=latency, raw_text=text)

    async def list_models(self, timeout: float = 15.0) -> list[dict[str, Any]]:
        headers = self._auth_headers() | self._version_headers()
        models: list[dict[str, Any]] = []
        params: dict[str, Any] = {"limit": 100} if self.name == "anthropic" else {}
        try:
            async with httpx.AsyncClient(timeout=timeout) as client:
                for _ in range(10):  # paginering begrensd
                    resp = await client.get(f"{self.base_url}/v1/models", headers=headers,
                                            params=params)
                    _raise_for_status(resp)
                    payload = resp.json()
                    for item in payload.get("data", []) or []:
                        if isinstance(item, dict) and isinstance(item.get("id"), str):
                            models.append({
                                "id": item["id"],
                                "display_name": item.get("display_name") or item["id"],
                                "created": item.get("created_at") or item.get("created"),
                            })
                    if self.name == "anthropic" and payload.get("has_more") and \
                            payload.get("last_id"):
                        params = {"limit": 100, "after_id": payload["last_id"]}
                        continue
                    break
        except httpx.TimeoutException as exc:
            raise ProviderError("timeout", "time-out bij ophalen modellen") from exc
        except httpx.HTTPError as exc:
            raise ProviderError("network", f"netwerkfout: {type(exc).__name__}") from exc
        except ValueError as exc:
            raise ProviderError("invalid_response", "modellijst is geen JSON") from exc
        return models

    def _version_headers(self) -> dict[str, str]:
        return {}


def _raise_for_status(resp: httpx.Response) -> None:
    code = resp.status_code
    if code < 400:
        return
    try:
        detail = json.dumps(resp.json())[:300]
    except ValueError:
        detail = resp.text[:300]
    if code in (401, 403):
        raise ProviderError("auth", f"API-sleutel geweigerd ({code})")
    if code == 429:
        raise ProviderError("rate_limited", f"rate limit/quota (429): {detail}", retryable=True)
    if code in (408, 409) or code >= 500:
        raise ProviderError("server", f"providerfout ({code}): {detail}", retryable=True)
    raise ProviderError("bad_request", f"verzoek geweigerd ({code}): {detail}")


class AnthropicClaudeAdapter(ProviderAdapter):
    name = "anthropic"

    def _auth_headers(self) -> dict[str, str]:
        return {"x-api-key": self.api_key}

    def _version_headers(self) -> dict[str, str]:
        return {"anthropic-version": ANTHROPIC_VERSION}

    def prepare(self, *, model: str, system: str, user: str, schema: dict[str, Any],
                max_output_tokens: int) -> PreparedRequest:
        body: dict[str, Any] = {
            "model": model,
            "max_tokens": max_output_tokens,
            "system": system,
            "messages": [{"role": "user", "content": user}],
            "output_config": {
                "effort": self.effort,
                "format": {"type": "json_schema", "schema": schema},
            },
        }
        return PreparedRequest(
            url=f"{self.base_url}/v1/messages",
            headers_public={"content-type": "application/json",
                            "anthropic-version": ANTHROPIC_VERSION,
                            "x-api-key": "***"},
            body=body,
        )

    def _parse(self, payload: dict[str, Any]) -> tuple[str, int, int, str]:
        usage = payload.get("usage") or {}
        in_tok = int(usage.get("input_tokens") or 0) + \
            int(usage.get("cache_creation_input_tokens") or 0) + \
            int(usage.get("cache_read_input_tokens") or 0)
        out_tok = int(usage.get("output_tokens") or 0)
        stop = payload.get("stop_reason")
        if stop == "refusal":
            raise ProviderError("refusal", "model weigerde het verzoek",
                                input_tokens=in_tok, output_tokens=out_tok)
        if stop == "max_tokens":
            raise ProviderError("truncated", "antwoord afgekapt op max_tokens",
                                input_tokens=in_tok, output_tokens=out_tok)
        texts: list[str] = []
        for block in payload.get("content") or []:
            if not isinstance(block, dict):
                continue
            btype = block.get("type")
            if btype == "text":
                texts.append(str(block.get("text", "")))
            elif btype in ("thinking", "redacted_thinking"):
                continue
            else:
                # tool_use of onbekende blokken: nooit uitvoeren, antwoord afwijzen
                raise ProviderError("invalid_response", f"onverwacht inhoudsblok: {btype}",
                                    input_tokens=in_tok, output_tokens=out_tok)
        if not texts:
            raise ProviderError("invalid_response", "geen tekstantwoord",
                                input_tokens=in_tok, output_tokens=out_tok)
        return "".join(texts), in_tok, out_tok, str(payload.get("model") or "")


class OpenAIAdapter(ProviderAdapter):
    name = "openai"

    def _auth_headers(self) -> dict[str, str]:
        return {"Authorization": f"Bearer {self.api_key}"}

    def prepare(self, *, model: str, system: str, user: str, schema: dict[str, Any],
                max_output_tokens: int) -> PreparedRequest:
        body: dict[str, Any] = {
            "model": model,
            "max_completion_tokens": max_output_tokens,
            "messages": [
                {"role": "system", "content": system},
                {"role": "user", "content": user},
            ],
            "response_format": {
                "type": "json_schema",
                "json_schema": {"name": "mediaguard_advice", "strict": True, "schema": schema},
            },
        }
        return PreparedRequest(
            url=f"{self.base_url}/v1/chat/completions",
            headers_public={"content-type": "application/json",
                            "Authorization": "Bearer ***"},
            body=body,
        )

    def _parse(self, payload: dict[str, Any]) -> tuple[str, int, int, str]:
        usage = payload.get("usage") or {}
        in_tok = int(usage.get("prompt_tokens") or usage.get("input_tokens") or 0)
        out_tok = int(usage.get("completion_tokens") or usage.get("output_tokens") or 0)
        choices = payload.get("choices") or []
        if not choices or not isinstance(choices[0], dict):
            raise ProviderError("invalid_response", "geen choices in antwoord",
                                input_tokens=in_tok, output_tokens=out_tok)
        choice = choices[0]
        message = choice.get("message") or {}
        if message.get("tool_calls") or message.get("function_call"):
            raise ProviderError("invalid_response", "onverwachte toolcall in antwoord",
                                input_tokens=in_tok, output_tokens=out_tok)
        if message.get("refusal"):
            raise ProviderError("refusal", "model weigerde het verzoek",
                                input_tokens=in_tok, output_tokens=out_tok)
        if choice.get("finish_reason") == "length":
            raise ProviderError("truncated", "antwoord afgekapt op max tokens",
                                input_tokens=in_tok, output_tokens=out_tok)
        content = message.get("content")
        if not isinstance(content, str) or not content.strip():
            raise ProviderError("invalid_response", "geen tekstantwoord",
                                input_tokens=in_tok, output_tokens=out_tok)
        return content, in_tok, out_tok, str(payload.get("model") or "")


ADAPTERS: dict[str, type[ProviderAdapter]] = {
    "anthropic": AnthropicClaudeAdapter,
    "openai": OpenAIAdapter,
}
