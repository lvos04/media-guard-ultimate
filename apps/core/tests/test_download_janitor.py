"""Opruimer voor downloadmappen: alleen wat aantoonbaar niemand meer gebruikt, via de
prullenbak, en nooit buiten de downloadmappen of in de bibliotheek."""

from __future__ import annotations

import asyncio
import errno
import fcntl
import multiprocessing
import os
import select
import sqlite3
import stat
import subprocess
import sys
from contextlib import closing, contextmanager
from datetime import UTC, datetime, timedelta
from pathlib import Path
from unittest.mock import patch
from urllib.parse import parse_qs

import httpx
import pytest
from fastapi.testclient import TestClient

from mediaguard import jobs, monitor
from mediaguard import storage_forecast as storage
from mediaguard.app import create_app
from mediaguard.config import load_settings
from mediaguard.downloads import janitor as J
from mediaguard.downloads.clients import ClientItem, QbitClient, Root, SabClient, seeding_status
from mediaguard.provisioning import store
from mediaguard.provisioning.store import DownloadClientCfg

NOW = datetime(2026, 11, 5, 12, 0, tzinfo=UTC)
OLD = NOW - timedelta(days=30)


def _mk(path: Path, size: int = 10) -> Path:
    path.mkdir(parents=True, exist_ok=True)
    (path / "video.mkv").write_bytes(b"x" * size)
    return path


def _age(path: Path, days: int) -> None:
    t = (NOW - timedelta(days=days)).timestamp()
    os.utime(path, (t, t))


@pytest.fixture
def layout(tmp_path: Path):
    dl = tmp_path / "data" / "downloads"
    tv = dl / "complete" / "tv"
    tv.mkdir(parents=True)
    torrents = dl / "torrents" / "movies"
    torrents.mkdir(parents=True)
    lib = tmp_path / "data" / "media" / "tv"
    lib.mkdir(parents=True)
    return {"tv": tv, "torrents": torrents, "lib": lib}


def _snap(layout, items, **kw) -> J.Snapshot:
    return J.Snapshot(roots=[Root(str(layout["tv"]), "sab", "complete", "tv"),
                             Root(str(layout["torrents"]), "qbit", "torrent", "movies")],
                      library_roots=[str(layout["lib"])], items=items,
                      managed={"tv", "movies"}, **kw)


def _nzb(layout, name: str, size: int = 10, **kw) -> ClientItem:
    path = _mk(layout["tv"] / name, size)
    return ClientItem("sab", "nzb", name.upper(), name, "tv", str(path), active=False,
                      completed_at=kw.pop("completed_at", NOW - timedelta(days=1)), **kw)


class FakeSab:
    def __init__(self):
        self.deleted: list[str] = []

    async def delete_history(self, nzo_id):
        self.deleted.append(nzo_id)


class FakeQbit:
    def __init__(self):
        self.removed: list[str] = []

    async def remove(self, h):
        self.removed.append(h)


@pytest.mark.asyncio
async def test_imported_goes_to_trash_then_purged(ctx, layout):
    it = _nzb(layout, "show.s01e01")
    snap = _snap(layout, [it], imported_ids={it.id})
    p = J.plan(ctx, snap, NOW)
    assert [a.reason for a in p.actions] == ["geïmporteerd door Sonarr/Radarr"]
    sab = FakeSab()
    res = await J.execute(ctx, p, {"sab": sab}, actor="t")
    assert not (layout["tv"] / "show.s01e01").exists()
    trash = list((layout["tv"] / J.TRASH_DIR).rglob("video.mkv"))
    assert len(trash) == 1 and res["done"][0]["freed"] == 10
    assert sab.deleted == [it.id]
    # binnen de bewaartermijn blijft het staan; daarna weg
    assert J.purge(ctx, datetime.now(UTC))["purged"] == 0
    assert J.purge(ctx, datetime.now(UTC) + timedelta(days=8))["purged"] == 1
    assert not trash[0].exists()
    audit = ctx.db.fetch_all("SELECT action FROM audit_events WHERE action LIKE 'downloads.%'")
    assert {r["action"] for r in audit} >= {"downloads.trashed", "downloads.purged"}


@pytest.mark.asyncio
async def test_restore_puts_it_back(ctx, layout):
    it = _nzb(layout, "show.s01e02")
    p = J.plan(ctx, _snap(layout, [it], imported_ids={it.id}), NOW)
    res = await J.execute(ctx, p, {"sab": FakeSab()}, actor="t")
    J.restore(ctx, res["done"][0]["id"], actor="t")
    assert (layout["tv"] / "show.s01e02" / "video.mkv").exists()


def test_active_or_queued_never(ctx, layout):
    busy = _nzb(layout, "busy")
    busy.active = True
    queued = _nzb(layout, "queued")
    snap = _snap(layout, [busy, queued], imported_ids={busy.id, queued.id},
                 queue_ids={queued.id})
    p = J.plan(ctx, snap, NOW)
    assert p.actions == []
    assert p.kept["client is er nog mee bezig"] == 1
    assert p.kept["staat nog in een Sonarr/Radarr-wachtrij"] == 1


def test_old_install_queue_counts_as_owner(ctx, layout):
    """Tijdens de migratie delen de oude installaties de clients: hun wachtrij is eigenaar."""
    it = _nzb(layout, "old-owner")
    p = J.plan(ctx, _snap(layout, [it], imported_ids={it.id}, queue_ids={it.id}), NOW)
    assert p.actions == []


@pytest.mark.asyncio
async def test_old_installs_are_asked_too(ctx, monkeypatch):
    from mediaguard.migration import sources

    class Fake:
        def __init__(self, ids):
            self.ids = ids

        async def __aenter__(self):
            return self

        async def __aexit__(self, *a):
            return None

        async def queue(self, all_pages=False):
            return [{"downloadId": i} for i in self.ids]

        async def history_since(self, _d):
            return [{"downloadId": "abc", "eventType": "downloadFolderImported"}]

        async def root_folders(self):
            return [{"path": "/data/media/tv"}]

    monkeypatch.setattr(sources, "list_sources", lambda _ctx: [{"id": "oud", "kind": "live"}])
    monkeypatch.setattr(sources, "source_client", lambda _ctx, _sid, **_k: Fake(["sab_old"]))
    snap = J.Snapshot([], [], [])
    await J._arr_ids(ctx, snap)
    assert "SAB_OLD" in snap.queue_ids and "ABC" in snap.imported_ids
    assert "/data/media/tv" in snap.library_roots


def test_unimported_only_after_days(ctx, layout):
    fresh = _nzb(layout, "fresh", completed_at=NOW - timedelta(days=2))
    old = _nzb(layout, "old", completed_at=OLD)
    # zolang het herstelvangnet het niet geprobeerd heeft, blijft ook de oude staan
    p = J.plan(ctx, _snap(layout, [fresh, old]), NOW)
    assert p.actions == []
    assert any("herstelpoging" in k for k in p.kept)
    ctx.state_set("download_recovery", {"items": {old.id.upper(): {
        "status": "not_recoverable", "reason_nl": "geen grab"}}})
    p = J.plan(ctx, _snap(layout, [fresh, old]), NOW)
    assert [a.path.name for a in p.actions if a.path] == ["old"]
    assert "nooit geïmporteerd" in p.actions[0].reason


def test_failed_handled_by_arr(ctx, layout):
    it = _nzb(layout, "broken", failed=True)
    p = J.plan(ctx, _snap(layout, [it], failed_handled_ids={it.id}), NOW)
    assert p.actions and "geblokkeerd" in p.actions[0].reason
    it2 = _nzb(layout, "broken2", failed=True)
    assert J.plan(ctx, _snap(layout, [it2]), NOW).actions == []


def test_orphans_only_after_days(ctx, layout):
    orphan = _mk(layout["tv"] / "nobody-knows")
    _age(orphan, 10)
    p1 = J.plan(ctx, _snap(layout, []), NOW)  # eerst gezien: nog niet
    assert p1.actions == [] and p1.kept["onbekende map, nog te nieuw"] == 1
    p2 = J.plan(ctx, _snap(layout, []), NOW + timedelta(days=4))
    assert [a.path.name for a in p2.actions] == ["nobody-knows"]
    # een recent gewijzigde map blijft, ook al is hij al lang onbekend
    fresh = _mk(layout["tv"] / "recent")
    J.plan(ctx, _snap(layout, []), NOW - timedelta(days=10))
    assert all(a.path.name != "recent" for a in J.plan(ctx, _snap(layout, []), NOW).actions)
    assert fresh.exists()


def test_known_paths_are_never_orphans(ctx, layout):
    p = _mk(layout["tv"] / "known")
    _age(p, 10)
    other_cat = ClientItem("sab", "nzb", "X", "known", "software", str(p), active=False)
    J.plan(ctx, _snap(layout, [other_cat]), NOW)
    plan = J.plan(ctx, _snap(layout, [other_cat]), NOW + timedelta(days=5))
    assert plan.actions == []


def test_torrent_under_share_limit_kept(ctx, layout):
    path = _mk(layout["torrents"] / "Movie.2023")
    t = ClientItem("qbit", "torrent", "HASH1", "Movie", "movies", str(path), active=False,
                   seeding_done=False, seeding_reason="deellimiet nog niet gehaald")
    p = J.plan(ctx, _snap(layout, [t], imported_ids={"HASH1"}), NOW)
    assert p.actions == [] and p.kept["deellimiet nog niet gehaald"] == 1


@pytest.mark.asyncio
async def test_hardlinked_torrent_only_removed_from_client(ctx, layout):
    path = _mk(layout["torrents"] / "Linked.2023")
    os.link(path / "video.mkv", layout["lib"] / "Linked.mkv")
    t = ClientItem("qbit", "torrent", "HASH2", "Linked", "movies", str(path), active=False)
    p = J.plan(ctx, _snap(layout, [t], imported_ids={"HASH2"}), NOW)
    assert len(p.actions) == 1 and not p.actions[0].trash_data
    q = FakeQbit()
    await J.execute(ctx, p, {"qbit": q}, actor="t")
    assert q.removed == ["HASH2"] and (path / "video.mkv").exists()
    assert (layout["lib"] / "Linked.mkv").exists()


@pytest.mark.asyncio
async def test_unlinked_torrent_data_to_trash(ctx, layout):
    path = _mk(layout["torrents"] / "Done.2023")
    t = ClientItem("qbit", "torrent", "HASH3", "Done", "movies", str(path), active=False)
    p = J.plan(ctx, _snap(layout, [t], imported_ids={"HASH3"}), NOW)
    q = FakeQbit()
    await J.execute(ctx, p, {"qbit": q}, actor="t")
    assert q.removed == ["HASH3"] and not path.exists()


def test_root_overlapping_library_refused(ctx, layout, tmp_path):
    item = _nzb(layout, "x")
    snap = J.Snapshot(roots=[Root(str(tmp_path / "data"), "sab", "complete", "tv")],
                      library_roots=[str(layout["lib"])], items=[item],
                      imported_ids={item.id}, managed={"tv"})
    p = J.plan(ctx, snap, NOW)
    assert p.actions == []
    assert p.refused_roots and "bibliotheek" in p.refused_roots[0]["reason"]


def test_path_outside_roots_refused(ctx, layout, tmp_path):
    outside = _mk(tmp_path / "elsewhere" / "thing")
    it = ClientItem("sab", "nzb", "OUT", "thing", "tv", str(outside), active=False)
    p = J.plan(ctx, _snap(layout, [it], imported_ids={"OUT"}), NOW)
    assert p.actions == [] and outside.exists()
    with pytest.raises(PermissionError):
        J._trash(ctx, outside, layout["tv"], "x", actor="t")


def test_symlink_escape_refused(ctx, layout, tmp_path):
    target = _mk(tmp_path / "data" / "media" / "tv" / "Show")
    link = layout["tv"] / "sneaky"
    link.symlink_to(target)
    it = ClientItem("sab", "nzb", "SN", "sneaky", "tv", str(link), active=False)
    p = J.plan(ctx, _snap(layout, [it], imported_ids={"SN"}), NOW)
    assert p.actions == [] and (target / "video.mkv").exists()


@pytest.mark.asyncio
async def test_run_skips_everything_when_something_unreachable(ctx, layout):
    it = _nzb(layout, "keep-me")

    async def broken(_ctx):
        raise httpx.ConnectError("geen verbinding")

    res = await J.run_once(ctx, gatherer=broken)
    assert res["status"] == "skipped" and (layout["tv"] / "keep-me").exists()
    assert it.path


@pytest.mark.asyncio
async def test_run_once_summary_and_status_api(ctx, client, layout):
    it = _nzb(layout, "done.s01e03", size=2048)

    async def gatherer(_ctx):
        return _snap(layout, [it], imported_ids={it.id}), {"sab": FakeSab()}

    res = await J.run_once(ctx, gatherer=gatherer)
    assert res["trashed"] == 1 and res["summary_nl"].startswith("Opgeruimd: 1 oude download")
    data = client.get("/api/v1/downloads/cleanup").json()
    assert data["last_run"]["trashed"] == 1 and data["trash"][0]["name"] == "done.s01e03"
    tid = data["trash"][0]["id"]
    assert client.post(f"/api/v1/downloads/cleanup/trash/{tid}/restore").status_code == 200
    assert (layout["tv"] / "done.s01e03").exists()


@pytest.fixture
def cleanup_round(ctx, layout):
    previous = _nzb(layout, "oude-prullenbak")
    item = _nzb(layout, "nieuwe-download")
    root = layout["tv"].resolve()
    trashed = J._trash(ctx, Path(previous.path), root, "test", actor="test",
                       now=datetime.now(UTC) - timedelta(days=8))
    sab = FakeSab()

    async def gatherer(_ctx):
        return _snap(layout, [item], imported_ids={item.id}), {"sab": sab}

    row = ctx.db.fetch_one("SELECT trash_path FROM download_janitor_trash WHERE id=?",
                           (trashed["id"],))
    return gatherer, Path(item.path), Path(row["trash_path"]), sab


def _healthy_pool(ctx):
    return storage.record_pool(ctx, {"pools": [{"name": "voorbeeldpool", "status": "ONLINE",
                                               "healthy": True}]})


@pytest.fixture
def pool_configured(ctx):
    ctx.config.set("global", "", "recovery.truenas_url", "https://truenas.invalid", "test")
    ctx.secrets.set("truenas:apikey", "sleutel", actor="test")


@pytest.mark.asyncio
@pytest.mark.parametrize("measurement", ["degraded", "missing", "unreachable", "stale", "future",
                                         "empty", "malformed", "bad-response", "read-error"])
async def test_run_once_pauses_on_unhealthy_or_unknown_pool(
        ctx, client, cleanup_round, pool_configured, measurement, monkeypatch):
    gatherer, source, trash, sab = cleanup_round
    if measurement == "degraded":
        storage.record_pool(ctx, {"pools": [{"name": "voorbeeldpool", "status": "DEGRADED",
                                            "healthy": False}]})
    elif measurement == "unreachable":
        _healthy_pool(ctx)
        storage.record_pool(ctx, {"error_nl": "Onbereikbaar"})
    elif measurement in {"stale", "future"}:
        state = _healthy_pool(ctx)
        delta = -16 if measurement == "stale" else 5
        state["checked_at"] = (datetime.now(UTC) + timedelta(minutes=delta)).isoformat()
        ctx.state_set(storage.POOL_STATE, state)
    elif measurement == "empty":
        storage.record_pool(ctx, {"pools": []})
    elif measurement == "malformed":
        ctx.state_set(storage.POOL_STATE, {**_healthy_pool(ctx), "pools": [None]})
    elif measurement == "bad-response":
        _healthy_pool(ctx)
        ctx.services["truenas_transport"] = httpx.MockTransport(lambda _req: httpx.Response(
            200, json=[{"name": "voorbeeldpool", "status": "ONLINE", "healthy": "false"}]))
        await storage.refresh_pool(ctx)
    elif measurement == "read-error":
        state_get = type(ctx).state_get

        def broken_state(self, key, default=None):
            if key == storage.POOL_STATE:
                raise OSError("test")
            return state_get(self, key, default)

        monkeypatch.setattr(type(ctx), "state_get", broken_state)

    result = await J.run_once(ctx, gatherer=gatherer)

    assert result["status"] == "skipped"
    assert result["trashed"] == result["purged"] == result["temp_removed"] == 0
    assert (source / "video.mkv").exists() and (trash / "video.mkv").exists()
    assert sab.deleted == []
    message = "niet gezond" if measurement == "degraded" else "onbekend"
    assert message in result["summary_nl"]
    assert client.get("/api/v1/downloads/cleanup").json()["last_run"]["summary_nl"] == (
        result["summary_nl"])


@pytest.mark.asyncio
@pytest.mark.parametrize("configured", [True, False])
async def test_run_once_cleans_with_healthy_pool_or_without_key(ctx, cleanup_round, configured,
                                                               request):
    gatherer, source, trash, sab = cleanup_round
    if configured:
        request.getfixturevalue("pool_configured")
        _healthy_pool(ctx)
    else:
        # Een eerder opgeslagen meting mag een losse installatie zonder sleutel niet blokkeren.
        storage.record_pool(ctx, {"pools": [{"status": "DEGRADED", "healthy": False}]})

    result = await J.run_once(ctx, gatherer=gatherer)

    assert result["status"] == "ok" and result["trashed"] == result["purged"] == 1
    assert not source.exists() and not trash.exists()
    assert sab.deleted == [source.name.upper()]


@pytest.mark.asyncio
async def test_run_once_rechecks_pool_after_gathering(ctx, cleanup_round, pool_configured):
    gatherer, source, trash, sab = cleanup_round
    _healthy_pool(ctx)

    async def pool_went_offline(_ctx):
        snapshot = await gatherer(_ctx)
        storage.record_pool(ctx, {"error_nl": "Onbereikbaar"})
        return snapshot

    result = await J.run_once(ctx, gatherer=pool_went_offline)

    assert result["status"] == "skipped" and result["trashed"] == result["purged"] == 0
    assert source.exists() and trash.exists() and sab.deleted == []


def _pool_response(ctx, healthy, expected_host=None):
    row = {"name": "proef", "healthy": healthy, "status": "ONLINE" if healthy else "DEGRADED"}

    def response(request):
        if expected_host:
            assert request.url.host == expected_host
        return httpx.Response(200, json=[row])

    ctx.services["truenas_transport"] = httpx.MockTransport(response)


async def _expect_pause(ctx, cleanup_round):
    gatherer, source, trash, sab = cleanup_round
    result = await J.run_once(ctx, gatherer=gatherer)
    assert (result["status"], result["trashed"], result["purged"]) == ("skipped", 0, 0)
    assert source.is_dir() and trash.is_dir() and not sab.deleted


async def _resume_with_healthy_pool(ctx, cleanup_round, expected_host=None):
    _pool_response(ctx, True, expected_host)
    await storage.refresh_pool(ctx)
    result = await J.run_once(ctx, gatherer=cleanup_round[0])
    assert (result["status"], result["trashed"], result["purged"]) == ("ok", 1, 1)


@pytest.mark.parametrize("change", ["adres", "heen-en-terug", "sleutel", "tls"])
async def test_pool_bronwisseling(ctx, cleanup_round, pool_configured, change):
    # De werker heeft een eigen instellingencache en leest dezelfde database als de kern.
    worker = create_app(ctx.settings).state.ctx
    with closing(worker.db):
        _pool_response(worker, True)
        await storage.refresh_pool(worker)
        if change == "sleutel":
            ctx.secrets.set("truenas:apikey", os.urandom(16).hex(), "test")
        elif change == "tls":
            ctx.config.set("global", "", "recovery.truenas_verify_tls", False, "test")
        else:
            url = ctx.config.value("recovery.truenas_url")
            ctx.config.set("global", "", "recovery.truenas_url", "https://tweede.invalid", "test")
            if change == "heen-en-terug":
                ctx.config.set("global", "", "recovery.truenas_url", url, "test")
        await _expect_pause(worker, cleanup_round)
        host = "tweede.invalid" if change == "adres" else "truenas.invalid"
        await _resume_with_healthy_pool(worker, cleanup_round, host)


async def test_pool_meting_van_voor_upgrade(ctx, cleanup_round, pool_configured):
    # Opslagvorm van de oude lezer: de tekstwaarde "false" werd toen healthy=True.
    ctx.state_set(storage.POOL_STATE, {
        "reachable": True, "checked_at": datetime.now(UTC).isoformat(), "degraded": [],
        "degraded_since": None, "pools": [{"name": "proef", "status": "ONLINE", "healthy": True}],
    })
    worker = create_app(ctx.settings).state.ctx
    with closing(worker.db):
        await _expect_pause(worker, cleanup_round)
        await _resume_with_healthy_pool(worker, cleanup_round)


async def test_pool_volgorde_bewaking_en_prognose(ctx, cleanup_round, pool_configured):
    received, deliver = asyncio.Event(), asyncio.Event()

    async def delayed(_):
        received.set()
        await deliver.wait()
        return httpx.Response(200, json=[{"name": "proef", "status": "ONLINE", "healthy": True}])

    ctx.services["truenas_transport"] = httpx.MockTransport(delayed)
    first = asyncio.create_task(storage.refresh_pool(ctx))
    try:
        await asyncio.wait_for(received.wait(), 5)
        worker = create_app(ctx.settings).state.ctx
        with closing(worker.db):
            _pool_response(worker, False)
            forecast = await storage.storage_forecast(None, worker)
            assert forecast["truenas"]["pools"][0]["healthy"] is False
            await _expect_pause(ctx, cleanup_round)
    finally:
        deliver.set()
        await first
    await _expect_pause(ctx, cleanup_round)
    await _resume_with_healthy_pool(ctx, cleanup_round)


async def test_pool_versheid_telt_vanaf_meetstart(ctx, cleanup_round, pool_configured, monkeypatch):
    current = storage.utcnow
    old = (datetime.now(UTC) - timedelta(minutes=16)).isoformat()

    def delayed(_):
        monkeypatch.setattr(storage, "utcnow", current)
        return httpx.Response(200, json=[{"name": "proef", "status": "ONLINE", "healthy": True}])

    monkeypatch.setattr(storage, "utcnow", lambda: old)
    ctx.services["truenas_transport"] = httpx.MockTransport(delayed)
    await storage.refresh_pool(ctx)
    await _expect_pause(ctx, cleanup_round)
    await _resume_with_healthy_pool(ctx, cleanup_round)


async def test_pool_publicatiefout(ctx, cleanup_round, pool_configured, monkeypatch):
    _pool_response(ctx, True)
    await storage.refresh_pool(ctx)
    _pool_response(ctx, False)
    ctx.config.set("global", "", "monitor.enabled", False, "test")
    save = ctx.state_set
    failed = False

    def fail_publication(key, value):
        nonlocal failed
        if key == storage.POOL_STATE and value.get("degraded"):
            failed = True
            raise OSError("Gesimuleerde schrijffout")
        save(key, value)

    with monkeypatch.context() as patch:
        patch.setattr(ctx, "state_set", fail_publication)
        await monitor.run_once(ctx)
    assert failed
    worker = create_app(ctx.settings).state.ctx
    with closing(worker.db):
        await _expect_pause(worker, cleanup_round)
        await _resume_with_healthy_pool(worker, cleanup_round)


async def test_meetstart_alleen_lezen(ctx, cleanup_round, pool_configured):
    _pool_response(ctx, True)
    oud = await storage.refresh_pool(ctx)
    worker = create_app(ctx.settings).state.ctx
    with closing(worker.db):
        assert storage.pool_cleanup_pause_reason(worker) is None
        ctx.config.set("global", "", "monitor.enabled", False, "test")
        ctx.db.execute("PRAGMA query_only=ON")
        try:
            await monitor.run_once(ctx)
        finally:
            ctx.db.execute("PRAGMA query_only=OFF")
        # Ook een laat antwoord van vóór de fout mag geen toestemming teruggeven.
        storage.record_pool(ctx, {"pools": oud["pools"]}, request_id=oud["request_id"])
        await _expect_pause(worker, cleanup_round)
    opnieuw = create_app(ctx.settings).state.ctx
    with closing(opnieuw.db):
        await _expect_pause(opnieuw, cleanup_round)
        await _resume_with_healthy_pool(opnieuw, cleanup_round, "truenas.invalid")


async def test_meetstart_in_ander_proces(ctx, cleanup_round, pool_configured):
    _healthy_pool(ctx)
    # Een apart proces erft geen kanaal of SQLite-verbinding van de opruimer.
    script = """
import asyncio
import sqlite3
import sys
from pathlib import Path
from mediaguard.app import create_app
from mediaguard.config import load_settings
from mediaguard.storage_forecast import refresh_pool

ctx = create_app(load_settings(data_dir=Path(sys.argv[1]), start_background=False)).state.ctx
ctx.db.execute("PRAGMA query_only=ON")
try:
    asyncio.run(refresh_pool(ctx))
except sqlite3.OperationalError:
    pass
else:
    raise AssertionError("SQLite heeft de meetopdracht niet geweigerd")
"""
    subprocess.run([sys.executable, "-c", script, str(ctx.settings.data_dir)], check=True)
    await _expect_pause(ctx, cleanup_round)
    _healthy_pool(ctx)
    await storage.shutdown(ctx)
    await _expect_pause(ctx, cleanup_round)
    await _resume_with_healthy_pool(ctx, cleanup_round)


@pytest.mark.parametrize("rechten", ["444", "777"], ids=["datamapslot", "zoekrecht"])
async def test_slotfout_aparte_meetwerker(ctx, cleanup_round, pool_configured, rechten):
    # De meetwerker is al gestart voordat de opruimer zijn eerste gezonde meting krijgt.
    script = """
import asyncio, stat, sys
from pathlib import Path
from mediaguard.app import create_app
from mediaguard.config import load_settings
from mediaguard import storage_forecast as pool

folder = Path(sys.argv[1])
ctx = create_app(load_settings(data_dir=folder, start_background=False)).state.ctx
asyncio.run(pool.startup(ctx))
print("gestart", flush=True)
assert input() == "meet"
mode = stat.S_IMODE(folder.stat().st_mode)
try:
    folder.chmod(mode & ~int(sys.argv[2], 8))
    try:
        asyncio.run(pool.refresh_pool(ctx))
    except PermissionError:
        pass
    else:
        raise AssertionError("De meetstart had moeten worden geweigerd")
finally:
    folder.chmod(mode)
"""
    folder = ctx.settings.data_dir
    mode = stat.S_IMODE(folder.stat().st_mode)
    with subprocess.Popen([sys.executable, "-c", script, str(folder), rechten],
                          stdin=subprocess.PIPE, stdout=subprocess.PIPE, text=True) as worker:
        try:
            assert select.select([worker.stdout], [], [], 15)[0], "De meetwerker startte niet"
            assert worker.stdout.readline().strip() == "gestart"
            old = _healthy_pool(ctx)
            worker.communicate("meet\n", timeout=15)
            assert worker.returncode == 0
        finally:
            if worker.poll() is None:
                worker.kill()
                worker.wait()
            folder.chmod(mode)
    storage.record_pool(ctx, {"pools": old["pools"]}, request_id=old["request_id"])
    await _expect_pause(ctx, cleanup_round)
    await _resume_with_healthy_pool(ctx, cleanup_round)


async def test_leesfout_trekt_toestemming_in(ctx, cleanup_round, pool_configured, monkeypatch):
    _healthy_pool(ctx)
    worker = create_app(ctx.settings).state.ctx
    with closing(worker.db):
        await storage.startup(worker)
        try:
            with monkeypatch.context() as error:
                error.setattr(worker.secrets, "get", lambda _: None)
                assert storage.pool_cleanup_pause_reason(worker)
        finally:
            await storage.shutdown(worker)
    await _expect_pause(ctx, cleanup_round)
    await _resume_with_healthy_pool(ctx, cleanup_round)


async def test_slotfout_tijdens_toestemmingscontrole(ctx, cleanup_round, pool_configured,
                                                     monkeypatch):
    _healthy_pool(ctx)
    meter = create_app(ctx.settings).state.ctx
    await storage.startup(meter)
    open_pipe = storage._request_pipe

    def weiger_slot():
        raise PermissionError("datamapslot")

    @contextmanager
    def onderbroken_controle(*args, **kwargs):
        with open_pipe(*args, **kwargs) as stream:
            read = stream.read

            def tijdens_lezen(*args):
                current = read(*args)
                stream.read = read
                with pytest.raises(PermissionError, match="datamapslot"):
                    storage.record_pool(meter, None)
                return current

            stream.read = tijdens_lezen
            yield stream

    with closing(meter.db):
        try:
            with monkeypatch.context() as fault:
                fault.setattr(meter.db, "startup_lock", weiger_slot)
                fault.setattr(storage, "_request_pipe", onderbroken_controle)
                assert storage.pool_cleanup_pause_reason(ctx)
        finally:
            await storage.shutdown(meter)
    await _expect_pause(ctx, cleanup_round)
    await _resume_with_healthy_pool(ctx, cleanup_round)


async def test_lezende_werker_zonder_zoekrecht_trekt_meting_in(ctx, cleanup_round, pool_configured):
    _healthy_pool(ctx)
    worker = create_app(ctx.settings).state.ctx
    folder = ctx.db.path.parent
    mode = stat.S_IMODE(folder.stat().st_mode)
    with closing(worker.db):
        assert storage.pool_cleanup_pause_reason(worker) is None
        try:
            folder.chmod(0)
            with pytest.raises(PermissionError):
                await storage.refresh_pool(worker)
        finally:
            folder.chmod(mode)
            await storage.shutdown(worker)
    await _expect_pause(ctx, cleanup_round)
    await _resume_with_healthy_pool(ctx, cleanup_round)


async def test_kanaal_onleesbaar_bij_start_behoudt_kern(ctx, cleanup_round, pool_configured,
                                                       monkeypatch):
    _healthy_pool(ctx)
    worker = create_app(ctx.settings).state.ctx
    with closing(worker.db):
        def weiger_kanaal(*args, **kwargs):
            raise PermissionError("Geen leesrecht op het meetkanaal")

        with monkeypatch.context() as fault:
            fault.setattr(storage, "_request_pipe", weiger_kanaal)
            await storage.startup(worker)
            assert worker.state_get(storage.POOL_REQUEST) is None
        await _expect_pause(ctx, cleanup_round)
        await _resume_with_healthy_pool(worker, cleanup_round)
        await storage.shutdown(worker)


def _meter_met_kanaalfout(folder, signal, abrupt, dubbel):
    worker = create_app(load_settings(data_dir=folder, start_background=False)).state.ctx
    requests = []
    worker.services["truenas_transport"] = httpx.MockTransport(requests.append)
    fifo = worker.db.path.with_suffix(".pool-fifo")
    lock = worker.db.path.with_suffix(".pool-workers")
    with closing(worker.db), signal:
        mode = stat.S_IMODE(fifo.stat().st_mode)
        try:
            fifo.chmod(0)
            if dubbel:
                lock.chmod(0)
            asyncio.run(storage.startup(worker))
        finally:
            fifo.chmod(mode)
            if dubbel:
                lock.chmod(0o600)
        signal.send("gestart")
        assert signal.recv() == "meet"
        mode = stat.S_IMODE(folder.stat().st_mode)
        try:
            folder.chmod(0)
            with pytest.raises(PermissionError):
                asyncio.run(storage.refresh_pool(worker))
        finally:
            folder.chmod(mode)
        assert not requests
        if abrupt:
            os._exit(0)
        asyncio.run(storage.shutdown(worker))


@pytest.mark.parametrize("abrupt", [False, True], ids=["afsluiten", "procesuitval"])
@pytest.mark.parametrize("dubbel", [False, True], ids=["kanaalfout", "beide-onleesbaar"])
async def test_slotfout_na_kanaalfout_bij_start(
        ctx, cleanup_round, pool_configured, abrupt, dubbel):
    _pool_response(ctx, True)
    assert (await storage.refresh_pool(ctx))["reachable"]
    folder = ctx.settings.data_dir
    mode = stat.S_IMODE(folder.stat().st_mode)
    fifo = ctx.db.path.with_suffix(".pool-fifo")
    fifo_mode = stat.S_IMODE(fifo.stat().st_mode)
    processes = multiprocessing.get_context("spawn")
    parent, child = processes.Pipe()
    worker = processes.Process(target=_meter_met_kanaalfout, args=(folder, child, abrupt, dubbel))
    worker.start()
    child.close()
    try:
        with parent:
            assert parent.poll(20) and parent.recv() == "gestart"
            assert storage.pool_cleanup_pause_reason(ctx)
            assert (await storage.refresh_pool(ctx))["reachable"]
            while_active = storage.pool_cleanup_pause_reason(ctx)
            parent.send("meet")
            worker.join(20)
            assert worker.exitcode == 0
        await _expect_pause(ctx, cleanup_round)
        assert while_active
        _, source, trash, _ = cleanup_round
        for folder_with_video in (source, trash):
            assert (folder_with_video / "video.mkv").read_bytes() == b"x" * 10
        await _resume_with_healthy_pool(ctx, cleanup_round)
    finally:
        if worker.is_alive():
            worker.kill()
            worker.join()
        folder.chmod(mode)
        fifo.chmod(fifo_mode)
        await storage.shutdown(ctx)


async def test_werkerslot_onleesbaar_trekt_toestemming_in(ctx, cleanup_round, pool_configured):
    _healthy_pool(ctx)
    lock = ctx.db.path.with_suffix(".pool-workers")
    mode = stat.S_IMODE(lock.stat().st_mode)
    try:
        lock.chmod(0)
        assert storage.pool_cleanup_pause_reason(ctx)
    finally:
        lock.chmod(mode)
    await _expect_pause(ctx, cleanup_round)
    await _resume_with_healthy_pool(ctx, cleanup_round)


@pytest.mark.parametrize("ingesteld", [False, True], ids=["los", "met-pool"])
@pytest.mark.parametrize("herverbinden", [False, True], ids=["herstart", "kanaal-herstellen"])
async def test_kern_bereikbaar_bij_onleesbaar_kanaal_en_werkerslot(
        ctx, client, cleanup_round, ingesteld, herverbinden, request):
    if ingesteld:
        request.getfixturevalue("pool_configured")
        _healthy_pool(ctx)
    assert client.get("/readyz").status_code == 200
    job_id = jobs.enqueue(ctx.db, "library.sync")
    jobs.JobContext(job_id, "library.sync", {}, None, None, ctx).progress(0.75)
    fifo = ctx.db.path.with_suffix(".pool-fifo")
    lock = ctx.db.path.with_suffix(".pool-workers")
    lock.touch(mode=0o600, exist_ok=True)
    app = create_app(ctx.settings)
    worker = app.state.ctx
    _pool_response(worker, True)
    with closing(worker.db):
        try:
            fifo.chmod(0)
            lock.chmod(0)
            with TestClient(app) as restarted:
                restarted.cookies.update(client.cookies)
                assert restarted.get("/readyz").status_code == 200
                detail = restarted.get(f"/api/v1/jobs/{job_id}")
                assert detail.status_code == 200 and detail.json()["progress"] == 0.75
                forecast = restarted.get("/api/v1/storage/forecast")
                assert forecast.status_code == 200
                if ingesteld:
                    assert forecast.json()["truenas"]["ok"] is False
                else:
                    assert forecast.json()["truenas"] is None
                worker.state_set("schrijfproef", job_id)
                assert ctx.state_get("schrijfproef") == job_id
                await _expect_pause(ctx, cleanup_round)
                fifo.chmod(0o600)
                lock.chmod(0o600)
                if ingesteld:
                    _healthy_pool(ctx)
                # De andere werker kan meten, maar deze werker heeft nog geen kanaal.
                await _expect_pause(ctx, cleanup_round)
                for folder in cleanup_round[1:3]:
                    assert (folder / "video.mkv").read_bytes() == b"x" * 10
                if herverbinden:
                    await storage.refresh_pool(worker)
                    assert storage.pool_cleanup_pause_reason(ctx) is None
        finally:
            fifo.chmod(0o600)
            lock.chmod(0o600)
    if ingesteld and not herverbinden:
        await _expect_pause(ctx, cleanup_round)
        _healthy_pool(ctx)
    result = await J.run_once(ctx, gatherer=cleanup_round[0])
    assert (result["status"], result["trashed"], result["purged"]) == ("ok", 1, 1)
    with closing(worker.db), TestClient(app) as recovered:
        assert recovered.get("/readyz").status_code == 200


def _meter_zonder_terugvalslot(settings, signal, foutnummer, vanaf_inschrijving):
    paths = {settings.db_path.with_suffix(ext) for ext in (".pool-fifo", ".pool-workers")}
    open_file, lock = os.open, fcntl.fcntl
    errors = []
    requests = []

    def open_without_channel(path, *args, **kwargs):
        if Path(path) in paths:
            raise PermissionError(errno.EACCES, "Meetbestand onleesbaar", path)
        return open_file(path, *args, **kwargs)

    def refuse_lock(fd, command, *args):
        if command == fcntl.F_OFD_SETLK:
            errors.append(foutnummer)
            raise OSError(foutnummer, "Terugvalslot geweigerd")
        return lock(fd, command, *args)

    app = None if vanaf_inschrijving else create_app(settings)
    with (signal, patch.object(os, "open", open_without_channel),
          patch.object(fcntl, "fcntl", refuse_lock)):
        app = app or create_app(settings)
        worker = app.state.ctx
        worker.services["truenas_transport"] = httpx.MockTransport(requests.append)
        with closing(worker.db), TestClient(app) as client:
            _werk_zonder_terugvalslot(worker, client, signal, errors, requests)


def _werk_zonder_terugvalslot(worker, client, signal, errors, requests):
    with pytest.raises(OSError):
        asyncio.run(storage.refresh_pool(worker))
    worker.state_set("slotproef", "schrijfbaar")
    signal.send((client.get("/readyz").status_code, errors[0]))
    assert signal.poll(30) and signal.recv() == "meet"
    with (patch.object(worker.db, "startup_lock", side_effect=PermissionError("datamapslot")),
          pytest.raises(OSError)):
        asyncio.run(storage.refresh_pool(worker))
    signal.send((client.get("/readyz").status_code, len(requests)))
    assert signal.poll(30)
    if signal.recv() == "procesuitval":
        os._exit(0)


def _weiger_eerste_inschrijving(ctx):
    ctx.db.execute("CREATE TRIGGER weiger_werker BEFORE INSERT ON system_state "
                   "WHEN NEW.key GLOB 'pool_worker_v1:*' "
                   "BEGIN SELECT RAISE(FAIL, 'Inschrijving geweigerd'); END")


@pytest.mark.parametrize(("foutnummer", "inschrijving"), [
    (errno.ENOLCK, "vooraf"), (errno.EACCES, "vooraf"), (errno.EIO, "vooraf"),
    (errno.ETIMEDOUT, "vooraf"), (errno.ENOENT, "vooraf"),
    (errno.ENOLCK, "bij-start"), (errno.ENOLCK, "sqlite-geweigerd"),
], ids=["ENOLCK", "EACCES", "EIO", "ETIMEDOUT", "ENOENT", "ENOLCK-bij-inschrijving",
        "ENOLCK-en-SQLite-geweigerd"])
async def test_onbekende_werker_zonder_terugvalslot(ctx, settings, cleanup_round, pool_configured,
                                                    foutnummer, inschrijving):
    gather, source, trash, sab = cleanup_round
    inhoud = b"enige archiefkopie"
    (trash / "video.mkv").write_bytes(inhoud)
    _pool_response(ctx, True)
    assert (await storage.refresh_pool(ctx))["reachable"]
    if inschrijving == "sqlite-geweigerd":
        _weiger_eerste_inschrijving(ctx)
    processes = multiprocessing.get_context("spawn")
    parent, child = processes.Pipe()
    worker = processes.Process(target=_meter_zonder_terugvalslot,
                               args=(settings, child, foutnummer, inschrijving != "vooraf"))
    worker.start()
    child.close()
    try:
        with parent:
            assert parent.poll(25) and parent.recv() == (200, foutnummer)
            assert ctx.state_get("slotproef") == "schrijfbaar"
            assert (await storage.refresh_pool(ctx))["reachable"]
            parent.send("meet")
            assert parent.poll(25) and parent.recv() == (200, 0)
            result = await J.run_once(ctx, gatherer=gather)
            assert worker.is_alive()
            assert (result["status"], result["trashed"], result["purged"], sab.deleted) == (
                "skipped", 0, 0, [])
            assert (source / "video.mkv").read_bytes() == b"x" * 10
            assert [p for p in source.parent.rglob("*.mkv") if p.read_bytes() == inhoud] == [
                trash / "video.mkv"]
            parent.send("procesuitval" if foutnummer == errno.ENOLCK else "afsluiten")
            worker.join(20)
            assert worker.exitcode == 0
        if inschrijving == "sqlite-geweigerd":
            ctx.db.execute("DROP TRIGGER weiger_werker")
        await _expect_pause(ctx, cleanup_round)
        await _resume_with_healthy_pool(ctx, cleanup_round)
    finally:
        if worker.is_alive():
            worker.kill()
            worker.join()
        worker.close()
        await storage.shutdown(ctx)


@pytest.mark.parametrize("fout", ["EIO", "ETIMEDOUT", "volle-fifo", "nul-bytes"])
async def test_meldfout_zonder_sloten_of_sqlite(ctx, cleanup_round, pool_configured,
                                                monkeypatch, fout):
    worker = create_app(ctx.settings).state.ctx
    with closing(worker.db):
        await storage.startup(worker)
        try:
            _healthy_pool(ctx)

            def write(_data):
                if fout == "volle-fifo":
                    return None
                if fout == "nul-bytes":
                    return 0
                raise OSError(getattr(errno, fout), "Schrijven geweigerd")

            def denied(*args, **kwargs):
                raise PermissionError("Slot geweigerd")

            with monkeypatch.context() as fault:
                fault.setattr(worker.services[storage._POOL_PIPE], "write", write)
                fault.setattr(storage, "_worker_lock", denied)
                fault.setattr(storage, "_directory_worker_lock", denied)
                fault.setattr(worker.db, "startup_lock", denied)
                with pytest.raises(OSError):
                    await storage.refresh_pool(worker)
            _healthy_pool(ctx)
            await _expect_pause(ctx, cleanup_round)
            # Het eerste succesvolle contact is nog geen meting.
            await storage.startup(worker)
            await _expect_pause(ctx, cleanup_round)
            await _resume_with_healthy_pool(ctx, cleanup_round)
        finally:
            await storage.shutdown(worker)


@pytest.mark.parametrize("hermaakt", [False, True], ids=["verdwenen", "vervangen"])
async def test_meetkanaal_verandert_met_twee_werkers(ctx, cleanup_round, pool_configured, hermaakt):
    worker = create_app(ctx.settings).state.ctx
    with closing(worker.db):
        await storage.startup(worker)
        try:
            _healthy_pool(ctx)
            fifo = ctx.db.path.with_suffix(".pool-fifo")
            fifo.unlink()
            if hermaakt:
                os.mkfifo(fifo, 0o600)
            await _expect_pause(ctx, cleanup_round)
            assert _healthy_pool(ctx)["reachable"]
            await _expect_pause(ctx, cleanup_round)
            await storage.startup(worker)
            await _expect_pause(ctx, cleanup_round)
            await _resume_with_healthy_pool(ctx, cleanup_round)
        finally:
            await storage.shutdown(worker)


async def test_verdwenen_werkerslot_trekt_meting_in(ctx, cleanup_round, pool_configured):
    _healthy_pool(ctx)
    lock = ctx.db.path.with_suffix(".pool-workers")
    lock.unlink()
    await _expect_pause(ctx, cleanup_round)
    lock.touch(mode=0o600, exist_ok=True)
    await _expect_pause(ctx, cleanup_round)
    await _resume_with_healthy_pool(ctx, cleanup_round)


@pytest.mark.parametrize("herstel", ["herverbinden", "afsluiten"])
async def test_eerste_sqlite_inschrijving_geweigerd(ctx, cleanup_round, pool_configured, herstel):
    _healthy_pool(ctx)
    _weiger_eerste_inschrijving(ctx)
    app = create_app(ctx.settings)
    worker = app.state.ctx
    with closing(worker.db):
        with TestClient(app) as client:
            assert client.get("/readyz").status_code == 200
            worker.state_set("schrijfproef", "bereikbaar")
            assert ctx.state_get("schrijfproef") == "bereikbaar"
            assert _healthy_pool(ctx)["reachable"]
            await _expect_pause(ctx, cleanup_round)
            for folder in cleanup_round[1:3]:
                assert (folder / "video.mkv").read_bytes() == b"x" * 10
            ctx.db.execute("DROP TRIGGER weiger_werker")
            if herstel == "herverbinden":
                await storage.startup(worker)
                await _expect_pause(ctx, cleanup_round)
                await _resume_with_healthy_pool(ctx, cleanup_round)
        if herstel == "afsluiten":
            await _expect_pause(ctx, cleanup_round)
            await _resume_with_healthy_pool(ctx, cleanup_round)


async def test_aanmelding_zonder_schrijfbare_datamap(ctx, cleanup_round, pool_configured):
    _healthy_pool(ctx)
    _weiger_eerste_inschrijving(ctx)
    folder = ctx.db.path.parent
    mode = stat.S_IMODE(folder.stat().st_mode)
    try:
        folder.chmod(mode & ~0o222)
        app = create_app(ctx.settings)
        with closing(app.state.ctx.db), TestClient(app) as client:
            assert client.get("/readyz").status_code == 200
            assert _healthy_pool(ctx)["reachable"]
            await _expect_pause(ctx, cleanup_round)
    finally:
        folder.chmod(mode)
        ctx.db.execute("DROP TRIGGER weiger_werker")
    await _expect_pause(ctx, cleanup_round)
    await _resume_with_healthy_pool(ctx, cleanup_round)


@pytest.mark.parametrize("writable", [True, False], ids=["schrijfbaar", "alleen-lezen"])
async def test_vervangen_aanmeldbestand_vergeet_geen_werker(
        ctx, cleanup_round, pool_configured, writable):
    _healthy_pool(ctx)
    _weiger_eerste_inschrijving(ctx)
    folder = ctx.db.path.parent
    ledger = ctx.db.path.with_suffix(".pool-aanmeldingen")
    original = ledger.with_suffix(".bewaar")
    mode = stat.S_IMODE(folder.stat().st_mode)
    file_mode = stat.S_IMODE(ledger.stat().st_mode)
    try:
        folder.chmod(mode & ~0o222)
        if not writable:
            ledger.chmod(file_mode & ~0o222)
        app = create_app(ctx.settings)
        with closing(app.state.ctx.db):
            folder.chmod(mode)
            ledger.rename(original)
            ledger.touch()
            assert _healthy_pool(ctx)["reachable"]
            await _expect_pause(ctx, cleanup_round)
            original.replace(ledger)
            ledger.chmod(file_mode)
            assert _healthy_pool(ctx)["reachable"]
            await _expect_pause(ctx, cleanup_round)
            with TestClient(app) as client:
                assert client.get("/readyz").status_code == 200
    finally:
        folder.chmod(mode)
        ctx.db.execute("DROP TRIGGER weiger_werker")
    await _expect_pause(ctx, cleanup_round)
    await _resume_with_healthy_pool(ctx, cleanup_round)


def _qbit_refusal(source, root, reply):
    torrent = {"hash": "aflevering", "name": source.name, "category": "movies",
               "content_path": str(source), "state": "stoppedUP", "progress": 1,
               "completion_on": int((datetime.now(UTC) - timedelta(days=30)).timestamp())}
    registered = [torrent]
    refusals = []

    def answer(request):
        if request.method == "POST":
            assert request.url.path == "/api/v2/torrents/delete"
            assert parse_qs(request.content.decode()) == {
                "hashes": ["aflevering"], "deleteFiles": ["false"]}
            refusals.append(reply)
            return httpx.Response(reply)
        responses = {"/api/v2/app/preferences": {},
                     "/api/v2/torrents/categories": {"movies": {"savePath": str(root)}},
                     "/api/v2/torrents/info": registered}
        return httpx.Response(200, json=responses[request.url.path])

    return httpx.MockTransport(answer), refusals


@pytest.mark.parametrize("reply", [503, 200], ids=["HTTP-503", "nog-geregistreerd"])
async def test_qbit_weigert_afmelden(ctx, layout, cleanup_round, pool_configured,
                                     monkeypatch, reply):
    source = layout["torrents"] / "aflevering.mkv"
    content = b"enige lokale kopie van de proefdownload"
    source.write_bytes(content)
    transport, refusals = _qbit_refusal(source, source.parent, reply)
    ctx.services["download_client_transports"] = {"qbit": transport}
    cfg = DownloadClientCfg(name="qbit", implementation="QBittorrent", host="qbit.invalid", port=80)
    _pool_response(ctx, True)
    assert (await storage.refresh_pool(ctx))["reachable"]
    usage = type(J.shutil.disk_usage(source.parent))
    monkeypatch.setattr(J.shutil, "disk_usage", lambda _: usage(100, 99, 1))
    async with QbitClient(ctx, cfg, None) as client:
        async def gather(_):
            view = await client.view({"movies"}, 24)
            return _snap(layout, view.items, imported_ids={"AFLEVERING"}), {"qbit": client}

        result = await J.run_once(ctx, gatherer=gather)
        assert [item.id for item in (await client.view({"movies"}, 24)).items] == ["AFLEVERING"]
    assert refusals == [reply] and result["errors"]
    if reply == 503:
        assert "503" in result["errors"][0]
    assert (result["trashed"], result["purged"]) == (0, 0)
    assert source.read_bytes() == content
    assert (cleanup_round[2] / "video.mkv").read_bytes() == b"x" * 10


@pytest.mark.parametrize("submap", [False, True], ids=["rechtstreeks", "submap"])
async def test_reservering_beschermt_download_bij_ontbrekende_client(
        ctx, layout, pool_configured, monkeypatch, submap):
    root = layout["torrents"]
    folder = root / "seizoen" if submap else root
    folder.mkdir(exist_ok=True)
    source = folder / "aflevering.mkv"
    content = b"enige kopie van de download"
    source.write_bytes(content)
    owner, refusals = _qbit_refusal(source, root, 503)

    def sab_response(request):
        assert request.method == "GET" and "name" not in request.url.params
        responses = {"get_config": {"config": {"misc": {"complete_dir": str(root.parent)},
                                               "categories": [{"name": "movies",
                                                               "dir": root.name}]}},
                     "queue": {"queue": {"slots": []}}, "history": {"history": {"slots": []}}}
        return httpx.Response(200, json=responses[request.url.params["mode"]])

    qbit = dict(name="qbit", implementation="QBittorrent", host="qbit.invalid", port=80)
    sab = dict(name="sab", implementation="Sabnzbd", host="sab.invalid", port=80)
    transports = {"qbit": owner, "sab": httpx.MockTransport(sab_response)}
    usage = type(J.shutil.disk_usage(root))
    monkeypatch.setattr(J.shutil, "disk_usage", lambda _: usage(100, 99, 1))

    async def healthy_round(worker):
        worker.services["download_client_transports"] = transports
        worker.services["download_janitor_search_bases"] = [root]
        _pool_response(worker, True)
        assert (await storage.refresh_pool(worker))["reachable"]
        return await J.run_once(worker)

    ctx.state_set("download_recovery", {"items": {"AFLEVERING": {"status": "not_recoverable"}}})
    store.save(ctx, {"download_clients": [qbit, sab]}, "test")
    try:
        first = await healthy_round(ctx)
        assert first["errors"] and refusals == [503]
        assert (first["trashed"], first["purged"]) == (0, 0) and source.read_bytes() == content
    finally:
        await storage.shutdown(ctx)
    store.save(ctx, {"download_clients": [{**qbit, "enabled": False}, sab]}, "test")

    class Later(datetime):
        @classmethod
        def now(cls, tz=None):
            return datetime.now(tz) + timedelta(days=4)

    worker = create_app(ctx.settings).state.ctx
    with closing(worker.db):
        try:
            for clock in (datetime, Later):
                monkeypatch.setattr(J, "datetime", clock)
                result = await healthy_round(worker)
                assert (result["trashed"], result["purged"], result["errors"]) == (0, 0, [])
                assert source.read_bytes() == content
            async with QbitClient(worker, DownloadClientCfg(**qbit), None) as client:
                assert [i.id for i in (await client.view({"movies"}, 24)).items] == ["AFLEVERING"]
            assert refusals == [503]
            assert sum(p.read_bytes() == content for p in root.rglob("*") if p.is_file()) == 1
        finally:
            await storage.shutdown(worker)


@pytest.mark.parametrize("reservering", ["ouder", "kind"])
def test_overlap_opnieuw_gecontroleerd_onder_het_slot(ctx, layout, reservering):
    root = layout["tv"]
    folder = _mk(root / "seizoen")
    child = folder / "video.mkv"
    protected, candidate = (folder, child) if reservering == "ouder" else (child, folder)
    # Een verweesde download is al geboekt wanneer de client zich meldt.
    orphan = J._reserve_trash(ctx, candidate, root, "onbekend", None, NOW, None)
    item = ClientItem("qbit", "torrent", "TORRENT", protected.name, "tv", str(protected), False)
    J._reserve_trash(ctx, protected, root, "afmelden", item, NOW, None)
    # De tweede client kent een andere download op het overlappende pad.
    other = ClientItem("sab", "nzb", "NZB", candidate.name, "tv", str(candidate), False)
    plan = J.plan(ctx, _snap(layout, [other], imported_ids={other.id}), NOW)
    assert not plan.actions
    with pytest.raises(ValueError, match="afmelding"):
        J._reserve_trash(ctx, candidate, root, "afmelden", other, NOW, None)
    with pytest.raises(ValueError, match="afmelding"):
        J._move_reserved(ctx, orphan, "test", NOW, None)
    assert J.purge(ctx, NOW + timedelta(days=30))["purged"] == 0
    assert child.read_bytes() == b"x" * 10
    sibling = _mk(root / "seizoen-extra")
    assert J._trash(ctx, sibling, root, "onbekend", actor="test", now=NOW)["id"]


def test_eerder_verplaatste_oudermap_blijft_terugzetbaar(ctx, layout):
    root = layout["tv"]
    folder = _mk(root / "seizoen")
    source = folder / "video.mkv"
    item = ClientItem("qbit", "torrent", "TORRENT", source.name, "tv", str(source), False)
    J._reserve_trash(ctx, source, root, "afmelden", item, NOW, None)
    # Een oudere opruimer kon de oudermap vóór de upgrade al als wees verplaatsen.
    dest = root / J.TRASH_DIR / "oud" / folder.name
    dest.parent.mkdir()
    folder.rename(dest)
    tid = ctx.db.execute_insert(
        "INSERT INTO download_janitor_trash(root,original_path,trash_path,reason,moved_at) "
        "VALUES(?,?,?,?,?)", (str(root), str(folder), str(dest), "onbekend", OLD.isoformat()))
    restarted = create_app(ctx.settings).state.ctx
    with closing(restarted.db):
        assert J.purge(restarted, NOW)["purged"] == 0
        assert (dest / source.name).read_bytes() == b"x" * 10
        assert J.restore(restarted, tid, actor="test")["restored"]
        assert source.read_bytes() == b"x" * 10


@pytest.mark.parametrize("refusal", ["geweigerd", "ingeschreven"])
async def test_sab_afmelding_na_herstart(ctx, layout, refusal):
    source = layout["tv"] / "aflevering.mkv"
    content = b"volledige download"
    source.write_bytes(content)
    raw_id = "SABnzbd_nzo_Aflevering"
    history = [{"nzo_id": raw_id, "name": source.name, "category": "tv", "storage": str(source),
                "status": "Completed"}]
    accept = False

    def answer(request):
        params = request.url.params
        if params.get("name") == "delete":
            assert params["value"] == raw_id and params["del_files"] == "0"
            assert source.read_bytes() == content
            if accept:
                history.clear()
            return httpx.Response(200, json={"status": accept or refusal == "ingeschreven"})
        if "nzo_ids" in params:
            assert params["nzo_ids"] == raw_id
        return httpx.Response(200, json={params["mode"]: {"slots": history}
                                         if params["mode"] == "history" else {}})

    cfg = DownloadClientCfg(name="sab", implementation="Sabnzbd", host="sab.invalid", port=80)

    async def gather(worker):
        worker.services["download_client_transports"] = {"sab": httpx.MockTransport(answer)}
        client = SabClient(worker, cfg, None)
        view = await client.view({"tv"})
        return _snap(layout, view.items, imported_ids={raw_id.upper()}), {"sab": client}

    result = await J.run_once(ctx, gatherer=gather)
    assert result["errors"] and (result["trashed"], result["purged"]) == (0, 0)
    assert history and source.read_bytes() == content
    assert not J.cleanup_status(None, ctx)["trash"]
    worker = create_app(ctx.settings).state.ctx
    with closing(worker.db):
        assert J.purge(worker, datetime.now(UTC) + timedelta(days=30))["purged"] == 0
        accept = True
        result = await J.run_once(worker, gatherer=gather)
        assert not history and result["trashed"] == 1 and not result["errors"]
        row, = worker.db.fetch_all("SELECT * FROM download_janitor_trash")
        assert Path(row["trash_path"]).read_bytes() == content
        assert J.restore(worker, row["id"], actor="test")["restored"]
        assert source.read_bytes() == content


async def test_onderbroken_vrijgave_behoudt_prullenbak(ctx, layout):
    item = _nzb(layout, "vrijgave")
    source = Path(item.path)
    plan = J.plan(ctx, _snap(layout, [item], imported_ids={item.id}), NOW)
    # De vrijgave gebeurt vanuit een andere thread, op een eigen SQLite-verbinding.
    ctx.db.execute("CREATE TRIGGER onderbreek BEFORE UPDATE OF moved_at ON download_janitor_trash "
                   "BEGIN SELECT RAISE(ABORT, 'vrijgave'); END")
    sab = FakeSab()
    result = await J.execute(ctx, plan, {"sab": sab}, actor="test")
    assert not result["done"] and "vrijgave" in result["errors"][0] and sab.deleted == [item.id]
    worker = create_app(ctx.settings).state.ctx
    with closing(worker.db):
        assert J.purge(worker, datetime.now(UTC) + timedelta(days=30))["purged"] == 0
        entry, = J.cleanup_status(None, worker)["trash"]
        assert entry["manual_only"]
        worker.db.execute("CREATE TEMP TRIGGER weiger_audit BEFORE INSERT ON audit_events "
                          "WHEN NEW.action='downloads.restored' "
                          "BEGIN SELECT RAISE(ABORT, 'herstelboeking'); END")
        with pytest.raises(sqlite3.IntegrityError, match="herstelboeking"):
            J.restore(worker, entry["id"], actor="test")
    restarted = create_app(ctx.settings).state.ctx
    with closing(restarted.db):
        retry, = J.cleanup_status(None, restarted)["trash"]
        assert retry["id"] == entry["id"]
        assert J.restore(restarted, retry["id"], actor="test")["restored"]
    assert (source / "video.mkv").read_bytes() == b"x" * 10


async def test_onbevestigde_afmelding_blijft_bij_de_client(ctx, layout):
    item = _nzb(layout, "in-gebruik")
    source = Path(item.path)
    _age(source, 30)
    plan = J.plan(ctx, _snap(layout, [item], imported_ids={item.id}), NOW)
    assert (await J.execute(ctx, plan, {}, actor="test"))["errors"]
    # Een volgende momentopname mist deze client. Ook ná de termijn voor een
    # onbekende download blijft de eerdere reservering aan zijn afmelding gebonden.
    worker = create_app(ctx.settings).state.ctx
    with closing(worker.db):
        snapshot = _snap(layout, [])
        J.plan(worker, snapshot, NOW)
        later = NOW + timedelta(days=30)
        orphan = J.plan(worker, snapshot, later)
        assert not orphan.actions
        # Ook een vóór de clientboeking vastgesteld weesplan moet veilig blijven.
        stale = J.Plan(actions=[J.Action("trash", "onbekend", source, layout["tv"])])
        result = await J.execute(worker, stale, {}, actor="test")
        assert not result["done"] and J.purge(worker, later)["purged"] == 0
        assert (source / "video.mkv").read_bytes() == b"x" * 10
        sab = FakeSab()
        retry = await J.execute(worker, plan, {"sab": sab}, actor="test")
        assert sab.deleted == [item.id] and len(retry["done"]) == 1


def test_historische_clientboeking_blijft_terugzetbaar(settings, layout, tmp_path, monkeypatch):
    from mediaguard import db

    migrations = tmp_path / "oude_migraties"
    migrations.mkdir()
    for file in db.MIGRATIONS_DIR.glob("*.sql"):
        if file.name != "078_prullenbakreservering.sql":
            (migrations / file.name).symlink_to(file)
    root = layout["tv"]
    source = root / "historisch"
    trash = _mk(root / J.TRASH_DIR / "oud" / source.name)
    with monkeypatch.context() as old:
        old.setattr(db, "MIGRATIONS_DIR", migrations)
        previous = create_app(settings).state.ctx
        with closing(previous.db):
            tid = previous.db.execute_insert(
                "INSERT INTO download_janitor_trash(root,original_path,trash_path,reason,client,"
                "download_id,moved_at) VALUES(?,?,?,?,?,?,?)",
                (str(root), str(source), str(trash), "oud", "sab", "HISTORISCH", OLD.isoformat()))
    worker = create_app(settings).state.ctx
    with closing(worker.db):
        assert J.purge(worker, NOW)["purged"] == 0
        assert (trash / "video.mkv").read_bytes() == b"x" * 10
        assert J.restore(worker, tid, actor="test")["restored"]
        assert (source / "video.mkv").read_bytes() == b"x" * 10


def test_bewaartermijn_na_onderbroken_boeking(ctx, layout, pool_configured, monkeypatch):
    root = layout["tv"]
    source = root / "aflevering.mkv"
    source.write_bytes(b"eerste")
    _healthy_pool(ctx)
    first = J._trash(ctx, source, root, "test", actor="test", now=NOW)
    usage = type(J.shutil.disk_usage(root))
    monkeypatch.setattr(J.shutil, "disk_usage", lambda _: usage(100, 99, 1))
    ctx.db.execute("CREATE TEMP TRIGGER weiger_einde BEFORE UPDATE OF purged_at ON "
                   "download_janitor_trash BEGIN SELECT RAISE(ABORT, 'eindboeking'); END")
    with pytest.raises(sqlite3.IntegrityError, match="eindboeking"):
        J.purge(ctx, NOW + timedelta(hours=1))
    ctx.db.close()
    worker = create_app(ctx.settings).state.ctx
    with closing(worker.db):
        _healthy_pool(worker)
        source.write_bytes(b"tweede")
        second = J._trash(worker, source, root, "test", actor="test", now=NOW + timedelta(hours=4))
        first_path = J._trash_entry(worker, first["id"])["trash_path"]
        second_path = J._trash_entry(worker, second["id"])["trash_path"]
        monkeypatch.setattr(J.shutil, "disk_usage", lambda _: usage(100, 50, 50))
        # Eén uur na de eerste vervaltijd heeft de tweede download nog drie uur.
        J.purge(worker, NOW + timedelta(days=7, hours=1))
        assert second_path != first_path and Path(second_path).read_bytes() == b"tweede"
        assert J.restore(worker, second["id"], actor="test")["restored"]
        assert source.read_bytes() == b"tweede"


@pytest.mark.parametrize("historisch", [False, True])
def test_dubbele_prullenbakverwijzing_behoudt_bestand(ctx, layout, historisch):
    root = layout["tv"]
    source = root / "dubbel.mkv"
    source.write_bytes(b"bewaren")
    item = J._trash(ctx, source, root, "test", actor="test", now=NOW)
    path = Path(J._trash_entry(ctx, item["id"])["trash_path"])
    ctx.db.execute("INSERT INTO download_janitor_trash(root,original_path,trash_path,reason,"
                   "moved_at,purged_at) VALUES(?,?,?,?,?,?)",
                   (str(root), str(root / "andere.mkv"), str(path), "oud", OLD.isoformat(),
                    NOW.isoformat() if historisch else None))
    assert J.purge(ctx, NOW + timedelta(days=8))["purged"] == 0
    with pytest.raises(ValueError, match="registratie"):
        J.restore(ctx, item["id"], actor="test")
    assert path.read_bytes() == b"bewaren"


@pytest.mark.parametrize("client_type", ["sab", "qbit"])
async def test_geweigerde_reservering_behoudt_download_en_client(ctx, layout, client_type):
    item = _nzb(layout, "geboekt")
    if client_type == "qbit":
        path = _mk(layout["torrents"] / "delen")
        item = ClientItem("qbit", "torrent", "DELING", "delen", "movies", str(path), active=False)
    source = Path(item.path)
    plan = J.plan(ctx, _snap(layout, [item], imported_ids={item.id}), NOW)
    sab, qbit = FakeSab(), FakeQbit()
    ctx.db.execute("CREATE TRIGGER weiger_reservering BEFORE INSERT ON "
                   "download_janitor_trash BEGIN SELECT RAISE(ABORT, 'reservering'); END")
    result = await J.execute(ctx, plan, {"sab": sab, "qbit": qbit}, actor="test")
    assert result["errors"] and not result["done"]
    assert (source / "video.mkv").is_file() and not sab.deleted and not qbit.removed


def test_geweigerde_herstelboeking_behoudt_prullenbak(ctx, layout):
    root = layout["tv"]
    source = root / "terug.mkv"
    source.write_bytes(b"inhoud")
    item = J._trash(ctx, source, root, "test", actor="test", now=NOW)
    trash = Path(J._trash_entry(ctx, item["id"])["trash_path"])
    ctx.db.execute("CREATE TEMP TRIGGER weiger_herstel BEFORE UPDATE OF restored_at ON "
                   "download_janitor_trash BEGIN SELECT RAISE(ABORT, 'herstelboeking'); END")
    with pytest.raises(sqlite3.IntegrityError, match="herstelboeking"):
        J.restore(ctx, item["id"], actor="test")
    assert not source.exists() and trash.read_bytes() == b"inhoud"
    ctx.db.execute("DROP TRIGGER weiger_herstel")
    assert J.restore(ctx, item["id"], actor="test")["restored"] and source.read_bytes() == b"inhoud"


@pytest.mark.parametrize("onderbreking", ["hernoemen", "audit"])
def test_onderbroken_terugplaatsing_hervat_na_herstart(ctx, layout, monkeypatch, onderbreking):
    source = layout["tv"] / "terug.mkv"
    source.write_bytes(b"dezelfde inhoud")
    tid = J._trash(ctx, source, layout["tv"], "test", actor="test", now=NOW)["id"]
    with monkeypatch.context() as error:
        if onderbreking == "hernoemen":
            def weigeren(*_):
                raise PermissionError("hernoemen")

            error.setattr(os, "rename", weigeren)
        else:
            ctx.db.execute("CREATE TEMP TRIGGER onderbreek_audit BEFORE INSERT ON audit_events "
                           "WHEN NEW.action='downloads.restored' BEGIN SELECT RAISE(ABORT, "
                           "'audit'); END")
        with pytest.raises((PermissionError, sqlite3.IntegrityError), match=onderbreking):
            J.restore(ctx, tid, actor="test")
    ctx.db.close()
    worker = create_app(ctx.settings).state.ctx
    with closing(worker.db):
        assert J.purge(worker, NOW + timedelta(days=8))["purged"] == 0
        assert [r["id"] for r in J.cleanup_status(ctx=worker)["trash"]] == [tid]
        assert J.restore(worker, tid, actor="test")["restored"]
        assert source.read_bytes() == b"dezelfde inhoud"
        assert not J.cleanup_status(ctx=worker)["trash"]


def test_geweigerde_commit_voor_unlink_behoudt_bestand(ctx, layout):
    root = layout["tv"]
    source = root / "blijft.mkv"
    source.write_bytes(b"bewaren")
    tid = J._trash(ctx, source, root, "test", actor="test", now=NOW)["id"]
    trash = Path(J._trash_entry(ctx, tid)["trash_path"])
    # De INSERT lukt. Pas COMMIT ziet de ontbrekende verwijzing.
    ctx.db.execute("CREATE TABLE proef_boeking(id INTEGER REFERENCES download_janitor_trash(id) "
                   "DEFERRABLE INITIALLY DEFERRED)")
    ctx.db.execute("CREATE TRIGGER weiger_commit AFTER INSERT ON download_janitor_purge_state "
                   "BEGIN INSERT INTO proef_boeking VALUES(-1); END")
    with pytest.raises(sqlite3.IntegrityError, match="FOREIGN KEY"):
        J.purge(ctx, NOW + timedelta(days=8))
    assert trash.read_bytes() == b"bewaren" and not J._trash_entry(ctx, tid)["purged_at"]


def test_boekingen_gebruiken_duurzame_commit(ctx, layout):
    # Controleer de feitelijke SQLite-instelling op de verbinding van de schrijver.
    normal = ctx.db.fetch_one("PRAGMA synchronous")[0]
    for table in ("download_janitor_trash", "download_janitor_purge_state"):
        ctx.db.execute(f"CREATE TEMP TRIGGER sync_{table} BEFORE INSERT ON {table} "
                       "WHEN (SELECT synchronous FROM pragma_synchronous)<2 BEGIN "
                       "SELECT RAISE(ABORT, 'geen duurzame boeking'); END")
    root = layout["tv"]
    source = _mk(root / "delen")
    J._trash(ctx, source, root, "test", actor="test", now=NOW)
    assert J.purge(ctx, NOW + timedelta(days=8))["purged"] == 1
    assert ctx.db.fetch_one("PRAGMA synchronous")[0] == normal


def test_meetuitval_tijdens_deelboeking_behoudt_eerste_bestand(ctx, layout, pool_configured,
                                                              monkeypatch):
    _healthy_pool(ctx)
    root = layout["tv"]
    source = _mk(root / "aflevering")
    tid = J._trash(ctx, source, root, "test", actor="test", now=NOW)["id"]
    trash = Path(J._trash_entry(ctx, tid)["trash_path"])
    boeking = ctx.db.durable_transaction

    @contextmanager
    def na_boeking():
        with boeking() as conn:
            yield conn
        if J._trash_entry(ctx, tid)["partial"]:
            storage.record_pool(ctx, {"error_nl": "Poolmeting weggevallen"})

    monkeypatch.setattr(ctx.db, "durable_transaction", na_boeking)
    assert J.purge(ctx, NOW + timedelta(days=8))["purged"] == 0
    assert (trash / "video.mkv").read_bytes() == b"x" * 10
    assert not J._trash_entry(ctx, tid)["partial"]


async def test_verwijderfout_zonder_sleutel(ctx, client, cleanup_round, monkeypatch):
    _, _, trash, _ = cleanup_round
    ctx.config.set("global", "", "downloads.cleanup_enabled", False, "test")
    origineel = os.unlink

    def weiger(name, **kwargs):
        if name == "video.mkv":
            raise PermissionError("Geen schrijfrecht")
        return origineel(name, **kwargs)

    with monkeypatch.context() as patch:
        patch.setattr(os, "unlink", weiger)
        assert (await J.run_once(ctx))["purged"] == 0
    opnieuw = create_app(ctx.settings).state.ctx
    with closing(opnieuw.db):
        assert (await J.run_once(opnieuw))["purged"] == 0
    assert (trash / "video.mkv").is_file()
    data = client.get("/api/v1/downloads/cleanup").json()
    assert data["purged_total"] == 0
    item, = data["trash"]
    assert item["manual_only"] and not item["partial"]
    assert ctx.db.fetch_one("SELECT 1 FROM audit_events WHERE action='downloads.purge_failed'")
    terug = client.post(f"/api/v1/downloads/cleanup/trash/{item['id']}/restore").json()
    assert terug["restored"] and not terug["partial"]


async def test_gedeeltelijke_map_terugzetten(ctx, client, layout, pool_configured, monkeypatch):
    _pool_response(ctx, True)
    await storage.refresh_pool(ctx)
    bron = _mk(layout["tv"] / "meerdelig", size=4)
    (bron / "leeg.mkv").touch()
    item = J._trash(ctx, bron, layout["tv"], "test", actor="test", now=NOW - timedelta(days=8))
    unlink = os.unlink

    def onderbreek(name, **kwargs):
        unlink(name, **kwargs)
        storage.record_pool(ctx, {"error_nl": "Meting onderbroken"})

    with monkeypatch.context() as patch:
        patch.setattr(os, "unlink", onderbreek)
        assert J.purge(ctx, now=NOW)["purged"] == 0
    data = client.get("/api/v1/downloads/cleanup").json()
    assert data["trash"][0]["partial"] is True
    terug = client.post(f"/api/v1/downloads/cleanup/trash/{item['id']}/restore").json()
    assert terug["restored"] and terug["partial"] is True
    bestanden = list(bron.iterdir())
    assert len(bestanden) == 1
    assert data["trash_bytes"] == bestanden[0].stat().st_size


async def test_pool_pauze_binnen_prullenbakmap(ctx, cleanup_round, pool_configured, monkeypatch):
    _, source, trash, _ = cleanup_round
    nested = trash / "delen"
    nested.mkdir()
    files = [trash / "video.mkv", nested / "twee.mkv", nested / "drie.mkv"]
    for path in files[1:]:
        path.write_bytes(b"deel")
    ctx.config.set("global", "", "downloads.cleanup_enabled", False, "test")
    _pool_response(ctx, True)
    await storage.refresh_pool(ctx)
    unlink = os.unlink
    removed = []

    def lose_measurement(path, *args, **kwargs):
        unlink(path, *args, **kwargs)
        removed.append(Path(path).name)
        if len(removed) == 1:
            storage.record_pool(ctx, {"error_nl": "Poolmeting weggevallen"})

    with monkeypatch.context() as patch:
        patch.setattr(os, "unlink", lose_measurement)
        result = await J.run_once(ctx)
    assert (len(removed), result["status"], result["purged"]) == (1, "skipped", 0)
    assert sum(path.is_file() for path in files) == 2
    assert ctx.db.fetch_one("SELECT purged_at FROM download_janitor_trash")[0] is None
    assert not ctx.db.fetch_one("SELECT 1 FROM audit_events WHERE action='downloads.purged'")
    (trash / "verwijzing").symlink_to(source, target_is_directory=True)
    await storage.refresh_pool(ctx)
    assert (await J.run_once(ctx))["purged"] == 1
    assert not trash.exists() and (source / "video.mkv").is_file()


@pytest.mark.asyncio
@pytest.mark.parametrize("verwijzing", [False, True], ids=["bestand", "symlink"])
async def test_own_temp_leftovers(ctx, tmp_path, layout, verwijzing):
    temp = tmp_path / ".mg-replace-7-abc.part"
    temp.write_bytes(b"x")
    old = (NOW - timedelta(days=3)).isoformat(timespec="seconds")
    ctx.db.execute(
        "INSERT INTO replacement_transactions(kind,library_path,final_path,staged_path,"
        "source_snapshot_json,temp_path,state,created_at,updated_at) VALUES "
        "('replace','/l','/l','/s','{}',?,'aborted',?,?)", (str(temp), old, old))
    active = tmp_path / ".mg-replace-8-def.part"
    active.write_bytes(b"x")
    ctx.db.execute(
        "INSERT INTO replacement_transactions(kind,library_path,final_path,staged_path,"
        "source_snapshot_json,temp_path,state,created_at,updated_at) VALUES "
        "('replace','/l','/l','/s','{}',?,'temp_ready',?,?)", (str(active), old, old))
    item = _nzb(layout, "open-reservering")
    protected = Path(item.path) / ".mg-replace-9.part"
    if verwijzing:
        target = tmp_path / "bron"
        target.write_bytes(b"behouden")
        protected.symlink_to(target)
    else:
        protected.write_bytes(b"behouden")
    J._reserve_trash(ctx, Path(item.path), layout["tv"], "afmelden", item, NOW, None)
    ctx.db.execute(
        "INSERT INTO replacement_transactions(kind,library_path,final_path,staged_path,"
        "source_snapshot_json,temp_path,state,created_at,updated_at) VALUES "
        "('replace','/l','/l','/s','{}',?,'aborted',?,?)", (str(protected), old, old))
    assert J.own_leftovers(ctx, NOW) == 1
    assert not temp.exists() and active.exists()
    assert protected.read_bytes() == b"behouden"


def test_seeding_rules():
    prefs = {"max_ratio_enabled": True, "max_ratio": 2.0}
    base = {"progress": 1, "state": "uploading", "ratio_limit": -2, "seeding_time_limit": -2}
    assert seeding_status({**base, "ratio": 1.0}, prefs, 24) == (False,
                                                                 "deellimiet nog niet gehaald")
    assert seeding_status({**base, "ratio": 2.1}, prefs, 24)[0]
    assert seeding_status({**base, "state": "stoppedUP"}, {}, 24)[0]
    # privé zonder limiet: nooit; openbaar: na de ingestelde uren
    assert not seeding_status({**base, "private": True, "seeding_time": 10 ** 7}, {}, 24)[0]
    assert not seeding_status({**base, "seeding_time": 10 ** 7}, {}, 24)[0]  # onbekend = privé
    assert seeding_status({**base, "private": False, "seeding_time": 25 * 3600}, {}, 24)[0]
    assert not seeding_status({**base, "progress": 0.5}, {}, 24)[0]


@pytest.mark.asyncio
async def test_sab_view_parsing(ctx):
    def handler(request: httpx.Request) -> httpx.Response:
        mode = request.url.params["mode"]
        if mode == "get_config":
            return httpx.Response(200, json={"config": {
                "misc": {"complete_dir": "/data/downloads/complete",
                         "download_dir": "/data/downloads/incomplete"},
                "categories": [{"name": "tv", "dir": "tv"}, {"name": "software", "dir": "sw"}]}})
        if mode == "queue":
            return httpx.Response(200, json={"queue": {"slots": [
                {"nzo_id": "SABnzbd_nzo_q", "cat": "tv", "filename": "x"}]}})
        return httpx.Response(200, json={"history": {"slots": [
            {"nzo_id": "SABnzbd_nzo_h", "status": "Failed", "category": "tv",
             "storage": "/data/downloads/complete/tv/y", "completed": 1700000000}]}})

    ctx.services["download_client_transports"] = {"SAB": httpx.MockTransport(handler)}
    dc = DownloadClientCfg(name="SAB", implementation="Sabnzbd", host="sab", port=8080)
    view = await SabClient(ctx, dc, "k").view({"tv"})
    assert [r.path for r in view.roots] == ["/data/downloads/complete/tv",
                                            "/data/downloads/incomplete"]
    q, h = view.items
    assert q.active and q.id == "SABNZBD_NZO_Q"
    assert h.failed and not h.active and h.path.endswith("/tv/y")


# -- padvertaling: clients in een eigen TrueNAS-app zien andere paden --------------------------
from mediaguard.downloads import paths as P  # noqa: E402
from mediaguard.downloads.clients import ClientView  # noqa: E402


def test_apply_longest_prefix():
    m = {"/downloads": "/data/downloads", "/downloads/complete/tv": "/data/x/tv"}
    assert P.apply("/downloads/complete/tv/A", m) == "/data/x/tv/A"
    assert P.apply("/downloads/other/B", m) == "/data/downloads/other/B"
    assert P.apply("/elders/C", m) == "/elders/C"
    assert P.apply("/downloadsX/D", m) == "/downloadsX/D"  # geen half voorvoegsel


def test_remote_path_mapping_used_only_when_local_exists(tmp_path):
    local = tmp_path / "data" / "downloads"
    local.mkdir(parents=True)
    rows = [{"host": "sabnzbd", "remotePath": "/downloads/", "localPath": str(local) + "/"},
            {"host": "andere", "remotePath": "/x/", "localPath": str(local)},
            {"host": "sabnzbd", "remotePath": "/y/", "localPath": "/bestaat/niet"}]
    assert P.from_remote_path_mappings(rows, "SABnzbd") == {"/downloads": str(local)}


def test_detect_by_matching_items(tmp_path):
    a = tmp_path / "data" / "downloads" / "complete" / "tv"
    b = tmp_path / "data" / "backup" / "tv"
    for d in (a, b):
        d.mkdir(parents=True)
    for n in ("s1", "s2", "s3"):
        (a / n).mkdir()
    (b / "s1").mkdir()
    local, why = P.detect("/mnt/pool/dl/tv", ["s1", "s2", "s3", "s4"], [tmp_path / "data"])
    assert local == str(a) and "3 items" in why


def test_detect_by_unique_suffix(tmp_path):
    a = tmp_path / "data" / "downloads" / "complete" / "tv"
    b = tmp_path / "data" / "media" / "tv"
    for d in (a, b):
        d.mkdir(parents=True)
    local, why = P.detect("/downloads/complete/tv", [], [tmp_path / "data"])
    assert local == str(a) and why == "zelfde mappenstructuur"


def test_detect_ambiguous_is_skipped(tmp_path):
    for d in ("one/tv", "two/tv"):
        (tmp_path / "data" / d).mkdir(parents=True)
    local, why = P.detect("/downloads/tv", ["x"], [tmp_path / "data"])
    assert local is None and why.startswith("pad niet herkend")
    local, why = P.detect("/downloads/films", [], [tmp_path / "data"])
    assert local is None and "geen map" in why


def test_translate_view_end_to_end(ctx, tmp_path):
    tv = tmp_path / "data" / "downloads" / "complete" / "tv"
    for n in ("Show.S01E01", "Show.S01E02", "Show.S01E03"):
        _mk(tv / n)
    ctx.services["download_janitor_search_bases"] = [tmp_path / "data"]
    remote = "/mnt/voorbeeldpool/apps/sab/complete/tv"

    def view():
        v = ClientView("sab", "nzb", roots=[Root(remote, "sab", "complete", "tv"),
                                             Root("/incomplete-elders", "sab", "incomplete")])
        v.items = [ClientItem("sab", "nzb", f"ID{i}", n, "tv", f"{remote}/{n}", active=False)
                   for i, n in enumerate(("Show.S01E01", "Show.S01E02", "Show.S01E03"))]
        return v

    snap = J.Snapshot([], [], [])
    v = view()
    J.translate_view(ctx, v, "sab", [], snap)
    assert [r.path for r in v.roots] == [str(tv)]
    assert v.items[0].path == str(tv / "Show.S01E01")
    assert snap.unrecognized and snap.unrecognized[0]["path"] == "/incomplete-elders"
    assert ctx.state_get(P.STATE_KEY)["sab"][remote] == str(tv)
    # tweede keer: uit het geheugen, ook zonder items
    v2 = view()
    v2.items = []
    J.translate_view(ctx, v2, "sab", [], J.Snapshot([], [], []))
    assert [r.path for r in v2.roots] == [str(tv)]
    # niet-herkende map verschijnt als 'pad niet herkend' in het plan
    p = J.plan(ctx, J.Snapshot([], [], [], managed={"tv"}, unrecognized=snap.unrecognized), NOW)
    assert p.refused_roots[0]["reason"].startswith("pad niet herkend")


def test_translate_prefers_remote_path_mapping(ctx, tmp_path):
    local = tmp_path / "data" / "dl"
    tv = local / "complete" / "tv"
    tv.mkdir(parents=True)
    ctx.services["download_janitor_search_bases"] = [tmp_path / "nergens"]
    v = ClientView("qb", "torrent", roots=[Root("/downloads/complete/tv", "qb", "torrent", "tv")])
    v.items = [ClientItem("qb", "torrent", "H", "Film", "tv", "/downloads/complete/tv/Film",
                          active=False)]
    J.translate_view(ctx, v, "qbittorrent",
                     [{"host": "qbittorrent", "remotePath": "/downloads/",
                       "localPath": str(local) + "/"}], J.Snapshot([], [], []))
    assert v.roots[0].path == str(tv) and v.items[0].path == str(tv / "Film")


def test_name_known_to_client_is_never_orphan(ctx, layout):
    d = _mk(layout["tv"] / "Same.Name")
    _age(d, 10)
    # de client meldt hem onder een pad dat niet te vertalen was
    it = ClientItem("sab", "nzb", "Z", "Same.Name", "tv", "/onbekend/Same.Name", active=True)
    J.plan(ctx, _snap(layout, [it]), NOW)
    assert J.plan(ctx, _snap(layout, [it]), NOW + timedelta(days=5)).actions == []
