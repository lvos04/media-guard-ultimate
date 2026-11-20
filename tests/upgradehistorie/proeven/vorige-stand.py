"""Toets de vorige stand van een door de oude schrijver voltooide omwisseling."""

import hashlib
import json
import os
import sqlite3
import sys
from contextlib import closing
from pathlib import Path
from unittest.mock import patch

from mediaguard import aio, db
from mediaguard.maintenance import preupdate
from versieproef import config, core, enc, helper, kind, lees, live, oud, repo, vrijgave

sys.path.insert(0, str(helper))
from startgrens import init_testdata, open_diensten

bestand = Path(__file__).resolve()
marker = core / preupdate.STAGE_MARKER
percent = "SELECT DISTINCT percent FROM job"
aantal = "SELECT count(*) FROM outbox_events"


def voorbereiden(nieuw, voor_resultaat, halverwege=False):
    assert (repo == oud) != nieuw
    init_testdata()
    archief = Path(open_diensten()["MG_PRE_UPDATE_SET"])
    with closing(sqlite3.connect(enc)) as e, e, closing(sqlite3.connect(live)) as k, k:
        assert e.execute("UPDATE job SET percent=90").rowcount > 0
        k.execute(
            "INSERT INTO outbox_events(ts,topic,payload) VALUES(?,?,?)",
            (db.utcnow(), "library.synced", "{}"),
        )
    preupdate.stage(core, archief, actor="proef")
    verwijderen = Path.unlink

    if halverwege:
        verplaats = os.replace

        def na_kern(src, dst):
            verplaats(src, dst)
            if Path(dst) == live and Path(src).name.endswith(preupdate.STAGED_SUFFIX):
                os._exit(84)

        with patch.object(os, "replace", na_kern):
            aio.before_services({}, config, None, migrate_core=True)
        raise AssertionError("tussenstap van de omwisseling niet bereikt")

    def onderbreken(pad, *args, **kwargs):
        if pad == marker:
            os._exit(83)
        return verwijderen(pad, *args, **kwargs)

    if voor_resultaat:
        schrijf_resultaat = preupdate._write_result

        def stop_voor_melding(kern, info, eigenaar):
            if kern != core:
                return schrijf_resultaat(kern, info, eigenaar)
            assert info["ok"] and not (core / preupdate.RESULT_FILE).exists()
            os._exit(83)

        with patch.object(preupdate, "_write_result", stop_voor_melding):
            aio.before_services({}, config, None, migrate_core=True)
        raise AssertionError("resultaatgrens niet bereikt")
    with patch.object(Path, "unlink", onderbreken):
        aio.before_services({}, config, None, migrate_core=True)
    raise AssertionError("oude omwisselgrens niet bereikt")


def proef(beschadig, nieuw, voor_resultaat):
    fase = "nieuwe-voorbereiding" if nieuw else "voorbereiden"
    kind(
        repo if nieuw else oud,
        bestand,
        "voor-resultaat" if voor_resultaat else fase,
        (83,),
    )
    aanvraag = json.loads(marker.read_text())
    assert ("omwisseling" in aanvraag) == nieuw
    for item in aanvraag["files"]:
        pad = config / item["live"]
        assert pad.is_file() and not (config / item["staged"]).exists()
        assert hashlib.sha256(pad.read_bytes()).hexdigest() == item["sha256"]
        assert lees(pad, "PRAGMA integrity_check", immutable=True) == [("ok",)]
    (vorige,) = (core / "backups" / preupdate.ASIDE_FOLDER).iterdir()
    vorig_enc = vorige / "encode__app.db"
    assert lees(vorig_enc, percent) == [(90,)] and lees(
        vorige / "core__mediaguard.db", aantal
    ) == [(3,)]
    if beschadig:
        # Een plaatselijke opslagfout in één proefbestand, ná het opzijzetten van de laatste stand.
        with vorig_enc.open("r+b") as stream:
            stream.write(bytes(16))
        try:
            lees(vorig_enc, "PRAGMA integrity_check", immutable=True)
        except sqlite3.DatabaseError:
            pass
        else:
            raise AssertionError("de beschadiging is niet aangetoond")
    digest = hashlib.sha256(vorig_enc.read_bytes()).digest()
    starts = []
    for _ in range(2):
        starts.append(kind(repo, bestand, "lezer", (0, 73)))
        assert lees(enc, percent) == [(75,)] and lees(live, aantal) == [(2,)]
        assert lees(vorige / "core__mediaguard.db", aantal) == [(3,)]
        assert hashlib.sha256(vorig_enc.read_bytes()).digest() == digest
    resultaat = preupdate.take_result(core)
    if resultaat and resultaat["ok"]:
        assert Path(resultaat["previous"]) == vorige
    positief = bool(resultaat and resultaat["ok"])
    geval = "beschadigd" if beschadig else "intact"
    print(
        f"Vorige encoder {geval}: {starts.count(0)}/2 starts vrijgegeven; "
        f"succes gemeld {'ja' if positief else 'nee'}; aanvraag {'bewaard' if marker.exists() else 'verwijderd'}; "
        "actief 75% en 2 events, vorige kern 3 events.",
        flush=True,
    )
    if beschadig or not nieuw:
        return int(starts != [73, 73] or positief or not marker.exists())
    return int(starts != [0, 0] or not positief or marker.exists())


def deelproef():
    kind(oud, bestand, "na-kern", (84,))
    aanvraag = json.loads(marker.read_text())
    assert "omwisseling" not in aanvraag
    (vorige,) = (core / "backups" / preupdate.ASIDE_FOLDER).iterdir()
    vorig = vorige / "core__mediaguard.db"
    assert lees(vorig, aantal) == [(3,)] and lees(live, aantal) == [(2,)]
    assert lees(enc, percent) == [(90,)]
    klaar = enc.with_name(enc.name + preupdate.STAGED_SUFFIX)
    assert lees(klaar, percent, immutable=True) == [(75,)]
    with vorig.open("r+b") as f:
        f.write(bytes(16))
    try:
        lees(vorig, "PRAGMA integrity_check", immutable=True)
    except sqlite3.DatabaseError:
        pass
    else:
        raise AssertionError("beschadiging van de vorige kern niet vastgesteld")
    st = [kind(repo, bestand, "lezer", (0, 73)) for _ in range(2)]
    (waarde,) = lees(enc, percent)
    uit = preupdate.take_result(core)
    print(
        f"Oude omwisseling na de kernwissel, vorige kern onleesbaar: {st.count(0)}/2 starts vrijgegeven; "
        f"encoder van 90% naar {waarde[0]}%; succes gemeld {'ja' if uit and uit['ok'] else 'nee'}; "
        f"aanvraag {'bewaard' if marker.exists() else 'verwijderd'}.",
        flush=True,
    )
    return int(
        st != [73, 73] or waarde != (90,) or not klaar.exists() or not marker.exists()
    )


if sys.argv[1:] == ["na-kern"]:
    voorbereiden(False, False, True)
elif sys.argv[1:] == ["half"]:
    raise SystemExit(deelproef())
elif sys.argv[1:] in (["voorbereiden"], ["nieuwe-voorbereiding"], ["voor-resultaat"]):
    voorbereiden(sys.argv[1:] != ["voorbereiden"], sys.argv[1:] == ["voor-resultaat"])
elif sys.argv[1:] == ["lezer"]:
    raise SystemExit(vrijgave())
else:
    raise SystemExit(
        proef(
            "intact" not in sys.argv[1:],
            "nieuwe-schrijver" in sys.argv[1:],
            "vóór-resultaat" in sys.argv[1:],
        )
    )
