"""Onderbreking, oude lezers en de vrijgave van een gevulde alles-in-één-installatie."""

import os
import sqlite3
import subprocess
import sys
from contextlib import closing, contextmanager
from pathlib import Path

import pytest

from mediaguard import aio, db, event_keys
from mediaguard.maintenance import preupdate


@pytest.fixture
def encoder_update(tmp_path):
    """De actieve AV1-taak en zijn volgende genummerde migratie."""
    encoder = tmp_path / "encode/app.db"
    encoder.parent.mkdir()
    sql = tmp_path / "motor/app/migrations"
    sql.mkdir(parents=True)
    (sql / "001_basis.sql").write_text("")
    (sql / "002_vervolg.sql").write_text("CREATE TABLE vervolg (id INTEGER);")
    with closing(sqlite3.connect(encoder)) as con, con:
        con.executescript("""
            CREATE TABLE schema_versie (versie INTEGER PRIMARY KEY, naam TEXT, toegepast_op TEXT);
            INSERT INTO schema_versie VALUES (1, '001_basis.sql', 'proef');
            CREATE TABLE job (percent INTEGER);
            INSERT INTO job VALUES (25);
            CREATE TABLE auditlog (actie TEXT);
        """)
    env = {"MG_AIO_ENCODER_DIR": str(sql.parents[1])}
    return encoder, sql, env


@pytest.fixture
def kern_155(tmp_path, monkeypatch):
    live = tmp_path / "core/mediaguard.db"
    vroeger = tmp_path / "schema-155"
    vroeger.mkdir()
    for script in db.MIGRATIONS_DIR.glob("*.sql"):
        if script.name < "156_":
            (vroeger / script.name).symlink_to(script)
    with monkeypatch.context() as versie, closing(db.Database(live)) as kern:
        versie.setattr(db, "MIGRATIONS_DIR", vroeger)
        kern.initialize()
        kern.execute("UPDATE outbox_retention SET cursor_key=randomblob(32)")
        kern.execute("INSERT INTO outbox_events(ts,topic,payload) VALUES(?,?,?)",
                     (db.utcnow(), "library.synced", "{}"))
    return live


def test_alleen_encoder_migreert_onder_de_laatste_geverifieerde_set(
    tmp_path, monkeypatch, encoder_update,
):
    core = tmp_path / "core"
    with closing(db.Database(core / "mediaguard.db")) as kern:
        kern.initialize()
    encoder, sql, env = encoder_update
    maak = preupdate._make_set
    publicaties = []

    def na_publicatie(*args, **kwargs):
        archief = maak(*args, **kwargs)
        publicaties.append(archief)
        if len(publicaties) == 1:
            with closing(sqlite3.connect(encoder, timeout=0)) as schrijver, schrijver:
                schrijver.execute("UPDATE job SET percent=75")
                schrijver.execute("INSERT INTO auditlog VALUES ('hervat')")
        return archief

    monkeypatch.setattr(preupdate, "_make_set", na_publicatie)
    regels = aio.before_services(env, tmp_path, None, migrate_core=True)
    gekozen = Path(env["MG_PRE_UPDATE_SET"])
    assert any(gekozen.name in regel for regel in regels)
    assert not preupdate.verify(gekozen)
    with closing(sqlite3.connect(encoder)) as con:
        assert con.execute("SELECT max(versie) FROM schema_versie").fetchone() == (2,)
    preupdate.stage(core, gekozen, actor="proef")
    assert preupdate.apply_staged(core, av1_migrations=sql)["ok"]
    with closing(sqlite3.connect(encoder)) as con:
        assert con.execute("SELECT percent FROM job").fetchall() == [(75,)]
        assert con.execute("SELECT actie FROM auditlog").fetchall() == [("hervat",)]
        assert con.execute("SELECT max(versie) FROM schema_versie").fetchone() == (1,)


def test_archief_met_historische_verwijzing_weigert_onvoorbereid_oud_herstel(tmp_path):
    core = tmp_path / "core"
    core.mkdir()
    live = core / "mediaguard.db"
    with closing(sqlite3.connect(live)) as con, con:
        con.execute("CREATE TABLE outbox_retention (singleton INTEGER, cursor_key BLOB)")
        con.execute("INSERT INTO outbox_retention VALUES (1, ?)", (os.urandom(32),))
    archief = preupdate.make_set(core / "backups" / preupdate.FOLDER, live, encode_dir=None)
    assert not preupdate.verify(archief)
    # De oude beheer-API toont alleen sets met meta.json en weigert direct klaarzetten
    # als deze ontbreekt. De numerieke naam blijft bruikbaar voor de oude supervisor.
    assert not (archief / "meta.json").exists()
    assert [row["name"] for row in preupdate.list_sets(archief.parent)] == [archief.name]
    assert preupdate.stage(core, archief, actor="proef")["set"] == archief.name
    assert preupdate.apply_staged(core)["ok"]


@pytest.mark.parametrize("vroeger", [False, True], ids=["publicatie", "oude-onderbreking"])
def test_herstart_na_sleutelpublicatie_behoudt_dezelfde_sleutel(tmp_path, vroeger):
    core = tmp_path / "core"
    bestand = event_keys.key_path(core)
    bestand.parent.mkdir(mode=0o700, parents=True)
    if vroeger:
        # De vorige schrijver kon na link() en voor het opruimen van NamedTemporaryFile uitvallen.
        deel = bestand.parent / "tmp1234abcd"
        with deel.open("xb") as handle:
            os.fchmod(handle.fileno(), 0o600)
            handle.write(os.urandom(32))
        os.link(deel, bestand)
    else:
        script = """
import os, sys
from pathlib import Path
from mediaguard import event_keys
pad = Path(sys.argv[1])
def haak(naam):
    origineel = getattr(os, naam)
    def onderbreek(src, dst, *args, **kwargs):
        origineel(src, dst, *args, **kwargs)
        if Path(dst).name == pad.name:
            os._exit(81)
    setattr(os, naam, onderbreek)
for naam in ("link", "replace", "rename"):
    haak(naam)
event_keys.ensure_key(pad)
"""
        proc = subprocess.run([sys.executable, "-B", "-c", script, str(bestand)], check=False)
        assert proc.returncode == 81
    origineel = bestand.read_bytes()
    for _ in range(2):
        event_keys.ensure_key(bestand)
        assert bestand.stat().st_nlink == 1
        if event_keys.read_key(core, event_keys.KEY_REF) != origineel:
            pytest.fail("De uitgegeven sleutel is veranderd", pytrace=False)


def test_een_onbekende_link_blijft_geweigerd(tmp_path):
    core = tmp_path / "core"
    pad = event_keys.key_path(core)
    event_keys.ensure_key(pad)
    os.link(pad, tmp_path / "andere-naam")
    with pytest.raises(PermissionError):
        event_keys.ensure_key(pad)
    assert event_keys.read_key(core, event_keys.KEY_REF) is None


def test_geweigerde_terugval_blijft_bij_elke_start_geblokkeerd(tmp_path):
    core = tmp_path / "core"
    live = core / "mediaguard.db"
    with closing(db.Database(live)) as kern:
        kern.initialize()
        kern.execute("INSERT INTO system_state VALUES ('proef', '75', 'proef')")
    archive = preupdate.make_set(core / "backups" / preupdate.FOLDER, live, encode_dir=None)
    preupdate.stage(core, archive, actor="proef")
    live.with_name(live.name + preupdate.STAGED_SUFFIX).unlink()
    for _ in range(2):
        with pytest.raises(RuntimeError, match="terugzetten niet gedaan"):
            aio.before_services({}, tmp_path, None, migrate_core=True)
        assert preupdate.pending(core)["set"] == archive.name
    with closing(sqlite3.connect(live)) as conn:
        row = conn.execute("SELECT value_json FROM system_state WHERE key='proef'").fetchone()
        assert row == ("75",)
    assert preupdate.cancel(core)
    assert aio.before_services({}, tmp_path, None, migrate_core=True) == []


def test_wachtende_terugzetkopie_afgeschermd_voor_alle_nevendiensten(tmp_path):
    config = tmp_path / "config"
    kern = config / "core"
    kern.mkdir(parents=True)
    openbaar = config / "encode"
    openbaar.mkdir()
    (openbaar / "proef").write_text("leesbaar")
    env = {"MG_AIO_CONFIG": str(config), "MG_AIO_WORK": str(tmp_path / "werk"),
           "MG_AIO_DATA": str(tmp_path / "data")}
    bestand = kern / ("mediaguard.db" + preupdate.STAGED_SUFFIX)
    script = """
import sys
from pathlib import Path
assert Path(sys.argv[2]).read_text() == 'leesbaar'
try:
    Path(sys.argv[1]).open('rb').close()
except PermissionError:
    sys.exit(0)
sys.exit(17)
"""
    # Alleen gewone proeftekst. Ook een dienst die na het klaarzetten herstart krijgt geen recht.
    bestand.write_text("toegangsproef")
    diensten = [s for s in aio.build_specs(env) if s.restricted_roots]
    assert len(diensten) == 7
    for dienst in diensten:
        dienst.argv = [sys.executable, "-I", "-c", script, str(bestand), str(openbaar / "proef")]
        assert subprocess.run(dienst.start_command(), check=False).returncode == 0, dienst.name


@pytest.mark.parametrize("dienst", ["encode", "hookguard"])
def test_actieve_dienst_leest_late_wal_en_weigert_wachtende_kopie(tmp_path, dienst):
    config = tmp_path / "config"
    core = config / "core"
    pad = core / "mediaguard.db"
    with closing(db.Database(pad)) as kern:
        kern.initialize()
    env = {"MG_AIO_CONFIG": str(config)}
    spec = next(s for s in aio.build_specs(env) if s.name == dienst)
    script = """
import sqlite3, sys
from pathlib import Path
pad = Path(sys.argv[1])
print('wacht', flush=True)
input()
with sqlite3.connect(pad.as_uri() + '?mode=ro', uri=True) as con:
    row = con.execute("SELECT value_json FROM system_state WHERE key='proef'").fetchone()
    assert row == ('75',)
if sys.argv[2] == 'hookguard':
    assert (pad.parent / 'hookguard/noodstop.json').read_text() == 'proef'
try:
    pad.with_suffix('.db.terugzetten').open('rb').close()
except PermissionError:
    sys.exit(0)
sys.exit(17)
"""
    spec.argv = [sys.executable, "-I", "-c", script, str(pad), dienst]
    with aio.live_core_reader(core), subprocess.Popen(
        spec.start_command(), stdin=subprocess.PIPE, stdout=subprocess.PIPE, text=True,
    ) as proc:
        try:
            assert proc.stdout.readline().strip() == "wacht"
            with closing(sqlite3.connect(pad)) as writer, writer:
                writer.execute("INSERT INTO system_state VALUES ('proef','75','proef')")
            vlag = core / "hookguard/nieuw"
            vlag.write_text("proef")
            vlag.replace(vlag.with_name("noodstop.json"))
            pad.with_suffix(".db.terugzetten").write_text("toegangsproef")
        finally:
            proc.communicate("\n", timeout=10)
        assert proc.returncode == 0


@pytest.mark.parametrize("gebrek", ["ontbreekt", "vreemde-link"])
def test_ongeldige_bestaande_sleutel_blokkeert_de_diensten(tmp_path, gebrek):
    core = tmp_path / "core"
    with closing(db.Database(core / "mediaguard.db")) as kern:
        kern.initialize()
    bestand = event_keys.key_path(core)
    if gebrek == "ontbreekt":
        bestand.unlink()
    else:
        os.link(bestand, tmp_path / "vreemd")
    env = {}
    with pytest.raises(RuntimeError, match="diensten en migratie zijn niet gestart"):
        aio.before_services(env, tmp_path, None, migrate_core=True)
    assert "MG_PRE_UPDATE_SET" not in env
    assert not list(core.glob("backups/voor-update/*"))


def test_onderbroken_commit_over_beide_databases_bewaart_de_terugvalbasis(
    tmp_path, monkeypatch, kern_155, encoder_update,
):
    live, core = kern_155, kern_155.parent
    enc, scripts, env = encoder_update
    with closing(sqlite3.connect(enc)) as motor, motor:
        motor.execute("UPDATE job SET percent=75")
    reserveer = preupdate.reserve_encoders

    @contextmanager
    def onderbreek_commit(folder):
        with reserveer(folder) as verbindingen:
            yield verbindingen
            raise InterruptedError("onderbreking na de kerncommit")

    with monkeypatch.context() as storing:
        storing.setattr(preupdate, "reserve_encoders", onderbreek_commit)
        with pytest.raises(RuntimeError, match="onderbreking na de kerncommit"):
            aio.before_services(env, tmp_path, None, migrate_core=True)
    archief = core / "backups" / preupdate.FOLDER / (core / preupdate.COMMIT_MARKER).read_text()
    assert not preupdate.verify(archief)
    with pytest.raises(RuntimeError, match="update deels vastgelegd"):
        aio.before_services(env, tmp_path, None, migrate_core=True)
    assert "MG_PRE_UPDATE_SET" not in env
    preupdate.stage(core, archief, actor="proef")
    assert preupdate.apply_staged(core, av1_migrations=scripts)["ok"]
    aio.before_services(env, tmp_path, None, migrate_core=True)
    with closing(sqlite3.connect(live)) as kern, closing(sqlite3.connect(enc)) as motor:
        assert kern.execute("SELECT topic FROM outbox_events").fetchall() == [("library.synced",)]
        assert motor.execute("SELECT percent FROM job").fetchall() == [(75,)]
        assert motor.execute("SELECT max(versie) FROM schema_versie").fetchone() == (2,)
    assert not (core / preupdate.COMMIT_MARKER).exists()


def test_sleutelverwijzing_uit_vorige_versie_blijft_bruikbaar(tmp_path):
    core = tmp_path / "core"
    core.mkdir()
    pad = core / "mediaguard.db"
    with closing(sqlite3.connect(pad)) as con, con:
        con.execute("CREATE TABLE outbox_retention (singleton INTEGER, cursor_key BLOB)")
        con.execute("INSERT INTO outbox_retention VALUES (1, ?)", (os.urandom(32),))
    archief = preupdate.make_set(core / "backups" / preupdate.FOLDER, pad, encode_dir=None)
    (archief / preupdate.LEGACY_KEY_META).rename(archief / "meta.json")
    assert not preupdate.verify(archief)
    aio.before_services({}, tmp_path, None)
    assert not (archief / "meta.json").exists()
    assert not preupdate.verify(archief)
    assert preupdate.stage(core, archief, actor="proef")["set"] == archief.name


def test_verruimde_archiefrechten_geven_geen_toestemming(tmp_path, monkeypatch, encoder_update):
    with closing(db.Database(tmp_path / "core/mediaguard.db")) as kern:
        kern.initialize()
    enc, _, env = encoder_update
    match = preupdate.matches_sources
    gezien = []

    def controle(archief, *args):
        (archief / "mediaguard.db").chmod(0o644)
        gezien.append(archief)
        return match(archief, *args)

    monkeypatch.setattr(preupdate, "matches_sources", controle)
    with pytest.raises(RuntimeError, match="toegangsrechten"):
        aio.before_services(env, tmp_path, None, migrate_core=True)
    assert gezien and "MG_PRE_UPDATE_SET" not in env
    with closing(sqlite3.connect(enc)) as motor:
        assert motor.execute("SELECT max(versie) FROM schema_versie").fetchone() == (1,)


def test_reeds_via_oud_beheer_teruggezette_verwijzing(tmp_path, kern_155):
    live, core = kern_155, kern_155.parent
    with closing(sqlite3.connect(live)) as kern, kern:
        origineel = kern.execute("SELECT cursor_key FROM outbox_retention").fetchone()[0]
        ref = event_keys.store_legacy(core, origineel)
        kern.execute("PRAGMA secure_delete=ON")
        kern.execute("UPDATE outbox_retention SET cursor_key=?", (ref,))
    env = {}
    aio.before_services(env, tmp_path, None, migrate_core=True)
    with closing(db.Database(live)) as kern:
        assert kern.fetch_one("SELECT cursor_key FROM outbox_retention")[0] == event_keys.KEY_REF
        assert kern.fetch_one("SELECT count(*) FROM outbox_events")[0] == 1
    archief = Path(env["MG_PRE_UPDATE_SET"])
    assert not (archief / "meta.json").exists() and not preupdate.verify(archief)
    preupdate.stage(core, archief, actor="proef")
    assert preupdate.apply_staged(core)["ok"]
    with closing(sqlite3.connect(live)) as kern:
        if kern.execute("SELECT cursor_key FROM outbox_retention").fetchone()[0] != origineel:
            pytest.fail("De historische leespositiesleutel is veranderd", pytrace=False)
