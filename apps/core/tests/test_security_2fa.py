"""Tweestapsverificatie, sessies en aanmeldgeschiedenis (rapport 5, M12)."""

from __future__ import annotations

from fastapi.testclient import TestClient

from mediaguard.security import totp

# RFC 6238 bijlage B: secret "12345678901234567890" (ASCII), SHA-1, 8 cijfers
RFC_SECRET = "GEZDGNBVGY3TQOJQGEZDGNBVGY3TQOJQ"


def test_rfc6238_vectors():  # type: ignore[no-untyped-def]
    for ts, expected in ((59, "94287082"), (1111111109, "07081804"),
                         (1234567890, "89005924"), (2000000000, "69279037")):
        assert totp.hotp(RFC_SECRET, ts // 30, digits=8) == expected


def test_verify_window_and_replay():  # type: ignore[no-untyped-def]
    secret = totp.new_secret()
    now = 1_700_000_000
    code = totp.hotp(secret, now // 30)
    counter = totp.verify(secret, code, ts=now)
    assert counter == now // 30
    assert totp.verify(secret, code, ts=now, last_counter=counter) is None  # geen hergebruik
    assert totp.verify(secret, totp.hotp(secret, now // 30 - 1), ts=now) is not None
    assert totp.verify(secret, totp.hotp(secret, now // 30 - 3), ts=now) is None
    assert totp.verify(secret, "12ab", ts=now) is None


def _enable(client) -> tuple[str, list[str]]:  # type: ignore[no-untyped-def]
    setup = client.post("/api/v1/security/totp/setup").json()
    assert setup["otpauth_uri"].startswith("otpauth://totp/MediaGuard%3Aadmin?secret=")
    assert setup["qr_svg"].startswith("data:image/svg+xml")
    bad = client.post("/api/v1/security/totp/enable", json={"code": "000000"})
    assert bad.status_code in (200, 422)
    if bad.status_code == 200:  # 1 op een miljoen: toevallig goed
        return setup["secret"], bad.json()["recovery_codes"]
    code = totp.hotp(setup["secret"], totp.counter_at())
    r = client.post("/api/v1/security/totp/enable", json={"code": code})
    assert r.status_code == 200, r.text
    return setup["secret"], r.json()["recovery_codes"]



def test_login_requires_second_factor(client, app):  # type: ignore[no-untyped-def]
    secret, codes = _enable(client)
    assert len(codes) == 10
    assert client.get("/api/v1/auth/me").json()["twofa_enabled"] is True
    with TestClient(app) as other:
        r = other.post("/api/v1/auth/login",
                       json={"username": "admin", "password": "geheim-wachtwoord"})
        assert r.status_code == 200 and r.json()["needs_2fa"] is True
        assert "mg_session" not in r.cookies
        challenge = r.json()["challenge"]
        assert other.post("/api/v1/auth/login/2fa",
                          json={"challenge": challenge, "code": "111111"}).status_code == 401
        # herstelcode werkt één keer
        ok = other.post("/api/v1/auth/login/2fa", json={"challenge": challenge, "code": codes[0]})
        assert ok.status_code == 200 and ok.json()["role"] == "admin"
        r = other.post("/api/v1/auth/login",
                       json={"username": "admin", "password": "geheim-wachtwoord"})
        again = other.post("/api/v1/auth/login/2fa",
                           json={"challenge": r.json()["challenge"], "code": codes[0]})
        assert again.status_code == 401
    hist = client.get("/api/v1/security/logins").json()["items"]
    assert any(h["method_nl"] == "Herstelcode" and h["success"] for h in hist)
    assert any(not h["success"] for h in hist)


def test_challenge_locks_after_five_wrong(client, app):  # type: ignore[no-untyped-def]
    secret, _codes = _enable(client)
    with TestClient(app) as other:
        r = other.post("/api/v1/auth/login",
                       json={"username": "admin", "password": "geheim-wachtwoord"})
        challenge = r.json()["challenge"]
        for _ in range(5):
            other.post("/api/v1/auth/login/2fa", json={"challenge": challenge, "code": "999999"})
        good = totp.hotp(secret, totp.counter_at())
        r = other.post("/api/v1/auth/login/2fa", json={"challenge": challenge, "code": good})
        assert r.status_code == 401 and "opnieuw" in r.json()["detail"]


def test_sessions_list_and_remote_logout(client, app):  # type: ignore[no-untyped-def]
    with TestClient(app) as phone:
        phone.headers["user-agent"] = "Mozilla/5.0 (Linux; Android 14) Chrome/130"
        r = phone.post("/api/v1/auth/login",
                       json={"username": "admin", "password": "geheim-wachtwoord"})
        assert r.status_code == 200
        items = client.get("/api/v1/security/sessions").json()["items"]
        assert len(items) == 2 and sum(i["current"] for i in items) == 1
        other = next(i for i in items if not i["current"])
        assert other["device_nl"] == "Chrome op Android"
        assert client.delete(f"/api/v1/security/sessions/{other['id']}").json()["revoked"] == 1
        assert phone.get("/api/v1/auth/me").status_code == 401
    assert client.get("/api/v1/auth/me").status_code == 200


def test_new_ip_login_notifies(client, app, ctx):  # type: ignore[no-untyped-def]
    with TestClient(app) as c2:
        c2.post("/api/v1/auth/login", json={"username": "admin", "password": "geheim-wachtwoord"})
    with TestClient(app) as c3:
        c3.headers["x-forwarded-for"] = "203.0.113.9"
        c3.post("/api/v1/auth/login", json={"username": "admin", "password": "geheim-wachtwoord"})
    row = ctx.db.fetch_one("SELECT title, body FROM notifications WHERE dedupe_key LIKE "
                           "'security.login.%203.0.113.9'")
    assert row is not None and "203.0.113.9" in row["body"]


def test_require_2fa_for_admins_policy(client, ctx):  # type: ignore[no-untyped-def]
    r = client.put("/api/v1/security/policy", json={"require_2fa_admins": True})
    assert r.status_code == 409  # eerst zelf instellen
    ctx.config.set("global", "", "security.require_2fa_admins", True, "t")
    blocked = client.get("/api/v1/status")
    assert blocked.status_code == 403 and "tweestapsverificatie" in blocked.json()["detail"]
    assert client.get("/api/v1/auth/me").json()["twofa_required"] is True
    _enable(client)
    assert client.get("/api/v1/status").status_code == 200
    assert client.put("/api/v1/security/policy",
                      json={"require_2fa_admins": True}).status_code == 200


def test_disable_needs_password_and_admin_reset(client, ctx):  # type: ignore[no-untyped-def]
    _enable(client)
    assert client.post("/api/v1/security/totp/disable",
                       json={"password": "fout"}).status_code == 401
    r = client.post("/api/v1/security/totp/disable", json={"password": "geheim-wachtwoord"})
    assert r.json() == {"enabled": False}
    assert client.get("/api/v1/security/me").json()["totp_enabled"] is False
    uid = ctx.db.fetch_one("SELECT id FROM users WHERE username='admin'")["id"]
    assert client.post(f"/api/v1/security/users/{uid}/totp/reset").status_code == 200


def test_api_keys_unaffected_by_2fa(client, app, ctx):  # type: ignore[no-untyped-def]
    _enable(client)
    ctx.config.set("global", "", "security.require_2fa_admins", True, "t")
    key = client.post("/api/v1/auth/api-keys", json={"name": "mso", "role": "viewer"}).json()["key"]
    with TestClient(app) as machine:
        r = machine.get("/api/v1/auth/me", headers={"X-Api-Key": key})
        assert r.status_code == 200 and r.json()["role"] == "viewer"
        assert r.json()["twofa_required"] is False


