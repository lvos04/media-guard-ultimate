"""Dolby Vision- en kleurmetadata teruglezen en vergelijken na een encode.

"Het commando ziet er goed uit" is geen test (AGENTS.md). Deze module leest het
resultaat terug met drie onafhankelijke bronnen:

1. ffprobe stream-side-data: het DOVI-configuratierecord (profiel, BL-
   compatibiliteit, rpu/el/bl-vlaggen) zoals de container het aankondigt;
2. ffprobe frame-side-data: of de decoder per frame echt een RPU vindt
   ("Dolby Vision Metadata"). Een configuratierecord zonder RPU in de
   bitstream is precies de stille fout waar spec/02 punt 11 voor waarschuwt;
3. optioneel mediainfo (HDR-formaat/profielstring) en dovi_tool (alleen HEVC:
   dovi_tool kan geen AV1-in-Matroska lezen, vastgesteld met dovi_tool 2.3.1).

Niets hier verandert bestanden.
"""

from __future__ import annotations

import json
import re
import shutil
import subprocess
import tempfile
from pathlib import Path
from typing import Any

# Aantal frames waarover we per-frame-RPU's tellen. Klein houden: dit draait
# ook op 4K-resultaten en moet seconden duren, geen minuten.
RPU_FRAME_VENSTER = 24


def _run(arguments: list[str], timeout: int = 120) -> tuple[int, str]:
    try:
        result = subprocess.run(arguments, capture_output=True, text=True, timeout=timeout)
    except (OSError, subprocess.SubprocessError):
        return 127, ""
    return result.returncode, result.stdout + result.stderr


def _ffprobe_json(arguments: list[str]) -> dict[str, Any]:
    code, output = _run(["ffprobe", "-v", "quiet", "-print_format", "json", *arguments])
    if code != 0:
        return {}
    try:
        value = json.loads(output)
    except json.JSONDecodeError:
        return {}
    return value if isinstance(value, dict) else {}


def dv_readback(path: str | Path) -> dict[str, Any]:
    """Lees DV-, kleur- en codecmetadata van een bestand terug."""

    pad = str(path)
    streams = _ffprobe_json(["-show_streams", "-select_streams", "v:0", pad]).get("streams") or []
    stream: dict[str, Any] = streams[0] if streams else {}
    record = next(
        (item for item in stream.get("side_data_list") or [] if item.get("dv_profile") is not None),
        None,
    )
    frames = (
        _ffprobe_json(
            [
                "-select_streams",
                "v:0",
                "-read_intervals",
                f"%+#{RPU_FRAME_VENSTER}",
                "-show_frames",
                "-show_entries",
                "frame=side_data_list",
                pad,
            ]
        ).get("frames")
        or []
    )
    rpu_frames = sum(
        1
        for frame in frames
        if any(
            "dolby vision" in str(item.get("side_data_type") or "").lower()
            and "configuration" not in str(item.get("side_data_type") or "").lower()
            for item in frame.get("side_data_list") or []
        )
    )
    result: dict[str, Any] = {
        "codec": str(stream.get("codec_name") or ""),
        "profile": None,
        "dv_profile": int(record.get("dv_profile") or 0) if record else 0,
        "bl_compat": int(record.get("dv_bl_signal_compatibility_id") or 0) if record else 0,
        "rpu_present_flag": bool(record.get("rpu_present_flag")) if record else False,
        "el_present_flag": bool(record.get("el_present_flag")) if record else False,
        "bl_present_flag": bool(record.get("bl_present_flag")) if record else False,
        "frames_gelezen": len(frames),
        "rpu_frames": rpu_frames,
        "rpu_present": rpu_frames > 0,
        "kleur": {
            "primaries": str(stream.get("color_primaries") or ""),
            "transfer": str(stream.get("color_transfer") or ""),
            "matrix": str(stream.get("color_space") or ""),
            "pix_fmt": str(stream.get("pix_fmt") or ""),
        },
        "mediainfo_hdr": None,
        "bron": "ffprobe",
    }
    if result["dv_profile"]:
        result["profile"] = f"{result['dv_profile']}.{result['bl_compat']}"
    if shutil.which("mediainfo"):
        code, output = _run(
            [
                "mediainfo",
                "--Inform=Video;%HDR_Format%|%HDR_Format_Profile%|%HDR_Format_Compatibility%",
                pad,
            ],
            timeout=60,
        )
        if code == 0 and output.strip():
            result["mediainfo_hdr"] = output.strip().splitlines()[0]
    return result


def mediainfo_kleur(path: str | Path) -> dict[str, Any] | None:
    """Container- vs bitstreamkleur via mediainfo (None als mediainfo ontbreekt).

    Matroska kan kleur op containerniveau dragen (mkvmerge --colour-*). Spreekt
    die de AV1-sequence-header tegen, dan toont ffprobe alleen de bitstream,
    terwijl sommige spelers de container volgen. mediainfo meldt zo'n conflict
    als ``<veld>_Original``; dat is altijd een verificatiefout.
    """

    if shutil.which("mediainfo") is None:
        return None
    code, output = _run(["mediainfo", "--Output=JSON", str(path)], timeout=60)
    if code != 0:
        return None
    try:
        data = json.loads(output)
        tracks = data["media"]["track"]
    except (json.JSONDecodeError, KeyError, TypeError):
        return None
    video = next((t for t in tracks if t.get("@type") == "Video"), None)
    if not isinstance(video, dict):
        return None
    conflicts = [
        f"{key}: {video.get(key)} (container) vs {video.get(key + '_Original')} (stream)"
        for key in ("colour_primaries", "transfer_characteristics", "matrix_coefficients")
        if video.get(key + "_Original") not in (None, video.get(key))
    ]
    return {
        "primaries": video.get("colour_primaries"),
        "transfer": video.get("transfer_characteristics"),
        "matrix": video.get("matrix_coefficients"),
        "conflicts": conflicts,
    }


_PROFIEL = re.compile(r"Profile:\s*(\d+)(?:\s*\(([A-Za-z]+)\))?")


def dovi_rpu_summary(path: str | Path, frames: int = RPU_FRAME_VENSTER) -> dict[str, Any]:
    """Gebruik dovi_tool op een HEVC-bron: profiel en EL-type (MEL/FEL).

    Alleen voor HEVC. Een onbekend EL-type blijft ``None``; de beslisboom
    behandelt dat bij profiel 7 conservatief als FEL.
    """

    if shutil.which("dovi_tool") is None:
        return {"beschikbaar": False, "reden": "dovi_tool ontbreekt"}
    with tempfile.TemporaryDirectory(prefix="mg-rpu-") as directory:
        rpu = Path(directory) / "rpu.bin"
        code, output = _run(
            ["dovi_tool", "extract-rpu", "-l", str(frames), "-i", str(path), "-o", str(rpu)],
            timeout=300,
        )
        if code != 0 or not rpu.is_file() or rpu.stat().st_size == 0:
            return {"beschikbaar": True, "rpu_present": False, "reden": output[-300:]}
        code, output = _run(["dovi_tool", "info", "-s", "-i", str(rpu)])
    match = _PROFIEL.search(output)
    return {
        "beschikbaar": True,
        "rpu_present": code == 0 and match is not None,
        "profiel": int(match.group(1)) if match else None,
        "el_type": (match.group(2) or "").upper() or None if match else None,
        "samenvatting": output.strip()[-600:],
    }


def verwachte_bl_compat(bron_profiel: int, bron_compat: int) -> int:
    """Profiel 7 wordt via dovi_tool -m 2 naar 8.1, dus HDR10-compatibel (1)."""

    return 1 if bron_profiel == 7 else bron_compat


def controleer_dv(
    source_probe: dict[str, Any], result_path: Path
) -> tuple[bool, str, dict[str, Any]]:
    """Harde DV-controle voor een resultaat waarvan het besluit DV wilde behouden.

    Faalt als: geen DV-record, geen RPU per frame, BL-compatibiliteit gewijzigd
    (bv. HDR10-basis kwijt) of kleur (primaries/transfer/matrix) gewijzigd.
    """

    readback = dv_readback(result_path)
    bron_profiel = int(source_probe.get("dv_profile") or 0)
    bron_compat = int(source_probe.get("dv_compat") or 0)
    if not readback["dv_profile"]:
        return False, "Dolby Vision-configuratierecord ontbreekt in het resultaat", readback
    if not readback["rpu_present"]:
        return False, "Dolby Vision-RPU ontbreekt in de frames van het resultaat", readback
    if readback["codec"] == "av1" and readback["dv_profile"] != 10:
        return False, f"AV1-resultaat heeft DV-profiel {readback['dv_profile']} i.p.v. 10", readback
    verwacht = verwachte_bl_compat(bron_profiel, bron_compat)
    if bron_compat and readback["bl_compat"] != verwacht:
        return (
            False,
            f"DV-basislaagcompatibiliteit gewijzigd ({bron_compat} -> {readback['bl_compat']})",
            readback,
        )
    ok, reden = kleur_gelijk(source_probe.get("kleur"), readback["kleur"])
    if not ok:
        return False, reden, readback
    container = mediainfo_kleur(result_path)
    readback["container_kleur"] = container
    if container is not None and container["conflicts"]:
        return (
            False,
            "container- en bitstreamkleur verschillen: " + "; ".join(container["conflicts"]),
            readback,
        )
    return True, "Dolby Vision-RPU, basislaag en kleur behouden", readback


def kleur_gelijk(bron: object, resultaat: object) -> tuple[bool, str]:
    """Vergelijk primaries/transfer/matrix; ontbrekende bronwaarden zijn geen bewijs."""

    if not isinstance(bron, dict) or not isinstance(resultaat, dict):
        return True, "geen kleurgegevens van vóór de encode"
    for sleutel in ("primaries", "transfer", "matrix"):
        voor = str(bron.get(sleutel) or "")
        na = str(resultaat.get(sleutel) or "")
        if voor and voor != "unknown" and voor != na:
            return False, f"kleur-{sleutel} gewijzigd ({voor} -> {na or 'leeg'})"
    return True, "kleur behouden"
