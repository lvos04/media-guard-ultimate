"""Toets de vastgelegde taakstand rond een echt, gedeeltelijk SQLite-checkpoint."""

import hashlib
import json
import os
import sqlite3
import subprocess
import sys
from pathlib import Path

import test_herstartpunten as punten
from mediaguard.maintenance import preupdate
from walgrenzen import lees

werk = Path(os.environ["WERK"])
config = werk / "installatie"
kern = config / "core"
enc = config / "encode/app.db"
wal = enc.with_name("app.db-wal")
fase = kern / preupdate.COMMIT_ORIGIN
marker = kern / preupdate.STAGE_MARKER
assert Path(preupdate.__file__).resolve().is_relative_to(Path(os.environ["REPO"]))


def getuige(main, met_wal=True):
    return lees(main, "SELECT percent FROM job", met_wal)[0][0]


def schrijf(soort):
    env = punten.installatie.__wrapped__(config)
    aanvraag, _, _ = punten.herstelvraag(config, env)
    assert preupdate.cancel(kern)
    archief = kern / "backups" / preupdate.FOLDER / aanvraag["set"]
    db = sqlite3.connect(enc)
    assert db.execute("PRAGMA journal_mode=WAL").fetchone() == ("wal",)
    db.execute("PRAGMA wal_autocheckpoint=0")
    db.execute("CREATE TABLE checkpoint_getuige(n INTEGER)")
    db.execute("INSERT INTO checkpoint_getuige VALUES (0)")
    db.commit()
    assert db.execute("PRAGMA wal_checkpoint(TRUNCATE)").fetchone() == (0, 0, 0)
    # Twee afzonderlijke pagina's. De lezer houdt het checkpoint vóór de taakcommit.
    db.execute("UPDATE checkpoint_getuige SET n=1")
    db.commit()
    lezer = sqlite3.connect(enc)
    lezer.execute("BEGIN")
    assert lezer.execute("SELECT n FROM checkpoint_getuige").fetchone() == (1,)
    db.execute("UPDATE job SET percent=92")
    db.commit()
    assert getuige(enc) == 92 and getuige(enc, False) == 90
    preupdate.stage(kern, archief, actor="lokale proef")
    state = json.loads(fase.read_bytes())
    (entry,) = [
        item for item in state["databases"] if item["bestand"] == "encode/app.db"
    ]
    assert state["fase"] == "klaarzetten" and entry["wal_frames"] >= 2
    assert entry["kern"] == hashlib.sha256(enc.read_bytes()).hexdigest()
    with preupdate.checked_database(kern, preupdate.database_files(enc)) as (
        snapshot,
        _,
    ):
        assert snapshot.execute("SELECT percent FROM job").fetchone() == (92,)
        assert entry["stand"] == preupdate._logical_state(snapshot)
    if soort != "zonder-checkpoint":
        busy, frames, copied = db.execute("PRAGMA wal_checkpoint(PASSIVE)").fetchone()
        assert busy == 0 and 0 < copied < frames
        assert hashlib.sha256(enc.read_bytes()).hexdigest() != entry["kern"]
        assert getuige(enc, False) == 90 and getuige(enc) == 92
    if soort == "nieuwe-wal":
        lezer.close()
        assert db.execute("PRAGMA wal_checkpoint(TRUNCATE)").fetchone() == (0, 0, 0)
        assert getuige(enc, False) == 92
    if soort in ("latere-wal", "nieuwe-wal"):
        db.execute("UPDATE job SET percent=93")
        db.commit()
    os._exit(
        0
    )  # Sluit alleen dit proces; SQLite mag geen afsluitend checkpoint uitvoeren.


def meet(soort):
    config.mkdir()
    subprocess.run(
        [sys.executable, "-B", __file__, "schrijf", soort], check=True, timeout=60
    )
    intact = soort in ("intact", "latere-wal", "nieuwe-wal")
    verwacht = 93 if soort in ("latere-wal", "nieuwe-wal") else 92
    assert getuige(enc) == verwacht
    if not intact:
        wal.rename(werk / "apart-gehouden-wal")
        enc.with_name("app.db-shm").unlink(missing_ok=True)
    begin = getuige(enc)
    voor = punten.bestandsinhoud(config)
    vrij = 0
    for _ in range(2):
        try:
            punten.herstart(config)
        except RuntimeError as err:
            assert "onbewezen" in str(err) or "ontbreekt" in str(err), str(err)
        else:
            vrij += 1
    bewaard = (
        voor == punten.bestandsinhoud(config) and marker.exists() and fase.exists()
    )
    result = preupdate.take_result(kern)
    vorige = (
        None if not result else getuige(Path(result["previous"]) / "encode__app.db")
    )
    print(
        f"{soort}: bewezen 92%, bij herstart {begin}%, {vrij}/2 starts, actief {getuige(enc)}%, "
        f"vorige {str(vorige) + '%' if vorige is not None else 'op zijn plaats'}, "
        f"succes {'ja' if result and result['ok'] else 'nee'}, "
        f"aanvraag en bestanden {'bewaard' if bewaard else 'gewijzigd'}.",
        flush=True,
    )
    if intact:
        return vrij == 2 and vorige == verwacht and getuige(enc) == 75 and result["ok"]
    return vrij == 0 and bewaard and result is None and getuige(enc) == 90


if sys.argv[1] == "schrijf":
    schrijf(sys.argv[2])
raise SystemExit(0 if meet(sys.argv[1]) else 1)
