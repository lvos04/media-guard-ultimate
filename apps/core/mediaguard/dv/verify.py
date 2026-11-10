"""Verplichte, niet-schakelbare verificatie van een DV/HDR-resultaat tegen de probe van
vóór de encode (AV1-spec regel 3: nooit het origineel opnieuw inspecteren na vervanging).

Ontbrekende RPU, gewijzigde basislaag of kleurwaarden, verloren streams of HDR10+-verlies
→ verificatiefout; de bron blijft staan.
"""

from __future__ import annotations

import re
from dataclasses import asdict, dataclass, field
from typing import Any

from mediaguard.dv.rules import input_profile

MD_TOLERANCE = 0.0005
# Luminantie: HEVC-SEI telt in 0,0001 cd/m², AV1-metadata in 2^-14 (min) en 2^-8 (max);
# omrekenen geeft kleine afwijkingen die geen echte verandering zijn.
LUMA_REL_TOLERANCE = 0.01
LUMA_KEYS = ("max_luminance", "min_luminance")
# ffprobe/mediainfo-schrijfwijzen voor dezelfde waarde
COLOUR_ALIASES = {
    "bt2020nc": "bt2020nc", "bt2020_ncl": "bt2020nc", "bt2020-ncl": "bt2020nc",
    "bt2020c": "bt2020c", "bt2020_cl": "bt2020c",
    "smpte2084": "smpte2084", "pq": "smpte2084", "st2084": "smpte2084",
    "arib-std-b67": "arib-std-b67", "hlg": "arib-std-b67",
    "bt709": "bt709", "bt2020": "bt2020", "bt2020-10": "bt2020-10", "bt2020-12": "bt2020-12",
}


def _colour(value: Any) -> str:
    v = str(value or "").strip().lower().replace(" ", "")
    return COLOUR_ALIASES.get(v, v)


def _cll(value: Any) -> tuple[int, int] | None:
    """'1000,400', 'MaxCLL=1000, MaxFALL=400' of {'max_content':1000,…} → (1000, 400)."""
    if isinstance(value, dict):
        nums = [str(value[k]) for k in ("max_content", "max_average")
                if value.get(k) is not None]
    else:
        nums = re.findall(r"\d+", str(value or ""))
    try:
        pair = (int(nums[0]), int(nums[1])) if len(nums) >= 2 else None
    except (TypeError, ValueError):
        return None
    # 0,0 betekent 'onbekend' (HEVC/AV1 schrijven dan vaak niets): gelijk aan ontbreken
    return None if pair == (0, 0) else pair


def _md_diffs(src_md: dict[str, Any], out_md: dict[str, Any]) -> list[str]:
    diffs = []
    for k, v in src_md.items():
        if k not in out_md and float(v or 0) == 0:
            continue  # 0 in de bron = geen waarde; ontbreken in de uitvoer is gelijkwaardig
        a, b = float(v), float(out_md.get(k, -1))
        if k in LUMA_KEYS:
            ok = abs(a - b) <= max(MD_TOLERANCE, abs(a) * LUMA_REL_TOLERANCE)
        else:
            ok = abs(a - b) <= MD_TOLERANCE
        if not ok:
            diffs.append(f"{k} {a:g} → {b:g}")
    return diffs


@dataclass
class Check:
    name: str
    passed: bool
    detail_nl: str


@dataclass
class Verification:
    passed: bool
    checks: list[Check] = field(default_factory=list)
    rpu_preserved: bool | None = None
    all_picture_info_preserved: bool | None = None

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


def _dv_out_expected(src_profile: str | None) -> tuple[int, int] | None:
    """AV1 met DV = profiel 10; compat-ID volgt de basislaag (8.1 → 10.1, 8.4 → 10.4)."""
    if src_profile in ("8.1", "7_mel", "7_fel", "7_unknown"):
        return (10, 1)
    if src_profile == "8.4":
        return (10, 4)
    if src_profile == "5":
        return (10, 0)
    return None


def verify_output(source: dict[str, Any], output: dict[str, Any], *,
                  keep_all_audio: bool = True, keep_all_subtitles: bool = True,
                  encoder_verification: dict[str, Any] | None = None,
                  hdr10plus_mode: str = "convert_verified",
                  dv_to_hdr10: bool = False) -> Verification:
    checks: list[Check] = []
    prof = input_profile(source)

    if encoder_verification is not None:
        ok = bool(encoder_verification.get("passed"))
        failed = [c.get("name") for c in encoder_verification.get("checks") or []
                  if not c.get("passed")]
        checks.append(Check("encoder_verification", ok,
                            "Encoderverificatie geslaagd" if ok else
                            "Encoderverificatie mislukt: " + ", ".join(map(str, failed))))

    checks.append(Check("codec", str(output.get("codec", "")).lower() == "av1",
                        f"Uitvoercodec {output.get('codec') or 'onbekend'}"))
    same_dims = (int(source.get("width") or 0), int(source.get("height") or 0)) == (
        int(output.get("width") or 0), int(output.get("height") or 0))
    checks.append(Check("base_layer_dimensions", same_dims,
                        f"Resolutie {source.get('width')}x{source.get('height')} → "
                        f"{output.get('width')}x{output.get('height')}"))

    rpu: bool | None = None
    if prof and dv_to_hdr10:
        # Rapport 7 §4.3: DV 8.1 bewust als HDR10. Dan moet DV juist weg zijn (een halve DV-
        # laag geeft op een DV-tv verkeerde kleuren); de HDR10-checks hieronder blijven.
        gone = not int(output.get("dv_profile") or 0)
        checks.append(Check("dv_removed", gone,
                            "Dolby Vision bewust verwijderd (DV 8.1 → HDR10)" if gone else
                            "Dolby Vision zit nog in het resultaat (DV 8.1 → HDR10 gevraagd)"))
    elif prof:
        out_p, out_c = int(output.get("dv_profile") or 0), int(output.get("dv_compat") or 0)
        rpu = out_p > 0
        checks.append(Check("rpu_present", rpu,
                            f"Dolby Vision RPU {'aanwezig' if rpu else 'ONTBREEKT'} "
                            f"(profiel {out_p or '-'})"))
        expected = _dv_out_expected(prof)
        if expected and rpu:
            ok = (out_p, out_c) == expected
            checks.append(Check("dv_profile_mapping", ok,
                                f"Verwacht DV {expected[0]}.{expected[1]}, gekregen "
                                f"{out_p}.{out_c}"))

    # HDR10-basislaag / kleurwaarden
    if source.get("is_hdr10") or source.get("mastering_display"):
        checks.append(Check("hdr10_static_metadata", bool(output.get("is_hdr10")),
                            "HDR10 mastering-display " + (
                                "behouden" if output.get("is_hdr10") else "VERLOREN")))
        src_md, out_md = source.get("md_ruw") or {}, output.get("md_ruw") or {}
        diffs = _md_diffs(src_md, out_md)
        src_empty = not any(float(v or 0) for v in src_md.values())
        md_ok = not diffs and (bool(out_md) or src_empty)
        checks.append(Check("mastering_display_values", md_ok,
                            "Mastering-display-waarden gelijk" if md_ok else
                            "Afwijkende kleurwaarden: " + ", ".join(diffs or ["ontbreken"])))
        if _cll(source.get("content_light")) is not None:
            same = _cll(source.get("content_light")) == _cll(output.get("content_light"))
            checks.append(Check("content_light_level", same,
                                f"MaxCLL/MaxFALL {source.get('content_light')} → "
                                f"{output.get('content_light') or 'ontbreekt'}"))
    # De encoder-probe levert kleur in 'kleur' {primaries, transfer, matrix, pix_fmt}; oudere
    # probes platte velden. Bitdiepte/pix_fmt bewust niet vergelijken: 8-bit wordt 10-bit AV1.
    src_k, out_k = source.get("kleur") or {}, output.get("kleur") or {}
    for key, flat in (("primaries", "color_primaries"), ("transfer", "color_transfer"),
                      ("matrix", "color_space")):
        a = src_k.get(key, source.get(flat))
        b = out_k.get(key, output.get(flat))
        if a not in (None, "", "unknown"):
            checks.append(Check(flat, _colour(a) == _colour(b), f"{flat}: {a} → {b}"))
    if source.get("hdr10plus") and hdr10plus_mode == "hdr10_fallback_explicit":
        checks.append(Check("hdr10plus_preserved", True, "HDR10+ bewust als HDR10 omgezet "
                            "(expliciete keuze): dynamische metadata niet meegenomen"))
    elif source.get("hdr10plus"):
        checks.append(Check("hdr10plus_preserved", bool(output.get("hdr10plus")),
                            "HDR10+ dynamische metadata " + (
                                "behouden" if output.get("hdr10plus") else "VERLOREN")))

    # timing en streams
    sd, od = float(source.get("duration") or 0), float(output.get("duration") or 0)
    tol = max(1.0, sd * 0.001)
    checks.append(Check("duration", sd > 0 and abs(sd - od) <= tol,
                        f"Speelduur {sd:.1f}s → {od:.1f}s (tolerantie {tol:.1f}s)"))
    sa = [str(a.get("taal") or a.get("language") or "und") for a in source.get("audio") or []]
    oa = [str(a.get("taal") or a.get("language") or "und") for a in output.get("audio") or []]
    ok = sorted(sa) == sorted(oa) if keep_all_audio else bool(oa) and set(oa) <= set(sa)
    checks.append(Check("audio_tracks", ok, f"Audio ({len(sa)} → {len(oa)}): {sa} → {oa}"))
    ss = [str(s.get("taal") or "und") for s in source.get("subs") or []]
    os_ = [str(s.get("taal") or "und") for s in output.get("subs") or []]
    ok = sorted(ss) == sorted(os_) if keep_all_subtitles else set(os_) <= set(ss)
    checks.append(Check("subtitle_tracks", ok,
                        f"Ondertitels ({len(ss)} → {len(os_)}): {ss} → {os_}"))
    if int(source.get("attachments") or 0) and any(
            str(s.get("codec")) in ("ass", "ssa") for s in source.get("subs") or []):
        ok = int(output.get("attachments") or 0) >= int(source.get("attachments") or 0)
        checks.append(Check("attachments", ok, "Lettertypen (ASS-attachments) "
                            + ("behouden" if ok else "VERLOREN")))

    passed = all(c.passed for c in checks)
    return Verification(passed=passed, checks=checks, rpu_preserved=rpu,
                        all_picture_info_preserved=(False if prof in ("7_fel", "7_unknown")
                                                    else (rpu if prof else None)))
