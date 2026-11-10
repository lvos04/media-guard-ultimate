"""Containermodus: MediaGuard beslist, de encoder voert alleen uit.

Plus de idempotentie en randgevallen van de brug (committed/rejected/cancel,
correlation_id, staging-wezen) en de gedeelde handoff-statussen.
"""

from __future__ import annotations

import sqlite3
from pathlib import Path
from typing import Any

import pytest
from conftest import mg_fixture
from fastapi.testclient import TestClient
from mg_helpers import FAST, HEADERS, copy_into_media, run_bridge_job

from app.config import Settings
from app.db import Database
from app.mediaguard import handoff
from app.mediaguard.api import trials_refusal
from app.mediaguard.capabilities import CapabilityService
from app.mediaguard.handoff import sweep_orphan_staging
from app.mediaguard.trials import _sum_window_packets

pytestmark = pytest.mark.usefixtures("mg_testdata")
MgClient = tuple[TestClient, Database, Settings]


def _create(client: TestClient, source: Path, correlation: str = "mg-enc-1") -> int:
    response = client.post(
        "/api/mediaguard/v1/jobs",
        json={"source_path": str(source), "correlation_id": correlation, "policy": FAST},
        headers=HEADERS,
    )
    assert response.status_code == 201, response.text
    return int(response.json()["job_id"])


# ── A. Alleen uitvoeren ─────────────────────────────────────────────────────


def test_modusvlag_voor_ui_en_core(mg_client: MgClient) -> None:
    client, _database, _settings = mg_client
    mode = client.get("/api/modus", headers=HEADERS).json()
    assert mode["beslisser"] == "mediaguard"
    assert mode["alleen_uitvoeren"] is True
    assert mode["vervangmodus"] == "handoff"
    assert "besluit_goedkeuren" in mode["geblokkeerde_acties"]
    settings = client.get("/api/instellingen", headers=HEADERS).json()
    assert settings["beslisser"] == "mediaguard" and settings["container_modus"] is True


def test_containermodus_weigert_eigen_beslissingen(mg_client: MgClient) -> None:
    client, database, configured = mg_client
    source = copy_into_media(configured, mg_fixture("mg-dual-audio.mkv"), "films/a.mkv")
    job_id = _create(client, source)
    library = database.fetch_one(
        "SELECT f.library_id FROM job j JOIN bestand f ON f.id=j.bestand_id WHERE j.id=?",
        (job_id,),
    )
    assert library is not None
    library_id = int(library["library_id"])
    profile_id = client.get("/api/profielen", headers=HEADERS).json()[0]["id"]

    refused = [
        client.post(
            "/api/libraries",
            json={"naam": "x", "pad": str(configured.media_roots[0]), "profiel_id": profile_id},
            headers=HEADERS,
        ),
        client.post(f"/api/libraries/{library_id}/scan", headers=HEADERS),
        client.post(f"/api/libraries/{library_id}/droogloop", headers=HEADERS),
        client.patch(
            f"/api/libraries/{library_id}", json={"scan_interval": 86400}, headers=HEADERS
        ),
        client.request(
            "DELETE",
            f"/api/libraries/{library_id}",
            json={"bevestiging": "BIBLIOTHEEK VERWIJDEREN"},
            headers=HEADERS,
        ),
        client.post("/api/besluiten/goedkeuren", json={"ids": [1]}, headers=HEADERS),
        client.post("/api/besluiten/afwijzen", json={"ids": [1]}, headers=HEADERS),
        client.patch(
            "/api/besluiten/1", json={"baan": "cpu", "encoder": "libsvtav1"}, headers=HEADERS
        ),
        client.patch(
            "/api/ai-controller",
            json={"ingeschakeld": True, "modus": "schaduw"},
            headers=HEADERS,
        ),
        client.post("/api/ai-controller/analyseer", headers=HEADERS),
        client.patch(
            "/api/functies/canary_limiet", json={"ingeschakeld": True}, headers=HEADERS
        ),
        client.post("/api/profielen", json={"naam": "eigen"}, headers=HEADERS),
        client.patch(
            "/api/instellingen", json={"hardlink_modus": "overslaan"}, headers=HEADERS
        ),
    ]
    for response in refused:
        assert response.status_code == 409, response.text
        assert "MediaGuard beslist" in response.json()["detail"]
    # Naam wijzigen is geen beslissing; uitvoeringsinstellingen blijven bedienbaar.
    renamed = client.patch(
        f"/api/libraries/{library_id}", json={"naam": "Films"}, headers=HEADERS
    )
    assert renamed.status_code == 200
    assert client.patch("/api/workers/cpu", json={"aantal": 1}, headers=HEADERS).status_code == 200


def test_opnieuw_alleen_voor_bridge_jobs(mg_client: MgClient) -> None:
    client, database, configured = mg_client
    source = copy_into_media(configured, mg_fixture("mg-dual-audio.mkv"), "films/b.mkv")
    job_id = _create(client, source)
    database.execute("UPDATE job SET status='mislukt' WHERE id=?", (job_id,))
    assert client.post(f"/api/jobs/{job_id}/opnieuw", headers=HEADERS).status_code == 200
    database.execute("UPDATE job SET status='mislukt',mg_bron=NULL WHERE id=?", (job_id,))
    response = client.post(f"/api/jobs/{job_id}/opnieuw", headers=HEADERS)
    assert response.status_code == 409
    assert "MediaGuard beslist" in response.json()["detail"]


def test_scheduler_en_ai_controller_starten_niet_in_containermodus(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from conftest import MG_TOKEN
    from mg_helpers import container_settings

    from app.main import create_app

    monkeypatch.setenv("MG_INTERNAL_TOKEN", MG_TOKEN)
    configured = container_settings(tmp_path)
    application = create_app(configured, Database(configured), start_planner=True)
    started: list[str] = []

    async def fake_scheduler() -> None:
        started.append("scan")

    async def fake_ai() -> None:
        started.append("ai")

    async def fake_planner() -> None:
        started.append("planner")

    monkeypatch.setattr(application.state.scan_scheduler, "run_forever", fake_scheduler)
    monkeypatch.setattr(application.state.ai_controller, "run_forever", fake_ai)
    monkeypatch.setattr(application.state.planner, "run_forever", fake_planner)
    with TestClient(application):
        pass
    assert started == ["planner"]


# ── B. Brug: idempotent en racebestendig ────────────────────────────────────


def test_jobs_idempotent_op_correlation_id(mg_client: MgClient) -> None:
    client, database, configured = mg_client
    source = copy_into_media(configured, mg_fixture("mg-dual-audio.mkv"), "tv/e1.mkv")
    first = _create(client, source, "mg-enc-7")
    assert _create(client, source, "mg-enc-7") == first
    count = database.fetch_one("SELECT COUNT(*) AS n FROM job WHERE mg_correlation_id='mg-enc-7'")
    assert count is not None and int(count["n"]) == 1
    # Zelfde id voor een ander bestand (bijv. nieuwe core-database) = nieuwe job.
    other = copy_into_media(configured, mg_fixture("mg-dual-audio.mkv"), "tv/e2.mkv")
    assert _create(client, other, "mg-enc-7") != first


def test_rejected_is_idempotent_en_lange_reden_wordt_ingekort(mg_client: MgClient) -> None:
    client, database, configured = mg_client
    source = copy_into_media(configured, mg_fixture("mg-dual-audio.mkv"), "tv/r.mkv")
    job_id = _create(client, source)
    assert run_bridge_job(database, configured, job_id) is True
    body = {"reason": "x" * 5000}
    first = client.post(f"/api/mediaguard/v1/jobs/{job_id}/rejected", json=body, headers=HEADERS)
    assert first.status_code == 200, first.text
    row = database.fetch_one("SELECT fout FROM job WHERE id=?", (job_id,))
    assert row is not None and len(str(row["fout"])) == 1000
    again = client.post(f"/api/mediaguard/v1/jobs/{job_id}/rejected", json=body, headers=HEADERS)
    assert again.status_code == 200 and again.json()["status"] == "rejected"
    assert (
        client.post(f"/api/mediaguard/v1/jobs/{job_id}/committed", headers=HEADERS).status_code
        == 409
    )


def test_annuleren_tijdens_klaarzetten_laat_geen_staging_achter(
    mg_client: MgClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    client, database, configured = mg_client
    source = copy_into_media(configured, mg_fixture("mg-dual-audio.mkv"), "tv/c.mkv")
    job_id = _create(client, source)
    original = handoff.move_to_staging

    def cancel_while_moving(source_path: Path, target: Path) -> str:
        # MediaGuard annuleert precies terwijl het resultaat naar staging gaat.
        response = client.post(f"/api/mediaguard/v1/jobs/{job_id}/cancel", headers=HEADERS)
        assert response.status_code == 200
        return original(source_path, target)

    monkeypatch.setattr(handoff, "move_to_staging", cancel_while_moving)
    assert run_bridge_job(database, configured, job_id) is False
    row = database.fetch_one("SELECT status,mg_staged_pad FROM job WHERE id=?", (job_id,))
    assert row is not None and row["status"] == "afgebroken" and row["mg_staged_pad"] is None
    assert not (configured.effective_staging_root / str(job_id)).exists()
    attempt = database.fetch_one(
        "SELECT status FROM job_poging WHERE job_id=? ORDER BY poging DESC", (job_id,)
    )
    assert attempt is not None and attempt["status"] == "afgebroken"


def test_stagingwezen_worden_opgeruimd(mg_client: MgClient) -> None:
    client, database, configured = mg_client
    source = copy_into_media(configured, mg_fixture("mg-dual-audio.mkv"), "tv/s.mkv")
    job_id = _create(client, source)
    root = configured.effective_staging_root
    orphan = root / str(job_id)
    orphan.mkdir(parents=True)
    (orphan / "out.mkv").write_bytes(b"half")
    unknown = root / "999999"
    unknown.mkdir()
    foreign = root / "niet-van-ons"
    foreign.mkdir()
    database.execute("UPDATE job SET status='mislukt' WHERE id=?", (job_id,))
    assert sorted(sweep_orphan_staging(database, configured)) == [job_id, 999999]
    assert not orphan.exists() and not unknown.exists() and foreign.exists()
    # Een job die echt op core wacht, blijft staan.
    database.execute("UPDATE job SET status='wacht_op_commit' WHERE id=?", (job_id,))
    orphan.mkdir()
    assert sweep_orphan_staging(database, configured) == []
    assert orphan.exists()


def test_proefweigering_geldt_ook_voor_plan_met_trials() -> None:
    assert trials_refusal({"hdr10plus": True}, None) is not None
    assert trials_refusal({"dv_profile": 5}, "trial_copy") is not None
    assert trials_refusal({"dv_profile": 8}, None) is not None
    assert trials_refusal({"dv_profile": 8}, "trial_copy") is None
    assert trials_refusal({}, None) is None


def test_samplebytes_tellen_alleen_het_venster() -> None:
    output = "\n".join(
        [
            "pts_time=9.000|size=1000",  # keyframe vóór het venster
            "pts_time=10.000|size=10",
            "pts_time=11.500|size=20",
            "pts_time=12.000|size=5000",  # eerste pakket ná het venster
            "pts_time=N/A|size=7",
        ]
    )
    assert _sum_window_packets(output, 10.0, 12.0) == 30


def test_health_niet_degraded_zonder_gpu(mg_client: MgClient) -> None:
    client, _database, _settings = mg_client
    body = client.get("/api/mediaguard/v1/health", headers=HEADERS).json()
    # Geen /dev/dri doorgegeven: CPU-only is een geldige opstelling.
    assert not any(reason.startswith("gpu-baan") for reason in body["reasons"]), body
    assert any(note.startswith("gpu-baan") for note in body["notes"])


async def test_mislukte_qsv_proef_wordt_opnieuw_geprobeerd(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from mg_helpers import container_settings

    import app.mediaguard.capabilities as capabilities

    render = tmp_path / "renderD128"
    render.write_bytes(b"")
    service = CapabilityService(container_settings(tmp_path, render_node=render))
    service.cached = {"mounts": {}}
    service.qsv_probe = {"geslaagd": False}
    service.detected_monotonic = 0.0  # lang geleden
    probes: list[str] = []
    original_report = capabilities.dependency_report

    async def report_with_qsv(render_node: Path | None = None) -> dict[str, Any]:
        return {**(await original_report(render_node)), "av1_qsv": True}

    async def fake_qsv_test(_settings: Settings, **_kw: Any) -> dict[str, Any]:
        probes.append("qsv")
        return {"geslaagd": True}

    monkeypatch.setattr(capabilities, "dependency_report", report_with_qsv)
    monkeypatch.setattr(capabilities, "qsv_test", fake_qsv_test)
    monkeypatch.setattr(capabilities, "qsv_runtime_report", lambda: {"complete": True})
    result = await service.detect(max_age_seconds=300)
    # Verlopen + eerder mislukte proef => opnieuw proberen; de GPU-baan herstelt.
    assert probes == ["qsv"]
    assert result["qsv"]["ready"] is True
    # Geslaagd en vers: geen nieuwe proef bij iedere health-aanroep.
    await service.detect(max_age_seconds=300)
    assert probes == ["qsv"]


def _gpu_service(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, probes: list[str]
) -> tuple[CapabilityService, Path]:
    from mg_helpers import container_settings

    import app.mediaguard.capabilities as capabilities

    render = tmp_path / "dri" / "renderD128"
    original_report = capabilities.dependency_report

    async def report_with_qsv(render_node: Path | None = None) -> dict[str, Any]:
        return {**(await original_report(render_node)), "av1_qsv": True}

    async def fake_qsv_test(_settings: Settings, **_kw: Any) -> dict[str, Any]:
        probes.append("qsv")
        return {"geslaagd": True}

    monkeypatch.setattr(capabilities, "dependency_report", report_with_qsv)
    monkeypatch.setattr(capabilities, "qsv_test", fake_qsv_test)
    monkeypatch.setattr(capabilities, "qsv_runtime_report", lambda: {"complete": True})
    return CapabilityService(container_settings(tmp_path, render_node=render)), render


async def test_geen_gpu_is_rustige_cpu_status(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from app.mediaguard.capabilities import NO_GPU_TEXT, honest_dependencies

    probes: list[str] = []
    service, _render = _gpu_service(tmp_path, monkeypatch, probes)
    caps = await service.detect(refresh=True, run_qsv_probe=True)
    assert caps["gpu_status"] == {"state": "geen_gpu", "severity": "info",
                                  "message": NO_GPU_TEXT, "active": False}
    assert caps["lanes"]["gpu"]["reason"] == NO_GPU_TEXT
    assert caps["lanes"]["cpu"]["enabled"] is True
    assert probes == []  # zonder kaart geen proefencode
    deps = honest_dependencies({"qsv_gereed": True}, caps)
    assert deps["qsv_gereed"] is False


async def test_later_doorgegeven_gpu_wordt_vanzelf_gebruikt(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    probes: list[str] = []
    service, render = _gpu_service(tmp_path, monkeypatch, probes)
    first = await service.detect(refresh=True, run_qsv_probe=True)
    assert first["lanes"]["gpu"]["enabled"] is False
    # De kaart verschijnt; de bewaking (detect zonder argumenten) ziet het meteen.
    render.parent.mkdir(parents=True)
    render.write_bytes(b"")
    caps = await service.detect()
    assert probes == ["qsv"]
    assert caps["gpu_status"]["state"] == "actief"
    assert caps["lanes"]["gpu"]["enabled"] is True
    # Niets veranderd: geen nieuwe proef bij de volgende ronde.
    await service.detect()
    assert probes == ["qsv"]
    # Na 6 uur wordt een geslaagde proef opnieuw bevestigd.
    assert service.probe_monotonic is not None
    service.probe_monotonic -= 6 * 3600 + 1
    await service.detect()
    assert probes == ["qsv", "qsv"]


async def test_niet_intel_gpu_wordt_niet_getest(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    import app.mediaguard.capabilities as capabilities

    probes: list[str] = []
    service, render = _gpu_service(tmp_path, monkeypatch, probes)
    render.parent.mkdir(parents=True)
    render.write_bytes(b"")
    monkeypatch.setattr(capabilities, "render_vendor", lambda _node: "0x1a03")  # BMC-chip
    caps = await service.detect(refresh=True, run_qsv_probe=True)
    assert probes == []
    assert caps["gpu_status"]["state"] == "geen_intel"
    assert caps["gpu_status"]["severity"] == "info"


def test_pick_render_node_kiest_intel(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from mg_helpers import container_settings

    import app.mediaguard.capabilities as capabilities

    nodes = [Path("/dev/dri/renderD128"), Path("/dev/dri/renderD129")]
    monkeypatch.delenv("MG_RENDER_NODE", raising=False)
    monkeypatch.setattr(capabilities, "_render_nodes", lambda: nodes)
    monkeypatch.setattr(capabilities, "render_vendor",
                        lambda node: "0x8086" if node.name == "renderD129" else "0x1002")
    settings = container_settings(tmp_path, render_node=capabilities.DEFAULT_RENDER_NODE)
    assert capabilities.pick_render_node(settings) == nodes[1]
    explicit = container_settings(tmp_path, render_node=tmp_path / "renderD200")
    assert capabilities.pick_render_node(explicit) == tmp_path / "renderD200"


def test_qsv_runtime_rapport_noemt_ontbrekende_delen() -> None:
    from app.mediaguard.capabilities import qsv_runtime_report

    report = qsv_runtime_report()
    assert set(report) >= {"libvpl", "libmfx_gen", "intel_media_driver", "complete", "missing"}
    assert report["complete"] == (not report["missing"])


def test_een_statusregel_per_baan() -> None:
    from app.live import lane_summary

    paused = lane_summary({"baan": "gpu", "aantal": 4, "gepauzeerd": 1,
                           "pauze_reden": "MediaGuard: Geen GPU gevonden — encodes draaien op "
                           "de CPU"}, 0)
    assert paused["status"] == "Geen GPU gevonden — encodes draaien op de CPU"
    busy = lane_summary({"baan": "cpu", "aantal": 2, "gepauzeerd": 0, "pauze_reden": None}, 1)
    assert busy["status"] == "1 van 2 tegelijk bezig"
    assert "worker" not in (paused["status"] + busy["status"]).lower()


# ── C. Gedeelde statussen ───────────────────────────────────────────────────


def test_gestaged_resultaat_bewaakt_het_bestand(mg_client: MgClient) -> None:
    client, database, configured = mg_client
    source = copy_into_media(configured, mg_fixture("mg-dual-audio.mkv"), "tv/u.mkv")
    job_id = _create(client, source)
    database.execute("UPDATE job SET status='wacht_op_commit' WHERE id=?", (job_id,))
    row = database.fetch_one("SELECT besluit_id,bestand_id FROM job WHERE id=?", (job_id,))
    assert row is not None
    with pytest.raises(sqlite3.IntegrityError):
        database.execute_insert(
            "INSERT INTO job(besluit_id,bestand_id,baan,status,prioriteit) "
            "VALUES (?,?,'cpu','wacht',0)",
            (int(row["besluit_id"]), int(row["bestand_id"])),
        )
    response = client.post(
        "/api/mediaguard/v1/jobs",
        json={"source_path": str(source), "correlation_id": "ander", "policy": FAST},
        headers=HEADERS,
    )
    assert response.status_code == 409


def test_commit_zet_klaar_op_en_telt_als_voltooid(mg_client: MgClient) -> None:
    from app.live import _queue_counts

    client, database, configured = mg_client
    source = copy_into_media(configured, mg_fixture("mg-dual-audio.mkv"), "tv/k.mkv")
    job_id = _create(client, source)
    assert run_bridge_job(database, configured, job_id) is True
    assert _queue_counts(database)["wacht_op_commit"] == 1
    sha = client.get(f"/api/mediaguard/v1/jobs/{job_id}", headers=HEADERS).json()[
        "staged_output"
    ]["sha256"]
    done = client.post(
        f"/api/mediaguard/v1/jobs/{job_id}/committed", json={"sha256": sha}, headers=HEADERS
    )
    assert done.status_code == 200
    counts = _queue_counts(database)
    assert counts["wacht_op_commit"] == 0 and counts["klaar_vandaag"] == 1
    libraries = client.get("/api/libraries", headers=HEADERS).json()
    assert sum(int(item["verwerkt"]) for item in libraries) == 1


def test_device_wissel_na_remount_blokkeert_containerjobs_niet(mg_client: MgClient) -> None:
    client, database, configured = mg_client
    source = copy_into_media(configured, mg_fixture("mg-dual-audio.mkv"), "tv/d.mkv")
    job_id = _create(client, source)
    # ZFS-remount/herstart: zelfde dataset, ander st_dev.
    database.execute("UPDATE library SET verwacht_device=verwacht_device+1")
    assert run_bridge_job(database, configured, job_id) is True
    library = database.fetch_one("SELECT verwacht_device FROM library")
    assert library is not None and int(library["verwacht_device"]) == source.stat().st_dev


def test_migratie_017_neemt_wacht_op_commit_op_in_unieke_index(tmp_path: Path) -> None:
    from mg_helpers import container_settings

    configured = container_settings(tmp_path)
    for path in (configured.config_root, configured.backup_root):
        path.mkdir(parents=True, exist_ok=True)
    database = Database(configured)
    database.initialize()
    index = database.fetch_one(
        "SELECT sql FROM sqlite_master WHERE name='idx_job_een_actief_per_bestand'"
    )
    assert index is not None and "wacht_op_commit" in str(index["sql"])


def test_canary_rem_geldt_niet_in_containermodus(mg_client: MgClient) -> None:
    """MediaGuard beslist; de standalone canary-rem mag een bridge-job nooit laten wachten."""
    from app.planner import Planner

    client, database, configured = mg_client
    source = copy_into_media(configured, mg_fixture("mg-dual-audio.mkv"), "films/c.mkv")
    job_id = _create(client, source, correlation="mg-canary")
    lane = database.fetch_one("SELECT baan FROM job WHERE id=?", (job_id,))["baan"]
    database.execute("UPDATE functie SET ingeschakeld=1,configuratie=? "
                     "WHERE sleutel='canary_limiet'",
                     ('{"max_bestanden": 0, "max_gb": 0, "max_jobs_per_dag": 0}',))
    database.execute("UPDATE library SET canary_status='actief'")
    database.execute("UPDATE job SET fout='wacht op expliciete vrijgave van canary-batch' "
                     "WHERE id=?", (job_id,))
    planner = Planner(database, configured, storage_validator=lambda: None,
                      machine_guard=lambda _lane: (True, None))
    assert database.fetch_one("SELECT COUNT(*) FROM library WHERE canary_status='actief'")[0] == 0
    assert database.fetch_one("SELECT fout FROM job WHERE id=?", (job_id,))[0] is None
    database.execute("UPDATE library SET canary_status='actief'")  # bv. later door AI-reset
    _claimed, reason = planner.claim_next(lane, "w1")
    assert reason != "canary-limiet bereikt"


def test_wacht_op_commit_lijst_noemt_herkansing(mg_client: MgClient) -> None:
    """Rapport 8, A6: core moet een geslaagde herkansing kunnen koppelen."""
    client, database, configured = mg_client
    source = copy_into_media(configured, mg_fixture("mg-dual-audio.mkv"), "films/r.mkv")
    job_id = _create(client, source, correlation="mg-retry-1")
    database.execute("UPDATE job SET status='wacht_op_commit',pogingen=2,vmaf_score=97.1 "
                     "WHERE id=?", (job_id,))
    data = client.get("/api/mediaguard/v1/jobs-awaiting-commit", headers=HEADERS).json()
    assert job_id in data["job_ids"]
    job = next(j for j in data["jobs"] if j["job_id"] == job_id)
    assert job["correlation_id"] == "mg-retry-1" and job["retried"] is True
    assert job["vmaf"] == 97.1 and job["path"].endswith("films/r.mkv")
