from __future__ import annotations

import os
import shutil
import sqlite3
import time
from concurrent.futures import ThreadPoolExecutor
from contextlib import closing, contextmanager
from dataclasses import replace
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest

from app.config import Settings, settings
from app.db import Database
from app.mediaguard.holds import apply_playback_hold
from app.onderhoud import (
    lane_pause_state,
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


def test_bezette_map_leidt_tot_begrensd_opnieuw_reserveren(tmp_path, monkeypatch):
    db, _, job = _case(tmp_path)
    lock = db.startup_lock
    pogingen = 0
    eerste = None

    @contextmanager
    def bezet_bij_hercontrole(*, blocking=True):
        nonlocal pogingen, eerste
        if not blocking:
            pogingen += 1
            if eerste is None:
                eerste = time.monotonic()
            if time.monotonic() - eerste < 0.15:
                raise BlockingIOError
        with lock(blocking=blocking):
            yield

    monkeypatch.setattr(db, "startup_lock", bezet_bij_hercontrole)
    db.execute("UPDATE job SET percent=65 WHERE id=?", (job,))
    assert db.fetch_one("SELECT percent FROM job WHERE id=?", (job,))[0] == 65
    assert pogingen <= 12


@pytest.mark.usefixtures("ruime_cache")
def test_opslagbewaking_gebruikt_grenzen_van_de_schrijftransactie(tmp_path, monkeypatch):
    db, configured, _ = _case(tmp_path)
    db.execute("UPDATE instelling SET cache_pauze_gb=1,cache_waarschuwing_gb=2")
    ingesteld = False

    def trace(sql):
        nonlocal ingesteld
        if sql == "BEGIN IMMEDIATE" and not ingesteld:
            with closing(sqlite3.connect(db.path)) as other, other:
                other.execute(
                    "UPDATE instelling SET cache_pauze_gb=999999,cache_waarschuwing_gb=1000000"
                )
            ingesteld = True

    _trace_sql(db, monkeypatch, trace)
    storage_guard(db, configured)
    assert ingesteld
    assert db.fetch_one("SELECT gepauzeerd FROM worker_config WHERE baan='cpu'")[0] == 1


@pytest.mark.usefixtures("ruime_cache")
@pytest.mark.parametrize("herstel", [False, True], ids=["uitval", "herstel"])
def test_wachtende_opslagcontrole_behoudt_actuele_pauzes(tmp_path, monkeypatch, herstel):
    db, configured, _ = _case(tmp_path)
    tweede = Database(configured)
    cache = configured.cache_root
    apart = cache.with_name("ontkoppeld")
    apply_playback_hold(db, True, ["cpu"], "iemand kijkt")
    if herstel:
        cache.rename(apart)
        storage_guard(db, configured)

    # Laat een tweede controle als eerste schrijven terwijl de eerste nog op
    # haar schrijftransactie wacht. De opslag verandert tussen de twee aanroepen.
    tussendoor = []

    def trace(sql):
        if sql == "BEGIN IMMEDIATE" and not tussendoor:
            if herstel:
                apart.rename(cache)
            else:
                cache.rename(apart)
            tussendoor.append(storage_guard(tweede, configured))

    _trace_sql(db, monkeypatch, trace)
    resultaat = storage_guard(db, configured)
    assert tussendoor == [resultaat]
    banen = lane_pause_state(db)
    assert all(baan["storage_pause"] == (not herstel) for baan in banen.values())
    if herstel:
        assert banen["cpu"]["reason"] == "Kijken: iemand kijkt"
        assert not banen["gpu"]["paused"]
    else:
        assert all(baan["reason"].startswith("automatisch: opslag") for baan in banen.values())


@pytest.mark.parametrize("wijziging", ["onbekend", "teruggezet"])
def test_backup_wordt_alleen_bij_de_gekopieerde_database_geregistreerd(
    tmp_path, monkeypatch, wijziging
):
    from app.backups import BackupError, create_backup
    from app.db import SchemaTooNew

    db, configured, _ = _case(tmp_path)
    eerdere_paden = set(configured.backup_root.iterdir())
    restored = tmp_path / "terugzetting.db"
    with db.connection(write=False) as source, closing(sqlite3.connect(restored)) as copy:
        source.backup(copy)
    connection = db.connection

    @contextmanager
    def wissel_voor_registratie(*, write=True, blocking=True):
        if write:
            with db.startup_lock():
                if wijziging == "onbekend":
                    with closing(sqlite3.connect(db.path)) as other, other:
                        other.execute("INSERT INTO schema_versie VALUES (999,'proef','proef')")
                else:
                    restored.replace(db.path)
        with connection(write=write, blocking=blocking) as conn:
            yield conn

    monkeypatch.setattr(db, "connection", wissel_voor_registratie)
    with pytest.raises(SchemaTooNew if wijziging == "onbekend" else BackupError):
        create_backup(db, configured)
    assert set(configured.backup_root.iterdir()) == eerdere_paden
    with closing(sqlite3.connect(db.path)) as current:
        assert current.execute("SELECT count(*) FROM backup_status").fetchone() == (0,)


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

    from app.backups import enforce_retention, verify_backup
    from app.onderhoud import run_maintenance

    db, configured, _ = _case(tmp_path)
    core = tmp_path / "core"
    core.mkdir()
    free_folders = []
    bezet = []

    def trace(sql):
        if sql in ("BEGIN EXCLUSIVE", "BEGIN IMMEDIATE"):
            for folder in (core, db.path.parent):
                fd = os.open(folder, os.O_RDONLY | os.O_DIRECTORY)
                try:
                    fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
                    if sql == "BEGIN EXCLUSIVE":
                        free_folders.append(folder)
                except BlockingIOError:
                    bezet.append((sql, folder.name))
                finally:
                    os.close(fd)

    _trace_sql(db, monkeypatch, trace)
    run_maintenance(db, configured)
    backup = db.fetch_one("SELECT id FROM backup_status")[0]
    assert verify_backup(db, configured, backup)["herstelbaar"]
    assert enforce_retention(db, keep=0) == 1
    assert not bezet
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
