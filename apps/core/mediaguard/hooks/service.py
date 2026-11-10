"""Beslislogica voor pre-grab/pre-import en opslag van webhook-events.

Pre-grab/pre-import doen uitsluitend lokale DB-lookups (geen netwerk), zodat de fork
binnen het tijdsbudget een antwoord krijgt (contract: timeout + fail policy aan
fork-zijde). Alle beslissingen worden met duur gelogd in ``hook_decisions``.

Noodstop: pre-grab weigert alles; pre-import stelt upgrades uit (het huidige bestand blijft
staan), een import zonder bestaand bestand gaat gewoon door. Zoekpauzes per item
(``downloads.holds``) gelden ook voor grabs die Sonarr/Radarr zelf doen (RSS).
"""

from __future__ import annotations

import hashlib
import logging
import threading
import time
from collections import deque
from typing import TYPE_CHECKING, Any

from mediaguard import emergency, jobs
from mediaguard.audio import inventory
from mediaguard.audio.evaluate import AudioPolicy, evaluate_inventory, validate_claims
from mediaguard.db import dumps, utcnow
from mediaguard.identity import service as identity
from mediaguard.identity.engine import EpisodeRef
from mediaguard.identity.parser import parse_release
from mediaguard.policy import profiles
from mediaguard.policy.ports import instance_flags
from mediaguard.policy.ranker import Candidate, _title_norm, evaluate_candidate
from mediaguard.settings_registry import SettingDef

LOGGER = logging.getLogger(__name__)

if TYPE_CHECKING:
    from mediaguard.context import AppContext

CATEGORY = "Hooks & import"
HOOK_SCOPES = ("global", "media_type", "instance", "series", "movie")


def setting_defs() -> list[SettingDef]:
    return [
        SettingDef("hooks.pregrab_enabled", "bool", True, CATEGORY,
                   "Pre-grab-beleid toepassen", "Apply pre-grab policy",
                   help_nl="Uit = alles accepteren (noodstop/schorsing blijven wel gelden).",
                   scopes=HOOK_SCOPES),
        SettingDef("hooks.pregrab_identity_check", "bool", True, CATEGORY,
                   "Afleveringnummer in releasenaam controleren",
                   "Check episode number in release name",
                   help_nl="Weigert alleen bij een eenduidig verkeerd SxxEyy (niet bij anime/"
                           "scene-nummering).", scopes=HOOK_SCOPES),
        SettingDef("hooks.audio_score_step", "int", 1000, CATEGORY,
                   "Scorebijstelling per audiostap", "Score adjustment per audio tier",
                   help_nl="Bijv. anime: Dual Audio +2 stappen, Engels +1, Japans 0.",
                   minimum=0, maximum=100000, scopes=HOOK_SCOPES, advanced=True),
        SettingDef("hooks.preimport_defer_on_uncertain", "bool", False, CATEGORY,
                   "Import uitstellen bij onzekere identiteit",
                   "Defer import on uncertain identity",
                   help_nl="Uit: onzeker wordt geïmporteerd en gemarkeerd voor review.",
                   scopes=HOOK_SCOPES),
        SettingDef("hooks.preimport_enforce_audio", "bool", False, CATEGORY,
                   "Verplichte audiotaal afdwingen bij import",
                   "Enforce required audio language on import",
                   help_nl="Weigert import als het profiel NL-audio verplicht en mediainfo "
                           "aantoont dat die ontbreekt.", scopes=HOOK_SCOPES),
        SettingDef("hooks.preimport_cache_minutes", "int", 30, CATEGORY,
                   "Herhaalde importvraag onthouden (min)", "Remember repeated import check",
                   help_nl="Sonarr/Radarr vragen elke minuut opnieuw of een geblokkeerde "
                           "download mag worden geïmporteerd. MediaGuard onthoudt de beslissing "
                           "zolang, en vraagt opnieuw na een handmatig oordeel of als de tijd "
                           "om is. 0 = altijd opnieuw beoordelen.", minimum=0, maximum=1440,
                   unit="min", advanced=True),
        SettingDef("hooks.post_import_analysis", "bool", True, CATEGORY,
                   "Analyse na import (identiteit, audio, AV1)",
                   "Post-import analysis (identity, audio, AV1)", scopes=HOOK_SCOPES),
    ]


def _media_type(body: dict[str, Any], flags: dict[str, Any]) -> str:
    if flags.get("media_type"):
        return str(flags["media_type"])
    series = body.get("series") or {}
    if body.get("movie"):
        return "movie"
    if str(series.get("series_type") or series.get("seriesType") or "") == "anime":
        return "anime"
    return "tv"


def _chain(body: dict[str, Any], instance_id: str, flags: dict[str, Any]) -> dict[str, str]:
    series = body.get("series") or {}
    movie = body.get("movie") or {}
    episodes = body.get("episodes") or []
    season = episodes[0].get("season_number") if len(episodes) == 1 else None
    return profiles.build_chain(
        instance_id=instance_id, media_type=_media_type(body, flags),
        series_id=series.get("id"), movie_id=movie.get("id"), season=season,
        episode_id=episodes[0].get("id") if len(episodes) == 1 else None)


_LOG_SQL = ("INSERT INTO hook_decisions(ts,instance_id,hook,release_title,download_id,decision,"
            "reason_code,message,score_adjustment,duration_ms,details_json) "
            "VALUES (?,?,?,?,?,?,?,?,?,?,?)")
_pending_logs: deque[tuple[Any, tuple[Any, ...]]] = deque(maxlen=5000)
_writer_lock = threading.Lock()
_writer: threading.Thread | None = None


def _flush_pending() -> None:
    used: dict[int, Any] = {}
    try:
        while _pending_logs:
            db, params = _pending_logs.popleft()
            used[id(db)] = db
            try:
                db.execute(_LOG_SQL, params)
            except Exception:  # noqa: BLE001 - een logregel mag nooit iets breken
                LOGGER.warning("hook-beslissing niet vastgelegd", exc_info=True)
    finally:
        for db in used.values():
            db.close()  # sluit de verbinding van deze (kortlevende) thread


def _writer_loop() -> None:
    global _writer
    try:
        _flush_pending()
    finally:
        with _writer_lock:
            _writer = None
        if _pending_logs:
            _start_writer()


def _start_writer() -> None:
    global _writer
    with _writer_lock:
        if _writer is None:
            _writer = threading.Thread(target=_writer_loop, name="hook-log", daemon=True)
            _writer.start()


def flush_logs(timeout: float = 5.0) -> None:
    """Wacht tot uitgestelde logregels geschreven zijn (tests, afsluiten)."""
    deadline = time.monotonic() + timeout
    while (_pending_logs or _writer is not None) and time.monotonic() < deadline:
        time.sleep(0.01)


def _log(ctx: AppContext, *, instance_id: str, hook: str, title: str | None,
         download_id: str | None, result: dict[str, Any], started: float,
         details: dict[str, Any] | None = None) -> None:
    ms = (time.perf_counter() - started) * 1000
    result["duration_ms"] = round(ms, 2)
    params = (utcnow(), instance_id, hook, title, download_id, result["decision"],
              result.get("reason_code"), result.get("message"),
              int(result.get("score_adjustment", 0)), ms, dumps(details or {}))
    # Nooit op de schrijfvergrendeling wachten: een lange transactie elders (sync,
    # optimalisatie) zou het antwoord aan Sonarr/Radarr anders over de time-out duwen.
    if not _pending_logs and ctx.db.try_execute(_LOG_SQL, params):
        return
    _pending_logs.append((ctx.db, params))
    _start_writer()


# -- pre-import: zelfde vraag elke cyclus → één beslissing per download ------------------
_decision_cache: dict[tuple[Any, ...], tuple[float, dict[str, Any]]] = {}
_CACHE_MAX = 5000


def _cache_key(ctx: AppContext, instance_id: str, body: dict[str, Any]) -> tuple[Any, ...] | None:
    download_id = body.get("download_id")
    path = body.get("path")
    if not download_id and not path:
        return None
    eps = tuple(sorted(str(e.get("id")) for e in body.get("episodes") or []))
    movie = (body.get("movie") or {}).get("id")
    # een handmatig identiteitsoordeel maakt eerdere beslissingen ongeldig
    gen = ctx.db.fetch_one("SELECT MAX(id) AS g FROM identity_results WHERE instance_id=? "
                           "AND source='user'", (instance_id,))
    return (str(ctx.db.path), instance_id, download_id, path, eps, movie,
            gen["g"] if gen else None,
            bool(instance_flags(ctx, instance_id)["suspended"]),
            bool(body.get("is_upgrade")) and emergency.active(ctx))


def cached_decision(ctx: AppContext, key: tuple[Any, ...] | None) -> dict[str, Any] | None:
    if key is None:
        return None
    hit = _decision_cache.get(key)
    if hit is None:
        return None
    expires, result = hit
    if time.monotonic() >= expires:
        _decision_cache.pop(key, None)
        return None
    return {**result, "cached": True}


def remember_decision(ctx: AppContext, key: tuple[Any, ...] | None,
                      result: dict[str, Any]) -> None:
    if key is None or result.get("decision") == "accept":
        return  # alleen blokkades herhalen zich elke cyclus
    minutes = int(ctx.config.value("hooks.preimport_cache_minutes"))
    if minutes <= 0:
        return
    if len(_decision_cache) >= _CACHE_MAX:
        now = time.monotonic()
        for k in [k for k, (exp, _r) in _decision_cache.items() if exp <= now] or \
                list(_decision_cache)[: _CACHE_MAX // 10]:
            _decision_cache.pop(k, None)
    _decision_cache[key] = (time.monotonic() + minutes * 60, dict(result))


def clear_decision_cache() -> None:
    _decision_cache.clear()


def _refs(episodes: list[dict[str, Any]]) -> list[EpisodeRef]:
    return [EpisodeRef.from_dict(e) for e in episodes or []]


RECENT_GRAB_HOURS = 2


def _recent_same_grab(ctx: AppContext, instance_id: str, title: str,
                      indexer: Any) -> str | None:
    """Dezelfde release (titel, en indexer als bekend) in de laatste 2 uur al gegrabd voor
    deze instantie en niet mislukt? Dan geeft dit een korte omschrijving ('om 13:14')."""
    if not title:
        return None
    rows = ctx.db.fetch_all(
        "SELECT download_id, indexer, received_at FROM release_provenance WHERE "
        "instance_id=? AND event_type='Grab' AND lower(release_title)=lower(?) AND "
        "datetime(received_at) >= datetime('now', ?)",
        (instance_id, title, f"-{RECENT_GRAB_HOURS} hours"))
    if not rows:
        return None
    outcomes = ctx.state_get("download_outcomes", {}) or {}
    for r in rows:
        if indexer and r["indexer"] and str(r["indexer"]).lower() != str(indexer).lower():
            continue
        did = str(r["download_id"] or "")
        failed = ctx.db.fetch_one(
            "SELECT 1 FROM release_provenance WHERE instance_id=? AND download_id=? AND "
            "event_type IN ('DownloadFailed','DownloadIgnored')", (instance_id, did))
        if failed is not None or (outcomes.get(did) or {}).get("outcome") == "failed":
            continue
        stamp = str(r["received_at"] or "").replace("T", " ")[11:16]
        return f"om {stamp} (UTC)" if stamp else "kort geleden"
    return None


def pre_grab(ctx: AppContext, instance_id: str, body: dict[str, Any]) -> dict[str, Any]:
    started = time.perf_counter()
    release = body.get("release") or {}
    title = str(release.get("title") or "")

    def done(decision: str, code: str | None, message: str, adj: int = 0,
             details: dict[str, Any] | None = None) -> dict[str, Any]:
        result = {"decision": decision, "reason_code": code, "message": message,
                  "score_adjustment": adj}
        _log(ctx, instance_id=instance_id, hook="pre_grab", title=title, download_id=None,
             result=result, started=started, details=details)
        return result

    if emergency.active(ctx):
        return done("reject", emergency.REASON_CODE, "Noodstop actief: geen nieuwe downloads")
    flags = instance_flags(ctx, instance_id)
    if flags["suspended"] or flags["archived"]:
        return done("reject", "INSTANCE_SUSPENDED",
                    "Instantie is geschorst of gearchiveerd in MediaGuard")
    if flags["shadow_mode"]:
        return done("reject", "SHADOW", "Instantie draait in shadow-modus: alleen observeren")
    group = str(release.get("release_group") or "").strip()
    if group:
        from mediaguard.downloads.failures import unreliable_groups

        bad = unreliable_groups(ctx).get(group.lower())
        if bad is not None:  # rapport 6, N3: bv. een groep met veel grabs en bijna geen imports
            return done("reject", "GROUP_UNRELIABLE",
                        f"Releasegroep {bad['name']} importeert zelden ({bad['imported']} van "
                        f"{bad['grabs']} downloads in 30 dagen); voorlopig overgeslagen")
    from mediaguard.upgrade_churn import pre_grab_block

    churn = pre_grab_block(ctx, instance_id, body)
    if churn:  # rapport 8: hetzelfde item steeds opnieuw geüpgraded
        return done("reject", "UPGRADE_CHURN", churn)
    from mediaguard.downloads import holds

    paused = holds.grab_block(ctx, instance_id, body)
    if paused:  # zoekpauze geldt ook voor RSS en Sonarr/Radarr's eigen zoekacties
        return done("reject", holds.GRAB_BLOCK_CODE, paused)
    chain = _chain(body, instance_id, flags)
    if not ctx.config.value("hooks.pregrab_enabled", chain):
        return done("accept", None, "Pre-grab-beleid uitgeschakeld voor deze scope")

    eff = profiles.effective_policy(ctx, chain)
    cfg = eff["config"]
    expected = _refs(body.get("episodes") or []) \
        if ctx.config.value("hooks.pregrab_identity_check", chain) else []
    false_claims = set()
    row = ctx.db.fetch_one(
        "SELECT 1 FROM audio_claim_failures WHERE instance_id=? AND release_title_norm=?",
        (instance_id, _title_norm(title)))
    if row is not None:
        false_claims.add(_title_norm(title))
    cand = Candidate(title=title, size=release.get("size"),
                     resolution=release.get("resolution"), source=release.get("source"),
                     languages=list(release.get("languages") or []),
                     custom_format_score=release.get("custom_format_score"),
                     custom_formats=list(release.get("custom_formats") or []),
                     release_group=release.get("release_group"),
                     indexer=release.get("indexer"), guid=release.get("guid"))
    ranked = evaluate_candidate(cand, cfg, expected=expected or None,
                                false_claim_titles=false_claims,
                                is_anime=chain.get("media_type") in ("anime", "anime_movie"))
    details = {"profile_id": eff["profile_id"], "labels": ranked.labels,
               "audio": ranked.audio["tier"]}
    if ranked.rejected:
        first = ranked.rejections[0]
        return done("reject", first.code, first.message_nl, details=details)
    dup = _recent_same_grab(ctx, instance_id, title, release.get("indexer"))
    if dup is not None:  # rapport 8, B4: zelfde release binnen 8 min twee keer gegrabd
        return done("reject", "RECENTLY_GRABBED",
                    f"Deze release is {dup} al gedownload en loopt nog; niet opnieuw")
    step = int(ctx.config.value("hooks.audio_score_step", chain))
    tier_rank = list(ranked.keys["audio"])[-2] if len(ranked.keys["audio"]) >= 2 else 0
    adj = max(0, int(tier_rank) - 1) * step
    if cfg["audio"]["nl_audio"] == "preferred" and "nl" in ranked.audio["languages"]:
        adj += step
    msg = f"Toegestaan door profiel '{eff['profile_name_nl']}' ({ranked.labels['audio']})"
    if ranked.audio.get("keep_searching"):
        msg += "; fallback, MediaGuard blijft zoeken naar een betere audioversie"
    return done("accept", "AUDIO_PREFERENCE" if adj else None, msg, adj, details)


def pre_import(ctx: AppContext, instance_id: str, body: dict[str, Any]) -> dict[str, Any]:
    key = _cache_key(ctx, instance_id, body)
    hit = cached_decision(ctx, key)
    if hit is not None:
        return hit  # zelfde blokkade als vorige cyclus: niet opnieuw beoordelen of loggen
    result = _pre_import(ctx, instance_id, body)
    remember_decision(ctx, key, result)
    return result


def _pre_import(ctx: AppContext, instance_id: str, body: dict[str, Any]) -> dict[str, Any]:
    started = time.perf_counter()
    title = body.get("release_title")
    download_id = body.get("download_id")

    def done(decision: str, code: str | None, message: str,
             details: dict[str, Any] | None = None) -> dict[str, Any]:
        result = {"decision": decision, "reason_code": code, "message": message}
        _log(ctx, instance_id=instance_id, hook="pre_import", title=title,
             download_id=download_id, result=result, started=started, details=details)
        return result

    flags = instance_flags(ctx, instance_id)
    if flags["suspended"] or flags["archived"]:
        return done("defer", "INSTANCE_SUSPENDED", "Instantie geschorst: import uitgesteld")
    if body.get("is_upgrade") and emergency.active(ctx):
        # een upgrade vervangt het huidige bestand: tijdens de noodstop blijft dat staan
        return done("defer", emergency.REASON_CODE,
                    "Noodstop actief: upgrade wacht, het huidige bestand blijft staan")
    chain = _chain(body, instance_id, flags)
    movie = body.get("movie") or {}
    series = body.get("series") or {}
    details: dict[str, Any] = {}

    if movie:
        prov = ctx.db.fetch_one(
            "SELECT movie_id, release_title FROM release_provenance WHERE instance_id=? AND "
            "download_id=? AND event_type='Grab' ORDER BY id DESC LIMIT 1",
            (instance_id, download_id)) if download_id else None
        if prov is not None and prov["movie_id"] and movie.get("id") \
                and int(prov["movie_id"]) != int(movie["id"]):
            return done("reject", "IDENTITY_MISMATCH",
                        f"Download is gegrabd voor film-ID {prov['movie_id']}, niet "
                        f"{movie['id']}", {"grabbed_movie_id": prov["movie_id"]})
    else:
        episodes = body.get("episodes") or []
        result = identity.evaluate(
            ctx, instance_id=instance_id, kind="episode_file",
            provider_id=",".join(str(e.get("id")) for e in episodes) or body.get("path", "?"),
            expected=_refs(episodes), path=body.get("path"), release_title=title,
            download_id=download_id, series=series, source="pre_import", actor="hook")
        details["identity"] = result.status
        if result.status == "mismatch":
            if result.actionable:
                return done("reject", "IDENTITY_MISMATCH", result.reason_nl, details)
            return done("defer", "IDENTITY_REVIEW", result.reason_nl, details)
        if result.status == "uncertain" and ctx.config.value(
                "hooks.preimport_defer_on_uncertain", chain):
            return done("defer", "IDENTITY_UNCERTAIN", result.reason_nl, details)

    media_info = body.get("media_info") or {}
    if media_info:
        eff = profiles.effective_policy(ctx, chain)
        inv = inventory.from_arr_media_info(media_info)
        verdict = evaluate_inventory(inv, AudioPolicy.from_dict(eff["config"]["audio"]))
        details["audio"] = verdict.tier
        if title:
            parsed = parse_release(title)
            for check in validate_claims(dual_audio=parsed.dual_audio,
                                         claimed_audio=parsed.audio_language_claims, inv=inv):
                if check.valid is False:
                    record_false_claim(ctx, instance_id, title, parsed.release_group,
                                       check.claim, inv.known_main_audio_languages)
                    details.setdefault("false_claims", []).append(check.reason_nl)
        if (not verdict.acceptable and eff["config"]["audio"]["nl_audio"] == "required"
                and inv.known_main_audio_languages
                and ctx.config.value("hooks.preimport_enforce_audio", chain)):
            return done("reject", "AUDIO_REQUIREMENT",
                        "Nederlandse audio verplicht, niet aanwezig in bestand", details)
    return done("accept", None, "Import toegestaan", details)


def record_false_claim(ctx: AppContext, instance_id: str, title: str, group: str | None,
                       claim: str, observed: list[str]) -> None:
    ctx.db.execute(
        "INSERT OR IGNORE INTO audio_claim_failures(instance_id,release_title_norm,"
        "release_group,claim,observed_languages,detected_at) VALUES (?,?,?,?,?,?)",
        (instance_id, _title_norm(title), group, claim, ",".join(observed), utcnow()))


# ── webhook ───────────────────────────────────────────────────────────────────
IMPORT_EVENTS = {"Download", "Upgrade"}


def _episodes(payload: dict[str, Any]) -> list[dict[str, Any]]:
    out = []
    for e in payload.get("episodes") or []:
        out.append({"id": e.get("id"), "season_number": e.get("seasonNumber"),
                    "episode_number": e.get("episodeNumber"),
                    "absolute_episode_number": e.get("absoluteEpisodeNumber")})
    return out


def store_webhook(ctx: AppContext, instance_id: str, payload: dict[str, Any]) -> dict[str, Any]:
    event = str(payload.get("eventType") or "Unknown")[:64]
    if event == "Test":
        # niet opslaan als import-event, wel zichtbaar maken (UI: 'Webhook testen' slaagt
        # pas aantoonbaar als het testbericht bij core is aangekomen)
        ctx.state_set(f"webhook_test:{instance_id}", {"received_at": utcnow()})
        ctx.events.publish("arr.webhook", {"event_type": "Test"}, instance_id=instance_id)
        return {"status": "ok", "event_type": "Test"}
    is_upgrade = bool(payload.get("isUpgrade"))
    if event == "Download" and is_upgrade:
        event = "Upgrade"
    download_id = payload.get("downloadId")
    series = payload.get("series") or {}
    movie = payload.get("movie") or {}
    file = payload.get("episodeFile") or payload.get("movieFile") or {}
    release = payload.get("release") or {}
    episodes = _episodes(payload)
    episode_ids = sorted(int(e["id"]) for e in episodes if e.get("id") is not None)
    file_key = str(file.get("id") or file.get("path") or file.get("relativePath") or "")
    # Download en Upgrade van hetzelfde bestand zijn één import (punt 39: dubbele regels in
    # Recent en een tweede analyse-job)
    family = "Download" if event in IMPORT_EVENTS else event
    if download_id or file_key or episode_ids:
        basis = f"{family}|{download_id or ''}|{file_key}|{episode_ids}"
    else:
        basis = f"{family}|" + dumps(payload)
    key = hashlib.sha256(basis.encode()).hexdigest()
    now = utcnow()
    with ctx.db.transaction() as conn:
        if event in IMPORT_EVENTS and download_id and not file_key:
            # Sonarr's 'Import voltooid' (eventType Download met episodeFiles, zonder
            # episodeFile) vat een import samen die per bestand al binnenkwam
            prior = conn.execute(
                "SELECT id, analysis_job_id FROM import_events WHERE instance_id=? AND "
                "series_id IS ? AND movie_id IS ? AND download_id=? AND event_type IN "
                "('Download','Upgrade') ORDER BY id DESC LIMIT 1",
                (instance_id, series.get("id"), movie.get("id"), download_id)).fetchone()
            if prior is not None:
                return {"status": "duplicate", "event_id": prior["id"],
                        "job_id": prior["analysis_job_id"], "event_type": event}
        cur = conn.execute(
            "INSERT OR IGNORE INTO import_events(instance_id,event_type,is_upgrade,download_id,"
            "series_id,movie_id,file_id,file_path,idempotency_key,payload_json,received_at) "
            "VALUES (?,?,?,?,?,?,?,?,?,?,?)",
            (instance_id, event, int(is_upgrade), download_id, series.get("id"), movie.get("id"),
             file.get("id"), file.get("path"), key, dumps(payload), now))
        if cur.rowcount == 0:
            row = conn.execute("SELECT id, analysis_job_id FROM import_events WHERE "
                               "instance_id=? AND idempotency_key=?", (instance_id, key)).fetchone()
            return {"status": "duplicate", "event_id": row["id"],
                    "job_id": row["analysis_job_id"], "event_type": event}
        event_id = int(cur.lastrowid or 0)
        if event in ("Grab", "Download", "Upgrade"):
            title = release.get("releaseTitle") or file.get("sceneName") or payload.get(
                "release", {}).get("title")
            quality = release.get("quality") or (file.get("quality") or {})
            if isinstance(quality, dict):
                quality = (quality.get("quality") or {}).get("name") or quality.get("name")
            conn.execute(
                "INSERT OR IGNORE INTO release_provenance(instance_id,event_type,download_id,"
                "release_title,release_group,quality,custom_format_score,custom_formats_json,"
                "indexer,size,languages_json,series_id,movie_id,episode_ids_json,episodes_json,"
                "file_id,file_path,idempotency_key,received_at) "
                "VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
                (instance_id, event, download_id, title,
                 release.get("releaseGroup") or file.get("releaseGroup"), quality,
                 release.get("customFormatScore", payload.get("customFormatInfo", {}).get(
                     "customFormatScore")),
                 dumps(release.get("customFormats") or [cf.get("name") for cf in (
                     payload.get("customFormatInfo") or {}).get("customFormats") or []]),
                 release.get("indexer"), release.get("size") or file.get("size"),
                 dumps([lang.get("name") if isinstance(lang, dict) else lang
                        for lang in (file.get("languages") or release.get("languages") or [])]),
                 series.get("id"), movie.get("id"), dumps(episode_ids), dumps(episodes),
                 file.get("id"), file.get("path"), key, now))
    job_id = None
    if event in IMPORT_EVENTS:
        chain = profiles.build_chain(instance_id=instance_id, series_id=series.get("id"),
                                     movie_id=movie.get("id"))
        if ctx.config.value("hooks.post_import_analysis", chain):
            media_info = (file.get("mediaInfo") or {})
            job_id = jobs.enqueue(
                ctx.db, "analysis.post_import",
                {"instance_id": instance_id, "event_id": event_id,
                 "kind": "movie_file" if movie else "episode_file",
                 "provider_id": file.get("id"), "file_path": file.get("path"),
                 "release_title": release.get("releaseTitle") or file.get("sceneName"),
                 "download_id": download_id,
                 "series": {"id": series.get("id"), "tvdb_id": series.get("tvdbId"),
                            "imdb_id": series.get("imdbId"),
                            "series_type": series.get("type") or series.get("seriesType")}
                 if series else None,
                 "movie": {"id": movie.get("id"), "tmdb_id": movie.get("tmdbId"),
                           "imdb_id": movie.get("imdbId"), "title": movie.get("title"),
                           "year": movie.get("year")}
                 if movie else None,
                 "episodes": episodes, "media_info": media_info},
                instance_id=instance_id, target_type="movie_file" if movie else "episode_file",
                target_id=file.get("id"), idempotency_key=f"pia:{instance_id}:{key}",
                created_by=f"hook:{instance_id}")
            ctx.db.execute("UPDATE import_events SET analysis_job_id=? WHERE id=?",
                           (job_id, event_id))
            runner = ctx.services.get("jobs")
            if runner is not None:
                runner.wake()
    if event in ("SeriesAdd", "MovieAdded") and (series.get("id") or movie.get("id")):
        # anime die via Seerr in de gewone instantie belandt → anime-instantie
        from mediaguard.routing.api import enqueue_check

        enqueue_check(ctx, instance_id, "series" if series else "movie",
                      int(series.get("id") or movie.get("id")))
    ctx.events.publish("arr.webhook", {"event_type": event, "event_id": event_id,
                                       "download_id": download_id}, instance_id=instance_id)
    return {"status": "stored", "event_id": event_id, "job_id": job_id, "event_type": event}
