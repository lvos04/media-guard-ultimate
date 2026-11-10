"""Encoding-API (AV1 Studio): jobs, per-titelplan, benchmarks, capabilities."""

from __future__ import annotations

import asyncio
import contextlib
import logging
from typing import Any

from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel, Field

from mediaguard import taskwatch
from mediaguard.audit import audit
from mediaguard.auth import Principal, require_operator, require_viewer
from mediaguard.context import AppContext, get_app_context
from mediaguard.db import dumps, loads, utcnow
from mediaguard.encoding import resources, service
from mediaguard.encoding.client import EncoderError, get_encoder_client
from mediaguard.settings_registry import REGISTRY

LOGGER = logging.getLogger(__name__)
router = APIRouter(prefix="/api/v1/encoding", tags=["encoding"])


def register_settings() -> None:
    REGISTRY.register(*service.setting_defs())


IDLE_POLL_SECONDS = 300


async def _poll_loop(ctx: AppContext) -> None:
    while True:
        active = 0
        try:
            active = await service.sync_active(ctx)
        except Exception:  # noqa: BLE001
            LOGGER.exception("encoder-sync mislukt")
        try:
            await service.activate_waiting(ctx)
        except Exception:  # noqa: BLE001
            LOGGER.exception("automatisch activeren mislukt")
        try:
            await service.reconcile_committed(ctx)
        except Exception:  # noqa: BLE001
            LOGGER.exception("overdrachten naar de encoder bevestigen mislukt")
        try:
            # Vangnet: een push die mislukte (encoder offline) wordt hier herhaald.
            await service.push_encoder_settings(ctx)
            await resources.push(ctx)
            # Vangnet na een engineherstart of mislukte push: elke poll de noodstop opnieuw
            # zetten (idempotent aan de engine-kant).
            await service.push_emergency_stop(ctx, force=True)
            await service.reconcile_engine_commits(ctx)
        except Exception:  # noqa: BLE001
            LOGGER.exception("encoder-instellingen doorzetten mislukt")
        taskwatch.ok(ctx, "encoding_poll")
        try:
            interval = int(ctx.config.value("encoding.poll_interval_seconds"))
        except Exception:  # noqa: BLE001 - instelling onleesbaar: rustig verder
            interval = 60
        # niets onderweg: zelden kijken (instellingswijzigingen gaan via de listener direct)
        await asyncio.sleep(interval if active else max(interval, IDLE_POLL_SECONDS))


async def _settings_listener(ctx: AppContext) -> None:
    """Zet een gewijzigde pauzebodem direct door (niet pas bij de volgende poll)."""
    queue = ctx.events.subscribe()
    try:
        while True:
            event = await queue.get()
            if event.get("topic") == "system.emergency_stop":
                # Direct doorzetten: lopende encodes bevriezen / hervatten (rapport 6, N1).
                try:
                    await service.push_emergency_stop(ctx, force=True)
                except Exception:  # noqa: BLE001
                    LOGGER.exception("noodstop doorzetten naar de AV1-engine mislukt")
                continue
            if event.get("topic") != "settings.changed":
                continue
            changes = (event.get("payload") or {}).get("changes") or []
            if any(c.get("key") == "encoding.cache_min_free_gb" for c in changes):
                try:
                    await service.push_encoder_settings(ctx, force=True)
                except Exception:  # noqa: BLE001
                    LOGGER.exception("pauzebodem doorzetten mislukt")
            if any(str(c.get("key", "")).startswith("resources.") for c in changes):
                try:
                    await resources.push(ctx)
                except Exception:  # noqa: BLE001
                    LOGGER.exception("systeembelasting doorzetten mislukt")
    finally:
        ctx.events.unsubscribe(queue)


SAFE_KEEP_KEYS = ("encoding.keep_all_subtitles", "encoding.keep_all_audio")


def ensure_safe_track_defaults(ctx: AppContext) -> list[str]:
    """Eenmalig (rapport 2, N2): 'alle ondertitels/audio bewaren' stond bij bestaande
    installaties soms uit, waardoor AV1-versies sporen misten. Zet het één keer terug op
    aan (op elk niveau) en meld dat; daarna respecteert MediaGuard jouw keuze weer."""
    if ctx.state_get("safe_track_defaults_r2"):
        return []
    rows = ctx.db.fetch_dicts(
        "SELECT scope_type,scope_id,key,value_json FROM settings_values WHERE key IN (?,?)",
        SAFE_KEEP_KEYS)
    flipped = []
    for r in rows:
        if loads(r["value_json"], True) is False:
            ctx.config.set(r["scope_type"], r["scope_id"], r["key"], True,
                           "svc:safe-defaults")
            flipped.append(r["key"])
    ctx.state_set("safe_track_defaults_r2", {"at": utcnow(), "flipped": flipped})
    if flipped:
        from mediaguard.api.system import notify

        what = " en ".join(sorted({"ondertitels" if "subtitle" in k else "audiosporen"
                                   for k in flipped}))
        notify(ctx, "info", f"Alle {what} gaan weer mee naar AV1",
               f"'Alle {what} bewaren' stond uit, waardoor AV1-versies sporen konden missen. "
               "MediaGuard heeft dit weer aangezet. Wil je toch talen weglaten, kies ze dan "
               "bewust bij Instellingen → AV1.", link="/instellingen",
               dedupe_key="encoding.safe_track_defaults")
    return flipped


async def startup(ctx: AppContext) -> None:
    try:
        ensure_safe_track_defaults(ctx)
    except Exception:  # noqa: BLE001 - nooit de start blokkeren
        LOGGER.exception("veilige spoorstandaard zetten mislukt")
    if ctx.settings.start_background and ctx.settings.role in ("core", "all"):
        ctx.services["encoding_poll_task"] = taskwatch.spawn(
            ctx, "encoding_poll", _poll_loop, label_nl="AV1-engine bijhouden",
            every=IDLE_POLL_SECONDS)
        # wacht op gebeurtenissen: alleen herstarten bij een fout, geen stilstandbewaking
        ctx.services["encoding_settings_task"] = taskwatch.spawn(
            ctx, "encoding_settings", _settings_listener,
            label_nl="Instellingen doorzetten naar de AV1-engine")


async def shutdown(ctx: AppContext) -> None:
    for name in ("encoding_poll_task", "encoding_settings_task"):
        task = ctx.services.pop(name, None)
        if task is not None:
            task.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await task


def _gate_http(exc: service.GateError) -> HTTPException:
    status = 503 if exc.code in ("ENCODER_UNAVAILABLE",) else 409
    return HTTPException(status, {"code": exc.code, "message": exc.message})


@router.get("/capabilities")
async def capabilities(_: Principal = Depends(require_viewer),
                       ctx: AppContext = Depends(get_app_context)) -> dict[str, Any]:
    try:
        caps = await get_encoder_client(ctx).capabilities()
    except EncoderError as exc:
        return {"available": False, "error": str(exc)}
    return {"available": True, "capabilities": caps}


@router.get("/storage")
async def storage_floor(_: Principal = Depends(require_viewer),
                        ctx: AppContext = Depends(get_app_context)) -> dict[str, Any]:
    """Pauzebodem (core-instelling) + wat de encoder nu meet en welke banen pauzeren."""
    desired = ctx.config.effective("encoding.cache_min_free_gb")
    out: dict[str, Any] = {"setting": desired,
                           "last_push": ctx.state_get(service.PUSHED_FLOOR_STATE)}
    try:
        out["encoder"] = await get_encoder_client(ctx).storage_settings()
        out["available"] = True
    except EncoderError as exc:
        out.update(available=False, error=str(exc))
    return out


@router.post("/storage/push")
async def storage_floor_push(principal: Principal = Depends(require_operator),
                             ctx: AppContext = Depends(get_app_context)) -> dict[str, Any]:
    result = await service.push_encoder_settings(ctx, force=True)
    audit(ctx.db, actor=principal.actor, action="encoding.storage_floor.push",
          target_type="setting", target_id="encoding.cache_min_free_gb",
          new_value={"value": result.get("value"), "pushed": result.get("pushed")})
    if not result.get("pushed"):
        raise HTTPException(503, {"code": "ENCODER_UNAVAILABLE",
                                  "message": result.get("error") or "push mislukt"})
    return result


@router.get("/jobs")
def list_jobs(status: str | None = None, instance_id: str | None = None,
              limit: int = Query(200, le=2000), _: Principal = Depends(require_viewer),
              ctx: AppContext = Depends(get_app_context)) -> list[dict[str, Any]]:
    sql = "SELECT id FROM encoding_jobs WHERE 1=1"
    params: list[Any] = []
    if status:
        sql += " AND status=?"
        params.append(status)
    if instance_id:
        sql += " AND instance_id=?"
        params.append(instance_id)
    rows = ctx.db.fetch_all(sql + " ORDER BY id DESC LIMIT ?", [*params, limit])
    return [j for r in rows if (j := service.get_job(ctx, int(r["id"]))) is not None]


@router.get("/forecast")
def forecast(_: Principal = Depends(require_viewer),
             ctx: AppContext = Depends(get_app_context)) -> dict[str, Any]:
    """Prognose voor Ruimte → Doorvoer (rapport 3, R12): echte doorvoer van de laatste 24 uur
    (anders 7 dagen) over de hele resterende hoeveelheid werk; geen datum bij lege wachtrij."""
    from mediaguard.encoding.autopilot import ENCODER_BUSY
    from mediaguard.optimization.planner import weeks_nl

    def n(sql: str, *p: Any) -> int:
        row = ctx.db.fetch_one(sql, p)
        return int((row[0] if row else 0) or 0)

    day = n("SELECT COUNT(*) FROM encoding_jobs WHERE status='committed' AND "
            "updated_at>=datetime('now','-1 day')")
    week = n("SELECT COUNT(*) FROM encoding_jobs WHERE status='committed' AND "
             "updated_at>=datetime('now','-7 days')")
    marks = ",".join("?" for _ in ENCODER_BUSY)
    busy = n(f"SELECT COUNT(*) FROM encoding_jobs WHERE status IN ({marks})",  # noqa: S608
             *ENCODER_BUSY)
    planned = n("SELECT COUNT(*) FROM optimization_items i JOIN optimization_plans p ON "
                "p.id=i.plan_id WHERE p.status='running' AND i.status='planned' AND "
                "i.action='av1'")
    remaining = busy + planned
    per_day = float(day) if day else week / 7
    days = remaining / per_day if per_day and remaining else None
    if not remaining:
        text = "Niets in de wachtrij"
    elif not per_day:
        text = f"{remaining} bestanden te gaan; nog geen tempo bekend"
    else:
        text = (f"{remaining} bestanden te gaan, ± {per_day:.0f} per dag: klaar over "
                f"{weeks_nl(days)}")
    return {"remaining": remaining, "in_progress": busy, "planned": planned,
            "per_day": round(per_day, 1), "basis": "24 uur" if day else "7 dagen",
            "eta_days": round(days, 1) if days else None, "eta_nl": weeks_nl(days),
            "text_nl": text}


@router.get("/engine-states")
def engine_states(_: Principal = Depends(require_viewer),
                  ctx: AppContext = Depends(get_app_context)) -> dict[str, Any]:
    """Per engine-job de status volgens MediaGuard: teruggezet, of opgelost door een latere
    poging (telt dan niet meer als fout). Sleutel = engine-jobnummer (rapport 3, R4/R21)."""
    from mediaguard.encoding import jobstates

    return {"items": [s for s in jobstates.states(ctx) if s["engine_id"] is not None]}


@router.get("/jobs/resolve/{job_id}")
def resolve_job(job_id: int, _: Principal = Depends(require_viewer),
                ctx: AppContext = Depends(get_app_context)) -> dict[str, Any]:
    """Engine- of MediaGuard-jobnummer → beide nummers (links uit Problemen, rapport 3 R5)."""
    from mediaguard.encoding import jobstates

    found = jobstates.resolve(ctx, job_id)
    if found is None:
        raise HTTPException(404, "Encodeerjob niet gevonden")
    return found


@router.get("/jobs/{job_id}")
def job_detail(job_id: int, _: Principal = Depends(require_viewer),
               ctx: AppContext = Depends(get_app_context)) -> dict[str, Any]:
    job = service.get_job(ctx, job_id)
    if job is None:
        raise HTTPException(404, "Encodeerjob niet gevonden")
    return job


class JobBody(BaseModel):
    path: str = Field(min_length=1, max_length=4096)
    instance_id: str | None = None
    media_kind: str | None = None
    provider_id: int | str | None = None
    series_id: int | None = None
    movie_id: int | None = None
    media_type: str | None = None
    episode_ids: list[int] = Field(default_factory=list)
    trial_only: bool = False
    force_cpu: bool = False


def _refresh_for(body: JobBody) -> dict[str, Any]:
    if body.series_id is not None:
        return {"command": "RescanSeries", "seriesId": body.series_id,
                "episode_ids": body.episode_ids}
    if body.movie_id is not None:
        return {"command": "RefreshMovie", "movieIds": [body.movie_id],
                "movie_id": body.movie_id}
    return {}


@router.post("/jobs")
async def create_job(body: JobBody, principal: Principal = Depends(require_operator),
                     ctx: AppContext = Depends(get_app_context)) -> dict[str, Any]:
    try:
        return await service.submit(
            ctx, path=body.path, instance_id=body.instance_id, media_kind=body.media_kind,
            provider_id=body.provider_id, series_id=body.series_id, movie_id=body.movie_id,
            media_type=body.media_type, trial_only=body.trial_only, force_cpu=body.force_cpu,
            actor=principal.actor, refresh=_refresh_for(body))
    except service.GateError as exc:
        raise _gate_http(exc) from exc


@router.post("/jobs/{job_id}/cancel")
async def cancel_job(job_id: int, principal: Principal = Depends(require_operator),
                     ctx: AppContext = Depends(get_app_context)) -> dict[str, Any]:
    try:
        return await service.cancel(ctx, job_id, principal.actor)
    except LookupError as exc:
        raise HTTPException(404, str(exc)) from exc
    except service.GateError as exc:
        raise _gate_http(exc) from exc


class ActivateBody(BaseModel):
    confirm: bool = False
    hardlink_break_confirmed: bool = False


@router.post("/jobs/{job_id}/activate")
async def activate_job(job_id: int, body: ActivateBody,
                       principal: Principal = Depends(require_operator),
                       ctx: AppContext = Depends(get_app_context)) -> dict[str, Any]:
    if not body.confirm:
        raise HTTPException(422, "Bevestiging vereist ('confirm': true)")
    try:
        return await service.handoff(ctx, job_id, actor=principal.actor,
                                     hardlink_break_confirmed=body.hardlink_break_confirmed)
    except LookupError as exc:
        raise HTTPException(404, str(exc)) from exc
    except service.GateError as exc:
        raise _gate_http(exc) from exc


@router.post("/sync")
async def sync(_: Principal = Depends(require_operator),
               ctx: AppContext = Depends(get_app_context)) -> dict[str, Any]:
    return {"synced": await service.sync_active(ctx)}


class PlanBody(BaseModel):
    path: str = Field(min_length=1, max_length=4096)
    instance_id: str | None = None
    media_kind: str | None = None
    provider_id: int | str | None = None
    series_id: int | None = None
    movie_id: int | None = None
    media_type: str | None = None


@router.post("/plan")
async def plan(body: PlanBody, principal: Principal = Depends(require_operator),
               ctx: AppContext = Depends(get_app_context)) -> dict[str, Any]:
    return await service.plan_title(ctx, actor=principal.actor, **body.model_dump())


@router.get("/plan/{plan_id}")
def plan_get(plan_id: int, _: Principal = Depends(require_viewer),
             ctx: AppContext = Depends(get_app_context)) -> dict[str, Any]:
    row = ctx.db.fetch_one("SELECT * FROM encoding_plans WHERE id=?", (plan_id,))
    if row is None:
        raise HTTPException(404, "Plan niet gevonden")
    data = loads(row["plan_json"], {})
    data["id"] = plan_id
    return data


@router.post("/plan/{plan_id}/simulate")
async def plan_simulate(plan_id: int, _: Principal = Depends(require_viewer),
                        ctx: AppContext = Depends(get_app_context)) -> dict[str, Any]:
    """Herbereken plan + poorten met de huidige instellingen, zonder op te slaan."""
    old = plan_get(plan_id, _, ctx)
    new = await service.plan_title(ctx, path=old["source_path"],
                                   instance_id=old.get("instance_id"),
                                   media_kind=old.get("media_kind"),
                                   provider_id=old.get("provider_id"), store=False)
    return {"before": old, "after": new}


@router.post("/plan/{plan_id}/execute")
async def plan_execute(plan_id: int, principal: Principal = Depends(require_operator),
                       ctx: AppContext = Depends(get_app_context)) -> dict[str, Any]:
    data = plan_get(plan_id, principal, ctx)
    fresh = await service.plan_title(ctx, path=data["source_path"],
                                     instance_id=data.get("instance_id"),
                                     media_kind=data.get("media_kind"),
                                     provider_id=data.get("provider_id"), store=False)
    if not fresh["approved_for_execution"]:
        failed = [g for g in fresh["gates"] if not g["passed"]]
        raise HTTPException(409, {"code": "GATES_FAILED", "gates": failed,
                                  "errors": fresh["errors"]})
    try:
        return await service.submit(ctx, path=data["source_path"],
                                    instance_id=data.get("instance_id"),
                                    media_kind=data.get("media_kind"),
                                    provider_id=data.get("provider_id"),
                                    actor=principal.actor, plan_id=plan_id)
    except service.GateError as exc:
        raise _gate_http(exc) from exc


class BenchmarkBody(BaseModel):
    path: str = Field(min_length=1, max_length=4096)
    samples: list[dict[str, float]] = Field(default_factory=list, max_length=20)
    settings: list[dict[str, Any]] = Field(default_factory=list, max_length=50)
    dv_mode: str = "keep_original"


@router.get("/benchmarks")
def benchmarks(_: Principal = Depends(require_viewer),
               ctx: AppContext = Depends(get_app_context)) -> list[dict[str, Any]]:
    rows = ctx.db.fetch_dicts("SELECT * FROM encoding_benchmarks ORDER BY id DESC LIMIT 200")
    for r in rows:
        r["request"] = loads(r.pop("request_json"), {})
        r["result"] = loads(r.pop("result_json"), None)
    return rows


@router.post("/benchmarks")
async def run_benchmark(body: BenchmarkBody, principal: Principal = Depends(require_operator),
                        ctx: AppContext = Depends(get_app_context)) -> dict[str, Any]:
    request = body.model_dump()
    bid = ctx.db.execute_insert(
        "INSERT INTO encoding_benchmarks(source_path,request_json,status,created_at,created_by)"
        " VALUES (?,?,?,?,?)", (body.path, dumps(request), "running", utcnow(),
                                 principal.actor))
    try:
        result = await get_encoder_client(ctx).trials(request)
    except EncoderError as exc:
        ctx.db.execute("UPDATE encoding_benchmarks SET status='failed', error=? WHERE id=?",
                       (str(exc), bid))
        raise HTTPException(502, f"Proefencode mislukt: {exc}") from exc
    ctx.db.execute("UPDATE encoding_benchmarks SET status='done', result_json=? WHERE id=?",
                   (dumps(result), bid))
    audit(ctx.db, actor=principal.actor, action="encoding.benchmark", target_type="file",
          target_id=body.path, new_value={"benchmark_id": bid})
    return {"id": bid, "status": "done", "result": result}
