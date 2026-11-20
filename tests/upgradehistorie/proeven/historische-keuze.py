"""Hervat een echt door de basiscommit geschreven terugzetjournaal via de beheerroute."""

import contextlib
import io
import json
import os
import sqlite3
import subprocess
import sys
from pathlib import Path
from types import SimpleNamespace

import test_herstartpunten as punten
from mediaguard.connector import backups
from mediaguard.maintenance import preupdate
from walgrenzen import lees

repo = Path(os.environ["REPO"])
werk = Path(os.environ["WERK"])
config = werk / "installatie"
core = config / "core"
enc = config / "encode/app.db"
assert Path(preupdate.__file__).resolve().is_relative_to(repo)


def percentages(main):
    return lees(main, "SELECT percent FROM job")[0][0]


def oud(punt):
    config.mkdir()
    env = punten.installatie.__wrapped__(config)
    if punt != "onvolledige-encoder":
        from av1_fixture import maak_av1_v1

        # Gebruik de echte eerste AV1-migratie als invoer voor de ongewijzigde oude schrijver.
        fixture = werk / "encoder-v1.db"
        with contextlib.closing(sqlite3.connect(fixture)) as db:
            maak_av1_v1(db, 75)
        fixture.replace(enc)
    info, _, _ = punten.herstelvraag(config, env)
    punten.onderbreek(
        config, "omwisselen", "stap-0" if punt == "onvolledige-encoder" else punt
    )
    assert preupdate.pending(core)["set"] == info["set"]
    return 0


def cli(args):
    with (
        contextlib.redirect_stdout(io.StringIO()) as stdout,
        contextlib.redirect_stderr(io.StringIO()) as stderr,
    ):
        code = preupdate.main(args)
    assert not stderr.getvalue(), stderr.getvalue()
    return code, stdout.getvalue()


def proef(punt):
    old = Path(os.environ["HISTORIE"]) / "voor-fasebestand"
    pp = [
        old / "apps/core",
        old / "apps/core/tests",
        old / "scripts",
        Path(__file__).parent,
        Path(os.environ["TESTBRON"]) / "apps/core/tests",
    ]
    env = dict(
        os.environ,
        REPO=str(old),
        PROEFBRON=str(old),
        MG_REPO=str(old),
        PYTHONPATH=os.pathsep.join(map(str, pp)),
    )
    subprocess.run(
        [sys.executable, "-B", __file__, "oud", punt],
        cwd=werk,
        env=env,
        check=True,
        timeout=90,
    )
    marker = core / preupdate.STAGE_MARKER
    info = json.loads(marker.read_text())
    assert "omwisseling" in info and not (core / preupdate.COMMIT_ORIGIN).exists()
    if punt == "stap-0":
        assert info["omwisseling"]["stap"] == 0
    before = punten.bestandsinhoud(config)
    automatic = 0
    for _ in range(2):
        try:
            punten.herstart(config)
        except RuntimeError as err:
            assert preupdate.MANUAL_CHOICE in str(err)
        else:
            automatic += 1
    assert automatic == 0 and punten.bestandsinhoud(config) == before
    policy = backups.rollback_policy(
        SimpleNamespace(settings=SimpleNamespace(data_dir=core))
    )
    assert policy.pre_update_restore_mode == preupdate.MANUAL_CHOICE
    args = ["zet-klaar", "--kernmap", str(core), "--set", info["set"]]
    if punt == "onvolledige-encoder":
        with contextlib.redirect_stderr(io.StringIO()) as stderr:
            assert preupdate.main(args) == 1
        assert "Voortgangstabellen ontbreken" in stderr.getvalue()
        assert policy.staged is None and punten.bestandsinhoud(config) == before
        print(
            "Historische wissel met onvolledige primaire AV1-database: 0/2 starts, "
            "beheerkeuze geweigerd, aanvraag en bestanden bewaard."
        )
        return 0
    code, preview = cli(args)
    assert (
        code == 1
        and preupdate.MANUAL_CHOICE in preview
        and "gekozen" in preview
        and "vorige" in preview
    )
    choice = preview.split()[-1]
    choices = {choice}
    assert percentages(enc) == 90 and punten.bestandsinhoud(config) == before
    for _ in range(2):
        code, reply = cli([*args, "--bevestig-stand", choice])
        if code == 0:
            break
        assert preupdate.MANUAL_CHOICE in reply
        choice = reply.split()[-1]
        choices.add(choice)
        assert punten.bestandsinhoud(config) == before
    done = code == 0
    if done:
        assert len(choices) == 1 and info["omwisseling"]["vorige"] in preview
        for _ in range(2):
            punten.herstart(config)
        result = preupdate.take_result(core)
        assert Path(result["previous"]).name == info["omwisseling"]["vorige"]
        assert result["ok"] and percentages(enc) == 75
        assert percentages(Path(result["previous"]) / "encode__app.db") == 90
    else:
        assert (
            not preupdate.take_result(core)
            and marker.is_file()
            and percentages(enc) == 90
        )
    print(
        f"{punt}: oude schrijver, 0/2 automatische starts; handmatig herstel "
        f"{'voltooid' if done else 'tweemaal geweigerd'}, aantal bevestigingswaarden bij "
        f"gelijke stand {len(choices)}, aanvraag {'afgehandeld' if done else 'bewaard'}, "
        f"actief {percentages(enc)}%."
    )
    return 0 if done else 1


if sys.argv[1] == "oud":
    raise SystemExit(oud(sys.argv[2]))
raise SystemExit(proef(sys.argv[1]))
