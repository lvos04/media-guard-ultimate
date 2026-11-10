"""Automatische anime-routering (DEEL R4).

Seerr stuurt anime zonder expliciete serverkeuze naar de standaard Sonarr/Radarr
(getest tegen Seerr 3.5.1). MediaGuard herkent zulke titels en verplaatst ze naar de
anime-instantie: sonarr-series → sonarr-anime, radarr-films → radarr-animefilms.

Veiligheid:
- Alleen titels zonder bestanden en zonder lopende download worden automatisch
  verplaatst. Anders: status 'needs_review' met melding (nooit bestanden verplaatsen).
- Eerst toevoegen + controleren op de doelinstantie, pas daarna verwijderen bij de bron
  (altijd zonder bestanden te verwijderen).
- Herkenning is deterministisch met bewijs; per titel kan de gebruiker 'hier laten'
  kiezen, waarna MediaGuard die titel nooit meer verplaatst.
- Tijdens een noodstop wordt niets verplaatst of teruggezet; de volgende ronde na het
  opheffen pakt de titel weer op.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from typing import TYPE_CHECKING, Any

from mediaguard import emergency
from mediaguard.audit import audit
from mediaguard.db import dumps, loads, utcnow
from mediaguard.settings_registry import SettingDef

if TYPE_CHECKING:
    from mediaguard.context import AppContext

CATEGORY = "Anime-routering"
DEFAULT_TARGETS = {"sonarr-series": "sonarr-anime", "radarr-films": "radarr-animefilms"}


def setting_defs() -> list[SettingDef]:
    return [
        SettingDef("routing.anime.enabled", "bool", True, CATEGORY,
                   "Anime automatisch naar anime-instantie", "Auto-route anime",
                   help_nl="Nieuw toegevoegde anime in Sonarr Series of Radarr Films (bv. via "
                           "Seerr) wordt verplaatst naar Sonarr Anime of Radarr Animefilms."),
        SettingDef("routing.anime.mode", "enum", "auto", CATEGORY, "Werkwijze", "Mode",
                   choices=("auto", "suggest"),
                   choice_labels_nl={"auto": "Automatisch verplaatsen",
                                     "suggest": "Alleen voorstellen (ik keur goed)"}),
        SettingDef("routing.anime.detect_animation_japanese", "bool", True, CATEGORY,
                   "Japanse animatie als anime herkennen",
                   "Treat Japanese animation as anime",
                   help_nl="Naast het type 'anime' of genre 'Anime' telt ook genre "
                           "'Animation' met oorspronkelijke taal Japans."),
        SettingDef("routing.anime.max_age_hours", "int", 48, CATEGORY,
                   "Alleen recent toegevoegde titels (uur)", "Only recently added (hours)",
                   help_nl="Oudere titels worden nooit automatisch verplaatst, alleen "
                           "voorgesteld.", minimum=1, maximum=8760, unit="uur"),
        SettingDef("routing.anime.target_profile", "string", "", CATEGORY,
                   "Kwaliteitsprofiel op anime-instantie", "Quality profile on anime instance",
                   help_nl="Naam van het profiel. Leeg = profiel met 'anime' in de naam, "
                           "anders het eerste profiel.", scopes=("global", "instance")),
        SettingDef("routing.anime.target_root", "string", "", CATEGORY,
                   "Hoofdmap op anime-instantie", "Root folder on anime instance",
                   help_nl="Leeg = eerste hoofdmap van de anime-instantie.",
                   scopes=("global", "instance")),
        SettingDef("routing.anime.sweep_minutes", "int", 15, CATEGORY,
                   "Controle-interval (minuten)", "Sweep interval (minutes)",
                   help_nl="Vangnet naast de webhook: zoekt periodiek naar nieuwe anime.",
                   minimum=5, maximum=1440, unit="min", advanced=True),
    ]


# ── herkenning ───────────────────────────────────────────────────────────────
def _genres(item: dict[str, Any]) -> set[str]:
    return {str(g).strip().lower() for g in item.get("genres") or []}


def _original_language(item: dict[str, Any]) -> str:
    lang = item.get("originalLanguage")
    if isinstance(lang, dict):
        lang = lang.get("name")
    return str(lang or "").strip().lower()


def detect_anime(item: dict[str, Any], app: str, *,
                 japanese_animation: bool = True) -> dict[str, Any]:
    """Geeft {'is_anime': bool, 'evidence': [...]}; bewijs is uitlegbaar in de UI."""
    evidence: list[str] = []
    if app == "sonarr" and str(item.get("seriesType", "")).lower() == "anime":
        evidence.append("Seriestype is 'anime'")
    genres = _genres(item)
    if "anime" in genres:
        evidence.append("Genre 'Anime'")
    if japanese_animation and "animation" in genres and _original_language(item) in (
            "japanese", "ja", "jpn"):
        evidence.append("Genre 'Animation' en oorspronkelijke taal Japans")
    return {"is_anime": bool(evidence), "evidence": evidence}


# ── opslag ───────────────────────────────────────────────────────────────────
def _record(ctx: AppContext, *, source: str, target: str | None, kind: str, provider_id: int,
            external_id: int | None, title: str, status: str, evidence: list[str],
            detail: str = "", target_provider_id: int | None = None) -> int:
    now = utcnow()
    existing = ctx.db.fetch_one(
        "SELECT id FROM anime_routing_actions WHERE source_instance=? AND kind=? AND "
        "provider_id=?", (source, kind, provider_id))
    if existing:
        ctx.db.execute(
            "UPDATE anime_routing_actions SET target_instance=?, status=?, evidence_json=?, "
            "detail=?, target_provider_id=COALESCE(?, target_provider_id), updated_at=? "
            "WHERE id=?", (target, status, dumps(evidence), detail, target_provider_id, now,
                           existing["id"]))
        return int(existing["id"])
    return ctx.db.execute_insert(
        "INSERT INTO anime_routing_actions(source_instance,target_instance,kind,provider_id,"
        "external_id,title,status,evidence_json,detail,target_provider_id,created_at,"
        "updated_at) VALUES (?,?,?,?,?,?,?,?,?,?,?,?)",
        (source, target, kind, provider_id, external_id, title, status, dumps(evidence), detail,
         target_provider_id, now, now))


def _excluded(ctx: AppContext, source: str, kind: str, external_id: int | None) -> bool:
    if external_id is None:
        return False
    return ctx.db.fetch_one(
        "SELECT 1 FROM anime_routing_exclusions WHERE source_instance=? AND kind=? AND "
        "external_id=?", (source, kind, external_id)) is not None


def list_actions(ctx: AppContext, status: str | None = None) -> list[dict[str, Any]]:
    sql = "SELECT * FROM anime_routing_actions"
    params: list[Any] = []
    if status:
        sql += " WHERE status=?"
        params.append(status)
    rows = ctx.db.fetch_dicts(sql + " ORDER BY id DESC LIMIT 500", params)
    for r in rows:
        r["evidence"] = loads(r.pop("evidence_json"), [])
    return rows


# ── verplaatsen ──────────────────────────────────────────────────────────────
def _pick_profile(profiles: list[dict[str, Any]], wanted: str) -> int | None:
    if not profiles:
        return None
    if wanted:
        for p in profiles:
            if str(p.get("name", "")).lower() == wanted.lower():
                return int(p["id"])
    for p in profiles:
        if "anime" in str(p.get("name", "")).lower():
            return int(p["id"])
    return int(profiles[0]["id"])


def _recent(item: dict[str, Any], max_age_hours: int) -> bool:
    added = item.get("added")
    if not added:
        return False
    try:
        when = datetime.fromisoformat(str(added).replace("Z", "+00:00"))
    except ValueError:
        return False
    return datetime.now(UTC) - when <= timedelta(hours=max_age_hours)


async def _in_queue(client: Any, kind: str, provider_id: int) -> bool:
    data = await client.queue(all_pages=True)
    records = data.get("records", data) if isinstance(data, dict) else data
    key = "seriesId" if kind == "series" else "movieId"
    return any(int(r.get(key) or -1) == provider_id for r in records or [])


async def evaluate(ctx: AppContext, source: str, kind: str, provider_id: int, *,
                   actor: str = "svc:anime-routing", force: bool = False,
                   quiet: bool = False) -> dict[str, Any]:
    """Beoordeel één titel op de broninstantie en verplaats indien toegestaan.
    force=True: handmatig 'Verplaats nu' (negeert leeftijd en suggest-modus, nooit de
    bestanden/download-voorwaarde)."""
    from mediaguard.instances.registry import arr_client, get_instance

    target = DEFAULT_TARGETS.get(source)
    if target is None:
        return {"status": "ignored", "reason": "geen anime-doel voor deze instantie"}
    if not force and not ctx.config.value("routing.anime.enabled"):
        return {"status": "ignored", "reason": "anime-routering staat uit"}
    tgt = get_instance(ctx, target)
    if tgt is None or tgt.archived:
        return {"status": "ignored", "reason": f"doelinstantie {target} ontbreekt"}
    from mediaguard.arr.client import ArrNotFound

    app = "sonarr" if kind == "series" else "radarr"
    async with arr_client(ctx, source) as src:
        try:
            item = await (src.series_get(provider_id) if kind == "series"
                          else src.movie_get(provider_id))
        except ArrNotFound:
            item = {}
        if not item:
            # titel inmiddels verwijderd: open vragen erover sluiten, anders blijft elke
            # leveringsronde erop vastlopen (verbeterlijst 9)
            ctx.db.execute(
                "UPDATE anime_routing_actions SET status='dismissed', detail=?, updated_at=? "
                "WHERE source_instance=? AND kind=? AND provider_id=? AND status IN "
                "('suggested','needs_review')",
                ("Titel bestaat niet meer in de instantie", utcnow(), source, kind, provider_id))
            return {"status": "ignored", "reason": "titel niet gevonden"}
        external = item.get("tvdbId") if kind == "series" else item.get("tmdbId")
        external_id = int(external) if external else None
        title = str(item.get("title") or "")
        found = detect_anime(item, app, japanese_animation=bool(
            ctx.config.value("routing.anime.detect_animation_japanese")))
        if not found["is_anime"]:
            return {"status": "not_anime"}
        if _excluded(ctx, source, kind, external_id):
            return {"status": "excluded", "reason": "gebruiker koos 'hier laten'"}
        stats = item.get("statistics") or {}
        has_files = bool(item.get("hasFile")) or int(stats.get("episodeFileCount") or 0) > 0 \
            or int(stats.get("movieFileCount") or 0) > 0 or int(
                stats.get("sizeOnDisk") or item.get("sizeOnDisk") or 0) > 0
        downloading = await _in_queue(src, kind, provider_id)
        common = {"source": source, "target": target, "kind": kind, "provider_id": provider_id,
                  "external_id": external_id, "title": title, "evidence": found["evidence"]}
        if has_files or downloading:
            why = "heeft al bestanden" if has_files else "wordt nu gedownload"
            _record(ctx, status="needs_review", detail=f"Niet automatisch verplaatst: {why}",
                    **common)
            if not quiet and not ctx.config.value("delivery.enabled"):
                _notify(ctx, f"Anime '{title}' staat in {source} maar {why}",
                        "Verplaats handmatig of laat staan.")
            return {"status": "needs_review", "reason": why}
        age_ok = _recent(item, int(ctx.config.value("routing.anime.max_age_hours")))
        if not force and (ctx.config.value("routing.anime.mode") == "suggest" or not age_ok):
            _record(ctx, status="suggested", detail="Wacht op goedkeuring" if age_ok
                    else "Ouder dan ingestelde termijn: alleen voorstel", **common)
            return {"status": "suggested"}
        if external_id is None:
            _record(ctx, status="failed", detail="Geen TVDb/TMDb-ID", **common)
            return {"status": "failed", "reason": "geen extern ID"}
        if emergency.skip(ctx, "anime-routering"):
            # niets vastleggen: na de noodstop beoordeelt de volgende ronde hem opnieuw
            return {"status": "paused", "reason": emergency.message_nl("verplaatsen")}
        try:
            new_id = await _move(ctx, src, item, kind, target, external_id)
        except Exception as exc:  # noqa: BLE001 - bron blijft ongewijzigd bij fout
            _record(ctx, status="failed", detail=f"{type(exc).__name__}: {exc}"[:400], **common)
            _notify(ctx, f"Verplaatsen van '{title}' naar {target} mislukt",
                    "De titel staat nog ongewijzigd in de broninstantie.")
            return {"status": "failed", "reason": str(exc)[:200]}
    _record(ctx, status="moved", detail="Verplaatst", target_provider_id=new_id, **common)
    audit(ctx.db, actor=actor, action="routing.anime_moved", target_type=kind,
          target_id=f"{source}:{provider_id}", instance_id=source, reason="; ".join(
              found["evidence"]), old_value={"instance": source, "id": provider_id},
          new_value={"instance": target, "id": new_id},
          undo_hint="Kies 'Terugzetten' bij Anime-routering")
    ctx.events.publish("routing.anime_moved", {"title": title, "from": source, "to": target},
                       instance_id=target)
    return {"status": "moved", "target": target, "target_id": new_id}


async def _move(ctx: AppContext, src: Any, item: dict[str, Any], kind: str, target: str,
                external_id: int) -> int:
    from mediaguard.instances.registry import arr_client

    chain = {"instance": target}
    async with arr_client(ctx, target) as dst:
        profiles = await dst.quality_profiles()
        roots = await dst.root_folders()
        profile_id = _pick_profile(profiles, str(ctx.config.value(
            "routing.anime.target_profile", chain) or ""))
        root = str(ctx.config.value("routing.anime.target_root", chain) or "") or (
            roots[0]["path"] if roots else "")
        if profile_id is None or not root:
            raise RuntimeError(f"{target} heeft geen kwaliteitsprofiel of hoofdmap")
        if kind == "series":
            existing = [s for s in await dst.series_list() if s.get("tvdbId") == external_id]
            if existing:
                new_id = int(existing[0]["id"])
            else:
                found = (await dst.series_lookup(f"tvdb:{external_id}") or [None])[0]
                if not found:
                    raise RuntimeError("serie niet gevonden op doelinstantie")
                body = {**found, "qualityProfileId": profile_id, "rootFolderPath": root,
                        "seriesType": "anime", "monitored": bool(item.get("monitored")),
                        "seasonFolder": item.get("seasonFolder", True),
                        "monitorNewItems": item.get("monitorNewItems", "all"),
                        "seasons": [{"seasonNumber": s["seasonNumber"],
                                     "monitored": bool(s.get("monitored"))}
                                    for s in item.get("seasons", [])],
                        "tags": [], "addOptions": {"monitor": "none",
                                                   "searchForMissingEpisodes": False}}
                body.pop("id", None)
                body.pop("path", None)
                new_id = int((await dst.series_add(body))["id"])
                # seizoens- en afleveringsvlaggen exact overnemen
                await dst.season_pass(new_id, {int(s["seasonNumber"]): bool(s.get("monitored"))
                                               for s in item.get("seasons", [])},
                                      item.get("monitorNewItems"))
                src_eps = {(e["seasonNumber"], e["episodeNumber"]): bool(e.get("monitored"))
                           for e in await src.episodes(int(item["id"]))}
                on, off = [], []
                for e in await dst.episodes(new_id):
                    want = src_eps.get((e["seasonNumber"], e["episodeNumber"]))
                    if want is not None and want != bool(e.get("monitored")):
                        (on if want else off).append(int(e["id"]))
                if on:
                    await dst.episodes_monitor(on, True)
                if off:
                    await dst.episodes_monitor(off, False)
            check = await dst.series_get(new_id)
            if not check or check.get("tvdbId") != external_id:
                raise RuntimeError("controle na toevoegen mislukt")
            await src.series_delete(int(item["id"]), delete_files=False)
            if item.get("monitored"):
                await dst.series_search(new_id)
        else:
            existing = [m for m in await dst.movie_list() if m.get("tmdbId") == external_id]
            if existing:
                new_id = int(existing[0]["id"])
            else:
                found = (await dst.movie_lookup(f"tmdb:{external_id}") or [None])[0]
                if not found:
                    raise RuntimeError("film niet gevonden op doelinstantie")
                body = {**found, "qualityProfileId": profile_id, "rootFolderPath": root,
                        "monitored": bool(item.get("monitored")),
                        "minimumAvailability": item.get("minimumAvailability", "released"),
                        "tags": [], "addOptions": {"searchForMovie": bool(item.get(
                            "monitored"))}}
                body.pop("id", None)
                body.pop("path", None)
                new_id = int((await dst.movie_add(body))["id"])
            check = await dst.movie_get(new_id)
            if not check or check.get("tmdbId") != external_id:
                raise RuntimeError("controle na toevoegen mislukt")
            await src.movie_delete(int(item["id"]), delete_files=False)
    return new_id


async def undo(ctx: AppContext, action_id: int, actor: str) -> dict[str, Any]:
    """Terugzetten: titel weer in de broninstantie, uit de anime-instantie (zonder
    bestanden te verwijderen) en nooit meer automatisch verplaatsen."""
    from mediaguard.instances.registry import arr_client

    row = ctx.db.fetch_one("SELECT * FROM anime_routing_actions WHERE id=?", (action_id,))
    if row is None or row["status"] != "moved":
        raise ValueError("Alleen verplaatste titels kunnen worden teruggezet")
    if emergency.active(ctx):
        raise ValueError(emergency.message_nl("terugzetten"))
    exclude(ctx, row["source_instance"], row["kind"], row["external_id"], actor)
    async with arr_client(ctx, row["target_instance"]) as tgt:
        item = await (tgt.series_get(int(row["target_provider_id"])) if row["kind"] == "series"
                      else tgt.movie_get(int(row["target_provider_id"])))
        stats = item.get("statistics") or {}
        if item.get("hasFile") or int(stats.get("episodeFileCount") or 0) > 0:
            raise ValueError("De titel heeft inmiddels bestanden; terugzetten kan dan niet "
                             "automatisch")
        back = await _move(ctx, tgt, item, row["kind"], row["source_instance"],
                           int(row["external_id"]))
    ctx.db.execute("UPDATE anime_routing_actions SET status='reverted', detail=?, "
                   "updated_at=? WHERE id=?", (f"Teruggezet (nieuw id {back})", utcnow(),
                                               action_id))
    audit(ctx.db, actor=actor, action="routing.anime_reverted", target_type=row["kind"],
          target_id=action_id, instance_id=row["source_instance"])
    return {"status": "reverted", "id": back}


def exclude(ctx: AppContext, source: str, kind: str, external_id: int | None,
            actor: str) -> None:
    if external_id is None:
        return
    ctx.db.execute("INSERT OR IGNORE INTO anime_routing_exclusions(source_instance,kind,"
                   "external_id,created_at,created_by) VALUES (?,?,?,?,?)",
                   (source, kind, int(external_id), utcnow(), actor))


def _notify(ctx: AppContext, title: str, body: str) -> None:
    from mediaguard.api.system import notify

    notify(ctx, "warning", title, body, link="/anime")


async def _recent_items(ctx: AppContext, client: Any, source: str, app: str, kind: str,
                        max_age: int) -> list[dict[str, Any]]:
    """Alleen recent toegevoegde titels ophalen. Is de bibliotheek van deze instantie
    onlangs gesynchroniseerd, dan staan nieuwe titels al in lib_items en halen we alleen
    die op (in plaats van elke 15 minuten de hele serie-/filmlijst van megabytes)."""
    fresh = ctx.db.fetch_one(
        "SELECT MAX(finished_at) AS t FROM library_sync_runs WHERE instance_id=? AND "
        "status IN ('succeeded','partial')", (source,))
    last_sync = fresh["t"] if fresh else None
    window = (datetime.now(UTC) - timedelta(hours=2)).isoformat(timespec="seconds")
    if not last_sync or last_sync < window:
        return list(await (client.series_list() if app == "sonarr" else client.movie_list()))
    since = (datetime.now(UTC) - timedelta(hours=max_age)).isoformat(timespec="seconds")
    ids = [int(r["provider_id"]) for r in ctx.db.fetch_all(
        "SELECT provider_id FROM lib_items WHERE instance_id=? AND kind=? AND removed_at IS "
        "NULL AND added_at>=?", (source, kind, since))]
    out = []
    for pid in ids:
        item = await client.get(f"{'series' if app == 'sonarr' else 'movie'}/{pid}")
        if item:
            out.append(item)
    # titels die ná de laatste sync zijn toegevoegd komen via de webhook (SeriesAdd/
    # MovieAdded) of bij de volgende sync binnen
    return out


async def sweep(ctx: AppContext) -> list[dict[str, Any]]:
    """Vangnet: recent toegevoegde titels op de bron-instanties beoordelen."""
    from mediaguard.instances.registry import arr_client, get_instance

    results = []
    if not ctx.config.value("routing.anime.enabled"):
        return results
    if emergency.skip(ctx, "anime-routering"):
        return results
    max_age = int(ctx.config.value("routing.anime.max_age_hours"))
    for source in DEFAULT_TARGETS:
        inst = get_instance(ctx, source)
        if inst is None or inst.archived or inst.suspended:
            continue
        kind = "series" if inst.app == "sonarr" else "movie"
        async with arr_client(ctx, source) as client:
            items = await _recent_items(ctx, client, source, inst.app, kind, max_age)
        handled = {int(r["provider_id"]) for r in ctx.db.fetch_all(
            "SELECT provider_id FROM anime_routing_actions WHERE source_instance=? AND kind=? "
            "AND status IN ('moved','needs_review','failed','suggested')", (source, kind))}
        for item in items:
            if int(item["id"]) in handled or not _recent(item, max_age):
                continue
            if detect_anime(item, inst.app, japanese_animation=bool(ctx.config.value(
                    "routing.anime.detect_animation_japanese")))["is_anime"]:
                results.append(await evaluate(ctx, source, kind, int(item["id"])))
    return results


async def backlog_sweep(ctx: AppContext) -> list[dict[str, Any]]:
    """Rapport 2 / N23: ook bestaande (oudere) anime in de gewone instanties beoordelen.

    De toestemmingsregels blijven gelden: titels ouder dan de ingestelde termijn worden
    alleen *voorgesteld* (status 'suggested') en titels met bestanden of een lopende download
    'needs_review' — zo ziet Anime-routering wat er staat en waarom, in plaats van 'Nog niets
    gerouteerd'. Eén keer per dag."""
    from mediaguard.instances.registry import arr_client, get_instance

    results: list[dict[str, Any]] = []
    if not ctx.config.value("routing.anime.enabled"):
        return results
    if emergency.skip(ctx, "anime-routering"):
        return results
    for source in DEFAULT_TARGETS:
        inst = get_instance(ctx, source)
        if inst is None or inst.archived or inst.suspended:
            continue
        kind = "series" if inst.app == "sonarr" else "movie"
        handled = {int(r["provider_id"]) for r in ctx.db.fetch_all(
            "SELECT provider_id FROM anime_routing_actions WHERE source_instance=? AND kind=?",
            (source, kind))}
        async with arr_client(ctx, source) as client:
            items = list(await (client.series_list() if inst.app == "sonarr"
                                else client.movie_list()))
        for item in items:
            if int(item["id"]) in handled:
                continue
            if detect_anime(item, inst.app, japanese_animation=bool(ctx.config.value(
                    "routing.anime.detect_animation_japanese")))["is_anime"]:
                results.append(await evaluate(ctx, source, kind, int(item["id"]),
                                              quiet=True))
    if results:
        _notify(ctx, f"Anime-routering: {len(results)} bestaande titel(s) beoordeeld",
                "Zie Anime-routering voor wat MediaGuard voorstelt en waarom.")
    return results
