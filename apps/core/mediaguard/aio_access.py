"""Start een nevendienst met dezelfde media-ACL's en zonder toegang tot de kernsleutels.

Landlock beperkt het kind en zijn nakomelingen, ook wanneer de hele container al als
de appgebruiker draait. Een ontbrekende kernelvoorziening mag het programma niet starten.
Dit bestand wordt als afzonderlijk proces uitgevoerd, vóór de dienstcode wordt geladen.
"""

from __future__ import annotations

import ctypes
import os
import platform
import stat
import sys
from contextlib import suppress
from pathlib import Path

# Linux UAPI (linux/landlock.h) op amd64 en arm64.
CREATE_RULESET, ADD_RULE, RESTRICT_SELF = 444, 445, 446
PATH_BENEATH = 1
NO_NEW_PRIVS = 38
READ_WRITE_FILE = (1 << 1) | (1 << 2) | (1 << 14)  # write_file, read_file, truncate
MAKE_ENTRIES = 0x7F << 6  # De zeven opeenvolgende rechten MAKE_CHAR t/m MAKE_SYM.
DIRECTORY_ACCESS = READ_WRITE_FILE | MAKE_ENTRIES | (1 << 4) | (1 << 5) | (1 << 13)


class PathRule(ctypes.Structure):
    _pack_ = 1
    _fields_ = [("access", ctypes.c_uint64), ("parent_fd", ctypes.c_int32)]


def _checked(result: int) -> int:
    if result < 0:
        err = ctypes.get_errno()
        raise OSError(err, os.strerror(err))
    return result


def restrict(config: Path, work: Path, data: Path, service: str = "") -> None:
    if platform.machine() not in {"x86_64", "aarch64"}:
        raise OSError("onbekende architectuur voor de procesafscherming")
    libc = ctypes.CDLL(None, use_errno=True)
    # ABI 3 dekt ook truncate. Oudere kernels krijgen geen onbeperkte terugval.
    if _checked(libc.syscall(CREATE_RULESET, None, 0, 1)) < 3:
        raise OSError("Landlock ABI 3 is vereist voor de procesafscherming")
    rights = ctypes.c_uint64(DIRECTORY_ACCESS)
    rules = _checked(libc.syscall(CREATE_RULESET, ctypes.byref(rights), ctypes.sizeof(rights), 0))
    try:
        config = config.resolve()
        core = config / "core"
        private = (core.resolve(), (config / "secrets" / "core").resolve())
        # Andere top-level koppelingen blijven bruikbaar voor de media- en GPU-diensten.
        # Geef nooit een voorouder van de privémap vrij. Binnen appdata zijn alleen de
        # dienstmappen en de gedeelde interne token nodig. Forksleutels staan al in hun env.
        paths = [*Path("/").iterdir(), work, data, Path(sys.prefix), Path.cwd(),
                 config / "secrets" / "internal"]
        # De webgebruiker kan al niet in een afgeschermde appdatamap.
        with suppress(PermissionError):
            paths.extend(p for p in config.iterdir() if p.name not in {"secrets", "core"})
        readable = []
        if service in {"encode", "hookguard"}:
            # Alleen de actieve SQLite-bestanden voor de schemavoorcontrole. De bewaker
            # houdt deze inodes open tot na de stop. Een nieuw terugzetbestand erft niets.
            readable = [core / ("mediaguard.db" + tail) for tail in ("", "-wal", "-shm")]
        if service == "hookguard":
            readable.append(core / "hookguard")
        for path in paths:
            try:
                if path.is_symlink():
                    continue
                resolved = path.resolve()
                if any(resolved.is_relative_to(p) or p.is_relative_to(resolved) for p in private):
                    continue
                fd = os.open(resolved, os.O_PATH | os.O_NOFOLLOW)
            except (FileNotFoundError, PermissionError):
                continue
            try:
                mode = os.fstat(fd).st_mode
                if stat.S_ISLNK(mode):
                    continue
                access = DIRECTORY_ACCESS if stat.S_ISDIR(mode) else READ_WRITE_FILE
                rule = PathRule(access, fd)
                _checked(libc.syscall(ADD_RULE, rules, PATH_BENEATH, ctypes.byref(rule), 0))
            finally:
                os.close(fd)
        for path in readable:
            if path.resolve() != path:
                raise OSError("ongeldig pad voor de alleen-lezen kerncontrole")
            try:
                fd = os.open(path, os.O_PATH | os.O_NOFOLLOW)
            except FileNotFoundError:
                continue
            try:
                mode = os.fstat(fd).st_mode
                expected = stat.S_ISDIR if path.name == "hookguard" else stat.S_ISREG
                if not expected(mode):
                    raise OSError("ongeldig bestand voor de alleen-lezen kerncontrole")
                rule = PathRule(1 << 2, fd)  # LANDLOCK_ACCESS_FS_READ_FILE
                _checked(libc.syscall(ADD_RULE, rules, PATH_BENEATH, ctypes.byref(rule), 0))
            finally:
                os.close(fd)
        _checked(libc.prctl(NO_NEW_PRIVS, 1, 0, 0, 0))
        _checked(libc.syscall(RESTRICT_SELF, rules, 0))
    finally:
        os.close(rules)


if __name__ == "__main__":
    try:
        service = sys.argv[4] if len(sys.argv) > 4 else ""
        restrict(*(Path(p) for p in sys.argv[1:4]), service=service)
        if len(sys.argv) > 5:
            os.execvpe(sys.argv[5], sys.argv[5:], os.environ)
    except OSError as exc:
        sys.exit(f"De dienst kon niet afgeschermd starten: {exc}")
