#!/usr/bin/env python3
"""Beslissen wat er met een bestand moet gebeuren.

Deze module doet NIETS. Hij kijkt naar de uitslag van probe.py en geeft een
Besluit terug. Dat is met opzet: in de pipeline wordt eerst voor de hele
bibliotheek besloten, dan bewaard, dan door jou bekeken, en pas daarna
uitgevoerd. Zie spec/04-veiligheidsregels.md, regel 1.

Zelfstandig te draaien:
    python3 beslissen.py /pad/naar/film.mkv
"""

from typing import Any

from pydantic import BaseModel, ConfigDict, Field

# Standaardinstellingen. In de pipeline komen die per library uit de database.
STANDAARD: dict[str, Any] = {
    "drempel_2160": 20000,
    "drempel_1080": 8000,
    "drempel_720": 4000,
    # Kwaliteitsgestuurd (rapport 4): HDR/DV vraagt een lagere CRF dan SDR, anders worden
    # donkere 4K-DV-scènes ± 2 Mbit/s. "_hdr" geldt voor HDR10/HLG/PQ/Dolby Vision-bronnen.
    "crf_2160": 24,
    "crf_2160_hdr": 21,
    "crf_1080": 27,
    "crf_1080_hdr": 24,
    "crf_720": 29,
    "crf_720_hdr": 26,
    # 0 = kwaliteitsgestuurd met CRF. Een waarde > 0 is een gemiddelde
    # doelbitrate in kbps; de encoder krijgt daarnaast een ruime VBV-band.
    # 6500 kbps is een bruikbaar startpunt voor 4K-DV op deze host.
    "doel_bitrate_2160_kbps": 6500,
    "doel_bitrate_1080_kbps": 0,
    "doel_bitrate_720_kbps": 0,
    "preset_2160": 8,
    "preset_1080": 6,
    "preset_720": 6,
    # 0 = automatisch. De uitvoerder kiest dan op basis van beschikbaar RAM
    # een veilige SVT-paralleliteit; een expliciete waarde blijft begrensd.
    "svt_threads_2160": 0,
    "svt_threads_1080": 0,
    "svt_threads_720": 0,
    "gpu_quality": 28,
    # Alleen de begrensde jobretry mag dit tijdelijk verlagen. Het profiel
    # houdt 64 voor normale QSV-doorvoer.
    "gpu_extra_hw_frames": 64,
    "gpu_voor_sdr_1080": True,
    "gpu_voor_sdr_4k": False,
    "gpu_voor_hdr10": False,
    "dv_modus": "skip",  # skip | av1
    "film_grain": 0,
    "audio_talen": ["nld", "eng", "jpn"],
    "ondertitel_talen": ["nld", "eng"],
    "commentaar_weg": True,
    "beste_per_taal": True,
}


class Besluit(BaseModel):
    """Wat er met een bestand gaat gebeuren, en waarom."""

    model_config = ConfigDict(extra="forbid")

    actie: str
    baan: str = ""
    encoder: str = ""
    reden: str = ""
    opschonen: bool = False
    behoud_hdr: bool = False
    herstel_hdr_na: bool = False
    dolby_vision: bool = False
    dv7_omzetten: bool = False
    audio_houden: list[int] = Field(default_factory=list)
    subs_houden: list[int] = Field(default_factory=list)
    attachments_houden: bool = False
    waarschuwingen: list[str] = Field(default_factory=list)
    # MediaGuard-uitbreiding (defaults houden oude payloads geldig):
    # dv_route is de expliciet gekozen DV-profielroute ("8.1", "8.4", "7_mel",
    # "7_fel"); apart_resultaat betekent dat het resultaat NOOIT de bron mag
    # vervangen (DV7 FEL verliest enhancement-layerbeeldinformatie).
    dv_route: str = ""
    apart_resultaat: bool = False
    # HDR10+ (ST 2094-40): "" = niet van toepassing; "behouden" = libaom-route die
    # de dynamische metadata als T.35-OBU meedraagt (FFmpeg >= 8.1);
    # "hdr10_fallback" = expliciet gekozen verlies: alleen statisch HDR10.
    hdr10plus_route: str = ""
    # Rapport 7 §4.3: DV 8.1 bewust als HDR10 via de GPU (de HDR10-basislaag blijft, de
    # RPU vervalt). Alleen met expliciete keuze ``dv81_naar_hdr10``.
    dv_naar_hdr10: bool = False


def klasse_van(hoogte: int) -> str:
    if hoogte > 1200:
        return "2160"
    if hoogte > 800:
        return "1080"
    return "720"


# Ondertitelformaten die Matroska rechtstreeks aankan. mov_text uit MP4 staat
# hier bewust niet bij: dat past niet in MKV en moet omgezet worden.
MKV_SUB_CODECS = (
    "subrip",
    "srt",
    "ass",
    "ssa",
    "webvtt",
    "dvd_subtitle",
    "hdmv_pgs_subtitle",
    "dvb_subtitle",
)
ASS_CODECS = ("ass", "ssa")


def kies_sporen(
    info: dict[str, Any], inst: dict[str, Any]
) -> tuple[list[dict[str, Any]], list[dict[str, Any]], bool, bool]:
    """Welke audio- en ondertitelsporen blijven over."""
    if inst.get("audio_alles_behouden") or inst.get("ondertitels_alles_behouden"):
        return _kies_sporen_behoudend(info, inst)
    audio_talen = inst["audio_talen"]
    sub_talen = inst["ondertitel_talen"]

    houden = [
        a
        for a in info["audio"]
        if a["taal"] in audio_talen and not (inst["commentaar_weg"] and _is_commentaar(a))
    ]

    if inst["beste_per_taal"] and houden:
        per_taal: dict[str, dict[str, Any]] = {}
        for a in houden:
            huidig = per_taal.get(a["taal"])
            if (
                huidig is None
                or a["kanalen"] > huidig["kanalen"]
                or (a["kanalen"] == huidig["kanalen"] and a["bit_rate"] > huidig["bit_rate"])
            ):
                per_taal[a["taal"]] = a
        houden = list(per_taal.values())

    # VEILIGHEIDSNET: nooit een bestand zonder audio opleveren. Een Koreaanse
    # film waarvan geen enkel spoor aan je talenlijst voldoet houdt zijn
    # originele spoor.
    noodgreep = False
    if not houden and info["audio"]:
        houden = [info["audio"][0]]
        noodgreep = True

    subs = info["subs"]
    subs_houden = (
        subs
        if not sub_talen
        else [
            s
            for s in subs
            if s["taal"] in sub_talen and not (inst["commentaar_weg"] and s["commentaar"])
        ]
    )

    # Blijft er een ASS/SSA-spoor over, dan MOETEN de lettertypen mee.
    # Anime-releases hangen hun typografie als attachment in het bestand.
    attachments = any(s["codec"] in ASS_CODECS for s in subs_houden) and info["attachments"] > 0

    return houden, subs_houden, attachments, noodgreep


def _kies_sporen_behoudend(
    info: dict[str, Any], inst: dict[str, Any]
) -> tuple[list[dict[str, Any]], list[dict[str, Any]], bool, bool]:
    """MediaGuard-standaard (P6): eerst inventariseren, niets weggooien.

    Dual audio, originele audio, audiodescriptie, commentaar en forced/signs &
    songs-ondertitels blijven staan. Opschonen gebeurt alleen nog via een
    expliciete per-library policy (keep_all=false) en geverifieerde output.
    """
    if inst.get("audio_alles_behouden"):
        houden = list(info["audio"])
        noodgreep = False
    else:
        houden, _subs, _att, noodgreep = kies_sporen(
            info, {**inst, "audio_alles_behouden": False, "ondertitels_alles_behouden": False}
        )
    if inst.get("ondertitels_alles_behouden"):
        subs_houden = list(info["subs"])
    else:
        _audio, subs_houden, _att, _nood = kies_sporen(
            info, {**inst, "audio_alles_behouden": False, "ondertitels_alles_behouden": False}
        )
    # Lettertypen reizen mee zodra er ASS/SSA blijft; bij keep-all ook als
    # alleen bijlagen aanwezig zijn, want we weten niet welk spoor ze gebruikt.
    attachments = info["attachments"] > 0 and (
        bool(inst.get("ondertitels_alles_behouden"))
        or any(s["codec"] in ASS_CODECS for s in subs_houden)
    )
    return houden, subs_houden, attachments, noodgreep


def dv_route_van(info: dict[str, Any]) -> str:
    """Vertaal probe-velden naar een expliciete DV-routesleutel.

    8.1 en 8.4 volgen uit de BL-compatibiliteits-id. Bij profiel 7 bepaalt het
    (optionele) veld dv_el_type of het MEL of FEL is; onbekend telt als FEL,
    want een FEL stil als MEL behandelen kost beeldinformatie.
    """
    profiel = int(info.get("dv_profile") or 0)
    if profiel == 7:
        return "7_mel" if str(info.get("dv_el_type") or "").upper() == "MEL" else "7_fel"
    if profiel == 8:
        return f"8.{int(info.get('dv_compat') or 0)}"
    return str(profiel)


def _is_commentaar(spoor: dict[str, Any]) -> bool:
    titel = spoor.get("titel", "").lower()
    return spoor.get("commentaar") or "commentary" in titel or "commentaar" in titel


COVER_ART_WARNING = "coverafbeelding (attached_pic) gaat bij deze encode verloren"


def beslis(info: dict[str, Any], inst: dict[str, Any] | None = None) -> Besluit:
    """Beslis; een verloren coverafbeelding wordt altijd expliciet gemeld."""

    b = _beslis(info, inst)
    if info.get("attached_pics") and (
        b.actie == "hercoderen"
        or (
            b.actie == "opschonen"
            and (
                not b.attachments_houden
                or "matroska" not in str(info.get("container") or "").lower()
            )
        )
    ):
        b.waarschuwingen.append(COVER_ART_WARNING)
    return b


def _beslis(info: dict[str, Any], inst: dict[str, Any] | None = None) -> Besluit:
    """De hele beslisboom. Zie spec/02-domeinkennis.md voor het waarom."""
    inst = {**STANDAARD, **(inst or {})}
    b = Besluit(actie="overslaan")

    audio_houden, subs_houden, attachments, noodgreep = kies_sporen(info, inst)
    b.audio_houden = [a["index"] for a in audio_houden]
    b.subs_houden = [s["index"] for s in subs_houden]
    b.attachments_houden = attachments
    if noodgreep:
        b.waarschuwingen.append("geen enkel audiospoor matchte de talen; eerste origineel behouden")

    if info["duration"] <= 0:
        b.reden = "speelduur onbekend"
        return b

    kl = klasse_van(info["height"])
    drempel = inst["drempel_" + kl]

    # ── HDR10+ ────────────────────────────────────────────────────────
    # Bewust vóór de DV-tak: een bestand met DV én HDR10+ zou anders via de
    # DV-route worden gehercodeerd en zijn ST 2094-40-metadata stil verliezen.
    # (De uitvoerder weigerde dit al; nu zegt ook het besluit het eerlijk.)
    hdr10plus_modus = str(inst.get("hdr10plus_modus") or "skip")
    if info["hdr10plus"]:
        if hdr10plus_modus == "skip" or info["dv_profile"]:
            # DV + HDR10+ samen: geen bewezen gecombineerde route.
            b.reden = "HDR10+: dynamische metadata overleeft geen encode" + (
                " (combinatie met Dolby Vision wordt nooit omgezet)"
                if info["dv_profile"] and hdr10plus_modus != "skip"
                else ""
            )
            return b
        if hdr10plus_modus == "converteer_geverifieerd":
            b.hdr10plus_route = "behouden"
        elif hdr10plus_modus == "hdr10_fallback":
            b.hdr10plus_route = "hdr10_fallback"
            b.waarschuwingen.append(
                "HDR10+: dynamische metadata (ST 2094-40) gaat VERLOREN; expliciet gekozen, "
                "alleen statische HDR10-metadata blijft"
            )
        else:
            b.reden = f"HDR10+: onbekende modus {hdr10plus_modus}"
            return b

    # ── Dolby Vision ──────────────────────────────────────────────────
    if info["dv_profile"]:
        p = info["dv_profile"]
        if p == 5:
            # Profiel 5 heeft geen HDR10-basislaag. Het beeld staat in
            # IPT-PQ-c2 en er is geen correcte niet-DV-interpretatie van.
            # Haal je de DV eruit, dan krijg je paars-groene onzin.
            b.reden = "DV profiel 5: geen HDR10-terugval, nooit aanraken"
            return b
        if inst["dv_modus"] != "av1":
            b.reden = f"DV profiel {p}: dv_modus staat op skip"
            return b
        # MediaGuard (DEEL Q): een expliciete allowlist van profielroutes. Zonder
        # deze sleutel blijft het oude gedrag (alle routes behalve 5) intact.
        toegestaan = inst.get("dv_profielen")
        route = dv_route_van(info)
        fel = route == "7_fel"
        if toegestaan is not None:
            if route not in set(toegestaan):
                b.reden = f"DV-route {route} staat niet in de toegestane profielen"
                return b
            if fel and not inst.get("dv_fel_experimenteel"):
                b.reden = (
                    "DV profiel 7 FEL: alleen als experimentele aparte output "
                    "(dv_mode experimental_keep_source)"
                )
                return b
            if p not in (7, 8):
                b.reden = f"DV profiel {p}: geen bewezen AV1-route"
                return b
        elif fel:
            # Oude route zonder profielallowlist kent geen apart resultaat: een
            # FEL (of niet bewezen MEL) zou daar stil naar single layer gaan en de
            # bron vervangen. Dat kost beeldinformatie, dus overslaan.
            b.reden = (
                "DV profiel 7 zonder bewezen MEL (FEL of onbekend): de enhancement "
                "layer zou verloren gaan; overgeslagen"
            )
            return b
        if info["kbps"] and info["kbps"] <= drempel:
            b.reden = "{} kbps onder drempel {}".format(info["kbps"], drempel)
            return b

        if (route == "8.1" and inst.get("dv81_naar_hdr10") and inst.get("gpu_voor_hdr10")
                and info["is_hdr10"] and not info["hdr10plus"]):
            # Rapport 7 §4.3: bewuste keuze. De HDR10-basislaag van 8.1 gaat via de Arc;
            # de RPU verdwijnt (bsf dovi_rpu=strip) en de verificatie eist géén DV meer.
            b.actie, b.baan, b.encoder = "hercoderen", "gpu", "av1_qsv"
            b.dv_naar_hdr10 = True
            b.herstel_hdr_na = True
            b.opschonen = True
            b.reden = "DV 8.1 bewust als HDR10 via de GPU (Dolby Vision vervalt)"
            b.waarschuwingen.append(
                "Dolby Vision 8.1 → HDR10: de DV-laag vervalt bewust (keuze in het "
                "DV-beleid); de HDR10-basislaag blijft")
            return b
        # Sinds ffmpeg 7.1 schrijft libsvtav1 de RPU zelf weg met
        # "-dolbyvision 1". HandBrake is hier niet meer voor nodig.
        # LET OP: de standaardstand "auto" schrijft de RPU NIET. Zonder die
        # expliciete 1 raak je je Dolby Vision stilzwijgend kwijt.
        b.actie, b.baan, b.encoder = "hercoderen", "cpu", "libsvtav1"
        b.dolby_vision = True
        b.behoud_hdr = info["is_hdr10"]
        b.opschonen = True
        b.reden = f"DV profiel {p} naar AV1 profiel 10"
        if toegestaan is not None:
            b.dv_route = route
        if p == 7:
            # Dual layer: ffmpeg kan de enhancement layer niet aan, dus eerst
            # met dovi_tool naar 8.1 (single layer).
            b.dv7_omzetten = True
            b.waarschuwingen.append("profiel 7: eerst dovi_tool naar 8.1, dan pas encoderen")
            if toegestaan is not None and fel:
                # Q1.1: FEL nooit stil naar single layer. Alleen een apart,
                # experimenteel resultaat; de bron blijft altijd bestaan.
                b.apart_resultaat = True
                b.waarschuwingen.append(
                    "profiel 7 FEL: de enhancement layer (extra beeldinformatie) gaat "
                    "verloren; alleen een apart experimenteel resultaat, bron blijft behouden"
                )
        return b

    # ── Al klaar of te klein ──────────────────────────────────────────
    if info["codec"].lower() == "av1":
        b.actie = "opschonen" if _valt_op_te_schonen(info, b) else "overslaan"
        b.reden = "al AV1" + (", alleen sporen uitdunnen" if b.actie == "opschonen" else "")
        b.baan = "cpu" if b.actie == "opschonen" else ""
        b.opschonen = b.actie == "opschonen"
        return b

    if not info["kbps"] and not inst.get("drempels_negeren"):
        b.reden = "bitrate niet te bepalen"
        return b
    if info["kbps"] and info["kbps"] <= drempel:
        b.actie = "opschonen" if _valt_op_te_schonen(info, b) else "overslaan"
        b.reden = "{} kbps onder drempel {}".format(info["kbps"], drempel)
        if b.actie == "opschonen":
            b.reden += ", alleen sporen uitdunnen"
            b.baan, b.opschonen = "cpu", True
        return b

    # ── Encoder kiezen ────────────────────────────────────────────────
    if b.hdr10plus_route == "behouden":
        # Alleen libaom (FFmpeg >= 8.1) schrijft HDR10+ als ITU-T T.35-OBU;
        # libsvtav1 en av1_qsv laten het stil vallen.
        b.baan, b.encoder, b.behoud_hdr = "cpu", "libaom-av1", True
    elif b.hdr10plus_route == "hdr10_fallback" and not (
            inst["gpu_voor_hdr10"] and info["is_hdr10"]):
        b.baan, b.encoder, b.behoud_hdr = "cpu", "libsvtav1", True
    elif info["is_hdr10"]:
        # Ook HDR10+ in de expliciete HDR10-fallback (rapport 7 §4.1): de dynamische
        # metadata vervalt toch, dus dezelfde GPU-route als gewoon HDR10.
        # av1_qsv heeft GEEN optie voor mastering display. Controleer zelf:
        #   ffmpeg -h encoder=av1_qsv | grep -i master   -> geeft niets.
        # HDR via de GPU levert dus een bestand op dat er als SDR uitziet,
        # zonder foutmelding. Alleen doen als je de metadata achteraf
        # terugzet met mkvmerge.
        if inst["gpu_voor_hdr10"]:
            b.baan, b.encoder = "gpu", "av1_qsv"
            b.herstel_hdr_na = True
            b.waarschuwingen.append(
                "HDR10 via de GPU: metadata wordt daarna teruggezet en gecontroleerd"
            )
        else:
            b.baan, b.encoder, b.behoud_hdr = "cpu", "libsvtav1", True
    elif kl == "2160":
        gpu = inst["gpu_voor_sdr_4k"]
        b.baan, b.encoder = ("gpu", "av1_qsv") if gpu else ("cpu", "libsvtav1")
    else:
        gpu = inst["gpu_voor_sdr_1080"]
        b.baan, b.encoder = ("gpu", "av1_qsv") if gpu else ("cpu", "libsvtav1")

    b.actie = "hercoderen"
    b.opschonen = True
    b.behoud_hdr = info["is_hdr10"] and b.encoder in {"libsvtav1", "libaom-av1"}
    b.reden = "{}p @ {} kbps naar AV1 via {}".format(
        kl, info["kbps"], "GPU" if b.baan == "gpu" else "CPU"
    )
    if b.herstel_hdr_na:
        b.reden += " (HDR10 via GPU, metadata daarna teruggezet)"
    if b.hdr10plus_route == "behouden":
        b.reden += " (libaom, HDR10+ dynamische metadata behouden)"
    elif b.hdr10plus_route == "hdr10_fallback":
        b.reden += " (HDR10+ expliciet naar statisch HDR10; dynamische metadata vervalt)"
    return b


def _valt_op_te_schonen(info: dict[str, Any], b: Besluit) -> bool:
    """Is er nog winst te halen zonder de video aan te raken?"""
    return (
        len(b.audio_houden) < len(info["audio"])
        or len(b.subs_houden) < len(info["subs"])
        or not info["pad"].lower().endswith(".mkv")
    )


if __name__ == "__main__":
    import json
    import sys

    from app.media.probe import probe

    if len(sys.argv) < 2:
        sys.exit("Gebruik: python3 beslissen.py <bestand>")
    info = probe(sys.argv[1])
    if not info:
        sys.exit("Niet te lezen")
    print(json.dumps(beslis(info).model_dump(), indent=2, ensure_ascii=False))
