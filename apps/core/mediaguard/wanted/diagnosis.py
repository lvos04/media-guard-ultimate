"""Root Cause Engine (D2): concrete oorzaak per ontbrekend item, met regel, tijdstip,
bewijs (historie, release-afwijzingen, indexer-/wachtrijstatus) en voorgestelde fix.

Geen generieke 'niet gevonden': iedere reden heeft een stabiele code. Volgorde van
``PRIORITY`` bepaalt de primaire oorzaak; alle bijdragende oorzaken worden getoond.
"""

from __future__ import annotations

import contextlib
import logging
from datetime import UTC, datetime, timedelta
from typing import TYPE_CHECKING, Any

from mediaguard.arr.client import ArrError, ArrNotFound, records_of
from mediaguard.db import dumps, loads, utcnow
from mediaguard.instances.registry import arr_client, instance_row
from mediaguard.library.sync import iso_z, now_z
from mediaguard.wanted.engine import get_wanted, settings_scope_chain

if TYPE_CHECKING:
    from mediaguard.arr.client import ArrClient
    from mediaguard.context import AppContext

LOGGER = logging.getLogger(__name__)

REASONS: dict[str, dict[str, Any]] = {
    "intentional-exception": {
        "label_nl": "Bewuste uitzondering",
        "fix_nl": "Niets te doen. Kies 'Opnieuw volgen' als dit item toch gezocht moet worden."},
    "unmonitored": {
        "label_nl": "Niet gemonitord",
        "fix_nl": "Zet monitoring aan voor serie/seizoen/aflevering als je dit wilt hebben."},
    "not-yet-aired": {
        "label_nl": "Nog niet uitgezonden / nog niet beschikbaar",
        "fix_nl": "Wacht tot de uitzend-/releasedatum. Controleer bij twijfel de metadata "
                  "(Vernieuwen)."},
    "instance-suspended": {
        "label_nl": "Instantie gepauzeerd",
        "fix_nl": "Hervat de instantie (Instellingen → Instanties) om zoeken weer toe te staan."},
    "root-folder-missing": {
        "label_nl": "Rootmap ontbreekt of is niet bereikbaar",
        "fix_nl": "Controleer de dataset/mount en rechten van de rootmap, of wijs een "
                  "bestaande rootmap toe."},
    "file-not-linked": {
        "label_nl": "Bestand aanwezig maar niet gekoppeld",
        "fix_nl": "Start 'Handmatig importeren' of 'Herscannen' voor deze serie/film."},
    "import-failed": {
        "label_nl": "Download voltooid, import mislukt",
        "fix_nl": "Bekijk de importmelding; controleer remote path mappings, rechten en de "
                  "episode-/filmkoppeling."},
    "download-client-failed": {
        "label_nl": "Downloadclient-fout",
        "fix_nl": "Controleer de downloadclient (bereikbaarheid, schijfruimte, categorie) en "
                  "zoek daarna opnieuw."},
    "stuck-queue": {
        "label_nl": "Wachtrij vastgelopen",
        "fix_nl": "Verwijder het item uit de wachtrij (eventueel met blocklist) en zoek opnieuw."},
    "downloading": {
        "label_nl": "Wordt gedownload",
        "fix_nl": "Geen actie nodig; wacht op voltooiing en import."},
    "download-client-missing": {
        "label_nl": "Geen (geschikte) downloadclient",
        "fix_nl": "Voeg een downloadclient toe of schakel er een in voor het protocol van de "
                  "gevonden releases."},
    "no-indexers": {
        "label_nl": "Geen bruikbare indexer",
        "fix_nl": "Schakel automatisch zoeken in op een indexer, of pas de indexer-tags aan "
                  "zodat ze voor deze titel gelden."},
    "indexer-auth": {
        "label_nl": "Indexer-authenticatie mislukt",
        "fix_nl": "Vernieuw de API-sleutel/inloggegevens van de indexer en test opnieuw."},
    "indexer-rate-limit": {
        "label_nl": "Indexer-limiet bereikt",
        "fix_nl": "Wacht tot de limiet reset; verlaag eventueel de backfill-quota voor deze "
                  "instantie."},
    "indexer-offline": {
        "label_nl": "Indexer offline of tijdelijk uitgeschakeld",
        "fix_nl": "Controleer de indexer (Test). Sonarr/Radarr schakelt hem automatisch weer in "
                  "na de wachttijd."},
    "incorrect-mapping": {
        "label_nl": "Verkeerde koppeling/nummering",
        "fix_nl": "Controleer scene-/absolute nummering (anime: TheXEM/AniDB) en de "
                  "serie-ID; vernieuw daarna de serie."},
    "rejected-quality": {
        "label_nl": "Afgewezen: kwaliteit",
        "fix_nl": "Releases bestaan maar in een kwaliteit die het profiel niet toestaat. Pas "
                  "het kwaliteitsprofiel aan of gebruik 'Opnieuw met versoepelde regels'."},
    "rejected-language": {
        "label_nl": "Afgewezen: taal",
        "fix_nl": "Alleen releases in een niet-toegestane taal. Pas taalbeleid aan of wacht "
                  "op een geschikte release."},
    "rejected-custom-format": {
        "label_nl": "Afgewezen: custom format-score",
        "fix_nl": "Releases halen de minimale custom format-score niet. Controleer de scores "
                  "(TRaSH) of versoepel expliciet."},
    "rejected-size": {
        "label_nl": "Afgewezen: grootte",
        "fix_nl": "Releases vallen buiten de min/max-grootte van de kwaliteitsdefinitie."},
    "rejected-other": {
        "label_nl": "Afgewezen: overige regel",
        "fix_nl": "Zie de afwijzingsregels (retentie, seeders, release profile, blocklist, "
                  "MediaGuard-beleid)."},
    "release-available": {
        "label_nl": "Geschikte release beschikbaar",
        "fix_nl": "Er is een goedgekeurde release; 'Zoek nu' zou die moeten grabben "
                  "(controleer anders het delay profile)."},
    "no-indexer-hit": {
        "label_nl": "Geen release gevonden op indexers",
        "fix_nl": "Er bestaat (nog) geen release. Backfill probeert het later opnieuw met "
                  "oplopende tussenpozen; niet alles is te vinden."},
    "searched-no-grab": {
        "label_nl": "Gezocht zonder resultaat",
        "fix_nl": "Automatisch zoeken leverde niets op. MediaGuard zoekt later vanzelf opnieuw "
                  "met oplopende tussenpozen; je hoeft niets te doen."},
    "not-searched-yet": {
        "label_nl": "Nog niet gezocht",
        "fix_nl": "Kies 'Zoek nu' of wacht op de volgende backfillronde."},
    "unknown": {
        "label_nl": "Oorzaak onbekend",
        "fix_nl": "MediaGuard onderzoekt dit vanzelf opnieuw bij de volgende zoekronde."},
    "niet-gediagnosticeerd": {
        "label_nl": "Nog niet onderzocht",
        "fix_nl": "MediaGuard onderzoekt deze items automatisch op de achtergrond (een portie "
                  "per kwartier). Je hoeft niets te doen."},
}
# Oorzaken die MediaGuard zelf afhandelt (backfill, achtergronddiagnose, wachten op release):
# horen niet in 'Actie nodig' (rapport 4 nov, H6). De rest vraagt iets van de gebruiker.
AUTOMATIC_REASONS = {"intentional-exception", "unmonitored", "not-yet-aired", "downloading",
                     "no-indexer-hit", "searched-no-grab", "not-searched-yet", "unknown",
                     "release-available", "indexer-rate-limit", "indexer-offline",
                     "niet-gediagnosticeerd"}
# Waar je het oplost (UI-route). Ontbreekt een code, dan de lijst Ontbrekend met dat filter.
FIX_HREF: dict[str, str] = {
    "instance-suspended": "/instellingen/instanties",
    "root-folder-missing": "/instellingen?tab=opslag",
    "file-not-linked": "/activiteit?tab=problemen",
    "import-failed": "/activiteit?tab=problemen",
    "download-client-failed": "/inrichten?tab=downloads",
    "stuck-queue": "/downloads",
    "download-client-missing": "/inrichten?tab=downloads",
    "no-indexers": "/inrichten?tab=indexers",
    "indexer-auth": "/inrichten?tab=indexers",
    "incorrect-mapping": "/activiteit?tab=problemen",
    "rejected-quality": "/kwaliteit",
    "rejected-language": "/kwaliteit",
    "rejected-custom-format": "/kwaliteit",
    "rejected-size": "/kwaliteit",
}
for _code, _meta in REASONS.items():
    _meta["code"] = _code
    _meta["needs_user"] = _code not in AUTOMATIC_REASONS
    _meta["automatic"] = _code in AUTOMATIC_REASONS
    _meta["href"] = FIX_HREF.get(_code, f"/ontbrekend?reason_code={_code}")

PRIORITY = [
    "intentional-exception", "unmonitored", "not-yet-aired", "instance-suspended",
    "root-folder-missing", "file-not-linked", "import-failed", "download-client-failed",
    "stuck-queue", "downloading", "download-client-missing", "indexer-auth",
    "indexer-rate-limit", "indexer-offline", "no-indexers", "incorrect-mapping",
    "release-available", "rejected-language", "rejected-custom-format", "rejected-quality",
    "rejected-size", "rejected-other", "no-indexer-hit", "searched-no-grab",
    "not-searched-yet", "unknown",
]

# Afwijzingstekst (Sonarr/Radarr DownloadRejection) → categorie. Volgorde is belangrijk.
_REJECTION_RULES: list[tuple[str, tuple[str, ...]]] = [
    ("rejected-other", ("mediaguard",)),
    ("incorrect-mapping", ("unknown series", "unknown movie", "wasn't requested",
                           "was not requested", "not requested", "unable to parse",
                           "unable to identify", "wrong season", "wrong episode",
                           "does not match", "doesn't match", "mapped to", "scene mapping",
                           "absolute episode", "episode not found", "invalid season",
                           "wrong movie")),
    ("not-yet-aired", ("not available", "considered available", "minimum availability",
                       "has not aired", "not yet aired")),
    ("rejected-other", ("in queue", "blocklist", "blacklist")),
    ("rejected-language", ("language", "is wanted, but found")),
    ("rejected-custom-format", ("custom format", "format score", "customformat")),
    ("rejected-size", ("smaller than", "larger than", "minimum allowed", "maximum allowed",
                       "size", "sample")),
    ("rejected-quality", ("not wanted in profile", "quality", "cutoff", "not an upgrade",
                          "upgrade", "existing file", "equal or higher preference",
                          "proper", "repack")),
    ("rejected-other", ("retention", "older than", "seeders", "blocklist", "blacklist",
                        "restricted term", "required term", "release profile", "delay",
                        "in queue", "already", "torrent", "usenet", "protocol", "indexer")),
]

RELAXABLE = {"rejected-quality": "quality", "rejected-custom-format": "custom-format",
             "rejected-size": "size", "rejected-language": "language"}


def classify_rejection(text: str) -> str:
    lower = text.lower()
    for code, needles in _REJECTION_RULES:
        if any(n in lower for n in needles):
            return code
    return "rejected-other"


def _rejection_texts(release: dict[str, Any]) -> list[str]:
    out = []
    for rej in release.get("rejections") or []:
        if isinstance(rej, str):
            out.append(rej)
        elif isinstance(rej, dict):
            out.append(str(rej.get("reason") or rej.get("message") or rej.get("type") or rej))
    return out


def summarize_release(release: dict[str, Any]) -> dict[str, Any]:
    quality = (release.get("quality") or {}).get("quality") or {}
    langs = release.get("languages")
    if isinstance(langs, list):
        languages = [str(lang.get("name")) for lang in langs if isinstance(lang, dict)]
    elif isinstance(release.get("language"), dict):
        languages = [str(release["language"].get("name"))]
    else:
        languages = []
    rejections = [{"text": t, "code": classify_rejection(t)} for t in _rejection_texts(release)]
    return {
        "title": release.get("title"), "guid": release.get("guid"),
        "indexer": release.get("indexer"), "indexer_id": release.get("indexerId"),
        "protocol": release.get("protocol"), "size": release.get("size"),
        "age_hours": release.get("ageHours"), "seeders": release.get("seeders"),
        "quality": quality.get("name"), "languages": languages,
        "custom_format_score": release.get("customFormatScore"),
        "release_group": release.get("releaseGroup"),
        "approved": bool(release.get("approved")),
        "temporarily_rejected": bool(release.get("temporarilyRejected")),
        "rejected": bool(release.get("rejected")) or bool(rejections),
        "download_allowed": release.get("downloadAllowed", True),
        "rejections": rejections,
    }


def candidate_category(candidate: dict[str, Any]) -> str | None:
    """Belangrijkste afwijzingscategorie van één kandidaat (None = goedgekeurd)."""
    if candidate.get("approved") and not candidate.get("rejections"):
        return None
    codes = [r["code"] for r in candidate.get("rejections", [])]
    if not codes:
        return "rejected-other" if candidate.get("rejected") else None
    for code in PRIORITY:
        if code in codes:
            return code
    return codes[0]


class _Collector:
    def __init__(self) -> None:
        self.reasons: dict[str, dict[str, Any]] = {}

    def add(self, code: str, rule: str, message: str, *, ts: str | None = None,
            evidence: Any = None, severity: str = "error", confidence: str = "high") -> None:
        if code in self.reasons:
            return
        meta = REASONS[code]
        self.reasons[code] = {
            "code": code, "label_nl": meta["label_nl"], "rule": rule,
            "ts": ts or utcnow(), "message_nl": message, "suggested_fix_nl": meta["fix_nl"],
            "evidence": evidence, "severity": severity, "confidence": confidence,
        }

    def ordered(self) -> list[dict[str, Any]]:
        return [self.reasons[c] for c in PRIORITY if c in self.reasons]


_AUTH = ("401", "403", "unauthorized", "forbidden", "api key", "apikey", "invalid key",
         "authentication", "credentials", "login", "passkey")
_RATE = ("429", "rate limit", "ratelimit", "too many requests", "request limit", "query limit",
         "grab limit", "api limit", "limit reached", "daily limit")


async def _safe(errors: list[dict[str, Any]], label: str, coro: Any, default: Any) -> Any:
    try:
        return await coro
    except ArrError as exc:
        errors.append({"source": label, **exc.to_dict()})
        return default
    except ValueError as exc:
        errors.append({"source": label, "message": str(exc)})
        return default


def _clean_provider(p: dict[str, Any]) -> dict[str, Any]:
    keep = ("id", "name", "implementation", "protocol", "enable", "enableRss",
            "enableAutomaticSearch", "enableInteractiveSearch", "priority", "tags")
    return {k: p.get(k) for k in keep if k in p}


async def diagnose(ctx: AppContext, wanted_id: int, *, interactive: bool = False,
                   trigger: str = "manual", persist: bool = True) -> dict[str, Any]:
    row = get_wanted(ctx, wanted_id)
    if row is None:
        raise KeyError(wanted_id)
    iid = row["instance_id"]
    inst = instance_row(ctx, iid) or {}
    errors: list[dict[str, Any]] = []
    evidence: dict[str, Any] = {"errors": errors}
    c = _Collector()
    now = now_z()
    is_episode = row["kind"] == "episode"
    pid = int(row["provider_id"])
    item_pid = int(row["item_provider_id"])

    # -- 1. cache + live itemstatus ---------------------------------------------------------
    item = ctx.db.fetch_one("SELECT * FROM lib_items WHERE instance_id=? AND kind=? AND "
                            "provider_id=?", (iid, "series" if is_episode else "movie", item_pid))
    item = dict(item) if item else {}
    episode: dict[str, Any] = {}
    if is_episode:
        ep = ctx.db.fetch_one("SELECT * FROM lib_episodes WHERE instance_id=? AND provider_id=?",
                              (iid, pid))
        episode = dict(ep) if ep else {}
    async with arr_client(ctx, iid) as client:
        if is_episode:
            live = await _safe(errors, "episode", client.episode_get(pid), None)
            if live:
                episode.update(monitored=int(bool(live.get("monitored"))),
                               has_file=int(bool(live.get("hasFile"))),
                               air_date_utc=iso_z(live.get("airDateUtc")),
                               unverified_scene_numbering=int(bool(
                                   live.get("unverifiedSceneNumbering"))),
                               absolute_episode_number=live.get("absoluteEpisodeNumber"))
            live_item = await _safe(errors, "series", client.series_get(item_pid), None)
        else:
            live_item = await _safe(errors, "movie", client.movie_get(pid), None)
        if live_item:
            item.update(monitored=int(bool(live_item.get("monitored"))),
                        root_folder_path=live_item.get("rootFolderPath") or
                        item.get("root_folder_path"),
                        path=live_item.get("path") or item.get("path"),
                        series_type=live_item.get("seriesType", item.get("series_type")),
                        tags_json=dumps(live_item.get("tags") or []),
                        is_available=int(bool(live_item.get("isAvailable",
                                                            item.get("is_available")))),
                        has_file=int(bool(live_item.get("hasFile", item.get("has_file")))))
        evidence["item"] = {k: item.get(k) for k in (
            "title", "monitored", "root_folder_path", "path", "series_type", "tvdb_id",
            "tmdb_id", "imdb_id", "quality_profile_id", "is_available", "has_file")}
        if is_episode:
            evidence["episode"] = {k: episode.get(k) for k in (
                "season_number", "episode_number", "absolute_episode_number", "air_date_utc",
                "monitored", "has_file", "unverified_scene_numbering")}

        # -- 2. bewuste uitzonderingen / monitoring / uitzenddatum -----------------------------
        if row["status"] == "ignored":
            c.add("intentional-exception", "mediaguard.wanted.ignore",
                  "Door gebruiker genegeerd" + (f": {row['ignored_reason']}"
                                                if row.get("ignored_reason") else "") + ".",
                  ts=row["updated_at"])
        if ctx.config.value("backfill.excluded", settings_scope_chain(row)):
            c.add("intentional-exception", "setting:backfill.excluded",
                  "Uitgesloten van backfill via instelling (serie/seizoen/aflevering).")
        monitored_ok = bool(item.get("monitored")) and (not is_episode or
                                                        bool(episode.get("monitored")))
        if not monitored_ok:
            which = "serie" if not item.get("monitored") else "aflevering"
            if not is_episode:
                which = "film"
            c.add("unmonitored", f"{inst.get('app')}.monitored",
                  f"De {which} is niet gemonitord in {iid}; telt niet als ontbrekend.")
        if is_episode:
            air = episode.get("air_date_utc")
            if not air or air > now:
                c.add("not-yet-aired", "sonarr.airDateUtc",
                      "Nog geen uitzenddatum bekend." if not air else
                      f"Uitzending gepland op {air}.", ts=air)
        elif not item.get("is_available"):
            c.add("not-yet-aired", "radarr.minimumAvailability",
                  "Film is volgens Radarr nog niet beschikbaar (minimum availability).")
        if (is_episode and episode.get("has_file")) or (not is_episode and item.get("has_file")
                                                       and row["reason"] == "missing"):
            evidence["note"] = "Bestand is inmiddels aanwezig; item wordt bij de volgende " \
                               "synchronisatie opgelost."
        if inst.get("suspended"):
            c.add("instance-suspended", "mediaguard.instance.suspended",
                  f"Instantie {iid} is gepauzeerd: RSS en automatisch zoeken staan uit.",
                  ts=inst.get("suspended_at"))

        # -- 3. live bewijs ophalen --------------------------------------------------------
        roots = await _safe(errors, "rootfolder", client.root_folders(), [])
        queue = await _safe(errors, "queue", client.queue(all_pages=True), [])
        if is_episode:
            history = await _safe(errors, "history", client.history_for_episode(pid, 30), [])
        else:
            history = await _safe(errors, "history", client.history_for_movie(pid), [])
        indexers = await _safe(errors, "indexer", client.indexers(), [])
        idx_status = await _safe(errors, "indexerstatus", client.indexer_status(), [])
        health = await _safe(errors, "health", client.health(), [])
        clients = await _safe(errors, "downloadclient", client.download_clients(), [])
        candidates: list[dict[str, Any]] = []
        candidates_source = None
        if interactive:
            releases = await _safe(
                errors, "release",
                client.releases(episode_id=pid) if is_episode else client.releases(movie_id=pid),
                None)
            if releases is not None:
                candidates = [summarize_release(r) for r in releases]
                candidates_source = "interactive"
                if persist:
                    _record_candidates(ctx, row, candidates, trigger)
        else:
            last = ctx.db.fetch_one(
                "SELECT ts, candidates_json FROM backfill_attempts WHERE wanted_id=? AND "
                "status='candidates' ORDER BY id DESC LIMIT 1", (wanted_id,))
            if last is not None:
                candidates = loads(last["candidates_json"], [])
                candidates_source = f"opgeslagen ({last['ts']})"
        manual: list[dict[str, Any]] = []
        folder = item.get("path")
        if folder and not (is_episode and episode.get("has_file")):
            found = await _safe(
                errors, "manualimport",
                client.manual_import_candidates(folder, series_id=item_pid if is_episode
                                                else None, movie_id=None if is_episode
                                                else pid), [])
            for entry in found or []:
                if is_episode:
                    ids = {int(e.get("id", 0)) for e in entry.get("episodes") or []}
                    if pid in ids:
                        manual.append(entry)
                elif (entry.get("movie") or {}).get("id") in (pid, None):
                    manual.append(entry)

    queue_items = [q for q in records_of(queue) if
                   (is_episode and int(q.get("episodeId") or 0) == pid) or
                   (not is_episode and int(q.get("movieId") or 0) == pid)]
    evidence.update(
        queue=[{k: q.get(k) for k in ("id", "title", "status", "trackedDownloadStatus",
                                      "trackedDownloadState", "statusMessages", "errorMessage",
                                      "downloadClient", "protocol", "size", "sizeleft",
                                      "timeleft", "added", "indexer")} for q in queue_items],
        history=[{k: h.get(k) for k in ("id", "date", "eventType", "sourceTitle", "data",
                                        "downloadId")} for h in history[:20]],
        indexers=[_clean_provider(i) for i in indexers],
        indexer_status=idx_status, health=health,
        download_clients=[_clean_provider(d) for d in clients],
        root_folders=[{k: r.get(k) for k in ("id", "path", "accessible", "freeSpace")}
                      for r in roots],
        candidates=candidates, candidates_source=candidates_source,
        manual_import=[{"path": m.get("path"), "rejections": m.get("rejections"),
                        "quality": ((m.get("quality") or {}).get("quality") or {}).get("name")}
                       for m in manual],
    )

    # -- 4. rootmap ------------------------------------------------------------------------------
    root = (item.get("root_folder_path") or "").rstrip("/")
    if root and "rootfolder" not in {e.get("source") for e in errors}:
        match = next((r for r in roots if str(r.get("path", "")).rstrip("/") == root), None)
        if match is None:
            c.add("root-folder-missing", "arr.rootfolder",
                  f"Rootmap {root} bestaat niet (meer) in {iid}.")
        elif match.get("accessible") is False:
            c.add("root-folder-missing", "arr.rootfolder.accessible",
                  f"Rootmap {root} is niet toegankelijk (mount/rechten).")
    for h in health:
        if str(h.get("source", "")).startswith("RootFolder") and root and root in str(
                h.get("message", "")):
            c.add("root-folder-missing", f"health:{h.get('source')}", str(h.get("message")))

    # -- 5. bestand aanwezig maar niet gekoppeld -------------------------------------------------
    if manual:
        c.add("file-not-linked", "arr.manualimport",
              f"{len(manual)} bestand(en) in {folder} horen bij dit item maar zijn niet "
              f"geïmporteerd: {manual[0].get('path')}", evidence=evidence["manual_import"])

    # -- 6. wachtrij ------------------------------------------------------------------------------
    for q in queue_items:
        state = str(q.get("trackedDownloadState") or "").lower()
        tstatus = str(q.get("trackedDownloadStatus") or "").lower()
        status = str(q.get("status") or "").lower()
        messages = []
        for sm in q.get("statusMessages") or []:
            messages += [str(m) for m in sm.get("messages") or []] or [str(sm.get("title"))]
        msg = "; ".join(messages) or str(q.get("errorMessage") or "")
        if state in ("importblocked", "importfailed") or (
                state == "importpending" and tstatus in ("warning", "error")):
            from mediaguard.activity.api import import_message_nl

            nl = "; ".join(dict.fromkeys(import_message_nl(m) for m in messages)) or msg
            c.add("import-failed", f"queue.trackedDownloadState={q.get('trackedDownloadState')}",
                  f"Import mislukt: {nl or 'geen melding'}", evidence=q.get("title"))
        elif status in ("failed", "downloadclientunavailable") or state in (
                "failedpending", "failed"):
            c.add("download-client-failed", f"queue.status={q.get('status')}",
                  f"Download mislukt in {q.get('downloadClient') or 'downloadclient'}: "
                  f"{msg or q.get('status')}")
        elif status in ("warning", "stalled") or tstatus in ("warning", "error"):
            c.add("stuck-queue", f"queue.status={q.get('status')}",
                  f"Download staat stil: {msg or q.get('status')}")
        else:
            added = iso_z(q.get("added"))
            stale = added and added < (datetime.now(UTC) - timedelta(hours=48)).strftime(
                "%Y-%m-%dT%H:%M:%SZ")
            if stale and q.get("sizeleft") == q.get("size") and status in ("queued", "paused"):
                c.add("stuck-queue", "queue.age", f"Al sinds {added} in de wachtrij zonder "
                      "voortgang.")
            else:
                c.add("downloading", f"queue.status={q.get('status')}",
                      f"In download ({q.get('status')}), resterend "
                      f"{q.get('timeleft') or 'onbekend'}.", severity="info")

    # -- 7. downloadclients ------------------------------------------------------------------------
    enabled_clients = [d for d in clients if d.get("enable", True)]
    if "downloadclient" not in {e.get("source") for e in errors}:
        if not enabled_clients:
            c.add("download-client-missing", "arr.downloadclient",
                  f"Geen ingeschakelde downloadclient in {iid}.")
        else:
            protocols = {str(d.get("protocol", "")).lower() for d in enabled_clients}
            approved_protocols = {str(x.get("protocol", "")).lower() for x in candidates
                                  if candidate_category(x) is None}
            if approved_protocols and not approved_protocols & protocols:
                c.add("download-client-missing", "arr.downloadclient.protocol",
                      f"Goedgekeurde releases zijn {', '.join(sorted(approved_protocols))}, "
                      f"maar er is alleen een client voor {', '.join(sorted(protocols))}.")
    for h in health:
        if str(h.get("source", "")).startswith("DownloadClient") and \
                str(h.get("type", "")).lower() == "error":
            c.add("download-client-failed", f"health:{h.get('source')}", str(h.get("message")),
                  confidence="medium")
        if str(h.get("source", "")) in ("RemotePathMappingCheck", "ImportMechanismCheck"):
            c.add("import-failed", f"health:{h.get('source')}", str(h.get("message")),
                  severity="warning", confidence="medium")

    # -- 8. indexers -------------------------------------------------------------------------------
    if "indexer" not in {e.get("source") for e in errors}:
        item_tags = set(loads(item.get("tags_json"), []) or [])
        search_enabled = [i for i in indexers if i.get("enableAutomaticSearch")
                          or (interactive and i.get("enableInteractiveSearch"))]
        applicable = [i for i in search_enabled
                      if not i.get("tags") or set(i.get("tags") or []) & item_tags]
        if not search_enabled:
            if not inst.get("suspended"):
                c.add("no-indexers", "arr.indexer.enableAutomaticSearch",
                      f"Geen indexer met automatisch zoeken ingeschakeld in {iid}.")
        elif not applicable:
            c.add("no-indexers", "arr.indexer.tags",
                  "Alle zoek-indexers zijn via tags beperkt tot andere titels; geen enkele "
                  "geldt voor dit item.")
        else:
            disabled = {}
            for s in idx_status:
                till = iso_z(s.get("disabledTill"))
                if till and till > now:
                    disabled[int(s.get("indexerId") or s.get("providerId") or 0)] = till
            down = [i for i in applicable if int(i["id"]) in disabled]
            idx_health = [h for h in health if str(h.get("source", "")).startswith("Indexer")]
            text = " ".join(str(h.get("message", "")) for h in idx_health).lower()
            if down:
                names = ", ".join(str(i.get("name")) for i in down)
                all_down = len(down) == len(applicable)
                if any(n in text for n in _AUTH):
                    code = "indexer-auth"
                elif any(n in text for n in _RATE):
                    code = "indexer-rate-limit"
                else:
                    code = "indexer-offline"
                c.add(code, "arr.indexerstatus.disabledTill",
                      f"{'Alle' if all_down else 'Sommige'} indexers uitgeschakeld tot "
                      f"{min(disabled[int(i['id'])] for i in down)}: {names}. "
                      f"{' '.join(str(h.get('message')) for h in idx_health[:2])}".strip(),
                      severity="error" if all_down else "warning",
                      confidence="high" if all_down else "medium")
            elif idx_health and any(n in text for n in _AUTH):
                c.add("indexer-auth", "health:Indexer", " ".join(
                    str(h.get("message")) for h in idx_health[:2]), severity="warning")
            elif idx_health and any(n in text for n in _RATE):
                c.add("indexer-rate-limit", "health:Indexer", " ".join(
                    str(h.get("message")) for h in idx_health[:2]), severity="warning")

    # -- 9. nummering -------------------------------------------------------------------------
    if is_episode:
        if episode.get("unverified_scene_numbering"):
            c.add("incorrect-mapping", "sonarr.unverifiedSceneNumbering",
                  "Scene-nummering van deze aflevering is niet geverifieerd (TheXEM).",
                  severity="warning", confidence="medium")
        if item.get("series_type") == "anime" and not episode.get("absolute_episode_number") \
                and int(episode.get("season_number") or 0) > 0:
            c.add("incorrect-mapping", "sonarr.absoluteEpisodeNumber",
                  "Anime-aflevering zonder absoluut nummer: anime-releases kunnen niet "
                  "worden gekoppeld.", severity="warning", confidence="medium")

    # -- 10. kandidaten -------------------------------------------------------------------------
    if candidates_source is not None:
        distribution: dict[str, int] = {}
        approved = []
        for cand in candidates:
            cat = candidate_category(cand)
            if cat is None:
                approved.append(cand)
            else:
                distribution[cat] = distribution.get(cat, 0) + 1
        evidence["rejection_distribution"] = distribution
        if not candidates:
            c.add("no-indexer-hit", "arr.release", "Interactieve zoekactie op alle indexers gaf "
                  "0 resultaten." if candidates_source == "interactive" else
                  f"Laatste interactieve zoekactie ({candidates_source}) gaf 0 resultaten.")
        elif approved:
            best = approved[0]
            c.add("release-available", "arr.release.approved",
                  f"{len(approved)} goedgekeurde release(s), bv. '{best.get('title')}' "
                  f"({best.get('indexer')}).", severity="info")
        else:
            top = sorted(distribution.items(), key=lambda kv: (-kv[1], PRIORITY.index(kv[0])
                                                               if kv[0] in PRIORITY else 99))
            for code, count in top:
                example = next((r["text"] for cand in candidates for r in cand["rejections"]
                                if r["code"] == code), "")
                c.add(code, f"release.rejection: {example}"[:300],
                      f"{count} van {len(candidates)} releases afgewezen: {example}",
                      evidence={"count": count, "example": example})

    # -- 11. historie ---------------------------------------------------------------------------
    events = sorted(history, key=lambda h: str(h.get("date", "")), reverse=True)
    if events:
        latest = events[0]
        etype = str(latest.get("eventType", "")).lower()
        data = latest.get("data") or {}
        if etype == "downloadfailed":
            c.add("download-client-failed", "history.downloadFailed",
                  f"Laatste download mislukt: {data.get('message') or latest.get('sourceTitle')}",
                  ts=latest.get("date"), confidence="medium")
        elif etype == "downloadignored":
            c.add("intentional-exception", "history.downloadIgnored",
                  f"Download handmatig genegeerd: {latest.get('sourceTitle')}",
                  ts=latest.get("date"))
        elif etype == "grabbed" and not queue_items and not (
                episode.get("has_file") if is_episode else item.get("has_file")):
            from mediaguard.textfmt import datetime_nl

            client_info = None
            if latest.get("downloadId"):
                from mediaguard.downloads.failure_lookup import lookup

                with contextlib.suppress(Exception):
                    client_info = (await lookup(ctx, [str(latest["downloadId"])])).get(
                        str(latest["downloadId"]))
            when = datetime_nl(latest.get("date"))
            if client_info and client_info.get("failed"):
                # rapport 3 / R6: de echte reden uit SABnzbd/qBittorrent
                c.add("download-client-failed", "client.history.failed",
                      f"Download mislukt op {when}: {client_info['reason_nl']} "
                      f"('{latest.get('sourceTitle')}'). MediaGuard markeert de grab als "
                      "mislukt; Sonarr/Radarr zoeken een andere release.",
                      ts=latest.get("date"), confidence="high",
                      evidence={"client": client_info.get("client"),
                                "client_code": client_info.get("code"),
                                "client_message": client_info.get("raw")})
            else:
                extra = (" De downloadclient kent deze download niet meer." if client_info is None
                         and latest.get("downloadId") else "")
                c.add("import-failed", "history.grabbed-without-import",
                      f"Gegrabd op {when} ('{latest.get('sourceTitle')}'), maar niet "
                      f"geïmporteerd en niet meer in de wachtrij.{extra}", ts=latest.get("date"),
                      confidence="medium")

    # -- 12. zoekpogingen ---------------------------------------------------------------------
    attempts = ctx.db.fetch_dicts("SELECT status, ts FROM backfill_attempts WHERE wanted_id=? "
                                  "AND status!='candidates' ORDER BY id DESC LIMIT 10",
                                  (wanted_id,))
    no_hits = [a for a in attempts if a["status"] == "no_candidate"]
    if no_hits:
        c.add("searched-no-grab", "mediaguard.backfill",
              f"{len(no_hits)} automatische zoekpoging(en) zonder grab, laatste op "
              f"{no_hits[0]['ts']}.", ts=no_hits[0]["ts"], confidence="medium")
    if not attempts and not history and candidates_source is None:
        c.add("not-searched-yet", "mediaguard.backfill",
              "Er is nog niet naar dit item gezocht (geen historie of zoekpogingen).")

    reasons = c.ordered()
    if not reasons:
        c.add("unknown", "mediaguard.diagnosis", "Geen oorzaak gevonden in de beschikbare "
              "gegevens.", confidence="low")
        reasons = c.ordered()
    primary = reasons[0]
    result = {
        "wanted_id": wanted_id, "instance_id": iid, "kind": row["kind"],
        "provider_id": pid, "title": row["title"], "generated_at": utcnow(),
        "interactive": interactive, "primary": primary, "reasons": reasons,
        "evidence": evidence,
    }
    if persist:
        ctx.db.execute_insert(
            "INSERT INTO missing_diagnostics(wanted_id,instance_id,ts,primary_code,reasons_json,"
            "evidence_json,interactive) VALUES (?,?,?,?,?,?,?)",
            (wanted_id, iid, result["generated_at"], primary["code"], dumps(reasons),
             dumps(evidence), int(interactive)))
        ctx.db.execute("UPDATE wanted_items SET last_reason_code=? WHERE id=?",
                       (primary["code"], wanted_id))
    return result


def _record_candidates(ctx: AppContext, row: dict[str, Any], candidates: list[dict],
                       trigger: str) -> int:
    rejections = sorted({r["text"] for c in candidates for r in c.get("rejections", [])})
    approved = sum(1 for c in candidates if candidate_category(c) is None)
    return ctx.db.execute_insert(
        "INSERT INTO backfill_attempts(wanted_id,instance_id,ts,trigger,command,counts_quota,"
        "status,candidates_json,rejections_json,message,finished_at) "
        "VALUES (?,?,?,?,?,?,?,?,?,?,?)",
        (row["id"], row["instance_id"], utcnow(), trigger, "InteractiveSearch", 1, "candidates",
         dumps(candidates), dumps(rejections),
         f"{len(candidates)} kandidaten, {approved} goedgekeurd", utcnow()))


async def interactive_candidates(client: ArrClient, row: dict[str, Any]) -> list[dict]:
    if row["kind"] == "episode":
        releases = await client.releases(episode_id=int(row["provider_id"]))
    else:
        releases = await client.releases(movie_id=int(row["provider_id"]))
    return [summarize_release(r) for r in releases]


def latest_diagnosis(ctx: AppContext, wanted_id: int) -> dict[str, Any] | None:
    row = ctx.db.fetch_one("SELECT * FROM missing_diagnostics WHERE wanted_id=? "
                           "ORDER BY id DESC LIMIT 1", (wanted_id,))
    if row is None:
        return None
    data = dict(row)
    data["reasons"] = loads(data.pop("reasons_json"), [])
    data["evidence"] = loads(data.pop("evidence_json"), {})
    return data


__all__ = ["REASONS", "PRIORITY", "classify_rejection", "diagnose", "summarize_release",
           "candidate_category", "latest_diagnosis", "interactive_candidates", "ArrNotFound"]
