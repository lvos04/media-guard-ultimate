"""Stap 9-10: gecontroleerde cutover per instantie (of groep), rollback en post-cutover
audit.

Volgorde per doel (elke fase wordt vóór de volgende persistent vastgelegd, zodat een
stroomstoring halverwege veilig hervat of teruggedraaid kan worden):

1. ``requested``  -> exacte huidige staat van de OUDE instantie vastleggen
   (indexers rss/auto/interactive, downloadclients, importlijsten, RSS-interval)
2. ``frozen``     -> RSS + automatisch zoeken op de oude indexers UIT (alleen deze
   twee velden; downloadclients blijven aan zodat lopende downloads afronden)
3. guard          -> oude indexers opnieuw lezen: staat er nog iets aan -> afbreken
4. ``delta_synced`` -> items/monitorwijzigingen sinds de schaduwimport naar het doel
5. guard + ``lifted`` -> doel-indexers/downloadclients krijgen de oorspronkelijke
   bronstanden; schaduwmodus uit
6. ``seerr_done`` -> Seerr-server omzetten via de B1-helper (vorige waarden bewaard)
7. ``completed``

Nooit twee RSS-grabbers voor dezelfde bibliotheek: stap 5 gebeurt alleen nadat de
oude indexers aantoonbaar (opnieuw gelezen) RSS-uit staan.
"""

from __future__ import annotations

from collections.abc import Callable
from datetime import UTC, datetime, timedelta
from typing import TYPE_CHECKING, Any

from mediaguard.arr.client import ArrError, records_of
from mediaguard.audit import audit
from mediaguard.db import dumps, loads, utcnow
from mediaguard.instances.registry import arr_client, instance_row, update_instance
from mediaguard.migration import store
from mediaguard.migration.backupfile import normalize_rules
from mediaguard.migration.importer import IMPORT_LIST_FLAGS, Importer
from mediaguard.migration.snapshot import parse_dt
from mediaguard.migration.sources import source_client, source_row
from mediaguard.migration.wizard import (
    StepBlocked,
    enforce_shadow,
    find_seerr_server,
    seerr_profile_overrides,
)

if TYPE_CHECKING:
    from mediaguard.context import AppContext

Log = Callable[[str], None]
FREEZE_FIELDS = ("enableRss", "enableAutomaticSearch")
DEFAULT_RSS_INTERVAL = 15


class CutoverAbort(Exception):
    pass


def cutover_row(ctx: AppContext, cutover_id: int) -> dict[str, Any]:
    row = ctx.db.fetch_one("SELECT * FROM migration_cutovers WHERE id=?", (cutover_id,))
    if row is None:
        raise KeyError(cutover_id)
    return dict(row)


def latest_cutover(ctx: AppContext, run_id: int, target: str) -> dict[str, Any] | None:
    row = ctx.db.fetch_one("SELECT * FROM migration_cutovers WHERE run_id=? AND "
                           "target_instance_id=? ORDER BY id DESC LIMIT 1", (run_id, target))
    return dict(row) if row else None


def _update(ctx: AppContext, cid: int, **fields: Any) -> None:
    fields["updated_at"] = utcnow()
    sets = ",".join(f"{k}=?" for k in fields)
    ctx.db.execute(f"UPDATE migration_cutovers SET {sets} WHERE id=?", [*fields.values(), cid])


def validate_request(ctx: AppContext, run_id: int, target: str, *, confirm_name: str,
                     accept_warnings: bool, confirm_old_offline: bool) -> dict[str, Any]:
    """Alle voorwaarden vóórdat er ook maar iets op de oude instantie verandert."""
    if ctx.emergency_stopped():
        raise StepBlocked("Noodstop actief: cutover geblokkeerd")
    inst = instance_row(ctx, target)
    if inst is None:
        raise StepBlocked(f"Onbekende doelinstantie {target}")
    if confirm_name.strip() != target:
        raise StepBlocked(f"Typ exact de instantienaam '{target}' om te bevestigen")
    m = store.mapping_for_target(ctx, run_id, target)
    row = source_row(ctx, m["source_id"])
    assert row is not None
    if row["kind"] != "live" and not confirm_old_offline:
        raise StepBlocked("Deze bron is alleen een back-up: bevestig expliciet dat de oude "
                          "instantie uit staat of zelf niet meer zoekt "
                          "(confirm_old_instance_offline), anders ontstaan dubbele grabs.")
    ver = store.latest_report(ctx, run_id, "verify", target)
    if ver is None:
        raise StepBlocked("Geen vergelijkingsrapport: cutover niet toegestaan")
    from mediaguard.migration.snapshot import verdict

    v = verdict(ver["mismatches"], store.accepted_ids(ctx, run_id, target))
    if v == "no_go":
        raise StepBlocked("NO-GO: het vergelijkingsrapport bevat blokkerende verschillen")
    if v == "warn" and not accept_warnings:
        raise StepBlocked("Het rapport bevat waarschuwingen; bevestig 'accept_warnings'")
    ready = store.latest_report(ctx, run_id, "readiness", target)
    if ready is None or ready["verdict"] == "no_go":
        raise StepBlocked("Cutover-gereedheid ontbreekt of is NO-GO (stap 8)")
    if ready["verdict"] == "warn" and not accept_warnings:
        raise StepBlocked("Gereedheidscontrole heeft waarschuwingen; bevestig 'accept_warnings'")
    if inst["suspended"]:
        raise StepBlocked("Doel is gepauzeerd via Instanties; hervat eerst")
    current = latest_cutover(ctx, run_id, target)
    if current and current["status"] in ("running", "failed") and \
            current["phase"] != "rolled_back":
        return {"existing": current, "mapping": m, "source": row}
    if current and current["status"] == "completed":
        raise StepBlocked("Deze instantie is al overgezet")
    return {"existing": None, "mapping": m, "source": row}


def resume(ctx: AppContext, cutover_id: int, actor: str) -> None:
    _update(ctx, cutover_id, status="running", error=None)
    audit(ctx.db, actor=actor, action="migration.cutover.resumed", target_type="cutover",
          target_id=cutover_id)


def start(ctx: AppContext, run_id: int, target: str, actor: str, confirm_name: str,
          kind: str) -> int:
    m = store.mapping_for_target(ctx, run_id, target)
    now = utcnow()
    cid = ctx.db.execute_insert(
        "INSERT INTO migration_cutovers(run_id,source_id,target_instance_id,kind,phase,status,"
        "confirmed_by,confirmed_name,started_at,updated_at) VALUES (?,?,?,?,?,?,?,?,?,?)",
        (run_id, m["source_id"], target, kind, "requested", "running", actor, confirm_name, now,
         now))
    audit(ctx.db, actor=actor, action="migration.cutover.requested", target_type="instance",
          target_id=target, instance_id=target, new_value={"run_id": run_id, "cutover_id": cid,
                                                           "source_id": m["source_id"],
                                                           "kind": kind})
    return cid


async def _indexer_states(ctx: AppContext, source_id: str) -> list[dict[str, Any]]:
    return await source_client(ctx, source_id).indexers()


async def _guard_old_frozen(ctx: AppContext, source_id: str) -> None:
    active = [i["name"] for i in await _indexer_states(ctx, source_id)
              if any(i.get(f) for f in FREEZE_FIELDS)]
    if active:
        raise CutoverAbort("Dubbele-grabber-bewaking: oude indexers staan nog AAN "
                           f"({', '.join(active)}); cutover afgebroken")


async def execute(ctx: AppContext, cutover_id: int, params: dict[str, Any],
                  log: Log) -> dict[str, Any]:  # noqa: C901 - expliciete fasen
    co = cutover_row(ctx, cutover_id)
    run_id, target, sid = co["run_id"], co["target_instance_id"], co["source_id"]
    row = source_row(ctx, sid)
    assert row is not None
    live = row["kind"] == "live"
    actor = co["confirmed_by"]
    m = store.mapping_for_target(ctx, run_id, target)
    tgt = arr_client(ctx, target)
    try:
        # 1-2: oude instantie bevriezen ------------------------------------------------
        if co["phase"] == "requested":
            if live:
                if co["source_state_json"] is None:  # exacte vorige staat, één keer
                    src = source_client(ctx, sid)
                    state = {
                        "indexers": {str(i["id"]): {"name": i["name"],
                                                    **{f: bool(i.get(f)) for f in (
                                                        *FREEZE_FIELDS,
                                                        "enableInteractiveSearch")}}
                                     for i in await src.indexers()},
                        "download_clients": {d["name"]: {"enable": bool(d.get("enable"))}
                                             for d in await src.download_clients()},
                        "import_lists": {il["name"]: {f: il[f] for f in IMPORT_LIST_FLAGS
                                                      if f in il}
                                         for il in await src.provider_list("importlist")},
                        "config": {"rsssyncinterval": (await src.get("config/indexer") or {})
                                   .get("rssSyncInterval")},
                        "captured_at": utcnow()}
                    _update(ctx, cutover_id, source_state_json=dumps(state))
                    co = cutover_row(ctx, cutover_id)
                state = loads(co["source_state_json"], {})
                frozen = source_client(ctx, sid, grant="freeze")
                for idx in await frozen.indexers():
                    if any(idx.get(f) for f in FREEZE_FIELDS):
                        await frozen.provider_update("indexer", {**idx, **dict.fromkeys(
                            FREEZE_FIELDS, False)}, force_save=True)
                        log(f"Oude indexer '{idx['name']}' bevroren (RSS + auto-zoeken uit)")
                audit(ctx.db, actor=actor, action="migration.freeze_old", target_type="source",
                      target_id=sid, instance_id=target, old_value=state["indexers"],
                      new_value={"enableRss": False, "enableAutomaticSearch": False},
                      undo_hint=f"POST /api/v1/migration/rollback (run {run_id}, {target})")
            else:
                log("Back-upbron: geen live oude instantie om te bevriezen (gebruiker heeft "
                    "bevestigd dat die uit staat)")
            _update(ctx, cutover_id, phase="frozen")
            co = cutover_row(ctx, cutover_id)
        # 3-4: guard + delta -------------------------------------------------------------
        if co["phase"] == "frozen":
            if live:
                await _guard_old_frozen(ctx, sid)
                log("Finale delta-synchronisatie (items sinds de schaduwimport)")
                delta = await Importer(ctx, run_id, target, source_client(ctx, sid), tgt,
                                       normalize_rules(m["path_map"]), log,
                                       store.run_options(ctx, run_id),
                                       phase="delta").run_items()
            else:
                delta = {"skipped": "back-upbron"}
            _update(ctx, cutover_id, phase="delta_synced", delta_json=dumps(delta))
            co = cutover_row(ctx, cutover_id)
        # 5: schaduw opheffen op het doel ------------------------------------------------
        if co["phase"] == "delta_synced":
            if live:
                await _guard_old_frozen(ctx, sid)  # vlak vóór activeren opnieuw lezen
            lift = await _lift_shadow(ctx, run_id, target, co, live)
            update_instance(ctx, target, {"shadow_mode": False})
            ctx.db.execute("UPDATE migration_shadow_states SET lifted_at=? WHERE run_id=? AND "
                           "target_instance_id=?", (utcnow(), run_id, target))
            _update(ctx, cutover_id, phase="lifted", lift_json=dumps(lift))
            audit(ctx.db, actor=actor, action="migration.lift_shadow", target_type="instance",
                  target_id=target, instance_id=target, new_value=lift)
            log(f"Schaduwmodus opgeheven op {target}")
            co = cutover_row(ctx, cutover_id)
        # 6: Seerr ----------------------------------------------------------------------------
        if co["phase"] == "lifted":
            seerr = await _switch_seerr(ctx, target, row, tgt, actor, params)
            _update(ctx, cutover_id, phase="seerr_done", seerr_json=dumps(seerr))
            log(f"Seerr: {seerr.get('status')}")
            co = cutover_row(ctx, cutover_id)
        if co["phase"] == "seerr_done":
            now = utcnow()
            ctx.db.execute(
                "INSERT INTO instance_cutover_state(instance_id,phase,source_url,updated_at,"
                "updated_by) VALUES (?,?,?,?,?) ON CONFLICT(instance_id) DO UPDATE SET "
                "phase=excluded.phase, source_url=excluded.source_url, "
                "updated_at=excluded.updated_at, updated_by=excluded.updated_by",
                (target, "cut_over", row["url"], now, actor))
            _update(ctx, cutover_id, phase="completed", status="completed", finished_at=now,
                    error=None)
            store.step_set(ctx, run_id, "cutover", target, "succeeded",
                           result={"cutover_id": cutover_id})
            if not ctx.config.value("library.write_enabled"):
                # na een geslaagde overstap is MediaGuard verantwoordelijk voor de bibliotheek
                ctx.config.set("global", "", "library.write_enabled", True, actor)
                audit(ctx.db, actor=actor, action="library.write_enabled",
                      reason="Automatisch na geslaagde overstap", new_value=True,
                      undo_hint="Zet 'MediaGuard mag bibliotheekbestanden vervangen' uit")
            audit(ctx.db, actor=actor, action="migration.cutover.completed",
                  target_type="instance", target_id=target, instance_id=target,
                  new_value={"run_id": run_id, "source_id": sid})
    except (CutoverAbort, ArrError, StepBlocked) as exc:
        msg = exc.message if isinstance(exc, ArrError) else str(exc)
        _update(ctx, cutover_id, status="failed", error=msg)
        store.step_set(ctx, run_id, "cutover", target, "failed", error=msg)
        audit(ctx.db, actor=actor, action="migration.cutover.failed", target_type="instance",
              target_id=target, instance_id=target, reason=msg)
        raise CutoverAbort(msg) from exc
    return cutover_row(ctx, cutover_id)


async def _lift_shadow(ctx: AppContext, run_id: int, target: str, co: dict[str, Any],
                       live: bool) -> dict[str, Any]:
    shadow = (store.shadow_state(ctx, run_id, target) or {}).get("state", {})
    live_state = loads(co["source_state_json"], {}) if live else {}
    idx_state = {v["name"]: v for v in (live_state.get("indexers") or {}).values()} \
        or shadow.get("indexers", {})
    dc_state = live_state.get("download_clients") or shadow.get("download_clients", {})
    il_state = live_state.get("import_lists") or shadow.get("import_lists", {})
    tgt = arr_client(ctx, target)
    applied: dict[str, Any] = {"indexers": {}, "download_clients": {}, "import_lists": {},
                               "not_matched": []}
    for idx in await tgt.indexers():
        prev = idx_state.get(idx["name"])
        if prev is None:
            applied["not_matched"].append(f"indexer:{idx['name']}")
            continue
        want = {f: bool(prev.get(f)) for f in FREEZE_FIELDS}
        if any(bool(idx.get(f)) != v for f, v in want.items()):
            await tgt.provider_update("indexer", {**idx, **want}, force_save=True)
        applied["indexers"][idx["name"]] = want
    for dc in await tgt.download_clients():
        prev = dc_state.get(dc["name"])
        if prev is None:
            applied["not_matched"].append(f"downloadclient:{dc['name']}")
            continue
        want_on = bool(prev.get("enable"))
        if bool(dc.get("enable")) != want_on:
            await tgt.provider_update("downloadclient", {**dc, "enable": want_on},
                                      force_save=True)
        applied["download_clients"][dc["name"]] = want_on
    try:
        lists = await tgt.provider_list("importlist")
    except ArrError:
        lists = []
    for il in lists:
        prev = il_state.get(il["name"])
        if not prev:
            continue
        want = {f: v for f, v in prev.items() if f in il}
        if any(il.get(f) != v for f, v in want.items()):
            await tgt.provider_update("importlist", {**il, **want}, force_save=True)
        applied["import_lists"][il["name"]] = want
    interval = (live_state.get("config") or {}).get("rsssyncinterval")
    if interval is None:
        interval = (shadow.get("config") or {}).get("rsssyncinterval")
    try:
        cfg = await tgt.get("config/indexer")
        if isinstance(cfg, dict):
            value = int(interval) if interval not in (None, "") else DEFAULT_RSS_INTERVAL
            await tgt.put("config/indexer", {**cfg, "rssSyncInterval": value})
            applied["rss_sync_interval"] = value
    except ArrError:
        pass
    return applied


async def _switch_seerr(ctx: AppContext, target: str, row: dict[str, Any], tgt: Any,
                        actor: str, params: dict[str, Any]) -> dict[str, Any]:
    from mediaguard.seerr.client import SeerrError
    from mediaguard.seerr.service import seerr_cutover

    try:
        server = await find_seerr_server(ctx, row, params.get("seerr_server_id"))
    except SeerrError as exc:
        if exc.code == "SEERR_NOT_CONFIGURED":
            return {"status": "skipped", "message_nl": "Seerr niet ingesteld."}
        raise CutoverAbort(f"Seerr niet bereikbaar: {exc.message}") from exc
    if server is None:
        return {"status": "skipped", "message_nl": "Geen Seerr-server wees naar de oude "
                                                   "instantie; niets omgezet."}
    overrides = await seerr_profile_overrides(ctx, row, server, tgt)
    try:
        res = await seerr_cutover(ctx, target, actor, confirm=True, server_id=int(server["id"]),
                                  overrides=overrides)
    except SeerrError as exc:
        raise CutoverAbort(f"Seerr omzetten mislukt: {exc.message}") from exc
    return {"status": "switched", "server_id": res.get("server_id"),
            "diff_fields": sorted((res.get("diff") or {}).keys())}


# -- rollback ---------------------------------------------------------------------------------
async def rollback(ctx: AppContext, run_id: int, target: str, actor: str,
                   log: Log) -> dict[str, Any]:
    co = latest_cutover(ctx, run_id, target)
    if co is None or co["status"] == "rolled_back":
        raise StepBlocked("Geen cutover om terug te draaien")
    cid = int(co["id"])
    _update(ctx, cid, status="rolling_back")
    rb = loads(co["rollback_json"], {}) or {}
    row = source_row(ctx, co["source_id"])
    assert row is not None
    try:
        # 1. doel opnieuw in schaduw (eerst! anders even twee grabbers)
        if not rb.get("target_resuspended"):
            fixed = await enforce_shadow(arr_client(ctx, target))
            update_instance(ctx, target, {"shadow_mode": True})
            active = [i["name"] for i in await arr_client(ctx, target).indexers()
                      if any(i.get(f) for f in FREEZE_FIELDS)]
            if active:
                raise CutoverAbort(f"Doel-indexers blijven aan ({', '.join(active)}); oude "
                                   "instantie NIET heractiveerd")
            rb["target_resuspended"] = {"disabled": fixed, "at": utcnow()}
            _update(ctx, cid, rollback_json=dumps(rb))
            log(f"{target} terug in schaduwmodus")
        # 2. Seerr terugzetten
        seerr = loads(co["seerr_json"], {}) or {}
        if seerr.get("status") == "switched" and not rb.get("seerr"):
            from mediaguard.seerr.service import seerr_rollback

            res = await seerr_rollback(ctx, target, actor, confirm=True)
            rb["seerr"] = res
            _update(ctx, cid, rollback_json=dumps(rb))
            log("Seerr teruggezet naar de oude server-instellingen")
        # 3. oude instantie exact herstellen
        state = loads(co["source_state_json"], {}) or {}
        if row["kind"] == "live" and state.get("indexers"):
            client = source_client(ctx, row["id"], grant="freeze")
            restored = []
            for idx_id, prev in state["indexers"].items():
                try:
                    cur = await client.provider_get("indexer", int(idx_id))
                except ArrError as exc:
                    if exc.status == 404:
                        continue
                    raise
                want = {f: bool(prev[f]) for f in FREEZE_FIELDS}
                if any(bool(cur.get(f)) != v for f, v in want.items()):
                    await client.provider_update("indexer", {**cur, **want}, force_save=True)
                restored.append(int(idx_id))
            check = {str(i["id"]): {f: bool(i.get(f)) for f in FREEZE_FIELDS}
                     for i in await source_client(ctx, row["id"]).indexers()}
            wrong = [k for k, prev in state["indexers"].items() if k in check and
                     check[k] != {f: bool(prev[f]) for f in FREEZE_FIELDS}]
            if wrong:
                raise CutoverAbort(f"Oude indexers niet exact hersteld: {wrong}")
            rb["old_restored"] = restored
            log(f"Oude instantie hersteld ({len(restored)} indexer(s) exact als voorheen)")
        now = utcnow()
        rb["at"] = now
        _update(ctx, cid, status="rolled_back", rolled_back_at=now, rollback_json=dumps(rb),
                phase="rolled_back")
        ctx.db.execute("UPDATE instance_cutover_state SET phase='rolled_back', updated_at=?, "
                       "updated_by=? WHERE instance_id=?", (now, actor, target))
        store.step_set(ctx, run_id, "cutover", target, "pending", result={"rolled_back": cid})
        audit(ctx.db, actor=actor, action="migration.rollback", target_type="instance",
              target_id=target, instance_id=target, new_value=rb)
        return cutover_row(ctx, cid)
    except (CutoverAbort, ArrError) as exc:
        msg = exc.message if isinstance(exc, ArrError) else str(exc)
        _update(ctx, cid, status="failed", error=f"rollback: {msg}", rollback_json=dumps(rb))
        audit(ctx.db, actor=actor, action="migration.rollback.failed", target_type="instance",
              target_id=target, instance_id=target, reason=msg)
        raise CutoverAbort(msg) from exc


# -- post-cutover audit -----------------------------------------------------------------------
GRAB_EVENTS = {"grabbed", "1"}
IMPORT_EVENTS = {"downloadFolderImported", "3", "episodeFileImported", "movieFileImported"}


def _events(records: list[dict[str, Any]], since: datetime) -> dict[str, int]:
    out = {"grabbed": 0, "imported": 0, "failed": 0}
    for r in records:
        when = parse_dt(r.get("date"))
        if when is None or when < since:
            continue
        ev = str(r.get("eventType"))
        if ev in GRAB_EVENTS:
            out["grabbed"] += 1
        elif ev in IMPORT_EVENTS:
            out["imported"] += 1
        elif "failed" in ev.lower():
            out["failed"] += 1
    return out


async def post_audit(ctx: AppContext, run_id: int, target: str, hours: int,
                     actor: str) -> dict[str, Any]:
    co = latest_cutover(ctx, run_id, target)
    if co is None or co["status"] != "completed":
        raise StepBlocked("Post-cutover audit kan pas na een voltooide cutover")
    since = parse_dt(co["finished_at"]) or datetime.now(UTC)
    elapsed_h = (datetime.now(UTC) - since).total_seconds() / 3600
    row = source_row(ctx, co["source_id"])
    assert row is not None
    tgt = arr_client(ctx, target)
    since_iso = since.strftime("%Y-%m-%dT%H:%M:%SZ")
    issues: list[dict[str, Any]] = []
    target_events = _events(records_of(await tgt.history_since(since_iso)), since)
    old_events = None
    if row["kind"] == "live":
        src = source_client(ctx, row["id"])
        old_events = _events(records_of(await src.history_since(since_iso)), since)
        if old_events["grabbed"]:
            issues.append({"severity": "no_go", "message_nl": f"De OUDE instantie heeft na de "
                           f"cutover nog {old_events['grabbed']} keer gegrepen."})
        active = [i["name"] for i in await src.indexers() if any(i.get(f) for f in FREEZE_FIELDS)]
        if active:
            issues.append({"severity": "no_go", "message_nl": "Oude indexers staan weer aan: "
                           + ", ".join(active)})
    health = await tgt.health()
    for h in health:
        if str(h.get("type")).lower() in ("error", "warning"):
            issues.append({"severity": "warning", "message_nl": str(h.get("message"))})
    failing = [s for s in await tgt.indexer_status() if s.get("disabledTill")]
    if failing:
        issues.append({"severity": "warning", "message_nl": f"{len(failing)} indexer(s) "
                       "tijdelijk uitgeschakeld door fouten."})
    if target_events["failed"]:
        issues.append({"severity": "warning", "message_nl": f"{target_events['failed']} "
                       "mislukte download(s)/import(s) op het doel."})
    queue = await tgt.get("queue", page=1, pageSize=1)
    window_done = elapsed_h >= hours
    v = "no_go" if any(i["severity"] == "no_go" for i in issues) else \
        "warn" if issues else "go" if window_done else "info"
    summary = {"since": since_iso, "window_hours": hours, "elapsed_hours": round(elapsed_h, 2),
               "window_complete": window_done, "target": target_events, "old": old_events,
               "queue": int((queue or {}).get("totalRecords") or 0), "issues": issues,
               "until": (since + timedelta(hours=hours)).isoformat(timespec="seconds")}
    store.add_report(ctx, run_id, "post_cutover", v, source_id=row["id"], target=target,
                     summary=summary, actor=actor)
    status = {"go": "succeeded", "warn": "warning", "no_go": "failed", "info": "running"}[v]
    store.step_set(ctx, run_id, "post_cutover", target, status, result=summary)
    return {"verdict": v, **summary}
