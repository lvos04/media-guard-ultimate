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
        # verwijzingen zonder FOREIGN KEY kloppen ook (S-P2-610): een voltooide overstap wijst
        # naar een bestaande bron en een bestaande instantie, zoals in een echte database
        for kol, ouder in (("source_id", "source_instances"), ("target_instance_id", "instances")):
            assert conn.execute(f"SELECT COUNT(*) FROM migration_cutovers WHERE {kol} NOT IN "
                                f"(SELECT id FROM {ouder})").fetchone()[0] == 0, kol
        # open werk (S-P2-610): één overdracht staat nog open (ververst, de afspeeltest nog
        # niet aan de beurt); inert: geen proef bereikt de wachttijd, dus geen verzoek naar Emby
        open_werk = conn.execute("SELECT refreshed_at, test_after, result FROM "
                                 "mediaserver_pending WHERE done_at IS NULL").fetchall()
        assert len(open_werk) >= 1, open_werk
        assert all(r[0] and r[1] >= "2099" and r[2] is None for r in open_werk), open_werk
        assert conn.execute("SELECT COUNT(*) FROM mediaserver_pending WHERE done_at IS NOT "
                            "NULL").fetchone()[0] >= 1
    finally:
        conn.close()


# -- de fixture van de vorige release is een database die zo echt kan bestaan (S-P2-610) ---------
def _app_op_fixture(tmp_path: Path) -> Any:
    from mediaguard.app import create_app
    from mediaguard.config import load_settings

    sql, _meta = _fixture()
    ub.herstel(sql, tmp_path / "core" / "mediaguard.db")
    settings = load_settings(data_dir=tmp_path / "core", staging_dir=tmp_path / "s",
                             quarantine_dir=tmp_path / "q", start_background=False,
                             internal_token="t")
    return create_app(settings)


def test_fixture_overstap_is_consistent_en_de_nazorg_is_al_gedaan(tmp_path: Path) -> None:
    """Vervolgpunt S-P2-600 ('job 26 mislukt' in de contracttest): de nazorg na de overstap
    faalde op de fixture met ``assert row is not None`` (migration/cutover.py, post_audit), omdat
    de voltooide overstap naar een bron verwees die niet bestaat. Dat kan in een echte database
    niet (bronnen worden alleen gearchiveerd). In de fixture verwijst de overstap nu naar een
    bestaande bron en instantie, en is de nazorg van een overstap van een week oud gedaan, zoals
    de vorige release hem na 24 uur vastlegt: er wordt op de fixture geen nazorg ingepland."""
    from mediaguard.migration import aftercare
    from mediaguard.migration import cutover as co

    app = _app_op_fixture(tmp_path)
    ctx = app.state.ctx
    try:
        voltooid = aftercare.completed_cutovers(ctx)
        assert voltooid, "de fixture hoort een voltooide overstap te hebben (migratiewizard)"
        for c in voltooid:
            rij = co.cutover_row(ctx, int(c["id"]))
            assert co.source_row(ctx, rij["source_id"]) is not None, rij["source_id"]
            assert co.instance_row(ctx, rij["target_instance_id"]) is not None, rij
            staat = ctx.state_get(f"migration_aftercare:{c['id']}", {}) or {}
            assert staat.get("pruned") and staat.get("audited"), staat
        assert aftercare.due(ctx) == []
    finally:
        ctx.db.close()


def test_make_aio_fixture_maakt_dezelfde_soort_fixture(tmp_path: Path) -> None:
    """Het gereedschap voor de volgende fixture (na een release) doet hetzelfde als de
    aangevulde 1.2.2-fixture: open werk in de metadata en in de database, en een voltooide
    overstap met bestaande bron en doel waarvan de nazorg al gedaan is."""
    from mediaguard.app import create_app
    from mediaguard.config import load_settings
    from mediaguard.migration import aftercare
    from mediaguard.migration import cutover as co

    spec = importlib.util.spec_from_file_location("make_aio_fixture",
                                                  ROOT / "scripts" / "make-aio-fixture.py")
    assert spec and spec.loader
    maker = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(maker)
    meta = maker.build(tmp_path)
    db = tmp_path / "core" / "mediaguard.db"
    conn = sqlite3.connect(db)
    try:
        n_open = conn.execute("SELECT COUNT(*) FROM mediaserver_pending WHERE done_at IS "
                              "NULL").fetchone()[0]
    finally:
        conn.close()
    assert meta["open_overdrachten"] == n_open >= 1
    settings = load_settings(data_dir=tmp_path / "core", staging_dir=tmp_path / "s2",
                             quarantine_dir=tmp_path / "q2", start_background=False,
                             internal_token="t")
    ctx = create_app(settings).state.ctx
    try:
        voltooid = aftercare.completed_cutovers(ctx)
        assert voltooid
        for c in voltooid:
            rij = co.cutover_row(ctx, int(c["id"]))
            assert co.source_row(ctx, rij["source_id"]) is not None
            assert co.instance_row(ctx, rij["target_instance_id"]) is not None
        assert aftercare.due(ctx) == []
    finally:
        ctx.db.close()


def test_fixture_heeft_open_werk_dat_de_poort_meet() -> None:
    """Vervolgpunt S-P2-600: de fixture had 0 open overdrachten (alle ``done_at`` gevuld). Nu
    staat er open werk in, en de metadata zegt hoeveel: de poort eist dat aantal vóór de upgrade
    (``dekking``) en meldt het na de upgrade en na het terugzetten."""
    sql, meta = _fixture()
    assert meta.get("open_overdrachten", 0) >= 1
    conn = sqlite3.connect(":memory:")
    try:
        conn.executescript(sql.read_text("utf-8"))
        open_rijen = conn.execute("SELECT transaction_id, refreshed_at, test_after, result FROM "
                                  "mediaserver_pending WHERE done_at IS NULL").fetchall()
        tids = {r[0] for r in open_rijen}
        # het open werk hoort bij een afgeronde vervanging (zoals de overdracht ontstaat)
        assert {r[0] for r in conn.execute(
            "SELECT id FROM replacement_transactions WHERE state='committed'")} >= tids
    finally:
        conn.close()
    assert len(open_rijen) == meta["open_overdrachten"], open_rijen
    assert all(r[1] and r[2] >= "2099" and r[3] is None for r in open_rijen), open_rijen
    assert meta["rows_per_table"]["mediaserver_pending"] == 3


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


#: de inhoud van de voortgang (veld ``toelichting`` van het uitzonderingenbestand): daarop mag
#: nooit een uitzondering staan. Per domein de tabellen van core (``core/mediaguard.db``) en van de
#: AV1-engine (``encode/app.db``).
VOORTGANG = {
    # bibliotheek en wanted
    "lib_items", "lib_seasons", "lib_episodes", "lib_files", "library_sync_runs",
    "wanted_items", "wanted_overrides", "missing_diagnostics", "backfill_runs",
    "backfill_attempts", "delivery_items", "delivery_events", "delivery_searches",
    "seerr_requests",
    # identity en policy
    "identity_results", "identity_known_hashes", "policy_profiles", "policy_profile_history",
    # encodes en AV1-jobs (ook optimalisatie en vrijgavecontroles)
    "encoding_jobs", "encoding_plans", "encoding_benchmarks", "encoding_autopilot_skips",
    "optimization_plans", "optimization_items", "av1_imports", "av1_provenance_map",
    "release_provenance", "job", "job_poging", "bestand", "besparing", "besluit", "trial_run",
    "encoding_trials", "scan_run", "verificatie", "kwaliteitsmeting", "quality_measurements",
    # quarantaine en herstel
    "quarantine_items", "quarantine_release_checks", "replacement_transactions",
    "replacement_journal",
    # ledger
    "storage_ledger_events",
    # mediaserver
    "mediaserver_actions", "mediaserver_pending", "mediaserver_watch", "mediaserver_servers",
    "mediaserver_server_results",
    # migratie(wizard)
    "migration_runs", "migration_steps", "migration_cutovers", "migration_reports",
    "migration_mappings", "migration_mapping_items", "migration_shadow_states",
    "source_instances", "source_backups", "instance_cutover_state",
    "instance_migration_checkpoints",
    # meldingen, audit en taken
    "notifications", "notify_outbox", "notify_digests", "notify_digest_items", "audit_events",
    "auditlog", "jobs", "job_logs",
    # AI-advies
    "ai_proposals", "ai_cases", "ai_feedback", "ai_evidence_index", "ai_policy_results",
    "ai_analyse",
}


def _controleer_uitzonderingen(uitz: dict[str, Any]) -> None:
    for db, regel in uitz["databases"].items():
        assert regel.get("reden") or regel.get("tabellen"), db
        if regel.get("hele_database"):
            assert db.endswith("logs.db"), db
        for tabel, t in regel.get("tabellen", {}).items():
            assert t.get("reden"), (db, tabel)
            assert tabel not in VOORTGANG, (db, tabel)
            assert not t.get("tabel_mag_weg"), (db, tabel)


def test_uitzonderingen_hebben_elk_een_reden_en_raken_de_voortgang_niet() -> None:
    _controleer_uitzonderingen(UITZ)


@pytest.mark.parametrize(("db", "tabel"), [
    ("core/mediaguard.db", "migration_runs"), ("core/mediaguard.db", "ai_proposals"),
    ("core/mediaguard.db", "jobs"), ("core/mediaguard.db", "identity_results"),
    ("core/mediaguard.db", "mediaserver_pending"), ("encode/app.db", "job_poging"),
])
def test_een_uitzondering_op_de_voortgang_wordt_geweigerd(db: str, tabel: str) -> None:
    """Mutatieproef (V-003): dezelfde uitzonderingen plus één regel op een voortgangstabel, met
    een reden, is rood (S-P2-610: de lijst miste o.a. migratie, AI-advies, taken en identity)."""
    uitz = copy.deepcopy(UITZ)
    uitz["databases"].setdefault(db, {"tabellen": {}})["tabellen"][tabel] = {
        "kolommen": ["status"], "reden": "proef"}
    with pytest.raises(AssertionError):
        _controleer_uitzonderingen(uitz)


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
    # op een push is inputs leeg: 'inputs.x != false' is dan onwaar (null == false), dus format()
    assert "format('{0}', inputs.upgradebewijs) != 'false'" in jobs["upgradebewijs"]["if"]
    stappen = json.dumps(jobs["upgradebewijs"]["steps"])
    assert "deploy/upgradebewijs/Dockerfile.kandidaat" in stappen
    assert "scripts/upgradebewijs.py poort" in stappen
    assert "kopieen" in jobs and "kopie-herkomst.py vergelijk --online" in json.dumps(
        jobs["kopieen"]["steps"])


def test_kandidaat_dockerfile_bouwt_op_de_gepinde_vorige_release() -> None:
    tekst = (ROOT / "deploy" / "upgradebewijs" / "Dockerfile.kandidaat").read_text("utf-8")
    assert f"ARG VORIGE={VORIGE['image']}@{VORIGE['digest']}" in tekst
    assert "latest" not in tekst


# -- nooit 'niets verloren' bij een lege of halve meting (fail-closed) ---------------------------
def test_lege_meting_voor_is_nooit_groen() -> None:
    fouten = ub.vergelijk({"databases": {}}, {"databases": {}}, UITZ)[0]
    assert fouten and "niets bewezen" in fouten[0]
    assert ub.vergelijk({}, {"databases": {}}, UITZ)[0]


def _encode_gevuld() -> dict[str, Any]:
    return {"integriteit": "ok", "tabellen": {"job": {"rijen": 2}}}


def test_dekking_eist_de_gevulde_fixture_en_de_av1_database(meting: dict[str, Any]) -> None:
    _sql, meta = _fixture()
    volledig = copy.deepcopy(meting)
    volledig["databases"]["encode/app.db"] = _encode_gevuld()
    assert ub.dekking(volledig, meta) == []
    # verkeerde map: niets gemeten
    fouten = ub.dekking({"databases": {}}, meta)
    assert any("core/mediaguard.db ontbreekt" in f for f in fouten)
    assert any("encode/app.db ontbreekt" in f for f in fouten)
    # AV1-database er wel, maar leeg (niet gevuld)
    leeg = copy.deepcopy(volledig)
    leeg["databases"]["encode/app.db"] = {"integriteit": "ok", "tabellen": {"job": {"rijen": 0}}}
    assert any("encode/app.db is leeg" in f for f in ub.dekking(leeg, meta))
    # de vorige release (of de meting) mist rijen van de fixture
    half = copy.deepcopy(volledig)
    t = half["databases"]["core/mediaguard.db"]["tabellen"]["quarantine_items"]
    t["rijen"] = 0
    assert any("quarantine_items heeft 0 rij(en)" in f for f in ub.dekking(half, meta))
    weg = copy.deepcopy(volledig)
    del weg["databases"]["core/mediaguard.db"]["tabellen"]["wanted_items"]
    assert any("wanted_items heeft 0 rij(en)" in f for f in ub.dekking(weg, meta))


def test_poort_controleert_de_dekking_en_de_vulling() -> None:
    bron = (ROOT / "scripts" / "upgradebewijs.py").read_text("utf-8")
    assert "fouten += dekking(voor, fixture)" in bron
    assert "AV1-database niet gevuld" in bron


def _met_open_overdracht_dicht(m: dict[str, Any]) -> None:
    t = _tabel(m, "mediaserver_pending")
    i = t["kolommen"].index("done_at")
    for rij in t["per_sleutel"].values():
        if rij[i] is None:
            rij[i] = "2026-11-12T00:00:00+00:00"


def test_dekking_eist_het_open_werk_van_de_fixture(meting: dict[str, Any]) -> None:
    """Sloot de vorige release het open werk al vóór de meting (bv. een ronde zonder
    mediaserver), dan bewijst de poort niets over open werk: rood, niet 'niets verloren'."""
    _sql, meta = _fixture()
    volledig = copy.deepcopy(meting)
    volledig["databases"]["encode/app.db"] = _encode_gevuld()
    assert ub.open_werk(volledig) == meta["open_overdrachten"] >= 1
    assert ub.dekking(volledig, meta) == []
    dicht = copy.deepcopy(volledig)
    _met_open_overdracht_dicht(dicht)
    assert ub.open_werk(dicht) == 0
    assert any("open overdracht" in f for f in ub.dekking(dicht, meta)), ub.dekking(dicht, meta)
    assert ub.open_werk({"databases": {}}) == 0


class _NepPoort(ub.Poort):
    """De poort zonder Docker: starten, stoppen en de hulpcontainer doen niets; meten leest de
    mappen van de werkmap zelf (de fixture), met een gevulde AV1-database erbij. ``wijzig`` past
    een meting aan (negatieve proeven)."""

    def __init__(self, args: Any, wijzig: dict[str, Any] | None = None) -> None:
        super().__init__(args)
        self.wijzig = wijzig or {}

    def start(self, image: str, config: str = "config") -> None:
        self.stap(f"(nep) start {image} op {config}")

    def stop(self) -> None:
        pass

    def readback(self, fixture: dict[str, Any], versie: str | None) -> list[str]:
        return []

    def hulp(self, *cmd: str, als: str = "0") -> str:
        assert "vul" in cmd, cmd
        return json.dumps({"profiel": "encode", "toegevoegd": {"job": 2}, "niet_gevuld": {}})

    def shell(self, script: str) -> str:
        import shutil

        if "cp -a /mnt/config /mnt/config-snapshot" in script:
            shutil.copytree(self.werk / "config", self.werk / "config-snapshot")
        if "ls -1" in script:
            return "voor-update-proef"
        return ""

    def meet(self, config: str, uit: str) -> dict[str, Any]:
        m = ub.meet(self.werk / config)
        m["databases"]["encode/app.db"] = {"integriteit": "ok", "tabellen": {"job": {
            "kolommen": ["id", "status"], "sleutel": ["id"], "rijen": 2, "inhoud_sha256": "",
            "per_sleutel": {"[1]": [1, "klaar"], "[2]": [2, "gecommit"]},
            "zonder_sleutel": []}}}
        if uit in self.wijzig:
            self.wijzig[uit](m)
        return m


def _nep_poort(tmp_path: Path, wijzig: dict[str, Any] | None = None) -> tuple[list[str], str]:
    import argparse

    args = argparse.Namespace(
        vorige="ghcr.io/x/y:1-aio@sha256:" + "a" * 64, vorige_versie=VORIGE["versie"],
        nieuw="kandidaat", versie="9.9.9", fixtures=str(FIXTURES),
        uitzonderingen=str(FIXTURES / "upgradebewijs-uitzonderingen.json"),
        werkmap=str(tmp_path / "werk"), poort=1, timeout=1, stop_timeout=1, rapport=None,
        bewaar=False)
    # geen opruimen(): dat roept docker aan; de werkmap staat in tmp_path
    return _NepPoort(args, wijzig).run()


def _sectie(tekst: str, kop: str) -> str:
    deel = tekst.split(f"## {kop}", 1)[1]
    return deel.split("\n## ", 1)[0]


def test_poort_toont_bij_terugzetten_de_vergelijking_per_tabel(tmp_path: Path) -> None:
    """Vervolgpunt S-P2-600: de sectie 'Terugzetten' toonde een lege tabel ('Tabellen zonder
    inhoud vóór de upgrade: 0.'), terwijl de vergelijking wel volledig rekende. Nu staat elke
    gevulde tabel er met vóór en na, net als bij de upgrade, plus de toegepaste uitzonderingen."""
    fouten, tekst = _nep_poort(tmp_path)
    assert fouten == [], fouten
    upgrade = _sectie(tekst, f"Upgradebewijs {VORIGE['versie']} → 9.9.9")
    terug = _sectie(tekst, "Terugzetten (vorige image + snapshot)")
    for sectie in (upgrade, terug):
        assert "**Uitkomst:** ✅ niets verloren" in sectie
    for domein in ("quarantine_items", "encoding_jobs", "mediaserver_pending", "migration_runs",
                   "ai_proposals", "jobs", "identity_results"):
        assert f"| core/mediaguard.db | {domein} |" in terug, domein
    assert "| encode/app.db | job | 2 | 2 | 0 | 0 | 0 |" in terug
    gevuld = [r for r in upgrade.splitlines() if r.startswith("| core/") or r.startswith("| enc")]
    assert gevuld and all(r in terug for r in gevuld)
    assert "### Uitzonderingen" in terug


def test_poort_meldt_het_open_werk_na_upgrade_en_terugzetten(tmp_path: Path) -> None:
    _sql, meta = _fixture()
    n = meta["open_overdrachten"]
    fouten, tekst = _nep_poort(tmp_path)
    assert fouten == [], fouten
    regel = f"Open overdrachten (mediaserver_pending zonder done_at): vóór {n}, na {n}."
    assert regel in _sectie(tekst, f"Upgradebewijs {VORIGE['versie']} → 9.9.9")
    assert regel in _sectie(tekst, "Terugzetten (vorige image + snapshot)")


@pytest.mark.parametrize("uit", ["meting-na.json", "meting-terug.json"])
def test_poort_is_rood_als_open_werk_dichtgaat(tmp_path: Path, uit: str) -> None:
    fouten, tekst = _nep_poort(tmp_path, {uit: _met_open_overdracht_dicht})
    soort = "upgrade" if uit == "meting-na.json" else "terugzetten"
    assert any(f.startswith(f"{soort}: ") and "mediaserver_pending" in f and "gewijzigd" in f
               for f in fouten), fouten
    assert any(f.startswith(f"{soort}: ") and "open overdracht" in f for f in fouten), fouten
    assert "❌ verlies of fout" in tekst


def test_poort_is_rood_als_open_werk_voor_de_meting_al_dicht_was(tmp_path: Path) -> None:
    fouten, _tekst = _nep_poort(tmp_path, {"meting-voor.json": _met_open_overdracht_dicht})
    assert any("open overdracht" in f for f in fouten), fouten


# -- vorige-release.json is de direct vorige gepubliceerde release --------------------------------
DIGEST = "sha256:" + "a" * 64
TAGS = ["1.2.0-aio", "1.2.1-aio", "1.2.2-aio", "1.2.2", "1.3.0-aio-kandidaat-1-1", "0.9.0-aio"]


def _vorige(versie: str = "1.2.2", digest: str = DIGEST) -> dict[str, Any]:
    return {"versie": versie, "image": f"ghcr.io/lvos04/mediaguard-core:{versie}-aio",
            "digest": digest}


def test_volgorde_klopt_voor_push_en_release() -> None:
    assert ub.controleer_volgorde(_vorige(), TAGS, DIGEST) == []
    assert ub.controleer_volgorde(_vorige(), TAGS, DIGEST, "1.3.0") == []
    # patch op een oudere lijn: vorige = hoogste gepubliceerde onder de versie
    tags = [*TAGS, "1.3.0-aio"]
    assert ub.controleer_volgorde(_vorige(), tags, DIGEST, "1.2.3") == []


def test_volgorde_negatief() -> None:
    def fout(*args: Any) -> str:
        fouten = ub.controleer_volgorde(*args)
        assert fouten, args
        return "\n".join(fouten)

    # na een release vergeten bij te werken (push en release)
    assert "laatst gepubliceerde release is 1.3.0" in fout(_vorige(), [*TAGS, "1.3.0-aio"], DIGEST)
    assert "direct vorige gepubliceerde release vóór 1.4.0 is 1.3.0" in fout(
        _vorige(), [*TAGS, "1.3.0-aio"], DIGEST, "1.4.0")
    # vorige niet ouder dan de kandidaat
    assert "niet ouder dan 1.2.2" in fout(_vorige(), TAGS, DIGEST, "1.2.2")
    assert "niet ouder dan 1.1.0" in fout(_vorige(), TAGS, DIGEST, "1.1.0")
    # een kandidaat-tag is geen gepubliceerde release
    assert "staat niet in de registry" in fout(_vorige("1.3.0"), TAGS, DIGEST)
    # ander image onder de gepinde digest, of digest niet leesbaar
    assert "is niet de digest van 1.2.2-aio" in fout(_vorige(), TAGS, "sha256:" + "b" * 64)
    assert "digest van 1.2.2-aio niet te lezen" in fout(_vorige(), TAGS, None)
    # image-tag past niet bij de versie
    v = _vorige()
    v["image"] = "ghcr.io/lvos04/mediaguard-core:1.2.1-aio"
    assert "is niet de tag 1.2.2-aio" in fout(v, TAGS, DIGEST)
    assert "geen versie" in fout(_vorige("1.2"), TAGS, DIGEST)
    # lege registry (niet leesbaar of verkeerd pakket)
    assert "staat niet in de registry" in fout(_vorige(), [], DIGEST)


def test_workflows_controleren_de_vorige_release() -> None:
    ci = json.dumps(_workflow("ci.yml")["jobs"]["upgradebewijs"]["steps"])
    assert "scripts/upgradebewijs.py volgorde" in ci
    rel = _workflow("release.yml")["jobs"]["upgradebewijs"]["steps"]
    stap = next(s["run"] for s in rel if "poort" in s.get("run", ""))
    assert stap.index("upgradebewijs.py volgorde") < stap.index("upgradebewijs.py poort")
    assert '--versie "$VERSION"' in stap.split("upgradebewijs.py volgorde", 1)[1].split("\n")[0]
