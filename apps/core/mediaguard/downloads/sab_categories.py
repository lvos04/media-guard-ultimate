"""SABnzbd-categorieën die Sonarr/Radarr gebruiken automatisch aanmaken.

qBittorrent maakt een onbekende categorie zelf aan, SABnzbd niet: een NZB met categorie
``tv-anime`` die SABnzbd niet kent komt in de standaardmap terecht. Sonarr/Radarr vinden de
download dan niet terug ("Category does not exist" bij de test, en downloads die
'verdwijnen' zonder import). MediaGuard leest per SABnzbd welke categorieën de beheerde
Sonarr/Radarr-instanties gebruiken en maakt de ontbrekende aan via de SABnzbd-API
(``mode=set_config&section=categories&keyword=…``; SABnzbd maakt via ``handle_cat_api`` een
nieuwe ``ConfigCat`` aan als die nog niet bestaat). Bestaande categorieën worden nooit
gewijzigd of verwijderd.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Any

from mediaguard.db import utcnow

if TYPE_CHECKING:
    from mediaguard.context import AppContext

LOGGER = logging.getLogger(__name__)
ACTOR = "svc:sab-categories"
STATE_KEY = "sab_categories"
NOTIFY_KEY = "sab-categories-created"


@dataclass
class SabTarget:
    host: str
    port: int
    cfg_name: str | None = None  # naam in MediaGuard's downloadclientconfig
    categories: dict[str, set[str]] = field(default_factory=dict)  # categorie -> instanties


def _norm_host(host: Any) -> str:
    return str(host or "").strip().lower().rstrip("/")


def _category_of(app: str, client: dict[str, Any]) -> str:
    from mediaguard.provisioning.engine import category_field, fget

    value = fget(client, category_field(app))
    if not value:
        value = fget(client, "category")
    return str(value or "").strip()


async def _collect(ctx: AppContext) -> dict[tuple[str, int], SabTarget]:
    """Alle SABnzbd-clients in de beheerde Sonarr/Radarr-instanties, gegroepeerd op host:poort,
    met de categorieën die ze gebruiken."""
    from mediaguard.instances.registry import arr_client, list_instances
    from mediaguard.provisioning.engine import fget

    targets: dict[tuple[str, int], SabTarget] = {}
    for inst in list_instances(ctx):
        if inst.app not in ("sonarr", "radarr"):
            continue
        try:
            async with arr_client(ctx, inst.id) as client:
                clients = await client.provider_list("downloadclient")
        except Exception as exc:  # noqa: BLE001 - instantie onbereikbaar: volgende keer
            LOGGER.info("SAB-categorieën: %s niet leesbaar: %s", inst.id, exc)
            continue
        for dc in clients or []:
            if str(dc.get("implementation") or "").lower() != "sabnzbd":
                continue
            host, port = _norm_host(fget(dc, "host")), int(fget(dc, "port") or 0)
            cat = _category_of(inst.app, dc)
            if not host or not port or not cat:
                continue
            t = targets.setdefault((host, port), SabTarget(host, port))
            t.categories.setdefault(cat.lower(), set()).add(inst.id)
    return targets


def _match_cfg(ctx: AppContext, targets: dict[tuple[str, int], SabTarget]) -> None:
    from mediaguard.provisioning import store

    cfg = store.load(ctx)
    for dc in cfg.download_clients:
        if dc.implementation != "Sabnzbd":
            continue
        t = targets.get((_norm_host(dc.host), int(dc.port)))
        if t is not None:
            t.cfg_name = dc.name


def _sab(ctx: AppContext, name: str) -> Any | None:
    """SabClient voor een geconfigureerde SABnzbd, of None zonder API-sleutel."""
    from mediaguard.downloads.clients import SabClient
    from mediaguard.provisioning import store

    cfg = store.load(ctx)
    dc = next((d for d in cfg.download_clients if d.name == name), None)
    key = ctx.secrets.get(store.dc_secret(name, "api_key")) if dc else None
    if dc is None or not key:
        return None
    return SabClient(ctx, dc, key)


async def sab_category_names(sab: Any) -> set[str]:
    data = await sab._api(mode="get_config", section="categories")
    cats = ((data or {}).get("config") or {}).get("categories") or []
    if isinstance(cats, dict):  # oudere SABnzbd-versies: {naam: {...}}
        return {str(k).lower() for k in cats}
    return {str(c.get("name") or "").lower() for c in cats if isinstance(c, dict)}


async def create_category(sab: Any, name: str) -> None:
    # pp/script/priority niet meesturen: SABnzbd vult de standaard ('Default' = overnemen)
    await sab._api(mode="set_config", section="categories", keyword=name, name=name,
                   dir=name)


async def ensure(ctx: AppContext) -> dict[str, Any]:
    """Ontbrekende SABnzbd-categorieën aanmaken. Geeft per SABnzbd wat er gebeurde en
    bewaart de toestand (voor 'Wacht op jou' en de downloadbewaking)."""
    from mediaguard.api.system import notify, resolve_notifications
    from mediaguard.audit import audit

    targets = await _collect(ctx)
    _match_cfg(ctx, targets)
    created: list[str] = []
    missing_no_key: list[dict[str, Any]] = []
    errors: list[dict[str, Any]] = []
    still_missing: dict[str, list[str]] = {}  # instance_id -> categorieën
    for t in targets.values():
        sab = _sab(ctx, t.cfg_name) if t.cfg_name else None
        if sab is None:
            # zonder sleutel kan MediaGuard niet kijken; alleen melden als de test het
            # aantoonbaar zegt (zie note_test_failure) — hier: niets beweren
            missing_no_key.append({"host": t.host, "port": t.port,
                                   "categories": sorted(t.categories)})
            continue
        try:
            have = await sab_category_names(sab)
        except Exception as exc:  # noqa: BLE001
            errors.append({"host": t.host, "port": t.port, "error": str(exc)[:200]})
            for cat, insts in t.categories.items():
                for iid in insts:
                    still_missing.setdefault(iid, []).append(cat)
            continue
        for cat, insts in sorted(t.categories.items()):
            if cat in have:
                continue
            try:
                await create_category(sab, cat)
            except Exception as exc:  # noqa: BLE001
                errors.append({"host": t.host, "port": t.port, "category": cat,
                               "error": str(exc)[:200]})
                for iid in insts:
                    still_missing.setdefault(iid, []).append(cat)
                continue
            created.append(cat)
            audit(ctx.db, actor=ACTOR, action="downloads.sab_category_created",
                  target_type="sabnzbd", target_id=f"{t.host}:{t.port}",
                  reason=f"Categorie '{cat}' ontbrak in SABnzbd (gebruikt door "
                         f"{', '.join(sorted(insts))})", new_value={"category": cat})
    state = {"checked_at": utcnow(), "created": created, "errors": errors,
             "no_key": missing_no_key, "missing": still_missing,
             "failed_tests": (ctx.state_get(STATE_KEY, {}) or {}).get("failed_tests", {})}
    if created:
        # de test meldt het niet meer zodra de categorie bestaat
        state["failed_tests"] = {}
        names = ", ".join(sorted(set(created)))
        notify(ctx, "info", f"SABnzbd: categorie{'ën' if len(set(created)) > 1 else ''} "
               f"{names} aangemaakt",
               "Sonarr/Radarr gebruikten deze categorie, maar SABnzbd kende hem niet. "
               "Downloads kwamen daardoor in de standaardmap en werden niet gevonden.",
               link="/inrichten", dedupe_key=NOTIFY_KEY)
        # downloads die door de ontbrekende categorie bleven liggen: meteen herstellen
        from mediaguard.downloads import recovery

        recovery.trigger(ctx)
    ctx.state_set(STATE_KEY, state)
    if not still_missing and not state["failed_tests"]:
        resolve_notifications(ctx, "sab-categories-missing")
    return state


# -- testresultaten ('Category does not exist') -------------------------------------------
CATEGORY_MISSING = "category does not exist"


def note_test_failure(ctx: AppContext, instance_id: str, message: str,
                      category: str | None) -> None:
    """Een clienttest die 'Category does not exist' meldt: onthouden voor 'Wacht op jou' als
    MediaGuard de categorie niet zelf kan aanmaken."""
    if CATEGORY_MISSING not in message.lower():
        return
    state = dict(ctx.state_get(STATE_KEY, {}) or {})
    failed = dict(state.get("failed_tests") or {})
    failed[instance_id] = {"category": category or "", "at": utcnow()}
    state["failed_tests"] = failed
    ctx.state_set(STATE_KEY, state)


def missing_text(ctx: AppContext, category: str | None) -> str:
    """Nederlandse uitleg bij 'Category does not exist'."""
    cat = f"'{category}'" if category else "van deze instantie"
    state = ctx.state_get(STATE_KEY, {}) or {}
    if state.get("no_key"):
        return (f"Categorie {cat} bestaat niet in SABnzbd — voeg de SABnzbd-API-sleutel toe bij "
                "Koppelingen, dan maakt MediaGuard hem automatisch aan")
    return f"Categorie {cat} bestaat niet in SABnzbd — MediaGuard maakt hem automatisch aan"


def clear_test_failure(ctx: AppContext, instance_id: str) -> None:
    state = dict(ctx.state_get(STATE_KEY, {}) or {})
    failed = dict(state.get("failed_tests") or {})
    if failed.pop(instance_id, None) is not None:
        state["failed_tests"] = failed
        ctx.state_set(STATE_KEY, state)


def missing_categories(ctx: AppContext) -> dict[str, list[str]]:
    """instance_id -> categorieën die (aantoonbaar) ontbreken in SABnzbd."""
    state = ctx.state_get(STATE_KEY, {}) or {}
    out: dict[str, list[str]] = {k: list(v) for k, v in (state.get("missing") or {}).items()}
    for iid, info in (state.get("failed_tests") or {}).items():
        cat = str((info or {}).get("category") or "")
        out.setdefault(iid, [])
        if cat and cat not in out[iid]:
            out[iid].append(cat)
    return out


def attention_items(ctx: AppContext) -> list[dict[str, Any]]:
    """Alleen een 'Wacht op jou'-regel als MediaGuard de categorie niet zelf kan aanmaken
    (geen SABnzbd-API-sleutel in Koppelingen)."""
    state = ctx.state_get(STATE_KEY, {}) or {}
    failed = state.get("failed_tests") or {}
    if not failed:
        return []
    cats = sorted({str((v or {}).get("category") or "") for v in failed.values()} - {""})
    label = ", ".join(cats) if cats else "van Sonarr/Radarr"
    return [{
        "title_nl": f"SABnzbd mist categorie {label}",
        "detail_nl": "Voeg de SABnzbd-API-sleutel toe bij Koppelingen → Downloads, dan maakt "
                     "MediaGuard de categorie zelf aan. Tot die tijd komen downloads in de "
                     "standaardmap en vindt Sonarr/Radarr ze niet terug.",
        "count": len(failed)}]
