"""Kwaliteitsmetingen (VMAF/SSIM) en vergelijkingsbeelden.

Verbeterpunt 29: elke meting heeft een time-out, draait op de encodekernen (de harde
CPU-grens, net als de encode zelf) met lage voorrang, bevriest bij een noodstop en stopt
bij een service-stop. Lukt geen enkele steekproef, dan meldt ``measure_quality`` dat
(``unmeasured``) in plaats van stil 'geen score' terug te geven.
"""

from __future__ import annotations

import asyncio
import json
import os
import re
import shutil
import signal
import time
from collections.abc import Callable
from contextlib import suppress
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from app.config import Settings
from app.db import Database

# Eén steekproef van 20 s beeld duurt normaal minder dan een minuut; ruim daarboven is er
# iets mis (vastgelopen filter, overbelaste machine). Een stilstaand beeld: 2 minuten.
MEASURE_TIMEOUT_SECONDS = 900.0
FRAME_TIMEOUT_SECONDS = 120.0


def measure_prefix(database: Database | None, job_id: int | None = None) -> list[str]:
    """Zelfde grenzen als de encode: alleen de encodekernen (taskset) en lage CPU-/I/O-
    voorrang. Zonder database (proefdraaien zonder job) alleen de voorrang."""
    prefix: list[str] = []
    if database is not None and shutil.which("ffmpeg"):
        with suppress(Exception):
            from app.cpuconfine import confine_prefix, encode_percent, job_part

            prefix += confine_prefix(encode_percent(database),
                                     job_part(database, job_id) if job_id else None)
    if shutil.which("nice"):
        prefix += ["nice", "-n", "10"]
    if shutil.which("ionice"):
        prefix += ["ionice", "-c", "2", "-n", "7"]
    return prefix


def _signal_group(pid: int, sig: signal.Signals) -> bool:
    try:
        os.killpg(pid, sig)
    except (ProcessLookupError, PermissionError):
        return False
    return True


MeasureOutcome = tuple[int | None, bytes, str | None]


async def run_measure(command: list[str], *, prefix: list[str] | None = None,
                      timeout: float = MEASURE_TIMEOUT_SECONDS) -> MeasureOutcome:
    """Draai een meetcommando. Geeft (exitcode, uitvoer, probleem); probleem is een
    Nederlandse reden bij een time-out, anders None.

    Bij een noodstop wordt de meting bevroren (telt niet mee voor de time-out); bij een
    service-stop gestopt met ``ProcessInterrupted`` (de job gaat terug in de wachtrij)."""
    from app import noodstop
    from app.uitvoerder import ProcessInterrupted, shutdown_requested

    if shutdown_requested():
        raise ProcessInterrupted("kwaliteitsmeting niet gestart: service- of systeemstop")
    process = await asyncio.create_subprocess_exec(
        *(prefix or []), *command, stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.STDOUT, start_new_session=True)
    reader = asyncio.ensure_future(process.communicate())
    loop = asyncio.get_running_loop()
    used = 0.0
    frozen = False
    try:
        while True:
            started = loop.time()
            done, _pending = await asyncio.wait({reader}, timeout=1.0)
            if done:
                output, _ = reader.result()
                return process.returncode, output or b"", None
            if not frozen:
                used += loop.time() - started
            if shutdown_requested():
                raise ProcessInterrupted("kwaliteitsmeting onderbroken door service- of "
                                         "systeemstop")
            if noodstop.active() and not frozen:
                frozen = _signal_group(process.pid, signal.SIGSTOP)
            elif frozen and not noodstop.active():
                _signal_group(process.pid, signal.SIGCONT)
                frozen = False
            if used >= timeout:
                minutes = max(1, int(timeout // 60))
                return None, b"", f"time-out: meting duurde langer dan {minutes} min"
    finally:
        if process.returncode is None:
            if frozen:
                _signal_group(process.pid, signal.SIGCONT)
            _signal_group(process.pid, signal.SIGKILL)
            with suppress(Exception):
                await asyncio.wait_for(process.wait(), timeout=10)
        if not reader.done():
            reader.cancel()
            with suppress(BaseException):
                await reader


async def vmaf_available() -> bool:
    try:
        process = await asyncio.create_subprocess_exec(
            "ffmpeg",
            "-hide_banner",
            "-filters",
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.STDOUT,
        )
        output, _ = await asyncio.wait_for(process.communicate(), timeout=15)
    except (FileNotFoundError, TimeoutError):
        return False
    text = output.decode("utf-8", errors="replace")
    return process.returncode == 0 and re.search(r"\blibvmaf\b", text) is not None


async def extract_frame(source: Path, target: Path, second: float, *,
                        prefix: list[str] | None = None) -> bool:
    target.parent.mkdir(parents=True, exist_ok=True)
    code, _output, _problem = await run_measure(
        ["ffmpeg", "-hide_banner", "-loglevel", "error", "-y", "-ss", str(max(0.0, second)),
         "-i", str(source), "-frames:v", "1", "-q:v", "2", str(target)],
        prefix=prefix, timeout=FRAME_TIMEOUT_SECONDS)
    return code == 0 and target.is_file() and target.stat().st_size > 0


async def compute_vmaf(
    result: Path,
    source: Path,
    start_second: float,
    duration: float,
    result_start_second: float | None = None,
    *,
    prefix: list[str] | None = None,
    timeout: float = MEASURE_TIMEOUT_SECONDS,
) -> float | None:
    """Meet alleen als het echte libvmaf-filter beschikbaar is; verzin nooit een score.

    ``result_start_second``: een proefclip begint zelf al bij ``start_second`` van
    de bron en moet dus vanaf 0 worden gelezen (anders valt het venster voorbij
    het einde van de clip en is er geen score).
    """

    score, _problem = await vmaf_sample(result, source, start_second, duration,
                                        result_start_second, prefix=prefix, timeout=timeout)
    return score


async def vmaf_sample(
    result: Path,
    source: Path,
    start_second: float,
    duration: float,
    result_start_second: float | None = None,
    *,
    prefix: list[str] | None = None,
    timeout: float = MEASURE_TIMEOUT_SECONDS,
) -> tuple[float | None, str | None]:
    """VMAF van één steekproef: (score, None) of (None, Nederlandse reden)."""

    if not await vmaf_available():
        return None, "libvmaf ontbreekt in ffmpeg"
    sample_duration = min(30.0, max(0.5, duration))
    result_start = start_second if result_start_second is None else result_start_second
    command = [
        "ffmpeg",
        "-hide_banner",
        "-ss",
        str(max(0.0, result_start)),
        "-t",
        str(sample_duration),
        "-i",
        str(result),
        "-ss",
        str(max(0.0, start_second)),
        "-t",
        str(sample_duration),
        "-i",
        str(source),
        "-lavfi",
        "[0:v]scale=1920:1080:flags=bicubic[a];"
        "[1:v]scale=1920:1080:flags=bicubic[b];"
        "[a][b]libvmaf=n_threads=4",
        "-f",
        "null",
        "-",
    ]
    code, output, problem = await run_measure(command, prefix=prefix, timeout=timeout)
    if problem:
        return None, problem
    if code != 0:
        return None, f"VMAF-meting faalde (exitcode {code}){_last_error(output)}"
    match = re.search(rb"VMAF score:\s*([0-9]+(?:\.[0-9]+)?)", output)
    if match is None:
        return None, "VMAF-meting gaf geen score"
    score = float(match.group(1))
    if not 0.0 <= score <= 100.0:
        return None, "VMAF-score buiten 0–100"
    return score, None


def _last_error(output: bytes) -> str:
    lines = [x.strip() for x in output.decode("utf-8", errors="replace").splitlines()
             if x.strip()]
    errors = [x for x in lines if any(m in x.lower() for m in ("error", "invalid", "fail"))]
    last = (errors or lines)[-1:] if (errors or lines) else []
    return f": {last[0][:160]}" if last else ""


async def compute_ssim(
    result: Path, source: Path, start_second: float, duration: float, *,
    prefix: list[str] | None = None, timeout: float = MEASURE_TIMEOUT_SECONDS,
) -> float | None:
    """SSIM (alle vlakken) op een steekproef; terugval als libvmaf ontbreekt."""
    score, _problem = await ssim_sample(result, source, start_second, duration,
                                        prefix=prefix, timeout=timeout)
    return score


async def ssim_sample(
    result: Path, source: Path, start_second: float, duration: float, *,
    prefix: list[str] | None = None, timeout: float = MEASURE_TIMEOUT_SECONDS,
) -> tuple[float | None, str | None]:
    """SSIM van één steekproef: (score, None) of (None, Nederlandse reden)."""
    command = [
        "ffmpeg", "-hide_banner", "-ss", str(max(0.0, start_second)), "-t", str(duration),
        "-i", str(result), "-ss", str(max(0.0, start_second)), "-t", str(duration),
        "-i", str(source), "-lavfi",
        "[0:v]scale=1920:1080:flags=bicubic,format=yuv420p[a];"
        "[1:v]scale=1920:1080:flags=bicubic,format=yuv420p[b];[a][b]ssim",
        "-f", "null", "-",
    ]
    code, output, problem = await run_measure(command, prefix=prefix, timeout=timeout)
    if problem:
        return None, problem
    if code != 0:
        return None, f"SSIM-meting faalde (exitcode {code}){_last_error(output)}"
    match = re.search(rb"SSIM .*All:([0-9.]+)", output)
    if match is None:
        return None, "SSIM-meting gaf geen score"
    score = float(match.group(1))
    if not 0.0 <= score <= 1.0:
        return None, "SSIM-score buiten 0–1"
    return score, None


async def extract_still(source: Path, target: Path, second: float, *,
                        prefix: list[str] | None = None) -> bool:
    """Klein vergelijkingsbeeld (max. 960 px breed) voor de pagina Conversies."""
    target.parent.mkdir(parents=True, exist_ok=True)
    code, _output, _problem = await run_measure(
        ["ffmpeg", "-hide_banner", "-loglevel", "error", "-y", "-ss", str(max(0.0, second)),
         "-i", str(source), "-frames:v", "1", "-vf", "scale='min(960,iw)':-2", "-q:v", "4",
         str(target)], prefix=prefix, timeout=FRAME_TIMEOUT_SECONDS)
    return code == 0 and target.is_file() and target.stat().st_size > 0


SAMPLE_COUNT = 3
SAMPLE_LENGTH = 20.0


def sample_points(duration: float) -> list[float]:
    """Drie steekproeven van 20 s op 20/50/80 % (korte bestanden: één in het midden)."""
    if duration < SAMPLE_LENGTH * 4:
        return [max(0.0, duration / 2 - SAMPLE_LENGTH / 2)]
    return [max(0.0, duration * f - SAMPLE_LENGTH / 2) for f in (0.2, 0.5, 0.8)]


async def measure_quality(
    database: Database, settings: Settings, job_id: int, source: Path, result: Path,
    duration: float, on_sample: Callable[[int, int], None] | None = None,
) -> dict[str, Any]:
    """Kwaliteitsmeting na de encode (rapport 5, M4): VMAF (of SSIM als libvmaf ontbreekt)
    op drie steekproeven van 20 s, plus drie vergelijkingsbeelden origineel ↔ AV1.

    Uitgeschakeld (functie 'vmaf' uit) = niets meten en ook niets afkeuren.
    Verbeterpunt 29: mislukt VMAF op alle steekproeven, dan eerst SSIM; lukt ook dat niet,
    dan ``unmeasured: True`` met de redenen. De uitvoerder plaatst zo'n resultaat nooit."""
    feature = database.fetch_one("SELECT ingeschakeld FROM functie WHERE sleutel='vmaf'")
    if feature is None or not int(feature["ingeschakeld"]):
        return {"metric": None, "score": None, "samples": [], "stills": []}
    directory = quality_directory(settings, job_id)
    prefix = measure_prefix(database, job_id)
    has_vmaf = await vmaf_available()
    points = sample_points(duration)
    samples: list[float] = []
    problems: list[str] = []
    stills: list[dict[str, Any]] = []
    for index, start in enumerate(points):
        if on_sample is not None:
            on_sample(index + 1, len(points))  # rapport 7 A4: "Kwaliteit meten 2/3"
        if has_vmaf:
            score, problem = await vmaf_sample(result, source, start, SAMPLE_LENGTH,
                                               prefix=prefix)
        else:
            score, problem = await ssim_sample(result, source, start, SAMPLE_LENGTH,
                                               prefix=prefix)
        if score is not None:
            samples.append(score)
        elif problem:
            problems.append(f"steekproef {index + 1}: {problem}")
        moment = start + SAMPLE_LENGTH / 2
        bron = directory / f"vergelijk-{index + 1}-bron.jpg"
        resultaat = directory / f"vergelijk-{index + 1}-resultaat.jpg"
        ok_bron = await extract_still(source, bron, moment, prefix=prefix)
        ok_resultaat = await extract_still(result, resultaat, moment, prefix=prefix)
        if ok_bron and ok_resultaat:
            stills.append({"nummer": index + 1, "seconde": round(moment, 1),
                           "bron": str(bron), "resultaat": str(resultaat)})
    metric = "vmaf" if has_vmaf else "ssim"
    fallback = None
    if not samples and has_vmaf:
        # VMAF lukte nergens: een andere meting (SSIM) mag wel oordelen, nooit 'geen score'
        for index, start in enumerate(points):
            score, problem = await ssim_sample(result, source, start, SAMPLE_LENGTH,
                                               prefix=prefix)
            if score is not None:
                samples.append(score)
            elif problem:
                problems.append(f"SSIM steekproef {index + 1}: {problem}")
        metric, fallback = "ssim", "VMAF-meting mislukte op alle steekproeven; SSIM gebruikt"
    average = round(sum(samples) / len(samples), 4 if metric == "ssim" else 2) \
        if samples else None
    out: dict[str, Any] = {
        "metric": metric if samples else None, "score": average,
        "minimum": min(samples) if samples else None, "samples": samples, "stills": stills,
        "attempted": len(points), "measured": len(samples), "problems": problems[:10]}
    if fallback and samples:
        out["fallback"] = fallback
    if not samples:
        out["unmeasured"] = True
    return out


def quality_directory(settings: Settings, job_id: int) -> Path:
    return settings.config_root / "kwaliteit" / str(job_id)


async def capture_comparison_frames(
    database: Database,
    settings: Settings,
    job_id: int,
    source: Path,
    result: Path | None,
    duration: float,
) -> dict[str, str | float | None]:
    feature = database.fetch_one("SELECT ingeschakeld FROM functie WHERE sleutel='vmaf'")
    if feature is None or not int(feature["ingeschakeld"]):
        return {"bron": None, "resultaat": None, "vmaf": None}
    # Begrenzing per week en alleen voor jobvergelijkingen: proefdraairijen tellen
    # niet mee, en na 50 jobs stopte de meting anders voorgoed.
    completed = database.fetch_one(
        "SELECT COUNT(*) AS aantal FROM kwaliteitsmeting WHERE soort='vergelijking' "
        "AND datetime(gemaakt_op)>=datetime('now','-7 days')"
    )
    if completed is not None and int(completed["aantal"]) >= 50:
        return {"bron": None, "resultaat": None, "vmaf": None}
    directory = quality_directory(settings, job_id)
    second = max(0.0, duration / 2)
    source_frame = directory / "bron.jpg"
    result_frame = directory / "resultaat.jpg"
    prefix = measure_prefix(database, job_id)
    if not source_frame.exists():
        await extract_frame(source, source_frame, second, prefix=prefix)
    if result is not None:
        await extract_frame(result, result_frame, second, prefix=prefix)
    score = (
        await compute_vmaf(result, source, max(0.0, second - 15.0), min(30.0, duration),
                           prefix=prefix)
        if result is not None
        else None
    )
    return {
        "bron": str(source_frame) if source_frame.is_file() else None,
        "resultaat": str(result_frame) if result_frame.is_file() else None,
        "vmaf": score,
    }


async def run_profile_trial(
    database: Database,
    settings: Settings,
    task_id: str,
    profile_id: int,
    file_id: int,
    crf_values: list[int],
) -> None:
    """Encodeer korte tijdelijke fragmenten; de catalogusbron blijft alleen-lezen."""

    row = database.fetch_one("SELECT pad,probe FROM bestand WHERE id=?", (file_id,))
    if row is None:
        raise ValueError("Bestand ontbreekt uit de catalogus")
    source = Path(str(row["pad"]))
    probe_data = json.loads(str(row["probe"] or "{}"))
    duration = float(probe_data.get("duration") or 0.0)
    if not source.is_file() or duration <= 0:
        raise ValueError("Bron of speelduur is niet beschikbaar voor de proefdraai")
    sample_duration = min(120.0, duration)
    start_second = max(0.0, (duration - sample_duration) / 2)
    directory = settings.work_root / "proefdraaien" / task_id
    directory.mkdir(parents=True, exist_ok=False)
    has_vmaf = await vmaf_available()
    # zelfde CPU-grens en lage voorrang als een echte encode (verbeterpunt 29)
    prefix = measure_prefix(database)
    try:
        for crf in crf_values:
            partial = directory / f"crf-{crf}.deel.mkv"
            result = directory / f"crf-{crf}.mkv"
            started = time.monotonic()
            process = await asyncio.create_subprocess_exec(
                *prefix,
                "ffmpeg",
                "-hide_banner",
                "-loglevel",
                "error",
                "-y",
                "-ss",
                str(start_second),
                "-t",
                str(sample_duration),
                "-i",
                str(source),
                "-map",
                "0:v:0",
                "-an",
                "-sn",
                "-dn",
                "-c:v",
                "libsvtav1",
                "-preset",
                "10",
                "-crf",
                str(crf),
                str(partial),
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.STDOUT,
            )
            output, _ = await process.communicate()
            if process.returncode != 0:
                explanation = output.decode("utf-8", errors="replace")[-1000:]
                database.execute(
                    """
                    INSERT INTO kwaliteitsmeting(
                        profiel_id,bestand_id,taak_id,soort,status,crf,toelichting,gemaakt_op
                    ) VALUES (?,?,?,'proefdraai','mislukt',?,?,?)
                    """,
                    (profile_id, file_id, task_id, crf, explanation, datetime.now(UTC).isoformat()),
                )
                continue
            os.replace(partial, result)
            elapsed = max(1, round(time.monotonic() - started))
            score = (
                await compute_vmaf(
                    result, source, start_second, sample_duration, result_start_second=0.0,
                    prefix=prefix,
                )
                if has_vmaf
                else None
            )
            database.execute(
                """
                INSERT INTO kwaliteitsmeting(
                    profiel_id,bestand_id,taak_id,soort,status,start_seconde,
                    duur_seconden,crf,vmaf_score,grootte,duur_encode_seconden,
                    toelichting,gemaakt_op
                ) VALUES (?,?,?,'proefdraai','klaar',?,?,?,?,?,?,?,?)
                """,
                (
                    profile_id,
                    file_id,
                    task_id,
                    start_second,
                    round(sample_duration),
                    crf,
                    score,
                    result.stat().st_size,
                    elapsed,
                    "VMAF gemeten" if score is not None else "libvmaf niet beschikbaar",
                    datetime.now(UTC).isoformat(),
                ),
            )
    finally:
        shutil.rmtree(directory, ignore_errors=True)


def trial_overview(database: Database, profile_id: int) -> list[dict[str, Any]]:
    return [
        dict(row)
        for row in database.fetch_all(
            """
            SELECT k.taak_id,k.status,k.crf,k.vmaf_score,k.grootte,
                   k.duur_encode_seconden,k.toelichting,k.gemaakt_op,f.pad
            FROM kwaliteitsmeting k JOIN bestand f ON f.id=k.bestand_id
            WHERE k.profiel_id=? AND k.soort='proefdraai'
            ORDER BY k.id DESC LIMIT 50
            """,
            (profile_id,),
        )
    ]


def verification_overview(database: Database) -> list[dict[str, Any]]:
    return [
        dict(row)
        for row in database.fetch_all(
            """
            SELECT j.id AS job_id,j.status,f.pad,v.hdr_bron,v.hdr_resultaat,
                   v.dv_bron,v.dv_resultaat,v.md_gelijk,v.audio_bron,v.audio_resultaat,
                   v.attachments_bron,v.attachments_resultaat,v.geslaagd,v.toelichting,
                   k.vmaf_score,k.bron_frame,k.resultaat_frame
            FROM job j JOIN bestand f ON f.id=j.bestand_id
            JOIN verificatie v ON v.job_id=j.id
            LEFT JOIN kwaliteitsmeting k ON k.job_id=j.id
            ORDER BY j.id DESC LIMIT 100
            """
        )
    ]


def dolby_vision_overview(database: Database, limit: int = 500) -> dict[str, Any]:
    """Toon DV-detectie ook voordat er een encoderesultaat bestaat.

    De verificatietabel ontstaat pas na een encodepoging. Alleen die tabel tonen
    wekte daardoor ten onrechte de indruk dat wachtende of veilig overgeslagen
    DV-bronnen niet waren herkend.
    """
    summary = database.fetch_one(
        """
        SELECT COUNT(*) AS totaal,
               SUM(CASE WHEN CAST(json_extract(probe,'$.dv_profile') AS INTEGER)=5
                        THEN 1 ELSE 0 END) AS profiel_5,
               SUM(CASE WHEN CAST(json_extract(probe,'$.dv_profile') AS INTEGER)=7
                        THEN 1 ELSE 0 END) AS profiel_7,
               SUM(CASE WHEN CAST(json_extract(probe,'$.dv_profile') AS INTEGER)=8
                        THEN 1 ELSE 0 END) AS profiel_8,
               SUM(CASE WHEN CAST(json_extract(probe,'$.dv_profile') AS INTEGER)
                                  NOT IN (5,7,8)
                        THEN 1 ELSE 0 END) AS overige_profielen
        FROM bestand
        WHERE COALESCE(CAST(json_extract(probe,'$.dv_profile') AS INTEGER),0)>0
        """
    )
    rows = database.fetch_all(
        """
        WITH laatste_besluit AS (
            SELECT b.*,
                   ROW_NUMBER() OVER (PARTITION BY b.bestand_id ORDER BY b.id DESC) AS rang
            FROM besluit b
        ), relevante_job AS (
            SELECT j.*,
                   ROW_NUMBER() OVER (
                       PARTITION BY j.bestand_id
                       ORDER BY CASE
                           WHEN j.status IN ('wacht','gereserveerd','bezig','verifieren') THEN 0
                           WHEN j.status='klaar' THEN 1
                           ELSE 2
                       END, j.id DESC
                   ) AS rang
            FROM job j
        )
        SELECT f.id AS bestand_id,f.pad,
               CAST(json_extract(f.probe,'$.dv_profile') AS INTEGER) AS dv_profile,
               COALESCE(CAST(json_extract(f.probe,'$.dv_compat') AS INTEGER),0) AS dv_compat,
               COALESCE(CAST(json_extract(f.probe,'$.is_hdr10') AS INTEGER),0) AS is_hdr10,
               b.id AS besluit_id,b.actie AS besluit_actie,b.status AS besluit_status,b.reden,
               j.id AS job_id,j.status AS job_status,
               v.dv_bron,v.dv_resultaat,v.geslaagd AS verificatie_geslaagd,
               v.toelichting AS verificatie_toelichting
        FROM bestand f
        LEFT JOIN laatste_besluit b ON b.bestand_id=f.id AND b.rang=1
        LEFT JOIN relevante_job j ON j.bestand_id=f.id AND j.rang=1
        LEFT JOIN verificatie v ON v.job_id=j.id
        WHERE COALESCE(CAST(json_extract(f.probe,'$.dv_profile') AS INTEGER),0)>0
        ORDER BY f.pad
        LIMIT ?
        """,
        (limit,),
    )
    items: list[dict[str, Any]] = []
    for row in rows:
        item = dict(row)
        job_status = str(item["job_status"] or "")
        if item["verificatie_geslaagd"] is not None:
            item["controle_status"] = (
                "geverifieerd" if bool(item["verificatie_geslaagd"]) else "afgekeurd"
            )
        elif job_status in {"wacht", "gereserveerd", "bezig", "verifieren"}:
            item["controle_status"] = "wacht_op_resultaat"
        elif item["besluit_actie"] == "overslaan":
            item["controle_status"] = "veilig_overgeslagen"
        else:
            item["controle_status"] = "niet_ingepland"
        items.append(item)

    counts = dict(summary) if summary is not None else {}
    total = int(counts.get("totaal") or 0)
    return {
        "totaal": total,
        "profielen": {
            "5": int(counts.get("profiel_5") or 0),
            "7": int(counts.get("profiel_7") or 0),
            "8": int(counts.get("profiel_8") or 0),
            "overig": int(counts.get("overige_profielen") or 0),
        },
        "items": items,
        "afgekapt": total > len(items),
        "uitleg": (
            "Bronprofiel komt uit de catalogusscan; resultaatprofiel verschijnt pas na "
            "een volledige encode en verplichte verificatie."
        ),
    }
