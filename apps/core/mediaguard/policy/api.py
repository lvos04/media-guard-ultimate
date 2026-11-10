"""Policy-API: profielen, effectieve policy, simulatie ('Waarom deze release?'), TRaSH.

Hangt ook de identity- en audio-routers in (die staan niet apart in ``app.MODULES``).
"""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel, Field, ValidationError

from mediaguard.audio.api import router as audio_router
from mediaguard.audit import audit
from mediaguard.auth import Principal, require_admin, require_operator, require_viewer
from mediaguard.context import AppContext, get_app_context
from mediaguard.identity import service as identity_service
from mediaguard.identity.api import router as identity_router
from mediaguard.identity.engine import EpisodeRef
from mediaguard.policy import profiles, trash
from mediaguard.policy.ranker import Candidate, rank
from mediaguard.settings_registry import REGISTRY

router = APIRouter(tags=["policy"])
router.include_router(identity_router)
router.include_router(audio_router)


def register_settings() -> None:
    REGISTRY.register(*profiles.setting_defs(), *identity_service.setting_defs())


async def startup(ctx: AppContext) -> None:
    profiles.ensure_builtin(ctx)


# ── profielen ─────────────────────────────────────────────────────────────────
@router.get("/api/v1/profiles")
def list_profiles(_: Principal = Depends(require_viewer),
                  ctx: AppContext = Depends(get_app_context)) -> list[dict[str, Any]]:
    return profiles.list_profiles(ctx)


@router.get("/api/v1/profiles/{profile_id}")
def get_profile(profile_id: str, _: Principal = Depends(require_viewer),
                ctx: AppContext = Depends(get_app_context)) -> dict[str, Any]:
    p = profiles.get_profile(ctx, profile_id)
    if p is None:
        raise HTTPException(404, "Profiel niet gevonden")
    return p


class ProfileBody(BaseModel):
    name_nl: str | None = Field(default=None, max_length=200)
    description_nl: str | None = Field(default=None, max_length=2000)
    config: dict[str, Any]
    reason: str = Field(default="", max_length=500)


@router.put("/api/v1/profiles/{profile_id}")
def put_profile(profile_id: str, body: ProfileBody,
                principal: Principal = Depends(require_operator),
                ctx: AppContext = Depends(get_app_context)) -> dict[str, Any]:
    if not profile_id.replace("-", "").replace("_", "").isalnum() or len(profile_id) > 64:
        raise HTTPException(422, "Profiel-ID: alleen letters, cijfers, '-' en '_' (max 64)")
    old = profiles.get_profile(ctx, profile_id)
    try:
        saved = profiles.save_profile(ctx, profile_id, name_nl=body.name_nl,
                                      description_nl=body.description_nl, config=body.config,
                                      actor=principal.actor)
    except ValidationError as exc:
        raise HTTPException(422, exc.errors(include_url=False, include_context=False)) from exc
    audit(ctx.db, actor=principal.actor, action="policy.profile_update", target_type="profile",
          target_id=profile_id, reason=body.reason or None,
          old_value=old["config"] if old else None, new_value=saved["config"],
          undo_hint="Zet de vorige versie terug via profielgeschiedenis")
    ctx.events.publish("policy.profile_changed", {"id": profile_id, "version": saved["version"]})
    return saved


@router.post("/api/v1/profiles/{profile_id}/reset")
def reset_profile(profile_id: str, principal: Principal = Depends(require_operator),
                  ctx: AppContext = Depends(get_app_context)) -> dict[str, Any]:
    if profile_id not in profiles.BUILTIN:
        raise HTTPException(404, "Alleen ingebouwde profielen kunnen worden hersteld")
    data = profiles.BUILTIN[profile_id]
    return put_profile(profile_id, ProfileBody(config=data["config"],
                                               reason="Herstel standaard"), principal, ctx)


@router.delete("/api/v1/profiles/{profile_id}")
def delete_profile(profile_id: str, principal: Principal = Depends(require_admin),
                   ctx: AppContext = Depends(get_app_context)) -> dict[str, Any]:
    p = profiles.get_profile(ctx, profile_id)
    if p is None:
        raise HTTPException(404, "Profiel niet gevonden")
    if p["builtin"]:
        raise HTTPException(409, "Ingebouwde profielen kunnen niet worden verwijderd")
    used = ctx.db.fetch_one(
        "SELECT COUNT(*) AS n FROM settings_values WHERE key='policy.profile_id' AND "
        "value_json=?", (f'"{profile_id}"',))
    if used and used["n"]:
        raise HTTPException(409, f"Profiel is nog in gebruik op {used['n']} scope(s)")
    ctx.db.execute("DELETE FROM policy_profiles WHERE id=?", (profile_id,))
    audit(ctx.db, actor=principal.actor, action="policy.profile_delete", target_type="profile",
          target_id=profile_id, old_value=p["config"])
    return {"deleted": profile_id}


@router.get("/api/v1/profiles/{profile_id}/history")
def profile_history(profile_id: str, _: Principal = Depends(require_viewer),
                    ctx: AppContext = Depends(get_app_context)) -> list[dict[str, Any]]:
    return ctx.db.fetch_dicts(
        "SELECT * FROM policy_profile_history WHERE profile_id=? ORDER BY version DESC",
        (profile_id,))


# ── effectieve policy + simulatie ─────────────────────────────────────────────
@router.get("/api/v1/policy/effective")
def get_effective(instance_id: str | None = None, media_type: str | None = None,
                  series_id: int | None = None, movie_id: int | None = None,
                  season: int | None = None, episode_id: int | None = None,
                  file_id: int | None = None, _: Principal = Depends(require_viewer),
                  ctx: AppContext = Depends(get_app_context)) -> dict[str, Any]:
    chain = profiles.build_chain(instance_id=instance_id, media_type=media_type,
                                 series_id=series_id, movie_id=movie_id, season=season,
                                 episode_id=episode_id, file_id=file_id)
    return profiles.effective_policy(ctx, chain)


class CandidateIn(BaseModel):
    title: str = Field(min_length=1, max_length=500)
    size: int | None = Field(default=None, ge=0)
    resolution: int | None = None
    source: str | None = None
    languages: list[str] = Field(default_factory=list, max_length=20)
    custom_format_score: int | None = None
    custom_formats: list[str] = Field(default_factory=list)
    release_group: str | None = None
    indexer: str | None = None
    guid: str | None = None


class SimulateBody(BaseModel):
    candidates: list[CandidateIn] = Field(min_length=1, max_length=500)
    profile_id: str | None = None
    profile: dict[str, Any] | None = None
    instance_id: str | None = None
    media_type: str | None = None
    series_id: int | None = None
    movie_id: int | None = None
    expected_episodes: list[dict[str, int | None]] = Field(default_factory=list)
    use_trash_scores: bool = True


@router.post("/api/v1/policy/simulate")
def simulate(body: SimulateBody, _: Principal = Depends(require_viewer),
             ctx: AppContext = Depends(get_app_context)) -> dict[str, Any]:
    """Rangschik kandidaat-releases zonder iets op te slaan."""
    chain = profiles.build_chain(instance_id=body.instance_id, media_type=body.media_type,
                                 series_id=body.series_id, movie_id=body.movie_id)
    eff = profiles.effective_policy(ctx, chain)
    cfg = eff["config"]
    if body.profile_id:
        p = profiles.get_profile(ctx, body.profile_id)
        if p is None:
            raise HTTPException(404, "Profiel niet gevonden")
        cfg = profiles.ProfileConfig.model_validate(p["config"]).model_dump()
        eff = {**eff, "profile_id": p["id"], "profile_name_nl": p["name_nl"]}
    if body.profile is not None:
        try:
            cfg = profiles.ProfileConfig.model_validate(body.profile).model_dump()
        except ValidationError as exc:
            raise HTTPException(422, exc.errors(include_url=False,
                                                include_context=False)) from exc
        eff = {**eff, "profile_id": "inline", "profile_name_nl": "Inline profiel"}
    formats = trash.load_formats(ctx) if body.use_trash_scores else []
    expected = [EpisodeRef.from_dict(e) for e in body.expected_episodes]
    is_anime = cfg["audio"]["mode"] == "anime"
    ranked = rank([Candidate.from_dict(c.model_dump()) for c in body.candidates], cfg,
                  expected=expected or None, formats=formats or None, is_anime=is_anime)
    winner = next((r for r in ranked if not r.rejected), None)
    return {
        "profile_id": eff["profile_id"], "profile_name_nl": eff["profile_name_nl"],
        "ranking_order": ["identity", *cfg["ranking_order"]],
        "winner": winner.candidate.title if winner else None,
        "why_nl": winner.explanation_nl if winner else "Geen release voldoet aan de harde regels.",
        "results": [r.to_dict() for r in ranked],
    }


# ── TRaSH custom formats ──────────────────────────────────────────────────────
class TrashImportBody(BaseModel):
    formats: list[dict[str, Any]] | None = None
    url: str | None = Field(default=None, max_length=2000)


@router.get("/api/v1/policy/trash")
def trash_list(_: Principal = Depends(require_viewer),
               ctx: AppContext = Depends(get_app_context)) -> list[dict[str, Any]]:
    return ctx.db.fetch_dicts(
        "SELECT id, trash_id, name, scores_json, source, imported_at, imported_by "
        "FROM trash_custom_formats ORDER BY name")


@router.post("/api/v1/policy/trash/import")
async def trash_import(body: TrashImportBody, principal: Principal = Depends(require_operator),
                       ctx: AppContext = Depends(get_app_context)) -> dict[str, Any]:
    if bool(body.formats) == bool(body.url):
        raise HTTPException(422, "Geef óf 'formats' (geüploade JSON) óf 'url'")
    if body.url:
        try:
            items = await trash.fetch_formats(body.url,
                                              transport=ctx.services.get("trash_transport"))
        except ValueError as exc:
            raise HTTPException(422, str(exc)) from exc
        except Exception as exc:  # noqa: BLE001 - netwerkfout naar UI
            raise HTTPException(502, f"Ophalen mislukt: {type(exc).__name__}") from exc
        from urllib.parse import urlparse
        source = f"url:{urlparse(body.url).netloc}"
    else:
        items, source = body.formats or [], "upload"
    try:
        result = trash.import_formats(ctx, items, source=source, actor=principal.actor)
    except ValueError as exc:
        raise HTTPException(422, str(exc)) from exc
    audit(ctx.db, actor=principal.actor, action="policy.trash_import", target_type="trash",
          target_id=source, new_value={"imported": result["imported"]})
    return result


@router.delete("/api/v1/policy/trash/{cf_id}")
def trash_delete(cf_id: int, principal: Principal = Depends(require_operator),
                 ctx: AppContext = Depends(get_app_context)) -> dict[str, Any]:
    if ctx.db.execute("DELETE FROM trash_custom_formats WHERE id=?", (cf_id,)) == 0:
        raise HTTPException(404, "Custom format niet gevonden")
    audit(ctx.db, actor=principal.actor, action="policy.trash_delete", target_type="trash",
          target_id=cf_id)
    return {"deleted": cf_id}


@router.get("/api/v1/policy/hook-decisions")
def hook_decisions(instance_id: str | None = None, limit: int = Query(100, le=1000),
                   _: Principal = Depends(require_viewer),
                   ctx: AppContext = Depends(get_app_context)) -> list[dict[str, Any]]:
    sql = "SELECT * FROM hook_decisions"
    params: list[Any] = []
    if instance_id:
        sql += " WHERE instance_id=?"
        params.append(instance_id)
    return ctx.db.fetch_dicts(sql + " ORDER BY id DESC LIMIT ?", [*params, limit])
