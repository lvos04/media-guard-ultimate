"""Controleer bij ieder onderbrekingspunt twee verse supervisorprocessen met een kapotte database."""

import hashlib
import os
import shutil
import sqlite3
import subprocess
import sys
import tempfile
from contextlib import closing
from pathlib import Path
from unittest.mock import patch

import test_herstartpunten as punten
from mediaguard import aio, schema
from mediaguard.maintenance import preupdate


def startpoging(config):
    env = {
        "MG_AIO_ENCODER_DIR": str(config / "motor"),
        "MG_AIO_CONFIG": str(config),
        "MG_AIO_WORK": str(config / "werk"),
        "MG_AIO_DATA": str(config / "data"),
    }
    with patch.object(aio, "prepare", return_value=[]):
        regels = aio.prepare_start(env, config, None)
    if env.get("MG_AIO_SCHEMA_CHECK_FAILED") or env.get(schema.AIO_RECOVERY_ENV):
        assert regels
        return 73
    try:
        aio.before_services(env, config, None, migrate_core=True)
    except RuntimeError as exc:
        if not any(
            s in str(exc)
            for s in ("terugzetten niet gedaan", "niet gestart", "terugzetten mislukt")
        ):
            raise
        return 73
    return 0


def duurzame_bestanden(config):
    # SQLite gebruikt SHM als veranderlijk lezersregister. Alle database-, WAL-,
    # rollbackjournaal-, archief- en aanvraagbestanden tellen zelf mee, inclusief hun namen.
    return {
        p.relative_to(config): hashlib.sha256(p.read_bytes()).digest()
        for p in config.rglob("*")
        if p.is_file() and not p.name.endswith("-shm")
    }


def reeks(functie):
    return next(
        m.args[1]
        for m in functie.pytestmark
        if m.name == "parametrize" and m.args[0] == "punt"
    )


def meet(config, actie, punt):
    env = punten.installatie.__wrapped__(config)
    kern = config / "core/mediaguard.db"
    encoder = config / "encode/app.db"
    if actie == "update":
        shutil.copyfile(encoder, encoder.with_name("tweede.db"))
        slachtoffer = kern
    else:
        info, _, _ = punten.herstelvraag(config, env)
        if punt.startswith("klaar-") or punt == "aanvraag":
            assert preupdate.cancel(kern.parent)
            (config / "setnaam").write_text(info["set"])
            actie = "klaarzetten"
        punten.late_wal(kern)
        encoder.with_name(encoder.name + "-journal").touch()
        slachtoffer = encoder
    punten.onderbreek(config, actie, punt)
    if actie != "update" and (kern.parent / preupdate.STAGE_MARKER).exists():
        staged = encoder.with_name(encoder.name + preupdate.STAGED_SUFFIX)
        if staged.is_file():
            slachtoffer = staged
    assert slachtoffer.is_file()
    with slachtoffer.open("r+b") as handle:
        assert handle.read(16) == b"SQLite format 3\x00"
        handle.seek(0)
        handle.write(bytes(16))
    try:
        with closing(
            sqlite3.connect(slachtoffer.as_uri() + "?mode=ro&immutable=1", uri=True)
        ) as db:
            db.execute("PRAGMA integrity_check").fetchone()
    except sqlite3.DatabaseError:
        pass
    else:
        raise AssertionError("schade niet onafhankelijk vastgesteld")
    voor = duurzame_bestanden(config)
    geblokkeerd = 0
    bewaard = 0
    for _ in range(2):
        p = subprocess.run(
            [sys.executable, "-B", __file__, str(config)],
            check=False,
            cwd=config,
            capture_output=True,
            text=True,
            timeout=60,
        )
        assert p.returncode in (0, 73), p.stderr[-600:]
        geblokkeerd += p.returncode == 73
        bewaard += duurzame_bestanden(config) == voor
    return geblokkeerd, bewaard


def alle():
    uitkomsten = []
    regels = ["bewerking\tonderbrekingspunt\tpogingen\tgeweigerd\tbestanden_bytegelijk"]
    for actie, functie in (
        ("update", punten.test_uitval_bij_elke_updatecommit),
        ("omwisselen", punten.test_uitval_bij_elke_terugzetstap),
    ):
        for punt in reeks(functie):
            with tempfile.TemporaryDirectory(dir=os.environ["WERK"]) as mapnaam:
                blok, behoud = meet(Path(mapnaam), actie, punt)
            print(
                f"{actie} / {punt}: {blok}/2 geweigerd, {behoud}/2 bytegelijk",
                flush=True,
            )
            uitkomsten.append((blok, behoud))
            regels.append(f"{actie}\t{punt}\t2\t{blok}\t{behoud}")
    assert len(uitkomsten) == 32
    (Path(os.environ["WERK"]) / "matrix.tsv").write_text("\n".join(regels) + "\n")
    blok, behoud = map(sum, zip(*uitkomsten))
    print(
        f"32 onderbrekingspunten met aantoonbare databaseschade: {blok}/64 starts geweigerd; "
        f"bij {behoud}/64 bleven de gemeten bestanden bytegelijk.",
        flush=True,
    )
    return int(blok != 64 or behoud != 64)


if len(sys.argv) == 2:
    raise SystemExit(startpoging(Path(sys.argv[1])))
raise SystemExit(alle())
