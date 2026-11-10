"""PUT /auth/me/preferences: per-gebruiker thema, server-side bewaard en in GET /auth/me."""

from __future__ import annotations


def test_theme_preference_roundtrip(client, app):  # type: ignore[no-untyped-def]
    assert client.get("/api/v1/auth/me").json()["preferences"] == {}
    r = client.put("/api/v1/auth/me/preferences", json={"theme": "light"})
    assert r.status_code == 200, r.text
    assert r.json() == {"preferences": {"theme": "light"}}
    assert client.get("/api/v1/auth/me").json()["preferences"] == {"theme": "light"}
    # overschrijven, ongeldige waarde en onbekende sleutel worden geweigerd
    assert client.put("/api/v1/auth/me/preferences", json={"theme": "system"}).status_code == 200
    assert client.put("/api/v1/auth/me/preferences", json={"theme": "roze"}).status_code == 422
    assert client.put("/api/v1/auth/me/preferences", json={"kleur": "x"}).status_code == 422
    assert client.get("/api/v1/auth/me").json()["preferences"] == {"theme": "system"}
    # null wist de voorkeur
    assert client.put("/api/v1/auth/me/preferences", json={"theme": None}).json() == {
        "preferences": {}}


def test_preferences_are_per_user(client, app):  # type: ignore[no-untyped-def]
    from fastapi.testclient import TestClient

    client.put("/api/v1/auth/me/preferences", json={"theme": "light"})
    new_user = {"username": "kijker", "password": "kijker-wachtwoord", "role": "viewer"}
    assert client.post("/api/v1/auth/users", json=new_user).status_code == 200
    with TestClient(app) as other:
        r = other.post("/api/v1/auth/login", json={"username": "kijker",
                                                   "password": "kijker-wachtwoord"})
        other.headers["X-CSRF-Token"] = r.json()["csrf_token"]
        assert other.get("/api/v1/auth/me").json()["preferences"] == {}
        assert other.put("/api/v1/auth/me/preferences", json={"theme": "dark"}).status_code == 200
    assert client.get("/api/v1/auth/me").json()["preferences"] == {"theme": "light"}


def test_preferences_require_login(anon_client):  # type: ignore[no-untyped-def]
    assert anon_client.put("/api/v1/auth/me/preferences", json={"theme": "dark"}).status_code == 401
