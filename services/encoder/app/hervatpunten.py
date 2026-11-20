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
    kopie: bool = False


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


def heeft_oud_deel(cache: Path, percent: float | None) -> bool:
    # 1.2.2 kan al beelden hebben geschreven vóór de eerste percentagemeting.
    paden = (cache / "resultaat.mkv.deel", cache / "resultaat.mkv")
    return any(p.exists() or p.is_symlink() for p in paden) or bool(
        percent and any(cache.glob("*.deel")))


def heeft_tussenresultaat(cache: Path, percent: float | None) -> bool:
    if cache.is_symlink():
        return False  # De opruimer weigert de koppeling zelf.
    return (cache / MAP).exists() or heeft_oud_deel(cache, percent)


def oud_bestand(cache: Path) -> Path:
    kandidaten = list(cache.glob("*.deel"))
    resultaat = cache / "resultaat.mkv"
    if resultaat.exists() or resultaat.is_symlink():
        kandidaten.append(resultaat)
    if len(kandidaten) != 1 or kandidaten[0].name not in {"resultaat.mkv.deel", "resultaat.mkv"}:
        raise Hervatfout("Het tussenresultaat hoort niet eenduidig bij deze job")
    _bestand(kandidaten[0])
    return kandidaten[0]


def _mediaplan(args: list[str]) -> list[str]:
    plan = args.copy()
    if "-threads" in plan:
        plan[plan.index("-threads") + 1] = "automatisch"
    if "-svtav1-params" in plan:
        i = plan.index("-svtav1-params") + 1
        plan[i] = re.sub(r"(^|:)lp=\d+(?=:|$)", r"\1lp=automatisch", plan[i])
    return plan


def controleer_oud_plan(opgeslagen: str | None, args: list[str]) -> None:
    try:
        oud = json.loads(opgeslagen or "null")
        if not isinstance(oud, list) or not all(isinstance(a, str) for a in oud):
            raise ValueError
        # Een bovengrens op het aantal uitvoerbeelden verandert de prefix niet.
        if len(oud) >= 3 and oud[-3] == "-frames:v" and oud[-2].isdecimal():
            del oud[-3:-1]
        if _mediaplan(oud) == _mediaplan(args):
            return
    except (IndexError, ValueError):
        pass
    raise Hervatfout("De opgeslagen encoderopdracht past niet bij het oude deelbestand")


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


def _meet(path: Path, *, timeout: float = 300) -> Fragment:
    _bestand(path)
    probe = _probe(path, timeout)
    stream = probe["streams"][0]
    frames = int(stream["nb_read_frames"])
    start, end = float(stream["start_time"]), float(probe["format"]["duration"])
    if stream["codec_name"] != "av1" or frames < 1 or not (0 <= start < end < math.inf):
        raise Hervatfout("Het fragment bevat geen volledig decodeerbare AV1-beelden")
    with path.open("rb") as handle:
        os.fsync(handle.fileno())
        digest = hashlib.file_digest(handle, "sha256").hexdigest()
    return Fragment(frames, start, end, digest)


def _leesbaar(stderr: str, onderbroken: bool) -> bool:
    # Bij een harde stop kan de laatste Matroska-cluster zonder trailer eindigen.
    # Alleen die containerdiagnose is toelaatbaar bij het lezen van het origineel.
    return all(onderbroken and re.fullmatch(
        r"(?:\[matroska,webm @ [^]]+\] )?File ended prematurely(?: at pos\..*)?", line.strip())
        for line in stderr.splitlines() if line.strip())


def _beeldtijden(path: Path, interval: list[str], *, onderbroken: bool = False,
                 bronklok: bool = False, timeout: float = 300) -> list[float]:
    try:
        result = subprocess.run(
            ["ffprobe", "-v", "error", "-threads", "1", "-select_streams", "v:0", *interval,
             "-show_frames", "-show_entries",
             "frame=best_effort_timestamp_time:frame_side_data=:format=start_time",
             "-of", "compact=p=1:nk=0", str(path)],
            capture_output=True, text=True, timeout=timeout,
        )
    except subprocess.TimeoutExpired as exc:
        raise Hervatfout("De controle van de laatste beeldtijden duurde te lang") from exc
    if result.returncode or not _leesbaar(result.stderr, onderbroken):
        raise Hervatfout("De laatste beeldtijden konden niet worden gecontroleerd")
    prefix = "frame|best_effort_timestamp_time="
    try:
        regels = result.stdout.splitlines()
        offset = 0.0
        if bronklok:
            # FFmpeg rekent de encoderklok vanaf het begin van de container.
            # Bij AAC-voorloop kan dat tijdstip vóór het eerste videobeeld liggen.
            offset, = (float(r.removeprefix("format|start_time=")) for r in regels
                       if r.startswith("format|start_time="))
            if not math.isfinite(offset):
                raise ValueError
        tijden = [float(line.removeprefix(prefix).partition("|")[0]) - offset
                  for line in regels if line.startswith("frame|")]
        if not all(math.isfinite(t) for t in tijden) or any(
                b <= a for a, b in zip(tijden, tijden[1:], strict=False)):
            raise ValueError
        return tijden
    except ValueError as exc:
        raise Hervatfout("De laatste beeldstroom bevat geen meetbare beeldtijd") from exc


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
                if (type(chunk.kopie) is not bool or type(chunk.beelden) is not int
                        or chunk.beelden <= 0
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
        if i < len(self.delen) and self.delen[i].kopie:
            return self.map / f"prefix-{i:06d}.mkv"
        return self.map / f"beeld-{i:06d}.mkv"

    def borg_prefix(self, invoer: Path, bestand: Path, i: int) -> None:
        """Kopieer uitsluitend de opeenvolgende beelden vanaf de bewezen bronpositie."""
        try:
            self._borg_prefix(invoer, bestand, i)
        except (OSError, ValueError, KeyError, IndexError, subprocess.TimeoutExpired) as exc:
            raise Hervatfout("De beeldprefix kon niet veilig worden vastgelegd") from exc

    def _borg_prefix(self, invoer: Path, bestand: Path, i: int) -> None:
        _bestand(bestand)
        tijden = _beeldtijden(bestand, [], onderbroken=True, timeout=max(300, 2 * self.duur))
        start = sum(d.beelden for d in self.delen[:i])
        # Lees vanaf het eerste beeld zodat ook bij variabele beeldsnelheid de
        # bronpositie vaststaat. Extra pakketten vangen de decoderherordening op.
        bron = _beeldtijden(invoer, ["-read_intervals", f"%+#{start + len(tijden) + 32}"],
                            bronklok=True, timeout=max(300, 2 * self.duur))
        if not tijden or start >= len(bron):
            raise Hervatfout("Geen aaneengesloten AV1-beelden op de verwachte bronpositie")
        if not i:
            # De beeldtijd kan bij het coderen op een halve beeldperiode afronden.
            marge = (bron[1] - bron[0]) / 2 if len(bron) > 1 else 0.0
            if abs(tijden[0] - bron[0]) > marge + 0.002:
                raise Hervatfout("Het deelbestand begint niet bij het eerste bronbeeld")
        else:
            # Het oude meer-sporenbestand kan een andere mux-offset hebben.
            # Vanaf het eerste native fragment geldt steeds dezelfde beeldklok.
            anker = 0
            if i > 1 and self.delen[0].kopie and not (self.map / "beeld-000000.mkv").exists():
                anker = 1
            bronanker = self.delen[0].beelden if anker else 0
            verwacht = bron[start] - bron[bronanker] + self.delen[anker].begin
            if abs(tijden[0] - verwacht) > 0.002:
                raise Hervatfout("Het fragment begint niet op het eerstvolgende bronbeeld")
        aantal = 0
        for tijd, origineel in zip(tijden, bron[start:], strict=False):
            if abs((tijd - tijden[0]) - (origineel - bron[start])) > 0.002:
                break
            aantal += 1
        if i < len(self.delen) and aantal == len(tijden) == self.delen[i].beelden:
            return  # De laatste registratie uit een ouder register klopt al.

        # Laat het origineel én een eventueel oud register staan tot de kopie
        # zelfstandig decodeert. Na een crash mag dezelfde kopie opnieuw ontstaan.
        tijdelijk = self.map / "prefix.nieuw"
        doel = self.map / f"prefix-{i:06d}.mkv"
        result = subprocess.run(
            ["ffmpeg", "-nostdin", "-v", "error", "-y", "-copyts", "-i", str(bestand),
             "-map", "0:v:0", "-c:v", "copy", "-frames:v", str(aantal), "-map_chapters", "-1",
             "-avoid_negative_ts", "disabled", "-f", "matroska", str(tijdelijk)],
            capture_output=True, text=True, timeout=max(300, 2 * self.duur),
        )
        if result.returncode or not _leesbaar(result.stderr, onderbroken=True):
            raise Hervatfout("De AV1-beelden konden niet zonder hercoderen worden gekopieerd")
        timeout = max(300, 2 * self.duur)
        meting = _meet(tijdelijk, timeout=timeout)
        kopie = _beeldtijden(tijdelijk, [], timeout=timeout)
        if meting.beelden != aantal or len(kopie) != aantal or any(
            abs(t - b) > 0.002 for t, b in zip(kopie, tijden[:aantal], strict=True)
        ):
            raise Hervatfout("De gekopieerde AV1-beelden wijken af van het deelbestand")
        einde = meting.einde
        if start + aantal < len(bron):
            einde = meting.begin + bron[start + aantal] - bron[start]
        tijdelijk.replace(doel)
        _sync_map(self.map)
        self.delen[i:] = [Fragment(aantal, meting.begin, einde, meting.sha256, kopie=True)]
        self.bewaar()

    def borg_lopend_fragment(self, invoer: Path) -> None:
        i = len(self.delen)
        bestand = self.pad(i)
        if not bestand.exists() and self.delen and not self.delen[-1].kopie:
            self.borg_prefix(invoer, self.pad(i - 1), i - 1)
        while bestand.exists():
            _bestand(bestand)
            if not bestand.stat().st_size:
                break  # geopende segmentuitgang vóór het eerste pakket
            self.borg_prefix(invoer, bestand, i)
            i = len(self.delen)
            bestand = self.pad(i)
        for pad in self.map.glob("beeld-*.mkv"):
            nummer = pad.stem.removeprefix("beeld-")
            if not nummer.isdecimal() or int(nummer) > i:
                raise Hervatfout("Er ontbreekt een eerder fragment in de jobcache")

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

    def verzamel(self, *, afgerond: bool = False) -> None:
        # De segmentmuxer publiceert een CSV-regel na het sluiten van het bestand.
        # De laatste, nog geopende mapinvoer kan na een harde stop onvolledig zijn.
        index = self.map / "gesloten.csv"
        if not index.is_file():
            return
        try:
            self._lees_index(index, afgerond)
        except (OSError, ValueError, csv.Error, IndexError, KeyError) as exc:
            raise Hervatfout("Het gesloten fragment kon niet worden bevestigd") from exc

    def _lees_index(self, index: Path, afgerond: bool) -> None:
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
            # Bij SIGTERM sluit FFmpeg ook de laatste Matroska af. Een encoder met
            # beeldherordening kan daar nog referentiebeelden zonder de tussenliggende
            # beelden in schrijven. Alleen een volgend fragment of een geslaagde
            # procesexit bevestigt dat ook dit beeldinterval is afgewerkt.
            if not afgerond and not self.pad(len(self.delen) + 1).is_file():
                break
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
        # Behoud ook beeldtijden buiten het nominale fps-raster, bijvoorbeeld
        # wanneer een volgend hoofdstuk na een langere beeldpauze begint.
        "-enc_time_base:v",
        "-1",
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
        end = chunk.einde
        if i + 1 < len(state.delen) and not chunk.kopie:
            end = state.delen[i + 1].begin
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
    identity = [
        job_id,
        str(source),
        stat.st_dev,
        stat.st_ino,
        stat.st_size,
        stat.st_mtime_ns,
        stat.st_ctime_ns,
        _mediaplan(args),
    ]
    binding = hashlib.sha256(json.dumps(identity).encode()).hexdigest()
    state = await asyncio.to_thread(Hervatpunt.lees, cache)
    if state is None:
        state = Hervatpunt(cache / MAP, binding, duration, [], gpu=gpu)
        state.map.mkdir(exist_ok=True)
        state.bewaar()
        _sync_map(cache)
    elif state.binding != binding:
        if state.delen or state.pad(0).exists() or Path(args[-1]).exists():
            raise Hervatfout("Bron of encode-instellingen wijken af van het geverifieerde werk")
        state.binding = binding
        state.duur = duration
        state.gpu = gpu
        state.bewaar()
    invoer = Path(args[args.index("-i") + 1])
    deel = Path(args[-1])
    if not state.compleet:
        if not state.delen and heeft_oud_deel(cache, None):
            if state.pad(0).exists():
                raise Hervatfout("Zowel een oud deelbestand als een nieuw fragment aangetroffen")
            await asyncio.to_thread(state.borg_prefix, invoer, oud_bestand(cache), 0)
        await asyncio.to_thread(state.borg_lopend_fragment, invoer)
    frames = sum(chunk.beelden for chunk in state.delen)
    if frames:
        vastgelegd(state.percent)
    expected: int | None = None
    if frames and not state.compleet:
        # Ook een stop tussen het laatste beeld en het schrijven van 'compleet'
        # hervat de mux. De encoder ontvangt geen eerder vastgelegde beelden.
        original = await asyncio.to_thread(_probe, invoer, max(300.0, 2 * duration))
        try:
            expected = int(original["streams"][0]["nb_read_frames"])
        except (KeyError, IndexError, TypeError, ValueError) as exc:
            raise Hervatfout("Het aantal beelden van de bron kon niet worden bevestigd") from exc
        if frames > expected:
            raise Hervatfout("Meer hervatbare beelden dan bronbeelden")
    if not state.compleet and frames != expected:
        (state.map / "gesloten.csv").unlink(missing_ok=True)
        process = asyncio.create_task(run(_video(args, state, frames)))
        ok = False
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
                await asyncio.to_thread(state.verzamel, afgerond=ok)
                if not ok:
                    await asyncio.to_thread(state.borg_lopend_fragment, invoer)
                    if state.delen:
                        vastgelegd(state.percent)
        if not ok:
            return False
    if not state.delen or (
        expected is not None and sum(c.beelden for c in state.delen) != expected
    ):
        raise Hervatfout("Het aantal hervatte beelden past niet bij de bron")
    vastgelegd(state.percent)
    state.compleet = True
    state.bewaar()
    mux = _mux(args, state)
    if not frames and not any(f.kopie for f in state.delen):
        return await run(mux)
    # Houd bij een afgebroken mux ook het oude deelbestand intact tot de
    # aansluiting tussen de overgenomen beelden en de nieuwe beelden klopt.
    samengevoegd = state.map / "samengevoegd.mkv"
    if not await run([*mux[:-1], str(samengevoegd)]):
        return False
    timeout = max(300, 2 * duration)
    bron = await asyncio.to_thread(_beeldtijden, invoer, [], timeout=timeout)
    resultaat = await asyncio.to_thread(_beeldtijden, samengevoegd, [], timeout=timeout)
    if not bron or len(resultaat) != len(bron) or any(
        abs((b - bron[0]) - (r - resultaat[0])) > 0.002
        for b, r in zip(bron, resultaat, strict=True)
    ):
        raise Hervatfout("De beeldtijden van het samengevoegde resultaat wijken af van de bron")
    samengevoegd.replace(deel)
    return True
