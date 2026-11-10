"""Rapport 6 (core): weesbestanden in quarantaine, vangnet zichtbaar, opruimrace,
uitgestelde activatie, onbetrouwbare releasegroepen, inhaalgolf na de noodstop,
downloadfout per item, eenmalige meldingen bij nieuwe standaarden."""

import asyncio
import os
import time
from datetime import UTC, datetime, timedelta
from pathlib import Path

from mediaguard.db import dumps, utcnow


def _txn(ctx, lib: str, state: str = "committed", qpath: str | None = None,
         finished: str | None = None, refresh: dict | None = None,
         instance: str | None = None) -> int:
    now = utcnow()
    return ctx.db.execute_insert(
        "INSERT INTO replacement_transactions(kind,instance_id,library_path,final_path,"
        "staged_path,source_snapshot_json,state,quarantine_path,refresh_json,created_at,"
        "updated_at,finished_at) VALUES ('replace',?,?,?,'/s','{}',?,?,?,?,?,?)",
        (instance, lib, lib, state, qpath, dumps(refresh or {}), now, now, finished or now))


def _item(ctx, txn: int, orig: str, qpath: str, status: str, size: int = 4,
          created: str | None = None) -> int:
    return ctx.db.execute_insert(
        "INSERT INTO quarantine_items(transaction_id,original_path,quarantine_path,method,"
        "status,size,created_at) VALUES (?,?,?,'link',?,?,?)",
        (txn, orig, qpath, status, size, created or utcnow()))


def _old(path: Path) -> None:
    past = time.time() - 7200
    os.utime(path, (past, past))


# ── N9: weesbestanden ───────────────────────────────────────────────────────────────
def test_n9_discard_removes_item_hardlink_when_original_in_place(ctx, tmp_path):
    from mediaguard.recovery import orphans

    lib = tmp_path / "lib" / "film.mkv"
    lib.parent.mkdir()
    lib.write_bytes(b"data")
    q = tmp_path / "lib" / ".mediaguard-quarantine" / "txn-1" / "film.mkv"
    q.parent.mkdir(parents=True)
    os.link(lib, q)
    txn = _txn(ctx, str(lib), state="aborted")  # write-ahead: geen quarantinepad op de txn
    _item(ctx, txn, str(lib), str(q), "pending")
    orphans.discard_item_files(ctx, {"id": txn, "library_path": str(lib),
                                     "quarantine_path": None})
    assert not q.exists() and lib.read_bytes() == b"data"


def test_n9_discard_keeps_copy_when_original_missing(ctx, tmp_path):
    from mediaguard.recovery import orphans

    lib = tmp_path / "lib" / "weg.mkv"
    lib.parent.mkdir()
    q = tmp_path / "q" / "txn-2" / "weg.mkv"
    q.parent.mkdir(parents=True)
    q.write_bytes(b"enige kopie")
    txn = _txn(ctx, str(lib), state="aborted")
    _item(ctx, txn, str(lib), str(q), "pending")
    orphans.discard_item_files(ctx, {"id": txn, "library_path": str(lib),
                                     "quarantine_path": str(q)})
    assert q.exists()  # nooit de enige kopie weggooien


def test_n9_sweep_removes_provable_orphans_and_reports_the_rest(ctx, tmp_path):
    from mediaguard.recovery import orphans

    root = tmp_path / "lib"
    ctx.settings.library_roots[:] = [str(root)]
    qroot = root / ".mediaguard-quarantine"
    # A: hardlink van het actieve bibliotheekbestand bij een geannuleerd item
    lib = root / "a.mkv"
    qroot.joinpath("txn-1").mkdir(parents=True)
    lib.parent.mkdir(exist_ok=True)
    lib.write_bytes(b"aaaa")
    qa = qroot / "txn-1" / "a.mkv"
    os.link(lib, qa)
    _item(ctx, _txn(ctx, str(lib), state="aborted"), str(lib), str(qa), "discarded")
    # B: later alsnog vervangen (> vangnet geleden) → oude origineel mag weg
    libb = root / "b.mkv"
    libb.write_bytes(b"nieuw-av1")
    qb = qroot / "txn-2" / "b.mkv"
    qb.parent.mkdir()
    qb.write_bytes(b"oud-origineel")
    t_old = _txn(ctx, str(libb), state="aborted")
    _item(ctx, t_old, str(libb), str(qb), "discarded")
    long_ago = (datetime.now(UTC) - timedelta(days=30)).isoformat(timespec="seconds")
    _txn(ctx, str(libb), state="committed", finished=long_ago)
    # onbekend bestand zonder item: alleen melden
    unknown = qroot / "txn-99" / "x.mkv"
    unknown.parent.mkdir()
    unknown.write_bytes(b"?")
    for p in (qa, qb, unknown):
        _old(p)
    result = orphans.sweep(ctx)
    assert not qa.exists() and lib.exists()
    assert not qb.exists() and libb.exists()
    assert unknown.exists()
    left = [i for i in result["items"] if not i["removed"]]
    assert [Path(i["path"]).name for i in left] == ["x.mkv"]
    assert result["removed"] == 2
    assert ctx.db.fetch_one("SELECT COUNT(*) FROM audit_events WHERE "
                            "action='quarantine.orphan_removed'")[0] == 2


def test_n9_list_shows_superseded_status_and_orphans_endpoint(client, ctx, tmp_path):
    lib = str(tmp_path / "c.mkv")
    t_old = _txn(ctx, lib, state="aborted")
    _item(ctx, t_old, lib, str(tmp_path / "q" / "c.mkv"), "discarded")
    _txn(ctx, lib, state="committed", finished="2026-11-05T19:00:00+00:00")
    rows = client.get("/api/v1/quarantine").json()
    row = next(r for r in rows if r["original_path"] == lib)
    assert row["status_nl"] == "Geannuleerd — later alsnog vervangen op 5 nov"
    assert client.get("/api/v1/quarantine/orphans").status_code == 200


# ── N10: opruimrace en verdwenen originelen ─────────────────────────────────────────
def test_n10_check_on_purged_item_is_skipped_and_not_overwritten(ctx, tmp_path):
    from mediaguard.recovery import release_check

    txn = _txn(ctx, str(tmp_path / "x.mkv"))
    item = _item(ctx, txn, str(tmp_path / "x.mkv"), str(tmp_path / "q.mkv"), "purged")
    ctx.db.execute("INSERT INTO quarantine_release_checks(item_id,status,checks_json,"
                   "checked_at,attempts) VALUES (?,'passed','[]',datetime('now'),1)", (item,))
    out = release_check.run_check(ctx, item)
    assert out["skipped"] and out["status"] == "passed"


def test_n10_missing_original_is_reported_as_error_not_purged_silently(ctx, tmp_path):
    from mediaguard.recovery import release_check

    active = tmp_path / "act.mkv"
    active.write_bytes(b"av1")
    txn = _txn(ctx, str(active))
    item = _item(ctx, txn, str(active), str(tmp_path / "gone.mkv"), "restorable")
    out = release_check.run_check(ctx, item)
    assert out["status"] == "failed"
    row = ctx.db.fetch_one("SELECT status, reason FROM quarantine_items WHERE id=?", (item,))
    assert row["status"] == "purged" and row["reason"].startswith("Origineel verdwenen")
    assert ctx.db.fetch_one("SELECT 1 FROM audit_events WHERE action='quarantine.vanished'")


# ── N8: vangnet zichtbaar ───────────────────────────────────────────────────────────
def test_n8_usage_shows_safety_net_contents_and_next_purge(client, ctx, tmp_path):
    ctx.config.set("global", "", "quarantine.mode", "immediate_after_check", "t")
    txn = _txn(ctx, str(tmp_path / "y.mkv"))
    created = (datetime.now(UTC) - timedelta(hours=1)).isoformat(timespec="seconds")
    _item(ctx, txn, str(tmp_path / "y.mkv"), str(tmp_path / "qy.mkv"), "restorable",
          size=5 * 1024**3, created=created)
    usage = client.get("/api/v1/quarantine/usage").json()
    net = usage["safety_net"]
    assert net["items"] == 1 and net["bytes"] == 5 * 1024**3 and net["first_expiry_at"]
    assert "vangnet" in usage["mode_nl"] and "1 item" in usage["mode_nl"]
    assert usage["next_purge"]["at"] == net["first_expiry_at"]
    assert usage["next_purge"]["at_nl"].startswith("Eerste item gaat weg op")


def test_n8_behaviour_notice_sent_once(ctx):
    from mediaguard import notices

    ctx.config.set("global", "", "quarantine.mode", "immediate_after_check", "t")
    assert notices.run(ctx) == ["0.3.8:quarantine.safety_net"]
    assert notices.run(ctx) == []
    row = ctx.db.fetch_one("SELECT COUNT(*) FROM notifications WHERE title LIKE "
                           "'Nieuwe standaard%'")
    assert row[0] == 1


def test_n8_no_notice_when_user_keeps_retention(ctx):
    from mediaguard import notices

    assert notices.run(ctx) == []


# ── N4: uitgestelde activatie ────────────────────────────────────────────────────────
def _grab(ctx, did: str, movie: int, hours_ago: float = 1) -> None:
    at = (datetime.now(UTC) - timedelta(hours=hours_ago)).strftime("%Y-%m-%d %H:%M:%S")
    ctx.db.execute(
        "INSERT INTO release_provenance(instance_id,event_type,download_id,release_title,"
        "movie_id,received_at,idempotency_key) VALUES ('radarr-films','Grab',?,?,?,?,?)",
        (did, f"Film.{did}", movie, at, f"g-{did}"))


def test_n4_failed_grab_does_not_block_activation(ctx):
    from mediaguard.recovery import executor

    txn = {"instance_id": "radarr-films", "refresh_json": dumps({"movie_id": 7})}
    _grab(ctx, "dl-1", 7)
    blocker = executor.download_blocker(ctx, txn)
    assert blocker is not None and blocker["release"] == "Film.dl-1"
    ctx.state_set("download_outcomes", {"dl-1": {"outcome": "failed", "at": utcnow()}})
    assert executor.download_blocker(ctx, txn) is None


def test_n4_only_newest_grab_counts(ctx):
    from mediaguard.recovery import executor

    txn = {"instance_id": "radarr-films", "refresh_json": dumps({"movie_id": 8})}
    _grab(ctx, "old", 8, hours_ago=5)
    _grab(ctx, "new", 8, hours_ago=1)
    ctx.state_set("download_outcomes", {"new": {"outcome": "imported", "at": utcnow()}})
    assert executor.download_blocker(ctx, txn) is None


def test_n4_active_downloads_ignores_failed(ctx):
    from mediaguard.optimization.runner import active_downloads

    _grab(ctx, "a", 1)
    _grab(ctx, "b", 2)
    ctx.state_set("download_outcomes", {"a": {"outcome": "failed", "at": utcnow()}})
    assert active_downloads(ctx) == 1


# ── N3: onbetrouwbare groepen en inhaalgolf ──────────────────────────────────────────
def test_n3_unreliable_group_rejected_in_pre_grab(ctx):
    from mediaguard.downloads import failures
    from mediaguard.hooks import service

    at = (datetime.now(UTC) - timedelta(days=2)).isoformat(timespec="seconds")
    outcomes = {f"k{i}": {"group": "GRPA", "outcome": "imported" if i < 1 else "grabbed",
                          "at": at} for i in range(25)}
    ctx.state_set("download_outcomes", outcomes)
    groups = failures.unreliable_groups(ctx)
    assert "grpa" in groups and groups["grpa"]["imported"] == 1
    res = service.pre_grab(ctx, "sonarr-anime", {"release": {
        "title": "Show.S01E01.REPACK.1080p-GRPA", "release_group": "GRPA"}})
    assert res["decision"] == "reject" and res["reason_code"] == "GROUP_UNRELIABLE"
    assert ctx.db.fetch_one("SELECT COUNT(*) FROM notifications WHERE title LIKE "
                            "'Onbetrouwbare releasegroep%'")[0] == 1


def test_n3_catchup_throttle_after_emergency_stop_lift(ctx):
    from mediaguard.downloads import failures

    ctx.state_set("emergency_stop", {"active": False, "since": utcnow()})
    allowed = [failures.catchup_throttle(ctx, "sonarr-anime") for _ in range(12)]
    assert allowed[:10] == [None] * 10 and allowed[10] and allowed[11]
    assert failures.catchup_throttle(ctx, "radarr-films") is None  # per instantie
    long_ago = (datetime.now(UTC) - timedelta(hours=3)).isoformat(timespec="seconds")
    ctx.state_set("emergency_stop", {"active": False, "since": long_ago})
    assert failures.catchup_throttle(ctx, "sonarr-anime") is None


def test_n3_lift_reports_what_resumes(client, ctx):
    client.post("/api/v1/system/emergency-stop", json={"active": True, "reason": "test"})
    out = client.post("/api/v1/system/emergency-stop", json={"active": False}).json()
    assert "worden hervat" in out["resumed_nl"]


# ── N6: downloadfout per item ────────────────────────────────────────────────────────
def test_n6_last_failure_only_for_this_series():
    from mediaguard.delivery import engine

    class Client:
        async def history(self, **_kw):
            return {"records": [
                {"seriesId": 99, "eventType": "downloadFailed", "date": "2026-11-05T12:09:00Z",
                 "sourceTitle": "Dr.STEEN.S01E21", "data": {"message": "x"}},
                {"seriesId": 5, "eventType": "grabbed", "date": "2026-11-05T11:00:00Z"}]}

    assert asyncio.run(engine._last_download_failure(Client(), True, 5)) is None


# ── N5: wachtstand telt niet als fout ────────────────────────────────────────────────
def test_n5_waiting_deferral_is_not_an_error(ctx):
    from mediaguard.health_summary import _av1_problems

    for i, err in enumerate(("Activatie uitgesteld: Er loopt nog een download/import",
                             "Activatie uitgesteld: PermissionError: Operation not permitted")):
        ctx.db.execute_insert(
            "INSERT INTO encoding_jobs(source_path,status,error,policy_json,created_at,"
            "updated_at) VALUES (?,?,?,'{}',?,?)", (f"/m/{i}.mkv", "awaiting_activation", err,
                                                   utcnow(), utcnow()))
    probs = _av1_problems(ctx)
    assert probs["not_activated"] == 1 and probs["waiting_activation"] == 1


def test_n9_no_orphan_removal_during_emergency_stop(ctx, tmp_path, monkeypatch):
    """Tijdens een noodstop ruimt de recovery-worker niets op, ook geen weesbestanden bij het
    opstarten of in het uurlijkse onderhoud (verbeterlijst 15)."""
    from dataclasses import replace

    from mediaguard.recovery import api as recovery_api

    root = tmp_path / "lib"
    ctx.settings.library_roots[:] = [str(root)]
    libb = root / "b.mkv"
    qb = root / ".mediaguard-quarantine" / "txn-2" / "b.mkv"
    qb.parent.mkdir(parents=True)
    libb.write_bytes(b"nieuw-av1")
    qb.write_bytes(b"oud-origineel")
    _item(ctx, _txn(ctx, str(libb), state="aborted"), str(libb), str(qb), "discarded")
    long_ago = (datetime.now(UTC) - timedelta(days=30)).isoformat(timespec="seconds")
    _txn(ctx, str(libb), state="committed", finished=long_ago)
    _old(qb)
    monkeypatch.setattr(ctx, "settings", replace(ctx.settings, role="recovery"))
    ctx.state_set("emergency_stop", {"active": True, "since": utcnow()})
    assert "Noodstop" in (recovery_api.purge_paused_reason(ctx) or "")
    asyncio.run(recovery_api.startup(ctx))
    assert qb.exists()  # alleen geteld, niets weggehaald
    ctx.state_set("emergency_stop", {"active": False, "since": utcnow()})
    asyncio.run(recovery_api.startup(ctx))
    assert not qb.exists() and libb.exists()
