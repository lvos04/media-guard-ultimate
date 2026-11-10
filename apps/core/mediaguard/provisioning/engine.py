"""Declaratieve reconcile-engine: gewenste staat per instantie → actuele staat via API →
diff (aanmaken/bijwerken; verwijderen alleen van eigen items of bij 'volledig beheer') →
toepassen → opnieuw lezen ter verificatie.

Regels (nooit destructief):
- Alleen items met de naam ``MediaGuard · …`` zijn van MediaGuard. Items die de gebruiker
  als 'zelf beheerd' markeert (``provisioning_items.ownership='user'``) worden nooit
  aangeraakt. Andere items worden alleen verwijderd als de gebruiker voor die sectie
  'beheer volledig door MediaGuard' heeft aangevinkt.
- Gemigreerde items (``ownership='migrated'``) behouden hun inloggegevens; alleen
  afgesproken velden (downloadcategorie, anime-toewijzing) worden beheerd.
- In schaduwmodus of bij een gepauzeerde instantie zet MediaGuard nooit RSS/zoeken/
  downloadclients aan.
"""

from __future__ import annotations

import asyncio
import copy
import logging
from collections.abc import Awaitable, Callable
from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Any

from mediaguard.arr.client import ArrClient, ArrError, ArrNotFound
from mediaguard.audit import audit
from mediaguard.db import loads, utcnow
from mediaguard.instances.registry import (
    apikey_secret,
    arr_client,
    instance_row,
    list_instances,
    update_instance,
)
from mediaguard.provisioning import store
from mediaguard.provisioning import templates as T
from mediaguard.provisioning.store import MASK, ProvisioningConfig

if TYPE_CHECKING:
    from mediaguard.context import AppContext

LOGGER = logging.getLogger(__name__)
PENDING = ("create", "update", "delete")
SECRET_FIELDS = ("apikey", "password", "passkey", "secret", "token")


def is_secret_field(name: str) -> bool:
    n = name.lower()
    return any(s in n for s in SECRET_FIELDS)


def show(name: str, value: Any) -> Any:
    if is_secret_field(name) and value not in (None, ""):
        return "***"
    return value


# -- veldhulpen ---------------------------------------------------------------------------
def fget(resource: dict[str, Any], name: str) -> Any:
    for f in resource.get("fields") or []:
        if str(f.get("name", "")).lower() == name.lower():
            return f.get("value")
    return None


def fhas(resource: dict[str, Any], name: str) -> bool:
    return any(str(f.get("name", "")).lower() == name.lower()
               for f in resource.get("fields") or [])


def fset(resource: dict[str, Any], name: str, value: Any, *, add: bool = False) -> None:
    for f in resource.setdefault("fields", []):
        if str(f.get("name", "")).lower() == name.lower():
            f["value"] = value
            return
    if add:
        resource["fields"].append({"name": name, "value": value})


def norm_url(url: str | None) -> str:
    from mediaguard.seerr.service import _norm_url

    return _norm_url(url or "") if url else ""


# -- wijzigingen ----------------------------------------------------------------------------
@dataclass
class Change:
    section: str
    action: str  # create | update | delete | blocked | needs_input | info | error
    key: str
    title_nl: str
    instance_id: str = ""
    diff: list[dict[str, Any]] = field(default_factory=list)
    owned: bool = True
    auto: bool = True  # mag door de drift-controle automatisch hersteld worden
    message_nl: str = ""
    run: Callable[[], Awaitable[Any]] | None = None

    @property
    def pending(self) -> bool:
        return self.action in PENDING

    def view(self) -> dict[str, Any]:
        return {"section": self.section, "section_nl": store.SECTION_NL.get(self.section),
                "action": self.action, "key": self.key, "title_nl": self.title_nl,
                "instance_id": self.instance_id or None, "diff": self.diff,
                "owned": self.owned, "auto": self.auto, "message_nl": self.message_nl}


def diff_line(field_name: str, label: str, old: Any, new: Any) -> dict[str, Any]:
    return {"field": field_name, "label_nl": label, "old": show(field_name, old),
            "new": show(field_name, new)}


def section_status(changes: list[Change]) -> tuple[str, str]:
    if any(c.action == "error" for c in changes):
        return "error", next(c.message_nl or c.title_nl for c in changes if c.action == "error")
    if any(c.action == "blocked" for c in changes):
        return "error", next(c.message_nl or c.title_nl for c in changes
                             if c.action == "blocked")
    if any(c.action == "needs_input" for c in changes):
        return "needs_input", next(c.message_nl or c.title_nl for c in changes
                                   if c.action == "needs_input")
    pending = [c for c in changes if c.pending]
    if any(c.action == "create" for c in pending):
        return "missing", f"{len(pending)} wijziging(en) nodig; ontbrekende onderdelen."
    if pending:
        return "drift", f"{len(pending)} afwijking(en) van de gewenste instelling."
    return "ok", "In orde."


# -- doel (één instantie) -----------------------------------------------------------------
@dataclass
class Target:
    ctx: AppContext
    cfg: ProvisioningConfig
    inst: dict[str, Any]
    client: ArrClient
    mode: str
    actor: str = "system"
    cache: dict[str, Any] = field(default_factory=dict)
    # voorvertoning van 'optimaliseren na migratie': bestaande niet-eigen items gelden
    # alvast als gemigreerd (bij uitvoeren worden ze echt zo vastgelegd)
    assume_migrated: bool = False

    def is_migrated(self, section: str, name: str) -> bool:
        if T.is_owned_name(name):
            return False
        rec = self.items(section).get(name)
        if rec is not None:
            return rec["ownership"] == "migrated"
        return self.assume_migrated

    @property
    def iid(self) -> str:
        return str(self.inst["id"])

    @property
    def app(self) -> str:
        return str(self.inst["app"])

    @property
    def media_type(self) -> str:
        return str(self.inst["media_type"])

    @property
    def is_anime(self) -> bool:
        return self.media_type in T.ANIME_TYPES

    @property
    def shadow(self) -> bool:
        return bool(self.inst["shadow_mode"])

    @property
    def suspended(self) -> bool:
        return bool(self.inst["suspended"])

    @property
    def migrated(self) -> bool:
        return self.mode == "optimized"

    async def get(self, path: str, default: Any = None) -> Any:
        if path not in self.cache:
            try:
                self.cache[path] = await self.client.get(path)
            except ArrNotFound:
                self.cache[path] = default
        return self.cache[path]

    def forget(self, *paths: str) -> None:
        for p in paths:
            self.cache.pop(p, None)

    def items(self, section: str) -> dict[str, dict[str, Any]]:
        return store.items(self.ctx, self.iid, section)

    def user_managed(self, section: str, key: str) -> bool:
        rec = self.items(section).get(key)
        return bool(rec and rec["ownership"] == "user")

    def full_manage(self, section: str) -> bool:
        return section in self.cfg.instance(self.iid).full_manage and self.mode != "hold"

    def change(self, section: str, action: str, key: str, title: str, **kw: Any) -> Change:
        return Change(section, action, key, title, instance_id=self.iid, **kw)

    def remember(self, section: str, key: str, remote_id: int | None, *,
                 ownership: str = "mediaguard", secret: str | None = None) -> None:
        store.item_set(self.ctx, self.iid, section, key, remote_id=remote_id,
                       ownership=ownership, secret=secret, actor=self.actor)


Planner = Callable[[Target], Awaitable[list[Change]]]


# -- tags ----------------------------------------------------------------------------------
async def plan_tags(t: Target) -> list[Change]:
    wanted = ["mediaguard"] + (["anime"] if t.is_anime else [])
    have = {str(x.get("label", "")).lower() for x in await t.get("tag", [])}
    out = []
    for label in wanted:
        if label in have:
            continue

        async def run(label: str = label) -> None:
            tag = await t.client.tag_create(label)
            t.remember("tags", label, tag.get("id"))
        out.append(t.change("tags", "create", label, f"Tag '{label}' aanmaken", run=run))
    return out


# -- rootmappen ----------------------------------------------------------------------------
async def path_visible(client: ArrClient, path: str) -> bool | None:
    """Bestaat de map in de container van de instantie? None = onbekend."""
    path = path.rstrip("/")
    if not path:
        return True
    try:
        # Gemeten (Sonarr 4.0.20 / Radarr 6.4): pad ZONDER slot-slash + allowFolders… →
        # bestaande map geeft 'parent', niet-bestaande map de root-lijst zonder 'parent'.
        # Mét slot-slash krijgt ook een niet-bestaand pad 'parent: "/"' (vals positief).
        data = await client.request("GET", "filesystem", params={
            "path": path, "includeFiles": False,
            "allowFoldersWithoutTrailingSlashes": True})
        if isinstance(data, dict) and data.get("parent") is not None:
            return True
        parent = path.rsplit("/", 1)[0] or "/"
        up = await client.request("GET", "filesystem", params={
            "path": parent.rstrip("/") + "/", "includeFiles": False})
        dirs = {str(d.get("path", "")).rstrip("/") for d in (up or {}).get("directories") or []}
        return path in dirs
    except ArrError:
        return None


def _typo_hint(path: str) -> str | None:
    """'Anime Fims' → 'Anime Films': alleen melden, nooit zelf hernoemen (media verplaatsen
    is een bewuste keuze van de gebruiker)."""
    import difflib

    name = path.rstrip("/").rsplit("/", 1)[-1]
    for word in name.split():
        for good in ("Films", "Movies", "Anime", "Series", "Shows", "Media"):
            if word != good and word.lower() != good.lower() and \
                    difflib.SequenceMatcher(None, word.lower(), good.lower()).ratio() >= 0.8:
                return name.replace(word, good)
    return None


async def plan_rootfolders(t: Target) -> list[Change]:
    path = store.root_folder_for(t.ctx, t.iid, t.media_type, t.cfg)
    roots = await t.get("rootfolder", [])
    existing = {str(r.get("path", "")).rstrip("/"): r for r in roots if r.get("path")}
    info: list[Change] = []
    for real in existing:
        hint = _typo_hint(real)
        if hint:
            info.append(t.change(
                "rootfolders", "info", real, f"Mapnaam {real} lijkt een typfout",
                auto=False, message_nl=f"Bedoel je '{hint}'? MediaGuard gebruikt de map zoals "
                "hij is en hernoemt niets: hernoemen verplaatst alle media. Wil je het "
                "aanpassen, doe dat dan in Sonarr/Radarr (Series/Movies → Mass Editor)."))
    if path not in existing and existing and not t.cfg.instance(t.iid).root_folder:
        # Rapport 4 nov (H12): bestaande, echte mappen gaan voor het sjabloon. Nooit een
        # '/data/media/tv' aanmaken naast '/data/Media/TV'.
        from mediaguard.provisioning.seerr_sync import pick_root

        same_case = next((p for p in existing if p.lower() == path.lower()), None)
        adopted = same_case or pick_root(set(existing), t.media_type)
        if adopted:
            if str((instance_row(t.ctx, t.iid) or {}).get("default_root_folder") or ""
                   ).rstrip("/") != adopted:
                update_instance(t.ctx, t.iid, {"default_root_folder": adopted})
            if same_case is None or same_case != path:
                info.append(t.change(
                    "rootfolders", "info", adopted, f"Bestaande mediamap {adopted} gebruikt",
                    auto=False, message_nl=f"MediaGuard verwachtte {path}, maar "
                    f"{t.inst['name']} gebruikt al {adopted}. Die echte map wordt gebruikt; "
                    "er wordt geen nieuwe map aangemaakt."))
            path = adopted
    match = existing.get(path)
    if match is not None:
        if match.get("accessible") is False:
            return [t.change("rootfolders", "blocked", path,
                             f"Mediamap {path} is niet toegankelijk",
                             message_nl=f"{t.inst['name']} kan {path} niet lezen/schrijven. "
                             "Controleer de TrueNAS-dataset en de rechten (hostactie).")] + info
        return info
    visible = await path_visible(t.client, path)
    if visible is False:
        return [t.change("rootfolders", "blocked", path, f"Mediamap {path} ontbreekt",
                         message_nl=f"De map {path} bestaat niet in de container van "
                         f"{t.inst['name']}. Koppel de juiste TrueNAS-dataset aan dit pad "
                         "(hostactie) of kies een ander mediapad in MediaGuard.")]

    async def run() -> None:
        try:
            rec = await t.client.root_folder_add(path)
        except ArrError as exc:
            if "writable" in exc.message.lower() or "schrijf" in exc.message.lower():
                raise RuntimeError(
                    f"Hostactie nodig: {t.inst['name']} kan niet schrijven in {path}. "
                    "Geef de gebruiker 'apps' (uid 568) schrijfrechten op de dataset "
                    "(TrueNAS: Datasets → Permissions/ACL).") from exc
            raise
        t.remember("rootfolders", path, rec.get("id"))
        if not instance_row(t.ctx, t.iid).get("default_root_folder"):  # type: ignore[union-attr]
            update_instance(t.ctx, t.iid, {"default_root_folder": path})
    return [t.change("rootfolders", "create", path, f"Mediamap {path} toevoegen",
                     diff=[diff_line("path", "Pad", None, path)], run=run)]


# -- configuratiesecties --------------------------------------------------------------------
def desired_mediamanagement(t: Target) -> dict[str, Any]:
    m = t.cfg.media
    out: dict[str, Any] = {"copyUsingHardlinks": m.hardlinks, "recycleBin": m.recycle_bin,
                           "downloadPropersAndRepacks": m.propers,
                           "importExtraFiles": m.import_extra_files,
                           "extraFileExtensions": m.extra_file_extensions,
                           # zonder analyse geen audio/ondertitels/codec in MediaGuard (R10)
                           "enableMediaInfo": True}
    key = "autoUnmonitorPreviouslyDownloadedEpisodes" if t.app == "sonarr" else \
        "autoUnmonitorPreviouslyDownloadedMovies"
    out[key] = m.unmonitor_deleted
    return out


MM_LABELS = {"copyUsingHardlinks": "Hardlinks gebruiken", "recycleBin": "Prullenbak",
             "enableMediaInfo": "Videobestanden analyseren (mediainfo)",
             "downloadPropersAndRepacks": "Propers/repacks",
             "importExtraFiles": "Extra bestanden (ondertitels) importeren",
             "extraFileExtensions": "Extensies extra bestanden",
             "autoUnmonitorPreviouslyDownloadedEpisodes": "Verwijderde afleveringen niet meer "
                                                         "volgen",
             "autoUnmonitorPreviouslyDownloadedMovies": "Verwijderde films niet meer volgen"}


def desired_naming(t: Target) -> dict[str, Any]:
    base = dict(T.SONARR_NAMING if t.app == "sonarr" else T.RADARR_NAMING)
    rename_key = "renameEpisodes" if t.app == "sonarr" else "renameMovies"
    base[rename_key] = t.cfg.media.rename
    for k, v in t.cfg.media.naming_overrides.items():
        if k in base:
            base[k] = v
    return base


async def _plan_config(t: Target, section: str, path: str, desired: dict[str, Any],
                       labels: dict[str, str], title: str) -> list[Change]:
    actual = await t.get(path)
    if not isinstance(actual, dict):
        return [t.change(section, "info", section, f"{title}: niet beschikbaar in deze versie")]
    diffs = [diff_line(k, labels.get(k, k), actual.get(k), v) for k, v in desired.items()
             if k in actual and actual.get(k) != v]
    if not diffs:
        return []

    async def run() -> None:
        fresh = await t.client.get(path)
        await t.client.put(path, {**fresh, **{k: v for k, v in desired.items() if k in fresh}})
        t.remember(section, section, fresh.get("id"))
    return [t.change(section, "update", section, f"{title} bijwerken", diff=diffs, run=run)]


async def plan_mediamanagement(t: Target) -> list[Change]:
    return await _plan_config(t, "mediamanagement", "config/mediamanagement",
                              desired_mediamanagement(t), MM_LABELS, "Mediabeheer")


async def plan_naming(t: Target) -> list[Change]:
    labels = {"renameEpisodes": "Afleveringen hernoemen", "renameMovies": "Films hernoemen",
              "standardEpisodeFormat": "Formaat gewone afleveringen",
              "animeEpisodeFormat": "Formaat anime (absolute nummering)",
              "dailyEpisodeFormat": "Formaat dagelijkse afleveringen",
              "seriesFolderFormat": "Seriemap", "seasonFolderFormat": "Seizoensmap",
              "specialsFolderFormat": "Specialsmap", "standardMovieFormat": "Filmformaat",
              "movieFolderFormat": "Filmmap", "replaceIllegalCharacters":
              "Ongeldige tekens vervangen"}
    return await _plan_config(t, "naming", "config/naming", desired_naming(t), labels,
                              "Naamgeving")


async def plan_qualitydefinitions(t: Target) -> list[Change]:
    if not t.cfg.quality.quality_definitions:
        return []
    defs = await t.get("qualitydefinition", [])
    diffs = []
    for d in defs or []:
        name = (d.get("quality") or {}).get("name")
        if name not in T.QUALITY_SIZES:
            continue
        lo, pref, hi = T.QUALITY_SIZES[name]
        for key, val in (("minSize", lo), ("preferredSize", pref), ("maxSize", hi)):
            if key in d and d.get(key) != val:
                diffs.append(diff_line(f"{name}.{key}", f"{name} {key}", d.get(key), val))
    if not diffs:
        return []

    async def run() -> None:
        fresh = await t.client.get("qualitydefinition") or []
        for d in fresh:
            name = (d.get("quality") or {}).get("name")
            if name in T.QUALITY_SIZES:
                lo, pref, hi = T.QUALITY_SIZES[name]
                for key, val in (("minSize", lo), ("preferredSize", pref), ("maxSize", hi)):
                    if key in d:
                        d[key] = val
        await t.client.put("qualitydefinition/update", fresh)
    return [t.change("qualitydefinitions", "update", "qualitydefinitions",
                     "Kwaliteitsdefinities (bestandsgroottes) bijwerken", diff=diffs, run=run)]


# -- custom formats --------------------------------------------------------------------------
def _spec_value(spec: dict[str, Any]) -> Any:
    fields = spec.get("fields")
    if isinstance(fields, dict):
        return fields.get("value")
    for f in fields or []:
        if f.get("name") == "value":
            return f.get("value")
    return None


def to_api_spec(spec: dict[str, Any]) -> dict[str, Any]:
    out = {k: v for k, v in spec.items() if k not in ("fields", "id")}
    fields = spec.get("fields")
    if isinstance(fields, dict):  # TRaSH-JSON-formaat
        out["fields"] = [{"name": k, "value": v} for k, v in fields.items()]
    else:
        out["fields"] = [{"name": f.get("name"), "value": f.get("value")} for f in fields or []]
    return out


def norm_specs(specs: list[dict[str, Any]]) -> list[tuple[Any, ...]]:
    return sorted((str(s.get("implementation")), bool(s.get("negate")),
                   bool(s.get("required")), str(_spec_value(s))) for s in specs or [])


def desired_formats(t: Target) -> dict[str, dict[str, Any]]:
    """{volledige naam: {specs, scores per sjabloon}}"""
    out: dict[str, dict[str, Any]] = {}
    for name in T.formats_for(t.media_type):
        out[T.owned_name(name)] = {"specs": T.CUSTOM_FORMATS[name], "short": name}
    extra = t.cfg.quality.extra_trash_formats
    wanted = {n for tpl in T.templates_for(t.media_type) for n in extra.get(tpl, [])}
    if wanted:
        marks = ",".join("?" for _ in wanted)
        for row in t.ctx.db.fetch_all(f"SELECT name, spec_json, scores_json FROM "
                                      f"trash_custom_formats WHERE name IN ({marks})",
                                      tuple(wanted)):
            spec = loads(row["spec_json"], {})
            out[T.owned_name(row["name"])] = {
                "specs": [to_api_spec(s) for s in spec.get("specifications") or []],
                "short": row["name"], "trash_scores": loads(row["scores_json"], {})}
    return out


async def plan_customformats(t: Target) -> list[Change]:
    if not t.cfg.quality.enabled:
        return []
    actual = {str(c.get("name")): c for c in await t.get("customformat", []) or []}
    out: list[Change] = []
    desired = desired_formats(t)
    for name, d in desired.items():
        body = {"name": name, "includeCustomFormatWhenRenaming": False,
                "specifications": copy.deepcopy(d["specs"])}
        cur = actual.get(name)
        if cur is not None and t.user_managed("customformats", name):
            continue
        if cur is None:
            async def create(body: Any = body, name: str = name) -> None:
                if problem := spec_problem(body["specifications"]):
                    raise ValueError(problem)
                rec = await t.client.post("customformat", body)
                t.remember("customformats", name, (rec or {}).get("id"))
            out.append(t.change("customformats", "create", name,
                                f"Custom format '{name}' aanmaken", run=create))
        elif norm_specs(cur.get("specifications")) != norm_specs(body["specifications"]):
            async def update(body: Any = body, cur: Any = cur,
                             name: str = name) -> None:
                if problem := spec_problem(body["specifications"]):
                    raise ValueError(problem)
                await t.client.put(f"customformat/{int(cur['id'])}", {**body, "id": cur["id"]})
                t.remember("customformats", name, cur["id"])
            out.append(t.change("customformats", "update", name,
                                f"Custom format '{name}' herstellen",
                                diff=[diff_line("specifications", "Voorwaarden", "gewijzigd",
                                                "MediaGuard-sjabloon")], run=update))
    for name, cur in actual.items():
        if name in desired or t.user_managed("customformats", name):
            continue
        if T.is_owned_name(name) or t.full_manage("customformats"):
            async def delete(cur: Any = cur, name: str = name) -> None:
                await t.client.delete(f"customformat/{int(cur['id'])}")
                store.item_delete(t.ctx, t.iid, "customformats", name)
            why = "niet meer in MediaGuard-sjabloon" if T.is_owned_name(name) else \
                "volledig beheer door MediaGuard aangevinkt"
            out.append(t.change("customformats", "delete", name,
                                f"Custom format '{name}' verwijderen ({why})", auto=False,
                                owned=T.is_owned_name(name), run=delete))
    return out


# -- kwaliteitsprofielen ----------------------------------------------------------------------
def leaves(item: dict[str, Any]) -> list[str]:
    if item.get("quality"):
        return [str(item["quality"].get("name"))]
    return [n for sub in item.get("items") or [] for n in leaves(sub)]


def allowed_leaves(profile: dict[str, Any]) -> set[str]:
    out: set[str] = set()
    for it in profile.get("items") or []:
        if it.get("allowed"):
            out.update(leaves(it))
    return out


def cutoff_names(profile: dict[str, Any]) -> set[str]:
    cid = profile.get("cutoff")
    for it in profile.get("items") or []:
        if it.get("quality") and it["quality"].get("id") == cid:
            return {str(it["quality"].get("name"))}
        if not it.get("quality") and it.get("id") == cid:
            return set(leaves(it))
    return set()


def ordered_leaves(profile: dict[str, Any]) -> list[str]:
    """Qualities van laag naar hoog zoals in het profiel (lijstvolgorde)."""
    return [n for it in profile.get("items") or [] for n in leaves(it)]


def template_scores(t: Target, tpl_id: str) -> dict[str, int]:
    tpl = T.QUALITY_TEMPLATES[tpl_id]
    scores = {T.owned_name(k): v for k, v in tpl["scores"].items()}
    for name, d in desired_formats(t).items():
        if "trash_scores" in d and d["short"] in t.cfg.quality.extra_trash_formats.get(
                tpl_id, []):
            ts = d["trash_scores"]
            scores[name] = int(ts.get("default", 0)) if isinstance(ts, dict) else 0
    for k, v in t.cfg.quality.score_overrides.get(tpl_id, {}).items():
        scores[k if T.is_owned_name(k) else T.owned_name(k)] = int(v)
    return scores


def merge_quality_group(items: list[dict[str, Any]],
                        group: dict[str, Any]) -> list[dict[str, Any]]:
    """Zet de genoemde kwaliteiten samen in één groep (bovenaan = hoogste prioriteit), zodat
    Sonarr/Radarr daarbinnen op custom-format-score kiezen. Iedere kwaliteit blijft precies
    één keer in het profiel staan (eis van de API); lege oude groepen verdwijnen."""
    wanted = list(group["qualities"])
    picked: dict[str, dict[str, Any]] = {}
    out: list[dict[str, Any]] = []
    for it in copy.deepcopy(items):
        if it.get("quality"):
            name = str(it["quality"].get("name"))
            if name in wanted:
                picked[name] = it
                continue
            out.append(it)
            continue
        if it.get("name") == group["name"]:
            for sub in it.get("items") or []:
                picked[str(sub["quality"].get("name"))] = sub
            continue
        rest = []
        for sub in it.get("items") or []:
            name = str(sub["quality"].get("name"))
            if name in wanted:
                picked[name] = sub
            else:
                rest.append(sub)
        if rest:
            it["items"] = rest
            out.append(it)
    if not picked:
        return out
    ids = [int(it.get("id") or 0) for it in out if not it.get("quality")]
    members = [dict(picked[n], items=[], allowed=True) for n in wanted if n in picked]
    out.append({"name": group["name"], "id": max([1000, *ids]) + 1, "allowed": True,
                "items": members})
    return out


def build_profile(t: Target, tpl_id: str, base: dict[str, Any],
                  formats: list[dict[str, Any]]) -> dict[str, Any]:
    tpl = T.QUALITY_TEMPLATES[tpl_id]
    body = copy.deepcopy(base)
    body["name"] = T.owned_name(tpl["name"])
    body["upgradeAllowed"] = bool(tpl["upgrade"])
    allowed = set(tpl["allowed"])
    if tpl.get("merge_group"):
        body["items"] = merge_quality_group(body.get("items") or [], tpl["merge_group"])
    for it in body.get("items") or []:
        on = any(n in allowed for n in leaves(it))
        it["allowed"] = on
        for sub in it.get("items") or []:
            sub["allowed"] = on
    cutoff_id = None
    for it in body.get("items") or []:
        if tpl["cutoff"] in leaves(it):
            cutoff_id = it["quality"]["id"] if it.get("quality") else it.get("id")
    if cutoff_id is None:
        for it in body.get("items") or []:
            if it.get("allowed"):
                cutoff_id = it["quality"]["id"] if it.get("quality") else it.get("id")
    body["cutoff"] = cutoff_id
    scores = template_scores(t, tpl_id)
    existing = {int(fi.get("format", -1)): fi.get("score", 0)
                for fi in base.get("formatItems") or []}
    body["formatItems"] = [
        {"format": int(cf["id"]), "name": cf["name"],
         "score": scores.get(cf["name"], 0 if T.is_owned_name(cf["name"])
                             else int(existing.get(int(cf["id"]), 0) or 0))}
        for cf in formats]
    body["minFormatScore"] = tpl["min_score"]
    body["cutoffFormatScore"] = tpl["cutoff_score"]
    if "minUpgradeFormatScore" in base:
        body["minUpgradeFormatScore"] = T.MIN_UPGRADE_SCORE
    if t.app == "radarr":
        lang = t.cfg.quality.language_films if tpl_id == "films" else tpl["language"]
        body["language"] = dict(T.RADARR_LANGUAGE[lang])
    return body


def profile_diff(t: Target, tpl_id: str, actual: dict[str, Any]) -> list[dict[str, Any]]:
    tpl = T.QUALITY_TEMPLATES[tpl_id]
    diffs = []
    want = set(tpl["allowed"]) & set(ordered_leaves(actual))
    have = allowed_leaves(actual)
    if want and not want <= have or have - want - _grouped_with(actual, want):
        diffs.append(diff_line("allowed", "Toegestane kwaliteiten", sorted(have), sorted(want)))
    cut = cutoff_names(actual)
    if cut and tpl["cutoff"] not in cut and tpl["cutoff"] in ordered_leaves(actual):
        diffs.append(diff_line("cutoff", "Upgrade tot", sorted(cut), tpl["cutoff"]))
    if bool(actual.get("upgradeAllowed")) != bool(tpl["upgrade"]):
        diffs.append(diff_line("upgradeAllowed", "Upgrades", actual.get("upgradeAllowed"),
                               tpl["upgrade"]))
    for key, val in (("minFormatScore", tpl["min_score"]),
                     ("cutoffFormatScore", tpl["cutoff_score"]),
                     ("minUpgradeFormatScore", T.MIN_UPGRADE_SCORE)):
        if key in actual and actual.get(key) != val:
            diffs.append(diff_line(key, key, actual.get(key), val))
    have_scores = {str(fi.get("name")): fi.get("score") for fi in actual.get("formatItems") or []}
    for name, score in template_scores(t, tpl_id).items():
        if have_scores.get(name) != score and (score != 0 or name in have_scores):
            diffs.append(diff_line(f"score:{name}", f"Score '{name}'", have_scores.get(name),
                                   score))
    if t.app == "radarr":
        lang = t.cfg.quality.language_films if tpl_id == "films" else tpl["language"]
        want_lang = T.RADARR_LANGUAGE[lang]["id"]
        if (actual.get("language") or {}).get("id") != want_lang:
            diffs.append(diff_line("language", "Taal", (actual.get("language") or {}).get(
                "name"), T.RADARR_LANGUAGE[lang]["name"]))
    return diffs


def _grouped_with(profile: dict[str, Any], names: set[str]) -> set[str]:
    out: set[str] = set()
    for it in profile.get("items") or []:
        ls = leaves(it)
        if not it.get("quality") and any(n in names for n in ls):
            out.update(ls)
    return out


async def plan_qualityprofiles(t: Target) -> list[Change]:
    if not t.cfg.quality.enabled:
        return []
    profiles = {str(p.get("name")): p for p in await t.get("qualityprofile", []) or []}
    out: list[Change] = []
    for tpl_id in T.templates_for(t.media_type):
        name = T.owned_name(T.QUALITY_TEMPLATES[tpl_id]["name"])
        cur = profiles.get(name)
        if cur is not None and t.user_managed("qualityprofiles", name):
            continue
        if cur is None:
            async def create(tpl_id: str = tpl_id, name: str = name) -> None:
                schema = await t.client.get("qualityprofile/schema")
                formats = await t.client.get("customformat") or []
                body = build_profile(t, tpl_id, schema, formats)
                body.pop("id", None)
                rec = await t.client.post("qualityprofile", body)
                t.remember("qualityprofiles", name, (rec or {}).get("id"))
            out.append(t.change("qualityprofiles", "create", name,
                                f"Kwaliteitsprofiel '{name}' aanmaken "
                                f"({T.QUALITY_TEMPLATES[tpl_id]['label_nl']})", run=create))
            continue
        diffs = profile_diff(t, tpl_id, cur)
        if diffs:
            async def update(tpl_id: str = tpl_id, cur: Any = cur,
                             name: str = name) -> None:
                fresh = await t.client.get(f"qualityprofile/{int(cur['id'])}")
                formats = await t.client.get("customformat") or []
                body = build_profile(t, tpl_id, fresh, formats)
                body["id"] = cur["id"]
                await t.client.put(f"qualityprofile/{int(cur['id'])}", body)
                t.remember("qualityprofiles", name, cur["id"])
            out.append(t.change("qualityprofiles", "update", name,
                                f"Kwaliteitsprofiel '{name}' herstellen", diff=diffs,
                                run=update))
    return out


# -- vertragingsprofiel ---------------------------------------------------------------------
def preferred_protocol(cfg: ProvisioningConfig) -> str:
    if cfg.delay.preferred_protocol != "auto":
        return cfg.delay.preferred_protocol
    protocols = {d.protocol for d in cfg.download_clients}
    return "usenet" if "usenet" in protocols or not protocols else "torrent"


async def plan_delayprofile(t: Target) -> list[Change]:
    profiles = await t.get("delayprofile", []) or []
    default = next((p for p in profiles if not p.get("tags")
                    and int(p.get("order", 0)) == 2147483647), None) or next(
        (p for p in profiles if int(p.get("id", 0)) == 1), None)
    if default is None:
        return []
    d = t.cfg.delay
    desired = {"enableUsenet": True, "enableTorrent": True,
               "preferredProtocol": preferred_protocol(t.cfg), "usenetDelay": d.usenet_delay,
               "torrentDelay": d.torrent_delay,
               "bypassIfHighestQuality": d.bypass_if_highest_quality}
    labels = {"preferredProtocol": "Voorkeursprotocol", "usenetDelay": "Usenet-vertraging",
              "torrentDelay": "Torrent-vertraging", "enableUsenet": "Usenet",
              "enableTorrent": "Torrent", "bypassIfHighestQuality":
              "Geen vertraging bij hoogste kwaliteit"}
    diffs = [diff_line(k, labels[k], default.get(k), v) for k, v in desired.items()
             if k in default and default.get(k) != v]
    if not diffs:
        return []

    async def run() -> None:
        await t.client.put(f"delayprofile/{int(default['id'])}", {**default, **{
            k: v for k, v in desired.items() if k in default}})
        t.remember("delayprofile", "default", default["id"])
    return [t.change("delayprofile", "update", "default", "Standaard-vertragingsprofiel "
                     "bijwerken", diff=diffs, run=run)]


async def plan_releaseprofiles(t: Target) -> list[Change]:
    return []  # MediaGuard gebruikt custom formats; oude releaseprofielen: zie optimize


# -- downloadclients -------------------------------------------------------------------------
def category_field(app: str) -> str:
    return "tvCategory" if app == "sonarr" else "movieCategory"


def dc_targets(dc: store.DownloadClientCfg, iid: str) -> bool:
    return dc.instances is None or iid in dc.instances


async def dc_body(t: Target, dc: store.DownloadClientCfg,
                  template: dict[str, Any]) -> dict[str, Any]:
    body = copy.deepcopy(template)
    body.pop("presets", None)
    body.pop("id", None)
    enable = dc.enabled and not t.shadow and not t.suspended
    body.update(name=T.owned_name(dc.name), enable=enable, priority=dc.priority,
                implementation=dc.implementation, protocol=dc.protocol, tags=[])
    body.setdefault("removeCompletedDownloads", True)
    body.setdefault("removeFailedDownloads", True)
    for name, value in dc_fields(t, dc).items():
        fset(body, name, value, add=False)
    return body


def dc_fields(t: Target, dc: store.DownloadClientCfg, *,
              with_secrets: bool = True) -> dict[str, Any]:
    out: dict[str, Any] = {"host": dc.host, "port": dc.port, "useSsl": dc.use_ssl,
                           "urlBase": dc.url_base, "username": dc.username or None,
                           category_field(t.app): store.category_for(
                               t.ctx, t.iid, t.media_type, t.cfg, dc)}
    if with_secrets:
        out["apiKey"] = t.ctx.secrets.get(store.dc_secret(dc.name, "api_key"))
        out["password"] = t.ctx.secrets.get(store.dc_secret(dc.name, "password"))
    out.update(dc.extra_fields)
    return {k: v for k, v in out.items() if v is not None}


def dc_secret_hash(t: Target, dc: store.DownloadClientCfg) -> str:
    return store.secret_hash(t.ctx.secrets.get(store.dc_secret(dc.name, "api_key")),
                             t.ctx.secrets.get(store.dc_secret(dc.name, "password")))


def provider_diff(actual: dict[str, Any], desired: dict[str, Any], top: dict[str, Any],
                  *, secrets_changed: bool) -> list[dict[str, Any]]:
    diffs = [diff_line(k, k, actual.get(k), v) for k, v in top.items() if actual.get(k) != v]
    for name, value in desired.items():
        if not fhas(actual, name):
            continue
        cur = fget(actual, name)
        if is_secret_field(name):
            if cur not in (MASK, None, "") and cur != value:
                diffs.append(diff_line(name, name, cur, value))
            continue
        if cur != value and not (cur in (None, "") and value in (None, "")):
            diffs.append(diff_line(name, name, cur, value))
    if secrets_changed:
        diffs.append({"field": "secrets", "label_nl": "Inloggegevens", "old": "***",
                      "new": "*** (gewijzigd in MediaGuard)"})
    return diffs


async def plan_downloadclients(t: Target) -> list[Change]:  # noqa: C901
    cfg = t.cfg
    actual = await t.get("downloadclient", []) or []
    by_name = {str(d.get("name")): d for d in actual}
    recs = t.items("downloadclients")
    out: list[Change] = []
    wanted = [dc for dc in cfg.download_clients if dc_targets(dc, t.iid)]
    if not wanted and not any(not T.is_owned_name(d.get("name")) for d in actual):
        out.append(t.change("downloadclients", "needs_input", "downloadclients",
                            "Nog geen downloadclient ingevoerd",
                            message_nl="Voer eenmalig je downloadclient in bij MediaGuard → "
                                       "Inrichten → Downloads."))
    schema: list[dict[str, Any]] | None = None
    desired_names = set()
    for dc in wanted:
        name = T.owned_name(dc.name)
        desired_names.add(name)
        missing = [f for f in T.DC_REQUIRED_SECRETS[dc.implementation]
                   if not t.ctx.secrets.has(store.dc_secret(dc.name, f))]
        if missing:
            label = "API-sleutel" if missing[0] == "api_key" else "wachtwoord"
            out.append(t.change("downloadclients", "needs_input", name,
                                f"downloadclient-{label} ontbreekt voor '{dc.name}'",
                                message_nl=f"downloadclient-{label} ontbreekt voor "
                                           f"'{dc.name}'"))
            continue
        if t.user_managed("downloadclients", name):
            continue
        cur = by_name.get(name)
        if cur is None and t.migrated:
            dup = next((d for d in actual if not T.is_owned_name(d.get("name"))
                        and d.get("implementation") == dc.implementation
                        and str(fget(d, "host")) == dc.host
                        and int(fget(d, "port") or 0) == dc.port), None)
            if dup is not None:
                out.append(t.change("downloadclients", "info", name,
                                    f"'{dc.name}' bestaat al uit de migratie als "
                                    f"'{dup.get('name')}'; die wordt gebruikt (inloggegevens "
                                    "blijven behouden)."))
                continue
        if schema is None:
            schema = await t.get("downloadclient/schema", []) or []
        template = next((s for s in schema if s.get("implementation") == dc.implementation),
                        None)
        if template is None:
            out.append(t.change("downloadclients", "error", name,
                                f"{dc.implementation} wordt niet ondersteund door "
                                f"{t.inst['name']}"))
            continue
        shash = dc_secret_hash(t, dc)
        if cur is None:
            async def create(dc: store.DownloadClientCfg = dc, template: Any = template,
                             name: str = name, shash: str = shash) -> None:
                rec = await t.client.provider_create("downloadclient",
                                                     await dc_body(t, dc, template),
                                                     force_save=True)
                t.remember("downloadclients", name, rec.get("id"), secret=shash)
            out.append(t.change(
                "downloadclients", "create", name,
                f"Downloadclient '{name}' aanmaken (categorie "
                f"{store.category_for(t.ctx, t.iid, t.media_type, cfg, dc)})",
                diff=[diff_line(k, k, None, v) for k, v in dc_fields(t, dc).items()],
                run=create))
            continue
        top = {"enable": dc.enabled and not t.shadow and not t.suspended,
               "priority": dc.priority}
        rec = recs.get(name) or {}
        diffs = provider_diff(cur, dc_fields(t, dc), top,
                              secrets_changed=bool(rec.get("secret_hash"))
                              and rec.get("secret_hash") != shash)
        if diffs:
            async def update(dc: store.DownloadClientCfg = dc, cur: Any = cur,
                             template: Any = template, name: str = name,
                             shash: str = shash) -> None:
                body = await dc_body(t, dc, template)
                body = {**cur, **{k: v for k, v in body.items() if k != "fields"}}
                body["fields"] = copy.deepcopy(cur.get("fields") or [])
                for k, v in dc_fields(t, dc).items():
                    fset(body, k, v)
                body["id"] = cur["id"]
                await t.client.provider_update("downloadclient", body, force_save=True)
                t.remember("downloadclients", name, cur["id"], secret=shash)
            out.append(t.change("downloadclients", "update", name,
                                f"Downloadclient '{name}' herstellen", diff=diffs, run=update))
    # gemigreerde downloadclients: inloggegevens blijven, categorie per instantie
    cat_field = category_field(t.app)
    category = store.category_for(t.ctx, t.iid, t.media_type, cfg)
    for d in actual:
        name = str(d.get("name"))
        if T.is_owned_name(name) or t.user_managed("downloadclients", name):
            continue
        if t.is_migrated("downloadclients", name):
            if fhas(d, cat_field) and fget(d, cat_field) != category:
                async def fix(d: Any = d, name: str = name) -> None:
                    body = copy.deepcopy(d)
                    fset(body, cat_field, category)
                    await t.client.provider_update("downloadclient", body, force_save=True)
                    t.remember("downloadclients", name, d["id"], ownership="migrated")
                out.append(t.change("downloadclients", "update", name,
                                    f"Categorie van gemigreerde downloadclient '{name}' "
                                    f"→ {category}", owned=False,
                                    diff=[diff_line(cat_field, "Categorie",
                                                    fget(d, cat_field), category)], run=fix))
            continue
    # wezen en volledig beheer
    for d in actual:
        name = str(d.get("name"))
        if name in desired_names or t.user_managed("downloadclients", name):
            continue
        if t.is_migrated("downloadclients", name):
            continue
        if T.is_owned_name(name) or t.full_manage("downloadclients"):
            out.append(_delete_change(t, "downloadclients", "downloadclient", d))
    return out


def _delete_change(t: Target, section: str, kind: str, item: dict[str, Any]) -> Change:
    name = str(item.get("name"))

    async def delete() -> None:
        await t.client.provider_delete(kind, int(item["id"]))
        store.item_delete(t.ctx, t.iid, section, name)
    why = "niet meer in de MediaGuard-configuratie" if T.is_owned_name(name) else \
        "volledig beheer door MediaGuard aangevinkt"
    return t.change(section, "delete", name, f"'{name}' verwijderen ({why})", auto=False,
                    owned=T.is_owned_name(name), run=delete)


# -- indexers ------------------------------------------------------------------------------
def indexer_targets(idx: store.IndexerCfg, inst: dict[str, Any]) -> bool:
    if idx.instances is not None:
        return inst["id"] in idx.instances
    scope = idx.effective_scope()
    anime = inst["media_type"] in T.ANIME_TYPES
    return scope == "both" or (scope == "anime") == anime


def indexer_fields(t: Target, idx: store.IndexerCfg) -> dict[str, Any]:
    out: dict[str, Any] = {"baseUrl": idx.base_url.rstrip("/"), "apiPath": idx.api_path,
                           "apiKey": t.ctx.secrets.get(store.indexer_secret(idx.name))}
    if t.app == "sonarr":
        out["categories"] = idx.categories if idx.categories is not None else (
            [] if idx.effective_scope() == "anime" else T.SONARR_CATEGORIES)
        out["animeCategories"] = idx.anime_categories if idx.anime_categories is not None \
            else T.SONARR_ANIME_CATEGORIES
    else:
        cats = idx.movie_categories if idx.movie_categories is not None else (
            T.RADARR_CATEGORIES + (T.RADARR_ANIME_EXTRA if t.is_anime else []))
        out["categories"] = cats
    if idx.implementation == "Torznab":
        out["minimumSeeders"] = idx.minimum_seeders
    out.update(idx.extra_fields)
    return {k: v for k, v in out.items() if v is not None}


def indexer_flags(t: Target, idx: store.IndexerCfg) -> dict[str, bool]:
    on = idx.enabled and not t.shadow and not t.suspended
    return {"enableRss": on, "enableAutomaticSearch": on, "enableInteractiveSearch":
            idx.enabled}


async def plan_indexers(t: Target) -> list[Change]:  # noqa: C901
    cfg = t.cfg
    actual = await t.get("indexer", []) or []
    out: list[Change] = []
    desired_names: set[str] = set()
    if cfg.indexer_mode == "none" and not actual:
        out.append(t.change("indexers", "needs_input", "indexers",
                            "Kies Prowlarr of voer indexers in",
                            message_nl="Kies bij MediaGuard → Inrichten → Indexers voor "
                                       "Prowlarr of voer je indexers in."))
    if cfg.indexer_mode == "direct":
        schema: list[dict[str, Any]] | None = None
        by_name = {str(i.get("name")): i for i in actual}
        recs = t.items("indexers")
        for idx in cfg.indexers:
            if not indexer_targets(idx, t.inst):
                continue
            name = T.owned_name(idx.name)
            desired_names.add(name)
            if not t.ctx.secrets.has(store.indexer_secret(idx.name)):
                out.append(t.change("indexers", "needs_input", name,
                                    f"indexer-API-sleutel ontbreekt voor '{idx.name}'",
                                    message_nl=f"indexer-API-sleutel ontbreekt voor "
                                               f"'{idx.name}'"))
                continue
            if t.user_managed("indexers", name):
                continue
            cur = by_name.get(name)
            if cur is None and t.migrated:
                dup = next((i for i in actual if not T.is_owned_name(i.get("name"))
                            and norm_url(fget(i, "baseUrl")) == norm_url(idx.base_url)), None)
                if dup is not None:
                    out.append(t.change("indexers", "info", name,
                                        f"'{idx.name}' bestaat al uit de migratie als "
                                        f"'{dup.get('name')}'; die blijft in gebruik."))
                    continue
            if schema is None:
                schema = await t.get("indexer/schema", []) or []
            template = next((s for s in schema if s.get("implementation") ==
                             idx.implementation), None)
            if template is None:
                out.append(t.change("indexers", "error", name, f"{idx.implementation} wordt "
                                    f"niet ondersteund door {t.inst['name']}"))
                continue
            shash = store.secret_hash(t.ctx.secrets.get(store.indexer_secret(idx.name)))
            flags = indexer_flags(t, idx)
            if cur is None:
                async def create(idx: store.IndexerCfg = idx, template: Any = template,
                                 name: str = name, shash: str = shash) -> None:
                    body = copy.deepcopy(template)
                    body.pop("presets", None)
                    body.pop("id", None)
                    body.update(name=name, priority=idx.priority, tags=[],
                                implementation=idx.implementation, **indexer_flags(t, idx))
                    for k, v in indexer_fields(t, idx).items():
                        fset(body, k, v, add=True)
                    rec = await t.client.provider_create("indexer", body, force_save=True)
                    t.remember("indexers", name, rec.get("id"), secret=shash)
                scope = idx.effective_scope()
                out.append(t.change("indexers", "create", name,
                                    f"Indexer '{name}' aanmaken (bereik: {scope})",
                                    diff=[diff_line(k, k, None, v) for k, v in
                                          indexer_fields(t, idx).items()], run=create))
                continue
            top: dict[str, Any] = {"priority": idx.priority}
            if not t.suspended:
                top.update({k: v for k, v in flags.items()
                            if not (t.shadow and v is False and not cur.get(k))})
            rec = recs.get(name) or {}
            diffs = provider_diff(cur, indexer_fields(t, idx), top,
                                  secrets_changed=bool(rec.get("secret_hash"))
                                  and rec.get("secret_hash") != shash)
            if diffs:
                async def update(idx: store.IndexerCfg = idx, cur: Any = cur,
                                 name: str = name, shash: str = shash,
                                 top: Any = top) -> None:
                    body = copy.deepcopy(cur)
                    body.update(top)
                    for k, v in indexer_fields(t, idx).items():
                        fset(body, k, v, add=True)
                    await t.client.provider_update("indexer", body, force_save=True)
                    t.remember("indexers", name, cur["id"], secret=shash)
                out.append(t.change("indexers", "update", name,
                                    f"Indexer '{name}' herstellen", diff=diffs, run=update))
    # gemigreerde indexers: anime-indexers alleen op anime-instanties
    recs = t.items("indexers")
    for i in actual:
        name = str(i.get("name"))
        if T.is_owned_name(name) or t.user_managed("indexers", name) or \
                name.endswith("(Prowlarr)"):
            continue
        if not t.is_migrated("indexers", name) or t.is_anime or \
                name.endswith(ANIME_OFF_SUFFIX):
            continue
        if T.is_anime_name(name, str(fget(i, "baseUrl") or "")):
            out.append(_anime_off_change(t, i))
    for i in actual:
        name = str(i.get("name"))
        if name in desired_names or t.user_managed("indexers", name):
            continue
        if t.is_migrated("indexers", name):
            continue
        if T.is_owned_name(name) or (t.full_manage("indexers")
                                     and not name.endswith("(Prowlarr)")):
            out.append(_delete_change(t, "indexers", "indexer", i))
    return out


ANIME_OFF_SUFFIX = " [uit: anime-indexer]"


def _anime_off_change(t: Target, i: dict[str, Any]) -> Change:
    """Anime-indexer op een niet-anime-instantie: uitzetten en hernoemen (inloggegevens
    blijven; de nieuwe naam voorkomt dat een cutover hem op naam weer aanzet)."""
    name = str(i.get("name"))
    new_name = (name + ANIME_OFF_SUFFIX)[:100]

    async def run() -> None:
        body = copy.deepcopy(i)
        body.update(name=new_name, enableRss=False, enableAutomaticSearch=False,
                    enableInteractiveSearch=False)
        await t.client.provider_update("indexer", body, force_save=True)
        t.remember("indexers", new_name, i["id"], ownership="migrated")
    return t.change("indexers", "update", name,
                    f"Anime-indexer '{name}' uitzetten op {t.inst['name']} (alleen anime-"
                    "instanties zoeken op anime-indexers)", owned=False,
                    diff=[diff_line("name", "Naam", name, new_name),
                          diff_line("enableRss", "RSS", i.get("enableRss"), False),
                          diff_line("enableAutomaticSearch", "Automatisch zoeken",
                                    i.get("enableAutomaticSearch"), False)], run=run)


# -- webhook -------------------------------------------------------------------------------
async def plan_webhook(t: Target) -> list[Change]:
    from mediaguard.instances.service import WEBHOOK_NAME, register_webhook, webhook_url

    url = webhook_url(t.ctx, t.iid)
    notes = await t.get("notification", []) or []
    hook = next((n for n in notes if n.get("implementation") == "Webhook" and (
        n.get("name") == WEBHOOK_NAME or fget(n, "url") == url)), None)

    async def run() -> None:
        res = await register_webhook(t.ctx, t.iid, t.actor)
        t.remember("webhook", WEBHOOK_NAME, res.get("notification_id"))
    if hook is None:
        return [t.change("webhook", "create", WEBHOOK_NAME, "MediaGuard-webhook registreren",
                         diff=[diff_line("url", "URL", None, url)], run=run)]
    diffs = []
    for name, want in (("url", url), ("username", t.iid), ("method", 1)):
        if fget(hook, name) != want:
            diffs.append(diff_line(name, name, fget(hook, name), want))
    for ev in ("onGrab", "onDownload", "onUpgrade"):
        if ev in hook and not hook.get(ev):
            diffs.append(diff_line(ev, ev, False, True))
    if not diffs:
        return []
    return [t.change("webhook", "update", WEBHOOK_NAME, "MediaGuard-webhook herstellen",
                     diff=diffs, run=run)]


PLANNERS: dict[str, Planner] = {
    "tags": plan_tags, "rootfolders": plan_rootfolders,
    "mediamanagement": plan_mediamanagement, "naming": plan_naming,
    "qualitydefinitions": plan_qualitydefinitions, "customformats": plan_customformats,
    "qualityprofiles": plan_qualityprofiles, "delayprofile": plan_delayprofile,
    "releaseprofiles": plan_releaseprofiles, "downloadclients": plan_downloadclients,
    "indexers": plan_indexers, "webhook": plan_webhook,
}


# -- plan --------------------------------------------------------------------------------------
@dataclass
class Plan:
    instances: dict[str, dict[str, Any]] = field(default_factory=dict)
    changes: list[Change] = field(default_factory=list)
    globals: dict[str, dict[str, Any]] = field(default_factory=dict)

    def pending(self) -> list[Change]:
        return [c for c in self.changes if c.pending]

    def view(self) -> dict[str, Any]:
        pend = self.pending()
        return {
            "instances": self.instances, "global": self.globals,
            "summary": {"changes": len(pend),
                        "create": sum(c.action == "create" for c in pend),
                        "update": sum(c.action == "update" for c in pend),
                        "delete": sum(c.action == "delete" for c in pend),
                        "blocked": sum(c.action in ("blocked", "error") for c in self.changes),
                        "needs_input": sum(c.action == "needs_input" for c in self.changes)},
            "text_nl": text_diff(self),
        }


def text_diff(plan: Plan) -> list[str]:
    """Leesbare Nederlandse diff, één regel per wijziging."""
    verbs = {"create": "+", "update": "~", "delete": "-", "blocked": "!", "error": "!",
             "needs_input": "?", "info": "i"}
    lines = []
    for c in plan.changes:
        where = c.instance_id or "algemeen"
        line = f"{verbs.get(c.action, ' ')} [{where}] {store.SECTION_NL.get(c.section)}: " \
               f"{c.title_nl}"
        if c.diff and c.action == "update":
            parts = [f"{d['label_nl']}: {d['old']} → {d['new']}" for d in c.diff[:6]]
            line += " (" + "; ".join(str(p) for p in parts) + ")"
        lines.append(line)
    return lines


SKIP_IN_HOLD_NL = ("Gemigreerde instantie: wacht op 'Optimaliseren na migratie'. Alleen de "
                   "essentiële onderdelen (webhook, mediapaden, tags) worden nu ingericht.")


def eligible(ctx: AppContext, instances: list[str] | None) -> list[dict[str, Any]]:
    rows = [instance_row(ctx, i.id) for i in list_instances(ctx)]
    out = [r for r in rows if r is not None]
    if instances:
        unknown = set(instances) - {r["id"] for r in out}
        if unknown:
            raise ValueError(f"Onbekende instantie(s): {', '.join(sorted(unknown))}")
        out = [r for r in out if r["id"] in instances]
    return out


HOLD_OWNED_SECTIONS = ("customformats", "qualityprofiles")


async def plan_instance(ctx: AppContext, cfg: ProvisioningConfig, inst: dict[str, Any],
                        sections: list[str], actor: str, mode: str | None = None,
                        assume_migrated: bool = False) -> tuple[dict[str, Any], list[Change]]:
    iid = inst["id"]
    mode = mode or store.resolve_mode(ctx, iid, cfg)
    disabled = set(cfg.instance(iid).disabled_sections)
    result: dict[str, Any] = {"instance_id": iid, "name": inst["name"], "app": inst["app"],
                              "media_type": inst["media_type"], "mode": mode,
                              "shadow_mode": bool(inst["shadow_mode"]),
                              "suspended": bool(inst["suspended"]), "sections": {}}
    changes: list[Change] = []
    if not ctx.secrets.has(apikey_secret(iid)):
        for s in sections:
            result["sections"][s] = {"status": "needs_input", "message_nl":
                                     "API-sleutel van de instantie ontbreekt", "changes": []}
        return result, changes
    async with arr_client(ctx, iid) as client:
        t = Target(ctx, cfg, inst, client, mode, actor, assume_migrated=assume_migrated)
        for section in sections:
            if section in disabled:
                result["sections"][section] = {"status": "skipped", "changes": [],
                                               "message_nl": "Door jou beheerd (uitgezet)."}
                continue
            hold_owned_only = mode == "hold" and section in HOLD_OWNED_SECTIONS
            if mode == "hold" and section not in store.ESSENTIAL_SECTIONS \
                    and not hold_owned_only:
                result["sections"][section] = {"status": "skipped", "changes": [],
                                               "message_nl": SKIP_IN_HOLD_NL}
                continue
            try:
                sc = await PLANNERS[section](t)
                if hold_owned_only:
                    # vóór 'optimaliseren na migratie' alleen MediaGuard's eigen formats en
                    # profielen bijhouden; gemigreerde items van jou blijven onaangeroerd
                    allowed = ("create", "update") if store.is_provisioned(ctx, iid) \
                        else ("update",)
                    sc = [c for c in sc if c.action in allowed
                          and (str(c.key).startswith(T.PREFIX)
                               or str(c.title_nl).find(T.PREFIX) >= 0)]
            except ArrError as exc:
                sc = [t.change(section, "error", section, f"Lezen mislukt: {exc.message}",
                               message_nl=exc.message)]
            status, msg = section_status(sc)
            result["sections"][section] = {"status": status, "message_nl": msg,
                                           "changes": [c.view() for c in sc]}
            changes.extend(sc)
    return result, changes


async def build_plan(ctx: AppContext, *, instances: list[str] | None = None,
                     sections: list[str] | None = None, actor: str = "system",
                     include_global: bool = True) -> Plan:
    cfg = store.load(ctx)
    sections = [s for s in (sections or store.SECTIONS) if s in store.SECTIONS]
    inst_sections = [s for s in store.INSTANCE_SECTIONS if s in sections]
    plan = Plan()
    rows = eligible(ctx, instances)
    results = await asyncio.gather(*(plan_instance(ctx, cfg, r, inst_sections, actor)
                                     for r in rows), return_exceptions=True)
    for row, res in zip(rows, results, strict=True):
        if isinstance(res, BaseException):
            LOGGER.exception("plan %s", row["id"], exc_info=res)
            plan.instances[row["id"]] = {"instance_id": row["id"], "name": row["name"],
                                         "error": str(res), "sections": {}}
            continue
        view, changes = res
        plan.instances[row["id"]] = view
        plan.changes.extend(changes)
    if include_global and "prowlarr" in sections:
        from mediaguard.provisioning.prowlarr_sync import plan_prowlarr

        pc = await plan_prowlarr(ctx, cfg, rows, actor)
        status, msg = section_status(pc) if pc or cfg.indexer_mode == "prowlarr" else \
            ("skipped", "Prowlarr wordt niet gebruikt.")
        plan.globals["prowlarr"] = {"status": status, "message_nl": msg,
                                    "changes": [c.view() for c in pc]}
        plan.changes.extend(pc)
    if include_global and "seerr" in sections:
        from mediaguard.provisioning.seerr_sync import plan_seerr

        sc = await plan_seerr(ctx, cfg, actor)
        status, msg = section_status(sc) if sc else ("ok", "In orde.")
        if any(c.key == "seerr:not_configured" for c in sc):
            status = "skipped"
        plan.globals["seerr"] = {"status": status, "message_nl": msg,
                                 "changes": [c.view() for c in sc]}
        plan.changes.extend(sc)
    return plan


def persist_status(ctx: AppContext, plan: Plan) -> None:
    for iid, view in plan.instances.items():
        for section, data in (view.get("sections") or {}).items():
            store.status_set(ctx, iid, section, data["status"], data.get("message_nl", ""),
                             [c for c in data["changes"] if c["action"] in PENDING])
    for section, data in plan.globals.items():
        store.status_set(ctx, "", section, data["status"], data.get("message_nl", ""),
                         [c for c in data["changes"] if c["action"] in PENDING])


# -- mislukte wijzigingen: valideren en niet eindeloos herhalen (rapport 3, R7) -----------------
FAIL_GIVE_UP = 3
FAIL_RETRY_HOURS = 24
FAILURES_STATE = "provisioning_failures"


def failure_key(c: Change) -> tuple[str, str]:
    import hashlib
    import json as _json

    key = f"{c.instance_id}|{c.section}|{c.key}"
    fprint = hashlib.sha256(_json.dumps([c.title_nl, c.diff], sort_keys=True, default=str)
                            .encode()).hexdigest()[:16]
    return key, fprint


def _failures(ctx: AppContext) -> dict[str, Any]:
    return dict(ctx.state_get(FAILURES_STATE, {}) or {})


def gave_up(ctx: AppContext, key: str, fprint: str) -> bool:
    """Na 3 weigeringen met dezelfde inhoud pas na een dag (of na een configwijziging, die
    een andere fingerprint geeft) opnieuw."""
    from datetime import UTC, datetime, timedelta

    f = _failures(ctx).get(key)
    if not f or f.get("fingerprint") != fprint or int(f.get("count") or 0) < FAIL_GIVE_UP:
        return False
    try:
        last = datetime.fromisoformat(str(f.get("last")))
    except ValueError:
        return False
    if last.tzinfo is None:
        last = last.replace(tzinfo=UTC)
    return datetime.now(UTC) - last < timedelta(hours=FAIL_RETRY_HOURS)


def note_failure(ctx: AppContext, key: str, fprint: str, error: str, c: Change) -> None:
    data = _failures(ctx)
    prev = data.get(key) or {}
    count = int(prev.get("count") or 0) + 1 if prev.get("fingerprint") == fprint else 1
    data[key] = {"fingerprint": fprint, "count": count, "last": utcnow(), "error": error[:500],
                 "instance_id": c.instance_id or None, "section": c.section,
                 "title_nl": c.title_nl, "gave_up": count >= FAIL_GIVE_UP}
    ctx.state_set(FAILURES_STATE, data)


def clear_failure(ctx: AppContext, key: str) -> None:
    data = _failures(ctx)
    if data.pop(key, None) is not None:
        ctx.state_set(FAILURES_STATE, data)


def failing_items(ctx: AppContext) -> list[dict[str, Any]]:
    """Voor Koppelingen: wat Sonarr/Radarr weigert, met de echte foutmelding."""
    return [{"key": k, **v} for k, v in _failures(ctx).items()]


def spec_problem(specs: list[dict[str, Any]]) -> str | None:
    """Vang ongeldige custom formats af vóór het verzoek (Sonarr/Radarr weigert ze toch)."""
    for spec in specs or []:
        if spec.get("implementation") != "SizeSpecification":
            continue
        fields = spec.get("fields") or []
        vals = ({f.get("name"): f.get("value") for f in fields} if isinstance(fields, list)
                else dict(fields))
        lo, hi = float(vals.get("min") or 0), float(vals.get("max") or 0)
        if hi <= lo:
            return (f"Ongeldig custom format: grootte max ({hi:g} GB) moet groter zijn dan "
                    f"min ({lo:g} GB)")
    return None


# -- toepassen -----------------------------------------------------------------------------------
async def apply(ctx: AppContext, *, actor: str, instances: list[str] | None = None,
                sections: list[str] | None = None, auto_only: bool = False,
                include_deletes: bool = True, kind: str = "apply") -> dict[str, Any]:
    """Voert de wijzigingen uit (per instantie de secties in vaste volgorde), daarna een
    nieuwe plan-ronde als verificatie. Seerr wordt hier NIET toegepast (eigen bevestiging)."""
    lock: asyncio.Lock = ctx.services.setdefault("provisioning.lock", asyncio.Lock())
    sections = [s for s in (sections or store.SECTIONS) if s != "seerr"]
    async with lock:
        run_id = store.run_start(ctx, kind, actor, sections, instances or [])
        plan = await build_plan(ctx, instances=instances, sections=sections, actor=actor)
        applied: list[dict[str, Any]] = []
        failed: list[dict[str, Any]] = []
        order = {s: i for i, s in enumerate(store.SECTIONS)}
        todo = sorted(plan.pending(), key=lambda c: (c.instance_id, order[c.section]))
        for c in todo:
            if auto_only and (not c.auto or c.action == "delete"):
                continue
            if auto_only and c.section not in store.ESSENTIAL_SECTIONS \
                    and not store.is_provisioned(ctx, c.instance_id):
                continue  # nog nooit ingericht: achtergrond houdt het bij de essentials
            if c.action == "delete" and not include_deletes:
                continue
            fkey, fprint = failure_key(c)
            if auto_only and gave_up(ctx, fkey, fprint):
                continue  # 3× geweigerd met dezelfde inhoud: niet elke 10 min opnieuw (R7)
            try:
                assert c.run is not None
                await c.run()
            except (ArrError, Exception) as exc:  # noqa: BLE001 - per wijziging rapporteren
                msg = exc.message if isinstance(exc, ArrError) else str(exc)
                failed.append({**c.view(), "error": msg})
                note_failure(ctx, fkey, fprint, msg, c)
                audit(ctx.db, actor=actor, action=f"provisioning.{c.section}.failed",
                      target_type="provisioning", target_id=c.key,
                      instance_id=c.instance_id or None, reason=msg)
                continue
            clear_failure(ctx, fkey)
            applied.append(c.view())
            audit(ctx.db, actor=actor, action=f"provisioning.{c.section}.{c.action}",
                  target_type="provisioning", target_id=c.key,
                  instance_id=c.instance_id or None, reason=c.title_nl,
                  old_value={d["field"]: d["old"] for d in c.diff} or None,
                  new_value={d["field"]: d["new"] for d in c.diff} or None,
                  undo_hint="Configuratie in MediaGuard aanpassen en opnieuw toepassen")
        touched = sorted({c["instance_id"] for c in applied if c["instance_id"]})
        for iid in touched:
            await set_instance_defaults(ctx, iid)
        if any(c["section"] == "prowlarr" for c in applied):
            from mediaguard.provisioning.prowlarr_sync import trigger_sync

            await trigger_sync(ctx)
        verify = await build_plan(ctx, instances=instances, sections=sections, actor=actor)
        persist_status(ctx, verify)
        remaining = [c.view() for c in verify.pending()]
        if kind == "apply" and not auto_only:
            for iid, view in verify.instances.items():
                if not any(s.get("status") == "error" for s in view.get("sections", {}).values()):
                    store.mark_initialized(ctx, iid)
        blocked = [c.view() for c in verify.changes if c.action in ("blocked", "error")]
        status = "failed" if failed and not applied else "partial" if (
            failed or remaining or blocked) else "succeeded"
        summary = {"applied": len(applied), "failed": len(failed),
                   "remaining": len(remaining), "blocked": len(blocked),
                   "planned": len(todo)}
        store.run_finish(ctx, run_id, status, summary, applied + failed)
        if applied or failed:
            ctx.events.publish("provisioning.applied", {"run_id": run_id, **summary})
        return {"run_id": run_id, "status": status, "summary": summary, "applied": applied,
                "failed": failed, "remaining": remaining, "blocked": blocked,
                "verify": verify.view()}


async def set_instance_defaults(ctx: AppContext, iid: str) -> None:
    """Na inrichten: standaardprofiel/-rootmap van de instantie (voor 'serie toevoegen' in
    de MediaGuard-UI) invullen als die nog leeg of ongeldig zijn."""
    row = instance_row(ctx, iid)
    if row is None:
        return
    try:
        async with arr_client(ctx, iid) as client:
            profiles = await client.quality_profiles()
            roots = await client.root_folders()
    except ArrError:
        return
    ids = {int(p["id"]): p["name"] for p in profiles}
    changes: dict[str, Any] = {}
    tpls = T.templates_for(row["media_type"])
    if tpls and (row["default_quality_profile_id"] is None or
                 int(row["default_quality_profile_id"]) not in ids):
        want = T.owned_name(T.QUALITY_TEMPLATES[tpls[0]]["name"])
        pid = next((i for i, n in ids.items() if n == want), None)
        if pid is not None:
            changes["default_quality_profile_id"] = pid
    paths = {str(r.get("path", "")).rstrip("/") for r in roots}
    cfg = store.load(ctx)
    root = store.root_folder_for(ctx, iid, row["media_type"], cfg)
    current = str(row["default_root_folder"] or "").rstrip("/")
    if root in paths and not current:
        changes["default_root_folder"] = root
    elif paths and current not in paths and root not in paths:
        # Gemigreerde instantie met eigen mappen (bv. '/data/media/Anime Films'): standaard
        # de map die de instantie al gebruikt, niet het sjabloonpad
        from mediaguard.provisioning.seerr_sync import pick_root

        picked = pick_root(paths, row["media_type"])
        if picked:
            changes["default_root_folder"] = picked
    if changes:
        update_instance(ctx, iid, changes)
