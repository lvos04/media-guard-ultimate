"""Genormaliseerde momentopname van een Sonarr/Radarr-configuratie + bibliotheek, uit de
API (live bron of doel-fork) óf uit de SQLite van een native back-up. Beide vormen zijn
identiek zodat :func:`compare` ze één-op-één kan vergelijken.

Koppeling gebeurt NOOIT op titel: series op TVDb-ID, films op TMDb-ID, configuratie op
naam (Sonarr/Radarr dwingen unieke namen af), tags op label.
"""

from __future__ import annotations

import hashlib
import json
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from mediaguard.arr.client import ArrClient, ArrError, ArrNotFound
from mediaguard.migration.backupfile import columns, open_ro, remap, tables

SERIES_TYPES = {0: "standard", 1: "daily", 2: "anime"}
NEW_ITEMS = {0: "all", 1: "none"}
MIN_AVAIL = {0: "tba", 1: "announced", 2: "inCinemas", 3: "released", 4: "preDB"}


def parse_dt(value: Any) -> datetime | None:
    if not value:
        return None
    text = str(value).strip().replace(" ", "T").replace("Z", "+00:00")
    try:
        dt = datetime.fromisoformat(text)
    except ValueError:
        return None
    return dt if dt.tzinfo else dt.replace(tzinfo=UTC)


def _allowed(items: list[dict[str, Any]] | None) -> list[int]:
    out: set[int] = set()
    for entry in items or []:
        nested = entry.get("items") or []
        if nested:
            if entry.get("allowed"):
                for sub in nested:
                    q = sub.get("quality")
                    qid = q.get("id") if isinstance(q, dict) else q
                    if qid is not None:
                        out.add(int(qid))
            continue
        if entry.get("allowed"):
            q = entry.get("quality")
            qid = q.get("id") if isinstance(q, dict) else q
            if qid is not None:
                out.add(int(qid))
    return sorted(out)


def _labels(ids: Any, tag_map: dict[int, str]) -> list[str]:
    if isinstance(ids, str):
        try:
            ids = json.loads(ids)
        except ValueError:
            ids = []
    return sorted(tag_map.get(int(i), f"#{i}") for i in ids or [])


def _categories(settings: dict[str, Any]) -> dict[str, Any]:
    return {k: v for k, v in sorted(settings.items()) if "category" in k.lower()}


def _fields(resource: dict[str, Any]) -> dict[str, Any]:
    return {str(f.get("name")): f.get("value") for f in resource.get("fields") or []}


def empty(app: str) -> dict[str, Any]:
    return {"app": app, "series": {}, "movies": {}, "tags": [], "quality_profiles": {},
            "custom_formats": [], "indexers": {}, "download_clients": {}, "import_lists": {},
            "root_folders": [], "remote_path_mappings": [], "wanted_missing": None}


def _episode_summary(entry: dict[str, Any], eps: list[tuple[int, int, bool, Any, bool]],
                     now: datetime) -> None:
    entry["episodes"] = {f"{s}:{e}": mon for s, e, mon, _, _ in sorted(eps)}
    entry["future_monitored"] = sum(1 for _, _, mon, air, _ in eps
                                    if mon and air is not None and air > now)
    entry["with_file"] = sum(1 for *_, has in eps if has)


# -- API -----------------------------------------------------------------------------------
async def from_api(client: ArrClient, *, episodes: bool = True) -> dict[str, Any]:
    snap = empty(client.app)
    now = datetime.now(UTC)
    tag_map = {int(t["id"]): str(t["label"]) for t in await client.tags()}
    snap["tags"] = sorted(tag_map.values())
    qp_map: dict[int, str] = {}
    for qp in await client.quality_profiles():
        qp_map[int(qp["id"])] = str(qp["name"])
        snap["quality_profiles"][qp["name"]] = {
            "upgradeAllowed": bool(qp.get("upgradeAllowed")), "cutoff": qp.get("cutoff"),
            "allowed": _allowed(qp.get("items"))}
    try:
        snap["custom_formats"] = sorted(str(c["name"]) for c in await client.custom_formats())
    except ArrNotFound:
        snap["custom_formats"] = []
    for idx in await client.indexers():
        snap["indexers"][idx["name"]] = {
            "implementation": idx.get("implementation"), "enableRss": bool(idx.get("enableRss")),
            "enableAutomaticSearch": bool(idx.get("enableAutomaticSearch")),
            "enableInteractiveSearch": bool(idx.get("enableInteractiveSearch")),
            "priority": idx.get("priority"), "tags": _labels(idx.get("tags"), tag_map)}
    for dc in await client.download_clients():
        snap["download_clients"][dc["name"]] = {
            "implementation": dc.get("implementation"), "enable": bool(dc.get("enable")),
            "priority": dc.get("priority"), "categories": _categories(_fields(dc)),
            "tags": _labels(dc.get("tags"), tag_map)}
    try:
        lists = await client.provider_list("importlist")
    except ArrNotFound:
        lists = []
    for il in lists:
        snap["import_lists"][il["name"]] = {"implementation": il.get("implementation")}
    snap["root_folders"] = sorted(str(r["path"]).rstrip("/") for r in await client.root_folders())
    snap["remote_path_mappings"] = sorted(
        f"{m.get('host')}|{m.get('remotePath')}|{str(m.get('localPath') or '').rstrip('/')}"
        for m in await client.remote_path_mappings())
    if client.app == "sonarr":
        for s in await client.series_list():
            entry = {"title": s.get("title"), "monitored": bool(s.get("monitored")),
                     "monitorNewItems": s.get("monitorNewItems"),
                     "seriesType": s.get("seriesType"),
                     "qualityProfile": qp_map.get(int(s.get("qualityProfileId") or 0)),
                     "tags": _labels(s.get("tags"), tag_map),
                     "path": str(s.get("path") or "").rstrip("/"),
                     "seasons": {str(x["seasonNumber"]): bool(x.get("monitored"))
                                 for x in s.get("seasons") or []}}
            if episodes:
                eps = [(int(e["seasonNumber"]), int(e["episodeNumber"]), bool(e.get("monitored")),
                        parse_dt(e.get("airDateUtc")), bool(e.get("hasFile")))
                       for e in await client.episodes(int(s["id"]))]
                _episode_summary(entry, eps, now)
            snap["series"][str(s["tvdbId"])] = entry
    else:
        for m in await client.movie_list():
            snap["movies"][str(m["tmdbId"])] = {
                "title": m.get("title"), "monitored": bool(m.get("monitored")),
                "minimumAvailability": m.get("minimumAvailability"),
                "qualityProfile": qp_map.get(int(m.get("qualityProfileId") or 0)),
                "tags": _labels(m.get("tags"), tag_map),
                "path": str(m.get("path") or "").rstrip("/"), "hasFile": bool(m.get("hasFile")),
                "future": not bool(m.get("isAvailable", True))}
    try:
        data = await client.get("wanted/missing", page=1, pageSize=1, monitored="true")
        snap["wanted_missing"] = int((data or {}).get("totalRecords") or 0)
    except ArrError:
        snap["wanted_missing"] = None
    return snap


# -- SQLite van een back-up ---------------------------------------------------------------------
def _json(value: Any, default: Any) -> Any:
    if value in (None, ""):
        return default
    try:
        return json.loads(value)
    except (TypeError, ValueError):
        return default


def from_db(db_path: Path, app: str) -> dict[str, Any]:  # noqa: C901 - schema-varianten
    snap = empty(app)
    now = datetime.now(UTC)
    conn = open_ro(db_path)
    try:
        names = tables(conn)
        tag_map = {int(r["Id"]): str(r["Label"]) for r in conn.execute("SELECT * FROM Tags")}
        snap["tags"] = sorted(tag_map.values())
        qp_map: dict[int, str] = {}
        for r in conn.execute("SELECT * FROM QualityProfiles"):
            keys = r.keys()
            qp_map[int(r["Id"])] = str(r["Name"])
            snap["quality_profiles"][r["Name"]] = {
                "upgradeAllowed": bool(r["UpgradeAllowed"]) if "UpgradeAllowed" in keys
                else None, "cutoff": r["Cutoff"], "allowed": _allowed(_json(r["Items"], []))}
        if "CustomFormats" in names:
            snap["custom_formats"] = sorted(str(r["Name"]) for r in
                                            conn.execute("SELECT Name FROM CustomFormats"))
        icols = columns(conn, "Indexers")
        for r in conn.execute("SELECT * FROM Indexers"):
            snap["indexers"][r["Name"]] = {
                "implementation": r["Implementation"], "enableRss": bool(r["EnableRss"]),
                "enableAutomaticSearch": bool(r["EnableAutomaticSearch"]),
                "enableInteractiveSearch": bool(r["EnableInteractiveSearch"]),
                "priority": r["Priority"] if "Priority" in icols else None,
                "tags": _labels(r["Tags"] if "Tags" in icols else "[]", tag_map)}
        dcols = columns(conn, "DownloadClients")
        for r in conn.execute("SELECT * FROM DownloadClients"):
            snap["download_clients"][r["Name"]] = {
                "implementation": r["Implementation"], "enable": bool(r["Enable"]),
                "priority": r["Priority"] if "Priority" in dcols else None,
                "categories": _categories(_json(r["Settings"], {})),
                "tags": _labels(r["Tags"] if "Tags" in dcols else "[]", tag_map)}
        if "ImportLists" in names:
            for r in conn.execute("SELECT Name, Implementation FROM ImportLists"):
                snap["import_lists"][r["Name"]] = {"implementation": r["Implementation"]}
        snap["root_folders"] = sorted(str(r["Path"]).rstrip("/") for r in
                                      conn.execute("SELECT Path FROM RootFolders"))
        if "RemotePathMappings" in names:
            snap["remote_path_mappings"] = sorted(
                f"{r['Host']}|{r['RemotePath']}|{str(r['LocalPath'] or '').rstrip('/')}"
                for r in conn.execute("SELECT * FROM RemotePathMappings"))
        if app == "sonarr":
            scols = columns(conn, "Series")
            eps_by: dict[int, list[tuple[int, int, bool, Any, bool]]] = {}
            for e in conn.execute("SELECT SeriesId, SeasonNumber, EpisodeNumber, Monitored, "
                                  "AirDateUtc, EpisodeFileId FROM Episodes"):
                eps_by.setdefault(int(e["SeriesId"]), []).append(
                    (int(e["SeasonNumber"]), int(e["EpisodeNumber"]), bool(e["Monitored"]),
                     parse_dt(e["AirDateUtc"]), bool(e["EpisodeFileId"])))
            missing = 0
            for s in conn.execute("SELECT * FROM Series"):
                st = s["SeriesType"]
                entry = {"title": s["Title"], "monitored": bool(s["Monitored"]),
                         "monitorNewItems": NEW_ITEMS.get(s["MonitorNewItems"], s[
                             "MonitorNewItems"]) if "MonitorNewItems" in scols else None,
                         "seriesType": SERIES_TYPES.get(st, st) if isinstance(st, int) else st,
                         "qualityProfile": qp_map.get(int(s["QualityProfileId"] or 0)),
                         "tags": _labels(s["Tags"], tag_map),
                         "path": str(s["Path"] or "").rstrip("/"),
                         "seasons": {str(x["seasonNumber"]): bool(x.get("monitored"))
                                     for x in _json(s["Seasons"], [])}}
                eps = eps_by.get(int(s["Id"]), [])
                _episode_summary(entry, eps, now)
                if entry["monitored"]:
                    missing += sum(1 for _, _, mon, air, has in eps
                                   if mon and not has and air is not None and air <= now)
                snap["series"][str(s["TvdbId"])] = entry
            snap["wanted_missing"] = missing
        else:
            mcols = columns(conn, "Movies")
            if "MovieMetadataId" in mcols and "MovieMetadata" in names:
                sql = ("SELECT m.*, mm.TmdbId AS _tmdb, mm.Title AS _title FROM Movies m "
                       "JOIN MovieMetadata mm ON mm.Id = m.MovieMetadataId")
            else:
                sql = "SELECT m.*, m.TmdbId AS _tmdb, m.Title AS _title FROM Movies m"
            for m in conn.execute(sql):
                ma = m["MinimumAvailability"]
                snap["movies"][str(m["_tmdb"])] = {
                    "title": m["_title"], "monitored": bool(m["Monitored"]),
                    "minimumAvailability": MIN_AVAIL.get(ma, ma) if isinstance(ma, int) else ma,
                    "qualityProfile": qp_map.get(int(m["QualityProfileId"] or 0)),
                    "tags": _labels(m["Tags"], tag_map),
                    "path": str(m["Path"] or "").rstrip("/"),
                    "hasFile": bool(m["MovieFileId"]) if "MovieFileId" in mcols else None,
                    "future": None}
    finally:
        conn.close()
    return snap


def remap_snapshot(snap: dict[str, Any], rules: list[tuple[str, str]]) -> dict[str, Any]:
    """Past padregels toe op een bron-snapshot (verwachte doelpaden)."""
    if not rules:
        return snap
    out = json.loads(json.dumps(snap))
    for coll in ("series", "movies"):
        for entry in out[coll].values():
            entry["path"] = remap(entry["path"], rules)
    out["root_folders"] = sorted(str(remap(p, rules)) for p in out["root_folders"])
    rpm = []
    for item in out["remote_path_mappings"]:
        host, remote, local = item.split("|", 2)
        rpm.append(f"{host}|{remote}|{remap(local, rules)}")
    out["remote_path_mappings"] = sorted(rpm)
    return out


# -- anime-signalen (mappingvoorstel) ------------------------------------------------------
def anime_signals(snap: dict[str, Any], extra_names: list[str] | None = None) -> dict:
    reasons: list[str] = []
    score = 0.0
    if snap["app"] == "sonarr" and snap["series"]:
        anime = sum(1 for s in snap["series"].values() if s.get("seriesType") == "anime")
        frac = anime / len(snap["series"])
        score += frac * 0.7
        reasons.append(f"{anime}/{len(snap['series'])} series hebben seriestype 'anime'")
    words = [p.lower() for p in snap["root_folders"]]
    words += [n.lower() for n in snap["quality_profiles"]]
    words += [str(x).lower() for x in (extra_names or []) if x]
    hits = [w for w in words if "anime" in w]
    if hits:
        score += 0.3 if snap["app"] == "sonarr" else 0.8
        reasons.append("'anime' in rootfolder/profiel/naam: " + ", ".join(sorted(set(hits))[:4]))
    if snap["app"] == "radarr" and snap["movies"]:
        anime_tag = sum(1 for m in snap["movies"].values()
                        if any("anime" in t.lower() for t in m["tags"]))
        if anime_tag:
            score += 0.2 * anime_tag / len(snap["movies"])
            reasons.append(f"{anime_tag} films met een anime-tag")
    return {"score": round(min(1.0, score), 3), "reasons": reasons}


# -- vergelijking -------------------------------------------------------------------------------
HARD = "no_go_hard"  # niet te accepteren (C1-no-go: aantallen/monitorstatus)


def _mm(out: list[dict[str, Any]], category: str, key: str, field: str, src: Any, tgt: Any,
        severity: str, message: str) -> None:
    mid = hashlib.sha1(f"{category}|{key}|{field}".encode()).hexdigest()[:12]
    out.append({"id": mid, "category": category, "key": key, "field": field,
                "source": src, "target": tgt,
                "severity": "no_go" if severity == HARD else severity,
                "acceptable": severity != HARD, "message_nl": message})


def _no_nulls(value: Any) -> Any:
    """null en 'veld ontbreekt' zijn gelijk: {'movieCategory': 'radarr',
    'movieImportedCategory': None} == {'movieCategory': 'radarr'} (rapport 4 nov, H13)."""
    if isinstance(value, dict):
        return {k: _no_nulls(v) for k, v in value.items() if v is not None}
    return value


def compare(src: dict[str, Any], tgt: dict[str, Any], *, shadow: bool,
            overrides: dict[str, Any] | None = None) -> list[dict[str, Any]]:  # noqa: C901
    """Bron (na padregels) vs doel. ``shadow`` = doel moet nog grab-loos zijn."""
    overrides = overrides or {}
    out: list[dict[str, Any]] = []
    coll = "series" if src["app"] == "sonarr" else "movies"
    label = "series" if coll == "series" else "films"
    s_items, t_items = src[coll], tgt[coll]
    if len(s_items) != len(t_items):
        _mm(out, coll, "*", "count", len(s_items), len(t_items), HARD,
            f"Aantal {label} verschilt: bron {len(s_items)}, doel {len(t_items)}.")
    for key, s in s_items.items():
        t = t_items.get(key)
        name = f"{s.get('title')} ({'tvdb' if coll == 'series' else 'tmdb'} {key})"
        if t is None:
            _mm(out, coll, key, "present", True, False, HARD, f"{name} ontbreekt in het doel.")
            continue
        if s["monitored"] != t["monitored"]:
            _mm(out, coll, key, "monitored", s["monitored"], t["monitored"], HARD,
                f"{name}: monitorstatus verschilt.")
        if coll == "series":
            want_new = overrides.get("monitorNewItems", {}).get(key, s.get("monitorNewItems"))
            if want_new is not None and str(want_new) not in ("all", "none"):
                want_new = "all"  # keuze die alleen een nieuwere versie kent
            if want_new is not None and want_new != t.get("monitorNewItems"):
                _mm(out, coll, key, "monitorNewItems", want_new, t.get("monitorNewItems"), HARD,
                    f"{name}: 'nieuwe seizoenen volgen' verschilt.")
            for n, mon in s["seasons"].items():
                if t["seasons"].get(n) != mon:
                    _mm(out, coll, key, f"season:{n}", mon, t["seasons"].get(n), HARD,
                        f"{name}: seizoen {n} monitorstatus verschilt.")
            if "episodes" in s and "episodes" in t:
                diff = [k for k, v in s["episodes"].items() if t["episodes"].get(k) != v]
                if diff:
                    _mm(out, coll, key, "episodes", len(s["episodes"]), len(t["episodes"]), HARD,
                        f"{name}: {len(diff)} afleveringen met afwijkende/ontbrekende "
                        f"monitorstatus (bv. {', '.join(diff[:5])}).")
                if s.get("future_monitored") != t.get("future_monitored"):
                    _mm(out, coll, key, "future_monitored", s.get("future_monitored"),
                        t.get("future_monitored"), HARD,
                        f"{name}: aantal gemonitorde toekomstige afleveringen verschilt.")
            if s.get("seriesType") != t.get("seriesType") and \
                    s.get("seriesType") in ("standard", "daily", "anime", None):
                _mm(out, coll, key, "seriesType", s.get("seriesType"), t.get("seriesType"),
                    "no_go", f"{name}: seriestype verschilt.")
        else:
            if s.get("minimumAvailability") != t.get("minimumAvailability") and \
                    s.get("minimumAvailability") in ("tba", "announced", "inCinemas",
                                                     "released", "preDB", None):
                _mm(out, coll, key, "minimumAvailability", s.get("minimumAvailability"),
                    t.get("minimumAvailability"), "no_go", f"{name}: minimum availability "
                    "verschilt.")
        if s.get("qualityProfile") != t.get("qualityProfile"):
            _mm(out, coll, key, "qualityProfile", s.get("qualityProfile"),
                t.get("qualityProfile"), "no_go", f"{name}: kwaliteitsprofiel verschilt.")
        if s.get("tags") != t.get("tags"):
            _mm(out, coll, key, "tags", s.get("tags"), t.get("tags"), "no_go",
                f"{name}: tags verschillen.")
        if s.get("path") != t.get("path"):
            _mm(out, coll, key, "path", s.get("path"), t.get("path"), "no_go",
                f"{name}: pad verschilt (bestanden worden mogelijk niet gevonden).")
    for key in t_items.keys() - s_items.keys():
        _mm(out, coll, key, "extra", None, t_items[key].get("title"), "warning",
            f"{t_items[key].get('title')} staat in het doel maar niet in de bron.")
    missing_tags = sorted(set(src["tags"]) - set(tgt["tags"]))
    if missing_tags:
        _mm(out, "tags", "*", "missing", missing_tags, None, "no_go",
            f"Tags ontbreken in het doel: {', '.join(missing_tags)}.")
    for name, qp in src["quality_profiles"].items():
        tq = tgt["quality_profiles"].get(name)
        if tq is None:
            _mm(out, "quality_profiles", name, "present", True, False, "no_go",
                f"Kwaliteitsprofiel '{name}' ontbreekt.")
        elif qp["allowed"] != tq["allowed"] or qp["cutoff"] != tq["cutoff"]:
            _mm(out, "quality_profiles", name, "definition", qp, tq, "warning",
                f"Kwaliteitsprofiel '{name}' heeft andere kwaliteiten/cutoff.")
    missing_cf = sorted(set(src["custom_formats"]) - set(tgt["custom_formats"]))
    if missing_cf:
        _mm(out, "custom_formats", "*", "missing", missing_cf, None, "no_go",
            f"Custom formats ontbreken: {', '.join(missing_cf)}.")
    for name, idx in src["indexers"].items():
        ti = tgt["indexers"].get(name)
        if ti is None:
            _mm(out, "indexers", name, "present", True, False, "no_go",
                f"Indexer '{name}' ontbreekt.")
            continue
        if shadow:
            if ti["enableRss"] or ti["enableAutomaticSearch"]:
                _mm(out, "indexers", name, "shadow", False, True, HARD,
                    f"Indexer '{name}' op het doel staat AAN tijdens de schaduwfase "
                    "(dubbele grabber-risico).")
        else:
            for f in ("enableRss", "enableAutomaticSearch"):
                if idx[f] != ti[f]:
                    _mm(out, "indexers", name, f, idx[f], ti[f], "warning",
                        f"Indexer '{name}': {f} verschilt van de bron.")
        if _no_nulls(idx.get("tags")) != _no_nulls(ti.get("tags")) or \
                idx.get("priority") != ti.get("priority"):
            _mm(out, "indexers", name, "settings", idx, ti, "warning",
                f"Indexer '{name}': tags of prioriteit verschillen.")
    for name, dc in src["download_clients"].items():
        td = tgt["download_clients"].get(name)
        if td is None:
            _mm(out, "download_clients", name, "present", True, False, "no_go",
                f"Downloadclient '{name}' ontbreekt.")
            continue
        # alleen velden die het doel kent (een nieuwere bron kan extra categorieën hebben)
        src_cats = {k: v for k, v in dc["categories"].items() if k in td["categories"]} \
            if td["categories"] else dc["categories"]
        if _no_nulls(src_cats) != _no_nulls(td["categories"]):
            _mm(out, "download_clients", name, "categories", dc["categories"],
                td["categories"], "no_go", f"Downloadclient '{name}': categorieën verschillen.")
        if shadow and td["enable"]:
            _mm(out, "download_clients", name, "shadow", False, True, HARD,
                f"Downloadclient '{name}' op het doel staat AAN tijdens de schaduwfase.")
    for name in sorted(src["import_lists"].keys() - tgt["import_lists"].keys()):
        _mm(out, "import_lists", name, "present", True, False, "warning",
            f"Importlijst '{name}' ontbreekt.")
    missing_roots = sorted(set(src["root_folders"]) - set(tgt["root_folders"]))
    if missing_roots:
        _mm(out, "root_folders", "*", "missing", missing_roots, None, "no_go",
            f"Rootfolders ontbreken: {', '.join(missing_roots)}.")
    missing_rpm = sorted(set(src["remote_path_mappings"]) - set(tgt["remote_path_mappings"]))
    if missing_rpm:
        _mm(out, "remote_path_mappings", "*", "missing", missing_rpm, None, "no_go",
            "Remote path mappings ontbreken.")
    sf = sum(int(s.get("future_monitored") or 0) for s in s_items.values())
    tf = sum(int(t.get("future_monitored") or 0) for t in t_items.values())
    if coll == "series" and sf != tf:
        _mm(out, "monitoring", "*", "future_monitored_total", sf, tf, HARD,
            f"Totaal gemonitorde toekomstige afleveringen: bron {sf}, doel {tf}.")
    if src.get("wanted_missing") is not None and tgt.get("wanted_missing") is not None and \
            src["wanted_missing"] != tgt["wanted_missing"]:
        _mm(out, "wanted", "*", "missing_count", src["wanted_missing"], tgt["wanted_missing"],
            "warning", "Aantal ontbrekende (wanted) items verschilt; vaak tijdelijk tot de "
                       "rescan klaar is.")
    return out


def summarize(src: dict[str, Any], tgt: dict[str, Any]) -> dict[str, Any]:
    def counts(s: dict[str, Any]) -> dict[str, Any]:
        items = s["series"] or s["movies"]
        return {"items": len(items), "monitored": sum(1 for i in items.values() if i["monitored"]),
                "episodes": sum(len(i.get("episodes", {})) for i in s["series"].values()),
                "future_monitored": sum(int(i.get("future_monitored") or 0)
                                        for i in s["series"].values()),
                "with_file": sum(int(i.get("with_file") or 0) for i in s["series"].values())
                + sum(1 for i in s["movies"].values() if i.get("hasFile")),
                "tags": len(s["tags"]), "quality_profiles": len(s["quality_profiles"]),
                "custom_formats": len(s["custom_formats"]), "indexers": len(s["indexers"]),
                "download_clients": len(s["download_clients"]),
                "root_folders": len(s["root_folders"]),
                "remote_path_mappings": len(s["remote_path_mappings"]),
                "import_lists": len(s["import_lists"]), "wanted_missing": s["wanted_missing"]}
    return {"source": counts(src), "target": counts(tgt)}


def verdict(mismatches: list[dict[str, Any]], accepted: set[str]) -> str:
    live = [m for m in mismatches if not (m["acceptable"] and m["id"] in accepted)]
    if any(m["severity"] == "no_go" for m in live):
        return "no_go"
    if any(m["severity"] == "warning" for m in live):
        return "warn"
    return "go"
