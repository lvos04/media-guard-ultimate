"""Niet-kritieke hostmetingen en onschadelijke QSV-diagnostiek."""

from __future__ import annotations

import asyncio
import json
import math
import os
import shutil
import tempfile
import time
from contextlib import suppress
from pathlib import Path
from typing import Any

import psutil

from app.config import ConfigError, Settings
from app.db import Database
from app.hostlimits import cpu_temperature, effective_memory
from app.opslag import storage_validation_error


def _temperature() -> float | None:
    return cpu_temperature()


def mount_report(settings: Settings) -> dict[str, Any]:
    """Toon mount-identiteit expliciet; alleen een bestaande map is niet genoeg."""

    def details(path: Path) -> dict[str, Any]:
        exists = path.exists()
        try:
            stat = path.stat() if exists else None
            usage = shutil.disk_usage(path) if exists else None
        except OSError:
            stat = None
            usage = None
        return {
            "pad": str(path),
            "bestaat": exists,
            "mountpoint": path.is_mount() if exists else False,
            "device": stat.st_dev if stat is not None else None,
            "vrij_bytes": usage.free if usage is not None else None,
            "totaal_bytes": usage.total if usage is not None else None,
            "schrijfbaar": exists and os.access(path, os.W_OK | os.X_OK),
        }

    pipeline = details(settings.pipeline_root)
    workspace = details(settings.workspace_root)
    pipeline["schrijfbaar"] = all(
        path.is_dir() and os.access(path, os.W_OK | os.X_OK)
        for path in (settings.config_root, settings.backup_root)
        if path.is_relative_to(settings.pipeline_root)
    )
    workspace["schrijfbaar"] = all(
        path.is_dir() and os.access(path, os.W_OK | os.X_OK)
        for path in (settings.cache_root, settings.work_root)
    )
    workspace["modus"] = settings.storage_mode
    workspace["systeemschijf_reserve_gb"] = settings.system_disk_reserve_gb
    report = {
        "pipeline_opslag": pipeline,
        "werkruimte": workspace,
        "library_root": details(settings.library_root),
        "cache": details(settings.cache_root),
    }
    if settings.container_mode:
        # In de container zijn de doorgegeven mediamounts de bibliotheekbron;
        # LIBRARY_ROOT is alleen de native standaard.
        roots = [details(root) for root in settings.media_roots]
        report["media_roots"] = roots  # type: ignore[assignment]
        if roots:
            report["library_root"] = roots[0]
        report["staging"] = details(settings.effective_staging_root)
    return report


class GpuMonitor:
    """Optionele intel_gpu_top-lezer; een meetfout raakt de encoder nooit."""

    def __init__(self) -> None:
        self.latest: dict[str, Any] = {
            "video_percent": None,
            "render_percent": None,
            "freq_mhz": None,
            "power_w": None,
            "package_power_w": None,
            "platform_power_w": None,
            "platform_power_beschikbaar": False,
            "beschikbaar": False,
        }
        self.process: asyncio.subprocess.Process | None = None
        self.task: asyncio.Task[None] | None = None
        self.power_process: asyncio.subprocess.Process | None = None
        self.power_task: asyncio.Task[None] | None = None

    async def start(self) -> None:
        if (self.task is None or self.task.done()) and shutil.which("intel_gpu_top"):
            self.task = asyncio.create_task(self._run())
        elif shutil.which("intel_gpu_top") is None and (self.task is None or self.task.done()):
            # Zonder intel_gpu_top (en zonder perf-rechten): de kernel telt per proces hoe
            # lang de video-engine bezig was (/proc/<pid>/fdinfo, drm-engine-video).
            self.latest["fout"] = ("intel_gpu_top is niet geïnstalleerd; GPU-bezetting wordt "
                                   "gemeten via de kernel (eerste meting volgt)")
            self.task = asyncio.create_task(self._run_fdinfo())
        if (self.power_task is None or self.power_task.done()) and shutil.which("turbostat"):
            self.power_task = asyncio.create_task(self._run_platform_power())
        elif shutil.which("turbostat") is None:
            self.latest["platform_power_fout"] = "turbostat is niet geïnstalleerd"

    async def _run(self) -> None:
        try:
            self.process = await asyncio.create_subprocess_exec(
                "intel_gpu_top",
                "-J",
                "-s",
                "1000",
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
            )
            if self.process.stdout is None:
                return
            buffer = ""
            while True:
                chunk = await self.process.stdout.read(4096)
                if not chunk:
                    break
                buffer += chunk.decode("utf-8", errors="replace")
                objects, buffer = _json_objects(buffer)
                for value in objects:
                    self.latest.update(_gpu_values(value))
                    self.latest.pop("fout", None)
            stderr = await self.process.stderr.read() if self.process.stderr else b""
            if self.process.returncode is None:
                await self.process.wait()
            if not self.latest["beschikbaar"]:
                message = stderr.decode("utf-8", errors="replace").strip()
                self.latest["fout"] = message[-500:] or "GPU-monitor gaf geen meting"
        except (FileNotFoundError, OSError, ValueError) as exc:
            self.latest.update({
                "video_percent": None,
                "render_percent": None,
                "freq_mhz": None,
                "power_w": None,
                "package_power_w": None,
                "beschikbaar": False,
                "fout": str(exc),
            })
        finally:
            self.process = None

    # gedeelde Settings: capabilities zet daar de gekozen kaart (Arc i.p.v. iGPU) in
    settings: Any = None

    async def _run_fdinfo(self, interval: float = 2.0) -> None:
        previous: tuple[float, dict[str, int]] | None = None
        while True:
            node = (Path(self.settings.render_node) if self.settings is not None else
                    Path(os.environ.get("MG_RENDER_NODE", "") or "/dev/dri/renderD128"))
            pdev = render_pci_address(node)
            if pdev is None:
                self.latest.update({"video_percent": None, "beschikbaar": False,
                                    "fout": "geen GPU gevonden"})
                previous = None
            else:
                busy, capacity = await asyncio.to_thread(fdinfo_video_busy, pdev)
                now = time.monotonic()
                if previous is not None:
                    elapsed_ns = (now - previous[0]) * 1e9
                    delta = sum(max(0, v - previous[1].get(k, v)) for k, v in busy.items())
                    percent = 100.0 * delta / (elapsed_ns * max(1, capacity))
                    self.latest.update({"video_percent": round(min(100.0, percent), 1),
                                        "beschikbaar": True, "bron": "fdinfo"})
                    self.latest.pop("fout", None)
                previous = (now, busy)
            await asyncio.sleep(interval)

    async def _run_platform_power(self) -> None:
        """Lees Intel PSys; dit benadert het systeemtotaal zonder het te verzinnen."""

        try:
            self.power_process = await asyncio.create_subprocess_exec(
                "turbostat",
                "--quiet",
                "--no-msr",
                "--interval",
                "1",
                "--show",
                "SysWatt",
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
            )
            if self.power_process.stdout is None:
                return
            expecting_value = False
            while True:
                raw_line = await self.power_process.stdout.readline()
                if not raw_line:
                    break
                line = raw_line.decode("utf-8", errors="replace").strip()
                if line == "SysWatt":
                    expecting_value = True
                    continue
                if not expecting_value:
                    continue
                expecting_value = False
                value = _platform_power_value(line)
                if value is not None:
                    self.latest["platform_power_w"] = value
                    self.latest["platform_power_beschikbaar"] = True
                    self.latest.pop("platform_power_fout", None)
            stderr = (
                await self.power_process.stderr.read() if self.power_process.stderr else b""
            )
            if self.power_process.returncode is None:
                await self.power_process.wait()
            if not self.latest["platform_power_beschikbaar"]:
                message = stderr.decode("utf-8", errors="replace").strip()
                self.latest["platform_power_fout"] = (
                    message[-500:] or "PSys-monitor gaf geen meting"
                )
        except (FileNotFoundError, OSError, ValueError) as exc:
            self.latest.update(
                {
                    "platform_power_w": None,
                    "platform_power_beschikbaar": False,
                    "platform_power_fout": str(exc),
                }
            )
        finally:
            self.power_process = None

    async def stop(self) -> None:
        for process in (self.process, self.power_process):
            if process is None or process.returncode is not None:
                continue
            process.terminate()
            with suppress(TimeoutError):
                await asyncio.wait_for(process.wait(), timeout=3)
            if process.returncode is None:
                process.kill()
                await process.wait()
        for task in (self.task, self.power_task):
            if task is not None:
                task.cancel()
                with suppress(asyncio.CancelledError):
                    await task
        self.task = None
        self.power_task = None
        self.process = None
        self.power_process = None

    async def restart(self, enabled: bool) -> dict[str, Any]:
        await self.stop()
        self.latest = {
            "video_percent": None,
            "render_percent": None,
            "freq_mhz": None,
            "power_w": None,
            "package_power_w": None,
            "platform_power_w": None,
            "platform_power_beschikbaar": False,
            "beschikbaar": False,
        }
        if enabled:
            await self.start()
        return dict(self.latest)


def _json_objects(buffer: str) -> tuple[list[dict[str, Any]], str]:
    """Haal complete JSON-objecten uit zowel array- als losse-objectuitvoer."""

    decoder = json.JSONDecoder()
    objects: list[dict[str, Any]] = []
    cursor = 0
    while cursor < len(buffer):
        start = buffer.find("{", cursor)
        if start < 0:
            return objects, buffer[-1:]
        try:
            value, end = decoder.raw_decode(buffer[start:])
        except json.JSONDecodeError:
            return objects, buffer[start:]
        if isinstance(value, dict):
            objects.append(value)
        cursor = start + end
    return objects, ""


def _number(value: Any) -> float | None:
    if isinstance(value, (int, float)):
        return float(value)
    if isinstance(value, dict):
        for key in ("busy", "value", "actual", "GPU"):
            if key in value:
                found = _number(value[key])
                if found is not None:
                    return found
    return None


def _platform_power_value(line: str) -> float | None:
    """Accepteer uitsluitend een eindige, aannemelijke SysWatt-meting."""

    try:
        value = float(line.split()[0])
    except (IndexError, ValueError):
        return None
    return value if math.isfinite(value) and 0 <= value <= 10_000 else None


def _gpu_values(payload: dict[str, Any]) -> dict[str, Any]:
    raw_engines = payload.get("engines")
    engines: dict[str, Any] = raw_engines if isinstance(raw_engines, dict) else {}
    video_values = [
        _number(value) for name, value in engines.items() if str(name).lower().startswith("video")
    ]
    render_values = [
        _number(value) for name, value in engines.items() if "render" in str(name).lower()
    ]
    frequency = payload.get("frequency", {})
    raw_power = payload.get("power", {})
    power: dict[str, Any] = raw_power if isinstance(raw_power, dict) else {}
    video = max((value for value in video_values if value is not None), default=None)
    render = max((value for value in render_values if value is not None), default=None)
    return {
        "video_percent": video,
        "render_percent": render,
        "freq_mhz": _number(frequency),
        "power_w": _number(power.get("GPU")),
        "package_power_w": _number(power.get("Package")),
        "beschikbaar": video is not None or render is not None,
    }


ARCSTATS = Path("/proc/spl/kstat/zfs/arcstats")


def zfs_arc_bytes(path: Path = ARCSTATS) -> int | None:
    """Huidige grootte van de ZFS-cache (ARC) in bytes, of None als die niet leesbaar is."""
    try:
        for line in path.read_text(encoding="utf-8").splitlines():
            parts = line.split()
            if len(parts) == 3 and parts[0] == "size":
                return int(parts[2])
    except (OSError, ValueError):
        return None
    return None


def system_metrics(
    database: Database, settings: Settings, gpu_monitor: GpuMonitor
) -> dict[str, Any]:
    storage_error = storage_validation_error(settings)
    try:
        usage = shutil.disk_usage(settings.cache_root)
        free = usage.free
    except OSError as exc:
        free = 0
        storage_error = str(exc)
    reserved_row = database.fetch_one(
        "SELECT COALESCE(SUM(cache_reservering),0) AS bytes FROM job "
        "WHERE status IN ('bezig','verifieren')"
    )
    reserved = int(reserved_row["bytes"]) if reserved_row is not None else 0
    limits = database.fetch_one(
        "SELECT cache_waarschuwing_gb,cache_pauze_gb FROM instelling WHERE id=1"
    )
    warning = int(limits["cache_waarschuwing_gb"]) if limits is not None else 250
    pause = int(limits["cache_pauze_gb"]) if limits is not None else 150
    effective_warning, effective_pause = settings.effective_cache_limits(warning, pause)
    host_memory = psutil.virtual_memory()
    memory = effective_memory()
    used_percent = (
        float(host_memory.percent)
        if memory.total == host_memory.total
        else round(100.0 * (memory.total - memory.available) / max(memory.total, 1), 1)
    )
    free_gb = free / 1024**3
    cache_status = (
        "pauzegrens"
        if free_gb < effective_pause
        else "waarschuwing"
        if free_gb < effective_warning
        else "voldoende"
    )
    return {
        "cpu_percent": psutil.cpu_percent(interval=None),
        "cpu_temp": _temperature(),
        "geheugen": {
            "totaal_bytes": int(memory.total),
            "gebruikt_bytes": int(memory.total - memory.available),
            "beschikbaar_bytes": int(memory.available),
            "percent": used_percent,
            # ZFS-cache (ARC) apart: op TrueNAS lijkt het geheugen anders altijd vol
            # terwijl die cache vrijkomt zodra programma's hem nodig hebben (rapport 6).
            "zfs_cache_bytes": zfs_arc_bytes(),
        },
        "gpu": dict(gpu_monitor.latest),
        "schijf_vrij_gb": round(free / 1024**3, 2),
        "schijf_vrij_bytes": free,
        "schijf_gereserveerd_bytes": reserved,
        "schijf_beschikbaar_na_reservering": max(0, free - reserved),
        "cache_status": cache_status,
        "cache_waarschuwing_gb": warning,
        "cache_pauze_gb": pause,
        "effectieve_cache_waarschuwing_gb": effective_warning,
        "effectieve_cache_pauze_gb": effective_pause,
        "opslagmodus": settings.storage_mode,
        "systeemschijf_reserve_gb": settings.system_disk_reserve_gb,
        "opslagfout": storage_error,
    }


QSV_ATTEMPT_TIMEOUT = 30.0
QSV_TOTAL_BUDGET = 240.0


def node_host_info(node: Path) -> dict[str, Any]:
    """Wat de host over een render-node zegt: kerneldriver (i915/xe), PCI-ID, card-node."""

    sysdev = Path("/sys/class/drm") / node.name / "device"
    driver = None
    with suppress(OSError):
        driver = os.path.basename(os.readlink(sysdev / "driver"))
    device = vendor = None
    with suppress(OSError):
        device = (sysdev / "device").read_text().strip().lower()
    with suppress(OSError):
        vendor = (sysdev / "vendor").read_text().strip().lower()
    cards = sorted(str(p) for p in Path("/dev/dri").glob("card*")) if Path(
        "/dev/dri").is_dir() else []
    return {"node": str(node), "kerneldriver": driver, "pci_device": device,
            "pci_vendor": vendor, "card_nodes": cards}


async def _run_attempt(arguments: list[str], env: dict[str, str], target: Path,
                       timeout: float) -> tuple[bool, int | None, str]:
    process: asyncio.subprocess.Process | None = None
    try:
        process = await asyncio.create_subprocess_exec(
            *arguments, stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.STDOUT,
            env=env)
        stdout, _ = await asyncio.wait_for(process.communicate(), timeout=timeout)
        output = stdout.decode("utf-8", errors="replace")
        ok = process.returncode == 0 and target.is_file() and target.stat().st_size > 0
        return ok, process.returncode, output
    except (FileNotFoundError, PermissionError) as exc:
        return False, None, str(exc)
    except TimeoutError:
        # Een hangende driver mag geen ffmpeg-proces (en /dev/dri-handle) achterlaten.
        if process is not None and process.returncode is None:
            with suppress(ProcessLookupError):
                process.kill()
            await process.wait()
        return False, None, f"QSV-testencode duurde langer dan {int(timeout)} s en is gestopt"


def _attempt_args(variant: dict[str, Any], node: str, target: Path) -> list[str]:
    from app.media import gpuvarianten as gv

    rate = (["-global_quality", "30"] if variant.get("rate") == "icq"
            else ["-b:v", "2M", "-maxrate", "3M", "-bufsize", "4M"])
    return ["ffmpeg", "-hide_banner", "-loglevel", "warning", "-y",
            *gv.hw_args(variant, node),
            "-f", "lavfi", "-i", "testsrc2=size=1280x720:rate=10:duration=1",
            "-vf", gv.upload_filter(variant, 16), "-an",
            *gv.codec_args(variant), *rate, "-f", "ivf", str(target)]


async def qsv_test(settings: Settings, nodes: list[Path] | None = None) -> dict[str, Any]:
    """Probeer per render-node de GPU-varianten op volgorde; de eerste die een echt
    AV1-bestand maakt wint (node + variant). Elk tijdelijk bestand wordt opgeruimd."""

    from app.media import gpuvarianten as gv

    candidates = list(nodes) if nodes else [settings.render_node]
    render_node = str(candidates[0])
    try:
        settings.validate_storage()
    except ConfigError as exc:
        return {
            "geslaagd": False,
            "render_node": render_node,
            "melding": f"QSV-test niet uitgevoerd: opslag niet gereed: {exc}",
            "reden": "Opslag niet gereed",
            "hint": "Controleer de werkmap van de encoder.",
            "pogingen": [],
            "tijdelijke_bestanden_opgeruimd": True,
        }
    loop = asyncio.get_running_loop()
    deadline = loop.time() + QSV_TOTAL_BUDGET
    attempts: list[dict[str, Any]] = []
    base_env = dict(os.environ)
    ihd_env = {**base_env, "LIBVA_DRIVER_NAME": "iHD"}
    plain_env = {k: v for k, v in base_env.items() if k != "LIBVA_DRIVER_NAME"}
    hosts = [node_host_info(Path(n)) for n in candidates]
    with tempfile.TemporaryDirectory(prefix="qsv-test-", dir=settings.work_root) as directory:
        plan: list[tuple[str, dict[str, Any], dict[str, str], str]] = []
        for candidate in candidates:
            for variant in gv.VARIANTEN:
                plan.append((str(candidate), variant, ihd_env, "iHD"))
            # één keer zonder LIBVA_DRIVER_NAME (laat libva zelf kiezen)
            plan.append((str(candidate), gv.VARIANTEN[0], plain_env, "auto"))
        for index, (node, variant, env, env_label) in enumerate(plan):
            remaining = deadline - loop.time()
            if remaining <= 1:
                attempts.append({"node": node, "variant": variant["id"], "env": env_label,
                                 "returncode": None, "geslaagd": False,
                                 "uitvoer": "overgeslagen: tijdsbudget van de proef op"})
                break
            target = Path(directory) / f"controle-{index}.ivf"
            ok, code, output = await _run_attempt(
                _attempt_args(variant, node, target), env, target,
                min(QSV_ATTEMPT_TIMEOUT, remaining))
            with suppress(OSError):
                target.unlink()
            attempts.append({"node": node, "variant": variant["id"], "env": env_label,
                             "returncode": code, "geslaagd": ok, "uitvoer": output[-1500:]})
            if ok:
                chosen = {**variant, "libva_driver": "iHD" if env_label == "iHD" else None}
                return {
                    "geslaagd": True,
                    "render_node": node,
                    "variant": chosen,
                    "melding": f"QSV-testencode geslaagd ({variant['id']} op {node})",
                    "pogingen": attempts,
                    "host": hosts,
                    "tijdelijke_bestanden_opgeruimd": True,
                }
    vainfo_tail = ""
    if shutil.which("vainfo"):
        _ok, _code, out = await _run_attempt(
            ["vainfo", "--display", "drm", "--device", render_node], ihd_env,
            Path("/nonexistent"), 15)
        vainfo_tail = out[-1500:]
    # De meest zeggende fout: de eerste echte poging met uitvoer.
    first = next((a for a in attempts if a["uitvoer"]), {"uitvoer": ""})
    reason, hint = gv.explain(str(first["uitvoer"]))
    drivers = {h.get("kerneldriver") for h in hosts}
    if "xe" in drivers:
        hint += (" Let op: de host gebruikt de xe-kerneldriver; met i915 werkt Quick Sync "
                 "op de Arc A-serie het best.")
    return {
        "geslaagd": False,
        "render_node": render_node,
        "melding": str(first["uitvoer"])[-1000:] or reason,
        "reden": reason,
        "hint": hint,
        "pogingen": attempts,
        "host": hosts,
        "vainfo": vainfo_tail,
        "tijdelijke_bestanden_opgeruimd": True,
    }


def render_pci_address(node: Path) -> str | None:
    """PCI-adres van een render node (bv. 0000:03:00.0), zoals fdinfo 'drm-pdev' het noemt."""
    try:
        return Path(os.path.realpath(f"/sys/class/drm/{node.name}/device")).name or None
    except OSError:
        return None


def fdinfo_video_busy(pdev: str, proc_root: Path = Path("/proc")) -> tuple[dict[str, int], int]:
    """Bezette tijd (ns) van de video-engine per DRM-client op ``pdev``, plus het aantal
    video-engines (drm-engine-capacity-video). Clients worden op drm-client-id ontdubbeld."""
    busy: dict[str, int] = {}
    capacity = 1
    try:
        pids = [p for p in proc_root.iterdir() if p.name.isdigit()]
    except OSError:
        return busy, capacity
    for pid in pids:
        try:
            infos = list((pid / "fdinfo").iterdir())
        except OSError:
            continue
        for info in infos:
            try:
                text = info.read_text(errors="replace")
            except OSError:
                continue
            if "drm-engine-video" not in text:
                continue
            fields: dict[str, str] = {}
            for line in text.splitlines():
                key, _, value = line.partition(":")
                fields[key.strip()] = value.strip()
            if fields.get("drm-pdev") != pdev:
                continue
            client = fields.get("drm-client-id") or f"{pid.name}:{info.name}"
            raw = fields.get("drm-engine-video", "0").split()[0]
            try:
                busy[client] = max(busy.get(client, 0), int(raw))
            except ValueError:
                continue
            with suppress(ValueError):
                capacity = max(capacity, int(fields.get("drm-engine-capacity-video", "1")))
    return busy, capacity
