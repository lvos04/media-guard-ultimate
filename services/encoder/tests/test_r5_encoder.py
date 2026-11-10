"""Rapport 5: harde CPU-grens, instelbare afstelling, duurschatting, kwaliteitspoort."""

from __future__ import annotations

import json
from dataclasses import replace
from pathlib import Path
from typing import Any

import pytest

from app import afstelling, cpuconfine, duurschatting
from app.config import settings
from app.cpubudget import budget_threads
from app.db import Database
from app.planner import default_machine_guard, pick_candidate
from app.uitvoerder import quality_rejection


@pytest.fixture
def database(tmp_path: Path) -> Database:
    roots = {n: tmp_path / n for n in ("cache", "work", "config", "backups")}
    for path in roots.values():
        path.mkdir()
    configured = replace(settings, pipeline_root=tmp_path, cache_root=roots["cache"],
                         work_root=roots["work"], config_root=roots["config"],
                         backup_root=roots["backups"],
                         database_path=roots["config"] / "app.db")
    db = Database(configured)
    db.initialize()
    afstelling.refresh(db)
    return db


# ── T1: harde CPU-grens ──────────────────────────────────────────────────────


def test_encodeset_is_limiet_maal_kernen_en_laat_kern_0_vrij() -> None:
    cpus = list(range(28))
    chosen = cpuconfine.encode_cpu_set(75, cpus)
    assert len(chosen) == 21 and 0 not in chosen and chosen[-1] == 27
    assert cpuconfine.encode_cpu_set(100, cpus) == list(range(1, 28))  # altijd 1 kern vrij
    assert cpuconfine.encode_cpu_set(1, cpus) == [27]
    assert cpuconfine.cpu_list_text([2, 3, 4, 7, 9, 10]) == "2-4,7,9-10"


def test_threadbudget_binnen_de_encodeset() -> None:
    # 21 kernen in de set, 2 CPU-encodes, 4 GPU-jobs met 1 decodethread elk
    assert budget_threads(100, 2, 21, gpu_workers=4, reserve=1) == 8
    assert budget_threads(75, 2, 28, gpu_workers=0) == 10


def test_prefix_en_uitzetten(monkeypatch: pytest.MonkeyPatch, database: Database) -> None:
    monkeypatch.setattr(cpuconfine, "available_cpus", lambda: list(range(8)))
    monkeypatch.setattr(cpuconfine.shutil, "which", lambda name: "/usr/bin/taskset")
    assert cpuconfine.confine_prefix(50) == ["taskset", "-c", "4-7"]
    afstelling.update(database, {"cpu_harde_grens": False})
    assert cpuconfine.confine_prefix(50) == []
    afstelling.update(database, {"cpu_harde_grens": None})  # terug naar standaard


def test_gpu_baan_niet_geblokkeerd_door_eigen_cpu_encodes(
        monkeypatch: pytest.MonkeyPatch, database: Database) -> None:
    import app.planner as planner

    database.execute("UPDATE instelling SET av1_cpu_procent=75,cpu_max_percent=75 WHERE id=1")
    monkeypatch.setattr(planner.psutil, "cpu_percent", lambda interval=None: 92.0)
    monkeypatch.setattr(planner, "confinement_active", lambda db: True)
    assert default_machine_guard(database, "gpu", container_mode=True) == (True, None)
    monkeypatch.setattr(planner, "confinement_active", lambda db: False)
    allowed, reason = default_machine_guard(database, "gpu", container_mode=True)
    assert not allowed and "CPU-gebruik" in str(reason)


# ── afstelling: alles instelbaar in de app ───────────────────────────────────


def test_afstelling_standaard_bijwerken_en_grenzen(database: Database) -> None:
    view = {i["sleutel"]: i for i in afstelling.view()["items"]}
    assert view["max_4k_gpu"]["waarde"] == 2 and view["vmaf_minimum"]["waarde"] == 93.0
    assert all(i["label_nl"] and i["uitleg_nl"] for i in view.values())
    afstelling.update(database, {"max_4k_gpu": 9, "vastloper_minuten": 30})
    assert afstelling.get("max_4k_gpu") == 4  # begrensd op het maximum
    assert afstelling.get("vastloper_minuten") == 30
    with pytest.raises(ValueError):
        afstelling.update(database, {"bestaat_niet": 1})
    afstelling.update(database, {"max_4k_gpu": None, "vastloper_minuten": None})
    assert afstelling.get("max_4k_gpu") == 2


def test_omgevingsvariabele_blijft_noodoverride(
        monkeypatch: pytest.MonkeyPatch, database: Database) -> None:
    monkeypatch.setenv("MG_MAX_4K_CPU", "1")
    assert afstelling.get("max_4k_cpu") == 1
    item = next(i for i in afstelling.view()["items"] if i["sleutel"] == "max_4k_cpu")
    assert item["vastgezet_door_omgeving"] is True


# ── M9: duurschatting en lange jobs 's nachts ────────────────────────────────


def test_duurschatting_libaom_4k_is_lang() -> None:
    probe = {"duration": 3600, "height": 2160}
    seconds = duurschatting.estimate_seconds("cpu", "libaom-av1", probe)
    assert seconds is not None and seconds > 20 * 3600
    assert duurschatting.estimate_seconds("gpu", "av1_qsv", {"duration": 1440,
                                                               "height": 1080}) == 480
    assert duurschatting.duration_text(int(23.4 * 3600)) == "± 23 u 24 m"


def test_lange_job_wacht_overdag_tenzij_er_niets_anders_is(
        monkeypatch: pytest.MonkeyPatch, database: Database) -> None:
    import app.planner as planner

    monkeypatch.setattr(planner, "in_night_window", lambda now=None: False)
    lang = {"id": 1, "encoder": "libaom-av1", "probe": json.dumps({"duration": 3600,
                                                                    "height": 2160})}
    kort = {"id": 2, "encoder": "libsvtav1", "probe": json.dumps({"duration": 1400,
                                                                   "height": 1080})}
    assert pick_candidate(database, "cpu", [lang, kort])["id"] == 2
    assert pick_candidate(database, "cpu", [lang])["id"] == 1  # anders staat de baan stil
    monkeypatch.setattr(planner, "in_night_window", lambda now=None: True)
    assert pick_candidate(database, "cpu", [lang, kort])["id"] == 1


# ── M4: kwaliteitspoort ──────────────────────────────────────────────────────


def _quality(metric: str, score: float, worst: float) -> dict[str, Any]:
    return {"metric": metric, "score": score, "minimum": worst, "samples": [score]}


def test_kwaliteitspoort_vmaf_en_ssim(database: Database) -> None:
    assert quality_rejection(_quality("vmaf", 95.0, 94.0)) is None
    assert "VMAF" in str(quality_rejection(_quality("vmaf", 91.0, 90.0)))
    # gemiddelde ok, maar één steekproef ver onder de grens
    assert quality_rejection(_quality("vmaf", 94.0, 85.0)) is not None
    assert quality_rejection(_quality("ssim", 0.97, 0.96)) is None
    message = str(quality_rejection(_quality("ssim", 0.90, 0.89)))
    assert "SSIM 0,900" in message
    assert quality_rejection({"metric": None, "score": None}) is None


def test_steekproeven() -> None:
    from app.kwaliteit import sample_points

    assert len(sample_points(3600)) == 3
    assert sample_points(60) == [20.0]
