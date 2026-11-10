"""Per-title smart encoding (P4) als uitbreiding op de vaste, veilige CRF-basis.

Werkwijze (alles gemeten, niets aangenomen):

1. Sampleselectie: positie (begin/midden/eind, met marge voor intro/credits)
   plus de sterkste scènewissel; vensters die (bijna) zwart zijn schuiven op.
   De selectie wordt gecontroleerd: niet uitsluitend zwart/intro.
2. Per sample een frame-exacte, verliesvrije referentie (FFV1) in de werkmap.
3. Proefmatrix: korte encodes met de BESTAANDE commandobouwers
   (commandos.encode_cpu / encode_gpu) per CRF/CQ, preset en film-grain.
4. Meting: grootte, bitrate, encodetijd en VMAF (alleen als het echte libvmaf-
   filter bestaat) of anders SSIM + PSNR. Bij HDR is een SDR-metriek op een
   PQ-signaal slechts indicatief; dat staat expliciet in het resultaat.
5. Keuze: goedkoopste instelling die op ÁLLE samples de kwaliteitsvloer haalt
   én de minimale besparing; anders "origineel behouden".

Resultaten gaan naar trial_run / encoding_trials / quality_measurements met
tool-, encoder- en regelversies (replay, F2/P4.8). Het model stelt alleen
instellingen voor; de veiligheidsgates in beslissen/verificatie blijven gelden.
"""

from __future__ import annotations

import json
import re
import subprocess
import tempfile
import time
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from pydantic import BaseModel, ConfigDict, Field

from app.config import Settings
from app.db import Database
from app.media import commandos
from app.media.beslissen import STANDAARD, Besluit, klasse_van

RULE_VERSION = "p4-trials-1"
SAMPLE_SECONDS = 8.0


@dataclass(frozen=True, slots=True)
class QualityProfile:
    name: str
    crf_offsets: tuple[int, ...]
    preset: int
    vmaf_floor: float
    ssim_floor: float
    min_saving_percent: float
    max_encode_seconds_per_sample: float


# Startwaarden, geen bewezen optimum: ze worden per titel gemeten. De SSIM-
# vloeren zijn bewust strenger dan een losse VMAF-vertaling zou suggereren,
# omdat SSIM grain/banding slechter ziet (P4.4: geen absolute garantie).
QUALITY_PROFILES: dict[str, QualityProfile] = {
    "archiefkwaliteit": QualityProfile("archiefkwaliteit", (-6, -4, -2), 4, 95.0, 0.990, 10.0, 900),
    "gebalanceerd": QualityProfile("gebalanceerd", (-2, 0, 3), 6, 93.0, 0.980, 20.0, 600),
    "compact": QualityProfile("compact", (0, 4, 8), 8, 90.0, 0.970, 35.0, 300),
}


class TrialSample(BaseModel):
    model_config = ConfigDict(extra="forbid")
    start: float = Field(ge=0)
    duration: float = Field(gt=0, le=60)


class TrialSetting(BaseModel):
    model_config = ConfigDict(extra="forbid")
    encoder: str = Field(default="libsvtav1", pattern="^(libsvtav1|av1_qsv)$")
    crf: int | None = Field(default=None, ge=1, le=63)
    cq: int | None = Field(default=None, ge=1, le=51)
    preset: int | None = Field(default=None, ge=0, le=13)
    film_grain: int = Field(default=0, ge=0, le=50)


class CustomProfile(BaseModel):
    model_config = ConfigDict(extra="forbid")
    vmaf_floor: float = Field(default=93.0, ge=0, le=100)
    ssim_floor: float = Field(default=0.98, ge=0, le=1)
    min_saving_percent: float = Field(default=20.0, ge=0, le=95)
    preset: int = Field(default=6, ge=0, le=13)
    crf_offsets: list[int] = Field(default_factory=lambda: [-2, 0, 3])


def _run(arguments: list[str], timeout: float,
         prefix: list[str] | None = None) -> tuple[int, str, str]:
    """``prefix``: CPU-grens en lage voorrang (taskset/nice/ionice), zoals een echte encode."""
    try:
        result = subprocess.run([*(prefix or []), *arguments], capture_output=True, text=True,
                                timeout=timeout)
    except subprocess.TimeoutExpired:
        return 124, "", "timeout"
    except OSError as exc:
        return 127, "", str(exc)
    return result.returncode, result.stdout, result.stderr


def tool_versions() -> dict[str, Any]:
    _code, out, _err = _run(["ffmpeg", "-version"], 15)
    match = re.search(r"ffmpeg version\s+(\S+)", out)
    _code, _out, err = _run(
        [
            "ffmpeg",
            "-hide_banner",
            "-loglevel",
            "info",
            "-f",
            "lavfi",
            "-i",
            "color=size=64x64:duration=0.1",
            "-c:v",
            "libsvtav1",
            "-f",
            "null",
            "-",
        ],
        30,
    )
    svt = re.search(r"SVT-AV1 Encoder Lib v?(\S+)", err)
    _code, filters, _err = _run(["ffmpeg", "-hide_banner", "-filters"], 15)
    return {
        "ffmpeg": match.group(1) if match else None,
        "svt_av1": svt.group(1) if svt else None,
        "libvmaf": re.search(r"\blibvmaf\b", filters) is not None,
        "rule_version": RULE_VERSION,
    }


# ── 1. Sampleselectie ─────────────────────────────────────────────────────


def frame_stats(path: str, duration: float) -> list[tuple[float, float, float]]:
    """(tijd, gemiddelde luma 0-255, scènescore) per (key)frame op lage resolutie."""

    arguments = ["ffmpeg", "-hide_banner", "-loglevel", "info", "-nostats"]
    if duration > 120:
        # Lange titels: alleen keyframes decoderen houdt dit bij seconden.
        arguments += ["-skip_frame", "nokey"]
    arguments += [
        "-i",
        path,
        "-map",
        "0:v:0",
        "-an",
        "-sn",
        "-vf",
        "scale=96:-2,format=yuv420p,signalstats,select='gte(scene\\,0)',metadata=print",
        "-f",
        "null",
        "-",
    ]
    _code, _out, err = _run(arguments, max(120.0, duration / 4))
    stats: list[tuple[float, float, float]] = []
    current_time: float | None = None
    yavg: float | None = None
    scene = 0.0
    for line in err.splitlines():
        time_match = re.search(r"pts_time:([0-9.]+)", line)
        if time_match:
            if current_time is not None and yavg is not None:
                stats.append((current_time, yavg, scene))
            current_time, yavg, scene = float(time_match.group(1)), None, 0.0
            continue
        value = re.search(r"lavfi\.signalstats\.YAVG=([0-9.]+)", line)
        if value:
            yavg = float(value.group(1))
        value = re.search(r"lavfi\.scene_score=([0-9.]+)", line)
        if value:
            scene = float(value.group(1))
    if current_time is not None and yavg is not None:
        stats.append((current_time, yavg, scene))
    return stats


BLACK_LUMA = 20.0  # 8-bit gemiddelde; zwart/fade-beeld ligt daaronder


def select_samples(
    path: str, duration: float, *, count: int = 3, sample_seconds: float = SAMPLE_SECONDS
) -> tuple[list[TrialSample], dict[str, Any]]:
    """Begin/midden/eind + sterkste scènewissel, zonder zwarte of intro-only vensters."""

    length = min(sample_seconds, max(1.0, duration / (count + 1)))
    latest = max(0.0, duration - length)
    stats = frame_stats(path, duration)

    def mean_luma(start: float) -> float | None:
        window = [y for t, y, _s in stats if start <= t < start + length]
        return sum(window) / len(window) if window else None

    candidates = [duration * fraction for fraction in (0.15, 0.5, 0.8)][:count]
    inner = [(t, s) for t, _y, s in stats if duration * 0.05 <= t <= duration * 0.9]
    scene_pick = max(inner, key=lambda item: item[1]) if inner else None
    if scene_pick is not None and scene_pick[1] > 0.2:
        candidates.append(scene_pick[0])
    chosen: list[float] = []
    for candidate in candidates:
        start = min(max(0.0, candidate), latest)
        luma = mean_luma(start)
        if luma is not None and luma < BLACK_LUMA:
            brighter = [t for t, y, _s in stats if t > start and y >= BLACK_LUMA and t <= latest]
            if brighter:
                start = brighter[0]
        if all(abs(start - other) >= length for other in chosen):
            chosen.append(round(start, 3))
    if not chosen:
        chosen = [round(min(duration * 0.5, latest), 3)]
    lumas = [mean_luma(start) for start in chosen]
    checks = {
        "frames_analysed": len(stats),
        "not_only_black": any(value is None or value >= BLACK_LUMA for value in lumas),
        "not_only_intro": any(start >= duration * 0.1 for start in chosen) or duration < 60,
        "scene_change_sample": scene_pick is not None and scene_pick[1] > 0.2,
        "mean_luma": lumas,
    }
    return [TrialSample(start=start, duration=length) for start in sorted(chosen)], checks


# ── 2-4. Referentie, encode, meting ───────────────────────────────────────


def _source_sample_bytes(path: str, start: float, duration: float) -> int:
    """Bronbytes van precies het samplevenster (videospoor).

    ``-read_intervals`` zoekt naar het keyframe vóór ``start`` en leest tot na het
    einde; zonder filter op pts_time telt de bron soms een hele GOP te veel en
    lijkt de besparing groter dan ze is.
    """

    _code, out, _err = _run(
        [
            "ffprobe",
            "-v",
            "quiet",
            "-select_streams",
            "v:0",
            "-read_intervals",
            f"{start}%+{duration}",
            "-show_entries",
            "packet=pts_time,size",
            "-of",
            "compact=p=0:nk=0",
            path,
        ],
        120,
    )
    return _sum_window_packets(out, start, start + duration)


def _sum_window_packets(output: str, start: float, end: float) -> int:
    total = 0
    for line in output.splitlines():
        fields = dict(part.split("=", 1) for part in line.strip().split("|") if "=" in part)
        try:
            pts = float(fields.get("pts_time", ""))
            size = int(fields.get("size", ""))
        except ValueError:
            continue
        if start - 1e-6 <= pts < end - 1e-6:
            total += size
    return total


def _reference(path: str, sample: TrialSample, target: Path,
               prefix: list[str] | None = None) -> bool:
    code, _out, _err = _run(
        [
            "ffmpeg",
            "-hide_banner",
            "-loglevel",
            "error",
            "-y",
            "-ss",
            str(sample.start),
            "-i",
            path,
            "-t",
            str(sample.duration),
            "-map",
            "0:v:0",
            "-an",
            "-sn",
            "-dn",
            "-c:v",
            "ffv1",
            "-level",
            "3",
            str(target),
        ],
        600,
        prefix,
    )
    return code == 0 and target.is_file() and target.stat().st_size > 0


def measure_quality(distorted: Path, reference: Path, *, vmaf: bool,
                    prefix: list[str] | None = None) -> dict[str, float]:
    """SSIM + PSNR altijd; VMAF alleen met echt libvmaf. Nooit een verzonnen score."""

    graph = (
        "[0:v]format=yuv420p10le,setpts=PTS-STARTPTS,split=3[d1][d2][d3];"
        "[1:v]format=yuv420p10le,setpts=PTS-STARTPTS,split=3[r1][r2][r3];"
        "[d1][r1]ssim;[d2][r2]psnr;"
    )
    graph += "[d3][r3]libvmaf=n_threads=4" if vmaf else "[d3]nullsink;[r3]nullsink"
    _code, _out, err = _run(
        [
            "ffmpeg",
            "-hide_banner",
            "-nostats",
            "-i",
            str(distorted),
            "-i",
            str(reference),
            "-lavfi",
            graph,
            "-f",
            "null",
            "-",
        ],
        900,
        prefix,
    )
    result: dict[str, float] = {}
    ssim = re.search(r"SSIM .*All:([0-9.]+)", err)
    if ssim:
        result["ssim"] = float(ssim.group(1))
    psnr = re.search(r"PSNR .*average:([0-9.]+|inf)", err)
    if psnr:
        result["psnr"] = 100.0 if psnr.group(1) == "inf" else float(psnr.group(1))
    score = re.search(r"VMAF score:\s*([0-9.]+)", err)
    if score and vmaf:
        result["vmaf"] = float(score.group(1))
    return result


def _encode_command(
    info: dict[str, Any], setting: TrialSetting, reference: Path, target: Path, render_node: str
) -> list[str]:
    klasse = klasse_van(int(info["height"]))
    inst = {**STANDAARD, f"doel_bitrate_{klasse}_kbps": 0, "film_grain": setting.film_grain}
    if setting.crf is not None:
        inst[f"crf_{klasse}"] = setting.crf
    if setting.preset is not None:
        inst[f"preset_{klasse}"] = setting.preset
    trial_info = {**info, "subs": []}
    if setting.encoder == "av1_qsv":
        inst["gpu_quality"] = setting.cq or setting.crf or inst["gpu_quality"]
        decision = Besluit(actie="hercoderen", baan="gpu", encoder="av1_qsv")
        return commandos.encode_gpu(
            trial_info, decision, inst, str(reference), str(target), render_node
        )
    # De RPU telt niet mee in grootte/kwaliteit; de volledige job verifieert
    # DV apart. FFV1-referenties dragen geen RPU, dus dolby_vision=False.
    decision = Besluit(
        actie="hercoderen", baan="cpu", encoder="libsvtav1", behoud_hdr=bool(info["is_hdr10"])
    )
    return commandos.encode_cpu(trial_info, decision, inst, str(reference), str(target))


def default_matrix(info: dict[str, Any], profile: QualityProfile, gpu: bool) -> list[TrialSetting]:
    base = int(STANDAARD["crf_" + klasse_van(int(info["height"]))])
    settings = [
        TrialSetting(encoder="libsvtav1", crf=min(63, max(1, base + offset)), preset=profile.preset)
        for offset in profile.crf_offsets
    ]
    if gpu and not info.get("is_hdr10") and not info.get("dv_profile"):
        # av1_qsv draagt geen HDR-metadata (spec/02 #3): alleen SDR-proeven op de GPU.
        settings += [
            TrialSetting(encoder="av1_qsv", cq=int(STANDAARD["gpu_quality"]) + offset)
            for offset in (-2, 0, 3)
        ]
    return settings


def run_trials(
    database: Database,
    settings: Settings,
    *,
    path: Path,
    info: dict[str, Any],
    profile_name: str,
    custom: CustomProfile | None,
    samples: list[TrialSample] | None,
    trial_settings: list[TrialSetting] | None,
    dv_mode: str | None,
    gpu_available: bool,
    vmaf_available: bool,
) -> dict[str, Any]:
    if profile_name == "custom":
        custom = custom or CustomProfile()
        profile = QualityProfile(
            "custom",
            tuple(custom.crf_offsets),
            custom.preset,
            custom.vmaf_floor,
            custom.ssim_floor,
            custom.min_saving_percent,
            900,
        )
    else:
        profile = QUALITY_PROFILES[profile_name]
    duration = float(info["duration"])
    selection_checks: dict[str, Any] = {"requested": True}
    if not samples:
        samples, selection_checks = select_samples(str(path), duration)
    matrix = trial_settings or default_matrix(info, profile, gpu_available)
    matrix = [item for item in matrix if item.encoder != "av1_qsv" or gpu_available]
    versions = tool_versions()
    now = datetime.now(UTC).isoformat()
    stat = path.stat()
    run_id = database.execute_insert(
        """
        INSERT INTO trial_run(bron_pad,bron_grootte,bron_mtime,kwaliteitsprofiel,dv_mode,status,
                              samples,tool_versies,regel_versie,gemaakt_op)
        VALUES (?,?,?,?,?,'bezig',?,?,?,?)
        """,
        (
            str(path),
            stat.st_size,
            stat.st_mtime,
            profile.name,
            dv_mode,
            json.dumps([s.model_dump() for s in samples]),
            json.dumps(versions),
            RULE_VERSION,
            now,
        ),
    )
    settings.work_root.mkdir(parents=True, exist_ok=True)
    results: dict[int, dict[str, Any]] = {}
    from app.kwaliteit import measure_prefix

    # proefencodes en metingen op de encodekernen, met lage voorrang (verbeterpunt 29)
    limits = measure_prefix(database)
    try:
        with tempfile.TemporaryDirectory(prefix=f"trial-{run_id}-", dir=settings.work_root) as tmp:
            workdir = Path(tmp)
            for sample_index, sample in enumerate(samples):
                reference = workdir / f"ref-{sample_index}.mkv"
                if not _reference(str(path), sample, reference, limits):
                    raise RuntimeError(f"referentie voor sample {sample_index} mislukt")
                source_bytes = _source_sample_bytes(str(path), sample.start, sample.duration)
                for setting_index, setting in enumerate(matrix):
                    target = workdir / f"trial-{sample_index}-{setting_index}.mkv"
                    command = _encode_command(
                        info, setting, reference, target, str(settings.render_node)
                    )
                    command = [*command[:-1], "-f", "matroska", command[-1]]
                    started = time.monotonic()
                    code, _out, err = _run(command, profile.max_encode_seconds_per_sample,
                                           limits)
                    elapsed = time.monotonic() - started
                    ok = code == 0 and target.is_file()
                    size = target.stat().st_size if ok else None
                    trial_id = database.execute_insert(
                        """
                        INSERT INTO encoding_trials(run_id,sample_index,sample_start,sample_duur,
                            encoder,crf,preset,film_grain,bron_bytes,grootte,bitrate_kbps,
                            encode_seconden,status,fout,gemaakt_op)
                        VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)
                        """,
                        (
                            run_id,
                            sample_index,
                            sample.start,
                            sample.duration,
                            setting.encoder,
                            setting.crf if setting.encoder == "libsvtav1" else setting.cq,
                            setting.preset,
                            setting.film_grain,
                            source_bytes,
                            size,
                            round(size * 8 / sample.duration / 1000, 1) if size else None,
                            round(elapsed, 3),
                            "klaar" if ok else "mislukt",
                            None if ok else err[-500:],
                            datetime.now(UTC).isoformat(),
                        ),
                    )
                    metrics = (measure_quality(target, reference, vmaf=vmaf_available,
                                               prefix=limits) if ok else {})
                    for metric, value in metrics.items():
                        database.execute_insert(
                            """
                            INSERT INTO quality_measurements(trial_id,metriek,waarde,meetmethode,
                                                             tool_versie,gemaakt_op)
                            VALUES (?,?,?,?,?,?)
                            """,
                            (
                                trial_id,
                                metric,
                                value,
                                "ffmpeg-filter tegen FFV1-referentie, yuv420p10le",
                                versions["ffmpeg"],
                                datetime.now(UTC).isoformat(),
                            ),
                        )
                    entry = results.setdefault(
                        setting_index,
                        {"setting": setting.model_dump(), "samples": [], "failed": False},
                    )
                    entry["samples"].append(
                        {
                            "trial_id": trial_id,
                            "sample_index": sample_index,
                            "source_bytes": source_bytes,
                            "size": size,
                            "bitrate_kbps": round(size * 8 / sample.duration / 1000, 1)
                            if size
                            else None,
                            "encode_seconds": round(elapsed, 3),
                            "metrics": metrics,
                        }
                    )
                    entry["failed"] = entry["failed"] or not ok
                    target.unlink(missing_ok=True)
                reference.unlink(missing_ok=True)
        recommendation = recommend(list(results.values()), profile, info, vmaf_available)
        database.execute(
            "UPDATE trial_run SET status='klaar',aanbeveling=?,klaar_op=? WHERE id=?",
            (json.dumps(recommendation, ensure_ascii=False), datetime.now(UTC).isoformat(), run_id),
        )
    except Exception as exc:
        database.execute(
            "UPDATE trial_run SET status='mislukt',fout=?,klaar_op=? WHERE id=?",
            (str(exc)[-1000:], datetime.now(UTC).isoformat(), run_id),
        )
        raise
    return trial_view(database, run_id) | {"selection_checks": selection_checks}


def recommend(
    results: list[dict[str, Any]],
    profile: QualityProfile,
    info: dict[str, Any],
    vmaf_available: bool,
) -> dict[str, Any]:
    """Goedkoopste instelling die op ieder sample vloer én besparing haalt."""

    hdr = bool(info.get("is_hdr10") or info.get("dv_profile"))
    metric = "vmaf" if vmaf_available else "ssim"
    floor = profile.vmaf_floor if vmaf_available else profile.ssim_floor
    evaluated = []
    for item in results:
        samples = item["samples"]
        if item["failed"] or not samples:
            continue
        values = [s["metrics"].get(metric) for s in samples]
        if any(value is None for value in values):
            continue
        source_total = sum(int(s["source_bytes"] or 0) for s in samples)
        trial_total = sum(int(s["size"] or 0) for s in samples)
        saving = 100.0 * (1 - trial_total / source_total) if source_total else None
        worst = min(float(v) for v in values if v is not None)
        evaluated.append(
            {
                "setting": item["setting"],
                "worst_quality": round(worst, 5),
                "saving_percent": round(saving, 2) if saving is not None else None,
                "encode_seconds": round(sum(s["encode_seconds"] for s in samples), 2),
                "meets_floor": worst >= floor,
                "meets_saving": saving is not None and saving >= profile.min_saving_percent,
            }
        )
    passing = [e for e in evaluated if e["meets_floor"] and e["meets_saving"]]
    base = {
        "profile": profile.name,
        "metric": metric,
        "quality_floor": floor,
        "min_saving_percent": profile.min_saving_percent,
        "evaluated": evaluated,
        "hdr_metric_caveat": (
            "SDR-metriek op een PQ/HLG-signaal is indicatief; geen HDR-garantie" if hdr else None
        ),
    }
    if not evaluated:
        return base | {"action": "keep_original", "reason": "kwaliteit onzeker: geen meting"}
    if not passing:
        if not any(e["meets_floor"] for e in evaluated):
            reason = f"geen instelling haalt de kwaliteitsvloer ({metric} >= {floor})"
        else:
            reason = f"besparing te klein (< {profile.min_saving_percent}%) bij voldoende kwaliteit"
        return base | {"action": "keep_original", "reason": reason}
    best = max(passing, key=lambda e: (e["saving_percent"], -e["encode_seconds"]))
    return base | {
        "action": "encode",
        "reason": (
            f"{best['setting']['encoder']} haalt {metric} {best['worst_quality']} "
            f">= {floor} met {best['saving_percent']}% besparing"
        ),
        "recommended": best["setting"],
    }


def trial_view(database: Database, run_id: int) -> dict[str, Any]:
    run = database.fetch_one("SELECT * FROM trial_run WHERE id=?", (run_id,))
    if run is None:
        raise KeyError(run_id)
    trials = database.fetch_all(
        "SELECT * FROM encoding_trials WHERE run_id=? ORDER BY sample_index,id", (run_id,)
    )
    measurements: dict[int, dict[str, float]] = {}
    for row in database.fetch_all(
        "SELECT q.trial_id,q.metriek,q.waarde FROM quality_measurements q "
        "JOIN encoding_trials t ON t.id=q.trial_id WHERE t.run_id=?",
        (run_id,),
    ):
        measurements.setdefault(int(row["trial_id"]), {})[str(row["metriek"])] = float(
            row["waarde"]
        )
    return {
        "trial_run_id": int(run["id"]),
        "status": {"bezig": "running", "klaar": "done", "mislukt": "failed"}[str(run["status"])],
        "path": run["bron_pad"],
        "quality_profile": run["kwaliteitsprofiel"],
        "samples": json.loads(str(run["samples"])),
        "tool_versions": json.loads(str(run["tool_versies"])),
        "rule_version": run["regel_versie"],
        "error": run["fout"],
        "recommendation": json.loads(str(run["aanbeveling"])) if run["aanbeveling"] else None,
        "trials": [
            {
                "trial_id": int(row["id"]),
                "sample_index": int(row["sample_index"]),
                "sample_start": row["sample_start"],
                "sample_duration": row["sample_duur"],
                "encoder": row["encoder"],
                "crf_or_cq": row["crf"],
                "preset": row["preset"],
                "film_grain": row["film_grain"],
                "source_bytes": row["bron_bytes"],
                "size": row["grootte"],
                "bitrate_kbps": row["bitrate_kbps"],
                "encode_seconds": row["encode_seconden"],
                "status": row["status"],
                "error": row["fout"],
                "metrics": measurements.get(int(row["id"]), {}),
            }
            for row in trials
        ],
    }
