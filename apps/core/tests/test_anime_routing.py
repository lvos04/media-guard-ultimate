"""Automatische anime-routering: Seerr zet anime in sonarr-series/radarr-films → MediaGuard
verplaatst naar sonarr-anime/radarr-animefilms (veilig, met bewijs, terug te draaien)."""

from __future__ import annotations

from datetime import UTC, datetime

import pytest
from test_arr_fakes import (  # noqa: F401 - b1ctx is een fixture
    FakeArr,
    b1ctx,
    make_episode,
    make_movie,
    make_series,
    wire,
)

from mediaguard.routing import service


def now_iso() -> str:
    return datetime.now(UTC).isoformat().replace("+00:00", "Z")


@pytest.fixture
def fakes(b1ctx):  # noqa: F811  # type: ignore[no-untyped-def]
    series = wire(b1ctx, "sonarr-series", FakeArr("sonarr"))
    anime = wire(b1ctx, "sonarr-anime", FakeArr("sonarr"))
    anime.quality_profiles.append({"id": 7, "name": "Anime Dual Audio", "items": []})
    anime.root_folders = [{"id": 1, "path": "/anime/", "accessible": True}]
    films = wire(b1ctx, "radarr-films", FakeArr("radarr"))
    afilms = wire(b1ctx, "radarr-animefilms", FakeArr("radarr"))
    afilms.root_folders = [{"id": 1, "path": "/animefilms/", "accessible": True}]
    return b1ctx, series, anime, films, afilms


def test_detection_rules():
    assert service.detect_anime({"seriesType": "anime"}, "sonarr")["is_anime"]
    assert service.detect_anime({"genres": ["Anime", "Action"]}, "sonarr")["is_anime"]
    jp = {"genres": ["Animation"], "originalLanguage": {"name": "Japanese"}}
    assert service.detect_anime(jp, "radarr")["is_anime"]
    assert not service.detect_anime(jp, "radarr", japanese_animation=False)["is_anime"]
    western = {"genres": ["Animation", "Family"], "originalLanguage": {"name": "English"}}
    assert not service.detect_anime(western, "radarr")["is_anime"]


async def test_new_anime_series_is_moved_with_monitoring(fakes):
    ctx, series, anime, *_ = fakes
    s = make_series(10, "Frieren", 424536, series_type="anime", seasons={1: True, 2: False},
                    monitor_new_items="all")
    s["added"] = now_iso()
    series.series[10] = s
    for eid, (sn, en, mon) in enumerate([(1, 1, True), (1, 2, False), (2, 1, False)], 1):
        e = make_episode(eid, 10, sn, en)
        e["monitored"] = mon
        series.episodes[eid] = e
    # de 'lookup' op de doelinstantie levert de serie; de fake maakt afleveringen niet
    # zelf aan, dus leg ze na het toevoegen aan zoals Sonarr dat doet
    orig_add = anime.series

    result = await service.evaluate(ctx, "sonarr-series", "series", 10)
    assert result["status"] == "moved", result
    assert 10 not in series.series                              # weg uit gewone instantie
    moved = next(iter(orig_add.values()))
    assert moved["tvdbId"] == 424536 and moved["seriesType"] == "anime"
    assert moved["qualityProfileId"] == 7 and moved["rootFolderPath"] == "/anime/"
    assert {x["seasonNumber"]: x["monitored"] for x in moved["seasons"]} == {1: True, 2: False}
    assert moved["monitorNewItems"] == "all"
    row = service.list_actions(ctx)[0]
    assert row["status"] == "moved" and "Seriestype is 'anime'" in row["evidence"]


async def test_series_with_files_or_download_is_not_moved(fakes):
    ctx, series, anime, *_ = fakes
    s = make_series(11, "Bladschaduw", 78857, series_type="anime")
    s["added"] = now_iso()
    s["statistics"]["episodeFileCount"] = 3
    series.series[11] = s
    series._with_stats = lambda x: x  # type: ignore[method-assign]  # stats vasthouden
    result = await service.evaluate(ctx, "sonarr-series", "series", 11)
    assert result["status"] == "needs_review"
    assert 11 in series.series and not anime.series


async def test_non_anime_untouched_and_keep_here_respected(fakes):
    ctx, series, anime, *_ = fakes
    normal = make_series(12, "The Bear", 412345)
    normal["added"] = now_iso()
    series.series[12] = normal
    assert (await service.evaluate(ctx, "sonarr-series", "series", 12))["status"] == \
        "not_anime"
    s = make_series(13, "Zielsnijder", 74796, series_type="anime")
    s["added"] = now_iso()
    series.series[13] = s
    service.exclude(ctx, "sonarr-series", "series", 74796, "test")
    assert (await service.evaluate(ctx, "sonarr-series", "series", 13))["status"] == "excluded"
    assert not anime.series


async def test_suggest_mode_and_old_titles_only_suggested(fakes):
    ctx, series, anime, *_ = fakes
    old = make_series(14, "Cowboy Bebop", 76885, series_type="anime")  # added in 2023
    series.series[14] = old
    assert (await service.evaluate(ctx, "sonarr-series", "series", 14))["status"] == \
        "suggested"
    ctx.config.set("global", "", "routing.anime.mode", "suggest", "t")
    new = make_series(15, "Spookstad", 900433, series_type="anime")
    new["added"] = now_iso()
    series.series[15] = new
    assert (await service.evaluate(ctx, "sonarr-series", "series", 15))["status"] == \
        "suggested"
    assert not anime.series
    # goedkeuren = force
    res = await service.evaluate(ctx, "sonarr-series", "series", 15, force=True)
    assert res["status"] == "moved"


async def test_anime_film_moved_to_animefilms(fakes):
    ctx, _s, _a, films, afilms = fakes
    m = make_movie(20, "Suzume", 916224)
    m.update(genres=["Animation", "Adventure"], originalLanguage={"id": 8, "name": "Japanese"},
             added=now_iso())
    films.movies[20] = m
    result = await service.evaluate(ctx, "radarr-films", "movie", 20)
    assert result["status"] == "moved", result
    assert 20 not in films.movies
    moved = next(iter(afilms.movies.values()))
    assert moved["tmdbId"] == 916224 and moved["rootFolderPath"] == "/animefilms/"


async def test_failure_on_target_keeps_source(fakes):
    ctx, series, anime, *_ = fakes
    anime.root_folders = []
    s = make_series(16, "Golfbreker", 900817, series_type="anime")
    s["added"] = now_iso()
    series.series[16] = s
    result = await service.evaluate(ctx, "sonarr-series", "series", 16)
    assert result["status"] == "failed"
    assert 16 in series.series


def test_webhook_series_add_enqueues_check(fakes, client):
    ctx = fakes[0]
    from mediaguard.hooks import service as hooks

    hooks.store_webhook(ctx, "sonarr-series", {"eventType": "SeriesAdd",
                                               "series": {"id": 99, "title": "X"}})
    jobs = ctx.db.fetch_all("SELECT * FROM jobs WHERE job_type='routing.anime_check'")
    assert len(jobs) == 1
    hooks.store_webhook(ctx, "sonarr-anime", {"eventType": "SeriesAdd",
                                              "series": {"id": 5, "title": "Y"}})
    assert len(ctx.db.fetch_all(
        "SELECT * FROM jobs WHERE job_type='routing.anime_check'")) == 1  # alleen bron


async def test_question_about_removed_title_is_closed_not_crashing(fakes):
    """Verbeterlijst 9: een open 'anime of niet?'-vraag over een titel die inmiddels uit Sonarr
    is verwijderd liet elke leveringsronde crashen. Nu: vraag gesloten, ronde gaat door."""
    ctx, *_ = fakes
    now = now_iso()
    ctx.db.execute("INSERT INTO anime_routing_actions(source_instance,kind,provider_id,title,"
                   "status,created_at,updated_at) VALUES ('sonarr-series','series',99,'Weg',"
                   "'suggested',?,?)", (now, now))
    result = await service.evaluate(ctx, "sonarr-series", "series", 99, force=True)
    assert result == {"status": "ignored", "reason": "titel niet gevonden"}
    row = ctx.db.fetch_one("SELECT status, detail FROM anime_routing_actions WHERE "
                           "provider_id=99")
    assert row["status"] == "dismissed" and "bestaat niet meer" in row["detail"]
