#!/usr/bin/env python3
"""Maakt de gevulde core-database voor de AIO-rooktest en het upgradebewijs (HARDE EIS).

De rooktest (``scripts/aio-smoke.py``) en het upgradebewijs (``scripts/upgradebewijs.py``,
C1.3-MGU-03) starten een nieuw alles-in-één-image op een database van de VORIGE versie en
controleren dat er niets verloren gaat. Dit script maakt zo'n database met de core-code van NU
(dus het schema van deze versie):

1. via de gewone API: een beheerder en een kijker, eigen instellingen, een API-sleutel,
   meldingen, taken en auditregels (zoals vóór de HARDE EIS);
2. daarna **elke** overige tabel met herkenbare, inerte rijen (``upgradebewijs.vul``, profiel
   ``core``): bibliotheek en wanted, identity en policy, herstel, quarantaine en vervangingen,
   encodes en AV1-voortgang, storage ledger, mediaserver, migratiewizard, meldingen, audit en
   AI-advies (B03_DELTAVERZOEKEN §1 punt 2);
3. per leesroute van de voortgang de merktekens die na een upgrade via de API terug moeten komen
   (``readback``).

Geen geheimen: die zijn versleuteld met secret.key en horen niet in de repo.

    git checkout <releasecommit van die versie>
    PYTHONPATH=apps/core python3 scripts/make-aio-fixture.py

Draai het op de commit van een UITGEBRACHTE versie (anders klopt de naam niet met het schema);
het upgradebewijs start daarna de echte image van die versie op de fixture (bewijs dat die versie
hem accepteert). Schrijft ``tests/fixtures/aio/core-<versie>.sql`` (SQLite-dump) en
``core-<versie>.json``. Oude fixtures blijven staan.
"""

from __future__ import annotations

import importlib.util
import json
import re
import sqlite3
import sys
import tempfile
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
OUT = ROOT / "tests" / "fixtures" / "aio"

USER = "rooktest"
# Alleen voor deze testdatabase; nergens anders gebruikt.
PASSWORD = "rooktest-wachtwoord-1"
SETTINGS = {"backup.retention_count": 21, "ui.theme": "light"}
#: leesroutes van de voortgang; per route de merktekens die de fixture daar laat zien
READBACK_ROUTES = (
    "/api/v1/quarantine", "/api/v1/recovery/transactions", "/api/v1/encoding/jobs",
    "/api/v1/wanted", "/api/v1/library", "/api/v1/storage/ledger", "/api/v1/notifications",
    "/api/v1/audit", "/api/v1/jobs", "/api/v1/migration/runs", "/api/v1/optimization/plans",
    "/api/v1/delivery/requests", "/api/v1/notify/outbox", "/api/v1/notify/offsite",
)
MERKTEKEN = re.compile(r"fixture-[a-z0-9_]+-[a-z0-9_]+-\d+|/data/media/fixture/[\w/.-]+|"
                       r"Rooktest: [A-Za-z ]+")


def _upgradebewijs() -> Any:
    spec = importlib.util.spec_from_file_location("upgradebewijs",
                                                  ROOT / "scripts" / "upgradebewijs.py")
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    sys.modules["upgradebewijs"] = module
    spec.loader.exec_module(module)
    return module


def build(target_dir: Path) -> dict[str, Any]:
    from fastapi.testclient import TestClient
    from mediaguard import __version__, jobs
    from mediaguard.api.system import notify
    from mediaguard.app import create_app
    from mediaguard.config import load_settings

    def settings() -> Any:
        return load_settings(data_dir=target_dir / "core", staging_dir=target_dir / "staging",
                             quarantine_dir=target_dir / "quarantine", start_background=False,
                             internal_token="fixture-token")

    app = create_app(settings())
    ctx = app.state.ctx
    with TestClient(app) as c:
        r = c.post("/api/v1/auth/setup", json={"username": USER, "password": PASSWORD})
        r.raise_for_status()
        c.headers["X-CSRF-Token"] = r.json()["csrf_token"]
        c.post("/api/v1/auth/users", json={"username": "kijker", "password": "kijker-wachtwoord-1",
                                      "role": "viewer"}).raise_for_status()
        c.post("/api/v1/auth/api-keys", json={"name": "rooktest-app", "role": "viewer"}
               ).raise_for_status()
    for key, value in SETTINGS.items():
        ctx.config.set("global", "", key, value, f"user:{USER}")
    notify(ctx, "warning", "Rooktest: melding van de vorige versie", "Blijft na de update staan.",
           dedupe_key="fixture:melding")
    notify(ctx, "info", "Rooktest: info", "Gelezen melding.", dedupe_key="fixture:info")
    # alleen afgeronde taken: een taak in de wachtrij zou in de rooktest echt gaan draaien
    for status in ("succeeded", "cancelled"):
        job_id = jobs.enqueue(ctx.db, "fixture.voorbeeld", {"fixture": True}, created_by=USER)
        ctx.db.execute("UPDATE jobs SET status=?, finished_at=? WHERE id=?",
                       (status, "2026-11-08T12:00:00+00:00", job_id))
    ctx.db.execute("PRAGMA wal_checkpoint(TRUNCATE)")
    ctx.db.close()

    # 2. elke overige tabel vullen (HARDE EIS: alle domeinen van de voortgang)
    db_path = target_dir / "core" / "mediaguard.db"
    gevuld = _upgradebewijs().vul(db_path, "core")
    if gevuld["niet_gevuld"]:
        raise SystemExit(f"niet alle tabellen gevuld: {gevuld['niet_gevuld']}")

    # 3. readback: wat moet de API na een upgrade nog laten zien?
    app = create_app(settings())
    readback: dict[str, list[str]] = {}
    with TestClient(app) as c:
        c.post("/api/v1/auth/login", json={"username": USER, "password": PASSWORD}
               ).raise_for_status()
        for route in READBACK_ROUTES:
            r = c.get(route)
            if r.status_code != 200:
                raise SystemExit(f"{route}: {r.status_code} {r.text[:200]}")
            gezien = sorted(set(MERKTEKEN.findall(r.text)))
            if gezien:
                readback[route] = gezien[:4]
    versions = [r["version"] for r in app.state.ctx.db.fetch_all(
        "SELECT version FROM schema_migrations ORDER BY version")]
    app.state.ctx.db.execute("PRAGMA wal_checkpoint(TRUNCATE)")
    app.state.ctx.db.close()
    conn = sqlite3.connect(db_path)
    try:
        tabellen = [r[0] for r in conn.execute(
            "SELECT name FROM sqlite_master WHERE type='table' AND name NOT LIKE 'sqlite_%' "
            "ORDER BY name")]
        rijen = {t: conn.execute(f'SELECT COUNT(*) FROM "{t}"').fetchone()[0] for t in tabellen}
    finally:
        conn.close()
    # min_rows (aio-smoke): alleen tabellen die nooit automatisch worden opgeruimd (meldingen,
    # taken en audit krijgen een bewaartermijn; de rooktest zou dan over een paar maanden vals
    # falen). Het upgradebewijs meet alles, maar pas NA een start van de vorige release.
    min_rows = {t: rijen[t] for t in ("users", "settings_values", "api_keys")}
    return {"version": __version__, "schema_versions": versions, "user": USER,
            "password": PASSWORD, "settings": SETTINGS, "min_rows": min_rows,
            "rows_per_table": rijen, "readback": readback,
            "filled_by": "scripts/make-aio-fixture.py (API + upgradebewijs.vul, profiel core)"}


def dump(db_path: Path) -> str:
    conn = sqlite3.connect(db_path)
    try:
        # sessies en aanmeldregels zijn vluchtig en bevatten tokens: niet in de fixture
        conn.execute("DELETE FROM sessions")
        conn.commit()
        return "\n".join(conn.iterdump()) + "\n"
    finally:
        conn.close()


def main() -> int:
    with tempfile.TemporaryDirectory() as tmp:
        meta = build(Path(tmp))
        sql = dump(Path(tmp) / "core" / "mediaguard.db")
    OUT.mkdir(parents=True, exist_ok=True)
    base = OUT / f"core-{meta['version']}"
    sql_file, meta_file = Path(f"{base}.sql"), Path(f"{base}.json")
    sql_file.write_text(sql, encoding="utf-8")
    meta_file.write_text(json.dumps(meta, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    print(f"{sql_file} ({len(sql)} bytes), {meta_file}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
