from __future__ import annotations

import os
import shutil
import sqlite3
import time
from concurrent.futures import ThreadPoolExecutor
from contextlib import closing
from dataclasses import replace
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest

from app.config import Settings, settings
from app.db import Database
from app.onderhoud import (
    record_failure,
    rotate_logs,
    storage_guard,
    weekly_database_maintenance,
)


def _case(tmp_path: Path) -> tuple[Database, Settings, int]:
    roots = {name: tmp_path / name for name in ("cache", "work", "config", "backups")}
    for path in roots.values():
        path.mkdir()
    library = tmp_path / "library"
    library.mkdir()
    configured = replace(
        settings,
        library_root=library,
        pipeline_root=tmp_path,
        cache_root=roots["cache"],
        work_root=roots["work"],
        config_root=roots["config"],
        backup_root=roots["backups"],
        database_path=roots["config"] / "app.db",
        storage_mode="aparte_schijf",
    )
    database = Database(configured)
    database.initialize()
    profile_id = database.execute_insert("INSERT INTO profiel(naam,inst) VALUES ('P','{}')")
    library_id = database.execute_insert(
        "INSERT INTO library(naam,pad,profiel_id) VALUES ('L',?,?)",
        (str(library), profile_id),
    )
    file_id = database.execute_insert(
        """
        INSERT INTO bestand(library_id,pad,grootte,mtime,categorie,gescand_op)
        VALUES (?,?,1,1,'sdr',CURRENT_TIMESTAMP)
        """,
        (library_id, str(library / "film.mkv")),
    )
    decision_id = database.execute_insert(
        """
        INSERT INTO besluit(bestand_id,actie,baan,reden,payload,gemaakt_op)
        VALUES (?,'hercoderen','cpu','test','{}',CURRENT_TIMESTAMP)
        """,
        (file_id,),
    )
    job_id = database.execute_insert(
        "INSERT INTO job(besluit_id,bestand_id,baan,status) VALUES (?,?,'cpu','klaar')",
        (decision_id, file_id),
    )
    return database, configured, job_id


def test_logrotatie_begrenst_bestand_en_database(tmp_path: Path) -> None:
    database, configured, job_id = _case(tmp_path)
    log_dir = configured.config_root / "logs"
    log_dir.mkdir()
    completed = log_dir / f"job-{job_id}.log"
    completed.write_text("".join(f"regel {index}\n" for index in range(300)))
    old = log_dir / "job-999.log"
    old.write_text("oud\n")
    old_time = (datetime.now(UTC) - timedelta(days=31)).timestamp()
    os.utime(old, (old_time, old_time))
    with database.connection() as connection:
        connection.executemany(
            "INSERT INTO logregel(job_id,niveau,tekst,tijd) VALUES (?, 'info', ?, ?)",
            [(job_id, f"regel {index}", datetime.now(UTC).isoformat()) for index in range(600)],
        )
        connection.commit()

    result = rotate_logs(database, configured)

    assert result == {"verwijderd": 1, "ingekort": 1}
    assert len(completed.read_text().splitlines()) == 200
    count = database.fetch_one("SELECT COUNT(*) AS aantal FROM logregel WHERE job_id=?", (job_id,))
    assert count is not None and count["aantal"] == 500


@pytest.mark.usefixtures("ruime_cache")
def test_cachegrenzen_pauzeren_en_herstellen_alleen_eigen_pauze(tmp_path: Path) -> None:
    database, configured, _job_id = _case(tmp_path)
    database.execute(
        "UPDATE instelling SET cache_waarschuwing_gb=1000000000,cache_pauze_gb=999999999"
    )
    assert "gepauzeerd" in str(storage_guard(database, configured)["status"])
    assert all(
        row["gepauzeerd"] for row in database.fetch_all("SELECT gepauzeerd FROM worker_config")
    )

    database.execute("UPDATE instelling SET cache_waarschuwing_gb=1,cache_pauze_gb=1")
    storage_guard(database, configured)
    assert not any(
        row["gepauzeerd"] for row in database.fetch_all("SELECT gepauzeerd FROM worker_config")
    )


def test_ontbrekende_latere_werkruimte_pauzeert_zonder_crash(tmp_path: Path) -> None:
    database, configured, _job_id = _case(tmp_path)
    shutil.rmtree(configured.cache_root)

    result = storage_guard(database, configured)

    assert result["vrij_bytes"] == 0
    assert "gepauzeerd" in str(result["status"])
    assert all(
        row["gepauzeerd"] for row in database.fetch_all("SELECT gepauzeerd FROM worker_config")
    )


def test_weekonderhoud_en_drie_identieke_fouten(tmp_path: Path) -> None:
    database, _configured, job_id = _case(tmp_path)
    now = datetime.now(UTC)
    assert weekly_database_maintenance(database, now)
    assert not weekly_database_maintenance(database, now + timedelta(days=6))
    assert weekly_database_maintenance(database, now + timedelta(days=8))

    assert record_failure(database, job_id, "zelfde fout", "ExecutionError") == 1
    assert record_failure(database, job_id, "zelfde fout", "ExecutionError") == 2
    assert record_failure(database, job_id, "zelfde fout", "ExecutionError") == 3


@pytest.mark.parametrize("transactie", ["BEGIN", "BEGIN IMMEDIATE"])
def test_weekonderhoud_stelt_bezet_slot_uit_en_haalt_het_in(tmp_path, transactie):
    database, _, job = _case(tmp_path)
    database.execute("UPDATE job SET percent=37 WHERE id=?", (job,))
    with ThreadPoolExecutor(1) as pool, closing(sqlite3.connect(database.path)) as peer:
        peer.execute(transactie)
        assert peer.execute("SELECT percent FROM job WHERE id=?", (job,)).fetchone() == (37,)
        onderhoud = pool.submit(weekly_database_maintenance, database)
        try:
            assert onderhoud.result(timeout=3) is False
            if transactie == "BEGIN":
                database.execute("UPDATE job SET percent=65 WHERE id=?", (job,))
                assert database.fetch_one("SELECT percent FROM job WHERE id=?", (job,))[0] == 65
            assert database.fetch_one(
                "SELECT 1 FROM onderhoud_status WHERE sleutel='sqlite_weekonderhoud'"
            ) is None
        finally:
            peer.rollback()
    assert weekly_database_maintenance(database)
    assert not weekly_database_maintenance(database)


def test_wachtende_schrijver_laat_mapsloten_vrij_en_herleest_schema(tmp_path, monkeypatch):
    import fcntl
    from threading import Event

    from app.db import SchemaTooNew

    db, _, job = _case(tmp_path)
    core = tmp_path / "core"
    core.mkdir()
    started = Event()
    connect = db.connect

    def tracked_connect():
        conn = connect()
        conn.set_trace_callback(lambda sql: started.set() if sql == "BEGIN IMMEDIATE" else None)
        return conn

    monkeypatch.setattr(db, "connect", tracked_connect)
    with ThreadPoolExecutor(1) as pool, closing(sqlite3.connect(db.path)) as migration:
        migration.execute("BEGIN IMMEDIATE")
        blocked = pool.submit(db.execute, "UPDATE job SET percent=65 WHERE id=?", (job,))
        try:
            assert started.wait(3)
            for folder in (core, db.path.parent):
                fd = os.open(folder, os.O_RDONLY | os.O_DIRECTORY)
                try:
                    fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
                finally:
                    os.close(fd)
            migration.execute("INSERT INTO schema_versie VALUES (999, 'proef', 'proef')")
        finally:
            migration.commit()
        with pytest.raises(SchemaTooNew):
            blocked.result(timeout=3)
        assert migration.execute("SELECT percent FROM job WHERE id=?", (job,)).fetchone() == (0,)


def _trace_sql(db, monkeypatch, trace):
    open_connection = db._open_connection

    def tracked_open(*, timeout):
        conn = open_connection(timeout=timeout)
        conn.set_trace_callback(trace)
        return conn

    monkeypatch.setattr(db, "_open_connection", tracked_open)


def test_weekonderhoud_toetst_schema_in_het_exclusieve_slot(tmp_path, monkeypatch):
    from app.db import SchemaTooNew

    db, _, _ = _case(tmp_path)
    expected = []

    def trace(sql):
        if sql == "BEGIN EXCLUSIVE":
            with closing(sqlite3.connect(db.path)) as migration:
                migration.execute("INSERT INTO schema_versie VALUES (999, 'proef', 'proef')")
                migration.commit()
                expected.extend(migration.iterdump())

    _trace_sql(db, monkeypatch, trace)
    with pytest.raises(SchemaTooNew):
        weekly_database_maintenance(db)
    with closing(sqlite3.connect(db.path)) as check:
        assert expected and list(check.iterdump()) == expected


def test_uurcyclus_neemt_sqlite_slot_buiten_de_mapsloten(tmp_path, monkeypatch):
    import fcntl

    from app.onderhoud import run_maintenance

    db, configured, _ = _case(tmp_path)
    db.execute("UPDATE functie SET ingeschakeld=0 WHERE sleutel='automatische_backups'")
    core = tmp_path / "core"
    core.mkdir()
    free_folders = []

    def trace(sql):
        if sql == "BEGIN EXCLUSIVE":
            for folder in (core, db.path.parent):
                fd = os.open(folder, os.O_RDONLY | os.O_DIRECTORY)
                try:
                    fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
                    free_folders.append(folder)
                except BlockingIOError:
                    pass
                finally:
                    os.close(fd)

    _trace_sql(db, monkeypatch, trace)
    run_maintenance(db, configured)
    assert free_folders == [core, db.path.parent]
    assert db.fetch_one("SELECT 1 FROM onderhoud_status WHERE sleutel='sqlite_weekonderhoud'")


def test_wachtrijindex_blijft_snel_bij_honderdduizend_jobs(tmp_path: Path) -> None:
    database, _configured, _job_id = _case(tmp_path)
    with database.connection() as connection:
        connection.executescript(
            """
            WITH RECURSIVE seq(x) AS (
                SELECT 2 UNION ALL SELECT x + 1 FROM seq WHERE x <= 100000
            )
            INSERT INTO bestand(id,library_id,pad,grootte,mtime,categorie,gescand_op)
            SELECT x,1,'/library/test-' || x || '.mkv',1000,1,'sdr',CURRENT_TIMESTAMP FROM seq;

            INSERT INTO besluit(id,bestand_id,actie,baan,reden,payload,gemaakt_op)
            SELECT id,id,'hercoderen','cpu','schaaltest','{}',CURRENT_TIMESTAMP
            FROM bestand WHERE id > 1;

            INSERT INTO job(id,besluit_id,bestand_id,baan,status,prioriteit)
            SELECT id,id,id,'cpu','wacht',id % 10 FROM bestand WHERE id > 1;
            """
        )
        started = time.monotonic()
        rows = connection.execute(
            """
            SELECT id FROM job WHERE status='wacht' AND baan='cpu'
            ORDER BY prioriteit DESC,id LIMIT 100
            """
        ).fetchall()
        elapsed = time.monotonic() - started
        plan = " ".join(
            str(row[3])
            for row in connection.execute(
                "EXPLAIN QUERY PLAN SELECT id FROM job "
                "WHERE status='wacht' AND baan='cpu' ORDER BY prioriteit DESC,id LIMIT 100"
            )
        )
    assert len(rows) == 100
    assert elapsed < 1.0
    assert "idx_job_status" in plan
