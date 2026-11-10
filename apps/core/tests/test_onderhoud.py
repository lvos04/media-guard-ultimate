"""Werkpakket onderhoud: back-ups (3), herstarts (10), logboek (16), terugdraaien na een
update (17), taken en database (31) en 'Nieuw in deze versie' (38)."""

from __future__ import annotations

import errno
import io
import json
import logging
import os
import sqlite3
import sys
import threading
import time
import zipfile
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest

from mediaguard import __version__, aio, jobs, notices
from mediaguard.db import Database, migration_marker, migration_of, stale_migrations
from mediaguard.maintenance import api as maint
from mediaguard.maintenance import housekeeping, preupdate
from mediaguard.maintenance import status as maint_status


def _ago(**kw) -> str:
    return (datetime.now(UTC) - timedelta(**kw)).isoformat(timespec="seconds")


def _notes(ctx, like: str) -> list[dict]:
    return ctx.db.fetch_dicts("SELECT * FROM notifications WHERE title LIKE ? ORDER BY id",
                              (like,))


# ── 3: back-up, herstelbundel en nachtonderhoud los van elkaar ───────────────────────────────
def _night(hour: int = 4, minute: int = 30) -> datetime:
    return datetime.now().astimezone().replace(hour=hour, minute=minute, second=0)


def test_failed_bundle_does_not_repeat_backup_or_block_nightly(ctx, monkeypatch):
    from mediaguard.maintenance import bundle

    bundle.setup(ctx, "t")  # herstelcode ingesteld: bundel hoort erbij

    def broken(_ctx, **_kw):
        raise OSError(errno.ENOSPC, "No space left on device")

    monkeypatch.setattr(bundle, "create", broken)
    now = _night()
    first = maint.run_cycle(ctx, now)
    assert first["backup"]["ok"] and not first["bundle"]["ok"] and first["nightly"]["ok"]
    assert "vol" in first["bundle"]["error"]
    maint.report_cycle(ctx, first)
    note = _notes(ctx, "Herstelbundel mislukt")
    assert len(note) == 1 and note[0]["severity"] == "error" and note[0]["read"] == 0
    # tien minuten later: geen nieuwe back-up, geen nieuw onderhoud, bundel wacht een uur
    again = maint.run_cycle(ctx, now + timedelta(minutes=10))
    assert again == {}
    autos = list(ctx.settings.backup_dir.glob("mediaguard-auto-*.db"))
    assert len([a for a in autos if ".encode-" not in a.name]) == 1
    # na het uur opnieuw; lukt het, dan verdwijnt de melding vanzelf
    monkeypatch.setattr(bundle, "create", lambda _ctx, **_kw: {"file": "x"})
    later = maint.run_cycle(ctx, now + timedelta(minutes=70))
    assert set(later) == {"bundle"} and later["bundle"]["ok"]
    maint.report_cycle(ctx, later)
    assert _notes(ctx, "Herstelbundel mislukt")[0]["resolved_at"]
    assert ctx.state_get("backup.bundle_last_failure") is None


def test_failed_backup_leaves_no_partial_files_and_notifies(ctx, monkeypatch, client):
    def full(_target):
        _target.write_bytes(b"half")
        raise OSError(errno.ENOSPC, "No space left on device")

    monkeypatch.setattr(ctx.db, "backup_to", full)
    res = maint.run_cycle(ctx, _night())
    assert not res["backup"]["ok"] and res["backup"]["error"] == "de appdata-map (/config) is vol"
    assert res["nightly"]["ok"]  # onderhoud loopt gewoon door
    assert not [p for p in ctx.settings.backup_dir.iterdir() if p.is_file()]
    maint.report_cycle(ctx, res)
    assert _notes(ctx, "Back-up mislukt")[0]["severity"] == "error"
    # zichtbaar in 'Wacht op jou' en op de gezondheidspagina
    keys = {i["key"] for i in maint_status.attention_items(ctx)}
    assert "backup.failed" in keys
    health = client.get("/api/v1/admin/health").json()["components"]["backup"]
    assert health["ok"] is False and "mislukt" in health["detail"]
    # handmatig: duidelijke fout (507), geen half bestand
    r = client.post("/api/v1/backups")
    assert r.status_code == 507 and "vol" in r.json()["detail"]
    assert not [p for p in ctx.settings.backup_dir.iterdir() if p.is_file()]


def test_retention_counts_only_verified_backups(ctx):
    ctx.config.set("global", "", "backup.retention_count", 2, "t")
    folder = ctx.settings.backup_dir
    made = []
    for _ in range(3):
        made.append(maint.create_backup(ctx, kind="manual")["file"])
        time.sleep(1.05)  # unieke tijdstempel
    # hernoem naar automatische back-ups (zelfde inhoud en metadata)
    autos = []
    for name in made:
        new = name.replace("-manual-", "-auto-")
        (folder / name).rename(folder / new)
        meta = json.loads((folder / name).with_suffix(".json").read_text())
        meta["file"] = new
        (folder / new).with_suffix(".json").write_text(json.dumps(meta))
        (folder / name).with_suffix(".json").unlink()
        autos.append(folder / new)
    # de twee nieuwste zijn kapot: afgekapt en zonder metadata
    with autos[2].open("r+b") as fh:
        fh.truncate(100)
    autos[1].with_suffix(".json").unlink()
    old = time.time() - 7200
    for p in autos[1:]:
        os.utime(p, (old, old))
    (folder / "mediaguard-auto-20200101T000000Z.db.part").write_bytes(b"x")
    os.utime(folder / "mediaguard-auto-20200101T000000Z.db.part", (old, old))
    removed = maint._prune(ctx)
    # de enige goede back-up blijft; kapotte tellen niet mee en verdwijnen
    assert autos[0].exists() and not autos[1].exists() and not autos[2].exists()
    assert removed == {"old": 0, "broken": 2, "partial": 1}
    assert not maint.verified(autos[2])


def test_bundle_write_failure_leaves_no_tmp(ctx, monkeypatch):
    from mediaguard.maintenance import bundle

    bundle.setup(ctx, "t")
    before = {p.name for p in bundle.bundle_dir(ctx).glob("*.mgbundle")}
    real = Path.write_bytes

    def boom(self, data):
        if self.suffix == ".tmp":
            real(self, data[:10])
            raise OSError(errno.ENOSPC, "No space left on device")
        return real(self, data)

    monkeypatch.setattr(Path, "write_bytes", boom)
    with pytest.raises(OSError):
        bundle.create(ctx)
    assert not list(bundle.bundle_dir(ctx).glob("*.tmp"))
    assert {p.name for p in bundle.bundle_dir(ctx).glob("*.mgbundle")} == before


# ── 31: taken, bewaartermijnen en zware controles ────────────────────────────────────────────
def test_restart_only_resets_jobs_of_own_role(ctx):
    db = ctx.db
    rec = jobs.enqueue(db, "recovery.activate", {})
    core = jobs.enqueue(db, "library.sync", {})
    db.execute("UPDATE jobs SET status='running'")
    assert jobs.JobRunner(ctx).recover_after_restart() == 1  # rol core
    rows = {r["id"]: r["status"] for r in db.fetch_all("SELECT id, status FROM jobs")}
    assert rows[core] == "queued" and rows[rec] == "running"
    from dataclasses import replace

    other = replace(ctx.settings, role="recovery")
    runner = jobs.JobRunner(ctx)
    runner.app = type("A", (), {"settings": other, "db": db})()
    assert runner.recover_after_restart() == 1
    assert db.fetch_one("SELECT status FROM jobs WHERE id=?", (rec,))["status"] == "queued"


def test_prune_covers_unbounded_tables_in_small_batches(ctx, monkeypatch):
    monkeypatch.setattr(housekeeping, "BATCH_ROWS", 3)
    db = ctx.db
    old, new = _ago(days=400), _ago(days=1)
    for when in [old] * 7 + [new]:
        db.execute("INSERT INTO audit_events(ts,actor,action) VALUES (?,?,?)", (when, "t", "x"))
        db.execute("INSERT INTO login_history(username,ts,success) VALUES ('a',?,1)", (when,))
        db.execute("INSERT INTO import_events(instance_id,event_type,idempotency_key,"
                   "payload_json,received_at) VALUES ('s','Download',?,'{}',?)",
                   (f"k{when}{time.monotonic_ns()}", when))
    out = housekeeping.prune(ctx)
    assert out["audit"] == 7 and out["login_history"] == 7 and out["import_events"] == 7
    assert db.fetch_one("SELECT COUNT(*) AS n FROM audit_events")["n"] == 1
    assert all(v >= 0 for v in out.values())  # geen tabel mislukt


def test_delivery_timeline_keeps_latest_event_per_item(ctx):
    db = ctx.db
    old = _ago(days=400)
    item = db.execute_insert(
        "INSERT INTO delivery_items(item_key,source,media_type,created_at,updated_at) "
        "VALUES ('movie:tmdb:1','seerr','movie',?,?)", (old, old))
    for i in range(3):
        db.execute("INSERT INTO delivery_events(item_id,ts,stage,action,actor,detail_nl) "
                   "VALUES (?,?,?,?,?,?)", (item, _ago(days=400 - i), "s", f"a{i}", "t", "x"))
    housekeeping.prune(ctx)
    left = db.fetch_all("SELECT action FROM delivery_events WHERE item_id=?", (item,))
    assert [r["action"] for r in left] == ["a2"]  # de laatste stand blijft


def test_backup_does_not_hold_the_write_lock(ctx, tmp_path):
    held = threading.Event()
    release = threading.Event()

    def hold() -> None:
        with ctx.db._lock:
            held.set()
            release.wait(10)

    t = threading.Thread(target=hold)
    t.start()
    held.wait(5)
    started = time.monotonic()
    try:
        ctx.db.backup_to(tmp_path / "kopie.db")  # mag niet op het slot wachten
    finally:
        release.set()
        t.join()
    assert time.monotonic() - started < 5
    assert sqlite3.connect(tmp_path / "kopie.db").execute(
        "SELECT COUNT(*) FROM schema_migrations").fetchone()[0] > 0


def test_health_database_check_runs_off_loop_and_is_cached(client, ctx, monkeypatch):
    calls = []

    def check(_ctx, pragma):
        calls.append((pragma, threading.current_thread() is threading.main_thread()))
        return True

    monkeypatch.setattr(maint, "_pragma_check", check)
    ctx.services.pop("db_quick_check", None)
    for _ in range(3):
        assert client.get("/api/v1/admin/health").json()["components"]["database"]["ok"]
    assert len(calls) == 1 and calls[0] == ("quick_check", False)


def test_vacuum_only_with_much_free_space(ctx, monkeypatch):
    seen = {}
    monkeypatch.setattr(ctx.db, "maintenance", lambda vacuum=False: seen.update(v=vacuum))
    monkeypatch.setattr(ctx.db, "free_ratio", lambda: 0.05)

    class Sunday(datetime):
        @classmethod
        def now(cls, tz=None):  # noqa: D102
            return datetime(2026, 11, 8, 4, 30, tzinfo=tz)

    monkeypatch.setattr(housekeeping, "datetime", Sunday)
    housekeeping.run_nightly(ctx)
    assert seen["v"] is False
    monkeypatch.setattr(ctx.db, "free_ratio", lambda: 0.5)
    housekeeping.run_nightly(ctx)
    assert seen["v"] is True


def test_concurrent_migration_is_skipped_not_repeated(tmp_path):
    db = Database(tmp_path / "x.db")
    db.initialize()
    from mediaguard.db import MIGRATIONS_DIR

    first = sorted(MIGRATIONS_DIR.glob("*.sql"))[0]
    # het andere proces paste hem al toe: geen 'table already exists'
    db._apply_migration(db._connect(), first)
    assert not list(tmp_path.glob("migratie-bezig-*.json"))


# ── 10: herstarts, migratie niet afbreken, uitleg van een herstartlus ────────────────────────
def test_migration_marker_written_and_removed(tmp_path, monkeypatch):
    seen = {}
    real = Database._apply_migration

    def spy(self, conn, file):
        seen.setdefault("marker", migration_of(tmp_path, os.getpid()))
        return real(self, conn, file)

    monkeypatch.setattr(Database, "_apply_migration", spy)
    Database(tmp_path / "mediaguard.db").initialize()
    assert seen["marker"]["pid"] == os.getpid() and seen["marker"]["to"]
    assert migration_of(tmp_path, os.getpid()) is None
    # een marker van een gestopt proces = afgebroken migratie (voor de uitleg)
    migration_marker(tmp_path, 999999).write_text(json.dumps({"pid": 999999, "to": "x"}))
    assert stale_migrations(tmp_path)[0]["to"] == "x"
    Database(tmp_path / "mediaguard.db").initialize()  # ruimt hem op bij de volgende start
    assert not stale_migrations(tmp_path)


def test_supervisor_never_restarts_core_while_migrating(tmp_path):
    core = aio.Spec("core", [sys.executable, "-c", "import time; time.sleep(60)"],
                    {"PATH": os.environ["PATH"]}, health="http://127.0.0.1:9/health",
                    grace=0, migration_guard=True)
    state = tmp_path / "core" / "supervisor.json"
    state.parent.mkdir()
    sup = aio.Supervisor([core], None, None, state_path=state, max_fails=1,
                         config_dir=tmp_path)
    sup.start(core)
    try:
        migration_marker(state.parent, core.proc.pid).write_text(json.dumps(
            {"pid": core.proc.pid, "from": "065", "to": "120", "since": "2026-11-08T03:00:00"}))
        assert sup.check_health() == [] and sup.check_health() == []
        assert core.proc.poll() is None
        assert sup.snapshot()["services"]["core"]["state"] == "migrating"
        migration_marker(state.parent, core.proc.pid).unlink()
        assert sup.check_health() == ["core"]  # na de migratie gewoon weer bewaakt
    finally:
        sup.stop(budget=2)


def test_web_gets_only_a_limited_core_status_outside_config(tmp_path):
    """De web-server draait als eigen gebruiker en kan niet in /config/core (item 33). Voor
    de uitleg bij 'core onbereikbaar' (item 10) schrijft de supervisor een kopie met alleen
    de staat, de migratie en de uitleg van een herstartlus, leesbaar voor iedereen."""
    import stat

    spec = aio.Spec("core", ["x"], {}, migration_guard=True)
    web_state = tmp_path / "run" / "core-status.json"
    sup = aio.Supervisor([spec], None, None, state_path=tmp_path / "core" / "supervisor.json",
                         config_dir=tmp_path, web_state_path=web_state)
    (tmp_path / "core").mkdir()
    spec.last_exit = 1
    spec.recent.append("sqlite3.OperationalError: database or disk is full")
    for _ in range(3):
        sup.note_exit(spec)
    sup.write_state()
    public = json.loads(web_state.read_text())
    assert set(public["services"]) == {"core"}
    assert set(public["services"]["core"]) == {"state", "migrating", "loop_nl"}
    assert "/config is vol" in public["services"]["core"]["loop_nl"]
    assert stat.S_IMODE(web_state.stat().st_mode) == 0o644
    full = json.loads((tmp_path / "core" / "supervisor.json").read_text())
    assert "last_exit" in full["services"]["core"]  # het volledige bestand blijft in /config


def test_restart_loop_is_explained(tmp_path):
    spec = aio.Spec("core", ["x"], {}, migration_guard=True)
    sup = aio.Supervisor([spec], None, None, state_path=tmp_path / "core" / "supervisor.json",
                         config_dir=tmp_path, log_path=tmp_path / "supervisor.log")
    (tmp_path / "core").mkdir()
    spec.last_exit = 1
    spec.recent.extend(["Traceback ...", "sqlite3.OperationalError: database or disk is full"])
    for _ in range(3):
        sup.note_exit(spec)
    assert "/config is vol" in spec.loop_nl
    snap = sup.snapshot()["services"]["core"]
    assert snap["restarts_24h"] == 3 and snap["total_restarts"] == 3 and snap["loop_nl"]
    assert "herstart steeds opnieuw" in (tmp_path / "supervisor.log").read_text()
    # afgebroken migratie
    spec.recent.clear()
    spec.recent.append("Killed")
    migration_marker(tmp_path / "core", 999998).write_text(
        json.dumps({"pid": 999998, "from": "065_performance", "to": "120_x"}))
    text = aio.explain_restarts(spec, tmp_path, tmp_path / "core", free_bytes=10 * 1024**3)
    assert "database na de update" in text and "065_performance → 120_x" in text
    # onbekend: laatste regel, zonder geheimen
    other = aio.Spec("encode", ["x"], {})
    other.last_exit = 3
    other.recent.append("fout bij start api_key=geheim123")
    text = aio.explain_restarts(other, tmp_path, tmp_path / "core", free_bytes=10 * 1024**3)
    assert "code 3" in text and "geheim123" not in text


def test_core_shows_restart_counters_and_warns_once(client, ctx):
    state = {"updated_at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()), "healthy": True,
             "services": {
                 "encode": {"state": "running", "restarts_24h": 4, "total_restarts": 9,
                            "loop_nl": "De appdata-map /config is vol (nog 12 MB vrij)."},
                 "core": {"state": "running", "restarts_24h": 1, "total_restarts": 1}}}
    (ctx.settings.data_dir / "supervisor.json").write_text(json.dumps(state))
    comp = client.get("/api/v1/admin/health").json()["components"]["herstarts"]
    assert comp["ok"] is False and "AV1-engine: 4× herstart" in comp["detail"]
    assert comp["restarts_24h"]["core"] == 1
    items = {i["key"]: i for i in client.get("/api/v1/attention").json()["items"]}
    assert "/config is vol" in items["supervisor.restarts.encode"]["detail_nl"]
    maint_status.check(ctx)
    maint_status.check(ctx)
    assert len(_notes(ctx, "AV1-engine herstart steeds opnieuw")) == 1
    state["services"]["encode"].update(restarts_24h=0, loop_nl="")
    (ctx.settings.data_dir / "supervisor.json").write_text(json.dumps(state))
    maint_status.check(ctx)
    assert _notes(ctx, "AV1-engine herstart steeds opnieuw")[0]["resolved_at"]


# ── 16: echte logs ───────────────────────────────────────────────────────────────────────────
@pytest.fixture
def clean_root_logger():
    root = logging.getLogger()
    before = (list(root.handlers), root.level, getattr(root, "_mg_logs", None))
    yield root
    for h in root.handlers:
        if h not in before[0]:
            h.close()
    root.handlers[:] = before[0]
    root.setLevel(before[1])
    if before[2] is None and hasattr(root, "_mg_logs"):
        delattr(root, "_mg_logs")


def test_info_lines_are_kept_and_level_setting_works(ctx, client, tmp_path, monkeypatch,
                                                      clean_root_logger):
    from mediaguard import logs

    folder = tmp_path / "logs"
    monkeypatch.setenv("MG_LOG_DIR", str(folder))
    path = logs.configure("core", folder, "info")
    logging.getLogger("mediaguard.test").info("dit is een info-regel")
    assert "dit is een info-regel" in path.read_text()
    # instelling 'Logniveau' → dit proces én het niveaubestand voor de AV1-engine
    ctx.config.set("global", "", "logging.level", "warning", "t")
    assert logs.sync(ctx) == "warning"
    assert clean_root_logger.level == logging.WARNING
    assert (folder / "niveau").read_text().strip() == "warning"
    logging.getLogger("mediaguard.test").info("deze niet")
    assert "deze niet" not in path.read_text()
    # supportbundel bevat de logs (geredigeerd) en de supervisorstatus
    ctx.secrets.set("ai:openai:apikey", "sk-proj-lekniet123456")
    (folder / "encoder.log").write_text("encoder start\napi_key sk-proj-lekniet123456\n")
    zf = zipfile.ZipFile(io.BytesIO(client.get("/api/v1/admin/support-bundle").content))
    names = zf.namelist()
    assert "logs/core.log" in names and "logs/encoder.log" in names
    assert "supervisor.json" in names and "maintenance.json" in names
    assert b"lekniet" not in b"".join(zf.read(n) for n in names)


# ── 17: back-ups van vóór een update ─────────────────────────────────────────────────────────
def _encoder_db(path: Path, versions: list[int]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(path)
    conn.execute("CREATE TABLE IF NOT EXISTS schema_versie (versie INTEGER PRIMARY KEY, "
                 "naam TEXT, toegepast_op TEXT)")
    for v in versions:
        conn.execute("INSERT OR IGNORE INTO schema_versie VALUES (?,?,?)", (v, f"{v}", "x"))
    conn.commit()
    conn.close()


def test_supervisor_makes_set_with_encoder_db_only_when_migration_pending(tmp_path):
    config = tmp_path / "config"
    core_db = config / "core" / "mediaguard.db"
    Database(core_db).initialize()
    enc_mig = tmp_path / "enc-migrations"
    enc_mig.mkdir()
    for n in (1, 2):
        (enc_mig / f"{n:03d}_x.sql").write_text("")
    _encoder_db(config / "encode" / "app.db", [1, 2])
    from mediaguard.db import MIGRATIONS_DIR

    assert preupdate.snapshot_before_start(config, core_migrations=MIGRATIONS_DIR,
                                           encoder_migrations=enc_mig, app_version="9.9.9") is None
    (enc_mig / "003_y.sql").write_text("")  # nieuwe encoder-migratie in de nieuwe versie
    made = preupdate.snapshot_before_start(config, core_migrations=MIGRATIONS_DIR,
                                           encoder_migrations=enc_mig, app_version="9.9.9")
    meta = json.loads((made / "meta.json").read_text())
    assert meta["encoder_included"] and meta["to_version"] == "9.9.9"
    assert {f["name"] for f in meta["files"]} == {"mediaguard.db", "encode/app.db"}
    assert not preupdate.verify(made)


def test_pre_update_backup_listed_staged_and_swapped(tmp_path, monkeypatch):
    from fastapi.testclient import TestClient

    from mediaguard.app import create_app
    from mediaguard.config import load_settings

    config = tmp_path / "config"
    core_dir = config / "core"
    monkeypatch.setenv("MG_AIO_SUPERVISOR", "1")  # de AIO zet dit voor core
    settings = load_settings(data_dir=core_dir, staging_dir=tmp_path / "staging",
                             quarantine_dir=tmp_path / "q", start_background=False)
    app = create_app(settings)
    ctx = app.state.ctx
    ctx.state_set("app.version", {"version": "1.2.0"})
    ctx.config.set("global", "", "ui.theme", "light", "t")
    _encoder_db(config / "encode" / "app.db", [1, 2])
    made = preupdate.make_set(core_dir / "backups" / preupdate.FOLDER, settings.db_path,
                              encode_dir=config / "encode", from_version="1.2.0",
                              to_version="1.2.1")
    ctx.config.set("global", "", "ui.theme", "dark", "t")  # 'na de update'
    _encoder_db(config / "encode" / "app.db", [1, 2, 3])
    try:
        with TestClient(app) as client:
            r = client.post("/api/v1/auth/setup",
                            json={"username": "admin", "password": "geheim-wachtwoord"})
            client.headers["X-CSRF-Token"] = r.json()["csrf_token"]
            rows = [b for b in client.get("/api/v1/backups").json() if b["kind"] == "pre-update"]
            assert rows[0]["name"] == made.name and rows[0]["restore_mode"] == "bij-stoppen"
            assert "Vóór update naar 1.2.1" in rows[0]["label_nl"]
            assert client.get(f"/api/v1/backups/{made.name}/download").status_code == 200
            bad = client.post(f"/api/v1/backups/{made.name}/restore", json={"confirm": "x"})
            assert bad.status_code == 422
            r = client.post(f"/api/v1/backups/{made.name}/restore", json={"confirm": made.name})
            assert r.status_code == 200 and r.json()["staged"] == made.name
            assert "versie 1.2.0" in r.json()["message_nl"]
            # live database niet aangeraakt; kopieën staan klaar; zichtbaar in 'Wacht op jou'
            assert ctx.config.value("ui.theme") == "dark"
            assert (core_dir / "mediaguard.db.terugzetten").is_file()
            assert (config / "encode" / "app.db.terugzetten").is_file()
            keys = {i["key"] for i in client.get("/api/v1/attention").json()["items"]}
            assert "backup.rollback_pending" in keys
            assert client.get("/api/v1/backups/terugzetten").json()["pending"]["set"] == made.name
    finally:
        ctx.db.close()
    # de supervisor wisselt om zodra alles gestopt is (alleen hernoemen)
    (core_dir / "mediaguard.db-wal").write_bytes(b"")
    result = preupdate.apply_staged(core_dir)
    assert result["ok"] and set(result["files"]) == {"core/mediaguard.db", "encode/app.db"}
    assert not (core_dir / "mediaguard.db-wal").exists()  # wal van de oude db mee opzij
    kept = Path(result["previous"])
    assert (kept / "core__mediaguard.db").is_file() and (kept / "core__mediaguard.db-wal").exists()
    conn = sqlite3.connect(core_dir / "mediaguard.db")
    theme = conn.execute("SELECT value_json FROM settings_values WHERE key='ui.theme'").fetchone()
    conn.close()
    assert json.loads(theme[0]) == "light"
    enc = sqlite3.connect(config / "encode" / "app.db")
    assert [r[0] for r in enc.execute("SELECT versie FROM schema_versie ORDER BY 1")] == [1, 2]
    enc.close()
    assert preupdate.pending(core_dir) is None
    assert preupdate.take_result(core_dir)["ok"] and preupdate.take_result(core_dir) is None


def test_staging_refuses_damaged_set_and_cancel_cleans_up(tmp_path):
    core_dir = tmp_path / "config" / "core"
    Database(core_dir / "mediaguard.db").initialize()
    made = preupdate.make_set(core_dir / "backups" / preupdate.FOLDER,
                              core_dir / "mediaguard.db", encode_dir=None)
    with (made / "mediaguard.db").open("r+b") as fh:
        fh.seek(200)
        fh.write(b"kapot")
    with pytest.raises(preupdate.SetError, match="beschadigd"):
        preupdate.stage(core_dir, made, actor="t")
    assert preupdate.pending(core_dir) is None
    good = preupdate.make_set(core_dir / "backups" / preupdate.FOLDER,
                              core_dir / "mediaguard.db", encode_dir=None)
    preupdate.stage(core_dir, good, actor="t")
    assert (core_dir / "mediaguard.db.terugzetten").exists()
    assert preupdate.cancel(core_dir) and not (core_dir / "mediaguard.db.terugzetten").exists()
    assert preupdate.apply_staged(core_dir) is None  # niets klaargezet: niets veranderd


def test_set_retention_and_partial_sets(tmp_path):
    folder = tmp_path / "sets"
    db = tmp_path / "core" / "mediaguard.db"
    Database(db).initialize()
    for _ in range(preupdate.KEEP_SETS + 2):
        preupdate.make_set(folder, db, encode_dir=None)
    part = folder / "voor-update-20200101T000000Z.part"
    part.mkdir()
    old = time.time() - 7200
    os.utime(part, (old, old))
    preupdate.prune_sets(folder)
    assert len([p for p in folder.iterdir() if p.is_dir()]) == preupdate.KEEP_SETS


def test_core_skips_own_copy_when_supervisor_made_the_set(tmp_path, monkeypatch):
    made = tmp_path / "set"
    made.mkdir()
    (made / "meta.json").write_text("{}")
    monkeypatch.setenv("MG_PRE_UPDATE_SET", str(made))
    db = Database(tmp_path / "core" / "mediaguard.db")
    db.initialize()
    db._backup_before_migration("001", "002")
    assert not (tmp_path / "core" / "backups").exists()


def test_before_services_sets_env_for_core(tmp_path, monkeypatch):
    config = tmp_path / "config"
    Database(config / "core" / "mediaguard.db").initialize()
    calls = {}

    def fake(cfg, **kw):
        calls.update(kw)
        out = cfg / "core" / "backups" / "voor-update" / "voor-update-20261108T040000Z"
        out.mkdir(parents=True)
        return out

    monkeypatch.setattr(preupdate, "snapshot_before_start", fake)
    env = {"MG_AIO_CONFIG": str(config), "MG_AIO_ENCODER_DIR": str(tmp_path / "enc")}
    lines = aio.before_services(env, config, None)
    assert env["MG_PRE_UPDATE_SET"].endswith("voor-update-20261108T040000Z")
    assert any("gemaakt" in line for line in lines)
    specs = {s.name: s for s in aio.build_specs(env)}
    assert specs["core"].env["MG_PRE_UPDATE_SET"] == env["MG_PRE_UPDATE_SET"]
    assert specs["core"].env["MG_LOG_DIR"] == str(config / "logs")
    assert specs["encode"].env["MG_LOG_DIR"] == str(config / "logs")
    assert specs["core"].migration_guard and specs["recovery"].migration_guard
    assert specs["core"].env["MG_AIO_SUPERVISOR"] == "1"
    # de web-server (eigen gebruiker) kan niet in /config/core: een beperkte kopie erbuiten
    assert specs["web"].env["MG_SUPERVISOR_STATE"] == aio.WEB_STATE
    assert not specs["web"].env["MG_SUPERVISOR_STATE"].startswith(str(config))
    assert calls["app_version"] == __version__


# ── 38: 'Nieuw in deze versie' ───────────────────────────────────────────────────────────────
def _tuning(**overrides) -> dict:
    base = {"max_cpu_uren": {"sleutel": "max_cpu_uren", "waarde": 12.0, "standaard": 12.0},
            "anime_route": {"sleutel": "anime_route", "waarde": "auto", "standaard": "auto"}}
    for k, v in overrides.items():
        base[k] = {**base[k], "waarde": v}
    return base


def test_fresh_install_gets_no_changed_default_notices(ctx):
    assert notices.run(ctx) == []
    assert notices.version_info(ctx)["fresh_install"] is True
    sent = ctx.state_get(notices.STATE_KEY)
    assert sent["1.1.0:quarantine.purge_target_percent"] == "n.v.t."
    assert notices.attention_items(ctx) == []


def test_upgrade_shows_whats_new_notice_and_notification(client, ctx):
    # (de testclient startte de app al als 'nieuwe installatie'; simuleer een update)
    ctx.state_set(notices.STATE_KEY, {})
    ctx.state_set(notices.VERSION_KEY, {"version": "1.0.0"})
    ctx.services[notices.TUNING_KEY] = _tuning()
    new = notices.run(ctx)
    assert set(new) == {"1.1.0:quarantine.purge_target_percent", "1.2.0:encoder.max_cpu_uren",
                        "1.2.0:encoder.anime_route"}
    assert notices.version_info(ctx)["previous"] == "1.0.0"
    note = _notes(ctx, "Nieuw in deze versie%")
    assert len(note) == 1 and "opruimen tot 85 %" in note[0]["body"]
    assert notices.run(ctx) == []  # hooguit één keer
    items = {i["key"]: i for i in client.get("/api/v1/attention").json()["items"]}
    card = items["notices.whats_new"]
    assert card["severity"] == "info" and len(card["rows"]) == 3
    assert any("12 uur" in r["detail"] for r in card["rows"])
    assert client.get("/api/v1/notices").json()["show"] is True
    assert client.post("/api/v1/notices/gezien").status_code == 200
    keys = {i["key"] for i in client.get("/api/v1/attention").json()["items"]}
    assert "notices.whats_new" not in keys


def test_no_notice_when_user_changed_the_setting_or_came_from_newer(ctx):
    ctx.state_set(notices.VERSION_KEY, {"version": "1.1.5"})
    ctx.config.set("global", "", "quarantine.purge_target_percent", 95, "t")
    ctx.services[notices.TUNING_KEY] = _tuning(anime_route="cpu")
    assert notices.run(ctx) == ["1.2.0:encoder.max_cpu_uren"]
    note = _notes(ctx, "Nieuwe standaard: CPU-encode%")
    assert len(note) == 1 and "12 uur" in note[0]["body"]


def test_encoder_notice_waits_for_engine_then_after_a_day(ctx):
    ctx.state_set(notices.VERSION_KEY, {"version": "1.1.9"})
    assert notices.run(ctx) == []  # engine onbereikbaar: nog niet beslissen
    sent = ctx.state_get(notices.STATE_KEY) or {}
    assert "1.2.0:encoder.anime_route" not in sent
    info = ctx.state_get(notices.VERSION_KEY)
    info["first_seen"] = _ago(days=2)
    ctx.state_set(notices.VERSION_KEY, info)
    assert set(notices.run(ctx)) == {"1.2.0:encoder.max_cpu_uren", "1.2.0:encoder.anime_route"}


def test_version_compare():
    assert notices.older("1.0.9", "1.1.0") and notices.older(None, "1.1.0")
    assert not notices.older("1.2.0", "1.2.0") and not notices.older("1.10.0", "1.2.0")


def test_job_detail_caps_log_lines(client, ctx, monkeypatch):
    from mediaguard.api import system

    monkeypatch.setattr(system, "JOB_LOG_LIMIT", 5)
    jid = jobs.enqueue(ctx.db, "library.sync", {})
    for i in range(8):
        ctx.db.execute("INSERT INTO job_logs(job_id,ts,level,message) VALUES (?,?,?,?)",
                       (jid, _ago(seconds=10 - i), "info", f"regel {i}"))
    job = client.get(f"/api/v1/jobs/{jid}").json()
    assert job["logs_truncated"] is True
    assert [r["message"] for r in job["logs"]] == [f"regel {i}" for i in range(3, 8)]


def test_watch_round_never_raises(ctx, tmp_path, monkeypatch):
    import asyncio

    monkeypatch.setattr(maint_status, "check", lambda _ctx: 1 / 0)
    asyncio.run(maint._watch_round(ctx))  # fout wordt gelogd, lus gaat door
    assert aio.apply_staged_restore(tmp_path, None) == []  # niets klaargezet


def test_staged_restore_expires_after_a_day(tmp_path):
    core_dir = tmp_path / "config" / "core"
    Database(core_dir / "mediaguard.db").initialize()
    made = preupdate.make_set(core_dir / "backups" / preupdate.FOLDER,
                              core_dir / "mediaguard.db", encode_dir=None)
    info = preupdate.stage(core_dir, made, actor="t")
    info["requested_at"] = _ago(days=2)
    (core_dir / preupdate.STAGE_MARKER).write_text(json.dumps(info))
    before = (core_dir / "mediaguard.db").stat().st_ino
    result = preupdate.apply_staged(core_dir)
    assert result["ok"] is False and "verlopen" in result["error"]
    assert (core_dir / "mediaguard.db").stat().st_ino == before  # niets omgewisseld
    assert not (core_dir / "mediaguard.db.terugzetten").exists()


# ── controle (checker): randgevallen ─────────────────────────────────────────────────────────
def _as_auto(ctx, stamps: list[str]) -> list[Path]:
    """Gecontroleerde back-ups onder automatische namen met de gegeven tijdstempels."""
    folder = ctx.settings.backup_dir
    out = []
    for stamp in stamps:
        meta = maint.create_backup(ctx, kind="manual")
        src = folder / meta["file"]
        dest = folder / f"mediaguard-auto-{stamp}.db"
        src.rename(dest)
        meta["file"] = dest.name
        dest.with_suffix(".json").write_text(json.dumps(meta))
        src.with_suffix(".json").unlink()
        out.append(dest)
    return out


def test_backup_loop_on_one_day_never_pushes_out_older_days(ctx):
    """Rapport 3: liep de back-up ooit elke 10 minuten, dan bleven de oudere dagen staan."""
    ctx.config.set("global", "", "backup.retention_count", 3, "t")
    older = _as_auto(ctx, ["20261101T021500Z", "20261102T021500Z"])
    loop = _as_auto(ctx, [f"20261108T02{m:02d}00Z" for m in (0, 10, 20, 30, 40)])
    removed = maint._prune(ctx)
    assert all(p.exists() for p in older)  # twee oudere dagen blijven
    assert loop[-1].exists() and not any(p.exists() for p in loop[:-1])
    assert removed["same_day"] == 4 and removed["old"] == 0
    # bewaartermijn telt dagen: nog een dag erbij → de oudste dag gaat weg
    _as_auto(ctx, ["20261109T021500Z"])
    maint._prune(ctx)
    assert not older[0].exists() and older[1].exists()


def test_failing_prune_does_not_fail_a_good_backup(ctx, monkeypatch):
    def boom(_ctx, now=None):
        raise OSError(errno.EACCES, "geen rechten")

    monkeypatch.setattr(maint, "_prune", boom)
    meta = maint.create_backup(ctx, kind="auto")
    assert (ctx.settings.backup_dir / meta["file"]).is_file()


def test_own_pid_marker_from_earlier_process_is_removed(tmp_path):
    """In een container krijgt core vaak steeds hetzelfde pid: een oude markering met dat pid
    mag de supervisor niet laten denken dat core eeuwig migreert."""
    db = Database(tmp_path / "mediaguard.db")
    db.initialize()
    migration_marker(tmp_path, os.getpid()).write_text(json.dumps(
        {"pid": os.getpid(), "to": "999_oud"}))
    Database(tmp_path / "mediaguard.db").initialize()  # niets openstaand
    assert migration_of(tmp_path, os.getpid()) is None


def test_supervisor_ignores_marker_older_than_the_process(tmp_path):
    core = aio.Spec("core", [sys.executable, "-c", "import time; time.sleep(60)"],
                    {"PATH": os.environ["PATH"]}, health="http://127.0.0.1:9/health",
                    grace=0, migration_guard=True)
    state = tmp_path / "core" / "supervisor.json"
    state.parent.mkdir()
    sup = aio.Supervisor([core], None, None, state_path=state, max_fails=1,
                         config_dir=tmp_path)
    sup.start(core)
    try:
        marker = migration_marker(state.parent, core.proc.pid)
        marker.write_text(json.dumps({"pid": core.proc.pid, "to": "120"}))
        old = time.time() - 3600  # van de vorige containerstart
        os.utime(marker, (old, old))
        assert sup.check_health() == ["core"]  # gewoon bewaakt
    finally:
        sup.stop(budget=2)


def test_failed_migration_is_recorded_and_explained(tmp_path, monkeypatch):
    from mediaguard.db import MIGRATION_FAILED_FILE, failed_migration

    core_dir = tmp_path / "core"
    real = Database._apply_migration

    def broken(self, conn, file):
        raise sqlite3.OperationalError("no such column: oud_veld")

    monkeypatch.setattr(Database, "_apply_migration", broken)
    with pytest.raises(sqlite3.OperationalError):
        Database(core_dir / "mediaguard.db").initialize()
    info = failed_migration(core_dir)
    assert info and "no such column" in info["error"] and info["to"]
    assert not list(core_dir.glob("migratie-bezig-*.json"))
    spec = aio.Spec("core", ["x"], {}, migration_guard=True)
    spec.last_exit = 1
    spec.recent.append("sqlite3.OperationalError: no such column: oud_veld")
    text = aio.explain_restarts(spec, tmp_path, core_dir, free_bytes=10 * 1024**3)
    assert "mislukt steeds" in text and "no such column" in text and "Terugdraaien" in text
    # lukt het daarna wel, dan verdwijnt de uitleg
    monkeypatch.setattr(Database, "_apply_migration", real)
    Database(core_dir / "mediaguard.db").initialize()
    assert not (core_dir / MIGRATION_FAILED_FILE).exists()


def test_stale_supervisor_file_gives_no_warnings(client, ctx):
    old = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime(time.time() - 3600))
    (ctx.settings.data_dir / "supervisor.json").write_text(json.dumps({
        "updated_at": old, "services": {"encode": {"restarts_24h": 9, "loop_nl": "x"}}}))
    assert maint_status.supervisor_state(ctx)["warnings"] == []
    assert "herstarts" not in client.get("/api/v1/admin/health").json()["components"]
    keys = {i["key"] for i in client.get("/api/v1/attention").json()["items"]}
    assert "supervisor.restarts.encode" not in keys


def test_diagnose_button_works_for_new_components(client, ctx):
    assert client.post("/api/v1/admin/diagnostics/herstarts").status_code == 200
    r = client.post("/api/v1/admin/diagnostics/backup")
    assert r.status_code == 200 and r.json()["ok"] is False  # nog geen back-up


def test_pre_update_restore_only_under_supervisor(client, ctx, monkeypatch):
    """Zonder supervisor (losse containers) wisselt niemand de databases om: niet klaarzetten,
    wel zichtbaar en downloadbaar."""
    monkeypatch.delenv("MG_AIO_SUPERVISOR", raising=False)
    made = preupdate.make_set(ctx.settings.backup_dir / preupdate.FOLDER, ctx.db.path,
                              encode_dir=None, to_version="9.9.9")
    rows = [b for b in client.get("/api/v1/backups").json() if b["kind"] == "pre-update"]
    assert rows[0]["restore_mode"] == "handmatig" and "DATA_RECOVERY" in rows[0]["restore_nl"]
    r = client.post(f"/api/v1/backups/{made.name}/restore", json={"confirm": made.name})
    assert r.status_code == 409 and "alles-in-één" in r.json()["detail"]
    assert preupdate.pending(ctx.settings.data_dir) is None
    assert client.get(f"/api/v1/backups/{made.name}/download").status_code == 200
