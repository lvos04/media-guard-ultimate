"""Mediaserver-koppeling (Emby eerst) en Nederlandse ondertitels — rapport 5, M3/M8.
Alles tegen een nagebootste Emby-, Jellyfin-, Plex-, Bazarr- en OpenSubtitles-server."""

from __future__ import annotations

import asyncio
import json
from typing import Any

import httpx
import pytest

from mediaguard.db import utcnow
from mediaguard.mediaserver import paths as P
from mediaguard.mediaserver import service as S
from mediaguard.mediaserver import subtitles as T
from mediaguard.mediaserver.clients import EmbyClient, judge_playback

EMBY = "http://nas:8096"
MG_ROOT = "/data/Media/Films"
EMBY_ROOT = "/mnt/tank/Media/Films"
MOVIE = f"{MG_ROOT}/Stormvogel (2017)/Stormvogel (2017).mkv"
EMBY_MOVIE = f"{EMBY_ROOT}/Stormvogel (2017)/Stormvogel (2017).mkv"


class FakeEmby:
    """Minimale Emby Server-API onder /emby, met X-Emby-Token."""

    def __init__(self, key: str = "sleutel", product: str | None = None) -> None:
        self.key = key
        self.product = product
        self.updated: list[str] = []
        self.full_refresh = 0
        self.sessions: list[dict[str, Any]] = []
        self.playback: dict[str, Any] = {"MediaSources": [{
            "SupportsDirectPlay": True, "MediaStreams": [{"Type": "Video", "Codec": "av1"}]}]}
        self.known = {EMBY_MOVIE: "42"}
        self.prefix = "" if product else "/emby"

    def handler(self, request: httpx.Request) -> httpx.Response:
        url = request.url
        if url.host not in ("nas",) or url.port != 8096:
            raise httpx.ConnectError("geen server", request=request)
        path = url.path
        if not path.startswith(self.prefix):
            return httpx.Response(404)
        path = path[len(self.prefix):]
        if path == "/System/Info/Public":
            info = {"ServerName": "Thuis", "Version": "4.9.0", "Id": "abc"}
            if self.product:
                info["ProductName"] = self.product
            return httpx.Response(200, json=info)
        if request.headers.get("X-Emby-Token") != self.key:
            return httpx.Response(401)
        if path == "/System/Info":
            return httpx.Response(200, json={"Id": "abc"})
        if path == "/Library/VirtualFolders":
            return httpx.Response(200, json=[{"Name": "Films", "Locations": [EMBY_ROOT]}])
        if path == "/Library/Media/Updated":
            self.updated += [u["Path"] for u in json.loads(request.content)["Updates"]]
            return httpx.Response(204)
        if path == "/Library/Refresh":
            self.full_refresh += 1
            return httpx.Response(204)
        if path == "/Sessions":
            return httpx.Response(200, json=self.sessions)
        if path == "/Users":
            return httpx.Response(200, json=[{"Id": "u1", "Policy": {"IsAdministrator": True}}])
        if path == "/Items":
            items = [{"Id": i, "Type": "Movie", "Path": p} for p, i in self.known.items()
                     if request.url.params.get("SearchTerm", "").lower() in p.lower()]
            return httpx.Response(200, json={"Items": items})
        if path.endswith("/PlaybackInfo"):
            return httpx.Response(200, json=self.playback)
        return httpx.Response(404)


class FakeEncoder:
    def __init__(self) -> None:
        self.calls: list[tuple[bool, list[str], str]] = []

    async def set_playback_hold(self, active: bool, lanes: list[str], reason: str
                                ) -> dict[str, Any]:
        self.calls.append((active, lanes, reason))
        return {}


@pytest.fixture
def emby(ctx, monkeypatch):  # type: ignore[no-untyped-def]
    fake = FakeEmby()
    ctx.services["mediaserver_transport"] = httpx.MockTransport(fake.handler)
    ctx.services["encoder_client"] = FakeEncoder()
    monkeypatch.setattr(S, "mg_roots", lambda _ctx: [MG_ROOT])
    ctx.config.set("global", "", "mediaserver.url", EMBY, "test")
    ctx.secrets.set(S.SECRET_KEY, "sleutel", "test")
    return fake


def _commit_txn(ctx, path: str = MOVIE, kind: str = "replace", job: int | None = 7) -> int:  # type: ignore[no-untyped-def]
    return ctx.db.execute_insert(
        "INSERT INTO replacement_transactions(kind,library_path,final_path,staged_path,"
        "source_snapshot_json,state,encoding_job_id,created_at,updated_at) "
        "VALUES (?,?,?,?,?,?,?,?,?)",
        (kind, path, path, "/work/x", "{}", "committed", job, utcnow(), utcnow()))


def test_paths_map_on_tail_and_both_ways() -> None:
    m = P.build_map([MG_ROOT, "/data/Media/TV"], [EMBY_ROOT, "/mnt/tank/Media/TV", "/x/Muziek"])
    assert (MG_ROOT, EMBY_ROOT) in m and len(m) == 2
    assert P.to_server(MOVIE, m) == EMBY_MOVIE
    assert P.to_mediaguard(EMBY_MOVIE, m) == MOVIE
    assert P.to_server("/elders/x.mkv", m) is None
    # niet eenduidig → niet koppelen
    assert P.build_map(["/a/Films"], ["/x/Films", "/y/Films"]) == []


def test_discover_finds_emby_first(ctx, emby) -> None:  # type: ignore[no-untyped-def]
    res = asyncio.run(S.discover(ctx, hosts=["nas"], udp=False))
    assert res["servers"][0]["kind"] == "emby" and res["servers"][0]["url"] == EMBY
    assert res["servers"][0]["label"] == "Emby"


def test_jellyfin_detected_by_product_name(ctx) -> None:  # type: ignore[no-untyped-def]
    fake = FakeEmby(product="Jellyfin Server")
    ctx.services["mediaserver_transport"] = httpx.MockTransport(fake.handler)
    info = asyncio.run(EmbyClient(ctx.services, EMBY, None, "jellyfin").info())
    assert info["kind"] == "jellyfin"


def test_connection_maps_paths_and_wrong_key_is_clear(ctx, emby) -> None:  # type: ignore[no-untyped-def]
    res = asyncio.run(S.test_connection(ctx))
    assert res["ok"] and "Emby" in res["message_nl"] and [MG_ROOT, EMBY_ROOT] in [
        list(p) for p in res["path_map"]]
    ctx.secrets.set(S.SECRET_KEY, "fout", "test")
    bad = asyncio.run(S.test_connection(ctx))
    assert not bad["ok"] and "sleutel geweigerd" in bad["message_nl"]


def test_refresh_after_handoff_then_playback_test(ctx, emby) -> None:  # type: ignore[no-untyped-def]
    asyncio.run(S.tick(ctx, sessions=False))           # zet de cursor
    txn = _commit_txn(ctx)
    asyncio.run(S.tick(ctx, sessions=False))
    assert emby.updated == [EMBY_MOVIE]
    # afspeeltest pas na ± 10 min
    ctx.db.execute("UPDATE mediaserver_pending SET test_after=? WHERE transaction_id=?",
                   ("2000-01-01T00:00:00+00:00", txn))
    asyncio.run(S.tick(ctx, sessions=False))
    row = S.playback_for_job(ctx, 7)
    assert row and row["result"] == "ok" and "direct" in row["detail_nl"]


def test_failed_playback_notifies(ctx, emby) -> None:  # type: ignore[no-untyped-def]
    emby.playback = {"ErrorCode": "NoCompatibleStream"}
    asyncio.run(S.tick(ctx, sessions=False))
    txn = _commit_txn(ctx)
    asyncio.run(S.tick(ctx, sessions=False))
    ctx.db.execute("UPDATE mediaserver_pending SET test_after=? WHERE transaction_id=?",
                   ("2000-01-01T00:00:00+00:00", txn))
    asyncio.run(S.tick(ctx, sessions=False))
    assert S.playback_for_job(ctx, 7)["result"] == "failed"
    n = ctx.db.fetch_one("SELECT title FROM notifications WHERE title LIKE 'Afspeeltest%'")
    assert n is not None


def test_unmapped_path_falls_back_to_full_scan_once(ctx, emby) -> None:  # type: ignore[no-untyped-def]
    asyncio.run(S.tick(ctx, sessions=False))
    _commit_txn(ctx, path="/elders/a.mkv", job=None)
    _commit_txn(ctx, path="/elders/b.mkv", job=None)
    asyncio.run(S.tick(ctx, sessions=False))
    assert emby.full_refresh == 1 and emby.updated == []


def test_watching_holds_encodes_and_marks_recent(ctx, emby) -> None:  # type: ignore[no-untyped-def]
    enc = ctx.services["encoder_client"]
    emby.sessions = [{"UserName": "Jan", "NowPlayingItem": {"Name": "Stormvogel",
                                                             "Path": EMBY_MOVIE},
                      "PlayState": {"IsPaused": False}}]
    asyncio.run(S.tick(ctx))
    assert enc.calls[-1][0] is True and enc.calls[-1][1] == ["cpu", "gpu"]
    assert MOVIE in S.watched_paths(ctx)
    emby.sessions = []
    asyncio.run(S.tick(ctx))                 # eerste stille meting: nog niet hervatten
    assert enc.calls[-1][0] is True
    asyncio.run(S.tick(ctx))
    assert enc.calls[-1][0] is False
    ctx.config.set("global", "", "mediaserver.skip_recent_watch", False, "test")
    assert S.watched_paths(ctx) == set()


def test_paused_viewer_does_not_hold(ctx, emby) -> None:  # type: ignore[no-untyped-def]
    emby.sessions = [{"UserName": "L", "NowPlayingItem": {"Name": "x", "Path": EMBY_MOVIE},
                      "PlayState": {"IsPaused": True}}]
    asyncio.run(S.tick(ctx))
    assert ctx.services["encoder_client"].calls == []


def test_autopilot_skips_recently_watched(ctx, emby) -> None:  # type: ignore[no-untyped-def]
    from mediaguard.encoding import autopilot

    ctx.db.execute("INSERT INTO mediaserver_watch(path,title,user_name,last_seen) "
                   "VALUES (?,?,?,?)", (MOVIE, "Stormvogel", "Jan", utcnow()))
    assert MOVIE in autopilot._known_paths(ctx)


def test_judge_playback_variants() -> None:
    assert judge_playback({"MediaSources": [{"SupportsTranscoding": True,
                                             "TranscodingUrl": "/x"}]}).result == "transcode"
    assert judge_playback({"MediaSources": []}).result == "failed"


def test_api_config_status_never_leaks_key(client, ctx) -> None:  # type: ignore[no-untyped-def]
    fake = FakeEmby(key="geheim-123")
    ctx.services["mediaserver_transport"] = httpx.MockTransport(fake.handler)
    r = client.post("/api/v1/mediaserver/config",
                    json={"url": "nas:8096", "api_key": "geheim-123",
                          "features": {"pause_while_watching": "gpu"}})
    assert r.status_code == 200, r.text
    st = r.json()["status"]
    assert st["key_set"] and st["label"] == "Emby" and st["url"] == EMBY
    assert st["features"]["pause_while_watching"] == "gpu"
    assert "geheim-123" not in json.dumps(st)
    assert "API-sleutels" in st["key_help_nl"]["emby"]
    bad = client.post("/api/v1/mediaserver/config", json={"features": {"bestaat_niet": 1}})
    assert bad.status_code == 422


# -- ondertitels -------------------------------------------------------------------------------
class FakeSubs:
    def __init__(self) -> None:
        self.searches: list[dict[str, Any]] = []
        self.downloads = 0

    def handler(self, request: httpx.Request) -> httpx.Response:
        url = request.url
        if url.host == "bazarr":
            if request.headers.get("X-API-KEY") != "bk":
                return httpx.Response(401)
            if url.path == "/api/system/status":
                return httpx.Response(200, json={"data": {"bazarr_version": "1.4"}})
            if url.path == "/api/episodes/wanted":
                return httpx.Response(200, json={"data": [
                    {"seriesTitle": "Torenwachters", "episode_number": "3x04", "sonarrSeriesId": 5,
                     "sonarrEpisodeId": 50, "missing_subtitles": [{"code2": "nl"}]},
                    {"seriesTitle": "Torenwachters", "episode_number": "3x05", "sonarrSeriesId": 5,
                     "sonarrEpisodeId": 51, "missing_subtitles": [{"code2": "nl"}]},
                    {"seriesTitle": "Other", "episode_number": "1x01", "sonarrSeriesId": 6,
                     "sonarrEpisodeId": 60, "missing_subtitles": [{"code2": "en"}]}]})
            if url.path == "/api/movies/wanted":
                return httpx.Response(200, json={"data": []})
            if request.method == "PATCH":
                self.searches.append(dict(url.params))
                return httpx.Response(204)
        if url.host == "api.opensubtitles.com":
            if url.path.endswith("/login"):
                return httpx.Response(200, json={"token": "t"})
            if url.path.endswith("/subtitles"):
                return httpx.Response(200, json={"data": [
                    {"attributes": {"language": "nl", "download_count": 5,
                                    "files": [{"file_id": 11}]}},
                    {"attributes": {"language": "nl", "download_count": 50,
                                    "files": [{"file_id": 22}]}}]})
            if url.path.endswith("/download"):
                assert json.loads(request.content)["file_id"] == 22
                return httpx.Response(200, json={"link": "https://dl.example/x.srt"})
        if url.host == "dl.example":
            self.downloads += 1
            return httpx.Response(200, content=b"1\n00:00:01,000 --> 00:00:02,000\nHallo\n")
        return httpx.Response(404)


def test_bazarr_sync_marks_missing_and_searches_once_per_day(ctx) -> None:  # type: ignore[no-untyped-def]
    fake = FakeSubs()
    ctx.services["mediaserver_transport"] = httpx.MockTransport(fake.handler)
    ctx.config.set("global", "", "subtitles.bazarr_url", "http://bazarr:6767", "test")
    ctx.secrets.set(T.BAZARR_KEY, "bk", "test")
    res = asyncio.run(T.tick(ctx))
    assert res["bazarr"]["missing"] == 2 and fake.searches == [
        {"seriesid": "5", "action": "search-missing"}]
    assert T.missing(ctx)["total"] == 2
    asyncio.run(T.tick(ctx))
    assert len(fake.searches) == 1  # niet opnieuw binnen 24 uur


def test_opensubtitles_saves_srt_next_to_file_never_overwrites(ctx, tmp_path) -> None:  # type: ignore[no-untyped-def]
    root = tmp_path / "Films"
    video = root / "Film (2020)" / "Film (2020).mkv"
    video.parent.mkdir(parents=True)
    video.write_bytes(b"x")
    other = root / "Ander (2021)" / "Ander (2021).mkv"
    other.parent.mkdir(parents=True)
    other.write_bytes(b"x")
    keep = other.with_suffix(".nl.srt")
    keep.write_text("van de gebruiker")
    if ctx.db.fetch_one("SELECT 1 FROM instances WHERE id='radarr-films'") is None:
        ctx.db.execute("INSERT INTO instances(id,name,app,media_type,url,created_at,"
                       "updated_at) VALUES ('radarr-films','Films','radarr','movie','http://x',"
                       "?,?)", (utcnow(), utcnow()))
    for pid, path in ((1, video), (2, other)):
        ctx.db.execute(
            "INSERT INTO lib_items(instance_id,kind,provider_id,title,tmdb_id,root_folder_path,"
            "synced_at) VALUES ('radarr-films','movie',?,?,?,?,?)",
            (pid, path.stem, 100 + pid, str(root), utcnow()))
        ctx.db.execute(
            "INSERT INTO lib_files(instance_id,kind,provider_id,owner_provider_id,path,"
            "synced_at) VALUES ('radarr-films','movie',?,?,?,?)", (pid, pid, str(path), utcnow()))
    fake = FakeSubs()
    ctx.services["mediaserver_transport"] = httpx.MockTransport(fake.handler)
    ctx.config.set("global", "", "subtitles.opensubtitles_enabled", True, "test")
    for k, v in ((T.OS_KEY, "k"), (T.OS_USER, "u"), (T.OS_PASS, "p")):
        ctx.secrets.set(k, v, "test")
    res = asyncio.run(T.tick(ctx))
    assert res["opensubtitles"]["saved"] == 1
    assert video.with_suffix(".nl.srt").read_bytes().startswith(b"1\n")
    assert keep.read_text() == "van de gebruiker"  # bestond al: overgeslagen, niet gezocht
    assert T.downloads_today(ctx) == 1
