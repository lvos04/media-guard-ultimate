"""Activiteit: in één oogopslag wat er nu gebeurt (downloads, zoeken, AV1, MediaGuard-taken).

Eén endpoint dat alles tegelijk ophaalt met korte timeouts; een instantie die offline is levert
alleen een foutmelding voor die bron op, nooit een kapotte pagina.
"""

from __future__ import annotations

import asyncio
from datetime import UTC, datetime, timedelta
from typing import Any

from fastapi import APIRouter, Depends

from mediaguard.arr.client import ArrError
from mediaguard.auth import Principal, require_viewer
from mediaguard.context import AppContext, get_app_context
from mediaguard.db import loads
from mediaguard.instances.registry import arr_client, list_instances

router = APIRouter(prefix="/api/v1/activity", tags=["activity"])
TIMEOUT = 6.0

COMMANDS_NL = {
    "EpisodeSearch": "Aflevering zoeken", "SeasonSearch": "Seizoen zoeken",
    "SeriesSearch": "Serie zoeken", "MissingEpisodeSearch": "Ontbrekende afleveringen zoeken",
    "CutoffUnmetEpisodeSearch": "Kwaliteitsupgrades zoeken", "MoviesSearch": "Film zoeken",
    "MissingMoviesSearch": "Ontbrekende films zoeken",
    "CutoffUnmetMoviesSearch": "Kwaliteitsupgrades films zoeken", "RssSync": "RSS-synchronisatie",
    "RefreshSeries": "Serie verversen", "RefreshMovie": "Film verversen",
    "RescanSeries": "Serie opnieuw scannen", "RescanMovie": "Film opnieuw scannen",
    "DownloadedEpisodesScan": "Download importeren", "DownloadedMoviesScan": "Download importeren",
    "ProcessMonitoredDownloads": "Downloads controleren", "RenameFiles": "Bestanden hernoemen",
    "RenameSeries": "Serie hernoemen", "RenameMovie": "Film hernoemen", "Backup": "Back-up",
    "ApplicationUpdate": "Update", "Housekeeping": "Onderhoud", "CheckHealth": "Gezondheidscheck",
    "RefreshMonitoredDownloads": "Downloads controleren", "ImportListSync": "Importlijsten",
    "MessagingCleanup": "Opruimen", "UpdateSceneMapping": "Scene-mapping bijwerken",
}
# korte, vaak lopende huishoudtaken: niet tonen (ruis)
QUIET_COMMANDS = {"MessagingCleanup", "CheckHealth", "Housekeeping",
                  "RefreshMonitoredDownloads", "ProcessMonitoredDownloads"}
SEARCH_COMMANDS = {k for k in COMMANDS_NL if "Search" in k or k == "RssSync"}
JOBS_NL = {
    "library.sync": "Bibliotheek synchroniseren", "backfill.run": "Ontbrekende media zoeken",
    "optimize.run": "Optimalisatieplan uitvoeren", "migration.cutover": "Overstap uitvoeren",
    "migration.rollback": "Overstap terugdraaien", "recovery.replace": "Bestand vervangen",
    "recovery.restore": "Origineel terugzetten", "recovery.refresh": "Sonarr/Radarr verversen",
    "quarantine.purge": "Quarantaine opruimen", "quarantine.delete": "Quarantaine verwijderen",
    "quarantine.release_check": "Vervanging controleren",
    "analysis.post_import": "Nieuwe download controleren", "routing.anime_check":
    "Anime-routering", "seerr.sync_requests": "Seerr-aanvragen ophalen",
    "ai.analyze": "AI-analyse", "wanted.diagnose_background": "Ontbrekende items onderzoeken",
}
ENC_ACTIVE = ("submitted", "queued", "running", "verifying", "awaiting_commit",
              "awaiting_activation", "handing_off", "handed_off")
ENC_NL = {"submitted": "Aangeboden", "queued": "In wachtrij", "running": "Encoderen",
          "verifying": "Controleren", "awaiting_commit": "Gecontroleerd",
          "awaiting_activation": "Wacht op activatie", "handing_off": "Vervangen",
          "handed_off": "Vervangen"}


def _tidy(messages: list[str]) -> list[str]:
    """Dubbele meldingen weg (Sonarr herhaalt ze per bestand) en lange kort houden."""
    out: list[str] = []
    for m in messages:
        m = " ".join(str(m).split())
        if len(m) > 240:
            m = m[:237] + "…"
        if m and m not in out:
            out.append(m)
    return out[:3]


def _since(hours: float) -> str:
    return (datetime.now(UTC) - timedelta(hours=hours)).isoformat(timespec="seconds")


def _ep_code(ep: dict[str, Any] | None) -> str:
    if not ep:
        return ""
    return f"S{int(ep.get('seasonNumber') or 0):02d}E{int(ep.get('episodeNumber') or 0):02d}"


def _fill_titles(ctx: AppContext, inst: Any, records: list[dict[str, Any]]) -> None:
    """Titels uit MediaGuards eigen bibliotheek i.p.v. Sonarr te laten meesturen
    (includeSeries/includeEpisode maakt de wachtrijvraag zwaar voor Sonarr)."""
    if inst.app == "sonarr":
        sids = {int(q["seriesId"]) for q in records if q.get("seriesId")}
        eids = {int(q["episodeId"]) for q in records if q.get("episodeId")}
        series = {int(r["provider_id"]): r["title"] for r in ctx.db.fetch_all(
            f"SELECT provider_id, title FROM lib_items WHERE instance_id=? AND kind='series' "
            f"AND provider_id IN ({','.join('?' * len(sids)) or '-1'})", (inst.id, *sids))}
        eps = {int(r["provider_id"]): r for r in ctx.db.fetch_all(
            f"SELECT provider_id, season_number, episode_number FROM lib_episodes WHERE "
            f"instance_id=? AND provider_id IN ({','.join('?' * len(eids)) or '-1'})",
            (inst.id, *eids))}
        for q in records:
            if not q.get("series") and q.get("seriesId") in series:
                q["series"] = {"title": series[q["seriesId"]]}
            ep = eps.get(int(q.get("episodeId") or 0))
            if not q.get("episode") and ep is not None:
                q["episode"] = {"seasonNumber": ep["season_number"],
                                "episodeNumber": ep["episode_number"]}
    else:
        mids = {int(q["movieId"]) for q in records if q.get("movieId")}
        movies = {int(r["provider_id"]): r for r in ctx.db.fetch_all(
            f"SELECT provider_id, title, year FROM lib_items WHERE instance_id=? AND "
            f"kind='movie' AND provider_id IN ({','.join('?' * len(mids)) or '-1'})",
            (inst.id, *mids))}
        for q in records:
            m = movies.get(int(q.get("movieId") or 0))
            if not q.get("movie") and m is not None:
                q["movie"] = {"title": m["title"], "year": m["year"]}


def queue_item(inst: Any, q: dict[str, Any]) -> dict[str, Any]:
    size = float(q.get("size") or 0)
    left = float(q.get("sizeleft") or 0)
    progress = round((1 - left / size) * 100, 1) if size > 0 else None
    if inst.app == "sonarr":
        series = q.get("series") or {}
        title = " · ".join(x for x in (series.get("title"), _ep_code(q.get("episode"))) if x)
        link = _ref_link(inst.id, "series", q.get("seriesId"))
    else:
        movie = q.get("movie") or {}
        year = movie.get("year")
        title = f"{movie.get('title')} ({year})" if movie.get("title") and year else \
            movie.get("title") or ""
        link = _ref_link(inst.id, "movie", q.get("movieId"))
    messages = [m for s in q.get("statusMessages") or [] for m in s.get("messages") or []]
    state = str(q.get("trackedDownloadState") or q.get("status") or "").lower()
    warn = str(q.get("trackedDownloadStatus") or "").lower() in ("warning", "error") or \
        bool(q.get("errorMessage"))
    return {
        "id": q.get("id"), "instance_id": inst.id, "instance_name": inst.name,
        "title": title or q.get("title") or "", "release": q.get("title"),
        "protocol": q.get("protocol"), "client": q.get("downloadClient"),
        "status": str(q.get("status") or "").lower(), "state": state,
        "progress": progress, "size": int(size), "size_left": int(left),
        "eta": q.get("estimatedCompletionTime"), "time_left": q.get("timeleft"),
        "quality": ((q.get("quality") or {}).get("quality") or {}).get("name"),
        "warning": warn, "messages": _tidy(([q["errorMessage"]] if q.get("errorMessage")
                                            else []) + messages), "link": link,
        "messages_nl": [import_message_nl(m) for m in messages],
        "awaiting_import": state in ("importpending", "importblocked", "importing")
        or (str(q.get("status") or "").lower() == "completed" and warn),
        "download_id": q.get("downloadId"),
        "movie_id": q.get("movieId"), "series_id": q.get("seriesId"),
        "episode_id": q.get("episodeId"),
        "can_import": needs_manual_import(messages),
    }


IMPORT_NL = (
    ("matched to movie by ID", "Radarr herkende de film via de download-ID; de import moet "
                               "worden bevestigd (MediaGuard doet dat zelf als het eenduidig is)"),
    ("matched to series by ID", "Sonarr herkende de serie via de download-ID; de import moet "
                                "worden bevestigd (MediaGuard doet dat zelf als het eenduidig is)"),
    ("automatic import not possible", "Sonarr herkende de serie via de download-ID; de import "
                                      "moet worden bevestigd (MediaGuard doet dat zelf als het "
                                      "eenduidig is)"),
    ("Manual Import required", "Handmatige import nodig"),
    ("No files found are eligible for import", "Geen bestand in de download dat geïmporteerd "
                                               "kan worden"),
    ("Not an upgrade for existing", "Geen verbetering ten opzichte van het bestand dat er al is"),
    ("Not a Custom Format upgrade", "Geen verbetering in custom formats"),
    ("sample", "Alleen een sample-bestand gevonden"),
    ("Unable to determine if file is a sample", "Kan niet bepalen of het een sample is"),
    ("has a TBA title", "Aflevering heeft nog geen titel (TBA); import wacht"),
    ("Episode file already imported", "Al geïmporteerd"),
    ("Unknown Series", "Onbekende serie"), ("Unknown Movie", "Onbekende film"),
    ("Invalid season or episode", "Seizoen/aflevering niet herkend"),
    ("Destination already exists", "Doelbestand bestaat al"),
    ("Access to the path", "Geen toegang tot de map (rechten)"),
    ("Not enough free space", "Onvoldoende vrije ruimte"),
)


def import_message_nl(message: str) -> str:
    for needle, nl in IMPORT_NL:
        if needle.lower() in str(message).lower():
            return nl
    return str(message)


def needs_manual_import(messages: list[str]) -> bool:
    from mediaguard.activity.manual_import import is_id_match_block

    return is_id_match_block(messages)


def command_item(inst: Any, c: dict[str, Any]) -> dict[str, Any]:
    name = str(c.get("name") or c.get("commandName") or "")
    body = c.get("body") or {}
    return {"id": c.get("id"), "instance_id": inst.id, "instance_name": inst.name,
            "name": name, "label": COMMANDS_NL.get(name, c.get("commandName") or name),
            "search": name in SEARCH_COMMANDS, "status": c.get("status"),
            "queued_at": c.get("queued"), "started_at": c.get("started"),
            "message": c.get("message"),
            "series_id": body.get("seriesId"), "movie_ids": body.get("movieIds"),
            "episode_ids": body.get("episodeIds")}


class _SourceInst:
    """Oude Sonarr/Radarr als 'instantie' voor queue_item (alleen weergave)."""

    def __init__(self, row: dict[str, Any]) -> None:
        self.id = f"source:{row['id']}"
        self.name = f"{row['name']} (oude installatie)"
        self.app = row["app"]


async def _instance(ctx: AppContext, inst: Any) -> dict[str, Any]:
    out: dict[str, Any] = {"instance_id": inst.id, "name": inst.name, "downloads": [],
                           "commands": [], "error": None}
    try:
        async with arr_client(ctx, inst.id, timeout=TIMEOUT) as client:
            queue, commands = await asyncio.gather(
                client.get("queue", page=1, pageSize=100), client.get("command"))
    except ArrError as exc:
        out["error"] = exc.message
        return out
    except Exception as exc:  # noqa: BLE001 - per bron isoleren
        out["error"] = f"{type(exc).__name__}: {exc}"
        return out
    records = (queue.get("records") if isinstance(queue, dict) else queue) or []
    _fill_titles(ctx, inst, records)
    out["downloads"] = [queue_item(inst, q) for q in records]
    out["commands"] = [command_item(inst, c) for c in commands or []
                       if str(c.get("status")) in ("queued", "started")
                       and str(c.get("name")) not in QUIET_COMMANDS]
    _resolve_command_titles(ctx, inst.id, out["commands"])
    return out


def _resolve_command_titles(ctx: AppContext, iid: str, commands: list[dict[str, Any]]) -> None:
    for c in commands:
        title = None
        if c.get("series_id"):
            row = ctx.db.fetch_one("SELECT title FROM lib_items WHERE instance_id=? AND "
                                   "kind='series' AND provider_id=?", (iid, int(c["series_id"])))
            title = row["title"] if row else None
        elif c.get("movie_ids"):
            ids = [int(i) for i in c["movie_ids"]][:3]
            rows = ctx.db.fetch_all(
                f"SELECT title FROM lib_items WHERE instance_id=? AND kind='movie' AND "
                f"provider_id IN ({','.join('?' for _ in ids)})", (iid, *ids))
            title = ", ".join(r["title"] for r in rows) or None
        c["target"] = title


def _jobs(ctx: AppContext) -> list[dict[str, Any]]:
    rows = ctx.db.fetch_dicts(
        "SELECT id, job_type, status, instance_id, progress, created_at, started_at, run_after, "
        "error FROM jobs WHERE status IN ('running','queued','retry_scheduled') "
        "ORDER BY CASE status WHEN 'running' THEN 0 ELSE 1 END, priority, id LIMIT 50")
    return [{**r, "label": JOBS_NL.get(r["job_type"], r["job_type"]),
             "progress": round(float(r["progress"] or 0) * 100, 1)
             if float(r["progress"] or 0) <= 1 else float(r["progress"]),
             "link": "/activiteit?tab=geschiedenis"} for r in rows]


ESTOP_FROZEN = ("running", "verifying")
ESTOP_LABEL_NL = "Gepauzeerd (noodstop)"


def _encoding(ctx: AppContext) -> dict[str, Any]:
    marks = ",".join("?" for _ in ENC_ACTIVE)
    rows = ctx.db.fetch_dicts(
        f"SELECT id, encoder_job_id, instance_id, source_path, status, progress, created_at, "
        f"updated_at, error FROM encoding_jobs WHERE status IN ({marks}) ORDER BY id", ENC_ACTIVE)
    # noodstop (punt 39): lopende encodes staan bevroren; zeg dat in plaats van 'bezig'
    estop = ctx.emergency_stopped()
    jobs = [{"id": r["id"], "instance_id": r["instance_id"],
             "title": str(r["source_path"] or "").rsplit("/", 1)[-1],
             "status": r["status"],
             "paused_estop": estop and r["status"] in ESTOP_FROZEN,
             "label": ESTOP_LABEL_NL if estop and r["status"] in ESTOP_FROZEN
             else ENC_NL.get(r["status"], r["status"]),
             "progress": round(float(r["progress"] or 0) * 100, 1),
             "updated_at": r["updated_at"],
             # met engine-ID opent de wachtrij direct het detailpaneel van díe job
             "link": (f"/av1/wachtrij?job={int(r['encoder_job_id'])}"
                      if str(r["encoder_job_id"] or "").isdigit() else "/av1")} for r in rows]
    autopilot: dict[str, Any] = {}
    try:
        from mediaguard.encoding import autopilot as ap

        autopilot = {"enabled": bool(ctx.config.value("encoding.autopilot_enabled")),
                     "blocked": ap.blocked_reason(ctx),
                     "queue_depth": int(ctx.config.value("encoding.autopilot_queue_depth")),
                     "last": ctx.state_get("encoding_autopilot", {})}
    except Exception:  # noqa: BLE001 - autopilot optioneel
        autopilot = {}
    return {"jobs": jobs, "autopilot": autopilot}


SEARCH_DONE_AFTER_MINUTES = 5
SEARCH_STATUS_NL = {"pending": "Bezig", "grabbed": "Gevonden en gedownload",
                    "no_candidate": "Klaar — niets passends gevonden", "error": "Mislukt",
                    "done": "Klaar — nog geen nieuwe download (MediaGuard kijkt later opnieuw)"}


def _searches(ctx: AppContext) -> list[dict[str, Any]]:
    rows = ctx.db.fetch_dicts(
        "SELECT a.id, a.instance_id, a.ts, a.status, a.command, a.message, "
        "COALESCE(w.item_title, w.title) AS title, w.title AS series_title, w.season_number, "
        "w.episode_number FROM backfill_attempts a LEFT JOIN wanted_items w ON w.id=a.wanted_id "
        "WHERE a.ts >= ? ORDER BY a.id DESC LIMIT 20", (_since(6),))
    # rapport 2 / N23: een zoekopdracht staat niet uren op 'Bezig'; Sonarr/Radarr zoeken in
    # seconden, de uitkomst (grab of niet) controleert MediaGuard later zelf
    cutoff = (datetime.now(UTC) - timedelta(minutes=SEARCH_DONE_AFTER_MINUTES)).isoformat(
        timespec="seconds")
    for r in rows:
        if r["status"] == "pending" and str(r["ts"]) < cutoff:
            r["status"] = "done"
        r["status_nl"] = SEARCH_STATUS_NL.get(str(r["status"]), str(r["status"]))
    return rows


def _recent(ctx: AppContext) -> list[dict[str, Any]]:
    since = _since(24)
    out: list[dict[str, Any]] = []
    for r in ctx.db.fetch_dicts(
            "SELECT instance_id, event_type, release_title, quality, indexer, received_at, "
            "series_id, movie_id FROM release_provenance WHERE received_at>=? "
            # rapport 7 / A4: imports komen uit import_events; de herkomstregel van dezelfde
            # import gaf elke import dubbel in 'Recent'
            "AND event_type='Grab' ORDER BY id DESC LIMIT 30", (since,)):
        out.append({"kind": "grab",
                    "instance_id": r["instance_id"], "at": r["received_at"],
                    "title": r["release_title"], "detail": " · ".join(
                        x for x in (r["quality"], r["indexer"]) if x),
                    "link": _lib_link(r)})
    for r in ctx.db.fetch_dicts(
            "SELECT instance_id, event_type, is_upgrade, file_path, received_at, series_id, "
            "movie_id, payload_json FROM import_events WHERE received_at>=? "
            "ORDER BY id DESC LIMIT 30", (since,)):
        if r["event_type"] not in ("Download", "Upgrade"):
            continue
        out.append({"kind": "upgrade" if r["is_upgrade"] else "import",
                    "instance_id": r["instance_id"], "at": r["received_at"],
                    "title": _import_title(r), "detail": None, "link": _lib_link(r)})
    from mediaguard.audit_labels import action_nl, actor_nl

    # rapport 4, S17: wat een andere app (via een API-sleutel) liet doen, zichtbaar maken
    for r in ctx.db.fetch_dicts(
            "SELECT actor, action, instance_id, ts, reason FROM audit_events WHERE ts>=? AND "
            "actor LIKE 'apikey:%' AND action NOT LIKE 'apikey.%' ORDER BY id DESC LIMIT 20",
            (since,)):
        out.append({"kind": "external", "instance_id": r["instance_id"], "at": r["ts"],
                    "title": action_nl(r["action"]),
                    "detail": f"gestart door {actor_nl(r['actor'])}", "link": None,
                    "actor_nl": actor_nl(r["actor"])})
    for r in ctx.db.fetch_dicts(
            "SELECT id, instance_id, library_path, state, finished_at FROM "
            "replacement_transactions WHERE finished_at>=? AND "
            "state IN ('committed','rolled_back') "
            "ORDER BY id DESC LIMIT 20", (since,)):
        out.append({"kind": "replaced" if r["state"] == "committed" else "restored",
                    "instance_id": r["instance_id"], "at": r["finished_at"],
                    "title": str(r["library_path"]).rsplit("/", 1)[-1], "detail": None,
                    "link": _quarantine_link(ctx, int(r["id"]))})
    for r in ctx.db.fetch_dicts(
            "SELECT source_instance, target_instance, title, status, updated_at FROM "
            "anime_routing_actions WHERE updated_at>=? AND status IN ('moved','reverted') "
            "ORDER BY id DESC LIMIT 20", (since,)):
        out.append({"kind": "routed", "instance_id": r["target_instance"],
                    "at": r["updated_at"], "title": r["title"],
                    "detail": f"{r['source_instance']} → {r['target_instance']}",
                    "link": "/anime"})
    for r in ctx.db.fetch_dicts(
            "SELECT instance_id, title, detail, action, acted_at FROM stuck_import_actions "
            "WHERE acted_at>=? AND action IS NOT NULL ORDER BY id DESC LIMIT 20", (since,)):
        out.append({"kind": "stuck_fixed" if r["action"] == "removed_blocklisted"
                    else "stuck_failed", "instance_id": r["instance_id"], "at": r["acted_at"],
                    "title": r["title"] or "", "detail": r["detail"], "link": None})
    for r in ctx.db.fetch_dicts(
            "SELECT action, actor, target_type, target_id, instance_id, ts, reason, new_value "
            "FROM audit_events WHERE ts>=? AND action IN (" + ",".join("?" * len(AUTO_KINDS))
            + ") ORDER BY id DESC LIMIT 30", (since, *AUTO_KINDS)):
        new = loads(r["new_value"], {}) or {}
        title = str(new.get("file") or new.get("title") or "").rsplit("/", 1)[-1]
        link = None
        if r["target_type"] in ("episode_file", "movie_file"):
            from mediaguard.attention import identity_href

            link = identity_href(ctx, r["instance_id"], r["target_type"], r["target_id"])
        out.append({"kind": AUTO_KINDS[r["action"]], "instance_id": r["instance_id"],
                    "at": r["ts"], "title": title or str(r["target_id"] or ""),
                    "detail": r["reason"], "link": link})
    out.sort(key=lambda x: str(x["at"] or ""), reverse=True)
    return dedupe_recent(out)[:30]


# Zelfde gebeurtenis via twee webhooks/bronnen kort na elkaar: één regel. Vroeger telde
# 'dezelfde minuut' (afgekapt op HH:MM), zodat 12:00:59 en 12:01:02 twee regels gaven
# (punt 39). Nu: binnen dit venster rond de vorige getoonde regel van dezelfde soort.
RECENT_DUP_SECONDS = 180


def _when(value: Any) -> datetime | None:
    try:
        dt = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    except ValueError:
        return None
    return dt if dt.tzinfo else dt.replace(tzinfo=UTC)


def dedupe_recent(rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Rijen nieuwste eerst. Rapport 8 / B5: dezelfde import kwam ook als 'import' én
    'upgrade' binnen (twee events voor hetzelfde bestand); die worden één regel 'upgrade'."""
    kept: dict[tuple[Any, ...], list[dict[str, Any]]] = {}
    unique: list[dict[str, Any]] = []
    for row in rows:
        family = "import" if row["kind"] in ("import", "upgrade") else row["kind"]
        key = (family, row.get("instance_id"), row.get("title"))
        at = _when(row.get("at"))
        # zonder leesbaar tijdstip alleen samenvoegen met een andere regel zonder tijdstip
        twin = next((k for k in kept.get(key, []) if (at is None and k["_at"] is None)
                     or (at is not None and k["_at"] is not None
                         and abs((k["_at"] - at).total_seconds()) <= RECENT_DUP_SECONDS)),
                    None)
        if twin is not None:
            if row["kind"] == "upgrade":
                twin["kind"] = "upgrade"
            continue
        row["_at"] = at
        kept.setdefault(key, []).append(row)
        unique.append(row)
    for row in unique:
        row.pop("_at", None)
    return unique


# wat MediaGuard zelf heeft beslist (automation.py), zichtbaar in Activiteit → Recent
AUTO_KINDS = {
    "identity.reevaluated": "identity_reevaluated",
    "identity.ai_verdict": "identity_ai",
    "identity.auto_replaced": "identity_replaced",
    "arr.removed_deleted_title": "title_removed",
}


def _ref_link(instance_id: str, kind: str, provider_id: Any) -> str | None:
    """Voorlopige verwijzing op Sonarr/Radarr-ID; ``resolve_links`` maakt er de echte
    bibliotheekpagina van (die verwacht MediaGuard's eigen item-ID, niet het Sonarr-ID —
    anders opent een ánder item met toevallig hetzelfde nummer)."""
    return f"ref:{instance_id}:{kind}:{int(provider_id)}" if provider_id else None


def _quarantine_link(ctx: AppContext, transaction_id: int) -> str:
    row = ctx.db.fetch_one("SELECT id FROM quarantine_items WHERE transaction_id=? ORDER BY id "
                           "DESC LIMIT 1", (transaction_id,))
    return f"/quarantaine?item={int(row['id'])}" if row else "/quarantaine"


def _lib_link(r: dict[str, Any]) -> str | None:
    if r.get("series_id"):
        return _ref_link(r["instance_id"], "series", r["series_id"])
    if r.get("movie_id"):
        return _ref_link(r["instance_id"], "movie", r["movie_id"])
    return None


def resolve_links(ctx: AppContext, rows: list[dict[str, Any]]) -> None:
    refs = {str(r["link"]) for r in rows if str(r.get("link") or "").startswith("ref:")}
    if not refs:
        return
    found: dict[str, str] = {}
    for ref in refs:
        _, iid, kind, pid = ref.split(":", 3)
        row = ctx.db.fetch_one("SELECT id FROM lib_items WHERE instance_id=? AND kind=? AND "
                               "provider_id=? AND removed_at IS NULL", (iid, kind, int(pid)))
        if row is not None:
            found[ref] = f"/bibliotheek/{iid}/{int(row['id'])}"
    for r in rows:
        link = str(r.get("link") or "")
        if link.startswith("ref:"):
            r["link"] = found.get(link)


def _import_title(r: dict[str, Any]) -> str:
    payload = loads(r.get("payload_json"), {}) or {}
    series = (payload.get("series") or {}).get("title")
    movie = (payload.get("movie") or {}).get("title")
    eps = payload.get("episodes") or []
    code = _ep_code(eps[0]) if eps else ""
    if series:
        return f"{series} · {code}" if code else series
    if movie:
        return movie
    return str(r.get("file_path") or "").rsplit("/", 1)[-1]


CACHE_SECONDS = 4.0


@router.get("")
async def activity(_: Principal = Depends(require_viewer),
                   ctx: AppContext = Depends(get_app_context)) -> dict[str, Any]:
    """Max. één ronde per 4 s, gedeeld door alle open tabbladen/kijkers: de pagina ververst
    elke 5 s en vroeg anders per kijker 8 verzoeken aan Sonarr/Radarr."""
    result = await activity_cached(ctx)
    from mediaguard.health_summary import compose

    # zelfde tellers als topbalk/Overzicht (/api/v1/health/summary)
    return {**result, "health": compose(ctx, result, (ctx.services.get(
        "health_summary_encoder") or (0, None))[1])}


async def activity_cached(ctx: AppContext) -> dict[str, Any]:
    import time

    cached = ctx.services.get("activity_cache")
    if cached and time.monotonic() - cached[0] < CACHE_SECONDS:
        return cached[1]  # type: ignore[no-any-return]
    lock = ctx.services.setdefault(f"activity_lock:{id(asyncio.get_running_loop())}",
                                   asyncio.Lock())
    async with lock:
        cached = ctx.services.get("activity_cache")
        if cached and time.monotonic() - cached[0] < CACHE_SECONDS:
            return cached[1]  # type: ignore[no-any-return]
        result = await _collect(ctx)
        ctx.services["activity_cache"] = (time.monotonic(), result)
        return result


def _is_transferring(d: dict[str, Any]) -> bool:
    """Telt mee als 'Downloads' (rapport 8, B3): dezelfde regels die de lijst als lopend toont,
    ook als Sonarr/Radarr een andere status geven dan de client (bv. 'queued'/'warning' terwijl
    SABnzbd al binnenhaalt met een resterende tijd)."""
    status = str(d.get("status") or "").lower()
    if status in ("downloading", "stalled"):
        return True
    if d.get("awaiting_import") or status in ("paused", "completed", "failed"):
        return False
    progress = d.get("progress")
    return bool(d.get("time_left")) and (progress is None or float(progress) < 100)


async def _collect(ctx: AppContext) -> dict[str, Any]:
    from mediaguard.downloads import live

    instances = [i for i in list_instances(ctx) if not i.suspended]
    per, (client_items, client_errors), (old_queues, old_errors) = await asyncio.gather(
        asyncio.gather(*(_instance(ctx, i) for i in instances)),
        live.client_downloads(ctx), live.source_queues(ctx))
    downloads = [d for p in per for d in p["downloads"]]
    # rapport 7 / A2: tijdens de migratie grabben de oude Sonarr/Radarr nog
    for row, records in old_queues:
        old = _SourceInst(row)
        _fill_titles(ctx, old, records)
        downloads.extend({**queue_item(old, q), "link": None, "old_install": True}
                         for q in records)
    # en wat de downloadclients zelf laten zien (ook zonder Sonarr/Radarr-koppeling)
    downloads = live.merge(downloads, client_items)
    commands = [c for p in per for c in p["commands"]]
    errors = [{"instance_id": p["instance_id"], "name": p["name"], "error": p["error"]}
              for p in per if p["error"]]
    errors += [{"instance_id": None, "name": e["client"], "error": e["error"]}
               for e in client_errors]
    errors += old_errors
    downloads.sort(key=lambda d: (d["status"] != "downloading", -(d["progress"] or 0)))
    enc = _encoding(ctx)
    jobs = _jobs(ctx)
    searches = _searches(ctx)
    problems = sum(1 for d in downloads if d["warning"]) + len(errors) + sum(
        1 for j in jobs if j["status"] == "retry_scheduled")
    names = {i.id: i.name for i in list_instances(ctx, include_archived=True)}
    recent = _recent(ctx)
    resolve_links(ctx, [*downloads, *recent])
    for row in (*enc["jobs"], *jobs, *searches, *recent):
        row["instance_name"] = names.get(row.get("instance_id") or "", row.get("instance_id"))
    return {
        "generated_at": datetime.now(UTC).isoformat(timespec="seconds"),
        "summary": {"downloads": sum(1 for d in downloads if _is_transferring(d)),
                    # 0 betekent alleen 'niets' als elke client antwoordde (A2)
                    "download_clients_ok": not client_errors,
                    "download_client_errors": [e["error"] for e in client_errors],
                    "queued_downloads": len(downloads),
                    # rapport 2 / N9: voltooid, wacht op import (was 'Downloads 0')
                    "awaiting_import": sum(1 for d in downloads if d.get("awaiting_import")),
                    # alleen zoekacties die nu lopen; in de rij = 'in de wachtrij', niet
                    # 'bezig' (rapport 4, S11: 'met 29 dingen bezig')
                    "searches": sum(1 for c in commands if c["search"]
                                    and str(c.get("status") or "").lower() == "started"),
                    "queued_searches": sum(1 for c in commands if c["search"]
                                           and str(c.get("status") or "").lower() != "started"),
                    "commands": len(commands), "av1": len(enc["jobs"]),
                    "tasks": len(jobs), "problems": problems},
        "downloads": downloads, "commands": commands, "encoding": enc, "jobs": jobs,
        "searches": searches, "recent": recent, "errors": errors,
    }
