from __future__ import annotations

import json
import os
import shutil
import sqlite3
import time
from dataclasses import replace
from pathlib import Path

import pytest
from conftest import TESTDATA

from app import scanner as scanner_module
from app.config import settings
from app.db import Database
from app.media.beslissen import STANDAARD
from app.media.probe import probe as media_probe
from app.scanner import scan_library


def _database(tmp_path: Path) -> Database:
    return _database_for_path(tmp_path, TESTDATA / "mappen")


def _database_for_path(tmp_path: Path, library_path: Path) -> Database:
    tmp_path.mkdir(parents=True, exist_ok=True)
    roots = {name: tmp_path / name for name in ("cache", "work", "config", "backups")}
    for path in roots.values():
        path.mkdir()
    configured = replace(
        settings,
        pipeline_root=tmp_path,
        cache_root=roots["cache"],
        work_root=roots["work"],
        config_root=roots["config"],
        backup_root=roots["backups"],
        database_path=roots["config"] / "app.db",
    )
    database = Database(configured)
    database.initialize()
    profile_id = database.execute_insert(
        "INSERT INTO profiel(naam,inst) VALUES (?,?)",
        ("Test", json.dumps({**STANDAARD, "drempel_1080": 500})),
    )
    database.execute_insert(
        "INSERT INTO library(naam,pad,profiel_id,verwacht_device) VALUES (?,?,?,?)",
        ("Test", str(library_path), profile_id, library_path.stat().st_dev),
    )
    return database


def test_dry_run_schrijft_niets_en_filtert_veilig(tmp_path: Path) -> None:
    database = _database(tmp_path)
    summary = scan_library(database, 1, dry_run=True)
    by_name = {Path(item.pad).name: item for item in summary.items}

    assert by_name["Trailer.Park.Boys.S01E01.1080p.mkv"].categorie == "ok"
    assert by_name["Short.Term.12.2013.1080p.BluRay.x264.mkv"].categorie == "ok"
    assert by_name["Wadi Deel Twee (2024)-trailer.mkv"].categorie == "extra"
    assert by_name["Wadi Deel Twee (2024)-trailer.mkv"].actie == "opruimen"
    assert by_name["Making Of.mkv"].categorie == "extra"
    assert database.fetch_one("SELECT COUNT(*) FROM bestand")[0] == 0


def test_twee_scans_geven_geen_dubbele_rijen(tmp_path: Path) -> None:
    database = _database(tmp_path)
    first = scan_library(database, 1)
    second = scan_library(database, 1)

    assert first.nieuw == 6
    assert second.nieuw == 0
    assert database.fetch_one("SELECT COUNT(*) FROM bestand")[0] == 6
    assert database.fetch_one("SELECT COUNT(*) FROM besluit")[0] == 6


def test_rescan_na_planning_maakt_geen_tweede_besluit_voor_ongewijzigd_bestand(
    tmp_path: Path,
) -> None:
    database = _database(tmp_path)
    scan_library(database, 1)
    database.execute("UPDATE besluit SET status='ingepland'")

    scan_library(database, 1)

    assert database.fetch_one("SELECT COUNT(*) FROM besluit")[0] == 6
    assert database.fetch_one(
        "SELECT COUNT(*) FROM besluit WHERE status='nieuw'"
    )[0] == 0


def test_afgebroken_scan_wordt_hervat(tmp_path: Path) -> None:
    database = _database(tmp_path)
    partial = scan_library(database, 1, stop_after_directories=2)
    resumed = scan_library(database, 1)

    assert partial.gevonden < 6
    assert resumed.hervat is True
    assert database.fetch_one("SELECT COUNT(*) FROM bestand")[0] == 6
    assert database.fetch_one("SELECT status FROM scan_run ORDER BY id DESC")[0] == "klaar"


def test_hervatten_bewaart_bestandsresultaat_en_alle_tellingen(tmp_path, monkeypatch):
    library = tmp_path / "films"
    library.mkdir()
    changed = library / "A.mkv"
    changed.touch()
    os.utime(changed, (1, 1))
    db = _database_for_path(tmp_path / "database", library)
    monkeypatch.setattr(scanner_module, "is_hoofdbestand", lambda *a, **kw: (True, "proef", "ok"))
    monkeypatch.setattr(scanner_module, "probe", lambda path: None)
    scan_library(db, 1)
    changed.write_bytes(b"gewijzigd")
    os.utime(changed, (2, 2))
    (library / "B.mkv").touch()  # Nieuw en nog in het stabiliteitsvenster.
    child = library / "vervolg"
    child.mkdir()
    (child / "C.mkv").touch()
    os.utime(child / "C.mkv", (1, 1))
    original = scanner_module._process_file
    processed = 0

    def interrupt(*args, **kwargs):
        nonlocal processed
        original(*args, **kwargs)
        processed += 1
        if processed == 2:
            raise scanner_module.ScanInterrupted("proefstop midden in de map")

    with monkeypatch.context() as patch:
        patch.setattr(scanner_module, "_process_file", interrupt)
        with pytest.raises(scanner_module.ScanInterrupted):
            scan_library(db, 1)

    def counts():
        return tuple(db.fetch_one(
            "SELECT gevonden,nieuw,gewijzigd,uitgesteld FROM scan_run ORDER BY id DESC LIMIT 1"
        ))

    assert counts() == (2, 1, 1, 1)
    partial = scan_library(db, 1, stop_after_directories=1)
    assert partial.hervat and counts() == (2, 1, 1, 1)
    final = scan_library(db, 1)
    assert (final.gevonden, final.nieuw, final.gewijzigd, final.uitgesteld) == (3, 2, 1, 1)
    assert counts() == (3, 2, 1, 1)
    assert db.fetch_one("SELECT COUNT(*) FROM besluit")[0] == 3


def test_fout_bij_cursor_draait_ook_bestand_en_besluit_terug(tmp_path, monkeypatch):
    library = tmp_path / "films"
    library.mkdir()
    source = library / "Film.mkv"
    source.touch()
    os.utime(source, (1, 1))
    db = _database_for_path(tmp_path / "database", library)
    monkeypatch.setattr(scanner_module, "is_hoofdbestand", lambda *a, **kw: (True, "proef", "ok"))
    monkeypatch.setattr(scanner_module, "probe", lambda path: None)
    db.execute("""
        CREATE TRIGGER weiger_cursor BEFORE UPDATE OF scan_cursor ON library
        BEGIN SELECT RAISE(ABORT, 'proefstop bij cursor'); END
    """)
    with pytest.raises(sqlite3.IntegrityError, match="proefstop bij cursor"):
        scan_library(db, 1)
    assert db.fetch_one("SELECT COUNT(*) FROM bestand")[0] == 0
    assert db.fetch_one("SELECT COUNT(*) FROM besluit")[0] == 0
    assert tuple(db.fetch_one("SELECT cursor,gevonden,nieuw FROM scan_run")) == (None, 0, 0)
    db.execute("DROP TRIGGER weiger_cursor")
    assert scan_library(db, 1).nieuw == 1


def test_hervatten_van_oude_scan_zonder_bestandscursor(tmp_path, monkeypatch):
    library = tmp_path / "films"
    library.mkdir()
    source = library / "Film.mkv"
    source.touch()
    os.utime(source, (1, 1))
    db = _database_for_path(tmp_path / "database", library)
    monkeypatch.setattr(scanner_module, "is_hoofdbestand", lambda *a, **kw: (True, "proef", "ok"))
    monkeypatch.setattr(scanner_module, "probe", lambda path: None)
    with monkeypatch.context() as patch:
        patch.setattr(scanner_module, "DECISION_CODE_FINGERPRINT", "eerdere-scanner")
        assert scan_library(db, 1, stop_after_directories=1).nieuw == 1
    # De eerdere scanner kon het bestand en besluit al bewaren vóór de eerste mapcursor.
    with db.connection() as conn, conn:
        conn.execute("UPDATE scan_run SET cursor=NULL,gevonden=0,nieuw=0")
        conn.execute("UPDATE library SET scan_cursor=NULL")
    resumed = scan_library(db, 1)
    assert resumed.hervat and (resumed.gevonden, resumed.nieuw, resumed.gewijzigd) == (1, 1, 0)
    assert db.fetch_one("SELECT COUNT(*) FROM besluit")[0] == 1


def test_profielwijziging_tijdens_scan_herstart_met_nieuwste_instellingen(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    database = _database(tmp_path)
    original_process_file = scanner_module._process_file
    changed = False

    def change_profile_after_first_file(*args: object, **kwargs: object) -> None:
        nonlocal changed
        original_process_file(*args, **kwargs)
        if changed:
            return
        row = database.fetch_one("SELECT inst FROM profiel WHERE id=1")
        values = json.loads(str(row["inst"]))
        values["drempel_1080"] = 999_999
        database.execute(
            "UPDATE profiel SET inst=? WHERE id=1",
            (json.dumps(values, ensure_ascii=False),),
        )
        changed = True

    monkeypatch.setattr(scanner_module, "_process_file", change_profile_after_first_file)

    result = scan_library(database, 1)

    assert changed is True
    assert result.gevonden == 6
    assert database.fetch_one(
        "SELECT COUNT(DISTINCT profiel_fingerprint) FROM bestand"
    )[0] == 1
    latest = database.fetch_one(
        """
        SELECT d.reden
        FROM besluit d JOIN bestand b ON b.id=d.bestand_id
        WHERE b.pad LIKE '%1080p%' AND d.status='nieuw'
        ORDER BY d.id DESC LIMIT 1
        """
    )
    assert latest is not None and "drempel 999999" in str(latest["reden"])


def test_ongewijzigde_media_wordt_niet_opnieuw_geprobed(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    database = _database(tmp_path)
    scan_library(database, 1)

    def forbidden_probe(_path: str) -> None:
        raise AssertionError("ffprobe mag niet draaien voor een ongewijzigd bestand")

    monkeypatch.setattr("app.scanner.probe", forbidden_probe)
    summary = scan_library(database, 1)

    assert summary.nieuw == 0
    assert summary.gewijzigd == 0


def test_nieuw_bestand_wacht_eerst_zestig_minuten(tmp_path: Path) -> None:
    library = tmp_path / "media"
    library.mkdir()
    source = library / "Nieuwe.Film.mkv"
    shutil.copy2(TESTDATA / "sdr-meertalig.mkv", source)
    os.utime(source, None)
    database = _database_for_path(tmp_path / "database", library)

    first = scan_library(database, 1)
    row = database.fetch_one("SELECT scan_status FROM bestand WHERE pad=?", (str(source),))

    assert first.nieuw == 1
    assert first.uitgesteld == 1
    assert row is not None and row["scan_status"] == "wacht_op_stabiliteit"
    assert database.fetch_one("SELECT COUNT(*) FROM besluit")[0] == 0

    old = time.time() - 7200
    os.utime(source, (old, old))
    second = scan_library(database, 1)

    assert second.uitgesteld == 0
    assert database.fetch_one("SELECT scan_status FROM bestand WHERE pad=?", (str(source),))[0] == (
        "gereed"
    )
    assert database.fetch_one("SELECT COUNT(*) FROM besluit")[0] == 1


def test_verdwenen_media_blijft_met_historie_bewaard(tmp_path: Path) -> None:
    library = tmp_path / "media"
    shutil.copytree(TESTDATA / "mappen", library)
    database = _database_for_path(tmp_path / "database", library)
    scan_library(database, 1)
    source = next(path for path in library.rglob("*.mkv"))
    source.unlink()

    summary = scan_library(database, 1)
    row = database.fetch_one(
        "SELECT id,aanwezig,scan_status FROM bestand WHERE pad=?", (str(source),)
    )

    assert summary.verwijderd == 1
    assert row is not None
    assert row["aanwezig"] == 0
    assert row["scan_status"] == "verdwenen"
    assert database.fetch_one(
        "SELECT COUNT(*) FROM besluit WHERE bestand_id=?", (row["id"],)
    )[0] >= 1


def test_gewijzigd_bestand_wordt_na_stabiliteitsvenster_opnieuw_geprobed(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    library = tmp_path / "media"
    library.mkdir()
    source = library / "Film.mkv"
    shutil.copy2(TESTDATA / "sdr-meertalig.mkv", source)
    database = _database_for_path(tmp_path / "database", library)
    scan_library(database, 1)
    os.utime(source, None)
    assert scan_library(database, 1).uitgesteld == 1
    old = time.time() - 7200
    os.utime(source, (old, old))
    calls = 0

    def counted_probe(path: str) -> object:
        nonlocal calls
        calls += 1
        return media_probe(path)

    monkeypatch.setattr("app.scanner.probe", counted_probe)
    result = scan_library(database, 1)

    assert calls == 1
    assert result.gewijzigd == 1
    assert database.fetch_one("SELECT COUNT(*) FROM besluit")[0] == 2


def test_bestand_dat_tijdens_probe_verandert_wordt_uitgesteld(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    library = tmp_path / "media"
    library.mkdir()
    source = library / "Film.mkv"
    shutil.copy2(TESTDATA / "sdr-meertalig.mkv", source)
    old = time.time() - 7200
    os.utime(source, (old, old))
    database = _database_for_path(tmp_path / "database", library)

    def changing_probe(path: str) -> object:
        result = media_probe(path)
        os.utime(path, None)
        return result

    monkeypatch.setattr("app.scanner.probe", changing_probe)
    result = scan_library(database, 1)

    assert result.uitgesteld == 1
    assert database.fetch_one("SELECT scan_status FROM bestand")[0] == (
        "wacht_op_stabiliteit"
    )
    assert database.fetch_one("SELECT COUNT(*) FROM besluit")[0] == 0


def test_ffprobe_houdt_geen_sqlite_schrijftransactie_open(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    library = tmp_path / "media"
    library.mkdir()
    recent = library / "A.Nieuw.mkv"
    stable = library / "B.Stabiel.mkv"
    shutil.copy2(TESTDATA / "sdr-meertalig.mkv", recent)
    shutil.copy2(TESTDATA / "sdr-meertalig.mkv", stable)
    os.utime(recent, None)
    old = time.time() - 7200
    os.utime(stable, (old, old))
    database = _database_for_path(tmp_path / "database", library)

    def probe_with_competing_writer(path: str) -> object:
        with database.connection() as competing:
            competing.execute("PRAGMA busy_timeout=1")
            try:
                competing.execute(
                    "INSERT INTO auditlog(soort,onderwerp,tijd) "
                    "VALUES ('test','scanner-lock',CURRENT_TIMESTAMP)"
                )
                competing.commit()
            except sqlite3.OperationalError as exc:
                pytest.fail(f"ffprobe draaide nog onder een schrijftransactie: {exc}")
        return media_probe(path)

    monkeypatch.setattr("app.scanner.probe", probe_with_competing_writer)

    result = scan_library(database, 1)

    assert result.uitgesteld == 1
    assert database.fetch_one(
        "SELECT COUNT(*) FROM auditlog WHERE onderwerp='scanner-lock'"
    )[0] == 1


@pytest.mark.parametrize("dry_run", [False, True])
def test_voortgang_schrijft_tijdens_mediaprobe(tmp_path, monkeypatch, dry_run):
    from concurrent.futures import ThreadPoolExecutor
    from threading import Event

    from test_onderhoud import _case

    from app.uitvoerder import _set_detail

    database, settings, job = _case(tmp_path)
    source = settings.library_root / "Ander bestand.mkv"
    source.touch()
    os.utime(source, (1, 1))
    database.execute("UPDATE job SET percent=37 WHERE id=?", (job,))
    begonnen, vrijgave = Event(), Event()

    def trage_probe(*args, **kwargs):
        begonnen.set()
        assert vrijgave.wait(10)
        return True, "proef", "ok"

    monkeypatch.setattr(scanner_module, "is_hoofdbestand", trage_probe)
    monkeypatch.setattr(scanner_module, "probe", lambda path: None)
    with ThreadPoolExecutor(2) as pool:
        scan = pool.submit(scan_library, database, 1, dry_run=dry_run)
        try:
            assert begonnen.wait(5)
            voortgang = pool.submit(_set_detail, database, job, "proef", 65)
            voortgang.result(timeout=3)
            assert database.fetch_one("SELECT percent FROM job WHERE id=?", (job,))[0] == 65
        finally:
            vrijgave.set()
        assert scan.result(timeout=5).gevonden == 1


def test_scan_toetst_schema_opnieuw_na_de_probe(tmp_path, monkeypatch):
    from contextlib import closing

    from app.db import SchemaTooNew

    library = tmp_path / "media"
    library.mkdir()
    source = library / "Proef.mkv"
    source.touch()
    os.utime(source, (1, 1))
    db = _database_for_path(tmp_path / "database", library)

    def nieuw_schema(*args, **kwargs):
        with closing(sqlite3.connect(db.path)) as peer, peer:
            peer.execute("INSERT INTO schema_versie VALUES (999, 'proef', 'proef')")
        return True, "proef", "ok"

    monkeypatch.setattr(scanner_module, "is_hoofdbestand", nieuw_schema)
    monkeypatch.setattr(scanner_module, "probe", lambda path: None)
    with pytest.raises(SchemaTooNew):
        scan_library(db, 1)
    with closing(sqlite3.connect(db.path)) as conn:
        assert conn.execute("SELECT COUNT(*) FROM bestand").fetchone() == (0,)
        assert conn.execute("SELECT COUNT(*) FROM besluit").fetchone() == (0,)
        assert conn.execute("SELECT cursor,gevonden FROM scan_run").fetchone() == (None, 0)
