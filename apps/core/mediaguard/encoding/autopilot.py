"""AV1-autopilot: MediaGuard kiest, de encoder voert uit.

De AV1-engine maakt in de container zelf geen keuzes meer (geen eigen scans, beslissingen
of AI). Deze lus doet dat in core, met exact dezelfde beslisboom als het
optimalisatieplan (``optimization.planner.classify``: drempels per resolutie, profielen,
DV-/HDR-beleid) en dezelfde poorten als een handmatige encode (identiteit, sloten,
quarantainequotum, noodstop). Alleen als de bibliotheek is vrijgegeven
(``library.write_enabled``, na de overstap) worden er bestanden aangeboden.
"""

from __future__ import annotations

import asyncio
import contextlib
import logging
from datetime import UTC, datetime, timedelta
from typing import Any

from fastapi import APIRouter, Depends

from mediaguard import taskwatch
from mediaguard.audit import audit
from mediaguard.auth import Principal, require_operator, require_viewer
from mediaguard.context import AppContext, get_app_context
from mediaguard.db import utcnow
from mediaguard.encoding import lanes
from mediaguard.encoding import service as enc
from mediaguard.settings_registry import REGISTRY, SettingDef

LOGGER = logging.getLogger(__name__)
router = APIRouter(prefix="/api/v1/encoding/autopilot", tags=["encoding"])
CATEGORY = "AV1"
# Bezet: alles wat ruimte in staging of een encoderplek inneemt
IN_FLIGHT = ("submitted", "queued", "running", "verifying", "awaiting_commit",
             "awaiting_activation", "handing_off", "handed_off")
# Een eerder resultaat voor hetzelfde pad: niet automatisch opnieuw (wel handmatig)
DONE_OR_BLOCKED = IN_FLIGHT + ("committed", "rejected", "failed", "skipped",
                               "conflict_review")
SKIP_HOURS = {"DUPLICATE": 6, "ALREADY_AV1": 24 * 30,
              "IDENTITY": 24, "LOCKED": 1, "ENCODER_UNAVAILABLE": 0.25,
              "ENCODER_ERROR": 6, "QUARANTINE_FULL": 1, "EMERGENCY_STOP": 0.25,
              "LANE_FULL": lanes.LANE_FULL_SKIP_HOURS}


def register_settings() -> None:
    REGISTRY.register(
        SettingDef("encoding.autopilot_enabled", "bool", True, CATEGORY,
                   "AV1-autopilot (MediaGuard kiest bestanden)",
                   "AV1 autopilot (MediaGuard picks files)",
                   help_nl="MediaGuard zoekt zelf de bestanden die AV1 waard zijn en stuurt ze "
                           "naar de encoder. Werkt pas als de bibliotheek is vrijgegeven (na de "
                           "overstap). De encoder maakt zelf geen keuzes."),
        SettingDef("encoding.autopilot_queue_depth", "int", 0, CATEGORY,
                   "Bestanden tegelijk bij de encoder (0 = automatisch)",
                   "Files queued at the encoder (0 = automatic)",
                   help_nl="Zoveel bestanden mogen in totaal tegelijk bij de encoder liggen "
                           "(wachtend of bezig). Automatisch = per baan (CPU en GPU) de "
                           "encodes tegelijk plus de voorraad hieronder.",
                   minimum=0, maximum=50, advanced=True),
        SettingDef("encoding.autopilot_lane_buffer", "int", 1, CATEGORY,
                   "Voorraad per baan (CPU en GPU)", "Queued extra per lane (CPU and GPU)",
                   help_nl="Zoveel bestanden mogen per baan klaarliggen naast de encodes die "
                           "al lopen. Werk voor de CPU (zoals Dolby Vision) kan zo nooit de "
                           "hele wachtrij vullen: de GPU krijgt altijd eigen werk.",
                   help_en="How many files may wait per lane on top of the running encodes, "
                           "so CPU-only work never fills the whole queue.",
                   minimum=0, maximum=10, advanced=True),
        SettingDef("encoding.autopilot_interval_minutes", "int", 10, CATEGORY,
                   "Interval AV1-autopilot", "AV1 autopilot interval",
                   minimum=1, maximum=1440, unit="min", advanced=True),
    )


# Alleen deze bezetten een plek in de encoder. Resultaten die op plaatsing wachten
# (awaiting_activation e.d.) tellen NIET mee: 9 niet-geplaatste films vulden de hele
# wachtrijdiepte en hielden de autopilot 14 uur stil (rapport 3, R3).
ENCODER_BUSY = lanes.BUSY
AWAITING_PLACEMENT = ("awaiting_commit", "awaiting_activation", "handed_off")


def _count_status(ctx: AppContext, statuses: tuple[str, ...]) -> int:
    marks = ",".join("?" for _ in statuses)
    row = ctx.db.fetch_one(f"SELECT COUNT(*) AS n FROM encoding_jobs WHERE status IN ({marks})",
                           statuses)
    return int(row["n"] if row else 0)


def _in_flight(ctx: AppContext) -> int:
    return _count_status(ctx, ENCODER_BUSY)


def placement_backlog_cap(depth: int) -> int:
    """Zoveel resultaten mogen op plaatsing wachten voordat er geen nieuwe bij komen
    (werkruimte), ruim boven de wachtrijdiepte."""
    return max(20, depth * 4)


def next_round_at(ctx: AppContext) -> str | None:
    state = ctx.state_get("encoding_autopilot", {}) or {}
    return state.get("next_at")


def stalled_since(ctx: AppContext, *, threshold_minutes: int = 60) -> str | None:
    """Tijdstip sinds wanneer de AV1-omzetting stilstaat terwijl er werk is (None = loopt).

    Stil = geen encode bezig en de laatste indiening langer dan ``threshold_minutes``
    geleden, terwijl de laatste ronde nog kandidaten zag of een plan AV1-items heeft."""
    if blocked_reason(ctx) or _in_flight(ctx):
        return None
    last = ctx.state_get("encoding_autopilot_last_submit", None)
    if not last:
        return None
    state = ctx.state_get("encoding_autopilot", {}) or {}
    planned = ctx.db.fetch_one(
        "SELECT COUNT(*) AS n FROM optimization_items i JOIN optimization_plans p ON "
        "p.id=i.plan_id WHERE p.status='running' AND i.status='planned' AND i.action='av1'")
    if not int(state.get("candidates") or 0) and not int((planned or {"n": 0})["n"]):
        return None
    try:
        at = datetime.fromisoformat(str(last))
    except ValueError:
        return None
    if at.tzinfo is None:
        at = at.replace(tzinfo=UTC)
    if datetime.now(UTC) - at < timedelta(minutes=threshold_minutes):
        return None
    return at.isoformat(timespec="seconds")


# Afgewezen door een opslag-/rechtenprobleem bij het vervangen (niet door de encode zelf):
# de encode was goed en mag na een dag automatisch opnieuw, hooguit ENV_MAX_RETRIES keer.
ENV_ERROR_HINTS = ("PermissionError", "Operation not permitted", "Permission denied",
                   "[Errno 1]", "[Errno 13]", "[Errno 28]", "[Errno 30]", "No space left",
                   "Read-only file system", "Geen schrijfrechten", "Onvoldoende ruimte")
ENV_RETRY_AFTER_HOURS = 24
ENV_MAX_RETRIES = 2
# Slaagt de schrijftest later wél voor de map van het bestand, dan was de oorzaak opgelost:
# meteen opnieuw, ook na de gewone pogingen (drie films in rapport 4, S7).
ENV_MAX_AFTER_PROBE = 5
# Onderbroken, niet kapot: container-stop/update, SIGTERM (ffmpeg 255), engine-herstart,
# encoder onbereikbaar of job kwijt, time-outs. Snel en vaker opnieuw.
TRANSIENT_HINTS = ("hersteld na backendherstart", "onderbroken", "servicestop", "sigterm",
                   "exitcode 255", "exit code 255", "returncode 255", "kent deze job niet meer",
                   "niet bereikbaar", "onbereikbaar", "timeout", "timed out", "time-out",
                   "connecterror", "connection", "readerror", "remoteprotocolerror",
                   "http 502", "http 503", "http 504", "servicestop", "herstart",
                   # tijdelijke omstandigheden die de encoder zelf benoemt (verbeterpunt 28)
                   "geen ruimte meer op de cacheschijf", "te weinig werkgeheugen")
TRANSIENT_MAX_RETRIES = 3
TRANSIENT_BACKOFF_MINUTES = (15, 30, 60)
# Inhoudelijk (bron onleesbaar, verificatie mislukt, …): één keer na een dag, dan stoppen.
DETERMINISTIC_MAX_RETRIES = 1
DETERMINISTIC_RETRY_HOURS = 24
# 'Te traag voor de CPU' (overgeslagen): geen fout maar ook geen eindoordeel. De meting kan
# verstoord zijn (noodstop, andere zware taken); na een dag opnieuw, hooguit twee keer.
# Daarna blijft het origineel stil staan (geen probleemmelding; verbeterpunt 28).
SLOW_MARKER = "te traag voor de cpu"
SLOW_MAX_RETRIES = 2
SLOW_RETRY_HOURS = 24
USER_CANCEL = "door gebruiker geannuleerd"
FAILURE_STATUSES = ("failed", "rejected", "cancelled")


def environment_rejected(error: str | None) -> bool:
    return bool(error) and any(h in str(error) for h in ENV_ERROR_HINTS)


def classify_failure(status: str, error: str | None) -> str | None:
    """'environment', 'transient', 'deterministic', 'slow' of None (geen mislukking:
    blokkeert)."""
    text = str(error or "")
    low = text.lower()
    if status == "skipped":
        return "slow" if SLOW_MARKER in low else None
    if status == "cancelled":
        # geannuleerd ('Origineel houden') is een keuze; alleen een onderbreking telt als fout
        if USER_CANCEL in text or "geannuleerd" in low:
            return None
        return "transient" if any(h in low for h in TRANSIENT_HINTS) else None
    if status not in ("failed", "rejected"):
        return None
    if "origineel teruggezet" in low:
        # afgekeurd ná plaatsing en automatisch teruggedraaid: opgelost, geen fout meer
        # (rapport 4, S7: een teruggedraaide serie telde nog mee in 'Fouten')
        return None
    if status == "rejected" and environment_rejected(text):
        return "environment"
    if status == "failed" and (not text or any(h in low for h in TRANSIENT_HINTS)):
        return "transient"
    return "deterministic"


def _wait(kind: str, n: int) -> timedelta:
    if kind == "transient":
        return timedelta(minutes=TRANSIENT_BACKOFF_MINUTES[
            min(n, len(TRANSIENT_BACKOFF_MINUTES)) - 1])
    if kind == "environment":
        return timedelta(hours=ENV_RETRY_AFTER_HOURS)
    if kind == "slow":
        return timedelta(hours=SLOW_RETRY_HOURS)
    return timedelta(hours=DETERMINISTIC_RETRY_HOURS)


MAX_RETRIES = {"transient": TRANSIENT_MAX_RETRIES, "environment": ENV_MAX_RETRIES,
               "deterministic": DETERMINISTIC_MAX_RETRIES, "slow": SLOW_MAX_RETRIES}


def _passing_probe_roots(ctx: AppContext) -> list[tuple[str, str]]:
    """(map, tijdstip) van mappen waar de laatste schrijftest (incl. titelmap) slaagde."""
    from mediaguard.recovery import probe

    state = ctx.state_get(probe.STATE_KEY, {}) or {}
    return [(str(r.get("path")), str(r.get("checked_at") or state.get("checked_at") or ""))
            for r in state.get("roots") or [] if r.get("ok") and r.get("path")]


def _probe_passed_since(roots: list[tuple[str, str]], path: str, since: str) -> bool:
    for root, at in roots:
        under = path == root or path.startswith(root.rstrip("/") + "/")
        if under and at and at > since:
            return True
    return False


def retry_states(ctx: AppContext) -> dict[str, dict[str, Any]]:
    """Per bronpad: 'blocked' (eerder resultaat/keuze of bezig), 'wait' (opnieuw na
    backoff), 'retry' (nu opnieuw) of 'final' (pogingen op: in Problemen)."""
    marks = ",".join("?" for _ in DONE_OR_BLOCKED + ("cancelled",))
    rows = ctx.db.fetch_dicts(
        f"SELECT id, source_path, status, COALESCE(error, skip_reason) AS error, updated_at "
        f"FROM encoding_jobs WHERE status IN ({marks}) ORDER BY id",
        (*DONE_OR_BLOCKED, "cancelled"))
    by_path: dict[str, list[dict[str, Any]]] = {}
    for r in rows:
        by_path.setdefault(str(r["source_path"]), []).append(r)
    now = datetime.now(UTC)
    out: dict[str, dict[str, Any]] = {}
    probe_roots = _passing_probe_roots(ctx)
    for path, jobs in by_path.items():
        last = jobs[-1]
        kind = classify_failure(str(last["status"]), last["error"])
        if kind is None or any(classify_failure(str(j["status"]), j["error"]) is None
                               and j["status"] != "cancelled" for j in jobs):
            out[path] = {"state": "blocked"}
            continue
        failures = [j for j in jobs if classify_failure(str(j["status"]), j["error"]) == kind]
        retries_done = len(failures) - 1
        info = {"kind": kind, "job_id": last["id"], "error": last["error"],
                "attempts": len(failures)}
        if kind == "environment" and retries_done < ENV_MAX_AFTER_PROBE \
                and _probe_passed_since(probe_roots, path, str(last["updated_at"])):
            out[path] = {"state": "retry", "due": now.isoformat(timespec="seconds"),
                         "after_probe": True, **info}
            continue
        if retries_done >= MAX_RETRIES[kind]:
            # te traag: bewust overgeslagen, origineel blijft; geen probleem voor de gebruiker
            out[path] = {"state": "blocked" if kind == "slow" else "final", **info}
            continue
        try:
            at = datetime.fromisoformat(str(last["updated_at"]))
        except ValueError:
            at = now
        if at.tzinfo is None:
            at = at.replace(tzinfo=UTC)
        due = at + _wait(kind, len(failures))
        out[path] = {"state": "retry" if due <= now else "wait",
                     "due": due.isoformat(timespec="seconds"), **info}
    return out


def _known_paths(ctx: AppContext) -> set[str]:
    known = {p for p, st in retry_states(ctx).items() if st["state"] != "retry"}
    skips = ctx.db.fetch_all("SELECT path FROM encoding_autopilot_skips WHERE retry_after>?",
                             (utcnow(),))
    from mediaguard.mediaserver.service import watched_paths  # recent bekeken: nog niet

    return known | {r["path"] for r in skips} | watched_paths(ctx)


def _remember_skip(ctx: AppContext, path: str, code: str, reason: str) -> None:
    hours = SKIP_HOURS.get(code, 24)
    until = (datetime.now(UTC) + timedelta(hours=hours)).isoformat(timespec="seconds")
    ctx.db.execute(
        "INSERT INTO encoding_autopilot_skips(path,code,reason_nl,retry_after,updated_at) "
        "VALUES (?,?,?,?,?) ON CONFLICT(path) DO UPDATE SET code=excluded.code, "
        "reason_nl=excluded.reason_nl, retry_after=excluded.retry_after, "
        "updated_at=excluded.updated_at", (path, code, reason[:500], until, utcnow()))


def queue_depth(ctx: AppContext) -> int:
    """Ingesteld aantal, of automatisch: per baan de encodes tegelijk plus de voorraad
    (rapport 6/7/8: één gezamenlijke grens liet CPU-werk de GPU uithongeren)."""
    explicit = int(ctx.config.value("encoding.autopilot_queue_depth"))
    return explicit if explicit > 0 else max(1, sum(lanes.lane_caps(ctx).values()))


def blocked_reason(ctx: AppContext) -> str | None:
    """Waarom de autopilot nu niets aanbiedt (None = mag)."""
    from mediaguard.recovery.release_check import quota_exceeded

    if not bool(ctx.config.value("encoding.autopilot_enabled")):
        return "uitgeschakeld"
    if ctx.emergency_stopped():
        return "noodstop actief"
    if not bool(ctx.config.value("library.write_enabled")):
        return "bibliotheek nog vergrendeld (vóór de overstap)"
    if quota_exceeded(ctx):
        return "quarantaine vol"
    return None


SCAN_BATCH = 4000


def _pick_candidates(ctx: AppContext, free: int,
                     cpu_full: bool = False) -> list[Any]:
    """Grootste niet-AV1-bestanden eerst, in porties van ``SCAN_BATCH`` met een doorlopende
    cursor: per ronde hooguit een paar duizend bestanden beoordelen (was: de hele
    bibliotheek, ~10 s per ronde bij 100k bestanden). Stopt zodra er genoeg kandidaten zijn;
    na het einde begint de cursor weer bij de grootste bestanden."""
    import os

    from mediaguard.optimization.planner import classify
    from mediaguard.optimization.provider import get_provider
    from mediaguard.recovery import probe

    known = _known_paths(ctx)
    failing = [str(r["path"]) for r in probe.status(ctx)["failing"]]
    want = max(free * 20, 20)
    candidates: list[Any] = []
    state = ctx.state_get("encoding_autopilot_cursor", {}) or {}
    offset = int(state.get("offset") or 0)
    seen = 0
    provider = get_provider(ctx)
    batch = list(provider.items(ctx, {"av1_candidates": True, "stat": False,
                                      "limit": SCAN_BATCH, "offset": offset}))
    if not batch and offset:
        offset = 0
        batch = list(provider.items(ctx, {"av1_candidates": True, "stat": False,
                                          "limit": SCAN_BATCH, "offset": 0}))
    for item in batch:
        seen += 1
        if not item.path or item.path in known or not item.has_file:
            continue
        if failing and any(probe._under(item.path, root) for root in failing):
            continue  # schrijftest van die map faalt: vervangen zou toch niet lukken
        c = classify(ctx, item)
        if c.action != "av1":
            continue
        # hardlinks (seeding) pas controleren voor echte kandidaten: één stat i.p.v. 100k
        if item.nlink is None:
            try:
                item.nlink = os.stat(item.path).st_nlink
            except OSError:
                item.nlink = None
            if (item.nlink or 1) > 1:
                c = classify(ctx, item)
                if c.action != "av1":
                    continue
        candidates.append(c)
        if len(candidates) >= want:
            break
    # cursor verder (of terug naar het begin als de portie niet vol was)
    next_offset = offset + seen if len(batch) >= SCAN_BATCH or seen < len(batch) else 0
    ctx.state_set("encoding_autopilot_cursor", {"offset": next_offset})
    candidates.sort(key=lambda c: (c.priority, -c.est_saving))
    if cpu_full:
        # CPU zit vol: eerst wat waarschijnlijk naar de GPU kan (geen DV/HDR10+). Alleen de
        # volgorde; de encoder bepaalt de echte baan.
        candidates.sort(key=lambda c: lanes.likely_cpu(c.item))
    return candidates


async def tick(ctx: AppContext) -> dict[str, Any]:
    """Eén ronde: vul de encoderwachtrij per baan aan (CPU en GPU apart begrensd)."""

    reason = blocked_reason(ctx)
    if reason:
        return {"submitted": 0, "waiting": reason}
    depth = queue_depth(ctx)
    if not enc.canary_passed(ctx):
        depth = 1  # voorzichtige start: één tegelijk tot de eerste conversies geslaagd zijn
    budget = lanes.LaneBudget.load(ctx, depth=depth)
    if not budget.has_room():
        return {"submitted": 0, "waiting": "encoder heeft genoeg werk", "lanes": budget.view()}
    awaiting = _count_status(ctx, AWAITING_PLACEMENT)
    if awaiting >= placement_backlog_cap(depth):
        return {"submitted": 0, "waiting": f"{awaiting} omgezette bestanden wachten op "
                                           "plaatsing; nieuwe pas als die geplaatst zijn"}
    # Eerst bestanden die nu opnieuw mogen (bv. PermissionError en de schrijftest slaagt
    # weer): niet wachten tot de bibliotheekscan ze toevallig weer tegenkomt (rapport 5, T8).
    retried, skipped = await _resubmit_due(ctx, budget)
    if not budget.has_room():
        _note_submit(ctx, retried)
        return {"submitted": retried, "retried": retried, "skipped": skipped,
                "candidates": 0, "lanes": budget.view()}
    # De selectie leest de hele bibliotheek en draait daarom buiten de event loop (was:
    # ~17 s geblokkeerd per ronde bij 100k bestanden; hooks en UI stonden dan stil).
    candidates = await asyncio.to_thread(_pick_candidates, ctx, budget.free_total(),
                                         "cpu" in budget.full_lanes())
    submitted = tried = 0
    for c in candidates:
        if not budget.has_room() or budget.refusals() >= lanes.MAX_LANE_FULL_PER_ROUND:
            break
        tried += 1
        it = c.item
        refresh = ({"command": "RefreshMovie", "movieIds": [it.movie_id],
                    "movie_id": it.movie_id} if it.movie_id else
                   {"command": "RescanSeries", "seriesId": it.series_id,
                    "episode_ids": it.episode_ids})
        try:
            job = await enc.submit(
                ctx, path=it.path or "", instance_id=it.instance_id, media_kind=it.media_kind,
                provider_id=it.provider_id, series_id=it.series_id, movie_id=it.movie_id,
                media_type=it.media_type, actor="svc:av1-autopilot", refresh=refresh,
                avoid_lanes=budget.full_lanes())
        except enc.GateError as exc:
            _remember_skip(ctx, it.path or "", exc.code, exc.message)
            skipped += 1
            if exc.code == "LANE_FULL":
                budget.refused(exc.lane)
                continue  # zoek door naar werk voor de baan met ruimte
            if exc.code in ("ENCODER_UNAVAILABLE", "EMERGENCY_STOP", "QUARANTINE_FULL"):
                break  # geldt voor alles: deze ronde stoppen
            continue
        if job.get("status") != "skipped":
            submitted += 1
            budget.take(job.get("lane"))
    _note_submit(ctx, submitted + retried)
    # Kandidaten die waarschijnlijk naar de GPU kunnen maar deze ronde niet aan de beurt
    # kwamen. Alleen die maken een stilstaande GPU een probleem; zijn ze er niet, dan is
    # al het gevonden werk CPU-werk (bv. Dolby Vision) en volgt geen pushmelding.
    gpu_left = sum(1 for c in candidates[tried:] if not lanes.likely_cpu(c.item))
    return {"submitted": submitted + retried, "retried": retried, "skipped": skipped,
            "candidates": len(candidates), "gpu_left": gpu_left, "lanes": budget.view()}


def _note_submit(ctx: AppContext, submitted: int) -> None:
    if submitted:
        ctx.state_set("encoding_autopilot_last_submit", utcnow())
        audit(ctx.db, actor="svc:av1-autopilot", action="encoding.autopilot",
              target_type="encoding", target_id=None, new_value={"submitted": submitted})


RETRY_ORDER = {"environment": 0, "transient": 1, "deterministic": 2, "slow": 3}


async def _resubmit_due(ctx: AppContext, budget: lanes.LaneBudget | int) -> tuple[int, int]:
    """Paden met retry-status direct opnieuw indienen, met de gegevens van de vorige job.
    Houdt de ruimte per baan aan (een getal = alleen een totaal, voor oude aanroepers)."""
    from pathlib import Path

    if isinstance(budget, int):
        budget = lanes.LaneBudget(caps={"cpu": budget, "gpu": budget},
                                  busy={"cpu": 0, "gpu": 0}, depth=budget)

    due = [(p, st) for p, st in retry_states(ctx).items() if st.get("state") == "retry"]
    due.sort(key=lambda x: (RETRY_ORDER.get(str(x[1].get("kind")), 9), x[1]["job_id"]))
    skips = {r["path"] for r in ctx.db.fetch_all(
        "SELECT path FROM encoding_autopilot_skips WHERE retry_after>?", (utcnow(),))}
    done = skipped = 0
    for path, st in due:
        if not budget.has_room() or budget.refusals() >= lanes.MAX_LANE_FULL_PER_ROUND:
            break
        if path in skips or not Path(path).is_file():
            continue
        last = enc.get_job(ctx, int(st["job_id"])) or {}
        meta = last.get("decision") or {}
        try:
            job = await enc.submit(
                ctx, path=path, instance_id=last.get("instance_id"),
                media_kind=last.get("media_kind"), provider_id=last.get("provider_id"),
                series_id=meta.get("series_id"), movie_id=meta.get("movie_id"),
                actor="svc:av1-autopilot", refresh=meta.get("refresh") or None,
                avoid_lanes=budget.full_lanes())
        except enc.GateError as exc:
            _remember_skip(ctx, path, exc.code, exc.message)
            skipped += 1
            if exc.code == "LANE_FULL":
                budget.refused(exc.lane)
                continue
            if exc.code in ("ENCODER_UNAVAILABLE", "EMERGENCY_STOP", "QUARANTINE_FULL"):
                break
            continue
        if job.get("status") != "skipped":
            done += 1
            budget.take(job.get("lane"))
            audit(ctx.db, actor="svc:av1-autopilot", action="encoding.retry",
                  target_type="encoding_job", target_id=job.get("id"),
                  instance_id=last.get("instance_id"),
                  reason=f"Opnieuw na eerdere fout ({st.get('kind')}): "
                         f"{str(st.get('error') or '')[:200]}")
    return done, skipped


async def _loop(ctx: AppContext) -> None:
    await asyncio.sleep(30)
    while True:
        try:
            minutes = max(1, int(ctx.config.value("encoding.autopilot_interval_minutes")))
        except Exception:  # noqa: BLE001 - instelling onleesbaar: standaard
            minutes = 10
        next_at = (datetime.now(UTC) + timedelta(minutes=minutes)).isoformat(timespec="seconds")
        try:
            result = await tick(ctx)
            ctx.state_set("encoding_autopilot", {**result, "at": utcnow(), "next_at": next_at})
            taskwatch.ok(ctx, "av1_autopilot")
        except Exception as exc:  # noqa: BLE001 - lus nooit laten sterven
            LOGGER.exception("AV1-autopilot mislukt")
            taskwatch.fail(ctx, "av1_autopilot", exc)
        try:
            # ook als niemand de app opent: GPU > 30 min leeg terwijl er werk is → melding
            lanes.check_gpu_idle(ctx)
        except Exception:  # noqa: BLE001
            LOGGER.exception("controle stilstaande GPU mislukt")
        await asyncio.sleep(60 * minutes)


async def startup(ctx: AppContext) -> None:
    if ctx.settings.start_background and ctx.settings.role in ("core", "all"):
        ctx.services["encoding_autopilot_task"] = taskwatch.spawn(
            ctx, "av1_autopilot", _loop, label_nl="AV1-autopilot",
            every=lambda c: 60 * int(c.config.value("encoding.autopilot_interval_minutes")))


async def shutdown(ctx: AppContext) -> None:
    task = ctx.services.pop("encoding_autopilot_task", None)
    if task is not None:
        task.cancel()
        with contextlib.suppress(asyncio.CancelledError):
            await task


@router.get("")
def status(_: Principal = Depends(require_viewer),
           ctx: AppContext = Depends(get_app_context)) -> dict[str, Any]:
    skips = ctx.db.fetch_dicts(
        "SELECT path, code, reason_nl, retry_after FROM encoding_autopilot_skips WHERE "
        "retry_after>? ORDER BY updated_at DESC LIMIT 100", (utcnow(),))
    budget = lanes.LaneBudget.load(ctx)
    return {"enabled": bool(ctx.config.value("encoding.autopilot_enabled")),
            "blocked": blocked_reason(ctx), "in_flight": _in_flight(ctx),
            "queue_depth": queue_depth(ctx), "lanes": budget.view(),
            "gpu_idle": lanes.gpu_idle(ctx),
            "awaiting_placement": _count_status(ctx, AWAITING_PLACEMENT),
            "next_at": next_round_at(ctx), "stalled_since": stalled_since(ctx),
            "last": ctx.state_get("encoding_autopilot", {}), "skipped": skips}


@router.post("/run")
async def run_now(principal: Principal = Depends(require_operator),
                  ctx: AppContext = Depends(get_app_context)) -> dict[str, Any]:
    result = await tick(ctx)
    ctx.state_set("encoding_autopilot", {**result, "at": utcnow(), "by": principal.actor})
    return result
