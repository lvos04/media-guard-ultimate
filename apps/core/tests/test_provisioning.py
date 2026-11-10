"""Automatische inrichting: verse instanties → toepassen → alles aanwezig en correct per
instantie; idempotent; drift detecteren/herstellen; gebruikersitems onaangeroerd; geheimen
nooit in API-antwoorden; Prowlarr-applicaties; Seerr volgens docs/SEERR_INSTELLEN.md."""

from __future__ import annotations

import json
from types import SimpleNamespace
from typing import Any

import pytest
from test_arr_fakes import FakeSeerr, indexer, seerr_server
from test_provisioning_fakes import FakeProwlarr, wire_all

from mediaguard.provisioning import templates as T

SECRETS = ("sab-secret-xyz", "geek-secret-123", "nyaa-secret-456", "prowlarr-key-123",
           "seerr-key-123456")
CFG: dict[str, Any] = {
    "indexer_mode": "direct",
    "download_clients": [{"name": "SAB", "implementation": "Sabnzbd", "host": "sabnzbd",
                          "port": 8080, "api_key": "sab-secret-xyz"}],
    "indexers": [
        {"name": "NZBgeek", "implementation": "Newznab", "base_url": "https://api.nzbgeek.info",
         "api_key": "geek-secret-123"},
        {"name": "Nyaa", "implementation": "Torznab",
         "base_url": "http://jackett:9117/api/v2.0/indexers/nyaasi/results/torznab",
         "api_key": "nyaa-secret-456"}],
}
CATS = {"sonarr-series": "tv", "sonarr-anime": "tv-anime", "radarr-films": "movies",
        "radarr-animefilms": "movies-anime"}


@pytest.fixture
def env(client, ctx):  # type: ignore[no-untyped-def]
    fakes = wire_all(ctx)
    prow = FakeProwlarr()
    ctx.services["prowlarr_transport"] = prow.transport()
    seerr = FakeSeerr()
    ctx.services["seerr_transport"] = seerr.transport()
    return SimpleNamespace(arr=fakes, prowlarr=prow, seerr=seerr, client=client, ctx=ctx)


def fval(resource: dict[str, Any], name: str) -> Any:
    return next((f.get("value") for f in resource.get("fields") or [] if f["name"] == name),
                None)


def put_cfg(client, cfg: dict[str, Any] | None = None):  # type: ignore[no-untyped-def]
    r = client.put("/api/v1/provisioning/config", json=cfg or CFG)
    assert r.status_code == 200, r.text
    return r.json()


def apply(client, **body: Any) -> dict[str, Any]:  # type: ignore[no-untyped-def]
    r = client.post("/api/v1/provisioning/apply", json={"confirm": True, **body})
    assert r.status_code == 200, r.text
    return r.json()


def dcs(fake) -> list[dict[str, Any]]:  # type: ignore[no-untyped-def]
    return list(fake.providers["downloadclient"].values())


def idx_names(fake) -> set[str]:  # type: ignore[no-untyped-def]
    return {i["name"] for i in fake.providers["indexer"].values()}


def test_fresh_instances_fully_provisioned_and_idempotent(env):  # noqa: C901
    c = env.client
    put_cfg(c)
    plan = c.post("/api/v1/provisioning/plan", json={}).json()
    assert plan["dry_run"] and plan["summary"]["changes"] > 20
    assert any("Downloadclient 'MediaGuard · SAB' aanmaken (categorie tv-anime)" in line
               for line in plan["text_nl"])
    # zonder bevestiging gebeurt niets
    r = c.post("/api/v1/provisioning/apply", json={"confirm": False}).json()
    assert r["requires_confirmation"] and not dcs(env.arr["sonarr-series"])
    res = apply(c)
    assert res["status"] == "succeeded", res["failed"] or res["remaining"]
    assert res["remaining"] == [] and res["summary"]["applied"] > 20
    for iid, fake in env.arr.items():
        anime = iid in ("sonarr-anime", "radarr-animefilms")
        assert [r["path"] for r in fake.root_folders] == [T.DEFAULT_ROOT[
            {"sonarr-series": "tv", "sonarr-anime": "anime", "radarr-films": "movie",
             "radarr-animefilms": "anime_movie"}[iid]]]
        (dc,) = dcs(fake)
        assert dc["name"] == "MediaGuard · SAB" and dc["enable"] is True
        cat = "tvCategory" if fake.app == "sonarr" else "movieCategory"
        assert fval(dc, cat) == CATS[iid] and fval(dc, "apiKey") == "sab-secret-xyz"
        assert fval(dc, "host") == "sabnzbd"
        want = {"MediaGuard · NZBgeek", "MediaGuard · Nyaa"} if anime else \
            {"MediaGuard · NZBgeek"}
        assert idx_names(fake) == want, iid
        mm = fake.configs["mediamanagement"]
        assert mm["copyUsingHardlinks"] is True and mm["recycleBin"] == ""
        assert mm["importExtraFiles"] is True and "srt" in mm["extraFileExtensions"]
        hooks = list(fake.providers["notification"].values())
        assert len(hooks) == 1 and hooks[0]["name"] == "MediaGuard"
        assert fval(hooks[0], "url").endswith(f"/arr-webhook/{iid}")
        assert {"mediaguard"} | ({"anime"} if anime else set()) == \
            {t["label"] for t in fake.tags.values()}
        assert fake.delay_profiles[0]["preferredProtocol"] == "usenet"
        defs = {d["title"]: d for d in fake.quality_definitions}
        assert defs["Bluray-1080p"]["minSize"] == 50.4
    sa = env.arr["sonarr-anime"]
    assert "{absolute:000}" in sa.configs["naming"]["animeEpisodeFormat"]
    assert sa.configs["naming"]["renameEpisodes"] is True
    prof = sa.profile("MediaGuard · Anime Dual Audio")
    assert prof is not None and sa.profile("MediaGuard · WEB-2160p") is None
    scores = {f["name"]: f["score"] for f in prof["formatItems"]}
    assert scores["MediaGuard · Dual Audio"] == 2000
    assert scores["MediaGuard · Anime slechte groep"] == -10000
    from mediaguard.provisioning.engine import allowed_leaves, cutoff_names

    assert {"Bluray-1080p", "WEBDL-1080p", "WEBRip-1080p"} <= allowed_leaves(prof)
    # audio-first: 4K toegestaan, maar in één groep met 1080p (Dual Audio wint via score)
    assert "WEBDL-2160p" in allowed_leaves(prof)
    assert any(it.get("name", "").endswith("(audio eerst)") for it in prof["items"])
    # cutoff = de samengevoegde groep; daarbinnen beslist de CF-score (cutoff_score)
    assert "Bluray-2160p" in cutoff_names(prof) and "Bluray-1080p" in cutoff_names(prof)
    ss = env.arr["sonarr-series"]
    web = ss.profile("MediaGuard · WEB-2160p")
    assert allowed_leaves(web) == {"WEBDL-1080p", "WEBRip-1080p", "WEBDL-2160p", "WEBRip-2160p"}
    assert cutoff_names(web) == {"WEBRip-2160p", "WEBDL-2160p"}  # groep 'WEB 2160p'
    assert ss.profile("MediaGuard · Anime Dual Audio") is not None  # voor Seerr-animevelden
    films = env.arr["radarr-films"].profile("MediaGuard · UHD HDR")
    assert films["language"]["name"] == "Original"
    assert {f["name"]: f["score"] for f in films["formatItems"]}["MediaGuard · Dolby Vision"] \
        == 600
    af = env.arr["radarr-animefilms"].profile("MediaGuard · Anime Dual Audio")
    assert af["language"]["name"] == "Any"
    # standaardprofiel/-rootmap van de instantie ingevuld (voor 'toevoegen' in de UI)
    row = env.ctx.db.fetch_one("SELECT * FROM instances WHERE id='sonarr-anime'")
    assert row["default_quality_profile_id"] == prof["id"]
    assert row["default_root_folder"] == "/data/media/anime"
    # idempotent: tweede keer verandert er niets
    writes = {iid: len(f.writes()) for iid, f in env.arr.items()}
    again = apply(c)
    assert again["summary"]["applied"] == 0 and again["status"] == "succeeded"
    assert {iid: len(f.writes()) for iid, f in env.arr.items()} == writes
    st = c.get("/api/v1/provisioning/status").json()
    for inst in st["instances"]:
        assert inst["initialized"] and inst["mode"] == "manage"
        bad = {k: v for k, v in inst["sections"].items() if v["status"] not in ("ok",)}
        assert not bad, (inst["instance_id"], bad)


def test_drift_detected_and_repaired_user_items_untouched(env):
    c, ctx = env.client, env.ctx
    put_cfg(c)
    apply(c)
    ss = env.arr["sonarr-series"]
    (dc,) = dcs(ss)
    for f in dc["fields"]:
        if f["name"] == "tvCategory":
            f["value"] = "verkeerd"
    ss.configs["mediamanagement"]["copyUsingHardlinks"] = False
    user_idx = indexer(4242, "Mijn eigen indexer")
    ss.add_indexer(user_idx)
    before_user = json.dumps(user_idx, sort_keys=True)
    r = c.post("/api/v1/provisioning/drift-check", params={"repair": False}).json()
    assert r["drift"] == 2 and r["repaired"] is None
    st = c.get("/api/v1/provisioning/status").json()
    secs = next(i for i in st["instances"] if i["instance_id"] == "sonarr-series")["sections"]
    assert secs["downloadclients"]["status"] == "drift"
    assert secs["mediamanagement"]["status"] == "drift"
    assert secs["indexers"]["status"] == "ok"  # eigen indexer van de gebruiker = geen drift
    r = c.post("/api/v1/provisioning/drift-check", params={"repair": True}).json()
    assert r["repaired"] == 2 and r["remaining"] == 0
    assert fval(dcs(ss)[0], "tvCategory") == "tv"
    assert ss.configs["mediamanagement"]["copyUsingHardlinks"] is True
    assert json.dumps(ss.providers["indexer"][4242], sort_keys=True) == before_user
    # door de gebruiker als 'zelf beheerd' gemarkeerd: nooit meer aangeraakt
    r = c.put("/api/v1/provisioning/items/ownership", json={
        "instance_id": "sonarr-series", "section": "downloadclients",
        "key": "MediaGuard · SAB", "ownership": "user"})
    assert r.status_code == 200
    for f in dcs(ss)[0]["fields"]:
        if f["name"] == "tvCategory":
            f["value"] = "eigen-keuze"
    assert c.post("/api/v1/provisioning/drift-check", params={"repair": True}).json()[
        "drift"] == 0
    assert fval(dcs(ss)[0], "tvCategory") == "eigen-keuze"
    # 'volledig beheer' aangevinkt: pas dan wordt de eigen indexer verwijderd
    put_cfg(c, {"instances": {"sonarr-series": {"full_manage": ["indexers"]}}})
    plan = c.post("/api/v1/provisioning/plan", json={"instances": ["sonarr-series"],
                                                     "sections": ["indexers"]}).json()
    dels = [x for x in plan["instances"]["sonarr-series"]["sections"]["indexers"]["changes"]
            if x["action"] == "delete"]
    assert [d["key"] for d in dels] == ["Mijn eigen indexer"]
    # drift-herstel verwijdert nooit
    c.post("/api/v1/provisioning/drift-check", params={"repair": True})
    assert 4242 in ss.providers["indexer"]
    apply(c, instances=["sonarr-series"], sections=["indexers"])
    assert 4242 not in ss.providers["indexer"]
    assert ctx.db.fetch_one("SELECT COUNT(*) AS n FROM audit_events WHERE action LIKE "
                            "'provisioning.indexers.delete'")["n"] == 1


def test_secrets_never_returned(env):
    c = env.client
    cfg = {**CFG, "prowlarr": {"url": "http://prowlarr:9696", "api_key": "prowlarr-key-123"}}
    out = put_cfg(c, cfg)
    assert out["download_clients"][0]["api_key_set"] is True
    assert out["prowlarr"]["api_key_set"] is True and "api_key" not in out["prowlarr"]
    assert out["indexers"][1]["effective_scope"] == "anime"
    apply(c)
    texts = [c.get("/api/v1/provisioning/config").text,
             c.get("/api/v1/provisioning/status?refresh=true").text,
             c.post("/api/v1/provisioning/plan", json={}).text,
             c.get("/api/v1/provisioning/items").text,
             c.get("/api/v1/provisioning/runs").text]
    env.arr["sonarr-series"].configs["mediamanagement"]["copyUsingHardlinks"] = False
    for f in dcs(env.arr["sonarr-series"])[0]["fields"]:
        if f["name"] == "apiKey":
            f["value"] = "ander-geheim-999"
    texts.append(c.post("/api/v1/provisioning/plan", json={}).text)
    for t in texts:
        for s in (*SECRETS, "ander-geheim-999", "key-sonarr-series-0001"):
            assert s not in t
    rows = env.ctx.db.fetch_all("SELECT old_value, new_value FROM audit_events")
    for r in rows:
        assert "sab-secret-xyz" not in (r["old_value"] or "") + (r["new_value"] or "")
    # wissen en ongewijzigd laten
    put_cfg(c, {"download_clients": [{**CFG["download_clients"][0], "api_key": None}]})
    assert env.ctx.secrets.get("downloadclient:SAB:apikey") == "sab-secret-xyz"
    put_cfg(c, {"download_clients": []})
    assert env.ctx.secrets.get("downloadclient:SAB:apikey") is None


def test_needs_input_reported(env):
    c = env.client
    st = c.get("/api/v1/provisioning/status").json()
    msgs = [n["message_nl"] for n in st["needs"]]
    assert "Nog geen downloadclient ingevoerd" in msgs
    assert "Kies Prowlarr of voer indexers in" in msgs and st["ready"] is False
    put_cfg(c, {"download_clients": [{"name": "SAB", "implementation": "Sabnzbd",
                                      "host": "sab", "port": 8080}],
                "indexer_mode": "prowlarr"})
    msgs = [n["message_nl"] for n in c.get("/api/v1/provisioning/status").json()["needs"]]
    assert "downloadclient-API-sleutel ontbreekt voor 'SAB'" in msgs
    assert "Prowlarr-API-sleutel ontbreekt" in msgs
    plan = c.post("/api/v1/provisioning/plan", json={"sections": ["downloadclients"]}).json()
    sec = plan["instances"]["sonarr-series"]["sections"]["downloadclients"]
    assert sec["status"] == "needs_input"
    r = c.put("/api/v1/provisioning/config", json={"download_clients": [
        {"name": "x/y", "implementation": "Sabnzbd", "host": "a", "port": 1}]})
    assert r.status_code == 422
    r = c.put("/api/v1/provisioning/config", json={"onbekend": 1})
    assert r.status_code == 422


def test_missing_media_path_is_host_action(env):
    env.arr["sonarr-series"].existing_paths.discard("/data/media/tv")
    put_cfg(env.client)
    res = apply(env.client, sections=["rootfolders"])
    st = env.client.get("/api/v1/provisioning/status").json()
    sec = next(i for i in st["instances"] if i["instance_id"] == "sonarr-series")["sections"]
    assert sec["rootfolders"]["status"] == "error"
    assert "bestaat niet in de container" in sec["rootfolders"]["message_nl"]
    assert not env.arr["sonarr-series"].root_folders
    assert env.arr["sonarr-anime"].root_folders and res["status"] == "partial"
    v = env.client.post("/api/v1/provisioning/paths/validate",
                        json={"instance_id": "sonarr-series"}).json()
    assert v["exists"] is False and v["path"] == "/data/media/tv"


def test_prowlarr_applications_registered(env):
    c, prow = env.client, env.prowlarr
    prow.indexers = {
        1: {"id": 1, "name": "NZBgeek", "definitionName": "nzbgeek", "protocol": "usenet",
            "tags": [7]},
        2: {"id": 2, "name": "Nyaa.si", "definitionName": "nyaasi", "protocol": "torrent",
            "tags": []}}
    put_cfg(c, {"indexer_mode": "prowlarr",
                "download_clients": CFG["download_clients"],
                "prowlarr": {"url": "http://prowlarr:9696", "api_key": "prowlarr-key-123"}})
    t = c.post("/api/v1/provisioning/prowlarr/test", json={}).json()
    assert t["ok"] and t["indexers"][1]["suggested_scope"] == "anime"
    res = apply(c)
    assert res["status"] == "succeeded", res
    tags = {t["label"]: t["id"] for t in prow.tags.values()}
    assert set(tags) == {"anime", "normal"}
    apps = {a["name"]: a for a in prow.apps.values()}
    assert set(apps) == {"MediaGuard · Sonarr Series", "MediaGuard · Sonarr Anime",
                         "MediaGuard · Radarr Films", "MediaGuard · Radarr Animefilms"}
    sa = apps["MediaGuard · Sonarr Anime"]
    assert sa["implementation"] == "Sonarr" and sa["tags"] == [tags["anime"]]
    assert fval(sa, "animeSyncCategories") == [5070]
    assert 5040 in fval(sa, "syncCategories")
    assert fval(sa, "apiKey") == "key-sonarr-anime-0001"
    assert fval(sa, "prowlarrUrl") == "http://prowlarr:9696"
    rf = apps["MediaGuard · Radarr Films"]
    assert rf["tags"] == [tags["normal"]] and 2000 in fval(rf, "syncCategories")
    assert 5070 not in fval(rf, "syncCategories")
    assert 5070 in fval(apps["MediaGuard · Radarr Animefilms"], "syncCategories")
    assert set(prow.indexers[1]["tags"]) == {7, tags["anime"], tags["normal"]}
    assert prow.indexers[2]["tags"] == [tags["anime"]]
    assert {"name": "ApplicationIndexerSync"} in prow.commands
    assert not any(env.arr[i].providers["indexer"] for i in env.arr)  # Prowlarr pusht zelf
    n = len(prow.calls)
    again = apply(c)
    assert again["summary"]["applied"] == 0
    assert all(m == "GET" for m, _, _ in prow.calls[n:])


def _seerr_setup(env):  # type: ignore[no-untyped-def]
    put_cfg(env.client, {**CFG, "seerr_connection": {"url": "http://seerr:5055",
                                                     "api_key": env.seerr.api_key}})
    apply(env.client)


def test_seerr_configured_per_docs(env):
    c, seerr = env.client, env.seerr
    old = seerr_server(0, "Sonarr (oud)", "oldbox", 8989, "old-key-777", is_default=True,
                       kind="sonarr")
    seerr.servers["sonarr"] = [old]
    old_copy = json.dumps(old, sort_keys=True)
    _seerr_setup(env)
    r = c.post("/api/v1/provisioning/seerr/apply", json={"confirm": False}).json()
    assert r["requires_confirmation"] and len(seerr.servers["radarr"]) == 0
    r = c.post("/api/v1/provisioning/seerr/apply", json={"confirm": True}).json()
    assert not r["failed"] and not r["remaining"], r
    s = {x["name"]: x for x in seerr.servers["sonarr"]}
    rd = {x["name"]: x for x in seerr.servers["radarr"]}
    assert json.dumps(s["Sonarr (oud)"], sort_keys=True) == old_copy  # oud: onaangeroerd
    series, anime = s["Sonarr Series"], s["Sonarr Anime"]
    # oude installatie is nog standaard: nieuwe Series-server wordt (nog) niet standaard
    assert series["isDefault"] is False and anime["isDefault"] is False
    assert any("blijft voorlopig standaardserver" in x["title_nl"] for x in r["changes"])
    ss_profiles = {p["name"]: p["id"] for p in env.arr["sonarr-series"].quality_profiles}
    assert series["activeProfileId"] == ss_profiles["MediaGuard · WEB-2160p"]
    assert series["activeAnimeProfileId"] == ss_profiles["MediaGuard · Anime Dual Audio"]
    assert series["activeDirectory"] == "/data/media/tv" and series["syncEnabled"] is True
    assert series["enableSeasonFolders"] is True and series["seriesType"] == "standard"
    assert anime["seriesType"] == "anime" and anime["activeDirectory"] == "/data/media/anime"
    assert series["port"] == 8989 and series["apiKey"] == "key-sonarr-series-0001"
    films, af = rd["Radarr Films"], rd["Radarr Animefilms"]
    assert films["isDefault"] is True and af["isDefault"] is False
    assert films["minimumAvailability"] == "released" and af["activeDirectory"] == \
        "/data/media/animefilms"
    for _, _, body in seerr.calls:  # _writable: nooit 'id' of null naar Seerr
        if isinstance(body, dict) and "hostname" in body and "apiKey" in body and \
                "activeDirectory" in body:
            assert "id" not in body and None not in body.values()
    routing = env.ctx.db.fetch_dicts("SELECT * FROM instance_seerr_routing ORDER BY "
                                     "instance_id")
    assert len(routing) == 4 and all(r["seerr_server_id"] is not None for r in routing)
    again = c.post("/api/v1/provisioning/seerr/apply", json={"confirm": True}).json()
    assert again["applied"] == [] and again["remaining"] == []


def test_seerr_blocked_during_shadow_phase(env):
    ctx = env.ctx
    _seerr_setup(env)
    rid = ctx.db.execute_insert("INSERT INTO migration_runs(name,created_at,updated_at) "
                                "VALUES ('m','2026-01-01','2026-01-01')")
    ctx.db.execute("INSERT INTO migration_shadow_states(run_id,target_instance_id,source_id,"
                   "mode,state_json,applied_at) VALUES (?,?,?,?,?,?)",
                   (rid, "sonarr-series", "oud", "native", "{}", "2026-01-01"))
    r = env.client.post("/api/v1/provisioning/seerr/apply", json={"confirm": True}).json()
    assert r["applied"] == [] and not env.seerr.servers["sonarr"]
    assert any("schaduwfase" in (x.get("message_nl") or "") for x in r["changes"])


def test_startup_essentials_and_drift_without_config(env):
    import asyncio

    from mediaguard.provisioning.api import drift_check, ensure_essentials

    res = asyncio.run(ensure_essentials(env.ctx))
    assert {c["section"] for c in res["applied"]} == {"tags", "rootfolders", "webhook"}
    for fake in env.arr.values():
        assert len(fake.root_folders) == 1 and fake.providers["notification"]
        assert not fake.providers["downloadclient"] and fake.profile("MediaGuard · WEB-2160p") \
            is None
    # niet-ingerichte instanties: drift-herstel beperkt zich tot de essentials
    env.arr["sonarr-series"].providers["notification"].clear()
    r = asyncio.run(drift_check(env.ctx, repair=True))
    assert r["repaired"] == 1 and env.arr["sonarr-series"].providers["notification"]
    assert not env.arr["sonarr-series"].custom_formats
    assert env.ctx.db.fetch_one("SELECT COUNT(*) AS n FROM notifications WHERE "
                                "title='Inrichting wijkt af'")["n"] == 1


def test_anime_profile_merges_1080p_and_2160p_so_dual_audio_wins():
    """L2: anime 1080p Dual Audio moet winnen van 2160p Japans-alleen. Sonarr rangschikt
    kwaliteit vóór CF-score; daarom staan 1080p en 2160p in één groep."""
    from mediaguard.provisioning import templates as T
    from mediaguard.provisioning.engine import leaves, merge_quality_group

    names = ["SDTV", "HDTV-1080p", "WEBRip-1080p", "WEBDL-1080p", "Bluray-1080p",
             "WEBRip-2160p", "WEBDL-2160p", "Bluray-2160p"]
    items = [{"quality": {"id": i, "name": n}, "items": [], "allowed": False}
             for i, n in enumerate(names[:2])]
    items.append({"name": "WEB 1080p", "id": 1001, "allowed": False, "items": [
        {"quality": {"id": 10, "name": "WEBRip-1080p"}, "items": [], "allowed": False},
        {"quality": {"id": 11, "name": "WEBDL-1080p"}, "items": [], "allowed": False}]})
    items += [{"quality": {"id": 20 + i, "name": n}, "items": [], "allowed": False}
              for i, n in enumerate(names[4:])]
    merged = merge_quality_group(items, T.ANIME_MERGE_GROUP)
    flat = [n for it in merged for n in leaves(it)]
    assert sorted(flat) == sorted(names)                  # elke kwaliteit precies één keer
    group = merged[-1]
    assert group["name"] == T.ANIME_MERGE_GROUP["name"]
    assert set(leaves(group)) == set(T.ANIME_MERGE_GROUP["qualities"])
    assert not any(it.get("name") == "WEB 1080p" for it in merged)  # lege groep weg
    # idempotent
    assert merge_quality_group(merged, T.ANIME_MERGE_GROUP)[-1]["items"] == group["items"]
    s = T.ANIME_SCORES
    assert s["Dual Audio"] > s["Resolutie 2160p"] > 0


def test_download_client_url_parts_are_normalised():
    """Praktijk: 'Must be a valid URL path (ie: '/sonarr')' en een rauwe pydantic-fout."""
    from pydantic import ValidationError

    from mediaguard.provisioning.api import _bad
    from mediaguard.provisioning.store import DownloadClientCfg

    dc = DownloadClientCfg.model_validate({
        "name": "SABnzbd", "implementation": "Sabnzbd",
        "host": "https://192.0.2.10:30055/sabnzbd/", "port": 8080})
    assert (dc.host, dc.port, dc.use_ssl, dc.url_base) == ("192.0.2.10", 30055, True,
                                                           "/sabnzbd")
    swapped = DownloadClientCfg.model_validate({
        "name": "SABnzbd", "implementation": "Sabnzbd", "host": "Sabnzdb", "port": 30055,
        "url_base": "http://192.0.2.10"})
    assert (swapped.host, swapped.port, swapped.url_base) == ("192.0.2.10", 30055, "")
    for raw, want in (("sabnzbd", "/sabnzbd"), ("/", ""), ("/sab/", "/sab"), ("", "")):
        assert DownloadClientCfg.model_validate({
            "name": "x", "implementation": "Sabnzbd", "host": "sab", "port": 1,
            "url_base": raw}).url_base == want
    try:
        DownloadClientCfg.model_validate({"name": "x", "implementation": "Sabnzbd",
                                          "host": "  ", "port": 1})
    except ValidationError as exc:
        detail = _bad(exc).detail
        assert "adres" in detail and "vul het adres" in detail
    else:
        raise AssertionError("lege host geaccepteerd")


def test_prowlarr_never_gets_container_internal_address(monkeypatch):
    """Alles-in-één: interne URL is http://127.0.0.1:38989; Prowlarr (andere app) kan die
    niet bereiken. Registreren faalde 4×; het LAN-adres moet gebruikt worden."""
    from mediaguard.provisioning.prowlarr_sync import _reachable_base_url
    from mediaguard.provisioning.store import ProvisioningConfig

    cfg = ProvisioningConfig()
    inst = {"id": "sonarr-series", "url": "http://127.0.0.1:38989", "public_url": None}
    monkeypatch.setenv("MG_PUBLIC_HOST", "192.0.2.10")
    assert _reachable_base_url(cfg, inst) == "http://192.0.2.10:38989"
    inst["public_url"] = "http://192.0.2.10:38989/"
    assert _reachable_base_url(cfg, inst) == "http://192.0.2.10:38989"


def test_real_root_folders_are_adopted_not_templates(env):
    """Rapport 4 nov (H12): Sonarr heeft al /data/Media/TV en Radarr 'Anime Fims'. Geen
    sjabloonmap /data/media/tv aanmaken; de echte map gebruiken en de typfout alleen melden."""
    fake = env.arr["sonarr-series"]
    fake.root_folders.append({"id": 7, "path": "/data/Media/TV", "accessible": True,
                              "freeSpace": 1})
    anime = env.arr.get("radarr-animefilms")
    if anime is not None:
        anime.root_folders.append({"id": 8, "path": "/data/Media/Anime Fims",
                                   "accessible": True, "freeSpace": 1})
    put_cfg(env.client)
    apply(env.client, sections=["rootfolders"])
    assert [r["path"] for r in fake.root_folders] == ["/data/Media/TV"]
    row = env.ctx.db.fetch_one("SELECT default_root_folder FROM instances WHERE "
                               "id='sonarr-series'")
    assert row["default_root_folder"] == "/data/Media/TV"
    if anime is not None:
        plan = env.client.post("/api/v1/provisioning/plan", json={}).json()
        changes = plan["instances"]["radarr-animefilms"]["sections"]["rootfolders"]["changes"]
        assert any("typfout" in c["title_nl"] and "Anime Films" in c["message_nl"]
                   for c in changes)
        assert [r["path"] for r in anime.root_folders] == ["/data/Media/Anime Fims"]
