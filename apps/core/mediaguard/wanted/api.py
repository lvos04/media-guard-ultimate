"""Wanted-, diagnose- en backfill-API (D2/D3/D4, R4)."""

from __future__ import annotations

import asyncio
import contextlib
import logging
import time
from typing import Any, Literal

from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel, Field

from mediaguard import jobs, taskwatch
from mediaguard.audit import audit
from mediaguard.auth import Principal, require_operator, require_viewer
from mediaguard.context import AppContext, get_app_context
from mediaguard.db import loads
from mediaguard.instances.deps import arr_call
from mediaguard.instances.registry import list_instances
from mediaguard.settings_registry import REGISTRY, SettingDef
from mediaguard.wanted import backfill, engine
from mediaguard.wanted.diagnosis import REASONS, diagnose, latest_diagnosis

LOGGER = logging.getLogger(__name__)
router = APIRouter(prefix="/api/v1/wanted", tags=["wanted"])
CAT = "Ontbrekende media"
BF = ("global", "media_type", "instance")


def register_settings() -> None:
    from mediaguard.wanted import stuck

    REGISTRY.register(*stuck.setting_defs())
    REGISTRY.register(
        SettingDef("backfill.enabled", "bool", True, CAT, "Backfill ingeschakeld",
                   "Backfill enabled", scopes=BF,
                   help_nl="Zoekt periodiek in kleine batches naar al ontbrekende afleveringen/"
                           "films. RSS van Sonarr/Radarr blijft altijd actief."),
        SettingDef("backfill.interval_hours", "int", 12, CAT, "Backfill-interval",
                   "Backfill interval", minimum=6, maximum=24, unit="uur", scopes=BF,
                   help_nl="Elke 6–24 uur een beperkte zoekronde per instantie."),
        SettingDef("backfill.max_searches_per_hour", "int", 20, CAT,
                   "Max. zoekopdrachten per uur", "Max searches per hour", minimum=0,
                   maximum=500, scopes=BF,
                   help_nl="Per instantie. Telt backfill, 'Zoek nu' en interactieve diagnoses."),
        SettingDef("backfill.max_searches_per_day", "int", 150, CAT,
                   "Max. zoekopdrachten per dag", "Max searches per day", minimum=0,
                   maximum=5000, scopes=BF,
                   help_nl="Per instantie, voortschrijdend venster van 24 uur."),
        SettingDef("backfill.batch_size", "int", 10, CAT, "Batchgrootte", "Batch size",
                   minimum=1, maximum=100, scopes=BF,
                   help_nl="Maximaal aantal items per backfillronde."),
        SettingDef("backfill.base_backoff_hours", "int", 24, CAT, "Basis-wachttijd na misser",
                   "Base backoff", minimum=1, maximum=720, unit="uur", scopes=BF,
                   help_nl="Wachttijd na de eerste vergeefse zoekpoging; verdubbelt per "
                           "volgende poging (exponential backoff)."),
        SettingDef("backfill.max_backoff_days", "int", 30, CAT, "Maximale wachttijd",
                   "Max backoff", minimum=1, maximum=365, unit="dagen", scopes=BF),
        SettingDef("backfill.unavailable_after_attempts", "int", 5, CAT,
                   "'Niet beschikbaar' na pogingen", "Unavailable after attempts",
                   minimum=1, maximum=50, scopes=BF,
                   help_nl="Na zoveel vergeefse pogingen krijgt een item de status 'niet "
                           "beschikbaar' en wordt het pas veel later opnieuw gezocht."),
        SettingDef("backfill.unavailable_retry_days", "int", 30, CAT,
                   "Nieuwe poging na 'niet beschikbaar'", "Retry unavailable after",
                   minimum=1, maximum=365, unit="dagen", scopes=BF),
        SettingDef("backfill.include_cutoff_unmet", "bool", True, CAT,
                   "Ook upgrades (onder cutoff) zoeken", "Include cutoff unmet", scopes=BF),
        SettingDef("backfill.priority_recent_days", "int", 30, CAT,
                   "Recente afleveringen voorrang (dagen)", "Recent priority window",
                   minimum=1, maximum=365, unit="dagen", scopes=BF),
        SettingDef("backfill.season_search_threshold", "int", 4, CAT,
                   "Seizoenszoekactie vanaf", "Season search threshold", minimum=2,
                   maximum=100, scopes=BF, advanced=True,
                   help_nl="Ontbreekt een volledig seizoen met minstens zoveel afleveringen, "
                           "dan één SeasonSearch (seizoenspakket) i.p.v. losse zoekacties."),
        SettingDef("backfill.command_wait_seconds", "int", 120, CAT,
                   "Wachttijd op zoekresultaat", "Command wait", minimum=0, maximum=1800,
                   unit="s", scopes=BF, advanced=True),
        SettingDef("backfill.collect_candidates", "enum", "on_unavailable", CAT,
                   "Kandidaten en afwijzingen bewaren", "Collect candidates",
                   choices=("never", "on_unavailable", "always"),
                   choice_labels_nl={"never": "Nooit", "on_unavailable":
                                     "Als item 'niet beschikbaar' wordt",
                                     "always": "Na elke vergeefse poging"}, scopes=BF,
                   help_nl="Voert een interactieve zoekactie uit om afwijzingsredenen vast te "
                           "leggen (telt mee in het quotum)."),
        SettingDef("backfill.priority", "enum", "normal", CAT, "Backfill-prioriteit",
                   "Backfill priority", choices=("low", "normal", "high"),
                   choice_labels_nl={"low": "Laag", "normal": "Normaal", "high": "Hoog "
                                     "(favoriet)"},
                   scopes=("global", "instance", "series", "movie")),
        SettingDef("backfill.excluded", "bool", False, CAT, "Uitsluiten van backfill",
                   "Exclude from backfill",
                   scopes=("instance", "series", "movie", "season", "episode"),
                   help_nl="Bewuste uitzondering: wordt niet automatisch gezocht en als "
                           "'bewuste uitzondering' gediagnosticeerd."),
        SettingDef("wanted.relaxed_allowed_categories", "list",
                   ["quality", "custom-format", "size"], CAT,
                   "Toegestane versoepelingen", "Allowed relaxations",
                   choices=("quality", "custom-format", "size", "language"),
                   choice_labels_nl={"quality": "Kwaliteit", "custom-format":
                                     "Custom format-score", "size": "Grootte",
                                     "language": "Taal"},
                   scopes=BF,
                   help_nl="Welke regels de gebruiker per item expliciet mag versoepelen via "
                           "'Opnieuw met versoepelde regels'. Nooit automatisch."),
    )


@jobs.job_handler("backfill.run")
async def _job_backfill(jc: jobs.JobContext) -> dict[str, Any]:
    if not jc.instance_id:
        raise ValueError("backfill.run vereist instance_id")
    return await backfill.run_backfill(jc.app, jc.instance_id,
                                       trigger=jc.payload.get("trigger", "scheduler"),
                                       actor="system:backfill")


@jobs.job_handler("imports.stuck_check")
async def _job_stuck(jc: jobs.JobContext) -> dict[str, Any]:
    from mediaguard.wanted import stuck

    if not jc.instance_id:
        raise ValueError("imports.stuck_check vereist instance_id")
    return await stuck.check_instance(jc.app, jc.instance_id)


DIAGNOSE_BATCH = 40


@jobs.job_handler("wanted.diagnose_background")
async def _job_diagnose(jc: jobs.JobContext) -> dict[str, Any]:
    """Nog niet onderzochte ontbrekende items automatisch diagnosticeren (zonder
    interactieve zoekactie, dus zonder indexerquotum). 'Nog niet onderzocht' is nooit een
    taak voor de gebruiker (rapport 4 nov, H6)."""
    from mediaguard.wanted.diagnosis import diagnose

    if not jc.instance_id:
        raise ValueError("wanted.diagnose_background vereist instance_id")
    rows = jc.app.db.fetch_all(
        "SELECT id FROM wanted_items WHERE instance_id=? AND last_reason_code IS NULL AND "
        "status IN ('wanted','searching','unavailable') ORDER BY CASE reason WHEN 'missing' "
        "THEN 0 ELSE 1 END, priority DESC, id LIMIT ?", (jc.instance_id, DIAGNOSE_BATCH))
    done = 0
    for r in rows:
        jc.check_cancelled()
        try:
            await diagnose(jc.app, int(r["id"]), interactive=False, trigger="background")
            done += 1
        except Exception as exc:  # noqa: BLE001 - één item mag de rest niet tegenhouden
            LOGGER.info("achtergronddiagnose %s mislukt: %s", r["id"], exc)
    return {"diagnosed": done, "batch": len(rows)}


def _due_diagnose(ctx: AppContext) -> None:
    bucket = int(time.time() // 900)  # elk kwartier een portie
    for inst in list_instances(ctx):
        if inst.suspended:
            continue
        open_row = ctx.db.fetch_one(
            "SELECT 1 FROM wanted_items WHERE instance_id=? AND last_reason_code IS NULL AND "
            "status IN ('wanted','searching','unavailable') LIMIT 1", (inst.id,))
        if open_row is None:
            continue
        jobs.enqueue(ctx.db, "wanted.diagnose_background", {}, instance_id=inst.id,
                     max_attempts=1, idempotency_key=f"wanted.diagnose:{inst.id}:{bucket}",
                     created_by="system:scheduler", priority=180)


def _due_stuck(ctx: AppContext) -> None:
    bucket = int(time.time() // 1800)  # elk half uur
    for inst in list_instances(ctx):
        if inst.suspended:
            continue
        jobs.enqueue(ctx.db, "imports.stuck_check", {}, instance_id=inst.id, max_attempts=1,
                     idempotency_key=f"imports.stuck:{inst.id}:{bucket}",
                     created_by="system:scheduler", priority=150)


def _due_backfill(ctx: AppContext) -> list[int]:
    queued = []
    for inst in list_instances(ctx):
        if inst.suspended or not backfill.cfg(ctx, "backfill.enabled", inst.id):
            continue
        hours = int(backfill.cfg(ctx, "backfill.interval_hours", inst.id))
        bucket = int(time.time() // (hours * 3600))
        queued.append(jobs.enqueue(ctx.db, "backfill.run", {"trigger": "scheduler"},
                                   instance_id=inst.id, max_attempts=1,
                                   idempotency_key=f"backfill:{inst.id}:{hours}:{bucket}",
                                   created_by="system:scheduler"))
    return queued


async def _scheduler(ctx: AppContext) -> None:
    await asyncio.sleep(30)
    while True:
        try:
            _due_backfill(ctx)
            _due_stuck(ctx)
            _due_diagnose(ctx)
            runner = ctx.services.get("jobs")
            if runner is not None:
                runner.wake()
            taskwatch.ok(ctx, "wanted_scheduler")
        except Exception as exc:  # noqa: BLE001
            LOGGER.exception("backfill-planner")
            taskwatch.fail(ctx, "wanted_scheduler", exc)
        await asyncio.sleep(60)


async def startup(ctx: AppContext) -> None:
    if ctx.settings.start_background and ctx.settings.role in ("core", "all"):
        ctx.services["wanted.scheduler"] = taskwatch.spawn(
            ctx, "wanted_scheduler", _scheduler, label_nl="Ontbrekende media zoeken (planner)",
            every=60)


async def shutdown(ctx: AppContext) -> None:
    task = ctx.services.pop("wanted.scheduler", None)
    if task is not None:
        task.cancel()
        with contextlib.suppress(asyncio.CancelledError, Exception):
            await task


# -- statische routes (vóór /{wanted_id}) -------------------------------------------------------
@router.get("")
def wanted_list(instance_id: str | None = None, media_type: str | None = None,
                kind: Literal["episode", "movie"] | None = None,
                reason: Literal["missing", "cutoff"] | None = None,
                status: str | None = "open", q: str | None = None,
                season: int | None = None, series_id: int | None = None,
                reason_code: str | None = None, searched_before: str | None = None,
                searched_after: str | None = None, never_searched: bool | None = None,
                page: int = Query(1, ge=1), page_size: int = Query(50, ge=1, le=500),
                sort: str = "priority", _: Principal = Depends(require_viewer),
                ctx: AppContext = Depends(get_app_context)) -> dict[str, Any]:
    return engine.list_wanted(
        ctx, instance_id=instance_id, media_type=media_type, kind=kind, reason=reason,
        status=status or None, q=q, season=season, series_id=series_id,
        reason_code=reason_code, searched_before=searched_before,
        searched_after=searched_after, never_searched=never_searched, page=page,
        page_size=page_size, sort=sort)


@router.get("/kpis")
def kpis(instance_id: str | None = None, _: Principal = Depends(require_viewer),
         ctx: AppContext = Depends(get_app_context)) -> dict[str, Any]:
    data = engine.wanted_kpis(ctx, instance_id)
    data["per_instance"] = {i.id: engine.wanted_kpis(ctx, i.id) for i in list_instances(ctx)
                            if instance_id in (None, i.id)}
    return data


@router.get("/reasons")
def reasons(_: Principal = Depends(require_viewer)) -> dict[str, Any]:
    """Alle oorzaakcodes met Nederlandse label en standaardfix (voor filters/legenda)."""
    return REASONS


class PlanBody(BaseModel):
    instance_id: str | None = None


@router.post("/backfill-plan")
def backfill_plan(body: PlanBody | None = None, _: Principal = Depends(require_viewer),
                  ctx: AppContext = Depends(get_app_context)) -> dict[str, Any]:
    """Dry-run: wat de volgende ronde per instantie zou zoeken, met prioriteiten en quota.
    Leest de huidige wanted-lijst; herberekent die alleen als de bibliotheek veranderde."""
    target = body.instance_id if body else None
    plans = []
    for inst in list_instances(ctx):
        if target and inst.id != target:
            continue
        engine.refresh_wanted_if_changed(ctx, inst.id)
        p = backfill.plan(ctx, inst.id)
        p["suspended"] = inst.suspended
        plans.append(p)
    if target and not plans:
        raise HTTPException(404, "Onbekende instantie")
    return {"dry_run": True, "plans": plans}


@router.get("/backfill/runs")
def backfill_runs(instance_id: str | None = None, limit: int = Query(50, le=500),
                  _: Principal = Depends(require_viewer),
                  ctx: AppContext = Depends(get_app_context)) -> list[dict[str, Any]]:
    sql = "SELECT * FROM backfill_runs"
    params: list[Any] = []
    if instance_id:
        sql += " WHERE instance_id=?"
        params.append(instance_id)
    rows = ctx.db.fetch_dicts(sql + " ORDER BY id DESC LIMIT ?", [*params, limit])
    for r in rows:
        r["quota"] = loads(r.pop("quota_json"), {})
    return rows


@router.get("/backfill/quota")
def quota(_: Principal = Depends(require_viewer),
          ctx: AppContext = Depends(get_app_context)) -> dict[str, Any]:
    return {i.id: backfill.quota_status(ctx, i.id) for i in list_instances(ctx)}


class BulkAction(BaseModel):
    action: Literal["search", "ignore", "unignore", "keep_monitoring"]
    wanted_ids: list[int] = Field(default_factory=list, max_length=1000)
    instance_id: str | None = None
    series_id: int | None = None
    season_number: int | None = None
    reason: str = Field(default="", max_length=500)


@router.post("/actions")
async def bulk_action(body: BulkAction, principal: Principal = Depends(require_operator),
                      ctx: AppContext = Depends(get_app_context)) -> dict[str, Any]:
    """Acties per aflevering of per seizoen (instance_id + series_id + season_number)."""
    ids = list(body.wanted_ids)
    if not ids and body.instance_id and body.series_id is not None:
        sql = ("SELECT id FROM wanted_items WHERE instance_id=? AND item_provider_id=? AND "
               "status!='resolved'")
        params: list[Any] = [body.instance_id, body.series_id]
        if body.season_number is not None:
            sql += " AND season_number=?"
            params.append(body.season_number)
        ids = [int(r["id"]) for r in ctx.db.fetch_all(sql, params)]
    if not ids:
        raise HTTPException(422, "Geen items geselecteerd")
    if body.action == "search":
        try:
            return await arr_call(backfill.search_now(ctx, ids, principal.actor))
        except backfill.QuotaExceeded as exc:
            raise HTTPException(429, {"message": str(exc), "quota": exc.quota}) from exc
        except KeyError as exc:
            raise HTTPException(404, str(exc)) from exc
    results = []
    for wid in ids:
        try:
            results.append(engine.apply_action(ctx, wid, body.action, body.reason))
        except KeyError:
            continue
    first = engine.get_wanted(ctx, ids[0])
    audit(ctx.db, actor=principal.actor, action=f"wanted.{body.action}", target_type="wanted",
          target_id=",".join(map(str, ids[:100])),
          instance_id=first["instance_id"] if first else body.instance_id, reason=body.reason,
          new_value={"count": len(results)})
    return {"updated": len(results), "results": results,
            "explanation_nl": engine.ACTION_EXPLANATIONS[body.action]}


# -- per item ----------------------------------------------------------------------------------
@router.get("/{wanted_id}")
def wanted_item(wanted_id: int, _: Principal = Depends(require_viewer),
                ctx: AppContext = Depends(get_app_context)) -> dict[str, Any]:
    item = engine.wanted_detail(ctx, wanted_id)
    if item is None:
        raise HTTPException(404, "Item niet gevonden")
    item["latest_diagnosis"] = latest_diagnosis(ctx, wanted_id)
    return item


@router.get("/{wanted_id}/diagnosis")
async def diagnosis(wanted_id: int, interactive: bool = False, cached: bool = False,
                    principal: Principal = Depends(require_viewer),
                    ctx: AppContext = Depends(get_app_context)) -> dict[str, Any]:
    """Concrete oorzaak. ``interactive=true`` bevraagt de indexers (operator; telt mee in
    het quotum); ``cached=true`` geeft de laatst opgeslagen diagnose zonder live-aanroepen."""
    row = engine.get_wanted(ctx, wanted_id)
    if row is None:
        raise HTTPException(404, "Item niet gevonden")
    if cached:
        latest = latest_diagnosis(ctx, wanted_id)
        if latest is None:
            raise HTTPException(404, "Nog geen diagnose opgeslagen")
        return latest
    if interactive:
        if principal.role not in ("operator", "admin"):
            raise HTTPException(403, "Interactieve diagnose vereist de rol 'operator'")
        q = backfill.quota_status(ctx, row["instance_id"])
        if q["remaining"] <= 0:
            raise HTTPException(429, {"message": "Zoekquotum op; interactieve diagnose later "
                                                 "opnieuw of gebruik de diagnose zonder "
                                                 "zoekactie.", "quota": q})
    return await arr_call(diagnose(ctx, wanted_id, interactive=interactive,
                                   trigger="diagnosis"))


class SearchBody(BaseModel):
    wait_seconds: float = Field(default=0, ge=0, le=300)


@router.post("/{wanted_id}/search")
async def search_now(wanted_id: int, body: SearchBody | None = None,
                     principal: Principal = Depends(require_operator),
                     ctx: AppContext = Depends(get_app_context)) -> dict[str, Any]:
    """'Zoek nu' — telt mee in het instantiequotum, omzeilt het nooit."""
    try:
        result = await arr_call(backfill.search_now(ctx, [wanted_id], principal.actor,
                                                    wait_seconds=(body.wait_seconds
                                                                  if body else 0)))
    except backfill.QuotaExceeded as exc:
        raise HTTPException(429, {"message": str(exc), "quota": exc.quota}) from exc
    except KeyError as exc:
        raise HTTPException(404, "Item niet gevonden") from exc
    item = engine.get_wanted(ctx, wanted_id)
    return {**result["results"][0], "item": item}


class RelaxedBody(BaseModel):
    relaxations: list[Literal["quality", "custom-format", "size", "language"]] = Field(
        min_length=1)
    confirm: bool = False
    reason: str = Field(default="", max_length=500)


@router.post("/{wanted_id}/retry-relaxed")
async def retry_relaxed(wanted_id: int, body: RelaxedBody,
                        principal: Principal = Depends(require_operator),
                        ctx: AppContext = Depends(get_app_context)) -> dict[str, Any]:
    """Expliciete, eenmalige versoepeling (override-record + audit). Zonder ``confirm``
    alleen uitleg van de gevolgen."""
    try:
        return await arr_call(backfill.retry_relaxed(ctx, wanted_id, list(body.relaxations),
                                                     principal.actor, confirm=body.confirm,
                                                     reason=body.reason))
    except backfill.QuotaExceeded as exc:
        raise HTTPException(429, {"message": str(exc), "quota": exc.quota}) from exc
    except KeyError as exc:
        raise HTTPException(404, "Item niet gevonden") from exc


class ItemAction(BaseModel):
    reason: str = Field(default="", max_length=500)


def _single(ctx: AppContext, principal: Principal, wanted_id: int, action: str,
            reason: str) -> dict[str, Any]:
    try:
        result = engine.apply_action(ctx, wanted_id, action, reason)
    except KeyError as exc:
        raise HTTPException(404, "Item niet gevonden") from exc
    row = engine.get_wanted(ctx, wanted_id) or {}
    audit(ctx.db, actor=principal.actor, action=f"wanted.{action}", target_type="wanted",
          target_id=wanted_id, instance_id=row.get("instance_id"), reason=reason,
          old_value={"status": result["old_status"]}, new_value={"status": result["status"]})
    return result


@router.post("/{wanted_id}/ignore")
def ignore(wanted_id: int, body: ItemAction | None = None,
           principal: Principal = Depends(require_operator),
           ctx: AppContext = Depends(get_app_context)) -> dict[str, Any]:
    return _single(ctx, principal, wanted_id, "ignore", body.reason if body else "")


@router.post("/{wanted_id}/unignore")
def unignore(wanted_id: int, principal: Principal = Depends(require_operator),
             ctx: AppContext = Depends(get_app_context)) -> dict[str, Any]:
    return _single(ctx, principal, wanted_id, "unignore", "")


@router.post("/{wanted_id}/keep-monitoring")
def keep_monitoring(wanted_id: int, body: ItemAction | None = None,
                    principal: Principal = Depends(require_operator),
                    ctx: AppContext = Depends(get_app_context)) -> dict[str, Any]:
    return _single(ctx, principal, wanted_id, "keep_monitoring", body.reason if body else "")
