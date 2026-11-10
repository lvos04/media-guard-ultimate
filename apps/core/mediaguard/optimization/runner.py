"""Uitvoering van een bevestigd optimalisatieplan, in batches en binnen limieten.

Respecteert: noodstop, tijdvenster, max actieve downloads, max GB/dag, zoekquota per
uur, max gelijktijdige encodes (CPU/GPU) en stagingreserve. Bestaande goede media blijft
bruikbaar: zoeken vervangt pas na import, AV1 pas na verificatie + recovery-commit.
"""

from __future__ import annotations

import logging
import shutil
from datetime import UTC, datetime, timedelta
from typing import TYPE_CHECKING, Any

from mediaguard.db import dumps, loads, utcnow
from mediaguard.encoding import service as enc
from mediaguard.optimization.planner import SEARCH_ACTIONS, in_window
from mediaguard.policy.ports import arr_client

if TYPE_CHECKING:
    from mediaguard.context import AppContext

LOGGER = logging.getLogger(__name__)
GB = 1024**3


def _since(hours: float) -> str:
    return (datetime.now(UTC) - timedelta(hours=hours)).isoformat(timespec="seconds")


def _today() -> str:
    return datetime.now(UTC).strftime("%Y-%m-%d")


def active_downloads(ctx: AppContext, hours: float = 48) -> int:
    """Grabs (48 uur) die nog op import wachten. Mislukte, genegeerde of door MediaGuard als
    mislukt gemarkeerde downloads tellen niet mee (rapport 6: 'er lopen al zoveel downloads
    als ingesteld' terwijl er niets liep)."""
    rows = ctx.db.fetch_all(
        "SELECT DISTINCT p.instance_id, p.download_id FROM release_provenance p WHERE "
        "p.event_type='Grab' AND p.received_at >= ? AND NOT EXISTS (SELECT 1 FROM "
        "import_events i WHERE i.instance_id=p.instance_id AND i.download_id=p.download_id "
        "AND i.event_type IN ('Download','Upgrade')) AND NOT EXISTS (SELECT 1 FROM "
        "release_provenance f WHERE f.instance_id=p.instance_id AND "
        "f.download_id=p.download_id AND f.event_type IN ('DownloadFailed',"
        "'DownloadIgnored'))", (_since(hours),))
    outcomes = ctx.state_get("download_outcomes", {}) or {}
    return sum(1 for r in rows
               if (outcomes.get(str(r["download_id"] or "")) or {}).get("outcome")
               not in ("failed", "imported"))


LIVE_ACTIVE = {"downloading", "queued", "stalled"}


async def live_active_downloads(ctx: AppContext) -> int:
    """Downloads die NU lopen, uit dezelfde bron als Activiteit (rapport 8, A7): de wachtrijen
    van SABnzbd/qBittorrent in de MediaGuard-categorieën. Pas als een client niet antwoordt,
    valt het terug op de grabs van de laatste 6 uur zonder import."""
    from mediaguard.downloads import live
    from mediaguard.downloads.janitor import managed_categories
    from mediaguard.provisioning.store import load

    try:
        items, errors = await live.client_downloads(ctx)
    except Exception:  # noqa: BLE001
        items, errors = [], [{"error": "onbekend"}]
    if errors:
        return active_downloads(ctx, hours=6)
    try:
        cats = managed_categories(ctx, load(ctx))
    except Exception:  # noqa: BLE001
        cats = set()
    return sum(1 for i in items if i.get("status") in LIVE_ACTIVE
               and (not cats or str(i.get("category") or "") in cats))


def limits_state(ctx: AppContext) -> dict[str, Any]:
    searches_hour = ctx.db.fetch_one(
        "SELECT COUNT(*) AS n FROM optimization_items WHERE status='search_sent' AND "
        "started_at >= ?", (_since(1),))
    bytes_today = ctx.db.fetch_one(
        "SELECT COALESCE(SUM(est_bytes),0) AS b FROM optimization_items WHERE status="
        "'search_sent' AND substr(started_at,1,10)=?", (_today(),))
    encodes = ctx.db.fetch_one(
        "SELECT COUNT(*) AS n FROM encoding_jobs WHERE status IN ('submitted','queued',"
        "'running','verifying','awaiting_commit')")
    try:
        staging_free = shutil.disk_usage(ctx.settings.staging_dir).free \
            if ctx.settings.staging_dir.exists() else 0
    except OSError:
        staging_free = 0
    return {
        "searches_last_hour": int(searches_hour["n"] if searches_hour else 0),
        "bytes_today": int(bytes_today["b"] if bytes_today else 0),
        "active_encodes": int(encodes["n"] if encodes else 0),
        "active_downloads": active_downloads(ctx),
        "staging_free_bytes": staging_free,
    }


async def _search(ctx: AppContext, item: dict[str, Any], context: dict[str, Any]) -> str:
    client = arr_client(ctx, item["instance_id"])
    if client is None:
        raise RuntimeError("instance-registry niet beschikbaar")
    from mediaguard.downloads import holds

    if item["media_kind"].startswith("movie"):
        movie_id = context.get("movie_id")
        hold = holds.hold_for(ctx, holds.item_key(item["instance_id"], "movie", movie_id or 0))
        if hold is not None:
            raise RuntimeError(f"zoeken gepauzeerd: {hold['reason_nl']}")
        await client.command("MoviesSearch", movieIds=[movie_id])
        return f"MoviesSearch {movie_id}"
    episode_ids = context.get("episode_ids") or []
    if not episode_ids:
        raise RuntimeError("geen episode-ID's bekend voor dit bestand")
    await client.command("EpisodeSearch", episodeIds=episode_ids)
    return f"EpisodeSearch {episode_ids}"


WAITING_NL = {
    "max actieve downloads bereikt": "er lopen al zoveel downloads als ingesteld; het plan "
                                     "gaat vanzelf verder zodra er een klaar is",
    "zoekquotum per uur bereikt": "het zoekquotum voor dit uur is op; volgend uur verder",
    "max GB per dag bereikt": "het downloadmaximum voor vandaag is bereikt; morgen verder",
    "max gelijktijdige encodes bereikt": "de AV1-encoder heeft genoeg werk (per baan, CPU en "
                                         "GPU apart); verder zodra er plek is",
    "onvoldoende stagingruimte (incl. reserve)": "te weinig vrije werkruimte; verder zodra "
                                                 "er ruimte is",
    "noodstop actief": "noodstop staat aan",
    "wacht op akkoord voor downloaden": "betere versies downloaden start pas als jij dat "
                                        "bevestigt; de AV1-omzetting loopt via de autopilot",
    "buiten tijdvenster": "buiten het ingestelde tijdvenster; verder binnen het venster",
}


def waiting_nl(code: str, ctx: AppContext | None = None) -> str:
    text = WAITING_NL.get(code, code)
    if ctx is not None and code == "wacht op akkoord voor downloaden":
        from mediaguard.encoding.autopilot import next_round_at, stalled_since
        from mediaguard.textfmt import time_nl

        since = stalled_since(ctx)
        if since:
            return (f"{text}. Let op: de AV1-omzetting staat stil sinds {time_nl(since)}; "
                    "zie Overzicht")
        nxt = next_round_at(ctx)
        if nxt:
            return f"{text} (volgende ronde {time_nl(nxt)})"
    return text


async def run_batch(ctx: AppContext, plan_id: int) -> dict[str, Any]:
    plan = ctx.db.fetch_one("SELECT status FROM optimization_plans WHERE id=?", (plan_id,))
    if plan is None or plan["status"] != "running":
        return {"stopped": True, "reason": f"plan status {plan['status'] if plan else '?'}"}
    if ctx.emergency_stopped():
        return {"waiting": "noodstop actief", "processed": 0}
    if not in_window(ctx):
        return {"waiting": "buiten tijdvenster", "processed": 0}
    batch = int(ctx.config.value("optimization.batch_size"))
    # Rapport 8 / A7: AV1-items en zoek/download-items apart ophalen. Voorheen kwamen de eerste
    # N items (op prioriteit) samen; waren dat zoekitems die op de downloadlimiet wachtten,
    # dan bereikte het plan de AV1-items nooit en kreeg de GPU geen werk.
    # Bestanden die net 'baan vol' kregen (alleen op de volle CPU te doen) even overslaan,
    # zodat de rest van het plan de GPU kan voeden (verbeterpunt 6).
    rows = ctx.db.fetch_dicts(
        "SELECT * FROM optimization_items WHERE plan_id=? AND status='planned' "
        "AND action='av1' AND COALESCE(path,'') NOT IN (SELECT path FROM "
        "encoding_autopilot_skips WHERE code='LANE_FULL' AND retry_after>?) "
        "ORDER BY priority, id LIMIT ?", (plan_id, utcnow(), batch))
    rows += ctx.db.fetch_dicts(
        "SELECT * FROM optimization_items WHERE plan_id=? AND status='planned' "
        "AND action<>'av1' ORDER BY priority, id LIMIT ?", (plan_id, batch))
    state = limits_state(ctx)
    if any(r["action"] in SEARCH_ACTIONS for r in rows):
        state["active_downloads"] = await live_active_downloads(ctx)
    from mediaguard.encoding import lanes

    # per baan (CPU en GPU apart): CPU-werk kan de GPU niet meer uithongeren
    budget = lanes.LaneBudget.load(ctx, buffer=False)
    reserve = int(ctx.config.value("optimization.staging_reserve_gb")) * GB
    processed, deferred = 0, []
    for item in rows:
        context = loads(item.get("context_json"), {}) or {}
        chain = {"instance": item["instance_id"]} if item["instance_id"] else None
        action = item["action"]
        result: dict[str, Any] = {}
        status = item["status"]
        if action in SEARCH_ACTIONS:
            from mediaguard.optimization.planner import downloads_confirmed

            if not downloads_confirmed(ctx, plan_id):
                deferred.append("wacht op akkoord voor downloaden")
                continue
            if state["active_downloads"] >= int(ctx.config.value(
                    "optimization.max_active_downloads", chain)):
                deferred.append("max actieve downloads bereikt")
                continue
            if state["searches_last_hour"] >= int(ctx.config.value(
                    "optimization.max_searches_per_hour", chain)):
                deferred.append("zoekquotum per uur bereikt")
                continue
            if state["bytes_today"] + int(item["est_bytes"] or 0) > int(ctx.config.value(
                    "optimization.max_gb_per_day", chain)) * GB:
                deferred.append("max GB per dag bereikt")
                continue
            try:
                result["command"] = await _search(ctx, item, context)
                status = "search_sent"
                state["searches_last_hour"] += 1
                state["bytes_today"] += int(item["est_bytes"] or 0)
            except Exception as exc:  # noqa: BLE001 - per item vastleggen
                status, result["error"] = "failed", f"{type(exc).__name__}: {exc}"
        elif action == "av1":
            if not budget.has_room() or budget.refusals() >= lanes.MAX_LANE_FULL_PER_ROUND:
                deferred.append("max gelijktijdige encodes bereikt")
                continue
            if state["staging_free_bytes"] < reserve + int(item["size"] or 0):
                deferred.append("onvoldoende stagingruimte (incl. reserve)")
                continue
            try:
                refresh = ({"command": "RefreshMovie", "movieIds": [context["movie_id"]],
                            "movie_id": context["movie_id"]} if context.get("movie_id") else
                           {"command": "RescanSeries", "seriesId": context.get("series_id"),
                            "episode_ids": context.get("episode_ids") or []})
                job = await enc.submit(
                    ctx, path=item["path"], instance_id=item["instance_id"],
                    media_kind=item["media_kind"], provider_id=item["provider_id"],
                    series_id=context.get("series_id"), movie_id=context.get("movie_id"),
                    media_type=context.get("media_type"), actor=f"svc:optimize:{plan_id}",
                    refresh=refresh, avoid_lanes=budget.full_lanes())
                result["encoding_job_id"] = job.get("id")
                status = "encode_skipped" if job.get("status") == "skipped" else \
                    "encode_submitted"
                if status == "encode_submitted":
                    state["active_encodes"] += 1
                    budget.take(job.get("lane"))
                else:
                    result["reason"] = job.get("skip_reason")
            except enc.GateError as exc:
                if exc.code == "LANE_FULL":
                    # geen blokkade: blijft gepland en komt terug zodra die baan ruimte heeft
                    from mediaguard.encoding.autopilot import _remember_skip

                    _remember_skip(ctx, item["path"] or "", exc.code, exc.message)
                    budget.refused(exc.lane)
                    deferred.append("max gelijktijdige encodes bereikt")
                    continue
                status, result["error"] = "blocked", f"{exc.code}: {exc.message}"
        elif action == "verify":
            # Twijfel over de aflevering: de identiteitsautomatisering (opnieuw beoordelen,
            # AI-tiebreaker, bij zwak bewijs houden) handelt dit zelf af — geen gebruikerstaak.
            status = "auto_identity"
            result["reason"] = ("MediaGuard controleert dit zelf (identiteitscontrole, zo nodig "
                                "de AI-adviseur); bij zwak bewijs blijft het bestand staan")
        else:  # repair (dubbel/verkeerd gekoppeld): veilig laten staan, alleen ter info
            status = "kept_info"
            result["reason"] = "Bestand blijft staan; MediaGuard vervangt alleen bij sterk bewijs"
        ctx.db.execute(
            "UPDATE optimization_items SET status=?, result_json=?, started_at=? WHERE id=?",
            (status, dumps(result), utcnow(), item["id"]))
        processed += 1
    remaining = ctx.db.fetch_one(
        "SELECT COUNT(*) AS n FROM optimization_items WHERE plan_id=? AND status='planned'",
        (plan_id,))
    left = int(remaining["n"] if remaining else 0)
    if left == 0:
        ctx.db.execute("UPDATE optimization_plans SET status='completed', updated_at=? "
                       "WHERE id=?", (utcnow(), plan_id))
    return {"processed": processed, "remaining": left,
            "waiting": sorted(set(deferred))[0] if deferred and not processed else None,
            "deferred": sorted(set(deferred))}
