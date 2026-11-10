"""Root Cause Engine: elke oorzaakcode met concreet bewijs + wanted-API en acties."""

from __future__ import annotations

from collections.abc import Callable

import pytest
from test_arr_fakes import (
    FakeArr,
    b1ctx,  # noqa: F401
    download_client,
    indexer,
    iso,
    make_episode,
    make_movie,
    make_release,
    make_series,
    wire,
)

from mediaguard.library.sync import sync_instance
from mediaguard.wanted.diagnosis import classify_rejection, diagnose
from mediaguard.wanted.engine import apply_action


def healthy_sonarr() -> FakeArr:
    fake = FakeArr("sonarr")
    fake.add_indexer(indexer(1, "NZBgeek"))
    fake.providers["downloadclient"][1] = download_client(1)
    fake.add_series(make_series(1, "Severance", 371980, series_type="standard"),
                    [make_episode(10, 1, 1, 1, has_file=True, file_id=1),
                     make_episode(11, 1, 1, 2, aired_days=-10)])
    return fake


async def _setup(ctx, fake: FakeArr) -> int:  # type: ignore[no-untyped-def]
    wire(ctx, "sonarr-series", fake)
    await sync_instance(ctx, "sonarr-series")
    row = ctx.db.fetch_one("SELECT id FROM wanted_items WHERE provider_id=11")
    return int(row["id"])


def _ep(fake: FakeArr) -> dict:
    return fake.episodes[11]


SCENARIOS: dict[str, Callable[[FakeArr, object, int], None]] = {
    "unmonitored": lambda f, c, w: _ep(f).update(monitored=False),
    "not-yet-aired": lambda f, c, w: _ep(f).update(airDateUtc=iso(3)),
    "intentional-exception": lambda f, c, w: apply_action(c, w, "ignore", "bewust"),
    "instance-suspended": lambda f, c, w: c.db.execute(
        "UPDATE instances SET suspended=1 WHERE id='sonarr-series'"),
    "root-folder-missing": lambda f, c, w: setattr(f, "root_folders", [
        {"id": 1, "path": "/tv/", "accessible": False}]),
    "file-not-linked": lambda f, c, w: setattr(f, "manual_import", [
        {"path": "/tv/Severance/S01E02.mkv", "episodes": [{"id": 11}], "rejections": [],
         "quality": {"quality": {"name": "WEBDL-1080p"}}}]),
    "import-failed": lambda f, c, w: f.queue.append({
        "id": 5, "episodeId": 11, "seriesId": 1, "title": "Severance.S01E02", "status":
        "completed", "trackedDownloadStatus": "warning", "trackedDownloadState": "importBlocked",
        "statusMessages": [{"title": "x.mkv", "messages": ["Not a Custom Format upgrade"]}],
        "size": 10, "sizeleft": 0, "downloadClient": "SABnzbd"}),
    "download-client-failed": lambda f, c, w: f.grab_event(
        episode_id=11, event="downloadFailed", data={"message": "Disk full"}),
    "stuck-queue": lambda f, c, w: f.queue.append({
        "id": 6, "episodeId": 11, "seriesId": 1, "title": "x", "status": "warning",
        "trackedDownloadStatus": "warning", "trackedDownloadState": "downloading",
        "statusMessages": [{"title": "x", "messages": ["The download is stalled"]}],
        "size": 10, "sizeleft": 10}),
    "downloading": lambda f, c, w: f.queue.append({
        "id": 7, "episodeId": 11, "seriesId": 1, "title": "x", "status": "downloading",
        "trackedDownloadStatus": "ok", "trackedDownloadState": "downloading", "size": 10,
        "sizeleft": 5, "timeleft": "00:10:00", "added": iso(-0.1)}),
    "download-client-missing": lambda f, c, w: f.providers["downloadclient"].clear(),
    "no-indexers": lambda f, c, w: f.providers["indexer"][1].update(
        enableAutomaticSearch=False, enableInteractiveSearch=False),
    "indexer-auth": lambda f, c, w: (
        f.indexer_status.append({"id": 1, "indexerId": 1, "disabledTill": iso(1)}),
        f.health.append({"source": "IndexerStatusCheck", "type": "error", "message":
                         "Indexers unavailable due to failures: NZBgeek (HTTP 401 "
                         "Unauthorized - invalid API key)"})),
    "indexer-rate-limit": lambda f, c, w: (
        f.indexer_status.append({"id": 1, "indexerId": 1, "disabledTill": iso(1)}),
        f.health.append({"source": "IndexerStatusCheck", "type": "warning", "message":
                         "NZBgeek: API request limit reached (429 Too Many Requests)"})),
    "indexer-offline": lambda f, c, w: f.indexer_status.append(
        {"id": 1, "indexerId": 1, "disabledTill": iso(0.5)}),
}

INTERACTIVE: dict[str, list[dict]] = {
    "incorrect-mapping": [make_release("Severance.S01E02", rejections=["Unknown Series"])],
    "rejected-quality": [make_release("Sev.S01E02.480p", rejections=[
        "SDTV is not wanted in profile"])],
    "rejected-language": [make_release("Sev.S01E02.GERMAN", rejections=[
        "Language German is not wanted in profile"])],
    "rejected-custom-format": [make_release("Sev.S01E02.x265", rejections=[
        "Custom Formats x265 (HD) have score -10000 below Series's minimum 0"])],
    "rejected-size": [make_release("Sev.S01E02.SAMPLE", rejections=[
        "50.0 MB is smaller than minimum allowed 300.0 MB"])],
    "no-indexer-hit": [],
    "release-available": [make_release("Severance.S01E02.1080p.WEB-DL")],
}


@pytest.mark.parametrize("code", sorted(SCENARIOS))
async def test_diagnosis_live_reason(b1ctx, code):  # noqa: F811
    fake = healthy_sonarr()
    wid = await _setup(b1ctx, fake)
    SCENARIOS[code](fake, b1ctx, wid)
    result = await diagnose(b1ctx, wid)
    assert result["primary"]["code"] == code, result["reasons"]
    primary = result["primary"]
    assert primary["rule"] and primary["ts"] and primary["suggested_fix_nl"]
    assert primary["message_nl"] and primary["label_nl"]
    stored = b1ctx.db.fetch_one("SELECT last_reason_code FROM wanted_items WHERE id=?", (wid,))
    assert stored["last_reason_code"] == code


@pytest.mark.parametrize("code", sorted(INTERACTIVE))
async def test_diagnosis_interactive_rejections(b1ctx, code):  # noqa: F811
    fake = healthy_sonarr()
    wid = await _setup(b1ctx, fake)
    fake.releases[("episode", 11)] = INTERACTIVE[code]
    result = await diagnose(b1ctx, wid, interactive=True)
    assert result["primary"]["code"] == code, result["reasons"]
    assert result["evidence"]["candidates_source"] == "interactive"
    # kandidaten + afwijzingen zijn bewaard en tellen mee in het quotum
    att = b1ctx.db.fetch_one("SELECT * FROM backfill_attempts WHERE wanted_id=?", (wid,))
    assert att["status"] == "candidates" and att["counts_quota"] == 1
    # zonder interactief zoeken wordt het opgeslagen bewijs hergebruikt
    again = await diagnose(b1ctx, wid)
    assert again["primary"]["code"] == code
    assert again["evidence"]["candidates_source"].startswith("opgeslagen")


async def test_not_searched_yet_and_evidence(b1ctx):  # noqa: F811
    fake = healthy_sonarr()
    wid = await _setup(b1ctx, fake)
    result = await diagnose(b1ctx, wid)
    assert result["primary"]["code"] == "not-searched-yet"
    ev = result["evidence"]
    assert ev["indexers"][0]["name"] == "NZBgeek" and "fields" not in ev["indexers"][0]
    assert ev["episode"]["season_number"] == 1 and ev["errors"] == []


async def test_unverified_numbering_is_contributing(b1ctx):  # noqa: F811
    fake = healthy_sonarr()
    wid = await _setup(b1ctx, fake)
    _ep(fake)["unverifiedSceneNumbering"] = True
    codes = [r["code"] for r in (await diagnose(b1ctx, wid))["reasons"]]
    assert "incorrect-mapping" in codes


async def test_movie_diagnosis(b1ctx):  # noqa: F811
    fake = wire(b1ctx, "radarr-films", FakeArr("radarr", api_key="films-key-999"))
    fake.add_indexer(indexer(1, "NZBgeek"))
    fake.providers["downloadclient"][1] = download_client(1)
    fake.movies[1] = make_movie(1, "Oppenheimer", 872585)
    await sync_instance(b1ctx, "radarr-films")
    wid = int(b1ctx.db.fetch_one("SELECT id FROM wanted_items WHERE instance_id='radarr-films'"
                                 )["id"])
    fake.grab_event(movie_id=1, event="grabbed", days=-2, title="Oppenheimer.2023.1080p")
    result = await diagnose(b1ctx, wid)
    assert result["primary"]["code"] == "import-failed"
    assert "Oppenheimer.2023.1080p" in result["primary"]["message_nl"]
    fake.movies[1]["isAvailable"] = False
    assert (await diagnose(b1ctx, wid))["primary"]["code"] == "not-yet-aired"


@pytest.mark.parametrize("text,code", [
    ("WEBDL-720p is not wanted in profile", "rejected-quality"),
    ("Existing file on disk is of equal or higher preference: Bluray-1080p", "rejected-quality"),
    ("Language Japanese is not wanted in profile", "rejected-language"),
    ("English is wanted, but found Japanese", "rejected-language"),
    ("Custom Formats DV (WEBDL) have score -10000 below Series's minimum 0",
     "rejected-custom-format"),
    ("1.2 GB is larger than maximum allowed 1 GB", "rejected-size"),
    ("Unknown Series", "incorrect-mapping"),
    ("Episode wasn't requested: 2x05", "incorrect-mapping"),
    ("Release is blocklisted", "rejected-other"),
    ("Older than configured retention", "rejected-other"),
    ("Not enough seeders: 0. Minimum seeders: 1", "rejected-other"),
    ("MediaGuard: alleen Engelse audio [AUDIO_PREFERENCE]", "rejected-other"),
    ("Movie Oppenheimer does not have a minimum availability", "not-yet-aired"),
    ("Release in queue already meets cutoff: WEBDL-1080p", "rejected-other"),
])
def test_classify_rejection(text, code):
    assert classify_rejection(text) == code


def test_wanted_api_filters_actions_and_diagnosis(client, ctx):
    fake = wire(ctx, "sonarr-series", healthy_sonarr())
    anime = wire(ctx, "sonarr-anime", FakeArr("sonarr", api_key="anime-key-999"))
    anime.add_series(make_series(4, "Frieren", 424536, series_type="anime"),
                     [make_episode(40, 4, 1, 1, aired_days=-100, absolute=1),
                      make_episode(41, 4, 1, 2, aired_days=-1, monitored=False, absolute=2)])
    for iid in ("sonarr-series", "sonarr-anime"):
        client.post(f"/api/v1/instances/{iid}/sync?mode=inline")
    data = client.get("/api/v1/wanted").json()
    assert data["total"] == 2  # ongemonitorde anime-aflevering telt niet
    assert client.get("/api/v1/wanted?media_type=anime").json()["total"] == 1
    assert client.get("/api/v1/wanted?q=Severance&season=1").json()["total"] == 1
    assert client.get("/api/v1/wanted?never_searched=true").json()["total"] == 2
    wid = client.get("/api/v1/wanted?instance_id=sonarr-series").json()["items"][0]["id"]
    diag = client.get(f"/api/v1/wanted/{wid}/diagnosis").json()
    assert diag["primary"]["code"] == "not-searched-yet"
    fake.releases[("episode", 11)] = INTERACTIVE["rejected-quality"]
    diag = client.get(f"/api/v1/wanted/{wid}/diagnosis?interactive=true").json()
    assert diag["primary"]["code"] == "rejected-quality"
    cached = client.get(f"/api/v1/wanted/{wid}/diagnosis?cached=true").json()
    assert cached["primary_code"] == "rejected-quality"
    assert client.get("/api/v1/wanted?reason_code=rejected-quality").json()["total"] == 1
    r = client.post(f"/api/v1/wanted/{wid}/ignore", json={"reason": "bestaat niet in NL"})
    assert r.json()["status"] == "ignored" and "monitorstatus" in r.json()["explanation_nl"]
    assert client.get("/api/v1/wanted").json()["total"] == 1
    assert client.get(f"/api/v1/wanted/{wid}/diagnosis").json()["primary"]["code"] \
        == "intentional-exception"
    r = client.post(f"/api/v1/wanted/{wid}/keep-monitoring")
    assert r.json()["status"] == "wanted"
    detail = client.get(f"/api/v1/wanted/{wid}").json()
    assert detail["keep_monitoring"] is True and detail["latest_diagnosis"]
    assert detail["settings_scope"]["series"] == "sonarr-series:1"
    r = client.post("/api/v1/wanted/actions", json={
        "action": "ignore", "instance_id": "sonarr-anime", "series_id": 4, "season_number": 1})
    assert r.json()["updated"] == 1
    kp = client.get("/api/v1/wanted/kpis").json()
    assert kp["ignored"] == 1 and kp["missing"] == 1
    assert kp["reason_distribution"] == {"intentional-exception": 1}
    assert set(kp["per_instance"]) == {"sonarr-series", "sonarr-anime", "radarr-films",
                                       "radarr-animefilms"}
    assert "rejected-quality" in client.get("/api/v1/wanted/reasons").json()
    assert client.get("/api/v1/wanted/999999").status_code == 404
    audit = client.get("/api/v1/audit?action=wanted.").json()
    assert {a["action"] for a in audit} >= {"wanted.ignore", "wanted.keep_monitoring"}
    assert all(a["instance_id"] for a in audit)


async def test_unreleased_movies_are_upcoming_and_kpis_split(b1ctx, client):  # noqa: F811
    """Rapport 4 nov (H6/H7): Wadi 3 (2026, nog niet digitaal/fysiek uit) is 'verwacht',
    niet ontbrekend; ontbrekend en onder kwaliteitsgrens apart; 'Nog niet onderzocht' is
    filterbaar en wordt automatisch gediagnosticeerd."""
    from mediaguard.wanted import api as wanted_api
    from mediaguard.wanted.engine import list_wanted, wanted_kpis

    fake = wire(b1ctx, "radarr-films", FakeArr("radarr", api_key="films-key-999"))
    fake.add_indexer(indexer(1, "NZBgeek"))
    fake.providers["downloadclient"][1] = download_client(1)
    fake.movies[1] = make_movie(1, "Oppenheimer", 872585)
    wadi = make_movie(2, "Wadi Deel Drie", 1170608, digital_days=200)
    wadi["physicalRelease"] = None
    fake.movies[2] = wadi  # Radarr 'announced' → isAvailable True, maar nog niet uit
    fake.movies[3] = make_movie(3, "Upgrade", 3, has_file=True, file_cutoff=True)
    await sync_instance(b1ctx, "radarr-films")
    titles = {r["title"] for r in b1ctx.db.fetch_dicts(
        "SELECT title FROM wanted_items WHERE instance_id='radarr-films'")}
    assert not any("Wadi" in t for t in titles)
    k = wanted_kpis(b1ctx, "radarr-films")
    assert k["upcoming"]["movies"] == 1
    assert k["reason_distribution"] == {"niet-gediagnosticeerd": 1}
    assert k["reason_distribution_cutoff"] == {"niet-gediagnosticeerd": 1}
    assert k["reason_distribution_detail"]["missing"]["niet-gediagnosticeerd"]["movies"] == 1
    rows = list_wanted(b1ctx, instance_id="radarr-films", reason_code="niet-gediagnosticeerd")
    assert len(rows["items"] if isinstance(rows, dict) else rows) == 2
    reasons = client.get("/api/v1/wanted/reasons").json()
    assert reasons["niet-gediagnosticeerd"]["needs_user"] is False
    assert reasons["root-folder-missing"]["needs_user"] is True
    wanted_api._due_diagnose(b1ctx)
    job = b1ctx.db.fetch_one("SELECT id FROM jobs WHERE job_type='wanted.diagnose_background'")
    assert job is not None


def test_reasons_expose_needs_user_label_and_href(client):
    data = client.get("/api/v1/wanted/reasons").json()
    assert data["indexer-auth"]["needs_user"] is True
    assert data["indexer-auth"]["href"] == "/inrichten?tab=indexers"
    assert data["no-indexer-hit"]["needs_user"] is False and data["no-indexer-hit"]["automatic"]
    assert data["niet-gediagnosticeerd"]["href"].startswith("/ontbrekend?reason_code=")
    assert all({"code", "label_nl", "fix_nl", "needs_user", "href"} <= set(v) for v in
               data.values())
    k = client.get("/api/v1/wanted/kpis").json()
    assert k["needs_user"] == {"total": 0, "reasons": []} and k["automatic_total"] == 0
