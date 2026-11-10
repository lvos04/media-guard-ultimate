"""Audiotaal en ondertiteling (E2/P6/L2)."""

from __future__ import annotations

import pytest

from mediaguard.audio import inventory
from mediaguard.audio.evaluate import AudioPolicy, evaluate_inventory, validate_claims
from mediaguard.audio.languages import normalize, normalize_list
from mediaguard.identity.parser import parse_release


@pytest.mark.parametrize("value,code", [
    ("nl", "nl"), ("nld", "nl"), ("dut", "nl"), ("Dutch", "nl"), ("Flemish", "nl"),
    ("nl-BE", "nl"), ("en", "en"), ("eng", "en"), ("English", "en"), ("en_US", "en"),
    ("ja", "ja"), ("jpn", "ja"), ("Japanese", "ja"), ("", "und"), (None, "und"),
    ("und", "und"), ("???", "und"), ("kor", "ko"), ("xyz", "xyz"),
])
def test_normalize(value, code):
    assert normalize(value) == code


def test_normalize_list_arr_formats():
    assert normalize_list("eng/jpn") == ["en", "ja"]
    assert normalize_list([{"id": 1, "name": "English"}, {"name": "Dutch"}]) == ["en", "nl"]


def ffprobe(*streams):
    return {"streams": list(streams)}


def a(i, lang, title="", **disp):
    return {"index": i, "codec_type": "audio", "codec_name": "aac", "channels": 2,
            "tags": {"language": lang, "title": title}, "disposition": disp}


def s(i, lang, title="", **disp):
    return {"index": i, "codec_type": "subtitle", "codec_name": "ass",
            "tags": {"language": lang, "title": title}, "disposition": disp}


ANIME = AudioPolicy(mode="anime")


def test_english_commentary_is_not_an_english_dub():
    inv = inventory.build(ffprobe(a(1, "jpn"), a(2, "eng", "Director's Commentary")))
    assert inv.main_audio_languages == ["ja"]
    verdict = evaluate_inventory(inv, ANIME)
    assert verdict.tier == "japanese"
    assert any("commentaar" in r for r in verdict.reasons)
    by_disposition = inventory.build(ffprobe(a(1, "jpn"), a(2, "eng", comment=1)))
    assert evaluate_inventory(by_disposition, ANIME).tier == "japanese"


def test_audio_description_not_counted():
    inv = inventory.build(ffprobe(a(1, "nld", "Audiodescriptie"), a(2, "eng")))
    assert inv.main_audio_languages == ["en"]


def test_dual_audio_claim_with_only_japanese_is_false():
    parsed = parse_release("[Group] Show - 05 (1080p) [Dual Audio]")
    inv = inventory.build(ffprobe(a(1, "jpn"), a(2, "eng", "Commentary")))
    checks = validate_claims(dual_audio=parsed.dual_audio,
                             claimed_audio=parsed.audio_language_claims, inv=inv)
    assert checks[0].claim == "dual_audio" and checks[0].valid is False
    assert "commentaar" in checks[0].reason_nl
    ok = inventory.build(ffprobe(a(1, "jpn"), a(2, "eng")))
    assert validate_claims(dual_audio=True, claimed_audio=[], inv=ok)[0].valid is True
    unknown = inventory.build(ffprobe(a(1, ""), a(2, "und")))
    assert validate_claims(dual_audio=True, claimed_audio=[], inv=unknown)[0].valid is None


def test_anime_preference_order_and_fallback():
    dual = evaluate_inventory(inventory.build(ffprobe(a(1, "jpn"), a(2, "eng"))), ANIME)
    eng = evaluate_inventory(inventory.build(ffprobe(a(1, "eng"))), ANIME)
    jpn = evaluate_inventory(inventory.build(ffprobe(a(1, "jpn"))), ANIME)
    assert dual.rank > eng.rank > jpn.rank
    assert dual.meets_preference and not dual.keep_searching
    assert jpn.acceptable and jpn.keep_searching  # fallback: importeren, blijven zoeken
    exception = AudioPolicy(mode="anime", anime_preference="japanese_first")
    assert evaluate_inventory(inventory.build(ffprobe(a(1, "jpn"))), exception).meets_preference


def test_regular_content_nl_subs_and_kids_nl_audio():
    regular = AudioPolicy(mode="original", nl_subtitles_wanted=True)
    inv = inventory.build(ffprobe(a(1, "eng"), s(2, "dut"), s(3, "eng", "Signs & Songs")))
    v = evaluate_inventory(inv, regular)
    assert v.acceptable and v.meets_preference and "nl" in v.subtitle_languages
    no_nl = evaluate_inventory(inventory.build(ffprobe(a(1, "eng"), s(2, "eng"))), regular)
    assert no_nl.acceptable and not no_nl.meets_preference
    kids = AudioPolicy(mode="original", nl_audio="required")
    assert not evaluate_inventory(inventory.build(ffprobe(a(1, "eng"))), kids).acceptable
    assert evaluate_inventory(inventory.build(ffprobe(a(1, "nld"), a(2, "eng"))),
                              kids).acceptable


def test_subtitle_flags():
    inv = inventory.build(ffprobe(s(1, "eng", "Signs & Songs"), s(2, "eng", forced=1),
                                  s(3, "nld", "SDH")))
    flags = [(t.signs_songs, t.forced, t.sdh) for t in inv.subtitles]
    assert flags == [(True, False, False), (False, True, False), (False, False, True)]
    assert inv.full_subtitle_languages == ["nl"]


def test_encoder_probe_and_arr_formats():
    enc = inventory.build({"audio": [{"index": 1, "taal": "jpn", "titel": "",
                                      "commentaar": False, "kanalen": 2}],
                           "subs": [{"index": 2, "taal": "dut", "forced": False}]})
    assert enc.main_audio_languages == ["ja"] and enc.full_subtitle_languages == ["nl"]
    arr = inventory.build({"audio_languages": ["eng", "jpn"], "subtitles": ["dut"]})
    assert arr.main_audio_languages == ["en", "ja"]


def test_audio_inspect_api(client):
    r = client.post("/api/v1/audio/inspect", json={
        "probe": ffprobe(a(1, "jpn"), a(2, "eng", "Commentary")),
        "release_title": "[G] Show - 01 [Dual Audio]", "media_type": "anime"})
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["profile_id"] == "anime-dual-first"
    assert body["verdict"]["tier"] == "japanese" and body["claims"][0]["valid"] is False
    assert client.get("/api/v1/audio/normalize/dut").json()["code"] == "nl"
