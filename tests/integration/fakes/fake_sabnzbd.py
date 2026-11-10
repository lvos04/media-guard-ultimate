#!/usr/bin/env python3
"""Fake SABnzbd-compatible download client for MediaGuard fork integration tests (stdlib only).

Implements the SABnzbd API calls Sonarr/Radarr use (all on /api?mode=...&apikey=...&output=json):
  version, get_config, fullstatus, addfile (multipart NZB upload), addurl, queue (+ name=delete),
  history (+ name=delete), retry.

A job "downloads" for --download-seconds, then is completed by copying a real (small, valid)
MKV made with ffmpeg into <complete_dir>/<category dir>/<job name>/<job name>.mkv, so
Sonarr/Radarr actually import it (ffprobe runtime is long enough to not count as a sample).

Admin API (JSON):
  GET  /admin/jobs                 all jobs (queue + history) with category, state, files
  POST /admin/config               {"download_seconds": 3, "fail_next": false}
  GET  /admin/calls                every API call (mode, category, apikey)
  GET  /health
"""
import argparse
import email
import email.policy
import json
import os
import re
import shutil
import subprocess
import threading
import time
import uuid
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import parse_qs, urlparse
from urllib.request import urlopen

LOCK = threading.Lock()
JOBS = []          # dicts
CALLS = []
CONFIG = {"download_seconds": 4.0, "fail_next": False}
CATEGORIES = ["tv", "tv-anime", "movies", "movies-anime"]


def ensure_sample(path):
    if os.path.exists(path) and os.path.getsize(path) > 0:
        return path
    os.makedirs(os.path.dirname(path), exist_ok=True)
    # 20 minute, 1 fps, tiny video + mono audio with language tags: a few hundred KB, long enough
    # to never be classified as a sample by Sonarr/Radarr.
    subprocess.run([
        "ffmpeg", "-hide_banner", "-loglevel", "error", "-y",
        "-f", "lavfi", "-i", "color=c=darkblue:s=320x180:r=1",
        "-f", "lavfi", "-i", "anullsrc=r=8000:cl=mono",
        "-t", "1200", "-c:v", "libx264", "-preset", "ultrafast", "-g", "600",
        "-c:a", "aac", "-b:a", "8k", "-metadata:s:a:0", "language=eng", path,
    ], check=True)
    return path


def safe_name(name):
    name = re.sub(r"[\\/:*?\"<>|]", "", name).strip()
    return name[:200] or "job"


def complete_job(job, server):
    time.sleep(CONFIG["download_seconds"])
    with LOCK:
        if job["status"] == "Deleted":
            return
        fail = CONFIG["fail_next"]
        CONFIG["fail_next"] = False
    cat_dir = os.path.join(server.complete_dir, job["cat"])
    job_dir = os.path.join(cat_dir, job["name"])
    if fail:
        with LOCK:
            job.update(status="Failed", fail_message="Simulated failure", completed=time.time())
        return
    os.makedirs(job_dir, exist_ok=True)
    target = os.path.join(job_dir, job["name"] + ".mkv")
    shutil.copyfile(server.sample, target)
    os.chmod(target, 0o664)
    with LOCK:
        job.update(status="Completed", storage=job_dir, files=[target], completed=time.time(),
                   bytes=os.path.getsize(target))


class Handler(BaseHTTPRequestHandler):
    server_version = "MediaGuardFakeSAB/1.0"

    def log_message(self, fmt, *args):
        if self.server.verbose:
            super().log_message(fmt, *args)

    def _send(self, code, body):
        data = json.dumps(body).encode()
        self.send_response(code)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(data)))
        self.end_headers()
        self.wfile.write(data)

    def _params(self):
        url = urlparse(self.path)
        return url.path.rstrip("/"), {k: v[-1] for k, v in parse_qs(url.query).items()}

    def do_GET(self):
        path, params = self._params()
        if path == "/health":
            return self._send(200, {"status": "ok"})
        if path == "/admin/jobs":
            with LOCK:
                return self._send(200, JOBS)
        if path == "/admin/calls":
            with LOCK:
                return self._send(200, CALLS)
        if path == "/api":
            return self._api(params, None)
        return self._send(404, {"error": "not found"})

    def do_POST(self):
        path, params = self._params()
        if path == "/admin/config":
            length = int(self.headers.get("Content-Length") or 0)
            data = json.loads(self.rfile.read(length) or b"{}")
            with LOCK:
                CONFIG.update({k: v for k, v in data.items() if k in CONFIG})
                return self._send(200, CONFIG)
        if path == "/api":
            upload = None
            ctype = self.headers.get("Content-Type", "")
            length = int(self.headers.get("Content-Length") or 0)
            raw = self.rfile.read(length)
            if ctype.startswith("multipart/form-data"):
                msg = email.message_from_bytes(b"Content-Type: " + ctype.encode() + b"\r\n\r\n" + raw,
                                               policy=email.policy.HTTP)
                for part in msg.iter_parts():
                    if part.get_param("name", header="content-disposition") in ("name", "nzbfile") and part.get_filename():
                        upload = (part.get_filename(), part.get_payload(decode=True))
                        break
            return self._api(params, upload)
        return self._send(404, {"error": "not found"})

    def _api(self, params, upload):
        mode = params.get("mode", "")
        apikey = params.get("apikey")
        if self.server.apikey and apikey != self.server.apikey:
            return self._send(200, {"status": False, "error": "API Key Incorrect"})
        with LOCK:
            CALLS.append({"time": time.time(), "mode": mode, "name": params.get("name"),
                          "category": params.get("category") or params.get("cat")})
        if mode == "version":
            return self._send(200, {"version": "4.3.3"})
        if mode == "get_config":
            return self._send(200, {"config": {
                "misc": {"complete_dir": self.server.complete_dir, "download_dir": "/downloads/incomplete",
                         "tv_categories": [], "enable_tv_sorting": False, "movie_categories": [],
                         "enable_movie_sorting": False, "date_categories": [], "enable_date_sorting": False,
                         "pre_check": False, "history_retention": "", "history_retention_option": "all",
                         "history_retention_number": 0},
                "categories": [{"name": "*", "order": 0, "pp": "3", "script": "None", "dir": "", "priority": -100}] +
                              [{"name": c, "order": i + 1, "pp": "", "script": "Default", "dir": c, "priority": -100}
                               for i, c in enumerate(CATEGORIES)],
                "servers": [], "sorters": []}})
        if mode == "fullstatus":
            return self._send(200, {"status": {"completedir": self.server.complete_dir}})
        if mode in ("addfile", "addurl"):
            if mode == "addurl":
                url = params.get("name")
                try:
                    data = urlopen(url, timeout=10).read()
                except Exception as ex:  # noqa
                    return self._send(200, {"status": False, "error": str(ex)})
                upload = (os.path.basename(urlparse(url).path), data)
            if not upload:
                return self._send(200, {"status": False, "error": "No file"})
            filename, data = upload
            title = re.sub(r"\.nzb$", "", filename, flags=re.I)
            m = re.search(rb'<meta type="title">([^<]+)</meta>', data)
            if m and not title:
                title = m.group(1).decode()
            if b"<nzb" not in data:
                return self._send(200, {"status": False, "error": "Invalid NZB"})
            nzo = "SABnzbd_nzo_" + uuid.uuid4().hex[:8]
            job = {"nzo_id": nzo, "name": safe_name(title), "cat": params.get("cat") or "*",
                   "priority": params.get("priority", "0"), "status": "Downloading", "added": time.time(),
                   "bytes": len(data) * 1000, "storage": None, "files": [], "fail_message": ""}
            with LOCK:
                JOBS.append(job)
            threading.Thread(target=complete_job, args=(job, self.server), daemon=True).start()
            return self._send(200, {"status": True, "nzo_ids": [nzo]})
        if mode in ("queue", "history"):
            if params.get("name") == "delete":
                ids = (params.get("value") or "").split(",")
                with LOCK:
                    for job in JOBS:
                        if job["nzo_id"] in ids:
                            job["status"] = "Deleted" if mode == "queue" else job["status"]
                            job["removed"] = True
                return self._send(200, {"status": True, "nzo_ids": ids})
            cat = params.get("category")
            with LOCK:
                rows = [j for j in JOBS if not j.get("removed") and (not cat or j["cat"] == cat)]
                if mode == "queue":
                    slots = [{"status": "Downloading", "index": i, "timeleft": "0:00:05",
                              "mb": f"{j['bytes'] / 1e6:.2f}", "mbleft": f"{j['bytes'] / 2e6:.2f}",
                              "filename": j["name"], "priority": "Normal", "cat": j["cat"],
                              "percentage": "50", "nzo_id": j["nzo_id"]}
                             for i, j in enumerate(r for r in rows if r["status"] == "Downloading")]
                    return self._send(200, {"queue": {"my_home": "/downloads", "paused": False,
                                                      "noofslots": len(slots), "slots": slots}})
                slots = [{"fail_message": j["fail_message"], "bytes": j["bytes"], "category": j["cat"],
                          "nzb_name": j["name"] + ".nzb", "download_time": 4, "storage": j["storage"] or "",
                          "status": j["status"], "nzo_id": j["nzo_id"], "name": j["name"]}
                         for j in rows if j["status"] in ("Completed", "Failed")]
                return self._send(200, {"history": {"paused": False, "noofslots": len(slots), "slots": slots}})
        if mode == "retry":
            return self._send(200, {"status": True, "nzo_id": params.get("value")})
        return self._send(200, {"status": False, "error": f"unsupported mode {mode}"})


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--port", type=int, default=8080)
    ap.add_argument("--apikey", default="")
    ap.add_argument("--complete-dir", default="/downloads/complete")
    ap.add_argument("--sample", default="/fixtures/sample.mkv")
    ap.add_argument("--download-seconds", type=float, default=4.0)
    ap.add_argument("--verbose", action="store_true")
    args = ap.parse_args()
    CONFIG["download_seconds"] = args.download_seconds
    os.makedirs(args.complete_dir, exist_ok=True)
    srv = ThreadingHTTPServer(("0.0.0.0", args.port), Handler)
    srv.apikey = args.apikey
    srv.complete_dir = args.complete_dir
    srv.sample = ensure_sample(args.sample)
    srv.verbose = args.verbose
    print(f"fake sabnzbd listening on :{args.port}, complete_dir={args.complete_dir}", flush=True)
    srv.serve_forever()


if __name__ == "__main__":
    main()
