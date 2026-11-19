"""Verzoekcorrelatie voor HTTP-antwoorden en mutatie-audit."""

from __future__ import annotations

import re
from uuid import uuid4

from starlette.datastructures import Headers, MutableHeaders
from starlette.types import ASGIApp, Message, Receive, Scope, Send

HEADER = "X-Correlation-Id"
UUID_TEXT = re.compile(r"[0-9a-fA-F]{8}(?:-[0-9a-fA-F]{4}){3}-[0-9a-fA-F]{12}")


class CorrelationId:
    def __init__(self, app: ASGIApp) -> None:
        self.app = app

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return

        values = Headers(scope=scope).getlist(HEADER)
        if len(values) == 1 and UUID_TEXT.fullmatch(values[0]):
            correlation_id = values[0]
        else:
            correlation_id = str(uuid4())
        scope.setdefault("state", {})["correlation_id"] = correlation_id

        async def send_with_correlation(message: Message) -> None:
            if message["type"] == "http.response.start":
                MutableHeaders(scope=message)[HEADER] = correlation_id
            await send(message)

        await self.app(scope, receive, send_with_correlation)
