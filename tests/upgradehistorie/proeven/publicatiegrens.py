"""Volledige vorige release, alleen de laatste encodermigratie staat open."""

import os
import subprocess
import sys
from pathlib import Path
from unittest.mock import patch

from mediaguard.maintenance import preupdate
from startgrens import core, init_testdata, open_diensten, stand

if sys.argv[1:] == ["stop"]:
    openen = preupdate.tempfile.NamedTemporaryFile

    def open_tijdelijk(*args, **kwargs):
        bestand = openen(*args, **kwargs)
        if kwargs.get("prefix") == preupdate.COMMIT_MARKER + ".":
            assert Path(bestand.name).stat().st_size == 0
            assert not (core / preupdate.COMMIT_MARKER).exists()
            os._exit(82)
        return bestand

    with patch.object(preupdate.tempfile, "NamedTemporaryFile", open_tijdelijk):
        open_diensten()
    raise AssertionError("Onderbreking niet bereikt.")

init_testdata()
voor = stand()
assert len(voor[0][-1]) == 2 and all(r[-1] == 75 for r in voor[1])
assert (
    subprocess.run([sys.executable, "-B", __file__, "stop"], check=False).returncode
    == 82
)
marker = core / preupdate.COMMIT_MARKER
assert not marker.exists()
assert stand() == voor
archieven = [
    p
    for p in (core / "backups" / preupdate.FOLDER).iterdir()
    if p.is_dir() and preupdate._read_meta(p)
]
gekozen = max(archieven, key=lambda p: p.stat().st_mtime_ns)
assert not preupdate.verify(gekozen)
for _ in range(2):
    open_diensten()
    assert stand()[:2] == voor[:2]
assert not list(core.glob(preupdate.COMMIT_MARKER + ".*.part"))
preupdate.stage(core, gekozen, actor="lokale herstelproef")
# Simuleer de destijds zichtbare lege markering samen met expliciet herstel.
marker.write_bytes(b"")
for _ in range(2):
    open_diensten()
    assert stand()[:2] == voor[:2] and not marker.exists()
result = preupdate.take_result(core)
assert result and result["ok"] and result["set"] == gekozen.name
print(
    "Uitval vóór publicatie: 2/2 herstarts; oude lege markering en expliciet terugzetten: "
    "2/2 herstarts; 75% en 2/2 events behouden."
)
