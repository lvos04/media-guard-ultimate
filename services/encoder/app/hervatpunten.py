"""Hervat een AV1-encode met gesloten Matroska-fragmenten uit de eigen jobcache."""

from __future__ import annotations

import asyncio
import csv
import hashlib
import json
import math
import os
import re
import subprocess
from collections.abc import Callable, Coroutine
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any, Self

from app.db import Database
from app.vervanging import file_sha256

INTERVAL = 60
MAP = "hervatpunten"


class Hervatfout(RuntimeError):
    """Een aan de job gebonden tussenresultaat kon niet worden bevestigd."""


@dataclass(frozen=True)
class Fragment:
    beelden: int
    begin: float
    einde: float
    sha256: str


@dataclass(frozen=True)
class GpuKeuze:
    factor: float
    hardwaredecode: bool
    vooruitkijken: bool


def _sync_map(path: Path) -> None:
    fd = os.open(path, os.O_RDONLY | os.O_DIRECTORY)
    try:
        os.fsync(fd)
    finally:
        os.close(fd)


def _bestand(path: Path) -> None:
    if path.is_symlink() or not path.is_file():
        raise Hervatfout(f"Het verwachte gewone bestand ontbreekt: {path.name}")


def eerder_geverifieerd(database: Database, cache: Path, job: int) -> bool:
    """Ook vóór fragmentregistratie kon een job al bij de eindcontrole zijn."""
    result = cache / "resultaat.mkv"
    if not result.exists():
        return False
    _bestand(result)
    return database.fetch_one(
        "SELECT 1 FROM verificatie v JOIN job j ON j.id=v.job_id "
        "WHERE v.job_id=? AND v.geslaagd=1 AND j.bron_inode IS NOT NULL", (job,)
    ) is not None


def _probe(path: Path, timeout: float = 300) -> dict[str, Any]:
    try:
        result = subprocess.run(
            [
                "ffprobe",
                "-v",
                "error",
                "-threads",
                "1",
                "-select_streams",
                "v:0",
                "-count_frames",
                "-show_entries",
                "stream=codec_name,nb_read_frames,start_time:format=duration",
                "-of",
                "json",
                str(path),
            ],
            capture_output=True,
            text=True,
            check=False,
            timeout=timeout,
        )
    except subprocess.TimeoutExpired as exc:
        raise Hervatfout("De beeldmeting van het hervatpunt duurde te lang") from exc
    if result.returncode or result.stderr.strip():
        raise Hervatfout("De beeldstroom van het hervatpunt kon niet volledig worden gelezen")
    return dict(json.loads(result.stdout))


def _meet(path: Path) -> Fragment:
    _bestand(path)
    probe = _probe(path)
    stream = probe["streams"][0]
    frames = int(stream["nb_read_frames"])
    start, end = float(stream["start_time"]), float(probe["format"]["duration"])
    if stream["codec_name"] != "av1" or frames < 1 or not (0 <= start < end < math.inf):
        raise Hervatfout("Het fragment bevat geen volledig decodeerbare AV1-beelden")
    with path.open("rb") as handle:
        os.fsync(handle.fileno())
        digest = hashlib.file_digest(handle, "sha256").hexdigest()
    return Fragment(frames, start, end, digest)


@dataclass
class Hervatpunt:
    map: Path
    binding: str
    duur: float
    delen: list[Fragment]
    compleet: bool = False
    gpu: GpuKeuze | None = None

    @classmethod
    def lees(cls, cache: Path) -> Self | None:
        directory = cache / MAP
        if cache.is_symlink() or directory.is_symlink():
            raise Hervatfout("Onverwachte koppeling in de hervatmap")
        if not directory.exists():
            return None
        try:
            if not directory.is_dir() or any(p.is_symlink() for p in directory.iterdir()):
                raise Hervatfout("Onveilig pad in de hervatmap")
            journal = directory / "stand.json"
            if not journal.exists() and all(p.name == "stand.nieuw" for p in directory.iterdir()):
                return None  # onderbroken vóór de eerste encoderopdracht
            _bestand(journal)
            data = json.loads(journal.read_text())
            state = cls(
                directory,
                data["binding"],
                data["duur"],
                [Fragment(**chunk) for chunk in data["delen"]],
                data["compleet"],
                GpuKeuze(**data["gpu"]) if data.get("gpu") else None,
            )
            for i, chunk in enumerate(state.delen):
                path = state.pad(i)
                _bestand(path)
                if (type(chunk.beelden) is not int or chunk.beelden <= 0
                        or not 0 <= chunk.begin < chunk.einde < math.inf
                        or (i and chunk.begin <= state.delen[i - 1].begin)):
                    raise Hervatfout(f"Ongeldige beeldmeting van fragment {i}")
                if file_sha256(path) != chunk.sha256:
                    raise Hervatfout(f"Controlegetal van fragment {i} wijkt af")
            if not math.isfinite(state.duur) or state.duur <= 0:
                raise Hervatfout("Ongeldige duur in het hervatbewijs")
            state.verzamel()
            return state
        except (ValueError, OSError, KeyError, TypeError, IndexError) as exc:
            raise Hervatfout("Het hervatbewijs is onleesbaar") from exc

    def pad(self, i: int) -> Path:
        return self.map / f"beeld-{i:06d}.mkv"

    @property
    def percent(self) -> float:
        return min(99.9, round(self.delen[-1].einde * 100 / self.duur, 1)) if self.delen else 0.0

    def bewaar(self) -> None:
        data = {
            "binding": self.binding,
            "duur": self.duur,
            "compleet": self.compleet,
            "gpu": asdict(self.gpu) if self.gpu else None,
            "delen": [asdict(d) for d in self.delen],
        }
        pending = self.map / "stand.nieuw"
        with pending.open("w") as handle:
            json.dump(data, handle)
            handle.flush()
            os.fsync(handle.fileno())
        pending.replace(self.map / "stand.json")
        _sync_map(self.map)

    def verzamel(self) -> None:
        # De segmentmuxer publiceert een CSV-regel na het sluiten van het bestand.
        # De laatste, nog geopende mapinvoer kan na een harde stop onvolledig zijn.
        index = self.map / "gesloten.csv"
        if not index.is_file():
            return
        try:
            self._lees_index(index)
        except (OSError, ValueError, csv.Error, IndexError, KeyError) as exc:
            raise Hervatfout("Het gesloten fragment kon niet worden bevestigd") from exc

    def _lees_index(self, index: Path) -> None:
        for line in index.read_text().splitlines(keepends=True):
            if not line.endswith("\n"):
                break
            name, _, _ = next(csv.reader([line]))
            expected = self.pad(len(self.delen))
            if name != expected.name:
                match = re.fullmatch(r"beeld-(\d{6,})\.mkv", name)
                if match and int(match[1]) < len(self.delen):
                    continue  # dit gesloten fragment staat al in het duurzame register
                raise Hervatfout("De volgorde van de gesloten fragmenten wijkt af")
            chunk = _meet(expected)
            if self.delen and chunk.begin <= self.delen[-1].begin:
                raise Hervatfout("De beeldtijd loopt niet op")
            self.delen.append(chunk)
            self.bewaar()


def _video(args: list[str], state: Hervatpunt, frames: int) -> list[str]:
    # Alleen beeld fragmenteren. Audio, ondertitels, bijlagen en hoofdstukken
    # worden eenmaal van de oorspronkelijke invoer gemuxt zodat er geen naad ontstaat.
    video = list(args[:-1])
    i = video.index("-i") + 2
    while i < len(video):
        flag = video[i]
        if (
            (flag == "-map" and video[i + 1] != "0:v:0")
            or flag.startswith("-c:s:")
            or flag in ("-map_chapters", "-f")
        ):
            del video[i : i + 2]
        else:
            i += 1
    if frames:
        select = f"select=gte(n\\,{frames})"
        if "-vf" in video:
            n = video.index("-vf") + 1
            video[n] = f"{select},{video[n]}"
        else:
            video += ["-vf", select]
    return [
        *video,
        "-map_chapters",
        "-1",
        "-fps_mode",
        "passthrough",
        "-f",
        "segment",
        "-segment_time",
        str(INTERVAL),
        "-segment_format",
        "matroska",
        "-segment_list_type",
        "csv",
        "-segment_list",
        str(state.map / "gesloten.csv"),
        "-segment_start_number",
        str(len(state.delen)),
        str(state.map / "beeld-%06d.mkv"),
    ]


def _mux(args: list[str], state: Hervatpunt) -> list[str]:
    concat = state.map / "beeld.ffconcat"
    lines = ["ffconcat version 1.0"]
    for i, chunk in enumerate(state.delen):
        end = state.delen[i + 1].begin if i + 1 < len(state.delen) else chunk.einde
        lines += [f"file {state.pad(i).name}", f"duration {end - chunk.begin:.6f}"]
    concat.write_text("\n".join(lines) + "\n")
    tracks = []
    codecs = []
    for i, arg in enumerate(args[:-1]):
        if arg == "-map" and args[i + 1] != "0:v:0":
            tracks.extend(["-map", args[i + 1].replace("0:", "1:", 1)])
        if arg.startswith("-c:s:"):
            codecs.extend([arg, args[i + 1]])
    return [
        args[0],
        "-hide_banner",
        "-nostats",
        "-progress",
        "pipe:1",
        "-y",
        "-itsoffset",
        str(state.delen[0].begin),
        "-f",
        "concat",
        "-safe",
        "1",
        "-i",
        str(concat),
        "-i",
        args[args.index("-i") + 1],
        "-map",
        "0:v:0",
        *tracks,
        "-c",
        "copy",
        *codecs,
        "-map_metadata",
        "1",
        "-map_chapters",
        "1",
        "-max_muxing_queue_size",
        "9999",
        "-f",
        "matroska",
        args[-1],
    ]


async def encode(
    args: list[str],
    source: Path,
    job_id: int,
    duration: float,
    run: Callable[[list[str]], Coroutine[Any, Any, bool]],
    vastgelegd: Callable[[float], None],
    gpu: GpuKeuze | None = None,
) -> bool:
    cache = Path(args[-1]).parent
    stat = source.stat()
    # Het live CPU-budget mag na een herstart een ander aantal threads toekennen.
    # De media-instellingen (codec, kwaliteit, filters, sporen) blijven gebonden.
    plan = list(args)
    if "-threads" in plan:
        plan[plan.index("-threads") + 1] = "automatisch"
    if "-svtav1-params" in plan:
        i = plan.index("-svtav1-params") + 1
        plan[i] = re.sub(r"(^|:)lp=\d+(?=:|$)", r"\1lp=automatisch", plan[i])
    identity = [
        job_id,
        str(source),
        stat.st_dev,
        stat.st_ino,
        stat.st_size,
        stat.st_mtime_ns,
        stat.st_ctime_ns,
        plan,
    ]
    binding = hashlib.sha256(json.dumps(identity).encode()).hexdigest()
    state = await asyncio.to_thread(Hervatpunt.lees, cache)
    if state is None:
        state = Hervatpunt(cache / MAP, binding, duration, [], gpu=gpu)
        state.map.mkdir(exist_ok=True)
        state.bewaar()
        _sync_map(cache)
    elif state.binding != binding:
        if state.delen:
            raise Hervatfout("Bron of encode-instellingen wijken af van het geverifieerde werk")
        state.binding = binding
        state.duur = duration
        state.gpu = gpu
        state.bewaar()
    frames = sum(chunk.beelden for chunk in state.delen)
    expected: int | None = None
    if frames and not state.compleet:
        # Ook een stop tussen het laatste beeld en het schrijven van 'compleet'
        # hervat de mux. De encoder ontvangt geen eerder vastgelegde beelden.
        original = await asyncio.to_thread(_probe, Path(args[args.index("-i") + 1]),
                                           max(300.0, 2 * duration))
        try:
            expected = int(original["streams"][0]["nb_read_frames"])
        except (KeyError, IndexError, TypeError, ValueError) as exc:
            raise Hervatfout("Het aantal beelden van de bron kon niet worden bevestigd") from exc
        if frames > expected:
            raise Hervatfout("Meer hervatbare beelden dan bronbeelden")
    if not state.compleet and frames != expected:
        (state.map / "gesloten.csv").unlink(missing_ok=True)
        process = asyncio.create_task(run(_video(args, state, frames)))
        try:
            while not process.done():
                await asyncio.wait({process}, timeout=5.0)
                if not process.done():
                    await asyncio.to_thread(state.verzamel)
                    if state.delen:
                        vastgelegd(state.percent)
        finally:
            # Laat de procesbewaking ook bij een schrijffout haar eigen kind opruimen.
            try:
                ok = await process
            finally:
                await asyncio.to_thread(state.verzamel)
        if not ok:
            return False
    if not state.delen or (
        expected is not None and sum(c.beelden for c in state.delen) != expected
    ):
        raise Hervatfout("Het aantal hervatte beelden past niet bij de bron")
    vastgelegd(state.percent)
    state.compleet = True
    state.bewaar()
    return await run(_mux(args, state))
