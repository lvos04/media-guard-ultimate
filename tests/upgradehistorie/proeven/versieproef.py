"""Start uitsluitend proefprocessen met de gekozen bron en de eigen gevulde installatie."""

import os
import sqlite3
import subprocess
import sys
from contextlib import closing
from pathlib import Path
from unittest.mock import patch

werk = Path(os.environ["WERK"])
repo = Path(os.environ["REPO"])
oud = Path(os.environ["HISTORIE"]) / "wissel"
config = werk / "installatie"
core = config / "core"
enc = config / "encode/app.db"
live = core / "mediaguard.db"
helper = Path(__file__).parent


def kind(bron, bestand, fase, codes=(0,)):
    env = dict(
        os.environ,
        REPO=str(bron),
        PROEFBRON=str(bron),
        MG_REPO=str(bron),
        PYTHONPATH=os.pathsep.join(
            map(str, (bron / "apps/core", bron / "scripts", helper))
        ),
    )
    proc = subprocess.run(
        [sys.executable, "-B", str(bestand), fase],
        check=False,
        cwd=werk,
        env=env,
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        text=True,
        timeout=75,
    )
    if proc.returncode not in codes:
        raise AssertionError(
            f"{fase}: exitcode {proc.returncode}; " + proc.stdout[-1400:]
        )
    return proc.returncode


def lees(pad, sql, *, immutable=False):
    assert pad.is_file()
    uri = pad.as_uri() + "?mode=ro" + ("&immutable=1" if immutable else "")
    with closing(sqlite3.connect(uri, uri=True)) as db:
        return db.execute(sql).fetchall()


def vrijgave():
    from mediaguard import aio, schema
    from mediaguard.maintenance import preupdate

    assert Path(aio.__file__).resolve().is_relative_to(repo)
    env = {
        "MG_AIO_CONFIG": str(config),
        "MG_AIO_WORK": str(werk / "work"),
        "MG_AIO_DATA": str(werk / "data"),
    }
    if (config / "motor").is_dir():
        env["MG_AIO_ENCODER_DIR"] = str(config / "motor")
    with patch.object(aio, "prepare", return_value=[]):
        meldingen = aio.prepare_start(env, config, None)
    if env.get("MG_AIO_SCHEMA_CHECK_FAILED") or env.get(schema.AIO_RECOVERY_ENV):
        if not meldingen:
            raise AssertionError("weigering zonder diagnose")
        return 73
    try:
        aio.before_services(env, config, None, migrate_core=True)
    except RuntimeError as exc:
        if not any(
            s in str(exc)
            for s in (
                "onderbroken update",
                "onbewezen",
                "terugzetten niet gedaan",
                "niet gestart",
                "terugzetmarkering",
                "deels vastgelegd",
            )
        ):
            raise
        return 73
    assert not env.get(schema.AIO_RECOVERY_ENV)
    assert not (core / preupdate.STAGE_MARKER).exists()
    return 0
