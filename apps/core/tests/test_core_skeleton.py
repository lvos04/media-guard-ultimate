from __future__ import annotations

import pytest

from mediaguard import jobs
from mediaguard.secrets_store import redact


def test_health_without_auth(anon_client):
    assert anon_client.get("/api/v1/health").json()["status"] == "ok"


def test_status_requires_login(anon_client):
    assert anon_client.get("/api/v1/status").status_code == 401


def test_setup_only_once(client):
    r = client.post("/api/v1/auth/setup", json={"username": "x2", "password": "nogeenwachtwoord"})
    assert r.status_code == 409


def test_csrf_required_for_mutations(client):
    headers = dict(client.headers)
    del client.headers["X-CSRF-Token"]
    r = client.post("/api/v1/system/emergency-stop", json={"active": True})
    assert r.status_code == 403
    client.headers.update(headers)


def test_viewer_cannot_mutate(client):
    client.post("/api/v1/auth/users", json={"username": "kijker", "password": "kijkwachtwoord1",
                                             "role": "viewer"})
    from fastapi.testclient import TestClient
    with TestClient(client.app) as other:
        r = other.post("/api/v1/auth/login", json={"username": "kijker",
                                                   "password": "kijkwachtwoord1"})
        other.headers["X-CSRF-Token"] = r.json()["csrf_token"]
        assert other.get("/api/v1/status").status_code == 200
        assert other.post("/api/v1/system/emergency-stop", json={"active": True}).status_code == 403


def test_settings_schema_effective_and_inheritance(client):
    schema = client.get("/api/v1/settings/schema").json()
    keys = {s["key"] for s in schema["settings"]}
    assert "ui.theme" in keys
    r = client.patch("/api/v1/settings/scoped", json={"changes": [
        {"scope_type": "global", "key": "ui.theme", "value": "light"}]})
    assert r.status_code == 200
    eff = client.get("/api/v1/settings/effective?keys=ui.theme").json()[0]
    assert eff["value"] == "light" and eff["source"] == "global"
    bad = client.patch("/api/v1/settings/scoped", json={"changes": [
        {"scope_type": "global", "key": "ui.theme", "value": "paars"}]})
    assert bad.status_code == 422
    # ongeldige wijziging laat bestaande configuratie intact (Q3.14)
    assert client.get("/api/v1/settings/effective?keys=ui.theme").json()[0]["value"] == "light"
    audit = client.get("/api/v1/audit?action=settings").json()
    assert audit and audit[0]["new_value"] == "light"


def test_integrity_settings_cannot_be_disabled(client):
    r = client.patch("/api/v1/settings/scoped", json={"changes": [
        {"key": "integrity.require_recoverable_copy", "value": False}]})
    assert r.status_code == 422


def test_simulate_does_not_persist(client):
    r = client.post("/api/v1/settings/simulate", json={"changes": [
        {"key": "jobs.max_concurrency", "value": 9}]}).json()
    assert r["before"]["jobs.max_concurrency"] == 4 and r["after"]["jobs.max_concurrency"] == 9
    eff = client.get("/api/v1/settings/effective?keys=jobs.max_concurrency").json()[0]
    assert eff["value"] == 4


def test_secret_never_returned(client, ctx):
    client.put("/api/v1/settings/secrets/ai:anthropic", json={"value": "sk-ant-supergeheim123456"})
    listing = client.get("/api/v1/settings/secrets").text
    assert "supergeheim" not in listing
    assert ctx.secrets.get("ai:anthropic") == "sk-ant-supergeheim123456"


def test_redact():
    assert "abc123" not in redact("http://x/api?apikey=abc123&t=1")
    assert "geheim" not in redact('{"apiKey": "geheim"}')
    assert "sk-ant-" not in redact("key sk-ant-api03-abcdefghijklmnop")


async def test_job_idempotency_retry_and_emergency_stop(ctx):
    calls = []

    @jobs.job_handler("test.ok")
    async def ok(jc):  # type: ignore[no-untyped-def]
        calls.append(jc.payload)
        return {"done": True}

    @jobs.job_handler("optimize.test")
    async def mutating(jc):  # type: ignore[no-untyped-def]
        calls.append("mutating")
        return None

    a = jobs.enqueue(ctx.db, "test.ok", {"n": 1}, idempotency_key="k1")
    b = jobs.enqueue(ctx.db, "test.ok", {"n": 1}, idempotency_key="k1")
    assert a == b
    ctx.state_set("emergency_stop", {"active": True})
    m = jobs.enqueue(ctx.db, "optimize.test")
    runner = jobs.JobRunner(ctx)
    await runner.run_until_idle()
    assert calls == [{"n": 1}]
    assert ctx.db.fetch_one("SELECT status FROM jobs WHERE id=?", (m,))["status"] == "queued"
    ctx.state_set("emergency_stop", {"active": False})
    await runner.run_until_idle()
    assert "mutating" in calls


async def test_job_recovers_after_restart(ctx):
    jid = jobs.enqueue(ctx.db, "test.ok")
    ctx.db.execute("UPDATE jobs SET status='running' WHERE id=?", (jid,))
    assert jobs.JobRunner(ctx).recover_after_restart() == 1


@pytest.mark.parametrize("scope", ["instance", "series"])
def test_scope_validation(ctx, scope):
    with pytest.raises(ValueError):
        ctx.config.set(scope, "x", "ui.theme", "dark", "t")
