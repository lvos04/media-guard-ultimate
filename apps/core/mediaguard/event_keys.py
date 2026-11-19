"""Privésleutels voor actuele leesposities en terugval naar een eerder SQLite-schema."""

from __future__ import annotations

import fcntl
import hashlib
import os
import stat
import tempfile
from collections.abc import Iterator
from contextlib import contextmanager, suppress
from pathlib import Path

KEY_REF = "core/outbox.key"
LEGACY_PREFIX = "outbox-historisch-"


def key_path(core_dir: Path) -> Path:
    appdata = core_dir.parent if core_dir.name == "core" else core_dir
    return Path(os.environ.get("MG_SECRETS_DIR") or appdata / "secrets") / KEY_REF


def _permissions(fd: int, mode: int, owner: tuple[int, int], *, repair: bool) -> None:
    info = os.fstat(fd)
    if mode == 0o600 and (not stat.S_ISREG(info.st_mode) or info.st_nlink != 1):
        raise PermissionError("de cursorsleutel is geen zelfstandig regulier bestand")
    if (info.st_uid, info.st_gid) != owner:
        if not repair or os.geteuid() != 0:
            raise PermissionError("de eigenaar van de geheime opslag klopt niet")
        os.fchown(fd, *owner)
    if stat.S_IMODE(info.st_mode) != mode:
        if not repair:
            raise PermissionError("de toegangsrechten van de geheime opslag kloppen niet")
        os.fchmod(fd, mode)


@contextmanager
def _directory(path: Path, owner: tuple[int, int], *, repair: bool) -> Iterator[int]:
    if repair:
        path.mkdir(mode=0o700, parents=True, exist_ok=True)
    fd = os.open(path, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW)
    try:
        fcntl.flock(fd, fcntl.LOCK_EX if repair else fcntl.LOCK_SH)
        _permissions(fd, 0o700, owner, repair=repair)
        yield fd
    finally:
        os.close(fd)


def _read(directory: int, name: str, owner: tuple[int, int], *, repair: bool = False) -> bytes:
    fd = os.open(name, os.O_RDONLY | os.O_NONBLOCK | os.O_NOFOLLOW, dir_fd=directory)
    with os.fdopen(fd, "rb") as handle:
        _permissions(fd, 0o600, owner, repair=repair)
        value = handle.read(33)
        if len(value) != 32:
            raise ValueError("de cursorsleutel heeft een ongeldige lengte")
        return value


def _store(path: Path, owner: tuple[int, int], value: bytes | None = None) -> None:
    with _directory(path.parent, owner, repair=True) as directory:
        try:
            stored = _read(directory, path.name, owner, repair=True)
        except FileNotFoundError:
            # Link pas na de volledige schrijf. Ook twee starters overschrijven nooit een sleutel.
            with tempfile.NamedTemporaryFile(dir=path.parent) as part:
                part.write(value if value is not None else os.urandom(32))
                part.flush()
                if os.geteuid() == 0:
                    os.fchown(part.fileno(), *owner)
                os.fsync(part.fileno())
                with suppress(FileExistsError):
                    os.link(part.name, path.name, dst_dir_fd=directory)
            os.fsync(directory)
            stored = _read(directory, path.name, owner)
        if value is not None and stored != value:
            raise ValueError("de bewaarde historische cursorsleutel komt niet overeen")


def ensure_key(path: Path, owner: tuple[int, int] | None = None) -> None:
    _store(path, owner or (os.geteuid(), os.getegid()))


def read_key(core_dir: Path, reference: object) -> bytes | None:
    if reference != KEY_REF:
        return None
    path = key_path(core_dir)
    owner = (os.geteuid(), os.getegid())
    try:
        with _directory(path.parent, owner, repair=False) as directory:
            return _read(directory, path.name, owner)
    except (OSError, ValueError):
        return None


def store_legacy(core_dir: Path, value: bytes) -> str:
    if len(value) != 32:
        raise ValueError("de historische cursorsleutel heeft een ongeldige lengte")
    name = LEGACY_PREFIX + hashlib.sha256(value).hexdigest()
    _store(key_path(core_dir).with_name(name), (os.geteuid(), os.getegid()), value)
    return name


def load_legacy(core_dir: Path, name: str) -> bytes:
    if not isinstance(name, str):
        raise ValueError("ongeldige verwijzing naar een historische cursorsleutel")
    digest = name.removeprefix(LEGACY_PREFIX)
    if name == digest or len(digest) != 64 or any(c not in "0123456789abcdef" for c in digest):
        raise ValueError("ongeldige verwijzing naar een historische cursorsleutel")
    owner = (os.geteuid(), os.getegid())
    with _directory(key_path(core_dir).parent, owner, repair=False) as directory:
        value = _read(directory, name, owner)
    if hashlib.sha256(value).hexdigest() != digest:
        raise ValueError("de historische cursorsleutel komt niet overeen met de herstelset")
    return value
