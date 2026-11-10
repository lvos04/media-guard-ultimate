"""Bestandssysteem-primitieven voor de recovery-worker: fsync, gehashte kopie, ruimte.

Geen shell, geen subprocessen: alleen os-calls met paden als argumenten.
"""

from __future__ import annotations

import contextlib
import errno
import hashlib
import logging
import os
import shutil
import time
from collections.abc import Iterator
from dataclasses import dataclass
from pathlib import Path

CHUNK = 4 * 1024 * 1024

LOGGER = logging.getLogger(__name__)


@dataclass(frozen=True)
class FileStat:
    size: int
    mtime_ns: int
    inode: int
    device: int
    nlink: int
    mode: int
    uid: int
    gid: int

    @classmethod
    def of(cls, path: Path) -> FileStat:
        st = os.lstat(path)
        return cls(st.st_size, st.st_mtime_ns, st.st_ino, st.st_dev, st.st_nlink, st.st_mode,
                   st.st_uid, st.st_gid)

    def to_snapshot(self, path: Path) -> dict[str, int | str]:
        return {"path": str(path), "size": self.size, "mtime_ns": self.mtime_ns,
                "inode": self.inode, "device": self.device, "nlink": self.nlink}


# Leesbare namen van de bewerkingen (voor foutmeldingen en de schrijftest)
OPS_NL = {"create": "aanmaken", "write": "schrijven", "fsync": "wegschrijven (fsync)",
          "readback": "teruglezen", "rename": "hernoemen", "replace": "vervangen",
          "unlink": "verwijderen", "link": "hardlink maken", "mkdir": "map aanmaken",
          "exists": "map vinden"}


@contextlib.contextmanager
def op(name: str) -> Iterator[None]:
    """Markeer welke bewerking een OSError gaf (``exc.mg_op``), zodat een EPERM niet langer
    anoniem is: de melding noemt de exacte stap (aanmaken, schrijven, hernoemen, …)."""
    try:
        yield
    except OSError as exc:
        if not getattr(exc, "mg_op", None):
            exc.mg_op = name  # type: ignore[attr-defined]
        raise


def op_of(exc: BaseException) -> str | None:
    return getattr(exc, "mg_op", None)


def fsync_dir(path: Path) -> None:
    try:
        fd = os.open(path, os.O_RDONLY | os.O_DIRECTORY)
    except PermissionError:
        # map niet leesbaar voor de app-gebruiker (ACL): alleen de extra duurzaamheid vervalt
        LOGGER.warning("map %s niet te openen voor fsync; overgeslagen", path)
        return
    try:
        os.fsync(fd)
    finally:
        os.close(fd)


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for block in iter(lambda: handle.read(CHUNK), b""):
            digest.update(block)
    return digest.hexdigest()


def copy_with_hash(src: Path, dst: Path, mode: int = 0o600) -> tuple[int, str]:
    """Kopieer naar een NIEUW bestand (O_EXCL), fsync, en geef (bytes, sha256 van bron).

    Alleen aanmaken + schrijven + fsync: geen chmod/chown/utime/xattr (zoals Radarr/Sonarr
    een import kopiëren). Elke stap is gemarkeerd met ``op`` voor een precieze foutmelding.
    Faalt de kopie (ENOSPC, EACCES, …), dan wordt het onvolledige doel verwijderd.
    """
    digest = hashlib.sha256()
    written = 0
    with op("create"):
        fd = os.open(dst, os.O_WRONLY | os.O_CREAT | os.O_EXCL, mode)
    try:
        with src.open("rb") as reader, os.fdopen(fd, "wb", closefd=False) as writer:
            for block in iter(lambda: reader.read(CHUNK), b""):
                with op("write"):
                    writer.write(block)
                digest.update(block)
                written += len(block)
            with op("write"):
                writer.flush()
            with op("fsync"):
                os.fsync(fd)
    except BaseException:
        os.close(fd)
        with contextlib.suppress(FileNotFoundError):
            os.unlink(dst)
        raise
    os.close(fd)
    return written, digest.hexdigest()


def same_device(a: Path, b_dir: Path) -> bool:
    try:
        return os.stat(a).st_dev == os.stat(b_dir).st_dev
    except FileNotFoundError:
        return False


def free_bytes(path: Path) -> int:
    probe = path if path.is_dir() else path.parent
    return shutil.disk_usage(probe).free


# Zoals .NET (Radarr/Sonarr) een bestand aanmaakt: 0666, de umask van het proces en de
# geërfde map-ACL bepalen de rest. Geen expliciete modus → niets om door ZFS te weigeren.
ARR_CREATE_MODE = 0o666


def probe_dir(path: Path) -> dict[str, object]:
    """Schrijftest zoals een vervanging: aanmaken, schrijven, fsync, hernoemen over een
    bestaand bestand (activatie), verwijderen. Geeft de stap die faalde."""
    out: dict[str, object] = {"path": str(path), "ok": False, "step": None, "step_nl": None,
                              "error": None, "errno": None}
    tag = f".mg-probe-{os.getpid()}-{time.monotonic_ns()}"
    a, b = path / f"{tag}.part", path / f"{tag}.target"
    step = "exists"
    try:
        if not path.is_dir():
            raise FileNotFoundError(errno.ENOENT, "map bestaat niet", str(path))
        step = "create"
        fd = os.open(a, os.O_WRONLY | os.O_CREAT | os.O_EXCL, ARR_CREATE_MODE)
        try:
            step = "write"
            os.write(fd, b"mediaguard-schrijftest\n" * 64)
            step = "fsync"
            os.fsync(fd)
        finally:
            os.close(fd)
        step = "create"
        os.close(os.open(b, os.O_WRONLY | os.O_CREAT | os.O_EXCL, ARR_CREATE_MODE))
        step = "replace"
        os.replace(a, b)
        step = "unlink"
        os.unlink(b)
        out["ok"] = True
    except OSError as exc:
        out.update(step=step, step_nl=OPS_NL.get(step, step), errno=exc.errno,
                   error=f"{type(exc).__name__}: {exc}")
    finally:
        for leftover in (a, b):
            with contextlib.suppress(OSError):
                os.unlink(leftover)
    return out


def is_enospc(exc: BaseException) -> bool:
    return isinstance(exc, OSError) and exc.errno in (errno.ENOSPC, errno.EDQUOT)


def within(path: Path, roots: list[Path]) -> bool:
    resolved = path.resolve()
    return any(resolved.is_relative_to(r.resolve()) for r in roots)
