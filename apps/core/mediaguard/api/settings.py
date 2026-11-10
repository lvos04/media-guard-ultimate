"""Instellingen-API (Q2.3): schema, effectieve waarde met overerving, scoped wijzigen,
simulatie en secrets (alleen schrijven; lezen geeft enkel ``is_set``)."""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Depends, HTTPException, Query, Request, Response
from pydantic import BaseModel, Field

from mediaguard.audit import audit
from mediaguard.auth import Principal, require_admin, require_operator, require_viewer
from mediaguard.context import AppContext, get_app_context
from mediaguard.settings_registry import SCOPE_ORDER, SettingDef

router = APIRouter(prefix="/api/v1/settings", tags=["settings"])


def register_settings() -> None:
    from mediaguard.hub_url import HUB_URL_KEY, validate_hub_url
    from mediaguard.settings_registry import FORMAT_VALIDATORS, REGISTRY

    FORMAT_VALIDATORS[HUB_URL_KEY] = validate_hub_url
    REGISTRY.register(
        SettingDef("ui.theme", "enum", "system", "Systeem", "Thema", "Theme",
                   choices=("dark", "light", "system"),
                   choice_labels_nl={"dark": "Donker", "light": "Licht", "system": "Systeem"}),
        SettingDef("system.timezone", "string", "Europe/Amsterdam", "Systeem", "Tijdzone",
                   "Time zone", help_nl="Gebruikt voor planningen en maandreset van budgetten."),
        # Peil O-11 (ADR-0016 punt 1): optioneel adres van de Hub voor de link 'Terug naar de
        # Hub'. Leeg = geen link. Nooit een standaard- of voorbeeldadres; validatie in
        # mediaguard/hub_url.py (422 bij een ongeldige waarde).
        SettingDef(HUB_URL_KEY, "string", "", "Systeem", "Hub-adres", "Hub address",
                   help_nl="Vorm: http(s)://<NAS-adres>:<poort>. Geen localhost. "
                           "Leeg = geen link 'Terug naar de Hub'.",
                   help_en="Format: http(s)://<NAS address>:<port>. No localhost. "
                           "Empty = no 'Back to the Hub' link."),
        # ui.default_locale ('Taal') is weg (punt 39): de taal kies je per gebruiker in het
        # profielmenu; de globale instelling deed niets.
        SettingDef("jobs.max_concurrency", "int", 4, "Wachtrij & workers",
                   "Gelijktijdige achtergrondtaken", "Concurrent background jobs",
                   minimum=1, maximum=32),
        SettingDef("logging.level", "enum", "info", "Systeem", "Logniveau", "Log level",
                   choices=("debug", "info", "warning", "error"), advanced=True),
        # Integriteitscontroles: zichtbaar maar NIET uitschakelbaar (Q3)
        SettingDef("integrity.verify_streams_before_activate", "bool", True, "Veiligheid",
                   "Streams verifiëren vóór activatie", "Verify streams before activation",
                   help_nl="Altijd aan. Kan niet worden uitgezet.", integrity_locked=True),
        SettingDef("integrity.require_recoverable_copy", "bool", True, "Veiligheid",
                   "Herstelbare kopie vereist vóór vervanging",
                   "Recoverable copy required before replacement", integrity_locked=True),
    )


@router.get("/schema")
def schema(request: Request, _: Principal = Depends(require_viewer),
           ctx: AppContext = Depends(get_app_context)) -> Response:
    """Statisch per versie (~85 kB): ETag + cache-hint, zodat de browser hem niet bij elk
    instellingenblok opnieuw downloadt."""
    import hashlib
    import json

    from mediaguard.settings_curation import annotate, sections_public

    cached = ctx.services.get("settings_schema_cache")
    if cached is None:
        body = json.dumps({"scopes": SCOPE_ORDER, "sections": sections_public(),
                           "settings": [annotate(d.public()) for d in ctx.registry.all()]},
                          ensure_ascii=False, default=str).encode()
        etag = '"' + hashlib.sha256(body).hexdigest()[:20] + '"'
        cached = ctx.services["settings_schema_cache"] = (body, etag)
    body, etag = cached
    headers = {"ETag": etag, "Cache-Control": "private, max-age=3600"}
    if request.headers.get("if-none-match") == etag:
        return Response(status_code=304, headers=headers)
    return Response(content=body, media_type="application/json", headers=headers)


@router.get("/effective")
def effective(
    keys: str | None = Query(None, description="komma-gescheiden; leeg = alle"),
    scope: str | None = Query(None, description="keten als 'instance:sonarr-anime,series:x'"),
    _: Principal = Depends(require_viewer),
    ctx: AppContext = Depends(get_app_context),
) -> list[dict[str, Any]]:
    chain: dict[str, str] = {}
    if scope:
        for part in scope.split(","):
            if ":" in part:
                st, sid = part.split(":", 1)
                if st not in SCOPE_ORDER:
                    raise HTTPException(422, f"Onbekende scope {st}")
                chain[st] = sid
    wanted = [k for k in (keys.split(",") if keys else [d.key for d in ctx.registry.all()]) if k]
    out = []
    for key in wanted:
        try:
            out.append(ctx.config.effective(key, chain))
        except KeyError as exc:
            raise HTTPException(404, str(exc)) from exc
    return out


@router.get("/overrides")
def overrides(scope_type: str | None = None, scope_id: str | None = None,
              _: Principal = Depends(require_viewer),
              ctx: AppContext = Depends(get_app_context)) -> list[dict[str, Any]]:
    return ctx.config.overrides(scope_type, scope_id)


class ScopedChange(BaseModel):
    scope_type: str = "global"
    scope_id: str = ""
    key: str
    value: Any = None
    reset: bool = False


# Instellingen met eigen poortwachter-endpoint (activatie vereist sleutels/toestemming/tests).
GUARDED_PREFIXES = ("ai.",)


class ScopedPatch(BaseModel):
    changes: list[ScopedChange] = Field(min_length=1, max_length=200)
    reason: str = ""


@router.patch("/scoped")
def patch_scoped(body: ScopedPatch, principal: Principal = Depends(require_operator),
                 ctx: AppContext = Depends(get_app_context)) -> dict[str, Any]:
    """Alles-of-niets: eerst alles valideren, dan pas opslaan (geen half-opgeslagen form).
    Gevaarlijke instellingen (originelen, beveiliging, adressen) alleen voor een beheerder;
    'bibliotheek vrijgeven' alleen via de knop met bevestiging (zie settings_guard)."""
    from mediaguard.settings_guard import check_change

    for change in body.changes:
        if change.key.startswith(GUARDED_PREFIXES):
            raise HTTPException(409, f"{change.key} wordt beheerd via een eigen scherm met "
                                     "veiligheidscontroles (bv. /api/v1/ai/settings)")
        try:
            d = ctx.registry.get_def(change.key)
        except KeyError as exc:
            raise HTTPException(404, str(exc)) from exc
        check_change(principal.role, change.key, d.label_nl)
        if change.scope_type not in d.scopes:
            raise HTTPException(422, f"{change.key} kan niet op scope '{change.scope_type}'")
        if not change.reset:
            try:
                d.validate(change.value)
            except ValueError as exc:
                raise HTTPException(422, str(exc)) from exc
    applied = []
    for change in body.changes:
        old = ctx.config.raw(change.scope_type, change.scope_id, change.key)
        if change.reset:
            ctx.config.reset(change.scope_type, change.scope_id, change.key)
            new = None
        else:
            new = ctx.config.set(change.scope_type, change.scope_id, change.key, change.value,
                                 principal.actor)
        audit(ctx.db, actor=principal.actor, action="settings.change", target_type="setting",
              target_id=f"{change.scope_type}:{change.scope_id}:{change.key}",
              instance_id=change.scope_id if change.scope_type == "instance" else None,
              reason=body.reason or None, old_value=old, new_value=new,
              undo_hint="Herstel de oude waarde of 'Herstel standaard'")
        applied.append({"key": change.key, "scope_type": change.scope_type,
                        "scope_id": change.scope_id, "value": new})
    ctx.events.publish("settings.changed", {"changes": applied})
    return {"applied": applied}


class SimulateBody(BaseModel):
    changes: list[ScopedChange]
    context: dict[str, str] = Field(default_factory=dict)
    keys: list[str] = Field(default_factory=list)


@router.post("/simulate")
def simulate(body: SimulateBody, _: Principal = Depends(require_viewer),
             ctx: AppContext = Depends(get_app_context)) -> dict[str, Any]:
    """Toont effectieve waarden vóór/na zonder op te slaan (savepoint + rollback)."""
    keys = body.keys or sorted({c.key for c in body.changes})
    before = {k: ctx.config.effective(k, body.context)["value"] for k in keys}
    after: dict[str, Any] = {}
    conn = ctx.db._connect()
    with ctx.db._lock:
        conn.execute("SAVEPOINT simulate")
        try:
            for c in body.changes:
                if c.reset:
                    ctx.config.reset(c.scope_type, c.scope_id, c.key)
                else:
                    ctx.config.set(c.scope_type, c.scope_id, c.key, c.value, "simulate")
            after = {k: ctx.config.effective(k, body.context)["value"] for k in keys}
        finally:
            conn.execute("ROLLBACK TO simulate")
            conn.execute("RELEASE simulate")
            ctx.config.invalidate()  # cache mag de teruggedraaide waarden niet onthouden
    return {"before": before, "after": after}


@router.get("/search")
def search(q: str = Query(min_length=1), _: Principal = Depends(require_viewer),
           ctx: AppContext = Depends(get_app_context)) -> list[dict[str, Any]]:
    """'Zoeken in instellingen' over alle opties (Q2.2)."""
    from mediaguard.settings_curation import annotate

    needle = q.lower()
    hits = []
    for d in ctx.registry.all():
        hay = " ".join([d.key, d.label_nl, d.label_en, d.help_nl, d.category]).lower()
        if needle in hay:
            hits.append(annotate(d.public()))
    return hits


class SecretBody(BaseModel):
    value: str = Field(min_length=1, max_length=8192)


ALLOWED_SECRET_PREFIXES = ("instance:", "source:", "seerr:", "ai:", "truenas:", "notify:",
                           "indexer:", "downloadclient:")


@router.get("/secrets")
def secrets_list(_: Principal = Depends(require_admin),
                 ctx: AppContext = Depends(get_app_context)) -> list[dict[str, Any]]:
    """``readable`` = te ontsleutelen met de huidige secret.key. Onleesbaar telt niet als
    'ingesteld' (``is_set`` false): de koppeling werkt dan niet."""
    rows = ctx.db.fetch_dicts(
        "SELECT name, updated_at, updated_by FROM secrets ORDER BY name")
    bad = set(ctx.secrets.health()["unreadable"])  # type: ignore[arg-type]
    for r in rows:
        r["readable"] = r["name"] not in bad
        r["is_set"] = r["readable"]
    return rows


@router.get("/secrets/health")
def secrets_health(_: Principal = Depends(require_viewer),
                   ctx: AppContext = Depends(get_app_context)) -> dict[str, Any]:
    """Past secret.key bij de database? (alleen aantallen; namen alleen voor beheerders)"""
    h = ctx.secrets.health()
    unreadable = list(h["unreadable"])  # type: ignore[call-overload]
    return {"ok": h["ok"], "total": h["total"], "unreadable_count": len(unreadable),
            "message_nl": None if h["ok"] else (
                f"{len(unreadable)} opgeslagen sleutel(s) kunnen niet worden ontsleuteld: "
                "secret.key past niet bij deze database.")}


@router.put("/secrets/{name}")
def secret_put(name: str, body: SecretBody, principal: Principal = Depends(require_admin),
               ctx: AppContext = Depends(get_app_context)) -> dict[str, Any]:
    if not name.startswith(ALLOWED_SECRET_PREFIXES):
        raise HTTPException(422, "Onbekende geheimnaam")
    ctx.secrets.set(name, body.value, principal.actor)
    audit(ctx.db, actor=principal.actor, action="secret.set", target_type="secret",
          target_id=name)
    return {"name": name, "is_set": True}


@router.delete("/secrets/{name}")
def secret_delete(name: str, principal: Principal = Depends(require_admin),
                  ctx: AppContext = Depends(get_app_context)) -> dict[str, Any]:
    ctx.secrets.delete(name)
    audit(ctx.db, actor=principal.actor, action="secret.delete", target_type="secret",
          target_id=name)
    return {"name": name, "is_set": False}


# -- veilige standaard (rapport 4 nov, H1) ----------------------------------------------------
# Instellingen waarbij afwijken van de standaard herstel of kwaliteit kost. Ze blijven
# instelbaar, maar een afwijking staat altijd zichtbaar in 'Wacht op jou' en bij Instellingen.
SAFETY_KEYS: dict[str, str] = {
    "quarantine.mode": "Originelen worden direct verwijderd na de controle: een vervanging "
                       "terugzetten kan dan niet meer.",
    "quarantine.retention_days": "Originelen worden niet (lang genoeg) bewaard om een "
                                 "vervanging terug te zetten.",
    "encoding.keep_all_subtitles": "Niet alle ondertitels gaan mee naar het AV1-bestand; ze "
                                   "kunnen ongemerkt verdwijnen.",
    "encoding.keep_all_audio": "Niet alle audiosporen gaan mee naar het AV1-bestand.",
    "dv.allow_av1": "Dolby Vision-bestanden worden ook omgezet (standaard blijven ze "
                    "onaangeroerd).",
}


def safety_deviations(ctx: AppContext) -> list[dict[str, Any]]:
    out = []
    for key, why in SAFETY_KEYS.items():
        try:
            d = ctx.registry.get_def(key)
        except KeyError:
            continue
        value = ctx.config.value(key)
        if value == d.default:
            continue
        if key == "quarantine.retention_days" and isinstance(value, int) and value >= 1 and \
                ctx.config.value("quarantine.mode") != "immediate_after_check":
            continue  # korter of langer bewaren is een keuze, zolang er iets bewaard wordt
        label = d.choice_labels_nl.get(str(value), str(value)) if d.choice_labels_nl else value
        default = (d.choice_labels_nl.get(str(d.default), str(d.default))
                   if d.choice_labels_nl else d.default)
        out.append({"key": key, "label_nl": d.label_nl, "value": value, "value_nl": label,
                    "default": d.default, "default_nl": default, "why_nl": why,
                    "category": d.category})
    return out


@router.get("/safety")
def safety(_: Principal = Depends(require_viewer),
           ctx: AppContext = Depends(get_app_context)) -> dict[str, Any]:
    """Welke instellingen afwijken van de veilige standaard (voor de waarschuwing bovenaan
    Instellingen: 'N instellingen wijken af van de veilige standaard')."""
    rows = safety_deviations(ctx)
    return {"count": len(rows), "deviations": rows}


class SafetyRestore(BaseModel):
    keys: list[str] = Field(default_factory=list, max_length=20)


@router.post("/safety/restore")
def safety_restore(body: SafetyRestore, principal: Principal = Depends(require_operator),
                   ctx: AppContext = Depends(get_app_context)) -> dict[str, Any]:
    """Zet (de opgegeven of alle) afwijkende veiligheidsinstellingen terug op standaard."""
    wanted = set(body.keys) or set(SAFETY_KEYS)
    unknown = wanted - set(SAFETY_KEYS)
    if unknown:
        raise HTTPException(422, f"Geen veiligheidsinstelling: {', '.join(sorted(unknown))}")
    restored = []
    for row in safety_deviations(ctx):
        if row["key"] not in wanted:
            continue
        old = ctx.config.raw("global", "", row["key"])
        ctx.config.reset("global", "", row["key"])
        audit(ctx.db, actor=principal.actor, action="settings.change", target_type="setting",
              target_id=f"global::{row['key']}", reason="veilige standaard hersteld",
              old_value=old, new_value=None)
        restored.append(row["key"])
    if restored:
        ctx.events.publish("settings.changed", {"changes": [
            {"key": k, "scope_type": "global", "scope_id": "", "value": None}
            for k in restored]})
    return {"restored": restored, **safety(principal, ctx)}
