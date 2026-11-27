"""Start het productie-webpakket tweemaal via elk AIO-startpad, op lokale proefappdata."""

import hashlib
import os
import shutil
import socket
import stat
import tempfile
import time
from contextlib import ExitStack
from pathlib import Path
from urllib.request import ProxyHandler, build_opener

from mediaguard import aio

MANIFESTEN = ("prerender-manifest.json", "server/preview-props.json",
              "server/server-reference-manifest.json", "server/server-reference-manifest.js")


def start_en_meet(bewaker, web, manifesten, uid):
    bewaker.start(web)
    proces = web.proc
    if proces is None:
        raise RuntimeError("Web is niet gestart")
    try:
        einde = time.monotonic() + 30
        lokaal = build_opener(ProxyHandler({}))
        while time.monotonic() < einde and proces.poll() is None:
            try:
                with lokaal.open(f"http://localhost:{web.env['PORT']}/login", timeout=2) as pagina:
                    if pagina.status == 200 and b"MediaGuard" in pagina.read():
                        for pad in manifesten:
                            info = pad.stat()
                            if info.st_uid != uid or stat.S_IMODE(info.st_mode) != 0o600:
                                raise RuntimeError("Onjuiste eigenaar of modus van een webmanifest")
                        return [hashlib.sha256(pad.read_bytes()).digest() for pad in manifesten]
            except OSError:
                pass
            time.sleep(0.1)
        raise RuntimeError("De aanmeldpagina kwam niet op")
    finally:
        if proces.poll() is None:
            bewaker._terminate(proces, grace=3)
            proces.wait(timeout=5)


def proef(basis):
    basis.chmod(0o711)
    bron = Path(__file__).resolve().parents[1] / ".next/standalone"
    pakket = basis / "app"
    shutil.copytree(bron, pakket, symlinks=True)
    config, data = basis / "config", basis / "data"
    config.mkdir(mode=0o700)
    data.mkdir()
    manifesten = [pakket / ".next" / naam for naam in MANIFESTEN]
    for pad in manifesten:
        if pad.readlink() != Path("/run/mediaguard-web/mediaguard-next") / pad.name:
            raise RuntimeError("Onverwacht manifestdoel in het productie-webpakket")
    with socket.socket() as backend:
        backend.bind(("localhost", 0))  # gereserveerd, zonder luisterende backend
        with socket.socket() as poort:
            poort.bind(("localhost", 0))
            webpoort = poort.getsockname()[1]
        eerste = None
        for aparte_bewaker in (False, True):
            # Elke gesimuleerde container heeft een lege runtime en dezelfde installatie.
            werk = basis / ("webbewaker" if aparte_bewaker else "vaste-uid")
            werk.mkdir()
            werk.chmod(0o1733)
            for pad in manifesten:
                pad.unlink()
                pad.symlink_to(werk / "mediaguard-next" / pad.name)
            env = {"MG_AIO_CONFIG": str(config), "MG_AIO_WORK": str(werk), "MG_AIO_DATA": str(data),
                   "MG_AIO_NODE": shutil.which("node"), "MG_AIO_WEB_DIR": str(pakket),
                   "MG_WEB_PORT": str(webpoort)}
            web = next(dienst for dienst in aio.build_specs(env) if dienst.name == "web")
            web.env.update(MG_WEB_HOST="localhost",
                           MG_CORE_URL=f"http://localhost:{backend.getsockname()[1]}")
            uid, gid = os.geteuid(), os.getegid()
            if uid == 0:
                uid, gid = aio.web_identity({}) if aparte_bewaker else (568, 568)
                web.user = (uid, gid)
            with ExitStack() as stack:
                launcher = None
                if aparte_bewaker:
                    demote = aio._demote(uid, gid, []) if os.geteuid() == 0 else lambda: None
                    launcher = aio.WebLauncher(web.start_command(), web.env, web.cwd, demote)
                    stack.callback(launcher.close)
                bewaker = aio.Supervisor([web], uid, gid, config_dir=config, web_launcher=launcher)
                for _ in range(2):
                    meting = start_en_meet(bewaker, web, manifesten, uid)
                    if eerste is not None and meting != eerste:
                        raise RuntimeError("De runtime-manifesten veranderden na herstart")
                    eerste = meting
    print("Vier keer HTTP 200 via beide AIO-startpaden. Alle vier manifesten bleven gelijk.")


if __name__ == "__main__":
    with tempfile.TemporaryDirectory() as tijdelijk:
        proef(Path(tijdelijk))
