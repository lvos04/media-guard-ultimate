"""Vrije-ruimtebodem: zichtbaar in /health en /capabilities, instelbaar via de brug,
herbeoordeeld bij een wijziging en vóór een claim; cache → staging met EXDEV-terugval."""

from __future__ import annotations

import errno
import hashlib
import os
from pathlib import Path
from typing import Any

import pytest
from fastapi.testclient import TestClient
from mg_helpers import HEADERS
from test_onderhoud import _case

from app.config import Settings
from app.db import Database
from app.mediaguard import handoff
from app.onderhoud import pause_overview, storage_guard
from app.planner import Planner

MgClient = tuple[TestClient, Database, Settings]
HUGE_GB = 999_999
# Vaste, ruime meting van de vrije ruimte (zie conftest.ruime_cache): de drempels worden getest,
# niet de schijf van de testmachine.
pytestmark = pytest.mark.usefixtures("ruime_cache")


def _lane(database: Database, lane: str) -> Any:
    return database.fetch_one(
        "SELECT gepauzeerd,pauze_reden FROM worker_config WHERE baan=?", (lane,)
    )


def test_bodem_via_brug_pauzeert_en_is_zichtbaar_in_health_en_capabilities(
    mg_client: MgClient,
) -> None:
    client, database, _settings = mg_client
    before = client.get("/api/mediaguard/v1/settings/storage", headers=HEADERS)
    assert before.status_code == 200
    assert before.json()["cache_min_free_gb"] == 0  # fixture zet de bodem op 0

    response = client.put(
        "/api/mediaguard/v1/settings/storage",
        headers=HEADERS,
        json={"cache_min_free_gb": HUGE_GB},
    )
    assert response.status_code == 200, response.text
    body = response.json()
    assert body["cache_min_free_gb"] == HUGE_GB
    assert body["configured_warning_gb"] > HUGE_GB  # automatisch boven de bodem
    assert body["below_floor"] is True
    # Direct herbeoordeeld (niet pas bij het uuronderhoud): CPU-baan staat stil.
    cpu = _lane(database, "cpu")
    assert int(cpu["gepauzeerd"]) == 1
    assert str(cpu["pauze_reden"]).startswith("automatisch: cache heeft nog")
    assert f"pauzebodem van {HUGE_GB} GB" in str(cpu["pauze_reden"])

    health = client.get("/api/mediaguard/v1/health", headers=HEADERS).json()
    paused = {item["lane"]: item for item in health["lanes_paused"]}
    assert paused["cpu"]["storage_pause"] is True
    assert "pauzebodem" in paused["cpu"]["reason"]
    assert health["storage_guard"]["cache_min_free_gb"] == HUGE_GB
    assert health["status"] != "ok"
    assert any("cpu-baan gepauzeerd: automatisch: cache" in r for r in health["reasons"])

    caps = client.get("/api/mediaguard/v1/capabilities", headers=HEADERS).json()
    assert caps["lane_pause"]["cpu"]["paused"] is True
    assert caps["storage_guard"]["below_floor"] is True
    assert any(item["lane"] == "cpu" for item in caps["lanes_paused"])

    # Bodem omlaag → eigen automatische pauze direct opgeheven.
    lowered = client.put(
        "/api/mediaguard/v1/settings/storage", headers=HEADERS, json={"cache_min_free_gb": 1}
    )
    assert lowered.status_code == 200
    assert int(_lane(database, "cpu")["gepauzeerd"]) == 0
    caps = client.get("/api/mediaguard/v1/capabilities", headers=HEADERS).json()
    assert caps["lane_pause"]["cpu"]["paused"] is False
    assert caps["storage_guard"]["cache_min_free_gb"] == 1


def test_brug_weigert_bodem_boven_waarschuwing_en_vereist_token(mg_client: MgClient) -> None:
    client, _database, _settings = mg_client
    bad = client.put(
        "/api/mediaguard/v1/settings/storage",
        headers=HEADERS,
        json={"cache_min_free_gb": 300, "cache_warning_gb": 200},
    )
    assert bad.status_code == 422
    assert (
        client.put("/api/mediaguard/v1/settings/storage", json={"cache_min_free_gb": 5})
    ).status_code == 401


def test_legacy_instellingen_herbeoordelen_direct(mg_client: MgClient) -> None:
    client, database, _settings = mg_client
    response = client.patch(
        "/api/instellingen",
        headers=HEADERS,
        json={"cache_pauze_gb": HUGE_GB, "cache_waarschuwing_gb": HUGE_GB + 1},
    )
    assert response.status_code == 200, response.text
    assert int(_lane(database, "cpu")["gepauzeerd"]) == 1
    client.patch(
        "/api/instellingen",
        headers=HEADERS,
        json={"cache_pauze_gb": 1, "cache_waarschuwing_gb": 2},
    )
    assert int(_lane(database, "cpu")["gepauzeerd"]) == 0


def test_handmatige_pauze_blijft_staan_en_heeft_reden(tmp_path: Path) -> None:
    database, configured, _job = _case(tmp_path)
    database.execute(
        "UPDATE worker_config SET gepauzeerd=1,pauze_reden='handmatig gepauzeerd' "
        "WHERE baan='gpu'"
    )
    database.execute("UPDATE instelling SET cache_waarschuwing_gb=2,cache_pauze_gb=1")
    storage_guard(database, configured)
    overview = pause_overview(database, configured)
    assert overview["lanes"]["gpu"] == {
        "paused": True,
        "reason": "handmatig gepauzeerd",
        "storage_pause": False,
    }
    assert overview["storage_guard"]["below_floor"] is False


def _waiting_job(database: Database) -> int:
    row = database.fetch_one("SELECT besluit_id,bestand_id FROM job LIMIT 1")
    assert row is not None
    return database.execute_insert(
        "INSERT INTO job(besluit_id,bestand_id,baan,status) VALUES (?,?,'cpu','wacht')",
        (int(row["besluit_id"]), int(row["bestand_id"])),
    )


def test_claim_herbeoordeelt_vrije_ruimte_voor_het_uuronderhoud(tmp_path: Path) -> None:
    database, configured, _job = _case(tmp_path)
    _waiting_job(database)
    planner = Planner(database, configured, storage_validator=lambda: None,
                      machine_guard=lambda _lane: (True, None))
    # Bodem onderschreden zonder dat het uuronderhoud liep.
    database.execute(
        "UPDATE instelling SET cache_waarschuwing_gb=?,cache_pauze_gb=?",
        (HUGE_GB + 1, HUGE_GB),
    )
    job_id, reason = planner.claim_next("cpu", "cpu-1")
    assert job_id is None
    assert reason is not None and reason.startswith("automatisch: cache")
    assert int(_lane(database, "cpu")["gepauzeerd"]) == 1

    # Ruimte terug: de volgende claim hervat zelf (geen uur wachten).
    database.execute("UPDATE instelling SET cache_waarschuwing_gb=2,cache_pauze_gb=1")
    planner.last_storage_check = None
    planner.recheck_storage("cpu")
    assert int(_lane(database, "cpu")["gepauzeerd"]) == 0


def test_herbeoordeling_alleen_met_wachtend_werk_en_begrensd(tmp_path: Path) -> None:
    database, configured, _job = _case(tmp_path)
    planner = Planner(database, configured, storage_validator=lambda: None)
    database.execute(
        "UPDATE instelling SET cache_waarschuwing_gb=?,cache_pauze_gb=?",
        (HUGE_GB + 1, HUGE_GB),
    )
    planner.recheck_storage("cpu")  # geen wachtende job → niets
    assert int(_lane(database, "cpu")["gepauzeerd"]) == 0
    _waiting_job(database)
    planner.recheck_storage("cpu")
    assert int(_lane(database, "cpu")["gepauzeerd"]) == 1
    database.execute("UPDATE instelling SET cache_waarschuwing_gb=2,cache_pauze_gb=1")
    planner.recheck_storage("cpu")  # binnen 10 s: begrensd, nog geen hervatting
    assert int(_lane(database, "cpu")["gepauzeerd"]) == 1
    planner.recheck_storage("cpu", force=True)
    assert int(_lane(database, "cpu")["gepauzeerd"]) == 0


# ── cache → staging ──────────────────────────────────────────────────────────


def test_move_to_staging_hernoemt_op_hetzelfde_filesystem(tmp_path: Path) -> None:
    source = tmp_path / "cache" / "out.mkv"
    source.parent.mkdir()
    source.write_bytes(b"AV1" * 1000)
    target = tmp_path / "staging" / "out.mkv.deel"
    target.parent.mkdir()
    assert handoff.move_to_staging(source, target) == "rename"
    assert not source.exists() and target.read_bytes() == b"AV1" * 1000


def _exdev_replace(original: Any) -> Any:
    def replace(src: Any, dst: Any) -> None:
        if str(src).endswith("out.mkv") and "cache" in str(src):
            raise OSError(errno.EXDEV, "Invalid cross-device link")
        original(src, dst)

    return replace


def test_move_to_staging_kopieert_geverifieerd_bij_exdev(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    payload = os.urandom(3 * 1024 * 1024 + 17)
    source = tmp_path / "cache" / "out.mkv"
    source.parent.mkdir()
    source.write_bytes(payload)
    target = tmp_path / "staging" / "out.mkv.deel"
    target.parent.mkdir()
    synced: list[int] = []
    real_fsync = os.fsync
    monkeypatch.setattr(handoff.os, "replace", _exdev_replace(os.replace))
    monkeypatch.setattr(handoff.os, "fsync", lambda fd: (synced.append(fd), real_fsync(fd)))

    assert handoff.move_to_staging(source, target) == "copy"
    assert not source.exists()  # pas na geverifieerde kopie verwijderd
    assert hashlib.sha256(target.read_bytes()).digest() == hashlib.sha256(payload).digest()
    assert synced  # bestand en mappen gefsynct


def test_move_to_staging_houdt_bron_bij_afwijkende_kopie(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    source = tmp_path / "cache" / "out.mkv"
    source.parent.mkdir()
    source.write_bytes(b"ORIGINEEL" * 1000)
    target = tmp_path / "staging" / "out.mkv.deel"
    target.parent.mkdir()
    monkeypatch.setattr(handoff.os, "replace", _exdev_replace(os.replace))
    real_open = Path.open

    def corrupting_open(self: Path, mode: str = "r", *args: Any, **kwargs: Any) -> Any:
        handle = real_open(self, mode, *args, **kwargs)
        if self == target and mode == "rb":
            data = handle.read()
            handle.close()
            self.write_bytes(b"X" + data[1:])
            return real_open(self, mode, *args, **kwargs)
        return handle

    monkeypatch.setattr(Path, "open", corrupting_open)
    with pytest.raises(handoff.StagingMoveError, match="sha256"):
        handoff.move_to_staging(source, target)
    assert source.exists() and not target.exists()


def test_move_to_staging_andere_fouten_niet_maskeren(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    source = tmp_path / "cache" / "out.mkv"
    source.parent.mkdir()
    source.write_bytes(b"x")

    def denied(_src: Any, _dst: Any) -> None:
        raise PermissionError(errno.EACCES, "denied")

    monkeypatch.setattr(handoff.os, "replace", denied)
    with pytest.raises(PermissionError):
        handoff.move_to_staging(source, tmp_path / "staging.deel")
    assert source.exists()
