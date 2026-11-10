"""Gedeelde opbouw voor de MediaGuard-brugtests (containermodus, handoff)."""

from __future__ import annotations

import shutil
from dataclasses import replace
from pathlib import Path
from typing import Any

from conftest import MG_TOKEN

from app.config import Settings, settings
from app.db import Database
from app.uitvoerder import run_job

HEADERS = {"X-MediaGuard-Token": MG_TOKEN}


def container_settings(tmp_path: Path, **overrides: Any) -> Settings:
    config = tmp_path / "config"
    work = tmp_path / "work"
    media = tmp_path / "media"
    media.mkdir(parents=True, exist_ok=True)
    values: dict[str, Any] = {
        "library_root": media,
        "pipeline_root": work,
        "workspace_root": work,
        "cache_root": work / "cache",
        "work_root": work / "work",
        "config_root": config,
        "backup_root": config / "backups",
        "database_path": config / "app.db",
        "secret_key_file": config / "secrets.key",
        "storage_config_path": config / "opslag.json",
        "storage_mode": "aparte_schijf",
        "container_mode": True,
        "replacement_mode": "handoff",
        "staging_root": work / "staging",
        "media_roots": (media,),
        "render_node": tmp_path / "geen-dri" / "renderD128",
    }
    values.update(overrides)
    return replace(settings, **values)


def copy_into_media(configured: Settings, source: Path, relative: str) -> Path:
    target = configured.media_roots[0] / relative
    target.parent.mkdir(parents=True, exist_ok=True)
    shutil.copy2(source, target)
    return target


def run_bridge_job(database: Database, configured: Settings, job_id: int) -> bool:
    import asyncio

    result = asyncio.run(
        run_job(database, configured, job_id, worker_id="test", storage_validator=lambda: None)
    )
    row = database.fetch_one("SELECT status,fout FROM job WHERE id=?", (job_id,))
    if row is not None and row["status"] != "wacht_op_commit":
        # Zichtbaar in de pytest-uitvoer: waarom faalde de echte encode/verificatie?
        print(f"job {job_id} {row['status']}: {row['fout']}")
    return bool(result)


# Snel (preset 12) maar wél boven de echte kwaliteitspoort (afstelling vmaf_minimum,
# standaard 93). CRF 50 gaf op de testsrc2-fixtures VMAF ≈ 81,7 en werd daarom
# terecht afgekeurd (sinds ddf0ed3); gemeten met SVT-AV1 2.3.0 in het testimage:
# CRF 22 haalt 96,2–96,6 op mg-dual-audio, mg-hdr10, mg-dv84 en mg-hdr10plus.
# De poort zelf blijft dus actief in de brug-, DV-, HDR10+- en executortests.
FAST = {"crf": 22, "preset": 12, "min_saving_percent": 0}
