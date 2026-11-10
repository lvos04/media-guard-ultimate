"""Optimaliseren na migratie: behoud van inhoud/monitoring/inloggegevens, vervangen van
profielen met hertoewijzing, 'Oud ·'-profielen tot opruimen, geen hernoemen zonder
bevestiging, exact terugzetten, en voorvertoning met impactcijfers."""

from __future__ import annotations

import copy
from types import SimpleNamespace
from typing import Any

import pytest
from test_arr_fakes import (
    download_client,
    indexer,
    make_episode,
    make_episode_file,
    make_movie,
    make_series,
)
from test_provisioning_fakes import wire_all

from mediaguard.db import utcnow
from mediaguard.instances.registry import update_instance

OLD_CF = {"id": 501, "name": "Old CF", "includeCustomFormatWhenRenaming": False,
          "specifications": [{"name": "x", "implementation": "ReleaseTitleSpecification",
                              "negate": False, "required": False,
                              "fields": [{"name": "value", "value": "\\bOLD\\b"}]}]}


def _migrated_sonarr(fake) -> None:  # type: ignore[no-untyped-def]
    fake.custom_formats = [copy.deepcopy(OLD_CF)]
    fake.quality_profiles.append({
        "id": 2, "name": "HD-1080p", "upgradeAllowed": True, "cutoff": 3,
        "items": fake._ladder_items({"WEBDL-1080p", "HDTV-720p"}),
        "formatItems": [{"format": 501, "name": "Old CF", "score": 50}],
        "minFormatScore": 0, "cutoffFormatScore": 0})
    dc = download_client(601, "SABnzbd", enable=False)
    for f in dc["fields"]:
        if f["name"] == "apiKey":
            f["value"] = "sab-old-secret"
        if f["name"] == "tvCategory":
            f["value"] = "sonarr"
    fake.providers["downloadclient"][601] = dc
    fake.add_indexer(indexer(701, "NZBgeek", rss=False, auto=False))
    nyaa = indexer(702, "Nyaa", rss=False, auto=False, protocol="torrent")
    nyaa["fields"][0]["value"] = "https://nyaa.si"
    fake.add_indexer(nyaa)
    fake.release_profiles = [{"id": 801, "name": "Oud", "enabled": True, "required": ["x"],
                              "ignored": [], "indexerId": 0, "tags": []}]
    fake.rpm[901] = {"id": 901, "host": "sabnzbd", "remotePath": "/dl/",
                     "localPath": "/downloads/"}
    fake.tags[11] = {"id": 11, "label": "kids"}
    fake.root_folders = [{"id": 1, "path": "/data/media/tv", "accessible": True, "freeSpace": 1,
                          "unmappedFolders": []}]
    a = make_series(1, "Show A", 100, quality_profile_id=2, seasons={1: True, 2: False},
                    tags=[11], monitor_new_items="none", root="/data/media/tv")
    b = make_series(2, "Anime B", 200, quality_profile_id=2, series_type="anime",
                    root="/data/media/tv")
    fake.add_series(a, [make_episode(11, 1, 1, 1, has_file=True, file_id=1),
                        make_episode(12, 1, 1, 2, monitored=False, has_file=True, file_id=2),
                        make_episode(13, 1, 2, 1, monitored=False)],
                    [make_episode_file(1, 1, 1, quality="HDTV-720p"),
                     make_episode_file(2, 1, 1, quality="WEBDL-2160p")])
    fake.add_series(b, [make_episode(21, 2, 1, 1, has_file=True, file_id=3, absolute=1)],
                    [make_episode_file(3, 2, 1, quality="WEBDL-1080p")])


def _migrated_radarr(fake) -> None:  # type: ignore[no-untyped-def]
    fake.quality_profiles.append({
        "id": 2, "name": "HD-1080p", "upgradeAllowed": True, "cutoff": 7,
        "items": fake._ladder_items({"Bluray-1080p"}), "formatItems": [],
        "minFormatScore": 0, "cutoffFormatScore": 0, "language": {"id": 1, "name": "English"}})
    fake.root_folders = [{"id": 1, "path": "/data/media/movies", "accessible": True,
                          "freeSpace": 1, "unmappedFolders": []}]
    m1 = make_movie(1, "Film A", 500, has_file=True, root="/data/media/movies/")
    m1["qualityProfileId"] = 2
    m2 = make_movie(2, "Film B", 501, root="/data/media/movies/")
    m2["qualityProfileId"] = 2
    fake.movies = {1: m1, 2: m2}


def _mark_shadow_import(ctx, iid: str) -> None:  # type: ignore[no-untyped-def]
    rid = ctx.db.execute_insert("INSERT INTO migration_runs(name,created_at,updated_at) "
                                "VALUES ('m',?,?)", (utcnow(), utcnow()))
    ctx.db.execute("INSERT INTO migration_steps(run_id,step,scope,status,updated_at) VALUES "
                   "(?,?,?,?,?)", (rid, "shadow_import", iid, "succeeded", utcnow()))


@pytest.fixture
def env(client, ctx):  # type: ignore[no-untyped-def]
    fakes = wire_all(ctx)
    _migrated_sonarr(fakes["sonarr-series"])
    _migrated_radarr(fakes["radarr-films"])
    for iid in ("sonarr-series", "radarr-films"):
        _mark_shadow_import(ctx, iid)
    update_instance(ctx, "sonarr-series", {"shadow_mode": True})
    return SimpleNamespace(arr=fakes, client=client, ctx=ctx, ss=fakes["sonarr-series"],
                           rf=fakes["radarr-films"])


def state(fake) -> dict[str, Any]:  # type: ignore[no-untyped-def]
    return copy.deepcopy({
        "qp": fake.quality_profiles, "cf": fake.custom_formats,
        "dc": fake.providers["downloadclient"], "idx": fake.providers["indexer"],
        "naming": fake.configs["naming"], "mm": fake.configs["mediamanagement"],
        "delay": fake.delay_profiles, "release": fake.release_profiles,
        "qd": fake.quality_definitions,
        "assign": {k: v["qualityProfileId"] for k, v in
                   (fake.series or fake.movies).items()}})


def content(fake) -> dict[str, Any]:  # type: ignore[no-untyped-def]
    series = {k: {kk: vv for kk, vv in v.items() if kk != "qualityProfileId"}
              for k, v in fake.series.items()}
    movies = {k: {kk: vv for kk, vv in v.items() if kk != "qualityProfileId"}
              for k, v in fake.movies.items()}
    # MediaGuard voegt alleen eigen tags toe ('mediaguard'); bestaande tags blijven gelijk
    tags = {k: v for k, v in fake.tags.items() if v["label"] not in ("mediaguard", "anime")}
    return copy.deepcopy({"series": series, "movies": movies, "episodes": fake.episodes,
                          "files": fake.episode_files, "rpm": fake.rpm, "tags": tags,
                          "history": fake.history})


def fval(res: dict[str, Any], name: str) -> Any:
    return next((f.get("value") for f in res.get("fields") or [] if f["name"] == name), None)


def optimize(c, confirm: bool, **kw: Any) -> dict[str, Any]:  # type: ignore[no-untyped-def]
    r = c.post("/api/v1/provisioning/optimize-migrated",
               json={"instances": ["sonarr-series", "radarr-films"], "confirm": confirm, **kw})
    assert r.status_code == 200, r.text
    return r.json()


def test_hold_until_confirmed_only_essentials(env):
    c, ss = env.client, env.ss
    st = c.get("/api/v1/provisioning/status").json()
    modes = {i["instance_id"]: i["mode"] for i in st["instances"]}
    assert modes["sonarr-series"] == "hold" and modes["sonarr-anime"] == "manage"
    before = state(ss)
    r = c.post("/api/v1/provisioning/apply", json={"confirm": True,
                                                   "instances": ["sonarr-series"]}).json()
    assert {x["section"] for x in r["applied"]} <= {"tags", "webhook", "rootfolders"}
    assert state(ss) == before
    assert any(n["name"] == "MediaGuard" for n in ss.providers["notification"].values())


def test_optimize_preview_then_apply_keeps_content(env):  # noqa: C901
    c, ss, rf = env.client, env.ss, env.rf
    before_content = {"ss": content(ss), "rf": content(rf)}
    writes = (len(ss.writes()), len(rf.writes()))
    pv = optimize(c, False)
    assert pv["requires_confirmation"] and (len(ss.writes()), len(rf.writes())) == writes
    p_ss = next(i for i in pv["instances"] if i["instance_id"] == "sonarr-series")
    imp = p_ss["impact"]
    assert imp["items"] == 2 and imp["files"] == 3
    assert imp["upgrade_candidates"] == 2 and imp["upgrade_items"] == 2
    assert imp["estimated_searches"] == 2 and imp["estimated_days"] == 1
    assert "backfill-quota" in imp["message_nl"]
    assert p_ss["reassign"]["by_profile"] == {"MediaGuard · WEB-2160p": 1,
                                              "MediaGuard · Anime Dual Audio": 1}
    lines = "\n".join(p_ss["text_nl"])
    assert "Oud profiel 'HD-1080p' hernoemen naar 'Oud · HD-1080p'" in lines
    assert "Anime-indexer 'Nyaa' uitzetten" in lines
    assert "Categorie van gemigreerde downloadclient 'SABnzbd' → tv" in lines
    assert "releaseprofiel 'Oud' uitzetten" in lines
    p_rf = next(i for i in pv["instances"] if i["instance_id"] == "radarr-films")
    assert p_rf["impact"]["upgrade_candidates"] == 1  # Bluray-1080p < cutoff Bluray-2160p

    res = optimize(c, True)
    assert res["status"] == "succeeded", res
    # inhoud, monitoring en historie exact gelijk
    assert content(ss) == before_content["ss"] and content(rf) == before_content["rf"]
    # oude profielen hernoemd, nieuwe aanwezig, alles toegewezen
    names = {p["name"]: p["id"] for p in ss.quality_profiles}
    assert "Oud · HD-1080p" in names and "HD-1080p" not in names
    assert ss.series[1]["qualityProfileId"] == names["MediaGuard · WEB-2160p"]
    assert ss.series[2]["qualityProfileId"] == names["MediaGuard · Anime Dual Audio"]
    rnames = {p["name"]: p["id"] for p in rf.quality_profiles}
    assert {m["qualityProfileId"] for m in rf.movies.values()} == {rnames["MediaGuard · UHD HDR"]}
    row = env.ctx.db.fetch_one("SELECT default_quality_profile_id FROM instances WHERE "
                               "id='sonarr-series'")
    assert row["default_quality_profile_id"] == names["MediaGuard · WEB-2160p"]
    # inloggegevens behouden, categorie geoptimaliseerd, schaduwmodus gerespecteerd
    dc = ss.providers["downloadclient"][601]
    assert fval(dc, "apiKey") == "sab-old-secret" and fval(dc, "tvCategory") == "tv"
    assert dc["enable"] is False and dc["name"] == "SABnzbd"
    nyaa = ss.providers["indexer"][702]
    assert nyaa["name"] == "Nyaa [uit: anime-indexer]" and not nyaa["enableRss"]
    assert fval(nyaa, "apiKey") == "idx-secret-702"
    assert ss.providers["indexer"][701]["name"] == "NZBgeek"
    assert fval(ss.providers["indexer"][701], "apiKey") == "idx-secret-701"
    assert not any(i["enableRss"] for i in ss.providers["indexer"].values())
    assert ss.release_profiles[0]["enabled"] is False
    assert ss.configs["naming"]["renameEpisodes"] is True
    assert ss.configs["mediamanagement"]["copyUsingHardlinks"] is True
    assert any(cf["name"] == "MediaGuard · Dual Audio" for cf in ss.custom_formats)
    assert any(cf["name"] == "Old CF" for cf in ss.custom_formats)
    # nooit automatisch hernoemen
    assert not [c for c in ss.commands.values() if c["name"] == "RenameFiles"]
    st = {i["instance_id"]: i for i in c.get("/api/v1/provisioning/status").json()["instances"]}
    assert st["sonarr-series"]["mode"] == "optimized"
    assert st["sonarr-series"]["optimize"]["phase"] == "optimized"
    # tweede keer: niets meer te doen
    again = c.post("/api/v1/provisioning/apply", json={"confirm": True,
                                                       "instances": ["sonarr-series"]}).json()
    assert again["summary"]["applied"] == 0 and again["remaining"] == []
    # opruimen: eerst voorvertoning, dan pas verwijderen
    pv = c.post("/api/v1/provisioning/optimize-migrated/cleanup",
                json={"instances": ["sonarr-series"]}).json()
    assert pv["requires_confirmation"]
    assert sorted(p["original"] for p in pv["instances"][0]["profiles"]) == ["Any", "HD-1080p"]
    assert "Oud · HD-1080p" in {p["name"] for p in ss.quality_profiles}
    # praktijk (Radarr 'QualityProfile [10] is in use'): een importlijst op een oud profiel
    old_pid = next(p["id"] for p in ss.quality_profiles if p["name"] == "Oud · HD-1080p")
    ss.providers["importlist"][77] = {"id": 77, "name": "Trakt", "implementation": "TraktList",
                                      "qualityProfileId": old_pid, "fields": []}
    c.post("/api/v1/provisioning/optimize-migrated/cleanup",
           json={"instances": ["sonarr-series"], "confirm": True})
    assert "Oud · HD-1080p" not in {p["name"] for p in ss.quality_profiles}
    moved = ss.providers["importlist"][77]["qualityProfileId"]
    assert moved != old_pid and moved in {p["id"] for p in ss.quality_profiles}


def test_rollback_restores_exactly(env):
    c, ss, rf = env.client, env.ss, env.rf
    before = {"ss": state(ss), "rf": state(rf)}
    before_content = content(ss)
    optimize(c, True)
    assert state(ss) != before["ss"]
    snaps = env.ctx.db.fetch_all("SELECT data_json FROM provisioning_snapshots")
    assert snaps and all("sab-old-secret" not in r["data_json"] and
                         "idx-secret-70" not in r["data_json"] for r in snaps)
    pv = c.post("/api/v1/provisioning/optimize-migrated/rollback",
                json={"instances": ["sonarr-series", "radarr-films"]}).json()
    assert pv["requires_confirmation"] and state(ss) != before["ss"]
    r = c.post("/api/v1/provisioning/optimize-migrated/rollback",
               json={"instances": ["sonarr-series", "radarr-films"], "confirm": True}).json()
    assert r["status"] == "succeeded", r
    assert state(ss) == before["ss"] and state(rf) == before["rf"]
    assert content(ss) == before_content
    st = {i["instance_id"]: i for i in c.get("/api/v1/provisioning/status").json()["instances"]}
    assert st["sonarr-series"]["mode"] == "hold"
    # ook na opruimen kan nog exact (op naam) worden teruggezet
    optimize(c, True)
    c.post("/api/v1/provisioning/optimize-migrated/cleanup",
           json={"instances": ["sonarr-series"], "confirm": True})
    c.post("/api/v1/provisioning/optimize-migrated/rollback",
           json={"instances": ["sonarr-series"], "confirm": True})
    names = {p["name"]: p["id"] for p in ss.quality_profiles}
    assert set(names) == {"Any", "HD-1080p"}
    assert {s["qualityProfileId"] for s in ss.series.values()} == {names["HD-1080p"]}


def test_rename_only_after_explicit_confirmation(env):
    c, ss, ctx = env.client, env.ss, env.ctx
    ss.rename_preview[1] = [
        {"seriesId": 1, "seasonNumber": 1, "episodeNumbers": [1], "episodeFileId": 1,
         "existingPath": "Season 01/old1.mkv", "newPath": "Season 01/Show A - S01E01.mkv"},
        {"seriesId": 1, "seasonNumber": 1, "episodeNumbers": [2], "episodeFileId": 2,
         "existingPath": "Season 01/old2.mkv", "newPath": "Season 01/Show A - S01E02.mkv"}]
    ctx.db.execute("INSERT INTO locks(resource,owner,purpose,acquired_at,expires_at) VALUES "
                   "(?,?,?,?,?)", ("sonarr-series:file:2", "encode", "av1", utcnow(),
                                   "2999-01-01T00:00:00+00:00"))
    pv = c.post("/api/v1/provisioning/rename/preview",
                json={"instance_id": "sonarr-series", "ids": [1]}).json()
    assert pv["count"] == 2 and [f["locked"] for f in pv["files"]] == [False, True]
    assert not [x for x in ss.commands.values() if x["name"] == "RenameFiles"]
    files = [{"owner_id": f["owner_id"], "file_id": f["file_id"]} for f in pv["files"]]
    r = c.post("/api/v1/provisioning/rename/apply",
               json={"instance_id": "sonarr-series", "files": files})
    assert r.status_code == 409
    assert not [x for x in ss.commands.values() if x["name"] == "RenameFiles"]
    r = c.post("/api/v1/provisioning/rename/apply",
               json={"instance_id": "sonarr-series", "files": files, "confirm": True}).json()
    assert r["renamed_files"] == 1 and len(r["skipped_locked"]) == 1
    (cmd,) = [x for x in ss.commands.values() if x["name"] == "RenameFiles"]
    assert cmd["body"]["seriesId"] == 1 and cmd["body"]["files"] == [1]
