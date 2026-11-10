"""Rapport 8: upgradegolf (±611 anime-upgrades per dag) — oorzaak zichtbaar, rem en eerlijke
rekensom in het opslaglogboek."""

import re
from datetime import UTC, datetime, timedelta

from mediaguard import storage_ledger as sl
from mediaguard import upgrade_churn as uc
from mediaguard.db import utcnow
from mediaguard.downloads import holds
from mediaguard.provisioning import templates as T

GB = 1024 ** 3


def _iso(dt: datetime) -> str:
    return dt.isoformat(timespec="seconds")


def _seed(ctx) -> None:
    now = utcnow()
    ctx.db.execute("INSERT OR IGNORE INTO instances(id,app,media_type,name,url,created_at,"
                   "updated_at) VALUES ('sonarr-anime','sonarr','anime','Anime','http://x',?,?)",
                   (now, now))
    ctx.db.execute("INSERT INTO lib_items(instance_id,kind,provider_id,title,monitored,"
                   "synced_at) VALUES ('sonarr-anime','series',5,'Elfenvuur',1,?)", (now,))
    ctx.db.execute("INSERT INTO lib_episodes(instance_id,provider_id,series_provider_id,"
                   "season_number,episode_number,monitored,has_file,synced_at) VALUES "
                   "('sonarr-anime',501,5,1,29,1,1,?)", (now,))


def _upgrade(ctx, hid: int, at: datetime, *, old: int | None, new: int, path: str,
             grab_at: datetime | None = None, did: str | None = None) -> None:
    recs = []
    if grab_at is not None:
        recs.append({"id": hid + 1000, "eventType": "grabbed", "date": _iso(grab_at),
                     "seriesId": 5, "episodeId": 501, "downloadId": did,
                     "data": {"releaseGroup": "GRPA"}})
    data = {"reason": "Upgrade"}
    if old is not None:
        data["size"] = str(old)
    recs.append({"id": hid, "eventType": "episodeFileDeleted", "date": _iso(at),
                 "seriesId": 5, "episodeId": 501, "sourceTitle": path, "data": data})
    recs.append({"id": hid + 1, "eventType": "downloadFolderImported", "date": _iso(at),
                 "seriesId": 5, "episodeId": 501, "downloadId": did,
                 "data": {"size": str(new), "releaseGroup": "GRPA"}})
    sl.record_history(ctx, "sonarr-anime", "sonarr", recs)


# -- (iii) haalbare score-grens -----------------------------------------------------------
def test_cutoff_scores_are_reachable():
    for tpl_id, tpl in T.QUALITY_TEMPLATES.items():
        best = sum(v for v in tpl["scores"].values() if v > 0)
        assert 0 <= tpl["cutoff_score"] <= max(best, 0), tpl_id
        assert tpl["cutoff_score"] >= tpl["min_score"]
    assert T.QUALITY_TEMPLATES["anime"]["cutoff_score"] == 2200  # Dual Audio + 2160p
    assert T.QUALITY_TEMPLATES["films"]["cutoff_score"] == 1100  # HDR + DV
    assert T.QUALITY_TEMPLATES["series"]["cutoff_score"] == 0
    assert T.MIN_UPGRADE_SCORE == 100


# -- (i) na de AV1-overdracht blijft de score gelijk -----------------------------------------
def test_owned_formats_match_their_own_name_in_a_renamed_file():
    """Bij een extensiewissel (.mp4 → .mkv) leest Sonarr de score uit de bestandsnaam. Die bevat
    via '{Custom Formats }' de format-namen; elk positief title-format moet daarop matchen,
    anders ziet Sonarr de AV1-versie als slechter en haalt hij de release opnieuw."""
    positive = {n for tpl in T.QUALITY_TEMPLATES.values() for n, v in tpl["scores"].items()
                if v > 0}
    for name in positive:
        specs = T.CUSTOM_FORMATS.get(name) or []
        titles = [s for s in specs if s["implementation"] == "ReleaseTitleSpecification"
                  and not s["negate"]]
        for spec in titles:
            rx = next(f["value"] for f in spec["fields"] if f["name"] == "value")
            filename = f"Show - S01E01 - Title [{T.owned_name(name)} WEBDL-1080p][AV1]-Grp.mkv"
            assert re.search(rx, filename, re.IGNORECASE), name


# -- rekensom: onbekende oude grootte ---------------------------------------------------------
def test_upgrade_without_old_size_counts_zero_or_previous_import(ctx):
    _seed(ctx)
    now = datetime.now(UTC)
    sl.record_history(ctx, "sonarr-anime", "sonarr", [
        {"id": 1, "eventType": "downloadFolderImported", "date": _iso(now - timedelta(days=3)),
         "seriesId": 5, "episodeId": 501, "data": {"size": str(1 * GB)}}])
    _upgrade(ctx, 10, now - timedelta(hours=2), old=None, new=3 * GB, path="/x/a.mkv")
    row = next(i for i in sl.imports(ctx, _iso(now - timedelta(hours=24)), _iso(now))
               if i["kind"] == "upgrade")
    assert row["old_size"] == 1 * GB and row["old_size_estimated"]
    assert row["net_bytes"] == 2 * GB
    ctx.db.execute("DELETE FROM storage_ledger_events WHERE history_id=1")
    row = next(i for i in sl.imports(ctx, _iso(now - timedelta(hours=24)), _iso(now))
               if i["kind"] == "upgrade")
    assert row["old_size_unknown"] and row["net_bytes"] == 0  # niet de volle 3 GB


def test_reconciliation_uses_snapshot_at_start_of_window(ctx):
    _seed(ctx)
    now = datetime.now(UTC)
    sl.record_snapshot(ctx, free=100 * GB, downloads=0, at=now - timedelta(hours=24))
    sl.record_history(ctx, "sonarr-anime", "sonarr", [
        {"id": 50, "eventType": "downloadFolderImported", "date": _iso(now - timedelta(hours=1)),
         "seriesId": 5, "episodeId": 501, "data": {"size": str(10 * GB)}}])
    ctx.state_set(sl.DIRS_KEY, {"by_group": {"downloads": 0}})
    data = sl.ledger(ctx, "24h", now=now, live_free=90 * GB)
    assert data["reconciliation"]["used_delta"] == 10 * GB
    assert data["reconciliation"]["unexplained"] == 0


# -- diagnose en rem --------------------------------------------------------------------------
def test_diagnose_detects_av1_predecessor_backlog_and_churn(ctx):
    _seed(ctx)
    now = datetime.now(UTC)
    ctx.db.execute(
        "INSERT INTO encoding_jobs(instance_id,source_path,status,policy_json,created_at,"
        "updated_at) VALUES ('sonarr-anime','/data/anime/Elfenvuur - S01E29.mkv','committed',"
        "'{}',?,?)", (_iso(now), _iso(now)))
    _upgrade(ctx, 10, now - timedelta(hours=5), old=1 * GB, new=2 * GB,
             path="/data/anime/Elfenvuur - S01E29.mkv", grab_at=now - timedelta(hours=40),
             did="SAB1")
    _upgrade(ctx, 20, now - timedelta(hours=2), old=2 * GB, new=2 * GB,
             path="/data/anime/Elfenvuur - S01E29 v2.mkv", grab_at=now - timedelta(hours=3),
             did="SAB2")
    d = uc.diagnose(ctx, "24h", now=now)
    assert d["upgrades"] == 2 and d["items"] == 1
    assert d["replaced_av1"] == 1 and d["backlog"] == 1 and d["churn_items"] == 1
    top = d["top_items"][0]
    assert top["upgrades_7d"] == 2 and top["replaced_av1"] and "GRPA" in top["release_groups"]
    assert any("AV1" in v for v in d["verdict_nl"])
    assert any("inhaalslag" in v for v in d["verdict_nl"])


def test_churn_scan_holds_item_and_pregrab_refuses(ctx):
    _seed(ctx)
    now = datetime.now(UTC)
    _upgrade(ctx, 10, now - timedelta(days=2), old=1 * GB, new=2 * GB, path="/x/a.mkv")
    _upgrade(ctx, 20, now - timedelta(hours=1), old=2 * GB, new=2 * GB, path="/x/b.mkv")
    fresh = uc.scan(ctx, now=now)
    assert len(fresh) == 1 and fresh[0]["count"] == 2
    hit = holds.hold_for(ctx, holds.item_key("sonarr-anime", "episode", 501))
    assert hit and hit["kind"] == "churn"
    assert uc.scan(ctx, now=now) == []  # één keer melden
    n = ctx.db.fetch_one("SELECT COUNT(*) AS n FROM notifications WHERE title='Upgrades "
                         "afgeremd'")["n"]
    assert n == 1
    body = {"instance_id": "sonarr-anime", "episodes": [{"id": 501, "seasonNumber": 1}],
            "release": {"title": "[GRPA] Elfenvuur - 29 REPACK"}}
    assert "gepauzeerd" in (uc.pre_grab_block(ctx, "sonarr-anime", body) or "")
    assert uc.pre_grab_block(ctx, "sonarr-anime", {"episodes": [{"id": 999}]}) is None


def test_single_upgrade_is_not_churn(ctx):
    _seed(ctx)
    now = datetime.now(UTC)
    _upgrade(ctx, 10, now - timedelta(hours=1), old=1 * GB, new=2 * GB, path="/x/a.mkv")
    assert uc.scan(ctx, now=now) == []
