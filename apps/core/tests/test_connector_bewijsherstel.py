"""Migratie vanaf een gevulde installatie en bewijs bij hervatte jobs."""

from __future__ import annotations

import json
import os
import sqlite3
from contextlib import closing
from dataclasses import replace
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from mediaguard import aio, db, jobs
from mediaguard.app import create_app
from mediaguard.maintenance import preupdate

FIXTURES = Path(__file__).resolve().parents[3] / "tests/fixtures/aio"
VERSION = "152_connector_evidence_version"


@pytest.fixture
def before_evidence(tmp_path, settings, monkeypatch):
    settings = replace(settings, data_dir=tmp_path / "installatie/core")
    path = settings.db_path
    path.parent.mkdir(parents=True)
    scripts = sorted(db.MIGRATIONS_DIR.glob("*.sql"))
    old = tmp_path / "tot-151"
    old.mkdir()
    for script in scripts:
        if script.stem < VERSION:
            (old / script.name).symlink_to(script)
    with closing(sqlite3.connect(path)) as conn:
        conn.executescript((FIXTURES / "core-1.2.2.sql").read_text())
    with monkeypatch.context() as m, closing(db.Database(path)) as database:
        m.setattr(db, "MIGRATIONS_DIR", old)
        database.initialize()
        number = database.execute_insert(
            "INSERT INTO jobs(job_type,status,priority,progress,attempts,created_at) "
            "VALUES ('library.sync','retry_scheduled',-500,0.37,1,?)",
            (db.utcnow(),),
        )
    return settings, number, old


def database_contents(path):
    with closing(sqlite3.connect(path.as_uri() + "?mode=ro", uri=True)) as conn:
        assert conn.execute("PRAGMA integrity_check").fetchone()[0] == "ok"
        return list(conn.iterdump())


@pytest.mark.parametrize("fault", ["opslag_vol", "rechten", "onvolledige_set"])
def test_152_zonder_geverifieerde_set_weigert_en_herstelt(before_evidence, monkeypatch, fault):
    settings, _, pending = before_evidence
    path = settings.db_path
    (script,) = db.MIGRATIONS_DIR.glob("152_*.sql")
    (pending / script.name).symlink_to(script)
    monkeypatch.setattr(db, "MIGRATIONS_DIR", pending)
    before = database_contents(path)
    folder = settings.backup_dir / preupdate.FOLDER
    if fault == "onvolledige_set":
        with closing(db.Database(path)) as database:
            previous = database.fetch_one("SELECT MAX(version) FROM schema_migrations")[0]
        made = preupdate.make_set(
            folder, path, encode_dir=None, from_schema=previous, to_schema=VERSION, reason="update"
        )
        (made / "mediaguard.db").unlink()
        monkeypatch.setenv("MG_PRE_UPDATE_SET", str(made))

    original = os.replace

    def deny(src, dst):
        if Path(dst).name == "mediaguard.db" and folder in Path(dst).parents:
            if fault == "rechten":
                raise PermissionError(13, "geen schrijfrecht op de proefopslag")
            raise OSError(28, "proefopslag vol")
        return original(src, dst)

    with monkeypatch.context() as blocked:
        blocked.setattr(os, "replace", deny)
        if fault != "onvolledige_set":
            with pytest.raises(RuntimeError, match="back-up"):
                aio.before_services({}, path.parent.parent, None)
        with closing(db.Database(path)) as database, pytest.raises(RuntimeError, match="back-up"):
            database.initialize()
    assert database_contents(path) == before
    monkeypatch.delenv("MG_PRE_UPDATE_SET", raising=False)
    with closing(db.Database(path)) as database:
        database.initialize()
        assert database.fetch_one("SELECT MAX(version) FROM schema_migrations")[0] == VERSION
    copies = [p for p in folder.glob("*/mediaguard.db") if not preupdate.verify(p.parent)]
    assert any(database_contents(p) == before for p in copies)


def test_historisch_jobpakket_blijft_identiek_na_twee_pogingen(before_evidence, monkeypatch):
    settings, number, _ = before_evidence
    meta = json.loads((FIXTURES / "core-1.2.2.json").read_text())
    path = f"/connector/v1/evidence/job:{number}"
    app = create_app(settings)
    with closing(app.state.ctx.db), TestClient(app) as client:
        assert client.post(
            "/api/v1/auth/login", json={"username": meta["user"], "password": meta["password"]}
        ).is_success
        initial_response = client.get(path)
        assert initial_response.is_success
        original = initial_response.json()
        assert original["app_version"] is None and original["details"]["app_version"] == "onbekend"
        assert original["response"]["status"] == "retry_scheduled"
        attempts = []
        database = app.state.ctx.db
        for version in ("2.3.4", "2.3.5"):
            monkeypatch.setattr(jobs, "__version__", version)
            monkeypatch.setattr("mediaguard.audit.__version__", version)
            assert app.state.ctx.services["jobs"]._claim_next()["id"] == number
            assert client.get(path).content == initial_response.content
            audit = database.fetch_one(
                "SELECT id FROM audit_events WHERE action='job.started' "
                "AND target_type='job' AND target_id=? ORDER BY id DESC",
                (str(number),),
            )
            attempt = client.get(f"/connector/v1/evidence/audit:{audit['id']}").json()
            assert (
                attempt["app_version"] == version
                and attempt["details"]["app_version"] == "opgeslagen"
            )
            attempts.append(attempt)
            database.execute("UPDATE jobs SET status='retry_scheduled' WHERE id=?", (number,))
        assert attempts[0]["id"] != attempts[1]["id"]
        assert tuple(
            database.fetch_one("SELECT attempts,progress FROM jobs WHERE id=?", (number,))
        ) == (3, 0.37)
    restarted = create_app(settings)
    with closing(restarted.state.ctx.db), TestClient(restarted) as client:
        assert client.post(
            "/api/v1/auth/login", json={"username": meta["user"], "password": meta["password"]}
        ).is_success
        assert client.get(path).content == initial_response.content
        for attempt in attempts:
            assert client.get("/connector/v1/evidence/" + attempt["id"]).json() == attempt


def test_bestaand_versiegebonden_jobresultaat_en_audit_blijven_bewaard(
    before_evidence, monkeypatch
):
    from mediaguard.audit import audit

    settings, number, previous = before_evidence
    for script in db.MIGRATIONS_DIR.glob("*.sql"):
        if VERSION <= script.stem < "155":
            (previous / script.name).symlink_to(script)
    with monkeypatch.context() as m, closing(db.Database(settings.db_path)) as database:
        m.setattr(db, "MIGRATIONS_DIR", previous)
        database.initialize()
        database.execute(
            "UPDATE jobs SET status='failed', started_at=?, finished_at=?, evidence_app_version=?,"
            "payload=?,result=? WHERE id=?",
            (
                db.utcnow(),
                db.utcnow(),
                "2.3.4",
                db.dumps({"full": True}),
                db.dumps(
                    {
                        "movies": 0,
                        "episodes": 17,
                        "failed_items": True,
                        "ongebruikt": "privéfixture",
                    }
                ),
                number,
            ),
        )
        related = audit(
            database, actor="proef", action="job.retry", target_type="job", target_id=number
        )
    app = create_app(settings)
    meta = json.loads((FIXTURES / "core-1.2.2.json").read_text())
    path = f"/connector/v1/evidence/job:{number}"
    with closing(app.state.ctx.db) as database, TestClient(app) as client:
        assert client.post(
            "/api/v1/auth/login", json={"username": meta["user"], "password": meta["password"]}
        ).is_success
        original = client.get(path)
        body = original.json()
        assert original.is_success and body["app_version"] == "2.3.4"
        assert body["audit"]["id"] == f"audit:{related}" and body["request"]["full"] is True
        assert body["response"] == {
            "status": "failed",
            "movies": 0,
            "episodes": 17,
            "active": None,
            "failed_items": None,
        }
        assert body["started_at"] and body["finished_at"]
        database.execute(
            "UPDATE jobs SET status='queued',result=NULL,finished_at=NULL WHERE id=?", (number,)
        )
        assert app.state.ctx.services["jobs"]._claim_next()["id"] == number
        assert client.get(path).content == original.content
        saved = database.fetch_one(
            "SELECT result FROM job_evidence_snapshot WHERE job_id=?", (number,)
        )
        assert "privéfixture" not in saved["result"]
