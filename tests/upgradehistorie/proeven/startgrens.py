"""Controleer het herstel van een gevulde installatie bij uitval vóór de encodercommit."""

import os
import sqlite3
import subprocess
import sys
from contextlib import closing
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

from mediaguard import aio, db
from mediaguard.maintenance import preupdate
from upgradebewijs import herstel, vul

repo = Path(os.environ["REPO"])
werk = Path(os.environ["WERK"])
config = werk / "installatie"
core = config / "core"
kernpad = core / "mediaguard.db"
encdir = config / "encode"
encpad = encdir / "app.db"
sys.path.insert(0, str(repo / "services/encoder"))
from app.db import Database as Encoder

assert Path(db.__file__).resolve().is_relative_to(repo)
inst = SimpleNamespace(
    config_root=encdir, database_path=encpad, backup_root=werk / "av1-kopie"
)
scripts = repo / "services/encoder/app/migrations"
prior = werk / "vorig"
commitnaam = "update-in-uitvoering"
nieuw = hasattr(preupdate, "record_commit")


def init_testdata():
    herstel(repo / "tests/fixtures/aio/core-1.2.2.sql", kernpad)
    with closing(db.Database(kernpad)) as kern:
        kern.initialize()
    prior.mkdir()
    for pad in sorted(scripts.glob("*.sql"))[:-1]:
        (prior / pad.name).symlink_to(pad)
    Encoder(inst, migrations_dir=prior).initialize()
    vul(encpad, "encode")
    with closing(sqlite3.connect(encpad)) as enc, enc:
        assert enc.execute("UPDATE job SET percent=75").rowcount >= 1
    assert preupdate.inspect_schema(config, encoder_migrations=scripts) == (None, True)


def stand():
    with closing(sqlite3.connect(kernpad.as_uri() + "?mode=ro", uri=True)) as kern:
        kernrijen = [
            kern.execute(sql).fetchall()
            for sql in (
                "SELECT id,role FROM users ORDER BY id",
                "SELECT id,status,progress FROM jobs ORDER BY id",
                "SELECT id,topic FROM outbox_events ORDER BY id",
            )
        ]
    with closing(sqlite3.connect(encpad.as_uri() + "?mode=ro", uri=True)) as enc:
        werkrijen = enc.execute(
            "SELECT id,status,percent FROM job ORDER BY id"
        ).fetchall()
        schema = enc.execute("SELECT max(versie) FROM schema_versie").fetchone()[0]
    return kernrijen, werkrijen, schema


def open_diensten():
    env = {}
    aio.before_services(env, config, None, migrate_core=True)
    Encoder(inst).initialize()
    return env


def afgebroken_kind():
    # Stop alleen dit kind, na de echte exclusieve creatie en vóór de eerste write.
    if nieuw:
        oud_open = Path.open

        def open_markering(pad, mode="r", *args, **kwargs):
            handle = oud_open(pad, mode, *args, **kwargs)
            if pad == core / commitnaam and mode == "x":
                os._exit(82)
            return handle

        with patch.object(Path, "open", open_markering):
            open_diensten()
    else:
        # De oudercommit commit de encodermigratie in de encoderdienst, ná de supervisorset.
        origineel = Encoder._backup_before_migration

        def voor_commit(encoder):
            origineel(encoder)
            os._exit(82)

        with patch.object(Encoder, "_backup_before_migration", voor_commit):
            open_diensten()
    raise AssertionError("De beoogde onderbreking is niet bereikt.")


def probeer_start():
    try:
        open_diensten()
    except RuntimeError as exc:
        assert "herstelset van de onderbroken update ontbreekt" in str(exc), str(exc)
        return False
    return True


def controleer_start():
    init_testdata()
    begin = stand()
    assert all(begin[0]) and len(begin[0][-1]) == 2
    assert begin[1] and all(r[-1] == 75 for r in begin[1])
    assert (
        subprocess.run([sys.executable, "-B", __file__, "stop"], check=False).returncode
        == 82
    )
    assert stand() == begin
    marker = core / commitnaam
    if nieuw:
        assert marker.is_file() and marker.stat().st_size == 0
    else:
        assert not marker.exists()
    opties = [
        p
        for p in (core / "backups" / preupdate.FOLDER).iterdir()
        if p.is_dir()
        and preupdate._read_meta(p)
        and {f["live"] for f in preupdate._read_meta(p)["files"]}
        == {"core/mediaguard.db", "encode/app.db"}
    ]
    assert opties
    setmap = max(opties, key=lambda p: p.stat().st_mtime_ns)
    assert not preupdate.verify(setmap)
    starts = [probeer_start() for _ in range(2)]
    assert stand()[:2] == begin[:2]
    info = preupdate.stage(core, setmap, actor="lokale herstelproef")
    assert info["set"] == setmap.name
    na_herstel = probeer_start()
    resultaat = preupdate.take_result(core)
    assert resultaat and resultaat["ok"] and resultaat["set"] == setmap.name
    assert stand()[:2] == begin[:2]
    if nieuw and not na_herstel:
        assert marker.is_file() and marker.stat().st_size == 0
        assert stand() == begin
    detail = "0 bytes" if marker.is_file() and marker.stat().st_size == 0 else "afwezig"
    print(
        f"Markering {detail}; {sum(starts)}/2 herstarts vrijgegeven; geverifieerde set omgewisseld; "
        f"daarna {int(na_herstel)}/1 start vrijgegeven; encodertaken op 75% en 2/2 events behouden."
    )
    return int(not (all(starts) and na_herstel))


def echte_encoder():
    init_testdata()
    begin = stand()
    open_diensten()
    env = {
        "MG_AIO_CONFIG": str(config),
        "MG_AIO_WORK": str(werk / "work"),
        "MG_AIO_DATA": str(werk / "data"),
    }
    spec = next(s for s in aio.build_specs(env) if s.name == "encode")
    code = """
import sys
from pathlib import Path
from types import SimpleNamespace
repo, config, bew = map(Path, sys.argv[1:4])
sys.path[:0] = [str(repo / 'services/encoder'), str(repo / 'apps/core')]
from app.db import Database
enc = config / 'encode'
d = Database(SimpleNamespace(config_root=enc, database_path=enc / 'app.db', backup_root=bew))
d.initialize()
assert not d.read_only and d.fetch_one('SELECT min(percent) FROM job')[0] == 75
assert d.execute('UPDATE job SET percent=90') > 0
assert d.fetch_one('SELECT min(percent) FROM job')[0] == 90
"""
    spec.argv = [
        sys.executable,
        "-B",
        "-c",
        code,
        str(repo),
        str(config),
        str(inst.backup_root),
    ]
    with aio.live_core_reader(core):
        subprocess.run(spec.start_command(), cwd=repo, check=True)
    na = stand()
    assert na[0] == begin[0] and all(row[-1] == 90 for row in na[1])
    print(
        "De afgeschermde encoderdienst startte op het gemigreerde schema en schreef 75% naar 90%; de kern behield 2/2 events."
    )
    return 0


if __name__ == "__main__":
    if sys.argv[1:] == ["stop"]:
        afgebroken_kind()
    elif sys.argv[1:] == ["encoderdienst"]:
        raise SystemExit(echte_encoder())
    else:
        raise SystemExit(controleer_start())
