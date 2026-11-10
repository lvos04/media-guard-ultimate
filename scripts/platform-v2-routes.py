#!/usr/bin/env python3
"""Routetabel en contractprobe van mediaguard-core voor de platform-v2-inventaris (alleen lezen).

Bouwt de echte FastAPI-app (``create_app``) in een tijdelijke map, zonder achtergrondtaken
en zonder netwerk naar buiten. Wijzigt niets aan de repo of aan een database buiten de
tijdelijke map; geen productcode.

    .venv/bin/python scripts/platform-v2-routes.py            # Markdown-tabel van alle routes
    .venv/bin/python scripts/platform-v2-routes.py --json     # dezelfde routes als JSON
    .venv/bin/python scripts/platform-v2-routes.py --probe    # contractprobe met API-sleutels

``--probe`` maakt in de tijdelijke app een beheerder, een viewer- en een operator-API-sleutel
(zoals de Hub ze zou krijgen) en bevraagt daarmee de bestaande routes én de routes uit het
connectorcontract (PLATFORM_CONTRACTEN §5/§6). Uitkomst = HTTP-status + vorm van de
foutbody. Dit is een in-process probe (FastAPI TestClient), geen test op een echte NAS.

Gebruikt door docs/platform-v2/INVENTARIS.md (bijlage A) en DELTA_MATRIX.md.
"""

from __future__ import annotations

import argparse
import inspect
import json
import shutil
import sys
import tempfile
from collections.abc import Iterator
from contextlib import contextmanager
from pathlib import Path
from typing import Any

REPO = Path(__file__).resolve().parents[1]


def _role(dependant: Any) -> str:
    stack = list(dependant.dependencies)
    while stack:
        dep = stack.pop(0)
        call = dep.call
        if getattr(call, "__qualname__", "") == "require.<locals>.dep" and call.__closure__:
            return str(call.__closure__[0].cell_contents)
        if getattr(call, "__name__", "") == "current_principal":
            return "ingelogd"
        stack.extend(dep.dependencies)
    return "geen"


@contextmanager
def _temp_app() -> Iterator[Any]:
    from mediaguard.app import create_app
    from mediaguard.config import load_settings

    tmp = Path(tempfile.mkdtemp(prefix="mg-routes-"))
    settings = load_settings(data_dir=tmp / "config", staging_dir=tmp / "staging",
                             quarantine_dir=tmp / "quarantine", start_background=False,
                             internal_token="alleen-voor-inventaris")
    app = create_app(settings)
    try:
        yield app
    finally:
        app.state.ctx.db.close()
        shutil.rmtree(tmp, ignore_errors=True)


def collect() -> list[dict[str, Any]]:
    from fastapi.routing import APIRoute, APIWebSocketRoute

    rows: list[dict[str, Any]] = []

    def add(route: Any, prefix: str) -> None:
        fn = route.endpoint
        try:
            src = Path(inspect.getsourcefile(fn) or "").resolve()
            line = inspect.getsourcelines(fn)[1]
            rel = str(src.relative_to(REPO))
        except (OSError, TypeError, ValueError):
            rel, line = "?", 0
        rows.append({"methods": sorted(getattr(route, "methods", None) or ["WS"]),
                     "path": prefix + route.path, "role": _role(route.dependant),
                     "file": rel, "line": line, "func": fn.__name__,
                     "in_schema": bool(getattr(route, "include_in_schema", True))})

    def walk(routes: list[Any], prefix: str) -> None:
        for route in routes:
            if type(route).__name__ == "_IncludedRouter":  # FastAPI >= 0.16x: lui ingevoegd
                walk(route.original_router.routes, prefix + route.include_context.prefix)
            elif isinstance(route, (APIRoute, APIWebSocketRoute)):
                add(route, prefix)
            elif hasattr(route, "routes") and hasattr(route, "prefix"):
                walk(route.routes, prefix)

    with _temp_app() as app:
        walk(app.routes, "")
    rows.sort(key=lambda r: (r["path"], ",".join(r["methods"])))
    return rows


def markdown(rows: list[dict[str, Any]]) -> str:
    out = ["| Methode | Pad | Min. rol | Bestand:regel |", "|---|---|---|---|"]
    for r in rows:
        methods = ",".join(m for m in r["methods"] if m != "HEAD")
        out.append(f"| {methods} | `{r['path']}` | {r['role']} | `{r['file']}:{r['line']}` |")
    return "\n".join(out)


# (label, methode, pad, sleutel, body) — sleutel: none | viewer | operator | revoked
PROBES: list[tuple[str, str, str, str, dict[str, Any] | None]] = [
    ("liveness bestaand", "GET", "/api/v1/health", "none", None),
    ("eigen rol", "GET", "/api/v1/auth/me", "viewer", None),
    ("status + noodstop", "GET", "/api/v1/status", "viewer", None),
    ("versie + schema + manifest", "GET", "/api/v1/admin/version", "viewer", None),
    ("gezondheidsmodel", "GET", "/api/v1/health/summary", "viewer", None),
    ("componenten", "GET", "/api/v1/admin/health", "viewer", None),
    ("wanted (pagina)", "GET", "/api/v1/wanted?page_size=1", "viewer", None),
    ("beleid", "GET", "/api/v1/policy/effective", "viewer", None),
    ("aandacht/incidenten", "GET", "/api/v1/attention", "viewer", None),
    ("meldingen", "GET", "/api/v1/notifications?limit=1", "viewer", None),
    ("events (outbox)", "GET", "/api/v1/events/recent?limit=1", "viewer", None),
    ("audit", "GET", "/api/v1/audit?limit=1", "viewer", None),
    ("back-ups (lijst)", "GET", "/api/v1/backups", "viewer", None),
    ("OpenAPI met viewer-sleutel", "GET", "/api/v1/openapi.json", "viewer", None),
    ("OpenAPI met operator-sleutel", "GET", "/api/v1/openapi.json", "operator", None),
    ("contract §5 healthz", "GET", "/healthz", "none", None),
    ("contract §5 readyz", "GET", "/readyz", "none", None),
    ("contract §6 discover", "GET", "/connector/v1/discover", "viewer", None),
    ("contract §6 health", "GET", "/connector/v1/health", "viewer", None),
    ("contract §6 items", "GET", "/connector/v1/items?type=wanted", "viewer", None),
    ("contract §6 events", "GET", "/connector/v1/events", "viewer", None),
    ("contract §6 propose", "POST", "/connector/v1/propose", "operator", {}),
    ("contract §6 apply", "POST", "/connector/v1/apply", "operator", {}),
    ("contract §6 rollback", "POST", "/connector/v1/rollback", "operator", {}),
    ("noodstop aan met viewer-sleutel", "POST", "/api/v1/system/emergency-stop", "viewer",
     {"active": True, "reason": "probe"}),
    ("noodstop aan met operator-sleutel", "POST", "/api/v1/system/emergency-stop", "operator",
     {"active": True, "reason": "probe platform-v2"}),
    ("status tijdens noodstop", "GET", "/api/v1/status", "viewer", None),
    ("noodstop uit met operator-sleutel", "POST", "/api/v1/system/emergency-stop", "operator",
     {"active": False, "reason": "probe platform-v2"}),
    ("ingetrokken sleutel", "GET", "/api/v1/status", "revoked", None),
]


def _shape(resp: Any) -> str:
    try:
        body = resp.json()
    except ValueError:
        return "geen JSON"
    if isinstance(body, dict):
        keys = sorted(body)[:6]
        return "{" + ",".join(keys) + ("" if len(body) <= 6 else ",…") + "}"
    if isinstance(body, list):
        return f"[{len(body)} items]"
    return type(body).__name__


def probe() -> list[dict[str, Any]]:
    from fastapi.testclient import TestClient

    results: list[dict[str, Any]] = []
    with _temp_app() as app, TestClient(app) as c:
        r = c.post("/api/v1/auth/setup",
                   json={"username": "inventaris", "password": "alleen-voor-de-probe"})
        r.raise_for_status()
        csrf = {"X-CSRF-Token": r.json()["csrf_token"]}
        keys: dict[str, str] = {}
        for name, role in (("hub-viewer", "viewer"), ("hub-operator", "operator"),
                           ("hub-ingetrokken", "viewer")):
            created = c.post("/api/v1/auth/api-keys", json={"name": name, "role": role},
                             headers=csrf)
            created.raise_for_status()
            keys[name] = created.json()["key"]
            if name == "hub-ingetrokken":
                c.post(f"/api/v1/auth/api-keys/{created.json()['id']}/revoke",
                       headers=csrf).raise_for_status()
        c.cookies.clear()  # vanaf hier alleen API-sleutels, geen sessie
        header = {"viewer": keys["hub-viewer"], "operator": keys["hub-operator"],
                  "revoked": keys["hub-ingetrokken"]}
        for label, method, path, key, body in PROBES:
            headers = {} if key == "none" else {"X-Api-Key": header[key]}
            resp = c.request(method, path, headers=headers, json=body)
            results.append({"label": label, "method": method, "path": path, "key": key,
                            "status": resp.status_code, "body_shape": _shape(resp)})
    return results


def probe_markdown(rows: list[dict[str, Any]]) -> str:
    out = ["| Probe | Methode | Pad | Sleutel | HTTP | Body (sleutels) |",
           "|---|---|---|---|---|---|"]
    for r in rows:
        out.append(f"| {r['label']} | {r['method']} | `{r['path']}` | {r['key']} | "
                   f"{r['status']} | `{r['body_shape']}` |")
    return "\n".join(out)


def main() -> int:
    parser = argparse.ArgumentParser(description=(__doc__ or "").splitlines()[0])
    parser.add_argument("--json", action="store_true", help="JSON in plaats van Markdown")
    parser.add_argument("--probe", action="store_true",
                        help="contractprobe met viewer-/operator-API-sleutels (in-process)")
    args = parser.parse_args()
    if args.probe:
        rows = probe()
        text = json.dumps(rows, indent=1, ensure_ascii=False) if args.json else \
            probe_markdown(rows)
    else:
        routes = collect()
        text = json.dumps(routes, indent=1, ensure_ascii=False) if args.json else \
            markdown(routes)
    sys.stdout.write(text + "\n")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
