"""Twee echte supervisorstarts na een onderbreking op gevulde testdatabases."""

import hashlib
import json
import os
import shutil
import sqlite3
import subprocess
import sys
from contextlib import closing
from datetime import UTC, datetime
from pathlib import Path
from unittest.mock import patch

import pytest

from mediaguard import aio, db, schema
from mediaguard.maintenance import preupdate


@pytest.fixture
def installatie(tmp_path):
    core = tmp_path / "core"
    core.mkdir()
    fixture = Path(__file__).resolve().parents[3] / "tests/fixtures/aio/core-1.2.2.sql"
    live = core / "mediaguard.db"
    with closing(sqlite3.connect(live)) as conn:
        conn.executescript(fixture.read_text())
    with closing(db.Database(live)) as kern:
        kern.initialize()
    enc = tmp_path / "encode/app.db"
    enc.parent.mkdir()
    with closing(sqlite3.connect(enc)) as conn:
        conn.executescript("""
            CREATE TABLE schema_versie (versie INTEGER PRIMARY KEY, naam TEXT, toegepast_op TEXT);
            INSERT INTO schema_versie VALUES (1, '001_basis.sql', 'proef');
            CREATE TABLE job (percent INTEGER);
            INSERT INTO job VALUES (75);
        """)
    migraties = tmp_path / "motor/app/migrations"
    migraties.mkdir(parents=True)
    (migraties / "001_basis.sql").write_text("")
    (migraties / "002_vervolg.sql").write_text("CREATE TABLE vervolg (id INTEGER);")
    return {"MG_AIO_ENCODER_DIR": str(migraties.parents[1])}


def stand(kern, encoder):
    with closing(sqlite3.connect(kern.as_uri() + "?mode=ro", uri=True)) as k, closing(
        sqlite3.connect(encoder.as_uri() + "?mode=ro", uri=True)
    ) as e:
        assert k.execute("PRAGMA integrity_check").fetchall() == [("ok",)]
        assert e.execute("PRAGMA integrity_check").fetchall() == [("ok",)]
        return (
            k.execute("SELECT id,role FROM users ORDER BY id").fetchall(),
            k.execute("SELECT id,status,progress FROM jobs ORDER BY id").fetchall(),
            k.execute("SELECT id,topic FROM outbox_events ORDER BY id").fetchall(),
            e.execute("SELECT percent FROM job").fetchall(),
        )


def start(config, env):
    aio.before_services(env, config, None, migrate_core=True)


@pytest.mark.parametrize("inhoud", [b"", b"voor-update-202", b"\xff"])
def test_oude_onvolledige_updatemarkering(tmp_path, installatie, inhoud):
    core = tmp_path / "core"
    live, enc = core / "mediaguard.db", tmp_path / "encode/app.db"
    voor = stand(live, enc)
    assert all(voor) and len(voor[2]) == 2 and voor[3] == [(75,)]
    aio.before_services(installatie, tmp_path, None)
    (core / preupdate.COMMIT_MARKER).write_bytes(inhoud)
    for _ in range(2):
        herstart(tmp_path)
        assert stand(live, enc) == voor
    assert not (core / preupdate.COMMIT_MARKER).exists()


def herstelvraag(config, env):
    core = config / "core"
    live, enc = core / "mediaguard.db", config / "encode/app.db"
    voor = stand(live, enc)
    start(config, env)
    archive = Path(env["MG_PRE_UPDATE_SET"])
    with closing(sqlite3.connect(enc)) as e, e, closing(sqlite3.connect(live)) as k, k:
        e.execute("UPDATE job SET percent=90")
        k.execute("INSERT INTO outbox_events(ts,topic,payload) VALUES(?,?,?)",
                  (db.utcnow(), "library.synced", "{}"))
    later = stand(live, enc)
    assert len(voor[2]) == 2 and len(later[2]) == 3
    assert voor[3] == [(75,)] and later[3] == [(90,)]
    info = preupdate.stage(core, archive, actor="proef")
    return info, voor, later


def oude_omwisseling(config, info):
    """De vorige schrijver stopte na de laatste rename, met de aanvraag nog op schijf."""
    core = config / "core"
    aside = core / "backups" / preupdate.ASIDE_FOLDER / datetime.now(UTC).strftime("%Y%m%dT%H%M%SZ")
    aside.mkdir(parents=True)
    for entry in info["files"]:
        live = config / entry["live"]
        for suffix in ("", "-wal", "-shm", "-journal"):
            src = live.with_name(live.name + suffix)
            if src.exists():
                os.replace(src, aside / (entry["live"].replace("/", "__") + suffix))
        os.replace(config / entry["staged"], live)
    return aside


@pytest.mark.parametrize("verlopen", [False, True])
def test_oude_voltooide_omwisseling(tmp_path, installatie, verlopen):
    core = tmp_path / "core"
    info, voor, later = herstelvraag(tmp_path, installatie)
    if verlopen:
        info["requested_at"] = "2024-01-01T00:00:00+00:00"
        (core / preupdate.STAGE_MARKER).write_text(json.dumps(info))
    aside = oude_omwisseling(tmp_path, info)
    bestanden = bestandsinhoud(tmp_path)
    for _ in range(2):
        with pytest.raises(RuntimeError, match="wacht op handmatige keuze"):
            herstart(tmp_path)
        assert bestandsinhoud(tmp_path) == bestanden
        assert preupdate.pending(core) == info and preupdate.take_result(core) is None
    assert stand(core / "mediaguard.db", tmp_path / "encode/app.db") == voor
    assert stand(aside / "core__mediaguard.db", aside / "encode__app.db") == later


def kind(config, actie, punt):
    """Stop alleen dit proefproces, direct voor/na de echte aanroep op schijf."""
    core = config / "core"
    env = {"MG_AIO_ENCODER_DIR": str(config / "motor")}
    publiceer, hernoem, verwijder = preupdate._publish, os.replace, Path.unlink
    tijdelijk = preupdate.tempfile.NamedTemporaryFile

    def grens(naam):
        if naam == punt:
            os._exit(86)

    def nieuw(*args, **kwargs):
        bestand = tijdelijk(*args, **kwargs)
        if kwargs.get("prefix") == preupdate.COMMIT_MARKER + ".":
            assert Path(bestand.name).stat().st_size == 0
            assert not (core / preupdate.COMMIT_MARKER).exists()
            grens("lege-tijdelijke-naam")
        return bestand

    def schrijf(pad, inhoud, *args):
        if pad.name == preupdate.COMMIT_ORIGIN and json.loads(inhoud).get("fase") == "migratie":
            grens("voor-" + json.loads(inhoud)["stap"])
        return publiceer(pad, inhoud, *args)

    def wissel(src, dst, *args, **kwargs):
        hernoem(src, dst, *args, **kwargs)
        doel = Path(dst)
        if doel == core / preupdate.COMMIT_MARKER:
            assert preupdate.is_set_name(doel.read_text())
            grens("setnaam")
        elif doel == core / preupdate.COMMIT_ORIGIN:
            fase = json.loads(doel.read_text())
            if fase["fase"] == "archiveren":
                grens("voor-archief")
            elif fase["fase"] == "migratie":
                grens("na-" + fase["stap"])
            elif fase["fase"] == "omwisseling":
                grens(f"stap-{fase['stap']}")
        elif doel == core / preupdate.STAGE_MARKER:
            journal = json.loads(doel.read_text()).get("omwisseling")
            grens("aanvraag" if journal is None else "aanvraag-gebonden")
        elif doel == core / preupdate.RESULT_FILE:
            assert json.loads(doel.read_text())["ok"]
            grens("resultaat")
        elif doel.parent.name == preupdate.FOLDER and preupdate.is_set_name(doel.name):
            grens("archief-gepubliceerd")
        elif doel.parent.parent.name == preupdate.ASIDE_FOLDER:
            grens("opzij-" + doel.name)
        elif Path(src).name.endswith(preupdate.STAGED_SUFFIX):
            grens("actief-" + doel.name)
        elif doel.name.endswith(preupdate.STAGED_SUFFIX):
            grens("klaar-" + doel.name)

    def weg(pad, *args, **kwargs):
        verwijder(pad, *args, **kwargs)
        if pad.name in (preupdate.COMMIT_ORIGIN, preupdate.COMMIT_MARKER, preupdate.STAGE_MARKER):
            grens("verwijderd-" + pad.name)

    with patch.object(preupdate, "_publish", schrijf), patch.object(os, "replace", wissel), \
            patch.object(Path, "unlink", weg), \
            patch.object(preupdate.tempfile, "NamedTemporaryFile", nieuw):
        if actie == "klaarzetten":
            archief = core / "backups" / preupdate.FOLDER / (config / "setnaam").read_text()
            preupdate.stage(core, archief, actor="proef")
        else:
            start(config, env)
    raise AssertionError(f"Onderbrekingspunt niet bereikt: {punt}")


def onderbreek(config, actie, punt):
    uit = subprocess.run([sys.executable, "-B", __file__, str(config), actie, punt],
                         capture_output=True, text=True, check=False, timeout=60)
    assert uit.returncode == 86, uit.stderr


def herstart(config):
    uit = subprocess.run([sys.executable, "-B", __file__, str(config), "herstart"],
                         capture_output=True, text=True, check=False, timeout=60)
    if uit.returncode:
        raise RuntimeError(uit.stderr)


def twee_starts(config, verwacht):
    for _ in range(2):
        herstart(config)
        assert stand(config / "core/mediaguard.db", config / "encode/app.db") == verwacht


@pytest.mark.parametrize("punt", [
    "lege-tijdelijke-naam", "setnaam", "voor-voorbereid", "na-voorbereid",
    "voor-kern vastgelegd", "na-kern vastgelegd", "voor-encode/tweede.db", "na-encode/tweede.db",
    "voor-encode/app.db", "na-encode/app.db",
    "verwijderd-" + preupdate.COMMIT_ORIGIN, "verwijderd-" + preupdate.COMMIT_MARKER,
])
def test_uitval_bij_elke_updatecommit(tmp_path, installatie, punt):
    core = tmp_path / "core"
    enc = tmp_path / "encode/app.db"
    tweede = enc.with_name("tweede.db")
    shutil.copyfile(enc, tweede)
    voor = stand(core / "mediaguard.db", enc)
    onderbreek(tmp_path, "update", punt)
    if punt in ("voor-encode/tweede.db", "na-encode/tweede.db"):
        # Eén AV1-commit is zichtbaar; een tweede herstellen vergt dezelfde gekozen set.
        marker = core / preupdate.COMMIT_MARKER
        archief = core / "backups" / preupdate.FOLDER / marker.read_text()
        for _ in range(2):
            with pytest.raises(RuntimeError, match="update deels vastgelegd"):
                herstart(tmp_path)
            assert marker.read_text() == archief.name
            assert stand(core / "mediaguard.db", enc) == voor
            assert stand(core / "mediaguard.db", tweede) == voor
        preupdate.stage(core, archief, actor="proef")
    twee_starts(tmp_path, voor)
    for pad in (enc, tweede):
        with closing(sqlite3.connect(pad)) as conn:
            assert conn.execute("SELECT max(versie), (SELECT percent FROM job) "
                                "FROM schema_versie").fetchone() == (2, 75)
    assert not (core / preupdate.COMMIT_MARKER).exists()
    assert not (core / preupdate.COMMIT_STEP).exists()
    assert not list(core.glob(preupdate.COMMIT_MARKER + ".*.part"))


def late_wal(live):
    # Laat een werkelijk gecommitteerde wijziging in de WAL van het eigen kind staan.
    code = """
import os, sqlite3, sys
c = sqlite3.connect(sys.argv[1])
assert c.execute("PRAGMA journal_mode").fetchone()[0] == "wal"
c.execute("PRAGMA wal_autocheckpoint=0")
c.execute("UPDATE outbox_events SET topic='library.laatste' WHERE id=(SELECT max(id) "
          "FROM outbox_events)")
c.commit()
os._exit(0)
"""
    subprocess.run([sys.executable, "-B", "-c", code, str(live)], check=True)
    assert live.with_name(live.name + "-wal").stat().st_size > 0


@pytest.mark.parametrize("punt", [
    "klaar-mediaguard.db.terugzetten", "klaar-app.db.terugzetten", "aanvraag",
    "stap-0", "opzij-core__mediaguard.db", "stap-1", "opzij-core__mediaguard.db-wal",
    "stap-2", "opzij-core__mediaguard.db-shm", "stap-3", "actief-mediaguard.db", "stap-4",
    "opzij-encode__app.db", "stap-5", "opzij-encode__app.db-journal", "stap-6",
    "actief-app.db", "stap-7", "resultaat", "verwijderd-" + preupdate.STAGE_MARKER,
])
def test_uitval_bij_elke_terugzetstap(tmp_path, installatie, punt):
    core = tmp_path / "core"
    live, enc = core / "mediaguard.db", tmp_path / "encode/app.db"
    info, voor, _ = herstelvraag(tmp_path, installatie)
    klaarzetten = punt.startswith("klaar-") or punt == "aanvraag"
    if klaarzetten:
        assert preupdate.cancel(core)
        (tmp_path / "setnaam").write_text(info["set"])
    late_wal(live)
    later = stand(live, enc)
    assert later[2][-1][1] == "library.laatste"
    enc.with_name(enc.name + "-journal").touch()
    wal = hashlib.sha256(live.with_name(live.name + "-wal").read_bytes()).digest()
    onderbreek(tmp_path, "klaarzetten" if klaarzetten else "omwisselen", punt)
    wacht = punt.startswith("klaar-")
    twee_starts(tmp_path, later if wacht else voor)
    if wacht:
        assert not preupdate.pending(core)
        assert not (core / "backups" / preupdate.ASIDE_FOLDER).exists()
        return
    aside = Path(preupdate.take_result(core)["previous"])
    assert hashlib.sha256((aside / "core__mediaguard.db-wal").read_bytes()).digest() == wal
    assert (aside / "core__mediaguard.db-shm").is_file()
    assert (aside / "encode__app.db-journal").is_file()
    assert stand(aside / "core__mediaguard.db", aside / "encode__app.db") == later
    assert not preupdate.pending(core)


@pytest.mark.parametrize("gebrek", ["latere-wal", "ontbrekende-encoder", "beschadigd-archief"])
def test_onbewezen_oorsprong_blijft_bewaard(tmp_path, installatie, gebrek):
    core = tmp_path / "core"
    live, enc = core / "mediaguard.db", tmp_path / "encode/app.db"
    aio.before_services(installatie, tmp_path, None)
    archief = Path(installatie["MG_PRE_UPDATE_SET"])
    marker = core / preupdate.COMMIT_MARKER
    marker.write_bytes(b"")
    if gebrek == "latere-wal":
        late_wal(live)
    elif gebrek == "ontbrekende-encoder":
        enc.rename(enc.with_suffix(".bewaard"))
    else:
        (archief / "encode/app.db").write_bytes(b"onvolledige proefkopie")
    for _ in range(2):
        with pytest.raises(RuntimeError, match="onbewezen"):
            start(tmp_path, installatie)
        assert marker.read_bytes() == b""
        assert archief.is_dir()
    if gebrek == "latere-wal":
        assert stand(live, enc)[2][-1][1] == "library.laatste"


@pytest.mark.parametrize("gebrek", ["kopie", "actieve-wal", "vorige", "stapnummer"])
def test_beschadigde_tussenstand_weigert_zonder_opruimen(tmp_path, installatie, gebrek):
    core = tmp_path / "core"
    info, _, _ = herstelvraag(tmp_path, installatie)
    onderbreek(tmp_path, "omwisselen", "actief-mediaguard.db")
    marker = core / preupdate.STAGE_MARKER
    aside, = (core / "backups" / preupdate.ASIDE_FOLDER).iterdir()
    if gebrek == "kopie":
        (tmp_path / info["files"][1]["staged"]).write_bytes(b"onvolledige proefkopie")
    elif gebrek == "actieve-wal":
        (core / "mediaguard.db-wal").write_bytes(b"onbekende proef-WAL")
    elif gebrek == "vorige":
        (aside / "core__mediaguard.db").rename(aside / "apart.bewaard")
    else:
        aanvraag = json.loads(marker.read_bytes())
        aanvraag["omwisseling"]["stap"] = 0
        marker.write_text(json.dumps(aanvraag))

    def bestanden():
        return {p.relative_to(tmp_path): hashlib.sha256(p.read_bytes()).digest()
                for p in tmp_path.rglob("*") if p.is_file()}
    voor = bestanden()
    for _ in range(2):
        with pytest.raises(RuntimeError, match="terugzetten niet gedaan"):
            start(tmp_path, installatie)
        with pytest.raises(preupdate.SetError, match="begonnen omwisseling"):
            preupdate.cancel(core)
        assert bestanden() == voor


def bestandsinhoud(config):
    return {p.relative_to(config): hashlib.sha256(p.read_bytes()).digest()
            for p in config.rglob("*") if p.is_file()}


@pytest.mark.parametrize("gebrek", ["wal", "encoder", "oudere-setnaam", "recentere-set"])
def test_onderbroken_update_blijft_gebonden_aan_de_gekozen_set(tmp_path, installatie, gebrek):
    core = tmp_path / "core"
    enc = tmp_path / "encode/app.db"
    marker = core / preupdate.COMMIT_MARKER
    aio.before_services(installatie, tmp_path, None)
    eerste = Path(installatie["MG_PRE_UPDATE_SET"])
    taak = enc.with_name("tweede.db") if gebrek == "encoder" else enc
    if taak != enc:
        shutil.copyfile(enc, taak)
    code = """
import os, sqlite3, sys
db = sqlite3.connect(sys.argv[1])
db.execute("PRAGMA journal_mode=WAL")
db.execute("PRAGMA wal_autocheckpoint=0")
db.execute("UPDATE job SET percent=90")
db.commit()
os._exit(0)
"""
    subprocess.run([sys.executable, "-B", "-c", code, str(taak)], check=True)
    onderbreek(tmp_path, "update", "setnaam")
    gekozen = eerste.parent / marker.read_text()
    assert gekozen != eerste and not preupdate.verify(gekozen)
    kopie = gekozen / "encode" / taak.name
    with closing(sqlite3.connect(kopie.as_uri() + "?immutable=1", uri=True)) as db:
        assert db.execute("SELECT percent FROM job").fetchall() == [(90,)]
    bewijs = json.loads((core / preupdate.COMMIT_ORIGIN).read_bytes())
    assert bewijs["set"] == gekozen.name and bewijs["aantal"] == len(bewijs["databases"])
    origineel, = (r for r in bewijs["databases"] if r["bestand"] == "encode/" + taak.name)
    wal = taak.with_name(taak.name + "-wal").read_bytes()
    assert origineel["kern"] == hashlib.sha256(taak.read_bytes()).hexdigest()
    assert origineel["wal_frames"] > 0 and 32 < origineel["wal_positie"] <= len(wal)
    assert origineel["wal_sha256"] == hashlib.sha256(wal[:origineel["wal_positie"]]).hexdigest()
    if gebrek == "recentere-set":
        nieuw = preupdate.make_set(eerste.parent, core / "mediaguard.db",
                                   encode_dir=enc.parent, reason="update")
        assert nieuw.name != gekozen.name
        assert preupdate.prune_sets(eerste.parent, keep=1) == 0 and gekozen.exists()
    elif gebrek == "oudere-setnaam":
        marker.write_text(eerste.name)
    else:
        marker.write_bytes(b"")
        for suffix in ("-wal", "-shm") if gebrek == "wal" else ("", "-wal", "-shm"):
            taak.with_name(taak.name + suffix).unlink(missing_ok=True)
    voor = bestandsinhoud(tmp_path)
    for _ in range(2):
        with pytest.raises(RuntimeError):
            herstart(tmp_path)
        assert bestandsinhoud(tmp_path) == voor
    preupdate.stage(core, gekozen, actor="proef")
    for _ in range(2):
        herstart(tmp_path)
        assert stand(core / "mediaguard.db", taak)[3] == [(90,)]
    assert not marker.exists()


def test_lezersregister_van_vorige_wal_mag_wijzigen(tmp_path, installatie):
    core = tmp_path / "core"
    _, voor, _ = herstelvraag(tmp_path, installatie)
    late_wal(core / "mediaguard.db")
    onderbreek(tmp_path, "omwisselen", "resultaat")
    aside, = (core / "backups" / preupdate.ASIDE_FOLDER).iterdir()
    kern, enc = aside / "core__mediaguard.db", aside / "encode__app.db"
    bestanden = bestandsinhoud(aside)
    later = stand(kern, enc)
    assert later[2][-1][1] == "library.laatste" and later[3] == [(90,)]
    assert {p for p, h in bestandsinhoud(aside).items() if h != bestanden[p]} == {
        Path("core__mediaguard.db-shm")
    }
    twee_starts(tmp_path, voor)
    assert stand(kern, enc) == later
    assert Path(preupdate.take_result(core)["previous"]) == aside


@pytest.mark.parametrize(("halverwege", "schade"), [
    (True, "kern"), (False, "encoder"), (True, "wal"), (False, "wal"), (True, "laatste-frame"),
])
def test_oude_omwisseling_controleert_alle_vorige_databases(tmp_path, installatie,
                                                            halverwege, schade):
    core = tmp_path / "core"
    info, _, _ = herstelvraag(tmp_path, installatie)
    late_wal(core / "mediaguard.db")
    paren = info["files"][:1] if halverwege else info["files"]
    aside = oude_omwisseling(tmp_path, {"files": paren})
    if schade == "laatste-frame":
        pad = aside / "core__mediaguard.db-wal"
        with pad.open("r+b") as stream:
            header = stream.read(32)
            page_size = int.from_bytes(header[8:12])
            stream.seek(-page_size - 24 + 8, os.SEEK_END)
            stream.write((int.from_bytes(header[16:20]) ^ 1).to_bytes(4))
    elif schade == "wal":
        pad = aside / "core__mediaguard.db-wal"
        # SQLite kan bij een ongeldige WAL terugvallen op een nog leesbare oudere kern.
        with pad.open("r+b") as f:
            f.write(bytes(32))
    else:
        pad = aside / ("core__mediaguard.db" if schade == "kern" else "encode__app.db")
        with pad.open("r+b") as f:
            f.write(bytes(16))
    voor = bestandsinhoud(tmp_path)
    for _ in range(2):
        with pytest.raises(RuntimeError):
            herstart(tmp_path)
        assert bestandsinhoud(tmp_path) == voor
        assert not (core / preupdate.RESULT_FILE).exists()
    resultaat = preupdate.apply_staged(core)
    assert resultaat and not resultaat["ok"] and bestandsinhoud(tmp_path) == voor
    with pytest.raises(preupdate.SetError, match="begonnen omwisseling"):
        preupdate.cancel(core)


def test_oude_voortgang_met_de_doelversies_weigert(tmp_path, installatie):
    core, enc = tmp_path / "core", tmp_path / "encode/app.db"
    with closing(sqlite3.connect(enc)) as conn, conn:
        conn.execute("UPDATE job SET percent=90")
    onderbreek(tmp_path, "update", "na-encode/app.db")
    with closing(sqlite3.connect(enc)) as conn, conn:
        assert conn.execute("SELECT max(versie) FROM schema_versie").fetchone() == (2,)
        assert conn.execute("UPDATE job SET percent=75 WHERE percent=90").rowcount == 1
    voor = bestandsinhoud(tmp_path)
    for _ in range(2):
        with pytest.raises(RuntimeError, match="deels vastgelegd"):
            herstart(tmp_path)
        assert bestandsinhoud(tmp_path) == voor
    archive = core / "backups" / preupdate.FOLDER / (core / preupdate.COMMIT_MARKER).read_text()
    preupdate.stage(core, archive, actor="proef")
    for _ in range(2):
        herstart(tmp_path)
        assert stand(core / "mediaguard.db", enc)[3] == [(90,)]


def test_schade_tussen_twee_wissels_houdt_de_encoder_op_zijn_plek(tmp_path, installatie,
                                                                monkeypatch):
    core = tmp_path / "core"
    herstelvraag(tmp_path, installatie)
    publiceren = preupdate._publish_step
    na_schade = {}

    def na_kern(*args):
        publiceren(*args)
        if (core / ("mediaguard.db" + preupdate.STAGED_SUFFIX)).exists() or na_schade:
            return
        aside, = (core / "backups" / preupdate.ASIDE_FOLDER).iterdir()
        with (aside / "core__mediaguard.db").open("r+b") as f:
            f.write(bytes(16))
        na_schade.update(bestandsinhoud(tmp_path))

    monkeypatch.setattr(preupdate, "_publish_step", na_kern)
    for _ in range(2):
        with pytest.raises(RuntimeError, match="terugzetten niet gedaan"):
            start(tmp_path, installatie)
        assert bestandsinhoud(tmp_path) == na_schade
        assert (tmp_path / "encode/app.db.terugzetten").is_file()
        assert not (core / preupdate.RESULT_FILE).exists()


def test_sqlite_opent_bij_hervatten_uitsluitend_controlekopieen(tmp_path, installatie, monkeypatch):
    core = tmp_path / "core"
    herstelvraag(tmp_path, installatie)
    late_wal(core / "mediaguard.db")
    onderbreek(tmp_path, "omwisselen", "opzij-core__mediaguard.db")
    openen = sqlite3.connect
    geopend = []

    def op_kopie(pad, *args, **kwargs):
        pad = Path(pad)
        assert pad.parent.parent == core and pad.parent.name.startswith(".controle-sqlite-")
        geopend.append(pad)
        return openen(pad, *args, **kwargs)

    with monkeypatch.context() as controle:
        controle.setattr(sqlite3, "connect", op_kopie)
        resultaat = preupdate.apply_staged(core)
    assert resultaat["ok"] and geopend and all(not p.exists() for p in geopend)
    aside = Path(resultaat["previous"])
    vorige = stand(aside / "core__mediaguard.db", aside / "encode__app.db")
    assert vorige[2][-1][1] == "library.laatste"


def test_succesmelding_wacht_op_de_hervatte_omwisseling(tmp_path, installatie):
    core = tmp_path / "core"
    herstelvraag(tmp_path, installatie)
    onderbreek(tmp_path, "omwisselen", "resultaat")
    assert json.loads((core / preupdate.RESULT_FILE).read_bytes())["ok"]
    aside, = (core / "backups" / preupdate.ASIDE_FOLDER).iterdir()
    with (aside / "encode__app.db").open("r+b") as f:
        f.write(bytes(16))
    voor = bestandsinhoud(tmp_path)
    for _ in range(2):
        with pytest.raises(RuntimeError):
            herstart(tmp_path)
        assert preupdate.take_result(core) is None
        assert bestandsinhoud(tmp_path) == voor


@pytest.mark.parametrize("moment", ["na-archief", "tijdens-kopie"])
def test_checkpoint_voor_binding_levert_een_verse_set(tmp_path, installatie, monkeypatch, moment):
    core, enc = tmp_path / "core", tmp_path / "encode/app.db"
    archieven = []
    maken = preupdate._make_set
    with closing(sqlite3.connect(enc)) as schrijver:
        assert schrijver.execute("PRAGMA journal_mode=WAL").fetchone() == ("wal",)
        schrijver.execute("UPDATE job SET percent=90")
        schrijver.commit()

        def met_checkpoint(*args, **kwargs):
            archief = maken(*args, **kwargs)
            if not archieven and moment == "na-archief":
                assert schrijver.execute("PRAGMA wal_checkpoint(TRUNCATE)").fetchone() == (0, 0, 0)
            archieven.append(archief)
            return archief

        kopieer = shutil.copyfile

        def laatste_verbinding(src, dst, *args, **kwargs):
            if archieven and moment == "tijdens-kopie" and src == enc.with_name("app.db-wal"):
                schrijver.close()
            return kopieer(src, dst, *args, **kwargs)

        monkeypatch.setattr(preupdate, "_make_set", met_checkpoint)
        monkeypatch.setattr(shutil, "copyfile", laatste_verbinding)
        aio.before_services(installatie, tmp_path, None)
    gekozen = Path(installatie["MG_PRE_UPDATE_SET"])
    assert len(archieven) == 2 and gekozen == archieven[-1]
    assert gekozen.name == json.loads((core / preupdate.COMMIT_ORIGIN).read_bytes())["set"]
    with closing(sqlite3.connect((gekozen / "encode/app.db").as_uri() + "?immutable=1",
                                 uri=True)) as kopie:
        assert kopie.execute("SELECT percent FROM job").fetchall() == [(90,)]


def test_publicatie_synchroniseert_in_de_juiste_volgorde(tmp_path, monkeypatch):
    marker = tmp_path / preupdate.COMMIT_MARKER
    marker.write_text("vorige")
    uitvoer = []
    fsync, replace = os.fsync, os.replace

    def sync(fd):
        fsync(fd)
        pad = Path(os.readlink(f"/proc/self/fd/{fd}"))
        if pad.is_dir():
            assert pad == tmp_path and marker.read_text() == "volgende"
            uitvoer.append("map")
        else:
            assert pad != marker and pad.read_text() == "volgende"
            assert marker.read_text() == "vorige"
            uitvoer.append("tijdelijk bestand")

    def wissel(src, dst):
        assert dst == marker and marker.read_text() == "vorige"
        replace(src, dst)
        uitvoer.append("hernoeming")

    monkeypatch.setattr(os, "fsync", sync)
    monkeypatch.setattr(os, "replace", wissel)
    preupdate._publish(marker, "volgende")
    assert uitvoer == ["tijdelijk bestand", "hernoeming", "map"]


if __name__ == "__main__":
    config = Path(sys.argv[1])
    if sys.argv[2] == "herstart":
        env = {"MG_AIO_ENCODER_DIR": str(config / "motor")}
        # De schemavoorcontrole en omwisseling draaien ook bij de eerste start in een
        # vers proces. De bootstrap van de container valt onder het image-upgradebewijs.
        with patch.object(aio, "prepare", return_value=[]):
            meldingen = aio.prepare_start(env, config, None)
        assert not env.get("MG_AIO_SCHEMA_CHECK_FAILED"), meldingen
        start(config, env)
        assert not env.get(schema.AIO_RECOVERY_ENV)
    else:
        kind(config, *sys.argv[2:])
