"""Hervatbare, uitsluitend lezende bibliotheekscanner."""

from __future__ import annotations

import hashlib
import json
import logging
import os
import sqlite3
import threading
import time
from dataclasses import dataclass, field
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from app.beslisser import decide_catalog_item, store_decision
from app.bronnen import SourceError, validate_registered_source
from app.db import Database
from app.jobstatus import ACTIVE_SQL
from app.media.beslissen import Besluit
from app.media.bestandsfilter import VIDEO_EXT, is_hoofdbestand
from app.media.probe import probe


class ScanError(RuntimeError):
    """Een library of profiel is ongeldig."""


class ScanBusy(ScanError):
    """Een andere scan houdt de procesbrede scanlock vast; later opnieuw proberen."""


class ScanInterrupted(ScanError):
    """Service stopt: de scan staat netjes op zijn mapcursor en hervat later."""


# Gezet bij het stoppen van de backend; een lopende scan stopt na de huidige map.
SCAN_STOP = threading.Event()


SCAN_LOCK = threading.Lock()
FILE_STABILITY_SECONDS = 3600
LOGGER = logging.getLogger(__name__)

# Een volgende scan mag het bewijs voor de vorige afmelding niet overschrijven.
SCANTELLING_SLEUTEL = "volledige_scantelling:"
SCANTELLING_GEREGISTREERD = f"""EXISTS (
    SELECT 1 FROM staat WHERE sleutel='{SCANTELLING_SLEUTEL}' || s.id
    AND waarde=CAST(s.library_id AS TEXT) AND tijd=s.gestart_op
)"""
SCANTELLING_ONVOLLEDIG = f"""(
    COALESCE(s.fout LIKE 'telling onvolledig%', 0) OR NOT {SCANTELLING_GEREGISTREERD}
    OR EXISTS (SELECT 1 FROM bestand f
               WHERE f.library_id=s.library_id AND f.gescand_op=s.gestart_op AND f.aanwezig=1
               AND NOT EXISTS (SELECT 1 FROM scan_bijdrage b
                               WHERE b.scan_id=s.id AND b.bestand_id=f.id))
)"""


def _decision_code_fingerprint() -> str:
    root = Path(__file__).parent
    digest = hashlib.sha256()
    for path in (
        Path(__file__),
        root / "beslisser.py",
        root / "media" / "beslissen.py",
        root / "media" / "bestandsfilter.py",
    ):
        digest.update(path.read_bytes())
    return digest.hexdigest()


DECISION_CODE_FINGERPRINT = _decision_code_fingerprint()


@dataclass(slots=True)
class ScanItem:
    pad: str
    categorie: str
    filter_reden: str
    actie: str
    besluit_reden: str
    nlink: int


@dataclass(slots=True)
class ScanSummary:
    library_id: int
    dry_run: bool
    gevonden: int = 0
    nieuw: int = 0
    gewijzigd: int = 0
    verwijderd: int = 0
    uitgesteld: int = 0
    hervat: bool = False
    items: list[ScanItem] = field(default_factory=list)
    telling_onvolledig: bool = False


def _library(database: Database, library_id: int) -> tuple[Any, dict[str, Any]]:
    row = database.fetch_one(
        """
        SELECT l.*, p.inst AS profiel_inst
        FROM library l JOIN profiel p ON p.id=l.profiel_id
        WHERE l.id=? AND l.actief=1
        """,
        (library_id,),
    )
    if row is None:
        raise ScanError(f"Actieve library {library_id} bestaat niet")
    profile_value = json.loads(str(row["profiel_inst"]))
    if not isinstance(profile_value, dict):
        raise ScanError("Profielinstellingen moeten een JSON-object zijn")
    return row, profile_value


def _profile_fingerprint(profile: dict[str, Any]) -> str:
    """Koppel een besluit aan zowel de actuele instellingen als de besliscode."""

    return hashlib.sha256(
        json.dumps(
            {"profiel": profile, "besliscode": DECISION_CODE_FINGERPRINT},
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
        ).encode()
    ).hexdigest()


def _profile_from_connection(connection: Any, library_id: int) -> dict[str, Any]:
    row = connection.execute(
        """
        SELECT p.inst
        FROM library l JOIN profiel p ON p.id=l.profiel_id
        WHERE l.id=? AND l.actief=1
        """,
        (library_id,),
    ).fetchone()
    if row is None:
        raise ScanError(f"Actieve library {library_id} bestaat niet")
    value = json.loads(str(row["inst"]))
    if not isinstance(value, dict):
        raise ScanError("Profielinstellingen moeten een JSON-object zijn")
    return value


@dataclass(slots=True)
class _ScanBijdragen:
    scan_id: int
    gestart_op: str
    # Een oude tussentelling kan wijzigingen bevatten waarvan de bestands-id onbekend is.
    ongekoppeld_gewijzigd: int = 0

    def hervat(
        self, conn: sqlite3.Connection, summary: ScanSummary, *, vanaf_begin: bool = False,
    ) -> None:
        # Een eerdere scanner kon bestanden bewaren zonder bijdrage. Alleen het eerste
        # zien is vastgelegd. De huidige mtime vertelt niets over de vorige meting.
        ontbrekend = conn.execute(
            """INSERT INTO scan_bijdrage(scan_id,bestand_id,nieuw,gewijzigd)
               SELECT ?,id,COALESCE(eerste_gezien >= ?,0),0 FROM bestand
               WHERE library_id=? AND gescand_op=? AND aanwezig=1
               ON CONFLICT(scan_id,bestand_id) DO NOTHING""",
            (self.scan_id, self.gestart_op, summary.library_id, self.gestart_op),
        ).rowcount
        nieuw, gewijzigd = conn.execute(
            "SELECT COALESCE(SUM(nieuw),0),COALESCE(SUM(gewijzigd),0) FROM scan_bijdrage "
            "WHERE scan_id=?", (self.scan_id,),
        ).fetchone()
        rij = conn.execute(
            f"SELECT s.nieuw,s.gewijzigd,s.fout,{SCANTELLING_GEREGISTREERD} AS volledig "
            "FROM scan_run s WHERE s.id=?", (self.scan_id,),
        ).fetchone()
        eerder_onvolledig = (rij["fout"] or "").startswith("telling onvolledig")
        summary.telling_onvolledig = bool(ontbrekend or not rij["volledig"] or eerder_onvolledig)
        if ontbrekend and rij["volledig"]:
            # Ook een latere scanfout mag deze lacune niet verhullen.
            conn.execute("DELETE FROM staat WHERE sleutel=?",
                         (f"{SCANTELLING_SLEUTEL}{self.scan_id}",))
        summary.nieuw = max(rij["nieuw"], nieuw)
        summary.gewijzigd = max(rij["gewijzigd"], gewijzigd)
        self.ongekoppeld_gewijzigd = summary.gewijzigd - gewijzigd
        conn.execute(
            "UPDATE scan_run SET nieuw=?,gewijzigd=?,"
            "fout=CASE WHEN ? AND fout IS NULL THEN 'telling onvolledig: eerdere scanner' "
            "ELSE fout END WHERE id=?",
            (summary.nieuw, summary.gewijzigd, summary.telling_onvolledig, self.scan_id),
        )
        if vanaf_begin:
            conn.execute(
                "UPDATE scan_run SET cursor=NULL,gevonden=0,uitgesteld=0 WHERE id=?",
                (self.scan_id,),
            )
            conn.execute("UPDATE library SET scan_cursor=NULL WHERE id=?", (summary.library_id,))

    def registreer(
        self, conn: sqlite3.Connection, path: Path, summary: ScanSummary,
        voor: tuple[int, int], bron_gewijzigd: bool,
    ) -> None:
        nieuw = summary.nieuw - voor[0]
        gewijzigd = summary.gewijzigd - voor[1]
        toegevoegd = conn.execute(
            "INSERT INTO scan_bijdrage(scan_id,bestand_id,nieuw,gewijzigd) "
            "SELECT ?,id,?,? FROM bestand WHERE pad=? "
            "ON CONFLICT(scan_id,bestand_id) DO NOTHING",
            (self.scan_id, nieuw, gewijzigd, str(path)),
        ).rowcount
        if toegevoegd:
            return

        summary.nieuw, summary.gewijzigd = voor
        if not (gewijzigd and bron_gewijzigd):
            return
        verhoogd = conn.execute(
            "UPDATE scan_bijdrage SET gewijzigd=1 WHERE scan_id=? "
            "AND bestand_id=(SELECT id FROM bestand WHERE pad=?) AND nieuw=0 AND gewijzigd=0",
            (self.scan_id, str(path)),
        ).rowcount
        if verhoogd:
            # Hetzelfde historische bestand kan al in het bewaarde totaal zitten.
            if self.ongekoppeld_gewijzigd:
                self.ongekoppeld_gewijzigd -= 1
            else:
                summary.gewijzigd += 1


# Systeem-, prullenbak- en snapshotmappen van NAS'en; nooit media om te verwerken.
SKIP_DIRECTORIES = {".recycle", ".zfs", "@eadir", "#recycle", "$recycle.bin", "lost+found"}


def _skip_name(name: str) -> bool:
    lowered = name.casefold()
    return (
        lowered.startswith(".")
        or lowered in SKIP_DIRECTORIES
        or lowered.startswith(".trash-")
    )


def _directories(root: Path) -> list[Path]:
    directories: list[Path] = []
    # followlinks=False (standaard) plus het wegfilteren van verborgen en
    # systeemmappen: geen dubbele bestanden via symlinks, geen snapshots.
    for current, names, _files in os.walk(root):
        names[:] = sorted(name for name in names if not _skip_name(name))
        directories.append(Path(current))
    return sorted(directories, key=lambda path: str(path))


def scan_library(
    database: Database,
    library_id: int,
    *,
    dry_run: bool = False,
    stop_after_directories: int | None = None,
) -> ScanSummary:
    """Scan per map en commit onderweg; een test kan gecontroleerd onderbreken."""

    if not SCAN_LOCK.acquire(blocking=False):
        raise ScanBusy("Er draait al een bibliotheekscan")
    try:
        return _scan_library_locked(
            database,
            library_id,
            dry_run=dry_run,
            stop_after_directories=stop_after_directories,
        )
    finally:
        SCAN_LOCK.release()


def _scan_library_locked(
    database: Database,
    library_id: int,
    *,
    dry_run: bool,
    stop_after_directories: int | None,
) -> ScanSummary:
    """Voer één procesbrede scan uit; SQLite bewaakt daarnaast de libraryclaim."""

    library, profile = _library(database, library_id)
    profile_fingerprint = _profile_fingerprint(profile)
    root = Path(str(library["pad"]))
    if not root.is_dir():
        raise ScanError(f"Librarypad bestaat niet of is geen map: {root}")
    try:
        current_device = validate_registered_source(
            root,
            source_type=str(library["bron_type"]),
            source_uri=str(library["bron_uri"]) if library["bron_uri"] else None,
            expected_device=(
                int(library["verwacht_device"]) if library["verwacht_device"] is not None else None
            ),
        )
    except SourceError as exc:
        raise ScanError(str(exc)) from exc
    if str(library["bron_type"]) == "smb" and current_device != library["verwacht_device"]:
        database.execute(
            "UPDATE library SET verwacht_device=? WHERE id=?", (current_device, library_id)
        )

    summary = ScanSummary(library_id=library_id, dry_run=dry_run)
    scan_id: int | None = None
    bijdragen: _ScanBijdragen | None = None
    cursor_path = ""
    file_cursor: Path | None = None
    started_at = datetime.now(UTC).isoformat()
    if not dry_run:
        current = database.fetch_one(
            """SELECT s.*, l.scan_cursor FROM scan_run s JOIN library l ON l.id=s.library_id
               WHERE s.library_id=? AND s.status='bezig' ORDER BY s.id DESC LIMIT 1""",
            (library_id,),
        )
        if current is not None:
            scan_id = int(current["id"])
            cursor_path = str(current["cursor"] or "")
            # scan_run.cursor wijst naar de laatste voltooide map. Binnen de
            # volgende map wijst library.scan_cursor naar het laatste vastgelegde bestand.
            if current["scan_cursor"] and current["scan_cursor"] != cursor_path:
                file_cursor = Path(current["scan_cursor"])
            summary.gevonden = current["gevonden"]
            summary.nieuw = current["nieuw"]
            summary.gewijzigd = current["gewijzigd"]
            summary.uitgesteld = current["uitgesteld"]
            started_at = str(current["gestart_op"])
            summary.hervat = True
        else:
            with database.connection() as conn, conn:
                try:
                    scan_id = conn.execute(
                        "INSERT INTO scan_run(library_id,status,gestart_op) VALUES (?, 'bezig', ?)",
                        (library_id, started_at),
                    ).lastrowid
                except sqlite3.IntegrityError as exc:
                    raise ScanError("Voor deze library draait al een scan") from exc
                if scan_id is None:
                    raise sqlite3.DatabaseError("Nieuwe scan heeft geen id")
                conn.execute(
                    "INSERT INTO staat(sleutel,waarde,tijd) VALUES (?,?,?) "
                    "ON CONFLICT(sleutel) DO UPDATE SET waarde=excluded.waarde,tijd=excluded.tijd",
                    (f"{SCANTELLING_SLEUTEL}{scan_id}", str(library_id), started_at),
                )
        database.execute(
            "UPDATE library SET laatste_scan_status='bezig',laatste_scan_fout=NULL WHERE id=?",
            (library_id,),
        )

        bijdragen = _ScanBijdragen(scan_id, started_at)
        # Een afgebroken scan kan vóór een profielwijziging zijn begonnen. In
        # dat geval is de opgeslagen cursor niet meer geldig: ook de mappen vóór
        # die cursor moeten opnieuw door de beslisboom met het nieuwste profiel.
        stale_pass = database.fetch_one(
            f"""
            SELECT 1 FROM bestand
            WHERE library_id=? AND gescand_op=?
              AND COALESCE(profiel_fingerprint, '')<>?
              AND NOT EXISTS (SELECT 1 FROM job WHERE bestand_id=bestand.id
                              AND status IN {ACTIVE_SQL})
            LIMIT 1
            """,
            (library_id, started_at, profile_fingerprint),
        )
        if stale_pass is not None:
            cursor_path = ""
            file_cursor = None
            summary = ScanSummary(library_id=library_id, dry_run=False, hervat=True)
            with database.connection() as connection, connection:
                bijdragen.hervat(connection, summary, vanaf_begin=True)
        elif summary.hervat:
            with database.connection() as connection, connection:
                bijdragen.hervat(connection, summary)

    processed_directories = 0
    while True:
        restart_with_new_profile = False
        for directory in _directories(root):
            directory_text = str(directory)
            if cursor_path and directory_text <= cursor_path:
                continue
            if file_cursor is not None and directory_text < str(file_cursor.parent):
                continue
            if SCAN_STOP.is_set():
                raise ScanInterrupted("scan onderbroken door servicestop; hervat later")

            # Een profiel kan via de UI worden aangepast terwijl een grote scan
            # uren loopt. Controleer vóór iedere map; een wijziging maakt de
            # volledige voortgang van deze scan ongeldig.
            _library_row, latest_profile = _library(database, library_id)
            latest_fingerprint = _profile_fingerprint(latest_profile)
            if latest_fingerprint != profile_fingerprint:
                profile = latest_profile
                profile_fingerprint = latest_fingerprint
                cursor_path = ""
                file_cursor = None
                summary = ScanSummary(
                    library_id=library_id,
                    dry_run=dry_run,
                    hervat=summary.hervat,
                )
                if bijdragen is not None:
                    with database.connection() as connection, connection:
                        bijdragen.hervat(connection, summary, vanaf_begin=True)
                LOGGER.info(
                    "Profiel van library %s wijzigde tijdens de scan; herstart vanaf het begin",
                    library_id,
                )
                restart_with_new_profile = True
                break

            try:
                entries = sorted(directory.iterdir(), key=_file_order)
            except FileNotFoundError:
                # Map verdween tijdens de scan (Radarr/Sonarr verplaatste hem).
                entries = []
            for path in entries:
                if (file_cursor is not None and directory == file_cursor.parent
                        and _file_order(path) <= _file_order(file_cursor)):
                    continue
                if (
                    _skip_name(path.name)
                    or path.is_symlink()
                    or not path.is_file()
                    or path.suffix.lower() not in VIDEO_EXT
                ):
                    continue
                try:
                    stat = path.stat()
                    _process_file(
                        database,
                        library_id,
                        path,
                        stat,
                        profile,
                        profile_fingerprint,
                        started_at,
                        dry_run,
                        summary,
                        root,
                        bijdragen,
                    )
                except FileNotFoundError:
                    # Eén verdwenen bestand mag niet de hele scan afbreken;
                    # de volgende scan markeert het als afwezig.
                    LOGGER.info("Bestand verdween tijdens de scan: %s", path)
                    continue
            if not dry_run and scan_id is not None:
                with database.connection() as connection:
                    connection.execute(
                        "UPDATE scan_run SET cursor=? WHERE id=?", (directory_text, scan_id),
                    )
                    connection.execute(
                        "UPDATE library SET scan_cursor=? WHERE id=?",
                        (directory_text, library_id),
                    )
                    connection.commit()
            file_cursor = None
            processed_directories += 1
            if (
                stop_after_directories is not None
                and processed_directories >= stop_after_directories
            ):
                return summary

        if restart_with_new_profile:
            continue

        if dry_run:
            _library_row, latest_profile = _library(database, library_id)
            latest_fingerprint = _profile_fingerprint(latest_profile)
            if latest_fingerprint != profile_fingerprint:
                profile = latest_profile
                profile_fingerprint = latest_fingerprint
                cursor_path = ""
                file_cursor = None
                summary = ScanSummary(
                    library_id=library_id,
                    dry_run=True,
                    hervat=summary.hervat,
                )
                continue
            return summary

        if bijdragen is None:
            return summary

        with database.connection() as connection:
            # BEGIN IMMEDIATE sluit het laatste kleine racevenster: een profiel-
            # wijziging kan niet tegelijk met het definitief afronden committen.
            latest_profile = _profile_from_connection(connection, library_id)
            latest_fingerprint = _profile_fingerprint(latest_profile)
            if latest_fingerprint != profile_fingerprint:
                profile = latest_profile
                profile_fingerprint = latest_fingerprint
                cursor_path = ""
                summary = ScanSummary(
                    library_id=library_id,
                    dry_run=False,
                    hervat=summary.hervat,
                )
                bijdragen.hervat(connection, summary, vanaf_begin=True)
                connection.commit()
                LOGGER.info(
                    "Profiel van library %s wijzigde bij afronding; herstart vanaf het begin",
                    library_id,
                )
                continue
            cursor = connection.execute(
                """
                UPDATE bestand
                SET aanwezig=0,scan_status='verdwenen'
                WHERE library_id=? AND gescand_op < ? AND aanwezig=1
                """,
                (library_id, started_at),
            )
            summary.verwijderd = cursor.rowcount
            connection.execute(
                """
                UPDATE besluit
                SET status='afgewezen',reden=reden || '; bron niet meer aanwezig'
                WHERE status='nieuw' AND bestand_id IN (
                    SELECT id FROM bestand WHERE library_id=? AND aanwezig=0
                )
                """,
                (library_id,),
            )
            now = datetime.now(UTC).isoformat()
            connection.execute(
                """
                UPDATE scan_run
                SET status='klaar',klaar_op=?,gevonden=?,nieuw=?,gewijzigd=?,
                    verdwenen=?,uitgesteld=?
                WHERE id=?
                """,
                (
                    now,
                    summary.gevonden,
                    summary.nieuw,
                    summary.gewijzigd,
                    summary.verwijderd,
                    summary.uitgesteld,
                    scan_id,
                ),
            )
            connection.execute(
                """
                UPDATE library
                SET laatste_scan=?,scan_cursor=NULL,laatste_scan_status='klaar',
                    laatste_scan_fout=NULL
                WHERE id=?
                """,
                (now, library_id),
            )
            connection.commit()
        return summary


def _file_order(path: Path) -> tuple[str, str]:
    return path.name.casefold(), path.name


@dataclass(frozen=True, slots=True)
class _FileState:
    existing: sqlite3.Row | None
    metadata_unchanged: bool
    profile_unchanged: bool

    @property
    def active(self) -> bool:
        return self.existing is not None and bool(self.existing["actief"])

    def reuse_decision(self, dry_run: bool) -> bool:
        return (not dry_run and self.existing is not None and self.metadata_unchanged
                and self.profile_unchanged and self.existing["scan_status"] == "gereed")

    def count_change(self, summary: ScanSummary, scan_marker: str, changed: bool) -> None:
        row = self.existing
        # Bij een eerdere scanner kan de eerste waarneming al vastliggen zonder cursor.
        if (row is None or (row["gescand_op"] == scan_marker
                            and (row["eerste_gezien"] or "") >= scan_marker)):
            summary.nieuw += 1
        elif changed:
            summary.gewijzigd += 1


def _file_state(
    connection: sqlite3.Connection, path: Path, stat: os.stat_result, profile_fingerprint: str,
) -> _FileState:
    existing = connection.execute(
        f"""
        SELECT id,grootte,mtime,nlink,device,inode,categorie,probe,aanwezig,
               profiel_fingerprint,scan_status,gescand_op,eerste_gezien,
               EXISTS(SELECT 1 FROM job WHERE bestand_id=bestand.id AND status IN {ACTIVE_SQL})
                   AS actief
        FROM bestand WHERE pad=?
        """,
        (str(path),),
    ).fetchone()
    metadata_unchanged = bool(
        existing is not None
        and int(existing["grootte"]) == stat.st_size
        and float(existing["mtime"]) == stat.st_mtime
        and int(existing["nlink"]) == stat.st_nlink
        and (existing["device"] is None or int(existing["device"]) == stat.st_dev)
        and (existing["inode"] is None or int(existing["inode"]) == stat.st_ino)
        and int(existing["aanwezig"])
    )
    profile_unchanged = bool(
        existing is not None and existing["profiel_fingerprint"] == profile_fingerprint
    )

    return _FileState(existing, metadata_unchanged, profile_unchanged)


def _store_without_probe(
    connection: sqlite3.Connection, state: _FileState, library_id: int, path: Path,
    stat: os.stat_result, profile_fingerprint: str, scan_marker: str, dry_run: bool,
    summary: ScanSummary,
) -> bool:
    existing = state.existing
    if existing is not None and state.active:
        if not dry_run:
            connection.execute(
                "UPDATE bestand SET gescand_op=?,aanwezig=1 WHERE id=?",
                (scan_marker, int(existing["id"])),
            )
        summary.gevonden += 1
        state.count_change(summary, scan_marker, changed=False)
        summary.items.append(
            ScanItem(
                pad=str(path),
                categorie=str(existing["categorie"]),
                filter_reden="actieve job",
                actie="overslaan",
                besluit_reden="actieve job blijft eigenaar van dit bronpad",
                nlink=stat.st_nlink,
            )
        )
        return True

    if existing is not None and state.reuse_decision(dry_run):
        latest = connection.execute(
            "SELECT actie,reden FROM besluit WHERE bestand_id=? ORDER BY id DESC LIMIT 1",
            (int(existing["id"]),),
        ).fetchone()
        connection.execute(
            """
            UPDATE bestand
            SET gescand_op=?,aanwezig=1,scan_status='gereed',device=?,inode=?
            WHERE id=?
            """,
            (scan_marker, stat.st_dev, stat.st_ino, int(existing["id"])),
        )
        summary.gevonden += 1
        state.count_change(summary, scan_marker, changed=False)
        summary.items.append(
            ScanItem(
                pad=str(path),
                categorie=str(existing["categorie"]),
                filter_reden="ongewijzigd",
                actie=str(latest["actie"] if latest is not None else "overslaan"),
                besluit_reden=str(
                    latest["reden"] if latest is not None else "ongewijzigd catalogusbestand"
                ),
                nlink=stat.st_nlink,
            )
        )
        return True

    if time.time() - stat.st_mtime < FILE_STABILITY_SECONDS:
        _store_deferred_file(
            connection,
            library_id,
            path,
            stat,
            existing,
            profile_fingerprint,
            scan_marker,
            dry_run,
        )
        summary.gevonden += 1
        summary.uitgesteld += 1
        state.count_change(summary, scan_marker, changed=not state.metadata_unchanged)
        summary.items.append(
            ScanItem(
                pad=str(path),
                categorie=str(existing["categorie"] if existing is not None else "wacht"),
                filter_reden="bestand is minder dan 60 minuten stabiel",
                actie="overslaan",
                besluit_reden="wacht op stabiliteitsvenster",
                nlink=stat.st_nlink,
            )
        )
        return True

    return False


@dataclass(slots=True)
class _Analysis:
    category: str
    filter_reason: str
    probe_data: dict[str, Any] | None
    after: os.stat_result
    decision: Besluit | None


def _analyse_file(
    state: _FileState, path: Path, stat: os.stat_result, profile: dict[str, Any],
    root: Path,
) -> _Analysis:
    existing = state.existing
    # Duurmeting, mediaprobe en bestandsanalyse kunnen minuten duren.
    ok, filter_reason, category = is_hoofdbestand(
        str(path),
        float(profile.get("min_duur_sec", 0)),
        basis=str(root),
    )
    del ok
    if (
        state.metadata_unchanged
        and existing is not None
        and existing["scan_status"] == "gereed"
        and existing["probe"]
    ):
        parsed_probe = json.loads(str(existing["probe"]))
        probe_data = parsed_probe if isinstance(parsed_probe, dict) else None
    else:
        probe_data = probe(str(path)) if category in {"ok", "extra"} else None

    after = path.stat()
    changed_during_probe = (
        after.st_dev,
        after.st_ino,
        after.st_size,
        after.st_mtime,
        after.st_nlink,
    ) != (stat.st_dev, stat.st_ino, stat.st_size, stat.st_mtime, stat.st_nlink)
    decision = None if changed_during_probe else decide_catalog_item(
        str(path),
        category,
        probe_data,
        profile,
        stat.st_nlink,
        library_root=str(root),
    )

    return _Analysis(category, filter_reason, probe_data, after, decision)


def _store_analysis(
    connection: sqlite3.Connection, state: _FileState, analysis: _Analysis, library_id: int,
    path: Path, stat: os.stat_result, profile_fingerprint: str, scan_marker: str,
    dry_run: bool, summary: ScanSummary,
) -> None:
    existing = state.existing
    category, after, decision = analysis.category, analysis.after, analysis.decision
    if decision is None:
        _store_deferred_file(
            connection,
            library_id,
            path,
            after,
            existing,
            profile_fingerprint,
            scan_marker,
            dry_run,
        )
        summary.gevonden += 1
        summary.uitgesteld += 1
        state.count_change(summary, scan_marker, changed=True)
        summary.items.append(
            ScanItem(
                pad=str(path),
                categorie=category,
                filter_reden="bestand veranderde tijdens analyse",
                actie="overslaan",
                besluit_reden="wacht tot kopie of download klaar is",
                nlink=after.st_nlink,
            )
        )
        return

    summary.gevonden += 1
    summary.items.append(
        ScanItem(
            pad=str(path),
            categorie=category,
            filter_reden=analysis.filter_reason,
            actie=decision.actie,
            besluit_reden=decision.reden,
            nlink=stat.st_nlink,
        )
    )
    if dry_run:
        return
    probe_json = (json.dumps(analysis.probe_data, ensure_ascii=False)
                  if analysis.probe_data is not None else None)
    changed = bool(
        existing is None
        or existing["scan_status"] != "gereed"
        or not (state.metadata_unchanged and state.profile_unchanged)
    )
    now = datetime.now(UTC).isoformat()
    if existing is None:
        cursor = connection.execute(
            """
            INSERT INTO bestand(
                library_id,pad,grootte,mtime,nlink,categorie,probe,gescand_op,
                device,inode,aanwezig,eerste_gezien,stabiel_vanaf,scan_status,
                profiel_fingerprint
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 1, ?, ?, 'gereed', ?)
            """,
            (
                library_id,
                str(path),
                stat.st_size,
                stat.st_mtime,
                stat.st_nlink,
                category,
                probe_json,
                scan_marker,
                stat.st_dev,
                stat.st_ino,
                now,
                now,
                profile_fingerprint,
            ),
        )
        if cursor.lastrowid is None:
            raise sqlite3.DatabaseError("Nieuw catalogusbestand heeft geen id")
        bestand_id = cursor.lastrowid
    else:
        bestand_id = int(existing["id"])
        connection.execute(
            """
            UPDATE bestand
            SET grootte=?,mtime=?,nlink=?,categorie=?,probe=?,gescand_op=?,device=?,inode=?,
                aanwezig=1,stabiel_vanaf=?,scan_status='gereed',profiel_fingerprint=?
            WHERE id=?
            """,
            (
                stat.st_size,
                stat.st_mtime,
                stat.st_nlink,
                category,
                probe_json,
                scan_marker,
                stat.st_dev,
                stat.st_ino,
                now,
                profile_fingerprint,
                bestand_id,
            ),
        )
    state.count_change(summary, scan_marker, changed)
    replay = (existing is not None and existing["gescand_op"] == scan_marker
              and state.metadata_unchanged)
    store_decision(connection, bestand_id, decision, changed,
                   replay_since=scan_marker if replay else None)


def _process_file(
    database: Database, library_id: int, path: Path, stat: os.stat_result,
    profile: dict[str, Any], profile_fingerprint: str, scan_marker: str, dry_run: bool,
    summary: ScanSummary, root: Path, bijdragen: _ScanBijdragen | None,
) -> None:
    while True:
        with database.connection(write=False) as conn:
            state = _file_state(conn, path, stat, profile_fingerprint)
        analysis = None
        if (not state.active and not state.reuse_decision(dry_run)
                and time.time() - stat.st_mtime >= FILE_STABILITY_SECONDS):
            analysis = _analyse_file(state, path, stat, profile, root)

        with database.connection(write=not dry_run) as conn, conn:
            # Een job kan het bestand tijdens de probe claimen of vrijgeven.
            state = _file_state(conn, path, stat, profile_fingerprint)
            voor = (summary.nieuw, summary.gewijzigd)
            if not _store_without_probe(conn, state, library_id, path, stat, profile_fingerprint,
                                        scan_marker, dry_run, summary):
                if analysis is None:
                    continue  # Inmiddels is een probe nodig. Laat het schrijfslot eerst los.
                _store_analysis(conn, state, analysis, library_id, path, stat, profile_fingerprint,
                                scan_marker, dry_run, summary)
            if bijdragen is not None:
                tijdens_probe_gewijzigd = analysis is not None and analysis.decision is None
                bijdragen.registreer(
                    conn, path, summary, voor,
                    bron_gewijzigd=not state.metadata_unchanged or tijdens_probe_gewijzigd,
                )
                conn.execute(
                    "UPDATE scan_run SET gevonden=?,nieuw=?,gewijzigd=?,uitgesteld=? WHERE id=?",
                    (summary.gevonden, summary.nieuw, summary.gewijzigd,
                     summary.uitgesteld, bijdragen.scan_id),
                )
                conn.execute("UPDATE library SET scan_cursor=? WHERE id=?", (str(path), library_id))
            return


def _store_deferred_file(
    connection: Any,
    library_id: int,
    path: Path,
    stat: os.stat_result,
    existing: Any | None,
    profile_fingerprint: str,
    scan_marker: str,
    dry_run: bool,
) -> None:
    """Bewaar alleen observaties; een onstabiel bestand krijgt nooit een besluit."""

    if dry_run:
        return
    now = datetime.now(UTC).isoformat()
    if existing is None:
        connection.execute(
            """
            INSERT INTO bestand(
                library_id,pad,grootte,mtime,nlink,categorie,probe,gescand_op,
                device,inode,aanwezig,eerste_gezien,scan_status,profiel_fingerprint
            ) VALUES (?, ?, ?, ?, ?, 'wacht', NULL, ?, ?, ?, 1, ?,
                      'wacht_op_stabiliteit', ?)
            """,
            (
                library_id,
                str(path),
                stat.st_size,
                stat.st_mtime,
                stat.st_nlink,
                scan_marker,
                stat.st_dev,
                stat.st_ino,
                now,
                profile_fingerprint,
            ),
        )
        return
    bestand_id = int(existing["id"])
    connection.execute(
        """
        UPDATE bestand
        SET grootte=?,mtime=?,nlink=?,device=?,inode=?,gescand_op=?,aanwezig=1,
            stabiel_vanaf=NULL,scan_status='wacht_op_stabiliteit',profiel_fingerprint=?
        WHERE id=?
        """,
        (
            stat.st_size,
            stat.st_mtime,
            stat.st_nlink,
            stat.st_dev,
            stat.st_ino,
            scan_marker,
            profile_fingerprint,
            bestand_id,
        ),
    )
    connection.execute(
        """
        UPDATE besluit
        SET status='afgewezen',reden=reden || '; bron veranderde en wacht op stabiliteit'
        WHERE bestand_id=? AND status='nieuw'
        """,
        (bestand_id,),
    )
