"""Beveiligde doorgang van de web-UI naar de AV1-engine (AV1 Studio).

De engine (lvos04/av1) heeft zijn eigen REST-API (``/api/*``) en live-kanaal (``/ws``).
In containermodus accepteert hij die alleen met het interne token. De browser krijgt dat
token nooit: de UI praat met ``/api/v1/encoder/*`` op core, core controleert de
MediaGuard-login (lezen = viewer, wijzigen = operator + CSRF) en voegt het token toe.
"""

from __future__ import annotations

import asyncio
import contextlib
import logging
import time
from typing import Any

import httpx
from fastapi import APIRouter, Depends, HTTPException, Request, WebSocket
from fastapi.responses import Response

from mediaguard.auth import ROLE_RANK, Principal, current_principal, principal_from_request
from mediaguard.context import AppContext, get_app_context

LOGGER = logging.getLogger(__name__)
router = APIRouter(prefix="/api/v1/encoder", tags=["encoder-gateway"])

_HOP = {"connection", "keep-alive", "transfer-encoding", "te", "trailer", "upgrade",
        "proxy-authorization", "proxy-authenticate", "content-length", "content-encoding",
        "host", "cookie", "x-csrf-token", "x-mediaguard-token", "authorization",
        "x-mediaguard-gateway", "x-mediaguard-hook-key", "x-api-key"}
_READ_METHODS = {"GET", "HEAD", "OPTIONS"}


def _client(ctx: AppContext) -> httpx.AsyncClient:
    client = ctx.services.get("encoder_gateway_client")
    if client is None:
        client = httpx.AsyncClient(base_url=ctx.settings.encoder_url, timeout=httpx.Timeout(
            120, connect=5), transport=ctx.services.get("encoder_transport"))
        ctx.services["encoder_gateway_client"] = client
    return client  # type: ignore[no-any-return]


# Zet de gateway op elk doorgestuurd verzoek; de brug van de engine (/api/mediaguard/v1)
# weigert alles met deze header, ook als het pad toch ergens langs zou glippen.
GATEWAY_HEADER = "X-MediaGuard-Gateway"


def _safe_path(path: str) -> str:
    """Alleen gewone paden naar de engine-API: geen '.'/'..'-segmenten, lege segmenten,
    backslashes, %-codes of stuurtekens (rapport: met 'x/../mediaguard/…' bereikte je via
    httpx' padnormalisatie de interne brug). Nooit naar /api/mediaguard/…."""
    clean = path.strip("/")
    segments = clean.split("/")
    if (not clean or any(seg in ("", ".", "..") for seg in segments)
            or any(c in clean for c in "\\%?#") or any(ord(c) < 32 or ord(c) == 127
                                                       for c in clean)):
        raise HTTPException(status_code=400, detail="Ongeldig pad")
    final = httpx.URL(f"http://engine/api/{clean}").path.lower()
    if segments[0].lower() == "mediaguard" or final.startswith("/api/mediaguard") or \
            not final.startswith("/api/"):
        # de brug (/api/mediaguard/v1) is alleen voor core zelf: commit/reject/cancel lopen
        # via core's eigen statusmachine, nooit rechtstreeks vanuit de browser
        raise HTTPException(status_code=403, detail="Alleen via MediaGuard core")
    return clean


#: adres en sleutel van de encoder-koppelingen (Emby/Jellyfin, Radarr, Sonarr) wijzigen of testen
#: is beheer, net als POST /api/v1/mediaserver/config: alleen een beheerder (S-P2-600, R-VEILIG-1;
#: V-064). Lezen blijft voor iedereen met een login.
_ADMIN_WRITE = ("integraties",)


def _need(principal: Principal, method: str, path: str = "") -> None:
    from fastapi import HTTPException

    role = "viewer" if method in _READ_METHODS else "operator"
    if role == "operator" and path.strip("/").split("/", 1)[0].lower() in _ADMIN_WRITE:
        role = "admin"
    if ROLE_RANK.get(principal.role, 0) < ROLE_RANK[role]:
        raise HTTPException(status_code=403, detail=f"Rol '{role}' vereist")


@router.api_route("/{path:path}", methods=["GET", "POST", "PUT", "PATCH", "DELETE"],
                  include_in_schema=False)
async def forward(path: str, request: Request,
                  principal: Principal = Depends(current_principal)) -> Response:
    _need(principal, request.method, path)
    path = _safe_path(path)
    ctx = get_app_context(request)
    headers = {k: v for k, v in request.headers.items() if k.lower() not in _HOP}
    headers["X-MediaGuard-Token"] = ctx.settings.internal_token
    headers[GATEWAY_HEADER] = "1"
    params = request.query_params.multi_items()  # limit/offset/status gaan ongewijzigd door
    body = await request.body()

    stale_key = _stale_key(request.method, path, params)

    async def call() -> tuple[int, bytes, dict[str, str]]:
        # Overzichtsgegevens: niet 120 s wachten op een engine die onder volle belasting
        # traag is; na STALE_TIMEOUT de laatste bekende waarde tonen (rapport 3, R14).
        extra = {"timeout": STALE_TIMEOUT} if stale_key is not None and _last_good(
            ctx).get(stale_key) is not None else {}
        upstream = await _client(ctx).request(request.method, f"/api/{path}", params=params,
                                              content=body, headers=headers, **extra)
        out = {k: v for k, v in upstream.headers.items() if k.lower() not in _HOP}
        return upstream.status_code, upstream.content, out

    try:
        if request.method == "GET":
            status, content, out = await _single_flight(ctx, (path, tuple(sorted(params))),
                                                        call)
        else:
            _cache(ctx).clear()  # na een wijziging nooit een oud lijstje tonen
            status, content, out = await call()
    except httpx.HTTPError as exc:
        LOGGER.warning("AV1-engine onbereikbaar: %s", exc)
        stale = _last_good(ctx).get(_stale_key(request.method, path, params))
        if stale is not None:
            # laatste bekende waarde (bv. AV1-besparing) i.p.v. "nog niet beschikbaar" (N14)
            content, out, at = stale
            return Response(status_code=200, content=content, headers={
                **out, "X-MediaGuard-Stale": str(int(time.time() - at))})
        return Response(status_code=503, media_type="application/json",
                        content=b'{"detail":"AV1-engine is niet bereikbaar"}')
    key = stale_key
    if key is not None and status == 200:
        _last_good(ctx)[key] = (content, out, time.time())
    elif key is not None and status >= 500 and _last_good(ctx).get(key) is not None:
        content, out, at = _last_good(ctx)[key]
        return Response(status_code=200, content=content, headers={
            **out, "X-MediaGuard-Stale": str(int(time.time() - at))})
    return Response(status_code=status, content=content, headers=out)


# Overzichtsgegevens die bij een korte onderbreking hun laatste waarde mogen tonen: ook de
# wachtrij, jobs, fouten en systeemcijfers, anders toont Ruimte 'Nog niet beschikbaar'.
_STALE_OK = ("statistieken", "health", "instellingen", "functies", "wachtrij", "jobs",
             "fouten", "systeem", "workers", "hardware", "banen")
STALE_TIMEOUT = 8.0


def _stale_key(method: str, path: str, params: list[tuple[str, str]]) -> Any:
    clean = path.strip("/")
    if method != "GET" or not any(clean == p or clean.startswith(p + "/") for p in _STALE_OK):
        return None
    return (clean, tuple(sorted(params)))


def _last_good(ctx: AppContext) -> dict[Any, tuple[bytes, dict[str, str], float]]:
    return ctx.services.setdefault("encoder_gateway_last_good", {})  # type: ignore[no-any-return]


# Rapport 4 nov (M16): /encoder/jobs duurde tot 43 s omdat elke open pagina elke paar seconden
# opnieuw vroeg terwijl de vorige nog liep. Gelijke GET's binnen 2 s delen één antwoord, en
# een GET die al onderweg is wordt niet nog eens gestart.
CACHE_SECONDS = 2.0


def _cache(ctx: AppContext) -> dict[Any, Any]:
    return ctx.services.setdefault("encoder_gateway_cache", {})  # type: ignore[no-any-return]


async def _single_flight(ctx: AppContext, key: Any, call: Any) -> tuple[int, bytes,
                                                                        dict[str, str]]:
    cache = _cache(ctx)
    hit = cache.get(key)
    now = time.monotonic()
    if hit is not None:
        at, value = hit
        if isinstance(value, asyncio.Future):
            return await asyncio.shield(value)  # type: ignore[no-any-return]
        if now - at < CACHE_SECONDS:
            return value  # type: ignore[no-any-return]
    fut: asyncio.Future[Any] = asyncio.get_running_loop().create_future()
    cache[key] = (now, fut)
    try:
        value = await call()
    except BaseException as exc:
        cache.pop(key, None)
        fut.set_exception(exc)
        fut.exception()  # niet als 'nooit opgehaald' loggen
        raise
    fut.set_result(value)
    if value[0] < 500:
        cache[key] = (time.monotonic(), value)
    else:
        cache.pop(key, None)
    if len(cache) > 256:
        for k in [k for k, (at, v) in list(cache.items())
                  if not isinstance(v, asyncio.Future) and time.monotonic() - at > 10]:
            cache.pop(k, None)
    return value


def _ws_url(base: str) -> str:
    return ("wss://" + base[8:] if base.startswith("https://") else
            "ws://" + base.removeprefix("http://")).rstrip("/") + "/ws"


@router.websocket("/ws")
async def live(websocket: WebSocket) -> None:
    import websockets

    try:
        principal = principal_from_request(websocket)  # type: ignore[arg-type]
    except HTTPException:  # ongeldige API-sleutel: zelfde als niet ingelogd
        principal = None
    if principal is None:
        await websocket.close(code=4401)
        return
    ctx: AppContext = websocket.app.state.ctx
    await websocket.accept()
    try:
        async with websockets.connect(
                _ws_url(ctx.settings.encoder_url), open_timeout=5,
                additional_headers={"X-MediaGuard-Token": ctx.settings.internal_token}) as up:
            async def to_browser() -> None:
                async for message in up:
                    if isinstance(message, bytes):
                        await websocket.send_bytes(message)
                    else:
                        await websocket.send_text(message)

            async def to_engine() -> None:
                while True:
                    msg = await websocket.receive()
                    if msg.get("type") == "websocket.disconnect":
                        return
                    if msg.get("text") is not None:
                        await up.send(msg["text"])
                    elif msg.get("bytes") is not None:
                        await up.send(msg["bytes"])

            tasks = [asyncio.create_task(to_browser()), asyncio.create_task(to_engine())]
            _done, pending = await asyncio.wait(tasks, return_when=asyncio.FIRST_COMPLETED)
            for task in pending:
                task.cancel()
    except Exception as exc:  # noqa: BLE001 - engine weg: browser verbindt later opnieuw
        LOGGER.info("live-kanaal AV1-engine gesloten: %s", exc)
    with contextlib.suppress(Exception):
        await websocket.close(code=1011)


async def shutdown(ctx: AppContext) -> None:
    client = ctx.services.pop("encoder_gateway_client", None)
    if client is not None:
        await client.aclose()
