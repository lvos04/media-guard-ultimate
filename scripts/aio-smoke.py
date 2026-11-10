#!/usr/bin/env python3
"""Rooktest van het alles-in-één-image vóór publicatie (release.yml, job aio-smoke).

Start het nieuwe AIO-image zoals TrueNAS dat doet (drie mappen, als root gestart, alles
draait als 568) op een GEVULDE database van een eerdere versie en controleert:

1. de container wordt *healthy* en alle diensten draaien zonder herstart;
2. ``/api/v1/health`` (via de web-UI op poort 30080) geeft status ok en de juiste versie;
3. core gebruikt de oude database: geen installatiescherm, de oude beheerder kan inloggen
   en ``/api/v1/admin/version`` noemt precies de migraties uit het release-manifest;
4. na een nette stop (exitcode 0) is de database intact, zijn alle migraties toegepast,
   staan de oude gegevens er nog en is er een back-up van vóór de migratie (als er iets
   te migreren viel).

Alleen standaardbibliotheek; draait op de GitHub-runner als root (``sudo``) omdat de
mappen eigendom van 568 moeten zijn, net als op TrueNAS.

    sudo python3 scripts/aio-smoke.py --image ghcr.io/lvos04/mediaguard-core:1.2.2-aio-… \\
        --version 1.2.2
"""

from __future__ import annotations

import argparse
import contextlib
import json
import os
import shutil
import sqlite3
import subprocess
import sys
import tempfile
import time
import urllib.error
import urllib.request
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
APP_UID = APP_GID = 568
# alle diensten die mediaguard.aio start; elk moet draaien zonder één herstart
# hookguard: wachter tussen de forks en core (houdt de noodstop vast als core herstart)
SERVICES = ("core", "recovery", "encode", "web", "hookguard", "sonarr-series", "sonarr-anime",
            "radarr-films", "radarr-animefilms")


def version_tuple(text: str) -> tuple[int, ...]:
    parts = []
    for part in text.split("."):
        digits = "".join(ch for ch in part if ch.isdigit())
        parts.append(int(digits or 0))
    return tuple(parts)


def pick_fixture(folder: Path, release: str) -> tuple[Path, dict[str, Any]]:
    """Nieuwste fixture ``core-<versie>.json`` met een versie ≤ de release."""
    best: tuple[tuple[int, ...], Path] | None = None
    for meta in folder.glob("core-*.json"):
        v = version_tuple(meta.stem.removeprefix("core-"))
        if v <= version_tuple(release) and (best is None or v > best[0]):
            best = (v, meta)
    if best is None:
        raise SystemExit(f"geen fixture in {folder} voor versie {release}")
    meta_path = best[1]
    return meta_path.with_name(meta_path.stem + ".sql"), json.loads(meta_path.read_text("utf-8"))


def restore(sql: Path, db: Path) -> None:
    db.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(db)
    try:
        conn.executescript(sql.read_text("utf-8"))
        conn.commit()
    finally:
        conn.close()


def verify_db(db: Path, fixture: dict[str, Any], expected: list[str]) -> list[str]:
    """Problemen met de database na de run (lege lijst = goed)."""
    problems: list[str] = []
    conn = sqlite3.connect(db)  # tijdelijke kopie; schrijfbaar zodat WAL-bestanden kunnen
    try:
        check = conn.execute("PRAGMA integrity_check").fetchone()[0]
        if check != "ok":
            problems.append(f"integrity_check: {check}")
        applied = {r[0] for r in conn.execute("SELECT version FROM schema_migrations")}
        missing = [v for v in expected if v not in applied]
        if missing:
            problems.append(f"migraties niet toegepast: {', '.join(missing)}")
        for table, minimum in fixture.get("min_rows", {}).items():
            n = conn.execute(f"SELECT COUNT(*) FROM {table}").fetchone()[0]  # noqa: S608
            if n < minimum:
                problems.append(f"tabel {table}: {n} rijen, verwacht minstens {minimum}")
        user = conn.execute("SELECT role FROM users WHERE username=?",
                            (fixture["user"],)).fetchone()
        if user is None or user[0] != "admin":
            problems.append(f"beheerder {fixture['user']} ontbreekt na de migratie")
        for key, value in fixture.get("settings", {}).items():
            row = conn.execute("SELECT value_json FROM settings_values WHERE scope_type='global' "
                               "AND key=?", (key,)).fetchone()
            if row is None or json.loads(row[0]) != value:
                problems.append(f"instelling {key} niet behouden "
                                f"({row[0] if row else 'weg'} i.p.v. {value!r})")
    finally:
        conn.close()
    pending = [v for v in expected if v not in fixture.get("schema_versions", [])]
    # losse kopie (t/m 1.2.1) of een set-map met kopie + meta.json (daarna)
    if pending and not list((db.parent / "backups" / "voor-update").rglob("*.db")):
        problems.append("geen back-up van vóór de migratie in core/backups/voor-update")
    return problems


def verify_services(snapshot: dict[str, Any]) -> list[str]:
    services = snapshot.get("services", {})
    problems = []
    for name in SERVICES:
        s = services.get(name)
        if s is None:
            problems.append(f"dienst {name} ontbreekt in supervisor.json")
        elif s.get("state") != "running":
            problems.append(f"dienst {name}: {s.get('state')} (exit {s.get('last_exit')})")
        elif s.get("restarts") or s.get("hang_restarts"):
            problems.append(f"dienst {name} is herstart ({s.get('restarts')}x, "
                            f"vast {s.get('hang_restarts')}x)")
    return problems


def http(url: str, body: dict[str, Any] | None = None,
         headers: dict[str, str] | None = None) -> tuple[int, Any, list[str]]:
    data = None if body is None else json.dumps(body).encode()
    req = urllib.request.Request(url, data=data, method="POST" if body is not None else "GET",
                                 headers={"Content-Type": "application/json", **(headers or {})})
    try:
        with urllib.request.urlopen(req, timeout=10) as resp:  # noqa: S310 - eigen container
            raw = resp.read()
            cookies = resp.headers.get_all("Set-Cookie") or []
            status = resp.status
    except urllib.error.HTTPError as exc:
        return exc.code, exc.read().decode(errors="replace"), []
    try:
        return status, json.loads(raw), cookies
    except ValueError:
        return status, raw.decode(errors="replace"), cookies


def docker(*args: str, check: bool = True) -> str:
    out = subprocess.run(["docker", *args], capture_output=True, text=True, check=False)
    if check and out.returncode != 0:
        raise RuntimeError(f"docker {' '.join(args)}: {out.stderr.strip()}")
    return out.stdout.strip()


def wait_until_ready(name: str, base: str, config: Path, timeout: float) -> dict[str, Any]:
    """Wacht op Docker-health 'healthy', health-endpoint en alle diensten 'running'."""
    deadline = time.monotonic() + timeout
    last = "nog niets"
    while time.monotonic() < deadline:
        state = docker("inspect", "-f", "{{.State.Status}} {{if .State.Health}}"
                       "{{.State.Health.Status}}{{end}}", name)
        if state.startswith(("exited", "dead")):
            raise RuntimeError(f"container gestopt: {state}")
        try:
            snap = json.loads((config / "core" / "supervisor.json").read_text())
        except (OSError, ValueError):
            snap = {}
        try:
            code, health, _ = http(f"{base}/api/v1/health")
        except OSError as exc:
            code, health = 0, str(exc)
        last = f"docker={state} health={code} diensten={verify_services(snap) or 'ok'}"
        if state.endswith("healthy") and not state.endswith("unhealthy") and code == 200 \
                and not verify_services(snap):
            return health if isinstance(health, dict) else {}
        time.sleep(5)
    raise RuntimeError(f"niet klaar binnen {int(timeout)} s: {last}")


def check_api(base: str, fixture: dict[str, Any], version: str,
              expected: list[str]) -> list[str]:
    """Oude database in gebruik, oude beheerder kan inloggen, versie en migraties kloppen."""
    problems: list[str] = []
    _, setup, _ = http(f"{base}/api/v1/auth/setup-status")
    if not isinstance(setup, dict) or setup.get("needs_setup") is not False:
        problems.append(f"core vraagt om installatie: oude database niet gebruikt ({setup})")
    code, login, cookies = http(f"{base}/api/v1/auth/login",
                                {"username": fixture["user"], "password": fixture["password"]})
    if code != 200:
        problems.append(f"inloggen met de oude beheerder mislukt: {code} {login}")
        return problems
    cookie = "; ".join(c.split(";", 1)[0] for c in cookies)
    code, info, _ = http(f"{base}/api/v1/admin/version", headers={"Cookie": cookie})
    if code != 200 or not isinstance(info, dict):
        problems.append(f"/admin/version: {code} {info}")
        return problems
    if info.get("version") != version:
        problems.append(f"/admin/version meldt {info.get('version')}")
    if info.get("schema_versions") != expected:
        problems.append("schemaversies wijken af van het release-manifest: "
                        f"{info.get('schema_versions')}")
    # het image moet het manifest van deze release meedragen (MG_RELEASE_MANIFEST)
    bundled = info.get("manifest") if isinstance(info.get("manifest"), dict) else {}
    if not info.get("manifest_found") or bundled.get("version") != version:
        problems.append("release-manifest in het image ontbreekt of hoort bij versie "
                        f"{bundled.get('version')}")
    return problems


def run(args: argparse.Namespace) -> list[str]:
    manifest = json.loads(Path(args.manifest).read_text("utf-8"))
    expected = manifest["schema_versions"]["core"]
    sql, fixture = pick_fixture(Path(args.fixtures), args.version)
    pending = [v for v in expected if v not in fixture["schema_versions"]]
    print(f"fixture: {sql.name} (schema {len(fixture['schema_versions'])} migraties, release "
          f"{len(expected)}); te migreren: {', '.join(pending) or 'geen'}", flush=True)
    tmp = Path(tempfile.mkdtemp(prefix="mg-aio-smoke-"))
    config, work, data = tmp / "config", tmp / "work", tmp / "data"
    for d in (config, work, data / "media", data / "downloads"):
        d.mkdir(parents=True, exist_ok=True)
    restore(sql, config / "core" / "mediaguard.db")
    if os.geteuid() == 0:  # zoals de TrueNAS-ACL: alles van 'apps' (568)
        for path in (tmp, *tmp.rglob("*")):
            os.chown(path, APP_UID, APP_GID)
    name = f"mg-aio-smoke-{os.getpid()}"
    base = f"http://127.0.0.1:{args.port}"
    problems: list[str] = []
    docker("run", "-d", "--name", name, "-p", f"127.0.0.1:{args.port}:30080",
           "-e", "TZ=UTC", "-e", "MG_HOST=127.0.0.1",
           "-v", f"{config}:/config", "-v", f"{work}:/work", "-v", f"{data}:/data", args.image)
    try:
        health = wait_until_ready(name, base, config, args.timeout)
        if health.get("version") != args.version:
            problems.append(f"health meldt versie {health.get('version')}, verwacht "
                            f"{args.version}")
        problems += check_api(base, fixture, args.version, expected)
    except (RuntimeError, OSError) as exc:  # OSError: verbinding weg tijdens de controles
        problems.append(str(exc))
    finally:
        snapshot_text = ""
        with contextlib.suppress(OSError):
            snapshot_text = (config / "core" / "supervisor.json").read_text()
        subprocess.run(["docker", "stop", "-t", str(args.stop_timeout), name],
                       capture_output=True, check=False)
        exit_code = docker("inspect", "-f", "{{.State.ExitCode}}", name, check=False)
        logs = subprocess.run(["docker", "logs", "--tail", "200", name], capture_output=True,
                              text=True, check=False)
        docker("rm", "-f", name, check=False)
    if exit_code != "0":
        problems.append(f"geen nette stop binnen {args.stop_timeout} s (exitcode {exit_code})")
    problems += verify_db(config / "core" / "mediaguard.db", fixture, expected)
    if problems:
        print("---- supervisor.json ----\n" + snapshot_text, flush=True)
        print("---- docker logs (laatste 200 regels) ----", flush=True)
        print(logs.stdout + logs.stderr, flush=True)
    if not args.keep:
        shutil.rmtree(tmp, ignore_errors=True)
    return problems


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(description=__doc__.split("\n", 1)[0])
    p.add_argument("--image", required=True)
    p.add_argument("--version", required=True)
    p.add_argument("--manifest", default=str(ROOT / "deploy" / "release-manifest.json"))
    p.add_argument("--fixtures", default=str(ROOT / "tests" / "fixtures" / "aio"))
    p.add_argument("--port", type=int, default=30080)
    p.add_argument("--timeout", type=float, default=600)
    p.add_argument("--stop-timeout", type=int, default=90)
    p.add_argument("--keep", action="store_true", help="tijdelijke mappen laten staan")
    args = p.parse_args(argv)
    problems = run(args)
    summary = os.environ.get("GITHUB_STEP_SUMMARY")
    lines = [f"## AIO-rooktest {args.version}", ""]
    lines += [f"- ❌ {p}" for p in problems] or ["- ✅ start, health, versie, inloggen, "
                                                  "migratie en nette stop in orde"]
    if summary:
        with open(summary, "a", encoding="utf-8") as fh:
            fh.write("\n".join(lines) + "\n")
    print("\n".join(lines), flush=True)
    return 1 if problems else 0


if __name__ == "__main__":
    sys.exit(main())
