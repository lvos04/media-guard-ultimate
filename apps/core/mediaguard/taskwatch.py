"""Achtergrondtaken die nooit stil stoppen (verbeterlijst 9).

Elke achtergrondlus start via :func:`spawn`. Valt een lus toch om door een onverwachte fout
(ook buiten haar eigen try-blok, bijvoorbeeld bij het lezen van een instelling), dan start
de bewaker hem na een korte pauze opnieuw, met een oplopende wachttijd. Per taak houdt
MediaGuard bij wanneer een ronde voor het laatst lukte (:func:`ok`) en wat de laatste fout
was (:func:`fail`). De bewaking (``mediaguard.monitor``) meldt een taak die te lang niets
heeft gedaan, en een Seerr-sync die blijft mislukken.

Opslag: één ``system_state``-rij per taak (``taskwatch:<naam>``), zodat core en de
recovery-worker (eigen proces, zelfde database) elkaar niet overschrijven.
"""

from __future__ import annotations

import asyncio
import logging
import os
import time
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import TYPE_CHECKING, Any

from mediaguard.db import utcnow
from mediaguard.secrets_store import redact

if TYPE_CHECKING:
    from mediaguard.context import AppContext

LOGGER = logging.getLogger(__name__)
PREFIX = "taskwatch:"
# wachttijd vóór een herstart na een onverwachte fout (oplopend; terug naar 30 s na een
# geslaagde ronde)
BACKOFF_SECONDS = (30, 60, 120, 300, 600, 900)
# ok() schrijft hooguit eens per minuut naar de database (de jobrunner draait elke 2 s)
OK_WRITE_SECONDS = 60
# 'doet te lang niets' = geen geslaagde ronde in max(30 min, 3 × interval + 15 min)
MIN_QUIET_SECONDS = 30 * 60
SLACK_SECONDS = 15 * 60

Every = float | Callable[["AppContext"], float] | None


@dataclass
class Spec:
    name: str
    label_nl: str
    every: Every  # verwachte tijd tussen twee rondes (s); None = niet op stilstand bewaken


def _mem(ctx: AppContext) -> dict[str, Any]:
    mem = ctx.services.get("taskwatch")
    if not isinstance(mem, dict):
        mem = {"specs": {}, "written": {}, "backoff": {}, "tasks": {}}
        ctx.services["taskwatch"] = mem
    return mem


def _row(ctx: AppContext, name: str) -> dict[str, Any]:
    row = ctx.state_get(PREFIX + name, {})
    return dict(row) if isinstance(row, dict) else {}


def _parse(value: Any) -> datetime | None:
    if not value:
        return None
    try:
        dt = datetime.fromisoformat(str(value))
    except ValueError:
        return None
    return dt if dt.tzinfo else dt.replace(tzinfo=UTC)


def quiet_seconds(ctx: AppContext, every: Every) -> float | None:
    """Hoe lang een taak zonder geslaagde ronde mag zijn voordat hij als stil telt."""
    if every is None:
        return None
    try:
        seconds = float(every(ctx)) if callable(every) else float(every)
    except Exception:  # noqa: BLE001 - instelling onleesbaar: ruime standaard
        seconds = 3600.0
    return max(MIN_QUIET_SECONDS, 3 * seconds + SLACK_SECONDS)


def register(ctx: AppContext, name: str, label_nl: str, every: Every = None) -> None:
    """Meld een taak aan (zonder hem te starten), bv. de jobrunner."""
    _mem(ctx)["specs"][name] = Spec(name, label_nl, every)
    try:
        row = _row(ctx, name)
        row.update(label_nl=label_nl, started_at=utcnow(), pid=os.getpid(),
                   role=ctx.settings.role, quiet_s=quiet_seconds(ctx, every))
        ctx.state_set(PREFIX + name, row)
    except Exception:  # noqa: BLE001 - bijhouden mag de taak nooit tegenhouden
        LOGGER.debug("taakstatus %s niet vastgelegd", name, exc_info=True)


def ok(ctx: AppContext, name: str) -> None:
    """Een ronde is gelukt (ook 'niets te doen' telt als gelukt). Faalt nooit."""
    try:
        mem = _mem(ctx)
        mem["backoff"][name] = 0
        now = time.monotonic()
        last = mem["written"].get(name)
        if last is not None and now - last[0] < OK_WRITE_SECONDS and not last[1]:
            return
        row = _row(ctx, name)
        row.update(last_ok_at=utcnow(), failing_since=None, fails=0)
        ctx.state_set(PREFIX + name, row)
        mem["written"][name] = (now, False)
    except Exception:  # noqa: BLE001
        LOGGER.debug("taakstatus %s niet vastgelegd", name, exc_info=True)


def fail(ctx: AppContext, name: str, exc: BaseException | str) -> None:
    """Een ronde mislukte; de lus gaat door. Faalt nooit."""
    try:
        text = exc if isinstance(exc, str) else (f"{type(exc).__name__}: {exc}"
                                                  if str(exc) else type(exc).__name__)
        row = _row(ctx, name)
        now = utcnow()
        row.update(last_error=redact(str(text))[:300], last_error_at=now,
                   failing_since=row.get("failing_since") or now,
                   fails=int(row.get("fails") or 0) + 1)
        ctx.state_set(PREFIX + name, row)
        _mem(ctx)["written"][name] = (time.monotonic(), True)
    except Exception:  # noqa: BLE001
        LOGGER.debug("taakstatus %s niet vastgelegd", name, exc_info=True)


def spawn(ctx: AppContext, name: str, loop: Callable[[AppContext], Awaitable[Any]], *,
          label_nl: str, every: Every = None) -> asyncio.Task[None]:
    """Start ``loop(ctx)`` als bewaakte achtergrondtaak.

    Valt de lus om (of keert hij onverwacht terug), dan start hij na 30 s – 15 min opnieuw.
    Alleen annuleren (afsluiten) stopt hem echt."""
    register(ctx, name, label_nl, every)

    async def supervise() -> None:
        mem = _mem(ctx)
        while True:
            try:
                await loop(ctx)
                LOGGER.warning("achtergrondtaak %s stopte onverwacht; start opnieuw", name)
                fail(ctx, name, "taak stopte onverwacht")
            except asyncio.CancelledError:
                raise
            except Exception as exc:  # noqa: BLE001 - de taak mag nooit voorgoed stoppen
                LOGGER.exception("achtergrondtaak %s viel om; start opnieuw", name)
                fail(ctx, name, exc)
            step = int(mem["backoff"].get(name) or 0)
            mem["backoff"][name] = step + 1
            _note_restart(ctx, name)
            await asyncio.sleep(BACKOFF_SECONDS[min(step, len(BACKOFF_SECONDS) - 1)])

    task = asyncio.create_task(supervise(), name=f"mediaguard-{name}")
    _mem(ctx)["tasks"][name] = task
    return task


def _note_restart(ctx: AppContext, name: str) -> None:
    try:
        row = _row(ctx, name)
        row["restarts"] = int(row.get("restarts") or 0) + 1
        row["restarted_at"] = utcnow()
        ctx.state_set(PREFIX + name, row)
    except Exception:  # noqa: BLE001
        LOGGER.debug("herstart %s niet vastgelegd", name, exc_info=True)


async def stop(ctx: AppContext, task: asyncio.Task[Any] | None) -> None:
    """Netjes stoppen bij afsluiten (zelfde gedrag als de oude shutdown-functies)."""
    if task is None:
        return
    task.cancel()
    try:
        await task
    except asyncio.CancelledError:
        pass
    except Exception:  # noqa: BLE001 - afsluiten mag nooit falen
        LOGGER.debug("taak stopte met een fout", exc_info=True)


# -- status ----------------------------------------------------------------------------------
def _expected_rows(ctx: AppContext) -> list[tuple[str, dict[str, Any], float | None]]:
    """(naam, rij, stilte-grens) voor taken die nu horen te draaien.

    Taken van dit proces komen uit het register in het geheugen. Taken van de
    recovery-worker (ander proces) tellen alleen mee als die worker nu leeft met hetzelfde
    proces (heartbeat-pid), zodat een rij van een vorige start nooit vals alarm geeft."""
    out: list[tuple[str, dict[str, Any], float | None]] = []
    seen: set[str] = set()
    for name, spec in _mem(ctx)["specs"].items():
        out.append((name, _row(ctx, name), quiet_seconds(ctx, spec.every)))
        seen.add(name)
    if ctx.settings.role in ("recovery", "all"):
        return out
    try:
        beat = ctx.state_get("recovery_heartbeat") or {}
        pid = beat.get("pid")
        rows = ctx.db.fetch_dicts("SELECT key, value_json FROM system_state WHERE key LIKE ?",
                                  (PREFIX + "%",))
    except Exception:  # noqa: BLE001
        return out
    from mediaguard.db import loads

    for r in rows:
        name = str(r["key"])[len(PREFIX):]
        row = loads(r["value_json"], {}) or {}
        if name in seen or not isinstance(row, dict) or row.get("role") != "recovery":
            continue
        if pid is None or row.get("pid") != pid:
            continue
        quiet = row.get("quiet_s")
        out.append((name, row, float(quiet) if quiet else None))
    return out


def status(ctx: AppContext, now: datetime | None = None) -> list[dict[str, Any]]:
    """Per taak: naam, label, laatste geslaagde ronde, laatste fout, herstarts, stil ja/nee."""
    now = now or datetime.now(UTC)
    out = []
    for name, row, quiet in _expected_rows(ctx):
        last_ok = _parse(row.get("last_ok_at"))
        started = _parse(row.get("started_at"))
        ref = max((d for d in (last_ok, started) if d is not None), default=None)
        idle = (now - ref).total_seconds() if ref else None
        out.append({
            "name": name, "label_nl": row.get("label_nl") or name,
            "last_ok_at": row.get("last_ok_at"), "started_at": row.get("started_at"),
            "last_error": row.get("last_error"), "last_error_at": row.get("last_error_at"),
            "failing_since": row.get("failing_since"), "fails": int(row.get("fails") or 0),
            "restarts": int(row.get("restarts") or 0), "role": row.get("role"),
            "quiet_minutes": round(quiet / 60) if quiet else None,
            "stale": bool(quiet and idle is not None and idle > quiet),
            "idle_minutes": round(idle / 60) if idle is not None else None,
        })
    out.sort(key=lambda r: (not r["stale"], r["label_nl"]))
    return out


def stale(ctx: AppContext, now: datetime | None = None) -> list[dict[str, Any]]:
    return [r for r in status(ctx, now) if r["stale"]]


def failing(ctx: AppContext, name: str) -> dict[str, Any] | None:
    """De rij van een taak waarvan de laatste ronde(s) mislukten, anders None."""
    row = _row(ctx, name)
    return row if row.get("failing_since") else None
