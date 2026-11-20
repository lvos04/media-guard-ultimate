"""De bewaker controleert de volledige klaargezette terugzetting voor de wissel."""

import hashlib
import json
import sqlite3
from contextlib import closing

import pytest
from av1_fixture import maak_av1_v1

from mediaguard import aio, schema
from mediaguard.db import Database
from mediaguard.maintenance import preupdate, status


def test_ontbrekende_klaargezette_kopie_geeft_beheerder_een_melding(ctx):
    core, live = ctx.settings.data_dir, ctx.db.path
    backup = preupdate.make_set(core / "backups" / preupdate.FOLDER, live, encode_dir=None)
    preupdate.stage(core, backup, actor="proef")
    live.with_name(live.name + preupdate.STAGED_SUFFIX).unlink()
    voor = live.read_bytes()

    result = preupdate.apply_staged(core)
    assert result and not result["ok"]
    assert not preupdate.pending(core)
    assert live.read_bytes() == voor
    assert not (core / "backups" / preupdate.ASIDE_FOLDER).exists()
    status.check(ctx)
    assert ctx.db.fetch_one("SELECT 1 FROM notifications WHERE title='Terugzetten niet gelukt'")


def test_geweigerde_markering_raakt_geen_kopie_buiten_appdata(tmp_path):
    core = tmp_path / "appdata" / "core"
    db = Database(core / "mediaguard.db")
    db.initialize()
    db.close()
    extern = tmp_path / "elders"
    extern.mkdir()
    kopie = extern / "app.db.terugzetten"
    kopie.write_bytes(b"proef")
    (core.parent / "encode").symlink_to(extern, target_is_directory=True)
    marker = core / preupdate.STAGE_MARKER
    marker.write_text(json.dumps({"files": [{"live": "encode/app.db"}]}))

    result = preupdate.apply_staged(core)
    assert result and not result["ok"]
    assert not marker.exists()
    assert kopie.read_bytes() == b"proef"


@pytest.mark.parametrize("wal_met_inhoud", [False, True])
def test_herhaal_legacy_terugzetting_met_sqlite_hulpbestanden(tmp_path, wal_met_inhoud):
    core = tmp_path / "core"
    db = Database(core / "mediaguard.db")
    db.initialize()
    db.execute("INSERT INTO system_state VALUES ('proef', '17', 'proef')")
    archief = core / "backups" / preupdate.FOLDER / "mediaguard-001-naar-002-20260101T000000Z.db"
    db.backup_to(archief)
    preupdate.stage(core, archief, actor="proef")
    assert preupdate.cancel(core)
    db.execute("UPDATE system_state SET value_json='18' WHERE key='proef'")
    db.close()

    # De eerdere release opende de kopie in WAL-modus tijdens het klaarzetten.
    with closing(sqlite3.connect(archief)) as werk:
        werk.execute("PRAGMA journal_mode=WAL")
        werk.execute("SELECT count(*) FROM system_state").fetchone()
        if wal_met_inhoud:
            werk.execute("UPDATE system_state SET value_json='19' WHERE key='proef'")
            werk.commit()
        wal = archief.with_name(archief.name + "-wal")
        assert wal.exists() and (wal.stat().st_size > 0) == wal_met_inhoud
        if wal_met_inhoud:
            with pytest.raises(preupdate.SetError):
                preupdate.stage(core, archief, actor="proef")
            assert preupdate.pending(core) is None
            return
        preupdate.stage(core, archief, actor="proef")
        result = preupdate.apply_staged(core)
        assert result and result["ok"]
    assert db.fetch_one("SELECT value_json FROM system_state WHERE key='proef'")[0] == "17"


def test_set_bevat_geen_sqlite_hulpbestanden_van_tijdelijke_kopie(tmp_path):
    core = tmp_path / "core"
    db = Database(core / "mediaguard.db")
    db.initialize()
    db.close()
    made = preupdate.make_set(core / "backups" / preupdate.FOLDER, db.path, encode_dir=None)
    assert {p.name for p in made.iterdir()} == {"mediaguard.db", "meta.json"}


@pytest.mark.parametrize(
    "gebrek", ["verlopen", "ontbrekend", "beschadigd", "onvolledig", "ongeldige-markering"],
)
def test_geweigerd_herstel_laat_appdata_gelijk(tmp_path, monkeypatch, gebrek):
    core = tmp_path / "core"
    live = core / "mediaguard.db"
    db = Database(live)
    db.initialize()
    db.close()
    encode = tmp_path / "encode"
    encode.mkdir()
    with closing(sqlite3.connect(encode / "app.db")) as conn, conn:
        maak_av1_v1(conn)
    backup = preupdate.make_set(core / "backups" / preupdate.FOLDER, live, encode_dir=encode)
    marker = core / preupdate.STAGE_MARKER
    info = preupdate.stage(core, backup, actor="proef")
    if gebrek == "verlopen":
        info["requested_at"] = "2000-01-01T00:00:00+00:00"
    elif gebrek == "ontbrekend":
        (encode / "app.db.terugzetten").unlink()
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
        maak_av1_v1(conn)
    backup = preupdate.make_set(core / "backups" / preupdate.FOLDER, live, encode_dir=encode)
    entry = "mediaguard.db" if onbekend == "core" else "encode/app.db"
    copy = backup / entry
    sql = ("INSERT INTO schema_migrations VALUES ('999_toekomst', 'proef')" if onbekend == "core"
           else "INSERT INTO schema_versie(versie) VALUES (999)")
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
