"""Behoud van de gevulde historische installatie wanneer de bestaande bewijscontrole de upgrade weigert."""

import hashlib
import os
import sqlite3
import subprocess
import sys
import tempfile
from contextlib import closing
from pathlib import Path

from mediaguard import aio, db
from mediaguard.maintenance import preupdate

vorige = Path(os.environ["HISTORIE"]) / "155/apps/core"
fixture = Path(__file__).with_name("terugval-start.py")
huidig = Path(os.environ["REPO"])
assert Path(db.__file__).resolve().is_relative_to(huidig)


def stand(pad):
    with closing(
        sqlite3.connect(pad.resolve().as_uri() + "?mode=ro", uri=True)
    ) as conn:
        tabellen = conn.execute(
            "SELECT count(*) FROM sqlite_schema WHERE type='table'"
        ).fetchone()[0]
        digest = hashlib.sha256()
        for statement in conn.iterdump():
            digest.update(statement.encode())
            digest.update(b"\x00")
    return tabellen, digest.digest()


oud_env = os.environ | {"PYTHONPATH": f"{vorige}:{huidig}/scripts"}
with tempfile.TemporaryDirectory(dir=os.environ["TMPDIR"]) as werk:
    config = Path(werk)
    subprocess.run(
        [sys.executable, str(fixture), "fase", "oud", werk],
        env=oud_env,
        cwd=werk,
        check=True,
    )
    kern = config / "core/mediaguard.db"
    enc = config / "encode/app.db"
    enc.parent.mkdir()
    with closing(sqlite3.connect(enc)) as conn, conn:
        conn.execute("CREATE TABLE job(percent INTEGER)")
        conn.execute("INSERT INTO job VALUES(75)")
    voor = [stand(kern), stand(enc)]
    assert voor[0][0] > 100 and voor[1][0] == 1
    env = {}
    try:
        aio.before_services(env, config, None, migrate_core=True)
    except RuntimeError as exc:
        assert "onbekende weergave" in str(exc)
    else:
        raise AssertionError("De dubbelzinnige installatie is ten onrechte gemigreerd.")
    assert "MG_PRE_UPDATE_SET" not in env
    assert [stand(kern), stand(enc)] == voor
    sets = [
        p.parent
        for p in kern.parent.glob("backups/voor-update/*/meta*.json")
        if preupdate._read_meta(p.parent).get("from_schema", "").startswith("155_")
    ]
    assert len(sets) == 1 and not preupdate.verify(sets[0])
    info = preupdate.stage(kern.parent, sets[0], actor="herstelproef")
    assert {f["live"] for f in info["files"]} == {"core/mediaguard.db", "encode/app.db"}
    assert [
        stand(p.with_name(p.name + preupdate.STAGED_SUFFIX)) for p in (kern, enc)
    ] == voor
    code = """
from pathlib import Path
import sys
from mediaguard import aio
from mediaguard.maintenance import preupdate
config, bron = map(Path, sys.argv[1:])
assert Path(aio.__file__).resolve().is_relative_to(bron)
core = config / "core"
naam = preupdate.pending(core)["set"]
assert preupdate.find(core / "backups" / preupdate.FOLDER, naam)
aio.before_services({}, config, None)
result = preupdate.take_result(core)
assert result["ok"] and result["set"] == naam and preupdate.pending(core) is None
"""
    subprocess.run(
        [sys.executable, "-c", code, werk, str(vorige)],
        env=oud_env,
        cwd=werk,
        check=True,
    )
    assert [stand(kern), stand(enc)] == voor
    print(
        f"Schema 155 bleef intact bij weigering en na terugzetten door de oude supervisor: "
        f"{voor[0][0]} kerntabellen en {voor[1][0]} encodertabel inhoudelijk gelijk."
    )
