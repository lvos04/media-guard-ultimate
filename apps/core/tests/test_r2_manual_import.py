"""Rapport 2 / N9: eenduidige ID-match automatisch importeren, anders niet."""

import contextlib

import pytest

from mediaguard.activity import manual_import as mi
from mediaguard.activity.api import import_message_nl, needs_manual_import
from mediaguard.db import utcnow

MSG = ("Found matching movie via grab history, but release was matched to movie by ID. "
       "Manual Import required.")


class Fake:
    """Radarr: ManualImport geeft een opdracht-ID; 'gelukt' blijkt pas uit de historie."""
    app = "radarr"

    def __init__(self, items, *, status="completed", imports=True):
        self.items, self.posts = items, []
        self.status, self.imports = status, imports

    async def get(self, path, **params):
        if path.startswith("command/"):
            return {"id": 1, "status": self.status, "message": "Disk full"}
        if path == "history":
            assert params["downloadId"] == "abc"
            done = self.imports and self.status == "completed"
            return {"records": [{"eventType": "downloadFolderImported", "downloadId": "abc",
                                 "date": utcnow()} for p in self.posts for _ in p["files"]]
                    if done else []}
        assert path == "manualimport" and params["downloadId"] == "abc"
        return self.items

    async def post(self, path, body):
        self.posts.append(body)
        return {"id": 1, "status": "queued"}


def _patch(monkeypatch, fake):
    @contextlib.asynccontextmanager
    async def client(_ctx, _iid, **_kw):
        yield fake

    monkeypatch.setattr(mi, "arr_client", client)


def test_message_detection_and_translation():
    assert needs_manual_import([MSG])
    assert "download-ID" in import_message_nl(MSG)


@pytest.mark.asyncio
async def test_single_matching_file_is_imported(ctx, monkeypatch):
    fake = Fake([{"path": "/dl/Pope.2023.mkv", "movie": {"id": 7}, "quality": {},
                  "rejections": [{"reason": "matched to movie by ID"}]}])
    _patch(monkeypatch, fake)
    res = await mi.try_import(ctx, "radarr-films", "abc", movie_id=7, actor="t")
    assert res["imported"] and fake.posts[0]["files"][0]["movieId"] == 7


@pytest.mark.asyncio
async def test_other_rejection_or_two_files_not_imported(ctx, monkeypatch):
    fake = Fake([{"path": "/dl/a.mkv", "movie": {"id": 7},
                  "rejections": [{"reason": "Not an upgrade for existing movie file"}]}])
    _patch(monkeypatch, fake)
    assert not (await mi.try_import(ctx, "radarr-films", "abc", movie_id=7,
                                    actor="t"))["imported"]
    fake = Fake([{"path": "/dl/a.mkv", "movie": {"id": 7}}, {"path": "/dl/b.mkv",
                                                              "movie": {"id": 7}}])
    _patch(monkeypatch, fake)
    assert not (await mi.try_import(ctx, "radarr-films", "abc", movie_id=7,
                                    actor="t"))["imported"]
    assert fake.posts == []


# ── verbeterlijst 12: 'gelukt' pas na een echte import ─────────────────────────────────
@pytest.mark.asyncio
async def test_sent_command_is_not_success_until_import_really_happened(ctx, monkeypatch):
    item = {"path": "/dl/Pope.2023.mkv", "movie": {"id": 7}, "quality": {},
            "rejections": [{"reason": "matched to movie by ID"}]}
    # opdracht afgerond maar Sonarr/Radarr weigerde het bestand bij het importeren
    fake = Fake([item], imports=False)
    _patch(monkeypatch, fake)
    res = await mi.try_import(ctx, "radarr-films", "abc", movie_id=7, actor="t")
    assert fake.posts and not res["imported"] and "niet geïmporteerd" in res["reason_nl"]
    assert not ctx.db.fetch_one("SELECT 1 FROM audit_events WHERE "
                                "action='download.manual_import'")
    # opdracht mislukt: nooit 'gelukt'
    fake = Fake([item], status="failed")
    _patch(monkeypatch, fake)
    res = await mi.try_import(ctx, "radarr-films", "abc", movie_id=7, actor="t")
    assert not res["imported"] and "mislukt" in res["reason_nl"]


@pytest.mark.asyncio
async def test_running_import_is_pending_and_not_sent_twice(ctx, monkeypatch):
    item = {"path": "/dl/Pope.2023.mkv", "movie": {"id": 7}, "quality": {},
            "rejections": [{"reason": "matched to movie by ID"}]}
    fake = Fake([item], status="started")
    _patch(monkeypatch, fake)
    res = await mi.try_import(ctx, "radarr-films", "abc", movie_id=7, actor="t",
                              wait_seconds=0)
    assert res["pending"] and not res["imported"] and len(fake.posts) == 1
    # volgende ronde: nog bezig → niet opnieuw versturen
    res = await mi.try_import(ctx, "radarr-films", "abc", movie_id=7, actor="t")
    assert res["pending"] and len(fake.posts) == 1
    assert not ctx.db.fetch_one("SELECT 1 FROM audit_events WHERE "
                                "action='download.manual_import'")
    # klaar en geïmporteerd → nu pas gelukt (en pas nu in de audit)
    fake.status = "completed"
    res = await mi.try_import(ctx, "radarr-films", "abc", movie_id=7, actor="t")
    assert res["imported"] and res["count"] == 1 and len(fake.posts) == 1
    row = ctx.db.fetch_one("SELECT new_value FROM audit_events WHERE "
                           "action='download.manual_import'")
    assert row and "Pope.2023.mkv" in row["new_value"]
