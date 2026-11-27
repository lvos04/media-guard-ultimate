"""Negatieve bronproeven voor de leesconnector, uitsluitend met lokale testgegevens."""

from __future__ import annotations

import secrets
import sqlite3
from contextlib import closing
from datetime import UTC, datetime, timedelta

import httpx
import pytest
from fastapi.testclient import TestClient
from test_arr_fakes import FakeArr, wire
from test_connector_lezen import assert_database_preserved, database_snapshot, key

from mediaguard.app import create_app
from mediaguard.db import dumps, utcnow
from mediaguard.instances.registry import create_instance
from mediaguard.provisioning import store
from mediaguard.provisioning.templates import owned_name
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


@pytest.mark.parametrize("veld", ["username", "password"])
def test_downloadconnector_meldt_onbeschikbaar_zonder_aanmelding(client, anon_client, ctx, veld):
    headers = key(client)
    ctx.db.execute("UPDATE instances SET suspended=1")
    store.save(ctx, {"download_clients": [{
        "name": "Proefbron", "implementation": "QBittorrent", "host": "bron.invalid",
        "port": 8080, veld: secrets.token_hex(12),
    }]}, "proef")
    calls = []

    def source(request):
        calls.append(request.method)
        return httpx.Response(200, text="Ok.") if request.method == "POST" else httpx.Response(
            200, json=[])

    ctx.services["live_download_transport"] = httpx.MockTransport(source)
    before = database_snapshot(ctx, allow_auth_timestamps=True)
    page = anon_client.get(ITEMS, params={"type": "downloads"}, headers=headers).raise_for_status()
    assert calls == []
    assert (page.json()["status"], page.json()["reason_code"], page.json()["total"]) == (
        "unknown", "capability_unavailable", None)
    assert_database_preserved(ctx, before, allow_auth_timestamps=True)
    # De normale activiteit mag daarna zelfstandig haar eigen meting uitvoeren.
    assert anon_client.get("/api/v1/activity", headers=headers).is_success
    assert calls == ["POST", "GET"]


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
    gpu = {**worker, "baan": "gpu"}
    if value is None:
        worker.pop(field)
    else:
        worker[field] = value
    ctx.services["encoder_transport"] = httpx.MockTransport(
        lambda _: httpx.Response(200, json=[worker, gpu])
    )
    page = client.get(ITEMS, params={"type": "av1_lanes"}).json()
    assert (page["status"], page["items"], page["total"]) == ("unknown", [], None)


def test_gemeten_nul_en_ongepauzeerde_baan(client, ctx):
    worker = {"baan": "cpu", "aantal": 0, "actieve_jobs": [], "gepauzeerd": False}
    ctx.services["encoder_transport"] = httpx.MockTransport(
        lambda _: httpx.Response(200, json=[worker, {**worker, "baan": "gpu"}])
    )
    page = client.get(ITEMS, params={"type": "av1_lanes"}).json()
    assert page["status"] == "ok" and page["total"] == 2
    assert {r["lane"] for r in page["items"]} == {"cpu", "gpu"}
    assert all((r["slots"], r["running"], r["paused"]) == (0, 0, False) for r in page["items"])


@pytest.mark.parametrize("lanes", [["cpu"], ["cpu", "cpu"], ["gpu", "cpu", "gpu"]])
def test_onvolledige_of_dubbele_werkermeting(client, ctx, lanes):
    workers = [
        {"baan": lane, "aantal": 0, "actieve_jobs": [], "gepauzeerd": False} for lane in lanes
    ]
    ctx.services["encoder_transport"] = httpx.MockTransport(
        lambda _: httpx.Response(200, json=workers)
    )
    page = client.get(ITEMS, params={"type": "av1_lanes"}).json()
    assert (page["status"], page["total"], page["items"]) == ("unknown", None, [])


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
    ctx.db.execute("UPDATE instances SET suspended=1")
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


@pytest.mark.parametrize(
    "fault", ["lege_configuratie", "lege_sab_id", "lege_torrent_id", "lege_lijst"]
)
def test_ongeldige_downloadbron_is_geen_nulmeting(client, ctx, fault):
    sab = fault != "lege_torrent_id"
    download_source(ctx, "Sabnzbd" if sab else "QBittorrent")
    rows = [] if fault == "lege_lijst" else [{"state": "downloading"}]
    ctx.services["live_download_transport"] = httpx.MockTransport(
        lambda _: httpx.Response(200, json={"queue": {"slots": rows}} if sab else rows)
    )
    if fault == "lege_configuratie":
        ctx.db.execute(
            "UPDATE provisioning_config SET value_json='' WHERE key=?", (store.CONFIG_KEY,)
        )
    page = client.get(ITEMS, params={"type": "downloads"}).json()
    assert (page["status"], page["total"]) == (
        ("ok", 0) if fault == "lege_lijst" else ("unknown", None)
    )


def test_enkele_download_behoudt_historisch_id_bij_beheerde_koppeling(client, ctx):
    from mediaguard.connector.items import content_hash

    download_source(ctx, "QBittorrent")
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
    torrent = "ab" * 20
    ctx.services["live_download_transport"] = httpx.MockTransport(
        lambda _: httpx.Response(
            200, json=[{"hash": torrent, "state": "downloading", "progress": 0.37}]
        )
    )
    historical_id = content_hash(["torrent", torrent.upper()])
    for linked in (False, True):
        if linked:
            arr.queue = [
                {
                    "id": 4,
                    "title": "Proef",
                    "downloadId": torrent.upper(),
                    "protocol": "torrent",
                    "downloadClient": owned_name("Proefbron"),
                    "status": "queued",
                    "statusMessages": [],
                }
            ]
        ctx.services.pop("activity_cache", None)
        ctx.services.pop("connector_items:downloads:admin", None)
        page = client.get(ITEMS, params={"type": "downloads"}).json()
        assert page["status"] == "ok" and page["total"] == 1
        assert [(r["id"], r["progress"]) for r in page["items"]] == [(historical_id, 37.0)]
        assert len(client.get("/api/v1/activity").json()["downloads"]) == 1


def test_schema_156_behoudt_de_naam_van_een_arr_download(client, ctx):
    from mediaguard.connector.items import content_hash

    download_source(ctx, "QBittorrent")
    ctx.db.execute(
        "UPDATE connector_evidence_origin SET schema_version='156_outbox_cursor_history'"
    )
    inst = create_instance(ctx, {"id": "wachtrij-proef", "app": "radarr", "media_type": "movie",
                                 "name": "Wachtrij", "url": "http://wachtrij.invalid"})
    arr = wire(ctx, inst.id, FakeArr("radarr"))
    name = owned_name("Proefbron")
    torrent = "ab" * 20
    arr.queue = [{"id": 4, "downloadId": torrent, "protocol": "torrent", "downloadClient": name,
                  "status": "queued", "statusMessages": []}]
    ctx.services["live_download_transport"] = httpx.MockTransport(
        lambda _: httpx.Response(200, json=[])
    )
    page = client.get(ITEMS, params={"type": "downloads"}).json()
    assert (page["status"], page["total"]) == ("ok", 1)
    assert page["items"][0]["id"] == content_hash(["torrent", torrent.upper(), name])


def test_twee_arr_instanties_behouden_beide_historische_download_ids(ctx):
    from mediaguard.connector.download_ids import content_hash, download_ids

    download_source(ctx, "QBittorrent")
    torrent = "ab" * 20
    first, second = [
        {"instance_id": name, "id": 7, "download_id": torrent, "protocol": "torrent"}
        for name in ("a", "b")
    ]
    historical = content_hash(["torrent", torrent.upper()])
    second_id = content_hash([historical, "b", 7, "Proefbron"])
    for rows, expected in (([first, second], [historical, second_id]),
                           ([second], [second_id]), ([first, second], [historical, second_id])):
        assert download_ids(ctx, rows, datetime.now(UTC)) == expected


@pytest.mark.parametrize("controle", ["samenloop", "overname", "herstart", "inrichting"])
def test_download_id_blijft_bij_dezelfde_client(client, ctx, settings, controle):
    headers = key(client)
    ctx.db.execute("UPDATE instances SET suspended=1")
    clients = [
        {"name": name, "implementation": "QBittorrent", "host": name + ".invalid", "port": 8080}
        for name in ("eerste", "tweede")
    ]
    store.save(ctx, {"download_clients": clients[:1] if controle == "inrichting" else clients},
               "proef")

    def measure(application, reader, visible):
        context = application.state.ctx

        def queue(request):
            name = request.url.host.split(".")[0]
            assert request.method == "GET" and request.url.path == "/api/v2/torrents/info"
            return httpx.Response(200, json=[{
                "hash": "ab" * 20, "state": "downloading",
                "progress": 0.37 if name == "eerste" else 0.14,
            }] if name in visible else [])

        context.services["live_download_transport"] = httpx.MockTransport(queue)
        context.services.pop("activity_cache", None)
        context.services.pop("connector_items:downloads:viewer", None)
        before = database_snapshot(context, allow_auth_timestamps=True)
        page = reader.get(ITEMS, params={"type": "downloads"}, headers=headers).raise_for_status()
        assert_database_preserved(context, before, allow_auth_timestamps=True)
        data = page.json()
        assert (data["status"], data["total"]) == ("ok", len(visible))
        ids = {row["progress"]: row["id"] for row in data["items"]}
        assert len(set(ids.values())) == len(visible)
        return ids

    app = create_app(settings)
    with closing(app.state.ctx.db), closing(TestClient(app)) as reader:
        first = measure(app, reader, {"eerste"})
        if controle == "inrichting":
            store.save(ctx, {"download_clients": clients[::-1]}, "proef")
        both = measure(app, reader, {"eerste", "tweede"})
        assert measure(app, reader, {"eerste"}) == first
    if controle == "inrichting":
        store.save(ctx, {"download_clients": clients[1:]}, "proef")
    restarted = create_app(settings)
    with closing(restarted.state.ctx.db), closing(TestClient(restarted)) as reader:
        second = measure(restarted, reader, {"tweede"})
    if controle == "samenloop":
        assert first[37] == both[37]
    elif controle == "overname":
        assert first[37] != second[14]
    elif controle == "herstart":
        assert both[14] == second[14]
    else:
        assert first[37] == both[37] and both[14] == second[14]


@pytest.mark.parametrize("rows", [0, 2])
def test_wanted_totaal_heeft_de_meettijd_van_de_hele_dataset(client, ctx, rows):
    ctx.db.execute("UPDATE instances SET suspended=1")
    inst = create_instance(
        ctx,
        {
            "id": "wanted-proef",
            "app": "radarr",
            "media_type": "movie",
            "name": "Wanted",
            "url": "http://bron.invalid",
        },
    )
    now = utcnow()
    old = (datetime.now(UTC) - timedelta(hours=2)).isoformat(timespec="seconds")

    def read(cursor=None):
        params = {"type": "wanted", "limit": 1}
        if cursor:
            params["cursor"] = cursor
        return client.get(ITEMS, params=params).json()

    def sync(when, status):
        ctx.db.execute(
            "INSERT INTO library_sync_runs(instance_id,started_at,finished_at,status,counts_json) "
            "VALUES (?,?,?,?,?)",
            (inst.id, when, when, status, dumps({"wanted": {"open": rows}})),
        )

    if rows:
        for provider, when in enumerate((now, old), start=1):
            ctx.db.execute(
                "INSERT INTO wanted_items(instance_id,kind,provider_id,item_provider_id,"
                "reason,title,first_seen_at,updated_at) "
                "VALUES (?,'movie',?,?,'missing','Proef',?,?)",
                (inst.id, provider, provider, when, when),
            )
    else:
        assert read()["status"] == "unknown"
        sync(old, "succeeded")
        sync(now, "failed")
    page = read()
    assert (page["status"], page["total"], page["reason_code"]) == (
        "unknown",
        None,
        "meting_verouderd",
    )
    assert page["observed_at"] == old.replace("+00:00", "Z")
    if rows:
        assert len(page["items"]) == 1
        following = read(page["next_cursor"])
        assert (following["status"], following["total"], following["items"]) == (
            "unknown",
            None,
            [],
        )
    sync(now, "succeeded")
    fresh = read()
    assert (fresh["status"], fresh["total"], fresh["observed_at"]) == (
        "ok",
        rows,
        now.replace("+00:00", "Z"),
    )
