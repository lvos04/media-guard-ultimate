"""Q2.3: iedere geregistreerde instelling staat in de UI-coverage-matrix (en omgekeerd)."""

from __future__ import annotations

import re
from pathlib import Path


def test_matrix_matches_registry(app):  # noqa: ARG001 - app laadt alle modules/instellingen
    from mediaguard.settings_registry import REGISTRY

    doc = Path(__file__).resolve().parents[3] / "docs" / "UI_CONTROL_COVERAGE_MATRIX.md"
    listed = {k for k in re.findall(r"^\| `([^`]+)` \|", doc.read_text(encoding="utf-8"), re.M)
              if not k.startswith("encoder.")}  # encoder-afstelling: eigen sectie/test
    registered = {d.key for d in REGISTRY.all()}
    assert listed == registered, (
        f"ontbreekt: {sorted(registered - listed)}, overbodig: {sorted(listed - registered)} — "
        "draai scripts/gen-coverage-matrix.py")


def test_every_setting_has_dutch_label_and_explanation(app):  # noqa: ARG001
    """Rapport 5: 'alles kunnen instellen in de app' — een instelling zonder Nederlandse naam
    of uitleg is in de UI onbegrijpelijk. Voeg ``help_nl`` toe bij de registratie of in
    ``mediaguard/settings_help.py``."""
    from mediaguard.settings_registry import REGISTRY

    missing = [d.key for d in REGISTRY.all()
               if not (d.label_nl or "").strip() or not (d.help_nl or "").strip()]
    assert not missing, f"instellingen zonder Nederlandse naam/uitleg: {missing}"


def test_encoder_tuning_is_in_matrix():
    """De uitvoeringsafstelling van de encoder (GET/PUT /api/afstelling) staat in de matrix,
    met een Nederlandse naam en uitleg per knop; de UI rendert ze generiek."""
    import ast

    root = Path(__file__).resolve().parents[3]
    src = (root / "services" / "encoder" / "app" / "afstelling.py").read_text(encoding="utf-8")
    knobs = []
    for node in ast.walk(ast.parse(src)):
        if isinstance(node, ast.Call) and getattr(node.func, "id", None) == "Knob":
            args = [a.value if isinstance(a, ast.Constant) else None for a in node.args]
            knobs.append(args)
    assert knobs, "geen encoder-knoppen gevonden"
    doc = (root / "docs" / "UI_CONTROL_COVERAGE_MATRIX.md").read_text(encoding="utf-8")
    listed = set(re.findall(r"^\| `encoder\.([^`]+)` \|", doc, re.M))
    keys = {k[0] for k in knobs}
    assert listed == keys, (
        f"encoder-afstelling ontbreekt in de matrix: {sorted(keys - listed)}, "
        f"overbodig: {sorted(listed - keys)} — draai scripts/gen-coverage-matrix.py")
    for k in knobs:
        assert (k[6] or "").strip() and (k[7] or "").strip(), \
            f"encoder-knop zonder naam/uitleg: {k[0]}"
