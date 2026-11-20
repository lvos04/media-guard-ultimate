"""Reviewerproeven op de startgrens, de SQLite-commit en de handmatige herstelkeuze."""

import json
import sqlite3
import subprocess
import sys
from contextlib import closing
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch
from urllib.parse import unquote, urlsplit

import pytest
import test_herstartpunten as punten

from mediaguard import aio
from mediaguard.connector import backups
from mediaguard.maintenance import preupdate


@pytest.fixture(name="installatie")
def gevulde_installatie(tmp_path):
    return punten.installatie.__wrapped__(tmp_path)


def schrijf_en_stop(path, sql):
    """Alleen het eigen proces stopt, met zijn schrijfverbinding nog open."""
    code = """
import os, sqlite3, sys
db = sqlite3.connect(sys.argv[1])
assert db.execute('PRAGMA journal_mode=WAL').fetchone() == ('wal',)
assert db.execute('PRAGMA wal_checkpoint(TRUNCATE)').fetchone() == (0, 0, 0)
db.execute('PRAGMA wal_autocheckpoint=0')
""" + sql + "\nos._exit(0)"
    subprocess.run([sys.executable, "-B", "-c", code, str(path)], check=True, timeout=30)


@pytest.mark.parametrize("andere_commit", [False, True])
def test_checkpoint_tijdens_de_meting_vergt_dezelfde_commit(tmp_path, monkeypatch, andere_commit):
    core = tmp_path / "core"
    core.mkdir()
    live = core / "mediaguard.db"
    schrijf_en_stop(live, """
db.execute('CREATE TABLE taak(voortgang INTEGER)')
db.execute('INSERT INTO taak VALUES (75)')
db.commit()
db.execute('PRAGMA wal_checkpoint(TRUNCATE)')
db.execute('UPDATE taak SET voortgang=90')
db.commit()
""")
    lees = preupdate._logical_state
    standen = []

    def checkpoint(conn):
        stand = lees(conn)
        standen.append(stand)
        if len(standen) == 1:
            with closing(sqlite3.connect(live)) as writer:
                assert writer.execute("PRAGMA wal_checkpoint(TRUNCATE)").fetchone() == (0, 0, 0)
                if andere_commit:
                    writer.execute("UPDATE taak SET voortgang=75")
                    writer.commit()
        return stand

    monkeypatch.setattr(preupdate, "_logical_state", checkpoint)
    if andere_commit:
        with pytest.raises(preupdate.SetError, match="veranderde tijdens het meten"):
            preupdate._source_state(core, "core/mediaguard.db")
        assert len(standen) == 2 and standen[0] != standen[1]
    else:
        bewijs = preupdate._source_state(core, "core/mediaguard.db")
        assert standen == [bewijs["stand"], bewijs["stand"]]


def test_lege_installatie_met_fasebestand_blijft_onder_de_startcontrole(tmp_path, monkeypatch):
    core = tmp_path / "core"
    core.mkdir()
    monkeypatch.setattr(aio.os, "geteuid", lambda: 0)
    # Start het echte controleprogramma onder de huidige proefgebruiker.
    monkeypatch.setattr(aio, "_demote", lambda *_: None)
    assert aio._preupdate("inspect", tmp_path, tmp_path, (0, 0)) is None
    marker = core / preupdate.COMMIT_ORIGIN
    marker.write_text("{}")
    with pytest.raises(RuntimeError, match="beschadigd bewijs"):
        aio._preupdate("inspect", tmp_path, tmp_path, (0, 0))
    assert marker.read_text() == "{}" and not list(core.glob("*.db*"))


@pytest.mark.parametrize("punt", ["klaar-mediaguard.db.terugzetten", "aanvraag"])
def test_walverlies_na_klaarzetten_bewaart_de_aanvraag_en_de_standen(tmp_path, installatie, punt):
    core, enc = tmp_path / "core", tmp_path / "encode/app.db"
    info, _, _ = punten.herstelvraag(tmp_path, installatie)
    assert preupdate.cancel(core)
    (tmp_path / "setnaam").write_text(info["set"])
    schrijf_en_stop(enc, "db.execute('UPDATE job SET percent=92')\ndb.commit()")
    punten.onderbreek(tmp_path, "klaarzetten", punt)
    phase = preupdate._read_proof(core / preupdate.COMMIT_ORIGIN)
    assert phase["fase"] == "klaarzetten" and phase["set"] == info["set"]
    wal = enc.with_name(enc.name + "-wal")
    wal.rename(tmp_path / "opgeslagen-wal")
    voor = punten.bestandsinhoud(tmp_path)
    for _ in range(2):
        with pytest.raises(RuntimeError):
            punten.herstart(tmp_path)
        assert punten.bestandsinhoud(tmp_path) == voor


@pytest.mark.parametrize("punt", ["lege-tijdelijke-naam", "setnaam"])
def test_schemavoorcontrole_op_backupkopie(tmp_path, installatie, monkeypatch, punt):
    core, enc = tmp_path / "core", tmp_path / "encode/app.db"
    schrijf_en_stop(enc, "db.execute('UPDATE job SET percent=90')\ndb.commit()")
    punten.onderbreek(tmp_path, "update", punt)
    shm = enc.with_name("app.db-shm")
    shm.unlink(missing_ok=True)
    voor = punten.bestandsinhoud(tmp_path)
    open_sqlite = sqlite3.connect
    geopend = []

    def observeer(pad, *args, **kwargs):
        opened = Path(unquote(urlsplit(str(pad)).path))
        assert opened.parent.parent == core and opened.parent.name.startswith(".controle-sqlite-")
        geopend.append(opened.name)
        return open_sqlite(pad, *args, **kwargs)

    monkeypatch.setattr(sqlite3, "connect", observeer)
    with patch.object(aio, "prepare", return_value=[]):
        assert aio.prepare_start(installatie, tmp_path, None) == []
    assert "stand.db" in geopend and "controle.db" in geopend
    assert punten.bestandsinhoud(tmp_path) == voor and not shm.exists()


@pytest.mark.parametrize("herstel", ["wal", "set"])
def test_oorsprong_voor_publicatie_blijft_bindend(tmp_path, installatie, herstel):
    core, enc = tmp_path / "core", tmp_path / "encode/app.db"
    schrijf_en_stop(enc, "db.execute('UPDATE job SET percent=90')\ndb.commit()")
    punten.onderbreek(tmp_path, "update", "lege-tijdelijke-naam")
    state = json.loads((core / preupdate.COMMIT_ORIGIN).read_bytes())
    assert state["fase"] == "oorsprong" and len(state["databases"]) == 2
    kern = next(row for row in state["databases"] if row["bestand"] == "core/mediaguard.db")
    assert kern["stand"]["tabellen"]["outbox_events"] == {"rijen": 2, "hoogste": 2}
    assert kern["integriteit"] == "ok" and state["poging"]
    assert not (core / preupdate.COMMIT_MARKER).exists()
    wal = enc.with_name("app.db-wal")
    bewaard = tmp_path / "bewaarde-wal"
    wal.rename(bewaard)
    enc.with_name("app.db-shm").unlink(missing_ok=True)
    before = punten.bestandsinhoud(tmp_path)
    for _ in range(2):
        with pytest.raises(RuntimeError):
            punten.herstart(tmp_path)
        with closing(punten.db.Database(core / "mediaguard.db")) as standalone, \
                pytest.raises(RuntimeError, match="onderbroken update|deels vastgelegd"):
            standalone.initialize()
        assert punten.bestandsinhoud(tmp_path) == before
    if herstel == "wal":
        bewaard.rename(wal)
    else:
        archive = core / "backups" / preupdate.FOLDER / state["set"]
        preupdate.stage(core, archive, actor="beheerder")
    for _ in range(2):
        punten.herstart(tmp_path)
        assert punten.stand(core / "mediaguard.db", enc)[3] == [(90,)]


@pytest.mark.parametrize("schrijver", ["oud", "nieuw"])
@pytest.mark.parametrize("volledig", [False, True])
@pytest.mark.parametrize("storing", ["intact", "zonder-wal", "zonder-slotcommit"])
def test_vorige_commit_en_beheerkeuze(tmp_path, installatie, capsys, schrijver, volledig, storing):
    core, enc = tmp_path / "core", tmp_path / "encode/app.db"
    live = core / "mediaguard.db"
    request, _, _ = punten.herstelvraag(tmp_path, installatie)
    # Eerst twee events in het hoofdbestand, daarna twee afzonderlijke WAL-commits.
    with closing(sqlite3.connect(live)) as conn, conn:
        conn.execute("DELETE FROM outbox_events WHERE id=(SELECT max(id) FROM outbox_events)")
    schrijf_en_stop(live, """
for name in ('proef.eerste', 'proef.laatste'):
    db.execute("INSERT INTO outbox_events(ts,topic,payload) VALUES ('proef', ?, '{}')", (name,))
    db.commit()
    if name == 'proef.eerste':
        with open(sys.argv[1] + '.grens', 'w') as out:
            out.write(str(os.stat(sys.argv[1] + '-wal').st_size))
""")
    if schrijver == "oud":
        files = request["files"] if volledig else request["files"][:1]
        aside = punten.oude_omwisseling(tmp_path, {"files": files})
    else:
        punten.onderbreek(tmp_path, "omwisselen", "actief-" + (enc if volledig else live).name)
        aside, = (core / "backups" / preupdate.ASIDE_FOLDER).iterdir()
    wal = aside / "core__mediaguard.db-wal"
    if storing == "zonder-wal":
        wal.rename(tmp_path / "bewaarde-wal")
    elif storing == "zonder-slotcommit":
        with wal.open("r+b") as stream:
            stream.truncate(int((core / "mediaguard.db.grens").read_text()))
    before = punten.bestandsinhoud(tmp_path)
    wacht = schrijver == "oud" or storing != "intact"
    if wacht:
        for _ in range(2):
            with pytest.raises(RuntimeError):
                punten.herstart(tmp_path)
            assert punten.bestandsinhoud(tmp_path) == before
            assert preupdate.pending(core) == request and preupdate.take_result(core) is None
    if schrijver == "oud":
        ctx = SimpleNamespace(settings=SimpleNamespace(data_dir=core))
        policy = backups.rollback_policy(ctx)
        assert policy.pre_update_restore_mode == "wacht op handmatige keuze"
        if storing == "intact":
            assert policy.staged is True
        args = ["zet-klaar", "--kernmap", str(core), "--set", request["set"]]
        assert preupdate.main(args) == 1
        preview = capsys.readouterr().out
        assert "wacht op handmatige keuze" in preview and "gekozen" in preview
        assert "vorige core/mediaguard.db" in preview and "voortgang [90" in preview
        assert preupdate.main([*args, "--bevestig-stand", "verouderde-keuze"]) == 1
        assert punten.bestandsinhoud(tmp_path) == before
        bevestig = [*args, "--bevestig-stand", preview.split()[-1]]
        if not volledig and storing == "intact":
            with closing(sqlite3.connect(enc)) as db, db:
                db.execute("UPDATE job SET percent=91")
            veranderd = punten.bestandsinhoud(tmp_path)
            assert preupdate.main(bevestig) == 1
            assert "voortgang [91" in capsys.readouterr().out
            assert punten.bestandsinhoud(tmp_path) == veranderd
            with closing(sqlite3.connect(enc)) as db, db:
                db.execute("UPDATE job SET percent=90")
            (aside / "core__mediaguard.db-shm").unlink()
        assert preupdate.main(bevestig) == 0
    if schrijver == "oud" or not wacht:
        for _ in range(2):
            punten.herstart(tmp_path)
        assert punten.stand(live, enc)[3] == [(75,)]
        actual = punten.stand(aside / "core__mediaguard.db", aside / "encode__app.db")
        assert len(actual[2]) == {"intact": 4, "zonder-wal": 2, "zonder-slotcommit": 3}[storing]
        assert actual[3] == [(90,)] and preupdate.take_result(core)["ok"]


@pytest.mark.parametrize("via", ["supervisor", "omwisseling"])
def test_open_schrijftransactie_behoudt_laatste_commit(tmp_path, installatie, via):
    core, enc = tmp_path / "core", tmp_path / "encode/app.db"
    punten.herstelvraag(tmp_path, installatie)
    schrijf_en_stop(enc, """
db.execute('PRAGMA cache_size=5')
db.execute('PRAGMA cache_spill=ON')
db.execute('CREATE TABLE proefblokken(id INTEGER PRIMARY KEY, inhoud BLOB)')
db.executemany('INSERT INTO proefblokken VALUES (?, zeroblob(3000))', [(i,) for i in range(120)])
db.commit()
db.execute('PRAGMA wal_checkpoint(TRUNCATE)')
db.execute('UPDATE job SET percent=92')
db.commit()
with open(sys.argv[1] + '.grens', 'w') as out:
    out.write(str(os.stat(sys.argv[1] + '-wal').st_size))
db.execute('BEGIN IMMEDIATE')
db.execute('UPDATE job SET percent=95')
db.execute('UPDATE proefblokken SET inhoud=randomblob(3000)')
""")
    wal = enc.with_name("app.db-wal").read_bytes()
    tail = wal[int(enc.with_suffix(".db.grens").read_text()):]
    size = 24 + int.from_bytes(wal[8:12])
    assert len(tail) >= size and len(tail) % size == 0
    assert all(tail[pos + 4:pos + 8] == bytes(4) for pos in range(0, len(tail), size))
    if via == "omwisseling":
        assert preupdate.apply_staged(core)["ok"]
        assert preupdate.apply_staged(core) is None
    for _ in range(2):
        punten.herstart(tmp_path)
    aside = Path(preupdate.take_result(core)["previous"])
    assert punten.stand(core / "mediaguard.db", enc)[3] == [(75,)]
    assert punten.stand(aside / "core__mediaguard.db", aside / "encode__app.db")[3] == [(92,)]
    with closing(sqlite3.connect(aside / "encode__app.db")) as old:
        count = old.execute("SELECT count(*) FROM proefblokken WHERE inhoud=zeroblob(3000)")
        assert count.fetchone() == (120,)


@pytest.mark.parametrize("punt", ["voor-archief", "archief-gepubliceerd"])
def test_fase_bewaakt_ook_de_aanmaak_van_de_set(tmp_path, installatie, punt):
    core, enc = tmp_path / "core", tmp_path / "encode/app.db"
    schrijf_en_stop(enc, "db.execute('UPDATE job SET percent=90')\ndb.commit()")
    punten.onderbreek(tmp_path, "update", punt)
    state = json.loads((core / preupdate.COMMIT_ORIGIN).read_bytes())
    assert state["fase"] == "archiveren"
    archive = core / "backups" / preupdate.FOLDER / state["set"]
    assert archive.exists() == (punt == "archief-gepubliceerd")
    wal = enc.with_name("app.db-wal")
    wal.rename(tmp_path / "wal")
    before = punten.bestandsinhoud(tmp_path)
    for _ in range(2):
        with pytest.raises(RuntimeError):
            punten.herstart(tmp_path)
        assert punten.bestandsinhoud(tmp_path) == before
    (tmp_path / "wal").rename(wal)
    for _ in range(2):
        punten.herstart(tmp_path)
        assert punten.stand(core / "mediaguard.db", enc)[3] == [(90,)]


def test_hub_toont_een_historische_aanvraag_ook_binnen_de_cachetermijn(client, ctx):
    from test_connector_lezen import key

    headers = key(client)
    route = "/connector/v1/items?type=backups"
    eerste, = client.get(route, headers=headers).json()["items"]
    assert eerste["rollback"]["staged"] is False
    core = ctx.settings.data_dir
    archive = preupdate.make_set(core / "backups" / preupdate.FOLDER, ctx.db.path, encode_dir=None)
    request = preupdate.stage(core, archive, actor="lokale beheerder")
    # Een oudere schrijver laat een aanvraag en een bewaarmap achter. Hier ligt de
    # gekozen kopie op de verkeerde plaats: de Hub kan de wissel niet verifiëren.
    aside = core / "backups" / preupdate.ASIDE_FOLDER / "20990101T000000Z"
    aside.mkdir(parents=True)
    entry, = request["files"]
    staged = core.parent / entry["staged"]
    staged.rename(aside / entry["live"].replace("/", "__"))
    tweede, = client.get(route, headers=headers).json()["items"]
    assert tweede["status"] == "degraded"
    assert tweede["rollback"] == {"owner": "mediaguard", "staged": None,
                                  "pre_update_restore_mode": "wacht op handmatige keuze"}
    assert preupdate.pending(core) == request
