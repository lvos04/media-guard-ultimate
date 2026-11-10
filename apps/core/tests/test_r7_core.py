"""Rapport 7: vangnet met hysterese en vooraf ruimte maken, één quarantainegetal, enz."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

GB = 1024**3


def _item(ctx, txn, age_h, size_gb, check="passed", now=None):
    now = now or datetime.now(UTC)
    created = (now - timedelta(hours=age_h)).isoformat(timespec="seconds")
    item = ctx.db.execute_insert(
        "INSERT INTO quarantine_items(transaction_id,original_path,quarantine_path,method,"
        "status,size,created_at) VALUES (?,?,?,'move','restorable',?,?)",
        (txn, f"/m/{age_h}.mkv", f"/q/{age_h}.mkv", int(size_gb * GB), created))
    if check:
        ctx.db.execute("INSERT INTO quarantine_release_checks(item_id,status,checks_json,"
                       "checked_at,attempts) VALUES (?,?,'[]',datetime('now'),1)",
                       (item, check))
    return item


def _txn(ctx):
    now = datetime.now(UTC).isoformat()
    return ctx.db.execute_insert(
        "INSERT INTO replacement_transactions(kind,library_path,final_path,staged_path,"
        "source_snapshot_json,state,created_at,updated_at) VALUES ('replace','/a','/a','/s',"
        "'{}','committed',?,?)", (now, now))


def _direct(ctx, cap_gb=100):
    ctx.config.set("global", "", "quarantine.mode", "immediate_after_check", "t")
    ctx.config.set("global", "", "quarantine.safety_net_hours", 48, "t")
    ctx.config.set("global", "", "quarantine.safety_net_gb", cap_gb, "t")


def test_safety_net_purges_down_to_target_percent(ctx):
    from mediaguard.recovery import service

    _direct(ctx)
    txn = _txn(ctx)
    for age in range(1, 12):  # 11 × 10 GB = 110 GB, allemaal binnen 48 uur
        _item(ctx, txn, age, 10)
    picked = service.purge_candidates(ctx)
    # tot 85 % van 100 GB: 110 → ≤ 85, dus 3 oudste (30 GB) weg, niet maar 1
    assert len(picked) == 3
    assert all(p["purge_reason"] == "vangnet vol" for p in picked)
    assert [p["original_path"] for p in picked] == ["/m/11.mkv", "/m/10.mkv", "/m/9.mkv"]


def test_unchecked_originals_count_but_are_never_purged(ctx):
    from mediaguard.recovery import service

    _direct(ctx)
    txn = _txn(ctx)
    _item(ctx, txn, 5, 40)
    _item(ctx, txn, 4, 40)
    _item(ctx, txn, 1, 41.8, check=None)  # een nieuwe 4K-film, nog niet gecontroleerd
    picked = service.purge_candidates(ctx)
    assert "/m/1.mkv" not in [p["original_path"] for p in picked]
    assert len(picked) == 1  # 121,8 → 81,8 GB (≤ 85)


def test_reserve_makes_room_before_handoff(ctx):
    from mediaguard.db import dumps, utcnow
    from mediaguard.recovery import service

    _direct(ctx)
    txn = _txn(ctx)
    for age in range(1, 10):  # 90 GB: onder het maximum
        _item(ctx, txn, age, 10)
    assert service.purge_candidates(ctx) == []
    now = utcnow()
    ctx.db.execute(
        "INSERT INTO encoding_jobs(instance_id,media_kind,provider_id,source_path,trial_only,"
        "status,policy_json,source_snapshot_json,created_at,created_by,updated_at) VALUES "
        "('radarr-films','movie_file','5','/x.mkv',0,'awaiting_activation','{}',?,?,'t',?)",
        (dumps({"size": 40 * GB}), now, now))
    picked = service.purge_candidates(ctx)
    # 90 + 40 verwacht = 130 > 100 → tot 85: 45 GB weg = 5 items
    assert len(picked) == 5


def test_queue_does_not_pause_when_purge_can_make_room(ctx):
    from mediaguard import jobs
    from mediaguard.recovery.release_check import quota_exceeded

    _direct(ctx)
    txn = _txn(ctx)
    for age in range(1, 12):
        _item(ctx, txn, age, 10)
    assert quota_exceeded(ctx) is False
    rows = ctx.db.fetch_all("SELECT job_type FROM jobs WHERE job_type='quarantine.purge'")
    assert rows, "opruimronde ingepland"
    assert jobs  # noqa: B018 - import gebruikt


def test_queue_pauses_when_nothing_can_be_purged(ctx):
    from mediaguard.recovery.release_check import quota_exceeded

    _direct(ctx)
    txn = _txn(ctx)
    for age in range(1, 12):
        _item(ctx, txn, age, 10, check=None)  # niets gecontroleerd → niets op te ruimen
    assert quota_exceeded(ctx) is True


def test_usage_api_one_number(client, ctx):
    _direct(ctx)
    txn = _txn(ctx)
    _item(ctx, txn, 3, 40)
    _item(ctx, txn, 1, 41.8, check=None)
    body = client.get("/api/v1/quarantine/usage").json()
    assert body["used_bytes"] == body["safety_net"]["bytes"]
    assert body["unchecked_bytes"] == int(41.8 * GB)
    assert body["purge_target_percent"] == 85
    assert body["cap_bytes"] == 100 * GB


def test_n10_old_purged_item_with_missing_file_becomes_vanished(ctx):
    from mediaguard.recovery import orphans

    txn = _txn(ctx)
    item = _item(ctx, txn, 30, 1, check=None)
    ctx.db.execute("UPDATE quarantine_items SET status='purged', reason='Bewaartermijn verlopen'"
                   " WHERE id=?", (item,))
    ctx.db.execute("INSERT INTO quarantine_release_checks(item_id,status,checks_json,checked_at,"
                   "attempts) VALUES (?,'failed',?,datetime('now'),1)",
                   (item, '[{"name":"probe","passed":false,"detail":"ffprobe faalde: '
                          '/data/.mediaguard-quarantine/txn-17/x.mkv: No such file or '
                          'directory"}]'))
    assert orphans.fix_vanished_history(ctx) == 1
    assert orphans.fix_vanished_history(ctx) == 0  # idempotent
    row = ctx.db.fetch_one("SELECT reason FROM quarantine_items WHERE id=?", (item,))
    assert row["reason"] == "Origineel verdwenen buiten MediaGuard om"


def test_n9_orphan_sweep_notifies(ctx, tmp_path):
    from mediaguard.recovery import orphans

    orphans._notify(ctx, {"checked_at": "2026-11-06T10:00:00", "removed": 2,
                          "freed_bytes": 5 * GB, "left": 1, "left_bytes": 2 * GB, "items": []})
    titles = [r["title"] for r in ctx.db.fetch_all("SELECT title FROM notifications")]
    assert any("opgeruimd" in t for t in titles)
    assert any("blijven staan" in t for t in titles)


def test_a4_recent_shows_each_import_once(client, ctx):
    from mediaguard.db import utcnow

    now = utcnow()
    ctx.db.execute(
        "INSERT INTO release_provenance(instance_id,event_type,download_id,release_title,"
        "quality,indexer,series_id,idempotency_key,received_at) VALUES ('sonarr-series',"
        "'Download','d1','Spoedpost.S23E04','WEBDL-1080p','NZBgeek',7,'k1',?)", (now,))
    for key in ("a", "b"):  # twee webhooks voor dezelfde import
        ctx.db.execute(
            "INSERT INTO import_events(instance_id,event_type,is_upgrade,download_id,series_id,"
            "file_path,idempotency_key,payload_json,received_at) VALUES ('sonarr-series',"
            "'Download',0,'d1',7,'/tv/Grey/S23E04.mkv',?,'{}',?)", (key, now))
    recent = client.get("/api/v1/activity").json()["recent"]
    imports = [r for r in recent if r["kind"] in ("import", "event")]
    assert len(imports) == 1
