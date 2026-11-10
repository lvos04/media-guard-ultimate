"""HDR10 via de Arc: kleursignalering in de bitstream, metadata terug met mkvmerge, en één
CPU-herkansing als de metadata na de GPU-encode niet klopt."""

from __future__ import annotations

import json
from dataclasses import replace
from pathlib import Path

import pytest
from conftest import pad
from test_uitvoerder import _case

from app.media import commandos, gpuvarianten
from app.media.beslissen import STANDAARD, Besluit, beslis
from app.media.probe import probe
from app.mediaguard.policy import MediaGuardPolicy, to_profile_inst
from app.uitvoerder import GPU_HDR_FALLBACK_REDEN, run_job
from app.verificatie import VerificationResult

LAAG = {"drempel_2160": 500, "drempel_1080": 500, "drempel_720": 300}


def _inst(encoder: str = "auto", gpu: bool = True) -> dict:
    policy = MediaGuardPolicy.model_validate({"encoder": encoder})
    return {**STANDAARD, **to_profile_inst(policy, gpu_lane_available=gpu), **LAAG}


def test_hdr10_auto_gaat_naar_gpu_met_metadataherstel() -> None:
    info = probe(pad("hdr10.mkv"))
    assert info is not None and info["is_hdr10"]
    b = beslis(info, _inst())
    assert (b.baan, b.encoder, b.herstel_hdr_na) == ("gpu", "av1_qsv", True)
    assert "HDR10 via GPU, metadata daarna teruggezet" in b.reden
    cpu = beslis(info, _inst(gpu=False))
    assert (cpu.baan, cpu.encoder, cpu.behoud_hdr) == ("cpu", "libsvtav1", True)
    assert beslis(info, _inst(encoder="cpu")).baan == "cpu"


def test_dv_en_hdr10plus_blijven_op_de_cpu() -> None:
    base = probe(pad("hdr10.mkv"))
    assert base is not None
    hdr10plus = {**base, "hdr10plus": True}
    for modus in ("converteer_geverifieerd", "skip"):
        b = beslis(hdr10plus, {**_inst(), "hdr10plus_modus": modus})
        assert b.encoder != "av1_qsv" and b.baan != "gpu", modus
    # Rapport 7 §4.1: in de expliciete HDR10-terugval vervalt de dynamische metadata toch,
    # dus dezelfde GPU-route als gewoon HDR10 (zonder GPU: SVT op de CPU).
    b = beslis(hdr10plus, {**_inst(), "hdr10plus_modus": "hdr10_fallback"})
    assert (b.baan, b.encoder, b.herstel_hdr_na, b.hdr10plus_route) == (
        "gpu", "av1_qsv", True, "hdr10_fallback")
    b = beslis(hdr10plus, {**_inst(gpu=False), "hdr10plus_modus": "hdr10_fallback"})
    assert (b.baan, b.encoder) == ("cpu", "libsvtav1")
    dv = {**base, "dv_profile": 8, "dv_compat": 1}
    b = beslis(dv, _inst())
    assert b.encoder != "av1_qsv" and b.baan != "gpu"
    with pytest.raises(ValueError):
        commandos.encode_gpu(dv, b.model_copy(update={"dolby_vision": True}), _inst(),
                             "in.mkv", "uit.mkv")


def test_gpu_commando_signaleert_pq_bt2020_en_blijft_10_bit() -> None:
    info = probe(pad("hdr10.mkv"))
    assert info is not None
    b = beslis(info, _inst())
    for variant in gpuvarianten.VARIANTEN:
        v = {**variant, "pix": "nv12"}
        cmd = commandos.encode_gpu(info, b, _inst(), "in.mkv", "uit.mkv", variant=v)
        joined = " ".join(cmd)
        assert "-color_primaries bt2020" in joined and "-color_trc smpte2084" in joined
        assert "-colorspace bt2020nc" in joined and "-color_range tv" in joined
        assert "p010" in joined and "nv12" not in joined
    sdr = probe(pad("sdr-meertalig.mkv"))
    assert sdr is not None and not sdr["is_hdr10"]
    cmd = commandos.encode_gpu(sdr, beslis(sdr, _inst()), _inst(), "in.mkv", "uit.mkv")
    assert "-color_trc" not in cmd


def test_herstel_zonder_of_met_halve_metadata_crasht_niet() -> None:
    kaal = commandos.herstel_hdr(None, None, "a.mkv", "b.mkv")
    assert "--colour-transfer-characteristics" in kaal
    assert "--chromaticity-coordinates" not in kaal and "--max-content-light" not in kaal
    assert commandos.herstel_hdr({}, "0,0", "a.mkv", "b.mkv") == kaal
    half = commandos.herstel_hdr({"max_luminance": 1000.0, "red_x": 0.68}, "1000,400",
                                 "a.mkv", "b.mkv")
    assert "--max-luminance" in half and "--chromaticity-coordinates" not in half
    assert half[half.index("--max-content-light") + 1] == "0:1000"
    assert kaal[-1] == "a.mkv" and half[-1] == "a.mkv"


def _naar_gpu(database, job_id: int) -> Besluit:
    row = database.fetch_one("SELECT besluit_id FROM job WHERE id=?", (job_id,))
    payload = database.fetch_one("SELECT payload FROM besluit WHERE id=?", (row[0],))[0]
    gpu = Besluit.model_validate_json(payload).model_copy(update={
        "baan": "gpu", "encoder": "av1_qsv", "herstel_hdr_na": True, "behoud_hdr": False})
    database.execute("UPDATE besluit SET baan='gpu',encoder='av1_qsv',payload=? WHERE id=?",
                     (gpu.model_dump_json(), row[0]))
    database.execute("UPDATE job SET baan='gpu' WHERE id=?", (job_id,))
    return gpu


def _state(database, job_id: int):
    return database.fetch_one(
        "SELECT j.status,j.baan,j.gpu_hdr_teruggevallen,j.fout,b.encoder,b.payload "
        "FROM job j JOIN besluit b ON b.id=j.besluit_id WHERE j.id=?", (job_id,))


@pytest.mark.asyncio
async def test_mislukte_gpu_hdr_encode_gaat_een_keer_naar_de_cpu(tmp_path: Path) -> None:
    database, configured, _source, job_id = _case(tmp_path)
    _naar_gpu(database, job_id)
    fail = ["/usr/bin/python3", "-c", "import sys; sys.exit(3)"]
    assert not await run_job(database, configured, job_id, storage_validator=lambda: None,
                             command_override=fail)
    row = _state(database, job_id)
    assert (row["status"], row["baan"], row["gpu_hdr_teruggevallen"]) == ("wacht", "cpu", 1)
    assert row["encoder"] == "libsvtav1" and row["fout"] == GPU_HDR_FALLBACK_REDEN
    payload = json.loads(row["payload"])
    assert payload["behoud_hdr"] is True and payload["herstel_hdr_na"] is False
    assert not (configured.cache_root / str(job_id)).exists()
    # tweede keer (zelfde job weer op GPU gezet): geen lus, gewoon een fout
    _naar_gpu(database, job_id)
    assert not await run_job(database, configured, job_id, storage_validator=lambda: None,
                             command_override=fail)
    assert _state(database, job_id)["status"] == "mislukt"


@pytest.mark.asyncio
async def test_hdr_metadata_ongelijk_na_gpu_gaat_naar_de_cpu(tmp_path: Path) -> None:
    database, configured, _source, job_id = _case(tmp_path)
    gpu = _naar_gpu(database, job_id)
    info = probe(str(_source))
    assert info is not None
    partial = configured.cache_root / str(job_id) / "resultaat.mkv.deel"
    cpu_cmd = commandos.encode_cpu(info, gpu.model_copy(update={"encoder": "libsvtav1"}),
                                   {**STANDAARD, "preset_1080": 12, "crf_1080": 50},
                                   str(_source), str(partial))
    cpu_cmd = [*cpu_cmd[:-1], "-f", "matroska", cpu_cmd[-1]]

    def mismatch(result: VerificationResult) -> VerificationResult:
        return replace(result, passed=False,
                       reason="HDR10 mastering display of content light is gewijzigd")

    assert not await run_job(database, configured, job_id, storage_validator=lambda: None,
                             command_override=cpu_cmd, verification_hook=mismatch)
    row = _state(database, job_id)
    assert (row["status"], row["baan"], row["encoder"]) == ("wacht", "cpu", "libsvtav1")
    assert "HDR10 mastering display" in json.loads(row["payload"])["reden"]


def test_rebalance_verplaatst_hdr10_maar_niet_na_terugval(mg_client) -> None:
    from conftest import mg_fixture
    from mg_helpers import FAST, HEADERS, copy_into_media
    from test_rebalance import _caps, _lane

    from app.mediaguard.rebalance import rebalance_waiting_jobs

    client, database, configured = mg_client
    ids = []
    for name in ("h1", "h2"):
        source = copy_into_media(configured, mg_fixture("mg-hdr10.mkv"), f"films/{name}.mkv")
        response = client.post(
            "/api/mediaguard/v1/jobs",
            json={"source_path": str(source), "correlation_id": f"hdr-{name}",
                  "policy": {**FAST, "encoder": "auto"}},
            headers=HEADERS)
        assert response.status_code == 201, response.text
        ids.append(int(response.json()["job_id"]))
    assert all(_lane(database, i)[0] == "cpu" for i in ids)
    database.execute("UPDATE job SET gpu_hdr_teruggevallen=1 WHERE id=?", (ids[1],))
    moved = rebalance_waiting_jobs(database, configured, _caps(client, gpu=True))
    assert moved["naar_gpu"] == 1
    assert _lane(database, ids[0]) == ("gpu", "av1_qsv")
    assert _lane(database, ids[1]) == ("cpu", "libsvtav1")
