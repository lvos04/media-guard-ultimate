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
import time
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

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
        check = sqlite3.connect(f"file:{part}?mode=ro", uri=True)
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


def stage(core_dir: Path, path: Path, *, actor: str) -> dict[str, Any]:
    """Zet de kopieën klaar naast de echte databases; de supervisor wisselt ze om."""
    config_root = core_dir.parent
    problems = verify(path)
    if problems:
        raise SetError("; ".join(problems))
    if path.is_file():
        pairs = [(path, f"{core_dir.name}/mediaguard.db")]
        name = path.name
    else:
        meta = _read_meta(path) or {}
        pairs = [(path / str(f["name"]), str(f["live"])) for f in meta.get("files") or []]
        name = path.name
    need = sum(src.stat().st_size for src, _ in pairs)
    free = shutil.disk_usage(config_root).free
    if free < need * 1.1 + 256 * 1024**2:
        raise SetError(f"niet genoeg vrije ruimte in /config (nodig ±{need // 1024**2} MB)",
                       disk=True)
    cancel(core_dir)
    staged: list[dict[str, str]] = []
    try:
        for src, live in pairs:
            dest = _within(config_root, live + STAGED_SUFFIX)
            part = dest.with_name(dest.name + ".part")
            shutil.copyfile(src, part)
            os.replace(part, dest)
            staged.append({"live": live, "staged": live + STAGED_SUFFIX})
    except OSError as exc:
        for s in staged:
            with contextlib.suppress(OSError):
                _within(config_root, s["staged"]).unlink()
        raise SetError(f"klaarzetten mislukt: {exc}", disk=True) from exc
    info = {"set": name, "requested_at": _now(), "by": actor, "files": staged}
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
    for f in info.get("files") or []:
        with contextlib.suppress(OSError, SetError):
            _within(core_dir.parent, str(f.get("staged"))).unlink()
    (core_dir / STAGE_MARKER).unlink(missing_ok=True)
    return True


def apply_staged(core_dir: Path, owner: tuple[int, int] | None = None,
                 max_age_s: float = STAGE_MAX_AGE_S) -> dict[str, Any] | None:
    """Alleen aanroepen als ALLE diensten gestopt zijn (supervisor). Hernoemt de huidige
    databases (met hun -wal/-shm) naar ``voor-terugzetten/<tijd>/`` en de klaargezette
    kopieën naar hun plek. Ontbreekt er iets, dan verandert er niets."""
    info = pending(core_dir)
    if info is None:
        return None
    config_root = core_dir.parent
    marker = core_dir / STAGE_MARKER
    pairs = []
    try:
        requested = datetime.fromisoformat(str(info.get("requested_at")))
        age = (datetime.now(UTC) - requested).total_seconds()
    except (TypeError, ValueError):
        age = STAGE_MAX_AGE_S + 1
    if age > max_age_s:
        cancel(core_dir)
        result = {"ok": False, "set": info.get("set"), "at": _now(),
                  "error": "verlopen: langer dan een dag geleden klaargezet; zet hem opnieuw "
                           "klaar als je hem nog wilt terugzetten"}
        _write_result(core_dir, result, owner)
        return result
    try:
        for f in info.get("files") or []:
            live = _within(config_root, str(f["live"]))
            staged = _within(config_root, str(f["staged"]))
            if not staged.is_file():
                raise SetError(f"klaargezette kopie ontbreekt: {f['staged']}")
            pairs.append((live, staged, str(f["live"])))
    except (SetError, KeyError) as exc:
        result = {"ok": False, "set": info.get("set"), "at": _now(), "error": str(exc)}
        _write_result(core_dir, result, owner)
        marker.unlink(missing_ok=True)
        return result
    aside = core_dir / "backups" / ASIDE_FOLDER / datetime.now(UTC).strftime("%Y%m%dT%H%M%SZ")
    aside.mkdir(parents=True, exist_ok=True)
    moved = []
    for live, staged, rel in pairs:
        keep = aside / rel.replace("/", "__")
        for suffix in ("", "-wal", "-shm", "-journal"):
            cur = live.with_name(live.name + suffix)
            if cur.exists():
                os.replace(cur, keep.with_name(keep.name + suffix))
        os.replace(staged, live)
        moved.append(rel)
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
def _core_pending(core_db: Path, migrations_dir: Path) -> tuple[str | None, str] | None:
    files = sorted(p.stem for p in migrations_dir.glob("*.sql"))
    if not files or not core_db.is_file():
        return None
    try:
        conn = sqlite3.connect(f"file:{core_db}?mode=ro", uri=True, timeout=10)
        try:
            applied = {r[0] for r in conn.execute("SELECT version FROM schema_migrations")}
        finally:
            conn.close()
    except sqlite3.Error:
        return None
    if not applied or set(files) <= applied:
        return None
    return max(applied), files[-1]


def _encoder_pending(encode_dir: Path, migrations_dir: Path | None) -> bool:
    if migrations_dir is None or not migrations_dir.is_dir():
        return False
    wanted = {int(m.group(1)) for p in migrations_dir.glob("*.sql")
              if (m := _ENC_VERSION.match(p.name))}
    for db in encode_dir.glob("*.db"):
        try:
            conn = sqlite3.connect(f"file:{db}?mode=ro", uri=True, timeout=10)
            try:
                have = {int(r[0]) for r in conn.execute("SELECT versie FROM schema_versie")}
            finally:
                conn.close()
        except sqlite3.Error:
            continue
        if have and not wanted <= have:
            return True
    return False


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
    core_db = config_root / "core" / "mediaguard.db"
    core = _core_pending(core_db, core_migrations)
    enc = _encoder_pending(config_root / "encode", encoder_migrations)
    if core is None and not enc:
        return None
    return make_set(config_root / "core" / "backups" / FOLDER, core_db,
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
    snap = sub.add_parser("snapshot")
    snap.add_argument("--config", required=True, type=Path)
    snap.add_argument("--core-migrations", required=True, type=Path)
    snap.add_argument("--encoder-migrations", type=Path)
    snap.add_argument("--version", required=True)
    a = p.parse_args(argv)
    try:
        made = snapshot_before_start(a.config, core_migrations=a.core_migrations,
                                     encoder_migrations=a.encoder_migrations,
                                     app_version=a.version)
    except Exception as exc:  # noqa: BLE001
        print(str(exc), file=sys.stderr)
        return 1
    print(str(made) if made else "")
    return 0


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())
