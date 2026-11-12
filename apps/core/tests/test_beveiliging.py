"""Beveiliging (verbeterlijst 4, 13, 32, 33, 34): admin-only instellingen, inlogrem, echt IP,
https, hookwacht bij een herstart van core, tunnelafscherming en geheimen/sessies."""

from __future__ import annotations

import io
import json
import threading
import time
from contextlib import redirect_stdout
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any

import httpx
import pytest
from cryptography.fernet import Fernet
from fastapi.testclient import TestClient
from starlette.requests import Request

from mediaguard import hookguard, noodtoegang
from mediaguard.security import net

PASSWORD = "geheim-wachtwoord"


# -- hulpjes ------------------------------------------------------------------------------------
def _user_client(app, client, username: str, role: str) -> TestClient:  # type: ignore[no-untyped-def]
    r = client.post("/api/v1/auth/users",
                    json={"username": username, "password": PASSWORD + "!", "role": role})
    assert r.status_code == 200, r.text
    c = TestClient(app)
    r = c.post("/api/v1/auth/login", json={"username": username, "password": PASSWORD + "!"})
    assert r.status_code == 200, r.text
    c.headers["X-CSRF-Token"] = r.json()["csrf_token"]
    return c


def _patch(c: TestClient, key: str, value=None, *, reset: bool = False):  # type: ignore[no-untyped-def]
    change = {"scope_type": "global", "scope_id": "", "key": key}
    change.update({"reset": True} if reset else {"value": value})
    return c.patch("/api/v1/settings/scoped", json={"changes": [change]})


def _request(headers: dict[str, str] | None = None, peer: str = "127.0.0.1",
             scheme: str = "http", app=None) -> Request:  # type: ignore[no-untyped-def]
    scope = {"type": "http", "method": "GET", "path": "/", "scheme": scheme,
             "headers": [(k.lower().encode(), v.encode()) for k, v in (headers or {}).items()],
             "client": (peer, 12345), "server": ("127.0.0.1", 38100), "query_string": b""}
    if app is not None:
        scope["app"] = app
    return Request(scope)


# == item 4: gevaarlijke instellingen alleen voor admin =========================================
DANGEROUS = [("quarantine.mode", "immediate_after_check"), ("quarantine.retention_days", 0),
             # zelfde effect als 'direct verwijderen': klein quotum zonder minimale bewaartijd
             ("quarantine.max_size_gb", 1), ("quarantine.min_age_hours", 0),
             ("quarantine.safety_net_gb", 0), ("quarantine.release_check_full_decode", False),
             ("recovery.zfs_dataset", "voorbeeldpool/anders"),
             ("security.require_2fa_admins", False), ("security.notify_new_login", False),
             ("seerr.url", "http://kwaadaardig.example:5055"),
             ("mediaserver.url", "http://kwaadaardig.example:8096"),
             ("recovery.truenas_url", "https://kwaadaardig.example"),
             ("backup.offsite.webdav_url", "https://kwaadaardig.example/dav"),
             ("app.public_url", "https://kwaadaardig.example"),
             # Peil O-11: de link 'Terug naar de Hub' stuurt iedereen naar dit adres (ADR-0020)
             ("system.hub_url", "http://kwaadaardig.example:30400")]


@pytest.mark.parametrize(("key", "value"), DANGEROUS)
def test_operator_cannot_change_dangerous_settings(app, client, ctx, key, value):  # type: ignore[no-untyped-def]
    op = _user_client(app, client, "operator1", "operator")
    before = ctx.config.value(key)
    r = _patch(op, key, value)
    assert r.status_code == 403 and "beheerder" in r.json()["detail"]
    assert _patch(op, key, reset=True).status_code == 403
    assert ctx.config.value(key) == before
    # een beheerder mag het wel
    assert _patch(client, key, value).status_code == 200
    assert ctx.config.value(key) == value


def test_app_key_cannot_change_dangerous_settings(client, app, ctx):  # type: ignore[no-untyped-def]
    key = client.post("/api/v1/app/pairing/key").json()["qr_payload"]["key"]
    with TestClient(app) as phone:
        h = {"X-Api-Key": key}
        r = phone.patch("/api/v1/settings/scoped", headers=h, json={"changes": [
            {"scope_type": "global", "scope_id": "", "key": "seerr.url",
             "value": "http://kwaadaardig.example"}]})
        assert r.status_code == 403
        r = phone.patch("/api/v1/settings/scoped", headers=h, json={"changes": [
            {"scope_type": "global", "scope_id": "", "key": "library.write_enabled",
             "value": True}]})
        assert r.status_code == 409
        # gewone instellingen blijven werken voor de app
        r = phone.patch("/api/v1/settings/scoped", headers=h, json={"changes": [
            {"scope_type": "global", "scope_id": "", "key": "jobs.max_concurrency",
             "value": 3}]})
        assert r.status_code == 200
    assert ctx.config.value("seerr.url") == ""


def test_library_release_only_via_confirm_button(client, app):  # type: ignore[no-untyped-def]
    ctx = app.state.ctx
    ctx.config.set("global", "", "library.write_enabled", False, "test")
    # ook een beheerder kan het niet als gewone schakelaar zetten (of 'standaard' herstellen)
    r = _patch(client, "library.write_enabled", True)
    assert r.status_code == 409 and "Bibliotheek vrijgeven" in r.json()["detail"]
    assert _patch(client, "library.write_enabled", reset=True).status_code == 409
    assert ctx.config.value("library.write_enabled") is False
    # alles-of-niets: een geldige wijziging in hetzelfde verzoek wordt niet half opgeslagen
    r = client.patch("/api/v1/settings/scoped", json={"changes": [
        {"scope_type": "global", "scope_id": "", "key": "jobs.max_concurrency", "value": 5},
        {"scope_type": "global", "scope_id": "", "key": "library.write_enabled",
         "value": True}]})
    assert r.status_code == 409 and ctx.config.value("jobs.max_concurrency") != 5
    # de knop met bevestiging werkt wel
    assert client.post("/api/v1/recovery/library-write",
                       json={"enabled": True}).status_code == 422
    r = client.post("/api/v1/recovery/library-write", json={"enabled": True, "confirm": True})
    assert r.status_code == 200 and ctx.config.value("library.write_enabled") is True


def test_operator_keeps_normal_settings_and_safe_restore(app, client, ctx):  # type: ignore[no-untyped-def]
    op = _user_client(app, client, "operator2", "operator")
    assert _patch(op, "jobs.max_concurrency", 6).status_code == 200
    ctx.config.set("global", "", "quarantine.mode", "immediate_after_check", "t")
    # terug naar de veilige standaard mag een operator wel
    r = op.post("/api/v1/settings/safety/restore", json={"keys": ["quarantine.mode"]})
    assert r.status_code == 200 and ctx.config.value("quarantine.mode") == "retain"


def test_operator_cannot_shorten_audit_or_login_history(app, client, ctx):  # type: ignore[no-untyped-def]
    """Een operator (ook de app-sleutel) mag het auditlog of de aanmeldgeschiedenis niet laten
    inkorten om sporen van zijn eigen acties te wissen."""
    op = _user_client(app, client, "operator10", "operator")
    for key in ("maintenance.retention_days_audit", "maintenance.retention_days_history"):
        before = ctx.config.value(key)
        assert _patch(op, key, 30).status_code == 403
        assert _patch(op, key, reset=True).status_code == 403
        assert ctx.config.value(key) == before
        assert _patch(client, key, 400).status_code == 200  # beheerder wel


def test_schema_marks_admin_only_and_button_only(client):  # type: ignore[no-untyped-def]
    schema = client.get("/api/v1/settings/schema").json()
    by = {d["key"]: d for d in schema["settings"]}
    assert by["quarantine.mode"]["admin_only"] is True
    assert by["seerr.url"]["admin_only"] is True
    assert by["security.trusted_proxies"]["admin_only"] is True
    assert by["system.hub_url"]["admin_only"] is True
    assert by["jobs.max_concurrency"]["admin_only"] is False
    assert by["library.write_enabled"]["managed_nl"]
    assert by["jobs.max_concurrency"]["managed_nl"] == ""


def test_hook_key_only_works_on_hook_endpoints(anon_client, ctx):  # type: ignore[no-untyped-def]
    ctx.set_hook_key("sonarr-series", "series-hook-key-456")
    h = {"X-MediaGuard-Hook-Key": "series-hook-key-456"}
    assert anon_client.get("/api/v1/auth/me", headers=h).status_code == 401
    r = anon_client.patch("/api/v1/settings/scoped", headers=h, json={"changes": [
        {"scope_type": "global", "scope_id": "", "key": "seerr.url", "value": "http://x"}]})
    assert r.status_code == 401
    assert anon_client.post("/api/v1/system/emergency-stop", headers=h,
                            json={"active": False}).status_code == 401
    # de eigen hook blijft werken
    r = anon_client.post("/api/v1/hooks/pre-grab", headers=h, json={
        "instance_id": "sonarr-series", "release": {"title": "X"}})
    assert r.status_code == 200


def test_operator_cannot_change_instance_public_url(app, client, ctx):  # type: ignore[no-untyped-def]
    from mediaguard.instances.registry import seed_instances

    seed_instances(ctx)
    op = _user_client(app, client, "operator6", "operator")
    r = op.patch("/api/v1/instances/sonarr-series",
                 json={"public_url": "http://kwaadaardig.example:8989", "verify": False})
    assert r.status_code == 403
    assert op.patch("/api/v1/instances/sonarr-series",
                    json={"name": "Series", "verify": False}).status_code == 200


def test_custom_address_tests_need_admin(app, client):  # type: ignore[no-untyped-def]
    op = _user_client(app, client, "operator3", "operator")
    r = op.post("/api/v1/provisioning/prowlarr/test",
                json={"url": "http://kwaadaardig.example:9696"})
    assert r.status_code == 403
    r = op.post("/api/v1/provisioning/downloadclients/test",
                json={"download_client": {"name": "x", "host": "kwaadaardig.example"}})
    assert r.status_code == 403


# == item 13: inlogrem, echt IP, https, 2FA-aansporing ===========================================
def test_login_backoff_per_address_and_notification(app, client, ctx):  # type: ignore[no-untyped-def]
    with TestClient(app) as c:
        c.headers["x-forwarded-for"] = "203.0.113.50"
        for _ in range(5):
            r = c.post("/api/v1/auth/login", json={"username": "admin", "password": "fout"})
            assert r.status_code == 401
        # ook het juiste wachtwoord moet nu even wachten
        r = c.post("/api/v1/auth/login", json={"username": "admin", "password": PASSWORD})
        assert r.status_code == 429 and int(r.headers["Retry-After"]) > 0
        assert "Te veel mislukte pogingen" in r.json()["detail"]
    note = ctx.db.fetch_one("SELECT title, body FROM notifications WHERE dedupe_key=?",
                            ("security.bruteforce",))
    assert note is not None and "203.0.113.50" in note["body"]
    assert note["title"] == "Veel mislukte inlogpogingen"
    # een bekend apparaat (hier: waar de beheerder is aangemaakt) kan gewoon inloggen
    with TestClient(app) as other:
        assert other.post("/api/v1/auth/login",
                          json={"username": "admin", "password": PASSWORD}).status_code == 200
    # een ander, onbekend adres wacht ook (rem per gebruiker), maar korter dan het
    # aanvallende adres zelf
    with TestClient(app) as stranger:
        stranger.headers["x-forwarded-for"] = "198.51.100.20"
        r = stranger.post("/api/v1/auth/login", json={"username": "admin", "password": PASSWORD})
        assert r.status_code == 429


def test_login_backoff_per_user_spares_known_devices(app, client, ctx):  # type: ignore[no-untyped-def]
    with TestClient(app) as home:
        home.headers["x-forwarded-for"] = "192.168.1.20"
        assert home.post("/api/v1/auth/login",
                         json={"username": "admin", "password": PASSWORD}).status_code == 200
    for n in range(5):  # vijf verschillende onbekende adressen, elk één poging
        with TestClient(app) as c:
            c.headers["x-forwarded-for"] = f"203.0.113.{n + 1}"
            assert c.post("/api/v1/auth/login",
                          json={"username": "admin", "password": "fout"}).status_code == 401
    with TestClient(app) as c:
        c.headers["x-forwarded-for"] = "203.0.113.99"
        r = c.post("/api/v1/auth/login", json={"username": "admin", "password": PASSWORD})
        assert r.status_code == 429
    with TestClient(app) as home:
        home.headers["x-forwarded-for"] = "192.168.1.20"
        assert home.post("/api/v1/auth/login",
                         json={"username": "admin", "password": PASSWORD}).status_code == 200


def _concurrent_logins(app, n: int, url: str = "/api/v1/auth/login", **body: Any) -> list[int]:  # type: ignore[no-untyped-def]
    import asyncio

    async def run() -> list[int]:
        transport = httpx.ASGITransport(app=app)
        async with httpx.AsyncClient(transport=transport, base_url="http://testserver") as c:
            out = await asyncio.gather(*[c.post(url, json=body, headers={
                "x-forwarded-for": "203.0.113.61"}) for _ in range(n)])
        return [r.status_code for r in out]

    return asyncio.run(run())


def test_concurrent_wrong_logins_cannot_bypass_the_limit(app, client, ctx):  # type: ignore[no-untyped-def]
    """Controle, wachtwoord nakijken en de mislukking vastleggen zijn één stap: 30
    gelijktijdige gokken geven er hooguit 'login_max_attempts' een antwoord, de rest 429."""
    limit = int(ctx.config.value("security.login_max_attempts"))
    codes = _concurrent_logins(app, 30, username="admin", password="fout")
    assert codes.count(401) <= limit, codes
    assert codes.count(429) >= 30 - limit and set(codes) <= {401, 429}


def test_rotating_addresses_and_made_up_names_give_one_push(app, client, ctx):  # type: ignore[no-untyped-def]
    """Via de tunnel heeft een aanvaller een heel IPv6-blok en kan hij namen verzinnen. Dat
    geeft hooguit één melding (vaste titel, geen vrije tekst), geen pushstroom. Binnen één /64
    telt alles samen, dus wisselen van adres geeft geen nieuwe pogingen."""
    for n in range(6):  # zes verschillende /64-blokken
        with TestClient(app) as c:
            c.headers["x-forwarded-for"] = f"2001:db8:{n + 1}::1"
            for _ in range(5):
                c.post("/api/v1/auth/login", json={
                    "username": f"Klik op evil.example voor herstel {n}", "password": "fout"})
    notes = ctx.db.fetch_dicts("SELECT title, body, count FROM notifications WHERE "
                               "title LIKE '%inlog%' OR dedupe_key LIKE 'security.brute%'")
    assert len(notes) == 1, notes
    assert notes[0]["title"] == "Veel mislukte inlogpogingen"
    assert "evil.example" not in notes[0]["title"] + notes[0]["body"]
    assert int(notes[0]["count"]) >= 2  # de rest alleen geteld
    # wisselen binnen één /64: na 5 pogingen moet het hele blok wachten
    codes = []
    for n in range(7):
        with TestClient(app) as c:
            c.headers["x-forwarded-for"] = f"2001:db8:aa:bb::{n + 1}"
            codes.append(c.post("/api/v1/auth/login", json={"username": "admin",
                                                            "password": "fout"}).status_code)
    assert codes[:5] == [401] * 5 and codes[5:] == [429, 429]
    note = ctx.db.fetch_one("SELECT body FROM notifications WHERE dedupe_key=?",
                            ("security.bruteforce",))
    assert "2001:db8:aa:bb::/64" in note["body"]


def test_login_limit_is_configurable(app, client, ctx):  # type: ignore[no-untyped-def]
    ctx.config.set("global", "", "security.login_max_attempts", 3, "t")
    with TestClient(app) as c:
        c.headers["x-forwarded-for"] = "203.0.113.77"
        for _ in range(3):
            c.post("/api/v1/auth/login", json={"username": "admin", "password": "fout"})
        r = c.post("/api/v1/auth/login", json={"username": "admin", "password": PASSWORD})
        assert r.status_code == 429


def test_delay_grows_and_is_capped(ctx):  # type: ignore[no-untyped-def]
    from mediaguard.security.limiter import delay_for

    assert delay_for(4, 5, 900) == 0
    assert delay_for(5, 5, 900) == 30 and delay_for(6, 5, 900) == 60
    assert delay_for(50, 5, 900) == 900


def test_twofa_codes_are_rate_limited(app, client, ctx):  # type: ignore[no-untyped-def]
    from mediaguard.security import totp

    secret = client.post("/api/v1/security/totp/setup").json()["secret"]
    assert client.post("/api/v1/security/totp/enable",
                       json={"code": totp.hotp(secret, totp.counter_at())}).status_code == 200
    with TestClient(app) as c:
        c.headers["x-forwarded-for"] = "203.0.113.60"
        statuses = []
        for _ in range(3):  # het wachtwoord klopt, maar de codes niet
            ch = c.post("/api/v1/auth/login", json={"username": "admin", "password": PASSWORD})
            if ch.status_code != 200:
                statuses.append(ch.status_code)
                break
            for _ in range(2):
                r = c.post("/api/v1/auth/login/2fa", json={
                    "challenge": ch.json()["challenge"], "code": "000000"})
                statuses.append(r.status_code)
        assert 429 in statuses


def test_client_ip_only_trusts_headers_from_trusted_proxy(app):  # type: ignore[no-untyped-def]
    # via de web-server, vanaf internet zonder vertrouwde proxy: header genegeerd
    r = _request({"x-mg-peer": "198.51.100.7", "cf-connecting-ip": "1.2.3.4",
                  "x-forwarded-for": "5.6.7.8"}, app=app)
    assert net.client_ip(r) == "198.51.100.7"
    # via de lokale cloudflared (Docker-netwerk): CF-Connecting-IP telt
    r = _request({"x-mg-peer": "172.18.0.5", "cf-connecting-ip": "203.0.113.5"}, app=app)
    assert net.client_ip(r) == "203.0.113.5"
    r = _request({"x-mg-peer": "::ffff:127.0.0.1", "x-forwarded-for": "9.9.9.9, 203.0.113.6"},
                 app=app)
    assert net.client_ip(r) == "203.0.113.6"  # het adres dat de proxy zelf toevoegde
    # niet via een interne verbinding: alle headers genegeerd
    r = _request({"x-mg-peer": "172.18.0.5", "cf-connecting-ip": "1.2.3.4"},
                 peer="203.0.113.9", app=app)
    assert net.client_ip(r) == "203.0.113.9"


def test_trusted_proxies_setting(app, ctx):  # type: ignore[no-untyped-def]
    ctx.config.set("global", "", "security.trusted_proxies", ["10.9.0.0/16", "onzin"], "t")
    r = _request({"x-mg-peer": "10.9.1.2", "cf-connecting-ip": "203.0.113.5"}, app=app)
    assert net.client_ip(r) == "203.0.113.5"
    r = _request({"x-mg-peer": "172.18.0.5", "cf-connecting-ip": "203.0.113.5"}, app=app)
    assert net.client_ip(r) == "172.18.0.5"


def test_hint_when_cloudflared_is_not_trusted(app, client, ctx):  # type: ignore[no-untyped-def]
    """cloudflared op een ander apparaat: niet stil vertrouwen, wel een hint geven."""
    with TestClient(app) as c:
        c.post("/api/v1/auth/login", headers={"x-mg-peer": "192.168.1.40",
                                              "cf-connecting-ip": "203.0.113.8"},
               json={"username": "admin", "password": PASSWORD})
    row = ctx.db.fetch_one("SELECT ip FROM login_history ORDER BY id DESC LIMIT 1")
    assert row["ip"] == "192.168.1.40"
    items = {i["key"]: i for i in client.get("/api/v1/attention").json()["items"]}
    assert "192.168.1.40" in items["security.tunnel_untrusted"]["detail_nl"]
    ctx.config.set("global", "", "security.trusted_proxies", ["192.168.1.40"], "t")
    with TestClient(app) as c:
        c.post("/api/v1/auth/login", headers={"x-mg-peer": "192.168.1.40",
                                              "cf-connecting-ip": "203.0.113.8"},
               json={"username": "admin", "password": PASSWORD})
    row = ctx.db.fetch_one("SELECT ip FROM login_history ORDER BY id DESC LIMIT 1")
    assert row["ip"] == "203.0.113.8"


def test_session_cookie_secure_behind_tunnel(app, client):  # type: ignore[no-untyped-def]
    def cookie(headers: dict[str, str]) -> str:
        with TestClient(app) as c:
            r = c.post("/api/v1/auth/login", headers=headers,
                       json={"username": "admin", "password": PASSWORD})
            assert r.status_code == 200, r.text
            return r.headers["set-cookie"]

    assert "secure" in cookie({"x-mg-peer": "172.18.0.5", "x-forwarded-proto": "https"}).lower()
    assert "secure" in cookie({"x-mg-peer": "192.168.1.30", "x-mg-tls": "1"}).lower()
    assert "secure" not in cookie({"x-mg-peer": "192.168.1.30",
                                   "x-forwarded-proto": "https"}).lower()
    assert "secure" not in cookie({"x-mg-peer": "192.168.1.30"}).lower()


def test_pairing_qr_uses_https_for_public_address(client, ctx):  # type: ignore[no-untyped-def]
    # via de tunnel, zonder ingesteld adres: cloudflared praat http met de web-server
    r = client.get("/api/v1/app/pairing", headers={
        "x-mg-peer": "172.18.0.5", "x-forwarded-proto": "https",
        "x-forwarded-host": "mediaguard.voorbeeld.nl"}).json()
    assert r["server_url"] == "https://mediaguard.voorbeeld.nl"
    # zelfs als de proxy geen https meldt: publiek domein = https
    r = client.get("/api/v1/app/pairing", headers={
        "x-mg-peer": "198.51.100.3", "x-forwarded-host": "mediaguard.voorbeeld.nl"}).json()
    assert r["server_url"] == "https://mediaguard.voorbeeld.nl" and r["server_url_upgraded"]
    # LAN-adres blijft http
    r = client.get("/api/v1/app/pairing", headers={
        "x-mg-peer": "192.168.1.30", "x-forwarded-host": "192.168.1.10:30080"}).json()
    assert r["server_url"] == "http://192.168.1.10:30080" and not r["server_url_upgraded"]
    ctx.config.set("global", "", "app.public_url", "http://mediaguard.voorbeeld.nl/", "t")
    made = client.post("/api/v1/app/pairing/key").json()
    assert made["qr_payload"]["url"] == "https://mediaguard.voorbeeld.nl"


@pytest.mark.parametrize(("raw", "expected"), [
    ("http://mediaguard.voorbeeld.nl", "https://mediaguard.voorbeeld.nl"),
    ("mediaguard.voorbeeld.nl", "https://mediaguard.voorbeeld.nl"),
    ("http://mediaguard.voorbeeld.nl:80/", "https://mediaguard.voorbeeld.nl"),
    ("https://mediaguard.voorbeeld.nl", "https://mediaguard.voorbeeld.nl"),
    ("http://nas.lan:30080", "http://nas.lan:30080"),
    ("http://truenas.local:30080", "http://truenas.local:30080"),
    ("http://nas:30080", "http://nas:30080"),
    ("http://nas.voorbeeld.nl:30080", "http://nas.voorbeeld.nl:30080"),
    ("http://100.64.1.2:30080", "http://100.64.1.2:30080"),
    ("http://8.8.8.8", "https://8.8.8.8"),
])
def test_secure_public_url(raw, expected):  # type: ignore[no-untyped-def]
    assert net.secure_public_url(raw)[0] == expected


def test_twofa_nudge_without_forcing(client, ctx):  # type: ignore[no-untyped-def]
    ctx.services.setdefault("x", None)
    me = client.get("/api/v1/auth/me").json()
    assert me["twofa_nudge"] is True and me["twofa_required"] is False
    assert client.get("/api/v1/status").status_code == 200  # niet verplicht
    items = {i["key"]: i for i in client.get("/api/v1/attention").json()["items"]}
    assert items["security.twofa_off"]["severity"] == "info"
    assert items["security.twofa_off"]["href"] == "/instellingen/beveiliging"
    ctx.config.set("global", "", "security.nudge_2fa", False, "t")
    assert client.get("/api/v1/auth/me").json()["twofa_nudge"] is False
    items = {i["key"] for i in client.get("/api/v1/attention").json()["items"]}
    assert "security.twofa_off" not in items


# == item 32: hookwacht ==========================================================================
class _FakeCore:
    def __init__(self, mode: str = "ok") -> None:
        self.mode = mode
        self.seen: list[tuple[str, dict[str, str], bytes]] = []
        outer = self

        class H(BaseHTTPRequestHandler):
            protocol_version = "HTTP/1.1"

            def do_POST(self) -> None:  # noqa: N802
                body = self.rfile.read(int(self.headers.get("Content-Length") or 0))
                outer.seen.append((self.path, dict(self.headers), body))
                if outer.mode == "slow":
                    time.sleep(1.5)
                status = {"error": 500, "unauthorized": 401}.get(outer.mode, 200)
                data = json.dumps({"decision": "accept", "reason_code": None, "message": "ok",
                                   "score_adjustment": 0}).encode()
                self.send_response(status)
                self.send_header("Content-Type", "application/json")
                self.send_header("Content-Length", str(len(data)))
                self.end_headers()
                self.wfile.write(data)

            def log_message(self, *_a) -> None:  # type: ignore[no-untyped-def]
                return

        self.server = ThreadingHTTPServer(("127.0.0.1", 0), H)
        threading.Thread(target=self.server.serve_forever, daemon=True).start()

    @property
    def addr(self) -> str:
        return f"127.0.0.1:{self.server.server_address[1]}"

    def close(self) -> None:
        self.server.shutdown()
        self.server.server_close()


def _free_port() -> int:
    import socket

    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return int(s.getsockname()[1])


@pytest.fixture
def guard(tmp_path):  # type: ignore[no-untyped-def]
    started = []

    def start(core: str, **env: str) -> str:
        server = hookguard.serve({"MG_HOOKGUARD_PORT": "0", "MG_HOOKGUARD_CORE": core,
                                  "MG_HOOKGUARD_TIMEOUT_MS": "500",
                                  "MG_HOOKGUARD_FLAG": str(tmp_path / "noodstop.json"), **env})
        threading.Thread(target=server.serve_forever, daemon=True).start()
        started.append(server)
        return f"http://127.0.0.1:{server.server_address[1]}"

    yield start
    for s in started:
        s.shutdown()
        s.server_close()


def _grab(base: str, hook: str = "pre-grab", **extra: Any) -> httpx.Response:
    return httpx.post(f"{base}/api/v1/hooks/{hook}", timeout=5,
                      headers={"X-MediaGuard-Hook-Key": "k"},
                      json={"instance_id": "sonarr-series", "release": {"title": "X"},
                            **extra})


def test_guard_forwards_to_core_unchanged(guard):  # type: ignore[no-untyped-def]
    core = _FakeCore()
    try:
        base = guard(core.addr)
        r = _grab(base)
        assert r.status_code == 200 and r.json()["decision"] == "accept"
        path, headers, body = core.seen[-1]
        assert path == "/api/v1/hooks/pre-grab"
        assert headers["X-MediaGuard-Hook-Key"] == "k" and b"sonarr-series" in body
        assert httpx.get(f"{base}/livez").status_code == 200
    finally:
        core.close()


def test_guard_holds_emergency_stop_while_core_is_down(guard, tmp_path):  # type: ignore[no-untyped-def]
    flag = tmp_path / "noodstop.json"
    base = guard(f"127.0.0.1:{_free_port()}")  # niemand luistert: core herstart
    # geen noodstop: 503, de fork past zijn eigen fail policy toe (downloads gaan door)
    flag.write_text(json.dumps({"active": False}))
    assert _grab(base).status_code == 503
    # noodstop actief: de wachter weigert de grab zelf
    flag.write_text(json.dumps({"active": True}))
    r = _grab(base)
    assert r.status_code == 200
    assert r.json()["decision"] == "reject" and r.json()["reason_code"] == "EMERGENCY_STOP"
    # pre-import kent geen noodstop-weigering in core: fail policy
    assert _grab(base, "pre-import").status_code == 503


def test_guard_falls_back_on_core_error_and_timeout(guard, tmp_path):  # type: ignore[no-untyped-def]
    (tmp_path / "noodstop.json").write_text(json.dumps({"active": True}))
    core = _FakeCore("error")
    try:
        base = guard(core.addr)
        assert _grab(base).json()["reason_code"] == "EMERGENCY_STOP"
        core.mode = "slow"  # trage core (> 0,5 s)
        started = time.monotonic()
        assert _grab(base).json()["reason_code"] == "EMERGENCY_STOP"
        assert time.monotonic() - started < 1.4
        # trage core zonder noodstop: niet blokkeren, de fork beslist (fail open)
        (tmp_path / "noodstop.json").write_text(json.dumps({"active": False}))
        assert _grab(base).status_code == 503
    finally:
        core.close()


def test_guard_holds_emergency_stop_on_pre_grab_client_error(guard, tmp_path):  # type: ignore[no-untyped-def]
    """Een 4xx van core (bv. 401 kort na een herstart) ziet de fork als 'onbereikbaar' en
    staat de grab dan toe. Tijdens een noodstop weigert de wachter zelf; zonder noodstop en
    bij andere hooks gaat het antwoord van core ongewijzigd door."""
    flag = tmp_path / "noodstop.json"
    flag.write_text(json.dumps({"active": True}))
    core = _FakeCore("unauthorized")
    try:
        base = guard(core.addr)
        assert _grab(base).json()["reason_code"] == "EMERGENCY_STOP"
        assert _grab(base, "pre-import").status_code == 401
        flag.write_text(json.dumps({"active": False}))
        assert _grab(base).status_code == 401
    finally:
        core.close()


def test_guard_defers_upgrade_import_during_emergency_stop(guard, tmp_path):  # type: ignore[no-untyped-def]
    """Een upgrade vervangt het huidige bibliotheekbestand. Antwoordt core niet (of met een
    4xx) tijdens een noodstop, dan stelt de wachter de import zelf uit, zoals core doet. Een
    gewone import zonder bestaand bestand houdt de 503 (fail policy van de fork)."""
    flag = tmp_path / "noodstop.json"
    flag.write_text(json.dumps({"active": True}))
    base = guard(f"127.0.0.1:{_free_port()}")  # core ligt plat
    r = _grab(base, "pre-import", is_upgrade=True)
    assert r.status_code == 200 and r.json()["decision"] == "defer"
    assert r.json()["reason_code"] == "EMERGENCY_STOP"
    assert "huidige bestand blijft staan" in r.json()["message"]
    assert _grab(base, "pre-import").status_code == 503
    assert _grab(base, "pre-import", is_upgrade=False).status_code == 503
    flag.write_text(json.dumps({"active": False}))
    assert _grab(base, "pre-import", is_upgrade=True).status_code == 503
    # core antwoordt met 401 (vlak na een herstart): niet doorgeven, maar uitstellen
    flag.write_text(json.dumps({"active": True}))
    core = _FakeCore("unauthorized")
    try:
        base = guard(core.addr)
        assert _grab(base, "pre-import", is_upgrade=True).json()["decision"] == "defer"
        assert _grab(base, "pre-import").status_code == 401
        core.mode = "ok"  # core beslist zelf zodra hij antwoordt
        assert _grab(base, "pre-import", is_upgrade=True).json()["decision"] == "accept"
    finally:
        core.close()


def test_guard_reads_database_without_flag_file(guard, tmp_path, ctx):  # type: ignore[no-untyped-def]
    ctx.state_set("emergency_stop", {"active": True})
    base = guard(f"127.0.0.1:{_free_port()}", MG_HOOKGUARD_FLAG=str(tmp_path / "ontbreekt.json"),
                 MG_HOOKGUARD_DB=str(ctx.settings.db_path))
    assert _grab(base).json()["reason_code"] == "EMERGENCY_STOP"
    ctx.state_set("emergency_stop", {"active": False})
    assert _grab(base).status_code == 503


def test_emergency_stop_is_written_for_the_guard(client, ctx):  # type: ignore[no-untyped-def]
    flag = Path(ctx.settings.data_dir) / "noodstop.json"
    assert client.post("/api/v1/system/emergency-stop",
                       json={"active": True, "reason": "test"}).status_code == 200
    assert json.loads(flag.read_text())["active"] is True
    assert hookguard.emergency_active(flag, None)
    client.post("/api/v1/system/emergency-stop", json={"active": False})
    assert json.loads(flag.read_text())["active"] is False


# == item 33: tunnelafscherming ==================================================================
def test_internal_token_refused_through_proxy(app, client):  # type: ignore[no-untyped-def]
    token = {"X-MediaGuard-Token": "test-internal-token"}
    with TestClient(app) as c:
        assert c.get("/api/v1/auth/me", headers=token).status_code == 200
        for extra in ({"x-mg-peer": "127.0.0.1"}, {"x-forwarded-for": "1.2.3.4"},
                      {"cf-connecting-ip": "1.2.3.4"}):
            assert c.get("/api/v1/auth/me", headers={**token, **extra}).status_code == 401
    # niet van een interne verbinding: altijd geweigerd
    r = _request(token, peer="203.0.113.9", app=app)
    assert net.internal_origin(r) is False


@pytest.mark.parametrize("path", [
    "x/../mediaguard/v1/jobs/1/committed", "./mediaguard/v1/health", "MediaGuard/v1/health",
    "jobs/%2e%2e/mediaguard/v1/health", "jobs/..%2fmediaguard/v1/health",
    "jobs\\..\\mediaguard/v1/health", "jobs//mediaguard",
])
def test_gateway_blocks_path_tricks(client, ctx, path):  # type: ignore[no-untyped-def]
    seen: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(request)
        return httpx.Response(200, json={"ok": True})

    ctx.services["encoder_transport"] = httpx.MockTransport(handler)
    ctx.services.pop("encoder_gateway_client", None)
    r = client.post(f"/api/v1/encoder/{path}", json={})
    assert r.status_code in (400, 403, 404)
    assert not any("/mediaguard" in req.url.path.lower() for req in seen)


def test_gateway_marks_forwarded_requests(client, ctx):  # type: ignore[no-untyped-def]
    seen: list[httpx.Request] = []
    ctx.services["encoder_transport"] = httpx.MockTransport(
        lambda req: (seen.append(req), httpx.Response(200, json={}))[1])
    ctx.services.pop("encoder_gateway_client", None)
    assert client.get("/api/v1/encoder/systeem/hardware").status_code == 200
    assert seen[-1].headers["X-MediaGuard-Gateway"] == "1"


def test_api_docs_only_for_admin(app, client, anon_client):  # type: ignore[no-untyped-def]
    assert anon_client.get("/api/v1/openapi.json").status_code == 401
    assert anon_client.get("/api/v1/docs").status_code == 401
    for path in ("/docs", "/redoc", "/openapi.json"):
        assert anon_client.get(path).status_code == 404
    op = _user_client(app, client, "operator4", "operator")
    assert op.get("/api/v1/openapi.json").status_code == 403
    assert client.get("/api/v1/openapi.json").status_code == 200
    r = client.get("/api/v1/docs")
    assert r.status_code == 200 and "swagger" in r.text.lower()


# == item 34: geheimen en sessies ================================================================
def _swap_secret_key(ctx) -> None:  # type: ignore[no-untyped-def]
    ctx.secrets.key_path.write_bytes(Fernet.generate_key())
    ctx.secrets._fernet = None


def test_wrong_secret_key_is_shown_not_ingesteld(client, ctx):  # type: ignore[no-untyped-def]
    ctx.secrets.set("seerr:api_key", "abc", "t")
    assert ctx.secrets.has("seerr:api_key") and ctx.secrets.health()["ok"]
    _swap_secret_key(ctx)
    assert ctx.secrets.exists("seerr:api_key") and not ctx.secrets.has("seerr:api_key")
    assert ctx.secrets.health()["unreadable"] == ["seerr:api_key"]
    rows = client.get("/api/v1/settings/secrets").json()
    assert rows[0]["readable"] is False and rows[0]["is_set"] is False
    health = client.get("/api/v1/settings/secrets/health").json()
    assert health["ok"] is False and health["unreadable_count"] == 1
    items = {i["key"]: i for i in client.get("/api/v1/attention").json()["items"]}
    assert items["security.secret_key_mismatch"]["severity"] == "error"
    assert "secret.key" in items["security.secret_key_mismatch"]["title_nl"]
    assert items["security.secret_key_mismatch"]["rows"][0]["title"] == "seerr:api_key"
    # opnieuw invullen lost het op
    ctx.secrets.set("seerr:api_key", "abc", "t")
    assert ctx.secrets.health()["ok"]


def test_viewer_sees_no_secret_names_or_admins_without_2fa(app, client, ctx):  # type: ignore[no-untyped-def]
    """Zoals /settings/secrets/health: namen alleen voor beheerders. Een viewer (web of
    app-status) ziet het punt en het aantal, maar niet welke geheimen onleesbaar zijn of
    welke beheerders alleen een wachtwoord hebben."""
    ctx.secrets.set("seerr:api_key", "abc", "t")
    _swap_secret_key(ctx)
    ctx.config.set("global", "", "security.nudge_2fa", True, "t")
    ctx.config.set("global", "", "security.require_2fa_admins", False, "t")
    viewer = _user_client(app, client, "kijker1", "viewer")
    for body in (viewer.get("/api/v1/attention").json()["items"],
                 viewer.get("/api/v1/app/status").json()["attention"]["items"]):
        items = {i["key"]: i for i in body}
        for key in ("security.secret_key_mismatch", "security.twofa_off"):
            assert items[key]["rows"] == [] and items[key]["count"] >= 1
            assert "alleen een beheerder" in items[key]["detail_nl"]
        text = str(body)
        assert "seerr:api_key" not in text, [i for i in body if "seerr:api_key" in str(i)]
    items = {i["key"]: i for i in client.get("/api/v1/attention").json()["items"]}
    assert [r["title"] for r in items["security.twofa_off"]["rows"]] == ["admin"]
    ctx.secrets.set("seerr:api_key", "abc", "t")


def test_fork_keys_reseeded_from_files_after_key_swap(ctx, monkeypatch):  # type: ignore[no-untyped-def]
    from mediaguard.instances.registry import apikey_secret, seed_instances

    monkeypatch.setenv("MG_INSTANCE_SONARR_SERIES_APIKEY", "a" * 32)
    seed_instances(ctx)
    assert ctx.secrets.get(apikey_secret("sonarr-series")) == "a" * 32
    _swap_secret_key(ctx)
    seed_instances(ctx)
    assert ctx.secrets.get(apikey_secret("sonarr-series")) == "a" * 32


def test_temp_secret_keeps_unreadable_ciphertext(ctx):  # type: ignore[no-untyped-def]
    from mediaguard.provisioning.api import _temp_secret

    ctx.secrets.set("downloadclient:sab:api_key", "oud", "t")
    _swap_secret_key(ctx)
    raw = ctx.secrets.raw("downloadclient:sab:api_key")
    with _temp_secret(ctx, "downloadclient:sab:api_key", "nieuw"):
        assert ctx.secrets.get("downloadclient:sab:api_key") == "nieuw"
    assert ctx.secrets.raw("downloadclient:sab:api_key") == raw  # niet stil gewist


def test_admin_password_reset_logs_out_other_sessions(app, client, ctx):  # type: ignore[no-untyped-def]
    op = _user_client(app, client, "operator5", "operator")
    uid = ctx.db.fetch_one("SELECT id FROM users WHERE username='operator5'")["id"]
    assert op.get("/api/v1/auth/me").status_code == 200
    r = client.patch(f"/api/v1/auth/users/{uid}", json={"password": "een-nieuw-wachtwoord"})
    assert r.status_code == 200 and r.json()["sessions_ended"] == 1
    assert op.get("/api/v1/auth/me").status_code == 401
    # eigen wachtwoord via gebruikersbeheer: deze sessie blijft, andere apparaten niet
    with TestClient(app) as phone:
        phone.post("/api/v1/auth/login", json={"username": "admin", "password": PASSWORD})
        me = ctx.db.fetch_one("SELECT id FROM users WHERE username='admin'")["id"]
        r = client.patch(f"/api/v1/auth/users/{me}", json={"password": "nog-een-wachtwoord"})
        assert r.status_code == 200
        assert phone.get("/api/v1/auth/me").status_code == 401
    assert client.get("/api/v1/auth/me").status_code == 200


def test_change_own_password(app, client, ctx):  # type: ignore[no-untyped-def]
    with TestClient(app) as phone:
        phone.post("/api/v1/auth/login", json={"username": "admin", "password": PASSWORD})
        r = client.post("/api/v1/auth/me/password",
                        json={"current_password": "fout", "new_password": "nieuw-wachtwoord-1"})
        assert r.status_code == 401
        assert phone.get("/api/v1/auth/me").status_code == 200
        r = client.post("/api/v1/auth/me/password", json={
            "current_password": PASSWORD, "new_password": "nieuw-wachtwoord-1"})
        assert r.status_code == 200 and r.json()["sessions_ended"] == 1
        assert "uitgelogd" in r.json()["message_nl"]
        assert phone.get("/api/v1/auth/me").status_code == 401
    assert client.get("/api/v1/auth/me").status_code == 200
    with TestClient(app) as again:
        assert again.post("/api/v1/auth/login", json={
            "username": "admin", "password": "nieuw-wachtwoord-1"}).status_code == 200
    audit = ctx.db.fetch_one("SELECT 1 FROM audit_events WHERE action='auth.password_changed'")
    assert audit is not None


def _cli(*args: str, stdin: str | None = None, monkeypatch=None) -> tuple[int, str]:  # type: ignore[no-untyped-def]
    out = io.StringIO()
    if stdin is not None and monkeypatch is not None:
        monkeypatch.setattr("sys.stdin", io.StringIO(stdin))
    with redirect_stdout(out):
        code = noodtoegang.main(list(args))
    return code, out.getvalue()


def test_emergency_access_cli(app, client, ctx, monkeypatch):  # type: ignore[no-untyped-def]
    from mediaguard.security import totp

    db = str(ctx.settings.db_path)
    secret = client.post("/api/v1/security/totp/setup").json()["secret"]
    client.post("/api/v1/security/totp/enable", json={"code": totp.hotp(secret, totp.counter_at())})
    code, text = _cli("--db", db, "status")
    assert code == 0 and "admin: admin, 2FA aan" in text and "allemaal leesbaar" in text
    # wachtwoord kwijt én telefoon kwijt
    code, text = _cli("--db", db, "wachtwoord", "admin", "--2fa-uit", "--wachtwoord-stdin",
                      stdin="nood-wachtwoord-1\n", monkeypatch=monkeypatch)
    assert code == 0 and "Nieuw wachtwoord" in text
    assert client.get("/api/v1/auth/me").status_code == 401  # overal uitgelogd
    with TestClient(app) as c:
        r = c.post("/api/v1/auth/login", json={"username": "admin",
                                               "password": "nood-wachtwoord-1"})
        assert r.status_code == 200 and not r.json().get("needs_2fa")
    assert ctx.db.fetch_one("SELECT 1 FROM audit_events WHERE actor='cli:noodtoegang'")
    # te kort wachtwoord: niets gewijzigd
    with pytest.raises(SystemExit):
        _cli("--db", db, "wachtwoord", "admin", "--wachtwoord-stdin", stdin="kort\n",
             monkeypatch=monkeypatch)
    # gebruiker weer beheerder maken
    ctx.db.execute("UPDATE users SET role='viewer', disabled=1 WHERE username='admin'")
    assert _cli("--db", db, "beheerder", "admin")[0] == 0
    row = ctx.db.fetch_one("SELECT role, disabled FROM users WHERE username='admin'")
    assert row["role"] == "admin" and row["disabled"] == 0
    _swap_secret_key(ctx)
    ctx.secrets.set("x:y", "z", "t")
    ctx.secrets.key_path.write_bytes(Fernet.generate_key())
    code, text = _cli("--db", db, "status")
    assert "ONLEESBAAR" in text
