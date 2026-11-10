"""P4: per-title proefencodes, metingen, keuze en persistentie."""

from __future__ import annotations

import subprocess
from pathlib import Path

import pytest
from conftest import mg_fixture
from fastapi.testclient import TestClient
from mg_helpers import HEADERS, copy_into_media

from app.config import Settings
from app.db import Database
from app.mediaguard.trials import QUALITY_PROFILES, recommend, select_samples

pytestmark = pytest.mark.usefixtures("mg_testdata")
MgClient = tuple[TestClient, Database, Settings]


def test_sampleselectie_vermijdt_zwart_intro(tmp_path: Path) -> None:
    clip = tmp_path / "zwart-intro.mkv"
    subprocess.run(
        [
            "ffmpeg",
            "-hide_banner",
            "-loglevel",
            "error",
            "-y",
            "-f",
            "lavfi",
            "-i",
            "color=black:size=320x180:rate=24:duration=12",
            "-f",
            "lavfi",
            "-i",
            "testsrc2=size=320x180:rate=24:duration=12",
            "-filter_complex",
            "[0:v][1:v]concat=n=2:v=1[v]",
            "-map",
            "[v]",
            "-c:v",
            "libx264",
            "-preset",
            "ultrafast",
            str(clip),
        ],
        check=True,
    )
    samples, checks = select_samples(str(clip), 24.0, sample_seconds=3.0)
    assert checks["not_only_black"] is True
    assert checks["frames_analysed"] > 0
    assert samples and all(s.start >= 0 and s.start + s.duration <= 24.0 for s in samples)
    # Het venster op 15% (≈3,6 s) valt in het zwart en moet zijn opgeschoven.
    assert all(s.start >= 11.0 for s in samples), samples
    assert checks["scene_change_sample"] is True


def _trial(client: TestClient, source: Path, **extra: object) -> dict[str, object]:
    body = {
        "path": str(source),
        "samples": [{"start": 1.0, "duration": 2.0}],
        "settings": [
            {"encoder": "libsvtav1", "crf": 30, "preset": 12},
            {"encoder": "libsvtav1", "crf": 55, "preset": 12},
        ],
        "quality_profile": "custom",
        **extra,
    }
    response = client.post("/api/mediaguard/v1/trials", json=body, headers=HEADERS)
    assert response.status_code == 200, response.text
    return dict(response.json())


def test_proefmatrix_meet_en_bewaart_met_toolversies(mg_client: MgClient) -> None:
    client, database, configured = mg_client
    source = copy_into_media(configured, mg_fixture("mg-dual-audio.mkv"), "tv/trial.mkv")
    result = _trial(
        client, source, custom={"ssim_floor": 0.80, "vmaf_floor": 50, "min_saving_percent": 10}
    )
    assert result["status"] == "done"
    trials = result["trials"]
    assert isinstance(trials, list) and len(trials) == 2
    for trial in trials:
        assert trial["status"] == "klaar"
        assert trial["size"] > 0 and trial["bitrate_kbps"] > 0 and trial["encode_seconds"] > 0
        assert {"ssim", "psnr"} <= set(trial["metrics"])
    small, large = sorted(trials, key=lambda t: t["size"])
    assert small["crf_or_cq"] == 55
    recommendation = result["recommendation"]
    assert isinstance(recommendation, dict)
    assert recommendation["action"] == "encode"
    assert recommendation["recommended"]["crf"] == 55  # goedkoopste die de vloer haalt
    versions = result["tool_versions"]
    assert isinstance(versions, dict) and versions["ffmpeg"] and versions["svt_av1"]
    assert database.fetch_one("SELECT COUNT(*) FROM encoding_trials")[0] == 2
    assert database.fetch_one("SELECT COUNT(*) FROM quality_measurements")[0] >= 4
    run_id = result["trial_run_id"]
    again = client.get(f"/api/mediaguard/v1/trials/{run_id}", headers=HEADERS).json()
    assert again["recommendation"] == recommendation
    assert not list(configured.work_root.glob("trial-*"))  # werkmap opgeruimd


def test_onhaalbare_vloer_houdt_origineel(mg_client: MgClient) -> None:
    client, _database, configured = mg_client
    source = copy_into_media(configured, mg_fixture("mg-dual-audio.mkv"), "tv/trial2.mkv")
    # Beide vloeren onhaalbaar: met libvmaf (testimage) oordeelt de proef op VMAF,
    # zonder libvmaf op SSIM. Alleen ssim_floor zetten liet de VMAF-vloer op 93.
    result = _trial(
        client,
        source,
        custom={"ssim_floor": 0.99999, "vmaf_floor": 99.99, "min_saving_percent": 10},
    )
    recommendation = result["recommendation"]
    assert isinstance(recommendation, dict)
    assert recommendation["action"] == "keep_original"
    assert "kwaliteitsvloer" in recommendation["reason"]


def test_dv_proef_vereist_expliciete_dv_mode(mg_client: MgClient) -> None:
    client, _database, configured = mg_client
    source = copy_into_media(configured, mg_fixture("mg-dv81.mkv"), "films/dvtrial.mkv")
    response = client.post("/api/mediaguard/v1/trials", json={"path": str(source)}, headers=HEADERS)
    assert response.status_code == 409
    result = _trial(
        client, source, dv_mode="trial_copy", custom={"ssim_floor": 0.5, "min_saving_percent": 0}
    )
    recommendation = result["recommendation"]
    assert isinstance(recommendation, dict)
    assert recommendation["hdr_metric_caveat"]


def test_plan_met_trials_kiest_instelling(mg_client: MgClient) -> None:
    client, _database, configured = mg_client
    source = copy_into_media(configured, mg_fixture("mg-dual-audio.mkv"), "tv/plan.mkv")
    response = client.post(
        "/api/mediaguard/v1/plan",
        json={"path": str(source), "policy": {"quality_profile": "compact"}, "run_trials": True},
        headers=HEADERS,
    )
    assert response.status_code == 200, response.text
    plan = response.json()
    assert "smart" in plan
    smart = plan["smart"]
    assert smart["quality_profile"] == "compact"
    assert len(smart["samples"]) >= 1
    if smart["recommendation"]["action"] == "encode":
        assert plan["recommended_policy"]["crf"] is not None
    else:
        assert plan["actie"] == "overslaan"


def test_keuze_regels_zonder_meting_houden_origineel() -> None:
    profile = QUALITY_PROFILES["gebalanceerd"]
    result = recommend([], profile, {"is_hdr10": False}, vmaf_available=False)
    assert result["action"] == "keep_original"
    assert "onzeker" in result["reason"]
