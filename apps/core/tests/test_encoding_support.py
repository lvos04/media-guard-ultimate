"""Nep-encoder (httpx.MockTransport) volgens packages/contracts/encoder-bridge.md, plus
helpers voor probe-dicts in het formaat van services/encoder/app/media/probe.py.

Bevat zelf geen tests; andere testbestanden importeren hieruit.
"""

from __future__ import annotations

import copy
import hashlib
import json
import os
from collections.abc import Callable
from pathlib import Path
from typing import Any

import httpx

TOKEN = "test-internal-token"
MD_RUW = {"red_x": 0.68, "red_y": 0.32, "green_x": 0.265, "green_y": 0.69, "blue_x": 0.15,
          "blue_y": 0.06, "white_point_x": 0.3127, "white_point_y": 0.329,
          "max_luminance": 1000.0, "min_luminance": 0.0001}


def make_probe(path: str, *, codec: str = "hevc", height: int = 2160, dv_profile: int = 0,
               dv_compat: int = 0, dv_el_type: str | None = None, hdr10: bool = False,
               hdr10plus: bool = False, audio: list[tuple[str, str]] | None = None,
               subs: list[str] | None = None, duration: float = 3600.0) -> dict[str, Any]:
    audio = audio if audio is not None else [("eng", ""), ("jpn", "")]
    subs = subs if subs is not None else ["nld", "eng"]
    probe: dict[str, Any] = {
        "pad": path, "codec": codec, "width": 3840 if height > 1200 else 1920,
        "height": height, "fps": "24000/1001", "duration": duration,
        "size": 10_000_000, "container": "matroska,webm", "kbps": 30000,
        "hdr10plus": hdr10plus, "dv_profile": dv_profile, "dv_compat": dv_compat,
        "is_hdr10": hdr10 or dv_compat == 1, "mastering_display": "x" if hdr10 or dv_compat == 1
        else "", "md_ruw": dict(MD_RUW) if hdr10 or dv_compat == 1 else {},
        "content_light": "1000,400" if hdr10 or dv_compat == 1 else "",
        "audio": [{"index": i + 1, "codec": "eac3", "kanalen": 6, "taal": lang,
                   "titel": title, "commentaar": False, "bit_rate": 0}
                  for i, (lang, title) in enumerate(audio)],
        "subs": [{"index": 10 + i, "codec": "subrip", "taal": s, "forced": False,
                  "commentaar": False} for i, s in enumerate(subs)],
        "attachments": 0,
    }
    if dv_el_type:
        probe["dv_el_type"] = dv_el_type
    return probe


def av1_output(source: dict[str, Any]) -> dict[str, Any]:
    """Correct resultaat: AV1, DV 8.x/7 → profiel 10, alles behouden."""
    out = copy.deepcopy(source)
    out["codec"] = "av1"
    if source.get("dv_profile"):
        out["dv_profile"] = 10
        out["dv_compat"] = 4 if source.get("dv_compat") == 4 else 1
    return out


class FakeEncoder:
    def __init__(self, staging_dir: Path) -> None:
        self.staging_dir = staging_dir
        self.probes: dict[str, dict[str, Any]] = {}
        self.caps: dict[str, Any] = {
            "ffmpeg": "8.0", "encoders": {"libsvtav1": {"dolbyvision": True},
                                          "av1_qsv": {}},
            "dovi_tool": True, "mkvmerge": True, "devices": ["/dev/dri/renderD128"]}
        self.plan_result: dict[str, Any] = {"actie": "hercoderen", "reden": "boven drempel",
                                            "expected_size": 4_000_000}
        self.jobs: dict[int, dict[str, Any]] = {}
        self.calls: list[tuple[str, str, Any]] = []
        self.transform: Callable[[dict[str, Any]], dict[str, Any]] = av1_output
        self.output_bytes = b"AV1-OUTPUT" * 1000
        self.polls_until_done = 1
        self.unavailable = False
        self.verification_passed = True
        # Vrije-ruimtebodem (services/encoder: PUT /settings/storage, default 150 GB)
        self.cache_min_free_gb = 150
        self.cache_free_gb = 500.0
        # Baan zoals de echte encoder hem kiest: 'cpu' als de policy dat zegt of bij DV/
        # HDR10+, anders de GPU. Tests kunnen dit vervangen.
        self.lane_for: Callable[[dict[str, Any]], str] = self._default_lane

    def _default_lane(self, body: dict[str, Any]) -> str:
        policy = body.get("policy") or {}
        if policy.get("encoder") in ("cpu", "gpu"):
            return str(policy["encoder"])
        probe = self.probes.get(str(body.get("source_path"))) or {}
        return "cpu" if probe.get("dv_profile") or probe.get("hdr10plus") else "gpu"

    def lanes_paused(self) -> list[dict[str, Any]]:
        if self.cache_free_gb >= self.cache_min_free_gb:
            return []
        reason = (f"automatisch: cache heeft nog {self.cache_free_gb:.1f} GB vrij, onder de "
                  f"pauzebodem van {self.cache_min_free_gb} GB; encoderen gepauzeerd tot er "
                  "weer ruimte is")
        return [{"lane": lane, "reason": reason, "storage_pause": True}
                for lane in ("cpu", "gpu")]

    def storage_guard(self) -> dict[str, Any]:
        return {"cache_min_free_gb": self.cache_min_free_gb, "free_gb": self.cache_free_gb,
                "below_floor": self.cache_free_gb < self.cache_min_free_gb}

    def add_source(self, path: Path, **kwargs: Any) -> dict[str, Any]:
        probe = make_probe(str(path), **kwargs)
        probe["size"] = path.stat().st_size
        self.probes[str(path)] = probe
        return probe

    # ── transport ─────────────────────────────────────────────────────
    def handler(self, request: httpx.Request) -> httpx.Response:
        if self.unavailable:
            raise httpx.ConnectError("encoder down", request=request)
        if request.headers.get("X-MediaGuard-Token") != TOKEN:
            return httpx.Response(401, json={"detail": "token"})
        path = request.url.path.removeprefix("/api/mediaguard/v1")
        body = json.loads(request.content or b"{}")
        self.calls.append((request.method, path, body))
        if path == "/health":
            paused = self.lanes_paused()
            return httpx.Response(200, json={
                "status": "degraded" if paused else "ok", "version": "test",
                "reasons": [f"{p['lane']}-baan gepauzeerd: {p['reason']}" for p in paused],
                "lanes_paused": paused, "storage_guard": self.storage_guard()})
        if path == "/settings/storage":
            if request.method == "PUT":
                self.cache_min_free_gb = int(body["cache_min_free_gb"])
            return httpx.Response(200, json=self.storage_guard()
                                  | {"lanes_paused": self.lanes_paused()})
        if path == "/capabilities":
            return httpx.Response(200, json=self.caps)
        if path == "/probe":
            probe = self.probes.get(body["path"])
            if probe is None:
                return httpx.Response(422, json={"detail": "onleesbaar"})
            return httpx.Response(200, json=probe)
        if path == "/plan":
            return httpx.Response(200, json=self.plan_result | {"policy": body["policy"]})
        if path == "/trials":
            return httpx.Response(200, json={"results": [{"crf": 30, "size": 1234,
                                                          "vmaf": 95.1}]})
        if path == "/jobs" and request.method == "POST":
            lane = self.lane_for(body)
            if lane in (body.get("avoid_lanes") or []):
                return httpx.Response(409, json={
                    "detail": f"LANE_FULL: {lane}-baan zit vol bij MediaGuard; bestand later "
                              "opnieuw aanbieden"})
            job_id = len(self.jobs) + 1
            self.jobs[job_id] = {"job_id": job_id, "status": "queued", "request": body,
                                 "polls": 0, "lane": lane}
            return httpx.Response(200, json={"job_id": job_id, "lane": lane})
        parts = path.strip("/").split("/")
        if parts[0] == "jobs" and len(parts) >= 2:
            job = self.jobs.get(int(parts[1]))
            if job is None:
                return httpx.Response(404, json={})
            if len(parts) == 3:
                job["status"] = {"cancel": "cancelled", "committed": "committed",
                                 "rejected": "rejected"}[parts[2]]
                return httpx.Response(200, json={"status": job["status"]})
            return httpx.Response(200, json=self._advance(job))
        return httpx.Response(404, json={"detail": path})

    def _advance(self, job: dict[str, Any]) -> dict[str, Any]:
        job["polls"] += 1
        req = job["request"]
        if job["status"] in ("queued", "running") and job["polls"] >= self.polls_until_done:
            src = Path(req["source_path"])
            st = os.stat(src)
            out_dir = self.staging_dir / str(job["job_id"])
            out_dir.mkdir(parents=True, exist_ok=True)
            out = out_dir / (src.stem + ".mkv")
            out.write_bytes(self.output_bytes)
            source_probe = self.probes[str(src)]
            self.probes[str(out)] = self.transform(source_probe)
            job.update({
                "status": "awaiting_commit",
                "source_snapshot": {"path": str(src), "size": st.st_size,
                                    "mtime_ns": st.st_mtime_ns, "inode": st.st_ino,
                                    "device": st.st_dev, "nlink": st.st_nlink,
                                    "probe": source_probe},
                "staged_output": {"path": str(out), "size": out.stat().st_size,
                                  "sha256": hashlib.sha256(self.output_bytes).hexdigest()},
                "verification": {"passed": self.verification_passed,
                                 "checks": [{"name": "duration",
                                             "passed": self.verification_passed}]},
                "progress": 1.0})
        elif job["status"] == "queued":
            job["status"] = "running"
            job["progress"] = 0.5
        return {k: v for k, v in job.items() if k not in ("request", "polls")}


def install(ctx: Any, staging_dir: Path) -> FakeEncoder:
    fake = FakeEncoder(staging_dir)
    ctx.services["encoder_transport"] = httpx.MockTransport(fake.handler)
    return fake


class FakeArr:
    def __init__(self) -> None:
        self.commands: list[tuple[str, dict[str, Any]]] = []
        self.fail = False

    async def command(self, name: str, **body: Any) -> dict[str, Any]:
        if self.fail:
            raise RuntimeError("arr offline")
        self.commands.append((name, body))
        return {"id": len(self.commands), "name": name}


class FakeInstance:
    def __init__(self, id: str, media_type: str = "tv", *, suspended: bool = False,  # noqa: A002
                 shadow_mode: bool = False, app: str = "sonarr") -> None:
        self.id = id
        self.media_type = media_type
        self.suspended = suspended
        self.shadow_mode = shadow_mode
        self.archived = False
        self.app = app


class FakeRegistry:
    def __init__(self, *instances: FakeInstance) -> None:
        self.instances = {i.id: i for i in instances}
        self.arr = FakeArr()

    def get_instance(self, ctx: Any, instance_id: str) -> FakeInstance | None:
        return self.instances.get(instance_id)

    def arr_client(self, ctx: Any, instance_id: str) -> FakeArr:
        return self.arr
