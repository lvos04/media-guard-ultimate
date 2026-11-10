"""Fakes voor de inrichtingstests: Sonarr/Radarr met de configuratie-API's die MediaGuard
inricht (profielschema, custom formats, downloadclient-/indexerschema's, filesystem,
series/movie-editor, rename) en een fake Prowlarr. Bouwt voort op ``MigFakeArr``."""

from __future__ import annotations

import copy
from typing import Any

import httpx
from fastapi import APIRouter, FastAPI, HTTPException, Request
from fastapi.responses import JSONResponse
from test_arr_fakes import FakeSeerr, wire
from test_migration_fakes import MigFakeArr

SONARR_LADDER: list[Any] = [
    (1, "SDTV"), (4, "HDTV-720p"), ("WEB 720p", [(14, "WEBRip-720p"), (5, "WEBDL-720p")]),
    (6, "Bluray-720p"), (9, "HDTV-1080p"),
    ("WEB 1080p", [(15, "WEBRip-1080p"), (3, "WEBDL-1080p")]), (7, "Bluray-1080p"),
    (20, "Bluray-1080p Remux"), (16, "HDTV-2160p"),
    ("WEB 2160p", [(17, "WEBRip-2160p"), (18, "WEBDL-2160p")]), (19, "Bluray-2160p"),
    (21, "Bluray-2160p Remux")]
RADARR_LADDER: list[Any] = [
    (1, "SDTV"), (4, "HDTV-720p"), ("WEB 720p", [(14, "WEBRip-720p"), (5, "WEBDL-720p")]),
    (6, "Bluray-720p"), (9, "HDTV-1080p"),
    ("WEB 1080p", [(15, "WEBRip-1080p"), (3, "WEBDL-1080p")]), (7, "Bluray-1080p"),
    (30, "Remux-1080p"), (16, "HDTV-2160p"),
    ("WEB 2160p", [(17, "WEBRip-2160p"), (18, "WEBDL-2160p")]), (19, "Bluray-2160p"),
    (31, "Remux-2160p"), (22, "BR-DISK")]


def _items(ladder: list[Any]) -> list[dict[str, Any]]:
    out: list[dict[str, Any]] = []
    gid = 1000
    for entry in ladder:
        if isinstance(entry[1], list):
            gid += 1
            out.append({"id": gid, "name": entry[0], "allowed": False, "items": [
                {"quality": {"id": q, "name": n}, "items": [], "allowed": False}
                for q, n in entry[1]]})
        else:
            out.append({"quality": {"id": entry[0], "name": entry[1]}, "items": [],
                        "allowed": False})
    return out


def quality_names(ladder: list[Any]) -> list[str]:
    out = []
    for e in ladder:
        out += [n for _, n in e[1]] if isinstance(e[1], list) else [e[1]]
    return out


def _f(name: str, value: Any = None, privacy: str = "normal") -> dict[str, Any]:
    return {"name": name, "value": value, "privacy": privacy, "label": name}


def dc_schema(app: str) -> list[dict[str, Any]]:
    cat = "tvCategory" if app == "sonarr" else "movieCategory"
    out = []
    for impl, proto, extra in (
            ("Sabnzbd", "usenet", [_f("apiKey", None, "apiKey")]),
            ("Nzbget", "usenet", [_f("username"), _f("password", None, "password")]),
            ("QBittorrent", "torrent", [_f("username"), _f("password", None, "password")]),
            ("Transmission", "torrent", [_f("username"), _f("password", None, "password")]),
            ("Deluge", "torrent", [_f("password", None, "password")])):
        out.append({"name": "", "implementation": impl, "implementationName": impl,
                    "configContract": f"{impl}Settings", "protocol": proto, "enable": True,
                    "priority": 1, "removeCompletedDownloads": True,
                    "removeFailedDownloads": True, "tags": [], "presets": [],
                    "fields": [_f("host", "localhost"), _f("port", 8080), _f("useSsl", False),
                               _f("urlBase", ""), *extra, _f(cat, app)]})
    return out


def indexer_schema(app: str) -> list[dict[str, Any]]:
    out = []
    for impl, proto in (("Newznab", "usenet"), ("Torznab", "torrent")):
        fields = [_f("baseUrl"), _f("apiPath", "/api"), _f("apiKey", None, "apiKey"),
                  _f("categories", [])]
        if app == "sonarr":
            fields.append(_f("animeCategories", []))
        if impl == "Torznab":
            fields.append(_f("minimumSeeders", 1))
        out.append({"name": "", "implementation": impl, "implementationName": impl,
                    "configContract": f"{impl}Settings", "protocol": proto,
                    "enableRss": True, "enableAutomaticSearch": True,
                    "enableInteractiveSearch": True, "supportsRss": True,
                    "supportsSearch": True, "priority": 25, "downloadClientId": 0, "tags": [],
                    "presets": [{"name": "preset"}], "fields": fields})
    return out


class ProvFakeArr(MigFakeArr):
    def __init__(self, app: str, *, paths: set[str] | None = None, **kw: Any) -> None:
        self.ladder = SONARR_LADDER if app == "sonarr" else RADARR_LADDER
        self.existing_paths: set[str] = set(paths or {
            "/data/media/tv", "/data/media/anime", "/data/media/movies", "/data/media/animefilms"})
        self.rename_preview: dict[int, list[dict[str, Any]]] = {}
        super().__init__(app, **kw)
        self.root_folders = []
        self.custom_formats = []
        self.quality_profiles = [{"id": 1, "name": "Any", "upgradeAllowed": False,
                                  "cutoff": 1, "items": self._ladder_items(allowed={"SDTV"}),
                                  "formatItems": [], "minFormatScore": 0,
                                  "cutoffFormatScore": 0}]
        if app == "radarr":
            self.quality_profiles[0]["language"] = {"id": 1, "name": "English"}
        self.configs["mediamanagement"] = {
            "id": 1, "recycleBin": "", "deleteEmptyFolders": False,
            "copyUsingHardlinks": False, "downloadPropersAndRepacks": "preferAndUpgrade",
            "importExtraFiles": False, "extraFileExtensions": "srt",
            ("autoUnmonitorPreviouslyDownloadedEpisodes" if app == "sonarr" else
             "autoUnmonitorPreviouslyDownloadedMovies"): False}
        if app == "sonarr":
            self.configs["naming"] = {"id": 1, "renameEpisodes": False,
                                      "replaceIllegalCharacters": True,
                                      "standardEpisodeFormat": "{Series Title} - S{season:00}",
                                      "dailyEpisodeFormat": "x", "animeEpisodeFormat": "y",
                                      "seriesFolderFormat": "{Series Title}",
                                      "seasonFolderFormat": "Season {season}",
                                      "specialsFolderFormat": "Specials",
                                      "multiEpisodeStyle": 0, "colonReplacementFormat": 4}
        else:
            self.configs["naming"] = {"id": 1, "renameMovies": False,
                                      "replaceIllegalCharacters": True,
                                      "standardMovieFormat": "{Movie Title}",
                                      "movieFolderFormat": "{Movie Title} ({Release Year})",
                                      "colonReplacementFormat": "delete"}
        self.quality_definitions = [
            {"id": i + 1, "quality": {"id": i + 1, "name": n}, "title": n, "minSize": 1,
             "maxSize": 200, "preferredSize": 150} for i, n in enumerate(
                quality_names(self.ladder))]

    def _ladder_items(self, allowed: set[str]) -> list[dict[str, Any]]:
        items = _items(self.ladder)
        for it in items:
            names = [it["quality"]["name"]] if it.get("quality") else \
                [s["quality"]["name"] for s in it["items"]]
            on = any(n in allowed for n in names)
            it["allowed"] = on
            for s in it.get("items") or []:
                s["allowed"] = on
        return items

    def profile(self, name: str) -> dict[str, Any] | None:
        return next((p for p in self.quality_profiles if p["name"] == name), None)

    def _build(self):  # type: ignore[no-untyped-def]  # noqa: C901
        api = super()._build()
        fake = self
        r = APIRouter()
        p = "/api/v3"

        @r.get(p + "/qualityprofile/schema")
        def qp_schema() -> dict:
            return {"name": "", "upgradeAllowed": False, "cutoff": 0,
                    "items": _items(fake.ladder), "minFormatScore": 0,
                    "cutoffFormatScore": 0, "minUpgradeFormatScore": 1,
                    "formatItems": [{"format": c["id"], "name": c["name"], "score": 0}
                                    for c in fake.custom_formats],
                    **({"language": {"id": 1, "name": "English"}} if fake.app == "radarr"
                       else {})}

        @r.get(p + "/qualityprofile/{qid}")
        def qp_get(qid: int) -> dict:
            prof = next((q for q in fake.quality_profiles if q["id"] == qid), None)
            if prof is None:
                raise HTTPException(404, "nf")
            return prof

        @r.delete(p + "/qualityprofile/{qid}")
        def qp_delete(qid: int) -> Any:
            used = [i for i in list(fake.series.values()) + list(fake.movies.values())
                    if i.get("qualityProfileId") == qid]
            if used:
                return JSONResponse(status_code=400, content={"message": "Profile in use"})
            fake.quality_profiles = [q for q in fake.quality_profiles if q["id"] != qid]
            return {}

        @r.put(p + "/customformat/{cid}", status_code=202)
        async def cf_put(cid: int, request: Request) -> dict:
            body = await request.json()
            fake.custom_formats = [body if c["id"] == cid else c for c in fake.custom_formats]
            return body

        @r.delete(p + "/customformat/{cid}")
        def cf_delete(cid: int) -> dict:
            fake.custom_formats = [c for c in fake.custom_formats if c["id"] != cid]
            for q in fake.quality_profiles:
                q["formatItems"] = [f for f in q.get("formatItems") or [] if f["format"] != cid]
            return {}

        @r.get(p + "/downloadclient/schema")
        def dcs() -> list:
            return dc_schema(fake.app)

        @r.get(p + "/indexer/schema")
        def ixs() -> list:
            return indexer_schema(fake.app)

        @r.get(p + "/filesystem")
        def filesystem(path: str) -> dict:
            clean = path.rstrip("/")
            if clean in fake.existing_paths:
                return {"parent": clean.rsplit("/", 1)[0] + "/", "directories": [],
                        "files": []}
            kids = [{"path": x + "/", "name": x.rsplit("/", 1)[1], "type": "folder"}
                    for x in fake.existing_paths if x.rsplit("/", 1)[0] == clean]
            return {"directories": kids, "files": []}

        @r.post(p + "/rootfolder", status_code=201)
        async def root_add(request: Request) -> Any:
            body = await request.json()
            if body["path"].rstrip("/") not in fake.existing_paths:
                return JSONResponse(status_code=400, content=[{
                    "propertyName": "Path", "errorMessage": "Path does not exist"}])
            rec = {"id": fake.next_id(), "path": body["path"], "accessible": True,
                   "freeSpace": 1, "unmappedFolders": []}
            fake.root_folders.append(rec)
            return rec

        @r.put(p + "/series/editor", status_code=202)
        async def series_editor(request: Request) -> list:
            body = await request.json()
            for sid in body["seriesIds"]:
                if "qualityProfileId" in body:
                    fake.series[sid]["qualityProfileId"] = body["qualityProfileId"]
            return [fake.series[s] for s in body["seriesIds"]]

        @r.put(p + "/movie/editor", status_code=202)
        async def movie_editor(request: Request) -> list:
            body = await request.json()
            for mid in body["movieIds"]:
                if "qualityProfileId" in body:
                    fake.movies[mid]["qualityProfileId"] = body["qualityProfileId"]
            return [fake.movies[m] for m in body["movieIds"]]

        @r.get(p + "/rename")
        def rename(request: Request) -> list:
            q = request.query_params
            key = int(q.get("seriesId") or q.get("movieId") or 0)
            return fake.rename_preview.get(key, [])

        @r.delete(p + "/delayprofile/{did}")
        def delay_delete(did: int) -> dict:
            fake.delay_profiles = [d for d in fake.delay_profiles if d["id"] != did]
            return {}

        api.router.routes[0:0] = r.routes
        return api


class FakeProwlarr:
    def __init__(self, api_key: str = "prowlarr-key-123") -> None:
        self.api_key = api_key
        self.apps: dict[int, dict[str, Any]] = {}
        self.tags: dict[int, dict[str, Any]] = {}
        self.indexers: dict[int, dict[str, Any]] = {}
        self.commands: list[dict[str, Any]] = []
        self.calls: list[tuple[str, str, Any]] = []
        self._id = 100
        self.asgi = self._build()

    def nid(self) -> int:
        self._id += 1
        return self._id

    def transport(self) -> httpx.ASGITransport:
        return httpx.ASGITransport(app=self.asgi)

    def _build(self) -> FastAPI:  # noqa: C901
        api = FastAPI()
        fake = self
        p = "/api/v1"

        @api.middleware("http")
        async def auth(request: Request, call_next):  # type: ignore[no-untyped-def]
            body = None
            if request.method in ("POST", "PUT"):
                import json as _json
                raw = await request.body()
                body = _json.loads(raw) if raw else None
            fake.calls.append((request.method, request.url.path, body))
            if request.headers.get("X-Api-Key") != fake.api_key:
                return JSONResponse(status_code=401, content={"message": "Unauthorized"})
            return await call_next(request)

        @api.get(p + "/system/status")
        def status() -> dict:
            return {"appName": "Prowlarr", "version": "1.30.2.4939"}

        @api.get(p + "/applications/schema")
        def schema() -> list:
            out = []
            for impl in ("Sonarr", "Radarr", "Lidarr"):
                fields = [{"name": "prowlarrUrl", "value": "http://localhost:9696"},
                          {"name": "baseUrl", "value": "http://localhost"},
                          {"name": "apiKey", "value": None, "privacy": "apiKey"},
                          {"name": "syncCategories", "value": []}]
                if impl == "Sonarr":
                    fields += [{"name": "animeSyncCategories", "value": []},
                               {"name": "syncAnimeStandardFormatSearch", "value": False}]
                out.append({"name": "", "implementation": impl, "implementationName": impl,
                            "configContract": f"{impl}Settings", "syncLevel": "fullSync",
                            "tags": [], "fields": fields, "presets": []})
            return out

        @api.get(p + "/applications")
        def apps() -> list:
            out = []
            for a in fake.apps.values():
                a = copy.deepcopy(a)
                for f in a["fields"]:
                    if f["name"] == "apiKey" and f.get("value"):
                        f["value"] = "********"
                out.append(a)
            return out

        @api.post(p + "/applications", status_code=201)
        async def app_add(request: Request) -> dict:
            body = await request.json()
            body["id"] = fake.nid()
            fake.apps[body["id"]] = body
            return body

        @api.put(p + "/applications/{aid}", status_code=202)
        async def app_put(aid: int, request: Request) -> dict:
            body = await request.json()
            if aid not in fake.apps:
                raise HTTPException(404, "nf")
            for f in body["fields"]:
                if f["name"] == "apiKey" and f.get("value") == "********":
                    f["value"] = next(x["value"] for x in fake.apps[aid]["fields"]
                                      if x["name"] == "apiKey")
            fake.apps[aid] = body
            return body

        @api.get(p + "/tag")
        def tags() -> list:
            return list(fake.tags.values())

        @api.post(p + "/tag", status_code=201)
        async def tag_add(request: Request) -> dict:
            body = await request.json()
            rec = {"id": fake.nid(), "label": body["label"]}
            fake.tags[rec["id"]] = rec
            return rec

        @api.get(p + "/indexer")
        def indexers() -> list:
            return list(fake.indexers.values())

        @api.put(p + "/indexer/{iid}", status_code=202)
        async def idx_put(iid: int, request: Request) -> dict:
            fake.indexers[iid] = await request.json()
            return fake.indexers[iid]

        @api.post(p + "/command", status_code=201)
        async def command(request: Request) -> dict:
            body = await request.json()
            fake.commands.append(body)
            return {"id": len(fake.commands), **body}

        return api


def wire_all(ctx: Any, **kw: Any) -> dict[str, ProvFakeArr]:
    fakes = {}
    for iid in ("sonarr-series", "sonarr-anime", "radarr-films", "radarr-animefilms"):
        app = "sonarr" if iid.startswith("sonarr") else "radarr"
        fakes[iid] = wire(ctx, iid, ProvFakeArr(app, api_key=f"key-{iid}-0001", **kw))
    return fakes


def test_prov_fake_schema_and_editor():
    import asyncio

    from mediaguard.arr.client import ArrClient

    fake = ProvFakeArr("sonarr")

    async def run() -> None:
        c = ArrClient("http://x", fake.api_key, instance_id="t", app="sonarr",
                      transport=fake.transport(), retries=0)
        schema = await c.get("qualityprofile/schema")
        assert any(it.get("name") == "WEB 1080p" for it in schema["items"])
        assert [s["implementation"] for s in await c.get("downloadclient/schema")][0] == \
            "Sabnzbd"
        fs = await c.request("GET", "filesystem", params={"path": "/data/media/tv/"})
        assert fs.get("parent")

    asyncio.run(run())
    assert isinstance(FakeSeerr(), FakeSeerr)
