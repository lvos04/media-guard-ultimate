"""Websleutels bij een update met gevulde appdata en bij beide startwijzen van de AIO."""

import base64
import contextlib
import hashlib
import multiprocessing
import os
import sqlite3
import stat
import sys
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

import pytest

from mediaguard import aio, aio_access, bootstrap, event_keys


def test_websleutel_bij_update_en_gelijktijdige_start(tmp_path, monkeypatch):
    monkeypatch.setenv("MG_UID", str(os.geteuid()))
    monkeypatch.setenv("MG_GID", str(os.getegid()))
    monkeypatch.setattr(bootstrap, "main", lambda: 0)
    for mapnaam in ("SECRETS", "APPDATA", "WORK", "DATA"):
        monkeypatch.setenv("MG_BOOTSTRAP_" + mapnaam, str(tmp_path))
    bestanden = []
    for mapnaam, tabel, waarde in (("core", "kijkgeschiedenis", 3), ("encode", "voortgang", 42)):
        db = tmp_path / mapnaam / "app.db"
        db.parent.mkdir()
        with contextlib.closing(sqlite3.connect(db)) as conn:
            conn.execute(f"CREATE TABLE {tabel}(waarde INTEGER)")
            conn.execute(f"INSERT INTO {tabel} VALUES (?)", (waarde,))
            conn.commit()
        bestanden.append((db, db.read_bytes()))
    env = {"MG_AIO_CONFIG": str(tmp_path)}
    with ThreadPoolExecutor(max_workers=2) as pool:
        list(pool.map(lambda _: aio.prepare(env), range(2)))
    sleutel = tmp_path / "secrets/web/server-actions.key"
    assert stat.S_IMODE(sleutel.stat().st_mode) == 0o600
    assert len(sleutel.read_bytes()) == 32
    # De bewaker heeft na de voorbereiding alleen zoekrecht op de bovenliggende geheimenmap nodig.
    sleutel.parent.parent.chmod(0o500)
    try:
        eerste = aio.web_key(tmp_path, False)
        herstart = aio.web_key(tmp_path, False)
        herstel = aio.web_key(tmp_path, True)
    finally:
        sleutel.parent.parent.chmod(0o700)
    assert all(k == eerste for k in (herstart, herstel))
    assert base64.b64decode(eerste, validate=True) == sleutel.read_bytes()
    nieuw = tmp_path / "nieuw"
    aio.prepare({"MG_AIO_CONFIG": str(nieuw)})
    assert eerste != aio.web_key(nieuw, False)
    assert all(db.read_bytes() == inhoud for db, inhoud in bestanden)


def test_herstelweergave_zonder_websleutel_schrijft_niets(tmp_path):
    assert len(base64.b64decode(aio.web_key(tmp_path, True), validate=True)) == 32
    assert list(tmp_path.iterdir()) == []


def test_bestaand_ongeldig_sleutelbestand_wordt_niet_overschreven(tmp_path):
    sleutel = tmp_path / "secrets/web/server-actions.key"
    sleutel.parent.mkdir(mode=0o700, parents=True)
    sleutel.touch(mode=0o600)
    with pytest.raises(ValueError):
        aio.web_key(tmp_path, False)
    assert len(base64.b64decode(aio.web_key(tmp_path, True), validate=True)) == 32
    assert sleutel.read_bytes() == b""


@pytest.mark.parametrize("aparte_bewaker", [False, True])
def test_webproces_krijgt_de_bewaarde_sleutel_zonder_logwaarde(tmp_path, aparte_bewaker, capfd):
    event_keys.ensure_key(tmp_path / aio.WEB_KEY_FILE)
    web = aio.Spec("web", [sys.executable, "-c",
        "import base64, hashlib, os, pathlib; "
        "key = base64.b64decode(os.environ['NEXT_SERVER_ACTIONS_ENCRYPTION_KEY'], validate=True); "
        "pathlib.Path('ontvangen').write_bytes(hashlib.sha256(key).digest())"],
        {"PYTHONPATH": str(Path(aio.__file__).resolve().parents[1]),
         "PYTHONDONTWRITEBYTECODE": "1"},
        cwd=str(tmp_path))
    with contextlib.ExitStack() as cleanup:
        launcher = None
        if aparte_bewaker:
            launcher = aio.WebLauncher(web.argv, web.env, web.cwd, lambda: None)
            cleanup.callback(launcher.close)
        sup = aio.Supervisor([web], None, None, config_dir=tmp_path, web_launcher=launcher)
        key = (tmp_path / aio.WEB_KEY_FILE).read_bytes()
        for _ in range(2):
            sup.start(web)
            assert web.proc is not None and web.proc.wait(timeout=5) == 0
            ontvangen = tmp_path / "ontvangen"
            assert ontvangen.read_bytes() == hashlib.sha256(key).digest()
            ontvangen.unlink()
    assert all(base64.b64encode(key).decode() not in log for log in capfd.readouterr())


def test_alleen_web_mag_de_runtime_manifesten_openen(tmp_path, monkeypatch):
    # Een lokale containermap met gewone proeftekst. Alleen het opsommen van / is
    # omgeleid. De kernel beslist over iedere open(), ook bij dezelfde eigenaar.
    run = tmp_path / "run"
    gedeeld = tmp_path / "tmp"
    runtime = run / "mediaguard-web" / "mediaguard-next"
    runtime.mkdir(mode=0o700, parents=True)
    gedeeld.mkdir()
    status = run / "mediaguard" / "status"
    status.parent.mkdir()
    status.touch()
    manifesten = [runtime / naam for naam in (
        "prerender-manifest.json", "preview-props.json",
        "server-reference-manifest.json", "server-reference-manifest.js",
    )]
    for bestand in manifesten:
        bestand.touch(mode=0o600)
    iterdir = Path.iterdir
    monkeypatch.setattr(Path, "iterdir",
                        lambda p: iter((run, gedeeld)) if p == Path("/") else iterdir(p))
    env = {f"MG_AIO_{naam.upper()}": str(tmp_path / naam) for naam in ("config", "work", "data")}
    for pad in env.values():
        Path(pad).mkdir()
    diensten = [s for s in aio.build_specs(env) if s.restricted_roots]
    assert {s.name for s in diensten} == {
        "web", "encode", "hookguard", *(naam for naam, _, _ in aio.FORKS),
    }

    def open_proef(spec):
        aio_access.restrict(*spec.restricted_roots, service=spec.name)
        (gedeeld / spec.name).touch()
        with status.open("rb"):
            pass
        for bestand in manifesten:
            if spec.name == "web":
                with bestand.open("rb+"):
                    pass
            else:
                with pytest.raises(PermissionError), bestand.open("rb"):
                    pass

    for spec in diensten:
        kind = multiprocessing.get_context("fork").Process(target=open_proef, args=(spec,))
        kind.start()
        kind.join(5)
        if kind.is_alive():
            kind.kill()
            kind.join()
        assert kind.exitcode == 0, spec.name
