"""Controleer een bewaarde setnaam rechtstreeks op de oude beheerroute."""

import json
import os
import subprocess
import sys
from contextlib import closing
from pathlib import Path

from fastapi.testclient import TestClient
from mediaguard import db
from mediaguard.app import create_app
from mediaguard.config import Settings
from mediaguard.maintenance import preupdate

werk = Path(os.environ["WERK"])
repo = Path(os.environ["REPO"])
oud = Path(os.environ["HISTORIE"]) / "155/apps/core"
config = werk / "installatie"
core = config / "core"


def controleer_oude_route(naam):
    assert Path(db.__file__).resolve().is_relative_to(oud)
    fixture = json.loads((repo / "tests/fixtures/aio/core-1.2.2.json").read_text())
    app = create_app(
        Settings(
            data_dir=core,
            staging_dir=config / "staging",
            quarantine_dir=config / "quarantaine",
            start_background=False,
        )
    )
    with closing(app.state.ctx.db), TestClient(app) as client:
        login = client.post(
            "/api/v1/auth/login",
            json={"username": fixture["user"], "password": fixture["password"]},
        )
        assert login.status_code == 200
        client.headers["X-CSRF-Token"] = login.json()["csrf_token"]
        r = client.get("/connector/v1/events")
        assert r.status_code == 200 and len(r.json()["events"]) == 2
        pagina = r.json()
        lijst = client.get("/api/v1/backups")
        assert lijst.status_code == 200 and all(
            item["name"] != naam for item in lijst.json()
        )
        r = client.post(f"/api/v1/backups/{naam}/restore", json={"confirm": naam})
        assert r.status_code in {400, 404, 409, 422} and preupdate.pending(core) is None
        na = client.get("/connector/v1/events")
        assert na.status_code == 200 and na.json()["events"] == pagina["events"]
        hervat = client.get(
            "/connector/v1/events", params={"cursor": pagina["next_cursor"]}
        )
        assert hervat.status_code == 200 and hervat.json()["events"] == []
        print(
            f"Oude beheerroute weigert de bekende setnaam met HTTP {r.status_code}; "
            "geen wachtende aanvraag; 2/2 events en de oorspronkelijke cursor geven HTTP 200."
        )


if len(sys.argv) == 2:
    controleer_oude_route(sys.argv[1])
else:
    subprocess.run(
        [
            sys.executable,
            "-B",
            str(Path(__file__).with_name("oude-keuze.py")),
            "onderbreking",
        ],
        check=True,
    )
    (manifest,) = core.glob("backups/voor-update/*/meta-met-sleutelverwijzing.json")
    assert not preupdate.verify(manifest.parent)
    env = os.environ | {"PYTHONPATH": f"{oud}:{repo}/scripts", "MG_AIO_SUPERVISOR": "1"}
    subprocess.run(
        [sys.executable, "-B", __file__, manifest.parent.name],
        cwd=config,
        env=env,
        check=True,
    )
