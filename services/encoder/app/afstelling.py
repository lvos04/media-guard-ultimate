"""Uitvoeringsafstelling van de encoder, instelbaar in de app (rapport 5).

Alles wat eerder alleen via omgevingsvariabelen kon (MG_MAX_4K_*, nachtvenster, vastloper,
VMAF-grens, bitrate-ondergrens, hardwaredecode, harde CPU-grens, lange jobs 's nachts)
staat nu in de tabel ``afstelling`` en is via ``GET/PUT /api/afstelling`` (en de
MediaGuard-gateway) te lezen en te wijzigen. Een omgevingsvariabele blijft alleen als
noodoverride werken.

De waarden worden in het geheugen gehouden (lezen kost niets) en ververst na elke
wijziging en periodiek door de planner.
"""

from __future__ import annotations

import json
import os
import time
from dataclasses import dataclass
from typing import Any


@dataclass(frozen=True)
class Knob:
    key: str
    kind: str  # int | float | bool | keuze
    default: Any
    minimum: float | None
    maximum: float | None
    env: str | None
    label_nl: str
    help_nl: str
    keuzes: tuple[tuple[str, str], ...] = ()  # (waarde, label) bij soort 'keuze'


KNOBS: tuple[Knob, ...] = (
    Knob("max_4k_gpu", "int", 2, 1, 4, "MG_MAX_4K_GPU", "Max. 4K-encodes tegelijk op de GPU",
         "Een Arc A310 haalt met 1–2 tegelijk de beste totale snelheid."),
    Knob("max_4k_cpu", "int", 1, 1, 4, "MG_MAX_4K_CPU", "Max. 4K-encodes tegelijk op de CPU",
         "Standaard 1: twee 4K-HDR-encodes naast elkaar liepen steeds vast (rapport 6); "
         "1080p loopt er gewoon naast."),
    Knob("nacht_start", "int", 1, 0, 23, None, "Nachtvenster: begin (uur)",
         "Zware jobs (4K-herkansingen, jobs > de grens hieronder) starten bij voorkeur 's nachts."),
    Knob("nacht_einde", "int", 7, 0, 23, None, "Nachtvenster: einde (uur)",
         "Buiten dit venster starten ze alleen als de baan anders stil zou staan."),
    Knob("lange_jobs_alleen_nacht", "bool", True, None, None, None,
         "Lange jobs alleen 's nachts",
         "Jobs met een geschatte duur boven de grens wachten tot het nachtvenster, tenzij er "
         "niets anders te doen is."),
    Knob("lange_job_uren", "float", 6.0, 1, 72, None, "Grens voor een lange job (uren)",
         "Geschatte duur vooraf, uit de eigen geschiedenis per baan, encoder en resolutie."),
    Knob("vastloper_minuten", "int", 15, 1, 240, "MG_STALL_SECONDS",
         "Encode stoppen na zoveel minuten zonder voortgang",
         "Een encode die zo lang niet vooruitgaat, wordt gestopt met een duidelijke oorzaak."),
    Knob("vmaf_minimum", "float", 93.0, 50, 100, "MG_VMAF_MIN", "Minimale VMAF-score",
         "Komt de gemeten kwaliteit (steekproef) lager uit, dan blijft het origineel staan."),
    Knob("ssim_minimum", "float", 0.94, 0.5, 1.0, None, "Minimale SSIM-score (terugval)",
         "Alleen als VMAF niet beschikbaar is: komt SSIM (0–1) lager uit, dan blijft het "
         "origineel staan."),
    Knob("bitrate_ondergrens_schaal", "float", 1.0, 0, 3, "MG_BITRATE_FLOOR_SCALE",
         "Schaal bitrate-ondergrens",
         "1,0 = standaard (4K HDR/DV 8 Mbit/s, 1080p SDR 1,5 Mbit/s); 0 zet de ondergrens uit."),
    Knob("gpu_hardware_decode", "bool", True, None, None, "MG_GPU_HW_DECODE",
         "Bron uitpakken op de GPU",
         "De Arc pakt de bron zelf uit; scheelt veel CPU. Valt bij een fout vanzelf terug."),
    Knob("gpu_lookahead", "bool", True, None, None, "MG_GPU_LOOKAHEAD",
         "Vooruitkijken op de GPU (lookahead)",
         "Betere kwaliteit per MB op de Arc (± halve snelheid). Weigert de driver het, dan "
         "valt de encode vanzelf terug op de gewone instelling."),
    Knob("gpu_vmaf_sturing", "bool", True, None, None, None,
         "GPU-bitrate sturen op VMAF",
         "Het proefstuk wordt gemeten; de bitrate gaat omhoog of omlaag tot de VMAF-doelwaarde "
         "(binnen ondergrens en plafond). Eenvoudige bronnen krijgen minder bits, lastige meer."),
    Knob("vmaf_doel", "float", 95.0, 85, 99, None, "VMAF-doelwaarde (GPU-sturing)",
         "Waar de GPU-bitrate naartoe gestuurd wordt. De minimale VMAF hierboven blijft de "
         "harde ondergrens."),
    Knob("gpu_plekken_auto", "bool", True, None, None, None,
         "GPU-plekken automatisch meten",
         "Na het vinden van de GPU (en na een driver- of instellingwijziging) meet MediaGuard "
         "1, 2 en 4 encodes tegelijk en gebruikt het aantal met de hoogste totale snelheid "
         "(nooit meer dan het ingestelde aantal)."),
    Knob("cpu_tweede_plek_proef", "bool", True, None, None, None,
         "Tweede CPU-plek voorzichtig proberen",
         "Staan er 2 CPU-encodes ingesteld, dan begint MediaGuard met 1. Na 20 geslaagde "
         "encodes zonder vastloper komt de tweede plek erbij; bij een vastloper gaat hij "
         "terug naar 1 (24 uur)."),
    Knob("geheugen_reserve_gb", "float", 4.0, 0, 64, None,
         "Geheugenreserve voor AV1 (GB)",
         "Zoveel werkgeheugen blijft altijd vrij naast een nieuwe encode. De ZFS-cache "
         "telt als vrij: TrueNAS geeft die direct terug als een encode geheugen vraagt."),
    Knob("anime_route", "keuze", "auto", None, None, None, "Route voor anime",
         "Automatisch: anime begint op de GPU; haalt de GPU de VMAF-doelwaarde in een reeks "
         "twee keer niet, dan gaat de rest van die reeks via de CPU. 'Altijd CPU' kost meer "
         "rekentijd maar haalt de kwaliteit vrijwel altijd.",
         (("auto", "Automatisch (leert per reeks)"), ("gpu", "Altijd GPU"),
          ("cpu", "Altijd CPU"))),
    Knob("max_cpu_uren", "float", 12.0, 1, 96, None, "Max. duur van één CPU-encode (uren)",
         "Na 10 minuten wordt de snelheid gemeten. Duurt de rest langer dan dit, dan stopt "
         "MediaGuard de encode ('te traag voor de CPU') en blijft het origineel staan, zodat "
         "de CPU niet dagen bezet blijft."),
    Knob("cpu_harde_grens", "bool", True, None, None, "MG_CPU_CONFINE",
         "Harde CPU-grens voor encodes",
         "Encodes draaien op een vaste set kernen (CPU-limiet × kernen); de rest blijft vrij "
         "voor de app, Sonarr/Radarr en Emby."),
)
BY_KEY = {knob.key: knob for knob in KNOBS}

_CACHE: dict[str, Any] = {}
_LOADED_AT = {"t": 0.0}
REFRESH_SECONDS = 60.0


def _coerce(knob: Knob, value: Any) -> Any:
    if knob.kind == "keuze":
        text = str(value).strip().lower()
        allowed = {key for key, _label in knob.keuzes}
        if text not in allowed:
            raise ValueError(f"{knob.key}: kies uit {', '.join(sorted(allowed))}")
        return text
    if knob.kind == "bool":
        if isinstance(value, str):
            return value.strip().lower() not in {"0", "false", "nee", "no", "off", ""}
        return bool(value)
    number = float(value)
    if knob.minimum is not None:
        number = max(knob.minimum, number)
    if knob.maximum is not None:
        number = min(knob.maximum, number)
    return int(number) if knob.kind == "int" else number


def _env(knob: Knob) -> Any | None:
    if not knob.env:
        return None
    raw = os.environ.get(knob.env)
    if raw is None or raw.strip() == "":
        return None
    try:
        if knob.key == "vastloper_minuten":  # env in seconden (oud contract)
            return _coerce(knob, float(raw) / 60)
        return _coerce(knob, raw)
    except (TypeError, ValueError):
        return None


def get(key: str) -> Any:
    knob = BY_KEY[key]
    override = _env(knob)
    if override is not None:
        return override
    if key in _CACHE:
        return _CACHE[key]
    return knob.default


def refresh(database: Any) -> None:
    try:
        rows = database.fetch_all("SELECT sleutel,waarde FROM afstelling")
    except Exception:  # noqa: BLE001 - tabel nog niet aangemaakt: standaardwaarden
        return
    values: dict[str, Any] = {}
    for row in rows:
        knob = BY_KEY.get(str(row["sleutel"]))
        if knob is None:
            continue
        try:
            values[knob.key] = _coerce(knob, json.loads(str(row["waarde"])))
        except (TypeError, ValueError):
            continue
    _CACHE.clear()
    _CACHE.update(values)
    _LOADED_AT["t"] = time.monotonic()


def refresh_if_stale(database: Any) -> None:
    if time.monotonic() - _LOADED_AT["t"] >= REFRESH_SECONDS:
        refresh(database)


def update(database: Any, changes: dict[str, Any]) -> dict[str, Any]:
    unknown = sorted(set(changes) - set(BY_KEY))
    if unknown:
        raise ValueError("onbekende instelling(en): " + ", ".join(unknown))
    for key, value in changes.items():
        knob = BY_KEY[key]
        if value is None:
            database.execute("DELETE FROM afstelling WHERE sleutel=?", (key,))
            continue
        coerced = _coerce(knob, value)
        database.execute(
            "INSERT INTO afstelling(sleutel,waarde) VALUES (?,?) "
            "ON CONFLICT(sleutel) DO UPDATE SET waarde=excluded.waarde",
            (key, json.dumps(coerced)))
    refresh(database)
    return view()


def view() -> dict[str, Any]:
    items = []
    for knob in KNOBS:
        override = _env(knob)
        items.append({
            "sleutel": knob.key, "soort": knob.kind, "waarde": get(knob.key),
            "standaard": knob.default, "minimum": knob.minimum, "maximum": knob.maximum,
            "label_nl": knob.label_nl, "uitleg_nl": knob.help_nl,
            "vastgezet_door_omgeving": override is not None,
            **({"keuzes": [{"waarde": k, "label_nl": lab} for k, lab in knob.keuzes]}
               if knob.keuzes else {}),
        })
    return {"items": items}
