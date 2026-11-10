"""Dolby Vision & HDR API (Q2.3)."""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field

from mediaguard.audit import audit
from mediaguard.auth import Principal, require_operator, require_viewer
from mediaguard.context import AppContext, get_app_context
from mediaguard.db import dumps, loads, utcnow
from mediaguard.dv import policy as dv_policy
from mediaguard.dv import rules
from mediaguard.encoding import service as enc
from mediaguard.encoding.client import EncoderError, get_encoder_client
from mediaguard.recovery import service as recovery
from mediaguard.settings_registry import REGISTRY

router = APIRouter(prefix="/api/v1/dolby-vision", tags=["dolby-vision"])

RULES_TABLE = [
    {"profile": "8.1/8.4", "ui": "Via schakelaar AV1/SVT proef- of productie-encode toestaan",
     "limits": "Toolchain en RPU-output worden geverifieerd; playback kan per client "
               "verschillen"},
    {"profile": "7 MEL", "ui": "Via schakelaar en geverifieerde 7→8.1-route",
     "limits": "Bron blijft herstelbaar; timing, kleur, RPU en audio worden getest"},
    {"profile": "7 FEL", "ui": "Alleen experimenteel naar apart resultaat",
     "limits": "FEL-beeldinformatie kan verloren gaan; bron wordt nooit automatisch vervangen"},
    {"profile": "5", "ui": "Alleen met bewezen omzetroute, anders 'Niet ondersteund'",
     "limits": "Geen HDR10-terugval; forceren tast kleur ernstig aan"},
    {"profile": "HDR10+", "ui": "Eigen schakelaar, standaard overslaan",
     "limits": "Dynamische metadata wordt nooit stil verwijderd"},
]


def register_settings() -> None:
    REGISTRY.register(*dv_policy.setting_defs())


@router.get("/capabilities")
async def capabilities(_: Principal = Depends(require_viewer),
                       ctx: AppContext = Depends(get_app_context)) -> dict[str, Any]:
    try:
        caps = await get_encoder_client(ctx).capabilities()
    except EncoderError as exc:
        return {"available": False, "error": str(exc), "routes": {},
                "statuses": list(rules.UI_STATUSES)}
    routes = rules.technical_routes(caps) or set()
    return {"available": True, "raw": caps, "statuses": list(rules.UI_STATUSES),
            "routes": {p: (p in routes) for p in dv_policy.PROFILES},
            "hdr10plus_route_verified": bool(caps.get("hdr10plus_route_verified")),
            "rules": RULES_TABLE}


def _chain_from(library: str | None, profile: str | None, series: str | None,
                movie: str | None, file: str | None, job: str | None) -> dict[str, str]:
    return dv_policy.scope_chain(library=library, profile=profile, series=series, movie=movie,
                                 file=file, job=job)


@router.get("/policies")
def get_policies(library: str | None = None, profile: str | None = None,
                 series: str | None = None, movie: str | None = None, file: str | None = None,
                 job: str | None = None, _: Principal = Depends(require_viewer),
                 ctx: AppContext = Depends(get_app_context)) -> dict[str, Any]:
    chain = _chain_from(library, profile, series, movie, file, job)
    eff = dv_policy.effective(ctx, chain)
    overrides = ctx.db.fetch_dicts(
        "SELECT scope_type, scope_id, key, value_json, updated_at, updated_by FROM "
        "settings_values WHERE key LIKE 'dv.%' ORDER BY scope_type, scope_id, key")
    for o in overrides:
        o["value"] = loads(o.pop("value_json"))
        o["label_nl"] = dv_policy.LEVEL_LABELS_NL.get(o["scope_type"], o["scope_type"])
    return {
        "chain": chain, "effective": eff.to_dict(),
        "levels": dv_policy.levels_view(ctx, chain),
        "inheritance_nl": "Globaal → Bibliotheek → Profiel → Titel → Job",
        "modes": [{"value": m, "label_nl": dv_policy.MODE_LABELS_NL[m]}
                  for m in dv_policy.MODES],
        "profiles": [{"value": p, "label_nl": dv_policy.PROFILE_LABELS_NL[p]}
                     for p in dv_policy.PROFILES],
        "overrides": overrides, "rules": RULES_TABLE,
        "integrity_note_nl": "Metadata-, stream- en herstelverificatie zijn altijd actief en "
                             "niet uit te schakelen.",
    }


class PolicyPatch(BaseModel):
    scope_type: str = "global"
    scope_id: str = ""
    allow: bool | None = None
    mode: str | None = None
    allowed_profiles: list[str] | None = None
    hdr10plus_mode: str | None = None
    reset: list[str] = Field(default_factory=list)
    reason: str = Field(default="", max_length=500)


@router.patch("/policies")
def patch_policies(body: PolicyPatch, principal: Principal = Depends(require_operator),
                   ctx: AppContext = Depends(get_app_context)) -> dict[str, Any]:
    values = {k: v for k, v in body.model_dump(
        include={"allow", "mode", "allowed_profiles", "hdr10plus_mode"}).items()
        if v is not None}
    if not values and not body.reset:
        raise HTTPException(422, "Geen wijziging opgegeven")
    try:
        change = dv_policy.apply_change(ctx, body.scope_type, body.scope_id, values,
                                        body.reset, principal.actor, body.reason or None)
    except (ValueError, KeyError) as exc:
        raise HTTPException(422, str(exc)) from exc
    audit(ctx.db, actor=principal.actor, action="dv.policy_change", target_type="dv_policy",
          target_id=f"{body.scope_type}:{body.scope_id}", reason=body.reason or None,
          old_value=change["old"], new_value=change["new"],
          undo_hint="Zet de vorige waarde terug of gebruik 'Herstel standaard' (reset)")
    ctx.events.publish("dv.policy_changed", {"scope_type": body.scope_type,
                                             "scope_id": body.scope_id, "new": change["new"]})
    return {"scope_type": body.scope_type, "scope_id": body.scope_id, **change}


class TestEncodeBody(BaseModel):
    path: str = Field(min_length=1, max_length=4096)
    instance_id: str | None = None
    media_kind: str | None = None
    provider_id: int | str | None = None
    series_id: int | None = None
    movie_id: int | None = None
    use_cpu: bool = True


def _test_view(ctx: AppContext, test_id: int) -> dict[str, Any]:
    row = ctx.db.fetch_one("SELECT * FROM dv_tests WHERE id=?", (test_id,))
    if row is None:
        raise HTTPException(404, "Test niet gevonden")
    d = dict(row)
    d["decision"] = loads(d.pop("decision_json"), {})
    d["verification"] = loads(d.pop("verification_json"), None)
    d["scope"] = loads(d.pop("scope_json"), {})
    d["evidence"] = ctx.db.fetch_dicts(
        "SELECT * FROM dv_conversion_evidence WHERE dv_test_id=? ORDER BY id DESC", (test_id,))
    for e in d["evidence"]:
        for col in ("metadata_before_json", "metadata_after_json", "checks_json"):
            e[col[:-5]] = loads(e.pop(col), None)
    return d


@router.post("/test-encode")
async def test_encode(body: TestEncodeBody, principal: Principal = Depends(require_operator),
                      ctx: AppContext = Depends(get_app_context)) -> dict[str, Any]:
    """'Test nu': proefconversie op kopie; het origineel wordt nooit aangeraakt."""
    now = utcnow()
    test_id = ctx.db.execute_insert(
        "INSERT INTO dv_tests(instance_id,kind,provider_id,source_path,scope_json,status,state,"
        "use_cpu,created_at,created_by,updated_at) VALUES (?,?,?,?,?,?,?,?,?,?,?)",
        (body.instance_id, body.media_kind, None if body.provider_id is None
         else str(body.provider_id), body.path, dumps(body.model_dump()), rules.TRIAL,
         "planned", int(body.use_cpu), now, principal.actor, now))
    try:
        job = await enc.submit(ctx, path=body.path, instance_id=body.instance_id,
                               media_kind=body.media_kind, provider_id=body.provider_id,
                               series_id=body.series_id, movie_id=body.movie_id,
                               manual_activation=True, force_cpu=body.use_cpu,
                               actor=principal.actor, dv_test_id=test_id)
    except enc.GateError as exc:
        ctx.db.execute("UPDATE dv_tests SET state='failed', status=?, error=?, updated_at=? "
                       "WHERE id=?", (rules.KEEP, exc.message, utcnow(), test_id))
        raise HTTPException(503 if exc.code == "ENCODER_UNAVAILABLE" else 409,
                            {"code": exc.code, "message": exc.message,
                             "test_id": test_id}) from exc
    decision = job.get("dv_decision") or {}
    if job["status"] == "skipped":
        state, status = ("unsupported" if decision.get("status") == rules.UNSUPPORTED
                         else "kept"), decision.get("status") or rules.KEEP
    else:
        state, status = "running", rules.TRIAL
    ctx.db.execute("UPDATE dv_tests SET encoding_job_id=?, decision_json=?, state=?, status=?, "
                   "updated_at=? WHERE id=?",
                   (job["id"], dumps(decision), state, status, utcnow(), test_id))
    audit(ctx.db, actor=principal.actor, action="dv.test_encode", target_type="file",
          target_id=body.provider_id or body.path, instance_id=body.instance_id,
          new_value={"test_id": test_id, "status": status, "encoding_job_id": job["id"]})
    return _test_view(ctx, test_id)


@router.get("/tests/{test_id}")
async def get_test(test_id: int, _: Principal = Depends(require_viewer),
                   ctx: AppContext = Depends(get_app_context)) -> dict[str, Any]:
    test = _test_view(ctx, test_id)
    if test["encoding_job_id"] and test["state"] == "running":
        job = await enc.sync_job(ctx, int(test["encoding_job_id"]))
        state, status, err = test["state"], test["status"], None
        if job["status"] == "awaiting_activation":
            state, status = "verified", rules.CHECKED
        elif job["status"] == "rejected":
            state, status, err = "failed", rules.VERIFY_FAILED, job.get("error")
        elif job["status"] in ("failed", "cancelled", "skipped"):
            state, status, err = "failed", rules.KEEP, job.get("error") or job.get(
                "skip_reason")
        ctx.db.execute("UPDATE dv_tests SET state=?, status=?, verification_json=?, error=?, "
                       "updated_at=? WHERE id=?",
                       (state, status, dumps(job.get("core_verification")), err, utcnow(),
                        test_id))
        test = _test_view(ctx, test_id)
    return test


class ConfirmationBody(BaseModel):
    client: str = Field(min_length=1, max_length=200)
    server: str = Field(min_length=1, max_length=200)
    container: str = Field(default="mkv", max_length=20)
    codec: str = Field(default="av1", max_length=20)
    dv_profile: str = Field(min_length=1, max_length=20)
    note: str = Field(default="", max_length=1000)


@router.get("/playback-confirmations")
def list_confirmations(_: Principal = Depends(require_viewer),
                       ctx: AppContext = Depends(get_app_context)) -> list[dict[str, Any]]:
    return enc.confirmations(ctx)


@router.post("/playback-confirmations")
def add_confirmation(body: ConfirmationBody, principal: Principal = Depends(require_operator),
                     ctx: AppContext = Depends(get_app_context)) -> dict[str, Any]:
    """'Deze afspeelketen werkt bij mij': persoonlijke waarneming. Vermindert waarschuwingen,
    schakelt nooit technische controles uit."""
    ctx.db.execute(
        "INSERT INTO dv_playback_confirmations(client,server,container,codec,dv_profile,note,"
        "confirmed_by,confirmed_at) VALUES (?,?,?,?,?,?,?,?) ON CONFLICT(client,server,"
        "container,codec,dv_profile) DO UPDATE SET note=excluded.note, "
        "confirmed_by=excluded.confirmed_by, confirmed_at=excluded.confirmed_at",
        (body.client, body.server, body.container.lower(), body.codec.lower(), body.dv_profile,
         body.note, principal.actor, utcnow()))
    audit(ctx.db, actor=principal.actor, action="dv.playback_confirmed",
          target_type="playback_chain", target_id=f"{body.client}/{body.server}",
          new_value=body.model_dump())
    return {"ok": True, "note_nl": "Waarschuwingen voor deze keten worden verminderd; "
                                   "metadata-verificatie blijft verplicht."}


@router.delete("/playback-confirmations/{conf_id}")
def delete_confirmation(conf_id: int, principal: Principal = Depends(require_operator),
                        ctx: AppContext = Depends(get_app_context)) -> dict[str, Any]:
    if ctx.db.execute("DELETE FROM dv_playback_confirmations WHERE id=?", (conf_id,)) == 0:
        raise HTTPException(404, "Bevestiging niet gevonden")
    audit(ctx.db, actor=principal.actor, action="dv.playback_unconfirmed",
          target_type="playback_chain", target_id=conf_id)
    return {"deleted": conf_id}


class ActivateBody(BaseModel):
    confirm: bool = False
    hardlink_break_confirmed: bool = False


@router.post("/activate/{job_id}")
async def activate(job_id: int, body: ActivateBody,
                   principal: Principal = Depends(require_operator),
                   ctx: AppContext = Depends(get_app_context)) -> dict[str, Any]:
    """'Activeer na verificatie': geverifieerd DV-resultaat via recovery activeren."""
    if not body.confirm:
        raise HTTPException(422, "Bevestiging vereist ('confirm': true)")
    try:
        job = await enc.handoff(ctx, job_id, actor=principal.actor,
                                hardlink_break_confirmed=body.hardlink_break_confirmed)
    except LookupError as exc:
        raise HTTPException(404, str(exc)) from exc
    except enc.GateError as exc:
        raise HTTPException(409, {"code": exc.code, "message": exc.message}) from exc
    ctx.db.execute("UPDATE dv_conversion_evidence SET approved_by=?, approved_at=? WHERE "
                   "encoding_job_id=? AND approved_by IS NULL",
                   (principal.actor, utcnow(), job_id))
    audit(ctx.db, actor=principal.actor, action="dv.activate", target_type="encoding_job",
          target_id=job_id, instance_id=job.get("instance_id"),
          new_value={"transaction_id": job.get("transaction_id")},
          undo_hint="Herstel origineel via /dolby-vision/restore")
    return {"job_id": job_id, "transaction_id": job.get("transaction_id"),
            "status": job["status"], "ui_status": rules.RESTORABLE}


class RestoreBody(BaseModel):
    confirm: bool = False
    reason: str = Field(default="", max_length=500)


@router.post("/restore/{job_id}")
def restore(job_id: int, body: RestoreBody, principal: Principal = Depends(require_operator),
            ctx: AppContext = Depends(get_app_context)) -> dict[str, Any]:
    """'Herstel origineel' voor een geactiveerde DV-conversie."""
    if not body.confirm:
        raise HTTPException(422, "Bevestiging vereist ('confirm': true)")
    job = enc.get_job(ctx, job_id)
    if job is None or not job.get("transaction_id"):
        raise HTTPException(404, "Geen geactiveerde conversie voor deze job")
    item = ctx.db.fetch_one(
        "SELECT q.id FROM quarantine_items q WHERE q.transaction_id=? AND "
        "q.status='restorable'", (job["transaction_id"],))
    if item is None:
        raise HTTPException(409, "Geen herstelbaar origineel (transactie niet afgerond of "
                                 "al hersteld)")
    try:
        txn_id, recovery_job = recovery.create_restore(ctx, int(item["id"]), principal.actor)
    except (LookupError, ValueError, FileNotFoundError) as exc:
        raise HTTPException(409, str(exc)) from exc
    audit(ctx.db, actor=principal.actor, action="dv.restore", target_type="encoding_job",
          target_id=job_id, instance_id=job.get("instance_id"), reason=body.reason or None,
          new_value={"transaction_id": txn_id})
    return {"job_id": job_id, "restore_transaction_id": txn_id, "recovery_job_id": recovery_job}
