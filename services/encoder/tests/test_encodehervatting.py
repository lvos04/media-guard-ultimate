"""Twee afzonderlijke encoderprocessen op een korte lokale film."""

from __future__ import annotations

import asyncio
import json
import multiprocessing
import os
import signal
import subprocess
from pathlib import Path
from unittest.mock import patch

import psutil
import pytest
from test_uitvoerder import _case

from app import geplandestop, uitvoerder
from app.config import Settings
from app.db import Database
from app.planner import recover_orphaned_jobs
from app.vervanging import file_sha256


def onderbreek(settings: Settings, job: int, gepland: bool) -> None:
    db = Database(settings)
    run = uitvoerder._run_process

    async def eerste_beelden(database, job_id, args, log, duration):
        if "libsvtav1" not in args:
            return await run(database, job_id, args, log, duration)
        # Speel de fixture in werkelijke tijd in en gebruik korte fragmenten om
        # een bepaald beeldinterval te onderbreken, ook op een snelle runner.
        args = list(args)
        args.insert(args.index("-i"), "-re")
        args[args.index("-segment_time") + 1] = "1"
        args[-1:-1] = ["-g", "24"]
        closed = Path(args[args.index("-segment_list") + 1])
        running = asyncio.create_task(run(database, job_id, args, log, duration))
        while not running.done():
            if closed.is_file() and closed.read_bytes().endswith(b"\n"):
                pid = db.fetch_one("SELECT proces_pid FROM job WHERE id=?", (job,))[0]
                proc = psutil.Process(pid)
                assert proc.ppid() == os.getpid() and args[-1] in proc.cmdline()
                if gepland:
                    geplandestop.announce(settings)
                    uitvoerder.request_shutdown()
                    proc.send_signal(signal.SIGTERM)
                    return await running
                proc.kill()
                await running
                os._exit(23)  # de planner heeft geen gelegenheid zijn toestand op te slaan
            await asyncio.sleep(.02)
        await running
        pytest.fail("Geen lopende encode met gesloten fragment aangetroffen")

    with patch.object(uitvoerder, "_run_process", eerste_beelden):
        try:
            assert not asyncio.run(
                uitvoerder.run_job(db, settings, job, storage_validator=lambda: None)
            )
        finally:
            uitvoerder.reset_shutdown()


def beeldtijden(path: Path) -> list[float]:
    output = subprocess.check_output(
        [
            "ffprobe",
            "-v",
            "error",
            "-threads",
            "1",
            "-select_streams",
            "v:0",
            "-show_frames",
            "-show_entries",
            "frame=best_effort_timestamp_time",
            "-of",
            "csv=p=0",
            str(path),
        ],
        text=True,
    )
    return [
        float(line.split(",")[0])
        for line in output.splitlines()
        if line and line.split(",")[0].replace(".", "", 1).isdigit()
    ]


@pytest.mark.parametrize("gepland", [True, False], ids=["gepland", "onverwacht"])
def test_hervat_echte_encode_na_processtop(tmp_path: Path, gepland: bool) -> None:
    db, settings, source, job = _case(tmp_path)
    origineel = file_sha256(source)
    tijden = beeldtijden(source)
    proc = multiprocessing.get_context("spawn").Process(
        target=onderbreek, args=(settings, job, gepland)
    )
    proc.start()
    proc.join(120)
    if proc.is_alive():
        proc.terminate()
        proc.join()
        pytest.fail("De eigen proef bereikte de onderbreking niet")
    assert proc.exitcode == (0 if gepland else 23)
    assert file_sha256(source) == origineel
    recover_orphaned_jobs(db, settings)
    row = db.fetch_one("SELECT status,percent FROM job WHERE id=?", (job,))
    assert row["status"] == "wacht" and 0 < row["percent"] < 100
    journal = settings.cache_root / str(job) / "hervatpunten/stand.json"
    frames = sum(c["beelden"] for c in json.loads(journal.read_text())["delen"])
    assert 0 < frames < len(tijden)
    run = uitvoerder._run_process
    encodes = []

    async def observeer(database, job_id, args, log, duration):
        if "libsvtav1" in args:
            encodes.append(args)
        return await run(database, job_id, args, log, duration)

    with patch.object(uitvoerder, "_run_process", observeer):
        assert asyncio.run(uitvoerder.run_job(db, settings, job, storage_validator=lambda: None))
    assert len(encodes) == 1 and any(f"gte(n,{frames})" in a.replace("\\", "") for a in encodes[0])
    assert db.fetch_one("SELECT geslaagd FROM verificatie WHERE job_id=?", (job,))[0]
    na = beeldtijden(source)
    assert len(na) == len(tijden)
    assert [t - na[0] for t in na] == pytest.approx([t - tijden[0] for t in tijden], abs=0.002)
    print(f"{len(na)} beelden, {frames} hervat, eindcontrole akkoord")


def test_beschadigd_fragment_blokkeert_hervatting_en_vervanging(tmp_path: Path) -> None:
    db, settings, source, job = _case(tmp_path)
    origineel = file_sha256(source)
    onderbreek(settings, job, True)
    fragment = settings.cache_root / str(job) / "hervatpunten" / "beeld-000000.mkv"
    with fragment.open("ab") as handle:
        handle.write(b"beschadigd")
    recover_orphaned_jobs(db, settings)
    row = db.fetch_one("SELECT status,foutsoort FROM job WHERE id=?", (job,))
    assert tuple(row) == ("mislukt", "Hervatfout")
    assert file_sha256(source) == origineel and fragment.is_file()
    assert db.fetch_one("SELECT 1 FROM verificatie WHERE job_id=?", (job,)) is None
    assert db.fetch_one("SELECT 1 FROM vervang_transactie WHERE job_id=?", (job,)) is None


def test_laatste_csv_regel_wacht_op_een_volgende_grens_of_succes(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    from app import hervatpunten

    stand = hervatpunten.Hervatpunt(tmp_path, "proef", 2, [])
    for index in range(2):
        stand.pad(index).touch()
    (tmp_path / "gesloten.csv").write_text("beeld-000000.mkv,0,1\nbeeld-000001.mkv,1,2\n")

    def meting(path: Path) -> hervatpunten.Fragment:
        begin = float(path.stem.split("-")[1])
        return hervatpunten.Fragment(24, begin, begin + 1, "0" * 64)

    monkeypatch.setattr(hervatpunten, "_meet", meting)
    stand.verzamel()
    assert len(stand.delen) == 1
    stand.verzamel(afgerond=True)
    assert len(stand.delen) == 2


@pytest.mark.parametrize("laatste,behouden", [(1.083, 2), (1.125, 1)])
def test_oude_staart_moet_alle_opeenvolgende_bronbeelden_bevatten(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, laatste: float, behouden: int,
) -> None:
    from app import hervatpunten

    bron = tmp_path / "bron.mkv"
    eerste = hervatpunten.Fragment(24, 0, 1, "0" * 64)
    staart = hervatpunten.Fragment(3, 1, 1.167, "0" * 64)
    stand = hervatpunten.Hervatpunt(tmp_path, "proef", 3, [eerste, staart])

    def tijden(path: Path, interval: list[str]) -> list[float]:
        return [6.000, 6.042, 6.083, 6.125] if path == bron else [1.000, 1.042, laatste]

    monkeypatch.setattr(hervatpunten, "_beeldtijden", tijden)
    stand.controleer_oude_staart(bron, bronbegin=5)
    assert len(stand.delen) == behouden and stand.delen[0] == eerste


def test_eerder_geverifieerd_eindbestand_doorloopt_opnieuw_de_eindcontrole(tmp_path: Path) -> None:
    from dataclasses import replace

    from app import hervatpunten

    db, settings, source, job = _case(tmp_path)
    origineel = file_sha256(source)

    class Onderbreking(BaseException):
        pass

    async def encode_uit_vorige_versie(args, source, job, duration, run, vastgelegd, gpu=None):
        return await run(args)

    with (
        patch.object(hervatpunten, "encode", encode_uit_vorige_versie),
        patch.object(uitvoerder, "measure_quality", side_effect=Onderbreking),
        pytest.raises(Onderbreking),
    ):
        asyncio.run(uitvoerder.run_job(db, settings, job, storage_validator=lambda: None))
    assert db.fetch_one("SELECT geslaagd FROM verificatie WHERE job_id=?", (job,))[0] == 1
    recover_orphaned_jobs(db, settings)
    assert db.fetch_one("SELECT percent FROM job WHERE id=?", (job,))[0] > 0
    with patch.object(hervatpunten, "encode", side_effect=AssertionError) as encoder:
        assert not asyncio.run(uitvoerder.run_job(
            db, settings, job, storage_validator=lambda: None,
            verification_hook=lambda result: replace(result, passed=False, reason="proefafkeuring"),
        ))
        encoder.assert_not_called()
    assert db.fetch_one("SELECT geslaagd FROM verificatie WHERE job_id=?", (job,))[0] == 0
    assert db.fetch_one("SELECT 1 FROM vervang_transactie WHERE job_id=?", (job,)) is None
    assert file_sha256(source) == origineel


def test_ingetrokken_toestemming_bewaart_hervatwerk_tot_herbevestiging(tmp_path: Path) -> None:
    from app.ai_controller import revoke_ai_consent
    from app.onderhoud import sweep_finished_job_cache

    db, settings, source, job = _case(tmp_path)
    origineel = file_sha256(source)
    besluit = db.fetch_one("SELECT besluit_id FROM job WHERE id=?", (job,))[0]
    db.execute("UPDATE besluit SET goedkeuringsbron='ai_policy' WHERE id=?", (besluit,))
    onderbreek(settings, job, True)
    recover_orphaned_jobs(db, settings)
    percent = db.fetch_one("SELECT percent FROM job WHERE id=?", (job,))[0]
    assert percent > 0
    fragment = settings.cache_root / str(job) / "hervatpunten/beeld-000000.mkv"
    digest = file_sha256(fragment)
    revoke_ai_consent(db, reason="toestemming opnieuw beoordelen")
    assert db.fetch_one("SELECT status FROM job WHERE id=?", (job,))[0] == "overgeslagen"
    with pytest.raises(uitvoerder.ExecutionError, match="goedgekeurd"):
        uitvoerder.create_job_from_decision(db, besluit)
    assert job not in sweep_finished_job_cache(db, settings)
    assert file_sha256(fragment) == digest and file_sha256(source) == origineel
    db.execute("UPDATE besluit SET status='goedgekeurd' WHERE id=?", (besluit,))
    with pytest.raises(uitvoerder.ExecutionError, match="herbevestiging"):
        uitvoerder.create_job_from_decision(db, besluit)
    db.execute("UPDATE besluit SET status='goedgekeurd',door_mens=1,goedkeuringsbron='mens' "
               "WHERE id=?", (besluit,))
    assert uitvoerder.create_job_from_decision(db, besluit) == job
    row = db.fetch_one("SELECT status,percent FROM job WHERE id=?", (job,))
    assert tuple(row) == ("wacht", percent)
    assert asyncio.run(uitvoerder.run_job(db, settings, job, storage_validator=lambda: None))
    assert db.fetch_one("SELECT geslaagd FROM verificatie WHERE job_id=?", (job,))[0] == 1
