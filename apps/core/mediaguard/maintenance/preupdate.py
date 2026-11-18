"""Back-ups van vóór een update: één set per update, terug te zetten vanuit de app.

Een set is een map ``/config/core/backups/voor-update/voor-update-<tijd>/`` met:

- ``mediaguard.db``: consistente kopie van de core-database;
- ``encode/<naam>.db``: de database(s) van de AV1-engine (alleen in de AIO);
- ``meta.json``: versies, schema's, checksums en het resultaat van de hersteltest.

In de alles-in-één-container maakt de supervisor de set vóórdat er één dienst start, en
alleen als core of de AV1-engine een databasemigratie heeft openstaan. Zo horen de twee
databases bij elkaar en is geen van beide al gemigreerd. Zonder supervisor maakt core zelf
een set (alleen de core-database) vlak vóór zijn migratie.

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
import time
from collections.abc import Mapping
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from mediaguard import schema
from mediaguard.db import MIGRATIONS_DIR, Database, check_schema_versions

FOLDER = "voor-update"
SET_PREFIX = "voor-update-"
ASIDE_FOLDER = "voor-terugzetten"
STAGE_MARKER = "terugzetten.json"
RESULT_FILE = "terugzetten-resultaat.json"
STAGED_SUFFIX = ".terugzetten"
KEEP_SETS = 5
PARTIAL_GRACE_S = 3600
STAGE_MAX_AGE_S = 24 * 3600  # daarna niet meer omwisselen (geen verrassing weken later)
_SET_NAME = re.compile(r"^voor-update-\d{8}T\d{6}Z(-\d+)?$")
_LEGACY_NAME = re.compile(r"^mediaguard-[\w.-]+-naar-[\w.-]+-\d{8}T\d{6}Z\.db$")
_ENC_VERSION = re.compile(r"^(\d+)_")


class SetError(Exception):
    def __init__(self, message: str, *, disk: bool = False) -> None:
        super().__init__(message)
        self.disk = disk


def _now() -> str:
    return datetime.now(UTC).isoformat(timespec="seconds")


def _sha256(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def _chown(paths: list[Path], owner: tuple[int, int] | None) -> None:
    if owner is None or os.geteuid() != 0:
        return
    for p in paths:
        with contextlib.suppress(OSError):
            os.chown(p, owner[0], owner[1])


def _copy_sqlite(src: Path, dest: Path) -> dict[str, Any]:
    """Kopie via de backup-API (WAL-veilig) + hersteltest; nooit een half bestand."""
    part = dest.with_name(dest.name + ".part")
    try:
        s = sqlite3.connect(f"file:{src}?mode=ro", uri=True, timeout=30)
        d = sqlite3.connect(part)
        try:
            s.backup(d)
        finally:
            d.close()
            s.close()
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
    except SetError:
        part.unlink(missing_ok=True)
        raise
    return {"sha256": _sha256(dest), "size": dest.stat().st_size}


def make_set(folder: Path, core_db: Path, *, encode_dir: Path | None,
             from_schema: str | None = None, to_schema: str | None = None,
             from_version: str | None = None, to_version: str | None = None,
             reason: str = "migratie", owner: tuple[int, int] | None = None) -> Path:
    """Maak een complete set (eerst als ``.part``-map, pas daarna de echte naam)."""
    peer = encode_dir or schema.encoder_dir(core_db.parent)
    with schema.startup_locks(core_db.parent, peer):
        Database(core_db).require_writable()
        if peer != schema.encoder_dir(core_db.parent):
            check_encoder_schema(peer)
        return _make_set(folder, core_db, encode_dir=encode_dir, from_schema=from_schema,
                         to_schema=to_schema, from_version=from_version, to_version=to_version,
                         reason=reason, owner=owner)


def _make_set(folder: Path, core_db: Path, *, encode_dir: Path | None,
              from_schema: str | None, to_schema: str | None, from_version: str | None,
              to_version: str | None, reason: str, owner: tuple[int, int] | None) -> Path:
    folder.mkdir(parents=True, exist_ok=True)
    _chown([folder.parent, folder], owner)
    base = SET_PREFIX + datetime.now(UTC).strftime("%Y%m%dT%H%M%SZ")
    name, n = base, 1
    while (folder / name).exists():
        n += 1
        name = f"{base}-{n}"
    work = folder / (name + ".part")
    shutil.rmtree(work, ignore_errors=True)
    work.mkdir(parents=True)
    files: list[dict[str, Any]] = []
    try:
        files.append({"name": "mediaguard.db", "live": f"{core_db.parent.name}/{core_db.name}",
                      **_copy_sqlite(core_db, work / "mediaguard.db")})
        encoder_problem = None
        if encode_dir is not None and encode_dir.is_dir():
            (work / "encode").mkdir()
            for db in sorted(encode_dir.glob("*.db")):
                try:
                    files.append({"name": f"encode/{db.name}", "live": f"encode/{db.name}",
                                  **_copy_sqlite(db, work / "encode" / db.name)})
                except SetError as exc:
                    encoder_problem = str(exc)
                    if exc.disk:
                        raise
        meta = {"kind": "pre-update", "name": name, "created_at": _now(), "reason": reason,
                "from_version": from_version, "to_version": to_version,
                "from_schema": from_schema, "to_schema": to_schema, "files": files,
                "encoder_included": any(f["name"].startswith("encode/") for f in files),
                "encoder_problem": encoder_problem, "integrity_ok": True}
        (work / "meta.json").write_text(json.dumps(meta, indent=2), encoding="utf-8")
        os.replace(work, folder / name)
    except BaseException:
        shutil.rmtree(work, ignore_errors=True)
        raise
    done = folder / name
    _chown([done, *done.rglob("*")], owner)
    prune_sets(folder)
    return done


def prune_sets(folder: Path, keep: int = KEEP_SETS, now: float | None = None) -> int:
    """Alleen complete sets tellen mee; halve ``.part``-mappen verdwijnen na een uur."""
    now = time.time() if now is None else now
    removed = 0
    complete = sorted(p for p in folder.glob(SET_PREFIX + "*")
                      if p.is_dir() and _SET_NAME.match(p.name) and (p / "meta.json").is_file())
    for old in complete[:-keep]:
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
    try:
        meta = json.loads((set_dir / "meta.json").read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None
    return meta if isinstance(meta, dict) else None


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


def verify(path: Path) -> list[str]:
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
                        av1_migrations: Path) -> None:
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
        uri = f"{path.resolve().as_uri()}?mode=ro&immutable=1"
        with contextlib.closing(sqlite3.connect(uri, uri=True)) as db:
            if db.execute("PRAGMA quick_check").fetchone()[0] != "ok":
                raise SetError(f"{path.name} is beschadigd")
            if rel.startswith("encode/"):
                have = schema.versions_in(db, "schema_versie", "versie")
                if have - _encoder_versions(av1_migrations):
                    raise schema.SchemaTooNew()
            else:
                check_schema_versions(path, connection=db)
    except (OSError, sqlite3.Error) as exc:
        raise SetError(f"{path.name} is onleesbaar: {exc}") from exc


def _manifest(name: str, files: list[dict[str, Any]]) -> str:
    return hashlib.sha256(json.dumps([name, files], sort_keys=True).encode()).hexdigest()


def stage(core_dir: Path, path: Path, *, actor: str) -> dict[str, Any]:
    """Zet de kopieën klaar naast de echte databases; de supervisor wisselt ze om."""
    db = Database(core_dir / "mediaguard.db")
    with db.startup_lock():
        db.require_writable()
        return _stage(core_dir, path, actor)


def _stage(core_dir: Path, path: Path, actor: str) -> dict[str, Any]:
    config_root = core_dir.parent
    pairs = _set_files(core_dir, path)
    migrations = encoder_migrations(os.environ)
    for rel, (src, size, digest) in pairs.items():
        _check_restore_copy(src, rel, size, digest, migrations)
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
            part = dest.with_name(dest.name + ".part")
            shutil.copyfile(src, part)
            os.replace(part, dest)
            staged.append({"live": live, "staged": live + STAGED_SUFFIX,
                           "size": size, "sha256": digest})
    except OSError as exc:
        for s in staged:
            with contextlib.suppress(OSError):
                _within(config_root, s["staged"]).unlink()
        raise SetError(f"klaarzetten mislukt: {exc}", disk=True) from exc
    info = {"set": path.name, "requested_at": _now(), "by": actor, "files": staged,
            "manifest": _manifest(path.name, staged)}
    marker = core_dir / STAGE_MARKER
    tmp = marker.with_name(marker.name + ".part")
    tmp.write_text(json.dumps(info, indent=2), encoding="utf-8")
    os.replace(tmp, marker)
    return info


def pending(core_dir: Path) -> dict[str, Any] | None:
    try:
        info = json.loads((core_dir / STAGE_MARKER).read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None
    return info if isinstance(info, dict) else None


def cancel(core_dir: Path) -> bool:
    info = pending(core_dir)
    if info is None:
        return False
    files = info.get("files")
    for f in files if isinstance(files, list) else []:
        rel = f.get("live") if isinstance(f, dict) else None
        if isinstance(rel, str) and _live_name(core_dir, rel):
            with contextlib.suppress(OSError, SetError):
                staged_name = rel + STAGED_SUFFIX
                staged = core_dir.parent / staged_name
                if _within(core_dir.parent, staged_name) == staged.absolute():
                    staged.unlink()
    (core_dir / STAGE_MARKER).unlink(missing_ok=True)
    return True


def apply_staged(core_dir: Path, owner: tuple[int, int] | None = None,
                 max_age_s: float = STAGE_MAX_AGE_S, *,
                 av1_migrations: Path | None = None) -> dict[str, Any] | None:
    """Alleen aanroepen als ALLE diensten gestopt zijn (supervisor). Hernoemt de huidige
    databases (met hun -wal/-shm) naar ``voor-terugzetten/<tijd>/`` en de klaargezette
    kopieën naar hun plek. Ontbreekt er iets, dan verandert er niets."""
    with Database(core_dir / "mediaguard.db").startup_lock():
        return _apply_staged(core_dir, owner, max_age_s,
                             av1_migrations or encoder_migrations(os.environ))


def _restore_pairs(core_dir: Path, info: dict[str, Any], av1_migrations: Path,
                   max_age_s: float) -> list[tuple[Path, Path, str]]:
    name, files = info.get("set"), info.get("files")
    if not isinstance(name, str) or not is_set_name(name) or not isinstance(files, list):
        raise SetError("ongeldige markering voor terugzetten")
    try:
        requested = datetime.fromisoformat(str(info.get("requested_at")))
        age = (datetime.now(UTC) - requested).total_seconds()
    except (TypeError, ValueError) as exc:
        raise SetError("ongeldige datum in markering voor terugzetten") from exc
    if age < 0 or age > max_age_s:
        raise SetError("verlopen of ongeldig tijdstip; zet de back-up opnieuw klaar")

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
        _check_restore_copy(staged, rel, size, digest, av1_migrations)
        pairs.append((live, staged, rel))
    if f"{core_dir.name}/mediaguard.db" not in seen or (
        original is not None and seen != original.keys()
    ):
        raise SetError("klaargezette set is onvolledig")
    return pairs


def _apply_staged(core_dir: Path, owner: tuple[int, int] | None, max_age_s: float,
                  av1_migrations: Path) -> dict[str, Any] | None:
    info = pending(core_dir)
    if info is None:
        return None
    marker = core_dir / STAGE_MARKER
    try:
        pairs = _restore_pairs(core_dir, info, av1_migrations, max_age_s)
    except (SetError, schema.SchemaTooNew) as exc:
        result = {"ok": False, "set": info.get("set"), "at": _now(), "error": str(exc)}
        # Tijdens de herstelweergave moet ook een geweigerde aanvraag appdata ongemoeid laten.
        # Bij bekende live-schema's kan de app de fout tonen en de aanvraag opruimen.
        try:
            check_schema_versions(core_dir / "mediaguard.db")
            _encoder_pending(schema.encoder_dir(core_dir), av1_migrations)
        except (OSError, sqlite3.Error, RuntimeError):
            return result
        cancel(core_dir)
        _write_result(core_dir, result, owner)
        return result
    base = core_dir / "backups" / ASIDE_FOLDER / datetime.now(UTC).strftime("%Y%m%dT%H%M%SZ")
    aside = base
    suffix = 1
    while aside.exists():
        suffix += 1
        aside = base.with_name(f"{base.name}-{suffix}")
    aside.mkdir(parents=True)
    moved = []
    renames: list[tuple[Path, Path]] = []
    try:
        for live, staged, rel in pairs:
            keep = aside / rel.replace("/", "__")
            for tail in ("", "-wal", "-shm", "-journal"):
                cur = live.with_name(live.name + tail)
                if cur.exists():
                    dest = keep.with_name(keep.name + tail)
                    os.replace(cur, dest)
                    renames.append((dest, cur))
            os.replace(staged, live)
            renames.append((live, staged))
            moved.append(rel)
    except OSError as exc:
        for src, dst in reversed(renames):
            try:
                os.replace(src, dst)
            except OSError as undo:
                exc.add_note(f"Terugwisselen van {src.name} mislukt: {undo}")
        with contextlib.suppress(OSError):
            aside.rmdir()
        raise
    _chown([aside.parent, aside, *aside.iterdir(), *(p for p, _s, _r in pairs)], owner)
    marker.unlink(missing_ok=True)
    result = {"ok": True, "set": info.get("set"), "at": _now(), "files": moved,
              "previous": str(aside)}
    _write_result(core_dir, result, owner)
    return result


def _write_result(core_dir: Path, result: dict[str, Any],
                  owner: tuple[int, int] | None) -> None:
    path = core_dir / RESULT_FILE
    with contextlib.suppress(OSError):
        path.write_text(json.dumps(result, indent=2), encoding="utf-8")
        _chown([path], owner)


def take_result(core_dir: Path) -> dict[str, Any] | None:
    """Uitkomst van de laatste omwisseling, één keer (voor de melding in de app)."""
    path = core_dir / RESULT_FILE
    try:
        result = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None
    with contextlib.suppress(OSError):
        path.rename(path.with_name(path.name + ".gemeld"))
    return result if isinstance(result, dict) else None


# -- supervisor: set vóór de start van een nieuwe versie --------------------------------------
def _core_pending(core_db: Path, migrations_dir: Path) -> tuple[str, str] | None:
    wanted = {p.stem for p in migrations_dir.glob("*.sql")}
    have = schema.applied_versions(core_db, "schema_migrations", "version")
    if have - wanted:
        raise schema.SchemaTooNew()
    pending = wanted - have
    return (max(map(str, have)), max(pending)) if have and pending else None


def _encoder_versions(migrations_dir: Path) -> set[int]:
    if not migrations_dir.is_dir():
        raise RuntimeError("Migratiemap van de AV1-engine ontbreekt")
    return {int(m.group(1)) for p in migrations_dir.glob("*.sql")
            if (m := _ENC_VERSION.match(p.name))}


def _encoder_pending(encode_dir: Path, migrations_dir: Path | None, *, timeout: float = 30) -> bool:
    if migrations_dir is None:
        return False
    databases = sorted(encode_dir.glob("*.db"))
    if not databases:
        return False
    wanted = _encoder_versions(migrations_dir)
    pending = False
    for db in databases:
        have = schema.applied_versions(db, "schema_versie", "versie", timeout=timeout)
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
    return (
        _core_pending(config_root / "core" / "mediaguard.db", core_migrations),
        _encoder_pending(config_root / "encode", encoder_migrations),
    )


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
                          owner: tuple[int, int] | None = None) -> Path | None:
    """Supervisor, vóór de start van de diensten: staat er een migratie open (core of AV1-
    engine), maak dan een set met beide databases. Een fout blokkeert de start nooit."""
    with config_locks(config_root):
        core, enc = inspect_schema(config_root, core_migrations=core_migrations,
                                   encoder_migrations=encoder_migrations)
        if core is None and not enc:
            return None
        core_db = config_root / "core" / "mediaguard.db"
        return _make_set(core_db.parent / "backups" / FOLDER, core_db,
                         encode_dir=config_root / "encode",
                         from_schema=core[0] if core else None, to_schema=core[1] if core else None,
                         from_version=_previous_version(core_db), to_version=app_version,
                         reason="update", owner=owner)


def main(argv: list[str] | None = None) -> int:  # pragma: no cover - aangeroepen door aio
    """``python -m mediaguard.maintenance.preupdate snapshot …``: de supervisor draait dit als
    de app-gebruiker (niet als root), zodat SQLite-hulpbestanden (-wal/-shm) die bij het lezen
    ontstaan nooit van root worden en core daarna gewoon kan schrijven."""
    import argparse
    import sys

    p = argparse.ArgumentParser(prog="python -m mediaguard.maintenance.preupdate")
    sub = p.add_subparsers(dest="cmd", required=True)
    for cmd in ("inspect", "snapshot"):
        action = sub.add_parser(cmd)
        action.add_argument("--config", required=True, type=Path)
        action.add_argument("--core-migrations", required=True, type=Path)
        action.add_argument("--encoder-migrations", type=Path)
        if cmd == "snapshot":
            action.add_argument("--version", required=True)
    a = p.parse_args(argv)
    try:
        if a.cmd == "inspect":
            inspect_schema(a.config, core_migrations=a.core_migrations,
                           encoder_migrations=a.encoder_migrations)
            return 0
        made = snapshot_before_start(a.config, core_migrations=a.core_migrations,
                                     encoder_migrations=a.encoder_migrations,
                                     app_version=a.version)
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
