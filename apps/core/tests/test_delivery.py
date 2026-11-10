"""Leveringsgarantie: een Seerr-aanvraag wordt zonder interactie gevolgd en hersteld tot hij
volgens de eisen geleverd is (docs/DELIVERY.md)."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from typing import Any

import pytest
from test_arr_fakes import (  # noqa: F401 - b1ctx is een fixture
    FakeArr,
    FakeSeerr,
    b1ctx,
    iso,
    make_episode,
    make_movie,
    make_series,
    wire,
)

from mediaguard.delivery import ai as decider
from mediaguard.delivery import engine, store

MG_SERIES = "MediaGuard · WEB-2160p"


@pytest.fixture
def env(b1ctx):  # noqa: F811  # type: ignore[no-untyped-def]
    ctx = b1ctx
    arr = {iid: wire(ctx, iid, FakeArr("sonarr" if iid.startswith("sonarr") else "radarr"))
           for iid in ("sonarr-series", "sonarr-anime", "radarr-films", "radarr-animefilms")}
    arr["sonarr-anime"].quality_profiles.append({"id": 7, "name": "Anime Dual Audio",
                                                 "items": []})
    arr["sonarr-anime"].root_folders = [{"id": 1, "path": "/anime/", "accessible": True}]
    seerr = FakeSeerr()
    seerr.jobs: list[str] = []  # type: ignore[attr-defined]

    @seerr.asgi.post("/api/v1/request/{rid}/approve")
    def approve(rid: int) -> dict:
        for r in seerr.requests:
            if r["id"] == rid:
                r["status"] = 2
        return {}

    @seerr.asgi.post("/api/v1/request/{rid}/retry")
    def retry(rid: int) -> dict:
        return {}

    @seerr.asgi.post("/api/v1/settings/jobs/{job}/run")
    def run_job(job: str) -> dict:
        seerr.jobs.append(job)  # type: ignore[attr-defined]
        return {}

    ctx.services["seerr_transport"] = seerr.transport()
    ctx.config.set("global", "", "seerr.url", "http://seerr:5055", "test")
    ctx.secrets.set("seerr:apikey", seerr.api_key, "test")
    return ctx, arr, seerr


def seerr_request(rid: int, *, tv: bool, ext: int, seasons: list[int] | None = None,
                  status: int = 2) -> dict[str, Any]:
    media = {"tvdbId": ext, "tmdbId": None, "status": 3} if tv else \
        {"tmdbId": ext, "tvdbId": None, "status": 3}
    return {"id": rid, "type": "tv" if tv else "movie", "status": status, "media": media,
            "seasons": [{"seasonNumber": s} for s in seasons or []],
            "createdAt": iso(-1), "requestedBy": {"displayName": "Jan"}}


def commands(fake: FakeArr, name: str) -> list[dict[str, Any]]:
    return [c["body"] for c in fake.commands.values() if c["name"] == name]


def item(ctx: Any) -> dict[str, Any]:
    return store.list_items(ctx)[0]


def managed_since(ctx: Any, days: float = 30) -> None:
    """MediaGuard beheert de instanties al een tijd (aanvragen daarna zijn 'nieuw')."""
    ctx.db.execute("UPDATE instances SET created_at=?", (iso(-days),))


async def test_series_request_is_searched_then_delivered_and_seerr_synced(env):
    ctx, arr, seerr = env
    managed_since(ctx)
    sonarr = arr["sonarr-series"]
    sonarr.quality_profiles.append({"id": 9, "name": MG_SERIES, "items": []})
    s = make_series(10, "Severance", 371980, seasons={1: True, 2: False})
    s["added"] = iso()  # Seerr zette hem net in Sonarr
    sonarr.add_series(s, [make_episode(1, 10, 1, 1), make_episode(2, 10, 1, 2),
                          make_episode(3, 10, 2, 1, monitored=False)])
    seerr.requests.append(seerr_request(1, tv=True, ext=371980, seasons=[1, 2]))

    out = await engine.run_once(ctx, force=True)
    assert out["processed"] == 1 and out["errors"] == 0
    row = item(ctx)
    assert row["instance_id"] == "sonarr-series" and row["provider_id"] == 10
    # eisen afgedwongen: MediaGuard-profiel en het aangevraagde seizoen 2 bewaakt
    assert sonarr.series[10]["qualityProfileId"] == 9
    assert {s["seasonNumber"]: s["monitored"] for s in sonarr.series[10]["seasons"]} == {
        1: True, 2: True}
    # seizoen 2 was aangevraagd: Sonarr bewaakt dan ook aflevering 3
    assert commands(sonarr, "EpisodeSearch")[0]["episodeIds"] == [1, 2, 3]
    assert row["stage"] == "searching" and row["state"] == "active"

    for eid in (1, 2, 3):
        sonarr.episodes[eid].update(hasFile=True, episodeFileId=100 + eid, monitored=True)
    await engine.run_once(ctx, force=True)
    row = item(ctx)
    assert row["state"] == "delivered" and row["stage_nl"] == "Geleverd"
    assert "sonarr-scan" in seerr.jobs and "availability-sync" in seerr.jobs
    timeline = [e["action"] for e in store.events(ctx, row["id"])]
    assert "profiel" in timeline and "gezocht" in timeline


async def test_movie_waits_for_release_then_failed_download_is_replaced(env):
    ctx, arr, seerr = env
    radarr = arr["radarr-films"]
    radarr.movies[5] = make_movie(5, "Wadi Deel Drie", 999, available=False)
    seerr.requests.append(seerr_request(2, tv=False, ext=999))

    await engine.run_once(ctx, force=True)
    assert item(ctx)["state"] == "waiting_release"
    assert not commands(radarr, "MoviesSearch")

    radarr.movies[5]["isAvailable"] = True
    radarr.queue.append({"id": 77, "movieId": 5, "title": "Wadi.Drie.2160p.WEB-DL",
                         "status": "failed", "trackedDownloadState": "failedPending",
                         "trackedDownloadStatus": "error", "downloadId": "SAB_9",
                         "statusMessages": [{"messages": ["Uitpakken mislukt"]}]})
    await engine.run_once(ctx, force=True)
    deletes = radarr.calls_to("DELETE", "/api/v3/queue/77")
    assert deletes and deletes[0][2].get("blocklist") == "true"  # release geblokkeerd
    assert commands(radarr, "MoviesSearch")  # direct een andere release gezocht
    assert any(e["action"] == "download_mislukt" for e in store.events(ctx, item(ctx)["id"]))

    radarr.movies[5] = make_movie(5, "Wadi Deel Drie", 999, has_file=True)
    await engine.run_once(ctx, force=True)
    assert item(ctx)["state"] == "delivered"


async def test_anime_request_lands_in_anime_instance(env):
    ctx, arr, seerr = env
    series, anime = arr["sonarr-series"], arr["sonarr-anime"]
    s = make_series(11, "Frieren", 424536, series_type="anime")
    s["added"] = iso()
    series.add_series(s, [make_episode(21, 11, 1, 1)])
    seerr.requests.append(seerr_request(3, tv=True, ext=424536, seasons=[1]))

    await engine.run_once(ctx, force=True)
    row = item(ctx)
    assert 11 not in series.series and row["instance_id"] == "sonarr-anime"
    moved = next(iter(anime.series.values()))
    assert moved["qualityProfileId"] == 7 and moved["seriesType"] == "anime"


async def test_pending_seerr_request_is_approved_without_user(env):
    ctx, _arr, seerr = env
    seerr.requests.append(seerr_request(4, tv=False, ext=1234, status=1))
    await engine.run_once(ctx, force=True)
    assert seerr.requests[0]["status"] == 2
    row = item(ctx)
    assert row["stage"] == "adding" and "Seerr" in (row["detail_nl"] or "")


async def test_stuck_import_is_left_to_the_stuck_checker(env):
    """Verbeterlijst 5: de motor gooit een download die op import wacht nooit zelf weg (ook
    niet 5 uur na de grab, ook niet met een foutstatus); dat doet alleen wanted/stuck.py."""
    ctx, arr, seerr = env
    sonarr = arr["sonarr-series"]
    sonarr.add_series(make_series(12, "Andor", 393189), [make_episode(31, 12, 1, 1)])
    seerr.requests.append(seerr_request(5, tv=True, ext=393189, seasons=[1]))
    sonarr.queue.append({"id": 88, "seriesId": 12, "episodeId": 31, "title": "Andor.S01E01",
                         "status": "completed", "trackedDownloadState": "importBlocked",
                         "trackedDownloadStatus": "warning", "downloadId": "SAB_X",
                         "added": (datetime.now(UTC) - timedelta(hours=5)).isoformat(),
                         "statusMessages": [{"messages": ["No files found are eligible"]}]})
    sonarr.queue.append({"id": 89, "seriesId": 12, "episodeId": 31, "title": "Andor.S01E01.b",
                         "status": "completed", "trackedDownloadState": "importPending",
                         "trackedDownloadStatus": "error", "downloadId": "SAB_Y",
                         "added": (datetime.now(UTC) - timedelta(hours=9)).isoformat()})
    await engine.run_once(ctx, force=True)
    await engine.run_once(ctx, force=True)
    assert not sonarr.calls_to("DELETE", "/api/v3/queue/")
    assert not commands(sonarr, "EpisodeSearch")
    row = item(ctx)
    assert row["stage"] == "importing" and "No files found" in row["detail_nl"]
    assert not any(e["action"] == "import_vast" for e in store.events(ctx, row["id"]))


async def test_not_found_after_long_search_only_informs(env):
    ctx, arr, seerr = env
    sonarr = arr["sonarr-series"]
    sonarr.add_series(make_series(13, "Obscure", 111), [make_episode(41, 13, 1, 1)])
    seerr.requests.append(seerr_request(6, tv=True, ext=111, seasons=[1]))
    await engine.run_once(ctx, force=True)
    old = (datetime.now(UTC) - timedelta(days=40)).isoformat(timespec="seconds")
    store.update(ctx, item(ctx)["id"], first_search_at=old)
    await engine.run_once(ctx, force=True)
    row = item(ctx)
    assert row["state"] == "not_found"
    notes = ctx.db.fetch_dicts("SELECT severity, title FROM notifications")
    assert any(n["severity"] == "info" and "nergens te vinden" in n["title"] for n in notes)
    # niets in 'Wacht op jou': informatie, geen vraag
    from mediaguard.attention import collect_local

    assert not [i for i in collect_local(ctx) if "delivery" in i["key"]]


async def test_requirements_are_never_relaxed(env):
    """De motor kiest nooit een lager profiel en de AI kan alleen opties binnen de eisen
    kiezen; een antwoord buiten de opties wordt genegeerd."""
    ctx, arr, seerr = env
    sonarr = arr["sonarr-series"]
    sonarr.quality_profiles.append({"id": 9, "name": MG_SERIES, "items": []})
    s = make_series(14, "Shogun", 392573, quality_profile_id=9)
    sonarr.add_series(s, [make_episode(51, 14, 1, 1)])
    seerr.requests.append(seerr_request(7, tv=True, ext=392573, seasons=[1]))
    await engine.run_once(ctx, force=True)
    assert sonarr.series[14]["qualityProfileId"] == 9
    assert not [c for c in sonarr.calls if c[0] == "PUT" and "qualityprofile" in c[1]]

    async def rogue(*_a: Any, **_k: Any) -> decider.Decision | None:
        return None  # onbekende/ongeldige keuze wordt in _ask al afgewezen

    import mediaguard.delivery.ai as mod
    mod_ask = mod._ask
    try:
        mod._ask = rogue  # type: ignore[assignment]
        d = await decider.decide(ctx, "x", "?", ["a", "b"], {}, fallback="a",
                                 fallback_reason_nl="regel")
        assert d.choice == "a" and d.by == "regel"
    finally:
        mod._ask = mod_ask  # type: ignore[assignment]
    with pytest.raises(ValueError):
        await decider.decide(ctx, "x", "?", ["a"], {}, fallback="z", fallback_reason_nl="")


async def test_anime_review_is_decided_by_ai_not_user(env, monkeypatch):
    ctx, arr, _seerr = env
    from mediaguard.attention import collect_local
    from mediaguard.routing import service as routing

    series = arr["sonarr-series"]
    s = make_series(15, "Cowboy Bebop", 76885, series_type="standard")
    s["genres"] = ["Animation"]
    s["originalLanguage"] = {"name": "Japanese"}
    series.add_series(s, [make_episode(61, 15, 1, 1)])
    routing._record(ctx, source="sonarr-series", target="sonarr-anime", kind="series",
                    provider_id=15, external_id=76885, title="Cowboy Bebop",
                    status="suggested", evidence=["Genre 'Animation' en oorspronkelijke taal "
                                                  "Japans"])
    assert not [i for i in collect_local(ctx) if i["key"] == "routing.review"]

    async def ai(*_a: Any, **_k: Any) -> decider.Decision:
        return decider.Decision("verplaatsen", "AI (test): Japanse anime", "ai:test", 0.95)

    monkeypatch.setattr(decider, "_ask", ai)
    assert await engine.resolve_routing_reviews(ctx) == 1
    assert 15 not in series.series and arr["sonarr-anime"].series
    acts = ctx.db.fetch_dicts("SELECT action, actor, reason FROM audit_events WHERE "
                              "action='delivery.ai_decision'")
    assert acts and acts[0]["actor"] == "ai:test" and "Japanse" in acts[0]["reason"]


async def test_api_lists_requests_with_dutch_stage(env, client):  # type: ignore[no-untyped-def]
    ctx, arr, seerr = env
    arr["radarr-films"].movies[8] = make_movie(8, "Arrival", 329865, has_file=True)
    seerr.requests.append(seerr_request(9, tv=False, ext=329865))
    await engine.run_once(ctx, force=True)
    body = client.get("/api/v1/delivery/requests").json()
    assert body["counts"]["delivered"] == 1
    first = body["items"][0]
    assert first["stage_nl"] == "Geleverd" and first["title"] == "Arrival"
    detail = client.get(f"/api/v1/delivery/requests/{first['id']}").json()
    assert detail["events"]


def test_r4_untitled_or_dateless_movie_is_not_released():
    from datetime import UTC, datetime

    from mediaguard.delivery.engine import movie_released

    now = datetime(2026, 11, 5, tzinfo=UTC)
    assert not movie_released({"title": "Untitled Spiegelpaleis 4"}, now)
    assert not movie_released({"title": "X"}, now)
    assert not movie_released({"title": "X", "digitalRelease": "2027-01-01T00:00:00Z"}, now)
    assert movie_released({"title": "X", "digitalRelease": "2026-10-01T00:00:00Z"}, now)
    assert movie_released({"title": "X", "inCinemas": "2020-01-01T00:00:00Z"}, now)
