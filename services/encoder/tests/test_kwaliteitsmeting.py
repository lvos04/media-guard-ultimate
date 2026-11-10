"""Verbeterpunt 29: kwaliteitscontrole.

- Mislukken alle VMAF-metingen, dan gaat het resultaat niet stil door: eerst SSIM, lukt
  ook dat niet, dan blijft het origineel staan en staat de reden bij de job.
- Elke meting heeft een time-out en draait binnen de CPU-grens (zelfde kernen als de
  encode)."""

from __future__ import annotations

import asyncio
import time
from pathlib import Path
from typing import Any

import psutil
import pytest
from test_uitvoerder import _case

from app import kwaliteit, uitvoerder
from app.verificatie import _duration_ok
from app.vervanging import file_sha256


def _db_with_vmaf(tmp_path: Path) -> Any:
    database, configured, _source, job_id = _case(tmp_path, "sdr-meertalig.mkv")
    database.execute("UPDATE functie SET ingeschakeld=1 WHERE sleutel='vmaf'")
    return database, configured, job_id


@pytest.mark.asyncio
async def test_alle_vmaf_metingen_mislukt_dan_ssim(tmp_path: Path,
                                                    monkeypatch: pytest.MonkeyPatch) -> None:
    database, configured, job_id = _db_with_vmaf(tmp_path)

    async def has_vmaf() -> bool:
        return True

    async def vmaf_fails(*_a: Any, **_k: Any) -> tuple[float | None, str | None]:
        return None, "VMAF-meting faalde (exitcode 1): Invalid argument"

    async def ssim_ok(*_a: Any, **_k: Any) -> tuple[float | None, str | None]:
        return 0.991, None

    async def still(*_a: Any, **_k: Any) -> bool:
        return False

    monkeypatch.setattr(kwaliteit, "vmaf_available", has_vmaf)
    monkeypatch.setattr(kwaliteit, "vmaf_sample", vmaf_fails)
    monkeypatch.setattr(kwaliteit, "ssim_sample", ssim_ok)
    monkeypatch.setattr(kwaliteit, "extract_still", still)
    result = await kwaliteit.measure_quality(database, configured, job_id, Path("/a"),
                                             Path("/b"), 600.0)
    assert result["metric"] == "ssim" and result["score"] == 0.991
    assert "SSIM gebruikt" in result["fallback"] and not result.get("unmeasured")


@pytest.mark.asyncio
async def test_niets_te_meten_is_geen_goedkeuring(tmp_path: Path,
                                                  monkeypatch: pytest.MonkeyPatch) -> None:
    database, configured, job_id = _db_with_vmaf(tmp_path)

    async def has_vmaf() -> bool:
        return True

    async def fails(*_a: Any, **_k: Any) -> tuple[float | None, str | None]:
        return None, "time-out: meting duurde langer dan 15 min"

    async def still(*_a: Any, **_k: Any) -> bool:
        return False

    monkeypatch.setattr(kwaliteit, "vmaf_available", has_vmaf)
    monkeypatch.setattr(kwaliteit, "vmaf_sample", fails)
    monkeypatch.setattr(kwaliteit, "ssim_sample", fails)
    monkeypatch.setattr(kwaliteit, "extract_still", still)
    result = await kwaliteit.measure_quality(database, configured, job_id, Path("/a"),
                                             Path("/b"), 600.0)
    assert result["unmeasured"] is True and result["score"] is None
    assert result["attempted"] == 3 and result["measured"] == 0
    assert any("time-out" in p for p in result["problems"])


@pytest.mark.asyncio
async def test_job_zonder_meting_plaatst_niets(tmp_path: Path,
                                               monkeypatch: pytest.MonkeyPatch) -> None:
    """Echte encode; de meting faalt overal. Vroeger: quality_rejection(None) = goed."""
    database, configured, source, job_id = _case(tmp_path, "sdr-meertalig.mkv")
    before = file_sha256(source)

    async def unmeasured(*_a: Any, **_k: Any) -> dict[str, Any]:
        return {"metric": None, "score": None, "samples": [], "stills": [], "attempted": 3,
                "measured": 0, "unmeasured": True,
                "problems": ["steekproef 1: VMAF-meting faalde (exitcode 1)"]}

    monkeypatch.setattr(uitvoerder, "measure_quality", unmeasured)
    assert not await uitvoerder.run_job(database, configured, job_id,
                                        storage_validator=lambda: None)
    row = database.fetch_one("SELECT status,fout,foutsoort FROM job WHERE id=?", (job_id,))
    assert row["status"] == "mislukt" and row["foutsoort"] == "QualityUnmeasured"
    assert "Kwaliteitsmeting mislukt" in row["fout"] and "origineel blijft staan" in row["fout"]
    assert file_sha256(source) == before  # origineel onaangeroerd
    assert not (configured.cache_root / str(job_id)).exists()


@pytest.mark.asyncio
async def test_meting_heeft_een_time_out() -> None:
    started = time.monotonic()
    code, _out, problem = await kwaliteit.run_measure(
        ["sleep", "30"], timeout=1.0)
    assert code is None and problem and problem.startswith("time-out")
    assert time.monotonic() - started < 10
    # het proces is echt weg (geen achtergebleven meting die CPU blijft gebruiken)
    assert not [p for p in psutil.process_iter(["cmdline"])
                if (p.info.get("cmdline") or [])[:2] == ["sleep", "30"]]


@pytest.mark.asyncio
async def test_meting_houdt_zich_aan_de_cpu_grens(monkeypatch: pytest.MonkeyPatch) -> None:
    if not kwaliteit.shutil.which("taskset") or len(psutil.Process().cpu_affinity() or []) < 2:
        pytest.skip("taskset of meerdere kernen niet beschikbaar")
    import app.cpuconfine as cpuconfine

    monkeypatch.setattr(cpuconfine, "confine_prefix",
                        lambda _percent, _part=None: ["taskset", "-c", "1"])
    prefix = kwaliteit.measure_prefix(object(), 1)
    assert prefix[:3] == ["taskset", "-c", "1"]
    code, out, problem = await kwaliteit.run_measure(
        ["python3", "-c", "import os; print(sorted(os.sched_getaffinity(0)))"], prefix=prefix)
    assert code == 0 and problem is None and out.decode().strip() == "[1]"


@pytest.mark.asyncio
async def test_meting_stopt_bij_service_stop() -> None:
    uitvoerder.request_shutdown()
    try:
        with pytest.raises(uitvoerder.ProcessInterrupted):
            await asyncio.wait_for(kwaliteit.run_measure(["sleep", "30"]), timeout=10)
    finally:
        uitvoerder.reset_shutdown()


def test_onbekende_speelduur_van_resultaat_keurt_af() -> None:
    assert _duration_ok(3600.0, 3600.4)
    assert not _duration_ok(3600.0, 0)  # vroeger: onbekend = goed
    assert not _duration_ok(3600.0, None)
    assert _duration_ok(0, 3600.0)  # bron onbekend: niets om mee te vergelijken


@pytest.mark.asyncio
async def test_noodstop_bevriest_meting_zonder_time_out(tmp_path: Path) -> None:
    """Tijdens een noodstop staat de meting stil; die tijd telt niet mee voor de time-out."""
    from app import noodstop

    database, _configured, _job_id = _db_with_vmaf(tmp_path)
    noodstop._reset_for_tests()
    try:
        task = asyncio.create_task(kwaliteit.run_measure(
            ["python3", "-c", "import time; time.sleep(1.5); print('klaar')"], timeout=2.5))
        await asyncio.sleep(0.3)
        noodstop.set_state(database, True, "test")
        await asyncio.sleep(3.5)  # langer dan de time-out, maar bevroren
        assert not task.done()
        noodstop.set_state(database, False)
        code, out, problem = await asyncio.wait_for(task, timeout=20)
        assert code == 0 and problem is None and b"klaar" in out
    finally:
        noodstop._reset_for_tests()


def test_proefencodes_houden_zich_aan_de_cpu_grens(monkeypatch: pytest.MonkeyPatch) -> None:
    """Ook de handmatige proefmatrix (/trials) meet en encodeert binnen de CPU-grens."""
    from app.mediaguard import trials

    seen: list[list[str]] = []

    class Done:
        returncode, stdout, stderr = 0, "", "SSIM Y:0.9 All:0.98 (17)\nPSNR average:44.1"

    def fake_run(argv: list[str], **_kw: Any) -> Any:
        seen.append(list(argv))
        return Done()

    monkeypatch.setattr(trials.subprocess, "run", fake_run)
    limits = ["taskset", "-c", "2-3", "nice", "-n", "10"]
    metrics = trials.measure_quality(Path("a.mkv"), Path("b.mkv"), vmaf=False, prefix=limits)
    assert metrics["ssim"] == 0.98
    assert trials._reference("bron.mkv", trials.TrialSample(start=10, duration=5),
                             Path("/nonexistent/ref.mkv"), limits) is False
    assert seen and all(argv[:6] == limits for argv in seen)
