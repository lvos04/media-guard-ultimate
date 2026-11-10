"""Synchronisatie van series/seizoenen/afleveringen/films/bestanden per instantie naar
de core-cache (``lib_*``-tabellen). De fork blijft de bron (B3); de cache dient voor
overzichten, wanted-analyse en KPI's over alle instanties.

Robuust voor Sonarr v3/v4 en Radarr v3/v5+ verschillen (bijv. ``language`` vs
``languages``, ``statistics.sizeOnDisk`` vs ``sizeOnDisk``, ``monitorNewItems``)."""

from __future__ import annotations

import asyncio
import logging
import uuid
from datetime import UTC, datetime
from typing import TYPE_CHECKING, Any

from mediaguard.arr.client import ArrError
from mediaguard.db import dumps, utcnow
from mediaguard.instances.registry import arr_client, get_instance

if TYPE_CHECKING:
    from mediaguard.context import AppContext

LOGGER = logging.getLogger(__name__)


def iso_z(value: Any) -> str | None:
    """Normaliseert een ISO-datum naar 'YYYY-MM-DDTHH:MM:SSZ' (UTC) zodat
    stringvergelijking in SQL klopt."""
    if not value:
        return None
    text = str(value).strip()
    try:
        if len(text) == 10:
            dt = datetime.fromisoformat(text).replace(tzinfo=UTC)
        else:
            dt = datetime.fromisoformat(text.replace("Z", "+00:00"))
            if dt.tzinfo is None:
                dt = dt.replace(tzinfo=UTC)
    except ValueError:
        return None
    if dt.year < 1900:
        return None
    return dt.astimezone(UTC).strftime("%Y-%m-%dT%H:%M:%SZ")


def now_z() -> str:
    return datetime.now(UTC).strftime("%Y-%m-%dT%H:%M:%SZ")


def _poster(item: dict[str, Any]) -> str | None:
    for image in item.get("images") or []:
        if image.get("coverType") == "poster":
            return image.get("remoteUrl") or image.get("url")
    return None


def _languages(file: dict[str, Any]) -> list[str]:
    langs = file.get("languages")
    if isinstance(langs, list):
        return [str(lang.get("name")) for lang in langs if isinstance(lang, dict)]
    lang = file.get("language")
    if isinstance(lang, dict) and lang.get("name"):
        return [str(lang["name"])]
    return []


def _quality_name(file: dict[str, Any]) -> str | None:
    quality = file.get("quality") or {}
    inner = quality.get("quality") if isinstance(quality, dict) else None
    return (inner or {}).get("name") if isinstance(inner, dict) else None


def _size_on_disk(item: dict[str, Any]) -> int:
    stats = item.get("statistics") or {}
    return int(stats.get("sizeOnDisk") or item.get("sizeOnDisk") or 0)


def _int(value: Any) -> int | None:
    try:
        return int(value) if value not in (None, "", 0) else None
    except (TypeError, ValueError):
        return None


FULL_SYNC_HOURS = 24


def _full_due(ctx: AppContext, instance_id: str) -> bool:
    last = (ctx.state_get(f"library_full_sync:{instance_id}", {}) or {}).get("at")
    if not last:
        return True
    try:
        when = datetime.fromisoformat(str(last))
    except ValueError:
        return True
    return (datetime.now(UTC) - when).total_seconds() >= FULL_SYNC_HOURS * 3600


async def sync_instance(ctx: AppContext, instance_id: str, *,
                        refresh_wanted: bool = True, full: bool | None = None
                        ) -> dict[str, Any]:
    """``full``: True = alle series opnieuw ophalen; False = alleen gewijzigde; None =
    automatisch (eens per 24 uur volledig, anders alleen gewijzigde series)."""
    inst = get_instance(ctx, instance_id)
    if inst is None:
        raise KeyError(instance_id)
    if full is None:
        full = _full_due(ctx, instance_id)
    run_id = ctx.db.execute_insert(
        "INSERT INTO library_sync_runs(instance_id,started_at,status) VALUES (?,?,?)",
        (instance_id, utcnow(), "running"))
    marker = f"{utcnow()}#{uuid.uuid4().hex[:8]}"
    try:
        if inst.app == "sonarr":
            counts = await _sync_sonarr(ctx, instance_id, marker, full=full)
            if full and not counts.get("failed_items"):
                ctx.state_set(f"library_full_sync:{instance_id}", {"at": utcnow()})
        else:
            counts = await _sync_radarr(ctx, instance_id, marker)
        try:
            from mediaguard.instances.service import refresh_metadata

            counts["metadata"] = await refresh_metadata(ctx, instance_id)
        except ArrError as exc:
            counts["metadata_error"] = exc.message
        if refresh_wanted:
            from mediaguard.wanted.engine import refresh_wanted

            counts["wanted"] = await asyncio.to_thread(refresh_wanted, ctx, instance_id)
    except ArrError as exc:
        ctx.db.execute("UPDATE library_sync_runs SET status='failed', finished_at=?, error=? "
                       "WHERE id=?", (utcnow(), exc.message, run_id))
        raise
    except Exception as exc:
        ctx.db.execute("UPDATE library_sync_runs SET status='failed', finished_at=?, error=? "
                       "WHERE id=?", (utcnow(), f"{type(exc).__name__}: {exc}", run_id))
        raise
    status = "partial" if counts.get("failed_items") else "succeeded"
    ctx.db.execute("UPDATE library_sync_runs SET status=?, finished_at=?, counts_json=? "
                   "WHERE id=?", (status, utcnow(), dumps(counts), run_id))
    ctx.events.publish("library.synced", {"run_id": run_id, "counts": counts},
                       instance_id=instance_id)
    try:  # rapport 2 / N28: dagelijkse momentopname van de aantallen
        from mediaguard.library.api import record_count_snapshot

        await asyncio.to_thread(record_count_snapshot, ctx)
    except Exception:  # noqa: BLE001 - alleen informatief
        LOGGER.debug("momentopname aantallen mislukt", exc_info=True)
    return {"run_id": run_id, "status": status, **counts}


# -- Sonarr ---------------------------------------------------------------------------------
def series_fingerprint(s: dict[str, Any]) -> str:
    """Verandert zodra er iets aan de afleveringen van een serie verandert: aantallen,
    grootte, (volgende) uitzending, monitoring per seizoen. Ongewijzigde series hoeven
    hun afleveringen niet opnieuw op te halen."""
    st = s.get("statistics") or {}
    seasons = [(se.get("seasonNumber"), bool(se.get("monitored")),
                (se.get("statistics") or {}).get("episodeCount"),
                (se.get("statistics") or {}).get("episodeFileCount"),
                (se.get("statistics") or {}).get("totalEpisodeCount"),
                (se.get("statistics") or {}).get("sizeOnDisk"))
               for se in s.get("seasons") or []]
    return dumps([st.get("episodeCount"), st.get("episodeFileCount"),
                  st.get("totalEpisodeCount"), st.get("sizeOnDisk"), s.get("previousAiring"),
                  s.get("nextAiring"), bool(s.get("monitored")), s.get("monitorNewItems"),
                  s.get("seriesType"), seasons])


async def _sync_sonarr(ctx: AppContext, instance_id: str, marker: str, *,
                       full: bool = True) -> dict[str, Any]:
    # Ruime timeout: na een restore of grote scan is Sonarr soms traag (gemeten bij een
    # gebruiker: 20 s was te kort, alle afleveringen ontbraken en de UI toonde 0 / 0).
    async with arr_client(ctx, instance_id, timeout=90) as client:
        series_list = await client.series_list()
        known = {int(r["provider_id"]): r["sync_fingerprint"] for r in ctx.db.fetch_all(
            "SELECT provider_id, sync_fingerprint FROM lib_items WHERE instance_id=? AND "
            "kind='series' AND removed_at IS NULL", (instance_id,))}
        have_eps = {int(r["series_provider_id"]) for r in ctx.db.fetch_all(
            "SELECT DISTINCT series_provider_id FROM lib_episodes WHERE instance_id=?",
            (instance_id,))}
        prints = {int(s["id"]): series_fingerprint(s) for s in series_list}
        todo = [s for s in series_list if full or prints[int(s["id"])] != known.get(int(s["id"]))
                or int(s["id"]) not in have_eps]
        # alle seriesrijen (goedkoop) in één korte transactie, buiten de event loop
        await asyncio.to_thread(_write_series_rows, ctx, instance_id, series_list, marker)
        sem = asyncio.Semaphore(4)
        errors: dict[int, str] = {}
        counts = {"episodes": 0, "files": 0}

        async def fetch(series: dict[str, Any]) -> bool:
            sid = int(series["id"])
            async with sem:
                try:
                    eps = await client.episodes(sid)
                except ArrError as exc:
                    errors[sid] = exc.message
                    return False
                try:
                    files = await client.episode_files(sid)
                except ArrError as exc:
                    if not (exc.status and exc.status >= 500):
                        errors[sid] = exc.message
                        return False
                    # Sonarr crasht op de bestandenlijst van deze serie (kapotte waarde in
                    # zijn database): afleveringen en 'heeft bestand' kloppen wel
                    LOGGER.warning("bestandenlijst %s niet leesbaar: %s",
                                   series.get("title"), exc.message)
                    files = []
            # direct wegschrijven en loslaten (geen honderden MB aan resultaten vasthouden)
            await asyncio.to_thread(_write_series_episodes, ctx, instance_id, sid, eps, files,
                                    prints[sid], marker)
            counts["episodes"] += len(eps)
            counts["files"] += len(files)
            errors.pop(sid, None)
            return True

        ok = await asyncio.gather(*(fetch(s) for s in todo))
        # mislukte series nog één keer, één voor één (minder belasting op Sonarr)
        retry = [s for s, r in zip(todo, ok, strict=True) if not r]
        if retry:
            sem = asyncio.Semaphore(1)
            for s in retry:
                await fetch(s)
        failed = [{"series_id": s.get("id"), "title": s.get("title"),
                   "error": errors[int(s["id"])]} for s in series_list
                  if int(s["id"]) in errors]
    present = [int(s["id"]) for s in series_list]
    await asyncio.to_thread(_remove_gone_series, ctx, instance_id, present, marker)
    return {"series": len(series_list), "episodes": counts["episodes"],
            "files": counts["files"], "fetched_series": len(todo), "full": full,
            "failed_items": failed}


def _write_series_rows(ctx: AppContext, instance_id: str, series_list: list[dict[str, Any]],
                       marker: str) -> None:
    with ctx.db.transaction() as conn:
        for series in series_list:
            _upsert_series(conn, instance_id, series, marker)


EP_COLS = ("series_provider_id", "season_number", "episode_number", "absolute_episode_number",
           "scene_season_number", "scene_episode_number", "title", "air_date", "air_date_utc",
           "monitored", "has_file", "episode_file_id", "unverified_scene_numbering")
FILE_COLS = ("owner_provider_id", "path", "relative_path", "size", "quality",
             "quality_cutoff_not_met", "languages_json", "release_group", "media_info_json",
             "date_added")


def _write_series_episodes(ctx: AppContext, instance_id: str, sid: int,
                           eps: list[dict[str, Any]], files: list[dict[str, Any]],
                           fingerprint: str, marker: str) -> None:
    """Eén korte transactie per serie; ongewijzigde rijen worden niet herschreven (geen
    WAL-groei van ~90 MB per sync), verdwenen afleveringen/bestanden worden opgeruimd."""
    with ctx.db.transaction() as conn:
        for ep in eps:
            _upsert_episode(conn, instance_id, ep, marker)
        for file in files:
            _upsert_file(conn, instance_id, "episode", file, sid, marker)
        ep_ids = [int(e["id"]) for e in eps] or [-1]
        conn.execute(f"DELETE FROM lib_episodes WHERE instance_id=? AND series_provider_id=? "
                     f"AND provider_id NOT IN ({','.join('?' * len(ep_ids))})",
                     (instance_id, sid, *ep_ids))
        file_ids = [int(f["id"]) for f in files] or [-1]
        conn.execute(f"DELETE FROM lib_files WHERE instance_id=? AND kind='episode' AND "
                     f"owner_provider_id=? AND provider_id NOT IN "
                     f"({','.join('?' * len(file_ids))})", (instance_id, sid, *file_ids))
        conn.execute("UPDATE lib_items SET sync_fingerprint=? WHERE instance_id=? AND "
                     "kind='series' AND provider_id=?", (fingerprint, instance_id, sid))


def _remove_gone_series(ctx: AppContext, instance_id: str, present: list[int],
                        marker: str) -> None:
    now = utcnow()
    with ctx.db.transaction() as conn:
        # verwijderd in Sonarr -> markeren (series) of opruimen (afleveringen/bestanden)
        conn.execute("UPDATE lib_items SET removed_at=?, sync_fingerprint=NULL WHERE "
                     "instance_id=? AND kind='series' AND synced_at!=? AND removed_at IS NULL",
                     (now, instance_id, marker))
        marks = ",".join("?" for _ in present) or "-1"
        for table in ("lib_episodes", "lib_seasons"):
            conn.execute(f"DELETE FROM {table} WHERE instance_id=? AND series_provider_id "
                         f"NOT IN ({marks})", (instance_id, *present))
        conn.execute(f"DELETE FROM lib_files WHERE instance_id=? AND kind='episode' AND "
                     f"owner_provider_id NOT IN ({marks})", (instance_id, *present))


def _upsert_series(conn: Any, instance_id: str, s: dict[str, Any], marker: str) -> None:
    stats = s.get("statistics") or {}
    conn.execute(
        "INSERT INTO lib_items(instance_id,kind,provider_id,title,sort_title,year,tvdb_id,"
        "tmdb_id,imdb_id,tvmaze_id,series_type,status,monitored,monitor_new_items,"
        "quality_profile_id,language_profile_id,root_folder_path,path,tags_json,size_on_disk,"
        "episode_count,episode_file_count,network,added_at,poster_url,synced_at,removed_at) "
        "VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,NULL) "
        "ON CONFLICT(instance_id,kind,provider_id) DO UPDATE SET title=excluded.title, "
        "sort_title=excluded.sort_title, year=excluded.year, tvdb_id=excluded.tvdb_id, "
        "tmdb_id=excluded.tmdb_id, imdb_id=excluded.imdb_id, tvmaze_id=excluded.tvmaze_id, "
        "series_type=excluded.series_type, status=excluded.status, "
        "monitored=excluded.monitored, monitor_new_items=excluded.monitor_new_items, "
        "quality_profile_id=excluded.quality_profile_id, "
        "language_profile_id=excluded.language_profile_id, "
        "root_folder_path=excluded.root_folder_path, path=excluded.path, "
        "tags_json=excluded.tags_json, size_on_disk=excluded.size_on_disk, "
        "episode_count=excluded.episode_count, episode_file_count=excluded.episode_file_count, "
        "network=excluded.network, added_at=excluded.added_at, poster_url=excluded.poster_url, "
        "synced_at=excluded.synced_at, removed_at=NULL",
        (instance_id, "series", int(s["id"]), str(s.get("title") or ""),
         s.get("sortTitle") or str(s.get("title") or "").lower(), _int(s.get("year")),
         _int(s.get("tvdbId")), _int(s.get("tmdbId")), s.get("imdbId") or None,
         _int(s.get("tvMazeId")), s.get("seriesType"), s.get("status"),
         int(bool(s.get("monitored"))), s.get("monitorNewItems"),
         _int(s.get("qualityProfileId")), _int(s.get("languageProfileId")),
         s.get("rootFolderPath"), s.get("path"), dumps(s.get("tags") or []), _size_on_disk(s),
         stats.get("episodeCount"), stats.get("episodeFileCount"), s.get("network"),
         s.get("added"), _poster(s), marker))
    for season in s.get("seasons") or []:
        st = season.get("statistics") or {}
        conn.execute(
            "INSERT INTO lib_seasons(instance_id,series_provider_id,season_number,monitored,"
            "episode_count,episode_file_count,total_episode_count,size_on_disk,synced_at) "
            "VALUES (?,?,?,?,?,?,?,?,?) ON CONFLICT(instance_id,series_provider_id,"
            "season_number) DO UPDATE SET monitored=excluded.monitored, "
            "episode_count=excluded.episode_count, episode_file_count=excluded.episode_file_count,"
            " total_episode_count=excluded.total_episode_count, "
            "size_on_disk=excluded.size_on_disk, synced_at=excluded.synced_at",
            (instance_id, int(s["id"]), int(season.get("seasonNumber", 0)),
             int(bool(season.get("monitored"))), st.get("episodeCount"),
             st.get("episodeFileCount"), st.get("totalEpisodeCount"), st.get("sizeOnDisk"),
             marker))
    conn.execute("DELETE FROM lib_seasons WHERE instance_id=? AND series_provider_id=? AND "
                 "synced_at!=?", (instance_id, int(s["id"]), marker))


def _upsert_episode(conn: Any, instance_id: str, e: dict[str, Any], marker: str) -> None:
    conn.execute(
        "INSERT INTO lib_episodes(instance_id,provider_id,series_provider_id,season_number,"
        "episode_number,absolute_episode_number,scene_season_number,scene_episode_number,title,"
        "air_date,air_date_utc,monitored,has_file,episode_file_id,unverified_scene_numbering,"
        "synced_at) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?) "
        "ON CONFLICT(instance_id,provider_id) DO UPDATE SET "
        "series_provider_id=excluded.series_provider_id, season_number=excluded.season_number, "
        "episode_number=excluded.episode_number, "
        "absolute_episode_number=excluded.absolute_episode_number, "
        "scene_season_number=excluded.scene_season_number, "
        "scene_episode_number=excluded.scene_episode_number, title=excluded.title, "
        "air_date=excluded.air_date, air_date_utc=excluded.air_date_utc, "
        "monitored=excluded.monitored, has_file=excluded.has_file, "
        "episode_file_id=excluded.episode_file_id, "
        "unverified_scene_numbering=excluded.unverified_scene_numbering, "
        "synced_at=excluded.synced_at WHERE (" + ",".join(f"lib_episodes.{c}" for c in EP_COLS)
        + ") IS NOT (" + ",".join(f"excluded.{c}" for c in EP_COLS) + ")",
        (instance_id, int(e["id"]), int(e.get("seriesId") or 0), int(e.get("seasonNumber", 0)),
         int(e.get("episodeNumber", 0)), _int(e.get("absoluteEpisodeNumber")),
         e.get("sceneSeasonNumber"), e.get("sceneEpisodeNumber"), e.get("title"),
         e.get("airDate"), iso_z(e.get("airDateUtc")), int(bool(e.get("monitored"))),
         int(bool(e.get("hasFile"))), _int(e.get("episodeFileId")),
         int(bool(e.get("unverifiedSceneNumbering"))), marker))


def _upsert_file(conn: Any, instance_id: str, kind: str, f: dict[str, Any], owner_id: int,
                 marker: str) -> None:
    conn.execute(
        "INSERT INTO lib_files(instance_id,kind,provider_id,owner_provider_id,path,"
        "relative_path,size,quality,quality_cutoff_not_met,languages_json,release_group,"
        "media_info_json,date_added,synced_at) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?) "
        "ON CONFLICT(instance_id,kind,provider_id) DO UPDATE SET "
        "owner_provider_id=excluded.owner_provider_id, path=excluded.path, "
        "relative_path=excluded.relative_path, size=excluded.size, quality=excluded.quality, "
        "quality_cutoff_not_met=excluded.quality_cutoff_not_met, "
        "languages_json=excluded.languages_json, release_group=excluded.release_group, "
        "media_info_json=excluded.media_info_json, date_added=excluded.date_added, "
        "synced_at=excluded.synced_at WHERE (" + ",".join(f"lib_files.{c}" for c in FILE_COLS)
        + ") IS NOT (" + ",".join(f"excluded.{c}" for c in FILE_COLS) + ")",
        (instance_id, kind, int(f["id"]), owner_id, f.get("path"), f.get("relativePath"),
         _int(f.get("size")), _quality_name(f), int(bool(f.get("qualityCutoffNotMet"))),
         dumps(_languages(f)), f.get("releaseGroup"), dumps(f.get("mediaInfo") or {}),
         f.get("dateAdded"), marker))


# -- Radarr ---------------------------------------------------------------------------------
async def _sync_radarr(ctx: AppContext, instance_id: str, marker: str) -> dict[str, Any]:
    async with arr_client(ctx, instance_id) as client:
        movies = await client.movie_list()
    return await asyncio.to_thread(_write_radarr, ctx, instance_id, movies, marker)


def _write_radarr(ctx: AppContext, instance_id: str, movies: list[dict[str, Any]],
                  marker: str) -> dict[str, Any]:
    now = utcnow()
    n_files = 0
    file_ids: list[int] = []
    with ctx.db.transaction() as conn:
        for m in movies:
            file = m.get("movieFile") if isinstance(m.get("movieFile"), dict) else None
            has_file = bool(m.get("hasFile")) or file is not None
            conn.execute(
                "INSERT INTO lib_items(instance_id,kind,provider_id,title,sort_title,year,"
                "tmdb_id,imdb_id,status,monitored,minimum_availability,is_available,"
                "quality_profile_id,root_folder_path,path,tags_json,has_file,file_id,"
                "size_on_disk,in_cinemas,digital_release,physical_release,added_at,poster_url,"
                "synced_at,removed_at) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,"
                "?,NULL) ON CONFLICT(instance_id,kind,provider_id) DO UPDATE SET "
                "title=excluded.title, sort_title=excluded.sort_title, year=excluded.year, "
                "tmdb_id=excluded.tmdb_id, imdb_id=excluded.imdb_id, status=excluded.status, "
                "monitored=excluded.monitored, minimum_availability=excluded.minimum_availability,"
                " is_available=excluded.is_available, "
                "quality_profile_id=excluded.quality_profile_id, "
                "root_folder_path=excluded.root_folder_path, path=excluded.path, "
                "tags_json=excluded.tags_json, has_file=excluded.has_file, "
                "file_id=excluded.file_id, size_on_disk=excluded.size_on_disk, "
                "in_cinemas=excluded.in_cinemas, digital_release=excluded.digital_release, "
                "physical_release=excluded.physical_release, added_at=excluded.added_at, "
                "poster_url=excluded.poster_url, synced_at=excluded.synced_at, removed_at=NULL",
                (instance_id, "movie", int(m["id"]), str(m.get("title") or ""),
                 m.get("sortTitle") or str(m.get("title") or "").lower(), _int(m.get("year")),
                 _int(m.get("tmdbId")), m.get("imdbId") or None, m.get("status"),
                 int(bool(m.get("monitored"))), m.get("minimumAvailability"),
                 int(bool(m.get("isAvailable"))), _int(m.get("qualityProfileId")),
                 m.get("rootFolderPath"), m.get("path"), dumps(m.get("tags") or []),
                 int(has_file), _int((file or {}).get("id")), _size_on_disk(m),
                 iso_z(m.get("inCinemas")), iso_z(m.get("digitalRelease")),
                 iso_z(m.get("physicalRelease")), m.get("added"), _poster(m), marker))
            if file is not None and file.get("id"):
                _upsert_file(conn, instance_id, "movie", file, int(m["id"]), marker)
                file_ids.append(int(file["id"]))
                n_files += 1
        conn.execute("UPDATE lib_items SET removed_at=? WHERE instance_id=? AND kind='movie' "
                     "AND synced_at!=? AND removed_at IS NULL", (now, instance_id, marker))
        conn.execute("CREATE TEMP TABLE IF NOT EXISTS _mg_ids(id INTEGER PRIMARY KEY)")
        conn.execute("DELETE FROM _mg_ids")
        conn.executemany("INSERT OR IGNORE INTO _mg_ids(id) VALUES (?)",
                         [(i,) for i in file_ids])
        conn.execute("DELETE FROM lib_files WHERE instance_id=? AND kind='movie' AND "
                     "provider_id NOT IN (SELECT id FROM _mg_ids)", (instance_id,))
        conn.execute("DELETE FROM _mg_ids")
    return {"movies": len(movies), "files": n_files, "failed_items": []}
