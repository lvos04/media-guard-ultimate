"""Lees een stilstaande SQLite-bestandsfamilie uitsluitend op een afzonderlijke kopie."""

import hashlib
import shutil
import sqlite3
import stat
import tempfile
from collections.abc import Iterator, Mapping
from contextlib import closing, contextmanager
from pathlib import Path
from typing import NamedTuple


class WalCommit(NamedTuple):
    frames: int
    end: int
    sha256: str


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
            # Een hergebruikte WAL kan nog volledige frames van de vorige cyclus bevatten.
            # Beschadigde of onbewezen frames van de huidige cyclus mogen niet verdwijnen.
            if (size - end) % frame_size or not frames:
                raise sqlite3.DatabaseError("de laatste SQLite-WAL-stand is onbewezen")
            for offset in range(end, size, frame_size):
                stream.seek(offset + 8)
                salts = stream.read(8)
                # Bij elke herstart verhoogt SQLite salt-1 en vervangt het salt-2.
                # Eén beschadigd zout mag dus geen oudere commit geldig laten lijken.
                age = (int.from_bytes(header[16:20]) - int.from_bytes(salts[:4])) % (1 << 32)
                if not 0 < age < (1 << 31) or salts[4:] == header[20:24]:
                    raise sqlite3.DatabaseError("ongelezen frames in de vorige SQLite-WAL")
    return WalCommit(frames, end, wal_prefix_hash(wal, end))


@contextmanager
def checked_database(work: Path, files: Mapping[str, Path], *,
                     strict_wal: bool = True) -> Iterator[tuple[sqlite3.Connection, WalCommit]]:
    """Combineer ook verspreid liggende originelen. Alleen SQLite op de kopie mag herstellen."""
    with tempfile.TemporaryDirectory(dir=work, prefix=".controle-sqlite-") as tmp:
        main = Path(tmp) / "controle.db"
        for tail, source in files.items():
            if tail == "-shm":
                continue
            if not stat.S_ISREG(source.lstat().st_mode):
                raise sqlite3.DatabaseError(f"{source.name} is geen regulier databasebestand")
            shutil.copyfile(source, main.with_name(main.name + tail))
        if "" not in files or not main.stat().st_size:
            raise sqlite3.DatabaseError("het SQLite-hoofdbestand ontbreekt of is leeg")
        wal = main.with_name(main.name + "-wal")
        size = wal.stat().st_size if "-wal" in files else 0
        with closing(sqlite3.connect(main)) as conn:
            conn.execute("PRAGMA temp_store=MEMORY")
            if conn.execute("PRAGMA integrity_check").fetchall() != [("ok",)]:
                raise sqlite3.DatabaseError("de SQLite-integriteitscontrole meldt schade")
            for name, in conn.execute("SELECT name FROM sqlite_schema WHERE type='table'"):
                quoted = '"' + name.replace('"', '""') + '"'
                conn.execute(f"SELECT * FROM {quoted} LIMIT 1").fetchone()  # noqa: S608
            yield conn, _committed_wal(conn, wal, size, strict_wal)
