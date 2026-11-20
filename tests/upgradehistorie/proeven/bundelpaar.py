"""Herstel een lokaal en offsite sleutelpaar van vóór het gebonden bundelbewijs."""

import json
import os
import secrets
import subprocess
import sys
from contextlib import closing
from pathlib import Path

import httpx
from fastapi.testclient import TestClient
from mediaguard.app import build_context, create_app
from mediaguard.config import load_settings
from mediaguard.instances.registry import list_instances
from mediaguard.maintenance import bundle
from mediaguard.maintenance.api import register_settings


def maak_oud():
    register_settings()
    ctx = build_context(load_settings())
    with closing(ctx.db):
        inrichting = bundle.setup(ctx, "proef")
        gemaakt = bundle.create(ctx)
        print(json.dumps([gemaakt["file"], inrichting["recovery_code"]]))


def herstel():
    werk = Path(os.environ["WERK"])
    bron = Path(os.environ["HISTORIE"]) / "voor-bundelbewijs/apps/core"
    oud = subprocess.run(
        [sys.executable, "-B", __file__, "maak"],
        cwd=bron, env={**os.environ, "PYTHONPATH": str(bron)}, check=True,
        text=True, capture_output=True, timeout=60,
    )
    naam, code = json.loads(oud.stdout.splitlines()[-1])
    app = create_app(load_settings())
    ctx = app.state.ctx
    with closing(ctx.db), TestClient(app) as client:
        login = client.post("/api/v1/auth/setup", json={
            "username": "proef", "password": secrets.token_urlsafe(20),
        })
        assert login.status_code == 200
        client.headers["X-CSRF-Token"] = login.json()["csrf_token"]
        lokaal = bundle.bundle_dir(ctx)
        assert not bundle.has_pair_proof(lokaal / naam)
        doel = werk / "offsite"
        doel.mkdir()
        ctx.config.set("global", "", "backup.offsite.kind", "path", "proef")
        ctx.config.set("global", "", "backup.offsite.path", str(doel), "proef")
        assert client.post("/api/v1/notify/offsite/run").json()["ok"]
        for mapje, hersteld in ((lokaal, werk / "uit-lokaal"), (doel, werk / "uit-offsite")):
            assert "core/mediaguard.db" in bundle.restore(
                mapje / naam, code, mapje / bundle.KEYFILE, hersteld)
        assert ((werk / "uit-lokaal/core/mediaguard.db").read_bytes()
                == (werk / "uit-offsite/core/mediaguard.db").read_bytes())
        for instance in list_instances(ctx):
            ctx.services.setdefault("arr_transports", {})[instance.id] = httpx.MockTransport(
                lambda request: httpx.Response(200, json=[]))
        antwoord = client.get("/connector/v1/items?type=backups")
        assert antwoord.status_code == 200
        item, = antwoord.json()["items"]
        assert item["status"] == item["bundle"]["status"] == item["offsite"]["status"] == "degraded"
        assert not item["bundle"]["verified"] and not item["offsite"]["verified"]
    print("Oud sleutelpaar lokaal en offsite hersteld, beide dekkingen degraded.")


if __name__ == "__main__":
    if sys.argv[1:] == ["maak"]:
        maak_oud()
    else:
        herstel()
