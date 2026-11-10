"""Status, health, noodstop, audit, jobs, notificaties."""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel, Field

from mediaguard import __version__, jobs
from mediaguard.audit import audit
from mediaguard.auth import Principal, require_admin, require_operator, require_viewer
from mediaguard.context import AppContext, get_app_context
from mediaguard.db import loads, utcnow

router = APIRouter(prefix="/api/v1", tags=["system"])


@router.get("/health")
def health() -> dict[str, str]:
    """Liveness zonder authenticatie (Docker healthcheck)."""
    return {"status": "ok", "version": __version__}


@router.get("/status")
def status(_: Principal = Depends(require_viewer),
           ctx: AppContext = Depends(get_app_context)) -> dict[str, Any]:
    counts = {r["status"]: r["n"] for r in ctx.db.fetch_all(
        "SELECT status, COUNT(*) AS n FROM jobs GROUP BY status")}
    # 'failed' = alleen recente, nog niet opgeloste of genegeerde fouten (de badge en
    # 'Actie nodig' zakken weer naar nul); 'failed_all' blijft het historische totaal
    counts["failed_all"] = counts.get("failed", 0)
    counts["failed"] = jobs.unresolved_failures(ctx.db)
    # badge: alleen waarschuwingen en fouten (info/succes zijn al gelezen)
    unread = ctx.db.fetch_one("SELECT COUNT(*) AS n FROM notifications WHERE read=0 AND "
                              "severity IN ('warning','error')")
    return {
        "version": __version__,
        "role": ctx.settings.role,
        "emergency_stop": ctx.state_get("emergency_stop", {"active": False}),
        "jobs": counts,
        "unread_notifications": int(unread["n"]) if unread else 0,
        # 'Wacht op jou' (zonder encoder-health): telt mee in 'N dingen vragen aandacht'
        **_attention_and_health(ctx),
        "time": utcnow(),
    }


def _attention_summary(ctx: AppContext) -> dict[str, int]:
    from mediaguard.attention import summary

    try:
        return summary(ctx)
    except Exception:  # noqa: BLE001 - status mag hier nooit op falen
        return {"count": 0, "errors": 0, "warnings": 0}


def _attention_and_health(ctx: AppContext) -> dict[str, Any]:
    """'attention' telt mee in 'N dingen vragen aandacht'; 'health' is hetzelfde
    gezondheidsmodel als /api/v1/health/summary (zonder netwerk: laatst bekende downloads)."""
    from mediaguard.health_summary import local

    try:
        h = local(ctx)
    except Exception:  # noqa: BLE001 - status mag hier nooit op falen
        return {"attention": _attention_summary(ctx), "health": None}
    return {"attention": h["attention"],
            "health": {"active": h["active"], "errors": h["errors"],
                       "warnings": h["warnings"], "encoder": h["encoder"].get("state"),
                       "complete": h["complete"]}}


class EmergencyBody(BaseModel):
    active: bool
    reason: str = Field(default="", max_length=500)


@router.post("/system/emergency-stop")
def emergency_stop(body: EmergencyBody, principal: Principal = Depends(require_operator),
                   ctx: AppContext = Depends(get_app_context)) -> dict[str, Any]:
    """Noodstop: blokkeert nieuwe grabs (via pre-grab hook), recovery-mutaties en encodes.
    Lopende muterende jobs stoppen bij hun eerstvolgende controlepunt."""
    old = ctx.state_get("emergency_stop", {"active": False})
    new: dict[str, Any] = {"active": body.active, "reason": body.reason,
                           "by": principal.username, "since": utcnow()}
    if old.get("active") and not body.active:
        # rapport 6: na opheffen laten zien wat er weer gaat lopen
        handoffs = ctx.db.fetch_one("SELECT COUNT(*) AS n FROM encoding_jobs WHERE "
                                    "status='awaiting_activation'")
        encodes = ctx.db.fetch_one("SELECT COUNT(*) AS n FROM encoding_jobs WHERE status IN "
                                   "('submitted','queued')")
        h, e = int(handoffs["n"] if handoffs else 0), int(encodes["n"] if encodes else 0)
        new["resumed"] = {"handoffs": h, "encodes": e}
        new["resumed_nl"] = (f"{h} overdracht{'en' if h != 1 else ''} en {e} "
                             f"encode{'s' if e != 1 else ''} worden hervat; downloads komen "
                             "de eerste 2 uur rustig op gang (max. 10 per 5 minuten per "
                             "instantie)")
    ctx.state_set("emergency_stop", new)
    from mediaguard.hookguard import write_flag

    write_flag(ctx)  # hookwacht: noodstop blijft gelden als core straks herstart
    audit(ctx.db, actor=principal.actor, action="system.emergency_stop", reason=body.reason,
          old_value=old, new_value=new, undo_hint="Zet de noodstop weer uit")
    ctx.events.publish("system.emergency_stop", new)
    runner = ctx.services.get("jobs")
    if runner is not None:
        runner.wake()
    return new


@router.get("/audit")
def audit_list(limit: int = Query(200, le=2000), offset: int = 0,
               instance_id: str | None = None, action: str | None = None,
               _: Principal = Depends(require_viewer),
               ctx: AppContext = Depends(get_app_context)) -> list[dict[str, Any]]:
    sql = "SELECT * FROM audit_events WHERE 1=1"
    params: list[Any] = []
    if instance_id:
        sql += " AND instance_id=?"
        params.append(instance_id)
    if action:
        sql += " AND action LIKE ?"
        params.append(action + "%")
    rows = ctx.db.fetch_dicts(sql + " ORDER BY id DESC LIMIT ? OFFSET ?", [*params, limit, offset])
    from mediaguard.audit_labels import label_row

    for row in rows:
        row["old_value"] = loads(row["old_value"])
        row["new_value"] = loads(row["new_value"])
        label_row(row)
    return rows


@router.get("/jobs")
def job_list(status: str | None = None, job_type: str | None = None,
             instance_id: str | None = None, limit: int = Query(200, le=1000),
             unresolved: bool = False,
             _: Principal = Depends(require_viewer),
             ctx: AppContext = Depends(get_app_context)) -> list[dict[str, Any]]:
    sql = "SELECT * FROM jobs WHERE 1=1"
    params: list[Any] = []
    if unresolved:
        sql += " AND resolved_at IS NULL"
    for col, val in (("status", status), ("job_type", job_type), ("instance_id", instance_id)):
        if val:
            sql += f" AND {col}=?"
            params.append(val)
    rows = ctx.db.fetch_dicts(sql + " ORDER BY id DESC LIMIT ?", [*params, limit])
    for row in rows:
        row["payload"] = loads(row["payload"], {})
        row["result"] = loads(row["result"])
    return rows


@router.get("/jobs/{job_id}")
def job_detail(job_id: int, _: Principal = Depends(require_viewer),
               ctx: AppContext = Depends(get_app_context)) -> dict[str, Any]:
    row = ctx.db.fetch_one("SELECT * FROM jobs WHERE id=?", (job_id,))
    if row is None:
        raise HTTPException(404, "Job niet gevonden")
    job = dict(row)
    job["payload"] = loads(job["payload"], {})
    job["result"] = loads(job["result"])
    # rapport 31: een job met tienduizenden logregels mag core niet ophouden; de nieuwste
    # regels tellen (oudste eerst weergegeven)
    logs = ctx.db.fetch_dicts(
        "SELECT ts,level,message FROM job_logs WHERE job_id=? ORDER BY id DESC LIMIT ?",
        (job_id, JOB_LOG_LIMIT + 1))
    job["logs_truncated"] = len(logs) > JOB_LOG_LIMIT
    job["logs"] = list(reversed(logs[:JOB_LOG_LIMIT]))
    return job


JOB_LOG_LIMIT = 2000


@router.post("/jobs/{job_id}/cancel")
def job_cancel(job_id: int, principal: Principal = Depends(require_operator),
               ctx: AppContext = Depends(get_app_context)) -> dict[str, bool]:
    ok = jobs.cancel(ctx.db, job_id)
    if ok:
        audit(ctx.db, actor=principal.actor, action="job.cancel", target_type="job",
              target_id=job_id)
    return {"cancelled": ok}


@router.post("/jobs/{job_id}/retry")
def job_retry(job_id: int, principal: Principal = Depends(require_operator),
              ctx: AppContext = Depends(get_app_context)) -> dict[str, bool]:
    n = ctx.db.execute(
        "UPDATE jobs SET status='queued', run_after=NULL, attempts=0, error=NULL, "
        "resolved_at=NULL, resolved_reason=NULL "
        "WHERE id=? AND status IN ('failed','cancelled','retry_scheduled')", (job_id,))
    if n:
        audit(ctx.db, actor=principal.actor, action="job.retry", target_type="job",
              target_id=job_id)
        runner = ctx.services.get("jobs")
        if runner is not None:
            runner.wake()
    return {"queued": n > 0}


@router.post("/jobs/{job_id}/ignore")
def job_ignore(job_id: int, principal: Principal = Depends(require_operator),
               ctx: AppContext = Depends(get_app_context)) -> dict[str, bool]:
    """Mislukte job negeren: telt niet meer mee in 'Actie nodig' en de foutbadge."""
    n = ctx.db.execute("UPDATE jobs SET resolved_at=?, resolved_reason='genegeerd' WHERE id=? "
                       "AND status='failed' AND resolved_at IS NULL", (utcnow(), job_id))
    if n:
        audit(ctx.db, actor=principal.actor, action="job.ignore", target_type="job",
              target_id=job_id)
    return {"ignored": n > 0}


@router.post("/jobs/ignore-failed")
def job_ignore_all(principal: Principal = Depends(require_operator),
                   ctx: AppContext = Depends(get_app_context)) -> dict[str, int]:
    n = ctx.db.execute("UPDATE jobs SET resolved_at=?, resolved_reason='genegeerd' WHERE "
                       "status='failed' AND resolved_at IS NULL", (utcnow(),))
    if n:
        audit(ctx.db, actor=principal.actor, action="job.ignore_all", target_type="job",
              new_value={"count": n})
    return {"ignored": n}


SUMMARY_CHARS = 120


def _summary(text: str | None) -> str:
    t = " ".join(str(text or "").split())
    return t if len(t) <= SUMMARY_CHARS else t[:SUMMARY_CHARS - 1].rstrip() + "…"


@router.get("/notifications")
def notifications(unread_only: bool = False, limit: int = 50, before_id: int | None = None,
                  full: bool = False, _: Principal = Depends(require_viewer),
                  ctx: AppContext = Depends(get_app_context)) -> list[dict[str, Any]]:
    """Gepagineerd (``limit`` + ``before_id``), nieuwste eerst. Elke melding heeft een
    ``summary`` van max. 120 tekens; de volledige ``body`` alleen met ``full=true``."""
    limit = max(1, min(int(limit), 200))
    where, params = [], []
    if unread_only:
        where.append("read=0")
    if before_id:
        # op tijd sorteren: een samengevoegde melding krijgt een nieuwe ts maar houdt zijn id
        # ('4 uur geleden' stond boven '6 minuten geleden', rapport 4 S14)
        ref = ctx.db.fetch_one("SELECT ts FROM notifications WHERE id=?", (int(before_id),))
        if ref is not None:
            where.append("(ts<? OR (ts=? AND id<?))")
            params.extend([ref["ts"], ref["ts"], int(before_id)])
        else:
            where.append("id<?")
            params.append(int(before_id))
    sql = ("SELECT id,ts,severity,title,body,link,instance_id,read,count,dedupe_key,"
           "resolved_at FROM notifications")
    if where:
        sql += " WHERE " + " AND ".join(where)
    rows = ctx.db.fetch_dicts(sql + " ORDER BY ts DESC, id DESC LIMIT ?", [*params, limit])
    from mediaguard.textfmt import plain_fields

    for r in rows:
        plain_fields(r, "title", "body")
        r["summary"] = _summary(r["body"])
        r["has_more_text"] = len(str(r["body"] or "")) > SUMMARY_CHARS
        if not full:
            r["body"] = r["summary"]
    return rows


@router.get("/notifications/count")
def notifications_count(_: Principal = Depends(require_viewer),
                        ctx: AppContext = Depends(get_app_context)) -> dict[str, int]:
    """Goedkoop badge-getal (geïndexeerd), los van het laden van de lijst.

    ``badge`` = ongelezen meldingen + punten die op de gebruiker wachten, zodat het belletje
    niet leeg is terwijl 'Aandacht 9' rood staat (rapport 5, T12)."""
    import time

    row = ctx.db.fetch_one("SELECT COUNT(*) AS n FROM notifications WHERE read=0")
    unread = int(row["n"]) if row else 0
    cached = ctx.services.get("attention_count_cache")
    if cached and time.monotonic() - cached[0] < 15:
        attention = int(cached[1])
    else:
        from mediaguard import attention as attention_mod

        try:
            attention = int(attention_mod.summary(ctx)["count"])
        except Exception:  # noqa: BLE001 - badge blijft werken
            attention = 0
        ctx.services["attention_count_cache"] = (time.monotonic(), attention)
    return {"unread": unread, "attention": attention, "badge": unread + attention}


@router.get("/notifications/{notification_id}")
def notification_detail(notification_id: int, _: Principal = Depends(require_viewer),
                        ctx: AppContext = Depends(get_app_context)) -> dict[str, Any]:
    row = ctx.db.fetch_one("SELECT * FROM notifications WHERE id=?", (notification_id,))
    if row is None:
        raise HTTPException(404, "Melding niet gevonden")
    from mediaguard.textfmt import plain_fields

    return plain_fields(dict(row), "title", "body")


@router.post("/notifications/read-all")
def notifications_read(_: Principal = Depends(require_viewer),
                       ctx: AppContext = Depends(get_app_context)) -> dict[str, int]:
    return {"updated": ctx.db.execute("UPDATE notifications SET read=1 WHERE read=0")}


def notify(ctx: AppContext, severity: str, title: str, body: str = "", *,
           link: str | None = None, instance_id: str | None = None,
           dedupe_key: str | None = None, group_title: str | None = None,
           category: str | None = None) -> int:
    """Nieuwe melding, of een bestaande ongelezen gelijke melding ophogen (teller + nieuwste
    tekst). Gelijk = zelfde ``dedupe_key`` (standaard: ernst + titel + pagina zonder query).
    Ruwe structuren in titel/tekst worden leesbaar gemaakt (rapport 3, R15).
    ``group_title`` vervangt de titel zodra er meer dan één is ('{n} originelen: …').
    ``category`` kiest de meldingssoort (bv. 'errors') in plaats van die af te leiden."""
    from mediaguard.textfmt import plain_nl

    title, body = plain_nl(title), plain_nl(body)
    key = dedupe_key or f"{severity}|{title}|{(link or '').split('?', 1)[0]}"
    row = ctx.db.fetch_one("SELECT id, count, link FROM notifications WHERE dedupe_key=? AND "
                           "read=0 AND resolved_at IS NULL ORDER BY id DESC LIMIT 1", (key,))
    read = 1 if severity in ("info", "success") else 0
    if row is not None and not read:
        n = int(row["count"] or 1) + 1
        same_link = row["link"] == link
        ctx.db.execute(
            "UPDATE notifications SET ts=?, count=?, body=?, title=?, link=?, severity=? "
            "WHERE id=?", (utcnow(), n, body, (group_title or title).replace("{n}", str(n)),
                           link if same_link else (link or "").split("?", 1)[0] or None,
                           severity, row["id"]))
        nid = int(row["id"])
        is_new = False
    else:
        # info/succes komen in de lijst maar tellen niet mee in de badge (direct gelezen)
        nid = ctx.db.execute_insert(
            "INSERT INTO notifications(ts,severity,title,body,link,instance_id,read,count,"
            "dedupe_key) VALUES (?,?,?,?,?,?,?,1,?)",
            (utcnow(), severity, title, body, link, instance_id, read, key))
        is_new = True
    if "notify_channels_ready" in ctx.services:  # buiten de browser (rapport 5, M1)
        from mediaguard.notify.service import on_notification

        on_notification(ctx, nid, severity, title, body, link, is_new=is_new, dedupe_key=key,
                        category=category)
    ctx.events.publish("notification", {"severity": severity, "title": title, "body": body,
                                        "link": link}, instance_id=instance_id, persist=False)
    return nid


def resolve_notifications(ctx: AppContext, dedupe_key: str) -> int:
    """Melding vanzelf opgelost (bv. 'Inrichting wijkt af' terwijl alles weer klopt)."""
    return ctx.db.execute("UPDATE notifications SET read=1, resolved_at=? WHERE dedupe_key=? "
                          "AND resolved_at IS NULL", (utcnow(), dedupe_key))


_ = require_admin  # beschikbaar voor modules die dit bestand importeren
