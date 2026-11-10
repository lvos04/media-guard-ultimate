"""Rapport 6: noodstop in de engine (N1), vastlopers (N2), ZFS-cache in de meting."""

from __future__ import annotations

import asyncio
import json
from pathlib import Path
from typing import Any

import psutil
import pytest
from mg_helpers import HEADERS
from test_planner import _planner_case

from app import noodstop, uitvoerder, vastlopers
from app.cpuconfine import partition
from app.db import Database
from app.media.beslissen import Besluit
from app.planner import Planner, recover_orphaned_jobs
from app.systeem import zfs_arc_bytes


@pytest.fixture(autouse=True)
def _reset_noodstop() -> Any:
    noodstop._reset_for_tests()
    yield
    noodstop._reset_for_tests()


def _fake_ffmpeg(tmp_path: Path, body: str) -> Path:
    path = tmp_path / "bin" / "ffmpeg"
    path.parent.mkdir(exist_ok=True)
    path.write_text("#!/usr/bin/env python3\nimport sys, time\n" + body, encoding="utf-8")
    path.chmod(0o755)
    return path


PROGRESS = (
    "def p(i):\n"
    "    print(f'out_time_ms={i*100000}', flush=True)\n"
    "    print('progress=continue', flush=True)\n"
)


def _fast(monkeypatch: pytest.MonkeyPatch, stall: float = 1.0) -> None:
    monkeypatch.setattr(uitvoerder, "PROGRESS_WRITE_SECONDS", 0.05)
    monkeypatch.setattr(uitvoerder, "stall_seconds", lambda: stall)


# ── N1: noodstop ─────────────────────────────────────────────────────────────────


@pytest.mark.usefixtures("ruim_werkgeheugen")
def test_planner_pakt_niets_op_tijdens_noodstop(tmp_path: Path) -> None:
    database, configured, _ids = _planner_case(tmp_path)
    planner = Planner(database, configured, storage_validator=lambda: None,
                      machine_guard=lambda _lane: (True, None))
    noodstop.set_state(database, True, "test")
    job_id, reason = planner.claim_next("cpu", "cpu-1")
    assert job_id is None and reason and reason.startswith("noodstop actief")
    noodstop.set_state(database, False)
    job_id, _reason = planner.claim_next("cpu", "cpu-1")
    assert job_id is not None


def test_noodstop_blijft_bewaard_na_herstart(tmp_path: Path) -> None:
    database, configured, _ids = _planner_case(tmp_path)
    noodstop.set_state(database, True, "bewaard")
    noodstop._reset_for_tests()  # nieuwe engine-levenscyclus
    planner = Planner(database, configured, storage_validator=lambda: None,
                      machine_guard=lambda _lane: (True, None))
    assert noodstop.active()
    assert planner.claim_next("gpu", "gpu-1")[0] is None


@pytest.mark.asyncio
async def test_lopende_encode_bevriest_en_hervat_zonder_vastloperfout(
        tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    database, _configured, ids = _planner_case(tmp_path)
    _fast(monkeypatch, stall=1.0)
    fake = _fake_ffmpeg(
        tmp_path, PROGRESS + "for i in range(40):\n    p(i)\n    time.sleep(0.05)\n")
    task = asyncio.create_task(uitvoerder._run_process(
        database, ids[0], [str(fake), "-progress", "pipe:1"], tmp_path / "log.txt", 10.0))
    await asyncio.sleep(0.5)
    noodstop.set_state(database, True, "test")
    await asyncio.sleep(2.5)  # ruim langer dan de vastlopergrens van 1 s
    pid = int(database.fetch_one("SELECT proces_pid FROM job WHERE id=?", (ids[0],))[0])
    assert psutil.Process(pid).status() == psutil.STATUS_STOPPED
    assert database.fetch_one("SELECT noodstop_gepauzeerd FROM job WHERE id=?",
                              (ids[0],))[0] == 1
    assert not task.done()
    noodstop.set_state(database, False)
    assert await asyncio.wait_for(task, timeout=20) is True
    assert database.fetch_one("SELECT noodstop_gepauzeerd FROM job WHERE id=?",
                              (ids[0],))[0] == 0


@pytest.mark.asyncio
async def test_geen_nieuwe_stap_tijdens_noodstop(tmp_path: Path) -> None:
    database, _configured, ids = _planner_case(tmp_path)
    noodstop.set_state(database, True, "test")
    with pytest.raises(uitvoerder.ProcessInterrupted):
        await uitvoerder._run_process(database, ids[0], ["/bin/true"], tmp_path / "l", 1.0)


def test_herstart_tijdens_noodstop_kost_geen_poging(tmp_path: Path) -> None:
    database, configured, ids = _planner_case(tmp_path)
    database.execute("UPDATE job SET status='bezig',pogingen=1,worker_id='cpu-1' WHERE id=?",
                     (ids[0],))
    database.execute("INSERT INTO job_poging(job_id,poging,status,gestart_op) "
                     "VALUES (?,1,'bezig','2026-11-05T14:00:00+00:00')", (ids[0],))
    noodstop.set_state(database, True, "test")
    recover_orphaned_jobs(database, configured)
    row = database.fetch_one("SELECT status,pogingen,fout FROM job WHERE id=?", (ids[0],))
    assert row["status"] == "wacht" and int(row["pogingen"]) == 0
    assert "noodstop" in str(row["fout"])
    assert database.fetch_one("SELECT COUNT(*) FROM job_poging WHERE job_id=? AND "
                              "foutsoort='ServiceStop'", (ids[0],))[0] == 0


def test_bridge_noodstop_endpoint(mg_client: Any) -> None:
    client, database, _configured = mg_client
    response = client.put("/api/mediaguard/v1/settings/emergency-stop",
                          json={"active": True, "reason": "test"}, headers=HEADERS)
    assert response.status_code == 200 and response.json()["active"] is True
    assert client.get("/api/mediaguard/v1/settings/emergency-stop",
                      headers=HEADERS).json()["active"] is True
    assert database.fetch_one("SELECT actief FROM noodstop WHERE id=1")[0] == 1
    client.put("/api/mediaguard/v1/settings/emergency-stop", json={"active": False},
               headers=HEADERS)
    assert not noodstop.active()


# ── N2: vastlopers ───────────────────────────────────────────────────────────────


def test_cpu_plekken_krijgen_eigen_kernen() -> None:
    cpus = list(range(7, 28))  # 21 kernen
    first, second = partition(cpus, 0, 2), partition(cpus, 1, 2)
    assert not set(first) & set(second)
    assert sorted(first + second) == cpus
    assert partition(cpus, 0, 1) == cpus


@pytest.mark.asyncio
async def test_rekenend_proces_zonder_uitvoer_krijgt_extra_tijd(
        tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    database, _configured, ids = _planner_case(tmp_path)
    _fast(monkeypatch, stall=1.0)
    fake = _fake_ffmpeg(tmp_path, PROGRESS + (
        "p(1)\n"
        "end = time.time() + 1.6\n"
        "while time.time() < end:\n    pass\n"
        "for i in range(2, 6):\n    p(i)\n    time.sleep(0.05)\n"))
    assert await uitvoerder._run_process(
        database, ids[0], [str(fake), "-progress", "pipe:1"], tmp_path / "log.txt", 10.0)
    logs = " ".join(str(r[0]) for r in database.fetch_all(
        "SELECT tekst FROM logregel WHERE job_id=?", (ids[0],)))
    assert "rekent nog" in logs


@pytest.mark.asyncio
async def test_echte_vastloper_met_diagnose(tmp_path: Path,
                                            monkeypatch: pytest.MonkeyPatch) -> None:
    database, _configured, ids = _planner_case(tmp_path)
    _fast(monkeypatch, stall=1.0)
    fake = _fake_ffmpeg(tmp_path, PROGRESS + (
        "print('Stream #0:0: Video: hevc', flush=True)\np(1)\ntime.sleep(30)\n"))
    with pytest.raises(uitvoerder.EncodeStalled) as caught:
        await uitvoerder._run_process(database, ids[0], [str(fake), "-progress", "pipe:1"],
                                      tmp_path / "log.txt", 10.0)
    assert "Stream #0:0" in caught.value.diagnose
    assert "kernen bezig" in caught.value.diagnose


def _stall(database: Database, job_id: int, decision: Besluit) -> None:
    exc = uitvoerder.EncodeStalled("Geen voortgang", percent=2.0, diagnose="d")
    uitvoerder._handle_stall(database, job_id, decision, exc)


def test_tweede_vastloper_wisselt_route_derde_geeft_op(tmp_path: Path) -> None:
    database, _configured, ids = _planner_case(tmp_path)
    job_id = ids[0]
    database.execute("UPDATE job SET baan='cpu',status='bezig',worker_id='cpu-2' WHERE id=?",
                     (job_id,))
    database.execute("UPDATE worker_config SET aantal=0 WHERE baan='gpu'")  # geen GPU
    decision = Besluit(actie="hercoderen", baan="cpu", encoder="libsvtav1", dolby_vision=True)
    _stall(database, job_id, decision)
    assert database.fetch_one("SELECT status FROM job WHERE id=?", (job_id,))[0] == "mislukt"
    database.execute("UPDATE job SET status='bezig' WHERE id=?", (job_id,))
    _stall(database, job_id, decision)
    row = database.fetch_one("SELECT status,route_wissel,mg_inst FROM job WHERE id=?",
                             (job_id,))
    assert row["status"] == "wacht" and int(row["route_wissel"]) == 1
    assert json.loads(row["mg_inst"])["preset_2160"] == vastlopers.FAST_CPU_PRESET
    database.execute("UPDATE job SET status='bezig' WHERE id=?", (job_id,))
    _stall(database, job_id, decision)
    row = database.fetch_one("SELECT status,foutsoort FROM job WHERE id=?", (job_id,))
    assert row["status"] == "mislukt" and row["foutsoort"] == vastlopers.GIVE_UP_TYPE


def test_sdr_bron_gaat_na_tweede_vastloper_naar_gpu(tmp_path: Path) -> None:
    database, _configured, ids = _planner_case(tmp_path)
    job_id = ids[0]
    database.execute("UPDATE job SET baan='cpu',status='bezig' WHERE id=?", (job_id,))
    database.execute("UPDATE worker_config SET aantal=2,gepauzeerd=0 WHERE baan='gpu'")
    decision = Besluit(actie="hercoderen", baan="cpu", encoder="libsvtav1")
    _stall(database, job_id, decision)
    database.execute("UPDATE job SET status='bezig' WHERE id=?", (job_id,))
    _stall(database, job_id, decision)
    row = database.fetch_one("SELECT status,baan FROM job WHERE id=?", (job_id,))
    assert row["status"] == "wacht" and row["baan"] == "gpu"


def test_baan_gaat_terug_naar_een_plek_na_drie_vastlopers(tmp_path: Path) -> None:
    database, _configured, ids = _planner_case(tmp_path)
    assert vastlopers.lane_cap(database, "cpu", 2) == 2
    for _ in range(3):
        vastlopers.record(database, ids[0], percent=1.0, diagnose="d")
    assert vastlopers.lane_cap(database, "cpu", 2) == 1
    assert "één plek" in (vastlopers.lane_notice(database, "cpu") or "")


# ── Geheugen: ZFS-cache apart ───────────────────────────────────────────────────


def test_zfs_cache_wordt_gelezen(tmp_path: Path) -> None:
    stats = tmp_path / "arcstats"
    stats.write_text("13 1 0x01 123 33456 1 2\nname type data\nhits 4 100\n"
                     "size 4 17179869184\n", encoding="utf-8")
    assert zfs_arc_bytes(stats) == 17179869184
    assert zfs_arc_bytes(tmp_path / "bestaat-niet") is None
