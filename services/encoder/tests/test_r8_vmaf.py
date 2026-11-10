"""Rapport 8, B2: representatieve proefstukken en één herkansing bij een VMAF-afkeuring."""

from __future__ import annotations

import json

from app.media.beslissen import Besluit
from app.uitvoerder import pick_sample_starts, requeue_quality_vmaf


def test_drukste_venster_eerst_zonder_intro_en_aftiteling() -> None:
    duration = 1440.0  # 24 min, vensters van 60 s
    buckets = {i: 1000 for i in range(24)}
    buckets[0] = 9999   # zware intro: valt binnen de rand van 8 %
    buckets[23] = 9999  # aftiteling: idem
    buckets[17] = 5000  # drukste stuk in de aflevering zelf
    starts = pick_sample_starts(duration, buckets)
    assert starts[0] == (17 * 60.0, 5000)
    # daarna nog 15/50/80 % als reserve, nooit dubbel
    positions = [s for s, _b in starts]
    assert len(positions) == len(set(positions)) and len(positions) >= 3


def test_zonder_pakketscan_vaste_posities() -> None:
    starts = pick_sample_starts(3000.0, None)
    assert [round(s) for s, _b in starts] == [450, 1500, 2400]
    assert all(b is None for _s, b in starts)


def _besluit(encoder: str) -> Besluit:
    return Besluit(actie="hercoderen", baan="gpu" if encoder == "av1_qsv" else "cpu",
                   encoder=encoder, reden="test")


def test_gpu_herkansing_met_meer_bits(tmp_path) -> None:
    from test_uitvoerder import _case

    database, _settings, _source, job_id = _case(tmp_path)
    route = requeue_quality_vmaf(database, job_id, _besluit("av1_qsv"), 1.2, "VMAF 73,5")
    row = database.fetch_one("SELECT status,baan,mg_inst,kwaliteit_herkansd,fout FROM job "
                             "WHERE id=?", (job_id,))
    assert route == "gpu" and row["status"] == "wacht" and int(row["kwaliteit_herkansd"]) == 1
    assert json.loads(row["mg_inst"])["gpu_rate_factor"] == 1.8
    assert "VMAF te laag" in row["fout"]


def test_gpu_op_plafond_gaat_via_cpu_met_lagere_crf(tmp_path) -> None:
    from test_uitvoerder import _case

    database, _settings, _source, job_id = _case(tmp_path)
    route = requeue_quality_vmaf(database, job_id, _besluit("av1_qsv"), 3.5, "VMAF 70")
    row = database.fetch_one("SELECT baan,mg_inst FROM job WHERE id=?", (job_id,))
    inst = json.loads(row["mg_inst"])
    assert route == "cpu" and row["baan"] == "cpu"
    assert inst["gpu_voor_sdr_1080"] is False and inst["crf_1080"] < 27


def test_cpu_herkansing_lagere_crf(tmp_path) -> None:
    from test_uitvoerder import _case

    from app.media.beslissen import STANDAARD

    database, _settings, _source, job_id = _case(tmp_path)
    route = requeue_quality_vmaf(database, job_id, _besluit("libsvtav1"), 1.0, "VMAF 88")
    inst = json.loads(database.fetch_one("SELECT mg_inst FROM job WHERE id=?",
                                         (job_id,))["mg_inst"])
    assert route == "cpu" and inst["crf_2160"] == max(10, int(STANDAARD["crf_2160"]) - 6)
