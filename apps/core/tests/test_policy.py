"""Policy: deterministische ranker, profielen, scope-overerving, simulatie, TRaSH."""

from __future__ import annotations

import httpx

from mediaguard.identity.engine import EpisodeRef
from mediaguard.policy import profiles
from mediaguard.policy.ranker import Candidate, rank

GB = 1024**3
DUAL_1080 = Candidate(title="[Group] Show - 05 (1080p) [Dual Audio]", size=2 * GB)
JA_2160 = Candidate(title="[Other] Show - 05 (2160p)", size=8 * GB, languages=["Japanese"])


def cfg(profile_id: str) -> dict:
    return profiles.ProfileConfig.model_validate(
        profiles.BUILTIN[profile_id]["config"]).model_dump()


def test_anime_1080p_dual_beats_4k_japanese_only():
    ranked = rank([JA_2160, DUAL_1080], cfg("anime-dual-first"), is_anime=True)
    assert ranked[0].candidate is DUAL_1080
    assert "audio" in ranked[0].explanation_nl and "resolutie" in ranked[0].explanation_nl


def test_resolution_first_profile_prefers_4k():
    c = cfg("anime-dual-first")
    c["ranking_order"] = ["resolution", "audio", "source", "custom_format", "group"]
    assert rank([DUAL_1080, JA_2160], c, is_anime=True)[0].candidate is JA_2160


def test_ranking_is_deterministic():
    cands = [Candidate(title=f"Show.S01E01.1080p.WEB-DL-G{i}") for i in range(5)]
    first = [r.candidate.title for r in rank(cands, cfg("standaard"))]
    assert first == [r.candidate.title for r in rank(list(reversed(cands)), cfg("standaard"))]


def test_hard_rules_beat_custom_format_score():
    c = cfg("standaard")
    wrong = Candidate(title="Show.S01E04.2160p.BluRay.REMUX-GRP", custom_format_score=99999)
    right = Candidate(title="Show.S01E03.720p.HDTV-GRP", custom_format_score=-100)
    ranked = rank([wrong, right], c, expected=[EpisodeRef(1, 3)])
    assert ranked[0].candidate is right
    assert ranked[1].rejected and ranked[1].rejections[0].code == "IDENTITY_MISMATCH"
    kids = cfg("kids-nl-audio")
    en = Candidate(title="Kids.S01E01.1080p.WEB-DL-X", languages=["English"],
                   custom_format_score=5000)
    nl = Candidate(title="Kids.S01E01.NL.Gesproken.720p.WEB-DL-Y")
    out = rank([en, nl], kids)
    assert out[0].candidate is nl and out[1].rejections[0].code == "AUDIO_REQUIREMENT"


def test_source_then_resolution_then_cf():
    c = cfg("standaard")
    a = Candidate(title="M.2020.1080p.BluRay.x264-A", custom_format_score=0)
    b = Candidate(title="M.2020.2160p.WEB-DL.x265-B", custom_format_score=0)
    d = Candidate(title="M.2020.1080p.BluRay.x264-C", custom_format_score=100)
    assert [r.candidate for r in rank([a, b, d], c)] == [d, a, b]


def test_effective_policy_scope_inheritance(ctx):
    chain = profiles.build_chain(instance_id="sonarr-anime", media_type="anime", series_id=7)
    eff = profiles.effective_policy(ctx, chain)
    assert eff["profile_id"] == "anime-dual-first"
    # globaal → media_type → instantie → serie: laatste expliciete wint
    ctx.config.set("global", "", "audio.nl_audio_mode", "preferred", "t")
    ctx.config.set("series", "sonarr-anime:7", "audio.nl_audio_mode", "required", "t")
    ctx.config.set("instance", "sonarr-anime", "policy.ranking_mode", "resolution_first", "t")
    eff = profiles.effective_policy(ctx, chain)
    assert eff["config"]["audio"]["nl_audio"] == "required"
    assert eff["trace"]["audio.nl_audio"]["source"] == "series"
    assert eff["config"]["ranking_order"][0] == "resolution"
    other = profiles.effective_policy(ctx, profiles.build_chain(
        instance_id="sonarr-anime", media_type="anime", series_id=8))
    assert other["config"]["audio"]["nl_audio"] == "preferred"
    ctx.config.set("series", "sonarr-series:1", "policy.profile_id", "kids-nl-audio", "t")
    kid = profiles.effective_policy(ctx, profiles.build_chain(
        instance_id="sonarr-series", media_type="tv", series_id=1))
    assert kid["profile_id"] == "kids-nl-audio"


def test_profiles_api_crud_validation_audit(client):
    listed = client.get("/api/v1/profiles").json()
    assert {p["id"] for p in listed} >= set(profiles.BUILTIN)
    bad = client.put("/api/v1/profiles/anime-dual-first",
                     json={"config": {"ranking_order": ["audio", "kleur"]}})
    assert bad.status_code == 422
    assert client.get("/api/v1/profiles/anime-dual-first").json()["version"] == 1
    new_cfg = {**profiles.BUILTIN["anime-dual-first"]["config"],
               "ranking_order": ["resolution", "audio", "source"]}
    r = client.put("/api/v1/profiles/anime-dual-first", json={"config": new_cfg})
    assert r.status_code == 200 and r.json()["version"] == 2
    assert client.get("/api/v1/profiles/anime-dual-first/history").json()[0]["version"] == 2
    assert client.post("/api/v1/profiles/anime-dual-first/reset").json()["version"] == 3
    custom = client.put("/api/v1/profiles/mijn-profiel",
                        json={"name_nl": "Mijn profiel", "config": {"max_resolution": 1080}})
    assert custom.status_code == 200 and not custom.json()["builtin"]
    client.patch("/api/v1/settings/scoped", json={"changes": [
        {"scope_type": "instance", "scope_id": "sonarr-series", "key": "policy.profile_id",
         "value": "mijn-profiel"}]})
    assert client.delete("/api/v1/profiles/mijn-profiel").status_code == 409
    assert client.delete("/api/v1/profiles/standaard").status_code == 409
    audit = client.get("/api/v1/audit?action=policy").json()
    assert any(a["action"] == "policy.profile_update" for a in audit)


def test_simulate_api_explains_why(client):
    r = client.post("/api/v1/policy/simulate", json={
        "media_type": "anime", "candidates": [
            {"title": "[Other] Show - 05 (2160p)", "languages": ["Japanese"]},
            {"title": "[Group] Show - 05 (1080p) [Dual Audio]"},
            {"title": "[X] Show - 05 (1080p)", "languages": ["English"],
             "release_group": "BadGroup"}]})
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["winner"] == "[Group] Show - 05 (1080p) [Dual Audio]"
    assert body["ranking_order"][0] == "identity"
    assert "Gekozen boven" in body["why_nl"]
    inline = client.post("/api/v1/policy/simulate", json={
        "profile": {"blocked_groups": ["BadGroup"], "audio": {"mode": "anime"}},
        "candidates": [{"title": "[X] Show - 05 (1080p)", "release_group": "BadGroup"}]}).json()
    assert inline["winner"] is None and inline["results"][0]["rejections"][0]["code"] == \
        "BLOCKED_GROUP"


TRASH = [{
    "trash_id": "abc", "name": "Anime Dual Audio",
    "trash_scores": {"default": 101, "anime-sonarr": 2000},
    "specifications": [{"name": "Dual", "implementation": "ReleaseTitleSpecification",
                        "negate": False, "required": True,
                        "fields": {"value": r"dual[ ._-]?audio"}}]}, {
    "name": "Bad regex", "specifications": [{"name": "x",
                                             "implementation": "ReleaseTitleSpecification",
                                             "fields": [{"name": "value", "value": "(("}]}]}]


def test_trash_import_scores_but_never_overrides_hard_rules(client):
    r = client.post("/api/v1/policy/trash/import", json={"formats": TRASH})
    assert r.status_code == 200 and r.json()["imported"] == 2
    assert any("ongeldige regex" in e for e in r.json()["errors"])
    sim = client.post("/api/v1/policy/simulate", json={
        "profile": {"audio": {"mode": "original", "nl_audio": "required"}},
        "candidates": [{"title": "Show.S01E01.1080p.Dual.Audio.WEB-DL-A"},
                       {"title": "Show.S01E01.NL.Gesproken.720p.WEB-DL-B"}]}).json()
    assert sim["winner"] == "Show.S01E01.NL.Gesproken.720p.WEB-DL-B"
    rejected = sim["results"][1]
    assert rejected["custom_format_score"] == 101 and rejected["rejected"]


async def test_trash_import_from_url_with_fake_server(client, ctx):
    ctx.services["trash_transport"] = httpx.MockTransport(
        lambda req: httpx.Response(200, json=TRASH[:1]))
    r = client.post("/api/v1/policy/trash/import",
                    json={"url": "https://example.invalid/cf.json"})
    assert r.status_code == 200 and r.json()["imported"] == 1
    assert client.post("/api/v1/policy/trash/import",
                       json={"url": "http://insecure.invalid/x"}).status_code == 422
    assert client.get("/api/v1/policy/trash").json()[0]["source"] == "url:example.invalid"
