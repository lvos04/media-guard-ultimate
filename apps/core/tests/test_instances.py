"""Instanties: seeding, CRUD, health, suspend/resume, webhook, ownership, beheer-API, RBAC."""

from __future__ import annotations

import json

import httpx
import pytest
from fastapi.testclient import TestClient
from test_arr_fakes import (
    FakeArr,
    download_client,
    indexer,
    make_episode,
    make_movie,
    make_series,
    wire,
)

from mediaguard.instances.registry import (
    apikey_secret,
    arr_client,
    get_instance,
    hookkey_secret,
    list_instances,
    seed_instances,
)


@pytest.fixture
def fakes(client, ctx):  # type: ignore[no-untyped-def]
    out = {
        "sonarr-series": wire(ctx, "sonarr-series", FakeArr("sonarr")),
        "sonarr-anime": wire(ctx, "sonarr-anime", FakeArr("sonarr", api_key="anime-key-999")),
        "radarr-films": wire(ctx, "radarr-films", FakeArr("radarr", api_key="films-key-999")),
    }
    return out


def test_four_default_instances_seeded_without_secrets(client):
    data = client.get("/api/v1/instances").json()
    assert [i["id"] for i in data] == ["sonarr-series", "sonarr-anime", "radarr-films",
                                       "radarr-animefilms"]
    assert {i["media_type"] for i in data} == {"tv", "anime", "movie", "anime_movie"}
    assert all("api_key" not in i for i in data)
    assert all(i["status"] == "unknown" for i in data)


def test_seed_from_env(ctx, monkeypatch):
    monkeypatch.setenv("MG_INSTANCES", json.dumps([
        {"id": "sonarr-series", "url": "http://s1:8989", "api_key": "envkey-12345"},
        {"id": "sonarr-4k", "app": "sonarr", "media_type": "tv", "name": "Sonarr 4K",
         "url": "http://s4k:8989"}]))
    monkeypatch.setenv("MG_INSTANCE_SONARR_4K_APIKEY", "fourk-key-123")
    assert seed_instances(ctx) == ["sonarr-series", "sonarr-4k"]
    assert ctx.secrets.get(apikey_secret("sonarr-series")) == "envkey-12345"
    assert ctx.secrets.get(apikey_secret("sonarr-4k")) == "fourk-key-123"
    assert get_instance(ctx, "sonarr-series").url == "http://s1:8989"
    # tweede keer: niets overschrijven
    monkeypatch.setenv("MG_INSTANCE_SONARR_4K_APIKEY", "other-key-123")
    assert seed_instances(ctx) == []
    assert ctx.secrets.get(apikey_secret("sonarr-4k")) == "fourk-key-123"


def test_create_instance_verifies_connection(client, ctx):
    fake = FakeArr("sonarr", api_key="new-key-12345")
    ctx.services.setdefault("arr_transports", {})["sonarr-4k"] = fake.transport()
    body = {"id": "sonarr-4k", "app": "sonarr", "media_type": "tv", "name": "Sonarr 4K",
            "url": "http://sonarr-4k:8989", "api_key": "wrong-key-123"}
    r = client.post("/api/v1/instances", json=body)
    assert r.status_code == 422 and get_instance(ctx, "sonarr-4k") is None
    r = client.post("/api/v1/instances", json={**body, "app": "radarr", "media_type": "movie",
                                               "api_key": "new-key-12345"})
    assert r.status_code == 422 and "WRONG_APP" in r.text
    r = client.post("/api/v1/instances", json={**body, "api_key": "new-key-12345"})
    assert r.status_code == 201, r.text
    assert r.json()["api_key_set"] is True and "new-key" not in r.text
    assert client.post("/api/v1/instances", json={**body, "api_key": "new-key-12345"}
                       ).status_code == 409
    assert client.post("/api/v1/instances", json={**body, "id": "Bad ID"}).status_code == 422


def test_health_states_and_notifications(client, ctx, fakes):
    r = client.post("/api/v1/instances/sonarr-series/test").json()
    assert r["status"] == "healthy" and r["version"] == fakes["sonarr-series"].version
    # ongeldige sleutel → degraded + api_key_valid False + melding
    ctx.secrets.set(apikey_secret("sonarr-series"), "verlopen-sleutel", "t")
    r = client.post("/api/v1/instances/sonarr-series/test").json()
    assert r["status"] == "degraded" and r["api_key_valid"] is False
    notes = client.get("/api/v1/notifications").json()
    assert any("Verminderd" in n["title"] and n["severity"] == "error" for n in notes)
    # offline
    def refuse(request: httpx.Request) -> httpx.Response:
        raise httpx.ConnectError("connection refused")
    ctx.services["arr_transports"]["sonarr-anime"] = httpx.MockTransport(refuse)
    r = client.post("/api/v1/instances/sonarr-anime/test").json()
    assert r["status"] == "offline"
    # kort offline (herstart): nog geen melding; na >10 min één foutmelding
    titles = [n["title"] for n in client.get("/api/v1/notifications").json()]
    assert not any("Sonarr Anime" in t for t in titles)
    alert = ctx.state_get("instance_alert:sonarr-anime")
    ctx.state_set("instance_alert:sonarr-anime", {**alert, "since": alert["since"] - 700})
    client.post("/api/v1/instances/sonarr-anime/test")
    off = [n for n in client.get("/api/v1/notifications").json()
           if n["instance_id"] == "sonarr-anime"]
    assert len(off) == 1 and off[0]["severity"] == "error" and off[0]["read"] == 0
    assert off[0]["link"] == "/instellingen/instanties"
    assert client.get("/api/v1/status").json()["unread_notifications"] >= 1
    # weer bereikbaar: dezelfde melding wordt 'werkt weer' (gelezen), geen nieuwe
    ctx.services["arr_transports"]["sonarr-anime"] = fakes["sonarr-anime"].transport()
    client.post("/api/v1/instances/sonarr-anime/test")
    back = [n for n in client.get("/api/v1/notifications").json()
            if n["instance_id"] == "sonarr-anime"]
    assert len(back) == 1 and "werkt weer" in back[0]["title"] and back[0]["read"] == 1
    # health error → degraded
    fakes["radarr-films"].health = [{"source": "IndexerStatusCheck", "type": "error",
                                     "message": "Indexers unavailable due to failures"}]
    r = client.post("/api/v1/instances/radarr-films/test").json()
    assert r["status"] == "degraded"
    assert "tijdelijk uitgeschakeld" in r["error"]  # Nederlandse uitleg i.p.v. Engels
    # verkeerde app op URL
    ctx.services["arr_transports"]["radarr-animefilms"] = FakeArr("sonarr").transport()
    ctx.secrets.set(apikey_secret("radarr-animefilms"), "fake-api-key-123", "t")
    r = client.post("/api/v1/instances/radarr-animefilms/test").json()
    assert r["status"] == "degraded" and "Verkeerde applicatie" in r["error"]


def test_suspend_resume_restores_exact_state_and_isolated(client, ctx, fakes):
    s = fakes["sonarr-series"]
    s.add_indexer(indexer(1, "A", rss=True, auto=True, interactive=True))
    s.add_indexer(indexer(2, "B", rss=False, auto=True, interactive=False))
    s.add_indexer(indexer(3, "C", rss=False, auto=False, interactive=True))
    other = fakes["sonarr-anime"]
    other.add_indexer(indexer(1, "Nyaa"))
    before = json.loads(json.dumps(s.providers["indexer"]))
    r = client.post("/api/v1/instances/sonarr-series/suspend", json={"reason": "migratie"})
    assert r.status_code == 200, r.text
    assert sorted(r.json()["changed_indexers"]) == [1, 2]
    for idx in s.providers["indexer"].values():
        assert idx["enableRss"] is False and idx["enableAutomaticSearch"] is False
    assert s.providers["indexer"][1]["enableInteractiveSearch"] is True
    assert s.calls_to("PUT", "/api/v3/indexer/1")[-1][2]["forceSave"] == "true"
    assert get_instance(ctx, "sonarr-series").suspended is True
    # andere instantie onaangeroerd (R6.7)
    assert other.calls_to("PUT", "/api/v3/indexer") == []
    assert other.providers["indexer"][1]["enableRss"] is True
    assert get_instance(ctx, "sonarr-anime").suspended is False
    # idempotent
    assert client.post("/api/v1/instances/sonarr-series/suspend").json()["already"] is True
    # tijdens pauze: wens onthouden i.p.v. live aanzetten
    r = client.patch("/api/v1/instances/sonarr-series/indexers/3/enable",
                     json={"enable_rss": True})
    assert r.json()["deferred_until_resume"] == {"enableRss": True}
    assert s.providers["indexer"][3]["enableRss"] is False
    r = client.post("/api/v1/instances/sonarr-series/resume")
    assert r.status_code == 200, r.text
    expected = json.loads(json.dumps(before))
    expected["3"]["enableRss"] = True
    assert {str(k): v for k, v in s.providers["indexer"].items()} == expected
    assert get_instance(ctx, "sonarr-series").suspended is False
    audit = client.get("/api/v1/audit?instance_id=sonarr-series&action=instance.").json()
    assert {a["action"] for a in audit} >= {"instance.suspend", "instance.resume"}


def test_suspend_rolls_back_on_failure(client, ctx, fakes):
    s = fakes["sonarr-series"]
    s.add_indexer(indexer(1, "A"))
    s.add_indexer(indexer(2, "B"))
    s.fail_paths["/api/v3/indexer/2"] = 500
    r = client.post("/api/v1/instances/sonarr-series/suspend")
    assert r.status_code == 502
    s.fail_paths.clear()
    assert s.providers["indexer"][1]["enableRss"] is True
    assert get_instance(ctx, "sonarr-series").suspended is False


def test_webhook_registration(client, ctx, fakes):
    s = fakes["sonarr-series"]
    r = client.post("/api/v1/instances/sonarr-series/webhook")
    assert r.status_code == 200, r.text
    data = r.json()
    assert data["action"] == "created"
    assert data["url"].endswith("/api/v1/hooks/arr-webhook/sonarr-series")
    note = next(iter(s.providers["notification"].values()))
    fields = {f["name"]: f["value"] for f in note["fields"]}
    key = ctx.secrets.get(hookkey_secret("sonarr-series"))
    assert fields["username"] == "sonarr-series" and fields["password"] == key
    assert fields["method"] == 1 and note["onGrab"] and note["onDownload"]
    assert note["onHealthIssue"] and note["onManualInteractionRequired"]
    assert ctx.hook_key_owner(key) == "sonarr-series"
    assert s.calls_to("POST", "/api/v3/notification")[-1][2]["forceSave"] == "true"
    # opnieuw: bijwerken, geen duplicaat; sleutel blijft gelijk
    r = client.post("/api/v1/instances/sonarr-series/webhook").json()
    assert r["action"] == "updated" and len(s.providers["notification"]) == 1
    assert ctx.secrets.get(hookkey_secret("sonarr-series")) == key
    r = client.post("/api/v1/instances/sonarr-series/webhook", json={"rotate_key": True}).json()
    assert ctx.secrets.get(hookkey_secret("sonarr-series")) != key
    assert ctx.hook_key_owner(key) is None
    # lijst toont wachtwoord gemaskeerd
    listed = client.get("/api/v1/instances/sonarr-series/notifications").json()
    assert listed[0]["fields"][3]["value"] == "********"


def test_patch_archive_and_rbac(client, ctx, fakes):
    r = client.patch("/api/v1/instances/sonarr-anime", json={"name": "Anime!",
                                                             "default_monitor_new_items": "all"})
    assert r.status_code == 200 and r.json()["name"] == "Anime!"
    r = client.patch("/api/v1/instances/sonarr-anime", json={"api_key": "wrong-key-999"})
    assert r.status_code == 422  # verbindingstest faalt → niets opgeslagen
    assert ctx.secrets.get(apikey_secret("sonarr-anime")) == "anime-key-999"
    r = client.patch("/api/v1/instances/sonarr-anime", json={"archived": True})
    assert r.json()["archived"] is True
    assert "sonarr-anime" not in [i.id for i in list_instances(ctx)]
    assert client.post("/api/v1/instances/sonarr-anime/suspend").status_code == 409
    # media blijft ongemoeid: geen enkele DELETE naar de fork
    assert fakes["sonarr-anime"].calls_to("DELETE", "/api/v3") == []
    client.post("/api/v1/auth/users", json={"username": "kijker", "password": "kijkwachtwoord1",
                                             "role": "viewer"})
    with TestClient(client.app) as other:
        tok = other.post("/api/v1/auth/login", json={"username": "kijker",
                                                     "password": "kijkwachtwoord1"})
        other.headers["X-CSRF-Token"] = tok.json()["csrf_token"]
        assert other.get("/api/v1/instances").status_code == 200
        assert other.post("/api/v1/instances/sonarr-series/suspend").status_code == 403
        assert other.post("/api/v1/instances/sonarr-series/series", json={
            "tvdb_id": 1}).status_code == 403


def test_series_management_same_title_two_instances_no_cross_mutation(client, ctx, fakes):
    series, anime = fakes["sonarr-series"], fakes["sonarr-anime"]
    # zelfde provider-ID 1 in beide instanties, verschillende titels
    series.add_series(make_series(1, "Breaking Bad", 81189), [make_episode(11, 1, 1, 1)])
    anime.add_series(make_series(1, "Cowboy Bebop", 76885, series_type="anime"),
                     [make_episode(11, 1, 1, 1, absolute=1)])
    client.post("/api/v1/instances/sonarr-series/sync?mode=inline")
    client.post("/api/v1/instances/sonarr-anime/sync?mode=inline")
    # dezelfde titel toevoegen aan anime → ownership-conflict
    r = client.post("/api/v1/instances/sonarr-anime/series", json={
        "tvdb_id": 81189, "quality_profile_id": 2, "root_folder_path": "/anime/"})
    assert r.status_code == 409 and "OWNERSHIP_CONFLICT" in r.text
    r = client.post("/api/v1/instances/sonarr-anime/series", json={
        "tvdb_id": 81189, "quality_profile_id": 2, "root_folder_path": "/anime/",
        "monitor": "future", "monitor_new_items": "all", "force_duplicate": True})
    assert r.status_code == 201, r.text
    added = anime.calls_to("POST", "/api/v3/series")[-1][3]
    assert added["addOptions"]["monitor"] == "future" and added["monitorNewItems"] == "all"
    assert added["qualityProfileId"] == 2 and added["seriesType"] == "anime"
    assert series.calls_to("POST", "/api/v3/series") == []
    # wijzig profiel van serie 1 in anime → Sonarr Series serie 1 blijft gelijk
    r = client.patch("/api/v1/instances/sonarr-anime/series/1", json={
        "quality_profile_id": 2, "monitor_new_items": "none"})
    assert r.status_code == 200
    assert anime.series[1]["qualityProfileId"] == 2
    assert series.series[1]["qualityProfileId"] == 1
    assert series.series[1]["monitorNewItems"] == "all"
    assert series.calls_to("PUT", "/api/v3") == []
    # seizoensmonitoring en episode-monitoring alleen op de juiste instantie
    r = client.put("/api/v1/instances/sonarr-anime/series/1/seasons/1",
                   json={"monitored": False})
    assert r.status_code == 200 and anime.series[1]["seasons"][0]["monitored"] is False
    assert series.series[1]["seasons"][0]["monitored"] is True
    r = client.put("/api/v1/instances/sonarr-series/episodes/monitor",
                   json={"episode_ids": [11], "monitored": False})
    assert series.episodes[11]["monitored"] is False and anime.episodes[11]["monitored"] is False
    # cache: twee aparte rijen met (instance_id, provider_id)
    rows = ctx.db.fetch_dicts("SELECT instance_id, provider_id, title FROM lib_items "
                              "WHERE provider_id=1 ORDER BY instance_id")
    assert [(r["instance_id"], r["title"]) for r in rows] == [
        ("sonarr-anime", "Cowboy Bebop"), ("sonarr-series", "Breaking Bad")]
    # verwijderen verwijdert nooit bestanden
    r = client.delete("/api/v1/instances/sonarr-anime/series/1")
    assert r.json() == {"deleted": True, "files_deleted": False}
    assert anime.calls_to("DELETE", "/api/v3/series/1")[-1][2]["deleteFiles"] == "false"
    audit = client.get("/api/v1/audit?instance_id=sonarr-anime&action=series.").json()
    assert {a["action"] for a in audit} >= {"series.add", "series.update", "series.delete"}
    # films-routes op een Sonarr-instantie geweigerd
    assert client.get("/api/v1/instances/sonarr-series/movies").status_code == 400


def test_movies_management(client, ctx, fakes):
    films = fakes["radarr-films"]
    films.movies[1] = make_movie(1, "Wadi", 438631)
    r = client.post("/api/v1/instances/radarr-films/movies", json={
        "tmdb_id": 603, "quality_profile_id": 1, "root_folder_path": "/movies/",
        "minimum_availability": "released"})
    assert r.status_code == 201, r.text
    assert films.calls_to("POST", "/api/v3/movie")[-1][3]["addOptions"]["searchForMovie"] is False
    r = client.patch("/api/v1/instances/radarr-films/movies/1", json={"monitored": False})
    assert r.status_code == 200 and films.movies[1]["monitored"] is False
    assert client.post("/api/v1/instances/radarr-films/movies", json={
        "tmdb_id": 438631, "quality_profile_id": 1, "root_folder_path": "/m/"}
    ).status_code == 409
    r = client.post("/api/v1/instances/radarr-films/search", json={"type": "movies",
                                                                   "movie_ids": [1]})
    assert r.json()["name"] == "MoviesSearch"


def test_indexer_crud_masks_and_preserves_secrets(client, ctx, fakes):
    s = fakes["sonarr-series"]
    s.add_indexer(indexer(1, "NZBgeek"))
    listed = client.get("/api/v1/instances/sonarr-series/indexers").json()
    fields = {f["name"]: f for f in listed[0]["fields"]}
    assert fields["apiKey"]["value"] == "********" and "idx-secret" not in json.dumps(listed)
    body = listed[0]
    body["priority"] = 10
    r = client.put("/api/v1/instances/sonarr-series/indexers/1", json=body)
    assert r.status_code == 200
    stored = {f["name"]: f["value"] for f in s.providers["indexer"][1]["fields"]}
    assert stored["apiKey"] == "idx-secret-1" and s.providers["indexer"][1]["priority"] == 10
    assert client.post("/api/v1/instances/sonarr-series/indexers/1/test").json()["ok"] is True
    r = client.post("/api/v1/instances/sonarr-series/indexers/test",
                    json={**indexer(0, "nieuw"), "fields": [{"name": "apiKey", "value": ""}]})
    assert r.status_code == 422 and "Invalid API Key" in r.text
    new = client.post("/api/v1/instances/sonarr-series/indexers", json=indexer(0, "Nieuw"))
    assert new.status_code == 201
    r = client.patch(f"/api/v1/instances/sonarr-series/indexers/{new.json()['id']}/enable",
                     json={"enable_rss": False})
    assert s.providers["indexer"][new.json()["id"]]["enableRss"] is False
    assert client.delete(f"/api/v1/instances/sonarr-series/indexers/{new.json()['id']}"
                         ).json() == {"deleted": True}
    s.providers["downloadclient"][1] = download_client(1)
    dcs = client.get("/api/v1/instances/sonarr-series/download-clients").json()
    assert dcs[0]["fields"][1]["value"] == "********"
    r = client.patch("/api/v1/instances/sonarr-series/download-clients/1/enable",
                     json={"enable": False})
    assert s.providers["downloadclient"][1]["enable"] is False
    # 'Alles testen' (lege body → was 422 'Name must not be empty'): per client resultaat
    s.providers["downloadclient"][2] = download_client(2, "qBittorrent", "torrent")
    s.providers["downloadclient"][2]["fields"][1]["value"] = ""
    for path in ("download-clients/test", "download-clients/test-all"):
        r = client.post(f"/api/v1/instances/sonarr-series/{path}", json={})
        assert r.status_code == 200, r.text
        res = {x["name"]: x["ok"] for x in r.json()["results"]}
        assert res == {"SABnzbd": True, "qBittorrent": False}
        assert "qBittorrent ✗" in r.json()["summary_nl"]
    audit = client.get("/api/v1/audit?instance_id=sonarr-series&action=indexer").json()
    assert audit and all("idx-secret" not in json.dumps(a) for a in audit)


def test_config_resources_and_views(client, ctx, fakes):
    base = "/api/v1/instances/sonarr-series"
    assert client.get(f"{base}/quality-profiles").json()[0]["name"] == "HD-1080p"
    assert client.get(f"{base}/language-profiles").json() == []  # Sonarr v4
    assert client.post(f"{base}/root-folders", json={"path": "/tv2/"}).status_code == 201
    tag = client.post(f"{base}/tags", json={"label": "anime"}).json()
    assert client.put(f"{base}/tags/{tag['id']}", json={"label": "anime2"}).status_code == 200
    rpm = client.post(f"{base}/remote-path-mappings", json={
        "host": "sabnzbd", "remote_path": "/data/", "local_path": "/downloads/"}).json()
    assert rpm["remotePath"] == "/data/"
    assert client.delete(f"{base}/remote-path-mappings/{rpm['id']}").json()["deleted"]
    assert client.get(f"{base}/queue").json()["totalRecords"] == 0
    assert client.get(f"{base}/history").json()["records"] == []
    assert client.post(f"{base}/commands", json={"name": "RssSync"}).json()["name"] == "RssSync"
    assert client.post(f"{base}/commands", json={"name": "MissingEpisodeSearch"}
                       ).status_code == 409
    assert client.post(f"{base}/commands", json={"name": "Shutdown"}).status_code == 422
    assert client.get(f"{base}/system/status").json()["appName"] == "Sonarr"
    assert client.get(f"{base}/backups").json()[0]["type"] == "scheduled"
    detail = client.get(base).json()
    assert detail["api_key_set"] is True and "fake-api-key" not in json.dumps(detail)


def test_ownership_conflicts_and_simulation(client, ctx, fakes):
    fakes["sonarr-series"].add_series(make_series(1, "Attack on Titan", 267440), [])
    fakes["sonarr-anime"].add_series(make_series(7, "Attack on Titan", 267440,
                                                 series_type="anime"), [])
    for iid in ("sonarr-series", "sonarr-anime"):
        client.post(f"/api/v1/instances/{iid}/sync?mode=inline")
    conf = client.get("/api/v1/ownership/conflicts").json()
    assert conf["total"] == 1 and conf["conflicts"][0]["type"] == "cross_scope_duplicate"
    sim = client.post("/api/v1/ownership/simulate", json={"kind": "series",
                                                          "tvdb_id": 267440}).json()
    assert sim["predicted_instance"] in ("sonarr-series", "sonarr-anime")
    sim = client.post("/api/v1/ownership/simulate", json={"kind": "series", "tvdb_id": 1,
                                                          "is_anime": True}).json()
    assert sim["predicted_instance"] == "sonarr-anime" and sim["conflict"] is None
    sim = client.post("/api/v1/ownership/simulate", json={"kind": "movie", "tmdb_id": 5,
                                                          "is_anime": False}).json()
    assert sim["predicted_instance"] == "radarr-films"
    for iid in ("sonarr-series", "sonarr-anime"):
        assert client.post("/api/v1/ownership", json={
            "media_type": "anime" if "anime" in iid else "tv", "canonical_id": "tvdb:267440",
            "instance_id": iid, "allow_multi": True, "reason": "bewust dubbel"}
        ).status_code == 201
    assert client.get("/api/v1/ownership/conflicts").json()["total"] == 0
    assert client.get("/api/v1/ownership/conflicts?include_intentional=true"
                      ).json()["conflicts"][0]["intentional"] is True


def test_arr_client_contract(ctx, fakes):
    c = arr_client(ctx, "sonarr-series")
    assert c.instance_id == "sonarr-series" and c.app == "sonarr"
    with pytest.raises(KeyError):
        arr_client(ctx, "bestaat-niet")
