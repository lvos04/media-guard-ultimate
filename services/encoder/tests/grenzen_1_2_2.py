"""Meet hergebruik van AV1-werk uit 1.2.2."""

import asyncio
import csv
import json
import os
import subprocess
import sys
from dataclasses import replace
from pathlib import Path
from unittest.mock import patch

import pytest
from test_encodehervatting import beeldtijden


def pakketcontroles(pad):
    uitvoer = subprocess.check_output(
        ["ffprobe", "-v", "error", "-select_streams", "v:0", "-show_packets",
         "-show_data_hash", "sha256", "-show_entries", "packet=data_hash",
         "-of", "json", str(pad)], text=True, timeout=60,
    )
    return [pakket["data_hash"] for pakket in json.loads(uitvoer)["packets"]]


def oude_poging(map_, gepland):
    from test_uitvoerder import _case

    from app import geplandestop, uitvoerder

    assert Path(uitvoerder.__file__).resolve().parents[1] == Path(os.environ["OUDE_ENCODER"])
    db, instellingen, bron, job = _case(map_)
    assert max(db.applied_versions()) == 25
    echt_proces = uitvoerder._run_process
    tijden = beeldtijden(bron)

    class Meetgrens(BaseException):
        pass

    async def eerste_stuk(database, job_id, args, log, duur):
        if "libsvtav1" not in args:
            return await echt_proces(database, job_id, args, log, duur)
        deel = Path(args[-1])
        assert deel == instellingen.cache_root / str(job) / "resultaat.mkv.deel"
        # Gebruik de encoderopdracht van 1.2.2 met een reproduceerbare onderbrekingsgrens.
        assert await echt_proces(
            database, job_id, [*args[:-1], "-frames:v", "60", args[-1]], log, duur
        )
        prefix = beeldtijden(deel)
        assert (len(prefix), len(tijden)) == (60, 144), (len(prefix), len(tijden))
        assert [t - prefix[0] for t in prefix] == pytest.approx(
            [t - tijden[0] for t in tijden[: len(prefix)]], abs=0.002
        )
        percentage = round(100 * len(prefix) / len(tijden))
        assert percentage == 42
        database.execute("UPDATE job SET percent=? WHERE id=?", (percentage, job_id))
        if gepland:
            geplandestop.announce(instellingen)
        raise Meetgrens  # nog geen resultaat gepromoveerd of bron vervangen

    with patch.object(uitvoerder, "_run_process", eerste_stuk), pytest.raises(Meetgrens):
        asyncio.run(uitvoerder.run_job(db, instellingen, job, storage_validator=lambda: None))
    assert tuple(db.fetch_one("SELECT status,percent FROM job WHERE id=?", (job,))) == ("bezig", 42)
    (map_ / "proef.json").write_text(json.dumps({"job": job, "bron": str(bron), "tijden": tijden}))


@pytest.mark.parametrize("gepland", [True, False], ids=["gepland", "onverwacht"])
def test_oud_beeldwerk_wordt_na_de_claim_hergebruikt(tmp_path, gepland):
    from app import hervatpunten, uitvoerder
    from app.config import settings
    from app.db import Database
    from app.planner import recover_orphaned_jobs

    oude_bron = os.environ["OUDE_ENCODER"]
    env = dict(os.environ, PYTHONPATH=oude_bron + os.pathsep + str(Path(__file__).resolve().parent))
    subprocess.run(
        [sys.executable, __file__, str(tmp_path), "gepland" if gepland else "onverwacht"],
        cwd=oude_bron,
        env=env,
        check=True,
        timeout=180,
    )
    proef = json.loads((tmp_path / "proef.json").read_text())
    job = proef["job"]
    bron = Path(proef["bron"])
    instellingen = replace(
        settings,
        library_root=tmp_path / "library",
        pipeline_root=tmp_path,
        cache_root=tmp_path / "cache",
        work_root=tmp_path / "work",
        config_root=tmp_path / "config",
        backup_root=tmp_path / "backups",
        database_path=tmp_path / "config/app.db",
    )
    deel = instellingen.cache_root / str(job) / "resultaat.mkv.deel"
    db = Database(instellingen)
    db.initialize()
    assert max(db.applied_versions()) > 25
    assert recover_orphaned_jobs(db, instellingen) == 1
    assert tuple(db.fetch_one("SELECT status,percent FROM job WHERE id=?", (job,))) == ("wacht", 42)
    assert len(beeldtijden(deel)) == 60
    metingen = []
    echt_proces = uitvoerder._run_process

    async def meet_start(database, job_id, args, log, duur):
        if "libsvtav1" not in args:
            return await echt_proces(database, job_id, args, log, duur)
        register = hervatpunten.Hervatpunt.lees(deel.parent)
        gemeten = sum(f.beelden for f in register.delen) if register else 0
        bestaat = deel.is_file()
        assert register and bestaat
        oud = pakketcontroles(deel)
        assert len(oud) == 60 and pakketcontroles(register.pad(0)) == oud
        percent = database.fetch_one("SELECT percent FROM job WHERE id=?", (job_id,))[0]
        gelukt = await echt_proces(database, job_id, args, log, duur)
        assert gelukt
        if "-segment_list" in args:
            index = Path(args[args.index("-segment_list") + 1])
            with index.open(newline="") as bestand:
                uitgangen = [index.parent / rij[0] for rij in csv.reader(bestand)]
        else:
            uitgangen = [Path(args[-1])]
        geschreven = sum(len(beeldtijden(pad)) for pad in uitgangen)
        metingen.append((gemeten, bestaat, percent, geschreven))
        return gelukt

    with patch.object(uitvoerder, "_run_process", meet_start):
        assert asyncio.run(
            uitvoerder.run_job(db, instellingen, job, storage_validator=lambda: None)
        )
    assert db.fetch_one("SELECT geslaagd FROM verificatie WHERE job_id=?", (job,))[0] == 1
    tijden = beeldtijden(bron)
    assert len(tijden) == len(proef["tijden"]) == 144
    assert [t - tijden[0] for t in tijden] == pytest.approx(
        [t - proef["tijden"][0] for t in proef["tijden"]], abs=0.002
    )
    assert len(metingen) == 1
    gemeten, bestaat, percent, geschreven = metingen[0]
    print(
        f"{'gepland' if gepland else 'onverwacht'}: wachtrij=42%, oud=60/144 beelden, "
        f"workerpercentage={percent}, oud deel aanwezig={bestaat}, geregistreerd={gemeten}, "
        f"nieuw gecodeerd={geschreven}, eindcontrole=144/144"
    )
    assert bestaat and gemeten == 60 and percent >= 42
    assert geschreven == len(tijden) - 60, (
        f"Na 60 leesbare AV1-beelden werden nog 84 nieuwe beelden verwacht, "
        f"gemeten {geschreven}"
    )


if __name__ == "__main__":
    oude_poging(Path(sys.argv[1]), sys.argv[2] == "gepland")
