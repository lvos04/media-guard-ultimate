"""Migratiewizard: vier oude productie-instanties -> vier beheerde forks (C1, R3, R6.3/R6.9).

Kernbewijs: de oude instanties krijgen vóór de cutover GEEN enkel muterend verzoek
(asserties op de vastgelegde fake-requests), en na cutover/rollback staan ze exact in
hun vorige staat."""

from __future__ import annotations

import hashlib
import io
import json
import zipfile
from pathlib import Path
from typing import Any

import pytest
from test_arr_fakes import (
    FakeSeerr,
    download_client,
    indexer,
    make_episode,
    make_movie,
    make_series,
    seerr_server,
    wire,
)
from test_migration_fakes import MigFakeArr, make_backup_zip

from mediaguard.instances.registry import instance_row

TARGET_KEYS = {"sonarr-series": "series-key-111", "sonarr-anime": "anime-key-222",
               "radarr-films": "films-key-333", "radarr-animefilms": "afilm-key-444"}
OLD = {"old-sonarr": ("sonarr", "http://old-sonarr:8989", "OLDKEY-sonarr-1"),
       "old-anime": ("sonarr", "http://old-anime:8990", "OLDKEY-anime-2"),
       "old-radarr": ("radarr", "http://old-radarr:7878", "OLDKEY-radarr-3"),
       "old-radarr-anime": ("radarr", "http://old-radarr-anime:7879", "OLDKEY-ranime-4")}
EXPECTED_MAP = {"old-sonarr": "sonarr-series", "old-anime": "sonarr-anime",
                "old-radarr": "radarr-films", "old-radarr-anime": "radarr-animefilms"}


def _episodes(sid: int, base: int, spec: list[tuple[int, int, bool, float]]) -> list[dict]:
    return [make_episode(base + i, sid, s, e, monitored=m, aired_days=d)
            for i, (s, e, m, d) in enumerate(spec)]


def build_sources() -> dict[str, MigFakeArr]:
    src: dict[str, MigFakeArr] = {}
    # reguliere series: seizoen 2 bewust niet gemonitord, episode-uitzonderingen, toekomst
    s = MigFakeArr("sonarr", api_key=OLD["old-sonarr"][2])
    s.tags = {1: {"id": 1, "label": "4k"}}
    s.add_indexer(indexer(1, "NZBgeek", rss=True, auto=True))
    s.add_indexer(indexer(2, "Torznab", rss=False, auto=True, protocol="torrent"))
    s.providers["downloadclient"][1] = download_client(1)
    s.rpm[1] = {"id": 1, "host": "sabnzbd", "remotePath": "/downloads/", "localPath": "/data/dl/"}
    s.add_series(make_series(1, "Attack on Titan", 5001, seasons={1: True, 2: False, 3: True},
                             tags=[1], monitor_new_items="all"),
                 _episodes(1, 100, [(1, 1, True, -100), (1, 2, False, -90), (2, 1, False, -50),
                                    (3, 1, True, 30), (3, 2, False, 37)]))
    s.add_series(make_series(2, "Ended Show", 5002, monitored=False, seasons={1: False},
                             monitor_new_items="none", status="ended"),
                 _episodes(2, 200, [(1, 1, False, -400)]))
    src["old-sonarr"] = s
    # anime: zelfde TITEL "Attack on Titan", ander TVDb-ID; koppeling mag niet op titel
    a = MigFakeArr("sonarr", api_key=OLD["old-anime"][2])
    a.root_folders = [{"id": 1, "path": "/anime/", "accessible": True, "freeSpace": 1}]
    a.quality_profiles.append({"id": 3, "name": "Anime Dual Audio", "cutoff": 3,
                               "upgradeAllowed": True, "items": []})
    a.add_indexer(indexer(1, "AnimeTosho", rss=True, auto=True))
    a.providers["downloadclient"][1] = download_client(1, "qBit-anime", "torrent")
    a.add_series(make_series(1, "Attack on Titan", 267440, series_type="anime", root="/anime/",
                             quality_profile_id=3, seasons={1: True, 2: True}),
                 _episodes(1, 300, [(1, 1, True, -500), (2, 1, True, 10)]))
    a.add_series(make_series(2, "Frieren", 424536, series_type="anime", root="/anime/",
                             quality_profile_id=3, seasons={1: True}),
                 _episodes(2, 400, [(1, 1, True, -60), (1, 2, False, -53)]))
    src["old-anime"] = a
    r = MigFakeArr("radarr", api_key=OLD["old-radarr"][2])
    r.root_folders[0]["path"] = "/movies/"
    r.add_indexer(indexer(1, "NZBgeek", rss=True, auto=False))
    r.providers["downloadclient"][1] = download_client(1)
    r.movies = {1: make_movie(1, "Wadi", 438631), 2: make_movie(2, "Old Film", 11,
                                                                monitored=False)}
    src["old-radarr"] = r
    ra = MigFakeArr("radarr", api_key=OLD["old-radarr-anime"][2])
    ra.root_folders = [{"id": 1, "path": "/animemovies/", "accessible": True, "freeSpace": 1}]
    ra.tags = {1: {"id": 1, "label": "anime"}}
    ra.add_indexer(indexer(1, "AnimeTosho", rss=True, auto=True))
    ra.providers["downloadclient"][1] = download_client(1, "qBit-anime", "torrent")
    m = make_movie(1, "Your Name", 372058, root="/animemovies/")
    m["tags"] = [1]
    ra.movies = {1: m}
    src["old-radarr-anime"] = ra
    for fake in src.values():
        for mv in fake.movies.values():
            mv["path"] = mv["folderName"]
            mv["qualityProfileId"] = 1
            mv.setdefault("tags", [])
    return src


@pytest.fixture
def world(client, ctx):  # type: ignore[no-untyped-def]
    for key in ("command_wait_seconds", "restart_wait_seconds", "episode_wait_seconds"):
        ctx.config.set("global", "", f"migration.{key}", 0, "test")
    targets: dict[str, MigFakeArr] = {}
    for iid, key in TARGET_KEYS.items():
        app = "sonarr" if iid.startswith("sonarr") else "radarr"
        fake = MigFakeArr(app, api_key=key)
        fake.providers["indexer"].clear()
        targets[iid] = wire(ctx, iid, fake)
    sources = build_sources()
    ctx.services["source_transports"] = {sid: f.transport() for sid, f in sources.items()}
    # metadata-catalogus van de doelen = afleveringen zoals TVDb ze kent
    for sid, fake in sources.items():
        tgt = targets[EXPECTED_MAP[sid]]
        for series in fake.series.values():
            tgt.catalog[series["tvdbId"]] = [e for e in fake.episodes.values()
                                             if e["seriesId"] == series["id"]]
    seerr = FakeSeerr()
    ctx.services["seerr_transport"] = seerr.transport()
    seerr.servers["sonarr"] = [
        seerr_server(0, "Sonarr", "old-sonarr", 8989, OLD["old-sonarr"][2], is_default=True),
        seerr_server(1, "Sonarr Anime", "old-anime", 8990, OLD["old-anime"][2],
                     is_default=False, directory="/anime/")]
    seerr.servers["radarr"] = [
        seerr_server(0, "Radarr", "old-radarr", 7878, OLD["old-radarr"][2], is_default=True,
                     kind="radarr", directory="/movies/")]
    r = client.put("/api/v1/seerr/settings", json={"url": "http://seerr:5055",
                                                   "api_key": seerr.api_key})
    assert r.status_code == 200, r.text
    for sid, (_, url, key) in OLD.items():
        r = client.post("/api/v1/migration/sources", json={"id": sid, "name": sid, "url": url,
                                                           "api_key": key})
        assert r.status_code == 200, r.text
    return {"sources": sources, "targets": targets, "seerr": seerr, "client": client,
            "ctx": ctx}


def new_run(client: Any, sources: list[str], **options: Any) -> int:
    r = client.post("/api/v1/migration/runs", json={"name": "Productie", "source_ids": sources,
                                                    "options": options})
    assert r.status_code == 200, r.text
    return int(r.json()["id"])


def mapped_run(client: Any, sources: list[str] | None = None, **options: Any) -> int:
    run_id = new_run(client, sources or list(OLD), **options)
    r = client.post("/api/v1/migration/instance-mapping/preview", json={"run_id": run_id})
    assert r.status_code == 200, r.text
    assert client.post(f"/api/v1/migration/runs/{run_id}/mapping/confirm").status_code == 200
    r = client.post("/api/v1/migration/preflight", json={"run_id": run_id, "wait": True})
    assert r.json()["status"] in ("succeeded", "warning"), r.text
    return run_id


def shadow(client: Any, run_id: int, target: str, **kw: Any) -> dict[str, Any]:
    r = client.post(f"/api/v1/migration/runs/{run_id}/shadow-import",
                    json={"target_instance_id": target, "wait": True, **kw})
    assert r.status_code == 200, r.text
    return r.json()


def verify(client: Any, run_id: int, target: str) -> dict[str, Any]:
    r = client.post("/api/v1/migration/instance-mapping/verify",
                    json={"run_id": run_id, "target_instance_id": target, "wait": True})
    assert r.status_code == 200, r.text
    return r.json()[target]["report"]


def assert_source_untouched(sources: dict[str, MigFakeArr]) -> None:
    for sid, fake in sources.items():
        assert fake.writes() == [], f"{sid} kreeg muterende verzoeken: {fake.writes()}"


# -- bronnen ---------------------------------------------------------------------------------
def test_source_registration_detects_app_and_hides_key(world):
    client, ctx = world["client"], world["ctx"]
    data = {s["id"]: s for s in client.get("/api/v1/migration/sources").json()}
    assert data["old-anime"]["app"] == "sonarr" and data["old-radarr"]["app"] == "radarr"
    assert data["old-sonarr"]["version"] == "4.0.14.2939" and data["old-sonarr"]["api_key_set"]
    assert "OLDKEY" not in json.dumps(data)
    assert ctx.secrets.get("source:old-sonarr:apikey") == "OLDKEY-sonarr-1"
    row = ctx.db.fetch_one("SELECT ciphertext FROM secrets WHERE name='source:old-sonarr:apikey'")
    assert b"OLDKEY" not in bytes(row["ciphertext"])
    assert_source_untouched(world["sources"])


def test_source_client_structurally_read_only(world):
    import asyncio

    from mediaguard.migration.sources import SourceWriteBlocked, source_client

    ctx = world["ctx"]

    async def run() -> None:
        c = source_client(ctx, "old-sonarr")
        for coro in (c.post("command", {"name": "RssSync"}), c.put("indexer/1", {}),
                     c.delete("series/1"), c.command("Backup")):
            with pytest.raises(SourceWriteBlocked):
                await coro
        b = source_client(ctx, "old-sonarr", grant="backup")
        with pytest.raises(SourceWriteBlocked):
            await b.post("command", {"name": "RssSync"})
        with pytest.raises(SourceWriteBlocked):
            await b.put("indexer/1", {})

    asyncio.run(run())
    assert_source_untouched(world["sources"])


# -- mapping ---------------------------------------------------------------------------------
def test_four_instance_mapping_anime_not_by_title(world):
    client = world["client"]
    run_id = new_run(client, list(OLD))
    r = client.post("/api/v1/migration/instance-mapping/preview", json={"run_id": run_id}).json()
    got = {m["source_id"]: m["target_instance_id"] for m in r["mappings"]}
    assert got == EXPECTED_MAP
    flags = {(i["source_id"], i["flag"]) for i in r["items"]}
    # zelfde titel in twee bronnen maar ander TVDb-ID: GEEN duplicaat, geen verkeerde scope
    assert not any(f.startswith("duplicate") for _, f in flags), flags
    assert all(not m["duplicate_target_with"] for m in r["matrix"])
    # gebruiker kan kiezen; dubbel doel wordt geweigerd bij bevestigen
    r = client.put(f"/api/v1/migration/runs/{run_id}/mapping", json={"entries": [
        {"source_id": "old-anime", "target_instance_id": "sonarr-series"}]})
    assert r.status_code == 200
    dup = next(m for m in r.json()["matrix"] if m["source_id"] == "old-anime")
    assert dup["duplicate_target_with"] == ["old-sonarr"]
    assert client.post(f"/api/v1/migration/runs/{run_id}/mapping/confirm").status_code == 409
    bad = client.put(f"/api/v1/migration/runs/{run_id}/mapping", json={"entries": [
        {"source_id": "old-anime", "target_instance_id": "radarr-films"}]})
    assert bad.status_code == 422
    # anime-serie die per ongeluk in de reguliere bron staat wordt gesignaleerd, niet verplaatst
    world["sources"]["old-sonarr"].series[2]["seriesType"] = "anime"
    r = client.post("/api/v1/migration/instance-mapping/preview",
                    json={"run_id": run_id, "reset": True}).json()
    assert ("old-sonarr", "anime_in_standard") in {(i["source_id"], i["flag"])
                                                   for i in r["items"]}
    assert_source_untouched(world["sources"])


# -- API-import --------------------------------------------------------------------------------
def test_api_import_identical_and_source_read_only(world):
    client, targets, sources = world["client"], world["targets"], world["sources"]
    run_id = mapped_run(client)
    for target in TARGET_KEYS:
        out = shadow(client, run_id, target, mode="api")
        assert out["status"] in ("succeeded", "warning"), out
        rep = verify(client, run_id, target)
        assert rep["verdict"] == "go", (target, rep["mismatches"])
    t = targets["sonarr-series"]
    aot = next(s for s in t.series.values() if s["tvdbId"] == 5001)
    assert aot["monitorNewItems"] == "all"
    assert {x["seasonNumber"]: x["monitored"] for x in aot["seasons"]} == {1: True, 2: False,
                                                                           3: True}
    eps = {(e["seasonNumber"], e["episodeNumber"]): e["monitored"] for e in t.episodes.values()
           if e["seriesId"] == aot["id"]}
    assert eps == {(1, 1): True, (1, 2): False, (2, 1): False, (3, 1): True, (3, 2): False}
    assert [t.tags[i]["label"] for i in aot["tags"]] == ["4k"]
    ended = next(s for s in t.series.values() if s["tvdbId"] == 5002)
    assert ended["monitored"] is False and ended["monitorNewItems"] == "none"
    # anime ging naar de anime-instantie op TVDb-ID, niet op titel
    assert {s["tvdbId"] for s in targets["sonarr-anime"].series.values()} == {267440, 424536}
    assert 267440 not in {s["tvdbId"] for s in t.series.values()}
    # schaduwmodus: alles uit op het doel, originele standen bewaard
    assert all(not i["enableRss"] and not i["enableAutomaticSearch"]
               for f in targets.values() for i in f.providers["indexer"].values())
    assert all(not d["enable"] for f in targets.values()
               for d in f.providers["downloadclient"].values())
    assert targets["sonarr-series"].configs["indexer"]["rssSyncInterval"] == 0
    assert instance_row(world["ctx"], "sonarr-series")["shadow_mode"] == 1
    assert {(m["host"], m["localPath"]) for m in t.rpm.values()} == {("sabnzbd", "/data/dl/")}
    assert len(targets["radarr-films"].movies) == 2
    assert_source_untouched(sources)


def test_mismatch_is_no_go_and_blocks_cutover(world):
    client, targets = world["client"], world["targets"]
    run_id = mapped_run(client, ["old-sonarr"])
    shadow(client, run_id, "sonarr-series", mode="api")
    t = targets["sonarr-series"]
    ep = next(e for e in t.episodes.values() if e["seasonNumber"] == 3 and
              e["episodeNumber"] == 1)
    ep["monitored"] = False  # toekomstige aflevering niet meer gemonitord
    r = client.post("/api/v1/migration/instance-mapping/verify",
                    json={"run_id": run_id, "target_instance_id": "sonarr-series",
                          "wait": True, "sync_delta": False}).json()["sonarr-series"]["report"]
    assert r["verdict"] == "no_go"
    fields = {m["field"] for m in r["mismatches"]}
    assert {"episodes", "future_monitored", "future_monitored_total"} <= fields
    hard = [m["id"] for m in r["mismatches"]]
    res = client.post(f"/api/v1/migration/runs/{run_id}/accept-mismatches",
                      json={"target_instance_id": "sonarr-series", "mismatch_ids": hard,
                            "reason": "test"})
    assert res.status_code == 409
    res = client.post("/api/v1/migration/cutover", json={
        "run_id": run_id, "targets": ["sonarr-series"],
        "confirm_names": {"sonarr-series": "sonarr-series"}, "wait": True})
    assert res.status_code == 409 and "NO-GO" in res.text
    assert_source_untouched(world["sources"])
    # delta-sync (alleen doel) herstelt het verschil
    assert verify(client, run_id, "sonarr-series")["verdict"] == "go"


def test_api_import_resumes_after_crash_without_duplicates(world):
    client, t = world["client"], world["targets"]["sonarr-series"]
    run_id = mapped_run(client, ["old-sonarr"])
    t.fail_paths = {"/api/v3/episode/monitor": 500}
    out = shadow(client, run_id, "sonarr-series", mode="api")
    assert out["status"] == "failed"
    step = client.get(f"/api/v1/migration/runs/{run_id}").json()
    assert next(s for s in step["steps"] if s["step"] == "shadow_import" and
                s["scope"] == "sonarr-series")["status"] == "failed"
    t.fail_paths = {}
    assert shadow(client, run_id, "sonarr-series", mode="api")["status"] == "succeeded"
    assert shadow(client, run_id, "sonarr-series", mode="api")["status"] == "succeeded"
    assert len(t.calls_to("POST", "/api/v3/series")) == 2
    assert len(t.calls_to("POST", "/api/v3/tag")) == 1
    assert len(t.providers["indexer"]) == 2 and len(t.providers["downloadclient"]) == 1
    assert len(t.series) == 2
    assert verify(client, run_id, "sonarr-series")["verdict"] == "go"


def test_masked_credentials_flagged_and_resolved(world):
    client, ctx = world["client"], world["ctx"]
    src = world["sources"]["old-radarr"]
    masked = MigFakeArr("radarr", api_key=src.api_key, mask_secrets=True)
    masked.__dict__.update({k: v for k, v in src.__dict__.items()
                            if k in ("movies", "providers", "root_folders", "tags")})
    ctx.services["source_transports"]["old-radarr"] = masked.transport()
    run_id = mapped_run(client, ["old-radarr"])
    out = shadow(client, run_id, "radarr-films", mode="api")
    assert out["status"] == "warning"
    names = {(c["kind"], c["name"]) for c in out["result"]["credentials_needed"]}
    assert names == {("indexer", "NZBgeek"), ("downloadclient", "SABnzbd")}
    t = world["targets"]["radarr-films"]
    assert "********" not in json.dumps(t.providers)
    rep = verify(client, run_id, "radarr-films")
    assert rep["verdict"] == "no_go"
    assert {m["category"] for m in rep["mismatches"]} == {"credentials"}
    for kind in ("indexer", "downloadclient"):  # gebruiker vult sleutels in op het doel
        for res in t.providers[kind].values():
            for f in res["fields"]:
                if f["name"] == "apiKey":
                    f["value"] = "nieuw-ingevuld"
    assert verify(client, run_id, "radarr-films")["verdict"] == "go"
    assert masked.writes() == []


# -- native back-up/restore --------------------------------------------------------------------
def test_backup_existing_is_get_only_and_create_requires_opt_in(world):
    client, src = world["client"], world["sources"]["old-sonarr"]
    src.add_backup("scheduled")
    run_id = new_run(client, ["old-sonarr"])
    r = client.post(f"/api/v1/migration/runs/{run_id}/backup",
                    json={"source_id": "old-sonarr", "wait": True})
    assert r.json()["status"] == "succeeded", r.text
    assert src.writes() == []
    r = client.post(f"/api/v1/migration/runs/{run_id}/backup",
                    json={"source_id": "old-sonarr", "strategy": "create", "wait": True})
    assert r.status_code == 409
    r = client.post(f"/api/v1/migration/runs/{run_id}/backup",
                    json={"source_id": "old-sonarr", "strategy": "create",
                          "confirm_create": True, "wait": True})
    assert r.json()["status"] == "succeeded", r.text
    writes = src.writes()
    assert [(w[0], w[1], w[3]) for w in writes] == [("POST", "/api/v3/command",
                                                     {"name": "Backup"})]
    backups = world["ctx"].db.fetch_dicts("SELECT * FROM source_backups")
    assert all(Path(b["path"]).is_relative_to(world["ctx"].settings.backup_dir / "sources")
               for b in backups)
    for b in backups:
        assert hashlib.sha256(Path(b["path"]).read_bytes()).hexdigest() == b["sha256"]
        assert json.loads(b["verify_json"])["integrity"] == "ok"


def _native_ready(world: dict[str, Any], source: str = "old-sonarr") -> int:
    client = world["client"]
    world["sources"][source].add_backup("scheduled")
    run_id = mapped_run(client, [source])
    r = client.post(f"/api/v1/migration/runs/{run_id}/backup",
                    json={"source_id": source, "wait": True})
    assert r.json()["status"] == "succeeded"
    return run_id


def test_native_restore_disables_rss_in_copy_original_untouched(world):
    client, ctx, t = world["client"], world["ctx"], world["targets"]["sonarr-series"]
    run_id = _native_ready(world)
    b = ctx.db.fetch_one("SELECT * FROM source_backups")
    before = hashlib.sha256(Path(b["path"]).read_bytes()).hexdigest()
    out = shadow(client, run_id, "sonarr-series")
    assert out["status"] == "succeeded", out
    assert out["result"]["mode"] == "native"
    assert len(t.restores) == 1 and t.restarts == 1
    assert hashlib.sha256(Path(b["path"]).read_bytes()).hexdigest() == before == b["sha256"]
    with zipfile.ZipFile(io.BytesIO(t.restores[0])) as z:
        cfg = z.read("config.xml").decode()
    assert "<ApiKey>series-key-111</ApiKey>" in cfg and "OLD-SOURCE-KEY" not in cfg
    idx = {i["name"]: i for i in t.providers["indexer"].values()}
    assert not idx["NZBgeek"]["enableRss"] and not idx["NZBgeek"]["enableAutomaticSearch"]
    assert idx["NZBgeek"]["enableInteractiveSearch"] is True
    assert {f["name"]: f["value"] for f in idx["NZBgeek"]["fields"]}["apiKey"] == "idx-secret-1"
    assert all(not d["enable"] for d in t.providers["downloadclient"].values())
    assert t.configs["indexer"]["rssSyncInterval"] == 0
    assert [c[1] for c in t.calls if c[1] == "/api/v3/command"]  # rescan aangevraagd
    st = ctx.db.fetch_one("SELECT state_json FROM migration_shadow_states")
    state = json.loads(st["state_json"])
    assert state["indexers"]["Torznab"] == {"id": 2, "enableRss": False,
                                            "enableAutomaticSearch": True}
    assert verify(client, run_id, "sonarr-series")["verdict"] == "go"
    assert_source_untouched(world["sources"])


def test_native_resume_after_restore_before_restart(world):
    client, t = world["client"], world["targets"]["sonarr-series"]
    run_id = _native_ready(world)
    t.fail_paths = {"/api/v3/system/status": 503}
    out = shadow(client, run_id, "sonarr-series")
    assert out["status"] == "queued" and len(t.restores) == 1
    t.fail_paths = {}
    out = shadow(client, run_id, "sonarr-series")
    assert out["status"] == "succeeded", out
    assert len(t.restores) == 1  # niet opnieuw ge-restored


# -- cutover / rollback ------------------------------------------------------------------------
def ready_for_cutover(world: dict[str, Any], targets: list[str]) -> int:
    client = world["client"]
    sources = [s for s, t in EXPECTED_MAP.items() if t in targets]
    run_id = mapped_run(client, sources)
    for t in targets:
        shadow(client, run_id, t, mode="api")
        assert verify(client, run_id, t)["verdict"] == "go"
        resp = client.post(f"/api/v1/migration/runs/{run_id}/readiness",
                           json={"target_instance_id": t, "wait": True})
        assert resp.status_code == 200, resp.text
        r = resp.json()[t]
        assert r["status"] in ("succeeded", "warning"), r
    return run_id


def test_readiness_checks(world):
    client = world["client"]
    run_id = ready_for_cutover(world, ["sonarr-series"])
    rep = client.get(f"/api/v1/migration/runs/{run_id}").json()
    ready = next(r for r in rep["reports"] if r["kind"] == "readiness")
    checks = {c["check"]: c["status"] for c in ready["summary"]["checks"]}
    assert checks["verify"] == "ok" and checks["target_healthy"] == "ok"
    assert checks["webhook"] == "ok" and checks["future_episodes_monitored"] == "ok"
    assert checks["seerr_route"] == "ok" and checks["indexer_test"] == "ok"
    scan = client.post(f"/api/v1/migration/runs/{run_id}/scan",
                       json={"wait": True}).json()["sonarr-series"]
    assert scan["status"] in ("succeeded", "warning"), scan
    assert scan["result"]["expected_items"] == scan["result"]["found_items"] == 2
    assert scan["result"]["missing_items"] == []
    dry = client.post("/api/v1/migration/dry-run", json={"run_id": run_id, "wait": True}).json()
    assert dry["status"] in ("succeeded", "skipped"), dry
    prev = client.post("/api/v1/migration/import-preview", json={"run_id": run_id}).json()
    assert prev["targets"][0]["source_writes"] == "geen"
    assert_source_untouched(world["sources"])


def test_cutover_order_guard_and_seerr(world):
    client, src, t = world["client"], world["sources"]["old-sonarr"], \
        world["targets"]["sonarr-series"]
    run_id = ready_for_cutover(world, ["sonarr-series"])
    assert_source_untouched(world["sources"])
    body = {"run_id": run_id, "targets": ["sonarr-series"], "wait": True,
            "confirm_names": {"sonarr-series": "sonarr series"}}
    assert client.post("/api/v1/migration/cutover", json=body).status_code == 409
    body["confirm_names"] = {"sonarr-series": "sonarr-series"}
    r = client.post("/api/v1/migration/cutover", json=body)
    assert r.status_code == 200, r.text
    assert r.json()["cutovers"][0]["status"] == "completed"
    # oude instantie: alleen RSS/auto-zoeken uit, interactief ongewijzigd
    assert all(not i["enableRss"] and not i["enableAutomaticSearch"]
               for i in src.providers["indexer"].values())
    assert src.providers["indexer"][1]["enableInteractiveSearch"] is True
    assert {(w[0], w[1]) for w in src.writes()} <= {("PUT", "/api/v3/indexer/1"),
                                                    ("PUT", "/api/v3/indexer/2")}
    # doel krijgt de exacte oorspronkelijke bronstanden
    idx = {i["name"]: i for i in t.providers["indexer"].values()}
    assert (idx["NZBgeek"]["enableRss"], idx["NZBgeek"]["enableAutomaticSearch"]) == (True, True)
    assert (idx["Torznab"]["enableRss"], idx["Torznab"]["enableAutomaticSearch"]) == (False,
                                                                                      True)
    assert all(d["enable"] for d in t.providers["downloadclient"].values())
    assert t.configs["indexer"]["rssSyncInterval"] == 15
    assert instance_row(world["ctx"], "sonarr-series")["shadow_mode"] == 0
    # volgorde: laatste freeze-PUT op de oude instantie vóór de eerste activering op het doel
    last_freeze = max(c[-1] for c in src.writes())
    first_enable = min(c[-1] for c in t.calls if c[0] == "PUT" and
                       c[1].startswith("/api/v3/indexer/") and c[3].get("enableRss"))
    assert last_freeze < first_enable
    seerr = world["seerr"].servers["sonarr"][0]
    assert seerr["hostname"] == "mediaguard-sonarr-series" and seerr["apiKey"] == \
        TARGET_KEYS["sonarr-series"]
    assert world["seerr"].servers["sonarr"][1]["hostname"] == "old-anime"  # anime onaangeroerd
    # rollback: getypte bevestiging, exacte vorige staat, Seerr terug
    bad = client.post("/api/v1/migration/rollback", json={
        "run_id": run_id, "target_instance_id": "sonarr-series", "confirm_name": "x"})
    assert bad.status_code == 409
    r = client.post("/api/v1/migration/rollback", json={
        "run_id": run_id, "target_instance_id": "sonarr-series",
        "confirm_name": "sonarr-series", "wait": True})
    assert r.status_code == 200 and r.json()["status"] == "rolled_back", r.text
    assert (src.providers["indexer"][1]["enableRss"],
            src.providers["indexer"][1]["enableAutomaticSearch"]) == (True, True)
    assert (src.providers["indexer"][2]["enableRss"],
            src.providers["indexer"][2]["enableAutomaticSearch"]) == (False, True)
    assert all(not i["enableRss"] and not i["enableAutomaticSearch"]
               for i in t.providers["indexer"].values())
    assert instance_row(world["ctx"], "sonarr-series")["shadow_mode"] == 1
    seerr = world["seerr"].servers["sonarr"][0]
    assert seerr["hostname"] == "old-sonarr" and seerr["apiKey"] == OLD["old-sonarr"][2]
    audits = {r["action"] for r in world["ctx"].db.fetch_all(
        "SELECT action FROM audit_events WHERE instance_id='sonarr-series'")}
    assert {"migration.freeze_old", "migration.lift_shadow", "migration.cutover.completed",
            "migration.rollback", "seerr.cutover", "seerr.rollback"} <= audits


def test_double_grabber_guard_aborts_before_target_activation(world):
    client, src, t = world["client"], world["sources"]["old-sonarr"], \
        world["targets"]["sonarr-series"]
    run_id = ready_for_cutover(world, ["sonarr-series"])
    src.sticky_rss = {1}  # oude instantie 'negeert' de freeze
    r = client.post("/api/v1/migration/cutover", json={
        "run_id": run_id, "targets": ["sonarr-series"], "wait": True,
        "confirm_names": {"sonarr-series": "sonarr-series"}})
    assert r.status_code == 409 and "Dubbele-grabber" in r.text
    assert all(not i["enableRss"] and not i["enableAutomaticSearch"]
               for i in t.providers["indexer"].values())
    assert instance_row(world["ctx"], "sonarr-series")["shadow_mode"] == 1
    assert world["seerr"].servers["sonarr"][0]["hostname"] == "old-sonarr"


def test_power_loss_mid_cutover_resumes_with_original_state(world):
    client, src, t = world["client"], world["sources"]["old-sonarr"], \
        world["targets"]["sonarr-series"]
    run_id = ready_for_cutover(world, ["sonarr-series"])
    body = {"run_id": run_id, "targets": ["sonarr-series"], "wait": True,
            "confirm_names": {"sonarr-series": "sonarr-series"}}
    t.fail_paths = {"/api/v3/indexer/": 500}  # stroomstoring/fout tijdens activeren doel
    assert client.post("/api/v1/migration/cutover", json=body).status_code == 409
    co = world["ctx"].db.fetch_one("SELECT * FROM migration_cutovers")
    assert co["status"] == "failed" and co["phase"] == "delta_synced"
    assert not src.providers["indexer"][1]["enableRss"]  # oud bevroren, doel nog in schaduw
    t.fail_paths = {}
    r = client.post("/api/v1/migration/cutover", json=body)
    assert r.status_code == 200 and r.json()["cutovers"][0]["status"] == "completed"
    assert world["ctx"].db.fetch_one("SELECT COUNT(*) AS n FROM migration_cutovers")["n"] == 1
    state = json.loads(world["ctx"].db.fetch_one(
        "SELECT source_state_json FROM migration_cutovers")["source_state_json"])
    assert state["indexers"]["1"]["enableRss"] is True  # origineel, niet de bevroren staat
    client.post("/api/v1/migration/rollback", json={
        "run_id": run_id, "target_instance_id": "sonarr-series",
        "confirm_name": "sonarr-series", "wait": True})
    assert src.providers["indexer"][1]["enableRss"] is True


def test_cutover_via_job_and_post_audit(world):
    import asyncio

    client, ctx, src = world["client"], world["ctx"], world["sources"]["old-sonarr"]
    run_id = ready_for_cutover(world, ["sonarr-series"])
    r = client.post("/api/v1/migration/cutover", json={
        "run_id": run_id, "targets": ["sonarr-series"],
        "confirm_names": {"sonarr-series": "sonarr-series"}})
    job_id = r.json()["job_id"]
    assert ctx.db.fetch_one("SELECT job_type FROM jobs WHERE id=?",
                            (job_id,))["job_type"] == "migration.cutover"
    asyncio.run(ctx.services["jobs"].run_until_idle())
    assert ctx.db.fetch_one("SELECT status FROM jobs WHERE id=?", (job_id,))["status"] == \
        "succeeded"
    from test_arr_fakes import iso
    src.history_since_records = [{"id": 1, "date": iso(1), "eventType": "grabbed"}]
    rep = client.post(f"/api/v1/migration/runs/{run_id}/post-audit",
                      json={"target_instance_id": "sonarr-series", "hours": 24}).json()
    assert rep["verdict"] == "no_go" and rep["old"]["grabbed"] == 1


# -- upload-only bronnen + padherschrijving ------------------------------------------------------
def _anime_backup(root: str = "/anime") -> MigFakeArr:
    a = build_sources()["old-anime"]
    for s in a.series.values():
        s["path"] = s["path"].replace("/anime", root)
    a.root_folders[0]["path"] = root + "/"
    for e in a.episodes.values():
        if e["seasonNumber"] == 1 and e["episodeNumber"] == 1:
            e["hasFile"] = True
    return a


def test_upload_validation(world):
    client, ctx = world["client"], world["ctx"]

    def up(data: bytes, name: str = "x.zip"):  # type: ignore[no-untyped-def]
        return client.post("/api/v1/migration/sources/upload",
                           files={"file": (name, data, "application/zip")})

    assert up(b"geen zip").status_code == 422
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as z:
        z.writestr("readme.txt", "hallo")
    assert up(buf.getvalue()).status_code == 422
    slip = io.BytesIO()
    good = make_backup_zip(_anime_backup())
    with zipfile.ZipFile(io.BytesIO(good)) as src, zipfile.ZipFile(slip, "w") as z:
        for info in src.infolist():
            z.writestr(info.filename, src.read(info))
        z.writestr("../../etc/evil.sh", "rm -rf /")
    r = up(slip.getvalue())
    assert r.status_code == 422 and "Onveilige" in r.text
    fake_db = io.BytesIO()
    with zipfile.ZipFile(fake_db, "w") as z:
        z.writestr("sonarr.db", b"SQLite format 3\x00 maar kapot")
    assert up(fake_db.getvalue()).status_code == 422
    ctx.config.set("global", "", "migration.max_backup_mb", 1, "test")
    assert up(b"0" * (2 * 1024 * 1024)).status_code == 413
    assert ctx.db.fetch_one("SELECT COUNT(*) AS n FROM source_instances WHERE kind='backup'")[
        "n"] == 0
    assert not list((ctx.settings.backup_dir / "sources" / "_incoming").glob("*"))


def test_path_remap_unit(tmp_path):
    from mediaguard.migration.backupfile import build_shadow_zip, normalize_rules, remap
    from mediaguard.migration.snapshot import from_db

    rules = normalize_rules([{"from": "/anime", "to": "/media/anime"},
                             {"from": "/data/dl", "to": "/media/downloads"}])
    assert remap("/anime/Frieren", rules) == "/media/anime/Frieren"
    assert remap("/anime/", rules) == "/media/anime/"
    assert remap("/animefilms/X", rules) == "/animefilms/X"  # padgrens gerespecteerd
    src = tmp_path / "orig.zip"
    fake = _anime_backup()
    fake.rpm[1] = {"id": 1, "host": "qbit", "remotePath": "/dl/", "localPath": "/data/dl/"}
    src.write_bytes(make_backup_zip(fake))
    before = src.read_bytes()
    out = tmp_path / "shadow.zip"
    state = build_shadow_zip(src, out, rules=rules, target_api_key="K", target_url="http://t:1/x",
                             max_uncompressed=10**8)
    assert src.read_bytes() == before
    assert state["remapped"] == {"RootFolders.Path": 1, "Series.Path": 2,
                                 "ImportLists.RootFolderPath": 0,
                                 "RemotePathMappings.LocalPath": 1}
    with zipfile.ZipFile(out) as z:
        (tmp_path / "db").write_bytes(z.read("sonarr.db"))
        assert "<UrlBase>/x</UrlBase>" in z.read("config.xml").decode()
    snap = from_db(tmp_path / "db", "sonarr")
    assert snap["root_folders"] == ["/media/anime"]
    assert all(s["path"].startswith("/media/anime/") for s in snap["series"].values())
    assert not any(i["enableRss"] or i["enableAutomaticSearch"]
                   for i in snap["indexers"].values())
    assert all(not d["enable"] for d in snap["download_clients"].values())


def test_upload_only_flow_with_path_remap_and_activate_search(world, tmp_path):
    client, ctx, t = world["client"], world["ctx"], world["targets"]["sonarr-anime"]
    t.check_disk = True
    media = tmp_path / "media" / "anime"
    fake = _anime_backup("/anime")
    zip_bytes = make_backup_zip(fake)
    r = client.post("/api/v1/migration/sources/upload", data={"name": "Oude Sonarr Anime"},
                    files={"file": ("sonarr_backup_v4.0.14.2939_2026.10.01.zip", zip_bytes,
                                    "application/zip")})
    assert r.status_code == 200, r.text
    src = r.json()
    assert src["kind"] == "backup" and src["app"] == "sonarr" and src["version"] == "4.0.14.2939"
    assert src["suggested_target"] == "sonarr-anime"
    assert src["detected"]["anime"]["score"] >= 0.5
    run_id = new_run(client, [src["id"]])
    r = client.post("/api/v1/migration/instance-mapping/preview", json={"run_id": run_id}).json()
    assert r["mappings"][0]["target_instance_id"] == "sonarr-anime"
    client.put(f"/api/v1/migration/runs/{run_id}/mapping", json={"entries": [
        {"source_id": src["id"], "path_map": [{"from": "/anime", "to": str(media)}]}]})
    assert client.post(f"/api/v1/migration/runs/{run_id}/mapping/confirm").status_code == 200
    pre = client.post("/api/v1/migration/preflight", json={"run_id": run_id, "wait": True})
    checks = pre.json()["result"]["sources"][src["id"]]
    assert {c["check"]: c["status"] for c in checks}["live_checks"] == "skipped"
    (media / "Attack on Titan").mkdir(parents=True)
    pp = client.post(f"/api/v1/migration/runs/{run_id}/path-preview",
                     json={"source_id": src["id"]}).json()
    assert pp["blocking"] and pp["unresolved_count"] == 1  # Frieren-map ontbreekt
    assert pp["per_prefix"]["/anime"]["count"] >= 3
    out = shadow(client, run_id, "sonarr-anime")
    assert out["status"] == "blocked" and not t.restores
    (media / "Frieren").mkdir()
    pp = client.post(f"/api/v1/migration/runs/{run_id}/path-preview",
                     json={"source_id": src["id"]}).json()
    assert not pp["blocking"]
    out = shadow(client, run_id, "sonarr-anime")
    assert out["status"] == "succeeded", out
    assert out["result"]["link"] == {"expected_with_file": 2, "linked": 2, "missing": 0}
    assert all(s["path"].startswith(str(media)) for s in t.series.values())
    rep = verify(client, run_id, "sonarr-anime")
    assert rep["verdict"] == "go", rep["mismatches"]
    assert rep["summary"]["compared_against"] == "database in de back-up"
    r = client.post(f"/api/v1/migration/runs/{run_id}/readiness",
                    json={"target_instance_id": "sonarr-anime", "wait": True})
    assert r.json()["sonarr-anime"]["status"] in ("succeeded", "warning"), r.text
    body = {"run_id": run_id, "targets": ["sonarr-anime"], "wait": True, "accept_warnings": True,
            "confirm_names": {"sonarr-anime": "sonarr-anime"}}
    r = client.post(f"/api/v1/migration/runs/{run_id}/activate-search", json=body)
    assert r.status_code == 409 and "confirm_old_instance_offline" in r.text
    body["confirm_old_instance_offline"] = True
    r = client.post(f"/api/v1/migration/runs/{run_id}/activate-search", json=body)
    assert r.status_code == 200, r.text
    idx = next(iter(t.providers["indexer"].values()))
    assert idx["enableRss"] and idx["enableAutomaticSearch"]
    assert all(d["enable"] for d in t.providers["downloadclient"].values())
    assert instance_row(ctx, "sonarr-anime")["shadow_mode"] == 0


# -- herstart / jobs / RBAC / AV1 --------------------------------------------------------------
def test_status_survives_restart_and_jobs(world, settings):
    import asyncio

    from fastapi.testclient import TestClient

    from mediaguard.app import create_app

    client, ctx = world["client"], world["ctx"]
    run_id = new_run(client, ["old-radarr"])
    client.post("/api/v1/migration/instance-mapping/preview", json={"run_id": run_id})
    client.post(f"/api/v1/migration/runs/{run_id}/mapping/confirm")
    r = client.post("/api/v1/migration/preflight", json={"run_id": run_id})
    job_id = r.json()["job_id"]
    again = client.post("/api/v1/migration/preflight", json={"run_id": run_id}).json()
    assert again["job_id"] == job_id and again["already_running"]
    asyncio.run(ctx.services["jobs"].run_until_idle())
    assert ctx.db.fetch_one("SELECT job_type, status FROM jobs WHERE id=?",
                            (job_id,))["status"] == "succeeded"
    app2 = create_app(settings)
    with TestClient(app2) as c2:
        r = c2.post("/api/v1/auth/login", json={"username": "admin",
                                                "password": "geheim-wachtwoord"})
        c2.headers["X-CSRF-Token"] = r.json()["csrf_token"]
        status = c2.get(f"/api/v1/migration/runs/{run_id}").json()
    steps = {(s["step"], s["scope"]): s["status"] for s in status["steps"]}
    assert steps[("preflight", "")] in ("succeeded", "warning")
    assert steps[("mapping", "")] == "succeeded"
    assert status["mappings"][0]["target_instance_id"] == "radarr-films"


def test_rbac_operator_cannot_cutover_or_register(world, app):
    from fastapi.testclient import TestClient

    client = world["client"]
    client.post("/api/v1/auth/users", json={"username": "oper", "password": "operator-wachtw",
                                            "role": "operator"})
    with TestClient(app) as op:
        r = op.post("/api/v1/auth/login", json={"username": "oper",
                                                "password": "operator-wachtw"})
        op.headers["X-CSRF-Token"] = r.json()["csrf_token"]
        run_id = new_run(op, ["old-radarr"])
        assert op.post("/api/v1/migration/instance-mapping/preview",
                       json={"run_id": run_id}).status_code == 200
        assert op.post("/api/v1/migration/cutover", json={
            "run_id": run_id, "targets": ["radarr-films"],
            "confirm_names": {"radarr-films": "radarr-films"}}).status_code == 403
        assert op.post("/api/v1/migration/rollback", json={
            "run_id": run_id, "target_instance_id": "radarr-films",
            "confirm_name": "radarr-films"}).status_code == 403
        assert op.post("/api/v1/migration/sources", json={
            "name": "x", "url": "http://x:1", "api_key": "12345678"}).status_code == 403
        assert op.post(f"/api/v1/migration/runs/{run_id}/backup", json={
            "source_id": "old-radarr"}).status_code == 403


def test_av1_import_read_only_and_idempotent(world, tmp_path):
    import sqlite3

    client, ctx = world["client"], world["ctx"]
    db = tmp_path / "av1.db"
    conn = sqlite3.connect(db)
    conn.executescript("""
        CREATE TABLE library (id INTEGER PRIMARY KEY, naam TEXT, pad TEXT, profiel_id INTEGER);
        CREATE TABLE bestand (id INTEGER PRIMARY KEY, library_id INTEGER, pad TEXT, grootte INT,
            mtime REAL, nlink INT, categorie TEXT, probe TEXT, gescand_op TEXT);
        CREATE TABLE job (id INTEGER PRIMARY KEY, besluit_id INT, bestand_id INT, baan TEXT,
            status TEXT, bron_grootte INT, bron_mtime REAL, bron_device INT, bron_inode INT,
            resultaat_grootte INT, fout TEXT, klaar_op TEXT, mg_media_ref TEXT);
        CREATE TABLE vervang_transactie (id INTEGER PRIMARY KEY, job_id INT, status TEXT,
            resultaat_sha256 TEXT, toelichting TEXT);
        CREATE TABLE verificatie (job_id INTEGER PRIMARY KEY, geslaagd INT, toelichting TEXT);
        CREATE TABLE besparing (id INTEGER PRIMARY KEY, job_id INT, bytes_voor INT, bytes_na INT);
        INSERT INTO library VALUES (1, 'Films', '/movies', 1);
        INSERT INTO bestand VALUES (1, 1, '/movies/Wadi (2021)/Wadi.mkv', 100, 1.0, 1, 'film',
            '{"streams":[{"codec_type":"video","codec_name":"hevc"}]}', 'x');
        INSERT INTO bestand VALUES (2, 1, '/movies/Other/Other.mkv', 50, 1.0, 1, 'film',
            '{"streams":[{"codec_type":"video","codec_name":"av1"}]}', 'x');
        INSERT INTO job VALUES (7, 1, 1, 'gpu', 'klaar', 100, 1.0, 64768, 1234, 40, NULL,
            '2025-01-01', NULL);
        INSERT INTO vervang_transactie VALUES (1, 7, 'voltooid', 'abc', NULL);
        INSERT INTO verificatie VALUES (7, 1, 'ok');
        INSERT INTO besparing VALUES (1, 7, 100, 40);
    """)
    conn.commit()
    conn.close()
    before = hashlib.sha256(db.read_bytes()).hexdigest()
    ctx.db.execute("INSERT INTO lib_files(instance_id,kind,provider_id,owner_provider_id,path,"
                   "synced_at) VALUES ('radarr-films','movie',10,1,"
                   "'/media/movies/Wadi (2021)/Wadi.mkv','x')")
    rules = json.dumps([{"from": "/movies", "to": "/media/movies"}])
    for _ in range(2):
        r = client.post("/api/v1/migration/av1/import", data={"path": str(db), "path_map": rules,
                                                               "source_instance": "ubuntu-av1"})
        assert r.status_code == 200, r.text
    assert r.json()["counts"] == {"jobs": 1, "files": 1, "matched": 1, "unmatched": 1,
                                  "av1_unknown_source": 1}
    rows = client.get("/api/v1/migration/av1/provenance").json()
    assert len(rows) == 2
    job = next(x for x in rows if x["source_kind"] == "job")
    assert job["mediaguard_ref"] == "radarr-films:movie:10" and job["status"] == "converted"
    assert job["inode"] == 1234 and job["data"]["saved_bytes"] == 60
    assert next(x for x in rows if x["source_kind"] == "bestand")["status"] == \
        "av1_unknown_source"
    assert hashlib.sha256(db.read_bytes()).hexdigest() == before
    up = client.post("/api/v1/migration/av1/import", files={"file": ("x.db", b"nope")})
    assert up.status_code == 422


def test_readiness_after_native_restore_in_shadow_mode(world):
    """Bugs gevonden met echte Sonarr 4.0.20-containers (tests/integration/stack):
    1. na een native restore is de MediaGuard-webhook uit de doel-DB verdwenen, maar de
       gereedheid testte het oude webhook_notification_id → 404 → NO-GO;
    2. in schaduwmodus meldt Sonarr zelf 'No indexers available with RSS sync enabled'
       (IndexerRssCheck) — verwacht, mag de gereedheid niet blokkeren."""
    client, ctx, t = world["client"], world["ctx"], world["targets"]["sonarr-series"]
    ctx.db.execute("UPDATE instances SET webhook_notification_id=999 WHERE id='sonarr-series'")
    run_id = _native_ready(world)
    assert shadow(client, run_id, "sonarr-series")["status"] == "succeeded"
    assert verify(client, run_id, "sonarr-series")["verdict"] == "go"
    t.health = [{"source": "IndexerRssCheck", "type": "error",
                 "message": "No indexers available with RSS sync enabled"}]
    r = client.post(f"/api/v1/migration/runs/{run_id}/readiness",
                    json={"target_instance_id": "sonarr-series", "wait": True}).json()
    checks = {c["check"]: c for c in r["sonarr-series"]["result"]["checks"]}
    assert checks["target_healthy"]["status"] == "ok", checks["target_healthy"]
    assert checks["webhook"]["status"] == "ok", checks["webhook"]
    # een ONverwachte health-fout blijft wél blokkeren
    t.health = [{"source": "RootFolderCheck", "type": "error", "message": "Missing root folder"}]
    r = client.post(f"/api/v1/migration/runs/{run_id}/readiness",
                    json={"target_instance_id": "sonarr-series", "wait": True}).json()
    checks = {c["check"]: c for c in r["sonarr-series"]["result"]["checks"]}
    assert checks["target_healthy"]["status"] == "fail"


def test_native_restore_drops_item_scoped_settings_of_target(world):
    """Bug (stacktest): na een native restore bleef een MediaGuard-override op
    'series:sonarr-series:1' staan; serie-ID 1 is na de restore een andere/nieuwe titel uit
    de bron, die zo ongemerkt 'Nederlandse audio verplicht' kreeg (alle grabs geweigerd)."""
    client, ctx = world["client"], world["ctx"]
    ctx.config.set("series", "sonarr-series:1", "policy.profile_id", "kids-nl-audio", "test")
    ctx.config.set("series", "sonarr-anime:1", "policy.profile_id", "kids-nl-audio", "test")
    ctx.config.set("instance", "sonarr-series", "hooks.pregrab_enabled", True, "test")
    run_id = _native_ready(world)
    out = shadow(client, run_id, "sonarr-series")
    assert out["status"] == "succeeded", out
    left = ctx.db.fetch_dicts("SELECT scope_type, scope_id FROM settings_values WHERE "
                              "key IN ('policy.profile_id','hooks.pregrab_enabled')")
    assert {(r["scope_type"], r["scope_id"]) for r in left} == {
        ("series", "sonarr-anime:1"), ("instance", "sonarr-series")}
    assert ctx.db.fetch_one("SELECT 1 FROM audit_events WHERE "
                            "action='migration.item_settings_dropped'")


def test_readiness_broken_indexers_and_library_notices_are_warnings(world):
    """Praktijk (TrueNAS): een handvol Prowlarr-indexers gaf 404 of 'no results in the
    configured categories', en Radarr meldde films die van TMDb verwijderd zijn. Dat mag
    de overstap niet blokkeren; alleen 'geen enkele werkende indexer' blokkeert."""
    from mediaguard.migration.wizard import explain_provider_error

    client, t = world["client"], world["targets"]["sonarr-series"]
    run_id = _native_ready(world)
    assert shadow(client, run_id, "sonarr-series")["status"] == "succeeded"
    assert verify(client, run_id, "sonarr-series")["verdict"] == "go"
    t.health = [{"source": "RemovedSeriesCheck", "type": "error",
                 "message": "Series Foo (tvdbid 1) was removed from TheTVDB"}]
    r = client.post(f"/api/v1/migration/runs/{run_id}/readiness",
                    json={"target_instance_id": "sonarr-series", "wait": True}).json()
    checks = r["sonarr-series"]["result"]["checks"]
    by = {c["check"]: c for c in checks}
    assert by["target_healthy"]["status"] == "ok"
    assert by["target_notice"]["status"] == "warn"
    assert r["sonarr-series"]["result"]["verdict"] in ("go", "warn")
    # alle indexertests mislukken: losse tests zijn waarschuwingen, het totaal blokkeert
    t.fail_paths["/api/v3/indexer/test"] = 400
    r = client.post(f"/api/v1/migration/runs/{run_id}/readiness",
                    json={"target_instance_id": "sonarr-series", "wait": True}).json()
    checks = r["sonarr-series"]["result"]["checks"]
    assert all(c["status"] == "warn" for c in checks if c["check"] == "indexer_test")
    assert {c["check"]: c for c in checks}["indexer_working"]["status"] == "fail"
    assert r["sonarr-series"]["result"]["verdict"] == "no_go"
    t.fail_paths.clear()
    assert "Prowlarr kent deze indexer niet" in explain_provider_error(
        "HTTP request failed: [404:NotFound] [GET] at [http://x:30050/15/api?t=caps")
    assert "geen resultaten" in explain_provider_error(
        "Query successful, but no results in the configured categories were returned")


# -- automatische doorloop (minder klikken) ---------------------------------------------------
async def test_autopilot_runs_steps_until_a_decision_and_never_cuts_over(world):
    from mediaguard.migration import autopilot

    client, ctx = world["client"], world["ctx"]
    world["sources"]["old-sonarr"].add_backup("scheduled")
    run_id = new_run(client, ["old-sonarr"])
    assert client.post("/api/v1/migration/instance-mapping/preview",
                       json={"run_id": run_id}).status_code == 200
    logs: list[str] = []
    # vóór de bevestigde koppeling: controle vooraf + back-up gaan vanzelf, dan stoppen
    st = await autopilot.advance(ctx, run_id, "test", logs.append)
    assert st["state"] == "needs_input" and st["needs"][0]["kind"] == "mapping"
    steps = {(s["step"], s["scope"]): s["status"] for s in autopilot.plan(ctx, run_id)}
    assert steps[("preflight", "")] in ("succeeded", "warning")
    assert steps[("backup", "source:old-sonarr")] == "succeeded"
    # bevestigen zet de doorloop zelf in de wachtrij
    r = client.post(f"/api/v1/migration/runs/{run_id}/mapping/confirm")
    assert r.status_code == 200 and r.json()["autopilot_job_id"]
    st = await autopilot.advance(ctx, run_id, "test", logs.append)
    view = client.get(f"/api/v1/migration/runs/{run_id}").json()["autopilot"]
    assert view["done"] >= 5, view
    assert st["state"] == "ready_for_cutover", st
    assert st["needs"][0]["action"] == "open_cutover"
    assert st["needs"][0]["targets"] == ["sonarr-series"]
    # alles klaar behalve de overstap zelf: 'Nog 1 stap: overschakelen' (rapport H13)
    assert view["done"] == view["total"] - 1, view
    assert view["cutover_pending"] == ["sonarr-series"]
    assert view["headline_nl"].startswith("Nog 1 stap: overschakelen")
    # overschakelen gebeurt nooit automatisch; oude installatie onaangeroerd
    assert ctx.db.fetch_one("SELECT COUNT(*) AS n FROM migration_cutovers")["n"] == 0
    assert_source_untouched(world["sources"])
    # tweede keer: niets opnieuw (idempotent), zelfde eindtoestand
    before = ctx.db.fetch_one("SELECT COUNT(*) AS n FROM migration_reports")["n"]
    again = await autopilot.advance(ctx, run_id, "test", logs.append)
    assert again["state"] == st["state"]
    assert ctx.db.fetch_one("SELECT COUNT(*) AS n FROM migration_reports")["n"] == before


async def test_autopilot_asks_before_creating_backup_on_old_install(world):
    from mediaguard.migration import autopilot

    client, ctx = world["client"], world["ctx"]
    run_id = new_run(client, ["old-radarr"])
    client.post("/api/v1/migration/instance-mapping/preview", json={"run_id": run_id})
    st = await autopilot.advance(ctx, run_id, "test", lambda _m: None)
    assert st["needs"][0]["kind"] == "backup_create"
    assert st["needs"][0]["action"] == "allow_backup_create"
    r = client.post(f"/api/v1/migration/runs/{run_id}/autopilot",
                    json={"allow_backup_create": True})
    assert r.status_code == 200 and autopilot.state(ctx, run_id)["allow_backup_create"]


def test_newer_source_version_is_harmless(world):
    """Oude installatie Sonarr v5/Radarr v7 (develop), doel fork v4/v6: wat het doel niet
    kent wordt weggelaten of automatisch geaccepteerd; de overstap blijft GO."""
    client, targets, sources = world["client"], world["targets"], world["sources"]
    src, tgt = sources["old-sonarr"], targets["sonarr-series"]
    src.version, tgt.version = "5.0.0.3100", "4.0.20.3014"
    # nieuwere downloadclient-implementatie die v4 niet kent
    newer = {**src.providers["downloadclient"][1], "id": 7, "name": "NieuweClient",
             "implementation": "FutureClient", "configContract": "FutureSettings"}
    src.providers["downloadclient"][7] = newer
    tgt.intercept[("GET", "/api/v3/downloadclient/schema")] = lambda _b: (
        200, [{**src.providers["downloadclient"][1], "id": 0}])
    # nieuw veld in de naamgeving van v5 en een v5-veld op kwaliteitsprofielen
    src.configs["naming"]["v5OnlyToken"] = "{Series TitleYear}"
    src.quality_profiles.append({"id": 9, "name": "V5 profiel", "cutoff": 99, "upgradeAllowed":
                                 True, "minUpgradeFormatScore": 5, "formatItems": [],
                                 "items": [{"quality": {"id": 3, "name": "WEBDL-1080p"},
                                            "allowed": True},
                                           {"quality": {"id": 99, "name": "NieuwFormaat"},
                                            "allowed": True}]})

    def reject_v5_field(body):
        if isinstance(body, dict) and "minUpgradeFormatScore" in body:
            return 400, [{"propertyName": "MinUpgradeFormatScore",
                          "errorMessage": "Unknown property", "severity": "error"}]
        return None
    tgt.intercept[("POST", "/api/v3/qualityprofile")] = reject_v5_field
    # custom format met een v5-voorwaarde
    src.custom_formats.append({"id": 5, "name": "Alleen v5", "specifications": [
        {"name": "Nieuw", "implementation": "FutureSpecification", "fields": []}]})
    tgt.intercept[("GET", "/api/v3/customformat/schema")] = lambda _b: (
        200, [{"implementation": "ReleaseTitleSpecification", "fields": []}])

    run_id = mapped_run(client, ["old-sonarr"])
    out = shadow(client, run_id, "sonarr-series", mode="api")
    assert out["status"] in ("succeeded", "warning"), out
    assert "v5OnlyToken" not in tgt.configs["naming"]
    assert "NieuweClient" not in {d["name"] for d in tgt.providers["downloadclient"].values()}
    v5 = next(q for q in tgt.quality_profiles if q["name"] == "V5 profiel")
    assert "minUpgradeFormatScore" not in v5 and v5["cutoff"] == 3
    assert [i["quality"]["id"] for i in v5["items"]] == [3]
    assert "Alleen v5" not in {c["name"] for c in tgt.custom_formats}
    rep = verify(client, run_id, "sonarr-series")
    assert rep["verdict"] == "go", rep["mismatches"]
    auto = [m for m in rep["mismatches"] if m.get("auto_accepted")]
    texts = " ".join(m["message_nl"] for m in auto)
    assert "NieuweClient" in texts and "Alleen v5" in texts and "V5 profiel" in texts
    assert "bestaat niet in Sonarr 4.0.20.3014" in texts
    from mediaguard.migration import store

    latest = store.latest_report(world["ctx"], run_id, "verify", "sonarr-series")
    assert latest["open_mismatches"] == [] and len(latest["accepted_mismatches"]) >= 3
    assert store.accepted_ids(world["ctx"], run_id, "sonarr-series") >= {m["id"] for m in auto}
    assert_source_untouched(sources)


def test_version_check_is_info_not_warning(world):
    """Een nieuwere oude installatie is geen waarschuwing meer (rapport 4 nov, H13)."""
    import asyncio

    from mediaguard.instances.registry import arr_client, instance_row
    from mediaguard.migration.sources import source_row
    from mediaguard.migration.wizard import _version_and_rss

    ctx = world["ctx"]
    world["sources"]["old-radarr"].version = "7.0.1.10859"
    world["targets"]["radarr-films"].version = "6.4.4.10685"
    row = dict(source_row(ctx, "old-radarr"))
    row["version"] = "7.0.1.10859"
    checks = asyncio.run(_version_and_rss(ctx, row, dict(instance_row(ctx, "radarr-films")),
                                          arr_client(ctx, "radarr-films")))
    v = next(c for c in checks if c["check"] == "version")
    assert v["status"] == "ok" and v["newer_source"] is True
    assert v["message_nl"].startswith("Oud 7.0.1.10859 → nieuw 6.4.4.10685.")


def test_compat_helpers():
    from mediaguard.migration import compat

    assert compat.failing_properties([{"property": "Fields[3].Value"},
                                      {"property": "$.monitorNewItems"}]) == ["Fields",
                                                                              "monitorNewItems"]
    assert compat.failing_properties({"message": "could not convert $.foo to X"}) == ["foo"]
    body = {"name": "x", "fields": [{"name": "a"}, {"name": "b"}], "newThing": 1}
    assert compat.strip_properties(body, ["newThing", "b"]) == {"name": "x",
                                                                "fields": [{"name": "a"}]}
    items = [{"quality": {"id": 1}, "allowed": True},
             {"id": 1000, "name": "grp", "allowed": True,
              "items": [{"quality": {"id": 2}}, {"quality": {"id": 77}}]}]
    fitted, dropped = compat.fit_quality_items(items, {1, 2})
    assert dropped == [77] and compat.allowed_ids(fitted) == [1, 2]
    cf, d = compat.fit_custom_format(
        {"name": "Taal", "specifications": [{"name": "Klingon", "implementation":
                                              "LanguageSpecification",
                                              "fields": [{"name": "value", "value": 999}]}]},
        None, {1, 2})
    assert cf is None and d == ["Klingon"]
