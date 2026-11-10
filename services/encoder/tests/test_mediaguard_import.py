"""P8: import van een bestaande standalone SQLite-database (additief, gemapt)."""

from __future__ import annotations

import shutil
from dataclasses import replace
from pathlib import Path

import pytest
from conftest import MG_TOKEN
from fastapi.testclient import TestClient
from mg_helpers import HEADERS, container_settings

from app.config import settings
from app.db import Database
from app.main import create_app


def _old_install(tmp_path: Path) -> Path:
    """Een 'oude' installatie: alleen migraties 001-015, met echte rijen."""

    migrations = tmp_path / "oude-migraties"
    migrations.mkdir()
    for path in (Path(__file__).parents[1] / "app" / "migrations").glob("0*.sql"):
        if int(path.name[:3]) <= 15:
            shutil.copy(path, migrations / path.name)
    root = tmp_path / "oud"
    (root / "config").mkdir(parents=True)
    old_settings = replace(
        settings,
        config_root=root / "config",
        backup_root=root / "backups",
        database_path=root / "config" / "app.db",
    )
    old = Database(old_settings, migrations)
    old.initialize()
    profile = old.execute_insert("INSERT INTO profiel(naam,inst) VALUES ('Oud','{}')")
    library = old.execute_insert(
        "INSERT INTO library(naam,pad,profiel_id,bron_type,bron_uri) "
        "VALUES ('Films','/library/netwerk/nas/films',?,'smb','smb://nas/media/films')",
        (profile,),
    )
    file_id = old.execute_insert(
        "INSERT INTO bestand(library_id,pad,grootte,mtime,categorie,gescand_op) "
        "VALUES (?,'/library/netwerk/nas/films/Wadi (2021)/Wadi.mkv',1,1,'ok','t')",
        (library,),
    )
    decision = old.execute_insert(
        "INSERT INTO besluit(bestand_id,actie,reden,payload,gemaakt_op) "
        "VALUES (?,'hercoderen','r','{}','t')",
        (file_id,),
    )
    old.execute(
        "INSERT INTO job(besluit_id,bestand_id,baan,status) VALUES (?,?,'cpu','klaar')",
        (decision, file_id),
    )
    old.execute(
        "INSERT INTO job(besluit_id,bestand_id,baan,status) VALUES (?,?,'cpu','wacht')",
        (decision, file_id),
    )
    return old_settings.database_path


def test_standalone_db_import_dry_run_en_echt(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("MG_INTERNAL_TOKEN", MG_TOKEN)
    source = _old_install(tmp_path)
    configured = container_settings(tmp_path / "nieuw")
    database = Database(configured)
    with TestClient(create_app(configured, database, start_planner=False)) as client:
        (configured.config_root / "import").mkdir()
        target = configured.config_root / "import" / "app.db"
        shutil.copy(source, target)
        url = "/api/mediaguard/v1/import/standalone-db"
        unmapped = client.post(url, json={"db_path": str(target)}, headers=HEADERS).json()
        assert unmapped["resultaat"] == "geweigerd"
        assert "niet-gemapte libraries" in unmapped["blokkades"][0]
        mapping = {"/library/netwerk/nas/films": str(configured.media_roots[0] / "films")}
        dry = client.post(
            url, json={"db_path": str(target), "path_map": mapping}, headers=HEADERS
        ).json()
        assert dry["resultaat"] == "kan worden geïmporteerd"
        assert dry["jobs"] == {"klaar": 1, "wacht": 1}
        assert database.fetch_one("SELECT COUNT(*) FROM library")[0] == 0  # dry-run schrijft niets

        done = client.post(
            url,
            json={"db_path": str(target), "path_map": mapping, "dry_run": False},
            headers=HEADERS,
        )
        assert done.status_code == 200, done.text
        assert 16 in database.applied_versions()
        library = database.fetch_one("SELECT * FROM library")
        assert library is not None
        assert library["pad"] == str(configured.media_roots[0] / "films")
        assert library["bron_type"] == "lokaal" and library["canary_status"] == "actief"
        row = database.fetch_one("SELECT pad FROM bestand")
        assert row is not None and row["pad"].endswith("/films/Wadi (2021)/Wadi.mkv")
        statuses = {r["status"] for r in database.fetch_all("SELECT status FROM job")}
        assert statuses == {"klaar", "overgeslagen"}  # historie blijft, niets start vanzelf
        assert all(
            int(r["gepauzeerd"]) for r in database.fetch_all("SELECT gepauzeerd FROM worker_config")
        )
        again = client.post(
            url,
            json={"db_path": str(target), "path_map": mapping, "dry_run": False},
            headers=HEADERS,
        )
        assert again.status_code == 409  # nooit samenvoegen: geen dubbele waarheid


def test_import_buiten_importmap_wordt_geweigerd(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("MG_INTERNAL_TOKEN", MG_TOKEN)
    source = _old_install(tmp_path)
    configured = container_settings(tmp_path / "nieuw")
    with TestClient(create_app(configured, Database(configured), start_planner=False)) as client:
        response = client.post(
            "/api/mediaguard/v1/import/standalone-db",
            json={"db_path": str(source)},
            headers=HEADERS,
        )
        assert response.status_code == 409
