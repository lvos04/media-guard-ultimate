from __future__ import annotations

import os
import subprocess
import time
from collections.abc import Iterator
from pathlib import Path
from typing import Any

import pytest

# MediaGuard-import: de backend staat in services/encoder; het fixturescript is
# ongewijzigd meegekopieerd naar tests/fixtures/. TESTDATA blijft overschrijfbaar.
ENCODER_ROOT = Path(__file__).resolve().parents[1]
TESTDATA = Path(os.environ.get("TESTDATA", str(ENCODER_ROOT / ".testdata")))
FIXTURE_SCRIPT = ENCODER_ROOT / "tests" / "fixtures" / "maak-testbestanden.sh"

# De scanner geeft pas een besluit voor bestanden die minstens zestig minuten
# stabiel zijn (scanner.FILE_STABILITY_SECONDS). De originele suite ging er
# stilzwijgend van uit dat testdata ouder dan een uur was; een vers gegenereerde
# set liet daardoor ~40 scanner-/planner-/uitvoerdertests falen. We zetten de
# mtime van de fixtures daarom expliciet twee uur terug. De productiecode en
# het stabiliteitsvenster blijven ongewijzigd.
_FIXTURE_AGE_SECONDS = 2 * 3600


def _backdate(root: Path) -> None:
    moment = time.time() - _FIXTURE_AGE_SECONDS
    for path in root.rglob("*"):
        if path.is_file() and path.stat().st_mtime > moment:
            os.utime(path, (moment, moment))


@pytest.fixture(scope="session", autouse=True)
def testbestanden() -> str:
    """Genereer uitsluitend de korte representatieve testbestanden."""

    if not TESTDATA.is_dir() or not any(TESTDATA.iterdir()):
        subprocess.run(["bash", str(FIXTURE_SCRIPT), str(TESTDATA)], check=True)
    _backdate(TESTDATA)
    return str(TESTDATA)


def pad(naam: str) -> str:
    return str(TESTDATA / naam)


# ── MediaGuard-brug ─────────────────────────────────────────────────────────
MG_TESTDATA = TESTDATA / "mediaguard"
MG_FIXTURE_SCRIPT = ENCODER_ROOT / "tests" / "fixtures" / "maak-mediaguard-fixtures.sh"
MG_TOKEN = "test-token-met-voldoende-lengte-0123456789"


@pytest.fixture(scope="session")
def mg_testdata(testbestanden: str) -> Path:
    """Genereer de MediaGuard-fixtures eenmalig (DV/HDR10+/dual audio)."""

    if not (MG_TESTDATA / "mg-dual-audio.mkv").is_file():
        subprocess.run(["bash", str(MG_FIXTURE_SCRIPT), str(MG_TESTDATA)], check=True)
    _backdate(MG_TESTDATA)
    return MG_TESTDATA


def mg_fixture(naam: str) -> Path:
    path = MG_TESTDATA / naam
    if not path.is_file():
        pytest.skip(f"{naam} ontbreekt (toolchain zonder dovi_tool/mkvmerge/hdr10plus_tool)")
    return path


@pytest.fixture
def mg_client(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Iterator[Any]:
    """TestClient op een app in containermodus (handoff, token verplicht)."""

    from fastapi.testclient import TestClient
    from mg_helpers import container_settings

    from app.db import Database
    from app.main import create_app

    monkeypatch.setenv("MG_INTERNAL_TOKEN", MG_TOKEN)
    configured = container_settings(tmp_path)
    database = Database(configured)
    application = create_app(configured, database, start_planner=False)
    with TestClient(application) as client:
        database.execute("UPDATE instelling SET cache_pauze_gb=0 WHERE id=1")
        yield client, database, configured


# ── Werkgeheugen van de testmachine ────────────────────────────────────────
@pytest.fixture
def ruim_werkgeheugen(monkeypatch: pytest.MonkeyPatch) -> None:
    """Planner-claimtests toetsen wachtrij-, baan- en encodegrenzen, niet het RAM van
    de testmachine. Zonder deze vaste meting weigert memory_block_reason op een kleine
    CI-runner (privé-repo: ±7 GB) of onder een cgroup-limiet terecht ('wacht op
    werkgeheugen'), en faalden die tests dus alleen daar. De geheugenpoort zelf heeft
    eigen tests met een expliciete ``memory=`` (test_planner, test_r8_geheugen)."""

    from app import planner
    from app.hostlimits import Memory

    gib = 1024**3
    monkeypatch.setattr(planner, "effective_memory",
                        lambda *_args, **_kwargs: Memory(total=64 * gib, available=48 * gib))
