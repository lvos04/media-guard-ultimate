"""OpenAPI-export actueel (platformcontract §5: CI faalt bij drift).

``docs/openapi/mediaguard.openapi.json`` is de geëxporteerde OpenAPI van core. Deze test bouwt
de app opnieuw en vergelijkt byte voor byte. Wijkt hij af, dan is er een API-, route- of
schemawijziging: bewust? Draai dan ``python scripts/export-openapi.py`` en commit het bestand
mee (ADR-0020).
"""

from __future__ import annotations

import json
from pathlib import Path

EXPORT = Path(__file__).resolve().parents[3] / "docs" / "openapi" / "mediaguard.openapi.json"


def render(spec: dict) -> str:
    # zelfde vorm als scripts/export-openapi.py
    return json.dumps(spec, indent=2, ensure_ascii=False, sort_keys=True) + "\n"


def test_openapi_export_is_current(app) -> None:  # type: ignore[no-untyped-def]
    assert EXPORT.is_file(), "docs/openapi/mediaguard.openapi.json ontbreekt"
    committed = EXPORT.read_text(encoding="utf-8")
    current = render(app.openapi())
    if committed != current:
        old, new = json.loads(committed), json.loads(current)
        added = sorted(set(new["paths"]) - set(old["paths"]))
        removed = sorted(set(old["paths"]) - set(new["paths"]))
        raise AssertionError(
            "OpenAPI-drift: draai scripts/export-openapi.py en controleer de wijziging. "
            f"Nieuwe paden: {added[:10]}; verdwenen paden: {removed[:10]}")


def test_openapi_export_has_no_secrets_or_hosts() -> None:
    text = EXPORT.read_text(encoding="utf-8")
    spec = json.loads(text)
    assert spec["openapi"].startswith("3.")
    assert "servers" not in spec, "geen vast serveradres in de export"
    for word in ("192.168.", "localhost:", "BEGIN PRIVATE KEY", "sk-ant-", "ghp_"):
        assert word not in text, f"export bevat {word!r}"
