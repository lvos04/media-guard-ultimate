"""REST-brug voor MediaGuard core: exact packages/contracts/encoder-bridge.md."""

from __future__ import annotations

import asyncio
import os
from pathlib import Path
from typing import Any, Literal

from fastapi import APIRouter, Depends, HTTPException, Request, status
from pydantic import BaseModel, ConfigDict, Field

from app import __version__
from app.config import Settings
from app.db import Database
from app.mediaguard import service, trials
from app.mediaguard.auth import require_token
from app.mediaguard.capabilities import PAUSE_PREFIX, CapabilityService, apply_lane_gates
from app.mediaguard.importer import ImportError_, import_standalone_db
from app.mediaguard.policy import DvMode, MediaGuardPolicy, QualityProfileName
from app.onderhoud import cache_limits, pause_overview, storage_guard

AV1_COMMIT = "4b31986b8e91bc3d03f5372436e02644efe0c41d"


class PathRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    path: str = Field(min_length=1, max_length=4096)


class PlanRequest(PathRequest):
    policy: MediaGuardPolicy = Field(default_factory=MediaGuardPolicy)
    trial_only: bool = False
    run_trials: bool = False


class MediaRef(BaseModel):
    model_config = ConfigDict(extra="allow")
    instance_id: str | None = None
    kind: str | None = None
    provider_id: int | str | None = None


class JobRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    source_path: str = Field(min_length=1, max_length=4096)
    correlation_id: str | None = Field(default=None, max_length=128)
    media_ref: MediaRef | None = None
    policy: MediaGuardPolicy = Field(default_factory=MediaGuardPolicy)
    trial_only: bool = False
    # Banen die bij MediaGuard vol zitten: komt dit bestand daarop uit, dan geen job maar
    # 409 LANE_FULL (MediaGuard zoekt dan werk voor de andere baan, verbeterpunt 6).
    avoid_lanes: list[Literal["cpu", "gpu"]] = Field(default_factory=list, max_length=2)


class CommitRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    sha256: str | None = Field(default=None, pattern="^[0-9a-f]{64}$")
    # Geen max_length: te lange tekst wordt door de service ingekort (geen 422
    # waardoor een commit/afwijzing van core blijft hangen).
    note: str | None = None


class RejectRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    reason: str | None = None


class TrialRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    path: str = Field(min_length=1, max_length=4096)
    samples: list[trials.TrialSample] | None = Field(default=None, max_length=8)
    settings: list[trials.TrialSetting] | None = Field(default=None, max_length=12)
    dv_mode: DvMode | None = None
    quality_profile: QualityProfileName = "gebalanceerd"
    custom: trials.CustomProfile | None = None


class StorageSettingsRequest(BaseModel):
    """Vrije-ruimtebodem van de cache (GB). Onder deze grens pauzeren de banen."""

    model_config = ConfigDict(extra="forbid")
    cache_min_free_gb: int = Field(ge=1, le=1_000_000)
    cache_warning_gb: int | None = Field(default=None, ge=2, le=1_000_001)


class ResourceSettingsRequest(BaseModel):
    """Systeembelasting vanuit MediaGuard (Instellingen → Systeem & prestaties)."""

    model_config = ConfigDict(extra="forbid")
    cpu_percent: int = Field(ge=10, le=100)
    cpu_workers: int = Field(ge=0, le=8)
    gpu_workers: int = Field(ge=0, le=8)
    quiet_start: str | None = Field(default=None, pattern=r"^([01]\d|2[0-3]):[0-5]\d$")
    quiet_end: str | None = Field(default=None, pattern=r"^([01]\d|2[0-3]):[0-5]\d$")
    max_cpu_temp: int = Field(default=85, ge=50, le=110)
    nice: int = Field(default=10, ge=0, le=19)


# Pauze omdat er in de mediaserver gekeken wordt: eigen voorvoegsel, zodat noch de
# capability-poorten noch een handmatige pauze hem per ongeluk opheffen of overschrijven.
PLAYBACK_HOLD_PREFIX = "Kijken:"


class PlaybackHoldRequest(BaseModel):
    """Vanuit core: iemand kijkt in Emby/Jellyfin/Plex → nieuwe encodes wachten."""

    model_config = ConfigDict(extra="forbid")
    active: bool
    lanes: list[str] = Field(default_factory=list)
    reason: str = Field(default="", max_length=200)


class EmergencyStopRequest(BaseModel):
    """Vanuit core: de noodstop van MediaGuard (rapport 6, N1)."""

    model_config = ConfigDict(extra="forbid")
    active: bool
    reason: str = Field(default="", max_length=500)


def apply_playback_hold(database: Any, active: bool, lanes: list[str],
                        reason: str) -> dict[str, Any]:
    """Zet alleen pauzes die van ons zijn of op een vrije baan; heft alleen eigen pauzes op.
    Lopende encodes draaien door (met hun lage prioriteit); alleen nieuwe wachten."""

    out: dict[str, Any] = {}
    for row in database.fetch_all("SELECT baan,gepauzeerd,pauze_reden FROM worker_config"):
        lane = str(row["baan"])
        current = str(row["pauze_reden"] or "")
        ours = int(row["gepauzeerd"]) and current.startswith(PLAYBACK_HOLD_PREFIX)
        if active and lane in lanes:
            if not int(row["gepauzeerd"]) or ours:
                database.execute(
                    "UPDATE worker_config SET gepauzeerd=1,pauze_reden=? WHERE baan=?",
                    (f"{PLAYBACK_HOLD_PREFIX} {reason or 'iemand kijkt'}"[:200], lane))
                out[lane] = "wacht"
            else:
                out[lane] = "al gepauzeerd"
        elif ours:
            database.execute(
                "UPDATE worker_config SET gepauzeerd=0,pauze_reden=NULL WHERE baan=?", (lane,))
            out[lane] = "hervat"
        else:
            out[lane] = "ongewijzigd"
    return {"active": active, "lanes": out}


def _resources_view(database: Any) -> dict[str, Any]:
    from app.cpubudget import from_database

    lanes = {str(r["baan"]): dict(r) for r in database.fetch_all(
        "SELECT baan,aantal,nice,stilte_start,stilte_einde,gepauzeerd FROM worker_config")}
    temp = database.fetch_one("SELECT cpu_max_temperatuur FROM instelling WHERE id=1")
    from app.cpuconfine import status as confine_status

    return {**from_database(database), "lanes": lanes, "cpu_grens": confine_status(database),
            "max_cpu_temp": int(temp["cpu_max_temperatuur"]) if temp else None}


class ImportRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    db_path: str = Field(min_length=1, max_length=4096)
    path_map: dict[str, str] = Field(default_factory=dict)
    dry_run: bool = True


def _db(request: Request) -> Database:
    database: Database = request.app.state.database
    return database


def _settings(request: Request) -> Settings:
    settings: Settings = request.app.state.settings
    return settings


def _caps_service(request: Request) -> CapabilityService:
    caps: CapabilityService = request.app.state.capabilities
    return caps


def _raise(exc: service.BridgeError) -> HTTPException:
    return HTTPException(exc.status_code, str(exc))


def trials_refusal(info: dict[str, Any], dv_mode: str | None) -> str | None:
    """Bronnen waarvoor een proefmatrix niets zinnigs kan meten (gedeeld door /plan)."""

    if info.get("hdr10plus"):
        # Proefencodes gebruiken de SVT-commandobouwer; die laat HDR10+ vallen.
        # Een geverifieerde HDR10+-proef gaat via POST /jobs met trial_only=true.
        return (
            "HDR10+: proefmatrix niet ondersteund; gebruik een job met trial_only en "
            "hdr10plus_mode=convert_verified"
        )
    dv_profile = int(info.get("dv_profile") or 0)
    if dv_profile == 5:
        return "DV profiel 5: niet ondersteund (geen HDR10-terugval)"
    if dv_profile and dv_mode in {None, "keep_original"}:
        return "Dolby Vision-bron: kies dv_mode trial_copy om een proefencode te maken"
    return None


def create_mediaguard_router() -> APIRouter:
    router = APIRouter(
        prefix="/api/mediaguard/v1",
        tags=["mediaguard"],
        dependencies=[Depends(require_token)],
    )
    trial_lock = asyncio.Semaphore(1)

    @router.get("/health")
    async def health(request: Request) -> dict[str, Any]:
        database = _db(request)
        settings = _settings(request)
        # Zelfherstellend: hooguit elke 5 min opnieuw meten en de fail-closed
        # baanpauzes bijwerken (alleen pauzes met MediaGuard-reden).
        caps = await _caps_service(request).detect(max_age_seconds=300)
        if settings.container_mode:
            apply_lane_gates(database, caps, settings)
        reasons = []
        if not database.reachable():
            reasons.append("database onbereikbaar")
        try:
            settings.validate_storage()
        except Exception as exc:  # noqa: BLE001 - rapporteren, niet crashen
            reasons.append(f"opslag: {exc}")
        # Geen (Intel-)GPU is een geldige CPU-only-opstelling, geen degradatie; alleen
        # een GPU die er is maar niet werkt telt als probleem.
        gpu_expected = caps.get("gpu_status", {}).get("severity") == "warning"
        notes: list[str] = []
        for lane in ("cpu", "gpu"):
            if not caps["lanes"][lane]["enabled"]:
                text = f"{lane}-baan uit: {caps['lanes'][lane]['reason']}"
                (reasons if lane == "cpu" or gpu_expected else notes).append(text)
        overview = await asyncio.to_thread(pause_overview, database, settings)
        from app.planner import deliberate_block

        for paused in overview["lanes_paused"]:
            # Capability-pauzes (prefix "MediaGuard:") staan hierboven al als reden.
            if not str(paused["reason"] or "").startswith(PAUSE_PREFIX) and not (
                paused["lane"] == "gpu" and not gpu_expected
            ):
                text = f"{paused['lane']}-baan gepauzeerd: {paused['reason']}"
                # kijken, eigen pauze: bewust, geen degradatie (verbeterlijst 8)
                (notes if deliberate_block(paused["reason"]) else reasons).append(text)
        cpu_ok = caps["lanes"]["cpu"]["enabled"] and database.reachable()
        return {
            "status": "ok" if not reasons else ("degraded" if cpu_ok else "unavailable"),
            "lanes_paused": overview["lanes_paused"],
            "lanes": overview["lanes"],
            "storage_guard": overview["storage_guard"],
            "version": __version__,
            "av1_commit": os.environ.get("AV1_COMMIT", AV1_COMMIT),
            "container_mode": settings.container_mode,
            "replacement_mode": settings.replacement_mode,
            "reasons": reasons,
            "notes": notes,
            "gpu_status": caps.get("gpu_status"),
            "vastlopers": await asyncio.to_thread(_stall_summary, database),
            # baan > 30 min stil met wachtend werk om dezelfde reden (rapport 8, A5)
            "baan_stilstand": await asyncio.to_thread(_lane_stalls, database),
        }

    @router.get("/capabilities")
    async def capabilities(request: Request, refresh: bool = False) -> dict[str, Any]:
        caps = await _caps_service(request).detect(refresh=refresh, run_qsv_probe=refresh)
        overview = await asyncio.to_thread(pause_overview, _db(request), _settings(request))
        # Kopie: de gecachte detectie blijft puur; pauzestatus is altijd actueel.
        return {
            **caps,
            "lanes_paused": overview["lanes_paused"],
            "lane_pause": overview["lanes"],
            "storage_guard": overview["storage_guard"],
        }

    @router.get("/settings/resources")
    async def get_resource_settings(request: Request) -> dict[str, Any]:
        return await asyncio.to_thread(_resources_view, _db(request))

    @router.put("/settings/resources")
    async def put_resource_settings(
        body: ResourceSettingsRequest, request: Request
    ) -> dict[str, Any]:
        """CPU-budget, encodes tegelijk, stille uren en temperatuurgrens in één keer."""

        if (body.quiet_start is None) != (body.quiet_end is None):
            raise HTTPException(422, "quiet_start en quiet_end horen samen")
        database = _db(request)

        def apply() -> dict[str, Any]:
            database.execute(
                "UPDATE instelling SET av1_cpu_procent=?, cpu_max_percent=?, "
                "cpu_max_temperatuur=?, max_gelijktijdige_encodes=? WHERE id=1",
                (body.cpu_percent, body.cpu_percent, body.max_cpu_temp,
                 max(1, body.cpu_workers + body.gpu_workers)))
            for lane, count in (("cpu", body.cpu_workers), ("gpu", body.gpu_workers)):
                database.execute(
                    "UPDATE worker_config SET aantal=?, nice=?, stilte_start=?, stilte_einde=? "
                    "WHERE baan=?",
                    (count, body.nice, body.quiet_start, body.quiet_end, lane))
            return _resources_view(database)

        return await asyncio.to_thread(apply)

    @router.get("/settings/emergency-stop")
    async def get_emergency_stop(request: Request) -> dict[str, Any]:
        from app import noodstop

        database = _db(request)
        state = await asyncio.to_thread(noodstop.load, database)
        return {"active": bool(state["active"]), "reason": state["reason"],
                "since": state["since"],
                "paused_jobs": await asyncio.to_thread(noodstop.paused_count, database)}

    @router.put("/settings/emergency-stop")
    async def put_emergency_stop(body: EmergencyStopRequest,
                                 request: Request) -> dict[str, Any]:
        from app import noodstop

        return await asyncio.to_thread(noodstop.set_state, _db(request), body.active,
                                       body.reason)

    @router.put("/settings/playback-hold")
    async def put_playback_hold(body: PlaybackHoldRequest, request: Request) -> dict[str, Any]:
        lanes = [lane for lane in body.lanes if lane in ("cpu", "gpu")]
        return await asyncio.to_thread(apply_playback_hold, _db(request), body.active, lanes,
                                       body.reason)

    @router.get("/settings/storage")
    async def get_storage_settings(request: Request) -> dict[str, Any]:
        overview = await asyncio.to_thread(pause_overview, _db(request), _settings(request))
        return {**overview["storage_guard"], "lanes_paused": overview["lanes_paused"]}

    @router.put("/settings/storage")
    async def put_storage_settings(
        body: StorageSettingsRequest, request: Request
    ) -> dict[str, Any]:
        """Pauzebodem instellen vanuit core; daarna direct herbeoordelen."""

        database = _db(request)
        settings = _settings(request)
        current = cache_limits(database, settings)
        warning = body.cache_warning_gb
        if warning is None:
            # Waarschuwingsgrens moet boven de bodem blijven (legacy-validatie).
            warning = max(current["configured_warning_gb"], body.cache_min_free_gb + 100)
        if body.cache_min_free_gb >= warning:
            raise HTTPException(422, "cache_min_free_gb moet lager zijn dan cache_warning_gb")
        if (
            settings.storage_mode == "systeemschijf"
            and body.cache_min_free_gb < settings.system_disk_reserve_gb
        ):
            raise HTTPException(
                422, "Cachepauze mag niet onder de vaste systeemschijfreserve komen"
            )
        database.execute(
            "UPDATE instelling SET cache_pauze_gb=?,cache_waarschuwing_gb=? WHERE id=1",
            (body.cache_min_free_gb, warning),
        )
        guard = await asyncio.to_thread(storage_guard, database, settings)
        overview = await asyncio.to_thread(pause_overview, database, settings)
        return {
            **overview["storage_guard"],
            "status": guard["status"],
            "lanes_paused": overview["lanes_paused"],
        }

    @router.post("/probe")
    async def probe_file(body: PathRequest, request: Request) -> dict[str, Any]:
        try:
            return await asyncio.to_thread(service.probe_report, _settings(request), body.path)
        except service.BridgeError as exc:
            raise _raise(exc) from exc

    @router.post("/plan")
    async def plan(body: PlanRequest, request: Request) -> dict[str, Any]:
        settings = _settings(request)
        caps = await _caps_service(request).detect()
        try:
            path = service.resolve_media_path(settings, body.path)
            info = await asyncio.to_thread(service.enriched_probe, path)
            result = await asyncio.to_thread(
                service.build_plan,
                settings,
                caps,
                path,
                body.policy,
                trial_only=body.trial_only,
                info=info,
            )
        except service.BridgeError as exc:
            raise _raise(exc) from exc
        refusal = (
            trials_refusal(info, body.policy.dv_mode)
            if body.run_trials and result["action"] == "hercoderen"
            else None
        )
        if refusal is not None:
            # Zelfde weigeringen als /trials: geen proefmatrix, het basisplan blijft.
            result["smart"] = {"recommendation": None, "refused": refusal}
            result["warnings"].append(f"per-title-proef overgeslagen: {refusal}")
        elif body.run_trials and result["action"] == "hercoderen":
            async with trial_lock:
                smart = await asyncio.to_thread(
                    trials.run_trials,
                    _db(request),
                    settings,
                    path=path,
                    info=info,
                    profile_name=body.policy.quality_profile,
                    custom=None,
                    samples=None,
                    trial_settings=None,
                    dv_mode=body.policy.dv_mode,
                    gpu_available=bool(caps["lanes"]["gpu"]["enabled"]),
                    vmaf_available=bool(caps["quality_metrics"]["libvmaf"]),
                )
            result["smart"] = smart
            recommendation = smart.get("recommendation") or {}
            if recommendation.get("action") != "encode":
                result["action"] = result["actie"] = "overslaan"
                result["approved_for_execution"] = False
                result["reason"] = result["reden"] = f"per-title: {recommendation.get('reason')}"
            else:
                result["recommended_policy"] = {
                    "encoder": "gpu"
                    if recommendation["recommended"]["encoder"] == "av1_qsv"
                    else "cpu",
                    "crf": recommendation["recommended"].get("crf"),
                    "cq": recommendation["recommended"].get("cq"),
                    "preset": recommendation["recommended"].get("preset"),
                    "film_grain": recommendation["recommended"].get("film_grain"),
                }
        return result

    @router.post("/jobs", status_code=status.HTTP_201_CREATED)
    async def create_job(body: JobRequest, request: Request) -> dict[str, Any]:
        caps = await _caps_service(request).detect()
        try:
            job_id = await asyncio.to_thread(
                service.create_job,
                _db(request),
                _settings(request),
                caps,
                source_path=body.source_path,
                policy=body.policy,
                correlation_id=body.correlation_id,
                media_ref=body.media_ref.model_dump() if body.media_ref else None,
                trial_only=body.trial_only,
                avoid_lanes=set(body.avoid_lanes),
            )
            view = service.job_view(_db(request), job_id)
        except service.BridgeError as exc:
            raise _raise(exc) from exc
        return {"job_id": job_id, "status": view["status"], "skip_reason": view["skip_reason"],
                "lane": view["lane"]}

    @router.get("/jobs")
    async def list_jobs(request: Request, limit: int = 100) -> list[dict[str, Any]]:
        return service.list_jobs(_db(request), max(1, min(limit, 500)))

    @router.get("/jobs-awaiting-commit")
    async def jobs_awaiting_commit(request: Request) -> dict[str, Any]:
        """Engine-jobs die nog op core's commit/reject wachten (rapport 6, N5): core vergelijkt
        dit met zijn eigen stand en meldt een gemiste commit alsnog."""
        rows = await asyncio.to_thread(
            _db(request).fetch_all,
            "SELECT j.id,j.mg_correlation_id,j.mg_media_ref,j.pogingen,j.kwaliteit_herkansd,"
            "j.klaar_op,j.vmaf_score,j.resultaat_grootte,f.pad FROM job j "
            "JOIN bestand f ON f.id=j.bestand_id WHERE j.status='wacht_op_commit' "
            "ORDER BY j.id")
        # Rapport 8, A6: een geslaagde herkansing (core had het item al op 'failed') moet
        # core kunnen herkennen: correlatie-id, media-ref en pad per job erbij.
        return {
            "job_ids": [int(r["id"]) for r in rows],
            "jobs": [{
                "job_id": int(r["id"]),
                "correlation_id": r["mg_correlation_id"],
                "media_ref": r["mg_media_ref"],
                "path": r["pad"],
                "attempts": int(r["pogingen"] or 0),
                "retried": int(r["pogingen"] or 0) > 1 or bool(int(r["kwaliteit_herkansd"] or 0)),
                "finished_at": r["klaar_op"],
                "vmaf": r["vmaf_score"],
                "result_size": r["resultaat_grootte"],
            } for r in rows],
        }

    @router.get("/jobs/{job_id}")
    async def get_job(job_id: int, request: Request) -> dict[str, Any]:
        try:
            return service.job_view(_db(request), job_id)
        except service.BridgeError as exc:
            raise _raise(exc) from exc

    @router.post("/jobs/{job_id}/cancel")
    async def cancel(job_id: int, request: Request) -> dict[str, Any]:
        try:
            return service.cancel_job(_db(request), _settings(request), job_id)
        except service.BridgeError as exc:
            raise _raise(exc) from exc

    @router.post("/jobs/{job_id}/committed")
    async def committed(job_id: int, request: Request, body: CommitRequest | None = None) -> Any:
        body = body or CommitRequest()
        try:
            return service.mark_committed(
                _db(request), _settings(request), job_id, body.sha256, body.note
            )
        except service.BridgeError as exc:
            raise _raise(exc) from exc

    @router.post("/jobs/{job_id}/rejected")
    async def rejected(job_id: int, request: Request, body: RejectRequest | None = None) -> Any:
        body = body or RejectRequest()
        try:
            return service.mark_rejected(_db(request), _settings(request), job_id, body.reason)
        except service.BridgeError as exc:
            raise _raise(exc) from exc

    @router.post("/trials")
    async def run_trials(body: TrialRequest, request: Request) -> dict[str, Any]:
        settings = _settings(request)
        caps = await _caps_service(request).detect()
        try:
            path = service.resolve_media_path(settings, body.path)
            info = await asyncio.to_thread(service.enriched_probe, path)
        except service.BridgeError as exc:
            raise _raise(exc) from exc
        refusal = trials_refusal(info, body.dv_mode)
        if refusal is not None:
            raise HTTPException(409, refusal)
        if body.samples and any(
            sample.start + sample.duration > float(info["duration"]) + 0.5
            for sample in body.samples
        ):
            raise HTTPException(422, "sample valt buiten de speelduur")
        async with trial_lock:
            try:
                return await asyncio.to_thread(
                    trials.run_trials,
                    _db(request),
                    settings,
                    path=path,
                    info=info,
                    profile_name=body.quality_profile,
                    custom=body.custom,
                    samples=body.samples,
                    trial_settings=body.settings,
                    dv_mode=body.dv_mode,
                    gpu_available=bool(caps["lanes"]["gpu"]["enabled"]),
                    vmaf_available=bool(caps["quality_metrics"]["libvmaf"]),
                )
            except RuntimeError as exc:
                raise HTTPException(500, f"proefencode mislukt: {exc}") from exc

    @router.post("/import/standalone-db")
    async def import_standalone(body: ImportRequest, request: Request) -> dict[str, Any]:
        """P8: neem een oude standalone app.db over (eerst dry-run, dan echt)."""

        planner_tasks = getattr(request.app.state.planner, "tasks", {})
        if any(not task.done() for task in planner_tasks.values()):
            raise HTTPException(409, "er lopen encodes; wacht tot de wachtrij leeg is")
        try:
            return await asyncio.to_thread(
                import_standalone_db,
                _db(request),
                _settings(request),
                Path(body.db_path),
                body.path_map,
                dry_run=body.dry_run,
            )
        except ImportError_ as exc:
            raise HTTPException(409, str(exc)) from exc

    @router.get("/trials/{run_id}")
    async def get_trial(run_id: int, request: Request) -> dict[str, Any]:
        try:
            return trials.trial_view(_db(request), run_id)
        except KeyError as exc:
            raise HTTPException(404, "proefrun bestaat niet") from exc

    return router


def _stall_summary(database: Database) -> dict[str, Any]:
    """Vastlopers van de laatste 12 uur (core toont een aandachtspunt bij terugschalen)."""
    from app import vastlopers

    return vastlopers.summary(database)


def _lane_stalls(database: Database) -> list[dict[str, Any]]:
    from app.planner import lane_stalls

    return lane_stalls(database)
