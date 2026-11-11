"""Peil-review mediaservers: twee nep-mediaservers op 127.0.0.1 (alleen de standaardbibliotheek).

- poort 18931: gedraagt zich als Emby (paden onder /emby, sleutel in X-Emby-Token, geen ProductName);
- poort 18932: gedraagt zich als Jellyfin 12.x (geen /emby-voorvoegsel, sleutel alleen in
  ``Authorization: MediaBrowser … Token="…"``).

Ze beantwoorden precies wat "Verbinding testen" van de core vraagt (/System/Info/Public, /System/Info,
/Library/VirtualFolders, /Sessions) en niets anders. Geen echte Emby of Jellyfin: alles wat hiermee is gemeten
is Sim-getest. Alleen-lezen hulpscript bij docs/ontwerp/PEIL_REVIEW.md; hoort niet bij de app.
"""
import json
import re
import sys
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

KEY = "nep-sleutel-peil-mediaserver"
SERVERS = {
    18931: {"kind": "emby", "public": {"ServerName": "Testserver Emby", "Version": "4.10.2.0", "Id": "nep-emby-0001"}},
    18932: {"kind": "jellyfin", "public": {"ServerName": "Testserver Jelly Leon", "Version": "12.4.0",
                                           "ProductName": "Jellyfin Server", "Id": "nep-jellyfin-0001"}},
}


def handler(conf):
    class H(BaseHTTPRequestHandler):
        def log_message(self, *_):  # stil
            return

        def _send(self, status, body=None):
            data = json.dumps(body if body is not None else {}).encode()
            self.send_response(status)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(data)))
            self.end_headers()
            self.wfile.write(data)

        def _authed(self):
            if conf["kind"] == "emby":
                return self.headers.get("X-Emby-Token") == KEY
            m = re.search(r'Token="([^"]*)"', self.headers.get("Authorization") or "")
            return bool(m) and m.group(1) == KEY

        def do_GET(self):
            path = self.path.split("?", 1)[0]
            if conf["kind"] == "emby" and path.startswith("/emby"):
                path = path[5:]
            elif conf["kind"] == "jellyfin" and path.startswith("/emby"):
                return self._send(404, {"error": "not found"})
            if path == "/System/Info/Public":
                return self._send(200, conf["public"])
            if not self._authed():
                return self._send(401, {"error": "unauthorized"})
            if path == "/System/Info":
                return self._send(200, conf["public"])
            if path == "/Library/VirtualFolders":
                return self._send(200, [{"Name": "Films", "Locations": ["/media/movies"]},
                                        {"Name": "Series", "Locations": ["/media/tv"]}])
            if path == "/Sessions":
                return self._send(200, [])
            return self._send(404, {"error": "not found"})

    return H


def main():
    threads = []
    for port, conf in SERVERS.items():
        srv = ThreadingHTTPServer(("127.0.0.1", port), handler(conf))
        t = threading.Thread(target=srv.serve_forever, daemon=True)
        t.start()
        threads.append(t)
        print(f"nep-{conf['kind']} op 127.0.0.1:{port}", flush=True)
    try:
        threading.Event().wait()
    except KeyboardInterrupt:
        sys.exit(0)


if __name__ == "__main__":
    main()
