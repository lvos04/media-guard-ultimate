"""Minimale async client voor de Seerr-API (Overseerr/Jellyseerr-compatibel, ``/api/v1``).
Authenticatie via header ``X-Api-Key`` (Seerr → Instellingen → Algemeen → API-sleutel)."""

from __future__ import annotations

import asyncio
from typing import TYPE_CHECKING, Any

import httpx

from mediaguard.secrets_store import redact

if TYPE_CHECKING:
    from mediaguard.context import AppContext

SECRET_NAME = "seerr:apikey"


class SeerrError(Exception):
    def __init__(self, message: str, status: int | None = None, code: str = "SEERR_ERROR",
                 details: Any = None) -> None:
        self.message = redact(message)
        self.status = status
        self.code = code
        self.details = details
        super().__init__(self.message)

    @property
    def http_status(self) -> int:
        if self.code == "SEERR_NOT_CONFIGURED":
            return 409
        if self.status == 404:
            return 404
        return 502

    def to_dict(self) -> dict[str, Any]:
        return {"code": self.code, "message": self.message, "upstream_status": self.status,
                "details": self.details}


class SeerrClient:
    def __init__(self, base_url: str, api_key: str, *, timeout: float = 15.0,
                 retries: int = 2, transport: httpx.AsyncBaseTransport | None = None) -> None:
        self.base_url = base_url.rstrip("/")
        self.api_key = api_key
        self.timeout = timeout
        self.retries = retries
        self.transport = transport
        # True als de laatste ``requests()`` aantoonbaar álle aanvragen ophaalde
        self.requests_complete = False

    async def request(self, method: str, path: str, *, params: dict[str, Any] | None = None,
                      json: Any = None) -> Any:
        url = f"/api/v1/{path.lstrip('/')}"
        attempt = 0
        while True:
            attempt += 1
            try:
                async with httpx.AsyncClient(
                        base_url=self.base_url, timeout=self.timeout, transport=self.transport,
                        headers={"X-Api-Key": self.api_key, "Accept": "application/json"}) as c:
                    resp = await c.request(method, url, params=params, json=json)
            except httpx.TransportError as exc:
                if method == "GET" and attempt <= self.retries:
                    await asyncio.sleep(0.5 * attempt)
                    continue
                raise SeerrError(f"Seerr niet bereikbaar: {type(exc).__name__}: {exc}",
                                 code="SEERR_UNAVAILABLE") from exc
            if resp.status_code >= 500 and method == "GET" and attempt <= self.retries:
                await asyncio.sleep(0.5 * attempt)
                continue
            body: Any = None
            if resp.content:
                try:
                    body = resp.json()
                except ValueError:
                    body = {"message": resp.text[:300]}
            if resp.status_code in (401, 403):
                raise SeerrError("Seerr weigert de API-sleutel", resp.status_code, "SEERR_AUTH")
            if resp.status_code >= 400:
                msg = body.get("message") if isinstance(body, dict) else str(body)
                raise SeerrError(f"Seerr {method} {url}: HTTP {resp.status_code} {msg or ''}",
                                 resp.status_code, "SEERR_HTTP", details=body)
            return body

    async def status(self) -> dict[str, Any]:
        return await self.request("GET", "status") or {}

    async def servers(self, kind: str) -> list[dict[str, Any]]:
        return await self.request("GET", f"settings/{kind}") or []

    @staticmethod
    def _writable(body: dict[str, Any]) -> dict[str, Any]:
        """Seerr valideert tegen zijn OpenAPI-spec: 'id' is read-only en null-waarden
        worden geweigerd (HTTP 400). Getest tegen Seerr 3.5.1."""
        return {k: v for k, v in body.items() if k != "id" and v is not None}

    async def server_update(self, kind: str, server_id: int, body: dict[str, Any]) -> dict:
        return await self.request("PUT", f"settings/{kind}/{int(server_id)}",
                                  json=self._writable(body)) or {}

    async def server_create(self, kind: str, body: dict[str, Any]) -> dict[str, Any]:
        return await self.request("POST", f"settings/{kind}", json=self._writable(body)) or {}

    async def server_delete(self, kind: str, server_id: int) -> Any:
        return await self.request("DELETE", f"settings/{kind}/{int(server_id)}")

    async def server_test(self, kind: str, server: dict[str, Any]) -> dict[str, Any]:
        body = {k: server.get(k) for k in ("hostname", "port", "apiKey", "useSsl", "baseUrl")}
        return await self.request("POST", f"settings/{kind}/test", json=body) or {}

    async def requests(self, *, take: int = 100, max_pages: int = 100) -> list[dict[str, Any]]:
        out: list[dict[str, Any]] = []
        ids: set[Any] = set()
        skip = 0
        self.requests_complete = False
        for _ in range(max_pages):
            page = await self.request("GET", "request", params={
                "take": take, "skip": skip, "filter": "all", "sort": "added"}) or {}
            results = page.get("results") or []
            for r in results:
                # schuift de lijst tussen twee pagina's op, dan komt een aanvraag dubbel mee
                if r.get("id") in ids:
                    continue
                ids.add(r.get("id"))
                out.append(r)
            info = page.get("pageInfo") or {}
            total = int(info.get("results") or 0)
            skip += take
            if not results or skip >= total:
                # alles binnen (elke aanvraag één keer). Ook dan kan een verwijdering tussen
                # twee pagina's een levende aanvraag overslaan: wat ontbreekt, controleert
                # sync_requests daarom nog per aanvraag (request_gone)
                self.requests_complete = "results" in info and len(ids) == total
                break
        return out

    async def request_gone(self, request_id: int) -> bool | None:
        """True als Seerr zegt dat de aanvraag niet (meer) bestaat (404), False als hij er
        nog is, None bij een storing (dan niets beslissen)."""
        try:
            await self.request("GET", f"request/{int(request_id)}")
        except SeerrError as exc:
            return True if exc.status == 404 else None
        return False


def seerr_client(ctx: AppContext, *, url: str | None = None,
                 api_key: str | None = None) -> SeerrClient:
    url = url if url is not None else str(ctx.config.value("seerr.url") or "")
    key = api_key if api_key is not None else ctx.secrets.get(SECRET_NAME)
    if not url or not key:
        raise SeerrError("Seerr is nog niet ingesteld (URL en API-sleutel)",
                         code="SEERR_NOT_CONFIGURED")
    return SeerrClient(url, key, transport=ctx.services.get("seerr_transport"))
