"""Manieren om de Intel-GPU aan te spreken, plus het uitleggen van een mislukte proef.

Niet elke combinatie van kerneldriver (i915/xe), media-driver en FFmpeg accepteert
dezelfde opstartregel. De proef (app/systeem.py) probeert deze varianten op volgorde;
de eerste die een echt AV1-bestand oplevert, bepaalt ook het echte encodecommando
(app/media/commandos.encode_gpu). Zo gebruiken proef en encode altijd dezelfde weg.
"""

from __future__ import annotations

import re
from typing import Any

# id, hoe het apparaat wordt opgezet, low-power, pixelformaat, encoder, ratemodus
VARIANTEN: tuple[dict[str, Any], ...] = (
    {"id": "qsv_va_lp_p010", "init": "va", "low_power": True, "pix": "p010le",
     "codec": "av1_qsv", "rate": "vbr"},
    {"id": "qsv_va_lp_nv12", "init": "va", "low_power": True, "pix": "nv12",
     "codec": "av1_qsv", "rate": "vbr"},
    {"id": "qsv_va_p010", "init": "va", "low_power": False, "pix": "p010le",
     "codec": "av1_qsv", "rate": "vbr"},
    {"id": "qsv_child_p010", "init": "child", "low_power": False, "pix": "p010le",
     "codec": "av1_qsv", "rate": "vbr"},
    {"id": "qsv_child_icq", "init": "child", "low_power": False, "pix": "nv12",
     "codec": "av1_qsv", "rate": "icq"},
    {"id": "qsv_any_p010", "init": "any", "low_power": False, "pix": "p010le",
     "codec": "av1_qsv", "rate": "vbr"},
    {"id": "vaapi_p010", "init": "vaapi", "low_power": False, "pix": "p010le",
     "codec": "av1_vaapi", "rate": "vbr"},
    {"id": "vaapi_nv12", "init": "vaapi", "low_power": False, "pix": "nv12",
     "codec": "av1_vaapi", "rate": "vbr"},
)
# De oude vaste opstartregel; gebruikt zolang er (nog) geen proefuitslag is.
STANDAARD: dict[str, Any] = {"id": "qsv_child_p010", "init": "child", "low_power": False,
                             "pix": "p010le", "codec": "av1_qsv", "rate": "vbr"}


def hw_args(variant: dict[str, Any], render_node: str) -> list[str]:
    """Apparaat-opzet voor FFmpeg; zet ook het filterapparaat voor hwupload."""

    init = variant.get("init")
    if init == "va":
        return ["-init_hw_device", f"vaapi=va:{render_node}", "-init_hw_device", "qsv=hw@va",
                "-filter_hw_device", "hw"]
    if init == "any":
        return ["-init_hw_device", f"qsv=hw:hw_any,child_device={render_node}",
                "-filter_hw_device", "hw"]
    if init == "vaapi":
        return ["-init_hw_device", f"vaapi=hw:{render_node}", "-filter_hw_device", "hw"]
    return ["-init_hw_device", f"qsv=hw,child_device={render_node}", "-filter_hw_device", "hw"]


def upload_filter(variant: dict[str, Any], extra_hw_frames: int) -> str:
    pix = str(variant.get("pix") or "p010le")
    if variant.get("codec") == "av1_vaapi":
        return f"format={pix},hwupload"
    return f"format={pix},hwupload=extra_hw_frames={extra_hw_frames}"


def codec_args(variant: dict[str, Any]) -> list[str]:
    out = ["-c:v", str(variant.get("codec") or "av1_qsv")]
    if variant.get("low_power"):
        out += ["-low_power", "1"]
    return out


def is_vaapi(variant: dict[str, Any] | None) -> bool:
    return bool(variant) and (variant or {}).get("codec") == "av1_vaapi"


def label(variant: dict[str, Any] | None) -> str:
    return "VA-API" if is_vaapi(variant) else "Quick Sync"


# (patroon, korte reden, hint). Volgorde = specifiek eerst.
_FOUTEN: tuple[tuple[str, str, str], ...] = (
    (r"permission denied|operation not permitted|eacces",
     "Geen toegang tot de GPU (Permission denied)",
     "Geef de app toegang tot de GPU: TrueNAS → Apps → mediaguard → Edit → GPU toewijzen."),
    (r"timeout|duurde langer",
     "De proef bleef hangen (driver reageert niet)",
     "Herstart de app; helpt dat niet, werk TrueNAS bij (GPU-driver)."),
    (r"huc|low.?power.*(unsupported|not supported)|unsupported.*low.?power",
     "Low-power-encode niet ondersteund — HuC-firmware ontbreekt op de host?",
     "Werk TrueNAS bij; de Intel GuC/HuC-firmware moet op de host geladen zijn."),
    (r"error creating a mfx session|mfx session.*-9|\(-9\)|mfx_err_unsupported|"
     r"no.*implementation.*found",
     "MFX-sessie kon niet starten (-9)",
     "De Intel-runtime vindt geen bruikbare encoder op deze kaart; controleer of de host "
     "de kaart met de i915- of xe-driver heeft geladen."),
    (r"vainitialize failed|failed to initiali[sz]e vaapi|va_opendriver|init failed|"
     r"libva error",
     "De Intel media-driver start niet (driver init failed)",
     "De media-driver herkent de kaart niet; werk TrueNAS bij."),
    (r"device creation failed|failed to create.*device|cannot (open|load).*device",
     "GPU-apparaat kon niet worden aangemaakt",
     "Controleer of de juiste GPU aan de app is toegewezen."),
    (r"unknown encoder|encoder not found",
     "Deze FFmpeg mist de AV1-GPU-encoder", "Werk MediaGuard bij."),
    (r"unsupported|not supported|-3\)",
     "Deze instelling wordt door de kaart niet ondersteund",
     "MediaGuard probeert automatisch andere instellingen."),
    (r"no such file or directory",
     "Render-node niet gevonden", "Controleer of de GPU aan de app is toegewezen."),
)


def explain(output: str) -> tuple[str, str]:
    """Korte Nederlandse reden + concrete hint uit FFmpeg-uitvoer."""

    text = output or ""
    low = text.lower()
    for pattern, reason, hint in _FOUTEN:
        if re.search(pattern, low):
            return reason, hint
    lines = [line.strip() for line in text.splitlines() if line.strip()]
    last = lines[-1][:160] if lines else "geen uitvoer van FFmpeg"
    return last, "Kopieer de diagnose en stuur hem mee; dan is de oorzaak te zien."
