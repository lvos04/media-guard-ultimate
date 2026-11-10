"""Wanted-items: afgeleid uit de bibliotheekcache per instantie.

Alleen *gemonitorde*, *uitgezonden/beschikbare* items zonder bestand worden 'missing';
items onder cutoff worden 'cutoff'. Bewust niet-gemonitorde of toekomstige afleveringen
worden nooit als ontbrekend gemarkeerd (R4, D4).

Scope-ID's voor instellingen (zie ``settings_scope_chain``):
  instance = '<instance_id>'
  series   = '<instance_id>:<series_id>'      movie   = '<instance_id>:<movie_id>'
  season   = '<instance_id>:<series_id>:<season_number>'
  episode  = '<instance_id>:<episode_id>'
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from typing import TYPE_CHECKING, Any

from mediaguard.db import loads, utcnow
from mediaguard.library.sync import now_z

if TYPE_CHECKING:
    from mediaguard.context import AppContext

OPEN_STATUSES = ("wanted", "searching", "unavailable")
WANTED_STATUS_NL = {"wanted": "Gewenst", "searching": "Wordt gezocht",
                    "unavailable": "Niet beschikbaar", "found": "Gevonden",
                    "ignored": "Genegeerd", "resolved": "Opgelost"}


def settings_scope_chain(row: dict[str, Any]) -> dict[str, str]:
    iid = row["instance_id"]
    chain = {"instance": iid}
    if row.get("media_type"):
        chain["media_type"] = row["media_type"]
    if row["kind"] == "episode":
        chain["series"] = f"{iid}:{row['item_provider_id']}"
        chain["season"] = f"{iid}:{row['item_provider_id']}:{row.get('season_number')}"
        chain["episode"] = f"{iid}:{row['provider_id']}"
    else:
        chain["movie"] = f"{iid}:{row['provider_id']}"
    return chain


def _z(dt: datetime) -> str:
    return dt.astimezone(UTC).strftime("%Y-%m-%dT%H:%M:%SZ")


TBA_GRACE_DAYS = 14
CINEMA_ONLY_DAYS = 365
# Een film is pas 'ontbrekend' als hij te koop/te streamen is (digitale of fysieke release
# voorbij). Radarr zegt met minimumAvailability 'announced' al 'beschikbaar' bij Wadi 3 (2026)
# of Wolkenridders 3 (2028): die zijn 'verwacht'. Zonder datums: alleen oude bioscoopfilms.
RELEASED_SQL = ("COALESCE(l.digital_release<=? OR l.physical_release<=? OR "
                "(l.digital_release IS NULL AND l.physical_release IS NULL AND "
                "l.in_cinemas IS NOT NULL AND l.in_cinemas<=?), 0)")


def upcoming_counts(ctx: AppContext, instance_id: str | None = None) -> dict[str, int]:
    """Gevolgd, nog geen bestand, nog niet uit: 'Verwacht' (telt niet als ontbrekend)."""
    now = now_z()
    w = " AND l.instance_id=?" if instance_id else ""
    p: tuple[Any, ...] = (instance_id,) if instance_id else ()
    movies = ctx.db.fetch_one(
        "SELECT COUNT(*) AS n FROM lib_items l WHERE l.kind='movie' AND l.removed_at IS NULL "
        "AND l.monitored=1 AND l.has_file=0 AND NOT " + RELEASED_SQL + w,
        (now, now, _z(datetime.now(UTC) - timedelta(days=CINEMA_ONLY_DAYS)), *p))
    we = " AND e.instance_id=?" if instance_id else ""
    episodes = ctx.db.fetch_one(
        "SELECT COUNT(*) AS n FROM lib_episodes e JOIN lib_items l ON l.instance_id="
        "e.instance_id AND l.kind='series' AND l.provider_id=e.series_provider_id WHERE "
        "l.removed_at IS NULL AND e.monitored=1 AND l.monitored=1 AND e.has_file=0 AND "
        "(e.air_date_utc IS NULL OR e.air_date_utc>? OR (COALESCE(e.title,'') IN ('TBA','TBD') "
        "AND e.air_date_utc>?))" + we,
        (now, _z(datetime.now(UTC) - timedelta(days=TBA_GRACE_DAYS)), *p))
    m, e = int(movies["n"] if movies else 0), int(episodes["n"] if episodes else 0)
    return {"total": m + e, "movies": m, "episodes": e}


def _current_sets(ctx: AppContext, instance_id: str) -> list[dict[str, Any]]:
    """Alle items die nu missing/cutoff zijn volgens de cache."""
    now = now_z()
    app_row = ctx.db.fetch_one("SELECT app FROM instances WHERE id=?", (instance_id,))
    if app_row is None:
        return []
    if app_row["app"] == "sonarr":
        rows = ctx.db.fetch_dicts(
            "SELECT e.provider_id, e.series_provider_id AS item_provider_id, e.season_number, "
            "e.episode_number, e.absolute_episode_number, e.air_date_utc, e.title AS ep_title, "
            "e.has_file, l.title AS item_title, f.quality_cutoff_not_met "
            "FROM lib_episodes e JOIN lib_items l ON l.instance_id=e.instance_id AND "
            "l.kind='series' AND l.provider_id=e.series_provider_id "
            "LEFT JOIN lib_files f ON f.instance_id=e.instance_id AND f.kind='episode' AND "
            "f.provider_id=e.episode_file_id "
            "WHERE e.instance_id=? AND l.removed_at IS NULL AND e.monitored=1 AND l.monitored=1 "
            "AND ((e.has_file=0 AND e.air_date_utc IS NOT NULL AND e.air_date_utc<=? "
            # 'TBA' (titel nog niet bekend) die net/vandaag 'uitgezonden' is: verwacht, niet
            # ontbrekend (rapport 4 nov, H7)
            "AND NOT (COALESCE(e.title,'') IN ('TBA','TBD') AND e.air_date_utc>?)) OR "
            "(e.has_file=1 AND f.quality_cutoff_not_met=1))",
            (instance_id, now, _z(datetime.now(UTC) - timedelta(days=TBA_GRACE_DAYS))))
        out = []
        for r in rows:
            code = f"S{int(r['season_number']):02d}E{int(r['episode_number']):02d}"
            out.append({**r, "kind": "episode",
                        "reason": "cutoff" if r["has_file"] else "missing",
                        "title": f"{r['item_title']} – {code}"
                                 + (f" – {r['ep_title']}" if r.get("ep_title") else "")})
        return out
    rows = ctx.db.fetch_dicts(
        "SELECT l.provider_id, l.provider_id AS item_provider_id, l.title AS item_title, "
        "l.year, l.has_file, l.is_available, f.quality_cutoff_not_met, "
        "COALESCE(l.digital_release, l.physical_release, l.in_cinemas) AS air_date_utc "
        "FROM lib_items l LEFT JOIN lib_files f ON f.instance_id=l.instance_id AND "
        "f.kind='movie' AND f.provider_id=l.file_id WHERE l.instance_id=? AND l.kind='movie' "
        "AND l.removed_at IS NULL AND l.monitored=1 AND ((l.has_file=0 AND l.is_available=1 AND "
        + RELEASED_SQL + ") OR (l.has_file=1 AND f.quality_cutoff_not_met=1))",
        (instance_id, now, now, _z(datetime.now(UTC) - timedelta(days=CINEMA_ONLY_DAYS))))
    return [{**r, "kind": "movie", "reason": "cutoff" if r["has_file"] else "missing",
             "season_number": None, "episode_number": None, "absolute_episode_number": None,
             "title": f"{r['item_title']} ({r['year']})" if r.get("year") else r["item_title"]}
            for r in rows]


def _priority(ctx: AppContext, item: dict[str, Any], season_missing: dict, season_sizes: dict,
              cache: dict[str, Any], instance_id: str) -> int:
    score = 100
    if "_recent_days" not in cache:
        from mediaguard.wanted.backfill import cfg

        cache["_recent_days"] = int(cfg(ctx, "backfill.priority_recent_days", instance_id))
        # grenzen één keer per ronde berekenen (was: 2× strftime per open item)
        cache["_recent_cut"] = _z(datetime.now(UTC) - timedelta(days=cache["_recent_days"]))
        cache["_old_cut"] = _z(datetime.now(UTC) - timedelta(days=365))
    air = item.get("air_date_utc")
    if air:
        if air >= cache["_recent_cut"]:
            score += 50
        elif air < cache["_old_cut"]:
            score += 10  # lang ontbrekend
    if item["reason"] == "cutoff":
        score -= 40
    if item["kind"] == "episode":
        key = (item["item_provider_id"], item["season_number"])
        size = season_sizes.get(key, 0)
        missing = season_missing.get(key, 0)
        if size >= 4 and 0 < missing <= 2:
            score += 20  # seizoen bijna compleet
        if item["season_number"] == 0:
            score -= 20  # specials lager
    scope_key = f"{instance_id}:{item['item_provider_id']}"
    if scope_key not in cache:
        chain: dict[str, str] = {"instance": instance_id}
        chain["series" if item["kind"] == "episode" else "movie"] = scope_key
        cache[scope_key] = ctx.config.value("backfill.priority", chain)
    score += {"high": 30, "normal": 0, "low": -30}.get(cache[scope_key], 0)
    return score


def refresh_wanted_if_changed(ctx: AppContext, instance_id: str) -> dict[str, int] | None:
    """Alleen herberekenen als de bibliotheekcache sinds de vorige keer veranderde (een
    nieuwe sync of een handmatige monitorwijziging)."""
    row = ctx.db.fetch_one("SELECT MAX(id) AS id FROM library_sync_runs WHERE instance_id=?",
                           (instance_id,))
    marker = (ctx.state_get(f"wanted_refresh:{instance_id}", {}) or {})
    latest = int(row["id"] or 0) if row else 0
    if marker.get("sync_run") == latest and latest:
        return None
    return refresh_wanted(ctx, instance_id)


def refresh_wanted(ctx: AppContext, instance_id: str) -> dict[str, int]:
    """Synchroniseert wanted_items met de cache. Idempotent."""
    row = ctx.db.fetch_one("SELECT MAX(id) AS id FROM library_sync_runs WHERE instance_id=?",
                           (instance_id,))
    ctx.state_set(f"wanted_refresh:{instance_id}",
                  {"sync_run": int(row["id"] or 0) if row else 0})
    current = _current_sets(ctx, instance_id)
    now = utcnow()
    season_missing: dict[tuple[int, int], int] = {}
    for item in current:
        if item["kind"] == "episode" and item["reason"] == "missing":
            key = (item["item_provider_id"], item["season_number"])
            season_missing[key] = season_missing.get(key, 0) + 1
    season_sizes = {(r["series_provider_id"], r["season_number"]): int(r["n"])
                    for r in ctx.db.fetch_all(
                        "SELECT series_provider_id, season_number, COUNT(*) AS n FROM "
                        "lib_episodes WHERE instance_id=? GROUP BY 1,2", (instance_id,))}
    cache: dict[str, Any] = {}
    seen: set[tuple[str, int, str]] = set()
    # Alles in één keer inlezen en alleen echt gewijzigde rijen schrijven (was: per open
    # rij een SELECT + UPDATE, ~1,5 s per keer bij 67k open items, ook als niets veranderde)
    existing = {(r["kind"], int(r["provider_id"]), r["reason"]): r for r in ctx.db.fetch_all(
        "SELECT id, kind, provider_id, reason, status, title, item_title, season_number, "
        "episode_number, absolute_episode_number, air_date_utc, priority, item_provider_id "
        "FROM wanted_items WHERE instance_id=?", (instance_id,))}
    inserts: list[tuple[Any, ...]] = []
    reopen: list[tuple[Any, ...]] = []
    updates: list[tuple[Any, ...]] = []
    for item in current:
        key = (item["kind"], int(item["provider_id"]), item["reason"])
        seen.add(key)
        prio = _priority(ctx, item, season_missing, season_sizes, cache, instance_id)
        fields = (item["title"], item["item_title"], item.get("season_number"),
                  item.get("episode_number"), item.get("absolute_episode_number"),
                  item.get("air_date_utc"), prio, int(item["item_provider_id"]))
        row = existing.get(key)
        if row is None:
            inserts.append((instance_id, item["kind"], int(item["provider_id"]),
                            int(item["item_provider_id"]), item["reason"], item["title"],
                            item["item_title"], item.get("season_number"),
                            item.get("episode_number"), item.get("absolute_episode_number"),
                            item.get("air_date_utc"), prio, now, now))
            continue
        if row["status"] == "resolved":
            reopen.append((row["id"],))
        have = (row["title"], row["item_title"], row["season_number"], row["episode_number"],
                row["absolute_episode_number"], row["air_date_utc"], row["priority"],
                row["item_provider_id"])
        if have != fields or row["status"] == "resolved":
            updates.append((*fields, now, row["id"]))
    opened, reopened = len(inserts), len(reopen)
    with ctx.db.transaction() as conn:
        conn.executemany(
            "INSERT INTO wanted_items(instance_id,kind,provider_id,item_provider_id,"
            "reason,title,item_title,season_number,episode_number,"
            "absolute_episode_number,air_date_utc,status,priority,first_seen_at,"
            "updated_at) VALUES (?,?,?,?,?,?,?,?,?,?,?,'wanted',?,?,?)", inserts)
        conn.executemany(
            "UPDATE wanted_items SET status='wanted', attempts=0, next_search_at=NULL,"
            " resolved_at=NULL, resolution=NULL, last_outcome=NULL WHERE id=?", reopen)
        conn.executemany(
            "UPDATE wanted_items SET title=?, item_title=?, season_number=?, "
            "episode_number=?, absolute_episode_number=?, air_date_utc=?, priority=?, "
            "item_provider_id=?, updated_at=? WHERE id=?", updates)
        resolved = 0
        gone = [r for k, r in existing.items() if k not in seen and r["status"] != "resolved"]
        for row in gone:
            resolution = _resolution(conn, instance_id, dict(row))
            conn.execute("UPDATE wanted_items SET status='resolved', resolved_at=?, resolution=?,"
                         " updated_at=? WHERE id=?", (now, resolution, now, row["id"]))
            resolved += 1
    return {"open": len(current), "opened": opened, "reopened": reopened, "resolved": resolved}


def _resolution(conn: Any, instance_id: str, row: dict[str, Any]) -> str:
    if row["kind"] == "episode":
        ep = conn.execute("SELECT e.has_file, e.monitored, e.air_date_utc, l.monitored AS sm "
                          "FROM lib_episodes e JOIN lib_items l ON l.instance_id=e.instance_id "
                          "AND l.kind='series' AND l.provider_id=e.series_provider_id "
                          "WHERE e.instance_id=? AND e.provider_id=?",
                          (instance_id, row["provider_id"])).fetchone()
        if ep is None:
            return "removed"
        if ep["has_file"]:
            return "available" if row["reason"] == "missing" else "upgraded"
        if not ep["monitored"] or not ep["sm"]:
            return "unmonitored"
        return "not_aired"
    mv = conn.execute("SELECT has_file, monitored, is_available, removed_at FROM lib_items "
                      "WHERE instance_id=? AND kind='movie' AND provider_id=?",
                      (instance_id, row["provider_id"])).fetchone()
    if mv is None or mv["removed_at"]:
        return "removed"
    if mv["has_file"]:
        return "available" if row["reason"] == "missing" else "upgraded"
    if not mv["monitored"]:
        return "unmonitored"
    return "not_available"


# -- lezen ------------------------------------------------------------------------------------
WANTED_SORTS = {
    "priority": "w.priority DESC, w.air_date_utc DESC",
    "air_date": "w.air_date_utc DESC",
    "last_searched": "w.last_searched_at IS NULL DESC, w.last_searched_at",
    "title": "w.item_title, w.season_number, w.episode_number",
    "attempts": "w.attempts DESC",
}


def list_wanted(ctx: AppContext, *, instance_id: str | None = None,
                media_type: str | None = None, kind: str | None = None,
                reason: str | None = None, status: str | None = "open", q: str | None = None,
                season: int | None = None, series_id: int | None = None,
                reason_code: str | None = None, searched_before: str | None = None,
                searched_after: str | None = None, never_searched: bool | None = None,
                page: int = 1, page_size: int = 50, sort: str = "priority") -> dict:
    sql = "FROM wanted_items w JOIN instances i ON i.id=w.instance_id WHERE 1=1"
    params: list[Any] = []
    if status == "open":
        sql += " AND w.status IN ('wanted','searching','unavailable')"
    elif status:
        sql += " AND w.status=?"
        params.append(status)
    for col, val in (("w.instance_id", instance_id), ("i.media_type", media_type),
                     ("w.kind", kind), ("w.reason", reason), ("w.season_number", season),
                     ("w.item_provider_id", series_id),
                     ("w.last_reason_code", None if reason_code == UNDIAGNOSED else reason_code)):
        if val is not None and val != "":
            sql += f" AND {col}=?"
            params.append(val)
    if reason_code == UNDIAGNOSED:
        # 'Nog niet onderzocht' = nog geen oorzaak bekend (was: lege lijst, rapport H6)
        sql += " AND w.last_reason_code IS NULL"
    if q:
        sql += " AND w.title LIKE ?"
        params.append(f"%{q}%")
    if searched_before:
        sql += " AND w.last_searched_at<?"
        params.append(searched_before)
    if searched_after:
        sql += " AND w.last_searched_at>=?"
        params.append(searched_after)
    if never_searched is True:
        sql += " AND w.last_searched_at IS NULL"
    elif never_searched is False:
        sql += " AND w.last_searched_at IS NOT NULL"
    total_row = ctx.db.fetch_one("SELECT COUNT(*) AS n " + sql, params)
    order = WANTED_SORTS.get(sort, WANTED_SORTS["priority"])
    rows = ctx.db.fetch_dicts(
        "SELECT w.*, i.media_type, i.name AS instance_name " + sql + f" ORDER BY {order} "
        "LIMIT ? OFFSET ?", [*params, page_size, (page - 1) * page_size])
    for row in rows:
        row["keep_monitoring"] = bool(row["keep_monitoring"])
    _attach_reason_detail(ctx, rows)
    return {"page": page, "page_size": page_size, "total": int(total_row["n"]) if total_row
            else 0, "items": rows}


def _attach_reason_detail(ctx: AppContext, rows: list[dict[str, Any]]) -> None:
    """Rapport 2 / N8: per item de laatste concrete reden (bv. de importmelding van
    Sonarr/Radarr, vertaald) i.p.v. alleen 'Download voltooid, import mislukt'."""
    from mediaguard.activity.api import import_message_nl

    ids = [int(r["id"]) for r in rows]
    if not ids:
        return
    marks = ",".join("?" for _ in ids)
    latest = {int(r["wanted_id"]): r for r in ctx.db.fetch_all(
        f"SELECT d.wanted_id, d.reasons_json FROM missing_diagnostics d JOIN (SELECT wanted_id, "  # noqa: S608
        f"MAX(id) AS mid FROM missing_diagnostics WHERE wanted_id IN ({marks}) GROUP BY "
        f"wanted_id) m ON m.mid=d.id", ids)}
    for row in rows:
        d = latest.get(int(row["id"]))
        reasons = loads(d["reasons_json"], []) if d else []
        first = next((x for x in reasons if x.get("code") == row.get("last_reason_code")),
                     reasons[0] if reasons else None)
        msg = str((first or {}).get("message_nl") or "")
        if msg.startswith("Import mislukt: "):
            msg = "Import mislukt: " + import_message_nl(msg[len("Import mislukt: "):])
        from mediaguard.textfmt import plain_nl

        row["reason_detail_nl"] = plain_nl(msg) or None
        row["status_nl"] = wanted_status_nl(row)


SEARCH_RECENT_MINUTES = 30


def wanted_status_nl(row: dict[str, Any]) -> str:
    """'Wordt gezocht' alleen bij een zoekpoging van de laatste minuten; anders wanneer de
    volgende poging is (rapport 4, S15: 'Wordt gezocht' bij 'laatst gezocht 10 uur geleden')."""
    from datetime import UTC, datetime, timedelta

    from mediaguard.textfmt import datetime_nl

    status = str(row.get("status") or "")
    if status != "searching":
        return str(WANTED_STATUS_NL.get(status, status))
    last = row.get("last_searched_at")
    try:
        at = datetime.fromisoformat(str(last).replace("Z", "+00:00")) if last else None
    except ValueError:
        at = None
    if at is not None and at.tzinfo is None:
        at = at.replace(tzinfo=UTC)
    if at is not None and datetime.now(UTC) - at <= timedelta(minutes=SEARCH_RECENT_MINUTES):
        return "Wordt gezocht"
    nxt = row.get("next_search_at")
    return f"Volgende zoekpoging {datetime_nl(nxt)}" if nxt else "Wacht op volgende zoekpoging"


def get_wanted(ctx: AppContext, wanted_id: int) -> dict[str, Any] | None:
    row = ctx.db.fetch_one("SELECT w.*, i.media_type, i.name AS instance_name, i.app FROM "
                           "wanted_items w JOIN instances i ON i.id=w.instance_id WHERE w.id=?",
                           (wanted_id,))
    return None if row is None else dict(row)


def wanted_detail(ctx: AppContext, wanted_id: int) -> dict[str, Any] | None:
    row = get_wanted(ctx, wanted_id)
    if row is None:
        return None
    row["keep_monitoring"] = bool(row["keep_monitoring"])
    attempts = ctx.db.fetch_dicts("SELECT * FROM backfill_attempts WHERE wanted_id=? "
                                  "ORDER BY id DESC LIMIT 50", (wanted_id,))
    for a in attempts:
        a["candidates"] = loads(a.pop("candidates_json"), [])
        a["rejections"] = loads(a.pop("rejections_json"), [])
    row["attempts_log"] = attempts
    diag = ctx.db.fetch_dicts("SELECT id, ts, primary_code, interactive FROM "
                              "missing_diagnostics WHERE wanted_id=? ORDER BY id DESC LIMIT 20",
                              (wanted_id,))
    row["diagnoses"] = diag
    overrides = ctx.db.fetch_dicts("SELECT * FROM wanted_overrides WHERE wanted_id=? "
                                   "ORDER BY id DESC", (wanted_id,))
    for o in overrides:
        o["relaxations"] = loads(o.pop("relaxations_json"), [])
        o["result"] = loads(o.pop("result_json"), None)
    row["overrides"] = overrides
    row["settings_scope"] = settings_scope_chain(row)
    return row


# -- acties -------------------------------------------------------------------------------------
ACTION_EXPLANATIONS = {
    "ignore": "Het item wordt niet meer automatisch gezocht en telt niet mee als probleem. "
              "De monitorstatus in Sonarr/Radarr blijft ongewijzigd; RSS kan het nog steeds "
              "oppakken. Ongedaan maken met 'Opnieuw volgen'.",
    "unignore": "Het item doet weer mee aan de backfillplanning.",
    "keep_monitoring": "Het item blijft gezocht worden, ook als er langdurig geen geschikte "
                       "release is. Na herhaalde mislukte pogingen wordt met het lange "
                       "'niet beschikbaar'-interval opnieuw gezocht; regels worden niet "
                       "versoepeld.",
}


def apply_action(ctx: AppContext, wanted_id: int, action: str, reason: str = "") -> dict:
    row = get_wanted(ctx, wanted_id)
    if row is None:
        raise KeyError(wanted_id)
    now = utcnow()
    if action == "ignore":
        ctx.db.execute("UPDATE wanted_items SET status='ignored', ignored_reason=?, "
                       "updated_at=? WHERE id=?", (reason or None, now, wanted_id))
    elif action == "unignore":
        ctx.db.execute("UPDATE wanted_items SET status='wanted', ignored_reason=NULL, "
                       "next_search_at=NULL, updated_at=? WHERE id=? AND status='ignored'",
                       (now, wanted_id))
    elif action == "keep_monitoring":
        ctx.db.execute("UPDATE wanted_items SET keep_monitoring=1, ignored_reason=NULL, "
                       "status=CASE WHEN status IN ('ignored','unavailable') THEN 'wanted' "
                       "ELSE status END, updated_at=? WHERE id=?", (now, wanted_id))
    else:
        raise ValueError(f"onbekende actie: {action}")
    new = get_wanted(ctx, wanted_id) or {}
    return {"id": wanted_id, "action": action, "old_status": row["status"],
            "status": new.get("status"), "explanation_nl": ACTION_EXPLANATIONS[action]}


# -- KPI's -----------------------------------------------------------------------------------
UNDIAGNOSED = "niet-gediagnosticeerd"

def wanted_kpis(ctx: AppContext, instance_id: str | None = None) -> dict[str, Any]:
    where = " AND instance_id=?" if instance_id else ""
    p: tuple[Any, ...] = (instance_id,) if instance_id else ()
    out: dict[str, Any] = {}
    row = ctx.db.fetch_one(
        "SELECT SUM(CASE WHEN reason='missing' AND status IN ('wanted','searching',"
        "'unavailable') THEN 1 ELSE 0 END) AS missing, SUM(CASE WHEN reason='cutoff' AND status "
        "IN ('wanted','searching','unavailable') THEN 1 ELSE 0 END) AS cutoff, SUM(CASE WHEN "
        "status='unavailable' THEN 1 ELSE 0 END) AS unavailable, SUM(CASE WHEN status='ignored' "
        "THEN 1 ELSE 0 END) AS ignored FROM wanted_items WHERE 1=1" + where, p)
    out.update({k: int((row[k] if row else 0) or 0) for k in
                ("missing", "cutoff", "unavailable", "ignored")})
    for days in (7, 30, 90):
        since = _z(datetime.now(UTC) - timedelta(days=days))
        r = ctx.db.fetch_one(
            "SELECT COUNT(*) AS n FROM wanted_items WHERE reason='missing' AND status IN "
            "('wanted','searching','unavailable') AND air_date_utc>=?" + where, (since, *p))
        out[f"missing_aired_{days}d"] = int(r["n"]) if r else 0
        resolved_since = (datetime.now(UTC) - timedelta(days=days)).isoformat(timespec="seconds")
        r = ctx.db.fetch_one(
            "SELECT COUNT(*) AS n FROM wanted_items WHERE status='resolved' AND resolution IN "
            "('available','upgraded') AND resolved_at>=?" + where, (resolved_since, *p))
        out[f"resolved_{days}d"] = int(r["n"]) if r else 0
    # Alleen ontbrekende items (was: ontbrekend + onder kwaliteitsgrens door elkaar, zodat
    # 'niet-gediagnosticeerd · 8.223' naast 'Ontbrekend 869' stond). Kwaliteit apart.
    dist: dict[str, dict[str, dict[str, int]]] = {"missing": {}, "cutoff": {}}
    for r in ctx.db.fetch_all(
            "SELECT reason, last_reason_code AS code, kind, COUNT(*) AS n FROM wanted_items "
            "WHERE status IN ('wanted','searching','unavailable')" + where +
            " GROUP BY reason, last_reason_code, kind", p):
        code = r["code"] or UNDIAGNOSED
        slot = dist[r["reason"]].setdefault(code, {"total": 0, "episodes": 0, "movies": 0})
        slot["total"] += int(r["n"])
        slot["episodes" if r["kind"] == "episode" else "movies"] += int(r["n"])
    out["reason_distribution"] = {k: v["total"] for k, v in dist["missing"].items()}
    out["reason_distribution_cutoff"] = {k: v["total"] for k, v in dist["cutoff"].items()}
    # per reden ook afleveringen/films apart ('1 film' i.p.v. '1 afl.')
    out["reason_distribution_detail"] = dist
    out["undiagnosed"] = {"missing": dist["missing"].get(UNDIAGNOSED, {}).get("total", 0),
                          "cutoff": dist["cutoff"].get(UNDIAGNOSED, {}).get("total", 0),
                          # wordt automatisch op de achtergrond onderzocht: geen gebruikersactie
                          "automatic": True}
    out["upcoming"] = upcoming_counts(ctx, instance_id)
    # één lijst voor 'Actie nodig': alleen oorzaken waar de gebruiker iets mee moet,
    # met Nederlandse label, uitleg en waar je het oplost
    from mediaguard.wanted.diagnosis import REASONS

    actions: list[dict[str, Any]] = []
    for reason_kind in ("missing", "cutoff"):
        for code, c in dist[reason_kind].items():
            meta = REASONS.get(code) or REASONS["unknown"]
            if not meta["needs_user"]:
                continue
            actions.append({"code": code, "reason": reason_kind, "label_nl": meta["label_nl"],
                            "fix_nl": meta["fix_nl"],
                            "href": meta["href"] + ("&reason=cutoff" if reason_kind == "cutoff"
                                                    and "?reason_code=" in meta["href"]
                                                    else ""), **c})
    actions.sort(key=lambda a: -a["total"])
    out["needs_user"] = {"total": sum(a["total"] for a in actions), "reasons": actions}
    out["automatic_total"] = sum(c["total"] for k in ("missing", "cutoff")
                                 for code, c in dist[k].items()
                                 if not (REASONS.get(code) or REASONS["unknown"])["needs_user"])
    since30 = (datetime.now(UTC) - timedelta(days=30)).isoformat(timespec="seconds")
    r = ctx.db.fetch_one(
        "SELECT SUM(CASE WHEN status='grabbed' THEN 1 ELSE 0 END) AS ok, SUM(CASE WHEN status IN "
        "('grabbed','no_candidate') THEN 1 ELSE 0 END) AS done, SUM(CASE WHEN status='error' "
        "THEN 1 ELSE 0 END) AS errors, SUM(counts_quota) AS searches FROM backfill_attempts "
        "WHERE ts>=?" + where, (since30, *p))
    done = int((r["done"] if r else 0) or 0)
    out["backfill_30d"] = {
        "searches": int((r["searches"] if r else 0) or 0), "completed": done,
        "grabbed": int((r["ok"] if r else 0) or 0), "errors": int((r["errors"] if r else 0) or 0),
        "success_rate": round(int(r["ok"] or 0) / done, 4) if r and done else None}
    r = ctx.db.fetch_one(
        "SELECT COUNT(*) AS n FROM wanted_items WHERE last_reason_code='import-failed' AND "
        "status IN ('wanted','searching','unavailable')" + where, p)
    out["import_failed"] = int(r["n"]) if r else 0
    # Rapport 2 / N8: 'Geblokkeerd' telt op uit zijn onderdelen (was 198 = '0 + 0')
    det = dist["missing"]
    parts = {
        "import_failed": det.get("import-failed", {}).get("total", 0),
        "client_error": sum(det.get(c, {}).get("total", 0) for c in (
            "download-client-failed", "download-client-missing", "stuck-queue")),
        "unavailable": out["unavailable"],
        "ignored": out["ignored"],
    }
    out["blocked"] = {"total": sum(parts.values()), **parts,
                      "labels_nl": {"import_failed": "import mislukt",
                                    "client_error": "downloadclient-fout",
                                    "unavailable": "niet beschikbaar",
                                    "ignored": "uitgesloten"}}
    # één getal voor 'Ontbrekend' overal (Overzicht, Ontbrekend & herstel)
    out["missing_total"] = out["missing"]
    return out
