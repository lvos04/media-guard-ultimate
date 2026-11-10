"""Opslaglogboek: wat kwam erbij, waar ging de ruimte heen, en de meldingen erover."""

from datetime import UTC, datetime, timedelta

from mediaguard import storage_ledger as sl
from mediaguard.db import dumps, utcnow

GB = 1024 ** 3
NOW = datetime(2026, 11, 6, 10, 0, tzinfo=UTC)


def _iso(dt: datetime) -> str:
    return dt.isoformat(timespec="seconds")


def _seed(ctx) -> None:
    now = utcnow()
    for iid, app, mt, name in (("sonarr-series", "sonarr", "tv", "Series"),
                               ("radarr-films", "radarr", "movie", "Films")):
        ctx.db.execute("INSERT OR IGNORE INTO instances(id,app,media_type,name,url,created_at,"
                       "updated_at) VALUES (?,?,?,?,'http://x',?,?)",
                       (iid, app, mt, name, now, now))
    ctx.db.execute("INSERT INTO lib_items(instance_id,kind,provider_id,title,monitored,"
                   "synced_at) VALUES ('sonarr-series','series',7,'Dr. Steen',1,?)", (now,))
    ctx.db.execute("INSERT INTO lib_items(instance_id,kind,provider_id,title,monitored,"
                   "synced_at) VALUES ('radarr-films','movie',40,'Stormvogel',1,?)", (now,))
    ctx.db.execute("INSERT INTO lib_episodes(instance_id,provider_id,series_provider_id,"
                   "season_number,episode_number,monitored,has_file,synced_at) VALUES "
                   "('sonarr-series',701,7,1,21,1,1,?)", (now,))
    sl.record_history(ctx, "sonarr-series", "sonarr", [
        {"id": 1, "eventType": "grabbed", "date": _iso(NOW - timedelta(hours=3)),
         "seriesId": 7, "episodeId": 701, "downloadId": "SAB1",
         "sourceTitle": "Dr.STEEN.S01E21.1080p", "data": {"indexer": "NZBgeek",
                                                          "releaseGroup": "GRPB",
                                                          "size": str(2 * GB)}},
        {"id": 2, "eventType": "downloadFolderImported", "date": _iso(NOW - timedelta(hours=2)),
         "seriesId": 7, "episodeId": 701, "downloadId": "SAB1",
         "quality": {"quality": {"name": "WEBDL-1080p"}},
         "sourceTitle": "Dr.STEEN.S01E21.1080p", "data": {}},
        {"id": 3, "eventType": "episodeFileRenamed", "date": _iso(NOW)},  # niet relevant
    ])
    sl.record_history(ctx, "radarr-films", "radarr", [
        {"id": 10, "eventType": "movieFileDeleted", "date": _iso(NOW - timedelta(hours=5)),
         "movieId": 40, "data": {"reason": "Upgrade", "size": str(50 * GB)}},
        {"id": 11, "eventType": "downloadFolderImported", "date": _iso(NOW - timedelta(hours=5)),
         "movieId": 40, "downloadId": "QB9", "sourceTitle": "Stormvogel.2160p",
         "quality": {"quality": {"name": "Remux-2160p"}}, "data": {"size": str(60 * GB)}},
        {"id": 12, "eventType": "downloadFolderImported", "date": _iso(NOW - timedelta(days=3)),
         "movieId": 40, "data": {"size": str(1 * GB)}},  # buiten 24 uur
    ])


def test_record_history_is_idempotent(ctx):
    _seed(ctx)
    again = sl.record_history(ctx, "sonarr-series", "sonarr", [
        {"id": 2, "eventType": "downloadFolderImported", "date": _iso(NOW), "data": {}}])
    assert again == 1  # INSERT OR IGNORE: telt de poging, maar geen dubbele rij
    n = ctx.db.fetch_one("SELECT COUNT(*) AS n FROM storage_ledger_events")["n"]
    assert n == 5  # renamed-record is overgeslagen


def test_imports_new_upgrade_size_and_source(ctx):
    _seed(ctx)
    ctx.db.execute("INSERT INTO delivery_items(item_key,source,media_type,title,instance_id,"
                   "provider_id,created_at,updated_at) VALUES ('movie:tmdb:1','seerr','movie',"
                   "'Stormvogel','radarr-films',40,?,?)", (utcnow(), utcnow()))
    rows = sl.imports(ctx, _iso(NOW - timedelta(hours=24)), _iso(NOW))
    by = {r["title"]: r for r in rows}
    ep = by["Dr. Steen"]
    assert ep["kind"] == "new" and ep["size"] == 2 * GB  # grootte uit de grab
    assert ep["episode"] == "S01E21" and ep["indexer"] == "NZBgeek"
    assert ep["release_group"] == "GRPB" and ep["category_nl"] == "Series"
    assert ep["source"] == "arr" and ep["href"].startswith("/bibliotheek/sonarr-series/")
    ww = by["Stormvogel"]
    assert ww["kind"] == "upgrade" and ww["old_size"] == 50 * GB
    assert ww["net_bytes"] == 10 * GB and ww["source"] == "upgrade"  # upgrade ≠ Seerr-levering


def test_ledger_buckets_summary_and_reconciliation(ctx, tmp_path):
    _seed(ctx)
    ctx.db.execute(
        "INSERT INTO encoding_jobs(instance_id,source_path,status,policy_json,"
        "source_snapshot_json,staged_output_json,created_at,updated_at) VALUES "
        "('radarr-films','/data/media/movies/X/X.mkv','committed','{}',?,?,?,?)",
        (dumps({"size": 30 * GB}), dumps({"size": 6 * GB}), _iso(NOW - timedelta(hours=1)),
         _iso(NOW - timedelta(hours=1))))
    day = (NOW - timedelta(hours=24)).date().isoformat()
    ctx.state_set(f"library_space:{day}", {"free": 1000 * GB})
    data = sl.ledger(ctx, "24h", NOW, live_free=1000 * GB - 20 * GB)
    t = data["totals"]
    assert t["added"] == 2 * GB and t["upgrades_net"] == 10 * GB
    assert t["av1_saved"] == 24 * GB and t["library_net"] == -12 * GB
    assert t["episodes"] == 1 and t["movies"] == 1
    keys = [b["key"] for b in data["buckets"]]
    assert keys == ["added", "upgrades", "av1", "quarantine", "downloads", "unexplained"]
    recon = data["reconciliation"]
    assert recon["used_delta"] == 20 * GB
    assert recon["unexplained"] == 20 * GB - recon["explained"]
    assert data["summary_nl"].startswith("Laatste 24 uur: +12,0 GB — 1 aflevering, 1 film")
    assert "AV1 bespaarde 24,0 GB" in data["summary_nl"]
    assert {s["key"] for s in data["by_source"]} == {"arr", "upgrade"}
    assert any(i["kind"] == "av1" for i in data["items"])


def test_ledger_without_space_history_is_honest(ctx):
    _seed(ctx)
    data = sl.ledger(ctx, "7d", NOW, live_free=5 * GB)
    assert data["reconciliation"]["unexplained"] is None
    assert "Nog geen dagelijkse meting" in data["reconciliation"]["text_nl"]
    assert all(b["key"] != "unexplained" for b in data["buckets"])


def test_scan_dirs_excludes_hardlinks(ctx, tmp_path):
    dl = tmp_path / "downloads" / "complete" / "tv"
    dl.mkdir(parents=True)
    (dl / "a.mkv").write_bytes(b"x" * 1000)
    lib = tmp_path / "lib.mkv"
    lib.write_bytes(b"y" * 500)
    (dl / "linked.mkv").hardlink_to(lib)
    ctx.state_set(sl.ROOTS_KEY, [{"path": str(dl), "client": "SAB", "kind": "complete",
                                  "category": "tv"}])
    snap = sl.scan_dirs(ctx)
    d = next(x for x in snap["dirs"] if x["group"] == "downloads")
    assert d["bytes"] == 1500 and d["own_bytes"] == 1000
    assert d["label_nl"] == "Downloads (klaar) · tv"
    assert snap["by_group"]["downloads"] == 1000


def _notifications(ctx):
    return ctx.db.fetch_dicts("SELECT title, body, link, severity FROM notifications ORDER BY id")


def test_notify_tick_daily_weekly_and_growth(ctx):
    _seed(ctx)
    ctx.config.set("global", "", "notify.digest.time", "08:00", "test")
    ctx.config.set("global", "", "notify.digest.weekday", 5, "test")  # 6 nov 2026 = vrijdag
    ctx.config.set("global", "", "storage.growth_alert_gb", 5, "test")
    early = datetime(2026, 11, 6, 5, 0, tzinfo=UTC)  # 06:00 lokaal: nog niet
    assert sl.notify_tick(ctx, early) == ["growth"]
    sent = sl.notify_tick(ctx, NOW)  # 11:00 lokaal
    assert sent == ["daily", "weekly"]  # groei: nog in de afkoeltijd van 12 uur
    assert sl.notify_tick(ctx, NOW + timedelta(minutes=15)) == []  # één keer per dag
    rows = _notifications(ctx)
    titles = [r["title"] for r in rows]
    assert titles[0].startswith("Opslag: +10,0 GB in 24 uur")  # aflevering kwam later
    daily = next(r for r in rows if r["title"] == "Opslag laatste 24 uur")
    assert daily["link"] == "/activiteit?tab=opslag&periode=24h"
    assert "Grootste toevoegingen:" in daily["body"] and "Stormvogel" in daily["body"]
    weekly = next(r for r in rows if r["title"] == "Opslag laatste 7 dagen")
    assert weekly["link"].endswith("periode=7d")


def test_notify_respects_switches(ctx):
    _seed(ctx)
    ctx.config.set("global", "", "storage.ledger_notify_daily", False, "test")
    ctx.config.set("global", "", "storage.ledger_notify_weekly", False, "test")
    ctx.config.set("global", "", "storage.growth_alert_gb", 0, "test")
    assert sl.notify_tick(ctx, NOW) == []


def test_storage_notifications_go_direct_by_default(ctx):
    from mediaguard.notify.service import categorize, preference

    assert categorize("info", "Opslag laatste 24 uur", "/activiteit?tab=opslag") == "storage"
    assert preference(ctx, "storage") == "direct"


def test_digest_contains_storage_line(ctx):
    from mediaguard.notify.service import build_digest

    _seed(ctx)
    line = sl.digest_line(ctx, _iso(NOW - timedelta(days=1)), _iso(NOW))
    assert line and line["text_nl"].startswith("Erbij: 1 aflevering, 1 film, +12,0 GB")
    digest = build_digest(ctx, "weekly", (NOW + timedelta(days=1)).date())
    assert "storage" in digest["stats"]


def test_ledger_api(client):
    r = client.get("/api/v1/storage/ledger?period=7d")
    assert r.status_code == 200, r.text
    assert r.json()["period"] == "7d" and "summary_nl" in r.json()
    assert client.get("/api/v1/storage/ledger?period=1y").status_code == 422


def test_storage_notify_settings_via_meldingen_api(client):
    r = client.put("/api/v1/notify/preferences",
                   json={"storage_daily": False, "storage_growth_alert_gb": 50})
    assert r.status_code == 200, r.text
    st = r.json()["storage"]
    assert st == {"daily": False, "weekly": True, "growth_alert_gb": 50}
    assert client.put("/api/v1/notify/preferences",
                      json={"storage_growth_alert_gb": -1}).status_code == 422
