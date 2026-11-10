"""Verbeterpunten 28 en 29 (core-kant): 'te traag' is geen eindoordeel, tijdelijke
oorzaken die de encoder benoemt worden opnieuw geprobeerd, en een resultaat zonder
geslaagde kwaliteitsmeting wordt nooit stil geplaatst."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

from mediaguard.encoding import autopilot


def _add(ctx, path, status, *, error=None, skip=None, hours_ago=0.0):
    at = (datetime.now(UTC) - timedelta(hours=hours_ago)).isoformat(timespec="seconds")
    ctx.db.execute(
        "INSERT INTO encoding_jobs(source_path,status,error,skip_reason,trial_only,policy_json,"
        "created_at,updated_at) VALUES (?,?,?,?,0,'{}',?,?)",
        (path, status, error, skip, at, at))


SLOW = ("te traag voor de CPU: nog ± 31,5 uur bij 0,06× (grens 12 uur) — overgeslagen, "
        "origineel blijft")


def test_too_slow_is_retried_later_not_blocked_forever(ctx):
    """Na een lange noodstop leek een gezonde encode 'te traag' en werd hij nooit meer
    geprobeerd. Nu: na een dag opnieuw, hooguit twee keer, en nooit als fout geteld."""
    from mediaguard import attention, health_summary

    assert autopilot.classify_failure("skipped", SLOW) == "slow"
    assert autopilot.classify_failure("skipped", "Geen besparing") is None
    _add(ctx, "/s/a.mkv", "skipped", skip=SLOW, hours_ago=2)
    st = autopilot.retry_states(ctx)["/s/a.mkv"]
    assert st["state"] == "wait" and st["kind"] == "slow"
    _add(ctx, "/s/b.mkv", "skipped", skip=SLOW, hours_ago=25)
    assert autopilot.retry_states(ctx)["/s/b.mkv"]["state"] == "retry"
    assert "/s/b.mkv" not in autopilot._known_paths(ctx)  # mag weer worden aangeboden
    # ander overslaan (geen winst, HDR10+, …) blijft definitief
    _add(ctx, "/s/c.mkv", "skipped", skip="Geen besparing: origineel blijft")
    assert autopilot.retry_states(ctx)["/s/c.mkv"]["state"] == "blocked"
    # 'te traag' telt niet als AV1-fout
    assert not health_summary.error_items(ctx, "av1_failed")
    # na drie keer te traag: stil laten staan, geen 'lukt niet'-melding
    for hours in (80, 60):
        _add(ctx, "/s/d.mkv", "skipped", skip=SLOW, hours_ago=hours)
    _add(ctx, "/s/d.mkv", "skipped", skip=SLOW, hours_ago=30)
    assert autopilot.retry_states(ctx)["/s/d.mkv"]["state"] == "blocked"
    keys = {i["key"] for i in attention._encoding(ctx)}
    assert "encoding.failed_final" not in keys


def test_named_temporary_causes_are_transient():
    for text in ("encoderproces faalde (ffmpeg, exitcode 1): geen ruimte meer op de "
                 "cacheschijf — laatste melding: \"No space left on device\"",
                 "Encode gestopt: te weinig werkgeheugen (het systeem stopte ffmpeg)."):
        assert autopilot.classify_failure("failed", text) == "transient"
    assert autopilot.classify_failure(
        "failed", "encoderproces faalde (ffmpeg, exitcode 1): de bron is beschadigd of "
                  "onleesbaar") == "deterministic"
