"""Audio-API: inventaris + beoordeling + claimcontrole van een probe/ffprobe-resultaat."""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Depends
from pydantic import BaseModel, Field

from mediaguard.audio import inventory
from mediaguard.audio.evaluate import AudioPolicy, evaluate_inventory, validate_claims
from mediaguard.audio.languages import normalize
from mediaguard.auth import Principal, require_viewer
from mediaguard.context import AppContext, get_app_context
from mediaguard.identity.parser import parse_release
from mediaguard.policy.profiles import build_chain, effective_policy

router = APIRouter(prefix="/api/v1/audio", tags=["audio"])


class InspectBody(BaseModel):
    probe: dict[str, Any] = Field(description="ffprobe-JSON, encoder-probe of arr mediaInfo")
    release_title: str | None = Field(default=None, max_length=1000)
    instance_id: str | None = None
    media_type: str | None = None
    series_id: int | None = None
    movie_id: int | None = None


@router.post("/inspect")
def inspect(body: InspectBody, _: Principal = Depends(require_viewer),
            ctx: AppContext = Depends(get_app_context)) -> dict[str, Any]:
    inv = inventory.build(body.probe)
    eff = effective_policy(ctx, build_chain(instance_id=body.instance_id,
                                            media_type=body.media_type,
                                            series_id=body.series_id, movie_id=body.movie_id))
    verdict = evaluate_inventory(inv, AudioPolicy.from_dict(eff["config"]["audio"]))
    claims = []
    if body.release_title:
        parsed = parse_release(body.release_title)
        claims = [c.to_dict() for c in validate_claims(
            dual_audio=parsed.dual_audio, claimed_audio=parsed.audio_language_claims, inv=inv)]
    return {"inventory": inv.to_dict(), "verdict": verdict.to_dict(), "claims": claims,
            "profile_id": eff["profile_id"]}


@router.get("/normalize/{code}")
def normalize_code(code: str, _: Principal = Depends(require_viewer)) -> dict[str, str]:
    return {"input": code, "code": normalize(code)}
