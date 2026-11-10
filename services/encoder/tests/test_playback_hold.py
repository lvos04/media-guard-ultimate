"""Pauze tijdens kijken (rapport 5, M3c): alleen eigen pauzes zetten en opheffen."""

from __future__ import annotations

import sqlite3
from typing import Any

from app.mediaguard.api import PLAYBACK_HOLD_PREFIX, apply_playback_hold


class Db:
    def __init__(self) -> None:
        self.conn = sqlite3.connect(":memory:")
        self.conn.row_factory = sqlite3.Row
        self.conn.execute("CREATE TABLE worker_config(baan TEXT, gepauzeerd INT, pauze_reden TEXT)")
        self.conn.executemany("INSERT INTO worker_config VALUES (?,?,?)",
                              [("cpu", 0, None), ("gpu", 1, "handmatig gepauzeerd")])

    def fetch_all(self, sql: str, params: tuple[Any, ...] = ()) -> list[sqlite3.Row]:
        return list(self.conn.execute(sql, params))

    def execute(self, sql: str, params: tuple[Any, ...] = ()) -> int:
        return self.conn.execute(sql, params).rowcount

    def lane(self, name: str) -> tuple[int, str | None]:
        row = self.conn.execute("SELECT gepauzeerd,pauze_reden FROM worker_config WHERE baan=?",
                                (name,)).fetchone()
        return int(row[0]), row[1]


def test_hold_only_touches_free_or_own_lanes() -> None:
    db = Db()
    res = apply_playback_hold(db, True, ["cpu", "gpu"], "iemand kijkt (Stormvogel)")
    assert res["lanes"] == {"cpu": "wacht", "gpu": "al gepauzeerd"}
    paused, reason = db.lane("cpu")
    assert paused == 1 and reason and reason.startswith(PLAYBACK_HOLD_PREFIX)
    assert db.lane("gpu") == (1, "handmatig gepauzeerd")  # handmatige pauze blijft

    res = apply_playback_hold(db, False, [], "")
    assert res["lanes"]["cpu"] == "hervat" and db.lane("cpu") == (0, None)
    assert db.lane("gpu") == (1, "handmatig gepauzeerd")


def test_gpu_only_mode_leaves_cpu_running() -> None:
    db = Db()
    db.execute("UPDATE worker_config SET gepauzeerd=0, pauze_reden=NULL")
    apply_playback_hold(db, True, ["gpu"], "iemand kijkt")
    assert db.lane("cpu") == (0, None) and db.lane("gpu")[0] == 1


def test_deliberate_block_classification() -> None:
    """Verbeterlijst 8: kijken, eigen pauze, stille uren en de noodstop zijn geen storing."""
    from app.planner import deliberate_block

    for reason in (f"{PLAYBACK_HOLD_PREFIX} iemand kijkt (Wadi)",
                   "Jellyfin: er wordt momenteel gekeken", "handmatig gepauzeerd",
                   "stiltevenster actief", "noodstop actief: geen nieuwe encodes"):
        assert deliberate_block(reason), reason
    for reason in ("automatisch: cache heeft nog 3 GB vrij", "MediaGuard: GPU-baan uit",
                   "wacht op werkgeheugen: vrij 1 GB", "CPU-gebruik 99% boven limiet 80%",
                   "ingestelde totale encodegrens van 2 actieve job(s) bereikt"):
        assert not deliberate_block(reason), reason


def test_playback_hold_is_not_degraded_in_health(mg_client: Any) -> None:
    """Iemand kijkt in Emby: de engine meldt een notitie, geen reden en geen 'degraded'."""
    from mg_helpers import HEADERS

    client, database, _settings = mg_client
    apply_playback_hold(database, True, ["cpu"], "iemand kijkt (Wadi)")
    health = client.get("/api/mediaguard/v1/health", headers=HEADERS).json()
    assert any(p["lane"] == "cpu" for p in health["lanes_paused"])  # pauze blijft zichtbaar
    assert not any("Kijken:" in r for r in health["reasons"])
    assert any("Kijken:" in n for n in health["notes"])
    database.execute("UPDATE worker_config SET gepauzeerd=1,pauze_reden='automatisch: opslag "
                     "niet bereikbaar: test' WHERE baan='cpu'")
    health = client.get("/api/mediaguard/v1/health", headers=HEADERS).json()
    assert any("automatisch: opslag" in r for r in health["reasons"])
