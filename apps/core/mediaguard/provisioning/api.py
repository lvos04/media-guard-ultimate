"""API 'Inrichten' (zero-config provisioning): status, plan (dry-run), toepassen, centrale
configuratie, verbindingstests, Seerr, drift-controle, optimaliseren na migratie en
bestanden hernoemen. Prefix ``/api/v1/provisioning``. Geheimen worden nooit teruggegeven."""

from __future__ import annotations

import asyncio
import contextlib
import copy
import logging
from typing import Any, Literal

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field, ValidationError

from mediaguard import taskwatch
from mediaguard.arr.client import ArrError
from mediaguard.audit import audit
from mediaguard.auth import Principal, require_admin, require_operator, require_viewer
from mediaguard.context import AppContext, get_app_context
from mediaguard.db import loads
from mediaguard.instances.registry import arr_client, instance_row, validate_url
from mediaguard.provisioning import engine, optimize, store
from mediaguard.provisioning import templates as T
from mediaguard.provisioning.prowlarr import ProwlarrClient, ProwlarrError, prowlarr_client
from mediaguard.provisioning.seerr_sync import apply_seerr
from mediaguard.seerr.client import SECRET_NAME as SEERR_SECRET
from mediaguard.seerr.client import SeerrClient, SeerrError
from mediaguard.settings_registry import REGISTRY, SettingDef

LOGGER = logging.getLogger(__name__)
router = APIRouter(prefix="/api/v1/provisioning", tags=["provisioning"])
CAT = "Inrichten"
SYSTEM_ACTOR = "system:provisioning"

MODE_NL = {"manage": "Volledig door MediaGuard ingericht",
           "hold": "Gemigreerd: wacht op 'Optimaliseren na migratie' (alleen essentials)",
           "optimized": "Gemigreerd en geoptimaliseerd door MediaGuard"}


def register_settings() -> None:
    REGISTRY.register(
        SettingDef("provisioning.on_startup", "bool", True, CAT,
                   "Essentials bij opstarten inrichten", "Ensure essentials on startup",
                   help_nl="Bij elke start van MediaGuard: webhook, mediapaden en tags op alle "
                           "instanties controleren en zo nodig aanmaken.",
                   effect_nl="Een nieuwe installatie werkt zonder handmatige stap."),
        SettingDef("provisioning.drift_check_enabled", "bool", True, CAT,
                   "Afwijkingen periodiek controleren", "Periodic drift check",
                   help_nl="Controleert of de instanties nog overeenkomen met de "
                           "MediaGuard-inrichting."),
        SettingDef("provisioning.drift_interval_hours", "int", 6, CAT,
                   "Interval afwijkingscontrole", "Drift check interval", minimum=1,
                   maximum=168, unit="uur"),
        SettingDef("provisioning.auto_repair", "bool", True, CAT,
                   "Afwijkingen automatisch herstellen", "Auto-repair drift",
                   help_nl="Herstelt alleen onderdelen die van MediaGuard zijn (naam "
                           "'MediaGuard · …') en essentials. Verwijdert nooit iets en raakt "
                           "nooit door jou beheerde items of Seerr aan.",
                   effect_nl="Wijzigingen die je in Sonarr/Radarr zelf doet aan "
                             "MediaGuard-onderdelen worden teruggezet."),
        SettingDef("provisioning.startup_delay_seconds", "int", 30, CAT,
                   "Wachttijd na opstarten", "Startup delay", minimum=0, maximum=600,
                   unit="s", advanced=True),
    )


# -- achtergrond ---------------------------------------------------------------------------
def _halted(ctx: AppContext) -> bool:
    """Noodstop: de achtergrond houdt alleen de essentials bij (tags, mediapaden,
    MediaGuard-webhook); profielen, formats en Seerr wachten tot na de noodstop."""
    from mediaguard import emergency

    return emergency.skip(ctx, "inrichting herstellen (profielen, formats, Seerr)")


async def ensure_essentials(ctx: AppContext) -> dict[str, Any]:
    sections = store.ESSENTIAL_SECTIONS if _halted(ctx) else store.AUTO_SECTIONS
    result = await engine.apply(ctx, actor=SYSTEM_ACTOR, sections=list(sections),
                                auto_only=True, include_deletes=False, kind="startup")
    await _ensure_sab_categories(ctx)
    return result


async def _ensure_sab_categories(ctx: AppContext) -> None:
    """SABnzbd kent categorieën niet vanzelf (qBittorrent wel): ontbrekende aanmaken."""
    from mediaguard.downloads import sab_categories

    try:
        await sab_categories.ensure(ctx)
    except Exception:  # noqa: BLE001 - mag de inrichting nooit stoppen
        LOGGER.exception("SABnzbd-categorieën controleren mislukt")


async def drift_check(ctx: AppContext, *, repair: bool | None = None) -> dict[str, Any]:
    repair = bool(ctx.config.value("provisioning.auto_repair")) if repair is None else repair
    # Seerr telt pas mee als de gebruiker Seerr ooit via MediaGuard heeft ingericht
    sections = [s for s in store.SECTIONS
                if s != "seerr" or ctx.state_get("provisioning:seerr_applied", False)]
    plan = await engine.build_plan(ctx, actor=SYSTEM_ACTOR, sections=sections)
    engine.persist_status(ctx, plan)
    found = [c.view() for c in plan.pending()]
    result: dict[str, Any] = {"drift": len(found), "repaired": None}
    if repair and found:
        inits = [iid for iid in plan.instances if store.initialized(ctx, iid)]
        others = [iid for iid in plan.instances if iid not in inits]
        repaired: list[dict[str, Any]] = []
        stopped = _halted(ctx)
        if stopped:
            from mediaguard import emergency

            result["paused_nl"] = emergency.message_nl("profielen, formats en Seerr herstellen")
        if inits:
            res = await engine.apply(ctx, actor=SYSTEM_ACTOR, instances=inits, auto_only=True,
                                     sections=list(store.ESSENTIAL_SECTIONS) if stopped
                                     else None, include_deletes=False, kind="drift")
            repaired += res["applied"]
        if others:
            res = await engine.apply(ctx, actor=SYSTEM_ACTOR, instances=others,
                                     sections=list(store.ESSENTIAL_SECTIONS if stopped
                                                   else store.AUTO_SECTIONS), auto_only=True,
                                     include_deletes=False, kind="drift")
            repaired += res["applied"]
        if not stopped and "seerr" in sections and any(
                c.section == "seerr" and c.action == "update" for c in plan.pending()):
            # MediaGuard's eigen Seerr-servers zelf rechtzetten (was: elke controle opnieuw
            # 'Seerr-server … bijwerken' als melding, rapport 4 S10)
            try:
                res_s = await apply_seerr(ctx, SYSTEM_ACTOR, confirm=True)
                repaired += res_s.get("applied") or []
            except Exception:  # noqa: BLE001 - drift-controle loopt door
                LOGGER.exception("Seerr-afwijking herstellen mislukt")
        result["repaired"] = len(repaired)
        plan = await engine.build_plan(ctx, actor=SYSTEM_ACTOR, sections=sections)
        engine.persist_status(ctx, plan)
    remaining = [c.view() for c in plan.pending()]
    run_id = store.run_start(ctx, "drift", SYSTEM_ACTOR, list(store.SECTIONS), [])
    store.run_finish(ctx, run_id, "succeeded" if not remaining else "partial",
                     {"found": len(found), "repaired": result["repaired"],
                      "remaining": len(remaining)}, remaining)
    result.update(run_id=run_id, remaining=len(remaining))
    await _ensure_sab_categories(ctx)
    from mediaguard.api.system import notify, resolve_notifications

    if remaining:
        which = "; ".join(f"{c.get('instance_id') or 'algemeen'}: {c['title_nl']}"
                          for c in remaining[:4])
        notify(ctx, "warning", "Inrichting wijkt af",
               f"{len(remaining)} afwijking(en) van de MediaGuard-inrichting: {which}"
               + (" …" if len(remaining) > 4 else ""), link="/inrichten",
               dedupe_key=DRIFT_NOTIFICATION)
    else:
        # afwijking verdwenen → melding weg (was: bleef staan terwijl alles groen was)
        resolve_notifications(ctx, DRIFT_NOTIFICATION)
    return result


DRIFT_NOTIFICATION = "provisioning.drift"


async def _loop(ctx: AppContext) -> None:
    await asyncio.sleep(int(ctx.config.value("provisioning.startup_delay_seconds")))
    if ctx.config.value("provisioning.on_startup"):
        try:
            await ensure_essentials(ctx)
        except Exception:  # noqa: BLE001 - achtergrondtaak mag nooit stoppen
            LOGGER.exception("provisioning: essentials bij opstarten mislukt")
    while True:
        try:
            if ctx.config.value("provisioning.drift_check_enabled"):
                await drift_check(ctx)
            taskwatch.ok(ctx, "provisioning")
        except Exception as exc:  # noqa: BLE001
            LOGGER.exception("provisioning: drift-controle mislukt")
            taskwatch.fail(ctx, "provisioning", exc)
        try:
            wait = _drift_seconds(ctx)
        except Exception:  # noqa: BLE001 - instelling onleesbaar: elke 6 uur
            wait = 6 * 3600.0
        await asyncio.sleep(wait)


def _drift_seconds(ctx: AppContext) -> float:
    return 3600 * max(1, int(ctx.config.value("provisioning.drift_interval_hours")))


async def startup(ctx: AppContext) -> None:
    if ctx.settings.start_background and ctx.settings.role == "core":
        ctx.services["provisioning.task"] = taskwatch.spawn(
            ctx, "provisioning", _loop, label_nl="Inrichting controleren", every=_drift_seconds)


async def shutdown(ctx: AppContext) -> None:
    task = ctx.services.pop("provisioning.task", None)
    if task is not None:
        task.cancel()
        with contextlib.suppress(asyncio.CancelledError, Exception):
            await task


# -- hulp ------------------------------------------------------------------------------------
FIELD_NL = {"download_clients": "Downloadclient", "indexers": "Indexer", "host": "adres",
            "port": "poort", "url_base": "URL-basis", "name": "naam", "api_key": "API-sleutel",
            "username": "gebruikersnaam", "password": "wachtwoord", "url": "URL",
            "priority": "prioriteit", "categories": "categorie"}


def _nl_error(err: dict[str, Any]) -> str:
    """Pydantic-fout → leesbare Nederlandse melding ('Downloadclient 1 · adres: …')."""
    parts: list[str] = []
    for p in err.get("loc") or ():
        if isinstance(p, int):
            if parts:
                parts[-1] = f"{parts[-1]} {p + 1}"
            continue
        parts.append(FIELD_NL.get(str(p), str(p)))
    msg = str(err.get("msg") or "ongeldig").removeprefix("Value error, ")
    msg = {"Field required": "verplicht",
           "String should have at least 1 character": "verplicht"}.get(msg, msg)
    return (" · ".join(parts) + ": " if parts else "") + msg


def _bad(exc: Exception) -> HTTPException:
    if isinstance(exc, ArrError):
        return HTTPException(exc.http_status, exc.to_dict())
    if isinstance(exc, ProwlarrError | SeerrError):
        return HTTPException(502, exc.to_dict())
    if isinstance(exc, ValidationError):
        return HTTPException(422, "; ".join(_nl_error(e) for e in exc.errors(
            include_url=False, include_input=False, include_context=False)))
    return HTTPException(422, str(exc))


def _check_sections(sections: list[str] | None) -> list[str] | None:
    if sections is None:
        return None
    bad = [s for s in sections if s not in store.SECTIONS]
    if bad:
        raise HTTPException(422, f"Onbekende sectie(s): {', '.join(bad)}")
    return sections


# -- status / plan / apply -------------------------------------------------------------------
@router.get("/status")
async def status(refresh: bool = False, _: Principal = Depends(require_viewer),
                 ctx: AppContext = Depends(get_app_context)) -> dict[str, Any]:
    if refresh:
        try:
            engine.persist_status(ctx, await engine.build_plan(ctx, actor="user"))
        except (ArrError, ValueError) as exc:
            raise _bad(exc) from exc
    cfg = store.load(ctx)
    rows = store.status_all(ctx)
    instances = []
    for inst in engine.eligible(ctx, None):
        iid = inst["id"]
        mode = store.resolve_mode(ctx, iid, cfg)
        opt = store.optimize_row(ctx, iid)
        secs = {r["section"]: {"status": r["status"], "message_nl": r["message_nl"],
                               "changes": r["changes"], "checked_at": r["checked_at"]}
                for r in rows if r["instance_id"] == iid}
        for s in store.INSTANCE_SECTIONS:
            secs.setdefault(s, {"status": "unknown", "message_nl": "Nog niet gecontroleerd.",
                                "changes": 0, "checked_at": None})
        instances.append({
            "instance_id": iid, "name": inst["name"], "app": inst["app"],
            "media_type": inst["media_type"], "mode": mode, "mode_nl": MODE_NL[mode],
            "shadow_mode": bool(inst["shadow_mode"]), "suspended": bool(inst["suspended"]),
            "initialized": store.initialized(ctx, iid),
            "shadow_import_done": store.shadow_import_done(ctx, iid),
            "optimize": None if opt is None else {
                k: opt[k] for k in ("phase", "snapshot_id", "optimized_at", "cleaned_at",
                                    "rolled_back_at", "error")},
            "root_folder": store.root_folder_for(ctx, iid, inst["media_type"], cfg),
            "category": store.category_for(ctx, iid, inst["media_type"], cfg),
            "sections": secs})
    glob = {r["section"]: {"status": r["status"], "message_nl": r["message_nl"],
                           "changes": r["changes"], "checked_at": r["checked_at"]}
            for r in rows if r["instance_id"] == ""}
    needs = store.needs(ctx, cfg)
    last: dict[str, Any] = {k: next(iter(store.runs(ctx, 1, k)), None)
                            for k in ("apply", "drift", "startup", "optimize")}
    return {"instances": instances, "global": glob, "needs": needs,
            "ready": not any(n["severity"] == "required" for n in needs),
            "indexer_mode": cfg.indexer_mode,
            "active_shadow_phase": store.active_shadow_phase(ctx),
            # rapport 3 / R7: wat Sonarr/Radarr weigert, met de echte fout (na 3× niet meer
            # elke ronde opnieuw, wel na een dag of na een configwijziging)
            "failing": engine.failing_items(ctx),
            "settings": {k.split(".", 1)[1]: ctx.config.value(k) for k in (
                "provisioning.on_startup", "provisioning.drift_check_enabled",
                "provisioning.drift_interval_hours", "provisioning.auto_repair")},
            "last_runs": last}


class PlanBody(BaseModel):
    sections: list[str] | None = None
    instances: list[str] | None = None


@router.post("/plan")
async def plan(body: PlanBody | None = None, principal: Principal = Depends(require_operator),
               ctx: AppContext = Depends(get_app_context)) -> dict[str, Any]:
    body = body or PlanBody()
    try:
        p = await engine.build_plan(ctx, instances=body.instances,
                                    sections=_check_sections(body.sections),
                                    actor=principal.actor)
    except (ArrError, ValueError) as exc:
        raise _bad(exc) from exc
    engine.persist_status(ctx, p)
    run_id = store.run_start(ctx, "plan", principal.actor, body.sections or [],
                             body.instances or [])
    store.run_finish(ctx, run_id, "succeeded", p.view()["summary"],
                     [c.view() for c in p.pending()])
    return {"run_id": run_id, "dry_run": True, **p.view()}


class ApplyBody(PlanBody):
    confirm: bool = False
    include_deletes: bool = True


@router.post("/apply")
async def apply(body: ApplyBody, principal: Principal = Depends(require_admin),
                ctx: AppContext = Depends(get_app_context)) -> dict[str, Any]:
    sections = _check_sections(body.sections)
    if not body.confirm:
        p = await engine.build_plan(ctx, instances=body.instances, sections=sections,
                                    actor=principal.actor)
        return {"requires_confirmation": True, "dry_run": True, **p.view()}
    if ctx.emergency_stopped():
        raise HTTPException(409, "Noodstop actief: inrichten geblokkeerd")
    try:
        result = await engine.apply(ctx, actor=principal.actor, instances=body.instances,
                                    sections=sections, include_deletes=body.include_deletes)
    except (ArrError, ValueError) as exc:
        raise _bad(exc) from exc
    if sections and "seerr" in sections:
        result["seerr"] = await apply_seerr(ctx, principal.actor, confirm=True)
    return result


@router.post("/drift-check")
async def drift_now(repair: bool = False, _: Principal = Depends(require_operator),
                    ctx: AppContext = Depends(get_app_context)) -> dict[str, Any]:
    return await drift_check(ctx, repair=repair)


@router.get("/runs")
def runs(limit: int = 20, kind: str | None = None, _: Principal = Depends(require_viewer),
         ctx: AppContext = Depends(get_app_context)) -> list[dict[str, Any]]:
    return store.runs(ctx, min(max(limit, 1), 200), kind)


# -- configuratie -------------------------------------------------------------------------------
@router.get("/config")
def get_config(_: Principal = Depends(require_viewer),
               ctx: AppContext = Depends(get_app_context)) -> dict[str, Any]:
    out = store.public_view(ctx)
    out["templates"] = {k: {"name": T.owned_name(v["name"]), "label_nl": v["label_nl"],
                            "allowed": v["allowed"], "cutoff": v["cutoff"],
                            "scores": v["scores"]} for k, v in T.QUALITY_TEMPLATES.items()}
    out["sections"] = [{"id": s, "label_nl": store.SECTION_NL[s],
                        "full_manage_possible": s in store.FULL_MANAGE_SECTIONS}
                       for s in store.SECTIONS]
    return out


class SeerrConnection(BaseModel):
    url: str = Field(max_length=500)
    api_key: str | None = Field(default=None, max_length=200)


@router.put("/config")
async def put_config(body: dict[str, Any], principal: Principal = Depends(require_admin),
                     ctx: AppContext = Depends(get_app_context)) -> dict[str, Any]:
    allowed = set(store.ProvisioningConfig.model_fields) | {"seerr_connection"}
    unknown = set(body) - allowed
    if unknown:
        raise HTTPException(422, f"Onbekende sleutel(s): {', '.join(sorted(unknown))}")
    if body.get("prowlarr", {}).get("url"):
        try:
            validate_url(body["prowlarr"]["url"])
        except ValueError as exc:
            raise _bad(exc) from exc
    for iid in (body.get("instances") or {}):
        if instance_row(ctx, iid) is None:
            raise HTTPException(422, f"Onbekende instantie: {iid}")
    if body.get("seerr_connection") is not None:
        sc = SeerrConnection.model_validate(body["seerr_connection"])
        try:
            validate_url(sc.url)
            key = sc.api_key if sc.api_key not in (None, "", store.MASK) else \
                ctx.secrets.get(SEERR_SECRET)
            if key:
                await SeerrClient(sc.url, key,
                                  transport=ctx.services.get("seerr_transport")).status()
        except (SeerrError, ValueError) as exc:
            raise _bad(exc) from exc
        ctx.config.set("global", "", "seerr.url", sc.url.rstrip("/"), principal.actor)
        if sc.api_key not in (None, "", store.MASK):
            ctx.secrets.set(SEERR_SECRET, sc.api_key, principal.actor)  # type: ignore[arg-type]
    before = store.public_view(ctx)
    try:
        cfg = store.save(ctx, body, principal.actor)
    except (ValidationError, ValueError) as exc:
        raise _bad(exc) from exc
    after = store.public_view(ctx, cfg)
    audit(ctx.db, actor=principal.actor, action="provisioning.config", target_type="config",
          target_id="provisioning", old_value={k: before.get(k) for k in body},
          new_value={k: after.get(k) for k in body})
    return after


# -- items en eigenaarschap ----------------------------------------------------------------------
@router.get("/items")
def items(instance_id: str | None = None, _: Principal = Depends(require_viewer),
          ctx: AppContext = Depends(get_app_context)) -> list[dict[str, Any]]:
    sql = "SELECT instance_id, section, item_key, remote_id, ownership, applied_at, " \
          "updated_at, updated_by FROM provisioning_items"
    if instance_id is not None:
        return ctx.db.fetch_dicts(sql + " WHERE instance_id=? ORDER BY section, item_key",
                                  (instance_id,))
    return ctx.db.fetch_dicts(sql + " ORDER BY instance_id, section, item_key")


class OwnershipBody(BaseModel):
    instance_id: str
    section: Literal["downloadclients", "indexers", "customformats", "qualityprofiles"]
    key: str = Field(min_length=1, max_length=200)
    ownership: Literal["user", "mediaguard", "migrated"]


@router.put("/items/ownership")
def set_ownership(body: OwnershipBody, principal: Principal = Depends(require_admin),
                  ctx: AppContext = Depends(get_app_context)) -> dict[str, Any]:
    if instance_row(ctx, body.instance_id) is None:
        raise HTTPException(404, "Onbekende instantie")
    store.item_set(ctx, body.instance_id, body.section, body.key, ownership=body.ownership,
                   actor=principal.actor, applied=False)
    audit(ctx.db, actor=principal.actor, action="provisioning.ownership",
          target_type=body.section, target_id=body.key, instance_id=body.instance_id,
          new_value={"ownership": body.ownership})
    return {"ok": True, **body.model_dump()}


# -- verbindingstests -----------------------------------------------------------------------------
class DcTestBody(BaseModel):
    name: str | None = None
    download_client: dict[str, Any] | None = None
    instance_id: str | None = None


async def _target_for(ctx: AppContext, instance_id: str | None, app: str | None = None,
                      pred: Any = None) -> engine.Target:
    cfg = store.load(ctx)
    rows = engine.eligible(ctx, [instance_id] if instance_id else None)
    rows = [r for r in rows if (app is None or r["app"] == app) and (pred is None or pred(r))]
    if not rows:
        raise HTTPException(409, "Geen instantie beschikbaar om mee te testen")
    inst = rows[0]
    return engine.Target(ctx, cfg, inst, arr_client(ctx, inst["id"]),
                         store.resolve_mode(ctx, inst["id"], cfg))


def _temp_secret(ctx: AppContext, name: str, value: str | None) -> Any:
    @contextlib.contextmanager
    def cm():  # type: ignore[no-untyped-def]
        if value in (None, "", store.MASK):
            yield
            return
        # de versleutelde waarde zelf bewaren en terugzetten: een (met een andere
        # secret.key) onleesbaar geheim mag een test niet stil laten verdwijnen
        old = ctx.secrets.raw(name)
        ctx.secrets.set(name, value, "test")  # type: ignore[arg-type]
        try:
            yield
        finally:
            ctx.secrets.restore_raw(name, old, "test")
    return cm()


def _custom_target_needs_admin(principal: Principal) -> None:
    """Een test met een zelf opgegeven adres kan de opgeslagen sleutel naar dat adres sturen:
    alleen een beheerder (de app-sleutel en operators testen wat er is opgeslagen)."""
    if principal.role != "admin":
        raise HTTPException(403, "Alleen een beheerder kan een ander adres testen; test de "
                                 "opgeslagen instelling, of vraag een beheerder")


@router.post("/downloadclients/test")
async def test_dc(body: DcTestBody, principal: Principal = Depends(require_operator),
                  ctx: AppContext = Depends(get_app_context)) -> dict[str, Any]:
    cfg = store.load(ctx)
    if body.download_client is not None:
        _custom_target_needs_admin(principal)
        try:
            dc = store.DownloadClientCfg.model_validate(body.download_client)
        except ValidationError as exc:
            raise _bad(exc) from exc
    else:
        found = next((d for d in cfg.download_clients if d.name == body.name), None)
        if found is None:
            raise HTTPException(404, f"Downloadclient '{body.name}' niet gevonden")
        dc = found
    t = await _target_for(ctx, body.instance_id, pred=lambda r: engine.dc_targets(dc, r["id"]))
    try:
        with _temp_secret(ctx, store.dc_secret(dc.name, "api_key"), dc.api_key), \
                _temp_secret(ctx, store.dc_secret(dc.name, "password"), dc.password):
            schema = await t.client.provider_schema("downloadclient")
            template = next((s for s in schema if s.get("implementation") == dc.implementation),
                            None)
            if template is None:
                raise HTTPException(422, f"{dc.implementation} niet ondersteund")
            req = await engine.dc_body(t, dc, template)
            req["enable"] = True
            await t.client.provider_test("downloadclient", req)
    except ArrError as exc:
        return {"ok": False, "instance_id": t.iid, "message_nl": exc.message,
                "details": exc.details}
    return {"ok": True, "instance_id": t.iid,
            "message_nl": f"{t.inst['name']} kan '{dc.name}' bereiken."}


class IdxTestBody(BaseModel):
    name: str | None = None
    indexer: dict[str, Any] | None = None
    instance_id: str | None = None


@router.post("/indexers/test")
async def test_indexer(body: IdxTestBody, principal: Principal = Depends(require_operator),
                       ctx: AppContext = Depends(get_app_context)) -> dict[str, Any]:
    cfg = store.load(ctx)
    if body.indexer is not None:
        _custom_target_needs_admin(principal)
        try:
            idx = store.IndexerCfg.model_validate(body.indexer)
        except ValidationError as exc:
            raise _bad(exc) from exc
    else:
        found_idx = next((i for i in cfg.indexers if i.name == body.name), None)
        if found_idx is None:
            raise HTTPException(404, f"Indexer '{body.name}' niet gevonden")
        idx = found_idx
    t = await _target_for(ctx, body.instance_id, pred=lambda r: engine.indexer_targets(idx, r))
    try:
        with _temp_secret(ctx, store.indexer_secret(idx.name), idx.api_key):
            schema = await t.client.provider_schema("indexer")
            template = next((s for s in schema if s.get("implementation") ==
                             idx.implementation), None)
            if template is None:
                raise HTTPException(422, f"{idx.implementation} niet ondersteund")
            req = copy.deepcopy(template)
            req.pop("presets", None)
            req.update(name=T.owned_name(idx.name), enableRss=True, enableAutomaticSearch=True,
                       enableInteractiveSearch=True, priority=idx.priority, tags=[])
            for k, v in engine.indexer_fields(t, idx).items():
                engine.fset(req, k, v, add=True)
            await t.client.provider_test("indexer", req)
    except ArrError as exc:
        return {"ok": False, "instance_id": t.iid, "message_nl": exc.message,
                "details": exc.details}
    return {"ok": True, "instance_id": t.iid,
            "message_nl": f"{t.inst['name']} kan indexer '{idx.name}' bevragen."}


class ProwlarrTestBody(BaseModel):
    url: str | None = Field(default=None, max_length=500)
    api_key: str | None = Field(default=None, max_length=200)


@router.post("/prowlarr/test")
async def test_prowlarr(body: ProwlarrTestBody | None = None,
                        principal: Principal = Depends(require_operator),
                        ctx: AppContext = Depends(get_app_context)) -> dict[str, Any]:
    body = body or ProwlarrTestBody()
    if body.url:
        _custom_target_needs_admin(principal)
    try:
        if body.url:
            validate_url(body.url)
        client = prowlarr_client(ctx, url=body.url or None,
                                 api_key=body.api_key if body.api_key not in (
                                     None, "", store.MASK) else None)
        if isinstance(client, ProwlarrClient):
            info = await client.status()
            indexers = await client.indexers()
            apps = await client.applications()
    except (ProwlarrError, ValueError) as exc:
        msg = exc.message if isinstance(exc, ProwlarrError) else str(exc)
        return {"ok": False, "message_nl": msg}
    return {"ok": True, "version": info.get("version"), "indexers": [
        {"id": i.get("id"), "name": i.get("name"), "protocol": i.get("protocol"),
         "tags": i.get("tags"), "suggested_scope": "anime" if T.is_anime_name(
             i.get("name"), i.get("definitionName")) else "both"} for i in indexers],
        "applications": [{"id": a.get("id"), "name": a.get("name"),
                          "implementation": a.get("implementation")} for a in apps],
        "message_nl": f"Prowlarr {info.get('version')} bereikbaar; {len(indexers)} "
                      "indexer(s)."}


class PathBody(BaseModel):
    instance_id: str
    path: str | None = Field(default=None, max_length=500)


@router.post("/paths/validate")
async def validate_path(body: PathBody, _: Principal = Depends(require_operator),
                        ctx: AppContext = Depends(get_app_context)) -> dict[str, Any]:
    row = instance_row(ctx, body.instance_id)
    if row is None:
        raise HTTPException(404, "Onbekende instantie")
    path = (body.path or store.root_folder_for(ctx, row["id"], row["media_type"],
                                               store.load(ctx))).rstrip("/")
    async with arr_client(ctx, row["id"]) as client:
        visible = await engine.path_visible(client, path)
        try:
            roots = await client.root_folders()
        except ArrError:
            roots = []
    root = next((r for r in roots if str(r.get("path", "")).rstrip("/") == path), None)
    msg = ("Map gevonden." if visible else "Map niet gevonden in de container: koppel de "
           "TrueNAS-dataset aan dit pad (hostactie)." if visible is False
           else "Kon niet controleren (instantie onbereikbaar?).")
    return {"instance_id": row["id"], "path": path, "exists": visible,
            "is_root_folder": root is not None,
            "accessible": None if root is None else root.get("accessible"),
            "free_space": None if root is None else root.get("freeSpace"), "message_nl": msg}


# -- Seerr --------------------------------------------------------------------------------------
class ConfirmBody(BaseModel):
    confirm: bool = False


class SeerrApplyBody(ConfirmBody):
    # adres waarmee de browser MediaGuard opent (window.location.hostname); vult ontbrekende
    # publieke instantie-URL's in zodat Seerr de vier instanties kan bereiken
    public_host: str | None = Field(default=None, max_length=253)


@router.post("/seerr/apply")
async def seerr_apply(body: SeerrApplyBody | None = None,
                      principal: Principal = Depends(require_admin),
                      ctx: AppContext = Depends(get_app_context)) -> dict[str, Any]:
    if body and body.public_host:
        from mediaguard.instances.registry import fill_public_urls

        fill_public_urls(ctx, body.public_host, principal.actor)
    try:
        return await apply_seerr(ctx, principal.actor, confirm=bool(body and body.confirm))
    except SeerrError as exc:
        raise _bad(exc) from exc


# -- optimaliseren na migratie --------------------------------------------------------------------
class OptimizeBody(BaseModel):
    instances: list[str] | None = None
    sections: list[str] | None = None
    confirm: bool = False


@router.get("/optimize-migrated")
def optimize_status(_: Principal = Depends(require_viewer),
                    ctx: AppContext = Depends(get_app_context)) -> list[dict[str, Any]]:
    out = []
    for inst in engine.eligible(ctx, None):
        opt = store.optimize_row(ctx, inst["id"])
        out.append({"instance_id": inst["id"], "name": inst["name"],
                    "shadow_import_done": store.shadow_import_done(ctx, inst["id"]),
                    "phase": opt["phase"] if opt else None,
                    "snapshot_id": opt["snapshot_id"] if opt else None,
                    "result": loads(opt["result_json"], {}) if opt else None,
                    "error": opt["error"] if opt else None})
    return out


@router.post("/optimize-migrated")
async def optimize_migrated(body: OptimizeBody, principal: Principal = Depends(require_admin),
                            ctx: AppContext = Depends(get_app_context)) -> dict[str, Any]:
    if body.sections:
        bad = [s for s in body.sections if s not in optimize.OPTIMIZE_SECTIONS]
        if bad:
            raise HTTPException(422, f"Onbekende sectie(s): {', '.join(bad)}")
    try:
        return await optimize.optimize(ctx, instances=body.instances, sections=body.sections,
                                       confirm=body.confirm, actor=principal.actor)
    except (ArrError, ValueError, optimize.OptimizeError) as exc:
        raise _bad(exc) from exc


@router.post("/optimize-migrated/cleanup")
async def optimize_cleanup(body: OptimizeBody, principal: Principal = Depends(require_admin),
                           ctx: AppContext = Depends(get_app_context)) -> dict[str, Any]:
    try:
        return await optimize.cleanup(ctx, instances=body.instances, confirm=body.confirm,
                                      actor=principal.actor)
    except (ArrError, ValueError) as exc:
        raise _bad(exc) from exc


@router.post("/optimize-migrated/rollback")
async def optimize_rollback(body: OptimizeBody, principal: Principal = Depends(require_admin),
                            ctx: AppContext = Depends(get_app_context)) -> dict[str, Any]:
    try:
        return await optimize.rollback(ctx, instances=body.instances, confirm=body.confirm,
                                       actor=principal.actor)
    except (ArrError, ValueError) as exc:
        raise _bad(exc) from exc


# -- bestanden hernoemen --------------------------------------------------------------------------
class RenamePreviewBody(BaseModel):
    instance_id: str
    ids: list[int] | None = None
    limit: int = Field(default=500, ge=1, le=5000)


@router.post("/rename/preview")
async def rename_preview(body: RenamePreviewBody, _: Principal = Depends(require_operator),
                         ctx: AppContext = Depends(get_app_context)) -> dict[str, Any]:
    try:
        return await optimize.rename_preview(ctx, body.instance_id, body.ids, body.limit)
    except KeyError as exc:
        raise HTTPException(404, "Onbekende instantie") from exc
    except ArrError as exc:
        raise _bad(exc) from exc


class RenameFile(BaseModel):
    owner_id: int
    file_id: int


class RenameApplyBody(BaseModel):
    instance_id: str
    files: list[RenameFile] = Field(min_length=1, max_length=5000)
    confirm: bool = False


@router.post("/rename/apply")
async def rename_apply(body: RenameApplyBody, principal: Principal = Depends(require_admin),
                       ctx: AppContext = Depends(get_app_context)) -> dict[str, Any]:
    if not body.confirm:
        raise HTTPException(409, "Hernoemen vereist expliciete bevestiging (confirm: true)")
    try:
        return await optimize.rename_apply(ctx, body.instance_id,
                                           [f.model_dump() for f in body.files], True,
                                           principal.actor)
    except KeyError as exc:
        raise HTTPException(404, "Onbekende instantie") from exc
    except (ArrError, optimize.OptimizeError) as exc:
        raise _bad(exc) from exc
