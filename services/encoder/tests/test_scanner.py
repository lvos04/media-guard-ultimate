from __future__ import annotations

import json
import os
import shutil
import sqlite3
import time
from dataclasses import replace
from pathlib import Path
from types import SimpleNamespace

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


def _gewijzigd_met_wachttaak(tmp_path, monkeypatch):
    from app.media.beslissen import Besluit
    from app.uitvoerder import create_job_from_decision

    library = tmp_path / "films"
    library.mkdir()
    film, taakbron = library / "Film.mkv", library / "Z.mkv"
    for bron in (film, taakbron):
        bron.write_bytes(b"synthetisch")
        os.utime(bron, (1, 1))
    db = _database_for_path(tmp_path / "database", library)
    monkeypatch.setattr(scanner_module, "is_hoofdbestand", lambda *a, **kw: (True, "proef", "ok"))
    monkeypatch.setattr(scanner_module, "probe", lambda path: None)
    origineel = scanner_module.decide_catalog_item
    monkeypatch.setattr(
        scanner_module, "decide_catalog_item",
        lambda path, *a, **kw: Besluit(actie="hercoderen", baan="cpu", encoder="libsvtav1",
                                       reden="proef") if Path(path).name == "Z.mkv"
        else origineel(path, *a, **kw),
    )
    with monkeypatch.context() as patch:  # De taakbron houdt een oudere vingerafdruk.
        patch.setattr(scanner_module, "DECISION_CODE_FINGERPRINT", "eerdere-versie")
        scan_library(db, 1)
    besluit = db.fetch_one("SELECT id FROM besluit WHERE actie='hercoderen'")[0]
    db.execute("UPDATE besluit SET status='goedgekeurd' WHERE id=?", (besluit,))
    create_job_from_decision(db, besluit)
    film.write_bytes(b"gewijzigde vermelding")
    return db


def _stop_na_beide_bestanden(db, monkeypatch):
    original = scanner_module._process_file
    verwerkt = []

    def stop(*args, **kwargs):
        original(*args, **kwargs)
        verwerkt.append(args[2])
        if len(verwerkt) == 2:
            raise scanner_module.ScanInterrupted("proefstop")

    with monkeypatch.context() as patch:
        patch.setattr(scanner_module, "_process_file", stop)
        with pytest.raises(scanner_module.ScanInterrupted):
            scan_library(db, 1)


def _api_telling(db):
    from fastapi import FastAPI
    from fastapi.testclient import TestClient

    from app.api import create_api_router

    app = FastAPI()
    app.state.database = db
    app.include_router(create_api_router())
    with TestClient(app) as client:
        antwoord = client.get("/api/libraries")
        antwoord.raise_for_status()
        (bibliotheek,) = antwoord.json()
    return bibliotheek["laatste_scan_gewijzigd"], bibliotheek["laatste_scan_telling_onvolledig"]


def _historische_registratie(db, *, opgeslagen, midden_in_map):
    """Bewaar wat de vroegere scanner op dit onderbrekingspunt had vastgelegd."""
    with db.connection() as conn, conn:
        scan_id = conn.execute("SELECT MAX(id) FROM scan_run").fetchone()[0]
        conn.execute("DELETE FROM scan_bijdrage WHERE scan_id=?", (scan_id,))
        conn.execute("DELETE FROM staat WHERE sleutel=?", (f"volledige_scantelling:{scan_id}",))
        conn.execute("UPDATE scan_run SET gewijzigd=? WHERE id=?", (opgeslagen, scan_id))
        if midden_in_map:
            conn.execute("UPDATE scan_run SET gevonden=0,nieuw=0,uitgesteld=0,cursor=NULL "
                         "WHERE id=?", (scan_id,))
            conn.execute("UPDATE library SET scan_cursor=NULL WHERE id=1")


@pytest.mark.parametrize("positie", ["midden", "mapcursor", "afgerond"])
def test_historische_scan_blijft_onvolledig_na_heropenen(tmp_path, monkeypatch, capsys, positie):
    from app import cli

    db = _gewijzigd_met_wachttaak(tmp_path, monkeypatch)
    if positie == "midden":
        _stop_na_beide_bestanden(db, monkeypatch)
    else:
        stop = 1 if positie == "mapcursor" else None
        assert scan_library(db, 1, stop_after_directories=stop).gewijzigd == 1
    _historische_registratie(db, opgeslagen=0, midden_in_map=positie == "midden")
    db = Database(db.settings)
    db.initialize()
    assert _api_telling(db) == (0, 1)
    if positie == "mapcursor":
        monkeypatch.setattr(cli, "settings", db.settings)
        assert cli.main(["scan", "1"]) == 0
        uit = json.loads(capsys.readouterr().out)
        assert (uit["hervat"], uit["gewijzigd"], uit["telling_onvolledig"]) == (True, 0, True)
    elif positie == "midden":
        assert scan_library(db, 1).gewijzigd == 0
    assert _api_telling(Database(db.settings)) == (0, 1)
    assert [r[0] for r in db.fetch_all("SELECT status FROM job")] == ["wacht"]
    scan_library(db, 1)
    assert _api_telling(db) == (0, 0)


@pytest.mark.parametrize("vervang", [True, False])
def test_tweede_bestandsmeting_in_dezelfde_scan_telt_eenmaal(tmp_path, monkeypatch, vervang):
    mapje = tmp_path / "films"
    mapje.mkdir()
    film = mapje / "Film.mkv"
    film.write_bytes(b"a" * 11)
    os.utime(film, (1, 1))
    db = _database_for_path(tmp_path / "database", mapje)
    monkeypatch.setattr(scanner_module, "is_hoofdbestand", lambda *a, **kw: (True, "proef", "ok"))
    monkeypatch.setattr(scanner_module, "probe", lambda pad: None)
    assert scan_library(db, 1).nieuw == 1
    verwerk = scanner_module._process_file
    metingen = []

    def herbeoordeel(*args, **kwargs):
        verwerk(*args, **kwargs)
        metingen.append(tuple(db.fetch_one(
            "SELECT f.grootte,b.gewijzigd,s.gewijzigd FROM scan_bijdrage b "
            "JOIN bestand f ON f.id=b.bestand_id JOIN scan_run s ON s.id=b.scan_id "
            "WHERE s.status='bezig'"
        )))
        if len(metingen) <= 2:
            if len(metingen) == 1 and vervang:
                film.write_bytes(b"a" * 49)
                os.utime(film, (1, 1))
            db.execute("UPDATE profiel SET inst=? WHERE id=1",
                       (json.dumps({"drempel_1080": len(metingen)}),))

    monkeypatch.setattr(scanner_module, "_process_file", herbeoordeel)
    assert scan_library(db, 1).gewijzigd == int(vervang)
    laatste = (49 if vervang else 11, int(vervang), int(vervang))
    assert metingen == [(11, 0, 0), laatste, laatste]
    assert _api_telling(Database(db.settings)) == (int(vervang), 0)


@pytest.mark.parametrize("sprong", [0, 3700])
def test_wijzigingstelling_blijft_na_hervatten_met_wachttaak(tmp_path, monkeypatch, sprong):
    db = _gewijzigd_met_wachttaak(tmp_path, monkeypatch)
    _stop_na_beide_bestanden(db, monkeypatch)
    # De taak behoudt zijn oudere profielvingerafdruk; dat mag de telling niet wissen.
    monkeypatch.setattr(scanner_module, "time", SimpleNamespace(time=lambda: time.time() + sprong))
    final = scan_library(db, 1)
    assert final.hervat and final.gewijzigd == 1
    assert db.fetch_one("SELECT gewijzigd,fout FROM scan_run ORDER BY id DESC LIMIT 1")[0] == 1
    assert [r[0] for r in db.fetch_all("SELECT status FROM job")] == ["wacht"]
    assert db.fetch_one("SELECT COUNT(*) FROM besluit")[0] == 2


@pytest.mark.parametrize("sprong", [0, 3700])
@pytest.mark.parametrize("opgeslagen", [0, 1])
def test_oude_scan_zonder_telling_wordt_ondergrens_en_zichtbaar_onvolledig(
    tmp_path, monkeypatch, sprong, opgeslagen,
):
    db = _gewijzigd_met_wachttaak(tmp_path, monkeypatch)
    _stop_na_beide_bestanden(db, monkeypatch)
    assert _api_telling(db) == (1, 0)
    _historische_registratie(db, opgeslagen=opgeslagen, midden_in_map=True)
    if sprong:  # Het stabiliteitsvenster verstrijkt: het bestand wordt nu heranalyseerd.
        klok = SimpleNamespace(time=lambda: time.time() + sprong)
        monkeypatch.setattr(scanner_module, "time", klok)
    final = scan_library(db, 1)
    assert final.gewijzigd == opgeslagen  # Heranalyse bewijst geen extra bronwijziging.
    assert final.telling_onvolledig
    assert _api_telling(db) == (opgeslagen, 1)
    fout = db.fetch_one("SELECT fout FROM scan_run ORDER BY id DESC LIMIT 1")[0]
    assert fout.startswith("telling onvolledig")


def test_opgeslagen_telling_overleeft_profielherbeoordeling_bij_update(tmp_path, monkeypatch):
    db = _gewijzigd_met_wachttaak(tmp_path, monkeypatch)
    _stop_na_beide_bestanden(db, monkeypatch)
    _historische_registratie(db, opgeslagen=1, midden_in_map=False)
    assert db.fetch_one("SELECT gewijzigd FROM scan_run ORDER BY id DESC LIMIT 1")[0] == 1
    with monkeypatch.context() as patch:  # De update brengt een andere besliscode mee.
        patch.setattr(scanner_module, "DECISION_CODE_FINGERPRINT", "nieuwe-versie")
        final = scan_library(db, 1)
    assert final.gewijzigd == 1
    assert final.telling_onvolledig
    assert _api_telling(db) == (1, 1)


@pytest.mark.parametrize("opgeslagen", [0, 1])
def test_bewezen_vervanging_na_3700_seconden_telt_eenmaal(tmp_path, monkeypatch, opgeslagen):
    db = _gewijzigd_met_wachttaak(tmp_path, monkeypatch)
    _stop_na_beide_bestanden(db, monkeypatch)
    _historische_registratie(db, opgeslagen=opgeslagen, midden_in_map=True)
    film = tmp_path / "films/Film.mkv"
    tijd = film.stat().st_mtime_ns
    film.write_bytes(b"een tweede, aantoonbaar grotere vermelding")
    os.utime(film, ns=(tijd, tijd))
    monkeypatch.setattr(scanner_module, "time", SimpleNamespace(time=lambda: time.time() + 3700))
    assert scan_library(db, 1, stop_after_directories=1).gewijzigd == 1
    assert _api_telling(db) == (1, 1)
    assert scan_library(Database(db.settings), 1).gewijzigd == 1
    assert db.fetch_one("SELECT SUM(gewijzigd) FROM scan_bijdrage "
                        "WHERE scan_id=(SELECT MAX(id) FROM scan_run)")[0] == 1
    assert _api_telling(db) == (1, 1)
    assert [r[0] for r in db.fetch_all("SELECT status FROM job")] == ["wacht"]


def test_geweigerde_cursor_draait_ook_verhoogde_bijdrage_terug(tmp_path, monkeypatch):
    db = _gewijzigd_met_wachttaak(tmp_path, monkeypatch)
    scan_library(db, 1)
    assert scan_library(db, 1, stop_after_directories=1).gewijzigd == 0
    film = tmp_path / "films/Film.mkv"
    oude_maat = film.stat().st_size
    film.write_bytes(b"vervanging tussen twee vastgelegde metingen")
    db.execute("UPDATE profiel SET inst='{}' WHERE id=1")
    db.execute("""
        CREATE TRIGGER weiger_verhoging BEFORE UPDATE OF scan_cursor ON library
        WHEN (SELECT gewijzigd FROM scan_run WHERE status='bezig') > 0
        BEGIN SELECT RAISE(ABORT, 'geweigerde cursor'); END
    """)
    with pytest.raises(sqlite3.IntegrityError, match="geweigerde cursor"):
        scan_library(db, 1)
    assert tuple(db.fetch_one(
        "SELECT f.grootte,b.gewijzigd,s.gewijzigd FROM bestand f "
        "JOIN scan_bijdrage b ON b.bestand_id=f.id JOIN scan_run s ON s.id=b.scan_id "
        "WHERE s.status='bezig' AND f.pad=?", (str(film),),
    )) == (oude_maat, 0, 0)
    db.execute("DROP TRIGGER weiger_verhoging")
    assert scan_library(db, 1).gewijzigd == 1
    assert _api_telling(db) == (1, 0)


@pytest.mark.parametrize("sprong", [0, 180, 3700, 4000])
def test_voorlopende_bestandstijd_telt_niet_als_wijziging_bij_hervatten(
    tmp_path, monkeypatch, sprong,
):
    db = _gewijzigd_met_wachttaak(tmp_path, monkeypatch)
    film = Path(db.fetch_one("SELECT pad FROM library")[0]) / "Film.mkv"
    toekomst = time.time() + 120
    os.utime(film, (toekomst, toekomst))
    scan_library(db, 1)  # Deze tijdstempel is nu bekend en geldt als ongewijzigd.
    _stop_na_beide_bestanden(db, monkeypatch)
    _historische_registratie(db, opgeslagen=0, midden_in_map=True)
    monkeypatch.setattr(scanner_module, "time", SimpleNamespace(time=lambda: time.time() + sprong))
    assert scan_library(db, 1).gewijzigd == 0


def test_api_toont_onvolledige_telling_ook_voor_hervatten(tmp_path, monkeypatch):
    db = _gewijzigd_met_wachttaak(tmp_path, monkeypatch)
    _stop_na_beide_bestanden(db, monkeypatch)
    assert _api_telling(db) == (1, 0)
    # Zelfs met een bekende scanstart is één ontbrekende bestandsbijdrage voldoende.
    with db.connection() as conn, conn:
        conn.execute("DELETE FROM scan_bijdrage WHERE gewijzigd=1 "
                     "AND scan_id=(SELECT MAX(id) FROM scan_run)")
        conn.execute("UPDATE scan_run SET gewijzigd=0 WHERE status='bezig'")
    assert _api_telling(db) == (0, 1)
    scan_library(db, 1)
    assert _api_telling(db) == (0, 1)


@pytest.mark.parametrize("historisch", [False, True])
def test_live_afmelding_bewaart_de_telling_van_de_afgeronde_scan(tmp_path, monkeypatch, historisch):
    import asyncio
    from unittest.mock import AsyncMock

    from fastapi import WebSocket

    from app import live

    db = _gewijzigd_met_wachttaak(tmp_path, monkeypatch)
    assert scan_library(db, 1, stop_after_directories=1).gewijzigd == 1
    if historisch:
        _historische_registratie(db, opgeslagen=1, midden_in_map=False)
    bezig = live._scan_states(db)
    assert scan_library(db, 1).gewijzigd == 1
    # Tussen twee WebSocket-ticks kan de volgende scan al zijn begonnen.
    scan_library(db, 1, stop_after_directories=1)
    socket = AsyncMock(spec=WebSocket)
    asyncio.run(live._send_events(socket, db, {}, bezig))
    socket.send_json.assert_awaited_once_with({
        "type": "scan_klaar", "library_id": 1, "nieuw": 0, "gewijzigd": 1,
        "telling_onvolledig": historisch,
    })


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
