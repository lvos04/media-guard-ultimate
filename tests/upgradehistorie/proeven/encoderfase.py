"""Herhaal de laatste encodertransactie wanneer uitsluitend de encoder moet migreren."""

import os
import sqlite3
import sys
from contextlib import closing
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

from mediaguard import aio, db
from mediaguard.maintenance import preupdate

bron = Path(os.environ["PROEFBRON"])
assert Path(db.__file__).resolve() == bron / "apps/core/mediaguard/db.py"
sys.path.insert(0, str(bron / "services/encoder"))
from app.db import Database as Encoder

werk = Path(os.environ["WERK"])
config = werk / "installatie"
core = config / "core"
enc = config / "encode"
pad = enc / "app.db"
with closing(db.Database(core / "mediaguard.db")) as kern:
    kern.initialize()
    kern.execute(
        "INSERT INTO outbox_events(ts,topic,payload) VALUES(?,?,?)",
        (db.utcnow(), "library.synced", "{}"),
    )
huidig = bron / "services/encoder/app/migrations"
vorig = werk / "migraties-vorige-encoder"
vorig.mkdir()
scripts = sorted(huidig.glob("*.sql"))
for script in scripts[:-1]:
    (vorig / script.name).symlink_to(script)
instellingen = SimpleNamespace(
    config_root=enc, database_path=pad, backup_root=werk / "kopie"
)
Encoder(instellingen, migrations_dir=vorig).initialize()
assert preupdate.inspect_schema(config, encoder_migrations=huidig) == (None, True)
with closing(sqlite3.connect(pad)) as con, con:
    profiel = con.execute(
        "INSERT INTO profiel(naam,inst) VALUES ('proef','{}')"
    ).lastrowid
    lib = con.execute(
        "INSERT INTO library(naam,pad,profiel_id,actief) VALUES ('proef','/fixture',?,0)",
        (profiel,),
    ).lastrowid
    bestand = con.execute(
        "INSERT INTO bestand(library_id,pad,grootte,mtime,categorie,gescand_op) "
        "VALUES (?, 'proef', 1, 0, 'video', CURRENT_TIMESTAMP)",
        (lib,),
    ).lastrowid
    besluit = con.execute(
        "INSERT INTO besluit(bestand_id,actie,reden,payload,gemaakt_op) "
        "VALUES (?,'hercoderen','proef','{}',CURRENT_TIMESTAMP)",
        (bestand,),
    ).lastrowid
    taak = con.execute(
        "INSERT INTO job(besluit_id,bestand_id,baan,status,percent) "
        "VALUES (?,?,'cpu','bezig',25)",
        (besluit, bestand),
    ).lastrowid


def meting(bestand):
    with closing(sqlite3.connect(bestand.as_uri() + "?mode=ro", uri=True)) as con:
        return (
            int(
                con.execute("SELECT percent FROM job WHERE id=?", (taak,)).fetchone()[0]
            ),
            con.execute(
                "SELECT count(*) FROM auditlog WHERE onderwerp='proefvoortgang'"
            ).fetchone()[0],
        )


maak = preupdate._make_set
publicaties = []


def na_publicatie(*args, **kwargs):
    setmap = maak(*args, **kwargs)
    publicaties.append(setmap)
    if len(publicaties) == 1:
        with closing(sqlite3.connect(pad, timeout=0)) as ander, ander:
            ander.execute("UPDATE job SET percent=75 WHERE id=?", (taak,))
            ander.execute(
                "INSERT INTO auditlog(soort,onderwerp,oud,nieuw,tijd) "
                "VALUES('voortgang','proefvoortgang','25','75',CURRENT_TIMESTAMP)"
            )
    return setmap


assert meting(pad) == (25, 0)
env = {}
with patch.object(preupdate, "_make_set", na_publicatie):
    melding = aio.before_services(env, config, None, migrate_core=True)
setmap = Path(env["MG_PRE_UPDATE_SET"])
assert any(setmap.name in s for s in melding) and not preupdate.verify(setmap)
assert meting(pad) == (75, 1)
Encoder(instellingen).initialize()
assert preupdate.inspect_schema(config, encoder_migrations=huidig) == (None, False)
assert meting(pad) == (75, 1)
preupdate.stage(core, setmap, actor="lokale proef")
resultaat = preupdate.apply_staged(core)
assert resultaat["ok"] and meting(Path(resultaat["previous"]) / "encode__app.db") == (
    75,
    1,
)
percentage, audit = meting(pad)
print(
    f"Alleen encodermigratie: vóór en na migratie 75% en 1 auditrij; "
    f"na terugzetten van de gemelde set {percentage}% en {audit} auditrij; opzijgezette database 75% en 1 auditrij."
)
raise SystemExit(int((percentage, audit) != (75, 1)))
