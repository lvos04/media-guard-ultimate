"""Lees de vorige SQLite-stand via de gewone alleen-lezenverbinding na de laatste wissel."""

import hashlib
import json
import shutil
import sys
from pathlib import Path

from mediaguard.maintenance import preupdate
from versieproef import config, core, enc, kind, live, repo, vrijgave, werk

if sys.argv[1:] == ["lezer"]:
    raise SystemExit(vrijgave())

import test_herstartpunten as punten

config.mkdir()
env = punten.installatie.__wrapped__(config)
_, voor, later = punten.herstelvraag(config, env)
punten.late_wal(live)
punten.onderbreek(config, "omwisselen", "resultaat")
marker = core / preupdate.STAGE_MARKER
vast = json.loads(marker.read_text())["omwisseling"]
if "fasebestand" in vast:
    vast = json.loads((core / preupdate.COMMIT_ORIGIN).read_text())
aside = core / "backups" / preupdate.ASIDE_FOLDER / vast["vorige"]
kern = aside / "core__mediaguard.db"
motor = aside / "encode__app.db"


def hashes():
    return {p.name: hashlib.sha256(p.read_bytes()).digest() for p in aside.iterdir()}


oorsprong = hashes()
shm = kern.with_name(kern.name + "-shm")
eerste_shm = shm.read_bytes()
assert kern.with_name(kern.name + "-wal").stat().st_size > 0
assert all(
    oorsprong[k.replace("/", "__")].hex() == v for k, v in vast["originelen"].items()
)
actief = punten.stand(live, enc)
assert actief == voor and len(voor[2]) == 2 and voor[3] == [(75,)]
leesmap = aside
if "op-kopie" in sys.argv:
    leesmap = werk / "controlekopie"
    shutil.copytree(aside, leesmap)
gelezen = punten.stand(leesmap / kern.name, leesmap / motor.name)
assert gelezen[:2] == later[:2] and gelezen[3] == [(90,)]
assert len(gelezen[2]) == 3 and gelezen[2][-1][1] == "library.laatste"
gewijzigd = {k for k, v in hashes().items() if oorsprong.get(k) != v}
assert gewijzigd <= {kern.name + "-shm"} and oorsprong.keys() == hashes().keys()
starts = [kind(repo, Path(__file__).resolve(), "lezer", (0, 73)) for _ in range(2)]
assert punten.stand(live, enc) == voor and punten.stand(kern, motor) == gelezen
if "oorzaak" in sys.argv:
    assert gewijzigd == {shm.name} and starts == [73, 73]
    fout = preupdate.apply_staged(
        core, av1_migrations=config / "motor/app/migrations", keep_on_error=True
    )
    assert (
        fout
        and not fout["ok"]
        and shm.name in fout["error"]
        and "gewijzigd" in fout["error"]
    )
    assert (
        marker.exists()
        and hashes()[kern.name + "-wal"] == oorsprong[kern.name + "-wal"]
    )
    # Controle op dezelfde, volledig stilstaande proefkopie: alleen het lezersregister terug naar zijn beginbytes.
    shm.write_bytes(eerste_shm)
    assert hashes() == oorsprong
    for _ in range(2):
        assert kind(repo, Path(__file__).resolve(), "lezer") == 0
    assert punten.stand(live, enc) == voor and punten.stand(kern, motor) == gelezen
    print(
        "Weigering noemt het gewijzigde SHM-lezersregister; alleen de oorspronkelijke registerbytes teruggelegd: 2/2 starts, beide gegevensstanden gelijk."
    )
    raise SystemExit(0)
print(
    f"Alleen-lezencontrole van de vorige stand: kern en WAL bytegelijk, lezersregister "
    f"{'gewijzigd' if gewijzigd else 'gelijk'}; {starts.count(0)}/2 starts vrijgegeven; "
    "actief 75% en 2 events, vorige stand 90% en 3 events.",
    flush=True,
)
raise SystemExit(int(starts != [0, 0]))
