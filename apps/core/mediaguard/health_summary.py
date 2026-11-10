"""Eén gezondheidsmodel voor de hele UI (rapport 4 nov, B2/H8).

Topbalk ('N actief'), zijbalk, Overzicht en Activiteit lazen elk hun eigen tellers
(alleen MediaGuard-taken, alleen downloads, …) en spraken elkaar tegen: '0 actief' terwijl er
downloads en encodes liepen, 'Fouten 0' terwijl zes AV1-vervangingen mislukt waren. Dit
endpoint is de enige bron:

* ``active`` = downloads + zoekopdrachten + encodes + MediaGuard-taken;
* ``errors`` = alles wat mis is en niet vanzelf weggaat: 'Wacht op jou'-fouten, AV1 dat niet
  geactiveerd kon worden, encodes die mislukten of de verificatie niet haalden, mislukte
  vrijgavecontroles, mislukte taken, offline instanties en een AV1-engine die niet (volledig)
  werkt;
* ``attention`` = dezelfde tellingen als ``/api/v1/attention`` (zelfde bronnen).
"""

from __future__ import annotations

import asyncio
import time
from typing import Any

from fastapi import APIRouter, Depends

from mediaguard.auth import Principal, require_viewer
from mediaguard.context import AppContext, get_app_context
from mediaguard.db import utcnow

router = APIRouter(prefix="/api/v1/health", tags=["health"])
CACHE_SECONDS = 12.0
ENCODER_CACHE_SECONDS = 15.0
# 'bezig' = echt aan het encoderen/controleren, zoals 'Nu bezig' het toont (rapport 3, R13);
# aangemelde/wachtende jobs staan in de wachtrij, niet in 'bezig'
ENC_RUNNING = ("running", "verifying")


def _count(ctx: AppContext, sql: str, params: tuple[Any, ...] = ()) -> int:
    row = ctx.db.fetch_one(sql, params)
    return int(row[0]) if row else 0


async def _encoder_state(ctx: AppContext) -> dict[str, Any]:
    cached = ctx.services.get("health_summary_encoder")
    if cached and time.monotonic() - cached[0] < ENCODER_CACHE_SECONDS:
        return cached[1]  # type: ignore[no-any-return]
    from mediaguard.encoding.client import (
        EncoderError,
        get_encoder_client,
        note_encoder_reachability,
    )

    try:
        h = await asyncio.wait_for(get_encoder_client(ctx).health(), 10)
        note_encoder_reachability(ctx, True)
        state = encoder_state_from_health(h)
    except (EncoderError, TimeoutError, OSError) as exc:
        last = ctx.services.get("health_summary_encoder_last_ok")
        if not note_encoder_reachability(ctx, False) and last is not None:
            state = last  # laatste bekende toestand houden tot de engine echt weg is
        else:
            state = {"state": "offline", "reasons": [f"AV1-engine reageert niet ({exc})"],
                     "lanes_paused": 0}
        ctx.services["health_summary_encoder"] = (time.monotonic(), state)
        return state
    ctx.services["health_summary_encoder_last_ok"] = state
    ctx.services["health_summary_encoder"] = (time.monotonic(), state)
    return state


def encoder_state_from_health(h: dict[str, Any]) -> dict[str, Any]:
    """'ok' of 'degraded' uit de engine-health. Een pauze omdat iemand kijkt, een eigen
    pauze, stille uren en de noodstop zijn bewuste keuzes: geen fout, geen rode Overzicht
    (verbeterlijst 8). Ze staan apart in ``waiting`` zodat de UI kan zeggen waarom AV1 wacht."""
    from mediaguard.attention import BENIGN_PAUSES, pause_kind

    paused = [p for p in (h.get("lanes_paused") or []) if isinstance(p, dict)]
    # geen (Intel-)GPU is een geldige CPU-only-opstelling: de engine zet dat in 'notes'
    no_gpu = any(str(n).lower().startswith("gpu-baan uit") for n in h.get("notes") or [])
    paused = [p for p in paused if not (no_gpu and p.get("lane") == "gpu"
                                        and str(p.get("reason") or "").startswith("MediaGuard:"))]
    problems = [p for p in paused if pause_kind(p.get("reason")) not in BENIGN_PAUSES]
    waiting = [f"{str(p.get('lane') or '?').upper()}-baan: {p.get('reason')}"
               for p in paused if p not in problems]
    reasons = [f"{str(p.get('lane') or '?').upper()}-baan: {p.get('reason')}"
               for p in problems]
    other = [str(r) for r in h.get("reasons") or []]
    # oudere engines melden ook bewuste pauzes als reden ('cpu-baan gepauzeerd: Kijken: …')
    real = [r for r in other
            if pause_kind(r.split("gepauzeerd: ", 1)[1] if "gepauzeerd: " in r else "x")
            not in BENIGN_PAUSES]
    reasons += [r for r in real if r not in reasons]
    status = str(h.get("status") or "ok").lower()
    unexplained = status not in ("ok", "healthy", "gezond") and not other
    return {"state": "degraded" if problems or real or unexplained else "ok",
            "reasons": reasons, "lanes_paused": len(problems), "waiting": waiting}


# Een uitgestelde activatie telt alleen als fout als de oorzaak in de omgeving zit
# (rechten/opslag); de rest is een wachtstand die vanzelf verdergaat (rapport 6, N5).
REAL_DEFERRAL = ("(j.error NOT LIKE 'Activatie uitgesteld:%' OR j.error LIKE '%ermission%' "
                 "OR j.error LIKE '%not permitted%' OR j.error LIKE '%EPERM%' OR "
                 "j.error LIKE '%EACCES%' OR j.error LIKE '%Errno%' OR j.error LIKE '%rechten%' "
                 "OR j.error LIKE '%opslag%' OR j.error LIKE '%No space%')")


def _av1_problems(ctx: AppContext) -> dict[str, int]:
    from mediaguard.encoding.autopilot import retry_states

    states = retry_states(ctx)
    # 'te traag' (kind slow) is een bewuste overslag die later opnieuw mag: geen fout
    failed = sum(1 for st in states.values() if st["state"] in ("final", "wait", "retry")
                 and st.get("kind") != "slow")
    final = sum(1 for st in states.values() if st["state"] == "final")
    # niet meer meetellen zodra hetzelfde bestand later wél geplaatst is (rapport 3, R4)
    # rapport 6 / N5: een tijdelijk uitgestelde activatie (download loopt nog, noodstop,
    # slot) is een wachtstand die vanzelf verdergaat, geen fout. Alleen een uitstel door
    # rechten/opslag (omgeving) telt als 'niet geplaatst'.
    deferred = _count(ctx, "SELECT COUNT(*) FROM encoding_jobs j WHERE "
                           "j.status='awaiting_activation' AND j.error IS NOT NULL AND "
                           "j.error<>'' AND " + REAL_DEFERRAL + " AND NOT EXISTS (SELECT 1 FROM "
                           "encoding_jobs k WHERE k.source_path=j.source_path AND k.id>j.id "
                           "AND k.status='committed')")
    waiting = _count(ctx, "SELECT COUNT(*) FROM encoding_jobs WHERE "
                          "status='awaiting_activation'") - deferred
    conflicts = _count(ctx, "SELECT COUNT(*) FROM encoding_jobs WHERE status='conflict_review'")
    release = _count(ctx, "SELECT COUNT(*) FROM quarantine_items q JOIN "
                          "quarantine_release_checks c ON c.item_id=q.id WHERE "
                          "q.status IN ('restorable','held','pending') AND c.status='failed'")
    return {"not_activated": deferred, "failed": failed, "failed_final": final,
            "conflicts": conflicts, "release_check_failed": release,
            "waiting_activation": max(0, waiting),
            # 'Mislukt' zoals de gebruiker het ziet: alles wat niet in de bibliotheek kwam
            # en niet vanzelf verdergaat. Eén definitie voor Overzicht, Ruimte en Problemen.
            "failed_total": deferred + failed + conflicts + release}


def compose(ctx: AppContext, act: dict[str, Any] | None, encoder: dict[str, Any] | None,
            encoder_items: list[dict[str, Any]] | None = None) -> dict[str, Any]:
    """Tellers uit lokale gegevens + (optioneel) de laatst opgehaalde activiteit/encoder."""
    from mediaguard import attention, jobs

    downloads = searches = download_warnings = 0
    instance_errors: list[dict[str, Any]] = []
    if act:
        s = act["summary"]
        downloads, searches = int(s["downloads"]), int(s["searches"])
        instance_errors = list(act["errors"])
        download_warnings = sum(1 for d in act["downloads"] if d.get("warning"))
    encoder = encoder or {"state": "unknown", "reasons": [], "lanes_paused": 0}
    marks = ",".join("?" for _ in ENC_RUNNING)
    encodes = _count(ctx, f"SELECT COUNT(*) FROM encoding_jobs WHERE status IN ({marks})",
                     ENC_RUNNING)
    # noodstop (punt 39): de encoder bevriest lopende encodes; dan zijn ze gepauzeerd, niet
    # 'bezig' (topbalk, Overzicht en de app toonden anders 'bezig' terwijl alles stilstaat)
    estop = ctx.emergency_stopped()
    paused_encodes = encodes if estop else 0
    if estop:
        encodes = 0
    # alleen wat nu echt draait: ingeplande taken staan niet in 'Nu bezig' (rapport 3, R13)
    tasks = _count(ctx, "SELECT COUNT(*) FROM jobs WHERE status='running'")
    items = attention.collect_local(ctx) + list(encoder_items or [])
    av1 = _av1_problems(ctx)
    att_errors = sum(1 for i in items if i["severity"] == "error")
    att_warnings = sum(1 for i in items if i["severity"] == "warning")
    breakdown = {
        "attention": att_errors,
        "av1_not_activated": av1["not_activated"],
        "av1_failed": av1["failed"],
        "av1_conflicts": av1["conflicts"],
        "release_check_failed": av1["release_check_failed"],
        "tasks_failed": jobs.unresolved_failures(ctx.db),
        "instances_unreachable": len(instance_errors),
        # 'offline' zit al in de attention-fouten (encoder.offline); 'degraded' telt apart
        "encoder_degraded": 1 if encoder.get("state") == "degraded" else 0,
    }
    return {
        "generated_at": utcnow(),
        "complete": act is not None,
        "active": {"total": downloads + searches + encodes + tasks, "downloads": downloads,
                   "searches": searches, "encodes": encodes, "tasks": tasks},
        "paused": {"encodes": paused_encodes, "emergency_stop": estop},
        "errors": {"total": sum(breakdown.values()), **breakdown},
        "warnings": {"total": att_warnings + download_warnings, "attention": att_warnings,
                     "downloads": download_warnings},
        "attention": {"count": len(items), "errors": att_errors, "warnings": att_warnings},
        "av1": av1,
        "encoder": encoder,
        "instances": {"unreachable": instance_errors},
    }


async def build(ctx: AppContext) -> dict[str, Any]:
    from mediaguard import attention
    from mediaguard.activity import api as activity

    act = await activity.activity_cached(ctx)
    encoder = await _encoder_state(ctx)
    try:
        enc_items = await attention._encoder(ctx)
    except Exception:  # noqa: BLE001 - encoder-bron optioneel
        enc_items = []
    # SQLite-werk buiten de event loop: andere verzoeken blijven snel
    return await asyncio.to_thread(compose, ctx, act, encoder, enc_items)


def local(ctx: AppContext) -> dict[str, Any]:
    """Zonder netwerk (voor /status): laatste bekende downloads/encoder uit de cache."""
    cached = ctx.services.get("health_summary_cache")
    if cached and time.monotonic() - cached[0] < 60:
        return cached[1]  # type: ignore[no-any-return]
    act = ctx.services.get("activity_cache")
    enc = ctx.services.get("health_summary_encoder")
    return compose(ctx, act[1] if act else None, enc[1] if enc else None)


@router.get("/summary")
async def summary(_: Principal = Depends(require_viewer),
                  ctx: AppContext = Depends(get_app_context)) -> dict[str, Any]:
    cached = ctx.services.get("health_summary_cache")
    if cached and time.monotonic() - cached[0] < CACHE_SECONDS:
        return cached[1]  # type: ignore[no-any-return]
    # rapport 2 / N25: gelijktijdige verzoeken delen één berekening (was 3,5 s per verzoek)
    pending = ctx.services.get("health_summary_inflight")
    if pending is not None and not pending.done():
        return await asyncio.shield(pending)  # type: ignore[no-any-return]
    task = asyncio.ensure_future(build(ctx))
    ctx.services["health_summary_inflight"] = task
    try:
        result = await task
    finally:
        ctx.services.pop("health_summary_inflight", None)
    ctx.services["health_summary_cache"] = (time.monotonic(), result)
    return result


ERROR_LABELS_NL = {
    "attention": "Meldingen die aandacht vragen",
    "av1_not_activated": "AV1-resultaat nog niet geplaatst",
    "av1_failed": "AV1-omzetting mislukt",
    "av1_conflicts": "AV1-conflict",
    "release_check_failed": "Vrijgavecontrole mislukt",
    "tasks_failed": "Mislukte taken (7 dagen)",
    "instances_unreachable": "Sonarr/Radarr onbereikbaar",
    "encoder_degraded": "AV1-engine verminderd",
}
ERROR_HREFS = {
    "attention": "/activiteit?tab=problemen",
    "av1_not_activated": "/av1/wachtrij?status=niet_geactiveerd",
    "av1_failed": "/av1/wachtrij?status=mislukt",
    "av1_conflicts": "/av1/wachtrij?status=conflict",
    "release_check_failed": "/quarantaine?check=failed",
    "tasks_failed": "/activiteit?tab=geschiedenis&status=failed",
    "instances_unreachable": "/instellingen/instanties",
    "encoder_degraded": "/av1",
}


def _name(path: Any) -> str:
    from pathlib import Path

    return Path(str(path or "")).name or str(path or "")


def error_items(ctx: AppContext, key: str, limit: int = 200) -> list[dict[str, Any]]:
    """Precies de items achter één foutteller (rapport 2 / N3, N13): titel, reden, link."""
    from mediaguard.db import loads
    from mediaguard.encoding.jobstates import engine_href

    if key == "av1_not_activated":
        rows = ctx.db.fetch_dicts(
            "SELECT id, source_path, error, encoder_job_id FROM encoding_jobs j WHERE "
            "status='awaiting_activation' AND error IS NOT NULL AND error<>'' AND "
            + REAL_DEFERRAL + " AND NOT EXISTS (SELECT 1 FROM encoding_jobs k WHERE "
            "k.source_path=j.source_path AND k.id>j.id AND k.status='committed') "
            "ORDER BY id DESC LIMIT ?", (limit,))
        return [{"id": r["id"], "title": _name(r["source_path"]), "reason_nl": r["error"],
                 "href": (f"/av1/wachtrij?job={r['encoder_job_id']}" if r["encoder_job_id"]
                          else "/av1/wachtrij")} for r in rows]
    if key == "av1_failed":
        from mediaguard.encoding.autopilot import retry_states

        out = []
        for path, st in retry_states(ctx).items():
            if st["state"] in ("final", "wait", "retry") and st.get("kind") != "slow":
                out.append({"id": st.get("job_id"), "title": _name(path),
                            "reason_nl": st.get("error") or "", "state": st["state"],
                            "attempts": st.get("attempts"),
                            "href": engine_href(ctx, st.get("job_id"))})
        return out[:limit]
    if key == "av1_conflicts":
        rows = ctx.db.fetch_dicts("SELECT id, source_path, error FROM encoding_jobs WHERE "
                                  "status='conflict_review' ORDER BY id DESC LIMIT ?", (limit,))
        return [{"id": r["id"], "title": _name(r["source_path"]), "reason_nl": r["error"] or "",
                 "href": engine_href(ctx, r["id"])} for r in rows]
    if key == "release_check_failed":
        from mediaguard.recovery.release_check import summary_nl

        rows = ctx.db.fetch_dicts(
            "SELECT q.id, q.original_path, c.checks_json FROM quarantine_items q JOIN "
            "quarantine_release_checks c ON c.item_id=q.id WHERE q.status IN ('restorable',"
            "'held','pending') AND c.status='failed' ORDER BY q.id DESC LIMIT ?", (limit,))
        return [{"id": r["id"], "title": _name(r["original_path"]),
                 "reason_nl": summary_nl(loads(r["checks_json"], [])),
                 "href": f"/quarantaine?item={r['id']}"} for r in rows]
    if key == "tasks_failed":
        from datetime import UTC, datetime, timedelta

        since = (datetime.now(UTC) - timedelta(days=7)).isoformat(timespec="seconds")
        rows = ctx.db.fetch_dicts(
            "SELECT id, job_type, error FROM jobs WHERE status='failed' AND resolved_at IS NULL "
            "AND COALESCE(finished_at, created_at)>=? ORDER BY id DESC LIMIT ?", (since, limit))
        return [{"id": r["id"], "title": r["job_type"], "reason_nl": r["error"] or "",
                 "href": f"/activiteit?tab=geschiedenis&job={r['id']}"} for r in rows]
    if key == "attention":
        from mediaguard import attention

        return [{"id": i["key"], "title": i["title_nl"], "reason_nl": i.get("detail_nl") or "",
                 "href": i["href"]} for i in attention.collect_local(ctx)
                if i["severity"] == "error"][:limit]
    return []


@router.get("/errors")
async def errors(_: Principal = Depends(require_viewer),
                 ctx: AppContext = Depends(get_app_context)) -> dict[str, Any]:
    """Waar 'Fouten N' uit bestaat: per onderdeel aantal, label, link en de items zelf."""
    s = await summary(_, ctx)
    groups = []
    for key, n in s["errors"].items():
        if key == "total" or not n:
            continue
        items = await asyncio.to_thread(error_items, ctx, key)
        groups.append({"key": key, "label_nl": ERROR_LABELS_NL.get(key, key), "count": n,
                       "href": ERROR_HREFS.get(key, "/activiteit?tab=problemen"),
                       "items": items})
    return {"total": s["errors"]["total"], "groups": groups}
