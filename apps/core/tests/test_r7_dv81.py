"""Rapport 7 §4.3: DV 8.1 → HDR10 via de GPU als bewuste keuze (standaard DV behouden)."""

from __future__ import annotations

from test_dv import CAPS, DV7MEL, DV81, DV84, pol

from mediaguard.dv import policy as dv_policy
from mediaguard.dv import rules
from mediaguard.encoding.client import wire_policy


def test_standaard_blijft_dv_behouden() -> None:
    assert dv_policy.DvPolicy(allow=True, mode="auto_after_verify", allowed_profiles=["8.1"],
                              hdr10plus_mode="skip").dv81_route == "keep_dv"
    d = rules.decide(DV81, pol(mode="auto_after_verify"), CAPS)
    assert d.route != "dv81_naar_hdr10" and d.encoder_policy()["dv81_to_hdr10"] is False


def test_dv81_naar_hdr10_ook_als_dv_omzetten_uit_staat() -> None:
    d = rules.decide(DV81, pol(allow=False, dv81_route="hdr10_gpu"), CAPS)
    assert d.action == "convert" and d.route == "dv81_naar_hdr10"
    wire = d.encoder_policy()
    assert wire["dv81_to_hdr10"] is True and wire["dv_allowed_profiles"] == ["8.1"]
    assert wire_policy({"encoder": "auto", **wire})["dv81_to_hdr10"] is True
    assert "DV-laag vervalt" in " ".join(d.warnings)


def test_andere_profielen_niet_via_deze_route() -> None:
    for probe in (DV84, DV7MEL):
        d = rules.decide(probe, pol(mode="auto_after_verify", dv81_route="hdr10_gpu"), CAPS)
        assert d.route != "dv81_naar_hdr10"


def test_core_verificatie_eist_dat_dv_weg_is() -> None:
    from test_encoding_support import av1_output

    from mediaguard.dv.verify import verify_output

    out_dv = av1_output(DV81)
    with_dv = {**out_dv, "dv_profile": 10}
    without_dv = {**out_dv, "dv_profile": 0}
    bad = verify_output(DV81, with_dv, dv_to_hdr10=True)
    good = verify_output(DV81, without_dv, dv_to_hdr10=True)
    names_bad = {c.name: c.passed for c in bad.checks}
    names_good = {c.name: c.passed for c in good.checks}
    assert names_bad["dv_removed"] is False and names_good["dv_removed"] is True
    assert "rpu_present" not in names_good
