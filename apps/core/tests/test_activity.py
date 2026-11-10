"""Activiteit-pagina: alles wat nu bezig is, per bron geïsoleerd."""

from __future__ import annotations

from test_arr_fakes import (
    FakeArr,
    b1ctx,  # noqa: F401
    iso,
    make_series,
    wire,
)

from mediaguard.db import utcnow


def test_activity_aggregates_downloads_searches_av1_tasks_and_recent(client, b1ctx):  # noqa: F811
    ctx = b1ctx
    fake = wire(ctx, "sonarr-series", FakeArr("sonarr"))
    fake.add_series(make_series(7, "The Expanse", 280619), [], [])
    fake.queue = [{
        "id": 1, "seriesId": 7, "episodeId": 70, "title": "The.Expanse.S01E02.1080p.WEB",
        "series": {"title": "The Expanse"}, "episode": {"seasonNumber": 1, "episodeNumber": 2},
        "size": 1000, "sizeleft": 250, "timeleft": "00:05:00", "status": "downloading",
        "trackedDownloadState": "downloading", "trackedDownloadStatus": "ok",
        "protocol": "usenet", "downloadClient": "SABnzbd",
        "quality": {"quality": {"name": "WEBDL-1080p"}}, "statusMessages": []},
        {"id": 2, "seriesId": 7, "title": "stuck", "series": {"title": "The Expanse"},
         "episode": {"seasonNumber": 1, "episodeNumber": 3}, "size": 0, "sizeleft": 0,
         "status": "completed", "trackedDownloadStatus": "warning",
         "statusMessages": [{"title": "x", "messages": ["Geen bestanden gevonden"]}]}]
    fake.commands[90] = {"id": 90, "name": "SeriesSearch", "commandName": "Series Search",
                         "body": {"seriesId": 7}, "status": "started", "queued": iso()}
    fake.commands[91] = {"id": 91, "name": "MessagingCleanup", "body": {}, "status": "started"}
    # praktijk: Sonarr-ID en MediaGuard-item-ID verschillen; de link moet het juiste item openen
    ctx.db.execute("INSERT INTO lib_items(instance_id,kind,provider_id,title,monitored,"
                   "synced_at) VALUES ('sonarr-anime','series',7,'Chainsaw Man',1,'x')")
    item_id = ctx.db.execute_insert(
        "INSERT INTO lib_items(instance_id,kind,provider_id,title,monitored,synced_at) "
        "VALUES ('sonarr-series','series',7,'The Expanse',1,'x')")
    now = utcnow()
    ctx.db.execute(
        "INSERT INTO encoding_jobs(instance_id,media_kind,provider_id,source_path,trial_only,"
        "status,progress,policy_json,created_at,created_by,updated_at) VALUES "
        "('radarr-films','movie_file','5','/data/media/movies/Wadi.mkv',0,'running',0.42,'{}',?,"
        "'t',?)", (now, now))
    ctx.db.execute("INSERT INTO jobs(job_type,status,progress,created_at) VALUES "
                   "('library.sync','running',0.5,?)", (now,))
    ctx.db.execute(
        "INSERT INTO release_provenance(instance_id,event_type,download_id,release_title,"
        "quality,indexer,series_id,idempotency_key,received_at) VALUES ('sonarr-series',"
        "'Grab','d1','The.Expanse.S01E02','WEBDL-1080p','NZBgeek',7,'k1',?)", (now,))
    r = client.get("/api/v1/activity")
    assert r.status_code == 200, r.text
    data = r.json()
    dl = data["downloads"]
    assert dl[0]["title"] == "The Expanse · S01E02" and dl[0]["progress"] == 75.0
    assert dl[0]["link"] == f"/bibliotheek/sonarr-series/{item_id}" and item_id != 7
    assert any(d["warning"] and "Geen bestanden gevonden" in d["messages"] for d in dl)
    cmds = data["commands"]
    assert [c["label"] for c in cmds] == ["Serie zoeken"]  # huishoudtaken verborgen
    assert cmds[0]["target"] == "The Expanse" and cmds[0]["search"]
    assert data["encoding"]["jobs"][0]["progress"] == 42.0
    assert data["jobs"][0]["label"] == "Bibliotheek synchroniseren"
    assert data["recent"][0]["kind"] == "grab"
    assert data["recent"][0]["link"] == f"/bibliotheek/sonarr-series/{item_id}"
    s = data["summary"]
    assert (s["downloads"], s["searches"], s["av1"], s["tasks"]) == (1, 1, 1, 1)
    # de andere instanties zijn niet bereikbaar: alleen een melding, geen kapotte pagina
    assert {e["instance_id"] for e in data["errors"]} >= {"radarr-films"}
    assert s["problems"] >= 2


def test_activity_requires_login(anon_client):
    assert anon_client.get("/api/v1/activity").status_code == 401
