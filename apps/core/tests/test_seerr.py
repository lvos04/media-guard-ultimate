"""Seerr: instellingen, routetest (mismatchdetectie), mapping, cutover met rollback, aanvragen."""

from __future__ import annotations

import json

import pytest
from test_arr_fakes import FakeArr, FakeSeerr, make_movie, make_series, seerr_server, wire

from mediaguard.instances.registry import apikey_secret

KEYS = {"sonarr-series": "series-key-111", "sonarr-anime": "anime-key-222",
        "radarr-films": "films-key-333", "radarr-animefilms": "afilm-key-444"}
HOSTS = {"sonarr-series": ("mediaguard-sonarr-series", 8989),
         "sonarr-anime": ("mediaguard-sonarr-anime", 8989),
         "radarr-films": ("mediaguard-radarr-films", 7878),
         "radarr-animefilms": ("mediaguard-radarr-animefilms", 7878)}


@pytest.fixture
def env(client, ctx):  # type: ignore[no-untyped-def]
    arr = {}
    for iid, key in KEYS.items():
        app = "sonarr" if iid.startswith("sonarr") else "radarr"
        fake = FakeArr(app, api_key=key)
        if app == "radarr":
            fake.root_folders[0]["path"] = "/movies/"
        arr[iid] = wire(ctx, iid, fake)
    seerr = FakeSeerr()
    ctx.services["seerr_transport"] = seerr.transport()

    def srv(sid, iid, is_default, kind):  # type: ignore[no-untyped-def]
        host, port = HOSTS[iid]
        return seerr_server(sid, iid, host, port, KEYS[iid], is_default=is_default, kind=kind,
                            directory="/tv/" if kind == "sonarr" else "/movies/")
    seerr.servers["sonarr"] = [srv(0, "sonarr-series", True, "sonarr"),
                               srv(1, "sonarr-anime", False, "sonarr")]
    seerr.servers["radarr"] = [srv(0, "radarr-films", True, "radarr"),
                               srv(1, "radarr-animefilms", False, "radarr")]
    r = client.put("/api/v1/seerr/settings", json={"url": "http://seerr:5055",
                                                   "api_key": seerr.api_key})
    assert r.status_code == 200, r.text
    return {"arr": arr, "seerr": seerr}


def test_settings_verified_before_save(client, ctx):
    seerr = FakeSeerr()
    ctx.services["seerr_transport"] = seerr.transport()
    r = client.put("/api/v1/seerr/settings", json={"url": "http://seerr:5055",
                                                   "api_key": "verkeerde-sleutel"})
    assert r.status_code == 502 and "SEERR_AUTH" in r.text
    assert client.get("/api/v1/seerr/settings").json() == {
        "url": "", "api_key_set": False, "request_sync_interval_minutes": 30}
    assert client.post("/api/v1/seerr/test").status_code == 409
    r = client.put("/api/v1/seerr/settings", json={"url": "http://seerr:5055",
                                                   "api_key": seerr.api_key})
    assert r.json()["seerr_version"] == "2.7.3" and r.json()["api_key_set"]
    assert seerr.api_key not in json.dumps(client.get("/api/v1/seerr/settings").json())


def test_connection_test_masks_keys(client, env):
    r = client.post("/api/v1/seerr/test").json()
    assert r["ok"] and len(r["sonarr_servers"]) == 2
    assert all(s["apiKey"] == "********" for s in r["sonarr_servers"] + r["radarr_servers"])


def test_route_test_all_ok(client, env):
    r = client.post("/api/v1/seerr/route-test").json()
    assert r["ok"] is True, json.dumps(r, indent=1)[:2000]
    assert {i["instance_id"] for i in r["instances"]} == set(KEYS)
    checks = {c["check"] for c in r["instances"][0]["checks"]}
    assert {"server_found", "url_matches", "api_key_matches", "is_default", "is_4k",
            "seerr_connection_test"} <= checks
    assert "fake" not in json.dumps(r) and KEYS["sonarr-series"] not in json.dumps(r)


@pytest.mark.parametrize("mutate,instance,check", [
    (lambda s: s.servers["sonarr"][0].update(port=9999), "sonarr-series", "server_found"),
    (lambda s: s.servers["sonarr"][1].update(apiKey="oude-sleutel"), "sonarr-anime",
     "api_key_matches"),
    (lambda s: s.servers["radarr"][1].update(isDefault=True), "radarr-animefilms",
     "is_default"),
    (lambda s: s.servers["radarr"][0].update(is4k=True), "radarr-films", "is_4k"),
    (lambda s: s.servers["sonarr"][0].update(activeProfileId=42), "sonarr-series",
     "profile:activeProfileId"),
    (lambda s: s.servers["sonarr"][1].update(activeAnimeDirectory="/anime-weg/"),
     "sonarr-anime", "root:activeAnimeDirectory"),
    (lambda s: s.test_results.update({"mediaguard-radarr-films:7878": False}), "radarr-films",
     "seerr_connection_test"),
])
def test_route_test_detects_mismatch(client, env, mutate, instance, check):
    mutate(env["seerr"])
    r = client.post("/api/v1/seerr/route-test").json()
    assert r["ok"] is False
    inst = next(i for i in r["instances"] if i["instance_id"] == instance)
    failed = [c["check"] for c in inst["checks"] if c["ok"] is False]
    assert check in failed, inst["checks"]
    others = [i for i in r["instances"] if i["instance_id"] != instance]
    assert all(i["ok"] for i in others)


def test_old_system_server_and_duplicate_mapping(client, env):
    seerr = env["seerr"]
    seerr.servers["sonarr"].append(seerr_server(5, "Oude Sonarr", "oldbox", 8989, "x" * 10,
                                                is_default=False))
    r = client.get("/api/v1/seerr/mapping").json()
    assert r["tested"] is False and r["ok"] is False
    assert r["unmapped_servers"][0]["server"]["name"] == "Oude Sonarr"
    assert all(i["ok"] for i in r["instances"])
    # twee instanties naar dezelfde Seerr-server
    client.put("/api/v1/seerr/routing/sonarr-anime", json={"role": "anime",
                                                           "seerr_server_id": 0})
    r = client.get("/api/v1/seerr/mapping").json()
    anime = next(i for i in r["instances"] if i["instance_id"] == "sonarr-anime")
    assert "unique_server" in [c["check"] for c in anime["checks"] if c["ok"] is False]


def test_cutover_requires_confirmation_and_rollback(client, ctx, env):
    seerr = env["seerr"]
    seerr.servers["sonarr"][0].update(hostname="old-sonarr", port=8990, apiKey="old-key-999")
    r = client.post("/api/v1/seerr/cutover", json={"instance_id": "sonarr-series",
                                                   "server_id": 0}).json()
    assert r["requires_confirmation"] is True
    assert r["diff"]["hostname"] == {"old": "old-sonarr", "new": "mediaguard-sonarr-series"}
    assert r["diff"]["apiKey"] == {"old": "***", "new": "***"}
    assert not [c for c in seerr.calls if c[0] == "PUT"]
    r = client.post("/api/v1/seerr/cutover", json={"instance_id": "sonarr-series",
                                                   "server_id": 0, "confirm": True})
    assert r.status_code == 200, r.text
    server = seerr.servers["sonarr"][0]
    assert (server["hostname"], server["port"], server["apiKey"]) == (
        "mediaguard-sonarr-series", 8989, KEYS["sonarr-series"])
    assert server["activeProfileId"] == 1  # profielen ongewijzigd
    state = ctx.db.fetch_one("SELECT * FROM instance_cutover_state WHERE "
                             "instance_id='sonarr-series'")
    assert "old-key-999" not in state["seerr_previous_json"]
    assert client.post("/api/v1/seerr/route-test").json()["ok"] is True
    audit = client.get("/api/v1/audit?action=seerr.cutover").json()
    assert audit and "old-key-999" not in json.dumps(audit)
    r = client.post("/api/v1/seerr/cutover/rollback", json={"instance_id": "sonarr-series"})
    assert r.json()["requires_confirmation"]
    r = client.post("/api/v1/seerr/cutover/rollback", json={"instance_id": "sonarr-series",
                                                            "confirm": True})
    assert r.json()["rolled_back"]
    server = seerr.servers["sonarr"][0]
    assert (server["hostname"], server["port"], server["apiKey"]) == (
        "old-sonarr", 8990, "old-key-999")
    assert client.post("/api/v1/seerr/cutover/rollback", json={
        "instance_id": "sonarr-series", "confirm": True}).status_code == 422


def test_cutover_create_missing_server(client, env):
    env["seerr"].servers["radarr"] = [s for s in env["seerr"].servers["radarr"] if s["id"] != 1]
    r = client.post("/api/v1/seerr/cutover", json={"instance_id": "radarr-animefilms",
                                                   "confirm": True})
    assert r.status_code == 422
    r = client.post("/api/v1/seerr/cutover", json={
        "instance_id": "radarr-animefilms", "confirm": True, "create_if_missing": True,
        "active_profile_id": 1, "active_directory": "/movies/"})
    assert r.status_code == 200, r.text
    created = next(s for s in env["seerr"].servers["radarr"] if s["id"] == r.json()["server_id"])
    assert created["isDefault"] is False and created["apiKey"] == KEYS["radarr-animefilms"]
    r = client.post("/api/v1/seerr/cutover/rollback", json={"instance_id": "radarr-animefilms",
                                                            "confirm": True})
    assert r.json()["deleted_server"] == created["id"]
    assert all(s["id"] != created["id"] for s in env["seerr"].servers["radarr"])


def test_request_sync_links_items(client, ctx, env):
    arr = env["arr"]
    arr["sonarr-anime"].add_series(make_series(3, "Frieren", 424536, series_type="anime"), [])
    arr["radarr-films"].movies[8] = make_movie(8, "Wadi", 438631, has_file=True)
    arr["radarr-animefilms"].movies[8] = make_movie(8, "Your Name", 372058)
    for iid in KEYS:
        client.post(f"/api/v1/instances/{iid}/sync?mode=inline")
    client.put("/api/v1/seerr/routing/sonarr-anime", json={"role": "anime",
                                                           "seerr_server_id": 1})
    env["seerr"].requests = [
        {"id": 1, "status": 2, "type": "tv", "is4k": False, "serverId": 1,
         "createdAt": "2026-10-01T10:00:00.000Z", "updatedAt": "2026-10-01T10:00:00.000Z",
         "requestedBy": {"displayName": "Lisa"}, "seasons": [{"seasonNumber": 1, "status": 2}],
         "media": {"mediaType": "tv", "tmdbId": 209867, "tvdbId": 424536, "status": 3,
                   "externalServiceId": 3}},
        {"id": 2, "status": 2, "type": "movie", "is4k": False, "serverId": None,
         "createdAt": "2026-10-01T10:00:00.000Z", "updatedAt": "2026-10-02T10:00:00.000Z",
         "media": {"mediaType": "movie", "tmdbId": 438631, "status": 5,
                   "mediaAddedAt": "2026-10-01T22:00:00.000Z"}},
        {"id": 3, "status": 1, "type": "movie", "is4k": False,
         "createdAt": "2026-10-03T10:00:00.000Z",
         "media": {"mediaType": "movie", "tmdbId": 999999, "status": 2}},
    ]
    r = client.post("/api/v1/seerr/requests/sync").json()
    assert r == {"total": 3, "linked": 2, "unlinked": 1, "ambiguous": 0, "not_in_instance": 0}
    data = client.get("/api/v1/seerr/requests").json()
    by_id = {i["seerr_request_id"]: i for i in data["items"]}
    assert by_id[1]["instance_id"] == "sonarr-anime" and by_id[1]["title"] == "Frieren"
    assert by_id[1]["media_status_nl"] == "Wordt verwerkt"
    assert by_id[2]["instance_id"] == "radarr-films" and by_id[2]["available_at"]
    assert data["summary"]["lead_time_hours_median"] == 12.0
    assert client.get("/api/v1/seerr/requests?link_status=unlinked").json()["total"] == 1


def test_routing_defaults_and_validation(client, ctx, env):
    routing = {r["instance_id"]: r["routing"] for r in client.get("/api/v1/seerr/routing").json()}
    assert routing["sonarr-anime"]["role"] == "anime"
    assert routing["radarr-films"]["expected_is_default"] is True
    r = client.put("/api/v1/seerr/routing/sonarr-series", json={"seerr_kind": "radarr"})
    assert r.status_code == 422
    ctx.secrets.set(apikey_secret("sonarr-series"), KEYS["sonarr-series"], "t")
