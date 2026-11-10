"""DEEL Q / P6: de per-job MediaGuard-policy stuurt echt de bestaande beslisboom.

Pure beslis-/commandotests op probe-dicts: geen toolchain nodig.
"""

from __future__ import annotations

from typing import Any

import pytest

from app.media import commandos
from app.media.beslissen import STANDAARD, beslis
from app.mediaguard.policy import (
    MediaGuardPolicy,
    effective_inst,
    normalize_language,
    normalized_probe,
    replace_eligible,
    to_profile_inst,
)


def _info(**overrides: Any) -> dict[str, Any]:
    info: dict[str, Any] = {
        "pad": "/media/films/Film (2024).mkv",
        "codec": "hevc",
        "width": 3840,
        "height": 2160,
        "fps": "24000/1001",
        "duration": 7200.0,
        "size": 40_000_000_000,
        "container": "matroska,webm",
        "kbps": 40000,
        "is_hdr10": True,
        "mastering_display": "G(0.2650,0.6900)B(0.1500,0.0600)R(0.6800,0.3200)"
        "WP(0.3127,0.3290)L(1000.0000,0.0001)",
        "md_ruw": {},
        "content_light": "1000,400",
        "hdr10plus": False,
        "dv_profile": 8,
        "dv_compat": 1,
        "audio": [
            {
                "index": 1,
                "codec": "truehd",
                "kanalen": 8,
                "taal": "eng",
                "titel": "",
                "commentaar": False,
                "bit_rate": 0,
            },
        ],
        "subs": [],
        "attachments": 0,
    }
    info.update(overrides)
    return info


def _decide(info: dict[str, Any], **policy: Any) -> Any:
    parsed = MediaGuardPolicy.model_validate(policy)
    inst = effective_inst(to_profile_inst(parsed, gpu_lane_available=False))
    return beslis(normalized_probe(info), inst)


# ── Q3 acceptatietest 1 en 2 ────────────────────────────────────────────────


def test_dv81_toggle_uit_houdt_origineel() -> None:
    besluit = _decide(_info(), dv_mode="keep_original")
    assert besluit.actie == "overslaan"
    assert "dv_modus staat op skip" in besluit.reden


@pytest.mark.parametrize("mode", ["trial_copy", "auto_after_verify", "experimental_keep_source"])
def test_dv81_toggle_aan_geeft_uitvoerbare_libsvtav1_job_met_expliciete_dv_vlag(mode: str) -> None:
    info = _info()
    parsed = MediaGuardPolicy(dv_mode=mode)  # type: ignore[arg-type]
    inst = effective_inst(to_profile_inst(parsed, gpu_lane_available=True))
    besluit = beslis(normalized_probe(info), inst)
    assert besluit.actie == "hercoderen"
    assert (besluit.baan, besluit.encoder) == ("cpu", "libsvtav1")
    assert besluit.dolby_vision is True
    assert besluit.dv_route == "8.1"
    assert besluit.apart_resultaat is False
    command = commandos.encode_cpu(info, besluit, inst, "/in.mkv", "/out.mkv")
    # De expliciete 1 is verplicht: "auto" gooit de RPU stil weg (spec/02 #11).
    position = command.index("-dolbyvision")
    assert command[position + 1] == "1"
    assert command[command.index("-c:v") + 1] == "libsvtav1"


def test_dv84_route_volgt_de_allowlist() -> None:
    info = _info(dv_compat=4, is_hdr10=False, mastering_display="", content_light="")
    assert _decide(info, dv_mode="trial_copy").dv_route == "8.4"
    besluit = _decide(info, dv_mode="trial_copy", dv_allowed_profiles=["8.1"])
    assert besluit.actie == "overslaan"
    assert "8.4 staat niet in de toegestane profielen" in besluit.reden


def test_dv7_mel_gaat_via_dovi_tool_naar_81() -> None:
    info = _info(dv_profile=7, dv_compat=6, dv_el_type="MEL")
    besluit = _decide(info, dv_mode="auto_after_verify", dv_allowed_profiles=["8.1", "7_mel"])
    assert besluit.actie == "hercoderen"
    assert besluit.dv7_omzetten is True
    assert besluit.dv_route == "7_mel"
    assert besluit.apart_resultaat is False


def test_dv7_mel_zonder_toestemming_blijft_origineel() -> None:
    info = _info(dv_profile=7, dv_compat=6, dv_el_type="MEL")
    assert _decide(info, dv_mode="auto_after_verify").actie == "overslaan"


@pytest.mark.parametrize("el_type", ["FEL", None])
def test_dv7_fel_of_onbekend_nooit_stil_naar_single_layer(el_type: str | None) -> None:
    info = _info(dv_profile=7, dv_compat=6, dv_el_type=el_type)
    besluit = _decide(
        info, dv_mode="auto_after_verify", dv_allowed_profiles=["8.1", "7_mel", "7_fel"]
    )
    assert besluit.actie == "overslaan"
    assert "FEL" in besluit.reden


def test_dv7_fel_experimenteel_alleen_als_apart_resultaat_met_waarschuwing() -> None:
    info = _info(dv_profile=7, dv_compat=6, dv_el_type="FEL")
    policy = {"dv_mode": "experimental_keep_source", "dv_allowed_profiles": ["7_fel"]}
    besluit = _decide(info, **policy)
    assert besluit.actie == "hercoderen"
    assert besluit.apart_resultaat is True
    assert besluit.dv7_omzetten is True
    assert any("enhancement layer" in w for w in besluit.waarschuwingen)
    assert replace_eligible(MediaGuardPolicy.model_validate(policy), trial_only=False) is False


def test_dv5_blijft_niet_ondersteund_ongeacht_toggles() -> None:
    info = _info(dv_profile=5, dv_compat=0)
    besluit = _decide(
        info,
        dv_mode="experimental_keep_source",
        dv_allowed_profiles=["8.1", "8.4", "7_mel", "7_fel"],
    )
    assert besluit.actie == "overslaan"
    assert "profiel 5" in besluit.reden


def test_hdr10plus_met_dv_wordt_altijd_overgeslagen() -> None:
    besluit = _decide(_info(hdr10plus=True), dv_mode="auto_after_verify")
    assert besluit.actie == "overslaan"
    assert "HDR10+" in besluit.reden


def test_oude_profielen_zonder_allowlist_houden_hun_gedrag() -> None:
    """Native installaties: dv_modus=av1 zonder dv_profielen werkt als voorheen.

    Behalve profiel 7 zonder bewezen MEL: de oude route kent geen apart
    resultaat en zou een FEL stil naar single layer brengen; dat wordt overslaan.
    """

    inst = {**STANDAARD, "dv_modus": "av1"}
    besluit = beslis(_info(dv_profile=7, dv_compat=6, dv_el_type="MEL"), inst)
    assert besluit.actie == "hercoderen"
    assert besluit.dv7_omzetten is True
    assert besluit.dv_route == ""
    onbekend = beslis(_info(dv_profile=7, dv_compat=6), inst)
    assert onbekend.actie == "overslaan" and "MEL" in onbekend.reden


def test_replace_eligible_per_dv_mode() -> None:
    assert replace_eligible(MediaGuardPolicy(dv_mode="auto_after_verify"), False) is True
    assert replace_eligible(MediaGuardPolicy(dv_mode="keep_original"), False) is True
    assert replace_eligible(MediaGuardPolicy(dv_mode="trial_copy"), False) is False
    assert replace_eligible(MediaGuardPolicy(dv_mode="auto_after_verify"), True) is False


# ── P6: audio inventory vóór cleanup ────────────────────────────────────────


def _dual_audio_info() -> dict[str, Any]:
    def audio(index: int, taal: str, titel: str = "", commentaar: bool = False) -> dict[str, Any]:
        return {
            "index": index,
            "codec": "aac",
            "kanalen": 2,
            "taal": taal,
            "titel": titel,
            "commentaar": commentaar,
            "bit_rate": 0,
        }

    return _info(
        width=1280,
        height=720,
        kbps=9000,
        is_hdr10=False,
        dv_profile=0,
        dv_compat=0,
        codec="h264",
        audio=[
            audio(1, "eng"),
            audio(2, "jpn"),
            audio(3, "dut"),
            audio(4, "eng", "Director Commentary", True),
            audio(5, "eng", "Audio Description"),
        ],
        subs=[
            {"index": 6, "codec": "subrip", "taal": "eng", "forced": False, "commentaar": False},
            {"index": 7, "codec": "subrip", "taal": "eng", "forced": True, "commentaar": False},
            {"index": 8, "codec": "ass", "taal": "eng", "forced": False, "commentaar": False},
        ],
        attachments=1,
    )


def test_mediaguard_standaard_houdt_dual_audio_nl_ad_commentaar_en_forced() -> None:
    besluit = _decide(_dual_audio_info())
    assert besluit.audio_houden == [1, 2, 3, 4, 5]
    assert besluit.subs_houden == [6, 7, 8]
    assert besluit.attachments_houden is True


def test_oude_standaard_zou_wel_sporen_verwijderen() -> None:
    """Bewijs dat de MediaGuard-standaard een echte gedragswijziging is (P2-tabel)."""

    besluit = beslis(_dual_audio_info(), STANDAARD)
    assert len(besluit.audio_houden) < 5


def test_expliciete_cleanup_policy_normaliseert_taal_en_kiest_alleen_gevraagd() -> None:
    besluit = _decide(
        _dual_audio_info(),
        audio_policy={"keep_all": False, "keep_languages": ["nl"], "keep_commentary": False},
        subtitle_policy={"keep_all": True},
    )
    assert besluit.audio_houden == [3]  # 'dut' -> nld
    assert besluit.subs_houden == [6, 7, 8]


def test_taalnormalisatie() -> None:
    assert [normalize_language(x) for x in ("nl", "dut", "NLD", "en", "ja", "", "??")] == [
        "nld",
        "nld",
        "nld",
        "eng",
        "jpn",
        "und",
        "und",
    ]


def test_policy_weigert_onbekende_velden() -> None:
    with pytest.raises(ValueError):
        MediaGuardPolicy.model_validate({"dv_mode": "force"})
    with pytest.raises(ValueError):
        MediaGuardPolicy.model_validate({"hdr10plus": "allow"})


# ── HDR10+ (Q1.1: eigen schakelaar) ─────────────────────────────────────────


def _hdr10plus_info(**overrides: Any) -> dict[str, Any]:
    return _info(dv_profile=0, dv_compat=0, hdr10plus=True, **overrides)


def test_hdr10plus_default_blijft_skip() -> None:
    besluit = _decide(_hdr10plus_info())
    assert besluit.actie == "overslaan"
    assert "HDR10+" in besluit.reden


def test_hdr10plus_convert_verified_kiest_libaom_met_hdr10_behoud() -> None:
    besluit = _decide(_hdr10plus_info(), hdr10plus_mode="convert_verified")
    assert besluit.actie == "hercoderen"
    assert (besluit.baan, besluit.encoder) == ("cpu", "libaom-av1")
    assert besluit.hdr10plus_route == "behouden"
    assert besluit.behoud_hdr is True
    info = _hdr10plus_info()
    inst = effective_inst(
        to_profile_inst(MediaGuardPolicy(hdr10plus="convert_verified"), gpu_lane_available=True)
    )
    command = commandos.encode_cpu_aom(info, besluit, inst, "/in.mkv", "/out.mkv")
    assert command[0].endswith("ffmpeg")
    assert command[command.index("-c:v") + 1] == "libaom-av1"
    assert command[command.index("-color_trc") + 1] == "smpte2084"


def test_hdr10plus_fallback_is_expliciet_gelabeld_metadataverlies() -> None:
    besluit = _decide(_hdr10plus_info(), hdr10plus_mode="hdr10_fallback_explicit")
    assert besluit.actie == "hercoderen"
    assert besluit.encoder == "libsvtav1"
    assert besluit.hdr10plus_route == "hdr10_fallback"
    assert any("VERLOREN" in w for w in besluit.waarschuwingen)
    assert "dynamische metadata vervalt" in besluit.reden


def test_dv_met_hdr10plus_wordt_nooit_omgezet() -> None:
    besluit = _decide(
        _info(hdr10plus=True), dv_mode="auto_after_verify", hdr10plus_mode="convert_verified"
    )
    assert besluit.actie == "overslaan"
    assert "Dolby Vision" in besluit.reden


def test_hdr10plus_contractnaam_en_settingnaam_moeten_overeenkomen() -> None:
    assert MediaGuardPolicy(hdr10plus="convert_verified").effective_hdr10plus_mode == (
        "convert_verified"
    )
    assert MediaGuardPolicy().effective_hdr10plus_mode == "skip"
    with pytest.raises(ValueError):
        MediaGuardPolicy(hdr10plus="skip", hdr10plus_mode="convert_verified")


# ── H3: MediaGuard beslist, de encoder kiest niet opnieuw op bitrate ────────


def test_drempels_overgeslagen_als_mediaguard_beslist() -> None:
    from app.mediaguard.policy import MediaGuardPolicy, thresholds_skipped

    assert thresholds_skipped(MediaGuardPolicy(decided_by="mediaguard"), container_mode=False)
    assert thresholds_skipped(MediaGuardPolicy(), container_mode=True)
    assert not thresholds_skipped(MediaGuardPolicy(), container_mode=False)
    assert not thresholds_skipped(
        MediaGuardPolicy(decided_by="mediaguard", skip_thresholds=False), container_mode=True
    )


def test_lage_bitrate_wordt_niet_overgeslagen_na_mediaguard_keuze() -> None:
    from conftest import mg_fixture

    from app.media.beslissen import beslis
    from app.mediaguard.policy import (
        MediaGuardPolicy,
        effective_inst,
        normalized_probe,
        to_profile_inst,
    )
    from app.mediaguard.service import enriched_probe

    info = normalized_probe(enriched_probe(mg_fixture("mg-dual-audio.mkv")))
    info.update({"kbps": 18430, "height": 2160, "width": 3840})
    policy = MediaGuardPolicy(decided_by="mediaguard")
    eigen = beslis(info, effective_inst(to_profile_inst(policy, gpu_lane_available=False)))
    assert eigen.actie != "hercoderen" and "onder drempel 20000" in eigen.reden
    gekozen = beslis(info, effective_inst(
        to_profile_inst(policy, gpu_lane_available=False, skip_thresholds=True)))
    assert gekozen.actie == "hercoderen", gekozen.reden
    # Ook een onbekende bitrate is dan geen reden meer om over te slaan.
    info["kbps"] = 0
    onbekend = beslis(info, effective_inst(
        to_profile_inst(policy, gpu_lane_available=False, skip_thresholds=True)))
    assert onbekend.actie == "hercoderen", onbekend.reden
