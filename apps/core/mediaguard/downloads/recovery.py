"""Vangnet: voltooide downloads die Sonarr/Radarr nooit importeerden, alsnog importeren.

Oorzaak maakt niet uit (ontbrekende/verkeerde categorie, verkeerd pad, mislukte of
geblokkeerde import, herstart van de client): elke 30 minuten — en na elke wijziging aan
downloadclients/categorieën — zoekt MediaGuard in SABnzbd en qBittorrent naar voltooide
downloads die geen enkele Sonarr/Radarr heeft geïmporteerd en die niet meer in een wachtrij
staan, en herstelt ze zo:

1. Eigenaar bepalen via de grab in de historie van een MediaGuard-instantie: eerst op
   download-ID (nzo_id/hash, eenduidig), anders op exact dezelfde releasetitel. Meer dan één
   kandidaat → de AI kiest tussen kandidaten die allemaal bij een echte grab horen, zonder
   AI blijft het liggen en wordt het gemeld.
2. Staat de download in de prullenbak van de opruimer, dan eerst terugzetten.
3. Importeren via ``DownloadedEpisodesScan``/``DownloadedMoviesScan`` met pad +
   ``downloadClientId`` (Sonarr/Radarr koppelt dan zelf aan de juiste aflevering/film en
   past zijn eigen identiteits- en kwaliteitscontroles toe; MediaGuard versoepelt niets).
4. Gelukt → releases die MediaGuard eerder ten onrechte als 'verdwenen' blokkeerde, gaan
   weer van de blokkadelijst.

Er wordt nooit iets verwijderd. De opruimer gooit een voltooid-maar-niet-geïmporteerd item
pas weg als dit vangnet het heeft geprobeerd (status 'geïmporteerd' of 'niet te herstellen').
"""

from __future__ import annotations

import asyncio
import contextlib
import logging
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import TYPE_CHECKING, Any

from mediaguard import emergency, taskwatch
from mediaguard.db import utcnow

if TYPE_CHECKING:
    from mediaguard.context import AppContext
    from mediaguard.downloads.clients import ClientItem

LOGGER = logging.getLogger(__name__)
ACTOR = "svc:download-recovery"
STATE_KEY = "download_recovery"
INTERVAL_SECONDS = 30 * 60
SETTLE = timedelta(hours=1)          # Sonarr/Radarr eerst zelf laten importeren
HISTORY_DAYS = 30
MAX_ATTEMPTS = 2
FINAL = ("imported", "not_recoverable")


class _All(set[str]):
    """'Alle categorieën' voor de clientweergave (ook SABnzbd's standaardcategorie '*')."""

    def __contains__(self, _item: object) -> bool:
        return True


def _state(ctx: AppContext) -> dict[str, Any]:
    return dict(ctx.state_get(STATE_KEY, {}) or {})


def status_of(ctx: AppContext, key: str) -> str | None:
    """Herstelstatus van een download (download-ID of pad); None = nog niet geprobeerd."""
    entry = (_state(ctx).get("items") or {}).get(key.upper()) or \
        (_state(ctx).get("items") or {}).get(key)
    return entry.get("status") if entry else None


def janitor_may_remove(ctx: AppContext, key: str) -> bool:
    """De opruimer mag een voltooid-maar-niet-geïmporteerd item pas weggooien als dit
    vangnet het heeft geprobeerd."""
    if not bool(ctx.config.value("downloads.recovery_enabled")):
        return True
    return status_of(ctx, key) in FINAL


def register_settings() -> None:
    from mediaguard.downloads.janitor import CATEGORY
    from mediaguard.settings_registry import REGISTRY, SettingDef

    REGISTRY.register(SettingDef(
        "downloads.recovery_enabled", "bool", True, CATEGORY,
        "Niet-geïmporteerde downloads herstellen", "Recover unimported downloads",
        help_nl="Elke 30 minuten zoekt MediaGuard voltooide downloads die Sonarr/Radarr nooit "
                "importeerden (bv. door een ontbrekende SABnzbd-categorie) en importeert ze "
                "alsnog als duidelijk is waar ze bij horen. Er wordt nooit iets verwijderd.",
        effect_nl="De opruimer gooit zulke downloads pas weg nadat herstel is geprobeerd."))


# -- verzamelen ------------------------------------------------------------------------------
async def _grabs(ctx: AppContext) -> tuple[dict[str, dict[str, Any]],
                                           dict[str, list[dict[str, Any]]],
                                           set[str], set[str]]:
    """(grab per download-ID, grabs per titel, geïmporteerde ID's, ID's in een wachtrij)
    over alle MediaGuard-instanties; de oude live installaties tellen mee als eigenaar
    (hun downloads worden nooit door MediaGuard geïmporteerd)."""
    from mediaguard.instances.registry import arr_client, list_instances
    from mediaguard.migration.sources import list_sources, source_client

    since = (datetime.now(UTC) - timedelta(days=HISTORY_DAYS)).isoformat(timespec="seconds")
    by_id: dict[str, dict[str, Any]] = {}
    by_title: dict[str, list[dict[str, Any]]] = {}
    imported: set[str] = set()
    queued: set[str] = set()

    async def scan(client: Any, instance_id: str | None, app: str) -> None:
        for q in await client.queue(all_pages=True) or []:
            if q.get("downloadId"):
                queued.add(str(q["downloadId"]).upper())
        for h in await client.history_since(since) or []:
            did = str(h.get("downloadId") or "").upper()
            ev = str(h.get("eventType") or "")
            if ev in ("downloadFolderImported", "3") and did:
                imported.add(did)
            elif ev in ("grabbed", "1"):
                g = {"instance_id": instance_id, "app": app, "download_id": did,
                     "title": str(h.get("sourceTitle") or ""), "series_id": h.get("seriesId"),
                     "episode_id": h.get("episodeId"), "movie_id": h.get("movieId"),
                     "date": h.get("date")}
                if did:
                    by_id.setdefault(did, g)
                if g["title"]:
                    by_title.setdefault(g["title"].lower(), []).append(g)

    for inst in list_instances(ctx):
        with contextlib.suppress(Exception):
            async with arr_client(ctx, inst.id) as client:
                await scan(client, inst.id, inst.app)
    for s in list_sources(ctx):
        if s.get("kind") != "live":
            continue
        with contextlib.suppress(Exception):
            async with source_client(ctx, s["id"]) as client:
                await scan(client, None, str(s.get("app") or ""))
    return by_id, by_title, imported, queued


def _same_target(a: dict[str, Any], b: dict[str, Any]) -> bool:
    return (a["instance_id"], a.get("series_id"), a.get("movie_id")) == \
        (b["instance_id"], b.get("series_id"), b.get("movie_id"))


async def _owner(ctx: AppContext, item: ClientItem, by_id: dict[str, dict[str, Any]],
                 by_title: dict[str, list[dict[str, Any]]]
                 ) -> tuple[dict[str, Any] | None, str]:
    """(grab, uitleg). None = geen eenduidige eigenaar."""
    g = by_id.get(item.id.upper())
    if g is not None:
        return g, "download-ID"
    cands = by_title.get(item.name.lower()) or []
    uniq: list[dict[str, Any]] = []
    for c in cands:
        if not any(_same_target(c, u) for u in uniq):
            uniq.append(c)
    if len(uniq) == 1:
        return uniq[0], "releasetitel"
    if not uniq:
        return None, "geen grab gevonden in de laatste 30 dagen"
    from mediaguard.delivery import ai

    labels = [f"{u['instance_id']}:{u.get('series_id') or u.get('movie_id')}" for u in uniq]
    dec = await ai.decide(
        ctx, "download_recovery",
        f"Bij welke grab hoort de voltooide download '{item.name}'?", labels + ["geen"],
        {"download": item.name, "grabs": uniq}, fallback="geen",
        fallback_reason_nl="meerdere grabs met dezelfde titel; zonder AI niet eenduidig")
    if dec.choice == "geen":
        return None, dec.reason_nl
    chosen = uniq[labels.index(dec.choice)]
    return chosen, f"gekozen door {dec.by}: {dec.reason_nl}"


def _from_trash(ctx: AppContext, path: str) -> tuple[bool, str]:
    """Download die de opruimer al in de prullenbak zette: terugzetten."""
    from mediaguard.downloads import janitor

    row = ctx.db.fetch_one(
        "SELECT id FROM download_janitor_trash WHERE original_path=? AND purged_at IS NULL "
        "AND restored_at IS NULL ORDER BY id DESC LIMIT 1", (path,))
    if row is None:
        return False, "bestanden bestaan niet meer"
    try:
        janitor.restore(ctx, int(row["id"]), actor=ACTOR)
    except (ValueError, OSError, KeyError) as exc:
        return False, f"terugzetten uit de prullenbak mislukt: {exc}"
    return True, "uit de prullenbak teruggezet"


async def _import(ctx: AppContext, owner: dict[str, Any], item: ClientItem) -> int | None:
    from mediaguard.instances.registry import arr_client

    name = "DownloadedEpisodesScan" if owner["app"] == "sonarr" else "DownloadedMoviesScan"
    async with arr_client(ctx, owner["instance_id"]) as client:
        res = await client.command(name, path=item.path, downloadClientId=item.id,
                                   importMode="Auto")
    cid = res.get("id") if isinstance(res, dict) else None
    return int(cid) if cid else None


async def _try_manual(ctx: AppContext, owner: dict[str, Any], item: ClientItem) -> int | None:
    """Eerst de ManualImport-route (werkt ook bij 'matched by ID / automatic import not
    possible'); None = niet gelukt, dan valt de ronde terug op de map-scan."""
    from mediaguard.activity.manual_import import try_import

    if not item.id:
        return None
    try:
        res = await try_import(ctx, owner["instance_id"], item.id,
                               movie_id=owner.get("movie_id"), series_id=owner.get("series_id"),
                               episode_ids=[int(owner["episode_id"])]
                               if owner.get("episode_id") and owner.get("app") == "radarr"
                               else None, actor=ACTOR)
    except Exception:  # noqa: BLE001 - client kent de download niet (meer): map-scan
        return None
    # loopt de ManualImport nog, dan geen tweede import via de map-scan ernaast
    return int(res.get("count") or 1) if res.get("imported") or res.get("pending") else None


async def _unblocklist(ctx: AppContext, owner: dict[str, Any], release: str) -> int:
    """Releases die MediaGuard zelf als 'verdwenen' blokkeerde weer vrijgeven."""
    from mediaguard.instances.registry import arr_client

    marked = ctx.db.fetch_one(
        "SELECT 1 AS x FROM audit_events WHERE action='download.marked_failed' AND "
        "instance_id=? AND old_value LIKE ? LIMIT 1",
        (owner["instance_id"], f'%{release.replace("%", "")[:120]}%'))
    if marked is None:
        return 0
    removed = 0
    async with arr_client(ctx, owner["instance_id"]) as client:
        data = await client.get("blocklist", page=1, pageSize=500) or {}
        for rec in (data.get("records") if isinstance(data, dict) else data) or []:
            if str(rec.get("sourceTitle") or "").lower() == release.lower():
                with contextlib.suppress(Exception):
                    await client.delete(f"blocklist/{int(rec['id'])}")
                    removed += 1
    return removed


# -- één ronde -------------------------------------------------------------------------------
async def run_once(ctx: AppContext) -> dict[str, Any]:
    from mediaguard.api.system import notify
    from mediaguard.audit import audit
    from mediaguard.downloads import janitor

    if not bool(ctx.config.value("downloads.recovery_enabled")):
        return {"skipped": "uitgeschakeld"}
    if emergency.skip(ctx, "downloadherstel"):
        return emergency.skipped("downloadherstel")
    snap, _clients = await janitor.gather(ctx, managed_override=_All())
    with contextlib.suppress(Exception):
        for c in ctx.services.pop("_janitor_open", []) or []:
            await c.__aexit__(None, None, None)
    by_id, by_title, imported, queued = await _grabs(ctx)
    state = _state(ctx)
    items: dict[str, Any] = dict(state.get("items") or {})
    now = datetime.now(UTC)
    newly_imported: list[str] = []
    newly_failed: list[str] = []
    for it in snap.items:
        if emergency.active(ctx):
            break  # noodstop tijdens de ronde: niets meer terugzetten of importeren
        key = it.id.upper()
        entry = dict(items.get(key) or {})
        if key in imported:
            if entry and entry.get("status") != "imported" and entry.get("attempts"):
                entry.update(status="imported", at=utcnow())
                newly_imported.append(it.name)
                owner = entry.get("owner") or {}
                if owner.get("instance_id"):
                    with contextlib.suppress(Exception):
                        entry["unblocked"] = await _unblocklist(ctx, owner, it.name)
                audit(ctx.db, actor=ACTOR, action="downloads.recovered", target_type="download",
                      target_id=key, instance_id=owner.get("instance_id"),
                      reason=f"Alsnog geïmporteerd: {it.name}")
                items[key] = entry
            continue
        if it.active or it.failed or key in queued or it.completed_at is None or \
                now - it.completed_at < SETTLE or entry.get("status") in FINAL:
            continue
        owner, how = await _owner(ctx, it, by_id, by_title)
        if owner is None or owner.get("instance_id") is None:
            why = how if owner is None else "hoort bij je oude installatie"
            entry.update(status="not_recoverable", reason_nl=why, name=it.name,
                         client=it.client, category=it.category, at=utcnow())
            if why != "hoort bij je oude installatie":
                newly_failed.append(f"{it.name} ({why})")
            items[key] = entry
            continue
        if int(entry.get("attempts") or 0) >= MAX_ATTEMPTS:
            entry.update(status="not_recoverable", at=utcnow(),
                         reason_nl="Sonarr/Radarr wilde de download na 2 pogingen niet "
                                   "importeren (eigen controles)")
            newly_failed.append(f"{it.name} (Sonarr/Radarr weigert)")
            items[key] = entry
            continue
        if not it.path or not Path(it.path).exists():
            ok, why = _from_trash(ctx, it.path or "")
            if not ok:
                entry.update(status="not_recoverable", reason_nl=why, name=it.name, at=utcnow())
                newly_failed.append(f"{it.name} ({why})")
                items[key] = entry
                continue
            entry["restored_from_trash"] = True
        manual = await _try_manual(ctx, owner, it)
        if manual is not None:
            how = f"{how}, ManualImport ({manual} bestand(en))"
        try:
            cmd = None if manual is not None else await _import(ctx, owner, it)
        except Exception as exc:  # noqa: BLE001 - volgende ronde opnieuw
            entry.update(status="error", reason_nl=f"importopdracht mislukt: {exc}"[:300],
                         attempts=int(entry.get("attempts") or 0) + 1, at=utcnow())
            items[key] = entry
            continue
        entry.update(status="importing", name=it.name, client=it.client, how=how,
                     category=it.category, command_id=cmd, at=utcnow(),
                     attempts=int(entry.get("attempts") or 0) + 1,
                     owner={k: owner.get(k) for k in ("instance_id", "app", "series_id",
                                                     "episode_id", "movie_id")})
        audit(ctx.db, actor=ACTOR, action="downloads.recovery_import", target_type="download",
              target_id=key, instance_id=owner["instance_id"],
              reason=f"Niet-geïmporteerde download aangeboden aan Sonarr/Radarr ({how})",
              new_value={"path": it.path, "name": it.name})
        items[key] = entry
    # beperken in omvang: nieuwste 2000
    if len(items) > 2000:
        items = dict(sorted(items.items(), key=lambda kv: kv[1].get("at") or "")[-2000:])
    result = {"checked_at": utcnow(), "imported": newly_imported, "not_recoverable": newly_failed,
              "path_issues": snap.unrecognized, "errors": snap.errors[:20]}
    ctx.state_set(STATE_KEY, {**state, "items": items, "last": result})
    if newly_imported or newly_failed:
        parts = []
        if newly_imported:
            parts.append(f"{len(newly_imported)} eerder gedownloade "
                         f"{'bestand' if len(newly_imported) == 1 else 'bestanden'} alsnog "
                         "geïmporteerd")
        if newly_failed:
            parts.append(f"{len(newly_failed)} niet te herstellen: "
                         + "; ".join(newly_failed[:5]) + (" …" if len(newly_failed) > 5 else ""))
        notify(ctx, "info" if not newly_failed else "warning", "Downloads hersteld",
               ", ".join(parts), link="/activiteit?tab=downloads",
               dedupe_key="download-recovery")
    return result


def summary(ctx: AppContext, since_iso: str | None = None) -> dict[str, Any]:
    """Voor Activiteit → Downloads en het dagoverzicht."""
    items = (_state(ctx).get("items") or {}).values()
    rows = [i for i in items if not since_iso or str(i.get("at") or "") >= since_iso]
    rows.sort(key=lambda i: str(i.get("at") or ""), reverse=True)
    return {"last": _state(ctx).get("last"),
            "imported": sum(1 for i in rows if i.get("status") == "imported"),
            "not_recoverable": sum(1 for i in rows if i.get("status") == "not_recoverable"),
            "pending": sum(1 for i in rows if i.get("status") in ("importing", "error")),
            "items": rows[:100]}


def digest_line(ctx: AppContext, start_iso: str) -> str | None:
    s = summary(ctx, start_iso)
    if not s["imported"] and not s["not_recoverable"]:
        return None
    text = f"{s['imported']} niet-geïmporteerde download(s) alsnog geïmporteerd"
    if s["not_recoverable"]:
        text += f", {s['not_recoverable']} niet te herstellen"
    return text


# -- API -------------------------------------------------------------------------------------
from fastapi import APIRouter, Depends  # noqa: E402

from mediaguard.auth import Principal, require_operator, require_viewer  # noqa: E402
from mediaguard.context import get_app_context  # noqa: E402

router = APIRouter(prefix="/api/v1/downloads", tags=["downloads"])


@router.get("/recovery")
def recovery_status(_: Principal = Depends(require_viewer),
                    ctx: AppContext = Depends(get_app_context)) -> dict[str, Any]:
    """'Hersteld: …' — niet-geïmporteerde downloads die MediaGuard alsnog importeerde."""
    return summary(ctx)


@router.post("/recovery/run")
async def recovery_run(_: Principal = Depends(require_operator),
                       ctx: AppContext = Depends(get_app_context)) -> dict[str, Any]:
    emergency.require_clear(ctx, "downloadherstel")
    return await run_once(ctx)


# -- achtergrond -----------------------------------------------------------------------------
def trigger(ctx: AppContext) -> None:
    """Na een wijziging aan downloadclients/categorieën: zo snel mogelijk een ronde."""
    ev = ctx.services.get("download_recovery_wake")
    if isinstance(ev, asyncio.Event):
        ev.set()


async def _loop(ctx: AppContext) -> None:
    ev = asyncio.Event()
    ctx.services["download_recovery_wake"] = ev
    await asyncio.sleep(120)
    while True:
        try:
            await run_once(ctx)
            taskwatch.ok(ctx, "download_recovery")
        except Exception as exc:  # noqa: BLE001 - achtergrondtaak mag nooit stoppen
            LOGGER.exception("downloadherstel mislukt")
            taskwatch.fail(ctx, "download_recovery", exc)
        ev.clear()
        with contextlib.suppress(TimeoutError):
            await asyncio.wait_for(ev.wait(), INTERVAL_SECONDS)


async def startup(ctx: AppContext) -> None:
    if ctx.settings.start_background and ctx.settings.role in ("core", "all"):
        ctx.services["download_recovery_task"] = taskwatch.spawn(
            ctx, "download_recovery", _loop, label_nl="Vastgelopen downloads herstellen",
            every=INTERVAL_SECONDS)


async def shutdown(ctx: AppContext) -> None:
    task = ctx.services.pop("download_recovery_task", None)
    if task is not None:
        task.cancel()
        with contextlib.suppress(asyncio.CancelledError, Exception):
            await task
