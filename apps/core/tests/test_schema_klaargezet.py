"""De bewaker controleert de volledige klaargezette terugzetting voor de wissel."""

import hashlib
import json
import sqlite3
from contextlib import closing

import pytest

from mediaguard import aio, schema
from mediaguard.db import Database
from mediaguard.maintenance import preupdate


@pytest.mark.parametrize("gebrek", ["verlopen", "beschadigd", "onvolledig", "ongeldige-markering"])
def test_geweigerd_herstel_laat_appdata_gelijk(tmp_path, monkeypatch, gebrek):
    core = tmp_path / "core"
    live = core / "mediaguard.db"
    db = Database(live)
    db.initialize()
    db.close()
    encode = tmp_path / "encode"
    encode.mkdir()
    with closing(sqlite3.connect(encode / "app.db")) as conn, conn:
        conn.execute("CREATE TABLE schema_versie (versie INTEGER PRIMARY KEY)")
        conn.execute("INSERT INTO schema_versie VALUES (1)")
    backup = preupdate.make_set(core / "backups" / preupdate.FOLDER, live, encode_dir=encode)
    marker = core / preupdate.STAGE_MARKER
    info = preupdate.stage(core, backup, actor="proef")
    if gebrek == "verlopen":
        info["requested_at"] = "2000-01-01T00:00:00+00:00"
    elif gebrek == "beschadigd":
        # Beschadig alleen het tweede bestand: ook het eerste mag niet omgewisseld worden.
        (encode / "app.db.terugzetten").write_bytes(b"beschadigd")
    elif gebrek == "onvolledig":
        info["files"].pop()
    elif gebrek == "ongeldige-markering":
        info["requested_at"] = "geen tijdstip"
    marker.write_text(json.dumps(info))

    # Deze proef meet de herstelbestanden. WAL-zichtbaarheid heeft een afzonderlijke proef.
    with closing(sqlite3.connect(live)) as conn, conn:
        conn.execute("PRAGMA journal_mode=DELETE")
        conn.execute("INSERT INTO schema_migrations VALUES ('999_toekomst', 'proef')")

    def boom():
        return {p.relative_to(tmp_path): hashlib.sha256(p.read_bytes()).digest()
                if p.is_file() else None for p in tmp_path.rglob("*")}

    voor = boom()
    monkeypatch.setattr(aio, "prepare", lambda _: pytest.fail("mappen voorbereid"))
    env = {}
    assert schema.MESSAGE in aio.prepare_start(env, tmp_path, None)
    assert env[schema.AIO_RECOVERY_ENV] == "1"
    assert boom() == voor


@pytest.mark.parametrize("onbekend", ["core", "encode"])
def test_klaarzetten_weigert_set_met_onbekende_versie(tmp_path, onbekend):
    core = tmp_path / "core"
    live = core / "mediaguard.db"
    db = Database(live)
    db.initialize()
    db.close()
    encode = tmp_path / "encode"
    encode.mkdir()
    with closing(sqlite3.connect(encode / "app.db")) as conn, conn:
        conn.execute("CREATE TABLE schema_versie (versie INTEGER PRIMARY KEY)")
        conn.execute("INSERT INTO schema_versie VALUES (1)")
    backup = preupdate.make_set(core / "backups" / preupdate.FOLDER, live, encode_dir=encode)
    entry = "mediaguard.db" if onbekend == "core" else "encode/app.db"
    copy = backup / entry
    sql = ("INSERT INTO schema_migrations VALUES ('999_toekomst', 'proef')" if onbekend == "core"
           else "INSERT INTO schema_versie VALUES (999)")
    with closing(sqlite3.connect(copy)) as conn, conn:
        conn.execute(sql)
    meta = backup / "meta.json"
    info = json.loads(meta.read_text())
    row = next(row for row in info["files"] if row["name"] == entry)
    row.update(size=copy.stat().st_size, sha256=hashlib.sha256(copy.read_bytes()).hexdigest())
    meta.write_text(json.dumps(info))

    with pytest.raises(schema.SchemaTooNew):
        preupdate.stage(core, backup, actor="proef")
    assert not (core / preupdate.STAGE_MARKER).exists()
    assert not list(tmp_path.rglob("*.terugzetten"))
