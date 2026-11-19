#!/opt/venv/bin/python -S
"""Docker-healthcheck voor een verse bewakerstatus en de interne kernreadiness."""

from __future__ import annotations

import os
import sys
from collections.abc import Mapping

CORE_PORT = 38100
HEALTH_FILE = "/run/mediaguard/mediaguard-health"
STATUS_FIELDS = ("Uid", "Gid", "CapEff", "CapPrm", "CapInh", "CapAmb", "CapBnd", "NoNewPrivs")
PR_CAPBSET_DROP = 24
PR_SET_NO_NEW_PRIVS = 38
ZERO_CAPS = "0000000000000000"


def process_status() -> dict[str, str]:
    with open("/proc/self/status", encoding="ascii") as source:
        fields = dict(line.rstrip().split(":", 1) for line in source if ":" in line)
    return {key: fields[key].strip() for key in STATUS_FIELDS}


def restrict_process(uid: int, gid: int, groups: tuple[int, ...] = ()) -> dict[str, str]:
    """Laat onomkeerbaar alle capabilities vallen en controleer de actieve identiteit."""
    import ctypes

    prctl = ctypes.CDLL(None, use_errno=True).prctl

    def change(option: int, value: int) -> None:
        if prctl(option, value, 0, 0, 0) != 0:
            error = ctypes.get_errno()
            raise OSError(error, os.strerror(error))

    if any(not 0 < value < 2**32 - 1 for value in (uid, gid)):
        raise ValueError("UID en GID moeten geldige ID's boven nul zijn")

    root_start = os.geteuid() == 0
    if root_start:
        with open("/proc/sys/kernel/cap_last_cap", encoding="ascii") as source:
            last_capability = int(source.read())
        for capability in range(last_capability + 1):
            change(PR_CAPBSET_DROP, capability)
        os.setgroups(groups)
        os.setgid(gid)
        os.setuid(uid)
    change(PR_SET_NO_NEW_PRIVS, 1)

    status = process_status()
    if (status["Uid"].split() != [str(uid)] * 4 or status["Gid"].split() != [str(gid)] * 4
            or any(status[field] != ZERO_CAPS for field in ("CapEff", "CapPrm", "CapInh", "CapAmb"))
            or (root_start and (status["CapBnd"] != ZERO_CAPS
                                or sorted(os.getgroups()) != sorted(groups)))
            or status["NoNewPrivs"] != "1"):
        raise PermissionError("onverwachte UID, GID of procesrechten")
    return status


def app_identity(env: Mapping[str, str]) -> tuple[int, int]:
    try:
        return int(env.get("PUID", "568")), int(env.get("PGID", "568"))
    except ValueError as exc:
        raise ValueError("PUID en PGID moeten gehele getallen zijn") from exc


def lower_privileges() -> dict[str, str]:
    return restrict_process(*app_identity(os.environ))


def recent_ok() -> bool:
    import stat
    import time

    try:
        fd = os.open(os.environ.get("MG_AIO_HEALTH_FILE") or HEALTH_FILE,
                     os.O_RDONLY | os.O_NONBLOCK)
        with os.fdopen(fd, "rb") as source:
            info = os.fstat(source.fileno())
            return (stat.S_ISREG(info.st_mode) and 0 <= time.time() - info.st_mtime < 90
                    and source.readline(512).split(maxsplit=1)[:1] == [b"ok"])
    except OSError:
        return False


def ready() -> bool:
    import http.client
    import socket

    loopback = socket.inet_ntoa(socket.INADDR_LOOPBACK.to_bytes(4, "big"))
    connection = http.client.HTTPConnection(loopback, CORE_PORT, timeout=3)
    try:
        connection.request("GET", "/readyz")
        return connection.getresponse().status == 200
    except (OSError, http.client.HTTPException):
        return False
    finally:
        connection.close()


def main(args: list[str]) -> int:
    try:
        status = lower_privileges()
    except (KeyError, OSError, ValueError) as exc:
        print(f"Healthcheck: rechtenwissel mislukt: {exc}", file=sys.stderr)
        return 1
    if args == ["--controle"]:
        for field in STATUS_FIELDS:
            print(f"{field}: {status[field]}")
        return 0
    if args:
        return 1
    if not recent_ok():
        print("Healthcheck: geen verse gezonde bewakerstatus")
        return 1
    if not ready():
        print("Healthcheck: de kern is niet gereed")
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
