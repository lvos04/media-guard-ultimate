"""Negatieve bronproeven voor de leesconnector, uitsluitend met lokale testgegevens."""

from __future__ import annotations

import secrets
import sqlite3
from datetime import UTC, datetime, timedelta

import httpx
import pytest
from test_arr_fakes import FakeArr, wire
from test_connector_lezen import assert_database_preserved, database_snapshot, key

from mediaguard.db import dumps, utcnow
from mediaguard.instances.registry import create_instance
from mediaguard.provisioning import store
from mediaguard.storage_forecast import POOL_STATE, record_pool

ITEMS = "/connector/v1/items"


def download_source(ctx, implementation):
    ctx.db.execute("UPDATE instances SET suspended=1")
    value = secrets.token_hex(18)
    store.save(
        ctx,
        {
            "download_clients": [
                {
                    "name": "Proefbron",
                    "implementation": implementation,
                    "host": "bron.invalid",
                    "port": 8080,
                    "api_key": value,
                }
            ]
        },
        "proef",
    )
    return value


def test_downloadmeting_wijzigt_geen_inrichting(client, anon_client, ctx):
    headers = key(client)
    ctx.db.execute("UPDATE instances SET suspended=1")
    ctx.db.execute(
        "INSERT OR REPLACE INTO provisioning_config VALUES (?,?,?,?)",
        (store.CONFIG_KEY, dumps({"media": {"propers": "preferAndUpgrade"}}), utcnow(), "proef"),
    )
    before = database_snapshot(ctx, allow_auth_timestamps=True)
    response = anon_client.get(ITEMS, params={"type": "downloads"}, headers=headers)
    assert response.is_success and response.json()["status"] == "ok"
    assert_database_preserved(ctx, before, allow_auth_timestamps=True)


@pytest.mark.parametrize("fault", ["fouttekst", "http_status"])
def test_downloadfout_verbergt_brongegevens(client, anon_client, ctx, caplog, fault):
    headers = key(client)
    value = download_source(ctx, "Sabnzbd")
    calls = []

    def source(request):
        calls.append(request.method)
        assert request.url.params["apikey"] == value
        if fault == "http_status":
            return httpx.Response(503)
        return httpx.Response(200, json={"error": value})

    ctx.services["live_download_transport"] = httpx.MockTransport(source)
    caplog.set_level("INFO")
    response = anon_client.get(ITEMS, params={"type": "downloads"}, headers=headers)
    activity = anon_client.get("/api/v1/activity", headers=headers)
    assert response.is_success and response.json()["status"] == "unknown"
    assert activity.is_success and activity.json()["errors"] and calls == ["GET"]
    assert all(value not in part for part in (response.text, activity.text, caplog.text))
    assert activity.json()["errors"][0]["code"] == "bron_onbereikbaar"


@pytest.mark.parametrize("fault", ["onleesbaar", "niet_ondersteund"])
def test_onbeschikbare_downloadbron_is_onbekend(client, ctx, monkeypatch, fault):
    download_source(ctx, "Transmission" if fault == "niet_ondersteund" else "Sabnzbd")
    fetch = ctx.db.fetch_one

    def unavailable(sql, *args):
        if fault == "onleesbaar" and "FROM provisioning_config" in sql:
            raise sqlite3.OperationalError("database is locked")
        return fetch(sql, *args)

    monkeypatch.setattr(ctx.db, "fetch_one", unavailable)
    ctx.services["live_download_transport"] = httpx.MockTransport(lambda _: httpx.Response(503))
    response = client.get(ITEMS, params={"type": "downloads"})
    assert response.is_success
    page = response.json()
    assert (page["status"], page["total"], page["items"]) == ("unknown", None, [])


@pytest.mark.parametrize(
    ("field", "value"),
    [
        ("aantal", None),
        ("aantal", -1),
        ("aantal", True),
        ("aantal", "2"),
        ("actieve_jobs", None),
        ("actieve_jobs", {}),
        ("actieve_jobs", ["onbekend"]),
        ("gepauzeerd", None),
        ("gepauzeerd", "nee"),
    ],
)
def test_baan_zonder_geldige_meting_is_onbekend(client, ctx, field, value):
    worker = {"baan": "cpu", "aantal": 0, "actieve_jobs": [], "gepauzeerd": False}
    if value is None:
        worker.pop(field)
    else:
        worker[field] = value
    ctx.services["encoder_transport"] = httpx.MockTransport(
        lambda _: httpx.Response(200, json=[worker])
    )
    page = client.get(ITEMS, params={"type": "av1_lanes"}).json()
    assert (page["status"], page["items"], page["total"]) == ("unknown", [], None)


def test_gemeten_nul_en_ongepauzeerde_baan(client, ctx):
    worker = {"baan": "cpu", "aantal": 0, "actieve_jobs": [], "gepauzeerd": False}
    ctx.services["encoder_transport"] = httpx.MockTransport(
        lambda _: httpx.Response(200, json=[worker])
    )
    page = client.get(ITEMS, params={"type": "av1_lanes"}).json()
    (row,) = page["items"]
    assert page["status"] == "ok" and (row["slots"], row["running"], row["paused"]) == (0, 0, False)


@pytest.mark.parametrize("failed_probe", [False, True])
def test_poolincident_houdt_laatste_succesvolle_meettijd(client, ctx, failed_probe):
    old = (datetime.now(UTC) - timedelta(hours=2)).isoformat(timespec="seconds")
    ctx.state_set(
        POOL_STATE,
        {
            "reachable": True,
            "checked_at": old,
            "degraded": [{"name": "proef", "status": "DEGRADED"}],
        },
    )
    if failed_probe:
        record_pool(ctx, {"error_nl": "bron onbereikbaar"})
    ctx.services["encoder_transport"] = httpx.MockTransport(lambda _: httpx.Response(200, json={}))
    page = client.get(ITEMS, params={"type": "incidents"}).json()
    assert page["status"] == "unknown" and page["reason_code"] == "meting_verouderd"
    assert page["observed_at"] == old.replace("+00:00", "Z") and page["total"] is None
    for item in page["items"]:
        if item["cause_code"] == "storage.pool_degraded":
            assert item["observed_at"] == page["observed_at"]


@pytest.mark.parametrize("source", ["oud", "onleesbaar", "opnieuw_gemeten", "mislukte_sync"])
def test_wanted_gebruikt_werkelijke_bronmeting(client, ctx, source):
    inst = create_instance(
        ctx,
        {
            "id": "wanted-proef",
            "app": "radarr",
            "media_type": "movie",
            "name": "Proefbron",
            "url": "http://bron.invalid",
        },
    )
    old = (datetime.now(UTC) - timedelta(hours=2)).isoformat(timespec="seconds")
    updated = "onleesbaar" if source == "onleesbaar" else old
    ctx.db.execute(
        "INSERT INTO wanted_items(instance_id,kind,provider_id,item_provider_id,"
        "reason,title,first_seen_at,updated_at) VALUES (?,'movie',1,1,'missing','Proef',?,?)",
        (inst.id, old, updated),
    )
    now = utcnow()
    if source in ("opnieuw_gemeten", "mislukte_sync"):
        ctx.db.execute(
            "INSERT INTO library_sync_runs(instance_id,started_at,finished_at,status,counts_json) "
            "VALUES (?,?,?,?,?)",
            (
                inst.id,
                now,
                now,
                "succeeded" if source == "opnieuw_gemeten" else "failed",
                dumps({"wanted": {"open": 1}}),
            ),
        )
    page = client.get(ITEMS, params={"type": "wanted"}).json()
    if source == "opnieuw_gemeten":
        assert page["status"] == "ok" and page["items"][0]["observed_at"] == now.replace(
            "+00:00", "Z"
        )
    else:
        assert page["status"] == "unknown" and page["total"] is None
        if source == "onleesbaar":
            assert page["reason_code"] == "meting_ontbreekt" and page["observed_at"] is None
        else:
            assert page["reason_code"] == "meting_verouderd" and page["observed_at"] == old.replace(
                "+00:00", "Z"
            )


def test_gelijke_download_ids_blijven_bij_hun_client(client, ctx):
    ctx.db.execute("UPDATE instances SET suspended=1")
    inst = create_instance(
        ctx,
        {
            "id": "wachtrij-proef",
            "app": "radarr",
            "media_type": "movie",
            "name": "Wachtrij",
            "url": "http://wachtrij.invalid",
        },
    )
    arr = wire(ctx, inst.id, FakeArr("radarr"))
    clients = [
        {"name": name, "implementation": "QBittorrent", "host": name + ".invalid", "port": 8080}
        for name in ("eerste", "tweede")
    ]
    store.save(ctx, {"download_clients": clients}, "proef")
    transferring = ["eerste"]

    def source(request):
        name = request.url.host.split(".")[0]
        return httpx.Response(
            200,
            json=[
                {
                    "hash": "ab" * 20,
                    "name": name,
                    "progress": 0.1 if name == "eerste" else 0.2,
                    "state": "downloading" if name in transferring else "pausedDL",
                }
            ],
        )

    ctx.services["live_download_transport"] = httpx.MockTransport(source)

    def ids():
        ctx.services.pop("activity_cache", None)
        ctx.services.pop("connector_items:downloads:admin", None)
        page = client.get(ITEMS, params={"type": "downloads"}).json()
        assert page["status"] == "ok" and page["total"] == 2
        return {row["progress"]: row["id"] for row in page["items"]}

    original = ids()
    assert len(set(original.values())) == 2
    transferring[:] = ["tweede"]
    assert ids() == original
    arr.queue = [
        {
            "id": 4,
            "title": "Proef",
            "downloadId": "AB" * 20,
            "protocol": "torrent",
            "downloadClient": "eerste",
            "status": "queued",
            "statusMessages": [],
        }
    ]
    assert ids() == original
