"""Rapport 2 / N2: geen AV1-versie zonder sporen; leesbare redenen; automatisch terugzetten."""

from collections import Counter

from mediaguard.recovery import release_check as rc
from mediaguard.recovery.api import _content_loss


def _probe(subs, audio=("eng",), dur=100.0):
    streams = [{"codec_type": "video", "width": 1920, "height": 1080}]
    streams += [{"codec_type": "audio", "tags": {"language": a}} for a in audio]
    streams += [{"codec_type": "subtitle", "tags": {"language": s}} for s in subs]
    return {"format": {"duration": str(dur)}, "streams": streams, "chapters": []}


SRC = _probe(["eng", "ger", "ger", "ara", "spa"])
AV1_ENG = _probe(["eng"])


def test_languages_nl_readable():
    assert rc.languages_nl(Counter({"ger": 2, "ara": 1})) == "Arabisch, Duits (2)"


def test_default_policy_requires_all_subtitles():
    checks = rc.compare(SRC, AV1_ENG, {})
    sub = next(c for c in checks if c["name"] == "ondertitels")
    assert not sub["passed"]
    assert sub["detail"].startswith("Ondertitels ontbreken: Arabisch, Duits (2), Spaans")
    assert "{" not in rc.summary_nl(checks)


def test_explicit_language_choice_is_respected():
    pol = {"subtitle_policy": {"keep_all": False, "keep_languages": ["eng", "nld"]}}
    sub = next(c for c in rc.compare(SRC, AV1_ENG, pol) if c["name"] == "ondertitels")
    assert sub["passed"]


def test_content_loss_only_when_file_unchanged():
    base = [{"name": "ondertitels", "passed": False, "detail": "x"}]
    ok = [{"name": "ongewijzigd-sinds-activatie", "passed": True, "detail": ""}]
    changed = [{"name": "ongewijzigd-sinds-activatie", "passed": False, "detail": ""}]
    assert _content_loss(base + ok)
    assert not _content_loss(base + changed)  # bv. Sonarr-upgrade: niet terugzetten
    assert not _content_loss([{"name": "origineel", "passed": False, "detail": ""}] + ok)


def test_safe_track_defaults_flipped_once(ctx):
    from mediaguard.encoding.api import ensure_safe_track_defaults

    ctx.config.set("global", "", "encoding.keep_all_subtitles", False, "jan")
    assert ensure_safe_track_defaults(ctx) == ["encoding.keep_all_subtitles"]
    assert ctx.config.value("encoding.keep_all_subtitles") is True
    ctx.config.set("global", "", "encoding.keep_all_subtitles", False, "jan")
    assert ensure_safe_track_defaults(ctx) == []  # daarna respecteren we de keuze
    assert ctx.config.value("encoding.keep_all_subtitles") is False


def test_one_tap_back_to_quarantine(client, ctx):
    ctx.config.set("global", "", "quarantine.mode", "immediate_after_check", "jan")
    assert client.post("/api/v1/quarantine/use-quarantine", json={}).status_code == 422
    r = client.post("/api/v1/quarantine/use-quarantine", json={"confirm": True})
    assert r.status_code == 200 and ctx.config.value("quarantine.mode") == "retain"
