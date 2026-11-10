"""Interne authenticatie voor de MediaGuard-brug.

Alle ``/api/mediaguard/v1/*``-routes vereisen ``X-MediaGuard-Token`` gelijk aan
``MG_INTERNAL_TOKEN``. Zonder geconfigureerd token weigert de brug alles (503):
fail-closed, nooit open.

In containermodus bindt uvicorn op 0.0.0.0 binnen het private compose-netwerk.
De oude ``/api``- en ``/ws``-routes vertrouwden op ``127.0.0.1``-binding plus de
Next.js-gateway; daarom vereist de middleware hieronder in containermodus
hetzelfde token ook voor die routes (``MG_PROTECT_LEGACY_API=0`` schakelt dat
bewust uit, bijvoorbeeld voor een lokale ontwikkelopstelling).
"""

from __future__ import annotations

import hmac
import json
import os
from typing import Any

from fastapi import Header, HTTPException
from starlette.types import ASGIApp, Receive, Scope, Send

TOKEN_HEADER = "X-MediaGuard-Token"
# core's browser-gateway (/api/v1/encoder/*) zet deze header; zie require_token
GATEWAY_HEADER = "X-MediaGuard-Gateway"
MIN_TOKEN_LENGTH = 24


def configured_token() -> str | None:
    token = os.environ.get("MG_INTERNAL_TOKEN", "")
    if not token and os.environ.get("MG_INTERNAL_TOKEN_FILE"):
        # door mediaguard-init gegenereerd; de gebruiker hoeft niets in te vullen
        try:
            with open(os.environ["MG_INTERNAL_TOKEN_FILE"], encoding="utf-8") as handle:
                token = handle.read().strip()
        except OSError:
            token = ""
    return token if len(token) >= MIN_TOKEN_LENGTH else None


def token_matches(supplied: str | None) -> bool:
    expected = configured_token()
    if expected is None or supplied is None:
        return False
    return hmac.compare_digest(supplied.encode("utf-8"), expected.encode("utf-8"))


async def require_token(
    x_mediaguard_token: str | None = Header(default=None, alias=TOKEN_HEADER),
    x_mediaguard_gateway: str | None = Header(default=None, alias=GATEWAY_HEADER),
) -> None:
    if x_mediaguard_gateway is not None:
        # via de browser-gateway van core: de brug is alleen voor core zelf (commit/reject
        # lopen via core's statusmachine), ook als een pad toch langs de gateway glipt
        raise HTTPException(403, "de MediaGuard-brug is niet bereikbaar via de gateway")
    if configured_token() is None:
        raise HTTPException(
            503, f"MG_INTERNAL_TOKEN ontbreekt of is korter dan {MIN_TOKEN_LENGTH} tekens"
        )
    if not token_matches(x_mediaguard_token):
        raise HTTPException(401, "ongeldig of ontbrekend X-MediaGuard-Token")


class LegacyApiTokenMiddleware:
    """Pure ASGI-middleware (ook voor WebSockets) voor /api en /ws in containermodus."""

    def __init__(self, app: ASGIApp) -> None:
        self.app = app

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] not in {"http", "websocket"}:
            await self.app(scope, receive, send)
            return
        path = str(scope.get("path") or "")
        protected = path == "/ws" or path.startswith("/api/")
        if not protected:
            await self.app(scope, receive, send)
            return
        headers: dict[str, str] = {
            key.decode("latin-1").lower(): value.decode("latin-1")
            for key, value in scope.get("headers") or []
        }
        if token_matches(headers.get(TOKEN_HEADER.lower())):
            await self.app(scope, receive, send)
            return
        if scope["type"] == "websocket":
            await send({"type": "websocket.close", "code": 4401})
            return
        status = 503 if configured_token() is None else 401
        body: dict[str, Any] = {
            "detail": "X-MediaGuard-Token vereist"
            if status == 401
            else "MG_INTERNAL_TOKEN ontbreekt; API blijft dicht"
        }
        payload = json.dumps(body).encode("utf-8")
        await send(
            {
                "type": "http.response.start",
                "status": status,
                "headers": [
                    (b"content-type", b"application/json"),
                    (b"content-length", str(len(payload)).encode()),
                ],
            }
        )
        await send({"type": "http.response.body", "body": payload})
