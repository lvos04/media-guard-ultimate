#!/usr/bin/env python3
"""Stub MediaGuard core hook server for testing the Sonarr/Radarr forks in isolation (stdlib only).

Implements packages/contracts/fork-hooks.md:
  POST /api/v1/hooks/pre-grab
  POST /api/v1/hooks/pre-import
  POST /api/v1/hooks/arr-webhook/{instance_id}     (stock Webhook connection events)

Auth: X-MediaGuard-Hook-Key must match the key configured for the payload's instance_id
(--keys "sonarr-series=k1,sonarr-anime=k2"); webhooks also accept HTTP Basic instance_id:key.
Wrong/missing key -> 401 (recorded).

Admin API (JSON):
  POST   /admin/rules   {"hook": "pre-grab"|"pre-import"|"*", "instance_id": "...", "title_contains": "...",
                         "decision": "accept|reject|defer", "reason_code": "...", "message": "...",
                         "score_adjustment": 0, "delay_ms": 0, "status": 200, "times": null}
                        first matching rule wins (most recently added first); times = max uses
  DELETE /admin/rules
  GET    /admin/calls[?hook=&instance_id=]   recorded calls (headers, payload, decision, latency)
  DELETE /admin/calls
  GET    /health
Default decision without matching rule: accept.
"""
import argparse
import base64
import json
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import parse_qs, urlparse

LOCK = threading.Lock()
RULES = []
CALLS = []


def title_of(hook, payload):
    if hook == "pre-grab":
        return ((payload or {}).get("release") or {}).get("title") or ""
    if hook == "pre-import":
        return " ".join(filter(None, [(payload or {}).get("release_title"), (payload or {}).get("path")]))
    return json.dumps(payload)[:300]


def pick_rule(hook, instance_id, title):
    with LOCK:
        for rule in reversed(RULES):
            if rule.get("hook", "*") not in ("*", hook):
                continue
            if rule.get("instance_id") and rule["instance_id"] != instance_id:
                continue
            if rule.get("title_contains") and rule["title_contains"].lower() not in title.lower():
                continue
            if rule.get("times") is not None:
                if rule["times"] <= 0:
                    continue
                rule["times"] -= 1
            return dict(rule)
    return None


class Handler(BaseHTTPRequestHandler):
    server_version = "MediaGuardStub/1.0"
    protocol_version = "HTTP/1.1"

    def log_message(self, fmt, *args):
        if self.server.verbose:
            super().log_message(fmt, *args)

    def _send(self, code, body):
        data = json.dumps(body).encode()
        try:
            self.send_response(code)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(data)))
            self.end_headers()
            self.wfile.write(data)
        except (BrokenPipeError, ConnectionResetError):
            pass  # client gave up (timeout test)

    def _body(self):
        length = int(self.headers.get("Content-Length") or 0)
        raw = self.rfile.read(length) if length else b""
        try:
            return json.loads(raw or b"null")
        except ValueError:
            return {"_raw": raw.decode(errors="replace")}

    def do_GET(self):
        url = urlparse(self.path)
        params = {k: v[-1] for k, v in parse_qs(url.query).items()}
        if url.path == "/health":
            return self._send(200, {"status": "ok"})
        if url.path == "/admin/calls":
            with LOCK:
                rows = [c for c in CALLS
                        if (not params.get("hook") or c["hook"] == params["hook"])
                        and (not params.get("instance_id") or c["instance_id"] == params["instance_id"])]
            return self._send(200, rows)
        if url.path == "/admin/rules":
            with LOCK:
                return self._send(200, RULES)
        return self._send(404, {"error": "not found"})

    def do_DELETE(self):
        path = urlparse(self.path).path
        with LOCK:
            if path == "/admin/rules":
                RULES.clear()
            elif path == "/admin/calls":
                CALLS.clear()
            else:
                return self._send(404, {"error": "not found"})
        return self._send(200, {"ok": True})

    def do_PUT(self):
        return self.do_POST()

    def do_POST(self):
        path = urlparse(self.path).path
        body = self._body()
        if path == "/admin/rules":
            rules = body if isinstance(body, list) else [body]
            with LOCK:
                RULES.extend(rules)
            return self._send(201, {"rules": len(RULES)})
        if path.startswith("/api/v1/hooks/arr-webhook/"):
            instance_id = path.rsplit("/", 1)[-1]
            key = self.headers.get("X-MediaGuard-Hook-Key")
            auth = self.headers.get("Authorization", "")
            if auth.startswith("Basic "):
                user, _, pwd = base64.b64decode(auth[6:]).decode().partition(":")
                if user == instance_id:
                    key = key or pwd
            ok = self.server.keys.get(instance_id) in (None, key) if self.server.keys else True
            self._record("arr-webhook", instance_id, body, None, 0, 200 if ok else 401, key)
            return self._send(200 if ok else 401, {"ok": ok})
        if path in ("/api/v1/hooks/pre-grab", "/api/v1/hooks/pre-import"):
            hook = path.rsplit("/", 1)[-1]
            started = time.time()
            instance_id = (body or {}).get("instance_id")
            key = self.headers.get("X-MediaGuard-Hook-Key")
            if self.server.keys and self.server.keys.get(instance_id) != key:
                self._record(hook, instance_id, body, None, started, 401, key)
                return self._send(401, {"error": "invalid hook key"})
            rule = pick_rule(hook, instance_id, title_of(hook, body)) or {}
            delay = rule.get("delay_ms", 0) / 1000.0
            if delay:
                time.sleep(delay)
            decision = {"decision": rule.get("decision", "accept"),
                        "reason_code": rule.get("reason_code", "OK" if rule.get("decision", "accept") == "accept" else "STUB_REJECT"),
                        "message": rule.get("message", "Stub-beslissing"),
                        "score_adjustment": rule.get("score_adjustment", 0)}
            status = rule.get("status", 200)
            self._record(hook, instance_id, body, decision, started, status, key)
            if status != 200:
                return self._send(status, {"error": "stub error"})
            return self._send(200, decision)
        return self._send(404, {"error": "not found"})

    def _record(self, hook, instance_id, payload, decision, started, status, key):
        with LOCK:
            CALLS.append({"time": time.time(), "hook": hook, "instance_id": instance_id, "status": status,
                          "key_ok": status != 401, "latency_ms": int((time.time() - started) * 1000) if started else 0,
                          "title": title_of(hook, payload), "decision": decision, "payload": payload,
                          "user_agent": self.headers.get("User-Agent")})


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--port", type=int, default=8100)
    ap.add_argument("--keys", default="", help="instance=key,instance2=key2 (empty = accept any key)")
    ap.add_argument("--verbose", action="store_true")
    args = ap.parse_args()
    srv = ThreadingHTTPServer(("0.0.0.0", args.port), Handler)
    srv.keys = dict(kv.split("=", 1) for kv in args.keys.split(",") if "=" in kv)
    srv.verbose = args.verbose
    print(f"stub mediaguard listening on :{args.port} ({len(srv.keys)} instance keys)", flush=True)
    srv.serve_forever()


if __name__ == "__main__":
    main()
