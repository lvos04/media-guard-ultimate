"""Contractfouten en begrensde invoer, uitsluitend op de nieuwe routes."""

from __future__ import annotations

from typing import Any
from uuid import uuid4

from fastapi import FastAPI, Request
from fastapi.exception_handlers import http_exception_handler, request_validation_exception_handler
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from starlette.exceptions import HTTPException
from starlette.types import ASGIApp, Receive, Scope, Send

from mediaguard.auth import SCOPE_DENIED_DETAIL

MAX_BYTES = 1048576


def is_connector(path: str) -> bool:
    return path == "/connector/v1" or path.startswith("/connector/v1/")


def failure(
    status: int,
    code: str,
    message: str,
    details: dict[str, Any] | None = None,
    headers: dict[str, str] | None = None,
) -> JSONResponse:
    return JSONResponse(
        {"code": code, "message": message, "details": details or {}, "trace_id": str(uuid4())},
        status_code=status,
        headers={**(headers or {}), "Cache-Control": "no-store"},
    )


def install(app: FastAPI) -> None:
    @app.exception_handler(HTTPException)
    async def http_error(request: Request, exc: HTTPException):
        if not is_connector(request.url.path):
            return await http_exception_handler(request, exc)
        if exc.status_code == 403 and exc.detail == SCOPE_DENIED_DETAIL:
            return failure(403, "scope_ontbreekt", "Deze API-sleutel mist het vereiste recht",
                           {"reason": "scope_ontbreekt"}, exc.headers)
        code, message = {
            401: ("unauthorized", "Niet ingelogd of ongeldige aanmelding"),
            403: ("forbidden", "Onvoldoende rechten voor deze route"),
            404: ("not_found", "Route niet gevonden"),
            405: ("method_not_allowed", "Deze methode is niet toegestaan"),
            429: ("rate_limited", "Te veel verzoeken"),
        }.get(exc.status_code, ("dependency_unavailable", "Verzoek niet beschikbaar"))
        details = {"reason": code} if exc.status_code in (401, 403) else {}
        return failure(exc.status_code, code, message, details, exc.headers)

    @app.exception_handler(RequestValidationError)
    async def validation(request: Request, exc: RequestValidationError):
        if not is_connector(request.url.path):
            return await request_validation_exception_handler(request, exc)
        return failure(
            422,
            "validation_error",
            "Ongeldige invoer",
            {"fouten": [{"veld": list(e["loc"]), "type": e["type"]} for e in exc.errors()]},
        )


class InputLimit:
    """ASGI vóór routering en auth; begrenst ook chunked invoer zonder Content-Length."""

    def __init__(self, app: ASGIApp):
        self.app = app

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        path = scope.get("path", "")
        if scope["type"] != "http" or not (is_connector(path) or path in ("/healthz", "/readyz")):
            await self.app(scope, receive, send)
            return
        headers = dict(scope.get("headers", []))
        try:
            length = int(headers.get(b"content-length", b"0"))
        except ValueError:
            length = 0
        too_large = length > MAX_BYTES
        body = bytearray()
        while not too_large:
            message = await receive()
            if message["type"] == "http.disconnect":
                return
            chunk = message.get("body", b"")
            if len(body) + len(chunk) > MAX_BYTES:
                too_large = True
                break
            body.extend(chunk)
            if not message.get("more_body", False):
                break
        if too_large:
            await failure(
                413, "payload_too_large", "Verzoek is te groot", {"max_bytes": MAX_BYTES}
            )(scope, receive, send)
            return
        # Gebruik de contractpaden zonder eindslash. Een routerredirect kan het
        # interne kernadres van de webproxy in Location terugsturen.
        if is_connector(path) and path.endswith("/"):
            await failure(404, "not_found", "Route niet gevonden")(scope, receive, send)
            return
        consumed = False

        async def replay():
            nonlocal consumed
            if not consumed:
                consumed = True
                return {"type": "http.request", "body": bytes(body), "more_body": False}
            return await receive()

        started = False

        async def track(message):
            nonlocal started
            if message["type"] == "http.response.start":
                started = True
                # Versheid komt van observed_at, nooit van een proxycache.
                message["headers"] = [
                    (k, v) for k, v in message.get("headers", []) if k != b"cache-control"
                ] + [(b"cache-control", b"no-store")]
            await send(message)

        try:
            await self.app(scope, replay, track)
        except Exception:  # noqa: BLE001 - geen interne fouttekst naar de connector
            if started:
                raise
            await failure(500, "internal_error", "Interne fout bij het lezen van de toestand")(
                scope, receive, send
            )
