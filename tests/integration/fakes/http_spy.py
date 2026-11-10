#!/usr/bin/env python3
"""Transparent recording HTTP proxy (stdlib only) for integration tests.

Forwards every request to --upstream unchanged and records method, path and status, so a test
can prove which calls a client made (e.g. "MediaGuard sent only GETs to the old Sonarr before
cutover"). Request/response bodies are streamed through, not stored.

  GET    /__spy/requests     recorded calls [{time, method, path, status}]
  DELETE /__spy/requests     clear
"""
import argparse
import http.client
import json
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import urlsplit

LOCK = threading.Lock()
CALLS = []
HOP = {"connection", "keep-alive", "proxy-authenticate", "proxy-authorization", "te", "trailers",
       "transfer-encoding", "upgrade", "host", "content-length"}


class Handler(BaseHTTPRequestHandler):
    protocol_version = "HTTP/1.1"

    def log_message(self, fmt, *args):
        pass

    def _admin(self):
        if self.command == "DELETE":
            with LOCK:
                CALLS.clear()
            body = b'{"ok": true}'
        else:
            with LOCK:
                body = json.dumps(CALLS).encode()
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def _forward(self):
        if self.path.startswith("/__spy/"):
            return self._admin()
        up = urlsplit(self.server.upstream)
        length = int(self.headers.get("Content-Length") or 0)
        body = self.rfile.read(length) if length else None
        headers = {k: v for k, v in self.headers.items() if k.lower() not in HOP}
        conn = http.client.HTTPConnection(up.hostname, up.port or 80, timeout=600)
        status = 502
        try:
            conn.request(self.command, self.path, body=body, headers=headers)
            resp = conn.getresponse()
            data = resp.read()
            status = resp.status
            self.send_response(resp.status, resp.reason)
            for k, v in resp.getheaders():
                if k.lower() not in HOP:
                    self.send_header(k, v)
            self.send_header("Content-Length", str(len(data)))
            self.end_headers()
            self.wfile.write(data)
        except OSError as exc:
            msg = json.dumps({"spy_error": str(exc)}).encode()
            self.send_response(502)
            self.send_header("Content-Length", str(len(msg)))
            self.end_headers()
            self.wfile.write(msg)
        finally:
            conn.close()
            with LOCK:
                CALLS.append({"time": time.time(), "method": self.command,
                              "path": self.path.split("?", 1)[0], "status": status})

    do_GET = do_POST = do_PUT = do_DELETE = do_PATCH = do_HEAD = _forward


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--port", type=int, default=8989)
    ap.add_argument("--upstream", required=True)
    args = ap.parse_args()
    srv = ThreadingHTTPServer(("0.0.0.0", args.port), Handler)
    srv.upstream = args.upstream
    print(f"http spy :{args.port} -> {args.upstream}", flush=True)
    srv.serve_forever()


if __name__ == "__main__":
    main()
