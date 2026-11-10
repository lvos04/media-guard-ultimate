"""AV1-banen in core: op welke baan (CPU/GPU) een job draait, hoeveel werk er per baan bij
de encoder mag liggen en of de GPU onnodig stilstaat.

Rapport 6, 7 en 8: de wachtrij had één grens voor CPU en GPU samen. Dolby Vision,
HDR-herkansingen en omgeleide jobs gaan naar de CPU (één plek) en vulden de hele rij,
terwijl de Arc A310 (4 plekken, ~460 fps) niets kreeg. Nu:

- core bewaart per job de baan (uit het antwoord van de encoder en bij elke statuspoll
  bijgewerkt, zodat een GPU→CPU-herkansing meeverhuist);
- per baan ligt er hooguit ``encodes tegelijk + voorraad`` bij de encoder;
- zit een baan vol, dan maakt de encoder voor die baan geen job aan (``avoid_lanes``); de
  autopilot slaat zo'n bestand even over en zoekt door naar werk voor de baan met ruimte;
- staat de GPU langer dan 30 minuten leeg terwijl er werk is, dan komt er een melding.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import TYPE_CHECKING, Any

from mediaguard.db import utcnow

if TYPE_CHECKING:
    from mediaguard.context import AppContext

LOGGER = logging.getLogger(__name__)
LANES = ("cpu", "gpu")
LANE_LABEL = {"cpu": "CPU", "gpu": "GPU"}
# Bezet een plek in de encoder (zelfde lijst als autopilot.ENCODER_BUSY).
BUSY = ("submitted", "queued", "running", "verifying", "handing_off")
# Een bestand dat nu alleen op een volle baan kan, komt na zo lang weer aan de beurt.
LANE_FULL_SKIP_HOURS = 0.5
# Hooguit zoveel 'baan vol'-weigeringen per ronde: elke weigering kost een probe.
MAX_LANE_FULL_PER_ROUND = 12
GPU_IDLE_MINUTES = 30
GPU_IDLE_STATE = "encoding_gpu_idle"
GPU_IDLE_KEY = "encoding.gpu_idle"


def lane_slots(ctx: AppContext) -> dict[str, int]:
    """Encodes tegelijk per baan (Instellingen → Systeem & prestaties). Meldt de encoder
    dat de GPU-baan uit staat (geen /dev/dri), dan telt de GPU niet mee."""
    slots = {"cpu": max(0, int(ctx.config.value("resources.encodes_cpu"))),
             "gpu": max(0, int(ctx.config.value("resources.encodes_gpu")))}
    caps = ctx.services.get("encoder_caps_cache")
    gpu = ((caps or {}).get("lanes") or {}).get("gpu") if isinstance(caps, dict) else None
    if isinstance(gpu, dict) and gpu.get("enabled") is False:
        slots["gpu"] = 0
    return slots


def lane_caps(ctx: AppContext) -> dict[str, int]:
    """Zoveel bestanden mogen per baan tegelijk bij de encoder liggen (bezig + wachtend)."""
    buffer = max(0, int(ctx.config.value("encoding.autopilot_lane_buffer")))
    return {lane: (n + buffer if n > 0 else 0) for lane, n in lane_slots(ctx).items()}


def busy_by_lane(ctx: AppContext) -> dict[str, int]:
    """Jobs die nu een plek in de encoder bezetten, per baan. Oude jobs zonder bekende baan
    tellen onder 'onbekend' (de eerstvolgende statuspoll vult de baan in)."""
    marks = ",".join("?" for _ in BUSY)
    rows = ctx.db.fetch_all(
        f"SELECT COALESCE(lane,'onbekend') AS lane, COUNT(*) AS n FROM encoding_jobs "  # noqa: S608
        f"WHERE status IN ({marks}) GROUP BY COALESCE(lane,'onbekend')", BUSY)
    out = {"cpu": 0, "gpu": 0, "onbekend": 0}
    for r in rows:
        out[str(r["lane"]) if str(r["lane"]) in out else "onbekend"] += int(r["n"])
    return out


@dataclass
class LaneBudget:
    """Ruimte per baan voor één ronde (autopilot of optimalisatieplan)."""

    caps: dict[str, int]
    busy: dict[str, int]
    depth: int
    submitted: dict[str, int] = field(default_factory=lambda: {"cpu": 0, "gpu": 0})
    lane_full: dict[str, int] = field(default_factory=lambda: {"cpu": 0, "gpu": 0})

    @classmethod
    def load(cls, ctx: AppContext, *, depth: int | None = None,
             buffer: bool = True) -> LaneBudget:
        """Ruimte nu. ``buffer=False``: alleen de plekken zelf, zonder voorraad (het
        optimalisatieplan vult nooit verder dan er tegelijk kan lopen)."""
        from mediaguard.encoding.autopilot import queue_depth

        caps = lane_caps(ctx) if buffer else lane_slots(ctx)
        if depth is None:
            depth = queue_depth(ctx) if buffer else max(1, sum(caps.values()))
        return cls(caps=caps, busy=busy_by_lane(ctx), depth=depth)

    def total_busy(self) -> int:
        return sum(self.busy.values()) + sum(self.submitted.values())

    def free(self, lane: str) -> int:
        return self.caps.get(lane, 0) - self.busy.get(lane, 0) - self.submitted.get(lane, 0)

    def free_total(self) -> int:
        """Plekken die deze ronde nog gevuld mogen worden: per baan, en nooit boven de
        totale wachtrijdiepte."""
        per_lane = sum(max(0, self.free(lane)) for lane in LANES)
        return max(0, min(self.depth - self.total_busy(), per_lane))

    def full_lanes(self) -> list[str]:
        return [lane for lane in LANES if self.free(lane) <= 0]

    def has_room(self) -> bool:
        return self.free_total() > 0

    def take(self, lane: str | None) -> None:
        if lane in LANES:
            self.submitted[str(lane)] += 1
        else:  # baan onbekend (oude encoder): telt alleen in het totaal
            self.busy["onbekend"] = self.busy.get("onbekend", 0) + 1

    def refused(self, lane: str | None) -> None:
        if lane in LANES:
            self.lane_full[str(lane)] += 1

    def refusals(self) -> int:
        return sum(self.lane_full.values())

    def view(self) -> dict[str, Any]:
        return {lane: {"cap": self.caps.get(lane, 0),
                       "busy": self.busy.get(lane, 0) + self.submitted.get(lane, 0),
                       "submitted": self.submitted.get(lane, 0),
                       "lane_full": self.lane_full.get(lane, 0)} for lane in LANES} | {
            "unknown_busy": self.busy.get("onbekend", 0), "depth": self.depth}


def predicted_lane(policy: dict[str, Any]) -> str | None:
    """Baan die al vastligt vóór de encoder beslist: ``encoder: cpu`` (Dolby Vision met
    behoud, of geforceerd) of ``gpu``. Bij 'auto' beslist de encoder."""
    enc = str(policy.get("encoder") or "auto")
    return enc if enc in LANES else None


def likely_cpu(item: Any) -> bool:
    """Grove inschatting uit de bibliotheekgegevens (zonder probe): Dolby Vision en HDR10+
    gaan meestal via de CPU. Alleen gebruikt om de volgorde te kiezen, nooit om over te
    slaan."""
    dr = str(getattr(item, "dynamic_range", "") or "").upper().replace(" ", "")
    return "DV" in dr or "DOVI" in dr or "HDR10PLUS" in dr or "HDR10+" in dr


def lane_full_error(exc: Exception) -> str | None:
    """Baan uit een 'baan vol'-weigering van de encoder (409 LANE_FULL), anders None."""
    text = str(exc)
    if "LANE_FULL" not in text:
        return None
    low = text.lower()
    for lane in LANES:
        if f"{lane}-baan" in low or f"lane={lane}" in low:
            return lane
    return "cpu"


def lane_full_message(lane: str) -> str:
    return (f"{LANE_LABEL.get(lane, lane)}-baan zit vol; dit bestand moet via de "
            f"{LANE_LABEL.get(lane, lane)} en komt later aan de beurt")


# -- GPU staat stil terwijl er werk is ---------------------------------------------------------
def _parse(at: Any) -> datetime | None:
    try:
        dt = datetime.fromisoformat(str(at))
    except (TypeError, ValueError):
        return None
    return dt if dt.tzinfo else dt.replace(tzinfo=UTC)


def _outside_encode_window(ctx: AppContext, now: datetime | None = None) -> bool:
    if not bool(ctx.config.value("resources.encode_window_enabled")):
        return False
    win = ctx.config.value("resources.encode_window") or {}
    start, end = str(win.get("start") or ""), str(win.get("end") or "")
    if not start or not end or start == end:
        return False
    t = (now or datetime.now()).strftime("%H:%M")
    inside = start <= t < end if start <= end else (t >= start or t < end)
    return not inside


def _benign_reason(ctx: AppContext) -> str | None:
    """Waarom een lege GPU nu bewust is (dan geen melding)."""
    from mediaguard.encoding import service as enc
    from mediaguard.encoding.autopilot import blocked_reason

    reason = blocked_reason(ctx)
    if reason:
        return reason
    if lane_slots(ctx)["gpu"] <= 0:
        return "GPU-baan uit of niet beschikbaar"
    if not enc.canary_passed(ctx):
        return "voorzichtige start"
    if ctx.state_get("mediaserver_hold_active"):
        return "iemand kijkt"
    if _outside_encode_window(ctx):
        return "buiten het tijdvenster"
    return None


def _work_waiting(ctx: AppContext, busy: dict[str, int]) -> dict[str, Any] | None:
    """Is er AV1-werk dat de GPU zou kunnen doen? Uit de laatste autopilotronde of een
    lopend optimalisatieplan, of de (ingestelde) wachtrij zit helemaal vol met CPU-werk.

    ``cpu_only``: alles wat de laatste ronde vond moet via de (volle) CPU en er ligt geen
    ander werk. Dan alleen uitleg, geen pushmelding: een bibliotheek vol Dolby Vision laat
    de GPU terecht leeg."""
    state = ctx.state_get("encoding_autopilot", {}) or {}
    candidates = int(state.get("candidates") or 0)
    gpu_left_raw = state.get("gpu_left")
    # oudere ronde zonder telling: elke kandidaat telt als mogelijk GPU-werk
    gpu_left = candidates if gpu_left_raw is None else int(gpu_left_raw or 0)
    # plan-items die net 'baan vol' kregen (alleen via de volle CPU) zijn geen GPU-werk
    planned_row = ctx.db.fetch_one(
        "SELECT COUNT(*) AS n FROM optimization_items i JOIN optimization_plans p ON "
        "p.id=i.plan_id WHERE p.status='running' AND i.status='planned' AND i.action='av1' "
        "AND COALESCE(i.path,'') NOT IN (SELECT path FROM encoding_autopilot_skips WHERE "
        "code='LANE_FULL' AND retry_after>?)", (utcnow(),))
    planned = int(planned_row["n"] if planned_row else 0)
    queue_full = state.get("waiting") == "encoder heeft genoeg werk" and busy["cpu"] > 0
    lanes = state.get("lanes") or {}
    cpu_full = int((lanes.get("cpu") or {}).get("lane_full") or 0)
    gpu_sent = int((lanes.get("gpu") or {}).get("submitted") or 0)
    cpu_only = (candidates > 0 and cpu_full > 0 and gpu_sent == 0 and gpu_left == 0
                and not planned and not queue_full)
    if not gpu_left and not planned and not queue_full and not cpu_only:
        return None
    return {"candidates": candidates, "gpu_left": gpu_left, "planned": planned,
            "cpu_full": cpu_full, "gpu_sent": gpu_sent, "queue_full": queue_full,
            "cpu_only": cpu_only}


# Statussen waarin een job de GPU bezet of net klaar is. Wachten op plaatsing telt niet:
# die rijen worden periodiek bijgewerkt (activatie uitgesteld) en lieten de stilstand-
# teller anders steeds opnieuw beginnen.
GPU_ACTIVITY = BUSY + ("awaiting_commit", "failed", "skipped", "rejected", "cancelled")


def gpu_idle(ctx: AppContext, now: datetime | None = None) -> dict[str, Any] | None:
    """GPU-baan langer dan ``GPU_IDLE_MINUTES`` zonder werk terwijl er AV1-werk is.

    Houdt in de state bij sinds wanneer (eerste waarneming of laatste GPU-activiteit) en
    vergeet dat zodra de GPU weer iets doet of het stilstaan bewust is (noodstop, kijken,
    tijdvenster, voorzichtige start). Geeft ``None`` als er niets te melden is."""
    now = now or datetime.now(UTC)
    busy = busy_by_lane(ctx)
    benign = _benign_reason(ctx)
    work = None if benign or busy["gpu"] > 0 else _work_waiting(ctx, busy)
    if work is None:
        if ctx.state_get(GPU_IDLE_STATE):
            ctx.state_set(GPU_IDLE_STATE, None)
        return None
    state = ctx.state_get(GPU_IDLE_STATE) or {}
    since = _parse(state.get("since"))
    if since is None:
        since = now
        ctx.state_set(GPU_IDLE_STATE, {"since": since.isoformat(timespec="seconds")})
    marks = ",".join("?" for _ in GPU_ACTIVITY)
    last_row = ctx.db.fetch_one(
        f"SELECT MAX(updated_at) AS at FROM encoding_jobs WHERE lane='gpu' "  # noqa: S608
        f"AND status IN ({marks})", GPU_ACTIVITY)
    last_gpu = _parse(last_row["at"]) if last_row and last_row["at"] else None
    if last_gpu is not None and last_gpu > since:
        # de GPU deed sinds de eerste waarneming nog iets: de stilstand begint opnieuw
        since = last_gpu
        ctx.state_set(GPU_IDLE_STATE, {"since": since.isoformat(timespec="seconds")})
    minutes = int((now - since).total_seconds() // 60)
    if minutes < GPU_IDLE_MINUTES:
        return None
    # Alles wat de autopilot vond moest via de CPU (bv. Dolby Vision): uitleg, geen push.
    cpu_only = bool(work["cpu_only"])
    duration = f"{minutes // 60} u {minutes % 60} min" if minutes >= 60 else f"{minutes} min"
    if cpu_only:
        detail = (f"De GPU staat al {duration} leeg. Alle bestanden die MediaGuard nu vond "
                  "moeten via de CPU (bijvoorbeeld Dolby Vision); die zit vol. MediaGuard "
                  "zoekt elke ronde verder naar werk voor de GPU.")
    else:
        parts = []
        if work["queue_full"]:
            parts.append("de wachtrij zit vol met CPU-werk; verhoog 'Bestanden tegelijk bij "
                         "de encoder' of zet die op automatisch")
        if work["gpu_left"]:
            parts.append(f"{work['gpu_left']} bestand(en) voor de GPU nog niet aangeboden")
        if work["planned"]:
            parts.append(f"{work['planned']} bestand(en) in het optimalisatieplan")
        detail = (f"De GPU staat al {duration} leeg terwijl er werk is ("
                  f"{', '.join(parts)}). MediaGuard probeert het elke ronde opnieuw; met "
                  "'Nu proberen' start je direct een ronde.")
    return {"since": since.isoformat(timespec="seconds"), "minutes": minutes,
            "duration_nl": duration, "cpu_only": cpu_only, "detail_nl": detail,
            "severity": "info" if cpu_only else "warning", **work}


def notify_gpu_idle(ctx: AppContext, idle: dict[str, Any] | None) -> bool:
    """Eén pushmelding per stilstand (niet bij 'alleen CPU-werk'). True = verstuurd."""
    if not idle or idle.get("cpu_only"):
        return False
    state = ctx.state_get(GPU_IDLE_STATE) or {}
    if state.get("notified") == idle["since"]:
        return False
    try:
        from mediaguard.api.system import notify

        notify(ctx, "warning", f"AV1: GPU staat al {idle['duration_nl']} stil",
               idle["detail_nl"], link="/av1", dedupe_key=GPU_IDLE_KEY)
    except Exception:  # noqa: BLE001 - de melding mag de ronde niet stoppen
        LOGGER.exception("melding stilstaande GPU")
        return False
    ctx.state_set(GPU_IDLE_STATE, {**state, "since": state.get("since") or idle["since"],
                                   "notified": idle["since"]})
    return True


def check_gpu_idle(ctx: AppContext, now: datetime | None = None) -> dict[str, Any] | None:
    idle = gpu_idle(ctx, now)
    notify_gpu_idle(ctx, idle)
    return idle
