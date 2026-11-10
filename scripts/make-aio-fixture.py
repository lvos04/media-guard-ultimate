#!/usr/bin/env python3
"""Maakt de gevulde core-database voor de AIO-rooktest (release.yml, job aio-smoke).

De rooktest start het nieuwe alles-in-één-image op een database van een EERDERE versie en
controleert dat core hem migreert en gewoon blijft gebruiken. Dit script maakt zo'n
database met de core-code van NU (dus het schema van deze versie) en vult hem via de
gewone API: een beheerder en een kijker, eigen instellingen, een API-sleutel, meldingen,
taken en auditregels. Geen geheimen: die zijn versleuteld met secret.key en horen niet
in de repo.

    git checkout <releasecommit van die versie>
    PYTHONPATH=apps/core python3 scripts/make-aio-fixture.py

Draai het op de commit van een UITGEBRACHTE versie (anders klopt de naam niet met het
schema). Schrijft ``tests/fixtures/aio/core-<versie>.sql`` (SQLite-dump) en
``core-<versie>.json``
(wat de rooktest na de migratie terug moet vinden). Maak een nieuwe fixture als een
release het schema flink verandert; oude fixtures blijven staan (de rooktest neemt de
nieuwste die ouder is dan of gelijk is aan de release).
"""

from __future__ import annotations

import json
import sqlite3
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
OUT = ROOT / "tests" / "fixtures" / "aio"

USER = "rooktest"
# Alleen voor deze testdatabase; nergens anders gebruikt.
PASSWORD = "rooktest-wachtwoord-1"
SETTINGS = {"backup.retention_count": 21, "ui.theme": "light"}


def build(target_dir: Path) -> dict[str, object]:
    from fastapi.testclient import TestClient

    from mediaguard import __version__, jobs
    from mediaguard.api.system import notify
    from mediaguard.app import create_app
    from mediaguard.config import load_settings

    settings = load_settings(data_dir=target_dir / "core", staging_dir=target_dir / "staging",
                             quarantine_dir=target_dir / "quarantine", start_background=False,
                             internal_token="fixture-token")
    app = create_app(settings)
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
    versions = [r["version"] for r in ctx.db.fetch_all(
        "SELECT version FROM schema_migrations ORDER BY version")]
    # alleen tabellen die nooit automatisch worden opgeruimd (meldingen, taken en audit
    # krijgen een bewaartermijn; dan zou de rooktest over een paar maanden vals falen)
    counts = {t: int(ctx.db.fetch_one(f"SELECT COUNT(*) AS n FROM {t}")["n"])  # noqa: S608
              for t in ("users", "settings_values", "api_keys")}
    ctx.db.execute("PRAGMA wal_checkpoint(TRUNCATE)")
    ctx.db.close()
    return {"version": __version__, "schema_versions": versions, "user": USER,
            "password": PASSWORD, "settings": SETTINGS, "min_rows": counts}


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
    meta_file.write_text(json.dumps(meta, indent=2) + "\n", encoding="utf-8")
    print(f"{sql_file} ({len(sql)} bytes), {meta_file}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
