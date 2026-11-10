"""Wachtende bridge-jobs gaan naar de GPU zodra die werkt, en terug als hij uitvalt."""

from __future__ import annotations

import copy
import json
from typing import Any

from conftest import mg_fixture
from fastapi.testclient import TestClient
from mg_helpers import FAST, HEADERS, copy_into_media

from app.config import Settings
from app.db import Database
from app.mediaguard import rebalance
from app.mediaguard.rebalance import maybe_rebalance, rebalance_waiting_jobs

MgClient = tuple[TestClient, Database, Settings]


def _job(client: TestClient, configured: Settings, name: str) -> int:
    source = copy_into_media(configured, mg_fixture("mg-dual-audio.mkv"), f"films/{name}.mkv")
    response = client.post(
        "/api/mediaguard/v1/jobs",
        json={"source_path": str(source), "correlation_id": f"rb-{name}",
              "policy": {**FAST, "encoder": "auto"}},
        headers=HEADERS,
    )
    assert response.status_code == 201, response.text
    return int(response.json()["job_id"])


def _caps(client: TestClient, gpu: bool) -> dict[str, Any]:
    caps = copy.deepcopy(client.app.state.capabilities.cached)  # type: ignore[attr-defined]
    caps["lanes"]["gpu"]["enabled"] = gpu
    caps["lanes"]["gpu"]["reason"] = None if gpu else "Geen GPU gevonden"
    caps["lanes"]["cpu"]["enabled"] = True
    caps["lanes"]["cpu"]["reason"] = None
    return caps


def _lane(database: Database, job_id: int) -> tuple[str, str | None]:
    row = database.fetch_one(
        "SELECT j.baan,b.encoder FROM job j JOIN besluit b ON b.id=j.besluit_id WHERE j.id=?",
        (job_id,))
    assert row is not None
    return str(row["baan"]), row["encoder"]


def test_gpu_erbij_verplaatst_wachtende_jobs_en_terug(mg_client: MgClient) -> None:
    client, database, configured = mg_client
    job_id = _job(client, configured, "a")
    assert _lane(database, job_id) == ("cpu", "libsvtav1")  # GPU stond uit bij het plannen

    moved = rebalance_waiting_jobs(database, configured, _caps(client, gpu=True))
    assert moved == {"naar_gpu": 1, "naar_cpu": 0}
    assert _lane(database, job_id) == ("gpu", "av1_qsv")
    inst = json.loads(database.fetch_one("SELECT mg_inst FROM job WHERE id=?", (job_id,))[0])
    assert inst.get("gpu_voor_sdr_1080") is not False
    payload = json.loads(database.fetch_one(
        "SELECT b.payload FROM job j JOIN besluit b ON b.id=j.besluit_id WHERE j.id=?",
        (job_id,))[0])
    assert payload["encoder"] == "av1_qsv" and payload["baan"] == "gpu"

    moved = rebalance_waiting_jobs(database, configured, _caps(client, gpu=False))
    assert moved == {"naar_gpu": 0, "naar_cpu": 1}
    assert _lane(database, job_id) == ("cpu", "libsvtav1")


def test_gestarte_en_cpu_geforceerde_jobs_blijven(mg_client: MgClient) -> None:
    client, database, configured = mg_client
    started = _job(client, configured, "b")
    forced = _job(client, configured, "c")
    database.execute("UPDATE job SET gestart_op='2026-11-04T10:00:00+00:00' WHERE id=?",
                     (started,))
    policy = json.loads(database.fetch_one("SELECT mg_policy FROM job WHERE id=?",
                                           (forced,))[0])
    policy["encoder"] = "cpu"
    database.execute("UPDATE job SET mg_policy=? WHERE id=?", (json.dumps(policy), forced))
    moved = rebalance_waiting_jobs(database, configured, _caps(client, gpu=True))
    assert moved["naar_gpu"] == 0
    assert _lane(database, started)[0] == "cpu" and _lane(database, forced)[0] == "cpu"


def test_dolby_vision_blijft_op_de_cpu(mg_client: MgClient) -> None:
    client, database, configured = mg_client
    job_id = _job(client, configured, "d")
    row = database.fetch_one("SELECT bestand_id FROM job WHERE id=?", (job_id,))
    probe = json.loads(database.fetch_one("SELECT probe FROM bestand WHERE id=?",
                                          (int(row["bestand_id"]),))[0])
    probe.update(dv_profile=8, dv_compat=1)
    database.execute("UPDATE bestand SET probe=? WHERE id=?",
                     (json.dumps(probe), int(row["bestand_id"])))
    moved = rebalance_waiting_jobs(database, configured, _caps(client, gpu=True))
    assert moved["naar_gpu"] == 0
    assert _lane(database, job_id)[0] == "cpu"


def test_maybe_rebalance_meteen_bij_wissel_daarna_gedrosseld(mg_client: MgClient) -> None:
    client, database, configured = mg_client
    rebalance._last.update(at=None, gpu_ok=None)
    first = _job(client, configured, "e")
    assert maybe_rebalance(database, configured, _caps(client, gpu=True)) == {
        "naar_gpu": 1, "naar_cpu": 0}
    _job(client, configured, "f")
    assert maybe_rebalance(database, configured, _caps(client, gpu=True)) is None  # binnen 3 min
    assert maybe_rebalance(database, configured, _caps(client, gpu=False)) == {
        "naar_gpu": 0, "naar_cpu": 1}  # wissel: meteen; de nieuwe CPU-job blijft
    assert _lane(database, first)[0] == "cpu"
