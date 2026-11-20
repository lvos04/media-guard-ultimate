"""Levensduur en meldbaarheid van iedere bekende poolwerker op dezelfde datamap."""

from __future__ import annotations

import fcntl
import io
import logging
import os
import sqlite3
import stat
import struct
from contextlib import suppress
from pathlib import Path
from uuid import uuid4
from weakref import finalize

from mediaguard.context import AppContext
from mediaguard.db import loads

_PREFIX = "pool_worker_v1:"
_PENDING = ".pool-aanmelding-"
_ADMISSIONS_STATE = "pool_aanmeldbestand_v1"
LOGGER = logging.getLogger(__name__)
# Linux struct flock: type, whence, start, lengte en pid (64-bits off_t).
_FLOCK = struct.Struct("@hhqqi0q")
SERVICE = "pool_worker_vote"
CLEANUP_REQUEST = "truenas_pool_cleanup_request"


def record_lock(fd: int, offset: int, *, probe: bool) -> int:
    command = getattr(fcntl, "F_OFD_GETLK" if probe else "F_OFD_SETLK", None)
    if command is None:
        raise OSError("Deze kernel ondersteunt het vereiste werkerslot niet")
    request = _FLOCK.pack(fcntl.F_WRLCK if probe else fcntl.F_RDLCK, os.SEEK_SET, offset, 1, 0)
    kind, _, start, length, _ = _FLOCK.unpack(fcntl.fcntl(fd, command, request))
    if probe and kind == fcntl.F_RDLCK and (start, length) != (offset, 1):
        raise OSError("Het gevonden slot hoort niet bij deze meetwerker")
    return kind


def _pid_scope() -> str:
    boot = Path("/proc/sys/kernel/random/boot_id").read_text().strip()
    return f"{boot}.{os.stat('/proc/self/ns/pid').st_ino}"


def _owner() -> str:
    with suppress(OSError):
        return f"{os.getpid()}.{os.stat('/proc/self').st_ino}.{_pid_scope()}"
    return "onbekend"


def _exited(owner: str) -> bool:
    # Een lege of afgeschermde /proc-map is geen bewijs van vertrek. ESRCH op
    # pidfd_open of een andere procesidentiteit binnen dezelfde naamruimte wel.
    parts = owner.split(".", 2)
    if len(parts) != 3 or parts[2] != _pid_scope():
        return False
    pid = int(parts[0])
    try:
        fd = os.pidfd_open(pid)
    except ProcessLookupError:
        return True
    try:
        return os.stat(f"/proc/{pid}").st_ino != int(parts[1])
    finally:
        os.close(fd)


def _admissions(ctx: AppContext, *, create: bool = False, readonly: bool = False) -> io.FileIO:
    path = ctx.db.path.with_suffix(".pool-aanmeldingen")
    flags = (os.O_RDONLY if readonly else os.O_RDWR | os.O_APPEND) | os.O_NOFOLLOW | os.O_NONBLOCK
    fd = os.open(path, flags | os.O_CREAT if create else flags, 0o600)
    stream = io.FileIO(fd, "r" if readonly else "r+")
    try:
        if not stat.S_ISREG(os.fstat(fd).st_mode):
            raise OSError("Ongeldig aanmeldbestand voor de poolwerkers")
    except BaseException:
        stream.close()
        raise
    return stream


def _note(stream: io.FileIO, slot: int, state: str) -> None:
    line = f"{slot} {state}\n".encode("ascii")
    if stream.write(line) != len(line):
        raise OSError("Onvolledige aanmelding van de poolwerker")


def _outstanding(stream: io.FileIO) -> dict[int, str]:
    pending = {}
    for line in stream:
        slot, sep, state = line.partition(b" ")
        if not sep or not slot.isdigit() or not state.endswith(b"\n"):
            raise OSError("Onleesbare aanmelding van een poolwerker")
        if not state.isascii():
            raise OSError("Onleesbare identiteit in het aanmeldbestand")
        owner = state[:-1].decode("ascii")
        if owner == "verwerkt":
            pending.pop(int(slot), None)
        else:
            pending[int(slot)] = owner
    return pending


class Lease:
    """Het sluiten van de eigen descriptor trekt dit bewijs zonder slot- of padtoegang in."""

    def __init__(self, ctx: AppContext, offset: int) -> None:
        self.fd = os.open(ctx.db.path.parent, os.O_RDONLY | os.O_DIRECTORY)
        self.close = finalize(self, os.close, self.fd)
        try:
            record_lock(self.fd, offset, probe=False)
        except BaseException:
            self.close()
            raise


class WorkerVote:
    def __init__(self, slot: int) -> None:
        self.slot = slot
        self.key = _PREFIX + str(slot)
        self.life: Lease | None = None
        self.ready: Lease | None = None
        self.channel: list[int] | None = None
        self.owner = _owner()
        self.pending: Path | None = None
        self.admission: io.FileIO | None = None
        self.guard: io.FileIO | None = None
        self.unregistered = False

    def withdraw(self) -> None:
        if self.ready is not None:
            self.ready.close()
            self.ready = None

    def report(self, ctx: AppContext, stream: io.FileIO | None = None) -> None:
        channel = None
        if stream is not None:
            info = os.fstat(stream.fileno())
            channel = [info.st_dev, info.st_ino]
        if self.ready is not None and self.channel == channel:
            return
        try:
            with ctx.db.transaction():
                reconnecting = self.ready is None or self.channel is not None
                if self.unregistered or (stream is not None and reconnecting):
                    ctx.db.execute("DELETE FROM system_state WHERE key=?", (CLEANUP_REQUEST,))
                if self.life is None:
                    self.life = Lease(ctx, self.slot)
                if self.ready is None:
                    self.ready = Lease(ctx, self.slot + 1)
                info = os.fstat(self.life.fd)
                ctx.state_set(self.key, {
                    "owner": self.owner, "directory": [info.st_dev, info.st_ino],
                    "channel": channel,
                })
            self.clear_pending()
            self.unregistered = False
        except BaseException:
            self.withdraw()
            raise
        self.channel = channel

    def clear_pending(self) -> None:
        if self.admission is not None:
            _note(self.admission, self.slot, "verwerkt")
            self.admission.close()
            self.admission = None
        if self.pending is not None:
            self.pending.rmdir()
            self.pending = None
        if self.guard is not None:
            self.guard.close()
            self.guard = None

    def close(self, ctx: AppContext) -> None:
        prefix = f"{ctx.db.path.name}{_PENDING}{self.slot}_"
        try:
            # Een klaargezette herstelkopie verdraagt hier geen nieuwe SQLite-commit.
            if self.ready is not None and self.channel is not None:
                # Deze werker kon tot de afmelding fouten doorgeven. Een meting
                # die onder zijn meldslot is gedaan, blijft geldig.
                ctx.db.path.with_name(prefix + "gemeld").mkdir(mode=0o700)
            elif self.admission is not None:
                with self.admission:
                    _note(self.admission, self.slot, "afgesloten")
            elif self.guard is not None:
                self.guard.close()
            elif self.pending is not None or self.life is None:
                closed = ctx.db.path.with_name(prefix + "afgesloten")
                if self.pending is None:
                    closed.mkdir(mode=0o700)
                else:
                    self.pending.rename(closed)
        finally:
            self.withdraw()
            if self.life is not None:
                self.life.close()


def register(ctx: AppContext) -> WorkerVote:
    # Byte nul blijft voor het gedeelde terugvalslot. Iedere werker krijgt twee eigen bytes.
    vote = WorkerVote((uuid4().int % (1 << 61) + 1) * 2)
    try:
        with ctx.db.transaction():
            while ctx.db.fetch_one("SELECT 1 FROM system_state WHERE key=?", (vote.key,)):
                vote = WorkerVote((uuid4().int % (1 << 61) + 1) * 2)
            ctx.state_set(vote.key, {"owner": vote.owner, "directory": None})
    except (OSError, sqlite3.Error):
        vote.unregistered = True
        # De naam is de volledige melding. mkdir maakt deze in één stap zichtbaar,
        # ook zonder SQLite, leesbare meetbestanden of beschikbare recordsloten.
        name = f"{ctx.db.path.name}{_PENDING}{vote.slot}_{vote.owner}"
        marker = ctx.db.path.with_name(name)
        try:
            marker.mkdir(mode=0o700)
            vote.pending = marker
        except OSError:
            try:
                vote.admission = _admissions(ctx, create=True)
                _note(vote.admission, vote.slot, vote.owner)
            except OSError:
                if vote.admission is not None:
                    vote.admission.close()
                    vote.admission = None
                # Een alleen leesbaar bestand kan nog steeds een gedeeld flock
                # dragen. De tijdstempel trekt ook na abrupt vertrek de oude meting in.
                with suppress(OSError):
                    vote.guard = _admissions(ctx, readonly=True)
                    fcntl.flock(vote.guard, fcntl.LOCK_SH | fcntl.LOCK_NB)
                    os.utime(vote.guard.fileno())
                with suppress(OSError, sqlite3.Error):
                    ctx.db.execute("DELETE FROM system_state WHERE key=?", (CLEANUP_REQUEST,))
        LOGGER.warning("SQLite-inschrijving van de poolwerker uitgesteld")
    ctx.services[SERVICE] = vote
    if not vote.unregistered:
        with suppress(OSError, sqlite3.Error):  # De onbekende registratie is al gecommit.
            vote.report(ctx)
    return vote


def all_reported(ctx: AppContext, fd: int, *, new_measurement: bool) -> bool:
    saved = ctx.state_get(_ADMISSIONS_STATE)
    with _admissions(ctx, create=new_measurement and saved is None) as admissions:
        fcntl.flock(admissions, fcntl.LOCK_EX | fcntl.LOCK_NB)
        unfinished = _outstanding(admissions)
        # Slot nul identificeert het aanmeldbestand. Een lege vervanger kan
        # geen uitsluitsel geven over werkers die alleen in het origineel staan.
        identity = unfinished.pop(0, None)
        if saved is not None:
            if identity != saved[0] or (
                    not new_measurement and os.fstat(admissions.fileno()).st_mtime_ns != saved[1]):
                return False
        elif not new_measurement:
            return False
        elif identity is None:
            identity = uuid4().hex
            _note(admissions, 0, identity)
        for owner in unfinished.values():
            if not new_measurement or (owner != "afgesloten" and not _exited(owner)):
                return False
        if not _registered_reported(ctx, fd, new_measurement, set(unfinished)):
            return False
        for slot in unfinished:
            _note(admissions, slot, "verwerkt")
        stamp = [identity, os.fstat(admissions.fileno()).st_mtime_ns]
        if new_measurement and stamp != saved:
            ctx.state_set(_ADMISSIONS_STATE, stamp)
    return True


def _registered_reported(ctx: AppContext, fd: int, new_measurement: bool, exited: set[int]) -> bool:
    prefix = ctx.db.path.name + _PENDING
    closed = {}
    reported = set()
    for name in os.listdir(fd):
        if name.startswith(prefix):
            slot, _, owner = name[len(prefix):].partition("_")
            if owner == "gemeld":
                reported.add(int(slot))
            elif not new_measurement or (owner != "afgesloten" and not _exited(owner)):
                return False
            closed[int(slot)] = name
    rows = ctx.db.fetch_all("SELECT key, value_json FROM system_state WHERE key GLOB ?",
                            (_PREFIX + "*",))
    directory_info = os.fstat(fd)
    channel = ctx.db.path.with_suffix(".pool-fifo")
    for row in rows:
        slot = int(row["key"][len(_PREFIX):])
        record = loads(row["value_json"])
        if slot in closed or slot in exited:
            life = fcntl.F_UNLCK
        elif record["directory"] == [directory_info.st_dev, directory_info.st_ino]:
            life = record_lock(fd, slot, probe=True)
        elif _exited(record["owner"]):
            life = fcntl.F_UNLCK
        else:
            return False
        if life == fcntl.F_UNLCK:
            if not new_measurement:
                if slot not in reported:
                    return False
                continue
            ctx.db.execute("DELETE FROM system_state WHERE key=?", (row["key"],))
            continue
        if life != fcntl.F_RDLCK or record_lock(fd, slot + 1, probe=True) != fcntl.F_RDLCK:
            return False
        if record["channel"] is not None:
            info = channel.lstat()
            if record["channel"] != [info.st_dev, info.st_ino]:
                return False
    if new_measurement:
        for name in closed.values():
            os.rmdir(name, dir_fd=fd)
    return True
