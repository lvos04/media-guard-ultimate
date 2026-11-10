"""Vangnet voor voltooide downloads die Sonarr/Radarr nooit importeerden (6 nov: SABnzbd
kende tv-anime/movies-anime niet, downloads bleven in de standaardmap liggen)."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

import pytest

from mediaguard.downloads import janitor as J
from mediaguard.downloads import recovery as R
from mediaguard.downloads.clients import ClientItem

OLD = datetime.now(UTC) - timedelta(hours=5)


def _item(path: Path | None, nzo: str, name: str, cat: str = "*", **kw: Any) -> ClientItem:
    return ClientItem("SABnzbd", "nzb", nzo.upper(), name, cat, str(path) if path else None,
                      active=False, completed_at=kw.pop("completed_at", OLD), **kw)


def _grab(nzo: str, title: str, instance: str = "sonarr-anime", app: str = "sonarr",
          series: int = 7) -> dict[str, Any]:
    return {"instance_id": instance, "app": app, "download_id": nzo.upper(), "title": title,
            "series_id": series, "episode_id": 70, "movie_id": None, "date": None}


class Harness:
    def __init__(self, monkeypatch, items: list[ClientItem], grabs: list[dict[str, Any]],
                 imported: set[str] | None = None) -> None:
        self.items, self.grabs = items, grabs
        self.imported = imported or set()
        self.imports: list[tuple[str, str]] = []
        self.unblocked: list[str] = []

        async def gather(ctx, managed_override=None):
            assert "*" in managed_override  # ook SABnzbd's standaardcategorie
            snap = J.Snapshot(roots=[], library_roots=[], items=list(self.items))
            return snap, {}

        async def grabs(ctx):
            by_id = {g["download_id"]: g for g in self.grabs}
            by_title: dict[str, list[dict[str, Any]]] = {}
            for g in self.grabs:
                by_title.setdefault(g["title"].lower(), []).append(g)
            return by_id, by_title, set(self.imported), set()

        async def do_import(ctx, owner, item):
            self.imports.append((owner["instance_id"], item.id))
            return 1

        async def unblock(ctx, owner, release):
            self.unblocked.append(release)
            return 1

        monkeypatch.setattr(J, "gather", gather)
        monkeypatch.setattr(R, "_grabs", grabs)
        monkeypatch.setattr(R, "_import", do_import)
        monkeypatch.setattr(R, "_unblocklist", unblock)


@pytest.mark.asyncio
async def test_default_category_download_is_imported_and_unblocked(ctx, monkeypatch, tmp_path):
    f = tmp_path / "complete" / "Show.S01E05.1080p-GRP"
    f.mkdir(parents=True)
    it = _item(f, "SABnzbd_nzo_abc", "Show.S01E05.1080p-GRP")
    h = Harness(monkeypatch, [it], [_grab("SABnzbd_nzo_abc", "Show.S01E05.1080p-GRP")])
    assert not R.janitor_may_remove(ctx, it.id)
    await R.run_once(ctx)
    assert h.imports == [("sonarr-anime", it.id)]
    assert R.status_of(ctx, it.id) == "importing"
    # tweede ronde: Sonarr heeft hem geïmporteerd
    h.imported = {it.id}
    res = await R.run_once(ctx)
    assert res["imported"] == ["Show.S01E05.1080p-GRP"]
    assert R.status_of(ctx, it.id) == "imported"
    assert h.unblocked == ["Show.S01E05.1080p-GRP"]
    assert R.janitor_may_remove(ctx, it.id)
    note = ctx.db.fetch_one("SELECT body FROM notifications WHERE title='Downloads hersteld'")
    assert note and "alsnog geïmporteerd" in note["body"]
    assert R.summary(ctx)["imported"] == 1
    assert "alsnog geïmporteerd" in (R.digest_line(ctx, "2000-01-01") or "")


@pytest.mark.asyncio
async def test_restored_from_janitor_trash_before_import(ctx, monkeypatch, tmp_path):
    orig = tmp_path / "complete" / "Movie.2024.2160p-GRP"
    trash = tmp_path / "complete" / ".mediaguard-prullenbak" / "x" / "Movie.2024.2160p-GRP"
    trash.mkdir(parents=True)
    ctx.db.execute(
        "INSERT INTO download_janitor_trash(root,original_path,trash_path,reason,moved_at) "
        "VALUES (?,?,?,?,?)", (str(tmp_path), str(orig), str(trash), "test", "2026-11-05"))
    it = _item(orig, "nzo_m", "Movie.2024.2160p-GRP")
    h = Harness(monkeypatch, [it], [_grab("nzo_m", "Movie.2024.2160p-GRP",
                                          "radarr-animefilms", "radarr")])
    await R.run_once(ctx)
    assert orig.exists() and not trash.exists()
    assert h.imports == [("radarr-animefilms", it.id)]


@pytest.mark.asyncio
async def test_without_grab_not_recoverable_and_janitor_may_clean(ctx, monkeypatch, tmp_path):
    f = tmp_path / "complete" / "Random.Thing"
    f.mkdir(parents=True)
    it = _item(f, "nzo_x", "Random.Thing")
    h = Harness(monkeypatch, [it], [])
    res = await R.run_once(ctx)
    assert h.imports == [] and R.status_of(ctx, it.id) == "not_recoverable"
    assert res["not_recoverable"] and R.janitor_may_remove(ctx, it.id)


@pytest.mark.asyncio
async def test_title_match_only_when_unambiguous(ctx, monkeypatch, tmp_path):
    f = tmp_path / "complete" / "Same.Title"
    f.mkdir(parents=True)
    it = _item(f, "nzo_other_id", "Same.Title")
    # twee verschillende series met exact dezelfde releasetitel; geen AI → laten liggen
    h = Harness(monkeypatch, [it], [_grab("a", "Same.Title", series=1),
                                    _grab("b", "Same.Title", series=2)])
    await R.run_once(ctx)
    assert h.imports == [] and R.status_of(ctx, it.id) == "not_recoverable"
    # één kandidaat op titel → wel importeren
    it2 = _item(f, "nzo_t2", "Unique.Title")
    h2 = Harness(monkeypatch, [it2], [_grab("c", "Unique.Title", series=3)])
    await R.run_once(ctx)
    assert h2.imports == [("sonarr-anime", it2.id)]


@pytest.mark.asyncio
async def test_fresh_active_queued_and_failed_are_left_alone(ctx, monkeypatch, tmp_path):
    f = tmp_path / "complete" / "a"
    f.mkdir(parents=True)
    fresh = _item(f, "n1", "a", completed_at=datetime.now(UTC) - timedelta(minutes=5))
    failed = _item(f, "n2", "a", failed=True)
    h = Harness(monkeypatch, [fresh, failed], [_grab("n1", "a"), _grab("n2", "a")])
    await R.run_once(ctx)
    assert h.imports == []
    assert R.status_of(ctx, fresh.id) is None and R.status_of(ctx, failed.id) is None


@pytest.mark.asyncio
async def test_gives_up_after_two_attempts(ctx, monkeypatch, tmp_path):
    f = tmp_path / "complete" / "Show.S02E01"
    f.mkdir(parents=True)
    it = _item(f, "nzo_r", "Show.S02E01")
    h = Harness(monkeypatch, [it], [_grab("nzo_r", "Show.S02E01")])
    for _ in range(3):
        await R.run_once(ctx)
    assert len(h.imports) == 2
    assert R.status_of(ctx, it.id) == "not_recoverable"


def test_disabled_setting_lets_janitor_proceed(ctx):
    ctx.config.set("global", "", "downloads.recovery_enabled", False, "t")
    assert R.janitor_may_remove(ctx, "WHATEVER")
