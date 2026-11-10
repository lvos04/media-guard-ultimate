#!/usr/bin/env python3
"""Docker-healthcheck: vraag de MediaGuard-health op met het interne token.

Alleen standaardbibliotheek, zodat de check ook werkt als de app-venv stuk is.
"""

import json
import os
import sys
import urllib.request

port = os.environ.get("MG_PORT", "8000")
# Zelfde bron als app/mediaguard/auth.py: env-variabele of het door mediaguard-init
# gegenereerde tokenbestand (in de alles-in-één-image staat alleen het bestand).
token = os.environ.get("MG_INTERNAL_TOKEN", "")
if not token and os.environ.get("MG_INTERNAL_TOKEN_FILE"):
    try:
        with open(os.environ["MG_INTERNAL_TOKEN_FILE"], encoding="utf-8") as handle:
            token = handle.read().strip()
    except OSError:
        token = ""
request = urllib.request.Request(
    f"http://127.0.0.1:{port}/api/mediaguard/v1/health",
    headers={"X-MediaGuard-Token": token},
)
try:
    with urllib.request.urlopen(request, timeout=8) as response:
        body = json.loads(response.read().decode("utf-8"))
except Exception as exc:  # noqa: BLE001 - iedere fout betekent: niet gezond
    print(f"unhealthy: {exc}", file=sys.stderr)
    sys.exit(1)
# "degraded" (bv. GPU ontbreekt) is nog steeds een draaiende, bruikbare CPU-baan.
sys.exit(0 if body.get("status") in {"ok", "degraded"} else 1)
