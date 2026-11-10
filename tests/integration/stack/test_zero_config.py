"""Zero-config installation on real containers (run via run_zero_config.sh, ADR-011).

Nothing is pre-seeded: mediaguard-init generates every key. The test only learns the fork API
keys by reading the generated files (as a user never has to) to verify the forks independently.
Proves end to end: core ⇄ fork API keys, fork → core pre-grab hook keys (401-free), core ⇄
recovery ⇄ encoder internal token, init idempotent across `docker compose up`, and existing media
dirs/files never re-owned.
"""
from __future__ import annotations

import hashlib
import json
import re
import subprocess
import time

import pytest
import requests
import test_stack as ts
from test_stack import CORE, INDEXER, SAB, core, feed_add, log, wait_for

RUN = ts.RUN
assert RUN.name == ".run-zero", "run via tests/integration/stack/run_zero_config.sh"
SECRETS = RUN / "appdata" / "secrets"
INSTANCES = list(ts.INST)
EXISTING_DIR = RUN / "data" / "media" / "tv"
EXISTING_FILE = EXISTING_DIR / "Bestaande Serie" / "bestaand.mkv"
OTHER_UID = 1234
Z: dict = {}


def secret(*parts):
    return (SECRETS.joinpath(*parts)).read_text().strip()


def secret_digests():
    return {str(p.relative_to(SECRETS)): hashlib.sha256(p.read_bytes()).hexdigest()
            for p in sorted(SECRETS.rglob("*")) if p.is_file()}


def owner(p):
    st = p.stat()
    return st.st_uid, st.st_gid, oct(st.st_mode & 0o7777)


def init_state():
    info = json.loads(subprocess.check_output(["docker", "inspect", ts.container("mediaguard-init")]))[0]
    return info["State"]


def init_logs():
    return subprocess.run(["docker", "logs", ts.container("mediaguard-init")], check=True, capture_output=True,
                          text=True).stdout


def wait_stack():
    wait_for(lambda: requests.get(CORE + "/api/v1/health", timeout=5).ok, timeout=180, desc="core")
    for arr in ts.INST.values():
        wait_for(lambda arr=arr: requests.get(f"http://127.0.0.1:{arr.port}/ping", timeout=5).ok,
                 timeout=240, desc=f"{arr.iid} /ping")
    for url in (INDEXER, SAB):
        wait_for(lambda url=url: requests.get(url + "/health", timeout=5).ok, timeout=60, desc=url)


@pytest.fixture(scope="session", autouse=True)
def stack_ready():
    wait_stack()
    yield


@pytest.fixture(autouse=True)
def clean_feed():
    ts.feed_clear()
    yield


# --------------------------------------------------------------------------------------- tests
def test_z1_init_generated_all_secrets():
    st = init_state()
    assert st["Status"] == "exited" and st["ExitCode"] == 0, st
    out = init_logs()
    log("init log:", out.strip().replace("\n", " | ")[:900])
    assert "interne token gegenereerd" in out
    for iid in INSTANCES:
        assert f"API-sleutel gegenereerd voor {iid}" in out
        assert f"hook-sleutel gegenereerd voor {iid}" in out
    token = secret("internal", "token")
    assert re.fullmatch(r"[0-9a-f]{64}", token)
    values = [token]
    for iid in INSTANCES:
        api, hook = secret(iid, "apikey"), secret(iid, "hookkey")
        assert re.fullmatch(r"[0-9a-f]{32}", api), iid          # Sonarr/Radarr key format
        assert re.fullmatch(r"[0-9a-f]{64}", hook), iid
        values += [api, hook]
        for name in ("apikey", "hookkey"):
            assert owner(SECRETS / iid / name) == (568, 568, "0o640")
        # the fork really runs with the generated key (its own API accepts it)
        arr = ts.INST[iid]
        arr.s.headers["X-Api-Key"] = api
        assert arr.get("/system/status")["instanceName"]
    assert len(set(values)) == len(values), "every generated secret must be unique"
    assert owner(SECRETS / "internal" / "token") == (568, 568, "0o640")
    # app dirs created and owned by the app user
    for d in ("core", "encode", *INSTANCES):
        assert owner(RUN / "appdata" / d)[:2] == (568, 568), d
    for d in ("encode", "encode/staging", "encode/cache", "quarantine"):
        assert owner(RUN / "work" / d)[:2] == (568, 568), d
    Z["digests"] = secret_digests()


def test_z2_existing_media_never_reowned_and_layout_untouched():
    assert owner(EXISTING_DIR) == (OTHER_UID, OTHER_UID, "0o777")
    assert owner(EXISTING_FILE) == (OTHER_UID, OTHER_UID, "0o640")
    assert owner(EXISTING_FILE.parent)[:2] == (OTHER_UID, OTHER_UID)
    # bestaande data-dataset (niet leeg): init voegt niets toe aan de indeling van de gebruiker
    assert sorted(p.name for p in (RUN / "data").iterdir()) == ["downloads", "media"]
    assert sorted(p.name for p in (RUN / "data" / "media").iterdir()) == \
        ["anime", "animefilms", "movies", "tv"]
    Z["existing"] = (owner(EXISTING_DIR), owner(EXISTING_FILE))


def test_z3_setup_unlock_and_four_instances_authenticated():
    ts.test_a1_first_run_setup_login_and_csrf()     # also: library locked by default → unlock
    insts = core.get("/instances")
    assert [i["id"] for i in insts] == INSTANCES
    for i in insts:
        assert i["api_key_set"] and i["hook_key_set"], i
        assert not i["public_url"], i                    # no MG_HOST in a zero-config install
        res = core.post(f"/instances/{i['id']}/test")
        log(i["id"], "test ->", json.dumps(res)[:200])
        assert res["api_key_valid"] is True, res
    # Seerr: the UI passes window.location.hostname; MediaGuard fills the public URLs itself
    r = core.s.post(CORE + "/api/v1/provisioning/seerr/apply", json={"public_host": "127.0.0.1"},
                    timeout=60)
    log("seerr apply (no Seerr configured):", r.status_code, r.text[:200])
    urls = {i["id"]: i["public_url"] for i in core.get("/instances")}
    assert urls == {"sonarr-series": "http://127.0.0.1:38989", "sonarr-anime": "http://127.0.0.1:38990",
                    "radarr-films": "http://127.0.0.1:37878", "radarr-animefilms": "http://127.0.0.1:37879"}


def test_z4_four_forks_healthy_and_webhooks_reach_core():
    ts.test_a3_configure_four_instances_through_core_api()   # waits until all 4 are 'healthy'
    rows = {i["id"]: i for i in core.get("/instances")}
    assert all(r["status"] == "healthy" and r["api_key_valid"] for r in rows.values()), rows
    ts.test_a4_webhook_registration_via_core_and_test_event_reaches_core()


def _pre_grab_everywhere(tag):
    """Interactive search on each fork → the fork calls core's pre-grab hook with ITS generated
    hook key; a hook_decisions row only exists after successful authentication."""
    for iid in INSTANCES:
        arr = ts.INST[iid]
        if arr.app == "sonarr":
            show = ts.SERIES if iid == "sonarr-series" else ts.ANIME
            key = f"series:{iid}"
            if key not in Z:
                Z[key] = ts.add_series(iid, show, "standard" if iid == "sonarr-series" else "anime")
            if iid == "sonarr-series":
                e = ts.ep(iid, Z[key]["id"], 1, 1)
                title = f"Breaking.Bad.S01E01.1080p.WEB-DL.H.264-{tag}"
                feed_add(title=title, categories=[5040], tvdbid=show["tvdb"], season=1, episode=1)
            else:
                e = ts.anime_ep(Z[key]["id"], 1)
                title = f"[{tag}] Cowboy Bebop - 01 (WEB 1080p) [Dual Audio]"
                feed_add(title=title, categories=[5070], tvdbid=show["tvdb"], season=e["seasonNumber"],
                         episode=e["episodeNumber"], absolute=1)
            releases = arr.get("/release", episodeId=e["id"])
        else:
            film = ts.FILM if iid == "radarr-films" else ts.ANIMEFILM
            key = f"movie:{iid}"
            if key not in Z:
                Z[key] = core.post(f"/instances/{iid}/movies", {"tmdb_id": film["tmdb"],
                                                                "minimum_availability": "released"})
            year = 1968 if film is ts.FILM else 2001          # Radarr needs the year to parse
            title = f"{film['title'].replace(' ', '.')}.{year}.1080p.BluRay.x264-{tag}"
            feed_add(title=title, categories=[2040], tmdbid=film["tmdb"], imdbid=film["imdb"])
            releases = arr.get("/release", movieId=Z[key]["id"])
        log(iid, "releases:", [r["title"] for r in releases][:5])
        dec = wait_for(lambda iid=iid, title=title: core.decisions(iid, "pre_grab", title),
                       timeout=60, desc=f"{iid} pre-grab decision")
        log(iid, "pre-grab decision:", json.dumps(dec[0])[:250])
        ts.feed_clear()


def _no_hook_auth_failures():
    for iid in INSTANCES:
        logs = subprocess.run(["docker", "logs", ts.container(f"mediaguard-{iid}")],
                              check=True, capture_output=True, text=True)
        text = logs.stdout + logs.stderr
        bad = [line for line in text.splitlines() if "MediaGuard" in line and
               ("401" in line or "Unauthorized" in line or "Ongeldige hook-sleutel" in line)]
        assert not bad, (iid, bad[:5])


def test_z5_pre_grab_hooks_authenticate_from_every_fork():
    _pre_grab_everywhere("MGZERO")
    _no_hook_auth_failures()


def test_z6_internal_token_core_recovery_encoder():
    ready = wait_for(lambda: (x := core.get("/recovery/readiness"))["heartbeat"] and x, timeout=120,
                     desc="recovery heartbeat")
    log("readiness:", ready)
    assert ready["ready"] is True and ready["library_locked"] is False, ready
    health = core.get("/admin/health")
    log("admin health:", json.dumps(health)[:500])
    comps = health.get("components", health)
    assert comps["encoder"]["ok"] is True, comps["encoder"]
    assert comps["recovery"]["ok"] is True, comps["recovery"]
    # the encoder refuses calls without / with a wrong token (token from the file is enforced)
    assert requests.get(ts.ENCODER + "/api/mediaguard/v1/capabilities", timeout=10).status_code in (401, 403)
    ok = requests.get(ts.ENCODER + "/api/mediaguard/v1/capabilities", timeout=10,
                      headers={"X-MediaGuard-Token": secret("internal", "token")})
    assert ok.status_code == 200, ok.text[:300]


def test_z7_init_idempotent_across_compose_up():
    before = secret_digests()
    assert before == Z["digests"]
    cmd = [c for c in (RUN / "compose-cmd").read_text().split("\n") if c]
    svcs = [s for s in (RUN / "services").read_text().split("\n") if s]
    # 1) plain `up -d` again: the exited init container is started once more
    subprocess.run(cmd + ["up", "-d", *svcs], check=True, capture_output=True, text=True)
    st = init_state()
    assert st["ExitCode"] == 0, st
    # 2) `down` (containers removed, datasets kept) + `up`: a NEW init container on the same data
    t0 = time.time()
    subprocess.run(cmd + ["down"], check=True, capture_output=True, text=True)
    subprocess.run(cmd + ["up", "-d", *svcs], check=True, capture_output=True, text=True)
    log("down + up in %.0fs" % (time.time() - t0))
    st = init_state()
    assert st["Status"] == "exited" and st["ExitCode"] == 0, st
    out = init_logs()
    log("init log after restart:", out.strip())
    assert "alles was al ingericht" in out and "gegenereerd" not in out, out
    assert secret_digests() == before, "init must never overwrite existing secrets"
    assert (owner(EXISTING_DIR), owner(EXISTING_FILE)) == Z["existing"]
    wait_stack()
    core.login()                                          # session store + admin survived
    for i in core.get("/instances"):
        res = core.post(f"/instances/{i['id']}/test")
        assert res["api_key_valid"] is True, (i["id"], res)
    _pre_grab_everywhere("MGZERO2")
    _no_hook_auth_failures()
    assert core.get("/recovery/readiness")["library_locked"] is False    # setting persisted
