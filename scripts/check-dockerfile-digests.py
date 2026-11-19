#!/usr/bin/env python3
"""Weiger externe Dockerfile-bases zonder versietag en volledige sha256-digest."""

import argparse
import re
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
AIO = ROOT / "deploy/aio/Dockerfile"
# Alleen deze vier ARG's worden door de release uit dezelfde run of een vaste fork-tag ingevuld.
AIO_COMPONENTS = {
    "ENCODE_IMAGE": "ghcr.io/lvos04/mediaguard-encode:dev",
    "WEB_IMAGE": "ghcr.io/lvos04/mediaguard-web:dev",
    "SONARR_IMAGE": "ghcr.io/lvos04/mediaguard-sonarr-fork:dev",
    "RADARR_IMAGE": "ghcr.io/lvos04/mediaguard-radarr-fork:dev",
}
PIN = re.compile(r"[^\s@$]+:(?!(?i:latest)@)[\w][\w.-]*@sha256:[a-f0-9]{64}", re.ASCII)
ARG_REF = re.compile(r"\$(?:\{(\w+)\}|(\w+))")


def instructions(path):
    pending = ""
    start = 0
    for number, line in enumerate(path.read_text(encoding="utf-8").splitlines(), 1):
        line = line.strip()
        if not line or line.startswith("#"):
            continue
        if not pending:
            start = number
        if line.endswith("\\"):
            pending += line[:-1]
            continue
        fields = (pending + line).split(maxsplit=1)
        pending = ""
        if len(fields) == 2:
            yield start, fields[0].upper(), fields[1]


def check(path):
    args = {}
    stages = {"scratch"}
    before_from = True

    def pinned(value, name=None):
        return bool(PIN.fullmatch(value)) or (
            path.resolve() == AIO and name in AIO_COMPONENTS and AIO_COMPONENTS[name] == value
        )

    for number, instruction, options in instructions(path):
        if instruction == "ARG":
            name, equal, value = options.partition("=")
            name = name.strip()
            value = value.strip().strip("'\"") if equal else args.get(name, "")
            if before_from:
                args[name] = value
            if name.endswith("_IMAGE") and not pinned(value, name):
                yield number, f"ARG {name}"
        if instruction == "FROM":
            before_from = False
            fields = options.split()
            if fields[0].startswith("--platform="):
                fields = fields[1:]
            ref = fields[0] if fields else ""
            variable = ARG_REF.fullmatch(ref)
            if variable:
                name = variable[1] or variable[2]
                allowed = pinned(args.get(name, ""), name)
            else:
                allowed = ref.lower() in stages or pinned(ref)
            if not allowed:
                yield number, f"FROM {ref}"
            if len(fields) == 3 and fields[1].lower() == "as":
                stages.add(fields[2].lower())


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("dockerfiles", nargs="*", type=Path)
    paths = parser.parse_args().dockerfiles
    if not paths:
        tracked = subprocess.check_output(
            ["git", "ls-files", "-z", "--", "Dockerfile*", ":(glob)**/Dockerfile*"], cwd=ROOT,
        )
        paths = [ROOT / filename for filename in tracked.decode().split("\0") if filename]
    if not paths:
        parser.error("Geen Dockerfiles gevonden.")
    failed = False
    for path in paths:
        for line, instruction in check(path):
            print(
                f"{path}:{line}: {instruction}"
                " vereist een versietag en een volledige sha256-digest.",
                file=sys.stderr,
            )
            failed = True
    if failed:
        return 1
    print(f"Basisimages gecontroleerd in {len(paths)} Dockerfiles.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
