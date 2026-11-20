"""Laat SQLite zelf pagina's van een onderbroken, ongecommitteerde transactie schrijven."""

import os
import sqlite3
import sys
from contextlib import closing
from pathlib import Path

from walgrenzen import (
    config,
    core,
    enc,
    events,
    kind,
    lees,
    live,
    percentage,
    repo,
    tweemaal,
    vingerafdruk,
    werk,
)

sys.path.insert(0, str(repo / "apps/core/tests"))
import test_herstartpunten as stappen
from mediaguard.maintenance import preupdate


def onderbreek_transactie():
    # Geen handmatige schrijfactie aan de WAL of het lezersregister.
    conn = sqlite3.connect(enc)
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA wal_autocheckpoint=0")
    conn.execute("PRAGMA cache_size=5")
    conn.execute("PRAGMA cache_spill=ON")
    conn.execute("CREATE TABLE proef_blokken (id INTEGER PRIMARY KEY, inhoud BLOB)")
    conn.executemany(
        "INSERT INTO proef_blokken VALUES (?, zeroblob(3000))",
        [(i,) for i in range(120)],
    )
    conn.commit()
    assert conn.execute("PRAGMA wal_checkpoint(TRUNCATE)").fetchone() == (0, 0, 0)
    assert conn.execute("UPDATE job SET percent=92").rowcount == 1
    conn.commit()
    wal = Path(str(enc) + "-wal")
    (werk / "laatste-commit").write_bytes(wal.read_bytes())
    conn.execute("BEGIN IMMEDIATE")
    assert conn.execute("UPDATE job SET percent=95").rowcount == 1
    assert (
        conn.execute("UPDATE proef_blokken SET inhoud=randomblob(3000)").rowcount == 120
    )
    assert wal.stat().st_size > (werk / "laatste-commit").stat().st_size
    os._exit(88)


def proef():
    config.mkdir()
    env = stappen.installatie.__wrapped__(config)
    stappen.herstelvraag(config, env)
    kind(repo, Path(__file__).resolve(), "onderbreek", (88,))
    prefix = (werk / "laatste-commit").read_bytes()
    wal = Path(str(enc) + "-wal").read_bytes()
    blok = 24 + int.from_bytes(wal[8:12], "big")
    assert wal.startswith(prefix) and (len(prefix) - 32) % blok == 0
    laatste = len(prefix) - blok
    assert int.from_bytes(wal[laatste + 4 : laatste + 8], "big") > 0
    staart = wal[len(prefix) :]
    assert len(staart) > 0 and len(staart) % blok == 0
    for pos in range(0, len(staart), blok):
        assert staart[pos + 4 : pos + 8] == bytes(4)
        assert staart[pos + 8 : pos + 16] == wal[16:24]
    assert lees(enc, percentage) == [(92,)]
    assert lees(
        enc, "SELECT count(*) FROM proef_blokken WHERE inhoud=zeroblob(3000)"
    ) == [(120,)]
    assert len(lees(live, events)) == 3
    bestanden = vingerafdruk()
    migraties = config / "motor/app/migrations"
    if sys.argv[1:] == ["herstart"]:
        vrij = tweemaal()
        assert vrij in (0, 2)
        if vrij:
            assert (
                lees(enc, percentage) == [(75,)]
                and not (core / preupdate.STAGE_MARKER).exists()
            )
            vorig = Path(preupdate.take_result(core)["previous"]) / "encode__app.db"
            assert lees(vorig, percentage) == [(92,)]
        else:
            assert (
                vingerafdruk() == bestanden
                and not (core / preupdate.RESULT_FILE).exists()
            )
            assert (core / preupdate.STAGE_MARKER).exists() and lees(
                enc, percentage
            ) == [(92,)]
        print(
            f"Na een echte procesuitval met {len(staart) // blok} ongecommitteerde WAL-frames: "
            f"{vrij}/2 supervisorstarts vrijgegeven, actieve encoder {int(lees(enc, percentage)[0][0])}%, "
            f"aanvraag {'bewaard' if (core / preupdate.STAGE_MARKER).exists() else 'afgehandeld'}."
        )
        return int(vrij != 2)
    aanvragen = []
    for _ in range(2):
        res = preupdate.apply_staged(core, av1_migrations=migraties, keep_on_error=True)
        aanvragen.append(res)
    if aanvragen[0] and aanvragen[0]["ok"]:
        assert aanvragen[1] is None
        vorig = Path(aanvragen[0]["previous"]) / "encode__app.db"
        assert lees(vorig, percentage) == [(92,)] and lees(enc, percentage) == [(75,)]
        print(
            f"{len(staart) // blok} ongecommitteerde WAL-frames na 92%: omwisseling voltooid, "
            "herhaalde aanroep zonder aanvraag, actief 75%, vorige encoder 92%."
        )
        return 0
    assert all(r and not r["ok"] for r in aanvragen) and vingerafdruk() == bestanden
    assert (core / preupdate.STAGE_MARKER).exists() and not (
        core / preupdate.RESULT_FILE
    ).exists()
    assert all("ongelezen frames" in r["error"] for r in aanvragen), aanvragen
    # De positieve controle gebruikt een gewoon SQLite-checkpoint op een stilstaande proefdatabase.
    with closing(sqlite3.connect(enc)) as conn:
        assert conn.execute("PRAGMA wal_checkpoint(TRUNCATE)").fetchone() == (0, 0, 0)
    assert lees(enc, percentage) == [(92,)]
    assert lees(
        enc, "SELECT count(*) FROM proef_blokken WHERE inhoud=zeroblob(3000)"
    ) == [(120,)]
    res = preupdate.apply_staged(core, av1_migrations=migraties, keep_on_error=True)
    assert res and res["ok"]
    assert lees(enc, percentage) == [(75,)]
    assert lees(Path(res["previous"]) / "encode__app.db", percentage) == [(92,)]
    print(
        f"{len(staart) // blok} ongecommitteerde WAL-frames na 92%: 2/2 omwisselingen geweigerd "
        "met behoud van de aanvraag. Na SQLite-checkpoint met dezelfde 92% en 120/120 rijen "
        "werkte de omwisseling, actief 75%, vorige encoder 92%."
    )
    return 1


if sys.argv[1:] == ["onderbreek"]:
    onderbreek_transactie()
raise SystemExit(proef())
