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


def fase(naam, root):
    bron = kandidaat if naam in ("nieuw", "herupgrade", "herupgrade-schade") else basis
    assert Path(database.__file__).resolve().is_relative_to(bron)
    pad = root / "core/mediaguard.db"
    if naam in ("herupgrade", "herupgrade-schade"):
        import hashlib
        import json

        def bestanden():
            return {
                p.relative_to(root): hashlib.sha256(p.read_bytes()).digest()
                for p in root.rglob("*")
                if p.is_file() and not p.name.endswith("-shm")
            }

        if naam == "herupgrade-schade":
            resultaat = json.loads((pad.parent / preupdate.RESULT_FILE).read_text())
            met_wijziging = Path(resultaat["previous"]) / "encode__app.db"
            with closing(sqlite3.connect(met_wijziging)) as db, db:
                assert db.execute("UPDATE job SET percent=50").rowcount == 1
            voor = bestanden()
            for _ in range(2):
                try:
                    aio.before_services({}, root, None, migrate_core=True)
                except RuntimeError as exc:
                    assert "vorige database" in str(exc)
                else:
                    raise AssertionError(
                        "de gewijzigde vorige stand liet een update toe"
                    )
                assert bestanden() == voor
            return 0
        for _ in range(2):
            aio.before_services({}, root, None, migrate_core=True)
            assert stand(pad) == (0.75, 2, kandidaatschema)
            with closing(sqlite3.connect(root / "encode/app.db")) as db:
                assert db.execute("SELECT percent FROM job").fetchall() == [(75,)]
        return 0
    if naam == "oud":
        # Deze installatie is op 155 aangemaakt. De historische weergave van elk
        # proefbewijs is eenduidig, dus de afzonderlijke 157-controle blijft actief.
        with closing(database.Database(pad)) as db:
            db.initialize()
            db.execute(
                "INSERT INTO jobs(job_type,status,progress,created_at) "
                "VALUES ('herstelproef','running',0.75,?)",
                (database.utcnow(),),
            )
            db.execute(
                "INSERT INTO audit_events(ts,actor,action) VALUES (?,?,?)",
                (database.utcnow(), "fixture", "job.retry"),
            )
            for topic in ("library.synced", "recovery.committed"):
                db.execute(
                    "INSERT INTO outbox_events(ts,topic,payload) VALUES (?,?,?)",
                    (database.utcnow(), topic, "{}"),
                )
            import hashlib

            before = db.fetch_one("SELECT cursor_key FROM outbox_retention")[0]
            assert isinstance(before, bytes) and len(before) == 32
            (root / "digest").write_bytes(hashlib.sha256(before).digest())
        sys.path.insert(0, str(Path(__file__).resolve().parents[3] / "apps/core/tests"))
        from av1_fixture import maak_av1_v1

        encoder = root / "encode/app.db"
        encoder.parent.mkdir(exist_ok=True)
        with closing(sqlite3.connect(encoder)) as engine, engine:
            maak_av1_v1(engine, 75)
        assert stand(pad)[0:2] == (0.75, 2) and stand(pad)[2].startswith("155_")
        return 0
    if naam == "nieuw":
        env = {}
        aio.before_services(env, root, None, migrate_core=True)
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
        subprocess.run(
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
            check=True,
        )
    if naam not in ("controle", "start"):
        raise ValueError(naam)
    assert stand(pad)[2] == kandidaatschema
    gekozen = preupdate.pending(pad.parent)["set"]
    zichtbaar = (
        preupdate.find(pad.parent / "backups" / preupdate.FOLDER, gekozen) is not None
    )
    # De oude supervisor behandelt de klaargezette terugval voordat een schrijver start.
    env = {}
    from unittest.mock import patch

    with patch.object(aio, "prepare", return_value=[]):
        regels = aio.prepare_start(env, root, None)
        for regel in regels:
            print(regel)
    schema_na = stand(pad)[2][:3]
    hersteld = schema_na == "155" and stand(pad)[0:2] == (0.75, 2)
    wacht = preupdate.pending(pad.parent) is not None
    print(
        f"Oude supervisor: set vindbaar={zichtbaar}, herstel toegepast={hersteld}, "
        f"schema={schema_na}, herstelstand={bool(env.get(schema.AIO_RECOVERY_ENV))}, markering aanwezig={wacht}.",
        flush=True,
    )
    if not (hersteld and zichtbaar and not wacht):
        return 1
    import hashlib

    with (
        closing(sqlite3.connect(pad)) as db,
        closing(sqlite3.connect(root / "encode/app.db")) as enc,
    ):
        assert db.execute("SELECT action FROM audit_events").fetchall() == [
            ("job.retry",)
        ]
        assert enc.execute("SELECT percent FROM job").fetchall() == [(75,)]
        sleutel = db.execute("SELECT cursor_key FROM outbox_retention").fetchone()[0]
        assert hashlib.sha256(sleutel).digest() == (root / "digest").read_bytes()
    print(
        "Schema 155 actief; kern en encoder 75%, één auditstuk, twee events en oorspronkelijke leespositiesleutel behouden."
    )
    return 0


if len(sys.argv) == 4:
    raise SystemExit(fase(sys.argv[2], Path(sys.argv[3])))
with tempfile.TemporaryDirectory(dir=os.environ["TMPDIR"]) as tmp:
    root = Path(tmp)
    fasen = (
        ("oud", "controle") if sys.argv[1] == "controle" else ("oud", "nieuw", "start")
    )
    if sys.argv[1] in ("herupgrade", "herupgrade-schade"):
        fasen += (sys.argv[1],)
    for stap in fasen:
        bron = (
            kandidaat if stap in ("nieuw", "herupgrade", "herupgrade-schade") else basis
        )
        code = subprocess.run(
            [sys.executable, __file__, "fase", stap, str(root)],
            check=False,
            cwd=root,
            env=os.environ | {"PYTHONPATH": f"{bron}:{os.environ['REPO']}/scripts"},
        ).returncode
        if code:
            raise SystemExit(code)
