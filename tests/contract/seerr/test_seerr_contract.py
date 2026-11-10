"""Seerr compatibility contract tests (MEDIAGUARD_GOAL DEEL H, R4, R6.6, Q3.12).

A real, unmodified Seerr talks to the four MediaGuard fork instances exactly as it would in
production; MediaGuard core's route test then runs against that same Seerr.

Run via tests/contract/seerr/run.sh (starts the compose stack). Tests run in file order and
share state (STATE): later tests build on the servers/requests created by earlier ones.
"""
import json
import os
import shutil
import socket
import subprocess
import tempfile
import time
from pathlib import Path

import pytest
import requests

HERE = Path(__file__).resolve().parent
RUN = HERE / ".run"
REPO = HERE.parents[2]
SEERR = "http://127.0.0.1:25055/api/v1"
JELLYFIN = "http://127.0.0.1:28096"
def _free_port():
    with socket.socket() as sock:
        sock.bind(("127.0.0.1", 0))
        return sock.getsockname()[1]


CORE_PORT = int(os.environ.get("MG_CORE_PORT") or _free_port())
CORE = f"http://127.0.0.1:{CORE_PORT}"
CORE_PYTHON = os.environ.get("MG_CORE_PYTHON", "/tmp/corevenv/bin/python")
CORE_DIR = Path(os.environ.get("MG_CORE_DIR") or REPO / "apps" / "core")   # override to test a patched core
JF_USER, JF_PASS = "mgadmin", "MgJellyfin-Test-1"

# titles (TMDB ids as Seerr uses them; tvdb ids as Sonarr uses them)
FILM = {"title": "Night of the Living Dead", "tmdb": 10331, "year": 1968}
FILM_PREEXISTING = {"title": "Big Buck Bunny", "tmdb": 10378, "year": 2008}
ANIMEFILM = {"title": "Spirited Away", "tmdb": 129, "year": 2001}
SERIES = {"title": "Breaking Bad", "tmdb": 1396, "tvdb": 81189}
ANIME = {"title": "Cowboy Bebop", "tmdb": 30991, "tvdb": 76885}
ANIME_NO_SERVER = {"title": "Neon Genesis Evangelion", "tmdb": 890, "tvdb": 70350}

STATE = {}
RESULTS = []   # (test, assertion, ok, detail) -> printed and written to .run/assertions.json


def log(*args):
    print("   ", *args, flush=True)


def check(name, cond, detail=""):
    """Record every contract assertion (for RESULTS.md), then assert it."""
    RESULTS.append({"test": os.environ.get("PYTEST_CURRENT_TEST", "?").split(" ")[0].split("::")[-1],
                    "assertion": name, "ok": bool(cond), "detail": str(detail)[:400]})
    log(("PASS" if cond else "FAIL"), name, "|", str(detail)[:300])
    assert cond, f"{name}: {detail}"


def wait_for(fn, timeout=120, interval=2, desc="condition"):
    deadline = time.time() + timeout
    last = None
    while time.time() < deadline:
        try:
            last = fn()
            if last:
                return last
        except Exception as ex:  # noqa: BLE001
            last = ex
        time.sleep(interval)
    raise AssertionError(f"timeout waiting for {desc}: last={last!r}")


# --------------------------------------------------------------------------------------------- clients
class Arr:
    def __init__(self, name, app, host_port, port, apikey, root, profile_name, anime):
        self.name, self.app, self.port, self.apikey = name, app, port, apikey
        self.host_port = host_port
        self.base = f"http://127.0.0.1:{host_port}/api/v3"
        self.root, self.profile_name, self.anime = root, profile_name, anime
        self.media_dir = RUN / "media" / name
        self.s = requests.Session()
        self.s.headers["X-Api-Key"] = apikey

    @property
    def kind(self):
        return self.app

    @property
    def internal_url(self):
        return f"http://{self.name}:{self.port}"

    def get(self, path, **params):
        r = self.s.get(self.base + path, params=params, timeout=120)
        r.raise_for_status()
        return r.json()

    def post(self, path, body, **params):
        r = self.s.post(self.base + path, json=body, params=params, timeout=120)
        if r.status_code >= 400:
            raise AssertionError(f"{self.name} POST {path} -> {r.status_code} {r.text[:800]}")
        return r.json() if r.text else None

    def command(self, name, wait=True, **body):
        cmd = self.post("/command", dict(name=name, **body))
        if wait:
            wait_for(lambda: self.get(f"/command/{cmd['id']}")["status"] in ("completed", "failed"),
                     timeout=180, interval=1, desc=f"{self.name} {name}")
        return cmd

    def items(self):
        return self.get("/series" if self.app == "sonarr" else "/movie")

    def by_tmdb(self, tmdb):
        return [m for m in self.get("/movie") if m["tmdbId"] == tmdb]

    def by_tvdb(self, tvdb):
        return [s for s in self.get("/series") if s["tvdbId"] == tvdb]


FORKS = {
    "sonarr-series": Arr("sonarr-series", "sonarr", 28989, 8989, "5e1a0000000000000000000000000001",
                         "/media/tv", "HD-1080p", False),
    "sonarr-anime": Arr("sonarr-anime", "sonarr", 28990, 8990, "5e1a0000000000000000000000000002",
                        "/media/anime", "HD - 720p/1080p", True),
    "radarr-films": Arr("radarr-films", "radarr", 27878, 7878, "5e1a0000000000000000000000000003",
                        "/media/movies", "HD-1080p", False),
    "radarr-animefilms": Arr("radarr-animefilms", "radarr", 27879, 7879, "5e1a0000000000000000000000000004",
                             "/media/animefilms", "HD - 720p/1080p", True),
}
SS, SA, RF, RA = (FORKS[k] for k in ("sonarr-series", "sonarr-anime", "radarr-films", "radarr-animefilms"))


class Seerr:
    def __init__(self):
        self.s = requests.Session()

    def set_key(self, key):
        self.s.headers["X-Api-Key"] = key

    def call(self, method, path, body=None, **params):
        return self.s.request(method, SEERR + path, json=body, params=params or None, timeout=120)

    def get(self, path, **params):
        r = self.call("GET", path, **params)
        assert r.status_code == 200, f"Seerr GET {path} -> {r.status_code} {r.text[:500]}"
        return r.json()

    def post(self, path, body=None, expect=(200, 201)):
        r = self.call("POST", path, body)
        assert r.status_code in expect, f"Seerr POST {path} -> {r.status_code} {r.text[:800]}"
        return r.json() if r.text else None

    def put(self, path, body):
        r = self.call("PUT", path, body)
        assert r.status_code == 200, f"Seerr PUT {path} -> {r.status_code} {r.text[:800]}"
        return r.json()

    def media(self, media_type, tmdb):
        info = self.get(f"/{media_type}/{tmdb}")
        return info.get("mediaInfo") or {}

    def run_job(self, job_id):
        before = {j["id"]: j for j in self.get("/settings/jobs")}[job_id]
        self.post(f"/settings/jobs/{job_id}/run")
        # wait until it ran (running flag back to false)
        time.sleep(2)
        wait_for(lambda: not {j["id"]: j for j in self.get("/settings/jobs")}[job_id]["running"],
                 timeout=240, interval=2, desc=f"seerr job {job_id}")
        return before


SEERR_API = Seerr()


def compose(*args, check_=True):
    cmd = [c for c in (RUN / "compose-cmd").read_text().split("\n") if c]
    return subprocess.run(cmd + list(args), cwd=HERE, check=check_, capture_output=True, text=True)


# --------------------------------------------------------------------------------------------- setup
@pytest.fixture(scope="session", autouse=True)
def stack_ready():
    for arr in FORKS.values():
        wait_for(lambda: requests.get(f"http://127.0.0.1:{arr.host_port}/ping", timeout=5).status_code == 200,
                 timeout=240, desc=f"{arr.name} /ping")
        wait_for(lambda: arr.get("/system/status"), timeout=120, desc=f"{arr.name} status")
    # Jellyfin answers "Server is loading" (and resets connections) until its migrations are done
    wait_for(lambda: "Version" in requests.get(f"{JELLYFIN}/System/Info/Public", timeout=5).json(),
             timeout=240, desc="jellyfin")
    time.sleep(3)
    wait_for(lambda: requests.get(f"{SEERR}/status", timeout=5).ok, timeout=240, desc="seerr")
    yield
    out = RUN / "assertions.json"
    out.write_text(json.dumps({"state": {k: v for k, v in STATE.items() if k in (
        "seerr_version", "seerr_commit", "jellyfin_version", "fork_versions", "server_ids")},
        "assertions": RESULTS}, indent=1, default=str))


# --------------------------------------------------------------------------------------------- 1. init
def test_01_jellyfin_wizard_and_seerr_initialization():
    """Jellyfin startup wizard via its API, then Seerr's own setup via /auth/jellyfin."""
    info = requests.get(f"{JELLYFIN}/System/Info/Public", timeout=10).json()
    STATE["jellyfin_version"] = info["Version"]
    if not info.get("StartupWizardCompleted"):
        def jf(method, path, body=None):
            def go():
                r = requests.request(method, JELLYFIN + path, json=body, timeout=30)
                r.raise_for_status()
                return r
            return wait_for(go, timeout=60, interval=2, desc=f"jellyfin {method} {path}")
        jf("POST", "/Startup/Configuration", {"UICulture": "en-US", "MetadataCountryCode": "NL",
                                              "PreferredMetadataLanguage": "en"})
        jf("GET", "/Startup/User")      # creates the initial user
        jf("POST", "/Startup/User", {"Name": JF_USER, "Password": JF_PASS})
        jf("POST", "/Startup/RemoteAccess", {"EnableRemoteAccess": True, "EnableAutomaticPortMapping": False})
        jf("POST", "/Startup/Complete")
    info = requests.get(f"{JELLYFIN}/System/Info/Public", timeout=10).json()
    check("jellyfin wizard completed", info["StartupWizardCompleted"] is True, info["Version"])

    status = requests.get(f"{SEERR}/status", timeout=10).json()
    STATE["seerr_version"], STATE["seerr_commit"] = status["version"], status.get("commitTag")
    pub = requests.get(f"{SEERR}/settings/public", timeout=10).json()
    login = requests.Session()
    r = login.post(f"{SEERR}/auth/jellyfin", json={
        "username": JF_USER, "password": JF_PASS, "hostname": "jellyfin", "port": 8096,
        "useSsl": False, "urlBase": "", "email": "admin@mediaguard.test", "serverType": 2},
        timeout=60)
    check("seerr /auth/jellyfin creates admin from Jellyfin user", r.status_code == 200,
          f"{r.status_code} {r.text[:200]}")
    check("seerr admin user has ADMIN permission", r.json().get("permissions", 0) & 2 == 2, r.json().get("permissions"))
    main = login.get(f"{SEERR}/settings/main", timeout=30).json()
    if not pub.get("initialized"):
        login.post(f"{SEERR}/settings/initialize", timeout=30).raise_for_status()
    check("seerr initialized", requests.get(f"{SEERR}/settings/public", timeout=10).json()["initialized"] is True)
    SEERR_API.set_key(main["apiKey"])
    STATE["seerr_apikey"] = main["apiKey"]
    me = SEERR_API.get("/auth/me")
    check("seerr API key auth (X-Api-Key) works as admin", me["id"] == 1, me.get("displayName"))
    bad = requests.get(f"{SEERR}/settings/sonarr", headers={"X-Api-Key": "wrong"}, timeout=10)
    check("seerr rejects a wrong API key", bad.status_code in (401, 403), bad.status_code)
    log("Seerr", status, "Jellyfin", STATE["jellyfin_version"])


# --------------------------------------------------------------------------------------------- 2. forks
def test_02_prepare_fork_instances():
    """Root folder + a Seerr tag per fork instance (normal admin setup; no Seerr involvement)."""
    versions = {}
    for arr in FORKS.values():
        st = arr.get("/system/status")
        versions[arr.name] = f"{st['appName']} {st['version']} ({st.get('packageVersion')})"
        if not any(r["path"].rstrip("/") == arr.root for r in arr.get("/rootfolder")):
            arr.post("/rootfolder", {"path": arr.root})
        tag = next((t for t in arr.get("/tag") if t["label"] == "seerr"), None) or arr.post("/tag", {"label": "seerr"})
        profile = next(p for p in arr.get("/qualityprofile") if p["name"] == arr.profile_name)
        STATE[arr.name] = {"tag": tag["id"], "profile": profile["id"], "profile_name": profile["name"]}
    STATE["fork_versions"] = versions
    log(versions)
    check("4 fork instances prepared (root folder, tag, profile)",
          all(STATE[n]["profile"] for n in FORKS), {n: STATE[n] for n in FORKS})


# --------------------------------------------------------------------------------------------- 3. Seerr test + servers
def seerr_test_body(arr, api_key=None):
    return {"hostname": arr.name, "port": arr.port, "apiKey": api_key or arr.apikey,
            "useSsl": False, "baseUrl": ""}


def test_03_seerr_test_endpoint_accepts_every_fork():
    """POST /settings/{sonarr,radarr}/test: Seerr's own validation of our API (status, profiles,
    root folders, language profiles (v3 only), tags)."""
    for arr in FORKS.values():
        r = SEERR_API.call("POST", f"/settings/{arr.kind}/test", seerr_test_body(arr))
        check(f"{arr.name}: Seerr test endpoint 200", r.status_code == 200, f"{r.status_code} {r.text[:300]}")
        body = r.json()
        STATE.setdefault("seerr_test", {})[arr.name] = body
        check(f"{arr.name}: profiles visible to Seerr incl. '{arr.profile_name}'",
              STATE[arr.name]["profile"] in [p["id"] for p in body["profiles"]],
              [p["name"] for p in body["profiles"]])
        check(f"{arr.name}: root folder {arr.root} visible to Seerr",
              arr.root in [f["path"].rstrip("/") for f in body["rootFolders"]], body["rootFolders"])
        check(f"{arr.name}: tags visible to Seerr", "seerr" in [t["label"] for t in body["tags"]], body["tags"])
        check(f"{arr.name}: urlBase empty", body.get("urlBase") in ("", None), body.get("urlBase"))
        if arr.app == "sonarr":
            # Sonarr v4 -> Seerr does not ask for language profiles (null)
            check(f"{arr.name}: languageProfiles null for Sonarr v4", body["languageProfiles"] is None,
                  body["languageProfiles"])
    # error handling: wrong key / unreachable port -> Seerr reports failure (HTTP 500, no crash)
    r = SEERR_API.call("POST", "/settings/radarr/test", seerr_test_body(RF, api_key="0" * 32))
    check("wrong fork API key -> Seerr test fails cleanly", r.status_code == 500, f"{r.status_code} {r.text[:200]}")
    r = SEERR_API.call("POST", "/settings/sonarr/test", dict(seerr_test_body(SS), port=9999))
    check("unreachable fork -> Seerr test fails cleanly", r.status_code == 500, f"{r.status_code} {r.text[:200]}")


def server_body(arr, *, is_default, monitor_new="none", series_type="standard"):
    st = STATE[arr.name]
    body = {
        "name": arr.name, "hostname": arr.name, "port": arr.port, "apiKey": arr.apikey,
        "useSsl": False, "baseUrl": "", "activeProfileId": st["profile"],
        "activeProfileName": st["profile_name"], "activeDirectory": arr.root, "tags": [st["tag"]],
        "is4k": False, "isDefault": is_default, "externalUrl": f"http://127.0.0.1:{arr.host_port}",
        "syncEnabled": True, "preventSearch": False, "tagRequests": False, "overrideRule": [],
    }
    if arr.app == "radarr":
        body["minimumAvailability"] = "released"
    else:
        body.update(seriesType=series_type, animeSeriesType="anime", enableSeasonFolders=True,
                    monitorNewItems=monitor_new)   # Sonarr v4: no language profile
        if arr.anime:
            # the anime instance uses the same profile/root for Seerr's "anime" branch too
            body.update(activeAnimeProfileId=st["profile"], activeAnimeProfileName=st["profile_name"],
                        activeAnimeDirectory=arr.root, animeTags=[st["tag"]])
    return body


def test_04_configure_four_servers_in_seerr():
    """R4 routing as Seerr supports it: default Sonarr = sonarr-series, second (non-default)
    Sonarr = sonarr-anime; default Radarr = radarr-films, second Radarr = radarr-animefilms."""
    ids = {}
    for arr, kw in ((SS, dict(is_default=True, monitor_new="none")),
                    (SA, dict(is_default=False, monitor_new="all", series_type="anime")),
                    (RF, dict(is_default=True)),
                    (RA, dict(is_default=False))):
        r = SEERR_API.call("POST", f"/settings/{arr.kind}", server_body(arr, **kw))
        check(f"{arr.name}: POST /settings/{arr.kind} -> 201", r.status_code == 201, f"{r.status_code} {r.text[:200]}")
        ids[arr.name] = r.json()["id"]
    STATE["server_ids"] = ids
    sonarr = SEERR_API.get("/settings/sonarr")
    radarr = SEERR_API.get("/settings/radarr")
    by = {s["name"]: s for s in sonarr + radarr}
    check("Seerr lists 2 Sonarr + 2 Radarr servers", len(sonarr) == 2 and len(radarr) == 2,
          [(s["id"], s["name"], s["isDefault"]) for s in sonarr + radarr])
    check("exactly one default non-4K server per type",
          [s["name"] for s in sonarr if s["isDefault"]] == ["sonarr-series"]
          and [s["name"] for s in radarr if s["isDefault"]] == ["radarr-films"])
    check("Seerr returns the server API key unmasked to admins (core route test can compare keys)",
          by["sonarr-series"]["apiKey"] == SS.apikey)
    # Seerr's per-server endpoints used by the request modal (server selection UI)
    for arr in FORKS.values():
        svc = SEERR_API.get(f"/service/{arr.kind}/{ids[arr.name]}")
        check(f"{arr.name}: GET /service/{arr.kind}/{{id}} (request modal data)",
              STATE[arr.name]["profile"] in [p["id"] for p in svc["profiles"]]
              and arr.root in [f["path"].rstrip("/") for f in svc["rootFolders"]],
              {"server": svc["server"]["name"], "profiles": len(svc["profiles"])})


# --------------------------------------------------------------------------------------------- 4. requests
def wait_movie(arr, tmdb, timeout=120):
    return wait_for(lambda: arr.by_tmdb(tmdb), timeout=timeout, interval=2, desc=f"{arr.name} movie {tmdb}")[0]


def wait_series(arr, tvdb, timeout=180):
    return wait_for(lambda: arr.by_tvdb(tvdb), timeout=timeout, interval=2, desc=f"{arr.name} series {tvdb}")[0]


def wait_media_linked(media_type, tmdb, server_name, timeout=90):
    sid = STATE["server_ids"][server_name]

    def ok():
        m = SEERR_API.media(media_type, tmdb)
        return m if m.get("externalServiceId") and m.get("serviceId") == sid else None
    return wait_for(ok, timeout=timeout, desc=f"seerr media {media_type}/{tmdb} linked")


def request(body, expect=(201,)):
    r = SEERR_API.call("POST", "/request", body)
    assert r.status_code in expect, f"POST /request {body} -> {r.status_code} {r.text[:500]}"
    return r


def test_05_request_movie_default_server_radarr_films():
    r = request({"mediaType": "movie", "mediaId": FILM["tmdb"]}).json()
    check("movie request auto-approved (admin)", r["status"] == 2, r["status"])
    m = wait_movie(RF, FILM["tmdb"])
    st = STATE["radarr-films"]
    check("movie added to radarr-films", m["title"] == FILM["title"], m["title"])
    check("radarr-films: rootFolderPath", m["rootFolderPath"].rstrip("/") == "/media/movies", m["rootFolderPath"])
    check("radarr-films: qualityProfileId = Seerr activeProfileId", m["qualityProfileId"] == st["profile"],
          m["qualityProfileId"])
    check("radarr-films: monitored", m["monitored"] is True)
    check("radarr-films: minimumAvailability from Seerr server", m["minimumAvailability"] == "released",
          m["minimumAvailability"])
    check("radarr-films: Seerr server tag applied", st["tag"] in m["tags"], m["tags"])
    check("movie NOT added to radarr-animefilms", RA.by_tmdb(FILM["tmdb"]) == [])
    media = wait_media_linked("movie", FILM["tmdb"], "radarr-films")
    check("Seerr media.externalServiceId = Radarr movie id", media["externalServiceId"] == m["id"],
          (media["externalServiceId"], m["id"]))
    check("Seerr media status processing (3)", media["status"] == 3, media["status"])
    STATE["film_req"] = r["id"]
    STATE["film_movie"] = m


def test_06_request_anime_movie_with_server_selection_radarr_animefilms():
    """Seerr's request modal sends serverId for a non-default server; profile/root come from
    that server's defaults."""
    sid = STATE["server_ids"]["radarr-animefilms"]
    r = request({"mediaType": "movie", "mediaId": ANIMEFILM["tmdb"], "serverId": sid}).json()
    check("anime-film request stored with serverId", r.get("serverId") == sid, r.get("serverId"))
    m = wait_movie(RA, ANIMEFILM["tmdb"])
    st = STATE["radarr-animefilms"]
    check("anime film added to radarr-animefilms", m["title"] == ANIMEFILM["title"], m["title"])
    check("radarr-animefilms: rootFolderPath", m["rootFolderPath"].rstrip("/") == "/media/animefilms",
          m["rootFolderPath"])
    check("radarr-animefilms: qualityProfileId", m["qualityProfileId"] == st["profile"], m["qualityProfileId"])
    check("radarr-animefilms: tag", st["tag"] in m["tags"], m["tags"])
    check("anime film NOT added to radarr-films", RF.by_tmdb(ANIMEFILM["tmdb"]) == [])
    media = wait_media_linked("movie", ANIMEFILM["tmdb"], "radarr-animefilms")
    check("Seerr media.serviceId = radarr-animefilms server", media["serviceId"] == sid)
    STATE["animefilm_req"] = r["id"]


def season_map(series):
    return {s["seasonNumber"]: s["monitored"] for s in series["seasons"]}


def test_07_request_series_specific_seasons_sonarr_series():
    r = request({"mediaType": "tv", "mediaId": SERIES["tmdb"], "tvdbId": SERIES["tvdb"],
                 "seasons": [1, 2]}).json()
    check("series request has seasons 1,2", sorted(s["seasonNumber"] for s in r["seasons"]) == [1, 2],
          r["seasons"])
    s = wait_series(SS, SERIES["tvdb"])
    s = wait_for(lambda: (lambda x: x if len(x["seasons"]) >= 5 else None)(SS.get(f"/series/{s['id']}")),
                 timeout=120, desc="seasons")
    st = STATE["sonarr-series"]
    seasons = season_map(s)
    check("series added to sonarr-series", s["title"] == SERIES["title"], s["title"])
    check("sonarr-series: rootFolderPath", s["rootFolderPath"].rstrip("/") == "/media/tv", s["rootFolderPath"])
    check("sonarr-series: qualityProfileId", s["qualityProfileId"] == st["profile"], s["qualityProfileId"])
    check("sonarr-series: seriesType standard", s["seriesType"] == "standard", s["seriesType"])
    check("sonarr-series: seasonFolder", s["seasonFolder"] is True)
    check("sonarr-series: series monitored", s["monitored"] is True)
    check("sonarr-series: requested seasons 1,2 monitored", seasons.get(1) is True and seasons.get(2) is True, seasons)
    check("sonarr-series: other seasons (0,3,4,5) unmonitored",
          not any(v for k, v in seasons.items() if k not in (1, 2)), seasons)
    check("sonarr-series: future-season policy monitorNewItems=none (from Seerr server)",
          s.get("monitorNewItems") == "none", s.get("monitorNewItems"))
    check("sonarr-series: tag", st["tag"] in s["tags"], s["tags"])
    eps = wait_for(lambda: SS.get("/episode", seriesId=s["id"]), timeout=120, desc="episodes")
    mon = {}
    for e in eps:
        mon.setdefault(e["seasonNumber"], set()).add(e["monitored"])
    check("episodes of S1/S2 monitored, S3+ unmonitored",
          mon.get(1) == {True} and mon.get(2) == {True} and mon.get(3) == {False}, mon)
    check("series NOT added to sonarr-anime", SA.by_tvdb(SERIES["tvdb"]) == [])
    media = wait_media_linked("tv", SERIES["tmdb"], "sonarr-series")
    check("Seerr media.externalServiceId = Sonarr series id", media["externalServiceId"] == s["id"])
    STATE["series"] = s
    STATE["series_req"] = r["id"]


def test_08_request_anime_series_with_server_selection_sonarr_anime():
    sid = STATE["server_ids"]["sonarr-anime"]
    r = request({"mediaType": "tv", "mediaId": ANIME["tmdb"], "tvdbId": ANIME["tvdb"],
                 "seasons": [1], "serverId": sid}).json()
    s = wait_series(SA, ANIME["tvdb"])
    st = STATE["sonarr-anime"]
    check("anime added to sonarr-anime", s["title"] == ANIME["title"], s["title"])
    check("sonarr-anime: rootFolderPath", s["rootFolderPath"].rstrip("/") == "/media/anime", s["rootFolderPath"])
    check("sonarr-anime: qualityProfileId (anime profile)", s["qualityProfileId"] == st["profile"], s["qualityProfileId"])
    check("sonarr-anime: seriesType anime", s["seriesType"] == "anime", s["seriesType"])
    check("sonarr-anime: season 1 monitored", season_map(s).get(1) is True, season_map(s))
    check("sonarr-anime: monitorNewItems=all (anime server policy)", s.get("monitorNewItems") == "all",
          s.get("monitorNewItems"))
    check("anime NOT added to sonarr-series", SS.by_tvdb(ANIME["tvdb"]) == [])
    media = wait_media_linked("tv", ANIME["tmdb"], "sonarr-anime")
    check("Seerr media.serviceId = sonarr-anime server", media["serviceId"] == sid)
    STATE["anime_req"] = r["id"]


def test_09_anime_without_server_selection_lands_on_default_server():
    """Documents R4 limitation: Seerr has no automatic per-server anime routing. An anime
    series requested without serverId goes to the DEFAULT Sonarr server; Seerr only switches
    to seriesType/anime profile+root *within* that server (TMDB keyword 210024)."""
    r = request({"mediaType": "tv", "mediaId": ANIME_NO_SERVER["tmdb"], "tvdbId": ANIME_NO_SERVER["tvdb"],
                 "seasons": [1]}).json()
    s = wait_series(SS, ANIME_NO_SERVER["tvdb"])
    check("anime w/o serverId -> default server sonarr-series (no auto-routing)", bool(s), s["title"])
    check("... but Seerr detected anime: seriesType anime", s["seriesType"] == "anime", s["seriesType"])
    check("... with the default server's root (no activeAnimeDirectory on sonarr-series)",
          s["rootFolderPath"].rstrip("/") == "/media/tv", s["rootFolderPath"])
    check("... and NOT in sonarr-anime", SA.by_tvdb(ANIME_NO_SERVER["tvdb"]) == [])
    STATE["misrouted_anime"] = {"request": r["id"], "series": s["id"]}


def test_10_rerequest_existing_titles_no_duplicates():
    # a) same movie again -> Seerr refuses (409), Radarr unchanged
    r = request({"mediaType": "movie", "mediaId": FILM["tmdb"]}, expect=(409, 202, 400, 500))
    check("re-request of requested movie refused by Seerr", r.status_code == 409, f"{r.status_code} {r.text[:200]}")
    check("radarr-films still exactly 1 copy", len(RF.by_tmdb(FILM["tmdb"])) == 1)
    # b) same seasons again -> refused (no seasons left)
    r = request({"mediaType": "tv", "mediaId": SERIES["tmdb"], "tvdbId": SERIES["tvdb"], "seasons": [1, 2]},
                expect=(202, 409, 400, 500))
    check("re-request of already requested seasons refused", r.status_code in (202, 409),
          f"{r.status_code} {r.text[:200]}")
    # c) an extra season of an existing series -> Seerr updates the existing Sonarr series (PUT)
    r = request({"mediaType": "tv", "mediaId": SERIES["tmdb"], "tvdbId": SERIES["tvdb"], "seasons": [3]})
    sid = STATE["series"]["id"]
    s = wait_for(lambda: (lambda x: x if season_map(x).get(3) else None)(SS.get(f"/series/{sid}")),
                 timeout=120, desc="season 3 monitored")
    seasons = season_map(s)
    check("extra season 3 monitored on the SAME Sonarr series", seasons.get(3) is True and s["id"] == sid, seasons)
    check("seasons 1,2 still monitored, 4,5 not", seasons.get(1) and seasons.get(2) and not seasons.get(4)
          and not seasons.get(5), seasons)
    check("sonarr-series still exactly 1 copy of the series", len(SS.by_tvdb(SERIES["tvdb"])) == 1)
    STATE["series_req_s3"] = r.json()["id"]
    # d) a title that already exists in the fork (added outside Seerr, unmonitored) -> Seerr
    #    recognizes it and re-monitors instead of adding a duplicate
    lookup = RF.get("/movie/lookup/tmdb", tmdbId=FILM_PREEXISTING["tmdb"])
    pre = RF.post("/movie", dict(lookup, qualityProfileId=STATE["radarr-films"]["profile"],
                                  rootFolderPath="/media/movies", monitored=False,
                                  minimumAvailability="released", addOptions={"searchForMovie": False}))
    r = request({"mediaType": "movie", "mediaId": FILM_PREEXISTING["tmdb"]})
    m = wait_for(lambda: (lambda x: x if x and x[0]["monitored"] else None)(RF.by_tmdb(FILM_PREEXISTING["tmdb"])),
                 timeout=120, desc="pre-existing movie re-monitored")
    check("pre-existing movie: no duplicate in radarr-films", len(m) == 1 and m[0]["id"] == pre["id"],
          [(x["id"], x["title"]) for x in m])
    check("pre-existing movie: Seerr re-monitored it", m[0]["monitored"] is True)
    media = wait_media_linked("movie", FILM_PREEXISTING["tmdb"], "radarr-films")
    check("pre-existing movie: Seerr linked to the existing Radarr id", media["externalServiceId"] == pre["id"],
          (media["externalServiceId"], pre["id"]))
    check("pre-existing movie: no copy in radarr-animefilms", RA.by_tmdb(FILM_PREEXISTING["tmdb"]) == [])


# --------------------------------------------------------------------------------------------- 5. availability
def place_file(arr, item_path, filename, fixture="sample.mkv"):
    rel = Path(item_path).relative_to(arr.root)
    dest_dir = arr.media_dir / rel
    dest_dir.mkdir(parents=True, exist_ok=True)
    dest = dest_dir / filename
    shutil.copyfile(RUN / "fixtures" / fixture, dest)
    os.chmod(dest, 0o666)
    for p in [dest_dir, *dest_dir.parents]:
        if p == arr.media_dir.parent:
            break
        try:
            os.chown(p, 1000, 1000)
            os.chown(dest, 1000, 1000)
        except PermissionError:
            pass
    return dest


def test_11_movie_becomes_available_via_radarr_scan():
    m = STATE["film_movie"]
    place_file(RF, m["path"], "Night.of.the.Living.Dead.1968.1080p.WEB-DL.AAC.x264-MGTEST.mkv")
    RF.command("RescanMovie", movieId=m["id"])
    movie = wait_for(lambda: (lambda x: x if x["hasFile"] else None)(RF.get(f"/movie/{m['id']}")),
                     timeout=120, desc="radarr hasFile")
    check("radarr-films sees the file (hasFile)", movie["hasFile"], movie["movieFile"]["relativePath"])
    SEERR_API.run_job("radarr-scan")
    media = wait_for(lambda: (lambda x: x if x.get("status") == 5 else None)(SEERR_API.media("movie", FILM["tmdb"])),
                     timeout=120, desc="seerr movie available")
    check("Seerr media status -> 5 (available) after radarr-scan", media["status"] == 5, media["status"])
    req = SEERR_API.get(f"/request/{STATE['film_req']}")
    check("Seerr request status -> 5 (completed)", req["status"] == 5, req["status"])
    # animefilm (no file) stays processing
    check("other movie (no file) stays processing", SEERR_API.media("movie", ANIMEFILM["tmdb"])["status"] == 3)


def test_12_still_available_after_av1_replacement():
    """MediaGuard's AV1 conversion replaces the file but keeps the Radarr movie id: Seerr's
    radarr-scan and availability-sync must keep the title available."""
    m = STATE["film_movie"]
    old = RF.get(f"/movie/{m['id']}")["movieFile"]
    # add the AV1 file first, then remove the H.264 one (atomic swap as MediaGuard does it)
    place_file(RF, m["path"], "Night.of.the.Living.Dead.1968.1080p.WEB-DL.AAC.AV1-MGTEST.mkv", "sample-av1.mkv")
    (RF.media_dir / Path(m["path"]).relative_to(RF.root) / old["relativePath"]).unlink()
    RF.command("RescanMovie", movieId=m["id"])
    movie = wait_for(lambda: (lambda x: x if x["hasFile"] and "AV1" in x["movieFile"]["relativePath"] else None)(
        RF.get(f"/movie/{m['id']}")), timeout=120, desc="radarr picked up AV1 file")
    codec = movie["movieFile"].get("mediaInfo", {}).get("videoCodec")
    check("radarr-films now has the AV1 file, same movie id", movie["id"] == m["id"],
          f"{movie['movieFile']['relativePath']} codec={codec}")
    SEERR_API.run_job("radarr-scan")
    SEERR_API.run_job("availability-sync")
    media = SEERR_API.media("movie", FILM["tmdb"])
    check("Seerr still available (5) after AV1 replacement + radarr-scan + availability-sync",
          media["status"] == 5, media["status"])
    check("Seerr still linked to the same Radarr id", media["externalServiceId"] == m["id"])


def test_13_series_partially_available_via_sonarr_scan():
    s = STATE["series"]
    eps = SS.get("/episode", seriesId=s["id"])
    e1 = next(e for e in eps if e["seasonNumber"] == 1 and e["episodeNumber"] == 1)
    place_file(SS, s["path"] + "/Season 01", "Breaking.Bad.S01E01.1080p.WEB-DL.AAC.x264-MGTEST.mkv")
    SS.command("RescanSeries", seriesId=s["id"])
    wait_for(lambda: SS.get(f"/episode/{e1['id']}")["hasFile"], timeout=120, desc="sonarr S01E01 hasFile")
    check("sonarr-series sees S01E01 file", True)
    SEERR_API.run_job("sonarr-scan")
    media = wait_for(lambda: (lambda x: x if x.get("status") == 4 else None)(SEERR_API.media("tv", SERIES["tmdb"])),
                     timeout=120, desc="seerr tv partially available")
    check("Seerr tv media status -> 4 (partially available)", media["status"] == 4, media["status"])
    seasons = {x["seasonNumber"]: x["status"] for x in media.get("seasons", [])}
    check("Seerr season 1 -> 4 (partial), season 2 not available", seasons.get(1) == 4 and seasons.get(2) in (2, 3),
          seasons)


# --------------------------------------------------------------------------------------------- 6. MediaGuard core
class Core:
    def __init__(self):
        self.s = requests.Session()
        self.proc = None
        self.tmp = None

    def start(self):
        self.tmp = Path(tempfile.mkdtemp(prefix="mg-core-seerr-"))
        env = dict(os.environ, MG_DATA_DIR=str(self.tmp / "data"), MG_STAGING_DIR=str(self.tmp / "staging"),
                   MG_QUARANTINE_DIR=str(self.tmp / "quarantine"), MG_START_BACKGROUND="0",
                   MG_BIND="127.0.0.1", MG_PORT=str(CORE_PORT), MG_DEV_AUTH_BYPASS="0")
        for d in ("data", "staging", "quarantine"):
            (self.tmp / d).mkdir()
        self.logf = open(RUN / "core.log", "w")
        self.proc = subprocess.Popen([CORE_PYTHON, "-m", "mediaguard"], cwd=CORE_DIR, env=env,
                                     stdout=self.logf, stderr=subprocess.STDOUT)
        wait_for(lambda: requests.get(f"{CORE}/api/v1/auth/setup-status", timeout=3).ok, timeout=60,
                 desc="core up")
        r = self.s.post(f"{CORE}/api/v1/auth/setup", json={"username": "mgadmin", "password": "MgCore-Test-123"})
        r.raise_for_status()
        self.s.headers["X-CSRF-Token"] = r.json()["csrf_token"]

    def stop(self):
        if self.proc:
            self.proc.terminate()
            try:
                self.proc.wait(15)
            except subprocess.TimeoutExpired:
                self.proc.kill()
            self.logf.close()
        if self.tmp:
            shutil.rmtree(self.tmp, ignore_errors=True)

    def call(self, method, path, body=None, **params):
        return self.s.request(method, CORE + path, json=body, params=params or None, timeout=180)

    def ok(self, method, path, body=None, **params):
        r = self.call(method, path, body, **params)
        assert r.status_code < 300, f"core {method} {path} -> {r.status_code} {r.text[:800]}"
        return r.json()


CORE_APP = Core()
MEDIA_TYPES = {"sonarr-series": "tv", "sonarr-anime": "anime", "radarr-films": "movie",
               "radarr-animefilms": "anime_movie"}


@pytest.fixture(scope="module")
def core():
    CORE_APP.start()
    yield CORE_APP
    CORE_APP.stop()


def route_report(core):
    rep = core.ok("POST", "/api/v1/seerr/route-test")
    by = {i["instance_id"]: i for i in rep["instances"]}
    for iid, inst in by.items():
        log(iid, "ok" if inst["ok"] else "NOT OK",
            [(c["check"], c["ok"]) for c in inst["checks"]])
    if rep["unmapped_servers"]:
        log("unmapped:", [(u["kind"], u["server"]["name"], u["url"]) for u in rep["unmapped_servers"]])
    return rep, by


def failed_checks(inst):
    return sorted(c["check"] for c in inst["checks"] if c["ok"] is False)


def test_14_core_route_test_all_four_routes_ok(core):
    # core seeds the four standard instances (R4) -> point them at this stack; url = how core
    # reaches the fork, public_url = how Seerr reaches it (what the route test compares)
    for arr in FORKS.values():
        conn = {"url": f"http://127.0.0.1:{arr.host_port}", "public_url": arr.internal_url,
                "api_key": arr.apikey, "verify": True}
        if core.call("GET", f"/api/v1/instances/{arr.name}").status_code == 200:
            core.ok("PATCH", f"/api/v1/instances/{arr.name}", conn)
        else:
            core.ok("POST", "/api/v1/instances", dict(conn, id=arr.name, app=arr.app,
                                                     media_type=MEDIA_TYPES[arr.name], name=arr.name))
    insts = {i["id"]: i for i in core.ok("GET", "/api/v1/instances")}
    check("core: 4 instances registered with Seerr-facing public_url",
          all(insts[n]["public_url"] == FORKS[n].internal_url for n in FORKS),
          {n: (insts[n]["url"], insts[n]["public_url"]) for n in FORKS})
    st = core.ok("PUT", "/api/v1/seerr/settings", {"url": "http://127.0.0.1:25055",
                                                   "api_key": STATE["seerr_apikey"]})
    check("core: Seerr settings saved after live verification", st["saved"] and st["seerr_version"] == STATE["seerr_version"],
          st.get("seerr_version"))
    t = core.ok("POST", "/api/v1/seerr/test")
    check("core: /seerr/test sees 2+2 servers, keys masked",
          len(t["sonarr_servers"]) == 2 and len(t["radarr_servers"]) == 2
          and all(s["apiKey"] == "********" for s in t["sonarr_servers"] + t["radarr_servers"]),
          [s["name"] for s in t["sonarr_servers"] + t["radarr_servers"]])
    rep, by = route_report(core)
    STATE["route_report_ok"] = rep
    for iid in FORKS:
        inst = by[iid]
        check(f"core route-test: {iid} OK", inst["ok"] is True, failed_checks(inst))
        names = {c["check"]: c["ok"] for c in inst["checks"]}
        check(f"core route-test: {iid} checks url/key/default/profile/root/seerr-test all true",
              all(names.get(k) is True for k in ("server_found", "url_matches", "api_key_matches", "is_default",
                                                 "is_4k", "profile:activeProfileId", "root:activeDirectory",
                                                 "seerr_connection_test")), names)
    check("core route-test: anime routes also verify activeAnime* on sonarr-anime",
          {c["check"]: c["ok"] for c in by["sonarr-anime"]["checks"]}.get("root:activeAnimeDirectory") is True)
    check("core route-test: overall ok, no unmapped servers", rep["ok"] is True and not rep["unmapped_servers"],
          rep["unmapped_servers"])
    mapping = core.ok("GET", "/api/v1/seerr/mapping")
    check("core /seerr/mapping (read-only) agrees", mapping["ok"] is True and mapping["tested"] is False)


def test_15_core_request_sync(core):
    counts = core.ok("POST", "/api/v1/seerr/requests/sync")
    log("sync counts", counts)
    reqs = core.ok("GET", "/api/v1/seerr/requests", page_size=100)
    items = {(i["media_type"], i["tmdb_id"]): i for i in reqs["items"]}
    check("core: all Seerr requests synced", counts["total"] == len(SEERR_API.get("/request", take=100)["results"]),
          counts)
    film = items.get(("movie", FILM["tmdb"]))
    check("core: movie request status/media status synced (5/5)",
          film and film["request_status"] == 5 and film["media_status"] == 5 and film["available_at"],
          film)
    # without core's library index the requests cannot be linked to instance items yet
    check("core: before library sync requests are 'unlinked' (no lib_items)", counts["linked"] == 0, counts)
    for arr in FORKS.values():
        core.ok("POST", f"/api/v1/instances/{arr.name}/sync", mode="inline")
    counts = core.ok("POST", "/api/v1/seerr/requests/sync")
    log("sync counts after library sync", counts)
    reqs = core.ok("GET", "/api/v1/seerr/requests", page_size=100)["items"]
    where = {(i["media_type"], i["tmdb_id"]): i["instance_id"] for i in reqs}
    expected = {("movie", FILM["tmdb"]): "radarr-films", ("movie", FILM_PREEXISTING["tmdb"]): "radarr-films",
                ("movie", ANIMEFILM["tmdb"]): "radarr-animefilms", ("tv", SERIES["tmdb"]): "sonarr-series",
                ("tv", ANIME["tmdb"]): "sonarr-anime", ("tv", ANIME_NO_SERVER["tmdb"]): "sonarr-series"}
    check("core: after library sync every request is linked", counts["linked"] == counts["total"], counts)
    check("core: each request linked to the instance that really holds the title",
          all(where.get(k) == v for k, v in expected.items()), where)
    check("core: Seerr serverId for non-default-server requests is recorded",
          next(i for i in reqs if i["tmdb_id"] == ANIMEFILM["tmdb"])["server_id"] == STATE["server_ids"]["radarr-animefilms"],
          [(i["tmdb_id"], i["server_id"]) for i in reqs])


def seerr_server(kind, name):
    return next(s for s in SEERR_API.get(f"/settings/{kind}") if s["name"] == name)


def seerr_update(kind, server, **changes):
    # Seerr's OpenAPI validator rejects 'id' in the PUT body ("request/body/id is read-only")
    body = {k: v for k, v in dict(server, **changes).items() if k != "id"}
    return SEERR_API.put(f"/settings/{kind}/{server['id']}", body)


def test_16_core_detects_misconfigured_seerr_servers(core):
    # a) radarr-animefilms server in Seerr wrongly points at radarr-films (old-system style mistake)
    orig_ra = seerr_server("radarr", "radarr-animefilms")
    seerr_update("radarr", orig_ra, hostname="radarr-films", port=7878, apiKey=RF.apikey,
                 activeDirectory="/media/movies", activeProfileId=STATE["radarr-films"]["profile"])
    rep, by = route_report(core)
    check("misconfig A: radarr-animefilms not OK (no Seerr server points to it)",
          by["radarr-animefilms"]["ok"] is False and "server_found" in failed_checks(by["radarr-animefilms"]),
          failed_checks(by["radarr-animefilms"]))
    check("misconfig A: the stray server is reported as unmapped / overall not OK",
          rep["ok"] is False and any(u["server"]["name"] == "radarr-animefilms" for u in rep["unmapped_servers"]),
          [u["url"] for u in rep["unmapped_servers"]])
    check("misconfig A: radarr-films itself still OK", by["radarr-films"]["ok"] is True, failed_checks(by["radarr-films"]))
    seerr_update("radarr", seerr_server("radarr", "radarr-animefilms"), **{k: orig_ra[k] for k in (
        "hostname", "port", "apiKey", "activeDirectory", "activeProfileId")})

    # b) wrong API key on sonarr-series (key of sonarr-anime)
    orig_ss = seerr_server("sonarr", "sonarr-series")
    seerr_update("sonarr", orig_ss, apiKey=SA.apikey)
    rep, by = route_report(core)
    fc = failed_checks(by["sonarr-series"])
    check("misconfig B: wrong API key detected (api_key_matches=false)", "api_key_matches" in fc, fc)
    seerr_update("sonarr", seerr_server("sonarr", "sonarr-series"), apiKey=orig_ss["apiKey"])

    # c) anime Sonarr made default -> Seerr clears sonarr-series default; both detected
    orig_sa = seerr_server("sonarr", "sonarr-anime")
    seerr_update("sonarr", orig_sa, isDefault=True)
    rep, by = route_report(core)
    check("misconfig C: sonarr-anime isDefault=true detected", "is_default" in failed_checks(by["sonarr-anime"]),
          failed_checks(by["sonarr-anime"]))
    check("misconfig C: sonarr-series lost default detected", "is_default" in failed_checks(by["sonarr-series"]),
          failed_checks(by["sonarr-series"]))
    seerr_update("sonarr", seerr_server("sonarr", "sonarr-anime"), isDefault=False)
    seerr_update("sonarr", seerr_server("sonarr", "sonarr-series"), isDefault=True)

    # d) root folder that does not exist in the instance
    orig_rf = seerr_server("radarr", "radarr-films")
    seerr_update("radarr", orig_rf, activeDirectory="/media/does-not-exist")
    rep, by = route_report(core)
    check("misconfig D: non-existent root folder detected", "root:activeDirectory" in failed_checks(by["radarr-films"]),
          failed_checks(by["radarr-films"]))
    seerr_update("radarr", seerr_server("radarr", "radarr-films"), activeDirectory=orig_rf["activeDirectory"])

    # e) wrong port (unreachable) -> URL mismatch + Seerr's own connection test fails
    orig_sa = seerr_server("sonarr", "sonarr-anime")
    seerr_update("sonarr", orig_sa, port=8999)
    rep, by = route_report(core)
    inst = by["sonarr-anime"]
    check("misconfig E: wrong port -> sonarr-anime not OK", inst["ok"] is False, failed_checks(inst))
    seerr_update("sonarr", seerr_server("sonarr", "sonarr-anime"), port=orig_sa["port"])

    # restored -> all OK again
    rep, by = route_report(core)
    check("after restoring Seerr settings: route-test OK again", rep["ok"] is True and all(
        by[i]["ok"] for i in FORKS), {i: failed_checks(by[i]) for i in FORKS})


def test_17_core_cutover_and_rollback_against_real_seerr(core):
    """Cutover (Q3.12 / DEEL H): the Seerr server for radarr-animefilms still points at the OLD
    Radarr; core's cutover must re-point it via Seerr's API (preview -> confirm) and rollback
    must restore the old values exactly."""
    orig = seerr_server("radarr", "radarr-animefilms")
    old_values = {"hostname": "radarr-old", "port": 7878, "apiKey": "0123456789abcdef0123456789abcdef"}
    seerr_update("radarr", orig, **old_values)
    preview = core.ok("POST", "/api/v1/seerr/cutover", {"instance_id": "radarr-animefilms",
                                                        "server_id": orig["id"]})
    check("cutover preview requires confirmation and shows the diff",
          preview.get("requires_confirmation") is True and {"hostname", "port", "apiKey"} <= set(preview["diff"]),
          preview.get("diff"))
    check("cutover preview did not change Seerr", seerr_server("radarr", "radarr-animefilms")["hostname"] == "radarr-old")
    r = core.call("POST", "/api/v1/seerr/cutover", {"instance_id": "radarr-animefilms",
                                                    "server_id": orig["id"], "confirm": True})
    check("cutover confirm accepted by real Seerr (PUT /settings/radarr/{id})", r.status_code == 200,
          f"{r.status_code} {r.text[:400]}")
    now = seerr_server("radarr", "radarr-animefilms")
    check("after cutover Seerr points at radarr-animefilms with its key",
          now["hostname"] == "radarr-animefilms" and now["port"] == RA.port and now["apiKey"] == RA.apikey,
          {k: now[k] for k in ("hostname", "port")})
    check("cutover kept profile/root/default flags",
          now["activeDirectory"] == orig["activeDirectory"] and now["activeProfileId"] == orig["activeProfileId"]
          and now["isDefault"] is False)
    rep, by = route_report(core)
    check("route-test OK after cutover", by["radarr-animefilms"]["ok"] is True, failed_checks(by["radarr-animefilms"]))
    rb = core.call("POST", "/api/v1/seerr/cutover/rollback", {"instance_id": "radarr-animefilms", "confirm": True})
    check("rollback accepted by real Seerr", rb.status_code == 200, f"{rb.status_code} {rb.text[:400]}")
    back = seerr_server("radarr", "radarr-animefilms")
    check("rollback restored the exact pre-cutover values",
          all(back[k] == v for k, v in old_values.items()), {k: back[k] for k in old_values})
    # leave the stack in the correct state
    seerr_update("radarr", seerr_server("radarr", "radarr-animefilms"),
                 **{k: orig[k] for k in ("hostname", "port", "apiKey")})
