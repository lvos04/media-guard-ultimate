"""Vrijgavecontrole vóór opruimen, quotum (7 dagen / 500 GB standaard) en handmatig
verwijderen via de API."""

from __future__ import annotations

import shutil
import subprocess
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest

from mediaguard.recovery import release_check, service

FFMPEG = shutil.which("ffmpeg") is not None


def _clip(path: Path, *, seconds: int = 2, audio_langs: tuple[str, ...] = ("eng", "jpn")) -> None:
    cmd = ["ffmpeg", "-v", "error", "-y", "-f", "lavfi", "-i",
           f"testsrc=size=320x240:rate=25:duration={seconds}"]
    for _ in audio_langs:
        cmd += ["-f", "lavfi", "-i", f"sine=duration={seconds}"]
    cmd += ["-map", "0:v"]
    for i, lang in enumerate(audio_langs):
        cmd += ["-map", f"{i + 1}:a", f"-metadata:s:a:{i}", f"language={lang}"]
    cmd += ["-c:v", "libx264", "-preset", "ultrafast", "-c:a", "aac", str(path)]
    subprocess.run(cmd, check=True)


def _seed(ctx, tmp_path: Path, *, active_langs=("eng", "jpn"), age_days: float = 10,
          size: int = 1000) -> int:
    lib = tmp_path / "lib"
    lib.mkdir(exist_ok=True)
    q = tmp_path / "q"
    q.mkdir(exist_ok=True)
    n = len(list(lib.iterdir()))
    active, original = lib / f"ep{n}.mkv", q / f"ep{n}.orig.mkv"
    _clip(original)
    _clip(active, audio_langs=active_langs)
    from mediaguard.recovery.fsops import sha256_file

    created = (datetime.now(UTC) - timedelta(days=age_days)).isoformat(timespec="seconds")
    txn = ctx.db.execute_insert(
        "INSERT INTO replacement_transactions(kind,library_path,final_path,staged_path,"
        "staged_sha256,source_snapshot_json,state,created_at,updated_at) "
        "VALUES ('replace',?,?,'x',?,'{}','committed',?,?)",
        (str(active), str(active), sha256_file(active), created, created))
    return ctx.db.execute_insert(
        "INSERT INTO quarantine_items(transaction_id,original_path,quarantine_path,method,"
        "status,created_at,size) VALUES (?,?,?,'link','restorable',?,?)",
        (txn, str(active), str(original), created, size))


def test_defaults_match_user_choice(ctx):
    assert ctx.config.value("quarantine.retention_days") == 7
    assert ctx.config.value("quarantine.max_size_gb") == 500
    assert ctx.config.value("quarantine.auto_purge") is True


def test_release_check_cannot_be_disabled(client):
    r = client.patch("/api/v1/settings/scoped", json={"changes": [
        {"key": "integrity.release_check_before_purge", "value": False}]})
    assert r.status_code == 422


def test_amounts_editable_via_settings_api(client, ctx):
    r = client.patch("/api/v1/settings/scoped", json={"changes": [
        {"key": "quarantine.retention_days", "value": 3},
        {"key": "quarantine.max_size_gb", "value": 200}]})
    assert r.status_code == 200
    assert ctx.config.value("quarantine.max_size_gb") == 200


@pytest.mark.skipif(not FFMPEG, reason="ffmpeg ontbreekt")
def test_good_file_passes_and_is_purged_after_retention(ctx, tmp_path):
    item = _seed(ctx, tmp_path)
    assert service.purge_candidates(ctx) == []  # nog niet gecontroleerd
    result = release_check.run_check(ctx, item)
    assert result["status"] == "passed", result
    assert any(c["name"] == "volledige-decode" for c in result["checks"])
    assert [c["id"] for c in service.purge_candidates(ctx)] == [item]


@pytest.mark.skipif(not FFMPEG, reason="ffmpeg ontbreekt")
def test_missing_audio_language_blocks_purge(ctx, tmp_path):
    item = _seed(ctx, tmp_path, active_langs=("eng",))
    result = release_check.run_check(ctx, item)
    assert result["status"] == "failed"
    assert any(c["name"] == "audiosporen" and not c["passed"] for c in result["checks"])
    assert service.purge_candidates(ctx) == []


@pytest.mark.skipif(not FFMPEG, reason="ffmpeg ontbreekt")
def test_modified_active_file_blocks_purge(ctx, tmp_path):
    item = _seed(ctx, tmp_path)
    txn = ctx.db.fetch_one("SELECT t.final_path FROM replacement_transactions t JOIN "
                           "quarantine_items q ON q.transaction_id=t.id WHERE q.id=?", (item,))
    with open(txn["final_path"], "ab") as fh:
        fh.write(b"gewijzigd")
    result = release_check.run_check(ctx, item)
    assert result["status"] == "failed"


@pytest.mark.skipif(not FFMPEG, reason="ffmpeg ontbreekt")
def test_quota_purges_oldest_verified_after_min_age_and_pauses_encodes(ctx, tmp_path):
    ctx.config.set("global", "", "quarantine.max_size_gb", 1, "t")
    gb = 1024**3
    old = _seed(ctx, tmp_path, age_days=3, size=gb)        # binnen 7 dagen, ouder dan 24 uur
    young = _seed(ctx, tmp_path, age_days=0.1, size=gb)    # jonger dan minimale leeftijd
    for i in (old, young):
        release_check.run_check(ctx, i)
    assert release_check.quota_exceeded(ctx)
    chosen = service.purge_candidates(ctx)
    assert [c["id"] for c in chosen] == [old] and chosen[0]["purge_reason"] == "quotum"


async def test_encode_submit_blocked_when_quarantine_full(ctx):
    from mediaguard.encoding import service as enc

    ctx.config.set("global", "", "quarantine.max_size_gb", 1, "t")
    ctx.db.execute("INSERT INTO replacement_transactions(id,kind,library_path,final_path,"
                   "staged_path,source_snapshot_json,state,created_at,updated_at) VALUES "
                   "(99,'replace','a','a','b','{}','committed','x','x')")
    ctx.db.execute("INSERT INTO quarantine_items(transaction_id,original_path,quarantine_path,"
                   "method,status,created_at,size) VALUES (99,'a','b','link','restorable','x',?)",
                   (2 * 1024**3,))
    with pytest.raises(enc.GateError) as err:
        await enc.submit(ctx, path="/media/x.mkv")
    assert err.value.code == "QUARANTINE_FULL"


@pytest.mark.skipif(not FFMPEG, reason="ffmpeg ontbreekt")
def test_manual_delete_and_bulk_delete(client, ctx, tmp_path):
    item = _seed(ctx, tmp_path)
    # niet gecontroleerd: gewone bevestiging is niet genoeg
    r = client.request("DELETE", f"/api/v1/quarantine/{item}", json={"confirm": True})
    assert r.status_code == 409
    r = client.request("DELETE", f"/api/v1/quarantine/{item}",
                       json={"confirm": True, "force_unverified": True})
    assert r.status_code == 200 and r.json()["job_id"]
    other = _seed(ctx, tmp_path)
    release_check.run_check(ctx, other)
    preview = client.post("/api/v1/quarantine/bulk-delete",
                          json={"item_ids": [other]}).json()
    assert preview["dry_run"] and preview["count"] == 1
    done = client.post("/api/v1/quarantine/bulk-delete",
                       json={"item_ids": [other], "confirm": True}).json()
    assert len(done["job_ids"]) == 1
    usage = client.get("/api/v1/quarantine/usage").json()
    assert usage["quota_bytes"] == 500 * 1024**3 and "next_purge" in usage


@pytest.mark.skipif(not FFMPEG, reason="ffmpeg ontbreekt")
async def test_immediate_mode_purges_right_after_passed_check(ctx, tmp_path):
    from mediaguard import jobs

    ctx.config.set("global", "", "quarantine.mode", "immediate_after_check", "t")
    ctx.config.set("global", "", "quarantine.safety_net_hours", 0, "t")  # echt direct (M5)
    good = _seed(ctx, tmp_path, age_days=0)
    bad = _seed(ctx, tmp_path, age_days=0, active_langs=("eng",))
    assert service.purge_candidates(ctx) == []  # nooit vóór de controle
    for item in (good, bad):
        jobs.enqueue(ctx.db, "quarantine.release_check", {"item_id": item})
    runner = jobs.JobRunner(ctx)
    object.__setattr__(ctx.settings, "role", "all")
    await runner.run_until_idle(timeout=120)
    status = {r["id"]: r["status"] for r in ctx.db.fetch_all(
        "SELECT id,status FROM quarantine_items")}
    assert status[good] == "purged"          # direct weg na geslaagde controle
    assert status[bad] == "restorable"       # controle faalde: blijft staan


@pytest.mark.skipif(not FFMPEG, reason="ffmpeg ontbreekt")
def test_content_loss_triggers_automatic_restore(ctx, tmp_path):
    """Rapport 2 / N2: AV1-versie mist sporen → origineel automatisch terug (restore-txn)."""
    from mediaguard.recovery.api import restore_failed_checks

    item = _seed(ctx, tmp_path, active_langs=("eng",))
    result = release_check.run_check(ctx, item)
    assert result["status"] == "failed"
    assert "Audiosporen ontbreken: Japans" in release_check.summary_nl(result["checks"])
    assert restore_failed_checks(ctx) == 1
    txn = ctx.db.fetch_one("SELECT kind, created_by FROM replacement_transactions "
                           "WHERE restore_of_item_id=?", (item,))
    assert txn["kind"] == "restore" and txn["created_by"] == "svc:auto-restore"
    assert restore_failed_checks(ctx) == 0  # nooit dubbel
    assert ctx.db.fetch_one("SELECT COUNT(*) AS n FROM notifications WHERE title LIKE "
                            "'Origineel teruggezet%'")["n"] == 1


@pytest.mark.skipif(not FFMPEG, reason="ffmpeg ontbreekt")
def test_changed_active_file_is_never_auto_restored(ctx, tmp_path):
    from mediaguard.recovery.api import restore_failed_checks

    item = _seed(ctx, tmp_path, active_langs=("eng",))
    txn = ctx.db.fetch_one("SELECT t.final_path FROM replacement_transactions t JOIN "
                           "quarantine_items q ON q.transaction_id=t.id WHERE q.id=?", (item,))
    with open(txn["final_path"], "ab") as fh:
        fh.write(b"door Sonarr vervangen")
    release_check.run_check(ctx, item)
    assert restore_failed_checks(ctx) == 0
