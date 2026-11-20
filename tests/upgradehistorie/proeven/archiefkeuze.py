"""Een oudere geldige back-up met één encoder en de daadwerkelijk gekozen set met twee."""

import os
import sqlite3
import sys
from contextlib import closing
from pathlib import Path
from unittest.mock import patch

from mediaguard import aio
from mediaguard.maintenance import preupdate
from versieproef import (
    config,
    core,
    enc,
    helper,
    kind,
    lees,
    live,
    oud,
    repo,
    vrijgave,
    werk,
)

sys.path.insert(0, str(helper))
from startgrens import init_testdata

bestand = Path(__file__).resolve()
tweede = enc.with_name("tweede.db")
sets = core / "backups" / preupdate.FOLDER
marker = core / preupdate.COMMIT_MARKER


def voorbereiden(wal):
    assert repo == oud
    init_testdata()
    env = {}
    aio.before_services(env, config, None)
    eerste = Path(env["MG_PRE_UPDATE_SET"])
    assert set(preupdate._set_files(core, eerste)) == {
        "core/mediaguard.db",
        "encode/app.db",
    }
    if wal:
        bron = sqlite3.connect(enc)
        assert bron.execute("PRAGMA journal_mode").fetchone() == ("wal",)
        bron.execute("PRAGMA wal_autocheckpoint=0")
        with bron:
            assert bron.execute("UPDATE job SET percent=90").rowcount > 0
        assert lees(enc, "SELECT DISTINCT percent FROM job", immutable=True) == [(75,)]
        assert lees(enc, "SELECT DISTINCT percent FROM job") == [(90,)]
    else:
        with (
            closing(sqlite3.connect(enc)) as bron,
            closing(sqlite3.connect(tweede)) as db,
            db,
        ):
            bron.backup(db)
            assert db.execute("UPDATE job SET percent=90").rowcount > 0
    origineel, openen = preupdate.record_commit, Path.open

    def opslaan(kern, archief):
        (werk / "gekozen").write_text(archief.name)
        return origineel(kern, archief)

    def onderbreken(pad, mode="r", *args, **kwargs):
        handle = openen(pad, mode, *args, **kwargs)
        if pad == marker and mode == "x":
            os._exit(82)
        return handle

    with (
        patch.object(preupdate, "record_commit", opslaan),
        patch.object(Path, "open", onderbreken),
    ):
        aio.before_services({}, config, None, migrate_core=True)
    raise AssertionError("oude schrijfgrens niet bereikt")


def proef(wal, herstellen):
    kind(oud, bestand, "voorbereiden-wal" if wal else "voorbereiden", (82,))
    assert marker.read_bytes() == b""
    gekozen = sets / (werk / "gekozen").read_text()
    verwachte_namen = {"core/mediaguard.db", "encode/app.db"}
    if not wal:
        verwachte_namen.add("encode/tweede.db")
    assert set(preupdate._set_files(core, gekozen)) == verwachte_namen
    assert not preupdate.verify(gekozen)
    percent = "SELECT DISTINCT percent FROM job"
    taak = enc if wal else tweede
    assert lees(taak, percent) == [(90,)] and lees(
        gekozen / "encode" / taak.name, percent
    ) == [(90,)]
    # Bewaar alle bytes elders in de proefmap om een ontbrekend live-bestand te simuleren.
    apart = werk / "afzonderlijk-bewaard"
    apart.mkdir()
    if wal:
        assert enc.with_name(enc.name + "-wal").stat().st_size > 0
        assert lees(enc, percent, immutable=True) == [(75,)]
    patroon = enc.name + "-*" if wal else tweede.name + "*"
    for pad in taak.parent.glob(patroon):
        pad.rename(apart / pad.name)
    starts = []
    for _ in range(2):
        starts.append(kind(repo, bestand, "lezer", (0, 73)))
        assert not tweede.exists() and lees(enc, percent) == [(75,)]
        assert lees(live, "SELECT count(*) FROM outbox_events") == [(2,)]
        assert not preupdate.verify(gekozen)
    markering = "bewaard" if marker.exists() else "verwijderd"
    if herstellen:
        preupdate.stage(core, gekozen, actor="lokale terugzetproef")
        for _ in range(2):
            assert kind(repo, bestand, "lezer") == 0
            assert lees(taak, percent) == [(90,)] and lees(
                live, "SELECT count(*) FROM outbox_events"
            ) == [(2,)]
        print(
            "Expliciet terugzetten van de bij de onderbreking gekozen set: 2/2 starts met de taak op 90% en beide events.",
            flush=True,
        )
        return 0
    gebrek = "laatste encoder-WAL ontbreekt" if wal else "tweede encoder ontbreekt"
    print(
        f"Oude lege markering, {gebrek}: {starts.count(0)}/2 starts vrijgegeven; "
        f"markering {markering}; taak op 90% ontbreekt live en staat in de gekozen set, eerste encoder 75% en 2 events.",
        flush=True,
    )
    return int(starts != [73, 73] or markering != "bewaard")


if sys.argv[1:] in (["voorbereiden"], ["voorbereiden-wal"]):
    voorbereiden(sys.argv[1] == "voorbereiden-wal")
elif sys.argv[1:] == ["lezer"]:
    raise SystemExit(vrijgave())
else:
    raise SystemExit(proef("wal" in sys.argv[1:], "herstel" in sys.argv[1:]))
