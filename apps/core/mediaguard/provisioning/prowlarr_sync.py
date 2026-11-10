"""Prowlarr-modus: MediaGuard registreert elke instantie als Application in Prowlarr met de
juiste sync-categorieën en een tag ``anime`` of ``normal``. Prowlarr synchroniseert alleen
indexers met een overeenkomende tag; MediaGuard zet die tags ook op de Prowlarr-indexers
(alleen de twee eigen tags; andere tags blijven onaangeroerd)."""

from __future__ import annotations

import copy
import logging
import os
from typing import TYPE_CHECKING, Any

from mediaguard.instances.registry import apikey_secret
from mediaguard.provisioning import store
from mediaguard.provisioning import templates as T
from mediaguard.provisioning.engine import Change, diff_line, fget, fset, norm_url
from mediaguard.provisioning.prowlarr import ProwlarrError, prowlarr_client
from mediaguard.provisioning.store import MASK, ProvisioningConfig

if TYPE_CHECKING:
    from mediaguard.context import AppContext

LOGGER = logging.getLogger(__name__)
TAG_ANIME, TAG_NORMAL = "anime", "normal"


def _change(action: str, key: str, title: str, iid: str = "", **kw: Any) -> Change:
    return Change("prowlarr", action, key, title, instance_id=iid, **kw)


def instance_tag(media_type: str) -> str:
    return TAG_ANIME if media_type in T.ANIME_TYPES else TAG_NORMAL


def _reachable_base_url(cfg: ProvisioningConfig, inst: dict[str, Any]) -> str:
    """Adres waarop Prowlarr (een andere app/container) deze instantie bereikt. Nooit het
    interne 127.0.0.1-adres uit het alles-in-één-image: dat is voor Prowlarr zijn eigen
    container."""
    from urllib.parse import urlsplit

    from mediaguard.instances.registry import _public_from_host

    for url in (cfg.prowlarr.instance_urls.get(inst["id"]), inst.get("public_url"),
                inst["url"]):
        host = (urlsplit(url).hostname or "") if url else ""
        if url and host not in ("127.0.0.1", "localhost", "::1", "0.0.0.0"):
            return url.rstrip("/")
    lan = os.environ.get("MG_PUBLIC_HOST", "").strip()
    derived = _public_from_host(inst["id"], lan) if lan else None
    return (derived or inst["url"]).rstrip("/")


def app_fields(ctx: AppContext, cfg: ProvisioningConfig, inst: dict[str, Any]) -> dict:
    anime = inst["media_type"] in T.ANIME_TYPES
    out: dict[str, Any] = {
        "prowlarrUrl": (cfg.prowlarr.url_from_arr or cfg.prowlarr.url).rstrip("/"),
        "baseUrl": _reachable_base_url(cfg, inst),
        "apiKey": ctx.secrets.get(apikey_secret(inst["id"])),
    }
    if inst["app"] == "sonarr":
        out["syncCategories"] = list(T.PROWLARR_SONARR)
        out["animeSyncCategories"] = list(T.SONARR_ANIME_CATEGORIES)
    else:
        out["syncCategories"] = list(T.PROWLARR_RADARR) + (
            list(T.RADARR_ANIME_EXTRA) if anime else [])
    return out


async def ensure_tags(client: Any) -> dict[str, int]:
    tags = {str(t["label"]).lower(): int(t["id"]) for t in await client.tags()}
    for label in (TAG_ANIME, TAG_NORMAL):
        if label not in tags:
            rec = await client.tag_create(label)
            tags[label] = int(rec["id"])
    return tags


def indexer_scope(cfg: ProvisioningConfig, idx: dict[str, Any]) -> str:
    explicit = cfg.prowlarr.indexer_scopes.get(str(idx.get("id")))
    if explicit:
        return explicit
    return "anime" if T.is_anime_name(idx.get("name"), idx.get("definitionName")) else "both"


async def plan_prowlarr(ctx: AppContext, cfg: ProvisioningConfig,  # noqa: C901
                        rows: list[dict[str, Any]], actor: str) -> list[Change]:
    if cfg.indexer_mode != "prowlarr":
        return []
    if not cfg.prowlarr.url or not ctx.secrets.has(store.PROWLARR_SECRET):
        return [_change("needs_input", "prowlarr", "Prowlarr-URL en API-sleutel ontbreken",
                        message_nl="Vul bij Inrichten → Indexers de Prowlarr-URL en "
                                   "API-sleutel in.")]
    client = prowlarr_client(ctx)
    try:
        apps = await client.applications()
        schema = await client.application_schema()
        tags = {str(t["label"]).lower(): int(t["id"]) for t in await client.tags()}
        indexers = await client.indexers() if cfg.prowlarr.manage_indexer_tags else []
    except ProwlarrError as exc:
        return [_change("error", "prowlarr", f"Prowlarr lezen mislukt: {exc.message}",
                        message_nl=exc.message)]
    out: list[Change] = []
    missing_tags = [t for t in (TAG_ANIME, TAG_NORMAL) if t not in tags]
    if missing_tags:
        async def mk_tags() -> None:
            await ensure_tags(client)
        out.append(_change("create", "tags", f"Prowlarr-tags {', '.join(missing_tags)} "
                           "aanmaken", run=mk_tags))
    for inst in rows:
        iid = inst["id"]
        mode = store.resolve_mode(ctx, iid, cfg)
        name = T.owned_name(inst["name"])
        if mode == "hold":
            out.append(_change("info", name, f"{inst['name']}: wacht op 'Optimaliseren na "
                               "migratie'; nog niet in Prowlarr geregistreerd.", iid))
            continue
        if inst["shadow_mode"]:
            out.append(_change("info", name, f"{inst['name']} staat in schaduwmodus; Prowlarr "
                               "wordt pas na de cutover gekoppeld (anders twee grabbers).",
                               iid))
            continue
        if "prowlarr" in cfg.instance(iid).disabled_sections:
            continue
        if not ctx.secrets.has(apikey_secret(iid)):
            out.append(_change("needs_input", name, f"API-sleutel van {inst['name']} "
                               "ontbreekt", iid))
            continue
        desired = app_fields(ctx, cfg, inst)
        impl = "Sonarr" if inst["app"] == "sonarr" else "Radarr"
        cur = next((a for a in apps if a.get("name") == name), None)
        if cur is None:
            other = next((a for a in apps if a.get("implementation") == impl and
                          norm_url(fget(a, "baseUrl")) == norm_url(desired["baseUrl"])), None)
            if other is not None and "prowlarr" not in cfg.instance(iid).full_manage:
                out.append(_change("info", name, f"Prowlarr-app '{other.get('name')}' (door "
                                   f"jou beheerd) wijst al naar {inst['name']}; MediaGuard "
                                   "laat die ongemoeid.", iid, owned=False))
                continue
            cur = other
        tag_label = instance_tag(inst["media_type"])
        template = next((s for s in schema if s.get("implementation") == impl), None)
        if template is None and cur is None:
            out.append(_change("error", name, f"Prowlarr biedt geen {impl}-applicatie aan",
                               iid))
            continue
        shash = store.secret_hash(desired["apiKey"])
        if cur is None:
            async def create(inst: Any = inst, template: Any = template, name: str = name,
                             desired: Any = desired, tag_label: str = tag_label,
                             shash: str = shash) -> None:
                tag_ids = await ensure_tags(client)
                body = copy.deepcopy(template)
                body.pop("presets", None)
                body.pop("id", None)
                body.update(name=name, implementation=body.get("implementation"),
                            syncLevel=cfg.prowlarr.sync_level, tags=[tag_ids[tag_label]])
                for k, v in desired.items():
                    fset(body, k, v, add=True)
                rec = await client.application_create(body)
                store.item_set(ctx, inst["id"], "prowlarr", name, remote_id=rec.get("id"),
                               secret=shash, actor=actor)
            out.append(_change("create", name, f"{inst['name']} als applicatie in Prowlarr "
                               f"registreren (tag '{tag_label}')", iid,
                               diff=[diff_line(k, k, None, v) for k, v in desired.items()],
                               run=create))
            continue
        diffs = []
        for k, v in desired.items():
            have = fget(cur, k)
            if k == "apiKey":
                if have not in (MASK, None, "") and have != v:
                    diffs.append(diff_line(k, k, have, v))
                continue
            if have != v:
                diffs.append(diff_line(k, k, have, v))
        rec = store.items(ctx, iid, "prowlarr").get(name) or {}
        if rec.get("secret_hash") and rec["secret_hash"] != shash:
            diffs.append(diff_line("apiKey", "API-sleutel", "***", "*** (gewijzigd)"))
        if cur.get("syncLevel") != cfg.prowlarr.sync_level:
            diffs.append(diff_line("syncLevel", "Synchronisatie", cur.get("syncLevel"),
                                   cfg.prowlarr.sync_level))
        if tags.get(tag_label) not in (cur.get("tags") or []):
            diffs.append(diff_line("tags", "Tags", cur.get("tags"), [tag_label]))
        if diffs:
            async def update(inst: Any = inst, cur: Any = cur, desired: Any = desired,
                             tag_label: str = tag_label, shash: str = shash) -> None:
                tag_ids = await ensure_tags(client)
                body = copy.deepcopy(cur)
                body["syncLevel"] = cfg.prowlarr.sync_level
                body["tags"] = [tag_ids[tag_label]]
                for k, v in desired.items():
                    fset(body, k, v, add=True)
                await client.application_update(body)
                store.item_set(ctx, inst["id"], "prowlarr", str(cur.get("name")),
                               remote_id=cur.get("id"), secret=shash, actor=actor)
            out.append(_change("update", str(cur.get("name")), f"Prowlarr-applicatie voor "
                               f"{inst['name']} herstellen", iid, diff=diffs, run=update))
    # indexer-tags in Prowlarr
    for idx in indexers:
        scope = indexer_scope(cfg, idx)
        want_labels = {TAG_ANIME, TAG_NORMAL} if scope == "both" else (
            {TAG_ANIME} if scope == "anime" else {TAG_NORMAL})
        cur_ids = set(idx.get("tags") or [])
        have_labels = {lbl for lbl in (TAG_ANIME, TAG_NORMAL) if tags.get(lbl) in cur_ids}
        if have_labels == want_labels:
            continue

        async def tag_idx(idx: Any = idx, want_labels: set = want_labels) -> None:
            tag_ids = await ensure_tags(client)
            body = copy.deepcopy(idx)
            keep = [t for t in body.get("tags") or [] if t not in set(tag_ids.values())]
            body["tags"] = keep + [tag_ids[lbl] for lbl in sorted(want_labels)]
            await client.indexer_update(body)
        out.append(_change("update", f"indexer:{idx.get('id')}",
                           f"Prowlarr-indexer '{idx.get('name')}' → bereik {scope}",
                           diff=[diff_line("tags", "Tags", sorted(have_labels),
                                           sorted(want_labels))], owned=False, run=tag_idx))
    return out


async def trigger_sync(ctx: AppContext) -> None:
    try:
        await prowlarr_client(ctx).sync()
    except ProwlarrError as exc:
        LOGGER.warning("Prowlarr-sync starten mislukt: %s", exc.message)
