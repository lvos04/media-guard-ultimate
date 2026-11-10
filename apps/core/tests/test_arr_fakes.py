"""In-proces fake Sonarr v3/v4- en Radarr v3/v5-API's (en Seerr) voor B1-tests.

Response-vormen volgen de echte API (camelCase, ``{page,pageSize,totalRecords,records}``
paging, provider-resources met ``fields``, commando-objecten met ``status``).
Andere testbestanden importeren deze module: ``from test_arr_fakes import ...``."""

from __future__ import annotations

import copy
import itertools
from collections.abc import Callable
from datetime import UTC, datetime, timedelta
from typing import Any

import httpx
import pytest
from fastapi import FastAPI, HTTPException, Request
from fastapi.responses import JSONResponse

from mediaguard.instances.registry import apikey_secret, seed_instances


def iso(days: float = 0) -> str:
    return (datetime.now(UTC) + timedelta(days=days)).strftime("%Y-%m-%dT%H:%M:%SZ")


def paged(records: list[dict[str, Any]], request: Request, sort_key: str = "id") -> dict:
    page = int(request.query_params.get("page", 1))
    size = int(request.query_params.get("pageSize", 10))
    start = (page - 1) * size
    return {"page": page, "pageSize": size, "sortKey": sort_key, "sortDirection": "descending",
            "totalRecords": len(records), "records": records[start:start + size]}


INDEXER_TEMPLATE = {
    "implementationName": "Newznab", "implementation": "Newznab",
    "configContract": "NewznabSettings", "infoLink": "https://wiki.servarr.com",
    "supportsRss": True, "supportsSearch": True, "protocol": "usenet", "priority": 25,
    "downloadClientId": 0, "tags": [],
}


def indexer(iid: int, name: str, *, rss: bool = True, auto: bool = True,
            interactive: bool = True, tags: list[int] | None = None,
            protocol: str = "usenet") -> dict[str, Any]:
    return {**INDEXER_TEMPLATE, "id": iid, "name": name, "enableRss": rss,
            "enableAutomaticSearch": auto, "enableInteractiveSearch": interactive,
            "tags": tags or [], "protocol": protocol,
            "fields": [
                {"order": 0, "name": "baseUrl", "label": "URL", "value": "https://idx.example",
                 "type": "textbox", "advanced": False, "privacy": "normal"},
                {"order": 2, "name": "apiKey", "label": "API Key", "value": f"idx-secret-{iid}",
                 "type": "textbox", "advanced": False, "privacy": "apiKey"},
                {"order": 3, "name": "categories", "label": "Categories", "value": [5030, 5040],
                 "type": "select", "privacy": "normal"},
            ]}


def download_client(cid: int, name: str = "SABnzbd", protocol: str = "usenet",
                    enable: bool = True) -> dict[str, Any]:
    return {"id": cid, "name": name, "enable": enable, "protocol": protocol, "priority": 1,
            "removeCompletedDownloads": True, "removeFailedDownloads": True,
            "implementation": "Sabnzbd", "implementationName": "SABnzbd",
            "configContract": "SabnzbdSettings", "tags": [],
            "fields": [{"name": "host", "value": "sabnzbd", "privacy": "normal"},
                       {"name": "apiKey", "value": "sab-secret", "privacy": "apiKey"},
                       {"name": "tvCategory", "value": "tv", "privacy": "normal"}]}


WEBHOOK_SCHEMA = {
    "name": "", "implementation": "Webhook", "implementationName": "Webhook",
    "configContract": "WebhookSettings", "tags": [],
    "fields": [{"order": 0, "name": "url", "label": "URL", "type": "url", "value": None},
               {"order": 1, "name": "method", "label": "Method", "type": "select", "value": 1},
               {"order": 2, "name": "username", "label": "Username", "type": "textbox",
                "value": None, "privacy": "userName"},
               {"order": 3, "name": "password", "label": "Password", "type": "password",
                "value": None, "privacy": "password"}],
    "onGrab": False, "onDownload": False, "onUpgrade": False, "onRename": False,
    "onHealthIssue": False, "onHealthRestored": False, "onApplicationUpdate": False,
    "onManualInteractionRequired": False, "includeHealthWarnings": False,
    "supportsOnGrab": True, "supportsOnDownload": True, "supportsOnUpgrade": True,
    "supportsOnRename": True, "supportsOnHealthIssue": True, "supportsOnHealthRestored": True,
    "supportsOnApplicationUpdate": True, "supportsOnManualInteractionRequired": True,
}


def make_series(sid: int, title: str, tvdb: int, *, monitored: bool = True,
                series_type: str = "standard", quality_profile_id: int = 1,
                seasons: dict[int, bool] | None = None, root: str = "/tv/",
                tags: list[int] | None = None, monitor_new_items: str = "all",
                status: str = "continuing") -> dict[str, Any]:
    seasons = seasons if seasons is not None else {1: True}
    return {
        "id": sid, "title": title, "sortTitle": title.lower(), "status": status,
        "ended": status == "ended", "network": "HBO", "year": 2020, "tvdbId": tvdb,
        "tvMazeId": tvdb + 1, "tmdbId": tvdb + 2, "imdbId": f"tt{tvdb}",
        "images": [{"coverType": "poster", "url": f"/MediaCover/{sid}/poster.jpg",
                    "remoteUrl": f"https://artworks.thetvdb.com/{tvdb}.jpg"}],
        "seasons": [{"seasonNumber": n, "monitored": m,
                     "statistics": {"episodeFileCount": 0, "episodeCount": 0,
                                    "totalEpisodeCount": 0, "sizeOnDisk": 0}}
                    for n, m in seasons.items()],
        "path": f"{root.rstrip('/')}/{title}", "rootFolderPath": root,
        "qualityProfileId": quality_profile_id, "seasonFolder": True, "monitored": monitored,
        "monitorNewItems": monitor_new_items, "seriesType": series_type, "tags": tags or [],
        "added": "2023-01-01T00:00:00Z",
        "statistics": {"seasonCount": len(seasons), "episodeFileCount": 0, "episodeCount": 0,
                       "totalEpisodeCount": 0, "sizeOnDisk": 0},
    }


def make_episode(eid: int, sid: int, season: int, number: int, *, aired_days: float = -30,
                 monitored: bool = True, has_file: bool = False, file_id: int = 0,
                 absolute: int | None = None, air: bool = True,
                 unverified: bool = False) -> dict[str, Any]:
    ep: dict[str, Any] = {
        "id": eid, "seriesId": sid, "tvdbId": 9000 + eid, "episodeFileId": file_id,
        "seasonNumber": season, "episodeNumber": number, "title": f"Aflevering {number}",
        "runtime": 45, "hasFile": has_file, "monitored": monitored,
        "unverifiedSceneNumbering": unverified, "overview": "",
    }
    if absolute is not None:
        ep["absoluteEpisodeNumber"] = absolute
    if air:
        ep["airDateUtc"] = iso(aired_days)
        ep["airDate"] = iso(aired_days)[:10]
    return ep


def make_episode_file(fid: int, sid: int, season: int, *, cutoff_not_met: bool = False,
                      quality: str = "WEBDL-1080p") -> dict[str, Any]:
    return {"id": fid, "seriesId": sid, "seasonNumber": season,
            "relativePath": f"Season {season:02d}/file{fid}.mkv",
            "path": f"/tv/x/Season {season:02d}/file{fid}.mkv", "size": 1_500_000_000,
            "dateAdded": "2024-01-01T00:00:00Z", "releaseGroup": "NTb",
            "languages": [{"id": 1, "name": "English"}],
            "quality": {"quality": {"id": 3, "name": quality, "source": "web",
                                    "resolution": 1080},
                        "revision": {"version": 1, "real": 0, "isRepack": False}},
            "customFormats": [], "customFormatScore": 0,
            "mediaInfo": {"videoCodec": "x264", "audioLanguages": "eng", "resolution": "1920x1080"},
            "qualityCutoffNotMet": cutoff_not_met}


def make_movie(mid: int, title: str, tmdb: int, *, monitored: bool = True,
               has_file: bool = False, available: bool = True, file_cutoff: bool = False,
               digital_days: float = -60, root: str = "/movies/") -> dict[str, Any]:
    movie: dict[str, Any] = {
        "id": mid, "title": title, "originalTitle": title, "sortTitle": title.lower(),
        "sizeOnDisk": 2_000_000_000 if has_file else 0, "status": "released" if available
        else "announced", "images": [], "year": 2021, "hasFile": has_file,
        "monitored": monitored, "minimumAvailability": "released", "isAvailable": available,
        "folderName": f"{root}{title} (2021)", "runtime": 120, "tmdbId": tmdb,
        "imdbId": f"tt{tmdb}", "titleSlug": str(tmdb), "rootFolderPath": root,
        "qualityProfileId": 1, "path": f"{root}{title} (2021)", "tags": [],
        "inCinemas": iso(digital_days - 60), "digitalRelease": iso(digital_days),
        "added": "2023-01-01T00:00:00Z",
        "statistics": {"movieFileCount": int(has_file), "sizeOnDisk": 0, "releaseGroups": []},
    }
    if has_file:
        movie["movieFile"] = {
            "id": mid * 10, "movieId": mid, "relativePath": f"{title}.mkv",
            "path": f"{root}{title} (2021)/{title}.mkv", "size": 2_000_000_000,
            "dateAdded": "2024-01-01T00:00:00Z", "languages": [{"id": 1, "name": "English"}],
            "quality": {"quality": {"id": 7, "name": "Bluray-1080p"}},
            "qualityCutoffNotMet": file_cutoff, "mediaInfo": {}}
    return movie


def make_release(title: str, *, rejections: list[str] | None = None, guid: str | None = None,
                 indexer_id: int = 1, protocol: str = "usenet",
                 quality: str = "WEBDL-1080p") -> dict[str, Any]:
    rejections = rejections or []
    return {"guid": guid or f"guid-{title}", "title": title, "indexerId": indexer_id,
            "indexer": "NZBgeek", "protocol": protocol, "size": 1_500_000_000, "age": 3,
            "ageHours": 80.0, "releaseGroup": "NTb",
            "quality": {"quality": {"id": 3, "name": quality, "source": "web",
                                    "resolution": 1080}},
            "languages": [{"id": 1, "name": "English"}], "customFormats": [],
            "customFormatScore": 0, "approved": not rejections, "temporarilyRejected": False,
            "rejected": bool(rejections), "rejections": rejections, "downloadAllowed": True,
            "seeders": None, "episodeRequested": True}


class FakeArr:
    """Emuleert de gebruikte delen van de Sonarr/Radarr v3-API met een in-memory staat."""

    def __init__(self, app: str, api_key: str = "fake-api-key-123",
                 version: str | None = None, language_profiles: bool = False) -> None:
        self.app = app
        self.api_key = api_key
        self.version = version or ("4.0.14.2939" if app == "sonarr" else "5.26.2.10099")
        self.language_profiles = language_profiles
        self.series: dict[int, dict[str, Any]] = {}
        self.episodes: dict[int, dict[str, Any]] = {}
        self.episode_files: dict[int, dict[str, Any]] = {}
        self.movies: dict[int, dict[str, Any]] = {}
        self.providers: dict[str, dict[int, dict[str, Any]]] = {
            "indexer": {}, "downloadclient": {}, "notification": {}, "importlist": {}}
        self.indexer_status: list[dict[str, Any]] = []
        self.health: list[dict[str, Any]] = []
        self.root_folders = [{"id": 1, "path": "/tv/" if app == "sonarr" else "/movies/",
                              "accessible": True, "freeSpace": 10**12, "unmappedFolders": []}]
        self.quality_profiles = [{"id": 1, "name": "HD-1080p", "upgradeAllowed": True,
                                  "cutoff": 3, "items": [{"quality": {"id": 3, "name":
                                                                      "WEBDL-1080p"},
                                                          "allowed": True}]},
                                 {"id": 2, "name": "Ultra-HD", "upgradeAllowed": True,
                                  "cutoff": 19, "items": []}]
        self.tags: dict[int, dict[str, Any]] = {}
        self.rpm: dict[int, dict[str, Any]] = {}
        self.queue: list[dict[str, Any]] = []
        self.history: list[dict[str, Any]] = []
        self.releases: dict[tuple[str, int], list[dict[str, Any]]] = {}
        self.manual_import: list[dict[str, Any]] = []
        self.commands: dict[int, dict[str, Any]] = {}
        self.command_final_status = "completed"
        self.grabs: list[dict[str, Any]] = []
        self.calls: list[tuple[str, str, dict[str, str], Any]] = []
        self.on_command: Callable[[dict[str, Any]], None] | None = None
        self.fail_paths: dict[str, int] = {}
        # (methode, pad) -> functie(body) -> (status, json) of None; om een andere
        # Sonarr/Radarr-versie na te bootsen (onbekende velden/implementaties)
        self.intercept: dict[tuple[str, str], Callable[[Any], tuple[int, Any] | None]] = {}
        self._ids = itertools.count(1000)
        self.asgi = self._build()

    # -- helpers ----------------------------------------------------------------------------
    def transport(self) -> httpx.ASGITransport:
        return httpx.ASGITransport(app=self.asgi)

    def next_id(self) -> int:
        return next(self._ids)

    def add_series(self, series: dict[str, Any], episodes: list[dict[str, Any]],
                   files: list[dict[str, Any]] | None = None) -> None:
        self.series[series["id"]] = series
        for ep in episodes:
            self.episodes[ep["id"]] = ep
        for f in files or []:
            self.episode_files[f["id"]] = f

    def add_indexer(self, idx: dict[str, Any]) -> None:
        self.providers["indexer"][idx["id"]] = idx

    def calls_to(self, method: str, prefix: str) -> list[tuple[str, str, dict, Any]]:
        return [c for c in self.calls if c[0] == method and c[1].startswith(prefix)]

    def grab_event(self, episode_id: int | None = None, movie_id: int | None = None,
                   title: str = "Release.Title", event: str = "grabbed",
                   days: float = 0, data: dict | None = None) -> dict[str, Any]:
        rec = {"id": self.next_id(), "sourceTitle": title, "date": iso(days),
               "eventType": event, "data": data or {}, "downloadId": "SAB_1"}
        if episode_id is not None:
            rec.update(episodeId=episode_id, seriesId=self.episodes[episode_id]["seriesId"])
        if movie_id is not None:
            rec["movieId"] = movie_id
        self.history.insert(0, rec)
        return rec

    def _with_stats(self, series: dict[str, Any]) -> dict[str, Any]:
        s = copy.deepcopy(series)
        eps = [e for e in self.episodes.values() if e["seriesId"] == s["id"]]
        for season in s["seasons"]:
            se = [e for e in eps if e["seasonNumber"] == season["seasonNumber"]]
            season["statistics"] = {"episodeFileCount": sum(e["hasFile"] for e in se),
                                    "episodeCount": len(se), "totalEpisodeCount": len(se),
                                    "sizeOnDisk": 0}
        s["statistics"] = {"seasonCount": len(s["seasons"]),
                           "episodeFileCount": sum(e["hasFile"] for e in eps),
                           "episodeCount": len(eps), "totalEpisodeCount": len(eps),
                           "sizeOnDisk": 0}
        return s

    # -- ASGI-app -----------------------------------------------------------------------------
    def _build(self) -> FastAPI:  # noqa: C901 - een fake-API is nu eenmaal breed
        api = FastAPI()
        fake = self

        @api.middleware("http")
        async def auth(request: Request, call_next):  # type: ignore[no-untyped-def]
            body = None
            if request.method in ("POST", "PUT"):
                raw = await request.body()
                if raw:
                    import json as _json
                    try:
                        body = _json.loads(raw)
                    except ValueError:  # multipart (bv. restore-upload): niet als JSON
                        body = {"_raw_bytes": len(raw)}
            fake.calls.append((request.method, request.url.path,
                               dict(request.query_params), body))
            if request.headers.get("X-Api-Key") != fake.api_key:
                return JSONResponse(status_code=401, content={"message": "Unauthorized"})
            hook = fake.intercept.get((request.method, request.url.path))
            if hook is not None:
                answer = hook(body)
                if answer is not None:
                    return JSONResponse(status_code=answer[0], content=answer[1])
            for prefix, status in fake.fail_paths.items():
                if request.url.path.startswith(prefix):
                    return JSONResponse(status_code=status, content={"message": "fake failure"})
            return await call_next(request)

        p = "/api/v3"

        @api.get(p + "/system/status")
        def status() -> dict:
            return {"appName": fake.app.capitalize(), "instanceName": fake.app.capitalize(),
                    "version": fake.version, "isDocker": True, "branch": "main",
                    "authentication": "forms", "urlBase": "", "runtimeVersion": "8.0.12"}

        @api.get(p + "/health")
        def health() -> list:
            return fake.health

        @api.get(p + "/system/backup")
        def backups() -> list:
            return [{"id": 1, "name": "sonarr_backup.zip", "path": "/backup/x.zip",
                     "type": "scheduled", "size": 1234, "time": iso(-1)}]

        # series ---------------------------------------------------------------------------
        @api.get(p + "/series")
        def series_list() -> list:
            return [fake._with_stats(s) for s in fake.series.values()]

        @api.get(p + "/series/lookup")
        def series_lookup(term: str) -> list:
            tvdb = int(term.split(":", 1)[1]) if term.startswith("tvdb:") else None
            for s in fake.series.values():
                if s["tvdbId"] == tvdb:
                    return [fake._with_stats(s)]
            if tvdb:
                s = make_series(0, f"Lookup {tvdb}", tvdb)
                s.pop("id")
                s["path"] = None
                return [s]
            return []

        @api.get(p + "/series/{sid}")
        def series_get(sid: int) -> dict:
            if sid not in fake.series:
                raise HTTPException(404, "Series not found")
            return fake._with_stats(fake.series[sid])

        @api.post(p + "/series", status_code=201)
        async def series_add(request: Request) -> dict:
            body = await request.json()
            sid = fake.next_id()
            body["id"] = sid
            body.setdefault("seasons", [{"seasonNumber": 1, "monitored": True}])
            fake.series[sid] = body
            return body

        @api.put(p + "/series/{sid}", status_code=202)
        async def series_put(sid: int, request: Request) -> dict:
            # zoals echte Sonarr (SeriesService.UpdateSeries): afleveringen volgen alleen
            # seizoenen waarvan de vlag wijzigt
            body = await request.json()
            before = {x["seasonNumber"]: x["monitored"]
                      for x in (fake.series.get(sid) or {}).get("seasons", [])}
            for season in body.get("seasons", []):
                n = season["seasonNumber"]
                if n in before and before[n] != season["monitored"]:
                    for e in fake.episodes.values():
                        if e["seriesId"] == sid and e["seasonNumber"] == n:
                            e["monitored"] = season["monitored"]
            fake.series[sid] = body
            return body

        @api.delete(p + "/series/{sid}")
        def series_delete(sid: int) -> dict:
            fake.series.pop(sid, None)
            return {}

        @api.post(p + "/seasonpass", status_code=202)
        async def seasonpass(request: Request) -> dict:
            body = await request.json()
            for entry in body["series"]:
                s = fake.series[entry["id"]]
                s["monitored"] = entry.get("monitored", s["monitored"])
                wanted = {x["seasonNumber"]: x["monitored"] for x in entry["seasons"]}
                for season in s["seasons"]:
                    if season["seasonNumber"] in wanted:
                        season["monitored"] = wanted[season["seasonNumber"]]
                        # echte Sonarr 4: /seasonpass zet afleveringen NIET mee
            return {}

        @api.get(p + "/episode")
        def episodes(seriesId: int, seasonNumber: int | None = None) -> list:  # noqa: N803
            return [e for e in fake.episodes.values() if e["seriesId"] == seriesId and
                    (seasonNumber is None or e["seasonNumber"] == seasonNumber)]

        @api.get(p + "/episode/{eid}")
        def episode(eid: int) -> dict:
            if eid not in fake.episodes:
                raise HTTPException(404, "Episode not found")
            return fake.episodes[eid]

        @api.put(p + "/episode/monitor", status_code=202)
        async def episode_monitor(request: Request) -> dict:
            body = await request.json()
            for eid in body["episodeIds"]:
                fake.episodes[eid]["monitored"] = body["monitored"]
            return {}

        @api.get(p + "/episodefile")
        def episode_files(seriesId: int) -> list:  # noqa: N803
            return [f for f in fake.episode_files.values() if f["seriesId"] == seriesId]

        # movies ------------------------------------------------------------------------
        @api.get(p + "/movie")
        def movies() -> list:
            return list(fake.movies.values())

        @api.get(p + "/movie/lookup/tmdb")
        def movie_lookup_tmdb(tmdbId: int) -> dict:  # noqa: N803
            for m in fake.movies.values():
                if m["tmdbId"] == tmdbId:
                    return m
            m = make_movie(0, f"Film {tmdbId}", tmdbId)
            m.pop("id")
            return m

        @api.get(p + "/movie/lookup")
        def movie_lookup(term: str) -> list:
            return [m for m in fake.movies.values() if term.lower() in m["title"].lower()]

        @api.get(p + "/movie/{mid}")
        def movie_get(mid: int) -> dict:
            if mid not in fake.movies:
                raise HTTPException(404, "Movie not found")
            return fake.movies[mid]

        @api.post(p + "/movie", status_code=201)
        async def movie_add(request: Request) -> dict:
            body = await request.json()
            body["id"] = fake.next_id()
            fake.movies[body["id"]] = body
            return body

        @api.put(p + "/movie/{mid}", status_code=202)
        async def movie_put(mid: int, request: Request) -> dict:
            body = await request.json()
            fake.movies[mid] = body
            return body

        @api.delete(p + "/movie/{mid}")
        def movie_delete(mid: int) -> dict:
            fake.movies.pop(mid, None)
            return {}

        # wanted / calendar / queue / history ------------------------------------------
        @api.get(p + "/wanted/missing")
        def wanted_missing(request: Request) -> dict:
            now = iso()
            if fake.app == "sonarr":
                recs = [e for e in fake.episodes.values() if e["monitored"] and not e["hasFile"]
                        and e.get("airDateUtc") and e["airDateUtc"] <= now and
                        fake.series[e["seriesId"]]["monitored"]]
            else:
                recs = [m for m in fake.movies.values() if m["monitored"] and not m["hasFile"]
                        and m["isAvailable"]]
            return paged(recs, request, "episodes.airDateUtc")

        @api.get(p + "/wanted/cutoff")
        def wanted_cutoff(request: Request) -> dict:
            return paged([], request)

        @api.get(p + "/calendar")
        def calendar(start: str, end: str) -> list:
            return [e for e in fake.episodes.values()
                    if e.get("airDateUtc") and start <= e["airDateUtc"] < end]

        @api.get(p + "/queue")
        def queue(request: Request) -> dict:
            return paged(fake.queue, request, "timeleft")

        @api.delete(p + "/queue/{qid}")
        def queue_delete(qid: int) -> dict:
            fake.queue = [q for q in fake.queue if q["id"] != qid]
            return {}

        @api.get(p + "/history")
        def history(request: Request) -> dict:
            recs = fake.history
            eid = request.query_params.get("episodeId")
            if eid:
                recs = [h for h in recs if h.get("episodeId") == int(eid)]
            return paged(recs, request, "date")

        @api.get(p + "/history/movie")
        def history_movie(movieId: int) -> list:  # noqa: N803
            return [h for h in fake.history if h.get("movieId") == movieId]

        # releases ----------------------------------------------------------------------
        @api.get(p + "/release")
        def release(request: Request) -> list:
            q = request.query_params
            if q.get("episodeId"):
                return fake.releases.get(("episode", int(q["episodeId"])), [])
            if q.get("movieId"):
                return fake.releases.get(("movie", int(q["movieId"])), [])
            return []

        @api.post(p + "/release")
        async def release_grab(request: Request) -> dict:
            body = await request.json()
            fake.grabs.append(body)
            return {"guid": body["guid"], "approved": True}

        # providers --------------------------------------------------------------------
        for kind in ("indexer", "downloadclient", "notification", "importlist"):
            fake._provider_routes(api, p, kind)

        @api.get(p + "/indexerstatus")
        def indexer_status() -> list:
            return fake.indexer_status

        @api.get(p + "/downloadclientstatus")
        def dc_status() -> list:
            return []

        # config resources --------------------------------------------------------------
        @api.get(p + "/rootfolder")
        def roots() -> list:
            return fake.root_folders

        @api.post(p + "/rootfolder", status_code=201)
        async def root_add(request: Request) -> dict:
            body = await request.json()
            rec = {"id": fake.next_id(), "path": body["path"], "accessible": True,
                   "freeSpace": 1, "unmappedFolders": []}
            fake.root_folders.append(rec)
            return rec

        @api.delete(p + "/rootfolder/{fid}")
        def root_delete(fid: int) -> dict:
            fake.root_folders = [r for r in fake.root_folders if r["id"] != fid]
            return {}

        @api.get(p + "/qualityprofile")
        def qps() -> list:
            return fake.quality_profiles

        @api.get(p + "/languageprofile")
        def lps() -> Any:
            if not fake.language_profiles:
                raise HTTPException(404, "Not Found")
            return [{"id": 1, "name": "English", "upgradeAllowed": False}]

        @api.get(p + "/customformat")
        def cfs() -> list:
            return [{"id": 1, "name": "Dual Audio", "includeCustomFormatWhenRenaming": False,
                     "specifications": []}]

        @api.get(p + "/tag")
        def tags() -> list:
            return list(fake.tags.values())

        @api.post(p + "/tag", status_code=201)
        async def tag_add(request: Request) -> dict:
            body = await request.json()
            rec = {"id": fake.next_id(), "label": body["label"]}
            fake.tags[rec["id"]] = rec
            return rec

        @api.put(p + "/tag/{tid}", status_code=202)
        async def tag_put(tid: int, request: Request) -> dict:
            fake.tags[tid] = await request.json()
            return fake.tags[tid]

        @api.delete(p + "/tag/{tid}")
        def tag_delete(tid: int) -> dict:
            fake.tags.pop(tid, None)
            return {}

        @api.get(p + "/remotepathmapping")
        def rpm() -> list:
            return list(fake.rpm.values())

        @api.post(p + "/remotepathmapping", status_code=201)
        async def rpm_add(request: Request) -> dict:
            body = await request.json()
            body["id"] = fake.next_id()
            fake.rpm[body["id"]] = body
            return body

        @api.put(p + "/remotepathmapping/{rid}", status_code=202)
        async def rpm_put(rid: int, request: Request) -> dict:
            fake.rpm[rid] = await request.json()
            return fake.rpm[rid]

        @api.delete(p + "/remotepathmapping/{rid}")
        def rpm_delete(rid: int) -> dict:
            fake.rpm.pop(rid, None)
            return {}

        @api.get(p + "/manualimport")
        def manual_import() -> list:
            return fake.manual_import

        # commands ----------------------------------------------------------------------
        @api.post(p + "/command", status_code=201)
        async def command(request: Request) -> dict:
            body = await request.json()
            cid = fake.next_id()
            cmd = {"id": cid, "name": body["name"], "commandName": body["name"],
                   "body": body, "priority": "normal", "status": "queued",
                   "queued": iso(), "trigger": "manual"}
            fake.commands[cid] = cmd
            if fake.on_command is not None:
                fake.on_command(cmd)
            cmd["status"] = fake.command_final_status
            return {**cmd, "status": "queued"}

        @api.get(p + "/command/{cid}")
        def command_get(cid: int) -> dict:
            if cid not in fake.commands:
                raise HTTPException(404, "not found")
            return fake.commands[cid]

        @api.get(p + "/command")
        def command_list() -> list:
            return [c for c in fake.commands.values() if c["status"] in ("queued", "started")]

        return api

    def _provider_routes(self, api: FastAPI, p: str, kind: str) -> None:
        fake = self
        store = self.providers[kind]

        @api.get(f"{p}/{kind}/schema", name=f"{kind}_schema")
        def schema() -> list:
            if kind == "notification":
                return [copy.deepcopy(WEBHOOK_SCHEMA)]
            if kind == "indexer":
                return [{**copy.deepcopy(INDEXER_TEMPLATE), "fields": indexer(0, "x")["fields"]}]
            return []

        @api.get(f"{p}/{kind}", name=f"{kind}_list")
        def lst() -> list:
            return list(store.values())

        @api.get(f"{p}/{kind}/{{item_id}}", name=f"{kind}_get")
        def get(item_id: int) -> dict:
            if item_id not in store:
                raise HTTPException(404, "NotFound")
            return store[item_id]

        @api.post(f"{p}/{kind}/test", name=f"{kind}_test")
        async def test(request: Request) -> dict:
            body = await request.json()
            api_key = next((f.get("value") for f in body.get("fields", [])
                            if f.get("name") == "apiKey"), "ok")
            if api_key in (None, "", "********"):
                return JSONResponse(status_code=400, content=[{
                    "propertyName": "ApiKey", "errorMessage": "Invalid API Key",
                    "severity": "error"}])
            return {}

        @api.post(f"{p}/{kind}", status_code=201, name=f"{kind}_create")
        async def create(request: Request) -> dict:
            body = await request.json()
            body["id"] = fake.next_id()
            store[body["id"]] = body
            return body

        @api.put(f"{p}/{kind}/{{item_id}}", status_code=202, name=f"{kind}_put")
        async def put(item_id: int, request: Request) -> dict:
            body = await request.json()
            if item_id not in store:
                raise HTTPException(404, "NotFound")
            store[item_id] = body
            return body

        @api.delete(f"{p}/{kind}/{{item_id}}", name=f"{kind}_delete")
        def delete(item_id: int) -> dict:
            store.pop(item_id, None)
            return {}


class FakeSeerr:
    def __init__(self, api_key: str = "seerr-key-123456") -> None:
        self.api_key = api_key
        self.servers: dict[str, list[dict[str, Any]]] = {"sonarr": [], "radarr": []}
        self.requests: list[dict[str, Any]] = []
        self.test_results: dict[str, bool] = {}
        self.calls: list[tuple[str, str, Any]] = []
        # catalogus voor zoeken/ontdekken/details (sleutel: (mediaType, tmdbId))
        self.catalog: dict[tuple[str, int], dict[str, Any]] = {}
        self.request_status = 2  # 1 = wacht op goedkeuring, 2 = goedgekeurd
        self.asgi = self._build()

    def transport(self) -> httpx.ASGITransport:
        return httpx.ASGITransport(app=self.asgi)

    def _build(self) -> FastAPI:
        api = FastAPI()
        fake = self

        @api.middleware("http")
        async def auth(request: Request, call_next):  # type: ignore[no-untyped-def]
            body = None
            if request.method in ("POST", "PUT"):
                import json as _json
                raw = await request.body()
                body = _json.loads(raw) if raw else None
            fake.calls.append((request.method, request.url.path, body))
            if request.headers.get("X-Api-Key") != fake.api_key:
                return JSONResponse(status_code=403, content={"message": "Forbidden"})
            return await call_next(request)

        @api.get("/api/v1/status")
        def status() -> dict:
            return {"version": "2.7.3", "commitTag": "v2.7.3", "updateAvailable": False,
                    "commitsBehind": 0, "restartRequired": False}

        @api.get("/api/v1/settings/{kind}")
        def servers(kind: str) -> list:
            return fake.servers[kind]

        @api.post("/api/v1/settings/{kind}/test")
        async def test(kind: str, request: Request) -> Any:
            body = await request.json()
            key = f"{body.get('hostname')}:{body.get('port')}"
            if not fake.test_results.get(key, True):
                return JSONResponse(status_code=500, content={
                    "message": f"Failed to connect to {kind}"})
            return {"profiles": [{"id": 1, "name": "HD-1080p"}],
                    "rootFolders": [{"id": 1, "path": "/tv/"}], "tags": [], "urlBase": ""}

        def _strict(body: dict) -> JSONResponse | None:
            # zoals echte Seerr 3.5.1 (OpenAPI-validatie): id read-only, geen nulls
            if "id" in body:
                return JSONResponse(status_code=400,
                                    content={"message": "request/body/id is read-only"})
            nulls = [k for k, v in body.items() if v is None]
            if nulls:
                return JSONResponse(status_code=400, content={
                    "message": f"request/body/{nulls[0]} must be number"})
            return None

        @api.put("/api/v1/settings/{kind}/{sid}")
        async def update(kind: str, sid: int, request: Request) -> Any:
            body = await request.json()
            if (err := _strict(body)) is not None:
                return err
            for i, s in enumerate(fake.servers[kind]):
                if s["id"] == sid:
                    fake.servers[kind][i] = {**body, "id": sid}
                    return fake.servers[kind][i]
            raise HTTPException(404, "not found")

        @api.post("/api/v1/settings/{kind}", status_code=201)
        async def create(kind: str, request: Request) -> Any:
            body = await request.json()
            if (err := _strict(body)) is not None:
                return err
            required = ["name", "hostname", "port", "apiKey", "activeProfileId",
                        "activeProfileName", "activeDirectory"]
            required += ["minimumAvailability"] if kind == "radarr" else \
                ["enableSeasonFolders"]
            missing = [k for k in required if k not in body]
            if missing:
                return JSONResponse(status_code=400, content={
                    "message": f"request/body must have required property '{missing[0]}'"})
            body["id"] = max([s["id"] for s in fake.servers[kind]] + [-1]) + 1
            fake.servers[kind].append(body)
            return body

        @api.delete("/api/v1/settings/{kind}/{sid}")
        def delete(kind: str, sid: int) -> dict:
            fake.servers[kind] = [s for s in fake.servers[kind] if s["id"] != sid]
            return {}

        def _results(kind: str | None = None) -> list:
            return [{**v, "mediaType": k[0], "id": k[1]} for k, v in fake.catalog.items()
                    if kind is None or k[0] == kind]

        @api.get("/api/v1/search")
        def search(request: Request) -> Any:
            raw = str(request.url).split("query=", 1)[-1].split("&", 1)[0]
            if "+" in raw:  # zoals echte Seerr: spaties moeten %20 zijn
                return JSONResponse(status_code=400, content={"message": "bad query"})
            from urllib.parse import unquote
            q = unquote(raw).lower()
            hits = [r for r in _results()
                    if q in str(r.get("title") or r.get("name") or "").lower()]
            hits.append({"mediaType": "person", "id": 9, "name": q})
            return {"page": 1, "totalPages": 1, "totalResults": len(hits), "results": hits}

        @api.get("/api/v1/discover/{kind}")
        def discover(kind: str) -> dict:
            mt = {"movies": "movie", "tv": "tv"}.get(kind)
            res = _results(mt)
            if mt:
                for r in res:
                    r.pop("mediaType", None)
            return {"page": 1, "totalPages": 1, "results": res}

        @api.get("/api/v1/movie/{tmdb}")
        def movie(tmdb: int) -> dict:
            if ("movie", tmdb) not in fake.catalog:
                raise HTTPException(404, "not found")
            return {**fake.catalog[("movie", tmdb)], "id": tmdb}

        @api.get("/api/v1/tv/{tmdb}")
        def tv(tmdb: int) -> dict:
            if ("tv", tmdb) not in fake.catalog:
                raise HTTPException(404, "not found")
            return {**fake.catalog[("tv", tmdb)], "id": tmdb}

        @api.post("/api/v1/request", status_code=201)
        async def make_request(request: Request) -> Any:
            body = await request.json()
            if body.get("mediaType") == "tv" and not body.get("seasons"):
                return JSONResponse(status_code=400, content={"message": "seasons"})
            rid = len(fake.requests) + 1
            item = {"id": rid, "status": fake.request_status, "type": body["mediaType"],
                    "createdAt": "2026-11-08T08:00:00Z",
                    "requestedBy": {"displayName": "jan"},
                    "seasons": [{"seasonNumber": n} for n in (
                        body.get("seasons") if isinstance(body.get("seasons"), list) else [])],
                    "media": {"tmdbId": body["mediaId"], "mediaType": body["mediaType"],
                              "status": 2}}
            fake.requests.append(item)
            return item

        @api.get("/api/v1/request/{rid}")
        def one_request(rid: int) -> dict:
            for r in fake.requests:
                if int(r["id"]) == rid:
                    return r
            raise HTTPException(404, "Request not found.")

        @api.get("/api/v1/request")
        def requests(take: int = 10, skip: int = 0) -> dict:
            items = fake.requests[skip:skip + take]
            return {"pageInfo": {"pages": max(1, -(-len(fake.requests) // take)),
                                 "pageSize": take, "results": len(fake.requests),
                                 "page": skip // take + 1}, "results": items}

        return api


def seerr_server(sid: int, name: str, host: str, port: int, api_key: str, *,
                 is_default: bool, is4k: bool = False, profile: int = 1,
                 directory: str = "/tv/", kind: str = "sonarr") -> dict[str, Any]:
    server = {"id": sid, "name": name, "hostname": host, "port": port, "apiKey": api_key,
              "useSsl": False, "baseUrl": "", "activeProfileId": profile,
              "activeProfileName": "HD-1080p", "activeDirectory": directory, "is4k": is4k,
              "isDefault": is_default, "externalUrl": "", "syncEnabled": False,
              "preventSearch": False, "tags": []}
    if kind == "sonarr":
        server.update(activeAnimeProfileId=profile, activeAnimeDirectory=directory,
                      activeLanguageProfileId=None, enableSeasonFolders=True,
                      seriesType="standard", animeSeriesType="anime", animeTags=[])
    else:
        server["minimumAvailability"] = "released"
    return server


# -- koppelen aan een MediaGuard-context ----------------------------------------------------
def wire(ctx: Any, instance_id: str, fake: FakeArr) -> FakeArr:
    ctx.services.setdefault("arr_transports", {})[instance_id] = fake.transport()
    ctx.services["arr_client_options"] = {"retries": 0, "backoff": 0, "timeout": 5}
    ctx.secrets.set(apikey_secret(instance_id), fake.api_key, "test")
    return fake


@pytest.fixture
def b1ctx(ctx):  # type: ignore[no-untyped-def]
    """Context met de vier standaardinstanties en snelle backfill-instellingen."""
    seed_instances(ctx)
    ctx.config.set("global", "", "backfill.command_wait_seconds", 0, "test")
    return ctx


def test_fake_requires_api_key():
    import asyncio

    from mediaguard.arr.client import ArrAuthError, ArrClient

    fake = FakeArr("sonarr")

    async def run() -> None:
        good = ArrClient("http://x", fake.api_key, instance_id="t", app="sonarr",
                         transport=fake.transport(), retries=0)
        assert (await good.system_status())["appName"] == "Sonarr"
        bad = ArrClient("http://x", "wrong", instance_id="t", app="sonarr",
                        transport=fake.transport(), retries=0)
        with pytest.raises(ArrAuthError):
            await bad.system_status()

    asyncio.run(run())
