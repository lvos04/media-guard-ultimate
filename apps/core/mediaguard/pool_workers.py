"""Levensduur en meldbaarheid van iedere bekende poolwerker op dezelfde datamap."""

from __future__ import annotations

import ctypes
import fcntl
import io
import logging
import os
import sqlite3
import stat
import struct
from contextlib import ExitStack, suppress
from pathlib import Path
from uuid import uuid4
from weakref import finalize

from mediaguard.context import AppContext
from mediaguard.db import dumps, loads, utcnow

_PREFIX = "pool_worker_v1:"
_RESERVED = "pool_toelating_v2:"
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


def _note(stream: io.FileIO, slot: int | str, state: str) -> None:
    line = f"{slot} {state}\n".encode("ascii")
    if stream.write(line) != len(line):
        raise OSError("Onvolledige aanmelding van de poolwerker")


def _outstanding(stream: io.FileIO) -> dict[int, str]:
    pending = {}
    before = os.fstat(stream.fileno())
    for line in stream:
        slot, sep, state = line.partition(b" ")
        if slot == b"generatie":
            slot = b"0"  # Oudere lezers weigeren dit record en kunnen dus geen toestemming geven.
        if not sep or not slot.isdigit() or not state.endswith(b"\n"):
            raise OSError("Onleesbare aanmelding van een poolwerker")
        if not state.isascii():
            raise OSError("Onleesbare identiteit in het aanmeldbestand")
        owner = state[:-1].decode("ascii")
        if slot == b"0" and (len(owner) != 32 or any(c not in "0123456789abcdef" for c in owner)):
            raise OSError("Onleesbare generatie in het aanmeldbestand")
        if owner == "verwerkt":
            pending.pop(int(slot), None)
        else:
            pending[int(slot)] = owner
    if os.fstat(stream.fileno()).st_ctime_ns != before.st_ctime_ns:
        raise OSError("Het aanmeldbestand veranderde tijdens het lezen")
    return pending


def _stamp(info: os.stat_result, generation: str) -> list[str | int]:
    # copy2 kan de mtime en de inhoud exact terugzetten, ook op dezelfde inode.
    return [generation, info.st_dev, info.st_ino, info.st_ctime_ns, info.st_size]


def _saved_stamp(ctx: AppContext) -> list | None:
    try:
        saved = ctx.state_get(_ADMISSIONS_STATE)
    except ValueError as exc:
        raise OSError("De opgeslagen registergeneratie is onleesbaar") from exc
    # De vorige kern bewaarde alleen een generatie en een tijdstempel.
    if saved is not None and not (
        isinstance(saved, list) and len(saved) in (2, 5) and isinstance(saved[0], str)
        and len(saved[0]) == 32 and all(c in "0123456789abcdef" for c in saved[0])
        and all(type(field) is int for field in saved[1:])
    ):
        raise OSError("De opgeslagen registergeneratie is ongeldig")
    return saved


def _generation(ctx: AppContext, stream: io.FileIO, pending: dict[int, str],
                saved: list | None) -> str:
    identity = pending.pop(0, None)
    if identity and saved == _stamp(os.fstat(stream.fileno()), identity):
        return identity
    # Ook bij terugplaatsen van het oorspronkelijke bestand geldt een nieuwe generatie.
    # Eerst schrijven op schijf. Bij een onderbreking kunnen de twee getuigen uitsluitend
    # weer overeenkomen door een volgende, nieuwe generatie uit te geven.
    generation = uuid4().hex
    _note(stream, "generatie", generation)
    os.fsync(stream.fileno())
    with ctx.db.transaction():
        # Een aanmelding uit een oudere kern kan nog uitsluitend in dit bestand staan.
        # Houd de waargenomen werker ook na een volgende terugplaatsing in de ledenlijst.
        for slot, owner in pending.items():
            if owner != "afgesloten":
                ctx.db.execute(
                    "INSERT OR IGNORE INTO system_state(key,value_json,updated_at) VALUES(?,?,?)",
                    (_RESERVED + str(slot), dumps(owner), utcnow()))
        ctx.db.execute("DELETE FROM system_state WHERE key=?", (CLEANUP_REQUEST,))
        ctx.state_set(_ADMISSIONS_STATE, _stamp(os.fstat(stream.fileno()), generation))
    return generation


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
        self.generation: str | None = None
        self.owner = _owner()
        self.pending: Path | None = None
        self.admission: io.FileIO | None = None
        self.guard: io.FileIO | None = None
        self.unregistered = False
        self.reserved = False

    def withdraw(self) -> None:
        if self.ready is not None:
            self.ready.close()
            self.ready = None

    def report(self, ctx: AppContext, stream: io.FileIO | None = None) -> None:
        channel = None
        if stream is not None:
            info = os.fstat(stream.fileno())
            channel = [info.st_dev, info.st_ino]
        try:
            saved = _saved_stamp(ctx)
            if (saved and self.ready is not None and self.channel == channel
                    and self.generation == saved[0]):
                return
            with ctx.db.transaction():
                if saved is None:
                    with _admissions(ctx, create=True) as ledger:
                        fcntl.flock(ledger, fcntl.LOCK_EX | fcntl.LOCK_NB)
                        generation = _generation(ctx, ledger, _outstanding(ledger), saved)
                else:
                    with _admissions(ctx, readonly=True) as ledger:
                        info = os.fstat(ledger.fileno())
                        generation = saved[0] if saved == _stamp(info, saved[0]) else None
                reconnecting = self.ready is None or self.channel is not None
                if (self.unregistered or (stream is not None and reconnecting)
                        or (self.generation is not None and self.generation != generation)):
                    ctx.db.execute("DELETE FROM system_state WHERE key=?", (CLEANUP_REQUEST,))
                if self.life is None:
                    self.life = Lease(ctx, self.slot)
                if self.ready is None:
                    self.ready = Lease(ctx, self.slot + 1)
                self._record(ctx, channel, generation)
                if self.reserved:
                    ctx.db.execute("DELETE FROM system_state WHERE key=?",
                                   (_RESERVED + str(self.slot),))
            self.clear_pending()
            self.unregistered = False
            self.reserved = False
        except BaseException:
            self.withdraw()
            raise
        self.channel = channel
        self.generation = generation

    def _record(self, ctx: AppContext, channel: list[int] | None, generation: str | None) -> None:
        assert self.life is not None
        info = os.fstat(self.life.fd)
        record = {
            "owner": self.owner, "directory": [info.st_dev, info.st_ino],
            "channel": channel, "generation": generation,
        }
        if self.unregistered:
            ctx.state_set(self.key, record)
        elif ctx.db.execute("UPDATE system_state SET value_json=?, updated_at=? WHERE key=?",
                            (dumps(record), utcnow(), self.key)) != 1:
            raise OSError("De inschrijving van de poolwerker ontbreekt in het register")

    def confirm_generation(self, ctx: AppContext, generation: str) -> None:
        if self.ready is not None and not self.unregistered and self.generation != generation:
            self._record(ctx, self.channel, generation)
            self.generation = generation

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
            elif self.pending is not None or self.reserved or self.life is None:
                closed = ctx.db.path.with_name(prefix + "afgesloten")
                if self.pending is not None:
                    self.pending.rename(closed)
                elif self.admission is None:
                    closed.mkdir(mode=0o700)
                else:
                    # De open descriptor kan na terugplaatsen naar een oud bestand wijzen.
                    # De afsluiting hoort bij de werker, ook in de nieuwe generatie.
                    with self.admission:
                        _note(self.admission, self.slot, "afgesloten")
                        path = ctx.db.path.with_suffix(".pool-aanmeldingen")
                        if not os.path.samestat(os.fstat(self.admission.fileno()), path.lstat()):
                            closed.mkdir(mode=0o700)
        finally:
            if self.guard is not None:
                self.guard.close()
            self.withdraw()
            if self.life is not None:
                self.life.close()


def _reserve(ctx: AppContext, vote: WorkerVote) -> None:
    # De ledenlijst moet ook na vervanging van het aanmeldbestand compleet zijn.
    name = f"{ctx.db.path.name}{_PENDING}{vote.slot}_{vote.owner}"
    marker = ctx.db.path.with_name(name)
    try:
        marker.mkdir(mode=0o700)
        vote.pending = marker
        return
    except OSError:
        LOGGER.warning("Poolwerker kan geen aanmeldmap maken, toelating via SQLite vereist")
    # De reservering staat los van de intrekking van de laatste meting. Ook een
    # geweigerde intrekking mag deze inschrijving niet terugdraaien.
    with ctx.db.durable_transaction():
        ctx.state_set(_RESERVED + str(vote.slot), vote.owner)
    vote.reserved = True
    try:
        vote.admission = _admissions(ctx, create=True)
        _note(vote.admission, vote.slot, vote.owner)
    except OSError:
        if vote.admission is not None:
            vote.admission.close()
            vote.admission = None
        LOGGER.warning("Aanmeldbestand niet schrijfbaar, poolwerker wacht op bevestiging")
        try:
            vote.guard = _admissions(ctx, readonly=True)
            fcntl.flock(vote.guard, fcntl.LOCK_SH | fcntl.LOCK_NB)
            os.utime(vote.guard.fileno())
        except OSError:
            if vote.guard is not None:
                vote.guard.close()
                vote.guard = None
            LOGGER.exception("Aanmeldbestand of aanmeldslot geweigerd, poolwerker blijft onbekend")


def register(ctx: AppContext) -> WorkerVote:
    # Byte nul blijft voor het gedeelde terugvalslot. Iedere werker krijgt twee eigen bytes.
    vote = WorkerVote((uuid4().int % (1 << 61) + 1) * 2)
    with ctx.db.startup_lock():
        try:
            with ctx.db.transaction():
                while ctx.db.fetch_one("SELECT 1 FROM system_state WHERE key IN (?,?)",
                                       (vote.key, _RESERVED + str(vote.slot))):
                    vote = WorkerVote((uuid4().int % (1 << 61) + 1) * 2)
                ctx.state_set(vote.key, {"owner": vote.owner, "directory": None})
        except (OSError, sqlite3.Error):
            vote.unregistered = True
            LOGGER.warning("SQLite-inschrijving van de poolwerker uitgesteld")
            _reserve(ctx, vote)
        if not vote.unregistered:
            try:
                vote.report(ctx)
            except (OSError, sqlite3.Error):
                LOGGER.exception("Poolwerker ingeschreven, meldbaarheid nog niet bevestigd")
    ctx.services[SERVICE] = vote
    return vote


def all_reported(ctx: AppContext, fd: int, *, new_measurement: bool) -> bool:
    vote = ctx.services.get(SERVICE) or register(ctx)
    if vote.unregistered or vote.ready is None:
        return False
    saved = _saved_stamp(ctx)
    with _admissions(ctx, create=new_measurement and saved is None) as admissions:
        fcntl.flock(admissions, fcntl.LOCK_EX | fcntl.LOCK_NB)
        unfinished = _outstanding(admissions)
        generation = _generation(ctx, admissions, unfinished, saved)
        if new_measurement:
            vote.confirm_generation(ctx, generation)
        elif not saved or saved[0] != generation:
            return False
        if unfinished and not new_measurement:
            return False
        exited = {slot for slot, owner in unfinished.items() if owner == "afgesloten"
                  or _exited(owner)}
        if not _registered_reported(ctx, fd, new_measurement, exited,
                                    (set(unfinished) - exited) | {vote.slot}, generation):
            return False
        for slot in unfinished:
            _note(admissions, slot, "verwerkt")
        stamp = _stamp(os.fstat(admissions.fileno()), generation)
        if unfinished:
            ctx.state_set(_ADMISSIONS_STATE, stamp)
        path = ctx.db.path.with_suffix(".pool-aanmeldingen")
        return stamp == ctx.state_get(_ADMISSIONS_STATE) == _stamp(path.lstat(), generation)


def measurement_visible(ctx: AppContext, request_id: str) -> bool:
    """Lees dezelfde werkers, generatie en kernelopdracht zonder iets te bevestigen."""
    vote = ctx.services.get(SERVICE)
    if not vote or vote.unregistered or vote.ready is None:
        return False
    try:
        flags = os.O_RDONLY | os.O_NONBLOCK | os.O_NOFOLLOW
        # De opruimer houdt het datamapslot vóór hij het werkerslot probeert.
        # Zo kan hij een lezende bewaker nooit voor een losgekoppelde werker aanzien.
        with ctx.db.startup_lock(blocking=False), ExitStack() as held:
            if ctx.state_get(CLEANUP_REQUEST) != request_id:
                return False

            def open_read(path: Path, extra: int = 0) -> int:
                fd = os.open(path, flags | extra)
                held.callback(os.close, fd)
                return fd

            guard = open_read(ctx.db.path.with_suffix(".pool-workers"))
            if not stat.S_ISREG(os.fstat(guard).st_mode):
                return False
            fcntl.flock(guard, fcntl.LOCK_EX | fcntl.LOCK_NB)
            directory = open_read(ctx.db.path.parent, os.O_DIRECTORY)
            if record_lock(directory, 0, probe=True) != fcntl.F_UNLCK:
                return False
            ledger = held.enter_context(_admissions(ctx, readonly=True))
            fcntl.flock(ledger, fcntl.LOCK_SH | fcntl.LOCK_NB)
            pending = _outstanding(ledger)
            generation = pending.pop(0, None)
            if not generation or pending:
                return False
            stamp = _stamp(os.fstat(ledger.fileno()), generation)
            if stamp != _saved_stamp(ctx) or not _registered_reported(
                    ctx, directory, False, set(), {vote.slot}, generation):
                return False
            channel = open_read(ctx.db.path.with_suffix(".pool-fifo"))
            info = os.fstat(channel)
            if not stat.S_ISFIFO(info.st_mode) or vote.channel != [info.st_dev, info.st_ino]:
                return False
            # Linux tee kopieert FIFO-bytes naar een eigen pijp. Een lezer neemt
            # de opdracht of een gelijktijdige intrekking nooit uit de gedeelde FIFO.
            reader, writer = os.pipe2(os.O_NONBLOCK | os.O_CLOEXEC)
            held.callback(os.close, reader)
            held.callback(os.close, writer)
            copied = ctypes.CDLL(None).tee(channel, writer, 33, os.SPLICE_F_NONBLOCK)
            return (copied == 32 and os.read(reader, copied) == request_id.encode("ascii")
                    and stamp == _stamp(ctx.db.path.with_suffix(".pool-aanmeldingen").lstat(),
                                        generation))
    except (AttributeError, OSError, sqlite3.Error, ValueError, TypeError, KeyError):
        return False


def _registered_reported(ctx: AppContext, fd: int, new_measurement: bool, exited: set[int],
                         pending: set[int], generation: str) -> bool:
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
    pending.difference_update(closed)
    reservations = ctx.db.fetch_all("SELECT key, value_json FROM system_state WHERE key GLOB ?",
                                    (_RESERVED + "*",))
    if reservations and not new_measurement:
        return False
    for row in reservations:
        slot = int(row["key"][len(_RESERVED):])
        if slot not in closed and slot not in exited and not _exited(loads(row["value_json"])):
            pending.add(slot)
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
            pending.discard(slot)
            if not new_measurement:
                if slot not in reported:
                    return False
                continue
            ctx.db.execute("DELETE FROM system_state WHERE key=?", (row["key"],))
            continue
        if (record.get("generation") != generation or life != fcntl.F_RDLCK
                or record_lock(fd, slot + 1, probe=True) != fcntl.F_RDLCK):
            return False
        if record["channel"] is not None:
            info = channel.lstat()
            if record["channel"] != [info.st_dev, info.st_ino]:
                return False
        # Een herstelde journaalkopie kan een inmiddels bevestigde aanmelding bevatten.
        pending.discard(slot)
    if pending:
        return False
    if new_measurement:
        for row in reservations:
            ctx.db.execute("DELETE FROM system_state WHERE key=?", (row["key"],))
        for name in closed.values():
            os.rmdir(name, dir_fd=fd)
    return True
