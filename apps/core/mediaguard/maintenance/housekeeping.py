"""Nachtelijk onderhoud van de core-database (set-and-forget).

Zonder dit groeien jobs/logs, events, hookbeslissingen (één rij per pre-grab/pre-import)
en meldingen onbeperkt. Bewaartermijnen zijn instellingen met veilige standaarden. Het
auditlog en de aanmeldgeschiedenis blijven minstens 30 dagen (standaard 365 en 180) en
alleen een beheerder kan die termijnen wijzigen; quarantaine en vervangingsjournaal blijven.
"""

from __future__ import annotations

import logging
import time
from datetime import UTC, datetime, timedelta
from typing import TYPE_CHECKING, Any

from mediaguard import jobs
from mediaguard.settings_registry import SettingDef

if TYPE_CHECKING:
    from mediaguard.context import AppContext

LOGGER = logging.getLogger(__name__)
CATEGORY = "Onderhoud"
BACKUP_HOUR = 4  # lokale tijd; rustig moment, vast tijdstip (niet 'elke 24 uur na herstart')
BACKUP_MINUTE = 15
VACUUM_FREE_RATIO = 0.2


def setting_defs() -> list[SettingDef]:
    return [
        SettingDef("maintenance.retention_days_jobs", "int", 30, CATEGORY,
                   "Afgeronde taken bewaren (dagen)", "Keep finished jobs (days)",
                   minimum=1, maximum=3650, unit="dagen", advanced=True,
                   help_nl="Geslaagde, geannuleerde en opgeloste taken plus hun logregels."),
        SettingDef("maintenance.retention_days_events", "int", 14, CATEGORY,
                   "Gebeurtenissen bewaren (dagen)", "Keep events (days)",
                   minimum=1, maximum=3650, unit="dagen", advanced=True),
        SettingDef("maintenance.retention_days_hooks", "int", 30, CATEGORY,
                   "Grab/import-beslissingen bewaren (dagen)", "Keep hook decisions (days)",
                   minimum=1, maximum=3650, unit="dagen", advanced=True),
        SettingDef("maintenance.retention_days_notifications", "int", 90, CATEGORY,
                   "Gelezen meldingen bewaren (dagen)", "Keep read notifications (days)",
                   minimum=1, maximum=3650, unit="dagen", advanced=True),
        SettingDef("maintenance.retention_days_history", "int", 180, CATEGORY,
                   "Geschiedenis bewaren (dagen)", "Keep history (days)",
                   minimum=30, maximum=3650, unit="dagen", advanced=True,
                   help_nl="Importgebeurtenissen, leveringstijdlijn, aanmeldgeschiedenis, "
                           "zoekrondes, inrichtingsrondes, ondertitel- en offsite-logboek. "
                           "De laatste stand per aanvraag of item blijft altijd staan."),
        SettingDef("maintenance.retention_days_audit", "int", 365, CATEGORY,
                   "Auditlog bewaren (dagen)", "Keep audit log (days)",
                   minimum=30, maximum=3650, unit="dagen", advanced=True,
                   help_nl="Wie wat wanneer veranderde. Ouder dan dit wordt opgeruimd."),
        SettingDef("maintenance.retry_transient_failures", "bool", True, CATEGORY,
                   "Tijdelijke fouten automatisch opnieuw proberen",
                   "Retry transient failures automatically",
                   help_nl="Taken die mislukten door een verbindingsfout of time-out worden "
                           "één keer per nacht opnieuw geprobeerd (maximaal 3 keer).",
                   advanced=True),
    ]


def _ago(days: int) -> str:
    return (datetime.now(UTC) - timedelta(days=days)).isoformat(timespec="seconds")


def _days(ctx: AppContext, key: str) -> int:
    return int(ctx.config.value(f"maintenance.retention_days_{key}"))


BATCH_ROWS = 2000
BATCH_PAUSE_S = 0.02  # tussen twee batches: andere schrijvers (hooks!) krijgen het slot
FAILED_JOBS_DAYS = 90
JOB_BATCH_ROWS = 200  # elke job neemt zijn logregels mee (cascade)


def _batched_delete(ctx: AppContext, table: str, where: str, params: tuple[Any, ...] = (),
                    key: str = "rowid", batch: int = BATCH_ROWS) -> int:
    """Verwijder in kleine batches, elk in een eigen korte transactie. Zo houdt het
    onderhoud het schrijfslot nooit lang vast: een pre-grab-hook (3 s time-out) of het
    recovery-proces wacht hooguit één batch. De batches lopen op ``key`` vooruit, zodat de
    tabel niet bij elke batch opnieuw van voren wordt doorzocht."""
    total, last = 0, None
    while True:
        cond = where if last is None else f"({where}) AND {key}>?"
        args = params if last is None else (*params, last)
        rows = ctx.db.fetch_all(f"SELECT {key} AS k FROM {table} WHERE {cond} "  # noqa: S608
                                f"ORDER BY {key} LIMIT {int(batch)}", args)
        if not rows:
            return total
        keys = [r["k"] for r in rows]
        marks = ",".join("?" * len(keys))
        with ctx.db.transaction() as conn:
            total += conn.execute(f"DELETE FROM {table} WHERE {key} IN ({marks})",  # noqa: S608
                                  keys).rowcount
        last = keys[-1]
        if len(keys) < batch:
            return total
        time.sleep(BATCH_PAUSE_S)


def _exists(ctx: AppContext, table: str) -> bool:
    return ctx.db.fetch_one("SELECT 1 FROM sqlite_master WHERE type='table' AND name=?",
                            (table,)) is not None


def prune(ctx: AppContext) -> dict[str, int]:
    """Bewaartermijnen voor alle tabellen die anders onbeperkt groeien. Wat voor herstel of
    lopend werk nodig is, blijft: openstaande jobs, de laatste stand per item, quarantaine,
    vervangingsjournaal en herkomst van releases."""
    out: dict[str, int] = {}
    jobs_cut = _ago(_days(ctx, "jobs"))
    history_cut = _ago(_days(ctx, "history"))
    now = datetime.now(UTC).isoformat(timespec="seconds")
    # logregels gaan mee via ON DELETE CASCADE
    out["jobs"] = _batched_delete(
        ctx, "jobs", "COALESCE(finished_at, created_at)<? AND (status IN "
        "('succeeded','cancelled') OR (status='failed' AND resolved_at IS NOT NULL))",
        (jobs_cut,), key="id", batch=JOB_BATCH_ROWS)
    # onopgeloste fouten tellen na 7 dagen niet meer mee; na 90 dagen weg
    out["jobs_failed_old"] = _batched_delete(
        ctx, "jobs", "status='failed' AND COALESCE(finished_at, created_at)<?",
        (_ago(max(FAILED_JOBS_DAYS, _days(ctx, "jobs"))),), key="id", batch=JOB_BATCH_ROWS)
    out["events"] = _batched_delete(ctx, "outbox_events", "ts<?",
                                    (_ago(_days(ctx, "events")),), key="id")
    out["hook_decisions"] = _batched_delete(ctx, "hook_decisions", "ts<?",
                                            (_ago(_days(ctx, "hooks")),))
    out["notifications"] = _batched_delete(
        ctx, "notifications", "read=1 AND ts<?", (_ago(_days(ctx, "notifications")),),
        key="id")
    out["sessions"] = _batched_delete(ctx, "sessions", "expires_at<?", (now,))
    with ctx.db.transaction() as conn:
        out["library_sync_runs"] = conn.execute(
            "DELETE FROM library_sync_runs WHERE id NOT IN (SELECT id FROM library_sync_runs r "
            "WHERE r.instance_id=library_sync_runs.instance_id ORDER BY id DESC LIMIT 50)"
        ).rowcount
    # oude optimalisatieplannen (elk ~100k rijen): de laatste 3 + lopende blijven; eerst de
    # items in batches, dan pas het plan (anders één lange cascade-delete)
    old_plans = [int(r["id"]) for r in ctx.db.fetch_all(
        "SELECT id FROM optimization_plans WHERE status NOT IN ('running','paused') AND id NOT "
        "IN (SELECT id FROM optimization_plans ORDER BY id DESC LIMIT 3)")]
    for plan_id in old_plans:
        _batched_delete(ctx, "optimization_items", "plan_id=?", (plan_id,), key="id")
    out["optimization_plans"] = _batched_delete(
        ctx, "optimization_plans", f"id IN ({','.join('?' * len(old_plans))})",
        tuple(old_plans), key="id") if old_plans else 0
    out["backfill_attempts"] = _batched_delete(
        ctx, "backfill_attempts", "status!='pending' AND COALESCE(finished_at, ts)<?",
        (_ago(90),), key="id")
    # rapport 31: tabellen zonder bewaartermijn
    for name, table, where, params in (
        ("audit", "audit_events", "ts<?", (_ago(_days(ctx, "audit")),)),
        ("login_history", "login_history", "ts<?", (history_cut,)),
        ("import_events", "import_events", "received_at<?", (history_cut,)),
        ("delivery_events", "delivery_events", "ts<? AND id NOT IN (SELECT MAX(id) FROM "
         "delivery_events GROUP BY item_id)", (history_cut,)),
        ("missing_diagnostics", "missing_diagnostics", "ts<? AND id NOT IN (SELECT MAX(id) "
         "FROM missing_diagnostics GROUP BY wanted_id)", (history_cut,)),
        ("backfill_runs", "backfill_runs", "started_at<? AND status!='running'",
         (history_cut,)),
        ("provisioning_runs", "provisioning_runs", "started_at<? AND status!='running' AND "
         "id NOT IN (SELECT id FROM provisioning_runs ORDER BY id DESC LIMIT 50)",
         (history_cut,)),
        ("download_janitor_runs", "download_janitor_runs", "started_at<? AND id NOT IN "
         "(SELECT MAX(id) FROM download_janitor_runs)", (history_cut,)),
        ("subtitle_downloads", "subtitle_downloads", "ts<?", (history_cut,)),
        ("offsite_copies", "offsite_copies", "created_at<?", (history_cut,)),
        ("notify_outbox", "notify_outbox", "status IN ('sent','failed','dropped') AND "
         "created_at<?", (_ago(30),)),
        ("notify_digest_items", "notify_digest_items", "created_at<?", (_ago(60),)),
        ("notify_digests", "notify_digests", "created_at<?", (history_cut,)),
        ("ai_usage", "ai_usage", "ts<? AND status!='reserved'", (_ago(400),)),
        ("encoding_autopilot_skips", "encoding_autopilot_skips", "retry_after<?",
         (_ago(30),)),
    ):
        if not _exists(ctx, table):
            continue
        try:
            out[name] = _batched_delete(ctx, table, where, params,
                                        key="rowid" if table == "encoding_autopilot_skips"
                                        else "id")
        except Exception:  # noqa: BLE001 - één tabel mag de rest niet tegenhouden
            LOGGER.exception("opruimen van %s mislukt", table)
            out[name] = -1
    return out


def run_nightly(ctx: AppContext) -> dict[str, Any]:
    result: dict[str, Any] = {}
    try:
        result["pruned"] = prune(ctx)
    except Exception as exc:  # noqa: BLE001 - onderhoud mag de rest niet tegenhouden
        LOGGER.exception("opruimen mislukt")
        result["prune_error"] = str(exc)
    if ctx.config.value("maintenance.retry_transient_failures"):
        result["retried"] = jobs.retry_transient_failures(ctx.db)
        if result["retried"]:
            runner = ctx.services.get("jobs")
            if runner is not None:
                runner.wake()
    busy = ctx.db.fetch_one("SELECT COUNT(*) AS n FROM jobs WHERE status='running'")
    weekly = datetime.now().weekday() == 6 and not (busy and busy["n"])
    try:
        # VACUUM blokkeert alle schrijvers (ook recovery en de hooks): alleen als er echt
        # veel lege ruimte is, anders hergebruikt SQLite de vrije pagina's gewoon
        weekly = weekly and ctx.db.free_ratio() >= VACUUM_FREE_RATIO
        ctx.db.maintenance(vacuum=weekly)
        result["vacuum"] = weekly
    except Exception as exc:  # noqa: BLE001
        LOGGER.warning("database-onderhoud overgeslagen: %s", exc)
    ctx.state_set("maintenance.last_nightly", {"at": datetime.now(UTC).isoformat(
        timespec="seconds"), **{k: v for k, v in result.items() if k != "pruned"},
        "pruned": result.get("pruned", {})})
    return result


def due_now(ctx: AppContext, now: datetime | None = None) -> bool:
    """Eén keer per nacht rond 04:15 lokale tijd."""
    now = now or datetime.now().astimezone()
    if (now.hour, now.minute) < (BACKUP_HOUR, BACKUP_MINUTE):
        return False
    last = (ctx.state_get("maintenance.last_nightly", {}) or {}).get("at")
    if not last:
        return True
    try:
        last_local = datetime.fromisoformat(last).astimezone(now.tzinfo)
    except ValueError:
        return True
    return last_local.date() < now.date()
