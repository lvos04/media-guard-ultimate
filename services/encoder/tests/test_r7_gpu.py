"""Rapport 7: meer werk naar de Arc (HDR10+, DV 8.1 → HDR10), lookahead + VMAF-sturing,
GPU-plekkenmeting, tweede CPU-plek pas na bewijs, HDR-metadata uit de GPU-runtime."""

from __future__ import annotations

import asyncio
import json
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import pytest
from conftest import pad
from test_planner import _planner_case

from app import afstelling, cpuplek, gpubench, uitvoerder, verificatie
from app.media import commandos, gpuvarianten
from app.media.beslissen import STANDAARD, beslis
from app.media.probe import probe
from app.mediaguard.policy import MediaGuardPolicy, to_profile_inst

LAAG = {"drempel_2160": 500, "drempel_1080": 500, "drempel_720": 300}


def _inst(**policy: Any) -> dict[str, Any]:
    mg = MediaGuardPolicy.model_validate({"encoder": "auto", **policy})
    return {**STANDAARD, **to_profile_inst(mg, gpu_lane_available=True), **LAAG}


def _hdr() -> dict[str, Any]:
    info = probe(pad("hdr10.mkv"))
    assert info is not None and info["is_hdr10"]
    return info


def test_dv81_naar_hdr10_alleen_met_keuze_en_alleen_81() -> None:
    dv81 = {**_hdr(), "dv_profile": 8, "dv_compat": 1}
    base = {"dv_mode": "auto_after_verify", "dv_allowed_profiles": ["8.1", "7_mel"]}
    keep = beslis(dv81, _inst(**base))
    assert (keep.baan, keep.encoder, keep.dolby_vision) == ("cpu", "libsvtav1", True)
    gpu = beslis(dv81, _inst(**base, dv81_to_hdr10=True))
    assert (gpu.baan, gpu.encoder, gpu.dv_naar_hdr10, gpu.dolby_vision) == (
        "gpu", "av1_qsv", True, False)
    assert gpu.herstel_hdr_na and "Dolby Vision vervalt" in gpu.reden
    command = commandos.encode_gpu(dv81, gpu, _inst(), "in.mkv", "uit.mkv")
    assert command[command.index("-bsf:v") + 1] == "dovi_rpu=strip=1"
    # profiel 7 nooit via deze route
    dv7 = {**_hdr(), "dv_profile": 7, "dv_el_type": "MEL"}
    assert beslis(dv7, _inst(**base, dv81_to_hdr10=True)).baan != "gpu"


def test_verificatie_eist_geen_dv_bij_dv81_naar_hdr10(monkeypatch: pytest.MonkeyPatch,
                                                      tmp_path: Path) -> None:
    source = {**_hdr(), "dv_profile": 8, "dv_compat": 1, "dv_rpu_frames": True}
    decision = beslis(source, _inst(dv_mode="auto_after_verify",
                                    dv_allowed_profiles=["8.1"], dv81_to_hdr10=True))
    result_file = tmp_path / "uit.mkv"
    result_file.write_bytes(b"x")
    with_dv = {**source, "size": 1}
    monkeypatch.setattr(verificatie, "probe", lambda _p: with_dv)
    outcome = verificatie.verify_result(source, result_file, 10, decision)
    assert not outcome.passed and "Dolby Vision" in outcome.reason


def test_lookahead_en_bitratefactor_op_de_gpu() -> None:
    info = probe(pad("sdr-meertalig.mkv"))
    assert info is not None
    b = beslis(info, _inst())
    cmd = commandos.encode_gpu(info, b, _inst(), "in.mkv", "uit.mkv", lookahead=True)
    assert cmd[cmd.index("-look_ahead_depth") + 1] == "40" and "-extbrc" in cmd
    import re

    frames = int(re.search(r"extra_hw_frames=(\d+)", " ".join(cmd)).group(1))  # type: ignore[union-attr]
    assert frames >= 48  # lookahead heeft minstens diepte + 8 frames nodig
    vaapi = next(v for v in gpuvarianten.VARIANTEN if gpuvarianten.is_vaapi(v))
    assert "-look_ahead_depth" not in commandos.encode_gpu(
        info, b, _inst(), "in.mkv", "uit.mkv", variant=vaapi, lookahead=True)
    rate = commandos.encode_gpu(info, b, _inst(), "in.mkv", "uit.mkv", lookahead=False)
    base = int(rate[rate.index("-b:v") + 1].rstrip("k"))
    up = commandos.encode_gpu(info, b, _inst(), "in.mkv", "uit.mkv", rate_factor=1.5)
    assert int(up[up.index("-b:v") + 1].rstrip("k")) == int(base * 1.5)
    down = commandos.encode_gpu(info, b, _inst(), "in.mkv", "uit.mkv", rate_factor=0.1)
    assert int(down[down.index("-b:v") + 1].rstrip("k")) == commandos.GPU_BITRATE_FLOOR["1080"]
    huge = commandos.encode_gpu(info, b, _inst(), "in.mkv", "uit.mkv", rate_factor=50)
    assert int(huge[huge.index("-b:v") + 1].rstrip("k")) == commandos.GPU_BITRATE_CEILING[
        "1080"]


def test_vmaf_sturing_factor() -> None:
    assert uitvoerder.steer_factor(1.0, 95.5, 95.0) is None
    assert uitvoerder.steer_factor(1.0, 92.0, 95.0) == pytest.approx(1.35)
    assert uitvoerder.steer_factor(1.0, 80.0, 95.0) == pytest.approx(1.6)
    assert uitvoerder.steer_factor(1.0, 98.0, 95.0) == pytest.approx(0.75)
    assert uitvoerder.steer_factor(1.0, 99.5, 95.0) == pytest.approx(0.6)
    assert uitvoerder.steer_factor(0.4, 99.9, 95.0) is None  # al op de ondergrens


def test_proefstukken_sturen_de_bitrate(monkeypatch: pytest.MonkeyPatch,
                                        tmp_path: Path) -> None:
    from app import kwaliteit

    database, _configured, job_ids = _planner_case(tmp_path)
    calls: list[float] = []

    async def fake_run(_db, _job, arguments, _log, _duration):  # type: ignore[no-untyped-def]
        Path(arguments[-1]).write_bytes(b"x" * 300_000)
        return True

    scores = iter([88.0, 93.0, 95.5])

    async def fake_vmaf(*_a, **_k):  # type: ignore[no-untyped-def]
        return next(scores)

    async def yes() -> bool:
        return True

    monkeypatch.setattr(uitvoerder, "_run_process", fake_run)
    monkeypatch.setattr(kwaliteit, "compute_vmaf", fake_vmaf)
    monkeypatch.setattr(kwaliteit, "vmaf_available", yes)

    def build(factor: float) -> list[str]:
        calls.append(factor)
        return ["ffmpeg", "-i", "bron.mkv", "-b:v", f"{int(3000 * factor)}k", "uit.deel"]

    skip, factor, reroute = asyncio.run(uitvoerder._sample_phase(
        database, job_ids[0], build, tmp_path, tmp_path / "bron.mkv", 104_000_000, 6000.0,
        15.0, tmp_path / "log", steer=True))
    assert skip is None
    assert reroute is None
    assert calls == [1.0, 1.0, 1.6, pytest.approx(1.6 * 1.35)]
    assert factor == pytest.approx(1.6 * 1.35)
    assert uitvoerder.JOB_PHASE_DETAIL[job_ids[0]].startswith("VMAF meten 3/3")


def test_gpu_plekkenmeting_kiest_kleinste_snelle_aantal(monkeypatch: pytest.MonkeyPatch,
                                                        tmp_path: Path) -> None:
    database, configured, _ids = _planner_case(tmp_path)
    assert gpubench.choose([{"plekken": 1, "ok": True, "fps": 100},
                            {"plekken": 2, "ok": True, "fps": 180},
                            {"plekken": 4, "ok": True, "fps": 185}]) == 2
    assert gpubench.choose([{"plekken": 1, "ok": False}]) is None

    async def fake_parallel(_cmd: list[str], count: int) -> dict[str, Any]:
        return {"plekken": count, "ok": True, "fps": {1: 90.0, 2: 170.0, 4: 172.0}[count],
                "seconden": 5.0, "vram_mb": 300 * count}

    monkeypatch.setattr(gpubench, "_run_parallel", fake_parallel)
    result = asyncio.run(gpubench.run(database, configured, configured=4, ffmpeg_version="8.1"))
    assert result["beste"] == 2 and "2 GPU-plek" in result["uitleg_nl"]
    assert gpubench.lane_cap(database, 4) == 2
    assert gpubench.lane_cap(database, 1) == 1
    assert not gpubench.due(database, configured, True, False, "8.1")
    assert gpubench.due(database, configured, True, False, "8.2")  # nieuwe ffmpeg: opnieuw
    assert not gpubench.due(database, configured, True, True, "8.2")  # baan bezig: wachten
    afstelling._CACHE["gpu_plekken_auto"] = False
    try:
        assert gpubench.lane_cap(database, 4) == 4
    finally:
        afstelling._CACHE.pop("gpu_plekken_auto", None)


def test_tweede_cpu_plek_pas_na_twintig_geslaagde_encodes(tmp_path: Path) -> None:
    database, _configured, job_ids = _planner_case(tmp_path)
    assert cpuplek.lane_cap(database, 1) == 1
    assert cpuplek.lane_cap(database, 2) == 1
    now = datetime.now(UTC).isoformat()
    template = database.fetch_one("SELECT * FROM job WHERE id=?", (job_ids[0],))
    assert template is not None
    for _ in range(cpuplek.PROOF_COUNT):
        database.execute(
            "INSERT INTO job(besluit_id,bestand_id,baan,status,prioriteit,klaar_op) "
            "VALUES (?,?,'cpu','klaar',0,?)",
            (template["besluit_id"], template["bestand_id"], now))
    assert cpuplek.lane_cap(database, 2) == 2
    database.execute(
        "INSERT INTO vastloper(job_id,bestand_id,baan,worker_id,encoder,percent,tijd,diagnose) "
        "VALUES (?,?,'cpu','cpu-2','libsvtav1',5,?,'')",
        (job_ids[0], template["bestand_id"], now))
    status = cpuplek.status(database, 2)
    assert status["plekken"] == 1 and "24 uur" in status["uitleg_nl"]


def test_hdr_metadata_uit_de_gpu_runtime(monkeypatch: pytest.MonkeyPatch,
                                         tmp_path: Path) -> None:
    source = _hdr()
    monkeypatch.setattr(uitvoerder, "probe", lambda _p: dict(source))
    assert uitvoerder.hdr_metadata_native(source, tmp_path / "x.mkv")
    monkeypatch.setattr(uitvoerder, "probe",
                        lambda _p: {**source, "mastering_display": None})
    assert not uitvoerder.hdr_metadata_native(source, tmp_path / "x.mkv")
    assert not uitvoerder.hdr_metadata_native({**source, "is_hdr10": False}, tmp_path / "x")


def test_nieuwe_afstelling_is_instelbaar(tmp_path: Path) -> None:
    database, _configured, _ids = _planner_case(tmp_path)
    keys = {item["sleutel"] for item in afstelling.view()["items"]}
    assert {"gpu_lookahead", "gpu_vmaf_sturing", "vmaf_doel", "gpu_plekken_auto",
            "cpu_tweede_plek_proef"} <= keys
    afstelling.update(database, {"vmaf_doel": 120})
    assert afstelling.get("vmaf_doel") == 99
    afstelling.update(database, {"vmaf_doel": None})
    assert json.dumps(afstelling.get("vmaf_doel")) == "95.0"
