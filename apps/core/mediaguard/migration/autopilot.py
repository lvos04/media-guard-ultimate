"""Migratie zonder klikken per stap: MediaGuard loopt de stappen die geen menselijke keuze
vragen zelf door (controle vooraf, back-up, proefimport, vergelijken, scan, gereedheid).

Het stopt alleen waar jij iets moet beslissen of bekijken:
- de koppeling bron → doel bevestigen;
- een back-up laten maken op de oude installatie (die heeft er geen);
- blokkerende verschillen of een geblokkeerde stap;
- de overstap zelf (blijft altijd een bevestigde actie per instantie, nooit automatisch).

De voortgang staat in ``state['migration_autopilot:<run>']`` en alle echte werk gaat via
dezelfde stapfuncties en poorten als de handmatige wizard (idempotent en hervatbaar: een
herstart pakt de job weer op en slaat afgeronde stappen over).
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

from mediaguard import taskwatch
from mediaguard.db import utcnow
from mediaguard.migration import store
from mediaguard.migration.snapshot import verdict

if TYPE_CHECKING:
    from mediaguard.context import AppContext

JOB_TYPE = "migration.autopilot"
TARGET_STEPS = ("shadow_import", "verify", "scan", "readiness")
LABELS = {"preflight": "Controle vooraf", "backup": "Back-up maken en controleren",
          "mapping": "Koppeling bevestigen",
          "shadow_import": "Proefimport (nieuwe installatie downloadt nog niets)",
          "verify": "Vergelijken met je oude installatie", "scan": "Bibliotheek controleren",
          "readiness": "Klaar voor overschakelen?", "cutover": "Overschakelen"}


def _key(run_id: int) -> str:
    return f"migration_autopilot:{run_id}"


def state(ctx: AppContext, run_id: int) -> dict[str, Any]:
    return ctx.state_get(_key(run_id), {}) or {}


def _save(ctx: AppContext, run_id: int, **fields: Any) -> dict[str, Any]:
    cur = {**state(ctx, run_id), **fields, "updated_at": utcnow()}
    ctx.state_set(_key(run_id), cur)
    ctx.events.publish("migration.autopilot", {"run_id": run_id, "state": cur.get("state")})
    return cur


def _done(st: dict[str, Any] | None) -> bool:
    return bool(st) and st["status"] in store.DONE  # type: ignore[index]


def _active_maps(ctx: AppContext, run_id: int) -> list[dict[str, Any]]:
    return [m for m in store.mappings(ctx, run_id) if m["status"] != "excluded"]


def plan(ctx: AppContext, run_id: int) -> list[dict[str, Any]]:
    """Alle stappen in volgorde met hun status (voor 'stap 4 van 9')."""
    out: list[dict[str, Any]] = []

    def add(step: str, scope: str, label: str) -> None:
        st = store.step_get(ctx, run_id, step, scope)
        out.append({"step": step, "scope": scope, "label": label,
                    "status": st["status"] if st else "pending",
                    "error": st.get("error") if st else None})

    maps = _active_maps(ctx, run_id)
    add("preflight", "", LABELS["preflight"])
    for m in maps:
        add("backup", f"source:{m['source_id']}", f"{LABELS['backup']} ({m['source_id']})")
    add("mapping", "", LABELS["mapping"])
    for m in maps:
        target = m["target_instance_id"]
        if not target:
            continue
        for step in TARGET_STEPS:
            add(step, target, f"{LABELS[step]} · {target}")
        # de overstap zelf telt mee: geen '22 van 22 klaar' zolang er nog overgeschakeld
        # moet worden (rapport 4 nov, H13). Blijft altijd een bevestigde gebruikersactie.
        from mediaguard.migration.cutover import latest_cutover

        co = latest_cutover(ctx, run_id, target)
        status = {"completed": "succeeded", "running": "running", "failed": "failed",
                  "rolled_back": "pending"}.get(str(co["status"]), "pending") if co else \
            "pending"
        out.append({"step": "cutover", "scope": target,
                    "label": f"{LABELS['cutover']} · {target}", "status": status,
                    "error": co.get("error") if co else None, "user_action": True})
    return out


def _need(kind: str, message_nl: str, *, target: str | None = None,
          action: str | None = None, **extra: Any) -> dict[str, Any]:
    return {"kind": kind, "message_nl": message_nl, "target": target,
            "action": action or kind, **extra}


async def advance(ctx: AppContext, run_id: int, actor: str, log: Any) -> dict[str, Any]:  # noqa: C901
    """Voer alle stappen uit die nu zonder keuze kunnen; geef de eindtoestand terug."""
    from mediaguard.jobs import RetryLater
    from mediaguard.migration.api import _execute_step

    run = store.run_row(ctx, run_id)
    if run is None or run["status"] != "active":
        return _save(ctx, run_id, state="stopped",
                     needs=[_need("stopped", "Deze migratie is gestopt of afgerond.")])
    opts = state(ctx, run_id)
    _save(ctx, run_id, state="running", needs=[], started_at=utcnow())
    tried: set[tuple[str, str]] = set()

    async def run_step(step: str, scope: str, params: dict[str, Any]) -> dict[str, Any]:
        tried.add((step, scope))
        _save(ctx, run_id, current={"step": step, "scope": scope,
                                    "label": LABELS.get(step, step)})
        log(f"{LABELS.get(step, step)} {scope}".strip())
        return await _execute_step(ctx, run_id, step, scope, params, actor, log)

    if ctx.emergency_stopped():
        return _save(ctx, run_id, state="needs_input", current=None, needs=[
            _need("emergency", "Noodstop actief: zet die uit om verder te gaan.")])
    maps = _active_maps(ctx, run_id)
    if not maps:
        return _save(ctx, run_id, state="needs_input", current=None, needs=[
            _need("sources", "Kies eerst welke oude installaties je wilt overzetten.")])

    # 1. controle vooraf (alleen fouten stoppen; waarschuwingen staan in het rapport)
    if not _done(store.step_get(ctx, run_id, "preflight", "")):
        out = await run_step("preflight", "", {})
        if out["status"] not in store.DONE:
            return _save(ctx, run_id, state="needs_input", current=None, needs=[_need(
                "preflight", "De controle vooraf vond een probleem: "
                f"{out.get('error') or 'bekijk het rapport bij Controle vooraf'}. Los het op "
                "en druk op Doorgaan.", action="open_preflight")])

    # 2. back-ups (bestaande; zelf laten maken alleen na jouw akkoord)
    for m in maps:
        scope = f"source:{m['source_id']}"
        if _done(store.step_get(ctx, run_id, "backup", scope)):
            continue
        params = {"strategy": "existing"}
        if opts.get("allow_backup_create"):
            params = {"strategy": "create", "confirm_create": True}
        out = await run_step("backup", scope, params)
        if out["status"] not in store.DONE:
            err = str(out.get("error") or "")
            if "Geen back-up" in err and not opts.get("allow_backup_create"):
                return _save(ctx, run_id, state="needs_input", current=None, needs=[_need(
                    "backup_create", f"Op {m['source_id']} staat nog geen back-up. Mag "
                    "MediaGuard daar een back-up laten maken? Dat verandert niets aan je "
                    "oude installatie.", target=m["source_id"], action="allow_backup_create")])
            return _save(ctx, run_id, state="needs_input", current=None, needs=[_need(
                "backup", f"Back-up van {m['source_id']} lukte niet: {err}",
                target=m["source_id"], action="open_backup")])

    # 3. koppeling: altijd door jou bevestigd
    if not _done(store.step_get(ctx, run_id, "mapping", "")) or \
            any(m["status"] != "confirmed" for m in maps):
        return _save(ctx, run_id, state="needs_input", current=None, needs=[_need(
            "mapping", "Controleer welke oude installatie naar welke nieuwe gaat en bevestig "
            "de koppeling.", action="open_mapping")])

    # 4. per doel: proefimport → vergelijken → scan → gereedheid
    needs: list[dict[str, Any]] = []
    waiting = False
    for m in maps:
        target = m["target_instance_id"]
        if not target:
            continue
        stop = False
        for step in TARGET_STEPS:
            cur = store.step_get(ctx, run_id, step, target)
            if step == "verify" and _done(cur):
                rep = store.latest_report(ctx, run_id, "verify", target)
                live = verdict(rep["mismatches"], store.accepted_ids(ctx, run_id, target)) \
                    if rep else "no_go"
                if live == "no_go":
                    cur = None  # opnieuw vergelijken (na een herstel van de bron)
            if step == "scan" and _done(cur):
                from mediaguard.migration.wizard import SCAN_MAX_AGE_HOURS, _age_hours

                rep = store.latest_report(ctx, run_id, "scan", target)
                if rep is None or _age_hours(rep.get("created_at")) > SCAN_MAX_AGE_HOURS:
                    cur = None  # rapport 3 / §6: verouderde scan zelf opnieuw (alleen lezen)
            if step == "readiness" and _done(cur):
                rep = store.latest_report(ctx, run_id, "readiness", target)
                ver = store.latest_report(ctx, run_id, "verify", target)
                if rep and ver and int(ver["id"]) > int(rep["id"]):
                    cur = None  # vergelijking is nieuwer: gereedheid opnieuw bepalen
            if _done(cur) and step != "shadow_import":
                continue
            if step == "shadow_import" and _done(cur):
                if cur and cur["status"] == "warning" and \
                        (cur.get("result") or {}).get("pending_tvdb") and \
                        (step, target) not in tried:
                    pass  # nog afleveringen ophalen: hervatten
                else:
                    continue
            if (step, target) in tried:
                stop = True
                break
            params: dict[str, Any] = {}
            if step == "verify":
                params = {"sync_delta": True}
            elif step == "readiness":
                params = {"register_webhook": True}
            try:
                out = await run_step(step, target, params)
            except RetryLater:
                waiting = True
                stop = True
                break
            res = out.get("result") or {}
            if step == "shadow_import" and out["status"] == "warning" and \
                    res.get("pending_tvdb"):
                waiting = True
                stop = True
                break
            if out["status"] in store.DONE:
                continue
            msg = out.get("error") or res.get("message_nl") or ""
            kind = {"shadow_import": "shadow", "verify": "verify", "scan": "scan",
                    "readiness": "readiness"}[step]
            text = {
                "shadow": f"Proefimport naar {target} kon niet verder: {msg}",
                "verify": f"{target} wijkt op belangrijke punten af van je oude installatie. "
                          "Bekijk het vergelijkingsrapport.",
                "scan": f"Bij de controle van {target} ontbreken titels: bekijk het "
                        "scanrapport.",
                "readiness": f"{target} is nog niet klaar om over te schakelen: bekijk wat "
                             "er blokkeert.",
            }[kind]
            needs.append(_need(kind, text, target=target, action=f"open_{step}",
                               detail=msg or None))
            stop = True
            break
        if not stop:
            continue
    if waiting and not needs:
        _save(ctx, run_id, state="waiting", current=None, needs=[_need(
            "waiting", "De nieuwe installatie haalt nog afleveringsgegevens op; MediaGuard gaat "
            "over een paar minuten vanzelf verder.", action="none")])
        raise RetryLater("wacht op afleveringsgegevens", 120)
    if needs:
        return _save(ctx, run_id, state="needs_input", current=None, needs=needs)
    ready = [m["target_instance_id"] for m in maps if m["target_instance_id"]]
    return _save(ctx, run_id, state="ready_for_cutover", current=None, needs=[_need(
        "cutover", "Alles is gecontroleerd. Schakel per instantie over wanneer het jou "
        "uitkomt; je oude installatie blijft tot dan gewoon werken.", action="open_cutover",
        targets=ready)])


def enqueue(ctx: AppContext, run_id: int, actor: str, **options: Any) -> int:
    """Start (of hervat) de automatische doorloop. Bestaat er al een actieve, dan die."""
    from mediaguard import jobs

    if options:
        _save(ctx, run_id, **options)
    row = ctx.db.fetch_one(
        "SELECT id FROM jobs WHERE job_type=? AND target_id=? AND status IN "
        "('queued','running','retry_scheduled') ORDER BY id DESC LIMIT 1",
        (JOB_TYPE, str(run_id)))
    if row is not None:
        return int(row["id"])
    _save(ctx, run_id, state="queued", needs=[])
    job_id = jobs.enqueue(ctx.db, JOB_TYPE, {"run_id": run_id, "actor": actor},
                          target_type="migration_run", target_id=run_id, created_by=actor,
                          max_attempts=1000)
    runner = ctx.services.get("jobs")
    if runner is not None:
        runner.wake()
    return job_id


def view(ctx: AppContext, run_id: int) -> dict[str, Any]:
    steps = plan(ctx, run_id)
    done = sum(1 for s in steps if s["status"] in store.DONE)
    st = state(ctx, run_id)
    left = [s for s in steps if s["status"] not in store.DONE]
    cutovers = [s["scope"] for s in left if s["step"] == "cutover"]
    if not left:
        headline = "Migratie klaar: alle instanties zijn overgeschakeld."
    elif len(left) == len(cutovers):
        names = ", ".join(cutovers)
        headline = (f"Nog {len(cutovers)} stap{'pen' if len(cutovers) > 1 else ''}: "
                    f"overschakelen ({names}). Je oude installatie blijft tot dan werken; "
                    "terugdraaien kan daarna nog.")
    else:
        headline = _blocking_sentence(ctx, run_id, [s for s in left if s["step"] != "cutover"],
                                      len(cutovers))
    return {"state": st.get("state") or "idle", "current": st.get("current"),
            "needs": st.get("needs") or [], "allow_backup_create":
            bool(st.get("allow_backup_create")), "updated_at": st.get("updated_at"),
            "steps": steps, "done": done, "total": len(steps),
            "remaining": len(left), "cutover_pending": cutovers, "headline_nl": headline}


def _instance_name(ctx: AppContext, iid: str) -> str:
    row = ctx.db.fetch_one("SELECT name FROM instances WHERE id=?", (iid,))
    return str(row["name"]) if row and row["name"] else iid


def _blocking_sentence(ctx: AppContext, run_id: int, open_steps: list[dict[str, Any]],
                       cutovers: int) -> str:
    """Eén zin: wat er nog nodig is, met de exacte titels (rapport 5, M14)."""
    if not open_steps:
        return f"Nog {cutovers} stap{'pen' if cutovers != 1 else ''}: overschakelen."
    first = open_steps[0]
    target = str(first.get("scope") or "")
    name = _instance_name(ctx, target) if target and ":" not in target else ""
    label = LABELS.get(str(first["step"]), str(first["step"]))
    detail = ""
    if first["step"] in ("scan", "readiness") and target:
        rep = store.latest_report(ctx, run_id, "scan", target)
        summary = (rep or {}).get("summary") or {}
        titles = [str(t) for t in summary.get("missing_titles") or []]
        if titles:
            shown = ", ".join(titles[:5]) + (f" en nog {len(titles) - 5}" if len(titles) > 5
                                              else "")
            detail = (f": {len(titles)} titel{'s' if len(titles) != 1 else ''} "
                      f"ontbre{'ken' if len(titles) != 1 else 'ekt'} in de nieuwe installatie "
                      f"({shown})")
        elif summary.get("files_missing_nl"):
            detail = ": bestanden nog niet allemaal gekoppeld"
    elif first.get("error"):
        detail = f": {str(first['error'])[:160]}"
    who = f"{name} — " if name else ""
    rest = len(open_steps) - 1
    more = f" Daarna nog {rest} andere stap{'pen' if rest != 1 else ''}." if rest > 0 else ""
    return (f"Nog nodig: {who}{label.lower()}{detail}. MediaGuard probeert dit zelf opnieuw "
            f"(scan elk uur).{more} Daarna kun je overschakelen.")


# -- verouderde scans zelf vernieuwen (rapport 3, §6) -----------------------------------------
STALE_CHECK_SECONDS = 3600


def stale_runs(ctx: AppContext) -> list[int]:
    """Actieve migraties met een scan die ouder is dan de grens (alleen lezen opnieuw)."""
    from mediaguard.migration.wizard import SCAN_MAX_AGE_HOURS, _age_hours

    out = []
    for r in ctx.db.fetch_all("SELECT id FROM migration_runs WHERE status='active'"):
        run_id = int(r["id"])
        for m in _active_maps(ctx, run_id):
            target = m.get("target_instance_id")
            if not target or not _done(store.step_get(ctx, run_id, "scan", target)):
                continue
            rep = store.latest_report(ctx, run_id, "scan", target)
            if rep is None or _age_hours(rep.get("created_at")) > SCAN_MAX_AGE_HOURS:
                out.append(run_id)
                break
    return out


async def _stale_loop(ctx: AppContext) -> None:
    import asyncio
    import logging

    log = logging.getLogger(__name__)
    await asyncio.sleep(300)
    while True:
        try:
            for run_id in stale_runs(ctx):
                enqueue(ctx, run_id, "svc:migration-autopilot")
            taskwatch.ok(ctx, "migration_stale")
        except Exception as exc:  # noqa: BLE001 - lus nooit laten sterven
            log.exception("verouderde migratiescans vernieuwen mislukt")
            taskwatch.fail(ctx, "migration_stale", exc)
        await asyncio.sleep(STALE_CHECK_SECONDS)


async def startup(ctx: AppContext) -> None:

    if ctx.settings.start_background and ctx.settings.role in ("core", "all"):
        ctx.services["migration_stale_scan_task"] = taskwatch.spawn(
            ctx, "migration_stale", _stale_loop, label_nl="Migratiescans vernieuwen",
            every=STALE_CHECK_SECONDS)


async def shutdown(ctx: AppContext) -> None:
    import asyncio
    import contextlib

    task = ctx.services.pop("migration_stale_scan_task", None)
    if task is not None:
        task.cancel()
        with contextlib.suppress(asyncio.CancelledError):
            await task
