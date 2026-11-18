"""Losse ingangen controleren beide databases van de app."""

import sqlite3
from contextlib import closing

import pytest
from test_upgradebewijs import ub

from mediaguard.app import create_app
from mediaguard.config import Settings
from mediaguard.db import Database
from mediaguard.schema import SchemaTooNew


@pytest.mark.parametrize("onbekend", ["core", "encode"])
@pytest.mark.parametrize("ingang", [
    "context", "database", "set", "kopie", "back-up", "onderhoud", "bootstrap",
    "noodtoegang", "bundel", "bundel-herstel",
])
def test_losse_kern_ingangen_toetsen_beide_databases(tmp_path, monkeypatch, ingang, onbekend) -> None:
    from mediaguard import bootstrap, noodtoegang
    from mediaguard.app import build_context
    from mediaguard.maintenance import api, bundle, preupdate
    from mediaguard.schema import EXIT_CODE

    core = tmp_path / "core"
    settings = Settings(data_dir=core, start_background=False)
    app = create_app(settings)
    encoder = tmp_path / "encode" / "app.db"
    encoder.parent.mkdir()
    with closing(sqlite3.connect(encoder)) as conn, conn:
        conn.execute("CREATE TABLE schema_versie (versie INTEGER PRIMARY KEY)")
        conn.execute("INSERT INTO schema_versie VALUES (?)", (999 if onbekend == "encode" else 1,))
    if onbekend == "core":
        with closing(sqlite3.connect(settings.db_path)) as conn, conn:
            conn.execute("INSERT INTO schema_migrations VALUES ('999_toekomst', 'proef')")
    before = ub.meet(tmp_path, intern=True)
    ctx = app.state.ctx
    if ingang == "context":
        assert build_context(settings).db.read_only
    elif ingang == "database":
        db = Database(settings.db_path)
        db.initialize()
        assert db.read_only
    elif ingang == "onderhoud":
        assert api.run_cycle(ctx) == {}
    elif ingang == "bootstrap":
        for name in ("APPDATA", "SECRETS", "DATA", "WORK"):
            monkeypatch.setenv(f"MG_BOOTSTRAP_{name}", str(tmp_path))
        monkeypatch.setattr(bootstrap, "run", lambda *_: pytest.fail("mappen voorbereid"))
        assert bootstrap.main() == EXIT_CODE
    elif ingang == "noodtoegang":
        assert noodtoegang.main(["--db", str(settings.db_path), "uitloggen"]) == EXIT_CODE
    else:
        with pytest.raises(SchemaTooNew):
            if ingang == "set":
                preupdate.make_set(settings.backup_dir / preupdate.FOLDER, settings.db_path,
                                   encode_dir=encoder.parent)
            elif ingang == "kopie":
                ctx.db.backup_to(settings.backup_dir / "proef.db")
            elif ingang == "bundel":
                bundle.create(ctx)
            elif ingang == "bundel-herstel":
                bundle.restore(tmp_path / "proefbundel", "proef", tmp_path / "herstelsleutel",
                               tmp_path)
            else:
                api.create_backup(ctx)
    assert not settings.backup_dir.exists()
    assert ub.meet(tmp_path, intern=True) == before
    ctx.db.close()
