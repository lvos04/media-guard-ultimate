"""Gebruikersrapport (zondag): anime op de GPU half afgekeurd op VMAF, een CPU-job van ± 40 uur,
DV-voorstappen op 0,0 % en 'groter dan de bron' als fout."""

from __future__ import annotations

import asyncio
import json
from pathlib import Path

import pytest
from test_planner import _planner_case

from app import afstelling, planner, routeleer, uitvoerder, vastlopers
from app.media import commandos
from app.media.beslissen import STANDAARD, Besluit


def test_proefstuk_onder_doel_leidt_om_in_plaats_van_volledige_gpu_encode(
        monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    from app import kwaliteit

    database, _configured, job_ids = _planner_case(tmp_path)

    async def fake_run(_db, _job, arguments, _log, _duration):  # type: ignore[no-untyped-def]
        Path(arguments[-1]).write_bytes(b"x" * 300_000)
        return True

    scores = iter([78.0, 82.0, 86.0])

    async def fake_vmaf(*_a, **_k):  # type: ignore[no-untyped-def]
        return next(scores)

    async def yes() -> bool:
        return True

    monkeypatch.setattr(uitvoerder, "_run_process", fake_run)
    monkeypatch.setattr(kwaliteit, "compute_vmaf", fake_vmaf)
    monkeypatch.setattr(kwaliteit, "vmaf_available", yes)
    skip, _factor, reroute = asyncio.run(uitvoerder._sample_phase(
        database, job_ids[0], lambda f: ["ffmpeg", "-i", "bron.mkv", "uit.deel"], tmp_path,
        tmp_path / "bron.mkv", 104_000_000, 1400.0, 15.0, tmp_path / "log", steer=True))
    assert skip is None
    assert reroute is not None and "VMAF 86,0" in reroute


def test_reeks_leert_na_twee_gpu_afkeuringen(tmp_path: Path) -> None:
    database, _configured, _ids = _planner_case(tmp_path)
    afstelling.refresh(database)
    ep1 = "/data/Media/Anime TV/Elfenvuur/Season 01/Elfenvuur - S01E21.mkv"
    ep2 = "/data/Media/Anime TV/Elfenvuur/Season 01/Elfenvuur - S01E29.mkv"
    ep3 = "/data/Media/Anime TV/Elfenvuur/Season 02/Elfenvuur - S02E01.mkv"
    assert routeleer.series_key(ep1) == routeleer.series_key(ep3)
    assert routeleer.gpu_block_reason(ep3) is None
    routeleer.record_gpu_rejection(database, ep1, "VMAF 73,5")
    assert routeleer.gpu_block_reason(ep3) is None
    routeleer.record_gpu_rejection(database, ep2, "VMAF 82,6")
    reason = routeleer.gpu_block_reason(ep3)
    assert reason is not None and "Anime-reeks gaat via de CPU" in reason and "(2×)" in reason
    other = "/data/Media/Anime TV/Wind Breaker/Season 01/Wind Breaker - S01E01.mkv"
    assert routeleer.gpu_block_reason(other) is None


def test_anime_route_instelling(tmp_path: Path) -> None:
    database, _configured, _ids = _planner_case(tmp_path)
    anime = "/data/Media/Anime TV/Frieren/Season 01/Frieren - S01E01.mkv"
    film = "/data/Media/Films/Wadi (2021)/Wadi.mkv"
    afstelling.update(database, {"anime_route": "cpu"})
    try:
        assert routeleer.gpu_block_reason(anime) is not None
        assert routeleer.gpu_block_reason(film) is None
        with pytest.raises(ValueError):
            afstelling.update(database, {"anime_route": "soms"})
    finally:
        afstelling.update(database, {"anime_route": None})
    view = {k["sleutel"]: k for k in afstelling.view()["items"]}
    assert [c["waarde"] for c in view["anime_route"]["keuzes"]] == ["auto", "gpu", "cpu"]


def test_geen_winst_band_wordt_geleerd(tmp_path: Path) -> None:
    database, _configured, _ids = _planner_case(tmp_path)
    info = {"codec": "hevc", "height": 1080, "kbps": 3500, "is_hdr10": False}
    assert routeleer.no_gain_reason(info) is None
    routeleer.record_no_gain(database, info, "groter")
    routeleer.record_no_gain(database, info, "groter")
    assert "geen winst verwacht" in str(routeleer.no_gain_reason(info))
    assert routeleer.no_gain_reason({**info, "kbps": 9000}) is None


def test_omleiden_naar_cpu_telt_niet_als_fout(tmp_path: Path) -> None:
    database, _configured, job_ids = _planner_case(tmp_path)
    job = job_ids[0]
    database.execute("UPDATE job SET baan='gpu',status='bezig' WHERE id=?", (job,))
    decision = Besluit(actie="hercoderen", baan="gpu", encoder="av1_qsv", reden="x")
    source = "/data/Media/Anime TV/Tokyo 24th Ward/Season 01/T24 - S01E01.mkv"
    route = uitvoerder.reroute_to_cpu(database, job, decision, source, "VMAF 70 < 95")
    assert route == "cpu"
    row = database.fetch_one("SELECT status,baan,omgeleid,mg_inst FROM job WHERE id=?", (job,))
    assert (row["status"], row["baan"], int(row["omgeleid"])) == ("wacht", "cpu", 1)
    assert json.loads(row["mg_inst"])["svt_extra"] == uitvoerder.ANIME_SVT_PARAMS
    # tweede keer omleiden: geen route meer → overgeslagen, geen 'mislukt'
    assert uitvoerder.reroute_to_cpu(database, job, decision, source, "nog steeds") == \
        "overgeslagen"
    assert database.fetch_one("SELECT status FROM job WHERE id=?", (job,))[0] == "overgeslagen"


def test_svt_extra_parameters_in_cpu_commando() -> None:
    info = {"height": 1080, "is_hdr10": False, "duration": 1400.0, "audio": [], "subs": [],
            "attachments": 0, "mastering_display": None, "content_light": None,
            "video_indices": [0], "container": "matroska"}
    decision = Besluit(actie="hercoderen", baan="cpu", encoder="libsvtav1", reden="x")
    inst = {**STANDAARD, "svt_extra": ["tune=0", "enable-tf=0"]}
    command = commandos.encode_cpu(info, decision, inst, "in.mkv", "uit.mkv")
    params = command[command.index("-svtav1-params") + 1]
    assert "tune=0" in params and "enable-tf=0" in params


def test_vastloper_route_vermijdt_libaom(tmp_path: Path) -> None:
    database, _configured, job_ids = _planner_case(tmp_path)
    job = job_ids[0]
    decision = Besluit(actie="hercoderen", baan="cpu", encoder="libaom-av1", reden="x",
                       hdr10plus_route="behouden")
    vastlopers.apply_reroute(database, job, decision, "cpu_snel", "sneller")
    row = database.fetch_one(
        "SELECT b.encoder,j.mg_inst FROM job j JOIN besluit b ON b.id=j.besluit_id "
        "WHERE j.id=?", (job,))
    assert row["encoder"] == "libsvtav1"
    assert json.loads(row["mg_inst"])["preset_2160"] >= 10


def test_nachtjobs_alleen_overdag_als_niets_anders(tmp_path: Path,
                                                   monkeypatch: pytest.MonkeyPatch) -> None:
    database, _configured, _ids = _planner_case(tmp_path)
    monkeypatch.setattr(planner, "in_night_window", lambda now=None: False)
    long_job = {"alleen_nacht": 1, "encoder": "libsvtav1", "probe": None, "id": 1}
    short_job = {"alleen_nacht": 0, "encoder": "libsvtav1", "probe": None, "id": 2}
    assert planner.pick_candidate(database, "cpu", [long_job, short_job])["id"] == 2
    assert planner.pick_candidate(database, "cpu", [long_job])["id"] == 1


def test_eerder_afgekeurde_gpu_jobs_krijgen_cpu_kans(tmp_path: Path) -> None:
    database, _configured, job_ids = _planner_case(tmp_path)
    job = job_ids[0]
    database.execute(
        "UPDATE job SET baan='gpu',status='mislukt',mg_bron='{}',"
        "fout='Kwaliteit te laag: VMAF 73,5 (slechtste steekproef 68,8) onder 93,0' WHERE id=?",
        (job,))
    database.execute(
        "UPDATE besluit SET payload=? WHERE id=(SELECT besluit_id FROM job WHERE id=?)",
        (Besluit(actie="hercoderen", baan="gpu", encoder="av1_qsv",
                 reden="x").model_dump_json(), job))
    assert uitvoerder.reroute_failed_gpu_quality_jobs(database) == 1
    assert database.fetch_one("SELECT status,baan FROM job WHERE id=?", (job,))[:] == \
        ("wacht", "cpu")
    assert uitvoerder.reroute_failed_gpu_quality_jobs(database) == 0
