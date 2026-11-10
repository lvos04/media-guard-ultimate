"""DEEL Q3 acceptatie: DV-routes echt encoderen en de metadata teruglezen.

Iedere test die "geslaagd" zegt, heeft het resultaat met ffprobe (stream-
configuratierecord + per-frame RPU) en mediainfo teruggelezen. Fixtures komen
uit tests/fixtures/maak-mediaguard-fixtures.sh (dovi_tool generate/inject-rpu).
"""

from __future__ import annotations

import shutil
import subprocess
from pathlib import Path
from typing import Any

import pytest
from conftest import mg_fixture
from fastapi.testclient import TestClient
from mg_helpers import FAST, HEADERS, copy_into_media, run_bridge_job

from app.config import Settings
from app.db import Database
from app.media import commandos
from app.media.beslissen import STANDAARD, Besluit
from app.media.dv_controle import controleer_dv, dv_readback
from app.media.probe import probe
from app.verificatie import verify_result
from app.vervanging import file_sha256

pytestmark = pytest.mark.usefixtures("mg_testdata")
MgClient = tuple[TestClient, Database, Settings]


def _requires_dv_toolchain() -> None:
    out = subprocess.run(
        ["ffmpeg", "-hide_banner", "-h", "encoder=libsvtav1"], capture_output=True, text=True
    ).stdout
    if "dolbyvision" not in out:
        pytest.skip("ffmpeg < 7.1 of zonder libsvtav1 -dolbyvision")


def _job(client: TestClient, source: Path, **policy: Any) -> dict[str, Any]:
    response = client.post(
        "/api/mediaguard/v1/jobs",
        json={"source_path": str(source), "policy": {**FAST, **policy}},
        headers=HEADERS,
    )
    assert response.status_code == 201, response.text
    return dict(response.json())


def _view(client: TestClient, job_id: int) -> dict[str, Any]:
    return dict(client.get(f"/api/mediaguard/v1/jobs/{job_id}", headers=HEADERS).json())


# ── Q3.1: toggle UIT ────────────────────────────────────────────────────────


def test_dv81_toggle_uit_job_is_skipped_origineel_behouden(mg_client: MgClient) -> None:
    client, _database, configured = mg_client
    source = copy_into_media(configured, mg_fixture("mg-dv81.mkv"), "films/dv81.mkv")
    created = _job(client, source, dv_mode="keep_original")
    assert created["status"] == "skipped"
    assert "dv_modus staat op skip" in created["skip_reason"]


# ── Q3.2: toggle AAN -> echte proefencode, RPU gevalideerd, origineel ongemoeid ──


def test_dv81_trial_copy_encodeert_naar_dv10_met_rpu_en_raakt_bron_niet(
    mg_client: MgClient,
) -> None:
    _requires_dv_toolchain()
    client, database, configured = mg_client
    source = copy_into_media(configured, mg_fixture("mg-dv81.mkv"), "films/dv81.mkv")
    before = file_sha256(source)
    plan = client.post(
        "/api/mediaguard/v1/plan",
        json={"path": str(source), "policy": {**FAST, "dv_mode": "trial_copy"}},
        headers=HEADERS,
    ).json()
    assert plan["action"] == "hercoderen" and plan["dv"]["executes"] is True
    assert plan["dv"]["route"] == "8.1"
    assert plan["replace_eligible"] is False

    job_id = int(_job(client, source, dv_mode="trial_copy")["job_id"])
    command = database.fetch_one("SELECT commando FROM job WHERE id=?", (job_id,))
    assert run_bridge_job(database, configured, job_id) is True
    command = database.fetch_one("SELECT commando FROM job WHERE id=?", (job_id,))
    assert command is not None and '"-dolbyvision", "1"' in str(command["commando"])

    job = _view(client, job_id)
    assert job["status"] == "awaiting_commit", job
    assert job["replace_eligible"] is False  # proefkopie: core mag niet activeren
    dv = job["verification"]["dv"]
    assert dv["rpu_present"] is True and dv["rpu_frames"] > 0
    assert dv["profile"] == "10.1"
    staged = Path(job["staged_output"]["path"])
    readback = dv_readback(staged)
    assert readback["codec"] == "av1"
    assert readback["dv_profile"] == 10 and readback["bl_compat"] == 1
    assert readback["rpu_present"] is True
    assert readback["kleur"]["transfer"] == "smpte2084"
    assert readback["kleur"]["primaries"] == "bt2020"
    if shutil.which("mediainfo"):
        assert "Dolby Vision" in str(readback["mediainfo_hdr"])
    result = probe(str(staged))
    source_info = probe(str(source))
    assert result is not None and source_info is not None
    assert result["mastering_display"] == source_info["mastering_display"]  # HDR10-basis
    assert file_sha256(source) == before
    # Core mag een proefkopie niet als vervanging activeren.
    response = client.post(f"/api/mediaguard/v1/jobs/{job_id}/committed", headers=HEADERS)
    assert response.status_code == 409


def test_dv81_auto_after_verify_is_vervangbaar_na_verificatie(mg_client: MgClient) -> None:
    """Q3.3 (encoderzijde): eligible zonder externe device-matrix; core doet canary/recovery."""

    _requires_dv_toolchain()
    client, database, configured = mg_client
    source = copy_into_media(configured, mg_fixture("mg-dv81.mkv"), "films/dv81-auto.mkv")
    job_id = int(_job(client, source, dv_mode="auto_after_verify")["job_id"])
    assert run_bridge_job(database, configured, job_id) is True
    job = _view(client, job_id)
    assert job["status"] == "awaiting_commit"
    assert job["replace_eligible"] is True
    assert job["verification"]["dv"]["profile"] == "10.1"


def test_dv84_hlg_encodeert_naar_dv10_4_en_behoudt_hlg(mg_client: MgClient) -> None:
    _requires_dv_toolchain()
    client, database, configured = mg_client
    source = copy_into_media(configured, mg_fixture("mg-dv84.mkv"), "films/dv84.mkv")
    job_id = int(_job(client, source, dv_mode="trial_copy")["job_id"])
    assert run_bridge_job(database, configured, job_id) is True
    job = _view(client, job_id)
    assert job["status"] == "awaiting_commit", job
    readback = dv_readback(Path(job["staged_output"]["path"]))
    assert readback["dv_profile"] == 10 and readback["bl_compat"] == 4
    assert readback["rpu_present"] is True
    assert readback["kleur"]["transfer"] == "arib-std-b67"


# ── Q3.6: DV5 en HDR10+ ─────────────────────────────────────────────────────


def test_dv5_job_is_niet_ondersteund(mg_client: MgClient) -> None:
    client, _database, configured = mg_client
    source = copy_into_media(configured, mg_fixture("mg-dv5.mkv"), "films/dv5.mkv")
    created = _job(
        client,
        source,
        dv_mode="experimental_keep_source",
        dv_allowed_profiles=["8.1", "8.4", "7_mel", "7_fel"],
    )
    assert created["status"] == "skipped"
    assert "profiel 5" in created["skip_reason"]
    trial = client.post(
        "/api/mediaguard/v1/trials",
        json={"path": str(source), "dv_mode": "trial_copy"},
        headers=HEADERS,
    )
    assert trial.status_code == 409


def test_hdr10plus_blijft_skip(mg_client: MgClient) -> None:
    client, _database, configured = mg_client
    source = copy_into_media(configured, mg_fixture("mg-hdr10plus.mkv"), "films/hdr10plus.mkv")
    created = _job(client, source)
    assert created["status"] == "skipped"
    assert "HDR10+" in created["skip_reason"]


def test_hdr10_mastering_display_wordt_teruggelezen(mg_client: MgClient) -> None:
    client, database, configured = mg_client
    source = copy_into_media(configured, mg_fixture("mg-hdr10.mkv"), "films/hdr10.mkv")
    job_id = int(_job(client, source)["job_id"])
    assert run_bridge_job(database, configured, job_id) is True
    job = _view(client, job_id)
    assert job["status"] == "awaiting_commit"
    result = probe(job["staged_output"]["path"])
    source_info = probe(str(source))
    assert result is not None and source_info is not None
    assert result["is_hdr10"] is True
    assert result["mastering_display"] == source_info["mastering_display"]
    assert result["content_light"] == source_info["content_light"]
    assert result["kleur"]["transfer"] == "smpte2084"


# ── Q3.7: DV-uitvoer zonder RPU -> verificatiefout, bron behouden ───────────


def test_encode_zonder_expliciete_dv_vlag_faalt_de_verificatie(
    mg_client: MgClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    _requires_dv_toolchain()
    client, database, configured = mg_client
    source = copy_into_media(configured, mg_fixture("mg-dv81.mkv"), "films/dv-zonder.mkv")
    before = file_sha256(source)
    original = commandos.encode_cpu

    def zonder_dv_vlag(*args: Any, **kwargs: Any) -> list[str]:
        command = original(*args, **kwargs)
        index = command.index("-dolbyvision")
        return command[:index] + command[index + 2 :]  # "auto": RPU stil weg

    monkeypatch.setattr(commandos, "encode_cpu", zonder_dv_vlag)
    job_id = int(_job(client, source, dv_mode="auto_after_verify")["job_id"])
    assert run_bridge_job(database, configured, job_id) is False
    job = _view(client, job_id)
    assert job["status"] == "failed"
    assert "Dolby Vision" in job["error"]
    assert job["staged_output"] is None
    assert not (configured.effective_staging_root / str(job_id)).exists()
    assert file_sha256(source) == before


def test_gewijzigde_kleur_faalt_de_dv_controle(tmp_path: Path) -> None:
    """Kleur die "er in de metadata goed uitziet" maar niet klopt, blokkeert.

    libsvtav1 weigert zelf al DV met tegenstrijdige kleurtags (vastgesteld:
    "could not determine profile and compatibility mode"). De realistische
    stille fout is daarom een remux die containerkleur overschrijft
    (spec/02 #4); die moet de verificatie vangen.
    """

    _requires_dv_toolchain()
    if not (shutil.which("mkvmerge") and shutil.which("mediainfo")):
        pytest.skip("mkvmerge/mediainfo ontbreken")
    source = mg_fixture("mg-dv81.mkv")
    info = probe(str(source))
    assert info is not None
    good = tmp_path / "goed.mkv"
    decision = Besluit(actie="hercoderen", baan="cpu", encoder="libsvtav1", dolby_vision=True)
    command = commandos.encode_cpu(
        info, decision, {**STANDAARD, "crf_720": 50, "preset_720": 12}, str(source), str(good)
    )
    subprocess.run(command, check=True, capture_output=True)
    assert controleer_dv(info, good)[0] is True
    bad = tmp_path / "bt709-container.mkv"
    subprocess.run(
        ["mkvmerge", "-q", "-o", str(bad), "--colour-primaries", "0:1", str(good)], check=True
    )
    ok, reason, _details = controleer_dv(info, bad)
    assert ok is False
    # FFmpeg ≥ 8.1 (eigen build) leest de containerkleur zelf al uit; dan vangt de
    # ffprobe-vergelijking het, anders het mediainfo-conflict. Beide = afgekeurd.
    assert "colour_primaries" in reason or "kleur-primaries" in reason
    verification = verify_result(info, bad, source.stat().st_size, decision)
    assert verification.passed is False


# ── Q3.4/Q3.5: profiel 7 ────────────────────────────────────────────────────


def test_dv7_omzetroute_mechaniek_dovi_tool_mkvmerge_svt(tmp_path: Path) -> None:
    """De drie-staps-route uit commandos.dv7_naar_81 echt uitvoeren en teruglezen.

    Er is geen synthetische profiel-7-bron (dovi_tool generate kent alleen 5, 8.1
    en 8.4). Deze test bewijst daarom de mechaniek (extract -> dovi_tool -m 2 ->
    mkvmerge met --default-duration -> libsvtav1 -dolbyvision 1) op een 8.1-
    invoer; échte P7 MEL/FEL staat als 'nog niet getest' in
    docs/AV1_DV_PLAYBACK_MATRIX.md.
    """

    _requires_dv_toolchain()
    if not (shutil.which("dovi_tool") and shutil.which("mkvmerge")):
        pytest.skip("dovi_tool/mkvmerge ontbreken")
    source = mg_fixture("mg-dv81.mkv")
    info = probe(str(source))
    assert info is not None
    steps, prepared = commandos.dv7_naar_81(str(source), str(info["fps"]), str(tmp_path))
    for step in steps:
        subprocess.run(step, check=True, capture_output=True)
    converted = probe(prepared)
    assert converted is not None and converted["dv_profile"] == 8
    assert abs(converted["duration"] - info["duration"]) < 0.5  # timing behouden
    assert len(converted["audio"]) == len(info["audio"])
    target = tmp_path / "p7route.mkv"
    decision = Besluit(
        actie="hercoderen",
        baan="cpu",
        encoder="libsvtav1",
        dolby_vision=True,
        behoud_hdr=True,
        audio_houden=[a["index"] for a in converted["audio"]],
    )
    subprocess.run(
        commandos.encode_cpu(
            converted,
            decision,
            {**STANDAARD, "crf_720": 50, "preset_720": 12},
            prepared,
            str(target),
        ),
        check=True,
        capture_output=True,
    )
    ok, reason, details = controleer_dv({**info, "dv_profile": 7, "dv_compat": 6}, target)
    assert ok, reason
    assert details["profile"] == "10.1"
