"""Importeer de SQLite-WAL-database van een bestaande standalone AV1-installatie.

P8 / P2 ("Voorkom big-bang migratie … elke oude job en auditrecord blijft
reconstrueerbaar"): de encoder houdt zijn eigen SQLite. Een oude ``app.db``
wordt in zijn geheel overgenomen (ids blijven gelijk, zodat core
``av1:<tabel>:<id>`` → MediaGuard-uuid kan mappen), daarna:

* alleen *additieve* migraties (016+) via het bestaande migratiemechanisme;
* paden herschreven met een expliciete prefixmapping (``/library/films`` →
  ``/media/films``); niet-gemapte libraries blokkeren de import;
* SMB-libraries worden lokale read-only mounts (TrueNAS mount de share);
* nog wachtende oude jobs worden ``overgeslagen`` (core plant opnieuw), workers
  gepauzeerd en libraries terug in canary — nooit direct massaal encoderen;
* geweigerd bij een actieve job of onafgemaakte vervangtransactie (expliciete
  writer-handoff: de oude app moet netjes gestopt zijn) of als de doel-DB al
  eigen data heeft (geen dubbele waarheid).

Versleutelde integratiesleutels blijven versleuteld; zonder het oude
sleutelbestand moeten ze in de UI opnieuw worden ingevoerd. Niets wordt gelogd.
"""

from __future__ import annotations

import json
import sqlite3
from contextlib import closing
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from app.config import Settings
from app.db import Database

IMPORT_DIRNAME = "import"
FINISHED_TRANSACTIONS = ("hersteld", "opgeruimd", "vervangen")


class ImportError_(RuntimeError):
    """De import is veilig geweigerd; er is niets gewijzigd."""


def _map_path(value: str | None, mapping: list[tuple[str, str]]) -> str | None:
    if value is None:
        return None
    for old, new in mapping:
        if value == old or value.startswith(old.rstrip("/") + "/"):
            return new.rstrip("/") + value[len(old.rstrip("/")) :]
    return None


def _counts(connection: sqlite3.Connection) -> dict[str, Any]:
    def one(sql: str) -> int:
        return int(connection.execute(sql).fetchone()[0])

    jobs = {
        str(row[0]): int(row[1])
        for row in connection.execute("SELECT status,COUNT(*) FROM job GROUP BY status")
    }
    return {
        "schema_versie": one("SELECT COALESCE(MAX(versie),0) FROM schema_versie"),
        "libraries": one("SELECT COUNT(*) FROM library"),
        "bestanden": one("SELECT COUNT(*) FROM bestand"),
        "besluiten": one("SELECT COUNT(*) FROM besluit"),
        "jobs": jobs,
        "auditregels": one("SELECT COUNT(*) FROM auditlog"),
        "bespaard_bytes": one("SELECT COALESCE(SUM(bytes_voor-bytes_na),0) FROM besparing"),
        "versleutelde_geheimen": one("SELECT COUNT(*) FROM geheim"),
        "open_transacties": one(
            "SELECT COUNT(*) FROM vervang_transactie WHERE status NOT IN "
            f"({','.join(repr(s) for s in FINISHED_TRANSACTIONS)})"
        ),
    }


def import_standalone_db(
    database: Database,
    settings: Settings,
    source_db: Path,
    path_map: dict[str, str],
    *,
    dry_run: bool = True,
) -> dict[str, Any]:
    import_root = (settings.config_root / IMPORT_DIRNAME).resolve()
    try:
        source = source_db.resolve(strict=True)
    except OSError as exc:
        raise ImportError_(f"importbestand niet gevonden: {source_db}") from exc
    if not source.is_relative_to(import_root):
        raise ImportError_(f"plaats de oude app.db onder {import_root}")
    mapping = sorted(path_map.items(), key=lambda item: len(item[0]), reverse=True)
    for _old, new in mapping:
        if not any(Path(new).is_relative_to(root) for root in settings.media_roots):
            raise ImportError_(f"doelpad {new} ligt niet onder een mediaroot")

    with closing(sqlite3.connect(f"file:{source}?mode=ro", uri=True)) as old:
        if old.execute("PRAGMA integrity_check").fetchone()[0] != "ok":
            raise ImportError_("de oude database is beschadigd (integrity_check)")
        report = _counts(old)
        libraries = old.execute("SELECT id,naam,pad,bron_type FROM library").fetchall()
    latest = max(version for version, _path in database.migration_files())
    if report["schema_versie"] > latest:
        raise ImportError_("de oude database is nieuwer dan deze encoder")
    unmapped = [str(row[2]) for row in libraries if _map_path(str(row[2]), mapping) is None]
    blockers = []
    if unmapped:
        blockers.append("niet-gemapte libraries: " + ", ".join(unmapped))
    active = sum(report["jobs"].get(status, 0) for status in ("bezig", "verifieren"))
    if active:
        blockers.append(f"{active} actieve job(s); stop de oude installatie eerst netjes")
    if report["open_transacties"]:
        blockers.append("onafgemaakte vervangtransacties; laat de oude app eerst herstellen")
    existing = database.fetch_one(
        "SELECT (SELECT COUNT(*) FROM library)+(SELECT COUNT(*) FROM job) AS n"
    )
    if existing is not None and int(existing["n"]):
        blockers.append("de encoder heeft al eigen libraries/jobs; samenvoegen is niet toegestaan")
    report["libraries_mapping"] = [
        {
            "id": int(row[0]),
            "naam": row[1],
            "oud": row[2],
            "nieuw": _map_path(str(row[2]), mapping),
            "was_smb": row[3] == "smb",
        }
        for row in libraries
    ]
    report["blokkades"] = blockers
    report["dry_run"] = dry_run
    if blockers:
        report["resultaat"] = "geweigerd"
        if not dry_run:
            raise ImportError_("; ".join(blockers))
        return report
    if dry_run:
        report["resultaat"] = "kan worden geïmporteerd"
        return report

    database._backup_before_migration()  # bestaand mechanisme: kopie in backups/voor-migratie
    with (
        closing(sqlite3.connect(f"file:{source}?mode=ro", uri=True)) as old,
        database.connection() as target,
    ):
        old.backup(target)
    database.apply_migrations()
    now = datetime.now(UTC).isoformat()
    with database.connection() as connection:
        connection.execute("BEGIN IMMEDIATE")
        for table, column in (
            ("library", "pad"),
            ("library", "bron_uri"),
            ("bestand", "pad"),
            ("vervang_transactie", "bron_pad"),
            ("job", "resultaat_pad"),
        ):
            rows = connection.execute(
                f"SELECT rowid,{column} FROM {table} WHERE {column} IS NOT NULL"  # noqa: S608
            ).fetchall()
            for row in rows:
                mapped = _map_path(str(row[1]), mapping)
                if mapped is not None:
                    connection.execute(
                        f"UPDATE {table} SET {column}=? WHERE rowid=?",  # noqa: S608
                        (mapped, row[0]),
                    )
        # Containermodus: SMB wordt door TrueNAS gemount en read-only doorgegeven.
        connection.execute(
            "UPDATE library SET bron_type='lokaal',bron_uri=pad,verwacht_device=NULL,"
            "canary_status='actief'"
        )
        connection.execute(
            "UPDATE job SET status='overgeslagen',foutsoort='Geimporteerd',"
            "fout='geïmporteerd uit standalone; core plant opnieuw',klaar_op=? "
            "WHERE status='wacht'",
            (now,),
        )
        connection.execute(
            "UPDATE worker_config SET gepauzeerd=1,"
            "pauze_reden='MediaGuard-import: controleer en hervat via de UI'"
        )
        connection.execute(
            "INSERT INTO auditlog(soort,onderwerp,oud,nieuw,tijd) VALUES (?,?,?,?,?)",
            (
                "mediaguard",
                "standalone-import",
                str(source),
                json.dumps(
                    {k: v for k, v in report.items() if k != "libraries_mapping"},
                    ensure_ascii=False,
                ),
                now,
            ),
        )
        connection.commit()
    report["resultaat"] = "geïmporteerd"
    report["na_import"] = {
        "workers": "gepauzeerd",
        "libraries": "canary actief",
        "geheimen": "versleuteld overgenomen; zonder oud sleutelbestand opnieuw invoeren",
    }
    return report
