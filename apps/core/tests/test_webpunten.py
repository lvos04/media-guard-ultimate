"""Verbeterlijst 25, 26 en 39: 'Niet meer zoeken' per titel, 'Nu zoeken' en de link naar de
titel op Aanvragen, noodstop = gepauzeerd, geen dubbele regels in Recent, een werkende
'Gelijktijdige achtergrondtaken' en geen dode 'Taal'-instelling."""

from __future__ import annotations

from typing import Any

import pytest
from test_arr_fakes import (  # noqa: F401 - b1ctx is een fixture
    FakeArr,
    b1ctx,
    make_episode,
    make_series,
    wire,
)

from mediaguard import health_summary, jobs
from mediaguard.activity import api as activity
from mediaguard.db import utcnow
from mediaguard.delivery import engine, store

HOOK_KEY = "hook-key-series"


@pytest.fixture
def sonarr(b1ctx):  # noqa: F811  # type: ignore[no-untyped-def]
    fake = wire(b1ctx, "sonarr-series", FakeArr("sonarr"))
    fake.add_series(make_series(10, "Severance", 371980),
                    [make_episode(1, 10, 1, 1), make_episode(2, 10, 1, 2)])
    return fake


def _request(ctx: Any) -> int:
    item_id = store.upsert(ctx, media_type="series", tmdb_id=None, tvdb_id=371980,
                           title="Severance", source="arr", instance_id="sonarr-series",
                           provider_id=10, seasons=[1])
    assert item_id is not None
    return item_id


def _searches(fake: FakeArr) -> list[dict[str, Any]]:
    return [c["body"] for c in fake.commands.values() if c["name"] == "EpisodeSearch"]


# ── 26: Aanvragen ─────────────────────────────────────────────────────────────
def test_requests_link_to_the_library_page(client, b1ctx, sonarr):  # noqa: F811
    item_id = _request(b1ctx)
    lib_id = b1ctx.db.execute_insert(
        "INSERT INTO lib_items(instance_id,kind,provider_id,title,synced_at) VALUES "
        "('sonarr-series','series',10,'Severance',?)", (utcnow(),))
    body = client.get("/api/v1/delivery/requests").json()
    row = next(i for i in body["items"] if i["id"] == item_id)
    # MediaGuard's eigen bibliotheek-ID, niet het Sonarr-ID (dat opent een ander item)
    assert row["library_id"] == lib_id and row["attempts"] == 0
    assert client.get(f"/api/v1/delivery/requests/{item_id}").json()["library_id"] == lib_id
    # een film met hetzelfde Radarr-nummer is een ander item
    b1ctx.db.execute("UPDATE lib_items SET kind='movie' WHERE id=?", (lib_id,))
    assert client.get(f"/api/v1/delivery/requests/{item_id}").json()["library_id"] is None


def test_search_now_searches_immediately_despite_backoff(client, b1ctx, sonarr):  # noqa: F811
    item_id = _request(b1ctx)
    # al een keer gezocht en de volgende poging pas over een dag
    store.update(b1ctx, item_id, attempts=1, next_action_at="2999-01-01T00:00:00+00:00")
    r = client.post(f"/api/v1/delivery/requests/{item_id}/search")
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["searched"] is True and body["item"]["attempts"] == 2
    assert _searches(sonarr) and _searches(sonarr)[0]["episodeIds"] == [1, 2]
    actions = [e["action"] for e in store.events(b1ctx, item_id)]
    assert "nu_zoeken" in actions and "gezocht" in actions


def test_search_now_refused_during_estop_and_when_delivered(client, b1ctx, sonarr):  # noqa: F811
    item_id = _request(b1ctx)
    b1ctx.state_set("emergency_stop", {"active": True})
    r = client.post(f"/api/v1/delivery/requests/{item_id}/search")
    assert r.status_code == 409 and "noodstop" in r.json()["detail"].lower()
    assert not _searches(sonarr)
    b1ctx.state_set("emergency_stop", {"active": False})
    store.update(b1ctx, item_id, state="delivered")
    assert client.post(f"/api/v1/delivery/requests/{item_id}/search").status_code == 409
    assert client.post("/api/v1/delivery/requests/999/search").status_code == 404


def test_search_now_refused_for_a_stopped_request(client, b1ctx, sonarr):  # noqa: F811
    """Samenspel 26 + 11: 'Nu zoeken' zoekt nooit voor een gestopte aanvraag; eerst 'Nu
    opnieuw' (die volgt hem weer)."""
    item_id = _request(b1ctx)
    store.stop(b1ctx, item_id, "user", "test")
    r = client.post(f"/api/v1/delivery/requests/{item_id}/search")
    assert r.status_code == 409 and "Nu opnieuw" in r.json()["detail"]
    assert not _searches(sonarr)
    assert store.get(b1ctx, item_id)["state"] == "stopped"


# ── 25: 'Niet meer zoeken' op de titelpagina ─────────────────────────────────
async def test_no_search_on_title_stops_delivery_searches(b1ctx, sonarr):  # noqa: F811
    item_id = _request(b1ctx)
    b1ctx.config.set("series", "sonarr-series:10", "backfill.excluded", True, "test")
    await engine.run_once(b1ctx, force=True)
    row = store.get(b1ctx, item_id)
    assert row is not None
    assert not _searches(sonarr)
    assert row["detail_nl"] == engine.SEARCH_OFF_NL and row["attempts"] == 0
    # weer aan: de volgende ronde zoekt gewoon
    b1ctx.config.reset("series", "sonarr-series:10", "backfill.excluded")
    await engine.run_once(b1ctx, force=True)
    assert _searches(sonarr)


async def test_instance_wide_exclusion_does_not_block_a_request(b1ctx, sonarr):  # noqa: F811
    """Alleen de titel-uitzondering telt voor de leveringsmotor; een uitzondering voor de hele
    instantie geldt voor backfill, niet voor een aanvraag die je net zelf deed."""
    _request(b1ctx)
    b1ctx.config.set("instance", "sonarr-series", "backfill.excluded", True, "test")
    await engine.run_once(b1ctx, force=True)
    assert _searches(sonarr)


async def test_search_resumes_right_after_no_search_is_switched_off(b1ctx, sonarr):  # noqa: F811
    """Weer aan op de titelpagina: de volgende gewone ronde zoekt meteen, niet pas na de
    geparkeerde dagcontrole (en de geparkeerde wachttijd telt niet als terugvaltijd)."""
    item_id = _request(b1ctx)
    store.update(b1ctx, item_id, attempts=2)
    b1ctx.config.set("series", "sonarr-series:10", "backfill.excluded", True, "test")
    await engine.run_once(b1ctx, force=True)
    parked = store.get(b1ctx, item_id)
    assert parked is not None and parked["detail_nl"] == engine.SEARCH_OFF_NL
    assert str(parked["next_action_at"]) > utcnow()
    await engine.run_once(b1ctx)  # nog uit: blijft geparkeerd
    assert not _searches(sonarr)
    b1ctx.config.reset("series", "sonarr-series:10", "backfill.excluded")
    await engine.run_once(b1ctx)  # gewone ronde, geen force
    assert _searches(sonarr)
    row = store.get(b1ctx, item_id)
    assert row is not None and row["attempts"] == 3 and row["detail_nl"] != engine.SEARCH_OFF_NL


def test_search_now_respects_no_search(client, b1ctx, sonarr):  # noqa: F811
    item_id = _request(b1ctx)
    b1ctx.config.set("series", "sonarr-series:10", "backfill.excluded", True, "test")
    body = client.post(f"/api/v1/delivery/requests/{item_id}/search").json()
    assert body["searched"] is False and body["detail_nl"] == engine.SEARCH_OFF_NL
    assert not _searches(sonarr)


# ── 39: noodstop = gepauzeerd, niet 'bezig' ──────────────────────────────────
def _encoding_job(ctx: Any, status: str) -> None:
    ctx.db.execute(
        "INSERT INTO encoding_jobs(instance_id,source_path,status,policy_json,progress,"
        "created_at,updated_at) VALUES ('sonarr-series','/tv/Show/S01E01.mkv',?,'{}',0.4,?,?)",
        (status, utcnow(), utcnow()))


def test_estop_counts_running_encodes_as_paused(b1ctx):  # noqa: F811
    _encoding_job(b1ctx, "running")
    before = health_summary.compose(b1ctx, None, None)
    assert before["active"]["encodes"] == 1 and before["paused"]["encodes"] == 0
    b1ctx.state_set("emergency_stop", {"active": True})
    during = health_summary.compose(b1ctx, None, None)
    assert during["active"]["encodes"] == 0 and during["active"]["total"] == 0
    assert during["paused"] == {"encodes": 1, "emergency_stop": True}
    enc = activity._encoding(b1ctx)["jobs"][0]
    assert enc["paused_estop"] is True and enc["label"] == "Gepauzeerd (noodstop)"
    b1ctx.state_set("emergency_stop", {"active": False})
    enc = activity._encoding(b1ctx)["jobs"][0]
    assert enc["paused_estop"] is False and enc["label"] == "Encoderen"


# ── 39: geen dubbele regels in Recent ────────────────────────────────────────
def test_recent_dedupes_across_a_minute_boundary():
    rows = [
        {"kind": "upgrade", "instance_id": "s", "title": "Show · S01E05",
         "at": "2026-11-08T12:01:02+00:00"},
        {"kind": "import", "instance_id": "s", "title": "Show · S01E05",
         "at": "2026-11-08T12:00:59+00:00"},
        # ander bestand: blijft
        {"kind": "import", "instance_id": "s", "title": "Show · S01E06",
         "at": "2026-11-08T12:00:58+00:00"},
        # dezelfde titel uren later is een nieuwe gebeurtenis
        {"kind": "import", "instance_id": "s", "title": "Show · S01E05",
         "at": "2026-11-08T08:00:00+00:00"},
    ]
    out = activity.dedupe_recent([dict(r) for r in rows])
    assert [(r["title"], r["kind"]) for r in out] == [
        ("Show · S01E05", "upgrade"), ("Show · S01E06", "import"), ("Show · S01E05", "import")]
    assert all("_at" not in r for r in out)


def test_recent_rows_without_time_only_merge_with_each_other():
    rows = [
        {"kind": "grab", "instance_id": "s", "title": "Show.S01E05", "at": "2026-11-08T12:00:00Z"},
        {"kind": "grab", "instance_id": "s", "title": "Show.S01E05", "at": None},
        {"kind": "grab", "instance_id": "s", "title": "Show.S01E05", "at": "onbekend"},
    ]
    out = activity.dedupe_recent([dict(r) for r in rows])
    # de regel met tijd blijft los; de twee zonder leesbare tijd worden één
    assert [r["at"] for r in out] == ["2026-11-08T12:00:00Z", None]


def _download(upgrade: bool = False, with_file: bool = True) -> dict[str, Any]:
    payload: dict[str, Any] = {
        "eventType": "Download", "isUpgrade": upgrade, "downloadId": "SAB_1",
        "series": {"id": 10, "title": "Severance", "tvdbId": 371980},
        "episodes": [{"id": 1, "episodeNumber": 1, "seasonNumber": 1}],
        "release": {"releaseTitle": "Severance.S01E01.1080p.WEB-DL-GRP"}}
    if with_file:
        payload["episodeFile"] = {"id": 91, "path": "/tv/Severance/S01E01.mkv",
                                  "relativePath": "S01E01.mkv"}
    else:  # Sonarr 'Import voltooid': samenvatting met episodeFiles
        payload["episodeFiles"] = [{"id": 91, "path": "/tv/Severance/S01E01.mkv"}]
    return payload


def test_webhook_import_twins_are_stored_once(client, b1ctx):  # noqa: F811
    b1ctx.set_hook_key("sonarr-series", HOOK_KEY)
    url = "/api/v1/hooks/arr-webhook/sonarr-series"
    h = {"X-MediaGuard-Hook-Key": HOOK_KEY}
    first = client.post(url, json=_download(), headers=h).json()
    assert first["status"] == "stored"
    # hetzelfde bestand nog eens als 'Upgrade' en als 'Import voltooid'
    assert client.post(url, json=_download(upgrade=True), headers=h).json()[
        "status"] == "duplicate"
    assert client.post(url, json=_download(with_file=False), headers=h).json()[
        "status"] == "duplicate"
    n = b1ctx.db.fetch_one("SELECT COUNT(*) AS n FROM import_events")["n"]
    assert n == 1
    # zonder eerdere import per bestand wordt 'Import voltooid' gewoon bewaard
    other = _download(with_file=False)
    other["downloadId"] = "SAB_2"
    assert client.post(url, json=other, headers=h).json()["status"] == "stored"


# ── 39: instellingen die niets deden ─────────────────────────────────────────
def test_job_runner_follows_max_concurrency_setting(ctx):
    runner = jobs.JobRunner(ctx)
    assert runner.concurrency == 4
    ctx.config.set("global", "", "jobs.max_concurrency", 9, "test")
    assert runner.concurrency == 9  # zonder herstart
    assert jobs.JobRunner(ctx, concurrency=2).concurrency == 2  # vast (tests)
    assert not ctx.registry.get_def("jobs.max_concurrency").restart_required


def test_dead_language_setting_is_gone(app):  # noqa: ARG001 - app registreert alles
    from mediaguard.settings_registry import REGISTRY

    with pytest.raises(KeyError):
        REGISTRY.get_def("ui.default_locale")
