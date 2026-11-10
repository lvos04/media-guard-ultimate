"""Historical Backfill Scheduler (D3), per instantie met eigen quota.

Eigenschappen:
- Alleen kleine batches ``EpisodeSearch``/``SeasonSearch``/``MoviesSearch``-commando's; RSS
  blijft volledig bij Sonarr/Radarr en wordt nooit geblokkeerd. Draait er al een
  zoekcommando op de instantie, dan slaat de scheduler de ronde over.
- Quota per instantie: max zoekopdrachten per uur en per dag + batchgrootte. Handmatige
  'Zoek nu' en interactieve diagnoses tellen mee; limieten worden nooit omzeild.
- Per item exponentiële backoff, 'laatst gezocht', dedup (één open poging per item,
  niets zoeken wat al in de wachtrij staat) en na N mislukte pogingen status
  ``unavailable`` met een veel later nieuw moment — geen eindeloze lussen.
- Regels worden nooit stilzwijgend versoepeld; dat kan alleen via een expliciete
  override (``retry_relaxed``) met uitleg en audit.
"""

from __future__ import annotations

import asyncio
import logging
from datetime import UTC, datetime, timedelta
from typing import TYPE_CHECKING, Any

from mediaguard.arr.client import SEARCH_COMMANDS, ArrError, ArrNotFound, records_of
from mediaguard.audit import audit
from mediaguard.db import dumps, loads, utcnow
from mediaguard.instances.registry import arr_client, instance_row
from mediaguard.library.queries import episode_state
from mediaguard.library.sync import iso_z, now_z
from mediaguard.wanted.diagnosis import (
    RELAXABLE,
    candidate_category,
    diagnose,
    interactive_candidates,
)
from mediaguard.wanted.engine import (
    get_wanted,
    refresh_wanted_if_changed,
    settings_scope_chain,
)

if TYPE_CHECKING:
    from mediaguard.arr.client import ArrClient
    from mediaguard.context import AppContext

LOGGER = logging.getLogger(__name__)
GRAB_GRACE_HOURS = 24
PENDING_TIMEOUT_HOURS = 6


class QuotaExceeded(Exception):
    def __init__(self, message: str, quota: dict[str, Any]) -> None:
        super().__init__(message)
        self.quota = quota


def _now() -> datetime:
    return datetime.now(UTC)


def _iso(dt: datetime) -> str:
    return dt.isoformat(timespec="seconds")


def cfg(ctx: AppContext, key: str, instance_id: str) -> Any:
    """Effectieve instelling: globaal → media-type → instantie (R2.5)."""
    row = ctx.db.fetch_one("SELECT media_type FROM instances WHERE id=?", (instance_id,))
    chain = {"instance": instance_id}
    if row is not None:
        chain["media_type"] = row["media_type"]
    return ctx.config.value(key, chain)


def quota_status(ctx: AppContext, instance_id: str) -> dict[str, Any]:
    now = _now()

    def used(since: datetime) -> int:
        # zoekacties van de leveringsmotor tellen mee (verbeterlijst 36)
        row = ctx.db.fetch_one(
            "SELECT (SELECT COALESCE(SUM(counts_quota),0) FROM backfill_attempts WHERE "
            "instance_id=? AND ts>=?) + (SELECT COALESCE(SUM(cost),0) FROM delivery_searches "
            "WHERE instance_id=? AND ts>=?) AS n",
            (instance_id, _iso(since), instance_id, _iso(since)))
        return int(row["n"]) if row else 0

    hour_max = int(cfg(ctx, "backfill.max_searches_per_hour", instance_id))
    day_max = int(cfg(ctx, "backfill.max_searches_per_day", instance_id))
    hour_used = used(now - timedelta(hours=1))
    day_used = used(now - timedelta(days=1))
    enabled = bool(cfg(ctx, "backfill.enabled", instance_id))
    row = ctx.db.fetch_one("SELECT SUM(CASE WHEN reason='missing' THEN 1 ELSE 0 END) AS m, "
                           "COUNT(*) AS n FROM wanted_items WHERE instance_id=? AND status IN "
                           "('wanted','searching')", (instance_id,))
    missing, open_total = int((row["m"] if row else 0) or 0), int((row["n"] if row else 0) or 0)
    per_day = min(day_max, hour_max * 24) if enabled else 0
    days = -(-open_total // per_day) if per_day and open_total else 0
    # één gewone zin i.p.v. 'quota 60/1.500 · Uur: 0/20 · batch 100' (rapport logica 3)
    if not enabled:
        plain = "Automatisch zoeken staat uit voor deze instantie."
    elif not open_total:
        plain = "Niets te zoeken: alles wat je volgt is er."
    else:
        limit = (f"max. {hour_max} per uur" if hour_max * 24 < day_max
                 else f"dagmaximum {day_max}")
        plain = (f"MediaGuard zoekt elke dag tot ~{per_day} items ({limit}): {missing} "
                 f"ontbrekend, {open_total - missing} onder kwaliteitsgrens. Klaar over ± "
                 f"{days} {'dag' if days == 1 else 'dagen'}; je hoeft niets te starten. "
                 f"Vandaag {day_used} van {per_day} gebruikt.")
    return {"hour_used": hour_used, "hour_max": hour_max, "day_used": day_used,
            "day_max": day_max, "batch_size": int(cfg(ctx, "backfill.batch_size", instance_id)),
            "remaining": max(0, min(hour_max - hour_used, day_max - day_used)),
            "automatic": enabled, "open": open_total, "per_day": per_day,
            "eta_days": days, "plain_nl": plain}


def _still_wanted(ctx: AppContext, row: dict[str, Any]) -> bool:
    """Controle tegen de cache vlak vóór zoeken (geen ongemonitorde/toekomstige items)."""
    iid = row["instance_id"]
    if row["kind"] == "episode":
        ep = ctx.db.fetch_one(
            "SELECT e.*, l.monitored AS sm, f.quality_cutoff_not_met FROM lib_episodes e "
            "JOIN lib_items l ON l.instance_id=e.instance_id AND l.kind='series' AND "
            "l.provider_id=e.series_provider_id LEFT JOIN lib_files f ON "
            "f.instance_id=e.instance_id AND f.kind='episode' AND f.provider_id=e.episode_file_id"
            " WHERE e.instance_id=? AND e.provider_id=?", (iid, row["provider_id"]))
        if ep is None:
            return False
        state = episode_state(dict(ep), bool(ep["sm"]),
                              cutoff_unmet=bool(ep["quality_cutoff_not_met"]))
        return state == ("missing" if row["reason"] == "missing" else "cutoff_unmet")
    mv = ctx.db.fetch_one("SELECT l.monitored, l.has_file, l.is_available, l.removed_at, "
                          "f.quality_cutoff_not_met FROM lib_items l LEFT JOIN lib_files f ON "
                          "f.instance_id=l.instance_id AND f.kind='movie' AND "
                          "f.provider_id=l.file_id WHERE l.instance_id=? AND l.kind='movie' AND "
                          "l.provider_id=?", (iid, row["provider_id"]))
    if mv is None or mv["removed_at"] or not mv["monitored"]:
        return False
    if row["reason"] == "missing":
        return not mv["has_file"] and bool(mv["is_available"])
    return bool(mv["has_file"]) and bool(mv["quality_cutoff_not_met"])


def _still_wanted_bulk(ctx: AppContext, instance_id: str,
                       rows: list[dict[str, Any]]) -> set[int]:
    """Zelfde controle als ``_still_wanted``, maar met twee queries voor alle rijen samen
    (was: één query per open item, ~337k queries per backfillronde bij 67k items)."""
    ok: set[int] = set()
    now = now_z()
    ep_rows = [r for r in rows if r["kind"] == "episode"]
    if ep_rows:
        eps = {int(e["provider_id"]): e for e in ctx.db.fetch_dicts(
            "SELECT e.provider_id, e.has_file, e.monitored, e.air_date_utc, l.monitored AS sm, "
            "f.quality_cutoff_not_met FROM lib_episodes e "
            "JOIN lib_items l ON l.instance_id=e.instance_id AND l.kind='series' AND "
            "l.provider_id=e.series_provider_id LEFT JOIN lib_files f ON "
            "f.instance_id=e.instance_id AND f.kind='episode' AND f.provider_id=e.episode_file_id"
            " WHERE e.instance_id=?", (instance_id,))}
        for row in ep_rows:
            ep = eps.get(int(row["provider_id"]))
            if ep is None:
                continue
            state = episode_state(ep, bool(ep["sm"]), now,
                                  cutoff_unmet=bool(ep["quality_cutoff_not_met"]))
            if state == ("missing" if row["reason"] == "missing" else "cutoff_unmet"):
                ok.add(int(row["id"]))
    mv_rows = [r for r in rows if r["kind"] != "episode"]
    if mv_rows:
        mvs = {int(m["provider_id"]): m for m in ctx.db.fetch_dicts(
            "SELECT l.provider_id, l.monitored, l.has_file, l.is_available, l.removed_at, "
            "f.quality_cutoff_not_met FROM lib_items l LEFT JOIN lib_files f ON "
            "f.instance_id=l.instance_id AND f.kind='movie' AND f.provider_id=l.file_id "
            "WHERE l.instance_id=? AND l.kind='movie'", (instance_id,))}
        for row in mv_rows:
            mv = mvs.get(int(row["provider_id"]))
            if mv is None or mv["removed_at"] or not mv["monitored"]:
                continue
            if row["reason"] == "missing":
                if not mv["has_file"] and bool(mv["is_available"]):
                    ok.add(int(row["id"]))
            elif bool(mv["has_file"]) and bool(mv["quality_cutoff_not_met"]):
                ok.add(int(row["id"]))
    return ok


def eligible_items(ctx: AppContext, instance_id: str, *, wanted_ids: list[int] | None = None,
                   manual: bool = False) -> tuple[list[dict[str, Any]], dict[str, int]]:
    now = _iso(_now())
    include_cutoff = bool(cfg(ctx, "backfill.include_cutoff_unmet", instance_id))
    # 'searching' zonder openstaande poging (bv. na crash) wordt zo vanzelf weer opgepakt
    statuses = ("wanted", "unavailable", "searching", "ignored") if manual else (
        "wanted", "unavailable", "searching")
    marks = ",".join("?" for _ in statuses)
    rows = ctx.db.fetch_dicts(
        f"SELECT * FROM wanted_items WHERE instance_id=? AND status IN ({marks}) "
        "ORDER BY priority DESC, air_date_utc DESC, id", (instance_id, *statuses))
    pending = {r["wanted_id"] for r in ctx.db.fetch_all(
        "SELECT DISTINCT wanted_id FROM backfill_attempts WHERE instance_id=? AND "
        "status='pending'", (instance_id,))}
    deferred: dict[str, int] = {}
    out = []

    def skip(reason: str) -> None:
        deferred[reason] = deferred.get(reason, 0) + 1

    if wanted_ids is not None:
        wanted_set = set(wanted_ids)
        rows = [r for r in rows if r["id"] in wanted_set]
    still = _still_wanted_bulk(ctx, instance_id, rows)
    for row in rows:
        if row["id"] in pending:
            skip("pending")
            continue
        if not manual:
            if row["reason"] == "cutoff" and not include_cutoff:
                skip("cutoff_disabled")
                continue
            if ctx.config.value("backfill.excluded", settings_scope_chain(row)):
                skip("excluded")
                continue
            if row["next_search_at"] and row["next_search_at"] > now:
                skip("unavailable_wait" if row["status"] == "unavailable" else "backoff")
                continue
            if _held(ctx, row):
                skip("download_hold")
                continue
        if int(row["id"]) not in still:
            skip("stale")
            continue
        out.append(row)
    return out, deferred


def _held(ctx: AppContext, row: dict[str, Any]) -> bool:
    """Zoekpauze uit de downloadbewaking (verdwijnende downloads / geweigerde upgrade)."""
    from mediaguard.downloads import holds

    try:
        return holds.hold_for(ctx, holds.item_key(row["instance_id"], row["kind"],
                                                  row["provider_id"])) is not None
    except (KeyError, TypeError, ValueError):
        return False


def build_commands(ctx: AppContext, app: str, items: list[dict[str, Any]], budget: int,
                   season_threshold: int) -> list[dict[str, Any]]:
    commands: list[dict[str, Any]] = []
    if budget <= 0 or not items:
        return commands
    if app == "radarr":
        chosen = items[:budget]
        return [{"command": "MoviesSearch",
                 "body": {"movieIds": [int(i["provider_id"]) for i in chosen]},
                 "wanted_ids": [i["id"] for i in chosen], "costs": [1] * len(chosen)}]
    remaining = budget
    used_ids: set[int] = set()
    # volledig ontbrekende seizoenen → één SeasonSearch (seizoenspakket eerst)
    groups: dict[tuple[int, int], list[dict[str, Any]]] = {}
    for item in items:
        if item["reason"] == "missing" and item["season_number"] not in (None, 0):
            groups.setdefault((item["item_provider_id"], item["season_number"]), []).append(item)
    iid = items[0]["instance_id"]
    for (series_id, season), group in groups.items():
        if remaining <= 0 or len(group) < season_threshold:
            continue
        row = ctx.db.fetch_one(
            "SELECT SUM(e.has_file) AS files, SUM(CASE WHEN e.monitored=1 AND e.has_file=0 AND "
            "e.air_date_utc IS NOT NULL AND e.air_date_utc<=? THEN 1 ELSE 0 END) AS missing "
            "FROM lib_episodes e WHERE e.instance_id=? AND e.series_provider_id=? AND "
            "e.season_number=?", (iso_z(_iso(_now())), iid, series_id, season))
        if row is None or int(row["files"] or 0) > 0 or int(row["missing"] or 0) != len(group):
            continue
        commands.append({"command": "SeasonSearch",
                         "body": {"seriesId": int(series_id), "seasonNumber": int(season)},
                         "wanted_ids": [g["id"] for g in group],
                         "costs": [1] + [0] * (len(group) - 1)})
        used_ids.update(g["id"] for g in group)
        remaining -= 1
    singles = [i for i in items if i["id"] not in used_ids][:max(0, remaining)]
    if singles:
        commands.append({"command": "EpisodeSearch",
                         "body": {"episodeIds": [int(i["provider_id"]) for i in singles]},
                         "wanted_ids": [i["id"] for i in singles], "costs": [1] * len(singles)})
    return commands


def plan(ctx: AppContext, instance_id: str, *, wanted_ids: list[int] | None = None,
         manual: bool = False) -> dict[str, Any]:
    inst = instance_row(ctx, instance_id)
    if inst is None:
        raise KeyError(instance_id)
    quota = quota_status(ctx, instance_id)
    items, deferred = eligible_items(ctx, instance_id, wanted_ids=wanted_ids, manual=manual)
    budget = min(quota["remaining"], quota["batch_size"]) if not manual else quota["remaining"]
    commands = build_commands(ctx, inst["app"], items, budget,
                              int(cfg(ctx, "backfill.season_search_threshold", instance_id)))
    planned_ids = {w for c in commands for w in c["wanted_ids"]}
    by_id = {i["id"]: i for i in items}
    if len(items) > len(planned_ids):
        deferred["quota_or_batch"] = len(items) - len(planned_ids)
    return {
        "instance_id": instance_id, "app": inst["app"], "quota": quota,
        "interval_hours": int(cfg(ctx, "backfill.interval_hours", instance_id)),
        "enabled": bool(cfg(ctx, "backfill.enabled", instance_id)),
        "eligible": len(items), "deferred": deferred,
        "commands": [{**c, "items": [{k: by_id[w][k] for k in (
            "id", "title", "reason", "priority", "attempts", "last_searched_at", "status")}
            for w in c["wanted_ids"]], "cost": sum(c["costs"])} for c in commands],
    }


# -- uitvoeren ----------------------------------------------------------------------------------
def _skip(instance_id: str, reason: str, message: str) -> dict[str, Any]:
    return {"instance_id": instance_id, "skipped": reason, "message_nl": message, "issued": 0}


async def run_backfill(ctx: AppContext, instance_id: str, *, trigger: str = "scheduler",
                       dry_run: bool = False, wanted_ids: list[int] | None = None,
                       wait_seconds: float | None = None, actor: str = "system") -> dict:
    inst = instance_row(ctx, instance_id)
    if inst is None:
        raise KeyError(instance_id)
    manual = trigger == "manual"
    if inst["archived"]:
        return _skip(instance_id, "archived", "Instantie is gearchiveerd.")
    if inst["suspended"] and not dry_run:
        return _skip(instance_id, "suspended", "Instantie is gepauzeerd; backfill wacht.")
    if ctx.emergency_stopped() and not dry_run:
        return _skip(instance_id, "emergency_stop", "Noodstop actief.")
    if trigger == "scheduler" and not cfg(ctx, "backfill.enabled", instance_id):
        return _skip(instance_id, "disabled", "Backfill staat uit voor deze instantie.")
    if inst["status"] == "offline" and not dry_run:
        return _skip(instance_id, "offline", "Instantie is offline.")
    await asyncio.to_thread(refresh_wanted_if_changed, ctx, instance_id)
    if dry_run:
        return {"dry_run": True, **await asyncio.to_thread(
            lambda: plan(ctx, instance_id, wanted_ids=wanted_ids, manual=manual))}
    async with arr_client(ctx, instance_id) as client:
        await evaluate_pending(ctx, instance_id, client)
        if not manual:
            running = await client.commands()
            busy = [c for c in running if c.get("name") in SEARCH_COMMANDS and
                    str(c.get("status", "")).lower() in ("queued", "started")]
            if busy:
                return _skip(instance_id, "busy", f"Er loopt al een zoekopdracht "
                             f"({busy[0].get('name')}); ronde overgeslagen.")
        # planning leest de hele wanted-lijst: buiten de event loop (hooks blijven snel)
        the_plan = await asyncio.to_thread(
            lambda: plan(ctx, instance_id, wanted_ids=wanted_ids, manual=manual))
        if manual and wanted_ids and the_plan["eligible"] and not the_plan["commands"]:
            raise QuotaExceeded("Zoekquotum voor deze instantie is op; probeer later opnieuw.",
                                the_plan["quota"])
        queue = records_of(await client.queue(all_pages=True))
        in_queue = {int(q.get("episodeId") or q.get("movieId") or 0) for q in queue}
        run_id = ctx.db.execute_insert(
            "INSERT INTO backfill_runs(instance_id,started_at,trigger,dry_run,planned,status,"
            "quota_json) VALUES (?,?,?,?,?,?,?)",
            (instance_id, utcnow(), trigger, 0, sum(len(c["wanted_ids"])
                                                    for c in the_plan["commands"]),
             "running", dumps(the_plan["quota"])))
        issued: list[int] = []
        skipped_in_queue = 0
        errors: list[str] = []
        command_ids: list[int] = []
        for cmd in the_plan["commands"]:
            items = [get_wanted(ctx, w) for w in cmd["wanted_ids"]]
            keep = [(w, c) for w, c in zip(items, cmd["costs"], strict=True)
                    if w and int(w["provider_id"]) not in in_queue]
            skipped_in_queue += len(items) - len(keep)
            if not keep:
                continue
            body = dict(cmd["body"])
            if cmd["command"] == "EpisodeSearch":
                body["episodeIds"] = [int(w["provider_id"]) for w, _ in keep]
            elif cmd["command"] == "MoviesSearch":
                body["movieIds"] = [int(w["provider_id"]) for w, _ in keep]
            try:
                response = await client.command(cmd["command"], **body)
            except ArrError as exc:
                errors.append(exc.message)
                for w, cost in keep:
                    _attempt(ctx, w, run_id, trigger, cmd["command"], None, "error",
                             cost, exc.message)
                break
            cid = int(response.get("id") or 0) or None
            if cid:
                command_ids.append(cid)
            for w, cost in keep:
                _attempt(ctx, w, run_id, trigger, cmd["command"], cid, "pending", cost, None)
                ctx.db.execute(
                    "UPDATE wanted_items SET status='searching', attempts=attempts+1, "
                    "last_searched_at=?, updated_at=? WHERE id=?", (utcnow(), utcnow(), w["id"]))
                issued.append(int(w["id"]))
        wait = float(cfg(ctx, "backfill.command_wait_seconds", instance_id)) \
            if wait_seconds is None else wait_seconds
        if command_ids and wait > 0:
            await _wait_commands(client, command_ids, wait)
        evaluation = await evaluate_pending(ctx, instance_id, client)
    status = "failed" if errors and not issued else "succeeded"
    message = "; ".join(errors) or None
    ctx.db.execute("UPDATE backfill_runs SET finished_at=?, issued=?, status=?, message=? "
                   "WHERE id=?", (utcnow(), len(issued), status, message, run_id))
    if issued:
        audit(ctx.db, actor=actor, action="backfill.run", target_type="instance",
              target_id=instance_id, instance_id=instance_id,
              new_value={"run_id": run_id, "issued": len(issued), "trigger": trigger})
    ctx.events.publish("backfill.run", {"run_id": run_id, "issued": len(issued),
                                        "evaluation": evaluation}, instance_id=instance_id)
    return {"instance_id": instance_id, "run_id": run_id, "issued": len(issued),
            "issued_wanted_ids": issued, "skipped_in_queue": skipped_in_queue,
            "errors": errors, "evaluation": evaluation, "plan": the_plan}


def _attempt(ctx: AppContext, w: dict[str, Any], run_id: int | None, trigger: str,
             command: str, command_id: int | None, status: str, cost: int,
             message: str | None) -> int:
    return ctx.db.execute_insert(
        "INSERT INTO backfill_attempts(wanted_id,instance_id,run_id,ts,trigger,command,"
        "command_id,counts_quota,status,message,finished_at) VALUES (?,?,?,?,?,?,?,?,?,?,?)",
        (w["id"], w["instance_id"], run_id, utcnow(), trigger, command, command_id, cost, status,
         message, None if status == "pending" else utcnow()))


async def _wait_commands(client: ArrClient, ids: list[int], timeout: float) -> None:
    loop = asyncio.get_running_loop()
    deadline = loop.time() + timeout
    delay = 0.2
    while loop.time() < deadline:
        done = True
        for cid in ids:
            try:
                st = await client.command_status(cid)
            except ArrNotFound:
                continue
            except ArrError:
                return
            if str(st.get("status", "")).lower() in ("queued", "started"):
                done = False
                break
        if done:
            return
        await asyncio.sleep(delay)
        delay = min(5.0, delay * 2)


async def evaluate_pending(ctx: AppContext, instance_id: str,
                           client: ArrClient | None = None) -> dict[str, int]:
    """Beoordeelt openstaande pogingen: grab gevonden → succes; anders backoff of
    'unavailable'. Idempotent en herstartbestendig."""
    pending = ctx.db.fetch_dicts("SELECT * FROM backfill_attempts WHERE instance_id=? AND "
                                 "status='pending' ORDER BY id", (instance_id,))
    result = {"grabbed": 0, "no_candidate": 0, "error": 0, "still_running": 0}
    if not pending:
        return result
    if client is None:
        async with arr_client(ctx, instance_id) as own:
            return await evaluate_pending(ctx, instance_id, own)
    statuses: dict[int, str] = {}
    for cid in {a["command_id"] for a in pending if a["command_id"]}:
        try:
            statuses[cid] = str((await client.command_status(cid)).get("status", "")).lower()
        except ArrNotFound:
            statuses[cid] = "completed"  # Sonarr ruimt oude commando's op
        except ArrError:
            statuses[cid] = "unknown"
    try:
        queue = records_of(await client.queue(all_pages=True))
    except ArrError:
        queue = []
    queued = {int(q.get("episodeId") or q.get("movieId") or 0) for q in queue}
    app = client.app
    for a in pending:
        status = statuses.get(a["command_id"] or 0, "completed")
        age_h = (_now() - datetime.fromisoformat(a["ts"])).total_seconds() / 3600
        w = get_wanted(ctx, a["wanted_id"])
        if w is None:
            continue
        if status in ("queued", "started", "unknown"):
            if age_h < PENDING_TIMEOUT_HOURS:
                result["still_running"] += 1
                continue
            _finish(ctx, a, w, "error", "Zoekcommando niet afgerond binnen "
                    f"{PENDING_TIMEOUT_HOURS} uur")
            result["error"] += 1
            continue
        if status in ("failed", "aborted", "cancelled", "orphaned"):
            _finish(ctx, a, w, "error", f"Zoekcommando eindigde met status {status}")
            result["error"] += 1
            continue
        grabbed = await _was_grabbed(client, app, w, a["ts"], queued)
        outcome = "grabbed" if grabbed else "no_candidate"
        _finish(ctx, a, w, outcome, grabbed or "Geen geschikte release gegrabd")
        result[outcome] += 1
        if outcome == "no_candidate":
            await _maybe_collect(ctx, instance_id, a["wanted_id"])
    return result


async def _was_grabbed(client: ArrClient, app: str, w: dict[str, Any], since: str,
                       queued: set[int]) -> str | None:
    pid = int(w["provider_id"])
    if pid in queued:
        return "In de downloadwachtrij"
    since_z = iso_z(since) or ""
    try:
        if app == "sonarr":
            hist = await client.history_for_episode(pid, 20)
        else:
            hist = await client.history_for_movie(pid)
    except ArrError:
        hist = []
    for h in hist:
        if str(h.get("eventType", "")).lower() == "grabbed" and (iso_z(h.get("date")) or "") \
                >= since_z:
            return f"Gegrabd: {h.get('sourceTitle')}"
    try:
        live = await (client.episode_get(pid) if app == "sonarr" else client.movie_get(pid))
    except ArrError:
        return None
    if w["reason"] == "missing" and live.get("hasFile"):
        return "Bestand inmiddels aanwezig"
    return None


def _finish(ctx: AppContext, attempt: dict[str, Any], w: dict[str, Any], outcome: str,
            message: str) -> None:
    now = _now()
    ctx.db.execute("UPDATE backfill_attempts SET status=?, message=?, finished_at=? WHERE id=?",
                   (outcome, message, _iso(now), attempt["id"]))
    iid = w["instance_id"]
    if outcome == "grabbed":
        nxt, status = now + timedelta(hours=GRAB_GRACE_HOURS), "wanted"
    elif outcome == "error":
        nxt = now + timedelta(hours=int(cfg(ctx, "backfill.base_backoff_hours", iid)))
        status = "wanted" if w["status"] != "unavailable" else "unavailable"
    else:
        failures = _consecutive_failures(ctx, w["id"])
        base = int(cfg(ctx, "backfill.base_backoff_hours", iid))
        cap_h = int(cfg(ctx, "backfill.max_backoff_days", iid)) * 24
        limit = int(cfg(ctx, "backfill.unavailable_after_attempts", iid))
        if failures >= limit and not w["keep_monitoring"]:
            status = "unavailable"
            nxt = now + timedelta(days=int(cfg(ctx, "backfill.unavailable_retry_days", iid)))
        else:
            status = "wanted"
            nxt = now + timedelta(hours=min(cap_h, base * (2 ** max(0, failures - 1))))
    if w["status"] == "ignored":
        status = "ignored"
    ctx.db.execute("UPDATE wanted_items SET status=?, next_search_at=?, last_outcome=?, "
                   "updated_at=? WHERE id=?", (status, _iso(nxt), outcome, _iso(now), w["id"]))
    if status == "unavailable" and w["status"] != "unavailable":
        ctx.events.publish("wanted.unavailable", {"wanted_id": w["id"], "title": w["title"]},
                           instance_id=iid)


def _consecutive_failures(ctx: AppContext, wanted_id: int) -> int:
    n = 0
    for row in ctx.db.fetch_all("SELECT status FROM backfill_attempts WHERE wanted_id=? AND "
                                "status IN ('grabbed','no_candidate') ORDER BY id DESC",
                                (wanted_id,)):
        if row["status"] != "no_candidate":
            break
        n += 1
    return n


async def _maybe_collect(ctx: AppContext, instance_id: str, wanted_id: int) -> None:
    """Bewaar kandidaten + afwijzingsredenen (interactief zoeken, telt mee in quota)."""
    mode = cfg(ctx, "backfill.collect_candidates", instance_id)
    w = get_wanted(ctx, wanted_id)
    if w is None or mode == "never":
        return
    if mode == "on_unavailable" and w["status"] != "unavailable":
        return
    if quota_status(ctx, instance_id)["remaining"] <= 0:
        return
    try:
        await diagnose(ctx, wanted_id, interactive=True, trigger="scheduler")
    except Exception:  # noqa: BLE001 - verzamelen is best-effort
        LOGGER.exception("kandidaten verzamelen voor %s mislukt", wanted_id)


# -- handmatige acties ----------------------------------------------------------------------
async def search_now(ctx: AppContext, wanted_ids: list[int], actor: str,
                     wait_seconds: float = 0) -> dict[str, Any]:
    rows = [get_wanted(ctx, w) for w in wanted_ids]
    if any(r is None for r in rows):
        raise KeyError("onbekend wanted-item")
    by_instance: dict[str, list[int]] = {}
    for r in rows:
        assert r is not None
        by_instance.setdefault(r["instance_id"], []).append(int(r["id"]))
    results = []
    for iid, ids in by_instance.items():
        inst = instance_row(ctx, iid) or {}
        if inst.get("suspended"):
            raise QuotaExceeded(f"Instantie {iid} is gepauzeerd; hervat eerst.",
                                quota_status(ctx, iid))
        res = await run_backfill(ctx, iid, trigger="manual", wanted_ids=ids,
                                 wait_seconds=wait_seconds, actor=actor)
        if res.get("skipped"):
            raise QuotaExceeded(res["message_nl"], quota_status(ctx, iid))
        audit(ctx.db, actor=actor, action="wanted.search_now", target_type="wanted",
              target_id=",".join(map(str, ids)), instance_id=iid,
              new_value={"issued": res["issued"], "run_id": res.get("run_id")})
        results.append(res)
    return {"results": results}


RELAX_EXPLANATIONS = {
    "quality": "een lagere kwaliteit dan het profiel toestaat",
    "custom-format": "een custom format-score onder het minimum",
    "size": "een bestandsgrootte buiten de kwaliteitsdefinitie",
    "language": "een andere taal dan het profiel vereist (bijv. alleen Japans bij anime)",
}


def explain_relaxation(relaxations: list[str]) -> str:
    parts = [RELAX_EXPLANATIONS[r] for r in relaxations]
    return ("Eenmalige uitzondering voor dit item: MediaGuard mag een release grabben met "
            + ", ".join(parts) + ". Alle andere regels (juiste aflevering/film, blocklist, "
            "MediaGuard-identiteits- en veiligheidsbeleid) blijven gelden. Het profiel van de "
            "serie/film wordt niet gewijzigd; Sonarr/Radarr kan later automatisch upgraden "
            "naar een release die wél aan het profiel voldoet.")


def active_override(ctx: AppContext, instance_id: str, kind: str,
                    provider_id: int) -> dict[str, Any] | None:
    """Voor policy/hook-modules: actieve versoepeling voor (instance, episode|movie)."""
    row = ctx.db.fetch_one(
        "SELECT o.* FROM wanted_overrides o JOIN wanted_items w ON w.id=o.wanted_id WHERE "
        "o.instance_id=? AND w.kind=? AND w.provider_id=? AND o.active=1 AND (o.expires_at "
        "IS NULL OR o.expires_at>?) ORDER BY o.id DESC LIMIT 1",
        (instance_id, kind, int(provider_id), utcnow()))
    if row is None:
        return None
    data = dict(row)
    data["relaxations"] = loads(data.pop("relaxations_json"), [])
    return data


async def retry_relaxed(ctx: AppContext, wanted_id: int, relaxations: list[str], actor: str,
                        *, confirm: bool, reason: str = "") -> dict[str, Any]:
    w = get_wanted(ctx, wanted_id)
    if w is None:
        raise KeyError(wanted_id)
    iid = w["instance_id"]
    allowed = set(cfg(ctx, "wanted.relaxed_allowed_categories", iid))
    relaxations = sorted(set(relaxations))
    if not relaxations:
        raise ValueError("Kies minstens één regel om te versoepelen")
    invalid = [r for r in relaxations if r not in RELAX_EXPLANATIONS]
    if invalid:
        raise ValueError(f"Niet versoepelbaar: {', '.join(invalid)}")
    blocked = [r for r in relaxations if r not in allowed]
    if blocked:
        raise ValueError(f"Versoepelen van {', '.join(blocked)} is uitgeschakeld voor {iid} "
                         "(instelling wanted.relaxed_allowed_categories)")
    explanation = explain_relaxation(relaxations)
    if not confirm:
        return {"requires_confirmation": True, "explanation_nl": explanation,
                "relaxations": relaxations, "quota": quota_status(ctx, iid)}
    quota = quota_status(ctx, iid)
    if quota["remaining"] <= 0:
        raise QuotaExceeded("Zoekquotum op; versoepelde zoekactie later opnieuw.", quota)
    expires = _iso(_now() + timedelta(days=7))
    override_id = ctx.db.execute_insert(
        "INSERT INTO wanted_overrides(wanted_id,instance_id,kind,relaxations_json,"
        "explanation_nl,created_by,created_at,expires_at,active) VALUES (?,?,?,?,?,?,?,?,1)",
        (wanted_id, iid, "relaxed_rules", dumps(relaxations), explanation, actor, utcnow(),
         expires))
    audit(ctx.db, actor=actor, action="wanted.retry_relaxed", target_type="wanted",
          target_id=wanted_id, instance_id=iid, reason=reason,
          new_value={"override_id": override_id, "relaxations": relaxations},
          undo_hint="Override vervalt automatisch na 7 dagen of na gebruik")
    allowed_codes = {code for code, name in RELAXABLE.items() if name in relaxations}
    async with arr_client(ctx, iid) as client:
        candidates = await interactive_candidates(client, w)
        pick = None
        from mediaguard.downloads.failures import avoided_groups

        avoid = avoided_groups(ctx, f"{iid}:{w['item_provider_id']}")
        for cand in candidates:
            if cand.get("download_allowed") is False:
                continue
            if cand.get("release_group") and cand["release_group"] in avoid:
                continue  # deze groep mislukte al 2× voor dit item (rapport 3, R6)
            codes = {r["code"] for r in cand.get("rejections", [])}
            if candidate_category(cand) is None or (codes and codes <= allowed_codes):
                pick = cand
                break
        rejections = sorted({r["text"] for c in candidates for r in c.get("rejections", [])})
        status = "no_candidate"
        message = "Geen release die alleen op de versoepelde regels werd afgewezen."
        if pick is not None:
            await client.release_grab(str(pick["guid"]), int(pick["indexer_id"]))
            status, message = "grabbed", f"Gegrabd met override: {pick['title']}"
    ctx.db.execute_insert(
        "INSERT INTO backfill_attempts(wanted_id,instance_id,ts,trigger,command,counts_quota,"
        "status,candidates_json,rejections_json,message,finished_at) VALUES "
        "(?,?,?,?,?,?,?,?,?,?,?)",
        (wanted_id, iid, utcnow(), "relaxed", "InteractiveSearch+Grab", 1, status,
         dumps(candidates), dumps(rejections), message, utcnow()))
    result = {"status": status, "message_nl": message, "picked": pick,
              "candidates": len(candidates)}
    ctx.db.execute("UPDATE wanted_overrides SET used_at=?, result_json=?, active=? WHERE id=?",
                   (utcnow(), dumps(result), 0 if status == "grabbed" else 1, override_id))
    now = utcnow()
    ctx.db.execute("UPDATE wanted_items SET last_searched_at=?, attempts=attempts+1, "
                   "last_outcome=?, updated_at=? WHERE id=?", (now, status, now, wanted_id))
    return {"override_id": override_id, "explanation_nl": explanation, **result}
