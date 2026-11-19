"""Migratie vanaf een gevulde installatie en bewijs bij hervatte jobs."""

from __future__ import annotations

import json
import os
import sqlite3
from contextlib import closing
from dataclasses import replace
from datetime import UTC, datetime, timedelta
from pathlib import Path
from unittest.mock import patch

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


def previous_schema(before_evidence, monkeypatch, maximum="154"):
    settings, number, folder = before_evidence
    for script in db.MIGRATIONS_DIR.glob("*.sql"):
        if script.stem >= VERSION and script.stem[:3] <= maximum:
            (folder / script.name).symlink_to(script)
    with monkeypatch.context() as m, closing(db.Database(settings.db_path)) as database:
        m.setattr(db, "MIGRATIONS_DIR", folder)
        database.initialize()
    return settings, number


def login(client):
    meta = json.loads((FIXTURES / "core-1.2.2.json").read_text())
    response = client.post(
        "/api/v1/auth/login",
        json={
            "username": meta["user"],
            "password": meta["password"],
        },
    )
    assert response.is_success
    client.headers["X-CSRF-Token"] = response.json()["csrf_token"]


def proof(client, source, number):
    response = client.get(f"/connector/v1/evidence/{source}:{number}")
    assert response.is_success
    return response


def test_historische_tellers_volgen_de_oorspronkelijke_jsonlezer(before_evidence, monkeypatch):
    settings, _ = previous_schema(before_evidence, monkeypatch)
    with closing(db.Database(settings.db_path)) as database:
        ids = {}
        for raw in (
            '{"episodes":17}',
            '{"episodes":1,"episodes":17}',
            '{"episodes":17,"extra":NaN}',
            '{"episodes":17,"extra":' + "[" * 1001 + "0" + "]" * 1001 + "}",
        ):
            number = jobs.enqueue(database, "library.sync")
            database.execute(
                "UPDATE jobs SET status='succeeded',result=?,finished_at=? WHERE id=?",
                (raw, db.utcnow(), number),
            )
            ids[number] = raw
    app = create_app(settings)
    with closing(app.state.ctx.db) as database, TestClient(app) as client:
        login(client)
        for number, raw in ids.items():
            response = proof(client, "job", number).json()["response"]
            assert response["status"] == "succeeded" and response["episodes"] == 17
            assert database.fetch_one("SELECT result FROM jobs WHERE id=?", (number,))[0] == raw


@pytest.mark.parametrize(("schema", "expanded"), [("154", False), ("156", True)])
def test_profielbewijs_houdt_zijn_weergave_bij_upgrade(
    before_evidence, monkeypatch, schema, expanded
):
    from mediaguard.audit import audit

    settings, _ = previous_schema(before_evidence, monkeypatch, schema)
    with closing(db.Database(settings.db_path)) as database:
        old = audit(
            database,
            actor="proef",
            action="policy.profile_update",
            old_value={"prefer_hdr": False},
            new_value={"prefer_hdr": True},
        )
    app = create_app(settings)
    with closing(app.state.ctx.db) as database, TestClient(app) as client:
        login(client)
        original = proof(client, "audit", old).json()
        assert original["request"] == ({"prefer_hdr": False} if expanded else None)
        assert original["response"] == ({"prefer_hdr": True} if expanded else None)
        new = audit(
            database,
            actor="proef",
            action="policy.profile_update",
            old_value={"prefer_hdr": True},
            new_value={"prefer_hdr": False},
        )
        assert proof(client, "audit", new).json()["response"] == {"prefer_hdr": False}
        assert proof(client, "audit", old).json() == original


@pytest.mark.parametrize("check", ["auditretentie", "eindresultaat"])
def test_poging_behoudt_audit_en_eindresultaat_na_onderhoud(before_evidence, monkeypatch, check):
    import asyncio

    from mediaguard.audit import audit
    from mediaguard.maintenance import housekeeping

    settings, number = previous_schema(before_evidence, monkeypatch)
    old_time = (datetime.now(UTC) - timedelta(days=400)).isoformat(timespec="seconds")
    with closing(db.Database(settings.db_path)) as database, monkeypatch.context() as m:
        database.execute(
            "UPDATE jobs SET status='queued',created_at=? WHERE id=?", (old_time, number)
        )
        m.setattr("mediaguard.audit.utcnow", lambda: old_time)
        old = audit(
            database, actor="proef", action="job.retry", target_type="job", target_id=number
        )

    async def complete(job):
        return {"episodes": 17, "movies": 0, "extra": "privéfixture"}

    app = create_app(settings)
    with closing(app.state.ctx.db) as database, TestClient(app) as client:
        login(client)
        initial = proof(client, "job", number).content
        original = proof(client, "audit", old).content
        if check == "auditretentie":
            assert housekeeping.prune(app.state.ctx)["audit"] > 0
        monkeypatch.setitem(jobs.HANDLERS, "library.sync", complete)
        asyncio.run(app.state.ctx.services["jobs"].run_until_idle())
        if check == "auditretentie":
            assert proof(client, "audit", old).content == original
        else:
            assert client.get(f"/api/v1/jobs/{number}").json()["progress"] == 1
            end = database.fetch_one(
                "SELECT id FROM audit_events WHERE action='job.finished' "
                "AND target_type='job' AND target_id=?",
                (str(number),),
            )
            assert end is not None
            package = proof(client, "audit", end["id"])
            assert package.json()["started_at"] and package.json()["finished_at"]
            assert package.json()["response"]["status"] == "succeeded"
            assert (
                package.json()["response"]["episodes"] == 17 and "privéfixture" not in package.text
            )
        assert proof(client, "job", number).content == initial
    again = create_app(settings)
    with closing(again.state.ctx.db), TestClient(again) as client:
        login(client)
        assert proof(client, "job", number).content == initial
        assert proof(client, "audit", old).content == original
        if check == "eindresultaat":
            assert proof(client, "audit", end["id"]).content == package.content


@pytest.mark.parametrize("bron", ["profiel", "download", "arr", "geen"])
def test_schema_155_zonder_herkenbare_lezer_blijft_intact(before_evidence, monkeypatch, bron):
    from mediaguard.audit import audit

    settings, _ = previous_schema(before_evidence, monkeypatch, "155")
    with closing(db.Database(settings.db_path)) as database:
        database.execute("UPDATE instances SET suspended=1")
        database.execute("UPDATE source_instances SET archived=1")
        database.execute("DELETE FROM provisioning_config WHERE key='main'")
        database.execute("DELETE FROM audit_events WHERE action='policy.profile_update'")
        if bron == "profiel":
            audit(
                database,
                actor="proef",
                action="policy.profile_update",
                new_value={"prefer_hdr": True},
            )
        elif bron == "download":
            database.execute(
                "INSERT OR REPLACE INTO provisioning_config(key,value_json,updated_at,updated_by) "
                "VALUES ('main',?,?,'proef')",
                (db.dumps({"download_clients": [{"name": "Proefbron"}]}), db.utcnow()),
            )
        elif bron == "arr":
            database.execute("UPDATE instances SET suspended=0 WHERE archived=0")
        else:
            database.initialize()
            assert database.fetch_one("SELECT MAX(version) FROM schema_migrations")[0][:3] == "157"
            return
        before = database_contents(settings.db_path)
        with pytest.raises(RuntimeError, match="kernrevisie"):
            database.initialize()
        assert database_contents(settings.db_path) == before
        folder = settings.backup_dir / preupdate.FOLDER
        assert any(
            not preupdate.verify(p.parent) and database_contents(p) == before
            for p in folder.glob("*/mediaguard.db")
        )


def test_reeds_toegepaste_155_bewaart_pakket_en_ontsluit_brondetail(before_evidence, monkeypatch):
    settings, number = previous_schema(before_evidence, monkeypatch)
    folder = before_evidence[2]
    raw = '{"episodes":1,"episodes":17}'
    with closing(db.Database(settings.db_path)) as database:
        database.execute(
            "UPDATE jobs SET status='succeeded',started_at=?,finished_at=?,result=? WHERE id=?",
            (db.utcnow(), db.utcnow(), raw, number),
        )
        for script in db.MIGRATIONS_DIR.glob("*.sql"):
            if script.stem[:3] in ("155", "156"):
                (folder / script.name).symlink_to(script)
        with monkeypatch.context() as m:
            m.setattr(db, "MIGRATIONS_DIR", folder)
            database.initialize()
            recorded = database.fetch_one(
                "SELECT result FROM job_evidence_snapshot WHERE job_id=?", (number,)
            )[0]
            assert json.loads(recorded)["episodes"] == 1
    app = create_app(settings)
    with closing(app.state.ctx.db) as database, TestClient(app) as client:
        login(client)
        assert proof(client, "job", number).json()["response"]["episodes"] == 1
        end = database.fetch_one(
            "SELECT id FROM audit_events WHERE action='job.finished' AND target_id=?",
            (str(number),),
        )
        assert end is not None
        companion = proof(client, "audit", end["id"]).json()
        assert companion["response"]["episodes"] == 17 and companion["finished_at"]
        assert companion["app_version"] is None
        assert database.fetch_one("SELECT result FROM jobs WHERE id=?", (number,))[0] == raw
        assert (
            database.fetch_one(
                "SELECT result FROM job_evidence_snapshot WHERE job_id=?", (number,)
            )[0]
            == recorded
        )


def test_verlopen_auditnummer_uit_een_bestaande_155_blijft_bezet(before_evidence, monkeypatch):
    from mediaguard.audit import audit

    settings, _ = previous_schema(before_evidence, monkeypatch, "156")
    with closing(db.Database(settings.db_path)) as database:
        cutoff = database.fetch_one("SELECT MAX(audit_cutoff) FROM job_evidence_snapshot")[0]
        assert cutoff > 0
        database.execute("DELETE FROM audit_events")
        database.initialize()
        assert audit(database, actor="proef", action="job.retry") > cutoff


def test_eindbewijs_en_jobstatus_vormen_een_transactie(ctx, monkeypatch):
    import asyncio

    async def voltooi(job):
        return {"episodes": 17}

    monkeypatch.setitem(jobs.HANDLERS, "library.sync", voltooi)
    number = jobs.enqueue(ctx.db, "library.sync")
    runner = ctx.services["jobs"]
    claim = runner._claim_next()
    assert claim["id"] == number
    original_audit = jobs.audit

    def geen_eindbewijs(db, **fields):
        if fields["action"] == "job.finished":
            raise sqlite3.OperationalError("proefopslag onbeschikbaar")
        return original_audit(db, **fields)

    with monkeypatch.context() as m:
        m.setattr(jobs, "audit", geen_eindbewijs)
        with pytest.raises(sqlite3.OperationalError, match="proefopslag"):
            asyncio.run(runner._execute(claim))
    row = ctx.db.fetch_one("SELECT status,finished_at,result FROM jobs WHERE id=?", (number,))
    assert tuple(row) == ("running", None, None)
    assert runner.recover_after_restart() == 1
    asyncio.run(runner.run_until_idle())
    row = ctx.db.fetch_one("SELECT status,progress,result FROM jobs WHERE id=?", (number,))
    assert (row["status"], row["progress"], json.loads(row["result"])) == (
        "succeeded",
        1,
        {"episodes": 17},
    )


@pytest.mark.parametrize("scenario", ["planpauze", "eerder_geannuleerd"])
def test_annulering_behoudt_historisch_en_afzonderlijk_eindbewijs(
    before_evidence, monkeypatch, scenario
):
    from mediaguard.maintenance import housekeeping

    settings, number = previous_schema(before_evidence, monkeypatch, "156")
    now = db.utcnow()
    with closing(db.Database(settings.db_path)) as database:
        plan = database.execute_insert(
            "INSERT INTO optimization_plans(status,created_at,updated_at,run_job_id) "
            "VALUES ('running',?,?,?)", (now, now, number),
        )
        database.execute("UPDATE jobs SET job_type='optimize.run',target_type='optimization_plan',"
                         "target_id=? WHERE id=?", (str(plan), number))
    app = create_app(settings)
    with closing(app.state.ctx.db) as database, TestClient(app) as client:
        login(client)
        original = proof(client, "job", number)
        assert original.json()["response"]["status"] == "retry_scheduled"
        if scenario == "planpauze":
            paused = client.post(f"/api/v1/optimization/plans/{plan}/pause").raise_for_status()
            assert paused.json()["status"] == "paused"
        else:
            # Op de oude kern werd alleen de operationele rij bijgewerkt.
            database.execute("UPDATE jobs SET status='cancelled',finished_at=? WHERE id=?",
                             (now, number))
        assert not jobs.cancel(database, number)
        current = client.get(f"/api/v1/jobs/{number}").raise_for_status().json()
        assert (current["status"], current["progress"]) == ("cancelled", 0.37)
        end_time = datetime.fromisoformat(current["finished_at"])
        if scenario == "planpauze":
            events = database.fetch_all("SELECT id FROM audit_events WHERE target_type='job' "
                                        "AND target_id=? AND action='job.finished'", (str(number),))
            assert len(events) == 1
            terminal = proof(client, "audit", events[0]["id"])
            assert terminal.json()["response"]["status"] == "cancelled"
            assert datetime.fromisoformat(terminal.json()["finished_at"]) == end_time
        with patch.object(housekeeping, "datetime", wraps=datetime) as clock:
            clock.now.return_value = end_time + timedelta(days=400)
            assert housekeeping.prune(app.state.ctx)["jobs"] > 0

    restarted = create_app(settings)
    with closing(restarted.state.ctx.db) as database, TestClient(restarted) as client:
        login(client)
        assert client.get(f"/api/v1/jobs/{number}").status_code == 404
        assert proof(client, "job", number).content == original.content
        archived = database.fetch_all("SELECT id FROM connector_audit_evidence WHERE job_target=?",
                                      (str(number),))
        outcomes = [proof(client, "audit", row["id"]) for row in archived]
        cancelled = [r for r in outcomes
                     if (r.json()["response"] or {}).get("status") == "cancelled"]
        assert len(cancelled) == 1
        assert datetime.fromisoformat(cancelled[0].json()["finished_at"]) == end_time
        if scenario == "planpauze":
            assert cancelled[0].content == terminal.content
        else:
            assert cancelled[0].json()["app_version"] is None


@pytest.mark.parametrize("state", ["queued", "retry_scheduled", "paused", "running"])
def test_annulering_zonder_bewijsruimte_laat_plan_en_job_intact(client, ctx, monkeypatch, state):
    number = jobs.enqueue(ctx.db, "optimize.run")
    ctx.db.execute("UPDATE jobs SET status=?,progress=0.37 WHERE id=?", (state, number))
    now = db.utcnow()
    plan = ctx.db.execute_insert(
        "INSERT INTO optimization_plans(status,created_at,updated_at,run_job_id) "
        "VALUES ('running',?,?,?)", (now, now, number),
    )

    def deny(*args, **kw):
        raise sqlite3.OperationalError("Geen ruimte voor het eindbewijs")

    with monkeypatch.context() as m:
        m.setattr(jobs, "audit", deny)
        with TestClient(client.app, raise_server_exceptions=False) as reader:
            reader.cookies.update(client.cookies)
            reader.headers.update(client.headers)
            assert reader.post(f"/api/v1/optimization/plans/{plan}/pause").status_code >= 500
    assert tuple(ctx.db.fetch_one("SELECT status,progress,finished_at FROM jobs WHERE id=?",
                                  (number,))) == (state, 0.37, None)
    plan_row = ctx.db.fetch_one("SELECT status FROM optimization_plans WHERE id=?", (plan,))
    assert plan_row["status"] == "running"
    assert client.post(f"/api/v1/optimization/plans/{plan}/pause").is_success


def test_herplannen_bewaart_de_annulering_uit_een_eerdere_kern(client, ctx):
    number = jobs.enqueue(ctx.db, "optimize.run")
    end = db.utcnow()
    ctx.db.execute("UPDATE jobs SET status='cancelled',finished_at=? WHERE id=?", (end, number))
    assert client.post(f"/api/v1/jobs/{number}/retry").json() == {"queued": True}
    events = ctx.db.fetch_all("SELECT id FROM audit_events WHERE action='job.finished' "
                              "AND target_type='job' AND target_id=?", (str(number),))
    assert len(events) == 1
    terminal = proof(client, "audit", events[0]["id"]).json()
    assert datetime.fromisoformat(terminal["finished_at"]) == datetime.fromisoformat(end)
    assert terminal["response"]["status"] == "cancelled" and terminal["app_version"] is None
