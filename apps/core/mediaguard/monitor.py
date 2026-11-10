"""Bewaking op de achtergrond (verbeterlijst 1, 9 en 37).

Elke paar minuten, ook als niemand de app openheeft:

* de TrueNAS-pool meten (als de API is ingesteld) en bewaren; bij een pool die niet gezond is
  pauzeert het opruimen van originelen (``recovery.api.purge_paused_reason``);
* alle 'Wacht op jou'-punten (met de AV1-engine) en het gezondheidsmodel nalopen;
* fouten en punten met ``push=True`` direct naar je telefoon sturen via de bestaande
  meldingen (``api.system.notify``: samenvoegen per sleutel, uurmaximum per kanaal en
  hooguit eens per 6 uur dezelfde melding opnieuw).

Eigen stilstandmeldingen (punten in 'Wacht op jou' die vanzelf verdwijnen zodra het weer
loopt): een aanvraag zonder voortgang, een noodstop die al lang aanstaat, een Seerr-sync die
blijft mislukken, een achtergrondtaak die te lang niets deed en een pool die niet gezond is.
Een AV1-baan die stilstaat komt uit de engine (``attention._lane_idle_item``).

Geen spam: een punt geeft één push als het verschijnt (en weer één als er iets nieuws bij
komt, bijvoorbeeld een tweede vastgelopen aanvraag). Verdwijnt het twee rondes achter elkaar,
dan is de melding opgelost; komt het binnen het uur terug, dan volgt geen nieuwe push.
"""

from __future__ import annotations

import asyncio
import logging
import time
from datetime import UTC, datetime, timedelta
from typing import Any

from fastapi import APIRouter, Depends

from mediaguard import taskwatch
from mediaguard.auth import Principal, require_operator, require_viewer
from mediaguard.context import AppContext, get_app_context
from mediaguard.db import loads, utcnow
from mediaguard.settings_registry import REGISTRY, SettingDef

LOGGER = logging.getLogger(__name__)
router = APIRouter(prefix="/api/v1/monitor", tags=["monitor"])
CATEGORY = "Meldingen"
ALERTS_KEY = "monitor_alerts"
LAST_KEY = "monitor_last"
PROGRESS_KEY = "monitor_request_progress"
START_DELAY_SECONDS = 180  # eerst Sonarr/Radarr, Seerr en de AV1-engine laten opstarten
RESOLVE_AFTER_ROUNDS = 2  # zo vaak achter elkaar weg = opgelost
REPUSH_COOLDOWN_MINUTES = 60  # na 'opgelost' binnen het uur terug: geen nieuwe push
SEERR_FAIL_MINUTES = 30
SEERR_MIN_FAILS = 2
ENCODER_DEGRADED_MINUTES = 30
# Fouten die bij een herstart of update even optreden: pas pushen als ze blijven
SETTLE_MINUTES = {"encoder.offline": 10, "recovery.not_ready": 10}
# Stappen waarin een aanvraag hoort op te schieten (zoeken mag lang duren: een release
# bestaat soms nog niet; daarvoor is er 'Nog nergens te vinden'). Downloaden mag dubbel zo lang.
STALL_STAGES = {"requested": 1, "adding": 1, "importing": 1, "checking": 1, "downloading": 2}
# Taken met een eigen melding bij mislukken hoeven dan niet nog eens als 'stil' te komen
# (hangt zo'n taak zonder fout, dan komt hij wel in de lijst)
OWN_ALERT_TASKS = ("seerr_sync",)


def register_settings() -> None:
    defs = [
        SettingDef("monitor.enabled", "bool", True, CATEGORY, "Bewaking op de achtergrond",
                   "Background monitoring",
                   help_nl="Controleert elke paar minuten alles, ook als de app dicht is, en "
                           "stuurt problemen direct naar je telefoon (zie 'Fouten: wanneer "
                           "melden'). Uit = alleen zichtbaar in de app."),
        SettingDef("monitor.interval_minutes", "int", 5, CATEGORY,
                   "Bewaking: elke zoveel minuten", "Monitoring interval (minutes)",
                   help_nl="Hoe vaak de bewaking alles nagaat.", minimum=1, maximum=60,
                   unit="min", advanced=True),
        SettingDef("monitor.lane_idle_minutes", "int", 30, CATEGORY,
                   "Melden als een AV1-baan stilstaat (minuten)",
                   "Alert when an AV1 lane is idle (minutes)",
                   help_nl="Een AV1-baan die zo lang niets doet terwijl er werk wacht, geeft een "
                           "melding. Kijken in Emby, je eigen pauze, stille uren en de noodstop "
                           "tellen niet mee.", minimum=30, maximum=1440, unit="min",
                   advanced=True),
        SettingDef("monitor.request_stall_hours", "int", 12, CATEGORY,
                   "Melden als een aanvraag niet verder komt (uur)",
                   "Alert when a request makes no progress (hours)",
                   help_nl="Een aanvraag die zo lang op toevoegen, importeren of controleren "
                           "blijft staan, geeft een melding. Downloaden mag twee keer zo lang "
                           "duren. Zoeken telt niet: dan bestaat de release vaak nog niet.",
                   minimum=2, maximum=168, unit="uur", advanced=True),
        SettingDef("monitor.emergency_stop_hours", "int", 2, CATEGORY,
                   "Melden als de noodstop lang aanstaat (uur)",
                   "Alert when the emergency stop stays on (hours)",
                   help_nl="Een vergeten noodstop houdt alles tegen. Na zoveel uur komt er een "
                           "melding met een knop om hem op te heffen.", minimum=1, maximum=168,
                   unit="uur", advanced=True),
    ]
    known = {d.key for d in REGISTRY.all()}
    REGISTRY.register(*[d for d in defs if d.key not in known])


def _value(ctx: AppContext, key: str, default: Any) -> Any:
    try:
        return ctx.config.value(key)
    except Exception:  # noqa: BLE001 - instelling (nog) niet geregistreerd
        return default


def _parse(value: Any) -> datetime | None:
    if not value:
        return None
    try:
        dt = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    except ValueError:
        return None
    return dt if dt.tzinfo else dt.replace(tzinfo=UTC)


def duration_nl(seconds: float) -> str:
    minutes = int(seconds // 60)
    if minutes < 60:
        return f"{minutes} min"
    hours = minutes // 60
    if hours < 48:
        rest = minutes % 60
        return f"{hours} u" + (f" {rest} min" if rest and hours < 10 else "")
    return f"{hours // 24} dagen"


def _item(key: str, severity: str, title: str, detail: str, href: str, **extra: Any
          ) -> dict[str, Any]:
    from mediaguard.attention import _item as base

    item = base(key, severity, title, detail, href, count=extra.pop("count", 1),
                actions=extra.pop("actions", None), rows=extra.pop("rows", None))
    item.update(extra)
    return item


# -- stilstandbronnen ('Wacht op jou') -----------------------------------------------------------
def emergency_stop_item(ctx: AppContext, now: datetime | None = None) -> list[dict[str, Any]]:
    from mediaguard.attention import _act, _dismiss_act, _hidden
    from mediaguard.textfmt import time_nl

    st = ctx.state_get("emergency_stop", {}) or {}
    key = "system.emergency_stop"
    since = _parse(st.get("since")) if st.get("active") else None
    hours = int(_value(ctx, "monitor.emergency_stop_hours", 2))
    now = now or datetime.now(UTC)
    if since is None or now - since < timedelta(hours=hours):
        _hidden(ctx, key, None if since is None else str(st.get("since")))
        return []
    if _hidden(ctx, key, str(st.get("since"))):
        return []
    reason = str(st.get("reason") or "").strip()
    return [_item(
        key, "warning", f"Noodstop staat al {duration_nl((now - since).total_seconds())} aan",
        f"Sinds {time_nl(st.get('since'))}" + (f" ({reason})" if reason else "")
        + ". Zolang de noodstop aanstaat, start MediaGuard geen downloads, encodes of "
          "vervangingen.", "/",
        actions=[_act("Noodstop opheffen", "/system/emergency-stop", body={"active": False},
                      primary=True, confirm="MediaGuard gaat daarna weer verder met "
                                            "downloaden, omzetten en vervangen."),
                 _dismiss_act([(key, str(st.get("since")), "Noodstop staat bewust aan")])],
        push=True)]


def _stage_start(ctx: AppContext, item_id: int, stage: str) -> str | None:
    row = ctx.db.fetch_one(
        "SELECT MIN(ts) AS ts FROM delivery_events WHERE item_id=? AND stage=? AND id > "
        "COALESCE((SELECT MAX(id) FROM delivery_events WHERE item_id=? AND stage<>?), 0)",
        (item_id, stage, item_id, stage))
    return str(row["ts"]) if row and row["ts"] else None


def _progress_fp(row: dict[str, Any]) -> str:
    p = loads(row.get("progress_json"), {}) or {}
    return f"{row['stage']}|{p.get('have')}|{p.get('missing')}|{p.get('total')}"


def _stall_candidates(ctx: AppContext) -> list[dict[str, Any]]:
    marks = ",".join("?" for _ in STALL_STAGES)
    return ctx.db.fetch_dicts(
        "SELECT d.id, d.title, d.stage, d.detail_nl, d.progress_json, d.instance_id, "
        "d.requested_at, d.created_at FROM delivery_items d LEFT JOIN instances i ON "
        "i.id=d.instance_id WHERE d.state='active' AND d.stopped_at IS NULL AND "
        "d.stage IN (" + marks + ") AND "
        "COALESCE(i.suspended,0)=0 AND COALESCE(i.shadow_mode,0)=0 ORDER BY d.id",
        tuple(STALL_STAGES))


def update_request_progress(ctx: AppContext, now: datetime | None = None) -> dict[str, Any]:
    """Per actieve aanvraag: wanneer veranderde er voor het laatst iets (stap of aantal
    binnen)? Alleen de bewaking schrijft dit; 'Wacht op jou' leest het."""
    now_iso = (now or datetime.now(UTC)).isoformat(timespec="seconds")
    old = ctx.state_get(PROGRESS_KEY, {}) or {}
    new: dict[str, Any] = {}
    # tijdens een noodstop of met de motor uit telt de klok niet: na het opheffen begint hij
    # opnieuw, anders volgt meteen een melding over aanvragen die bewust stillagen
    halted = ctx.emergency_stopped() or not _value(ctx, "delivery.enabled", True)
    for r in _stall_candidates(ctx):
        fp = _progress_fp(r)
        rec = old.get(str(r["id"]))
        if halted:
            rec = {"fp": fp, "since": now_iso}
        elif not isinstance(rec, dict) or rec.get("fp") != fp:
            since = now_iso if isinstance(rec, dict) else (
                _stage_start(ctx, int(r["id"]), str(r["stage"]))
                or r["requested_at"] or r["created_at"] or now_iso)
            rec = {"fp": fp, "since": since}
        new[str(r["id"])] = rec
    if new != old:
        ctx.state_set(PROGRESS_KEY, new)
    return new


def stalled_requests(ctx: AppContext, now: datetime | None = None) -> list[dict[str, Any]]:
    from mediaguard.delivery.store import STAGE_NL

    if not _value(ctx, "delivery.enabled", True) or ctx.emergency_stopped():
        return []  # motor uit of noodstop: dan staat alles bewust stil (eigen melding)
    now = now or datetime.now(UTC)
    hours = int(_value(ctx, "monitor.request_stall_hours", 12))
    progress = ctx.state_get(PROGRESS_KEY, {}) or {}
    out = []
    for r in _stall_candidates(ctx):
        rec = progress.get(str(r["id"]))
        if isinstance(rec, dict) and rec.get("fp") == _progress_fp(r):
            since = _parse(rec.get("since"))
        else:  # bewaking nog niet gedraaid: begin van deze stap uit de tijdlijn
            since = _parse(_stage_start(ctx, int(r["id"]), str(r["stage"]))
                           or r["requested_at"] or r["created_at"])
        if since is None:
            continue
        limit = timedelta(hours=hours * STALL_STAGES[str(r["stage"])])
        if now - since < limit:
            continue
        out.append({"id": int(r["id"]), "title": r["title"] or f"Aanvraag #{r['id']}",
                    "stage": r["stage"], "stage_nl": STAGE_NL.get(r["stage"], r["stage"]),
                    "since": since.isoformat(timespec="seconds"),
                    "seconds": (now - since).total_seconds(), "detail_nl": r["detail_nl"]})
    return out


def requests_item(ctx: AppContext, now: datetime | None = None) -> list[dict[str, Any]]:
    from mediaguard.attention import MAX_ROWS, _act

    stuck = stalled_requests(ctx, now)
    if not stuck:
        return []
    n = len(stuck)
    return [_item(
        "delivery.stalled", "warning",
        f"{n} aanvraag komt niet verder" if n == 1 else f"{n} aanvragen komen niet verder",
        "MediaGuard probeert het elke ronde opnieuw. Blijft dit zo, kijk dan bij de aanvraag "
        "wat er misgaat.", "/aanvragen", count=n,
        actions=[_act("Nu opnieuw proberen", "/delivery/run", primary=True)],
        rows=[{"id": s["id"], "title": s["title"], "href": "/aanvragen",
               "detail": f"Staat al {duration_nl(s['seconds'])} op '{s['stage_nl']}'"
                         + (f" · {s['detail_nl']}" if s.get("detail_nl") else ""),
               "actions": []} for s in stuck[:MAX_ROWS]],
        push=True, push_ids=[str(s["id"]) for s in stuck])]


def seerr_item(ctx: AppContext, now: datetime | None = None) -> list[dict[str, Any]]:
    from mediaguard.textfmt import time_nl

    row = taskwatch.failing(ctx, "seerr_sync")
    if row is None:
        return []
    since = _parse(row.get("failing_since"))
    now = now or datetime.now(UTC)
    # minstens twee mislukte pogingen: één misser vlak voor de volgende ronde is nog geen storing
    if since is None or now - since < timedelta(minutes=SEERR_FAIL_MINUTES) or \
            int(row.get("fails") or 0) < SEERR_MIN_FAILS:
        return []
    err = str(row.get("last_error") or "onbekende fout")
    return [_item(
        "seerr.sync_failed", "warning", "Seerr-aanvragen worden niet bijgewerkt",
        f"Ophalen uit Seerr lukt niet sinds {time_nl(row.get('failing_since'))}: {err}. "
        "Nieuwe aanvragen komen pas binnen als dit weer lukt; MediaGuard probeert het vanzelf "
        "opnieuw.", "/inrichten?tab=seerr", push=True)]


def tasks_item(ctx: AppContext, now: datetime | None = None) -> list[dict[str, Any]]:
    from mediaguard.attention import MAX_ROWS
    from mediaguard.textfmt import time_nl

    stale = [t for t in taskwatch.stale(ctx, now)
             if not (t["name"] in OWN_ALERT_TASKS and t.get("failing_since"))]
    if not stale:
        return []
    n = len(stale)
    rows = []
    for t in stale[:MAX_ROWS]:
        last = (f"laatst gelukt {time_nl(t['last_ok_at'])}" if t.get("last_ok_at")
                else "nog niet gelukt sinds de start")
        err = f" · laatste fout: {t['last_error']}" if t.get("last_error") else ""
        rows.append({"id": t["name"], "title": t["label_nl"], "detail": last + err,
                     "actions": []})
    return [_item(
        "tasks.stale", "warning",
        f"{n} achtergrondtaak doet al lang niets" if n == 1
        else f"{n} achtergrondtaken doen al lang niets",
        "MediaGuard start een taak die omvalt zelf opnieuw. Blijft een taak stilstaan, herstart "
        "dan MediaGuard (de container).", "/systeem#taken", count=n, rows=rows, push=True,
        push_ids=[str(t["name"]) for t in stale])]


def pool_item(ctx: AppContext) -> list[dict[str, Any]]:
    from mediaguard.storage_forecast import pool_degraded, pool_names_nl

    degraded = pool_degraded(ctx)
    if not degraded:
        return []
    return [_item(
        "storage.pool_degraded", "error",
        "TrueNAS-pool niet gezond: " + pool_names_nl(degraded),
        "MediaGuard ruimt geen originelen op tot de pool weer gezond is; je bestanden blijven "
        "staan. Controleer de schijven in TrueNAS.", "/systeem#opslag",
        push_ids=[str(p.get("name")) for p in degraded])]


def attention_items(ctx: AppContext) -> list[dict[str, Any]]:
    """Bronnen voor 'Wacht op jou' (lokaal, geen netwerk). Elk in een eigen try-blok."""
    out: list[dict[str, Any]] = []
    for source in (pool_item, emergency_stop_item, requests_item, seerr_item, tasks_item):
        try:
            out.extend(source(ctx))
        except Exception:  # noqa: BLE001 - één bron mag de rest nooit tegenhouden
            LOGGER.exception("bewakingsbron %s mislukt", source.__name__)
    return out


# -- pushen en vanzelf oplossen -----------------------------------------------------------------
def _alerts(items: list[dict[str, Any]], health: dict[str, Any] | None) -> list[dict[str, Any]]:
    out = []
    for i in items:
        if i.get("severity") != "error" and not i.get("push"):
            continue
        ids = i.get("push_ids") or [str(r.get("id")) for r in i.get("rows") or []] or ["*"]
        out.append({"key": i["key"], "severity": "error" if i["severity"] == "error"
                    else "warning", "title": i["title_nl"], "detail": i.get("detail_nl") or "",
                    "href": i.get("href") or "/activiteit?tab=problemen",
                    "rows": [r.get("title") for r in i.get("rows") or []][:5],
                    "ids": sorted({str(x) for x in ids}),
                    "after": SETTLE_MINUTES.get(i["key"], 0)})
    enc = (health or {}).get("encoder") or {}
    if enc.get("state") == "degraded" and enc.get("reasons"):
        out.append({"key": "encoder.degraded", "severity": "warning",
                    "title": "AV1-engine werkt beperkt",
                    "detail": "; ".join(str(r) for r in enc["reasons"][:3])
                              + ". MediaGuard probeert het zelf opnieuw.",
                    "href": "/av1", "rows": [], "ids": ["*"],
                    "after": ENCODER_DEGRADED_MINUTES})
    return out


def _notify(ctx: AppContext, alert: dict[str, Any]) -> None:
    from mediaguard.api.system import notify

    body = alert["detail"]
    if alert["rows"]:
        body = body + "\n" + "\n".join(f"• {r}" for r in alert["rows"])
    # categorie 'Fouten': standaard direct naar je telefoon (Instellingen → Meldingen)
    notify(ctx, alert["severity"], alert["title"], body, link=alert["href"],
           dedupe_key=f"monitor.{alert['key']}", category="errors")


def sync_alerts(ctx: AppContext, alerts: list[dict[str, Any]],
                now: datetime | None = None) -> dict[str, list[str]]:
    """Push nieuwe problemen één keer; los verdwenen problemen vanzelf op."""
    from mediaguard.api.system import resolve_notifications

    now = now or datetime.now(UTC)
    now_iso = now.isoformat(timespec="seconds")
    state = ctx.state_get(ALERTS_KEY, {}) or {}
    active: dict[str, Any] = dict(state.get("active") or {})
    resolved_at: dict[str, str] = dict(state.get("resolved") or {})
    pushed, resolved = [], []
    current = {a["key"] for a in alerts}
    for a in alerts:
        st = dict(active.get(a["key"]) or {"first_seen": now_iso, "ids": [],
                                           "notified_at": None})
        st["missing"] = 0
        first = _parse(st.get("first_seen")) or now
        due = now - first >= timedelta(minutes=int(a.get("after") or 0))
        new_ids = set(a["ids"]) - set(st.get("ids") or [])
        recent = _parse(resolved_at.get(a["key"]))
        # binnen het uur na 'opgelost' terug (flapperen): nog geen push. Blijft het staan, dan
        # volgt de push zodra dat uur om is; zo raakt een blijvend probleem nooit zoek.
        flapping = st.get("notified_at") is None and recent is not None and \
            now - recent < timedelta(minutes=REPUSH_COOLDOWN_MINUTES)
        if due and not flapping and (st.get("notified_at") is None or new_ids):
            try:
                _notify(ctx, a)
                pushed.append(a["key"])
            except Exception:  # noqa: BLE001 - de bewaking gaat altijd door
                LOGGER.exception("melding %s niet verstuurd", a["key"])
            st["notified_at"] = now_iso
            st["ids"] = sorted(set(st.get("ids") or []) | set(a["ids"]))[-500:]
        active[a["key"]] = st
    for key in list(active):
        if key in current:
            continue
        st = active[key]
        st["missing"] = int(st.get("missing") or 0) + 1
        if st["missing"] >= RESOLVE_AFTER_ROUNDS:
            resolve_notifications(ctx, f"monitor.{key}")
            if st.get("notified_at"):
                resolved_at[key] = now_iso
            active.pop(key)
            resolved.append(key)
    cutoff = now - timedelta(days=1)
    resolved_at = {k: v for k, v in resolved_at.items() if (_parse(v) or now) > cutoff}
    ctx.state_set(ALERTS_KEY, {"active": active, "resolved": resolved_at})
    return {"pushed": pushed, "resolved": resolved}


async def run_once(ctx: AppContext) -> dict[str, Any]:
    from mediaguard import attention, health_summary, storage_forecast

    # de pool altijd meten, ook met de bewaking uit: de opruimpauze bij een pool die niet
    # gezond is (verbeterlijst 37) beschermt originelen en hangt niet af van meldingen
    try:
        await storage_forecast.refresh_pool(ctx)
    except Exception:  # noqa: BLE001 - de pool is één van de bronnen
        LOGGER.exception("poolstatus meten mislukt")
    if not _value(ctx, "monitor.enabled", True):
        return {"skipped": "uit"}
    try:
        await asyncio.to_thread(update_request_progress, ctx)
    except Exception:  # noqa: BLE001
        LOGGER.exception("voortgang van aanvragen bijhouden mislukt")
    items = await attention.collect(ctx)
    health: dict[str, Any] | None = None
    try:
        health = await health_summary.build(ctx)
        ctx.services["health_summary_cache"] = (time.monotonic(), health)
    except Exception:  # noqa: BLE001 - gezondheidsmodel is aanvullend
        LOGGER.exception("gezondheidsmodel voor de bewaking mislukt")
    result = sync_alerts(ctx, _alerts(items, health))
    return {"items": len(items), "errors": sum(1 for i in items if i["severity"] == "error"),
            **result}


def _interval_seconds(ctx: AppContext) -> float:
    return 60 * max(1, int(_value(ctx, "monitor.interval_minutes", 5)))


async def _loop(ctx: AppContext) -> None:
    await asyncio.sleep(START_DELAY_SECONDS)
    while True:
        try:
            result = await run_once(ctx)
            ctx.state_set(LAST_KEY, {**result, "at": utcnow()})
            taskwatch.ok(ctx, "monitor")
        except Exception as exc:  # noqa: BLE001 - de bewaking mag nooit stoppen
            LOGGER.exception("bewakingsronde mislukt")
            taskwatch.fail(ctx, "monitor", exc)
        await asyncio.sleep(_interval_seconds(ctx))


async def startup(ctx: AppContext) -> None:
    if ctx.settings.start_background and ctx.settings.role in ("core", "all"):
        ctx.services["monitor_task"] = taskwatch.spawn(
            ctx, "monitor", _loop, label_nl="Bewaking op de achtergrond", every=_interval_seconds)


async def shutdown(ctx: AppContext) -> None:
    await taskwatch.stop(ctx, ctx.services.pop("monitor_task", None))


# -- API ---------------------------------------------------------------------------------------
@router.get("")
def monitor_status(_: Principal = Depends(require_viewer),
                   ctx: AppContext = Depends(get_app_context)) -> dict[str, Any]:
    """Laatste bewakingsronde, openstaande meldingen, achtergrondtaken en de TrueNAS-pool."""
    from mediaguard.storage_forecast import POOL_STATE

    state = ctx.state_get(ALERTS_KEY, {}) or {}
    return {"enabled": bool(_value(ctx, "monitor.enabled", True)),
            "last": ctx.state_get(LAST_KEY), "active": sorted(state.get("active") or {}),
            "tasks": taskwatch.status(ctx), "pool": ctx.state_get(POOL_STATE, {}) or {}}


@router.post("/run")
async def monitor_run(_: Principal = Depends(require_operator),
                      ctx: AppContext = Depends(get_app_context)) -> dict[str, Any]:
    result = await run_once(ctx)
    ctx.state_set(LAST_KEY, {**result, "at": utcnow()})
    return result
