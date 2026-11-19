#!/usr/bin/env python3
"""Lees de MSO-routes uit een Git-commit en schrijf de gepinde JSON naar stdout."""

import argparse
import ast
import json
import subprocess
from datetime import date
from pathlib import Path

PAD = "apps/core/mso/features/mediaguard/compat.py"
VELDEN = ("id", "method", "path", "role")
UITZONDERING = {
    "method": "GET",
    "path": "/api/v1/openapi.json",
    "mso_role": "none",
    "mediaguard_role": "admin",
    "reden": (
        "MediaGuard beperkt het OpenAPI-document tot beheerders. "
        "MSO valt bij weigering terug op directe routeprobes."
    ),
}


def lees_routes(bron):
    module = ast.parse(bron, filename=PAD)
    route_spec = next(
        node for node in module.body if isinstance(node, ast.ClassDef) and node.name == "RouteSpec"
    )
    velden = [node.target.id for node in route_spec.body if isinstance(node, ast.AnnAssign)]
    if tuple(velden[:len(VELDEN)]) != VELDEN:
        raise ValueError("RouteSpec heeft een andere veldvolgorde")

    routes = next(
        node.value for node in module.body
        if isinstance(node, ast.AnnAssign) and node.target.id == "ROUTES"
    )
    if not isinstance(routes, ast.List) or not routes.elts:
        raise ValueError("ROUTES is geen gevulde lijst")

    resultaat = []
    for route in routes.elts:
        if not (
            isinstance(route, ast.Call)
            and isinstance(route.func, ast.Name)
            and route.func.id == "RouteSpec"
            and len(route.args) >= len(VELDEN)
        ):
            raise ValueError("Verwacht een RouteSpec met positionele contractvelden")
        waarden = [ast.literal_eval(arg) for arg in route.args[:len(VELDEN)]]
        if not all(isinstance(waarde, str) and waarde for waarde in waarden):
            raise ValueError("Verwacht tekstwaarden voor id, methode, pad en rol")
        resultaat.append(dict(zip(VELDEN, waarden, strict=True)))
    return resultaat


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("mso_repo", type=Path, help="pad naar de lokale MSO-werkkopie")
    parser.add_argument("commit", help="MSO-commit waaruit compat.py wordt gelezen")
    parser.add_argument("--datum", type=date.fromisoformat, required=True, help="datum van de pin")
    args = parser.parse_args()

    git = ["git", "-C", str(args.mso_repo)]
    commit = subprocess.check_output(
        [*git, "rev-parse", "--verify", "--end-of-options", f"{args.commit}^{{commit}}"],
        encoding="utf-8",
    ).strip()
    bron = subprocess.check_output([*git, "show", f"{commit}:{PAD}"], encoding="utf-8")
    try:
        routes = lees_routes(bron)
    except (StopIteration, ValueError, SyntaxError) as fout:
        parser.error(f"Kan ROUTES niet lezen uit {PAD}: {fout}")
    pin = {
        "bron": f"media-source-optimizer@{commit}:{PAD}",
        "datum": args.datum.isoformat(),
        "uitzondering": UITZONDERING,
        "routes": routes,
    }
    print(json.dumps(pin, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
