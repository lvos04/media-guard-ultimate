"""Contract 1.4 §11 (C1.3-ALG-02, C1.3-MGU-01): EVIDENCE.json volgt de vaste regels.

- ``status`` uit de lijst van het blad (Werkpakketten, Testmatrix, Besluiten); ``Gereed`` en
  ``Geslaagd`` worden in deze bouwomgeving nooit gezet.
- ``validatie``: één of meer waarden uit de vaste lijst, gescheiden door ``"; "``; nooit leeg.
  ``Niet getest`` alleen bij ``Niet gestart``; ``Sim-geslaagd``/``Sim-mislukt`` alleen in de
  Testmatrix (``EX..``, ``JLT-..``), ``Sim-getest``/``Unit-getest`` niet in de Testmatrix.
- Elke ``NIET GEVALIDEERD (…)`` uit ``blokkade`` staat ook in ``validatie``.
- ID's: Excel-ID's uit ``EXCEL_TAKEN.json``, contractpunten voor MGU en ALG uit de
  conformiteitslijst van de bijbehorende contractversie, reviews
  ``REVIEW-media-guard-ultimate-<onderwerp>``.
- Elk genoemd testbestand bestaat, en ``pad::functie`` staat in dat bestand (eerlijkheid: geen
  bewijs dat naar een verdwenen test wijst).
"""

from __future__ import annotations

import json
import re
from pathlib import Path
from typing import Any

import pytest

ROOT = Path(__file__).resolve().parents[3]
PV2 = ROOT / "docs" / "platform-v2"
EVIDENCE = PV2 / "EVIDENCE.json"
REPO = "media-guard-ultimate"

STATUS_WERK = {"Niet gestart", "Bezig", "Geblokkeerd", "N.v.t."}  # 'Gereed' nooit
STATUS_TEST = {"Niet gestart", "Bezig", "Mislukt", "Geblokkeerd", "N.v.t."}  # 'Geslaagd' nooit
STATUS_BESLUIT = {"Open", "Ingevuld", "Getest", "Geblokkeerd", "N.v.t."}
ALGEMEEN = {
    "Intel-iGPU-getest (ontwikkelmachine)",
    "NIET GEVALIDEERD (echte NAS)",
    "NIET GEVALIDEERD (hardware)",
    "NIET GEVALIDEERD (account)",
    "NIET GEVALIDEERD (Mac/Xcode)",
    "NIET GEVALIDEERD (echte Hub)",  # aanvulling C-12 (C12-6)
    "Niet getest",
}
WERK = {"Sim-getest", "Unit-getest"}
TEST = {"Sim-geslaagd", "Sim-mislukt"}
VELDEN = {"id", "status", "validatie", "bewijs", "blokkade", "opmerking", "repo", "bijgewerkt"}
CONTRACTPUNT = re.compile(r"C(1\.[34])-(?:MGU|ALG)-\d\d")
REVIEW = re.compile(rf"REVIEW-{REPO}-[a-z0-9][a-z0-9.-]*")
NIET_GEVALIDEERD = re.compile(r"NIET GEVALIDEERD \([^)]*\)")
TESTPAD = re.compile(
    r"((?:apps/core/tests|apps/web/(?:tests|e2e)|services/encoder/tests|scripts)/[\w/.-]+"
    r"\.(?:py|ts|mjs))(?:::(\w+))?")


def _items() -> list[dict[str, Any]]:
    data = json.loads(EVIDENCE.read_text(encoding="utf-8"))
    assert isinstance(data, list) and data
    return data


def _blad(rid: str) -> str:
    if re.fullmatch(r"EX\d{2}|JLT-\d+", rid):
        return "test"
    if re.fullmatch(r"D\d{2}", rid):
        return "besluit"
    return "werk"


def problemen(item: dict[str, Any]) -> list[str]:
    """Wat er aan één regel niet klopt volgens §11 (leeg = in orde)."""
    uit: list[str] = []
    if set(item) != VELDEN:
        return [f"velden {sorted(set(item) ^ VELDEN)}"]
    if item["repo"] != REPO:
        uit.append(f"repo {item['repo']!r}")
    if not re.fullmatch(r"\d{4}-\d{2}-\d{2}", item["bijgewerkt"]):
        uit.append("bijgewerkt is geen datum")
    blad = _blad(item["id"])
    statussen = {"werk": STATUS_WERK, "test": STATUS_TEST, "besluit": STATUS_BESLUIT}[blad]
    if item["status"] not in statussen:
        uit.append(f"status {item['status']!r} niet toegestaan in blad {blad}")
    if not item["validatie"].strip():
        return [*uit, "lege validatie (nooit toegestaan)"]
    waarden = item["validatie"].split("; ")
    toegestaan = ALGEMEEN | (TEST if blad == "test" else WERK)
    uit += [f"validatie {w!r} niet toegestaan in blad {blad}" for w in waarden
            if w not in toegestaan]
    if len(waarden) != len(set(waarden)):
        uit.append("dubbele validatiewaarde")
    if "Niet getest" in waarden and item["status"] != "Niet gestart":
        uit.append("'Niet getest' alleen bij status 'Niet gestart'")
    for gevonden in NIET_GEVALIDEERD.findall(item["blokkade"]):
        if gevonden not in waarden:
            uit.append(f"{gevonden!r} in blokkade maar niet in validatie")
    return uit


@pytest.mark.parametrize("item", _items(), ids=lambda e: str(e.get("id")))
def test_regel_volgt_paragraaf_11(item: dict[str, Any]) -> None:
    assert problemen(item) == []


GOED = {"id": "B04-009", "status": "Bezig", "validatie": "Sim-getest; NIET GEVALIDEERD (echte NAS)",
        "bewijs": "x", "blokkade": "", "opmerking": "", "repo": REPO, "bijgewerkt": "2026-11-10"}


@pytest.mark.parametrize(("wijziging", "melding"), [
    ({"status": "Gereed"}, "status 'Gereed'"),
    ({"id": "JLT-73", "status": "Geslaagd", "validatie": "Sim-geslaagd"}, "status 'Geslaagd'"),
    ({"validatie": ""}, "lege validatie"),
    ({"validatie": "Getest op de NAS"}, "niet toegestaan"),
    ({"validatie": "Niet getest"}, "'Niet getest' alleen"),
    ({"id": "JLT-73", "validatie": "Sim-getest"}, "niet toegestaan in blad test"),
    ({"validatie": "Sim-geslaagd"}, "niet toegestaan in blad werk"),
    ({"blokkade": "NIET GEVALIDEERD (hardware): echte GPU"}, "in blokkade maar niet in validatie"),
    ({"validatie": "Sim-getest; Sim-getest"}, "dubbele"),
    ({"repo": "ai-server-manager"}, "repo"),
])
def test_negatieve_gevallen_worden_gezien(wijziging: dict[str, str], melding: str) -> None:
    assert problemen(GOED) == []
    gevonden = problemen({**GOED, **wijziging})
    assert any(melding in p for p in gevonden), gevonden


def test_vaste_lijst_is_de_tabel_van_paragraaf_11() -> None:
    """De vaste lijst volgt het contract: een waarde erbij of eraf zonder contracttekst is rood
    (C12-6: ``NIET GEVALIDEERD (echte Hub)``)."""
    tekst = (PV2 / "PLATFORM_CONTRACTEN.md").read_text(encoding="utf-8")
    begin = tekst.index("## 11. Bewijs en Excel")
    p11 = tekst[begin : tekst.index("## 12.", begin)]
    in_tabel: set[str] = set()
    for ruw in p11.splitlines():
        regel = ruw.strip()
        if regel.startswith("| `"):
            in_tabel.update(re.findall(r"`([^`]+)`", regel.split(" | ")[0]))
    vast = ALGEMEEN | WERK | TEST
    assert in_tabel == vast, f"verschil met contract §11: {sorted(in_tabel ^ vast)}"
    hub = {**GOED, "validatie": "Unit-getest; NIET GEVALIDEERD (echte Hub)",
           "blokkade": "NIET GEVALIDEERD (echte Hub): alleen met de Hub-simulator beproefd"}
    assert problemen(hub) == []


def test_ids_bestaan_en_zijn_uniek() -> None:
    excel = {t["ID"] for t in json.loads((PV2 / "EXCEL_TAKEN.json").read_text(encoding="utf-8"))}
    lijsten = {v: (PV2 / f"CONTRACT_{v}_CONFORMITEIT.md").read_text(encoding="utf-8")
               for v in ("1.3", "1.4")}
    ids = [e["id"] for e in _items()]
    assert len(ids) == len(set(ids))
    for rid in ids:
        if rid.startswith("C1."):
            punt = CONTRACTPUNT.fullmatch(rid)
            assert punt and f"| {rid} |" in lijsten[punt[1]], rid
        elif rid.startswith("REVIEW-"):
            assert REVIEW.fullmatch(rid), rid
        else:
            assert rid in excel, f"{rid} staat niet in EXCEL_TAKEN.json"


def test_bewijs_wijst_naar_bestaande_tests() -> None:
    problemen: list[str] = []
    for item in _items():
        for pad, functie in TESTPAD.findall(item["bewijs"]):
            bestand = ROOT / pad
            if not bestand.is_file():
                problemen.append(f"{item['id']}: {pad} bestaat niet")
            elif functie and not re.search(rf"(def |it\(|test\()[\"']?{re.escape(functie)}\b",
                                           bestand.read_text(encoding="utf-8")):
                problemen.append(f"{item['id']}: {pad}::{functie} bestaat niet")
    assert not problemen, "\n".join(problemen)


def test_nooit_gereed_of_geslaagd_ook_niet_in_tekst_als_status() -> None:
    for item in _items():
        tekst = item["opmerking"]
        assert not re.search(r"\bstatus\b[^.;]*\b(Gereed|Geslaagd)\b", tekst), item["id"]


def test_contractreview_vermeldt_alle_open_praktijkproeven() -> None:
    review = next(r for r in _items() if r["id"] == "REVIEW-media-guard-ultimate-contract-1.3")
    verwacht = {f"NIET GEVALIDEERD ({soort})" for soort in ("echte NAS", "hardware", "account")}
    assert verwacht <= set(review["validatie"].split("; "))


@pytest.mark.parametrize(("rij_id", "soorten"), [
    ("B04-001", ("echte NAS", "hardware", "account")),
    ("B04-002", ("echte NAS", "account")),
    ("B04-003", ("echte NAS", "hardware", "account")),
    ("B04-004", ("echte NAS", "account")),
    ("B04-005", ("echte NAS", "hardware", "account")),
    ("B04-006", ("echte NAS", "account")),
    ("B04-007", ("echte NAS", "hardware", "account")),
    ("B04-008", ("echte NAS", "hardware", "account")),
    ("B04-009", ("echte NAS", "account")),
    ("B04-010", ("echte NAS", "hardware", "account")),
    ("C1.3-MGU-03", ("echte NAS", "account")),
    ("C1.3-MGU-04", ("echte NAS", "hardware", "account")),
    ("C1.3-MGU-05", ("echte NAS", "hardware", "account")),
    ("C1.3-MGU-06", ("echte NAS", "account")),
    ("C1.3-MGU-07", ("account",)),
    ("C1.3-MGU-08", ("account",)),
    ("C1.3-MGU-09", ("echte NAS", "hardware", "account")),
    ("C1.3-ALG-04", ("account",)),
    ("C1.3-ALG-05", ("echte NAS", "hardware", "account")),
    ("B03-014", ("echte NAS", "hardware", "account")),
    ("B03-015", ("echte NAS", "hardware", "account")),
    ("JLT-73", ("echte NAS", "hardware", "account")),
    ("JLT-74", ("echte NAS", "hardware", "account")),
])
def test_praktijkproef_vermeldt_vereiste_markeringen(rij_id: str, soorten: tuple[str, ...]) -> None:
    """Ook in gewone taal beschreven afhankelijkheden uit de acceptatiestap tellen mee voor G-19."""
    per_id = {rij["id"]: rij["validatie"] for rij in _items()}
    verwacht = {f"NIET GEVALIDEERD ({soort})" for soort in soorten}
    assert verwacht <= set(per_id[rij_id].split("; "))
