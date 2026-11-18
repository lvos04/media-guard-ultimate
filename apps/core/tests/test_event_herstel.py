"""Negatieve proeven voor de herstelset van vóór migratie 151."""

from __future__ import annotations

import errno
import os
import sqlite3
from contextlib import closing
from pathlib import Path

import pytest

from mediaguard import aio
from mediaguard import db as database
from mediaguard.maintenance import preupdate

STAMP = "2020-01-01T00:00:00Z"


@pytest.fixture
def old_schema(tmp_path, settings, monkeypatch):
    previous = tmp_path / "voor-151"
    previous.mkdir()
    for script in database.MIGRATIONS_DIR.glob("*.sql"):
        if script.name < "151_":
            (previous / script.name).symlink_to(script)
    with monkeypatch.context() as old, closing(database.Database(settings.db_path)) as db:
        old.setattr(database, "MIGRATIONS_DIR", previous)
        db.initialize()
        db.execute(
            "INSERT INTO outbox_events(ts,topic,payload) VALUES (?,?,?)",
            (STAMP, "library.synced", "{}"),
        )
    return previous


@pytest.mark.parametrize("via", ["supervisor", "kern"])
def test_archieffout_stopt_voor_migratie_151(settings, old_schema, monkeypatch, via):
    path = settings.db_path
    replace = os.replace
    attempts = []

    def full(src, dst):
        if Path(dst).name == "mediaguard.db" and Path(dst).parent.name.endswith(".part"):
            attempts.append(1)
            raise OSError(errno.ENOSPC, "archiefopslag vol")
        return replace(src, dst)

    # De supervisor kent de vaste appdatamap 'core'.
    config = path.parent.parent / "aio-data"
    core = config / "core"
    config.mkdir()
    path.parent.rename(core)
    path = core / path.name
    with monkeypatch.context() as blocked, closing(database.Database(path)) as db:
        blocked.setattr(os, "replace", full)
        with pytest.raises(RuntimeError, match="back-up"):
            if via == "kern":
                db.initialize()
            else:
                aio.before_services({}, config, None)
    assert attempts == [1]
    with closing(sqlite3.connect(path)) as reader:
        assert not reader.execute(
            "SELECT 1 FROM schema_migrations WHERE version='151_outbox_event_identity'"
        ).fetchone()
        assert reader.execute("SELECT topic FROM outbox_events").fetchall() == [("library.synced",)]
    assert not list(core.glob("backups/voor-update/*/meta.json"))
    with closing(database.Database(path)) as db:
        db.initialize()
        assert db.fetch_one(
            "SELECT 1 FROM schema_migrations WHERE version='151_outbox_event_identity'"
        )
    sets = list(core.glob("backups/voor-update/*/meta.json"))
    assert len(sets) == 1 and not preupdate.verify(sets[0].parent)


def test_beschadigde_set_of_oude_markering_geeft_geen_toestemming(
    old_schema,
    settings,
    tmp_path,
    monkeypatch,
):
    claimed = tmp_path / "vorige-set"
    claimed.mkdir()
    (claimed / "meta.json").write_text("{}")
    monkeypatch.setenv("MG_PRE_UPDATE_SET", str(claimed))
    copy_database = preupdate._copy_sqlite

    def corrupt(src, dest):
        info = copy_database(src, dest)
        dest.write_bytes(b"beschadigde proefkopie")
        return info

    monkeypatch.setattr(preupdate, "_copy_sqlite", corrupt)
    monkeypatch.setattr(database, "active_migration", lambda _: {"pid": 1})
    with closing(database.Database(settings.db_path)) as db:
        with pytest.raises(RuntimeError, match="back-up"):
            db.initialize()
        assert not db.fetch_one("SELECT 1 FROM schema_migrations WHERE version LIKE '151_%'")
        assert db.fetch_one("SELECT topic FROM outbox_events")[0] == "library.synced"
        assert not list(settings.data_dir.glob("backups/voor-update/*/meta.json"))


@pytest.mark.parametrize("changed", [False, True])
def test_kern_hergebruikt_uitsluitend_een_actuele_supervisorset(settings, old_schema, changed,
                                                                monkeypatch):
    config = settings.data_dir.parent / "aio"
    config.mkdir()
    core = config / "core"
    settings.data_dir.rename(core)
    path = core / "mediaguard.db"
    env = {}
    aio.before_services(env, config, None)
    archive = Path(env["MG_PRE_UPDATE_SET"])
    assert preupdate.verify(archive) == []
    if changed:
        with monkeypatch.context() as old, closing(database.Database(path)) as db:
            old.setattr(database, "MIGRATIONS_DIR", old_schema)
            db.execute("INSERT INTO outbox_events(ts,topic,payload) VALUES (?,?,?)",
                       (STAMP, "recovery.committed", "{}"))
    monkeypatch.setenv("MG_PRE_UPDATE_SET", str(archive))
    with closing(database.Database(path)) as db:
        db.initialize()
    sets = sorted(p.parent for p in (core / "backups").glob("voor-update/*/meta.json"))
    assert len(sets) == 1 + int(changed)
    with closing(sqlite3.connect(sets[-1] / "mediaguard.db")) as saved:
        assert saved.execute("SELECT COUNT(*) FROM outbox_events").fetchone()[0] == 1 + int(changed)
