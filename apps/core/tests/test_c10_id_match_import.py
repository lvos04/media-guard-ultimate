"""Sonarr 'matched to series by ID, automatic import not possible': zelf importeren."""

import contextlib

import pytest

from mediaguard.activity import manual_import as mi
from mediaguard.activity.api import needs_manual_import
from mediaguard.db import utcnow
from mediaguard.downloads.failures import refusal_reason

SONARR_MSG = "Found matching series via grab history, but release was matched to series by " \
             "ID. Automatic import is not possible."
SONARR_MSG2 = "release was matched to series by ID, automatic import not possible"
BENIGN = [{"reason": SONARR_MSG2}]


class Fake:
    app = "sonarr"

    def __init__(self, items, episodes=None):
        self.items, self.episodes, self.posts = items, episodes or [], []

    async def get(self, path, **params):
        if path == "manualimport":
            return self.items
        if path == "episode":
            return self.episodes
        if path.startswith("command/"):
            return {"status": "completed"}
        if path == "history":  # elke verstuurde file is echt geïmporteerd
            return {"records": [{"eventType": "downloadFolderImported",
                                 "downloadId": params["downloadId"], "date": utcnow()}
                                for p in self.posts for _ in p["files"]]}
        raise AssertionError(path)

    async def post(self, path, body):
        self.posts.append(body)
        return {"id": 5}


def _patch(monkeypatch, fake):
    @contextlib.asynccontextmanager
    async def client(_ctx, _iid, **_kw):
        yield fake

    monkeypatch.setattr(mi, "arr_client", client)


def test_detection_variants():
    for m in (SONARR_MSG, SONARR_MSG2,
              "Found matching movie via grab history, but release was matched to movie by "
              "ID. Manual Import required."):
        assert needs_manual_import([m])
        assert refusal_reason([m]) is None
    assert not needs_manual_import(["Not an upgrade for existing episode file(s)"])


def test_absolute_number_parsing():
    assert mi.absolute_number("/dl/[SubsPlease] Dragon Quest Dai - 017 (1080p).mkv") == 17
    assert mi.absolute_number("/dl/Dai [012] [1080p x265].mkv") == 12
    assert mi.absolute_number("/dl/Show.S01E05.1080p.mkv") is None


@pytest.mark.asyncio
async def test_standard_season_episode_match(ctx, monkeypatch):
    fake = Fake([{"path": "/dl/Show.S01E05.mkv", "series": {"id": 3},
                  "episodes": [{"id": 105}], "rejections": BENIGN}])
    _patch(monkeypatch, fake)
    res = await mi.try_import(ctx, "sonarr-series", "abc", series_id=3, episode_ids=[105],
                              actor="t")
    assert res["imported"] and fake.posts[0]["files"][0]["episodeIds"] == [105]


@pytest.mark.asyncio
async def test_anime_absolute_numbering(ctx, monkeypatch):
    eps = [{"id": 900 + n, "absoluteEpisodeNumber": n} for n in range(1, 30)]
    fake = Fake([{"path": "/dl/[Grp] Dragon Quest Dai - 017 [1080p].mkv",
                  "series": {"id": 9}, "episodes": [], "rejections": BENIGN}], eps)
    _patch(monkeypatch, fake)
    res = await mi.try_import(ctx, "sonarr-anime", "dai", series_id=9, actor="t")
    assert res["imported"] and fake.posts[0]["files"][0]["episodeIds"] == [917]


@pytest.mark.asyncio
async def test_season_pack_file_by_file(ctx, monkeypatch):
    items = [{"path": f"/dl/pack/Show.S01E0{n}.mkv", "series": {"id": 3},
              "episodes": [{"id": 100 + n}], "rejections": BENIGN} for n in (1, 2, 3)]
    items.append({"path": "/dl/pack/sample.mkv", "series": {"id": 3}, "episodes": []})
    fake = Fake(items)
    _patch(monkeypatch, fake)
    res = await mi.try_import(ctx, "sonarr-series", "pack", series_id=3,
                              episode_ids=[101, 102, 103], actor="t")
    assert res["imported"] and res["count"] == 3
    assert [f["episodeIds"] for f in fake.posts[0]["files"]] == [[101], [102], [103]]


@pytest.mark.asyncio
async def test_ambiguous_not_imported(ctx, monkeypatch):
    # Sonarr zegt aflevering 105, absoluut nummer zegt 917: zonder AI overslaan
    eps = [{"id": 917, "absoluteEpisodeNumber": 17}]
    fake = Fake([{"path": "/dl/Show - 017.mkv", "series": {"id": 3},
                  "episodes": [{"id": 105}], "rejections": BENIGN}], eps)
    _patch(monkeypatch, fake)
    res = await mi.try_import(ctx, "sonarr-series", "x", series_id=3, actor="t")
    assert not res["imported"] and fake.posts == []
    # bestand zonder enige afleveringskoppeling
    fake = Fake([{"path": "/dl/random.mkv", "series": {"id": 3}, "episodes": []}])
    _patch(monkeypatch, fake)
    assert not (await mi.try_import(ctx, "sonarr-series", "y", series_id=3,
                                    actor="t"))["imported"]


@pytest.mark.asyncio
async def test_real_rejection_blocks(ctx, monkeypatch):
    fake = Fake([{"path": "/dl/Show.S01E05.mkv", "series": {"id": 3},
                  "episodes": [{"id": 105}],
                  "rejections": [{"reason": "Not an upgrade for existing episode file(s)"}]}])
    _patch(monkeypatch, fake)
    res = await mi.try_import(ctx, "sonarr-series", "abc", series_id=3, actor="t")
    assert not res["imported"] and fake.posts == []


@pytest.mark.asyncio
async def test_episode_outside_grab_not_imported(ctx, monkeypatch):
    fake = Fake([{"path": "/dl/Show.S01E06.mkv", "series": {"id": 3},
                  "episodes": [{"id": 106}], "rejections": BENIGN}])
    _patch(monkeypatch, fake)
    res = await mi.try_import(ctx, "sonarr-series", "abc", series_id=3, episode_ids=[105],
                              actor="t")
    assert not res["imported"]
