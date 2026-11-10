"""Quarantaine- en herstel-API + recovery-jobhandlers.

Jobs ``recovery.*`` en ``quarantine.*`` zijn rolgebonden (``jobs.bind_role``): alleen
het proces met ``MG_ROLE=recovery`` (of ``all`` voor single-container dev) voert ze uit.
De core-rol maakt alleen transacties aan en toont status.
"""

from __future__ import annotations

import asyncio
import contextlib
import logging
import os
from pathlib import Path
from typing import Any

from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel, Field

from mediaguard import jobs, taskwatch
from mediaguard.audit import audit
from mediaguard.auth import Principal, require_admin, require_operator, require_viewer
from mediaguard.context import AppContext, get_app_context
from mediaguard.db import loads, utcnow
from mediaguard.recovery import executor, orphans, probe, release_check, service, zfs
from mediaguard.settings_registry import REGISTRY

LOGGER = logging.getLogger(__name__)
router = APIRouter(tags=["recovery"])

jobs.bind_role("recovery.", "recovery")
jobs.bind_role("quarantine.", "recovery")


def register_settings() -> None:
    REGISTRY.register(*service.setting_defs())


def _is_recovery_role(ctx: AppContext) -> bool:
    return ctx.settings.role in ("recovery", "all")


async def _heartbeat_loop(ctx: AppContext) -> None:
    while True:
        try:
            service.mark_heartbeat(ctx)
            taskwatch.ok(ctx, "recovery_heartbeat")
        except Exception as exc:  # noqa: BLE001
            LOGGER.exception("heartbeat mislukt")
            taskwatch.fail(ctx, "recovery_heartbeat", exc)
        await asyncio.sleep(30)


async def startup(ctx: AppContext) -> None:
    if not _is_recovery_role(ctx):
        return
    ctx.settings.quarantine_dir.mkdir(parents=True, exist_ok=True)
    results = await asyncio.to_thread(executor.recover_pending, ctx)
    if results:
        LOGGER.warning("journal replay: %s", results)
    try:  # rapport 6 / N9: achtergebleven kopieën van geannuleerde vervangingen
        swept = await asyncio.to_thread(
            lambda: orphans.sweep(ctx, remove=purge_paused_reason(ctx) is None))
        if swept["removed"]:
            LOGGER.warning("quarantaine: %s weesbestand(en) opgeruimd", swept["removed"])
    except Exception:  # noqa: BLE001 - mag het opstarten nooit blokkeren
        LOGGER.exception("weesbestanden in quarantaine controleren mislukt")
    service.mark_heartbeat(ctx)
    try:
        await asyncio.to_thread(probe.run, ctx)
    except Exception:  # noqa: BLE001 - de test zelf mag het opstarten nooit blokkeren
        LOGGER.exception("schrijftest bij opstarten mislukt")
    if ctx.settings.start_background:
        ctx.services["recovery_heartbeat_task"] = taskwatch.spawn(
            ctx, "recovery_heartbeat", _heartbeat_loop, label_nl="Recovery-worker meldt zich",
            every=30)
        ctx.services["quarantine_maintenance_task"] = taskwatch.spawn(
            ctx, "quarantine_maintenance", _maintenance_loop,
            label_nl="Quarantaine-onderhoud", every=3600)


async def shutdown(ctx: AppContext) -> None:
    for name in ("recovery_heartbeat_task", "quarantine_maintenance_task"):
        task = ctx.services.pop(name, None)
        if task is not None:
            task.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await task


# ── jobhandlers (recovery-rol) ────────────────────────────────────────────────
@jobs.job_handler("recovery.replace")
async def _job_replace(jc: jobs.JobContext) -> dict[str, Any]:
    txn = await executor.run_transaction(jc.app, int(jc.payload["transaction_id"]))
    if txn["state"] == "committed" and txn.get("kind") == "replace" and \
            service.immediate_mode(jc.app):
        # direct-modus: controle meteen inplannen (aparte stap, nooit in deze transactie)
        for row in jc.app.db.fetch_all("SELECT id FROM quarantine_items WHERE "
                                       "transaction_id=? AND status='restorable'", (txn["id"],)):
            jobs.enqueue(jc.app.db, "quarantine.release_check", {"item_id": int(row["id"])},
                         target_type="quarantine", target_id=int(row["id"]), priority=60,
                         idempotency_key=f"quarantine.release_check:{row['id']}:direct",
                         max_attempts=1)
    jc.log(f"Transactie {txn['id']}: {txn['state']}" + (f" ({txn['error']})"
                                                        if txn["error"] else ""))
    return {"transaction_id": txn["id"], "state": txn["state"], "error": txn["error"]}


jobs.job_handler("recovery.restore")(_job_replace)


@jobs.job_handler("recovery.replace_wrong")
async def _job_replace_wrong(jc: jobs.JobContext) -> dict[str, Any]:
    """Verkeerd bestand vervangen namens core (die mag in de compose-installatie niet in de
    bibliotheek en quarantaine schrijven): zelfde regels, hier de bestandsstap."""
    from mediaguard.delivery import replace

    p = jc.payload
    res = await replace.replace_wrong_file(
        jc.app, instance_id=str(p["instance_id"]), kind=str(p["kind"]),
        file_id=int(p["file_id"]), reason_nl=str(p.get("reason_nl") or "verkeerd bestand"),
        actor=str(p.get("actor") or "svc:identity"),
        delivery_item_id=p.get("delivery_item_id"), in_worker=True)
    jc.log(f"Verkeerd bestand {p['file_id']}: {res['status']} ({res.get('reason_nl')})")
    return {k: v for k, v in res.items() if k != "error"}


@jobs.job_handler("recovery.refresh")
async def _job_refresh(jc: jobs.JobContext) -> dict[str, Any]:
    txn = executor.load(jc.app, int(jc.payload["transaction_id"]))
    try:
        await executor.send_refresh(jc.app, txn)
    except Exception as exc:  # noqa: BLE001
        raise jobs.RetryLater(f"arr-refresh mislukt: {type(exc).__name__}", 300) from exc
    return {"refreshed": True}


_PURGE_LOCK = asyncio.Lock()


def purge_paused_reason(ctx: AppContext) -> str | None:
    """Verbeterlijst 37: zolang een TrueNAS-pool niet gezond is, ruimt MediaGuard geen
    originelen op. Ze blijven bewaard tot de pool weer gezond is. Tijdens een noodstop ook
    niet (verbeterlijst 15): dan geldt dit voor de weescontrole, het uurlijkse onderhoud en
    de knop Opruimen tegelijk."""
    from mediaguard.storage_forecast import pool_degraded, pool_names_nl

    if ctx.emergency_stopped():
        return "Noodstop actief: originelen opruimen gepauzeerd"

    degraded = pool_degraded(ctx)
    if not degraded:
        return None
    return (f"TrueNAS-pool {pool_names_nl(degraded)} is niet gezond: originelen blijven "
            "bewaard tot de pool weer gezond is")


@jobs.job_handler("quarantine.purge")
async def _job_purge(jc: jobs.JobContext) -> dict[str, Any]:
    if not jc.app.config.value("quarantine.auto_purge") and not jc.payload.get("manual"):
        return {"purged": 0, "reason": "automatisch opruimen staat uit"}
    paused = purge_paused_reason(jc.app)
    if paused:
        jc.log(f"Opruimen gepauzeerd: {paused}", "warning")
        return {"purged": 0, "paused": True, "reason": paused}
    # rapport 6 / N10: twee opruimrondes tegelijk (direct na de controle + het uurlijkse
    # onderhoud) haalden een bestand weg terwijl de ander het nog controleerde
    async with _PURGE_LOCK:
        return await _purge_round(jc)


async def _purge_round(jc: jobs.JobContext) -> dict[str, Any]:
    purged = 0
    for item in service.purge_candidates(jc.app):
        jc.check_cancelled()
        # laatste controle vlak voor verwijderen: is het actieve bestand nog ongewijzigd?
        result = await release_check.run_check_async(jc.app, int(item["id"]))
        if result["status"] != "passed":
            jc.log(f"Item {item['id']} niet opgeruimd: vrijgavecontrole faalde", "warning")
            continue
        if is_rejected_av1(item):
            # rapport 3 / R11: dit is de afgekeurde AV1-versie (het origineel staat terug)
            _delete_item_file(jc.app, item, rejected_av1_reason(jc.app, item))
            purged += 1
            continue
        reason = {"quotum": "Quarantaine boven het maximum (opgeruimd tot onder de grens)",
                  "vangnet vol": "Vangnet boven het maximum (opgeruimd tot onder de grens)",
                  "vangnet verlopen": "Vangnettermijn verlopen",
                  "direct": "Direct verwijderen"}.get(str(item.get("purge_reason")),
                                                      "Bewaartermijn verlopen")
        _delete_item_file(jc.app, item, f"{reason}; vrijgavecontrole geslaagd")
        purged += 1
    return {"purged": purged}


def _content_loss(checks: list[dict[str, Any]]) -> bool:
    """Is het AV1-bestand inhoudelijk minder dan het origineel (en nog precies het
    bestand dat MediaGuard plaatste)? Alleen dan zet MediaGuard zelf terug."""
    failed = {c["name"] for c in checks if not c["passed"]}
    unchanged = any(c["name"] == "ongewijzigd-sinds-activatie" and c["passed"]
                    for c in checks)
    return bool(failed) and failed <= release_check.CONTENT_CHECKS and unchanged


def auto_restore(ctx: AppContext, item_id: int, checks: list[dict[str, Any]]) -> int | None:
    """Vrijgavecontrole faalde op inhoud (bv. ondertitels weg): origineel terugzetten.

    Alleen als het item nog herstelbaar is, het actieve bestand ongewijzigd MediaGuard's
    AV1-resultaat is en er geen noodstop is. Alles loopt via een gewone restore-transactie
    (journaal, checksum, snapshot)."""
    if not _content_loss(checks) or ctx.emergency_stopped():
        return None
    item = ctx.db.fetch_one("SELECT * FROM quarantine_items WHERE id=?", (item_id,))
    if item is None or item["status"] != "restorable":
        return None
    busy = ctx.db.fetch_one("SELECT 1 AS x FROM replacement_transactions WHERE kind='restore' "
                            "AND restore_of_item_id=? AND state NOT IN ('failed','aborted',"
                            "'rolled_back')", (item_id,))
    if busy:
        return None
    try:
        txn_id, _job = service.create_restore(ctx, item_id, "svc:auto-restore")
    except (LookupError, ValueError, FileNotFoundError) as exc:
        LOGGER.warning("automatisch terugzetten van item %s kon niet: %s", item_id, exc)
        return None
    why = release_check.summary_nl(checks)
    from mediaguard.api.system import notify

    name = Path(str(item["original_path"])).name
    notify(ctx, "warning", f"Origineel teruggezet: {name}",
           f"De AV1-versie was niet compleet ({why}). MediaGuard heeft het origineel "
           "teruggezet; je hoeft niets te doen."[:500],
           link=f"/quarantaine?item={item_id}", dedupe_key="quarantine.auto_restored",
           group_title="{n} originelen teruggezet: AV1-versie was niet compleet")
    audit(ctx.db, actor="svc:auto-restore", action="quarantine.auto_restore",
          target_type="quarantine", target_id=item_id, reason=why,
          new_value={"transaction_id": txn_id})
    ctx.db.execute("UPDATE encoding_jobs SET status='rejected', error=?, updated_at=? WHERE id="
                   "(SELECT encoding_job_id FROM replacement_transactions WHERE id=?)",
                   (f"Afgekeurd na activatie, origineel teruggezet: {why}"[:2000], utcnow(),
                    item["transaction_id"]))
    return txn_id


def restore_failed_checks(ctx: AppContext) -> int:
    """Eenmalig/elk uur: bestaande items met een inhoudelijk gefaalde vrijgavecontrole."""
    n = 0
    for row in ctx.db.fetch_all(
            "SELECT q.id, c.checks_json FROM quarantine_items q JOIN quarantine_release_checks c "
            "ON c.item_id=q.id WHERE q.status='restorable' AND c.status='failed'"):
        if auto_restore(ctx, int(row["id"]), loads(row["checks_json"], [])) is not None:
            n += 1
    return n


@jobs.job_handler("quarantine.release_check")
async def _job_release_check(jc: jobs.JobContext) -> dict[str, Any]:
    result = await release_check.run_check_async(jc.app, int(jc.payload["item_id"]))
    if result["status"] == "failed":
        item_id = int(jc.payload["item_id"])
        if auto_restore(jc.app, item_id, result["checks"]) is not None:
            result["auto_restored"] = True
            return result
        from mediaguard.api.system import notify

        item = jc.app.db.fetch_one("SELECT original_path FROM quarantine_items WHERE id=?",
                                   (item_id,))
        name = Path(str(item["original_path"])).name if item else f"#{item_id}"
        notify(jc.app, "warning", f"{name}: vrijgavecontrole mislukt, origineel blijft bewaard",
               release_check.summary_nl(result["checks"]),
               link=f"/quarantaine?item={item_id}",
               dedupe_key="quarantine.release_check_failed",
               group_title="{n} originelen blijven bewaard: vrijgavecontrole mislukt")
    elif service.immediate_mode(jc.app) and jc.app.config.value("quarantine.auto_purge"):
        jobs.enqueue(jc.app.db, "quarantine.purge", {}, priority=61,
                     idempotency_key=f"quarantine.purge:direct:{jc.payload['item_id']}",
                     max_attempts=1)
    return result


async def _maintenance_loop(ctx: AppContext) -> None:
    """Elk uur: schrijftest per hoofdmap, vrijgavecontroles plannen en (indien aan)
    opruimen. De schrijftest van het opstarten is al gedaan."""
    first = True
    first_sweep_done = False
    while True:
        if not first:
            try:
                await asyncio.to_thread(probe.run, ctx)
            except Exception:  # noqa: BLE001
                LOGGER.exception("schrijftest mislukt")
        first = False
        try:
            # verkeerd bestand: een vervanging die halverwege stopte (core kan dat in de
            # compose-installatie niet zelf rechtzetten: alleen-lezen)
            from mediaguard.delivery import replace

            if not ctx.emergency_stopped():
                await asyncio.to_thread(replace.recover_interrupted, ctx)
        except Exception:  # noqa: BLE001
            LOGGER.exception("onderbroken vervangingen nalopen mislukt")
        try:
            restored = await asyncio.to_thread(restore_failed_checks, ctx)
            if restored:
                LOGGER.info("%s origineel/originelen automatisch teruggezet", restored)
        except Exception:  # noqa: BLE001
            LOGGER.exception("automatisch terugzetten mislukt")
        try:
            fixed = await asyncio.to_thread(orphans.fix_vanished_history, ctx)
            if fixed:
                LOGGER.info("%s item(s) als 'origineel verdwenen' gemarkeerd", fixed)
        except Exception:  # noqa: BLE001
            LOGGER.exception("verdwenen originelen nalopen mislukt")
        if not first_sweep_done:
            first_sweep_done = True
        else:
            try:
                # pool niet gezond: alleen tellen, niets weghalen (verbeterlijst 37)
                await asyncio.to_thread(
                    lambda: orphans.sweep(ctx, remove=purge_paused_reason(ctx) is None))
            except Exception:  # noqa: BLE001
                LOGGER.exception("weesbestanden in quarantaine controleren mislukt")
        try:
            for item_id in release_check.due_for_check(ctx):
                jobs.enqueue(ctx.db, "quarantine.release_check", {"item_id": item_id},
                             target_type="quarantine", target_id=item_id, priority=200,
                             idempotency_key=f"quarantine.release_check:{item_id}:"
                                             f"{utcnow()[:13]}", max_attempts=1)
            if ctx.config.value("quarantine.auto_purge") and service.purge_candidates(ctx) \
                    and purge_paused_reason(ctx) is None:
                jobs.enqueue(ctx.db, "quarantine.purge", {}, priority=210,
                             idempotency_key=f"quarantine.purge:{utcnow()[:13]}",
                             max_attempts=1)
        except Exception:  # noqa: BLE001 - onderhoud mag nooit stoppen
            LOGGER.exception("quarantaine-onderhoud mislukt")
        taskwatch.ok(ctx, "quarantine_maintenance")
        await asyncio.sleep(3600)


@jobs.job_handler("recovery.storage_probe")
async def _job_storage_probe(jc: jobs.JobContext) -> dict[str, Any]:
    state = await asyncio.to_thread(probe.run, jc.app)
    return {"ok": state["ok"], "roots": len(state["roots"]), "requeued": state["requeued"]}


@router.get("/api/v1/storage/probe")
def storage_probe(_: Principal = Depends(require_viewer),
                  ctx: AppContext = Depends(get_app_context)) -> dict[str, Any]:
    """Laatste schrijftest per hoofdmap (Systeem → Opslag)."""
    return probe.status(ctx)


@router.post("/api/v1/storage/probe")
def storage_probe_run(principal: Principal = Depends(require_operator),
                      ctx: AppContext = Depends(get_app_context)) -> dict[str, Any]:
    """Schrijftest nu opnieuw (draait in de recovery-worker, die ook vervangt)."""
    job_id = jobs.enqueue(ctx.db, "recovery.storage_probe", {}, priority=20,
                          idempotency_key=f"recovery.storage_probe:{utcnow()[:16]}",
                          max_attempts=1)
    return {"job_id": job_id, **probe.status(ctx)}


@jobs.job_handler("quarantine.delete")
async def _job_delete(jc: jobs.JobContext) -> dict[str, Any]:
    row = jc.app.db.fetch_one("SELECT * FROM quarantine_items WHERE id=?",
                              (int(jc.payload["item_id"]),))
    if row is None or row["status"] in ("held", "pending", "purged"):
        return {"deleted": False}
    _delete_item_file(jc.app, dict(row), jc.payload.get("reason") or "handmatig verwijderd")
    return {"deleted": True}


RESTORE_DISPLACED = "Herstel van restore"


def is_rejected_av1(item: dict[str, Any]) -> bool:
    """Quarantaine-item dat bij het terugzetten van een origineel is weggezet: de
    afgekeurde AV1-versie, geen origineel."""
    return str(item.get("reason") or "") == RESTORE_DISPLACED


def rejected_av1_reason(ctx: AppContext, item: dict[str, Any]) -> str:
    from mediaguard.textfmt import plain_nl

    row = ctx.db.fetch_one(
        "SELECT error FROM encoding_jobs WHERE source_path=? AND error LIKE "
        "'%origineel teruggezet%' ORDER BY id DESC LIMIT 1", (item.get("original_path"),))
    why = plain_nl(row["error"]) if row and row["error"] else "vrijgavecontrole afgekeurd"
    return f"Afgekeurde AV1-versie verwijderd ({why})"[:500]


def _delete_item_file(ctx: AppContext, item: dict[str, Any], reason: str) -> None:
    path = Path(item["quarantine_path"])
    with contextlib.suppress(FileNotFoundError):
        os.unlink(path)
    with contextlib.suppress(OSError):
        path.parent.rmdir()
    changed = ctx.db.execute(
        "UPDATE quarantine_items SET status='purged', purged_at=? WHERE id=? AND "
        "status<>'purged'", (utcnow(), item["id"]))
    if not changed:
        return  # al opgeruimd door een gelijktijdige ronde: één auditregel per bestand (R11)
    audit(ctx.db, actor="svc:recovery", action="quarantine.purged",
          target_type="quarantine", target_id=item["id"], instance_id=item["instance_id"],
          reason=reason, old_value={"path": item["quarantine_path"]})


# ── API ───────────────────────────────────────────────────────────────────────
@router.get("/api/v1/quarantine")
def quarantine_list(status: str | None = None, instance_id: str | None = None,
                    limit: int = Query(200, le=2000), _: Principal = Depends(require_viewer),
                    ctx: AppContext = Depends(get_app_context)) -> list[dict[str, Any]]:
    sql = "SELECT * FROM quarantine_items WHERE 1=1"
    params: list[Any] = []
    if status:
        sql += " AND status=?"
        params.append(status)
    if instance_id:
        sql += " AND instance_id=?"
        params.append(instance_id)
    rows = ctx.db.fetch_dicts(sql + " ORDER BY id DESC LIMIT ?", [*params, limit])
    for row in rows:
        rc = release_check.check_status(ctx, int(row["id"]))
        row["release_check"] = rc
        # rapport 2 / N18: waarom de controle faalde, per item leesbaar
        row["release_check_reason_nl"] = (release_check.summary_nl(rc["checks"])
                                          if rc and rc["status"] == "failed" else None)
        # rapport 3 / R11: een weggezette, afgekeurde AV1-versie is geen 'geslaagd' origineel
        row["rejected_av1"] = is_rejected_av1(row)
        row["kind_nl"] = ("Afgekeurde AV1-versie" if row["rejected_av1"]
                          else "Verkeerd bestand (vervangen)"
                          if str(row.get("reason") or "").startswith("Verkeerd bestand")
                          else "Origineel vóór vervanging")
        if row["rejected_av1"]:
            row["status_nl"] = ("Afgekeurde AV1-versie verwijderd" if row["status"] == "purged"
                                else "Afgekeurde AV1-versie (origineel staat terug)")
        elif row["status"] == "discarded":
            later = ctx.db.fetch_one(
                "SELECT finished_at FROM replacement_transactions WHERE kind='replace' AND "
                "state='committed' AND id>? AND (library_path=? OR final_path=?) "
                "ORDER BY id DESC LIMIT 1",
                (int(row["transaction_id"]), row["original_path"], row["original_path"]))
            row["superseded_at"] = later["finished_at"] if later else None
            row["status_nl"] = (f"Geannuleerd — later alsnog vervangen op "
                                f"{orphans._date_nl(later['finished_at'])}" if later
                                else "Vervanging geannuleerd — origineel staat nog op "
                                     "zijn plek")
            row["file_present"] = Path(str(row["quarantine_path"])).is_file()
        elif row["status"] == "purged" and (
                str(row.get("reason") or "").startswith("Origineel verdwenen")
                or orphans.vanished_by_check(rc)):
            row["status_nl"] = "Origineel verdwenen buiten MediaGuard om"
            row["error"] = True
    return rows


@router.get("/api/v1/quarantine/orphans")
def quarantine_orphans(_: Principal = Depends(require_viewer),
                       ctx: AppContext = Depends(get_app_context)) -> dict[str, Any]:
    """Weesbestanden in quarantaine (rapport 6, N9): laatste controle door de
    recovery-worker; wat aantoonbaar overbodig is, is al opgeruimd."""
    return orphans.status(ctx)


@router.get("/api/v1/quarantine/usage")
def quarantine_usage(_: Principal = Depends(require_viewer),
                     ctx: AppContext = Depends(get_app_context)) -> dict[str, Any]:
    """Gebruik t.o.v. het ingestelde maximum + wat er automatisch opgeruimd zou worden."""
    # rapport 7 / A1: één getal voor balk én banner — alle bewaarde originelen, ook die nog
    # gecontroleerd worden — tegen het maximum dat nu geldt (quotum of vangnet)
    used = release_check.quarantine_usage_bytes(ctx)
    quota = service.effective_cap_bytes(ctx)
    candidates = service.purge_candidates(ctx)
    paused = release_check.quota_exceeded(ctx)
    # alle originelen die nog bewaard worden (ook 'held' na een mislukte controle: die
    # telden niet mee en de kop toonde '0 mislukt' naast 6 mislukte items)
    counts = {r["status"]: r["n"] for r in ctx.db.fetch_all(
        "SELECT COALESCE(c.status,'pending') AS status, COUNT(*) AS n FROM quarantine_items q "
        "LEFT JOIN quarantine_release_checks c ON c.item_id=q.id "
        "WHERE q.status IN ('restorable','held','pending') GROUP BY 1")}
    for key in ("passed", "failed", "pending", "running"):
        counts.setdefault(key, 0)
    # dezelfde verzameling als de lijst (ook opgeruimde/teruggezette items), zodat de kop
    # optelt tot wat eronder staat
    listed = {r["status"]: r["n"] for r in ctx.db.fetch_all(
        "SELECT COALESCE(c.status,'pending') AS status, COUNT(*) AS n FROM quarantine_items q "
        "LEFT JOIN quarantine_release_checks c ON c.item_id=q.id GROUP BY 1")}
    for key in ("passed", "failed", "pending", "running"):
        listed.setdefault(key, 0)
    immediate = service.immediate_mode(ctx)
    net_hours, net_bytes = service.safety_net(ctx)
    net = service.safety_net_view(ctx) if immediate and net_hours else None
    next_at = service.next_purge_at(ctx, candidates, net)
    target = int(quota * service.purge_target_fraction(ctx))
    return {"used_bytes": used, "quota_bytes": quota, "percent": round(100 * used / quota, 1)
            if quota else 0, "quota_exceeded": used >= quota,
            # pauzeert alleen als opruimen geen ruimte kan maken
            "encode_queue_paused": paused,
            "cap_bytes": quota, "purge_target_bytes": target,
            "purge_target_percent": round(100 * service.purge_target_fraction(ctx)),
            "reserve_bytes": service.pending_handoff_bytes(ctx),
            "unchecked_bytes": int((ctx.db.fetch_one(
                "SELECT COALESCE(SUM(q.size),0) AS n FROM quarantine_items q LEFT JOIN "
                "quarantine_release_checks c ON c.item_id=q.id WHERE q.status IN "
                "('restorable','held','pending') AND COALESCE(c.status,'pending')<>'passed'")
                or {"n": 0})["n"]),
            "release_checks": counts, "release_checks_listed": listed,
            "retention_days": ctx.config.value("quarantine.retention_days"),
            # echte modus: bij 'direct' geldt de bewaartermijn niet en kan niets terug
            "mode": "immediate" if immediate else "retain",
            "mode_nl": ((f"Direct verwijderen, met vangnet: originelen blijven nog "
                         f"{net_hours} uur bewaard (max. {net_bytes // 1024**3} GB); zo lang "
                         "kun je terugzetten"
                         + (f" — nu {net['items']} item(s), "
                            f"{round(net['bytes'] / 1024**3)} GB" if net else ""))
                        if immediate and net_hours else
                        "Originelen worden direct verwijderd zodra het nieuwe bestand de "
                        "controle doorstaat — terugzetten kan daarna niet meer"
                        if immediate else
                        f"Originelen blijven {ctx.config.value('quarantine.retention_days')} "
                        "dagen bewaard; zo lang kun je terugzetten"),
            "restorable": not immediate or net_hours > 0,
            # rapport 6 / N8: het vangnet zichtbaar maken (inhoud, eerste vervaldatum)
            "safety_net": ({"hours": net_hours, "bytes": net_bytes, "max_bytes": net_bytes,
                            **(net or {})} if immediate else None),
            "auto_purge": ctx.config.value("quarantine.auto_purge"),
            "orphans": {k: orphans.status(ctx).get(k) for k in
                        ("checked_at", "removed", "left", "left_bytes", "freed_bytes")},
            "next_purge": {"count": len(candidates),
                           "bytes": sum(int(c["size"] or 0) for c in candidates),
                           "at": next_at,
                           "at_nl": service.next_purge_nl(next_at, len(candidates))}}


@router.post("/api/v1/quarantine/{item_id}/release-check")
def quarantine_release_check(item_id: int, principal: Principal = Depends(require_operator),
                             ctx: AppContext = Depends(get_app_context)) -> dict[str, Any]:
    """'Controleer nu': plant de vrijgavecontrole direct in (recovery-worker)."""
    if ctx.db.fetch_one("SELECT 1 FROM quarantine_items WHERE id=?", (item_id,)) is None:
        raise HTTPException(404, "Quarantaine-item niet gevonden")
    job_id = jobs.enqueue(ctx.db, "quarantine.release_check", {"item_id": item_id},
                          target_type="quarantine", target_id=item_id, priority=50,
                          created_by=principal.actor, max_attempts=1)
    return {"job_id": job_id}


@router.get("/api/v1/quarantine/{item_id}")
def quarantine_get(item_id: int, _: Principal = Depends(require_viewer),
                   ctx: AppContext = Depends(get_app_context)) -> dict[str, Any]:
    row = ctx.db.fetch_one("SELECT * FROM quarantine_items WHERE id=?", (item_id,))
    if row is None:
        raise HTTPException(404, "Quarantaine-item niet gevonden")
    item = dict(row)
    item["transaction"] = _txn_view(ctx, int(row["transaction_id"]))
    item["release_check"] = release_check.check_status(ctx, item_id)
    return item


class ConfirmBody(BaseModel):
    confirm: bool = False
    reason: str = Field(default="", max_length=500)
    # handmatig verwijderen zonder geslaagde vrijgavecontrole vereist expliciete keuze
    force_unverified: bool = False


class BulkDeleteBody(ConfirmBody):
    item_ids: list[int] = Field(min_length=1, max_length=5000)


@router.post("/api/v1/quarantine/use-quarantine")
def use_quarantine(body: ConfirmBody, principal: Principal = Depends(require_operator),
                   ctx: AppContext = Depends(get_app_context)) -> dict[str, Any]:
    """Eén tik: originelen weer bewaren volgens de bewaartermijn (i.p.v. direct verwijderen).
    In de modus 'direct verwijderen' blijft het origineel trouwens altijd staan tot de
    vrijgavecontrole van het AV1-bestand is geslaagd."""
    if not body.confirm:
        raise HTTPException(422, "Bevestiging vereist ('confirm': true)")
    before = {"mode": ctx.config.value("quarantine.mode"),
              "retention_days": ctx.config.value("quarantine.retention_days")}
    ctx.config.set("global", "", "quarantine.mode", "retain", principal.actor)
    if int(ctx.config.value("quarantine.retention_days")) < 1:
        ctx.config.set("global", "", "quarantine.retention_days", 7, principal.actor)
    audit(ctx.db, actor=principal.actor, action="quarantine.use_quarantine",
          target_type="settings", target_id="quarantine.mode", old_value=before,
          new_value={"mode": "retain",
                     "retention_days": ctx.config.value("quarantine.retention_days")})
    return {"mode": "retain", "retention_days": ctx.config.value("quarantine.retention_days")}


@router.post("/api/v1/quarantine/{item_id}/restore")
def quarantine_restore(item_id: int, body: ConfirmBody,
                       principal: Principal = Depends(require_operator),
                       ctx: AppContext = Depends(get_app_context)) -> dict[str, Any]:
    if not body.confirm:
        raise HTTPException(422, "Bevestiging vereist ('confirm': true)")
    if ctx.emergency_stopped():
        raise HTTPException(409, "Noodstop actief: herstel tijdelijk geblokkeerd")
    try:
        txn_id, job_id = service.create_restore(ctx, item_id, principal.actor)
    except LookupError as exc:
        raise HTTPException(404, str(exc)) from exc
    except (ValueError, FileNotFoundError) as exc:
        raise HTTPException(409, str(exc)) from exc
    audit(ctx.db, actor=principal.actor, action="quarantine.restore_requested",
          target_type="quarantine", target_id=item_id, reason=body.reason or None,
          new_value={"transaction_id": txn_id})
    return {"transaction_id": txn_id, "job_id": job_id, "status": "queued"}


@router.post("/api/v1/quarantine/{item_id}/pin")
def quarantine_pin(item_id: int, pinned: bool = True,
                   principal: Principal = Depends(require_operator),
                   ctx: AppContext = Depends(get_app_context)) -> dict[str, Any]:
    if ctx.db.execute("UPDATE quarantine_items SET pinned=? WHERE id=?",
                      (int(pinned), item_id)) == 0:
        raise HTTPException(404, "Quarantaine-item niet gevonden")
    audit(ctx.db, actor=principal.actor, action="quarantine.pin", target_type="quarantine",
          target_id=item_id, new_value={"pinned": pinned})
    return {"id": item_id, "pinned": pinned}


@router.delete("/api/v1/quarantine/{item_id}")
def quarantine_delete(item_id: int, body: ConfirmBody,
                      principal: Principal = Depends(require_admin),
                      ctx: AppContext = Depends(get_app_context)) -> dict[str, Any]:
    row = ctx.db.fetch_one("SELECT * FROM quarantine_items WHERE id=?", (item_id,))
    if row is None:
        raise HTTPException(404, "Quarantaine-item niet gevonden")
    if row["status"] in ("held", "pending"):
        raise HTTPException(409, "Item hoort bij een lopende transactie en kan niet weg")
    if not body.confirm:
        raise HTTPException(422, "Definitief verwijderen vereist bevestiging ('confirm': true)")
    check = release_check.check_status(ctx, item_id)
    if (check is None or check["status"] != "passed") and not body.force_unverified:
        raise HTTPException(409, "Het nieuwe bestand is nog niet (succesvol) gecontroleerd. "
                                 "Verwijder alleen als je zeker bent ('force_unverified').")
    job_id = jobs.enqueue(ctx.db, "quarantine.delete", {"item_id": item_id,
                                                        "reason": body.reason},
                          idempotency_key=f"quarantine.delete:{item_id}",
                          created_by=principal.actor)
    audit(ctx.db, actor=principal.actor, action="quarantine.delete_requested",
          target_type="quarantine", target_id=item_id, reason=body.reason or None)
    return {"job_id": job_id}


@router.post("/api/v1/quarantine/bulk-delete")
def quarantine_bulk_delete(body: BulkDeleteBody, principal: Principal = Depends(require_admin),
                           ctx: AppContext = Depends(get_app_context)) -> dict[str, Any]:
    """Meerdere originelen handmatig definitief verwijderen. Zonder 'confirm' = preview."""
    marks = ",".join("?" * len(body.item_ids))
    rows = ctx.db.fetch_dicts(f"SELECT * FROM quarantine_items WHERE id IN ({marks})",
                              body.item_ids)
    blocked = [r["id"] for r in rows if r["status"] in ("held", "pending", "purged")]
    unverified = [r["id"] for r in rows if r["id"] not in blocked and (
        (c := release_check.check_status(ctx, int(r["id"]))) is None or c["status"] != "passed")]
    deletable = [r for r in rows if r["id"] not in blocked and
                 (body.force_unverified or r["id"] not in unverified)]
    preview = {"count": len(deletable), "bytes": sum(int(r["size"] or 0) for r in deletable),
               "blocked": blocked, "unverified": unverified}
    if not body.confirm:
        return {"dry_run": True, **preview}
    job_ids = [jobs.enqueue(ctx.db, "quarantine.delete", {"item_id": r["id"],
                                                          "reason": body.reason or "bulk"},
                            idempotency_key=f"quarantine.delete:{r['id']}",
                            created_by=principal.actor) for r in deletable]
    audit(ctx.db, actor=principal.actor, action="quarantine.bulk_delete_requested",
          reason=body.reason or None, new_value=preview)
    return {"job_ids": job_ids, **preview}


@router.post("/api/v1/quarantine/purge")
def quarantine_purge(body: ConfirmBody, principal: Principal = Depends(require_admin),
                     ctx: AppContext = Depends(get_app_context)) -> dict[str, Any]:
    candidates = service.purge_candidates(ctx)
    paused = purge_paused_reason(ctx)
    if not body.confirm:
        return {"dry_run": True, "count": len(candidates), "paused_nl": paused,
                "bytes": sum(int(c["size"] or 0) for c in candidates)}
    if paused:
        raise HTTPException(409, paused[:1].upper() + paused[1:] + ".")
    job_id = jobs.enqueue(ctx.db, "quarantine.purge", {"manual": True},
                          created_by=principal.actor)
    audit(ctx.db, actor=principal.actor, action="quarantine.purge_requested",
          new_value={"count": len(candidates)})
    return {"job_id": job_id, "count": len(candidates)}


def _txn_view(ctx: AppContext, txn_id: int) -> dict[str, Any] | None:
    row = ctx.db.fetch_one("SELECT * FROM replacement_transactions WHERE id=?", (txn_id,))
    if row is None:
        return None
    d = dict(row)
    d["source_snapshot"] = loads(d.pop("source_snapshot_json"), {})
    d["warnings"] = loads(d.pop("warnings_json"), [])
    d["refresh"] = loads(d.pop("refresh_json"), {})
    return d


@router.get("/api/v1/recovery/transactions")
def transactions(state: str | None = None, limit: int = Query(100, le=1000),
                 _: Principal = Depends(require_viewer),
                 ctx: AppContext = Depends(get_app_context)) -> list[dict[str, Any]]:
    sql = "SELECT id FROM replacement_transactions"
    params: list[Any] = []
    if state:
        sql += " WHERE state=?"
        params.append(state)
    rows = ctx.db.fetch_all(sql + " ORDER BY id DESC LIMIT ?", [*params, limit])
    return [v for r in rows if (v := _txn_view(ctx, int(r["id"]))) is not None]


@router.get("/api/v1/recovery/transactions/{txn_id}")
def transaction(txn_id: int, _: Principal = Depends(require_viewer),
                ctx: AppContext = Depends(get_app_context)) -> dict[str, Any]:
    view = _txn_view(ctx, txn_id)
    if view is None:
        raise HTTPException(404, "Transactie niet gevonden")
    journal = ctx.db.fetch_dicts("SELECT * FROM replacement_journal WHERE transaction_id=? "
                                 "ORDER BY id", (txn_id,))
    for j in journal:
        j["detail"] = loads(j.pop("detail_json"), {})
    view["journal"] = journal
    return view


class UnlockBody(BaseModel):
    confirm: bool = False
    enabled: bool = True


@router.post("/api/v1/recovery/library-write")
def library_write(body: UnlockBody, principal: Principal = Depends(require_admin),
                  ctx: AppContext = Depends(get_app_context)) -> dict[str, Any]:
    """'Bibliotheek vrijgeven' (verse installatie zonder migratie) of weer vergrendelen."""
    if body.enabled and not body.confirm:
        raise HTTPException(422, "Bevestiging vereist ('confirm': true)")
    old = ctx.config.value("library.write_enabled")
    ctx.config.set("global", "", "library.write_enabled", bool(body.enabled), principal.actor)
    audit(ctx.db, actor=principal.actor, action="library.write_enabled", old_value=old,
          new_value=bool(body.enabled))
    # de enige weg om dit te wijzigen (niet via de gewone instellingen): schermen verversen
    ctx.events.publish("settings.changed", {"changes": [
        {"key": "library.write_enabled", "scope_type": "global", "scope_id": "",
         "value": bool(body.enabled)}]})
    return {"library_write_enabled": bool(body.enabled)}


@router.get("/api/v1/recovery/readiness")
def readiness(_: Principal = Depends(require_viewer),
              ctx: AppContext = Depends(get_app_context)) -> dict[str, Any]:
    return service.readiness(ctx)


@router.post("/api/v1/recovery/truenas/test")
async def truenas_test(principal: Principal = Depends(require_admin),
                       ctx: AppContext = Depends(get_app_context)) -> dict[str, Any]:
    result = await zfs.test_connection(ctx)
    audit(ctx.db, actor=principal.actor, action="recovery.truenas_test",
          new_value={"ok": result["ok"]})
    return result
