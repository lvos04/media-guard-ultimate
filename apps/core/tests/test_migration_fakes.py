"""Uitbreiding van de B1-fakes voor de migratiewizard: native back-ups (echte SQLite in een
zip, met Sonarr/Radarr-kolomnamen), restore-upload, herstart, configsecties, custom
formats, delay/release-profielen, diskspace/filesystem en metadata-catalogus voor
nieuw toegevoegde series. Andere migratietests importeren deze module."""

from __future__ import annotations

import copy
import io
import itertools
import json
import os
import sqlite3
import tempfile
import zipfile
from typing import Any

from fastapi import APIRouter, HTTPException, Request
from test_arr_fakes import FakeArr, download_client, indexer, iso, make_episode, make_series

MASK = "********"
SEQ = itertools.count(1)


class SeqList(list):
    """Gedeelde, oplopende volgorde over ALLE fakes heen (voor ordeningstests)."""

    def append(self, item: Any) -> None:  # type: ignore[override]
        super().append((*item, next(SEQ)))
SERIES_TYPE = {"standard": 0, "daily": 1, "anime": 2}
NEW_ITEMS = {"all": 0, "none": 1}
MIN_AVAIL = {"tba": 0, "announced": 1, "inCinemas": 2, "released": 3, "preDB": 4}


def _settings(resource: dict[str, Any]) -> str:
    return json.dumps({f["name"]: f.get("value") for f in resource.get("fields") or []})


def _fields(settings: str) -> list[dict[str, Any]]:
    data = json.loads(settings or "{}")
    return [{"name": k, "value": v,
             "privacy": "apiKey" if k.lower() in ("apikey", "password") else "normal"}
            for k, v in data.items()]


class MigFakeArr(FakeArr):
    def __init__(self, app: str, *, mask_secrets: bool = False, check_disk: bool = False,
                 **kw: Any) -> None:
        self.mask_secrets = mask_secrets
        self.check_disk = check_disk
        self.custom_formats: list[dict[str, Any]] = [{"id": 1, "name": "Dual Audio",
                                                      "specifications": []}]
        self.delay_profiles: list[dict[str, Any]] = [{"id": 1, "enableUsenet": True,
                                                      "enableTorrent": True,
                                                      "preferredProtocol": "usenet",
                                                      "usenetDelay": 0, "torrentDelay": 0,
                                                      "order": 2147483647, "tags": []}]
        self.release_profiles: list[dict[str, Any]] = []
        self.configs: dict[str, dict[str, Any]] = {
            "indexer": {"id": 1, "rssSyncInterval": 15, "minimumAge": 0},
            "naming": {"id": 1, "renameEpisodes": False,
                       "standardEpisodeFormat": "{Series Title} - S{season:00}E{episode:00}"},
            "mediamanagement": {"id": 1, "recycleBin": "", "deleteEmptyFolders": False}}
        self.quality_definitions = [{"id": 1, "quality": {"id": 3, "name": "WEBDL-1080p"},
                                     "title": "WEBDL-1080p", "minSize": 0, "maxSize": 100}]
        self.backup_list: list[dict[str, Any]] = []
        self.backup_blobs: dict[str, bytes] = {}
        self.catalog: dict[int, list[dict[str, Any]]] = {}
        self.restores: list[bytes] = []
        self.restarts = 0
        self.history_since_records: list[dict[str, Any]] = []
        self.sticky_rss: set[int] = set()  # simuleert een oude instantie die 'freeze' negeert
        super().__init__(app, **kw)
        self.calls = SeqList()
        self.on_command = self._command_hook

    def writes(self) -> list[tuple[Any, ...]]:
        return [c for c in self.calls if c[0] not in ("GET", "HEAD")]

    # -- commando's ------------------------------------------------------------------------
    def _command_hook(self, cmd: dict[str, Any]) -> None:
        if cmd["name"] == "Backup":
            self.add_backup("manual")
        if cmd["name"] == "RescanSeries" and self.check_disk:
            for s in self.series.values():
                if not os.path.isdir(s["path"]):
                    for e in self.episodes.values():
                        if e["seriesId"] == s["id"]:
                            e["hasFile"] = False
                            e["episodeFileId"] = 0
        if cmd["name"] == "RescanMovie" and self.check_disk:
            for m in self.movies.values():
                if not os.path.isdir(m.get("path", "")):
                    m["hasFile"] = False

    def add_backup(self, kind: str = "scheduled") -> dict[str, Any]:
        n = len(self.backup_list) + 1
        name = f"{self.app}_backup_v{self.version}_2026.10.{n:02d}_10.00.00.zip"
        entry = {"id": n, "name": name, "path": f"/backup/{kind}/{name}", "type": kind,
                 "size": 0, "time": iso(-1 + n / 100)}
        blob = make_backup_zip(self)
        entry["size"] = len(blob)
        self.backup_blobs[name] = blob
        self.backup_list.append(entry)
        return entry

    # -- extra routes (vóór de B1-routes geregistreerd) -----------------------------------
    def _build(self):  # type: ignore[no-untyped-def]  # noqa: C901
        api = super()._build()
        fake = self
        r = APIRouter()
        p = "/api/v3"

        @r.get(p + "/system/backup")
        def backups() -> list:
            return fake.backup_list

        @r.get("/backup/{kind}/{name}")
        def backup_file(kind: str, name: str):  # type: ignore[no-untyped-def]
            from fastapi.responses import Response

            if name not in fake.backup_blobs:
                raise HTTPException(404, "not found")
            return Response(fake.backup_blobs[name], media_type="application/zip")

        @r.post(p + "/system/backup/restore/upload")
        async def restore(request: Request) -> dict:
            form = await request.form()
            upload = form.get("restore")
            if upload is None:
                raise HTTPException(400, "no file")
            data = await upload.read()  # type: ignore[union-attr]
            fake.restores.append(data)
            load_backup_zip(fake, data)
            return {"restartRequired": True}

        @r.post(p + "/system/restart")
        def restart() -> dict:
            fake.restarts += 1
            return {}

        @r.get(p + "/diskspace")
        def diskspace() -> list:
            return [{"path": "/", "label": "root", "freeSpace": 5 * 10**11,
                     "totalSpace": 10**12}]

        @r.get(p + "/filesystem")
        def filesystem(path: str) -> dict:
            if os.path.isdir(path) or path.rstrip("/") in {
                    str(x["path"]).rstrip("/") for x in fake.root_folders}:
                return {"parent": os.path.dirname(path.rstrip("/")), "directories": [],
                        "files": []}
            return {"directories": [], "files": []}

        @r.get(p + "/customformat")
        def cfs() -> list:
            return fake.custom_formats

        @r.post(p + "/customformat", status_code=201)
        async def cf_add(request: Request) -> dict:
            body = await request.json()
            body["id"] = fake.next_id()
            fake.custom_formats.append(body)
            return body

        @r.post(p + "/qualityprofile", status_code=201)
        async def qp_add(request: Request) -> dict:
            body = await request.json()
            body["id"] = fake.next_id()
            fake.quality_profiles.append(body)
            return body

        @r.put(p + "/qualityprofile/{qid}", status_code=202)
        async def qp_put(qid: int, request: Request) -> dict:
            body = await request.json()
            fake.quality_profiles = [body if q["id"] == qid else q for q in fake.quality_profiles]
            return body

        for name, store in (("delayprofile", "delay_profiles"),
                            ("releaseprofile", "release_profiles")):
            self._list_routes(r, p, name, store)

        @r.get(p + "/config/{name}")
        def config_get(name: str) -> dict:
            if name not in fake.configs:
                raise HTTPException(404, "nf")
            return fake.configs[name]

        @r.put(p + "/config/{name}", status_code=202)
        async def config_put(name: str, request: Request) -> dict:
            fake.configs[name] = await request.json()
            return fake.configs[name]

        @r.get(p + "/qualitydefinition")
        def qd() -> list:
            return fake.quality_definitions

        @r.put(p + "/qualitydefinition/update", status_code=202)
        async def qd_put(request: Request) -> list:
            fake.quality_definitions = await request.json()
            return fake.quality_definitions

        @r.post(p + "/series", status_code=201)
        async def series_add(request: Request) -> dict:
            body = await request.json()
            sid = fake.next_id()
            body["id"] = sid
            body.setdefault("seasons", [{"seasonNumber": 1, "monitored": True}])
            fake.series[sid] = body
            season_mon = {x["seasonNumber"]: x["monitored"] for x in body["seasons"]}
            for tmpl in fake.catalog.get(int(body["tvdbId"]), []):
                eid = fake.next_id()
                ep = {**copy.deepcopy(tmpl), "id": eid, "seriesId": sid, "hasFile": False,
                      "episodeFileId": 0}
                ep["monitored"] = bool(body["monitored"]) and season_mon.get(
                    tmpl["seasonNumber"], False)
                fake.episodes[eid] = ep
            return body

        @r.put(p + "/indexer/{item_id}", status_code=202)
        async def indexer_put(item_id: int, request: Request) -> dict:
            body = await request.json()
            if item_id not in fake.providers["indexer"]:
                raise HTTPException(404, "NotFound")
            if item_id in fake.sticky_rss:
                body["enableRss"] = True
            fake.providers["indexer"][item_id] = body
            return body

        @r.post(p + "/movie", status_code=201)
        async def movie_add(request: Request) -> dict:
            body = await request.json()
            body["id"] = fake.next_id()
            body.setdefault("hasFile", False)
            body.setdefault("isAvailable", True)
            fake.movies[body["id"]] = body
            return body

        @r.get(p + "/history/since")
        def history_since(date: str) -> list:
            return [h for h in fake.history_since_records if h["date"] >= date]

        if self.mask_secrets:
            for kind in ("indexer", "downloadclient"):
                self._masked_list(r, p, kind)

        api.router.routes[0:0] = r.routes
        return api

    def _list_routes(self, r: APIRouter, p: str, name: str, attr: str) -> None:
        fake = self

        @r.get(f"{p}/{name}", name=f"m_{name}_list")
        def lst() -> list:
            return getattr(fake, attr)

        @r.post(f"{p}/{name}", status_code=201, name=f"m_{name}_add")
        async def add(request: Request) -> dict:
            body = await request.json()
            body["id"] = fake.next_id()
            getattr(fake, attr).append(body)
            return body

        @r.put(f"{p}/{name}/{{item_id}}", status_code=202, name=f"m_{name}_put")
        async def put(item_id: int, request: Request) -> dict:
            body = await request.json()
            setattr(fake, attr, [body if x["id"] == item_id else x for x in getattr(fake, attr)])
            return body

    def _masked_list(self, r: APIRouter, p: str, kind: str) -> None:
        fake = self

        @r.get(f"{p}/{kind}", name=f"m_{kind}_masked")
        def lst() -> list:
            out = []
            for res in fake.providers[kind].values():
                res = copy.deepcopy(res)
                for f in res.get("fields") or []:
                    if f.get("privacy") in ("apiKey", "password") and f.get("value"):
                        f["value"] = MASK
                out.append(res)
            return out


# -- native back-up: echte SQLite met Sonarr/Radarr-kolomnamen -------------------------------
def _db_from_fake(fake: FakeArr, path: str) -> None:  # noqa: C901
    conn = sqlite3.connect(path)
    c = conn.cursor()
    c.executescript("""
        CREATE TABLE VersionInfo (Version INTEGER, AppliedOn TEXT, Description TEXT);
        CREATE TABLE Tags (Id INTEGER PRIMARY KEY, Label TEXT);
        CREATE TABLE RootFolders (Id INTEGER PRIMARY KEY, Path TEXT);
        CREATE TABLE QualityProfiles (Id INTEGER PRIMARY KEY, Name TEXT, Cutoff INTEGER,
            Items TEXT, UpgradeAllowed INTEGER);
        CREATE TABLE CustomFormats (Id INTEGER PRIMARY KEY, Name TEXT, Specifications TEXT);
        CREATE TABLE Indexers (Id INTEGER PRIMARY KEY, Name TEXT, Implementation TEXT,
            Settings TEXT, ConfigContract TEXT, EnableRss INTEGER, EnableAutomaticSearch INTEGER,
            EnableInteractiveSearch INTEGER, Priority INTEGER, Tags TEXT,
            DownloadClientId INTEGER);
        CREATE TABLE DownloadClients (Id INTEGER PRIMARY KEY, Enable INTEGER, Name TEXT,
            Implementation TEXT, Settings TEXT, ConfigContract TEXT, Priority INTEGER, Tags TEXT);
        CREATE TABLE RemotePathMappings (Id INTEGER PRIMARY KEY, Host TEXT, RemotePath TEXT,
            LocalPath TEXT);
        CREATE TABLE Config (Id INTEGER PRIMARY KEY, Key TEXT, Value TEXT);
        CREATE TABLE History (Id INTEGER PRIMARY KEY);
    """)
    c.execute("INSERT INTO VersionInfo VALUES (210, '2026-01-01', 'x')")
    c.executemany("INSERT INTO Tags VALUES (?,?)", [(t["id"], t["label"])
                                                    for t in fake.tags.values()])
    c.executemany("INSERT INTO RootFolders VALUES (?,?)", [(r["id"], r["path"])
                                                           for r in fake.root_folders])
    for q in fake.quality_profiles:
        items = [{"quality": i["quality"]["id"], "items": [], "allowed": i.get("allowed")}
                 for i in q.get("items") or [] if i.get("quality")]
        c.execute("INSERT INTO QualityProfiles VALUES (?,?,?,?,?)",
                  (q["id"], q["name"], q.get("cutoff"), json.dumps(items),
                   int(bool(q.get("upgradeAllowed")))))
    for cf in getattr(fake, "custom_formats", []):
        c.execute("INSERT INTO CustomFormats VALUES (?,?,?)", (cf["id"], cf["name"], "[]"))
    for i in fake.providers["indexer"].values():
        c.execute("INSERT INTO Indexers VALUES (?,?,?,?,?,?,?,?,?,?,?)",
                  (i["id"], i["name"], i["implementation"], _settings(i), i["configContract"],
                   int(i["enableRss"]), int(i["enableAutomaticSearch"]),
                   int(i["enableInteractiveSearch"]), i.get("priority", 25),
                   json.dumps(i.get("tags") or []), 0))
    for d in fake.providers["downloadclient"].values():
        c.execute("INSERT INTO DownloadClients VALUES (?,?,?,?,?,?,?,?)",
                  (d["id"], int(d["enable"]), d["name"], d["implementation"], _settings(d),
                   d["configContract"], d.get("priority", 1), json.dumps(d.get("tags") or [])))
    for m in fake.rpm.values():
        c.execute("INSERT INTO RemotePathMappings VALUES (?,?,?,?)",
                  (m["id"], m["host"], m["remotePath"], m["localPath"]))
    if fake.app == "sonarr":
        c.executescript("""
            CREATE TABLE Series (Id INTEGER PRIMARY KEY, TvdbId INTEGER, Title TEXT,
                Monitored INTEGER, MonitorNewItems INTEGER, SeriesType INTEGER,
                QualityProfileId INTEGER, Path TEXT, Tags TEXT, Seasons TEXT,
                SeasonFolder INTEGER, Status INTEGER);
            CREATE TABLE Episodes (Id INTEGER PRIMARY KEY, SeriesId INTEGER, SeasonNumber INTEGER,
                EpisodeNumber INTEGER, Monitored INTEGER, AirDateUtc TEXT,
                EpisodeFileId INTEGER);
            CREATE TABLE EpisodeFiles (Id INTEGER PRIMARY KEY, SeriesId INTEGER,
                SeasonNumber INTEGER, RelativePath TEXT, Size INTEGER);
            CREATE TABLE ImportLists (Id INTEGER PRIMARY KEY, Name TEXT, Implementation TEXT,
                Settings TEXT, ConfigContract TEXT, EnableAutomaticAdd INTEGER,
                SearchForMissingEpisodes INTEGER, RootFolderPath TEXT, QualityProfileId INTEGER,
                Tags TEXT);
        """)
        for s in fake.series.values():
            c.execute("INSERT INTO Series VALUES (?,?,?,?,?,?,?,?,?,?,?,?)",
                      (s["id"], s["tvdbId"], s["title"], int(s["monitored"]),
                       NEW_ITEMS[s.get("monitorNewItems", "all")], SERIES_TYPE[s["seriesType"]],
                       s["qualityProfileId"], s["path"], json.dumps(s.get("tags") or []),
                       json.dumps([{"seasonNumber": x["seasonNumber"],
                                    "monitored": x["monitored"]} for x in s["seasons"]]),
                       1, 0))
        for e in fake.episodes.values():
            fid = e.get("episodeFileId") or (e["id"] + 50000 if e["hasFile"] else 0)
            air = str(e.get("airDateUtc") or "").replace("T", " ") or None
            c.execute("INSERT INTO Episodes VALUES (?,?,?,?,?,?,?)",
                      (e["id"], e["seriesId"], e["seasonNumber"], e["episodeNumber"],
                       int(e["monitored"]), air, fid))
            if fid:
                c.execute("INSERT INTO EpisodeFiles VALUES (?,?,?,?,?)",
                          (fid, e["seriesId"], e["seasonNumber"], f"S{e['seasonNumber']}.mkv",
                           1))
        for il in fake.providers["importlist"].values():
            c.execute("INSERT INTO ImportLists VALUES (?,?,?,?,?,?,?,?,?,?)",
                      (il["id"], il["name"], il.get("implementation"), _settings(il), "x",
                       int(bool(il.get("enableAutomaticAdd"))), 0, il.get("rootFolderPath"),
                       il.get("qualityProfileId"), json.dumps(il.get("tags") or [])))
    else:
        c.executescript("""
            CREATE TABLE MovieMetadata (Id INTEGER PRIMARY KEY, TmdbId INTEGER, Title TEXT);
            CREATE TABLE Movies (Id INTEGER PRIMARY KEY, MovieMetadataId INTEGER,
                Monitored INTEGER, QualityProfileId INTEGER, Path TEXT, Tags TEXT,
                MinimumAvailability INTEGER, MovieFileId INTEGER);
            CREATE TABLE MovieFiles (Id INTEGER PRIMARY KEY, MovieId INTEGER);
            CREATE TABLE ImportLists (Id INTEGER PRIMARY KEY, Name TEXT, Implementation TEXT,
                Settings TEXT, ConfigContract TEXT, Enabled INTEGER, EnableAuto INTEGER,
                SearchOnAdd INTEGER, RootFolderPath TEXT, QualityProfileId INTEGER, Tags TEXT);
        """)
        for m in fake.movies.values():
            c.execute("INSERT INTO MovieMetadata VALUES (?,?,?)",
                      (m["id"] + 7000, m["tmdbId"], m["title"]))
            c.execute("INSERT INTO Movies VALUES (?,?,?,?,?,?,?,?)",
                      (m["id"], m["id"] + 7000, int(m["monitored"]),
                       m.get("qualityProfileId", 1), m["path"], json.dumps(m.get("tags") or []),
                       MIN_AVAIL[m.get("minimumAvailability", "released")],
                       m["id"] * 10 if m["hasFile"] else 0))
    c.execute("INSERT INTO Config(Key, Value) VALUES ('rsssyncinterval', ?)",
              (str(getattr(fake, "configs", {}).get("indexer", {}).get("rssSyncInterval", 15)),))
    conn.commit()
    conn.close()


def make_backup_zip(fake: FakeArr, *, api_key: str = "OLD-SOURCE-KEY") -> bytes:
    with tempfile.TemporaryDirectory() as tmp:
        db = os.path.join(tmp, "x.db")
        _db_from_fake(fake, db)
        buf = io.BytesIO()
        with zipfile.ZipFile(buf, "w") as z:
            z.write(db, f"{fake.app}.db")
            z.writestr("config.xml", f"<Config>\n  <Port>8989</Port>\n  <ApiKey>{api_key}"
                                     "</ApiKey>\n  <UrlBase></UrlBase>\n  <InstanceName>"
                                     f"{fake.app.capitalize()}</InstanceName>\n</Config>\n")
        return buf.getvalue()


def load_backup_zip(fake: FakeArr, data: bytes) -> None:  # noqa: C901
    """Emuleert een Sonarr/Radarr-restore: vervangt de staat door de inhoud van de DB."""
    with tempfile.TemporaryDirectory() as tmp:
        with zipfile.ZipFile(io.BytesIO(data)) as z:
            db = os.path.join(tmp, "r.db")
            with open(db, "wb") as out:
                out.write(z.read(f"{fake.app}.db"))
            cfg = z.read("config.xml").decode()
        fake.restored_config_xml = cfg  # type: ignore[attr-defined]
        conn = sqlite3.connect(db)
        conn.row_factory = sqlite3.Row
        fake.tags = {r["Id"]: {"id": r["Id"], "label": r["Label"]}
                     for r in conn.execute("SELECT * FROM Tags")}
        fake.root_folders = [{"id": r["Id"], "path": r["Path"], "accessible": True,
                              "freeSpace": 1, "unmappedFolders": []}
                             for r in conn.execute("SELECT * FROM RootFolders")]
        fake.quality_profiles = [
            {"id": r["Id"], "name": r["Name"], "cutoff": r["Cutoff"],
             "upgradeAllowed": bool(r["UpgradeAllowed"]),
             "items": [{"quality": {"id": i["quality"], "name": f"q{i['quality']}"},
                        "allowed": i["allowed"]} for i in json.loads(r["Items"])]}
            for r in conn.execute("SELECT * FROM QualityProfiles")]
        if hasattr(fake, "custom_formats"):
            fake.custom_formats = [{"id": r["Id"], "name": r["Name"], "specifications": []}
                                   for r in conn.execute("SELECT * FROM CustomFormats")]
        fake.providers["indexer"].clear()
        for r in conn.execute("SELECT * FROM Indexers"):
            idx = indexer(r["Id"], r["Name"], rss=bool(r["EnableRss"]),
                          auto=bool(r["EnableAutomaticSearch"]),
                          interactive=bool(r["EnableInteractiveSearch"]),
                          tags=json.loads(r["Tags"]))
            idx["fields"] = _fields(r["Settings"])
            idx["priority"] = r["Priority"]
            fake.providers["indexer"][r["Id"]] = idx
        fake.providers["downloadclient"].clear()
        for r in conn.execute("SELECT * FROM DownloadClients"):
            dc = download_client(r["Id"], r["Name"], enable=bool(r["Enable"]))
            dc["fields"] = _fields(r["Settings"])
            dc["priority"] = r["Priority"]
            dc["tags"] = json.loads(r["Tags"])
            fake.providers["downloadclient"][r["Id"]] = dc
        fake.providers["importlist"].clear()
        for r in conn.execute("SELECT * FROM ImportLists"):
            keys = r.keys()
            il = {"id": r["Id"], "name": r["Name"], "implementation": r["Implementation"],
                  "rootFolderPath": r["RootFolderPath"], "fields": _fields(r["Settings"]),
                  "tags": json.loads(r["Tags"] or "[]")}
            for col in ("EnableAutomaticAdd", "Enabled", "EnableAuto", "SearchOnAdd",
                        "SearchForMissingEpisodes"):
                if col in keys:
                    il[col[0].lower() + col[1:]] = bool(r[col])
            fake.providers["importlist"][r["Id"]] = il
        fake.rpm = {r["Id"]: {"id": r["Id"], "host": r["Host"], "remotePath": r["RemotePath"],
                              "localPath": r["LocalPath"]}
                    for r in conn.execute("SELECT * FROM RemotePathMappings")}
        row = conn.execute("SELECT Value FROM Config WHERE Key='rsssyncinterval'").fetchone()
        if row is not None and hasattr(fake, "configs"):
            fake.configs["indexer"]["rssSyncInterval"] = int(row["Value"])
        rev_type = {v: k for k, v in SERIES_TYPE.items()}
        rev_new = {v: k for k, v in NEW_ITEMS.items()}
        if fake.app == "sonarr":
            fake.series, fake.episodes = {}, {}
            for r in conn.execute("SELECT * FROM Series"):
                seasons = {x["seasonNumber"]: x["monitored"] for x in json.loads(r["Seasons"])}
                root = r["Path"].rsplit("/", 1)[0] + "/"
                s = make_series(r["Id"], r["Title"], r["TvdbId"], monitored=bool(r["Monitored"]),
                                series_type=rev_type[r["SeriesType"]],
                                quality_profile_id=r["QualityProfileId"], seasons=seasons,
                                root=root, tags=json.loads(r["Tags"]),
                                monitor_new_items=rev_new[r["MonitorNewItems"]])
                s["path"] = r["Path"]
                fake.series[r["Id"]] = s
            for r in conn.execute("SELECT * FROM Episodes"):
                ep = make_episode(r["Id"], r["SeriesId"], r["SeasonNumber"], r["EpisodeNumber"],
                                  monitored=bool(r["Monitored"]), has_file=bool(r["EpisodeFileId"]),
                                  file_id=r["EpisodeFileId"])
                ep["airDateUtc"] = (r["AirDateUtc"] or "").replace(" ", "T") or None
                fake.episodes[r["Id"]] = ep
        else:
            from test_arr_fakes import make_movie

            rev_avail = {v: k for k, v in MIN_AVAIL.items()}
            fake.movies = {}
            for r in conn.execute("SELECT m.*, mm.TmdbId, mm.Title FROM Movies m JOIN "
                                  "MovieMetadata mm ON mm.Id=m.MovieMetadataId"):
                mv = make_movie(r["Id"], r["Title"], r["TmdbId"], monitored=bool(r["Monitored"]),
                                has_file=bool(r["MovieFileId"]))
                mv.update(path=r["Path"], qualityProfileId=r["QualityProfileId"],
                          tags=json.loads(r["Tags"]),
                          minimumAvailability=rev_avail[r["MinimumAvailability"]])
                fake.movies[r["Id"]] = mv
        conn.close()


def test_backup_zip_roundtrip():
    src = MigFakeArr("sonarr")
    src.add_indexer(indexer(1, "NZBgeek"))
    src.providers["downloadclient"][1] = download_client(1)
    src.add_series(make_series(1, "Show", 100, seasons={1: True, 2: False}),
                   [make_episode(1, 1, 1, 1), make_episode(2, 1, 2, 1, monitored=False)])
    dst = MigFakeArr("sonarr")
    load_backup_zip(dst, make_backup_zip(src))
    assert dst.series[1]["seasons"][1]["monitored"] is False
    assert dst.episodes[2]["monitored"] is False
    assert dst.providers["indexer"][1]["fields"][1]["value"] == "idx-secret-1"
