"""Start en signaleer uitsluitend de vaste webdienst onder haar eigen UID."""

from __future__ import annotations

import contextlib
import os
import re
import signal
import socket
import subprocess
import sys
import threading
import time
from collections.abc import Callable


class WebLauncher:
    def __init__(self, argv: list[str], env: dict[str, str], cwd: str,
                 demote: Callable[[], None]) -> None:
        self.channel, child = socket.socketpair(type=socket.SOCK_SEQPACKET)
        self.channel.settimeout(2)
        self.lock = threading.Lock()
        try:
            with child:
                self.worker = subprocess.Popen(  # noqa: S603 - vast programma vanuit de image
                    [sys.executable, "-m", "mediaguard.aio_web", str(child.fileno()), *argv],
                    env=env, cwd=cwd, pass_fds=(child.fileno(),), preexec_fn=demote,
                    stdout=subprocess.PIPE, stderr=subprocess.STDOUT)
        except BaseException:
            self.channel.close()
            raise

    def request(self, command: str) -> int | None:
        with self.lock:
            try:
                self.channel.sendall(command.encode("ascii"))
                result = self.channel.recv(64)
            except OSError:
                self.channel.close()
                raise
        if result == b"-":
            return None
        if not result or result == b"error":
            raise OSError("webbewaker kon het verzoek niet uitvoeren")
        try:
            return int(result)
        except ValueError as exc:
            raise OSError("ongeldig antwoord van de webbewaker") from exc

    def start(self, key: str = "") -> WebProcess:
        pid = self.request(f"start {key}" if key else "start")
        if pid is None or pid <= 1:
            raise OSError("webbewaker gaf geen proces-ID")
        return WebProcess(self, pid)

    def close(self) -> None:
        self.channel.close()
        with contextlib.suppress(subprocess.TimeoutExpired):
            self.worker.wait(timeout=4)


class WebProcess:
    def __init__(self, launcher: WebLauncher, pid: int) -> None:
        self.launcher, self.pid = launcher, pid

    def poll(self) -> int | None:
        try:
            return self.launcher.request(f"poll {self.pid}")
        except OSError:
            return 1

    def wait(self, timeout: float) -> int:
        deadline = time.monotonic() + timeout
        while (code := self.poll()) is None:
            if time.monotonic() >= deadline:
                raise subprocess.TimeoutExpired("web", timeout)
            time.sleep(0.05)
        return code

    def signal_group(self, sig: signal.Signals) -> None:
        self.launcher.request(f"signal {self.pid} {int(sig)}")


def run(channel: socket.socket, argv: list[str]) -> None:
    proc: subprocess.Popen[bytes] | None = None
    allowed_signals = {str(int(sig)).encode("ascii"): sig
                       for sig in (signal.SIGTERM, signal.SIGKILL, signal.SIGCONT)}
    try:
        while request := channel.recv(65):
            words = request.split()
            if not words or len(request) > 64 or not request.isascii():
                channel.sendall(b"error")
                continue
            command, *args = words
            code = proc.poll() if proc is not None else 1
            pid = str(proc.pid).encode("ascii") if proc is not None else None
            if command == b"start" and code is not None and (
                not args or (len(args) == 1 and re.fullmatch(rb"[A-Za-z0-9+/]{43}=", args[0]))
            ):
                env = os.environ.copy()
                if args:
                    env["NEXT_SERVER_ACTIONS_ENCRYPTION_KEY"] = args[0].decode("ascii")
                try:
                    proc = subprocess.Popen(argv, env=env, start_new_session=True)  # noqa: S603
                except OSError as exc:
                    print(f"web kan niet starten: {exc}", flush=True)
                    channel.sendall(b"error")
                else:
                    channel.sendall(str(proc.pid).encode("ascii"))
            elif command == b"poll" and len(args) == 1:
                current = code if args[0] == pid else 1
                channel.sendall(b"-" if current is None else str(current).encode("ascii"))
            elif (command == b"signal" and len(args) == 2 and proc is not None
                  and args[0] == pid and args[1] in allowed_signals):
                if code is None:
                    with contextlib.suppress(ProcessLookupError):
                        os.killpg(proc.pid, allowed_signals[args[1]])
                channel.sendall(b"-")
            else:
                channel.sendall(b"error")
    finally:
        if proc is not None and proc.poll() is None:
            with contextlib.suppress(ProcessLookupError):
                os.killpg(proc.pid, signal.SIGTERM)
            try:
                proc.wait(timeout=2)
            except subprocess.TimeoutExpired:
                with contextlib.suppress(ProcessLookupError):
                    os.killpg(proc.pid, signal.SIGKILL)
                proc.wait()


if __name__ == "__main__":
    with socket.socket(fileno=int(sys.argv[1])) as control:
        os.set_inheritable(control.fileno(), False)
        run(control, sys.argv[2:])
