"""Systeembelasting (Instellingen → Systeem & prestaties): hoeveel CPU AV1 mag gebruiken,
hoeveel encodes tegelijk, stille uren en een temperatuurgrens.

Core is de baas over deze waarden en zet ze door naar de AV1-engine
(``PUT /api/mediaguard/v1/settings/resources``): bij iedere statuspoll (vangnet) en direct
na een wijziging. De engine vertaalt het CPU-percentage naar threads per encode
(cgroup-bewust) en draait encodes met een lagere prioriteit (nice).
"""

from __future__ import annotations

import contextlib
import logging
from typing import Any

from fastapi import APIRouter, Depends

from mediaguard.auth import Principal, require_viewer
from mediaguard.context import AppContext, get_app_context
from mediaguard.db import utcnow
from mediaguard.encoding.client import EncoderError, get_encoder_client
from mediaguard.settings_registry import REGISTRY, SettingDef

LOGGER = logging.getLogger(__name__)
router = APIRouter(prefix="/api/v1/encoding/resources", tags=["encoding"])
CATEGORY = "Systeem & prestaties"
PUSHED_STATE = "encoding_resources_pushed"
NICE = 10  # encodes altijd met lagere prioriteit: Sonarr/Radarr/Plex blijven vlot


def setting_defs() -> list[SettingDef]:
    return [
        SettingDef("resources.av1_cpu_percent", "int", 75, CATEGORY,
                   "Maximaal CPU-gebruik voor AV1", "Maximum CPU use for AV1",
                   help_nl="Welk deel van de processor AV1-encodes samen mogen gebruiken. De "
                           "rest blijft vrij voor Sonarr, Radarr, Plex/Jellyfin en TrueNAS. "
                           "Encodes draaien bovendien altijd met lagere voorrang.",
                   help_en="Share of the CPU that AV1 encodes may use together; the rest stays "
                           "free for other apps. Encodes always run at lower priority.",
                   minimum=10, maximum=100, unit="%",
                   effect_nl="Geldt vanaf de volgende encode."),
        SettingDef("resources.encodes_cpu", "int", 1, CATEGORY,
                   "Encodes tegelijk op de processor (CPU)", "Parallel CPU encodes",
                   help_nl="Hoeveel bestanden tegelijk met de processor worden omgezet. Het "
                           "CPU-budget hierboven wordt over deze encodes verdeeld.",
                   help_en="How many files are encoded on the CPU at the same time; the CPU "
                           "budget is shared between them.",
                   minimum=0, maximum=8, effect_nl="Direct."),
        SettingDef("resources.encodes_gpu", "int", 1, CATEGORY,
                   "Encodes tegelijk op de videokaart (GPU)", "Parallel GPU encodes",
                   help_nl="Hoeveel bestanden tegelijk met de Intel-GPU worden omgezet. 0 = de "
                           "GPU niet gebruiken.",
                   help_en="How many files are encoded on the Intel GPU at the same time "
                           "(0 = don't use the GPU).",
                   minimum=0, maximum=8, effect_nl="Direct."),
        SettingDef("resources.encode_window_enabled", "bool", False, CATEGORY,
                   "Alleen encoderen binnen een tijdvenster", "Only encode within a time window",
                   help_nl="Bijvoorbeeld alleen 's nachts. Buiten het venster wacht de AV1-"
                           "wachtrij; lopende encodes maken hun bestand af.",
                   help_en="For example only at night; outside the window the AV1 queue waits."),
        SettingDef("resources.encode_window", "time_window", {"start": "23:00", "end": "07:00"},
                   CATEGORY, "Tijdvenster voor encoderen", "Encoding time window",
                   help_nl="Van–tot (uu:mm). Over middernacht mag (23:00–07:00)."),
        SettingDef("resources.max_cpu_temp_c", "int", 85, CATEGORY,
                   "Pauzeer AV1 boven CPU-temperatuur", "Pause AV1 above CPU temperature",
                   help_nl="Wordt de processor warmer, dan start er geen nieuwe CPU-encode tot "
                           "hij is afgekoeld.",
                   help_en="No new CPU encode starts while the CPU is hotter than this.",
                   minimum=50, maximum=105, unit="°C", advanced=True),
    ]


def register_settings() -> None:
    REGISTRY.register(*setting_defs())


def desired(ctx: AppContext) -> dict[str, Any]:
    v = ctx.config.value
    body: dict[str, Any] = {
        "cpu_percent": int(v("resources.av1_cpu_percent")),
        "cpu_workers": int(v("resources.encodes_cpu")),
        "gpu_workers": int(v("resources.encodes_gpu")),
        "max_cpu_temp": int(v("resources.max_cpu_temp_c")),
        "nice": NICE,
        "quiet_start": None, "quiet_end": None,
    }
    if bool(v("resources.encode_window_enabled")):
        window = v("resources.encode_window") or {}
        # de engine kent 'stille uren' (niet encoderen); encoderen tussen A en B betekent
        # stil van B tot A
        if window.get("start") and window.get("end") and window["start"] != window["end"]:
            body["quiet_start"], body["quiet_end"] = window["end"], window["start"]
    return body


def parallel_encodes(ctx: AppContext) -> int:
    return int(ctx.config.value("resources.encodes_cpu")) + \
        int(ctx.config.value("resources.encodes_gpu"))


REPUSH_MINUTES = 30


def _recent(at: Any) -> bool:
    from datetime import UTC, datetime, timedelta

    try:
        return datetime.now(UTC) - datetime.fromisoformat(str(at)) < timedelta(
            minutes=REPUSH_MINUTES)
    except ValueError:
        return False


async def push(ctx: AppContext, *, force: bool = False) -> dict[str, Any]:
    want = desired(ctx)
    last = ctx.state_get(PUSHED_STATE)
    # niet alleen bij een wijziging: elk half uur opnieuw, zodat een engine die zijn eigen
    # (oude) waarde terugzette — 90 % naast 75 % in core (rapport H11) — vanzelf bijtrekt
    if not force and isinstance(last, dict) and last.get("value") == want and \
            _recent(last.get("at")):
        return {"pushed": False, "value": want}
    try:
        result = await get_encoder_client(ctx).set_resources(want)
    except EncoderError as exc:
        return {"pushed": False, "value": want, "error": str(exc)}
    ctx.state_set(PUSHED_STATE, {"value": want, "at": utcnow(), "effective": result})
    return {"pushed": True, "value": want, "encoder": result}


@router.get("")
async def status(_: Principal = Depends(require_viewer),
                 ctx: AppContext = Depends(get_app_context)) -> dict[str, Any]:
    """Gewenste waarden + wat de engine er nu van maakt ('≈ 12 van 16 cores')."""
    want = desired(ctx)
    client = get_encoder_client(ctx)
    try:
        effective = await client.get_resources()
        reachable = True
    except EncoderError:
        effective = (ctx.state_get(PUSHED_STATE) or {}).get("effective")
        reachable = False
    in_sync = _in_sync(want, effective)
    repushed = False
    if reachable and not in_sync:
        # engine gebruikt nog een andere waarde: meteen bijwerken en opnieuw lezen
        result = await push(ctx, force=True)
        repushed = bool(result.get("pushed"))
        if repushed:
            with contextlib.suppress(EncoderError):
                effective = await client.get_resources()
            in_sync = _in_sync(want, effective)
    eff = effective if isinstance(effective, dict) else {}
    cores = int(eff.get("cores") or 0) or None
    engine_percent = eff.get("percent")
    allowed = max(1, round(cores * want["cpu_percent"] / 100)) if cores else None
    return {"desired": want, "effective": effective, "encoder_reachable": reachable,
            "parallel_encodes": parallel_encodes(ctx),
            # één bron van waarheid: core-instelling; engine-waarde alleen ter controle
            "cpu_percent": want["cpu_percent"], "engine_cpu_percent": engine_percent,
            "in_sync": in_sync, "repushed": repushed, "cores_total": cores,
            "cores_allowed": allowed,
            "cores_nl": f"≈ {allowed} van {cores} kernen" if cores else None,
            "sync_nl": None if in_sync or not reachable else (
                f"Engine gebruikt nog {engine_percent} % — wordt bijgewerkt"),
            "source_nl": "Ingesteld bij Instellingen → Systeem & prestaties"}


def _in_sync(want: dict[str, Any], effective: Any) -> bool:
    if not isinstance(effective, dict):
        return False
    if effective.get("percent") is not None and int(effective["percent"]) != want["cpu_percent"]:
        return False
    workers = effective.get("cpu_workers")
    return workers is None or int(workers) == want["cpu_workers"]
