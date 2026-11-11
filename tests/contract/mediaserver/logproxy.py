#!/usr/bin/env python3
"""Doorgeefluik voor de contracttest mediaservers: legt vast wat MediaGuard naar een mediaserver
stuurt en geeft het ongewijzigd door.

Alleen de standaardbibliotheek. Per verzoek één JSON-regel in het logbestand:

- ``t``: tijdstip (UTC), ``method``, ``path`` (zonder query);
- ``query``: per parameternaam de eerste 16 tekens van de SHA-256 van de waarde. Zo kan de test
  bewijzen dat een sleutel nooit in de URL staat zonder dat de sleutel zelf in het log komt;
- ``auth``: de **vorm** van de aanmelding (``x-emby-token``, ``mediabrowser``,
  ``x-emby-authorization``, ``x-mediabrowser-token``, ``x-plex-token``, ``authorization-anders``),
  nooit de waarde;
- ``token``: de eerste 16 tekens van de SHA-256 van de meegestuurde sleutel (of ``null``);
- ``status``: de status van de server, of 502 als de server niet antwoordt.

Geen installatiemethode en geen onderdeel van het product: alleen voor ``run.sh`` in deze map.
"""

from __future__ import annotations

import argparse
import hashlib
import http.client
import json
import re
import threading
from datetime import UTC, datetime
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from typing import Any
from urllib.parse import parse_qsl, urlsplit

HOP = {"connection", "keep-alive", "proxy-authenticate", "proxy-authorization", "te", "trailers",
       "transfer-encoding", "upgrade", "content-length", "host"}
TOKEN = re.compile(r'Token="?([^",\s]+)"?')
LOCK = threading.Lock()


def digest(value: str) -> str:
    return hashlib.sha256(value.encode()).hexdigest()[:16]


def shape(headers: Any) -> tuple[list[str], str | None]:
    """Vorm van de aanmelding en de vingerafdruk van de sleutel (nooit de sleutel zelf)."""
    found: list[str] = []
    token: str | None = None
    for name in ("x-emby-token", "x-mediabrowser-token", "x-plex-token"):
        if headers.get(name):
            found.append(name)
            token = digest(str(headers[name]))
    for name, label in (("authorization", "mediabrowser"),
                        ("x-emby-authorization", "x-emby-authorization")):
        value = str(headers.get(name) or "")
        if not value:
            continue
        if name == "authorization" and not value.lower().startswith(("mediabrowser", "emby")):
            found.append("authorization-anders")
            continue
        found.append(label)
        match = TOKEN.search(value)
        if match:
            token = digest(match.group(1))
    return sorted(found), token


class Handler(BaseHTTPRequestHandler):
    protocol_version = "HTTP/1.1"
    upstream: tuple[str, int] = ("", 0)
    logfile = ""

    def log_message(self, format: str, *args: Any) -> None:  # noqa: A002 - signatuur van de basis
        return

    def _forward(self) -> None:
        length = int(self.headers.get("Content-Length") or 0)
        body = self.rfile.read(length) if length else None
        headers = {k: v for k, v in self.headers.items() if k.lower() not in HOP}
        status, data, answer = 502, b"", [("Content-Type", "application/json")]
        try:
            conn = http.client.HTTPConnection(*self.upstream, timeout=30)
            conn.request(self.command, self.path, body=body, headers=headers)
            resp = conn.getresponse()
            data, status = resp.read(), resp.status
            answer = [(k, v) for k, v in resp.getheaders() if k.lower() not in HOP]
            conn.close()
        except OSError as exc:
            data = json.dumps({"proxy": "server antwoordt niet",
                               "fout": type(exc).__name__}).encode()
        parts = urlsplit(self.path)
        auth, token = shape(self.headers)
        line = {"t": datetime.now(UTC).isoformat(timespec="milliseconds"), "method": self.command,
                "path": parts.path,
                "query": {k: digest(v) for k, v in parse_qsl(parts.query, keep_blank_values=True)},
                "auth": auth, "token": token, "status": status}
        with LOCK, open(self.logfile, "a", encoding="utf-8") as fh:
            fh.write(json.dumps(line, ensure_ascii=False) + "\n")
        self.send_response(status)
        for key, value in answer:
            self.send_header(key, value)
        self.send_header("Content-Length", str(len(data)))
        self.send_header("Connection", "close")
        self.end_headers()
        if self.command != "HEAD":
            self.wfile.write(data)
        self.close_connection = True

    do_GET = do_POST = do_PUT = do_DELETE = do_HEAD = do_PATCH = _forward  # noqa: N815


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--upstream", required=True, help="host:poort van de echte server")
    parser.add_argument("--log", required=True, help="logbestand (JSON-regels)")
    parser.add_argument("--port", type=int, default=8096)
    args = parser.parse_args()
    host, _, port = args.upstream.partition(":")
    Handler.upstream, Handler.logfile = (host, int(port or 8096)), args.log
    open(args.log, "a", encoding="utf-8").close()
    server = ThreadingHTTPServer(("0.0.0.0", args.port), Handler)  # noqa: S104 - eigen testnetwerk
    server.daemon_threads = True
    print(f"logproxy: :{args.port} -> {args.upstream}, log {args.log}", flush=True)
    server.serve_forever()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
