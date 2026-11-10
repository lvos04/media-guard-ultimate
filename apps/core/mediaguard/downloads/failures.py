"""Mislukte en geweigerde downloads zelf afhandelen (rapport 3, R6/R8) — zonder gebruiker.

1. Wachtrij: een voltooide download die Sonarr/Radarr weigert ('geen verbetering', 'niet
   gewenst in profiel', …) of die de client als mislukt meldt, gaat uit de client en op de
   blokkadelijst; Sonarr/Radarr zoeken dan zelf een andere release. Een 'handmatige import
   nodig' die MediaGuard niet eenduidig kan bevestigen (zie activity/manual_import) wordt na
   een wachttijd op dezelfde manier afgehandeld: nooit soepeler importeren dan toegestaan.
2. Gegrabd maar verdwenen ('niet geïmporteerd en niet meer in de wachtrij'): de reden komt
   uit SABnzbd/qBittorrent; de grab wordt in Sonarr/Radarr als mislukt gemarkeerd
   (blokkadelijst + opnieuw zoeken).
3. Releasegroepen die voor hetzelfde item twee keer mislukken, vermijdt MediaGuard bij zijn
   eigen keuzes (``avoided_groups``).

Herbruikbaar: ``refusal_reason``, ``handle_queue``, ``handle_grabbed_without_import``,
``record_group_failure`` en ``avoided_groups`` (ook voor mediaguard/delivery).
"""

from __future__ import annotations

import asyncio
import contextlib
import logging
from datetime import UTC, datetime, timedelta
from typing import Any

from fastapi import APIRouter, Depends

from mediaguard import emergency, taskwatch
from mediaguard.arr.client import ArrError, records_of
from mediaguard.audit import audit
from mediaguard.auth import Principal, require_viewer
from mediaguard.context import AppContext, get_app_context
from mediaguard.db import utcnow

LOGGER = logging.getLogger(__name__)
router = APIRouter(prefix="/api/v1/downloads", tags=["downloads"])
ACTOR = "svc:download-guard"
GROUP_AVOID_AFTER = 2
UNRESOLVED_IMPORT_HOURS = 6
INTERVAL_SECONDS = 900

# Sonarr/Radarr-weigering van een voltooide download → Nederlandse reden
REFUSALS_NL: tuple[tuple[str, str], ...] = (
    ("not a custom format upgrade", "Geen verbetering in custom formats"),
    ("custom format", "Geen verbetering in custom formats"),
    ("not an upgrade", "Geen verbetering ten opzichte van het bestaande bestand"),
    ("not a quality", "Geen kwaliteitsverbetering"),
    ("existing file on disk", "Het bestaande bestand is al even goed of beter"),
    ("is not wanted in profile", "Kwaliteit niet toegestaan in het profiel"),
    ("not wanted in profile", "Kwaliteit niet toegestaan in het profiel"),
    ("sample", "Alleen een sample in de download"),
    ("unknown series", "Download hoort bij geen bekende serie"),
    ("unknown movie", "Download hoort bij geen bekende film"),
    ("no files found are eligible", "Geen importeerbaar bestand in de download"),
    ("geen verbetering", "Geen verbetering in custom formats"),
)


def refusal_reason(messages: list[str]) -> str | None:
    """Nederlandse reden als Sonarr/Radarr de voltooide download weigert, anders None."""
    text = " ".join(str(m) for m in messages).lower()
    from mediaguard.activity.manual_import import is_id_match_block

    if is_id_match_block(messages):
        return None  # dat is de ID-match: activity/manual_import handelt die af
    for needle, nl in REFUSALS_NL:
        if needle in text:
            return nl
    return None


def _item_key(instance_id: str, d: dict[str, Any]) -> str:
    ref = d.get("episode_id") or d.get("episodeId") or d.get("movie_id") or d.get("movieId")
    return f"{instance_id}:{ref}"


def record_group_failure(ctx: AppContext, item_key: str, group: str | None) -> int:
    if not group:
        return 0
    data = dict(ctx.state_get("download_group_failures", {}) or {})
    per = dict(data.get(item_key) or {})
    per[group] = int(per.get(group) or 0) + 1
    data[item_key] = per
    ctx.state_set("download_group_failures", data)
    return per[group]


def avoided_groups(ctx: AppContext, item_key: str) -> set[str]:
    """Releasegroepen die voor dit item al ``GROUP_AVOID_AFTER``× mislukten, plus groepen die
    de laatste 7 dagen bij ``GLOBAL_AVOID_AFTER`` verschillende downloads mislukten."""
    per = (ctx.state_get("download_group_failures", {}) or {}).get(item_key) or {}
    local = {g for g, n in per.items() if int(n) >= GROUP_AVOID_AFTER}
    return local | {r["name"] for r in failure_stats(ctx).get("group", []) if r["avoided"]}


def _stuck_since(ctx: AppContext, key: str) -> datetime:
    seen = dict(ctx.state_get("download_guard_seen", {}) or {})
    if key not in seen:
        seen[key] = utcnow()
        ctx.state_set("download_guard_seen", seen)
    at = datetime.fromisoformat(str(seen[key]))
    return at if at.tzinfo else at.replace(tzinfo=UTC)


async def handle_queue(ctx: AppContext, downloads: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Weigeringen en mislukte downloads uit de wachtrij halen (+ blokkadelijst).

    ``downloads`` = activity-rijen (activity.api.queue_item). Geeft wat er gedaan is."""
    from mediaguard.instances.registry import arr_client

    done: list[dict[str, Any]] = []
    now = datetime.now(UTC)
    for d in downloads:
        if emergency.active(ctx):
            break  # noodstop: niets weghalen of blokkeren
        if not d.get("id") or not d.get("instance_id"):
            continue
        messages = [str(m) for m in d.get("messages") or []]
        reason = refusal_reason(messages) if d.get("awaiting_import") else None
        state = str(d.get("state") or "")
        if reason is None and state in ("failedpending", "failed"):
            reason = "Download mislukt in de downloadclient"
        if reason is None and d.get("can_import"):
            # ID-match die niet eenduidig te bevestigen is: na een wachttijd een andere
            # release laten zoeken (nooit iets importeren dat niet aantoonbaar klopt)
            key = f"{d['instance_id']}:{d.get('download_id')}"
            if now - _stuck_since(ctx, key) >= timedelta(hours=UNRESOLVED_IMPORT_HOURS):
                reason = "Import niet eenduidig te bevestigen; andere release gezocht"
        if reason is None:
            continue
        try:
            async with arr_client(ctx, d["instance_id"]) as client:
                await client.queue_remove(int(d["id"]), remove_from_client=True,
                                          blocklist=True)
        except (ArrError, Exception) as exc:  # noqa: BLE001 - per download
            LOGGER.info("download %s niet opgeruimd: %s", d.get("title"), exc)
            continue
        group = d.get("release_group")
        record_group_failure(ctx, _item_key(d["instance_id"], d), group)
        record_global_failure(ctx, group, d.get("indexer"))
        mark_outcome_failed(ctx, d.get("download_id"))
        if reason in UPGRADE_REFUSALS:
            await refuse_release(ctx, d, reason)
        audit(ctx.db, actor=ACTOR, action="download.refused_removed", target_type="download",
              target_id=d.get("download_id"), instance_id=d["instance_id"], reason=reason,
              old_value={"release": d.get("release"), "title": d.get("title")})
        done.append({"title": d.get("title"), "release": d.get("release"),
                     "reason_nl": reason, "instance_id": d["instance_id"]})
    return done


GONE_GRACE_HOURS = 6
GLOBAL_AVOID_AFTER = 5  # zelfde releasegroep, verschillende items, binnen 7 dagen


def _parse_dt(value: Any) -> datetime | None:
    try:
        at = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    except (TypeError, ValueError):
        return None
    return at if at.tzinfo else at.replace(tzinfo=UTC)


def janitor_reason(ctx: AppContext, download_id: str) -> str | None:
    """Heeft MediaGuard's eigen opruimer deze download weggehaald? Dan is dat de reden."""
    try:
        row = ctx.db.fetch_one(
            "SELECT reason FROM download_janitor_trash WHERE download_id=? ORDER BY id DESC "
            "LIMIT 1", (str(download_id),))
    except Exception:  # noqa: BLE001 - tabel ontbreekt in oude databases
        return None
    return f"Opgeruimd door MediaGuard: {row['reason']}" if row else None


async def grab_failure(ctx: AppContext, client: Any, history: list[dict[str, Any]]
                       ) -> dict[str, Any] | None:
    """Laatste grab zonder import: wat zegt de downloadclient? None = niets bekend.

    Velden: ``client`` (wat de client zegt, None = niet gevonden), ``client_checked``
    (de client is volledig bevraagd, dus 'niet gevonden' betekent echt weg), ``imported``
    (Sonarr/Radarr importeerden deze download toch), ``active`` (download loopt nog)."""
    from mediaguard.downloads.failure_lookup import kind_of, lookup_with_status

    events = sorted(history, key=lambda h: str(h.get("date", "")), reverse=True)
    latest = next((e for e in events if str(e.get("eventType", "")).lower() == "grabbed"), None)
    if latest is None or not latest.get("downloadId"):
        return None
    did = str(latest["downloadId"])
    imported = any(str(e.get("downloadId") or "") == did and str(e.get("eventType", "")).lower()
                   in ("downloadfolderimported", "episodefileimported", "moviefileimported")
                   for e in events)
    found, queried = await lookup_with_status(ctx, [did])
    info = found.get(did) or found.get(did.lower())
    data = latest.get("data") or {}
    return {"history_id": latest.get("id"), "download_id": did,
            "release": latest.get("sourceTitle"), "date": latest.get("date"),
            "release_group": data.get("releaseGroup"), "indexer": data.get("indexer"),
            "client": info, "client_checked": kind_of(did) in queried,
            "imported": imported, "active": bool(info and info.get("active")),
            "janitor_reason": janitor_reason(ctx, did)}


def record_global_failure(ctx: AppContext, group: str | None, indexer: str | None) -> None:
    """Teller per releasegroep en indexer over alle items (laatste 7 dagen)."""
    now = datetime.now(UTC)
    cutoff = now - timedelta(days=7)
    data = dict(ctx.state_get("download_failure_stats", {}) or {})
    for kind, name in (("group", group), ("indexer", indexer)):
        if not name:
            continue
        per = dict(data.get(kind) or {})
        hits = [t for t in per.get(name, []) if (_parse_dt(t) or now) >= cutoff]
        hits.append(now.isoformat(timespec="seconds"))
        per[name] = hits[-200:]
        data[kind] = per
    ctx.state_set("download_failure_stats", data)


def failure_stats(ctx: AppContext) -> dict[str, list[dict[str, Any]]]:
    """Mislukte downloads per releasegroep/indexer (7 dagen), meeste eerst."""
    now = datetime.now(UTC)
    cutoff = now - timedelta(days=7)
    data = ctx.state_get("download_failure_stats", {}) or {}
    out: dict[str, list[dict[str, Any]]] = {}
    for kind in ("group", "indexer"):
        rows = []
        for name, hits in (data.get(kind) or {}).items():
            n = sum(1 for t in hits if (_parse_dt(t) or now) >= cutoff)
            if n:
                rows.append({"name": name, "count": n,
                             "avoided": kind == "group" and n >= GLOBAL_AVOID_AFTER})
        out[kind] = sorted(rows, key=lambda r: -r["count"])[:20]
    return out


async def handle_grabbed_without_import(ctx: AppContext, limit: int = 50
                                        ) -> list[dict[str, Any]]:
    """Wanted-items met 'gegrabd maar niet geïmporteerd': reden ophalen, grab als mislukt
    markeren (blokkadelijst + opnieuw zoeken). Alleen als de client 'mislukt' meldt of de
    download nergens meer bestaat."""
    from mediaguard.downloads import holds
    from mediaguard.instances.registry import arr_client

    rows = ctx.db.fetch_dicts(
        "SELECT id, instance_id, kind, provider_id, item_provider_id, title FROM wanted_items "
        "WHERE last_reason_code='import-failed' ORDER BY id LIMIT ?", (limit,))
    done: list[dict[str, Any]] = []
    handled = dict(ctx.state_get("download_guard_marked", {}) or {})
    from mediaguard.downloads import sab_categories

    # Zolang SABnzbd een categorie van deze instantie mist, 'verdwijnen' downloads door die
    # oorzaak; dan niets als mislukt markeren (zou alleen nieuwe releases verbranden).
    sab_missing = {iid for iid, cats in sab_categories.missing_categories(ctx).items()
                   if cats}
    for r in rows:
        if emergency.active(ctx):
            break  # noodstop: geen grab als mislukt markeren (dat blokkeert de release)
        if r["instance_id"] in sab_missing:
            continue
        try:
            async with arr_client(ctx, r["instance_id"]) as client:
                # provider_id = aflevering- of film-ID. Vroeger stond hier item_provider_id
                # (het serie-ID): dan werd de historie van een willekeurige andere aflevering
                # gelezen en diens grab als mislukt gemarkeerd (rapport 5, T5).
                pid = int(r["provider_id"])
                history = (await client.history_for_episode(pid, 30) if r["kind"] == "episode"
                           else await client.history_for_movie(pid))
                info = await grab_failure(ctx, client, history)
                if info is None or not info.get("history_id"):
                    continue
                hid = str(info["history_id"])
                if handled.get(hid):
                    continue
                from mediaguard.downloads import recovery

                if recovery.status_of(ctx, str(info.get("download_id") or "")) == "importing":
                    continue  # het herstelvangnet biedt deze download net opnieuw aan
                cinfo = info.get("client")
                if info.get("imported") or info.get("active"):
                    continue  # toch geïmporteerd, of de download loopt nog
                grabbed_at = _parse_dt(info.get("date"))
                if grabbed_at and datetime.now(UTC) - grabbed_at < \
                        timedelta(hours=GONE_GRACE_HOURS):
                    continue  # jonge grab: SAB/qBit kunnen nog bezig zijn
                failed = bool(cinfo and cinfo.get("failed"))
                gone = cinfo is None and bool(info.get("client_checked"))
                if not (failed or gone):
                    continue  # client onbereikbaar of meldt voltooid: niet ingrijpen
                await client.post(f"history/failed/{int(info['history_id'])}")
        except (ArrError, Exception) as exc:  # noqa: BLE001 - per item
            LOGGER.info("grab zonder import %s niet afgehandeld: %s", r["id"], exc)
            continue
        reason = (cinfo or {}).get("reason_nl") or info.get("janitor_reason") or \
            "Download niet meer in de client (SABnzbd/qBittorrent kent hem niet meer)"
        handled[hid] = utcnow()
        mark_outcome_failed(ctx, info.get("download_id"))
        key = f"{r['instance_id']}:{r['provider_id']}"
        record_group_failure(ctx, key, info.get("release_group"))
        hkey = holds.item_key(r["instance_id"], r["kind"], r["provider_id"])
        if gone or failed:
            n = holds.record_vanished(ctx, hkey, reason)
            if n >= holds.VANISH_LIMIT and holds.hold_for(ctx, hkey) is None:
                until = holds.set_hold(ctx, hkey, hours=holds.VANISH_HOLD_HOURS,
                                       reason_nl=f"Downloads verdwijnen steeds ({reason})",
                                       kind="vanished")
                _notify_vanished(ctx, r, reason, n, until)
        record_global_failure(ctx, info.get("release_group"), info.get("indexer"))
        ctx.db.execute("UPDATE wanted_items SET last_reason_code='download-client-failed' "
                       "WHERE id=?", (r["id"],))
        audit(ctx.db, actor=ACTOR, action="download.marked_failed", target_type="wanted",
              target_id=r["id"], instance_id=r["instance_id"], reason=reason,
              old_value={"release": info.get("release"), "indexer": info.get("indexer"),
                         "release_group": info.get("release_group")})
        done.append({"wanted_id": r["id"], "release": info.get("release"), "reason_nl": reason})
    ctx.state_set("download_guard_marked", dict(list(handled.items())[-2000:]))
    return done


# Weigeringen die betekenen 'dit is geen verbetering': dezelfde release later opnieuw grabben
# heeft geen zin (rapport 5, T4: één film 4× via verschillende indexers).
UPGRADE_REFUSALS = {"Geen verbetering in custom formats",
                    "Geen verbetering ten opzichte van het bestaande bestand",
                    "Geen kwaliteitsverbetering", "Het bestaande bestand is al even goed of beter",
                    "Kwaliteit niet toegestaan in het profiel"}
REFUSED_KEY = "download_refused_releases"
REFUSED_KEEP_DAYS = 90
REFUSED_MAX = 300
REFUSED_PROFILE = "MediaGuard · Geweigerde releases"


def _regex_for(title: str) -> str:
    import re

    return "/^" + re.escape(title.strip()) + "$/i"


def refused_titles(ctx: AppContext, instance_id: str) -> list[str]:
    cutoff = datetime.now(UTC) - timedelta(days=REFUSED_KEEP_DAYS)
    rows = (ctx.state_get(REFUSED_KEY, {}) or {}).get(instance_id) or []
    return [r["title"] for r in rows if (_parse_dt(r.get("at")) or cutoff) >= cutoff]


async def refuse_release(ctx: AppContext, d: dict[str, Any], reason: str) -> None:
    """Blokkeert de RELEASE (titel, alle indexers) i.p.v. alleen het download-ID, en pauzeert
    upgrade-zoeken voor dit item. Radarr/Sonarr's eigen blokkadelijst matcht per indexer, dus
    dezelfde release via een andere indexer werd elke ronde opnieuw gegrabd."""
    from mediaguard.downloads import holds

    title = str(d.get("release") or "").strip()
    iid = str(d["instance_id"])
    if title:
        data = dict(ctx.state_get(REFUSED_KEY, {}) or {})
        rows = [r for r in (data.get(iid) or []) if r.get("title") != title]
        rows.append({"title": title, "at": utcnow(), "reason_nl": reason})
        data[iid] = rows[-REFUSED_MAX:]
        ctx.state_set(REFUSED_KEY, data)
        try:
            await sync_refused_profile(ctx, iid)
        except Exception as exc:  # noqa: BLE001 - de blokkadelijst werkt dan nog per indexer
            LOGGER.info("releaseprofiel geweigerde releases niet bijgewerkt: %s", exc)
    ref = d.get("movie_id") or d.get("movieId")
    kind = "movie" if ref else "episode"
    ref = ref or d.get("episode_id") or d.get("episodeId")
    if ref:
        holds.set_hold(ctx, holds.item_key(iid, kind, ref), hours=24 * holds.REFUSED_HOLD_DAYS,
                       reason_nl=f"Upgrade geweigerd ({reason}); niet opnieuw zoeken tot er "
                                 "iets beters kan zijn", kind="refused")


async def sync_refused_profile(ctx: AppContext, instance_id: str) -> None:
    """Eén MediaGuard-releaseprofiel met de geweigerde titels als 'mag niet bevatten'."""
    from mediaguard.instances.registry import arr_client

    if emergency.skip(ctx, "releaseprofiel bijwerken"):
        return  # de volgende weigering na de noodstop zet alle titels er alsnog in
    terms = [_regex_for(t) for t in refused_titles(ctx, instance_id)]
    async with arr_client(ctx, instance_id) as client:
        profiles = await client.get("releaseprofile") or []
        mine = next((p for p in profiles if p.get("name") == REFUSED_PROFILE), None)
        body: dict[str, Any] = {"name": REFUSED_PROFILE, "enabled": True, "required": [],
                                "ignored": terms, "indexerId": 0, "tags": []}
        if mine is None:
            if terms:
                await client.post("releaseprofile", body)
            return
        if sorted(mine.get("ignored") or []) != sorted(terms):
            await client.put(f"releaseprofile/{int(mine['id'])}", {**mine, **body,
                                                                   "id": mine["id"]})


def _notify_vanished(ctx: AppContext, row: dict[str, Any], reason: str, n: int,
                     until: str) -> None:
    from mediaguard.api.system import notify
    from mediaguard.textfmt import datetime_nl

    title = str(row.get("title") or "Item")
    notify(ctx, "warning", f"Downloads verdwijnen steeds: {title}",
           f"{n} downloads voor dit item verdwenen binnen 7 dagen ({reason}). MediaGuard "
           f"pauzeert het zoeken tot {datetime_nl(until)} en probeert het daarna opnieuw.",
           link="/downloads", instance_id=row.get("instance_id"),
           dedupe_key=f"download-vanished|{row.get('instance_id')}|{row.get('provider_id')}")


# -- gezondheid per bron (rapport 5, M7) ----------------------------------------------------
OUTCOME_KEY = "download_outcomes"
OUTCOME_CURSOR = "download_outcomes_cursor"
OUTCOME_KEEP_DAYS = 30
IMPORT_EVENTS = {"downloadfolderimported", "episodefileimported", "moviefileimported"}
FAILED_EVENTS = {"downloadfailed", "downloadignored"}


def record_outcomes(ctx: AppContext, events: list[dict[str, Any]]) -> int:
    """Telt grabs en hun uitkomst per indexer/releasegroep (per download-ID, idempotent).

    Opslag: {download_id: {indexer, group, at, outcome: grabbed|imported|failed}}."""
    data = dict(ctx.state_get(OUTCOME_KEY, {}) or {})
    changed = 0
    for e in sorted(events, key=lambda x: str(x.get("date", ""))):
        did = str(e.get("downloadId") or "")
        if not did:
            continue
        kind = str(e.get("eventType", "")).lower()
        info = e.get("data") or {}
        rec = dict(data.get(did) or {})
        if kind == "grabbed":
            rec.setdefault("outcome", "grabbed")
            rec.update(indexer=info.get("indexer") or rec.get("indexer"),
                       group=info.get("releaseGroup") or rec.get("group"),
                       at=str(e.get("date") or rec.get("at") or utcnow()))
        elif kind in IMPORT_EVENTS:
            rec["outcome"] = "imported"
            rec.setdefault("at", str(e.get("date") or utcnow()))
        elif kind in FAILED_EVENTS:
            if rec.get("outcome") != "imported":
                rec["outcome"] = "failed"
            rec.setdefault("at", str(e.get("date") or utcnow()))
        else:
            continue
        data[did] = rec
        changed += 1
    cutoff = datetime.now(UTC) - timedelta(days=OUTCOME_KEEP_DAYS)
    data = {k: v for k, v in data.items() if (_parse_dt(v.get("at")) or cutoff) >= cutoff}
    ctx.state_set(OUTCOME_KEY, data)
    return changed


def mark_outcome_failed(ctx: AppContext, download_id: str | None) -> None:
    if not download_id:
        return
    data = dict(ctx.state_get(OUTCOME_KEY, {}) or {})
    rec = dict(data.get(str(download_id)) or {"at": utcnow()})
    if rec.get("outcome") != "imported":
        rec["outcome"] = "failed"
        data[str(download_id)] = rec
        ctx.state_set(OUTCOME_KEY, data)


def source_health(ctx: AppContext, days: int) -> dict[str, list[dict[str, Any]]]:
    """Succespercentage per indexer en releasegroep over ``days`` dagen (afgeronde grabs)."""
    cutoff = datetime.now(UTC) - timedelta(days=days)
    stats: dict[str, dict[str, dict[str, int]]] = {"indexer": {}, "group": {}}
    for rec in (ctx.state_get(OUTCOME_KEY, {}) or {}).values():
        if (_parse_dt(rec.get("at")) or cutoff) < cutoff:
            continue
        for kind, name in (("indexer", rec.get("indexer")), ("group", rec.get("group"))):
            if not name:
                continue
            s = stats[kind].setdefault(str(name), {"grabs": 0, "imported": 0, "failed": 0})
            s["grabs"] += 1
            if rec.get("outcome") == "imported":
                s["imported"] += 1
            elif rec.get("outcome") == "failed":
                s["failed"] += 1
    avoided = {r["name"] for r in failure_stats(ctx).get("group", []) if r["avoided"]}
    out: dict[str, list[dict[str, Any]]] = {}
    for kind, per in stats.items():
        rows = []
        for name, s in per.items():
            done = s["imported"] + s["failed"]
            rows.append({"name": name, **s,
                         "success_rate": round(s["imported"] / done, 3) if done else None,
                         "avoided": kind == "group" and name in avoided})
        out[kind] = sorted(rows, key=lambda r: (-(r["failed"]), -r["grabs"]))[:50]
    return out


async def collect_outcomes(ctx: AppContext) -> int:
    """Nieuwe historie van alle MediaGuard-instanties sinds de vorige ronde."""
    from mediaguard.instances.registry import arr_client, list_instances

    cursors = dict(ctx.state_get(OUTCOME_CURSOR, {}) or {})
    total = 0
    for inst in list_instances(ctx):
        since = cursors.get(inst.id) or (datetime.now(UTC) - timedelta(days=7)).isoformat(
            timespec="seconds")
        try:
            async with arr_client(ctx, inst.id) as client:
                events = await client.history_since(since)
        except Exception as exc:  # noqa: BLE001 - per instantie
            LOGGER.debug("historie %s niet gelezen: %s", inst.id, exc)
            continue
        total += record_outcomes(ctx, list(events or []))
        cursors[inst.id] = utcnow()
    ctx.state_set(OUTCOME_CURSOR, cursors)
    return total


def failure_groups(ctx: AppContext, days: int = 7) -> list[dict[str, Any]]:
    """Wat MediaGuard de laatste dagen afhandelde, gegroepeerd op oorzaak."""
    since = (datetime.now(UTC) - timedelta(days=days)).isoformat(timespec="seconds")
    rows = ctx.db.fetch_dicts(
        "SELECT reason, COUNT(*) AS n FROM audit_events WHERE action IN "
        "('download.refused_removed','download.marked_failed') AND ts>=? GROUP BY reason "
        "ORDER BY n DESC", (since,))
    return [{"reason_nl": r["reason"], "count": int(r["n"])} for r in rows]


@router.get("/health")
async def downloads_health(_: Principal = Depends(require_viewer),
                           ctx: AppContext = Depends(get_app_context)) -> dict[str, Any]:
    """Gezondheid van de downloadbronnen voor de pagina Downloads (rapport 5, M7)."""
    from mediaguard.downloads import holds
    from mediaguard.downloads.failure_lookup import recent_client_errors

    try:
        errors = await recent_client_errors(ctx)
    except Exception:  # noqa: BLE001 - diagnose
        errors = []
    return {"sources_7d": source_health(ctx, 7), "sources_30d": source_health(ctx, 30),
            "client_errors": errors, "causes": failure_groups(ctx, 7),
            "paused_items": holds.active_holds(ctx),
            "refused_releases": {iid: rows[-20:] for iid, rows in
                                 (ctx.state_get(REFUSED_KEY, {}) or {}).items()},
            "last": ctx.state_get("download_guard_last", {})}


@router.get("/failures")
def failures(days: int = 7, _: Principal = Depends(require_viewer),
             ctx: AppContext = Depends(get_app_context)) -> dict[str, Any]:
    """Wat MediaGuard zelf met mislukte/geweigerde downloads deed, per oorzaak."""
    return {"groups": failure_groups(ctx, max(1, min(days, 90))),
            "by_source": failure_stats(ctx),
            "last": ctx.state_get("download_guard_last", {})}


async def run_once(ctx: AppContext) -> dict[str, Any]:
    from mediaguard.activity.api import activity_cached

    stopped = emergency.skip(ctx, "downloadbewaking")
    removed: list[dict[str, Any]] = []
    marked: list[dict[str, Any]] = []
    if not stopped:  # noodstop: alleen tellen, niets weghalen of blokkeren
        act = await activity_cached(ctx)
        removed = await handle_queue(ctx, list((act or {}).get("downloads", [])))
        marked = await handle_grabbed_without_import(ctx)
    try:
        await collect_outcomes(ctx)
    except Exception:  # noqa: BLE001 - statistiek mag de bewaking nooit stoppen
        LOGGER.exception("downloadstatistiek bijwerken mislukt")
    out: dict[str, Any] = {"removed": removed, "marked_failed": marked}
    if stopped:
        out.update(emergency.skipped("downloadbewaking"))
    return out


async def _loop(ctx: AppContext) -> None:
    await asyncio.sleep(180)
    while True:
        try:
            result = await run_once(ctx)
            ctx.state_set("download_guard_last", {
                "at": utcnow(), "removed": len(result["removed"]),
                "marked_failed": len(result["marked_failed"]),
                "paused_nl": result.get("reason_nl")})
            if result["removed"] or result["marked_failed"]:
                LOGGER.info("downloadbewaking: %s geweigerd/mislukt opgeruimd, %s grabs als "
                            "mislukt gemarkeerd", len(result["removed"]),
                            len(result["marked_failed"]))
            taskwatch.ok(ctx, "download_guard")
        except Exception as exc:  # noqa: BLE001 - lus nooit laten sterven
            LOGGER.exception("downloadbewaking mislukt")
            taskwatch.fail(ctx, "download_guard", exc)
        await asyncio.sleep(INTERVAL_SECONDS)


async def startup(ctx: AppContext) -> None:
    if ctx.settings.start_background and ctx.settings.role in ("core", "all"):
        ctx.services["download_guard_task"] = taskwatch.spawn(
            ctx, "download_guard", _loop, label_nl="Downloads bewaken", every=INTERVAL_SECONDS)


async def shutdown(ctx: AppContext) -> None:
    task = ctx.services.pop("download_guard_task", None)
    if task is not None:
        task.cancel()
        with contextlib.suppress(asyncio.CancelledError):
            await task


__all__ = ["UPGRADE_REFUSALS", "avoided_groups", "collect_outcomes", "refuse_release",
           "record_outcomes", "source_health", "failure_groups", "failure_stats", "grab_failure",
           "handle_grabbed_without_import",
           "handle_queue", "record_group_failure", "refusal_reason", "records_of", "run_once"]


# ── onbetrouwbare releasegroepen en inhaalgolf (rapport 6, N3) ─────────────────────────
UNRELIABLE_MIN_GRABS = 20
UNRELIABLE_MAX_RATE = 0.10
UNRELIABLE_KEY = "download_unreliable_groups"
CATCHUP_HOURS = 2
CATCHUP_MAX_PER_5MIN = 10


def unreliable_groups(ctx: AppContext) -> dict[str, dict[str, Any]]:
    """Groepen met ≥20 grabs (30 dagen, ouder dan 24 uur) en <10 % daarvan geïmporteerd.
    Een kwartier gecachet; valt vanzelf weg zodra de groep weer wél importeert."""
    cached = ctx.state_get(UNRELIABLE_KEY, {}) or {}
    at = _parse_dt(cached.get("at"))
    if at is not None and datetime.now(UTC) - at < timedelta(minutes=15):
        return dict(cached.get("groups") or {})
    now = datetime.now(UTC)
    lo, hi = now - timedelta(days=30), now - timedelta(hours=24)
    per: dict[str, dict[str, int]] = {}
    for rec in (ctx.state_get(OUTCOME_KEY, {}) or {}).values():
        when = _parse_dt(rec.get("at"))
        group = str(rec.get("group") or "")
        if not group or when is None or not (lo <= when <= hi):
            continue
        s = per.setdefault(group.lower(), {"grabs": 0, "imported": 0, "name": group})
        s["grabs"] += 1
        if rec.get("outcome") == "imported":
            s["imported"] += 1
    groups = {k: {"name": v["name"], "grabs": v["grabs"], "imported": v["imported"],
                  "rate": round(v["imported"] / v["grabs"], 3)}
              for k, v in per.items()
              if v["grabs"] >= UNRELIABLE_MIN_GRABS
              and v["imported"] / v["grabs"] < UNRELIABLE_MAX_RATE}
    ctx.state_set(UNRELIABLE_KEY, {"at": utcnow(), "groups": groups})
    notified = dict(ctx.state_get(UNRELIABLE_KEY + "_notified", {}) or {})
    fresh = [g for k, g in groups.items() if k not in notified]
    if fresh:
        from mediaguard.api.system import notify

        for g in fresh:
            notified[g["name"].lower()] = utcnow()
        names = ", ".join(f"{g['name']} ({g['imported']} van {g['grabs']} geïmporteerd)"
                          for g in fresh[:5])
        notify(ctx, "info", "Onbetrouwbare releasegroep(en) worden overgeslagen",
               f"{names}. MediaGuard laat Sonarr/Radarr releases van deze groep(en) "
               "voorlopig niet meer downloaden; dat stopt vanzelf zodra ze weer wél "
               "importeren.", link="/activiteit?tab=downloads",
               dedupe_key="downloads.unreliable_groups")
        ctx.state_set(UNRELIABLE_KEY + "_notified", notified)
    return groups


def catchup_throttle(ctx: AppContext, instance_id: str) -> str | None:
    """Na het opheffen van de noodstop kwam er in 1,5 minuut een golf van ~48 grabs.
    De eerste 2 uur daarna laat MediaGuard per instantie hooguit 10 grabs per 5 minuten
    door; de rest volgt bij volgende RSS-rondes of zoekacties. None = doorlaten."""
    state = ctx.state_get("emergency_stop", {}) or {}
    since = _parse_dt(state.get("since"))
    if state.get("active") or since is None:
        return None
    now = datetime.now(UTC)
    if now - since > timedelta(hours=CATCHUP_HOURS):
        return None
    window: dict[str, list[float]] = ctx.services.setdefault("catchup_window", {})
    stamps = [t for t in window.get(instance_id, []) if now.timestamp() - t < 300]
    if len(stamps) >= CATCHUP_MAX_PER_5MIN:
        window[instance_id] = stamps
        return (f"Inhaalgolf na de noodstop: hooguit {CATCHUP_MAX_PER_5MIN} downloads per "
                "5 minuten; deze volgt later vanzelf")
    stamps.append(now.timestamp())
    window[instance_id] = stamps
    return None
