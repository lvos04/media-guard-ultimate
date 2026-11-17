"""API-sleutels (X-Api-Key): machine-toegang met rol viewer/operator, nooit admin."""

from __future__ import annotations

import importlib.util
import json
import logging
import re
import shutil
from datetime import UTC, datetime, timedelta
from pathlib import Path

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


# -- rechten (scopes) op API-sleutels (S-P3-600, C1.3-MGU-05) ---------------------------------
# Besluit van de regisseur (2026-11-17, optie a): rechten alleen op rol viewer. Zo'n sleutel mag
# GET/HEAD als kijker plus precies wat zijn rechten openen; elke andere route met een andere
# methode geeft 403. NULL (geen rechten) = precies het oude gedrag. Enig recht: emergency_stop:on.
ROOT = Path(__file__).resolve().parents[3]
ESTOP = "/api/v1/system/emergency-stop"
SCOPE = "emergency_stop:on"
DENIED = {"detail": "Deze API-sleutel heeft beperkte rechten (scopes) en mag dit niet"}
NOOIT_OPHEFFEN = {"detail": "Deze API-sleutel mag de noodstop alleen aanzetten, nooit opheffen"}
OPERATOR_NODIG = {"detail": "Rol 'operator' vereist"}
ROL_EN_RECHTEN = ("Rechten (scopes) kunnen alleen op een sleutel met rol kijker. Kies rol kijker "
                  "of laat de rechten leeg.")


def _hub(client, name="hub", scopes=(SCOPE,)):  # type: ignore[no-untyped-def]
    return _create(client, name=name, role="viewer", scopes=list(scopes))


def _estop(ctx) -> dict:  # type: ignore[no-untyped-def]
    return ctx.state_get("emergency_stop", {"active": False}) or {}


def _routes_script():  # type: ignore[no-untyped-def]
    spec = importlib.util.spec_from_file_location(
        "platform_v2_routes", ROOT / "scripts" / "platform-v2-routes.py")
    assert spec and spec.loader
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def test_migratie_005_voegt_alleen_de_kolom_scopes_toe(ctx):  # type: ignore[no-untyped-def]
    sql = (ROOT / "apps/core/mediaguard/migrations/005_api_key_scopes.sql").read_text("utf-8")
    body = "\n".join(line.split("--", 1)[0] for line in sql.splitlines())
    statements = [" ".join(s.split()) for s in body.split(";") if s.strip()]
    assert statements == ["ALTER TABLE api_keys ADD COLUMN scopes TEXT NULL"]
    cols = {r["name"]: r for r in ctx.db.fetch_all("PRAGMA table_info(api_keys)")}
    assert list(cols)[-1] == "scopes"
    assert cols["scopes"]["notnull"] == 0 and cols["scopes"]["dflt_value"] is None
    assert ctx.db.fetch_one("SELECT 1 AS x FROM schema_migrations WHERE "
                            "version='005_api_key_scopes'")


def test_sleutel_met_noodstoprecht_leest_en_zet_de_noodstop_aan(client, machine, ctx):  # type: ignore[no-untyped-def]
    made = _hub(client)
    assert made["role"] == "viewer" and made["scopes"] == [SCOPE]
    row = ctx.db.fetch_one("SELECT scopes FROM api_keys WHERE id=?", (made["id"],))
    assert json.loads(row["scopes"]) == [SCOPE]
    h = {"X-Api-Key": made["key"]}
    me = machine.get("/api/v1/auth/me", headers=h).json()
    assert me["username"] == "apikey:hub" and me["role"] == "viewer" and me["scopes"] == [SCOPE]
    assert machine.get("/api/v1/status", headers=h).status_code == 200
    r = machine.post(ESTOP, headers=h, json={"active": True, "reason": "Hub: globale stop"})
    assert r.status_code == 200, r.text
    assert r.json()["active"] is True and r.json()["by"] == "apikey:hub"
    assert _estop(ctx)["active"] is True
    audit = ctx.db.fetch_all("SELECT actor FROM audit_events WHERE action='system.emergency_stop'")
    assert [a["actor"] for a in audit] == ["apikey:hub"]
    # nog een keer aanzetten mag (de Hub herhaalt een verzoek); de noodstop blijft aan
    assert machine.post(ESTOP, headers=h, json={"active": True}).status_code == 200
    assert _estop(ctx)["active"] is True


def test_noodstoprecht_mag_de_noodstop_nooit_opheffen(client, machine, ctx):  # type: ignore[no-untyped-def]
    """(a) Met emergency_stop:on en active=false: 403, op beide wegen (V-055), niets gewijzigd."""
    h = {"X-Api-Key": _hub(client)["key"]}
    # noodstop staat uit: ook dan geen schrijfactie
    r = machine.post(ESTOP, headers=h, json={"active": False})
    assert r.status_code == 403 and r.json() == NOOIT_OPHEFFEN
    assert ctx.state_get("emergency_stop") is None
    assert client.post(ESTOP, json={"active": True, "reason": "beheerder"}).status_code == 200
    before = _estop(ctx)
    audits = ctx.db.fetch_one("SELECT COUNT(*) AS n FROM audit_events")["n"]
    r = machine.post(ESTOP, headers=h, json={"active": False, "reason": "Hub"})
    assert r.status_code == 403 and r.json() == NOOIT_OPHEFFEN
    # tweede weg: de app-bediening roept dezelfde functie aan
    r = machine.post("/api/v1/app/control", headers=h,
                     json={"action": "emergency_stop_off", "confirm": True})
    assert r.status_code == 403
    r = machine.post("/api/v1/app/control", headers=h,
                     json={"action": "emergency_stop_on", "confirm": True})
    assert r.status_code == 403 and r.json() == DENIED
    assert _estop(ctx) == before and before["active"] is True
    assert ctx.db.fetch_one("SELECT COUNT(*) AS n FROM audit_events")["n"] == audits


def test_wie_de_noodstop_mag_zetten() -> None:
    """De regel in emergency_stop zelf (dekt ook POST /api/v1/app/control)."""
    from mediaguard.api.system import may_set_emergency_stop
    from mediaguard.auth import Principal

    def p(role: str, scopes: tuple[str, ...] | None) -> Principal:
        return Principal(username="apikey:x", role=role, machine=True, scopes=scopes)

    for active in (True, False):
        assert may_set_emergency_stop(p("operator", None), active)
        assert may_set_emergency_stop(p("admin", None), active)
        assert not may_set_emergency_stop(p("viewer", None), active)
        assert not may_set_emergency_stop(p("viewer", ()), active)
        assert not may_set_emergency_stop(p("viewer", ("wanted:search",)), active)
        assert not may_set_emergency_stop(p("operator", (SCOPE,)), active)
        assert not may_set_emergency_stop(p("admin", (SCOPE,)), active)
    assert may_set_emergency_stop(p("viewer", (SCOPE,)), True)
    assert not may_set_emergency_stop(p("viewer", (SCOPE,)), False)


def test_sleutel_met_rechten_krijgt_403_op_elke_andere_schrijvende_route(  # type: ignore[no-untyped-def]
        app, client, machine, ctx):
    """(b) Elke route uit de routetabel (scripts/platform-v2-routes.py) met een andere methode
    dan GET/HEAD: 403 met de vaste tekst. Routes zonder aanmelding (rol 'geen': inloggen,
    hooks) kijken niet naar X-Api-Key: daar geeft de sleutel precies hetzelfde als zonder."""
    routes = _routes_script().collect(app)
    h = {"X-Api-Key": _hub(client)["key"]}
    getoetst, openbaar, opened = 0, 0, []
    for r in routes:
        for method in r["methods"]:
            if method in ("GET", "HEAD", "WS"):
                continue
            if r["scopes"]:
                opened.append((method, r["path"], r["scopes"]))
                continue
            path = re.sub(r"\{[^}]+\}", "1", r["path"])
            got = machine.request(method, path, headers=h, json={})
            if r["role"] == "geen":
                anon = machine.request(method, path, json={})
                assert got.status_code == anon.status_code, (method, r["path"])
                openbaar += 1
            else:
                assert got.status_code == 403, (method, r["path"], got.status_code, got.text)
                assert got.json() == DENIED, (method, r["path"])
                getoetst += 1
    assert opened == [("POST", ESTOP, [SCOPE])]
    # ondergrens (V-005): de routetabel had op 2026-11-17 ruim 230 schrijvende routes met aanmelding
    assert getoetst >= 200 and openbaar >= 5
    assert _estop(ctx)["active"] is False
    assert not ctx.db.fetch_all("SELECT 1 FROM audit_events WHERE actor='apikey:hub'")


def test_sleutel_met_rechten_leest_als_kijker_en_mag_niet_wat_een_kijker_mag_schrijven(  # type: ignore[no-untyped-def]
        client, machine, fake_search):
    scoped = {"X-Api-Key": _hub(client)["key"]}
    plain = {"X-Api-Key": _create(client, name="lezer", role="viewer")["key"]}
    for path in ("/api/v1/status", "/api/v1/wanted", "/api/v1/audit?limit=1",
                 "/api/v1/notifications?limit=1", "/api/v1/auth/api-keys", "/api/v1/auth/users",
                 "/api/v1/openapi.json"):
        a = machine.get(path, headers=scoped)
        b = machine.get(path, headers=plain)
        assert a.status_code == b.status_code, path
    assert machine.get("/api/v1/auth/api-keys", headers=scoped).status_code == 403
    # wat een gewone kijker wel mag schrijven, mag de sleutel met rechten niet
    assert machine.post("/api/v1/notifications/read-all", headers=plain, json={}).status_code == 200
    r = machine.post("/api/v1/notifications/read-all", headers=scoped, json={})
    assert r.status_code == 403 and r.json() == DENIED
    assert machine.post("/api/v1/policy/simulate", headers=plain, json={}).status_code == 422
    assert machine.post("/api/v1/policy/simulate", headers=scoped, json={}).status_code == 403
    assert machine.post("/api/v1/wanted/7/search", headers=scoped, json={}).status_code == 403
    assert fake_search == []


@pytest.mark.parametrize("scope", ["wanted:search", "emergency_stop:off", "admin",
                                   "EMERGENCY_STOP:ON", "emergency_stop:on ", "", "x" * 300])
def test_onbekend_recht_bij_aanmaken_geweigerd(client, ctx, scope):  # type: ignore[no-untyped-def]
    """(c) Onbekend recht: 422 met een Nederlandse tekst (geen lijst met pydantic-fouten);
    wanted:search is onbekend zolang BG-05 open is. Er wordt niets aangemaakt."""
    r = client.post("/api/v1/auth/api-keys",
                    json={"name": "hub", "role": "viewer", "scopes": [SCOPE, scope]})
    assert r.status_code == 422
    assert r.json() == {"detail": f"Onbekend recht (scope) '{scope[:64]}'. Toegestaan: {SCOPE}."}
    assert ctx.db.fetch_one("SELECT COUNT(*) AS n FROM api_keys")["n"] == 0
    assert not ctx.db.fetch_all("SELECT 1 FROM audit_events WHERE action='apikey.created'")


@pytest.mark.parametrize("body", [{"role": "operator", "scopes": [SCOPE]}, {"scopes": [SCOPE]}])
def test_rechten_op_een_operator_sleutel_geweigerd(client, ctx, body):  # type: ignore[no-untyped-def]
    r = client.post("/api/v1/auth/api-keys", json={"name": "hub", **body})
    assert r.status_code == 422 and r.json() == {"detail": ROL_EN_RECHTEN}
    assert ctx.db.fetch_one("SELECT COUNT(*) AS n FROM api_keys")["n"] == 0


def test_lege_rechtenlijst_is_null_en_een_gewone_kijker(client, machine, ctx):  # type: ignore[no-untyped-def]
    made = _create(client, name="lezer", role="viewer", scopes=[])
    assert made["scopes"] is None
    assert ctx.db.fetch_one("SELECT scopes FROM api_keys")["scopes"] is None
    h = {"X-Api-Key": made["key"]}
    assert machine.get("/api/v1/auth/me", headers=h).json()["scopes"] is None
    assert machine.post("/api/v1/notifications/read-all", headers=h, json={}).status_code == 200
    r = machine.post(ESTOP, headers=h, json={"active": True})
    assert r.status_code == 403 and r.json() == OPERATOR_NODIG
    # operator met een lege lijst: gewoon een operator-sleutel (NULL)
    op = _create(client, name="mso", role="operator", scopes=[])
    assert op["scopes"] is None


def test_bestaande_sleutels_werken_na_migratie_005_precies_als_ervoor(  # type: ignore[no-untyped-def]
        tmp_path, monkeypatch, fake_search):
    """(d) Een database van vóór 005 met sleutels zoals 1.2.2 ze maakte (MSO-operator, kijker,
    app-sleutel): na de migratie zijn de rijen gelijk, scopes is NULL en elke sleutel doet
    precies wat hij deed."""
    from mediaguard import db as dbmod
    from mediaguard.app import create_app
    from mediaguard.config import load_settings

    oud = tmp_path / "migraties-zonder-005"
    oud.mkdir()
    for f in dbmod.MIGRATIONS_DIR.glob("*.sql"):
        if f.name != "005_api_key_scopes.sql":
            shutil.copy(f, oud / f.name)
    settings = load_settings(data_dir=tmp_path / "config", staging_dir=tmp_path / "staging",
                             quarantine_dir=tmp_path / "quarantine", start_background=False,
                             internal_token="test-internal-token")
    echte_map = dbmod.MIGRATIONS_DIR
    monkeypatch.setattr(dbmod, "MIGRATIONS_DIR", oud)
    old = dbmod.Database(settings.db_path)
    old.initialize()
    assert "scopes" not in [c["name"] for c in old.fetch_all("PRAGMA table_info(api_keys)")]
    keys: dict[str, str] = {}
    for name, role in (("mso", "operator"), ("lezer", "viewer"),
                       ("android-app 2026-11-01 10:00 ab12", "operator")):
        keys[name] = k = apikeys.generate_key()
        old.execute("INSERT INTO api_keys(name,prefix,key_hash,role,created_by,created_at) "
                    "VALUES (?,?,?,?,?,?)", (name, k[:8], apikeys.key_hash(k), role,
                                             "user:admin", "2026-11-01T10:00:00+00:00"))
    before = [dict(r) for r in old.fetch_all("SELECT * FROM api_keys ORDER BY id")]
    old.close()
    monkeypatch.setattr(dbmod, "MIGRATIONS_DIR", echte_map)
    app = create_app(settings)
    try:
        ctx = app.state.ctx
        assert ctx.db.fetch_one("SELECT 1 AS x FROM schema_migrations WHERE "
                                "version='005_api_key_scopes'")
        after = [dict(r) for r in ctx.db.fetch_all("SELECT * FROM api_keys ORDER BY id")]
        assert [{k: v for k, v in r.items() if k != "scopes"} for r in after] == before
        assert all(r["scopes"] is None for r in after)
        ctx.config.set("global", "", "library.write_enabled", True, "test")
        with TestClient(app) as m:
            mso = {"X-Api-Key": keys["mso"]}
            lezer = {"X-Api-Key": keys["lezer"]}
            telefoon = {"X-Api-Key": keys["android-app 2026-11-01 10:00 ab12"]}
            for h, role in ((mso, "operator"), (lezer, "viewer"), (telefoon, "operator")):
                me = m.get("/api/v1/auth/me", headers=h).json()
                assert me["role"] == role and me["scopes"] is None
                assert m.get("/api/v1/status", headers=h).status_code == 200
            # MSO: herzoek en noodstop aan én uit, zoals in 1.2.2
            assert m.post("/api/v1/wanted/7/search", headers=mso, json={}).status_code == 200
            assert m.post(ESTOP, headers=mso, json={"active": True}).status_code == 200
            assert m.post(ESTOP, headers=mso, json={"active": False}).status_code == 200
            # kijker: zelfde weigering als vroeger, en zijn eigen schrijfroutes blijven open
            r = m.post(ESTOP, headers=lezer, json={"active": True})
            assert r.status_code == 403 and r.json() == OPERATOR_NODIG
            assert m.post("/api/v1/wanted/7/search", headers=lezer, json={}).status_code == 403
            assert m.post("/api/v1/notifications/read-all", headers=lezer,
                          json={}).status_code == 200
            # app-sleutel: noodstop via de app-bediening, aan en uit
            for action in ("emergency_stop_on", "emergency_stop_off"):
                r = m.post("/api/v1/app/control", headers=telefoon,
                           json={"action": action, "confirm": True})
                assert r.status_code == 200, r.text
            assert m.post("/api/v1/app/paired", headers=telefoon, json={}).status_code == 200
            # alleen de app-sleutel mag het telefoonkanaal: MSO (ook operator) niet
            assert m.post("/api/v1/app/paired", headers=mso, json={}).status_code == 403
        assert fake_search == [([7], "apikey:mso")]
        assert _estop(ctx)["active"] is False
    finally:
        app.state.ctx.db.close()


def test_ingetrokken_of_verlopen_sleutel_met_recht_geeft_401(client, machine, ctx):  # type: ignore[no-untyped-def]
    """(e) Intrekken werkt ook voor een sleutel met rechten, op elke weg (V-043)."""
    made = _hub(client)
    h = {"X-Api-Key": made["key"]}
    assert machine.get("/api/v1/auth/me", headers=h).status_code == 200
    assert client.post(f"/api/v1/auth/api-keys/{made['id']}/revoke").status_code == 200
    for method, path, body in (("GET", "/api/v1/auth/me", None),
                               ("POST", ESTOP, {"active": True}),
                               ("POST", "/api/v1/app/control",
                                {"action": "emergency_stop_on", "confirm": True})):
        r = machine.request(method, path, headers=h, json=body)
        assert r.status_code == 401 and r.json() == INVALID, path
    assert ctx.state_get("emergency_stop") is None
    verlopen = _hub(client, name="hub-verlopen")
    past = (datetime.now(UTC) - timedelta(minutes=1)).isoformat(timespec="seconds")
    ctx.db.execute("UPDATE api_keys SET expires_at=? WHERE id=?", (past, verlopen["id"]))
    r = machine.post(ESTOP, headers={"X-Api-Key": verlopen["key"]}, json={"active": True})
    assert r.status_code == 401 and r.json() == INVALID
    assert ctx.state_get("emergency_stop") is None


def test_sleutel_met_recht_staat_niet_in_audit_of_log(client, machine, ctx, caplog):  # type: ignore[no-untyped-def]
    """(f) Aanmaken, gebruiken, weigeren en intrekken: de sleutel staat nergens."""
    with caplog.at_level(logging.DEBUG):
        made = _hub(client)
        key = made["key"]
        h = {"X-Api-Key": key}
        machine.post(ESTOP, headers=h, json={"active": True, "reason": "Hub"})
        machine.post(ESTOP, headers=h, json={"active": False})
        machine.post("/api/v1/notifications/read-all", headers=h, json={})
        client.post(f"/api/v1/auth/api-keys/{made['id']}/revoke")
        machine.get("/api/v1/status", headers=h)
        listed = client.get("/api/v1/auth/api-keys").text
    created = ctx.db.fetch_one("SELECT new_value FROM audit_events WHERE "
                               "action='apikey.created'")
    assert json.loads(created["new_value"])["scopes"] == [SCOPE]
    actions = {r["action"] for r in ctx.db.fetch_all("SELECT action FROM audit_events")}
    assert {"apikey.created", "system.emergency_stop", "apikey.revoked",
            "apikey.rejected"} <= actions
    for table in ("api_keys", "audit_events", "system_state"):
        for r in ctx.db.fetch_all(f"SELECT * FROM {table}"):  # noqa: S608
            assert key not in " ".join(str(v) for v in dict(r).values()), table
    assert key not in caplog.text and key not in listed


@pytest.mark.parametrize("stored", ['["wanted:search"]', "[]", "emergency_stop:on",
                                    '["emergency_stop:on", 3]', '{"emergency_stop:on": true}'])
def test_onbekend_recht_in_de_database_staat_niets_toe(client, machine, ctx, fake_search,  # type: ignore[no-untyped-def]
                                                       stored):
    """Fail-closed: een recht dat deze versie niet kent (of een onleesbare waarde) opent niets;
    de sleutel mag dan alleen lezen."""
    made = _create(client, name="hub", role="viewer")
    ctx.db.execute("UPDATE api_keys SET scopes=? WHERE id=?", (stored, made["id"]))
    h = {"X-Api-Key": made["key"]}
    assert machine.get("/api/v1/status", headers=h).status_code == 200
    me = machine.get("/api/v1/auth/me", headers=h).json()
    assert me["role"] == "viewer" and isinstance(me["scopes"], list) and SCOPE not in me["scopes"]
    for path, body in ((ESTOP, {"active": True}), ("/api/v1/wanted/7/search", {}),
                       ("/api/v1/notifications/read-all", {})):
        r = machine.post(path, headers=h, json=body)
        assert r.status_code == 403 and r.json() == DENIED, path
    assert fake_search == [] and ctx.state_get("emergency_stop") is None
    item = next(i for i in client.get("/api/v1/auth/api-keys").json()["items"]
                if i["name"] == "hub")
    assert item["scopes"] == me["scopes"]
    assert item["permissions_nl"] == "Alleen lezen; verder niets"


def test_rechten_op_een_operator_sleutel_in_de_database_geweigerd(client, machine, ctx):  # type: ignore[no-untyped-def]
    """Een operator met rechten kan alleen door een handmatig aangepaste database ontstaan:
    zo'n sleutel wordt geweigerd (401), niet stil als operator gebruikt."""
    made = _create(client, name="mso", role="operator")
    ctx.db.execute("UPDATE api_keys SET scopes=? WHERE id=?", (json.dumps([SCOPE]), made["id"]))
    h = {"X-Api-Key": made["key"]}
    for method, path, body in (("GET", "/api/v1/status", None), ("POST", ESTOP, {"active": False})):
        r = machine.request(method, path, headers=h, json=body)
        assert r.status_code == 401 and r.json() == INVALID, path
    reasons = [r["reason"] for r in ctx.db.fetch_all(
        "SELECT reason FROM audit_events WHERE action='apikey.rejected'")]
    assert reasons and all("rechten (scopes)" in x and made["key"] not in x for x in reasons)
    item = client.get("/api/v1/auth/api-keys").json()["items"][0]
    assert item["permissions_nl"] == ("Wordt geweigerd: rechten (scopes) kunnen alleen op een "
                                      "sleutel met rol kijker")


def test_lijst_en_me_tonen_de_rechten(client, machine):  # type: ignore[no-untyped-def]
    """GET /auth/me en de sleutellijst tonen de rechten; sleutels zonder rechten tonen null en
    dezelfde tekst als vroeger."""
    hub = _hub(client)
    _create(client, name="lezer", role="viewer")
    _create(client, name="mso", role="operator")
    items = {i["name"]: i for i in client.get("/api/v1/auth/api-keys").json()["items"]}
    assert items["hub"]["scopes"] == [SCOPE] and items["hub"]["role"] == "viewer"
    assert items["hub"]["permissions_nl"] == ("Alleen lezen, plus de noodstop activeren (nooit "
                                              "opheffen); verder niets")
    assert items["lezer"]["scopes"] is None and items["lezer"]["permissions_nl"] == "Alleen lezen"
    assert items["mso"]["scopes"] is None
    assert items["mso"]["permissions_nl"] == ("Lezen en acties zoals 'zoek opnieuw'; geen beheer, "
                                              "gebruikers of geheimen")
    assert all("key" not in i and "key_hash" not in i for i in items.values())
    revoked = client.post(f"/api/v1/auth/api-keys/{hub['id']}/revoke").json()
    assert revoked["scopes"] == [SCOPE]
    assert client.get("/api/v1/auth/me").json()["scopes"] is None  # sessie: geen rechten


def test_routetabel_en_rechtenlijst(app):  # type: ignore[no-untyped-def]
    """De routetabel kent de eigen noodstopcontrole (rol operator, of het recht), het enige
    recht is emergency_stop:on (wanted:search pas na BG-05) en elke route in de rechtenlijst
    bestaat echt."""
    from mediaguard.apikeys import KNOWN_SCOPES
    from mediaguard.auth import SCOPE_ROUTES

    assert KNOWN_SCOPES == (SCOPE,)
    assert dict(SCOPE_ROUTES) == {SCOPE: frozenset({("POST", ESTOP)})}
    rows = _routes_script().collect(app)
    estop = [r for r in rows if r["path"] == ESTOP]
    assert len(estop) == 1 and estop[0]["role"] == "operator" and estop[0]["scopes"] == [SCOPE]
    assert [r["path"] for r in rows if r["scopes"]] == [ESTOP]
    control = next(r for r in rows if r["path"] == "/api/v1/app/control")
    assert control["role"] == "operator" and control["scopes"] == []
