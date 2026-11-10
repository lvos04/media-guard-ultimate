"""Migration wizard against REAL containers (runs after test_stack.py in the same stack).

"Old production" Sonarr/Radarr = extra plain fork containers (compose.old.yaml) with legacy root
paths (/tv, /movies). Core reaches the old Sonarr only through a recording proxy, which proves
that before cutover MediaGuard sent nothing but GETs.

  m1  populate the old Sonarr/Radarr like a real user setup (season/episode exceptions,
      monitorNewItems, anime series, tags, indexer with API key, download client)
  m2  Sonarr, LIVE source: preflight → backup (download of the existing native backup) → mapping
      with path remap /tv→/data/media/tv → native shadow restore into sonarr-series → verify GO →
      only GETs on the old instance → readiness → cutover (old RSS frozen, target live, RSS grab by
      the NEW instance only) → rollback (old restored exactly, target back in shadow)
  m3  Radarr, UPLOADED backup source: upload zip → native restore into radarr-films → verify →
      activate-search (confirm_old_instance_offline) → target grabs from RSS

NOTE: the native restores REPLACE the databases of sonarr-series and radarr-films (that is what a
migration does), so this file must run last.
"""
from __future__ import annotations

import io
import json
import time

import pytest
import requests

import test_stack as ts
from test_stack import core, feed_add, log, wait_for

OLD_S = ts.Arr("old-sonarr", "sonarr", 48990, "01d50000000000000000000000000001", "/tv", "tv", [5030, 5040])
OLD_R = ts.Arr("old-radarr", "radarr", 47880, "01d50000000000000000000000000002", "/movies", "movies",
               [2000, 2040, 2045])
SPY_S = "http://127.0.0.1:48991/__spy/requests"
M: dict = {}


def spy_calls(url=SPY_S):
    return requests.get(url, timeout=10).json()


def mig(method, path, body=None, timeout=900, expect=(200, 201), **kw):
    return core.call(method, "/migration" + path, json=body, timeout=timeout, expect=expect, **kw)


@pytest.fixture(scope="module", autouse=True)
def old_ready():
    if "X-CSRF-Token" not in core.s.headers:
        core.login()
    for arr in (OLD_S, OLD_R):
        wait_for(lambda arr=arr: requests.get(f"http://127.0.0.1:{arr.port}/ping", timeout=5).ok,
                 timeout=240, desc=f"{arr.iid} ping")
        wait_for(lambda arr=arr: arr.get("/system/status"), timeout=120, desc=f"{arr.iid} status")
    yield


def _configure_old(arr, apikey_name):
    if not any(r["path"].rstrip("/") == arr.root for r in arr.get("/rootfolder")):
        arr.post("/rootfolder", {"path": arr.root})
    schema = next(s for s in arr.get("/downloadclient/schema") if s["implementation"] == "Sabnzbd")
    schema.update(name="SAB (oud)", enable=True, priority=1)
    ts.set_field(schema["fields"], "host", "fakesab")
    ts.set_field(schema["fields"], "port", 8080)
    ts.set_field(schema["fields"], "apiKey", "sabkey")
    ts.set_field(schema["fields"], "tvCategory" if arr.app == "sonarr" else "movieCategory", arr.category)
    arr.post("/downloadclient", schema)
    schema = next(s for s in arr.get("/indexer/schema") if s["implementation"] == "Newznab")
    schema.update(name="Indexer (oud)", enableRss=True, enableAutomaticSearch=True,
                  enableInteractiveSearch=True, priority=20)
    ts.set_field(schema["fields"], "baseUrl", "http://fakeindexer:9117")
    ts.set_field(schema["fields"], "apiPath", "/api")
    ts.set_field(schema["fields"], "apiKey", apikey_name)
    ts.set_field(schema["fields"], "categories", arr.cats if arr.app == "radarr" else [5030, 5040])
    if arr.app == "sonarr":
        ts.set_field(schema["fields"], "animeCategories", [5070])
    arr.post("/indexer", schema, forceSave="true")
    # the old installation sees downloads at /downloads, the (shared) download client reports
    # /data/downloads: a remote path mapping, exactly what such users have configured
    arr.post("/remotepathmapping", {"host": "fakesab", "remotePath": "/data/downloads/",
                                    "localPath": "/downloads/"})
    tag = arr.post("/tag", {"label": "legacy"})
    any_id = next(p["id"] for p in arr.get("/qualityprofile") if p["name"] == "Any")
    return tag["id"], any_id


def _backup(arr):
    cmd = arr.post("/command", {"name": "Backup"})
    wait_for(lambda: arr.get(f"/command/{cmd['id']}")["status"] == "completed", timeout=60, desc="backup")
    return sorted(arr.get("/system/backup"), key=lambda b: b["time"])[-1]


def test_m1_populate_old_production_instances():
    tag, any_id = _configure_old(OLD_S, "old-sonarr")
    shows = {}
    for show, stype, new_items in ((ts.SERIES, "standard", "all"), (ts.ANIME, "anime", "none")):
        lookup = OLD_S.get("/series/lookup", term=f"tvdb:{show['tvdb']}")[0]
        body = dict(lookup, qualityProfileId=any_id, rootFolderPath="/tv", monitored=True,
                    seasonFolder=True, seriesType=stype, tags=[tag], monitorNewItems=new_items,
                    addOptions={"monitor": "none", "searchForMissingEpisodes": False,
                                "searchForCutoffUnmetEpisodes": False})
        s = OLD_S.post("/series", body)
        wait_for(lambda s=s: len(OLD_S.get("/episode", seriesId=s["id"])) > 5, timeout=180, desc="episodes")
        # Sonarr applies addOptions.monitor=none asynchronously after the first scan
        # (SeriesScannedHandler), which can overwrite an early PUT: repeat until it sticks.
        def settle(s=s):
            full = OLD_S.get(f"/series/{s['id']}")
            full["monitored"] = True
            for season in full["seasons"]:
                season["monitored"] = season["seasonNumber"] == 1    # S1 on, others off
            OLD_S.s.put(f"{OLD_S.base}/series/{s['id']}", json=full, timeout=60).raise_for_status()
            time.sleep(4)
            now = OLD_S.get(f"/series/{s['id']}")
            ok = now["monitored"] and [x["monitored"] for x in now["seasons"] if x["seasonNumber"] == 1] == [True]
            return now if ok else None
        shows[stype] = wait_for(settle, timeout=120, interval=1, desc="old series monitoring settled")
    bb = shows["standard"]
    eps = OLD_S.get("/episode", seriesId=bb["id"])
    s1 = [e["id"] for e in eps if e["seasonNumber"] == 1]
    OLD_S.s.put(f"{OLD_S.base}/episode/monitor", json={"episodeIds": s1, "monitored": True},
                timeout=60).raise_for_status()
    e2 = next(e for e in eps if e["seasonNumber"] == 1 and e["episodeNumber"] == 2)
    OLD_S.s.put(f"{OLD_S.base}/episode/monitor", json={"episodeIds": [e2["id"]], "monitored": False},
                timeout=60).raise_for_status()                      # episode-level exception
    OLD_S.post("/command", {"name": "RescanSeries"})
    time.sleep(5)
    M["old_series"] = {k: v["id"] for k, v in shows.items()}
    M["old_monitor"] = {(e["seasonNumber"], e["episodeNumber"]): e["monitored"]
                        for e in OLD_S.get("/episode", seriesId=bb["id"])}
    log("old Sonarr: series", {k: (v["title"], v["seriesType"], v["monitorNewItems"]) for k, v in shows.items()},
        "S1 monitored eps", sum(v for (s, _), v in M["old_monitor"].items() if s == 1))
    M["old_backup_s"] = _backup(OLD_S)
    log("old Sonarr backup:", M["old_backup_s"]["path"])
    # Radarr
    tag_r, any_r = _configure_old(OLD_R, "old-radarr")
    for film in (ts.FILM, {"tmdb": 10378}):
        lookup = OLD_R.get("/movie/lookup/tmdb", tmdbId=film["tmdb"])
        OLD_R.post("/movie", dict(lookup, qualityProfileId=any_r, rootFolderPath="/movies", monitored=True,
                                  minimumAvailability="released", tags=[tag_r],
                                  addOptions={"searchForMovie": False}))
    OLD_R.post("/command", {"name": "RescanMovie"})
    time.sleep(5)
    M["old_backup_r"] = _backup(OLD_R)
    log("old Radarr movies:", [(m["title"], m["hasFile"]) for m in OLD_R.get("/movie")])


def test_m2_live_source_native_restore_verify_cutover_rollback():
    requests.delete(SPY_S, timeout=10)
    src = mig("POST", "/sources", {"name": "Oude Sonarr", "url": "http://old-sonarr-spy:8989",
                                   "api_key": OLD_S.s.headers["X-Api-Key"]})
    log("source:", {k: src.get(k) for k in ("id", "app", "version", "kind", "database_type")})
    assert src["app"] == "sonarr"
    run = mig("POST", "/runs", {"name": "stack-test sonarr", "source_ids": [src["id"]]})
    rid = run["id"] if "id" in run else run["run"]["id"]
    M["run_s"], M["src_s"] = rid, src["id"]
    pre = mig("POST", "/preflight", {"run_id": rid, "wait": True})
    log("preflight:", json.dumps(pre)[:600])
    b = mig("POST", f"/runs/{rid}/backup", {"source_id": src["id"], "strategy": "existing", "wait": True})
    log("backup step:", json.dumps(b)[:500])
    prev = mig("POST", "/instance-mapping/preview", {"run_id": rid})
    log("mapping preview:", json.dumps(prev)[:500])
    mig("PUT", f"/runs/{rid}/mapping", {"entries": [{
        "source_id": src["id"], "target_instance_id": "sonarr-series", "mode": "native",
        "path_map": [{"from": "/tv", "to": "/data/media/tv"},
                     {"from": "/downloads", "to": "/data/downloads"}]}]})
    pp = mig("POST", f"/runs/{rid}/path-preview", {"source_id": src["id"]})
    log("path preview:", json.dumps(pp)[:500])
    assert pp["verdict"] != "no_go", pp
    mig("POST", f"/runs/{rid}/mapping/confirm")
    t0 = time.time()
    sh = mig("POST", f"/runs/{rid}/shadow-import", {"target_instance_id": "sonarr-series", "wait": True})
    for _ in range(10):  # restart of the fork may need a retry ("hervat")
        if sh.get("status") != "queued":
            break
        time.sleep(10)
        sh = mig("POST", f"/runs/{rid}/shadow-import", {"target_instance_id": "sonarr-series", "wait": True})
    log("shadow import %.0fs:" % (time.time() - t0), json.dumps({k: v for k, v in sh.items() if k != "log"})[:700])
    log("shadow log:", sh.get("log"))
    # target is in shadow: no RSS/automatic search, no download client, RSS task interval 0
    SS = ts.SS
    tasks = {t["taskName"]: t["interval"] for t in SS.get("/system/task")}
    log("target RssSync interval:", tasks.get("RssSync"))
    assert tasks.get("RssSync") == 0
    assert all(not i["enableRss"] and not i["enableAutomaticSearch"] for i in SS.get("/indexer"))
    assert all(not d["enable"] for d in SS.get("/downloadclient"))
    assert core.get("/instances/sonarr-series")["shadow_mode"] is True
    # data equal to the source: series, types, monitorNewItems, season + episode exceptions, tags, paths
    tgt = {s["tvdbId"]: s for s in SS.get("/series")}
    assert set(tgt) == {ts.SERIES["tvdb"], ts.ANIME["tvdb"]}
    bb = tgt[ts.SERIES["tvdb"]]
    assert bb["monitorNewItems"] == "all" and tgt[ts.ANIME["tvdb"]]["seriesType"] == "anime"
    assert bb["path"].startswith("/data/media/tv/"), bb["path"]
    mon = {(e["seasonNumber"], e["episodeNumber"]): e["monitored"] for e in SS.get("/episode", seriesId=bb["id"])}
    assert mon == M["old_monitor"]
    assert [t["label"] for t in SS.get("/tag")] == ["legacy"]
    ver = mig("POST", "/instance-mapping/verify", {"run_id": rid, "target_instance_id": "sonarr-series", "wait": True})
    rep = ver["sonarr-series"].get("report") or {}
    log("verify:", rep.get("verdict"), json.dumps(rep.get("mismatches"))[:800])
    M["verify_s"] = rep.get("verdict")
    assert rep.get("verdict") in ("go", "warn"), rep
    calls = spy_calls()
    methods = sorted({c["method"] for c in calls})
    log("old Sonarr calls before cutover:", len(calls), methods,
        sorted({c["path"] for c in calls})[:25])
    assert methods == ["GET"], [c for c in calls if c["method"] != "GET"]
    # ADR-011: before cutover MediaGuard must not replace library files (software lock, the
    # stack tests unlocked it earlier for a fresh install; re-lock to model a migrating user)
    assert core.post("/recovery/library-write", {"enabled": False}) == {"library_write_enabled": False}
    assert core.get("/recovery/readiness")["library_locked"] is True
    rd = mig("POST", f"/runs/{rid}/readiness", {"target_instance_id": "sonarr-series", "wait": True})
    log("readiness:", json.dumps(rd)[:600])
    cut = mig("POST", "/cutover", {"run_id": rid, "targets": ["sonarr-series"],
                                   "confirm_names": {"sonarr-series": "sonarr-series"},
                                   "accept_warnings": True, "wait": True})
    log("cutover:", cut)
    assert cut["cutovers"][0]["status"] in ("completed", "succeeded"), cut
    # a successful cutover unlocks the library automatically (no manual step)
    assert core.get("/recovery/readiness")["library_locked"] is False
    assert any(a["new_value"] is True and "overstap" in (a.get("reason") or "")
               for a in core.get("/audit", action="library.write_enabled"))
    old_idx = OLD_S.get("/indexer")
    new_idx = SS.get("/indexer")
    log("old indexers:", [(i["name"], i["enableRss"], i["enableAutomaticSearch"]) for i in old_idx])
    log("new indexers:", [(i["name"], i["enableRss"], i["enableAutomaticSearch"]) for i in new_idx])
    assert all(not i["enableRss"] and not i["enableAutomaticSearch"] for i in old_idx)
    assert all(i["enableRss"] for i in new_idx) and all(d["enable"] for d in SS.get("/downloadclient"))
    assert core.get("/instances/sonarr-series")["shadow_mode"] is False
    non_get = [c for c in spy_calls() if c["method"] != "GET"]
    log("old Sonarr non-GET calls during cutover:", [(c["method"], c["path"]) for c in non_get])
    assert {c["path"].rsplit("/", 1)[0] for c in non_get} <= {"/api/v3/indexer"}
    # RSS: a new release for a monitored S1 episode without file → only the NEW instance grabs
    e = next(x for x in SS.get("/episode", seriesId=bb["id"]) if x["seasonNumber"] == 1 and x["monitored"]
             and not x["hasFile"])
    title = f"Breaking.Bad.S01E{e['episodeNumber']:02d}.720p.HDTV.x264-MGCUTOVER"
    feed_add(title=title, categories=[5040], tvdbid=ts.SERIES["tvdb"], season=1, episode=e["episodeNumber"])
    OLD_S.post("/command", {"name": "RssSync"})
    core.command("sonarr-series", "RssSync")
    wait_for(lambda: [h for h in SS.history("grabbed") if h["sourceTitle"] == title], timeout=90,
             desc="new instance grabs after cutover")
    time.sleep(3)
    assert not [h for h in OLD_S.history("grabbed") if h["sourceTitle"] == title]
    assert core.decisions("sonarr-series", "pre_grab", title), "real core pre-grab hook not called"
    # rollback: target back to shadow first, then the old instance exactly restored
    rb = mig("POST", "/rollback", {"run_id": rid, "target_instance_id": "sonarr-series",
                                   "confirm_name": "sonarr-series", "wait": True})
    log("rollback:", rb)
    old_after = [(i["name"], i["enableRss"], i["enableAutomaticSearch"], i["enableInteractiveSearch"]) for i in OLD_S.get("/indexer")]
    log("old indexers after rollback:", old_after)
    assert old_after == [("Indexer (oud)", True, True, True)]
    assert all(not i["enableRss"] and not i["enableAutomaticSearch"] for i in SS.get("/indexer"))
    assert core.get("/instances/sonarr-series")["shadow_mode"] is True


def test_m3_uploaded_backup_source_native_restore_and_activate_search():
    b = M["old_backup_r"]
    r = OLD_R.s.get(f"http://127.0.0.1:{OLD_R.port}{b['path']}", timeout=60)
    r.raise_for_status()
    assert r.content[:2] == b"PK"
    files = {"file": (b["name"], io.BytesIO(r.content), "application/zip")}
    hdr = {"X-CSRF-Token": core.s.headers["X-CSRF-Token"]}
    up = core.s.post(ts.CORE + "/api/v1/migration/sources/upload", files=files, data={"name": "Oude Radarr (back-up)"},
                     headers=hdr, timeout=120)
    assert up.status_code == 200, up.text[:800]
    src = up.json()
    log("upload source:", {k: src.get(k) for k in ("id", "app", "version", "kind")})
    assert src["kind"] == "backup" and src["app"] == "radarr"
    run = mig("POST", "/runs", {"name": "stack-test radarr", "source_ids": [src["id"]]})
    rid = run["id"] if "id" in run else run["run"]["id"]
    mig("POST", "/preflight", {"run_id": rid, "wait": True})
    mig("POST", "/instance-mapping/preview", {"run_id": rid})
    mig("PUT", f"/runs/{rid}/mapping", {"entries": [{
        "source_id": src["id"], "target_instance_id": "radarr-films", "mode": "native",
        "path_map": [{"from": "/movies", "to": "/data/media/movies"},
                     {"from": "/downloads", "to": "/data/downloads"}]}]})
    pp = mig("POST", f"/runs/{rid}/path-preview", {"source_id": src["id"]})
    log("path preview:", pp.get("verdict"), json.dumps(pp)[:300])
    mig("POST", f"/runs/{rid}/mapping/confirm")
    sh = mig("POST", f"/runs/{rid}/shadow-import", {"target_instance_id": "radarr-films", "wait": True})
    for _ in range(10):
        if sh.get("status") != "queued":
            break
        time.sleep(10)
        sh = mig("POST", f"/runs/{rid}/shadow-import", {"target_instance_id": "radarr-films", "wait": True})
    log("radarr shadow import:", json.dumps({k: v for k, v in sh.items() if k != "log"})[:600])
    RF = ts.RF
    movies = {m["tmdbId"]: m for m in RF.get("/movie")}
    assert set(movies) == {ts.FILM["tmdb"], 10378}
    assert all(m["path"].startswith("/data/media/movies/") for m in movies.values())
    tasks = {t["taskName"]: t["interval"] for t in RF.get("/system/task")}
    assert tasks.get("RssSync") == 0
    ver = mig("POST", "/instance-mapping/verify", {"run_id": rid, "target_instance_id": "radarr-films", "wait": True})
    rep = ver["radarr-films"].get("report") or {}
    log("verify:", rep.get("verdict"), json.dumps(rep.get("mismatches"))[:800])
    assert rep.get("verdict") in ("go", "warn"), rep
    mig("POST", f"/runs/{rid}/readiness", {"target_instance_id": "radarr-films", "wait": True})
    body = {"run_id": rid, "targets": ["radarr-films"], "confirm_names": {"radarr-films": "radarr-films"},
            "accept_warnings": True, "wait": True}
    r = mig("POST", f"/runs/{rid}/activate-search", body, expect=(409,))
    log("without confirm_old_instance_offline:", r)
    act = mig("POST", f"/runs/{rid}/activate-search", {**body, "confirm_old_instance_offline": True})
    log("activate-search:", act)
    assert all(i["enableRss"] for i in RF.get("/indexer"))
    title = "Big.Buck.Bunny.2008.1080p.BluRay.x264-MGACTIVATE"
    feed_add(title=title, size=400_000_000, categories=[2040], tmdbid=10378, imdbid="tt1254207")
    core.command("radarr-films", "RssSync")
    wait_for(lambda: [h for h in RF.history("grabbed") if h["sourceTitle"] == title], timeout=90,
             desc="radarr-films grabs after activate-search")
