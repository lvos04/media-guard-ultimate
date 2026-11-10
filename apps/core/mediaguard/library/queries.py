"""Leesmodellen over de bibliotheekcache: overzicht, seizoenmatrix (D4), kalender, KPI's.

Classificatie (zelfde semantiek als Sonarr/Radarr 'wanted/missing'):
- *ontbrekend* = gemonitorde aflevering van een gemonitorde serie, uitgezonden
  (airDateUtc <= nu) en zonder bestand. Film: gemonitord, ``isAvailable`` en geen bestand.
- *binnenkort* = gemonitord, nog niet uitgezonden of zonder datum (nooit 'ontbrekend').
- *niet gemonitord* telt NOOIT als ontbrekend (R4).
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from typing import TYPE_CHECKING, Any

from mediaguard.db import loads
from mediaguard.library.sync import iso_z, now_z

if TYPE_CHECKING:
    from mediaguard.context import AppContext

EP_STATS_SQL = """
SELECT e.instance_id, e.series_provider_id,
  COUNT(*) AS total,
  SUM(e.has_file) AS available,
  SUM(CASE WHEN e.has_file=0 AND e.monitored=1 AND l.monitored=1
      AND e.air_date_utc IS NOT NULL AND e.air_date_utc<=:now THEN 1 ELSE 0 END) AS missing,
  SUM(CASE WHEN e.has_file=0 AND e.monitored=1 AND l.monitored=1
      AND (e.air_date_utc IS NULL OR e.air_date_utc>:now) THEN 1 ELSE 0 END) AS upcoming,
  SUM(CASE WHEN e.has_file=0 AND (e.monitored=0 OR l.monitored=0) THEN 1 ELSE 0 END)
      AS unmonitored,
  SUM(CASE WHEN e.has_file=1 AND f.quality_cutoff_not_met=1 AND e.monitored=1
      AND l.monitored=1 THEN 1 ELSE 0 END) AS cutoff_unmet
FROM lib_episodes e
JOIN lib_items l ON l.instance_id=e.instance_id AND l.kind='series'
  AND l.provider_id=e.series_provider_id
LEFT JOIN lib_files f ON f.instance_id=e.instance_id AND f.kind='episode'
  AND f.provider_id=e.episode_file_id
WHERE l.removed_at IS NULL {where}
GROUP BY e.instance_id, e.series_provider_id
"""


def episode_state(ep: dict[str, Any], series_monitored: bool, now: str | None = None,
                  cutoff_unmet: bool = False) -> str:
    now = now or now_z()
    if ep.get("has_file"):
        return "cutoff_unmet" if cutoff_unmet and ep.get("monitored") and series_monitored \
            else "available"
    if not ep.get("monitored") or not series_monitored:
        return "unmonitored"
    air = ep.get("air_date_utc")
    if not air or air > now:
        return "upcoming"
    return "missing"


def movie_state(item: dict[str, Any], cutoff_unmet: bool = False) -> str:
    if item.get("has_file"):
        return "cutoff_unmet" if cutoff_unmet and item.get("monitored") else "available"
    if not item.get("monitored"):
        return "unmonitored"
    if not item.get("is_available"):
        return "upcoming"
    return "missing"


def series_stats(ctx: AppContext, instance_id: str | None = None,
                 series_provider_id: int | None = None,
                 series_ids: list[int] | None = None) -> dict[tuple[str, int], dict]:
    where = ""
    params: dict[str, Any] = {"now": now_z()}
    if series_ids is not None:
        # alleen de series op deze pagina (was: hele bibliotheek per lijstverzoek, ~60×)
        names = []
        for n, sid in enumerate(series_ids):
            params[f"s{n}"] = int(sid)
            names.append(f":s{n}")
        where += f" AND e.series_provider_id IN ({','.join(names) or '-1'})"
    if instance_id:
        where += " AND e.instance_id=:iid"
        params["iid"] = instance_id
    if series_provider_id is not None:
        where += " AND e.series_provider_id=:sid"
        params["sid"] = series_provider_id
    rows = ctx.db.fetch_dicts(EP_STATS_SQL.format(where=where), params)
    return {(r["instance_id"], int(r["series_provider_id"])): r for r in rows}


# Status uit Sonarr/Radarr in gewone taal (rapport 5, T10: 'uitgebracht'/'released')
ITEM_STATUS_NL = {"released": "Uitgebracht", "announced": "Aangekondigd",
                  "incinemas": "In de bioscoop", "tba": "Nog niet bekend",
                  "deleted": "Verwijderd bij TMDB", "continuing": "Loopt nog",
                  "ended": "Afgelopen", "upcoming": "Binnenkort"}


def _decorate(ctx: AppContext, items: list[dict[str, Any]]) -> list[dict[str, Any]]:
    stats = {}
    per_instance: dict[str, list[int]] = {}
    for i in items:
        if i["kind"] == "series":
            per_instance.setdefault(i["instance_id"], []).append(int(i["provider_id"]))
    for iid, ids in per_instance.items():
        stats.update(series_stats(ctx, iid, series_ids=ids) if len(ids) <= 500
                     else series_stats(ctx, iid))
    # cutoff-vlag van alle filmbestanden in één query (was: één query per film)
    movie_files: dict[str, list[int]] = {}
    for i in items:
        if i["kind"] == "movie" and i.get("file_id"):
            movie_files.setdefault(i["instance_id"], []).append(int(i["file_id"]))
    cutoff: set[tuple[str, int]] = set()
    for iid, fids in movie_files.items():
        rows = ctx.db.fetch_all(
            "SELECT provider_id FROM lib_files WHERE instance_id=? AND kind='movie' AND "
            "quality_cutoff_not_met=1", (iid,)) if len(fids) > 500 else ctx.db.fetch_all(
            f"SELECT provider_id FROM lib_files WHERE instance_id=? AND kind='movie' AND "
            f"quality_cutoff_not_met=1 AND provider_id IN ({','.join('?' * len(fids))})",
            (iid, *fids))
        cutoff.update((iid, int(r["provider_id"])) for r in rows)
    for item in items:
        item["tags"] = loads(item.pop("tags_json", "[]"), [])
        item["monitored"] = bool(item["monitored"])
        item["status_nl"] = ITEM_STATUS_NL.get(str(item.get("status") or "").lower(),
                                               str(item.get("status") or "").capitalize()
                                               or None)
        if item["kind"] == "series":
            s = stats.get((item["instance_id"], int(item["provider_id"])), {})
            item["stats"] = {k: int(s.get(k) or 0) for k in
                             ("total", "available", "missing", "upcoming", "unmonitored",
                              "cutoff_unmet")}
            if not s and item.get("episode_count"):
                # afleveringen nog niet (volledig) gesynchroniseerd: Sonarr's eigen telling
                # gebruiken in plaats van een misleidende 0 / 0 'compleet'
                total = int(item.get("episode_count") or 0)
                have = min(total, int(item.get("episode_file_count") or 0))
                item["stats"].update({"total": total, "available": have,
                                      "missing": total - have})
                item["stats_estimated"] = True
        else:
            state = movie_state(item, (item["instance_id"], int(item.get("file_id") or 0))
                                in cutoff)
            item["state"] = state
            item["stats"] = {"total": 1, "available": int(state in ("available",
                                                                    "cutoff_unmet")),
                             "missing": int(state == "missing"),
                             "upcoming": int(state == "upcoming"),
                             "unmonitored": int(state == "unmonitored"),
                             "cutoff_unmet": int(state == "cutoff_unmet")}
    return items


SORTS = {"title": "l.sort_title", "added": "l.added_at", "year": "l.year",
         "size": "l.size_on_disk", "instance": "l.instance_id, l.sort_title"}


def list_items(ctx: AppContext, *, instance_id: str | None = None, kind: str | None = None,
               media_type: str | None = None, q: str | None = None,
               monitored: bool | None = None, state: str | None = None, page: int = 1,
               page_size: int = 50, sort: str = "title",
               descending: bool = False) -> dict[str, Any]:
    sql = ("FROM lib_items l JOIN instances i ON i.id=l.instance_id "
           "WHERE l.removed_at IS NULL")
    params: list[Any] = []
    if instance_id:
        sql += " AND l.instance_id=?"
        params.append(instance_id)
    if kind:
        sql += " AND l.kind=?"
        params.append(kind)
    if media_type:
        sql += " AND i.media_type=?"
        params.append(media_type)
    if q:
        sql += " AND (l.title LIKE ? OR l.sort_title LIKE ?)"
        params += [f"%{q}%", f"%{q.lower()}%"]
    if monitored is not None:
        sql += " AND l.monitored=?"
        params.append(int(monitored))
    order = SORTS.get(sort, SORTS["title"]) + (" DESC" if descending else "")
    select = "SELECT l.*, i.media_type, i.app, i.name AS instance_name "
    if state:  # filter na classificatie (kleine sets; cache is lokaal)
        rows = _decorate(ctx, ctx.db.fetch_dicts(select + sql + f" ORDER BY {order}", params))
        rows = [r for r in rows if _matches_state(r, state)]
        total = len(rows)
        rows = rows[(page - 1) * page_size: page * page_size]
    else:
        total_row = ctx.db.fetch_one("SELECT COUNT(*) AS n " + sql, params)
        total = int(total_row["n"]) if total_row else 0
        rows = _decorate(ctx, ctx.db.fetch_dicts(
            select + sql + f" ORDER BY {order} LIMIT ? OFFSET ?",
            [*params, page_size, (page - 1) * page_size]))
    return {"page": page, "page_size": page_size, "total": total, "items": rows}


def _matches_state(item: dict[str, Any], state: str) -> bool:
    s = item["stats"]
    if state == "missing":
        return s["missing"] > 0
    if state == "complete":
        return s["missing"] == 0 and s["upcoming"] == 0 and s["total"] > 0
    if state == "upcoming":
        return s["upcoming"] > 0
    if state == "cutoff_unmet":
        return s["cutoff_unmet"] > 0
    if state == "unmonitored":
        return not item["monitored"]
    return True


def get_item(ctx: AppContext, item_id: int) -> dict[str, Any] | None:
    rows = ctx.db.fetch_dicts(
        "SELECT l.*, i.media_type, i.app, i.name AS instance_name FROM lib_items l "
        "JOIN instances i ON i.id=l.instance_id WHERE l.id=?", (item_id,))
    if not rows:
        return None
    item = _decorate(ctx, rows)[0]
    if item["kind"] == "series":
        item["seasons"] = season_matrix(ctx, item)["seasons"]
    else:
        file = ctx.db.fetch_one("SELECT * FROM lib_files WHERE instance_id=? AND kind='movie' "
                                "AND provider_id=?", (item["instance_id"], item["file_id"] or -1))
        item["file"] = _file_dict(file)
        item["wanted"] = ctx.db.fetch_dicts(
            "SELECT id,reason,status,attempts,last_searched_at,next_search_at,last_reason_code "
            "FROM wanted_items WHERE instance_id=? AND kind='movie' AND provider_id=?",
            (item["instance_id"], item["provider_id"]))
    return item


def _file_dict(row: Any) -> dict[str, Any] | None:
    if row is None:
        return None
    data = dict(row)
    data["languages"] = loads(data.pop("languages_json", "[]"), [])
    data["media_info"] = loads(data.pop("media_info_json", "{}"), {})
    data["quality_cutoff_not_met"] = bool(data["quality_cutoff_not_met"])
    return data


def season_matrix(ctx: AppContext, item: dict[str, Any],
                  queue_episode_ids: set[int] | None = None) -> dict[str, Any]:
    """Seizoenmatrix (D4): per seizoen tellingen + per aflevering een status-cel."""
    iid, sid = item["instance_id"], int(item["provider_id"])
    now = now_z()
    seasons = {r["season_number"]: dict(r) for r in ctx.db.fetch_all(
        "SELECT * FROM lib_seasons WHERE instance_id=? AND series_provider_id=?", (iid, sid))}
    eps = ctx.db.fetch_dicts(
        "SELECT e.*, f.quality_cutoff_not_met, f.quality FROM lib_episodes e "
        "LEFT JOIN lib_files f ON f.instance_id=e.instance_id AND f.kind='episode' "
        "AND f.provider_id=e.episode_file_id WHERE e.instance_id=? AND e.series_provider_id=? "
        "ORDER BY e.season_number, e.episode_number", (iid, sid))
    wanted = {(r["provider_id"]): r for r in ctx.db.fetch_dicts(
        "SELECT provider_id, status, last_reason_code, attempts FROM wanted_items "
        "WHERE instance_id=? AND kind='episode' AND item_provider_id=? AND reason='missing'",
        (iid, sid))}
    out: dict[int, dict[str, Any]] = {}
    for ep in eps:
        state = episode_state(ep, bool(item["monitored"]), now,
                              bool(ep.get("quality_cutoff_not_met")))
        w = wanted.get(ep["provider_id"])
        if queue_episode_ids and ep["provider_id"] in queue_episode_ids and state == "missing":
            state = "downloading"
        elif w and state == "missing" and w["status"] in ("ignored", "unavailable"):
            state = w["status"]
        number = int(ep["season_number"])
        season = out.setdefault(number, {
            "season_number": number,
            "monitored": bool(seasons.get(number, {}).get("monitored", False)),
            "counts": {k: 0 for k in ("total", "available", "missing", "upcoming",
                                      "unmonitored", "cutoff_unmet", "downloading",
                                      "ignored", "unavailable")},
            "episodes": []})
        season["counts"]["total"] += 1
        bucket = "available" if state == "cutoff_unmet" else state
        season["counts"][bucket] = season["counts"].get(bucket, 0) + 1
        if state == "cutoff_unmet":
            season["counts"]["cutoff_unmet"] += 1
        season["episodes"].append({
            "episode_id": ep["provider_id"], "episode_number": ep["episode_number"],
            "absolute_episode_number": ep["absolute_episode_number"], "title": ep["title"],
            "air_date_utc": ep["air_date_utc"], "monitored": bool(ep["monitored"]),
            "has_file": bool(ep["has_file"]), "quality": ep.get("quality"), "state": state,
            "wanted_status": w["status"] if w else None,
            "reason_code": w["last_reason_code"] if w else None,
        })
    for number, meta in seasons.items():
        out.setdefault(number, {"season_number": number, "monitored": bool(meta["monitored"]),
                                "counts": {"total": 0}, "episodes": []})
    result = []
    for number in sorted(out):
        season = out[number]
        c = season["counts"]
        expected = c.get("total", 0) - c.get("unmonitored", 0) - c.get("upcoming", 0)
        season["expected_aired"] = expected
        season["completeness"] = round(c.get("available", 0) / expected, 4) if expected else None
        result.append(season)
    return {"instance_id": iid, "series_id": sid, "title": item.get("title"),
            "seasons": result,
            "note_nl": "Toekomstige afleveringen tellen niet als ontbrekende download."}


# -- kalender -----------------------------------------------------------------------------------
def calendar(ctx: AppContext, start: str, end: str, *, instance_id: str | None = None,
             include_unmonitored: bool = False) -> list[dict[str, Any]]:
    s, e = iso_z(start), iso_z(end)
    if not s or not e:
        raise ValueError("start/end moeten ISO-datums zijn")
    out: list[dict[str, Any]] = []
    sql = ("SELECT e.*, l.title AS series_title, l.id AS lib_item_id, l.monitored AS "
           "series_monitored, i.media_type, i.name AS instance_name FROM lib_episodes e "
           "JOIN lib_items l ON l.instance_id=e.instance_id AND l.kind='series' AND "
           "l.provider_id=e.series_provider_id JOIN instances i ON i.id=e.instance_id "
           "WHERE l.removed_at IS NULL AND e.air_date_utc>=? AND e.air_date_utc<?")
    params: list[Any] = [s, e]
    if instance_id:
        sql += " AND e.instance_id=?"
        params.append(instance_id)
    if not include_unmonitored:
        sql += " AND e.monitored=1 AND l.monitored=1"
    now = now_z()
    for ep in ctx.db.fetch_dicts(sql + " ORDER BY e.air_date_utc", params):
        out.append({
            "type": "episode", "instance_id": ep["instance_id"],
            "instance_name": ep["instance_name"], "media_type": ep["media_type"],
            "lib_item_id": ep["lib_item_id"], "series_id": ep["series_provider_id"],
            "episode_id": ep["provider_id"], "title": ep["series_title"],
            "episode_title": ep["title"], "season_number": ep["season_number"],
            "episode_number": ep["episode_number"],
            "absolute_episode_number": ep["absolute_episode_number"],
            "date": ep["air_date_utc"], "has_file": bool(ep["has_file"]),
            "monitored": bool(ep["monitored"] and ep["series_monitored"]),
            "state": episode_state(ep, bool(ep["series_monitored"]), now),
        })
    msql = ("SELECT l.*, i.media_type, i.name AS instance_name FROM lib_items l JOIN instances i "
            "ON i.id=l.instance_id WHERE l.kind='movie' AND l.removed_at IS NULL")
    mparams: list[Any] = []
    if instance_id:
        msql += " AND l.instance_id=?"
        mparams.append(instance_id)
    if not include_unmonitored:
        msql += " AND l.monitored=1"
    for m in ctx.db.fetch_dicts(msql, mparams):
        for field, label in (("in_cinemas", "bioscoop"), ("digital_release", "digitaal"),
                             ("physical_release", "fysiek")):
            date = m.get(field)
            if date and s <= date < e:
                out.append({
                    "type": "movie", "instance_id": m["instance_id"],
                    "instance_name": m["instance_name"], "media_type": m["media_type"],
                    "lib_item_id": m["id"], "movie_id": m["provider_id"], "title": m["title"],
                    "release_type": field, "release_label_nl": label, "date": date,
                    "has_file": bool(m["has_file"]), "monitored": bool(m["monitored"]),
                    "state": movie_state(m),
                })
    out.sort(key=lambda x: (x["date"], x["title"]))
    return out


# -- KPI's ----------------------------------------------------------------------------------------
def _ago(days: int) -> str:
    return (datetime.now(UTC) - timedelta(days=days)).strftime("%Y-%m-%dT%H:%M:%SZ")


KPI_CACHE_SECONDS = 60


def kpis(ctx: AppContext, instance_id: str | None = None) -> dict[str, Any]:
    """KPI's over de hele bibliotheek (~0,3 s bij grote bibliotheken): een minuut gecachet
    en direct opnieuw na een nieuwe sync."""
    import time

    row = ctx.db.fetch_one("SELECT MAX(id) AS id FROM library_sync_runs")
    key = (instance_id or "*", int(row["id"] or 0) if row else 0)
    cache = ctx.services.setdefault("library_kpi_cache", {})
    hit = cache.get(key)
    if hit and time.monotonic() - hit[0] < KPI_CACHE_SECONDS:
        return hit[1]
    result = _kpis(ctx, instance_id)
    if len(cache) > 16:
        cache.clear()
    cache[key] = (time.monotonic(), result)
    return result


def _kpis(ctx: AppContext, instance_id: str | None = None) -> dict[str, Any]:
    now = now_z()
    instances = ctx.db.fetch_dicts(
        "SELECT id, name, app, media_type, status, suspended FROM instances WHERE archived=0 "
        + ("AND id=? " if instance_id else "") + "ORDER BY sort_order, id",
        (instance_id,) if instance_id else ())
    per_instance = []
    totals: dict[str, int] = {}
    in_week = (datetime.now(UTC) + timedelta(days=7)).strftime("%Y-%m-%dT%H:%M:%SZ")
    for inst in instances:
        iid = inst["id"]
        if inst["app"] == "sonarr":
            stats = series_stats(ctx, iid).values()
            data = {k: sum(int(s.get(k) or 0) for s in stats) for k in
                    ("total", "available", "missing", "upcoming", "unmonitored", "cutoff_unmet")}
            row = ctx.db.fetch_one(
                "SELECT COUNT(*) AS n FROM lib_episodes e JOIN lib_items l ON "
                "l.instance_id=e.instance_id AND l.kind='series' AND "
                "l.provider_id=e.series_provider_id WHERE e.instance_id=? AND e.monitored=1 AND "
                "l.monitored=1 AND l.removed_at IS NULL AND e.air_date_utc>? AND "
                "e.air_date_utc<=?", (iid, now, in_week))
            data["upcoming_7d"] = int(row["n"]) if row else 0
            fut = ctx.db.fetch_one(
                "SELECT SUM(CASE WHEN monitor_new_items='all' THEN 1 ELSE 0 END) AS covered, "
                "SUM(CASE WHEN status IN ('continuing','upcoming') THEN 1 ELSE 0 END) AS active, "
                "SUM(CASE WHEN monitor_new_items='all' AND status IN ('continuing','upcoming') "
                "THEN 1 ELSE 0 END) AS active_covered, COUNT(*) AS n FROM lib_items WHERE "
                "instance_id=? AND kind='series' AND removed_at IS NULL AND monitored=1", (iid,))
            active = int((fut["active"] if fut else 0) or 0)
            data["series"] = int((fut["n"] if fut else 0) or 0)
            data["future_season_coverage"] = (
                round(int(fut["active_covered"] or 0) / active, 4) if active else None)
        else:
            movies = _decorate(ctx, ctx.db.fetch_dicts(
                "SELECT * FROM lib_items WHERE instance_id=? AND kind='movie' AND "
                "removed_at IS NULL", (iid,)))
            data = {k: sum(m["stats"][k] for m in movies) for k in
                    ("total", "available", "missing", "upcoming", "unmonitored", "cutoff_unmet")}
            data["movies"] = len(movies)
            data["upcoming_7d"] = sum(
                1 for m in movies if m["monitored"] and not m["has_file"] and any(
                    m.get(f) and now < m[f] <= in_week
                    for f in ("in_cinemas", "digital_release", "physical_release")))
        for days in (7, 30, 90):
            row = ctx.db.fetch_one(
                "SELECT COUNT(*) AS n FROM wanted_items WHERE instance_id=? AND reason='missing' "
                "AND status IN ('wanted','searching','unavailable') AND air_date_utc>=?",
                (iid, _ago(days)))
            data[f"missing_aired_{days}d"] = int(row["n"]) if row else 0
        for key, value in data.items():
            if isinstance(value, int):
                totals[key] = totals.get(key, 0) + value
        per_instance.append({**inst, "suspended": bool(inst["suspended"]), "kpis": data})
    return {"generated_at": now, "totals": totals, "instances": per_instance}
