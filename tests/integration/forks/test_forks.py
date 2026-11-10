"""MediaGuard fork integration tests (R6.1, R6.4, R6.7-ish, Seerr API basics, hook contract).

Run via tests/integration/forks/run.sh (starts the compose stack). Tests run in file order and
share state (instances are configured once); each test clears the fake indexer feed first.
"""
import json
import re
import os
import subprocess
import time
from pathlib import Path

import pytest
import requests

HERE = Path(__file__).resolve().parent
RUN = HERE / ".run"
STUB = "http://127.0.0.1:18100"
INDEXER = "http://127.0.0.1:19117"
SAB = "http://127.0.0.1:18080"

SERIES = {"title": "Breaking Bad", "tvdb": 81189}
ANIME = {"title": "Cowboy Bebop", "tvdb": 76885}
FILM = {"title": "Night of the Living Dead", "tmdb": 10331, "year": 1968}
FILM2 = {"title": "Big Buck Bunny", "tmdb": 10378, "year": 2008}
ANIMEFILM = {"title": "Spirited Away", "tmdb": 129, "year": 2001}
ANIMEFILM2 = {"title": "My Neighbor Totoro", "tmdb": 8392, "year": 1988}


def log(*args):
    print("   ", *args, flush=True)


def wait_for(fn, timeout=120, interval=2, desc="condition"):
    deadline = time.time() + timeout
    last = None
    while time.time() < deadline:
        try:
            last = fn()
            if last:
                return last
        except Exception as ex:  # noqa
            last = ex
        time.sleep(interval)
    raise AssertionError(f"timeout waiting for {desc}: last={last!r}")


def compose(*args, check=True):
    cmd = (RUN / "compose-cmd").read_text().split("\n")
    cmd = [c for c in cmd if c]
    return subprocess.run(cmd + list(args), cwd=HERE, check=check, capture_output=True, text=True)


class Arr:
    def __init__(self, name, app, port, apikey, root, category, media_dir):
        self.name, self.app, self.port, self.apikey = name, app, port, apikey
        self.base = f"http://127.0.0.1:{port}/api/v3"
        self.root, self.category, self.media_dir = root, category, RUN / "media" / media_dir
        self.s = requests.Session()
        self.s.headers["X-Api-Key"] = apikey

    def get(self, path, **params):
        r = self.s.get(self.base + path, params=params, timeout=120)
        r.raise_for_status()
        return r.json()

    def post(self, path, body, **params):
        r = self.s.post(self.base + path, json=body, params=params, timeout=120)
        if r.status_code >= 400:
            raise AssertionError(f"{self.name} POST {path} -> {r.status_code} {r.text[:800]}")
        return r.json() if r.text else None

    def put(self, path, body, **params):
        r = self.s.put(self.base + path, json=body, params=params, timeout=120)
        if r.status_code >= 400:
            raise AssertionError(f"{self.name} PUT {path} -> {r.status_code} {r.text[:800]}")
        return r.json() if r.text else None

    def command(self, name, wait=True, **body):
        cmd = self.post("/command", dict(name=name, **body))
        if wait:
            wait_for(lambda: self.get(f"/command/{cmd['id']}")["status"] in ("completed", "failed"),
                     timeout=180, interval=1, desc=f"{self.name} {name}")
        return cmd

    def history(self, event_type=None):
        recs = self.get("/history", pageSize=200, sortKey="date", sortDirection="descending")["records"]
        return [r for r in recs if event_type is None or r["eventType"] == event_type]

    def container(self):
        return f"mgforks-{self.name}-1"


INSTANCES = {
    "sonarr-series": Arr("sonarr-series", "sonarr", 18989, "5e1a0000000000000000000000000001", "/media/tv", "tv", "sonarr-series"),
    "sonarr-anime": Arr("sonarr-anime", "sonarr", 18990, "5e1a0000000000000000000000000002", "/media/anime", "tv-anime", "sonarr-anime"),
    "radarr-films": Arr("radarr-films", "radarr", 17878, "5e1a0000000000000000000000000003", "/media/movies", "movies", "radarr-films"),
    "radarr-animefilms": Arr("radarr-animefilms", "radarr", 17879, "5e1a0000000000000000000000000004", "/media/animefilms", "movies-anime", "radarr-animefilms"),
}
SS, SA, RF, RA = (INSTANCES[k] for k in ("sonarr-series", "sonarr-anime", "radarr-films", "radarr-animefilms"))
STATE = {}


def stub_rules(*rules):
    requests.delete(f"{STUB}/admin/rules", timeout=10)
    if rules:
        requests.post(f"{STUB}/admin/rules", json=list(rules), timeout=10).raise_for_status()


def stub_calls(**params):
    return requests.get(f"{STUB}/admin/calls", params=params, timeout=10).json()


def feed_clear():
    requests.delete(f"{INDEXER}/admin/releases", timeout=10).raise_for_status()


def feed_add(**release):
    r = requests.post(f"{INDEXER}/admin/releases", json=release, timeout=10)
    r.raise_for_status()
    return r.json()


def sab_jobs():
    return requests.get(f"{SAB}/admin/jobs", timeout=10).json()


def set_field(fields, name, value):
    for f in fields:
        if f["name"] == name:
            f["value"] = value
            return
    raise KeyError(name)


def rss_sync_all():
    for arr in INSTANCES.values():
        if arr.name not in STATE.get("stopped", set()):
            arr.command("RssSync")


def episode(arr, series_id, season, number):
    eps = arr.get("/episode", seriesId=series_id)
    return next(e for e in eps if e["seasonNumber"] == season and e["episodeNumber"] == number)


def anime_ep(arr, series_id, absolute):
    eps = arr.get("/episode", seriesId=series_id)
    return next(e for e in eps if e.get("absoluteEpisodeNumber") == absolute)


def monitor_episode(arr, ep_id):
    arr.put("/episode/monitor", {"episodeIds": [ep_id], "monitored": True})


def wait_imported_episode(arr, ep_id, timeout=240):
    def check():
        arr.command("RefreshMonitoredDownloads", wait=False)
        return arr.get(f"/episode/{ep_id}")["hasFile"]
    return wait_for(check, timeout=timeout, interval=5, desc=f"{arr.name} episode {ep_id} imported")


def wait_imported_movie(arr, movie_id, timeout=240):
    def check():
        arr.command("RefreshMonitoredDownloads", wait=False)
        return arr.get(f"/movie/{movie_id}")["hasFile"]
    return wait_for(check, timeout=timeout, interval=5, desc=f"{arr.name} movie {movie_id} imported")


def files_under(path):
    return sorted(str(p.relative_to(RUN)) for p in Path(path).rglob("*.mkv"))


# --------------------------------------------------------------------------------------------- setup
@pytest.fixture(scope="session", autouse=True)
def stack_ready():
    for arr in INSTANCES.values():
        wait_for(lambda: requests.get(f"http://127.0.0.1:{arr.port}/ping", timeout=5).status_code == 200,
                 timeout=240, desc=f"{arr.name} /ping")
        wait_for(lambda: arr.get("/system/status"), timeout=120, desc=f"{arr.name} status")
    for url in (STUB, INDEXER, SAB):
        wait_for(lambda: requests.get(url + "/health", timeout=5).ok, timeout=60, desc=url)
    yield


@pytest.fixture(autouse=True)
def clean_feed():
    feed_clear()
    stub_rules()
    yield


# --------------------------------------------------------------------------------------------- R6.1
def test_r6_1_four_independent_instances_two_images():
    statuses = {n: a.get("/system/status") for n, a in INSTANCES.items()}
    for n, st in statuses.items():
        log(n, st["appName"], st["version"], st["instanceName"], "appData", st["appData"],
            "pkg", st.get("packageVersion"), "db", st["databaseType"], st["databaseVersion"])
    assert all(n in st["instanceName"] for n, st in statuses.items())     # Sonarr requires the "Sonarr" prefix
    assert statuses["sonarr-series"]["appName"] == statuses["sonarr-anime"]["appName"] == "Sonarr"
    assert statuses["radarr-films"]["appName"] == statuses["radarr-animefilms"]["appName"] == "Radarr"
    assert statuses["sonarr-series"]["version"].startswith("4.")       # Sonarr v4 line (API v3)
    # MediaGuard-patchrevisie (-mg1, -mg2, …): Sonarr staat sinds patch 0005 op mg2
    assert all(re.search(r"-mg\d+$", st["packageVersion"]) for st in statuses.values())

    images, volumes, pids = set(), {}, {}
    for n, arr in INSTANCES.items():
        info = json.loads(subprocess.check_output(["docker", "inspect", arr.container()]))[0]
        images.add(info["Image"])
        volumes[n] = [m["Name"] for m in info["Mounts"] if m["Destination"] == "/config"][0]
        pids[n] = info["State"]["Pid"]
        top = subprocess.check_output(["docker", "top", arr.container(), "-o", "pid,user,args"], text=True)
        log(n, "image", info["Config"]["Image"], "config volume", volumes[n], "pid", pids[n],
            "|", [l for l in top.splitlines() if "/app/" in l][0].strip())
        db = "sonarr.db" if arr.app == "sonarr" else "radarr.db"
        ls = subprocess.check_output(["docker", "exec", arr.container(), "ls", "-la", f"/config/{db}", "/config/config.xml"], text=True)
        assert db in ls
    assert len(images) == 2, images                           # exactly two fork images
    assert len(set(volumes.values())) == 4, volumes          # four separate /config volumes
    assert len(set(pids.values())) == 4                      # four separate processes
    # separate databases: a tag created in one instance is invisible in the others
    SS.post("/tag", {"label": "only-in-sonarr-series"})
    for arr in (SA, RF, RA):
        assert "only-in-sonarr-series" not in [t["label"] for t in arr.get("/tag")]


# --------------------------------------------------------------------------------------------- configure + Seerr API basics
def configure(arr):
    # root folder (Seerr: GET /rootfolder)
    if not any(r["path"].rstrip("/") == arr.root for r in arr.get("/rootfolder")):
        arr.post("/rootfolder", {"path": arr.root})
    # download client: fake SABnzbd, instance specific category
    schema = next(s for s in arr.get("/downloadclient/schema") if s["implementation"] == "Sabnzbd")
    schema.update(name="FakeSAB", enable=True, priority=1)
    set_field(schema["fields"], "host", "fakesab")
    set_field(schema["fields"], "port", 8080)
    set_field(schema["fields"], "apiKey", "sabkey")
    set_field(schema["fields"], "tvCategory" if arr.app == "sonarr" else "movieCategory", arr.category)
    arr.post("/downloadclient", schema)
    # indexer: the SAME fake Newznab feed for all 4 instances; apikey identifies the instance
    schema = next(s for s in arr.get("/indexer/schema") if s["implementation"] == "Newznab")
    schema.update(name="FakeNewznab", enableRss=True, enableAutomaticSearch=True, enableInteractiveSearch=True, priority=25)
    set_field(schema["fields"], "baseUrl", "http://fakeindexer:9117")
    set_field(schema["fields"], "apiPath", "/api")
    set_field(schema["fields"], "apiKey", arr.name)
    if arr.app == "sonarr":
        set_field(schema["fields"], "categories", [5030, 5040])
        set_field(schema["fields"], "animeCategories", [5070])
    else:
        set_field(schema["fields"], "categories", [2000, 2040, 2045])
    arr.post("/indexer", schema, forceSave="true")
    # stock Webhook connection -> stub (contract: events via upstream webhook, Basic auth instance:key)
    schema = next(s for s in arr.get("/notification/schema") if s["implementation"] == "Webhook")
    schema.update(name="MediaGuard", **{k: True for k in schema if k.startswith("on") and isinstance(schema[k], bool)})
    set_field(schema["fields"], "url", f"http://stub-mediaguard:8100/api/v1/hooks/arr-webhook/{arr.name}")
    set_field(schema["fields"], "username", arr.name)
    set_field(schema["fields"], "password", f"key-{arr.name}")
    arr.post("/notification", schema, forceSave="true")
    tag = arr.post("/tag", {"label": arr.name})
    profiles = arr.get("/qualityprofile")
    STATE.setdefault(arr.name, {}).update(tag=tag["id"], profile=next(p["id"] for p in profiles if p["name"] == "Any"))


def add_series(arr, show, series_type):
    st = STATE[arr.name]
    lookup = arr.get("/series/lookup", term=f"tvdb:{show['tvdb']}")
    assert lookup and lookup[0]["tvdbId"] == show["tvdb"], lookup[:1]
    body = dict(lookup[0], qualityProfileId=st["profile"], rootFolderPath=arr.root, monitored=True,
                seasonFolder=True, seriesType=series_type, tags=[st["tag"]],
                addOptions={"monitor": "none", "searchForMissingEpisodes": False, "searchForCutoffUnmetEpisodes": False})
    series = arr.post("/series", body)
    wait_for(lambda: len(arr.get("/episode", seriesId=series["id"])) > 5, timeout=180, desc=f"{arr.name} episodes")
    # monitor "none" also unmonitors the series in v4: monitor the series again, episodes stay unmonitored
    full = arr.get(f"/series/{series['id']}")
    full["monitored"] = True
    series = arr.put(f"/series/{series['id']}", full)
    assert series["monitored"] is True
    return series


def add_movie(arr, film):
    st = STATE[arr.name]
    lookup = arr.get("/movie/lookup/tmdb", tmdbId=film["tmdb"])
    assert lookup["tmdbId"] == film["tmdb"], lookup
    body = dict(lookup, qualityProfileId=st["profile"], rootFolderPath=arr.root, monitored=True,
                minimumAvailability="released", tags=[st["tag"]], addOptions={"searchForMovie": False})
    return arr.post("/movie", body)


def test_seerr_api_compat_sonarr():
    """Endpoints/methods Seerr uses for Sonarr v3/v4 servers."""
    for arr in (SS, SA):
        configure(arr)
        st = arr.get("/system/status")
        assert arr.get("/qualityprofile") and arr.get("/rootfolder")
        assert any(t["label"] == arr.name for t in arr.get("/tag"))
        lp = arr.s.get(arr.base + "/languageprofile", timeout=30)
        log(arr.name, "GET /languageprofile ->", lp.status_code, (lp.json()[:1] if lp.ok else lp.text[:100]))
        assert lp.status_code == 200          # Sonarr v4 keeps the (deprecated) v3 language profile endpoint
        assert arr.get("/downloadclient")[0]["implementation"] == "Sabnzbd"
        log(arr.name, "version", st["version"], "rootfolders", [r["path"] for r in arr.get("/rootfolder")])
    STATE["series"] = add_series(SS, SERIES, "standard")
    STATE["anime"] = add_series(SA, ANIME, "anime")
    # Seerr: GET /series?tvdbId=, PUT /series (season monitoring), /episode, /episode/monitor, command
    s = SS.get("/series", tvdbId=SERIES["tvdb"])
    assert len(s) == 1 and s[0]["title"] == SERIES["title"]
    assert SA.get("/series", tvdbId=SERIES["tvdb"]) == []            # not in the anime instance
    full = SS.get(f"/series/{s[0]['id']}")
    for season in full["seasons"]:
        season["monitored"] = season["seasonNumber"] == 1
    updated = SS.put(f"/series/{full['id']}", full)
    eps = SS.get("/episode", seriesId=full["id"])
    log("after season PUT: monitored episodes", sum(e["monitored"] for e in eps), "of", len(eps))
    SS.put("/episode/monitor", {"episodeIds": [e["id"] for e in eps], "monitored": False})
    assert [x["monitored"] for x in updated["seasons"] if x["seasonNumber"] == 1] == [True]
    e3 = episode(SS, full["id"], 1, 3)
    monitor_episode(SS, e3["id"])
    assert SS.get(f"/episode/{e3['id']}")["monitored"] is True
    an = SA.get("/series", tvdbId=ANIME["tvdb"])[0]
    assert an["seriesType"] == "anime"
    log("series ids:", full["id"], an["id"], "S01E03 id", e3["id"])


def test_seerr_api_compat_radarr():
    for arr in (RF, RA):
        configure(arr)
        assert arr.get("/qualityprofile") and arr.get("/rootfolder")
    STATE["film"] = add_movie(RF, FILM)
    STATE["film2"] = add_movie(RF, FILM2)
    STATE["animefilm"] = add_movie(RA, ANIMEFILM)
    STATE["animefilm2"] = add_movie(RA, ANIMEFILM2)
    m = RF.get("/movie", tmdbId=FILM["tmdb"])
    assert len(m) == 1 and m[0]["year"] == FILM["year"]
    assert RA.get("/movie", tmdbId=FILM["tmdb"]) == []
    full = RF.get(f"/movie/{m[0]['id']}")
    full["monitored"] = True
    assert RF.put(f"/movie/{full['id']}", full)["monitored"] is True
    log("movies:", [(x["title"], x["id"]) for x in RF.get("/movie")], [(x["title"], x["id"]) for x in RA.get("/movie")])


# --------------------------------------------------------------------------------------------- R6.4 RSS grab + import
def test_rss_new_episode_grabbed_and_imported_by_correct_instance_only():
    series = STATE["series"]
    e3 = episode(SS, series["id"], 1, 3)
    requests.delete(f"{STUB}/admin/calls", timeout=10)
    rel = feed_add(title="Breaking.Bad.S01E03.1080p.WEB-DL.DD5.1.H.264-MGTEST", categories=[5040],
                   tvdbid=SERIES["tvdb"], season=1, episode=3)
    t0 = time.time()
    rss_sync_all()
    grabbed = wait_for(lambda: SS.history("grabbed"), timeout=60, desc="sonarr-series grab")
    log("grabbed by sonarr-series after %.1fs:" % (time.time() - t0), grabbed[0]["sourceTitle"])
    for arr in (SA, RF, RA):
        assert arr.history("grabbed") == [], arr.name
    queries = requests.get(f"{INDEXER}/admin/queries", timeout=10).json()
    rss_hits = {q["apikey"] for q in queries if q.get("rss") and q["time"] >= t0}
    log("RSS feed polled by:", sorted(rss_hits))
    assert rss_hits == set(INSTANCES)          # all 4 saw the release, only one grabbed it
    calls = stub_calls(hook="pre-grab")
    assert any(c["instance_id"] == "sonarr-series" and c["title"] == rel["title"] and
               c["payload"]["search_type"] == "rss" for c in calls), calls
    pg = next(c for c in calls if c["title"] == rel["title"])
    assert pg["key_ok"] and pg["payload"]["series"]["tvdb_id"] == SERIES["tvdb"]
    assert pg["payload"]["episodes"][0]["episode_number"] == 3
    log("pre-grab payload:", json.dumps(pg["payload"])[:600])
    wait_imported_episode(SS, e3["id"])
    log("imported after %.1fs" % (time.time() - t0), files_under(SS.media_dir))
    assert any("Breaking Bad" in f and "S01E03" in f for f in files_under(SS.media_dir))
    for arr in (SA, RF, RA):
        assert files_under(arr.media_dir) == [], arr.name
    jobs = sab_jobs()
    assert [j["cat"] for j in jobs] == ["tv"], jobs
    pi = [c for c in stub_calls(hook="pre-import") if c["instance_id"] == "sonarr-series"]
    assert pi and pi[0]["payload"]["download_client"] == "FakeSAB" and pi[0]["payload"]["media_info"]["video_codec"]
    log("pre-import payload:", json.dumps(pi[0]["payload"])[:700])


def test_rss_new_anime_episode_grabbed_by_anime_instance_only():
    anime = STATE["anime"]
    e5 = anime_ep(SA, anime["id"], 5)
    monitor_episode(SA, e5["id"])
    feed_add(title="[MGTest] Cowboy Bebop - 05 (WEB 1080p) [Dual Audio]", categories=[5070],
             tvdbid=ANIME["tvdb"], season=e5["seasonNumber"], episode=e5["episodeNumber"], absolute=5)
    before = {a.name: len(a.history("grabbed")) for a in INSTANCES.values()}
    rss_sync_all()
    wait_for(lambda: SA.history("grabbed"), timeout=60, desc="sonarr-anime grab")
    after = {a.name: len(a.history("grabbed")) for a in INSTANCES.values()}
    log("grab counts before/after:", before, after)
    assert after["sonarr-anime"] == before["sonarr-anime"] + 1
    assert all(after[n] == before[n] for n in after if n != "sonarr-anime")
    pg = [c for c in stub_calls(hook="pre-grab") if c["instance_id"] == "sonarr-anime"]
    assert pg[-1]["payload"]["series"]["series_type"] == "anime"
    assert pg[-1]["payload"]["parsed"]["absolute_episode_numbers"] == [5]
    wait_imported_episode(SA, e5["id"])
    log("anime files:", files_under(SA.media_dir))
    assert any("Cowboy Bebop" in f for f in files_under(SA.media_dir))
    assert not any("Cowboy" in f for f in files_under(SS.media_dir))


def test_rss_new_movies_grabbed_by_correct_radarr_instance():
    feed_add(title="Night.of.the.Living.Dead.1968.1080p.BluRay.x264-MGTEST", categories=[2040],
             tmdbid=FILM["tmdb"], imdbid="tt0063350")
    feed_add(title="Spirited.Away.2001.1080p.BluRay.x264.Dual.Audio-MGTEST", categories=[2040],
             tmdbid=ANIMEFILM["tmdb"], imdbid="tt0245429")
    rss_sync_all()
    wait_for(lambda: RF.history("grabbed") and RA.history("grabbed"), timeout=60, desc="radarr grabs")
    log("radarr-films grabbed:", [h["sourceTitle"] for h in RF.history("grabbed")])
    log("radarr-animefilms grabbed:", [h["sourceTitle"] for h in RA.history("grabbed")])
    assert [h["sourceTitle"] for h in RF.history("grabbed")] == ["Night.of.the.Living.Dead.1968.1080p.BluRay.x264-MGTEST"]
    assert [h["sourceTitle"] for h in RA.history("grabbed")] == ["Spirited.Away.2001.1080p.BluRay.x264.Dual.Audio-MGTEST"]
    wait_imported_movie(RF, STATE["film"]["id"])
    wait_imported_movie(RA, STATE["animefilm"]["id"])
    log("films:", files_under(RF.media_dir), "animefilms:", files_under(RA.media_dir))
    assert any("Night of the Living Dead" in f for f in files_under(RF.media_dir))
    assert any("Spirited Away" in f for f in files_under(RA.media_dir))
    pg = [c for c in stub_calls(hook="pre-grab") if c["instance_id"] == "radarr-films"]
    assert pg[-1]["payload"]["movie"]["tmdb_id"] == FILM["tmdb"] and pg[-1]["payload"]["series"] is None
    cats = sorted(j["cat"] for j in sab_jobs())
    log("download categories used:", cats)
    assert cats == ["movies", "movies-anime", "tv", "tv-anime"]


# --------------------------------------------------------------------------------------------- pre-grab hook
def test_pregrab_reject_visible_in_interactive_search_and_blocks_rss():
    series = STATE["series"]
    e4 = episode(SS, series["id"], 1, 4)
    monitor_episode(SS, e4["id"])
    title = "Breaking.Bad.S01E04.1080p.WEB-DL.DD5.1.H.264-MGTEST"
    feed_add(title=title, categories=[5040], tvdbid=SERIES["tvdb"], season=1, episode=4)
    stub_rules({"hook": "pre-grab", "instance_id": "sonarr-series", "title_contains": "S01E04", "decision": "reject",
                "reason_code": "AUDIO_PREFERENCE", "message": "Geen Nederlandse of Engelse audio"})
    releases = SS.get("/release", episodeId=e4["id"])
    rel = next(r for r in releases if r["title"] == title)
    log("interactive search:", rel["title"], "approved", rel["approved"], "rejections", rel["rejections"])
    assert rel["approved"] is False
    assert "MediaGuard: Geen Nederlandse of Engelse audio [AUDIO_PREFERENCE]" in rel["rejections"]
    call = [c for c in stub_calls(hook="pre-grab") if c["title"] == title][-1]
    assert call["payload"]["search_type"] == "interactive"
    rss_sync_all()
    time.sleep(3)
    assert not [h for h in SS.history("grabbed") if h["sourceTitle"] == title]
    # accept decision -> approved
    stub_rules({"hook": "pre-grab", "title_contains": "S01E04", "decision": "accept", "reason_code": "OK"})
    rel = next(r for r in SS.get("/release", episodeId=e4["id"]) if r["title"] == title)
    log("after accept rule:", rel["approved"], rel["rejections"])
    assert rel["approved"] is True and not rel["rejections"]
    # Radarr interactive search shows the reason as well
    stub_rules({"hook": "pre-grab", "instance_id": "radarr-films", "decision": "reject",
                "reason_code": "IDENTITY_MISMATCH", "message": "Verkeerde film"})
    feed_add(title="Big.Buck.Bunny.2008.1080p.BluRay.x264-MGTEST", size=400_000_000, categories=[2040], tmdbid=FILM2["tmdb"], imdbid="tt1254207")
    rels = RF.get("/release", movieId=STATE["film2"]["id"])
    rel = next(r for r in rels if r["title"].startswith("Big.Buck.Bunny"))
    log("radarr interactive:", rel["title"], rel["rejections"])
    assert "MediaGuard: Verkeerde film [IDENTITY_MISMATCH]" in rel["rejections"]
    SS.put("/episode/monitor", {"episodeIds": [e4["id"]], "monitored": False})


def test_pregrab_timeout_fail_open_and_fail_closed():
    """sonarr-series: fail policy open; sonarr-anime: fail policy closed. Stub delays 4s > 1.5s timeout."""
    series, anime = STATE["series"], STATE["anime"]
    e6 = episode(SS, series["id"], 1, 6)
    a6 = anime_ep(SA, anime["id"], 6)
    feed_add(title="Breaking.Bad.S01E06.1080p.WEB-DL.DD5.1.H.264-MGTEST", categories=[5040], tvdbid=SERIES["tvdb"], season=1, episode=6)
    feed_add(title="[MGTest] Cowboy Bebop - 06 (WEB 1080p) [Dual Audio]", categories=[5070], tvdbid=ANIME["tvdb"], season=a6["seasonNumber"], episode=a6["episodeNumber"], absolute=6)
    stub_rules({"hook": "pre-grab", "delay_ms": 4000})
    t = time.time()
    rel = next(r for r in SS.get("/release", episodeId=e6["id"]) if "S01E06" in r["title"])
    open_s = time.time() - t
    log("fail-open (sonarr-series): %.1fs approved=%s rejections=%s" % (open_s, rel["approved"], rel["rejections"]))
    assert rel["approved"] is True
    t = time.time()
    rel = next(r for r in SA.get("/release", episodeId=a6["id"]) if "Bebop - 06" in r["title"])
    closed_s = time.time() - t
    log("fail-closed (sonarr-anime): %.1fs approved=%s rejections=%s" % (closed_s, rel["approved"], rel["rejections"]))
    assert rel["approved"] is False
    assert any(r.startswith("MediaGuard: MediaGuard niet bereikbaar") and r.endswith("[MEDIAGUARD_UNAVAILABLE]") for r in rel["rejections"])
    assert open_s < 15 and closed_s < 15                     # bounded: never waits for the 4s stub
    # HTTP 500 from core is treated the same way (radarr-animefilms = closed, radarr-films = open)
    stub_rules({"hook": "pre-grab", "status": 500})
    feed_add(title="My.Neighbor.Totoro.1988.1080p.BluRay.x264-MGTEST", categories=[2040], tmdbid=ANIMEFILM2["tmdb"], imdbid="tt0096283")
    feed_add(title="Big.Buck.Bunny.2008.720p.BluRay.x264-MGTEST", size=400_000_000, categories=[2040], tmdbid=FILM2["tmdb"], imdbid="tt1254207")
    rel = next(r for r in RA.get("/release", movieId=STATE["animefilm2"]["id"]) if r["title"].startswith("My.Neighbor"))
    log("radarr-animefilms (closed) on HTTP 500:", rel["rejections"])
    assert any("[MEDIAGUARD_UNAVAILABLE]" in r for r in rel["rejections"])
    rel = next(r for r in RF.get("/release", movieId=STATE["film2"]["id"]) if r["title"].startswith("Big.Buck.Bunny"))
    log("radarr-films (open) on HTTP 500:", rel["approved"], rel["rejections"])
    assert rel["approved"] is True
    logs = subprocess.run(["docker", "logs", SA.container()], capture_output=True, text=True).stdout
    warn = [l for l in logs.splitlines() if "MediaGuard pre-grab hook unavailable" in l]
    log("fork log warning:", warn[-1] if warn else None)
    assert warn


# --------------------------------------------------------------------------------------------- pre-import hook
def test_preimport_reject_prevents_import_then_accept_imports():
    series = STATE["series"]
    e5 = episode(SS, series["id"], 1, 5)
    monitor_episode(SS, e5["id"])
    title = "Breaking.Bad.S01E05.1080p.WEB-DL.DD5.1.H.264-MGTEST"
    feed_add(title=title, categories=[5040], tvdbid=SERIES["tvdb"], season=1, episode=5)
    stub_rules({"hook": "pre-import", "title_contains": "S01E05", "decision": "reject",
                "reason_code": "IDENTITY_MISMATCH", "message": "Aflevering-identiteit twijfelachtig"})
    SS.command("RssSync")
    wait_for(lambda: [h for h in SS.history("grabbed") if h["sourceTitle"] == title], timeout=60, desc="grab S01E05")

    def blocked():
        SS.command("RefreshMonitoredDownloads", wait=True)
        q = SS.get("/queue", pageSize=50)["records"]
        item = next((r for r in q if r["title"] == title), None)
        msgs = [m for s in (item or {}).get("statusMessages", []) for m in s.get("messages", [])]
        return item if any("MediaGuard: Aflevering-identiteit twijfelachtig [IDENTITY_MISMATCH]" in m for m in msgs) else None
    item = wait_for(blocked, timeout=180, interval=5, desc="import blocked by MediaGuard")
    log("queue item:", item["trackedDownloadStatus"], item["trackedDownloadState"], item["statusMessages"])
    assert SS.get(f"/episode/{e5['id']}")["hasFile"] is False
    assert not any("S01E05" in f for f in files_under(SS.media_dir))
    n_calls = len([c for c in stub_calls(hook="pre-import") if "S01E05" in c["title"]])
    assert n_calls >= 1
    # defer behaves the same (temporary), then accept -> import happens on the next cycle
    stub_rules({"hook": "pre-import", "title_contains": "S01E05", "decision": "defer", "reason_code": "AV1_ENCODE_RUNNING",
                "message": "Later opnieuw"})
    SS.command("RefreshMonitoredDownloads", wait=True)
    time.sleep(2)
    assert SS.get(f"/episode/{e5['id']}")["hasFile"] is False
    stub_rules()
    wait_imported_episode(SS, e5["id"])
    log("after accept:", [f for f in files_under(SS.media_dir) if "S01E05" in f])
    decisions = [c["decision"]["decision"] for c in stub_calls(hook="pre-import") if "S01E05" in c["title"]]
    log("pre-import decisions for S01E05:", decisions)
    assert "reject" in decisions and "defer" in decisions and decisions[-1] == "accept"


# --------------------------------------------------------------------------------------------- shadow mode + isolation
def started_at(arr):
    return json.loads(subprocess.check_output(["docker", "inspect", arr.container()]))[0]["State"]["StartedAt"]


def test_shadow_mode_blocks_all_grabs_and_restart_is_isolated():
    others = (SS, SA, RF)
    before = {a.name: (started_at(a), a.get("/system/status")["startTime"]) for a in others}
    env = dict(os.environ, ANIMEFILMS_SHADOW_MODE="1")
    cmd = [c for c in (RUN / "compose-cmd").read_text().split("\n") if c]
    subprocess.run(cmd + ["up", "-d", "radarr-animefilms"], cwd=HERE, env=env, check=True, capture_output=True)
    wait_for(lambda: requests.get(f"http://127.0.0.1:{RA.port}/ping", timeout=5).ok, timeout=180, desc="RA restart")
    wait_for(lambda: RA.get("/system/status"), timeout=60)
    after = {a.name: (started_at(a), a.get("/system/status")["startTime"]) for a in others}
    assert before == after, (before, after)                 # other three untouched
    # config/DB persisted across container re-creation (own volume)
    assert {m["tmdbId"] for m in RA.get("/movie")} == {ANIMEFILM["tmdb"], ANIMEFILM2["tmdb"]}
    requests.delete(f"{STUB}/admin/calls", timeout=10)
    feed_add(title="My.Neighbor.Totoro.1988.1080p.BluRay.x264-MGTEST", categories=[2040], tmdbid=ANIMEFILM2["tmdb"], imdbid="tt0096283")
    feed_add(title="My.Neighbor.Totoro.1988.2160p.UHD.BluRay.x265-MGTEST", categories=[2045], tmdbid=ANIMEFILM2["tmdb"], imdbid="tt0096283")
    rels = [r for r in RA.get("/release", movieId=STATE["animefilm2"]["id"]) if r["title"].startswith("My.Neighbor")]
    for r in rels:
        log("shadow:", r["title"], r["rejections"])
        assert any("[MEDIAGUARD_SHADOW_MODE]" in x for x in r["rejections"])
    RA.command("RssSync")
    time.sleep(3)
    assert not [h for h in RA.history("grabbed") if "Totoro" in h["sourceTitle"]]
    assert [c for c in stub_calls(hook="pre-grab") if c["instance_id"] == "radarr-animefilms"] == []   # no network call
    # radarr-films (same image, no shadow mode) still grabs normally at the same time
    feed_add(title="Big.Buck.Bunny.2008.1080p.WEB-DL.x264-MGTEST", size=400_000_000, categories=[2040], tmdbid=FILM2["tmdb"], imdbid="tt1254207")
    RF.command("RssSync")
    wait_for(lambda: [h for h in RF.history("grabbed") if "Big.Buck.Bunny" in h["sourceTitle"]], timeout=60, desc="RF grab")
    # back to normal
    subprocess.run(cmd + ["up", "-d", "radarr-animefilms"], cwd=HERE, check=True, capture_output=True)
    wait_for(lambda: requests.get(f"http://127.0.0.1:{RA.port}/ping", timeout=5).ok, timeout=180, desc="RA restart 2")


def test_suspended_instance_does_not_affect_others():
    """docker stop sonarr-anime (suspend-like); sonarr-series keeps grabbing/importing; anime state survives."""
    series, anime = STATE["series"], STATE["anime"]
    anime_count = len(SA.get("/series"))
    compose("stop", "sonarr-anime")
    STATE["stopped"] = {"sonarr-anime"}
    try:
        e7 = episode(SS, series["id"], 1, 7)
        monitor_episode(SS, e7["id"])
        feed_add(title="Breaking.Bad.S01E07.1080p.WEB-DL.DD5.1.H.264-MGTEST", categories=[5040], tvdbid=SERIES["tvdb"], season=1, episode=7)
        feed_add(title="[MGTest] Cowboy Bebop - 07 (WEB 1080p) [Dual Audio]", categories=[5070], tvdbid=ANIME["tvdb"], absolute=7)
        rss_sync_all()
        wait_imported_episode(SS, e7["id"])
        assert not [j for j in sab_jobs() if "Bebop - 07" in j["name"]]
        log("sonarr-series imported S01E07 while sonarr-anime was stopped")
    finally:
        compose("start", "sonarr-anime")
        STATE["stopped"] = set()
    wait_for(lambda: requests.get(f"http://127.0.0.1:{SA.port}/ping", timeout=5).ok, timeout=180, desc="SA start")
    wait_for(lambda: SA.get("/system/status"), timeout=60)
    assert len(SA.get("/series")) == anime_count
    assert anime_ep(SA, anime["id"], 5)["hasFile"] is True
    log("sonarr-anime resumed with its own state intact")


# --------------------------------------------------------------------------------------------- events + auth
def test_events_via_stock_webhook_and_hook_auth():
    for arr in INSTANCES.values():
        notif = arr.get("/notification")[0]
        arr.post("/notification/test", notif)
    events = stub_calls(hook="arr-webhook")
    by = {}
    for e in events:
        by.setdefault(e["instance_id"], set()).add((e["payload"] or {}).get("eventType"))
    log("webhook events per instance:", {k: sorted(filter(None, v)) for k, v in by.items()})
    assert all(e["key_ok"] for e in events)
    assert {"Grab", "Download", "Test"} <= by["sonarr-series"]
    assert {"Grab", "Test"} <= by["radarr-films"]
    for arr in INSTANCES.values():
        assert "Test" in by[arr.name]
    # every hook call carried the right per-instance key
    hooks = stub_calls(hook="pre-grab") + stub_calls(hook="pre-import")
    assert hooks and all(c["key_ok"] for c in hooks)
