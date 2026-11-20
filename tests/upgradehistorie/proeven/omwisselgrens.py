"""Onderbreek een echte, voltooide omwisseling vóór het verwijderen van de terugzetaanvraag."""

import hashlib
import os
import sqlite3
import subprocess
import sys
from contextlib import closing
from pathlib import Path
from unittest.mock import patch

from mediaguard import db
from mediaguard.maintenance import preupdate
from startgrens import (
    config,
    core,
    encpad,
    init_testdata,
    kernpad,
    open_diensten,
    stand,
)


def stop_na_omwisseling():
    verwijder = Path.unlink

    def markering_weghalen(pad, *args, **kwargs):
        if pad == core / preupdate.STAGE_MARKER:
            os._exit(83)
        return verwijder(pad, *args, **kwargs)

    with patch.object(Path, "unlink", markering_weghalen):
        open_diensten()
    raise AssertionError("De omwisseling is niet afgemaakt.")


def controleer():
    init_testdata()
    voor = stand()
    setmap = Path(open_diensten()["MG_PRE_UPDATE_SET"])
    assert not preupdate.verify(setmap)
    assert len(voor[0][-1]) == 2 and voor[1] and all(r[-1] == 75 for r in voor[1])
    with closing(sqlite3.connect(encpad)) as enc, enc:
        assert enc.execute("UPDATE job SET percent=90").rowcount == len(voor[1])
    with closing(sqlite3.connect(kernpad)) as kern, kern:
        kern.execute(
            "INSERT INTO outbox_events(ts,topic,payload) VALUES(?,?,?)",
            (db.utcnow(), "library.synced", "{}"),
        )
    assert len(stand()[0][-1]) == 3
    info = preupdate.stage(core, setmap, actor="lokale terugzetproef")
    bestanden = info["files"]
    assert {f["live"] for f in bestanden} == {"core/mediaguard.db", "encode/app.db"}
    assert (
        subprocess.run([sys.executable, "-B", __file__, "stop"], check=False).returncode
        == 83
    )
    assert preupdate.pending(core) == info
    for entry in bestanden:
        live = config / entry["live"]
        assert (
            not (config / entry["staged"]).exists()
            and live.stat().st_size == entry["size"]
        )
        assert hashlib.sha256(live.read_bytes()).hexdigest() == entry["sha256"]
        with closing(
            sqlite3.connect(live.as_uri() + "?mode=ro&immutable=1", uri=True)
        ) as con:
            assert con.execute("PRAGMA integrity_check").fetchone() == ("ok",)
    assert stand() == voor
    (aside,) = (core / "backups" / preupdate.ASIDE_FOLDER).iterdir()
    with closing(
        sqlite3.connect((aside / "encode__app.db").as_uri() + "?mode=ro", uri=True)
    ) as enc:
        assert enc.execute("SELECT min(percent),max(percent) FROM job").fetchone() == (
            90,
            90,
        )
    with closing(
        sqlite3.connect((aside / "core__mediaguard.db").as_uri() + "?mode=ro", uri=True)
    ) as kern:
        assert kern.execute("SELECT count(*) FROM outbox_events").fetchone() == (3,)
    vrij = 0
    for _ in range(2):
        try:
            open_diensten()
        except RuntimeError as exc:
            assert "klaargezette kopie ontbreekt" in str(exc), str(exc)
            assert preupdate.pending(core) == info
        else:
            vrij += 1
        assert stand()[:2] == voor[:2]
    print(
        f"Na volledige omwisseling klopten 2/2 SHA-256-controles en integriteitscontroles; "
        f"{vrij}/2 herstarts vrijgegeven; hersteld 75% en 2 events, opzijgezet 90% en 3 events."
    )
    return int(vrij != 2)


if sys.argv[1:] == ["stop"]:
    stop_na_omwisseling()
else:
    raise SystemExit(controleer())
