#!/usr/bin/env python3
"""Bouwt de ffmpeg- en mkvmerge-commando's.

HandBrake komt hier bewust niet meer in voor. Sinds ffmpeg 7.1 schrijft
libsvtav1 de Dolby Vision-RPU zelf weg met "-dolbyvision 1", en dat is
empirisch geverifieerd: een echt profiel 8.1-bestand komt er als AV1 met
DV profiel 10 uit, met de HDR10-laag, de audio, de ondertitels en de
lettertypen intact.

Dat scheelt een bouwstap van veertig minuten, een OS-afhankelijkheid, en
een encoder die geen "-progress pipe:1" kent.

Elke functie geeft een LIJST terug, nooit een string. Nooit shell=True: je
bestandsnamen bevatten spaties, haakjes, apostroffen en accenten, en een
bibliotheek van 50 TB vindt gegarandeerd de naam die je quoting breekt.

Alle commando's schrijven MKV. Verander dat niet: verandert de extensie, dan
breken je .nfo-bestanden, je externe .srt en je Radarr-koppeling.
"""

import os
from typing import Any

from app.hostlimits import effective_cpu_count, effective_memory
from app.media import gpuvarianten
from app.media.beslissen import Besluit

# Ondertitelformaten die Matroska aankan. Alles daarbuiten (mov_text uit MP4)
# moet omgezet, anders faalt de mux met "Subtitle codec 94213 is not supported".
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


def _basis(bron: str) -> list[str]:
    return [
        "ffmpeg",
        "-hide_banner",
        "-loglevel",
        "info",
        "-nostats",
        "-progress",
        "pipe:1",
        "-y",
        "-i",
        bron,
    ]


def _maps(besluit: Besluit, info: dict[str, Any] | None = None) -> list[str]:
    """De -map-vlaggen: welke sporen gaan mee (``info`` gereserveerd voor bijlagen)."""
    args = ["-map", "0:v:0"]
    for idx in besluit.audio_houden:
        args += ["-map", f"0:{idx}"]
    for idx in besluit.subs_houden:
        args += ["-map", f"0:{idx}"]
    if besluit.attachments_houden:
        # 0:t? = alle attachments; het vraagteken voorkomt een fout als er
        # toch geen zijn. Dit zijn de lettertypen van je ASS-ondertitels.
        args += ["-map", "0:t?"]
    # Coverafbeeldingen (attached_pic) gaan bewust NIET mee: de MKV-muxer van
    # FFmpeg schrijft ze als extra MJPEG-videospoor in plaats van als bijlage.
    # beslissen.py meldt dat verlies expliciet (waarschuwing + metadata_loss).
    return args


def remap_sporen(besluit: Besluit, bron: dict[str, Any], doel: dict[str, Any]) -> Besluit:
    """Vertaal spoorindexen van de bron naar een voorbewerkt bestand.

    Na DV7->8.1 verwijdert mkvmerge alle bronvideosporen en zet het nieuwe
    8.1-spoor vooraan. De volgorde binnen audio en ondertitels blijft gelijk,
    dus de positie per type is de sleutel; absolute indexen verschuiven bij
    bronnen met meer dan één videospoor.
    """

    def vertaal(soort: str, indices: list[int]) -> list[int]:
        oud = [int(item["index"]) for item in bron.get(soort) or []]
        nieuw = [int(item["index"]) for item in doel.get(soort) or []]
        if len(oud) != len(nieuw):
            raise ValueError(f"voorbewerkt bestand heeft een ander aantal {soort}-sporen")
        return [nieuw[oud.index(idx)] for idx in indices]

    return besluit.model_copy(
        update={
            "audio_houden": vertaal("audio", list(besluit.audio_houden)),
            "subs_houden": vertaal("subs", list(besluit.subs_houden)),
        }
    )


def _sub_codecs(besluit: Besluit, info: dict[str, Any]) -> list[str]:
    """Per bewaard ondertitelspoor: past het in MKV, of moet het om?

    LET OP DE VOLGORDE waarin je dit in het commando zet. Deze vlaggen moeten
    NA "-c copy" komen, anders overschrijft die generieke vlag ze weer en
    faalt de mux alsnog. ffmpeg waarschuwt daar wel voor, maar in een log van
    duizend regels leest niemand dat:

        Multiple -c options specified for stream 2, only the last will be used
    """
    args = []
    op_index = {s["index"]: s for s in info["subs"]}
    for positie, idx in enumerate(besluit.subs_houden):
        codec = op_index.get(idx, {}).get("codec", "")
        if codec and codec not in MKV_SUB_CODECS:
            args += [f"-c:s:{positie}", "srt"]
    return args


def _mkvmerge_track_selection(besluit: Besluit, info: dict[str, Any]) -> list[str]:
    """Selecteer exact dezelfde Matroska-sporen als de FFmpeg-route."""

    # Het hoofdvideospoor is niet altijd track 0 (bijv. audio eerst gemuxt).
    video = (info.get("video_indices") or [0])[0]
    args = ["--video-tracks", str(video)]
    if besluit.audio_houden:
        args += ["--audio-tracks", ",".join(str(item) for item in besluit.audio_houden)]
    else:
        args += ["--no-audio"]
    if besluit.subs_houden:
        args += ["--subtitle-tracks", ",".join(str(item) for item in besluit.subs_houden)]
    else:
        args += ["--no-subtitles"]
    if not besluit.attachments_houden:
        args += ["--no-attachments"]
    return args


def _can_mkvmerge_cleanup(info: dict[str, Any], besluit: Besluit) -> bool:
    """Gebruik mkvmerge alleen als geen ondertitelconversie nodig is."""

    if "matroska" not in str(info.get("container") or "").lower():
        return False
    subtitles = {int(item["index"]): str(item.get("codec") or "") for item in info["subs"]}
    return all(subtitles.get(index, "") in MKV_SUB_CODECS for index in besluit.subs_houden)


def _doel_bitrate(inst: dict[str, Any], klasse: str) -> int:
    """Lees een doelbitrate veilig; nul betekent de bestaande CRF-modus."""

    raw = inst.get(f"doel_bitrate_{klasse}_kbps", 0)
    try:
        value = int(raw or 0)
    except (TypeError, ValueError):
        value = 0
    return max(0, min(value, 200_000))


def _svt_paralleliteit(hoogte: int, inst: dict[str, Any]) -> int:
    """Kies SVT-workers op basis van RAM zonder een 4K-host te laten OOM'en.

    Een 4K-SVT-worker kan naast de framebuffers en muxing grofweg 1,5 GiB
    gebruiken. We reserveren daarom 50% van het totale RAM voor Ubuntu,
    SQLite, de GPU-driver en muxing en houden daarnaast 2 GiB van het op dit
    moment beschikbare geheugen vrij. Meer-RAM-machines krijgen daardoor
    automatisch meer paralleliteit, terwijl een drukke host terugschakelt.
    """

    klasse = "2160" if hoogte > 1200 else "1080" if hoogte > 800 else "720"
    try:
        requested = int(inst.get(f"svt_threads_{klasse}", 0) or 0)
    except (TypeError, ValueError):
        requested = 0
    # cgroup-bewust: in een container telt de container-limiet, niet de host.
    cpu_count = effective_cpu_count()
    memory = effective_memory()
    gib = 1024**3
    per_worker = {"2160": 1.5, "1080": 0.75, "720": 0.4}[klasse] * gib
    budget = min(memory.total * 0.50, max(1 * gib, memory.available - 2 * gib))
    automatic = max(1, min(cpu_count, int(budget / per_worker)))
    try:
        cap = int(inst.get("cpu_budget_threads", 0) or 0)
    except (TypeError, ValueError):
        cap = 0
    if cap > 0:
        # maximaal CPU-gebruik voor AV1 (MediaGuard-instelling), verdeeld over de encodes
        automatic = min(automatic, cap)
    if requested > 0:
        return max(1, min(requested, automatic, cpu_count))
    return automatic


HDR_TRANSFERS = {"smpte2084", "arib-std-b67"}
# Ondergrens videobitrate (kbit/s) per resolutieklasse, SDR en HDR/DV (rapport 4). Dezelfde
# familie als de GPU-VBR-minimums; te laag na een CRF-encode = één herkansing op de grens.
CPU_BITRATE_FLOOR = {"720": 800, "1080": 1500, "2160": 6000}
CPU_BITRATE_FLOOR_HDR = {"720": 1200, "1080": 3000, "2160": 8000}


def is_hdr_source(info: dict[str, Any]) -> bool:
    kleur = info.get("kleur") or {}
    return bool(info.get("is_hdr10") or info.get("dv_profile") or info.get("hdr10plus")
                or str(kleur.get("transfer") or "") in HDR_TRANSFERS)


def resolution_class(height: int) -> str:
    return "2160" if height > 1200 else "1080" if height > 800 else "720"


def bitrate_floor_kbps(info: dict[str, Any]) -> int:
    """Minimale videobitrate voor een AV1-resultaat van deze bron (MG_BITRATE_FLOOR_SCALE
    schaalt alle grenzen, bv. 0,8 of 1,2; 0 zet de ondergrens uit)."""
    kl = resolution_class(int(info.get("height") or 0))
    table = CPU_BITRATE_FLOOR_HDR if is_hdr_source(info) else CPU_BITRATE_FLOOR
    from app import afstelling

    scale = float(afstelling.get("bitrate_ondergrens_schaal"))
    return max(0, int(table[kl] * scale))


def _rate_or_quality(inst: dict[str, Any], klasse: str, crf: int) -> list[str]:
    target = _doel_bitrate(inst, klasse)
    if target <= 0:
        return ["-crf", str(crf)]
    # SVT-AV1 2.x accepteert maxrate alleen in CRF-modus. In combinatie met
    # een gemiddelde doelbitrate weigert de encoder al vóór het eerste frame
    # met "Max Bitrate only supported with CRF mode". De QSV-route heeft haar
    # eigen VBV-instellingen; voor SVT gebruiken we daarom zuivere ABR.
    return ["-b:v", f"{target}k"]


def opschonen(info: dict[str, Any], besluit: Besluit, bron: str, doel: str) -> list[str]:
    """Alleen sporen uitdunnen en remuxen naar MKV. Raakt de video niet aan.

    Dit is de grootste snelle winst van het hele project. Een UHD-remux met
    TrueHD Atmos, DTS-HD MA en een commentaarspoor draagt makkelijk 15 tot
    20 GB aan audio mee. Kost minuten per bestand, geen kwaliteitsverlies.
    """
    if _can_mkvmerge_cleanup(info, besluit):
        # FFmpeg weigert sommige oudere, verder afspeelbare HEVC-bitstreams
        # wegens een ongeldige default-display-window al tijdens het schrijven
        # van de MKV-header. mkvmerge kopieert zulke Matroska-sporen veilig
        # zonder de videobitstream of metadata te herschrijven.
        return [
            "mkvmerge",
            "--output",
            doel,
            *_mkvmerge_track_selection(besluit, info),
            bron,
        ]
    return (
        _basis(bron)
        + _maps(besluit, info)
        + ["-c", "copy"]
        + _sub_codecs(besluit, info)
        + ["-map_chapters", "0", "-max_muxing_queue_size", "9999", doel]
    )


def encode_cpu(
    info: dict[str, Any],
    besluit: Besluit,
    inst: dict[str, Any],
    bron: str,
    doel: str,
) -> list[str]:
    """SVT-AV1 op de CPU. De enige route die HDR10 correct meedraagt."""
    kl = "2160" if info["height"] > 1200 else "1080" if info["height"] > 800 else "720"
    crf = int(inst.get(f"crf_{kl}_hdr", inst["crf_" + kl]) if is_hdr_source(info)
              else inst["crf_" + kl])
    preset = inst["preset_" + kl]
    parallel = _svt_paralleliteit(info["height"], inst)

    params = ["film-grain={}".format(inst["film_grain"]), f"lp={parallel}"]
    # Omgeleid vanaf de GPU (gebruikersrapport, anime): extra SVT-parameters per job.
    params.extend(str(p) for p in (inst.get("svt_extra") or []) if "=" in str(p))
    kleur = []
    dv = []
    if getattr(besluit, "dolby_vision", False):
        # Sinds ffmpeg 7.1 schrijft libsvtav1 de Dolby Vision-RPU zelf weg.
        #
        # DIT MOET EXPLICIET OP 1. De standaardstand is "auto", en die
        # schrijft de RPU NIET weg - empirisch vastgesteld op een echt
        # profiel 8.1-bestand: met "auto" komt er dv_profile=0 uit, met
        # "-dolbyvision 1" komt er dv_profile=10 uit. Geen waarschuwing,
        # geen foutmelding. Je merkt het pas op je TV.
        dv = ["-dolbyvision", "1"]
    if besluit.behoud_hdr and info["is_hdr10"]:
        # Er bestaat GEEN ffmpeg-optie -master_display. Voor SVT-AV1 gaat het
        # via -svtav1-params, met decimale coordinaten en luminantie in nits.
        params.append("mastering-display=" + info["mastering_display"])
        if info["content_light"]:
            params.append("content-light=" + info["content_light"])
        kleur = ["-color_primaries", "bt2020", "-color_trc", "smpte2084", "-colorspace", "bt2020nc"]

    return (
        _basis(bron)
        + _maps(besluit, info)
        + ["-c", "copy"]
        + _sub_codecs(besluit, info)
        + [
            "-c:v",
            "libsvtav1",
            *_rate_or_quality(inst, kl, crf),
            "-threads",
            str(parallel),
            "-preset",
            str(preset),
            "-pix_fmt",
            "yuv420p10le",
            "-svtav1-params",
            ":".join(params),
        ]
        + dv
        + kleur
        + dv_strip_args(besluit)
        + ["-map_chapters", "0", "-max_muxing_queue_size", "9999", doel]
    )


def dv_strip_args(besluit: Besluit) -> list[str]:
    """DV 8.1 → HDR10 (rapport 7 §4.3): RPU én DV-configuratierecord uit de uitvoer, zodat
    een speler het bestand als gewoon HDR10 ziet (de verificatie eist dat ook)."""
    return ["-bsf:v", "dovi_rpu=strip=1"] if getattr(besluit, "dv_naar_hdr10", False) else []


def gpu_decode_threads(inst: dict[str, Any]) -> int:
    try:
        value = int(inst.get("gpu_decode_threads", 0) or 0)
    except (TypeError, ValueError):
        value = 0
    return max(1, min(value or GPU_DECODE_THREADS, 16))


GPU_DECODE_THREADS = 4
GPU_UHD_HW_FRAMES = 16
# Rapport 7 §4.4: met max. 2 4K-jobs tegelijk past een tragere preset weer; ≤1080p is op de
# Arc toch binnen minuten klaar, dus daar de beste kwaliteit per MB.
GPU_PRESETS = {"2160": "slow", "1080": "veryslow", "720": "veryslow"}
# Vooruitkijken (lookahead + extbrc + adaptieve I/B-frames): ± halve snelheid, zichtbaar
# betere kwaliteit per MB. Weigert de driver het, dan valt de job binnen dezelfde poging
# terug en blijft het voor deze sessie uit (zie uitvoerder).
GPU_LOOKAHEAD_DEPTH = {"2160": 20, "1080": 40, "720": 40}
_LOOKAHEAD_STATE: dict[str, bool] = {"broken": False}


def lookahead_enabled() -> bool:
    from app import afstelling

    return bool(afstelling.get("gpu_lookahead")) and not _LOOKAHEAD_STATE["broken"]


def mark_lookahead_broken() -> None:
    _LOOKAHEAD_STATE["broken"] = True


def lookahead_args(klasse: str) -> list[str]:
    return ["-extbrc", "1", "-look_ahead_depth", str(GPU_LOOKAHEAD_DEPTH[klasse]),
            "-adaptive_i", "1", "-adaptive_b", "1", "-b_strategy", "1", "-bf", "7"]
# kbit/s: gemiddelde nooit lager (VBR). HDR 4K iets hoger vanwege 10-bit PQ-gradiënten.
GPU_BITRATE_FLOOR = {"720": 900, "1080": 1500, "2160": 5000}
GPU_BITRATE_FLOOR_HDR_2160 = 7000
GPU_BITRATE_CEILING = {"720": 6000, "1080": 12000, "2160": 40000}


def gpu_bitrate_floor(klasse: str, hdr10: bool) -> int:
    if klasse == "2160" and hdr10:
        return GPU_BITRATE_FLOOR_HDR_2160
    return GPU_BITRATE_FLOOR.get(klasse, GPU_BITRATE_FLOOR["1080"])


# Hardwaredecode op de GPU: bron uitpakken op de Arc in plaats van met 4 CPU-threads per job.
# Uit te zetten met MG_GPU_HW_DECODE=0; faalt het één keer, dan blijft het voor deze
# engine-sessie uit (zie uitvoerder: terugval naar CPU-decode binnen dezelfde poging).
HW_DECODE_CODECS = {"h264", "hevc", "av1", "vp9"}
HW_DECODE_PIX = {"yuv420p", "yuv420p10le", "yuvj420p", ""}
_HW_DECODE_STATE: dict[str, bool] = {"broken": False}
HW_DECODE_RESERVE = 1  # CPU-threads per GPU-job bij hardwaredecode (demux/mux)


def hw_decode_enabled() -> bool:
    from app import afstelling

    return bool(afstelling.get("gpu_hardware_decode")) and not _HW_DECODE_STATE["broken"]


def mark_hw_decode_broken() -> None:
    _HW_DECODE_STATE["broken"] = True


def hw_decode_supported(info: dict[str, Any]) -> bool:
    codec = str(info.get("codec") or "").lower()
    pix = str(info.get("pix_fmt") or "").lower()
    return hw_decode_enabled() and codec in HW_DECODE_CODECS and pix in HW_DECODE_PIX


def encode_gpu(
    info: dict[str, Any],
    besluit: Besluit,
    inst: dict[str, Any],
    bron: str,
    doel: str,
    render_node: str = "/dev/dri/renderD128",
    variant: dict[str, Any] | None = None,
    hw_decode: bool = False,
    lookahead: bool | None = None,
    rate_factor: float = 1.0,
) -> list[str]:
    """av1_qsv (of av1_vaapi als alleen die de GPU-proef haalde) op de Intel Arc.

    Twintig keer sneller, geen HDR-metadata.

    p010le, niet yuv420p10le: QSV wil zijn eigen 10-bits formaat. Geef je het
    verkeerde mee, dan valt ffmpeg terug op 8-bit of faalt de encode.
    """
    if besluit.dolby_vision:
        raise ValueError(
            "Dolby Vision mag niet via av1_qsv: deze encoder kan de RPU niet behouden"
        )
    klasse = "2160" if info["height"] > 1200 else "1080" if info["height"] > 800 else "720"
    rate = _doel_bitrate(inst, klasse)
    try:
        extra_hw_frames = int(inst.get("gpu_extra_hw_frames", 64) or 64)
    except (TypeError, ValueError):
        extra_hw_frames = 64
    extra_hw_frames = max(8, min(extra_hw_frames, 64))
    v = variant or gpuvarianten.STANDAARD
    use_lookahead = (lookahead_enabled() if lookahead is None else lookahead) \
        and not gpuvarianten.is_vaapi(v)
    if use_lookahead:
        # lookahead heeft minstens zoveel frames in het videogeheugen nodig als hij vooruitkijkt
        extra_hw_frames = max(extra_hw_frames, GPU_LOOKAHEAD_DEPTH[klasse] + 8)
    if klasse == "2160":
        # 64 4K-p010-frames ≈ 800 MB videogeheugen per job; de A310 heeft 4 GB voor alles.
        extra_hw_frames = min(extra_hw_frames, GPU_UHD_HW_FRAMES + (
            GPU_LOOKAHEAD_DEPTH["2160"] if use_lookahead else 0))
    hdr10 = bool(info.get("is_hdr10"))
    floor = gpu_bitrate_floor(klasse, hdr10)
    if rate <= 0 and gpuvarianten.is_vaapi(v):
        # av1_vaapi kent geen ICQ-kwaliteitswaarde zoals QSV: vaste doelbitrate per klasse.
        rate = {"2160": 12000, "1080": 4000, "720": 2000}[klasse]
    # Kwaliteitsondergrens (rapport 3, R9): ICQ op de Arc gaf 1080p van ± 0,6 Mbit/s
    # (1,4 GB → 116 MB). Altijd VBR met minstens de ondergrens per resolutie; het
    # gemiddelde ligt nooit lager, pieken mogen tot 2,5× voor drukke scènes.
    target = max(rate, floor)
    if rate_factor != 1.0:
        # VMAF-sturing (rapport 7 §4.4): de proefstukken bepalen deze factor; de
        # ondergrens blijft altijd gelden, het plafond voorkomt runaway-bitrates.
        target = max(floor, min(int(target * rate_factor), GPU_BITRATE_CEILING[klasse]))
    rate_args = ["-b:v", f"{target}k", "-maxrate", f"{target * 5 // 2}k",
                 "-bufsize", f"{target * 4}k"]
    # HDR10 altijd 10-bit (p010), ook als de proef alleen met nv12 slaagde.
    pix = "nv12" if v.get("pix") == "nv12" and not hdr10 else "p010le"
    # veryslow was op een A310 met vier 4K-jobs tegelijk 0,1–0,3× realtime; medium voor 4K
    # en slow voor ≤1080p geven vrijwel dezelfde kwaliteit bij deze bitrates (R1).
    gpu_preset = str(inst.get(f"gpu_preset_{klasse}") or GPU_PRESETS[klasse])
    preset = [] if gpuvarianten.is_vaapi(v) else ["-preset", gpu_preset]
    if hw_decode:
        # Decoderen op de Arc; frames blijven in het videogeheugen (rapport 3, R1).
        target_fmt = "p010" if pix == "p010le" else "nv12"
        if gpuvarianten.is_vaapi(v):
            decode_args = ["-hwaccel", "vaapi", "-hwaccel_device", "hw",
                           "-hwaccel_output_format", "vaapi"]
            video_filter = f"scale_vaapi=format={target_fmt}"
        else:
            decode_args = ["-hwaccel", "qsv", "-hwaccel_device", "hw",
                           "-hwaccel_output_format", "qsv",
                           "-extra_hw_frames", str(extra_hw_frames)]
            video_filter = f"vpp_qsv=format={target_fmt}"
    else:
        # Decoderen op de CPU: begrens die threads, anders duwen vier 4K-GPU-jobs de
        # CPU ver boven de limiet.
        decode_args = ["-threads", str(gpu_decode_threads(inst))]
        video_filter = gpuvarianten.upload_filter({**v, "pix": pix}, extra_hw_frames)
    # De AV1-sequence header moet zelf PQ/BT.2020 zeggen; mastering display en content
    # light zet herstel_hdr() daarna op containerniveau terug.
    kleur = (["-color_primaries", "bt2020", "-color_trc", "smpte2084",
              "-colorspace", "bt2020nc", "-color_range", "tv"] if hdr10 else [])
    return (
        [
            "ffmpeg",
            "-hide_banner",
            "-loglevel",
            "info",
            "-nostats",
            "-progress",
            "pipe:1",
            "-y",
            *gpuvarianten.hw_args(v, render_node),
            *decode_args,
            "-i",
            bron,
        ]
        + _maps(besluit, info)
        + ["-c", "copy"]
        + _sub_codecs(besluit, info)
        + [  # Expliciet uploaden naar de GPU. Zonder deze filter valt
            # ffmpeg bij sommige bronformaten terug op software.
            "-vf",
            video_filter,
            *gpuvarianten.codec_args(v),
            *rate_args,
            *preset,
            *(lookahead_args(klasse) if use_lookahead else []),
            *kleur,
            *dv_strip_args(besluit),
            "-map_chapters",
            "0",
            "-max_muxing_queue_size",
            "9999",
            doel,
        ]
    )


def dv7_naar_81(bron: str, fps: str, werkmap: str) -> tuple[list[list[str]], str]:
    """Drie commando's die profiel 7 (dual layer) naar 8.1 omzetten.

    Alleen nodig bij profiel 7. ffmpeg kan de enhancement layer niet aan,
    dus die moet er met dovi_tool af voordat je kunt encoderen. Profiel 8.1
    gaat rechtstreeks door libsvtav1.

    Geeft (lijst_van_commandos, resultaatpad) terug. De aanroeper MOET de
    werkmap opruimen: de ruwe HEVC-stroom van een UHD-bron is tientallen GB.
    """
    raw = os.path.join(werkmap, "in.hevc")
    bl81 = os.path.join(werkmap, "bl81.hevc")
    uit = os.path.join(werkmap, "p81.mkv")
    return (
        [
            [
                "ffmpeg",
                "-hide_banner",
                "-loglevel",
                "error",
                "-y",
                "-i",
                bron,
                "-map",
                "0:v:0",
                "-c",
                "copy",
                "-bsf:v",
                "hevc_mp4toannexb",
                "-f",
                "hevc",
                raw,
            ],
            ["dovi_tool", "-m", "2", "convert", "--discard", "-i", raw, "-o", bl81],
            # --default-duration is verplicht: ruwe HEVC heeft geen timing, en
            # zonder deze vlag loopt je audio uit de pas.
            [
                "mkvmerge",
                "-o",
                uit,
                "-D",
                bron,
                "--default-duration",
                f"0:{fps}p",
                bl81,
                "--track-order",
                "1:0",
            ],
        ],
        uit,
    )


def herstel_hdr(
    md_ruw: dict[str, float] | None, content_light: str | None, bestand: str, doel: str
) -> list[str]:
    """Zet HDR10-metadata op containerniveau terug met mkvmerge.

    Nodig na een GPU-encode, want av1_qsv kan mastering display niet
    meedragen. Matroska kan die metadata zelf dragen en ffprobe leest hem
    daarna gewoon weer uit. Getest: alle tien de waarden komen exact terug.

    LET OP DE VOLGORDE. mkvmerge wil ROOD, GROEN, BLAUW. SVT-AV1 en x265
    gebruiken GROEN, BLAUW, ROOD. Verwissel je die, dan krijg je een bestand
    dat er in de metadata correct uitziet maar verkeerde kleuren claimt.
    """
    # Kleursignalering altijd; mastering display alleen als de bron hem volledig heeft en
    # content light alleen als die niet leeg/0,0 is (zoals de verificatie het ook ziet).
    args = [
        "mkvmerge",
        "-q",
        "-o",
        doel,
        "--colour-primaries",
        "0:9",
        "--colour-transfer-characteristics",
        "0:16",
        "--colour-matrix-coefficients",
        "0:9",
    ]
    md = md_ruw or {}
    chroma = ("red_x", "red_y", "green_x", "green_y", "blue_x", "blue_y")
    if all(md.get(k) is not None for k in chroma):
        args += ["--chromaticity-coordinates",
                 "0:" + ",".join(f"{float(md[k]):.4f}" for k in chroma)]
    if md.get("white_point_x") is not None and md.get("white_point_y") is not None:
        args += ["--white-colour-coordinates",
                 "0:{:.4f},{:.4f}".format(float(md["white_point_x"]), float(md["white_point_y"]))]
    if md.get("max_luminance") is not None:
        args += ["--max-luminance", "0:{:.4f}".format(float(md["max_luminance"]))]
    if md.get("min_luminance") is not None:
        args += ["--min-luminance", "0:{:.4f}".format(float(md["min_luminance"]))]
    maxc, _, maxa = str(content_light or "").partition(",")
    if maxc.strip() and maxa.strip() and not (maxc.strip() == "0" and maxa.strip() == "0"):
        args += ["--max-content-light", "0:" + maxc.strip(),
                 "--max-frame-light", "0:" + maxa.strip()]
    args.append(bestand)
    return args


# FFmpeg >= 8.1 met libaom: de enige geteste route die HDR10+ (ST 2094-40) als
# ITU-T T.35-metadata-OBU per frame in AV1 schrijft. Te overschrijven met
# MG_FFMPEG_HDR10PLUS (het image levert /opt/ffmpeg-hdr10plus/bin/ffmpeg).
FFMPEG_HDR10PLUS_DEFAULT = "/opt/ffmpeg-hdr10plus/bin/ffmpeg"


def ffmpeg_hdr10plus_binary() -> str:
    return os.environ.get("MG_FFMPEG_HDR10PLUS", FFMPEG_HDR10PLUS_DEFAULT)


def encode_cpu_aom(
    info: dict[str, Any],
    besluit: Besluit,
    inst: dict[str, Any],
    bron: str,
    doel: str,
    ffmpeg: str | None = None,
) -> list[str]:
    """libaom-av1 voor HDR10+-behoud. Trager dan SVT-AV1, maar draagt de metadata.

    libaom neemt HDR10+, mastering display en content light zelf over uit de
    frame-side-data (vastgesteld met FFmpeg 8.1.3: 96/96 frames). De kleurtags
    staan er toch expliciet bij: zonder die tags claimt het resultaat geen PQ.
    """
    kl = "2160" if info["height"] > 1200 else "1080" if info["height"] > 800 else "720"
    crf = inst["crf_" + kl]
    preset = int(inst["preset_" + kl])
    # SVT-presets 0-13 grofweg naar libaom cpu-used 2-8 (8 = snelst in "good").
    cpu_used = max(2, min(8, preset))
    threads = _svt_paralleliteit(info["height"], inst)
    basis = _basis(bron)
    basis[0] = ffmpeg or ffmpeg_hdr10plus_binary()
    kleur = []
    if info.get("is_hdr10"):
        kleur = ["-color_primaries", "bt2020", "-color_trc", "smpte2084", "-colorspace", "bt2020nc"]
    return (
        basis
        + _maps(besluit, info)
        + ["-c", "copy"]
        + _sub_codecs(besluit, info)
        + [
            "-c:v",
            "libaom-av1",
            "-crf",
            str(crf),
            "-b:v",
            "0",
            "-cpu-used",
            str(cpu_used),
            "-row-mt",
            "1",
            "-threads",
            str(threads),
            "-pix_fmt",
            "yuv420p10le",
        ]
        + kleur
        + ["-map_chapters", "0", "-max_muxing_queue_size", "9999", doel]
    )
