"""Seerr inrichten volgens docs/SEERR_INSTELLEN.md: vier servers (Sonarr Series = standaard
met ingevulde anime-velden, Sonarr Anime, Radarr Films = standaard, Radarr Animefilms),
scannen aan, seizoensmappen aan, minimale beschikbaarheid 'released'.

Alleen via Seerr's eigen API, alleen na expliciete bevestiging, nooit tijdens een actieve
schaduwfase van een migratie. Seerr-servers die naar de OUDE installatie wijzen worden
nooit gewijzigd of verwijderd (dat is de taak van de cutover); MediaGuard meldt ze alleen.
Seerr 3.5.1 weigert 'id' en null-waarden in bodies: ``SeerrClient._writable`` verwijdert
die; desired-bodies bevatten daarom nooit None.

Elke wijziging (ook het automatisch herstel na een afwijking) gaat eerst door Seerr's eigen
verbindingstest met de nieuwe waarden. Mislukt die, dan blijven de huidige Seerr-instellingen
staan en komt er een regel in 'Wacht op jou'."""

from __future__ import annotations

import logging
from typing import TYPE_CHECKING, Any

from mediaguard.arr.client import ArrError
from mediaguard.audit import audit
from mediaguard.db import utcnow
from mediaguard.instances.registry import apikey_secret, arr_client, list_instances
from mediaguard.provisioning import store
from mediaguard.provisioning import templates as T
from mediaguard.provisioning.engine import Change, diff_line
from mediaguard.provisioning.store import ProvisioningConfig
from mediaguard.seerr.client import SeerrError, seerr_client
from mediaguard.seerr.service import (
    _expected_url,
    _norm_url,
    connection_fields,
    connection_test,
    server_url,
    set_routing,
)

if TYPE_CHECKING:
    from mediaguard.context import AppContext

LOGGER = logging.getLogger(__name__)
COMPARE = ("name", "hostname", "port", "useSsl", "baseUrl", "activeProfileId",
           "activeDirectory", "is4k", "isDefault", "syncEnabled", "preventSearch",
           "enableSeasonFolders", "seriesType", "animeSeriesType", "activeAnimeProfileId",
           "activeAnimeDirectory", "minimumAvailability", "externalUrl")
LABELS = {"isDefault": "Standaardserver", "syncEnabled": "Scannen", "activeProfileId":
          "Kwaliteitsprofiel", "activeDirectory": "Hoofdmap", "activeAnimeProfileId":
          "Animeprofiel", "activeAnimeDirectory": "Anime-hoofdmap", "hostname": "Host",
          "port": "Poort", "apiKey": "API-sleutel", "minimumAvailability":
          "Minimale beschikbaarheid", "enableSeasonFolders": "Seizoensmappen"}


def _change(action: str, key: str, title: str, iid: str = "", **kw: Any) -> Change:
    return Change("seerr", action, key, title, instance_id=iid, **kw)


IGNORED_KEY = "provisioning:seerr_ignored_fields"
REPEAT_KEY = "provisioning:seerr_repeat"
REPEAT_LIMIT = 2  # zelfde verschil na zoveel herstelpogingen = Seerr bewaart het anders
TEST_FAILED_KEY = "provisioning:seerr_test_failed"


def note_test_failure(ctx: AppContext, inst: dict[str, Any], values: dict[str, Any],
                      problem: str) -> None:
    data = dict(ctx.state_get(TEST_FAILED_KEY, {}) or {})
    scheme = "https" if values.get("useSsl") else "http"
    data[inst["id"]] = {"name": inst["name"], "error": problem[:300], "at": utcnow(),
                        "url": f"{scheme}://{values.get('hostname')}:{values.get('port')}"
                               f"{values.get('baseUrl') or ''}"}
    ctx.state_set(TEST_FAILED_KEY, data)


def clear_test_failures(ctx: AppContext, keep: set[str] | None = None,
                        only: str | None = None) -> None:
    """Testfout van ``only`` wissen, of van alle instanties buiten ``keep``."""
    data = dict(ctx.state_get(TEST_FAILED_KEY, {}) or {})
    left = {k: v for k, v in data.items()
            if (only is not None and k != only) or (only is None and k in (keep or set()))}
    if left != data:
        ctx.state_set(TEST_FAILED_KEY, left)


def attention_items(ctx: AppContext) -> list[dict[str, Any]]:
    """'Wacht op jou': Seerr kon een instantie niet bereiken met de nieuwe instellingen."""
    data = ctx.state_get(TEST_FAILED_KEY, {}) or {}
    if not data:
        return []
    rows = sorted(data.values(), key=lambda v: str(v.get("name") or ""))
    names = ", ".join(str(v.get("name")) for v in rows)
    first = rows[0]
    error = str(first.get("error") or "").strip()
    detail = (f"Seerr kon {names} niet bereiken met de nieuwe instellingen. MediaGuard liet de "
              "oude instellingen in Seerr staan. Controleer of Seerr het adres "
              f"{first.get('url')} kan bereiken en of de API-sleutel klopt."
              + (f" Seerr meldt: {error}" if error else ""))
    return [{"title_nl": "Seerr-koppeling niet bijgewerkt", "detail_nl": detail,
             "count": len(rows), "error_nl": str(first.get("error") or "")}]


async def _tested(ctx: AppContext, client: Any, inst: dict[str, Any],
                  values: dict[str, Any]) -> None:
    """Seerr's eigen verbindingstest met de NIEUWE waarden, vóór het wegschrijven. Mislukt
    hij, dan blijft de huidige Seerr-instelling staan (SeerrError) en volgt 'Wacht op jou'."""
    problem = await connection_test(client, inst["app"], values)
    if problem is None:
        return
    note_test_failure(ctx, inst, values, problem)
    LOGGER.warning("Seerr-test voor %s mislukt; instellingen niet gewijzigd: %s",
                   inst["id"], problem)
    raise SeerrError(f"Seerr kan {inst['name']} niet bereiken met de nieuwe instellingen; "
                     f"de huidige instellingen blijven staan ({problem})",
                     code="SEERR_TEST_FAILED")


def _norm(key: str, value: Any) -> Any:
    """Seerr bewaart sommige velden anders (slash, hoofdletters, '' vs None, '1' vs 1)."""
    if value is None or value == "":
        return None
    if key in ("activeDirectory", "activeAnimeDirectory", "baseUrl"):
        return str(value).rstrip("/") or "/"
    if key == "externalUrl":
        return str(value).rstrip("/").lower()
    if key == "hostname":
        return str(value).lower()
    if key in ("port", "activeProfileId", "activeAnimeProfileId"):
        try:
            return int(value)
        except (TypeError, ValueError):
            return value
    return value


def same(key: str, current: Any, desired: Any) -> bool:
    return bool(_norm(key, current) == _norm(key, desired))


def note_repeats(ctx: AppContext, remaining: list[dict[str, Any]]) -> list[str]:
    """Velden die na herhaald herstel nog steeds afwijken voortaan negeren (S10): MediaGuard
    zette ze al goed, Seerr geeft ze alleen anders terug. Geeft de nieuw genegeerde velden."""
    repeats = dict(ctx.state_get(REPEAT_KEY, {}) or {})
    ignored = {k: list(v) for k, v in (ctx.state_get(IGNORED_KEY, {}) or {}).items()}
    newly: list[str] = []
    seen = set()
    for c in remaining:
        iid = str(c.get("instance_id") or "")
        for d in c.get("diff") or []:
            key = f"{iid}|{d.get('field')}|{d.get('old')}|{d.get('new')}"
            seen.add(key)
            repeats[key] = int(repeats.get(key) or 0) + 1
            if repeats[key] >= REPEAT_LIMIT and d.get("field") not in ignored.get(iid, []):
                ignored.setdefault(iid, []).append(str(d.get("field")))
                newly.append(f"{iid}: {d.get('field')}")
    ctx.state_set(REPEAT_KEY, {k: v for k, v in repeats.items() if k in seen})
    ctx.state_set(IGNORED_KEY, ignored)
    return newly


def role_of(media_type: str) -> str:
    return "anime" if media_type in T.ANIME_TYPES else "default"


async def _resources(ctx: AppContext, iid: str) -> tuple[dict[str, int], set[str]]:
    async with arr_client(ctx, iid) as client:
        profiles = {str(p["name"]): int(p["id"]) for p in await client.quality_profiles()}
        roots = {str(r.get("path", "")).rstrip("/") for r in await client.root_folders()}
    return profiles, roots


def pick_root(roots: set[str], media_type: str) -> str | None:
    """Beste bestaande hoofdmap: bij anime een map met 'anime' in de naam, anders een zonder."""
    if not roots:
        return None
    anime = media_type in T.ANIME_TYPES
    ranked = sorted(roots, key=lambda r: (("anime" in r.lower()) != anime, len(r), r))
    return ranked[0]


async def desired_server(ctx: AppContext, cfg: ProvisioningConfig, inst: dict[str, Any],
                         ) -> tuple[dict[str, Any] | None, str | None]:
    iid = inst["id"]
    try:
        profiles, roots = await _resources(ctx, iid)
    except ArrError as exc:
        return None, f"{inst['name']} niet bereikbaar: {exc.message}"
    tpls = T.templates_for(inst["media_type"])
    main = T.owned_name(T.QUALITY_TEMPLATES[tpls[0]]["name"]) if tpls else None
    if main not in profiles:
        return None, (f"Profiel '{main}' bestaat nog niet in {inst['name']}: richt eerst de "
                      "kwaliteitsprofielen in.")
    root = store.root_folder_for(ctx, iid, inst["media_type"], cfg)
    if root not in roots:
        # gemigreerde instantie met eigen mappen: neem de map die de instantie al gebruikt
        root = pick_root(roots, inst["media_type"]) or root
    if root not in roots:
        return None, f"Mediamap {root} bestaat nog niet in {inst['name']}: richt die eerst in."
    url = _expected_url(inst)
    key = ctx.secrets.get(apikey_secret(iid))
    if not key:
        return None, f"API-sleutel van {inst['name']} ontbreekt"
    role = role_of(inst["media_type"])
    body: dict[str, Any] = {
        "name": inst["name"], **connection_fields(url), "apiKey": key,
        "activeProfileId": profiles[main], "activeProfileName": main,
        "activeDirectory": root, "is4k": False, "isDefault": role == "default",
        "syncEnabled": bool(cfg.seerr.scan), "preventSearch": False, "tags": [],
        "externalUrl": (inst.get("public_url") or inst["url"]).rstrip("/"),
    }
    if inst["app"] == "sonarr":
        anime_name = T.owned_name(T.QUALITY_TEMPLATES["anime"]["name"])
        anime_id = profiles.get(anime_name, profiles[main])
        body.update(enableSeasonFolders=True, seriesType="anime" if role == "anime"
                    else "standard", animeSeriesType="anime", activeAnimeProfileId=anime_id,
                    activeAnimeProfileName=anime_name if anime_name in profiles else main,
                    activeAnimeDirectory=root, animeTags=[])
    else:
        body["minimumAvailability"] = "released"
    return body, None


async def plan_seerr(ctx: AppContext, cfg: ProvisioningConfig,  # noqa: C901
                     actor: str, *, record_routes: bool = False) -> list[Change]:
    if not cfg.seerr.enabled:
        clear_test_failures(ctx)  # Seerr-inrichting uit: geen open testfout laten staan
        return []
    try:
        client = seerr_client(ctx)
    except SeerrError:
        return [_change("info", "seerr:not_configured", "Seerr is nog niet gekoppeld")]
    shadow = store.active_shadow_phase(ctx)
    if shadow:
        return [_change("blocked", "seerr:shadow", "Seerr wordt nu niet aangepast",
                        message_nl="Er loopt een migratie in schaduwfase ("
                        f"{', '.join(shadow)}); je oude installatie is nog leidend. Seerr "
                        "wordt bij de cutover omgezet.", auto=False)]
    try:
        servers = {"sonarr": await client.servers("sonarr"),
                   "radarr": await client.servers("radarr")}
    except SeerrError as exc:
        return [_change("error", "seerr", f"Seerr lezen mislukt: {exc.message}",
                        message_nl=exc.message)]
    insts = [i.to_dict() for i in list_instances(ctx)]
    ours = {_norm_url(_expected_url(i)): i["id"] for i in insts}
    out: list[Change] = []
    claimed: set[tuple[str, int]] = set()
    for inst in insts:
        kind, iid = inst["app"], inst["id"]
        if "seerr" in cfg.instance(iid).disabled_sections:
            continue
        expected = _norm_url(_expected_url(inst))
        server = next((s for s in servers[kind] if server_url(s) == expected), None)
        if server is not None:
            claimed.add((kind, int(server["id"])))
        desired, problem = await desired_server(ctx, cfg, inst)
        if desired is None:
            out.append(_change("blocked", iid, f"Seerr-server '{inst['name']}'", iid,
                               message_nl=problem or "", auto=False))
            continue
        old_default = next((s for s in servers[kind] if s.get("isDefault") and not s.get("is4k")
                            and server_url(s) not in ours), None)
        if desired["isDefault"] and old_default is not None:
            desired["isDefault"] = False
            out.append(_change("info", f"{iid}:default", f"'{old_default.get('name')}' (oude "
                               "installatie) blijft voorlopig standaardserver; de cutover "
                               f"maakt '{inst['name']}' standaard.", iid))
        if server is None:
            async def create(desired: Any = desired, inst: Any = inst) -> None:
                await _tested(ctx, client, inst, desired)
                rec = await client.server_create(inst["app"], desired)
                _route(ctx, inst, int(rec.get("id") or 0), desired, actor)
                clear_test_failures(ctx, only=inst["id"])
            out.append(_change("create", iid, f"Seerr-server '{inst['name']}' aanmaken"
                               f"{' (standaard)' if desired['isDefault'] else ''}", iid,
                               diff=[diff_line(k, LABELS.get(k, k), None, desired.get(k))
                                     for k in ("hostname", "port", "isDefault",
                                               "activeProfileName", "activeDirectory",
                                               "syncEnabled")], auto=False, run=create))
            continue
        ignored = set((ctx.state_get(IGNORED_KEY, {}) or {}).get(iid) or [])
        diffs = [diff_line(k, LABELS.get(k, k), server.get(k), desired[k]) for k in COMPARE
                 if k in desired and k not in ignored and not same(k, server.get(k), desired[k])]
        if server.get("apiKey") and set(str(server["apiKey"])) != {"*"} and \
                server["apiKey"] != desired["apiKey"]:
            diffs.append(diff_line("apiKey", "API-sleutel", "***", "***"))
        if diffs:
            async def update(desired: Any = desired, inst: Any = inst,
                             server: Any = server) -> None:
                body = {**server, **desired}
                await _tested(ctx, client, inst, body)
                await client.server_update(inst["app"], int(server["id"]), body)
                _route(ctx, inst, int(server["id"]), desired, actor)
                clear_test_failures(ctx, only=inst["id"])
            fields = ", ".join(LABELS.get(d["field"], d["field"]) for d in diffs)
            out.append(_change("update", iid, f"Seerr-server '{server.get('name')}' bijwerken "
                               f"({fields})", iid, diff=diffs, auto=True, run=update))
        elif record_routes:
            _route(ctx, inst, int(server["id"]), desired, actor)
    for kind, items in servers.items():
        for s in items:
            if (kind, int(s.get("id", -1))) not in claimed:
                out.append(_change("info", f"other:{kind}:{s.get('id')}",
                                   f"Seerr-server '{s.get('name')}' ({server_url(s)}) hoort "
                                   "niet bij MediaGuard (oude installatie?): niet aangepast. "
                                   "De cutover in de migratiewizard zet die om.", owned=False))
    # niets meer bij te werken (ook handmatig opgelost): testfout uit 'Wacht op jou'
    clear_test_failures(ctx, keep={c.instance_id for c in out
                                   if c.action in ("create", "update")})
    return out


def _route(ctx: AppContext, inst: dict[str, Any], server_id: int, desired: dict[str, Any],
           actor: str) -> None:
    set_routing(ctx, inst["id"], {
        "seerr_kind": inst["app"], "seerr_server_id": server_id,
        "role": role_of(inst["media_type"]), "expected_is_default": desired["isDefault"],
        "expected_is_4k": False, "expected_profile_id": desired["activeProfileId"],
        "expected_root_folder": desired["activeDirectory"]}, actor)


async def apply_seerr(ctx: AppContext, actor: str, confirm: bool) -> dict[str, Any]:
    cfg = store.load(ctx)
    changes = await plan_seerr(ctx, cfg, actor)
    pending = [c for c in changes if c.pending]
    view = [c.view() for c in changes]
    if not confirm:
        return {"requires_confirmation": bool(pending), "changes": view,
                "message_nl": "Bevestig om Seerr in te richten. Servers van je oude "
                              "installatie worden niet aangeraakt."}
    applied, failed = [], []
    ctx.state_set("provisioning:seerr_applied", True)
    for c in pending:
        try:
            assert c.run is not None
            await c.run()
            applied.append(c.view())
            audit(ctx.db, actor=actor, action=f"provisioning.seerr.{c.action}",
                  target_type="seerr_server", target_id=c.key, instance_id=c.instance_id,
                  new_value={d["field"]: d["new"] for d in c.diff},
                  undo_hint="Seerr → Instellingen → Services")
        except (SeerrError, ArrError) as exc:
            failed.append({**c.view(), "error": exc.message})
    after = await plan_seerr(ctx, cfg, actor, record_routes=True)
    remaining = [c.view() for c in after if c.pending]
    if applied:
        # alleen instanties die echt geschreven zijn: een afgebroken test is geen 'Seerr
        # bewaart het anders' (anders zou het veld voorgoed genegeerd worden)
        written = {str(a.get("instance_id") or "") for a in applied}
        newly = note_repeats(ctx, [r for r in remaining if r.get("action") == "update"
                                   and str(r.get("instance_id") or "") in written])
        if newly:
            LOGGER.info("Seerr bewaart %s anders dan verstuurd; die velden worden niet meer "
                        "als afwijking gemeld", ", ".join(newly))
            after = await plan_seerr(ctx, cfg, actor)
            remaining = [c.view() for c in after if c.pending]
    status = "ok" if not remaining and not failed else "drift"
    store.status_set(ctx, "", "seerr", status, "In orde." if status == "ok" else
                     f"{len(remaining)} afwijking(en)", remaining)
    return {"applied": applied, "failed": failed, "remaining": remaining,
            "changes": [c.view() for c in after]}
