"""Connector deel 1: alleen lezen, contractfouten en werkelijke meettijden."""

from __future__ import annotations

import copy
import hashlib
import json
import secrets
import sqlite3
import time
import zipfile
from collections import Counter
from contextlib import closing
from datetime import UTC, datetime, timedelta
from io import BytesIO
from pathlib import Path
from types import SimpleNamespace
from uuid import UUID
from xml.etree import ElementTree

import httpx
import pytest
from fastapi.testclient import TestClient
from test_migration_fakes import MigFakeArr
from test_upgradebewijs import ROOT, ub

from mediaguard import health_summary
from mediaguard.app import create_app
from mediaguard.auth import Principal, current_principal
from mediaguard.connector import backups
from mediaguard.db import utcnow
from mediaguard.instances.registry import apikey_secret, list_instances
from mediaguard.maintenance import api as onderhoud
from mediaguard.maintenance import bundle, preupdate
from mediaguard.notify import offsite
from mediaguard.provisioning.templates import PREFIX, owned_name

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
    assert d["item_types"] == ["wanted", "status", "downloads", "av1_lanes", "incidents", "backups"]
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
    assert params["type"]["enum"] == [
        "wanted", "status", "downloads", "av1_lanes", "incidents", "backups",
    ]
    models = app.openapi()["components"]["schemas"]
    assert {"core", "encoder", "bundle", "offsite", "forks", "rollback"} <= set(
        models["BackupItem"]["properties"]
    )
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
def herstelbronnen(client, ctx, tmp_path, monkeypatch):
    monkeypatch.setenv("MG_AIO_CONFIG", str(tmp_path))
    encoder = tmp_path / "encode"
    encoder.mkdir()
    migration = preupdate.encoder_migrations({}) / "001_init.sql"
    with closing(sqlite3.connect(encoder / "app.db")) as db, db:
        db.executescript(migration.read_text())
        db.executescript("CREATE TABLE schema_versie (versie INTEGER); "
                         "INSERT INTO schema_versie VALUES (1);")
    backup = client.post("/api/v1/backups").json()
    assert backup["integrity_ok"] is True
    secret = bundle.setup(ctx, "test")
    archive = bundle.create(ctx)
    assert archive is not None
    calls = []
    forks = {}
    transports = ctx.services.setdefault("arr_transports", {})
    for row in ctx.db.fetch_dicts("SELECT id, app FROM instances ORDER BY id"):
        fake = MigFakeArr(row["app"])
        fake.add_backup()
        transport = fake.transport()
        iid = row["id"]
        forks[iid] = fake

        async def read(request, *, transport=transport, iid=iid):
            calls.append((iid, request.method, request.url.path))
            assert request.method == "GET"
            return await transport.handle_async_request(request)

        transports[iid] = httpx.MockTransport(read)
        ctx.secrets.set(apikey_secret(iid), fake.api_key)
    assert len(forks) == 4
    return backup, secret, archive, forks, calls


@pytest.fixture
def geverifieerde_offsite(client, ctx, respx_mock, herstelbronnen):
    _, _, archive, _, _ = herstelbronnen
    target = "https://offsite.example.test/privemap"
    ctx.config.set("global", "", "backup.offsite.kind", "webdav", "test")
    ctx.config.set("global", "", "backup.offsite.webdav_url", target, "test")
    archive_bytes = (bundle.bundle_dir(ctx) / archive["file"]).read_bytes()
    respx_mock.put(f"{target}/{archive['file']}").respond(201)
    respx_mock.get(f"{target}/{archive['file']}").respond(200, content=archive_bytes)
    respx_mock.put(f"{target}/{offsite.KEYFILE}").respond(201)
    respx_mock.get(f"{target}/{offsite.KEYFILE}").respond(
        200, content=(bundle.bundle_dir(ctx) / offsite.KEYFILE).read_bytes(),
    )
    assert client.post("/api/v1/notify/offsite/run").json()["ok"] is True
    respx_mock.calls.clear()
    return target


def test_herstelrapport_geverifieerde_dekking_en_alleen_lezen(
    client, machine, ctx, respx_mock, herstelbronnen, geverifieerde_offsite
):
    backup, secret, _, forks, calls = herstelbronnen
    headers = key(client)
    before = database_snapshot(ctx, allow_auth_timestamps=True)
    files = {path: path.read_bytes() for path in ctx.settings.backup_dir.rglob("*")
             if path.is_file()}
    response = machine.get("/connector/v1/items?type=backups", headers=headers)
    assert response.status_code == 200
    page = response.json()
    assert page["status"] == "ok" and page["total"] == 1 and page["next_cursor"] is None
    report, = page["items"]
    assert report["status"] == "healthy"
    assert report["core"]["sha256"] == backup["sha256"]
    assert report["core"]["schema_versions"] == sorted(backup["schema_versions"])
    assert report["encoder"]["databases"][0]["schema_versions"] == ["1"]
    assert report["bundle"]["fingerprint"] == secret["fingerprint"]
    assert report["bundle"]["key_match"] is True
    assert report["offsite"]["target_kind"] == "webdav"
    assert report["offsite"]["age_seconds"] >= 0
    assert report["rollback"] == {
        "owner": "mediaguard", "pre_update_restore_mode": "handmatig", "staged": False,
    }
    assert {item["instance_id"] for item in report["forks"]} == forks.keys()
    assert all(item["verified"] and item["name"] for item in report["forks"])
    assert len(calls) == 8
    assert [(call.request.method, call.request.url.path.rsplit("/", 1)[1])
            for call in respx_mock.calls] == [
                ("GET", herstelbronnen[2]["file"]), ("GET", offsite.KEYFILE),
            ]
    repeated = machine.get("/connector/v1/items?type=backups", headers=headers)
    assert repeated.headers["etag"] == response.headers["etag"] and len(calls) == 8
    assert len(respx_mock.calls) == 2
    denied = machine.get("/api/v1/notify/offsite", headers=headers)
    assert denied.status_code == 403
    keyfile = json.loads((bundle.bundle_dir(ctx) / offsite.KEYFILE).read_text())
    for reply in (response, repeated, denied):
        assert secret["recovery_code"] not in reply.text
        assert keyfile["wrapped"] not in reply.text
        assert ctx.state_get(bundle.STATE_KEY)["public"] not in reply.text
        assert geverifieerde_offsite not in reply.text
    assert_database_preserved(ctx, before, allow_auth_timestamps=True)
    assert files == {path: path.read_bytes() for path in ctx.settings.backup_dir.rglob("*")
                     if path.is_file()}


@pytest.mark.parametrize(
    "variant", ["afwezig", "beschadigd", "deflate", "extern_pad", "toekomst"]
)
def test_herstelrapport_fork_zonder_actuele_verificatie_is_degraded(
    client, machine, herstelbronnen, geverifieerde_offsite, variant
):
    _, _, _, forks, calls = herstelbronnen
    iid, fake = next(iter(forks.items()))
    entry = fake.backup_list[0]
    if variant == "afwezig":
        fake.backup_list.clear()
    elif variant in ("beschadigd", "deflate"):
        if variant == "beschadigd":
            corrupt = b"onleesbare testkopie"
        else:
            zip_buffer = BytesIO()
            with zipfile.ZipFile(zip_buffer, "w", zipfile.ZIP_DEFLATED) as archive:
                archive.writestr("config.xml", "<Config/>")
                archive_offset = archive.getinfo("config.xml").FileHeader()
            corrupt = bytearray(zip_buffer.getvalue())
            corrupt[len(archive_offset)] = 0xFF  # Ongeldig DEFLATE-blok, geldige zip-map.
            corrupt = bytes(corrupt)
        fake.backup_blobs[entry["name"]] = corrupt
        entry["size"] = len(corrupt)
        entry["verified"] = True  # Een bronclaim zonder leesbare kopie telt niet.
    elif variant == "extern_pad":
        entry["path"] = "https://elders.example.test/archief"
    else:
        entry["time"] = (datetime.now(UTC) + timedelta(days=1)).isoformat()
    response = machine.get("/connector/v1/items?type=backups", headers=key(client))
    assert response.status_code == 200
    report, = response.json()["items"]
    assert report["status"] == "degraded"
    assert all(report[part]["status"] == "healthy"
               for part in ("core", "encoder", "bundle", "offsite"))
    fork = next(row for row in report["forks"] if row["instance_id"] == iid)
    assert fork["status"] == "degraded" and fork["verified"] is False
    downloads = 2 if variant in ("beschadigd", "deflate") else 1
    assert sum(instance == iid for instance, _, _ in calls) == downloads


def test_herstelrapport_offsite_hoort_bij_huidige_doel(
    client, machine, ctx, geverifieerde_offsite
):
    ctx.config.set("global", "", "backup.offsite.webdav_url", geverifieerde_offsite + "/nieuw",
                   "test")
    response = machine.get("/connector/v1/items?type=backups", headers=key(client))
    assert response.status_code == 200
    report, = response.json()["items"]
    assert report["offsite"]["status"] == "degraded"
    assert report["offsite"]["sha256"] is None
    assert report["bundle"]["key_match"] is True and report["bundle"]["verified"] is False
    assert geverifieerde_offsite not in response.text


def test_herstelrapport_s3_doel_met_zelfde_bucket_blijft_onbevestigd(
    client, machine, ctx, monkeypatch, herstelbronnen
):
    from mediaguard import settings_registry

    backup, _, archive, _, _ = herstelbronnen
    kopietijd = utcnow()
    monkeypatch.setattr(settings_registry, "utcnow", lambda: kopietijd)
    for veld, waarde in (("kind", "s3"), ("s3_bucket", "proef"), ("s3_endpoint",
                                                               "https://nieuw.example.test")):
        ctx.config.set("global", "", f"backup.offsite.{veld}", waarde, "test")
    ctx.db.execute(
        "INSERT INTO offsite_copies(bundle_name,target_kind,target,status,sha256,size,created_at)"
        " VALUES (?,'s3',?,'ok',?,?,?)",
        (archive["file"], offsite.target_label(ctx), backup["sha256"], 1, kopietijd),
    )
    response = machine.get("/connector/v1/items?type=backups", headers=key(client))
    assert response.status_code == 200
    report, = response.json()["items"]
    assert report["offsite"]["target_kind"] == "s3"
    assert report["offsite"]["status"] == "degraded"
    assert report["offsite"]["verified"] is False


def test_herstelrapport_bundel_met_oude_sleutel_is_degraded(
    client, machine, ctx, geverifieerde_offsite
):
    current = bundle.setup(ctx, "test")
    response = machine.get("/connector/v1/items?type=backups", headers=key(client))
    assert response.status_code == 200
    report, = response.json()["items"]
    assert report["bundle"]["fingerprint"] == current["fingerprint"]
    assert report["bundle"]["key_match"] is False
    assert report["bundle"]["status"] == report["status"] == "degraded"
    assert report["offsite"]["verified"] is True


def test_herstelrapport_corrupt_lokaal_en_offsite_zonder_readback(
    client, machine, ctx, herstelbronnen
):
    backup, _, archive, _, _ = herstelbronnen
    core = ctx.settings.backup_dir / backup["file"]
    with core.open("r+b") as handle:
        handle.write(b"ongewijzigde grootte")
    ctx.config.set("global", "", "backup.offsite.kind", "email", "test")
    ctx.db.execute(
        "INSERT INTO offsite_copies(bundle_name,target_kind,target,status,sha256,created_at)"
        " VALUES (?,'email',?,'ok',?,?)",
        (archive["file"], offsite.target_label(ctx), backup["sha256"], utcnow()),
    )
    response = machine.get("/connector/v1/items?type=backups", headers=key(client))
    assert response.status_code == 200
    report, = response.json()["items"]
    assert report["core"]["status"] == "degraded" and report["core"]["sha256"] is None
    assert report["encoder"]["status"] == "healthy"
    assert report["bundle"]["status"] == "degraded"
    assert report["offsite"]["status"] == "degraded"
    assert report["offsite"]["age_seconds"] is None


def test_herstelrapport_kiest_geverifieerde_set_voor_update(
    client, machine, ctx, tmp_path, herstelbronnen
):
    backup, _, _, _, _ = herstelbronnen
    path = ctx.settings.backup_dir / backup["file"]
    meta_file = path.with_suffix(".json")
    meta = json.loads(meta_file.read_text())
    meta["integrity_ok"] = False
    meta_file.write_text(json.dumps(meta))
    folder = ctx.settings.backup_dir / preupdate.FOLDER
    made = preupdate.make_set(folder, ctx.settings.db_path, encode_dir=tmp_path / "encode")
    verified = {row["name"]: row for row in preupdate.list_sets(folder)}[made.name]
    headers = key(client)
    before = database_snapshot(ctx, allow_auth_timestamps=True)

    response = machine.get("/connector/v1/items?type=backups", headers=headers)
    assert response.status_code == 200
    report, = response.json()["items"]
    database, engine = verified["files"]
    assert report["core"]["sha256"] == database["sha256"]
    assert report["encoder"]["databases"][0]["sha256"] == engine["sha256"]
    assert report["core"]["verified"] and report["encoder"]["verified"]
    assert_database_preserved(ctx, before, allow_auth_timestamps=True)


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


# Eén torrent in alle metingen. Iedere regel bevat de volledige nieuwe toestand;
# de voorgaande regel bevat de vorige toestand en de daar uitgegeven ID's.
# Arr-rij = (instantie, rijnummer, gemelde clientnaam, eigen voortgang).
_ARR = ("arr-a", 7, None, 37)
_BEHEERD = ("arr-a", 7, owned_name("eerste"), 37)
_NAAMLOOS = ("arr-a", 7, None, None)
_TWEEDE_RIJ = ("arr-a", 8, None, 82)
_ANDERE_INSTANTIE = ("arr-b", 7, None, 82)

# Clientmeting = {adres vóór .invalid: voortgang}. De inrichting hieronder koppelt
# elk adres aan een naam. De laatste kolom geeft per voortgang het verwachte ID:
# gelijke letters zijn hetzelfde uitgegeven ID, verschillende letters aparte ID's.
_EEN = {"eerste": 37}
_TWEE = {"eerste": 37, "tweede": 14}
_RESTANT = {"tweede": 14}


@pytest.mark.parametrize(("schema", "historische_naam", "metingen"), [
    # Inrichting, Arr-rijen, clientmeting op adres, verwachte connector-ID's.
    pytest.param(100, None, [("eerste", (), _EEN, {37: "A"})] * 2, id="basis"),
    pytest.param(154, None, [("eerste", (), _EEN, {37: "A"}),
                             ("eerste", (_BEHEERD,), _EEN, {37: "A"})], id="arrkoppeling154"),
    pytest.param(100, None, [("eerste", (_ARR,), {}, {37: "A"}),
                             ("eerste", (_ARR,), _EEN, {37: "A"}),
                             ("eerste", (_ARR,), {}, {37: "A"})], id="wachtrij"),
    pytest.param(100, None, [("eerste", (), _EEN, {37: "A"}),
                             ("geweigerd", (), _EEN, {37: "A"})], id="opslag"),
    pytest.param(100, None, [("eerste", (_ARR,), {}, {37: "A"}),
                             ("reserve-uit", (_ARR,), {}, {37: "A"}),
                             ("eerste", (_ARR,), {}, {37: "A"})], id="reserveclient"),
    pytest.param(100, None, [("eerste", (_ARR,), {}, {37: "A"}),
                             ("beide", (_ARR,), {}, {37: "A"}),
                             ("tweede", (_ARR,), {}, {37: "A"})], id="andere-inrichting"),
    pytest.param(156, "eerste", [("eerste", (), _EEN, {37: "A"}),
                                 ("eerste", (_BEHEERD,), _EEN, {37: "A"}),
                                 ("eerste", (), _EEN, {37: "A"})], id="arrkoppeling156"),
    pytest.param(156, owned_name("eerste"), [
        ("eerste", (_BEHEERD,), {}, {37: "A"}), ("eerste", (), _EEN, {37: "A"}),
        ("reserve-uit", (_BEHEERD,), {}, {37: "A"}),
    ], id="arrkoppeling156-omgekeerd"),
    pytest.param(100, None, [("eerste", (), _EEN, {37: "A"}),
                             ("beide", (), _TWEE, {37: "A", 14: "B"}),
                             ("tweede", (), _RESTANT, {14: "B"})], id="clientwissel"),
    pytest.param(100, None, [("eerste", (), _EEN, {37: "A"}),
                             ("beide", (), _TWEE, {37: "A", 14: "B"}),
                             ("beide", (), _EEN, {37: "A"}),
                             ("tweede", (), _RESTANT, {14: "B"})], id="clientwissel-na-herstart"),
    pytest.param(100, "eerste", [
        ("beide", (), _EEN, {37: "A"}),
        ("beide", (), _TWEE, {37: "A", 14: "B"}),
        ("beide", (), _EEN, {37: "A"}),
        ("beide", (), _RESTANT, {14: "B"}),
    ], id="clientwissel-vaste-inrichting"),
    pytest.param(100, "eerste", [
        ("beide", (), _EEN, {37: "A"}),
        ("beide", (), {"eerste": 37, "tweede": 82}, {37: "A", 82: "B"}),
        ("beide", (), {"tweede": 82}, {82: "B"}),
    ], id="clientvertrek"),
    pytest.param(156, "eerste", [("beide", (), _EEN, {37: "A"}),
                                 ("beide", (_BEHEERD,), _TWEE, {37: "A", 14: "B"}),
                                 ("beide", (), _RESTANT, {14: "B"})], id="samenloop156"),
    pytest.param(156, "eerste", [("eerste", (), _EEN, {37: "A"}),
                                 ("hernoemd", (), _EEN, {37: "A"})], id="clientnaam"),
    pytest.param(156, "eerste", [("eerste", (), _EEN, {37: "A"}),
                                 ("verplaatst", (), {"ander": 41}, {41: "A"})], id="clientadres"),
    pytest.param(156, "eerste", [
        ("beide", (), _TWEE, {37: "A", 14: "B"}),
        ("beide", (), {"eerste": 45, "tweede": 82}, {45: "A", 82: "B"}),
        ("namen-verwisseld", (), {"eerste": 45, "tweede": 82}, {45: "A", 82: "B"}),
    ], id="omgekeerde-bronvolgorde"),
    pytest.param(156, "eerste", [
        ("beide", (), _TWEE, {37: "A", 14: "B"}),
        ("beide", (_NAAMLOOS,), _TWEE, {37: "A", 14: "B", None: "W"}),
        ("beide", (_NAAMLOOS,), _EEN, {37: "A"}),
    ], id="dubbelmelding"),
    pytest.param(156, "eerste", [
        ("eerste", (_NAAMLOOS,), _EEN, {37: "A"}),
        ("beide", (_NAAMLOOS,), _TWEE, {37: "A", 14: "B", None: "W"}),
        ("beide", (), _TWEE, {37: "A", 14: "B"}),
    ], id="dubbelmelding-omgekeerd"),
    pytest.param(100, None, [
        ("eerste", (_ARR, _TWEEDE_RIJ), {}, {37: "A", 82: "B"}),
        ("eerste", (_ARR, ("arr-a", 8, "eerste", 82)), {}, {37: "A", 82: "B"}),
        ("eerste", (_ARR, _TWEEDE_RIJ), {}, {37: "A", 82: "B"}),
    ], id="gedeelde-clientnaam"),
    pytest.param(100, None, [
        ("eerste", (_ARR, _ANDERE_INSTANTIE), {}, {37: "A", 82: "B"}),
        ("eerste", (_ARR, ("arr-b", 7, "eerste", 82)), {}, {37: "A", 82: "B"}),
        ("eerste", (_ARR, _ANDERE_INSTANTIE), {}, {37: "A", 82: "B"}),
        ("eerste", (_ANDERE_INSTANTIE,), {}, {82: "B"}),
        ("eerste", (_ARR, _ANDERE_INSTANTIE), {}, {37: "A", 82: "B"}),
    ], id="gedeelde-clientnaam-twee-instanties"),
    pytest.param(100, "eerste", [
        ("beide", (("arr-a", 7, "eerste", None),), _TWEE, {37: "A", 14: "B"}),
        ("beide", (), _TWEE, {37: "A", 14: "B"}),
        ("beide", (("arr-b", 8, None, None),), _TWEE, {37: "A", 14: "B", None: "W"}),
        ("beide", (("arr-b", 8, "eerste", None),), _TWEE, {37: "A", 14: "B"}),
    ], id="clientsamenloop"),
    pytest.param(100, "eerste", [
        ("beide", (("arr-a", 7, "eerste", None),), _TWEE, {37: "A", 14: "B"}),
        ("beide", (), _TWEE, {37: "A", 14: "B"}),
        ("beide", (("arr-a", 8, None, None),), _TWEE, {37: "A", 14: "B", None: "W"}),
        ("beide", (("arr-a", 8, "eerste", None),), _TWEE, {37: "A", 14: "B"}),
    ], id="clientsamenloop-zelfde-instantie"),
    pytest.param(100, None, [
        ("eerste", (), _EEN, {37: "A"}),
        ("eerste", (_BEHEERD, ("arr-a", 8, "eerste", 82)), {}, {37: "W", 82: "X"}),
        ("eerste", (_BEHEERD, ("arr-a", 8, "eerste", 82)), {}, {37: "W", 82: "X"}),
        ("eerste", (), _EEN, {37: "A"}),
    ], id="meerduidige-clientnaam"),
    pytest.param(100, None, [
        ("eerste", (_BEHEERD,), {}, {37: "A"}),
        ("eerste", (_BEHEERD, ("arr-b", 7, "eerste", 82)), {}, {37: "A", 82: "B"}),
        ("eerste", (("arr-b", 7, "eerste", 82),), {}, {82: "B"}),
    ], id="vertrek-bij-gedeelde-clientnaam"),
    pytest.param(100, None, [
        ("eerste", (_ARR,), {}, {37: "A"}),
        ("eerste", (_ARR, ("arr-b", 7, "eerste", 82)), {}, {37: "A", 82: "B"}),
        ("eerste", (("arr-a", 7, "eerste", 37),), {}, {37: "A"}),
        ("eerste", (("arr-a", 7, "eerste", 37),), {}, {37: "A"}),
    ], id="vertrek-met-arr-naam"),
])
def test_uitgegeven_download_id(populated_installation, settings, monkeypatch,
                                 schema, historische_naam, metingen):
    from test_arr_fakes import FakeArr, wire

    from mediaguard.connector.items import content_hash
    from mediaguard.downloads.live import ClientDownload
    from mediaguard.instances.registry import create_instance
    from mediaguard.provisioning import store

    ctx, admin, _ = populated_installation
    headers = key(admin)
    ctx.db.execute("UPDATE instances SET suspended=1")
    if schema != 100:
        version = {154: "154_outbox_generation", 156: "156_outbox_cursor_history"}[schema]
        ctx.db.execute("UPDATE connector_evidence_origin SET schema_version=?", (version,))
    for instance in ("arr-a", "arr-b"):
        create_instance(ctx, {"id": instance, "app": "radarr", "media_type": "movie",
                              "url": f"http://{instance}.invalid"})
    first, second = [
        {"name": name, "implementation": "QBittorrent", "host": name + ".invalid", "port": 8080}
        for name in ("eerste", "tweede")
    ]
    configs = {"eerste": [first], "hernoemd": [dict(first, name="Andere naam")],
               "verplaatst": [dict(first, host="ander.invalid")],
               "namen-verwisseld": [dict(first, name="tweede"), dict(second, name="eerste")],
               "reserve-uit": [first, dict(second, enabled=False)],
               "beide": [second, first], "tweede": [second]}
    torrent = "ab" * 20
    issued = {}
    clients_by_host = {}
    for config, arr_rows, visible, expected in metingen:
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
            clients_by_host = {c["host"]: c["name"] for c in configs[config]
                               if c.get("enabled", True)}

        # Iedere meting begint met een nieuwe kern en een lege activiteitencache.
        app = create_app(settings)
        current = app.state.ctx
        with closing(current.db), closing(TestClient(app)) as viewer:
            arrs = {inst: wire(current, inst, FakeArr("radarr")) for inst in ("arr-a", "arr-b")}
            for inst, row_id, name, pct in arr_rows:
                arrs[inst].queue.append({
                    "id": row_id, "downloadId": torrent, "protocol": "torrent",
                    "downloadClient": name, "status": "downloading",
                    "size": 100 if pct is not None else 0,
                    "sizeleft": 100 - pct if pct is not None else 0,
                })

            called = []

            def queue(request, visible=visible, called=called):
                address = request.url.host.removesuffix(".invalid")
                assert request.method == "GET" and request.url.path == "/api/v2/torrents/info"
                called.append(request.url.host)
                return httpx.Response(200, json=[{"hash": torrent, "state": "downloading",
                                                  "progress": visible[address] / 100}]
                                      if address in visible else [])

            current.services["live_download_transport"] = httpx.MockTransport(queue)
            before = database_snapshot(current, allow_auth_timestamps=True)
            page = viewer.get("/connector/v1/items?type=downloads", headers=headers).json()
            assert_database_preserved(current, before, allow_auth_timestamps=True)
            assert sorted(called) == sorted(clients_by_host)
            activity = current.services["activity_observation"][0]
            confirmed = {r["client"].removeprefix(PREFIX) for r in activity["downloads"]
                         if isinstance(r, ClientDownload)}
            assert confirmed == {clients_by_host[f"{address}.invalid"] for address in visible}
            assert (page["status"], page["total"]) == ("ok", len(expected))
            assert page["next_cursor"] is None
            assert len(page["items"]) == len(expected)
            rows = {r["progress"]: r["id"] for r in page["items"]}
            assert rows.keys() == expected.keys()
            if not issued:
                source = ["torrent", torrent.upper()]
                if historische_naam is not None:
                    source.append(historische_naam)
                assert rows[37] == content_hash(source)
            for pct, label in expected.items():
                issued.setdefault(label, rows[pct])
            assert len(set(issued.values())) == len(issued)
            progress_by_id = {r["id"]: r["progress"] for r in page["items"]}
            expected_by_label = {label: pct for pct, label in expected.items()}
            for label, identity in issued.items():
                if label in expected_by_label:
                    assert progress_by_id[identity] == expected_by_label[label], (config, label)
                else:
                    assert identity not in progress_by_id, (config, arr_rows, label)


def test_onvolledige_downloadmeting_behoudt_de_uitgegeven_ids(client, ctx, monkeypatch):
    from mediaguard.connector import download_ids

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


BACKUP_REPORT_ROUTE = "/connector/v1/items?type=backups"


def rapport(machine, headers):
    antwoord = machine.get(BACKUP_REPORT_ROUTE, headers=headers)
    assert antwoord.status_code == 200
    item, = antwoord.json()["items"]
    return item


@pytest.mark.parametrize("variant", ["sleutelbestand", "bundelinhoud"])
def test_onherstelbare_bundel(client, machine, ctx, herstelbronnen, tmp_path, variant):
    _, inrichting, archief, _, _ = herstelbronnen
    lokale_map = bundle.bundle_dir(ctx)
    bestand = lokale_map / archief["file"]
    sleutel = lokale_map / offsite.KEYFILE
    code = inrichting["recovery_code"]
    assert "core/mediaguard.db" in bundle.restore(bestand, code, sleutel, tmp_path / "controle")
    if variant == "sleutelbestand":
        inhoud = json.loads(sleutel.read_text())
        del inhoud["wrapped"]
        sleutel.write_text(json.dumps(inhoud))
    else:
        with bestand.open("r+b") as kopie:
            kopie.truncate(bestand.stat().st_size - 1)

    externe_map = tmp_path / "externe-kopie"
    externe_map.mkdir()
    ctx.config.set("global", "", "backup.offsite.kind", "path", "proef")
    ctx.config.set("global", "", "backup.offsite.path", str(externe_map), "proef")
    assert client.post("/api/v1/notify/offsite/run").json()["ok"] is False
    mislukt = 0
    for bron in (lokale_map, externe_map):
        try:
            bundle.restore(bron / bestand.name, code, bron / offsite.KEYFILE, tmp_path / "herstel")
        except (bundle.BundleError, FileNotFoundError):
            mislukt += 1
    assert mislukt == 2
    item = rapport(machine, key(client))
    dekking = item["bundle"]
    print(f"{variant}: herstel vooraf=ok, herstel geweigerd={mislukt}/2, "
          f"bundel={dekking['status']}, verified={dekking['verified']}, "
          f"key_match={dekking['key_match']}, totaal={item['status']}")
    assert dekking["status"] == item["status"] == "degraded" and not dekking["verified"]


def test_encoder_zonder_actieve_voortgang(
    client, machine, ctx, tmp_path, monkeypatch, herstelbronnen, geverifieerde_offsite
):
    # De primaire database is tijdens de kopie tijdelijk buiten de gekoppelde map.
    monkeypatch.syspath_prepend(str(ROOT / "services" / "encoder"))
    from app.db import Database

    oorspronkelijke_kopie = ctx.settings.backup_dir / herstelbronnen[0]["file"]
    oorspronkelijke_kopie.with_suffix(".json").unlink()
    (tmp_path / "encode" / "app.db").unlink()
    with closing(sqlite3.connect(tmp_path / "encode" / "encoder.db")) as db, db:
        db.executescript("CREATE TABLE schema_versie (versie INTEGER); "
                         "INSERT INTO schema_versie VALUES (1);")
    afgekoppeld = tmp_path / "losgekoppelde-engine" / "app.db"
    afgekoppeld.parent.mkdir()
    Database(SimpleNamespace(database_path=afgekoppeld, config_root=afgekoppeld.parent,
                             backup_root=tmp_path / "migratiekopie")).initialize()
    assert ub.vul(afgekoppeld, "encode")["toegevoegd"]["job"] >= 1
    with closing(sqlite3.connect(afgekoppeld)) as db, db:
        db.execute("UPDATE job SET status='bezig' WHERE id=(SELECT MIN(id) FROM job)")
    kopie = client.post("/api/v1/backups").json()
    actief = tmp_path / "encode" / "app.db"
    afgekoppeld.rename(actief)
    with closing(sqlite3.connect(actief.as_uri() + "?immutable=1", uri=True)) as db:
        assert db.execute("SELECT count(*) FROM job WHERE status='bezig'").fetchone()[0] == 1
    hulpbestanden = []
    for entry in kopie["extra_files"]:
        pad = ctx.settings.backup_dir / entry["file"]
        with closing(sqlite3.connect(pad.as_uri() + "?immutable=1", uri=True)) as db:
            assert not db.execute("SELECT 1 FROM sqlite_schema WHERE name='job'").fetchall()
        hulpbestanden.append(hashlib.sha256(pad.read_bytes()).hexdigest())
    assert hulpbestanden
    item = rapport(machine, key(client))
    dekking = item["encoder"]
    if dekking["verified"]:
        assert [db["sha256"] for db in dekking["databases"]] == hulpbestanden
    print(f"AV1: actieve taken=1, kopieën met taaktabel=0, encoder={dekking['status']}, "
          f"verified={dekking['verified']}, totaal={item['status']}")
    assert dekking["status"] == item["status"] == "degraded" and not dekking["verified"]


def test_fork_met_lege_config(client, machine, herstelbronnen, geverifieerde_offsite):
    _, _, _, forks, _ = herstelbronnen
    iid, fork = next(iter(forks.items()))
    entry = fork.backup_list[0]
    naam = entry["name"]
    leeg = b""
    with pytest.raises(ElementTree.ParseError):
        ElementTree.fromstring(leeg)
    doel = BytesIO()
    met_inhoud = BytesIO(fork.backup_blobs[naam])
    with zipfile.ZipFile(met_inhoud) as bron, zipfile.ZipFile(doel, "w") as uit:
        assert "config.xml" in bron.namelist()
        for lid in bron.infolist():
            data = leeg if lid.filename == "config.xml" else bron.read(lid)
            uit.writestr(lid, data)
    fork.backup_blobs[naam] = doel.getvalue()
    entry["size"] = len(doel.getvalue())
    item = rapport(machine, key(client))
    dekking = next(rij for rij in item["forks"] if rij["instance_id"] == iid)
    print(f"Fork: config.xml=0 bytes, XML=onleesbaar, fork={dekking['status']}, "
          f"verified={dekking['verified']}, totaal={item['status']}")
    assert dekking["status"] == item["status"] == "degraded" and not dekking["verified"]


@pytest.mark.parametrize("variant", ["sleutelupload", "doelreset"])
def test_offsite_bewijs_voor_huidig_doel(client, machine, ctx, herstelbronnen, variant):
    # De simulatiedienst geeft elke identiteit een eigen lege opslagruimte.
    _, _, archief, _, _ = herstelbronnen
    inhoud = {}
    weigeringen = []

    def opslag(request):
        doel = (request.headers.get("Authorization"), request.url.path)
        if request.method == "PUT":
            if variant == "sleutelupload" and request.url.path.endswith("/" + offsite.KEYFILE):
                weigeringen.append(503)
                return httpx.Response(503)
            inhoud[doel] = request.content
            return httpx.Response(201)
        assert request.method == "GET"
        if doel not in inhoud:
            return httpx.Response(404)
        return httpx.Response(200, content=inhoud[doel])

    vervoer = httpx.MockTransport(opslag)
    ctx.services["notify_transport"] = vervoer
    basis = "https://opslag.invalid/review"
    ctx.config.set("global", "", "backup.offsite.kind", "webdav", "proef")
    ctx.config.set("global", "", "backup.offsite.webdav_url", basis, "proef")
    if variant == "doelreset":
        ctx.config.set("global", "", "backup.offsite.webdav_user", "eerste-ruimte", "proef")
    run = client.post("/api/v1/notify/offsite/run").json()
    assert run["ok"] is (variant == "doelreset")
    if variant == "doelreset":
        ctx.config.reset("global", "", "backup.offsite.webdav_user")
    else:
        assert weigeringen == [503]
    nieuw = ctx.config.value("backup.offsite.webdav_user")
    with httpx.Client(transport=vervoer, auth=(nieuw, "")) as lezer:
        archiefstatus = lezer.get(f"{basis}/{archief['file']}").status_code
        sleutelstatus = lezer.get(f"{basis}/{offsite.KEYFILE}").status_code
    verwacht = (200, 404) if variant == "sleutelupload" else (404, 404)
    assert (archiefstatus, sleutelstatus) == verwacht
    item = rapport(machine, key(client))
    dekking = item["offsite"]
    print(f"{variant}: kopie={archiefstatus}, sleutelbestand={sleutelstatus}, "
          f"offsite={dekking['status']}, verified={dekking['verified']}, totaal={item['status']}")
    assert dekking["status"] == item["status"] == "degraded" and not dekking["verified"]


def test_rechten_en_foutcontract(client, machine, ctx, monkeypatch):
    error(machine.get(BACKUP_REPORT_ROUTE), 401, "unauthorized")
    headers = key(client)
    response = machine.get(BACKUP_REPORT_ROUTE + "&cursor=ongeldig", headers=headers)
    error(response, 422, "validation_error")
    assert machine.get("/api/v1/notify/offsite", headers=headers).status_code == 403
    with monkeypatch.context() as patch:
        patch.setitem(machine.app.dependency_overrides, current_principal,
                      lambda: Principal("proef", "onbekend"))
        error(machine.get(BACKUP_REPORT_ROUTE, headers=headers), 403, "forbidden")
    async def bezet(*_):
        raise sqlite3.OperationalError("database is locked")

    monkeypatch.setattr(backups, "observe", bezet)
    antwoord = machine.get(BACKUP_REPORT_ROUTE, headers=headers)
    error(antwoord, 503, "database_unavailable")
    assert int(antwoord.headers["Retry-After"]) >= 1 and "database is locked" not in antwoord.text


@pytest.mark.parametrize("versie", ["999_toekomst", "999_toekomst-v2"])
def test_kernschema_en_herstelroute(
    client, machine, ctx, herstelbronnen, geverifieerde_offsite, versie
):
    kopie, _, _, _, _ = herstelbronnen
    bestand = ctx.settings.backup_dir / kopie["file"]
    with closing(sqlite3.connect(bestand)) as db, db:
        db.execute("INSERT INTO schema_migrations(version,applied_at) VALUES (?,CURRENT_TIMESTAMP)",
                   (versie,))
    # Consistente kopie van een nieuwere versie; regenereer de echte integriteitsmeting.
    ok, versies = onderhoud._verify_copy(bestand)
    assert ok and versie in versies
    meta = bestand.with_suffix(".json")
    gegevens = json.loads(meta.read_text())
    gegevens.update(integrity_ok=ok, schema_versions=versies, size=bestand.stat().st_size,
                    sha256=hashlib.sha256(bestand.read_bytes()).hexdigest())
    meta.write_text(json.dumps(gegevens))
    item = rapport(machine, key(client))
    antwoord = client.post(f"/api/v1/backups/{bestand.name}/restore",
                           json={"confirm": bestand.name})
    assert antwoord.status_code == 409 and antwoord.json()["code"] == "schema_nieuwer"
    dekking = item["core"]
    zichtbaar = versie in (dekking["schema_versions"] or [])
    print(f"Kernschema {versie}: herstel=409 schema_nieuwer, versie getoond={zichtbaar}, "
          f"kern={dekking['status']}, verified={dekking['verified']}, totaal={item['status']}")
    assert dekking["status"] == item["status"] == "degraded" and not dekking["verified"]


def test_onvolledige_terugzetmarkering(client, machine, ctx, tmp_path, geverifieerde_offsite):
    marker = ctx.settings.data_dir / preupdate.STAGE_MARKER
    marker.write_text("{}")
    aparte_kopie = tmp_path / "terugzetproef" / "core"
    aparte_kopie.mkdir(parents=True)
    (aparte_kopie / preupdate.STAGE_MARKER).write_bytes(marker.read_bytes())
    resultaat = preupdate.apply_staged(aparte_kopie)
    assert resultaat["ok"] is False
    item = rapport(machine, key(client))
    staged = item["rollback"]["staged"]
    print(f"Terugzetten: set ontbreekt, toepassen={resultaat['ok']}, staged={staged}")
    assert staged is None and item["status"] == "degraded"


def test_terugzetrapport_controleert_klaargezet_bestand(client, machine, ctx, monkeypatch):
    headers = key(client)
    monkeypatch.setattr(backups, "CACHE_SECONDS", 0)
    core = ctx.settings.data_dir
    folder = ctx.settings.backup_dir / preupdate.FOLDER
    snapshot = preupdate.make_set(folder, ctx.settings.db_path, encode_dir=None)
    preupdate.stage(core, snapshot, actor="proef")
    marker = core / preupdate.STAGE_MARKER
    stored = marker.read_bytes()
    assert rapport(machine, headers)["rollback"]["staged"] is True
    assert marker.read_bytes() == stored
    staged = core / (ctx.settings.db_path.name + preupdate.STAGED_SUFFIX)
    staged.write_bytes(staged.read_bytes()[:-1])
    assert rapport(machine, headers)["rollback"]["staged"] is None
    assert marker.read_bytes() == stored


def test_offsite_reset_naar_dezelfde_doelwaarde_vervalt_direct(
    client, machine, ctx, geverifieerde_offsite
):
    headers = key(client)
    ctx.config.set("global", "", "backup.offsite.webdav_user", "", "proef")
    assert client.post("/api/v1/notify/offsite/run").json()["ok"] is True
    assert rapport(machine, headers)["offsite"]["verified"] is True
    ctx.config.reset("global", "", "backup.offsite.webdav_user")
    assert rapport(machine, headers)["offsite"]["verified"] is False
    assert client.post("/api/v1/notify/offsite/run").json()["ok"] is True
    # De mislukte waarneming blijft maximaal één meetinterval gedeeld.
    ctx.services.pop("connector_items:backups:viewer")
    assert rapport(machine, headers)["offsite"]["verified"] is True


def test_offsite_sleutel_verdwijnt_op_hetzelfde_doel(
    client, machine, ctx, respx_mock, geverifieerde_offsite, monkeypatch
):
    headers = key(client)
    monkeypatch.setattr(backups, "CACHE_SECONDS", 0)
    assert rapport(machine, headers)["offsite"]["verified"] is True
    respx_mock.get(f"{geverifieerde_offsite}/{offsite.KEYFILE}").respond(404)
    report = rapport(machine, headers)
    assert report["bundle"]["verified"] is report["offsite"]["verified"] is False
    assert report["status"] == "degraded"


def test_encoder_mist_tabel_van_geregistreerde_versie(client, machine, ctx, herstelbronnen):
    backup, *_ = herstelbronnen
    engine, = backup["extra_files"]
    path = ctx.settings.backup_dir / engine["file"]
    with closing(sqlite3.connect(path)) as db, db:
        db.execute("INSERT INTO schema_versie VALUES (26)")
    meta_path = (ctx.settings.backup_dir / backup["file"]).with_suffix(".json")
    meta = json.loads(meta_path.read_text())
    meta["extra_files"][0].update(
        size=path.stat().st_size, sha256=hashlib.sha256(path.read_bytes()).hexdigest(),
    )
    meta_path.write_text(json.dumps(meta))
    assert not rapport(machine, key(client))["encoder"]["verified"]


def test_lezen_op_gevulde_release(populated_installation):
    ctx, admin, viewer = populated_installation
    headers = key(admin)
    aanvragen = []

    def alleen_lezen(request):
        assert request.method == "GET"
        aanvragen.append(request.method)
        return httpx.Response(200, json=[])

    transports = ctx.services.setdefault("arr_transports", {})
    for instantie in list_instances(ctx):
        ctx.secrets.set(apikey_secret(instantie.id), secrets.token_urlsafe(20))
        transports[instantie.id] = httpx.MockTransport(alleen_lezen)
    voor = database_snapshot(ctx)
    open_werk = ctx.db.fetch_one(
        "SELECT COUNT(*) AS n FROM mediaserver_pending WHERE done_at IS NULL"
    )["n"]
    assert open_werk > 0
    assert ctx.db.fetch_one("SELECT COUNT(*) AS n FROM mediaserver_watch")["n"] > 0
    for _ in range(2):
        antwoord = viewer.get(BACKUP_REPORT_ROUTE, headers=headers)
        assert antwoord.status_code == 200
        assert antwoord.json()["items"][0]["status"] == "degraded"
    assert aanvragen
    assert_database_preserved(ctx, voor)
    print(f"Releasefixture: tabellen={len(voor)}, gevuld={sum(bool(v) for v in voor.values())}, "
          f"open overdrachten={open_werk}, rijverschillen=0, fork-GET={len(aanvragen)}")


def _av1_taken(path):
    with closing(sqlite3.connect(path.resolve().as_uri() + "?mode=ro", uri=True)) as db:
        return db.execute("SELECT id,status,percent FROM job ORDER BY id").fetchall()


@pytest.mark.parametrize("gebrek", ["kernsleutel", "andere_sleutel", "primair", "taaktabel"])
def test_herstelde_bundel_is_bruikbaar(client, machine, ctx, herstelbronnen, tmp_path,
                                      monkeypatch, gebrek):
    from cryptography.fernet import Fernet

    from mediaguard.db import Database
    from mediaguard.secrets_store import SecretStore

    _, inrichting, _, forks, _ = herstelbronnen
    naam = apikey_secret(next(iter(forks)))
    verwacht = ctx.secrets.get(naam)
    assert verwacht
    # De standaardnaam blijft als hulpdatabase liggen naast de ingestelde primaire database.
    engine = tmp_path / "encode"
    primair = engine / "wachtrij.db"
    (engine / "app.db").rename(primair)
    with closing(sqlite3.connect(engine / "app.db")) as hulp:
        hulp.execute("CREATE TABLE hulp (id INTEGER)")
    monkeypatch.setenv("DATABASE_URL", f"sqlite:///{primair}")
    assert ub.vul(primair, "encode")["toegevoegd"]["job"] == 2
    taken = _av1_taken(primair)
    bewaar = tmp_path / "offsite"
    bewaar.mkdir()
    ctx.config.set("global", "", "backup.offsite.kind", "path", "proef")
    ctx.config.set("global", "", "backup.offsite.path", str(bewaar), "proef")
    headers = key(client)
    monkeypatch.setattr(backups, "CACHE_SECONDS", 0)

    def herstel_vanaf_doel(doel):
        gemaakt = client.post("/api/v1/backups/bundle")
        assert gemaakt.status_code == 200
        assert client.post("/api/v1/notify/offsite/run").json()["ok"]
        bundle.restore(bewaar / gemaakt.json()["file"], inrichting["recovery_code"],
                       bewaar / bundle.KEYFILE, doel)
        return rapport(machine, headers)

    goed = tmp_path / "volledig"
    dekking = herstel_vanaf_doel(goed)
    assert dekking["bundle"]["verified"] and dekking["offsite"]["verified"]
    with closing(Database(goed / "core/mediaguard.db")) as db:
        assert SecretStore(db, goed / "core/secret.key").get(naam) == verwacht
    assert _av1_taken(goed / "encode" / primair.name) == taken

    sleutel = ctx.settings.secret_key_path
    if gebrek in ("kernsleutel", "andere_sleutel"):
        sleutel.rename(tmp_path / "bewaarde-sleutel")
        if gebrek == "andere_sleutel":
            sleutel.write_bytes(Fernet.generate_key())
    elif gebrek == "primair":
        primair.rename(tmp_path / "bewaarde-taken.db")
    else:
        with closing(sqlite3.connect(primair)) as db:
            db.execute("DROP TABLE job")

    deels = tmp_path / "onvolledig"
    dekking = herstel_vanaf_doel(deels)
    assert not dekking["bundle"]["verified"] and not dekking["offsite"]["verified"]
    assert dekking["bundle"]["status"] == dekking["offsite"]["status"] == dekking["status"] \
        == "degraded"
    if gebrek in ("kernsleutel", "andere_sleutel"):
        with closing(Database(deels / "core/mediaguard.db")) as db:
            assert db.fetch_one("SELECT 1 FROM secrets WHERE name=?", (naam,))
            if gebrek == "andere_sleutel":
                assert SecretStore(db, deels / "core/secret.key").get(naam) is None
            else:
                assert not (deels / "core/secret.key").exists()
        assert _av1_taken(deels / "encode" / primair.name) == taken
    elif gebrek == "primair":
        assert not (deels / "encode" / primair.name).exists()
    else:
        with closing(sqlite3.connect(deels / "encode" / primair.name)) as db:
            assert not db.execute("SELECT 1 FROM sqlite_schema WHERE name='job'").fetchone()


@pytest.mark.parametrize("fase", ["klaarzetten", "zonder-primair", "omwisselen", "andere-primair"])
def test_klaargezette_primaire_database_behoudt_taken(client, machine, ctx, herstelbronnen,
                                                      tmp_path, monkeypatch, fase):
    monkeypatch.setenv("MG_AIO_SUPERVISOR", "1")
    core = ctx.settings.data_dir
    engine = tmp_path / "encode"
    primair = engine / "taken.db"
    (engine / "app.db").rename(primair)
    monkeypatch.setenv("DATABASE_URL", f"sqlite:///{primair}")
    assert ub.vul(primair, "encode")["toegevoegd"]["job"] == 2
    taken = _av1_taken(primair)
    with closing(sqlite3.connect(engine / "app.db")) as hulp:
        hulp.execute("CREATE TABLE schema_versie (versie INTEGER)")
        hulp.execute("INSERT INTO schema_versie VALUES (1)")
        hulp.commit()
    archief = preupdate.make_set(ctx.settings.backup_dir / preupdate.FOLDER, ctx.db.path,
                                encode_dir=engine)
    info = preupdate.stage(core, archief, actor="proef")
    headers = key(client)
    assert rapport(machine, headers)["rollback"]["staged"] is True
    marker = core / preupdate.STAGE_MARKER
    bij_aanvraag = fase in ("klaarzetten", "zonder-primair")
    if bij_aanvraag:
        # Een nieuwe onvolledige set mag de eerdere geldige aanvraag niet verdringen.
        gekozen = preupdate.make_set(archief.parent, ctx.db.path, encode_dir=engine)
        pad = gekozen / "encode" / primair.name
        manifest = gekozen / "meta.json"
        veld = "name"
    else:
        # Een oudere schrijver kon de onvolledige set mét geldige checksums publiceren.
        pad = primair.with_name(primair.name + preupdate.STAGED_SUFFIX)
        manifest = marker
        veld = "live"
    meta = json.loads(manifest.read_bytes())
    rij = next(r for r in meta["files"] if r[veld] == f"encode/{primair.name}")
    if fase == "andere-primair":
        monkeypatch.setenv("DATABASE_URL", f"sqlite:///{engine / 'app.db'}")
    else:
        if fase == "zonder-primair":
            pad.unlink()
            meta["files"].remove(rij)
        else:
            with closing(sqlite3.connect(pad)) as db:
                db.execute("DROP TABLE job")
            rij.update(size=pad.stat().st_size, sha256=hashlib.sha256(pad.read_bytes()).hexdigest())
        if fase == "omwisselen":
            # Markering in het formaat van vóór het fasebestand.
            meta.pop("voorbereiding", None)
            (core / preupdate.COMMIT_ORIGIN).unlink(missing_ok=True)
            meta["manifest"] = preupdate._manifest(info["set"], meta["files"])
        manifest.write_text(json.dumps(meta))
    bewaard = marker.read_bytes()
    staged = {p: p.read_bytes() for p in tmp_path.rglob("*.terugzetten")}

    if bij_aanvraag:
        antwoord = client.post(f"/api/v1/backups/{gekozen.name}/restore",
                               json={"confirm": gekozen.name})
        assert antwoord.status_code == 409 and "primaire AV1" in antwoord.text
    else:
        monkeypatch.setattr(backups, "CACHE_SECONDS", 0)
        assert rapport(machine, headers)["rollback"]["staged"] is None
        resultaat = preupdate.apply_staged(core)
        assert resultaat and not resultaat["ok"] and "Voortgangstabellen" in resultaat["error"]
    assert _av1_taken(primair) == taken
    assert marker.read_bytes() == bewaard
    assert all(p.read_bytes() == data for p, data in staged.items())
    assert not (ctx.settings.backup_dir / preupdate.ASIDE_FOLDER).exists()


def test_kernset_zonder_ingestelde_primaire_kopie(client, machine, ctx, tmp_path, monkeypatch):
    monkeypatch.setenv("MG_AIO_SUPERVISOR", "1")
    primair = tmp_path / "encode" / "taken.db"
    monkeypatch.setenv("DATABASE_URL", f"sqlite:///{primair}")
    archief = preupdate.make_set(ctx.settings.backup_dir / preupdate.FOLDER, ctx.db.path,
                                encode_dir=primair.parent)
    antwoord = client.post(f"/api/v1/backups/{archief.name}/restore",
                           json={"confirm": archief.name})
    assert antwoord.status_code == 409 and "primaire AV1" in antwoord.text
    assert not (ctx.settings.data_dir / preupdate.STAGE_MARKER).exists()
    item = rapport(machine, key(client))
    assert item["core"]["sha256"] is None and item["status"] == "degraded"


def test_set_met_afwijkend_herstelpad(client, machine, ctx, herstelbronnen, tmp_path, monkeypatch):
    monkeypatch.setenv("MG_AIO_SUPERVISOR", "1")
    backup, *_ = herstelbronnen
    kopie = ctx.settings.backup_dir / backup["file"]
    bron = tmp_path / "vorige-inrichting/core/mediaguard.db"
    bron.parent.mkdir(parents=True)
    bron.write_bytes(kopie.read_bytes())
    kopie.with_suffix(".json").unlink()
    setmap = preupdate.make_set(ctx.settings.backup_dir / preupdate.FOLDER, bron,
                                encode_dir=tmp_path / "encode")
    antwoord = client.post(f"/api/v1/backups/{setmap.name}/restore", json={"confirm": setmap.name})
    assert antwoord.status_code == 409 and "ongeldig databasepad" in antwoord.text
    item = rapport(machine, key(client))
    assert item["core"]["sha256"] is None and item["encoder"]["databases"] == []
    assert item["core"]["status"] == item["encoder"]["status"] == item["status"] == "degraded"


def test_deels_gemaakte_set_met_andere_geldige_kopie(
    client, machine, ctx, herstelbronnen, tmp_path, monkeypatch,
):
    monkeypatch.setenv("MG_AIO_SUPERVISOR", "1")
    vorige, *_ = herstelbronnen
    engine = tmp_path / "encode"
    with closing(sqlite3.connect(engine / "hulp.db")) as hulp:
        hulp.execute("CREATE TABLE hulp (id INTEGER)")
    kopieer = preupdate._copy_sqlite

    def leesfout(src, dest):
        if src.name == "hulp.db":
            raise preupdate.SetError("Gesimuleerde leesfout")
        return kopieer(src, dest)

    with monkeypatch.context() as proef:
        proef.setattr(preupdate, "_copy_sqlite", leesfout)
        deels = preupdate.make_set(ctx.settings.backup_dir / preupdate.FOLDER, ctx.db.path,
                                   encode_dir=engine)
    assert hashlib.sha256((deels / "mediaguard.db").read_bytes()).hexdigest() != vorige["sha256"]
    antwoord = client.post(f"/api/v1/backups/{deels.name}/restore", json={"confirm": deels.name})
    assert antwoord.status_code == 409 and "onvolledig" in antwoord.text
    item = rapport(machine, key(client))
    assert item["core"]["sha256"] == vorige["sha256"] and item["core"]["verified"]
    assert [db["sha256"] for db in item["encoder"]["databases"]] == [
        row["sha256"] for row in vorige["extra_files"]]
    assert item["encoder"]["verified"]
    naam = vorige["file"]
    assert client.post(f"/api/v1/backups/{naam}/restore", json={"confirm": naam}).status_code == 200


def test_direct_herstel_voor_migratie_120(populated_installation, tmp_path):
    from mediaguard.apikeys import KEY_PREFIX, key_hash

    ctx, admin, _ = populated_installation
    for instantie in list_instances(ctx):
        ctx.services.setdefault("arr_transports", {})[instantie.id] = httpx.MockTransport(
            lambda _: httpx.Response(200, json=[]))
    fixture = ROOT / "tests/fixtures/aio"
    archief = ctx.settings.backup_dir / "mediaguard-vorige-release.db"
    archief.parent.mkdir(exist_ok=True)
    leessleutel = KEY_PREFIX + secrets.token_urlsafe(32)
    with closing(sqlite3.connect(archief)) as db, db:
        db.executescript((fixture / "core-1.2.2.sql").read_text())
        assert not db.execute("SELECT 1 FROM sqlite_schema WHERE name='offsite_target_generation'"
                              ).fetchone()
        assert db.execute("UPDATE api_keys SET key_hash=? WHERE id=1 AND role='viewer'",
                          (key_hash(leessleutel),)).rowcount == 1
        for veld, waarde in (("kind", "path"), ("path", str(tmp_path))):
            db.execute("INSERT INTO settings_values VALUES ('global','',?,?,?,NULL)",
                       (f"backup.offsite.{veld}", json.dumps(waarde), utcnow()))
        overdrachten = db.execute("SELECT * FROM mediaserver_pending WHERE done_at IS NULL")\
            .fetchall()
    assert overdrachten
    assert admin.get(BACKUP_REPORT_ROUTE).status_code == 200
    antwoord = admin.post(f"/api/v1/backups/{archief.name}/restore", json={"confirm": archief.name})
    assert antwoord.status_code == 200
    toegang = json.loads((fixture / "core-1.2.2.json").read_text())
    login = {"username": toegang["user"], "password": toegang["password"]}
    assert admin.post("/api/v1/auth/login", json=login).is_success
    ctx.db.execute("UPDATE sessions SET last_seen_at=NULL")
    voor = database_snapshot(ctx)
    for headers in ({}, {"X-Api-Key": leessleutel}):
        item = rapport(admin, headers)
        assert item["offsite"]["target_kind"] == "path" and not item["offsite"]["verified"]
        assert item["offsite"]["status"] == item["status"] == "degraded"
    assert_database_preserved(ctx, voor)
    na = ctx.db.fetch_all("SELECT * FROM mediaserver_pending WHERE done_at IS NULL")
    assert [tuple(r) for r in na] == overdrachten


@pytest.mark.parametrize("ontbreekt", ["complete", "proefherstel"])
def test_oud_aanmaakbewijs_bewijst_geen_bundelinhoud(
    client, machine, ctx, herstelbronnen, tmp_path, monkeypatch, respx_mock, ontbreekt,
):
    _, inrichting, *_ = herstelbronnen
    schrijf_json = json.dumps

    def historisch_formaat(obj, **kwargs):
        # Ook een historisch complete=true bewijst geen proefherstel.
        if isinstance(obj, dict) and "eph" in obj:
            obj = {k: v for k, v in obj.items() if k != ontbreekt}
        return schrijf_json(obj, **kwargs)

    with monkeypatch.context() as oud:
        oud.setattr(bundle.json, "dumps", historisch_formaat)
        gemaakt = client.post("/api/v1/backups/bundle")
    assert gemaakt.status_code == 200
    naam = gemaakt.json()["file"]
    pad = bundle.bundle_dir(ctx) / naam
    sleutel = pad.parent / bundle.KEYFILE
    assert bundle.has_pair_proof(pad)
    assert "core/mediaguard.db" in bundle.restore(pad, inrichting["recovery_code"], sleutel,
                                                  tmp_path / "oude-bundel")
    doel = "https://bundel.invalid/kopie"
    ctx.config.set("global", "", "backup.offsite.kind", "webdav", "proef")
    ctx.config.set("global", "", "backup.offsite.webdav_url", doel, "proef")
    for bron in (pad, sleutel):
        respx_mock.put(f"{doel}/{bron.name}").respond(201)
        respx_mock.get(f"{doel}/{bron.name}").respond(200, content=bron.read_bytes())
    assert client.post("/api/v1/notify/offsite/run").json()["ok"]
    item = rapport(machine, key(client))
    assert not item["offsite"]["verified"] and not item["bundle"]["verified"]
    assert item["status"] == "degraded"
