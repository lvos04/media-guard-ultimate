"""API-sleutels (X-Api-Key): machine-toegang met rol viewer/operator, nooit admin."""

from __future__ import annotations

import logging
from datetime import UTC, datetime, timedelta

import pytest
from fastapi.testclient import TestClient

from mediaguard import apikeys

INVALID = {"detail": "Ongeldige of ingetrokken API-sleutel"}


def _create(client, name="mso", role="operator", **extra):  # type: ignore[no-untyped-def]
    r = client.post("/api/v1/auth/api-keys", json={"name": name, "role": role, **extra})
    assert r.status_code == 200, r.text
    return r.json()


@pytest.fixture
def machine(app):  # type: ignore[no-untyped-def]
    """Client zonder cookie/CSRF, zoals MSO."""
    with TestClient(app) as c:
        yield c


@pytest.fixture
def fake_search(monkeypatch):  # type: ignore[no-untyped-def]
    from mediaguard.wanted import api as wanted_api

    calls: list[tuple[list[int], str]] = []

    async def search_now(ctx, ids, actor, wait_seconds=0):  # type: ignore[no-untyped-def]
        calls.append((ids, actor))
        return {"results": [{"wanted_id": ids[0], "status": "searched"}]}

    monkeypatch.setattr(wanted_api.backfill, "search_now", search_now)
    monkeypatch.setattr(wanted_api.engine, "get_wanted", lambda ctx, wid: {"id": wid})
    return calls


def test_create_returns_key_once_and_list_never(client):  # type: ignore[no-untyped-def]
    made = _create(client)
    assert made["key"].startswith("mgk_") and len(made["key"]) > 40
    assert made["prefix"] == made["key"][:8] and made["role"] == "operator"
    assert made["status"] == "actief"
    listed = client.get("/api/v1/auth/api-keys").json()["items"]
    assert listed and all("key" not in i and "key_hash" not in i for i in listed)
    assert listed[0]["name"] == "mso" and listed[0]["prefix"] == made["prefix"]


def test_admin_role_refused(client):  # type: ignore[no-untyped-def]
    r = client.post("/api/v1/auth/api-keys", json={"name": "root", "role": "admin"})
    assert r.status_code == 422


def test_duplicate_name_conflict(client):  # type: ignore[no-untyped-def]
    _create(client)
    assert client.post("/api/v1/auth/api-keys",
                       json={"name": "mso", "role": "viewer"}).status_code == 409


def test_key_stored_only_as_hash(client, ctx):  # type: ignore[no-untyped-def]
    key = _create(client)["key"]
    row = ctx.db.fetch_one("SELECT * FROM api_keys")
    assert row["key_hash"] == apikeys.key_hash(key) and row["prefix"] == key[:8]
    for table in ("api_keys", "audit_events"):
        for r in ctx.db.fetch_all(f"SELECT * FROM {table}"):  # noqa: S608
            assert key not in " ".join(str(v) for v in dict(r).values())


def test_operator_key_reads_and_searches_without_csrf(client, machine, ctx,  # type: ignore[no-untyped-def]
                                                       fake_search):
    key = _create(client)["key"]
    h = {"X-Api-Key": key}
    assert machine.get("/api/v1/wanted", headers=h).status_code == 200
    r = machine.post("/api/v1/wanted/7/search", headers=h, json={})
    assert r.status_code == 200, r.text
    assert fake_search == [([7], "apikey:mso")]
    me = machine.get("/api/v1/auth/me", headers=h).json()
    assert me["username"] == "apikey:mso" and me["role"] == "operator"
    assert me["csrf_token"] is None and me["preferences"] == {}
    assert ctx.db.fetch_one("SELECT last_used_at FROM api_keys")["last_used_at"]


def test_viewer_key_cannot_search(client, machine, fake_search):  # type: ignore[no-untyped-def]
    key = _create(client, name="lezer", role="viewer")["key"]
    h = {"X-Api-Key": key}
    assert machine.get("/api/v1/wanted", headers=h).status_code == 200
    assert machine.post("/api/v1/wanted/7/search", headers=h, json={}).status_code == 403
    assert fake_search == []


def test_key_never_reaches_admin_routes(client, machine):  # type: ignore[no-untyped-def]
    h = {"X-Api-Key": _create(client)["key"]}
    assert machine.get("/api/v1/auth/api-keys", headers=h).status_code == 403
    assert machine.post("/api/v1/auth/api-keys", headers=h,
                        json={"name": "x2", "role": "viewer"}).status_code == 403
    assert machine.get("/api/v1/auth/users", headers=h).status_code == 403


def test_revoked_key_rejected_immediately(client, machine):  # type: ignore[no-untyped-def]
    made = _create(client)
    h = {"X-Api-Key": made["key"]}
    assert machine.get("/api/v1/auth/me", headers=h).status_code == 200
    r = client.post(f"/api/v1/auth/api-keys/{made['id']}/revoke")
    assert r.status_code == 200 and r.json()["revoked_at"] and r.json()["status"] == "ingetrokken"
    r = machine.get("/api/v1/auth/me", headers=h)
    assert r.status_code == 401 and r.json() == INVALID


def test_expired_key_rejected(client, machine, ctx):  # type: ignore[no-untyped-def]
    made = _create(client, expires_in_days=1)
    past = (datetime.now(UTC) - timedelta(minutes=1)).isoformat(timespec="seconds")
    ctx.db.execute("UPDATE api_keys SET expires_at=? WHERE id=?", (past, made["id"]))
    r = machine.get("/api/v1/wanted", headers={"X-Api-Key": made["key"]})
    assert r.status_code == 401 and r.json() == INVALID


def test_unknown_key_no_cookie_fallback(client):  # type: ignore[no-untyped-def]
    # 'client' heeft een geldige admin-sessie + CSRF; een foute sleutel mag die niet maskeren
    assert client.get("/api/v1/auth/me").status_code == 200
    r = client.get("/api/v1/auth/me", headers={"X-Api-Key": "mgk_" + "x" * 43})
    assert r.status_code == 401 and r.json() == INVALID
    r = client.get("/api/v1/auth/me", headers={"X-Api-Key": "geen-sleutel"})
    assert r.status_code == 401


def test_rejections_audited_without_key(client, machine, ctx):  # type: ignore[no-untyped-def]
    bad = "mgk_" + "q" * 43
    machine.get("/api/v1/wanted", headers={"X-Api-Key": bad})
    rows = ctx.db.fetch_all("SELECT * FROM audit_events WHERE action='apikey.rejected'")
    assert rows and all(bad not in " ".join(str(v) for v in dict(r).values()) for r in rows)
    made = _create(client, name="weg")
    client.post(f"/api/v1/auth/api-keys/{made['id']}/revoke")
    actions = {r["action"] for r in ctx.db.fetch_all("SELECT action FROM audit_events")}
    assert {"apikey.created", "apikey.revoked", "apikey.rejected"} <= actions


def test_rate_limit_per_ip(client, machine):  # type: ignore[no-untyped-def]
    for _ in range(apikeys.FAIL_LIMIT):
        assert machine.get("/api/v1/wanted", headers={"X-Api-Key": "mgk_fout"}).status_code == 401
    r = machine.get("/api/v1/wanted", headers={"X-Api-Key": "mgk_fout"})
    assert r.status_code == 429 and int(r.headers["Retry-After"]) > 0
    # ook een geldige sleutel moet even wachten vanaf dit adres
    key = _create(client)["key"]
    assert machine.get("/api/v1/wanted", headers={"X-Api-Key": key}).status_code == 429
    # ander IP (via de eigen proxy) is niet geblokkeerd
    ok = machine.get("/api/v1/wanted", headers={"X-Api-Key": key,
                                                 "X-Forwarded-For": "10.0.0.9"})
    assert ok.status_code == 200


def test_last_used_written_at_most_once_per_minute(client, machine, ctx):  # type: ignore[no-untyped-def]
    key = _create(client)["key"]
    h = {"X-Api-Key": key}
    machine.get("/api/v1/auth/me", headers=h)
    first = ctx.db.fetch_one("SELECT last_used_at FROM api_keys")["last_used_at"]
    old = (datetime.now(UTC) - timedelta(seconds=30)).isoformat(timespec="seconds")
    ctx.db.execute("UPDATE api_keys SET last_used_at=?", (old,))
    machine.get("/api/v1/auth/me", headers=h)
    assert ctx.db.fetch_one("SELECT last_used_at FROM api_keys")["last_used_at"] == old
    older = (datetime.now(UTC) - timedelta(seconds=90)).isoformat(timespec="seconds")
    ctx.db.execute("UPDATE api_keys SET last_used_at=?", (older,))
    machine.get("/api/v1/auth/me", headers=h)
    assert ctx.db.fetch_one("SELECT last_used_at FROM api_keys")["last_used_at"] >= first


def test_logs_redact_keys(app, caplog):  # type: ignore[no-untyped-def]
    key = apikeys.generate_key()
    log = logging.getLogger("mediaguard.test")
    with caplog.at_level(logging.INFO):
        log.info("verzoek met X-Api-Key: %s", key)
        log.info("losse sleutel %s in tekst", key)
        log.warning("headers=%s", {"X-Api-Key": key})
    text = caplog.text
    assert key not in text and "***" in text


def test_existing_auth_paths_unchanged(client, app):  # type: ignore[no-untyped-def]
    with TestClient(app) as c:
        r = c.get("/api/v1/auth/me", headers={"X-MediaGuard-Token": "test-internal-token"})
        assert r.status_code == 200 and r.json()["role"] == "admin"
        assert c.get("/api/v1/auth/me").status_code == 401
    # sessie zonder CSRF blijft geweigerd bij een muterend verzoek
    token = client.headers.pop("X-CSRF-Token")
    assert client.post("/api/v1/auth/api-keys",
                       json={"name": "zonder-csrf", "role": "viewer"}).status_code == 403
    client.headers["X-CSRF-Token"] = token


def test_openapi_documents_api_key(client):  # type: ignore[no-untyped-def]
    # API-documentatie alleen voor een ingelogde beheerder (item 33)
    spec = client.get("/api/v1/openapi.json").json()
    scheme = spec["components"]["securitySchemes"]["apiKey"]
    assert scheme == {**scheme, "type": "apiKey", "in": "header", "name": "X-Api-Key"}
    paths = spec["paths"]
    for p in ("/api/v1/health", "/api/v1/auth/me", "/api/v1/status", "/api/v1/wanted",
              "/api/v1/wanted/kpis", "/api/v1/wanted/reasons", "/api/v1/wanted/backfill/quota",
              "/api/v1/wanted/{wanted_id}", "/api/v1/wanted/{wanted_id}/diagnosis",
              "/api/v1/policy/effective"):
        assert p in paths, p
    assert "post" in paths["/api/v1/wanted/{wanted_id}/search"]
