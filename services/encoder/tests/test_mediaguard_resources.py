"""Systeembelasting vanuit MediaGuard: CPU-budget, encodes tegelijk, stille uren, temperatuur."""

from __future__ import annotations

from fastapi.testclient import TestClient
from mg_helpers import HEADERS

from app.config import Settings
from app.cpubudget import budget_threads
from app.db import Database
from app.media.commandos import _svt_paralleliteit

MgClient = tuple[TestClient, Database, Settings]
URL = "/api/mediaguard/v1/settings/resources"


def test_budget_threads_verdeelt_cores_over_encodes() -> None:
    assert budget_threads(75, 1, cores=16) == 12
    assert budget_threads(75, 2, cores=16) == 6
    assert budget_threads(50, 3, cores=4) == 1  # nooit 0
    assert budget_threads(100, 0, cores=8) == 8


def test_svt_paralleliteit_respecteert_budget() -> None:
    assert _svt_paralleliteit(1080, {"cpu_budget_threads": 2}) <= 2
    assert _svt_paralleliteit(1080, {"cpu_budget_threads": 2, "svt_threads_1080": 8}) <= 2


def test_brug_zet_budget_workers_stilte_en_temperatuur(mg_client: MgClient) -> None:
    client, database, _settings = mg_client
    r = client.put(URL, headers=HEADERS, json={
        "cpu_percent": 50, "cpu_workers": 2, "gpu_workers": 1, "quiet_start": "07:00",
        "quiet_end": "23:00", "max_cpu_temp": 80, "nice": 12})
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["percent"] == 50 and body["cpu_workers"] == 2
    assert body["threads_per_encode"] == max(1, int(body["cores"] * 0.5) // 2)
    assert body["lanes"]["cpu"]["aantal"] == 2 and body["lanes"]["gpu"]["aantal"] == 1
    assert body["lanes"]["cpu"]["stilte_start"] == "07:00" and body["max_cpu_temp"] == 80
    row = database.fetch_one("SELECT nice FROM worker_config WHERE baan='cpu'")
    assert int(row["nice"]) == 12
    total = database.fetch_one("SELECT max_gelijktijdige_encodes FROM instelling WHERE id=1")
    assert int(total["max_gelijktijdige_encodes"]) == 3
    # stille uren uit
    r = client.put(URL, headers=HEADERS, json={"cpu_percent": 75, "cpu_workers": 1,
                                                "gpu_workers": 1})
    assert r.json()["lanes"]["gpu"]["stilte_start"] is None
    assert client.get(URL, headers=HEADERS).json()["percent"] == 75
    # half venster of ongeldige waarde → 422
    assert client.put(URL, headers=HEADERS, json={"cpu_percent": 75, "cpu_workers": 1,
                                                   "gpu_workers": 1,
                                                   "quiet_start": "07:00"}).status_code == 422
    assert client.put(URL, headers=HEADERS, json={"cpu_percent": 5, "cpu_workers": 1,
                                                   "gpu_workers": 1}).status_code == 422
    assert client.put(URL, json={"cpu_percent": 75, "cpu_workers": 1,
                                 "gpu_workers": 1}).status_code == 401


def test_een_cpu_waarde_in_containermodus(mg_client: MgClient) -> None:
    """H11: 75 % doorgezet door MediaGuard wint van de oude eigen 90 %."""

    from app.cpubudget import effective_cpu_percent, unify_cpu_percent
    from app.planner import default_machine_guard

    client, database, _settings = mg_client
    database.execute("UPDATE instelling SET cpu_max_percent=90,av1_cpu_procent=75 WHERE id=1")
    assert effective_cpu_percent(database, container_mode=True) == 75
    assert effective_cpu_percent(database, container_mode=False) == 90
    body = client.get("/api/instellingen", headers=HEADERS).json()
    assert body["cpu_max_percent"] == 75 and body["cpu_max_percent_bron"] == "mediaguard"
    unify_cpu_percent(database, container_mode=True)
    row = database.fetch_one("SELECT cpu_max_percent FROM instelling WHERE id=1")
    assert int(row["cpu_max_percent"]) == 75
    r = client.put(URL, headers=HEADERS, json={"cpu_percent": 60, "cpu_workers": 1,
                                                "gpu_workers": 1})
    assert r.status_code == 200
    assert client.get("/api/instellingen", headers=HEADERS).json()["cpu_max_percent"] == 60
    row = database.fetch_one("SELECT cpu_max_percent FROM instelling WHERE id=1")
    assert int(row["cpu_max_percent"]) == 60
    allowed, _reason = default_machine_guard(database, "gpu", container_mode=True)
    assert allowed


def test_gpu_decode_telt_mee_in_cpu_budget() -> None:
    """Rapport 2: 4 GPU-jobs decoderen op de CPU; dat moet binnen de CPU-limiet passen."""
    from app.cpubudget import GPU_DECODE_RESERVE

    assert budget_threads(75, 2, cores=28, gpu_workers=4) == (21 - 4 * GPU_DECODE_RESERVE) // 2
    assert budget_threads(75, 2, cores=28, gpu_workers=0) == 10
    assert budget_threads(25, 1, cores=8, gpu_workers=4) == 1  # nooit 0
