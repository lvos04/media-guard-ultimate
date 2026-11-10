"""Rapport 2 / N10: collecties mee naar de hernoemde hoofdmap; korte melding."""

import pytest

from mediaguard.instances.health_nl import translate
from mediaguard.storage_typos import repoint_collections


class FakeRadarr:
    app = "radarr"

    def __init__(self):
        self.cols = [{"id": 1, "title": "AFC", "rootFolderPath": "/data/Media/Anime Fims/"},
                     {"id": 2, "title": "X", "rootFolderPath": "/data/Media/Films"}]
        self.puts = []

    async def get(self, path):
        assert path == "collection"
        return self.cols

    async def put(self, path, body):
        self.puts.append((path, body["rootFolderPath"]))


@pytest.mark.asyncio
async def test_collections_follow_root_rename():
    c = FakeRadarr()
    n = await repoint_collections(c, "/data/Media/Anime Fims", "/data/Media/Anime Films")
    assert n == 1 and c.puts == [("collection", "/data/Media/Anime Films")]  # bulk-editor


def test_collection_health_message_is_short_dutch():
    t = translate({"source": "RootFolderCheck", "message":
                   "Missing root folder for movie collection: /data/Media/Anime Fims (A, B, C)"})
    assert t.startswith("3 collecties wijzen naar /data/Media/Anime Fims")


@pytest.mark.asyncio
async def test_case_twin_detected_and_only_empty_removable(ctx, tmp_path, monkeypatch):
    import contextlib
    from types import SimpleNamespace

    from mediaguard import storage_typos as st

    big, small = tmp_path / "Media" / "TV", tmp_path / "media" / "tv"
    big.mkdir(parents=True)
    small.mkdir(parents=True)
    (big / "Show").mkdir()
    lower_big = str(big).lower()
    assert lower_big == str(small).lower()

    class C:
        async def root_folders(self):
            return [{"id": 1, "path": str(big)}, {"id": 2, "path": str(small)}]

    @contextlib.asynccontextmanager
    async def fake_client(_ctx, _iid, **_kw):
        yield C()

    monkeypatch.setattr(st, "arr_client", fake_client)
    monkeypatch.setattr(st, "list_instances",
                        lambda _c: [SimpleNamespace(id="sonarr-series", name="Series")])
    found = {t["root_folder_id"]: t for t in await st.detect_twins(ctx)}
    assert found[2]["removable"] and not found[1]["removable"]


def test_count_changes_explains_removed_titles(client, ctx):
    from datetime import UTC, datetime, timedelta

    from mediaguard.library.api import record_count_snapshot

    yday = (datetime.now(UTC).date() - timedelta(days=1)).isoformat()
    ctx.state_set(f"library_counts:{yday}", {"sonarr-series": {"available": 10}})
    record_count_snapshot(ctx)
    r = client.get("/api/v1/library-count-changes?days=3").json()
    assert len(r["days"]) == 2 and r["days"][1]["delta"] is not None
