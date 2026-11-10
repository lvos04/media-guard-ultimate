"""Hook-endpoints voor de Sonarr/Radarr-forks + analyse-job na import."""

from __future__ import annotations

import asyncio
import base64
import binascii
import json
from typing import Any

from fastapi import APIRouter, Depends, HTTPException, Request

from mediaguard import jobs
from mediaguard.audio import inventory
from mediaguard.audio.evaluate import AudioPolicy, evaluate_inventory, validate_claims
from mediaguard.context import AppContext, get_app_context
from mediaguard.db import dumps, utcnow
from mediaguard.encoding import service as enc
from mediaguard.encoding.client import EncoderError, get_encoder_client
from mediaguard.hooks import service
from mediaguard.identity import service as identity
from mediaguard.identity.engine import STATUS_LABELS_NL, EpisodeRef
from mediaguard.identity.parser import parse_release
from mediaguard.policy import profiles
from mediaguard.settings_registry import REGISTRY

router = APIRouter(prefix="/api/v1/hooks", tags=["hooks"])
MAX_BODY = 2 * 1024 * 1024


def register_settings() -> None:
    REGISTRY.register(*service.setting_defs())


def _authenticate(request: Request, ctx: AppContext, instance_id: str) -> str:
    """Hook-key (header of HTTP Basic met username=instance_id) of intern token."""
    from mediaguard.auth import internal_token_ok

    internal = request.headers.get("X-MediaGuard-Token")
    if internal and internal_token_ok(request, internal, ctx.settings.internal_token):
        return "svc:internal"
    key = request.headers.get("X-MediaGuard-Hook-Key")
    user = None
    auth = request.headers.get("Authorization", "")
    if not key and auth.lower().startswith("basic "):
        try:
            user, _, key = base64.b64decode(auth[6:]).decode().partition(":")
        except (binascii.Error, UnicodeDecodeError) as exc:
            raise HTTPException(401, "Ongeldige Basic-authenticatie") from exc
    if not key:
        raise HTTPException(401, "Hook-sleutel ontbreekt",
                            headers={"WWW-Authenticate": 'Basic realm="mediaguard-hooks"'})
    owner = ctx.hook_key_owner(key)
    if owner is None or (user is not None and user != instance_id):
        raise HTTPException(401, "Ongeldige hook-sleutel")
    if owner != instance_id:
        raise HTTPException(403, "Hook-sleutel hoort bij een andere instantie")
    return f"hook:{owner}"


async def _json(request: Request) -> dict[str, Any]:
    raw = await request.body()
    if len(raw) > MAX_BODY:
        raise HTTPException(413, "Verzoek te groot")
    try:
        data = json.loads(raw or b"{}")
    except ValueError as exc:
        raise HTTPException(422, "Ongeldige JSON") from exc
    if not isinstance(data, dict):
        raise HTTPException(422, "JSON-object verwacht")
    return data


@router.post("/pre-grab")
async def pre_grab(request: Request, ctx: AppContext = Depends(get_app_context)
                   ) -> dict[str, Any]:
    body = await _json(request)
    instance_id = str(body.get("instance_id") or "")
    if not instance_id:
        raise HTTPException(422, "instance_id ontbreekt")
    _authenticate(request, ctx, instance_id)
    # in de threadpool: de event-loop blijft vrij, ook als de database even bezet is
    result = await asyncio.to_thread(service.pre_grab, ctx, instance_id, body)
    if result.get("decision") == "accept":
        from mediaguard.downloads.failures import catchup_throttle

        throttled = catchup_throttle(ctx, instance_id)
        if throttled:
            return {"decision": "reject", "reason_code": "CATCHUP_THROTTLE",
                    "message": throttled, "score_adjustment": 0}
    return result


@router.post("/pre-import")
async def pre_import(request: Request, ctx: AppContext = Depends(get_app_context)
                     ) -> dict[str, Any]:
    body = await _json(request)
    instance_id = str(body.get("instance_id") or "")
    if not instance_id:
        raise HTTPException(422, "instance_id ontbreekt")
    _authenticate(request, ctx, instance_id)
    return await asyncio.to_thread(service.pre_import, ctx, instance_id, body)


@router.post("/arr-webhook/{instance_id}")
async def arr_webhook(instance_id: str, request: Request,
                      ctx: AppContext = Depends(get_app_context)) -> dict[str, Any]:
    _authenticate(request, ctx, instance_id)
    payload = await _json(request)
    return await asyncio.to_thread(service.store_webhook, ctx, instance_id, payload)


# ── analyse na import ─────────────────────────────────────────────────────────
@jobs.job_handler("analysis.post_import")
async def post_import_analysis(jc: jobs.JobContext) -> dict[str, Any]:
    ctx, p = jc.app, jc.payload
    instance_id = p["instance_id"]
    kind = p.get("kind") or "episode_file"
    provider_id = p.get("provider_id") or p.get("file_path") or "?"
    path = p.get("file_path")
    series, movie = p.get("series") or {}, p.get("movie") or {}
    reasons: list[str] = []

    # 1. identiteit
    id_status = None
    if movie:
        info = dict(movie)
        if movie.get("id") and not info.get("title"):
            row = ctx.db.fetch_one("SELECT title, year, imdb_id FROM lib_items WHERE "
                                   "instance_id=? AND kind='movie' AND provider_id=?",
                                   (instance_id, movie["id"]))
            if row is not None:
                info |= {"title": row["title"], "year": row["year"], "imdb_id": row["imdb_id"]}
        result = identity.evaluate(
            ctx, instance_id=instance_id, kind=kind, provider_id=provider_id, expected=[],
            path=path, release_title=p.get("release_title"), download_id=p.get("download_id"),
            movie=info, source="post_import", actor="svc:analysis")
        id_status = result.status
        reasons.append(f"Identiteit: {STATUS_LABELS_NL[result.status]}")
    else:
        result = identity.evaluate(
            ctx, instance_id=instance_id, kind=kind, provider_id=provider_id,
            expected=[EpisodeRef.from_dict(e) for e in p.get("episodes") or []], path=path,
            release_title=p.get("release_title"), download_id=p.get("download_id"),
            series=series, source="post_import", actor="svc:analysis")
        id_status = result.status
        reasons.append(f"Identiteit: {STATUS_LABELS_NL[result.status]}")

    # 2. audio (echte probe via encoder; terugval op arr-mediaInfo)
    mt = "anime" if (series.get("series_type") == "anime") else ("movie" if movie else None)
    pchain, dchain = enc.chains(ctx, instance_id=instance_id, media_type=mt,
                                series_id=series.get("id"), movie_id=movie.get("id"),
                                file_id=p.get("provider_id"))
    eff = profiles.effective_policy(ctx, pchain)
    probe = None
    if path:
        try:
            probe = await get_encoder_client(ctx).probe(path)
        except EncoderError as exc:
            reasons.append(f"Probe niet beschikbaar ({exc}); mediaInfo van arr gebruikt")
    inv = inventory.from_encoder_probe(probe) if probe else inventory.from_arr_media_info(
        p.get("media_info") or {})
    verdict = evaluate_inventory(inv, AudioPolicy.from_dict(eff["config"]["audio"]))
    claims = []
    if p.get("release_title"):
        parsed = parse_release(p["release_title"])
        for check in validate_claims(dual_audio=parsed.dual_audio,
                                     claimed_audio=parsed.audio_language_claims, inv=inv):
            claims.append(check.to_dict())
            if check.valid is False:
                service.record_false_claim(ctx, instance_id, p["release_title"],
                                           parsed.release_group, check.claim,
                                           inv.known_main_audio_languages)
                reasons.append(check.reason_nl)
    reasons.extend(verdict.reasons)

    # 3. AV1-kandidaat (alleen beslissen, niets uitvoeren)
    av1: dict[str, Any] = {"candidate": None}
    if probe:
        caps = await enc.capabilities(ctx)
        decision = enc.dv_decision(ctx, probe, dchain, caps)
        av1 = {"candidate": decision.executable and str(probe.get("codec")).lower() != "av1",
               "dv": decision.to_dict(), "codec": probe.get("codec"),
               "risk_class": enc.risk_class(probe)}
    ctx.db.execute(
        "INSERT INTO post_import_analysis(instance_id,kind,provider_id,file_path,identity_status,"
        "audio_json,av1_json,upgrade_wanted,reason_nl,created_at) VALUES (?,?,?,?,?,?,?,?,?,?)",
        (instance_id, kind, str(provider_id), path, id_status,
         dumps({"inventory": inv.to_dict(), "verdict": verdict.to_dict(), "claims": claims}),
         dumps(av1), int(verdict.keep_searching), "; ".join(reasons), utcnow()))
    ctx.events.publish("analysis.post_import", {"provider_id": provider_id,
                                                "identity": id_status, "audio": verdict.tier,
                                                "upgrade_wanted": verdict.keep_searching},
                       instance_id=instance_id)
    return {"identity": id_status, "audio": verdict.tier,
            "upgrade_wanted": verdict.keep_searching, "av1_candidate": av1.get("candidate")}
