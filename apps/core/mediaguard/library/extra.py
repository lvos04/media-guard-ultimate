"""Bibliotheek-acties en -inzicht voor web en app (docs/APP_API.md §1–2):
seizoenen/afleveringen aan- en uitzetten, kwaliteit + custom formats per bestand, en waarom
er geüpgraded werd."""

from __future__ import annotations

import logging
from typing import Any

from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel

from mediaguard import jobs, upgrade_reasons
from mediaguard.audit import audit
from mediaguard.auth import Principal, require_operator, require_viewer
from mediaguard.context import AppContext, get_app_context
from mediaguard.instances.deps import arr_call
from mediaguard.instances.registry import arr_client
from mediaguard.library import queries
from mediaguard.library.api import media_summary

LOGGER = logging.getLogger(__name__)
router = APIRouter(prefix="/api/v1", tags=["library"])


class MonitorBody(BaseModel):
    monitored: bool
    search: bool = False


def _series(ctx: AppContext, item_id: int) -> dict[str, Any]:
    item = queries.get_item(ctx, item_id)
    if item is None or item["kind"] != "series" or item.get("app") != "sonarr":
        raise HTTPException(404, "Serie niet gevonden")
    return item


def _refresh_library(ctx: AppContext, instance_id: str) -> None:
    jobs.enqueue(ctx.db, "library.sync", {"full": False}, instance_id=instance_id,
                 max_attempts=2)


@router.put("/library/{item_id}/seasons/{season_number}")
async def season_monitor(item_id: int, season_number: int, body: MonitorBody,
                         principal: Principal = Depends(require_operator),
                         ctx: AppContext = Depends(get_app_context)) -> dict[str, Any]:
    item = _series(ctx, item_id)
    iid, sid = item["instance_id"], int(item["provider_id"])
    client = arr_client(ctx, iid)
    series = await arr_call(client.series_get(sid))
    if not any(int(s.get("seasonNumber", -1)) == season_number
               for s in series.get("seasons") or []):
        raise HTTPException(404, f"Seizoen {season_number} bestaat niet")
    # Sonarr zet afleveringen alleen mee als de seizoensvlag wijzigt; daarom daarna ook de
    # afleveringen expliciet (anders blijft een eerder uitgezette aflevering uit)
    await arr_call(client.season_pass(sid, {season_number: body.monitored}))
    episodes = await arr_call(client.episodes(sid, season_number))
    changed = [int(e["id"]) for e in episodes if bool(e.get("monitored")) != body.monitored]
    if changed:
        await arr_call(client.episodes_monitor(changed, body.monitored))
    searched = False
    if body.search and body.monitored:
        await arr_call(client.season_search(sid, season_number))
        searched = True
    audit(ctx.db, actor=principal.actor, action="library.season_monitor",
          target_type="series", target_id=str(sid), instance_id=iid,
          new_value={"season": season_number, "monitored": body.monitored,
                     "search": searched, "episodes_changed": len(changed)})
    _refresh_library(ctx, iid)
    msg = (f"Seizoen {season_number} wordt gevolgd" if body.monitored
           else f"Seizoen {season_number} wordt niet meer gevolgd")
    return {"item_id": item_id, "season_number": season_number, "monitored": body.monitored,
            "episodes_changed": len(changed), "search_started": searched,
            "message_nl": msg + ("; zoeken gestart" if searched else "")}


@router.put("/library/{item_id}/episodes/{episode_id}/monitor")
async def episode_monitor(item_id: int, episode_id: int, body: MonitorBody,
                          principal: Principal = Depends(require_operator),
                          ctx: AppContext = Depends(get_app_context)) -> dict[str, Any]:
    item = _series(ctx, item_id)
    iid, sid = item["instance_id"], int(item["provider_id"])
    client = arr_client(ctx, iid)
    episode = await arr_call(client.episode_get(episode_id))
    if int(episode.get("seriesId") or 0) != sid:
        raise HTTPException(404, "Aflevering hoort niet bij deze serie")
    await arr_call(client.episodes_monitor([episode_id], body.monitored))
    searched = False
    if body.search and body.monitored:
        await arr_call(client.episode_search([episode_id]))
        searched = True
    label = (f"S{int(episode.get('seasonNumber') or 0):02d}"
             f"E{int(episode.get('episodeNumber') or 0):02d}")
    audit(ctx.db, actor=principal.actor, action="library.episode_monitor",
          target_type="episode", target_id=str(episode_id), instance_id=iid,
          new_value={"monitored": body.monitored, "search": searched, "label": label})
    _refresh_library(ctx, iid)
    return {"item_id": item_id, "episode_id": episode_id, "monitored": body.monitored,
            "episodes_changed": 1, "search_started": searched,
            "message_nl": f"{label} wordt {'gevolgd' if body.monitored else 'niet meer gevolgd'}"
            + ("; zoeken gestart" if searched else "")}


# -- kwaliteit en custom formats per bestand ----------------------------------------------------
def _profile(profiles: list[dict[str, Any]], profile_id: Any) -> dict[str, Any] | None:
    for p in profiles or []:
        if int(p.get("id") or -1) == int(profile_id or -2):
            return p
    return None


def _cutoff_name(profile: dict[str, Any]) -> str | None:
    cutoff = profile.get("cutoff")
    for q in profile.get("items") or []:
        if int(q.get("id") or -1) == int(cutoff or -2):
            return str(q.get("name") or "")
        qq = q.get("quality") or {}
        if int(qq.get("id") or -1) == int(cutoff or -2):
            return str(qq.get("name") or "")
    return None


def _av1_paths(ctx: AppContext, instance_id: str) -> set[str]:
    return {str(r["source_path"]) for r in ctx.db.fetch_all(
        "SELECT source_path FROM encoding_jobs WHERE instance_id=? AND status='committed'",
        (instance_id,)) if r["source_path"]}


def _file_view(f: dict[str, Any], profile: dict[str, Any] | None, av1: set[str],
               label: str | None = None) -> dict[str, Any]:
    q = (f.get("quality") or {}).get("quality") or {}
    cfs = [{"name": c.get("name"), "score": _cf_score(profile, c)}
           for c in f.get("customFormats") or [] if isinstance(c, dict)]
    score = f.get("customFormatScore")
    if score is None:
        score = sum(int(c["score"] or 0) for c in cfs)
    cutoff_score = int((profile or {}).get("cutoffFormatScore") or 0)
    quality_ok = not bool(f.get("qualityCutoffNotMet"))
    score_ok = cutoff_score <= 0 or int(score or 0) >= cutoff_score
    media = media_summary(f.get("mediaInfo")) or {}
    av1_mg = str(f.get("path") or "") in av1
    if av1_mg:
        media = {**media, "video_codec": "AV1", "measured_by": "mediaguard"}
    else:
        media = {**media, "measured_by": "arr"} if media else {}
    bits = [str(q.get("name") or "?")]
    if media.get("video_codec"):
        bits.append(str(media["video_codec"]))
    if media.get("hdr"):
        bits.append(str(media.get("dynamic_range")))
    if cfs:
        bits.append(", ".join(str(c["name"]) for c in cfs))
    summary = " · ".join(bits) + (f" — score {score} van {cutoff_score}" if cutoff_score
                                  else f" — score {score}")
    return {"file_id": f.get("id"), "label": label, "path": f.get("path"),
            "size": f.get("size"), "release_group": f.get("releaseGroup"),
            "quality": {"name": q.get("name"), "resolution": q.get("resolution"),
                        "source": q.get("source")},
            "media": media, "custom_formats": cfs, "score": score,
            "cutoff_met": quality_ok and score_ok, "quality_cutoff_met": quality_ok,
            "score_cutoff_met": score_ok, "av1_by_mediaguard": av1_mg,
            "summary_nl": summary}


def _cf_score(profile: dict[str, Any] | None, cf: dict[str, Any]) -> int | None:
    for item in (profile or {}).get("formatItems") or []:
        if int(item.get("format") or -1) == int(cf.get("id") or -2) \
                or item.get("name") == cf.get("name"):
            return int(item.get("score") or 0)
    return None


@router.get("/library/{item_id}/files")
async def item_files(item_id: int, _: Principal = Depends(require_viewer),
                     ctx: AppContext = Depends(get_app_context)) -> dict[str, Any]:
    item = queries.get_item(ctx, item_id)
    if item is None:
        raise HTTPException(404, "Item niet gevonden")
    iid, pid = item["instance_id"], int(item["provider_id"])
    client = arr_client(ctx, iid)
    profiles = await arr_call(client.quality_profiles())
    profile = _profile(profiles, item.get("quality_profile_id"))
    av1 = _av1_paths(ctx, iid)
    files: list[dict[str, Any]] = []
    if item["kind"] == "series":
        eps = await arr_call(client.episodes(pid))
        by_file: dict[int, list[dict[str, Any]]] = {}
        for e in eps:
            if e.get("episodeFileId"):
                by_file.setdefault(int(e["episodeFileId"]), []).append(e)
        for f in await arr_call(client.episode_files(pid)):
            linked = sorted(by_file.get(int(f.get("id") or 0), []),
                            key=lambda e: (e.get("seasonNumber"), e.get("episodeNumber")))
            label = "+".join(f"S{int(e.get('seasonNumber') or 0):02d}"
                             f"E{int(e.get('episodeNumber') or 0):02d}" for e in linked) or None
            view = _file_view(f, profile, av1, label)
            if linked:
                view.update(episode_id=linked[0].get("id"),
                            season_number=linked[0].get("seasonNumber"),
                            episode_number=linked[0].get("episodeNumber"))
            files.append(view)
        files.sort(key=lambda v: (v.get("season_number") or 0, v.get("episode_number") or 0))
    else:
        for f in await arr_call(client.movie_files(pid)):
            files.append(_file_view(f, profile, av1))
    return {"item_id": item_id, "kind": item["kind"],
            "profile": ({"name": profile.get("name"), "cutoff": _cutoff_name(profile),
                         "cutoff_score": profile.get("cutoffFormatScore"),
                         "min_upgrade_score": profile.get("minUpgradeFormatScore"),
                         "upgrade_allowed": profile.get("upgradeAllowed")}
                        if profile else None),
            "files": files}


@router.get("/library/{item_id}/upgrades")
def item_upgrades(item_id: int, days: int = Query(90, ge=1, le=365),
                  _: Principal = Depends(require_viewer),
                  ctx: AppContext = Depends(get_app_context)) -> dict[str, Any]:
    item = queries.get_item(ctx, item_id)
    if item is None:
        raise HTTPException(404, "Item niet gevonden")
    return {"item_id": item_id, "days": days,
            "upgrades": upgrade_reasons.item_upgrades(ctx, item, days)}
