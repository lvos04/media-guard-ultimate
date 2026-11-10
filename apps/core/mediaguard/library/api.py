"""Bibliotheek-API: overzicht, detail, seizoenmatrix, kalender, KPI's, synchronisatie."""

from __future__ import annotations

import asyncio
import contextlib
import logging
import time
from typing import Any, Literal

from fastapi import APIRouter, Depends, HTTPException, Query

from mediaguard import jobs, taskwatch
from mediaguard.arr.client import records_of
from mediaguard.auth import Principal, require_operator, require_viewer
from mediaguard.context import AppContext, get_app_context
from mediaguard.db import dumps, loads
from mediaguard.instances.deps import arr_call
from mediaguard.instances.registry import arr_client, list_instances
from mediaguard.library import queries
from mediaguard.library.sync import sync_instance
from mediaguard.settings_registry import REGISTRY, SettingDef
from mediaguard.textfmt import plain_nl

LOGGER = logging.getLogger(__name__)
router = APIRouter(prefix="/api/v1", tags=["library"])


def register_settings() -> None:
    REGISTRY.register(
        SettingDef("library.sync_interval_minutes", "int", 60, "Bibliotheek",
                   "Synchronisatie-interval bibliotheek", "Library sync interval",
                   help_nl="Hoe vaak series, afleveringen, films en bestanden uit elke "
                           "instantie worden ingelezen voor overzichten en ontbrekende media.",
                   minimum=5, maximum=1440, unit="min", scopes=("global", "instance")),
    )


@jobs.job_handler("library.sync")
async def _job_sync(jc: jobs.JobContext) -> dict[str, Any]:
    if not jc.instance_id:
        raise ValueError("library.sync vereist instance_id")
    full = jc.payload.get("full")
    return await sync_instance(jc.app, jc.instance_id,
                               full=None if full is None else bool(full))


def _offset(instance_id: str, interval_s: int) -> int:
    """Vaste verschuiving per instantie: niet alle vier tegelijk syncen (geheugen, slot)."""
    import zlib

    return zlib.crc32(instance_id.encode()) % max(1, interval_s)


def _due_library(ctx: AppContext) -> None:
    for inst in list_instances(ctx):
        interval = int(ctx.config.value("library.sync_interval_minutes",
                                        {"instance": inst.id}))
        bucket = int((time.time() - _offset(inst.id, interval * 60)) // (interval * 60))
        jobs.enqueue(ctx.db, "library.sync", {}, instance_id=inst.id, max_attempts=2,
                     idempotency_key=f"library.sync:{inst.id}:{interval}:{bucket}",
                     created_by="system:scheduler")


async def _scheduler(ctx: AppContext) -> None:
    # Sonarr/Radarr zijn pas ~15 s na core bereikbaar (alles-in-één): niet meteen syncen
    await asyncio.sleep(60)
    while True:
        try:
            _due_library(ctx)
            runner = ctx.services.get("jobs")
            if runner is not None:
                runner.wake()
            taskwatch.ok(ctx, "library_scheduler")
        except Exception as exc:  # noqa: BLE001 - planner mag nooit stoppen
            LOGGER.exception("library-planner")
            taskwatch.fail(ctx, "library_scheduler", exc)
        await asyncio.sleep(60)


async def startup(ctx: AppContext) -> None:
    if ctx.settings.start_background and ctx.settings.role in ("core", "all"):
        ctx.services["library.scheduler"] = taskwatch.spawn(
            ctx, "library_scheduler", _scheduler, label_nl="Bibliotheek bijwerken (planner)",
            every=60)


async def shutdown(ctx: AppContext) -> None:
    task = ctx.services.pop("library.scheduler", None)
    if task is not None:
        task.cancel()
        with contextlib.suppress(asyncio.CancelledError, Exception):
            await task


@router.get("/library/kpis")
def library_kpis(instance_id: str | None = None, _: Principal = Depends(require_viewer),
                 ctx: AppContext = Depends(get_app_context)) -> dict[str, Any]:
    from mediaguard.wanted.engine import wanted_kpis

    data = queries.kpis(ctx, instance_id)
    data["wanted"] = wanted_kpis(ctx, instance_id)
    return data


@router.get("/library/sync-status")
def sync_status(_: Principal = Depends(require_viewer),
                ctx: AppContext = Depends(get_app_context)) -> list[dict[str, Any]]:
    out = []
    for inst in list_instances(ctx):
        row = ctx.db.fetch_one("SELECT * FROM library_sync_runs WHERE instance_id=? "
                               "ORDER BY id DESC LIMIT 1", (inst.id,))
        run = dict(row) if row else None
        if run:
            run["counts"] = loads(run.pop("counts_json"), {})
        out.append({"instance_id": inst.id, "last_run": run})
    return out


@router.post("/library/sync")
async def sync_now(instance_id: str | None = None, mode: Literal["queue", "inline"] = "queue",
                   _: Principal = Depends(require_operator),
                   ctx: AppContext = Depends(get_app_context)) -> dict[str, Any]:
    targets = [i.id for i in list_instances(ctx) if instance_id in (None, i.id)]
    if instance_id and not targets:
        raise HTTPException(404, "Onbekende instantie")
    if mode == "inline":
        results = {}
        for iid in targets:
            try:
                results[iid] = await arr_call(sync_instance(ctx, iid, full=True))
            except HTTPException as exc:
                results[iid] = {"error": exc.detail}
        return {"results": results}
    ids = {iid: jobs.enqueue(ctx.db, "library.sync", {"full": True}, instance_id=iid,
                             max_attempts=2, created_by="ui") for iid in targets}
    runner = ctx.services.get("jobs")
    if runner is not None:
        runner.wake()
    return {"queued": ids}


@router.get("/library")
def library(instance_id: str | None = None, kind: Literal["series", "movie"] | None = None,
            media_type: str | None = None, q: str | None = None,
            monitored: bool | None = None,
            state: Literal["missing", "complete", "upcoming", "cutoff_unmet",
                           "unmonitored"] | None = None,
            page: int = Query(1, ge=1), page_size: int = Query(50, ge=1, le=500),
            sort: str = "title", descending: bool = False,
            _: Principal = Depends(require_viewer),
            ctx: AppContext = Depends(get_app_context)) -> dict[str, Any]:
    return queries.list_items(ctx, instance_id=instance_id, kind=kind, media_type=media_type,
                              q=q, monitored=monitored, state=state, page=page,
                              page_size=page_size, sort=sort, descending=descending)


@router.get("/library/{item_id}")
def library_item(item_id: int, _: Principal = Depends(require_viewer),
                 ctx: AppContext = Depends(get_app_context)) -> dict[str, Any]:
    item = queries.get_item(ctx, item_id)
    if item is None:
        raise HTTPException(404, "Item niet gevonden")
    return item


HISTORY_NL = {"grabbed": "Gegrepen", "downloadFolderImported": "Geïmporteerd",
              "episodeFileImported": "Geïmporteerd", "movieFileImported": "Geïmporteerd",
              "downloadFailed": "Download mislukt", "episodeFileDeleted": "Bestand verwijderd",
              "movieFileDeleted": "Bestand verwijderd", "episodeFileRenamed": "Hernoemd",
              "movieFileRenamed": "Hernoemd", "downloadIgnored": "Download genegeerd"}


def media_summary(mi: dict[str, Any] | None) -> dict[str, Any] | None:
    """Wat Sonarr/Radarr al weten over het bestand, zonder eigen controle (rapport H14)."""
    if not mi:
        return None
    dyn = str(mi.get("videoDynamicRangeType") or mi.get("videoDynamicRange") or "")
    return {"resolution": mi.get("resolution"), "video_codec": mi.get("videoCodec"),
            "video_bit_depth": mi.get("videoBitDepth"), "fps": mi.get("videoFps"),
            "dynamic_range": dyn or "SDR", "hdr": bool(dyn and dyn.upper() != "SDR"),
            "dolby_vision": "DV" in dyn.upper() or "DOVI" in dyn.upper(),
            "audio_codec": mi.get("audioCodec"), "audio_channels": mi.get("audioChannels"),
            "audio_languages": [x.strip() for x in str(mi.get("audioLanguages") or "")
                                .split("/") if x.strip()],
            "subtitle_languages": [x.strip() for x in str(mi.get("subtitles") or "")
                                   .split("/") if x.strip()],
            "runtime": mi.get("runTime"), "video_bitrate": mi.get("videoBitrate")}


def current_av1_mediainfo(history: list[dict[str, Any]]) -> dict[str, Any] | None:
    """Mediainfo van de AV1-versie die nu in de bibliotheek staat (laatste geplaatste job)."""
    for h in history:
        if h.get("status") == "committed" and not h.get("rolled_back"):
            return h.get("output_mediainfo")
    return None


async def measured_av1(ctx: AppContext, history: list[dict[str, Any]],
                       path: str | None) -> dict[str, Any] | None:
    """Mediainfo van de AV1-versie: uit MediaGuard's eigen meting, of — voor bestanden die
    vóór die meting zijn geplaatst — nu alsnog gemeten en bewaard (rapport 5, T6: een film
    toonde nog de HEVC-gegevens uit Radarr)."""
    from mediaguard.encoding.client import EncoderError, get_encoder_client
    from mediaguard.encoding.service import mediainfo_summary

    current = current_av1_mediainfo(history)
    if current or not path:
        return current
    job = next((h for h in history if h.get("status") == "committed"
                and not h.get("rolled_back")), None)
    if job is None:
        return None
    try:
        async with asyncio.timeout(8):
            probe = await get_encoder_client(ctx).probe(path)
    except (EncoderError, TimeoutError, Exception):  # noqa: BLE001 - meting is extra
        return None
    if str(probe.get("codec") or "").lower() != "av1":
        return None  # het bestand is inmiddels vervangen door iets anders
    info = mediainfo_summary(probe)
    if info:
        row = ctx.db.fetch_one("SELECT core_verification_json FROM encoding_jobs WHERE id=?",
                               (job["id"],))
        cv = loads(row["core_verification_json"] if row else None, {}) or {}
        cv["output_mediainfo"] = {**info, "measured_later": True}
        ctx.db.execute("UPDATE encoding_jobs SET core_verification_json=? WHERE id=?",
                       (dumps(cv), job["id"]))
    return info


def _av1_history(ctx: AppContext, instance_id: str, kind: str, file_id: Any,
                 path: str | None) -> list[dict[str, Any]]:
    rows = ctx.db.fetch_dicts(
        "SELECT id, status, error, skip_reason, source_path, created_at, updated_at, "
        "source_snapshot_json, staged_output_json, core_verification_json, encoder_job_id "
        "FROM encoding_jobs WHERE (instance_id=? AND "
        "media_kind=? AND provider_id=?) OR source_path=? ORDER BY id DESC LIMIT 20",
        (instance_id, kind, str(file_id or ""), path or "\x00"))
    out = []
    for r in rows:
        src = (loads(r.pop("source_snapshot_json"), {}) or {}).get("size")
        dst = (loads(r.pop("staged_output_json"), {}) or {}).get("size")
        cv = loads(r.pop("core_verification_json"), {}) or {}
        rolled_back = "origineel teruggezet" in str(r.get("error") or "")
        status_nl = ("Teruggezet (origineel staat terug)" if rolled_back
                     else ENCODE_STATUS_NL.get(str(r["status"]), str(r["status"])))
        out.append({**r, "error": plain_nl(r.get("error")) or None,
                    "skip_reason": plain_nl(r.get("skip_reason")) or None,
                    "source_size": src, "output_size": dst, "status_nl": status_nl,
                    "rolled_back": rolled_back,
                    "output_mediainfo": cv.get("output_mediainfo")})
    return out


ENCODE_STATUS_NL = {
    "committed": "Geplaatst (AV1-versie in de bibliotheek)", "handed_off": "Wordt geplaatst",
    "awaiting_activation": "Klaar, nog niet geplaatst", "awaiting_commit": "Wordt gecontroleerd",
    "rejected": "Afgekeurd, origineel behouden", "failed": "Mislukt", "skipped": "Overgeslagen",
    "cancelled": "Gestopt", "queued": "In de wachtrij", "running": "Bezig",
    "verifying": "Wordt gecontroleerd", "submitted": "Aangemeld",
    "conflict_review": "Conflict: bestand veranderde tijdens het encoderen",
}


async def _arr_history(ctx: AppContext, instance_id: str, *, episode_id: int | None = None,
                       movie_id: int | None = None) -> tuple[list[dict[str, Any]], str | None]:
    try:
        async with asyncio.timeout(6):
            client = arr_client(ctx, instance_id)
            rows = (await client.history_for_episode(episode_id) if episode_id is not None
                    else await client.history_for_movie(int(movie_id or 0)))
    except Exception as exc:  # noqa: BLE001 - historie is extra; pagina blijft werken
        return [], f"Historie niet op te halen: {type(exc).__name__}"
    out = []
    for h in rows[:30]:
        event = str(h.get("eventType") or "")
        data = h.get("data") or {}
        out.append({"at": h.get("date"), "event": event,
                    "event_nl": HISTORY_NL.get(event, event),
                    "title": h.get("sourceTitle"),
                    "quality": ((h.get("quality") or {}).get("quality") or {}).get("name"),
                    "indexer": data.get("indexer"), "download_client":
                    data.get("downloadClientName") or data.get("downloadClient"),
                    "reason": data.get("reason") or data.get("message")})
    return out, None


def _identity(ctx: AppContext, instance_id: str, kind: str, provider_id: Any
              ) -> dict[str, Any]:
    from mediaguard.identity import service as identity

    hist = identity.history(ctx, instance_id, kind, provider_id, limit=1)
    if not hist:
        return {"status": "pending", "status_nl": "Nog niet gecontroleerd",
                "reason_nl": "Wordt automatisch gecontroleerd op de "
                "achtergrond (binnen een half uur)."}
    h = hist[0]
    from mediaguard.identity.engine import STATUS_LABELS_NL

    return {"status": h["status"], "status_nl": STATUS_LABELS_NL.get(str(h["status"]),
                                                                    str(h["status"])),
            "reason_nl": h.get("reason_nl"),
            "evidence_count": len(h.get("evidence") or []), "checked_at": h.get("created_at"),
            "source": h.get("source")}


@router.get("/library/{item_id}/episodes/{episode_id}/details")
async def episode_details(item_id: int, episode_id: int, _: Principal = Depends(require_viewer),
                          ctx: AppContext = Depends(get_app_context)) -> dict[str, Any]:
    """Alles over één aflevering in één keer: mediainfo uit Sonarr, historie (grab/import),
    identiteit en AV1-geschiedenis van het bestand."""
    item = ctx.db.fetch_one("SELECT instance_id, provider_id FROM lib_items WHERE id=? AND "
                            "kind='series'", (item_id,))
    if item is None:
        raise HTTPException(404, "Serie niet gevonden")
    iid = item["instance_id"]
    ep = ctx.db.fetch_one("SELECT * FROM lib_episodes WHERE instance_id=? AND provider_id=?",
                          (iid, episode_id))
    if ep is None:
        raise HTTPException(404, "Aflevering niet gevonden")
    file = None
    if ep["episode_file_id"]:
        file = queries._file_dict(ctx.db.fetch_one(
            "SELECT * FROM lib_files WHERE instance_id=? AND kind='episode' AND provider_id=?",
            (iid, ep["episode_file_id"])))
    history, history_error = await _arr_history(ctx, iid, episode_id=episode_id)
    wanted = ctx.db.fetch_dicts(
        "SELECT id,reason,status,attempts,last_searched_at,next_search_at,last_reason_code "
        "FROM wanted_items WHERE instance_id=? AND kind='episode' AND provider_id=?",
        (iid, episode_id))
    return {"episode": dict(ep), "file": file,
            "media": media_summary((file or {}).get("media_info")),
            "identity": _identity(ctx, iid, "episode", episode_id),
            "history": history, "history_error": history_error, "wanted": wanted,
            "av1": (av1 := _av1_history(ctx, iid, "episode_file", ep["episode_file_id"],
                                        (file or {}).get("path"))),
            "media_mediaguard": await measured_av1(ctx, av1, (file or {}).get("path"))}


@router.get("/library/{item_id}/details")
async def movie_details(item_id: int, _: Principal = Depends(require_viewer),
                        ctx: AppContext = Depends(get_app_context)) -> dict[str, Any]:
    """Film: mediainfo uit Radarr, historie, identiteit en AV1-geschiedenis (rapport H14)."""
    item = queries.get_item(ctx, item_id)
    if item is None or item["kind"] != "movie":
        raise HTTPException(404, "Film niet gevonden")
    file = item.get("file")
    history, history_error = await _arr_history(ctx, item["instance_id"],
                                                movie_id=int(item["provider_id"]))
    return {"movie": item, "file": file, "media": media_summary((file or {}).get("media_info")),
            "identity": (_identity(ctx, item["instance_id"], "movie_file", item["file_id"])
                         if item.get("file_id") else
                         _identity(ctx, item["instance_id"], "movie", item["provider_id"])),
            "history": history, "history_error": history_error,
            "av1": (av1 := _av1_history(ctx, item["instance_id"], "movie_file",
                                        item.get("file_id"), (file or {}).get("path"))),
            "media_mediaguard": await measured_av1(ctx, av1, (file or {}).get("path")),
            # Radarr's mediainfo loopt achter na een vervanging: dan is MediaGuard's meting
            # leidend (de UI toont 'Gemeten door MediaGuard')
            "media_stale": bool(current_av1_mediainfo(av1) or any(
                h.get("status") == "committed" and not h.get("rolled_back") for h in av1))
            and str(((file or {}).get("media_info") or {}).get("videoCodec") or "").lower()
            not in ("av1", "")}


@router.get("/library/{item_id}/seasons")
async def library_seasons(item_id: int, live_queue: bool = False,
                          _: Principal = Depends(require_viewer),
                          ctx: AppContext = Depends(get_app_context)) -> dict[str, Any]:
    rows = ctx.db.fetch_dicts("SELECT * FROM lib_items WHERE id=?", (item_id,))
    if not rows or rows[0]["kind"] != "series":
        raise HTTPException(404, "Serie niet gevonden")
    item = rows[0]
    queue_ids: set[int] = set()
    if live_queue:
        data = await arr_call(arr_client(ctx, item["instance_id"]).queue(all_pages=True))
        queue_ids = {int(q.get("episodeId") or 0) for q in records_of(data)
                     if int(q.get("seriesId") or 0) == int(item["provider_id"])}
    return queries.season_matrix(ctx, item, queue_ids)


@router.get("/calendar")
def calendar(start: str, end: str, instance_id: str | None = None,
             include_unmonitored: bool = False, _: Principal = Depends(require_viewer),
             ctx: AppContext = Depends(get_app_context)) -> dict[str, Any]:
    try:
        items = queries.calendar(ctx, start, end, instance_id=instance_id,
                                 include_unmonitored=include_unmonitored)
    except ValueError as exc:
        raise HTTPException(422, str(exc)) from exc
    return {"start": start, "end": end, "total": len(items), "items": items}


# -- rapport 2 / N28: waarom veranderden de aantallen? ----------------------------------------
def record_count_snapshot(ctx: AppContext) -> dict[str, Any]:
    """Eén momentopname per dag (laatste van de dag telt) per instantie."""
    from datetime import UTC, datetime

    day = datetime.now(UTC).date().isoformat()
    snap: dict[str, Any] = {}
    for inst in list_instances(ctx):
        k = queries.kpis(ctx, inst.id)
        snap[inst.id] = {key: k.get(key) for key in ("available", "monitored", "total",
                                                       "items", "missing")
                         if key in k}
    ctx.state_set(f"library_counts:{day}", snap)
    ctx.state_set(f"library_space:{day}", free_space_snapshot(ctx))
    return snap


def free_space_snapshot(ctx: AppContext) -> dict[str, Any]:
    """Vrije ruimte per schijf (één keer per apparaat) onder de bibliotheekmappen (R19)."""
    import os

    roots = [r[0] for r in ctx.db.fetch_all(
        "SELECT DISTINCT RTRIM(root_folder_path,'/') FROM lib_items WHERE removed_at IS NULL "
        "AND root_folder_path IS NOT NULL")]
    seen: dict[int, dict[str, Any]] = {}
    for root in roots:
        try:
            st = os.stat(root)
            vfs = os.statvfs(root)
        except OSError:
            continue
        if st.st_dev in seen:
            continue
        seen[st.st_dev] = {"path": root, "free": vfs.f_bavail * vfs.f_frsize,
                           "total": vfs.f_blocks * vfs.f_frsize}
    return {"disks": list(seen.values()),
            "free": sum(d["free"] for d in seen.values())}


def _space_reasons(ctx: AppContext, day: str, nxt: str, delta_free: int) -> list[str]:
    """Wat MediaGuard kan aanwijzen bij een verandering van de vrije ruimte (R19)."""
    from mediaguard.db import loads

    def gb(n: float) -> str:
        return f"{n / 1024 ** 3:.1f} GB".replace(".", ",")

    out = [f"Vrije ruimte {'+' if delta_free >= 0 else '−'}{gb(abs(delta_free))}"]
    rows = ctx.db.fetch_dicts(
        "SELECT source_snapshot_json, staged_output_json FROM encoding_jobs WHERE "
        "status='committed' AND updated_at>=? AND updated_at<?", (day, nxt))
    saved = sum(max(0, int((loads(r["source_snapshot_json"], {}) or {}).get("size") or 0)
                    - int((loads(r["staged_output_json"], {}) or {}).get("size") or 0))
                for r in rows)
    if saved:
        out.append(f"AV1 bespaarde {gb(saved)} ({len(rows)} bestand(en))")
    grew = ctx.db.fetch_one(
        "SELECT COUNT(*) AS n, SUM(is_upgrade) AS up FROM import_events WHERE received_at>=? "
        "AND received_at<?", (day, nxt))
    if grew and int(grew["n"] or 0):
        up = int(grew["up"] or 0)
        out.append(f"{int(grew['n'])} download(s) geïmporteerd"
                   + (f", waarvan {up} vervanging(en) door een betere versie" if up else ""))
    held = ctx.db.fetch_one(
        "SELECT COALESCE(SUM(size),0) AS b FROM quarantine_items WHERE created_at>=? AND "
        "created_at<? AND status IN ('restorable','held','pending')", (day, nxt))
    if held and int(held["b"]):
        out.append(f"{gb(int(held['b']))} originelen bewaard in quarantaine")
    return out


@router.get("/library-count-changes")
def count_changes(days: int = Query(14, ge=2, le=90), _: Principal = Depends(require_viewer),
                  ctx: AppContext = Depends(get_app_context)) -> dict[str, Any]:
    """Per dag de aantallen en het verschil met de dag ervoor, met de oorzaken die MediaGuard
    kan aanwijzen: titels die in Sonarr/Radarr zijn verwijderd en AV1-vervangingen."""
    from datetime import UTC, datetime, timedelta

    today = datetime.now(UTC).date()
    out = []
    prev: dict[str, Any] | None = None
    for i in range(days - 1, -1, -1):
        day = (today - timedelta(days=i)).isoformat()
        snap = ctx.state_get(f"library_counts:{day}")
        if snap is None:
            continue
        nxt = (today - timedelta(days=i - 1)).isoformat()
        removed = ctx.db.fetch_dicts(
            "SELECT instance_id, kind, title FROM lib_items WHERE removed_at>=? AND removed_at<? "
            "ORDER BY title LIMIT 50", (day, nxt))
        av1 = ctx.db.fetch_one(
            "SELECT COUNT(*) AS n FROM encoding_jobs WHERE status='committed' AND updated_at>=? "
            "AND updated_at<?", (day, nxt))
        delta = None
        if prev is not None:
            delta = {iid: {k: (v or 0) - ((prev.get(iid) or {}).get(k) or 0)
                           for k, v in vals.items() if isinstance(v, int)}
                     for iid, vals in snap.items()}
        reasons = []
        if removed:
            reasons.append(f"{len(removed)} titel(s) verwijderd in Sonarr/Radarr")
        if av1 and int(av1["n"]):
            reasons.append(f"{int(av1['n'])} AV1-vervanging(en) (bestand vervangen; telt niet "
                           "als verdwenen)")
        from mediaguard.downloads.janitor import freed_on

        cleaned = freed_on(ctx, day, nxt)
        if cleaned["items"]:
            reasons.append(f"{cleaned['items']} oude download(s) opgeruimd: "
                           f"{cleaned['bytes'] / 1024 ** 3:.1f} GB vrij".replace(".", ","))
        space = ctx.state_get(f"library_space:{day}") or {}
        prev_space = ctx.state_get(
            f"library_space:{(today - timedelta(days=i + 1)).isoformat()}") or {}
        free_delta = (int(space["free"]) - int(prev_space["free"])
                      if space.get("free") is not None and prev_space.get("free") is not None
                      else None)
        space_nl = (_space_reasons(ctx, day, nxt, free_delta)
                    if free_delta and abs(free_delta) >= 1024 ** 3 else [])
        out.append({"day": day, "counts": snap, "delta": delta, "removed": removed,
                    "free_bytes": space.get("free"), "free_delta": free_delta,
                    "space_reasons_nl": space_nl,
                    "reasons_nl": reasons or (["geen aanwijsbare oorzaak in MediaGuard; "
                                               "mogelijk niet meer gevolgd in Sonarr/Radarr"]
                                              if delta and any(v for d in delta.values()
                                                               for v in d.values()) else [])})
        prev = snap
    return {"days": out}
