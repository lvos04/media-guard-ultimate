"""Typfout in een mediamap herstellen ('Anime Fims' → 'Anime Films'), alleen op verzoek.

Herkennen gebeurt al bij het inrichten (alleen een melding). Hier staat de bewuste stap:
na expliciete bevestiging laat MediaGuard Sonarr/Radarr zélf de series/films naar de
goed gespelde map verplaatsen, in kleine porties, en wacht steeds tot het verplaatsen klaar
is. MediaGuard verwijdert nooit media; de oude hoofdmap wordt alleen uit de instantie
gehaald als die leeg is. Alleen voor de door MediaGuard beheerde forks, nooit voor de oude
installaties (migratiebronnen), en niet zolang de migratie nog in de schaduwfase zit.
"""

from __future__ import annotations

import asyncio
import contextlib
import logging
import os
from pathlib import Path
from typing import Any

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel

from mediaguard import jobs, taskwatch
from mediaguard.arr.client import ArrClient, ArrError
from mediaguard.audit import audit
from mediaguard.auth import Principal, require_operator, require_viewer
from mediaguard.context import AppContext, get_app_context
from mediaguard.instances.registry import arr_client, instance_row, list_instances

LOGGER = logging.getLogger(__name__)
router = APIRouter(prefix="/api/v1/storage", tags=["storage"])
BATCH = 5
MOVE_TIMEOUT_SECONDS = 4 * 3600
POLL_SECONDS = 5.0
HREF = "/instellingen?tab=opslag"


def suggestion(path: str) -> str | None:
    from mediaguard.provisioning.engine import _typo_hint

    hint = _typo_hint(path)
    if not hint:
        return None
    parent = path.rstrip("/").rsplit("/", 1)[0]
    return f"{parent}/{hint}"


def _norm(path: Any) -> str:
    return str(path or "").rstrip("/")


def _items_under(ctx: AppContext, instance_id: str, root: str) -> int:
    # hoofdlettergevoelig: SQLite-LIKE negeert hoofdletters, waardoor /data/media/tv en
    # /data/Media/TV allebei '98 titels' toonden (rapport 3, R18)
    row = ctx.db.fetch_one(
        "SELECT COUNT(*) AS n FROM lib_items WHERE instance_id=? AND removed_at IS NULL AND "
        "(RTRIM(root_folder_path,'/')=? OR substr(path,1,?)=?)",
        (instance_id, root, len(root) + 1, f"{root}/"))
    return int(row["n"]) if row else 0


def _size_under(ctx: AppContext, instance_id: str, root: str) -> int:
    row = ctx.db.fetch_one(
        "SELECT COALESCE(SUM(size_on_disk),0) AS n FROM lib_items WHERE instance_id=? AND "
        "removed_at IS NULL AND (RTRIM(root_folder_path,'/')=? OR substr(path,1,?)=?)",
        (instance_id, root, len(root) + 1, f"{root}/"))
    return int(row["n"]) if row else 0


def blocked_reason(ctx: AppContext, instance_id: str) -> str | None:
    inst = instance_row(ctx, instance_id)
    if inst is None or inst.get("archived"):
        return "Onbekende instantie"
    if inst.get("shadow_mode"):
        return ("Deze instantie zit nog in de schaduwfase van de migratie; je oude installatie "
                "gebruikt deze map nog. Herstel de mapnaam na de overstap.")
    if inst.get("suspended"):
        return "Deze instantie staat op pauze"
    return None


def detect_local(ctx: AppContext) -> list[dict[str, Any]]:
    """Zonder netwerk (voor 'Wacht op jou'): hoofdmappen uit de bibliotheek-sync."""
    out = []
    for inst in list_instances(ctx):
        rows = ctx.db.fetch_all(
            "SELECT RTRIM(root_folder_path,'/') AS root, COUNT(*) AS n FROM lib_items WHERE "
            "instance_id=? AND removed_at IS NULL AND root_folder_path IS NOT NULL "
            "GROUP BY RTRIM(root_folder_path,'/')", (inst.id,))
        for r in rows:
            fix = suggestion(str(r["root"]))
            if fix:
                out.append({"instance_id": inst.id, "instance_name": inst.name,
                            "path": r["root"], "suggested_path": fix, "items": int(r["n"])})
    return out


async def detect(ctx: AppContext) -> list[dict[str, Any]]:
    out: list[dict[str, Any]] = []
    for inst in list_instances(ctx):
        try:
            async with arr_client(ctx, inst.id) as client:
                roots = await client.root_folders()
        except ArrError:
            continue
        for root in roots:
            path = _norm(root.get("path"))
            fix = suggestion(path)
            if not fix:
                continue
            free = root.get("freeSpace")
            # broers in dezelfde map = zelfde dataset: hernoemen kost geen extra ruimte
            same_parent = fix.rsplit("/", 1)[0] == path.rsplit("/", 1)[0]
            need = _size_under(ctx, inst.id, path)
            out.append({
                "instance_id": inst.id, "instance_name": inst.name,
                "root_folder_id": int(root["id"]), "path": path, "suggested_path": fix,
                "items": _items_under(ctx, inst.id, path),
                "free_space_ok": bool(same_parent or free is None or int(free) >= need),
                "blocked_reason_nl": blocked_reason(ctx, inst.id)})
    return out


@router.get("/typos")
async def list_typos(_: Principal = Depends(require_viewer),
                     ctx: AppContext = Depends(get_app_context)) -> list[dict[str, Any]]:
    return await detect(ctx)


class FixBody(BaseModel):
    confirm: bool = False
    target_path: str | None = None


@router.post("/typos/{instance_id}/{root_folder_id}/fix")
async def fix_typo(instance_id: str, root_folder_id: int, body: FixBody,
                   principal: Principal = Depends(require_operator),
                   ctx: AppContext = Depends(get_app_context)) -> dict[str, Any]:
    if not body.confirm:
        raise HTTPException(400, "Bevestig dat Sonarr/Radarr de media mag verplaatsen "
                                 "(confirm: true)")
    if instance_id not in {i.id for i in list_instances(ctx)}:
        raise HTTPException(404, "Alleen voor instanties die MediaGuard beheert")
    reason = blocked_reason(ctx, instance_id)
    if reason:
        raise HTTPException(409, reason)
    found = next((t for t in await detect(ctx) if t["instance_id"] == instance_id
                  and t["root_folder_id"] == root_folder_id), None)
    if found is None:
        raise HTTPException(404, "Geen typfout gevonden in deze hoofdmap")
    target = _norm(body.target_path or found["suggested_path"])
    if not target.startswith("/") or target == found["path"] or ".." in target.split("/"):
        raise HTTPException(422, "Ongeldige doelmap")
    if not found["free_space_ok"]:
        raise HTTPException(409, "Niet genoeg vrije ruimte voor het verplaatsen")
    job_id = jobs.enqueue(
        ctx.db, "storage.typo_fix",
        {"instance_id": instance_id, "root_folder_id": root_folder_id,
         "source": found["path"], "target": target},
        instance_id=instance_id, target_type="root_folder", target_id=root_folder_id,
        idempotency_key=f"typo_fix:{instance_id}:{root_folder_id}:{target}",
        max_attempts=1, created_by=principal.actor)
    audit(ctx.db, actor=principal.actor, action="storage.typo_fix_requested",
          target_type="root_folder", target_id=str(root_folder_id), instance_id=instance_id,
          old_value={"path": found["path"]}, new_value={"path": target},
          reason="Gebruiker bevestigde het herstellen van de mapnaam")
    runner = ctx.services.get("jobs")
    if runner is not None:
        runner.wake()
    return {"job_id": job_id}


# -- tweelingmappen (rapport 2 / N11) ------------------------------------------------------
def _dir_empty(path: str) -> bool | None:
    try:
        return not any(Path(path).iterdir())
    except FileNotFoundError:
        return True
    except OSError:
        return None


async def detect_twins(ctx: AppContext) -> list[dict[str, Any]]:
    """Hoofdmappen die alleen in hoofdletters verschillen (/data/media/tv naast
    /data/Media/TV) en hoofdmappen die niet (meer) bestaan (offline)."""
    out: list[dict[str, Any]] = []
    for inst in list_instances(ctx):
        try:
            async with arr_client(ctx, inst.id) as client:
                roots = await client.root_folders()
        except Exception:  # noqa: BLE001 - instantie offline: overslaan
            continue
        paths = [_norm(r.get("path")) for r in roots]
        for r in roots:
            path = _norm(r.get("path"))
            twins = [p for p in paths if p != path and p.lower() == path.lower()]
            items = _items_under(ctx, inst.id, path)
            exists = await asyncio.to_thread(Path(path).is_dir)
            empty = await asyncio.to_thread(_dir_empty, path)
            if not twins and exists:
                continue
            out.append({"instance_id": inst.id, "instance_name": inst.name,
                        "root_folder_id": r.get("id"), "path": path, "twin_of": twins or None,
                        "offline": not exists, "items": items, "empty_on_disk": empty,
                        "removable": items == 0 and empty is True,
                        "label_nl": (f"Tweelingmap van {twins[0]}" if twins else
                                     "Map bestaat niet (offline)")})
    return out


@router.get("/twins")
async def list_twins(_: Principal = Depends(require_viewer),
                     ctx: AppContext = Depends(get_app_context)) -> list[dict[str, Any]]:
    return await detect_twins(ctx)


class RemoveBody(BaseModel):
    confirm: bool = False


@router.post("/twins/{instance_id}/{root_folder_id}/remove")
async def remove_twin(instance_id: str, root_folder_id: int, body: RemoveBody,
                      principal: Principal = Depends(require_operator),
                      ctx: AppContext = Depends(get_app_context)) -> dict[str, Any]:
    """Lege tweeling-/offline-hoofdmap uit de instantie halen (en de lege map zelf weg).
    Nooit iets met inhoud: dan 409."""
    if not body.confirm:
        raise HTTPException(400, "Bevestiging vereist (confirm: true)")
    found = next((t for t in await detect_twins(ctx) if t["instance_id"] == instance_id
                  and t["root_folder_id"] == root_folder_id), None)
    if found is None:
        raise HTTPException(404, "Geen tweeling- of offline-hoofdmap gevonden")
    if not found["removable"]:
        raise HTTPException(409, "Deze map is niet leeg (bestanden of titels): MediaGuard "
                                 "verwijdert alleen lege mappen")
    async with arr_client(ctx, instance_id) as client:
        await client.root_folder_delete(root_folder_id)
    removed_dir = False
    if not found["offline"]:
        try:
            await asyncio.to_thread(os.rmdir, found["path"])  # faalt als hij niet leeg is
            removed_dir = True
        except OSError:
            removed_dir = False
    audit(ctx.db, actor=principal.actor, action="storage.twin_removed",
          target_type="root_folder", target_id=str(root_folder_id), instance_id=instance_id,
          old_value={"path": found["path"]}, new_value={"dir_removed": removed_dir})
    return {"removed": True, "dir_removed": removed_dir, "path": found["path"]}


# -- uitvoering ----------------------------------------------------------------------------
def _items(client: ArrClient) -> Any:
    return client.series_list() if client.app == "sonarr" else client.movie_list()


async def _move_batch(client: ArrClient, ids: list[int], target: str) -> None:
    if client.app == "sonarr":
        await client.put("series/editor", {"seriesIds": ids, "rootFolderPath": target + "/",
                                           "moveFiles": True})
    else:
        await client.put("movie/editor", {"movieIds": ids, "rootFolderPath": target + "/",
                                          "moveFiles": True})


async def _wait_moves(client: ArrClient, timeout: float) -> None:
    loop = asyncio.get_running_loop()
    deadline = loop.time() + timeout
    while True:
        busy = [c for c in await client.commands()
                if "move" in str(c.get("name") or "").lower()
                and str(c.get("status")) in ("queued", "started")]
        if not busy:
            return
        if loop.time() >= deadline:
            raise TimeoutError("Verplaatsen duurt te lang")
        await asyncio.sleep(POLL_SECONDS)


def _under(path: Any, root: str) -> bool:
    p = _norm(path)
    return p == root or p.startswith(root + "/")




async def repoint_collections(client: ArrClient, source: str, target: str) -> int:
    """Radarr-collecties die nog naar de oude hoofdmap wijzen naar de nieuwe zetten
    (rapport 2 / N10: 'Missing root folder for movie collection: …/Anime Fims')."""
    if client.app != "radarr":
        return 0
    try:
        cols = await client.get("collection")
    except ArrError:
        return 0
    todo = [c for c in cols or [] if _norm(c.get("rootFolderPath")) == source and c.get("id")]
    if not todo:
        return 0
    # Radarr's bulk-editor (PUT /collection met collectionIds) is wat de UI zelf gebruikt;
    # een losse PUT met het hele object (incl. films) wordt door sommige versies geweigerd.
    try:
        await client.put("collection", {"collectionIds": [int(c["id"]) for c in todo],
                                        "rootFolderPath": target})
        return len(todo)
    except ArrError as exc:
        LOGGER.info("bulk-update collecties niet gelukt (%s); per collectie", exc)
    n = 0
    for col in todo:
        try:
            body = {k: v for k, v in col.items() if k != "movies"}
            await client.put(f"collection/{col['id']}", {**body, "rootFolderPath": target})
            n += 1
        except ArrError as exc:
            LOGGER.warning("collectie %s niet bijgewerkt: %s", col.get("title"), exc)
    return n


async def fix_collections(ctx: AppContext) -> int:
    """Bij opstarten/elk uur: collecties met een hoofdmap die niet meer bestaat naar de
    hoofdmap met dezelfde naam (hoofdletters/typfout gecorrigeerd) die wél bestaat."""
    from mediaguard import emergency
    from mediaguard.instances.registry import list_instances

    if emergency.skip(ctx, "Radarr-collecties bijwerken"):
        return 0
    total = 0
    for inst in list_instances(ctx):
        if getattr(inst, "app", None) != "radarr":
            continue
        try:
            async with arr_client(ctx, inst.id) as client:
                roots = [_norm(r.get("path")) for r in await client.root_folders()]
                cols = await client.get("collection") or []
                missing = {_norm(c.get("rootFolderPath")) for c in cols
                           if c.get("rootFolderPath") and _norm(c.get("rootFolderPath"))
                           not in roots}
                for old in missing:
                    fixed = suggestion(old)
                    match = next((r for r in roots if fixed and r.lower() == _norm(fixed).lower()
                                  or r.lower() == old.lower()), None)
                    if match:
                        total += await repoint_collections(client, old, match)
        except Exception as exc:  # noqa: BLE001 - per instantie
            LOGGER.info("collecties %s niet gecontroleerd: %s", inst.id, exc)
    return total


@jobs.job_handler("storage.typo_fix")
async def _job_typo_fix(jc: jobs.JobContext) -> dict[str, Any]:  # noqa: C901 - stapsgewijs
    ctx, p = jc.app, jc.payload
    iid, source, target = p["instance_id"], _norm(p["source"]), _norm(p["target"])
    reason = blocked_reason(ctx, iid)
    if reason:
        raise RuntimeError(reason)
    moved = 0
    async with arr_client(ctx, iid) as client:
        # 1. doelmap + hoofdmap in de instantie
        try:
            await asyncio.to_thread(os.makedirs, target, exist_ok=True)
            jc.log(f"Map {target} klaar")
        except OSError as exc:
            jc.log(f"Map {target} niet zelf aangemaakt ({exc}); Sonarr/Radarr probeert het",
                   "warning")
        roots = {_norm(r.get("path")): r for r in await client.root_folders()}
        if target not in roots:
            try:
                await client.root_folder_add(target + "/")
            except ArrError as exc:
                raise RuntimeError(f"Hoofdmap {target} toevoegen mislukt: {exc.message}. "
                                   "Er is niets verplaatst.") from exc
            jc.log(f"Hoofdmap {target} toegevoegd aan de instantie")
        # 2. in porties laten verplaatsen door Sonarr/Radarr zelf
        todo = [int(i["id"]) for i in await _items(client) if _under(i.get("path"), source)]
        total = len(todo)
        jc.log(f"{total} {'series' if client.app == 'sonarr' else 'films'} te verplaatsen")
        for start in range(0, total, BATCH):
            jc.check_cancelled()
            batch = todo[start:start + BATCH]
            try:
                await _move_batch(client, batch, target)
                await _wait_moves(client, MOVE_TIMEOUT_SECONDS)
            except (ArrError, TimeoutError) as exc:
                raise RuntimeError(f"Gestopt na {moved} van {total}: "
                                   f"{getattr(exc, 'message', exc)}. Wat al verplaatst is, "
                                   "staat goed; opnieuw starten gaat verder.") from exc
            now = {int(i["id"]): i for i in await _items(client)}
            stuck = [i for i in batch if i in now and not _under(now[i].get("path"), target)]
            if stuck:
                raise RuntimeError(f"Gestopt na {moved} van {total}: "
                                   f"{len(stuck)} item(s) staan nog niet in {target}.")
            moved += len(batch)
            jc.progress(moved / max(total, 1))
            jc.log(f"{moved}/{total} verplaatst")
        # 2b. Radarr-collecties mee naar de nieuwe map
        cols = await repoint_collections(client, source, target)
        if cols:
            jc.log(f"{cols} collectie(s) naar {target} gezet")
        # 3. oude hoofdmap alleen uit de instantie halen als hij echt leeg is
        removed = False
        left = [i for i in await _items(client) if _under(i.get("path"), source)]
        try:
            empty_on_disk = not any(Path(source).iterdir())
        except OSError:
            empty_on_disk = False
        old = next((r for r in await client.root_folders() if _norm(r.get("path")) == source),
                   None)
        if old is not None and not left and empty_on_disk:
            await client.root_folder_delete(int(old["id"]))
            removed = True
            jc.log(f"Lege hoofdmap {source} uit de instantie gehaald (map blijft bestaan)")
        elif old is not None:
            jc.log(f"Oude hoofdmap {source} blijft staan (niet leeg)", "warning")
    audit(ctx.db, actor="svc:storage", action="storage.typo_fixed", target_type="root_folder",
          target_id=str(p["root_folder_id"]), instance_id=iid, old_value={"path": source},
          new_value={"path": target, "moved": moved, "old_root_removed": removed})
    return {"moved": moved, "total": total, "old_root_removed": removed, "target": target}


async def _collections_loop(ctx: AppContext) -> None:
    await asyncio.sleep(90)
    while True:
        try:
            n = await fix_collections(ctx)
            if n:
                LOGGER.info("%s Radarr-collectie(s) naar de bestaande hoofdmap gezet", n)
            taskwatch.ok(ctx, "storage_collections")
        except Exception as exc:  # noqa: BLE001 - lus nooit laten sterven
            LOGGER.exception("collecties controleren mislukt")
            taskwatch.fail(ctx, "storage_collections", exc)
        await asyncio.sleep(3600)


async def startup(ctx: AppContext) -> None:
    if ctx.settings.start_background and ctx.settings.role in ("core", "all"):
        ctx.services["storage_collections_task"] = taskwatch.spawn(
            ctx, "storage_collections", _collections_loop,
            label_nl="Radarr-collecties controleren", every=3600)


async def shutdown(ctx: AppContext) -> None:
    task = ctx.services.pop("storage_collections_task", None)
    if task is not None:
        task.cancel()
        with contextlib.suppress(asyncio.CancelledError):
            await task
