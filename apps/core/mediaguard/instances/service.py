"""Instantie-operaties: health-check/polling, suspend/resume, webhookregistratie,
metadata-cache (profielen, rootfolders, indexers) en ownership-analyse."""

from __future__ import annotations

import asyncio
import contextlib
import logging
import secrets
from typing import TYPE_CHECKING, Any

from mediaguard import taskwatch
from mediaguard.arr.client import ArrAuthError, ArrError, ArrNotFound, ArrUnavailable
from mediaguard.audit import audit
from mediaguard.db import dumps, loads, utcnow
from mediaguard.instances.registry import (
    arr_client,
    env_hook_key,
    get_instance,
    hookkey_secret,
    instance_row,
    list_instances,
    set_status,
)

if TYPE_CHECKING:
    from mediaguard.context import AppContext

LOGGER = logging.getLogger(__name__)

LIBRARY_ONLY_HEALTH = {"RemovedMovieCheck", "RemovedSeriesCheck"}
STATUS_NL = {"healthy": "Gezond", "degraded": "Verminderd", "offline": "Offline",
             "unknown": "Onbekend"}
# Een korte onderbreking (herstart, update) is geen melding waard
ALERT_AFTER_SECONDS = 600


def _notify(ctx: AppContext, severity: str, title: str, body: str,
            instance_id: str | None) -> int:
    from mediaguard.api.system import notify

    return notify(ctx, severity, title, body,
                  link="/instellingen/instanties" if instance_id else None,
                  instance_id=instance_id)


# -- health -------------------------------------------------------------------------------
async def check_instance(ctx: AppContext, instance_id: str, *, notify: bool = True) -> dict:
    inst = get_instance(ctx, instance_id)
    if inst is None:
        raise KeyError(instance_id)
    result: dict[str, Any] = {"instance_id": instance_id, "ok": False, "checks": []}
    api_key_valid: bool | None = None
    version = app_name = None
    health: list[dict[str, Any]] | None = None
    if not ctx.secrets.has(f"instance:{instance_id}:apikey"):
        status, error = "degraded", "Geen API-sleutel ingesteld"
        api_key_valid = False
    else:
        try:
            async with arr_client(ctx, instance_id, timeout=10) as client:
                info = await client.system_status()
                version = str(info.get("version") or "") or None
                app_name = str(info.get("appName") or "") or None
                health = await client.health()
        except ArrAuthError:
            status, error, api_key_valid = "degraded", "API-sleutel ongeldig of verlopen", False
        except ArrUnavailable as exc:
            status, error = "offline", exc.message
        except ArrError as exc:
            status, error = "degraded", exc.message
        else:
            api_key_valid = True
            # 'van TMDb/TVDB verwijderd' gaat over de bibliotheek, niet over de werking van de
            # instantie; MediaGuard ruimt titels zonder bestand zelf op (automation.py)
            errors = [h for h in (health or []) if str(h.get("type")).lower() == "error"
                      and h.get("source") not in LIBRARY_ONLY_HEALTH]
            if app_name and app_name.lower() != inst.app:
                status = "degraded"
                error = (f"Verkeerde applicatie: verwacht {inst.app}, maar {app_name} "
                         "antwoordt op deze URL")
            elif errors:
                from mediaguard.instances.health_nl import summarize

                status = "degraded"
                error = summarize(errors)
            else:
                status, error = "healthy", None
    previous = set_status(ctx, instance_id, status, error=error, version=version,
                          app_name=app_name, api_key_valid=api_key_valid, health=health)
    result.update(ok=status == "healthy", status=status, previous_status=previous,
                  error=error, version=version, app_name=app_name,
                  api_key_valid=api_key_valid, health=health or [])
    if previous != status:
        ctx.events.publish("instance.status", {"status": status, "previous": previous,
                                               "error": error}, instance_id=instance_id)
    if notify:
        _alert(ctx, inst.name, instance_id, status, previous, error,
               key_invalid=api_key_valid is False)
    return result


def _alert(ctx: AppContext, name: str, instance_id: str, status: str, previous: str,
           error: str | None, *, key_invalid: bool) -> None:
    """Meldt een probleem pas als het langer dan 10 minuten duurt (een nachtelijke herstart
    gaf anders 8 meldingen) en zet 'werkt weer' in dezelfde melding in plaats van een
    nieuwe. Een ongeldige API-sleutel wordt direct gemeld: daar moet iemand iets doen."""
    import time

    key = f"instance_alert:{instance_id}"
    alert = ctx.state_get(key, {}) or {}
    if status == "healthy":
        if alert.get("nid"):
            since = time.strftime("%H:%M", time.localtime(alert.get("since", time.time())))
            ctx.db.execute(
                "UPDATE notifications SET severity='success', title=?, body=?, read=1 "
                "WHERE id=?", (f"{name}: werkt weer",
                               f"Was {STATUS_NL.get(alert.get('status', ''), '')} sinds "
                               f"{since}; nu weer gezond.", int(alert["nid"])))
            ctx.events.publish("notification", {"severity": "success",
                                                "title": f"{name}: werkt weer"},
                               instance_id=instance_id, persist=False)
        if alert:
            ctx.state_set(key, {})
        return
    if status not in ("offline", "degraded"):
        return
    now = time.time()
    if not alert or alert.get("status") != status and not alert.get("nid"):
        alert = {"since": alert.get("since", now), "status": status}
        ctx.state_set(key, alert)
    if alert.get("nid"):
        return
    if key_invalid or now - float(alert["since"]) >= ALERT_AFTER_SECONDS:
        severity = "error" if key_invalid or status == "offline" else "warning"
        nid = _notify(ctx, severity, f"{name}: {STATUS_NL[status]}",
                      error or "Instantie reageert niet.", instance_id)
        ctx.state_set(key, {**alert, "nid": nid})


async def health_loop(ctx: AppContext) -> None:
    await asyncio.sleep(20)  # forks eerst laten opstarten (geen valse 'offline')
    while True:
        try:
            interval = int(ctx.config.value("instances.health_interval_seconds"))
        except Exception:  # noqa: BLE001 - instelling onleesbaar: standaard
            interval = 60
        for inst in list_instances(ctx):
            try:
                await check_instance(ctx, inst.id)
            except Exception:  # noqa: BLE001 - polling mag nooit stoppen
                LOGGER.exception("health-check %s mislukt", inst.id)
        taskwatch.ok(ctx, "instance_health")
        await asyncio.sleep(max(10, interval))


# -- metadata-cache ------------------------------------------------------------------------
async def refresh_metadata(ctx: AppContext, instance_id: str) -> dict[str, int]:
    """Cachet profielen, rootfolders en indexers (voor UI, diagnose en Seerr-checks)."""
    now = utcnow()
    counts: dict[str, int] = {}
    async with arr_client(ctx, instance_id) as client:
        quality = await client.quality_profiles()
        languages = await client.language_profiles()
        try:
            formats = await client.custom_formats()
        except ArrNotFound:
            formats = []
        roots = await client.root_folders()
        indexers = await client.indexers()
    with ctx.db.transaction() as conn:
        conn.execute("DELETE FROM instance_profiles WHERE instance_id=?", (instance_id,))
        for kind, items in (("quality", quality), ("language", languages),
                            ("custom_format", formats)):
            for item in items:
                conn.execute(
                    "INSERT INTO instance_profiles(instance_id,kind,provider_id,name,data_json,"
                    "synced_at) VALUES (?,?,?,?,?,?)",
                    (instance_id, kind, int(item.get("id", 0)), str(item.get("name", "")),
                     dumps(_profile_summary(item)), now))
            counts[kind] = len(items)
        seen = []
        for root in roots:
            path = str(root.get("path", ""))
            seen.append(path)
            conn.execute(
                "INSERT INTO instance_root_mappings(instance_id,root_path,provider_root_folder_id,"
                "accessible,free_space,total_space,synced_at) VALUES (?,?,?,?,?,?,?) "
                "ON CONFLICT(instance_id,root_path) DO UPDATE SET "
                "provider_root_folder_id=excluded.provider_root_folder_id, "
                "accessible=excluded.accessible, free_space=excluded.free_space, "
                "total_space=excluded.total_space, synced_at=excluded.synced_at",
                (instance_id, path, root.get("id"), int(bool(root.get("accessible", True))),
                 root.get("freeSpace"), root.get("totalSpace"), now))
        marks = ",".join("?" for _ in seen) or "''"
        conn.execute(
            f"UPDATE instance_root_mappings SET provider_root_folder_id=NULL, accessible=0 "
            f"WHERE instance_id=? AND root_path NOT IN ({marks})", (instance_id, *seen))
        counts["root_folders"] = len(roots)
        conn.execute("DELETE FROM instance_indexer_assignments WHERE instance_id=?",
                     (instance_id,))
        for idx in indexers:
            conn.execute(
                "INSERT INTO instance_indexer_assignments(instance_id,indexer_id,name,"
                "implementation,protocol,enable_rss,enable_automatic_search,"
                "enable_interactive_search,priority,tags_json,synced_at) "
                "VALUES (?,?,?,?,?,?,?,?,?,?,?)",
                (instance_id, int(idx["id"]), str(idx.get("name", "")),
                 idx.get("implementation"), idx.get("protocol"),
                 int(bool(idx.get("enableRss"))), int(bool(idx.get("enableAutomaticSearch"))),
                 int(bool(idx.get("enableInteractiveSearch"))), idx.get("priority"),
                 dumps(idx.get("tags") or []), now))
        counts["indexers"] = len(indexers)
    return counts


def _profile_summary(item: dict[str, Any]) -> dict[str, Any]:
    keep = ("upgradeAllowed", "cutoff", "minFormatScore", "cutoffFormatScore", "language")
    out = {k: item.get(k) for k in keep if k in item}
    items = item.get("items")
    if isinstance(items, list):
        allowed = []
        for entry in items:
            if not entry.get("allowed"):
                continue
            if entry.get("quality"):
                allowed.append(entry["quality"].get("name"))
            elif entry.get("name"):
                allowed.append(entry.get("name"))
        out["allowed"] = allowed
    return out


# -- suspend / resume -------------------------------------------------------------------------
SUSPEND_FIELDS = ("enableRss", "enableAutomaticSearch")


class SuspendError(Exception):
    def __init__(self, message: str, details: Any = None) -> None:
        super().__init__(message)
        self.details = details


async def suspend_instance(ctx: AppContext, instance_id: str, actor: str,
                           reason: str = "") -> dict[str, Any]:
    """Zet RSS + automatisch zoeken uit op alle indexers van deze instantie en onthoudt de
    exacte vorige waarden. Andere instanties worden niet aangeraakt (R6.7)."""
    row = instance_row(ctx, instance_id)
    if row is None:
        raise KeyError(instance_id)
    if row["suspended"]:
        return {"suspended": True, "already": True,
                "state": loads(row["suspend_state_json"], {})}
    async with arr_client(ctx, instance_id) as client:
        indexers = await client.indexers()
        previous = {str(i["id"]): {f: bool(i.get(f)) for f in SUSPEND_FIELDS}
                    | {"name": i.get("name")} for i in indexers}
        state = {"indexers": previous, "at": utcnow(), "by": actor, "reason": reason}
        # eerst vastleggen (crash-veilig) en de pre-grab-hook laten weigeren
        ctx.db.execute(
            "UPDATE instances SET suspended=1, suspend_state_json=?, suspended_at=?, "
            "suspended_by=?, updated_at=? WHERE id=?",
            (dumps(state), state["at"], actor, utcnow(), instance_id))
        changed: list[dict[str, Any]] = []
        try:
            for idx in indexers:
                if not any(idx.get(f) for f in SUSPEND_FIELDS):
                    continue
                body = dict(idx)
                for f in SUSPEND_FIELDS:
                    body[f] = False
                await client.provider_update("indexer", body, force_save=True)
                changed.append(idx)
        except ArrError as exc:
            for idx in changed:  # terugdraaien wat al gewijzigd was
                with contextlib.suppress(ArrError):
                    await client.provider_update("indexer", idx, force_save=True)
            ctx.db.execute("UPDATE instances SET suspended=0, suspend_state_json=NULL, "
                           "suspended_at=NULL, suspended_by=NULL WHERE id=?", (instance_id,))
            raise SuspendError(f"Pauzeren mislukt, wijzigingen teruggedraaid: {exc.message}",
                               exc.to_dict()) from exc
    audit(ctx.db, actor=actor, action="instance.suspend", target_type="instance",
          target_id=instance_id, instance_id=instance_id, reason=reason,
          old_value={"indexers": previous}, new_value={"suspended": True,
                                                        "changed": [i["id"] for i in changed]},
          undo_hint=f"POST /api/v1/instances/{instance_id}/resume")
    ctx.events.publish("instance.suspended", {"changed": len(changed)}, instance_id=instance_id)
    return {"suspended": True, "changed_indexers": [i["id"] for i in changed],
            "state": state}


async def resume_instance(ctx: AppContext, instance_id: str, actor: str) -> dict[str, Any]:
    row = instance_row(ctx, instance_id)
    if row is None:
        raise KeyError(instance_id)
    if not row["suspended"]:
        return {"suspended": False, "already": True}
    state = loads(row["suspend_state_json"], {}) or {}
    previous: dict[str, dict[str, Any]] = state.get("indexers", {})
    restored: list[int] = []
    missing: list[int] = []
    errors: list[dict[str, Any]] = []
    async with arr_client(ctx, instance_id) as client:
        for idx_id, prev in previous.items():
            try:
                current = await client.provider_get("indexer", int(idx_id))
            except ArrNotFound:
                missing.append(int(idx_id))
                continue
            except ArrError as exc:
                errors.append(exc.to_dict())
                continue
            if all(bool(current.get(f)) == bool(prev.get(f)) for f in SUSPEND_FIELDS):
                restored.append(int(idx_id))
                continue
            body = dict(current)
            for f in SUSPEND_FIELDS:
                body[f] = bool(prev.get(f))
            try:
                await client.provider_update("indexer", body, force_save=True)
                restored.append(int(idx_id))
            except ArrError as exc:
                errors.append(exc.to_dict())
    if errors:
        raise SuspendError("Hervatten gedeeltelijk mislukt; instantie blijft gepauzeerd. "
                           "Probeer opnieuw.", {"errors": errors, "restored": restored})
    ctx.db.execute("UPDATE instances SET suspended=0, suspend_state_json=NULL, "
                   "suspended_at=NULL, suspended_by=NULL, updated_at=? WHERE id=?",
                   (utcnow(), instance_id))
    audit(ctx.db, actor=actor, action="instance.resume", target_type="instance",
          target_id=instance_id, instance_id=instance_id,
          old_value={"suspended": True}, new_value={"restored": restored, "missing": missing})
    ctx.events.publish("instance.resumed", {"restored": len(restored)}, instance_id=instance_id)
    return {"suspended": False, "restored_indexers": restored, "missing_indexers": missing}


# -- webhook-registratie ---------------------------------------------------------------------
WEBHOOK_NAME = "MediaGuard"


def webhook_url(ctx: AppContext, instance_id: str) -> str:
    return f"{ctx.settings.public_core_url.rstrip('/')}/api/v1/hooks/arr-webhook/{instance_id}"


def ensure_hook_key(ctx: AppContext, instance_id: str, rotate: bool = False,
                    actor: str = "system") -> str:
    if rotate and env_hook_key(instance_id):
        raise SuspendError(
            "De hook-sleutel van deze instantie is vastgezet via de omgeving "
            "(MG_INSTANCE_..._HOOKKEY = MEDIAGUARD_HOOK_KEY van de fork). Roteren via de UI "
            "zou de pre-grab/pre-import-hooks van de fork breken; wijzig de sleutel in de "
            "app-configuratie (.env) en herstart de app.")
    key = None if rotate else ctx.secrets.get(hookkey_secret(instance_id))
    if not key:
        key = secrets.token_urlsafe(32)
        ctx.secrets.set(hookkey_secret(instance_id), key, actor)
    ctx.set_hook_key(instance_id, key)
    return key


def _set_field(fields: list[dict[str, Any]], name: str, value: Any) -> None:
    for field in fields:
        if str(field.get("name", "")).lower() == name.lower():
            field["value"] = value
            return
    fields.append({"name": name, "value": value})


def _field_value(resource: dict[str, Any], name: str) -> Any:
    for field in resource.get("fields") or []:
        if str(field.get("name", "")).lower() == name.lower():
            return field.get("value")
    return None


async def register_webhook(ctx: AppContext, instance_id: str, actor: str,
                           rotate: bool = False) -> dict[str, Any]:
    """Maakt/werkt de standaard Webhook-connection bij die alle events naar core stuurt
    (contract fork-hooks.md, Basic auth = instance_id + hook key).

    Per instantie geserialiseerd: provisioning (achtergrond) en een UI-klik tegelijk maakten
    in de stacktest twee 'MediaGuard'-connections aan (Sonarr weigert daarna elke test/
    update met 'Name should be unique'). Bestaande dubbelen worden opgeruimd."""
    if get_instance(ctx, instance_id) is None:
        raise KeyError(instance_id)
    locks_ = ctx.services.setdefault("instances.webhook_locks", {})
    lock = locks_.setdefault(instance_id, asyncio.Lock())
    async with lock:
        return await _register_webhook(ctx, instance_id, actor, rotate)


async def _register_webhook(ctx: AppContext, instance_id: str, actor: str,
                            rotate: bool) -> dict[str, Any]:
    key = ensure_hook_key(ctx, instance_id, rotate=rotate, actor=actor)
    url = webhook_url(ctx, instance_id)
    async with arr_client(ctx, instance_id) as client:
        existing_list = await client.notifications()
        ours = [n for n in existing_list if n.get("implementation") == "Webhook" and (
            n.get("name") == WEBHOOK_NAME or _field_value(n, "url") == url)]
        existing = ours[0] if ours else None
        for dup in ours[1:]:
            with contextlib.suppress(ArrError):
                await client.provider_delete("notification", int(dup["id"]))
        schema = await client.provider_schema("notification")
        template = next((s for s in schema if s.get("implementation") == "Webhook"), None)
        if template is None and existing is None:
            raise SuspendError("Deze instantie biedt geen Webhook-connection aan")
        body: dict[str, Any] = dict(existing or template or {})
        body.pop("presets", None)
        body["name"] = WEBHOOK_NAME
        body["implementation"] = "Webhook"
        body["configContract"] = (template or body).get("configContract", "WebhookSettings")
        body.setdefault("tags", [])
        fields = [dict(f) for f in body.get("fields") or (template or {}).get("fields") or []]
        _set_field(fields, "url", url)
        _set_field(fields, "method", 1)  # POST
        _set_field(fields, "username", instance_id)
        _set_field(fields, "password", key)
        body["fields"] = fields
        supports = {**(template or {}), **body}
        for name, value in supports.items():
            if name.startswith("supportsOn") and value:
                body["on" + name[len("supportsOn"):]] = True
        body["includeHealthWarnings"] = True
        if existing is not None:
            saved = await client.provider_update("notification", body, force_save=True)
            action = "updated"
        else:
            saved = await client.provider_create("notification", body, force_save=True)
            action = "created"
    notification_id = int(saved.get("id") or (existing or {}).get("id") or 0) or None
    ctx.db.execute("UPDATE instances SET webhook_notification_id=?, webhook_registered_at=? "
                   "WHERE id=?", (notification_id, utcnow(), instance_id))
    audit(ctx.db, actor=actor, action="instance.webhook_register", target_type="instance",
          target_id=instance_id, instance_id=instance_id,
          new_value={"url": url, "notification_id": notification_id, "action": action,
                     "rotated": rotate})
    events = sorted(k for k, v in body.items() if k.startswith("on") and v is True)
    return {"action": action, "notification_id": notification_id, "url": url,
            "events": events}


# -- ownership ------------------------------------------------------------------------------
def _canonical(row: dict[str, Any]) -> str | None:
    if row["kind"] == "series":
        if row.get("tvdb_id"):
            return f"tvdb:{row['tvdb_id']}"
    elif row.get("tmdb_id"):
        return f"tmdb:{row['tmdb_id']}"
    if row.get("imdb_id"):
        return f"imdb:{row['imdb_id']}"
    return None


def ownership_records(ctx: AppContext, canonical_id: str | None = None) -> list[dict]:
    if canonical_id:
        return ctx.db.fetch_dicts("SELECT * FROM instance_ownership WHERE canonical_id=? "
                                  "ORDER BY id", (canonical_id,))
    return ctx.db.fetch_dicts("SELECT * FROM instance_ownership ORDER BY id")


def ownership_conflicts(ctx: AppContext, include_intentional: bool = False) -> list[dict]:
    rows = ctx.db.fetch_dicts(
        "SELECT l.id, l.instance_id, l.kind, l.provider_id, l.title, l.year, l.tvdb_id, "
        "l.tmdb_id, l.imdb_id, l.quality_profile_id, l.root_folder_path, l.path, "
        "i.media_type, i.app FROM lib_items l JOIN instances i ON i.id=l.instance_id "
        "WHERE l.removed_at IS NULL AND i.archived=0")
    groups: dict[str, list[dict[str, Any]]] = {}
    for row in rows:
        canonical = _canonical(row)
        if canonical:
            groups.setdefault(canonical, []).append(row)
    owners = {}
    for rec in ownership_records(ctx):
        owners.setdefault(rec["canonical_id"], []).append(rec)
    conflicts: list[dict[str, Any]] = []
    for canonical, items in groups.items():
        records = owners.get(canonical, [])
        instances = sorted({i["instance_id"] for i in items})
        if len(instances) > 1:
            media_types = {i["media_type"] for i in items}
            intentional = bool(records) and all(
                any(r["instance_id"] == inst and (r["allow_multi"] or r["is_override"])
                    for r in records) for inst in instances)
            if intentional and not include_intentional:
                continue
            same_type = len(media_types) == 1
            conflicts.append({
                "canonical_id": canonical, "title": items[0]["title"],
                "type": "duplicate_same_library" if same_type else "cross_scope_duplicate",
                "severity": "info" if intentional else ("error" if same_type else "warning"),
                "intentional": intentional,
                "message_nl": (
                    "Bewust in meerdere bibliotheken (ownership-override)." if intentional else
                    "Dubbel in dezelfde soort bibliotheek: dubbele downloads/imports mogelijk."
                    if same_type else
                    "Zelfde titel in meerdere scopes (bijv. series én anime). Controleer de "
                    "routering of leg een ownership-override vast."),
                "items": items, "ownership": records,
            })
        elif records:
            owner_ids = {r["instance_id"] for r in records}
            if instances[0] not in owner_ids:
                conflicts.append({
                    "canonical_id": canonical, "title": items[0]["title"],
                    "type": "ownership_mismatch", "severity": "warning", "intentional": False,
                    "message_nl": f"Geregistreerde eigenaar is {', '.join(sorted(owner_ids))}, "
                                  f"maar de titel staat in {instances[0]}.",
                    "items": items, "ownership": records,
                })
    conflicts.sort(key=lambda c: ({"error": 0, "warning": 1, "info": 2}[c["severity"]],
                                  c["title"] or ""))
    return conflicts


def simulate_ownership(ctx: AppContext, body: dict[str, Any]) -> dict[str, Any]:
    kind = body.get("kind")
    if kind not in ("series", "movie"):
        raise ValueError("kind moet 'series' of 'movie' zijn")
    canonical = None
    if kind == "series" and body.get("tvdb_id"):
        canonical = f"tvdb:{int(body['tvdb_id'])}"
    elif kind == "movie" and body.get("tmdb_id"):
        canonical = f"tmdb:{int(body['tmdb_id'])}"
    elif body.get("imdb_id"):
        canonical = f"imdb:{body['imdb_id']}"
    if canonical is None:
        raise ValueError("geef tvdb_id (series), tmdb_id (film) of imdb_id op")
    app = "sonarr" if kind == "series" else "radarr"
    target_profile = str(body.get("target_profile") or "")
    edition = str(body.get("edition") or "")
    is_anime = body.get("is_anime")
    existing = []
    column, value = canonical.split(":", 1)
    col = {"tvdb": "tvdb_id", "tmdb": "tmdb_id", "imdb": "imdb_id"}[column]
    for row in ctx.db.fetch_dicts(
            f"SELECT l.instance_id, l.provider_id, l.title, l.quality_profile_id, "
            f"l.root_folder_path, i.media_type FROM lib_items l JOIN instances i "
            f"ON i.id=l.instance_id WHERE l.{col}=? AND l.kind=? AND l.removed_at IS NULL",
            (value if col == "imdb_id" else int(value), kind)):
        existing.append(row)
    records = ownership_records(ctx, canonical)
    candidates = [i for i in list_instances(ctx) if i.app == app]
    explanation: list[str] = []
    chosen = None
    reason = None
    match = [r for r in records if r["target_profile"] == target_profile
             and r["edition"] == edition]
    if match:
        chosen, reason = match[0]["instance_id"], "ownership_override"
        explanation.append(f"Expliciete ownership-registratie wijst naar {chosen}.")
    elif is_anime is None and existing:
        chosen, reason = existing[0]["instance_id"], "existing"
        explanation.append(f"Titel bestaat al in {chosen}; die instantie blijft eigenaar.")
    else:
        if is_anime is None:
            explanation.append("Anime-status onbekend: standaardroute gekozen. Bevestig de "
                               "route vóór toevoegen (Seerr herkent anime niet altijd).")
        wanted_type = {("sonarr", True): "anime", ("sonarr", False): "tv",
                       ("radarr", True): "anime_movie", ("radarr", False): "movie"}[
            (app, bool(is_anime))]
        typed = [i for i in candidates if i.media_type == wanted_type]
        pick = next((i for i in typed if not i.suspended), typed[0] if typed else None)
        if pick:
            chosen, reason = pick.id, "media_type"
            explanation.append(f"Mediatype '{wanted_type}' wordt beheerd door {pick.id}.")
    conflict = None
    others = [e for e in existing if e["instance_id"] != chosen]
    if chosen and others:
        allowed = any(r["allow_multi"] for r in records)
        conflict = {
            "type": "would_duplicate", "allowed_by_override": allowed,
            "instances": sorted({e["instance_id"] for e in others}),
            "message_nl": ("Toegestaan door ownership-override." if allowed else
                           "Titel bestaat al in een andere instantie: toevoegen zou een "
                           "dubbele taak opleveren."),
        }
    already = any(e["instance_id"] == chosen for e in existing)
    return {"canonical_id": canonical, "predicted_instance": chosen, "reason": reason,
            "already_present": already, "existing": existing, "ownership": records,
            "conflict": conflict, "explanation_nl": explanation}
