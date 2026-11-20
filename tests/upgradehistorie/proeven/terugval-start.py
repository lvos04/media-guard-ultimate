"""Klaarzetten vóór een afgebroken stop, vervolgens de echte startvoorcontrole van de basis."""

import os
import sqlite3
import subprocess
import sys
import tempfile
from contextlib import closing
from pathlib import Path

from mediaguard import aio, schema
from mediaguard import db as database
from mediaguard.maintenance import preupdate

werk = Path(os.environ["WERK"])
kandidaat = Path(os.environ["REPO"]) / "apps/core"
basis = Path(os.environ["HISTORIE"]) / "155/apps/core"
kandidaatschema = max(
    p.stem for p in (kandidaat / "mediaguard/migrations").glob("*.sql")
)


def stand(pad):
    with closing(
        sqlite3.connect(pad.resolve().as_uri() + "?mode=ro", uri=True)
    ) as conn:
        (taak,) = conn.execute("SELECT progress FROM jobs LIMIT 1").fetchone()
        (event,) = conn.execute("SELECT count(*) FROM outbox_events").fetchone()
        (versie,) = conn.execute(
            "SELECT max(version) FROM schema_migrations"
        ).fetchone()
    return taak, event, versie


def bevestig_weigering(pad, melding):
    assert "Schema 155 bevat bewijs met een onbekende weergave" in melding
    assert stand(pad)[:2] == (0.75, 2) and stand(pad)[2].startswith("155_")
    (pad.parent.parent / "geweigerd").touch()


def fase(naam, root):
    bron = kandidaat if naam == "nieuw" else basis
    assert Path(database.__file__).resolve().is_relative_to(bron)
    pad = root / "core/mediaguard.db"
    if naam == "oud":
        import upgradebewijs

        fixture = Path(os.environ["REPO"]) / "tests/fixtures/aio/core-1.2.2.sql"
        upgradebewijs.herstel(fixture, pad)
        with closing(database.Database(pad)) as db:
            db.initialize()
            db.execute("UPDATE jobs SET progress=0.75")
        assert stand(pad)[0:2] == (0.75, 2) and stand(pad)[2].startswith("155_")
        return 0
    if naam == "nieuw":
        env = {}
        try:
            aio.before_services(env, root, None, migrate_core=True)
        except RuntimeError as exc:
            bevestig_weigering(pad, str(exc))
            (archief,) = (
                p
                for p in (pad.parent / "backups" / preupdate.FOLDER).iterdir()
                if (
                    (preupdate._read_meta(p) or {}).get("from_schema") or ""
                ).startswith("155_")
            )
        else:
            archief = Path(env["MG_PRE_UPDATE_SET"])
            assert stand(pad)[2] == kandidaatschema
        assert not preupdate.verify(archief)
        assert stand(archief / "mediaguard.db")[0:2] == (0.75, 2)
        assert stand(archief / "mediaguard.db")[2].startswith("155_")
        preupdate.stage(pad.parent, archief, actor="terugvalproef")
        assert preupdate.pending(pad.parent)["set"] == archief.name
        return 0
    if naam == "controle":
        # Controle met het oude naamformaat bij dezelfde terugval: een door de basis zelf gemaakte set.
        with closing(database.Database(pad)) as db:
            db.initialize()
        made = preupdate.make_set(
            pad.parent / "backups" / preupdate.FOLDER, pad, encode_dir=None
        )
        preupdate.stage(pad.parent, made, actor="terugvalproef")
        # Laat de eindversie werkelijk upgraden, maar laat de stage-marker staan.
        kandidaatstart = subprocess.run(
            [
                sys.executable,
                "-c",
                (
                    "from mediaguard.db import Database; import sys; from pathlib import Path; "
                    "d=Database(Path(sys.argv[1])); d.initialize(); d.close()"
                ),
                str(pad),
            ],
            cwd=root,
            env=os.environ | {"PYTHONPATH": str(kandidaat)},
            check=False,
            capture_output=True,
            text=True,
        )
        if kandidaatstart.returncode:
            bevestig_weigering(pad, kandidaatstart.stderr)
    if naam not in ("controle", "start"):
        raise ValueError(naam)
    if (root / "geweigerd").exists():
        assert stand(pad)[2].startswith("155_")
    else:
        assert stand(pad)[2] == kandidaatschema
    gekozen = preupdate.pending(pad.parent)["set"]
    zichtbaar = (
        preupdate.find(pad.parent / "backups" / preupdate.FOLDER, gekozen) is not None
    )
    # De oude supervisor behandelt de klaargezette terugval voordat een schrijver start.
    env = {}
    from unittest.mock import patch

    with patch.object(aio, "prepare", return_value=[]):
        aio.prepare_start(env, root, None)
    if (root / "geweigerd").exists() and preupdate.pending(pad.parent):
        aio.before_services(env, root, None)
    schema_na = stand(pad)[2][:3]
    hersteld = schema_na == "155" and stand(pad)[0:2] == (0.75, 2)
    wacht = preupdate.pending(pad.parent) is not None
    print(
        f"Oude supervisor: set vindbaar={zichtbaar}, herstel toegepast={hersteld}, "
        f"schema={schema_na}, herstelstand={bool(env.get(schema.AIO_RECOVERY_ENV))}, markering aanwezig={wacht}.",
        flush=True,
    )
    return 0 if hersteld and zichtbaar and not wacht else 1


if len(sys.argv) == 4:
    raise SystemExit(fase(sys.argv[2], Path(sys.argv[3])))
with tempfile.TemporaryDirectory(dir=os.environ["TMPDIR"]) as tmp:
    root = Path(tmp)
    fasen = (
        ("oud", "controle") if sys.argv[1] == "controle" else ("oud", "nieuw", "start")
    )
    for stap in fasen:
        bron = kandidaat if stap == "nieuw" else basis
        code = subprocess.run(
            [sys.executable, __file__, "fase", stap, str(root)],
            check=False,
            cwd=root,
            env=os.environ | {"PYTHONPATH": f"{bron}:{os.environ['REPO']}/scripts"},
        ).returncode
        if code:
            raise SystemExit(code)
