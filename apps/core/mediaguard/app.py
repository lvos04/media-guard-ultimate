"""FastAPI-applicatiefabriek van mediaguard-core.

Modules melden zich aan in ``MODULES``: elk module-pad moet een ``router`` (APIRouter)
exporteren en mag optioneel ``async def startup(ctx)`` / ``async def shutdown(ctx)`` en
``def register_settings()`` hebben. Modules die (nog) niet bestaan worden overgeslagen
met een waarschuwing, zodat parallel werk elkaar niet blokkeert.
"""

from __future__ import annotations

import importlib
import logging
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from types import ModuleType
from typing import Any

from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse

from mediaguard import __version__
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
    modules = _load_modules()
    ctx = build_context(settings)
    runner = JobRunner(ctx)
    ctx.services["jobs"] = runner

    @asynccontextmanager
    async def lifespan(_: FastAPI) -> AsyncIterator[None]:
        if settings.start_background:
            await runner.start()
        started: list[ModuleType] = []
        for module in modules:
            startup = getattr(module, "startup", None)
            if startup is not None:
                await startup(ctx)
                started.append(module)
        try:
            yield
        finally:
            for module in reversed(started):
                shutdown = getattr(module, "shutdown", None)
                if shutdown is not None:
                    await shutdown(ctx)
            await runner.stop()

    install_log_redaction()
    # API-documentatie alleen voor een ingelogde beheerder (zie _admin_docs), niet open via
    # de tunnel; FastAPI's eigen openbare routes (/docs, /redoc, /openapi.json) staan uit.
    app = FastAPI(title="MediaGuard Ultimate API", version=__version__, lifespan=lifespan,
                  openapi_url=None, docs_url=None, redoc_url=None)
    app.state.ctx = ctx

    @app.exception_handler(ValueError)
    async def _value_error(_: Request, exc: ValueError) -> JSONResponse:
        return JSONResponse(status_code=422, content={"detail": redact(str(exc))})

    for module in modules:
        router = getattr(module, "router", None)
        if router is not None:
            app.include_router(router)
    _document_security(app)
    _admin_docs(app)
    return app


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
