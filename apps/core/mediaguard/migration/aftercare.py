"""Nazorg na de overstap, zonder knoppen (rapport 4 nov, logica 12 en 15).

* Direct na een voltooide overstap: indexers die via Prowlarr gesynchroniseerd waren maar in
  Prowlarr niet meer bestaan ('OudeIndexer (Prowlarr)', …) worden van de nieuwe instantie
  verwijderd en Prowlarr synchroniseert opnieuw. Alleen namen met '(Prowlarr)' en alleen als
  Prowlarr zelf bereikbaar is; een indexer die nog in Prowlarr staat blijft altijd.
* 24 uur na de overstap: de nacontrole (oude instantie grijpt niet meer, oude indexers
  blijven uit, geen mislukte imports) draait vanzelf; het resultaat komt als melding.
"""

from __future__ import annotations

import logging
from datetime import UTC, datetime, timedelta
from typing import TYPE_CHECKING, Any

from mediaguard import jobs, taskwatch
from mediaguard.audit import audit
from mediaguard.migration.snapshot import parse_dt

if TYPE_CHECKING:
    from mediaguard.context import AppContext

LOGGER = logging.getLogger(__name__)
AUDIT_AFTER_HOURS = 24
JOB_TYPE = "migration.aftercare"
SUFFIX = " (Prowlarr)"


def _state_key(cutover_id: int) -> str:
    return f"migration_aftercare:{cutover_id}"


def completed_cutovers(ctx: AppContext) -> list[dict[str, Any]]:
    return ctx.db.fetch_dicts(
        "SELECT id, run_id, target_instance_id, finished_at FROM migration_cutovers "
        "WHERE status='completed' AND kind='cutover' ORDER BY id")


def due(ctx: AppContext) -> list[int]:
    """Plan nazorg in voor overstappen waar nog iets te doen is."""
    queued = []
    for co in completed_cutovers(ctx):
        st = ctx.state_get(_state_key(int(co["id"])), {}) or {}
        finished = parse_dt(co["finished_at"])
        audit_due = finished is not None and datetime.now(UTC) - finished >= timedelta(
            hours=AUDIT_AFTER_HOURS)
        if st.get("pruned") and (st.get("audited") or not audit_due):
            continue
        queued.append(jobs.enqueue(
            ctx.db, JOB_TYPE, {"cutover_id": int(co["id"])}, target_type="migration_cutover",
            target_id=int(co["id"]), max_attempts=1, priority=190,
            idempotency_key=f"{JOB_TYPE}:{co['id']}:{int(bool(st.get('pruned')))}:"
                            f"{int(audit_due)}", created_by="system:aftercare"))
    return queued


async def prune_dead_prowlarr_indexers(ctx: AppContext, target: str) -> list[str]:
    from mediaguard.instances.registry import arr_client
    from mediaguard.provisioning import store as pstore
    from mediaguard.provisioning.prowlarr import ProwlarrError, prowlarr_client
    from mediaguard.provisioning.prowlarr_sync import trigger_sync

    cfg = pstore.load(ctx)
    if cfg.indexer_mode != "prowlarr" or not cfg.prowlarr.url or \
            not ctx.secrets.has(pstore.PROWLARR_SECRET):
        return []
    try:
        alive = {str(i.get("name") or "").strip().lower()
                 for i in await prowlarr_client(ctx).indexers()}
    except ProwlarrError as exc:
        LOGGER.info("nazorg: Prowlarr niet bereikbaar (%s); niets opgeruimd", exc.message)
        return []
    client = arr_client(ctx, target)
    removed = []
    for idx in await client.provider_list("indexer"):
        name = str(idx.get("name") or "")
        if not name.endswith(SUFFIX):
            continue
        if name[: -len(SUFFIX)].strip().lower() in alive:
            continue
        await client.provider_delete("indexer", int(idx["id"]))
        audit(ctx.db, actor="system:aftercare", action="indexer.delete",
              target_type="indexer", target_id=idx["id"], instance_id=target,
              reason="bestaat niet meer in Prowlarr (nazorg na overstap)",
              old_value={"name": name})
        removed.append(name)
    if removed:
        await trigger_sync(ctx)
    return removed


@jobs.job_handler(JOB_TYPE)
async def _job(jc: jobs.JobContext) -> dict[str, Any]:
    from mediaguard import emergency
    from mediaguard.api.system import notify
    from mediaguard.migration import cutover as co

    ctx = jc.app
    emergency.wait_if_stopped(ctx, "nazorg na de overstap")  # verwijdert indexers: wacht
    row = co.cutover_row(ctx, int(jc.payload["cutover_id"]))
    if row["status"] != "completed":
        return {"skipped": "overstap niet (meer) voltooid"}
    target = str(row["target_instance_id"])
    key = _state_key(int(row["id"]))
    st = dict(ctx.state_get(key, {}) or {})
    out: dict[str, Any] = {}
    if not st.get("pruned"):
        removed = await prune_dead_prowlarr_indexers(ctx, target)
        st["pruned"] = True
        st["removed_indexers"] = removed
        out["removed_indexers"] = removed
        if removed:
            notify(ctx, "info", f"{len(removed)} verdwenen indexer(s) opgeruimd ({target})",
                   ", ".join(removed)[:500], link="/inrichten?tab=indexers",
                   instance_id=target)
    finished = parse_dt(row["finished_at"])
    if not st.get("audited") and finished is not None and \
            datetime.now(UTC) - finished >= timedelta(hours=AUDIT_AFTER_HOURS):
        result = await co.post_audit(ctx, int(row["run_id"]), target, AUDIT_AFTER_HOURS,
                                     "system:aftercare")
        st["audited"] = True
        st["audit_verdict"] = result["verdict"]
        out["audit"] = result["verdict"]
        issues = "; ".join(i["message_nl"] for i in result.get("issues") or [])[:500]
        if result["verdict"] == "go":
            notify(ctx, "success", f"Nacontrole {target}: alles in orde",
                   "24 uur na de overstap: de oude installatie grijpt niets meer en er zijn "
                   "geen mislukte imports.", link=f"/migratie?run={row['run_id']}",
                   instance_id=target)
        else:
            notify(ctx, "error" if result["verdict"] == "no_go" else "warning",
                   f"Nacontrole {target}: let op", issues or "Bekijk de nacontrole.",
                   link=f"/migratie?run={row['run_id']}", instance_id=target)
    ctx.state_set(key, st)
    return out


async def _loop(ctx: AppContext) -> None:
    import asyncio

    await asyncio.sleep(90)
    while True:
        try:
            if due(ctx):
                runner = ctx.services.get("jobs")
                if runner is not None:
                    runner.wake()
            taskwatch.ok(ctx, "migration_aftercare")
        except Exception as exc:  # noqa: BLE001 - nazorg mag nooit stoppen
            LOGGER.exception("nazorg na overstap mislukt")
            taskwatch.fail(ctx, "migration_aftercare", exc)
        await asyncio.sleep(1800)


async def startup(ctx: AppContext) -> None:

    if ctx.settings.start_background and ctx.settings.role in ("core", "all"):
        ctx.services["migration_aftercare_task"] = taskwatch.spawn(
            ctx, "migration_aftercare", _loop, label_nl="Nacontrole na de overstap", every=1800)


async def shutdown(ctx: AppContext) -> None:
    import asyncio
    import contextlib

    task = ctx.services.pop("migration_aftercare_task", None)
    if task is not None:
        task.cancel()
        with contextlib.suppress(asyncio.CancelledError):
            await task
