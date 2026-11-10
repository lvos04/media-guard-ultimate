"""Rapport 3: 4K-doorvoer, kwaliteitsondergrens, herstartlus, joblog en prognose."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from pathlib import Path

from conftest import pad
from test_gpu_hdr10 import _inst

from app.api import forecast_text, readable_job_log
from app.cpubudget import budget_threads
from app.media import commandos, gpuvarianten
from app.media.beslissen import beslis
from app.media.probe import probe
from app.uitvoerder import progress_log_line


def _rate(cmd: list[str]) -> int:
    return int(cmd[cmd.index("-b:v") + 1].rstrip("k"))


def test_gpu_preset_niet_meer_veryslow_en_bitrate_ondergrens() -> None:
    sdr = probe(pad("sdr-meertalig.mkv"))
    assert sdr is not None
    b = beslis(sdr, _inst())
    for klasse, height, floor in (("1080", 1080, 1500), ("720", 720, 900),
                                  ("2160", 2160, 5000)):
        info = {**sdr, "height": height}
        cmd = commandos.encode_gpu(info, b, _inst(), "in.mkv", "uit.mkv")
        # 4K blijft sneller dan veryslow (max. 2 tegelijk); ≤1080p mag veryslow (rapport 7)
        assert klasse != "2160" or "veryslow" not in cmd
        assert cmd[cmd.index("-preset") + 1] == commandos.GPU_PRESETS[klasse]
        assert _rate(cmd) >= floor and "-global_quality" not in cmd
    hdr = probe(pad("hdr10.mkv"))
    assert hdr is not None
    cmd = commandos.encode_gpu({**hdr, "height": 2160}, beslis(hdr, _inst()), _inst(),
                               "in.mkv", "uit.mkv", lookahead=False)
    assert _rate(cmd) >= commandos.GPU_BITRATE_FLOOR_HDR_2160
    assert "extra_hw_frames=16" in " ".join(cmd)  # 4K: minder videogeheugen per job


def test_hardwaredecode_houdt_frames_op_de_gpu_en_valt_terug() -> None:
    info = probe(pad("sdr-meertalig.mkv"))
    assert info is not None
    b = beslis(info, _inst())
    for variant in gpuvarianten.VARIANTEN:
        cmd = commandos.encode_gpu(info, b, _inst(), "in.mkv", "uit.mkv", variant=variant,
                                   hw_decode=True)
        joined = " ".join(cmd)
        assert "-hwaccel" in cmd and "hwupload" not in joined and "-threads" not in cmd
        assert ("scale_vaapi" in joined) == gpuvarianten.is_vaapi(variant)
    soft = commandos.encode_gpu(info, b, _inst(), "in.mkv", "uit.mkv", hw_decode=False)
    assert "-hwaccel" not in soft and "-threads" in soft
    commandos._HW_DECODE_STATE["broken"] = False
    assert commandos.hw_decode_supported({"codec": "hevc", "pix_fmt": "yuv420p10le"})
    assert not commandos.hw_decode_supported({"codec": "mpeg4", "pix_fmt": "yuv420p"})
    commandos.mark_hw_decode_broken()
    try:
        assert not commandos.hw_decode_supported({"codec": "hevc", "pix_fmt": "yuv420p"})
    finally:
        commandos._HW_DECODE_STATE["broken"] = False


def test_cpu_budget_met_hardwaredecode_geeft_cpu_encodes_echte_kernen() -> None:
    # 28 kernen, 75 %, 2 CPU-encodes, 4 GPU-jobs: met CPU-decode ± 2, met GPU-decode ± 8
    assert budget_threads(75, 2, cores=28, gpu_workers=4) == (21 - 16) // 2
    assert budget_threads(75, 2, cores=28, gpu_workers=4,
                          reserve=commandos.HW_DECODE_RESERVE) == (21 - 4) // 2


def _job(mg_client, name: str) -> tuple[object, int]:
    from conftest import mg_fixture
    from mg_helpers import copy_into_media
    from test_mediaguard_executor import _create

    client, database, configured = mg_client
    source = copy_into_media(configured, mg_fixture("mg-dual-audio.mkv"), f"films/{name}.mkv")
    return database, _create(client, source, correlation=f"mg-r3-{name}")


def test_leesbaar_joblog_toont_fasen_zonder_voortgangsruis(tmp_path: Path, mg_client) -> None:
    database, job = _job(mg_client, "log")
    from app.onderhoud import append_job_log

    append_job_log(database, job, "info", "Fase: Analyseren")
    log = tmp_path / "job.log"
    assert readable_job_log(database, job, log)[-1].endswith("Fase: Analyseren")
    log.write_text("frame=10\nout_time_ms=100\nprogress=continue\nStream mapping:\n"
                   + progress_log_line(12.5, 1.4, 3700) + "\n")
    lines = readable_job_log(database, job, log)
    assert "Stream mapping:" in lines and not any(x.startswith("frame=") for x in lines)
    assert any("Voortgang 12,5 %" in x and "1,40×" in x and "1u 1m" in x for x in lines)


def test_prognose_zonder_schijnprecisie() -> None:
    assert forecast_text(None).startswith("Nog te weinig")
    assert forecast_text(3.2) == "± 3 dagen"
    assert forecast_text(1.0) == "± 1 dag"
    assert forecast_text(30) == "± 4 weken"
    assert forecast_text(400) == "± 13 maanden"


def test_herstartlus_bij_nette_stops_wordt_begrensd(mg_client) -> None:
    from app.uitvoerder import MAX_RESTART_INTERRUPTIONS, cap_restart_loop

    database, job = _job(mg_client, "lus")
    database.execute("DELETE FROM job_poging WHERE job_id=?", (job,))
    now = datetime.now(UTC)
    for attempt in range(1, MAX_RESTART_INTERRUPTIONS + 1):
        database.execute("UPDATE job SET pogingen=? WHERE id=?", (attempt, job))
        database.execute(
            "INSERT INTO job_poging(job_id,poging,status,gestart_op,klaar_op,fout,foutsoort) "
            "VALUES (?,?,'afgebroken',?,?,'stop','ServiceStop')",
            (job, attempt, (now - timedelta(minutes=attempt)).isoformat(), now.isoformat()))
        capped = cap_restart_loop(database, job)
        assert capped == (attempt >= MAX_RESTART_INTERRUPTIONS)
    row = database.fetch_one("SELECT status,foutsoort,fout FROM job WHERE id=?", (job,))
    assert (row["status"], row["foutsoort"]) == ("mislukt", "HerstartLus")
    assert f"({MAX_RESTART_INTERRUPTIONS} pogingen in totaal)" in row["fout"]
