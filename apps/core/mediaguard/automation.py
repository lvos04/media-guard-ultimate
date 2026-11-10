"""Set-and-forget-automatisering voor identiteitstwijfel en opgeruimde titels.

De gebruiker hoeft hier niets voor te doen:

1. **Herbeoordelen** — twijfel-/afwijkingsresultaten van een oudere regelversie worden
   opnieuw beoordeeld met de huidige regels (bijv. seizoenspakketten sinds 0.2.6).
2. **Zwakke twijfel** — alleen zwakke aanwijzingen (bestandsnaam, speelduur) is geen taak:
   na ``identity.uncertain_auto_keep_days`` blijft het bestand en wordt het 'waarschijnlijk'.
3. **AI als scheidsrechter** — is een AI-provider met sleutel en toestemming ingesteld, dan
   beoordeelt die echte conflicten (sterk bewijs spreekt elkaar tegen). Alleen omkeerbare
   uitkomsten: 'klopt' (verified) of 'verkeerde aflevering' vastleggen; nooit bestanden.
4. **Verkeerde aflevering of film vervangen** — bij een bewezen, eenduidige mismatch zet
   MediaGuard het bestand eerst in zijn eigen quarantaine (herstelbaar), laat Sonarr/Radarr
   het daarna uit de bibliotheek halen en zoekt de juiste release (``delivery/replace.py``).
5. **Van TMDb/TVDB verwijderd** — titels zonder bestand die Radarr/Sonarr als verwijderd
   markeren, worden uit de instantie gehaald (bestanden worden nooit aangeraakt).
Alles komt in de audit en dus in Activiteit → Recent. Tijdens een noodstop slaan stap 4 en 5
over (niets verwijderen); beoordelen gaat door.
"""

from __future__ import annotations

import asyncio
import contextlib
import json
import logging
from datetime import UTC, datetime, timedelta
from typing import Any

from mediaguard import emergency, taskwatch
from mediaguard.arr.client import ArrError
from mediaguard.audit import audit
from mediaguard.context import AppContext
from mediaguard.db import loads, utcnow
from mediaguard.identity import service as identity
from mediaguard.identity.engine import (
    ENGINE_VERSION,
    EpisodeRef,
    Evidence,
    IdentityResult,
    decide,
    refs_covered,
)
from mediaguard.settings_registry import REGISTRY, SettingDef

LOGGER = logging.getLogger(__name__)
CATEGORY = "Identiteit"
INTERVAL_SECONDS = 1800
CONTAINER_SOURCES = ("download_history", "release_title")  # mogen méér afleveringen bevatten


def register_settings() -> None:
    REGISTRY.register(
        SettingDef("identity.uncertain_auto_keep_days", "int", 7, CATEGORY,
                   "Zwakke twijfel automatisch afsluiten na (dagen)",
                   "Auto-keep weak doubt after (days)",
                   help_nl="Alleen zwakke aanwijzingen (bestandsnaam, speelduur) zijn geen "
                           "reden om iets te doen: daarna blijft het bestand en verdwijnt het "
                           "uit 'Wacht op jou'.", minimum=0, maximum=365, unit="dagen",
                   advanced=True),
        SettingDef("identity.ai_tiebreak", "bool", True, CATEGORY,
                   "AI beslist bij tegenstrijdig bewijs",
                   "AI decides on conflicting evidence",
                   help_nl="Als een AI-provider is ingesteld (sleutel + toestemming) beoordeelt "
                           "die gevallen waarin sterk bewijs elkaar tegenspreekt. Alleen "
                           "omkeerbare uitkomsten; het maandbudget geldt.", advanced=True),
        SettingDef("arr.cleanup_removed_titles", "bool", True, CATEGORY,
                   "Van TMDb/TVDB verwijderde titels zonder bestand opruimen",
                   "Clean up titles removed from TMDb/TVDB",
                   help_nl="Radarr/Sonarr melden titels die bij de metadatabron verdwenen "
                           "zijn. Zonder bestand haalt MediaGuard ze weg (nooit bestanden); "
                           "met bestand blijven ze gewoon staan.", advanced=True),
    )


# -- 1. herbeoordelen met de huidige regels ------------------------------------------------------
def _latest_rows(ctx: AppContext, where: str, params: tuple[Any, ...] = (),
                 limit: int = 500) -> list[dict[str, Any]]:
    return ctx.db.fetch_dicts(
        "SELECT r.* FROM identity_results r JOIN (SELECT instance_id, kind, provider_id, "
        "MAX(id) AS mid FROM identity_results GROUP BY instance_id, kind, provider_id) m "
        f"ON m.mid=r.id WHERE {where} ORDER BY r.id LIMIT ?", (*params, limit))


def _evidence(row: dict[str, Any]) -> list[Evidence]:
    out = []
    for e in loads(row.get("evidence_json"), []) or []:
        claimed = [EpisodeRef.from_dict(c) for c in e.get("claimed") or []]
        out.append(Evidence(str(e.get("source")), e.get("relation") or "neutral",
                            str(e.get("detail_nl") or ""), claimed,
                            int(e.get("weight", -1)), int(e.get("tier", -1)),
                            dict(e.get("data") or {})))
    return out


def rejudge(row: dict[str, Any], threshold: int = 80) -> IdentityResult:
    expected = [EpisodeRef.from_dict(e) for e in loads(row.get("expected_json"), []) or []]
    evidence = _evidence(row)
    for e in evidence:
        if e.source in CONTAINER_SOURCES and e.claimed and e.relation in ("supports",
                                                                          "contradicts"):
            rel = refs_covered(expected, e.claimed)
            if rel is not None:
                e.relation = "supports" if rel else "contradicts"
    return decide(expected, evidence, mismatch_threshold=threshold)


def reevaluate(ctx: AppContext) -> int:
    rows = _latest_rows(ctx, "r.status IN ('mismatch','uncertain') AND r.source!='user' AND "
                             "r.engine_version<?", (ENGINE_VERSION,))
    changed = 0
    for row in rows:
        threshold = int(ctx.config.value("identity.mismatch_min_weight"))
        result = rejudge(row, threshold)
        identity.save(ctx, instance_id=row["instance_id"], kind=row["kind"],
                      provider_id=row["provider_id"], path=row["file_path"], result=result,
                      source="reevaluate", actor="svc:identity")
        if result.status != row["status"]:
            changed += 1
            audit(ctx.db, actor="svc:identity", action="identity.reevaluated",
                  target_type=row["kind"], target_id=row["provider_id"],
                  instance_id=row["instance_id"], old_value={"status": row["status"]},
                  new_value={"status": result.status, "file": row["file_path"]},
                  reason=result.reason_nl)
    return changed


# -- 2. zwakke twijfel is geen taak ----------------------------------------------------------
def weak_only(row: dict[str, Any]) -> bool:
    """Alleen zwakke aanwijzingen tegen (geen sterk onafhankelijk tegenbewijs)."""
    if row["status"] != "uncertain":
        return False
    return not any(e.relation == "contradicts" and e.independent and e.weight >= 80
                   for e in _evidence(row))


def auto_keep_uncertain(ctx: AppContext) -> int:
    days = int(ctx.config.value("identity.uncertain_auto_keep_days"))
    cutoff = (datetime.now(UTC) - timedelta(days=days)).isoformat(timespec="seconds")
    kept = 0
    for row in _latest_rows(ctx, "r.status='uncertain' AND r.created_at<=?", (cutoff,)):
        if not weak_only(row):
            continue
        expected = [EpisodeRef.from_dict(e) for e in loads(row["expected_json"], []) or []]
        result = IdentityResult(
            "probable", 0.5, False,
            "Alleen zwakke aanwijzingen voor een afwijking; het bestand blijft staan.",
            expected, list(expected), _evidence(row))
        identity.save(ctx, instance_id=row["instance_id"], kind=row["kind"],
                      provider_id=row["provider_id"], path=row["file_path"], result=result,
                      source="auto", actor="svc:identity")
        kept += 1
    return kept


# -- 3. AI als scheidsrechter ------------------------------------------------------------------
AI_SCHEMA: dict[str, Any] = {
    "type": "object", "additionalProperties": False,
    "required": ["verdict", "confidence", "reason_nl"],
    "properties": {
        "verdict": {"type": "string", "enum": ["match", "wrong_episode", "unsure"]},
        "confidence": {"type": "number", "minimum": 0, "maximum": 1},
        "reason_nl": {"type": "string", "maxLength": 400},
    },
}
AI_SYSTEM = (
    "Je controleert of een gedownload videobestand de aflevering is die de mediabibliotheek "
    "verwacht. Je krijgt alleen bestandsnaam, releasenaam en de bewijsregels van MediaGuard. "
    "Let op anime-nummering (absolute vs seizoen/aflevering), seizoenspakketten (een pakket "
    "bevat meerdere afleveringen, dat is geen fout), specials (S00) en multi-afleveringen. "
    "Antwoord 'match' als het bestand bij de verwachte aflevering hoort, 'wrong_episode' "
    "alleen als het duidelijk een andere aflevering is, anders 'unsure'. Bij een film krijg "
    "je titel en jaar: een vertaalde titel of een ander releasejaar met ±1 is geen fout, een "
    "remake met dezelfde titel wel. Antwoord in JSON.")


def _expected_movie(ctx: AppContext, row: dict[str, Any]) -> list[str]:
    if row["kind"] not in ("movie", "movie_file") or not str(row["provider_id"]).isdigit():
        return []
    pid = int(row["provider_id"])
    if row["kind"] == "movie_file":
        f = ctx.db.fetch_one("SELECT owner_provider_id FROM lib_files WHERE instance_id=? AND "
                             "kind='movie' AND provider_id=?", (row["instance_id"], pid))
        if f is None:
            return []
        pid = int(f["owner_provider_id"])
    m = ctx.db.fetch_one("SELECT title, year, tmdb_id, imdb_id FROM lib_items WHERE "
                         "instance_id=? AND kind='movie' AND provider_id=?",
                         (row["instance_id"], pid))
    if m is None:
        return []
    ids = ", ".join(f"{k}={m[k]}" for k in ("tmdb_id", "imdb_id") if m[k])
    return [f"film: {m['title']} ({m['year'] or '?'})" + (f" [{ids}]" if ids else "")]


def ai_provider(ctx: AppContext) -> str | None:
    """De ingestelde provider, of anders de enige/eerste met een API-sleutel."""
    from mediaguard.ai import service as ai
    from mediaguard.ai.constants import SECRET_NAMES

    chosen = ai.current_provider(ctx)
    if chosen in SECRET_NAMES and ctx.secrets.has(SECRET_NAMES[chosen]):
        return chosen
    return next((p for p, name in SECRET_NAMES.items() if ctx.secrets.has(name)), None)


def ai_ready(ctx: AppContext) -> tuple[str, str] | None:
    """(provider, model) als de AI zelf mag beslissen: sleutel, toestemming, model + prijs.
    Los van ``ai.mode``: dit is een begrensde, omkeerbare taak (alleen een oordeel)."""
    from mediaguard.ai import service as ai
    from mediaguard.ai import settings as ai_settings

    if not bool(ctx.config.value("identity.ai_tiebreak")):
        return None
    provider = ai_provider(ctx)
    if provider is None or not ai.consent_status(ctx, provider)["granted"]:
        return None
    model = ai_settings.provider_model(ctx, provider, "anime_identity")
    if not model and provider == "anthropic":
        model = ai_settings.DEFAULT_ANTHROPIC_MODELS[0]
    if not model and provider == "openai":
        model = ai_settings.DEFAULT_OPENAI_MODELS[0]
    table = ai_settings.get(ctx, "ai.price_table") or {}
    return (provider, model) if model and f"{provider}:{model}" in table else None


def ai_missing_consent(ctx: AppContext) -> str | None:
    """Provider met sleutel maar zonder toestemming (dan vraagt 'Wacht op jou' één klik)."""
    from mediaguard.ai import service as ai

    if not bool(ctx.config.value("identity.ai_tiebreak")):
        return None
    provider = ai_provider(ctx)
    if provider is None or ai.consent_status(ctx, provider)["granted"]:
        return None
    return provider


def conflicting(row: dict[str, Any]) -> bool:
    if row["status"] == "mismatch" and not int(row["actionable"] or 0):
        return True
    return row["status"] == "uncertain" and not weak_only(row)


async def ai_tiebreak(ctx: AppContext, limit: int = 10) -> int:
    from mediaguard.ai import service as ai

    ready = ai_ready(ctx)
    if ready is None:
        return 0
    provider, model = ready
    decided = 0
    rows = [r for r in _latest_rows(ctx, "r.status IN ('mismatch','uncertain') AND "
                                         "r.source NOT IN ('user','ai')") if conflicting(r)]
    for row in rows[:limit]:
        expected = [EpisodeRef.from_dict(e) for e in loads(row["expected_json"], []) or []]
        payload = {
            "bestand": str(row["file_path"] or "").rsplit("/", 1)[-1],
            "verwacht": [e.label() for e in expected] or _expected_movie(ctx, row),
            "bewijs": [f"{e.source} ({e.relation}): {e.detail_nl}" for e in _evidence(row)],
        }
        out = await ai.call_structured(
            ctx, provider=provider, model=model, task_type="anime_identity", case_id=None,
            purpose="identity_tiebreak", system=AI_SYSTEM,
            user=json.dumps(payload, ensure_ascii=False), schema=AI_SCHEMA)
        if not out.ok or not isinstance(out.data, dict):
            if out.error_kind == "no_key" or (out.error_kind or "").startswith("budget_"):
                break  # geldt voor alles: deze ronde stoppen
            continue
        verdict, conf = str(out.data.get("verdict")), float(out.data.get("confidence") or 0)
        reason = f"AI ({model}): {out.data.get('reason_nl') or verdict}"
        if verdict == "match" and conf >= 0.85:
            result = IdentityResult("verified", conf, False, reason, expected, list(expected),
                                    _evidence(row))
        elif verdict == "wrong_episode" and conf >= 0.9:
            actual = [EpisodeRef.from_dict(e) for e in loads(row["actual_json"], []) or []]
            result = IdentityResult("mismatch", conf, bool(actual), reason, expected, actual,
                                    _evidence(row))
        else:
            continue
        identity.save(ctx, instance_id=row["instance_id"], kind=row["kind"],
                      provider_id=row["provider_id"], path=row["file_path"], result=result,
                      source="ai", actor=f"ai:{provider}")
        audit(ctx.db, actor=f"ai:{provider}", action="identity.ai_verdict",
              target_type=row["kind"], target_id=row["provider_id"],
              instance_id=row["instance_id"], old_value={"status": row["status"]},
              new_value={"status": result.status, "file": row["file_path"],
                         "confidence": conf}, reason=reason,
              undo_hint="Beoordeel de aflevering handmatig op de afleveringspagina")
        decided += 1
    return decided


# -- 4. bewezen verkeerde aflevering of film vervangen ---------------------------------------
async def replace_mismatches(ctx: AppContext, limit: int = 5) -> int:
    """Verkeerd bestand via MediaGuard's eigen quarantaine vervangen, ook voor films
    (verbeterlijst 12): eerst veiligstellen, dan pas uit Sonarr/Radarr halen en zoeken.
    Een prullenbak in Sonarr/Radarr is niet meer nodig."""
    from mediaguard.delivery import replace

    if emergency.skip(ctx, "verkeerde afleveringen vervangen"):
        return 0
    await asyncio.to_thread(replace.recover_interrupted, ctx)
    done = 0
    rows = _latest_rows(ctx, "r.status='mismatch' AND r.actionable=1 AND "
                             "r.kind IN ('episode_file','movie_file')", limit=limit * 4)
    for row in rows:
        if done >= limit or emergency.active(ctx):
            break
        if identity.keyed_by_episode_ids(ctx, row["instance_id"], row["kind"],
                                         row["provider_id"]):
            continue  # vóór de import beoordeeld: er staat (nog) geen bestand om te vervangen
        if not str(row["provider_id"]).isdigit():
            continue
        if replace.already_replaced(ctx, row["instance_id"], int(row["provider_id"])):
            continue
        try:
            res = await replace.replace_wrong_file(
                ctx, instance_id=row["instance_id"], kind=row["kind"],
                file_id=int(row["provider_id"]),
                reason_nl=str(row["reason_nl"] or "verkeerde aflevering/film"))
        except ArrError as exc:
            LOGGER.info("vervangen verkeerd bestand mislukt: %s", exc.message)
            continue
        if res["status"] == "replaced":
            done += 1
    return done


# -- 5. van TMDb/TVDB verwijderde titels ------------------------------------------------------
async def cleanup_removed(ctx: AppContext) -> int:
    from mediaguard.instances.registry import arr_client, list_instances

    if not bool(ctx.config.value("arr.cleanup_removed_titles")):
        return 0
    if emergency.skip(ctx, "verwijderde titels opruimen"):
        return 0
    removed = 0
    for inst in list_instances(ctx):
        if emergency.active(ctx):
            break
        if inst.suspended or inst.shadow_mode:
            continue
        try:
            async with arr_client(ctx, inst.id) as client:
                if inst.app == "radarr":
                    for m in await client.movie_list():
                        if str(m.get("status")).lower() != "deleted" or m.get("hasFile"):
                            continue
                        await client.delete(f"movie/{int(m['id'])}", deleteFiles="false",
                                            addImportExclusion="false")
                        removed += 1
                        _audit_removed(ctx, inst.id, "movie", m)
                else:
                    for s in await client.series_list():
                        files = int((s.get("statistics") or {}).get("episodeFileCount") or 0)
                        if str(s.get("status")).lower() != "deleted" or files:
                            continue
                        await client.delete(f"series/{int(s['id'])}", deleteFiles="false",
                                            addImportListExclusion="false")
                        removed += 1
                        _audit_removed(ctx, inst.id, "series", s)
        except ArrError as exc:
            LOGGER.info("opruimen verwijderde titels %s: %s", inst.id, exc.message)
    return removed


def _audit_removed(ctx: AppContext, instance_id: str, kind: str, item: dict[str, Any]) -> None:
    audit(ctx.db, actor="svc:cleanup", action="arr.removed_deleted_title", target_type=kind,
          target_id=str(item.get("id")), instance_id=instance_id,
          new_value={"title": item.get("title"), "year": item.get("year")},
          reason="Verwijderd bij de metadatabron (TMDb/TVDB) en zonder bestand")


# -- lus ---------------------------------------------------------------------------------------
BACKGROUND_IDENTITY_BATCH = 300


def background_identity(ctx: AppContext, limit: int = BACKGROUND_IDENTITY_BATCH) -> int:
    """Afleveringsbestanden zonder identiteitscontrole automatisch beoordelen (bestandsnaam,
    seizoenspakket, TheXEM-koppeling, handmatige beslissingen). Op de detailpagina stond
    anders 'unresolved — nog geen identiteitscontrole uitgevoerd' (rapport 4 nov, H14)."""
    files = ctx.db.fetch_dicts(
        "SELECT f.instance_id, f.provider_id, f.owner_provider_id, f.path, f.relative_path "
        "FROM lib_files f WHERE f.kind='episode' AND f.path IS NOT NULL AND NOT EXISTS ("
        "SELECT 1 FROM identity_results r WHERE r.instance_id=f.instance_id AND "
        "r.kind='episode_file' AND r.provider_id=CAST(f.provider_id AS TEXT)) "
        "ORDER BY f.id DESC LIMIT ?", (limit,))
    done = 0
    for f in files:
        eps = ctx.db.fetch_all(
            "SELECT season_number, episode_number, absolute_episode_number FROM lib_episodes "
            "WHERE instance_id=? AND episode_file_id=? ORDER BY season_number, episode_number",
            (f["instance_id"], f["provider_id"]))
        if not eps:
            continue
        series = ctx.db.fetch_one(
            "SELECT provider_id AS id, tvdb_id, tmdb_id, imdb_id FROM lib_items WHERE "
            "instance_id=? AND kind='series' AND provider_id=?",
            (f["instance_id"], f["owner_provider_id"]))
        expected = [EpisodeRef(int(e["season_number"]), int(e["episode_number"]),
                               e["absolute_episode_number"]) for e in eps]
        try:
            identity.evaluate(
                ctx, instance_id=f["instance_id"], kind="episode_file",
                provider_id=str(f["provider_id"]), expected=expected, path=f["path"],
                release_title=f["relative_path"], series=dict(series) if series else None,
                source="background", actor="svc:identity")
            done += 1
        except Exception:  # noqa: BLE001 - één bestand mag de rest niet tegenhouden
            LOGGER.exception("achtergrond-identiteit %s mislukt", f["path"])
    return done + background_movie_identity(ctx, max(0, limit - done))


def background_movie_identity(ctx: AppContext, limit: int = BACKGROUND_IDENTITY_BATCH) -> int:
    """Filmbestanden zonder identiteitscontrole: titel, jaar en TMDb-/IMDb-ID in de naam
    vergeleken met de film in Radarr. Twijfel loopt via dezelfde route als afleveringen
    (automatisch afsluiten bij zwak bewijs, AI bij echt conflict)."""
    if limit <= 0:
        return 0
    files = ctx.db.fetch_dicts(
        "SELECT f.instance_id, f.provider_id, f.path, f.relative_path, i.provider_id AS movie_id,"
        " i.title, i.year, i.tmdb_id, i.imdb_id FROM lib_files f JOIN lib_items i ON "
        "i.instance_id=f.instance_id AND i.kind='movie' AND i.provider_id=f.owner_provider_id "
        "WHERE f.kind='movie' AND f.path IS NOT NULL AND i.removed_at IS NULL AND NOT EXISTS ("
        "SELECT 1 FROM identity_results r WHERE r.instance_id=f.instance_id AND "
        "r.kind='movie_file' AND r.provider_id=CAST(f.provider_id AS TEXT)) "
        "ORDER BY f.id DESC LIMIT ?", (limit,))
    done = 0
    for f in files:
        movie = {"id": f["movie_id"], "title": f["title"], "year": f["year"],
                 "tmdb_id": f["tmdb_id"], "imdb_id": f["imdb_id"]}
        try:
            identity.evaluate(
                ctx, instance_id=f["instance_id"], kind="movie_file",
                provider_id=str(f["provider_id"]), expected=[], path=f["path"],
                release_title=f["relative_path"], movie=movie, source="background",
                actor="svc:identity")
            done += 1
        except Exception:  # noqa: BLE001 - één bestand mag de rest niet tegenhouden
            LOGGER.exception("achtergrond-identiteit film %s mislukt", f["path"])
    return done


async def run_once(ctx: AppContext) -> dict[str, int]:
    out = {"identity_background": await asyncio.to_thread(background_identity, ctx),
           "reevaluated": await asyncio.to_thread(reevaluate, ctx),
           "kept": await asyncio.to_thread(auto_keep_uncertain, ctx)}
    out["ai"] = await ai_tiebreak(ctx)
    out["replaced"] = await replace_mismatches(ctx)
    out["removed_titles"] = await cleanup_removed(ctx)
    return out


async def _loop(ctx: AppContext) -> None:
    await asyncio.sleep(120)
    while True:
        try:
            result = await run_once(ctx)
            ctx.state_set("automation", {**result, "at": utcnow()})
            taskwatch.ok(ctx, "automation")
        except Exception as exc:  # noqa: BLE001 - lus nooit laten sterven
            LOGGER.exception("automatisering mislukt")
            taskwatch.fail(ctx, "automation", exc)
        await asyncio.sleep(INTERVAL_SECONDS)


async def startup(ctx: AppContext) -> None:
    if ctx.settings.start_background and ctx.settings.role in ("core", "all"):
        ctx.services["automation_task"] = taskwatch.spawn(
            ctx, "automation", _loop, label_nl="Bestanden controleren en vervangen",
            every=INTERVAL_SECONDS)


async def shutdown(ctx: AppContext) -> None:
    task = ctx.services.pop("automation_task", None)
    if task is not None:
        task.cancel()
        with contextlib.suppress(asyncio.CancelledError):
            await task
