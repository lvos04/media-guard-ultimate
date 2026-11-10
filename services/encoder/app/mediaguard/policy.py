"""Vertaal de per-job MediaGuard-policy van core naar de bestaande beslisboom.

Core (UI-first, DEEL Q) is eigenaar van de DV-schakelaar en zijn overerving
(Globaal → Bibliotheek → Profiel → Titel → Job). De encoder ontvangt per job de
uiteindelijk geldende policy en vertaalt die naar profielsleutels voor
``beslissen.beslis``. Er is geen tweede beslisboom: dezelfde Python-functie
beslist, met uitsluitend opt-in sleutels die het oude gedrag ongemoeid laten.
"""

from __future__ import annotations

from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator

from app.media.beslissen import STANDAARD

DvMode = Literal["keep_original", "trial_copy", "auto_after_verify", "experimental_keep_source"]
DvRoute = Literal["8.1", "8.4", "7_mel", "7_fel"]
Hdr10PlusMode = Literal["skip", "convert_verified", "hdr10_fallback_explicit"]
HDR10PLUS_INST = {
    "skip": "skip",
    "convert_verified": "converteer_geverifieerd",
    "hdr10_fallback_explicit": "hdr10_fallback",
}
QualityProfileName = Literal["archiefkwaliteit", "gebalanceerd", "compact", "custom"]

# Taalnormalisatie (P6): nl/nld/dut, en/eng, ja/jpn; leeg/ongeldig -> und.
_TAAL = {
    "nl": "nld",
    "nld": "nld",
    "dut": "nld",
    "nederlands": "nld",
    "dutch": "nld",
    "en": "eng",
    "eng": "eng",
    "english": "eng",
    "ja": "jpn",
    "jpn": "jpn",
    "jap": "jpn",
    "japanese": "jpn",
}


def _default_dv_routes() -> list[DvRoute]:
    return ["8.1", "8.4"]


def normalize_language(value: object) -> str:
    text = str(value or "").strip().lower()
    if not text:
        return "und"
    if text in _TAAL:
        return _TAAL[text]
    if len(text) == 3 and text.isalpha():
        return text
    return "und"


class AudioPolicy(BaseModel):
    model_config = ConfigDict(extra="forbid")

    keep_languages: list[str] = Field(default_factory=lambda: ["eng", "jpn", "nld"])
    # P6: standaard NIETS verwijderen. Opschonen alleen met expliciete
    # per-library policy (keep_all=false) én geverifieerde output.
    keep_all: bool = True
    keep_commentary: bool = True


class SubtitlePolicy(BaseModel):
    model_config = ConfigDict(extra="forbid")

    keep_all: bool = True
    keep_languages: list[str] = Field(default_factory=lambda: ["nld", "eng"])


class MediaGuardPolicy(BaseModel):
    """Exact het policy-object uit packages/contracts/encoder-bridge.md (+ optioneel)."""

    model_config = ConfigDict(extra="forbid")

    encoder: Literal["auto", "cpu", "gpu"] = "auto"
    crf: int | None = Field(default=None, ge=1, le=63)
    cq: int | None = Field(default=None, ge=1, le=51)
    preset: int | None = Field(default=None, ge=0, le=13)
    film_grain: int | None = Field(default=None, ge=0, le=50)
    target: Literal["staging"] = "staging"
    # DEEL Q: nieuwe productie-installaties starten in "Testen op kopie"; zonder
    # expliciete keuze van core blijft DV echter het origineel.
    dv_mode: DvMode = "keep_original"
    dv_allowed_profiles: list[DvRoute] = Field(default_factory=_default_dv_routes)
    # Q1.1: eigen HDR10+-schakelaar. "skip" (default) houdt het origineel;
    # "convert_verified" = libaom-route met per-frame verificatie van ST 2094-40;
    # "hdr10_fallback_explicit" = bewust alleen statisch HDR10 (metadataverlies,
    # altijd als waarschuwing gelabeld, nooit default). `hdr10plus` is de
    # contractnaam; `hdr10plus_mode` dezelfde waarde onder core's settingnaam.
    hdr10plus: Hdr10PlusMode | None = None
    hdr10plus_mode: Hdr10PlusMode | None = None
    audio_policy: AudioPolicy = Field(default_factory=AudioPolicy)
    subtitle_policy: SubtitlePolicy = Field(default_factory=SubtitlePolicy)
    min_saving_percent: float = Field(default=15.0, ge=0, le=95)
    quality_profile: QualityProfileName = "gebalanceerd"
    # MediaGuard heeft het bestand al gekozen (eigen drempels per resolutie/profiel).
    # Dan past de encoder geen eigen bitratedrempels meer toe; de veiligheid ná de
    # encode (resultaat moet echt kleiner zijn, min_saving_percent) blijft gelden.
    # Rapport 7 §4.3: DV 8.1 bewust als HDR10 via de GPU (DV vervalt). Standaard uit.
    dv81_to_hdr10: bool = False
    decided_by: Literal["mediaguard", "encoder"] | None = None
    skip_thresholds: bool | None = None

    @model_validator(mode="after")
    def _hdr10plus_consistent(self) -> MediaGuardPolicy:
        if self.hdr10plus and self.hdr10plus_mode and self.hdr10plus != self.hdr10plus_mode:
            raise ValueError("hdr10plus en hdr10plus_mode spreken elkaar tegen")
        return self

    @property
    def effective_hdr10plus_mode(self) -> Hdr10PlusMode:
        return self.hdr10plus_mode or self.hdr10plus or "skip"


def thresholds_skipped(policy: MediaGuardPolicy, *, container_mode: bool) -> bool:
    """Expliciet veld wint; anders: MediaGuard beslist (containermodus = altijd)."""

    if policy.skip_thresholds is not None:
        return policy.skip_thresholds
    if policy.decided_by is not None:
        return policy.decided_by == "mediaguard"
    return container_mode


def replace_eligible(policy: MediaGuardPolicy, trial_only: bool) -> bool:
    """Mag core dit resultaat ooit als vervanging activeren?

    trial_copy en experimental_keep_source maken altijd een aparte kopie; de
    bron blijft. Alleen auto_after_verify (of een niet-DV-bestand) is
    vervangbaar, en dan nog pas na core's eigen recovery-/canary-gates.
    """

    if trial_only:
        return False
    return policy.dv_mode not in {"trial_copy", "experimental_keep_source"}


def to_profile_inst(
    policy: MediaGuardPolicy,
    *,
    gpu_lane_available: bool,
    skip_thresholds: bool = False,
) -> dict[str, Any]:
    """Profielsleutels voor beslissen.beslis/commandos (alleen overrides)."""

    inst: dict[str, Any] = {
        "dv_modus": "skip" if policy.dv_mode == "keep_original" else "av1",
        "dv_profielen": list(policy.dv_allowed_profiles),
        "dv_fel_experimenteel": policy.dv_mode == "experimental_keep_source",
        "dv81_naar_hdr10": bool(policy.dv81_to_hdr10),
        "hdr10plus_modus": HDR10PLUS_INST[policy.effective_hdr10plus_mode],
        "audio_alles_behouden": policy.audio_policy.keep_all,
        "ondertitels_alles_behouden": policy.subtitle_policy.keep_all,
        "audio_talen": sorted({normalize_language(x) for x in policy.audio_policy.keep_languages}),
        "ondertitel_talen": sorted(
            {normalize_language(x) for x in policy.subtitle_policy.keep_languages}
        ),
        "commentaar_weg": not policy.audio_policy.keep_commentary,
        # MediaGuard voegt nooit twee sporen van dezelfde taal samen tot één:
        # een stereo- naast een 5.1-mix of een AD-spoor is geen dubbel spoor.
        "beste_per_taal": False,
        # P4: kwaliteitsgestuurd (CRF/CQ); doelbitrate alleen op expliciete keuze.
        "doel_bitrate_2160_kbps": 0,
        "doel_bitrate_1080_kbps": 0,
        "doel_bitrate_720_kbps": 0,
    }
    if skip_thresholds:
        # Geen tweede keuze door de encoder: "18430 kbps onder drempel 20000" mag een
        # door MediaGuard gekozen bestand niet meer overslaan.
        inst.update({"drempel_2160": 0, "drempel_1080": 0, "drempel_720": 0,
                     "drempels_negeren": True})
    if policy.crf is not None:
        inst.update({key: policy.crf for key in (
            "crf_2160", "crf_1080", "crf_720", "crf_2160_hdr", "crf_1080_hdr", "crf_720_hdr")})
    if policy.preset is not None:
        inst.update(
            {
                "preset_2160": policy.preset,
                "preset_1080": policy.preset,
                "preset_720": policy.preset,
            }
        )
    if policy.cq is not None:
        inst["gpu_quality"] = policy.cq
    if policy.film_grain is not None:
        inst["film_grain"] = policy.film_grain
    if policy.encoder == "cpu" or not gpu_lane_available:
        inst.update({"gpu_voor_sdr_1080": False, "gpu_voor_sdr_4k": False, "gpu_voor_hdr10": False})
    else:
        # auto/gpu: SDR (ook 4K) en statisch HDR10 naar de Arc. av1_qsv draagt geen mastering
        # display; dat zet mkvmerge daarna terug (herstel_hdr_na) en de verificatie controleert
        # het. Klopt het niet, dan gaat de job één keer opnieuw via de CPU. DV en HDR10+ worden
        # in beslissen.py al eerder naar de CPU gestuurd.
        inst.update({"gpu_voor_sdr_1080": True, "gpu_voor_sdr_4k": True, "gpu_voor_hdr10": True})
    return inst


def effective_inst(overrides: dict[str, Any]) -> dict[str, Any]:
    return {**STANDAARD, **overrides}


def normalized_probe(info: dict[str, Any]) -> dict[str, Any]:
    """Kopie met genormaliseerde taalcodes; indices blijven gelijk."""

    copy = dict(info)
    copy["audio"] = [
        {**item, "taal": normalize_language(item.get("taal"))} for item in info["audio"]
    ]
    copy["subs"] = [{**item, "taal": normalize_language(item.get("taal"))} for item in info["subs"]]
    return copy
