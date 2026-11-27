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
from collections.abc import Callable, Iterator, Mapping
from dataclasses import dataclass
from datetime import UTC, datetime
from functools import partial
from pathlib import Path
from typing import Any
from uuid import uuid4

from mediaguard import event_keys, schema
from mediaguard.db import MIGRATIONS_DIR, Database, check_schema_versions
from mediaguard.maintenance import restore_trial
from mediaguard.maintenance.sqlite_progress import contains_commit, content_proof, row_bytes
from mediaguard.maintenance.sqlite_recovery import (
    checked_database,
    copied_family,
    database_files,
    wal_prefix_hash,
)

FOLDER = "voor-update"
SET_PREFIX = "voor-update-"
ASIDE_FOLDER = "voor-terugzetten"
STAGE_MARKER = "terugzetten.json"
RESULT_FILE = "terugzetten-resultaat.json"
UPGRADE_SET = "update-herstelset"
COMMIT_MARKER = "update-in-uitvoering"
COMMIT_STEP = "update-commitstap.json"
COMMIT_ORIGIN = "update-oorsprong.json"
MANUAL_CHOICE = "wacht op handmatige keuze"
LEGACY_KEY_META = "meta-met-sleutelverwijzing.json"
_CONFLICT_PREFIX = "meta-conflict-"
STAGED_SUFFIX = ".terugzetten"
KEEP_SETS = 5
PARTIAL_GRACE_S = 3600
STAGE_MAX_AGE_S = 24 * 3600  # daarna niet meer omwisselen (geen verrassing weken later)
_SET_NAME = re.compile(r"^voor-update-\d{8}T\d{6}Z(?:-\d+|-[0-9]{6}-[a-f0-9]{32})?$")
_LEGACY_NAME = re.compile(r"^mediaguard-[\w.-]+-naar-[\w.-]+-\d{8}T\d{6}Z\.db$")
_ENC_VERSION = re.compile(r"^(\d+)_")
_ENGINE_FILE = r"[a-zA-Z0-9][a-zA-Z0-9_.-]*\.db"
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


class _ChoiceRequired(_PreviousDataError):
    def __init__(self, preview: dict):
        super().__init__(MANUAL_CHOICE)
        self.preview = preview


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


def _write_phase(core: Path, state: dict, owner: tuple[int, int] | None = None) -> None:
    _publish(core / COMMIT_ORIGIN, json.dumps({**state, "controle": _checksum(state)}), owner)


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


def _copy_sqlite(src: Path, dest: Path, *, work: Path | None = None) -> dict[str, Any]:
    """Kopie via de backup-API (WAL-veilig) + hersteltest; nooit een half bestand."""
    part = dest.with_name(dest.name + ".part")
    try:
        extra = {}
        with contextlib.ExitStack() as readers:
            if work is None:
                s = readers.enter_context(contextlib.closing(sqlite3.connect(
                    src.resolve().as_uri() + "?mode=ro", uri=True, timeout=30)))
            else:
                s, _ = readers.enter_context(checked_database(work, database_files(src)))
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
             preserve: Path | None = None, supervised: bool = False) -> Path:
    """Maak een complete set (eerst als ``.part``-map, pas daarna de echte naam)."""
    peer = encode_dir or schema.encoder_dir(core_db.parent)
    with schema.startup_locks(core_db.parent, peer):
        Database(core_db, offline_guard=supervised).require_writable()
        if peer != schema.encoder_dir(core_db.parent):
            check_encoder_schema(peer)
        return _make_set(folder, core_db, encode_dir=encode_dir, from_schema=from_schema,
                         to_schema=to_schema, from_version=from_version, to_version=to_version,
                         reason=reason, owner=owner, preserve=preserve, supervised=supervised)


def _make_set(folder: Path, core_db: Path, *, encode_dir: Path | None,
              from_schema: str | None, to_schema: str | None, from_version: str | None,
              to_version: str | None, reason: str, owner: tuple[int, int] | None,
              preserve: Path | None = None, supervised: bool = False,
              prepare: Callable[[], None] | None = None) -> Path:
    stamp = datetime.now(UTC)
    # Oudere supervisors accepteren na de seconde alleen één decimaal achtervoegsel.
    name = f"{SET_PREFIX}{stamp:%Y%m%dT%H%M%SZ-%f}{uuid4().int:039d}"
    if supervised:
        _archive_intent(core_db, encode_dir, name, owner)
    if prepare is not None:
        prepare()
    folder.mkdir(parents=True, exist_ok=True)
    _chown([folder.parent, folder], owner)
    work = folder / (name + ".part")
    work.mkdir(mode=0o700)
    files: list[dict[str, Any]] = []
    copy_db = partial(_copy_sqlite, work=core_db.parent) if supervised else _copy_sqlite
    try:
        generation = _source_generation(core_db, encode_dir) if reason == "update" else None
        files.append({"name": "mediaguard.db", "live": f"{core_db.parent.name}/{core_db.name}",
                      **copy_db(core_db, work / "mediaguard.db")})
        encoder_problem = None
        if encode_dir is not None and encode_dir.is_dir():
            (work / "encode").mkdir(mode=0o700)
            for db in sorted(encode_dir.glob("*.db")):
                try:
                    files.append({"name": f"encode/{db.name}", "live": f"encode/{db.name}",
                                  **copy_db(db, work / "encode" / db.name)})
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
    if folder.name == FOLDER:
        core = folder.parent.parent
        phase = _read_proof(core / COMMIT_ORIGIN)
        if (core / COMMIT_MARKER).exists() or (phase and phase.get("fase") != "aanbod"):
            return 0  # Bewaar ook strijdige sets totdat de onderbreking is afgehandeld.
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


def _single_manifest(folder: Path) -> Path:
    legacy, normal = folder / LEGACY_KEY_META, folder / "meta.json"
    if (os.path.lexists(legacy) and os.path.lexists(normal)
            or any(folder.glob(_CONFLICT_PREFIX + "*.json"))):
        raise _PreviousDataError("de herstelset heeft twee verschillende manifesten")
    manifest = legacy if os.path.lexists(legacy) else normal
    if manifest.is_symlink():
        raise _PreviousDataError("het manifest is geen zelfstandig bestand")
    return manifest


def _uses_legacy_key(manifest: Path) -> bool | None:
    """None als uit deze manifestnaam geen betrouwbare bestandenlijst te lezen is."""
    if manifest.is_symlink() or not manifest.is_file():
        return None
    try:
        files = json.loads(manifest.read_text(encoding="utf-8"))["files"]
    except (OSError, ValueError, KeyError, TypeError):
        return None
    if not isinstance(files, list):
        return None
    if any(isinstance(row, dict) and "legacy_cursor_key" in row for row in files):
        return True
    return False if all(isinstance(row, dict) for row in files) else None


def _old_manifests(folder: Path) -> Iterator[tuple[Path, Path]]:
    """Plan welke gewone manifestnamen voor beheer op schema 155 moeten verdwijnen."""
    for manifest in folder.glob(SET_PREFIX + "*/meta.json"):
        if not _SET_NAME.fullmatch(manifest.parent.name):
            continue
        target = manifest.with_name(LEGACY_KEY_META)
        historical = _uses_legacy_key(manifest)
        parked = any(manifest.parent.glob(_CONFLICT_PREFIX + "*.json"))
        if parked or os.path.lexists(target):
            if not parked and historical is False and _uses_legacy_key(target) is False:
                continue
            # Bewaar beide versies. Het oude beheer kent alleen meta.json en de
            # huidige lezer blijft de set ook na deze naamswijziging weigeren.
            target = manifest.with_name(f"{_CONFLICT_PREFIX}{uuid4().hex}.json")
        else:
            _single_manifest(manifest.parent)
            if historical is not True:
                continue
        if manifest.parent.is_symlink():
            raise _PreviousDataError("de herstelset is geen zelfstandige map")
        yield manifest, target


def _protect_old_manifests(folder: Path) -> None:
    for manifest, target in list(_old_manifests(folder)):
        manifest.rename(target)
        _sync(manifest.parent)


def list_sets(folder: Path) -> list[dict[str, Any]]:
    out: list[dict[str, Any]] = []
    if not folder.is_dir():
        return out
    for p in folder.iterdir():
        if p.is_dir() and _SET_NAME.match(p.name):
            meta = _read_meta(p)
            if meta is None:
                continue
            try:
                size = sum(int(f.get("size") or 0) for f in meta.get("files") or [])
            except (AttributeError, TypeError, ValueError):
                continue
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
    if path.is_file():
        try:
            with checked_database(core_dir or path.parent, database_files(path)):
                return []
        except (OSError, sqlite3.Error) as exc:
            return [f"{path.name}: {exc}"]
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
    _single_manifest(path)
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
        if "legacy_cursor_key" in entry:
            try:
                event_keys.load_legacy(core_dir, entry["legacy_cursor_key"])
            except (OSError, ValueError) as exc:
                raise SetError("de historische cursorsleutel is niet beschikbaar") from exc
        entries[rel] = (path / expected, size, digest)
    if f"{core_dir.name}/mediaguard.db" not in entries:
        raise SetError("MediaGuard-database ontbreekt in de back-upset")
    if bool(meta.get("encoder_included")) != any(rel.startswith("encode/") for rel in entries):
        raise SetError("AV1-database ontbreekt in de back-upset")
    return entries


def _restore_primary(core: Path, targets: set[str]) -> str | None:
    folder = encoder_backup_dir(core)
    primary = primary_encoder_db(folder)
    if (not os.environ.get("DATABASE_URL") and not any(rel.startswith("encode/") for rel in targets)
            and not any(folder.glob("*.db"))):
        return None  # Een historische kernset op een installatie zonder actieve encoder.
    if (primary is None or primary.parent.resolve() != (core.parent / "encode").resolve()
            or f"encode/{primary.name}" not in targets):
        raise _PreviousDataError("De ingestelde primaire AV1-database ontbreekt in de herstelset")
    return f"encode/{primary.name}"


def _check_source_file(path: Path, size: int, digest: str) -> None:
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


def _check_restore_copy(path: Path, rel: str, size: int, digest: str,
                        av1_migrations: Path, work: Path, primary: str | None, *,
                        describe: bool = False) -> dict | None:
    try:
        _check_source_file(path, size, digest)
        with checked_database(work, {"": path}) as (db, _):
            if rel.startswith("encode/"):
                encoder_copy_versions(db, av1_migrations, primary=rel == primary)
            else:
                check_schema_versions(path, connection=db)
            return _restore_state(db) if describe else None
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
        if os.environ.get(schema.AIO_RECOVERY_ENV):
            raise schema.SchemaTooNew()
        _core_pending(db.path, MIGRATIONS_DIR, core_dir)
        _encoder_pending(schema.encoder_dir(core_dir), encoder_migrations(os.environ),
                         work=core_dir)
        return _stage(core_dir, path, actor)


def _install_copy(destination: Path, entry: tuple[Path, int, str], rel: str, migrations: Path,
                  work: Path, primary: str | None, legacy: bytes | None,
                  before_publish: Callable[[], None] | None = None) -> tuple[int, str]:
    """Installeer dezelfde gecontroleerde bytes voor de proef en het echte klaarzetten."""
    src, size, digest = entry
    with tempfile.NamedTemporaryFile(dir=destination.parent, suffix=".part", delete=False) as out:
        part = Path(out.name)
    try:
        shutil.copyfile(src, part)
        _check_restore_copy(part, rel, size, digest, migrations, work, primary)
        if not rel.startswith("encode/") and legacy is not None:
            with contextlib.closing(sqlite3.connect(part)) as old, old:
                old.execute("UPDATE outbox_retention SET cursor_key=? WHERE singleton=1", (legacy,))
            size, digest = part.stat().st_size, _sha256(part)
            _check_restore_copy(part, rel, size, digest, migrations, work, primary)
        _sync(part)
        if before_publish is not None:
            before_publish()
        os.replace(part, destination)
        _sync(destination.parent)
        return size, digest
    finally:
        part.unlink(missing_ok=True)


def trial_databases(core: Path, entries: dict[str, tuple[Path, int, str]], primary: str | None,
                    migrations: Path, *, legacy: bytes | None = None,
                    online_core: bool = False, keep_core: bool = False,
                    source_set: Path | None = None) -> dict[str, list[str]]:
    """Klaarzetten en omwisselen op lege appdata, inclusief historische sleutelomzetting."""
    family = database_files(core / "mediaguard.db") if keep_core else {}
    sizes = [entry[1] for entry in entries.values()]
    if keep_core:
        sizes.append(sum(p.stat().st_size for p in family.values()))

    def restore(root: Path) -> None:
        copies = entries.copy()
        if keep_core:
            snapshot = root / "bestaande-kern.db"
            with checked_database(root, family, strict_wal=False) as (db, _), \
                    contextlib.closing(sqlite3.connect(snapshot)) as writer:
                db.backup(writer)
            copies["core/mediaguard.db"] = snapshot, snapshot.stat().st_size, _sha256(snapshot)
        restore_trial.copy_cursor_key(core, root / "secrets" / event_keys.KEY_REF)
        files = []
        for live, entry in copies.items():
            rel = live if live.startswith("encode/") else "core/mediaguard.db"
            target = root / rel
            target.parent.mkdir(exist_ok=True)
            staged = target.with_name(target.name + STAGED_SUFFIX)
            size, digest = _install_copy(staged, entry, rel, migrations, root, primary, legacy)
            if online_core and rel == "core/mediaguard.db":
                source = staged.with_suffix(".bron")
                staged.rename(source)
                with restore_trial.core_source(source, immutable=True) as reader, \
                        contextlib.closing(sqlite3.connect(staged)) as writer:
                    reader.backup(writer)
                source.unlink()
                size, digest = staged.stat().st_size, _sha256(staged)
            files.append({"live": rel, "staged": rel + STAGED_SUFFIX,
                          "size": size, "sha256": digest})
        # Dezelfde lezers, wisselplanning, uitvoering en eindcontrole als bij een
        # offline herstel. Alleen de lege proefmap krijgt een nieuwe aanvraag.
        trial_core = root / "core"
        name = datetime.now(UTC).strftime(SET_PREFIX + "%Y%m%dT%H%M%SZ")
        info = {"set": name, "requested_at": _now(), "files": files,
                "manifest": _manifest(name, files)}
        _publish(trial_core / STAGE_MARKER, json.dumps(info))
        pairs = _restore_pairs(trial_core, info)
        plan = _swap_plan(trial_core, info, pairs, None, migrations, STAGE_MAX_AGE_S, root,
                          primary=primary)
        _execute_swap(trial_core, info, pairs, None, None, migrations, primary,
                      STAGE_MAX_AGE_S, plan)

    return restore_trial.proefherstel(restore, primary=primary, migrations=migrations,
                                      size=sum(sizes) + 3 * max(sizes), source_set=source_set)


def _regular_entry(path: Path, row: dict) -> tuple[Path, int, str]:
    digest, size = row.get("sha256"), row.get("size")
    if (not isinstance(digest, str) or not re.fullmatch(r"[a-f0-9]{64}", digest)
            or type(size) is not int or size <= 0
            or path.resolve() != path.absolute()):
        raise SetError("ongeldige metadata van de databasekopie")
    _check_source_file(path, size, digest)
    return path, size, digest


def trial_regular(
    core: Path, path: Path, meta: dict, *, independent_encoder: bool = False,
) -> dict[str, tuple[str, list[str]]]:
    """Bewijs de losse back-upbestanden van de gewone kernherstelroute."""
    if meta.get("integrity_ok") is not True:
        raise SetError("de integriteitsmeting van de kernkopie ontbreekt")
    folder = encoder_backup_dir(core)
    configured = primary_encoder_db(folder)
    primary = f"encode/{configured.name}" if configured else None
    files = meta.get("extra_files", [])
    if (not isinstance(files, list) or meta.get("encoder_problem")
            or meta.get("encoder_ok") is False
            or (primary is None and (files or os.environ.get("DATABASE_URL")))):
        raise SetError("de AV1-kopie is onvolledig")
    entries = {}
    prefix = f"{path.stem}.encode-"
    for row in files:
        name = row.get("file") if isinstance(row, dict) else None
        if not isinstance(name, str) or not re.fullmatch(re.escape(prefix) + _ENGINE_FILE, name):
            raise SetError("ongeldig AV1-bestand in de back-up")
        rel = "encode/" + name.removeprefix(prefix)
        if rel in entries or "error" in row:
            raise SetError("de AV1-kopie is onvolledig")
        entries[rel] = _regular_entry(path.parent / name, row)
    if not files and not os.environ.get("DATABASE_URL") and not any(folder.glob("*.db")):
        primary = None
    if primary is not None and primary not in entries:
        raise SetError("de primaire AV1-kopie ontbreekt")

    migrations = encoder_migrations(os.environ)
    core_rel = "core/mediaguard.db"
    try:
        entries[core_rel] = _regular_entry(path, meta)
        versions = trial_databases(core, entries, primary, migrations, online_core=True)
    except (OSError, sqlite3.Error, RuntimeError, SetError):
        # De gewone AV1-kopieën zijn ook afzonderlijk herstelbaar naast de huidige kern.
        # Bewijs die combinatie op lege appdata. Alleen de AV1-bestanden krijgen dekking.
        if not files or not independent_encoder:
            raise
        entries.pop(core_rel, None)
        versions = trial_databases(core, entries, primary, migrations, online_core=True,
                                   keep_core=True)
    return {rel: (row[2], versions[rel])
            for rel, row in entries.items()}


def trial_set(core_dir: Path, path: Path) -> tuple[
        dict[str, tuple[Path, int, str]], bytes | None, str | None, dict[str, list[str]]]:
    # Ook een oude supervisor moet alle historische manifesten veilig kunnen verbergen.
    list(_old_manifests(core_dir / "backups" / FOLDER))
    manifest = _single_manifest(path) if path.is_dir() else path
    try:
        generation = schema._generation(manifest)
    except OSError as exc:
        raise SetError("meta.json ontbreekt of het archief is onleesbaar") from exc
    pairs = _set_files(core_dir, path)
    primary = _restore_primary(core_dir, set(pairs))
    migrations = encoder_migrations(os.environ)
    for rel, (src, size, digest) in pairs.items():
        dest = core_dir.parent / (rel + STAGED_SUFFIX)
        live = core_dir.parent / rel
        if (dest.resolve() != dest.absolute() or live.resolve() != live.absolute()
                or not dest.parent.is_dir() or src.resolve() != src.absolute()):
            raise SetError("ongeldig pad naar de klaargezette database")
        _check_source_file(src, size, digest)
    core_src = pairs[f"{core_dir.name}/mediaguard.db"][0]
    try:
        uri = core_src.resolve().as_uri() + "?immutable=1"
        with contextlib.closing(sqlite3.connect(uri, uri=True)) as db:
            ref = _cursor_value(db)
    except sqlite3.Error as exc:
        raise SetError("de kern in de back-upset is onleesbaar") from exc
    legacy = None
    if isinstance(ref, str) and ref.startswith(event_keys.LEGACY_PREFIX):
        try:
            legacy = event_keys.load_legacy(core_dir, ref)
        except (OSError, ValueError) as exc:
            raise SetError("de historische cursorsleutel is niet beschikbaar") from exc
    versions = trial_databases(core_dir, pairs, primary, migrations, legacy=legacy,
                               source_set=path if path.is_dir() else None)
    try:
        if schema._generation(manifest) != generation:
            raise SetError("het bronmanifest is tijdens het proefherstel gewijzigd")
    except OSError as exc:
        raise SetError("de bron is tijdens het proefherstel verdwenen") from exc
    return pairs, legacy, primary, versions


def _stage(core_dir: Path, path: Path, actor: str) -> dict[str, Any]:
    config_root = core_dir.parent
    pairs, legacy, primary, _ = trial_set(core_dir, path)
    migrations = encoder_migrations(os.environ)
    restore_trial.require_space(config_root, sum(size for _, size, _ in pairs.values()))
    previous = _read_proof(core_dir / COMMIT_ORIGIN)
    if previous and previous.get("fase") == "klaarzetten":
        previous = previous["update"]
    preparation = {"fase": "klaarzetten", "poging": uuid4().hex, "set": path.name,
                   "databases": [_source_state(core_dir, rel, record_content=True) for rel in pairs
                                 if (config_root / rel).is_file()], "update": previous}
    if path.is_dir():
        _single_manifest(path)
    _protect_old_manifests(core_dir / "backups" / FOLDER)
    _write_phase(core_dir, preparation)
    _cancel(core_dir)
    staged: list[dict[str, Any]] = []
    chosen = []
    try:
        for live, (src, size, digest) in pairs.items():
            dest = _within(config_root, live + STAGED_SUFFIX)
            if dest != (config_root / (live + STAGED_SUFFIX)).absolute():
                raise SetError("ongeldig pad naar de klaargezette database")
            size, digest = _install_copy(
                dest, (src, size, digest), live, migrations, core_dir, primary, legacy,
                partial(_check_preparation, core_dir, preparation["poging"]),
            )
            staged.append({"live": live, "staged": live + STAGED_SUFFIX,
                           "size": size, "sha256": digest})
            chosen.append({**_source_state(core_dir, live + STAGED_SUFFIX), "bestand": live})
    except _PreviousDataError:
        raise
    except (OSError, sqlite3.Error, SetError) as exc:
        for s in staged:
            with contextlib.suppress(OSError):
                _within(config_root, s["staged"]).unlink()
        raise SetError(f"klaarzetten mislukt: {exc}", disk=isinstance(exc, OSError)) from exc
    info = {"set": path.name, "requested_at": _now(), "by": actor, "files": staged,
            "manifest": _manifest(path.name, staged), "voorbereiding": preparation["poging"]}
    _check_preparation(core_dir, preparation["poging"])
    _write_phase(core_dir, {**preparation, "aanvraag": info, "gekozen": chosen})
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
    phase = _read_proof(core_dir / COMMIT_ORIGIN)
    if phase and phase.get("fase") == "omwisseling":
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


def manual_choice_pending(core: Path) -> bool:
    """De Hub en de offline beheerroute lezen dezelfde bestaande aanvraag."""
    if not (core / STAGE_MARKER).is_file() or not swapping(core):
        return False
    phase = _read_proof(core / COMMIT_ORIGIN)
    if not phase or phase.get("fase") != "omwisseling":
        return True
    return ("keuze" in phase and phase["stap"] <= phase["keuzestap"]
            and staged_status(core) is not True)


def _restore_state(db: sqlite3.Connection) -> dict:
    state = {"integriteit": "ok", **_logical_state(db)}
    for table, column in (("jobs", "progress"), ("job", "percent")):
        columns = {row[0] for row in db.execute("SELECT name FROM pragma_table_info(?)", (table,))}
        if column in columns:
            sql = (f"SELECT min({column}), max({column}) FROM {table} "  # noqa: S608
                   f"WHERE typeof({column}) IN ('integer', 'real')")
            state["voortgang"] = list(db.execute(sql).fetchone())
    return state


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
    phase = _read_proof(core_dir / COMMIT_ORIGIN)
    if (phase and phase.get("fase") == "klaarzetten"
            and phase["poging"] == info.get("voorbereiding")):
        if phase["update"] is None:
            _forget(core_dir / COMMIT_ORIGIN)
        else:
            _write_phase(core_dir, phase["update"])
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
    _write_phase(marker.parent, {**state, "fase": "omwisseling", "aanvraag": info}, owner)


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
                max_age_s: float, *, manual: bool = False, reserved: Path | None = None) -> dict:
    begun = any(not p.staged.exists() for p in pairs)
    candidates = _legacy_asides(root, pairs, requested)
    if len(candidates) > 1 or (begun and not candidates):
        raise SetError("klaargezette kopie ontbreekt of de vorige databases zijn niet eenduidig")
    if candidates:
        if not manual:
            raise _PreviousDataError(MANUAL_CHOICE)
        aside, = candidates
        if reserved is not None and reserved != aside:
            raise SetError("de historische bewaarmap en de vorige databases spreken elkaar tegen")
    elif reserved is not None:
        aside = reserved
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


def _check_position(core: Path, work: Path, pairs: list[_RestorePair], aside: Path, originals: dict,
                    moves: list[tuple[Path, Path]], step: int, migrations: Path,
                    primary: str | None, proof: dict | None = None, *, chosen: dict | None = None,
                    prepared: dict | None = None) -> dict:
    """Bewijs elke volledige database, ook tussen de rename van de kern en haar WAL."""
    moved = dict(moves[:step])
    expected = set()
    allowed = set()
    measured = {}
    prepared_hashes = prepared or {}
    if aside.resolve() != aside.absolute() or (os.path.lexists(aside) and not aside.is_dir()):
        raise SetError("ongeldig pad voor de vorige databases")
    for pair in pairs:
        restored = moved.get(pair.staged, pair.staged)
        selected = _check_restore_copy(restored, pair.rel, pair.size, pair.digest, migrations,
                                       work, primary, describe=chosen is not None)
        if chosen is not None:
            chosen[pair.rel] = selected
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
                if (tail == "-wal" and (proof is not None or chosen is not None
                                        or pair.rel in prepared_hashes)
                        and not os.path.lexists(source)):
                    continue  # De backup-API bewijst of alle gecommitteerde rijen er nog zijn.
                if not source.is_file() or not stat.S_ISREG(source.lstat().st_mode):
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
                with checked_database(work, family) as (conn, _):
                    measured[pair.rel] = _restore_state(conn)
            except (sqlite3.Error, OSError) as exc:
                raise _PreviousDataError(
                    f"de vorige database {pair.rel} is onleesbaar: {exc}") from exc
    if aside.exists() and any(path not in allowed for path in aside.iterdir()):
        raise SetError("onbekend bestand tussen de vorige databases")
    if any(os.path.lexists(path) for path in allowed - expected):
        raise SetError("onverwachte database of SQLite-hulpbestand bij de omwisseling")
    if proof is not None and measured != proof:
        raise _PreviousDataError("de vorige database wijkt af van de bewezen commit")
    if any(measured.get(rel, {}).get("inhoud") != digest
           for rel, digest in prepared_hashes.items()):
        raise _PreviousDataError("de gecommitteerde inhoud veranderde vóór de omwisseling")
    return measured


def _choice_preview(info: dict, aside: Path, previous: dict, chosen: dict) -> dict:
    return {"status": MANUAL_CHOICE, "set": info["set"], "bewaarmap": aside.name,
            "vorige": previous, "gekozen": chosen,
            "bevestig": _checksum([info, aside.name, previous, chosen])}


def _swap_plan(core: Path, info: dict, pairs: list[_RestorePair], journal: dict | None,
               migrations: Path, max_age_s: float, work: Path, *, primary: str | None,
               manual: bool = False,
               preview: dict | None = None) -> tuple[
                   Path, dict, list[tuple[Path, Path]], int, bool]:
    root = core / "backups" / ASIDE_FOLDER
    try:
        requested = datetime.fromisoformat(info["requested_at"])
        if requested.utcoffset() is None:
            raise ValueError()
    except (KeyError, TypeError, ValueError) as exc:
        raise SetError("ongeldige datum in markering voor terugzetten") from exc
    phase = _read_proof(core / COMMIT_ORIGIN)
    continuing = phase is not None and phase.get("fase") == "omwisseling"
    prepared = {}
    if continuing:
        state = phase.copy()
        original_journal = "keuze" in state and state["journaal"] == _checksum(journal)
        if state.pop("aanvraag") != info or (
            journal not in (None, {"fasebestand": state["poging"]}) and not original_journal
        ):
            raise SetError("de terugzetaanvraag en het fasebestand horen niet bij elkaar")
        state.pop("fase")
    else:
        if journal is None:
            state = _begin_swap(root, pairs, requested, max_age_s, manual=manual)
        else:
            if not manual:
                raise _PreviousDataError(MANUAL_CHOICE)
            state = journal.copy()
            if (state.pop("controle", None) != _checksum([info, state])
                    or state.keys() != {"vorige", "originelen", "stap"}):
                raise SetError("de historische omwisselstap hoort niet bij deze aanvraag")
        if not manual and (info.get("voorbereiding") or
                               (phase and phase.get("fase") == "klaarzetten")):
            prepared = _check_preparation(core, info.get("voorbereiding"), work=work)
        state["poging"] = uuid4().hex
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
    if (step == 0 and (manual or "keuze" in state) and journal is not None
            and all(p.staged.exists() for p in pairs)
            and not any(os.path.lexists(p.saved(aside, tail))
                        for p in pairs for tail in _SQLITE_TAILS)):
        # Vóór de eerste rename kan een gewone schrijver een nieuwe WAL hebben geopend.
        # De naam ligt vast in het journaal. Meet de huidige familie in diezelfde indeling.
        if any(p.rel in originals and not p.live.is_file() for p in pairs):
            raise _PreviousDataError("een oorspronkelijke database ontbreekt vóór de eerste wissel")
        fresh = _begin_swap(root, pairs, requested, max_age_s, manual=True, reserved=aside)
        if manual:
            originals = state["originelen"] = fresh["originelen"]
        elif fresh["originelen"].keys() != originals.keys():
            raise _PreviousDataError(MANUAL_CHOICE)
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
    if continuing and "databases" not in state:
        raise SetError("het vorige databasebewijs ontbreekt in het fasebestand")
    choosing = (manual and not continuing) or ("keuze" in state and step == state["keuzestap"])
    chosen = {} if choosing else None
    measured = _check_position(core, work, pairs, aside, originals, moves, step, migrations,
                               primary, None if choosing else state.get("databases"), chosen=chosen,
                               prepared=prepared)
    if not continuing or choosing:
        state["databases"] = measured
    if choosing:
        choice = _choice_preview(info, aside, measured, chosen)
        if not manual and choice["bevestig"] != state["keuze"]:
            raise _ChoiceRequired(choice)
        if preview is not None:
            preview.update(choice)
    return aside, state, moves, step, continuing


def _proven_swap(core: Path, info: dict, pairs: list[_RestorePair], journal: dict | None,
                 migrations: Path, max_age_s: float, work: Path, *, manual: bool = False,
                 preview: dict | None = None):
    primary = _restore_primary(core, {p.rel for p in pairs})
    plan = _swap_plan(core, info, pairs, journal, migrations, max_age_s, work,
                      primary=primary, manual=manual, preview=preview)
    _, _, moves, step, _ = plan
    # Bij hervatten kan de gekozen kopie al op haar eindnaam staan. Herhaal de proef
    # op precies die bytes, vóór de eerste of een volgende publicatie in echte appdata.
    moved = dict(moves[:step])
    entries = {p.rel: (moved.get(p.staged, p.staged), p.size, p.digest) for p in pairs}
    try:
        trial_databases(core, entries, primary, migrations,
                        source_set=core / "backups" / FOLDER / info["set"])
    except SetError as exc:
        raise _PreviousDataError(str(exc), disk=exc.disk) from exc
    return primary, plan


def staged_status(core: Path, *, manual: bool = False) -> bool | None:
    """Controleer de geplande wissel of beheerkeuze op afzonderlijke tijdelijke kopieën."""
    marker = core / STAGE_MARKER
    try:
        before = schema._generation(marker)
    except FileNotFoundError:
        return False
    except OSError:
        return None
    try:
        phase = _read_proof(core / COMMIT_ORIGIN)
        info = _request(core)
        if info is None:
            return None
        pairs, journal = _restore_request(core, info)
        with tempfile.TemporaryDirectory(prefix="mg-terugzetcontrole-") as work:
            _proven_swap(core, info, pairs, journal, encoder_migrations(os.environ),
                         STAGE_MAX_AGE_S, Path(work), manual=manual)
        if before != schema._generation(marker) or _read_proof(core / COMMIT_ORIGIN) != phase:
            return None
    except (OSError, ValueError, RuntimeError, SetError):
        return None
    return True


def _swap(core: Path, info: dict, pairs: list[_RestorePair], journal: dict | None,
          owner: tuple[int, int] | None, migrations: Path, max_age_s: float,
          *, manual: str | None = None) -> dict:
    preview = {}
    primary, plan = _proven_swap(core, info, pairs, journal, migrations, max_age_s, core,
                                 manual=manual is not None, preview=preview)
    return _execute_swap(core, info, pairs, journal, owner, migrations, primary, max_age_s, plan,
                         manual=manual, preview=preview)


def _execute_swap(core: Path, info: dict, pairs: list[_RestorePair], journal: dict | None,
                  owner: tuple[int, int] | None, migrations: Path, primary: str | None,
                  max_age_s: float, plan: tuple, *, manual: str | None = None,
                  preview: dict | None = None) -> dict:
    marker = core / STAGE_MARKER
    aside, state, moves, step, continuing = plan
    root, originals = aside.parent, state["originelen"]
    if manual is not None and preview:
        if manual != preview["bevestig"]:
            return preview
        state.update(keuze=manual, keuzestap=step)
        if not continuing:
            state["journaal"] = _checksum(journal)
    if not continuing or (manual is not None and preview):
        _publish_step(marker, info, state, owner)
    reference = {"fasebestand": state["poging"]}
    if journal != reference and marker.exists():
        _publish(marker, json.dumps({**info, "omwisseling": reference}), owner)

    def verify_step(position: int) -> None:
        if _read_proof(core / COMMIT_ORIGIN) != {**state, "fase": "omwisseling", "aanvraag": info}:
            raise SetError("het fasebestand is tijdens de omwisseling gewijzigd")
        if marker.exists():
            current = _request(core)
            if current != {**info, "omwisseling": reference}:
                raise SetError("de aanvraag is tijdens de omwisseling gewijzigd")
        elif position != len(moves):
            raise SetError("de aanvraag voor de onvoltooide omwisseling ontbreekt")
        if "keuze" in state and position == state["keuzestap"]:
            fresh = {}
            _, _, current_moves, current_step, _ = _swap_plan(
                core, info, pairs, reference, migrations, max_age_s, core, primary=primary,
                manual=True, preview=fresh)
            if current_step != position or not fresh:
                raise SetError("de bestandsposities veranderden vóór de bevestigde wissel")
            if fresh["bevestig"] != state["keuze"] or current_moves != moves:
                raise _ChoiceRequired(fresh)
        else:
            _check_position(core, core, pairs, aside, originals, moves, position, migrations,
                            primary, state["databases"])

    verify_step(step)
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
        verify_step(index - 1)
        # Een herbouwbaar lezersregister kan sinds de vorige publicatie verdwenen zijn.
        if not src.name.endswith(("-shm", "-wal")) or os.path.lexists(src):
            os.replace(src, dst)
        _sync(dst.parent)
        _sync(src.parent)
        state["stap"] = index
        _publish_step(marker, info, state, owner)
    verify_step(len(moves))
    _chown([root, aside, *aside.iterdir(), *(p.live for p in pairs)], owner)
    result = {"ok": True, "set": info["set"], "at": _now(), "files": [p.rel for p in pairs],
              "previous": str(aside)}
    if marker.exists():
        _write_result(core, result, owner)
        _forget(marker)
    finish_commit(core)
    return result


def _apply_staged(core_dir: Path, owner: tuple[int, int] | None, max_age_s: float,
                  av1_migrations: Path, keep_on_error: bool) -> dict[str, Any] | None:
    info = _request(core_dir)
    if info is None:
        phase = _read_proof(core_dir / COMMIT_ORIGIN)
        if not phase or phase.get("fase") != "omwisseling":
            return None
        if (core_dir / STAGE_MARKER).exists():
            raise SetError("de terugzetaanvraag is onleesbaar")
        info = phase["aanvraag"]
    begun = "omwisseling" in info
    try:
        pairs, journal = _restore_request(core_dir, info)
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


def _restore_request(core: Path, info: dict) -> tuple[list[_RestorePair], dict | None]:
    begun = "omwisseling" in info
    journal = info.pop("omwisseling", None)
    if begun and not isinstance(journal, dict):
        raise SetError("ongeldige voortgangsmarkering voor terugzetten")
    return _restore_pairs(core, info), journal


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
    if isinstance(result, dict) and result.get("ok"):
        phase = _read_proof(core_dir / COMMIT_ORIGIN)
        if (core_dir / STAGE_MARKER).exists() or (phase and phase.get("fase") == "omwisseling"):
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


_ENCODER_PROGRESS_TABLES = {
    1: {"library", "bestand", "besluit", "job", "verificatie", "vervang_transactie", "scan_run"},
    10: {"job_poging"},
    16: {"trial_run", "encoding_trials", "quality_measurements"},
    26: {"scan_bijdrage"},
}


def encoder_copy_versions(db: sqlite3.Connection, migrations: Path, *,
                          primary: bool = False) -> set[int]:
    have = schema.versions_in(db, "schema_versie", "versie")
    if have - _encoder_versions(migrations):
        raise schema.SchemaTooNew()
    if primary:
        tables = {row[0] for row in db.execute("SELECT name FROM sqlite_schema WHERE type='table'")}
        if 1 not in have or any(version in have and not required <= tables
                                for version, required in _ENCODER_PROGRESS_TABLES.items()):
            raise _PreviousDataError("Voortgangstabellen ontbreken in de primaire AV1-back-up")
    return have


def primary_encoder_db(folder: Path) -> Path | None:
    configured = os.environ.get("DATABASE_URL")
    if not configured:
        return folder / "app.db"
    if not configured.startswith("sqlite:///"):
        return None
    path = Path(configured.removeprefix("sqlite:///"))
    if path.is_absolute() and path.parent.resolve() == folder.resolve() and path.suffix == ".db":
        return path
    return None


def encoder_backup_dir(core: Path) -> Path:
    if configured := os.environ.get("MG_SCHEMA_ENCODER_DIR"):
        return Path(configured)
    return Path(os.environ.get("MG_AIO_CONFIG") or core.parent) / "encode"


@dataclass
class UpdateCommits:
    encoders: dict[Path, sqlite3.Connection]
    marker: Path | None = None
    origin: str | None = None
    targets: dict | None = None
    archive: Path | None = None

    def begin(self, core: Path, archive: Path, conn: sqlite3.Connection) -> None:
        # De verbindingen zien hun definitieve inhoud in dezelfde nog open schrijftransactie.
        self.targets = {f"{core.name}/mediaguard.db": _logical_state(conn)}
        self.targets.update({f"encode/{path.name}": _logical_state(encoder)
                             for path, encoder in self.encoders.items()})
        self.marker = core / COMMIT_ORIGIN
        self.archive = archive
        self.record("voorbereid")

    def record(self, step: str) -> None:
        if self.marker is None or self.targets is None or self.archive is None:
            raise SetError("de herstelset voor de databasecommit is niet vastgelegd")
        state = _read_proof(self.marker)
        if (not state or state.get("poging") != self.origin or state.get("set") != self.archive.name
                or state.get("doelen", self.targets) != self.targets):
            raise SetError("het fasebestand hoort niet bij deze update")
        core = self.marker.parent
        committed = list(state.get("vastgelegd", []))
        if step != "voorbereid":
            committed.append(f"{core.name}/mediaguard.db" if step == "kern vastgelegd" else step)
        for old in state["databases"]:
            rel = old["bestand"]
            expected = self.targets[rel] if rel in committed else old["stand"]
            if _source_state(core, rel, strict_wal=False)["stand"] != expected:
                raise SetError("de laatste gecommitteerde stand wijkt af van het fasebestand")
        _write_phase(core, {**state, "fase": "migratie", "stap": step, "doelen": self.targets,
                            "vastgelegd": committed})


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


def _source_state(core: Path, rel: str, *, strict_wal: bool = True, record_content: bool = False,
                  work: Path | None = None) -> dict:
    main = core.parent / rel
    observed = None
    for _ in range(2):
        generation = _file_generation(main)
        try:
            with checked_database(work or core, database_files(main),
                                  strict_wal=strict_wal) as (conn, wal):
                stand = _logical_state(conn)
                proof = {}
                if record_content:
                    proof["opvolging"] = content_proof(conn, stand["tabellen"])
                    if wal.frames and wal.cycle is not None:
                        proof["wal_cyclus"] = list(wal.cycle)
                digest = _regular_digest(main)
        except (OSError, sqlite3.Error):
            if _file_generation(main) == generation:
                raise
            continue
        # Een checkpoint kan de WAL naar de hoofddatabase verplaatsen tijdens het
        # kopiëren. Accepteer een volgende, stabiele kopie met dezelfde SQL-inhoud.
        if observed is not None and stand != observed:
            break
        if _file_generation(main) == generation:
            return {"bestand": rel, "kern": digest, "hoofdbestand_id": generation[0][:2],
                    "wal_frames": wal.frames,
                    "wal_positie": wal.end, "wal_sha256": wal.sha256, "inhoud": stand["inhoud"],
                    "integriteit": "ok", "stand": stand, **proof}
        observed = stand
    raise _SourceChanged("de database veranderde tijdens het meten van de commit")


def _bind_archive(core: Path, archive: Path, *, phase: str = "aanbod") -> str:
    entries = _update_entries(core, archive)
    meta = _read_meta(archive) or {}
    generation = meta.get("source_generation")
    main, encode = core / "mediaguard.db", schema.encoder_dir(core)
    if _source_generation(main, encode) != generation:
        raise _SourceChanged("de bronbestanden zijn sinds de herstelset gewijzigd")
    try:
        databases = [_source_state(core, rel, record_content=True) for rel in entries]
    except (OSError, sqlite3.Error) as exc:
        if _source_generation(main, encode) != generation:
            raise _SourceChanged("de bronbestanden zijn tijdens het kopiëren gewijzigd") from exc
        raise
    intent = _read_proof(core / COMMIT_ORIGIN)
    attempt = uuid4().hex
    if intent and intent.get("fase") == "archiveren":
        if (intent["set"] != archive.name or _stands(databases) != _stands(intent["databases"])):
            raise SetError("de gekozen herstelset wijkt af van de vooraf vastgelegde stand")
        attempt = intent["poging"]
    _verify_archived_stands(core, entries, _stands(databases))
    state = {"set": archive.name, "poging": attempt, "fase": phase, "manifest": _checksum(meta),
             "archieven": sorted(_archive_names(archive.parent)), "aantal": len(entries),
             "databases": databases, "oude_markering": (intent or {}).get("oude_markering")}
    if _source_generation(main, encode) != generation:
        raise _SourceChanged("de bronbestanden zijn tijdens de bewijscontrole gewijzigd")
    _write_phase(core, state)
    return state["poging"]


def record_commit(core: Path, archive: Path) -> str:
    """Bind onder alle schrijfsloten de originele stand vóór de eerste migratie."""
    marker = core / COMMIT_MARKER
    if os.path.lexists(marker):
        phase = _read_proof(core / COMMIT_ORIGIN)
        if not phase or phase.get("fase") != "archiveren" or phase["set"] != archive.name:
            raise SetError("er staat al een onderbroken update klaar")
    origin = _bind_archive(core, archive, phase="oorsprong")
    _publish(marker, archive.name)
    return origin


def finish_commit(core: Path) -> None:
    # De bewijzen blijven beschikbaar totdat de startmarkering duurzaam verdwenen is.
    for name in (COMMIT_MARKER, COMMIT_STEP, COMMIT_ORIGIN):
        if (core / name).exists():
            _forget(core / name)


def _read_proof(path: Path) -> dict | None:
    try:
        if not stat.S_ISREG(path.lstat().st_mode):
            raise SetError("het fasebestand is geen regulier bestand")
        state = json.loads(path.read_bytes())
    except FileNotFoundError:
        return None
    except (UnicodeError, ValueError) as exc:
        raise SetError("de onderbroken update heeft een onleesbaar bewijs") from exc
    if not isinstance(state, dict) or state.pop("controle", None) != _checksum(state):
        raise SetError("de onderbroken update heeft een beschadigd bewijs")
    return state


def _stands(databases: list[dict]) -> dict:
    return {item["bestand"]: item["stand"] for item in databases}


def _verify_archived_stands(core: Path, entries: dict, expected: dict) -> None:
    for rel, (path, _, _) in entries.items():
        with checked_database(core, {"": path}) as (copy, _):
            if _logical_state(copy) == expected[rel]:
                continue
            # Een historische sleutel kan in het archief door een verwijzing zijn vervangen.
            ref = _cursor_value(copy) if rel == f"{core.name}/mediaguard.db" else None
            if isinstance(ref, str) and ref.startswith(event_keys.LEGACY_PREFIX):
                with contextlib.closing(sqlite3.connect(":memory:")) as original:
                    copy.backup(original)
                    original.execute("UPDATE outbox_retention SET cursor_key=? WHERE singleton=1",
                                     (event_keys.load_legacy(core, ref),))
                    if _logical_state(original) == expected[rel]:
                        continue
            raise SetError("de herstelset mist de bewezen gecommitteerde inhoud")


def _contains_previous(conn: sqlite3.Connection, old: dict) -> bool:
    if _logical_state(conn) == old["stand"]:
        return True
    return "opvolging" in old and contains_commit(conn, old["opvolging"], old["stand"]["tabellen"])


def _later_cycle(previous, current) -> bool:
    if previous is None or current is None:
        return False
    # SQLite verhoogt zowel het checkpointnummer als salt-1 bij hergebruik van de WAL.
    distance = (current[0] - previous[0]) % (1 << 32)
    return (0 < distance < 1 << 31 and (current[1] - previous[1]) % (1 << 32) == distance
            and current[2] != previous[2])


def _verify_successor(core: Path, work: Path, old: dict, new: dict) -> None:
    # Vergelijk de huidige commit en zijn mogelijke voorganger op precies dezelfde bytes.
    with copied_family(work, database_files(core.parent / old["bestand"])) as family, \
            checked_database(work, family) as (current, wal):
        if _logical_state(current) != new["stand"]:
            raise SetError("de gecommitteerde inhoud veranderde tijdens de controle")
        positions = old["stand"]["tabellen"]
        # Een oudere schrijver kan zijn laatste taakwijziging al in het hoofdbestand
        # hebben vastgelegd. Vergelijk dan elke taak op identiteit en eigen voortgang.
        main_only = (not old["wal_frames"] and not wal.frames
                     and old.get("hoofdbestand_id") == new["hoofdbestand_id"])
        if not old["wal_frames"] and "opvolging" in old and contains_commit(
                current, old["opvolging"], positions, advanced_tasks=main_only):
            return
        if wal.frames:
            end = old["wal_positie"]
            if (old["wal_frames"] and wal.end >= end
                    and wal_prefix_hash(family["-wal"], end) == old["wal_sha256"]):
                with checked_database(work, family, wal_end=end) as (db, _):
                    if _contains_previous(db, old):
                        return
            if not old["wal_frames"] or _later_cycle(old.get("wal_cyclus"), wal.cycle):
                # Een oude, leesbare WAL kan de stand in een nieuwer hoofdbestand terugdraaien.
                # Toets ook de inhoud ná het afspelen: elke bestaande taak behoudt haar identiteit.
                base = {tail: path for tail, path in family.items() if tail != "-wal"}
                with checked_database(work, base) as (db, _):
                    if (_logical_state(db) == old["stand"]
                            or (not old["wal_frames"] and _contains_previous(db, old))):
                        proof = content_proof(db, positions, encoder_metrics=True)
                        if contains_commit(current, proof, positions, replayed=True):
                            return
    raise SetError("de eerder bewezen gecommitteerde inhoud ontbreekt")


def _verify_progress(core: Path, previous: list[dict], databases: list[dict], *,
                     work: Path | None = None) -> None:
    actual = {db["bestand"]: db for db in databases}
    for old in previous:
        new = actual.get(old["bestand"])
        if not new:
            raise SetError("een eerder bewezen database ontbreekt")
        if old["stand"] == new["stand"]:
            continue
        if any(new["stand"].get("volgnummers", {}).get(name, -1) < value
               for name, value in old["stand"].get("volgnummers", {}).items()):
            raise SetError("een eerder bewezen SQLite-volgnummer ontbreekt")
        for table, position in old["stand"]["tabellen"].items():
            now = new["stand"]["tabellen"].get(table)
            if (not now or now["rijen"] < position["rijen"] or
                    (position["hoogste"] is not None and
                     (now["hoogste"] is None or now["hoogste"] < position["hoogste"]))):
                raise SetError("een eerder bewezen rij of event ontbreekt")
        _verify_successor(core, work or core, old, new)


def _check_preparation(core: Path, attempt: str | None, *, work: Path | None = None) -> dict:
    try:
        phase = _read_proof(core / COMMIT_ORIGIN)
        if not phase or phase.get("fase") != "klaarzetten" or phase["poging"] != attempt:
            raise SetError("de aanvraag mist haar vooraf vastgelegde stand")
        current = [_source_state(core, item["bestand"], work=work) for item in phase["databases"]]
        _verify_progress(core, phase["databases"], current, work=work)
        return {db["bestand"]: db["inhoud"] for db in current}
    except (SetError, OSError, sqlite3.Error) as exc:
        raise _PreviousDataError(f"de stand van vóór het klaarzetten is onbewezen: {exc}") from exc


def _completed_legacy_restore(core: Path, phase: dict) -> bool:
    """Controleer de standen na een door een oude supervisor afgehandelde aanvraag."""
    request = phase.get("aanvraag")
    if not request or os.path.lexists(core / STAGE_MARKER):
        return False
    for name in (RESULT_FILE, RESULT_FILE + ".gemeld"):
        try:
            result = json.loads((core / name).read_bytes())
            if (result["ok"] is not True or result["set"] != phase["set"]
                    or result["files"] != [f["live"] for f in request["files"]]
                    or datetime.fromisoformat(result["at"]) < datetime.fromisoformat(
                        request["requested_at"])):
                continue
            aside = Path(result["previous"])
        except (OSError, KeyError, ValueError, TypeError):
            continue
        if (aside.parent != core / "backups" / ASIDE_FOLDER or not _ASIDE_NAME.fullmatch(aside.name)
                or not aside.is_dir() or aside.resolve() != aside.absolute()):
            continue
        # De oude succesmelding wijst uitsluitend de map aan. Meet beide kanten opnieuw.
        for old in phase["databases"]:
            saved = aside / old["bestand"].replace("/", "__")
            state = _source_state(core, saved.relative_to(core.parent).as_posix())
            if state["stand"] != old["stand"]:
                raise SetError("de vorige database mist de vóór het klaarzetten bewezen stand")
        current = [_source_state(core, db["bestand"]) for db in phase["gekozen"]]
        if _stands(current) != _stands(phase["gekozen"]):
            raise SetError("de geïnstalleerde gegevens wijken af van de expliciet gekozen stand")
        return True
    return False


def _archive_intent(main: Path, encode: Path | None, name: str,
                    owner: tuple[int, int] | None) -> None:
    core = main.parent
    sources = [f"{core.name}/{main.name}"]
    if encode is not None:
        sources.extend(f"encode/{p.name}" for p in sorted(encode.glob("*.db")))
    generation = _source_generation(main, encode)
    try:
        databases = [_source_state(core, rel, record_content=True) for rel in sources]
    except (OSError, sqlite3.Error) as exc:
        if _source_generation(main, encode) != generation:
            raise _SourceChanged("de bron veranderde tijdens de fasemeting") from exc
        raise
    if _source_generation(main, encode) != generation:
        raise _SourceChanged("de bronbestanden zijn tijdens de fasemeting gewijzigd")
    marker = core / COMMIT_MARKER
    previous = _read_proof(core / COMMIT_ORIGIN)
    if previous and previous.get("fase") in ("archiveren", "oorsprong", "migratie"):
        _verify_progress(core, previous["databases"], databases)
    _write_phase(core, {"fase": "archiveren", "set": name, "poging": uuid4().hex,
                        "databases": databases, "aantal": len(databases),
                        "archieven": sorted(_archive_names(core / "backups" / FOLDER)),
                        "oude_markering": marker.read_bytes().decode("ascii", "replace")
                        if marker.exists() else None}, owner)


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


def _logical_state(conn: sqlite3.Connection) -> dict:
    """Tel de rijen en hun hoogste rij-/eventnummer in dezelfde stand als de inhoudshash."""
    digest = hashlib.sha256()
    tables_state = {}

    def feed(row: tuple) -> None:
        digest.update(row_bytes(row))

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
        fields, highest = "*", "NULL"
        if not without_rowid:
            rowid = next((r for r in ("rowid", "_rowid_", "oid") if r not in columns), None)
            if rowid is None:
                raise SetError("de rij-identiteit van de oorspronkelijke database is onbewezen")
            fields = rowid + ",*"
            highest = f"MAX({rowid})"
        count, position = conn.execute(
            f"SELECT COUNT(*), {highest} FROM {quoted}"  # noqa: S608
        ).fetchone()
        tables_state[name] = {"rijen": count, "hoogste": position}
        # Een tabelscan leest de B-boom op rowid of, bij WITHOUT ROWID, op primaire sleutel.
        for row in conn.execute(f"SELECT {fields} FROM {quoted} NOT INDEXED"):  # noqa: S608
            feed(row)
    state = {"tabellen": tables_state, "inhoud": digest.hexdigest()}
    if "sqlite_sequence" in tables_state:
        state["volgnummers"] = dict(conn.execute("SELECT name, seq FROM sqlite_sequence"))
    return state


def _contents_hash(conn: sqlite3.Connection) -> str:
    return _logical_state(conn)["inhoud"]


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
    origin = _read_proof(core / COMMIT_ORIGIN)
    if origin and origin.get("fase") == "klaarzetten":
        if _completed_legacy_restore(core, origin):
            if finish:
                finish_commit(core)
            return None
        _check_preparation(core, origin["poging"])
        update = origin["update"]
        if finish and not (core / STAGE_MARKER).exists():
            if update is None:
                _forget(core / COMMIT_ORIGIN)
            else:
                _write_phase(core, update)
        origin = update
    if origin is None and (core / COMMIT_STEP).exists():
        raise SetError("de onderbroken update mist haar oorsprongsbewijs")
    try:
        name = marker.read_bytes().decode("ascii", errors="replace")
    except FileNotFoundError:
        if origin is None or origin.get("fase") == "aanbod":
            return None
        name = ""
    folder = core / "backups" / FOLDER
    bound = origin.get("set") if origin is not None else name
    if not isinstance(bound, str) or not _SET_NAME.fullmatch(bound):
        raise SetError("de herstelset voor deze onderbroken update is onbewezen")
    competing = not bound.startswith(name) or (_SET_NAME.fullmatch(name) and name != bound)
    if (name != (origin or {}).get("oude_markering") and name.startswith(SET_PREFIX)
            and competing):
        raise SetError("de setnaam voor deze onderbroken update spreekt het bewijs tegen")
    archive = folder / bound
    if origin and origin.get("fase") == "archiveren":
        if _archive_names(folder) - {bound, *origin["archieven"]}:
            raise SetError("de onderbroken update heeft een strijdige of recentere set")
        paths = [f"{core.name}/mediaguard.db",
                 *(f"encode/{p.name}" for p in sorted(schema.encoder_dir(core).glob("*.db")))]
        expected = _stands(origin["databases"])
        if _stands([_source_state(core, rel) for rel in paths]) != expected:
            raise SetError("de onderbroken update heeft een onbewezen oorspronkelijke stand")
        if archive.exists():
            _verify_archived_stands(core, _update_entries(core, archive), expected)
        return None
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
            initial = origin["databases"]
            if "fase" in origin:
                if origin["fase"] not in ("aanbod", "oorsprong", "migratie"):
                    raise SetError("onbekende of onafgehandelde herstelfase")
                reached = _stands(current)
                before = _stands(initial)
                matches = reached == before and all(
                    reached[rel] == origin["doelen"][rel] for rel in origin.get("vastgelegd", [])
                )
                matches |= reached == origin.get("doelen")
            else:
                # Een oudere schrijver bond de bestandsfamilie aan inhoud én WAL-prefix.
                matches = all(all(actual.get(k) == v for k, v in old.items())
                              for old, actual in zip(initial, current, strict=True))
                targets = _read_proof(core / COMMIT_STEP)
                if targets is not None:
                    if targets.get("set") != bound or targets.get("oorsprong") != _checksum(origin):
                        raise SetError("de commitstap hoort bij een andere onderbroken update")
                    for old, actual in zip(initial, current, strict=True):
                        if old["wal_frames"] and old["kern"] == actual["kern"]:
                            wal = core.parent / (actual["bestand"] + "-wal")
                            end = old["wal_positie"]
                            if (actual["wal_positie"] < end
                                    or wal_prefix_hash(wal, end) != old["wal_sha256"]):
                                raise SetError("de gecommitteerde WAL-stand wijkt af")
                    matches |= targets.get("doelinhoud") == {
                        item["bestand"]: item["inhoud"] for item in current}
    except (SetError, OSError, sqlite3.Error) as exc:
        raise SetError(f"de onderbroken update heeft een onbewezen oorspronkelijke stand: "
                       f"{exc}") from exc
    if not matches:
        raise SetError(f"update deels vastgelegd of oorsprong onbewezen; "
                       f"zet de geverifieerde set {bound} terug")
    if finish and (not origin or "fase" not in origin or
                   (origin.get("doelen") and _stands(current) == origin["doelen"])):
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


def check_offline_schema(path: Path) -> None:
    _core_pending(path, MIGRATIONS_DIR, path.parent)
    _encoder_pending(schema.encoder_dir(path.parent), encoder_migrations(os.environ),
                     work=path.parent)


def resume_standalone_start(core: Path, migrations: Path) -> Path | None:
    """Een losse kern kan de vroege archivering van alleen haar eigen database hervatten."""
    try:
        if (core / STAGE_MARKER).exists() and (_request(core) is None or swapping(core)):
            raise SetError(MANUAL_CHOICE if manual_choice_pending(core)
                           else "hervat de onderbroken omwisseling met de supervisor")
        phase = _read_proof(core / COMMIT_ORIGIN)
        if phase and phase.get("fase") == "klaarzetten":
            if _completed_legacy_restore(core, phase):
                return
            _check_preparation(core, phase["poging"])
            phase = phase["update"]
        if (core / COMMIT_MARKER).exists() or (phase and phase.get("fase") != "aanbod"):
            _interrupted_commit(core, finish=False)
            if (phase and phase.get("fase") == "archiveren" and core.name == "core"
                    and phase["aantal"] == 1 and phase["oude_markering"] is None
                    and not (core / COMMIT_MARKER).exists() and not (core / STAGE_MARKER).exists()):
                from mediaguard import __version__

                try:
                    archive = snapshot_before_start(
                        core.parent, core_migrations=migrations, encoder_migrations=None,
                        app_version=__version__, migrate_core=True,
                    )
                except (SetError, OSError, sqlite3.Error) as exc:
                    raise SetError(f"back-up vóór migratie mislukt: {exc}") from exc
                if archive is not None:
                    return archive
            raise SetError("hervat de onderbroken update met de supervisor")
        return None
    except SetError as exc:
        raise RuntimeError(str(exc)) from exc


def requires_inspection(config_root: Path) -> bool:
    core = config_root / "core"
    return any(os.path.lexists(core / name) for name in (
        "mediaguard.db", COMMIT_ORIGIN, COMMIT_MARKER, COMMIT_STEP, STAGE_MARKER,
    )) or any((config_root / "encode").glob("*.db"))


def inspect_schema(config_root: Path, *, core_migrations: Path = MIGRATIONS_DIR,
                   encoder_migrations: Path | None) -> tuple[tuple[str, str] | None, bool]:
    core = config_root / "core"
    try:
        _interrupted_commit(core, finish=False)
    except SetError as exc:
        raise RuntimeError(str(exc)) from exc
    return _pending_schema(config_root, core_migrations, encoder_migrations)


def _pending_schema(config_root: Path, core_migrations: Path,
                    encoder_migrations: Path | None) -> tuple[tuple[str, str] | None, bool]:
    core = config_root / "core"
    work = core if core.is_dir() else config_root
    return (_core_pending(core / "mediaguard.db", core_migrations, work),
            _encoder_pending(config_root / "encode", encoder_migrations, work=work))


def config_locks(config_root: Path) -> contextlib.AbstractContextManager[None]:
    return schema.startup_locks(config_root / "core", config_root / "encode")


def _previous_version(core_db: Path) -> str | None:
    try:
        with checked_database(core_db.parent, database_files(core_db)) as (conn, _):
            row = conn.execute("SELECT value_json FROM system_state WHERE key='app.version'"
                               ).fetchone()
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
        core, enc = _pending_schema(config_root, core_migrations, encoder_migrations)
        marker = core_db.parent / UPGRADE_SET

        def prepare_files() -> None:
            if migrate_core:
                event_keys.prepare_for_start(core_db, event_keys.key_path(core_db.parent), owner)
            _protect_old_manifests(core_db.parent / "backups" / FOLDER)
            _clean_marker_parts(core_db.parent)
            if marker.exists():
                _forget(marker)

        if core is None and not enc:
            prepare_files()
            if previous is not None:
                finish_commit(core_db.parent)
            if migrate_core:
                with contextlib.closing(Database(core_db, offline_guard=True)) as db:
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
                                    reason="update", owner=owner, supervised=True,
                                    prepare=prepare_files)
                if not migrate_core:
                    _bind_archive(core_db.parent, archive)
                break
            except _SourceChanged:
                # Ook een normaal checkpoint verandert de fysieke stand. Begin vóór
                # de migratie één keer opnieuw met een verse set en haar eigen bewijs.
                if attempt:
                    raise
        if migrate_core:
            writer = Database(core_db, synchronous="FULL", offline_guard=True)
            with contextlib.closing(writer) as db:
                archive = db.initialize(pre_update_set=archive,
                                        encoder_migrations=encoder_migrations if enc else None)
                db.require_writable()
                if archive is None:
                    raise SetError("er is geen geverifieerde herstelset voor deze update")
        elif core:
            _publish(marker, archive.name)
        prune_sets(archive.parent, protected={archive})
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
    restore.add_argument("--bevestig-stand", help="Bevestig de getoonde historische wissel")
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
                phase = _read_proof(core / COMMIT_ORIGIN)
                if manual_choice_pending(core) or (info and phase and "keuze" in phase):
                    assert info is not None
                    pairs = _restore_pairs(core, info)
                    request = _request(core) or {}
                    try:
                        result = _swap(core, info, pairs, request.get("omwisseling"), None,
                                       encoder_migrations(os.environ), STAGE_MAX_AGE_S,
                                       manual=a.bevestig_stand or "")
                    except _ChoiceRequired as exc:
                        result = exc.preview
                    if result.get("ok"):
                        print(f"De gekozen set {a.set} is teruggezet. "
                              f"Vorige stand: {result['previous']}")
                        return 0
                    print(MANUAL_CHOICE)
                    print(f"Bewaarmap voor de vorige databases: {result['bewaarmap']}")
                    for group in ("vorige", "gekozen"):
                        for rel, value in result[group].items():
                            tables = value["tabellen"]
                            events = tables.get("outbox_events", {})
                            print(f"{group} {rel}: integriteit {value['integriteit']}, "
                                  f"{sum(t['rijen'] for t in tables.values())} rijen, "
                                  f"hoogste event {events.get('hoogste')}, "
                                  f"voortgang {value.get('voortgang')}, inhoud {value['inhoud']}")
                    print(f"Bevestig deze keuze met --bevestig-stand {result['bevestig']}")
                    return 1
                if a.bevestig_stand:
                    raise SetError("de getoonde historische aanvraag is niet meer aanwezig")
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
