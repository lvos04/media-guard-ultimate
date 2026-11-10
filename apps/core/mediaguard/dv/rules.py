"""DV-beslissing per invoerprofiel (Q1.1) — vervangt de oude hardcoded ``dv_modus=skip``.

Een ingeschakelde, technisch ondersteunde route levert een *uitvoerbare* taak op
(Q1.2 laatste punt). Wat nooit schakelbaar is: DV5 zonder bewezen route, FEL-bron
vernietigen, HDR10+-metadata stil verliezen, verificatie overslaan.
"""

from __future__ import annotations

from dataclasses import asdict, dataclass, field
from typing import Any

from mediaguard.dv.policy import MODE_LABELS_NL, DvPolicy

# UI-statussen (Q1 punt 6)
ALLOWED = "Toegestaan"
TRIAL = "Proefconversie"
CHECKED = "Gecontroleerd"
KEEP = "Origineel behouden"
UNSUPPORTED = "Niet ondersteund"
VERIFY_FAILED = "Verificatie mislukt"
RESTORABLE = "Herstelbaar"
UI_STATUSES = (ALLOWED, TRIAL, CHECKED, KEEP, UNSUPPORTED, VERIFY_FAILED, RESTORABLE)


@dataclass
class DvDecision:
    is_dv: bool
    input_profile: str | None
    status: str | None
    action: str                  # none | keep | trial | convert | experimental_separate
    route: str | None
    executable: bool
    replace_source_allowed: bool
    separate_output: bool
    requires_manual_activation: bool
    reason_nl: str
    warnings: list[str] = field(default_factory=list)
    hdr10plus: bool = False
    policy: dict[str, Any] = field(default_factory=dict)

    def encoder_policy(self) -> dict[str, Any]:
        """Vertaling naar het encoder-bridge-contract (``dv_mode`` e.d.)."""
        # 'trial' = proef op een kopie in staging; de bron blijft staan tot een mens activeert
        # (core-gate requires_manual_activation). De encoder kent 'trial_copy' als
        # "nooit vervangen" en weigert dan /committed; daarom gaat een proef die later
        # geactiveerd mag worden als gewone auto_after_verify-job naar de encoder.
        dv_mode = {"keep": "keep_original", "trial": "auto_after_verify",
                   "convert": "auto_after_verify",
                   "experimental_separate": "experimental_keep_source"}.get(
            self.action, "keep_original")
        return {"dv_mode": dv_mode,
                # onbekend EL-type wordt veilig als FEL behandeld; de encoder kent geen
                # '7_unknown' (422)
                "dv_allowed_profiles": [("7_fel" if self.input_profile == "7_unknown"
                                         else self.input_profile)] if self.executable
                and self.input_profile else [],
                "dv_route": self.route,
                "dv81_to_hdr10": self.route == "dv81_naar_hdr10"}

    def to_dict(self) -> dict[str, Any]:
        d = asdict(self)
        d["encoder_policy"] = self.encoder_policy()
        return d


def input_profile(info: dict[str, Any]) -> str | None:
    """Uit de encoder-probe (``dv_profile``, ``dv_compat``, optioneel ``dv_el_type``)."""
    p = int(info.get("dv_profile") or 0)
    if not p:
        return None
    if p == 7:
        el = str(info.get("dv_el_type") or info.get("dv_el") or "").lower()
        if el in ("mel", "fel"):
            return f"7_{el}"
        return "7_unknown"
    if p == 8:
        return f"8.{int(info.get('dv_compat') or 0)}"
    return str(p)


def technical_routes(caps: dict[str, Any] | None) -> set[str] | None:
    """Welke DV-routes kan de encoder technisch? None = onbekend (encoder onbereikbaar)."""
    if caps is None:
        return None
    if isinstance(caps.get("dv_routes"), list):
        # de encoder stuurt objecten {"route": "8.1", "technically_possible": bool, ...};
        # oudere/tests: platte strings
        routes: set[str] = set()
        for r in caps["dv_routes"]:
            if isinstance(r, dict):
                if r.get("route") and r.get("technically_possible", True):
                    routes.add(str(r["route"]))
            else:
                routes.add(str(r))
        return routes
    enc = (caps.get("encoders") or {}).get("libsvtav1") or {}
    svt_dv = bool(caps.get("libsvtav1_dolbyvision") or enc.get("dolbyvision"))
    routes: set[str] = set()
    if svt_dv:
        routes |= {"8.1", "8.4"}
        if caps.get("dovi_tool") and caps.get("mkvmerge"):
            routes |= {"7_mel", "7_fel"}
    return routes


def _confirmed(profile: str | None, confirmations: list[dict[str, Any]]) -> bool:
    if not profile:
        return False
    for c in confirmations:
        if str(c.get("dv_profile")) in (profile, profile.split("_")[0], "10", "any") \
                and str(c.get("codec", "")).lower() in ("av1", "any"):
            return True
    return False


def decide(info: dict[str, Any], policy: DvPolicy, caps: dict[str, Any] | None,
           confirmations: list[dict[str, Any]] | None = None, *,
           show_playback_warnings: bool = True) -> DvDecision:
    confirmations = confirmations or []
    prof = input_profile(info)
    hdr10plus = bool(info.get("hdr10plus"))
    pol = policy.to_dict()
    routes = technical_routes(caps)

    def result(status: str | None, action: str, reason: str, *, route: str | None = None,
               warnings: list[str] | None = None) -> DvDecision:
        executable = action in ("trial", "convert", "experimental_separate", "none")
        return DvDecision(
            is_dv=prof is not None, input_profile=prof, status=status, action=action,
            route=route, executable=executable,
            replace_source_allowed=action in ("trial", "convert", "none"),
            separate_output=action in ("trial", "experimental_separate"),
            requires_manual_activation=action in ("trial", "experimental_separate"),
            reason_nl=reason, warnings=warnings or [], hdr10plus=hdr10plus, policy=pol)

    # ── HDR10+ (eigen schakelaar, standaard overslaan) ────────────────
    if hdr10plus:
        mode = policy.hdr10plus_mode
        if prof is not None:
            return result(UNSUPPORTED, "keep", "Dolby Vision én HDR10+ in één bron: "
                          "bron behouden")
        if mode == "skip":
            return result(KEEP, "keep", "HDR10+: dynamische metadata (ST 2094-40) wordt niet "
                          "stil verwijderd; beleid staat op overslaan")
        if mode == "convert_verified":
            if not caps or not caps.get("hdr10plus_route_verified"):
                return result(UNSUPPORTED, "keep", "HDR10+: geen bewezen route in deze "
                              "encoder" + (f" ({caps.get('hdr10plus_route_reason')})"
                                           if caps and caps.get("hdr10plus_route_reason")
                                           else ""))
            return result(None, "none", "HDR10+ wordt omgezet met behoud van dynamische "
                          "metadata (libaom, per beeld gecontroleerd)",
                          route="hdr10plus_behouden")
        if mode == "hdr10_fallback_explicit":
            return result(None, "none", "HDR10+ wordt als HDR10 omgezet (snel via SVT-AV1; "
                          "standaardbeleid)", route="hdr10_fallback",
                          warnings=["Dynamische HDR10+-metadata gaat VERLOREN (alleen statische "
                                    "HDR10 blijft)"])

    if prof is None:
        return result(None, "none", "Geen Dolby Vision")

    warnings: list[str] = []
    if prof == "7_unknown":
        warnings.append("DV 7: type enhancement layer onbekend; behandeld als FEL (veilig)")
        prof_key = "7_fel"
    else:
        prof_key = prof

    # ── technische grenzen (niet schakelbaar) ─────────────────────────
    if prof_key == "5":
        if routes is None or "5" not in routes:
            return result(UNSUPPORTED, "keep",
                          "DV 5 heeft geen HDR10-basislaag; zonder bewezen omzetroute wordt "
                          "kleur ernstig aangetast. Bron blijft behouden.")
    elif prof_key not in ("8.1", "8.4", "7_mel", "7_fel"):
        return result(UNSUPPORTED, "keep", f"DV-profiel {prof} wordt niet ondersteund")

    if prof_key == "8.1" and getattr(policy, "dv81_route", "keep_dv") == "hdr10_gpu":
        # Rapport 7 §4.3: bewuste keuze; de HDR10-basislaag gaat via de Arc, DV vervalt.
        # Zonder werkende GPU houdt de encoder zelf de gewone DV-route (CPU, DV behouden).
        return result(ALLOWED, "convert", "DV 8.1 wordt als HDR10 via de GPU omgezet "
                      "(keuze in het DV-beleid); Dolby Vision vervalt bewust",
                      route="dv81_naar_hdr10",
                      warnings=["Dolby Vision 8.1 → HDR10: de DV-laag vervalt; op een DV-tv "
                                "speelt het als HDR10"])
    if not policy.allow or policy.mode == "keep_original":
        why = "schakelaar 'Dolby Vision → AV1 toestaan' staat uit" if not policy.allow \
            else "modus 'Origineel behouden'"
        src = policy.trace.get("allow" if not policy.allow else "mode", {})
        level = src.get("source_label_nl")
        return result(KEEP, "keep", f"Origineel behouden: {why}"
                      + (f" (ingesteld op niveau {level})" if level else ""))
    if prof_key not in policy.allowed_profiles:
        return result(KEEP, "keep", f"{prof_key} is niet vrijgegeven in het DV-beleid")
    if routes is None:
        return result(KEEP, "keep", "Encodercapaciteiten onbekend (encoder onbereikbaar); "
                      "opnieuw controleren", warnings=warnings)
    if prof_key not in routes:
        need = "ffmpeg ≥ 7.1 met libsvtav1 -dolbyvision" if prof_key.startswith("8") else \
            "dovi_tool + mkvmerge + libsvtav1 -dolbyvision"
        return result(UNSUPPORTED, "keep", f"Route voor {prof_key} niet beschikbaar ({need})")

    route = {"8.1": "svt_av1_dv_direct", "8.4": "svt_av1_dv_direct",
             "7_mel": "dovi_tool_7_to_81_then_svt", "7_fel": "dovi_tool_7_to_81_then_svt",
             "5": "proven_dv5_route"}[prof_key]
    if prof_key == "7_mel":
        warnings.append("DV 7 MEL → 8.1: timing, kleur, RPU en audio worden apart geverifieerd")
    if show_playback_warnings and not _confirmed(prof_key, confirmations):
        warnings.append("Afspeelketen voor AV1 + DV nog niet door jou bevestigd "
                        "(informatie, geen blokkade)")

    if prof_key == "7_fel":
        if policy.mode != "experimental_keep_source":
            return result(KEEP, "keep", "DV 7 FEL: alleen 'Experimenteel profiel (behoud bron)'"
                          " mag een apart resultaat maken; FEL-beeldinformatie kan verloren "
                          "gaan", warnings=warnings)
        warnings.append("FEL-beeldinformatie gaat verloren bij omzetting naar 8.1; RPU behouden"
                        " ≠ alle beeldinformatie behouden. Originele bron blijft staan.")
        return result(TRIAL, "experimental_separate", "Experimentele conversie naar apart "
                      "resultaat; bron wordt nooit vervangen", route=route, warnings=warnings)

    if policy.mode == "trial_copy":
        return result(TRIAL, "trial", "Proefconversie op kopie; activeren na verificatie",
                      route=route, warnings=warnings)
    if policy.mode == "experimental_keep_source":
        return result(TRIAL, "experimental_separate", "Experimenteel: resultaat apart, bron "
                      "blijft behouden", route=route, warnings=warnings)
    return result(ALLOWED, "convert", f"{MODE_LABELS_NL['auto_after_verify']}: vervanging pas "
                  "na geslaagde verificatie, canary en herstelbare kopie", route=route,
                  warnings=warnings)


def full_auto_eligible(decision: DvDecision, *, verification_passed: bool,
                       recovery_ready: bool, canary_passed: bool) -> tuple[bool, list[str]]:
    """Q3.3: geen externe compatibiliteitsmatrix nodig; wel verificatie, herstel, canary."""
    reasons = []
    if decision.action not in ("convert", "none"):
        reasons.append(f"actie '{decision.action}' vereist handmatige activatie")
    if not verification_passed:
        reasons.append("verificatie niet geslaagd")
    if not recovery_ready:
        reasons.append("geen werkend herstelpad (quarantaine)")
    if not canary_passed:
        reasons.append("canary nog niet geslaagd")
    return (not reasons, reasons)
