"""De leveringsmotor: per aanvraag de stand bepalen en zelf de volgende stap zetten.

Stappen: aangevraagd → in Sonarr/Radarr (juiste instantie, profiel, gemonitord) → gezocht →
gedownload → geïmporteerd → gecontroleerd → geleverd. Elke stap die vastloopt wordt
automatisch hersteld, met terugvaltijden en grenzen. Niets vraagt de gebruiker om een klik;
waar een keuze nodig is beslist de AI tussen opties die al aan de eisen voldoen (anders een
vaste regel). De eisen zelf (profiel, talen, DV/HDR, ondertitels) worden nooit versoepeld.

Wat de motor bewust níet doet (verbeterlijst 5, 7, 11, 15):
- een import die vastzit weggooien: dat doet alleen de vastloper-controle
  (``wanted/stuck.py``: vanaf het eerste moment gezien, instelbare wachttijd, tijdelijke
  weigeringen en proefmodus tellen niet);
- een kwaliteitsprofiel overschrijven dat de eigenaar zelf koos: het MediaGuard-profiel komt
  alleen op titels die ná de overname via Seerr zijn aangevraagd, en maar één keer;
- een titel die uit Sonarr/Radarr verdween opnieuw laten aanvragen;
- iets doen tijdens een noodstop of op een instantie in proefmodus (daar alleen kijken).
"""

from __future__ import annotations

import logging
from datetime import UTC, datetime, timedelta
from typing import TYPE_CHECKING, Any

from mediaguard import emergency
from mediaguard.arr.client import ArrError, ArrNotFound
from mediaguard.audit import audit
from mediaguard.db import dumps, loads, utcnow
from mediaguard.delivery import ai as decider
from mediaguard.delivery import store

if TYPE_CHECKING:
    from mediaguard.context import AppContext

LOGGER = logging.getLogger(__name__)
ACTOR = "svc:delivery"
# terugvaltijden tussen zoekpogingen (uren); daarna wekelijks
BACKOFF_HOURS = [0, 1, 3, 6, 12, 24, 48, 96, 168]
NOT_FOUND_DAYS = 30
RECENT_ARR_DAYS = 14
MAX_ITEMS_PER_RUN = 40
MAX_EPISODES_PER_SEARCH = 50
# zo lang wacht een leveringsronde op een handmatige import (daarna: volgende ronde)
IMPORT_WAIT_SECONDS = 15.0
SERIES_INSTANCES = ("sonarr-series", "sonarr-anime")
MOVIE_INSTANCES = ("radarr-films", "radarr-animefilms")
FAILED_STATES = {"failed", "failedpending"}
# 'importfailed' is de oude naam (Sonarr v3) van importBlocked
IMPORT_STATES = {"importpending", "importblocked", "importfailed", "importing"}
BLOCKED_STATES = {"importblocked", "importfailed"}
# Seerr: hooguit zoveel keer goedkeuren/opnieuw laten versturen, met oplopende wachttijd (uur)
SEERR_RETRY_MAX = 3
SEERR_RETRY_WAIT_HOURS = [0.25, 1, 3]
# na goedkeuring zo lang elk kwartier kijken of Seerr hem verstuurd heeft, daarna elke 2 uur
SEERR_SEND_WAIT_HOURS = 2
# 'geleverd' naar Seerr: zo vaak elk uur opnieuw, daarna dagelijks
SEERR_SYNC_HOURLY = 24
# Sonarr/Radarr zet 'toegevoegd' bij het toevoegen; klokverschil met Seerr opvangen
ADDED_SLACK = timedelta(minutes=10)
SEERR_DECLINED, SEERR_FAILED, SEERR_PENDING = 3, 4, 1
MEDIA_AVAILABLE = {4, 5}
MEDIA_DELETED = {6, 7}


def _now() -> datetime:
    return datetime.now(UTC)


def _iso(dt: datetime) -> str:
    return dt.isoformat(timespec="seconds")


def _parse(value: Any) -> datetime | None:
    if not value:
        return None
    try:
        dt = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    except ValueError:
        return None
    return dt if dt.tzinfo else dt.replace(tzinfo=UTC)


def _later(hours: float) -> str:
    return _iso(_now() + timedelta(hours=hours))


MONTHS_NL = ("jan", "feb", "mrt", "apr", "mei", "jun", "jul", "aug", "sep", "okt", "nov", "dec")


def when_nl(dt: datetime | None) -> str:
    if dt is None:
        return "binnenkort"
    local = dt.astimezone()
    return f"{local.day} {MONTHS_NL[local.month - 1]} {local:%H:%M}"


# ── ontdekken ────────────────────────────────────────────────────────────────
def _migrated_keys(ctx: AppContext) -> set[tuple[str, str, str]]:
    """(instantie, soort, ID) van titels die de migratie-import heeft aangemaakt."""
    try:
        rows = ctx.db.fetch_all(
            "SELECT instance_id, kind, target_ref FROM instance_migration_checkpoints WHERE "
            "phase='import' AND kind IN ('series','movie') AND target_ref IS NOT NULL")
    except Exception:  # noqa: BLE001 - oude database zonder migratietabellen
        return set()
    return {(str(r["instance_id"]), str(r["kind"]), str(r["target_ref"])) for r in rows}


def _shadow_instances(ctx: AppContext) -> set[str]:
    from mediaguard.instances.registry import list_instances

    return {i.id for i in list_instances(ctx) if i.shadow_mode}


async def discover(ctx: AppContext) -> int:
    """Seerr-aanvragen en recent in Sonarr/Radarr toegevoegde titels gaan in de tracker.
    Gemigreerde titels en titels op een proefinstantie zijn geen nieuwe aanvragen."""
    added = 0
    try:
        from mediaguard.seerr.service import sync_requests

        await sync_requests(ctx)
    except Exception as exc:  # noqa: BLE001 - Seerr is optioneel; lokale gegevens blijven
        LOGGER.debug("Seerr-aanvragen niet bijgewerkt: %s", exc)
    for r in ctx.db.fetch_dicts(
            "SELECT * FROM seerr_requests WHERE COALESCE(request_status,0) != 3 AND "
            "deleted_at IS NULL"):
        try:
            media_type = "series" if r["media_type"] == "tv" else "movie"
            seasons = [int(s["season"]) for s in loads(r["seasons_json"], []) or []
                       if s.get("season") is not None]
            if store.upsert(ctx, media_type=media_type, tmdb_id=r["tmdb_id"],
                            tvdb_id=r["tvdb_id"], title=r["title"], source="seerr",
                            seerr_request_id=int(r["seerr_request_id"]),
                            instance_id=r["instance_id"], provider_id=r["provider_id"],
                            seasons=seasons, requested_at=r["created_at"]) is not None:
                added += 1
        except Exception:  # noqa: BLE001 - één aanvraag mag de rest niet tegenhouden
            LOGGER.exception("Seerr-aanvraag %s niet vastgelegd", r.get("seerr_request_id"))
    try:
        _stop_withdrawn(ctx)
    except Exception:  # noqa: BLE001
        LOGGER.exception("verwijderde Seerr-aanvragen niet bijgewerkt")
    migrated = _migrated_keys(ctx)
    shadow = _shadow_instances(ctx)
    try:
        _stop_migrated(ctx, migrated)
    except Exception:  # noqa: BLE001
        LOGGER.exception("gemigreerde titels niet bijgewerkt")
    since = _iso(_now() - timedelta(days=RECENT_ARR_DAYS))
    for r in ctx.db.fetch_dicts(
            "SELECT * FROM lib_items WHERE removed_at IS NULL AND added_at >= ? AND "
            "monitored=1", (since,)):
        if r["instance_id"] in shadow or (
                str(r["instance_id"]), str(r["kind"]), str(r["provider_id"])) in migrated:
            continue  # 'toegevoegd' is hier de importdag, geen aanvraag (verbeterlijst 7)
        try:
            store.upsert(ctx, media_type=r["kind"], tmdb_id=r["tmdb_id"], tvdb_id=r["tvdb_id"],
                         title=r["title"], source="arr", instance_id=r["instance_id"],
                         provider_id=r["provider_id"], requested_at=r["added_at"])
        except Exception:  # noqa: BLE001
            LOGGER.exception("titel %s niet vastgelegd", r.get("title"))
    return added


def _stop_withdrawn(ctx: AppContext) -> int:
    """Aanvragen die in Seerr verwijderd of afgewezen zijn, blijven niet 'actief'. Een al
    geleverde titel blijft 'geleverd' (aanvragen opruimen in Seerr is gewoon onderhoud).
    Staat een aanvraag van een zo gestopte titel later weer in Seerr (bv. weer goedgekeurd,
    of eerder ten onrechte als verwijderd gezien), dan volgt MediaGuard hem weer."""
    rows = ctx.db.fetch_dicts(
        "SELECT id, seerr_request_ids_json, stopped_at FROM delivery_items WHERE "
        "source='seerr' AND state!='delivered' AND (stopped_at IS NULL OR "
        "stop_code='seerr_deleted')")
    if not rows:
        return 0
    known = {int(r["seerr_request_id"]): r for r in ctx.db.fetch_dicts(
        "SELECT seerr_request_id, request_status, deleted_at FROM seerr_requests")}
    stopped = 0
    for row in rows:
        ids = [int(x) for x in loads(row["seerr_request_ids_json"], []) or []]
        reqs = [known.get(i) for i in ids]
        if row["stopped_at"]:
            if any(r and not r["deleted_at"] and r["request_status"] != SEERR_DECLINED
                   for r in reqs):
                store.resume(ctx, int(row["id"]), ACTOR,
                             "Aanvraag staat weer in Seerr; MediaGuard volgt hem weer")
            continue
        if not ids or any(r is None for r in reqs):
            continue  # nooit gezien in Seerr: daar niets over beslissen
        if all(r["deleted_at"] or r["request_status"] == SEERR_DECLINED for r in reqs if r):
            store.stop(ctx, int(row["id"]), "seerr_deleted", ACTOR)
            stopped += 1
    return stopped


def _stop_migrated(ctx: AppContext, migrated: set[tuple[str, str, str]]) -> int:
    """Titels die vóór deze versie door de migratie-import als 'aanvraag' in de tracker
    kwamen (toegevoegd = importdag) zijn geen aanvraag: niet meer volgen (verbeterlijst 7).
    Alleen zonder Seerr-aanvraag; een echte aanvraag blijft gewoon gevolgd."""
    if not migrated:
        return 0
    stopped = 0
    for r in ctx.db.fetch_dicts(
            "SELECT id, instance_id, media_type, provider_id FROM delivery_items WHERE "
            "source='arr' AND stopped_at IS NULL AND provider_id IS NOT NULL AND "
            "COALESCE(seerr_request_ids_json,'[]') IN ('[]','')"):
        if (str(r["instance_id"]), str(r["media_type"]), str(r["provider_id"])) in migrated:
            store.stop(ctx, int(r["id"]), "migrated", ACTOR)
            stopped += 1
    return stopped


# ── hulpfuncties per stap ─────────────────────────────────────────────────────
async def _locate(ctx: AppContext, item: dict[str, Any],
                  exclude: tuple[str, int] | None = None, *,
                  strict: bool = False) -> tuple[str, int] | None:
    """Waar staat de titel nu? Eerst de bibliotheekcache, dan Sonarr/Radarr zelf.
    ``exclude``: een plek die net 'niet gevonden' gaf (cache kan achterlopen).
    ``strict``: was een instantie onbereikbaar, dan is 'nergens' niet zeker → ArrError
    (zo stopt MediaGuard nooit een titel omdat Sonarr/Radarr even plat lag)."""
    from mediaguard.instances.registry import arr_client, get_instance

    media_type = item["media_type"]
    col, val = ("tvdb_id", item["tvdb_id"]) if media_type == "series" else \
        ("tmdb_id", item["tmdb_id"])
    rows = ctx.db.fetch_dicts(
        f"SELECT instance_id, provider_id FROM lib_items WHERE kind=? AND {col}=? AND "  # noqa: S608
        "removed_at IS NULL", (media_type, val)) if val else []
    for r in rows:
        hit = (str(r["instance_id"]), int(r["provider_id"]))
        if hit != exclude:
            return hit
    unreachable: list[str] = []
    for iid in SERIES_INSTANCES if media_type == "series" else MOVIE_INSTANCES:
        inst = get_instance(ctx, iid)
        if inst is None or inst.archived or inst.suspended:
            continue
        try:
            async with arr_client(ctx, iid) as client:
                listing = await (client.series_list() if media_type == "series"
                                 else client.movie_list())
        except ArrError:
            unreachable.append(iid)
            continue
        key = "tvdbId" if media_type == "series" else "tmdbId"
        hit = next((x for x in listing if val and x.get(key) == int(val)), None)
        if hit and (iid, int(hit["id"])) != exclude:
            return iid, int(hit["id"])
    if strict and unreachable:
        raise ArrError(f"{', '.join(unreachable)} niet bereikbaar; MediaGuard kan niet "
                       "nagaan of de titel nog bestaat")
    return None


def _set_location(ctx: AppContext, item: dict[str, Any], found: tuple[str, int]) -> None:
    item["instance_id"], item["provider_id"] = found
    first = not item.get("located_at")
    item["located_at"] = item.get("located_at") or utcnow()
    store.update(ctx, int(item["id"]), instance_id=found[0], provider_id=found[1],
                 located_at=item["located_at"])
    if first:
        store.event(ctx, int(item["id"]), "adding", "gevonden", ACTOR, f"Staat in {found[0]}")


async def _seerr(ctx: AppContext, method: str, path: str) -> bool:
    try:
        from mediaguard.seerr.client import seerr_client

        await seerr_client(ctx).request(method, path)
        return True
    except Exception as exc:  # noqa: BLE001 - Seerr is optioneel
        LOGGER.debug("Seerr %s %s mislukt: %s", method, path, exc)
        return False


def _seerr_rows(ctx: AppContext, item: dict[str, Any]) -> list[dict[str, Any]]:
    ids = [int(x) for x in loads(item.get("seerr_request_ids_json"), []) or []]
    if not ids:
        return []
    return ctx.db.fetch_dicts(
        f"SELECT * FROM seerr_requests WHERE seerr_request_id IN "  # noqa: S608
        f"({','.join('?' * len(ids))})", ids)


async def _not_in_arr(ctx: AppContext, item: dict[str, Any], *, manual: bool = False) -> str:
    """Staat (nog) niet in Sonarr/Radarr. Nooit een titel opnieuw laten toevoegen die er al
    eens stond: dan heeft iemand hem bewust verwijderd (verbeterlijst 11). Seerr alleen
    goedkeuren of opnieuw laten versturen bij een echte mislukking, met een maximum.
    ``manual`` (knop 'Nu opnieuw'): ook een goedgekeurde aanvraag één keer opnieuw laten
    versturen, want dan vraag je er zelf om."""
    item_id = int(item["id"])
    live = [r for r in _seerr_rows(ctx, item)
            if not r["deleted_at"] and r["request_status"] != SEERR_DECLINED]
    elsewhere = bool(live) and all(r["media_status"] in MEDIA_AVAILABLE for r in live)
    if item.get("located_at") or elsewhere:
        # vóór het stoppen zeker weten dat hij nergens staat (niet omdat Sonarr/Radarr even
        # onbereikbaar was: dan ArrError en later opnieuw)
        found = await _locate(ctx, item, strict=True)
        if found is not None:
            _set_location(ctx, item, found)
            store.update(ctx, item_id, next_action_at=utcnow())
            return "adding"
    if item.get("located_at"):
        store.stop(ctx, item_id, "removed", ACTOR)
        return "stopped"
    if any(r["media_status"] in MEDIA_DELETED for r in live):
        store.stop(ctx, item_id, "removed", ACTOR,
                   "Titel is volgens Seerr verwijderd; MediaGuard vraagt hem niet opnieuw aan")
        return "stopped"
    if elsewhere:
        store.stop(ctx, item_id, "elsewhere", ACTOR,
                   "Staat volgens Seerr al in een bibliotheek die MediaGuard niet beheert; "
                   "MediaGuard volgt hem niet")
        return "stopped"
    retries = int(item.get("seerr_retries") or 0)
    pending = [r for r in live if r["request_status"] == SEERR_PENDING]
    failed = [r for r in live if r["request_status"] == SEERR_FAILED or (
        manual and r["request_status"] not in (SEERR_PENDING, SEERR_DECLINED))]
    detail, nxt, wait = "Staat nog niet in Sonarr/Radarr", "Opnieuw controleren over 15 " \
        "minuten", 0.25
    if (pending or failed) and retries < SEERR_RETRY_MAX:
        did = False
        for r in pending:
            if await _seerr(ctx, "POST", f"request/{int(r['seerr_request_id'])}/approve"):
                store.event(ctx, item_id, "adding", "goedgekeurd", ACTOR,
                            "Aanvraag in Seerr goedgekeurd zodat hij naar Sonarr/Radarr gaat")
                did = True
        for r in failed:
            if await _seerr(ctx, "POST", f"request/{int(r['seerr_request_id'])}/retry"):
                store.event(ctx, item_id, "adding", "opnieuw_verstuurd", ACTOR,
                            "Op jouw verzoek via Seerr opnieuw naar Sonarr/Radarr gestuurd"
                            if manual and r["request_status"] != SEERR_FAILED else
                            f"Seerr kon hem niet versturen; opnieuw geprobeerd "
                            f"({retries + 1} van {SEERR_RETRY_MAX})")
                did = True
        retries += 1
        wait = SEERR_RETRY_WAIT_HOURS[min(retries, len(SEERR_RETRY_WAIT_HOURS)) - 1]
        if did:
            detail += "; MediaGuard heeft Seerr opnieuw laten versturen" if failed else \
                "; aanvraag in Seerr goedgekeurd"
        nxt = f"Opnieuw controleren {when_nl(_now() + timedelta(hours=wait))}"
    elif pending or failed:
        what = ("Seerr kon hem niet naar Sonarr/Radarr sturen" if failed
                else "Goedkeuren in Seerr lukte niet")
        detail = f"{what} ({SEERR_RETRY_MAX}× geprobeerd)"
        nxt, wait = "Kies 'Nu opnieuw' als het probleem in Seerr is opgelost", 6.0
        _notify_once(ctx, item, f"'{item.get('title') or 'Aanvraag'}' komt niet in "
                     "Sonarr/Radarr", f"{what} ({SEERR_RETRY_MAX}× geprobeerd). MediaGuard "
                     "probeert het niet meer zelf; kijk in Seerr en kies daarna 'Nu opnieuw' "
                     "bij Aanvragen.")
    else:
        # goedgekeurd: Seerr verstuurt hem zelf; niets opnieuw laten versturen
        approved = max((_parse(r["updated_at"]) or _parse(r["created_at"]) or _now()
                        for r in live), default=_now())
        if _now() - approved > timedelta(hours=SEERR_SEND_WAIT_HOURS):
            detail = "Staat nog niet in Sonarr/Radarr; Seerr heeft hem nog niet verstuurd"
            nxt, wait = "Opnieuw controleren over 2 uur", 2.0
        else:
            detail = "Wacht tot Seerr hem naar Sonarr/Radarr stuurt"
    store.update(ctx, item_id, stage="adding", state="active", last_action_at=utcnow(),
                 detail_nl=detail, next_step_nl=nxt, next_action_at=_later(wait),
                 seerr_retries=retries)
    return "adding"


def _desired_profile(ctx: AppContext, instance_id: str,
                     profiles: list[dict[str, Any]]) -> dict[str, Any] | None:
    from mediaguard.instances.registry import get_instance
    from mediaguard.provisioning import templates as T

    inst = get_instance(ctx, instance_id)
    if inst is None:
        return None
    names = T.PROFILE_TEMPLATES_FOR.get(inst.media_type) or []
    if not names:
        return None
    wanted = T.PREFIX + T.QUALITY_TEMPLATES[names[0]]["name"]
    return next((p for p in profiles if p.get("name") == wanted), None)


def _managed_since(ctx: AppContext, instance_id: str) -> datetime | None:
    """Sinds wanneer MediaGuard deze instantie beheert: aangemaakt, Seerr omgezet of de
    migratie afgerond (de laatste telt)."""
    times: list[datetime | None] = []
    queries = (
        ("SELECT created_at AS t FROM instances WHERE id=?", True),
        ("SELECT seerr_applied_at AS t FROM instance_cutover_state WHERE instance_id=?", False),
        ("SELECT MAX(finished_at) AS t FROM migration_cutovers WHERE target_instance_id=? "
         "AND status='completed'", False),
    )
    for sql, required in queries:
        try:
            row = ctx.db.fetch_one(sql, (instance_id,))
        except Exception:  # noqa: BLE001 - tabel ontbreekt in een oude database
            if required:
                raise
            continue
        times.append(_parse(row["t"]) if row else None)
    found = [t for t in times if t is not None]
    return max(found) if found else None


def _migrated(ctx: AppContext, item: dict[str, Any]) -> bool:
    return (str(item["instance_id"]), str(item["media_type"]),
            str(item["provider_id"])) in _migrated_keys(ctx)


def profile_verdict(ctx: AppContext, item: dict[str, Any], record: dict[str, Any],
                    desired: dict[str, Any]) -> str | None:
    """None = het MediaGuard-profiel mag gezet worden; anders waarom niet (verbeterlijst 7).

    Alleen voor een titel die ná de overname via Seerr is aangevraagd en die Seerr zelf in
    Sonarr/Radarr zette. Nooit voor gemigreerde/geïmporteerde titels, een 4K-aanvraag of een
    profiel dat in Seerr bewust gekozen is."""
    reqs = [r for r in _seerr_rows(ctx, item) if not r["deleted_at"]]
    if not reqs:
        return "niet via Seerr aangevraagd"
    if any(r["is4k"] for r in reqs):
        return "4K-aanvraag; het gekozen profiel blijft"
    if any(r["profile_id"] is not None and int(r["profile_id"]) != int(desired["id"])
           for r in reqs):
        return "profiel is in Seerr bewust gekozen"
    if _migrated(ctx, item):
        return "gemigreerde titel; het profiel blijft zoals het was"
    first = min((t for t in (_parse(r["created_at"]) for r in reqs) if t), default=None)
    if first is None:
        return "aanvraagdatum onbekend"
    since = _managed_since(ctx, str(item["instance_id"]))
    if since is not None and first < since:
        return "aangevraagd vóór MediaGuard deze instantie beheerde"
    added = _parse(record.get("added"))
    if added is not None and added < first - ADDED_SLACK:
        return "stond al in Sonarr/Radarr vóór de aanvraag"
    return None


async def _ensure_requirements(ctx: AppContext, item: dict[str, Any], client: Any,
                               record: dict[str, Any]) -> dict[str, Any]:
    """Eén keer per aanvraag: MediaGuard-profiel (alleen waar dat mag) en bewaken. Daarna
    nooit meer: wat de eigenaar later zelf verandert (bv. 4K) blijft staan."""
    from mediaguard.provisioning import templates as T

    item_id = int(item["id"])
    series = item["media_type"] == "series"
    changed = False
    if not item.get("profile_checked_at"):
        profiles = await client.quality_profiles()
        current = next((p for p in profiles if p.get("id") == record.get("qualityProfileId")),
                       None)
        desired = _desired_profile(ctx, item["instance_id"], profiles)
        name = str((current or {}).get("name", "?"))
        if desired and not name.startswith(T.PREFIX):
            why = profile_verdict(ctx, item, record, desired)
            if why is None:
                record["qualityProfileId"] = int(desired["id"])
                changed = True
                store.event(ctx, item_id, "adding", "profiel", ACTOR,
                            f"Kwaliteitsprofiel '{name}' vervangen door '{desired['name']}' "
                            "(de eisen van MediaGuard)")
            else:
                store.event(ctx, item_id, "adding", "profiel_gelaten", ACTOR,
                            f"Kwaliteitsprofiel '{name}' blijft staan: {why}")
        item["profile_checked_at"] = utcnow()
        store.update(ctx, item_id, profile_checked_at=item["profile_checked_at"])
    if item.get("requirements_checked_at"):
        if changed:
            record = await (client.series_update(record) if series
                            else client.movie_update(record)) or record
        return record
    if not record.get("monitored") and item.get("source") == "seerr":
        record["monitored"] = True
        changed = True
        store.event(ctx, item_id, "adding", "gemonitord", ACTOR,
                    "Titel stond niet op 'bewaken'; aangezet zodat hij gedownload wordt")
    if changed:
        record = await (client.series_update(record) if series
                        else client.movie_update(record)) or record
    complete = True
    if series:
        wanted = set(loads(item["requested_seasons_json"], []) or [])
        known = {int(s["seasonNumber"]) for s in record.get("seasons", [])}
        complete = wanted <= known  # net toegevoegd: seizoenen nog niet bekend → later
        off = {int(s["seasonNumber"]): True for s in record.get("seasons", [])
               if int(s["seasonNumber"]) in wanted and not s.get("monitored")}
        if off:
            await client.season_pass(int(record["id"]), off)
            store.event(ctx, item_id, "adding", "seizoenen", ACTOR,
                        "Aangevraagde seizoen(en) " + ", ".join(map(str, sorted(off))) +
                        " op 'bewaken' gezet")
            record = await client.series_get(int(record["id"]))
    if complete:
        item["requirements_checked_at"] = utcnow()
        store.update(ctx, item_id, requirements_checked_at=item["requirements_checked_at"])
    return record


async def _maybe_route_anime(ctx: AppContext, item: dict[str, Any], app: str,
                             record: dict[str, Any]) -> tuple[str, int] | None:
    """Anime in de gewone instantie: naar de anime-instantie (zelfde regels als routering)."""
    from mediaguard.routing import service as routing

    source = str(item["instance_id"])
    if source not in routing.DEFAULT_TARGETS or not ctx.config.value("routing.anime.enabled"):
        return None
    if not routing.detect_anime(record, app, japanese_animation=bool(ctx.config.value(
            "routing.anime.detect_animation_japanese")))["is_anime"]:
        return None
    kind = "series" if app == "sonarr" else "movie"
    result = await routing.evaluate(ctx, source, kind, int(record["id"]), actor=ACTOR,
                                    force=True, quiet=True)
    if result.get("status") == "moved":
        store.event(ctx, int(item["id"]), "adding", "anime", ACTOR,
                    f"Anime: verplaatst naar {result['target']} (Dual Audio/anime-profiel)")
        return str(result["target"]), int(result["target_id"])
    return None


def _queue_for(queue: list[dict[str, Any]], media_type: str, pid: int) -> list[dict[str, Any]]:
    key = "seriesId" if media_type == "series" else "movieId"
    return [q for q in queue if int(q.get(key) or -1) == pid]


def _queue_state(q: dict[str, Any]) -> str:
    """Een download die al op import staat is nooit 'mislukt', ook niet met een
    foutstatus: dat beoordeelt alleen de vastloper-controle (verbeterlijst 5)."""
    state = str(q.get("trackedDownloadState") or "").lower()
    status = str(q.get("status") or "").lower()
    if state in IMPORT_STATES:
        return "importing"
    if state in FAILED_STATES or status == "failed" or \
            str(q.get("trackedDownloadStatus") or "").lower() == "error":
        return "failed"
    if status == "completed":
        return "importing"
    return "downloading"


def _queue_reason(q: dict[str, Any]) -> str:
    msgs = [m for s in q.get("statusMessages") or [] for m in s.get("messages") or []]
    return "; ".join(msgs)[:240] or str(q.get("errorMessage") or "")[:240]


async def _handle_queue(ctx: AppContext, item: dict[str, Any], client: Any,
                        entries: list[dict[str, Any]], missing_ids: list[int], *,
                        observe: bool = False) -> tuple[str | None, str | None]:
    """Downloads van deze titel. Mislukt → blokkeren + opnieuw zoeken. Wacht op import →
    alleen een eenduidige ID-match zelf bevestigen; weggooien doet de vastloper-controle.
    Geeft (stap, toelichting)."""
    item_id = int(item["id"])
    stage: str | None = None
    note: str | None = None
    for q in entries:
        state = _queue_state(q)
        reason = _queue_reason(q)
        if state == "failed":
            if observe or emergency.active(ctx):
                stage = stage or "downloading"
                note = note or f"Download mislukt ({reason or 'onbekende reden'})"
                continue
            try:
                await client.queue_remove(int(q["id"]), remove_from_client=True, blocklist=True)
            except ArrNotFound:
                continue  # al opgeruimd door de downloadbewaking
            store.event(ctx, item_id, "downloading", "download_mislukt", ACTOR,
                        f"Download mislukt ({reason or 'onbekende reden'}); release geblokkeerd "
                        "en er wordt een andere gezocht", {"release": q.get("title")})
            await _search(ctx, item, client, missing_ids, force=True)
            stage = stage or "searching"
            continue
        if state == "importing":
            stage = "importing"
            blocked = str(q.get("trackedDownloadState") or "").lower() in BLOCKED_STATES
            if (blocked and not observe and not emergency.active(ctx)
                    and await _try_import(ctx, item, q)):
                store.event(ctx, item_id, "importing", "geimporteerd", ACTOR,
                            "Vastzittende import zelf bevestigd (eenduidige match)")
                continue
            if blocked and reason:
                note = f"Import wacht: {reason}"
            continue
        stage = stage or "downloading"
    return stage, note


async def _try_import(ctx: AppContext, item: dict[str, Any], q: dict[str, Any]) -> bool:
    if not q.get("downloadId"):
        return False
    try:
        from mediaguard.activity.manual_import import try_import

        # kort wachten op de uitkomst; loopt hij nog, dan kijkt de volgende ronde (15 min)
        res = await try_import(ctx, str(item["instance_id"]), str(q["downloadId"]),
                               movie_id=q.get("movieId"), series_id=q.get("seriesId"),
                               episode_ids=[int(q["episodeId"])] if q.get("episodeId") else None,
                               actor=ACTOR, wait_seconds=IMPORT_WAIT_SECONDS)
        return bool(res.get("imported"))
    except Exception as exc:  # noqa: BLE001 - dan wacht de import gewoon verder
        LOGGER.debug("handmatige import mislukt: %s", exc)
        return False


async def _search(ctx: AppContext, item: dict[str, Any], client: Any, missing_ids: list[int],
                  *, force: bool = False, manual: bool = False) -> bool:
    """Zoeken met terugvaltijd en binnen het zoekquotum van de instantie; elke zoekactie telt
    mee in dat quotum (verbeterlijst 36). ``force``: na een mislukte download (ook ondanks een
    zoekpauze); ``manual``: knop 'Nu opnieuw' (negeert alleen de terugvaltijd). Nooit voor een
    titel met 'Niet meer zoeken' (punt 25), ook niet na een mislukte download."""
    if search_off(ctx, item):
        return False
    if emergency.active(ctx):
        return False
    from mediaguard.downloads import holds

    item_id = int(item["id"])
    iid = str(item["instance_id"])
    due = _parse(item.get("next_action_at"))
    if not force and not manual and due and due > _now() and int(item.get("attempts") or 0) > 0:
        return False
    budget = store.search_budget(ctx, iid)
    if budget <= 0:
        item["search_note"] = "Zoekquotum van dit uur is op; volgende poging over een uur"
        store.update(ctx, item_id, next_action_at=_later(1))
        return False
    if item["media_type"] == "series":
        free = [e for e in missing_ids if holds.hold_for(
            ctx, holds.item_key(iid, "episode", e)) is None]
        if not free:
            if missing_ids:
                hold = holds.hold_for(ctx, holds.item_key(iid, "episode", missing_ids[0])) or {}
                item["search_note"] = hold.get("reason_nl") or "Zoeken even gepauzeerd"
                store.update(ctx, item_id, next_action_at=hold.get("until") or _later(24))
            return False
        batch = free[:min(MAX_EPISODES_PER_SEARCH, budget)]
        await client.episode_search(batch)
        store.record_search(ctx, iid, "EpisodeSearch", len(batch), item_id)
    else:
        hold = holds.hold_for(ctx, holds.item_key(iid, "movie", item["provider_id"]))
        if hold is not None and not force:
            item["search_note"] = hold["reason_nl"]
            store.update(ctx, item_id, next_action_at=hold["until"])
            return False
        await client.movies_search([int(item["provider_id"])])
        store.record_search(ctx, iid, "MoviesSearch", 1, item_id)
    attempts = int(item.get("attempts") or 0) + 1
    wait = BACKOFF_HOURS[min(attempts, len(BACKOFF_HOURS) - 1)]
    now = utcnow()
    store.update(ctx, item_id, attempts=attempts, last_action_at=now,
                 first_search_at=item.get("first_search_at") or now,
                 next_action_at=_later(wait))
    item.update(attempts=attempts, first_search_at=item.get("first_search_at") or now)
    store.event(ctx, item_id, "searching", "gezocht", ACTOR,
                f"Gezocht ({attempts}e poging)" + (f" naar {len(missing_ids)} aflevering(en)"
                                                  if missing_ids else ""))
    return True


def _identity_mismatches(ctx: AppContext, instance_id: str, kind: str,
                         file_ids: list[int]) -> list[dict[str, Any]]:
    """Bestanden van deze titel die volgens de laatste identiteitscontrole verkeerd zijn."""
    if not file_ids:
        return []
    marks = ",".join("?" * len(file_ids))
    return ctx.db.fetch_dicts(
        "SELECT r.provider_id, r.reason_nl FROM identity_results r JOIN (SELECT instance_id, "
        "kind, provider_id, MAX(id) AS mid FROM identity_results WHERE instance_id=? AND "
        f"kind=? AND provider_id IN ({marks}) GROUP BY instance_id, kind, provider_id) m "  # noqa: S608
        "ON m.mid=r.id WHERE r.status='mismatch' ORDER BY r.id",
        (instance_id, kind, *[str(f) for f in file_ids]))


async def _replace_wrong(ctx: AppContext, item: dict[str, Any], kind: str,
                         wrong: list[dict[str, Any]], observe: bool,
                         manual: bool = False) -> tuple[str, str, float]:
    """Verkeerd bestand via de quarantaine vervangen (verbeterlijst 12). Geeft
    (toelichting, volgende stap, wachttijd in uren); de tekst klopt met wat er gebeurde.
    Alleen bij eenduidig bewijs; bij twijfel blijft het bestand staan (AI of jij beslist)."""
    from mediaguard.delivery import replace

    head = "Een bestand bleek niet de juiste aflevering/film"
    if observe:
        return (f"{head}; proefmodus: MediaGuard vervangt niets",
                "Wordt vervangen na de overstap", 24.0)
    item_id = int(item["id"])
    replaced, queued, kept, searched = 0, 0, 0, False
    why: str | None = None
    review: str | None = None
    for row in wrong[:3]:
        try:
            res = await replace.replace_wrong_file(
                ctx, instance_id=str(item["instance_id"]), kind=kind,
                file_id=int(row["provider_id"]),
                reason_nl=str(row["reason_nl"] or "verkeerde aflevering/film"), actor=ACTOR,
                delivery_item_id=item_id, manual=manual)
        except Exception as exc:  # noqa: BLE001 - per bestand
            LOGGER.exception("vervangen van %s mislukt", row["provider_id"])
            res = {"status": "failed", "reason_nl": f"vervangen mislukt: {exc}"[:200]}
        if res["status"] == "replaced":
            replaced += 1
            searched = searched or bool(res.get("searched"))
            store.event(ctx, item_id, "checking", "vervangen", ACTOR,
                        "Verkeerd bestand in quarantaine gezet (herstelbaar); de juiste "
                        "release wordt gezocht")
        elif res["status"] == "queued":
            queued += 1
        elif res["status"] == "kept":
            kept += 1
        elif res["status"] == "review":
            review = review or res.get("reason_nl")
        elif res["status"] != "gone":
            why = why or res.get("reason_nl")
    if searched:
        # de vervanging zocht al: dat is een zoekpoging (niet een kwartier later nóg eens)
        now = utcnow()
        attempts = int(item.get("attempts") or 0) + 1
        item.update(attempts=attempts, first_search_at=item.get("first_search_at") or now)
        store.update(ctx, item_id, attempts=attempts, last_action_at=now,
                     first_search_at=item["first_search_at"])
    if why:
        return f"{head}; vervangen kan nu niet: {why}", "Opnieuw proberen over een uur", 1.0
    if review and not replaced:
        return ("Een bestand is misschien niet de juiste aflevering/film, maar het bewijs is "
                "niet eenduidig; het blijft staan",
                "Wacht op beoordeling door de AI of jou (Wacht op jou)", 6.0)
    if replaced:
        return (f"{head}; MediaGuard heeft het in quarantaine gezet en zoekt de juiste",
                "Juiste release wordt gezocht", 1.0 if searched else 0.25)
    if queued:
        return (f"{head}; de herstelwerker zet het nu in quarantaine",
                "Daarna wordt de juiste release gezocht", 0.25)
    if kept:
        return ("Je hebt een bestand zelf teruggezet uit de quarantaine; MediaGuard laat het "
                "staan", "Wordt opnieuw gecontroleerd", 0.25)
    return (f"{head}; het staat al niet meer in Sonarr/Radarr",
            "Juiste release wordt gezocht", 0.25)


async def _seerr_sync(ctx: AppContext, item: dict[str, Any]) -> bool:
    """Na levering Seerr laten bijwerken zodat hij 'beschikbaar' toont. Lukt dat niet, dan
    elk uur opnieuw (daarna dagelijks) tot het lukt (verbeterlijst 36)."""
    if not loads(item["seerr_request_ids_json"], []) or item.get("seerr_synced_at"):
        return True
    scan = "sonarr-scan" if item["media_type"] == "series" else "radarr-scan"
    ok = await _seerr(ctx, "POST", f"settings/jobs/{scan}/run")
    item_id = int(item["id"])
    if ok:
        await _seerr(ctx, "POST", "settings/jobs/availability-sync/run")
        store.update(ctx, item_id, seerr_synced_at=utcnow(), seerr_sync_attempts=0)
        return True
    tries = int(item.get("seerr_sync_attempts") or 0) + 1
    hourly = tries < SEERR_SYNC_HOURLY
    store.update(ctx, item_id, seerr_sync_attempts=tries,
                 next_action_at=_later(1 if hourly else 24),
                 next_step_nl="Seerr bijwerken lukte niet; opnieuw " +
                 ("over een uur" if hourly else "morgen"))
    return False


def _notify_once(ctx: AppContext, item: dict[str, Any], title: str, body: str) -> None:
    if item.get("notified_at"):
        return
    from mediaguard.api.system import notify

    notify(ctx, "info", title, body, link="/activiteit?tab=aanvragen",
           dedupe_key=f"delivery:{item['id']}:{title}")
    item["notified_at"] = utcnow()
    store.update(ctx, int(item["id"]), notified_at=item["notified_at"])


# ── één titel ─────────────────────────────────────────────────────────────────
def _observe_only(ctx: AppContext, instance_id: str) -> bool:
    """Proefmodus (of gepauzeerd): alleen meekijken, niets aanpassen."""
    from mediaguard.instances.registry import get_instance

    inst = get_instance(ctx, instance_id)
    return inst is None or inst.shadow_mode or inst.suspended or inst.archived


async def _fetch(client: Any, series: bool, pid: int) -> dict[str, Any]:
    return await (client.series_get(pid) if series else client.movie_get(pid))


async def process(ctx: AppContext, item: dict[str, Any], *, manual: bool = False) -> str:
    """Bepaal de stand en zet de volgende stap. Geeft de nieuwe stage terug."""
    from mediaguard.instances.registry import arr_client

    item_id = int(item["id"])
    if not item.get("instance_id") or not item.get("provider_id"):
        found = await _locate(ctx, item)
        if found is None:
            return await _not_in_arr(ctx, item, manual=manual)
        _set_location(ctx, item, found)
    series = item["media_type"] == "series"
    app = "sonarr" if series else "radarr"
    async with arr_client(ctx, str(item["instance_id"])) as client:
        try:
            record = await _fetch(client, series, int(item["provider_id"]))
        except ArrNotFound:
            record = None
    if record is None:
        # weg op deze plek: verplaatst (anime-routering) of bewust verwijderd
        found = await _locate(ctx, item, exclude=(str(item["instance_id"]),
                                                  int(item["provider_id"])), strict=True)
        if found is None:
            if item.get("located_at"):
                store.stop(ctx, item_id, "removed", ACTOR)
                return "stopped"
            # nooit echt gevonden (bv. verouderd ID uit Seerr): gewoon nog niet toegevoegd
            item["instance_id"] = item["provider_id"] = None
            store.update(ctx, item_id, instance_id=None, provider_id=None)
            return await _not_in_arr(ctx, item, manual=manual)
        moved_before = bool(item.get("located_at"))
        _set_location(ctx, item, found)
        if moved_before:
            store.event(ctx, item_id, "adding", "verhuisd", ACTOR, f"Staat nu in {found[0]}")
        store.update(ctx, item_id, next_action_at=utcnow())
        return "adding"
    if not item.get("located_at"):
        _set_location(ctx, item, (str(item["instance_id"]), int(item["provider_id"])))
    if not item.get("title"):
        store.update(ctx, item_id, title=record.get("title"))
        item["title"] = record.get("title")
    observe = _observe_only(ctx, str(item["instance_id"]))
    moved = None
    if not observe and not item.get("requirements_checked_at"):
        try:
            moved = await _maybe_route_anime(ctx, item, app, record)
        except Exception:  # noqa: BLE001 - routering mag de levering niet tegenhouden
            LOGGER.exception("anime-routering voor %s mislukt", item.get("title"))
    if moved:
        _set_location(ctx, item, moved)
        observe = _observe_only(ctx, str(item["instance_id"]))
    async with arr_client(ctx, str(item["instance_id"])) as client:
        if moved:
            record = await _fetch(client, series, int(item["provider_id"]))
        if not observe:
            record = await _ensure_requirements(ctx, item, client, record)
        return await _advance(ctx, item, client, record, observe=observe, manual=manual)


CINEMA_ONLY_DAYS = 365


def movie_released(record: dict[str, Any], now: datetime) -> bool:
    """Zelfde regel als Ontbrekend (wanted.engine.RELEASED_SQL): pas 'uit' na een digitale of
    fysieke release; zonder die datums alleen een bioscoopfilm van meer dan een jaar oud.
    'Untitled …' of zonder enige datum = nog niet verschenen (rapport 4, S8)."""
    titles = (record.get("title"), record.get("originalTitle"), record.get("_request_title"))
    if any(str(t or "").lower().startswith("untitled") for t in titles):
        return False
    # Radarr's eigen status: 'tba'/'announced' is nog niet uit, wat de datums ook zeggen
    if str(record.get("status") or "").lower() in ("tba", "announced"):
        return False
    year = int(record.get("year") or 0)
    if year and year > now.year:
        return False
    digital, physical = _parse(record.get("digitalRelease")), _parse(record.get("physicalRelease"))
    if digital or physical:
        return bool((digital and digital <= now) or (physical and physical <= now))
    cinema = _parse(record.get("inCinemas"))
    return bool(cinema and cinema <= now - timedelta(days=CINEMA_ONLY_DAYS))


def _vanished_summary(ctx: AppContext, instance_id: str, series: bool,
                      missing: list[int]) -> str | None:
    """'Downloads verdwijnen steeds (reden)' als de downloadbewaking dat zag (rapport 5, T9)."""
    from mediaguard.downloads import holds

    count, reason, paused = 0, None, None
    for ref in missing[:50]:
        key = holds.item_key(instance_id, "episode" if series else "movie", ref)
        hits = holds.vanished_for(ctx, key)
        if hits:
            count += len(hits)
            reason = reason or hits[-1].get("reason_nl")
        paused = paused or holds.hold_for(ctx, key)
    if not count:
        return None
    text = f"Downloads verdwijnen steeds ({count}× in 7 dagen: {reason})"
    if paused:
        text += f"; zoeken gepauzeerd tot {when_nl(_parse(paused['until']))}"
    return text


async def _last_download_failure(client: Any, series: bool, record_id: int) -> str | None:
    """Laatste mislukte download van deze titel volgens Sonarr/Radarr, in het Nederlands."""
    from mediaguard.downloads.failure_lookup import translate_failure

    try:
        rows = (await client.history(series_id=record_id, page_size=30) if series
                else await client.history_for_movie(record_id))
    except Exception:  # noqa: BLE001 - extra informatie; nooit een reden om te stoppen
        return None
    rows = rows.get("records", []) if isinstance(rows, dict) else list(rows or [])
    # rapport 6 / N6: Sonarr negeert 'seriesId' op /history (alleen seriesIds); zonder dit
    # filter kwam de laatste fout van de héle instantie (één serie) bij andere series terecht
    key = "seriesId" if series else "movieId"
    rows = [r for r in rows if r.get(key) is not None and int(r.get(key) or 0) == record_id]
    for h in sorted(rows, key=lambda r: str(r.get("date", "")), reverse=True):
        if str(h.get("eventType", "")).lower() == "downloadfailed":
            msg = (h.get("data") or {}).get("message")
            when = _parse(h.get("date"))
            stamp = f" ({when_nl(when)})" if when else ""
            return f"{translate_failure(msg)['reason_nl']}{stamp}: {h.get('sourceTitle') or ''}"
    return None


async def _advance(ctx: AppContext, item: dict[str, Any], client: Any,
                   record: dict[str, Any], *, observe: bool = False,
                   manual: bool = False) -> str:
    item_id = int(item["id"])
    series = item["media_type"] == "series"
    now = _now()
    wanted_seasons = set(loads(item["requested_seasons_json"], []) or [])
    missing: list[int] = []
    upcoming = 0
    have = 0
    file_ids: list[int] = []
    unavailable = False
    if series:
        eps = [e for e in await client.episodes(int(record["id"]))
               if int(e.get("seasonNumber") or 0) > 0 and e.get("monitored")
               and (not wanted_seasons or int(e["seasonNumber"]) in wanted_seasons)]
        for e in eps:
            aired = _parse(e.get("airDateUtc"))
            if e.get("hasFile"):
                have += 1
                if e.get("episodeFileId"):
                    file_ids.append(int(e["episodeFileId"]))
            elif aired is None or aired > now:
                upcoming += 1
            else:
                missing.append(int(e["id"]))
        total = have + len(missing) + upcoming
        unavailable = total > 0 and not missing and have == 0
    else:
        total = 1
        if record.get("hasFile"):
            have = 1
            mf = record.get("movieFile") or {}
            if mf.get("id"):
                file_ids.append(int(mf["id"]))
        elif record.get("isAvailable") is False or not movie_released(
                {**record, "_request_title": item.get("title")}, now):
            upcoming = 1
            unavailable = True
        else:
            missing = [int(record["id"])]
    progress: dict[str, Any] = {"total": total, "have": have, "missing": len(missing),
                                "upcoming": upcoming}
    if missing:
        last = await _last_download_failure(client, series, int(record["id"]))
        if last:
            progress["last_failure_nl"] = last
        vanished = _vanished_summary(ctx, str(item["instance_id"]), series, missing)
        if vanished:
            progress["vanished_nl"] = vanished
    queue = await client.queue(all_pages=True)
    queue = queue.get("records", []) if isinstance(queue, dict) else list(queue or [])
    entries = _queue_for(queue, item["media_type"], int(record["id"]))
    queue_stage, queue_note = (await _handle_queue(ctx, item, client, entries, missing,
                                                   observe=observe)
                               if entries else (None, None))
    if observe:
        progress["next_step_nl"] = "Proefmodus: MediaGuard kijkt alleen mee"

    def save(stage: str, state: str, detail: str, nxt: str, **extra: Any) -> str:
        store.update(ctx, item_id, stage=stage, state=state, detail_nl=detail,
                     next_step_nl=nxt, progress_json=dumps(progress), **extra)
        store.event(ctx, item_id, stage, "stand", ACTOR, detail)
        return stage

    kind = "episode_file" if series else "movie_file"
    if not missing and have and not queue_stage:
        wrong = _identity_mismatches(ctx, str(item["instance_id"]), kind, file_ids)
        if wrong:
            detail, nxt, wait = await _replace_wrong(ctx, item, kind, wrong, observe,
                                                     manual=manual)
            return save("checking", "active", detail, nxt, next_action_at=_later(wait))
        first = item.get("state") != "delivered"
        extra = " · " + (f"{upcoming} aflevering(en) nog niet uitgezonden" if upcoming else "")
        # tellers opnieuw: een nieuw seizoen begint niet meteen op 'na 30 dagen' (lijst 12)
        stage = save("delivered", "delivered",
                     ("Alles geleverd" if series else "Geleverd") + (extra if upcoming else ""),
                     "Nieuwe afleveringen worden automatisch gedownload" if series and upcoming
                     else "Klaar", delivered_at=item.get("delivered_at") or utcnow(),
                     next_action_at=_later(24), attempts=0, first_search_at=None,
                     notified_at=None)
        if first:
            audit(ctx.db, actor=ACTOR, action="delivery.delivered",
                  target_type=item["media_type"], target_id=item["provider_id"],
                  instance_id=item["instance_id"], reason=item.get("title") or "")
        await _seerr_sync(ctx, item)
        return stage
    if queue_stage in ("downloading", "importing"):
        detail = "Wordt gedownload" if queue_stage == "downloading" else "Wordt geïmporteerd"
        return save(queue_stage, "active", detail + (f" · {queue_note}" if queue_note else ""),
                    "Automatisch bijgehouden", next_action_at=_later(0.25))
    if series and total == 0:
        return save("waiting_release", "waiting_release", "Nog geen afleveringen bekend",
                    "Wordt gedownload zodra er afleveringen verschijnen",
                    next_action_at=_later(24))
    if unavailable or (not missing and upcoming):
        return save("waiting_release", "waiting_release",
                    "Nog niet verschenen" if not series else
                    f"{upcoming} aflevering(en) nog niet uitgezonden",
                    "Wordt gedownload zodra hij verschijnt (RSS + dagelijkse controle)",
                    next_action_at=_later(24))
    if search_off(ctx, item):  # 'Niet meer zoeken' op de titelpagina (punt 25)
        return save("searching", "active", SEARCH_OFF_NL, SEARCH_OFF_NEXT_NL,
                    next_action_at=_later(24))
    searched = False if observe else await _search(ctx, item, client, missing, manual=manual)
    first = _parse(item.get("first_search_at"))
    if first and _now() - first >= timedelta(days=NOT_FOUND_DAYS):
        _notify_once(ctx, item, f"'{item.get('title')}' is na {NOT_FOUND_DAYS} dagen nog "
                     "nergens te vinden",
                     "MediaGuard blijft wekelijks zoeken binnen jouw eisen; je hoeft niets te "
                     "doen.")
        return save("not_found", "not_found",
                    f"Na {NOT_FOUND_DAYS} dagen zoeken nog geen release die aan de eisen voldoet",
                    "Wekelijks opnieuw zoeken", next_action_at=_later(168))
    if observe:
        return save("searching", "active",
                    (f"{len(missing)} aflevering(en) ontbreken" if series else "Ontbreekt") +
                    " · proefmodus: MediaGuard zoekt niet zelf", "Na de overstap automatisch",
                    next_action_at=_later(6))
    cur = ctx.db.fetch_one("SELECT next_action_at FROM delivery_items WHERE id=?", (item_id,))
    nxt = _parse(cur["next_action_at"]) if cur else None
    when = when_nl(nxt)
    return save("searching", "active",
                (f"{len(missing)} aflevering(en) ontbreken" if series else "Wordt gezocht") +
                ("" if searched else " · wacht op volgende poging"),
                item.get("search_note") or f"Volgende zoekpoging {when}")


# ── AI-besluiten waar vroeger een klik nodig was ─────────────────────────────
async def resolve_routing_reviews(ctx: AppContext, limit: int = 10) -> int:
    """'Anime of niet?': de AI (of een vaste regel) beslist; de gebruiker kan het later nog
    terugdraaien op de Anime-pagina. Elke vraag apart: een titel die intussen weg is of een
    fout bij één titel houdt de rest (en de leveringsronde) niet tegen (verbeterlijst 9)."""
    done = 0
    rows = ctx.db.fetch_dicts(
        "SELECT * FROM anime_routing_actions WHERE status IN ('suggested','needs_review') "
        "ORDER BY id LIMIT ?", (limit,))
    for r in rows:
        try:
            if await _resolve_one(ctx, r):
                done += 1
        except ArrNotFound:
            ctx.db.execute("UPDATE anime_routing_actions SET status='dismissed', detail=?, "
                           "updated_at=? WHERE id=?",
                           (f"Titel bestaat niet meer in {r['source_instance']}", utcnow(),
                            r["id"]))
        except Exception:  # noqa: BLE001 - volgende vraag gewoon proberen
            LOGGER.exception("anime-vraag %s niet beslist", r.get("id"))
    return done


async def _resolve_one(ctx: AppContext, r: dict[str, Any]) -> bool:
    from mediaguard.routing import service as routing

    evidence = loads(r["evidence_json"], []) or []
    strong = any("Seriestype" in e or "Genre 'Anime'" in e for e in evidence)
    has_files = r["status"] == "needs_review"
    options = ["laten staan"] if has_files else ["verplaatsen", "laten staan"]
    decision = await decider.decide(
        ctx, "anime_routing",
        "Hoort deze titel in de anime-instantie (Dual Audio, absolute nummering)?",
        options, {"titel": r["title"], "bewijs": evidence, "bron": r["source_instance"],
                  "heeft_bestanden": has_files},
        fallback="verplaatsen" if strong and not has_files else "laten staan",
        fallback_reason_nl=("Duidelijk anime (seriestype of genre)" if strong
                            else "Geen eenduidig anime-bewijs") + (
            "; heeft al bestanden, verplaatsen zou ze loskoppelen" if has_files else ""))
    if decision.choice == "verplaatsen":
        await routing.evaluate(ctx, r["source_instance"], r["kind"], int(r["provider_id"]),
                               actor=decision.by, force=True, quiet=True)
    else:
        routing.exclude(ctx, r["source_instance"], r["kind"], r["external_id"], decision.by)
        ctx.db.execute("UPDATE anime_routing_actions SET status='dismissed', detail=?, "
                       "updated_at=? WHERE id=?", (decision.reason_nl[:400], utcnow(), r["id"]))
    audit(ctx.db, actor=decision.by, action="delivery.ai_decision",
          target_type=r["kind"], target_id=f"{r['source_instance']}:{r['provider_id']}",
          instance_id=r["source_instance"], reason=decision.reason_nl,
          new_value={"keuze": decision.choice},
          undo_hint="Anime-routering: terugzetten of verplaatsen")
    key = store.item_key("series" if r["kind"] == "series" else "movie",
                         r["external_id"], r["external_id"])
    row = ctx.db.fetch_one("SELECT id FROM delivery_items WHERE item_key=?", (key,))
    if row:
        store.event(ctx, int(row["id"]), "adding", "ai_besluit", decision.by,
                    f"Anime-instantie: {decision.choice} — {decision.reason_nl}",
                    {"opties": options, "keuze": decision.choice})
    return True


# ── ronde ─────────────────────────────────────────────────────────────────────
async def _process_safely(ctx: AppContext, item: dict[str, Any], *,
                          manual: bool = False) -> bool:
    """Eén titel; een fout blijft bij die titel (verbeterlijst 9). True = gelukt."""
    try:
        await process(ctx, item, manual=manual)
        return True
    except ArrError as exc:
        store.update(ctx, int(item["id"]), next_action_at=_later(0.5),
                     detail_nl=f"Sonarr/Radarr niet bereikbaar: {exc}"[:240],
                     next_step_nl="Over 30 minuten opnieuw")
    except Exception:  # noqa: BLE001 - één titel mag de rest niet tegenhouden
        LOGGER.exception("levering %s mislukt", item.get("item_key"))
        store.update(ctx, int(item["id"]), next_action_at=_later(1))
    return False


async def run_once(ctx: AppContext, *, force: bool = False) -> dict[str, Any]:
    if not ctx.config.value("delivery.enabled"):
        return {"skipped": "uit"}
    out: dict[str, Any] = {"discovered": 0, "processed": 0, "errors": 0}
    try:
        out["discovered"] = await discover(ctx)
    except Exception:  # noqa: BLE001 - de titels die er al zijn gewoon volgen
        LOGGER.exception("nieuwe aanvragen ophalen mislukt")
        out["errors"] += 1
    if emergency.active(ctx):
        out["skipped"] = "noodstop"
        return out
    try:
        out["routing_decisions"] = await resolve_routing_reviews(ctx)
    except Exception:  # noqa: BLE001
        LOGGER.exception("anime-vragen niet beslist")
        out["errors"] += 1
    now = utcnow()
    rows = ctx.db.fetch_dicts(
        "SELECT * FROM delivery_items WHERE stopped_at IS NULL AND "
        "(? OR next_action_at IS NULL OR next_action_at <= ?) "
        "ORDER BY CASE state WHEN 'active' THEN 0 ELSE 1 END, next_action_at LIMIT ?",
        (int(force), now, MAX_ITEMS_PER_RUN))
    rows += _search_back_on(ctx, now, {int(r["id"]) for r in rows})
    for row in rows:
        if emergency.active(ctx):
            out["skipped"] = "noodstop"
            break
        if await _process_safely(ctx, dict(row)):
            out["processed"] += 1
        else:
            out["errors"] += 1
    return out


async def retry_now(ctx: AppContext, item_id: int, actor: str) -> dict[str, Any] | None:
    """Knop 'Nu opnieuw': weer volgen en meteen de volgende stap zetten."""
    row = ctx.db.fetch_one("SELECT id FROM delivery_items WHERE id=?", (item_id,))
    if row is None:
        return None
    store.resume(ctx, item_id, actor)
    fresh = ctx.db.fetch_one("SELECT * FROM delivery_items WHERE id=?", (item_id,))
    if fresh is not None:
        await _process_safely(ctx, dict(fresh), manual=True)
    return store.get(ctx, item_id)


# ── 'Niet meer zoeken' per titel ─────────────────────────────────────────────
SEARCH_OFF_NL = "Zoeken staat uit voor deze titel ('Niet meer zoeken')"
SEARCH_OFF_NEXT_NL = "Zet 'Niet meer zoeken' uit op de titelpagina om weer te zoeken"


def search_off(ctx: AppContext, item: dict[str, Any]) -> bool:
    """De gebruiker zette op de titelpagina 'Niet meer zoeken' aan (backfill.excluded op de
    serie of film zelf). Alleen die titel-uitzondering telt: een uitzondering voor de hele
    instantie geldt voor backfill, niet voor een aanvraag die je net zelf deed."""
    if not item.get("instance_id") or not item.get("provider_id"):
        return False
    kind = "series" if item["media_type"] == "series" else "movie"
    scope_id = f"{item['instance_id']}:{int(item['provider_id'])}"
    return ctx.config.raw(kind, scope_id, "backfill.excluded") is True


def _search_back_on(ctx: AppContext, now: str, seen: set[int]) -> list[dict[str, Any]]:
    """'Niet meer zoeken' weer uit: deze ronde al meenemen en meteen zoeken, niet pas na de
    dagcontrole (de geparkeerde wachttijd telt dan niet als terugvaltijd)."""
    rows = ctx.db.fetch_dicts(
        "SELECT * FROM delivery_items WHERE state='active' AND stopped_at IS NULL AND "
        "detail_nl=? AND next_action_at>? "
        "LIMIT ?", (SEARCH_OFF_NL, now, MAX_ITEMS_PER_RUN))
    back = [r for r in rows if int(r["id"]) not in seen and not search_off(ctx, r)]
    for r in back:
        store.update(ctx, int(r["id"]), next_action_at=now)
        r["next_action_at"] = now
    return back
