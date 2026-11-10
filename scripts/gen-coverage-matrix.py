#!/usr/bin/env python3
"""Genereert docs/UI_CONTROL_COVERAGE_MATRIX.md uit het instellingenregister van core.

Gebruik (met de core-venv):  python scripts/gen-coverage-matrix.py
Bestaande kolommen 'Waar in de UI' en 'Test' blijven per key behouden; nieuwe keys krijgen de
generieke schema-gedreven locatie. apps/core/tests/test_coverage_matrix.py faalt zodra de
matrix niet meer overeenkomt met het register.
"""

from __future__ import annotations

import re
import sys
from datetime import date
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "apps" / "core"))

from mediaguard.app import _load_modules  # noqa: E402
from mediaguard.settings_registry import REGISTRY, SCOPE_ORDER  # noqa: E402

DOC = ROOT / "docs" / "UI_CONTROL_COVERAGE_MATRIX.md"
CONTROL = {"bool": "schakelaar", "enum": "keuzelijst", "list": "chips / komma-lijst",
           "int": "getalveld", "float": "getalveld", "duration": "duurveld",
           "time_window": "tijdvenster", "string": "tekstveld", "json": "editor",
           "secret": "geheimveld"}
GENERIC_TEST = "generiek: render+validatie via schema (E2E-pad gedekt door jobs.max_concurrency)"


def encoder_knobs() -> list[list[object]]:
    """Knoppen uit services/encoder/app/afstelling.py, zonder de encoder te importeren."""
    import ast

    src = (ROOT / "services" / "encoder" / "app" / "afstelling.py").read_text(encoding="utf-8")
    out = []
    for node in ast.walk(ast.parse(src)):
        if isinstance(node, ast.Call) and getattr(node.func, "id", None) == "Knob":
            out.append([a.value if isinstance(a, ast.Constant) else None for a in node.args])
    return out


def encoder_section() -> str:
    rows = ["", "## Encoder-afstelling", "",
            "Uitvoeringsknoppen van de AV1-engine (`GET/PUT /api/v1/encoder/afstelling`). De UI "
            "(Ruimte › Instellingen › Uitvoering) rendert ze generiek met naam, uitleg, bereik en "
            "standaard; een omgevingsvariabele kan een knop vastzetten (dan alleen-lezen).", "",
            "| Key | Label | Type | UI-control | Scopes | Waar in de UI | Test |",
            "|---|---|---|---|---|---|---|"]
    for k in encoder_knobs():
        rows.append(f"| `encoder.{k[0]}` | {k[6]} | {k[1]} | {CONTROL.get(str(k[1]), k[1])} | "
                    "engine | Ruimte › Instellingen › Uitvoering | generiek: afstelling-lijst "
                    "(e2e werk-r035) |")
    return "\n".join(rows) + "\n"


def main() -> int:
    _load_modules()
    defs = REGISTRY.all()
    old = DOC.read_text(encoding="utf-8") if DOC.exists() else ""
    known: dict[str, tuple[str, str]] = {}
    for line in old.splitlines():
        m = re.match(r"\| `([^`]+)` \|(.*)\|", line)
        if m:
            cols = [c.strip() for c in m.group(2).split("|")]
            if len(cols) >= 6:
                known[m.group(1)] = (cols[4], cols[5])
    head = old.split("| Key |", 1)[0] if "| Key |" in old else "# UI Control Coverage Matrix\n\n"
    head = re.sub(r"Gegenereerd op [^\n]*\n",
                  f"Gegenereerd op {date.today().isoformat()} met `scripts/gen-coverage-matrix.py` "
                  f"uit het instellingenregister van core ({len(defs)} instellingen; scopes: "
                  f"{', '.join(SCOPE_ORDER)}).\n", head, count=1)
    rows = ["| Key | Label | Type | UI-control | Scopes | Waar in de UI | Test |",
            "|---|---|---|---|---|---|---|"]
    for d in defs:
        where, test = known.get(d.key, (f"Instellingen › {d.category}", GENERIC_TEST))
        control = "vergrendeld (alleen-lezen)" if d.integrity_locked else CONTROL.get(d.type, d.type)
        rows.append(f"| `{d.key}` | {d.label_nl} | {d.type} | {control} | "
                    f"{', '.join(d.scopes)} | {where} | {test} |")
    tail = ""
    if "\n## " in old.split("| Key |", 1)[-1]:
        tail = "\n## " + old.split("| Key |", 1)[-1].split("\n## ", 1)[1]
    # encoder-afstelling (GET/PUT /api/afstelling) als eigen sectie, altijd opnieuw opgebouwd
    tail = re.sub(r"\n## Encoder-afstelling\n.*?(?=\n## |\Z)", "", tail, flags=re.S)
    tail += encoder_section()
    DOC.write_text(head + "\n".join(rows) + "\n" + tail, encoding="utf-8")
    print(f"{DOC.relative_to(ROOT)}: {len(defs)} instellingen")
    return 0


if __name__ == "__main__":
    sys.exit(main())
