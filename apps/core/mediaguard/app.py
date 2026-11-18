"""FastAPI-applicatiefabriek van mediaguard-core.

Modules melden zich aan in ``MODULES``: elk module-pad moet een ``router`` (APIRouter)
exporteren en mag optioneel ``async def startup(ctx)`` / ``async def shutdown(ctx)`` en
``def register_settings()`` hebben. Modules die (nog) niet bestaan worden overgeslagen
met een waarschuwing, zodat parallel werk elkaar niet blokkeert.
"""

from __future__ import annotations

import asyncio
import importlib
import logging
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager, suppress
from types import ModuleType
from typing import Any

from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse
from starlette.concurrency import run_in_threadpool
from starlette.types import ASGIApp, Receive, Scope, Send

from mediaguard import __version__, schema
from mediaguard.config import Settings, load_settings
from mediaguard.context import AppContext
from mediaguard.db import Database
from mediaguard.events import EventBus
from mediaguard.jobs import JobRunner
from mediaguard.secrets_store import SecretStore, install_log_redaction, redact

LOGGER = logging.getLogger(__name__)

# Volgorde = registratievolgorde van routers en startups.
MODULES: list[str] = [
    "mediaguard.api.auth",
    "mediaguard.api.system",
    "mediaguard.api.settings",
    "mediaguard.api.events",
    # B1: instanties, arr-integratie, bibliotheek, wanted/backfill, Seerr
    "mediaguard.instances.api",
    "mediaguard.library.api",
    "mediaguard.library.extra",
    "mediaguard.wanted.api",
    "mediaguard.seerr.api",
    "mediaguard.seerr.discover",
    "mediaguard.routing.api",
    "mediaguard.delivery.api",
    "mediaguard.mobile",
    # B2: identiteit, audio, policy, hooks, DV, recovery, optimalisatie, encoding
    "mediaguard.policy.api",
    "mediaguard.hooks.api",
    "mediaguard.dv.api",
    "mediaguard.recovery.api",
    "mediaguard.storage_typos",
    "mediaguard.storage_forecast",
    "mediaguard.storage_ledger",
    "mediaguard.optimization.api",
    "mediaguard.encoding.api",
    "mediaguard.encoding.gateway",
    "mediaguard.encoding.autopilot",
    "mediaguard.attention",
    # bewaking op de achtergrond: pusht problemen ook als niemand de app openheeft
    "mediaguard.monitor",
    "mediaguard.health_summary",
    "mediaguard.connector",
    "mediaguard.automation",
    "mediaguard.encoding.resources",
    "mediaguard.activity.api",
    "mediaguard.activity.manual_import",
    "mediaguard.downloads.janitor",
    "mediaguard.downloads.recovery",
    "mediaguard.downloads.failures",
    "mediaguard.mediaserver.api",
    # fase 2
    "mediaguard.migration.api",
    "mediaguard.migration.autopilot",
    "mediaguard.migration.aftercare",
    "mediaguard.ai.api",
    "mediaguard.maintenance.api",
    # meldingen buiten de browser, overzichten, bundel buiten de NAS, 2FA en sessies
    "mediaguard.notify.api",
    "mediaguard.security.api",
    # zero-config inrichting van de vier instanties, Prowlarr en Seerr
    "mediaguard.provisioning.api",
    # één melding als een update een nieuwe standaard met ander gedrag invoert
    "mediaguard.notices",
    # dagelijkse controle op een nieuwe MediaGuard-versie (GitHub-releases)
    "mediaguard.updates",
    # noodstop vastleggen voor de hookwacht (geldt ook terwijl core herstart)
    "mediaguard.hookguard",
]


def _load_modules() -> list[ModuleType]:
    loaded: list[ModuleType] = []
    for name in MODULES:
        try:
            module = importlib.import_module(name)
        except ModuleNotFoundError as exc:
            if exc.name and name.startswith(exc.name):
                LOGGER.warning("module %s nog niet aanwezig; overgeslagen", name)
                continue
            raise
        register = getattr(module, "register_settings", None)
        if callable(register):
            register()
        loaded.append(module)
    return loaded


def build_context(settings: Settings) -> AppContext:
    # recovery schrijft het vervangingsjournaal: daar elke commit direct naar schijf
    db = Database(settings.db_path,
                  synchronous="FULL" if settings.role == "recovery" else "NORMAL")
    db.initialize()
    return AppContext(settings=settings, db=db,
                      secrets=SecretStore(db, settings.secret_key_path), events=EventBus(db))


def create_app(settings: Settings | None = None) -> FastAPI:
    settings = settings or load_settings()
    ctx = build_context(settings)
    if ctx.db.read_only:
        LOGGER.warning(schema.MESSAGE)
        app = FastAPI(openapi_url=None, docs_url=None, redoc_url=None)
        app.state.ctx = ctx
        app.add_middleware(SchemaRecovery, database=ctx.db)
        return app

    modules = _load_modules()
    runner = JobRunner(ctx)
    ctx.services["jobs"] = runner

    @asynccontextmanager
    async def lifespan(_: FastAPI) -> AsyncIterator[None]:
        started: list[ModuleType] = []
        recovery_shutdown: asyncio.Task[None] | None = None

        async def stop_background() -> None:
            while started:
                module = started.pop()
                shutdown = getattr(module, "shutdown", None)
                if shutdown is not None:
                    with suppress(schema.SchemaTooNew):
                        await shutdown(ctx)
            with suppress(schema.SchemaTooNew):
                await runner.stop()

        async def watch_schema() -> None:
            nonlocal recovery_shutdown
            while not await run_in_threadpool(ctx.db.check_schema):
                await asyncio.sleep(1)
            recovery_shutdown = asyncio.create_task(stop_background())

        watcher = None
        try:
            if settings.start_background:
                await runner.start()
            for module in modules:
                startup = getattr(module, "startup", None)
                if startup is not None:
                    await startup(ctx)
                    started.append(module)
            if settings.start_background:
                watcher = asyncio.create_task(watch_schema())
        except schema.SchemaTooNew:
            await stop_background()
        try:
            yield
        finally:
            if watcher is not None:
                watcher.cancel()
                with suppress(asyncio.CancelledError):
                    await watcher
            if recovery_shutdown is not None:
                await recovery_shutdown
            else:
                await stop_background()

    install_log_redaction()
    # API-documentatie alleen voor een ingelogde beheerder (zie _admin_docs), niet open via
    # de tunnel; FastAPI's eigen openbare routes (/docs, /redoc, /openapi.json) staan uit.
    app = FastAPI(title="MediaGuard Ultimate API", version=__version__, lifespan=lifespan,
                  openapi_url=None, docs_url=None, redoc_url=None)
    app.state.ctx = ctx

    @app.exception_handler(ValueError)
    async def _value_error(_: Request, exc: ValueError) -> JSONResponse:
        if _.url.path.startswith("/connector/v1/"):
            from mediaguard.connector.errors import failure

            return failure(422, "validation_error", "Ongeldige invoer", {"fouten": []})
        return JSONResponse(status_code=422, content={"detail": redact(str(exc))})

    for module in modules:
        router = getattr(module, "router", None)
        if router is not None:
            app.include_router(router)
    from mediaguard.connector.errors import InputLimit, failure, install

    @app.exception_handler(schema.SchemaTooNew)
    async def _schema_error(request: Request, _: schema.SchemaTooNew) -> JSONResponse:
        if ctx.db.read_only:
            return SchemaRecovery.response(request.scope)
        return failure(409, schema.REASON, schema.MESSAGE)

    install(app)
    app.add_middleware(InputLimit)
    app.add_middleware(SchemaRecovery, database=ctx.db)
    _document_security(app)
    _admin_docs(app)
    return app


class SchemaRecovery:
    """Antwoord vóór routering, validatie, authenticatie en sessieverlenging."""

    def __init__(self, app: ASGIApp, database: Database) -> None:
        self.app = app
        self.database = database

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] not in {"http", "websocket"} or not await run_in_threadpool(
            self.database.check_schema,
        ):
            await self.app(scope, receive, send)
            return
        if scope["type"] == "websocket":
            await send({"type": "websocket.close", "code": 1013, "reason": schema.MESSAGE})
            return
        await self.response(scope)(scope, receive, send)

    @staticmethod
    def response(scope: Scope) -> JSONResponse:
        from mediaguard.connector.errors import failure

        path = scope["path"].rstrip("/")
        if scope["method"] in {"GET", "HEAD"} and path in {
            "/api/v1/health", "/healthz", "/readyz", "/connector/v1/health",
            "/api/v1/health/summary", "/api/v1/admin/health",
            "/api/v1/downloads/health", "/api/v1/settings/secrets/health",
        }:
            health: dict[str, Any] = {
                "status": "down", "reason_code": schema.REASON, "message": schema.MESSAGE,
            }
            if path == "/connector/v1/health":
                from mediaguard.connector import check, stamp

                at = stamp()
                health["observed_at"] = at
                health["checks"] = [
                    check("database", "down", at, {"reason_code": schema.REASON}),
                    check("worker", "unknown", None),
                    check("jobs", "unknown", None),
                ]
            return JSONResponse(
                health,
                status_code=503,
                headers={"Cache-Control": "no-store", "Retry-After": "300"},
            )
        return failure(409, schema.REASON, schema.MESSAGE)


def _admin_docs(app: FastAPI) -> None:
    """``/api/v1/openapi.json`` en ``/api/v1/docs`` alleen voor een ingelogde beheerder."""
    from fastapi import Depends
    from fastapi.openapi.docs import get_swagger_ui_html
    from fastapi.responses import HTMLResponse

    from mediaguard.auth import Principal, require_admin

    @app.get("/api/v1/openapi.json", include_in_schema=False)
    def openapi_json(_: Principal = Depends(require_admin)) -> JSONResponse:
        return JSONResponse(app.openapi())

    @app.get("/api/v1/docs", include_in_schema=False)
    def swagger(_: Principal = Depends(require_admin)) -> HTMLResponse:
        return get_swagger_ui_html(openapi_url="/api/v1/openapi.json",
                                   title=f"{app.title} – documentatie")


def _document_security(app: FastAPI) -> None:
    """securitySchemes in /api/v1/openapi.json: sessie (mensen), API-sleutel (koppelingen
    zoals Media Source Optimizer) en de interne token. Verandert geen gedrag."""
    from fastapi.openapi.utils import get_openapi

    def custom() -> dict[str, Any]:
        if app.openapi_schema:
            return app.openapi_schema
        schema = get_openapi(title=app.title, version=app.version, routes=app.routes,
                             description="Rollen: viewer < operator < admin. API-sleutels "
                             "(header X-Api-Key) hebben rol viewer of operator, nooit admin.")
        schema.setdefault("components", {})["securitySchemes"] = {
            "session": {"type": "apiKey", "in": "cookie", "name": "mg_session",
                        "description": "Sessie voor mensen; muterende verzoeken vereisen "
                                       "ook header X-CSRF-Token."},
            "apiKey": {"type": "apiKey", "in": "header", "name": "X-Api-Key",
                       "description": "API-sleutel (mgk_…) met rol viewer of operator; "
                                      "aan te maken door een beheerder onder Instellingen → "
                                      "API-sleutels. Geen CSRF nodig."},
            "internalToken": {"type": "apiKey", "in": "header", "name": "X-MediaGuard-Token",
                              "description": "Interne token (admin); alleen voor eigen "
                                             "diensten."},
        }
        schema["security"] = [{"session": []}, {"apiKey": []}, {"internalToken": []}]
        app.openapi_schema = schema
        return schema

    app.openapi = custom  # type: ignore[method-assign]
