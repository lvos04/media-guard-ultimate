"""'Nieuw in deze versie': meldingen bij een nieuwe standaard die bestaand gedrag verandert
(rapport 6, N8; rapport 38).

Een update kan een nieuwe instelling met een standaardwaarde introduceren die iets doet
wat de gebruiker eerder bewust anders koos of niet verwachtte (bv. opruimen tot 85 %,
CPU-encodes hooguit 12 uur, anime eerst via de GPU). Zo'n wijziging mag niet ongemerkt
gebeuren. Na de update krijgt de gebruiker:

- een melding (in de app en via het meldingenkanaal);
- een kaart 'Nieuw in deze versie' in 'Wacht op jou' (web en Android) tot hij op
  'Gelezen' tikt.

Elke melding heeft een vaste sleutel en wordt hooguit één keer verstuurd, en alleen aan wie
de vorige gedrag kende: een nieuwe installatie krijgt ze niet, en wie de instelling zelf al
anders zette ook niet.
"""

from __future__ import annotations

import re
from collections.abc import Callable
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any

from fastapi import APIRouter, Depends

from mediaguard import __version__
from mediaguard.auth import Principal, require_operator, require_viewer
from mediaguard.context import AppContext, get_app_context
from mediaguard.db import utcnow

router = APIRouter(prefix="/api/v1/notices", tags=["notices"])

STATE_KEY = "behaviour_notices_sent"
VERSION_KEY = "app.version"
HISTORY_KEY = "app.version_history"
WHATS_NEW_KEY = "app.whats_new"
FRESH_INSTALL_S = 3600
SHOW_DAYS = 30
UNDECIDED_GRACE_S = 24 * 3600  # AV1-engine onbereikbaar: na een dag toch melden
TUNING_KEY = "encoder_tuning_cache"
AV1_LINK = "/av1/instellingen#uitvoering"


@dataclass(frozen=True)
class Notice:
    key: str  # versie + onderwerp; nooit hergebruiken
    # True = melden, False = niet (nu en later), None = nog niet te bepalen (later opnieuw)
    applies: Callable[[AppContext], bool | None]
    title: str
    body: Callable[[AppContext], str]
    link: str
    # versie die de nieuwe standaard invoerde; alleen wie van daarvóór komt krijgt hem
    since: str | None = None


# -- versies ----------------------------------------------------------------------------------
def _vtuple(v: str | None) -> tuple[int, ...] | None:
    if not v:
        return None
    parts = re.findall(r"\d+", str(v).split("-", 1)[0])
    return tuple(int(p) for p in parts[:3]) if parts else None


def older(previous: str | None, since: str) -> bool:
    """Was ``previous`` een versie van vóór ``since``? Onbekend = ja (bestaande installatie
    van vóór de versiegeschiedenis)."""
    prev, ref = _vtuple(previous), _vtuple(since)
    return prev is None or ref is None or prev < ref


def _install_age_s(ctx: AppContext) -> float | None:
    row = ctx.db.fetch_one("SELECT MIN(applied_at) AS a FROM schema_migrations")
    try:
        first = datetime.fromisoformat(str(row["a"])) if row and row["a"] else None
    except ValueError:
        return None
    if first is None:
        return None
    if first.tzinfo is None:
        first = first.replace(tzinfo=UTC)
    return (datetime.now(UTC) - first).total_seconds()


def version_info(ctx: AppContext) -> dict[str, Any]:
    """Huidige en vorige versie (vastgelegd bij elke start van een nieuwe versie)."""
    info = ctx.state_get(VERSION_KEY)
    if isinstance(info, dict) and info.get("version") == __version__:
        return info
    if isinstance(info, dict) and info.get("version"):
        new = {"version": __version__, "previous": info["version"], "fresh_install": False,
               "first_seen": utcnow()}
    else:
        age = _install_age_s(ctx)
        fresh = age is not None and age < FRESH_INSTALL_S
        new = {"version": __version__, "previous": None, "fresh_install": fresh,
               "first_seen": utcnow()}
    ctx.state_set(VERSION_KEY, new)
    history = list(ctx.state_get(HISTORY_KEY, []) or [])
    history.append({"version": __version__, "at": new["first_seen"]})
    ctx.state_set(HISTORY_KEY, history[-30:])
    return new


def _upgraded_from_before(ctx: AppContext, since: str) -> bool:
    info = version_info(ctx)
    if info.get("fresh_install"):
        return False
    return older(info.get("previous"), since)


def _setting_default(ctx: AppContext, key: str) -> bool:
    try:
        return ctx.config.effective(key)["source"] == "default"
    except Exception:  # noqa: BLE001
        return False


def _knob(ctx: AppContext, key: str) -> dict[str, Any] | None:
    cache = ctx.services.get(TUNING_KEY)
    if not isinstance(cache, dict):
        return None
    return cache.get(key)


def _knob_applies(ctx: AppContext, key: str, since: str) -> bool | None:
    if not _upgraded_from_before(ctx, since):
        return False
    knob = _knob(ctx, key)
    if knob is None:
        first = version_info(ctx).get("first_seen")
        try:
            waited = (datetime.now(UTC) - datetime.fromisoformat(str(first))).total_seconds()
        except ValueError:
            waited = 0
        return True if waited > UNDECIDED_GRACE_S else None
    if knob.get("vastgezet_door_omgeving"):
        return False
    return knob.get("waarde") == knob.get("standaard")


async def refresh_tuning(ctx: AppContext) -> None:
    """Afstelling van de AV1-engine ophalen (voor de meldingen over CPU-uren en anime)."""
    from mediaguard.encoding.client import get_encoder_client

    try:
        data = await get_encoder_client(ctx).engine("GET", "/afstelling")
    except Exception:  # noqa: BLE001 - engine (nog) niet bereikbaar: later opnieuw
        return
    items = data.get("items") if isinstance(data, dict) else None
    if isinstance(items, list):
        ctx.services[TUNING_KEY] = {str(i.get("sleutel")): i for i in items
                                    if isinstance(i, dict)}


# -- de meldingen -----------------------------------------------------------------------------
def _safety_net_applies(ctx: AppContext) -> bool:
    from mediaguard.recovery.service import immediate_mode, safety_net

    try:
        default = ctx.config.effective("quarantine.safety_net_hours")["source"] == "default"
    except Exception:  # noqa: BLE001
        return False
    return immediate_mode(ctx) and safety_net(ctx)[0] > 0 and default


def _safety_net_body(ctx: AppContext) -> str:
    from mediaguard.recovery.service import safety_net

    hours, max_bytes = safety_net(ctx)
    return (f"Je hebt 'Direct verwijderen' gekozen. Sinds 0.3.8 blijft een vervangen origineel "
            f"toch nog {hours} uur bewaard (max. {max_bytes // 1024**3} GB), zodat je het kunt "
            "terugzetten als de AV1-versie tegenvalt. Wil je dat niet, zet dan 'Vangnet bij "
            "direct verwijderen' op 0 uur.")


def _purge_applies(ctx: AppContext) -> bool:
    return _upgraded_from_before(ctx, "1.1.0") and _setting_default(
        ctx, "quarantine.purge_target_percent")


def _purge_body(ctx: AppContext) -> str:
    pct = ctx.config.value("quarantine.purge_target_percent")
    return (f"Is het vangnet of de quarantaine vol, dan ruimt MediaGuard nu op tot {pct} % "
            "van het maximum, niet tot net eronder. Er gaan dan in één keer meer al "
            "gecontroleerde originelen weg, zodat de AV1-wachtrij niet bij elk groot bestand "
            "stilvalt. Liever zo weinig mogelijk weg? Zet 'Opruimen tot' hoger (max. 100 %).")


def _cpu_hours_body(ctx: AppContext) -> str:
    knob = _knob(ctx, "max_cpu_uren") or {}
    hours = knob.get("waarde") or 12
    hours_nl = f"{float(hours):g}".replace(".", ",")
    return (f"Na 10 minuten meet MediaGuard hoe snel een CPU-encode gaat. Duurt de rest langer "
            f"dan {hours_nl} uur, dan stopt hij ('te traag voor de CPU') en blijft het origineel "
            "gewoon staan. Zo blijft de CPU niet dagen bezet. Langere encodes toestaan? "
            "Verhoog 'Max. duur van één CPU-encode'.")


def _anime_body(_ctx: AppContext) -> str:
    return ("Anime begint nu op de GPU. Haalt de GPU de VMAF-doelwaarde in een reeks twee keer "
            "niet, dan gaat de rest van die reeks via de CPU. Liever altijd de CPU (trager, "
            "kwaliteit vrijwel altijd gehaald)? Kies bij 'Route voor anime' voor 'Altijd CPU'.")


NOTICES: tuple[Notice, ...] = (
    Notice("0.3.8:quarantine.safety_net", _safety_net_applies,
           "Nieuwe standaard: vangnet bij 'Direct verwijderen'", _safety_net_body,
           "/instellingen?q=vangnet"),
    Notice("1.1.0:quarantine.purge_target_percent", _purge_applies,
           "Nieuwe standaard: opruimen tot 85 %", _purge_body, "/instellingen?q=opruimen",
           since="1.1.0"),
    Notice("1.2.0:encoder.max_cpu_uren",
           lambda ctx: _knob_applies(ctx, "max_cpu_uren", "1.2.0"),
           "Nieuwe standaard: CPU-encode hooguit 12 uur", _cpu_hours_body, AV1_LINK,
           since="1.2.0"),
    Notice("1.2.0:encoder.anime_route",
           lambda ctx: _knob_applies(ctx, "anime_route", "1.2.0"),
           "Nieuwe standaard: anime eerst via de GPU", _anime_body, AV1_LINK, since="1.2.0"),
)


def run(ctx: AppContext) -> list[str]:
    """Verstuur wat nog niet verstuurd is en van toepassing is. Geeft de nieuwe sleutels."""
    from mediaguard.api.system import notify

    version_info(ctx)
    sent = dict(ctx.state_get(STATE_KEY, {}) or {})
    fresh: list[tuple[Notice, str]] = []
    changed = False
    for n in NOTICES:
        if n.key in sent:
            continue
        try:
            verdict = n.applies(ctx)
            if verdict is None:
                continue  # later opnieuw (bv. AV1-engine nog niet bereikbaar)
            if not verdict:
                if n.since:  # niet van toepassing (nieuwe installatie of zelf ingesteld)
                    sent[n.key] = "n.v.t."
                    changed = True
                continue
            fresh.append((n, n.body(ctx)))
        except Exception:  # noqa: BLE001 - een melding mag het opstarten nooit blokkeren
            continue
    if len(fresh) == 1:
        n, body = fresh[0]
        notify(ctx, "info", n.title, body, link=n.link, dedupe_key=f"notice:{n.key}")
    elif fresh:
        body = " ".join(f"• {n.title.removeprefix('Nieuwe standaard: ')}." for n, _ in fresh)
        notify(ctx, "info", f"Nieuw in deze versie: {len(fresh)} gewijzigde standaarden",
               body + " Uitleg en de instelling staan in 'Wacht op jou'.",
               link="/", dedupe_key=f"notice:{__version__}")
    for n, body in fresh:
        sent[n.key] = utcnow()
        _remember(ctx, n, body)
    if fresh or changed:
        ctx.state_set(STATE_KEY, sent)
    return [n.key for n, _ in fresh]


def _remember(ctx: AppContext, n: Notice, body: str) -> None:
    current = ctx.state_get(WHATS_NEW_KEY) or {}
    if not isinstance(current, dict) or current.get("version") != __version__ or \
            current.get("dismissed"):
        current = {"version": __version__, "items": [], "dismissed": False}
    items = [i for i in current.get("items") or [] if i.get("key") != n.key]
    items.append({"key": n.key, "title": n.title, "body": body, "link": n.link,
                  "at": utcnow()})
    current["items"] = items
    ctx.state_set(WHATS_NEW_KEY, current)


async def tick(ctx: AppContext) -> list[str]:
    """Vanuit de onderhoudslus: afstelling ophalen en openstaande meldingen afmaken."""
    if any(n.key not in (ctx.state_get(STATE_KEY, {}) or {}) for n in NOTICES):
        await refresh_tuning(ctx)
    return run(ctx)


def whats_new(ctx: AppContext) -> dict[str, Any]:
    current = ctx.state_get(WHATS_NEW_KEY) or {}
    if not isinstance(current, dict) or not current.get("items"):
        return {"version": __version__, "items": [], "show": False}
    newest = max(str(i.get("at") or "") for i in current["items"])
    try:
        age_days = (datetime.now(UTC) - datetime.fromisoformat(newest)).days
    except ValueError:
        age_days = 0
    show = not current.get("dismissed") and age_days <= SHOW_DAYS
    return {**current, "show": show}


def attention_items(ctx: AppContext) -> list[dict[str, Any]]:
    data = whats_new(ctx)
    if not data["show"]:
        return []
    items = data["items"]
    return [{
        "key": "notices.whats_new", "severity": "info",
        "title_nl": f"Nieuw in deze versie ({data.get('version')})",
        "detail_nl": f"{len(items)} standaard{'en' if len(items) != 1 else ''} "
                     f"veranderd{'en' if len(items) != 1 else ''} het gedrag van MediaGuard. "
                     "Lees wat er anders is en pas de instelling aan als je het anders wilt.",
        "href": items[0]["link"], "count": len(items),
        "actions": [{"label_nl": "Gelezen", "method": "post", "path": "/notices/gezien",
                     "body": {}, "confirm_nl": None, "role": "operator", "primary": True}],
        "rows": [{"id": i["key"], "title": i["title"], "detail": i["body"],
                  "href": i["link"], "actions": []} for i in items],
    }]


@router.get("")
def notices_view(_: Principal = Depends(require_viewer),
                 ctx: AppContext = Depends(get_app_context)) -> dict[str, Any]:
    """'Nieuw in deze versie' voor web en app."""
    return {**whats_new(ctx), "version_info": version_info(ctx)}


@router.post("/gezien")
def notices_seen(_: Principal = Depends(require_operator),
                 ctx: AppContext = Depends(get_app_context)) -> dict[str, Any]:
    current = ctx.state_get(WHATS_NEW_KEY) or {}
    if isinstance(current, dict) and current.get("items"):
        current["dismissed"] = True
        current["dismissed_at"] = utcnow()
        ctx.state_set(WHATS_NEW_KEY, current)
    return {"show": False}


async def startup(ctx: AppContext) -> None:
    if ctx.settings.role in ("core", "all"):
        version_info(ctx)
        run(ctx)
