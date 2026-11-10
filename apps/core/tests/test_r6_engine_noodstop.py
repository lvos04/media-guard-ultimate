"""Rapport 6: noodstop naar de AV1-engine (N1) en gemiste commits alsnog melden (N5)."""

from __future__ import annotations

import asyncio
from typing import Any

from mediaguard.encoding import service


class FakeEncoder:
    def __init__(self, waiting: list[int] | None = None) -> None:
        self.calls: list[tuple[str, Any]] = []
        self.waiting = waiting or []

    async def set_emergency_stop(self, active: bool, reason: str = "") -> dict[str, Any]:
        self.calls.append(("noodstop", (active, reason)))
        return {"active": active}

    async def awaiting_commit(self) -> list[int]:
        return list(self.waiting)

    async def committed(self, job_id: Any) -> dict[str, Any]:
        self.calls.append(("committed", str(job_id)))
        return {}

    async def rejected(self, job_id: Any, reason: str = "") -> dict[str, Any]:
        self.calls.append(("rejected", str(job_id)))
        return {}


def test_noodstop_gaat_naar_de_engine(ctx) -> None:  # type: ignore[no-untyped-def]
    fake = FakeEncoder()
    ctx.services["encoder_client"] = fake
    ctx.state_set("emergency_stop", {"active": True, "reason": "test"})
    assert asyncio.run(service.push_emergency_stop(ctx))["pushed"] is True
    assert fake.calls == [("noodstop", (True, "test"))]
    # zelfde stand: geen nieuwe push, tenzij geforceerd (vangnet na engineherstart)
    assert asyncio.run(service.push_emergency_stop(ctx))["pushed"] is False
    assert asyncio.run(service.push_emergency_stop(ctx, force=True))["pushed"] is True
    ctx.state_set("emergency_stop", {"active": False})
    asyncio.run(service.push_emergency_stop(ctx))
    assert fake.calls[-1] == ("noodstop", (False, ""))


def test_noodstop_event_wordt_direct_doorgezet(ctx) -> None:  # type: ignore[no-untyped-def]
    from mediaguard.encoding import api

    fake = FakeEncoder()
    ctx.services["encoder_client"] = fake

    async def run() -> None:
        listener = asyncio.create_task(api._settings_listener(ctx))
        await asyncio.sleep(0)
        ctx.state_set("emergency_stop", {"active": True, "reason": "nu"})
        ctx.events.publish("system.emergency_stop", {"active": True}, persist=False)
        for _ in range(50):
            if fake.calls:
                break
            await asyncio.sleep(0.01)
        listener.cancel()

    asyncio.run(run())
    assert fake.calls == [("noodstop", (True, "nu"))]


def _job(ctx, encoder_id: str, status: str) -> None:  # type: ignore[no-untyped-def]
    now = "2026-11-06T05:00:00+00:00"
    ctx.db.execute(
        "INSERT INTO encoding_jobs(source_path,status,policy_json,encoder_job_id,created_at,"
        "updated_at) VALUES (?,?,?,?,?,?)", (f"/m/{encoder_id}.mkv", status, "{}", encoder_id,
                                             now, now))


def test_gemiste_commit_wordt_alsnog_gemeld(ctx) -> None:  # type: ignore[no-untyped-def]
    _job(ctx, "41", "committed")
    _job(ctx, "42", "rejected")
    _job(ctx, "43", "awaiting_activation")  # nog niet afgehandeld: niets melden
    fake = FakeEncoder(waiting=[41, 42, 43])
    ctx.services["encoder_client"] = fake
    result = asyncio.run(service.reconcile_engine_commits(ctx))
    assert result == {"committed": 1, "rejected": 1, "adopted": 0}
    assert ("committed", "41") in fake.calls and ("rejected", "42") in fake.calls
    assert not any(call[1] == "43" for call in fake.calls)
