"""Minimale async client voor de Prowlarr-API (``/api/v1``), header ``X-Api-Key``.

MediaGuard registreert de vier instanties als *Applications* in Prowlarr; Prowlarr pusht
daarna zelf de indexers (met de juiste categorieën) naar elke instantie."""

from __future__ import annotations

import asyncio
from typing import TYPE_CHECKING, Any

import httpx

from mediaguard.secrets_store import redact

if TYPE_CHECKING:
    from mediaguard.context import AppContext


class ProwlarrError(Exception):
    def __init__(self, message: str, status: int | None = None,
                 code: str = "PROWLARR_ERROR", details: Any = None) -> None:
        self.message = redact(message)
        self.status = status
        self.code = code
        self.details = details
        super().__init__(self.message)

    def to_dict(self) -> dict[str, Any]:
        return {"code": self.code, "message": self.message, "upstream_status": self.status,
                "details": self.details}


class ProwlarrClient:
    def __init__(self, base_url: str, api_key: str, *, timeout: float = 15.0,
                 retries: int = 1, transport: httpx.AsyncBaseTransport | None = None) -> None:
        self.base_url = base_url.rstrip("/")
        self.api_key = api_key
        self.timeout = timeout
        self.retries = retries
        self.transport = transport

    async def request(self, method: str, path: str, *, params: dict[str, Any] | None = None,
                      json: Any = None) -> Any:
        url = f"/api/v1/{path.lstrip('/')}"
        attempt = 0
        while True:
            attempt += 1
            try:
                async with httpx.AsyncClient(
                        base_url=self.base_url, timeout=self.timeout, transport=self.transport,
                        headers={"X-Api-Key": self.api_key,
                                 "Accept": "application/json"}) as client:
                    resp = await client.request(method, url, params=params, json=json)
            except httpx.TransportError as exc:
                if method == "GET" and attempt <= self.retries:
                    await asyncio.sleep(0.3 * attempt)
                    continue
                raise ProwlarrError(f"Prowlarr niet bereikbaar: {type(exc).__name__}: {exc}",
                                    code="PROWLARR_UNAVAILABLE") from exc
            body: Any = None
            if resp.content:
                try:
                    body = resp.json()
                except ValueError:
                    body = {"message": resp.text[:300]}
            if resp.status_code in (401, 403):
                raise ProwlarrError("Prowlarr weigert de API-sleutel", resp.status_code,
                                    "PROWLARR_AUTH")
            if resp.status_code >= 400:
                if isinstance(body, list):
                    msg = "; ".join(str(e.get("errorMessage", "")) for e in body[:5]
                                    if isinstance(e, dict))
                else:
                    msg = (body or {}).get("message", "") if isinstance(body, dict) else ""
                raise ProwlarrError(f"Prowlarr {method} {url}: HTTP {resp.status_code} {msg}",
                                    resp.status_code, "PROWLARR_HTTP", details=body)
            return body

    async def status(self) -> dict[str, Any]:
        return await self.request("GET", "system/status") or {}

    async def applications(self) -> list[dict[str, Any]]:
        return await self.request("GET", "applications") or []

    async def application_schema(self) -> list[dict[str, Any]]:
        return await self.request("GET", "applications/schema") or []

    async def application_create(self, body: dict[str, Any]) -> dict[str, Any]:
        return await self.request("POST", "applications", params={"forceSave": "true"},
                                  json=body) or {}

    async def application_update(self, body: dict[str, Any]) -> dict[str, Any]:
        return await self.request("PUT", f"applications/{int(body['id'])}",
                                  params={"forceSave": "true"}, json=body) or {}

    async def tags(self) -> list[dict[str, Any]]:
        return await self.request("GET", "tag") or []

    async def tag_create(self, label: str) -> dict[str, Any]:
        return await self.request("POST", "tag", json={"label": label}) or {}

    async def indexers(self) -> list[dict[str, Any]]:
        return await self.request("GET", "indexer") or []

    async def indexer_update(self, body: dict[str, Any]) -> dict[str, Any]:
        return await self.request("PUT", f"indexer/{int(body['id'])}",
                                  params={"forceSave": "true"}, json=body) or {}

    async def sync(self) -> dict[str, Any]:
        return await self.request("POST", "command",
                                  json={"name": "ApplicationIndexerSync"}) or {}


def prowlarr_client(ctx: AppContext, *, url: str | None = None,
                    api_key: str | None = None) -> ProwlarrClient:
    from mediaguard.provisioning.store import PROWLARR_SECRET, load

    url = url if url is not None else load(ctx).prowlarr.url
    key = api_key if api_key is not None else ctx.secrets.get(PROWLARR_SECRET)
    if not url or not key:
        raise ProwlarrError("Prowlarr is nog niet ingesteld (URL en API-sleutel)",
                            code="PROWLARR_NOT_CONFIGURED")
    return ProwlarrClient(url, key, transport=ctx.services.get("prowlarr_transport"))
