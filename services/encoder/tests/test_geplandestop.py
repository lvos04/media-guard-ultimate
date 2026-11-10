"""Verbeterpunt 30: een geplande stop of update telt niet als herstartlus.

De AIO-supervisor schrijft vóór SIGTERM ``<CONFIG_ROOT>/geplande-stop``. Een encode die
daardoor wordt afgebroken, gaat terug in de wachtrij zonder 'ServiceStop'; de grens van drie
onderbrekingen ('HerstartLus') blijft alleen gelden voor crashes en hang-herstarts."""

from __future__ import annotations

from typing import Any

import pytest
from test_planner import _planner_case

from app import geplandestop, noodstop, uitvoerder
from app.planner import recover_orphaned_jobs


@pytest.fixture(autouse=True)
def _reset() -> Any:
    noodstop._reset_for_tests()
    uitvoerder.reset_shutdown()
    yield
    noodstop._reset_for_tests()
    uitvoerder.reset_shutdown()


async def _interrupted_run(database: Any, configured: Any, job_id: int,
                           monkeypatch: pytest.MonkeyPatch) -> None:
    database.execute("UPDATE job SET status='bezig',pogingen=pogingen+1 WHERE id=?", (job_id,))
    database.execute(
        "INSERT INTO job_poging(job_id,poging,status,gestart_op) SELECT id,pogingen,'bezig',"
        "'2026-11-08T10:00:00+00:00' FROM job WHERE id=?", (job_id,))

    async def stopped(*_a: Any, **_k: Any) -> Any:
        raise uitvoerder.ProcessInterrupted("mediaproces onderbroken door service- of "
                                            "systeemstop")

    monkeypatch.setattr(uitvoerder, "capture_comparison_frames", stopped)
    await uitvoerder.run_job(database, configured, job_id, storage_validator=lambda: None,
                             already_claimed=True)


@pytest.mark.asyncio
async def test_geplande_stops_tellen_niet_als_herstartlus(
        tmp_path: Any, monkeypatch: pytest.MonkeyPatch) -> None:
    database, configured, ids = _planner_case(tmp_path)
    job_id = ids[0]
    geplandestop.announce(configured)
    for _ in range(uitvoerder.MAX_RESTART_INTERRUPTIONS + 2):
        await _interrupted_run(database, configured, job_id, monkeypatch)
    row = database.fetch_one("SELECT status,fout,foutsoort FROM job WHERE id=?", (job_id,))
    assert row["status"] == "wacht" and row["foutsoort"] is None
    assert "geplande stop" in row["fout"]
    kinds = {r[0] for r in database.fetch_all(
        "SELECT foutsoort FROM job_poging WHERE job_id=?", (job_id,))}
    assert kinds == {geplandestop.PLANNED_STOP_TYPE}
    assert not (configured.cache_root / str(job_id)).exists()


@pytest.mark.asyncio
async def test_zonder_aankondiging_blijft_de_herstartlus_grens(
        tmp_path: Any, monkeypatch: pytest.MonkeyPatch) -> None:
    database, configured, ids = _planner_case(tmp_path)
    job_id = ids[0]
    for _ in range(uitvoerder.MAX_RESTART_INTERRUPTIONS):
        await _interrupted_run(database, configured, job_id, monkeypatch)
    row = database.fetch_one("SELECT status,foutsoort FROM job WHERE id=?", (job_id,))
    assert row["status"] == "mislukt" and row["foutsoort"] == "HerstartLus"


def test_harde_stop_na_aankondiging_is_geen_crash(tmp_path: Any) -> None:
    """De stop duurde te lang en Docker stopte hard: de volgende start ziet het bestand."""
    database, configured, ids = _planner_case(tmp_path)
    job_id = ids[0]
    database.execute("UPDATE job SET status='bezig',pogingen=3,worker_id='cpu-1' WHERE id=?",
                     (job_id,))
    for poging in (1, 2):  # twee eerdere echte onderbrekingen
        database.execute(
            "INSERT INTO job_poging(job_id,poging,status,fout,foutsoort,gestart_op) VALUES "
            "(?,?,'afgebroken','x','ServiceStop','2026-11-08T09:00:00+00:00')",
            (job_id, poging))
    database.execute("INSERT INTO job_poging(job_id,poging,status,gestart_op) VALUES "
                     "(?,3,'bezig','2026-11-08T10:00:00+00:00')", (job_id,))
    geplandestop.announce(configured)
    assert recover_orphaned_jobs(database, configured) == 1
    row = database.fetch_one("SELECT status,fout FROM job WHERE id=?", (job_id,))
    assert row["status"] == "wacht" and "geplande stop" in row["fout"]
    last = database.fetch_one("SELECT status,foutsoort FROM job_poging WHERE job_id=? AND "
                              "poging=3", (job_id,))
    assert last["status"] == "afgebroken" and last["foutsoort"] == "GeplandeStop"
    assert not geplandestop.planned_stop(configured)  # alleen voor deze ene herstart


def test_crash_zonder_aankondiging_telt_wel(tmp_path: Any) -> None:
    database, configured, ids = _planner_case(tmp_path)
    job_id = ids[0]
    database.execute("UPDATE job SET status='bezig',pogingen=3,worker_id='cpu-1' WHERE id=?",
                     (job_id,))
    for poging in (1, 2):
        database.execute(
            "INSERT INTO job_poging(job_id,poging,status,fout,foutsoort,gestart_op) VALUES "
            "(?,?,'afgebroken','x','ServiceStop','2026-11-08T09:00:00+00:00')",
            (job_id, poging))
    recover_orphaned_jobs(database, configured)
    row = database.fetch_one("SELECT status,foutsoort FROM job WHERE id=?", (job_id,))
    assert row["status"] == "mislukt" and row["foutsoort"] == "HerstartLus"


@pytest.mark.asyncio
async def test_oud_stopbestand_dekt_geen_herstartlus(
        tmp_path: Any, monkeypatch: pytest.MonkeyPatch) -> None:
    """Een achtergebleven stopbestand van uren geleden mag een echte herstartlus (hang-
    herstart door de levenscontrole) niet verbergen."""
    database, configured, ids = _planner_case(tmp_path)
    job_id = ids[0]
    geplandestop.marker_path(configured).write_text("2026-01-01T00:00:00+00:00")
    assert geplandestop.planned_stop_at(configured) is not None
    assert not geplandestop.planned_stop(configured)
    for _ in range(uitvoerder.MAX_RESTART_INTERRUPTIONS):
        await _interrupted_run(database, configured, job_id, monkeypatch)
    row = database.fetch_one("SELECT status,foutsoort FROM job WHERE id=?", (job_id,))
    assert row["status"] == "mislukt" and row["foutsoort"] == "HerstartLus"


def test_stopbestand_weg_ook_als_herstel_faalt(
        tmp_path: Any, monkeypatch: pytest.MonkeyPatch) -> None:
    from app import planner

    database, configured, _ids = _planner_case(tmp_path)
    geplandestop.announce(configured)

    def kapot(*_a: Any, **_k: Any) -> int:
        raise RuntimeError("database weg")

    monkeypatch.setattr(planner, "_recover_orphaned_jobs", kapot)
    with pytest.raises(RuntimeError):
        recover_orphaned_jobs(database, configured)
    assert geplandestop.planned_stop_at(configured) is None
