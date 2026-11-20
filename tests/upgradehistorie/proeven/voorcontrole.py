"""Observeer de werkelijke SQLite-verbindingen bij de schemacontrole van een herstart."""

import sys
from pathlib import Path
from unittest.mock import patch
from urllib.parse import unquote, urlsplit

from walgrenzen import (
    aio,
    config,
    core,
    enc,
    kind,
    lees,
    live,
    percentage,
    repo,
    script,
)

sys.path.insert(0, str(repo / "apps/core/tests"))
import test_herstartpunten as stappen
from mediaguard.maintenance import preupdate

punt = {"voor-markering": "lege-tijdelijke-naam", "na-markering": "setnaam"}[
    sys.argv[1]
]
config.mkdir()
instellingen = stappen.installatie.__wrapped__(config)
kind(repo, script, "schrijf-encoder")
stappen.onderbreek(config, "update", punt)
assert (core / preupdate.COMMIT_ORIGIN).exists()
assert (core / preupdate.COMMIT_MARKER).exists() == (punt == "setnaam")
assert lees(enc, percentage) == [(90,)]
shm = enc.with_name("app.db-shm")
shm.unlink(missing_ok=True)
geopend = []
meten = True


def observeer(event, args):
    if meten and event == "sqlite3.connect":
        naam = str(args[0])
        pad = unquote(urlsplit(naam).path) if naam.startswith("file:") else naam
        geopend.append(Path(pad).resolve())


sys.addaudithook(observeer)
with patch.object(aio, "prepare", return_value=[]):
    melding = aio.prepare_start(instellingen, config, None)
meten = False
assert melding == [] and not instellingen.get("MG_AIO_SCHEMA_CHECK_FAILED"), melding
originelen = [pad for pad in geopend if pad in (live, enc)]
kopieen = [
    pad
    for pad in geopend
    if pad.parent.parent == core and pad.parent.name.startswith(".controle-sqlite-")
]
assert geopend and len(kopieen) + len(originelen) == len(geopend)
assert lees(enc, percentage) == [(90,)] and lees(enc, percentage, False) == [(75,)]
print(
    f"Schemavoorcontrole {sys.argv[1]}: SQLite opende {len(originelen)} oorspronkelijke "
    f"databases en {len(kopieen)} controlekopieën, origineel encoder-SHM "
    f"{'aangemaakt' if shm.exists() else 'afwezig'}, taak 90%."
)
raise SystemExit(int(bool(originelen) or shm.exists()))
