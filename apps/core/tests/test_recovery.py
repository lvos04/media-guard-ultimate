"""Recovery-worker: vervangtransacties, journal replay en fault injection (G2/P2/L2).

Invariant na élke fout of crash + herstel: op het bibliotheekpad staat een geldig bestand
(origineel óf geverifieerd nieuw) en het origineel is herstelbaar; geen tijdelijke
bestanden achtergebleven.
"""

from __future__ import annotations

import errno
import os
from pathlib import Path

import httpx
import pytest
from fastapi.testclient import TestClient
from test_encoding_support import FakeInstance, FakeRegistry

from mediaguard import jobs, locks
from mediaguard.app import create_app
from mediaguard.config import load_settings
from mediaguard.recovery import executor, fsops, service
from mediaguard.recovery.executor import SimulatedCrash

ORIGINAL = b"ORIGINAL-H264-" * 5000
NEW = b"AV1-RESULT-" * 3000
POINTS = ["lock", "validate", "quarantine:after_action", "quarantine", "stage_temp",
          "activate:after_action", "activate", "record", "refresh"]


class Env:
    def __init__(self, ctx, tmp_path: Path, ext: str = ".mkv") -> None:
        self.ctx = ctx
        self.dir = tmp_path / "library" / "Show" / "Season 01"
        self.dir.mkdir(parents=True)
        self.lib = self.dir / f"Show - S01E01{ext}"
        self.lib.write_bytes(ORIGINAL)
        staged_dir = ctx.settings.staging_dir / "17"
        staged_dir.mkdir(parents=True)
        self.staged = staged_dir / "Show - S01E01.mkv"
        self.staged.write_bytes(NEW)
        self.final = self.lib.with_suffix(".mkv")
        ctx.settings.quarantine_dir.mkdir(parents=True, exist_ok=True)
        ctx.config.set("global", "", "recovery.min_free_gb", 0, "test")  # sandbox-schijf

    def snapshot(self) -> dict:
        st = os.stat(self.lib)
        return {"path": str(self.lib), "size": st.st_size, "mtime_ns": st.st_mtime_ns,
                "inode": st.st_ino, "device": st.st_dev, "nlink": st.st_nlink}

    def txn(self, **kw) -> int:
        args = {"library_path": str(self.lib), "staged_path": str(self.staged),
                "staged_size": len(NEW), "source_snapshot": self.snapshot(),
                "instance_id": "sonarr-series", "media_kind": "episode_file",
                "provider_id": 991, "refresh": {"command": "RescanSeries", "seriesId": 12,
                                                "episode_ids": [345]}} | kw
        txn_id, _ = service.create_transaction(self.ctx, **args)
        return txn_id

    def assert_consistent(self, txn_id: int) -> dict:
        txn = executor.load(self.ctx, txn_id)
        assert txn["state"] in ("committed", "rolled_back", "aborted"), txn
        assert not list(self.dir.glob(".mg-*")), "tijdelijk bestand achtergebleven"
        if txn["state"] == "committed":
            assert self.final.read_bytes() == NEW
            if self.final != self.lib:
                assert not self.lib.exists()
            item = self.ctx.db.fetch_one("SELECT * FROM quarantine_items WHERE id=?",
                                         (txn["quarantine_item_id"],))
            assert item["status"] == "restorable"
            assert Path(item["quarantine_path"]).read_bytes() == ORIGINAL
        else:
            assert self.lib.read_bytes() == ORIGINAL
            if self.final != self.lib:
                assert not self.final.exists()
        assert locks.holder(self.ctx.db, executor.resource_for(txn)) is None
        return txn


@pytest.fixture
def env(ctx, tmp_path):
    ctx.services["instance_registry"] = FakeRegistry(FakeInstance("sonarr-series"))
    return Env(ctx, tmp_path)


async def test_happy_path_commit_and_refresh(env, ctx):
    txn_id = env.txn()
    txn = await executor.run_transaction(ctx, txn_id)
    assert txn["state"] == "committed" and txn["quarantine_method"] == "link"
    env.assert_consistent(txn_id)
    assert ctx.services["instance_registry"].arr.commands == [
        ("RescanSeries", {"seriesId": 12})]
    journal = [r["step"] for r in ctx.db.fetch_all(
        "SELECT step FROM replacement_journal WHERE transaction_id=? AND phase='done'",
        (txn_id,))]
    assert journal[-1] == "committed" and "quarantined" in journal
    audit = ctx.db.fetch_one("SELECT * FROM audit_events WHERE action="
                             "'recovery.replace_committed'")
    assert audit is not None


@pytest.mark.parametrize("force_copy", [False, True], ids=["link", "copy"])
@pytest.mark.parametrize("point", POINTS)
async def test_crash_at_every_step_then_journal_replay(env, ctx, point, force_copy):
    ctx.services["recovery_force_copy"] = force_copy
    txn_id = env.txn()

    def crash(p: str) -> None:
        if p == point:
            raise SimulatedCrash(p)

    with pytest.raises(SimulatedCrash):
        await executor.run_transaction(ctx, txn_id, fault=crash)
    executor.recover_pending(ctx)
    txn = env.assert_consistent(txn_id)
    activated = POINTS.index(point) >= POINTS.index("activate:after_action")
    assert txn["state"] == ("committed" if activated else
                            ("aborted" if point in ("lock", "validate") else "rolled_back"))


@pytest.mark.parametrize("point", POINTS)
async def test_error_at_every_step_is_handled_in_process(env, ctx, point):
    txn_id = env.txn()

    def boom(p: str) -> None:
        if p == point:
            raise RuntimeError(f"fout bij {p}")

    await executor.run_transaction(ctx, txn_id, fault=boom)
    txn = env.assert_consistent(txn_id)
    assert txn["error"] is None or "fout bij" in txn["error"] or txn["state"] == "committed"


@pytest.mark.parametrize("point", POINTS + ["activate:after_unlink"])
async def test_extension_change_crash_consistency(ctx, tmp_path, point):
    ctx.services["instance_registry"] = FakeRegistry(FakeInstance("sonarr-series"))
    e = Env(ctx, tmp_path, ext=".mp4")
    txn_id = e.txn()

    def crash(p: str) -> None:
        if p == point:
            raise SimulatedCrash(p)

    with pytest.raises(SimulatedCrash):
        await executor.run_transaction(ctx, txn_id, fault=crash)
    executor.recover_pending(ctx)
    e.assert_consistent(txn_id)


async def test_crash_recovered_by_recovery_role_startup(env, ctx, settings):
    txn_id = env.txn()

    def crash(p: str) -> None:
        if p == "activate:after_action":
            raise SimulatedCrash(p)

    with pytest.raises(SimulatedCrash):
        await executor.run_transaction(ctx, txn_id, fault=crash)
    assert executor.load(ctx, txn_id)["state"] == "temp_ready"
    rec_settings = load_settings(**{**{k: getattr(settings, k) for k in
                                       settings.__dataclass_fields__}, "role": "recovery"})
    with TestClient(create_app(rec_settings)) as rc:
        rctx = rc.app.state.ctx
        assert executor.load(rctx, txn_id)["state"] == "committed"
        assert service.readiness(rctx)["ready"]
    env.assert_consistent(txn_id)


async def test_disk_full_during_copy(env, ctx, monkeypatch):
    real = fsops.copy_with_hash

    def full(src, dst, **kw):
        if dst.name.startswith(".mg-replace"):
            dst.write_bytes(NEW[:100])  # half geschreven, zonder eigen opruiming
            raise OSError(errno.ENOSPC, "No space left on device")
        return real(src, dst, **kw)

    monkeypatch.setattr(fsops, "copy_with_hash", full)
    txn_id = env.txn()
    txn = await executor.run_transaction(ctx, txn_id)
    # opslagprobleem: tijdelijke weigering (encode blijft bewaard), origineel intact
    assert txn["state"] == "aborted" and "ruimte" in txn["error"]
    env.assert_consistent(txn_id)


async def test_insufficient_space_aborts_before_touching(env, ctx, monkeypatch):
    monkeypatch.setattr(fsops, "free_bytes", lambda _p: 10)
    txn = await executor.run_transaction(ctx, env.txn())
    assert txn["state"] == "aborted" and "ruimte" in txn["error"]
    assert not list(ctx.settings.quarantine_dir.rglob("*.mkv"))
    env.assert_consistent(txn["id"])


async def test_permission_denied(env, ctx, monkeypatch):
    def denied(src, dst, **kw):
        raise PermissionError(errno.EACCES, "Permission denied", str(dst))

    monkeypatch.setattr(fsops, "copy_with_hash", denied)
    txn = await executor.run_transaction(ctx, env.txn())
    assert txn["state"] == "aborted" and "schrijfrechten" in txn["error"]
    env.assert_consistent(txn["id"])


async def test_source_modified_after_encode(env, ctx):
    txn_id = env.txn()
    with env.lib.open("ab") as fh:
        fh.write(b"extra")
    txn = await executor.run_transaction(ctx, txn_id)
    assert txn["state"] == "aborted" and "gewijzigd" in txn["error"]
    assert env.lib.read_bytes() == ORIGINAL + b"extra"
    assert env.staged.exists()


async def test_source_modified_during_transaction_is_conflict_not_loss(env, ctx):
    txn_id = env.txn()

    def modify(p: str) -> None:
        if p == "stage_temp":
            os.utime(env.lib, ns=(1, 1))

    txn = await executor.run_transaction(ctx, txn_id, fault=modify)
    assert txn["state"] == "conflict"
    assert env.lib.read_bytes() == ORIGINAL  # niets vervangen
    assert not list(env.dir.glob(".mg-*"))


async def test_hardlinked_source_policy(env, ctx, tmp_path):
    seed = tmp_path / "downloads" / "seed.mkv"
    seed.parent.mkdir()
    os.link(env.lib, seed)
    txn = await executor.run_transaction(ctx, env.txn())
    assert txn["state"] == "aborted" and "hardlinks" in txn["error"]
    ctx.config.set("global", "", "recovery.hardlink_policy", "break_with_confirmation", "t")
    txn = await executor.run_transaction(ctx, env.txn())
    assert txn["state"] == "aborted" and "bevestiging" in txn["error"]
    txn = await executor.run_transaction(ctx, env.txn(hardlink_break_confirmed=True))
    assert txn["state"] == "committed"
    assert seed.read_bytes() == ORIGINAL  # seedende kopie onaangetast
    env.assert_consistent(txn["id"])


async def test_lock_import_emergency_and_corrupt_staged(env, ctx):
    locks.acquire(ctx.db, locks.media_resource("sonarr-series", "episode_file", 991),
                  "import:sonarr", "import")
    txn = await executor.run_transaction(ctx, env.txn())
    assert txn["state"] == "aborted" and "bezet" in txn["error"]
    locks.release(ctx.db, locks.media_resource("sonarr-series", "episode_file", 991),
                  "import:sonarr")
    ctx.db.execute(
        "INSERT INTO release_provenance(instance_id,event_type,download_id,episode_ids_json,"
        "idempotency_key,received_at) VALUES ('sonarr-series','Grab','D9','[345]','g',"
        "datetime('now'))")
    txn = await executor.run_transaction(ctx, env.txn())
    assert txn["state"] == "aborted" and "download" in txn["error"]
    ctx.db.execute("DELETE FROM release_provenance")
    ctx.state_set("emergency_stop", {"active": True})
    assert (await executor.run_transaction(ctx, env.txn()))["state"] == "aborted"
    ctx.state_set("emergency_stop", {"active": False})
    txn = await executor.run_transaction(ctx, env.txn(staged_sha256="0" * 64))
    assert txn["state"] == "aborted" and "sha256" in txn["error"]
    assert env.lib.read_bytes() == ORIGINAL


async def test_paths_outside_staging_refused(env, ctx, tmp_path):
    rogue = tmp_path / "elders.mkv"
    rogue.write_bytes(NEW)
    txn = await executor.run_transaction(ctx, env.txn(staged_path=str(rogue)))
    assert txn["state"] == "aborted" and "staging" in txn["error"]


async def test_arr_refresh_failure_is_warning_and_retried(env, ctx):
    ctx.services["instance_registry"].arr.fail = True
    txn = await executor.run_transaction(ctx, env.txn())
    assert txn["state"] == "committed" and "Arr-refresh mislukt" in txn["warnings_json"]
    assert ctx.db.fetch_one("SELECT 1 FROM jobs WHERE job_type='recovery.refresh'")


async def test_restore_from_quarantine_api(env, ctx, client):
    txn = await executor.run_transaction(ctx, env.txn())
    item_id = txn["quarantine_item_id"]
    assert client.post(f"/api/v1/quarantine/{item_id}/restore", json={}).status_code == 422
    r = client.post(f"/api/v1/quarantine/{item_id}/restore", json={"confirm": True})
    assert r.status_code == 200, r.text
    restore = await executor.run_transaction(ctx, r.json()["transaction_id"])
    assert restore["state"] == "committed"
    assert env.lib.read_bytes() == ORIGINAL
    items = {i["id"]: i for i in client.get("/api/v1/quarantine").json()}
    assert items[item_id]["status"] == "restored"
    av1_item = items[restore["quarantine_item_id"]]
    assert av1_item["status"] == "restorable"  # ook de AV1-versie blijft herstelbaar
    assert Path(av1_item["quarantine_path"]).read_bytes() == NEW
    detail = client.get(f"/api/v1/recovery/transactions/{restore['id']}").json()
    assert detail["kind"] == "restore" and detail["journal"]
    assert client.post(f"/api/v1/quarantine/{item_id}/restore",
                       json={"confirm": True}).status_code == 409


async def test_jobs_are_role_bound(env, ctx, settings):
    txn_id = env.txn()
    await jobs.JobRunner(ctx).run_until_idle()
    assert executor.load(ctx, txn_id)["state"] == "pending"  # core voert niets uit
    all_settings = load_settings(**{**{k: getattr(settings, k) for k in
                                       settings.__dataclass_fields__}, "role": "all"})
    app = create_app(all_settings)
    actx = app.state.ctx
    actx.services["instance_registry"] = FakeRegistry(FakeInstance("sonarr-series"))
    await jobs.JobRunner(actx).run_until_idle()
    assert executor.load(actx, txn_id)["state"] == "committed"


async def test_zfs_snapshot_with_fake_truenas(env, ctx):
    calls = []

    def truenas(request: httpx.Request) -> httpx.Response:
        calls.append((request.url.path, request.headers.get("Authorization")))
        if request.url.path == "/api/v2.0/pool/snapshot":
            return httpx.Response(404)
        return httpx.Response(200, json={"id": "voorbeeldpool/media@mediaguard-txn-1"})

    ctx.services["truenas_transport"] = httpx.MockTransport(truenas)
    ctx.config.set("global", "", "recovery.zfs_snapshot_enabled", True, "t")
    ctx.config.set("global", "", "recovery.truenas_url", "https://truenas.local", "t")
    ctx.config.set("global", "", "recovery.zfs_dataset", "voorbeeldpool/media", "t")
    ctx.secrets.set("truenas:apikey", "1-abcdef", "t")
    txn = await executor.run_transaction(ctx, env.txn())
    assert txn["state"] == "committed" and txn["zfs_snapshot"].startswith("voorbeeldpool/media@")
    assert calls[-1] == ("/api/v2.0/zfs/snapshot", "Bearer 1-abcdef")
    ctx.services["truenas_transport"] = httpx.MockTransport(lambda r: httpx.Response(500))
    env.lib.write_bytes(ORIGINAL)
    failed = await executor.run_transaction(ctx, env.txn())
    assert failed["state"] == "aborted" and "TrueNAS" in failed["error"]


def test_quarantine_purge_rules(ctx, client):
    now = "2020-01-01T00:00:00+00:00"
    ctx.db.execute(
        "INSERT INTO replacement_transactions(id,kind,library_path,final_path,staged_path,"
        "source_snapshot_json,state,created_at,updated_at) VALUES (1,'replace','a','a','b','{}',"
        "'committed',?,?),(2,'replace','c','c','d','{}','quarantined',?,?)",
        (now, now, now, now))
    ctx.db.execute(
        "INSERT INTO quarantine_items(transaction_id,original_path,quarantine_path,method,"
        "status,created_at,pinned) VALUES (1,'a','/nope/a','link','restorable',?,0),"
        "(1,'a2','/nope/a2','link','restorable',?,1),(2,'c','/nope/c','link','held',?,0)",
        (now, now, now))
    # zonder geslaagde vrijgavecontrole wordt niets automatisch opgeruimd
    assert service.purge_candidates(ctx) == []
    ctx.db.execute("INSERT INTO quarantine_release_checks(item_id,status,checked_at) "
                   "SELECT id,'passed',? FROM quarantine_items", (now,))
    candidates = service.purge_candidates(ctx)
    assert [c["original_path"] for c in candidates] == ["a"]  # niet vastgezet, niet 'held'
    dry = client.post("/api/v1/quarantine/purge", json={}).json()
    assert dry["dry_run"] and dry["count"] == 1
    held = ctx.db.fetch_one("SELECT id FROM quarantine_items WHERE status='held'")["id"]
    assert client.request("DELETE", f"/api/v1/quarantine/{held}",
                          json={"confirm": True}).status_code == 409


def test_readiness_and_api_lists(client, ctx):
    assert client.get("/api/v1/recovery/readiness").json()["ready"] is False
    ctx.settings.quarantine_dir.mkdir(parents=True, exist_ok=True)
    service.mark_heartbeat(ctx)
    assert client.get("/api/v1/recovery/readiness").json()["ready"] is True
    assert client.get("/api/v1/recovery/transactions").json() == []


async def test_emergency_stop_mid_transaction_rolls_back(env, ctx):
    txn_id = env.txn()

    def stop(p: str) -> None:
        if p == "stage_temp":
            ctx.state_set("emergency_stop", {"active": True})

    txn = await executor.run_transaction(ctx, txn_id, fault=stop)
    assert txn["state"] == "aborted" and "Noodstop" in txn["error"]
    env.assert_consistent(txn_id)
