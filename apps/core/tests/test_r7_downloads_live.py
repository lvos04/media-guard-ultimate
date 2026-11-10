"""Rapport 7 / A2: Activiteit → Downloads leest ook de downloadclients zelf."""

from __future__ import annotations

import httpx
from test_arr_fakes import FakeArr, b1ctx, make_series, wire  # noqa: F401

from mediaguard.downloads import live


def _clients(ctx, handler):
    from mediaguard.provisioning import store

    store.save(ctx, {"download_clients": [
        {"name": "SAB", "implementation": "Sabnzbd", "host": "sab", "port": 8080,
         "api_key": "k"},
        {"name": "qBit", "implementation": "QBittorrent", "host": "qb", "port": 8081}]}, "t")
    ctx.services["live_download_transport"] = httpx.MockTransport(handler)


def _handler(request):
    if request.url.host == "sab":
        return httpx.Response(200, json={"queue": {"paused": False, "slots": [
            {"nzo_id": "SABnzbd_nzo_1", "filename": "Nevelwoud.S01E01.1080p", "cat": "tv",
             "status": "Downloading", "mb": "1000", "mbleft": "250", "percentage": "75",
             "timeleft": "0:02:10"},
            {"nzo_id": "SABnzbd_nzo_2", "filename": "Linked.Release", "cat": "tv",
             "status": "Queued", "mb": "500", "mbleft": "500", "percentage": "0"}]}})
    if request.url.path.endswith("/torrents/info"):
        return httpx.Response(200, json=[
            {"hash": "ab" * 20, "name": "Spierkracht.S02E03", "state": "downloading",
             "category": "tv-anime", "progress": 0.4, "size": 2000, "amount_left": 1200,
             "eta": 125},
            {"hash": "cd" * 20, "name": "Seeding.Done", "state": "uploading", "progress": 1}])
    return httpx.Response(404)


def test_client_downloads_reads_sab_and_qbit(b1ctx):  # noqa: F811
    import asyncio

    _clients(b1ctx, _handler)
    items, errors = asyncio.run(live.client_downloads(b1ctx))
    assert errors == []
    names = {i["release"]: i for i in items}
    assert set(names) == {"Nevelwoud.S01E01.1080p", "Linked.Release", "Spierkracht.S02E03"}
    assert names["Nevelwoud.S01E01.1080p"]["status"] == "downloading"
    assert names["Nevelwoud.S01E01.1080p"]["progress"] == 75.0
    assert names["Spierkracht.S02E03"]["progress"] == 40.0
    assert names["Spierkracht.S02E03"]["time_left"] == "0:02:05"


def test_unreachable_client_is_an_error_not_zero(b1ctx):  # noqa: F811
    import asyncio

    def boom(request):
        raise httpx.ConnectError("geweigerd", request=request)

    _clients(b1ctx, boom)
    items, errors = asyncio.run(live.client_downloads(b1ctx))
    assert items == [] and len(errors) == 2
    assert all("niet bereikbaar" in e["error"] for e in errors)


def test_activity_shows_client_downloads_and_merges_linked(client, b1ctx):  # noqa: F811
    fake = wire(b1ctx, "sonarr-series", FakeArr("sonarr"))
    fake.add_series(make_series(7, "Linked", 1), [], [])
    fake.queue = [{"id": 1, "seriesId": 7, "title": "Linked.Release", "size": 500,
                   "sizeleft": 500, "status": "queued", "downloadId": "SABnzbd_nzo_2",
                   "protocol": "usenet", "downloadClient": "SAB", "statusMessages": []}]
    _clients(b1ctx, _handler)
    data = client.get("/api/v1/activity").json()
    rel = [d["release"] for d in data["downloads"]]
    assert rel.count("Linked.Release") == 1  # gekoppeld, niet dubbel
    only = {d["release"] for d in data["downloads"] if d.get("only_in_client")}
    assert only == {"Nevelwoud.S01E01.1080p", "Spierkracht.S02E03"}
    assert data["summary"]["downloads"] >= 2
    assert data["summary"]["download_clients_ok"] is True


def test_activity_reports_unreachable_client(client, b1ctx):  # noqa: F811
    def boom(request):
        raise httpx.ConnectError("geweigerd", request=request)

    _clients(b1ctx, boom)
    data = client.get("/api/v1/activity").json()
    assert data["summary"]["download_clients_ok"] is False
    assert any("niet bereikbaar" in e["error"] for e in data["errors"])
