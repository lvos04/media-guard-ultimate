"""Optimaliseren na migratie.

De oude Sonarr/Radarr-instellingen van de gebruiker worden na de migratie NIET zomaar
overgenomen maar geoptimaliseerd met de MediaGuard-sjablonen:

BEHOUDEN (nooit verliezen): series/films, gemonitorde vlaggen op elk niveau,
monitorNewItems, aflevering-uitzonderingen, tags op items, historie, bestaande bestanden en
hun koppeling, inloggegevens/URL's van indexers en downloadclients, remote path mappings,
rootmappen met media.

VERVANGEN/OPTIMALISEREN: kwaliteitsprofielen + custom formats, kwaliteitsdefinities,
vertragingsprofiel, releaseprofielen (uitgezet), naamgeving (alleen voor nieuwe imports),
mediabeheer, downloadcategorie per instantie, indexertoewijzing (anime-indexers alleen op
anime-instanties), taal en de webhook. Elk item krijgt het passende nieuwe profiel; oude
profielen heten 'Oud · <naam>' tot de gebruiker opruimen bevestigt.

Vooraf: een volledige momentopname, zodat 'Terugzetten naar gemigreerde instellingen'
exact herstelt (geheimen hergebruikt, nooit getoond). Bestanden worden NOOIT automatisch
hernoemd of verplaatst; daarvoor is er een aparte actie met voorvertoning en bevestiging.
Zoekacties voor upgrades lopen via de backfill-quota (geen indexer-flood)."""

from __future__ import annotations

import copy
import math
from typing import TYPE_CHECKING, Any

from mediaguard import jobs
from mediaguard.arr.client import ArrClient, ArrError
from mediaguard.audit import audit
from mediaguard.db import dumps, loads, utcnow
from mediaguard.instances.registry import arr_client, instance_row, update_instance
from mediaguard.provisioning import engine, store
from mediaguard.provisioning import templates as T
from mediaguard.provisioning.store import MASK

if TYPE_CHECKING:
    from mediaguard.context import AppContext

OPTIMIZE_SECTIONS = ("tags", "rootfolders", "mediamanagement", "naming", "qualitydefinitions",
                     "customformats", "qualityprofiles", "delayprofile", "releaseprofiles",
                     "downloadclients", "indexers", "webhook")
SNAP_PROVIDERS = ("downloadclient", "indexer")
SNAP_LISTS = ("customformat", "qualityprofile", "delayprofile", "releaseprofile")
SNAP_CONFIG = ("config/naming", "config/mediamanagement")


class OptimizeError(Exception):
    pass


async def _opt(client: ArrClient, path: str, default: Any) -> Any:
    try:
        data = await client.get(path)
    except ArrError as exc:
        if exc.status in (404, 405):
            return default
        raise
    return default if data is None else data


# -- doelen --------------------------------------------------------------------------------
def candidates(ctx: AppContext, instances: list[str] | None) -> list[dict[str, Any]]:
    rows = engine.eligible(ctx, instances)
    if instances:
        return rows
    return [r for r in rows if store.shadow_import_done(ctx, r["id"])]


def _new_profile_template(inst: dict[str, Any], series_type: str | None = None) -> str:
    tpls = T.templates_for(inst["media_type"])
    if inst["app"] == "sonarr" and series_type == "anime" and "anime" in tpls:
        return "anime"
    return tpls[0]


# -- impact --------------------------------------------------------------------------------
def _rank(order: list[str]) -> dict[str, int]:
    return {n: i for i, n in enumerate(order)}


def _is_candidate(quality: str | None, tpl_id: str, rank: dict[str, int]) -> bool:
    tpl = T.QUALITY_TEMPLATES[tpl_id]
    if not quality:
        return False
    cutoff = rank.get(tpl["cutoff"])
    q = rank.get(quality)
    if cutoff is None or q is None:
        return quality not in tpl["allowed"]
    return q < cutoff


async def impact(ctx: AppContext, inst: dict[str, Any], client: ArrClient) -> dict[str, Any]:
    """Hoeveel items na optimaliseren 'cutoff niet gehaald' worden (upgradekandidaat) en
    hoeveel zoekacties dat ongeveer kost. Op basis van kwaliteit; custom-format-upgrades
    (bijv. Dual Audio) kan Sonarr/Radarr pas bij een zoekactie beoordelen."""
    from mediaguard.wanted.backfill import cfg as bf_cfg

    schema = await _opt(client, "qualityprofile/schema", {})
    rank = _rank(engine.ordered_leaves(schema)) if schema else {}
    try:
        before = await client.get("wanted/cutoff", page=1, pageSize=1)
    except ArrError:
        before = {}
    before_n = int((before or {}).get("totalRecords") or 0) if isinstance(before, dict) else 0
    out: dict[str, Any] = {"items": 0, "items_with_files": 0, "files": 0,
                           "upgrade_candidates": 0, "upgrade_items": 0,
                           "cutoff_unmet_before": before_n, "by_profile": {}}
    searches = 0
    if inst["app"] == "sonarr":
        threshold = int(bf_cfg(ctx, "backfill.season_search_threshold", inst["id"]))
        for s in await client.series_list():
            out["items"] += 1
            tpl = _new_profile_template(inst, s.get("seriesType"))
            out["by_profile"][T.owned_name(T.QUALITY_TEMPLATES[tpl]["name"])] = \
                out["by_profile"].get(T.owned_name(T.QUALITY_TEMPLATES[tpl]["name"]), 0) + 1
            if not int((s.get("statistics") or {}).get("episodeFileCount") or 0):
                continue
            out["items_with_files"] += 1
            try:
                files = await client.episode_files(int(s["id"]))
            except ArrError as exc:
                # één serie met kapotte gegevens in Sonarr mag de hele analyse niet stoppen
                out.setdefault("unreadable", []).append(
                    {"series_id": s.get("id"), "title": s.get("title"), "error": exc.message})
                continue
            per_season: dict[int, int] = {}
            for f in files:
                out["files"] += 1
                q = ((f.get("quality") or {}).get("quality") or {}).get("name")
                if _is_candidate(q, tpl, rank):
                    out["upgrade_candidates"] += 1
                    n = int(f.get("seasonNumber") or 0)
                    per_season[n] = per_season.get(n, 0) + 1
            if per_season:
                out["upgrade_items"] += 1
                searches += sum(1 if c >= threshold else c for c in per_season.values())
    else:
        tpl = _new_profile_template(inst)
        for m in await client.movie_list():
            out["items"] += 1
            name = T.owned_name(T.QUALITY_TEMPLATES[tpl]["name"])
            out["by_profile"][name] = out["by_profile"].get(name, 0) + 1
            mf = m.get("movieFile")
            if not m.get("hasFile") or not mf:
                continue
            out["items_with_files"] += 1
            out["files"] += 1
            q = ((mf.get("quality") or {}).get("quality") or {}).get("name")
            if _is_candidate(q, tpl, rank):
                out["upgrade_candidates"] += 1
                out["upgrade_items"] += 1
                searches += 1
    per_day = max(1, int(bf_cfg(ctx, "backfill.max_searches_per_day", inst["id"])))
    out.update(estimated_searches=searches, searches_per_day=per_day,
               estimated_days=math.ceil(searches / per_day) if searches else 0,
               include_cutoff_unmet=bool(bf_cfg(ctx, "backfill.include_cutoff_unmet",
                                               inst["id"])))
    out["message_nl"] = (
        f"{out['upgrade_candidates']} bestand(en) in {out['upgrade_items']} titel(s) halen "
        f"het nieuwe profiel nog niet (upgradekandidaat). Geschat {searches} zoekactie(s); "
        f"die lopen via de backfill-quota (max {per_day}/dag, ±{out['estimated_days']} "
        "dag(en)), nooit allemaal tegelijk. Bestanden worden niet hernoemd.")
    if not out["include_cutoff_unmet"]:
        out["message_nl"] += (" Let op: 'Ook upgrades zoeken' staat uit; upgrades komen dan "
                              "alleen via RSS binnen.")
    return out


# -- voorvertoning -------------------------------------------------------------------------
async def preview_instance(ctx: AppContext, inst: dict[str, Any], sections: list[str],
                           actor: str) -> dict[str, Any]:
    cfg = store.load(ctx)
    iid = inst["id"]
    view, changes = await engine.plan_instance(ctx, cfg, inst, sections, actor,
                                               mode="optimized", assume_migrated=True)
    extra: list[dict[str, Any]] = []
    async with arr_client(ctx, iid) as client:
        profiles = await client.quality_profiles()
        if "qualityprofiles" in sections:
            for p in profiles:
                name = str(p.get("name"))
                if T.is_owned_name(name) or name.startswith(T.OLD_PREFIX):
                    continue
                extra.append({"section": "qualityprofiles", "action": "update",
                              "key": f"old:{p['id']}",
                              "title_nl": f"Oud profiel '{name}' hernoemen naar "
                                          f"'{T.OLD_PREFIX}{name}' (blijft tot opruimen)",
                              "diff": [engine.diff_line("name", "Naam", name,
                                                        f"{T.OLD_PREFIX}{name}")]})
        if "releaseprofiles" in sections:
            for rp in await _opt(client, "releaseprofile", []):
                if rp.get("enabled", True):
                    extra.append({"section": "releaseprofiles", "action": "update",
                                  "key": f"releaseprofile:{rp.get('id')}",
                                  "title_nl": f"Oud releaseprofiel '{rp.get('name') or rp['id']}'"
                                              " uitzetten (custom formats nemen het over)",
                                  "diff": [engine.diff_line("enabled", "Aan", True, False)]})
        imp = await impact(ctx, inst, client) if "qualityprofiles" in sections else None
    reassign = None
    if imp is not None:
        reassign = {"items": imp["items"], "by_profile": imp["by_profile"],
                    "message_nl": f"Alle {imp['items']} titel(s) krijgen het passende nieuwe "
                                  "profiel. Gemonitorde vlaggen, seizoenen en afleveringen "
                                  "blijven exact gelijk."}
    return {"instance_id": iid, "name": inst["name"], "sections": view["sections"],
            "extra_changes": extra, "reassign": reassign, "impact": imp,
            "changes": sum(1 for c in changes if c.pending) + len(extra),
            "text_nl": engine.text_diff(engine.Plan(changes=changes))
            + [f"~ [{iid}] {store.SECTION_NL.get(e['section'])}: {e['title_nl']}"
               for e in extra],
            "keeps_nl": ["Alle series/films en hun gemonitorde status (serie, seizoen, "
                         "aflevering) en 'nieuwe seizoenen volgen'",
                         "Tags op items, historie, bestanden en hun koppelingen",
                         "Inloggegevens en URL's van indexers en downloadclients",
                         "Remote path mappings en rootmappen met media"],
            "no_renames_nl": "Bestaande bestanden worden niet hernoemd of verplaatst; de nieuwe "
                             "naamgeving geldt voor nieuwe imports. Hernoemen kan apart via "
                             "'Bestanden hernoemen volgens nieuw schema'."}


async def preview(ctx: AppContext, instances: list[str] | None, sections: list[str] | None,
                  actor: str) -> dict[str, Any]:
    secs = [s for s in (sections or OPTIMIZE_SECTIONS) if s in OPTIMIZE_SECTIONS]
    out = []
    for inst in candidates(ctx, instances):
        opt = store.optimize_row(ctx, inst["id"])
        if opt and opt["phase"] in ("optimized", "cleaned"):
            out.append({"instance_id": inst["id"], "name": inst["name"], "already": True,
                        "message_nl": "Al geoptimaliseerd."})
            continue
        try:
            out.append(await preview_instance(ctx, inst, secs, actor))
        except ArrError as exc:
            out.append({"instance_id": inst["id"], "name": inst["name"], "error": exc.message})
    return {"requires_confirmation": True, "sections": secs, "instances": out,
            "message_nl": "Controleer de voorvertoning en bevestig om te optimaliseren. Er "
                          "wordt eerst een volledige momentopname gemaakt voor terugzetten."}


# -- momentopname -----------------------------------------------------------------------------
def _secret_name(sid: int, kind: str, item_id: Any, field: str) -> str:
    return f"provisioning:snapshot:{sid}:{kind}:{item_id}:{field}"


async def snapshot(ctx: AppContext, inst: dict[str, Any], client: ArrClient,
                   actor: str) -> int:
    iid = inst["id"]
    sid = ctx.db.execute_insert("INSERT INTO provisioning_snapshots(instance_id,kind,"
                                "data_json,created_at,created_by) VALUES (?,?,?,?,?)",
                                (iid, "pre_optimize", "{}", utcnow(), actor))
    data: dict[str, Any] = {"app": inst["app"],
                            "default_quality_profile_id": inst["default_quality_profile_id"]}
    for kind in SNAP_PROVIDERS:
        items = copy.deepcopy(await client.provider_list(kind))
        for item in items:
            for f in item.get("fields") or []:
                if engine.is_secret_field(str(f.get("name"))) and \
                        f.get("value") not in (None, "", MASK):
                    name = _secret_name(sid, kind, item["id"], str(f["name"]))
                    ctx.secrets.set(name, str(f["value"]), actor)
                    f["value"] = {"$secret": name}
        data[kind] = items
    for kind in SNAP_LISTS:
        data[kind] = await _opt(client, kind, [])
    data["qualitydefinition"] = await _opt(client, "qualitydefinition", [])
    for path in SNAP_CONFIG:
        data[path] = await _opt(client, path, None)
    data["rootfolder"] = [r.get("path") for r in await client.root_folders()]
    if inst["app"] == "sonarr":
        data["assignments"] = {str(s["id"]): s.get("qualityProfileId")
                               for s in await client.series_list()}
    else:
        data["assignments"] = {str(m["id"]): m.get("qualityProfileId")
                               for m in await client.movie_list()}
    ctx.db.execute("UPDATE provisioning_snapshots SET data_json=? WHERE id=?",
                   (dumps(data), sid))
    return sid


def _resolve_secrets(ctx: AppContext, body: dict[str, Any]) -> dict[str, Any]:
    out = copy.deepcopy(body)
    for f in out.get("fields") or []:
        v = f.get("value")
        if isinstance(v, dict) and "$secret" in v:
            f["value"] = ctx.secrets.get(v["$secret"]) or MASK
    return out


# -- uitvoeren --------------------------------------------------------------------------------
async def _editor(client: ArrClient, app: str, ids: list[int], profile_id: int) -> None:
    if not ids:
        return
    if app == "sonarr":
        await client.put("series/editor", {"seriesIds": ids, "qualityProfileId": profile_id,
                                           "moveFiles": False})
    else:
        await client.put("movie/editor", {"movieIds": ids, "qualityProfileId": profile_id,
                                          "moveFiles": False})


async def reassign(client: ArrClient, inst: dict[str, Any]) -> dict[str, int]:
    profiles = {str(p["name"]): int(p["id"]) for p in await client.quality_profiles()}
    groups: dict[int, list[int]] = {}
    items = await (client.series_list() if inst["app"] == "sonarr" else client.movie_list())
    for it in items:
        tpl = _new_profile_template(inst, it.get("seriesType"))
        pid = profiles.get(T.owned_name(T.QUALITY_TEMPLATES[tpl]["name"]))
        if pid is None:
            raise OptimizeError(f"Nieuw profiel voor sjabloon {tpl} ontbreekt")
        if it.get("qualityProfileId") != pid:
            groups.setdefault(pid, []).append(int(it["id"]))
    for pid, ids in groups.items():
        await _editor(client, inst["app"], ids, pid)
    return {str(k): len(v) for k, v in groups.items()}


async def optimize_instance(ctx: AppContext, inst: dict[str, Any], sections: list[str],
                            actor: str) -> dict[str, Any]:
    iid = inst["id"]
    cfg_before = store.load(ctx)
    async with arr_client(ctx, iid) as client:
        sid = await snapshot(ctx, inst, client, actor)
        # bestaande (gemigreerde) downloadclients/indexers vastleggen: inloggegevens blijven
        for kind, section in (("downloadclient", "downloadclients"), ("indexer", "indexers")):
            for item in await client.provider_list(kind):
                name = str(item.get("name"))
                if not T.is_owned_name(name) and not store.items(ctx, iid, section).get(name):
                    store.item_set(ctx, iid, section, name, remote_id=item.get("id"),
                                   ownership="migrated", actor=actor)
        old_profiles: dict[str, str] = {}
        if "qualityprofiles" in sections:
            for p in await client.quality_profiles():
                name = str(p.get("name"))
                if T.is_owned_name(name) or name.startswith(T.OLD_PREFIX):
                    continue
                full = await client.get(f"qualityprofile/{int(p['id'])}")
                await client.put(f"qualityprofile/{int(p['id'])}",
                                 {**full, "name": f"{T.OLD_PREFIX}{name}"[:100]})
                old_profiles[str(p["id"])] = name
        disabled_rp = []
        if "releaseprofiles" in sections:
            for rp in await _opt(client, "releaseprofile", []):
                if rp.get("enabled", True):
                    await client.put(f"releaseprofile/{int(rp['id'])}", {**rp, "enabled": False})
                    disabled_rp.append(rp["id"])
    now = utcnow()
    ctx.db.execute(
        "INSERT INTO provisioning_optimize(instance_id,phase,snapshot_id,old_profiles_json,"
        "result_json,optimized_at,updated_at,updated_by) VALUES (?,?,?,?,?,?,?,?) "
        "ON CONFLICT(instance_id) DO UPDATE SET phase=excluded.phase, "
        "snapshot_id=excluded.snapshot_id, old_profiles_json=excluded.old_profiles_json, "
        "result_json=excluded.result_json, optimized_at=excluded.optimized_at, error=NULL, "
        "cleaned_at=NULL, rolled_back_at=NULL, updated_at=excluded.updated_at, "
        "updated_by=excluded.updated_by",
        (iid, "optimized", sid, dumps(old_profiles), "{}", now, now, actor))
    if cfg_before.instance(iid).mode == "hold":
        inst_cfgs = {k: v.model_dump() for k, v in cfg_before.instances.items()}
        inst_cfgs[iid] = {**inst_cfgs[iid], "mode": "auto"}
        store.save(ctx, {"instances": inst_cfgs}, actor)
    secs = [s for s in sections if s != "releaseprofiles"]
    res = await engine.apply(ctx, actor=actor, instances=[iid], sections=secs,
                             include_deletes=False, kind="optimize")
    async with arr_client(ctx, iid) as client:
        moved = await reassign(client, inst) if "qualityprofiles" in sections else {}
        profiles = {str(p["name"]): int(p["id"]) for p in await client.quality_profiles()}
    main = T.owned_name(T.QUALITY_TEMPLATES[_new_profile_template(inst)]["name"])
    if main in profiles:
        update_instance(ctx, iid, {"default_quality_profile_id": profiles[main]})
    store.mark_initialized(ctx, iid)
    result = {"snapshot_id": sid, "old_profiles": old_profiles,
              "disabled_release_profiles": disabled_rp, "reassigned": moved,
              "apply": {k: res[k] for k in ("status", "summary", "failed", "remaining")}}
    ctx.db.execute("UPDATE provisioning_optimize SET result_json=?, updated_at=? WHERE "
                   "instance_id=?", (dumps(result), utcnow(), iid))
    jobs.enqueue(ctx.db, "library.sync", {}, instance_id=iid, max_attempts=2,
                 idempotency_key=f"library.sync:{iid}:optimize:{sid}", created_by=actor)
    audit(ctx.db, actor=actor, action="provisioning.optimize_migrated", target_type="instance",
          target_id=iid, instance_id=iid,
          new_value={"snapshot_id": sid, "old_profiles": old_profiles, "reassigned": moved},
          undo_hint="POST /api/v1/provisioning/optimize-migrated/rollback")
    return {"instance_id": iid, "status": res["status"], **result}


async def optimize(ctx: AppContext, *, instances: list[str] | None,
                   sections: list[str] | None, confirm: bool, actor: str) -> dict[str, Any]:
    if not confirm:
        return await preview(ctx, instances, sections, actor)
    if ctx.emergency_stopped():
        raise OptimizeError("Noodstop actief: optimaliseren geblokkeerd")
    secs = [s for s in (sections or OPTIMIZE_SECTIONS) if s in OPTIMIZE_SECTIONS]
    run_id = store.run_start(ctx, "optimize", actor, secs, instances or [])
    results = []
    for inst in candidates(ctx, instances):
        opt = store.optimize_row(ctx, inst["id"])
        if opt and opt["phase"] in ("optimized", "cleaned"):
            results.append({"instance_id": inst["id"], "status": "skipped",
                            "message_nl": "Al geoptimaliseerd."})
            continue
        try:
            results.append(await optimize_instance(ctx, inst, secs, actor))
        except (ArrError, OptimizeError) as exc:
            msg = exc.message if isinstance(exc, ArrError) else str(exc)
            ctx.db.execute("UPDATE provisioning_optimize SET phase='failed', error=?, "
                           "updated_at=? WHERE instance_id=?", (msg, utcnow(), inst["id"]))
            results.append({"instance_id": inst["id"], "status": "failed", "error": msg,
                            "message_nl": "Mislukt; gebruik 'Terugzetten naar gemigreerde "
                                          "instellingen' om exact te herstellen."})
    status = "failed" if any(r["status"] == "failed" for r in results) else "succeeded"
    store.run_finish(ctx, run_id, status, {"instances": len(results)}, results)
    return {"run_id": run_id, "status": status, "instances": results}


# -- opruimen ------------------------------------------------------------------------------------
def _new_default_profile(ctx: AppContext, inst: dict[str, Any], current: dict[int, Any],
                         old: set[int]) -> int | None:
    """Het nieuwe (MediaGuard-)standaardprofiel van deze instantie, nooit een oud profiel."""
    row = ctx.db.fetch_one("SELECT default_quality_profile_id, media_type FROM instances "
                           "WHERE id=?", (inst["id"],))
    pid = row["default_quality_profile_id"] if row else None
    if pid is not None and int(pid) in current and int(pid) not in old:
        return int(pid)
    tpls = T.templates_for((row["media_type"] if row else None) or inst.get("media_type"))
    want = T.owned_name(T.QUALITY_TEMPLATES[tpls[0]]["name"]) if tpls else None
    return next((i for i, p in current.items() if p.get("name") == want and i not in old),
                None)


async def cleanup(ctx: AppContext, *, instances: list[str] | None, confirm: bool,
                  actor: str) -> dict[str, Any]:
    out = []
    for inst in engine.eligible(ctx, instances):
        opt = store.optimize_row(ctx, inst["id"])
        if not opt or opt["phase"] != "optimized":
            continue
        old = loads(opt["old_profiles_json"], {})
        async with arr_client(ctx, inst["id"]) as client:
            items = await (client.series_list() if inst["app"] == "sonarr"
                           else client.movie_list())
            used = {int(i.get("qualityProfileId") or 0) for i in items}
            current = {int(p["id"]): p for p in await client.quality_profiles()}
            # Importlijsten verwijzen ook naar een profiel (Radarr: 'QualityProfile is in use')
            lists = [x for x in await client.provider_list("importlist") or []
                     if int(x.get("qualityProfileId") or 0) in {int(k) for k in old}]
            target = _new_default_profile(ctx, inst, current, {int(k) for k in old})
            plan = []
            for pid, name in old.items():
                p = current.get(int(pid))
                if p is None:
                    continue
                on_lists = [str(x.get("name")) for x in lists
                            if int(x.get("qualityProfileId") or 0) == int(pid)]
                plan.append({"profile_id": int(pid), "name": p.get("name"), "original": name,
                             "in_use": int(pid) in used or (bool(on_lists) and target is None),
                             "import_lists": on_lists})
            if confirm:
                for entry in plan:
                    if entry["in_use"]:
                        continue
                    try:
                        # importlijsten eerst naar het nieuwe MediaGuard-profiel
                        for x in lists:
                            if int(x.get("qualityProfileId") or 0) == entry["profile_id"]:
                                await client.provider_update(
                                    "importlist", {**x, "qualityProfileId": target},
                                    force_save=True)
                        await client.delete(f"qualityprofile/{entry['profile_id']}")
                        entry["deleted"] = True
                    except ArrError as exc:
                        entry["in_use"] = True
                        entry["error"] = exc.message
                if not any(e["in_use"] for e in plan):
                    ctx.db.execute("UPDATE provisioning_optimize SET phase='cleaned', "
                                   "cleaned_at=?, updated_at=? WHERE instance_id=?",
                                   (utcnow(), utcnow(), inst["id"]))
                audit(ctx.db, actor=actor, action="provisioning.optimize_cleanup",
                      target_type="instance", target_id=inst["id"], instance_id=inst["id"],
                      old_value=plan)
        out.append({"instance_id": inst["id"], "profiles": plan})
    return {"requires_confirmation": not confirm, "instances": out,
            "message_nl": "Oude profielen die nog in gebruik zijn worden niet verwijderd."}


# -- terugzetten ----------------------------------------------------------------------------------
def _strip(body: dict[str, Any]) -> dict[str, Any]:
    return {k: v for k, v in body.items() if k != "id"}


async def rollback_instance(ctx: AppContext, inst: dict[str, Any],  # noqa: C901
                            actor: str) -> dict[str, Any]:
    iid = inst["id"]
    opt = store.optimize_row(ctx, iid)
    if not opt or not opt["snapshot_id"]:
        raise OptimizeError("Geen momentopname om naar terug te zetten")
    row = ctx.db.fetch_one("SELECT data_json FROM provisioning_snapshots WHERE id=?",
                           (opt["snapshot_id"],))
    snap = loads(row["data_json"], {}) if row else {}
    report: dict[str, Any] = {}
    async with arr_client(ctx, iid) as client:
        for path in SNAP_CONFIG:
            if snap.get(path):
                await client.put(path, snap[path])
        if snap.get("qualitydefinition"):
            await client.put("qualitydefinition/update", snap["qualitydefinition"])
        for kind in ("delayprofile", "releaseprofile"):
            snap_items = snap.get(kind) or []
            cur = {int(x["id"]): x for x in await _opt(client, kind, [])}
            for item in snap_items:
                if int(item["id"]) in cur:
                    await client.put(f"{kind}/{int(item['id'])}", item)
                else:
                    await client.post(kind, _strip(item))
            keep = {int(x["id"]) for x in snap_items}
            for xid in cur:
                if xid not in keep and xid != 1:
                    await client.delete(f"{kind}/{xid}")
        # custom formats: ontbrekende terugzetten (id-mapping), bestaande herstellen
        cf_map: dict[int, int] = {}
        cur_cf = {int(c["id"]): c for c in await _opt(client, "customformat", [])}
        for cf in snap.get("customformat") or []:
            if int(cf["id"]) in cur_cf:
                await client.put(f"customformat/{int(cf['id'])}", cf)
                cf_map[int(cf["id"])] = int(cf["id"])
            else:
                rec = await client.post("customformat", _strip(cf))
                cf_map[int(cf["id"])] = int(rec["id"])
        all_cf = await _opt(client, "customformat", [])
        # kwaliteitsprofielen exact terug (naam, items, scores)
        pmap: dict[int, int] = {}
        cur_p = {int(p["id"]): p for p in await client.quality_profiles()}
        for p in snap.get("qualityprofile") or []:
            body = copy.deepcopy(p)
            items = []
            for fi in body.get("formatItems") or []:
                fid = cf_map.get(int(fi["format"]), int(fi["format"]))
                items.append({**fi, "format": fid})
            # Sonarr/Radarr eisen alle bestaande CF's in formatItems: tijdelijk de eigen
            # (straks verwijderde) CF's met score 0 aanvullen
            have = {fi["format"] for fi in items}
            snap_ids = set(cf_map.values())
            items += [{"format": int(c["id"]), "name": c["name"], "score": 0}
                      for c in all_cf if int(c["id"]) not in have | snap_ids]
            body["formatItems"] = items
            if int(p["id"]) in cur_p:
                await client.put(f"qualityprofile/{int(p['id'])}", body)
                pmap[int(p["id"])] = int(p["id"])
            else:
                rec = await client.post("qualityprofile", _strip(body))
                pmap[int(p["id"])] = int(rec["id"])
        # toewijzing per item terug
        groups: dict[int, list[int]] = {}
        for item_id, pid in (snap.get("assignments") or {}).items():
            if pid is not None:
                groups.setdefault(pmap.get(int(pid), int(pid)), []).append(int(item_id))
        current_items = {int(i["id"]): i for i in await (
            client.series_list() if inst["app"] == "sonarr" else client.movie_list())}
        for pid, ids in groups.items():
            ids = [i for i in ids if i in current_items and
                   current_items[i].get("qualityProfileId") != pid]
            await _editor(client, inst["app"], ids, pid)
        # eigen profielen/custom formats verwijderen (alleen als niet meer in gebruik)
        used = {int(i.get("qualityProfileId") or 0) for i in await (
            client.series_list() if inst["app"] == "sonarr" else client.movie_list())}
        kept = []
        snap_pids = set(pmap.values())
        for p in await client.quality_profiles():
            if int(p["id"]) in snap_pids:
                continue
            if int(p["id"]) in used:
                kept.append(p["name"])
                continue
            await client.delete(f"qualityprofile/{int(p['id'])}")
        snap_cfs = set(cf_map.values())
        for c in await _opt(client, "customformat", []):
            if int(c["id"]) not in snap_cfs:
                await client.delete(f"customformat/{int(c['id'])}")
        # downloadclients / indexers exact terug (geheimen hergebruikt)
        for kind in SNAP_PROVIDERS:
            snap_items = snap.get(kind) or []
            cur = {int(x["id"]): x for x in await client.provider_list(kind)}
            for item in snap_items:
                body = _resolve_secrets(ctx, item)
                if int(item["id"]) in cur:
                    await client.provider_update(kind, body, force_save=True)
                else:
                    await client.provider_create(kind, _strip(body), force_save=True)
            keep = {int(x["id"]) for x in snap_items}
            for xid in cur:
                if xid not in keep:
                    await client.provider_delete(kind, xid)
        report["kept_in_use_profiles"] = kept
    if snap.get("default_quality_profile_id") is not None:
        old_default = int(snap["default_quality_profile_id"])
        update_instance(ctx, iid, {"default_quality_profile_id": pmap.get(old_default,
                                                                         old_default)})
    ctx.db.execute("DELETE FROM provisioning_items WHERE instance_id=? AND section NOT IN "
                   "('webhook','rootfolders','tags')", (iid,))
    cfg = store.load(ctx)
    inst_cfgs = {k: v.model_dump() for k, v in cfg.instances.items()}
    inst_cfgs[iid] = {**inst_cfgs.get(iid, store.InstanceCfg().model_dump()), "mode": "hold"}
    store.save(ctx, {"instances": inst_cfgs}, actor)
    ctx.state_set(f"provisioning:initialized:{iid}", False)
    ctx.db.execute("UPDATE provisioning_optimize SET phase='rolled_back', rolled_back_at=?, "
                   "updated_at=?, updated_by=? WHERE instance_id=?",
                   (utcnow(), utcnow(), actor, iid))
    audit(ctx.db, actor=actor, action="provisioning.optimize_rollback", target_type="instance",
          target_id=iid, instance_id=iid, new_value={"snapshot_id": opt["snapshot_id"],
                                                     **report})
    return {"instance_id": iid, "rolled_back": True, "snapshot_id": opt["snapshot_id"],
            **report}


async def rollback(ctx: AppContext, *, instances: list[str] | None, confirm: bool,
                   actor: str) -> dict[str, Any]:
    targets = [r for r in engine.eligible(ctx, instances)
               if (store.optimize_row(ctx, r["id"]) or {}).get("phase") in
               ("optimized", "cleaned", "failed")]
    if not confirm:
        return {"requires_confirmation": True,
                "instances": [{"instance_id": r["id"], "name": r["name"]} for r in targets],
                "message_nl": "Bevestig om exact terug te zetten naar de gemigreerde "
                              "instellingen (momentopname van vóór het optimaliseren)."}
    run_id = store.run_start(ctx, "optimize_rollback", actor, [], [r["id"] for r in targets])
    results = []
    for r in targets:
        try:
            results.append(await rollback_instance(ctx, r, actor))
        except (ArrError, OptimizeError) as exc:
            results.append({"instance_id": r["id"], "rolled_back": False,
                            "error": exc.message if isinstance(exc, ArrError) else str(exc)})
    status = "succeeded" if all(x.get("rolled_back") for x in results) else "partial"
    store.run_finish(ctx, run_id, status, {"instances": len(results)}, results)
    return {"run_id": run_id, "status": status, "instances": results}


# -- bestanden hernoemen (aparte, expliciete actie) ------------------------------------------
def _locked_ids(ctx: AppContext, iid: str) -> set[int]:
    rows = ctx.db.fetch_all("SELECT resource FROM locks WHERE resource LIKE ? AND expires_at>?",
                            (f"{iid}:%", utcnow()))
    out = set()
    for r in rows:
        tail = str(r["resource"]).rsplit(":", 1)[-1]
        if tail.isdigit():
            out.add(int(tail))
    return out


async def rename_preview(ctx: AppContext, iid: str, ids: list[int] | None,
                         limit: int = 500) -> dict[str, Any]:
    inst = instance_row(ctx, iid)
    if inst is None:
        raise KeyError(iid)
    out: list[dict[str, Any]] = []
    locked = _locked_ids(ctx, iid)
    async with arr_client(ctx, iid) as client:
        if inst["app"] == "sonarr":
            owners = ids or [int(s["id"]) for s in await client.series_list()
                             if int((s.get("statistics") or {}).get("episodeFileCount") or 0)]
            for sid in owners:
                for r in await client.get("rename", seriesId=sid) or []:
                    fid = int(r.get("episodeFileId") or 0)
                    out.append({"owner_id": sid, "file_id": fid,
                                "existing_path": r.get("existingPath"),
                                "new_path": r.get("newPath"),
                                "locked": fid in locked or sid in locked})
                if len(out) >= limit:
                    break
        else:
            owners = ids or [int(m["id"]) for m in await client.movie_list() if m.get("hasFile")]
            for mid in owners:
                for r in await client.get("rename", movieId=mid) or []:
                    fid = int(r.get("movieFileId") or 0)
                    out.append({"owner_id": mid, "file_id": fid,
                                "existing_path": r.get("existingPath"),
                                "new_path": r.get("newPath"),
                                "locked": fid in locked or mid in locked})
                if len(out) >= limit:
                    break
    return {"instance_id": iid, "files": out[:limit], "count": len(out[:limit]),
            "truncated": len(out) > limit, "requires_confirmation": True,
            "message_nl": "Alleen de getoonde bestanden worden hernoemd, en pas na bevestiging. "
                          "Bestanden met een lopende AV1-taak of herstelactie worden "
                          "overgeslagen."}


async def rename_apply(ctx: AppContext, iid: str, files: list[dict[str, int]], confirm: bool,
                       actor: str) -> dict[str, Any]:
    if not confirm:
        raise OptimizeError("Hernoemen vereist expliciete bevestiging (confirm)")
    inst = instance_row(ctx, iid)
    if inst is None:
        raise KeyError(iid)
    locked = _locked_ids(ctx, iid)
    groups: dict[int, list[int]] = {}
    skipped = []
    for f in files:
        owner, fid = int(f["owner_id"]), int(f["file_id"])
        if fid in locked or owner in locked:
            skipped.append(f)
            continue
        groups.setdefault(owner, []).append(fid)
    commands = []
    async with arr_client(ctx, iid) as client:
        for owner, fids in groups.items():
            body: dict[str, Any] = {"name": "RenameFiles", "files": fids}
            body["seriesId" if inst["app"] == "sonarr" else "movieId"] = owner
            # bewust buiten de commando-whitelist: alleen na expliciete bevestiging
            cmd = await client.post("command", body)
            commands.append((cmd or {}).get("id"))
    audit(ctx.db, actor=actor, action="provisioning.rename_files", target_type="instance",
          target_id=iid, instance_id=iid,
          new_value={"files": sum(len(v) for v in groups.values()), "skipped": len(skipped)})
    run_id = store.run_start(ctx, "rename", actor, [], [iid])
    store.run_finish(ctx, run_id, "succeeded", {"files": sum(len(v) for v in groups.values()),
                                                "skipped": len(skipped)}, [])
    return {"instance_id": iid, "commands": commands, "renamed_files":
            sum(len(v) for v in groups.values()), "skipped_locked": skipped}

