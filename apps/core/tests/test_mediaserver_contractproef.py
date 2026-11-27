"""Tegenproeven voor de mediaservertests. De runner krijgt alleen lokale proefbestanden."""

from __future__ import annotations

import os
import secrets
import shutil
import subprocess
import sys
import xml.etree.ElementTree as ET
from pathlib import Path

import pytest

CONTRACT = Path(__file__).resolve().parents[3] / "tests/contract/mediaserver"


def draai_runner(tmp_path: Path, testbron: str, *args: str) -> tuple[int, str]:
    """Voer de echte run.sh uit met een tijdelijke testsuite en zonder containers of media."""
    map_ = tmp_path / "tests/contract/mediaserver"
    map_.mkdir(parents=True)
    shutil.copyfile(CONTRACT / "run.sh", map_ / "run.sh")
    (map_ / "contractbron.py").symlink_to(CONTRACT / "test_mediaserver_contract.py")
    (map_ / "test_mediaserver_contract.py").write_text(testbron, encoding="utf-8")
    (tmp_path / "deploy").mkdir()
    (tmp_path / "deploy/release-manifest.json").write_text('{"version": "proef"}')
    # Bash leest de proefcommando's ook als de tijdelijke CI-schijf noexec is.
    proefcommando = tmp_path / "proefcommando.sh"
    proefcommando.write_text(
        'ffmpeg() { :; }\n'
        'docker() {\n'
        '  if [[ "$1" == compose && "${4:-}" == up ]]; then\n'
        '  for naam in proxylog/emby.jsonl proxylog/jellyfin.jsonl metingen.json '
        'logs-mediaguard-vorige.txt logs-mediaguard-kandidaat.txt; do\n'
        '    echo meting > "$MGMS_RUN/$naam"\n'
        '  done\n'
        '  fi\n'
        '  echo proefimage\n'
        '}\n'
    )
    env = dict(os.environ, BASH_ENV=str(proefcommando), MG_CORE_PYTHON=sys.executable,
               MGMS_KANDIDAAT="proefimage", MGMS_BASIS="enkelvoudig", MGMS_INTERN="false",
               KEEP_RUNNING="0", MGMS_BEWIJS=str(tmp_path / "bewijs"), PYTEST_ADDOPTS="")
    resultaat = subprocess.run(["bash", str(map_ / "run.sh"), *args], env=env, cwd=tmp_path,
                               stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True,
                               timeout=60)
    return resultaat.returncode, resultaat.stdout


def test_afwijzing_toont_geen_verzoekwaarden(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    marker = secrets.token_hex(16)
    monkeypatch.setenv("PROEFWAARDE", marker)
    rc, uitvoer = draai_runner(tmp_path, """
import os
import httpx
from contractbron import Opnemer, controleer_koppen

def test_ongeldig_pad():
    waarde = os.environ["PROEFWAARDE"]
    opnemer = Opnemer()
    opnemer.verzoeken.append((httpx.Request("GET", "https://proef.invalid/emby/" + waarde), 401))
    controleer_koppen(opnemer, "emby", waarde)
""")
    assert rc == 1
    bewijs = tmp_path / "bewijs"
    zaken = ET.parse(bewijs / "junit.xml").findall(".//testcase")
    assert len(zaken) == 1 and zaken[0].get("name") == "test_ongeldig_pad"
    fout = zaken[0].find("failure")
    juiste_afwijzing = fout is not None and "sleutel in het adres" in fout.get("message", "")
    assert juiste_afwijzing
    # Geef ook bij een regressie uitsluitend de namen van de getroffen uitvoerkanalen door.
    gelekt = [naam for naam in ("pytest.log", "junit.xml")
              if marker in (bewijs / naam).read_text(encoding="utf-8")]
    if marker in uitvoer:
        gelekt.append("console")
    assert not gelekt, gelekt


def test_sessiepoll_vergelijkt_het_venster(monkeypatch: pytest.MonkeyPatch):
    monkeypatch.syspath_prepend(str(CONTRACT))
    monkeypatch.delenv("MGMS_VORIGE", raising=False)
    from test_mediaserver_contract import emby_verzoeksoorten, vingerafdruk

    poll = {"method": "GET", "path": "/emby/Sessions", "auth": ["x-emby-token"]}

    def verzoek(venster: str):
        return {**poll, "query": {"ActiveWithinSeconds": vingerafdruk(venster)}}

    referentie = emby_verzoeksoorten([verzoek("120")])
    assert emby_verzoeksoorten([verzoek("120")]) == referentie
    assert emby_verzoeksoorten([verzoek("1")]) - referentie
    assert emby_verzoeksoorten([{**poll, "query": {}}]) - referentie


@pytest.mark.parametrize("situatie", ["volledig", "doel_is_map", "bron_ontbreekt"])
def test_bewaarfout_bepaalt_de_uitkomst_van_de_runner(tmp_path: Path, situatie: str):
    bewijs = tmp_path / "bewijs"
    bewijs.mkdir()
    testbron = "def test_groen():\n    pass\n"
    if situatie == "doel_is_map":
        (bewijs / "images.txt").mkdir()
    if situatie == "bron_ontbreekt":
        # Een oude kopie in het doel mag een ontbrekend nieuw bestand niet verhullen.
        (bewijs / "emby.jsonl").write_text("oude meting")
        testbron = (
            "import os\nfrom pathlib import Path\n"
            "def test_groen():\n    (Path(os.environ['MGMS_RUN']) / 'proxylog/emby.jsonl').unlink()"
        )
    rc, _ = draai_runner(tmp_path, testbron)
    assert "1 passed" in (bewijs / "pytest.log").read_text()
    assert not ET.parse(bewijs / "junit.xml").findall(".//failure")
    if situatie == "volledig":
        assert rc == 0
        assert (bewijs / "images.txt").read_text().splitlines() == ["proefimage"] * 4
        assert (bewijs / "emby.jsonl").read_text().strip() == "meting"
    else:
        assert rc != 0


def test_bewaarfout_behoudt_de_exitcode_van_pytest(tmp_path: Path):
    (tmp_path / "bewijs/images.txt").mkdir(parents=True)
    rc, _ = draai_runner(tmp_path, "def test_groen(): pass", "-k", "niet_geselecteerd")
    assert rc == pytest.ExitCode.NO_TESTS_COLLECTED
