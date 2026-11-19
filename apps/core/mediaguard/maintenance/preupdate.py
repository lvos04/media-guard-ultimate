"""Back-ups van vóór een update: één set per update, terug te zetten vanuit de app.

Een set is een map ``/config/core/backups/voor-update/voor-update-<tijd>/`` met:

- ``mediaguard.db``: consistente kopie van de core-database;
- ``encode/<naam>.db``: de database(s) van de AV1-engine (alleen in de AIO);
- een manifest met versies, schema's, checksums en de hersteltest. Sets met een
  historische sleutelverwijzing hebben een eigen manifestnaam voor de oudere lezers.

In de alles-in-één-container maakt de supervisor de set vóórdat er één dienst start, en
alleen als core of de AV1-engine een databasemigratie heeft openstaan. Zo horen de twee
databases bij elkaar en is geen van beide al gemigreerd. Zonder supervisor maakt core zelf
een set met de databases die voor hem beschikbaar zijn.

Terugzetten gebeurt in twee stappen, omdat een draaiende dienst zijn database open heeft:

1. de app zet de kopieën klaar naast de echte databases (``*.terugzetten``) en schrijft
   ``terugzetten.json``;
2. de supervisor wisselt ze om zodra alle diensten gestopt zijn (bij het stoppen van de app,
   of anders bij de volgende start). Dat is alleen hernoemen: binnen een seconde klaar. De
   databases van daarvoor worden nooit weggegooid maar verhuizen naar
   ``backups/voor-terugzetten/<tijd>/``.

Deze module gebruikt alleen de standaardbibliotheek: de supervisor importeert hem vóór
core draait.
"""

from __future__ import annotations

import contextlib
import hashlib
import json
import os
import re
import shutil
import sqlite3
import stat
import tempfile
import time
from collections.abc import Iterator, Mapping
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any
from uuid import uuid4

from mediaguard import event_keys, schema
from mediaguard.db import MIGRATIONS_DIR, Database, check_schema_versions
from mediaguard.maintenance.sqlite_recovery import checked_database, database_files, wal_prefix_hash

FOLDER = "voor-update"
SET_PREFIX = "voor-update-"
ASIDE_FOLDER = "voor-terugzetten"
STAGE_MARKER = "terugzetten.json"
RESULT_FILE = "terugzetten-resultaat.json"
UPGRADE_SET = "update-herstelset"
COMMIT_MARKER = "update-in-uitvoering"
COMMIT_STEP = "update-commitstap.json"
COMMIT_ORIGIN = "update-oorsprong.json"
LEGACY_KEY_META = "meta-met-sleutelverwijzing.json"
STAGED_SUFFIX = ".terugzetten"
KEEP_SETS = 5
PARTIAL_GRACE_S = 3600
STAGE_MAX_AGE_S = 24 * 3600  # daarna niet meer omwisselen (geen verrassing weken later)
_SET_NAME = re.compile(r"^voor-update-\d{8}T\d{6}Z(?:-\d+|-[0-9]{6}-[a-f0-9]{32})?$")
_LEGACY_NAME = re.compile(r"^mediaguard-[\w.-]+-naar-[\w.-]+-\d{8}T\d{6}Z\.db$")
_ENC_VERSION = re.compile(r"^(\d+)_")
_ASIDE_NAME = re.compile(r"[0-9]{8}T[0-9]{6}Z(?:-[0-9]+)?")
_SQLITE_TAILS = ("", "-wal", "-shm", "-journal")


class SetError(Exception):
    def __init__(self, message: str, *, disk: bool = False) -> None:
        super().__init__(message)
        self.disk = disk


class _SourceChanged(SetError):
    """Vóór de migratie kan de supervisor een nieuwe volledige set maken."""


class _PreviousDataError(SetError):
    """De oorspronkelijke SQLite-bestanden moeten voor nader herstel bewaard blijven."""


def _now() -> str:
    return datetime.now(UTC).isoformat(timespec="seconds")


def _sha256(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def _sync(path: Path) -> None:
    fd = os.open(path, os.O_RDONLY | os.O_NOFOLLOW)
    try:
        os.fsync(fd)
    finally:
        os.close(fd)


def _publish(path: Path, contents: str, owner: tuple[int, int] | None = None) -> None:
    """Publiceer een complete markering onder het mapslot, ook na stroomuitval."""
    if not contents:
        raise SetError("een lege markering kan niet worden gepubliceerd")
    with tempfile.NamedTemporaryFile(dir=path.parent, mode="w", encoding="utf-8",
                                     prefix=path.name + ".", suffix=".part",
                                     delete=False) as handle:
        tmp = Path(handle.name)
        try:
            handle.write(contents)
            handle.flush()
            if owner is not None and os.geteuid() == 0:
                os.fchown(handle.fileno(), *owner)
            os.fsync(handle.fileno())
            os.replace(tmp, path)
            _sync(path.parent)
        finally:
            tmp.unlink(missing_ok=True)


def _forget(path: Path) -> None:
    path.unlink()
    _sync(path.parent)


def _clean_marker_parts(core: Path) -> None:
    # Alleen ongepubliceerde markeringen van deze schrijver, onder beide opstartsloten.
    for name in (COMMIT_MARKER, COMMIT_STEP, COMMIT_ORIGIN, UPGRADE_SET, STAGE_MARKER, RESULT_FILE):
        for part in core.glob(name + ".????????.part"):
            if stat.S_ISREG(part.lstat().st_mode):
                _forget(part)


def _chown(paths: list[Path], owner: tuple[int, int] | None) -> None:
    if owner is None or os.geteuid() != 0:
        return
    for p in paths:
        with contextlib.suppress(OSError):
            os.chown(p, owner[0], owner[1])


def _cursor_value(db: sqlite3.Connection) -> object:
    if db.execute("SELECT 1 FROM pragma_table_info('outbox_retention') "
                  "WHERE name='cursor_key'").fetchone():
        row = db.execute("SELECT cursor_key FROM outbox_retention WHERE singleton=1").fetchone()
        if row:
            return row[0]
    return None


def _copy_legacy(source: sqlite3.Connection, core_dir: Path, dest: Path, value: bytes) -> str:
    # De onbewerkte pagina's mogen ook bij een onderbroken kopie uitsluitend in de
    # geheimenmap staan. De live verbinding blijft in dezelfde leestransactie.
    ref = event_keys.store_legacy(core_dir, value)
    with tempfile.TemporaryDirectory(dir=event_keys.key_path(core_dir).parent) as work:
        private = Path(work) / "snapshot.db"
        with contextlib.closing(sqlite3.connect(private)) as tmp:
            source.backup(tmp)
            tmp.execute("PRAGMA journal_mode=DELETE")
            tmp.execute("PRAGMA secure_delete=ON")
            with tmp:
                tmp.execute("UPDATE outbox_retention SET cursor_key=? WHERE singleton=1", (ref,))
        # Ook ongebruikte pagina's van een oudere SQLite-versie kunnen de waarde bevatten.
        # Publiceer in dat geval niets. De oorspronkelijke database blijft beschikbaar.
        tail = b""
        with private.open("rb") as handle:
            for chunk in iter(lambda: handle.read(1 << 20), b""):
                block = tail + chunk
                if value in block:
                    raise SetError("de historische cursorsleutel is nog aanwezig in de kopie")
                tail = block[-(len(value) - 1):]
        shutil.copyfile(private, dest)
    return ref


def _copy_sqlite(src: Path, dest: Path) -> dict[str, Any]:
    """Kopie via de backup-API (WAL-veilig) + hersteltest; nooit een half bestand."""
    part = dest.with_name(dest.name + ".part")
    try:
        extra = {}
        with contextlib.closing(sqlite3.connect(src.resolve().as_uri() + "?mode=ro", uri=True,
                                               timeout=30)) as s:
            s.execute("BEGIN")
            core = dest.name == "mediaguard.db" and dest.parent.name != "encode"
            value = _cursor_value(s) if core else None
            if isinstance(value, str) and value.startswith(event_keys.LEGACY_PREFIX):
                # Ook een door een eerdere beheerroute rechtstreeks teruggezette verwijzing
                # moet met de oorspronkelijke sleutel terug te zetten zijn.
                value = event_keys.load_legacy(src.parent, value)
            if isinstance(value, bytes):
                extra["legacy_cursor_key"] = _copy_legacy(s, src.parent, part, value)
            else:
                with contextlib.closing(sqlite3.connect(part)) as d:
                    s.backup(d)
        # De SQLite-back-up is gesloten en staat op een uniek tijdelijk pad.
        check = sqlite3.connect(f"{part.resolve().as_uri()}?mode=ro&immutable=1", uri=True)
        try:
            ok = check.execute("PRAGMA integrity_check").fetchone()[0] == "ok"
        finally:
            check.close()
        if not ok:
            raise SetError(f"kopie van {src.name} is niet intact")
        os.replace(part, dest)
    except OSError as exc:
        part.unlink(missing_ok=True)
        raise SetError(f"kopie van {src.name} mislukt: {exc}", disk=True) from exc
    except sqlite3.Error as exc:
        part.unlink(missing_ok=True)
        disk = "disk is full" in str(exc) or "unable to open" in str(exc)
        raise SetError(f"kopie van {src.name} mislukt: {exc}", disk=disk) from exc
    except ValueError as exc:
        part.unlink(missing_ok=True)
        raise SetError("historische cursorsleutel kan niet veilig worden bewaard") from exc
    except SetError:
        part.unlink(missing_ok=True)
        raise
    return {"sha256": _sha256(dest), "size": dest.stat().st_size, **extra}


def _file_generation(path: Path) -> list[list[int]]:
    # De gegevens staan in het hoofdbestand en eventueel de WAL. Het gedeelde
    # lezersregister (-shm) en een lege WAL bevatten geen databasewijzigingen.
    main = list(schema._generation(path))
    try:
        wal = list(schema._generation(path.with_name(path.name + "-wal")))
    except FileNotFoundError:
        wal = []
    return [main, wal if wal and wal[2] else []]


def _source_generation(core_db: Path, encode_dir: Path | None) -> dict[str, list[list[int]]]:
    stand = {"mediaguard.db": _file_generation(core_db)}
    if encode_dir is not None:
        for path in sorted(encode_dir.glob("*.db")):
            stand[f"encode/{path.name}"] = _file_generation(path)
    return stand


def claim_snapshot(core_dir: Path, archive: Path) -> bool:
    """De losse kern kan alleen de ongebruikte set van de laatste supervisorstart overnemen."""
    marker = core_dir / UPGRADE_SET
    try:
        name = marker.read_text(encoding="ascii")
        _forget(marker)
    except FileNotFoundError:
        return False
    return name == archive.name


def _check_archive_access(path: Path, owner: tuple[int, int] | None = None) -> None:
    expected = owner or (os.geteuid(), os.getegid())
    for entry in (path, *path.rglob("*")):
        info = entry.lstat()
        mode = 0o700 if stat.S_ISDIR(info.st_mode) else 0o600
        if ((info.st_uid, info.st_gid) != expected or stat.S_IMODE(info.st_mode) != mode
                or (mode == 0o600 and (not stat.S_ISREG(info.st_mode) or info.st_nlink != 1))):
            raise SetError("de eigenaar of toegangsrechten van de herstelset kloppen niet")


def matches_sources(path: Path, core_db: Path, current: str | None, target: str | None) -> bool:
    """Controleer onder beide mapsloten de volledige set en alle live databases met WAL."""
    if path.parent != core_db.parent / "backups" / FOLDER or not _SET_NAME.fullmatch(path.name):
        return False
    meta = _read_meta(path) or {}
    stand = _source_generation(core_db, schema.encoder_dir(core_db.parent))
    if (meta.get("name"), meta.get("reason"), meta.get("from_schema"), meta.get("to_schema"),
        meta.get("source_generation")) != (path.name, "update", current, target, stand):
        return False
    entries = _set_files(core_db.parent, path)
    if {p.relative_to(path).as_posix() for p, _, _ in entries.values()} != stand.keys():
        raise SetError("de herstelset bevat niet alle actuele databases")
    if problems := verify(path):
        raise SetError(", ".join(problems))
    _check_archive_access(path)
    return True


def make_set(folder: Path, core_db: Path, *, encode_dir: Path | None,
             from_schema: str | None = None, to_schema: str | None = None,
             from_version: str | None = None, to_version: str | None = None,
             reason: str = "migratie", owner: tuple[int, int] | None = None,
             preserve: Path | None = None) -> Path:
    """Maak een complete set (eerst als ``.part``-map, pas daarna de echte naam)."""
    peer = encode_dir or schema.encoder_dir(core_db.parent)
    with schema.startup_locks(core_db.parent, peer):
        Database(core_db).require_writable()
        if peer != schema.encoder_dir(core_db.parent):
            check_encoder_schema(peer)
        return _make_set(folder, core_db, encode_dir=encode_dir, from_schema=from_schema,
                         to_schema=to_schema, from_version=from_version, to_version=to_version,
                         reason=reason, owner=owner, preserve=preserve)


def _make_set(folder: Path, core_db: Path, *, encode_dir: Path | None,
              from_schema: str | None, to_schema: str | None, from_version: str | None,
              to_version: str | None, reason: str, owner: tuple[int, int] | None,
              preserve: Path | None = None) -> Path:
    folder.mkdir(parents=True, exist_ok=True)
    _chown([folder.parent, folder], owner)
    stamp = datetime.now(UTC)
    # Oudere supervisors accepteren na de seconde alleen één decimaal achtervoegsel.
    name = f"{SET_PREFIX}{stamp:%Y%m%dT%H%M%SZ-%f}{uuid4().int:039d}"
    work = folder / (name + ".part")
    work.mkdir(mode=0o700)
    files: list[dict[str, Any]] = []
    try:
        generation = _source_generation(core_db, encode_dir) if reason == "update" else None
        files.append({"name": "mediaguard.db", "live": f"{core_db.parent.name}/{core_db.name}",
                      **_copy_sqlite(core_db, work / "mediaguard.db")})
        encoder_problem = None
        if encode_dir is not None and encode_dir.is_dir():
            (work / "encode").mkdir(mode=0o700)
            for db in sorted(encode_dir.glob("*.db")):
                try:
                    files.append({"name": f"encode/{db.name}", "live": f"encode/{db.name}",
                                  **_copy_sqlite(db, work / "encode" / db.name)})
                except SetError as exc:
                    encoder_problem = str(exc)
                    if exc.disk:
                        raise
        meta = {"kind": "pre-update", "name": name, "reason": reason,
                "created_at": stamp.isoformat(timespec="microseconds"),
                "from_version": from_version, "to_version": to_version,
                "from_schema": from_schema, "to_schema": to_schema, "files": files,
                "encoder_included": any(f["name"].startswith("encode/") for f in files),
                "encoder_problem": encoder_problem, "integrity_ok": True}
        if generation is not None:
            if generation != _source_generation(core_db, encode_dir):
                raise _SourceChanged("de bronbestanden zijn tijdens het archiveren gewijzigd")
            meta["source_generation"] = generation
        manifest = LEGACY_KEY_META if any("legacy_cursor_key" in f for f in files) else "meta.json"
        (work / manifest).write_text(json.dumps(meta, indent=2), encoding="utf-8")
        if reason == "update":
            for entry in work.rglob("*"):
                entry.chmod(0o700 if entry.is_dir() else 0o600)
            _chown([work, *work.rglob("*")], owner)
            _check_archive_access(work, owner)
            if encoder_problem or _read_meta(work) != meta:
                raise SetError("de herstelset voor de update is onvolledig")
            if problems := verify(work, core_dir=core_db.parent):
                raise SetError(", ".join(problems))
            for entry in sorted(work.rglob("*"), key=lambda p: len(p.parts), reverse=True):
                _sync(entry)
            _sync(work)
        os.replace(work, folder / name)
        if reason == "update":
            for directory in (folder, folder.parent, core_db.parent):
                _sync(directory)
    except BaseException:
        shutil.rmtree(work, ignore_errors=True)
        raise
    done = folder / name
    if reason != "update":
        _chown([done, *done.rglob("*")], owner)
    protected = {done}
    if preserve is not None:
        protected.add(preserve)
    prune_sets(folder, protected=protected)
    if reason == "update":
        _check_archive_access(done, owner)
        if _read_meta(done) != meta:
            raise SetError("de gepubliceerde herstelset voor deze update ontbreekt of is gewijzigd")
        if problems := verify(done, core_dir=core_db.parent):
            raise SetError(", ".join(problems))
    return done


def prune_sets(folder: Path, keep: int = KEEP_SETS, now: float | None = None,
               *, protected: set[Path] | None = None) -> int:
    """Alleen complete sets tellen mee; halve ``.part``-mappen verdwijnen na een uur."""
    if folder.name == FOLDER and (folder.parent.parent / COMMIT_MARKER).exists():
        return 0  # Bewaar ook strijdige sets totdat de onderbroken update is afgehandeld.
    now = time.time() if now is None else now
    removed = 0
    complete = sorted(p for p in folder.glob(SET_PREFIX + "*")
                      if p.is_dir() and _SET_NAME.fullmatch(p.name) and _read_meta(p) is not None)
    removable = [p for p in complete if p not in (protected or set())]
    for old in removable[:max(0, len(complete) - keep)]:
        shutil.rmtree(old, ignore_errors=True)
        removed += 1
    for part in folder.glob(SET_PREFIX + "*.part"):
        with contextlib.suppress(OSError):
            if now - part.stat().st_mtime > PARTIAL_GRACE_S:
                shutil.rmtree(part, ignore_errors=True)
                removed += 1
    # oude losse kopieën van vóór deze versie (één bestand, alleen core): hooguit 5
    for old in sorted(p for p in folder.glob("mediaguard-*.db")
                      if _LEGACY_NAME.match(p.name))[:-keep]:
        old.unlink(missing_ok=True)
        removed += 1
    return removed


# -- lezen ------------------------------------------------------------------------------------
def _read_meta(set_dir: Path) -> dict[str, Any] | None:
    source = set_dir / LEGACY_KEY_META
    try:
        try:
            data = source.read_text(encoding="utf-8")
        except FileNotFoundError:
            data = (set_dir / "meta.json").read_text(encoding="utf-8")
        meta = json.loads(data)
    except (OSError, ValueError):
        return None
    return meta if isinstance(meta, dict) else None


def _protect_old_manifests(folder: Path) -> None:
    """Verberg ook eerdere sets met verwijzingen voor een beheerder van schema 155."""
    for manifest in folder.glob(SET_PREFIX + "*/meta.json"):
        if not _SET_NAME.fullmatch(manifest.parent.name):
            continue
        meta = _read_meta(manifest.parent) or {}
        if any(isinstance(f, dict) and "legacy_cursor_key" in f for f in meta.get("files", [])):
            target = manifest.with_name(LEGACY_KEY_META)
            if target.exists():
                raise SetError("de herstelset heeft twee verschillende manifesten")
            manifest.rename(target)
            _sync(target.parent)


def list_sets(folder: Path) -> list[dict[str, Any]]:
    out: list[dict[str, Any]] = []
    if not folder.is_dir():
        return out
    for p in folder.iterdir():
        if p.is_dir() and _SET_NAME.match(p.name):
            meta = _read_meta(p)
            if meta is None:
                continue
            size = sum(int(f.get("size") or 0) for f in meta.get("files") or [])
            out.append({**meta, "name": p.name, "size": size, "legacy": False})
        elif p.is_file() and _LEGACY_NAME.match(p.name):
            m = re.match(r"^mediaguard-(.+)-naar-(.+)-(\d{8}T\d{6}Z)\.db$", p.name)
            created = datetime.strptime(m.group(3), "%Y%m%dT%H%M%SZ").replace(
                tzinfo=UTC).isoformat(timespec="seconds") if m else None
            out.append({"kind": "pre-update", "name": p.name, "created_at": created,
                        "from_schema": m.group(1) if m else None,
                        "to_schema": m.group(2) if m else None, "from_version": None,
                        "to_version": None, "size": p.stat().st_size,
                        "encoder_included": False, "integrity_ok": None, "legacy": True,
                        "files": [{"name": p.name, "live": "core/mediaguard.db"}]})
    out.sort(key=lambda r: str(r.get("created_at") or ""), reverse=True)
    return out


def is_set_name(name: str) -> bool:
    return bool(_SET_NAME.match(name) or _LEGACY_NAME.match(name))


def find(folder: Path, name: str) -> Path | None:
    if not is_set_name(name):
        return None
    path = folder / name
    return path if path.exists() else None


def verify(path: Path, *, core_dir: Path | None = None) -> list[str]:
    """Problemen (leeg = in orde): checksums en grootte van elk bestand in de set."""
    if path.is_file():  # oude losse kopie: alleen een snelle controle mogelijk
        try:
            conn = sqlite3.connect(f"file:{path}?mode=ro", uri=True)
            try:
                ok = conn.execute("PRAGMA quick_check").fetchone()[0] == "ok"
            finally:
                conn.close()
        except sqlite3.Error as exc:
            return [f"{path.name}: {exc}"]
        return [] if ok else [f"{path.name} is beschadigd"]
    meta = _read_meta(path)
    if meta is None:
        return ["meta.json ontbreekt of is onleesbaar"]
    problems = []
    for f in meta.get("files") or []:
        file = path / str(f.get("name"))
        if not file.is_file():
            problems.append(f"{f.get('name')} ontbreekt")
        elif file.stat().st_size != int(f.get("size") or -1) or _sha256(file) != f.get("sha256"):
            problems.append(f"{f.get('name')} is beschadigd (checksum klopt niet)")
        if "legacy_cursor_key" in f:
            try:
                folder = core_dir or path.parent.parent.parent
                event_keys.load_legacy(folder, f["legacy_cursor_key"])
            except (TypeError, ValueError, OSError):
                problems.append("historische cursorsleutel ontbreekt of is beschadigd")
    return problems


# -- terugzetten ---------------------------------------------------------------------------
def _within(root: Path, rel: str) -> Path:
    target = (root / rel).resolve()
    if root.resolve() not in target.parents:
        raise SetError(f"ongeldig pad in set: {rel}")
    return target


def _live_name(core_dir: Path, rel: str) -> bool:
    parts = Path(rel).parts
    return rel == f"{core_dir.name}/mediaguard.db" or (
        len(parts) == 2 and parts[0] == "encode" and parts[1].endswith(".db")
        and rel == f"encode/{parts[1]}"
    )


def _set_files(core_dir: Path, path: Path) -> dict[str, tuple[Path, int, str]]:
    if path.is_file():
        return {f"{core_dir.name}/mediaguard.db": (path, path.stat().st_size, _sha256(path))}
    meta = _read_meta(path) or {}
    files = meta.get("files")
    if not isinstance(files, list) or meta.get("encoder_problem"):
        raise SetError("meta.json ontbreekt of de back-upset is onvolledig")
    entries: dict[str, tuple[Path, int, str]] = {}
    for entry in files:
        if not isinstance(entry, dict):
            raise SetError("ongeldige bestandenlijst in de back-upset")
        rel, name = entry.get("live"), entry.get("name")
        if not isinstance(rel, str) or not _live_name(core_dir, rel) or rel in entries:
            raise SetError("ongeldig databasepad in de back-upset")
        expected = Path(rel).name if rel.startswith(f"{core_dir.name}/") else rel
        size, digest = entry.get("size"), entry.get("sha256")
        if name != expected or not isinstance(size, int) or not isinstance(digest, str):
            raise SetError("ongeldige metadata in de back-upset")
        entries[rel] = (path / expected, size, digest)
    if f"{core_dir.name}/mediaguard.db" not in entries:
        raise SetError("MediaGuard-database ontbreekt in de back-upset")
    if bool(meta.get("encoder_included")) != any(rel.startswith("encode/") for rel in entries):
        raise SetError("AV1-database ontbreekt in de back-upset")
    return entries


def _check_restore_copy(path: Path, rel: str, size: int, digest: str,
                        av1_migrations: Path, work: Path) -> None:
    try:
        if not path.is_file():
            raise SetError(f"klaargezette kopie ontbreekt: {path.name}")
        if path.is_symlink() or path.stat().st_size != size or _sha256(path) != digest:
            raise SetError(f"{path.name} ontbreekt of is beschadigd (checksum klopt niet)")
        # Een eerdere release kon bij het lezen een lege WAL en een SHM achterlaten.
        # Alleen het gecontroleerde hoofdbestand wordt klaargezet en teruggezet.
        for suffix in ("-wal", "-shm", "-journal"):
            try:
                info = path.with_name(path.name + suffix).lstat()
            except FileNotFoundError:
                continue
            if (not stat.S_ISREG(info.st_mode) or suffix == "-journal"
                    or (suffix == "-wal" and info.st_size)):
                raise SetError(f"{path.name} heeft een ongecontroleerd SQLite-journaal")
        with checked_database(work, {"": path}) as (db, _):
            if rel.startswith("encode/"):
                have = schema.versions_in(db, "schema_versie", "versie")
                if have - _encoder_versions(av1_migrations):
                    raise schema.SchemaTooNew()
            else:
                check_schema_versions(path, connection=db)
    except (OSError, sqlite3.Error) as exc:
        raise SetError(f"{path.name} is onleesbaar: {exc}") from exc


def _checksum(value: object) -> str:
    return hashlib.sha256(json.dumps(value, sort_keys=True).encode()).hexdigest()


def _manifest(name: str, files: list[dict[str, Any]]) -> str:
    return _checksum([name, files])


def stage(core_dir: Path, path: Path, *, actor: str) -> dict[str, Any]:
    """Zet de kopieën klaar naast de echte databases; de supervisor wisselt ze om."""
    db = Database(core_dir / "mediaguard.db")
    with db.startup_lock():
        if swapping(core_dir):
            raise SetError("de begonnen omwisseling moet eerst worden hervat")
        if (core_dir / COMMIT_MARKER).exists():
            if os.environ.get(schema.AIO_RECOVERY_ENV):
                raise schema.SchemaTooNew()
            _core_pending(db.path, MIGRATIONS_DIR, core_dir)
            _encoder_pending(schema.encoder_dir(core_dir), encoder_migrations(os.environ),
                             work=core_dir)
        else:
            db.require_writable()
        return _stage(core_dir, path, actor)


def _stage(core_dir: Path, path: Path, actor: str) -> dict[str, Any]:
    config_root = core_dir.parent
    _protect_old_manifests(core_dir / "backups" / FOLDER)
    pairs = _set_files(core_dir, path)
    migrations = encoder_migrations(os.environ)
    for rel, (src, size, digest) in pairs.items():
        _check_restore_copy(src, rel, size, digest, migrations, core_dir)
    core_rel = f"{core_dir.name}/mediaguard.db"
    core_src = pairs[core_rel][0]
    with checked_database(core_dir, {"": core_src}) as (reader, _):
        ref = _cursor_value(reader)
    legacy = None
    if isinstance(ref, str) and ref.startswith(event_keys.LEGACY_PREFIX):
        try:
            legacy = event_keys.load_legacy(core_dir, ref)
        except (OSError, ValueError) as exc:
            raise SetError("de historische cursorsleutel is niet beschikbaar") from exc
    need = sum(size for _, size, _ in pairs.values())
    free = shutil.disk_usage(config_root).free
    if free < need * 1.1 + 256 * 1024**2:
        raise SetError(f"niet genoeg vrije ruimte in /config (nodig ±{need // 1024**2} MB)",
                       disk=True)
    cancel(core_dir)
    staged: list[dict[str, Any]] = []
    try:
        for live, (src, size, digest) in pairs.items():
            dest = _within(config_root, live + STAGED_SUFFIX)
            if dest != (config_root / (live + STAGED_SUFFIX)).absolute():
                raise SetError("ongeldig pad naar de klaargezette database")
            with tempfile.NamedTemporaryFile(dir=dest.parent, suffix=".part", delete=False) as out:
                part = Path(out.name)
            try:
                shutil.copyfile(src, part)
                # Controleer de kopie tegen de gekozen set vóór het herstellen van de sleutel
                # of het vrijgeven van een terugzetpaar voor handmatig herstel.
                _check_restore_copy(part, live, size, digest, migrations, core_dir)
                if live == core_rel and legacy is not None:
                    # Alleen de expliciet klaargezette database voor de oude lezer krijgt
                    # zijn sleutel terug. De markering dekt de uiteindelijke bytes.
                    with contextlib.closing(sqlite3.connect(part)) as old, old:
                        old.execute("UPDATE outbox_retention SET cursor_key=? WHERE singleton=1",
                                    (legacy,))
                    size, digest = part.stat().st_size, _sha256(part)
                    _check_restore_copy(part, live, size, digest, migrations, core_dir)
                _sync(part)
                os.replace(part, dest)
                _sync(dest.parent)
            finally:
                part.unlink(missing_ok=True)
            staged.append({"live": live, "staged": live + STAGED_SUFFIX,
                           "size": size, "sha256": digest})
    except (OSError, sqlite3.Error, SetError) as exc:
        for s in staged:
            with contextlib.suppress(OSError):
                _within(config_root, s["staged"]).unlink()
        raise SetError(f"klaarzetten mislukt: {exc}", disk=isinstance(exc, OSError)) from exc
    info = {"set": path.name, "requested_at": _now(), "by": actor, "files": staged,
            "manifest": _manifest(path.name, staged)}
    _publish(core_dir / STAGE_MARKER, json.dumps(info))
    return info


def _request(core_dir: Path) -> dict[str, Any] | None:
    try:
        info = json.loads((core_dir / STAGE_MARKER).read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None
    return info if isinstance(info, dict) else None


def pending(core_dir: Path) -> dict[str, Any] | None:
    info = _request(core_dir)
    if info is not None:
        info.pop("omwisseling", None)
    return info


def swapping(core_dir: Path) -> bool:
    info = _request(core_dir)
    if info is None:
        return False
    if "omwisseling" in info:
        return True
    root = core_dir / "backups" / ASIDE_FOLDER
    if not root.is_dir():
        return False
    try:
        pairs = _restore_pairs(core_dir, info)
        requested = datetime.fromisoformat(info["requested_at"])
        return bool(_legacy_asides(root, pairs, requested))
    except (KeyError, TypeError, ValueError, SetError):
        return False


def cancel(core_dir: Path) -> bool:
    with Database(core_dir / "mediaguard.db").startup_lock():
        return _cancel(core_dir)


def _cancel(core_dir: Path) -> bool:
    info = _request(core_dir)
    if info is None:
        return False
    if swapping(core_dir):
        raise SetError("de begonnen omwisseling moet eerst worden hervat")
    _forget(core_dir / STAGE_MARKER)
    files = info.get("files")
    for f in files if isinstance(files, list) else []:
        rel = f.get("live") if isinstance(f, dict) else None
        if isinstance(rel, str) and _live_name(core_dir, rel):
            with contextlib.suppress(OSError, SetError):
                staged_name = rel + STAGED_SUFFIX
                staged = core_dir.parent / staged_name
                if _within(core_dir.parent, staged_name) == staged.absolute():
                    staged.unlink()
    return True


def apply_staged(core_dir: Path, owner: tuple[int, int] | None = None,
                 max_age_s: float = STAGE_MAX_AGE_S, *,
                 av1_migrations: Path | None = None,
                 keep_on_error: bool = False) -> dict[str, Any] | None:
    """Alleen aanroepen als ALLE diensten gestopt zijn (supervisor). Hernoemt de huidige
    databases (met hun -wal/-shm) naar ``voor-terugzetten/<tijd>/`` en de klaargezette
    kopieën naar hun plek. Ontbreekt er iets, dan verandert er niets."""
    with Database(core_dir / "mediaguard.db").startup_lock():
        return _apply_staged(core_dir, owner, max_age_s,
                             av1_migrations or encoder_migrations(os.environ), keep_on_error)


@dataclass(frozen=True)
class _RestorePair:
    live: Path
    staged: Path
    rel: str
    size: int
    digest: str

    def original(self, tail: str) -> Path:
        return self.live.with_name(self.live.name + tail)

    def saved(self, aside: Path, tail: str) -> Path:
        return aside / (self.rel.replace("/", "__") + tail)


def _restore_pairs(core_dir: Path, info: dict[str, Any]) -> list[_RestorePair]:
    name, files = info.get("set"), info.get("files")
    if not isinstance(name, str) or not is_set_name(name) or not isinstance(files, list):
        raise SetError("ongeldige markering voor terugzetten")
    original = None
    if "manifest" not in info:
        # Markering van een vorige release: herleid de checksums uit de oorspronkelijke set.
        path = find(core_dir / "backups" / FOLDER, name)
        if path is None:
            raise SetError("oorspronkelijke back-upset voor de markering ontbreekt")
        original = _set_files(core_dir, path)
    elif info["manifest"] != _manifest(name, files):
        raise SetError("markering voor terugzetten is onvolledig of beschadigd")

    pairs = []
    seen = set()
    for entry in files:
        rel = entry.get("live") if isinstance(entry, dict) else None
        if (not isinstance(rel, str) or not _live_name(core_dir, rel) or rel in seen
                or entry.get("staged") != rel + STAGED_SUFFIX):
            raise SetError("ongeldig databasepad in markering voor terugzetten")
        seen.add(rel)
        live = core_dir.parent / rel
        staged = live.with_name(live.name + STAGED_SUFFIX)
        if (_within(core_dir.parent, rel) != live.absolute()
                or _within(core_dir.parent, rel + STAGED_SUFFIX) != staged.absolute()):
            raise SetError("ongeldig pad naar klaargezette database")
        if original is None:
            size, digest = entry.get("size"), entry.get("sha256")
        elif rel in original:
            _, size, digest = original[rel]
        else:
            raise SetError("ongeldige bestandenlijst in markering voor terugzetten")
        if not isinstance(size, int) or not isinstance(digest, str):
            raise SetError("ongeldige metadata in markering voor terugzetten")
        pairs.append(_RestorePair(live, staged, rel, size, digest))
    if f"{core_dir.name}/mediaguard.db" not in seen or (
        original is not None and seen != original.keys()
    ):
        raise SetError("klaargezette set is onvolledig")
    return pairs


def _regular_digest(path: Path) -> str:
    if not stat.S_ISREG(path.lstat().st_mode):
        raise _PreviousDataError(f"{path.name} is geen regulier bestand")
    return _sha256(path)


def _publish_step(marker: Path, info: dict, state: dict, owner: tuple[int, int] | None) -> None:
    checksum = _checksum([info, state])
    _publish(marker, json.dumps({**info, "omwisseling": {**state, "controle": checksum}}), owner)


def _legacy_asides(root: Path, pairs: list[_RestorePair], requested: datetime) -> list[Path]:
    stamp = requested.astimezone(UTC).strftime("%Y%m%dT%H%M%SZ")
    partial = [p for p in pairs if not p.live.exists() or not p.staged.exists()]
    if not partial:
        return []
    return [directory for directory in root.glob("*")
            if _ASIDE_NAME.fullmatch(directory.name) and directory.name[:16] >= stamp
            and not directory.is_symlink() and directory.is_dir()
            and any(p.saved(directory, "").is_file() for p in partial)]


def _begin_swap(root: Path, pairs: list[_RestorePair], requested: datetime,
                max_age_s: float) -> dict:
    # Oudere schrijvers lieten alleen de aanvraag en de hernoemde bestanden achter.
    # Neem uitsluitend één opzijgezette map over met een aaneengesloten reeks stappen.
    begun = any(not p.staged.exists() for p in pairs)
    candidates = _legacy_asides(root, pairs, requested)
    if len(candidates) > 1 or (begun and not candidates):
        raise SetError("klaargezette kopie ontbreekt of de vorige databases zijn niet eenduidig")
    if candidates:
        aside, = candidates
    else:
        age = (datetime.now(UTC) - requested).total_seconds()
        if not 0 <= age <= max_age_s:
            raise SetError("verlopen of ongeldig tijdstip; zet de back-up opnieuw klaar")
        aside = root / (datetime.now(UTC).strftime("%Y%m%dT%H%M%SZ-") + str(uuid4().int))

    originals = {}
    finished = []
    for p in pairs:
        installed = not p.staged.exists()
        if installed and not p.saved(aside, "").is_file():
            raise SetError("bijbehorende vorige database ontbreekt")
        for tail in _SQLITE_TAILS:
            saved = p.saved(aside, tail)
            if os.path.lexists(saved):
                originals[p.rel + tail] = _regular_digest(saved)
                finished.append(True)
            elif not installed and os.path.lexists(p.original(tail)):
                originals[p.rel + tail] = _regular_digest(p.original(tail))
                finished.append(False)
        finished.append(installed)
    step = finished.count(True)
    if any(finished[step:]):
        raise SetError("de bestanden horen niet bij één onderbroken omwisseling")
    return {"vorige": aside.name, "originelen": originals, "stap": step}


def _check_position(core: Path, pairs: list[_RestorePair], aside: Path, originals: dict,
                    moves: list[tuple[Path, Path]], step: int, migrations: Path) -> None:
    """Bewijs elke volledige database, ook tussen de rename van de kern en haar WAL."""
    moved = dict(moves[:step])
    expected = set()
    allowed = set()
    for pair in pairs:
        restored = moved.get(pair.staged, pair.staged)
        _check_restore_copy(restored, pair.rel, pair.size, pair.digest, migrations, core)
        expected.add(restored)
        allowed.add(pair.staged)
        family = {}
        for tail in _SQLITE_TAILS:
            live, saved = pair.original(tail), pair.saved(aside, tail)
            allowed.update((live, saved))
            if tail == "-shm":
                # Zowel de oude als de nieuwe lezer kan dit register opnieuw aanmaken.
                for shm in (live, saved):
                    if os.path.lexists(shm) and not stat.S_ISREG(shm.lstat().st_mode):
                        raise _PreviousDataError(f"{shm.name} is geen regulier lezersregister")
                    expected.add(shm)
                continue
            key = pair.rel + tail
            if key in originals:
                source = moved.get(live, live)
                if not source.is_file() or _regular_digest(source) != originals[key]:
                    raise _PreviousDataError(f"het oorspronkelijke bestand {source.name} "
                                             "ontbreekt of is gewijzigd")
                expected.add(source)
                family[tail] = source
        # Een geïnstalleerde kopie of een lezer van de vorige stand mag een lege WAL hebben.
        for main in (restored, family.get("")):
            if main is not None:
                side = main.with_name(main.name + "-wal")
                if os.path.lexists(side):
                    st = side.lstat()
                    if stat.S_ISREG(st.st_mode) and not st.st_size:
                        expected.add(side)
        if family:
            try:
                with checked_database(core, family):
                    pass
            except (sqlite3.Error, OSError) as exc:
                raise _PreviousDataError(
                    f"de vorige database {pair.rel} is onleesbaar: {exc}") from exc
    if aside.exists() and any(path not in allowed for path in aside.iterdir()):
        raise SetError("onbekend bestand tussen de vorige databases")
    if any(os.path.lexists(path) for path in allowed - expected):
        raise SetError("onverwachte database of SQLite-hulpbestand bij de omwisseling")


def _swap(core: Path, info: dict, pairs: list[_RestorePair], journal: dict | None,
          owner: tuple[int, int] | None, migrations: Path, max_age_s: float) -> dict:
    marker = core / STAGE_MARKER
    root = core / "backups" / ASIDE_FOLDER
    try:
        requested = datetime.fromisoformat(info["requested_at"])
        if requested.utcoffset() is None:
            raise ValueError()
    except (KeyError, TypeError, ValueError) as exc:
        raise SetError("ongeldige datum in markering voor terugzetten") from exc
    if journal is None:
        state = _begin_swap(root, pairs, requested, max_age_s)
    else:
        state = journal.copy()
        checksum = state.pop("controle", None)
        if checksum != _checksum([info, state]):
            raise SetError("de voortgangsmarkering van het terugzetten is beschadigd")
    name, originals, step = state.get("vorige"), state.get("originelen"), state.get("stap")
    if not isinstance(name, str) or not _ASIDE_NAME.fullmatch(name):
        raise SetError("ongeldige map voor de vorige databases")
    aside = root / name
    if aside.resolve() != aside.absolute():
        raise SetError("ongeldig pad voor de vorige databases")
    existing = next(parent for parent in (aside, *aside.parents) if parent.exists())
    if any(p.live.parent.stat().st_dev != existing.stat().st_dev for p in pairs):
        raise SetError("de vorige databases kunnen niet atomair naar de bewaarmap")
    allowed = {p.rel + tail for p in pairs for tail in _SQLITE_TAILS}
    if (not isinstance(originals, dict) or not originals.keys() <= allowed
            or not all(isinstance(v, str) and re.fullmatch("[0-9a-f]{64}", v)
                       for v in originals.values())):
        raise SetError("ongeldige bestandenlijst voor de vorige databases")
    moves = []
    for p in pairs:
        for tail in _SQLITE_TAILS:
            if p.rel + tail in originals:
                moves.append((p.original(tail), p.saved(aside, tail)))
        moves.append((p.staged, p.live))
    if type(step) is not int or not 0 <= step <= len(moves):
        raise SetError("ongeldige voortgang van de omwisseling")
    # De rename kan al duurzaam zijn terwijl de vorige stap nog in de markering staat.
    if step < len(moves):
        src, dst = moves[step]
        if not os.path.lexists(src) and os.path.lexists(dst):
            step += 1
    _check_position(core, pairs, aside, originals, moves, step, migrations)
    if journal is None:
        _publish_step(marker, info, state, owner)
    aside.mkdir(mode=0o700, parents=True, exist_ok=True)
    for directory in (root, root.parent, core):
        _sync(directory)
    if step != state["stap"]:
        src, dst = moves[step - 1]
        _sync(dst.parent)
        _sync(src.parent)
        state["stap"] = step
        _publish_step(marker, info, state, owner)
    for index, (src, dst) in enumerate(moves[step:], start=step + 1):
        _check_position(core, pairs, aside, originals, moves, index - 1, migrations)
        # Een herbouwbaar lezersregister kan sinds de vorige publicatie verdwenen zijn.
        if not src.name.endswith("-shm") or os.path.lexists(src):
            os.replace(src, dst)
        _sync(dst.parent)
        _sync(src.parent)
        state["stap"] = index
        _publish_step(marker, info, state, owner)
    _check_position(core, pairs, aside, originals, moves, len(moves), migrations)
    _chown([root, aside, *aside.iterdir(), *(p.live for p in pairs)], owner)
    result = {"ok": True, "set": info["set"], "at": _now(), "files": [p.rel for p in pairs],
              "previous": str(aside)}
    _write_result(core, result, owner)
    # De volledig uitgevoerde expliciete aanvraag bepaalt nu de actieve stand.
    if (core / COMMIT_MARKER).exists():
        finish_commit(core)
    _forget(marker)
    return result


def _apply_staged(core_dir: Path, owner: tuple[int, int] | None, max_age_s: float,
                  av1_migrations: Path, keep_on_error: bool) -> dict[str, Any] | None:
    info = _request(core_dir)
    if info is None:
        return None
    begun = "omwisseling" in info
    journal = info.pop("omwisseling", None)
    try:
        if begun and not isinstance(journal, dict):
            raise SetError("ongeldige voortgangsmarkering voor terugzetten")
        pairs = _restore_pairs(core_dir, info)
        return _swap(core_dir, info, pairs, journal, owner, av1_migrations, max_age_s)
    except (SetError, schema.SchemaTooNew) as exc:
        result = {"ok": False, "set": info.get("set"), "at": _now(), "error": str(exc)}
        # Na de eerste stap blijven beide standen en de aanvraag altijd beschikbaar.
        if keep_on_error or isinstance(exc, _PreviousDataError) or begun or swapping(core_dir):
            return result
        try:
            _core_pending(core_dir / "mediaguard.db", MIGRATIONS_DIR, core_dir)
            _encoder_pending(schema.encoder_dir(core_dir), av1_migrations, work=core_dir)
        except (OSError, sqlite3.Error, RuntimeError):
            return result
        cancel(core_dir)
        _write_result(core_dir, result, owner)
        return result


def _write_result(core_dir: Path, result: dict[str, Any],
                  owner: tuple[int, int] | None) -> None:
    _publish(core_dir / RESULT_FILE, json.dumps(result), owner)


def take_result(core_dir: Path) -> dict[str, Any] | None:
    """Uitkomst van de laatste omwisseling, één keer (voor de melding in de app)."""
    path = core_dir / RESULT_FILE
    try:
        result = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None
    if isinstance(result, dict) and result.get("ok") and (core_dir / STAGE_MARKER).exists():
        return None
    with contextlib.suppress(OSError):
        path.rename(path.with_name(path.name + ".gemeld"))
    return result if isinstance(result, dict) else None


# -- supervisor: set vóór de start van een nieuwe versie --------------------------------------
def _read_versions(path: Path, table: str, column: str, work: Path | None,
                   timeout: float = 30) -> set:
    if work is None:
        return schema.applied_versions(path, table, column, timeout=timeout)
    if not path.exists():
        return set()
    with checked_database(work, database_files(path), strict_wal=False) as (conn, _):
        return schema.versions_in(conn, table, column)


def _core_pending(core_db: Path, migrations_dir: Path,
                  work: Path | None = None) -> tuple[str, str] | None:
    wanted = {p.stem for p in migrations_dir.glob("*.sql")}
    have = _read_versions(core_db, "schema_migrations", "version", work)
    if have - wanted:
        raise schema.SchemaTooNew()
    pending = wanted - have
    return (max(map(str, have)), max(pending)) if have and pending else None


def _encoder_versions(migrations_dir: Path) -> set[int]:
    if not migrations_dir.is_dir():
        raise RuntimeError("Migratiemap van de AV1-engine ontbreekt")
    return {int(m.group(1)) for p in migrations_dir.glob("*.sql")
            if (m := _ENC_VERSION.match(p.name))}


@dataclass
class UpdateCommits:
    encoders: dict[Path, sqlite3.Connection]
    marker: Path | None = None
    origin: str | None = None
    targets: dict | None = None

    def begin(self, core: Path, archive: Path, conn: sqlite3.Connection) -> None:
        # De verbindingen zien hun definitieve inhoud in dezelfde nog open schrijftransactie.
        hashes = {f"{core.name}/mediaguard.db": _contents_hash(conn)}
        hashes.update({f"encode/{path.name}": _contents_hash(encoder)
                       for path, encoder in self.encoders.items()})
        self.marker = core / COMMIT_STEP
        self.targets = {"set": archive.name, "oorsprong": self.origin, "doelinhoud": hashes}
        self.record("voorbereid")

    def record(self, step: str) -> None:
        if self.marker is None or self.targets is None:
            raise SetError("de herstelset voor de databasecommit is niet vastgelegd")
        state = {**self.targets, "stap": step}
        _publish(self.marker, json.dumps({**state, "controle": _checksum(state)}))


@contextlib.contextmanager
def reserve_encoders(folder: Path) -> Iterator[UpdateCommits]:
    """Houd oude en nieuwe SQLite-schrijvers tegen tot de geverifieerde upgrade is afgerond."""
    with contextlib.ExitStack() as locks:
        group = UpdateCommits({})
        for path in sorted(folder.glob("*.db")):
            conn = locks.enter_context(contextlib.closing(sqlite3.connect(
                path.resolve().as_uri() + "?mode=rw", uri=True, timeout=30,
            )))
            conn.execute("PRAGMA foreign_keys=ON")
            conn.execute("PRAGMA synchronous=FULL")
            conn.execute("BEGIN IMMEDIATE")
            # executescript mag het slot van de herstelset niet impliciet committen.
            conn.autocommit = False
            group.encoders[path] = locks.enter_context(conn)
        yield group
        if group.marker is not None:
            # De kerntransactie binnen dit bereik is nu duurzaam afgesloten. De AV1-
            # transacties houden hun eigen schrijfslot tot elk zijn beurt heeft gehad.
            group.record("kern vastgelegd")
            for path, conn in reversed(group.encoders.items()):
                conn.commit()
                group.record(f"encode/{path.name}")


def migrate_encoders(databases: list[sqlite3.Connection], migrations: Path) -> None:
    """Pas de meegeleverde AV1-scripts op hun eigen versietabel toe onder dezelfde sloten."""
    scripts = {}
    for path in sorted(migrations.glob("*.sql")):
        match = _ENC_VERSION.match(path.name)
        if not match or (version := int(match[1])) in scripts:
            raise SetError("ongeldige of dubbele AV1-databasemigratie")
        scripts[version] = path
    for conn in databases:
        have = schema.versions_in(conn, "schema_versie", "versie")
        if have - scripts.keys():
            raise schema.SchemaTooNew()
        if have:
            for version in sorted(scripts.keys() - have):
                path = scripts[version]
                conn.executescript(path.read_text(encoding="utf-8"))
                conn.execute("INSERT INTO schema_versie(versie,naam,toegepast_op) VALUES (?,?,?)",
                             (version, path.name, _now()))


def _archive_names(folder: Path) -> set[str]:
    return {p.name for p in folder.glob(SET_PREFIX + "*") if _SET_NAME.fullmatch(p.name)}


def _source_state(core: Path, rel: str, *, strict_wal: bool = True) -> dict:
    main = core.parent / rel
    with checked_database(core, database_files(main), strict_wal=strict_wal) as (conn, wal):
        return {"bestand": rel, "kern": _regular_digest(main), "wal_frames": wal.frames,
                "wal_positie": wal.end, "wal_sha256": wal.sha256, "inhoud": _contents_hash(conn)}


def _bind_archive(core: Path, archive: Path) -> str:
    entries = _update_entries(core, archive)
    meta = _read_meta(archive) or {}
    generation = meta.get("source_generation")
    main, encode = core / "mediaguard.db", schema.encoder_dir(core)
    if _source_generation(main, encode) != generation:
        raise _SourceChanged("de bronbestanden zijn sinds de herstelset gewijzigd")
    try:
        databases = [_source_state(core, rel) for rel in entries]
    except (OSError, sqlite3.Error) as exc:
        if _source_generation(main, encode) != generation:
            raise _SourceChanged("de bronbestanden zijn tijdens het kopiëren gewijzigd") from exc
        raise
    state = {"set": archive.name, "poging": uuid4().hex, "manifest": _checksum(meta),
             "archieven": sorted(_archive_names(archive.parent)), "aantal": len(entries),
             "databases": databases}
    if _source_generation(main, encode) != generation:
        raise _SourceChanged("de bronbestanden zijn tijdens de bewijscontrole gewijzigd")
    digest = _checksum(state)
    _publish(core / COMMIT_ORIGIN, json.dumps({**state, "controle": digest}))
    return digest


def record_commit(core: Path, archive: Path) -> str:
    """Bind onder alle schrijfsloten de originele stand vóór de eerste migratie."""
    marker = core / COMMIT_MARKER
    if os.path.lexists(marker):
        raise SetError("er staat al een onderbroken update klaar")
    origin = _bind_archive(core, archive)
    _publish(marker, archive.name)
    return origin


def finish_commit(core: Path) -> None:
    # De bewijzen blijven beschikbaar totdat de startmarkering duurzaam verdwenen is.
    _forget(core / COMMIT_MARKER)
    for name in (COMMIT_STEP, COMMIT_ORIGIN):
        if (core / name).exists():
            _forget(core / name)


def _read_proof(path: Path) -> dict | None:
    try:
        state = json.loads(path.read_bytes())
    except FileNotFoundError:
        return None
    except (UnicodeError, ValueError) as exc:
        raise SetError("de onderbroken update heeft een onleesbaar bewijs") from exc
    if not isinstance(state, dict) or state.pop("controle", None) != _checksum(state):
        raise SetError("de onderbroken update heeft een beschadigd bewijs")
    return state


def _update_entries(core: Path, archive: Path) -> dict[str, tuple[Path, int, str]]:
    entries = _set_files(core, archive)
    _check_archive_access(archive)
    if problems := verify(archive):
        raise SetError(", ".join(problems))
    live = {f"{core.name}/mediaguard.db", *(
        f"encode/{p.name}" for p in schema.encoder_dir(core).glob("*.db")
    )}
    if entries.keys() != live or any(
        not (core.parent / rel).is_file()
        or (core.parent / rel).resolve() != (core.parent / rel).absolute() for rel in live
    ):
        raise SetError("de onderbroken update mist een bijbehorende database")
    return entries


def _contents_hash(conn: sqlite3.Connection) -> str:
    """Hash schema en alle getypeerde rijen van één SQLite-transactie, inclusief rowid."""
    digest = hashlib.sha256()

    def feed(row: tuple) -> None:
        encoded = json.dumps(tuple(row), default=lambda blob: {"blob": blob.hex()})
        digest.update(encoded.encode() + b"\n")

    for setting in ("user_version", "application_id", "encoding"):
        feed(conn.execute(f"PRAGMA {setting}").fetchone())
    for row in conn.execute("SELECT type,name,tbl_name,sql FROM sqlite_schema "
                            "ORDER BY type,name"):
        feed(row)
    tables = conn.execute("SELECT name,wr FROM pragma_table_list WHERE schema='main' "
                          "AND name!='sqlite_schema' ORDER BY name")
    for name, without_rowid in tables:
        feed((name,))
        quoted = '"' + name.replace('"', '""') + '"'
        columns = {r[0].lower() for r in conn.execute(
            "SELECT name FROM pragma_table_xinfo(?)", (name,))}
        fields = "*"
        if not without_rowid:
            rowid = next((r for r in ("rowid", "_rowid_", "oid") if r not in columns), None)
            if rowid is None:
                raise SetError("de rij-identiteit van de oorspronkelijke database is onbewezen")
            fields = rowid + ",*"
        # Een tabelscan leest de B-boom op rowid of, bij WITHOUT ROWID, op primaire sleutel.
        for row in conn.execute(f"SELECT {fields} FROM {quoted} NOT INDEXED"):  # noqa: S608
            feed(row)
    return digest.hexdigest()


def _legacy_original(core: Path, archive: Path, entries: dict) -> bool:
    # Alleen een volledige historische setnaam kan de update aanwijzen. Een oudere
    # schrijver zonder setnaam heeft geen duurzaam bewijs van zijn archiefkeuze.
    meta = _read_meta(archive) or {}
    for name in _archive_names(archive.parent) - {archive.name}:
        other = _read_meta(archive.parent / name)
        if not other or str(other.get("created_at", "")) >= str(meta.get("created_at", "")):
            return False
    if meta.get("source_generation") != _source_generation(
            core / "mediaguard.db", schema.encoder_dir(core)):
        return False
    for rel, (saved, _, _) in entries.items():
        main = core.parent / rel
        with checked_database(core, database_files(main)) as (live, _), \
                checked_database(core, {"": saved}) as (old, _):
            if _contents_hash(live) != _contents_hash(old):
                return False
    return True


def _interrupted_commit(core: Path, *, finish: bool = True) -> Path | None:
    marker = core / COMMIT_MARKER
    try:
        name = marker.read_bytes().decode("ascii", errors="replace")
    except FileNotFoundError:
        if finish and (core / COMMIT_STEP).exists():
            _forget(core / COMMIT_STEP)
            if (core / COMMIT_ORIGIN).exists():
                _forget(core / COMMIT_ORIGIN)
        return None
    folder = core / "backups" / FOLDER
    origin = _read_proof(core / COMMIT_ORIGIN)
    bound = origin.get("set") if origin is not None else name
    if not isinstance(bound, str) or not _SET_NAME.fullmatch(bound):
        raise SetError("de herstelset voor deze onderbroken update is onbewezen")
    if name.startswith(SET_PREFIX) and (not bound.startswith(name)
                                        or (_SET_NAME.fullmatch(name) and name != bound)):
        raise SetError("de setnaam voor deze onderbroken update spreekt het bewijs tegen")
    archive = folder / bound
    if not archive.is_dir():
        raise SetError("de herstelset voor deze onderbroken update ontbreekt")
    try:
        entries = _update_entries(core, archive)
        if origin is None:
            matches = _legacy_original(core, archive, entries)
        else:
            if (origin.get("aantal") != len(entries)
                    or origin.get("manifest") != _checksum(_read_meta(archive))
                    or _archive_names(folder) - set(origin["archieven"])):
                raise SetError("de onderbroken update heeft een strijdige of recentere set")
            current = [_source_state(core, rel, strict_wal=False) for rel in entries]
            targets = _read_proof(core / COMMIT_STEP)
            initial = origin["databases"]
            for old, actual in zip(initial, current, strict=True):
                if old["wal_frames"] and old["kern"] == actual["kern"]:
                    wal = core.parent / (actual["bestand"] + "-wal")
                    end = old["wal_positie"]
                    if (actual["wal_positie"] < end
                            or wal_prefix_hash(wal, end) != old["wal_sha256"]):
                        raise SetError("de gecommitteerde WAL-stand ontbreekt of is gewijzigd")
            matches = current == initial
            if targets is not None:
                if targets.get("set") != bound or targets.get("oorsprong") != _checksum(origin):
                    raise SetError("de commitstap hoort bij een andere onderbroken update")
                # Ook bij identieke schemaversies moet de laatste taak/eventrij er nog zijn.
                reached = {item["bestand"]: item["inhoud"] for item in current}
                matches |= targets.get("doelinhoud") == reached
    except (SetError, OSError, sqlite3.Error) as exc:
        raise SetError(f"de onderbroken update heeft een onbewezen oorspronkelijke stand: "
                       f"{exc}") from exc
    if not matches:
        raise SetError(f"update deels vastgelegd of oorsprong onbewezen; "
                       f"zet de geverifieerde set {bound} terug")
    if finish:
        finish_commit(core)
    return archive


def _encoder_pending(encode_dir: Path, migrations_dir: Path | None, *, timeout: float = 30,
                     work: Path | None = None) -> bool:
    if migrations_dir is None:
        return False
    databases = sorted(encode_dir.glob("*.db"))
    if not databases:
        return False
    wanted = _encoder_versions(migrations_dir)
    pending = False
    for db in databases:
        have = _read_versions(db, "schema_versie", "versie", work, timeout)
        if have - wanted:
            raise schema.SchemaTooNew()
        pending |= bool(have and wanted - have)
    return pending


def encoder_migrations(env: Mapping[str, str]) -> Path:
    configured = env.get("MG_AIO_ENCODER_DIR")
    if configured:
        return Path(configured) / "app" / "migrations"
    source = Path(__file__).resolve().parents[4] / "services" / "encoder"
    root = source if source.is_dir() else Path("/opt/mediaguard-encode")
    return root / "app" / "migrations"


def check_encoder_schema(encode_dir: Path, *, timeout: float = 30) -> None:
    _encoder_pending(encode_dir, encoder_migrations(os.environ), timeout=timeout)


def has_database(config_root: Path) -> bool:
    return (config_root / "core" / "mediaguard.db").exists() or any(
        (config_root / "encode").glob("*.db")
    )


def inspect_schema(config_root: Path, *, core_migrations: Path = MIGRATIONS_DIR,
                   encoder_migrations: Path | None) -> tuple[tuple[str, str] | None, bool]:
    core = config_root / "core"
    interrupted = (core / COMMIT_MARKER).exists()
    if interrupted:
        try:
            _interrupted_commit(core, finish=False)
        except SetError as exc:
            raise RuntimeError(str(exc)) from exc
    work = core if interrupted or (core / STAGE_MARKER).exists() else None
    return (_core_pending(core / "mediaguard.db", core_migrations, work),
            _encoder_pending(config_root / "encode", encoder_migrations, work=work))


def config_locks(config_root: Path) -> contextlib.AbstractContextManager[None]:
    return schema.startup_locks(config_root / "core", config_root / "encode")


def _previous_version(core_db: Path) -> str | None:
    try:
        conn = sqlite3.connect(f"file:{core_db}?mode=ro", uri=True, timeout=10)
        try:
            row = conn.execute("SELECT value_json FROM system_state WHERE key='app.version'"
                               ).fetchone()
        finally:
            conn.close()
        value = json.loads(row[0]) if row else None
    except (sqlite3.Error, ValueError, TypeError):
        return None
    return str(value.get("version")) if isinstance(value, dict) and value.get("version") \
        else None


def snapshot_before_start(config_root: Path, *, core_migrations: Path,
                          encoder_migrations: Path | None, app_version: str,
                          owner: tuple[int, int] | None = None,
                          migrate_core: bool = False) -> Path | None:
    """Supervisor, vóór de start van de diensten: staat er een migratie open (core of AV1-
    engine), verifieer dan de set met beide databases vóór het vrijgeven van de start."""
    with config_locks(config_root):
        core_db = config_root / "core" / "mediaguard.db"
        previous = _interrupted_commit(core_db.parent)
        core, enc = inspect_schema(config_root, core_migrations=core_migrations,
                                   encoder_migrations=encoder_migrations)
        if migrate_core:
            event_keys.prepare_for_start(core_db, event_keys.key_path(core_db.parent), owner)
        _protect_old_manifests(core_db.parent / "backups" / FOLDER)
        _clean_marker_parts(core_db.parent)
        marker = core_db.parent / UPGRADE_SET
        if marker.exists():
            _forget(marker)
        if core is None and not enc:
            if migrate_core:
                with contextlib.closing(Database(core_db)) as db:
                    initialized = db.initialize()
                    db.require_writable()
                    return initialized or previous
            return previous
        for attempt in range(2):
            try:
                archive = _make_set(core_db.parent / "backups" / FOLDER, core_db,
                                    encode_dir=config_root / "encode",
                                    from_schema=core[0] if core else None,
                                    to_schema=core[1] if core else None,
                                    from_version=_previous_version(core_db), to_version=app_version,
                                    reason="update", owner=owner)
                if not migrate_core:
                    _bind_archive(core_db.parent, archive)
                break
            except _SourceChanged:
                # Ook een normaal checkpoint verandert de fysieke stand. Begin vóór
                # de migratie één keer opnieuw met een verse set en haar eigen bewijs.
                if attempt:
                    raise
        if migrate_core:
            with contextlib.closing(Database(core_db, synchronous="FULL")) as db:
                archive = db.initialize(pre_update_set=archive,
                                        encoder_migrations=encoder_migrations if enc else None)
                db.require_writable()
                if archive is None:
                    raise SetError("er is geen geverifieerde herstelset voor deze update")
        elif core:
            _publish(marker, archive.name)
        return archive


def main(argv: list[str] | None = None) -> int:  # pragma: no cover - aangeroepen door aio
    """``python -m mediaguard.maintenance.preupdate snapshot …``: de supervisor draait dit als
    de app-gebruiker (niet als root), zodat SQLite-hulpbestanden (-wal/-shm) die bij het lezen
    ontstaan nooit van root worden en core daarna gewoon kan schrijven."""
    import argparse
    import sys

    p = argparse.ArgumentParser(prog="python -m mediaguard.maintenance.preupdate")
    sub = p.add_subparsers(dest="cmd", required=True)
    for cmd in ("inspect", "snapshot", "upgrade"):
        action = sub.add_parser(cmd)
        action.add_argument("--config", required=True, type=Path)
        action.add_argument("--core-migrations", required=True, type=Path)
        action.add_argument("--encoder-migrations", type=Path)
        if cmd != "inspect":
            action.add_argument("--version", required=True)
    restore = sub.add_parser("zet-klaar", help="Maak de gecontroleerde terugzetkopieën")
    restore.add_argument("--kernmap", required=True, type=Path)
    restore.add_argument("--set", required=True)
    a = p.parse_args(argv)
    try:
        if a.cmd == "zet-klaar":
            core = a.kernmap.resolve()
            with Database(core / "mediaguard.db").startup_lock(blocking=False):
                archive = find(core / "backups" / FOLDER, a.set)
                if archive is None:
                    raise SetError("herstelset ontbreekt")
                info = pending(core)
                if info and info.get("set") != a.set:
                    raise SetError("er staat al een andere set klaar")
                info = stage(core, archive, actor="offline-herstel")
                for entry in info["files"]:
                    print(f"{entry['staged']} → {entry['live']}")
                return 0
        if a.cmd == "inspect":
            inspect_schema(a.config, core_migrations=a.core_migrations,
                           encoder_migrations=a.encoder_migrations)
            return 0
        made = snapshot_before_start(a.config, core_migrations=a.core_migrations,
                                     encoder_migrations=a.encoder_migrations,
                                     app_version=a.version, migrate_core=a.cmd == "upgrade")
    except schema.SchemaTooNew as exc:
        print(str(exc), file=sys.stderr)
        return schema.EXIT_CODE
    except Exception as exc:  # noqa: BLE001
        print(str(exc), file=sys.stderr)
        return 1
    print(str(made) if made else "")
    return 0


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())
