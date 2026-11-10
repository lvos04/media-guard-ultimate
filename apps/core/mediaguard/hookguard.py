"""Hookwacht: houdt de noodstop vast terwijl core herstart (alles-in-één-container).

Probleem: Sonarr/Radarr vragen vóór elke grab aan core of het mag (pre-grab-hook). Antwoordt
core niet (herstart, update, migratie), dan past de fork zijn *fail policy* toe: ``open``
= toestaan. Dat is goed voor gewone downloads (een trage of herstartende core mag ze niet
blokkeren), maar tijdens een **noodstop** zou zo'n grab ongecontroleerd doorgaan.

Oplossing zonder de C#-forks opnieuw te bouwen: de forks praten met deze kleine wachter op
``127.0.0.1:38102`` in plaats van rechtstreeks met core.

- Normaal stuurt de wachter elk hook-verzoek ongewijzigd door naar core en geeft het antwoord
  van core terug (core beslist).
- Antwoordt core niet op tijd, met een 5xx-fout, of op een pre-grab met een andere fout
  (de fork ziet die als 'onbereikbaar' en staat de grab toe), dan kijkt de wachter naar de
  vastgelegde noodstop (``noodstop.json`` naast de database, door core bijgehouden; anders
  de database zelf, alleen-lezen). Staat de noodstop aan, dan weigert hij de grab zelf
  (``EMERGENCY_STOP``). Staat hij uit, dan antwoordt hij 503 en past de fork zijn eigen fail
  policy toe: normale downloads gaan dus gewoon door, net als zonder wachter.
- Een pre-import van een **upgrade** (``is_upgrade``: er staat al een bestand) geldt net zo:
  tijdens de noodstop stelt de wachter hem uit (``defer``), zoals core zelf doet. Het huidige
  bestand blijft dan staan. Een gewone import zonder bestaand bestand houdt de 503.
- De wachter antwoordt zelf nooit met 'toestaan'; alleen core kan iets goedkeuren.

Core-kant: :func:`write_flag` (bij elke wijziging van de noodstop) en een lus in
:func:`startup` die het bestand elke minuut ververst (ook na een teruggezette back-up).
Draaien: ``python -m mediaguard.hookguard`` (door ``mediaguard.aio`` gestart en bewaakt).

Deze module importeert bij het laden alleen de standaardbibliotheek: de wachter start snel en
hangt niet af van core.
"""

from __future__ import annotations

import contextlib
import http.client
import json
import os
import sqlite3
import sys
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from mediaguard.context import AppContext

FLAG_NAME = "noodstop.json"
DEFAULT_PORT = 38102
MAX_BODY = 4 * 1024 * 1024
REFRESH_SECONDS = 60
REJECT = {"decision": "reject", "reason_code": "EMERGENCY_STOP",
          "message": "Noodstop actief: geen nieuwe downloads (MediaGuard herstart, de noodstop "
                     "blijft gelden)",
          "score_adjustment": 0}
DEFER_UPGRADE = {"decision": "defer", "reason_code": "EMERGENCY_STOP",
                 "message": "Noodstop actief: upgrade wacht, het huidige bestand blijft staan"}


# -- core-kant -----------------------------------------------------------------------------
def flag_path(ctx: AppContext) -> Path:
    return Path(ctx.settings.data_dir) / FLAG_NAME


def write_flag(ctx: AppContext) -> bool:
    """Noodstopstatus naar ``noodstop.json`` (atomair). Nooit fataal."""
    state = ctx.state_get("emergency_stop", {"active": False}) or {}
    payload = {"active": bool(state.get("active")), "since": state.get("since"),
               "reason": state.get("reason") or "", "written_at": time.time()}
    path = flag_path(ctx)
    tmp = path.with_suffix(".tmp")
    try:
        tmp.write_text(json.dumps(payload), encoding="utf-8")
        os.replace(tmp, path)
    except OSError:
        with contextlib.suppress(OSError):
            tmp.unlink()
        return False
    return True


async def startup(ctx: AppContext) -> None:
    import asyncio

    if ctx.settings.role != "core":
        return
    write_flag(ctx)
    if not ctx.settings.start_background:
        return

    from mediaguard import taskwatch

    async def loop(ctx: AppContext) -> None:
        while True:
            await asyncio.sleep(REFRESH_SECONDS)
            try:
                written = await asyncio.to_thread(write_flag, ctx)
            except Exception as exc:  # noqa: BLE001 - de lus mag nooit stoppen
                taskwatch.fail(ctx, "hookguard", exc)
                continue
            if written:
                taskwatch.ok(ctx, "hookguard")
            else:
                taskwatch.fail(ctx, "hookguard", f"{FLAG_NAME} niet te schrijven")

    ctx.services["hookguard_task"] = taskwatch.spawn(
        ctx, "hookguard", loop, label_nl="Noodstop vastleggen voor de hookwacht",
        every=REFRESH_SECONDS)


async def shutdown(ctx: AppContext) -> None:
    task = ctx.services.pop("hookguard_task", None)
    if task is not None:
        task.cancel()


# -- wachter -------------------------------------------------------------------------------
def _flag_active(flag: Path | None) -> bool:
    if flag is None:
        return False
    try:
        data = json.loads(flag.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return False  # ontbreekt of half geschreven: de database beslist mee
    return isinstance(data, dict) and bool(data.get("active"))


def _db_active(db: Path | None) -> bool:
    if db is None or not db.exists():
        return False
    try:
        conn = sqlite3.connect(f"file:{db}?mode=ro", uri=True, timeout=0.5)
        try:
            row = conn.execute("SELECT value_json FROM system_state WHERE "
                               "key='emergency_stop'").fetchone()
        finally:
            conn.close()
        data = json.loads(row[0]) if row is not None else None
    except (sqlite3.Error, ValueError, TypeError):
        return False
    return isinstance(data, dict) and bool(data.get("active"))


def emergency_active(flag: Path | None, db: Path | None) -> bool:
    """Noodstop volgens het bestand van core óf de database (alleen-lezen). Bij twijfel
    (één van beide zegt 'aan') geldt de noodstop: alleen zolang core zelf niet antwoordt."""
    return _flag_active(flag) or _db_active(db)


class Guard:
    def __init__(self, core_host: str, core_port: int, *, timeout: float,
                 flag: Path | None, db: Path | None) -> None:
        self.core_host, self.core_port = core_host, core_port
        self.timeout, self.flag, self.db = timeout, flag, db

    def forward(self, method: str, path: str, headers: dict[str, str],
                body: bytes) -> tuple[int, bytes, str] | None:
        """Naar core; None als core niet (op tijd) antwoordt of met 5xx."""
        conn = http.client.HTTPConnection(self.core_host, self.core_port, timeout=self.timeout)
        try:
            conn.request(method, path, body=body, headers=headers)
            resp = conn.getresponse()
            data = resp.read()
            ctype = resp.getheader("Content-Type") or "application/json"
            status = resp.status
        except (OSError, http.client.HTTPException):
            return None
        finally:
            conn.close()
        if status >= 500:
            return None
        return status, data, ctype

    @staticmethod
    def _hook(path: str) -> str:
        return path.split("?", 1)[0].rstrip("/")

    @classmethod
    def is_pre_grab(cls, path: str) -> bool:
        return cls._hook(path) == "/api/v1/hooks/pre-grab"

    @classmethod
    def is_upgrade_import(cls, path: str, body: bytes = b"") -> bool:
        """Pre-import die een bestaand bibliotheekbestand zou vervangen."""
        if cls._hook(path) != "/api/v1/hooks/pre-import":
            return False
        try:
            data = json.loads(body or b"{}")
        except ValueError:
            return False
        return isinstance(data, dict) and bool(data.get("is_upgrade"))

    def guarded(self, path: str, body: bytes = b"") -> bool:
        """Hooks die tijdens een noodstop nooit via de fail policy door mogen."""
        return self.is_pre_grab(path) or self.is_upgrade_import(path, body)

    def passthrough(self, path: str, status: int, body: bytes = b"") -> bool:
        """Antwoord van core ongewijzigd doorgeven? Altijd bij 2xx, en bij andere hooks ook
        bij een 4xx. Een pre-grab of upgrade-import met een 4xx (bv. 401 kort na een
        herstart) zou de fork als 'onbereikbaar' zien en dus toestaan: dan eerst de noodstop
        bekijken."""
        return status < 300 or not self.guarded(path, body)

    def holds(self, path: str, body: bytes = b"") -> bool:
        return self.guarded(path, body) and emergency_active(self.flag, self.db)

    def fallback(self, path: str, body: bytes = b"") -> tuple[int, bytes]:
        if self.holds(path, body):
            return 200, json.dumps(REJECT if self.is_pre_grab(path) else DEFER_UPGRADE).encode()
        return 503, json.dumps({"detail": "MediaGuard core antwoordt niet; de fork past zijn "
                                          "eigen fail policy toe"}).encode()


def _handler(guard: Guard) -> type[BaseHTTPRequestHandler]:
    class Handler(BaseHTTPRequestHandler):
        protocol_version = "HTTP/1.1"
        server_version = "mediaguard-hookguard"

        def _send(self, status: int, body: bytes, ctype: str = "application/json") -> None:
            self.send_response(status)
            self.send_header("Content-Type", ctype)
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

        def do_GET(self) -> None:  # noqa: N802
            if self.path in ("/livez", "/health"):
                self._send(200, b'{"ok":true}')
            else:
                self._send(404, b'{"detail":"onbekend pad"}')

        def do_POST(self) -> None:  # noqa: N802
            if not self.path.startswith("/api/v1/hooks/"):
                self._send(404, b'{"detail":"alleen hooks"}')
                return
            try:
                length = int(self.headers.get("Content-Length") or 0)
            except ValueError:
                length = -1
            if length < 0 or length > MAX_BODY:
                self._send(413, b'{"detail":"verzoek te groot"}')
                return
            body = self.rfile.read(length) if length else b""
            headers = {k: v for k, v in self.headers.items()
                       if k.lower() in ("content-type", "x-mediaguard-hook-key",
                                        "authorization", "user-agent", "accept")}
            headers["Content-Length"] = str(len(body))
            result = guard.forward("POST", self.path, headers, body)
            # een fout van core op een pre-grab (of upgrade-import) tijdens een noodstop: de
            # wachter weigert of stelt zelf uit
            if result is not None and (guard.passthrough(self.path, result[0], body)
                                       or not guard.holds(self.path, body)):
                status, data, ctype = result
                self._send(status, data, ctype)
                return
            status, data = guard.fallback(self.path, body)
            self._send(status, data)

        def log_message(self, *_args: Any) -> None:
            return

    return Handler


def serve(env: dict[str, str] | None = None) -> ThreadingHTTPServer:
    env = dict(os.environ if env is None else env)
    port = int(env.get("MG_HOOKGUARD_PORT") or DEFAULT_PORT)
    core = env.get("MG_HOOKGUARD_CORE", "127.0.0.1:38100").removeprefix("http://").rstrip("/")
    host, _, core_port = core.partition(":")
    timeout_ms = int(env.get("MG_HOOKGUARD_TIMEOUT_MS") or 2500)
    flag = Path(env["MG_HOOKGUARD_FLAG"]) if env.get("MG_HOOKGUARD_FLAG") else None
    db = Path(env["MG_HOOKGUARD_DB"]) if env.get("MG_HOOKGUARD_DB") else None
    guard = Guard(host or "127.0.0.1", int(core_port or 38100), timeout=timeout_ms / 1000,
                  flag=flag, db=db)
    server = ThreadingHTTPServer((env.get("MG_HOOKGUARD_BIND", "127.0.0.1"), port),
                                 _handler(guard))
    server.daemon_threads = True
    return server


def main() -> int:  # pragma: no cover - procesingang (getest via serve())
    server = serve()
    print(f"hookwacht luistert op {server.server_address[0]}:{server.server_address[1]}",
          flush=True)
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        server.server_close()
    return 0


if __name__ == "__main__":  # pragma: no cover
    sys.exit(main())
