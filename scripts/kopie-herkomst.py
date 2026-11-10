#!/usr/bin/env python3
"""Byte-gelijke kopieën uit ``lvos04/ai-server-manager`` bewaken: contract en ontwerptaal Peil.

Twee sets, elk met een eigen ``HERKOMST.json`` die versie, bronrepo, **bron-commit** (40 hex) en
per kopie de sha256 van het bronbestand pint:

``contract``  ``docs/platform-v2/HERKOMST.json``
    ``PLATFORM_CONTRACTEN.md`` en ``CONTRACT_1.3_CONFORMITEIT.md``, helemaal byte-gelijk
    (contract 1.3, kop en §10, C1.3-ALG-01/C1.3-MGU-01). Fout bij elke afwijking van de bron op
    de gepinde commit. Is de bron daarna gewijzigd, dan is dat een **waarschuwing**: een nieuwe
    kopie overnemen is een stap van de orkestratie (contract, kop).
``peil``      ``design/ontwerptaal/HERKOMST.json``
    De Peil-kopieën (ONTWERPTAAL §14, pin- en driftregel E08 van Peil 1.1.1). Een kopie met
    ``kop: 2`` heeft een herkomstkop van twee regels; de sha256 geldt voor de rest. Fout bij
    (1) een afwijking van de gepinde inhoud en (2) voor ``strikt``-bestanden: dezelfde versie
    met andere inhoud in de bron. Een nieuwere bronversie is een waarschuwing (bewust bijwerken
    met ``bijwerken``). Niet-strikte bestanden (documentatie) geven bij elke latere
    bronwijziging alleen een waarschuwing.

Opdrachten (alleen de standaardbibliotheek):

``controleer``
    offline: HERKOMST.json, koppen en sha256 (CI en unit-tests)
``vergelijk --bron <map>`` en/of ``--online``
    tegen een checkout van de bron (``git show``) of GitHub (raw, publieke repo)
``bijwerken --set <contract|peil> --bron <map> --commit <40 hex> [--ref <branch>]``
    kopieën en HERKOMST.json opnieuw schrijven vanuit die commit

Uitgang 0 = in orde (waarschuwingen mogen), 1 = drift of fout, 2 = verkeerd gebruik.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import subprocess
import sys
import time
import urllib.error
import urllib.request
from collections.abc import Callable
from dataclasses import dataclass, field
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
BRON_REPO = "lvos04/ai-server-manager"
RAW = "https://raw.githubusercontent.com/{repo}/{ref}/{pad}"
SETS = {
    "contract": Path("docs/platform-v2/HERKOMST.json"),
    "peil": Path("design/ontwerptaal/HERKOMST.json"),
}
#: waar de versie van een set in de bron staat
VERSIE_BRON = {
    "contract": "docs/platform-v2/PLATFORM_CONTRACTEN.md",
    "peil": "design/ontwerptaal/tokens.json",
}
CONTRACT_VERSIE = re.compile(r"^Versie: `contracts (\d+\.\d+)`", re.M)
PEIL_VERSIE = re.compile(r"Peil (\d+\.\d+\.\d+)")
SHA = re.compile(r"sha256[^:\n]*:\s*([0-9a-f]{64})")
COMMIT = re.compile(r"@([0-9a-f]{40})\b")

Haler = Callable[[str, str], bytes]  # (ref, bronpad) -> inhoud


@dataclass
class Uitkomst:
    fouten: list[str] = field(default_factory=list)
    waarschuwingen: list[str] = field(default_factory=list)


def sha256(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def semver(v: str) -> tuple[int, ...]:
    return tuple(int(x) for x in v.split("."))


def set_versie(soort: str, data: bytes) -> str | None:
    """Versie van een set zoals de bron hem noemt (contract: kopregel, Peil: tokens.json)."""
    tekst = data.decode("utf-8", "replace")
    if soort == "contract":
        m = CONTRACT_VERSIE.search(tekst[:2000])
        return m.group(1) if m else None
    try:
        return str(json.loads(tekst)["$extensions"]["nl.peil"]["versie"])
    except (ValueError, KeyError, TypeError):
        m = PEIL_VERSIE.search(tekst[:600])
        return m.group(1) if m else None


def splits(data: bytes, kop: int) -> tuple[str, bytes]:
    """(kop als tekst, inhoud zonder kop). ``kop`` = aantal regels."""
    if kop <= 0:
        return "", data
    delen = data.split(b"\n", kop)
    if len(delen) <= kop:
        return data.decode("utf-8", "replace"), b""
    return b"\n".join(delen[:kop]).decode("utf-8", "replace"), delen[kop]


def lees_herkomst(root: Path, soort: str) -> dict[str, object]:
    data = json.loads((root / SETS[soort]).read_text(encoding="utf-8"))
    if not isinstance(data, dict):
        raise ValueError("geen object")
    return data


def _bestanden(herkomst: dict[str, object]) -> dict[str, dict[str, object]]:
    b = herkomst.get("bestanden")
    if not isinstance(b, dict) or not b or not all(isinstance(v, dict) for v in b.values()):
        raise ValueError("'bestanden' moet een niet-leeg object met objecten zijn")
    return b


def _kop(pin: dict[str, object]) -> int:
    """Aantal regels herkomstkop van een kopie (0 = helemaal byte-gelijk)."""
    waarde = pin.get("kop", 0)
    return waarde if isinstance(waarde, int) and waarde > 0 else 0


# -------------------------------------------------------------------------------------- controleer
def controleer(root: Path = ROOT, sets: tuple[str, ...] = tuple(SETS)) -> Uitkomst:
    """Offline: kloppen kopieën, koppen en HERKOMST.json met elkaar?"""
    u = Uitkomst()
    for soort in sets:
        try:
            herkomst = lees_herkomst(root, soort)
            bestanden = _bestanden(herkomst)
        except (OSError, ValueError) as exc:
            u.fouten.append(f"{soort}: {SETS[soort]} onleesbaar ({exc})")
            continue
        versie = str(herkomst.get("versie") or "")
        commit = str(herkomst.get("bron_commit") or "")
        if herkomst.get("soort") != soort:
            u.fouten.append(f"{soort}: 'soort' in {SETS[soort]} is {herkomst.get('soort')!r}")
        if herkomst.get("bron_repo") != BRON_REPO:
            u.fouten.append(f"{soort}: bron_repo moet {BRON_REPO} zijn")
        if not re.fullmatch(r"[0-9a-f]{40}", commit):
            u.fouten.append(f"{soort}: bron_commit moet een volledige commit-hash (40 hex) zijn")
        if not versie:
            u.fouten.append(f"{soort}: geen versie in {SETS[soort]}")
        for kopie, pin in bestanden.items():
            pad = root / kopie
            try:
                data = pad.read_bytes()
            except OSError as exc:
                u.fouten.append(f"{kopie}: ontbreekt ({exc.strerror})")
                continue
            kop_regels = _kop(pin)
            kop, inhoud = splits(data, kop_regels)
            if sha256(inhoud) != pin.get("sha256"):
                u.fouten.append(
                    f"{kopie}: inhoud gewijzigd (sha256 {sha256(inhoud)[:16]}… ≠ pin "
                    f"{str(pin.get('sha256'))[:16]}…); nooit met de hand wijzigen"
                )
            if not isinstance(pin.get("bron_pad"), str) or not pin.get("bron_pad"):
                u.fouten.append(f"{kopie}: geen bron_pad in {SETS[soort]}")
            if kop_regels:
                gevonden = SHA.search(kop)
                if not (gevonden and gevonden.group(1) == pin.get("sha256")):
                    u.fouten.append(f"{kopie}: sha256 in de herkomstkop ≠ {SETS[soort]}")
                c = COMMIT.search(kop)
                if not (c and c.group(1) == commit):
                    u.fouten.append(f"{kopie}: bron-commit in de herkomstkop ≠ {SETS[soort]}")
                v = PEIL_VERSIE.search(kop)
                if soort == "peil" and not (v and v.group(1) == versie):
                    u.fouten.append(f"{kopie}: versie in de herkomstkop ≠ Peil {versie}")
            if (
                soort == "peil"
                and pin.get("bron_pad") == VERSIE_BRON["peil"]
                and set_versie("peil", inhoud) != versie
            ):
                u.fouten.append(
                    f"{kopie}: tokens.json noemt Peil {set_versie('peil', inhoud)}, "
                    f"HERKOMST {versie}"
                )
            if (
                soort == "contract"
                and pin.get("bron_pad") == VERSIE_BRON["contract"]
                and set_versie("contract", inhoud) != versie
            ):
                u.fouten.append(
                    f"{kopie}: kopregel noemt contracts {set_versie('contract', inhoud)}, "
                    f"HERKOMST {versie}"
                )
    return u


# ----------------------------------------------------------------------------------------- bronnen
def haler_lokaal(bron: Path) -> Haler:
    def haal(ref: str, pad: str) -> bytes:
        out = subprocess.run(  # noqa: S603 - vaste opdracht, geen shell
            ["git", "-C", str(bron), "show", f"{ref}:{pad}"],  # noqa: S607
            capture_output=True,
            check=False,
        )
        if out.returncode != 0:
            raise FileNotFoundError(f"{bron}: {ref}:{pad} niet gevonden")
        return out.stdout

    return haal


def haler_online(wachttijden: tuple[int, ...] = (0, 2, 4, 8, 16)) -> Haler:
    """Publieke bron via raw.githubusercontent.com, zonder token.

    Bij netwerkfouten 4× opnieuw na 2/4/8/16 s."""

    def haal(ref: str, pad: str) -> bytes:
        url = RAW.format(repo=BRON_REPO, ref=ref, pad=pad)
        laatste: Exception | None = None
        for wacht in wachttijden:
            if wacht:
                time.sleep(wacht)
            try:
                with urllib.request.urlopen(url, timeout=30) as resp:  # noqa: S310 - vaste https-URL
                    return bytes(resp.read())
            except urllib.error.HTTPError as exc:
                if exc.code == 404:
                    raise FileNotFoundError(f"{url}: 404") from exc
                laatste = exc
            except (urllib.error.URLError, TimeoutError) as exc:
                laatste = exc
        raise ConnectionError(f"{url}: niet op te halen ({laatste})")

    return haal


# --------------------------------------------------------------------------------------- vergelijk
def vergelijk(
    haal: Haler,
    waar: str,
    root: Path = ROOT,
    sets: tuple[str, ...] = tuple(SETS),
    huidige_ref: str | None = None,
) -> Uitkomst:
    """Kopie tegen de bron op de gepinde commit (fout bij verschil) en tegen de huidige bron
    (``huidige_ref`` of de ``bron_ref`` uit HERKOMST.json): nieuwere versie = waarschuwing;
    zelfde versie met andere inhoud = fout bij strikte Peil-bestanden, anders waarschuwing."""
    u = controleer(root, sets)
    if u.fouten:
        return u
    for soort in sets:
        herkomst = lees_herkomst(root, soort)
        bestanden = _bestanden(herkomst)
        commit, versie = str(herkomst["bron_commit"]), str(herkomst["versie"])
        ref = huidige_ref or str(herkomst.get("bron_ref") or "")
        try:
            gepinde_versie = set_versie(soort, haal(commit, VERSIE_BRON[soort]))
        except (FileNotFoundError, ConnectionError, OSError) as exc:
            u.fouten.append(f"{waar} {soort}: gepinde commit {commit[:7]} niet te lezen ({exc})")
            continue
        if gepinde_versie != versie:
            u.fouten.append(
                f"{waar} {soort}: de bron noemt op {commit[:7]} versie {gepinde_versie}, "
                f"HERKOMST {versie}"
            )
        nu_versie: str | None = None
        if ref:
            try:
                nu_versie = set_versie(soort, haal(ref, VERSIE_BRON[soort]))
            except (FileNotFoundError, ConnectionError, OSError) as exc:
                u.waarschuwingen.append(
                    f"{waar} {soort}: huidige bron ({ref}) niet te lezen ({exc})"
                )
                ref = ""
        for kopie, pin in bestanden.items():
            bron_pad = str(pin["bron_pad"])
            _, inhoud = splits((root / kopie).read_bytes(), int(pin.get("kop", 0) or 0))  # type: ignore[call-overload]
            try:
                gepind = haal(commit, bron_pad)
            except (FileNotFoundError, ConnectionError, OSError) as exc:
                u.fouten.append(
                    f"{waar} {kopie}: {bron_pad} op de gepinde commit {commit[:7]} niet te lezen "
                    f"({exc})"
                )
                continue
            if gepind != inhoud:
                u.fouten.append(
                    f"{waar} {kopie}: kopie ≠ bron {bron_pad} op de gepinde commit {commit[:7]} "
                    "(drift)"
                )
                continue
            if not ref:
                continue
            try:
                nu = haal(ref, bron_pad)
            except (FileNotFoundError, ConnectionError, OSError) as exc:
                u.waarschuwingen.append(
                    f"{waar} {kopie}: {bron_pad} op {ref} niet te lezen ({exc})"
                )
                continue
            if nu == inhoud:
                continue
            if nu_versie is None:
                u.fouten.append(
                    f"{waar} {soort}: de huidige bron heeft geen leesbare versie meer (drift)"
                )
            elif semver(nu_versie) > semver(versie):
                u.waarschuwingen.append(
                    f"{waar} {kopie}: de bron heeft {soort} {nu_versie} (kopie {versie}); bewust "
                    "bijwerken met 'scripts/kopie-herkomst.py bijwerken' en de eigen tests"
                )
            elif semver(nu_versie) < semver(versie):
                u.fouten.append(
                    f"{waar} {kopie}: de bron ({nu_versie}) is ouder dan de kopie ({versie})"
                )
            elif soort == "peil" and pin.get("strikt", True):
                u.fouten.append(
                    f"{waar} {kopie}: Peil {versie} heeft in de bron ({ref}) andere inhoud dan "
                    "de gepinde "
                    "kopie (E08: zelfde versie, andere inhoud)"
                )
            else:
                u.waarschuwingen.append(
                    f"{waar} {kopie}: {bron_pad} is op {ref} gewijzigd na de gepinde commit "
                    f"{commit[:7]} (zelfde versie {versie}); nieuwe kopie = stap van de orkestratie"
                )
    return u


# --------------------------------------------------------------------------------------- bijwerken
def maak_kop(soort: str, kopie: str, versie: str, commit: str, bron_pad: str, digest: str) -> str:
    """Herkomstkop van twee regels (CSS-commentaar of HTML-commentaar + lege regel)."""
    if kopie.endswith(".css"):
        doel = "Docker-context apps/web" if kopie.startswith("apps/web/") else "design/ontwerptaal"
        return (
            f"/* HERKOMST — Peil {versie} · bron {BRON_REPO}@{commit} {bron_pad} · "
            f"sha256 van het bronbestand (zonder deze kop): {digest}\n"
            f"   Versie-gepinde kopie (O-14, ONTWERPTAAL §14, {doel}). Niet met de hand wijzigen: "
            "bijwerken met scripts/kopie-herkomst.py bijwerken; controle: npm run check:peil. */"
        )
    return (
        f"<!-- HERKOMST — Peil {versie} · bron {BRON_REPO}@{commit} {bron_pad} · "
        "sha256 van het bronbestand "
        "(zonder deze kop): "
        + digest
        + " · versie-gepinde kopie (O-14), niet met de hand wijzigen "
        "(scripts/kopie-herkomst.py). Relatieve links wijzen naar de bronrepo en werken in "
        "deze kopie niet. -->\n"
    )


def bijwerken(soort: str, bron: Path, commit: str, ref: str | None, root: Path = ROOT) -> Uitkomst:
    u = Uitkomst()
    if not re.fullmatch(r"[0-9a-f]{40}", commit):
        u.fouten.append("--commit moet een volledige commit-hash (40 hex) zijn")
        return u
    herkomst = lees_herkomst(root, soort)
    bestanden = _bestanden(herkomst)
    haal = haler_lokaal(bron)
    try:
        versie = set_versie(soort, haal(commit, VERSIE_BRON[soort]))
    except FileNotFoundError as exc:
        u.fouten.append(str(exc))
        return u
    if versie is None:
        u.fouten.append(f"{soort}: geen versie in de bron op {commit[:7]}")
        return u
    nieuw: dict[str, dict[str, object]] = {}
    for kopie, pin in bestanden.items():
        bron_pad = str(pin["bron_pad"])
        try:
            data = haal(commit, bron_pad)
        except FileNotFoundError as exc:
            u.fouten.append(str(exc))
            return u
        digest = sha256(data)
        kop = _kop(pin)
        kopregels = maak_kop(soort, kopie, versie, commit, bron_pad, digest) if kop else ""
        if kop and kopregels.count("\n") != kop - 1:
            u.fouten.append(f"{kopie}: herkomstkop heeft niet {kop} regels")
            return u
        (root / kopie).parent.mkdir(parents=True, exist_ok=True)
        (root / kopie).write_bytes((kopregels + "\n").encode("utf-8") + data if kop else data)
        nieuw[kopie] = {**pin, "sha256": digest}
    herkomst.update(
        {
            "versie": versie,
            "bron_commit": commit,
            "bron_ref": ref or herkomst.get("bron_ref", ""),
            "bestanden": nieuw,
        }
    )
    (root / SETS[soort]).write_text(
        json.dumps(herkomst, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )
    return controleer(root, (soort,))


# -------------------------------------------------------------------------------------------- main
def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(description="Byte-gelijke kopieën van contract en Peil bewaken.")
    sub = p.add_subparsers(dest="opdracht", required=True)
    c = sub.add_parser("controleer", help="offline: HERKOMST.json, koppen en sha256")
    c.add_argument("--set", choices=sorted(SETS), action="append")
    v = sub.add_parser("vergelijk", help="tegen de bron (lokale checkout en/of GitHub)")
    v.add_argument("--set", choices=sorted(SETS), action="append")
    v.add_argument("--bron", type=Path, help="checkout van ai-server-manager (git show)")
    v.add_argument("--online", action="store_true", help="tegen GitHub (raw, publieke repo)")
    v.add_argument(
        "--ref", help="huidige bron: branch of commit (standaard bron_ref uit HERKOMST.json)"
    )
    b = sub.add_parser(
        "bijwerken", help="kopieën en HERKOMST.json opnieuw schrijven vanuit een checkout"
    )
    b.add_argument("--set", choices=sorted(SETS), required=True)
    b.add_argument("--bron", type=Path, required=True)
    b.add_argument("--commit", required=True)
    b.add_argument("--ref")
    args = p.parse_args(argv)
    if args.opdracht == "controleer":
        u = controleer(ROOT, tuple(args.set or SETS))
    elif args.opdracht == "vergelijk":
        if args.bron is None and not args.online:
            p.error("geef --bron en/of --online")
        u = Uitkomst()
        if args.bron is not None:
            r = vergelijk(
                haler_lokaal(args.bron), "lokaal", ROOT, tuple(args.set or SETS), args.ref
            )
            u.fouten += r.fouten
            u.waarschuwingen += r.waarschuwingen
        if args.online:
            r = vergelijk(haler_online(), "online", ROOT, tuple(args.set or SETS), args.ref)
            u.fouten += r.fouten
            u.waarschuwingen += r.waarschuwingen
    else:
        u = bijwerken(args.set, args.bron, args.commit, args.ref)
    ci = os.environ.get("GITHUB_ACTIONS") == "true"
    for w in u.waarschuwingen:
        print(f"::warning::kopie: {w}" if ci else f"waarschuwing: {w}")
    for f in u.fouten:
        print(f"::error::kopie: {f}" if ci else f"fout: {f}", file=sys.stderr)
    if not u.fouten:
        print(f"Kopieën in orde ({args.opdracht}).")
    return 1 if u.fouten else 0


if __name__ == "__main__":
    sys.exit(main())
