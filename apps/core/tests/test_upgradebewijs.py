"""HARDE EIS (contract 1.3 §2.3 regel 7b, §12; C1.3-MGU-03): upgradebewijs op een gevulde database.

Op elke push, zonder Docker: de fixture van de vorige release (``tests/fixtures/aio/core-<versie>``,
elke tabel gevuld) wordt met de core-code van NU gemigreerd en gestart; de meting per tabel vóór en
na moet gelijk zijn op de kolommen van vóór (alleen de uitzonderingen met reden uit
``upgradebewijs-uitzonderingen.json``), en de oude beheerder, de instellingen en de voortgang komen
via de API terug. De Docker-poort met de echte images draait in CI (job ``upgradebewijs``) en in
``release.yml`` vóór ``publish``. Negatieve gevallen bewijzen dat de vergelijking elk soort verlies
ziet.
"""

from __future__ import annotations

import copy
import importlib.util
import json
import sqlite3
import sys
from pathlib import Path
from typing import Any

import pytest
import yaml

ROOT = Path(__file__).resolve().parents[3]
FIXTURES = ROOT / "tests" / "fixtures" / "aio"
_spec = importlib.util.spec_from_file_location("upgradebewijs",
                                               ROOT / "scripts" / "upgradebewijs.py")
assert _spec and _spec.loader
ub = importlib.util.module_from_spec(_spec)
sys.modules["upgradebewijs"] = ub
_spec.loader.exec_module(ub)

VORIGE = json.loads((ROOT / "deploy" / "upgradebewijs" / "vorige-release.json").read_text("utf-8"))
UITZ = json.loads((FIXTURES / "upgradebewijs-uitzonderingen.json").read_text("utf-8"))


def _fixture() -> tuple[Path, dict[str, Any]]:
    return ub.kies_fixture(FIXTURES, VORIGE["versie"])


# -- de vorige release en zijn fixture -----------------------------------------------------------
def test_vorige_release_is_op_digest_gepind_en_heeft_een_fixture() -> None:
    assert VORIGE["digest"].startswith("sha256:") and len(VORIGE["digest"]) == 71
    assert VORIGE["image"].endswith(f":{VORIGE['versie']}-aio")
    sql, meta = _fixture()
    assert sql.is_file() and meta["version"] == VORIGE["versie"]
    assert VORIGE["fixture"] == f"tests/fixtures/aio/core-{VORIGE['versie']}"


def test_fixture_vult_elk_domein_van_de_voortgang() -> None:
    """B03_DELTAVERZOEKEN §1 punt 2: gevuld = alle domeinen. Elke tabel heeft rijen, behalve de
    bewust overgeslagen (geheimen, sessies, aanmeldcodes, vergrendelingen)."""
    _sql, meta = _fixture()
    leeg = {t for t, n in meta["rows_per_table"].items() if not n}
    assert leeg <= {"locks", "login_challenges", "secrets", "sessions"}, leeg
    for domein in ("lib_items", "lib_episodes", "lib_files", "wanted_items", "identity_results",
                   "policy_profiles", "replacement_transactions", "quarantine_items",
                   "encoding_jobs", "optimization_plans", "release_provenance",
                   "storage_ledger_events", "mediaserver_actions", "mediaserver_pending",
                   "mediaserver_watch", "migration_runs", "notifications", "audit_events", "jobs",
                   "ai_proposals", "users", "api_keys", "settings_values"):
        assert meta["rows_per_table"][domein] >= 1, domein
    assert len(meta["readback"]) >= 8
    assert "/api/v1/quarantine" in meta["readback"] and "/api/v1/encoding/jobs" in meta["readback"]


def test_fixture_heeft_het_schema_van_deze_versie_ui_rondes_wijzigen_geen_datamodel() -> None:
    """Zolang er geen migratie bij komt, is het schema van de vorige release gelijk aan dat van
    deze code. Komt er wel een migratie, dan moet die in het upgradebewijs zitten (dit faalt dan
    niet, maar de upgradetest hieronder migreert echt)."""
    from mediaguard.db import MIGRATIONS_DIR

    _sql, meta = _fixture()
    huidig = sorted(p.stem for p in MIGRATIONS_DIR.glob("*.sql"))
    assert set(meta["schema_versions"]) <= set(huidig), "migratie verdwenen of hernoemd"
    manifest = json.loads((ROOT / "deploy" / "release-manifest.json").read_text("utf-8"))
    assert manifest["schema_versions"]["core"] == huidig


# -- de upgrade met de core van nu ---------------------------------------------------------------
def test_upgrade_van_de_vorige_release_verliest_niets(tmp_path: Path, monkeypatch) -> None:
    from fastapi.testclient import TestClient

    from mediaguard.app import create_app
    from mediaguard.config import load_settings

    sql, meta = _fixture()
    config = tmp_path / "config"
    ub.herstel(sql, config / "core" / "mediaguard.db")
    voor = ub.meet(config)
    assert set(voor["databases"]) == {"core/mediaguard.db"}
    monkeypatch.delenv("MG_RELEASE_MANIFEST", raising=False)
    settings = load_settings(data_dir=config / "core", staging_dir=tmp_path / "staging",
                             quarantine_dir=tmp_path / "quarantine", start_background=False,
                             internal_token="t")
    app = create_app(settings)
    try:
        with TestClient(app) as c:
            assert c.get("/api/v1/auth/setup-status").json() == {"needs_setup": False}
            r = c.post("/api/v1/auth/login",
                       json={"username": meta["user"], "password": meta["password"]})
            assert r.status_code == 200, r.text
            eff = {e["key"]: e["value"] for e in c.get(
                "/api/v1/settings/effective", params={"keys": ",".join(meta["settings"])}).json()}
            assert eff == meta["settings"]
            for route, merktekens in meta["readback"].items():
                r = c.get(route)
                assert r.status_code == 200, (route, r.text[:200])
                ontbreekt = [m for m in merktekens if m not in r.text]
                assert not ontbreekt, (route, ontbreekt)
    finally:
        app.state.ctx.db.close()
    na = ub.meet(config)
    fouten, toegepast, overzicht = ub.vergelijk(voor, na, UITZ)
    assert fouten == [], "\n".join(fouten)
    # elke gevulde tabel staat in het overzicht (niets stil overgeslagen)
    gevuld = {t for t, n in meta["rows_per_table"].items() if n}
    assert gevuld <= {regel.split("|")[2].strip() for regel in overzicht}
    rapport = ub.rapport("test", fouten, toegepast, overzicht)
    assert "✅ niets verloren" in rapport


def test_vul_vult_een_lege_database_inert(tmp_path: Path) -> None:
    from mediaguard.app import create_app
    from mediaguard.config import load_settings

    settings = load_settings(data_dir=tmp_path / "core", staging_dir=tmp_path / "s",
                             quarantine_dir=tmp_path / "q", start_background=False,
                             internal_token="t")
    create_app(settings).state.ctx.db.close()
    uit = ub.vul(tmp_path / "core" / "mediaguard.db", "core")
    assert uit["niet_gevuld"] == {}
    conn = sqlite3.connect(tmp_path / "core" / "mediaguard.db")
    try:
        # inerte toestanden: niets gaat na de start draaien, zoeken of verbinden
        assert {r[0] for r in conn.execute("SELECT status FROM jobs")} <= {"succeeded", "cancelled"}
        assert {r[0] for r in conn.execute("SELECT archived FROM instances")} == {1}
        assert {r[0] for r in conn.execute("SELECT enabled FROM notify_channels")} == {0}
        assert {r[0] for r in conn.execute("SELECT status FROM wanted_items")} <= {"resolved",
                                                                                  "ignored"}
        assert conn.execute("SELECT COUNT(*) FROM secrets").fetchone()[0] == 0
        assert conn.execute("PRAGMA foreign_key_check").fetchall() == []
    finally:
        conn.close()


# -- de vergelijking ziet elk soort verlies (negatieve gevallen) ---------------------------------
@pytest.fixture(scope="module")
def meting(tmp_path_factory: pytest.TempPathFactory) -> dict[str, Any]:
    sql, _meta = _fixture()
    config = tmp_path_factory.mktemp("meting") / "config"
    ub.herstel(sql, config / "core" / "mediaguard.db")
    return ub.meet(config)


def _na(meting: dict[str, Any]) -> dict[str, Any]:
    return copy.deepcopy(meting)


def _tabel(m: dict[str, Any], naam: str) -> dict[str, Any]:
    return m["databases"]["core/mediaguard.db"]["tabellen"][naam]


def test_gelijk_is_groen(meting: dict[str, Any]) -> None:
    assert ub.vergelijk(meting, _na(meting), UITZ)[0] == []


def test_verloren_rij_is_een_fout(meting: dict[str, Any]) -> None:
    na = _na(meting)
    t = _tabel(na, "quarantine_items")
    t["per_sleutel"].pop(next(iter(t["per_sleutel"])))
    t["rijen"] -= 1
    fouten = ub.vergelijk(meting, na, UITZ)[0]
    assert any("quarantine_items: 1 rij(en) verloren" in f for f in fouten), fouten


def test_gewijzigde_waarde_is_een_fout(meting: dict[str, Any]) -> None:
    na = _na(meting)
    t = _tabel(na, "encoding_jobs")
    sleutel = next(iter(t["per_sleutel"]))
    t["per_sleutel"][sleutel][t["kolommen"].index("status")] = "queued"
    fouten = ub.vergelijk(meting, na, UITZ)[0]
    assert any("encoding_jobs: 1 rij(en) gewijzigd" in f and "status" in f for f in fouten)


def test_weggevallen_tabel_kolom_of_database_is_een_fout(meting: dict[str, Any]) -> None:
    na = _na(meting)
    del na["databases"]["core/mediaguard.db"]["tabellen"]["wanted_items"]
    t = _tabel(na, "lib_items")
    i = t["kolommen"].index("title")
    t["kolommen"].pop(i)
    for rij in t["per_sleutel"].values():
        rij.pop(i)
    fouten = ub.vergelijk(meting, na, UITZ)[0]
    assert any("wanted_items: tabel is weg" in f for f in fouten), fouten
    assert any("lib_items: kolom title is weg" in f for f in fouten), fouten
    leeg = {"databases": {}}
    assert ub.vergelijk(meting, leeg, UITZ)[0] == ["core/mediaguard.db: database is weg na de "
                                                   "upgrade"]


def test_nieuwe_kolom_tabel_en_rij_mogen(meting: dict[str, Any]) -> None:
    na = _na(meting)
    t = _tabel(na, "notifications")
    t["kolommen"].append("nieuw_veld")
    for rij in t["per_sleutel"].values():
        rij.append(None)
    t["per_sleutel"]['[999]'] = [None] * len(t["kolommen"])
    t["rijen"] += 1
    na["databases"]["core/mediaguard.db"]["tabellen"]["nieuwe_tabel"] = {
        "kolommen": ["id"], "sleutel": ["id"], "rijen": 0, "inhoud_sha256": "",
        "per_sleutel": {}, "zonder_sleutel": []}
    fouten, _t, overzicht = ub.vergelijk(meting, na, UITZ)
    assert fouten == []
    assert any("| notifications |" in r and r.rstrip(" |").endswith("1") for r in overzicht)


def test_uitzondering_geldt_alleen_voor_de_genoemde_sleutels_en_kolommen(
        meting: dict[str, Any]) -> None:
    na = _na(meting)
    t = _tabel(na, "system_state")
    kol = t["kolommen"].index("value_json")
    fixture_sleutel = next(k for k in t["per_sleutel"] if "fixture" in k)
    t["per_sleutel"][fixture_sleutel][kol] = '"anders"'
    fouten, toegepast, _o = ub.vergelijk(meting, na, UITZ)
    assert any("system_state: 1 rij(en) gewijzigd" in f for f in fouten), fouten
    # een runtime-sleutel uit de uitzonderingen mag wel veranderen (en staat in het rapport)
    na2 = _na(meting)
    t2 = _tabel(na2, "system_state")
    t2["per_sleutel"]['["taskwatch:proef"]'] = ["taskwatch:proef", "{}", "x"]
    m = copy.deepcopy(meting)
    proef = ["taskwatch:proef", '{"a":1}', "y"]
    _tabel(m, "system_state")["per_sleutel"]['["taskwatch:proef"]'] = proef
    fouten2, toegepast2, _o2 = ub.vergelijk(m, na2, UITZ)
    assert fouten2 == []
    assert any("taskwatch:*" in t for t in toegepast2)


def test_tabel_zonder_sleutel_vergelijkt_als_multiset() -> None:
    def m(rijen: list[list[Any]]) -> dict[str, Any]:
        return {"databases": {"x.db": {"integriteit": "ok", "tabellen": {"t": {
            "kolommen": ["a", "b"], "sleutel": [], "rijen": len(rijen), "inhoud_sha256": "",
            "per_sleutel": {}, "zonder_sleutel": rijen}}}}}

    voor = m([[1, "x"], [1, "x"], [2, "y"]])
    assert ub.vergelijk(voor, m([[2, "y"], [1, "x"], [1, "x"], [3, "z"]]))[0] == []
    fouten = ub.vergelijk(voor, m([[1, "x"], [2, "y"]]))[0]
    assert fouten and "1 rij(en) verloren" in fouten[0]


def test_kapotte_database_na_de_upgrade_is_een_fout(meting: dict[str, Any]) -> None:
    na = _na(meting)
    na["databases"]["core/mediaguard.db"]["integriteit"] = "*** in database main ***"
    assert any("integrity_check" in f for f in ub.vergelijk(meting, na, UITZ)[0])


def test_back_ups_tellen_niet_als_live_database(tmp_path: Path) -> None:
    for rel in ("core/mediaguard.db", "core/backups/voor-update/set1/mediaguard.db",
                "encode/backups/app-1.db", "encode/app.db"):
        (tmp_path / rel).parent.mkdir(parents=True, exist_ok=True)
        sqlite3.connect(tmp_path / rel).close()
    assert [p.relative_to(tmp_path).as_posix() for p in ub.live_databases(tmp_path)] == [
        "core/mediaguard.db", "encode/app.db"]


def test_uitzonderingen_hebben_elk_een_reden_en_raken_de_voortgang_niet() -> None:
    voortgang = {"lib_items", "lib_episodes", "lib_files", "wanted_items", "encoding_jobs",
                 "quarantine_items", "replacement_transactions", "storage_ledger_events",
                 "mediaserver_actions", "mediaserver_pending", "mediaserver_watch",
                 "notifications", "audit_events", "job", "bestand", "besparing"}
    for db, regel in UITZ["databases"].items():
        assert regel.get("reden") or regel.get("tabellen"), db
        if regel.get("hele_database"):
            assert db.endswith("logs.db"), db
        for tabel, t in regel.get("tabellen", {}).items():
            assert t.get("reden"), (db, tabel)
            assert tabel not in voortgang, (db, tabel)
            assert not t.get("tabel_mag_weg"), (db, tabel)


# -- de poort zit in de workflows ----------------------------------------------------------------
def _workflow(naam: str) -> dict[str, Any]:
    return yaml.safe_load((ROOT / ".github" / "workflows" / naam).read_text("utf-8"))


def test_release_publiceert_pas_na_het_upgradebewijs() -> None:
    jobs = _workflow("release.yml")["jobs"]
    assert "upgradebewijs" in jobs
    stappen = json.dumps(jobs["upgradebewijs"]["steps"])
    assert "scripts/upgradebewijs.py poort" in stappen
    assert "deploy/upgradebewijs/vorige-release.json" in stappen
    assert "aio-image" in jobs["upgradebewijs"]["needs"]
    assert "upgradebewijs" in jobs["publish"]["needs"]


def test_ci_draait_het_upgradebewijs_op_elke_push() -> None:
    jobs = _workflow("ci.yml")["jobs"]
    stappen = json.dumps(jobs["upgradebewijs"]["steps"])
    assert "deploy/upgradebewijs/Dockerfile.kandidaat" in stappen
    assert "scripts/upgradebewijs.py poort" in stappen
    assert "kopieen" in jobs and "kopie-herkomst.py vergelijk --online" in json.dumps(
        jobs["kopieen"]["steps"])


def test_kandidaat_dockerfile_bouwt_op_de_gepinde_vorige_release() -> None:
    tekst = (ROOT / "deploy" / "upgradebewijs" / "Dockerfile.kandidaat").read_text("utf-8")
    assert f"ARG VORIGE={VORIGE['image']}@{VORIGE['digest']}" in tekst
    assert "latest" not in tekst
