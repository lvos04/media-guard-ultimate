"""Brug-API (packages/contracts/encoder-bridge.md), auth en handoff-modus.

Encodes zijn echt (libsvtav1, preset 12, ≈4 s 720p) en worden teruggelezen.
"""

from __future__ import annotations

import os
import sqlite3
from pathlib import Path

import pytest
from conftest import MG_TOKEN, TESTDATA, mg_fixture
from fastapi.testclient import TestClient
from mg_helpers import (
    FAST,
    HEADERS,
    container_settings,
    copy_into_media,
    run_bridge_job,
)

from app.config import ConfigError, Settings
from app.db import Database
from app.main import create_app
from app.media.probe import probe
from app.mediaguard.capabilities import apply_lane_gates
from app.vervanging import file_sha256

pytestmark = pytest.mark.usefixtures("mg_testdata")
MgClient = tuple[TestClient, Database, Settings]


# ── Auth en fail-closed ─────────────────────────────────────────────────────


def test_zonder_geconfigureerd_token_is_de_brug_dicht(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.delenv("MG_INTERNAL_TOKEN", raising=False)
    configured = container_settings(tmp_path)
    with TestClient(create_app(configured, Database(configured), start_planner=False)) as client:
        assert client.get("/api/mediaguard/v1/health", headers=HEADERS).status_code == 503
        # In containermodus is ook de oude /api dicht zonder token.
        assert client.get("/api/status").status_code == 503


def test_token_vereist_op_brug_en_legacy_api(
    mg_client: MgClient,
) -> None:
    client, _database, _settings = mg_client
    assert client.get("/api/mediaguard/v1/health").status_code == 401
    assert (
        client.get("/api/mediaguard/v1/health", headers={"X-MediaGuard-Token": "fout" * 10})
    ).status_code == 401
    assert client.get("/api/libraries").status_code == 401
    assert client.get("/api/libraries", headers=HEADERS).status_code == 200
    response = client.get("/api/mediaguard/v1/health", headers=HEADERS)
    assert response.status_code == 200
    body = response.json()
    assert body["av1_commit"] == "4b31986b8e91bc3d03f5372436e02644efe0c41d"
    assert body["replacement_mode"] == "handoff"
    assert body["container_mode"] is True


def test_brug_weigert_verzoeken_via_de_browser_gateway(
    mg_client: MgClient,
) -> None:
    """Core's gateway (/api/v1/encoder/*) zet X-MediaGuard-Gateway. Ook mét geldige token
    is de brug dan dicht; de gewone engine-API blijft via de gateway werken."""
    client, _database, _settings = mg_client
    via_gateway = {**HEADERS, "X-MediaGuard-Gateway": "1"}
    response = client.get("/api/mediaguard/v1/health", headers=via_gateway)
    assert response.status_code == 403
    assert client.post("/api/mediaguard/v1/jobs/1/committed", headers=via_gateway,
                       json={}).status_code == 403
    assert client.get("/api/libraries", headers=via_gateway).status_code == 200


def test_capabilities_meldt_gpu_baan_uit_met_reden_en_cpu_aan(
    mg_client: MgClient,
) -> None:
    client, database, _settings = mg_client
    caps = client.get("/api/mediaguard/v1/capabilities", headers=HEADERS).json()
    assert caps["encoders"]["libsvtav1"] is True
    assert caps["lanes"]["cpu"]["enabled"] is True
    assert caps["lanes"]["gpu"]["enabled"] is False
    assert caps["lanes"]["gpu"]["reason"]
    assert {route["route"] for route in caps["dv_routes"]} >= {"8.1", "8.4", "7_mel", "7_fel", "5"}
    gpu = database.fetch_one("SELECT gepauzeerd,pauze_reden FROM worker_config WHERE baan='gpu'")
    cpu = database.fetch_one("SELECT gepauzeerd FROM worker_config WHERE baan='cpu'")
    assert gpu is not None and int(gpu["gepauzeerd"]) == 1
    assert str(gpu["pauze_reden"]) == "MediaGuard: Geen GPU gevonden — encodes draaien op de CPU"
    assert cpu is not None and int(cpu["gepauzeerd"]) == 0


def test_lane_gate_overschrijft_nooit_een_handmatige_pauze(
    mg_client: MgClient,
) -> None:
    _client, database, _settings = mg_client
    database.execute(
        "UPDATE worker_config SET gepauzeerd=1,pauze_reden='handmatig' WHERE baan='gpu'"
    )
    caps = {
        "lanes": {
            "cpu": {"enabled": True, "reason": None},
            "gpu": {"enabled": True, "reason": None},
        }
    }
    apply_lane_gates(database, caps)
    row = database.fetch_one("SELECT gepauzeerd,pauze_reden FROM worker_config WHERE baan='gpu'")
    assert row is not None and int(row["gepauzeerd"]) == 1 and row["pauze_reden"] == "handmatig"


def test_ontbrekende_mediamount_is_fail_closed(tmp_path: Path) -> None:
    configured = container_settings(tmp_path, media_roots=(tmp_path / "bestaat-niet",))
    for path in (
        configured.config_root,
        configured.backup_root,
        configured.cache_root,
        configured.work_root,
        configured.effective_staging_root,
    ):
        path.mkdir(parents=True, exist_ok=True)
    with pytest.raises(ConfigError, match="Geen leesbare mediamount"):
        configured.validate_storage()


def test_pad_buiten_mediaroot_wordt_geweigerd(mg_client: MgClient, tmp_path: Path) -> None:
    client, _database, _settings = mg_client
    outside = tmp_path / "elders.mkv"
    outside.write_bytes(b"x")
    response = client.post("/api/mediaguard/v1/probe", json={"path": str(outside)}, headers=HEADERS)
    assert response.status_code == 403


def test_legacy_lan_en_smb_zijn_in_containermodus_uitgeschakeld(
    mg_client: MgClient,
) -> None:
    client, _database, _settings = mg_client
    response = client.patch(
        "/api/lan-toegang",
        json={
            "ingeschakeld": True,
            "bevestiging": "LAN-TOEGANG INSCHAKELEN",
            "wachtwoord": "een-lang-wachtwoord",
        },
        headers=HEADERS,
    )
    assert response.status_code == 409
    profile = client.get("/api/profielen", headers=HEADERS).json()[0]
    response = client.post(
        "/api/libraries",
        json={"naam": "SMB", "locatie": "smb://nas/media", "profiel_id": profile["id"]},
        headers=HEADERS,
    )
    # Containermodus: MediaGuard levert de bibliotheken; de encoder voegt niets toe.
    assert response.status_code == 409
    assert "MediaGuard beslist" in response.json()["detail"]


# ── Probe/plan ──────────────────────────────────────────────────────────────


def test_probe_geeft_audio_inventaris_met_rollen(
    mg_client: MgClient,
) -> None:
    client, _database, configured = mg_client
    source = copy_into_media(configured, mg_fixture("mg-dual-audio.mkv"), "anime/ep01.mkv")
    body = client.post("/api/mediaguard/v1/probe", json={"path": str(source)}, headers=HEADERS)
    assert body.status_code == 200, body.text
    inventory = body.json()["inventory"]
    roles = {item["index"]: item["role"] for item in inventory["audio"]}
    assert inventory["dual_audio"] is True
    assert roles == {1: "main", 2: "main", 3: "main", 4: "commentary", 5: "audio_description"}
    assert {item["language"] for item in inventory["audio"]} == {"eng", "jpn", "nld"}
    sub_roles = [item["role"] for item in inventory["subtitles"]]
    assert sub_roles == ["full", "forced", "signs_songs"]


def test_plan_voert_niets_uit_en_raakt_bron_niet(
    mg_client: MgClient,
) -> None:
    client, database, configured = mg_client
    source = copy_into_media(configured, mg_fixture("mg-dual-audio.mkv"), "anime/ep01.mkv")
    before = file_sha256(source)
    response = client.post(
        "/api/mediaguard/v1/plan", json={"path": str(source), "policy": FAST}, headers=HEADERS
    )
    assert response.status_code == 200, response.text
    plan = response.json()
    assert plan["action"] == "hercoderen"
    assert plan["actie"] == "hercoderen" and plan["reden"] == plan["reason"]
    assert plan["lane"] == "cpu"  # GPU-baan is uit: fail-closed naar CPU
    assert plan["decision"]["audio_houden"] == [1, 2, 3, 4, 5]
    assert database.fetch_one("SELECT COUNT(*) FROM job")[0] == 0
    assert file_sha256(source) == before


# ── Handoff ─────────────────────────────────────────────────────────────────


def _create(client: TestClient, source: Path, **policy: object) -> int:
    response = client.post(
        "/api/mediaguard/v1/jobs",
        json={
            "source_path": str(source),
            "correlation_id": "c0ffee",
            "media_ref": {"instance_id": "sonarr-anime", "kind": "episode_file", "provider_id": 9},
            "policy": {**FAST, **policy},
        },
        headers=HEADERS,
    )
    assert response.status_code == 201, response.text
    return int(response.json()["job_id"])


def test_handoff_stopt_bij_awaiting_commit_en_muteert_de_bron_nooit(
    mg_client: MgClient,
) -> None:
    client, database, configured = mg_client
    source = copy_into_media(configured, mg_fixture("mg-dual-audio.mkv"), "anime/ep01.mkv")
    stat_before = source.stat()
    hash_before = file_sha256(source)
    job_id = _create(client, source)
    assert client.get(f"/api/mediaguard/v1/jobs/{job_id}", headers=HEADERS).json()["status"] == (
        "queued"
    )

    assert run_bridge_job(database, configured, job_id) is True

    job = client.get(f"/api/mediaguard/v1/jobs/{job_id}", headers=HEADERS).json()
    assert job["status"] == "awaiting_commit", job
    assert job["verification"]["passed"] is True
    assert {check["name"] for check in job["verification"]["checks"]} >= {
        "duration",
        "audio_tracks",
        "subtitle_tracks",
        "attachments",
        "size",
        "min_saving",
    }
    staged = Path(job["staged_output"]["path"])
    assert staged == configured.effective_staging_root / str(job_id) / "out.mkv"
    assert staged.is_file()
    assert job["staged_output"]["size"] == staged.stat().st_size
    assert job["staged_output"]["sha256"] == file_sha256(staged)
    snapshot = job["source_snapshot"]
    assert snapshot["inode"] == stat_before.st_ino
    assert snapshot["mtime_ns"] == stat_before.st_mtime_ns
    assert snapshot["nlink"] == 1
    assert job["correlation_id"] == "c0ffee"
    assert job["media_ref"]["instance_id"] == "sonarr-anime"
    # P2: de bron is byte-, inode- en mtime-gelijk gebleven.
    assert file_sha256(source) == hash_before
    assert source.stat().st_ino == stat_before.st_ino
    assert source.stat().st_mtime_ns == stat_before.st_mtime_ns
    # Cache is opgeruimd; alleen staging bevat het resultaat.
    assert not (configured.cache_root / str(job_id)).exists()

    # Core leest het gestagede resultaat zelf terug via /probe.
    staged_probe = client.post(
        "/api/mediaguard/v1/probe", json={"path": str(staged)}, headers=HEADERS
    )
    assert staged_probe.status_code == 200, staged_probe.text
    assert staged_probe.json()["probe"]["codec"] == "av1"
    assert staged_probe.json()["dv_el_type"] is None

    # P6-acceptatie: dual audio (eng+jpn) + nld + commentaar + AD + alle subs + font.
    result = probe(str(staged))
    assert result is not None and result["codec"] == "av1"
    assert [a["taal"] for a in result["audio"]] == ["eng", "jpn", "dut", "eng", "eng"]
    assert len(result["subs"]) == 3
    assert any(s["forced"] for s in result["subs"])
    assert result["attachments"] == 1

    # Dubbele job op hetzelfde bestand is geblokkeerd zolang core niet besliste.
    duplicate = client.post(
        "/api/mediaguard/v1/jobs",
        json={"source_path": str(source), "policy": FAST},
        headers=HEADERS,
    )
    assert duplicate.status_code == 409

    # Core activeert (zijn recovery-worker deed de vervanging) -> staging weg.
    wrong = client.post(
        f"/api/mediaguard/v1/jobs/{job_id}/committed", json={"sha256": "0" * 64}, headers=HEADERS
    )
    assert wrong.status_code == 409
    done = client.post(
        f"/api/mediaguard/v1/jobs/{job_id}/committed",
        json={"sha256": job["staged_output"]["sha256"]},
        headers=HEADERS,
    )
    assert done.status_code == 200, done.text
    assert done.json()["status"] == "committed"
    assert not staged.parent.exists()
    assert file_sha256(source) == hash_before  # de encoder schreef nog steeds niets
    saving = database.fetch_one(
        "SELECT bytes_voor,bytes_na FROM besparing WHERE job_id=?", (job_id,)
    )
    assert saving is not None and int(saving["bytes_na"]) < int(saving["bytes_voor"])
    # Idempotent: een herhaalde terugmelding (retry na time-out) is geen fout.
    again = client.post(f"/api/mediaguard/v1/jobs/{job_id}/committed", headers=HEADERS)
    assert again.status_code == 200, again.text
    assert again.json()["status"] == "committed"
    rejected_after = client.post(f"/api/mediaguard/v1/jobs/{job_id}/rejected", headers=HEADERS)
    assert rejected_after.status_code == 409


def test_rejected_ruimt_staging_op_en_laat_bron_staan(
    mg_client: MgClient,
) -> None:
    client, database, configured = mg_client
    source = copy_into_media(configured, mg_fixture("mg-dual-audio.mkv"), "tv/s01e02.mkv")
    hash_before = file_sha256(source)
    job_id = _create(client, source)
    assert run_bridge_job(database, configured, job_id) is True
    staged = configured.effective_staging_root / str(job_id)
    assert staged.is_dir()
    response = client.post(
        f"/api/mediaguard/v1/jobs/{job_id}/rejected", json={"reason": "canary"}, headers=HEADERS
    )
    assert response.status_code == 200
    assert response.json()["status"] == "rejected"
    assert not staged.exists()
    assert file_sha256(source) == hash_before


def test_cancel_wachtend_en_awaiting_commit(
    mg_client: MgClient,
) -> None:
    client, database, configured = mg_client
    first = copy_into_media(configured, mg_fixture("mg-dual-audio.mkv"), "tv/a.mkv")
    second = copy_into_media(configured, mg_fixture("mg-dual-audio.mkv"), "tv/b.mkv")
    queued = _create(client, first)
    response = client.post(f"/api/mediaguard/v1/jobs/{queued}/cancel", headers=HEADERS)
    assert response.json()["status"] == "cancelled"
    staged_job = _create(client, second)
    assert run_bridge_job(database, configured, staged_job) is True
    response = client.post(f"/api/mediaguard/v1/jobs/{staged_job}/cancel", headers=HEADERS)
    assert response.json()["status"] == "cancelled"
    assert not (configured.effective_staging_root / str(staged_job)).exists()
    assert (
        client.post(f"/api/mediaguard/v1/jobs/{staged_job}/cancel", headers=HEADERS).status_code
        == 409
    )


def test_te_weinig_besparing_houdt_origineel(
    mg_client: MgClient,
) -> None:
    client, database, configured = mg_client
    source = copy_into_media(configured, mg_fixture("mg-dual-audio.mkv"), "films/f.mkv")
    job_id = _create(client, source, min_saving_percent=95)
    assert run_bridge_job(database, configured, job_id) is False
    job = client.get(f"/api/mediaguard/v1/jobs/{job_id}", headers=HEADERS).json()
    assert job["status"] == "skipped"
    assert "onder minimum" in job["skip_reason"]
    assert job["staged_output"] is None
    assert not (configured.effective_staging_root / str(job_id)).exists()


def test_gewijzigde_bron_tijdens_job_faalt_zonder_staging(
    mg_client: MgClient,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    client, database, configured = mg_client
    source = copy_into_media(configured, mg_fixture("mg-dual-audio.mkv"), "films/g.mkv")
    job_id = _create(client, source)
    import app.uitvoerder as uitvoerder

    original = uitvoerder.verify_result

    def touch_then_verify(*args: object, **kwargs: object) -> object:
        os.utime(source, None)  # bv. Sonarr importeert een nieuwe release
        return original(*args, **kwargs)  # type: ignore[arg-type]

    monkeypatch.setattr(uitvoerder, "verify_result", touch_then_verify)
    assert run_bridge_job(database, configured, job_id) is False
    job = client.get(f"/api/mediaguard/v1/jobs/{job_id}", headers=HEADERS).json()
    assert job["status"] == "failed"
    assert "gewijzigd" in job["error"]
    assert not (configured.effective_staging_root / str(job_id)).exists()


def test_hardlink_wordt_in_handoff_gemeld_niet_verbroken(
    mg_client: MgClient,
) -> None:
    client, database, configured = mg_client
    source = copy_into_media(configured, mg_fixture("mg-dual-audio.mkv"), "films/h.mkv")
    seed = source.with_name("seed.mkv")
    os.link(source, seed)
    job_id = _create(client, source)
    assert run_bridge_job(database, configured, job_id) is True
    job = client.get(f"/api/mediaguard/v1/jobs/{job_id}", headers=HEADERS).json()
    assert job["source_snapshot"]["nlink"] == 2
    assert source.stat().st_ino == seed.stat().st_ino  # hardlink intact


def test_migratie_016_is_additief(tmp_path: Path) -> None:
    configured = container_settings(tmp_path)
    configured.config_root.mkdir(parents=True)
    database = Database(configured)
    database.initialize()
    with sqlite3.connect(configured.database_path) as connection:
        columns = {row[1] for row in connection.execute("PRAGMA table_info(job)")}
        tables = {row[0] for row in connection.execute("SELECT name FROM sqlite_master")}
    assert {"mg_staged_sha256", "mg_snapshot", "mg_vervangbaar"} <= columns
    assert {"trial_run", "encoding_trials", "quality_measurements"} <= tables


def test_legacy_direct_modus_blijft_default_buiten_container() -> None:
    from app.config import settings

    assert settings.container_mode is False or os.environ.get("MG_CONTAINER_MODE")
    assert settings.replacement_mode in {"direct", "handoff"}
    assert TESTDATA.is_dir()
    assert MG_TOKEN


def test_containermodus_uit_env(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("MG_CONTAINER_MODE", "1")
    monkeypatch.setenv("PIPELINE_ROOT", "/work")
    monkeypatch.setenv("CONFIG_ROOT", "/config")
    monkeypatch.setenv("MG_MEDIA_ROOTS", "/media/tv:/media/films")
    monkeypatch.delenv("AV1_REPLACEMENT_MODE", raising=False)
    configured = Settings.from_env()
    assert configured.container_mode is True
    assert configured.replacement_mode == "handoff"
    assert configured.backup_root == Path("/config/backups")
    assert configured.cache_root == Path("/work/cache")
    assert configured.effective_staging_root == Path("/work/staging")
    assert configured.media_roots == (Path("/media/tv"), Path("/media/films"))
    monkeypatch.setenv("AV1_REPLACEMENT_MODE", "direct")
    assert Settings.from_env().replacement_mode == "direct"
    monkeypatch.setenv("AV1_REPLACEMENT_MODE", "yolo")
    with pytest.raises(ConfigError):
        Settings.from_env()
    monkeypatch.delenv("MG_CONTAINER_MODE")
    monkeypatch.delenv("AV1_REPLACEMENT_MODE")
    assert Settings.from_env().replacement_mode == "direct"  # native Ubuntu ongewijzigd


def test_health_heft_een_eigen_failclosed_pauze_op_als_de_baan_weer_kan(
    mg_client: MgClient,
) -> None:
    client, database, _settings = mg_client
    database.execute(
        "UPDATE worker_config SET gepauzeerd=1,"
        "pauze_reden='MediaGuard: CPU-baan uit: ffmpeg zonder libsvtav1' WHERE baan='cpu'"
    )
    assert client.get("/api/mediaguard/v1/health", headers=HEADERS).status_code == 200
    row = database.fetch_one("SELECT gepauzeerd FROM worker_config WHERE baan='cpu'")
    assert row is not None and int(row["gepauzeerd"]) == 0


def test_joblijst_is_licht_en_detail_volledig(mg_client: MgClient) -> None:
    """M16: de lijst laat grote JSON weg; GET /api/jobs/{id} en ?id= geven één job."""

    client, database, configured = mg_client
    source = copy_into_media(configured, mg_fixture("mg-dual-audio.mkv"), "tv/lijst.mkv")
    job_id = _create(client, source)
    database.execute("UPDATE job SET bron_probe=? WHERE id=?", ("x" * 50_000, job_id))
    listing = client.get("/api/jobs", headers=HEADERS).json()
    item = next(row for row in listing["items"] if row["id"] == job_id)
    assert "bron_probe" not in item and "mg_policy" not in item
    assert item["pad"] == str(source) and item["status"]
    one = client.get(f"/api/jobs?id={job_id}", headers=HEADERS).json()
    assert one["totaal"] == 1 and one["items"][0]["id"] == job_id
    detail = client.get(f"/api/jobs/{job_id}", headers=HEADERS).json()
    assert len(detail["bron_probe"]) == 50_000 and detail["mg_policy"]
    assert client.get("/api/jobs/999999", headers=HEADERS).status_code == 404


def test_volle_baan_maakt_geen_job_en_meldt_lane(mg_client: MgClient) -> None:
    """Verbeterpunt 6: MediaGuard geeft door welke banen vol zitten. Komt het bestand op
    zo'n baan uit, dan geen job (409 LANE_FULL); anders noemt het antwoord de baan."""
    client, database, configured = mg_client
    source = copy_into_media(configured, mg_fixture("mg-dual-audio.mkv"), "anime/vol.mkv")
    body = {"source_path": str(source), "correlation_id": "baan-vol",
            "media_ref": {"instance_id": "sonarr-anime", "kind": "episode_file",
                          "provider_id": 11},
            "policy": FAST}
    refused = client.post("/api/mediaguard/v1/jobs", json={**body, "avoid_lanes": ["cpu"]},
                          headers=HEADERS)
    assert refused.status_code == 409 and "LANE_FULL" in refused.text
    assert "cpu-baan" in refused.text
    assert database.fetch_one("SELECT COUNT(*) FROM job")[0] == 0
    assert database.fetch_one("SELECT COUNT(*) FROM besluit")[0] == 0
    bad = client.post("/api/mediaguard/v1/jobs", json={**body, "avoid_lanes": ["tpu"]},
                      headers=HEADERS)
    assert bad.status_code == 422
    ok = client.post("/api/mediaguard/v1/jobs", json={**body, "avoid_lanes": ["gpu"]},
                     headers=HEADERS)
    assert ok.status_code == 201, ok.text
    assert ok.json()["lane"] == "cpu"  # GPU-baan is uit in de test: fail-closed naar CPU
