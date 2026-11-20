"""Volg een geopende lokale stream terwijl retentie en een wachtende aflevering elkaar kruisen."""

import asyncio
import json
import os
import tempfile
from contextlib import aclosing, closing
from pathlib import Path
from unittest.mock import AsyncMock, patch

from fastapi import Request
from fastapi.testclient import TestClient
from mediaguard import db as database
from mediaguard.api.events import stream
from mediaguard.app import create_app
from mediaguard.config import load_settings
from mediaguard.maintenance.housekeeping import prune


async def controle(ctx, client):
    assert not ctx.db.fetch_all("SELECT id FROM outbox_events")
    request = Request(
        {"type": "http", "headers": [], "query_string": b""},
        receive=AsyncMock(return_value={"type": "http.request", "body": b""}),
    )
    response = await stream(request, None, ctx)
    assert response.status_code == 200
    async with aclosing(response.body_iterator) as berichten:
        assert (await anext(berichten)).startswith("retry:")
        for nummer in range(1, 5):
            # Rijen 1 en 3 zijn oud. De stream is open terwijl de nieuwe meldingen wachten.
            if nummer in (1, 3):
                with patch(
                    "mediaguard.events.utcnow", return_value="2000-01-01T00:00:00Z"
                ):
                    ctx.events.publish("library.synced", {"nr": nummer})
            else:
                ctx.events.publish("recovery.committed", {"nr": nummer})
        assert prune(ctx)["events"] == 2
        verwacht = [
            row[0]
            for row in ctx.db.fetch_all("SELECT id FROM outbox_events ORDER BY id")
        ]
        assert verwacht == [2, 4]
        polling = client.get("/connector/v1/events")
        assert polling.status_code == 200 and len(polling.json()["events"]) == 2
        resets = 0
        gelezen = []
        while 4 not in gelezen:
            frame = await asyncio.wait_for(anext(berichten), 5)
            if frame.startswith("event: reset"):
                resets += 1
            else:
                regel = next(
                    r[6:] for r in frame.splitlines() if r.startswith("data: ")
                )
                gelezen.append(json.loads(regel)["payload"]["nr"])
        # Herverbind met de door de stream zelf afgegeven positie van het laatste event.
        laatste = next(r[4:] for r in frame.splitlines() if r.startswith("id: "))
    opnieuw = Request(
        {"type": "http", "headers": [(b"last-event-id", laatste.encode())]}
    )
    hervat = await stream(opnieuw, None, ctx)
    async with aclosing(hervat.body_iterator) as vervolg:
        assert (await anext(vervolg)).startswith("retry:")
    aanwezig = [nr for nr in gelezen if nr in verwacht]
    print(
        f"Bewaard en via polling leesbaar: {verwacht}; SSE leverde {gelezen}; "
        f"resets={resets}; na hervatten vanaf 4 geen eerdere rij."
    )
    return 0 if aanwezig == verwacht else 1


assert (
    Path(database.__file__)
    .resolve()
    .is_relative_to(Path(os.environ["PROEFBRON"]).resolve())
)
with tempfile.TemporaryDirectory(dir=os.environ["TMPDIR"]) as tmp:
    root = Path(tmp)
    settings = load_settings(
        data_dir=root / "core",
        staging_dir=root / "staging",
        quarantine_dir=root / "quarantaine",
        start_background=False,
    )
    fixture = json.loads(
        (Path(os.environ["REPO"]) / "tests/fixtures/aio/core-1.2.2.json").read_text()
    )
    app = create_app(settings)
    with closing(app.state.ctx.db), TestClient(app) as client:
        assert client.post(
            "/api/v1/auth/setup",
            json={
                "username": fixture["user"],
                "password": fixture["password"],
            },
        ).is_success
        raise SystemExit(asyncio.run(controle(app.state.ctx, client)))
