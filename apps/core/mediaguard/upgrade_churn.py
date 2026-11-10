"""Upgradegolf herkennen en afremmen (rapport 8: ±611 anime-upgrades in 24 uur).

* **Diagnose** (``GET /api/v1/storage/ledger/diagnose``): per item hoe vaak het in 7 dagen
  geüpgraded is, of het vervangen bestand een AV1-versie van MediaGuard was, de bron, de
  releasegroepen en hoe oud de grab was (inhaalslag vs. nieuwe golf). Plus een korte uitleg
  welke oorzaak de cijfers het best verklaart.
* **Churn-rem**: een item dat ≥ 2× in 7 dagen geüpgraded is, krijgt 14 dagen geen nieuwe
  grabs meer (pre-grab weigert met ``UPGRADE_CHURN``); één melding met de lijst. Zonder
  gebruiker; het item heeft al een bestand, dus er gaat niets verloren.
"""

from __future__ import annotations

import logging
from collections import Counter, defaultdict
from datetime import UTC, datetime, timedelta
from pathlib import PurePosixPath
from typing import Any

from mediaguard.downloads import holds

LOGGER = logging.getLogger(__name__)

CHURN_LIMIT = 2
CHURN_WINDOW_DAYS = 7
CHURN_HOLD_DAYS = 14
BACKLOG_HOURS = 24
NOTIFIED_KEY = "upgrade_churn_notified"


def _iso(dt: datetime) -> str:
    return dt.astimezone(UTC).isoformat(timespec="seconds")


def _parse(value: Any) -> datetime | None:
    try:
        at = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    except (TypeError, ValueError):
        return None
    return at if at.tzinfo else at.replace(tzinfo=UTC)


def _stem(path: Any) -> str:
    name = PurePosixPath(str(path or "").replace("\\", "/")).name
    return name.rsplit(".", 1)[0].lower() if "." in name else name.lower()


def _upgrades(ctx: Any, start: datetime, end: datetime) -> list[dict[str, Any]]:
    from mediaguard.storage_ledger import imports

    return [i for i in imports(ctx, _iso(start), _iso(end + timedelta(seconds=1)))
            if i["kind"] == "upgrade" and i.get("item_id") is not None]


def _key(row: dict[str, Any]) -> str:
    return holds.item_key(row["instance_id"], row["media_kind"], row["item_id"])


def _av1_stems(ctx: Any) -> dict[str, set[str]]:
    """Per instantie de bestandsnamen (zonder extensie) die MediaGuard naar AV1 omzette."""
    out: dict[str, set[str]] = defaultdict(set)
    for r in ctx.db.fetch_all(
            "SELECT instance_id, source_path FROM encoding_jobs WHERE status='committed'"):
        out[str(r["instance_id"])].add(_stem(r["source_path"]))
    return out


# -- diagnose ---------------------------------------------------------------------------------
def diagnose(ctx: Any, period: str = "24h", now: datetime | None = None) -> dict[str, Any]:
    from mediaguard.storage_ledger import PERIODS

    if period not in PERIODS:
        raise ValueError(period)
    now = now or datetime.now(UTC)
    start = now - PERIODS[period]
    ups = _upgrades(ctx, start, now)
    week = _upgrades(ctx, now - timedelta(days=CHURN_WINDOW_DAYS), now)
    per_item_week = Counter(_key(r) for r in week)
    av1 = _av1_stems(ctx)
    grabs_at: dict[tuple[str, str], str] = {}
    for r in ctx.db.fetch_all(
            "SELECT instance_id, download_id, MIN(at) AS at FROM storage_ledger_events WHERE "
            "event='grabbed' AND download_id IS NOT NULL GROUP BY instance_id, download_id"):
        grabs_at[(str(r["instance_id"]), str(r["download_id"]))] = str(r["at"])
    ev_dl = {(str(r["instance_id"]), int(r["item_id"]), str(r["at"])): r["download_id"]
             for r in ctx.db.fetch_all(
                 "SELECT instance_id, item_id, at, download_id FROM storage_ledger_events WHERE "
                 "event='imported' AND item_id IS NOT NULL AND at>=?", (_iso(start),))}
    items: dict[str, dict[str, Any]] = {}
    groups: Counter[str] = Counter()
    sources: Counter[str] = Counter()
    instances: Counter[str] = Counter()
    from_av1 = backlog = 0
    for r in ups:
        k = _key(r)
        replaced_av1 = bool(r.get("replaced_path")) and \
            _stem(r["replaced_path"]) in av1.get(r["instance_id"], set())
        did = ev_dl.get((r["instance_id"], int(r["item_id"]), str(r["at"])))
        grab_at = _parse(grabs_at.get((r["instance_id"], str(did)))) if did else None
        imp_at = _parse(r["at"])
        age_h = round((imp_at - grab_at).total_seconds() / 3600, 1) \
            if grab_at and imp_at else None
        is_backlog = age_h is not None and age_h >= BACKLOG_HOURS
        from_av1 += replaced_av1
        backlog += is_backlog
        groups[str(r.get("release_group") or "onbekend")] += 1
        sources[str(r.get("source_nl") or r.get("source"))] += 1
        instances[str(r.get("instance_name") or r["instance_id"])] += 1
        it = items.setdefault(k, {
            "key": k, "instance_id": r["instance_id"], "title": r["title"],
            "episode": r.get("episode"), "href": r.get("href"), "upgrades_period": 0,
            "upgrades_7d": per_item_week.get(k, 0), "replaced_av1": False,
            "release_groups": [], "sources": [], "grab_age_hours": [],
            "held": holds.hold_for(ctx, k) is not None})
        it["upgrades_period"] += 1
        it["replaced_av1"] = it["replaced_av1"] or replaced_av1
        if r.get("release_group") and r["release_group"] not in it["release_groups"]:
            it["release_groups"].append(r["release_group"])
        if r.get("source_nl") and r["source_nl"] not in it["sources"]:
            it["sources"].append(r["source_nl"])
        if age_h is not None:
            it["grab_age_hours"].append(age_h)
    recovered = 0
    for row in ctx.db.fetch_all(
            "SELECT COUNT(*) FROM audit_events WHERE action='downloads.recovery_import' AND "
            "ts>=?", (_iso(start),)):
        recovered = int(row[0] or 0)
    total = len(ups)
    churn = [i for i in items.values() if i["upgrades_7d"] >= CHURN_LIMIT]
    verdict: list[str] = []
    if total:
        if from_av1 / total >= 0.2:
            verdict.append(f"{from_av1} van de {total} upgrades vervingen een AV1-versie van "
                           "MediaGuard: Sonarr/Radarr zagen het omgezette bestand als slechter "
                           "(AV1-lus). MediaGuard behoudt nu de release-informatie en remt dit.")
        if backlog / total >= 0.3:
            verdict.append(f"{backlog} van de {total} upgrades kwamen van grabs ouder dan "
                           f"{BACKLOG_HOURS} uur: een eenmalige inhaalslag (bv. na het "
                           "herstel van SABnzbd-categorieën of downloadherstel).")
        if recovered:
            verdict.append(f"{recovered} download(s) zijn alsnog geïmporteerd door het "
                           "downloadherstel.")
        if churn:
            verdict.append(f"{len(churn)} item(s) zijn in 7 dagen ≥ {CHURN_LIMIT}× geüpgraded; "
                           f"die krijgen {CHURN_HOLD_DAYS} dagen geen nieuwe upgrades.")
        if not verdict:
            verdict.append("Gewone upgrades naar een release met een hogere score; de "
                           "score-grens is nu bereikbaar, dus dit stopt zodra de beste "
                           "realistische release er is.")
    return {
        "period": period, "upgrades": total, "items": len(items),
        "replaced_av1": from_av1, "backlog": backlog, "recovered_imports": recovered,
        "churn_items": len(churn),
        "by_group": [{"name": n, "count": c} for n, c in groups.most_common(15)],
        "by_source": [{"name": n, "count": c} for n, c in sources.most_common()],
        "by_instance": [{"name": n, "count": c} for n, c in instances.most_common()],
        "top_items": sorted(items.values(), key=lambda i: (-i["upgrades_7d"],
                                                           -i["upgrades_period"]))[:100],
        "verdict_nl": verdict,
    }


# -- churn-rem --------------------------------------------------------------------------------
def scan(ctx: Any, now: datetime | None = None) -> list[dict[str, Any]]:
    """Zet een pauze op items die te vaak geüpgraded worden; meldt nieuwe gevallen één keer."""
    now = now or datetime.now(UTC)
    week = _upgrades(ctx, now - timedelta(days=CHURN_WINDOW_DAYS), now)
    counts = Counter(_key(r) for r in week)
    first = {_key(r): r for r in week}
    fresh = []
    for key, n in counts.items():
        if n < CHURN_LIMIT or holds.hold_for(ctx, key) is not None:
            continue
        r = first[key]
        label = r["title"] + (f" {r['episode']}" if r.get("episode") else "")
        holds.set_hold(ctx, key, hours=CHURN_HOLD_DAYS * 24, kind="churn",
                       reason_nl=f"{n}× geüpgraded in {CHURN_WINDOW_DAYS} dagen; "
                                 f"{CHURN_HOLD_DAYS} dagen geen nieuwe upgrades")
        fresh.append({"key": key, "label": label, "count": n})
    if fresh:
        from mediaguard.api.system import notify

        names = ", ".join(f"{f['label']} ({f['count']}×)" for f in fresh[:10])
        more = f" en nog {len(fresh) - 10}" if len(fresh) > 10 else ""
        notify(ctx, "info", "Upgrades afgeremd",
               f"Deze titels werden steeds opnieuw geüpgraded en krijgen {CHURN_HOLD_DAYS} "
               f"dagen geen nieuwe downloads: {names}{more}.",
               link="/activiteit?tab=opslag", dedupe_key="upgrade_churn")
    return fresh


def _ids_from_body(body: dict[str, Any]) -> list[tuple[str, int]]:
    return holds.item_refs(body)


def pre_grab_block(ctx: Any, instance_id: str, body: dict[str, Any]) -> str | None:
    """Tekst als deze grab een item raakt met een churn-pauze, anders None."""
    for kind, pid in _ids_from_body(body):
        hit = holds.hold_for(ctx, holds.item_key(instance_id, kind, pid))
        if hit and hit.get("kind") == "churn":
            until = str(hit.get("until") or "")[:16].replace("T", " ")
            return f"Te vaak geüpgraded; nieuwe downloads voor dit item gepauzeerd tot {until} " \
                   "(UTC)"
    return None


__all__ = ["diagnose", "pre_grab_block", "scan"]
