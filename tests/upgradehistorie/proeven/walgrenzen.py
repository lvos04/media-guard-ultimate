"""Meet gecommitteerde SQLite-inhoud rond een echte procesonderbreking in de supervisor."""

import hashlib
import json
import os
import shutil
import sqlite3
import sys
import tempfile
from contextlib import closing
from pathlib import Path
from unittest.mock import patch

from mediaguard import aio, db
from mediaguard.maintenance import preupdate
from versieproef import config, core, enc, helper, kind, live, oud, repo, vrijgave, werk

script = Path(__file__).resolve()
assert Path(aio.__file__).resolve().is_relative_to(repo)
percentage = "SELECT DISTINCT percent FROM job"
events = "SELECT id,topic FROM outbox_events ORDER BY id"


def lees(main, query, met_wal=True):
    """Open ook voor de onafhankelijke getuigenmeting alleen een wegwerpkopie."""
    with tempfile.TemporaryDirectory(dir=werk) as tijdelijk:
        kopie = Path(tijdelijk) / "getuige.db"
        shutil.copyfile(main, kopie)
        if met_wal:
            for tail in ("-wal", "-journal"):
                ander = main.with_name(main.name + tail)
                if ander.is_file():
                    shutil.copyfile(ander, kopie.with_name(kopie.name + tail))
        with closing(sqlite3.connect(kopie)) as conn:
            assert conn.execute("PRAGMA integrity_check").fetchall() == [("ok",)]
            return conn.execute(query).fetchall()


def vingerafdruk():
    return {
        p.relative_to(config): hashlib.sha256(p.read_bytes()).digest()
        for p in config.rglob("*")
        if p.is_file() and not p.name.endswith("-shm")
    }


def procent(main):
    ((waarde,),) = lees(main, percentage)
    return int(waarde)


def tweemaal():
    return [kind(repo, script, "lezer", (0, 73)) for _ in range(2)].count(0)


def wal_schrijven():
    # De verbinding sluit wanneer uitsluitend dit proefproces eindigt.
    motorproef = sys.argv[1] == "schrijf-encoder"
    path = enc if motorproef else live
    conn = sqlite3.connect(path)
    assert conn.execute("PRAGMA journal_mode=WAL").fetchone() == ("wal",)
    assert conn.execute("PRAGMA wal_checkpoint(TRUNCATE)").fetchone() == (0, 0, 0)
    conn.execute("PRAGMA wal_autocheckpoint=0")
    if motorproef:
        assert conn.execute("UPDATE job SET percent=90").rowcount > 0
        conn.commit()
    else:
        for naam in ("library.proef_eerste", "library.proef_laatste"):
            conn.execute(
                "INSERT INTO outbox_events(ts,topic,payload) VALUES(?,?,?)",
                (db.utcnow(), naam, "{}"),
            )
            conn.commit()
            if naam == "library.proef_eerste":
                (werk / "eerste-walgrens").write_text(
                    str(Path(str(live) + "-wal").stat().st_size)
                )
    os._exit(0)


def onderbroken_update(wal_vermist, expliciet=False):
    sys.path.insert(0, str(repo / "apps/core/tests"))
    import test_herstartpunten as stappen

    config.mkdir()
    stappen.installatie.__wrapped__(config)
    kind(repo, script, "schrijf-encoder")
    assert lees(enc, percentage, False) == [(75,)] and procent(enc) == 90
    stappen.onderbreek(config, "update", "lege-tijdelijke-naam")
    markering = core / preupdate.COMMIT_MARKER
    oorsprong = core / preupdate.COMMIT_ORIGIN
    bewijs = json.loads(oorsprong.read_bytes())
    archief = core / "backups" / preupdate.FOLDER / bewijs["set"]
    wal = enc.with_name(enc.name + "-wal")
    (bewijsregel,) = (r for r in bewijs["databases"] if r["bestand"] == "encode/app.db")
    grens = bewijsregel["wal_positie"]
    assert bewijsregel["wal_frames"] > 0 and 32 < grens <= wal.stat().st_size
    assert (
        hashlib.sha256(wal.read_bytes()[:grens]).hexdigest()
        == bewijsregel["wal_sha256"]
    )
    assert not markering.exists() and not preupdate.verify(archief)
    assert list(core.glob(preupdate.COMMIT_MARKER + ".*.part"))
    assert procent(archief / "encode/app.db") == procent(enc) == 90
    if wal_vermist:
        for tail in ("-wal", "-shm"):
            pad = enc.with_name(enc.name + tail)
            if pad.exists():
                pad.rename(werk / ("veilig-gesteld" + tail))
        assert procent(enc) == 75
    voor = vingerafdruk()
    starts = tweemaal()
    stand = procent(enc)
    behouden = voor == vingerafdruk()
    assert procent(archief / "encode/app.db") == 90 and len(lees(live, events)) == 2
    bewaard = (
        oorsprong.is_file() and oorsprong.read_bytes() == json.dumps(bewijs).encode()
    )
    if expliciet:
        preupdate.stage(core, archief, actor="expliciete testterugzetting")
        opnieuw = tweemaal()
        herstel = procent(enc)
        print(
            f"Uitval vóór de updatemarkering en ontbrekende WAL: automatisch {starts}/2 "
            f"starts op {stand}%, na expliciet terugzetten van de gekozen set "
            f"{opnieuw}/2 starts op {herstel}%, beide oorspronkelijke events behouden."
        )
        return (
            starts == 0
            and behouden
            and bewaard
            and opnieuw == 2
            and herstel == 90
            and len(lees(live, events)) == 2
        )
    print(
        f"Uitval na het duurzame oorsprongsbewijs, vóór de markering, WAL "
        f"{'ontbreekt' if wal_vermist else 'intact'}: {starts}/2 starts vrijgegeven, "
        f"actief {stand}%, geverifieerde set 90%, oorsprongsbewijs "
        f"{'bewaard' if bewaard else 'vervangen of verwijderd'}, "
        f"duurzame bestanden {'gelijk' if behouden else 'gewijzigd'}."
    )
    return (
        (starts == 0 and behouden and bewaard)
        if wal_vermist
        else (starts == 2 and stand == 90)
    )


def oude_of_nieuwe_wissel(helemaal):
    sys.path.insert(0, str(helper))
    from startgrens import init_testdata, open_diensten

    init_testdata()
    archief = Path(open_diensten()["MG_PRE_UPDATE_SET"])
    with closing(sqlite3.connect(enc)) as conn, conn:
        assert conn.execute("UPDATE job SET percent=90").rowcount > 0
    preupdate.stage(core, archief, actor="plaatselijke proef")
    kind(repo, script, "schrijf-events")
    assert len(lees(live, events, False)) == 2 and len(lees(live, events)) == 4
    echt = os.replace

    def grens(src, dst, *args):
        echt(src, dst, *args)
        if Path(dst) == (enc if helemaal else live) and str(src).endswith(
            preupdate.STAGED_SUFFIX
        ):
            os._exit(87)

    with patch.object(os, "replace", grens):
        aio.before_services({}, config, None, migrate_core=True)
    raise AssertionError("De wisselgrens is niet bereikt")


def terugzetten(schrijver, fase, storing):
    kind(oud if schrijver == "oud" else repo, script, "wissel-" + fase, (87,))
    marker = core / preupdate.STAGE_MARKER
    aanvraag = json.loads(marker.read_bytes())
    assert ("omwisseling" in aanvraag) == (schrijver == "nieuw")
    (aside,) = (core / "backups" / preupdate.ASIDE_FOLDER).iterdir()
    vorige = aside / "core__mediaguard.db"
    wal = vorige.with_name(vorige.name + "-wal")
    laatste = lees(vorige, events)
    assert len(laatste) == 4 and laatste[-1][1] == "library.proef_laatste"
    assert len(lees(vorige, events, False)) == 2
    grens = int((werk / "eerste-walgrens").read_text())
    assert 32 < grens < wal.stat().st_size
    if storing == "zonder-wal":
        wal.rename(werk / "veilig-gestelde-wal")
    elif storing == "zonder-slotcommit":
        shutil.copyfile(wal, werk / "volledige-wal")
        with wal.open("r+b") as stream:
            stream.truncate(grens)
    meting = lees(vorige, events)
    aantal = {"intact": 4, "zonder-wal": 2, "zonder-slotcommit": 3}[storing]
    assert len(meting) == aantal
    if aantal == 3:
        assert meting[-1][1] == "library.proef_eerste"
    voor = vingerafdruk()
    begin = procent(enc)
    assert begin == (75 if fase == "volledig" else 90)
    starts = tweemaal()
    gelijk = voor == vingerafdruk()
    res = preupdate.take_result(core)
    succes = bool(res and res["ok"])
    assert lees(vorige, events) == meting and len(lees(live, events)) == 2
    print(
        f"Schrijver {schrijver}, {fase}, {storing}: vorige kern bevat {aantal}/4 events, "
        f"{starts}/2 starts vrijgegeven, actieve encoder {begin}% naar {procent(enc)}%, "
        f"succes {'ja' if succes else 'nee'}, aanvraag {'bewaard' if marker.exists() else 'verwijderd'}, "
        f"duurzame bestanden {'gelijk' if gelijk else 'gewijzigd'}."
    )
    if storing == "intact" and schrijver == "nieuw":
        return starts == 2 and succes and not marker.exists()
    return (
        starts == 0
        and not succes
        and gelijk
        and marker.exists()
        and procent(enc) == begin
    )


if __name__ == "__main__":
    if sys.argv[1] == "lezer":
        raise SystemExit(vrijgave())
    if sys.argv[1].startswith("schrijf-"):
        wal_schrijven()
    if sys.argv[1].startswith("wissel-"):
        oude_of_nieuwe_wissel(sys.argv[1] == "wissel-volledig")
    if sys.argv[1] == "oorsprong":
        goed = onderbroken_update(
            sys.argv[2] == "zonder-wal", "expliciet" in sys.argv[3:]
        )
    else:
        goed = terugzetten(*sys.argv[1:])
    raise SystemExit(0 if goed else 1)
