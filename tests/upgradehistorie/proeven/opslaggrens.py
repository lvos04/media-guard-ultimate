"""Controleer als kern de locatie van de historische sleutel zolang terugzetten nog wacht."""

import hashlib
import json
import os
import sqlite3
import subprocess
import sys
from contextlib import closing
from pathlib import Path

from afscherming import controleer_afscherming
from fastapi.testclient import TestClient
from mediaguard import aio, db, event_keys
from mediaguard.app import create_app
from mediaguard.config import Settings
from mediaguard.maintenance import preupdate

werk = Path(os.environ["WERK"])
repo = Path(os.environ["REPO"])
assert Path(db.__file__).resolve() == repo / "apps/core/mediaguard/db.py"
root = werk / "installatie"
root.mkdir()
core = root / "core"
historie = Path(os.environ["HISTORIE"]) / "155/apps/core"
subprocess.run(
    [
        sys.executable,
        "-B",
        str(Path(__file__).with_name("terugval-gevuld.py")),
        "fase",
        "oud",
        str(root),
    ],
    cwd=root,
    check=True,
    env=os.environ | {"PYTHONPATH": f"{historie}:{repo}/scripts"},
)
env = {}
aio.before_services(env, root, None, migrate_core=True)
archief = Path(env["MG_PRE_UPDATE_SET"])
controlewaarde = (root / "digest").read_bytes()


def bevat_historische_waarde(pad):
    with closing(
        sqlite3.connect(pad.as_uri() + "?mode=ro&immutable=1", uri=True)
    ) as lezer:
        lezer.create_function(
            "historisch",
            1,
            lambda v: (
                isinstance(v, bytes) and hashlib.sha256(v).digest() == controlewaarde
            ),
        )
        return bool(
            lezer.execute(
                "SELECT historisch(cursor_key) FROM outbox_retention"
            ).fetchone()[0]
        )


assert not bevat_historische_waarde(archief / "mediaguard.db")
os.environ["MG_AIO_SUPERVISOR"] = "1"
app = create_app(
    Settings(
        data_dir=core,
        staging_dir=root / "staging",
        quarantine_dir=root / "quarantaine",
        start_background=False,
    )
)
with closing(app.state.ctx.db), TestClient(app) as client:
    meta = json.loads((repo / "tests/fixtures/aio/core-1.2.2.json").read_text())
    login = client.post(
        "/api/v1/auth/setup",
        json={"username": meta["user"], "password": meta["password"]},
    )
    assert login.status_code == 200
    client.headers["X-CSRF-Token"] = login.json()["csrf_token"]
    antwoord = client.post(
        f"/api/v1/backups/{archief.name}/restore", json={"confirm": archief.name}
    )
    assert antwoord.status_code == 200
    wacht = preupdate.pending(core)
    assert wacht and wacht["set"] == archief.name
    assert client.get("/connector/v1/events").status_code == 200
    (item,) = [r for r in wacht["files"] if r["live"] == "core/mediaguard.db"]
    kopie = (root / item["staged"]).resolve()
    privemap = event_keys.key_path(core).parent.resolve()
    vroeg = not kopie.is_relative_to(privemap) and bevat_historische_waarde(kopie)
    assert kopie.stat().st_uid == os.geteuid() and kopie.stat().st_mode & 0o777 == 0o600
print(
    f"Wachtende terugval: dienst HTTP 200; historische sleutel in het archief nee, "
    f"in de klaargezette database buiten secrets/core {'ja' if vroeg else 'nee'}."
)
if sys.argv[1:] == ["historisch"]:
    raise SystemExit(int(vroeg))
# Deze expliciete kopie moet door de oudere beheerroute te herstellen zijn.
assert vroeg
controleer_afscherming(
    {
        "MG_AIO_CONFIG": str(root),
        "MG_AIO_WORK": str(root / "werk"),
        "MG_AIO_DATA": str(root / "data"),
    },
    kopie,
)
print(
    "De kern kan de gekozen stand lezen; alle zeven nevendiensten krijgen een leesweigering."
)
