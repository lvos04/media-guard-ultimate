"""Eerlijke capability-detectie voor core en fail-closed GPU-/mountbewaking.

Alles wat hier staat is gemeten in de draaiende container, niet aangenomen:
ffmpeg-versie en encoders, de libsvtav1 ``-dolbyvision``-optie (>= 7.1),
dovi_tool/mkvmerge/mediainfo, libvmaf of SSIM/PSNR-terugval, /dev/dri en
VA-API/QSV. De GPU-baan staat alleen aan na een geslaagde echte QSV-proef.
"""

from __future__ import annotations

import asyncio
import os
import re
import shutil
import time
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from app.config import Settings
from app.db import Database
from app.health import dependency_report
from app.media import commandos, gpuvarianten
from app.systeem import node_host_info, qsv_test

PAUSE_PREFIX = "MediaGuard:"

DEFAULT_RENDER_NODE = Path("/dev/dri/renderD128")
INTEL_VENDOR = "0x8086"
NO_GPU_TEXT = "Geen GPU gevonden — encodes draaien op de CPU"
# Geslaagde proef: eens per 6 uur opnieuw bevestigen (driverupdate, kaart weg).
# Mislukte proef: na 30 min opnieuw; een gewijzigd /dev/dri meteen.
PROBE_RECHECK_OK_SECONDS = 6 * 3600
PROBE_RETRY_FAILED_SECONDS = 1800
_LIB_DIRS = (Path("/usr/lib/x86_64-linux-gnu"), Path("/usr/lib64"), Path("/usr/lib"))


def _render_nodes() -> list[Path]:
    root = Path("/dev/dri")
    if not root.is_dir():
        return []
    return sorted(root.glob("renderD*"))


def render_vendor(node: Path) -> str | None:
    """PCI-vendor van een render-node via sysfs (0x8086 = Intel); None = onbekend."""

    try:
        vendor = Path("/sys/class/drm") / node.name / "device" / "vendor"
        return vendor.read_text().strip().lower()
    except OSError:
        return None


def render_device_id(node: Path) -> str | None:
    try:
        path = Path("/sys/class/drm") / node.name / "device" / "device"
        return path.read_text().strip().lower()
    except OSError:
        return None


# Intel-GPU's die AV1 kunnen encoderen: Arc A (DG2, 0x56xx), Arc B (Battlemage, 0xe2xx),
# Meteor/Arrow Lake (0x7dxx) en Lunar Lake (0x64a0). Een Alder/Raptor Lake-iGPU (UHD 770,
# 0x46xx/0xa7xx) kan alleen AV1 decoderen.
_ARC_NAMES = {"0x56a6": "Intel Arc A310", "0x56a5": "Intel Arc A380", "0x56a0": "Intel Arc A770",
              "0x56a1": "Intel Arc A750", "0x56a2": "Intel Arc A580", "0xe20b": "Intel Arc B580",
              "0xe20c": "Intel Arc B570"}


def av1_encode_by_id(device: str | None) -> bool | None:
    """True = deze kaart kan AV1 encoderen, False = Intel zonder AV1-encode, None = onbekend."""

    if not device:
        return None
    return device.startswith(("0x56", "0xe2", "0x7d", "0x64a"))


def gpu_name(node: Path, device: str | None = None) -> str:
    device = device if device is not None else render_device_id(node)
    if device in _ARC_NAMES:
        return _ARC_NAMES[device]
    if device and device.startswith("0x56"):
        return "Intel Arc (A-serie)"
    if device and device.startswith("0xe2"):
        return "Intel Arc (B-serie)"
    if av1_encode_by_id(device) is False:
        return "Intel UHD/Iris (geïntegreerd)"
    return "Intel-GPU"


def _explicit_node(settings: Settings) -> bool:
    return bool(os.environ.get("MG_RENDER_NODE", "").strip()) or (
        settings.render_node != DEFAULT_RENDER_NODE)


def ranked_render_nodes(settings: Settings, explicit: bool | None = None) -> list[Path]:
    """Intel-nodes op volgorde van geschiktheid: eerst kaarten die AV1 kunnen encoderen
    (Arc), dan onbekende, als laatste een iGPU zonder AV1-encode. Een expliciet
    ingestelde MG_RENDER_NODE wint altijd."""

    if _explicit_node(settings) if explicit is None else explicit:
        return [settings.render_node]
    nodes = [n for n in _render_nodes() if render_vendor(n) in {INTEL_VENDOR, None}]

    def key(item: tuple[int, Path]) -> tuple[int, int]:
        capable = av1_encode_by_id(render_device_id(item[1]))
        return (0 if capable else 1 if capable is None else 2, item[0])

    return [n for _i, n in sorted(enumerate(nodes), key=key)]


def pick_render_node(settings: Settings) -> Path:
    """De beste Intel-node als niets expliciet is ingesteld (Arc vóór iGPU/BMC-chip)."""

    if _explicit_node(settings):
        return settings.render_node
    ranked = ranked_render_nodes(settings, explicit=False)
    return ranked[0] if ranked else settings.render_node


def device_fingerprint(render: Path) -> tuple[Any, ...]:
    """Goedkoop te meten: verandert zodra een GPU verschijnt, verdwijnt of rechten krijgt."""

    nodes = tuple(
        (str(node), os.access(node, os.R_OK | os.W_OK), render_vendor(node))
        for node in _render_nodes()
    )
    return (str(render), render.exists(), os.access(render, os.R_OK | os.W_OK), nodes)


def qsv_runtime_report() -> dict[str, Any]:
    """Statische controle: zit de Intel-runtime (oneVPL + media-driver) in het image?

    Zonder GPU kan geen echte proef draaien; dit bewijst dat QSV straks werkt zodra
    de kaart is doorgegeven (libvpl-dispatcher, libmfx-gen-runtime, iHD-VA-driver).
    """

    def find(name: str, dirs: list[Path]) -> str | None:
        for directory in dirs:
            if (directory / name).exists():
                return str(directory / name)
        return None

    lib_dirs = list(_LIB_DIRS)
    driver_dirs = [Path(d) for d in os.environ.get("LIBVA_DRIVERS_PATH", "").split(":") if d]
    driver_dirs += [d / "dri" for d in lib_dirs]
    report: dict[str, Any] = {
        "libvpl": find("libvpl.so.2", lib_dirs),
        "libmfx_gen": find("libmfx-gen.so.1.2", lib_dirs),
        "intel_media_driver": find("iHD_drv_video.so", driver_dirs),
    }
    missing = [key for key, value in report.items() if value is None]
    report["complete"] = not missing
    report["missing"] = missing
    return report


def gpu_status(
    *,
    av1_qsv: bool,
    runtime: dict[str, Any],
    render: Path,
    render_rw: bool,
    vendor: str | None,
    probe: dict[str, Any] | None,
    gpus: list[dict[str, Any]] | None = None,
) -> dict[str, Any]:
    """Eén eerlijke status voor de GPU-baan, in gewone taal.

    severity "info" = geen probleem (CPU-only is een geldige opstelling);
    "warning" = er is een GPU maar hij werkt (nog) niet.
    """

    gpus = gpus or []
    used = next((g for g in gpus if g["node"] == str(render)), None)

    def status(state: str, severity: str, message: str) -> dict[str, Any]:
        out: dict[str, Any] = {"state": state, "severity": severity, "message": message,
                               "active": state == "actief"}
        if gpus:
            out["kaart"] = (f"{used['naam']} ({Path(used['node']).name})" if used else None)
            out["niet_gebruikt"] = [
                f"{g['naam']} ({Path(g['node']).name})"
                + (" — geen AV1-encode" if g["av1_encode"] is False else "")
                for g in gpus if g["node"] != str(render)
            ]
        if probe and not probe.get("geslaagd") and state == "proef_mislukt":
            out["details"] = {
                "reden": probe.get("reden") or gpuvarianten.explain(
                    str(probe.get("melding") or ""))[0],
                "hint": probe.get("hint"),
                "pogingen": len(probe.get("pogingen") or []),
                "geprobeerd": sorted({str(a.get("variant")) for a in probe.get(
                    "pogingen") or []}),
            }
        return out

    if not render.exists():
        return status("geen_gpu", "info", NO_GPU_TEXT)
    if vendor is not None and vendor != INTEL_VENDOR:
        return status("geen_intel", "info",
                      "Geen Intel-GPU gevonden (QSV vereist Intel) — encodes draaien op de CPU")
    if not av1_qsv:
        return status("ffmpeg_zonder_qsv", "warning",
                      "Deze versie kan de GPU niet gebruiken (FFmpeg zonder av1_qsv) — "
                      "encodes draaien op de CPU")
    if not runtime.get("complete"):
        return status("runtime_ontbreekt", "warning",
                      "Intel-driver ontbreekt in de app ("
                      + ", ".join(runtime.get("missing") or []) + ") — encodes draaien op de CPU")
    if not render_rw:
        return status("geen_toegang", "warning",
                      "GPU gevonden, maar de app mag hem niet gebruiken (rechten op /dev/dri) — "
                      "encodes draaien op de CPU")
    if probe is None:
        return status("nog_niet_getest", "info",
                      "GPU gevonden; MediaGuard test hem automatisch")
    if not probe.get("geslaagd"):
        if gpus and all(g["av1_encode"] is False for g in gpus):
            return status("geen_av1_gpu", "info",
                          "Deze Intel-GPU kan geen AV1 encoderen — encodes draaien op de CPU")
        return status("proef_mislukt", "warning",
                      "GPU gevonden, maar de Intel-driver kon geen AV1-testbestand maken. "
                      "MediaGuard probeert het automatisch opnieuw; encodes draaien intussen "
                      "op de CPU")
    variant = probe.get("variant") if isinstance(probe.get("variant"), dict) else None
    name = used["naam"] if used else "GPU"
    return status("actief", "info", f"{name} actief (Intel {gpuvarianten.label(variant)})")


async def _gpu_inventory(nodes: list[Path]) -> list[dict[str, Any]]:
    """Per Intel-node: naam, PCI-ID, kerneldriver en of hij AV1 kan encoderen
    (vainfo als die antwoordt, anders op PCI-ID)."""

    out: list[dict[str, Any]] = []
    for node in nodes:
        if not node.exists():
            continue
        host = node_host_info(node)
        device = render_device_id(node) or host.get("pci_device")
        av1: bool | None = av1_encode_by_id(device)
        if node.exists() and shutil.which("vainfo"):
            code, output = await _output(["vainfo", "--display", "drm", "--device", str(node)])
            if code == 0 and "VAProfile" in output:
                av1 = re.search(r"VAProfileAV1\w*\s*:\s*VAEntrypointEnc", output) is not None
        out.append({"node": str(node), "naam": gpu_name(node, device), "pci_device": device,
                    "kerneldriver": host.get("kerneldriver"), "av1_encode": av1,
                    "gebruikt": False})
    return out


async def _output(arguments: list[str], timeout: float = 15.0) -> tuple[int, str]:
    try:
        process = await asyncio.create_subprocess_exec(
            *arguments, stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.STDOUT
        )
        stdout, _ = await asyncio.wait_for(process.communicate(), timeout=timeout)
    except (FileNotFoundError, PermissionError, TimeoutError):
        return 127, ""
    return process.returncode or 0, stdout.decode("utf-8", errors="replace")


def _version_tuple(text: str | None) -> tuple[int, int] | None:
    match = re.match(r"n?(\d+)\.(\d+)", text or "")
    return (int(match.group(1)), int(match.group(2))) if match else None


async def _tool_version(arguments: list[str], pattern: str) -> str | None:
    if shutil.which(arguments[0]) is None:
        return None
    code, output = await _output(arguments)
    match = re.search(pattern, output)
    return match.group(1) if code == 0 and match else None


def _devices() -> list[dict[str, Any]]:
    root = Path("/dev/dri")
    if not root.is_dir():
        return []
    result = []
    for entry in sorted(root.iterdir()):
        result.append(
            {
                "path": str(entry),
                "readable": os.access(entry, os.R_OK),
                "writable": os.access(entry, os.W_OK),
            }
        )
    return result


def mounts_report(settings: Settings) -> dict[str, Any]:
    roots = []
    for root in settings.media_roots:
        exists = root.is_dir()
        roots.append(
            {
                "path": str(root),
                "present": exists,
                "readable": exists and os.access(root, os.R_OK | os.X_OK),
                # Read-only is in MediaGuard-modus gewenst (K1); we rapporteren
                # het alleen, de encoder schrijft nooit in de bibliotheek.
                "writable": exists and os.access(root, os.W_OK),
            }
        )
    staging = settings.effective_staging_root
    return {
        "media_roots": roots,
        "media_ok": any(item["readable"] for item in roots),
        "staging": {
            "path": str(staging),
            "writable": staging.is_dir() and os.access(staging, os.W_OK | os.X_OK),
        },
        "cache": {
            "path": str(settings.cache_root),
            "writable": settings.cache_root.is_dir()
            and os.access(settings.cache_root, os.W_OK | os.X_OK),
        },
    }


class CapabilityService:
    """Cachet de (dure) detectie; ververs bij start of op verzoek."""

    def __init__(self, settings: Settings) -> None:
        self.settings = settings
        self.cached: dict[str, Any] | None = None
        self.qsv_probe: dict[str, Any] | None = None
        self.detected_monotonic = 0.0
        self.probe_monotonic: float | None = None  # None = nog nooit gemeten
        self.fingerprint: tuple[Any, ...] | None = None
        # Expliciet ingestelde node (MG_RENDER_NODE of instelling) blijft vast; anders
        # kiest de detectie zelf de kaart die AV1 kan encoderen (Arc vóór iGPU).
        self.explicit = _explicit_node(settings)
        self.configured_node = settings.render_node
        self.gpus: list[dict[str, Any]] = []

    def _probe_due(self) -> bool:
        if self.qsv_probe is None or self.probe_monotonic is None:
            return True
        age = time.monotonic() - self.probe_monotonic
        limit = (PROBE_RECHECK_OK_SECONDS if self.qsv_probe.get("geslaagd")
                 else PROBE_RETRY_FAILED_SECONDS)
        return age > limit

    def _apply_probe(self) -> None:
        """Een geslaagde proef bepaalt node + variant van de echte encodes."""

        probe = self.qsv_probe or {}
        if probe.get("geslaagd"):
            if probe.get("render_node"):
                self.settings.render_node = Path(str(probe["render_node"]))
            variant = probe.get("variant")
            self.settings.gpu_variant = dict(variant) if isinstance(variant, dict) else None
            driver = (variant or {}).get("libva_driver") if isinstance(variant, dict) else None
            if driver:
                os.environ["LIBVA_DRIVER_NAME"] = str(driver)
            elif isinstance(variant, dict):
                os.environ.pop("LIBVA_DRIVER_NAME", None)
        else:
            self.settings.gpu_variant = None
        for gpu in self.gpus:
            gpu["gebruikt"] = bool(probe.get("geslaagd")) and gpu["node"] == str(
                self.settings.render_node)

    async def detect(
        self,
        *,
        refresh: bool = False,
        run_qsv_probe: bool = False,
        max_age_seconds: float | None = None,
    ) -> dict[str, Any]:
        stale = (
            max_age_seconds is not None
            and self.cached is not None
            and time.monotonic() - self.detected_monotonic > max_age_seconds
        )
        # Een GPU die later verschijnt (kaart doorgegeven + herstart, of /dev/dri dat
        # pas na de start opduikt) wordt zonder handwerk opgepikt.
        candidates = (
            [self.configured_node] if self.explicit
            else ranked_render_nodes(self.settings, explicit=False)
        )
        chosen = (self.qsv_probe or {}).get("render_node") if (
            self.qsv_probe or {}).get("geslaagd") else None
        if chosen and Path(chosen) in candidates:
            self.settings.render_node = Path(chosen)
        else:
            self.settings.render_node = candidates[0] if candidates else self.configured_node
        fingerprint = device_fingerprint(self.settings.render_node)
        changed = self.fingerprint is not None and fingerprint != self.fingerprint
        if changed:
            refresh = run_qsv_probe = True
        elif self.cached is not None and self._probe_due() and (
            self.qsv_probe is not None
            or self.cached.get("gpu_status", {}).get("state") == "nog_niet_getest"
        ):
            # Zelfherstel: een mislukte proef (driver nog niet klaar) mag de GPU-baan niet
            # permanent uit laten; een geslaagde wordt periodiek opnieuw bevestigd.
            run_qsv_probe = True
        if self.cached is not None and not refresh and not run_qsv_probe and not stale:
            self.cached["mounts"] = mounts_report(self.settings)
            return self.cached
        self.fingerprint = fingerprint
        deps = await dependency_report(self.settings.render_node)
        if not deps["libsvtav1"]:
            # Een trage host kan `ffmpeg -encoders` laten time-outen; een vals
            # negatief zou de CPU-baan pauzeren. Eén herhaling, dan pas geloven.
            deps = await dependency_report(self.settings.render_node)
        version = deps.get("ffmpeg_version")
        parsed = _version_tuple(version)
        filters_code, filters = await _output(["ffmpeg", "-hide_banner", "-filters"])
        libvmaf = filters_code == 0 and re.search(r"\blibvmaf\b", filters) is not None
        ssim = filters_code == 0 and re.search(r"\bssim\b", filters) is not None
        psnr = filters_code == 0 and re.search(r"\bpsnr\b", filters) is not None
        encoders_code, encoders = await _output(["ffmpeg", "-hide_banner", "-encoders"])
        av1_vaapi = encoders_code == 0 and "av1_vaapi" in encoders
        render = self.settings.render_node
        vaapi_profiles: list[str] = []
        vaapi_av1_encode = False
        if render.exists() and shutil.which("vainfo"):
            code, output = await _output(["vainfo", "--display", "drm", "--device", str(render)])
            if code == 0:
                vaapi_profiles = sorted(
                    set(re.findall(r"(VAProfile\w+)\s*:\s*VAEntrypoint\w+", output))
                )
                vaapi_av1_encode = (
                    re.search(r"VAProfileAV1\w*\s*:\s*VAEntrypointEnc", output) is not None
                )
        render_rw = render.exists() and os.access(render, os.R_OK | os.W_OK)
        vendor = render_vendor(render) if render.exists() else None
        runtime = qsv_runtime_report()
        probe_possible = bool(
            deps["av1_qsv"] and render_rw and runtime["complete"]
            and vendor in {None, INTEL_VENDOR}
        )
        if not render.exists():
            self.qsv_probe = None  # kaart weg: een oude uitslag geldt niet meer
        if run_qsv_probe or not self.gpus or changed:
            self.gpus = await _gpu_inventory(candidates)
        if run_qsv_probe and probe_possible:
            # Kaarten waarvan vainfo zeker weet dat ze geen AV1 encoderen (bv. UHD 770)
            # slaan we over; de rest proberen we op volgorde (Arc eerst).
            probe_nodes = [Path(g["node"]) for g in self.gpus if g["av1_encode"] is not False]
            probe_nodes = probe_nodes or [render]
            try:
                self.qsv_probe = await qsv_test(self.settings, nodes=probe_nodes)
            except Exception as exc:  # noqa: BLE001 - iedere fout = GPU-baan uit
                self.qsv_probe = {"geslaagd": False, "melding": str(exc)[-500:]}
            self._apply_probe()
            render = self.settings.render_node
            render_rw = render.exists() and os.access(render, os.R_OK | os.W_OK)
            vendor = render_vendor(render) if render.exists() else None
        if run_qsv_probe:
            # Ook als de proef niet kon (geen toegang): pas na de wachttijd opnieuw.
            self.probe_monotonic = time.monotonic()
        tools = {
            "ffmpeg": version,
            "ffmpeg_ok_for_dv": parsed is not None and parsed >= (7, 1),
            "dovi_tool": await _tool_version(["dovi_tool", "--version"], r"dovi_tool\s+(\S+)"),
            "mkvmerge": await _tool_version(["mkvmerge", "--version"], r"mkvmerge\s+v?(\S+)"),
            "mediainfo": await _tool_version(["mediainfo", "--version"], r"v(\d[\w.]*)"),
            "svt_av1": await _svt_version(),
        }
        cpu_reason = None
        if not deps["libsvtav1"]:
            cpu_reason = "ffmpeg zonder libsvtav1"
        gpu = gpu_status(av1_qsv=bool(deps["av1_qsv"]), runtime=runtime, render=render,
                         render_rw=render_rw, vendor=vendor, probe=self.qsv_probe,
                         gpus=self.gpus)
        gpu_reason = None if gpu["active"] else str(gpu["message"])
        dv_svt = bool(deps["dolbyvision"] and tools["ffmpeg_ok_for_dv"] and deps["libsvtav1"])
        dv7 = bool(dv_svt and tools["dovi_tool"] is not None and tools["mkvmerge"] is not None)
        mounts = mounts_report(self.settings)
        hdr10plus = await _hdr10plus_route()
        self.detected_monotonic = time.monotonic()
        self.cached = {
            "detected_at": datetime.now(UTC).isoformat(),
            "ffmpeg": {"version": version, "dolbyvision_option": deps["dolbyvision"]},
            "encoders": {
                "libsvtav1": deps["libsvtav1"],
                "av1_qsv": deps["av1_qsv"],
                "av1_vaapi": av1_vaapi,
            },
            "tools": tools,
            "quality_metrics": {"libvmaf": libvmaf, "ssim": ssim, "psnr": psnr},
            "devices": _devices(),
            "render_node": {"path": str(render), "present": render.exists(), "rw": render_rw},
            "vaapi": {
                "available": bool(deps["vaapi"]) or bool(vaapi_profiles),
                "av1_encode": vaapi_av1_encode,
                "profiles": vaapi_profiles,
            },
            "qsv": {
                "onevpl": deps["onevpl"],
                "runtime": runtime,
                "vendor": vendor,
                "probe": self.qsv_probe,
                "ready": gpu_reason is None,
            },
            "gpu_status": gpu,
            "lanes": {
                "cpu": {
                    "enabled": cpu_reason is None and mounts["media_ok"],
                    "reason": cpu_reason
                    or (None if mounts["media_ok"] else "geen leesbare mediamount"),
                },
                "gpu": {
                    "enabled": gpu_reason is None and mounts["media_ok"],
                    "reason": gpu_reason
                    or (None if mounts["media_ok"] else "geen leesbare mediamount"),
                    "state": gpu["state"],
                    "severity": gpu["severity"],
                },
            },
            "dv_routes": _dv_routes(dv_svt, dv7),
            "hdr10plus": hdr10plus,
            "hdr10plus_route_verified": hdr10plus["route_verified"],
            "hdr10plus_route_reason": hdr10plus["reason"],
            "mounts": mounts,
            "container_mode": self.settings.container_mode,
            "replacement_mode": self.settings.replacement_mode,
        }
        return self.cached


async def _svt_version() -> str | None:
    code, output = await _output(
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
        timeout=30,
    )
    match = re.search(r"SVT-AV1 Encoder Lib v?(\S+)", output)
    return match.group(1) if match else None


async def _hdr10plus_route() -> dict[str, Any]:
    """HDR10+ -> AV1 kan alleen via libaom in FFmpeg >= 8.1 (T.35-metadata-OBU's).

    libsvtav1 (ook upstream master, 2026-11), librav1e en av1_qsv dragen ST 2094-40
    niet mee. Read-back vereist een decoder die AV1-T.35 exporteert (libdav1d in
    de systeem-FFmpeg). "route_verified" betekent: beide aanwezig én de route is in
    de testsuite met echte read-back bewezen; iedere job verifieert zelf opnieuw.
    """

    binary = commandos.ffmpeg_hdr10plus_binary()
    base: dict[str, Any] = {
        "route_verified": False,
        "encoder": "libaom-av1",
        "ffmpeg": None,
        "binary": binary,
        "modes": ["skip", "hdr10_fallback_explicit"],
        "av1_qsv": "niet ondersteund (geen HDR10+-pad in av1_qsv)",
        "libsvtav1": "niet ondersteund (FFmpeg-wrapper stuurt geen ST 2094-40 door)",
    }
    if not (Path(binary).is_file() and os.access(binary, os.X_OK)):
        return base | {"reason": f"{binary} ontbreekt (FFmpeg >= 8.1 met libaom vereist)"}
    code, output = await _output([binary, "-hide_banner", "-version"])
    match = re.search(r"ffmpeg version\s+n?(\S+)", output)
    version = match.group(1) if match else None
    base["ffmpeg"] = version
    parsed = _version_tuple(version)
    if code != 0 or parsed is None or parsed < (8, 1):
        return base | {"reason": f"FFmpeg {version} < 8.1: libaom schrijft geen HDR10+"}
    code, encoders = await _output([binary, "-hide_banner", "-encoders"])
    if code != 0 or "libaom-av1" not in encoders:
        return base | {"reason": "libaom-av1 ontbreekt in de HDR10+-FFmpeg"}
    code, decoders = await _output(["ffprobe", "-hide_banner", "-decoders"])
    if code != 0 or "libdav1d" not in decoders:
        return base | {"reason": "geen libdav1d in ffprobe: HDR10+-read-back niet mogelijk"}
    return base | {
        "route_verified": True,
        "reason": None,
        "modes": ["skip", "convert_verified", "hdr10_fallback_explicit"],
    }


def _dv_routes(dv_svt: bool, dv7: bool) -> list[dict[str, Any]]:
    svt_reason = None if dv_svt else "ffmpeg >= 7.1 met libsvtav1 -dolbyvision vereist"
    dv7_reason = None if dv7 else (svt_reason or "dovi_tool en mkvmerge vereist")
    return [
        {
            "route": "8.1",
            "technically_possible": dv_svt,
            "reason": svt_reason,
            "output": "AV1 DV profiel 10.1 (libsvtav1 -dolbyvision 1)",
        },
        {
            "route": "8.4",
            "technically_possible": dv_svt,
            "reason": svt_reason,
            "output": "AV1 DV profiel 10.4 (HLG-basis)",
        },
        {
            "route": "7_mel",
            "technically_possible": dv7,
            "reason": dv7_reason,
            "output": "dovi_tool -m 2 naar 8.1, daarna AV1 DV 10.1",
        },
        {
            "route": "7_fel",
            "technically_possible": dv7,
            "reason": dv7_reason,
            "output": "experimenteel apart resultaat; FEL-beeldinformatie gaat verloren",
            "experimental": True,
        },
        {
            "route": "5",
            "technically_possible": False,
            "reason": "geen HDR10-terugval en geen bewezen route; niet ondersteund",
        },
    ]


def apply_lane_gates(database: Database, capabilities: dict[str, Any],
                     settings: Settings | None = None) -> None:
    """Fail-closed: pauzeer een baan met duidelijke reden; hef alleen eigen pauzes op.

    Met ``settings`` (containermodus) worden daarna wachtende encodes naar de baan gezet die
    nu beschikbaar is (GPU erbij: naar de GPU; GPU weg: terug naar de CPU)."""

    for lane in ("cpu", "gpu"):
        info = capabilities["lanes"][lane]
        row = database.fetch_one(
            "SELECT gepauzeerd,pauze_reden FROM worker_config WHERE baan=?", (lane,)
        )
        if row is None:
            continue
        current_reason = str(row["pauze_reden"] or "")
        ours = current_reason.startswith(PAUSE_PREFIX)
        if not info["enabled"]:
            # GPU: de gewone-taalstatus ("Geen GPU gevonden — encodes draaien op de CPU").
            reason = (f"{PAUSE_PREFIX} {info['reason']}" if lane == "gpu"
                      else f"{PAUSE_PREFIX} {lane.upper()}-baan uit: {info['reason']}")
            # Een handmatige pauze van de gebruiker overschrijven we nooit.
            if not int(row["gepauzeerd"]) or ours:
                database.execute(
                    "UPDATE worker_config SET gepauzeerd=1,pauze_reden=? WHERE baan=?",
                    (reason, lane),
                )
        elif int(row["gepauzeerd"]) and ours:
            database.execute(
                "UPDATE worker_config SET gepauzeerd=0,pauze_reden=NULL WHERE baan=?", (lane,)
            )

    if settings is not None:
        from app.mediaguard.rebalance import maybe_rebalance

        maybe_rebalance(database, settings, capabilities)


def honest_dependencies(deps: dict[str, Any], caps: dict[str, Any] | None) -> dict[str, Any]:
    """Diagnose-uitvoer met "qsv_gereed" alleen waar na een geslaagde proef."""

    gpu = (caps or {}).get("gpu_status") or {}
    return {**deps, "qsv_gereed": bool(gpu.get("active")), "gpu_status": gpu or None}


async def capability_watch_loop(service: CapabilityService, database: Database,
                                interval: float = 120.0) -> None:
    """Containermodus: elke 2 min goedkoop kijken of er een GPU is bijgekomen of weg is.

    Alleen bij een wijziging (of als de proef aan de beurt is: mislukt na 30 min,
    geslaagd na 6 uur) draait er een echte proefencode; daarna gaan de baanpauzes mee.
    """

    import logging

    logger = logging.getLogger(__name__)
    while True:
        await asyncio.sleep(interval)
        try:
            before = (service.cached or {}).get("gpu_status", {}).get("state")
            caps = await service.detect()
            apply_lane_gates(database, caps, service.settings)
            _maybe_bench_gpu(database, service, caps)
            after = caps.get("gpu_status", {}).get("state")
            if after != before:
                logger.info("GPU-status: %s", caps["gpu_status"]["message"])
        except asyncio.CancelledError:
            raise
        except Exception as exc:  # noqa: BLE001 - bewaking mag nooit stoppen
            logger.warning("GPU-bewaking: %s", exc)


_BENCH_TASKS: set[asyncio.Task[Any]] = set()


def _maybe_bench_gpu(database: Database, service: CapabilityService,
                     caps: dict[str, Any]) -> None:
    """Rapport 7 §4.5: GPU-plekken (opnieuw) meten als de GPU er is en de baan stil staat."""
    from app import gpubench

    settings = service.settings
    if settings is None or not getattr(settings, "container_mode", False):
        return
    gpu_enabled = bool(((caps.get("lanes") or {}).get("gpu") or {}).get("enabled"))
    row = database.fetch_one(
        "SELECT COUNT(*) FROM job WHERE baan='gpu' AND status IN ('bezig','verifieren')")
    busy = bool(row and int(row[0]))
    version = (caps.get("tools") or {}).get("ffmpeg")
    if not gpubench.due(database, settings, gpu_enabled, busy, version):
        return
    config = database.fetch_one("SELECT aantal FROM worker_config WHERE baan='gpu'")
    configured = int(config["aantal"]) if config is not None else 1
    task = asyncio.create_task(
        gpubench.run(database, settings, configured=configured, ffmpeg_version=version))
    _BENCH_TASKS.add(task)
    task.add_done_callback(_BENCH_TASKS.discard)
