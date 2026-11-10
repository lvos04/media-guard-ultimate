"""Rapport 4: kwaliteitsondergrens voor CPU-encodes en CRF per dynamiek (HDR/DV lager)."""

from __future__ import annotations

from conftest import pad

from app.media import commandos
from app.media.beslissen import STANDAARD, beslis
from app.media.probe import probe
from app.mediaguard.policy import MediaGuardPolicy, to_profile_inst
from app.uitvoerder import _bitrate_floor_problem, requeue_quality_floor

LAAG = {"drempel_2160": 500, "drempel_1080": 500, "drempel_720": 300}


def _inst() -> dict:
    policy = MediaGuardPolicy.model_validate({"encoder": "cpu"})
    return {**STANDAARD, **to_profile_inst(policy, gpu_lane_available=False), **LAAG}


def _crf(cmd: list[str]) -> int:
    return int(cmd[cmd.index("-crf") + 1])


def test_hdr_bron_krijgt_lagere_crf_dan_sdr() -> None:
    info = probe(pad("hdr10.mkv"))
    assert info is not None
    b = beslis(info, _inst())
    hdr = _crf(commandos.encode_cpu(info, b, _inst(), "in.mkv", "uit.mkv"))
    sdr_info = {**info, "is_hdr10": False, "dv_profile": 0, "hdr10plus": False,
                "kleur": {**(info.get("kleur") or {}), "transfer": "bt709"},
                "mastering_display": "", "content_light": ""}
    sdr = _crf(commandos.encode_cpu(sdr_info, b.model_copy(update={"behoud_hdr": False}),
                                    _inst(), "in.mkv", "uit.mkv"))
    assert hdr < sdr
    # dv telt ook als HDR
    assert commandos.is_hdr_source({"dv_profile": 8})
    assert commandos.is_hdr_source({"kleur": {"transfer": "arib-std-b67"}})


def test_expliciete_crf_geldt_ook_voor_hdr() -> None:
    policy = MediaGuardPolicy.model_validate({"encoder": "cpu", "crf": 30})
    inst = {**STANDAARD, **to_profile_inst(policy, gpu_lane_available=False)}
    assert inst["crf_2160_hdr"] == inst["crf_1080"] == 30


def test_ondergrens_per_resolutie_en_dynamiek(monkeypatch) -> None:
    assert commandos.bitrate_floor_kbps({"height": 2160, "dv_profile": 8}) == 8000
    assert commandos.bitrate_floor_kbps({"height": 2160}) == 6000
    assert commandos.bitrate_floor_kbps({"height": 1080, "is_hdr10": True}) == 3000
    assert commandos.bitrate_floor_kbps({"height": 1080}) == 1500
    monkeypatch.setenv("MG_BITRATE_FLOOR_SCALE", "0")
    assert commandos.bitrate_floor_kbps({"height": 2160}) == 0


def test_te_laag_resultaat_geeft_probleem_alleen_bij_cpu_encode() -> None:
    info = probe(pad("hdr10.mkv"))
    assert info is not None
    b = beslis(info, _inst())
    bron = {"height": 2160, "dv_profile": 8, "kbps": 40000, "duration": 2700.0}
    found = _bitrate_floor_problem(bron, {"kbps": 2000}, b)
    assert found is not None and found[0] == "2160" and found[1] == 8000
    assert "onder de ondergrens" in found[2] and "HDR/DV" in found[2]
    assert _bitrate_floor_problem(bron, {"kbps": 9000}, b) is None
    # lage bron: kan niet boven de grens uitkomen → geen herkansing
    assert _bitrate_floor_problem({**bron, "kbps": 7000}, {"kbps": 2000}, b) is None
    assert _bitrate_floor_problem({**bron, "duration": 30.0}, {"kbps": 2000}, b) is None
    gpu = b.model_copy(update={"encoder": "av1_qsv"})
    assert _bitrate_floor_problem(bron, {"kbps": 2000}, gpu) is None


def test_herkansing_zet_doelbitrate_en_vlag(tmp_path) -> None:
    import json

    from test_uitvoerder import _case

    database, _settings, _source, job_id = _case(tmp_path)
    requeue_quality_floor(database, job_id, "2160", 8000, "te laag")
    row = database.fetch_one("SELECT status,mg_inst,kwaliteit_herkansd,fout FROM job WHERE id=?",
                             (job_id,))
    assert row["status"] == "wacht" and int(row["kwaliteit_herkansd"]) == 1
    assert json.loads(row["mg_inst"])["doel_bitrate_2160_kbps"] == 8800
    assert "ondergrens" in row["fout"]


def test_dv_record_zonder_rpu_per_beeld_wordt_afgekeurd(tmp_path, monkeypatch) -> None:
    import app.verificatie as verificatie

    info = probe(pad("hdr10.mkv"))
    assert info is not None
    b = beslis(info, _inst())
    bron = {**info, "dv_profile": 8, "dv_rpu_frames": True}
    uit = {**info, "dv_profile": 10, "dv_rpu_frames": False, "size": 10}
    result_path = tmp_path / "uit.mkv"
    result_path.write_bytes(b"x")
    monkeypatch.setattr(verificatie, "probe", lambda _p: uit)
    result = verificatie.verify_result(bron, result_path, 10**9, b)
    assert not result.passed and "RPU" in result.reason
    # bron zonder zichtbare RPU per beeld: geen valse afkeuring
    monkeypatch.setattr(verificatie, "probe", lambda _p: {**uit, "dv_rpu_frames": None})
    result = verificatie.verify_result({**bron, "dv_rpu_frames": None}, result_path, 10**9, b)
    assert "RPU" not in (result.reason or "")
