"""Dolby Vision zelf bedienen — acceptatietests DEEL Q3 (backend-testbaar deel)."""

from __future__ import annotations

import pytest
from fastapi.testclient import TestClient
from test_encoding_support import av1_output, install, make_probe

from mediaguard.app import create_app
from mediaguard.dv import policy as dv_policy
from mediaguard.dv import rules
from mediaguard.dv.verify import verify_output
from mediaguard.encoding import service as enc
from mediaguard.recovery import executor
from mediaguard.recovery import service as recovery

CAPS = {"encoders": {"libsvtav1": {"dolbyvision": True}}, "dovi_tool": True, "mkvmerge": True}
SOURCE_BYTES = b"DV81-HEVC-SOURCE" * 4000


def pol(**kw) -> dv_policy.DvPolicy:
    base = {"allow": True, "mode": "trial_copy", "allowed_profiles": ["8.1", "8.4", "7_mel"],
            "hdr10plus_mode": "skip"}
    return dv_policy.DvPolicy(**(base | kw))


DV81 = make_probe("/m/a.mkv", dv_profile=8, dv_compat=1)
DV84 = make_probe("/m/b.mkv", dv_profile=8, dv_compat=4)
DV7MEL = make_probe("/m/c.mkv", dv_profile=7, dv_compat=6, dv_el_type="mel")
DV7FEL = make_probe("/m/d.mkv", dv_profile=7, dv_compat=6, dv_el_type="fel")
DV5 = make_probe("/m/e.mkv", dv_profile=5)


# ── Q3.1 / Q3.2 / Q1.1 — beslisregels ───────────────────────────────────────
def test_q3_1_dv81_toggle_off_keeps_original():
    d = rules.decide(DV81, pol(allow=False), CAPS)
    assert d.status == rules.KEEP and not d.executable
    assert d.action == "keep" and "schakelaar" in d.reason_nl


def test_q3_2_dv81_toggle_on_is_executable_not_hardcoded_skip():
    d = rules.decide(DV81, pol(), CAPS)
    assert d.executable and d.action == "trial" and d.status == rules.TRIAL
    # handmatig activeren blijft een core-gate
    assert d.encoder_policy()["dv_mode"] == "auto_after_verify"
    assert d.encoder_policy()["dv_allowed_profiles"] == ["8.1"]
    auto = rules.decide(DV84, pol(mode="auto_after_verify"), CAPS)
    assert auto.executable and auto.action == "convert" and auto.status == rules.ALLOWED
    assert not auto.requires_manual_activation


def test_q3_3_full_auto_without_external_matrix():
    d = rules.decide(DV81, pol(mode="auto_after_verify"), CAPS, confirmations=[])
    ok, reasons = rules.full_auto_eligible(d, verification_passed=True, recovery_ready=True,
                                           canary_passed=True)
    assert ok and not reasons
    ok, reasons = rules.full_auto_eligible(d, verification_passed=True, recovery_ready=False,
                                           canary_passed=True)
    assert not ok and "herstelpad" in reasons[0]


def test_q3_4_dv7_mel_route_and_requirements():
    d = rules.decide(DV7MEL, pol(), CAPS)
    assert d.executable and d.route == "dovi_tool_7_to_81_then_svt"
    assert any("timing" in w for w in d.warnings)
    no_dovi = rules.decide(DV7MEL, pol(), {**CAPS, "dovi_tool": False})
    assert no_dovi.status == rules.UNSUPPORTED and not no_dovi.executable
    out = av1_output(DV7MEL)
    out["duration"] -= 5
    out["audio"] = out["audio"][:1]
    v = verify_output(DV7MEL, out)
    failed = {c.name for c in v.checks if not c.passed}
    assert not v.passed and {"duration", "audio_tracks"} <= failed


def test_q3_5_dv7_fel_never_silent_downgrade():
    d = rules.decide(DV7FEL, pol(allowed_profiles=["7_fel"]), CAPS)
    assert d.action == "keep" and "FEL" in d.reason_nl
    exp = rules.decide(DV7FEL, pol(mode="experimental_keep_source",
                                   allowed_profiles=["7_fel"]), CAPS)
    assert exp.action == "experimental_separate" and exp.separate_output
    assert not exp.replace_source_allowed
    assert any("FEL-beeldinformatie" in w for w in exp.warnings)
    unknown = rules.decide(make_probe("/m/x.mkv", dv_profile=7, dv_compat=6),
                           pol(allowed_profiles=["7_mel"]), CAPS)
    assert unknown.action == "keep"  # onbekend EL-type = FEL (veilig)
    v = verify_output(DV7FEL, av1_output(DV7FEL))
    assert v.passed and v.rpu_preserved and v.all_picture_info_preserved is False


def test_q3_6_dv5_explained_block():
    d = rules.decide(DV5, pol(allowed_profiles=["5"], mode="auto_after_verify"), CAPS)
    assert d.status == rules.UNSUPPORTED and "DV 5" in d.reason_nl and not d.executable
    proven = rules.decide(DV5, pol(allowed_profiles=["5"]), {"dv_routes": ["5"]})
    assert proven.executable  # alleen met expliciet bewezen route


def test_q3_7_output_without_rpu_or_changed_base_or_colour_fails():
    good = av1_output(DV81)
    assert verify_output(DV81, good).passed
    no_rpu = {**good, "dv_profile": 0}
    assert not verify_output(DV81, no_rpu).passed
    base = {**good, "width": 1920}
    assert "base_layer_dimensions" in {c.name for c in verify_output(DV81, base).checks
                                       if not c.passed}
    colour = {**good, "md_ruw": {**good["md_ruw"], "red_x": 0.64}}
    assert "mastering_display_values" in {c.name for c in verify_output(DV81, colour).checks
                                          if not c.passed}
    plus = make_probe("/m/p.mkv", hdr10=True, hdr10plus=True)
    assert not verify_output(plus, {**av1_output(plus), "hdr10plus": False}).passed


def test_hdr10plus_separate_switch():
    plus = make_probe("/m/p.mkv", hdr10=True, hdr10plus=True)
    assert rules.decide(plus, pol(hdr10plus_mode="skip"), CAPS).status == rules.KEEP
    # convert_verified zonder bewezen route: bron behouden
    no_route = rules.decide(plus, pol(hdr10plus_mode="convert_verified"), CAPS)
    assert no_route.status == rules.UNSUPPORTED and not no_route.executable
    ok = rules.decide(plus, pol(hdr10plus_mode="convert_verified"),
                      {**CAPS, "hdr10plus_route_verified": True})
    assert ok.executable and ok.route == "hdr10plus_behouden"
    fb = rules.decide(plus, pol(hdr10plus_mode="hdr10_fallback_explicit"), CAPS)
    assert fb.executable and any("VERLOREN" in w for w in fb.warnings)


def test_hdr10plus_verification_modes():
    plus = make_probe("/m/p.mkv", hdr10=True, hdr10plus=True)
    out = {**plus, "codec": "av1", "hdr10plus": False}
    lost = verify_output(plus, out)
    assert not lost.passed  # stil verlies bij convert_verified = fout
    assert all(c.passed for c in verify_output(
        plus, out, hdr10plus_mode="hdr10_fallback_explicit").checks
        if c.name == "hdr10plus_preserved")


def test_q3_10_playback_confirmation_reduces_warnings_only():
    before = rules.decide(DV81, pol(), CAPS)
    after = rules.decide(DV81, pol(), CAPS, confirmations=[
        {"dv_profile": "8.1", "codec": "av1", "container": "mkv"}])
    assert any("Afspeelketen" in w for w in before.warnings)
    assert not any("Afspeelketen" in w for w in after.warnings)
    assert after.action == before.action  # geen extra rechten
    assert not verify_output(DV81, {**av1_output(DV81), "dv_profile": 0}).passed


# ── API / persistentie ───────────────────────────────────────────────────────
def test_q3_8_9_13_14_policy_api_inheritance_persistence(client, settings):
    eff = client.get("/api/v1/dolby-vision/policies").json()
    assert eff["effective"]["allow"] is False and eff["effective"]["mode"] == "auto_after_verify"
    assert eff["inheritance_nl"].startswith("Globaal")
    r = client.patch("/api/v1/dolby-vision/policies", json={
        "scope_type": "series", "scope_id": "sonarr-series:12", "allow": True,
        "mode": "auto_after_verify", "reason": "deze serie speelt goed af"})
    assert r.status_code == 200, r.text
    series = client.get("/api/v1/dolby-vision/policies?library=sonarr-series"
                        "&series=sonarr-series:12").json()
    assert series["effective"]["allow"] is True
    assert series["effective"]["trace"]["allow"]["source_label_nl"] == "Titel"
    assert [lv["label_nl"] for lv in series["levels"]] == ["Globaal", "Bibliotheek", "Titel"]
    other = client.get("/api/v1/dolby-vision/policies?series=sonarr-series:13").json()
    assert other["effective"]["allow"] is False
    bad = client.patch("/api/v1/dolby-vision/policies", json={
        "scope_type": "series", "scope_id": "sonarr-series:12", "mode": "paars"})
    assert bad.status_code == 422
    bad2 = client.patch("/api/v1/dolby-vision/policies", json={
        "scope_type": "series", "scope_id": "sonarr-series:12", "allow": False,
        "allowed_profiles": ["9.9"]})
    assert bad2.status_code == 422  # alles-of-niets: ook 'allow' niet opgeslagen
    again = client.get("/api/v1/dolby-vision/policies?series=sonarr-series:12").json()
    assert again["effective"]["mode"] == "auto_after_verify" and again["effective"]["allow"]
    audit = client.get("/api/v1/audit?action=dv.policy").json()
    assert audit and audit[0]["reason"] == "deze serie speelt goed af"
    # herstart: nieuwe app op dezelfde datamap
    with TestClient(create_app(settings)) as c2:
        c2.post("/api/v1/auth/login", json={"username": "admin",
                                            "password": "geheim-wachtwoord"})
        persisted = c2.get("/api/v1/dolby-vision/policies?series=sonarr-series:12").json()
        assert persisted["effective"]["allow"] is True
    reset = client.patch("/api/v1/dolby-vision/policies", json={
        "scope_type": "series", "scope_id": "sonarr-series:12", "reset": ["allow", "mode"]})
    assert reset.status_code == 200
    assert client.get("/api/v1/dolby-vision/policies?series=sonarr-series:12").json()[
        "effective"]["allow"] is False


@pytest.fixture
def dvenv(client, ctx, tmp_path):
    fake = install(ctx, ctx.settings.staging_dir)
    src = tmp_path / "library" / "Film (2020)" / "Film (2020).mkv"
    src.parent.mkdir(parents=True)
    src.write_bytes(SOURCE_BYTES)
    fake.add_source(src, dv_profile=8, dv_compat=1)
    ctx.settings.quarantine_dir.mkdir(parents=True, exist_ok=True)
    recovery.mark_heartbeat(ctx)
    ctx.config.set("global", "", "recovery.min_free_gb", 0, "test")
    return fake, src


def test_capabilities_endpoint(client, dvenv):
    caps = client.get("/api/v1/dolby-vision/capabilities").json()
    assert caps["available"] and caps["routes"]["8.1"] and not caps["routes"]["5"]
    assert rules.UNSUPPORTED in caps["statuses"]


def test_q3_1_api_toggle_off_creates_no_encode(client, dvenv):
    fake, src = dvenv
    r = client.post("/api/v1/dolby-vision/test-encode", json={"path": str(src)})
    assert r.status_code == 200, r.text
    assert r.json()["status"] == rules.KEEP and r.json()["state"] == "kept"
    assert not fake.jobs


async def test_q3_2_trial_encode_verified_original_untouched(client, ctx, dvenv):
    fake, src = dvenv
    client.patch("/api/v1/dolby-vision/policies", json={"allow": True})
    t = client.post("/api/v1/dolby-vision/test-encode", json={
        "path": str(src), "instance_id": "radarr-films", "media_kind": "movie_file",
        "provider_id": 5, "movie_id": 5}).json()
    assert t["state"] == "running" and t["status"] == rules.TRIAL
    req = fake.jobs[1]["request"]
    # proef blijft in staging tot handmatige activatie; de encoder moet hem later wél
    # mogen committen (trial_copy/trial_only betekent bij de encoder: nooit vervangen)
    assert req["trial_only"] is False and req["policy"]["dv_mode"] == "auto_after_verify"
    assert req["policy"]["encoder"] == "cpu"
    done = client.get(f"/api/v1/dolby-vision/tests/{t['id']}").json()
    assert done["status"] == rules.CHECKED and done["verification"]["passed"]
    ev = done["evidence"][0]
    assert ev["rpu_preserved"] == 1 and ev["source_profile"] == "8.1" and ev["passed"] == 1
    assert src.read_bytes() == SOURCE_BYTES  # origineel ongemoeid

    # 'Activeer na verificatie' → recovery → Herstelbaar; daarna 'Herstel origineel'
    job_id = done["encoding_job_id"]
    act = client.post(f"/api/v1/dolby-vision/activate/{job_id}", json={"confirm": True})
    assert act.status_code == 200, act.text
    assert act.json()["ui_status"] == rules.RESTORABLE
    txn = await executor.run_transaction(ctx, act.json()["transaction_id"])
    assert txn["state"] == "committed" and src.read_bytes() == fake.output_bytes
    await enc.sync_job(ctx, job_id)
    assert enc.get_job(ctx, job_id)["status"] == "committed"
    assert ("POST", f"/jobs/{fake.jobs[1]['job_id']}/committed", {}) in fake.calls
    res = client.post(f"/api/v1/dolby-vision/restore/{job_id}", json={"confirm": True})
    assert res.status_code == 200, res.text
    restore = await executor.run_transaction(ctx, res.json()["restore_transaction_id"])
    assert restore["state"] == "committed" and src.read_bytes() == SOURCE_BYTES


def test_q3_7_api_verification_failure_keeps_source(client, ctx, dvenv):
    fake, src = dvenv
    fake.transform = lambda p: {**av1_output(p), "dv_profile": 0}  # RPU kwijt
    client.patch("/api/v1/dolby-vision/policies", json={"allow": True})
    t = client.post("/api/v1/dolby-vision/test-encode", json={"path": str(src)}).json()
    done = client.get(f"/api/v1/dolby-vision/tests/{t['id']}").json()
    assert done["status"] == rules.VERIFY_FAILED
    assert any(c["name"] == "rpu_present" and not c["passed"]
               for c in done["verification"]["checks"])
    assert any(p.endswith("/rejected") for _m, p, _b in fake.calls)
    act = client.post(f"/api/v1/dolby-vision/activate/{done['encoding_job_id']}",
                      json={"confirm": True})
    assert act.status_code == 409
    assert src.read_bytes() == SOURCE_BYTES


def test_q3_5_api_fel_experimental_separate_output_never_activates(client, ctx, dvenv,
                                                                   tmp_path):
    fake, _ = dvenv
    fel = tmp_path / "fel.mkv"
    fel.write_bytes(SOURCE_BYTES)
    fake.add_source(fel, dv_profile=7, dv_compat=6, dv_el_type="fel")
    client.patch("/api/v1/dolby-vision/policies", json={
        "allow": True, "mode": "experimental_keep_source",
        "allowed_profiles": ["8.1", "7_fel"]})
    t = client.post("/api/v1/dolby-vision/test-encode", json={"path": str(fel)}).json()
    assert t["decision"]["action"] == "experimental_separate"
    done = client.get(f"/api/v1/dolby-vision/tests/{t['id']}").json()
    assert done["status"] == rules.CHECKED
    assert done["evidence"][0]["all_picture_info_preserved"] == 0
    act = client.post(f"/api/v1/dolby-vision/activate/{done['encoding_job_id']}",
                      json={"confirm": True})
    assert act.status_code == 409 and act.json()["detail"]["code"] == "EXPERIMENTAL"
    assert fel.read_bytes() == SOURCE_BYTES


def test_q3_8_toggle_change_drives_backend_job(client, ctx, dvenv):
    fake, src = dvenv
    off = client.post("/api/v1/dolby-vision/test-encode", json={"path": str(src)}).json()
    assert off["status"] == rules.KEEP
    client.patch("/api/v1/dolby-vision/policies", json={
        "scope_type": "library", "scope_id": "radarr-films", "allow": True})
    on = client.post("/api/v1/dolby-vision/test-encode", json={
        "path": str(src), "instance_id": "radarr-films"}).json()
    assert on["status"] == rules.TRIAL and fake.jobs


def test_q3_10_playback_confirmation_api(client, dvenv):
    r = client.post("/api/v1/dolby-vision/playback-confirmations", json={
        "client": "LG C2 webOS", "server": "Jellyfin 10.10", "dv_profile": "8.1"})
    assert r.status_code == 200 and "verificatie blijft verplicht" in r.json()["note_nl"]
    listed = client.get("/api/v1/dolby-vision/playback-confirmations").json()
    assert listed[0]["codec"] == "av1"
    assert client.delete(f"/api/v1/dolby-vision/playback-confirmations/{listed[0]['id']}"
                         ).status_code == 200


def test_policy_persistence_file_level_and_job_level(ctx):
    dv_policy.apply_change(ctx, "global", "", {"allow": True}, [], "t", None)
    dv_policy.apply_change(ctx, "file", "radarr-films:99", {"allow": False}, [], "t", None)
    dv_policy.apply_change(ctx, "job", "777", {"allow": True}, [], "t", None)
    chain = dv_policy.scope_chain(file="radarr-films:99")
    assert dv_policy.effective(ctx, chain).allow is False
    assert dv_policy.effective(ctx, {**chain, "job": "777"}).allow is True
    with pytest.raises(ValueError):
        dv_policy.apply_change(ctx, "series", "", {"allow": True}, [], "t", None)
    versions = ctx.db.fetch_one("SELECT COUNT(*) AS n FROM dv_policy_versions")
    assert versions["n"] == 3


def test_no_switch_can_disable_verification(ctx):
    keys = [d.key for d in ctx.registry.all() if d.key.startswith("dv.")]
    assert keys and not any("verif" in k or "skip_check" in k for k in keys)


def test_technical_routes_reads_encoder_route_objects():
    """Bug: de encoder stuurt routes als objecten; core las ze als str(dict) en sloeg zo
    ieder DV-bestand over."""
    caps = {"dv_routes": [{"route": "8.1", "technically_possible": True},
                          {"route": "7_fel", "technically_possible": False}, "5"]}
    assert rules.technical_routes(caps) == {"8.1", "5"}
    d = rules.decide(DV81, pol(mode="auto_after_verify"), caps)
    assert "niet beschikbaar" not in d.reason_nl


def test_hdr10_dv_av1_representation_differences_pass():
    """DV8.1/HDR10-remux → SVT-AV1 (DV 10.1): andere schrijfwijzen en afronding van AV1-
    metadata zijn geen verlies. Echte afwijkingen blijven een fout, met details."""
    src = {**make_probe("/m/av.mkv", dv_profile=8, dv_compat=1, hdr10=True),
           "content_light": "1000,400",
           "kleur": {"primaries": "bt2020", "transfer": "smpte2084", "matrix": "bt2020nc"},
           "md_ruw": {"red_x": 0.708, "red_y": 0.292, "green_x": 0.17, "green_y": 0.797,
                      "blue_x": 0.131, "blue_y": 0.046, "white_point_x": 0.3127,
                      "white_point_y": 0.329, "max_luminance": 1000.0, "min_luminance": 0.0001}}
    out = {**av1_output(src), "content_light": "MaxCLL=1000, MaxFALL=400",
           "kleur": {"primaries": "bt2020", "transfer": "pq", "matrix": "bt2020_ncl"},
           "md_ruw": {**src["md_ruw"], "red_x": 0.70801, "min_luminance": 0.00006103515625}}
    v = verify_output(src, out)
    assert v.passed, [c for c in v.checks if not c.passed]
    bad = {**out, "md_ruw": {**out["md_ruw"], "max_luminance": 4000.0}}
    failed = [c for c in verify_output(src, bad).checks if not c.passed]
    assert failed and "max_luminance 1000 → 4000" in failed[0].detail_nl


def test_failed_verification_text_lists_checks():
    from mediaguard.encoding.service import failed_checks_text

    text = failed_checks_text({"checks": [
        {"name": "audio_tracks", "passed": False, "detail_nl": "Audio (8 → 7)"},
        {"name": "codec", "passed": True, "detail_nl": "ok"}]})
    assert text == "audio_tracks: Audio (8 → 7)"


def test_zero_content_light_equals_absent():
    """Rapport 4 nov (H2): MaxCLL/MaxFALL 0,0 in de bron = geen waarde; ontbreken in de
    uitvoer is dan geen verlies. Een echte waarde die verdwijnt blijft een fout."""
    src = {**make_probe("/m/lichtval.mkv", hdr10=True), "content_light": "0,0",
           "md_ruw": {"red_x": 0.708, "max_luminance": 1000.0, "min_luminance": 0.0}}
    out = {**av1_output(src), "content_light": None,
           "md_ruw": {"red_x": 0.708, "max_luminance": 1000.0}}
    v = verify_output(src, out)
    assert v.passed, [c for c in v.checks if not c.passed]
    real = {**src, "content_light": "1000,400"}
    assert not verify_output(real, out).passed
