"""Set-and-forget: twijfel lost zichzelf op, verwijderde titels worden opgeruimd."""

from __future__ import annotations

import pytest
from test_arr_fakes import FakeArr, b1ctx, make_movie, make_series, wire  # noqa: F401

from mediaguard import attention, automation
from mediaguard.identity import service as identity
from mediaguard.identity.engine import EpisodeRef, Evidence, IdentityResult


def _save(ctx, status, evidence, *, pid="501", kind="episode_file", source="post_import",
          expected=None, actionable=False, old_engine=False):
    exp = expected or [EpisodeRef(5, 1)]
    rid = identity.save(ctx, instance_id="sonarr-series", kind=kind, provider_id=pid,
                        path="/data/media/tv/Show/Show.S05E01.mkv",
                        result=IdentityResult(status, 0.4, actionable, "test", exp, [],
                                              evidence),
                        source=source, actor="t")
    if old_engine:
        ctx.db.execute("UPDATE identity_results SET engine_version=1 WHERE id=?", (rid,))
    return rid


def _latest(ctx, pid="501"):
    return identity.latest(ctx, "sonarr-series", "episode_file", pid)


def test_old_season_pack_mismatch_is_reevaluated(b1ctx):  # noqa: F811
    pack = [EpisodeRef(5, n) for n in range(1, 13)]
    _save(b1ctx, "mismatch", [Evidence("download_history", "contradicts",
                                       "gegrabd voor S05E01-E12", claimed=pack)],
          old_engine=True)
    assert automation.reevaluate(b1ctx) == 1
    latest = _latest(b1ctx)
    assert latest["status"] != "mismatch" and latest["source"] == "reevaluate"
    assert b1ctx.db.fetch_one("SELECT 1 FROM audit_events WHERE action='identity.reevaluated'")
    assert automation.reevaluate(b1ctx) == 0  # nieuwe versie: niet opnieuw


def test_weak_doubt_is_no_user_task_and_auto_kept(b1ctx):  # noqa: F811
    _save(b1ctx, "uncertain", [Evidence("filename", "contradicts", "naam zegt S05E02",
                                        claimed=[EpisodeRef(5, 2)])])
    assert attention._identity(b1ctx) == []
    b1ctx.config.set("global", "", "identity.uncertain_auto_keep_days", 0, "test")
    assert automation.auto_keep_uncertain(b1ctx) == 1
    assert _latest(b1ctx)["status"] == "probable"


def test_conflict_rows_link_to_episode_page(b1ctx):  # noqa: F811
    ctx = b1ctx
    item = ctx.db.execute_insert("INSERT INTO lib_items(instance_id,kind,provider_id,title,"
                                 "monitored,synced_at) VALUES ('sonarr-series','series',7,'S',1,"
                                 "'x')")
    ctx.db.execute("INSERT INTO lib_episodes(instance_id,provider_id,series_provider_id,"
                   "season_number,episode_number,episode_file_id,synced_at) VALUES "
                   "('sonarr-series',70,7,5,1,501,'x')")
    strong = [Evidence("download_history", "contradicts", "gegrabd voor S05E02",
                       claimed=[EpisodeRef(5, 2)]),
              Evidence("release_title", "supports", "release S05E01",
                       claimed=[EpisodeRef(5, 1)])]
    _save(ctx, "uncertain", strong)
    items = attention._identity(ctx)
    href = f"/bibliotheek/sonarr-series/{item}/aflevering/70"
    assert items[-1]["rows"][0]["href"] == href and items[-1]["href"] == href
    # pre-import sleutel = afleverings-ID's
    _save(ctx, "uncertain", strong, pid="70,71", source="pre_import")
    assert attention.identity_href(ctx, "sonarr-series", "episode_file", "70,71") == href


def test_episode_page_sees_and_resolves_file_result(client, b1ctx):  # noqa: F811
    ctx = b1ctx
    ctx.db.execute("INSERT INTO lib_episodes(instance_id,provider_id,series_provider_id,"
                   "season_number,episode_number,episode_file_id,synced_at) VALUES "
                   "('sonarr-series',70,7,5,1,501,'x')")
    _save(ctx, "uncertain", [Evidence("download_history", "contradicts", "x",
                                      claimed=[EpisodeRef(5, 2)])])
    r = client.get("/api/v1/identity/sonarr-series/episode/70/evidence").json()
    assert r["status"] == "uncertain"
    r = client.post("/api/v1/identity/sonarr-series/episode/70/decision",
                    json={"status": "verified"})
    assert r.status_code == 200, r.text
    assert _latest(ctx)["status"] == "verified"  # vervangt het bestandsresultaat
    assert attention._identity(ctx) == []


@pytest.mark.asyncio
async def test_ai_tiebreak_decides_conflicts(b1ctx, monkeypatch):  # noqa: F811
    from mediaguard.ai import service as ai

    ctx = b1ctx
    _save(ctx, "uncertain", [Evidence("download_history", "contradicts", "x",
                                      claimed=[EpisodeRef(5, 2)])])
    assert automation.ai_ready(ctx) is None  # geen sleutel
    ctx.secrets.set("ai:anthropic:apikey", "sk-test", "test")
    assert automation.ai_missing_consent(ctx) == "anthropic"
    assert any(i["key"] == "identity.ai_consent" for i in attention._identity(ctx))
    ai.record_consent(ctx, "anthropic", True, "test")
    assert automation.ai_ready(ctx) == ("anthropic", "claude-haiku-5-5")

    async def fake_call(ctx, **kw):
        return ai.CallOutcome(ok=True, data={"verdict": "match", "confidence": 0.95,
                                             "reason_nl": "zelfde aflevering"})
    monkeypatch.setattr(ai, "call_structured", fake_call)
    assert await automation.ai_tiebreak(ctx) == 1
    assert _latest(ctx)["status"] == "verified" and _latest(ctx)["source"] == "ai"
    assert await automation.ai_tiebreak(ctx) == 0


@pytest.mark.asyncio
async def test_removed_titles_without_file_are_cleaned_up(b1ctx):  # noqa: F811
    ctx = b1ctx
    radarr = wire(ctx, "radarr-films", FakeArr("radarr"))
    gone = make_movie(1, "Weg", 11)
    gone["status"] = "deleted"
    kept = make_movie(2, "Met bestand", 12, has_file=True)
    kept["status"] = "deleted"
    radarr.movies = {1: gone, 2: kept, 3: make_movie(3, "Normaal", 13)}
    sonarr = wire(ctx, "sonarr-series", FakeArr("sonarr"))
    sonarr.add_series(make_series(7, "Weg", 1, status="deleted"), [], [])
    assert await automation.cleanup_removed(ctx) >= 2
    assert set(radarr.movies) == {2, 3} and 7 not in sonarr.series
    deletes = [c for c in radarr.calls if c[0] == "DELETE"]
    assert deletes and all(c[2].get("deleteFiles") == "false" for c in deletes)
