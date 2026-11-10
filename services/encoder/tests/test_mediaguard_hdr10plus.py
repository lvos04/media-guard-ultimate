"""HDR10+ → AV1 (Q1.1): echte encode met FFmpeg 8.1 + libaom en per-frame read-back."""

from __future__ import annotations

from pathlib import Path
from typing import Any

import pytest
from conftest import mg_fixture
from fastapi.testclient import TestClient
from mg_helpers import FAST, HEADERS, copy_into_media, run_bridge_job

from app.config import Settings
from app.db import Database
from app.media import commandos
from app.media.hdr10plus_controle import hdr10plus_frames
from app.media.probe import probe
from app.vervanging import file_sha256

pytestmark = pytest.mark.usefixtures("mg_testdata")
MgClient = tuple[TestClient, Database, Settings]


def _requires_route(client: TestClient) -> None:
    caps = client.get("/api/mediaguard/v1/capabilities", headers=HEADERS).json()
    if not caps["hdr10plus_route_verified"]:
        pytest.skip(f"HDR10+-route niet beschikbaar: {caps['hdr10plus_route_reason']}")


def _job(client: TestClient, source: Path, **policy: Any) -> int:
    response = client.post(
        "/api/mediaguard/v1/jobs",
        json={"source_path": str(source), "policy": {**FAST, **policy}},
        headers=HEADERS,
    )
    assert response.status_code == 201, response.text
    body = response.json()
    assert body["status"] == "queued", body
    return int(body["job_id"])


def _view(client: TestClient, job_id: int) -> dict[str, Any]:
    return dict(client.get(f"/api/mediaguard/v1/jobs/{job_id}", headers=HEADERS).json())


def test_capabilities_meldt_hdr10plus_route(mg_client: MgClient) -> None:
    client, _database, _settings = mg_client
    caps = client.get("/api/mediaguard/v1/capabilities", headers=HEADERS).json()
    assert isinstance(caps["hdr10plus_route_verified"], bool)
    hdr10plus = caps["hdr10plus"]
    assert "libsvtav1" in hdr10plus and "av1_qsv" in hdr10plus
    if caps["hdr10plus_route_verified"]:
        assert hdr10plus["ffmpeg"].startswith("8.") and hdr10plus["encoder"] == "libaom-av1"
        assert "convert_verified" in hdr10plus["modes"]
    else:
        assert caps["hdr10plus_route_reason"]
        assert "convert_verified" not in hdr10plus["modes"]


def test_convert_verified_behoudt_hdr10plus_op_ieder_frame(mg_client: MgClient) -> None:
    client, database, configured = mg_client
    _requires_route(client)
    source = copy_into_media(configured, mg_fixture("mg-hdr10plus.mkv"), "films/h10p.mkv")
    before = file_sha256(source)
    plan = client.post(
        "/api/mediaguard/v1/plan",
        json={"path": str(source), "policy": {**FAST, "hdr10plus_mode": "convert_verified"}},
        headers=HEADERS,
    ).json()
    assert plan["actie"] == "hercoderen" and plan["encoder"] == "libaom-av1"
    assert plan["hdr"]["hdr10plus_route"] == "behouden" and plan["metadata_loss"] == []

    job_id = _job(client, source, hdr10plus_mode="convert_verified")
    assert run_bridge_job(database, configured, job_id) is True
    command = database.fetch_one("SELECT commando FROM job WHERE id=?", (job_id,))
    assert command is not None and "libaom-av1" in str(command["commando"])
    assert commandos.ffmpeg_hdr10plus_binary() in str(command["commando"])

    job = _view(client, job_id)
    assert job["status"] == "awaiting_commit", job
    assert job["metadata_loss"] == []
    details = job["verification"]["hdr"]["hdr10plus"]
    assert details["frames_compared"] == details["frames_identical"] == 96
    assert any(c["name"] == "hdr10plus" and c["passed"] for c in job["verification"]["checks"])

    # Onafhankelijke read-back van het gestagede bestand: alle 96 frames, alle velden.
    staged = Path(job["staged_output"]["path"])
    bron = hdr10plus_frames(source, "%+#200")
    uit = hdr10plus_frames(staged, "%+#200")
    assert len(bron) == len(uit) == 96
    assert all(uit[t] is not None and uit[t] == bron[t] for t in bron)
    assert ("maxscl", "1000/100000") in next(iter(uit.values()))  # arrays intact
    result, original = probe(str(staged)), probe(str(source))
    assert result is not None and original is not None
    assert result["codec"] == "av1" and result["hdr10plus"] is True
    assert result["mastering_display"] == original["mastering_display"]
    assert result["content_light"] == original["content_light"]
    assert result["kleur"]["transfer"] == "smpte2084"
    assert file_sha256(source) == before


def test_route_die_hdr10plus_verliest_faalt_en_houdt_bron(
    mg_client: MgClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    client, database, configured = mg_client
    _requires_route(client)
    source = copy_into_media(configured, mg_fixture("mg-hdr10plus.mkv"), "films/h10p-weg.mkv")
    before = file_sha256(source)
    # Simuleer een encoder die HDR10+ stil laat vallen (zoals libsvtav1 doet).
    monkeypatch.setattr(commandos, "encode_cpu_aom", commandos.encode_cpu)
    job_id = _job(client, source, hdr10plus_mode="convert_verified")
    assert run_bridge_job(database, configured, job_id) is False
    job = _view(client, job_id)
    assert job["status"] == "failed"
    assert "HDR10+" in job["error"]
    assert job["staged_output"] is None
    assert not (configured.effective_staging_root / str(job_id)).exists()
    assert file_sha256(source) == before


def test_expliciete_hdr10_fallback_meldt_metadataverlies(mg_client: MgClient) -> None:
    client, database, configured = mg_client
    source = copy_into_media(configured, mg_fixture("mg-hdr10plus.mkv"), "films/h10p-fb.mkv")
    plan = client.post(
        "/api/mediaguard/v1/plan",
        json={"path": str(source), "policy": {**FAST, "hdr10plus": "hdr10_fallback_explicit"}},
        headers=HEADERS,
    ).json()
    assert plan["metadata_loss"] == ["hdr10plus_dynamic_metadata"]
    assert any("VERLOREN" in w for w in plan["warnings"])
    job_id = _job(client, source, hdr10plus_mode="hdr10_fallback_explicit")
    assert run_bridge_job(database, configured, job_id) is True
    job = _view(client, job_id)
    assert job["status"] == "awaiting_commit"
    assert job["metadata_loss"] == ["hdr10plus_dynamic_metadata"]
    assert any("VERLOREN" in w for w in job["warnings"])
    result, original = probe(job["staged_output"]["path"]), probe(str(source))
    assert result is not None and original is not None
    assert result["hdr10plus"] is False  # eerlijk: dynamische metadata is weg
    assert result["mastering_display"] == original["mastering_display"]


def test_zonder_geverifieerde_route_wordt_convert_overgeslagen(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from conftest import MG_TOKEN
    from mg_helpers import container_settings

    from app.main import create_app

    monkeypatch.setenv("MG_INTERNAL_TOKEN", MG_TOKEN)
    monkeypatch.setenv("MG_FFMPEG_HDR10PLUS", str(tmp_path / "bestaat-niet" / "ffmpeg"))
    configured = container_settings(tmp_path)
    with TestClient(create_app(configured, Database(configured), start_planner=False)) as client:
        caps = client.get("/api/mediaguard/v1/capabilities", headers=HEADERS).json()
        assert caps["hdr10plus_route_verified"] is False
        assert "ontbreekt" in caps["hdr10plus_route_reason"]
        source = copy_into_media(configured, mg_fixture("mg-hdr10plus.mkv"), "films/x.mkv")
        plan = client.post(
            "/api/mediaguard/v1/plan",
            json={"path": str(source), "policy": {"hdr10plus_mode": "convert_verified"}},
            headers=HEADERS,
        ).json()
        assert plan["actie"] == "overslaan"
        assert "HDR10+-route niet beschikbaar" in plan["reden"]
