#!/usr/bin/env python3
"""Genereert app/src/main/java/nl/mediaguard/app/ui/theme/PeilTokens.kt uit design/ontwerptaal/tokens.json.

Peil (de ontwerptaal van het platform) levert zijn tokens als DTCG-JSON: licht = ``$value``, donker =
``$extensions["nl.peil"].modes.donker``. De Android-app krijgt daaruit twee sets kleuren (licht en donker),
de typografierollen en de radius. Niet met de hand wijzigen: ``PeilTokensTest`` vergelijkt elke waarde met
tokens.json en faalt bij verschil (docs/ontwerp/PEIL_MIGRATIE.md §10.1, ADR-0018).

    python3 apps/android/tools/peil_tokens.py           # schrijft het bestand
    python3 apps/android/tools/peil_tokens.py --check   # alleen controleren (exit 1 bij verschil)
"""

from __future__ import annotations

import hashlib
import json
import re
import sys
from pathlib import Path

HIER = Path(__file__).resolve().parent
REPO = HIER.parents[2]
TOKENS = REPO / "design" / "ontwerptaal" / "tokens.json"
DOEL = HIER.parent / "app/src/main/java/nl/mediaguard/app/ui/theme/PeilTokens.kt"

# Kleurgroepen die de app meekrijgt; "alias" zijn verwijzingen ({kleur.tekst.foreground}) en blijven weg.
GROEPEN = ["oppervlak", "tekst", "rand", "interactie", "status", "versheid", "risico", "as", "grafiek", "domein"]


def camel(naam: str) -> str:
    delen = naam.split("-")
    return delen[0] + "".join(d[:1].upper() + d[1:] for d in delen[1:])


def kleur(hexwaarde: str) -> str:
    h = hexwaarde.lstrip("#").upper()
    if not re.fullmatch(r"[0-9A-F]{6}", h):
        raise SystemExit(f"geen #RRGGBB-kleur: {hexwaarde}")
    return f"Color(0xFF{h})"


def rem_naar_sp(waarde: str | int | float) -> str:
    if isinstance(waarde, (int, float)):
        return f"{waarde}"
    m = re.fullmatch(r"([\d.]+)rem", waarde)
    if m:
        return f"{float(m.group(1)) * 16:g}"
    if re.fullmatch(r"[\d.]+", waarde):  # regelhoogte als factor ("1")
        return waarde
    raise SystemExit(f"onbekende maat: {waarde}")


def genereer() -> str:
    ruw = TOKENS.read_bytes()
    sha = hashlib.sha256(ruw).hexdigest()
    d = json.loads(ruw)
    versie = d["$extensions"]["nl.peil"]["versie"]

    velden: list[tuple[str, str, str, str]] = []  # (kotlin-naam, tokenpad, licht, donker)
    for groep in GROEPEN:
        for naam, t in d["kleur"][groep].items():
            if naam.startswith("$"):
                continue
            licht = t["$value"]
            donker = t["$extensions"]["nl.peil"]["modes"]["donker"]
            velden.append((camel(naam), f"kleur.{groep}.{naam}", licht, donker))
    namen = [v[0] for v in velden]
    if len(set(namen)) != len(namen):
        raise SystemExit("dubbele tokennaam na omzetting naar camelCase")

    rollen = []
    for naam, t in d["typografie"]["rol"].items():
        if naam.startswith("$"):
            continue
        v = t["$value"]
        regel = rem_naar_sp(v["lineHeight"])
        grootte = rem_naar_sp(v["fontSize"])
        if "." not in regel and float(regel) < 4:  # regelhoogte "1" (held) is een factor
            regel = f"{float(regel) * float(grootte):g}"
        rollen.append((naam, grootte, regel, int(v["fontWeight"])))

    radius = rem_naar_sp(d["maat"]["radius"]["$value"])
    touch = rem_naar_sp(d["maat"]["touch"]["$value"])

    r: list[str] = []
    r.append(f"// Peil {versie} · design/ontwerptaal/tokens.json sha256:{sha}")
    r.append("// Gegenereerd door apps/android/tools/peil_tokens.py — niet met de hand wijzigen;")
    r.append("// PeilTokensTest vergelijkt elke waarde met tokens.json (docs/ontwerp/PEIL_MIGRATIE.md §10.1).")
    r.append("package nl.mediaguard.app.ui.theme")
    r.append("")
    r.append("import androidx.compose.runtime.Immutable")
    r.append("import androidx.compose.ui.graphics.Color")
    r.append("")
    r.append("/** Alle Peil-kleuren van één modus (licht of donker), met de namen uit tokens.json in camelCase. */")
    r.append("@Immutable")
    r.append("data class PeilKleuren(")
    for naam, pad, _, _ in velden:
        r.append(f"    /** `{pad}` */")
        r.append(f"    val {naam}: Color,")
    r.append(")")
    r.append("")
    r.append("/** Een typografierol van Peil: lettergrootte en regelhoogte in sp (1 rem = 16), gewicht 100–900. */")
    r.append("data class PeilRol(val grootteSp: Float, val regelSp: Float, val gewicht: Int)")
    r.append("")
    r.append("object PeilTokens {")
    r.append(f'    const val VERSIE = "{versie}"')
    r.append("")
    r.append("    /** sha256 van design/ontwerptaal/tokens.json waaruit dit bestand is gemaakt. */")
    r.append(f'    const val TOKENS_SHA256 = "{sha}"')
    r.append("")
    r.append("    /** `maat.radius` in dp (kaart, knop, veld). */")
    r.append(f"    const val RADIUS_DP = {float(radius):g}f")
    r.append("")
    r.append("    /** `maat.touch` in dp (Peil: 44 px); Android gebruikt 48 dp (ADR-0016 punt 11). */")
    r.append(f"    const val TOUCH_DP = {float(touch):g}f")
    r.append("")
    for modus, idx in (("Licht", 2), ("Donker", 3)):
        r.append(f"    val {modus} = PeilKleuren(")
        for v in velden:
            r.append(f"        {v[0]} = {kleur(v[idx])},")
        r.append("    )")
        r.append("")
    r.append("    /** `typografie.rol.*` */")
    r.append("    val rollen: Map<String, PeilRol> = mapOf(")
    for naam, grootte, regel, gewicht in rollen:
        r.append(f'        "{naam}" to PeilRol({float(grootte):g}f, {float(regel):g}f, {gewicht}),')
    r.append("    )")
    r.append("")
    r.append("    /** Tokenpad → kleur, voor de drifttest en voor wie op naam zoekt. */")
    r.append("    fun perPad(k: PeilKleuren): Map<String, Color> = mapOf(")
    for naam, pad, _, _ in velden:
        r.append(f'        "{pad}" to k.{naam},')
    r.append("    )")
    r.append("}")
    r.append("")
    return "\n".join(r)


def main() -> int:
    tekst = genereer()
    if "--check" in sys.argv:
        huidig = DOEL.read_text(encoding="utf-8") if DOEL.exists() else ""
        if huidig != tekst:
            print(f"{DOEL} wijkt af van tokens.json; draai apps/android/tools/peil_tokens.py", file=sys.stderr)
            return 1
        print("PeilTokens.kt is gelijk aan tokens.json")
        return 0
    DOEL.write_text(tekst, encoding="utf-8")
    print(f"geschreven: {DOEL}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
