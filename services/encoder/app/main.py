"""FastAPI-applicatie, herstelvolgorde en levenscyclus van de native backend."""

from __future__ import annotations

import asyncio
import json
import logging
import os
import time
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager, suppress
from typing import Any

from fastapi import FastAPI

from app import __version__
from app.ai_controller import AiController, reconcile_safety_fingerprint
from app.api import create_api_router
from app.config import ConfigError, Settings, settings
from app.cpubudget import unify_cpu_percent
from app.db import Database
from app.health import dependency_report
from app.integraties import IntegrationMonitor
from app.live import clients_connected, register_websocket
from app.media.beslissen import STANDAARD
from app.mediaguard.api import create_mediaguard_router
from app.mediaguard.auth import LegacyApiTokenMiddleware
from app.mediaguard.capabilities import (
    CapabilityService,
    apply_lane_gates,
    capability_watch_loop,
    honest_dependencies,
)
from app.mediaguard.container import prepare_container_storage
from app.mediaguard.handoff import sweep_orphan_staging
from app.metingen import system_metrics_loop
from app.onderhoud import STORAGE_NOT_READY_PREFIX, maintenance_loop
from app.opslag import storage_validation_error
from app.planner import Planner
from app.scanner import SCAN_STOP
from app.scanscheduler import LibraryScanScheduler
from app.systeem import GpuMonitor
from app.vervanging import ReplacementError, recover_replacements

LOGGER = logging.getLogger(__name__)


def _ensure_default_profile(database: Database) -> None:
    row = database.fetch_one("SELECT id FROM profiel LIMIT 1")
    if row is None:
        database.execute_insert(
            "INSERT INTO profiel(naam,inst) VALUES (?,?)",
            ("Standaard veilig", json.dumps(STANDAARD, ensure_ascii=False)),
        )


class WakePlannerMiddleware:
    """Na iedere wijzigende API-aanroep (nieuwe job, opnieuw, workers, instellingen) de
    planner meteen laten kijken; zonder wijziging tikt hij hooguit elke 30 s."""

    def __init__(self, app: Any, planner: Planner) -> None:
        self.app = app
        self.planner = planner

    async def __call__(self, scope: Any, receive: Any, send: Any) -> None:
        await self.app(scope, receive, send)
        if scope.get("type") == "http" and scope.get("method") not in ("GET", "HEAD", "OPTIONS"):
            self.planner.wake()


class QuietAccessLog(logging.Filter):
    """Uvicorn-toegangslog alleen voor fouten (>= 400) en wijzigende verzoeken: geen regel
    per healthcheck of per poll van de UI in het containerlog."""

    def filter(self, record: logging.LogRecord) -> bool:
        args = record.args
        if isinstance(args, tuple) and len(args) >= 5:
            method, status = str(args[1]), args[4]
            try:
                return int(str(status)) >= 400 or method not in ("GET", "HEAD")
            except (TypeError, ValueError):
                return True
        return True


logging.getLogger("uvicorn.access").addFilter(QuietAccessLog())


def create_app(
    app_settings: Settings = settings,
    app_database: Database | None = None,
    *,
    start_planner: bool = True,
    validate_storage: bool = True,
) -> FastAPI:
    database = app_database or Database(app_settings)

    @asynccontextmanager
    async def lifespan(application: FastAPI) -> AsyncIterator[None]:
        from app import logboek

        # rapport 16: info-regels naar /config/logs/encoder.log (alleen met MG_LOG_DIR)
        log_task = (asyncio.create_task(logboek.volg_loop())
                    if logboek.inrichten(app_settings.log_level) is not None else None)
        storage_error: str | None = None
        if app_settings.container_mode:
            # TrueNAS: maak alleen de eigen submappen onder /config en /work aan
            # (geen chown, niets in de bibliotheek) en een eigen sleutelbestand.
            prepare_container_storage(app_settings)
        if validate_storage:
            try:
                app_settings.validate_storage()
            except ConfigError as exc:
                # De kleine controledatabase blijft op de systeemschijf bereikbaar,
                # zodat een ontbrekende latere werkschijf via de UI kan worden hersteld.
                storage_error = str(exc)
                LOGGER.error("Opslag niet gereed; workers blijven gepauzeerd: %s", exc)
        database.initialize()
        from app import afstelling

        afstelling.refresh(database)
        from app import routeleer

        routeleer.refresh(database)
        if app_settings.container_mode:
            from app.uitvoerder import reroute_failed_gpu_quality_jobs

            with suppress(Exception):
                reroute_failed_gpu_quality_jobs(database)
        application.state.storage_error = storage_error
        if storage_error is not None:
            # 'automatisch:' = door het systeem gezet en weer op te heffen zodra de
            # opslagbewaking ruimte ziet; iedere claim valideert de opslag opnieuw.
            # Een handmatige of MediaGuard-pauze blijft met haar eigen reden staan.
            database.execute(
                "UPDATE worker_config SET gepauzeerd=1,pauze_reden=? "
                "WHERE gepauzeerd=0 OR pauze_reden LIKE 'automatisch:%'",
                (f"{STORAGE_NOT_READY_PREFIX}{storage_error}",),
            )
        _ensure_default_profile(database)
        unify_cpu_percent(database, container_mode=app_settings.container_mode)
        if app_settings.container_mode:
            from app.mediaguard.service import _profile_id

            with suppress(Exception):  # alleen weergave: naam + GPU-waarden van het profiel
                _profile_id(database)
        if app_settings.container_mode:
            # Fail-closed: GPU-baan pas aan na een geslaagde echte QSV-proef;
            # ontbrekende mediamounts pauzeren beide banen met een duidelijke reden.
            capabilities = await application.state.capabilities.detect(
                refresh=True, run_qsv_probe=True
            )
            apply_lane_gates(database, capabilities, app_settings)
        reconcile_safety_fingerprint(database)
        # Containermodus: MediaGuard core beslist. De encoder scant niet zelf,
        # maakt geen eigen besluiten en draait geen AI-controller; hij voert
        # alleen de jobs uit die via de brug binnenkomen.
        decides_itself = not app_settings.container_mode
        if decides_itself:
            application.state.scan_scheduler.initialize_schedule()
        recovery = recover_replacements(database, app_settings)
        if recovery:
            LOGGER.warning("%d vervangtransacties beoordeeld bij opstart", len(recovery))
        if app_settings.replacement_mode == "handoff":
            try:
                orphans = sweep_orphan_staging(database, app_settings)
            except (OSError, ReplacementError) as exc:
                LOGGER.error("Stagingmap kon niet worden opgeschoond: %s", exc)
            else:
                if orphans:
                    LOGGER.warning("Verweesde stagingmappen opgeruimd voor jobs: %s", orphans)
        gpu_enabled = database.fetch_one(
            "SELECT ingeschakeld FROM functie WHERE sleutel='gpu_monitor'"
        )
        # containermodus: de GPU-meting via fdinfo is goedkoop en altijd nuttig (Belasting)
        if app_settings.container_mode or (
                gpu_enabled is not None and int(gpu_enabled["ingeschakeld"])):
            await application.state.gpu_monitor.start()
        planner_task: asyncio.Task[None] | None = None
        scan_scheduler_task: asyncio.Task[None] | None = None
        ai_controller_task: asyncio.Task[None] | None = None
        system_metrics_task = asyncio.create_task(
            system_metrics_loop(
                database,
                app_settings,
                application.state.gpu_monitor,
                busy=lambda: bool(application.state.planner.tasks) or clients_connected(),
            )
        )
        integration_task = asyncio.create_task(application.state.integration_monitor.run_forever())
        maintenance_task = asyncio.create_task(maintenance_loop(database, app_settings))
        # GPU later doorgegeven of /dev/dri pas na de start: automatisch oppikken.
        gpu_watch_task = (
            asyncio.create_task(
                capability_watch_loop(application.state.capabilities, database))
            if app_settings.container_mode else None
        )
        if start_planner:
            planner_task = asyncio.create_task(application.state.planner.run_forever())
            if decides_itself:
                scan_scheduler_task = asyncio.create_task(
                    application.state.scan_scheduler.run_forever()
                )
                ai_controller_task = asyncio.create_task(
                    application.state.ai_controller.run_forever()
                )
        SCAN_STOP.clear()
        yield
        # Een scan van uren mag de stop niet ophouden; hij hervat later vanaf zijn cursor.
        SCAN_STOP.set()
        application.state.planner.stopping = True
        application.state.scan_scheduler.stopping = True
        application.state.ai_controller.stopping = True
        application.state.integration_monitor.stopping = True
        if planner_task is not None:
            await planner_task
        if scan_scheduler_task is not None:
            await scan_scheduler_task
        if ai_controller_task is not None:
            await ai_controller_task
        integration_task.cancel()
        with suppress(asyncio.CancelledError):
            await integration_task
        maintenance_task.cancel()
        with suppress(asyncio.CancelledError):
            await maintenance_task
        if gpu_watch_task is not None:
            gpu_watch_task.cancel()
            with suppress(asyncio.CancelledError):
                await gpu_watch_task
        system_metrics_task.cancel()
        with suppress(asyncio.CancelledError):
            await system_metrics_task
        await application.state.gpu_monitor.stop()
        if log_task is not None:
            log_task.cancel()
            with suppress(asyncio.CancelledError):
                await log_task
        tasks: dict[str, asyncio.Task[object]] = application.state.background_tasks
        for task in tasks.values():
            task.cancel()
        for task in list(tasks.values()):
            with suppress(asyncio.CancelledError):
                await task
        SCAN_STOP.clear()

    application = FastAPI(
        title="AV1 Pipeline",
        description=("Veilige, expliciet goedgekeurde AV1-pipeline voor één native Ubuntu-host."),
        version=__version__,
        lifespan=lifespan,
    )
    application.state.settings = app_settings
    application.state.database = database
    application.state.gpu_monitor = GpuMonitor()
    application.state.gpu_monitor.settings = app_settings
    application.state.planner = Planner(database, app_settings)
    application.state.scan_scheduler = LibraryScanScheduler(database)
    application.state.ai_controller = AiController(database, app_settings)
    application.state.integration_monitor = IntegrationMonitor(database, app_settings)
    application.state.background_tasks = {}
    application.state.storage_error = None
    application.state.capabilities = CapabilityService(app_settings)
    application.include_router(create_api_router())
    application.include_router(create_mediaguard_router())
    if app_settings.container_mode and os.environ.get("MG_PROTECT_LEGACY_API", "1") != "0":
        application.add_middleware(LegacyApiTokenMiddleware)
    application.add_middleware(WakePlannerMiddleware, planner=application.state.planner)
    register_websocket(application)

    @application.get("/livez", include_in_schema=False)
    async def livez() -> dict[str, str]:
        """Levenscontrole van de supervisor: alleen 'de eventloop draait'. Bewust zonder
        subprocessen, GPU- of databasewerk, zodat hij ook onder volle encodebelasting snel
        antwoordt (een trage /health liet de engine eerder elke ~6 min herstarten)."""
        return {"status": "ok"}

    dependency_cache: dict[str, Any] = {}

    async def cached_dependencies() -> dict[str, Any]:
        # ffmpeg -encoders, vainfo e.d. veranderen niet tijdens het draaien: 5 min bewaren
        now = time.monotonic()
        if dependency_cache.get("value") is None or now - dependency_cache["at"] > 300:
            dependency_cache["value"] = await dependency_report(app_settings.render_node)
            dependency_cache["at"] = now
        return dict(dependency_cache["value"])

    @application.get("/health", include_in_schema=False)
    async def root_health() -> dict[str, object]:
        dependencies = honest_dependencies(
            await cached_dependencies(),
            application.state.capabilities.cached)
        database_ok = await asyncio.to_thread(database.reachable)
        application.state.storage_error = await asyncio.to_thread(
            storage_validation_error, app_settings)
        return {
            "status": (
                "ok"
                if database_ok
                and dependencies["media_stack_gereed"]
                and application.state.storage_error is None
                else "degraded"
            ),
            "versie": __version__,
            "database": "ok" if database_ok else "fout",
            "opslag": (
                "ok" if application.state.storage_error is None else application.state.storage_error
            ),
            "dependencies": dependencies,
        }

    return application


app = create_app()
