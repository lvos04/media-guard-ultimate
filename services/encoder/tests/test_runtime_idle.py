"""Stationair verbruik: planner wekbaar i.p.v. 0,5 s-poll, meetpunten middelen, logtail."""

from __future__ import annotations

import asyncio
from datetime import UTC, datetime, timedelta
from pathlib import Path

from test_planner import _planner_case

from app.metingen import record_system_metric, system_metric_history
from app.planner import Planner, wake_planner


async def test_planner_tikt_direct_na_wekker_en_anders_niet(tmp_path: Path) -> None:
    database, configured, _ids = _planner_case(tmp_path)
    planner = Planner(database, configured, storage_validator=lambda: None,
                      machine_guard=lambda _lane: (True, None))
    ticks = 0

    async def counting_tick() -> None:
        nonlocal ticks
        ticks += 1

    planner.tick = counting_tick  # type: ignore[method-assign]
    task = asyncio.create_task(planner.run_forever(interval=30))
    await asyncio.sleep(0.2)
    assert ticks == 1  # geen 0,5 s-poll meer
    wake_planner()
    await asyncio.sleep(0.1)
    assert ticks == 2  # nieuwe job / gewijzigde workers → meteen
    planner.stopping = True  # stoppen wekt ook: geen 30 s wachten
    await asyncio.wait_for(task, timeout=1)


def test_meetpunten_per_minuut_gemiddeld(tmp_path: Path) -> None:
    database, _configured, _ids = _planner_case(tmp_path)
    start = datetime.now(UTC).replace(second=0, microsecond=0) - timedelta(minutes=5)
    for i in range(36):  # 3 minuten, elke 5 s
        record_system_metric(database, {"cpu_percent": float(i), "geheugen": {
            "percent": 50.0, "gebruikt_bytes": 1000}}, start + timedelta(seconds=5 * i))
    full = system_metric_history(database, 1)
    per_minute = system_metric_history(database, 1, stap=60)
    assert len(full) == 36 and len(per_minute) == 3
    assert per_minute[0]["cpu"] == 5.5  # gemiddelde van 0..11
    later = system_metric_history(database, 1, since=full[-2]["tijd"])
    assert [p["tijd"] for p in later] == [full[-1]["tijd"]]
