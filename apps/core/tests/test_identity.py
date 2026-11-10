"""Episode Identity Engine (E1) + regressiefixtures (L2: 'S02E03 is eigenlijk S01E03')."""

from __future__ import annotations

import httpx
import pytest

from mediaguard.identity import adapters, service
from mediaguard.identity.engine import EpisodeRef, Observation, refs_match, resolve
from mediaguard.identity.parser import parse_release


@pytest.mark.parametrize("name,expect", [
    ("Show.Name.S01E03.1080p.WEB-DL.DDP5.1.H.264-GROUP.mkv",
     {"season": 1, "episodes": [3], "release_group": "GROUP", "resolution": 1080,
      "source": "webdl", "video_codec": "h264", "confidence": "high"}),
    ("Show S01E01E02 720p HDTV x264-LOL", {"season": 1, "episodes": [1, 2]}),
    ("Show.S01E01-E03.2160p.UHD.BluRay.REMUX.DV.HDR10.HEVC-MGTEST",
     {"episodes": [1, 2, 3], "source": "remux", "hdr": ["DV", "HDR10"]}),
    ("[SubsPlease] Vloekschrift - 05 (1080p) [ABCDEF12].mkv",
     {"absolute_episodes": [5], "release_group": "SubsPlease", "crc32": "ABCDEF12",
      "season": None}),
    ("[Group] Golfbreker - 1071 [1080p][Dual Audio]",
     {"absolute_episodes": [1071], "dual_audio": True}),
    ("Show - 05v2 [720p]", {"absolute_episodes": [5], "version": 2}),
    ("Show 1x03-04 HDTV", {"season": 1, "episodes": [3, 4]}),
    ("[Erai-raws] Show OVA 2 [1080p]", {"special": True, "special_kind": "OVA",
                                       "season": 0, "episodes": [2]}),
    ("Show.S00E05.720p", {"special": True, "season": 0, "episodes": [5]}),
    ("The.Daily.Show.2024.03.15.720p.WEB", {"air_date": "2024-03-15"}),
    ("Show.Season.2.Complete.1080p", {"season": 2, "full_season": True}),
    ("Show S2 - 05 [1080p]", {"season": 2, "episodes": [5]}),
    ("Film.2021.Directors.Cut.1080p.BluRay.x264-AAA",
     {"year": 2021, "edition": "Directors Cut", "release_group": "AAA"}),
    ("1917.2019.1080p.BluRay.x264-SPARKS", {"title": "1917", "year": 2019}),
])
def test_parser_fixtures(name, expect):
    p = parse_release(name)
    for key, value in expect.items():
        assert getattr(p, key) == value, (key, getattr(p, key))


def test_parser_dutch_tags_and_claims():
    p = parse_release("Kinderserie.S01E02.NL.Gesproken.1080p.WEB-DL-XYZ")
    assert p.language_claims == {"nl": "audio"} and p.audio_language_claims == ["nl"]
    q = parse_release("Movie.2019.2160p.UHD.BluRay.x265.HDR10Plus.NL.Subs-GRP")
    assert q.subtitle_language_claims == ["nl"] and "nl" not in q.audio_language_claims
    assert q.hdr == ["HDR10+"]
    d = parse_release("[Judas] Show - S02E03 [Dual Audio][Multi-Subs]")
    assert d.dual_audio and d.audio_language_claims == ["en", "ja"]


def test_parser_untrusted_input_is_bounded():
    p = parse_release("x" * 100_000 + "S01E01")
    assert len(p.raw) <= 400
    assert parse_release("").confidence == "none"
    assert parse_release("'; rm -rf / #.S01E02.mkv").episodes == [2]


def test_refs_match_absolute_and_scene():
    assert refs_match([EpisodeRef(1, 5, 5)], [EpisodeRef(absolute=5)]) is True
    assert refs_match([EpisodeRef(1, 5)], [EpisodeRef(absolute=5)]) is None
    assert refs_match([EpisodeRef(1, 1), EpisodeRef(1, 2)],
                      [EpisodeRef(1, 1), EpisodeRef(1, 2)]) is True
    assert refs_match([EpisodeRef(1, 1), EpisodeRef(1, 2)], [EpisodeRef(1, 1)]) is False


# ── regressie: 'bestand zegt S02E03, maar is eigenlijk S01E03' ──────────────
EXPECTED_S02E03 = [EpisodeRef(2, 3)]
FILE = "/media/tv/Show/Season 02/Show - S02E03 - Titel.mkv"


def test_s02e03_really_s01e03_detected_with_hash_evidence():
    obs = Observation(path=FILE, hash_episodes=[EpisodeRef(1, 3)])
    r = resolve(EXPECTED_S02E03, obs)
    assert r.status == "mismatch" and r.actionable
    assert r.actual == [EpisodeRef(1, 3)]


def test_s02e03_really_s01e03_detected_with_download_history():
    obs = Observation(path=FILE, grabbed_episodes=[EpisodeRef(1, 3)],
                      grabbed_release_title="Show.S01E03.1080p.WEB-DL-GRP")
    r = resolve(EXPECTED_S02E03, obs)
    assert r.status == "mismatch" and r.actionable


def test_s02e03_weak_evidence_never_mismatch():
    # onbekende hash + afwijkende speelduur + naam die 'S01E03' zegt: hooguit onzeker
    obs = Observation(path=FILE, release_title="Show.S01E03.720p", hash_checked=True,
                      duration_seconds=1300, expected_runtime_minutes=45,
                      mapped_episodes=[EpisodeRef(1, 3)])
    r = resolve(EXPECTED_S02E03, obs)
    assert r.status == "uncertain"
    assert not r.actionable


def test_unknown_hash_is_not_mismatch():
    r = resolve(EXPECTED_S02E03, Observation(path=FILE, hash_checked=True))
    assert r.status == "probable"
    assert any(e.source == "hash_unknown" and e.relation == "neutral" for e in r.evidence)


def test_conflicting_strong_evidence_is_uncertain():
    obs = Observation(path=FILE, hash_episodes=[EpisodeRef(1, 3)],
                      grabbed_episodes=[EpisodeRef(2, 3)])
    r = resolve(EXPECTED_S02E03, obs)
    assert r.status == "uncertain" and not r.actionable


def test_verified_and_probable():
    assert resolve(EXPECTED_S02E03, Observation(
        path=FILE, grabbed_episodes=[EpisodeRef(2, 3)])).status == "verified"
    r = resolve(EXPECTED_S02E03, Observation(path="Show.S01E03.mkv",
                                             grabbed_episodes=[EpisodeRef(2, 3)]))
    assert r.status == "probable"  # sterk bewijs wint, zwakke afwijking genoteerd
    assert resolve([EpisodeRef(1, 1)], Observation(fork_linked=True)).status == "unresolved"


def test_movie_series_id_mismatch():
    obs = Observation(is_movie=True, series_ids_expected={"tmdb_id": 1},
                      series_ids_observed={"tmdb_id": 2})
    r = resolve([], obs)
    assert r.status == "uncertain"  # serie-ID's 60 < drempel 80: geen automatische actie


def test_service_uses_download_history_and_mapping(ctx):
    ctx.db.execute(
        "INSERT INTO release_provenance(instance_id,event_type,download_id,release_title,"
        "episodes_json,idempotency_key,received_at) VALUES ('sonarr-anime','Grab','DL1',"
        "'[G] Show - 15','[{\"season_number\":2,\"episode_number\":3,"
        "\"absolute_episode_number\":15}]','k1','2026-01-01')")
    adapters.register_manual_mapping(ctx, "tvdb:42", 15, 2, 3)
    r = service.evaluate(ctx, instance_id="sonarr-anime", kind="episode_file", provider_id=7,
                         expected=[EpisodeRef(2, 3, 15)], path="[G] Show - 15 [1080p].mkv",
                         download_id="DL1", series={"id": 1, "tvdb_id": 42})
    assert r.status == "verified"
    sources = {e.source for e in r.evidence}
    assert {"download_history", "anime_mapping", "filename"} <= sources
    assert service.latest(ctx, "sonarr-anime", "episode_file", 7)["status"] == "verified"


def test_known_hash_remembered_after_verified(ctx):
    ctx.db.execute(
        "INSERT INTO release_provenance(instance_id,event_type,download_id,episodes_json,"
        "idempotency_key,received_at) VALUES ('s','Grab','D','[{\"season_number\":1,"
        "\"episode_number\":3}]','k','2026-01-01')")
    service.evaluate(ctx, instance_id="s", kind="episode_file", provider_id=1,
                     expected=[EpisodeRef(1, 3)], download_id="D", hashes={"sha256": "AB"})
    # dezelfde inhoud verschijnt later gekoppeld als S02E03 → sterk bewijs van mismatch
    r = service.evaluate(ctx, instance_id="s", kind="episode_file", provider_id=2,
                         expected=[EpisodeRef(2, 3)], path="Show - S02E03.mkv",
                         hashes={"sha256": "ab"})
    assert r.status == "mismatch" and r.actual == [EpisodeRef(1, 3)]


def test_identity_api_verify_evidence_decision(client):
    r = client.post("/api/v1/identity/sonarr-series/episode_file/55/verify", json={
        "expected": [{"season": 2, "episode": 3}], "path": FILE,
        "duration_seconds": 1300, "expected_runtime_minutes": 45})
    assert r.status_code == 200, r.text
    assert r.json()["status"] == "probable"
    ev = client.get("/api/v1/identity/sonarr-series/episode_file/55/evidence").json()
    assert ev["status"] == "probable" and ev["current"]["evidence"]
    d = client.post("/api/v1/identity/sonarr-series/episode_file/55/decision", json={
        "status": "mismatch", "actual": [{"season": 1, "episode": 3}],
        "expected": [{"season": 2, "episode": 3}], "note": "bekeken"})
    assert d.json()["status"] == "mismatch" and d.json()["actionable"]
    # volgende automatische controle neemt het handmatige oordeel mee
    again = client.post("/api/v1/identity/sonarr-series/episode_file/55/verify", json={
        "expected": [{"season": 2, "episode": 3}], "path": FILE}).json()
    assert again["status"] == "mismatch"
    audit = client.get("/api/v1/audit?action=identity").json()
    assert {a["action"] for a in audit} >= {"identity.verify", "identity.decision"}
    assert client.post("/api/v1/identity/parse", json={"name": "Show.S01E02.mkv"}).json()[
        "episodes"] == [2]


async def test_thexem_adapter_with_fake_server(ctx):
    def handler(request: httpx.Request) -> httpx.Response:
        assert request.url.path == "/map/all"
        assert request.url.params["id"] == "81189"
        return httpx.Response(200, json={"result": "success", "data": [
            {"scene": {"season": 1, "episode": 15, "absolute": 15},
             "tvdb": {"season": 2, "episode": 3, "absolute": 15}}]})

    n = await adapters.fetch_thexem(ctx.db, 81189, transport=httpx.MockTransport(handler))
    assert n == 1
    assert adapters.map_refs(ctx.db, "tvdb:81189", [EpisodeRef(absolute=15)]) == [
        EpisodeRef(2, 3, 15)]
    assert adapters.map_refs(ctx.db, "tvdb:81189", [EpisodeRef(1, 15)]) == [
        EpisodeRef(2, 3, 15)]
    assert adapters.map_refs(ctx.db, "tvdb:81189", [EpisodeRef(absolute=99)]) is None


def test_season_pack_grab_supports_each_episode_not_mismatch():
    """Praktijk: 'Shameless.US.S05.1080p.AMZN.WEB-DL…' gegrabd voor S05E01–E12; bij de
    import van S05E01 gaf MediaGuard IDENTITY_MISMATCH en bleef alles 'wacht op import'."""
    pack = [EpisodeRef(5, n) for n in range(1, 13)]
    obs = Observation(path="/data/downloads/tv/Shameless.US.S05.1080p/Shameless.US.S05E01.mkv",
                      release_title="Shameless.US.S05.1080p.AMZN.WEB-DL.DD+5.1.H.264-playWEB",
                      grabbed_episodes=pack,
                      grabbed_release_title="Shameless.US.S05.1080p.AMZN.WEB-DL.DD+5.1.H.264-"
                                            "playWEB")
    res = resolve([EpisodeRef(5, 1)], obs)
    assert res.status != "mismatch"
    assert all(e.relation != "contradicts" for e in res.evidence)
    # aflevering die NIET in het pakket zit blijft wél tegenbewijs
    other = resolve([EpisodeRef(6, 1)], obs)
    assert any(e.source == "download_history" and e.relation == "contradicts"
               for e in other.evidence)
    # multi-episode releasenaam (S05E01-E03) ondersteunt E02
    multi = resolve([EpisodeRef(5, 2)], Observation(
        path="/x/Shameless.US.S05E02.mkv", release_title="Shameless.US.S05E01-E03.1080p"))
    assert all(e.relation != "contradicts" for e in multi.evidence)
