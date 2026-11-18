"""Connector deel 1: alleen lezen, contractfouten en werkelijke meettijden."""

from __future__ import annotations

import copy
import sqlite3
import time
from collections import Counter
from datetime import UTC, datetime, timedelta
from uuid import UUID

import httpx
import pytest
from fastapi.testclient import TestClient

from mediaguard import health_summary
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
    assert d["actions"] == [] and d["item_types"] == []
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
    ctx.db.execute("DELETE FROM schema_migrations WHERE version='005_api_key_scopes'")
    r = machine.get("/readyz")
    assert r.status_code == 503 and r.json() == {"status": "unavailable"}

    def broken(*args, **kwargs):
        raise sqlite3.OperationalError("privaat pad met instelling")

    monkeypatch.setattr(ctx.db, "fetch_all", broken)
    assert machine.get("/readyz").json() == {"status": "unavailable"}
    assert machine.get("/healthz").json() == {"status": "ok"}


def test_legacy_error_and_container_health_unchanged(machine):
    r = machine.get("/api/v1/status")
    assert r.status_code == 401 and r.json() == {"detail": "Niet ingelogd"}
    assert set(machine.get("/api/v1/health").json()) == {"status", "version"}


@pytest.mark.parametrize(
    "path", ["/connector/v1/health", "/connector/v1/niet-bestaand", "/healthz", "/readyz"]
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


def test_connector_openapi_documents_only_current_routes(app):
    paths = app.openapi()["paths"]
    assert {p for p in paths if p.startswith("/connector/v1/")} == {
        "/connector/v1/discover", "/connector/v1/health", "/connector/v1/propose",
        "/connector/v1/apply", "/connector/v1/rollback",
    }
    for action in ("propose", "apply", "rollback"):
        assert "501" in paths[f"/connector/v1/{action}"]["post"]["responses"]
    for path in ("/healthz", "/readyz"):
        assert paths[path]["get"]["security"] == []


def database_snapshot(ctx, *, allow_auth=False):
    snapshot = {}
    for table in ctx.db.fetch_all("SELECT name, sql FROM sqlite_schema WHERE type='table'"):
        name = table["name"]
        quoted_name = '"' + name.replace('"', '""') + '"'
        ignored = AUTH_TIMESTAMP_COLUMNS.get(name) if allow_auth else None
        snapshot[name, table["sql"]] = Counter(
            tuple((column, value) for column, value in row.items() if column != ignored)
            for row in ctx.db.fetch_dicts(f"SELECT * FROM {quoted_name}")
        )
    return snapshot


def assert_database_preserved(ctx, before, *, allow_auth=False):
    after = database_snapshot(ctx, allow_auth=allow_auth)
    assert before.keys() == after.keys(), "Tabellen of hun schema gewijzigd"
    changed = []
    for table, old in before.items():
        new = after[table]
        if allow_auth and table[0] == "audit_events":
            for row, count in (new - old).items():
                if dict(row)["action"] == AUTH_NEW_AUDIT_ACTION:
                    new[row] -= count
        if new != old:
            changed.append(table[0])
    assert changed == [], "Onverwachte databasewijzigingen"


@pytest.fixture
def populated_database(ctx):
    now = utcnow()
    ctx.db.execute("INSERT INTO jobs(job_type,status,progress,created_at) "
                   "VALUES ('library.sync','running',0.5,?)", (now,))
    ctx.db.execute("INSERT INTO encoding_jobs(source_path,status,policy_json,created_at,updated_at)"
                   " VALUES ('/fixture/film.mkv','running','{}',?,?)", (now, now))
    ctx.db.execute(
        "INSERT INTO instances(id,app,media_type,name,url,suspended,created_at,updated_at) "
        "VALUES ('fixture','radarr','movie','Fixture','http://fixture.invalid',1,?,?)", (now, now)
    )
    ctx.db.execute("INSERT INTO lib_items(instance_id,kind,provider_id,title,synced_at) "
                   "VALUES ('fixture','movie',1,'Testfilm',?)", (now,))
    # Ook een eerste gezondheidsmeting wordt uitgevoerd, met een lokale encoderrespons.
    ctx.services["encoder_transport"] = httpx.MockTransport(lambda _: httpx.Response(200, json={}))
    return ctx


@pytest.mark.parametrize(("method", "route"), [
    ("GET", "discover"), ("GET", "health"), ("POST", "propose"), ("POST", "apply"),
    ("POST", "rollback"),
])
def test_action_payloads_never_dispatch_or_change_progress(client, machine, populated_database,
                                                           method, route):
    ctx = populated_database
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
        before = database_snapshot(ctx, allow_auth=True)
        check_accepted(request(machine, headers))
        assert ctx.db.fetch_one(sql)[0] > (previous or "")
        assert_database_preserved(ctx, before, allow_auth=True)

    ctx.db.execute("UPDATE sessions SET last_seen_at=?", (old,))
    before = database_snapshot(ctx, allow_auth=True)
    check_accepted(request(client))
    assert ctx.db.fetch_one("SELECT last_seen_at FROM sessions")[0] > old
    assert_database_preserved(ctx, before, allow_auth=True)

    before = database_snapshot(ctx)
    error(request(machine), 401, "unauthorized")
    assert_database_preserved(ctx, before)

    audit_sql = "SELECT COUNT(*) FROM audit_events WHERE action=?"
    for rejected in ({"X-Api-Key": "ongeldig"}, revoked, expired):
        before = database_snapshot(ctx, allow_auth=True)
        count = ctx.db.fetch_one(audit_sql, (AUTH_NEW_AUDIT_ACTION,))[0]
        error(request(client, rejected), 401, "unauthorized")
        assert ctx.db.fetch_one(audit_sql, (AUTH_NEW_AUDIT_ACTION,))[0] == count + 1
        assert_database_preserved(ctx, before, allow_auth=True)

    if method == "POST":
        before = database_snapshot(ctx, allow_auth=True)
        error(request(machine, scoped), 403, "scope_ontbreekt")
        assert_database_preserved(ctx, before, allow_auth=True)


def test_public_routes_and_oversized_request_do_not_write(client, machine, populated_database):
    ctx = populated_database
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
