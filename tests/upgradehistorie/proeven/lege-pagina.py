"""Hervat met de echt uitgegeven cursor van een lege pagina na snapshotherstel."""

import hashlib
import json
import os
import sqlite3
import tempfile
from contextlib import closing, contextmanager
from pathlib import Path
from unittest.mock import patch

from fastapi.testclient import TestClient
from mediaguard import db as database
from mediaguard.app import create_app
from mediaguard.config import load_settings
from mediaguard.maintenance.housekeeping import prune

route = "/connector/v1/events"
oud = "2000-01-01T00:00:00Z"
repo = Path(os.environ["REPO"])
assert (
    Path(database.__file__)
    .resolve()
    .is_relative_to(Path(os.environ["PROEFBRON"]).resolve())
)


@contextmanager
def open_app(settings, *, eerste=False):
    fixture = json.loads((repo / "tests/fixtures/aio/core-1.2.2.json").read_text())
    app = create_app(settings)
    with closing(app.state.ctx.db), TestClient(app) as client:
        route_auth = "setup" if eerste else "login"
        assert client.post(
            f"/api/v1/auth/{route_auth}",
            json={
                "username": fixture["user"],
                "password": fixture["password"],
            },
        ).is_success
        yield app.state.ctx, client


def stamp_oud(ctx, positie):
    ctx.db.execute("UPDATE outbox_events SET ts=? WHERE id=?", (oud, positie))


def publiceer_verlopen(ctx, aantal):
    with patch("mediaguard.events.utcnow", return_value=oud):
        for _ in range(aantal):
            ctx.events.publish("library.synced", {})


def pagina(client, **params):
    antwoord = client.get(route, params=params)
    assert antwoord.status_code == 200
    return antwoord.json()


with tempfile.TemporaryDirectory(dir=os.environ["TMPDIR"]) as tmp:
    root = Path(tmp)
    settings = load_settings(
        data_dir=root / "core",
        staging_dir=root / "staging",
        quarantine_dir=root / "quarantaine",
        start_background=False,
    )
    snapshot = root / "momentopname.db"
    with open_app(settings, eerste=True) as (ctx, client):
        ctx.events.publish("library.synced", {})
        assert len(pagina(client)["events"]) == 1
        ctx.db.backup_to(snapshot)
        sha = hashlib.sha256(snapshot.read_bytes()).digest()
        publiceer_verlopen(ctx, 3)
        verwerkt = pagina(client)
        assert len(verwerkt["events"]) == 4
        stamp_oud(ctx, 1)
        assert prune(ctx)["events"] == 4
        leeg = pagina(client)
        assert leeg["events"] == []
        # De echte cursor van deze lege pagina is correct op de oorspronkelijke tijdlijn.
        assert pagina(client, cursor=leeg["next_cursor"]) == leeg
        gerapporteerd = leeg["next_cursor"].split(".")
        assert gerapporteerd[1:3] == ["4", "4"]

    assert hashlib.sha256(snapshot.read_bytes()).digest() == sha
    with (
        closing(sqlite3.connect(snapshot)) as bewaard,
        closing(sqlite3.connect(settings.db_path)) as live,
    ):
        bewaard.backup(live)
    with open_app(settings) as (ctx, client):
        assert len(pagina(client)["events"]) == 1
        ctx.events.publish("recovery.committed", {})
        publiceer_verlopen(ctx, 4)
        assert prune(ctx)["events"] == 4
        stamp_oud(ctx, 1)
        assert prune(ctx)["events"] == 1
        revisie, grens = ctx.db.fetch_one(
            "SELECT revision, deleted_through FROM outbox_retention"
        )
        assert (revisie, grens) == (5, 6)
        vrij = pagina(client)
        assert [e["event_type"] for e in vrij["events"]] == ["media.recovery.succeeded"]
        behouden = ctx.db.fetch_all("SELECT id FROM outbox_events")
        assert [r[0] for r in behouden] == [2]
        antwoord = client.get(route, params={"cursor": leeg["next_cursor"]})
        if antwoord.status_code == 409:
            inhoud = antwoord.json()
            assert inhoud["code"] == "cursor_expired" and inhoud["details"] == {
                "restart": True
            }
            print(
                "Lege-paginacursor na herstel: HTTP 409 cursor_expired, zonder cursor 1 vervangend event."
            )
        else:
            assert antwoord.status_code == 200
            vervolg = antwoord.json()["events"]
            assert not vervolg
            nogmaals = pagina(client, cursor=antwoord.json()["next_cursor"])
            assert not nogmaals["events"]
            print(
                "Lege-paginacursor na herstel: HTTP 200, 0 events; ook volgende pagina 0; zonder cursor 1 vervangend event."
            )
            raise SystemExit(1)
