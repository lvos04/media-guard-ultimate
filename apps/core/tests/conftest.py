"""Gedeelde fixtures: een core-app met tijdelijke datamap en ingelogde admin."""

from __future__ import annotations

from collections.abc import Iterator
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from mediaguard.app import create_app
from mediaguard.config import load_settings


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
    application.state.ctx.db.close()


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
def anon_client(app) -> Iterator[TestClient]:  # type: ignore[no-untyped-def]
    with TestClient(app) as c:
        yield c
