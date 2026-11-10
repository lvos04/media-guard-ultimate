"""Regels die overal gelden.

* Item 15: de noodstop geldt voor alle automatische acties (downloadbewaking, vastgelopen
  downloads, auto-import, herstel, opruimen, routering, releaseprofiel, verwijderen).
* Item 27: zoekpauzes per item gelden ook voor grabs die Sonarr/Radarr zelf doen (RSS).
* Item 35: Seerr-herstel schrijft pas na Seerr's eigen verbindingstest met de nieuwe waarden.
"""

from __future__ import annotations

import asyncio
from datetime import UTC, datetime, timedelta

import pytest
from fastapi import HTTPException
from test_arr_fakes import FakeArr, b1ctx, make_movie, make_series, wire  # noqa: F401
from test_hooks import SERIES_KEY, grab, hooks, post  # noqa: F401 - hooks is een fixture
from test_provisioning import _seerr_setup, env  # noqa: F401 - env is een fixture
from test_r3_downloads import FakeArr as GuardArr
from test_r3_downloads import _grab, _patch, _sab, _wanted

from mediaguard import attention, emergency, jobs
from mediaguard.downloads import failures, holds


def _stop(ctx, active: bool = True) -> None:  # type: ignore[no-untyped-def]
    ctx.state_set("emergency_stop", {"active": active, "reason": "test"})


# -- item 15: één regel voor de noodstop -------------------------------------------------------
def test_noodstop_helper(ctx):  # type: ignore[no-untyped-def]
    assert emergency.skip(ctx, "x") is False
    emergency.require_clear(ctx, "x")
    emergency.wait_if_stopped(ctx, "x")
    _stop(ctx)
    assert emergency.skip(ctx, "x") is True
    with pytest.raises(HTTPException) as exc:
        emergency.require_clear(ctx, "importeren")
    assert exc.value.status_code == 409 and exc.value.detail == \
        "Noodstop actief: importeren gepauzeerd"
    with pytest.raises(jobs.Waiting):
        emergency.wait_if_stopped(ctx, "anime-routering")
    assert emergency.skipped("x")["skipped"] == "noodstop"


def test_noodstop_downloadbewaking_haalt_niets_weg(ctx, monkeypatch):  # type: ignore[no-untyped-def]
    fake = GuardArr()
    _patch(monkeypatch, fake)
    rows = [{"id": 7, "instance_id": "radarr-films", "awaiting_import": True,
             "title": "Jaar in Delft (2025)", "messages": ["Not a Custom Format upgrade"],
             "download_id": "SABnzbd_nzo_1", "movie_id": 3, "state": "importblocked"}]
    _stop(ctx)
    assert asyncio.run(failures.handle_queue(ctx, rows)) == []
    assert fake.removed == []

    from mediaguard.activity import api as activity_api

    async def no_activity(_ctx):  # type: ignore[no-untyped-def]
        raise AssertionError("tijdens de noodstop niets ophalen om op te ruimen")

    async def outcomes(_ctx):  # type: ignore[no-untyped-def]
        return 0

    monkeypatch.setattr(activity_api, "activity_cached", no_activity)
    monkeypatch.setattr(failures, "collect_outcomes", outcomes)
    res = asyncio.run(failures.run_once(ctx))
    assert res["skipped"] == "noodstop" and res["removed"] == [] and \
        res["marked_failed"] == [] and "Noodstop actief" in res["reason_nl"]
    # na de noodstop gewoon weer
    _stop(ctx, False)
    assert asyncio.run(failures.handle_queue(ctx, rows))
    assert fake.removed == [(7, True, True)]


def test_noodstop_markeert_geen_verdwenen_grab(ctx, monkeypatch):  # type: ignore[no-untyped-def]
    import httpx

    def handler(request):  # type: ignore[no-untyped-def]
        return httpx.Response(200, json={"queue": {"slots": []}, "history": {"slots": []}})

    _sab(ctx, handler)
    fake = _grab()
    _patch(monkeypatch, fake)
    _wanted(ctx)
    _stop(ctx)
    assert asyncio.run(failures.handle_grabbed_without_import(ctx)) == []
    assert fake.posts == []  # geen history/failed = geen blocklist
    _stop(ctx, False)
    asyncio.run(failures.handle_grabbed_without_import(ctx))
    assert fake.posts == ["history/failed/55"]


def test_noodstop_laat_releaseprofiel_staan(ctx, monkeypatch):  # type: ignore[no-untyped-def]
    from mediaguard.instances import registry

    def boom(*_a, **_kw):  # type: ignore[no-untyped-def]
        raise AssertionError("geen Sonarr/Radarr-aanroep tijdens de noodstop")

    monkeypatch.setattr(registry, "arr_client", boom)
    _stop(ctx)
    asyncio.run(failures.sync_refused_profile(ctx, "radarr-films"))


def _queue_item() -> dict:
    return {"id": 1, "downloadId": "D1", "title": "Show.S01E01", "status": "completed",
            "trackedDownloadState": "importPending", "timeleft": "00:00:00",
            "statusMessages": [{"title": "x", "messages": ["No files found are eligible"]}]}


async def test_noodstop_vastgelopen_download_blijft_staan(b1ctx):  # noqa: F811  # type: ignore[no-untyped-def]
    from mediaguard.wanted import stuck

    ctx = b1ctx
    sonarr = wire(ctx, "sonarr-series", FakeArr("sonarr"))
    sonarr.queue = [_queue_item()]
    assert (await stuck.check_instance(ctx, "sonarr-series"))["checked"] == 1
    ctx.db.execute("UPDATE stuck_import_actions SET first_seen_at='2020-01-01T00:00:00+00:00'")
    _stop(ctx)
    res = await stuck.check_instance(ctx, "sonarr-series")
    assert res["skipped"] == "noodstop" and res["fixed"] == 0
    assert len(sonarr.queue) == 1 and not sonarr.calls_to("DELETE", "/api/v3/queue")
    _stop(ctx, False)
    assert (await stuck.check_instance(ctx, "sonarr-series"))["fixed"] == 1
    assert sonarr.queue == []


def test_noodstop_uitgestelde_import_telt_niet_als_vastgelopen():
    from mediaguard.wanted import stuck

    q = {**_queue_item(), "trackedDownloadState": "importBlocked", "statusMessages": [
        {"messages": ["MediaGuard: Noodstop actief: upgrade wacht, het huidige bestand "
                      "blijft staan [EMERGENCY_STOP]"]}]}
    assert stuck._stuck(q)[0] is False
    assert failures.refusal_reason(q["statusMessages"][0]["messages"]) is None


async def test_noodstop_geen_automatische_import(ctx, monkeypatch):  # type: ignore[no-untyped-def]
    from mediaguard.activity import api as activity_api
    from mediaguard.activity import manual_import

    async def no_activity(_ctx):  # type: ignore[no-untyped-def]
        raise AssertionError("tijdens de noodstop niet importeren")

    monkeypatch.setattr(activity_api, "activity_cached", no_activity)
    _stop(ctx)
    assert await manual_import.auto_import(ctx) == 0
    res = await manual_import.try_import(ctx, "radarr-films", "D1", movie_id=3, actor="t")
    assert res["imported"] is False and res["reason_nl"] == \
        "Noodstop actief: importeren gepauzeerd"


def test_noodstop_api_acties_geven_409(client, ctx):  # type: ignore[no-untyped-def]
    _stop(ctx)
    r = client.post("/api/v1/activity/downloads/radarr-films/import",
                    json={"download_id": "D1", "confirm": True})
    assert r.status_code == 409 and "Noodstop actief" in r.json()["detail"]
    r = client.post("/api/v1/downloads/recovery/run")
    assert r.status_code == 409


async def test_noodstop_herstel_en_opruimen_slaan_over(ctx, monkeypatch):  # type: ignore[no-untyped-def]
    from mediaguard.downloads import janitor, recovery

    async def no_gather(*_a, **_kw):  # type: ignore[no-untyped-def]
        raise AssertionError("tijdens de noodstop niets verzamelen om te verplaatsen")

    monkeypatch.setattr(janitor, "gather", no_gather)
    _stop(ctx)
    assert (await recovery.run_once(ctx))["skipped"] == "noodstop"
    res = await janitor.run_once(ctx, gatherer=no_gather)
    assert res["status"] == "skipped" and res["trashed"] == res["purged"] == 0
    assert "Noodstop actief" in res["summary_nl"]
    assert ctx.db.fetch_one("SELECT COUNT(*) AS n FROM download_janitor_runs")["n"] == 0


async def test_noodstop_verwijdert_geen_titels(b1ctx):  # noqa: F811  # type: ignore[no-untyped-def]
    from mediaguard import automation

    ctx = b1ctx
    radarr = wire(ctx, "radarr-films", FakeArr("radarr"))
    gone = make_movie(1, "Weg", 11)
    gone["status"] = "deleted"
    radarr.movies = {1: gone}
    _stop(ctx)
    assert await automation.cleanup_removed(ctx) == 0
    assert await automation.replace_mismatches(ctx) == 0
    assert set(radarr.movies) == {1} and not radarr.calls_to("DELETE", "/api/v3")
    _stop(ctx, False)
    assert await automation.cleanup_removed(ctx) == 1 and radarr.movies == {}


async def test_noodstop_verplaatst_geen_anime(b1ctx, client):  # noqa: F811  # type: ignore[no-untyped-def]
    from mediaguard.routing import api as routing_api
    from mediaguard.routing import service

    ctx = b1ctx
    series = wire(ctx, "sonarr-series", FakeArr("sonarr"))
    wire(ctx, "sonarr-anime", FakeArr("sonarr"))
    s = make_series(16, "Golfbreker", 900817, series_type="anime")
    s["added"] = datetime.now(UTC).isoformat().replace("+00:00", "Z")
    series.series[16] = s
    _stop(ctx)
    res = await service.evaluate(ctx, "sonarr-series", "series", 16)
    assert res["status"] == "paused" and 16 in series.series
    assert ctx.db.fetch_one("SELECT COUNT(*) AS n FROM anime_routing_actions")["n"] == 0
    assert await service.sweep(ctx) == [] and await service.backlog_sweep(ctx) == []
    jc = jobs.JobContext(job_id=1, job_type="routing.anime_check", payload={
        "source": "sonarr-series", "kind": "series", "provider_id": 16}, instance_id=None,
        correlation_id=None, app=ctx)
    with pytest.raises(jobs.Waiting):
        await routing_api._job_check(jc)
    aid = ctx.db.execute_insert(
        "INSERT INTO anime_routing_actions(source_instance,target_instance,kind,provider_id,"
        "external_id,title,status,evidence_json,detail,target_provider_id,created_at,"
        "updated_at) VALUES ('sonarr-series','sonarr-anime','series',16,900817,'Golfbreker',"
        "'moved','[]','',3,'x','x')")
    with pytest.raises(ValueError, match="Noodstop"):
        await service.undo(ctx, aid, "t")
    assert client.post(f"/api/v1/routing/anime/actions/{aid}/undo").status_code == 409
    assert client.post(f"/api/v1/routing/anime/actions/{aid}/approve").status_code == 409
    _stop(ctx, False)
    assert (await service.evaluate(ctx, "sonarr-series", "series", 16))["status"] == "moved"


def _import_body(**extra):  # type: ignore[no-untyped-def]
    return {"instance_id": "sonarr-series", "app": "sonarr",
            "path": "/downloads/Show.S01E03.mkv",
            "release_title": "Show.S01E03.1080p.WEB-DL-GRP",
            "series": {"id": 12, "tvdb_id": 81189},
            "episodes": [{"id": 23, "season_number": 1, "episode_number": 3}], **extra}


def test_noodstop_stelt_upgrades_uit(client, ctx, hooks):  # noqa: F811  # type: ignore[no-untyped-def]
    """Een upgrade vervangt het huidige bestand: tijdens de noodstop blijft dat staan. Een
    import zonder bestaand bestand verwijdert niets en gaat gewoon door."""
    _stop(ctx)
    up = post(client, "/api/v1/hooks/pre-import", _import_body(is_upgrade=True),
              SERIES_KEY).json()
    assert up["decision"] == "defer" and up["reason_code"] == "EMERGENCY_STOP"
    again = post(client, "/api/v1/hooks/pre-import", _import_body(is_upgrade=True),
                 SERIES_KEY).json()
    assert again["decision"] == "defer"
    new = post(client, "/api/v1/hooks/pre-import", _import_body(is_upgrade=False),
               SERIES_KEY).json()
    assert new["decision"] == "accept"
    _stop(ctx, False)  # opgeheven: niet de onthouden 'uitstellen' teruggeven
    after = post(client, "/api/v1/hooks/pre-import", _import_body(is_upgrade=True),
                 SERIES_KEY).json()
    assert after["decision"] == "accept" and "cached" not in after


# -- item 27: zoekpauzes ook voor RSS ----------------------------------------------------------
def _hold(ctx, kind: str, *, episode: int = 345, hours: float = 24,  # type: ignore[no-untyped-def]
          instance: str = "sonarr-anime") -> None:
    holds.set_hold(ctx, holds.item_key(instance, "episode", episode), hours=hours,
                   reason_nl=f"test {kind}", kind=kind)


def test_rss_grab_respecteert_pauze_na_verdwenen_downloads(client, ctx, hooks):  # noqa: F811  # type: ignore[no-untyped-def]
    _hold(ctx, "vanished")
    r = post(client, "/api/v1/hooks/pre-grab", grab("[G] Show - 05 (1080p)")).json()
    assert r["decision"] == "reject" and r["reason_code"] == "ITEM_PAUSED"
    assert r["message"].startswith("Zoekpauze voor dit item tot ") and "test vanished" in \
        r["message"]
    # een andere aflevering heeft geen pauze
    other = grab("[G] Show - 06 (1080p)", episodes=[
        {"id": 346, "season_number": 1, "episode_number": 6, "absolute_episode_number": 6}])
    assert post(client, "/api/v1/hooks/pre-grab", other).json()["decision"] == "accept"


def test_seizoenspakket_alleen_geweigerd_als_alles_pauzeert(client, ctx, hooks):  # noqa: F811  # type: ignore[no-untyped-def]
    _hold(ctx, "vanished")
    pack = grab("[G] Show S01 (1080p)", episodes=[
        {"id": 345, "season_number": 1, "episode_number": 5, "absolute_episode_number": 5},
        {"id": 346, "season_number": 1, "episode_number": 6, "absolute_episode_number": 6}])
    assert post(client, "/api/v1/hooks/pre-grab", pack).json()["decision"] == "accept"
    _hold(ctx, "vanished", episode=346)
    r = post(client, "/api/v1/hooks/pre-grab", pack).json()
    assert r["decision"] == "reject" and r["reason_code"] == "ITEM_PAUSED"


def test_geweigerde_upgrade_pauzeert_alleen_met_bestand(client, ctx, hooks):  # noqa: F811  # type: ignore[no-untyped-def]
    _hold(ctx, "refused")
    body = grab("[G] Show - 05 (1080p)")
    # ontbrekend item: nooit laten wachten
    assert post(client, "/api/v1/hooks/pre-grab", body).json()["decision"] == "accept"
    ctx.db.execute(
        "INSERT INTO lib_episodes(instance_id,provider_id,series_provider_id,season_number,"
        "episode_number,has_file,synced_at) VALUES ('sonarr-anime',345,12,1,5,1,'x')")
    r = post(client, "/api/v1/hooks/pre-grab", body).json()
    assert r["decision"] == "reject" and r["reason_code"] == "ITEM_PAUSED"


def test_verlopen_en_churn_pauze(ctx):  # type: ignore[no-untyped-def]
    body = {"episodes": [{"id": 345}]}
    holds.set_hold(ctx, holds.item_key("sonarr-anime", "episode", 345), hours=1,
                   reason_nl="x", kind="vanished")
    data = ctx.state_get(holds.STATE_KEY)
    key = holds.item_key("sonarr-anime", "episode", 345)
    data[key]["until"] = (datetime.now(UTC) - timedelta(minutes=1)).isoformat()
    ctx.state_set(holds.STATE_KEY, data)
    assert holds.grab_block(ctx, "sonarr-anime", body) is None
    # churn heeft een eigen code (upgrade_churn.pre_grab_block)
    holds.set_hold(ctx, key, hours=1, reason_nl="x", kind="churn")
    assert holds.grab_block(ctx, "sonarr-anime", body) is None
    assert holds.grab_block(ctx, "sonarr-anime", {"episodes": []}) is None
    holds.set_hold(ctx, holds.item_key("radarr-films", "movie", 9), hours=1, reason_nl="weg",
                   kind="vanished")
    assert "weg" in (holds.grab_block(ctx, "radarr-films", {"movie": {"id": 9}}) or "")


# -- item 35: Seerr-herstel alleen na een geslaagde test ---------------------------------------
def _server(env, kind: str, name: str):  # noqa: F811  # type: ignore[no-untyped-def]
    return next(s for s in env.seerr.servers[kind] if s["name"] == name)


def _puts(env, kind: str, sid: int) -> int:  # noqa: F811  # type: ignore[no-untyped-def]
    return sum(1 for m, p, _ in env.seerr.calls if m == "PUT" and p ==
               f"/api/v1/settings/{kind}/{sid}")


def test_seerr_herstel_schrijft_pas_na_geslaagde_test(env):  # noqa: F811  # type: ignore[no-untyped-def]
    from mediaguard.provisioning import seerr_sync
    from mediaguard.provisioning.api import drift_check

    ctx, seerr = env.ctx, env.seerr
    _seerr_setup(env)
    r = env.client.post("/api/v1/provisioning/seerr/apply", json={"confirm": True}).json()
    assert not r["failed"] and not r["remaining"], r
    series = _server(env, "sonarr", "Sonarr Series")
    seerr.test_results[f"{series['hostname']}:{series['port']}"] = False
    for _ in range(seerr_sync.REPEAT_LIMIT + 1):
        _server(env, "sonarr", "Sonarr Series")["activeDirectory"] = "/verkeerd"
        films = _server(env, "radarr", "Radarr Films")
        films["activeDirectory"] = "/ook-verkeerd"
        before = _puts(env, "sonarr", series["id"])
        films_before = _puts(env, "radarr", films["id"])
        asyncio.run(drift_check(ctx, repair=True))
        assert _puts(env, "radarr", films["id"]) == films_before + 1
        # Seerr's test met de nieuwe waarden faalt: niets geschreven, oude waarde blijft
        assert _puts(env, "sonarr", series["id"]) == before
        assert _server(env, "sonarr", "Sonarr Series")["activeDirectory"] == "/verkeerd"
        # de andere server slaagt wel en wordt hersteld
        assert _server(env, "radarr", "Radarr Films")["activeDirectory"] != "/ook-verkeerd"
    # een afgebroken test telt niet als 'Seerr bewaart het anders'
    assert "sonarr-series" not in (ctx.state_get(seerr_sync.IGNORED_KEY, {}) or {})
    items = [i for i in attention.collect_local(ctx) if i["key"] == "seerr.repair_test_failed"]
    assert len(items) == 1 and "Sonarr Series" in items[0]["detail_nl"]
    assert "oude instellingen" in items[0]["detail_nl"]
    assert items[0]["actions"][0]["path"] == "/provisioning/seerr/apply"
    # handmatig toepassen: zelfde test, duidelijke fout
    r = env.client.post("/api/v1/provisioning/seerr/apply", json={"confirm": True}).json()
    assert r["failed"] and "blijven staan" in r["failed"][0]["error"]
    # Seerr bereikt de instantie weer: herstel gaat door en de regel verdwijnt
    seerr.test_results[f"{series['hostname']}:{series['port']}"] = True
    asyncio.run(drift_check(ctx, repair=True))
    assert _server(env, "sonarr", "Sonarr Series")["activeDirectory"] != "/verkeerd"
    assert not [i for i in attention.collect_local(ctx)
                if i["key"] == "seerr.repair_test_failed"]


def test_seerr_testfout_verdwijnt_als_niets_meer_afwijkt(env):  # noqa: F811  # type: ignore[no-untyped-def]
    from mediaguard.provisioning import seerr_sync

    ctx = env.ctx
    _seerr_setup(env)
    env.client.post("/api/v1/provisioning/seerr/apply", json={"confirm": True})
    ctx.state_set(seerr_sync.TEST_FAILED_KEY, {"sonarr-series": {
        "name": "Sonarr Series", "url": "http://x:1", "error": "kapot", "at": "x"}})
    assert seerr_sync.attention_items(ctx)
    env.client.post("/api/v1/provisioning/seerr/apply", json={"confirm": False})
    assert seerr_sync.attention_items(ctx) == []


# -- controle: ook inrichting, collecties en nazorg wachten tijdens de noodstop -----------------
def test_noodstop_inrichting_herstelt_alleen_essentials(env):  # noqa: F811  # type: ignore[no-untyped-def]
    """Drift-herstel en opstarten passen tijdens de noodstop geen profielen, formats,
    mediabeheer of Seerr aan; de essentials (webhook, tags, mediapaden) wel."""
    from test_provisioning import apply as prov_apply
    from test_provisioning import put_cfg

    from mediaguard.provisioning.api import drift_check, ensure_essentials

    ctx = env.ctx
    put_cfg(env.client)
    prov_apply(env.client)
    ss = env.arr["sonarr-series"]
    ss.configs["mediamanagement"]["copyUsingHardlinks"] = False
    prof = next(p for p in ss.quality_profiles if p["name"].startswith("MediaGuard"))
    prof["minFormatScore"] = 9999
    ss.providers["notification"].clear()
    _stop(ctx)
    r = asyncio.run(drift_check(ctx, repair=True))
    assert r["drift"] >= 3 and r["repaired"] == 1 and "Noodstop actief" in r["paused_nl"]
    assert ss.providers["notification"]  # essential: MediaGuard-webhook terug
    assert ss.configs["mediamanagement"]["copyUsingHardlinks"] is False
    assert ss.profile(prof["name"])["minFormatScore"] == 9999
    res = asyncio.run(ensure_essentials(ctx))  # herstart tijdens de noodstop
    assert {c["section"] for c in res["applied"]} <= {"tags", "rootfolders", "webhook"}
    assert ss.profile(prof["name"])["minFormatScore"] == 9999
    _stop(ctx, False)
    r = asyncio.run(drift_check(ctx, repair=True))
    assert r["remaining"] == 0 and "paused_nl" not in r
    assert ss.configs["mediamanagement"]["copyUsingHardlinks"] is True
    assert ss.profile(prof["name"])["minFormatScore"] != 9999


def test_noodstop_seerr_herstel_wacht(env):  # noqa: F811  # type: ignore[no-untyped-def]
    from mediaguard.provisioning.api import drift_check

    ctx = env.ctx
    _seerr_setup(env)
    env.client.post("/api/v1/provisioning/seerr/apply", json={"confirm": True})
    series = _server(env, "sonarr", "Sonarr Series")
    series["activeDirectory"] = "/verkeerd"
    before = _puts(env, "sonarr", series["id"])
    _stop(ctx)
    asyncio.run(drift_check(ctx, repair=True))
    assert _puts(env, "sonarr", series["id"]) == before
    _stop(ctx, False)
    asyncio.run(drift_check(ctx, repair=True))
    assert _server(env, "sonarr", "Sonarr Series")["activeDirectory"] != "/verkeerd"


async def test_noodstop_collecties_en_nazorg_wachten(ctx, monkeypatch):  # type: ignore[no-untyped-def]
    from mediaguard import storage_typos
    from mediaguard.instances import registry
    from mediaguard.migration import aftercare

    def boom(*_a, **_kw):  # type: ignore[no-untyped-def]
        raise AssertionError("geen Sonarr/Radarr-aanroep tijdens de noodstop")

    monkeypatch.setattr(registry, "list_instances", boom)
    _stop(ctx)
    assert await storage_typos.fix_collections(ctx) == 0
    jc = jobs.JobContext(job_id=1, job_type=aftercare.JOB_TYPE, payload={"cutover_id": 1},
                         instance_id=None, correlation_id=None, app=ctx)
    with pytest.raises(jobs.Waiting):  # telt niet als poging; na de noodstop verder
        await aftercare._job(jc)


# -- controle item 35: getest wordt met de NIEUWE waarden ---------------------------------------
def test_seerr_test_gebruikt_nieuwe_sleutel(env):  # noqa: F811  # type: ignore[no-untyped-def]
    from mediaguard.instances.registry import apikey_secret
    from mediaguard.provisioning.api import drift_check

    ctx, seerr = env.ctx, env.seerr
    _seerr_setup(env)
    env.client.post("/api/v1/provisioning/seerr/apply", json={"confirm": True})
    series = _server(env, "sonarr", "Sonarr Series")
    series["apiKey"] = "oude-sleutel-0000"
    seerr.calls.clear()
    asyncio.run(drift_check(ctx, repair=True))
    tests = [b for m, p, b in seerr.calls if m == "POST" and p == "/api/v1/settings/sonarr/test"]
    assert tests and tests[0]["apiKey"] == ctx.secrets.get(apikey_secret("sonarr-series"))
    assert tests[0]["hostname"] == series["hostname"] and "oude-sleutel" not in str(tests)
    assert _server(env, "sonarr", "Sonarr Series")["apiKey"] != "oude-sleutel-0000"


def test_seerr_nieuwe_server_alleen_na_geslaagde_test(env):  # noqa: F811  # type: ignore[no-untyped-def]
    from mediaguard.instances.registry import get_instance
    from mediaguard.provisioning import seerr_sync
    from mediaguard.seerr.service import _expected_url, connection_fields

    ctx, seerr = env.ctx, env.seerr
    fields = connection_fields(_expected_url(get_instance(ctx, "sonarr-series").to_dict()))
    seerr.test_results[f"{fields['hostname']}:{fields['port']}"] = False
    _seerr_setup(env)
    r = env.client.post("/api/v1/provisioning/seerr/apply", json={"confirm": True}).json()
    assert [f["instance_id"] for f in r["failed"]] == ["sonarr-series"]
    assert "Sonarr Series" not in {s["name"] for s in seerr.servers["sonarr"]}
    assert "Radarr Films" in {s["name"] for s in seerr.servers["radarr"]}
    (item,) = seerr_sync.attention_items(ctx)
    assert "Seerr meldt:" in item["detail_nl"]
    # Seerr-inrichting uitgezet: geen blijvende regel in 'Wacht op jou'
    from mediaguard.provisioning import store

    store.save(ctx, {"seerr": {"enabled": False, "scan": True}}, "test")
    env.client.post("/api/v1/provisioning/seerr/apply", json={"confirm": False})
    assert seerr_sync.attention_items(ctx) == []
