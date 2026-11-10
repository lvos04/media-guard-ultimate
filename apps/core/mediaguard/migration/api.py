"""/api/v1/migration — migratiewizard (J1 + R5 routes), bronbeheer, AV1-import.

RBAC: viewer = lezen; operator = previews, vergelijken, scans; admin = bronnen met
sleutels, back-up maken op de bron, restore/schaduwimport, cutover, rollback.
Lange stappen draaien als jobs (``migration.<stap>``) met voortgang en logs; ``wait=true``
voert ze direct uit (handig voor kleine installaties en tests). De status staat volledig
in de database en overleeft refresh/herstart (GET /migration/runs/{id}).
"""

from __future__ import annotations

import os
import uuid
from pathlib import Path
from typing import Any, Literal

from fastapi import APIRouter, Depends, File, Form, HTTPException, UploadFile
from pydantic import BaseModel, Field

from mediaguard import jobs
from mediaguard.arr.client import ArrError
from mediaguard.audit import audit
from mediaguard.auth import ROLE_RANK, Principal, require_admin, require_operator, require_viewer
from mediaguard.context import AppContext, get_app_context
from mediaguard.db import dumps, loads, utcnow
from mediaguard.instances.registry import arr_client, validate_url
from mediaguard.migration import cutover as co
from mediaguard.migration import store, wizard
from mediaguard.migration.av1 import import_av1_db
from mediaguard.migration.backupfile import BackupInvalid
from mediaguard.migration.sources import (
    apikey_secret,
    detect,
    list_sources,
    register_live,
    retest,
    source_client,
    source_row,
    source_view,
)
from mediaguard.settings_registry import REGISTRY, SettingDef

router = APIRouter(prefix="/api/v1/migration", tags=["migration"])


def register_settings() -> None:
    cat = "Migratie"
    REGISTRY.register(
        SettingDef("migration.max_backup_mb", "int", 4096, cat, "Maximale back-upgrootte",
                   "Maximum backup size", help_nl="Grootste Sonarr/Radarr-back-up (.zip) die "
                   "geüpload of gedownload mag worden.", minimum=1, maximum=65536, unit="MB"),
        SettingDef("migration.command_wait_seconds", "int", 300, cat,
                   "Wachttijd Sonarr/Radarr-commando's", "Command wait",
                   help_nl="Hoe lang de wizard wacht op Backup/Rescan-commando's.",
                   minimum=0, maximum=7200, unit="s", advanced=True),
        SettingDef("migration.restart_wait_seconds", "int", 300, cat,
                   "Wachttijd herstart na restore", "Restart wait", minimum=0, maximum=3600,
                   unit="s", advanced=True),
        SettingDef("migration.episode_wait_seconds", "int", 120, cat,
                   "Wachttijd afleveringsmetadata", "Episode metadata wait",
                   help_nl="Hoe lang de API-import wacht tot een nieuw toegevoegde serie haar "
                           "afleveringen heeft opgehaald.", minimum=0, maximum=3600, unit="s",
                   advanced=True),
        SettingDef("migration.path_sample_size", "int", 50, cat, "Steekproef padcontrole",
                   "Path check sample", help_nl="Aantal series/films met bestanden waarvan het "
                   "(herschreven) pad op de mount wordt gecontroleerd.", minimum=1,
                   maximum=100000),
    )


# -- jobs -------------------------------------------------------------------------------------
async def _execute_step(ctx: AppContext, run_id: int, step: str, scope: str,
                        params: dict[str, Any], actor: str, log: Any,
                        job_id: int | None = None) -> dict[str, Any]:
    fn = wizard.STEP_FUNCS[step]
    store.step_set(ctx, run_id, step, scope, "running", job_id=job_id)
    instance_id = scope if scope and not scope.startswith("source:") else None
    try:
        status, result = await fn(ctx, run_id, scope, params, actor, log)
    except wizard.StepBlocked as exc:
        store.step_set(ctx, run_id, step, scope, "blocked", error=str(exc))
        audit(ctx.db, actor=actor, action=f"migration.{step}.blocked", target_type="migration",
              target_id=run_id, instance_id=instance_id, reason=str(exc))
        return {"status": "blocked", "error": str(exc)}
    except jobs.RetryLater:
        store.step_set(ctx, run_id, step, scope, "queued")
        raise
    except (ArrError, BackupInvalid, ValueError) as exc:
        msg = exc.message if isinstance(exc, ArrError) else str(exc)
        store.step_set(ctx, run_id, step, scope, "failed", error=msg)
        audit(ctx.db, actor=actor, action=f"migration.{step}.failed", target_type="migration",
              target_id=run_id, instance_id=instance_id, reason=msg)
        return {"status": "failed", "error": msg}
    except Exception as exc:
        store.step_set(ctx, run_id, step, scope, "failed", error=f"{type(exc).__name__}: {exc}")
        raise
    store.step_set(ctx, run_id, step, scope, status, result=result)
    audit(ctx.db, actor=actor, action=f"migration.{step}", target_type="migration",
          target_id=run_id, instance_id=instance_id, new_value={"status": status,
                                                                "scope": scope})
    return {"status": status, "result": result}


def _make_handler(step: str) -> Any:
    async def handler(jc: jobs.JobContext) -> dict[str, Any]:
        p = jc.payload
        jc.progress(0.05)
        out = await _execute_step(jc.app, int(p["run_id"]), step, p.get("scope", ""),
                                  p.get("params") or {}, p.get("actor", "system"), jc.log,
                                  jc.job_id)
        jc.progress(1.0)
        return out
    return handler


for _step in wizard.STEP_FUNCS:
    jobs.job_handler(f"migration.{_step}")(_make_handler(_step))


@jobs.job_handler("migration.autopilot")
async def _autopilot_job(jc: jobs.JobContext) -> dict[str, Any]:
    from mediaguard.migration import autopilot

    out = await autopilot.advance(jc.app, int(jc.payload["run_id"]),
                                  jc.payload.get("actor", "system"), jc.log)
    return {"state": out.get("state"), "needs": out.get("needs")}


@jobs.job_handler("migration.cutover")
async def _cutover_job(jc: jobs.JobContext) -> dict[str, Any]:
    p = jc.payload
    done = []
    for i, cid in enumerate(p["cutover_ids"]):
        jc.check_cancelled()
        row = co.cutover_row(jc.app, int(cid))
        if row["status"] != "running":
            continue
        result = await co.execute(jc.app, int(cid), p.get("params") or {}, jc.log)
        done.append({"cutover_id": cid, "status": result["status"]})
        jc.progress((i + 1) / len(p["cutover_ids"]))
    return {"cutovers": done}


@jobs.job_handler("migration.rollback")
async def _rollback_job(jc: jobs.JobContext) -> dict[str, Any]:
    p = jc.payload
    res = await co.rollback(jc.app, int(p["run_id"]), p["target"], p["actor"], jc.log)
    return {"status": res["status"]}


def _active_job(ctx: AppContext, job_id: int | None) -> int | None:
    if not job_id:
        return None
    row = ctx.db.fetch_one("SELECT status FROM jobs WHERE id=?", (job_id,))
    if row and row["status"] in ("queued", "running", "retry_scheduled"):
        return job_id
    return None


async def _launch(ctx: AppContext, run_id: int, step: str, scope: str, params: dict[str, Any],
                  principal: Principal, wait: bool) -> dict[str, Any]:
    _require_run(ctx, run_id)
    current = store.step_get(ctx, run_id, step, scope)
    active = _active_job(ctx, current["job_id"] if current else None)
    if active:
        return {"queued": True, "job_id": active, "already_running": True}
    if wait:
        logs: list[str] = []
        try:
            out = await _execute_step(ctx, run_id, step, scope, params, principal.actor,
                                      logs.append)
        except jobs.RetryLater as exc:
            return {"status": "queued", "retry": str(exc), "log": logs,
                    "message_nl": "Nog niet klaar; voer de stap later opnieuw uit (hervat)."}
        return {**out, "log": logs}
    instance_id = scope if scope and not scope.startswith("source:") else None
    job_id = jobs.enqueue(ctx.db, f"migration.{step}", {
        "run_id": run_id, "scope": scope, "params": params, "actor": principal.actor},
        instance_id=instance_id, target_type="migration_run", target_id=run_id,
        created_by=principal.actor, max_attempts=5 if step in ("backup", "shadow_import") else 1)
    store.step_set(ctx, run_id, step, scope, "queued", job_id=job_id)
    runner = ctx.services.get("jobs")
    if runner is not None:
        runner.wake()
    return {"queued": True, "job_id": job_id}


def _require_run(ctx: AppContext, run_id: int) -> dict[str, Any]:
    row = store.run_row(ctx, run_id)
    if row is None:
        raise HTTPException(404, f"Migratierun {run_id} bestaat niet")
    if row["status"] != "active":
        raise HTTPException(409, f"Migratierun {run_id} is {row['status']}")
    return row


def _targets(ctx: AppContext, run_id: int, target: str | None) -> list[str]:
    targets = [m["target_instance_id"] for m in store.mappings(ctx, run_id, confirmed_only=True)]
    if target:
        if target not in targets:
            raise HTTPException(409, f"'{target}' heeft geen bevestigde mapping in deze run")
        return [target]
    if not targets:
        raise HTTPException(409, "Bevestig eerst de mapping bron → doel")
    return targets


def _blocked(exc: wizard.StepBlocked) -> HTTPException:
    return HTTPException(409, str(exc))


# -- bronnen -------------------------------------------------------------------------------
class NewSource(BaseModel):
    name: str = Field(min_length=1, max_length=80)
    url: str
    api_key: str = Field(min_length=8, max_length=200)
    id: str | None = None


@router.get("/sources")
def sources(include_archived: bool = False, _: Principal = Depends(require_viewer),
            ctx: AppContext = Depends(get_app_context)) -> list[dict[str, Any]]:
    return list_sources(ctx, include_archived)


@router.post("/sources")
async def source_create(body: NewSource, principal: Principal = Depends(require_admin),
                        ctx: AppContext = Depends(get_app_context)) -> dict[str, Any]:
    try:
        view = await register_live(ctx, name=body.name, url=body.url, api_key=body.api_key,
                                   actor=principal.actor, source_id=body.id)
    except ArrError as exc:
        raise HTTPException(exc.http_status, exc.to_dict()) from exc
    audit(ctx.db, actor=principal.actor, action="migration.source.create",
          target_type="source", target_id=view["id"],
          new_value={k: view[k] for k in ("app", "url", "version")})
    return view


async def _save_upload(ctx: AppContext, file: UploadFile) -> Path:
    limit = wizard.max_backup_bytes(ctx)
    incoming = wizard.backups_root(ctx) / "_incoming"
    incoming.mkdir(parents=True, exist_ok=True)
    tmp = incoming / f"upload-{uuid.uuid4().hex}.zip"
    size = 0
    fd = os.open(tmp, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    with os.fdopen(fd, "wb") as out:
        while chunk := await file.read(1024 * 1024):
            size += len(chunk)
            if size > limit:
                out.close()
                tmp.unlink(missing_ok=True)
                raise HTTPException(413, f"Bestand groter dan {limit // (1024 * 1024)} MB "
                                         "(instelling migration.max_backup_mb)")
            out.write(chunk)
    return tmp


@router.post("/sources/upload")
async def source_upload(file: UploadFile = File(...), name: str | None = Form(None),
                        principal: Principal = Depends(require_admin),
                        ctx: AppContext = Depends(get_app_context)) -> dict[str, Any]:
    tmp = await _save_upload(ctx, file)
    try:
        view = wizard.register_backup_upload(ctx, tmp, file.filename or "backup.zip", name,
                                             principal.actor)
    except BackupInvalid as exc:
        raise HTTPException(422, f"Geen bruikbare Sonarr/Radarr-back-up: {exc}") from exc
    finally:
        tmp.unlink(missing_ok=True)
    audit(ctx.db, actor=principal.actor, action="migration.source.upload",
          target_type="source", target_id=view["id"],
          new_value={"app": view["app"], "version": view["version"],
                     "sha256": (view["latest_backup"] or {}).get("sha256")})
    return view


@router.post("/sources/{source_id}/backups/upload")
async def source_backup_upload(source_id: str, file: UploadFile = File(...),
                               principal: Principal = Depends(require_admin),
                               ctx: AppContext = Depends(get_app_context)) -> dict[str, Any]:
    row = source_row(ctx, source_id)
    if row is None:
        raise HTTPException(404, "Onbekende bron")
    tmp = await _save_upload(ctx, file)
    try:
        info = wizard.inspect_backup(tmp, wizard.max_backup_bytes(ctx) * 4)
        if info["app"] != row["app"]:
            raise BackupInvalid(f"Back-up is van {info['app']}, bron is {row['app']}")
        backup = wizard._store_backup(ctx, source_id, tmp, file.filename or "backup.zip",
                                      "upload", principal.actor)
    except BackupInvalid as exc:
        raise HTTPException(422, f"Geen bruikbare back-up: {exc}") from exc
    finally:
        tmp.unlink(missing_ok=True)
    audit(ctx.db, actor=principal.actor, action="migration.backup.upload", target_type="source",
          target_id=source_id, new_value={"sha256": backup["sha256"]})
    return {k: backup[k] for k in ("id", "sha256", "size", "backup_name", "verified")}


class PatchSource(BaseModel):
    name: str | None = None
    url: str | None = None
    api_key: str | None = Field(None, min_length=8, max_length=200)


@router.patch("/sources/{source_id}")
async def source_patch(source_id: str, body: PatchSource,
                       principal: Principal = Depends(require_admin),
                       ctx: AppContext = Depends(get_app_context)) -> dict[str, Any]:
    row = source_row(ctx, source_id)
    if row is None:
        raise HTTPException(404, "Onbekende bron")
    if (body.url or body.api_key) and row["kind"] != "live":
        raise HTTPException(409, "Een back-upbron heeft geen URL/API-sleutel")
    url = body.url or row["url"]
    if body.url:
        validate_url(body.url)
    if body.url or body.api_key:
        key = body.api_key or ctx.secrets.get(apikey_secret(source_id)) or ""
        try:
            info = await detect(url, key, (ctx.services.get("source_transports") or {}).get(
                source_id), ctx.services.get("arr_client_options"))
        except ArrError as exc:
            raise HTTPException(exc.http_status, exc.to_dict()) from exc
        if info["app"] != row["app"]:
            raise HTTPException(409, f"Nieuwe URL is {info['app']}, bron was {row['app']}")
        if body.api_key:
            ctx.secrets.set(apikey_secret(source_id), body.api_key, principal.actor)
    ctx.db.execute("UPDATE source_instances SET name=COALESCE(?,name), url=?, updated_at=? "
                   "WHERE id=?", (body.name, url and url.rstrip("/"), utcnow(), source_id))
    audit(ctx.db, actor=principal.actor, action="migration.source.update", target_type="source",
          target_id=source_id, new_value={"name": body.name, "url": body.url,
                                          "api_key_changed": bool(body.api_key)})
    new = source_row(ctx, source_id)
    assert new is not None
    return source_view(ctx, new)


@router.post("/sources/{source_id}/test")
async def source_test(source_id: str, _: Principal = Depends(require_operator),
                      ctx: AppContext = Depends(get_app_context)) -> dict[str, Any]:
    try:
        return await retest(ctx, source_id)
    except KeyError as exc:
        raise HTTPException(404, "Onbekende bron") from exc


@router.delete("/sources/{source_id}")
def source_archive(source_id: str, principal: Principal = Depends(require_admin),
                   ctx: AppContext = Depends(get_app_context)) -> dict[str, Any]:
    """Archiveert alleen de registratie in MediaGuard; de oude instantie zelf en de
    gedownloade back-ups blijven onaangeroerd."""
    if source_row(ctx, source_id) is None:
        raise HTTPException(404, "Onbekende bron")
    ctx.db.execute("UPDATE source_instances SET archived=1, updated_at=? WHERE id=?",
                   (utcnow(), source_id))
    ctx.secrets.delete(apikey_secret(source_id))
    audit(ctx.db, actor=principal.actor, action="migration.source.archive",
          target_type="source", target_id=source_id)
    return {"archived": True}


# -- runs ------------------------------------------------------------------------------------
class RunOptions(BaseModel):
    follow_new_seasons: bool | None = None
    accept_unresolved_paths: bool = False


class NewRun(BaseModel):
    name: str = Field(min_length=1, max_length=120)
    source_ids: list[str] = Field(min_length=1, max_length=20)
    options: RunOptions = Field(default_factory=RunOptions)


@router.get("/runs")
def runs(_: Principal = Depends(require_viewer),
         ctx: AppContext = Depends(get_app_context)) -> list[dict[str, Any]]:
    return ctx.db.fetch_dicts("SELECT id,name,status,created_at,created_by,updated_at FROM "
                              "migration_runs ORDER BY id DESC")


@router.post("/runs")
def run_create(body: NewRun, principal: Principal = Depends(require_operator),
               ctx: AppContext = Depends(get_app_context)) -> dict[str, Any]:
    for sid in body.source_ids:
        row = source_row(ctx, sid)
        if row is None or row["archived"]:
            raise HTTPException(404, f"Onbekende bron {sid}")
    if len(set(body.source_ids)) != len(body.source_ids):
        raise HTTPException(422, "Dubbele bron in de lijst")
    run_id = store.create_run(ctx, body.name, body.source_ids, body.options.model_dump(),
                              principal.actor)
    for sid in body.source_ids:
        if (source_row(ctx, sid) or {}).get("kind") == "backup" and wizard.latest_backup(ctx,
                                                                                          sid):
            store.step_set(ctx, run_id, "backup", f"source:{sid}", "succeeded",
                           result={"origin": "upload"})
    audit(ctx.db, actor=principal.actor, action="migration.run.create",
          target_type="migration", target_id=run_id, new_value=body.model_dump())
    return store.run_status(ctx, run_id) or {}


@router.get("/runs/{run_id}")
def run_get(run_id: int, _: Principal = Depends(require_viewer),
            ctx: AppContext = Depends(get_app_context)) -> dict[str, Any]:
    from mediaguard.migration import autopilot

    status = store.run_status(ctx, run_id)
    if status is None:
        raise HTTPException(404, "Onbekende run")
    return {**status, "autopilot": autopilot.view(ctx, run_id)}


class AutopilotBody(BaseModel):
    allow_backup_create: bool | None = None


@router.post("/runs/{run_id}/autopilot")
def autopilot_start(run_id: int, body: AutopilotBody | None = None,
                    principal: Principal = Depends(require_operator),
                    ctx: AppContext = Depends(get_app_context)) -> dict[str, Any]:
    """'Doorgaan': MediaGuard voert alle stappen uit die geen keuze vragen."""
    from mediaguard.migration import autopilot

    _require_run(ctx, run_id)
    opts: dict[str, Any] = {}
    if body is not None and body.allow_backup_create is not None:
        if body.allow_backup_create and ROLE_RANK.get(principal.role, 0) < ROLE_RANK["admin"]:
            raise HTTPException(403, "Een back-up laten maken vereist de rol admin")
        opts["allow_backup_create"] = body.allow_backup_create
    job_id = autopilot.enqueue(ctx, run_id, principal.actor, **opts)
    audit(ctx.db, actor=principal.actor, action="migration.autopilot",
          target_type="migration", target_id=run_id, new_value=opts or None)
    return {"queued": True, "job_id": job_id, "autopilot": autopilot.view(ctx, run_id)}


@router.patch("/runs/{run_id}/options")
def run_options(run_id: int, body: RunOptions, principal: Principal = Depends(require_operator),
                ctx: AppContext = Depends(get_app_context)) -> dict[str, Any]:
    _require_run(ctx, run_id)
    ctx.db.execute("UPDATE migration_runs SET options_json=?, updated_at=? WHERE id=?",
                   (dumps(body.model_dump()), utcnow(), run_id))
    audit(ctx.db, actor=principal.actor, action="migration.run.options",
          target_type="migration", target_id=run_id, new_value=body.model_dump())
    return store.run_status(ctx, run_id) or {}


@router.post("/runs/{run_id}/cancel")
def run_cancel(run_id: int, principal: Principal = Depends(require_admin),
               ctx: AppContext = Depends(get_app_context)) -> dict[str, Any]:
    _require_run(ctx, run_id)
    running = ctx.db.fetch_one("SELECT 1 FROM migration_cutovers WHERE run_id=? AND status IN "
                               "('running','rolling_back')", (run_id,))
    if running:
        raise HTTPException(409, "Er loopt een cutover/rollback; rond die eerst af")
    ctx.db.execute("UPDATE migration_runs SET status='cancelled', updated_at=? WHERE id=?",
                   (utcnow(), run_id))
    audit(ctx.db, actor=principal.actor, action="migration.run.cancel",
          target_type="migration", target_id=run_id)
    return {"cancelled": True}


@router.get("/reports/{report_id}")
def report(report_id: int, _: Principal = Depends(require_viewer),
           ctx: AppContext = Depends(get_app_context)) -> dict[str, Any]:
    row = ctx.db.fetch_one("SELECT * FROM migration_reports WHERE id=?", (report_id,))
    if row is None:
        raise HTTPException(404, "Onbekend rapport")
    return store.report_view(row, ctx)


# -- 1 preflight / 2 backup ----------------------------------------------------------------
class RunBody(BaseModel):
    run_id: int
    wait: bool = False


@router.post("/preflight")
async def preflight(body: RunBody, principal: Principal = Depends(require_operator),
                    ctx: AppContext = Depends(get_app_context)) -> dict[str, Any]:
    return await _launch(ctx, body.run_id, "preflight", "", {}, principal, body.wait)


class BackupBody(BaseModel):
    source_id: str
    strategy: Literal["existing", "create"] = "existing"
    confirm_create: bool = False
    wait: bool = False


@router.post("/runs/{run_id}/backup")
async def backup(run_id: int, body: BackupBody, principal: Principal = Depends(require_admin),
                 ctx: AppContext = Depends(get_app_context)) -> dict[str, Any]:
    _require_run(ctx, run_id)
    if body.source_id not in {m["source_id"] for m in store.mappings(ctx, run_id)}:
        raise HTTPException(404, "Bron hoort niet bij deze run")
    if body.strategy == "create" and not body.confirm_create:
        raise HTTPException(409, "Een back-up laten maken door de oude instantie vereist "
                                 "expliciete bevestiging (confirm_create=true)")
    return await _launch(ctx, run_id, "backup", f"source:{body.source_id}",
                         {"strategy": body.strategy, "confirm_create": body.confirm_create},
                         principal, body.wait)


# -- 3 mapping --------------------------------------------------------------------------------
class PreviewBody(BaseModel):
    run_id: int
    reset: bool = False


@router.post("/instance-mapping/preview")
async def mapping_preview(body: PreviewBody, principal: Principal = Depends(require_operator),
                          ctx: AppContext = Depends(get_app_context)) -> dict[str, Any]:
    _require_run(ctx, body.run_id)
    try:
        out = await wizard.mapping_preview(ctx, body.run_id, principal.actor, body.reset)
    except wizard.StepBlocked as exc:
        raise _blocked(exc) from exc
    except ArrError as exc:
        raise HTTPException(exc.http_status, exc.to_dict()) from exc
    store.step_set(ctx, body.run_id, "mapping", "", "pending", result={"previewed": utcnow()})
    return out


class MappingEntry(BaseModel):
    source_id: str
    target_instance_id: str | None = None
    mode: Literal["auto", "native", "api"] | None = None
    path_map: list[dict[str, str]] | None = None
    excluded: bool = False


class MappingBody(BaseModel):
    entries: list[MappingEntry]


@router.put("/runs/{run_id}/mapping")
def mapping_update(run_id: int, body: MappingBody,
                   principal: Principal = Depends(require_operator),
                   ctx: AppContext = Depends(get_app_context)) -> dict[str, Any]:
    _require_run(ctx, run_id)
    entries = [{k: v for k, v in e.model_dump().items() if v is not None or k == "excluded"}
               for e in body.entries]
    wizard.update_mapping(ctx, run_id, entries, principal.actor)
    store.step_set(ctx, run_id, "mapping", "", "pending", result={"edited": utcnow()})
    audit(ctx.db, actor=principal.actor, action="migration.mapping.update",
          target_type="migration", target_id=run_id, new_value=entries)
    return {"matrix": wizard._matrix(ctx, run_id)}


@router.post("/runs/{run_id}/mapping/confirm")
def mapping_confirm(run_id: int, principal: Principal = Depends(require_operator),
                    ctx: AppContext = Depends(get_app_context)) -> dict[str, Any]:
    _require_run(ctx, run_id)
    try:
        out = wizard.confirm_mapping(ctx, run_id, principal.actor)
    except wizard.StepBlocked as exc:
        raise _blocked(exc) from exc
    audit(ctx.db, actor=principal.actor, action="migration.mapping.confirm",
          target_type="migration", target_id=run_id, new_value=wizard._matrix(ctx, run_id))
    from mediaguard.migration import autopilot

    # vanaf hier loopt MediaGuard alle stappen zonder keuze zelf door
    out["autopilot_job_id"] = autopilot.enqueue(ctx, run_id, principal.actor)
    return out


class PathPreviewBody(BaseModel):
    source_id: str


@router.post("/runs/{run_id}/path-preview")
async def path_preview(run_id: int, body: PathPreviewBody,
                       principal: Principal = Depends(require_operator),
                       ctx: AppContext = Depends(get_app_context)) -> dict[str, Any]:
    _require_run(ctx, run_id)
    try:
        return await wizard.path_preview(ctx, run_id, body.source_id, principal.actor)
    except StopIteration as exc:
        raise HTTPException(404, "Bron hoort niet bij deze run") from exc
    except wizard.StepBlocked as exc:
        raise _blocked(exc) from exc


class ScopeBody(BaseModel):
    target_instance_id: str | None = None
    wait: bool = False


class TargetBody(ScopeBody):
    run_id: int


@router.post("/import-preview")
async def import_preview(body: TargetBody, principal: Principal = Depends(require_operator),
                         ctx: AppContext = Depends(get_app_context)) -> dict[str, Any]:
    """Wat de schaduwimport zou doen; leest alleen."""
    _require_run(ctx, body.run_id)
    out = []
    for target in _targets(ctx, body.run_id, body.target_instance_id):
        m = store.mapping_for_target(ctx, body.run_id, target)
        mode, why = wizard.resolve_mode(ctx, m)
        row = source_row(ctx, m["source_id"])
        assert row is not None
        snap = await wizard._source_snapshot(ctx, row["id"], episodes=False)
        credentials = []
        if row["kind"] == "live":
            from mediaguard.migration.importer import _needs_credentials

            client = source_client(ctx, row["id"])
            for kind in ("indexer", "downloadclient"):
                credentials += [{"kind": kind, "name": r["name"]} for r in
                                await client.provider_list(kind) if _needs_credentials(r)]
        try:
            tgt = arr_client(ctx, target)
            existing = len(await (tgt.series_list() if row["app"] == "sonarr"
                                  else tgt.movie_list()))
        except ArrError:
            existing = None
        pp = store.latest_report(ctx, body.run_id, "path_preview", target)
        out.append({
            "target_instance_id": target, "source_id": row["id"], "mode": mode,
            "mode_reason": why,
            "writes_nl": ("De doeldatabase van de fork wordt vervangen door een KOPIE van de "
                          "back-up met RSS, automatisch zoeken, importlijsten en downloadclients "
                          "uitgeschakeld." if mode == "native" else
                          "Tags, profielen, custom formats, indexers en downloadclients "
                          "(uitgeschakeld), rootfolders, path mappings, importlijsten en "
                          "alle series/films worden via de API op de fork aangemaakt."),
            "source_writes": "geen", "items": len(snap["series"] or snap["movies"]),
            "indexers": len(snap["indexers"]), "download_clients": len(snap["download_clients"]),
            "quality_profiles": len(snap["quality_profiles"]),
            "custom_formats": len(snap["custom_formats"]), "tags": len(snap["tags"]),
            "root_folders": snap["root_folders"],
            "credentials_needed": credentials if mode == "api" else [],
            "target_existing_items": existing,
            "path_preview": pp["summary"] if pp else None})
    store.add_report(ctx, body.run_id, "import_preview", "info", summary={"targets": out},
                     actor=principal.actor)
    return {"targets": out}


# -- 4 schaduwimport ------------------------------------------------------------------------
class ShadowBody(BaseModel):
    target_instance_id: str
    mode: Literal["native", "api"] | None = None
    accept_unresolved_paths: bool = False
    wait: bool = False


@router.post("/runs/{run_id}/shadow-import")
async def shadow_import(run_id: int, body: ShadowBody,
                        principal: Principal = Depends(require_admin),
                        ctx: AppContext = Depends(get_app_context)) -> dict[str, Any]:
    _targets(ctx, run_id, body.target_instance_id)
    return await _launch(ctx, run_id, "shadow_import", body.target_instance_id,
                         {"mode": body.mode,
                          "accept_unresolved_paths": body.accept_unresolved_paths},
                         principal, body.wait)


# -- 5 verify / 6 scan / 7 dry-run / 8 readiness ----------------------------------------------
class VerifyBody(TargetBody):
    sync_delta: bool = True


@router.post("/instance-mapping/verify")
async def verify(body: VerifyBody, principal: Principal = Depends(require_operator),
                 ctx: AppContext = Depends(get_app_context)) -> dict[str, Any]:
    out = {}
    for target in _targets(ctx, body.run_id, body.target_instance_id):
        out[target] = await _launch(ctx, body.run_id, "verify", target,
                                    {"sync_delta": body.sync_delta}, principal, body.wait)
        if body.wait:
            rep = store.latest_report(ctx, body.run_id, "verify", target)
            if rep:
                out[target]["report"] = rep
    return out


class AcceptBody(BaseModel):
    target_instance_id: str
    mismatch_ids: list[str]
    reason: str = Field(min_length=3, max_length=500)


@router.post("/runs/{run_id}/accept-mismatches")
def accept(run_id: int, body: AcceptBody, principal: Principal = Depends(require_admin),
           ctx: AppContext = Depends(get_app_context)) -> dict[str, Any]:
    """R6.3: een afwijking expliciet bevestigen. Aantallen en monitorstatussen (C1-no-go)
    zijn NIET te accepteren."""
    run = _require_run(ctx, run_id)
    rep = store.latest_report(ctx, run_id, "verify", body.target_instance_id)
    if rep is None:
        raise HTTPException(409, "Nog geen vergelijkingsrapport")
    by_id = {m["id"]: m for m in rep["mismatches"]}
    bad = [i for i in body.mismatch_ids if i not in by_id or not by_id[i]["acceptable"]]
    if bad:
        raise HTTPException(409, f"Niet te accepteren (onbekend of C1-no-go): {bad}")
    data = loads(run["accepted_json"], {})
    bucket = data.setdefault(body.target_instance_id, {})
    for i in body.mismatch_ids:
        bucket[i] = {"by": principal.actor, "at": utcnow(), "reason": body.reason,
                     "message_nl": by_id[i]["message_nl"]}
    ctx.db.execute("UPDATE migration_runs SET accepted_json=?, updated_at=? WHERE id=?",
                   (dumps(data), utcnow(), run_id))
    audit(ctx.db, actor=principal.actor, action="migration.accept_mismatch",
          target_type="migration", target_id=run_id, instance_id=body.target_instance_id,
          reason=body.reason, new_value=body.mismatch_ids)
    from mediaguard.migration import autopilot

    autopilot.enqueue(ctx, run_id, principal.actor)
    return {"accepted": sorted(bucket)}


@router.post("/runs/{run_id}/scan")
async def scan(run_id: int, body: ScopeBody, principal: Principal = Depends(require_operator),
               ctx: AppContext = Depends(get_app_context)) -> dict[str, Any]:
    return {t: await _launch(ctx, run_id, "scan", t, {}, principal, body.wait)
            for t in _targets(ctx, run_id, body.target_instance_id)}


class DryRunBody(RunBody):
    limit: int | None = Field(None, ge=1, le=1_000_000)


@router.post("/dry-run")
async def dry_run(body: DryRunBody, principal: Principal = Depends(require_operator),
                  ctx: AppContext = Depends(get_app_context)) -> dict[str, Any]:
    return await _launch(ctx, body.run_id, "optimization", "", {"limit": body.limit},
                         principal, body.wait)


class ReadinessBody(ScopeBody):
    register_webhook: bool = True


@router.post("/runs/{run_id}/readiness")
async def readiness(run_id: int, body: ReadinessBody,
                    principal: Principal = Depends(require_operator),
                    ctx: AppContext = Depends(get_app_context)) -> dict[str, Any]:
    return {t: await _launch(ctx, run_id, "readiness", t,
                             {"register_webhook": body.register_webhook}, principal, body.wait)
            for t in _targets(ctx, run_id, body.target_instance_id)}


# -- 9 cutover / rollback / 10 post-audit ----------------------------------------------------
class CutoverBody(BaseModel):
    run_id: int
    targets: list[str] = Field(min_length=1, max_length=10)
    confirm_names: dict[str, str]
    accept_warnings: bool = False
    confirm_old_instance_offline: bool = False
    seerr_server_id: int | None = None
    wait: bool = False


async def _cutover(ctx: AppContext, body: CutoverBody, principal: Principal,
                   kind: str) -> dict[str, Any]:
    _require_run(ctx, body.run_id)
    prepared = []
    try:
        for target in body.targets:
            prepared.append((target, co.validate_request(
                ctx, body.run_id, target, confirm_name=body.confirm_names.get(target, ""),
                accept_warnings=body.accept_warnings,
                confirm_old_offline=body.confirm_old_instance_offline)))
    except wizard.StepBlocked as exc:
        raise _blocked(exc) from exc
    ids = []
    for target, info in prepared:
        if info["existing"]:
            # hervatten (na crash/fout): NOOIT opnieuw de 'vorige staat' vastleggen
            ids.append(int(info["existing"]["id"]))
            co.resume(ctx, int(info["existing"]["id"]), principal.actor)
        else:
            ids.append(co.start(ctx, body.run_id, target, principal.actor,
                                body.confirm_names[target], kind))
        store.step_set(ctx, body.run_id, "cutover", target, "running")
    params = {"seerr_server_id": body.seerr_server_id}
    if body.wait:
        results = []
        for cid in ids:
            try:
                results.append(await co.execute(ctx, cid, params, lambda _m: None))
            except co.CutoverAbort as exc:
                raise HTTPException(409, {"message": str(exc), "cutover_id": cid,
                                          "completed": [r["id"] for r in results]}) from exc
        return {"cutovers": [{k: r[k] for k in ("id", "target_instance_id", "status", "phase")}
                             for r in results]}
    job_id = jobs.enqueue(ctx.db, "migration.cutover", {"cutover_ids": ids, "params": params},
                          target_type="migration_run", target_id=body.run_id,
                          created_by=principal.actor, max_attempts=1)
    runner = ctx.services.get("jobs")
    if runner is not None:
        runner.wake()
    return {"queued": True, "job_id": job_id, "cutover_ids": ids}


@router.post("/cutover")
async def cutover(body: CutoverBody, principal: Principal = Depends(require_admin),
                  ctx: AppContext = Depends(get_app_context)) -> dict[str, Any]:
    return await _cutover(ctx, body, principal, "cutover")


@router.post("/runs/{run_id}/activate-search")
async def activate_search(run_id: int, body: CutoverBody,
                          principal: Principal = Depends(require_admin),
                          ctx: AppContext = Depends(get_app_context)) -> dict[str, Any]:
    """'Zoeken activeren' voor een bron die alleen als back-up bestaat (geen oude live
    instantie om te bevriezen). Vereist getypte bevestiging én confirm_old_instance_offline."""
    if body.run_id != run_id:
        raise HTTPException(422, "run_id komt niet overeen")
    for t in body.targets:
        m = store.mapping_for_target(ctx, run_id, t)
        if (source_row(ctx, m["source_id"]) or {}).get("kind") != "backup":
            raise HTTPException(409, f"{t} heeft een live bron: gebruik de gewone cutover")
    return await _cutover(ctx, body, principal, "activate_search")


class RollbackBody(BaseModel):
    run_id: int
    target_instance_id: str
    confirm_name: str
    wait: bool = False


@router.post("/rollback")
async def rollback(body: RollbackBody, principal: Principal = Depends(require_admin),
                   ctx: AppContext = Depends(get_app_context)) -> dict[str, Any]:
    if store.run_row(ctx, body.run_id) is None:
        raise HTTPException(404, "Onbekende run")
    if body.confirm_name.strip() != body.target_instance_id:
        raise HTTPException(409, f"Typ exact '{body.target_instance_id}' om te bevestigen")
    if co.latest_cutover(ctx, body.run_id, body.target_instance_id) is None:
        raise HTTPException(409, "Geen cutover om terug te draaien")
    if body.wait:
        try:
            res = await co.rollback(ctx, body.run_id, body.target_instance_id, principal.actor,
                                    lambda _m: None)
        except (co.CutoverAbort, wizard.StepBlocked) as exc:
            raise HTTPException(409, str(exc)) from exc
        return {k: res[k] for k in ("id", "target_instance_id", "status", "phase")}
    job_id = jobs.enqueue(ctx.db, "migration.rollback", {
        "run_id": body.run_id, "target": body.target_instance_id, "actor": principal.actor},
        instance_id=body.target_instance_id, target_type="migration_run",
        target_id=body.run_id, created_by=principal.actor, max_attempts=3)
    runner = ctx.services.get("jobs")
    if runner is not None:
        runner.wake()
    return {"queued": True, "job_id": job_id}


class PostAuditBody(BaseModel):
    target_instance_id: str
    hours: int = Field(72, ge=1, le=24 * 30)


@router.post("/runs/{run_id}/post-audit")
async def post_audit(run_id: int, body: PostAuditBody,
                     principal: Principal = Depends(require_operator),
                     ctx: AppContext = Depends(get_app_context)) -> dict[str, Any]:
    try:
        return await co.post_audit(ctx, run_id, body.target_instance_id, body.hours,
                                   principal.actor)
    except wizard.StepBlocked as exc:
        raise _blocked(exc) from exc


# -- AV1-app ----------------------------------------------------------------------------------
@router.post("/av1/import")
async def av1_import(file: UploadFile | None = File(None), path: str | None = Form(None),
                     source_instance: str = Form("av1-ubuntu"),
                     path_map: str | None = Form(None),
                     principal: Principal = Depends(require_admin),
                     ctx: AppContext = Depends(get_app_context)) -> dict[str, Any]:
    import json

    rules = json.loads(path_map) if path_map else []
    tmp: Path | None = None
    try:
        if file is not None:
            tmp = await _save_upload(ctx, file)
            db_file = tmp
        elif path:
            db_file = Path(path)
        else:
            raise HTTPException(422, "Upload een bestand of geef een pad op")
        result = import_av1_db(ctx, db_file, source_instance=source_instance, path_map=rules,
                               actor=principal.actor)
    except ValueError as exc:
        raise HTTPException(422, str(exc)) from exc
    finally:
        if tmp is not None:
            tmp.unlink(missing_ok=True)
    audit(ctx.db, actor=principal.actor, action="migration.av1_import",
          target_type="av1_import", target_id=result["import_id"], new_value=result)
    return result


@router.get("/av1/imports")
def av1_imports(_: Principal = Depends(require_viewer),
                ctx: AppContext = Depends(get_app_context)) -> list[dict[str, Any]]:
    rows = ctx.db.fetch_dicts("SELECT * FROM av1_imports ORDER BY id DESC")
    for r in rows:
        r["counts"] = loads(r.pop("counts_json"), {})
        r["path_map"] = loads(r.pop("path_map_json"), [])
    return rows


@router.get("/av1/provenance")
def av1_provenance(source_instance: str | None = None, status: str | None = None,
                   mediaguard_ref: str | None = None, limit: int = 200, offset: int = 0,
                   _: Principal = Depends(require_viewer),
                   ctx: AppContext = Depends(get_app_context)) -> list[dict[str, Any]]:
    sql = "SELECT * FROM av1_provenance_map WHERE 1=1"
    params: list[Any] = []
    for col, val in (("source_instance", source_instance), ("status", status),
                     ("mediaguard_ref", mediaguard_ref)):
        if val is not None:
            sql += f" AND {col}=?"
            params.append(val)
    rows = ctx.db.fetch_dicts(sql + " ORDER BY source_kind, source_id LIMIT ? OFFSET ?",
                              (*params, min(limit, 1000), offset))
    for r in rows:
        r["data"] = loads(r.pop("data_json"), {})
    return rows
