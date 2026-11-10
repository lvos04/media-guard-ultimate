"""Bibliotheeksync, classificatie (unmonitored/toekomst nooit missing), matrix, kalender, KPI's."""

from __future__ import annotations

import pytest
from test_arr_fakes import (
    FakeArr,
    b1ctx,  # noqa: F401
    iso,
    make_episode,
    make_episode_file,
    make_movie,
    make_series,
    wire,
)

from mediaguard.library.queries import kpis, season_matrix
from mediaguard.library.sync import iso_z, sync_instance


def sonarr_library() -> FakeArr:
    fake = FakeArr("sonarr")
    eps = [
        make_episode(1, 1, 1, 1, has_file=True, file_id=101),            # beschikbaar
        make_episode(2, 1, 1, 2, has_file=True, file_id=102),            # onder cutoff
        make_episode(3, 1, 1, 3, aired_days=-3),                        # ontbreekt (recent)
        make_episode(4, 1, 1, 4, aired_days=-200, monitored=False),     # bewust unmonitored
        make_episode(5, 1, 2, 1, aired_days=5),                         # toekomst
        make_episode(6, 1, 2, 2, air=False),                            # TBA
        make_episode(7, 1, 0, 1, aired_days=-400),                      # special, ontbreekt
    ]
    fake.add_series(make_series(1, "The Expanse", 280619, seasons={0: False, 1: True, 2: True}),
                    eps, [make_episode_file(101, 1, 1),
                          make_episode_file(102, 1, 1, cutoff_not_met=True)])
    # volledig niet-gemonitorde serie: niets telt als ontbrekend
    fake.add_series(make_series(2, "Lost", 73739, monitored=False),
                    [make_episode(20, 2, 1, 1, aired_days=-3000)])
    return fake


async def test_sync_and_classification(b1ctx):  # noqa: F811
    fake = wire(b1ctx, "sonarr-series", sonarr_library())
    result = await sync_instance(b1ctx, "sonarr-series")
    assert result["series"] == 2 and result["episodes"] == 8 and result["files"] == 2
    assert result["wanted"]["open"] == 3  # ep3 + special ep7 (missing) + ep2 (cutoff)
    rows = b1ctx.db.fetch_dicts("SELECT provider_id, reason FROM wanted_items ORDER BY 1")
    assert [(r["provider_id"], r["reason"]) for r in rows] == [
        (2, "cutoff"), (3, "missing"), (7, "missing")]
    k = kpis(b1ctx, "sonarr-series")["instances"][0]["kpis"]
    assert k["missing"] == 2 and k["available"] == 2 and k["cutoff_unmet"] == 1
    assert k["upcoming"] == 2  # toekomst + TBA, nooit 'missing'
    assert k["unmonitored"] == 2  # ep4 + serie Lost
    assert k["upcoming_7d"] == 1
    assert k["missing_aired_7d"] == 1 and k["missing_aired_90d"] == 1
    assert k["future_season_coverage"] == 1.0
    # verwijderde serie wordt gemarkeerd, niet gewist
    del fake.series[2]
    await sync_instance(b1ctx, "sonarr-series")
    row = b1ctx.db.fetch_one("SELECT removed_at FROM lib_items WHERE provider_id=2")
    assert row["removed_at"] is not None
    assert b1ctx.db.fetch_one("SELECT COUNT(*) AS n FROM lib_episodes WHERE "
                              "series_provider_id=2")["n"] == 0


async def test_season_matrix_marks_future_not_missing(b1ctx):  # noqa: F811
    wire(b1ctx, "sonarr-series", sonarr_library())
    await sync_instance(b1ctx, "sonarr-series")
    item = b1ctx.db.fetch_dicts("SELECT * FROM lib_items WHERE provider_id=1")[0]
    matrix = season_matrix(b1ctx, item, queue_episode_ids={3})
    seasons = {s["season_number"]: s for s in matrix["seasons"]}
    states = {e["episode_id"]: e["state"] for s in matrix["seasons"] for e in s["episodes"]}
    assert states == {1: "available", 2: "cutoff_unmet", 3: "downloading",
                      4: "unmonitored", 5: "upcoming", 6: "upcoming", 7: "missing"}
    assert seasons[2]["counts"]["missing"] == 0 and seasons[2]["expected_aired"] == 0
    assert seasons[1]["completeness"] == pytest.approx(2 / 3, rel=1e-3)
    assert "Toekomstige" in matrix["note_nl"]


async def test_radarr_sync_states(b1ctx):  # noqa: F811
    fake = wire(b1ctx, "radarr-films", FakeArr("radarr", api_key="films-key-999"))
    fake.movies = {
        1: make_movie(1, "Wadi", 438631, has_file=True),
        2: make_movie(2, "Oppenheimer", 872585),                          # ontbreekt
        3: make_movie(3, "Nevelwoud 4", 393209, available=False, digital_days=3),  # nog niet
        4: make_movie(4, "Cats", 536869, monitored=False),                # unmonitored
        5: make_movie(5, "Tenet", 577922, has_file=True, file_cutoff=True),
    }
    res = await sync_instance(b1ctx, "radarr-films")
    assert res["movies"] == 5 and res["files"] == 2
    k = kpis(b1ctx, "radarr-films")["instances"][0]["kpis"]
    assert (k["available"], k["missing"], k["upcoming"], k["unmonitored"], k["cutoff_unmet"]) \
        == (2, 1, 1, 1, 1)
    assert k["upcoming_7d"] == 1
    reasons = {(r["provider_id"], r["reason"]) for r in b1ctx.db.fetch_dicts(
        "SELECT provider_id, reason FROM wanted_items WHERE instance_id='radarr-films'")}
    assert reasons == {(2, "missing"), (5, "cutoff")}


def test_library_api_filters_detail_calendar(client, ctx):
    wire(ctx, "sonarr-series", sonarr_library())
    anime = wire(ctx, "sonarr-anime", FakeArr("sonarr", api_key="anime-key-999"))
    anime.add_series(make_series(1, "Golfbreker", 900817, series_type="anime"),
                     [make_episode(1, 1, 1, 1, aired_days=1, absolute=1)])
    r = client.post("/api/v1/library/sync?mode=inline")
    assert r.status_code == 200
    assert set(r.json()["results"]) == {"sonarr-series", "sonarr-anime", "radarr-films",
                                        "radarr-animefilms"}
    assert "error" in r.json()["results"]["radarr-films"]  # geen fake → onbereikbaar
    lib = client.get("/api/v1/library").json()
    assert lib["total"] == 3
    assert client.get("/api/v1/library?media_type=anime").json()["total"] == 1
    assert client.get("/api/v1/library?state=missing").json()["total"] == 1
    assert client.get("/api/v1/library?q=expanse").json()["items"][0]["stats"]["missing"] == 2
    item_id = client.get("/api/v1/library?q=expanse").json()["items"][0]["id"]
    detail = client.get(f"/api/v1/library/{item_id}").json()
    assert detail["tvdb_id"] == 280619 and len(detail["seasons"]) == 3
    seasons = client.get(f"/api/v1/library/{item_id}/seasons?live_queue=true").json()
    assert seasons["series_id"] == 1
    cal = client.get("/api/v1/calendar", params={"start": iso(-1), "end": iso(10)}).json()
    assert {(i["instance_id"], i["episode_id"]) for i in cal["items"]} == {
        ("sonarr-series", 5), ("sonarr-anime", 1)}
    cal = client.get("/api/v1/calendar", params={"start": iso(-1), "end": iso(10),
                                                  "instance_id": "sonarr-anime"}).json()
    assert cal["total"] == 1 and cal["items"][0]["media_type"] == "anime"
    kp = client.get("/api/v1/library/kpis").json()
    assert kp["totals"]["missing"] == 2 and "wanted" in kp
    status = client.get("/api/v1/library/sync-status").json()
    assert {s["instance_id"]: s["last_run"]["status"] for s in status}["sonarr-series"] \
        == "succeeded"
    assert client.get("/api/v1/instances/sonarr-anime/library").json()["total"] == 1


def test_iso_z_normalisation():
    assert iso_z("2024-01-01T01:00:00Z") == "2024-01-01T01:00:00Z"
    assert iso_z("2024-01-01T01:00:00.000Z") == "2024-01-01T01:00:00Z"
    assert iso_z("2024-01-01T03:00:00+02:00") == "2024-01-01T01:00:00Z"
    assert iso_z("2024-01-01") == "2024-01-01T00:00:00Z"
    assert iso_z("0001-01-01T00:00:00Z") is None
    assert iso_z(None) is None and iso_z("onzin") is None


async def test_episode_fetch_failure_shows_sonarr_counts_not_zero(b1ctx):  # noqa: F811
    """Praktijk: na een restore was Sonarr te traag, alle afleveringsverzoeken faalden en de
    bibliotheek toonde '0 / 0 · Compleet' bij series van 100 GB. Nu: nog een poging per
    serie, en anders Sonarr's eigen telling (geschat), nooit een valse 0 / 0."""
    from mediaguard.library.queries import list_items

    fake = wire(b1ctx, "sonarr-series", sonarr_library())
    fake.fail_paths["/api/v3/episode"] = 500  # afleveringen én bestanden onleesbaar
    result = await sync_instance(b1ctx, "sonarr-series")
    assert {f["series_id"] for f in result["failed_items"]} == {1, 2}
    items = {i["provider_id"]: i for i in list_items(b1ctx, instance_id="sonarr-series")["items"]}
    assert b1ctx.db.fetch_one("SELECT COUNT(*) AS n FROM lib_episodes")["n"] == 0
    row = b1ctx.db.fetch_one("SELECT episode_count, episode_file_count FROM lib_items WHERE "
                             "provider_id=1")
    st = items[1]["stats"]
    assert row["episode_count"] > 0
    assert (st["total"], st["available"]) == (row["episode_count"], row["episode_file_count"])
    assert items[1]["stats_estimated"] is True
    fake.fail_paths.clear()
    await sync_instance(b1ctx, "sonarr-series")
    items = {i["provider_id"]: i for i in list_items(b1ctx, instance_id="sonarr-series")["items"]}
    assert items[1]["stats"]["total"] == 7 and "stats_estimated" not in items[1]



async def test_broken_episodefile_list_keeps_episodes(b1ctx):  # noqa: F811
    """Praktijk: Sonarr gaf 500 'Value cannot be null (Parameter source)' op /episodefile
    voor gemigreerde series. Afleveringen (en 'heeft bestand') moeten dan toch binnenkomen."""
    fake = wire(b1ctx, "sonarr-series", sonarr_library())
    fake.fail_paths["/api/v3/episodefile"] = 500
    result = await sync_instance(b1ctx, "sonarr-series")
    assert result["episodes"] == 8 and result["files"] == 0 and not result["failed_items"]
    k = kpis(b1ctx, "sonarr-series")["instances"][0]["kpis"]
    assert k["available"] == 2


async def test_episode_details_and_background_identity(b1ctx):  # noqa: F811
    """Rapport 4 nov (H14): detail met mediainfo, historie en AV1; identiteit automatisch."""
    from mediaguard.automation import background_identity
    from mediaguard.library.api import episode_details

    wire(b1ctx, "sonarr-series", sonarr_library())
    await sync_instance(b1ctx, "sonarr-series")
    assert background_identity(b1ctx) >= 1
    ep = b1ctx.db.fetch_one("SELECT e.provider_id, l.id AS item_id FROM lib_episodes e JOIN "
                            "lib_items l ON l.instance_id=e.instance_id AND l.kind='series' "
                            "AND l.provider_id=e.series_provider_id WHERE e.episode_file_id>0 "
                            "LIMIT 1")
    d = await episode_details(int(ep["item_id"]), int(ep["provider_id"]), ctx=b1ctx)
    assert d["file"] is not None and d["identity"]["status"] != "pending"
    assert isinstance(d["history"], list) and d["av1"] == []


async def test_background_movie_identity(b1ctx):  # noqa: F811
    """Films krijgen ook een identiteitscontrole: titel/jaar/ID in de naam vs Radarr."""
    from mediaguard.automation import background_movie_identity
    from mediaguard.library.api import movie_details

    fake = wire(b1ctx, "radarr-films", FakeArr("radarr", api_key="films-key-999"))
    good = make_movie(1, "Wadi", 438631, has_file=True)
    good["movieFile"]["relativePath"] = "Wadi.2021.2160p.UHD.BluRay-GRP.mkv"
    good["movieFile"]["path"] = "/movies/Wadi (2021)/Wadi.2021.2160p.UHD.BluRay-GRP.mkv"
    wrong = make_movie(2, "Tenet", 577922, has_file=True)
    wrong["movieFile"]["relativePath"] = "Dromenvanger.2010.1080p.mkv"
    wrong["movieFile"]["path"] = "/movies/Tenet (2021)/Dromenvanger.2010.1080p.mkv"
    fake.movies = {1: good, 2: wrong}
    await sync_instance(b1ctx, "radarr-films")
    assert background_movie_identity(b1ctx) == 2
    assert background_movie_identity(b1ctx) == 0  # al beoordeeld
    rows = {r["provider_id"]: r for r in b1ctx.db.fetch_dicts(
        "SELECT provider_id, status, reason_nl FROM identity_results WHERE kind='movie_file'")}
    assert rows["10"]["status"] == "probable"
    # zwak bewijs (naam) mag nooit zelf 'verkeerd' worden: alleen twijfel
    assert rows["20"]["status"] == "uncertain" and "Dromenvanger" in rows["20"]["reason_nl"]
    item = b1ctx.db.fetch_one("SELECT id FROM lib_items WHERE kind='movie' AND provider_id=1")
    d = await movie_details(int(item["id"]), ctx=b1ctx)
    assert d["identity"]["status"] == "probable"


def test_movie_name_evidence_rules():
    from mediaguard.identity.engine import Observation, resolve

    def status(path: str, title: str, year: int, **kw):
        return resolve([], Observation(path=path, is_movie=True, movie_title=title,
                                       movie_year=year, **kw)).status
    assert status("/m/Blade.Runner.2049.2017.1080p.mkv", "Blade Runner 2049", 2017) == "probable"
    assert status("/m/The.Matrix.2000.mkv", "The Matrix", 1999) == "probable"  # ±1 jaar
    assert status("/m/Wadi.1984.1080p.mkv", "Wadi", 2021) == "uncertain"       # remake
    assert status("/m/Le.Fabuleux.Destin.2001.mkv", "Amélie", 2001) == "unresolved"
    assert status("/m/Le.Fabuleux.Destin.2001.mkv", "Amélie", 2001,
                  movie_alt_titles=["Le Fabuleux Destin"]) == "probable"
    assert status("/m/Amelie {tmdb-999}.mkv", "Amélie", 2001,
                  series_ids_expected={"tmdb_id": 194}) == "uncertain"
