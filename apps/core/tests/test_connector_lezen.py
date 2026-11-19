"""Connector deel 1: alleen lezen, contractfouten en werkelijke meettijden."""

from __future__ import annotations

import copy
import json
import secrets
import sqlite3
import time
from collections import Counter
from contextlib import closing
from datetime import UTC, datetime, timedelta
from pathlib import Path
from uuid import UUID

import httpx
import pytest
from fastapi.testclient import TestClient

from mediaguard import health_summary
from mediaguard.app import create_app
from mediaguard.auth import Principal, current_principal
from mediaguard.db import utcnow

# Alleen de bestaande aanmeldadministratie mag wijzigen: gebruikstijdstippen in
# bestaande rijen en uitsluitend nieuwe auditregels voor geweigerde sleutels.
AUTH_TIMESTAMP_COLUMNS = {"api_keys": "last_used_at", "sessions": "last_seen_at"}
AUTH_NEW_AUDIT_ACTION = "apikey.rejected"


@pytest.fixture
def machine(app):
    with TestClient(app) as c:
        yield c


def key(client, **extra):
    r = client.post("/api/v1/auth/api-keys", json={"name": "hub", "role": "viewer", **extra})
    assert r.status_code == 200
    return {"X-Api-Key": r.json()["key"]}


def error(response, status, code):
    assert response.status_code == status, response.text
    body = response.json()
    assert set(body) == {"code", "message", "details", "trace_id"}
    assert body["code"] == code and body["message"]
    UUID(body["trace_id"])
    return body


@pytest.fixture
def measurement(ctx):
    now = utcnow()
    value = {
        "generated_at": now,
        "complete": True,
        "active": {"tasks": 0},
        "errors": {"total": 0, "tasks_failed": 0},
        "warnings": {"total": 0},
        "encoder": {"state": "ok"},
        "instances": {"unreachable": []},
    }
    ctx.services["health_summary_cache"] = (time.monotonic(), value)
    ctx.services["health_summary_observations"] = (value, {"encoder": now, "instances": now})
    ctx.state_set("taskwatch:jobs", {"last_ok_at": now, "fails": 0})
    return value


@pytest.mark.parametrize(
    "path,method",
    [
        ("discover", "get"),
        ("health", "get"),
        ("events", "get"),
        ("items?type=status", "get"),
        ("evidence/audit:1", "get"),
        ("propose", "post"),
        ("apply", "post"),
        ("rollback", "post"),
    ],
)
def test_every_connector_route_requires_auth(machine, path, method):
    error(getattr(machine, method)("/connector/v1/" + path), 401, "unauthorized")


def test_discover_advertises_only_existing_read_access(client, machine):
    headers = key(client, scopes=[])
    r = machine.get("/connector/v1/discover", headers=headers)
    assert r.status_code == 200
    d = r.json()
    assert d["app_id"] == "media-guard-ultimate" and d["source_version"]
    assert d["schema_version"] == "1.0" and d["capabilities"] == ["read"]
    assert d["permissions"] == ["viewer"]
    assert d["actions"] == []
    assert d["item_types"] == ["wanted", "status", "downloads", "av1_lanes", "incidents"]
    assert all(d["limits"]["max_age_s"][name] == 900 for name in d["item_types"])
    assert d["limits"]["max_age_s"]["health"] == 900
    assert d["timestamp_utc"].endswith("Z")


@pytest.mark.parametrize("path", ["propose", "apply", "rollback"])
def test_unavailable_actions_and_scoped_denial(client, machine, path):
    headers = key(client)
    error(machine.post("/connector/v1/" + path, headers=headers), 501, "capability_unavailable")
    scoped = key(client, name="beperkt", scopes=["emergency_stop:on"])
    error(machine.post("/connector/v1/" + path, headers=scoped), 403, "scope_ontbreekt")
    # Bestaande sessiecontrole en CSRF blijven gelden.
    error(client.post("/connector/v1/" + path), 501, "capability_unavailable")
    error(client.post("/connector/v1/" + path, headers={"X-CSRF-Token": "fout"}), 403, "forbidden")


def test_invalid_key_never_falls_back_to_valid_session(client):
    error(
        client.get("/connector/v1/discover", headers={"X-Api-Key": "ongeldig"}), 401, "unauthorized"
    )


def test_revoked_and_wrong_role(client, machine, ctx, app):
    headers = key(client)
    ctx.db.execute("UPDATE api_keys SET revoked_at=?", (utcnow(),))
    error(machine.get("/connector/v1/discover", headers=headers), 401, "unauthorized")
    app.dependency_overrides[current_principal] = lambda: Principal("test", "onbekend")
    error(client.get("/connector/v1/discover"), 403, "forbidden")


def test_health_reuses_cache_without_mutation(client, machine, measurement, ctx, monkeypatch):
    async def forbidden(*args):
        pytest.fail("Tweede meting gestart ondanks bestaande cache")

    monkeypatch.setattr(health_summary, "build", forbidden)
    before = copy.deepcopy(measurement)
    headers = key(client)
    a = machine.get("/connector/v1/health", headers=headers)
    b = machine.get("/connector/v1/health", headers=headers)
    assert a.status_code == b.status_code == 200
    data = a.json()
    assert data == b.json() and data["status"] == "healthy"
    assert {"database", "worker", "jobs", "encoder", "instances"} <= {
        c["name"] for c in data["checks"]
    }
    assert measurement == before
    assert all(c["observed_at"].endswith("Z") for c in data["checks"])


@pytest.mark.parametrize(
    "bad,expected", [("ok", "unknown"), ("degraded", "degraded"), ("offline", "down")]
)
def test_stale_check_and_worst_status(client, machine, measurement, ctx, bad, expected):
    old = (datetime.now(UTC) - timedelta(seconds=1000)).isoformat()
    ctx.services["health_summary_observations"][1]["instances"] = old
    measurement["encoder"]["state"] = bad
    d = machine.get("/connector/v1/health", headers=key(client)).json()
    assert d["status"] == expected
    check = next(c for c in d["checks"] if c["name"] == "instances")
    assert check["status"] == "unknown" and check["observed_at"] == old.replace("+00:00", "Z")
    assert check["detail"]["reason_code"] == "meting_verouderd"
    assert d["observed_at"] == check["observed_at"]
    if expected == "unknown":
        assert d["reason_code"] == "meting_verouderd"


def test_old_summary_and_missing_heartbeat_never_healthy(client, machine, measurement, ctx):
    measurement["generated_at"] = (datetime.now(UTC) - timedelta(days=1)).isoformat()
    ctx.db.execute("DELETE FROM system_state WHERE key='taskwatch:jobs'")
    d = machine.get("/connector/v1/health", headers=key(client)).json()
    assert d["status"] == "unknown"
    assert next(c for c in d["checks"] if c["name"] == "worker")["status"] == "unknown"
    assert next(c for c in d["checks"] if c["name"] == "jobs")["status"] == "unknown"


@pytest.mark.parametrize("path", ["/healthz", "/readyz"])
def test_public_health_has_no_information(machine, path):
    r = machine.get(path, headers={"X-Api-Key": "ongeldig"})
    assert r.status_code == 200 and r.json() == {"status": "ok"}
    assert r.headers["cache-control"] == "no-store"


def test_readiness_pending_migration_and_database_failure(machine, ctx, monkeypatch):
    assert "retry-after" not in machine.get("/readyz").headers
    ctx.db.execute("DELETE FROM schema_migrations WHERE version='005_api_key_scopes'")
    r = machine.get("/readyz")
    assert r.status_code == 503 and r.json() == {"status": "unavailable"}
    assert r.headers["retry-after"] == "5"

    def broken(*args, **kwargs):
        raise sqlite3.OperationalError("privaat pad met instelling")

    monkeypatch.setattr(ctx.db, "fetch_all", broken)
    r = machine.get("/readyz")
    assert r.status_code == 503 and r.json() == {"status": "unavailable"}
    assert r.headers["retry-after"] == "5"
    assert machine.get("/healthz").json() == {"status": "ok"}


def test_legacy_error_and_container_health_unchanged(machine):
    r = machine.get("/api/v1/status")
    assert r.status_code == 401 and r.json() == {"detail": "Niet ingelogd"}
    assert set(machine.get("/api/v1/health").json()) == {"status", "version"}


@pytest.mark.parametrize(
    "path", ["/connector/v1/health", "/connector/v1/health/", "/connector/v1/niet-bestaand",
             "/connector/v1/items", "/connector/v1/evidence/audit:1", "/healthz", "/readyz"]
)
def test_body_limit_before_auth_with_and_without_content_length(machine, path):
    payload = b"x" * (1048576 + 1)
    r = machine.post(path, content=payload)
    assert error(r, 413, "payload_too_large")["details"]["max_bytes"] == 1048576
    r = machine.post(path, content=iter([payload[:600000], payload[600000:]]))
    error(r, 413, "payload_too_large")


def test_unknown_connector_errors_are_contract_errors(machine):
    error(machine.get("/connector/v1/niet-bestaand"), 404, "not_found")
    error(machine.delete("/connector/v1/discover"), 405, "method_not_allowed")


@pytest.mark.parametrize(("method", "suffix"), [
    ("GET", ""), ("GET", "discover/"), ("GET", "health/"),
    ("GET", "items/"), ("GET", "evidence/job:1/"),
    ("POST", "propose/"), ("POST", "apply/"), ("POST", "rollback/"),
])
def test_connector_trailing_slash_returns_contract_404(machine, method, suffix):
    response = machine.request(method, f"/connector/v1/{suffix}", follow_redirects=False)
    assert "location" not in response.headers
    error(response, 404, "not_found")


def test_legacy_trailing_slash_preserves_redirect(machine):
    response = machine.get("/api/v1/status/", follow_redirects=False)
    assert response.status_code == 307
    assert response.headers["location"] == str(machine.base_url) + "/api/v1/status"


def test_connector_openapi_documents_only_current_routes(app):
    paths = app.openapi()["paths"]
    assert {p for p in paths if p.startswith("/connector/v1/")} == {
        "/connector/v1/discover", "/connector/v1/health", "/connector/v1/propose",
        "/connector/v1/apply", "/connector/v1/rollback", "/connector/v1/events",
        "/connector/v1/items", "/connector/v1/evidence/{id}",
    }
    for action in ("propose", "apply", "rollback"):
        assert "501" in paths[f"/connector/v1/{action}"]["post"]["responses"]
    for path in ("/healthz", "/readyz"):
        assert paths[path]["get"]["security"] == []
    events = paths["/connector/v1/events"]["get"]
    params = {p["name"]: p["schema"] for p in events["parameters"]}
    assert params["limit"]["type"] == "integer" and params["limit"]["maximum"] == 200
    assert {"200", "401", "409", "422"} <= set(events["responses"])
    for status in ("401", "409", "422"):
        schema = events["responses"][status]["content"]["application/json"]["schema"]
        assert schema["$ref"].endswith("/ConnectorError")
    assert events["responses"]["200"]["content"]["application/json"]["schema"]["$ref"].endswith(
        "/EventPage"
    )

    items = paths["/connector/v1/items"]["get"]
    assert "ETag" in items["responses"]["200"]["headers"]
    params = {p["name"]: p["schema"] for p in items["parameters"]}
    assert params["type"]["enum"] == ["wanted", "status", "downloads", "av1_lanes", "incidents"]
    evidence = paths["/connector/v1/evidence/{id}"]["get"]
    for endpoint, statuses in ((items, ("401", "403", "409", "422", "503")),
                               (evidence, ("401", "403", "404", "422", "503"))):
        assert endpoint["responses"]["503"]["headers"]["Retry-After"]["schema"]["minimum"] == 1
        for status in statuses:
            schema = endpoint["responses"][status]["content"]["application/json"]["schema"]
            assert schema["$ref"].endswith("/ConnectorError")
    app_version = app.openapi()["components"]["schemas"]["EvidencePackage"]["properties"][
        "app_version"
    ]
    assert {option["type"] for option in app_version["anyOf"]} == {"string", "null"}
    assert '"onbekend" in details.app_version' in app_version["description"]


def database_snapshot(ctx, *, allow_auth_timestamps=False):
    snapshot = {}
    for table in ctx.db.fetch_all("SELECT name, sql FROM sqlite_schema WHERE type='table'"):
        name = table["name"]
        quoted_name = '"' + name.replace('"', '""') + '"'
        ignored = AUTH_TIMESTAMP_COLUMNS.get(name) if allow_auth_timestamps else None
        snapshot[name, table["sql"]] = Counter(
            tuple((column, value) for column, value in row.items() if column != ignored)
            for row in ctx.db.fetch_dicts(f"SELECT * FROM {quoted_name}")
        )
    return snapshot


def assert_database_preserved(ctx, before, *, allow_auth_timestamps=False,
                              allow_rejection_audit=False):
    after = database_snapshot(ctx, allow_auth_timestamps=allow_auth_timestamps)
    assert before.keys() == after.keys(), "Tabellen of hun schema gewijzigd"
    changed = []
    for table, old in before.items():
        new = after[table]
        if allow_rejection_audit and table[0] == "audit_events":
            for row, count in (new - old).items():
                if dict(row)["action"] == AUTH_NEW_AUDIT_ACTION:
                    new[row] -= count
        if new != old:
            changed.append(table[0])
    assert not changed, f"Onverwachte databasewijzigingen in {', '.join(changed)}"


@pytest.fixture
def populated_installation(settings):
    fixtures = Path(__file__).resolve().parents[3] / "tests" / "fixtures" / "aio"
    settings.db_path.parent.mkdir(parents=True, exist_ok=True)
    with closing(sqlite3.connect(settings.db_path)) as db:
        db.executescript((fixtures / "core-1.2.2.sql").read_text(encoding="utf-8"))
    meta = json.loads((fixtures / "core-1.2.2.json").read_text(encoding="utf-8"))

    app = create_app(settings)
    ctx = app.state.ctx
    with closing(ctx.db), TestClient(app) as client, TestClient(app) as machine:
        response = client.post("/api/v1/auth/login",
                               json={"username": meta["user"], "password": meta["password"]})
        assert response.status_code == 200
        client.headers["X-CSRF-Token"] = response.json()["csrf_token"]

        snapshot = database_snapshot(ctx)
        filled = {name for (name, _), rows in snapshot.items() if rows}
        empty = {name for name, _ in snapshot} - filled
        # De releasefixture zelf bevat 105 gevulde tabellen vóór migratie en aanmelding.
        assert len(filled) >= 105, f"Slechts {len(filled)} tabellen gevuld, leeg: {sorted(empty)}"
        progress_tables = {
            "jobs", "job_logs", "wanted_items", "quarantine_items", "encoding_jobs",
            "optimization_plans", "replacement_transactions", "mediaserver_pending",
            "mediaserver_watch", "migration_runs", "outbox_events", "lib_items", "lib_episodes",
            "lib_files", "delivery_items", "backfill_runs", "settings_values", "system_state",
        }
        missing = progress_tables - filled
        assert not missing, f"Ongevulde of ontbrekende voortgangstabellen: {sorted(missing)}"

        # De eerste gezondheidsmeting gebruikt een lokale encoderrespons.
        ctx.services["encoder_transport"] = httpx.MockTransport(
            lambda _: httpx.Response(200, json={})
        )
        yield ctx, client, machine


@pytest.mark.parametrize("herkomst", ["release", "opvolger", "156", "156_arr_naam"])
def test_arr_download_behoudt_id_bij_wisselende_clientwachtrij(populated_installation, herkomst):
    from test_arr_fakes import FakeArr, wire

    from mediaguard.connector.items import content_hash
    from mediaguard.instances.registry import create_instance
    from mediaguard.provisioning import store
    from mediaguard.provisioning.templates import owned_name

    ctx, admin, viewer = populated_installation
    headers = key(admin)
    ctx.db.execute("UPDATE instances SET suspended=1")
    client = {"name": "Proefbron", "implementation": "QBittorrent",
              "host": "client.invalid", "port": 8080}
    if herkomst == "opvolger":
        store.save(ctx, {"download_clients": [dict(client, name="Eerdere")]}, "proef")
    if herkomst.startswith("156"):
        ctx.db.execute(
            "UPDATE connector_evidence_origin SET schema_version='156_outbox_cursor_history'"
        )
    store.save(ctx, {"download_clients": [client]}, "proef")
    inst = create_instance(ctx, {"id": "proefwachtrij", "app": "radarr", "media_type": "movie",
                                 "url": "http://wachtrij.invalid"})
    arr = wire(ctx, inst.id, FakeArr("radarr"))
    torrent = "ab" * 20
    arr.queue = [{"id": 7, "title": "Proef", "downloadId": torrent, "protocol": "torrent",
                  "status": "downloading", "size": 100, "sizeleft": 63}]
    arr_name = owned_name(client["name"]) if herkomst == "156_arr_naam" else None
    if arr_name:
        arr.queue[0]["downloadClient"] = arr_name
    source = ["torrent", torrent.upper()]
    if herkomst != "release":
        source.append(arr_name or client["name"])
    for visible in (False, True, False):
        def torrents(request, present=visible):
            assert request.method == "GET" and request.url.path == "/api/v2/torrents/info"
            return httpx.Response(200, json=[
                {"hash": torrent, "state": "downloading", "progress": 0.37}
            ] if present else [])

        ctx.services["live_download_transport"] = httpx.MockTransport(torrents)
        ctx.services.pop("activity_cache", None)
        ctx.services.pop("connector_items:downloads:viewer", None)
        activity = viewer.get("/api/v1/activity", headers=headers).raise_for_status().json()
        row, = activity["downloads"]
        assert (row["instance_id"], row["id"], row["download_id"].upper(), row["progress"]) == (
            inst.id, 7, torrent.upper(), 37.0,
        )
        if not arr_name:
            assert row["client"] == (client["name"] if visible else None)
        before = database_snapshot(ctx, allow_auth_timestamps=True)
        page = viewer.get("/connector/v1/items?type=downloads", headers=headers).raise_for_status()
        assert_database_preserved(ctx, before, allow_auth_timestamps=True)
        assert (page.json()["status"], page.json()["total"]) == ("ok", 1)
        item, = page.json()["items"]
        assert (item["id"], item["progress"]) == (content_hash(source), 37.0)


@pytest.mark.parametrize("foutplek", ["id_toewijzing", "audit"])
def test_geweigerde_inrichting_behoudt_sleutel_en_voortgang(
    populated_installation, settings, monkeypatch, foutplek,
):
    from mediaguard.provisioning import api, store

    ctx, admin, viewer = populated_installation
    headers = key(admin)
    ctx.db.execute("UPDATE instances SET suspended=1")
    path = "/api/v1/provisioning/config"
    first = {"name": "Wachtrij", "host": "wachtrij.invalid", "port": 8080,
             "implementation": "Sabnzbd"}
    second = {"name": "Tweede", "host": "tweede.invalid", "port": 8080,
              "implementation": "QBittorrent"}
    value = secrets.token_hex(18)
    admin.put(path, json={"download_clients": [dict(first, api_key=value)]}).raise_for_status()

    def queue(request):
        assert request.method == "GET"
        if request.url.host == second["host"]:
            return httpx.Response(200, json=[])
        assert request.url.host == first["host"] and request.url.params["apikey"] == value
        return httpx.Response(200, json={"queue": {"slots": [
            {"nzo_id": "proefrij", "mb": 100, "mbleft": 63,
             "percentage": 37, "status": "Downloading"}
        ]}})

    def downloads(reader):
        page = reader.get("/connector/v1/items?type=downloads", headers=headers).raise_for_status()
        assert page.json()["status"] == "ok"
        return [(item["id"], item["progress"]) for item in page.json()["items"]]

    ctx.services["live_download_transport"] = httpx.MockTransport(queue)
    original = downloads(viewer)
    assert len(original) == 1 and original[0][1] == 37.0
    before = database_snapshot(ctx, allow_auth_timestamps=True)
    change = {"download_clients": [dict(first, enabled=False, api_key=""), second]}
    if foutplek == "audit":
        # Ook de in dezelfde PUT gewijzigde koppeling moet samen met het auditbewijs terugrollen.
        change["seerr_connection"] = {"url": "http://koppeling.invalid", "api_key": value}
        ctx.services["seerr_transport"] = httpx.MockTransport(
            lambda _: httpx.Response(200, json={}))
    state_set = ctx.state_set

    def refuse(*args, **kwargs):
        raise sqlite3.OperationalError("Proefopslag vol")

    def refuse_owners(name, data):
        if name == store.DOWNLOAD_ID_OWNERS:
            refuse()
        state_set(name, data)

    with monkeypatch.context() as patch, closing(
        TestClient(admin.app, raise_server_exceptions=False)
    ) as writer:
        if foutplek == "id_toewijzing":
            patch.setattr(ctx, "state_set", refuse_owners)
        else:
            patch.setattr(api, "audit", refuse)
        writer.cookies.update(admin.cookies)
        writer.headers.update(admin.headers)
        response = writer.put(path, json=change)
    assert response.status_code == 503 and value not in response.text
    assert int(response.headers["Retry-After"]) > 0
    assert_database_preserved(ctx, before, allow_auth_timestamps=True)
    app = create_app(settings)
    with closing(app.state.ctx.db), closing(TestClient(app)) as reader:
        app.state.ctx.services["live_download_transport"] = httpx.MockTransport(queue)
        config = store.public_view(app.state.ctx)
        saved, = config["download_clients"]
        assert saved["name"] == first["name"] and saved["enabled"] and saved["api_key_set"]
        assert downloads(reader) == original
    result = admin.put(path, json=change).raise_for_status().json()["download_clients"]
    assert [(c["name"], c["enabled"], c["api_key_set"]) for c in result] == [
        (first["name"], False, False), (second["name"], True, False),
    ]


@pytest.mark.parametrize(("schema", "metingen"), [
    # Per meting: inrichting, Arr-rij en zichtbare clients. None = geen Arr-rij,
    # "" = naamloos op 37%, "zonder meting" = naamloos zonder eigen voortgang.
    pytest.param(100, [("eerste", None, ("eerste",))] * 2, id="basis"),
    pytest.param(100, [("eerste", "", ()), ("eerste", "", ("eerste",)),
                       ("eerste", "", ())], id="wachtrij"),
    pytest.param(100, [("eerste", None, ("eerste",)), ("geweigerd", None, ("eerste",))],
                 id="opslag"),
    pytest.param(100, [("eerste", "", ()), ("reserve-uit", "", ()), ("eerste", "", ())],
                 id="reserveclient"),
    pytest.param(100, [("eerste", "", ()), ("beide", "", ()), ("tweede", "", ())],
                 id="andere-inrichting"),
    pytest.param(156, [("eerste", None, ("eerste",)), ("eerste", "eerste", ("eerste",)),
                       ("eerste", None, ("eerste",))], id="arrkoppeling156"),
    pytest.param(156, [("eerste", "eerste", ()), ("eerste", None, ("eerste",)),
                       ("reserve-uit", "eerste", ())], id="arrkoppeling156-omgekeerd"),
    pytest.param(100, [("eerste", None, ("eerste",)), ("beide", None, ("eerste", "tweede")),
                       ("tweede", None, ("tweede",))], id="clientwissel"),
    pytest.param(156, [("beide", None, ("eerste",)), ("beide", "eerste", ("eerste", "tweede")),
                       ("beide", None, ("tweede",))], id="samenloop156"),
    pytest.param(156, [("eerste", None, ("eerste",)), ("hernoemd", None, ("eerste",))],
                 id="clientnaam"),
    pytest.param(156, [("beide", None, ("eerste", "tweede")),
                       ("beide", "zonder meting", ("eerste", "tweede")),
                       ("beide", "zonder meting", ("eerste",))], id="dubbelmelding"),
    pytest.param(156, [("eerste", "zonder meting", ("eerste",)),
                       ("beide", "zonder meting", ("eerste", "tweede")),
                       ("beide", None, ("eerste", "tweede"))], id="dubbelmelding-omgekeerd"),
])
def test_uitgegeven_download_id(populated_installation, settings, monkeypatch, schema, metingen):
    from test_arr_fakes import FakeArr, wire

    from mediaguard.connector.items import content_hash
    from mediaguard.instances.registry import create_instance
    from mediaguard.provisioning import store
    from mediaguard.provisioning.templates import owned_name

    ctx, admin, _ = populated_installation
    headers = key(admin)
    ctx.db.execute("UPDATE instances SET suspended=1")
    if schema == 156:
        ctx.db.execute(
            "UPDATE connector_evidence_origin SET schema_version='156_outbox_cursor_history'"
        )
    inst = create_instance(ctx, {"id": "id-proef", "app": "radarr", "media_type": "movie",
                                 "url": "http://wachtrij.invalid"})
    first, second = [
        {"name": name, "implementation": "QBittorrent", "host": name + ".invalid", "port": 8080}
        for name in ("eerste", "tweede")
    ]
    configs = {"eerste": [first], "hernoemd": [dict(first, name="Andere naam")],
               "reserve-uit": [first, dict(second, enabled=False)],
               "beide": [second, first], "tweede": [second]}
    torrent = "ab" * 20
    progress = {"eerste": 37, "tweede": 14}
    issued = {}
    for config, arr_name, visible in metingen:
        path = "/api/v1/provisioning/config"
        if config == "geweigerd":
            def refuse(name, data):
                assert name == store.DOWNLOAD_ID_OWNERS
                raise sqlite3.OperationalError("Proefopslag vol")

            with monkeypatch.context() as patch:
                patch.setattr(ctx, "state_set", refuse)
                change = [dict(first, enabled=False), dict(second, implementation="Sabnzbd")]
                response = admin.put(path, json={"download_clients": change})
                assert response.status_code == 503 and int(response.headers["Retry-After"]) > 0
        else:
            admin.put(path, json={"download_clients": configs[config]}).raise_for_status()

        # Iedere meting begint met een nieuwe kern en een lege activiteitencache.
        app = create_app(settings)
        current = app.state.ctx
        with closing(current.db), closing(TestClient(app)) as viewer:
            arr = wire(current, inst.id, FakeArr("radarr"))
            ambiguous = arr_name == "zonder meting"
            if arr_name is not None:
                arr.queue = [{"id": 7, "downloadId": torrent, "protocol": "torrent",
                              "downloadClient": owned_name(arr_name) if arr_name and not ambiguous
                              else None, "status": "downloading", "size": 0 if ambiguous else 100,
                              "sizeleft": 0 if ambiguous else 63}]

            def queue(request, visible=visible):
                name = request.url.host.split(".")[0]
                assert request.method == "GET" and request.url.path == "/api/v2/torrents/info"
                return httpx.Response(200, json=[{"hash": torrent, "state": "downloading",
                                                  "progress": progress[name] / 100}]
                                      if name in visible else [])

            current.services["live_download_transport"] = httpx.MockTransport(queue)
            expected = {progress[name] for name in visible}
            if ambiguous and len(visible) > 1:
                expected.add(None)  # De bron bewaart ook de nog niet toewijsbare Arr-rij.
            elif arr_name is not None:
                expected.add(37)
            before = database_snapshot(current, allow_auth_timestamps=True)
            page = viewer.get("/connector/v1/items?type=downloads", headers=headers).json()
            assert_database_preserved(current, before, allow_auth_timestamps=True)
            assert (page["status"], page["total"]) == ("ok", len(expected))
            rows = {r["progress"]: r["id"] for r in page["items"]}
            assert rows.keys() == expected
            if not issued:
                source = ["torrent", torrent.upper()]
                if schema == 156:
                    source.append(owned_name(arr_name) if arr_name and not ambiguous
                                  else first["name"])
                assert rows[37] == content_hash(source)
            for pct, connector_id in rows.items():
                assert issued.setdefault(pct, connector_id) == connector_id
            assert len(set(issued.values())) == len(issued)


def test_onvolledige_downloadmeting_behoudt_de_uitgegeven_ids(client, ctx, monkeypatch):
    from mediaguard.connector import download_ids
    from mediaguard.provisioning.templates import owned_name

    ctx.db.execute("UPDATE connector_evidence_origin SET schema_version='156'")
    transfer = {"download_id": "proefrij", "protocol": "torrent", "progress": 37}
    arr = {**transfer, "client": owned_name("Bron"), "instance_id": "wachtrij", "id": 7}

    def read(rows, when, *, errors=False):
        data = {"downloads": rows, "errors": errors, "summary": {}}

        async def activity(context):
            context.services["activity_observation"] = (data, when.isoformat())
            return data

        monkeypatch.setattr("mediaguard.connector.items.activity_cached", activity)
        ctx.services.pop("connector_items:downloads:admin", None)
        return client.get("/connector/v1/items?type=downloads").raise_for_status().json()

    now = datetime.now(UTC)
    first, = read([arr], now)["items"]
    path = ctx.settings.download_index_path
    original = path.read_bytes()
    assert b"proefrij" not in original and b"Bron" not in original
    for stale, errors in ((False, True), (True, False)):
        page = read([], now - timedelta(hours=2) if stale else now, errors=errors)
        assert (page["status"], page["total"]) == ("unknown", None)
        assert path.read_bytes() == original

    def refuse(*args):
        raise OSError("Proefopslag vol")

    with monkeypatch.context() as patch:
        patch.setattr(download_ids, "_write_index", refuse)
        other = {**arr, "id": 8, "download_id": "ander"}
        assert read([arr, other], datetime.now(UTC))["status"] == "unknown"
        assert path.read_bytes() == original
    visible, = read([{**transfer, "client": "Bron"}], datetime.now(UTC))["items"]
    assert visible["id"] == first["id"] and visible["progress"] == 37
    # Een andere database in dezelfde datamap heeft een eigen uitgiftegeschiedenis.
    ctx.db.execute("UPDATE outbox_generation SET generation=randomblob(16)")
    other, = read([{**transfer, "client": "Bron"}], datetime.now(UTC))["items"]
    assert other["id"] == download_ids.content_hash(["torrent", "PROEFRIJ", "Bron"])
    assert other["id"] != first["id"]


@pytest.mark.parametrize(("method", "route"), [
    ("GET", "discover"), ("GET", "health"), ("POST", "propose"), ("POST", "apply"),
    ("POST", "rollback"), ("GET", "events"),
])
def test_action_payloads_never_dispatch_or_change_progress(populated_installation, method, route):
    ctx, client, machine = populated_installation
    headers = key(client)
    revoked = key(client, name="ingetrokken")
    expired = key(client, name="verlopen")
    scoped = key(client, name="beperkt", scopes=["emergency_stop:on"])
    old = "2020-01-01T00:00:00+00:00"
    ctx.db.execute("UPDATE api_keys SET revoked_at=? WHERE name='ingetrokken'", (old,))
    ctx.db.execute("UPDATE api_keys SET expires_at=? WHERE name='verlopen'", (old,))

    def request(transport, request_headers=None):
        body = {"action_id": "wanted:search", "parameters": {"id": 1}} if method == "POST" else None
        return transport.request(
            method, f"/connector/v1/{route}", headers=request_headers, json=body
        )

    def check_accepted(response):
        if method == "POST":
            error(response, 501, "capability_unavailable")
        else:
            assert response.status_code == 200

    sql = "SELECT last_used_at FROM api_keys WHERE name='hub'"
    for previous in (None, old):
        ctx.db.execute("UPDATE api_keys SET last_used_at=? WHERE name='hub'", (previous,))
        before = database_snapshot(ctx, allow_auth_timestamps=True)
        check_accepted(request(machine, headers))
        assert ctx.db.fetch_one(sql)[0] > (previous or "")
        assert_database_preserved(ctx, before, allow_auth_timestamps=True)

    ctx.db.execute("UPDATE sessions SET last_seen_at=?", (old,))
    before = database_snapshot(ctx, allow_auth_timestamps=True)
    check_accepted(request(client))
    assert ctx.db.fetch_one("SELECT MAX(last_seen_at) FROM sessions")[0] > old
    assert_database_preserved(ctx, before, allow_auth_timestamps=True)

    before = database_snapshot(ctx)
    error(request(machine), 401, "unauthorized")
    assert_database_preserved(ctx, before)

    audit_sql = "SELECT COUNT(*) FROM audit_events WHERE action=?"
    for rejected in ({"X-Api-Key": "ongeldig"}, revoked, expired):
        before = database_snapshot(ctx)
        count = ctx.db.fetch_one(audit_sql, (AUTH_NEW_AUDIT_ACTION,))[0]
        error(request(client, rejected), 401, "unauthorized")
        assert ctx.db.fetch_one(audit_sql, (AUTH_NEW_AUDIT_ACTION,))[0] == count + 1
        assert_database_preserved(ctx, before, allow_rejection_audit=True)

    if method == "POST":
        before = database_snapshot(ctx, allow_auth_timestamps=True)
        error(request(machine, scoped), 403, "scope_ontbreekt")
        assert_database_preserved(ctx, before, allow_auth_timestamps=True)


def test_public_routes_and_oversized_request_do_not_write(populated_installation):
    ctx, client, machine = populated_installation
    headers = key(client)
    before = database_snapshot(ctx)
    for path in ("/healthz", "/readyz"):
        assert machine.get(path, headers={"X-Api-Key": "ongeldig"}).status_code == 200
        assert_database_preserved(ctx, before)
    response = machine.post("/connector/v1/apply", headers=headers, content=b"x" * (1048576 + 1))
    error(response, 413, "payload_too_large")
    assert_database_preserved(ctx, before)


@pytest.mark.asyncio
async def test_encoder_failed_probe_does_not_refresh_retained_measurement(ctx, monkeypatch):
    from mediaguard.encoding import client as encoding

    class Engine:
        async def health(self):
            raise encoding.EncoderError("niet beschikbaar")

    old = "2020-01-01T00:00:00+00:00"
    ctx.services["health_summary_encoder_last_ok"] = {"state": "ok"}
    ctx.services["health_summary_encoder_observed_at"] = old
    monkeypatch.setattr(encoding, "get_encoder_client", lambda _: Engine())
    monkeypatch.setattr(encoding, "note_encoder_reachability", lambda *args: False)
    await health_summary._encoder_state(ctx)
    assert ctx.services["health_summary_encoder_observed_at"] == old


def test_jobs_stalled_and_worker_heartbeat_not_request_time(client, machine, measurement, ctx):
    old = (datetime.now(UTC) - timedelta(seconds=1200)).isoformat()
    ctx.services["health_summary_observations"][1].update(
        jobs={"wachtrij": 3, "bezig": 0, "laatste_job_klaar": old}, oldest_queued_at=old
    )
    ctx.state_set("taskwatch:jobs", {"last_ok_at": old, "fails": 2, "last_error_at": utcnow()})
    d = machine.get("/connector/v1/health", headers=key(client)).json()
    jobs = next(c for c in d["checks"] if c["name"] == "jobs")
    assert jobs["status"] == "degraded" and jobs["detail"]["wachtrij"] == 3
    worker = next(c for c in d["checks"] if c["name"] == "worker")
    assert worker["status"] == "unknown" and worker["observed_at"] == old.replace("+00:00", "Z")
    assert d["status"] == "degraded"


def test_internal_errors_do_not_leak(client, machine, ctx, monkeypatch):
    async def broken(*args):
        raise RuntimeError("privaat pad en instelling")

    monkeypatch.setattr(health_summary, "summary", broken)
    r = machine.get("/connector/v1/health", headers=key(client))
    error(r, 500, "internal_error")
    assert "privaat" not in r.text


@pytest.mark.asyncio
async def test_summary_preserves_source_times_across_cached_rebuilds(ctx, monkeypatch):
    from mediaguard import attention
    from mediaguard.activity import api as activity

    old = "2020-01-01T00:00:00+00:00"
    act = {"summary": {"downloads": 0, "searches": 0}, "downloads": [], "errors": []}
    ctx.services["activity_cache"] = (time.monotonic(), act)
    ctx.services["activity_observation"] = (act, old)
    ctx.services["health_summary_encoder"] = (time.monotonic(), {"state": "ok"})
    ctx.services["health_summary_encoder_observed_at"] = old

    async def no_items(*args):
        return []

    monkeypatch.setattr(attention, "_encoder", no_items)

    async def no_collection(*args):
        pytest.fail("De activiteitencache is genegeerd")

    monkeypatch.setattr(activity, "_collect", no_collection)
    a = await health_summary.summary(Principal("hub", "viewer"), ctx)
    assert ctx.services["health_summary_observations"][1]["encoder"] == old
    assert ctx.services["health_summary_observations"][1]["instances"] == old
    ctx.services.pop("health_summary_cache")
    b = await health_summary.summary(Principal("hub", "viewer"), ctx)
    assert a is not b
    assert ctx.services["health_summary_observations"][1]["encoder"] == old
    assert ctx.services["health_summary_observations"][1]["instances"] == old
    assert set(ctx.services["health_summary_observations"][1]["jobs"]) == {
        "wachtrij",
        "bezig",
        "laatste_job_klaar",
    }


@pytest.mark.asyncio
async def test_stream_limit_counts_each_chunk_before_routing():
    from mediaguard.connector.errors import InputLimit

    calls = []

    async def downstream(*args):
        calls.append(True)

    chunks = iter(
        [
            {"type": "http.request", "body": b"x" * 600000, "more_body": True},
            {"type": "http.request", "body": b"x" * 448577, "more_body": False},
        ]
    )

    async def receive():
        return next(chunks)

    output = []

    async def send(message):
        output.append(message)

    await InputLimit(downstream)(
        {"type": "http", "path": "/connector/v1/health", "headers": []}, receive, send
    )
    assert calls == [] and output[0]["status"] == 413


def test_items_validation_and_evidence_not_found(client, machine, caplog):
    headers = key(client)
    caplog.set_level("INFO")
    supplied = headers["X-Api-Key"]
    for params in ({"type": supplied}, {"type": "wanted", "limit": 0},
                   {"type": "wanted", "cursor": supplied}):
        response = machine.get("/connector/v1/items", headers=headers, params=params)
        assert error(response, 422, "validation_error")["details"]["fouten"]
        assert supplied not in response.text
    for identifier in ("audit:99999", "job:99999", supplied):
        response = machine.get(f"/connector/v1/evidence/{identifier}", headers=headers)
        error(response, 404, "not_found")
        assert supplied not in response.text
    assert supplied not in caplog.text


def measure_empty_wanted_sources(ctx):
    now = utcnow()
    ctx.db.execute(
        "INSERT INTO library_sync_runs(instance_id,started_at,finished_at,status,counts_json) "
        "SELECT id,?,?,'succeeded',? FROM instances WHERE archived=0 AND suspended=0",
        (now, now, json.dumps({"wanted": {"open": 0}})),
    )


def test_items_wanted_pagination(populated_installation):
    ctx, client, machine = populated_installation
    headers = key(client)
    instance = ctx.db.fetch_one("SELECT id FROM instances LIMIT 1")[0]
    measure_empty_wanted_sources(ctx)
    now = utcnow()
    with ctx.db.transaction() as conn:
        conn.executemany(
            "INSERT INTO wanted_items(instance_id,kind,provider_id,item_provider_id,reason,title,"
            "first_seen_at,updated_at) VALUES (?,'movie',?,?,'missing','Fixture',?,?)",
            [(instance, n, n, now, now) for n in range(10000, 10210)],
        )
    url = "/connector/v1/items"
    first = machine.get(url, headers=headers, params={"type": "wanted", "limit": 201})
    assert first.status_code == 200
    again = machine.get(url, headers=headers, params={"type": "wanted", "limit": 201})
    assert first.headers["etag"] == again.headers["etag"]
    page = first.json()
    assert len(page["items"]) == 200
    remainder = machine.get(url, headers=headers, params={
        "type": "wanted", "cursor": page["next_cursor"], "limit": 201,
    }).json()
    ids = [row["id"] for row in page["items"] + remainder["items"]]
    assert len(ids) == len(set(ids)) == page["total"]
    assert remainder["next_cursor"] is None
    error(machine.get(url, headers=headers, params={
        "type": "downloads", "cursor": page["next_cursor"],
    }), 422, "validation_error")


def test_evidence_versions_and_audit_job_identity(client, machine, ctx, caplog):
    from mediaguard import __version__
    from mediaguard.audit import audit
    from mediaguard.jobs import enqueue

    headers = key(client)
    supplied = headers["X-Api-Key"]
    caplog.set_level("INFO")
    job = enqueue(ctx.db, "library.sync", {"full": True, "secret": supplied})
    ctx.db.execute("DELETE FROM audit_events WHERE id=?", (job,))
    old = ctx.db.execute_insert(
        "INSERT INTO audit_events(id, ts, actor, action) VALUES (?,?,'fixture','job.cancel')",
        (job, utcnow()),
    )
    assert old == job
    related = audit(
        ctx.db, actor=supplied, action="job.cancel", target_type="job", target_id=job,
        undo_hint=f"/fixture/{supplied}",
    )
    prefix = "/connector/v1/evidence/"
    historical = machine.get(prefix + f"audit:{old}", headers=headers).json()
    assert historical["app_version"] is None
    assert historical["details"]["app_version"] == "onbekend"
    record = machine.get(prefix + f"job:{job}", headers=headers)
    assert record.status_code == 200 and supplied not in record.text
    assert record.json()["app_version"] == __version__
    assert record.json()["request"] == {"active": None, "full": True}
    assert record.json()["response"]["status"] == "queued"
    assert record.json()["audit"]["id"] == f"audit:{related}"
    assert record.json()["audit"]["undo_hint"] is None
    linked = machine.get(prefix + f"audit:{related}", headers=headers).json()
    assert linked["id"] == f"audit:{related}" and linked["app_version"] == __version__
    estop = audit(
        ctx.db, actor=supplied, action="system.emergency_stop", reason=supplied,
        old_value={"active": False, "payload": [{"headers": {"X-Api-Key": supplied}}]},
        new_value={"active": True, "by": supplied, "path": f"/fixture/{supplied}"},
        undo_hint="Zet de noodstop weer uit",
    )
    response = machine.get(prefix + f"audit:{estop}", headers=headers)
    assert response.json()["request"]["active"] is False
    assert response.json()["response"]["active"] is True
    assert response.json()["audit"]["undo_hint"] == "Zet de noodstop weer uit"
    assert supplied not in response.text and supplied not in caplog.text


def test_items_five_sources_and_exact_fields(populated_installation, monkeypatch, caplog):
    from test_arr_fakes import FakeArr, wire

    from mediaguard import attention, storage_forecast
    from mediaguard.downloads import live
    from mediaguard.encoding import resources

    ctx, client, machine = populated_installation
    headers = key(client, scopes=[])
    supplied = headers["X-Api-Key"]
    markers = {
        "sessions": "privaat_kijkgedrag",
        "hold": {"active": True, "reason": "privaat_kijkreden"},
        "actions": "privaat_acties",
        "tests": "privaat_proefgegevens",
        "last": "privaat_vorige_waarde",
    }
    arr = [wire(ctx, row["id"], FakeArr(row["app"]))
           for row in ctx.db.fetch_all("SELECT id,app FROM instances")]
    measure_empty_wanted_sources(ctx)
    instance = ctx.db.fetch_one(
        "SELECT id FROM instances WHERE archived=0 ORDER BY id LIMIT 1",
    )[0]
    ctx.db.execute("UPDATE instances SET suspended=1 WHERE id=?", (instance,))
    ctx.db.execute(
        "INSERT INTO wanted_items(instance_id,kind,provider_id,item_provider_id,reason,title,"
        "first_seen_at,updated_at) VALUES (?,'movie',99001,99001,'cutoff',?,?,?)",
        (instance, supplied, utcnow(), utcnow()),
    )
    ctx.state_set("emergency_stop", {"active": True, "since": utcnow(), **markers})
    workers = [
        {"baan": "cpu", "aantal": 2, "gepauzeerd": True, "actieve_jobs": [],
         "pauze_reden": supplied, **markers},
        {"baan": "gpu", "aantal": 3, "gepauzeerd": False, "actieve_jobs": [
            {"job_id": 99, "bestand": f"/fixture/{supplied}", **markers},
        ]},
    ]
    called = []

    def engine(request):
        called.append(request.method)
        assert request.method == "GET"
        if request.url.path.endswith("/workers"):
            return httpx.Response(200, json=workers)
        if request.url.path.endswith("/settings/resources"):
            return httpx.Response(200, json={"percent": 1, "cpu_workers": 99})
        assert request.url.path.endswith("/health")
        return httpx.Response(200, json={"lanes_paused": [
            {"lane": "cpu", "reason": "automatisch: proefpauze", **markers},
        ]})

    ctx.services["encoder_transport"] = httpx.MockTransport(engine)

    async def downloads(_):
        return [
            {"download_id": "fixture-taak", "client": "fixture", "protocol": "torrent",
             "release": supplied, "progress": 37, "status": "downloading", "size": 1000,
             "size_left": 630, "time_left": "0:00:30", "status_nl": "Bezig", "category": "tv",
             **markers},
        ], []

    async def old_queues(_):
        return [], []

    monkeypatch.setattr(live, "client_downloads", downloads)
    monkeypatch.setattr(live, "source_queues", old_queues)

    def synthetic_incident(_):
        return [{
            **markers, "key": "system.update_available", "severity": "warning", "count": 3,
            "detail_nl": supplied, "href": f"//fixture.invalid/{supplied}",
            "rows": [{"id": "privaat_beheerder", "fields": markers}],
            "actions": [{"label_nl": markers["actions"], "path": "/fixture/actie",
                         "role": "admin"}],
        }]

    monkeypatch.setattr(attention, "SOURCES", (*attention.SOURCES, synthetic_incident))
    monkeypatch.setattr(storage_forecast, "attention_items", lambda _: [
        {"path": f"/fixture/{n}/{supplied}", "severity": "warning",
         "title_nl": "Schijf bijna vol", "detail_nl": supplied}
        for n in range(2)
    ])
    src_wanted = machine.get("/api/v1/wanted", headers=headers).json()
    src_status = machine.get("/api/v1/status", headers=headers).json()
    src_downloads = machine.get("/api/v1/activity", headers=headers).json()["downloads"]
    src_lanes = machine.get("/api/v1/app/status", headers=headers).json()["encoder"]["lanes"]
    src_incidents = machine.get("/api/v1/attention", headers=headers).json()["items"]
    assert src_wanted["items"] and src_downloads and src_lanes and src_incidents
    assert src_downloads[0]["id"] is None

    # Het verouderde wegklikbesluit blijft behouden. De bestaande bronroute mag
    # het bij een volgende gewone aanvraag wel opruimen.
    saved = {"instance.suspended." + instance: {"fp": "oude-proefkeuze"}}
    ctx.state_set(attention.DISMISSED_KEY, saved)
    before = database_snapshot(ctx, allow_auth_timestamps=True)
    expected_fields = {
        "wanted": {"instance_id", "kind", "reason", "status"},
        "status": {"emergency_stop_active", "emergency_stop_since", "jobs_queued",
                   "jobs_running", "jobs_failed", "unread_notifications"},
        "downloads": {"status", "progress", "awaiting_import"},
        "av1_lanes": {"lane", "running", "slots", "desired_slots", "paused"},
        "incidents": {"owner_app", "cause_code", "instance_id", "severity", "count"},
    }
    caplog.set_level("INFO")
    results = {}
    for kind, fields in expected_fields.items():
        url = f"/connector/v1/items?type={kind}"
        first = machine.get(url, headers=headers)
        second = machine.get(url, headers=headers)
        assert first.status_code == second.status_code == 200, first.text
        first_data = first.json()
        assert first_data == second.json()
        assert first.headers["ETag"] == second.headers["ETag"]
        assert len(first.headers["ETag"]) == 66
        assert first_data["status"] == "ok" and first_data["items"]
        for row in first_data["items"]:
            assert set(row) == fields | {"id", "observed_at", "deep_link"}
            assert row["observed_at"].endswith("Z")
            assert row["deep_link"].startswith("/") and not row["deep_link"].startswith("//")
        for marker in (*[value for value in markers.values() if isinstance(value, str)],
                       markers["hold"]["reason"], "privaat_beheerder", supplied):
            assert marker not in first.text and marker not in caplog.text
        results[kind] = first_data["items"]

    assert called and all(method == "GET" for method in called)
    assert any(fake.calls for fake in arr)
    assert all(call[0] == "GET" for fake in arr for call in fake.calls)
    assert ctx.state_get(attention.DISMISSED_KEY) == saved
    assert ctx.state_get(resources.PUSHED_STATE) is None
    assert_database_preserved(ctx, before, allow_auth_timestamps=True)
    wanted_ids = {f"wanted:{r['id']}" for r in src_wanted["items"]}
    assert {r["id"] for r in results["wanted"]} == wanted_ids
    assert {r["status"] for r in results["wanted"]} == {r["status"] for r in src_wanted["items"]}
    assert results["status"][0]["emergency_stop_active"] == src_status["emergency_stop"]["active"]
    assert results["status"][0]["unread_notifications"] == src_status["unread_notifications"]
    assert results["status"][0]["jobs_failed"] == src_status["jobs"]["failed"]
    assert [(d["status"], d["progress"]) for d in results["downloads"]] == [
        (d["status"], d["progress"]) for d in src_downloads
    ]
    downloads_observed = ctx.services["activity_observation"][1].replace("+00:00", "Z")
    assert results["downloads"][0]["observed_at"] == downloads_observed
    assert {(r["lane"], r["running"], r["slots"], r["paused"]) for r in results["av1_lanes"]} == {
        (r["lane"], r["running"], r["slots"], r["paused"]) for r in src_lanes
    }
    from mediaguard.connector.items import content_hash

    assert len(results["incidents"]) == len(src_incidents)
    assert {r["id"] for r in results["incidents"] if r["cause_code"] != "storage.full_soon"} == {
        content_hash(["incidents", r["key"]]) for r in src_incidents
        if r["key"] != "storage.full_soon"
    }
    storage_ids = {r["id"] for r in results["incidents"] if r["cause_code"] == "storage.full_soon"}
    assert len(storage_ids) == 2
    assert {r["owner_app"] for r in results["incidents"]} == {"mediaguard"}
    assert any(r["cause_code"] == "instance.suspended" and r["instance_id"] == instance
               and r["deep_link"] == f"/instellingen/instanties#{instance}"
               for r in results["incidents"])
    assert any(r["count"] == 3 and r["deep_link"] == "/activiteit?tab=problemen"
               for r in results["incidents"])


def test_items_measured_snapshot_and_source_failure(client, machine, ctx, caplog):
    from mediaguard.activity import api as activity
    from mediaguard.connector.items import Snapshot

    headers = key(client)
    supplied = headers["X-Api-Key"]
    caplog.set_level("INFO")
    url = "/connector/v1/items?type=downloads"
    slot = "connector_items:downloads:viewer"

    def seed(observed, *, broken=False, linked=False):
        rows = [
            {"id": None, "download_id": str(n), "protocol": "torrent",
             "status": "downloading", "progress": 12}
            for n in range(3)
        ]
        if linked:
            rows[0].update(id=734, instance_id="sonarr-series")
        value = {"downloads": rows, "summary": {"download_clients_ok": not broken},
                 "errors": [{"error": supplied}] if broken else []}
        ctx.services["activity_cache"] = (time.monotonic(), value)
        ctx.services["activity_observation"] = (value, observed)

    seed(utcnow())
    first = machine.get(url + "&limit=1", headers=headers).json()
    nxt = first["next_cursor"]
    assert nxt and first["total"] == 3 and first["snapshot_id"]
    seed(utcnow(), linked=True)
    query = {"type": "downloads", "cursor": nxt}
    second = machine.get("/connector/v1/items", params=query, headers=headers).json()
    assert second["snapshot_id"] == first["snapshot_id"]
    assert second["observed_at"] == first["observed_at"] and second["total"] == first["total"]
    ids = {r["id"] for r in first["items"] + second["items"]}
    assert len(ids) == 3 and second["next_cursor"] is None

    previous = ctx.services[slot]
    assert isinstance(previous, Snapshot)
    previous.collected_at -= activity.CACHE_SECONDS + 1
    linked = machine.get(url, headers=headers).json()
    assert {row["id"] for row in linked["items"]} == ids

    old = (datetime.now(UTC) - timedelta(seconds=1000)).isoformat()
    ctx.services[slot].collected_at -= activity.CACHE_SECONDS + 1
    seed(old)
    stale = machine.get(url, headers=headers).json()
    assert stale["status"] == "unknown" and stale["reason_code"] == "meting_verouderd"
    assert stale["observed_at"] == old.replace("+00:00", "Z")
    assert stale["items"] == [] and stale["total"] is None
    error(machine.get("/connector/v1/items", params=query, headers=headers),
          409, "snapshot_changed")

    ctx.services.pop(slot)
    seed(utcnow(), broken=True)
    response = machine.get(url, headers=headers)
    assert response.json()["status"] == "unknown"
    assert response.json()["reason_code"] == "bron_onbereikbaar"
    assert response.json()["total"] is None and response.json()["items"] == []
    assert supplied not in response.text and supplied not in caplog.text


def test_items_unreachable_encoder_and_read_only_secret_store(client, machine, ctx, monkeypatch):
    from mediaguard.encoding import client as encoder

    headers = key(client)
    remote_calls = []

    def unavailable(request):
        remote_calls.append(request.method)
        assert request.method == "GET"
        raise httpx.ConnectError("proefbron niet bereikbaar", request=request)

    def forbidden(*args):
        pytest.fail("Waarnemen mag de uitvalteller niet wijzigen")

    monkeypatch.setattr(encoder, "note_encoder_reachability", forbidden)
    monkeypatch.delenv("MG_ATTENTION_SKIP_ENCODER", raising=False)
    ctx.services["encoder_transport"] = httpx.MockTransport(unavailable)
    path = ctx.secrets.key_path
    assert not path.exists()
    lanes = machine.get("/connector/v1/items?type=av1_lanes", headers=headers).json()
    assert lanes["status"] == "unknown" and lanes["total"] is None and lanes["items"] == []
    incidents = machine.get("/connector/v1/items?type=incidents", headers=headers).json()
    assert incidents["status"] == "unknown" and incidents["total"] is None
    assert remote_calls == ["GET", "GET"]
    assert not path.exists()


def test_connector_item_and_evidence_role_and_legacy_shape(client, machine, app, ctx):
    from mediaguard.jobs import enqueue

    job = enqueue(ctx.db, "library.sync")
    assert "evidence_app_version" not in client.get("/api/v1/jobs").json()[0]
    assert "evidence_app_version" not in client.get(f"/api/v1/jobs/{job}").json()
    assert all("evidence_app_version" not in row for row in client.get("/api/v1/audit").json())
    app.dependency_overrides[current_principal] = lambda: Principal("test", "onbekend")
    for route in ("items?type=status", f"evidence/job:{job}"):
        error(machine.get(f"/connector/v1/{route}"), 403, "forbidden")



def test_evidence_job_version_follows_worker_attempt(client, machine, ctx, monkeypatch):
    from mediaguard import jobs

    headers = key(client)
    monkeypatch.setattr(jobs, "__version__", "2.3.4")
    job = jobs.enqueue(ctx.db, "library.sync")
    url = f"/connector/v1/evidence/job:{job}"
    created = machine.get(url, headers=headers).json()
    assert created["app_version"] == "2.3.4" and created["started_at"] is None
    monkeypatch.setattr(jobs, "__version__", "2.3.5")
    monkeypatch.setattr("mediaguard.audit.__version__", "2.3.5")
    assert ctx.services["jobs"]._claim_next()["id"] == job
    running = machine.get(url, headers=headers).json()
    assert running["app_version"] == created["app_version"] and running["started_at"].endswith("Z")
    attempt_path = "/connector/v1/evidence/" + running["audit"]["id"]
    attempt = machine.get(attempt_path, headers=headers).json()
    assert attempt["app_version"] == "2.3.5" and attempt["audit"]["action"] == "job.started"
    assert running["response"]["status"] == "running" and running["finished_at"] is None
    monkeypatch.setattr(jobs, "__version__", "2.3.6")
    assert machine.get(url, headers=headers).json() == running
    assert machine.get(attempt_path, headers=headers).json() == attempt
