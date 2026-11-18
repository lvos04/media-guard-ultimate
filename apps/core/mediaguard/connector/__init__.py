"""Alleen-lezen connector voor het platform, contract 1.3 §5 en §6."""

from __future__ import annotations

import sqlite3
from datetime import UTC, datetime
from typing import Any

from fastapi import APIRouter, Depends
from fastapi.responses import JSONResponse

from mediaguard import __version__, health_summary
from mediaguard.auth import Principal, require_viewer
from mediaguard.context import AppContext, get_app_context
from mediaguard.db import MIGRATIONS_DIR

from .errors import failure

router = APIRouter(tags=["connector"])
MAX_AGE = 900
ORDER = {"healthy": 0, "unknown": 1, "degraded": 2, "down": 3}


def stamp() -> str:
    return datetime.now(UTC).isoformat(timespec="microseconds").replace("+00:00", "Z")


def parse(value: Any) -> datetime | None:
    try:
        dt = datetime.fromisoformat(str(value))
        return dt.astimezone(UTC) if dt.tzinfo else None
    except (ValueError, TypeError):
        return None


def check(
    name: str, state: str, observed: Any, detail: dict[str, Any] | None = None
) -> dict[str, Any]:
    measured = parse(observed)
    info = dict(detail or {})
    if measured is None:
        state = "unknown"
        info["reason_code"] = "meting_ontbreekt"
    elif (datetime.now(UTC) - measured).total_seconds() > MAX_AGE:
        state = "unknown"
        info["reason_code"] = "meting_verouderd"
    elif measured > datetime.now(UTC):
        state = "unknown"
        info["reason_code"] = "meettijd_ongeldig"
    return {
        "name": name,
        "status": state,
        "observed_at": measured.isoformat().replace("+00:00", "Z") if measured else None,
        "detail": info,
    }


@router.get("/connector/v1/discover")
def discover(_: Principal = Depends(require_viewer)) -> dict[str, Any]:
    """Alleen aanwezige leesmogelijkheden; items en acties volgen in andere stappen."""
    return {
        "app_id": "media-guard-ultimate",
        "source_version": __version__,
        "schema_version": "1.0",
        "capabilities": ["read"],
        "permissions": ["viewer"],
        "limits": {"max_age_s": {"health": MAX_AGE}},
        "timestamp_utc": stamp(),
        "item_types": [],
        "actions": [],
    }


@router.get("/connector/v1/health")
async def health(
    principal: Principal = Depends(require_viewer), ctx: AppContext = Depends(get_app_context)
) -> dict[str, Any]:
    """Zelfde gezondheidsbron als de UI; geen ruwe foutteksten, paden of kijkgedrag."""
    try:
        value = await health_summary.summary(principal, ctx)
    except (sqlite3.Error, OSError):
        checks = [check("database", "down", stamp(), {"reason_code": "database_unavailable"})]
    else:
        meta = ctx.services.get("health_summary_observations")
        observed = meta[1] if meta and meta[0] is value else {}
        at = value.get("generated_at")
        jobs = observed.get("jobs", {})
        beat = ctx.state_get("taskwatch:" + ctx.services["jobs"]._watch_name, {}) or {}
        checks = [
            check("database", "healthy", at),
            check(
                "worker",
                "degraded" if beat.get("fails") else "healthy",
                beat.get("last_ok_at"),
            ),
            check(
                "jobs", "degraded" if value["errors"].get("tasks_failed") else "healthy", at, jobs
            ),
            check(
                "encoder",
                {"ok": "healthy", "degraded": "degraded", "offline": "down"}.get(
                    value["encoder"].get("state"), "unknown"
                ),
                observed.get("encoder"),
            ),
            check(
                "instances",
                "degraded" if value["instances"]["unreachable"] else "healthy",
                observed.get("instances"),
            ),
        ]
        # Een niet-afnemende, uitvoerbare wachtrij met een oude oudste job is geen gezonde keten.
        if jobs.get("wachtrij", 0) and not ctx.emergency_stopped():
            oldest = parse(observed.get("oldest_queued_at"))
            finished = parse(jobs.get("laatste_job_klaar"))
            if (
                oldest
                and (datetime.now(UTC) - max(oldest, finished or oldest)).total_seconds() > MAX_AGE
                and checks[2]["status"] == "healthy"
            ):
                checks[2]["status"] = "degraded"
                checks[2]["detail"]["reason_code"] = "wachtrij_stil"
    worst = max(checks, key=lambda c: ORDER[c["status"]])
    times = [c["observed_at"] for c in checks if c["observed_at"]]
    oldest = min(times, key=lambda t: parse(t)) if times else None
    return {
        "status": worst["status"],
        "observed_at": oldest,
        "reason_code": worst["detail"].get(
            "reason_code", "ok" if worst["status"] == "healthy" else "onderdeel_" + worst["status"]
        ),
        "checks": checks,
    }


@router.post("/connector/v1/propose", status_code=501)
@router.post("/connector/v1/apply", status_code=501)
@router.post("/connector/v1/rollback", status_code=501)
def unavailable(_: Principal = Depends(require_viewer)) -> JSONResponse:
    """Geen acties; de bestaande centrale scope- en sessiecontrole blijft van toepassing."""
    return failure(501, "capability_unavailable", "Deze connectoractie is niet beschikbaar")


@router.get("/healthz", openapi_extra={"security": []})
def healthz() -> JSONResponse:
    """Proces leeft; geen versie of andere installatiegegevens."""
    return JSONResponse({"status": "ok"}, headers={"Cache-Control": "no-store"})


@router.get("/readyz", openapi_extra={"security": []})
def readyz(ctx: AppContext = Depends(get_app_context)) -> JSONResponse:
    """Alleen lezen: database bereikbaar en alle meegeleverde migraties toegepast."""
    try:
        applied = {r["version"] for r in ctx.db.fetch_all("SELECT version FROM schema_migrations")}
        ready = {f.stem for f in MIGRATIONS_DIR.glob("*.sql")} <= applied
    except (sqlite3.Error, OSError):
        ready = False
    return JSONResponse(
        {"status": "ok" if ready else "unavailable"},
        status_code=200 if ready else 503,
        headers={"Cache-Control": "no-store"},
    )
