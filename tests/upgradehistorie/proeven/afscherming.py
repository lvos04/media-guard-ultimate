"""Controleer het echte leesrecht van elk van de zeven nevendiensten."""

import subprocess
import sys

from mediaguard import aio


def controleer_afscherming(env, staged):
    proefbestand = staged.with_name("toegangsproef.terugzetten")
    proefbestand.write_text("proef")
    script = """
from pathlib import Path
import sys
for p in map(Path, sys.argv[1:]):
    try:
        p.open("rb").close()
    except PermissionError:
        continue
    sys.exit(17)
"""
    specs = [s for s in aio.build_specs(env) if s.restricted_roots]
    assert len(specs) == 7
    for spec in specs:
        spec.argv = [sys.executable, "-I", "-c", script, str(proefbestand), str(staged)]
        subprocess.run(spec.start_command(), check=True)
