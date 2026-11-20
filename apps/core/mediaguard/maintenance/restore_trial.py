"""Proefherstel op lege appdata. Alleen de teruggezette inhoud kan bewijs opleveren."""

from __future__ import annotations

import errno
import os
import shutil
import sqlite3
import stat
import tempfile
import threading
from collections.abc import Callable, Iterator
from contextlib import closing, contextmanager
from pathlib import Path

from cryptography.fernet import Fernet, InvalidToken

from mediaguard import event_keys, schema
from mediaguard.db import check_schema_versions

# Eén uitpak- en SQLite-proef tegelijk per proces, ook bij gelijktijdige HTTP-verzoeken.
_slot = threading.Lock()


def require_space(folder: Path, size: int) -> None:
    from mediaguard.maintenance.preupdate import SetError

    if shutil.disk_usage(folder).free < size * 1.1 + 256 * 1024**2:
        raise SetError(f"niet genoeg vrije ruimte voor herstel (nodig ±{size // 1024**2} MB)",
                       disk=True)


def copy_cursor_key(core: Path, target: Path) -> None:
    """Een databaseset gebruikt de bestaande geheimenmap van de installatie."""
    source = event_keys.key_path(core)
    if source.is_symlink() or source.parent.is_symlink():
        raise ValueError("de cursorsleutel heeft een ongeldig pad")
    try:
        fd = os.open(source, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK)
        with os.fdopen(fd, "rb") as handle:
            info = os.fstat(fd)
            if not stat.S_ISREG(info.st_mode) or info.st_nlink != 1:
                raise ValueError("de cursorsleutel is geen zelfstandig regulier bestand")
            data = handle.read(33)
    except FileNotFoundError:
        return  # De echte startcontrole beslist of deze schemaversie een sleutel mag maken.
    if len(data) != 32:
        raise ValueError("de cursorsleutel heeft een ongeldige lengte")
    target.parent.mkdir(parents=True, mode=0o700, exist_ok=True)
    target.write_bytes(data)
    target.chmod(0o600)


@contextmanager
def core_source(path: Path, *, immutable: bool = False) -> Iterator[sqlite3.Connection]:
    """Houd dezelfde SQLite-leestransactie vast van de schematoets tot het herstel."""
    uri = path.resolve().as_uri() + ("?immutable=1" if immutable else "?mode=ro")
    with closing(sqlite3.connect(uri, uri=True)) as reader:
        reader.execute("BEGIN")
        check_schema_versions(path, connection=reader)
        yield reader


def proefherstel(
    restore: Callable[[Path], object], *, primary: str | None, migrations: Path,
    size: int, bundle: bool = False, source_set: Path | None = None,
) -> dict[str, list[str]]:
    """Voer de herstelbewerking uit en start de leescontroles uitsluitend op het resultaat."""
    from mediaguard.maintenance import preupdate

    try:
        with _slot, tempfile.TemporaryDirectory(prefix="mg-proefherstel-") as tmp:
            root = Path(tmp)
            require_space(root, size)
            manifest = preupdate._single_manifest(source_set) if source_set is not None else None
            restore(root)
            core = root / "core/mediaguard.db"
            if not core.is_file():
                raise preupdate.SetError("de herstelde kern ontbreekt")
            event_keys.prepare_for_start(core, root / "secrets" / event_keys.KEY_REF)
            versions = {}
            with closing(sqlite3.connect(core.as_uri() + "?immutable=1", uri=True)) as db:
                check_schema_versions(core, connection=db)
                if db.execute("PRAGMA integrity_check").fetchall() != [("ok",)]:
                    raise preupdate.SetError("de herstelde kern is beschadigd")
                versions["core/mediaguard.db"] = sorted(
                    schema.versions_in(db, "schema_migrations", "version"))
                if bundle:
                    key = core.with_name("secret.key")
                    cipher = Fernet(key.read_bytes().strip()) if key.is_file() else None
                    for (token,) in db.execute("SELECT ciphertext FROM secrets"):
                        if cipher is None:
                            raise preupdate.SetError("de bundel mist de sleutel van de koppelingen")
                        cipher.decrypt(token).decode("utf-8")
            for path in sorted((root / "encode").glob("*.db")):
                rel = path.relative_to(root).as_posix()
                with closing(sqlite3.connect(path.as_uri() + "?immutable=1", uri=True)) as db:
                    if db.execute("PRAGMA integrity_check").fetchall() != [("ok",)]:
                        raise preupdate.SetError("de herstelde AV1-database is beschadigd")
                    have = preupdate.encoder_copy_versions(db, migrations, primary=rel == primary)
                    versions[rel] = [str(v) for v in sorted(have)]
                    if rel == primary:
                        # Ook een lege wachtrij moet de versiegebonden kolommen kunnen lezen.
                        query = migrations / "leesopdrachten/wachtrij.sql"
                        if not query.is_file():
                            query = preupdate.encoder_migrations({}) / query.relative_to(migrations)
                        try:
                            for _row in db.execute(query.read_text(encoding="utf-8")):
                                pass
                        except sqlite3.Error as exc:
                            raise preupdate.SetError(
                                "De taakvoortgang in de primaire AV1-back-up is onleesbaar"
                            ) from exc
            if (primary is not None or bundle) and primary not in versions:
                raise preupdate.SetError("De ingestelde primaire AV1-database ontbreekt")
            if source_set is not None and preupdate._single_manifest(source_set) != manifest:
                raise preupdate.SetError("het bronmanifest is tijdens het proefherstel gewijzigd")
            return versions
    except (OSError, ValueError, TypeError, InvalidToken, sqlite3.Error) as exc:
        disk = isinstance(exc, OSError) and exc.errno == errno.ENOSPC
        raise preupdate.SetError("de herstelde gegevens of startsleutel zijn onleesbaar",
                                 disk=disk) from exc
