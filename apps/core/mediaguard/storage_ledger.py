"""Opslaglogboek: wat kwam erbij en waar ging de ruimte heen.

De gebruiker ziet de vrije ruimte dalen maar niet waarom. Deze module legt per periode
(24 uur, 7 of 30 dagen) naast elkaar:

* **Erbij**: imports uit de Sonarr/Radarr-historie (nieuw of een upgrade; bij een upgrade telt
  alleen het verschil met het verwijderde bestand), met titel, instantie, kwaliteit, groep,
  indexer en de bron (Seerr-aanvraag, ontbrekend/backfill, optimalisatieplan, Media Source
  Optimizer of gewoon Sonarr/Radarr zelf).
* **MediaGuard zelf**: AV1-besparing, quarantaine en vangnet (bijgekomen en opgeruimd) en wat
  de downloadopruimer vrijmaakte.
* **Buiten de bibliotheek**: de huidige grootte van downloadmappen, prullenbak, quarantaine en
  de werkmap van de encoder (elk uur gemeten, één keer per dag bewaard).
* **Afstemming**: de gemeten daling van de vrije ruimte (dagelijkse momentopnamen, R19) tegen
  de som van de bekende oorzaken: verklaard en eerlijk 'onverklaard' (bv. andere apps op de
  pool).

De Sonarr/Radarr-historie wordt elke 15 minuten opgehaald en lokaal bewaard
(``storage_ledger_events``); de pagina rekent zonder netwerkverkeer.
"""

from __future__ import annotations

import asyncio
import contextlib
import logging
import os
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

from fastapi import APIRouter, Depends, HTTPException, Query

from mediaguard import taskwatch
from mediaguard.auth import Principal, require_operator, require_viewer
from mediaguard.context import AppContext, get_app_context
from mediaguard.db import dumps, loads, utcnow

LOGGER = logging.getLogger(__name__)
router = APIRouter(prefix="/api/v1/storage", tags=["storage"])

CURSOR_KEY = "storage_ledger_cursor"
DIRS_KEY = "storage_ledger_dirs"
ROOTS_KEY = "download_roots_local"  # gevuld door de downloadopruimer
KEEP_DAYS = 40
COLLECT_SECONDS = 15 * 60
SCAN_SECONDS = 60 * 60
PERIODS = {"24h": timedelta(hours=24), "7d": timedelta(days=7), "30d": timedelta(days=30)}
PERIOD_NL = {"24h": "Laatste 24 uur", "7d": "Laatste 7 dagen", "30d": "Laatste 30 dagen"}
CATEGORY_NL = {"tv": "Series", "anime": "Anime", "movie": "Films", "anime_movie": "Animefilms"}
SOURCE_NL = {
    "seerr": "Seerr-aanvraag",
    "backfill": "Ontbrekend / backfill",
    "optimization": "Optimalisatieplan",
    "mso": "Media Source Optimizer",
    "upgrade": "Upgrade door Sonarr/Radarr",
    "arr": "Sonarr/Radarr (RSS of handmatig)",
}
IMPORT_EVENTS = {"downloadfolderimported", "episodefileimported", "moviefileimported"}
DELETE_EVENTS = {"episodefiledeleted", "moviefiledeleted"}
UPGRADE_WINDOW = timedelta(minutes=30)


# -- hulpjes -----------------------------------------------------------------------------------
def gb(n: float | None) -> str:
    n = float(n or 0)
    sign = "−" if n < 0 else ""
    v = abs(n) / 1024 ** 3
    txt = f"{v:.0f}" if v >= 100 else f"{v:.1f}"
    return f"{sign}{txt.replace('.', ',')} GB"


def signed_gb(n: float | None) -> str:
    n = float(n or 0)
    return ("+" if n >= 0 else "") + gb(n)


def _int(value: Any) -> int | None:
    try:
        return int(float(value))
    except (TypeError, ValueError):
        return None


def _iso(dt: datetime) -> str:
    return dt.astimezone(UTC).isoformat(timespec="seconds")


def _parse(ts: str | None) -> datetime | None:
    if not ts:
        return None
    try:
        dt = datetime.fromisoformat(str(ts).replace("Z", "+00:00"))
    except ValueError:
        return None
    return dt if dt.tzinfo else dt.replace(tzinfo=UTC)


def _plural(n: int, one: str, many: str) -> str:
    return f"{n} {one if n == 1 else many}"


# -- historie verzamelen ------------------------------------------------------------------------
def _event_of(rec: dict[str, Any]) -> str | None:
    et = str(rec.get("eventType") or "").replace("_", "").lower()
    if et == "grabbed":
        return "grabbed"
    if et in IMPORT_EVENTS:
        return "imported"
    if et in DELETE_EVENTS:
        return "deleted"
    return None


def record_history(ctx: AppContext, instance_id: str, app: str,
                   records: list[dict[str, Any]]) -> int:
    """Bewaar de relevante historie-records (idempotent op history-id)."""
    kind = "movie" if app == "radarr" else "episode"
    n = 0
    for rec in records:
        event = _event_of(rec)
        hid = _int(rec.get("id"))
        if event is None or hid is None:
            continue
        data = rec.get("data") or {}
        quality = ((rec.get("quality") or {}).get("quality") or {}).get("name")
        owner = _int(rec.get("seriesId")) if kind == "episode" else _int(rec.get("movieId"))
        item = _int(rec.get("episodeId")) if kind == "episode" else _int(rec.get("movieId"))
        cf_names = [str(c.get("name")) for c in rec.get("customFormats") or []
                    if isinstance(c, dict) and c.get("name")]
        cf_score = _int(rec.get("customFormatScore"))
        if cf_score is None:
            cf_score = _int(data.get("customFormatScore"))
        revision = (rec.get("quality") or {}).get("revision") or {}
        repack = bool(revision.get("isRepack") or int(revision.get("version") or 1) > 1)
        try:
            ctx.db.execute(
                "INSERT OR IGNORE INTO storage_ledger_events(instance_id,history_id,at,event,"
                "media_kind,owner_id,item_id,size,quality,release_group,indexer,download_id,"
                "source_title,reason,cf_score,custom_formats_json,is_repack) "
                "VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
                (instance_id, hid, _iso(_parse(rec.get("date")) or datetime.now(UTC)), event,
                 kind, owner, item, _int(data.get("size")), quality,
                 data.get("releaseGroup") or None, data.get("indexer") or None,
                 rec.get("downloadId") or None, rec.get("sourceTitle") or None,
                 (data.get("reason") or None), cf_score,
                 dumps(cf_names) if cf_names else None, int(repack)))
            n += 1
        except Exception:  # noqa: BLE001 - één slecht record mag de rest niet tegenhouden
            LOGGER.debug("historie-record %s/%s overgeslagen", instance_id, hid)
    return n


async def collect(ctx: AppContext) -> int:
    """Nieuwe historie van alle instanties sinds de vorige ronde (eerste keer: 31 dagen)."""
    from mediaguard.instances.registry import arr_client, list_instances

    cursors = dict(ctx.state_get(CURSOR_KEY, {}) or {})
    total = 0
    for inst in list_instances(ctx):
        since = cursors.get(inst.id) or _iso(datetime.now(UTC) - timedelta(days=31))
        started = utcnow()
        try:
            async with arr_client(ctx, inst.id) as client:
                events = await client.history_since(since)
        except Exception as exc:  # noqa: BLE001 - per instantie
            LOGGER.debug("opslaglogboek: historie %s niet gelezen: %s", inst.id, exc)
            continue
        total += record_history(ctx, inst.id, inst.app, list(events or []))
        cursors[inst.id] = started
    ctx.state_set(CURSOR_KEY, cursors)
    cutoff = _iso(datetime.now(UTC) - timedelta(days=KEEP_DAYS))
    ctx.db.execute("DELETE FROM storage_ledger_events WHERE at<?", (cutoff,))
    return total


# -- mappen buiten de bibliotheek ----------------------------------------------------------------
def _dir_size(path: Path) -> tuple[int, int]:
    """(alle bytes, bytes die alleen hier staan — zonder hardlinks naar de bibliotheek)."""
    total = own = 0
    for root, _dirs, files in os.walk(path):
        for name in files:
            try:
                st = os.lstat(os.path.join(root, name))
            except OSError:
                continue
            total += st.st_size
            if st.st_nlink <= 1:
                own += st.st_size
    return total, own


def scan_targets(ctx: AppContext) -> list[dict[str, Any]]:
    """Welke mappen buiten de bibliotheek MediaGuard meet (dubbelingen eruit)."""
    from mediaguard.recovery.orphans import quarantine_roots

    targets: list[dict[str, Any]] = []
    for r in ctx.state_get(ROOTS_KEY, []) or []:
        label = {"incomplete": "Downloads (bezig)", "torrent": "Torrents"}.get(
            str(r.get("kind")), "Downloads (klaar)")
        cat = str(r.get("category") or "")
        targets.append({"path": str(r.get("path")), "group": "downloads",
                        "label_nl": f"{label}{' · ' + cat if cat else ''}",
                        "client": r.get("client")})
    with contextlib.suppress(Exception):
        for q in quarantine_roots(ctx):
            targets.append({"path": str(q), "group": "quarantine",
                            "label_nl": "Quarantaine & vangnet"})
    staging = Path(ctx.settings.staging_dir)
    work = staging.parent if staging.name == "staging" else staging
    targets.append({"path": str(work), "group": "encoder", "label_nl": "Werkmap AV1-encoder"})
    out: list[dict[str, Any]] = []
    seen: list[Path] = []
    for t in targets:
        p = Path(t["path"])
        try:
            rp = p.resolve()
        except OSError:
            continue
        # overlap met een eerder gekozen map (erin of eromheen): overslaan, nooit dubbel tellen
        if not rp.is_dir() or any(rp == s or s in rp.parents or rp in s.parents for s in seen):
            continue
        seen.append(rp)
        out.append({**t, "path": str(rp)})
    return out


def scan_dirs(ctx: AppContext) -> dict[str, Any]:
    dirs = []
    for t in scan_targets(ctx):
        total, own = _dir_size(Path(t["path"]))
        dirs.append({**t, "bytes": total, "own_bytes": own})
    snap: dict[str, Any] = {"at": utcnow(), "dirs": dirs,
            "by_group": {g: sum(d["own_bytes"] for d in dirs if d["group"] == g)
                         for g in ("downloads", "quarantine", "encoder")}}
    ctx.state_set(DIRS_KEY, snap)
    ctx.state_set(f"{DIRS_KEY}:{datetime.now(UTC).date().isoformat()}", snap)
    free = None
    with contextlib.suppress(Exception):
        from mediaguard.library.api import free_space_snapshot

        free = _int(free_space_snapshot(ctx).get("free"))
    record_snapshot(ctx, free=free, downloads=snap["by_group"].get("downloads"))
    return snap


SNAPS_KEY = "storage_ledger_snaps"
SNAP_KEEP_DAYS = 35
SNAP_TOLERANCE = timedelta(hours=3)


def record_snapshot(ctx: AppContext, *, free: int | None, downloads: int | None,
                    at: datetime | None = None) -> None:
    """Uurlijkse meting van vrije ruimte en downloadmappen, met tijdstip. De afstemming
    rekent zo over precies hetzelfde venster als de oorzaken (rapport 8: de dagelijkse meting
    viel op een ander tijdstip dan het begin van de periode, waardoor 'onverklaard' sterk
    negatief kon worden)."""
    if free is None and downloads is None:
        return
    now = at or datetime.now(UTC)
    snaps = [x for x in (ctx.state_get(SNAPS_KEY, []) or [])
             if (_parse(x.get("at")) or now) >= now - timedelta(days=SNAP_KEEP_DAYS)]
    snaps.append({"at": _iso(now), "free": free, "downloads": downloads})
    ctx.state_set(SNAPS_KEY, snaps[-SNAP_KEEP_DAYS * 24 * 2:])


def snapshot_near(ctx: AppContext, when: datetime) -> dict[str, Any] | None:
    """De meting het dichtst bij ``when`` (binnen 3 uur), anders None."""
    best, best_gap = None, None
    for x in ctx.state_get(SNAPS_KEY, []) or []:
        at = _parse(x.get("at"))
        if at is None or x.get("free") is None:
            continue
        gap = abs(at - when)
        if gap <= SNAP_TOLERANCE and (best_gap is None or gap < best_gap):
            best, best_gap = x, gap
    return best


# -- bron van een import ----------------------------------------------------------------------
def _source(ctx: AppContext, ev: dict[str, Any], is_upgrade: bool, owner_kind: str) -> str:
    iid, item, owner, at = ev["instance_id"], ev["item_id"], ev["owner_id"], ev["at"]
    since = _iso((_parse(at) or datetime.now(UTC)) - timedelta(days=30))
    # Een upgrade van een ooit via Seerr aangevraagde serie is geen Seerr-levering meer
    # (rapport 8: 'Seerr-aanvraag 6× −0,5 GB' waren upgrades van zo'n serie).
    if owner is not None and not is_upgrade:
        row = ctx.db.fetch_one(
            "SELECT 1 FROM delivery_items WHERE source='seerr' AND instance_id=? AND "
            "provider_id=? LIMIT 1", (iid, owner))
        if row:
            return "seerr"
    keys = [str(x) for x in (item, owner) if x is not None]
    if keys:
        row = ctx.db.fetch_one(
            "SELECT 1 FROM optimization_items WHERE instance_id=? AND status IN "
            f"('search_sent','done','imported','replaced') AND provider_id IN "
            f"({','.join('?' * len(keys))}) LIMIT 1", (iid, *keys))
        if row:
            return "optimization"
    if item is not None:
        w = ctx.db.fetch_one(
            "SELECT id, last_searched_at FROM wanted_items WHERE instance_id=? AND "
            "item_provider_id=? AND kind=? ORDER BY last_searched_at DESC LIMIT 1",
            (iid, item, owner_kind))
        if w and w["last_searched_at"] and w["last_searched_at"] >= since \
                and w["last_searched_at"] <= at:
            mso = ctx.db.fetch_one(
                "SELECT 1 FROM audit_events WHERE actor LIKE 'apikey:%' AND target_id=? "
                "AND action LIKE 'wanted.%' AND ts>=? LIMIT 1", (str(w["id"]), since))
            return "mso" if mso else "backfill"
    return "upgrade" if is_upgrade else "arr"


# -- het logboek -------------------------------------------------------------------------------
def _titles(ctx: AppContext) -> dict[tuple[str, int], dict[str, Any]]:
    return {(r["instance_id"], int(r["provider_id"])): r for r in ctx.db.fetch_dicts(
        "SELECT id, instance_id, provider_id, kind, title, year FROM lib_items")}


def _episode_label(ctx: AppContext, iid: str, episode_id: int | None) -> str | None:
    if episode_id is None:
        return None
    row = ctx.db.fetch_one(
        "SELECT season_number, episode_number FROM lib_episodes WHERE instance_id=? AND "
        "provider_id=?", (iid, episode_id))
    if not row:
        return None
    return f"S{int(row['season_number']):02d}E{int(row['episode_number']):02d}"


def _file_size(ctx: AppContext, ev: dict[str, Any]) -> int | None:
    if ev["media_kind"] == "movie":
        row = ctx.db.fetch_one(
            "SELECT size FROM lib_files WHERE instance_id=? AND kind='movie' AND "
            "owner_provider_id=? ORDER BY synced_at DESC LIMIT 1",
            (ev["instance_id"], ev["item_id"]))
    else:
        row = ctx.db.fetch_one(
            "SELECT f.size FROM lib_episodes e JOIN lib_files f ON f.instance_id=e.instance_id "
            "AND f.kind='episode' AND f.provider_id=e.episode_file_id WHERE e.instance_id=? "
            "AND e.provider_id=?", (ev["instance_id"], ev["item_id"]))
    return _int(row["size"]) if row else None


def imports(ctx: AppContext, start: str, end: str) -> list[dict[str, Any]]:
    """Alle imports in [start, end) met grootte, upgrade-verschil en bron."""
    insts = {r["id"]: dict(r) for r in ctx.db.fetch_dicts(
        "SELECT id, name, media_type FROM instances")}
    titles = _titles(ctx)
    rows = ctx.db.fetch_dicts(
        "SELECT * FROM storage_ledger_events WHERE event='imported' AND at>=? AND at<? "
        "ORDER BY at DESC", (start, end))
    out = []
    for ev in rows:
        size = ev["size"]
        grab = None
        if ev["download_id"]:
            grab = ctx.db.fetch_one(
                "SELECT size, indexer, release_group FROM storage_ledger_events WHERE "
                "event='grabbed' AND instance_id=? AND download_id=? ORDER BY at DESC LIMIT 1",
                (ev["instance_id"], ev["download_id"]))
        if size is None and grab is not None:
            size = grab["size"]
        if size is None:
            size = _file_size(ctx, ev)
        at = _parse(ev["at"]) or datetime.now(UTC)
        deleted = ctx.db.fetch_one(
            "SELECT size, reason, source_title FROM storage_ledger_events WHERE "
            "event='deleted' AND "
            "instance_id=? AND item_id=? AND at>=? AND at<=? AND LOWER(COALESCE(reason,''))"
            " IN ('upgrade','') ORDER BY ABS(julianday(at)-julianday(?)) LIMIT 1",
            (ev["instance_id"], ev["item_id"], _iso(at - UPGRADE_WINDOW),
             _iso(at + UPGRADE_WINDOW), ev["at"]))
        is_upgrade = deleted is not None
        if not is_upgrade and ev["download_id"]:
            hook = ctx.db.fetch_one(
                "SELECT is_upgrade FROM import_events WHERE instance_id=? AND download_id=? "
                "LIMIT 1", (ev["instance_id"], ev["download_id"]))
            is_upgrade = bool(hook and hook["is_upgrade"])
        old = _int(deleted["size"]) if deleted is not None else None
        old_estimated = False
        if is_upgrade and old is None:
            # Sonarr/Radarr geven de grootte van het verwijderde bestand niet altijd mee: neem
            # de vorige import van hetzelfde item. Anders telde een upgrade zijn volle nieuwe
            # grootte en werd 'onverklaard' sterk negatief (rapport 8: −387 GB).
            prev = ctx.db.fetch_one(
                "SELECT size FROM storage_ledger_events WHERE event='imported' AND "
                "instance_id=? AND item_id=? AND at<? AND size IS NOT NULL ORDER BY at DESC "
                "LIMIT 1", (ev["instance_id"], ev["item_id"], ev["at"]))
            if prev is not None:
                old, old_estimated = _int(prev["size"]), True
        # onbekende oude grootte bij een upgrade: geen netto verschil aannemen (gemarkeerd)
        net = ((size or 0) - old if old is not None else 0) if is_upgrade else (size or 0)
        inst = insts.get(ev["instance_id"]) or {}
        owner_kind = "movie" if ev["media_kind"] == "movie" else "series"
        lib = titles.get((ev["instance_id"], int(ev["owner_id"] or -1)))
        title = (lib or {}).get("title") or ev["source_title"] or "onbekende titel"
        label = _episode_label(ctx, ev["instance_id"], ev["item_id"]) \
            if ev["media_kind"] == "episode" else None
        source = _source(ctx, ev, is_upgrade, "movie" if owner_kind == "movie" else "episode")
        out.append({
            "at": ev["at"], "instance_id": ev["instance_id"],
            "instance_name": inst.get("name") or ev["instance_id"],
            "category": inst.get("media_type"),
            "category_nl": CATEGORY_NL.get(str(inst.get("media_type")), "Overig"),
            "media_kind": ev["media_kind"], "title": title, "episode": label,
            "kind": "upgrade" if is_upgrade else "new",
            "kind_nl": "Upgrade" if is_upgrade else "Nieuw",
            "size": size, "old_size": old, "net_bytes": net,
            "old_size_unknown": is_upgrade and old is None,
            "old_size_estimated": old_estimated,
            "item_id": ev["item_id"], "owner_id": ev["owner_id"],
            "replaced_path": (deleted["source_title"] if deleted is not None else None),
            "quality": ev["quality"],
            "release_group": ev["release_group"] or (grab["release_group"] if grab else None),
            "indexer": ev["indexer"] or (grab["indexer"] if grab else None),
            "source": source, "source_nl": SOURCE_NL[source],
            "release": ev["source_title"],
            # waarom deze upgrade? (kwaliteit, custom formats, REPACK) — zie upgrade_reasons
            "waarom": (_why(ctx, ev) if is_upgrade else None),
            "href": f"/bibliotheek/{ev['instance_id']}/{lib['id']}" if lib else None,
        })
    return out


def _why(ctx: AppContext, ev: dict[str, Any]) -> str | None:
    from mediaguard import upgrade_reasons

    try:
        return str(upgrade_reasons.reason_for_import(ctx, ev)["reason_nl"])
    except Exception:  # noqa: BLE001 - de reden is extra; het overzicht blijft werken
        LOGGER.debug("upgrade-reden niet te bepalen", exc_info=True)
        return None


def _av1(ctx: AppContext, start: str, end: str) -> list[dict[str, Any]]:
    out = []
    for r in ctx.db.fetch_dicts(
            "SELECT id, instance_id, source_path, source_snapshot_json, staged_output_json, "
            "updated_at FROM encoding_jobs WHERE status='committed' AND updated_at>=? AND "
            "updated_at<? ORDER BY updated_at DESC", (start, end)):
        src = _int((loads(r["source_snapshot_json"], {}) or {}).get("size")) or 0
        dst = _int((loads(r["staged_output_json"], {}) or {}).get("size")) or 0
        if not src or not dst:
            continue
        out.append({"at": r["updated_at"], "instance_id": r["instance_id"],
                    "title": Path(str(r["source_path"] or "")).stem or f"job {r['id']}",
                    "kind": "av1", "kind_nl": "AV1-omzetting", "size": dst,
                    "old_size": src, "net_bytes": dst - src, "source": "av1",
                    "source_nl": "MediaGuard AV1", "href": None})
    return out


def _sum(ctx: AppContext, sql: str, args: tuple[Any, ...]) -> int:
    row = ctx.db.fetch_one(sql, args)
    return int((row[0] if row else 0) or 0)


def _free_at(ctx: AppContext, day: str) -> int | None:
    snap = ctx.state_get(f"library_space:{day}") or {}
    return _int(snap.get("free"))


def _parts(ctx: AppContext, start: str, end: str) -> dict[str, Any]:
    items = imports(ctx, start, end)
    av1 = _av1(ctx, start, end)
    new = [i for i in items if i["kind"] == "new"]
    up = [i for i in items if i["kind"] == "upgrade"]
    return {
        "items": items, "av1": av1, "new": new, "up": up,
        "added": sum(i["size"] or 0 for i in new),
        "upgrades_net": sum(i["net_bytes"] for i in up),
        "av1_saved": sum(-i["net_bytes"] for i in av1),
        "q_in": _sum(ctx, "SELECT COALESCE(SUM(size),0) FROM quarantine_items WHERE "
                     "created_at>=? AND created_at<?", (start, end)),
        "q_out": _sum(ctx, "SELECT COALESCE(SUM(size),0) FROM quarantine_items WHERE status="
                      "'purged' AND purged_at>=? AND purged_at<?", (start, end)),
        "janitor_freed": _sum(ctx, "SELECT COALESCE(SUM(freed),0) FROM download_janitor_trash "
                              "WHERE purged_at>=? AND purged_at<?", (start, end)),
    }


def ledger(ctx: AppContext, period: str = "24h", now: datetime | None = None,
           *, live_free: int | None = None) -> dict[str, Any]:
    """Het volledige overzicht voor één periode."""
    if period not in PERIODS:
        raise ValueError(period)
    now = now or datetime.now(UTC)
    start_dt = now - PERIODS[period]
    start, end = _iso(start_dt), _iso(now + timedelta(seconds=1))
    parts = _parts(ctx, start, end)
    items, av1, new, up = parts["items"], parts["av1"], parts["new"], parts["up"]
    added, upgrades_net, av1_saved = parts["added"], parts["upgrades_net"], parts["av1_saved"]
    q_in, q_out, janitor_freed = parts["q_in"], parts["q_out"], parts["janitor_freed"]
    dirs_now = ctx.state_get(DIRS_KEY) or {}
    dl_now = (dirs_now.get("by_group") or {}).get("downloads")
    near = snapshot_near(ctx, start_dt)
    dl_then = near.get("downloads") if near else None
    if dl_then is None:
        dirs_then = ctx.state_get(f"{DIRS_KEY}:{start_dt.date().isoformat()}") or {}
        dl_then = (dirs_then.get("by_group") or {}).get("downloads")
    downloads_delta = (int(dl_now) - int(dl_then)) if dl_now is not None and \
        dl_then is not None else -janitor_freed
    library_net = added + upgrades_net - av1_saved
    quarantine_net = q_in - q_out

    if live_free is None:
        with contextlib.suppress(Exception):
            from mediaguard.library.api import free_space_snapshot

            live_free = _int(free_space_snapshot(ctx).get("free"))
    window_nl = None
    if near is not None:
        free_then = _int(near.get("free"))
        explained = library_net + quarantine_net + downloads_delta
        near_at = _parse(near.get("at"))
        if near_at is not None and abs(near_at - start_dt) > timedelta(minutes=30):
            # zelfde venster als de meting: oorzaken opnieuw over [meting, nu]
            w = _parts(ctx, _iso(near_at), end)
            explained = (w["added"] + w["upgrades_net"] - w["av1_saved"]
                         + w["q_in"] - w["q_out"] + downloads_delta)
            window_nl = f"gemeten vanaf {near_at.astimezone(UTC).strftime('%d-%m %H:%M')} UTC"
    else:
        free_then = _free_at(ctx, start_dt.date().isoformat())
        explained = library_net + quarantine_net + downloads_delta
        if free_then is not None:
            window_nl = "dagmeting (tijdstip wijkt af van het begin van de periode)"
    used_delta = (free_then - live_free) if free_then is not None and live_free is not None \
        else None
    unexplained = (used_delta - explained) if used_delta is not None else None
    unknown_old = sum(1 for i in up if i.get("old_size_unknown"))

    def bucket(key: str, label: str, value: int, hint: str) -> dict[str, Any]:
        return {"key": key, "label_nl": label, "bytes": int(value), "bytes_nl": signed_gb(value),
                "hint_nl": hint}

    buckets = [
        bucket("added", "Erbij", added, f"{_plural(len(new), 'nieuw bestand', 'nieuwe bestanden')}"
               " geïmporteerd"),
        bucket("upgrades", "Upgrades", upgrades_net,
               f"{_plural(len(up), 'betere versie', 'betere versies')} (alleen het verschil)"),
        bucket("av1", "AV1-besparing", -av1_saved,
               f"{_plural(len(av1), 'bestand', 'bestanden')} kleiner gemaakt"),
        bucket("quarantine", "Quarantaine & vangnet", quarantine_net,
               f"{gb(q_in)} bewaard, {gb(q_out)} opgeruimd"),
        bucket("downloads", "Downloadmappen", downloads_delta,
               (f"{gb(janitor_freed)} opgeruimd door MediaGuard" if janitor_freed
                else "verschil in de downloadmappen")),
    ]
    if unexplained is not None:
        buckets.append(bucket("unexplained", "Onverklaard", unexplained,
                              "niet aan MediaGuard of Sonarr/Radarr toe te wijzen (bv. andere "
                              "apps op dezelfde pool, of nog niet gemeten)"))

    episodes = sum(1 for i in items if i["media_kind"] == "episode")
    movies = sum(1 for i in items if i["media_kind"] == "movie")
    head = signed_gb(added + upgrades_net)
    counts = []
    if episodes:
        counts.append(_plural(episodes, "aflevering", "afleveringen"))
    if movies:
        counts.append(_plural(movies, "film", "films"))
    summary = f"{PERIOD_NL[period]}: {head}"
    summary += f" — {', '.join(counts)}" if counts else " — niets geïmporteerd"
    if av1_saved:
        summary += f"; AV1 bespaarde {gb(av1_saved)}"
    summary += f"; netto {signed_gb(library_net)}"

    def totals(key: str, label_key: str) -> list[dict[str, Any]]:
        agg: dict[str, dict[str, Any]] = {}
        for i in items:
            k = str(i.get(key) or "overig")
            a = agg.setdefault(k, {"key": k, "label_nl": i.get(label_key) or k, "count": 0,
                                   "bytes": 0})
            a["count"] += 1
            a["bytes"] += int(i["net_bytes"])
        rows = sorted(agg.values(), key=lambda r: -r["bytes"])
        for r in rows:
            r["bytes_nl"] = signed_gb(r["bytes"])
        return rows

    recon = {
        "free_delta": (-used_delta if used_delta is not None else None),
        "used_delta": used_delta, "used_delta_nl": signed_gb(used_delta)
        if used_delta is not None else None,
        "explained": explained, "explained_nl": signed_gb(explained),
        "unexplained": unexplained,
        "unexplained_nl": signed_gb(unexplained) if unexplained is not None else None,
        "window_nl": window_nl, "upgrades_old_size_unknown": unknown_old,
        "text_nl": (
            f"Gebruikte ruimte {signed_gb(used_delta)}: verklaard {signed_gb(explained)}, "
            f"onverklaard {signed_gb(unexplained)}"
            + (f" ({window_nl})" if window_nl else "")
            + (f"; {unknown_old} upgrade(s) zonder bekende oude grootte telden als 0"
               if unknown_old else "") + "." if used_delta is not None else
            "Nog geen dagelijkse meting van de vrije ruimte aan het begin van deze periode; "
            "de afstemming verschijnt vanzelf zodra die er is."),
    }
    return {
        "period": period, "period_nl": PERIOD_NL[period], "start": start, "end": _iso(now),
        "summary_nl": summary,
        "totals": {"added": added, "upgrades_net": upgrades_net, "av1_saved": av1_saved,
                   "library_net": library_net, "quarantine_in": q_in, "quarantine_out": q_out,
                   "janitor_freed": janitor_freed, "downloads_delta": downloads_delta,
                   "episodes": episodes, "movies": movies, "new": len(new),
                   "upgrades": len(up), "av1": len(av1)},
        "buckets": buckets,
        "reconciliation": recon,
        "by_category": totals("category", "category_nl"),
        "by_source": totals("source", "source_nl"),
        "items": sorted(items + av1, key=lambda i: str(i["at"]), reverse=True)[:500],
        "dirs": dirs_now.get("dirs") or [], "dirs_measured_at": dirs_now.get("at"),
        "history_since": (ctx.state_get(CURSOR_KEY, {}) or {}),
    }


def digest_line(ctx: AppContext, start: str, end: str) -> dict[str, Any] | None:
    """Eén regel voor het dag-/weekoverzicht: erbij, AV1-besparing en netto."""
    items = imports(ctx, start, end)
    av1 = _av1(ctx, start, end)
    if not items and not av1:
        return None
    eps = sum(1 for i in items if i["media_kind"] == "episode")
    movies = sum(1 for i in items if i["media_kind"] == "movie")
    grown = sum(int(i["net_bytes"]) for i in items)
    saved = sum(-int(i["net_bytes"]) for i in av1)
    counts = ", ".join(x for x in (
        _plural(eps, "aflevering", "afleveringen") if eps else "",
        _plural(movies, "film", "films") if movies else "") if x) or "geen imports"
    text = f"Erbij: {counts}, {signed_gb(grown)}"
    if saved:
        text += f"; AV1 bespaarde {gb(saved)}"
    text += f"; netto {signed_gb(grown - saved)}"
    return {"text_nl": text, "added_bytes": grown, "av1_saved": saved,
            "episodes": eps, "movies": movies}


# -- meldingen ---------------------------------------------------------------------------------
NOTIFY_CATEGORY = "Meldingen"
SENT_KEY = "storage_ledger_notified"


def register_settings() -> None:
    from mediaguard.settings_registry import REGISTRY, SettingDef

    defs = [
        SettingDef("storage.ledger_notify_daily", "bool", True, NOTIFY_CATEGORY,
                   "Dagelijks opslagoverzicht", "Daily storage summary",
                   help_nl="Elke dag op de tijd van het dagoverzicht een melding 'Opslag "
                           "laatste 24 uur': wat erbij kwam, de grootste toevoegingen en wat "
                           "niet te verklaren is. Gaat ook naar je telefoon (soort 'Opslag')."),
        SettingDef("storage.ledger_notify_weekly", "bool", True, NOTIFY_CATEGORY,
                   "Wekelijks opslagoverzicht", "Weekly storage summary",
                   help_nl="Op de dag van het weekoverzicht dezelfde melding over 7 dagen."),
        SettingDef("storage.growth_alert_gb", "int", 200, NOTIFY_CATEGORY,
                   "Waarschuwen bij snelle groei (GB per 24 uur)", "Growth alert (GB/24h)",
                   minimum=0, maximum=10000, unit="GB",
                   help_nl="Groeit de bibliotheek in 24 uur meer dan dit, dan komt er meteen "
                           "een melding met wat het veroorzaakte. 0 = uit."),
    ]
    for d in defs:
        if d.key not in {x.key for x in REGISTRY.all()}:
            REGISTRY.register(d)


def _cfg(ctx: AppContext, key: str, default: Any) -> Any:
    try:
        return ctx.config.value(key)
    except Exception:  # noqa: BLE001 - instelling (nog) niet geregistreerd
        return default


def _local_now(ctx: AppContext, now: datetime | None) -> datetime:
    from zoneinfo import ZoneInfo

    try:
        tz = ZoneInfo(str(_cfg(ctx, "notify.timezone", "Europe/Amsterdam")))
    except Exception:  # noqa: BLE001
        tz = ZoneInfo("Europe/Amsterdam")
    return (now or datetime.now(UTC)).astimezone(tz)


def notification_body(data: dict[str, Any], top: int = 5) -> str:
    lines = [data["summary_nl"]]
    biggest = sorted((i for i in data["items"] if i["kind"] != "av1"),
                     key=lambda i: -int(i["net_bytes"]))[:top]
    if biggest:
        lines.append("Grootste toevoegingen:")
        for i in biggest:
            name = i["title"] + (f" {i['episode']}" if i.get("episode") else "")
            lines.append(f"• {name} — {signed_gb(i['net_bytes'])} ({i['kind_nl']}, "
                         f"{i['source_nl']})")
    recon = data["reconciliation"]
    if recon.get("unexplained") is not None:
        lines.append(f"Onverklaard: {recon['unexplained_nl']} (gebruikte ruimte "
                     f"{recon['used_delta_nl']}).")
    return "\n".join(lines)


def _send(ctx: AppContext, severity: str, title: str, body: str, period: str,
          dedupe_key: str) -> int:
    from mediaguard.api.system import notify

    return notify(ctx, severity, title, body,
                  link=f"/activiteit?tab=opslag&periode={period}", dedupe_key=dedupe_key)


def notify_tick(ctx: AppContext, now: datetime | None = None) -> list[str]:
    """Dag-/weekmelding op de tijd van het overzicht en een groeialarm; idempotent."""
    sent: list[str] = []
    local = _local_now(ctx, now)
    state = dict(ctx.state_get(SENT_KEY, {}) or {})
    try:
        hh, mm = (int(x) for x in str(_cfg(ctx, "notify.digest.time", "08:00")).split(":", 1))
    except ValueError:
        hh, mm = 8, 0
    due = (local.hour, local.minute) >= (hh, mm)
    day = local.date().isoformat()
    if due and bool(_cfg(ctx, "storage.ledger_notify_daily", True)) \
            and state.get("daily") != day:
        data = ledger(ctx, "24h", now)
        _send(ctx, "info", "Opslag laatste 24 uur", notification_body(data), "24h",
              f"storage.ledger.daily:{day}")
        state["daily"] = day
        sent.append("daily")
    weekday = int(_cfg(ctx, "notify.digest.weekday", 1) or 1)
    if due and bool(_cfg(ctx, "storage.ledger_notify_weekly", True)) \
            and local.isoweekday() == weekday and state.get("weekly") != day:
        data = ledger(ctx, "7d", now)
        _send(ctx, "info", "Opslag laatste 7 dagen", notification_body(data), "7d",
              f"storage.ledger.weekly:{day}")
        state["weekly"] = day
        sent.append("weekly")
    limit_gb = int(_cfg(ctx, "storage.growth_alert_gb", 200) or 0)
    last_alert = _parse(state.get("growth_at"))
    cooled = last_alert is None or (now or datetime.now(UTC)) - last_alert >= timedelta(hours=12)
    if limit_gb > 0 and cooled:
        data = ledger(ctx, "24h", now)
        growth = int(data["totals"]["library_net"])
        if growth > limit_gb * 1024 ** 3:
            _send(ctx, "warning", f"Opslag: {signed_gb(growth)} in 24 uur",
                  f"Meer dan {limit_gb} GB groei in een dag.\n" + notification_body(data),
                  "24h", "storage.ledger.growth")
            state["growth_at"] = _iso(now or datetime.now(UTC))
            sent.append("growth")
    ctx.state_set(SENT_KEY, state)
    return sent


# -- API ---------------------------------------------------------------------------------------
@router.get("/ledger")
async def storage_ledger(period: str = Query("24h"), _: Principal = Depends(require_viewer),
                         ctx: AppContext = Depends(get_app_context)) -> dict[str, Any]:
    if period not in PERIODS:
        raise HTTPException(422, "Periode moet 24h, 7d of 30d zijn")
    return await asyncio.to_thread(ledger, ctx, period)


@router.get("/ledger/diagnose")
async def storage_ledger_diagnose(period: str = Query("24h"),
                                  _: Principal = Depends(require_viewer),
                                  ctx: AppContext = Depends(get_app_context)) -> dict[str, Any]:
    """Waar komt een upgradegolf vandaan? (rapport 8: ±611 anime-upgrades per dag)."""
    from mediaguard import upgrade_churn

    if period not in PERIODS:
        raise HTTPException(422, "Periode moet 24h, 7d of 30d zijn")
    return await asyncio.to_thread(upgrade_churn.diagnose, ctx, period)


@router.post("/ledger/refresh")
async def storage_ledger_refresh(principal: Principal = Depends(require_operator),
                                 ctx: AppContext = Depends(get_app_context)) -> dict[str, Any]:
    n = await collect(ctx)
    snap = await asyncio.to_thread(scan_dirs, ctx)
    return {"recorded": n, "dirs_measured_at": snap["at"]}


# -- achtergrond -------------------------------------------------------------------------------
async def _loop(ctx: AppContext) -> None:
    await asyncio.sleep(120)
    last_scan = 0.0
    loop = asyncio.get_running_loop()
    while True:
        try:
            await collect(ctx)
        except Exception:  # noqa: BLE001 - lus nooit laten sterven
            LOGGER.exception("opslaglogboek: historie bijwerken mislukt")
        if loop.time() - last_scan >= SCAN_SECONDS or last_scan == 0.0:
            try:
                await asyncio.to_thread(scan_dirs, ctx)
                last_scan = loop.time()
            except Exception:  # noqa: BLE001
                LOGGER.exception("opslaglogboek: mappen meten mislukt")
        try:
            from mediaguard import upgrade_churn

            upgrade_churn.scan(ctx)  # in de event loop: notify() publiceert events
        except Exception:  # noqa: BLE001
            LOGGER.exception("opslaglogboek: upgrade-rem mislukt")
        try:
            notify_tick(ctx)  # in de event loop: notify() publiceert events
        except Exception:  # noqa: BLE001
            LOGGER.exception("opslaglogboek: melding mislukt")
        taskwatch.ok(ctx, "storage_ledger")
        await asyncio.sleep(COLLECT_SECONDS)


async def startup(ctx: AppContext) -> None:
    if ctx.settings.start_background and ctx.settings.role in ("core", "all"):
        ctx.services["storage_ledger_task"] = taskwatch.spawn(
            ctx, "storage_ledger", _loop, label_nl="Opslaglogboek bijhouden", every=COLLECT_SECONDS)


async def shutdown(ctx: AppContext) -> None:
    task = ctx.services.pop("storage_ledger_task", None)
    if task is not None:
        task.cancel()
        with contextlib.suppress(asyncio.CancelledError):
            await task
