"""De oude installatie moet na een afgebroken upgrade veilig een aangeboden set kunnen kiezen."""

import json
import os
import sqlite3
import subprocess
import sys
from contextlib import closing, contextmanager
from pathlib import Path
from unittest.mock import patch

from fastapi.testclient import TestClient
from mediaguard import aio, db
from mediaguard.app import create_app
from mediaguard.config import Settings
from mediaguard.maintenance import preupdate

werk = Path(os.environ["WERK"])
config = werk / "installatie"
core = config / "core"
pad = core / "mediaguard.db"
repo = Path(os.environ["REPO"])
vorige = Path(os.environ["HISTORIE"]) / "155/apps/core"
vroeger = os.environ | {
    "PYTHONPATH": f"{vorige}:{repo}/scripts",
    "MG_AIO_SUPERVISOR": "1",
}
fixture = repo / "tests/fixtures/aio/core-1.2.2.json"


@contextmanager
def beheer(actie="login"):
    settings = Settings(
        data_dir=core,
        staging_dir=config / "staging",
        quarantine_dir=config / "quarantaine",
        start_background=False,
    )
    app = create_app(settings)
    with (
        closing(app.state.ctx.db),
        TestClient(app, raise_server_exceptions=False) as client,
    ):
        meta = json.loads(fixture.read_text())
        login = client.post(
            "/api/v1/auth/" + actie,
            json={"username": meta["user"], "password": meta["password"]},
        )
        assert login.status_code == 200
        client.headers["X-CSRF-Token"] = login.json()["csrf_token"]
        yield client


def oude_beheerroute():
    assert Path(db.__file__).resolve() == vorige / "mediaguard/db.py"
    with beheer() as client:
        voor = client.get("/connector/v1/events")
        assert voor.status_code == 200 and len(voor.json()["events"]) == 2
        pagina = client.get("/api/v1/backups")
        assert pagina.status_code == 200
        keuzes = [
            r for r in pagina.json() if (r.get("from_schema") or "").startswith("155_")
        ]
        if not keuzes:
            print(
                "Oude beheer-API biedt deze set niet aan; eventpagina HTTP 200 met 2/2 events."
            )
            return 0
        (keuze,) = keuzes
        assert keuze["verified"] and keuze["restore_mode"] == "bij-stoppen"
        naam = keuze["name"]
        antwoord = client.post(
            f"/api/v1/backups/{naam}/restore", json={"confirm": naam}
        )
        if antwoord.status_code == 409:
            assert preupdate.pending(core) is None
            assert client.get("/connector/v1/events").status_code == 200
            print(
                "Oude beheer-API weigert de ongeschikte set; eventpagina HTTP 200 met 2/2 events."
            )
            return 0
        assert antwoord.status_code == 200 and antwoord.json()["staged"] == naam
    aio.apply_staged_restore(config, None)
    resultaat = preupdate.take_result(core)
    assert resultaat and resultaat["ok"] and resultaat["set"] == naam
    with beheer() as client:
        na = client.get("/connector/v1/events")
        hervat = client.get(
            "/connector/v1/events", params={"cursor": voor.json()["next_cursor"]}
        )
        if na.status_code == 200:
            assert na.json()["events"] == voor.json()["events"]
            assert hervat.status_code == 200 and not hervat.json()["events"]
    with closing(sqlite3.connect(pad.as_uri() + "?mode=ro", uri=True)) as lezer:
        (events,) = lezer.execute("SELECT count(*) FROM outbox_events").fetchone()
        assert events == 2
        assert lezer.execute(
            "SELECT min(progress),max(progress) FROM jobs"
        ).fetchone() == (0.75, 0.75)
        assert (
            lezer.execute("SELECT max(version) FROM schema_migrations")
            .fetchone()[0]
            .startswith("155_")
        )
    print(
        f"Oude beheer-API: klaarzetten HTTP 200, terugzetten gemeld als succesvol; "
        f"eventpagina {voor.status_code} → {na.status_code}, hervatten {hervat.status_code}; "
        f"taak 75% en {events}/2 events op schijf."
    )
    return int(na.status_code != 200 or hervat.status_code != 200)


if sys.argv[1:] == ["oud"]:
    raise SystemExit(oude_beheerroute())
if sys.argv[1:] == ["eerste-beheerder"]:
    with beheer("setup") as client:
        pagina = client.get("/connector/v1/events")
        assert pagina.status_code == 200 and len(pagina.json()["events"]) == 2
    raise SystemExit(0)
assert (
    Path(db.__file__).resolve()
    == Path(os.environ["PROEFBRON"]) / "apps/core/mediaguard/db.py"
)
config.mkdir()
onderbreking = sys.argv[1:] == ["onderbreking"]
naam = "terugval-gevuld.py" if onderbreking else "terugval-start.py"
proef = Path(__file__).with_name(naam)
subprocess.run(
    [sys.executable, "-B", str(proef), "fase", "oud", str(config)],
    cwd=config,
    env=vroeger,
    check=True,
)
if onderbreking:
    subprocess.run(
        [sys.executable, "-B", __file__, "eerste-beheerder"],
        cwd=config,
        env=vroeger,
        check=True,
    )
initialiseer = db.Database.initialize


def stop_voor_kernstart(self, *args, **kwargs):
    archief = kwargs.get("pre_update_set")
    if onderbreking and archief is not None:
        assert not preupdate.verify(archief)
        raise InterruptedError("onderbreking na de geverifieerde set")
    return initialiseer(self, *args, **kwargs)


with patch.object(db.Database, "initialize", stop_voor_kernstart):
    try:
        preupdate.snapshot_before_start(
            config,
            core_migrations=db.MIGRATIONS_DIR,
            encoder_migrations=None,
            app_version="proef",
            migrate_core=True,
        )
    except (InterruptedError, RuntimeError) as exc:
        # De bewijsbewaking kan de op 155 bijgewerkte 1.2.2-fixture nog vóór 156 weigeren.
        melding = (
            "onderbreking na de geverifieerde set"
            if onderbreking
            else "onbekende weergave"
        )
        assert melding in str(exc)
    else:
        raise AssertionError("De proef verwacht een afgebroken of geweigerde update.")
sets = [
    p.parent
    for p in core.glob("backups/voor-update/*/meta*.json")
    if (json.loads(p.read_text()).get("from_schema") or "").startswith("155_")
]
assert (
    len(sets) == 1 and not preupdate.verify(sets[0]) and preupdate.pending(core) is None
)
result = subprocess.run(
    [sys.executable, "-B", __file__, "oud"], check=False, cwd=config, env=vroeger
)
raise SystemExit(result.returncode)
