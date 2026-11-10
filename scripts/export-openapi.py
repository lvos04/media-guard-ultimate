#!/usr/bin/env python3
"""Exporteert de OpenAPI van mediaguard-core naar ``docs/openapi/mediaguard.openapi.json``.

Platformcontract §5: elke dienst exporteert zijn OpenAPI en CI faalt bij drift. Bouwt de echte
FastAPI-app (``create_app``) in een tijdelijke datamap, zonder achtergrondtaken en zonder
netwerk; wijzigt alleen het exportbestand. De drift-controle in CI is
``apps/core/tests/test_openapi_export.py`` (zelfde serialisatie).

    python scripts/export-openapi.py           # schrijft het bestand opnieuw
    python scripts/export-openapi.py --check   # exitcode 1 als het bestand afwijkt

Draai met de Python-omgeving van core (``pip install -e apps/core``).
"""

from __future__ import annotations

import argparse
import json
import os
import sys
import tempfile
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
TARGET = REPO / "docs" / "openapi" / "mediaguard.openapi.json"


def render(spec: dict) -> str:
    """Vaste vorm: sleutels gesorteerd, 2 spaties, UTF-8 zonder escapes, slotregel."""
    return json.dumps(spec, indent=2, ensure_ascii=False, sort_keys=True) + "\n"


def build() -> str:
    with tempfile.TemporaryDirectory(prefix="mg-openapi-") as data:
        os.environ["MG_DATA_DIR"] = data
        os.environ.setdefault("MG_START_BACKGROUND", "0")
        from mediaguard.app import create_app

        app = create_app()
        try:
            return render(app.openapi())
        finally:
            app.state.ctx.db.close()


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--check", action="store_true", help="alleen vergelijken, niets schrijven")
    args = parser.parse_args()
    text = build()
    if args.check:
        current = TARGET.read_text(encoding="utf-8") if TARGET.exists() else ""
        if current != text:
            print(f"OpenAPI-drift: {TARGET.relative_to(REPO)} wijkt af van de app; "
                  "draai scripts/export-openapi.py en controleer de wijziging.", file=sys.stderr)
            return 1
        print(f"ok  {TARGET.relative_to(REPO)} is actueel")
        return 0
    TARGET.parent.mkdir(parents=True, exist_ok=True)
    TARGET.write_text(text, encoding="utf-8")
    print(f"geschreven: {TARGET.relative_to(REPO)} ({len(json.loads(text)['paths'])} paden)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
