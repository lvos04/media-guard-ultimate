"""Vergelijk een encoderesultaat uitsluitend met vóór de encode opgeslagen metadata."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Any

from app.media.beslissen import Besluit
from app.media.dv_controle import controleer_dv, kleur_gelijk, mediainfo_kleur
from app.media.hdr10plus_controle import controleer_hdr10plus
from app.media.probe import probe


@dataclass(frozen=True, slots=True)
class VerificationResult:
    passed: bool
    reason: str
    result_probe: dict[str, Any] | None
    mastering_equal: bool
    # MediaGuard: teruggelezen DV-metadata (leeg als het besluit geen DV droeg).
    dv: dict[str, Any] | None = None
    # MediaGuard: per-frame HDR10+-vergelijking (alleen bij hdr10plus_route "behouden").
    hdr10plus: dict[str, Any] | None = None


def _duration_ok(source: Any, result: Any) -> bool:
    """Speelduur mag hooguit 1 s of 1 % afwijken (containerafronding).

    Bekende bronduur maar onbekende resultaatduur is níet goed (verbeterpunt 29): een
    afgebroken of kapot resultaat zonder duur ging anders ongezien door."""

    try:
        voor = float(source or 0)
    except (TypeError, ValueError):
        return True
    try:
        na = float(result or 0)
    except (TypeError, ValueError):
        na = 0.0
    if voor <= 0:
        return True  # niets om mee te vergelijken
    if na <= 0:
        return False
    return abs(voor - na) <= max(1.0, voor * 0.01)


def _known_duration(value: Any) -> bool:
    try:
        return float(value or 0) > 0
    except (TypeError, ValueError):
        return False


def _content_light_equal(source: object, result: object) -> bool:
    """Behandel uitsluitend ontbrekend en 0,0 als dezelfde lege CLL-waarde."""

    source_value = str(source or "")
    result_value = str(result or "")
    if source_value == result_value:
        return True
    # MaxCLL=0 en MaxFALL=0 bevatten geen gemeten lichtinformatie. libsvtav1
    # accepteert die waarden, maar laat de lege record in AV1 weg. Niet-nul
    # waarden blijven hieronder bewust exact vergeleken.
    empty_values = {"", "0,0"}
    return source_value in empty_values and result_value in empty_values


def verify_result(
    source_probe: dict[str, Any],
    result_path: Path,
    source_size: int,
    decision: Besluit,
) -> VerificationResult:
    result = probe(str(result_path))
    if result is None:
        return VerificationResult(False, "resultaat is niet leesbaar", None, False)
    if bool(source_probe.get("hdr10plus")) and not decision.hdr10plus_route:
        return VerificationResult(False, "HDR10+ had nooit uitgevoerd mogen worden", result, False)
    mastering_equal = source_probe.get("mastering_display") == result.get(
        "mastering_display"
    ) and _content_light_equal(
        source_probe.get("content_light"), result.get("content_light")
    )
    if bool(source_probe.get("is_hdr10")) and (
        not bool(result.get("is_hdr10")) or not mastering_equal
    ):
        return VerificationResult(
            False, "HDR10 mastering display of content light is gewijzigd", result, False
        )
    if getattr(decision, "dv_naar_hdr10", False):
        # Rapport 7 §4.3: bewust DV 8.1 → HDR10. Dan mag er géén DV-restant in zitten
        # (een half DV-bestand toont op een DV-tv verkeerde kleuren).
        if int(result.get("dv_profile") or 0) or result.get("dv_rpu_frames") is True:
            return VerificationResult(False, "DV 8.1 → HDR10: er zit nog Dolby Vision in het "
                                      "resultaat", result, mastering_equal)
    elif int(source_probe.get("dv_profile") or 0) and not int(result.get("dv_profile") or 0):
        return VerificationResult(False, "Dolby Vision-RPU is verdwenen", result, mastering_equal)
    elif (int(source_probe.get("dv_profile") or 0) and source_probe.get("dv_rpu_frames") is True
            and result.get("dv_rpu_frames") is False):
        # Het configuratierecord staat er wel, maar de beelden dragen geen RPU meer: een
        # speler toont dan geen Dolby Vision. Alleen als ffprobe het bij de bron wél zag.
        return VerificationResult(
            False, "Dolby Vision-configuratie aanwezig, maar de beelden missen hun RPU",
            result, mastering_equal)
    if int(result.get("size") or result_path.stat().st_size) > source_size:
        return VerificationResult(False, "resultaat is groter dan de bron", result, mastering_equal)
    # Alleen een bron mét audio moet een resultaat met audio opleveren.
    if source_probe.get("audio") and not result.get("audio"):
        return VerificationResult(False, "resultaat bevat geen audiospoor", result, mastering_equal)
    if decision.audio_houden and len(result["audio"]) != len(decision.audio_houden):
        return VerificationResult(
            False, "aantal bewaarde audiosporen wijkt af", result, mastering_equal
        )
    if decision.attachments_houden and int(result.get("attachments") or 0) < int(
        source_probe.get("attachments") or 0
    ):
        return VerificationResult(
            False, "ondertitellettertypen zijn verdwenen", result, mastering_equal
        )
    # ── MediaGuard-uitbreidingen (alleen strenger, nooit soepeler) ──────────
    if decision.subs_houden and "subs" in result and len(result["subs"]) != len(
        decision.subs_houden
    ):
        return VerificationResult(
            False, "aantal bewaarde ondertitelsporen wijkt af", result, mastering_equal
        )
    if not _duration_ok(source_probe.get("duration"), result.get("duration")):
        reason = ("speelduur wijkt af van de bron" if _known_duration(result.get("duration"))
                  else "speelduur van het resultaat is onbekend")
        return VerificationResult(False, reason, result, mastering_equal)
    if bool(source_probe.get("is_hdr10")) or int(source_probe.get("dv_profile") or 0):
        kleur_ok, kleur_reden = kleur_gelijk(source_probe.get("kleur"), result.get("kleur"))
        if not kleur_ok:
            return VerificationResult(False, kleur_reden, result, mastering_equal)
        container = mediainfo_kleur(result_path) if result_path.exists() else None
        if container is not None and container["conflicts"]:
            return VerificationResult(
                False,
                "container- en bitstreamkleur verschillen: " + "; ".join(container["conflicts"]),
                result,
                mastering_equal,
            )
    dv_details: dict[str, Any] | None = None
    if decision.dolby_vision:
        dv_ok, dv_reden, dv_details = controleer_dv(source_probe, result_path)
        if not dv_ok:
            return VerificationResult(False, dv_reden, result, mastering_equal, dv_details)
    hdr10plus_details: dict[str, Any] | None = None
    if bool(source_probe.get("hdr10plus")) and decision.hdr10plus_route == "behouden":
        ok, reden, hdr10plus_details = controleer_hdr10plus(
            str(source_probe.get("pad") or ""),
            result_path,
            float(source_probe.get("duration") or 0),
        )
        if not ok:
            return VerificationResult(
                False, reden, result, mastering_equal, dv_details, hdr10plus_details
            )
    return VerificationResult(
        True,
        "metadata en verplichte sporen zijn behouden",
        result,
        mastering_equal,
        dv_details,
        hdr10plus_details,
    )
