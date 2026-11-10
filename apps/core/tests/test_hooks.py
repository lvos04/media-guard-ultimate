"""Fork-hooks core-kant: pre-grab, pre-import, arr-webhook (fork-hooks.md)."""

from __future__ import annotations

import base64
import time

import pytest
from test_encoding_support import FakeInstance, FakeRegistry, install

from mediaguard import jobs

ANIME_KEY = "anime-hook-key-123"
SERIES_KEY = "series-hook-key-456"


@pytest.fixture
def hooks(client, ctx):
    ctx.set_hook_key("sonarr-anime", ANIME_KEY)
    ctx.set_hook_key("sonarr-series", SERIES_KEY)
    reg = FakeRegistry(FakeInstance("sonarr-anime", "anime"), FakeInstance("sonarr-series"))
    ctx.services["instance_registry"] = reg
    return reg


def grab(title, *, instance="sonarr-anime", episodes=None, languages=None, **release):
    return {"instance_id": instance, "app": "sonarr", "search_type": "rss",
            "release": {"title": title, "size": 1_000_000_000, "languages": languages or [],
                        **release},
            "series": {"id": 12, "tvdb_id": 81189, "title": "Show",
                       "series_type": "anime" if instance == "sonarr-anime" else "standard"},
            "episodes": episodes if episodes is not None else [
                {"id": 345, "season_number": 1, "episode_number": 5,
                 "absolute_episode_number": 5}]}


def post(client, path, body, key=ANIME_KEY):
    return client.post(path, json=body, headers={"X-MediaGuard-Hook-Key": key})


def test_pre_grab_auth(anon_client, ctx):
    ctx.set_hook_key("sonarr-anime", ANIME_KEY)
    body = grab("[G] Show - 05 (1080p)")
    assert anon_client.post("/api/v1/hooks/pre-grab", json=body).status_code == 401
    r = anon_client.post("/api/v1/hooks/pre-grab", json=body,
                         headers={"X-MediaGuard-Hook-Key": "fout"})
    assert r.status_code == 401
    ctx.set_hook_key("sonarr-series", SERIES_KEY)
    r = anon_client.post("/api/v1/hooks/pre-grab", json=body,
                         headers={"X-MediaGuard-Hook-Key": SERIES_KEY})
    assert r.status_code == 403  # sleutel van andere instantie


def test_pre_grab_emergency_stop_suspended_shadow(client, ctx, hooks):
    ctx.state_set("emergency_stop", {"active": True})
    r = post(client, "/api/v1/hooks/pre-grab", grab("[G] Show - 05 (1080p)")).json()
    assert r["decision"] == "reject" and r["reason_code"] == "EMERGENCY_STOP"
    ctx.state_set("emergency_stop", {"active": False})
    hooks.instances["sonarr-anime"].suspended = True
    r = post(client, "/api/v1/hooks/pre-grab", grab("[G] Show - 05 (1080p)")).json()
    assert r["reason_code"] == "INSTANCE_SUSPENDED"
    hooks.instances["sonarr-anime"].suspended = False
    hooks.instances["sonarr-anime"].shadow_mode = True
    r = post(client, "/api/v1/hooks/pre-grab", grab("[G] Show - 05 (1080p)")).json()
    assert r["reason_code"] == "SHADOW"


def _warm_up(client) -> None:
    """Eén hookaanroep op de andere instantie vóór een tijdmeting, plus gc.collect().

    De eerste aanroep na het starten van de app laadt nog modules en vult caches (lokaal
    ±120 ms buiten de handler, op een trage CI-runner >1 s); de garbage collector kan midden in
    een meting een SQLite-verbinding van een eerdere test sluiten (WAL-checkpoint + fsync, zie
    conftest.app). Beide zijn geen hooklatentie. De grenzen blijven gelijk (ADR-0020)."""
    import gc

    r = post(client, "/api/v1/hooks/pre-grab", grab("[W] Warm - 01 (1080p)",
                                                     instance="sonarr-series"), SERIES_KEY)
    assert r.status_code == 200
    gc.collect()


def test_pre_grab_audio_preference_score_and_speed(client, hooks):
    _warm_up(client)
    started = time.perf_counter()
    dual = post(client, "/api/v1/hooks/pre-grab",
                grab("[G] Show - 05 (1080p) [Dual Audio]")).json()
    elapsed = (time.perf_counter() - started) * 1000
    ja = post(client, "/api/v1/hooks/pre-grab",
              grab("[G] Show - 05 (2160p)", languages=["Japanese"])).json()
    assert dual["decision"] == ja["decision"] == "accept"
    assert dual["score_adjustment"] > ja["score_adjustment"] == 0
    assert "blijft zoeken" in ja["message"]
    assert dual["duration_ms"] < 300 and elapsed < 1000
    decisions = client.get("/api/v1/policy/hook-decisions?instance_id=sonarr-anime").json()
    assert len(decisions) == 2


def test_pre_grab_identity_and_kids_nl_audio(client, ctx, hooks):
    body = grab("Show.S01E06.1080p.WEB-DL-GRP", instance="sonarr-series",
                episodes=[{"id": 1, "season_number": 1, "episode_number": 5}])
    r = post(client, "/api/v1/hooks/pre-grab", body, SERIES_KEY).json()
    assert r["decision"] == "reject" and r["reason_code"] == "IDENTITY_MISMATCH"
    ctx.config.set("series", "sonarr-series:12", "policy.profile_id", "kids-nl-audio", "t")
    en = grab("Show.S01E05.1080p.WEB-DL-GRP", instance="sonarr-series",
              episodes=[{"id": 1, "season_number": 1, "episode_number": 5}],
              languages=["English"])
    r = post(client, "/api/v1/hooks/pre-grab", en, SERIES_KEY).json()
    assert r["reason_code"] == "AUDIO_REQUIREMENT"
    nl = grab("Show.S01E05.NL.Gesproken.1080p.WEB-DL-GRP", instance="sonarr-series",
              episodes=[{"id": 1, "season_number": 1, "episode_number": 5}])
    assert post(client, "/api/v1/hooks/pre-grab", nl, SERIES_KEY).json()["decision"] == "accept"


def webhook_grab(download_id="HASH1", title="[G] Show - 05 [Dual Audio]"):
    return {"eventType": "Grab", "series": {"id": 12, "title": "Show", "tvdbId": 81189,
                                            "type": "anime"},
            "episodes": [{"id": 345, "episodeNumber": 5, "seasonNumber": 1,
                          "absoluteEpisodeNumber": 5}],
            "release": {"quality": "WEBDL-1080p", "releaseGroup": "G", "releaseTitle": title,
                        "indexer": "Nyaa", "size": 123, "customFormatScore": 1500,
                        "customFormats": ["Dual Audio"]},
            "downloadClient": "qBittorrent", "downloadId": download_id}


def webhook_download(download_id="HASH1", upgrade=False, audio=("jpn",)):
    return {"eventType": "Download", "isUpgrade": upgrade,
            "series": {"id": 12, "title": "Show", "tvdbId": 81189, "type": "anime"},
            "episodes": [{"id": 345, "episodeNumber": 5, "seasonNumber": 1,
                          "absoluteEpisodeNumber": 5}],
            "episodeFile": {"id": 991, "relativePath": "Season 01/Show - S01E05.mkv",
                            "path": "/media/anime/Show/Season 01/Show - S01E05.mkv",
                            "sceneName": "[G] Show - 05 [Dual Audio]", "size": 123,
                            "mediaInfo": {"audioLanguages": list(audio),
                                          "subtitles": ["eng"]}},
            "release": {"releaseTitle": "[G] Show - 05 [Dual Audio]"},
            "downloadClient": "qBittorrent", "downloadId": download_id}


def basic(user, password):
    return {"Authorization": "Basic " + base64.b64encode(f"{user}:{password}".encode()).decode()}


async def test_webhook_basic_auth_provenance_idempotent_and_analysis(client, ctx, hooks,
                                                                     tmp_path):
    fake = install(ctx, tmp_path / "staging")
    fake.unavailable = True  # analyse valt terug op arr-mediaInfo
    url = "/api/v1/hooks/arr-webhook/sonarr-anime"
    assert client.post(url, json=webhook_grab(),
                       headers=basic("sonarr-anime", "fout")).status_code == 401
    assert client.post(url, json=webhook_grab(),
                       headers=basic("sonarr-series", ANIME_KEY)).status_code == 401
    r = client.post(url, json=webhook_grab(), headers=basic("sonarr-anime", ANIME_KEY))
    assert r.status_code == 200 and r.json()["status"] == "stored"
    dup = client.post(url, json=webhook_grab(), headers=basic("sonarr-anime", ANIME_KEY))
    assert dup.json()["status"] == "duplicate"
    prov = ctx.db.fetch_one("SELECT * FROM release_provenance WHERE event_type='Grab'")
    assert prov["release_group"] == "G" and prov["custom_format_score"] == 1500
    assert prov["download_id"] == "HASH1" and prov["instance_id"] == "sonarr-anime"

    imp = client.post(url, json=webhook_download(),
                      headers={"X-MediaGuard-Hook-Key": ANIME_KEY}).json()
    assert imp["job_id"]
    replay = client.post(url, json=webhook_download(),
                         headers={"X-MediaGuard-Hook-Key": ANIME_KEY}).json()
    assert replay["status"] == "duplicate" and replay["job_id"] == imp["job_id"]
    count = ctx.db.fetch_one("SELECT COUNT(*) AS n FROM jobs WHERE job_type="
                             "'analysis.post_import'")
    assert count["n"] == 1

    await jobs.JobRunner(ctx).run_until_idle()
    row = ctx.db.fetch_one("SELECT * FROM post_import_analysis")
    assert row["identity_status"] == "verified"  # download-ID-historie bevestigt
    assert row["upgrade_wanted"] == 1  # alleen Japans → blijven zoeken
    failure = ctx.db.fetch_one("SELECT * FROM audio_claim_failures")
    assert failure is not None and failure["claim"] == "dual_audio"
    # volgende keer weigert pre-grab dezelfde onjuiste 'Dual Audio'-release
    r = post(client, "/api/v1/hooks/pre-grab", grab("[G] Show - 05 [Dual Audio]")).json()
    assert r["decision"] == "reject" and r["reason_code"] == "FALSE_DUAL_AUDIO_CLAIM"


def test_pre_import_mismatch_uncertain_and_defer(client, ctx, hooks):
    url = "/api/v1/hooks/arr-webhook/sonarr-series"
    grabbed = webhook_grab("DL-S1E3", "Show.S01E03.1080p.WEB-DL-GRP")
    grabbed["episodes"] = [{"id": 3, "seasonNumber": 1, "episodeNumber": 3}]
    client.post(url, json=grabbed, headers={"X-MediaGuard-Hook-Key": SERIES_KEY})
    body = {"instance_id": "sonarr-series", "app": "sonarr",
            "path": "/downloads/Show.S02E03.mkv", "download_id": "DL-S1E3",
            "release_title": "Show.S01E03.1080p.WEB-DL-GRP",
            "series": {"id": 12, "tvdb_id": 81189},
            "episodes": [{"id": 23, "season_number": 2, "episode_number": 3}]}
    r = post(client, "/api/v1/hooks/pre-import", body, SERIES_KEY).json()
    assert r["decision"] == "reject" and r["reason_code"] == "IDENTITY_MISMATCH"
    # zwak bewijs (alleen naam): accepteren, of uitstellen als dat is ingesteld
    weak = {**body, "download_id": None, "path": "/downloads/Show.S01E03.mkv"}
    assert post(client, "/api/v1/hooks/pre-import", weak, SERIES_KEY).json()[
        "decision"] == "accept"
    ctx.config.set("instance", "sonarr-series", "hooks.preimport_defer_on_uncertain", True, "t")
    r = post(client, "/api/v1/hooks/pre-import", weak, SERIES_KEY).json()
    assert r["decision"] == "defer" and r["reason_code"] == "IDENTITY_UNCERTAIN"
    stored = ctx.db.fetch_one("SELECT COUNT(*) AS n FROM identity_results WHERE "
                              "source='pre_import'")
    assert stored["n"] == 3


def test_pre_import_movie_and_audio_enforcement(client, ctx, hooks):
    ctx.set_hook_key("radarr-films", "films-key")
    hooks.instances["radarr-films"] = FakeInstance("radarr-films", "movie", app="radarr")
    client.post("/api/v1/hooks/arr-webhook/radarr-films", headers={
        "X-MediaGuard-Hook-Key": "films-key"}, json={
        "eventType": "Grab", "movie": {"id": 5, "tmdbId": 1}, "downloadId": "M1",
        "release": {"releaseTitle": "Film.2020.1080p.BluRay-G"}})
    body = {"instance_id": "radarr-films", "app": "radarr", "path": "/dl/Film.mkv",
            "download_id": "M1", "movie": {"id": 6}, "media_info": {
                "audio_languages": ["eng"]}}
    r = post(client, "/api/v1/hooks/pre-import", body, "films-key").json()
    assert r["reason_code"] == "IDENTITY_MISMATCH"
    body["movie"] = {"id": 5}
    ctx.config.set("instance", "radarr-films", "audio.nl_audio_mode", "required", "t")
    assert post(client, "/api/v1/hooks/pre-import", body, "films-key").json()[
        "decision"] == "accept"  # afdwingen staat standaard uit
    ctx.config.set("instance", "radarr-films", "hooks.preimport_enforce_audio", True, "t")
    r = post(client, "/api/v1/hooks/pre-import", body, "films-key").json()
    assert r["decision"] == "reject" and r["reason_code"] == "AUDIO_REQUIREMENT"


def test_webhook_test_event_and_other_events(client, ctx, hooks):
    url = "/api/v1/hooks/arr-webhook/sonarr-series"
    h = {"X-MediaGuard-Hook-Key": SERIES_KEY}
    assert client.post(url, json={"eventType": "Test"}, headers=h).json()["status"] == "ok"
    health = {"eventType": "Health", "message": "Indexer down", "level": "warning"}
    assert client.post(url, json=health, headers=h).json()["status"] == "stored"
    assert client.post(url, json=health, headers=h).json()["status"] == "duplicate"
    upgrade = webhook_download("UP1", upgrade=True)
    r = client.post(url, json=upgrade, headers=h).json()
    assert r["event_type"] == "Upgrade" and r["job_id"]
    assert client.post(url, content=b"[1,2]", headers=h).status_code == 422


def test_pre_import_block_is_remembered_per_download(client, ctx, hooks):
    """Sonarr vraagt elke cyclus opnieuw: één beoordeling + één logregel per download,
    tot een handmatig oordeel of de bewaartijd verloopt."""
    from mediaguard.hooks import service

    url = "/api/v1/hooks/arr-webhook/sonarr-series"
    grabbed = webhook_grab("DL-C1", "Show.S01E03.1080p.WEB-DL-GRP")
    grabbed["episodes"] = [{"id": 3, "seasonNumber": 1, "episodeNumber": 3}]
    client.post(url, json=grabbed, headers={"X-MediaGuard-Hook-Key": SERIES_KEY})
    body = {"instance_id": "sonarr-series", "app": "sonarr",
            "path": "/downloads/Show.S02E03.mkv", "download_id": "DL-C1",
            "release_title": "Show.S01E03.1080p.WEB-DL-GRP",
            "series": {"id": 12, "tvdb_id": 81189},
            "episodes": [{"id": 23, "season_number": 2, "episode_number": 3}]}

    def count() -> int:
        service.flush_logs()
        return ctx.db.fetch_one("SELECT COUNT(*) AS n FROM hook_decisions WHERE "
                                "hook='pre_import' AND download_id='DL-C1'")["n"]

    first = post(client, "/api/v1/hooks/pre-import", body, SERIES_KEY).json()
    assert first["decision"] == "reject" and "cached" not in first
    for _ in range(5):
        again = post(client, "/api/v1/hooks/pre-import", body, SERIES_KEY).json()
        assert again["decision"] == "reject" and again["cached"] is True
    assert count() == 1
    # handmatig oordeel 'klopt toch' → opnieuw beoordelen
    ctx.db.execute("INSERT INTO identity_results(instance_id,kind,provider_id,status,"
                   "confidence,actionable,expected_json,actual_json,evidence_json,reason_nl,"
                   "source,created_at) VALUES ('sonarr-series','episode_file','23','verified',"
                   "1,0,'[]','[]','[]','handmatig','user','x')")
    after = post(client, "/api/v1/hooks/pre-import", body, SERIES_KEY).json()
    assert "cached" not in after and count() == 2
    # uitgezet: elke keer opnieuw
    ctx.config.set("global", "", "hooks.preimport_cache_minutes", 0, "t")
    service.clear_decision_cache()
    post(client, "/api/v1/hooks/pre-import", body, SERIES_KEY)
    post(client, "/api/v1/hooks/pre-import", body, SERIES_KEY)
    assert count() == 4


def test_pre_grab_answers_while_database_is_busy(client, ctx, hooks):
    """Een lange schrijftransactie elders (sync/optimalisatie) mag het hookantwoord niet
    over de 3 s-time-out van de fork duwen; de logregel volgt later."""
    import threading
    import time

    from mediaguard.hooks import service

    _warm_up(client)
    holding = threading.Event()
    release = threading.Event()

    def long_transaction() -> None:
        with ctx.db.transaction() as conn:
            conn.execute("UPDATE settings_values SET updated_at=updated_at")
            holding.set()
            release.wait(10)

    t = threading.Thread(target=long_transaction)
    t.start()
    assert holding.wait(5)
    try:
        started = time.monotonic()
        r = post(client, "/api/v1/hooks/pre-grab", grab("[G] Show - 05 (1080p)"))
        elapsed = time.monotonic() - started
        assert r.status_code == 200 and r.json()["decision"] in ("accept", "reject")
        assert elapsed < 1.5, f"hook wachtte {elapsed:.2f}s op de database"
    finally:
        release.set()
        t.join(10)
    service.flush_logs()
    assert ctx.db.fetch_one("SELECT COUNT(*) AS n FROM hook_decisions WHERE "
                            "hook='pre_grab'")["n"] >= 1
