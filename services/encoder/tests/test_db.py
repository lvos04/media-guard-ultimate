from __future__ import annotations

import shutil
import sqlite3
from dataclasses import replace
from pathlib import Path

import pytest

from app.config import settings
from app.db import Database


def test_migraties_zijn_herhaalbaar(tmp_path: Path) -> None:
    roots = {name: tmp_path / name for name in ("cache", "work", "config", "backups")}
    for path in roots.values():
        path.mkdir()
    test_settings = replace(
        settings,
        pipeline_root=tmp_path,
        cache_root=roots["cache"],
        work_root=roots["work"],
        config_root=roots["config"],
        backup_root=roots["backups"],
        database_path=roots["config"] / "app.db",
    )
    database = Database(test_settings)
    database.initialize()
    database.initialize()

    # 016 = additieve MediaGuard-brugmigratie (docs/AV1_REPO_INTEGRATION.md),
    # 018 = wacht_op_commit telt mee in de unieke actieve-jobindex (017 bewust
    # overgeslagen: dat nummer stond op een niet-uitgebrachte WIP-tak).
    # 019 = indexen voor de snelle joblijst (GET /api/jobs).
    # 022 = afstelling (instelbaar in de app) + kwaliteitsmeting per job.
    # 023 = noodstop in de engine + vastloperstatistiek (rapport 6).
    # 027 = additieve termijn en reden voor de platformpauze.
    assert database.applied_versions() == set(range(1, 28))
    assert database.fetch_one("PRAGMA journal_mode")[0] == "wal"
    assert database.fetch_one("SELECT COUNT(*) FROM functie")[0] == 13
    assert database.fetch_one(
        "SELECT max_gelijktijdige_encodes FROM instelling WHERE id=1"
    )[0] == 1


def test_migratie_herleest_plan_na_terugzetten(tmp_path, monkeypatch):
    from contextlib import closing

    migrations = tmp_path / "migraties"
    migrations.mkdir()
    first = migrations / "001_eerste.sql"
    first.write_text("CREATE TABLE proef (id INTEGER PRIMARY KEY);")
    cfg = replace(settings, config_root=tmp_path / "encode", database_path=tmp_path / "encode/db",
                  backup_root=tmp_path / "archief")
    db = Database(cfg, migrations)
    db.initialize()
    restored = tmp_path / "terugzetten.db"
    with db.connection(write=False) as source, closing(sqlite3.connect(restored)) as copy:
        source.backup(copy)
        copy.execute("INSERT INTO proef VALUES (7)")
        copy.commit()
    (migrations / "002_naam.sql").write_text("ALTER TABLE proef ADD COLUMN naam TEXT;")
    db.apply_migrations()
    third = migrations / "003_vullen.sql"
    third.write_text("UPDATE proef SET naam='hersteld';")
    read = Path.read_text

    def replace_before_write(path, *args, **kwargs):
        if path == third and restored.exists():
            with db.startup_lock():
                restored.replace(db.path)
        return read(path, *args, **kwargs)

    monkeypatch.setattr(Path, "read_text", replace_before_write)
    db.apply_migrations()
    assert db.applied_versions() == {1, 2, 3}
    assert db.fetch_one("SELECT naam FROM proef WHERE id=7")[0] == "hersteld"


def test_migratie_negen_normaliseert_dubbele_wachtrij_en_cpu_workers(tmp_path: Path) -> None:
    roots = {name: tmp_path / name for name in ("cache", "work", "config", "backups")}
    for path in roots.values():
        path.mkdir()
    test_settings = replace(
        settings,
        pipeline_root=tmp_path,
        cache_root=roots["cache"],
        work_root=roots["work"],
        config_root=roots["config"],
        backup_root=roots["backups"],
        database_path=roots["config"] / "app.db",
    )
    migrations = tmp_path / "migrations"
    migrations.mkdir()
    source = Path(__file__).parents[1] / "app" / "migrations"
    for migration in sorted(source.glob("00[1-8]_*.sql")):
        shutil.copy2(migration, migrations / migration.name)
    database = Database(test_settings, migrations)
    database.initialize()
    profile_id = database.execute_insert(
        "INSERT INTO profiel(naam,inst) VALUES ('Test','{}')"
    )
    library_id = database.execute_insert(
        "INSERT INTO library(naam,pad,profiel_id) VALUES ('Test','/media',?)",
        (profile_id,),
    )

    def add_file(path: str) -> tuple[int, int, int]:
        file_id = database.execute_insert(
            "INSERT INTO bestand(library_id,pad,grootte,mtime,categorie,gescand_op) "
            "VALUES (?,?,1,1,'ok',CURRENT_TIMESTAMP)",
            (library_id, path),
        )
        decision_ids = [
            database.execute_insert(
                "INSERT INTO besluit(bestand_id,actie,baan,reden,payload,status,gemaakt_op) "
                "VALUES (?,'hercoderen','cpu','test','{}','ingepland',CURRENT_TIMESTAMP)",
                (file_id,),
            )
            for _ in range(2)
        ]
        return file_id, decision_ids[0], decision_ids[1]

    completed_file, completed_decision, duplicate_decision = add_file("/media/klaar.mkv")
    database.execute_insert(
        "INSERT INTO job(besluit_id,bestand_id,baan,status) VALUES (?,?,'cpu','klaar')",
        (completed_decision, completed_file),
    )
    completed_duplicate = database.execute_insert(
        "INSERT INTO job(besluit_id,bestand_id,baan,status) VALUES (?,?,'cpu','wacht')",
        (duplicate_decision, completed_file),
    )
    active_file, first_decision, second_decision = add_file("/media/wacht.mkv")
    first = database.execute_insert(
        "INSERT INTO job(besluit_id,bestand_id,baan,status) VALUES (?,?,'cpu','wacht')",
        (first_decision, active_file),
    )
    second = database.execute_insert(
        "INSERT INTO job(besluit_id,bestand_id,baan,status) VALUES (?,?,'cpu','bezig')",
        (second_decision, active_file),
    )
    database.execute("UPDATE worker_config SET aantal=2 WHERE baan='cpu'")
    shutil.copy2(source / "009_queue_safety.sql", migrations / "009_queue_safety.sql")

    database.initialize()

    assert database.fetch_one("SELECT status FROM job WHERE id=?", (completed_duplicate,))[0] == (
        "overgeslagen"
    )
    assert database.fetch_one("SELECT status FROM job WHERE id=?", (first,))[0] == "wacht"
    assert database.fetch_one("SELECT status FROM job WHERE id=?", (second,))[0] == "overgeslagen"
    assert database.fetch_one("SELECT aantal FROM worker_config WHERE baan='cpu'")[0] == 1
    with pytest.raises(sqlite3.IntegrityError):
        database.execute_insert(
            "INSERT INTO job(besluit_id,bestand_id,baan,status) VALUES (?,?,'cpu','wacht')",
            (second_decision, active_file),
        )


@pytest.fixture
def toekomstig_schema(tmp_path: Path):
    from contextlib import closing

    instance = replace(
        settings, config_root=tmp_path / "config", pipeline_root=tmp_path,
        work_root=tmp_path / "work", backup_root=tmp_path / "backups",
        database_path=tmp_path / "config" / "app.db", container_mode=True,
    )
    database = Database(instance)
    database.initialize()
    shutil.rmtree(instance.backup_root)
    with closing(sqlite3.connect(instance.database_path)) as conn, conn:
        # De onbekende versie is kleiner dan de hoogste bekende versie.
        conn.execute("INSERT INTO schema_versie VALUES (0, 'onbekend', 'fixture')")
        conn.execute("DELETE FROM schema_versie WHERE versie=25")
    return instance


def test_onbekend_schema_blokkeert_av1_ingangen(toekomstig_schema, monkeypatch, capsys):
    import sys

    from fastapi.testclient import TestClient

    from app import backups, cli
    from app.main import create_app

    monkeypatch.syspath_prepend(str(Path(__file__).resolve().parents[3] / "scripts"))
    import upgradebewijs

    path = toekomstig_schema.database_path
    voor = upgradebewijs.meet_db(path)
    db = Database(toekomstig_schema)
    db.initialize()
    assert upgradebewijs.meet_db(path) == voor
    assert not toekomstig_schema.backup_root.exists()

    app = create_app(toekomstig_schema, db)
    with TestClient(app) as client:
        for route in ("/health", "/livez", "/api/health", "/api/mediaguard/v1/health"):
            response = client.get(route)
            assert response.status_code == 503
            assert response.json()["reason_code"] == "schema_nieuwer"
        assert client.post("/api/backups").status_code == 409
    assert not toekomstig_schema.work_root.exists()

    from app.db import SchemaTooNew

    with pytest.raises(SchemaTooNew):
        backups.create_backup(Database(toekomstig_schema), toekomstig_schema)
    with pytest.raises(SchemaTooNew):
        backups.verify_backup(Database(toekomstig_schema), toekomstig_schema, 1)
    with pytest.raises(SchemaTooNew):
        backups.enforce_retention(Database(toekomstig_schema))
    with pytest.raises(SchemaTooNew):
        Database(toekomstig_schema).apply_migrations()
    monkeypatch.setattr(cli, "settings", toekomstig_schema)
    for args in (["scan", "1"], ["draai", "1"], ["planner-start"]):
        assert cli.main(args) != 0
        assert "database is van een nieuwere" in capsys.readouterr().err
    assert upgradebewijs.meet_db(path) == voor
    assert not toekomstig_schema.backup_root.exists()
    sys.modules.pop("upgradebewijs", None)


def test_draaiende_av1_gaat_blijvend_naar_herstelweergave(tmp_path: Path) -> None:
    from contextlib import closing

    from fastapi.testclient import TestClient

    from app.db import SchemaTooNew
    from app.main import create_app

    instance = replace(
        settings, config_root=tmp_path, pipeline_root=tmp_path, work_root=tmp_path / "work",
        backup_root=tmp_path / "backups", database_path=tmp_path / "app.db",
    )
    database = Database(instance)
    with TestClient(create_app(instance, database, start_planner=False, validate_storage=False),
                    raise_server_exceptions=False) as client:
        assert client.get("/livez").status_code == 200
        with closing(sqlite3.connect(database.path)) as conn, conn:
            conn.execute("INSERT INTO schema_versie VALUES (999, 'proef', 'proef')")
        with pytest.raises(SchemaTooNew):
            database.execute("CREATE TABLE proef (id INTEGER)")
        response = client.get("/livez")
        assert response.status_code == 503
        assert response.json()["reason_code"] == "schema_nieuwer"
        assert client.post("/api/backups").status_code == 409
        with closing(sqlite3.connect(database.path)) as conn, conn:
            assert not conn.execute("SELECT 1 FROM sqlite_master WHERE name='proef'").fetchone()
            conn.execute("DELETE FROM schema_versie WHERE versie=999")
        assert client.get("/livez").status_code == 503


@pytest.mark.parametrize("in_wal", [False, True])
def test_av1_voorcontrole_leest_ook_wal_en_maakt_geen_hulpbestanden(tmp_path, in_wal):
    from contextlib import closing

    from app.db import SchemaTooNew

    path = tmp_path / "app.db"
    instance = replace(settings, config_root=tmp_path, database_path=path)
    with closing(sqlite3.connect(path)) as conn, conn:
        conn.execute("PRAGMA journal_mode=WAL")
        conn.execute("CREATE TABLE schema_versie (versie INTEGER PRIMARY KEY)")
        conn.commit()
        conn.execute("INSERT INTO schema_versie VALUES (999)")
        conn.commit()
        if in_wal:
            assert path.with_suffix(".db-wal").stat().st_size > 0
            with pytest.raises(SchemaTooNew):
                Database(instance).require_writable()
            return
    files = {p.name: p.read_bytes() for p in tmp_path.iterdir()}
    with pytest.raises(SchemaTooNew):
        Database(instance).require_writable()
    assert {p.name: p.read_bytes() for p in tmp_path.iterdir()} == files


@pytest.mark.parametrize("fout", ["nieuwer_schema", "ongeldige_sql"])
def test_av1_migratiescript_blijft_in_de_getoetste_transactie(tmp_path, monkeypatch, fout):
    from contextlib import closing

    from test_onderhoud import _case

    from app.db import MigrationError, SchemaTooNew

    db, _, _ = _case(tmp_path)
    db.migrations_dir = shutil.copytree(db.migrations_dir, tmp_path / "migraties")
    versie = db.migration_files()[-1][0] + 1
    script = db.migrations_dir / f"{versie:03d}_proef.sql"
    sql = "CREATE TABLE proef (id INTEGER);"
    if fout == "ongeldige_sql":
        sql += " INSERT INTO ontbrekende_tabel VALUES (1);"
    script.write_text(sql)
    gelezen = False
    read = Path.read_text
    connect = db.connect

    def lees(pad, *args, **kwargs):
        nonlocal gelezen
        if pad == script:
            gelezen = True
        return read(pad, *args, **kwargs)

    def verbinden():
        conn = connect()

        def trace(statement):
            if gelezen and statement.startswith("BEGIN IMMEDIATE") and fout == "nieuwer_schema":
                with closing(sqlite3.connect(db.path)) as peer, peer:
                    peer.execute("INSERT INTO schema_versie VALUES (999, 'proef', 'proef')")

        conn.set_trace_callback(trace)
        return conn

    monkeypatch.setattr(Path, "read_text", lees)
    monkeypatch.setattr(db, "connect", verbinden)
    with pytest.raises(SchemaTooNew if fout == "nieuwer_schema" else MigrationError):
        db.apply_migrations()
    with closing(sqlite3.connect(db.path)) as conn:
        assert conn.execute("SELECT 1 FROM sqlite_master WHERE name='proef'").fetchone() is None
        assert conn.execute(
            "SELECT 1 FROM schema_versie WHERE versie=?", (versie,),
        ).fetchone() is None
        toekomst = conn.execute("SELECT 1 FROM schema_versie WHERE versie=999").fetchone()
        assert toekomst == ((1,) if fout == "nieuwer_schema" else None)
