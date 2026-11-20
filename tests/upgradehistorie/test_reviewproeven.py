"""Herhaal de data- en compatibiliteitsproeven tegen de oorspronkelijke releasecode."""

import os
import subprocess
import sys
import tarfile
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parents[2]
PROEVEN = Path(__file__).with_name("proeven")
VERSIES = {
    "voor-bundelbewijs": ("e1c121d4a29bff7228f56bd6ede5f9548ac00ae5", ["apps/core/mediaguard"]),
    "122": ("3998bea1880229e8351b4b28e8f912275cdd15c1", ["apps/core/mediaguard"]),
    "150": (
        "29b9af2e55385d1f9f9c536d55dd3f42d8e92368",
        ["apps/core/mediaguard/jobs.py", "apps/core/mediaguard/migrations"],
    ),
    "154": (
        "5c0dd33f044b6db11a46be96cbe41ce4ea316f65",
        ["apps/core/mediaguard"],
    ),
    "155": (
        "2393f5261464deb54392e62a0924cd79ba797b85",
        ["apps/core/mediaguard", "services/encoder/app/migrations"],
    ),
    "wissel": (
        "d516f3871c55b69b08b202660c578961de291d82",
        ["apps/core", "scripts", "tests/fixtures", "deploy", "services/encoder/app"],
    ),
}


BRONPADEN = ["apps/core", "scripts", "tests/fixtures", "deploy", "services/encoder/app"]
for naam, commit in {
    "voor-fasebestand": "1739ee01a1d8a11d58d4163793705eff2b45eb21",
    "eerste-fasebestand": "16c29bdaab5c313436fbf9bdb62fac8b7e4fb50a",
    "154": VERSIES["154"][0],
    "voor-155": "060ecfa8daa2efacbf8ed9ea06cdaa151096c696",
    "155": VERSIES["155"][0],
    "eerste-terugval": "ac5a8f036decdb44a54f1b998034e3a4fb614e86",
    "voor-herstelschrijver": "128680f74ff247a861273a93420905ac371cc6e9",
    "voor-wissel": "c744623024199ad124771a90436761c19a3baff3",
    "ouder-wissel": "27a889c692203ba59429d8e428007d145c6bddf6",
    "ouder-fase": "21b9e761dd711ae7a16d27e134af147a767ff9a4",
    "oude-fase": "0673229629b596bb071be8586e431cba20cf7b91",
    "ouder-binding": "cd8c1c13d54c6724388c0f5f2eca805e86b0a8db",
    "binding": "420b5e86f70d8ac72fd2bb218b031254d2604417",
}.items():
    VERSIES[naam] = (commit, BRONPADEN)


@pytest.fixture(scope="session")
def historie(tmp_path_factory):
    root = tmp_path_factory.mktemp("releasebron")
    for naam, (commit, paden) in VERSIES.items():
        with subprocess.Popen(
            ["git", "-C", str(REPO), "archive", commit, *paden], stdout=subprocess.PIPE
        ) as git:
            with tarfile.open(fileobj=git.stdout, mode="r|") as archive:
                archive.extractall(root / naam, filter="data")
            assert git.wait() == 0
    return root


GEVALLEN = [
    "bundelpaar",
    "proeven behoud",
    "proeven archieffout",
    "proeven oude-client",
    "proeven terugvalcursor",
    "proeven gelijktijdig",
    "lege-pagina",
    "archiefrotatie",
    "archiefrotatie systeem",
    "sse-retentie",
    "schrijver",
    "schrijver historisch",
    "encoderfase",
    "oude-keuze",
    "oude-keuze onderbreking",
    "oude-keuze-op-naam",
    "grens155",
    "terugval-start start",
    "terugval-start controle",
    "terugval-gevuld terugval",
    "terugval-gevuld controle",
    "terugval-gevuld herupgrade",
    "terugval-gevuld herupgrade-schade",
    "terugval122",
    "wachtend",
    "opslaggrens",
    "onderbreking hervatten",
    "sleutel-herstelset",
    "sleutel-afgebroken",
    "publicatiegrens",
    "omwisselgrens",
    "startgrens encoderdienst",
    "archiefkeuze",
    "archiefkeuze wal",
    "archiefkeuze wal herstel",
    "lees-vorige",
    "lees-vorige op-kopie",
    "vorige-stand",
    "vorige-stand half",
    "vorige-stand intact",
    "vorige-stand nieuwe-schrijver",
    "vorige-stand nieuwe-schrijver vóór-resultaat",
    "voorcontrole voor-markering",
    "voorcontrole na-markering",
    "open-transactie",
    "open-transactie herstart",
    "walgrenzen oorsprong intact",
    "walgrenzen oorsprong zonder-wal",
    "walgrenzen oorsprong zonder-wal expliciet",
    *(
        f"walgrenzen {schrijver} {fase} {storing}"
        for schrijver in ("oud", "nieuw")
        for fase in ("half", "volledig")
        for storing in ("intact", "zonder-wal", "zonder-slotcommit")
    ),
    "deelcheckpoint zonder-checkpoint",
    "deelcheckpoint deelcheckpoint",
    "deelcheckpoint intact",
    "deelcheckpoint latere-wal",
    "deelcheckpoint nieuwe-wal",
    "historische-keuze stap-0",
    "historische-keuze actief-mediaguard.db",
    "historische-keuze onvolledige-encoder",
    "beschadigingsmatrix",
    "kijkpunten",
    "rechten",
]


def proefomgeving(tmp_path, historie, bron):
    env = {k: v for k, v in os.environ.items() if not k.startswith("MG_")}
    env.update(
        REPO=str(bron),
        PROEFBRON=str(bron),
        MG_REPO=str(bron),
        HISTORIE=str(historie),
        TESTBRON=str(REPO),
        TMPDIR=str(tmp_path),
        SQLITE_TMPDIR=str(tmp_path),
        WERK=str(tmp_path),
        MG_DATA_DIR=str(tmp_path / "invoer/core"),
        MG_STAGING_DIR=str(tmp_path / "staging"),
        MG_QUARANTINE_DIR=str(tmp_path / "quarantaine"),
        MG_START_BACKGROUND="0",
        PYTHONDONTWRITEBYTECODE="1",
        PYTHONNOUSERSITE="1",
        PYTHONPATH=os.pathsep.join(
            map(
                str,
                (
                    bron / "apps/core",
                    bron / "apps/core/tests",
                    bron / "scripts",
                    PROEVEN,
                ),
            )
        ),
    )
    return env


@pytest.mark.parametrize("geval", GEVALLEN)
def test_reviewproef(tmp_path, historie, geval):
    programma, *args = geval.split()
    env = proefomgeving(tmp_path, historie, REPO)
    if programma == "archiefrotatie" and args:
        env["PROEF_ARCHIEFKLOK"] = args.pop()
    if programma == "kijkpunten":
        cmd = [
            "-m",
            "pytest",
            "-q",
            "-p",
            "no:cacheprovider",
            "-p",
            "kijkpunten",
            str(REPO / "apps/core/tests/test_herstartpunten.py"),
            "-k",
            "uitval_bij_elke",
        ]
    elif programma == "rechten":
        cmd = [
            "-m",
            "pytest",
            "-q",
            "-p",
            "no:cacheprovider",
            "-p",
            "conftest",
            "-p",
            "test_event_herstel",
            "-c",
            str(REPO / "apps/core/pyproject.toml"),
            str(PROEVEN / "controle_rechten.py"),
        ]
    else:
        cmd = [str(PROEVEN / (programma + ".py")), *args]
    subprocess.run(
        [sys.executable, "-B", *cmd],
        check=True,
        cwd=tmp_path,
        env=env,
        timeout=900 if programma in ("kijkpunten", "beschadigingsmatrix") else 180,
    )


# Elke verwachte weigering moet de inhoudelijke slotregel van de oorspronkelijke proef
# opleveren. Een importfout of een uitgevallen proefproces levert daardoor nooit bewijs.
TEGENPROEVEN = [
    ("eerste-fasebestand", "deelcheckpoint deelcheckpoint", 1, "vorige 90%, succes ja"),
    ("eerste-fasebestand", "historische-keuze stap-0", 1, "aantal bevestigingswaarden bij gelijke stand 3"),
    ("154", "lege-pagina", 1, "HTTP 200, 0 events"),
    ("155", "lege-pagina", 1, "HTTP 200, 0 events"),
    ("voor-155", "lege-pagina", 0, "HTTP 409"),
    ("155", "archiefrotatie", 1, "na terugval 25%"),
    ("155", "sse-retentie", 0, "SSE leverde [1, 2, 3, 4]"),
    ("eerste-terugval", "terugval-start start", 1, "herstel toegepast=False"),
    ("eerste-terugval", "terugval-start controle", 0, "herstel toegepast=True"),
    ("voor-herstelschrijver", "oude-keuze", 0, "eventpagina HTTP 200 met 2/2 events"),
    ("voor-wissel", "oude-keuze", 1, "eventpagina 200 → 500, hervatten 500"),
    (
        "voor-herstelschrijver",
        "onderbreking historisch",
        0,
        "2/2 herstarts vrijgegeven",
    ),
    ("voor-wissel", "onderbreking historisch", 1, "0/2 herstarts vrijgegeven"),
    (
        "voor-wissel",
        "opslaggrens historisch",
        1,
        "in de klaargezette database buiten secrets/core ja.",
    ),
    ("voor-herstelschrijver", "encoderfase", 1, "25% en 0 auditrij"),
    ("voor-wissel", "encoderfase", 1, "25% en 0 auditrij"),
    ("ouder-wissel", "startgrens", 0, "2/2 herstarts vrijgegeven"),
    ("ouder-wissel", "omwisselgrens", 0, "2/2 herstarts vrijgegeven"),
    ("wissel", "startgrens", 1, "0/2 herstarts vrijgegeven"),
    ("wissel", "omwisselgrens", 1, "0/2 herstarts vrijgegeven"),
    ("ouder-fase", "archiefkeuze", 0, "0/2 starts vrijgegeven"),
    ("ouder-fase", "archiefkeuze wal", 0, "0/2 starts vrijgegeven"),
    ("ouder-fase", "vorige-stand", 0, "0/2 starts vrijgegeven"),
    ("ouder-fase", "vorige-stand half", 0, "0/2 starts vrijgegeven"),
    ("oude-fase", "archiefkeuze", 1, "2/2 starts vrijgegeven"),
    ("oude-fase", "archiefkeuze wal", 1, "2/2 starts vrijgegeven"),
    ("oude-fase", "lees-vorige", 1, "0/2 starts vrijgegeven"),
    ("oude-fase", "lees-vorige oorzaak", 0, "beide gegevensstanden gelijk"),
    ("oude-fase", "vorige-stand", 1, "2/2 starts vrijgegeven"),
    ("oude-fase", "vorige-stand half", 1, "2/2 starts vrijgegeven"),
    ("oude-fase", "open-transactie", 0, "vorige encoder 92%"),
    ("oude-fase", "open-transactie herstart", 0, "2/2 supervisorstarts vrijgegeven"),
    (
        "ouder-binding",
        "open-transactie herstart",
        0,
        "2/2 supervisorstarts vrijgegeven",
    ),
    ("binding", "walgrenzen oorsprong zonder-wal", 1, "2/2 starts vrijgegeven"),
    ("binding", "voorcontrole voor-markering", 1, "opende 2 oorspronkelijke databases"),
    ("binding", "walgrenzen oud half zonder-slotcommit", 1, "3/4 events, 2/2 starts"),
    ("binding", "open-transactie herstart", 1, "0/2 supervisorstarts vrijgegeven"),
]


@pytest.mark.parametrize(("versie", "geval", "code", "bewijs"), TEGENPROEVEN)
def test_vastgelegde_referentie(tmp_path, historie, versie, geval, code, bewijs):
    programma, *args = geval.split()
    env = proefomgeving(tmp_path, historie, historie / versie)
    result = subprocess.run(
        [sys.executable, "-B", str(PROEVEN / (programma + ".py")), *args],
        check=False,
        cwd=tmp_path,
        env=env,
        timeout=180,
        text=True,
        capture_output=True,
    )
    assert result.returncode == code, result.stderr[-2000:] + result.stdout[-500:]
    assert bewijs in result.stdout.strip().splitlines()[-1], result.stdout[-500:]
