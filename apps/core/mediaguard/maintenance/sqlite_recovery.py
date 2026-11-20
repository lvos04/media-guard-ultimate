"""Lees een stilstaande SQLite-bestandsfamilie uitsluitend op een afzonderlijke kopie."""

import hashlib
import shutil
import sqlite3
import stat
import struct
import tempfile
from collections.abc import Iterator, Mapping
from contextlib import closing, contextmanager
from pathlib import Path
from typing import NamedTuple


class WalCommit(NamedTuple):
    frames: int
    end: int
    sha256: str
    cycle: tuple[int, int, int] | None = None


def database_files(main: Path) -> dict[str, Path]:
    files = {"": main}
    for tail in ("-wal", "-journal"):
        side = main.with_name(main.name + tail)
        try:
            side.lstat()
        except FileNotFoundError:
            continue
        files[tail] = side
    return files


def wal_prefix_hash(wal: Path, end: int) -> str:
    digest = hashlib.sha256()
    with wal.open("rb") as stream:
        remaining = end
        while remaining:
            block = stream.read(min(remaining, 1 << 20))
            if not block:
                raise sqlite3.DatabaseError("de laatste SQLite-WAL-stand ontbreekt")
            digest.update(block)
            remaining -= len(block)
    return digest.hexdigest()


def _committed_wal(conn: sqlite3.Connection, wal: Path, size: int, strict: bool) -> WalCommit:
    """SQLite herbouwt de wal-index op de kopie en bepaalt het laatste commitframe."""
    if not size:
        return WalCommit(0, 0, "")
    busy, frames, written = conn.execute("PRAGMA wal_checkpoint(PASSIVE)").fetchone()
    page_size, = conn.execute("PRAGMA page_size").fetchone()
    if not wal.is_file() or wal.stat().st_size != size:
        raise sqlite3.DatabaseError("SQLite heeft het gekopieerde WAL-logboek verworpen")
    frame_size = page_size + 24
    end = 32 + frames * frame_size
    with wal.open("rb") as stream:
        header = stream.read(32)
        if (len(header) != 32 or int.from_bytes(header[:4]) not in (0x377F0682, 0x377F0683)
                or int.from_bytes(header[4:8]) != 3007000
                or int.from_bytes(header[8:12]) != page_size
                or busy or frames < 0 or written != frames or end > size):
            raise sqlite3.DatabaseError("het SQLite-WAL-logboek is onleesbaar")
        if strict and end != size:
            if (size - end) % frame_size:
                raise sqlite3.DatabaseError("de laatste SQLite-WAL-stand is onbewezen")
            # SQLite levert het laatste commitframe. Verifieer de keten van eventuele
            # volgende frames: een volledige, ongecommitteerde transactie mag blijven liggen.
            order = ">" if header[3] & 1 else "<"
            stream.seek(end - page_size - 8 if frames else 24)
            a, b = struct.unpack(">II", stream.read(8))
            old_cycle = False
            for offset in range(end, size, frame_size):
                stream.seek(offset)
                frame = stream.read(24)
                salts = frame[8:16]
                if not old_cycle and salts == header[16:24] and frame[4:8] == bytes(4):
                    block = frame[:8] + stream.read(page_size)
                    for x, y in struct.iter_unpack(order + "II", block):
                        a = (a + x + b) & 0xffffffff
                        b = (b + y + a) & 0xffffffff
                    if (a, b) == struct.unpack(">II", frame[16:24]):
                        continue
                # Bij elke herstart verhoogt SQLite salt-1 en vervangt het salt-2.
                # Eén beschadigd zout mag dus geen oudere commit geldig laten lijken.
                age = (int.from_bytes(header[16:20]) - int.from_bytes(salts[:4])) % (1 << 32)
                if not 0 < age < (1 << 31) or salts[4:] == header[20:24]:
                    raise sqlite3.DatabaseError("ongelezen frames in de vorige SQLite-WAL")
                old_cycle = True
    return WalCommit(frames, end, wal_prefix_hash(wal, end), struct.unpack(">III", header[12:24]))


@contextmanager
def copied_family(work: Path, files: Mapping[str, Path]) -> Iterator[dict[str, Path]]:
    """Verzamel de bestanden één keer, ook als de vorige database deels is verplaatst."""
    with tempfile.TemporaryDirectory(dir=work, prefix=".controle-sqlite-") as tmp:
        main = Path(tmp) / "controle.db"
        copied = {}
        for tail, source in files.items():
            if tail == "-shm":
                continue
            if not stat.S_ISREG(source.lstat().st_mode):
                raise sqlite3.DatabaseError(f"{source.name} is geen regulier databasebestand")
            copied[tail] = main.with_name(main.name + tail)
            shutil.copyfile(source, copied[tail])
        if "" not in files or not main.stat().st_size:
            raise sqlite3.DatabaseError("het SQLite-hoofdbestand ontbreekt of is leeg")
        yield copied


@contextmanager
def checked_database(work: Path, files: Mapping[str, Path], *,
                     strict_wal: bool = True,
                     wal_end: int | None = None) -> Iterator[tuple[sqlite3.Connection, WalCommit]]:
    """Alleen SQLite op de kopie mag herstellen en de gecommitteerde inhoud overdragen."""
    with copied_family(work, files) as copied:
        main = copied[""]
        wal = main.with_name(main.name + "-wal")
        size = wal.stat().st_size if "-wal" in files else 0
        if wal_end is not None:
            if not 0 < wal_end <= size:
                raise sqlite3.DatabaseError("het bewezen WAL-prefix ontbreekt")
            with wal.open("r+b") as stream:
                stream.truncate(wal_end)
            size = wal_end
        with closing(sqlite3.connect(main)) as source, \
                closing(sqlite3.connect(main.with_name("stand.db"))) as snapshot:
            commit = _committed_wal(source, wal, size, strict_wal)
            source.backup(snapshot)
            snapshot.execute("PRAGMA temp_store=MEMORY")
            if snapshot.execute("PRAGMA integrity_check").fetchall() != [("ok",)]:
                raise sqlite3.DatabaseError("de SQLite-integriteitscontrole meldt schade")
            yield snapshot, commit
