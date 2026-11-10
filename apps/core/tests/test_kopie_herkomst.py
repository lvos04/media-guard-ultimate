"""Byte-gelijke kopieën van het platformcontract en van Peil.

Contract 1.3 (kop en §10, C1.3-ALG-01/C1.3-MGU-01) en ONTWERPTAAL §14 (pin- en driftregel E08 van
Peil 1.1.1). ``scripts/kopie-herkomst.py`` doet het werk; deze tests pinnen bovendien de
bron-commit en de sha256 van het contract (een nieuwe kopie overnemen = stap van de orkestratie:
HERKOMST.json, de kopie én deze constanten), draaien de controle op de echte repo en bewijzen met
een nagebootste bron dat elke soort drift wordt gezien (negatieve gevallen)."""

from __future__ import annotations

import importlib.util
import json
import shutil
import subprocess
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[3]
_spec = importlib.util.spec_from_file_location(
    "kopie_herkomst", ROOT / "scripts" / "kopie-herkomst.py"
)
assert _spec and _spec.loader
kh = importlib.util.module_from_spec(_spec)
sys.modules["kopie_herkomst"] = kh  # dataclasses zoeken hun module op
_spec.loader.exec_module(kh)

#: bron-commit in lvos04/ai-server-manager (branch ccr-c133d7a0-kuz1cv):
#: contract 1.3 na de review én Peil 1.1.1
BRON_COMMIT = "0a7143bc0a94ed5317a9655fa785e73a1fb07ba1"
CONTRACT_SHA256 = {
    "docs/platform-v2/PLATFORM_CONTRACTEN.md": (
        "15b30cf17f91b606451ef9c41f02327076ef76ce655ee94e40e3538106962310"
    ),
    "docs/platform-v2/CONTRACT_1.3_CONFORMITEIT.md": (
        "f0a2352ddb27088ae668013501c62a1eaaf879efe742a2d4422ffe8d33b43466"
    ),
}
PEIL_VERSIE = "1.1.1"
#: naast elkaar uitgecheckt (bouwomgeving): ook tegen de echte bron (git show), anders overgeslagen
SIBLING = ROOT.parent / "ai-server-manager"


def _herkomst(soort: str) -> dict:
    return json.loads((ROOT / kh.SETS[soort]).read_text(encoding="utf-8"))


# -- de echte repo -------------------------------------------------------------------------------
def test_kopieen_in_deze_repo_kloppen_met_hun_herkomst() -> None:
    u = kh.controleer(ROOT)
    assert u.fouten == []


def test_contract_13_is_byte_gelijk_aan_de_gepinde_bron() -> None:
    h = _herkomst("contract")
    assert (
        h["versie"] == "1.3"
        and h["bron_commit"] == BRON_COMMIT
        and h["bron_repo"] == "lvos04/ai-server-manager"
    )
    assert set(h["bestanden"]) == set(CONTRACT_SHA256)
    for pad, digest in CONTRACT_SHA256.items():
        data = (ROOT / pad).read_bytes()
        assert kh.sha256(data) == digest, (
            f"{pad} wijkt af van contract 1.3 (bron-commit {BRON_COMMIT[:7]})"
        )
        assert h["bestanden"][pad]["sha256"] == digest and h["bestanden"][pad]["kop"] == 0
    assert (
        b"Versie: `contracts 1.3`"
        in (ROOT / "docs/platform-v2/PLATFORM_CONTRACTEN.md").read_bytes()
    )


def test_peil_kopieen_pinnen_111_met_commit_en_herkomstkop() -> None:
    h = _herkomst("peil")
    assert h["versie"] == PEIL_VERSIE and h["bron_commit"] == BRON_COMMIT
    css = {k for k in h["bestanden"] if k.endswith(".css")}
    assert css == {
        "design/ontwerptaal/ontwerptaal.css",
        "design/ontwerptaal/aliaslagen/mediaguard-mso.css",
        "apps/web/styles/peil/ontwerptaal.css",
        "apps/web/styles/peil/mediaguard-mso.css",
    }
    for kopie in css:
        assert h["bestanden"][kopie]["strikt"] is True and h["bestanden"][kopie]["kop"] == 2
        kop = (ROOT / kopie).read_text(encoding="utf-8").split("\n", 2)
        assert f"Peil {PEIL_VERSIE}" in kop[0] and f"@{BRON_COMMIT}" in kop[0]
        assert "Niet met de hand wijzigen" in kop[1]
    # de web-kopie (Docker-context) en de repo-kopie hebben dezelfde bron en dus dezelfde inhoud
    assert (
        h["bestanden"]["apps/web/styles/peil/ontwerptaal.css"]["sha256"]
        == h["bestanden"]["design/ontwerptaal/ontwerptaal.css"]["sha256"]
    )
    tokens = json.loads((ROOT / "design/ontwerptaal/tokens.json").read_text(encoding="utf-8"))
    assert tokens["$extensions"]["nl.peil"]["versie"] == PEIL_VERSIE


@pytest.mark.skipif(
    not (SIBLING / ".git").exists(), reason="geen checkout van ai-server-manager naast deze repo"
)
def test_lokale_bron_op_de_gepinde_commit_is_gelijk() -> None:
    probe = subprocess.run(
        ["git", "-C", str(SIBLING), "cat-file", "-e", f"{BRON_COMMIT}^{{commit}}"],
        capture_output=True,
        check=False,
    )
    if probe.returncode != 0:
        pytest.skip("de gepinde commit staat niet in de lokale checkout")
    # alleen de gepinde commit: de werkbranch van de bron kan intussen verder zijn
    # (dat is hooguit een waarschuwing)
    u = kh.vergelijk(kh.haler_lokaal(SIBLING), "lokaal", ROOT, huidige_ref=BRON_COMMIT)
    assert u.fouten == [] and u.waarschuwingen == []


# -- nagebootste bron: elke soort drift wordt gezien ---------------------------------------------
def _kopie_van_repo(tmp: Path) -> Path:
    root = tmp / "repo"
    for soort, rel in kh.SETS.items():
        (root / rel).parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(ROOT / rel, root / rel)
        for kopie in _herkomst(soort)["bestanden"]:
            (root / kopie).parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(ROOT / kopie, root / kopie)
    return root


def _bron(root: Path) -> dict[tuple[str, str], bytes]:
    """De bron op de gepinde commit én op de branch (afgeleid uit de kopieën zelf)."""
    bron: dict[tuple[str, str], bytes] = {}
    for soort in kh.SETS:
        h = json.loads((root / kh.SETS[soort]).read_text(encoding="utf-8"))
        for kopie, pin in h["bestanden"].items():
            _, inhoud = kh.splits((root / kopie).read_bytes(), pin["kop"])
            for ref in (h["bron_commit"], h["bron_ref"]):
                bron[(ref, pin["bron_pad"])] = inhoud
    return bron


def _haler(bron: dict[tuple[str, str], bytes]):  # type: ignore[no-untyped-def]
    def haal(ref: str, pad: str) -> bytes:
        try:
            return bron[(ref, pad)]
        except KeyError:
            raise FileNotFoundError(f"{ref}:{pad}") from None

    return haal


REF = "ccr-c133d7a0-kuz1cv"


def test_nagebootste_bron_gelijk_geeft_niets(tmp_path: Path) -> None:
    root = _kopie_van_repo(tmp_path)
    u = kh.vergelijk(_haler(_bron(root)), "test", root)
    assert u.fouten == [] and u.waarschuwingen == []


@pytest.mark.parametrize(
    "kopie",
    [
        "docs/platform-v2/PLATFORM_CONTRACTEN.md",
        "design/ontwerptaal/tokens.json",
        "apps/web/styles/peil/mediaguard-mso.css",
        "design/ontwerptaal/ONTWERPTAAL.md",
    ],
)
def test_met_de_hand_gewijzigde_kopie_faalt_offline(tmp_path: Path, kopie: str) -> None:
    root = _kopie_van_repo(tmp_path)
    pad = root / kopie
    pad.write_bytes(pad.read_bytes() + b" ")
    u = kh.controleer(root)
    assert any(kopie in f and "inhoud gewijzigd" in f for f in u.fouten), u.fouten


def test_herkomstkop_met_andere_commit_of_versie_faalt(tmp_path: Path) -> None:
    root = _kopie_van_repo(tmp_path)
    pad = root / "design/ontwerptaal/ontwerptaal.css"
    tekst = pad.read_text(encoding="utf-8")
    pad.write_text(tekst.replace(f"@{BRON_COMMIT}", "@" + "1" * 40, 1), encoding="utf-8")
    andere = root / "apps/web/styles/peil/ontwerptaal.css"
    andere.write_text(
        andere.read_text(encoding="utf-8").replace("Peil 1.1.1", "Peil 1.1.0", 1), encoding="utf-8"
    )
    fouten = kh.controleer(root).fouten
    assert any("ontwerptaal.css: bron-commit in de herkomstkop" in f for f in fouten), fouten
    assert any(
        "apps/web/styles/peil/ontwerptaal.css: versie in de herkomstkop" in f for f in fouten
    ), fouten


def test_ontbrekende_of_onleesbare_herkomst_faalt(tmp_path: Path) -> None:
    root = _kopie_van_repo(tmp_path)
    (root / kh.SETS["contract"]).write_text("{", encoding="utf-8")
    (root / "design/ontwerptaal/components.json").unlink()
    fouten = kh.controleer(root).fouten
    assert any(f.startswith("contract:") and "onleesbaar" in f for f in fouten), fouten
    assert any("components.json: ontbreekt" in f for f in fouten), fouten


def test_bron_op_gepinde_commit_anders_is_drift(tmp_path: Path) -> None:
    root = _kopie_van_repo(tmp_path)
    bron = _bron(root)
    bron[(BRON_COMMIT, "docs/platform-v2/CONTRACT_1.3_CONFORMITEIT.md")] += b"\nlokaal aangepast"
    u = kh.vergelijk(_haler(bron), "test", root)
    assert any(
        "CONTRACT_1.3_CONFORMITEIT.md: kopie ≠ bron" in f and "drift" in f for f in u.fouten
    ), u.fouten


def test_peil_e08_zelfde_versie_andere_inhoud_faalt_alleen_bij_strikte_bestanden(
    tmp_path: Path,
) -> None:
    root = _kopie_van_repo(tmp_path)
    bron = _bron(root)
    bron[(REF, "design/ontwerptaal/aliaslagen/mediaguard-mso.css")] += b"\n.extra{}"
    bron[(REF, "docs/platform-v2/ONTWERPTAAL.md")] += b"\nredactionele zin"
    u = kh.vergelijk(_haler(bron), "test", root)
    # aliaslaag: zowel de repo-kopie als de web-kopie (zelfde bron) falen
    assert sum("mediaguard-mso.css" in f and "E08" in f for f in u.fouten) == 2, u.fouten
    assert not any("ONTWERPTAAL.md" in f for f in u.fouten)
    assert any("ONTWERPTAAL.md" in w and "zelfde versie" in w for w in u.waarschuwingen), (
        u.waarschuwingen
    )


def test_peil_nieuwere_bronversie_is_alleen_een_waarschuwing(tmp_path: Path) -> None:
    root = _kopie_van_repo(tmp_path)
    bron = _bron(root)
    tokens = json.loads(bron[(REF, "design/ontwerptaal/tokens.json")])
    tokens["$extensions"]["nl.peil"]["versie"] = "1.1.2"
    bron[(REF, "design/ontwerptaal/tokens.json")] = json.dumps(tokens).encode()
    bron[(REF, "design/ontwerptaal/ontwerptaal.css")] += b"\n/* 1.1.2 */"
    u = kh.vergelijk(_haler(bron), "test", root)
    assert u.fouten == []
    assert any("peil 1.1.2" in w and "bewust" in w for w in u.waarschuwingen), u.waarschuwingen


def test_peil_oudere_bron_dan_de_kopie_faalt(tmp_path: Path) -> None:
    root = _kopie_van_repo(tmp_path)
    bron = _bron(root)
    tokens = json.loads(bron[(REF, "design/ontwerptaal/tokens.json")])
    tokens["$extensions"]["nl.peil"]["versie"] = "1.1.0"
    bron[(REF, "design/ontwerptaal/tokens.json")] = json.dumps(tokens).encode()
    u = kh.vergelijk(_haler(bron), "test", root)
    assert any("ouder dan de kopie" in f for f in u.fouten), u.fouten


def test_contract_later_gewijzigd_in_de_bron_is_een_waarschuwing(tmp_path: Path) -> None:
    """Contract (kop): de drifttest controleert tegen een VASTE bron-commit; een latere wijziging
    in de bron is een signaal voor de orkestratie (nieuwe kopie), geen rode CI in deze repo."""
    root = _kopie_van_repo(tmp_path)
    bron = _bron(root)
    bron[(REF, "docs/platform-v2/CONTRACT_1.3_CONFORMITEIT.md")] += b"\nC1.3-ASM-01 conform"
    u = kh.vergelijk(_haler(bron), "test", root)
    assert u.fouten == []
    assert any("CONTRACT_1.3_CONFORMITEIT.md" in w and "orkestratie" in w for w in u.waarschuwingen)


def test_onbereikbare_bron_is_een_fout_voor_de_pin(tmp_path: Path) -> None:
    root = _kopie_van_repo(tmp_path)

    def stuk(ref: str, pad: str) -> bytes:
        raise ConnectionError("geen netwerk")

    u = kh.vergelijk(stuk, "test", root)
    assert u.fouten and all("niet te lezen" in f for f in u.fouten)


def test_bijwerken_schrijft_kop_en_herkomst_vanuit_een_git_bron(tmp_path: Path) -> None:
    root = _kopie_van_repo(tmp_path)
    bron = tmp_path / "bron"
    for (ref, pad), inhoud in _bron(root).items():
        if ref == REF:
            (bron / pad).parent.mkdir(parents=True, exist_ok=True)
            (bron / pad).write_bytes(inhoud)
    tokens = json.loads((bron / "design/ontwerptaal/tokens.json").read_text(encoding="utf-8"))
    tokens["$extensions"]["nl.peil"]["versie"] = "1.1.2"
    (bron / "design/ontwerptaal/tokens.json").write_text(json.dumps(tokens), encoding="utf-8")
    (bron / "design/ontwerptaal/ontwerptaal.css").write_bytes(b"/* Peil 1.1.2 */\n:root{}\n")
    git = ["git", "-C", str(bron), "-c", "user.name=t", "-c", "user.email=t@example.invalid"]
    subprocess.run([*git, "init", "-q"], check=True)
    subprocess.run([*git, "add", "-A"], check=True)
    subprocess.run([*git, "commit", "-qm", "bron"], check=True)
    commit = subprocess.run(
        [*git, "rev-parse", "HEAD"], capture_output=True, text=True, check=True
    ).stdout.strip()
    assert kh.bijwerken("peil", bron, "abc", None, root).fouten  # geen volledige commit-hash
    u = kh.bijwerken("peil", bron, commit, "main", root)
    assert u.fouten == []
    h = json.loads((root / kh.SETS["peil"]).read_text(encoding="utf-8"))
    assert h["versie"] == "1.1.2" and h["bron_commit"] == commit and h["bron_ref"] == "main"
    kopie = (root / "apps/web/styles/peil/ontwerptaal.css").read_text(encoding="utf-8").split("\n")
    assert kopie[0].startswith("/* HERKOMST — Peil 1.1.2 · bron lvos04/ai-server-manager@" + commit)
    assert kopie[2:] == ["/* Peil 1.1.2 */", ":root{}", ""]
    md = (root / "design/ontwerptaal/ONTWERPTAAL.md").read_text(encoding="utf-8").split("\n")
    assert md[0].startswith("<!-- HERKOMST — Peil 1.1.2") and md[0].endswith("-->") and md[1] == ""
    # en de nieuwe stand is meteen weer in orde tegen die bron
    assert (
        kh.vergelijk(kh.haler_lokaal(bron), "test", root, sets=("peil",), huidige_ref=commit).fouten
        == []
    )
