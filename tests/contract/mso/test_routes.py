"""Vergelijk het gepinde MSO-contract met de routetabel van de lokale MediaGuard-app."""

import json
import os
import subprocess
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[3]
PIN = Path(__file__).with_name("routes.json")
OPENAPI = ("GET", "/api/v1/openapi.json")

# Elke ingelogde gebruiker heeft minstens de rol viewer.
MSO_ROLE = {"none": 0, "viewer": 1, "operator": 2, "admin": 3}
MG_ROLE = {"geen": 0, "ingelogd": 1, "viewer": 1, "operator": 2, "admin": 3}


@pytest.fixture(scope="module")
def contract():
    return json.loads(PIN.read_text(encoding="utf-8"))


@pytest.fixture(scope="module")
def routetabel(tmp_path_factory):
    # De inventaris bouwt een lokale app zonder achtergrondtaken en verwijdert de testdatabase.
    result = subprocess.run(
        [sys.executable, str(ROOT / "scripts/platform-v2-routes.py"), "--json"],
        cwd=ROOT / "apps/core",
        env={**os.environ, "TMPDIR": str(tmp_path_factory.mktemp("mso-routes"))},
        check=False,
        capture_output=True,
        text=True,
    )
    assert result.returncode == 0, result.stderr
    return json.loads(result.stdout)


def controleer_routes(contract, routetabel):
    verwachte_routes = {(r["method"], r["path"]): r["role"] for r in contract["routes"]}
    assert len(verwachte_routes) == len(contract["routes"]) > 0, "Lege of dubbele MSO-routes"
    uitzondering = contract["uitzondering"]
    assert (
        (uitzondering["method"], uitzondering["path"]) == OPENAPI
        and uitzondering["mso_role"] == verwachte_routes.get(OPENAPI) == "none"
        and uitzondering["mediaguard_role"] == "admin"
        and uitzondering["reden"].strip()
    ), "Ongeldige OpenAPI-uitzondering"

    for route, verwacht in verwachte_routes.items():
        methode, pad = route
        label = f"{methode} {pad}"
        rollen = [
            rij["role"] for rij in routetabel
            if rij["path"] == pad and methode in rij["methods"]
        ]
        assert rollen, f"{label} ontbreekt in de MediaGuard-routetabel"
        assert len(rollen) == 1, f"{label} is meer dan eens geregistreerd"
        actueel = rollen[0]
        if route == OPENAPI:
            assert actueel == uitzondering["mediaguard_role"], (
                f"{label}: de OpenAPI-uitzondering vereist admin, gevonden: {actueel}"
            )
        else:
            assert MG_ROLE[actueel] <= MSO_ROLE[verwacht], (
                f"{label}: MediaGuard vereist {actueel}, MSO verwacht maximaal {verwacht}"
            )


def test_mso_routes(contract, routetabel):
    controleer_routes(contract, routetabel)


@pytest.mark.parametrize(
    ("methode", "pad"),
    [("GET", "/api/v1/wanted"), OPENAPI],
)
def test_mso_weigert_ontbrekende_route(contract, routetabel, methode, pad):
    kopie = [
        rij for rij in routetabel
        if not (rij["path"] == pad and methode in rij["methods"])
    ]
    with pytest.raises(AssertionError, match=f"{methode} {pad} ontbreekt"):
        controleer_routes(contract, kopie)


@pytest.mark.parametrize(
    ("pad", "rol", "melding"),
    [
        ("/api/v1/wanted", "operator", "MediaGuard vereist operator"),
        (OPENAPI[1], "viewer", "de OpenAPI-uitzondering vereist admin"),
    ],
)
def test_mso_weigert_rolafwijking(contract, routetabel, pad, rol, melding):
    kopie = [
        {**rij, "role": rol}
        if rij["path"] == pad and "GET" in rij["methods"]
        else rij
        for rij in routetabel
    ]
    with pytest.raises(AssertionError, match=f"GET {pad}: {melding}"):
        controleer_routes(contract, kopie)
