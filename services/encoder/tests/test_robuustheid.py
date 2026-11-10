"""Verbeterpunt 28: de encoder is robuuster bij fouten.

- na een (lange) noodstop wordt een CPU-encode niet als 'te traag' overgeslagen;
- een fout noemt de echte oorzaak, niet alleen 'encoderproces faalde';
- na een onverwachte fout blijft er geen cache staan.
"""

from __future__ import annotations

import asyncio
import signal
from pathlib import Path
from typing import Any

import pytest
from test_planner import _planner_case

from app import afstelling, noodstop, uitvoerder
from app.onderhoud import sweep_finished_job_cache
from app.planner import Planner


@pytest.fixture(autouse=True)
def _reset_noodstop() -> Any:
    noodstop._reset_for_tests()
    yield
    noodstop._reset_for_tests()


def _fake_ffmpeg(tmp_path: Path, body: str, name: str = "ffmpeg") -> Path:
    path = tmp_path / "bin" / name
    path.parent.mkdir(exist_ok=True)
    path.write_text("#!/usr/bin/env python3\nimport sys, time\n" + body, encoding="utf-8")
    path.chmod(0o755)
    return path


def _progress(step_us: int, sleep: float, count: int) -> str:
    return (f"for i in range({count}):\n"
            f"    print(f'out_time_ms={{i*{step_us}}}', flush=True)\n"
            "    print('progress=continue', flush=True)\n"
            f"    time.sleep({sleep})\n")


def _slow_case(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> tuple[Any, int]:
    database, _configured, ids = _planner_case(tmp_path)
    database.execute("UPDATE job SET baan='cpu' WHERE id=?", (ids[0],))
    monkeypatch.setattr(uitvoerder, "PROGRESS_WRITE_SECONDS", 0.05)
    monkeypatch.setattr(uitvoerder, "SLOW_CHECK_SECONDS", 0.6)
    monkeypatch.setattr(uitvoerder, "stall_seconds", lambda: 30.0)
    real_get = afstelling.get
    knobs = {"max_cpu_uren": 0.01, "lange_jobs_alleen_nacht": False}  # grens 36 s
    monkeypatch.setattr(afstelling, "get", lambda key: knobs.get(key, real_get(key)))
    uitvoerder._set_phase(database, ids[0], "encode")
    return database, ids[0]


@pytest.mark.asyncio
async def test_na_noodstop_niet_te_traag(tmp_path: Path,
                                         monkeypatch: pytest.MonkeyPatch) -> None:
    """Vroeger telde de bevroren tijd mee: 1 s rekenen + 3 s noodstop leek 4× zo traag."""
    database, job_id = _slow_case(tmp_path, monkeypatch)
    # 1 s beeld per 0,02 s = 50×; 600 s bron → rest ruim onder de grens van 36 s
    fake = _fake_ffmpeg(tmp_path, _progress(1_000_000, 0.02, 150))
    task = asyncio.create_task(uitvoerder._run_process(
        database, job_id, [str(fake), "-progress", "pipe:1"], tmp_path / "log.txt", 600.0))
    await asyncio.sleep(0.3)
    noodstop.set_state(database, True, "test")
    await asyncio.sleep(3.0)
    assert not task.done()
    noodstop.set_state(database, False)
    assert await asyncio.wait_for(task, timeout=30) is True


@pytest.mark.asyncio
async def test_echt_te_trage_cpu_encode_stopt_nog_steeds(
        tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    database, job_id = _slow_case(tmp_path, monkeypatch)
    # 0,1 s beeld per 0,05 s = 2×; rest ± 300 s > 36 s
    fake = _fake_ffmpeg(tmp_path, _progress(100_000, 0.05, 400))
    with pytest.raises(uitvoerder.EncodeTooSlow, match="te traag voor de CPU"):
        await asyncio.wait_for(uitvoerder._run_process(
            database, job_id, [str(fake), "-progress", "pipe:1"], tmp_path / "log.txt",
            600.0), timeout=30)


def test_foutmelding_noemt_de_oorzaak() -> None:
    text = uitvoerder.describe_process_failure("encoderproces faalde", {
        "tool": "ffmpeg", "returncode": 1,
        "lines": ["Stream #0:0 -> #0:0 (hevc (native) -> av1 (av1_qsv))",
                  "[matroska @ 0x1] Invalid data found when processing input",
                  "Conversion failed!"]})
    assert text.startswith("encoderproces faalde (ffmpeg, exitcode 1)")
    assert "de bron is beschadigd of onleesbaar" in text
    assert "Conversion failed!" in text
    full = uitvoerder.describe_process_failure("encoderproces faalde", {
        "tool": "ffmpeg", "returncode": 1,
        "lines": ["av_interleaved_write_frame(): No space left on device"]})
    assert "geen ruimte meer op de cacheschijf" in full
    gpu = uitvoerder.describe_process_failure("encoderproces faalde", {
        "tool": "ffmpeg", "returncode": 1,
        "lines": ["[AVHWDeviceContext @ 0x1] Error creating a MFX session: -9."]})
    assert "de GPU weigerde" in gpu
    crash = uitvoerder.describe_process_failure("encoderproces faalde", {
        "tool": "ffmpeg", "returncode": -int(signal.SIGSEGV), "lines": []})
    assert "crashte" in crash
    # alleen de bannerregel noemt qsv: geen GPU-oorzaak verzinnen
    plain = uitvoerder.describe_process_failure("encoderproces faalde", {
        "tool": "ffmpeg", "returncode": 1,
        "lines": ["Stream #0:0 -> #0:0 (hevc (native) -> av1 (av1_qsv))"]})
    assert "GPU" not in plain


@pytest.mark.asyncio
async def test_mislukte_stap_krijgt_specifieke_fout(tmp_path: Path) -> None:
    database, _configured, ids = _planner_case(tmp_path)
    fake = _fake_ffmpeg(tmp_path, "print('Unknown encoder \\'libsvtav1\\'', flush=True)\n"
                                  "sys.exit(8)\n")
    assert await uitvoerder._run_process(database, ids[0], [str(fake)], tmp_path / "l",
                                         1.0) is False
    failure = uitvoerder._step_failed(database, ids[0], "encoderproces faalde")
    assert isinstance(failure, uitvoerder.ExecutionError)
    assert "exitcode 8" in str(failure) and "kent een optie niet" in str(failure)


@pytest.mark.asyncio
async def test_onverwachte_fout_laat_geen_cache_staan(
        tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    database, configured, ids = _planner_case(tmp_path)
    job_id = ids[0]
    database.execute("UPDATE job SET status='bezig',pogingen=1 WHERE id=?", (job_id,))

    async def boom(*_args: Any, **_kwargs: Any) -> Any:
        (configured.cache_root / str(job_id) / "resultaat.mkv.deel").write_bytes(b"x" * 1024)
        raise KeyError("onverwacht")

    monkeypatch.setattr(uitvoerder, "capture_comparison_frames", boom)
    ok = await uitvoerder.run_job(database, configured, job_id, storage_validator=lambda: None,
                                  already_claimed=True)
    assert ok is False
    assert not (configured.cache_root / str(job_id)).exists()
    row = database.fetch_one("SELECT status,fout FROM job WHERE id=?", (job_id,))
    assert row["status"] == "mislukt"
    assert "onverwachte fout (KeyError)" in row["fout"] and "origineel" in row["fout"]


@pytest.mark.asyncio
async def test_onverwachte_fout_tijdens_vervangen_laat_herstel_beslissen(
        tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """Direct-modus, fase 'vervangen': het vervangherstel beheert cache en bron. Dan niets
    weghalen en niet beweren dat het origineel onaangeraakt is."""
    from dataclasses import replace

    database, configured, ids = _planner_case(tmp_path)
    configured = replace(configured, replacement_mode="direct")
    job_id = ids[0]
    database.execute("UPDATE job SET status='bezig',pogingen=1 WHERE id=?", (job_id,))

    async def boom(*_args: Any, **_kwargs: Any) -> Any:
        (configured.cache_root / str(job_id) / "resultaat.mkv").write_bytes(b"x" * 1024)
        uitvoerder._set_phase(database, job_id, "vervangen")
        raise KeyError("onverwacht")

    monkeypatch.setattr(uitvoerder, "capture_comparison_frames", boom)
    ok = await uitvoerder.run_job(database, configured, job_id, storage_validator=lambda: None,
                                  already_claimed=True)
    assert ok is False
    assert (configured.cache_root / str(job_id) / "resultaat.mkv").is_file()
    row = database.fetch_one("SELECT status,fout FROM job WHERE id=?", (job_id,))
    assert row["status"] == "mislukt"
    assert "vervangherstel" in row["fout"] and "niet aangeraakt" not in row["fout"]


@pytest.mark.asyncio
async def test_achtergebleven_cache_blokkeert_volgende_poging_niet(
        tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """Vroeger: 'File exists' op de cachemap en de job lukte nooit meer."""
    database, configured, ids = _planner_case(tmp_path)
    job_id = ids[0]
    leftover = configured.cache_root / str(job_id)
    leftover.mkdir()
    (leftover / "resultaat.mkv.deel").write_bytes(b"oud")
    database.execute("UPDATE job SET status='bezig',pogingen=1 WHERE id=?", (job_id,))
    seen: dict[str, bool] = {}

    async def stop_after_check(*_args: Any, **_kwargs: Any) -> Any:
        seen["old_gone"] = not (leftover / "resultaat.mkv.deel").exists()
        raise uitvoerder.ExecutionError("test: hier stoppen")

    monkeypatch.setattr(uitvoerder, "capture_comparison_frames", stop_after_check)
    await uitvoerder.run_job(database, configured, job_id, storage_validator=lambda: None,
                             already_claimed=True)
    assert seen == {"old_gone": True}
    logs = " ".join(str(r[0]) for r in database.fetch_all(
        "SELECT tekst FROM logregel WHERE job_id=?", (job_id,)))
    assert "Achtergebleven cache" in logs


@pytest.mark.asyncio
async def test_worker_ruimt_cache_op_bij_fout_buiten_uitvoerder(
        tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    database, configured, ids = _planner_case(tmp_path)
    job_id = ids[0]
    (configured.cache_root / str(job_id)).mkdir()
    (configured.cache_root / str(job_id) / "groot.deel").write_bytes(b"x" * 2048)
    planner = Planner(database, configured, storage_validator=lambda: None,
                      machine_guard=lambda _lane: (True, None))

    async def crash(*_args: Any, **_kwargs: Any) -> bool:
        raise RuntimeError("buiten de uitvoerder")

    monkeypatch.setattr("app.planner.run_job", crash)
    assert await planner._worker(job_id, "cpu-1") is False
    assert not (configured.cache_root / str(job_id)).exists()
    row = database.fetch_one("SELECT status,fout FROM job WHERE id=?", (job_id,))
    assert row["status"] == "mislukt" and "RuntimeError" in row["fout"]


def test_onderhoud_ruimt_cache_van_afgeronde_jobs_op(tmp_path: Path) -> None:
    database, configured, ids = _planner_case(tmp_path)
    done, running = ids[0], ids[1]
    for job_id in (done, running, 9999):
        (configured.cache_root / str(job_id)).mkdir()
        (configured.cache_root / str(job_id) / "x.deel").write_bytes(b"x")
    (configured.work_root / str(done)).mkdir()
    database.execute("UPDATE job SET status='mislukt' WHERE id=?", (done,))
    database.execute("UPDATE job SET status='bezig' WHERE id=?", (running,))
    assert sweep_finished_job_cache(database, configured) == [done]
    assert not (configured.cache_root / str(done)).exists()
    assert not (configured.work_root / str(done)).exists()
    assert (configured.cache_root / str(running)).exists()  # loopt nog
    assert (configured.cache_root / "9999").exists()  # onbekend: nooit weg
