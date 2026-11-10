"""Identity-API (wordt ingehangen door ``mediaguard.policy.api``)."""

from __future__ import annotations

from typing import Any, Literal

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field

from mediaguard.audit import audit
from mediaguard.auth import Principal, require_operator, require_viewer
from mediaguard.context import AppContext, get_app_context
from mediaguard.identity import adapters, service
from mediaguard.identity.engine import EpisodeRef, Evidence, IdentityResult
from mediaguard.identity.parser import parse_release

router = APIRouter(prefix="/api/v1/identity", tags=["identity"])

Kind = Literal["episode_file", "movie_file", "episode", "movie"]


class ParseBody(BaseModel):
    name: str = Field(min_length=1, max_length=1000)


@router.post("/parse")
def parse(body: ParseBody, _: Principal = Depends(require_viewer)) -> dict[str, Any]:
    return parse_release(body.name).to_dict()


class EpisodeIn(BaseModel):
    season: int | None = None
    episode: int | None = None
    absolute: int | None = None


class VerifyBody(BaseModel):
    expected: list[EpisodeIn] = Field(default_factory=list, max_length=50)
    path: str | None = Field(default=None, max_length=4096)
    release_title: str | None = Field(default=None, max_length=1000)
    download_id: str | None = Field(default=None, max_length=200)
    hashes: dict[str, str] = Field(default_factory=dict)
    series: dict[str, Any] | None = None
    observed_series_ids: dict[str, Any] | None = None
    duration_seconds: float | None = None
    expected_runtime_minutes: float | None = None


def _refs(items: list[EpisodeIn]) -> list[EpisodeRef]:
    return [EpisodeRef(i.season, i.episode, i.absolute) for i in items]


@router.get("/{instance_id}/{kind}/{provider_id}/evidence")
def evidence(instance_id: str, kind: Kind, provider_id: str,
             _: Principal = Depends(require_viewer),
             ctx: AppContext = Depends(get_app_context)) -> dict[str, Any]:
    # de aflevering/film-pagina vraagt per aflevering; imports slaan per bestand op
    history = service.history(ctx, instance_id, kind, provider_id)
    if not history:
        return {"instance_id": instance_id, "kind": kind, "provider_id": provider_id,
                "status": "unresolved", "history": [],
                "reason_nl": "Nog geen identiteitscontrole uitgevoerd"}
    current = history[0]
    return {"instance_id": instance_id, "kind": kind, "provider_id": provider_id,
            "status": current["status"], "current": current, "history": history}


@router.post("/{instance_id}/{kind}/{provider_id}/verify")
def verify(instance_id: str, kind: Kind, provider_id: str, body: VerifyBody,
           principal: Principal = Depends(require_operator),
           ctx: AppContext = Depends(get_app_context)) -> dict[str, Any]:
    result = service.evaluate(
        ctx, instance_id=instance_id, kind=kind, provider_id=provider_id,
        expected=_refs(body.expected), path=body.path, release_title=body.release_title,
        download_id=body.download_id, hashes=body.hashes, series=body.series,
        observed_series_ids=body.observed_series_ids, duration_seconds=body.duration_seconds,
        expected_runtime_minutes=body.expected_runtime_minutes,
        is_movie=kind in ("movie", "movie_file"), source="api", actor=principal.actor)
    audit(ctx.db, actor=principal.actor, action="identity.verify", target_type=kind,
          target_id=provider_id, instance_id=instance_id, new_value={"status": result.status},
          reason=result.reason_nl)
    return result.to_dict()


class DecisionBody(BaseModel):
    status: Literal["verified", "mismatch"]
    actual: list[EpisodeIn] = Field(default_factory=list, max_length=50)
    expected: list[EpisodeIn] = Field(default_factory=list, max_length=50)
    note: str = Field(default="", max_length=1000)


@router.post("/{instance_id}/{kind}/{provider_id}/decision")
def manual_decision(instance_id: str, kind: Kind, provider_id: str, body: DecisionBody,
                    principal: Principal = Depends(require_operator),
                    ctx: AppContext = Depends(get_app_context)) -> dict[str, Any]:
    """Handmatige review-uitkomst. Wordt het sterkste bewijs bij volgende controles."""
    expected = _refs(body.expected)
    actual = _refs(body.actual) or (expected if body.status == "verified" else [])
    ev = Evidence("user_confirmed", "supports" if body.status == "verified" else "contradicts",
                  f"Handmatig beoordeeld door {principal.username}: {body.note or body.status}",
                  claimed=actual)
    result = IdentityResult(body.status, 1.0, body.status == "mismatch" and bool(actual),
                            ev.detail_nl, expected, actual, [ev])
    # opslaan onder de sleutel van het actuele resultaat, zodat dit 'Wacht op jou' oplost
    key_kind, key_pid = service.canonical_key(ctx, instance_id, kind, provider_id)
    old = service.latest(ctx, instance_id, key_kind, key_pid)
    if not expected and old:
        result.expected = [EpisodeRef.from_dict(e) for e in old["expected"]]
        if body.status == "verified" and not body.actual:
            result.actual = list(result.expected)
    service.save(ctx, instance_id=instance_id, kind=key_kind, provider_id=key_pid,
                 path=old.get("file_path") if old else None,
                 result=result, source="user", actor=principal.actor)
    audit(ctx.db, actor=principal.actor, action="identity.decision", target_type=key_kind,
          target_id=key_pid, instance_id=instance_id,
          old_value={"status": old["status"]} if old else None,
          new_value={"status": body.status}, reason=body.note or None,
          undo_hint="Voer een nieuwe beoordeling uit")
    return result.to_dict()


class MappingBody(BaseModel):
    tvdb_id: int | None = None
    absolute: int | None = None
    season: int | None = None
    episode: int | None = None


@router.post("/anime-mappings/thexem/{tvdb_id}/refresh")
async def refresh_thexem(tvdb_id: int, principal: Principal = Depends(require_operator),
                         ctx: AppContext = Depends(get_app_context)) -> dict[str, Any]:
    transport = ctx.services.get("thexem_transport")
    try:
        count = await adapters.fetch_thexem(ctx.db, tvdb_id, transport=transport)
    except Exception as exc:  # noqa: BLE001 - externe dienst; toon fout aan UI
        raise HTTPException(502, f"TheXEM niet bereikbaar of ongeldig antwoord: {exc}") from exc
    audit(ctx.db, actor=principal.actor, action="identity.thexem_refresh",
          target_type="series", target_id=f"tvdb:{tvdb_id}", new_value={"mappings": count})
    return {"tvdb_id": tvdb_id, "mappings": count}


@router.post("/anime-mappings/manual")
def manual_mapping(body: MappingBody, principal: Principal = Depends(require_operator),
                   ctx: AppContext = Depends(get_app_context)) -> dict[str, Any]:
    if None in (body.tvdb_id, body.absolute, body.season, body.episode):
        raise HTTPException(422, "tvdb_id, absolute, season en episode zijn verplicht")
    key = f"tvdb:{body.tvdb_id}"
    adapters.register_manual_mapping(ctx, key, int(body.absolute or 0), int(body.season or 0),
                                     int(body.episode or 0))
    audit(ctx.db, actor=principal.actor, action="identity.manual_mapping", target_type="series",
          target_id=key, new_value=body.model_dump())
    return {"ok": True}
