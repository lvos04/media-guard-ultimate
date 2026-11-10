"""Seerr-routecontrole (R4/H), mappingrapport, gecontroleerde cutover met rollback en
synchronisatie van aanvragen naar instantie-items.

Seerr wordt uitsluitend via zijn eigen ondersteunde API gewijzigd, alleen met expliciete
bevestiging, en de vorige waarden worden bewaard voor rollback (geheimen in de
secrets-store, niet in JSON)."""

from __future__ import annotations

from datetime import datetime
from typing import TYPE_CHECKING, Any
from urllib.parse import urlsplit

from mediaguard.arr.client import ArrError
from mediaguard.audit import audit
from mediaguard.db import dumps, loads, utcnow
from mediaguard.instances.registry import apikey_secret, arr_client, list_instances
from mediaguard.seerr.client import SeerrError, seerr_client

if TYPE_CHECKING:
    from mediaguard.context import AppContext

# standaardrollen (R4): reguliere series/films = standaardserver; anime = aparte server
DEFAULT_ROUTING: dict[str, dict[str, Any]] = {
    "sonarr-series": {"seerr_kind": "sonarr", "role": "default", "expected_is_default": True,
                      "expected_is_4k": False},
    "sonarr-anime": {"seerr_kind": "sonarr", "role": "anime", "expected_is_default": False,
                     "expected_is_4k": False},
    "radarr-films": {"seerr_kind": "radarr", "role": "default", "expected_is_default": True,
                     "expected_is_4k": False},
    "radarr-animefilms": {"seerr_kind": "radarr", "role": "anime", "expected_is_default": False,
                          "expected_is_4k": False},
}
ROLE_DEFAULTS = {"default": (True, False), "anime": (False, False), "4k": (True, True),
                 "4k-anime": (False, True), "manual": (False, False)}
REQUEST_STATUS_NL = {1: "Wacht op goedkeuring", 2: "Goedgekeurd", 3: "Afgewezen",
                     4: "Mislukt", 5: "Voltooid"}
# zoveel ontbrekende aanvragen per ronde nog bij Seerr navragen; de rest volgt de volgende ronde
MAX_GONE_CHECKS = 50
MEDIA_STATUS_NL = {1: "Onbekend", 2: "In afwachting", 3: "Wordt verwerkt",
                   4: "Gedeeltelijk beschikbaar", 5: "Beschikbaar",
                   6: "Verwijderd of geblokkeerd", 7: "Verwijderd"}


def routing_for(ctx: AppContext, instance_id: str, app: str) -> dict[str, Any] | None:
    row = ctx.db.fetch_one("SELECT * FROM instance_seerr_routing WHERE instance_id=?",
                           (instance_id,))
    if row is not None:
        data = dict(row)
        data["expected_is_default"] = bool(data["expected_is_default"])
        data["expected_is_4k"] = bool(data["expected_is_4k"])
        data["source"] = "configured"
        return data
    default = DEFAULT_ROUTING.get(instance_id)
    if default is None:
        return None
    return {"instance_id": instance_id, "seerr_server_id": None, "expected_profile_id": None,
            "expected_root_folder": None, **default, "source": "default"}


def _norm_url(url: str) -> str:
    parts = urlsplit(url.strip())
    scheme = (parts.scheme or "http").lower()
    port = parts.port or (443 if scheme == "https" else 80)
    path = parts.path.rstrip("/")
    return f"{scheme}://{(parts.hostname or '').lower()}:{port}{path}"


def server_url(server: dict[str, Any]) -> str:
    scheme = "https" if server.get("useSsl") else "http"
    base = str(server.get("baseUrl") or "")
    if base and not base.startswith("/"):
        base = "/" + base
    return _norm_url(f"{scheme}://{server.get('hostname')}:{server.get('port')}{base}")


def connection_fields(url: str) -> dict[str, Any]:
    parts = urlsplit(url)
    scheme = parts.scheme or "http"
    return {"hostname": parts.hostname, "port": parts.port or (443 if scheme == "https" else 80),
            "useSsl": scheme == "https", "baseUrl": parts.path.rstrip("/")}


_LOOPBACK = ("127.0.0.1", "localhost", "::1", "0.0.0.0", "")


def _expected_url(inst: dict[str, Any]) -> str:
    """Adres waarop Seerr (een andere app/container) deze instantie bereikt. Nooit het interne
    127.0.0.1-adres van het alles-in-één-image: dat is voor Seerr zijn eigen container."""
    import os

    from mediaguard.instances.registry import _public_from_host

    for url in (inst.get("public_url"), inst.get("url")):
        if url and (urlsplit(url).hostname or "") not in _LOOPBACK:
            return str(url).rstrip("/")
    lan = os.environ.get("MG_PUBLIC_HOST", "").strip()
    derived = _public_from_host(inst["id"], lan) if lan else None
    return (derived or inst.get("public_url") or inst["url"]).rstrip("/")


async def connection_test(client: Any, kind: str, values: dict[str, Any]) -> str | None:
    """Seerr's eigen verbindingstest (``POST settings/<kind>/test``) met precies deze waarden
    (host, poort, SSL, base-URL, API-sleutel). None = Seerr bereikt de instantie; anders de
    reden. Wordt gebruikt vóórdat MediaGuard Seerr-instellingen wegschrijft."""
    try:
        tested = await client.server_test(kind, values)
    except SeerrError as exc:
        return exc.message
    if isinstance(tested, dict) and "profiles" in tested:
        return None
    return "Seerr gaf een onverwacht antwoord op de verbindingstest"


def _mask_server(server: dict[str, Any] | None) -> dict[str, Any] | None:
    if server is None:
        return None
    out = dict(server)
    if out.get("apiKey"):
        out["apiKey"] = "********"
    return out


def _check(name: str, ok: bool | None, expected: Any, actual: Any, msg: str) -> dict:
    return {"check": name, "ok": ok, "expected": expected, "actual": actual, "message_nl": msg}


async def _instance_resources(ctx: AppContext, instance_id: str) -> dict[str, Any]:
    out: dict[str, Any] = {"profiles": None, "roots": None, "error": None}
    try:
        async with arr_client(ctx, instance_id) as client:
            out["profiles"] = {int(p["id"]) for p in await client.quality_profiles()}
            out["roots"] = {str(r.get("path", "")).rstrip("/") for r in
                            await client.root_folders()}
    except ArrError as exc:
        out["error"] = exc.message
    return out


async def route_report(ctx: AppContext, *, run_tests: bool) -> dict[str, Any]:
    """Controleert per instantie de Seerr-server (URL, API-sleutel, default/4K/anime,
    profiel, rootmap) en optioneel Seerr's eigen verbindingstest."""
    client = seerr_client(ctx)
    status = await client.status()
    servers = {"sonarr": await client.servers("sonarr"), "radarr": await client.servers("radarr")}
    results = []
    claimed: dict[tuple[str, int], list[str]] = {}
    for inst_obj in list_instances(ctx):
        inst = inst_obj.to_dict()
        routing = routing_for(ctx, inst["id"], inst["app"])
        if routing is None:
            results.append({"instance_id": inst["id"], "routing": None, "ok": None,
                            "checks": [], "message_nl": "Geen Seerr-routering voor deze "
                                                        "instantie ingesteld."})
            continue
        kind = routing["seerr_kind"]
        expected_url = _norm_url(_expected_url(inst))
        candidates = servers.get(kind, [])
        server = None
        if routing.get("seerr_server_id") is not None:
            server = next((s for s in candidates if int(s.get("id", -1)) ==
                           int(routing["seerr_server_id"])), None)
        if server is None:
            server = next((s for s in candidates if server_url(s) == expected_url), None)
        checks: list[dict[str, Any]] = []
        if server is None:
            checks.append(_check("server_found", False, expected_url, None,
                                 f"Geen {kind}-server in Seerr wijst naar {expected_url}."))
            results.append({"instance_id": inst["id"], "routing": routing, "server": None,
                            "ok": False, "checks": checks})
            continue
        claimed.setdefault((kind, int(server["id"])), []).append(inst["id"])
        checks.append(_check("server_found", True, expected_url, server.get("name"),
                             f"Seerr-server '{server.get('name')}' (id {server.get('id')})."))
        actual_url = server_url(server)
        checks.append(_check("url_matches", actual_url == expected_url, expected_url,
                             actual_url, "URL klopt." if actual_url == expected_url else
                             "Seerr wijst naar een andere host/poort/base-URL."))
        key = ctx.secrets.get(apikey_secret(inst["id"]))
        seerr_key = server.get("apiKey")
        if not seerr_key or set(str(seerr_key)) <= {"*"}:
            checks.append(_check("api_key_matches", None, "***", None,
                                 "Seerr toont de API-sleutel niet; gebruik de verbindingstest."))
        else:
            same = bool(key) and seerr_key == key
            checks.append(_check("api_key_matches", same, "***", "***",
                                 "API-sleutel komt overeen." if same else
                                 "API-sleutel in Seerr hoort niet bij deze instantie."))
        for field, label, exp in (("isDefault", "is_default", routing["expected_is_default"]),
                                  ("is4k", "is_4k", routing["expected_is_4k"])):
            actual = bool(server.get(field))
            checks.append(_check(label, actual == exp, exp, actual,
                                 f"{field} = {actual}" + ("" if actual == exp else
                                                          f", verwacht {exp}.")))
        res = await _instance_resources(ctx, inst["id"])
        profile_fields = ["activeProfileId"]
        if kind == "sonarr" and routing["role"] in ("anime", "4k-anime"):
            profile_fields.append("activeAnimeProfileId")
        for pf in profile_fields:
            pid = server.get(pf)
            if res["profiles"] is None or pid is None:
                checks.append(_check(f"profile:{pf}", None, None, pid,
                                     res["error"] or "Profiel niet ingesteld in Seerr."))
                continue
            ok = int(pid) in res["profiles"] and (routing.get("expected_profile_id") in (
                None, int(pid)) or pf != "activeProfileId")
            checks.append(_check(f"profile:{pf}", ok, routing.get("expected_profile_id") or
                                 sorted(res["profiles"]), pid,
                                 "Kwaliteitsprofiel bestaat in de instantie." if ok else
                                 "Kwaliteitsprofiel bestaat niet of wijkt af."))
        dir_fields = ["activeDirectory"]
        if kind == "sonarr" and routing["role"] in ("anime", "4k-anime"):
            dir_fields.append("activeAnimeDirectory")
        for df in dir_fields:
            directory = server.get(df)
            if res["roots"] is None or not directory:
                checks.append(_check(f"root:{df}", None, None, directory,
                                     res["error"] or "Rootmap niet ingesteld in Seerr."))
                continue
            d = str(directory).rstrip("/")
            exp_root = (routing.get("expected_root_folder") or "").rstrip("/")
            ok = d in res["roots"] and (not exp_root or df != "activeDirectory" or d == exp_root)
            checks.append(_check(f"root:{df}", ok, exp_root or sorted(res["roots"]), directory,
                                 "Rootmap bestaat in de instantie." if ok else
                                 "Rootmap bestaat niet in de instantie of wijkt af."))
        if run_tests:
            try:
                tested = await client.server_test(kind, server)
                ok = isinstance(tested, dict) and "profiles" in tested
                checks.append(_check("seerr_connection_test", ok, "profiles", None,
                                     "Seerr kan de instantie bereiken." if ok else
                                     "Seerr-test gaf een onverwacht antwoord."))
            except SeerrError as exc:
                checks.append(_check("seerr_connection_test", False, "ok", exc.status,
                                     f"Seerr kan de instantie niet bereiken: {exc.message}"))
        if routing["role"] in ("anime", "4k-anime") and not server.get("isDefault"):
            checks.append(_check("anime_selection", None, None, None,
                                 "Seerr kiest deze server alleen bij expliciete serverkeuze of "
                                 "override; bevestig de route vóór toevoegen."))
        ok = all(c["ok"] is not False for c in checks)
        results.append({"instance_id": inst["id"], "routing": routing,
                        "server": _mask_server(server), "ok": ok, "checks": checks})
    for (kind, sid), owners in claimed.items():
        if len(owners) > 1:
            for r in results:
                if r["instance_id"] in owners:
                    r["ok"] = False
                    r["checks"].append(_check("unique_server", False, 1, len(owners),
                                              f"Seerr-server {kind}#{sid} is aan meerdere "
                                              f"instanties gekoppeld: {', '.join(owners)}."))
    unmapped = []
    for kind, items in servers.items():
        for s in items:
            if (kind, int(s.get("id", -1))) not in claimed:
                unmapped.append({"kind": kind, "server": _mask_server(s), "url": server_url(s),
                                 "message_nl": "Seerr-server hoort bij geen MediaGuard-instantie "
                                               "(mogelijk het oude systeem)."})
    return {"seerr_version": status.get("version"), "tested": run_tests,
            "ok": all(r["ok"] is not False for r in results if r["routing"]) and not unmapped,
            "instances": results, "unmapped_servers": unmapped, "checked_at": utcnow()}


# -- cutover -----------------------------------------------------------------------------------
def _cutover_row(ctx: AppContext, instance_id: str) -> dict[str, Any] | None:
    row = ctx.db.fetch_one("SELECT * FROM instance_cutover_state WHERE instance_id=?",
                           (instance_id,))
    return dict(row) if row else None


async def seerr_cutover(ctx: AppContext, instance_id: str, actor: str, *, confirm: bool,
                        server_id: int | None = None, create_if_missing: bool = False,
                        overrides: dict[str, Any] | None = None) -> dict[str, Any]:
    inst = next((i.to_dict() for i in list_instances(ctx) if i.id == instance_id), None)
    if inst is None:
        raise KeyError(instance_id)
    routing = routing_for(ctx, instance_id, inst["app"]) or {
        "seerr_kind": inst["app"], "role": "manual", "expected_is_default": False,
        "expected_is_4k": False, "seerr_server_id": None}
    kind = routing["seerr_kind"]
    client = seerr_client(ctx)
    servers = await client.servers(kind)
    sid = server_id if server_id is not None else routing.get("seerr_server_id")
    server = next((s for s in servers if sid is not None and int(s.get("id", -1)) == int(sid)),
                  None)
    if server is None:
        target = _norm_url(_expected_url(inst))
        server = next((s for s in servers if server_url(s) == target), None)
    api_key = ctx.secrets.get(apikey_secret(instance_id))
    if not api_key:
        raise ValueError(f"Geen API-sleutel voor {instance_id}")
    desired = {**connection_fields(_expected_url(inst)), "apiKey": api_key,
               "isDefault": routing["expected_is_default"], "is4k": routing["expected_is_4k"]}
    for key, value in (overrides or {}).items():
        if value is not None:
            desired[key] = value
    if server is None and not create_if_missing:
        raise ValueError(f"Geen {kind}-server in Seerr gevonden voor {instance_id}; geef "
                         "server_id op of kies 'create_if_missing'")
    current = dict(server or {})
    new = {**current, **desired}
    # Seerr vereist een profielnaam bij het profiel-ID; bij nieuw of gewijzigd profiel ophalen
    if new.get("activeProfileId") is not None and (
            server is None or "activeProfileId" in (overrides or {})
            or not new.get("activeProfileName")):
        name = await _profile_name(ctx, instance_id, int(new["activeProfileId"]))
        if name:
            new["activeProfileName"] = name
            desired["activeProfileName"] = name
    if server is None:
        new.setdefault("name", inst["name"])
        if kind == "radarr":
            new.setdefault("minimumAvailability", "released")
        else:
            new.setdefault("enableSeasonFolders", True)
        missing = [f for f in ("activeProfileId", "activeProfileName", "activeDirectory")
                   if new.get(f) in (None, "")]
        if missing:
            raise ValueError(f"Nieuwe Seerr-server vereist: {', '.join(missing)}")
    diff = {k: {"old": ("***" if k == "apiKey" and current.get(k) else current.get(k)),
                "new": ("***" if k == "apiKey" else v)}
            for k, v in desired.items() if current.get(k) != v}
    if not confirm:
        return {"requires_confirmation": True, "instance_id": instance_id, "kind": kind,
                "server_id": current.get("id"), "action": "update" if server else "create",
                "diff": diff, "message_nl": "Bevestig om Seerr bij te werken. De huidige "
                "waarden worden bewaard voor rollback."}
    existing = _cutover_row(ctx, instance_id) or {}
    now = utcnow()
    if server is not None:
        if not existing.get("seerr_previous_json"):
            prev = dict(current)
            if prev.get("apiKey"):
                ctx.secrets.set(f"seerr:rollback:{instance_id}:apikey", str(prev["apiKey"]),
                                actor)
            prev["apiKey"] = None
            previous_json = dumps({"action": "update", "server": prev})
        else:
            previous_json = existing["seerr_previous_json"]
        saved = await client.server_update(kind, int(current["id"]), new)
        saved_id = int(current["id"])
    else:
        saved = await client.server_create(kind, new)
        saved_id = int(saved.get("id") or 0)
        previous_json = existing.get("seerr_previous_json") or dumps(
            {"action": "create", "server_id": saved_id})
    applied = {k: v for k, v in desired.items() if k != "apiKey"}
    ctx.db.execute(
        "INSERT INTO instance_cutover_state(instance_id,phase,seerr_kind,seerr_server_id,"
        "seerr_previous_json,seerr_applied_json,seerr_applied_at,updated_at,updated_by) "
        "VALUES (?,?,?,?,?,?,?,?,?) ON CONFLICT(instance_id) DO UPDATE SET "
        "phase=excluded.phase, seerr_kind=excluded.seerr_kind, "
        "seerr_server_id=excluded.seerr_server_id, "
        "seerr_previous_json=excluded.seerr_previous_json, "
        "seerr_applied_json=excluded.seerr_applied_json, "
        "seerr_applied_at=excluded.seerr_applied_at, updated_at=excluded.updated_at, "
        "updated_by=excluded.updated_by",
        (instance_id, "seerr_cutover", kind, saved_id, previous_json, dumps(applied), now, now,
         actor))
    if routing.get("source") != "configured" or routing.get("seerr_server_id") != saved_id:
        set_routing(ctx, instance_id, {**routing, "seerr_server_id": saved_id}, actor)
    audit(ctx.db, actor=actor, action="seerr.cutover", target_type="seerr_server",
          target_id=f"{kind}:{saved_id}", instance_id=instance_id,
          old_value={k: d["old"] for k, d in diff.items()},
          new_value={k: d["new"] for k, d in diff.items()},
          undo_hint=f"POST /api/v1/seerr/cutover/rollback (instance_id={instance_id})")
    return {"applied": True, "instance_id": instance_id, "kind": kind, "server_id": saved_id,
            "diff": diff, "server": _mask_server(saved if isinstance(saved, dict) else None)}


async def seerr_rollback(ctx: AppContext, instance_id: str, actor: str,
                         confirm: bool) -> dict[str, Any]:
    row = _cutover_row(ctx, instance_id)
    if row is None or not row.get("seerr_previous_json"):
        raise ValueError("Er is geen Seerr-cutover om terug te draaien")
    previous = loads(row["seerr_previous_json"], {})
    kind = row["seerr_kind"]
    if not confirm:
        return {"requires_confirmation": True, "action": previous.get("action"),
                "server_id": row["seerr_server_id"],
                "message_nl": "Bevestig om de Seerr-server terug te zetten naar de waarden van "
                              "vóór de cutover."}
    client = seerr_client(ctx)
    if previous.get("action") == "create":
        await client.server_delete(kind, int(previous["server_id"]))
        result = {"deleted_server": previous["server_id"]}
    else:
        server = dict(previous["server"])
        key_name = f"seerr:rollback:{instance_id}:apikey"
        server["apiKey"] = ctx.secrets.get(key_name) or ""
        await client.server_update(kind, int(server["id"]), server)
        ctx.secrets.delete(key_name)
        result = {"restored_server": server["id"]}
    ctx.db.execute("UPDATE instance_cutover_state SET phase='seerr_rolled_back', "
                   "seerr_previous_json=NULL, seerr_rolled_back_at=?, updated_at=?, updated_by=?"
                   " WHERE instance_id=?", (utcnow(), utcnow(), actor, instance_id))
    audit(ctx.db, actor=actor, action="seerr.rollback", target_type="seerr_server",
          target_id=f"{kind}:{row['seerr_server_id']}", instance_id=instance_id,
          new_value=result)
    return {"rolled_back": True, **result}


def set_routing(ctx: AppContext, instance_id: str, data: dict[str, Any], actor: str) -> dict:
    role = data.get("role") or "default"
    default_default, default_4k = ROLE_DEFAULTS.get(role, (False, False))
    is_default = data.get("expected_is_default")
    is_4k = data.get("expected_is_4k")
    ctx.db.execute(
        "INSERT INTO instance_seerr_routing(instance_id,seerr_kind,seerr_server_id,role,"
        "expected_is_default,expected_is_4k,expected_profile_id,expected_root_folder,notes,"
        "updated_at,updated_by) VALUES (?,?,?,?,?,?,?,?,?,?,?) ON CONFLICT(instance_id) DO "
        "UPDATE SET seerr_kind=excluded.seerr_kind, seerr_server_id=excluded.seerr_server_id, "
        "role=excluded.role, expected_is_default=excluded.expected_is_default, "
        "expected_is_4k=excluded.expected_is_4k, "
        "expected_profile_id=excluded.expected_profile_id, "
        "expected_root_folder=excluded.expected_root_folder, notes=excluded.notes, "
        "updated_at=excluded.updated_at, updated_by=excluded.updated_by",
        (instance_id, data["seerr_kind"], data.get("seerr_server_id"), role,
         int(default_default if is_default is None else bool(is_default)),
         int(default_4k if is_4k is None else bool(is_4k)), data.get("expected_profile_id"),
         data.get("expected_root_folder"), data.get("notes"), utcnow(), actor))
    row = ctx.db.fetch_one("SELECT * FROM instance_seerr_routing WHERE instance_id=?",
                           (instance_id,))
    return dict(row) if row else {}


# -- aanvragen ----------------------------------------------------------------------------------
def _ts(value: Any) -> str | None:
    if not value:
        return None
    try:
        return datetime.fromisoformat(str(value).replace("Z", "+00:00")).isoformat(
            timespec="seconds")
    except ValueError:
        return None


async def sync_requests(ctx: AppContext) -> dict[str, Any]:
    client = seerr_client(ctx)
    requests = await client.requests()
    routes = {(r["seerr_kind"], int(r["seerr_server_id"])): r["instance_id"] for r in
              ctx.db.fetch_dicts("SELECT * FROM instance_seerr_routing WHERE seerr_server_id "
                                 "IS NOT NULL")}
    counts = {"total": 0, "linked": 0, "unlinked": 0, "ambiguous": 0, "not_in_instance": 0}
    now = utcnow()
    seen: list[int] = []
    for req in requests:
        media = req.get("media") or {}
        media_type = str(req.get("type") or media.get("mediaType") or "")
        is4k = bool(req.get("is4k"))
        kind = "sonarr" if media_type == "tv" else "radarr"
        lib_kind = "series" if media_type == "tv" else "movie"
        tmdb = media.get("tmdbId")
        tvdb = media.get("tvdbId")
        server_id = req.get("serverId")
        ext = media.get("externalServiceId4k" if is4k else "externalServiceId")
        instance_id = routes.get((kind, int(server_id))) if server_id is not None else None
        provider_id = int(ext) if ext else None
        lib = None
        link = "unlinked"
        if instance_id and provider_id:
            lib = ctx.db.fetch_one("SELECT id, title FROM lib_items WHERE instance_id=? AND "
                                   "kind=? AND provider_id=?", (instance_id, lib_kind,
                                                                provider_id))
            link = "linked" if lib else "not_in_instance"
        if lib is None:
            col, val = ("tvdb_id", tvdb) if lib_kind == "series" and tvdb else ("tmdb_id", tmdb)
            matches = ctx.db.fetch_dicts(
                f"SELECT id, title, instance_id, provider_id FROM lib_items WHERE kind=? AND "
                f"{col}=? AND removed_at IS NULL", (lib_kind, val)) if val else []
            if instance_id:
                matches = [m for m in matches if m["instance_id"] == instance_id] or matches
            if len(matches) == 1:
                lib = matches[0]
                instance_id, provider_id = matches[0]["instance_id"], matches[0]["provider_id"]
                link = "linked"
            elif len(matches) > 1:
                link = "ambiguous"
        counts["total"] += 1
        counts[link] += 1
        media_status = media.get("status4k" if is4k else "status")
        existing = ctx.db.fetch_one("SELECT available_at FROM seerr_requests WHERE "
                                    "seerr_request_id=?", (int(req["id"]),))
        available_at = existing["available_at"] if existing else None
        if media_status == 5 and not available_at:
            available_at = _ts(media.get("mediaAddedAt")) or now
        requested_by = (req.get("requestedBy") or {}).get("displayName") or \
            (req.get("requestedBy") or {}).get("email")
        profile = req.get("profileId")
        ctx.db.execute(
            "INSERT INTO seerr_requests(seerr_request_id,media_type,tmdb_id,tvdb_id,"
            "request_status,media_status,is4k,server_id,instance_id,provider_id,lib_item_id,"
            "title,requested_by,seasons_json,created_at,updated_at,synced_at,available_at,"
            "link_status,profile_id) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?) "
            "ON CONFLICT(seerr_request_id) DO UPDATE SET request_status=excluded.request_status,"
            " media_status=excluded.media_status, server_id=excluded.server_id, "
            "instance_id=excluded.instance_id, provider_id=excluded.provider_id, "
            "lib_item_id=excluded.lib_item_id, title=excluded.title, "
            "seasons_json=excluded.seasons_json, updated_at=excluded.updated_at, "
            "synced_at=excluded.synced_at, available_at=excluded.available_at, "
            "link_status=excluded.link_status, profile_id=excluded.profile_id, deleted_at=NULL",
            (int(req["id"]), media_type, tmdb, tvdb, req.get("status"), media_status, int(is4k),
             server_id, instance_id, provider_id, lib["id"] if lib else None,
             lib["title"] if lib else None, requested_by,
             dumps([{"season": s.get("seasonNumber"), "status": s.get("status")}
                    for s in req.get("seasons") or []]),
             _ts(req.get("createdAt")), _ts(req.get("updatedAt")), now, available_at, link,
             int(profile) if isinstance(profile, int) else None))
        seen.append(int(req["id"]))
    # verbeterlijst 11: een in Seerr verwijderde aanvraag mag niet eeuwig 'actief' blijven.
    # Alleen bij een volledige lijst (en nooit op een lege lijst: dan eerder een storing).
    # Een verwijdering tíjdens het ophalen kan een levende aanvraag overslaan; daarom per
    # ontbrekende aanvraag nog bij Seerr navragen en alleen bij 'bestaat niet' (404) markeren
    if seen and getattr(client, "requests_complete", False):
        marks = ",".join("?" * len(seen))
        missing = [int(r["seerr_request_id"]) for r in ctx.db.fetch_dicts(
            f"SELECT seerr_request_id FROM seerr_requests WHERE deleted_at IS NULL AND "  # noqa: S608
            f"seerr_request_id NOT IN ({marks}) ORDER BY seerr_request_id LIMIT ?",
            (*seen, MAX_GONE_CHECKS))]
        gone = 0
        for rid in missing:
            if await client.request_gone(rid) is True:
                gone += ctx.db.execute("UPDATE seerr_requests SET deleted_at=? WHERE "
                                       "seerr_request_id=? AND deleted_at IS NULL", (now, rid))
        if gone:
            counts["deleted"] = gone
    return counts


def request_summary(ctx: AppContext) -> dict[str, Any]:
    rows = ctx.db.fetch_dicts("SELECT created_at, available_at FROM seerr_requests WHERE "
                              "available_at IS NOT NULL AND created_at IS NOT NULL")
    hours = []
    for r in rows:
        try:
            delta = datetime.fromisoformat(r["available_at"]) - datetime.fromisoformat(
                r["created_at"])
            hours.append(max(0.0, delta.total_seconds() / 3600))
        except ValueError:
            continue
    hours.sort()
    links = {r["link_status"]: int(r["n"]) for r in ctx.db.fetch_all(
        "SELECT link_status, COUNT(*) AS n FROM seerr_requests GROUP BY link_status")}
    return {"links": links, "available_count": len(hours),
            "lead_time_hours_median": hours[len(hours) // 2] if hours else None,
            "lead_time_hours_avg": round(sum(hours) / len(hours), 2) if hours else None}


async def _profile_name(ctx: AppContext, instance_id: str, profile_id: int) -> str | None:
    """Naam van het kwaliteitsprofiel op de fork-instantie (Seerr toont/eist die)."""
    try:
        async with arr_client(ctx, instance_id) as client:
            profiles = await client.quality_profiles()
    except Exception:  # noqa: BLE001 - naam is aanvullend; fout = laat Seerr valideren
        return None
    for p in profiles or []:
        if int(p.get("id", -1)) == profile_id:
            return str(p.get("name"))
    return None
