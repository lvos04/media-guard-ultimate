"""Gedeelde fixtures: een core-app met tijdelijke datamap en ingelogde admin."""

from __future__ import annotations

import asyncio
import gc
import secrets
from collections.abc import Iterator
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from mediaguard import storage_forecast
from mediaguard.app import create_app
from mediaguard.config import load_settings


@pytest.fixture(scope="module", autouse=True)
def ruim_testverbindingen_op() -> Iterator[None]:
    yield
    # SQLite-verbindingen van beëindigde TestClient-threads kunnen in een referentiecyclus
    # zitten. Laat hun bestanden vrij vóór de volgende testmodule begint.
    gc.collect()


@pytest.fixture
def settings(tmp_path: Path):  # type: ignore[no-untyped-def]
    return load_settings(data_dir=tmp_path / "config", staging_dir=tmp_path / "staging",
                         quarantine_dir=tmp_path / "quarantine", start_background=False,
                         internal_token="test-internal-token")


@pytest.fixture
def app(settings):  # type: ignore[no-untyped-def]
    application = create_app(settings)
    yield application
    # Verbinding expliciet sluiten: anders sluit de garbage collector hem later, midden in een
    # andere test (WAL-checkpoint + fsync, ~1 s), wat tijdmetingen zoals
    # test_pre_grab_audio_preference_score_and_speed liet falen na test_coverage_matrix.
    ctx = application.state.ctx
    try:
        # Ook zonder TestClient heeft create_app de meetwerker en zijn sloten aangemeld.
        asyncio.run(storage_forecast.shutdown(ctx))
    finally:
        ctx.db.close()


@pytest.fixture
def ctx(app):  # type: ignore[no-untyped-def]
    c = app.state.ctx
    # in de meeste tests is de overstap al gedaan; de vergrendeling zelf heeft eigen tests
    c.config.set("global", "", "library.write_enabled", True, "test")
    return c


@pytest.fixture
def client(app) -> Iterator[TestClient]:  # type: ignore[no-untyped-def]
    with TestClient(app) as c:
        r = c.post("/api/v1/auth/setup",
                   json={"username": "admin", "password": "geheim-wachtwoord"})
        assert r.status_code == 200, r.text
        c.headers["X-CSRF-Token"] = r.json()["csrf_token"]
        yield c


@pytest.fixture
def viewer_client(client) -> Iterator[TestClient]:  # type: ignore[no-untyped-def]
    login = {"username": "kijker", "password": secrets.token_urlsafe(24)}
    client.post("/api/v1/auth/users", json={**login, "role": "viewer"}).raise_for_status()
    with TestClient(client.app) as kijker:
        antwoord = kijker.post("/api/v1/auth/login", json=login)
        antwoord.raise_for_status()
        kijker.headers["X-CSRF-Token"] = antwoord.json()["csrf_token"]
        yield kijker


@pytest.fixture
def plaats_supportsleutel(ctx):  # type: ignore[no-untyped-def]
    """Zet een herkenbaar merkteken in de testkluis voor de supportbundel."""
    def plaats(merkteken: str) -> str:
        sleutel = f"sk-proj-{merkteken}"
        ctx.secrets.set("ai:openai:apikey", sleutel)
        return sleutel

    return plaats


@pytest.fixture
def anon_client(app) -> Iterator[TestClient]:  # type: ignore[no-untyped-def]
    with TestClient(app) as c:
        yield c


@pytest.fixture
def poolhub(ctx, monkeypatch):
    """Lokale simulator van de dienstprojectie uit contract 1.4, met één bevestigd poolrecht."""
    from datetime import UTC, datetime, timedelta

    import httpx

    from mediaguard import hub_pool

    now = datetime.now(UTC)
    token = secrets.token_urlsafe()
    ctx.config.set("global", "", hub_pool.URL_KEY, "https://hub.example.test", "test")
    ctx.secrets.set(hub_pool.SECRET, token, "test")
    sim = {"code": 200, "calls": 0, "body": {
        "kind": "pools", "status": "ok", "reason_code": None,
        "observed_at": (now - timedelta(seconds=60)).isoformat(),
        "geldig_tot": (now + timedelta(seconds=120)).isoformat(),
        "items": [{"status": "ONLINE", "gezond": True}],
    }}

    async def address(url):
        assert url.host == "hub.example.test"
        return "peer.example.test"

    def answer(request):
        sim["calls"] += 1
        assert request.method == "GET"
        assert str(request.url) == "https://peer.example.test/api/v1/inventory/pools"
        assert request.headers["Host"] == request.extensions["sni_hostname"] == "hub.example.test"
        assert request.headers["Accept"] == "application/json"
        assert not request.content and "Cookie" not in request.headers
        assert "X-CSRF-Token" not in request.headers
        if request.headers.get("Authorization") != f"Bearer {token}":
            return httpx.Response(401)
        if sim["code"] == 302:
            return httpx.Response(302, headers={"Location": "https://andere-hub.example.test"})
        return httpx.Response(sim["code"], json=sim["body"])

    monkeypatch.setattr(hub_pool, "_address", address)
    ctx.services["hub_pool_transport"] = httpx.MockTransport(answer)
    return sim
