"""Beheer-API zodat de Nederlandse UI nooit de Sonarr/Radarr-UI nodig heeft (Q2.1):
series/films toevoegen en bewerken, seizoen-/episodemonitoring, indexers,
downloadclients, rootmappen, profielen, tags, remote path mappings, zoekacties,
wachtrij en historie. Alle routes: ``/api/v1/instances/{instance_id}/...``.

Lezen = viewer, muteren = operator; iedere mutatie wordt geaudit met ``instance_id``.
Bestanden worden via deze API NOOIT verwijderd (``deleteFiles`` staat altijd uit).
Geheimen van indexers/downloadclients worden gemaskeerd teruggegeven."""

from __future__ import annotations

import contextlib
from typing import Any, Literal

from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel, Field

from mediaguard.arr.client import ArrError
from mediaguard.audit import audit
from mediaguard.auth import Principal, require_operator, require_viewer
from mediaguard.context import AppContext, get_app_context
from mediaguard.db import dumps, loads, utcnow
from mediaguard.instances.deps import arr_call, mask_provider, require_instance, unmask_provider
from mediaguard.instances.registry import arr_client

router = APIRouter(prefix="/instances/{instance_id}", tags=["instance-management"])


def _audit(ctx: AppContext, principal: Principal, action: str, instance_id: str,
           target_type: str, target_id: Any = None, old: Any = None, new: Any = None,
           reason: str | None = None) -> None:
    audit(ctx.db, actor=principal.actor, action=action, target_type=target_type,
          target_id=target_id, instance_id=instance_id, old_value=old, new_value=new,
          reason=reason)


# ==== Series (Sonarr) ======================================================================
@router.get("/series")
async def series_list(instance_id: str, _: Principal = Depends(require_viewer),
                      ctx: AppContext = Depends(get_app_context)) -> list[dict[str, Any]]:
    require_instance(ctx, instance_id, app="sonarr")
    return await arr_call(arr_client(ctx, instance_id).series_list())


@router.get("/series/lookup")
async def series_lookup(instance_id: str, term: str = Query(min_length=1, max_length=200),
                        _: Principal = Depends(require_viewer),
                        ctx: AppContext = Depends(get_app_context)) -> list[dict[str, Any]]:
    require_instance(ctx, instance_id, app="sonarr")
    return await arr_call(arr_client(ctx, instance_id).series_lookup(term))


MonitorOption = Literal["all", "future", "missing", "existing", "recent", "pilot",
                        "firstSeason", "lastSeason", "monitorSpecials", "unmonitorSpecials",
                        "none"]


class AddSeries(BaseModel):
    tvdb_id: int
    quality_profile_id: int | None = None
    language_profile_id: int | None = None
    root_folder_path: str | None = None
    monitor: MonitorOption = "all"
    monitor_new_items: Literal["all", "none"] = "all"
    monitored: bool = True
    season_folder: bool = True
    series_type: Literal["standard", "anime", "daily"] | None = None
    tags: list[int] = Field(default_factory=list)
    search_for_missing: bool = False
    search_for_cutoff_unmet: bool = False
    force_duplicate: bool = False


def _ownership_guard(ctx: AppContext, instance_id: str, kind: str, canonical: dict[str, Any],
                     force: bool) -> dict[str, Any] | None:
    from mediaguard.instances.service import simulate_ownership

    try:
        sim = simulate_ownership(ctx, {"kind": kind, **canonical})
    except ValueError:
        return None
    others = [e for e in sim["existing"] if e["instance_id"] != instance_id]
    allowed = any(r["allow_multi"] for r in sim["ownership"])
    where = {o["instance_id"] for o in others}
    if others and not allowed and not force:
        raise HTTPException(409, {
            "code": "OWNERSHIP_CONFLICT",
            "message": f"Deze titel bestaat al in {', '.join(sorted(where))}. "
                       "Toevoegen geeft een dubbele taak. Leg een ownership-override vast of "
                       "bevestig met force_duplicate.",
            "existing": others})
    return sim


def _keep_here(ctx: AppContext, instance_id: str, kind: str, external_id: int,
               actor: str) -> None:
    """Bewust dubbel (force_duplicate, R6.2): ook de automatische anime-routering mag deze
    titel niet uit deze instantie weghalen. Vóór het toevoegen vastleggen, want de
    SeriesAdd/MovieAdded-webhook komt binnen een seconde."""
    try:
        from mediaguard.routing.service import exclude
    except ImportError:  # module niet aanwezig
        return
    exclude(ctx, instance_id, kind, external_id, actor)


@router.post("/series", status_code=201)
async def series_add(instance_id: str, body: AddSeries,
                     principal: Principal = Depends(require_operator),
                     ctx: AppContext = Depends(get_app_context)) -> dict[str, Any]:
    inst = require_instance(ctx, instance_id, app="sonarr", mutable=True)
    _ownership_guard(ctx, instance_id, "series", {"tvdb_id": body.tvdb_id},
                     body.force_duplicate)
    if body.force_duplicate:
        _keep_here(ctx, instance_id, "series", body.tvdb_id, principal.actor)
    client = arr_client(ctx, instance_id)
    found = await arr_call(client.series_lookup(f"tvdb:{body.tvdb_id}"))
    if not found:
        raise HTTPException(404, f"Geen serie gevonden met TVDB-ID {body.tvdb_id}")
    series = dict(found[0])
    if series.get("id"):
        raise HTTPException(409, f"Serie staat al in {instance_id} (id {series['id']})")
    quality = body.quality_profile_id or inst["default_quality_profile_id"]
    root = body.root_folder_path or inst["default_root_folder"]
    if not quality or not root:
        raise HTTPException(422, "Kies een kwaliteitsprofiel en rootmap (of stel standaarden "
                                 "in bij de instantie)")
    language = body.language_profile_id or inst["default_language_profile_id"]
    if not language:
        profiles = await arr_call(client.language_profiles())
        language = profiles[0]["id"] if profiles else None
    series.update({
        "qualityProfileId": int(quality), "rootFolderPath": root, "monitored": body.monitored,
        "seasonFolder": body.season_folder, "monitorNewItems": body.monitor_new_items,
        "seriesType": body.series_type or inst["default_series_type"] or
        series.get("seriesType") or "standard",
        "tags": body.tags,
        "addOptions": {"monitor": body.monitor,
                       "searchForMissingEpisodes": body.search_for_missing,
                       "searchForCutoffUnmetEpisodes": body.search_for_cutoff_unmet},
    })
    if language:
        series["languageProfileId"] = int(language)
    created = await arr_call(client.series_add(series))
    _audit(ctx, principal, "series.add", instance_id, "series", created.get("id"),
           new={"tvdb_id": body.tvdb_id, "title": created.get("title"),
                "monitor": body.monitor, "monitor_new_items": body.monitor_new_items,
                "quality_profile_id": quality, "root_folder_path": root})
    return created


@router.get("/series/{series_id}")
async def series_get(instance_id: str, series_id: int, _: Principal = Depends(require_viewer),
                     ctx: AppContext = Depends(get_app_context)) -> dict[str, Any]:
    require_instance(ctx, instance_id, app="sonarr")
    return await arr_call(arr_client(ctx, instance_id).series_get(series_id))


class UpdateSeries(BaseModel):
    monitored: bool | None = None
    monitor_new_items: Literal["all", "none"] | None = None
    quality_profile_id: int | None = None
    language_profile_id: int | None = None
    series_type: Literal["standard", "anime", "daily"] | None = None
    season_folder: bool | None = None
    tags: list[int] | None = None


SERIES_FIELDS = {"monitored": "monitored", "monitor_new_items": "monitorNewItems",
                 "quality_profile_id": "qualityProfileId",
                 "language_profile_id": "languageProfileId", "series_type": "seriesType",
                 "season_folder": "seasonFolder", "tags": "tags"}


@router.patch("/series/{series_id}")
async def series_update(instance_id: str, series_id: int, body: UpdateSeries,
                        principal: Principal = Depends(require_operator),
                        ctx: AppContext = Depends(get_app_context)) -> dict[str, Any]:
    require_instance(ctx, instance_id, app="sonarr", mutable=True)
    client = arr_client(ctx, instance_id)
    current = await arr_call(client.series_get(series_id))
    changes = body.model_dump(exclude_unset=True)
    old = {k: current.get(SERIES_FIELDS[k]) for k in changes}
    updated = dict(current)
    for key, value in changes.items():
        updated[SERIES_FIELDS[key]] = value
    result = await arr_call(client.series_update(updated, move_files=False))
    _audit(ctx, principal, "series.update", instance_id, "series", series_id, old, changes)
    return result


@router.delete("/series/{series_id}")
async def series_delete(instance_id: str, series_id: int, add_import_exclusion: bool = False,
                        principal: Principal = Depends(require_operator),
                        ctx: AppContext = Depends(get_app_context)) -> dict[str, Any]:
    require_instance(ctx, instance_id, app="sonarr", mutable=True)
    client = arr_client(ctx, instance_id)
    current = await arr_call(client.series_get(series_id))
    await arr_call(client.series_delete(series_id, delete_files=False,
                                        add_import_exclusion=add_import_exclusion))
    _audit(ctx, principal, "series.delete", instance_id, "series", series_id,
           old={"title": current.get("title"), "tvdbId": current.get("tvdbId"),
                "path": current.get("path")},
           new={"delete_files": False, "add_import_exclusion": add_import_exclusion},
           reason="Serie verwijderd uit Sonarr; bestanden blijven staan")
    return {"deleted": True, "files_deleted": False}


class SeasonMonitor(BaseModel):
    monitored: bool


@router.put("/series/{series_id}/seasons/{season_number}")
async def season_monitor(instance_id: str, series_id: int, season_number: int,
                         body: SeasonMonitor, principal: Principal = Depends(require_operator),
                         ctx: AppContext = Depends(get_app_context)) -> dict[str, Any]:
    require_instance(ctx, instance_id, app="sonarr", mutable=True)
    client = arr_client(ctx, instance_id)
    await arr_call(client.season_pass(series_id, {season_number: body.monitored}))
    _audit(ctx, principal, "season.monitor", instance_id, "season",
           f"{series_id}:{season_number}", new={"monitored": body.monitored})
    return await arr_call(client.series_get(series_id))


class SeasonsMonitor(BaseModel):
    seasons: dict[int, bool] = Field(default_factory=dict)
    monitor_new_items: Literal["all", "none"] | None = None


@router.put("/series/{series_id}/seasons")
async def seasons_monitor(instance_id: str, series_id: int, body: SeasonsMonitor,
                          principal: Principal = Depends(require_operator),
                          ctx: AppContext = Depends(get_app_context)) -> dict[str, Any]:
    """Seizoenen in één keer + 'Nieuwe seizoenen automatisch volgen' (monitorNewItems)."""
    require_instance(ctx, instance_id, app="sonarr", mutable=True)
    client = arr_client(ctx, instance_id)
    before = await arr_call(client.series_get(series_id))
    await arr_call(client.season_pass(series_id, body.seasons, body.monitor_new_items))
    _audit(ctx, principal, "season.monitor_bulk", instance_id, "series", series_id,
           old={"seasons": {s["seasonNumber"]: s.get("monitored") for s in
                            before.get("seasons", [])},
                "monitorNewItems": before.get("monitorNewItems")},
           new=body.model_dump())
    return await arr_call(client.series_get(series_id))


@router.get("/series/{series_id}/episodes")
async def episodes(instance_id: str, series_id: int, season_number: int | None = None,
                   _: Principal = Depends(require_viewer),
                   ctx: AppContext = Depends(get_app_context)) -> list[dict[str, Any]]:
    require_instance(ctx, instance_id, app="sonarr")
    return await arr_call(arr_client(ctx, instance_id).episodes(series_id, season_number))


class EpisodeMonitor(BaseModel):
    episode_ids: list[int] = Field(min_length=1, max_length=5000)
    monitored: bool


@router.put("/episodes/monitor")
async def episodes_monitor(instance_id: str, body: EpisodeMonitor,
                           principal: Principal = Depends(require_operator),
                           ctx: AppContext = Depends(get_app_context)) -> dict[str, Any]:
    require_instance(ctx, instance_id, app="sonarr", mutable=True)
    await arr_call(arr_client(ctx, instance_id).episodes_monitor(body.episode_ids,
                                                                 body.monitored))
    _audit(ctx, principal, "episode.monitor", instance_id, "episode",
           ",".join(map(str, body.episode_ids[:50])), new=body.model_dump())
    # cache direct bijwerken zodat wanted/KPI's kloppen tot de volgende sync
    marks = ",".join("?" for _ in body.episode_ids)
    ctx.db.execute(f"UPDATE lib_episodes SET monitored=? WHERE instance_id=? AND provider_id IN "
                   f"({marks})", (int(body.monitored), instance_id, *body.episode_ids))
    from mediaguard.wanted.engine import refresh_wanted

    refresh_wanted(ctx, instance_id)
    return {"updated": len(body.episode_ids), "monitored": body.monitored}


# ==== Films (Radarr) =========================================================================
@router.get("/movies")
async def movie_list(instance_id: str, _: Principal = Depends(require_viewer),
                     ctx: AppContext = Depends(get_app_context)) -> list[dict[str, Any]]:
    require_instance(ctx, instance_id, app="radarr")
    return await arr_call(arr_client(ctx, instance_id).movie_list())


@router.get("/movies/lookup")
async def movie_lookup(instance_id: str, term: str = Query(min_length=1, max_length=200),
                       _: Principal = Depends(require_viewer),
                       ctx: AppContext = Depends(get_app_context)) -> list[dict[str, Any]]:
    require_instance(ctx, instance_id, app="radarr")
    return await arr_call(arr_client(ctx, instance_id).movie_lookup(term))


class AddMovie(BaseModel):
    tmdb_id: int
    quality_profile_id: int | None = None
    root_folder_path: str | None = None
    monitored: bool = True
    minimum_availability: Literal["announced", "inCinemas", "released"] | None = None
    monitor: Literal["movieOnly", "movieAndCollection", "none"] = "movieOnly"
    tags: list[int] = Field(default_factory=list)
    search_for_movie: bool = False
    force_duplicate: bool = False


@router.post("/movies", status_code=201)
async def movie_add(instance_id: str, body: AddMovie,
                    principal: Principal = Depends(require_operator),
                    ctx: AppContext = Depends(get_app_context)) -> dict[str, Any]:
    inst = require_instance(ctx, instance_id, app="radarr", mutable=True)
    _ownership_guard(ctx, instance_id, "movie", {"tmdb_id": body.tmdb_id},
                     body.force_duplicate)
    if body.force_duplicate:
        _keep_here(ctx, instance_id, "movie", body.tmdb_id, principal.actor)
    client = arr_client(ctx, instance_id)
    found = await arr_call(client.movie_lookup(f"tmdb:{body.tmdb_id}"))
    if not found:
        raise HTTPException(404, f"Geen film gevonden met TMDB-ID {body.tmdb_id}")
    movie = dict(found[0])
    if movie.get("id"):
        raise HTTPException(409, f"Film staat al in {instance_id} (id {movie['id']})")
    quality = body.quality_profile_id or inst["default_quality_profile_id"]
    root = body.root_folder_path or inst["default_root_folder"]
    if not quality or not root:
        raise HTTPException(422, "Kies een kwaliteitsprofiel en rootmap (of stel standaarden "
                                 "in bij de instantie)")
    movie.update({
        "qualityProfileId": int(quality), "rootFolderPath": root, "monitored": body.monitored,
        "minimumAvailability": body.minimum_availability or
        inst["default_minimum_availability"] or "released",
        "tags": body.tags,
        "addOptions": {"searchForMovie": body.search_for_movie, "monitor": body.monitor},
    })
    created = await arr_call(client.movie_add(movie))
    _audit(ctx, principal, "movie.add", instance_id, "movie", created.get("id"),
           new={"tmdb_id": body.tmdb_id, "title": created.get("title"),
                "quality_profile_id": quality, "root_folder_path": root})
    return created


@router.get("/movies/{movie_id}")
async def movie_get(instance_id: str, movie_id: int, _: Principal = Depends(require_viewer),
                    ctx: AppContext = Depends(get_app_context)) -> dict[str, Any]:
    require_instance(ctx, instance_id, app="radarr")
    return await arr_call(arr_client(ctx, instance_id).movie_get(movie_id))


class UpdateMovie(BaseModel):
    monitored: bool | None = None
    quality_profile_id: int | None = None
    minimum_availability: Literal["announced", "inCinemas", "released"] | None = None
    tags: list[int] | None = None


MOVIE_FIELDS = {"monitored": "monitored", "quality_profile_id": "qualityProfileId",
                "minimum_availability": "minimumAvailability", "tags": "tags"}


@router.patch("/movies/{movie_id}")
async def movie_update(instance_id: str, movie_id: int, body: UpdateMovie,
                       principal: Principal = Depends(require_operator),
                       ctx: AppContext = Depends(get_app_context)) -> dict[str, Any]:
    require_instance(ctx, instance_id, app="radarr", mutable=True)
    client = arr_client(ctx, instance_id)
    current = await arr_call(client.movie_get(movie_id))
    changes = body.model_dump(exclude_unset=True)
    old = {k: current.get(MOVIE_FIELDS[k]) for k in changes}
    updated = dict(current)
    for key, value in changes.items():
        updated[MOVIE_FIELDS[key]] = value
    result = await arr_call(client.movie_update(updated, move_files=False))
    _audit(ctx, principal, "movie.update", instance_id, "movie", movie_id, old, changes)
    return result


@router.delete("/movies/{movie_id}")
async def movie_delete(instance_id: str, movie_id: int, add_import_exclusion: bool = False,
                       principal: Principal = Depends(require_operator),
                       ctx: AppContext = Depends(get_app_context)) -> dict[str, Any]:
    require_instance(ctx, instance_id, app="radarr", mutable=True)
    client = arr_client(ctx, instance_id)
    current = await arr_call(client.movie_get(movie_id))
    await arr_call(client.movie_delete(movie_id, delete_files=False,
                                       add_import_exclusion=add_import_exclusion))
    _audit(ctx, principal, "movie.delete", instance_id, "movie", movie_id,
           old={"title": current.get("title"), "tmdbId": current.get("tmdbId"),
                "path": current.get("path")},
           new={"delete_files": False}, reason="Film verwijderd uit Radarr; bestanden blijven")
    return {"deleted": True, "files_deleted": False}


# ==== Zoekacties & commando's ===================================================================
class SearchBody(BaseModel):
    type: Literal["episodes", "season", "series", "movies"]
    episode_ids: list[int] = Field(default_factory=list, max_length=500)
    series_id: int | None = None
    season_number: int | None = None
    movie_ids: list[int] = Field(default_factory=list, max_length=500)


@router.post("/search")
async def search(instance_id: str, body: SearchBody,
                 principal: Principal = Depends(require_operator),
                 ctx: AppContext = Depends(get_app_context)) -> dict[str, Any]:
    inst = require_instance(ctx, instance_id, mutable=True)
    if inst["suspended"]:
        raise HTTPException(409, "Instantie is gepauzeerd; hervat eerst om te zoeken")
    client = arr_client(ctx, instance_id)
    if body.type == "movies":
        if inst["app"] != "radarr" or not body.movie_ids:
            raise HTTPException(422, "movie_ids vereist op een Radarr-instantie")
        cmd = client.movies_search(body.movie_ids)
    elif inst["app"] != "sonarr":
        raise HTTPException(422, "Afleveringen/seizoenen zoeken kan alleen op Sonarr")
    elif body.type == "episodes":
        if not body.episode_ids:
            raise HTTPException(422, "episode_ids vereist")
        cmd = client.episode_search(body.episode_ids)
    elif body.type == "season":
        if body.series_id is None or body.season_number is None:
            raise HTTPException(422, "series_id en season_number vereist")
        cmd = client.season_search(body.series_id, body.season_number)
    else:
        if body.series_id is None:
            raise HTTPException(422, "series_id vereist")
        cmd = client.series_search(body.series_id)
    result = await arr_call(cmd)
    _audit(ctx, principal, "search.manual", instance_id, "command", result.get("id"),
           new=body.model_dump())
    return result


UI_COMMANDS = {"RssSync", "RefreshSeries", "RescanSeries", "RefreshMovie", "RescanMovie",
               "DownloadedEpisodesScan", "DownloadedMoviesScan", "CheckHealth", "Backup",
               "RefreshMonitoredDownloads", "ApplicationCheckUpdate"}
MASS_SEARCH = {"MissingEpisodeSearch", "CutoffUnmetEpisodeSearch", "MissingMoviesSearch",
               "CutoffUnmetMoviesSearch"}


class CommandBody(BaseModel):
    name: str = Field(max_length=64)
    body: dict[str, Any] = Field(default_factory=dict)
    confirm_mass_search: bool = False


@router.post("/commands")
async def command(instance_id: str, body: CommandBody,
                  principal: Principal = Depends(require_operator),
                  ctx: AppContext = Depends(get_app_context)) -> dict[str, Any]:
    require_instance(ctx, instance_id, mutable=True)
    if body.name in MASS_SEARCH:
        if not body.confirm_mass_search:
            raise HTTPException(409, "Een bibliotheekbrede zoekactie bevraagt alle indexers voor "
                                     "alle ontbrekende items en kan limieten overschrijden. "
                                     "Gebruik bij voorkeur de Backfill-planner; bevestig anders "
                                     "met confirm_mass_search.")
    elif body.name not in UI_COMMANDS:
        raise HTTPException(422, f"Commando {body.name} is niet toegestaan")
    result = await arr_call(arr_client(ctx, instance_id).command(body.name, **body.body))
    _audit(ctx, principal, "command", instance_id, "command", result.get("id"),
           new={"name": body.name, "body": body.body})
    return result


@router.get("/commands/{command_id}")
async def command_status(instance_id: str, command_id: int,
                         _: Principal = Depends(require_viewer),
                         ctx: AppContext = Depends(get_app_context)) -> dict[str, Any]:
    require_instance(ctx, instance_id)
    return await arr_call(arr_client(ctx, instance_id).command_status(command_id))


@router.get("/releases")
async def releases(instance_id: str, episode_id: int | None = None,
                   series_id: int | None = None, season_number: int | None = None,
                   movie_id: int | None = None, _: Principal = Depends(require_operator),
                   ctx: AppContext = Depends(get_app_context)) -> list[dict[str, Any]]:
    """Interactief zoeken (bevraagt indexers; daarom operator). Inclusief afwijzingen en
    MediaGuard-classificatie per afwijzing."""
    from mediaguard.wanted.diagnosis import summarize_release

    require_instance(ctx, instance_id)
    if episode_id is None and movie_id is None and series_id is None:
        raise HTTPException(422, "episode_id, series_id+season_number of movie_id vereist")
    raw = await arr_call(arr_client(ctx, instance_id).releases(
        episode_id=episode_id, series_id=series_id, season_number=season_number,
        movie_id=movie_id))
    return [{**summarize_release(r), "raw": r} for r in raw]


class GrabBody(BaseModel):
    guid: str = Field(min_length=1, max_length=2000)
    indexer_id: int


@router.post("/releases")
async def release_grab(instance_id: str, body: GrabBody,
                       principal: Principal = Depends(require_operator),
                       ctx: AppContext = Depends(get_app_context)) -> dict[str, Any]:
    inst = require_instance(ctx, instance_id, mutable=True)
    if inst["suspended"]:
        raise HTTPException(409, "Instantie is gepauzeerd; grabben is geblokkeerd")
    result = await arr_call(arr_client(ctx, instance_id).release_grab(body.guid,
                                                                      body.indexer_id))
    _audit(ctx, principal, "release.grab", instance_id, "release", body.guid[:200],
           new=body.model_dump())
    return {"grabbed": True, "result": result}


# ==== Providers: indexers / downloadclients / importlijsten ======================================
PROVIDERS = {"indexers": "indexer", "download-clients": "downloadclient",
             "import-lists": "importlist"}


class EnableBody(BaseModel):
    enable: bool | None = None
    enable_rss: bool | None = None
    enable_automatic_search: bool | None = None
    enable_interactive_search: bool | None = None


async def _test_all(ctx: AppContext, instance_id: str, kind: str,
                    label: str) -> dict[str, Any]:
    """Elke client/indexer apart testen (zoals de knop per regel) en per item het resultaat
    geven: 'qBittorrent ✓, SABnzbd ✗' (rapport 4 nov, H9)."""
    from mediaguard.downloads import sab_categories

    if kind == "downloadclient":
        # eerst ontbrekende SABnzbd-categorieën aanmaken, dan pas testen
        with contextlib.suppress(Exception):  # de test zelf moet altijd doorgaan
            await sab_categories.ensure(ctx)
    client = arr_client(ctx, instance_id)
    items = await arr_call(client.provider_list(kind))
    results = []
    for item in items:
        row: dict[str, Any] = {"id": item.get("id"), "name": item.get("name"),
                               "implementation": item.get("implementation"),
                               "enabled": bool(item.get("enable", True))}
        try:
            await client.provider_test(kind, item)
            row.update(ok=True, message_nl="Verbindingstest geslaagd")
        except ArrError as exc:
            row.update(ok=False, message_nl=str(exc)[:500])
        if kind == "downloadclient" and str(item.get("implementation")).lower() == "sabnzbd":
            inst = require_instance(ctx, instance_id)
            cat = sab_categories._category_of(str(inst["app"]), item)
            row["category"] = cat
            if row["ok"]:
                sab_categories.clear_test_failure(ctx, instance_id)
            else:
                sab_categories.note_test_failure(ctx, instance_id, row["message_nl"], cat)
                if sab_categories.CATEGORY_MISSING in row["message_nl"].lower():
                    row["message_nl"] = sab_categories.missing_text(ctx, cat)
        results.append(row)
    ok = all(r["ok"] for r in results if r["enabled"])
    summary = ", ".join(f"{r['name']} {'✓' if r['ok'] else '✗'}" for r in results)
    return {"ok": ok, "all": True, "results": results,
            "summary_nl": summary or f"Geen {label}s ingesteld"}


def _register_provider_routes(segment: str, kind: str) -> None:
    label = segment.rstrip("s")

    @router.get(f"/{segment}", name=f"{kind}_list")
    async def p_list(instance_id: str, _: Principal = Depends(require_viewer),
                     ctx: AppContext = Depends(get_app_context)) -> list[dict[str, Any]]:
        require_instance(ctx, instance_id)
        items = await arr_call(arr_client(ctx, instance_id).provider_list(kind))
        return [mask_provider(i) for i in items]

    @router.get(f"/{segment}/schema", name=f"{kind}_schema")
    async def p_schema(instance_id: str, _: Principal = Depends(require_viewer),
                       ctx: AppContext = Depends(get_app_context)) -> list[dict[str, Any]]:
        require_instance(ctx, instance_id)
        return await arr_call(arr_client(ctx, instance_id).provider_schema(kind))

    @router.get(f"/{segment}/status", name=f"{kind}_status")
    async def p_status(instance_id: str, _: Principal = Depends(require_viewer),
                       ctx: AppContext = Depends(get_app_context)) -> list[dict[str, Any]]:
        require_instance(ctx, instance_id)
        client = arr_client(ctx, instance_id)
        if kind == "indexer":
            return await arr_call(client.indexer_status())
        if kind == "downloadclient":
            return await arr_call(client.download_client_status())
        return []

    @router.post(f"/{segment}", status_code=201, name=f"{kind}_create")
    async def p_create(instance_id: str, body: dict[str, Any], force_save: bool = False,
                       principal: Principal = Depends(require_operator),
                       ctx: AppContext = Depends(get_app_context)) -> dict[str, Any]:
        require_instance(ctx, instance_id, mutable=True)
        body.pop("id", None)
        created = await arr_call(arr_client(ctx, instance_id).provider_create(
            kind, unmask_provider(body, None), force_save=force_save))
        _audit(ctx, principal, f"{kind}.create", instance_id, kind, created.get("id"),
               new=mask_provider(created))
        return mask_provider(created)

    @router.put(f"/{segment}/{{item_id}}", name=f"{kind}_update")
    async def p_update(instance_id: str, item_id: int, body: dict[str, Any],
                       force_save: bool = False, principal: Principal = Depends(require_operator),
                       ctx: AppContext = Depends(get_app_context)) -> dict[str, Any]:
        require_instance(ctx, instance_id, mutable=True)
        client = arr_client(ctx, instance_id)
        current = await arr_call(client.provider_get(kind, item_id))
        merged = unmask_provider({**body, "id": item_id}, current)
        updated = await arr_call(client.provider_update(kind, merged, force_save=force_save))
        _audit(ctx, principal, f"{kind}.update", instance_id, kind, item_id,
               old=mask_provider(current), new=mask_provider(updated))
        return mask_provider(updated)

    @router.delete(f"/{segment}/{{item_id}}", name=f"{kind}_delete")
    async def p_delete(instance_id: str, item_id: int,
                       principal: Principal = Depends(require_operator),
                       ctx: AppContext = Depends(get_app_context)) -> dict[str, bool]:
        require_instance(ctx, instance_id, mutable=True)
        client = arr_client(ctx, instance_id)
        current = await arr_call(client.provider_get(kind, item_id))
        await arr_call(client.provider_delete(kind, item_id))
        _audit(ctx, principal, f"{kind}.delete", instance_id, kind, item_id,
               old=mask_provider(current))
        return {"deleted": True}

    @router.post(f"/{segment}/test-all", name=f"{kind}_test_all")
    async def p_test_all(instance_id: str, _: Principal = Depends(require_operator),
                         ctx: AppContext = Depends(get_app_context)) -> dict[str, Any]:
        require_instance(ctx, instance_id)
        return await _test_all(ctx, instance_id, kind, label)

    @router.post(f"/{segment}/test", name=f"{kind}_test_body")
    async def p_test_body(instance_id: str, body: dict[str, Any] | None = None,
                          _: Principal = Depends(require_operator),
                          ctx: AppContext = Depends(get_app_context)) -> dict[str, Any]:
        require_instance(ctx, instance_id)
        if not body or not body.get("implementation"):
            # 'Alles testen' stuurde een lege body → Sonarr gaf 422 'Name must not be empty'
            return await _test_all(ctx, instance_id, kind, label)
        client = arr_client(ctx, instance_id)
        current = None
        if body.get("id"):
            current = await arr_call(client.provider_get(kind, int(body["id"])))
        result = await arr_call(client.provider_test(kind, unmask_provider(body, current)))
        return {"ok": True, "result": result}

    @router.post(f"/{segment}/{{item_id}}/test", name=f"{kind}_test")
    async def p_test(instance_id: str, item_id: int, _: Principal = Depends(require_operator),
                     ctx: AppContext = Depends(get_app_context)) -> dict[str, Any]:
        require_instance(ctx, instance_id)
        client = arr_client(ctx, instance_id)
        current = await arr_call(client.provider_get(kind, item_id))
        if kind == "downloadclient" and \
                str(current.get("implementation") or "").lower() == "sabnzbd":
            from mediaguard.downloads import sab_categories

            with contextlib.suppress(Exception):  # ontbrekende categorie eerst aanmaken
                await sab_categories.ensure(ctx)
        result = await arr_call(client.provider_test(kind, current))
        return {"ok": True, "result": result}

    @router.patch(f"/{segment}/{{item_id}}/enable", name=f"{kind}_enable")
    async def p_enable(instance_id: str, item_id: int, body: EnableBody,
                       principal: Principal = Depends(require_operator),
                       ctx: AppContext = Depends(get_app_context)) -> dict[str, Any]:
        inst = require_instance(ctx, instance_id, mutable=True)
        client = arr_client(ctx, instance_id)
        current = await arr_call(client.provider_get(kind, item_id))
        mapping = {"enable": "enable", "enable_rss": "enableRss",
                   "enable_automatic_search": "enableAutomaticSearch",
                   "enable_interactive_search": "enableInteractiveSearch"}
        changes = {mapping[k]: v for k, v in body.model_dump(exclude_unset=True).items()}
        if not changes:
            raise HTTPException(422, "Geen wijziging opgegeven")
        deferred: dict[str, Any] = {}
        if kind == "indexer" and inst["suspended"]:
            # tijdens pauze: RSS/automatisch zoeken onthouden voor hervatten
            state = loads(inst["suspend_state_json"], {}) or {}
            remembered = state.setdefault("indexers", {}).setdefault(
                str(item_id), {"enableRss": bool(current.get("enableRss")),
                               "enableAutomaticSearch": bool(
                                   current.get("enableAutomaticSearch")),
                               "name": current.get("name")})
            for f in ("enableRss", "enableAutomaticSearch"):
                if f in changes:
                    remembered[f] = changes.pop(f)
                    deferred[f] = remembered[f]
            ctx.db.execute("UPDATE instances SET suspend_state_json=? WHERE id=?",
                           (dumps(state), instance_id))
        updated = current
        if changes:
            updated = await arr_call(client.provider_update(kind, {**current, **changes},
                                                            force_save=True))
        _audit(ctx, principal, f"{kind}.enable", instance_id, kind, item_id,
               old={k: current.get(k) for k in mapping.values() if k in current},
               new={**changes, **{f"{k} (na hervatten)": v for k, v in deferred.items()}})
        return {**mask_provider(updated), "deferred_until_resume": deferred}

    _ = (p_list, p_schema, p_status, p_create, p_update, p_delete, p_test_body, p_test,
         p_enable, label)


for _segment, _kind in PROVIDERS.items():
    _register_provider_routes(_segment, _kind)


@router.get("/notifications")
async def notifications(instance_id: str, _: Principal = Depends(require_viewer),
                        ctx: AppContext = Depends(get_app_context)) -> list[dict[str, Any]]:
    require_instance(ctx, instance_id)
    items = await arr_call(arr_client(ctx, instance_id).notifications())
    return [mask_provider(i) for i in items]


# ==== Rootmappen, profielen, tags, remote path mappings =========================================
@router.get("/root-folders")
async def root_folders(instance_id: str, _: Principal = Depends(require_viewer),
                       ctx: AppContext = Depends(get_app_context)) -> list[dict[str, Any]]:
    require_instance(ctx, instance_id)
    return await arr_call(arr_client(ctx, instance_id).root_folders())


class RootFolderBody(BaseModel):
    path: str = Field(min_length=1, max_length=1000)


@router.post("/root-folders", status_code=201)
async def root_folder_add(instance_id: str, body: RootFolderBody,
                          principal: Principal = Depends(require_operator),
                          ctx: AppContext = Depends(get_app_context)) -> dict[str, Any]:
    require_instance(ctx, instance_id, mutable=True)
    created = await arr_call(arr_client(ctx, instance_id).root_folder_add(body.path))
    _audit(ctx, principal, "rootfolder.add", instance_id, "rootfolder", created.get("id"),
           new={"path": body.path})
    return created


@router.delete("/root-folders/{folder_id}")
async def root_folder_delete(instance_id: str, folder_id: int,
                             principal: Principal = Depends(require_operator),
                             ctx: AppContext = Depends(get_app_context)) -> dict[str, Any]:
    """Verwijdert alleen de rootmap-registratie in Sonarr/Radarr; nooit de map zelf."""
    require_instance(ctx, instance_id, mutable=True)
    await arr_call(arr_client(ctx, instance_id).root_folder_delete(folder_id))
    _audit(ctx, principal, "rootfolder.delete", instance_id, "rootfolder", folder_id)
    return {"deleted": True, "files_deleted": False}


@router.get("/quality-profiles")
async def quality_profiles(instance_id: str, _: Principal = Depends(require_viewer),
                           ctx: AppContext = Depends(get_app_context)) -> list[dict[str, Any]]:
    require_instance(ctx, instance_id)
    return await arr_call(arr_client(ctx, instance_id).quality_profiles())


@router.get("/language-profiles")
async def language_profiles(instance_id: str, _: Principal = Depends(require_viewer),
                            ctx: AppContext = Depends(get_app_context)) -> list[dict[str, Any]]:
    require_instance(ctx, instance_id)
    return await arr_call(arr_client(ctx, instance_id).language_profiles())


@router.get("/custom-formats")
async def custom_formats(instance_id: str, _: Principal = Depends(require_viewer),
                         ctx: AppContext = Depends(get_app_context)) -> list[dict[str, Any]]:
    require_instance(ctx, instance_id)
    return await arr_call(arr_client(ctx, instance_id).custom_formats())


@router.get("/tags")
async def tags(instance_id: str, _: Principal = Depends(require_viewer),
               ctx: AppContext = Depends(get_app_context)) -> list[dict[str, Any]]:
    require_instance(ctx, instance_id)
    return await arr_call(arr_client(ctx, instance_id).tags())


class TagBody(BaseModel):
    label: str = Field(min_length=1, max_length=100, pattern=r"^[a-z0-9_\-]+$")


@router.post("/tags", status_code=201)
async def tag_create(instance_id: str, body: TagBody,
                     principal: Principal = Depends(require_operator),
                     ctx: AppContext = Depends(get_app_context)) -> dict[str, Any]:
    require_instance(ctx, instance_id, mutable=True)
    created = await arr_call(arr_client(ctx, instance_id).tag_create(body.label))
    _audit(ctx, principal, "tag.create", instance_id, "tag", created.get("id"),
           new={"label": body.label})
    return created


@router.put("/tags/{tag_id}")
async def tag_update(instance_id: str, tag_id: int, body: TagBody,
                     principal: Principal = Depends(require_operator),
                     ctx: AppContext = Depends(get_app_context)) -> dict[str, Any]:
    require_instance(ctx, instance_id, mutable=True)
    updated = await arr_call(arr_client(ctx, instance_id).tag_update(tag_id, body.label))
    _audit(ctx, principal, "tag.update", instance_id, "tag", tag_id, new={"label": body.label})
    return updated


@router.delete("/tags/{tag_id}")
async def tag_delete(instance_id: str, tag_id: int,
                     principal: Principal = Depends(require_operator),
                     ctx: AppContext = Depends(get_app_context)) -> dict[str, bool]:
    require_instance(ctx, instance_id, mutable=True)
    await arr_call(arr_client(ctx, instance_id).tag_delete(tag_id))
    _audit(ctx, principal, "tag.delete", instance_id, "tag", tag_id)
    return {"deleted": True}


@router.get("/remote-path-mappings")
async def rpm_list(instance_id: str, _: Principal = Depends(require_viewer),
                   ctx: AppContext = Depends(get_app_context)) -> list[dict[str, Any]]:
    require_instance(ctx, instance_id)
    return await arr_call(arr_client(ctx, instance_id).remote_path_mappings())


class RemotePathBody(BaseModel):
    host: str = Field(min_length=1, max_length=255)
    remote_path: str = Field(min_length=1, max_length=1000)
    local_path: str = Field(min_length=1, max_length=1000)


@router.post("/remote-path-mappings", status_code=201)
async def rpm_create(instance_id: str, body: RemotePathBody,
                     principal: Principal = Depends(require_operator),
                     ctx: AppContext = Depends(get_app_context)) -> dict[str, Any]:
    require_instance(ctx, instance_id, mutable=True)
    created = await arr_call(arr_client(ctx, instance_id).remote_path_mapping_create(
        body.host, body.remote_path, body.local_path))
    _audit(ctx, principal, "remotepathmapping.create", instance_id, "remotepathmapping",
           created.get("id"), new=body.model_dump())
    return created


@router.put("/remote-path-mappings/{mapping_id}")
async def rpm_update(instance_id: str, mapping_id: int, body: RemotePathBody,
                     principal: Principal = Depends(require_operator),
                     ctx: AppContext = Depends(get_app_context)) -> dict[str, Any]:
    require_instance(ctx, instance_id, mutable=True)
    updated = await arr_call(arr_client(ctx, instance_id).remote_path_mapping_update(
        {"id": mapping_id, "host": body.host, "remotePath": body.remote_path,
         "localPath": body.local_path}))
    _audit(ctx, principal, "remotepathmapping.update", instance_id, "remotepathmapping",
           mapping_id, new=body.model_dump())
    return updated


@router.delete("/remote-path-mappings/{mapping_id}")
async def rpm_delete(instance_id: str, mapping_id: int,
                     principal: Principal = Depends(require_operator),
                     ctx: AppContext = Depends(get_app_context)) -> dict[str, bool]:
    require_instance(ctx, instance_id, mutable=True)
    await arr_call(arr_client(ctx, instance_id).remote_path_mapping_delete(mapping_id))
    _audit(ctx, principal, "remotepathmapping.delete", instance_id, "remotepathmapping",
           mapping_id)
    return {"deleted": True}


# ==== Wachtrij, historie, kalender, systeem ======================================================
@router.get("/queue")
async def queue(instance_id: str, page: int = Query(1, ge=1),
                page_size: int = Query(50, ge=1, le=500), _: Principal = Depends(require_viewer),
                ctx: AppContext = Depends(get_app_context)) -> Any:
    require_instance(ctx, instance_id)
    return await arr_call(arr_client(ctx, instance_id).queue(page=page, page_size=page_size))


@router.delete("/queue/{queue_id}")
async def queue_remove(instance_id: str, queue_id: int, remove_from_client: bool = True,
                       blocklist: bool = False, principal: Principal = Depends(require_operator),
                       ctx: AppContext = Depends(get_app_context)) -> dict[str, bool]:
    require_instance(ctx, instance_id, mutable=True)
    await arr_call(arr_client(ctx, instance_id).queue_remove(
        queue_id, remove_from_client=remove_from_client, blocklist=blocklist))
    _audit(ctx, principal, "queue.remove", instance_id, "queue", queue_id,
           new={"remove_from_client": remove_from_client, "blocklist": blocklist})
    return {"removed": True}


@router.get("/history")
async def history(instance_id: str, page: int = Query(1, ge=1),
                  page_size: int = Query(50, ge=1, le=500), episode_id: int | None = None,
                  series_id: int | None = None, movie_id: int | None = None,
                  event_type: int | None = None, _: Principal = Depends(require_viewer),
                  ctx: AppContext = Depends(get_app_context)) -> Any:
    require_instance(ctx, instance_id)
    return await arr_call(arr_client(ctx, instance_id).history(
        page=page, page_size=page_size, episode_id=episode_id, series_id=series_id,
        movie_id=movie_id, event_type=event_type))


@router.get("/upstream-wanted/{which}")
async def upstream_wanted(instance_id: str, which: Literal["missing", "cutoff"],
                          page: int = Query(1, ge=1), page_size: int = Query(50, ge=1, le=500),
                          _: Principal = Depends(require_viewer),
                          ctx: AppContext = Depends(get_app_context)) -> Any:
    """Ruwe wanted/missing of wanted/cutoff van de instantie zelf (ter vergelijking)."""
    require_instance(ctx, instance_id)
    client = arr_client(ctx, instance_id)
    fn = client.wanted_missing if which == "missing" else client.wanted_cutoff
    return await arr_call(fn(page=page, page_size=page_size))


@router.get("/upstream-calendar")
async def upstream_calendar(instance_id: str, start: str, end: str, unmonitored: bool = False,
                            _: Principal = Depends(require_viewer),
                            ctx: AppContext = Depends(get_app_context)) -> list[dict[str, Any]]:
    require_instance(ctx, instance_id)
    return await arr_call(arr_client(ctx, instance_id).calendar(start, end, unmonitored))


@router.get("/system/status")
async def system_status(instance_id: str, _: Principal = Depends(require_viewer),
                        ctx: AppContext = Depends(get_app_context)) -> dict[str, Any]:
    require_instance(ctx, instance_id)
    return await arr_call(arr_client(ctx, instance_id).system_status())


@router.get("/health")
async def health(instance_id: str, _: Principal = Depends(require_viewer),
                 ctx: AppContext = Depends(get_app_context)) -> list[dict[str, Any]]:
    require_instance(ctx, instance_id)
    return await arr_call(arr_client(ctx, instance_id).health())


@router.get("/backups")
async def backups(instance_id: str, _: Principal = Depends(require_viewer),
                  ctx: AppContext = Depends(get_app_context)) -> list[dict[str, Any]]:
    require_instance(ctx, instance_id)
    return await arr_call(arr_client(ctx, instance_id).backups())


@router.post("/backups")
async def backup_create(instance_id: str, principal: Principal = Depends(require_operator),
                        ctx: AppContext = Depends(get_app_context)) -> dict[str, Any]:
    require_instance(ctx, instance_id)
    result = await arr_call(arr_client(ctx, instance_id).create_backup())
    _audit(ctx, principal, "backup.create", instance_id, "command", result.get("id"),
           new={"requested_at": utcnow()})
    return result
