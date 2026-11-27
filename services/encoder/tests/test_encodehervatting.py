"""Twee afzonderlijke encoderprocessen op een korte lokale film."""

from __future__ import annotations

import asyncio
import json
import multiprocessing
import os
import signal
import subprocess
import time
from pathlib import Path
from unittest.mock import patch

import psutil
import pytest
from conftest import TESTDATA
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


@pytest.mark.parametrize("laatste,behouden", [(1.083, 3), (1.125, 2)])
def test_oude_staart_moet_alle_opeenvolgende_bronbeelden_bevatten(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, laatste: float, behouden: int,
) -> None:
    from app import hervatpunten

    bron = tmp_path / "bron.mkv"
    eerste = hervatpunten.Fragment(24, 0, 1, "0" * 64)
    staart = hervatpunten.Fragment(3, 1, 1.167, "0" * 64)
    stand = hervatpunten.Hervatpunt(tmp_path, "proef", 3, [eerste, staart])
    origineel = stand.pad(1)
    origineel.write_bytes(b"oorspronkelijke staart")

    def tijden(path: Path, interval: list[str], **kwargs) -> list[float]:
        if path == bron:
            return [5 + round(i / 24, 3) for i in range(30)]
        if path == origineel:
            return [1.000, 1.042, laatste]
        return [1.000, 1.042]

    def kopieer(args, **kwargs):
        assert args[args.index("-c:v") + 1] == "copy"
        assert args[args.index("-frames:v") + 1] == str(behouden)
        Path(args[-1]).write_bytes(b"gekopieerde prefix")
        return subprocess.CompletedProcess(args, 0, stderr="")

    monkeypatch.setattr(hervatpunten, "_beeldtijden", tijden)
    monkeypatch.setattr(subprocess, "run", kopieer)
    monkeypatch.setattr(hervatpunten, "_meet", lambda p, **kw: hervatpunten.Fragment(
        behouden, 1, 1.083, file_sha256(p)))
    stand.borg_prefix(bron, origineel, 1)
    assert stand.delen[0] == eerste and stand.delen[1].beelden == behouden
    assert origineel.read_bytes() == b"oorspronkelijke staart"
    if behouden < staart.beelden:
        assert stand.pad(1).read_bytes() == b"gekopieerde prefix"
        assert json.loads((tmp_path / "stand.json").read_text())["delen"][1]["beelden"] == behouden


@pytest.mark.parametrize(("begin", "eerste"), [(-0.023, 0.0), (0.125, 0.148)])
def test_bronklok_telt_vanaf_de_gemeten_containerstart(tmp_path, monkeypatch, begin, eerste):
    from app import hervatpunten

    uitvoer = (
        f"frame|best_effort_timestamp_time={eerste}\n"
        f"frame|best_effort_timestamp_time={eerste + 0.042}\n"
        f"format|start_time={begin}\n"
    )
    monkeypatch.setattr(subprocess, "run", lambda args, **kw:
                        subprocess.CompletedProcess(args, 0, stdout=uitvoer, stderr=""))
    bron = tmp_path / "bron.mkv"
    assert hervatpunten._beeldtijden(bron, [], bronklok=True) == pytest.approx([0.023, 0.065])
    assert hervatpunten._beeldtijden(bron, []) == pytest.approx([eerste, eerste + 0.042])


def test_verschoven_begintijd_blokkeert_overname_van_oud_beeldwerk(tmp_path, monkeypatch):
    from app import hervatpunten

    oud = tmp_path / "resultaat.mkv.deel"
    oud.write_bytes(b"bewaard voor beoordeling")
    stand = hervatpunten.Hervatpunt(tmp_path, "proef", 6, [])
    monkeypatch.setattr(hervatpunten, "_beeldtijden", lambda pad, *args, **kw:
                        [0.5, 0.542] if pad == oud else [0.0, 0.042, 0.083])
    with pytest.raises(hervatpunten.Hervatfout, match="eerste bronbeeld"):
        stand.borg_prefix(tmp_path / "bron", oud, 0)
    assert stand.delen == [] and oud.read_bytes() == b"bewaard voor beoordeling"


def test_tijdafwijking_midden_in_deel_bewaart_alle_beelden(tmp_path):
    from app import hervatpunten

    bron, deel = tmp_path / "bron.mkv", tmp_path / "resultaat.mkv.deel"
    kopie = ["ffmpeg", "-nostdin", "-v", "error", "-copyts", "-i"]
    video = ["-map", "0:v:0", "-c", "copy", "-avoid_negative_ts", "disabled"]
    subprocess.run([*kopie, str(TESTDATA / "al-av1.mkv"), *video, str(bron)], check=True)
    subprocess.run([*kopie, str(bron), *video, "-frames:v", "60", "-bsf:v",
                    r"setts=pts=PTS+0.010/TB*eq(N\,24):dts=DTS", "-f", "matroska", str(deel)],
                   check=True)
    tijden, afwijkend = beeldtijden(bron), beeldtijden(deel)
    assert len(afwijkend) == 60
    assert [i for i, tijd in enumerate(afwijkend) if abs(tijd - tijden[i]) > 0.002] == [24]
    voor = file_sha256(deel), file_sha256(bron)
    stand = hervatpunten.Hervatpunt(tmp_path, "proef", 6, [])
    with pytest.raises(hervatpunten.Hervatfout, match="beeldtijd"):
        stand.borg_prefix(bron, deel, 0)
    assert not stand.delen and (file_sha256(deel), file_sha256(bron)) == voor


@pytest.mark.parametrize(("gepland", "afkeuring", "fragmenten"), [
    (True, "structuur", False), (False, "structuur", False),
    (False, "kwaliteit", False), (True, "kwaliteit", True),
])
def test_eerder_geverifieerd_eindbestand_doorloopt_opnieuw_de_eindcontrole(
    tmp_path, gepland, afkeuring, fragmenten
):
    from contextlib import nullcontext
    from dataclasses import replace

    from fastapi.testclient import TestClient

    from app import hervatpunten
    from app.main import create_app
    from app.onderhoud import sweep_finished_job_cache
    from app.wachtrijherstel import recover_queue

    db, settings, source, job = _case(tmp_path)
    origineel = file_sha256(source)

    def start(**opties):
        return asyncio.run(uitvoerder.run_job(db, settings, job, storage_validator=lambda: None,
                                              **opties))

    class Onderbreking(BaseException):
        pass

    async def encode_uit_vorige_versie(args, source, job, duration, run, vastgelegd, gpu=None):
        return await run(args)

    oude_encoder = patch.object(hervatpunten, "encode", encode_uit_vorige_versie)
    with (
        nullcontext() if fragmenten else oude_encoder,
        patch.object(uitvoerder, "measure_quality", side_effect=Onderbreking),
        pytest.raises(Onderbreking),
    ):
        start()
    eerste = dict(db.fetch_one("SELECT * FROM verificatie WHERE job_id=?", (job,)))
    assert eerste["geslaagd"] == 1
    cache = settings.cache_root / str(job)
    resultaat = cache / "resultaat.mkv"
    resultaat_hash = file_sha256(resultaat)
    if gepland:
        geplandestop.announce(settings)
    recover_orphaned_jobs(db, settings)
    percent = db.fetch_one("SELECT percent FROM job WHERE id=?", (job,))[0]
    assert percent > 0
    opties = {}
    if afkeuring == "structuur":
        opties["verification_hook"] = lambda v: replace(v, passed=False, reason="proefafkeuring")
    with (
        patch.object(hervatpunten, "encode", side_effect=AssertionError) as encoder,
        patch.object(uitvoerder, "measure_quality", return_value={"metric": "vmaf", "score": 50}),
    ):
        assert not start(**opties)
        encoder.assert_not_called()
    assert db.fetch_one("SELECT 1 FROM vervang_transactie WHERE job_id=?", (job,)) is None
    assert file_sha256(source) == origineel
    sweep_finished_job_cache(db, settings)
    recover_queue(db, settings)
    assert file_sha256(resultaat) == resultaat_hash
    assert hervatpunten.eerder_geverifieerd(db, cache, job)
    fout = db.fetch_one("SELECT percent,foutsoort,fout FROM job WHERE id=?", (job,))
    assert fout["percent"] == percent and fout["foutsoort"] == "Hervatfout"
    reden = "proefafkeuring" if afkeuring == "structuur" else "VMAF"
    assert reden in fout["fout"]
    assert reden in db.fetch_one("SELECT fout FROM job_poging WHERE job_id=? AND poging=2",
                                 (job,))[0]
    laatste = db.fetch_one("SELECT geslaagd FROM verificatie WHERE job_id=?", (job,))[0]
    assert laatste == (0 if afkeuring == "structuur" else 1)
    historie = db.fetch_all("SELECT * FROM verificatie_poging WHERE job_id=? ORDER BY poging",
                            (job,))
    assert [(v["poging"], v["geslaagd"]) for v in historie] == [(1, 1), (2, laatste)]
    assert {k: historie[0][k] for k in eerste} == eerste

    app = create_app(settings, db, start_planner=False, validate_storage=False)
    with TestClient(app) as client:
        client.post(f"/api/jobs/{job}/opnieuw").raise_for_status()
    with (
        patch.object(hervatpunten, "encode", side_effect=AssertionError) as encoder,
        patch.object(uitvoerder, "measure_quality", return_value={"metric": "vmaf", "score": 99}),
    ):
        assert start()
        encoder.assert_not_called()
    assert file_sha256(source) == resultaat_hash
    assert db.fetch_one("SELECT count(*) FROM verificatie_poging WHERE job_id=?", (job,))[0] == 3


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


def test_geplande_stop_voor_de_eerste_normale_fragmentgrens(tmp_path, monkeypatch):
    import test_uitvoerder

    from app import hervatpunten

    corpus = tmp_path / "corpus"
    corpus.mkdir()
    film = corpus / "hdr10.mkv"
    subprocess.run(["ffmpeg", "-nostdin", "-loglevel", "error", "-stream_loop", "3",
                    "-i", str(test_uitvoerder.TESTDATA / film.name), "-map", "0", "-c", "copy",
                    str(film)], check=True, timeout=60)
    oud = time.time() - 7200
    os.utime(film, (oud, oud))
    monkeypatch.setattr(test_uitvoerder, "TESTDATA", corpus)
    db, instellingen, bron, job = test_uitvoerder._case(tmp_path)
    bronhash = file_sha256(bron)
    echte_uitvoering = uitvoerder._run_process
    vast = []

    async def stop_tijdens_encode(database, job_id, args, log, duur):
        if "libsvtav1" not in args:
            return await echte_uitvoering(database, job_id, args, log, duur)
        assert float(args[args.index("-segment_time") + 1]) == 60 > duur
        vertraagd = args.copy()
        vertraagd.insert(vertraagd.index("-i"), "-re")
        taak = asyncio.create_task(echte_uitvoering(database, job_id, vertraagd, log, duur))
        while not taak.done():
            rij = database.fetch_one("SELECT proces_pid,percent FROM job WHERE id=?", (job_id,))
            if rij["proces_pid"] and 5 <= (rij["percent"] or 0) < 95:
                proces = psutil.Process(rij["proces_pid"])
                assert proces.ppid() == os.getpid() and args[-1] in proces.cmdline()
                vast.append(rij["percent"])
                geplandestop.announce(instellingen)
                uitvoerder.request_shutdown()
                proces.send_signal(signal.SIGTERM)
                break
            await asyncio.sleep(0.05)
        return await taak

    with patch.object(uitvoerder, "_run_process", stop_tijdens_encode):
        try:
            assert not asyncio.run(uitvoerder.run_job(db, instellingen, job,
                                                      storage_validator=lambda: None))
        finally:
            uitvoerder.reset_shutdown()
    assert len(vast) == 1 and file_sha256(bron) == bronhash
    heropend = Database(instellingen)
    heropend.initialize()
    recover_orphaned_jobs(heropend, instellingen)
    map_job = instellingen.cache_root / str(job)
    register = hervatpunten.Hervatpunt.lees(map_job)
    geborgd = sum(d.beelden for d in register.delen) if register else 0
    juist_na = heropend.fetch_one("SELECT percent,status FROM job WHERE id=?", (job,))
    assert juist_na["status"] == "wacht"
    assert register is not None
    # Meet welk aaneengesloten beeldwerk in het werkelijk afgesloten fragment zit.
    beelden = beeldtijden(register.map / "beeld-000000.mkv")
    tijden_bron = beeldtijden(bron)
    voorvoegsel = 0
    for t, b in zip(beelden, tijden_bron, strict=False):
        if abs((t - beelden[0]) - (b - tijden_bron[0])) > 0.002:
            break
        voorvoegsel += 1
    assert voorvoegsel > 0
    print(f"standaardfragment=60s, gemeld voor stop={vast[0]:.2f}%, na heropenen="
          f"{juist_na['percent']}%, opeenvolgende leesbare beelden={voorvoegsel}, "
          f"beelden in hervatregister={geborgd}, bronhash gelijk")
    assert juist_na["percent"] >= vast[0] and geborgd == voorvoegsel > 0, (
        "Een geplande stop verliest gemelde voortgang voor de eerste fragmentgrens")
    assert asyncio.run(uitvoerder.run_job(
        heropend, instellingen, job, storage_validator=lambda: None))
    assert heropend.fetch_one("SELECT geslaagd FROM verificatie WHERE job_id=?", (job,))[0] == 1
    na = beeldtijden(bron)
    assert [t - na[0] for t in na] == pytest.approx(
        [t - tijden_bron[0] for t in tijden_bron], abs=0.002)
    afwijking = max(abs((t - na[0]) - (b - tijden_bron[0]))
                    for t, b in zip(na, tijden_bron, strict=True))
    print(f"Na hervatten {len(na)} beelden, grootste tijdverschil {afwijking:.3f} seconde")


@pytest.mark.parametrize("bronbewijs", [False, True], ids=["bronbewijs-ontbreekt", "kapot-deel"])
def test_onleesbaar_oud_deel_blijft_bewaard_tot_na_onderhoud(tmp_path: Path, bronbewijs: bool):
    from app import hervatpunten
    from app.onderhoud import sweep_finished_job_cache
    from app.wachtrijherstel import recover_queue

    db, settings, source, job = _case(tmp_path)
    cache = settings.cache_root / str(job)
    deel = cache / "resultaat.mkv.deel"
    origineel = file_sha256(source)

    class Onderbreking(BaseException):
        pass

    async def oude_poging(args, *rest):
        deel.write_bytes(b"onleesbaar proefbestand")
        db.execute("UPDATE job SET percent=42,commando=? WHERE id=?", (json.dumps(args), job))
        raise Onderbreking

    with patch.object(hervatpunten, "encode", oude_poging), pytest.raises(Onderbreking):
        asyncio.run(uitvoerder.run_job(db, settings, job, storage_validator=lambda: None))
    if not bronbewijs:
        db.execute("UPDATE job SET bron_inode=NULL WHERE id=?", (job,))
    assert recover_orphaned_jobs(db, settings) == 1
    with patch.object(uitvoerder, "_run_process", return_value=False) as proces:
        assert not asyncio.run(uitvoerder.run_job(
            db, settings, job, storage_validator=lambda: None))
    row = db.fetch_one("SELECT status,foutsoort,percent,fout FROM job WHERE id=?", (job,))
    assert tuple(row[:3]) == ("mislukt", "Hervatfout", 42)
    reden = "beeldtijden" if bronbewijs else "bronidentiteit"
    assert "gepauzeerd" in row["fout"] and reden in row["fout"]
    assert job not in sweep_finished_job_cache(db, settings)
    recover_queue(db, settings)
    assert deel.read_bytes() == b"onleesbaar proefbestand" and file_sha256(source) == origineel
    assert db.fetch_one("SELECT 1 FROM verificatie WHERE job_id=?", (job,)) is None
    proces.assert_not_called()


def test_geplande_stop_voordat_het_eerste_fragment_bestaat(tmp_path: Path) -> None:
    db, settings, source, job = _case(tmp_path)
    origineel = file_sha256(source)

    async def stop_voor_het_starten(database, job_id, args, log, duration):
        assert "-segment_list" in args
        geplandestop.announce(settings)
        raise uitvoerder.ProcessInterrupted("Geplande stop voor het eerste beeld")

    with patch.object(uitvoerder, "_run_process", stop_voor_het_starten):
        assert not asyncio.run(uitvoerder.run_job(
            db, settings, job, storage_validator=lambda: None))
    recover_orphaned_jobs(db, settings)
    assert file_sha256(source) == origineel
    assert tuple(db.fetch_one("SELECT status,percent FROM job WHERE id=?", (job,))) == ("wacht", 0)
    assert asyncio.run(uitvoerder.run_job(db, settings, job, storage_validator=lambda: None))
    assert db.fetch_one("SELECT geslaagd FROM verificatie WHERE job_id=?", (job,))[0] == 1


def test_uitstel_tot_de_nacht_bewaart_het_bevestigde_fragment(tmp_path: Path) -> None:
    from app import hervatpunten

    db, settings, source, job = _case(tmp_path)
    origineel = file_sha256(source)
    cache = settings.cache_root / str(job)
    stand = hervatpunten.Hervatpunt(cache / hervatpunten.MAP, "proef", 5, [])

    async def maak_plaats(*args):
        stand.map.mkdir()
        stand.pad(0).write_bytes(b"reeds geregistreerd fragment")
        stand.delen.append(hervatpunten.Fragment(48, 0, 2, file_sha256(stand.pad(0))))
        stand.bewaar()
        raise uitvoerder.EncodePreempted("De CPU-job gaat vannacht verder")

    with patch.object(hervatpunten, "encode", maak_plaats):
        assert not asyncio.run(uitvoerder.run_job(
            db, settings, job, storage_validator=lambda: None))
    recover_orphaned_jobs(db, settings)
    rij = db.fetch_one("SELECT status,alleen_nacht,percent FROM job WHERE id=?", (job,))
    assert tuple(rij) == ("wacht", 1, 40)
    assert stand.pad(0).read_bytes() == b"reeds geregistreerd fragment"
    assert file_sha256(source) == origineel


@pytest.mark.parametrize("storing", ["procesfout", "vastgelopen", "te traag"])
def test_procesfout_direct_na_het_eerste_bevestigde_fragment(tmp_path, monkeypatch, storing):
    from app import hervatpunten

    db, settings, source, job = _case(tmp_path)
    origineel = file_sha256(source)
    cache = settings.cache_root / str(job)

    async def onderbroken(database, job_id, args, log, duur):
        assert "-segment_list" in args
        (cache / hervatpunten.MAP / "beeld-000000.mkv").write_bytes(b"afgesloten beeldwerk")
        if storing == "vastgelopen":
            raise uitvoerder.EncodeStalled(storing, percent=35, diagnose=storing)
        if storing == "te traag":
            raise uitvoerder.EncodeTooSlow(storing)
        return False

    def bevestig(stand, invoer):
        pad = stand.pad(0)
        if pad.exists():
            stand.delen.append(hervatpunten.Fragment(48, 0, 2, file_sha256(pad)))
            stand.bewaar()

    monkeypatch.setattr(uitvoerder, "_run_process", onderbroken)
    monkeypatch.setattr(hervatpunten.Hervatpunt, "borg_lopend_fragment", bevestig)
    assert not asyncio.run(uitvoerder.run_job(db, settings, job, storage_validator=lambda: None))
    rij = db.fetch_one("SELECT foutsoort,fout FROM job WHERE id=?", (job,))
    assert rij["foutsoort"] == "Hervatfout" and "gepauzeerd" in rij["fout"]
    stand = hervatpunten.Hervatpunt.lees(cache)
    assert stand and stand.delen[0].beelden == 48
    assert stand.pad(0).read_bytes() == b"afgesloten beeldwerk" and file_sha256(source) == origineel
