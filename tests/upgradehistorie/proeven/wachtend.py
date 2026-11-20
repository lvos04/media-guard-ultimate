"""Meet beide procesgrenzen tijdens een via de beheer-API aangevraagde terugval."""

import json
import os
import subprocess
import sys
from contextlib import closing
from pathlib import Path
from unittest.mock import patch

from afscherming import controleer_afscherming
from fastapi.testclient import TestClient
from mediaguard import aio, db
from mediaguard.app import create_app
from mediaguard.config import Settings
from mediaguard.maintenance import preupdate

werk = Path(os.environ["WERK"])
repo = Path(os.environ["REPO"])
config = werk / "installatie"
core = config / "core"
oud = Path(os.environ["HISTORIE"]) / "155/apps/core"
fixture = json.loads((repo / "tests/fixtures/aio/core-1.2.2.json").read_text())
oud_env = os.environ | {"PYTHONPATH": f"{oud}:{repo}/scripts", "MG_AIO_SUPERVISOR": "1"}
positie = werk / "oude-pagina.json"


def web():
    return create_app(
        Settings(
            data_dir=core,
            staging_dir=config / "staging",
            quarantine_dir=config / "quarantaine",
            start_background=False,
        )
    )


def login(client, actie):
    r = client.post(
        "/api/v1/auth/" + actie,
        json={"username": fixture["user"], "password": fixture["password"]},
    )
    assert r.status_code == 200
    client.headers["X-CSRF-Token"] = r.json()["csrf_token"]


def oude_fase(actie):
    assert Path(db.__file__).resolve().is_relative_to(oud)
    if actie == "oud-lezen":
        from types import SimpleNamespace

        from fastapi import Request
        from mediaguard.connector.events import poll

        with closing(db.Database(core / "mediaguard.db")) as kern:
            req = Request({"type": "http", "query_string": b"", "headers": []})
            pagina = poll(req, None, SimpleNamespace(db=kern))
            assert len(pagina.events) == 2
            positie.write_text(pagina.model_dump_json())
        return
    if actie == "setup":
        with patch.object(aio, "prepare", return_value=[]):
            env = {}
            aio.prepare_start(env, config, None)
        assert "MG_AIO_SCHEMA_RECOVERY" not in env and preupdate.take_result(core)["ok"]
    app = web()
    with closing(app.state.ctx.db), TestClient(app) as client:
        login(client, actie)
        r = client.get("/connector/v1/events")
        assert r.status_code == 200 and len(r.json()["events"]) == 2
        was = json.loads(positie.read_text())
        assert r.json()["events"] == was["events"]
        vervolg = client.get(
            "/connector/v1/events", params={"cursor": was["next_cursor"]}
        )
        assert vervolg.status_code == 200 and vervolg.json()["events"] == []


if len(sys.argv) > 1:
    oude_fase(sys.argv[1])
    raise SystemExit(0)

config.mkdir()
assert Path(db.__file__).resolve().is_relative_to(repo)
proef = Path(__file__).with_name("terugval-gevuld.py")
subprocess.run(
    [sys.executable, "-B", proef, "fase", "oud", config],
    env=oud_env,
    cwd=config,
    check=True,
)
subprocess.run(
    [sys.executable, "-B", __file__, "oud-lezen"], env=oud_env, cwd=config, check=True
)
env = {
    "MG_AIO_CONFIG": str(config),
    "MG_AIO_WORK": str(config / "work"),
    "MG_AIO_DATA": str(config / "data"),
}
aio.before_services(env, config, None, migrate_core=True)
archief = Path(env["MG_PRE_UPDATE_SET"])
assert not preupdate.verify(archief)
os.environ["MG_AIO_SUPERVISOR"] = "1"
app = web()
with closing(app.state.ctx.db), TestClient(app) as client:
    login(client, "setup")
    assert (
        client.post(
            f"/api/v1/backups/{archief.name}/restore", json={"confirm": archief.name}
        ).status_code
        == 200
    )
    assert client.get("/connector/v1/events").status_code == 200
    info = preupdate.pending(core)
    assert info and info["set"] == archief.name
    (staged,) = [
        config / f["staged"] for f in info["files"] if f["live"] == "core/mediaguard.db"
    ]
    assert staged.is_file() and staged.stat().st_mode & 0o777 == 0o600
    controleer_afscherming(env, staged)
subprocess.run(
    [sys.executable, "-B", __file__, "setup"], env=oud_env, cwd=config, check=True
)
print(
    "Bij HTTP 200 kregen 7/7 nevendiensten geen toegang tot het proefbestand of de wachtende kopie; schema 155 herstelde 2/2 events en hervatte de oorspronkelijke cursor met HTTP 200."
)
