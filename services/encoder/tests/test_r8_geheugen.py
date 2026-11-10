"""Rapport 8, A5: de ZFS-cache telt als vrij geheugen en de reserve is zichtbaar."""

from __future__ import annotations

import sqlite3
from datetime import UTC, datetime, timedelta
from pathlib import Path
from types import SimpleNamespace

from app import hostlimits, planner

GIB = 1024**3


def _arcstats(path: Path, size: int, c_min: int) -> Path:
    path.write_text(
        "13 1 0x01 123 33456 1 2\n"
        "name                            type data\n"
        f"hits                            4    12345\n"
        f"size                            4    {size}\n"
        f"c_min                           4    {c_min}\n",
        encoding="utf-8",
    )
    return path


def test_zfs_cache_telt_als_vrij(tmp_path: Path, monkeypatch) -> None:  # type: ignore[no-untyped-def]
    arc = _arcstats(tmp_path / "arcstats", 165 * GIB, 6 * GIB)
    assert hostlimits.zfs_reclaimable_bytes(arc) == 159 * GIB
    monkeypatch.setattr(hostlimits.psutil, "virtual_memory",
                        lambda: SimpleNamespace(total=202 * GIB, available=int(11.6 * GIB)))
    memory = hostlimits.effective_memory(tmp_path / "geen-cgroup", arc)
    assert memory.zfs_reclaimable == 159 * GIB
    assert memory.available == int(11.6 * GIB) + 159 * GIB
    assert hostlimits.zfs_reclaimable_bytes(tmp_path / "bestaat-niet") == 0


def test_cgroup_limiet_gaat_voor(tmp_path: Path, monkeypatch) -> None:  # type: ignore[no-untyped-def]
    arc = _arcstats(tmp_path / "arcstats", 165 * GIB, 6 * GIB)
    cg = tmp_path / "cg"
    cg.mkdir()
    (cg / "memory.max").write_text(str(16 * GIB))
    (cg / "memory.current").write_text(str(10 * GIB))
    monkeypatch.setattr(hostlimits.psutil, "virtual_memory",
                        lambda: SimpleNamespace(total=202 * GIB, available=int(11.6 * GIB)))
    memory = hostlimits.effective_memory(cg, arc)
    assert memory.total == 16 * GIB and memory.available == 6 * GIB


def test_melding_toont_echte_vergelijking(tmp_path: Path) -> None:
    connection = sqlite3.connect(":memory:")
    connection.execute("CREATE TABLE bestand (id INTEGER PRIMARY KEY, probe TEXT)")
    connection.execute("CREATE TABLE job (id INTEGER PRIMARY KEY, bestand_id INT, baan TEXT,"
                       " status TEXT, gestart_op TEXT)")
    probe_4k = '{"height": 2160}'
    # Het rapport-8-geval: 11,6 GB 'vrij' volgens Linux + 159 GB ARC → start gewoon.
    roomy = SimpleNamespace(total=202 * GIB, available=int(170.6 * GIB),
                            zfs_reclaimable=159 * GIB)
    assert planner.memory_block_reason(connection, "cpu", probe_4k, memory=roomy) is None
    # Echt krap: de melding noemt vrij, ZFS-cache, nodig en reserve.
    tight = SimpleNamespace(total=32 * GIB, available=8 * GIB, zfs_reclaimable=2 * GIB)
    reason = planner.memory_block_reason(connection, "cpu", probe_4k, memory=tight)
    assert reason is not None
    assert "vrij 8,0 GB" in reason and "ZFS-cache 2,0 GB" in reason
    assert "nodig 6,0 GB" in reason and "reserve 4,0 GB" in reason


def test_baan_stilstand_na_30_minuten() -> None:
    planner.note_lane_block("cpu", None)
    start = datetime.now(UTC) - timedelta(minutes=45)
    planner.note_lane_block("cpu", "wacht op werkgeheugen: vrij 1 GB", now=start)
    # andere getallen, zelfde soort reden: de teller loopt door
    planner.note_lane_block("cpu", "wacht op werkgeheugen: vrij 2 GB")
    rows = {"running": 0, "waiting": 3}

    class FakeDb:
        def fetch_one(self, sql: str, params: tuple[object, ...]) -> tuple[int]:
            return (rows["running"] if "bezig" in sql else rows["waiting"],)

    stalls = planner.lane_stalls(FakeDb())
    assert stalls and stalls[0]["baan"] == "cpu" and stalls[0]["minuten"] >= 44
    rows["running"] = 1
    assert planner.lane_stalls(FakeDb()) == []
    planner.note_lane_block("cpu", None)
    rows["running"] = 0
    assert planner.lane_stalls(FakeDb()) == []


def test_baan_stilstand_niet_bij_bewuste_pauze() -> None:
    """Verbeterlijst 8: kijken, stille uren, een eigen pauze en de noodstop geven geen
    stilstandmelding, hoe lang ze ook duren."""
    class FakeDb:
        def fetch_one(self, sql: str, params: tuple[object, ...]) -> tuple[int]:
            return (0 if "bezig" in sql else 5,)

    start = datetime.now(UTC) - timedelta(hours=3)
    for reason in ("Kijken: iemand kijkt (Wadi)", "stiltevenster actief", "handmatig gepauzeerd",
                   "noodstop actief: geen nieuwe encodes"):
        planner.note_lane_block("gpu", None)
        planner.note_lane_block("gpu", reason, now=start)
        assert planner.lane_stalls(FakeDb()) == [], reason
    planner.note_lane_block("gpu", None)
    planner.note_lane_block("gpu", "wacht op werkgeheugen: vrij 1 GB", now=start)
    assert [s["baan"] for s in planner.lane_stalls(FakeDb())] == ["gpu"]
    planner.note_lane_block("gpu", None)
