"""Opruimer voor downloadmappen: alleen wat aantoonbaar niemand meer gebruikt, via de
prullenbak, en nooit buiten de downloadmappen of in de bibliotheek."""

from __future__ import annotations

import os
from datetime import UTC, datetime, timedelta
from pathlib import Path

import httpx
import pytest

from mediaguard.downloads import janitor as J
from mediaguard.downloads.clients import ClientItem, Root, SabClient, seeding_status

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
    res = await J.execute(ctx, p, {}, actor="t")
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


@pytest.mark.asyncio
async def test_own_temp_leftovers(ctx, tmp_path):
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
    assert J.own_leftovers(ctx, NOW) == 1
    assert not temp.exists() and active.exists()


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
    from mediaguard.provisioning.store import DownloadClientCfg

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
