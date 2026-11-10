"""Koppeling MediaGuard-job ↔ engine-job en de 'echte' status per job (rapport 3, R4/R5/R21).

De wachtrij in de UI toont engine-jobs (eigen nummering). MediaGuard weet meer: dat een oude
mislukte job is opgevolgd door een geslaagde herkansing, of dat een geactiveerde versie later
is afgekeurd en teruggedraaid. Dit module levert dat per engine-job, plus een resolver voor
links die nog een MediaGuard-ID bevatten.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from mediaguard.context import AppContext

FAILURE = ("failed", "rejected", "cancelled", "conflict_review")
SUCCESS = ("committed", "handed_off", "awaiting_activation", "awaiting_commit")
ROLLED_BACK_MARK = "origineel teruggezet"


def _engine_id(value: Any) -> int | None:
    try:
        return int(str(value))
    except (TypeError, ValueError):
        return None


def engine_href(ctx: AppContext, core_job_id: Any) -> str:
    """Link naar de wachtrij met het engine-nummer (die de wachtrij kent)."""
    row = ctx.db.fetch_one("SELECT encoder_job_id FROM encoding_jobs WHERE id=?",
                           (_engine_id(core_job_id) or -1,))
    eid = _engine_id(row["encoder_job_id"]) if row else None
    return f"/av1/wachtrij?job={eid}" if eid is not None else "/av1/wachtrij"


def resolve(ctx: AppContext, job_id: int) -> dict[str, Any] | None:
    """Accepteert een engine-ID of een MediaGuard-ID; engine-ID wint bij twijfel."""
    row = ctx.db.fetch_one("SELECT id, encoder_job_id FROM encoding_jobs WHERE "
                           "encoder_job_id=? ORDER BY id DESC LIMIT 1", (str(job_id),))
    if row is not None:
        return {"core_id": int(row["id"]), "engine_id": job_id, "matched": "engine"}
    row = ctx.db.fetch_one("SELECT id, encoder_job_id FROM encoding_jobs WHERE id=?", (job_id,))
    if row is not None and _engine_id(row["encoder_job_id"]) is not None:
        return {"core_id": int(row["id"]), "engine_id": _engine_id(row["encoder_job_id"]),
                "matched": "core"}
    return None


def states(ctx: AppContext, limit: int = 2000) -> list[dict[str, Any]]:
    """Per engine-job: status volgens MediaGuard, opgevolgd door, en of hij meetelt als fout."""
    rows = ctx.db.fetch_dicts(
        "SELECT id, encoder_job_id, source_path, status, error FROM encoding_jobs "
        "WHERE encoder_job_id IS NOT NULL AND encoder_job_id<>'' ORDER BY id DESC LIMIT ?",
        (limit,))
    rows.reverse()
    later_success: dict[str, dict[str, Any]] = {}
    out_by_id: dict[int, dict[str, Any]] = {}
    # van nieuw naar oud: onthoud per pad de nieuwste geslaagde job
    for r in reversed(rows):
        path = str(r["source_path"])
        rolled_back = ROLLED_BACK_MARK in str(r["error"] or "")
        superseded = None
        if r["status"] in FAILURE and not rolled_back and path in later_success \
                and later_success[path]["id"] > r["id"]:
            superseded = later_success[path]
        if r["status"] in SUCCESS:
            later_success.setdefault(path, r)
        if rolled_back:
            status_nl = "Teruggezet (origineel staat terug)"
        elif superseded is not None:
            status_nl = "Opgelost door een latere poging"
        else:
            status_nl = None
        out_by_id[int(r["id"])] = {
            "engine_id": _engine_id(r["encoder_job_id"]), "core_id": int(r["id"]),
            "core_status": r["status"], "status_nl": status_nl,
            "rolled_back": rolled_back,
            "superseded_by": _engine_id(superseded["encoder_job_id"]) if superseded else None,
            "resolved": rolled_back or superseded is not None,
        }
    return [out_by_id[int(r["id"])] for r in rows if int(r["id"]) in out_by_id]


def superseded_core_ids(ctx: AppContext) -> set[int]:
    return {s["core_id"] for s in states(ctx) if s["superseded_by"] is not None}
