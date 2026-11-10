"""Optimalisatie-API: dry-run, plan, start (alleen na dry-run + bevestiging), pauze,
hervatten, export (CSV/JSON) en dashboardtellingen."""

from __future__ import annotations

from typing import Any, Literal

from fastapi import APIRouter, Depends, HTTPException, Query
from fastapi.responses import PlainTextResponse
from pydantic import BaseModel, Field

from mediaguard import jobs
from mediaguard.audit import audit
from mediaguard.auth import Principal, require_operator, require_viewer
from mediaguard.context import AppContext, get_app_context
from mediaguard.db import loads, utcnow
from mediaguard.optimization import planner, runner
from mediaguard.settings_registry import REGISTRY

router = APIRouter(prefix="/api/v1/optimization", tags=["optimization"])


def register_settings() -> None:
    REGISTRY.register(*planner.setting_defs())


@jobs.job_handler("optimize.run")
async def _run(jc: jobs.JobContext) -> dict[str, Any]:
    plan_id = int(jc.payload["plan_id"])
    result = await runner.run_batch(jc.app, plan_id)
    jc.log(f"Batch: {result}")
    if result.get("stopped"):
        return result
    if result.get("remaining"):
        if result.get("waiting"):
            raise jobs.Waiting(runner.waiting_nl(str(result["waiting"]), jc.app), 600)
        raise jobs.RetryLater("volgende batch", 5)
    return result


class Filters(BaseModel):
    instance_ids: list[str] = Field(default_factory=list)
    media_types: list[str] = Field(default_factory=list)
    series_ids: list[int] = Field(default_factory=list)
    movie_ids: list[int] = Field(default_factory=list)
    season: int | None = None


class DryRunBody(BaseModel):
    filters: Filters = Field(default_factory=Filters)
    limit: int | None = Field(default=None, ge=1, le=1_000_000)


@router.post("/dry-run")
def dry_run(body: DryRunBody, principal: Principal = Depends(require_operator),
            ctx: AppContext = Depends(get_app_context)) -> dict[str, Any]:
    plan = planner.dry_run(ctx, body.filters.model_dump(), principal.actor, body.limit)
    audit(ctx.db, actor=principal.actor, action="optimization.dry_run",
          target_type="optimization_plan", target_id=plan["id"],
          new_value={"summary": plan["summary"]})
    return plan


@router.get("/plan")
def get_plan(plan_id: int | None = None, bucket: str | None = None, offset: int = 0,
             limit: int = Query(200, le=5000), _: Principal = Depends(require_viewer),
             ctx: AppContext = Depends(get_app_context)) -> dict[str, Any]:
    plan = planner.get_plan(ctx, plan_id)
    if plan is None:
        raise HTTPException(404, "Nog geen optimalisatieplan; maak eerst een dry-run")
    plan["items"] = _with_encode_state(ctx, planner.items(
        ctx, int(plan["id"]), bucket=bucket, offset=offset, limit=limit))
    plan["limits"] = runner.limits_state(ctx)
    return plan


def _with_encode_state(ctx: AppContext, rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """AV1-items krijgen de stand van hun encode (status + encoder-job voor de wachtrij-link)."""
    for row in rows:
        result = loads(row.get("result_json"), {}) if row.get("result_json") else {}
        job_id = result.get("encoding_job_id") if isinstance(result, dict) else None
        row["encode"] = None
        if job_id is None:
            continue
        job = ctx.db.fetch_one("SELECT id,status,encoder_job_id FROM encoding_jobs WHERE id=?",
                               (int(job_id),))
        if job is not None:
            row["encode"] = {"id": int(job["id"]), "status": job["status"],
                             "encoder_job_id": job["encoder_job_id"]}
    return rows


@router.get("/plans")
def list_plans(_: Principal = Depends(require_viewer),
               ctx: AppContext = Depends(get_app_context)) -> list[dict[str, Any]]:
    rows = ctx.db.fetch_all("SELECT id FROM optimization_plans ORDER BY id DESC LIMIT 100")
    return [p for r in rows if (p := planner.get_plan(ctx, int(r["id"]))) is not None]


class RunBody(BaseModel):
    plan_id: int
    confirm: bool = False


@router.post("/run")
def run(body: RunBody, principal: Principal = Depends(require_operator),
        ctx: AppContext = Depends(get_app_context)) -> dict[str, Any]:
    plan = planner.get_plan(ctx, body.plan_id)
    if plan is None:
        raise HTTPException(404, "Plan niet gevonden")
    if not body.confirm:
        raise HTTPException(422, "Expliciete bevestiging vereist ('confirm': true) na het "
                                 "bekijken van de dry-run")
    if plan["status"] != "dry_run":
        raise HTTPException(409, f"Plan heeft status '{plan['status']}'; alleen een nieuwe "
                                 "dry-run kan gestart worden")
    if not planner.plan_valid(ctx, plan):
        ctx.db.execute("UPDATE optimization_plans SET status='expired' WHERE id=?",
                       (body.plan_id,))
        raise HTTPException(409, "Dry-run is verlopen; maak een nieuwe dry-run")
    if ctx.emergency_stopped():
        raise HTTPException(409, "Noodstop actief")
    job_id = jobs.enqueue(ctx.db, "optimize.run", {"plan_id": body.plan_id},
                          target_type="optimization_plan", target_id=body.plan_id,
                          idempotency_key=f"optimize.run:{body.plan_id}:{utcnow()}",
                          max_attempts=100000, created_by=principal.actor)
    ctx.db.execute("UPDATE optimization_plans SET status='running', run_job_id=?, "
                   "confirmed_by=?, confirmed_at=?, updated_at=? WHERE id=?",
                   (job_id, principal.actor, utcnow(), utcnow(), body.plan_id))
    audit(ctx.db, actor=principal.actor, action="optimization.run",
          target_type="optimization_plan", target_id=body.plan_id,
          new_value={"summary": plan["summary"], "forecast": plan["forecast"]},
          undo_hint="Pauzeer het plan; vervangingen zijn per item herstelbaar")
    runner_ = ctx.services.get("jobs")
    if runner_ is not None:
        runner_.wake()
    return {"plan_id": body.plan_id, "job_id": job_id, "status": "running"}


class ConfirmDownloadsBody(BaseModel):
    confirm: bool = False


@router.post("/plans/{plan_id}/confirm-downloads")
def confirm_downloads(plan_id: int, body: ConfirmDownloadsBody,
                      principal: Principal = Depends(require_operator),
                      ctx: AppContext = Depends(get_app_context)) -> dict[str, Any]:
    """Rapport 2 / N16: downloaden van betere versies (bv. 10 TB) is een apart besluit."""
    plan = planner.get_plan(ctx, plan_id)
    if plan is None:
        raise HTTPException(404, "Plan niet gevonden")
    if not body.confirm:
        raise HTTPException(422, "Bevestiging vereist ('confirm': true)")
    ctx.state_set(f"optimization_downloads_ok:{plan_id}", {"by": principal.actor,
                                                          "at": utcnow()})
    audit(ctx.db, actor=principal.actor, action="optimization.downloads_confirmed",
          target_type="optimization_plan", target_id=plan_id,
          new_value=(plan.get("display") or {}).get("downloads"))
    runner_ = ctx.services.get("jobs")
    if runner_ is not None:
        runner_.wake()
    return {"plan_id": plan_id, "downloads_confirmed": True}


@router.post("/plans/{plan_id}/pause")
def pause(plan_id: int, principal: Principal = Depends(require_operator),
          ctx: AppContext = Depends(get_app_context)) -> dict[str, Any]:
    plan = planner.get_plan(ctx, plan_id)
    if plan is None:
        raise HTTPException(404, "Plan niet gevonden")
    if plan["status"] != "running":
        raise HTTPException(409, f"Plan loopt niet (status {plan['status']})")
    ctx.db.execute("UPDATE optimization_plans SET status='paused', updated_at=? WHERE id=?",
                   (utcnow(), plan_id))
    if plan.get("run_job_id"):
        jobs.cancel(ctx.db, int(plan["run_job_id"]))
    audit(ctx.db, actor=principal.actor, action="optimization.pause",
          target_type="optimization_plan", target_id=plan_id)
    return {"plan_id": plan_id, "status": "paused"}


@router.post("/plans/{plan_id}/resume")
def resume(plan_id: int, principal: Principal = Depends(require_operator),
           ctx: AppContext = Depends(get_app_context)) -> dict[str, Any]:
    plan = planner.get_plan(ctx, plan_id)
    if plan is None:
        raise HTTPException(404, "Plan niet gevonden")
    if plan["status"] != "paused":
        raise HTTPException(409, f"Plan is niet gepauzeerd (status {plan['status']})")
    if ctx.emergency_stopped():
        raise HTTPException(409, "Noodstop actief")
    job_id = jobs.enqueue(ctx.db, "optimize.run", {"plan_id": plan_id},
                          idempotency_key=f"optimize.run:{plan_id}:{utcnow()}",
                          max_attempts=100000, created_by=principal.actor)
    ctx.db.execute("UPDATE optimization_plans SET status='running', run_job_id=?, "
                   "updated_at=? WHERE id=?", (job_id, utcnow(), plan_id))
    audit(ctx.db, actor=principal.actor, action="optimization.resume",
          target_type="optimization_plan", target_id=plan_id)
    return {"plan_id": plan_id, "status": "running", "job_id": job_id}


@router.post("/plans/{plan_id}/cancel")
def cancel(plan_id: int, principal: Principal = Depends(require_operator),
           ctx: AppContext = Depends(get_app_context)) -> dict[str, Any]:
    plan = planner.get_plan(ctx, plan_id)
    if plan is None:
        raise HTTPException(404, "Plan niet gevonden")
    if plan.get("run_job_id"):
        jobs.cancel(ctx.db, int(plan["run_job_id"]))
    ctx.db.execute("UPDATE optimization_plans SET status='cancelled', updated_at=? WHERE id=?",
                   (utcnow(), plan_id))
    audit(ctx.db, actor=principal.actor, action="optimization.cancel",
          target_type="optimization_plan", target_id=plan_id)
    return {"plan_id": plan_id, "status": "cancelled"}


@router.get("/plans/{plan_id}/export")
def export(plan_id: int, format: Literal["csv", "json"] = "csv",  # noqa: A002
           _: Principal = Depends(require_viewer),
           ctx: AppContext = Depends(get_app_context)) -> PlainTextResponse:
    if planner.get_plan(ctx, plan_id) is None:
        raise HTTPException(404, "Plan niet gevonden")
    body = planner.export(ctx, plan_id, format)
    media = "text/csv" if format == "csv" else "application/json"
    return PlainTextResponse(body, media_type=media, headers={
        "Content-Disposition": f'attachment; filename="optimalisatie-{plan_id}.{format}"'})


@router.get("/dashboard")
def dashboard(_: Principal = Depends(require_viewer),
              ctx: AppContext = Depends(get_app_context)) -> dict[str, Any]:
    plan = planner.get_plan(ctx)
    # alleen het laatste plan (index status+plan_id): oude plannen hebben elk ~100k rijen
    failed = ctx.db.fetch_dicts(
        "SELECT id, title, action, result_json FROM optimization_items WHERE plan_id=? AND "
        "status IN ('failed','blocked') ORDER BY id DESC LIMIT 50",
        (int((plan or {}).get("id") or 0),))
    return {"latest_plan": plan, "limits": runner.limits_state(ctx), "failed": failed,
            "in_progress": (plan or {}).get("progress", {})}
