"""Bewijs op wegwerpdata voor bundelaanmaak, rapport en offline terugzetten."""

import hashlib
import json
import os
import secrets
import shutil
import sqlite3
from contextlib import closing

import pytest
from av1_fixture import maak_av1_v1
from test_connector_lezen import geverifieerde_offsite as geverifieerde_offsite
from test_connector_lezen import herstelbronnen as herstelbronnen
from test_connector_lezen import key, rapport
from test_connector_lezen import machine as machine
from test_updatevolgorde import kern_155
from test_upgradebewijs import ub

from mediaguard import aio, event_keys
from mediaguard.config import load_settings
from mediaguard.connector import backups
from mediaguard.maintenance import bundle, preupdate


@pytest.fixture
def settings(tmp_path, monkeypatch):
    monkeypatch.setenv("MG_AIO_CONFIG", str(tmp_path))
    monkeypatch.setenv("MG_SECRETS_DIR", str(tmp_path / "secrets"))
    monkeypatch.setenv("MG_AIO_SUPERVISOR", "1")
    monkeypatch.setattr(backups, "CACHE_SECONDS", 0)
    return load_settings(data_dir=tmp_path / "core", staging_dir=tmp_path / "staging",
                         quarantine_dir=tmp_path / "quarantaine", start_background=False,
                         internal_token=secrets.token_urlsafe())


def wachtrij(pad):
    with closing(sqlite3.connect(pad.as_uri() + "?mode=ro", uri=True)) as db:
        return db.execute("SELECT id,baan,status,prioriteit,percent,fout FROM job "
                          "ORDER BY baan,prioriteit DESC,id").fetchall()


def vul_taken(engine):
    assert ub.vul(engine, "encode")["toegevoegd"]["job"] == 2
    with closing(sqlite3.connect(engine)) as db, db:
        db.execute("UPDATE job SET percent=CASE id WHEN 1 THEN 37 ELSE 82 END")
    taken = wachtrij(engine)
    assert sorted(rij[4] for rij in taken) == [37, 82]
    return taken


def test_bundel_na_uitpakken_startbaar(client, machine, ctx, herstelbronnen, tmp_path, monkeypatch):
    _, inrichting, *_ = herstelbronnen
    engine = tmp_path / "encode/app.db"
    engine.unlink()
    with closing(sqlite3.connect(engine)) as db:
        maak_av1_v1(db)
    aio.before_services(dict(os.environ), tmp_path, None, migrate_core=True)
    taken = vul_taken(engine)
    headers = key(client)
    doel = tmp_path / "offsite"
    doel.mkdir()
    ctx.config.set("global", "", "backup.offsite.kind", "path", "proef")
    ctx.config.set("global", "", "backup.offsite.path", str(doel), "proef")
    cursor = event_keys.key_path(ctx.settings.data_dir)
    for aanwezig in (True, False):
        veilig = tmp_path / "tijdelijk-bewaard"
        if not aanwezig:
            cursor.rename(veilig)
        try:
            gemaakt = client.post("/api/v1/backups/bundle")
        finally:
            if not aanwezig:
                veilig.rename(cursor)
        assert gemaakt.status_code == 200
        assert client.post("/api/v1/notify/offsite/run").json()["ok"]
        dest = tmp_path / ("volledig" if aanwezig else "onvolledig")
        item = rapport(machine, headers)
        with monkeypatch.context() as herstel:
            herstel.setenv("MG_AIO_CONFIG", str(dest))
            herstel.setenv("MG_SECRETS_DIR", str(dest / "secrets"))
            bundle.restore(doel / gemaakt.json()["file"], inrichting["recovery_code"],
                           doel / bundle.KEYFILE, dest)
            assert wachtrij(dest / "encode/app.db") == taken
            if aanwezig:
                aio.before_services(dict(os.environ), dest, None, migrate_core=True)
            else:
                with pytest.raises(RuntimeError, match="eerder uitgegeven cursorsleutel ontbreekt"):
                    aio.before_services(dict(os.environ), dest, None, migrate_core=True)
        for onderdeel in ("bundle", "offsite"):
            assert item[onderdeel]["verified"] is aanwezig
        assert item["status"] == ("healthy" if aanwezig else "degraded")


@pytest.mark.parametrize("moment", ["klaarzetten", "omwisselen"])
def test_primaire_kopie_behoudt_leesbare_taakvoortgang(
    client, machine, ctx, herstelbronnen, geverifieerde_offsite, tmp_path, moment,
):
    backup, *_ = herstelbronnen
    core = ctx.settings.data_dir
    engine = tmp_path / "encode/app.db"
    taken = vul_taken(engine)
    archief = preupdate.make_set(ctx.settings.backup_dir / preupdate.FOLDER, ctx.db.path,
                                encode_dir=engine.parent)
    (ctx.settings.backup_dir / backup["file"]).with_suffix(".json").unlink()
    headers = key(client)
    aanvraag = preupdate.stage(core, archief, actor="proef")
    assert rapport(machine, headers)["rollback"]["staged"] is True
    marker = core / preupdate.STAGE_MARKER
    fase = core / preupdate.COMMIT_ORIGIN
    if moment == "klaarzetten":
        kopie, manifest = archief / "encode/app.db", archief / "meta.json"
    else:
        # Een historische schrijver kon al een markering met geldige hashes neerzetten.
        kopie, manifest = engine.with_name(engine.name + preupdate.STAGED_SUFFIX), marker
    with closing(sqlite3.connect(kopie)) as db, db:
        db.execute("ALTER TABLE job DROP COLUMN percent")
        assert db.execute("SELECT COUNT(*) FROM job").fetchone() == (2,)
        assert db.execute("PRAGMA integrity_check").fetchall() == [("ok",)]
    meta = json.loads(manifest.read_bytes())
    rij = next(r for r in meta["files"] if r["live"] == "encode/app.db")
    rij.update(size=kopie.stat().st_size, sha256=hashlib.sha256(kopie.read_bytes()).hexdigest())
    if moment == "omwisselen":
        meta.pop("voorbereiding")
        fase.unlink()
        meta["manifest"] = preupdate._manifest(aanvraag["set"], meta["files"])
    manifest.write_text(json.dumps(meta))
    bewaard = marker.read_bytes()
    item = rapport(machine, headers)
    if moment == "klaarzetten":
        voor = fase.read_bytes()
        url = f"/api/v1/backups/{archief.name}/restore"
        assert client.post(url, json={"confirm": archief.name}).status_code == 409
        assert item["core"]["sha256"] is None and item["encoder"]["databases"] == []
        assert item["status"] == "degraded"
        assert fase.read_bytes() == voor
    else:
        assert item["rollback"]["staged"] is None
        result = preupdate.apply_staged(core)
        assert not result["ok"] and "taakvoortgang" in result["error"]
        assert not fase.exists()
    assert marker.read_bytes() == bewaard
    assert wachtrij(engine) == taken
    assert not (ctx.settings.backup_dir / preupdate.ASIDE_FOLDER).exists()


@pytest.mark.parametrize("klaargezet", [False, True])
def test_twee_manifesten_naast_elkaar(client, machine, ctx, herstelbronnen,
                                      geverifieerde_offsite, tmp_path, monkeypatch, klaargezet):
    backup, *_ = herstelbronnen
    (ctx.settings.backup_dir / backup["file"]).with_suffix(".json").unlink()
    oud = tmp_path / "bron-155"
    oud.mkdir()
    vorige = kern_155.__wrapped__(oud, monkeypatch)
    engine = tmp_path / "encode/app.db"
    taken = vul_taken(engine)
    archief = preupdate.make_set(ctx.settings.backup_dir / preupdate.FOLDER, vorige,
                                encode_dir=engine.parent)
    speciaal = archief / preupdate.LEGACY_KEY_META
    headers = key(client)
    url = f"/api/v1/backups/{archief.name}/restore"
    alleen = rapport(machine, headers)
    digest = hashlib.sha256((archief / "mediaguard.db").read_bytes()).hexdigest()
    assert alleen["core"]["sha256"] == digest
    assert alleen["status"] == "healthy"
    assert client.post(url, json={"confirm": archief.name}).status_code == 200
    if not klaargezet:
        assert client.post("/api/v1/backups/terugzetten/annuleren").json()["cancelled"]
    fase = ctx.settings.data_dir / preupdate.COMMIT_ORIGIN
    voor = fase.read_bytes() if klaargezet else None
    shutil.copyfile(speciaal, archief / "meta.json")
    beide = rapport(machine, headers)
    assert beide["core"]["sha256"] is None and beide["encoder"]["databases"] == []
    assert beide["core"]["status"] == beide["encoder"]["status"] == beide["status"] == "degraded"
    lijst = client.get("/api/v1/backups")
    assert lijst.status_code == 200
    rij = next(item for item in lijst.json() if item["file"] == archief.name)
    assert not rij["verified"] and not rij["staged"]
    antwoord = client.post(url, json={"confirm": archief.name})
    assert antwoord.status_code == 409 and "twee verschillende manifesten" in antwoord.text
    if klaargezet:
        assert beide["rollback"]["staged"] is None
        assert not preupdate.apply_staged(ctx.settings.data_dir)["ok"]
        assert fase.read_bytes() == voor
        tweede = archief / "meta.json"
        tweede.unlink()
        starten = event_keys.prepare_for_start

        def tweede_tijdens_proef(database, sleutel):
            shutil.copyfile(speciaal, tweede)
            return starten(database, sleutel)

        with monkeypatch.context() as tijdens:
            tijdens.setattr(event_keys, "prepare_for_start", tweede_tijdens_proef)
            result = preupdate.apply_staged(ctx.settings.data_dir)
        assert tweede.exists() and not result["ok"]
        assert fase.read_bytes() == voor
    else:
        assert not fase.exists()
    assert wachtrij(engine) == taken


@pytest.mark.parametrize("sleutelmanifest", [None, "meta.json", preupdate.LEGACY_KEY_META],
                         ids=["huidig", "historisch-gewoon", "historisch-speciaal"])
def test_geldige_set_naast_dubbel_manifest(
    client, machine, ctx, herstelbronnen, geverifieerde_offsite, tmp_path, monkeypatch,
    sleutelmanifest,
):
    losse, *_ = herstelbronnen
    (ctx.settings.backup_dir / losse["file"]).with_suffix(".json").unlink()
    core = ctx.settings.data_dir
    engine = tmp_path / "encode/app.db"
    taken = vul_taken(engine)
    root = ctx.settings.backup_dir / preupdate.FOLDER
    headers = key(client)

    def klaarzetten(archief):
        url = f"/api/v1/backups/{archief.name}/restore"
        return client.post(url, json={"confirm": archief.name})

    ctx.state_set("proef.archief", {"nummer": 1})
    goed = preupdate.make_set(root, ctx.db.path, encode_dir=engine.parent)
    digest = hashlib.sha256((goed / "mediaguard.db").read_bytes()).hexdigest()
    assert rapport(machine, headers)["core"]["sha256"] == digest
    ctx.state_set("proef.archief", {"nummer": 2})
    with closing(sqlite3.connect(engine)) as db, db:
        db.execute("UPDATE job SET percent=percent+1")
    actief = wachtrij(engine)
    assert sorted(rij[4] for rij in actief) == [38, 83]
    bron = ctx.db.path
    if sleutelmanifest:
        oud = tmp_path / "bron-155"
        oud.mkdir()
        bron = kern_155.__wrapped__(oud, monkeypatch)
    ander = preupdate.make_set(root, bron, encode_dir=engine.parent)
    nieuwste = hashlib.sha256((ander / "mediaguard.db").read_bytes()).hexdigest()
    assert nieuwste != digest
    assert rapport(machine, headers)["core"]["sha256"] == nieuwste
    assert klaarzetten(goed).status_code == 200
    assert client.post("/api/v1/backups/terugzetten/annuleren").json()["cancelled"]

    normaal, speciaal = ander / "meta.json", ander / preupdate.LEGACY_KEY_META
    if sleutelmanifest:
        shutil.copyfile(speciaal, normaal)
        zonder = speciaal if sleutelmanifest == normaal.name else normaal
        meta = json.loads(zonder.read_bytes())
        assert any("legacy_cursor_key" in rij for rij in meta["files"])
        for rij in meta["files"]:
            rij.pop("legacy_cursor_key", None)
        zonder.write_text(json.dumps(meta))
    else:
        shutil.copyfile(normaal, speciaal)
        bestanden = json.loads(normaal.read_bytes())["files"]
        assert all("legacy_cursor_key" not in rij for rij in bestanden)
    manifesten = sorted([normaal.read_bytes(), speciaal.read_bytes()])
    geweigerd = klaarzetten(ander)
    assert geweigerd.status_code == 409 and "twee verschillende manifesten" in geweigerd.text
    assert not (core / preupdate.STAGE_MARKER).exists()
    item = rapport(machine, headers)
    assert item["core"]["sha256"] == digest
    assert sorted([normaal.read_bytes(), speciaal.read_bytes()]) == manifesten
    assert klaarzetten(goed).status_code == 200
    marker = core / preupdate.STAGE_MARKER
    fase = core / preupdate.COMMIT_ORIGIN
    bewaard = marker.read_bytes(), fase.read_bytes()
    geweigerd = klaarzetten(ander)
    assert geweigerd.status_code == 409 and "twee verschillende manifesten" in geweigerd.text
    assert (marker.read_bytes(), fase.read_bytes()) == bewaard
    assert ctx.state_get("proef.archief") == {"nummer": 2} and wachtrij(engine) == actief
    assert speciaal.is_file() and normaal.exists() == (sleutelmanifest is None)
    assert sorted(p.read_bytes() for p in ander.glob("*.json")) == manifesten

    offline = tmp_path / "offline"
    shutil.copytree(core, offline / "core", ignore=shutil.ignore_patterns("mediaguard.pool-fifo"))
    shutil.copytree(engine.parent, offline / "encode")
    shutil.copytree(tmp_path / "secrets", offline / "secrets")
    monkeypatch.setenv("MG_AIO_CONFIG", str(offline))
    monkeypatch.setenv("MG_SECRETS_DIR", str(offline / "secrets"))
    assert preupdate.apply_staged(offline / "core")["ok"]
    assert wachtrij(offline / "encode/app.db") == taken
    buurkopie = offline / "core/backups" / preupdate.FOLDER / ander.name
    with pytest.raises(preupdate.SetError, match="twee verschillende manifesten"):
        preupdate.trial_set(offline / "core", buurkopie)
    assert sorted(p.read_bytes() for p in buurkopie.glob("*.json")) == manifesten
    hersteld = offline / "core/mediaguard.db"
    with closing(sqlite3.connect(hersteld.as_uri() + "?mode=ro", uri=True)) as db:
        waarde, = db.execute("SELECT value_json FROM system_state WHERE key=?",
                             ("proef.archief",)).fetchone()
        assert json.loads(waarde) == {"nummer": 1}


def test_tweede_manifest_na_proef_laat_de_klaargezette_keuze_intact(
    client, ctx, herstelbronnen, tmp_path, monkeypatch,
):
    core = ctx.settings.data_dir
    root = ctx.settings.backup_dir / preupdate.FOLDER
    archief = preupdate.make_set(root, ctx.db.path, encode_dir=tmp_path / "encode")
    preupdate.stage(core, archief, actor="proef")
    fase, marker = core / preupdate.COMMIT_ORIGIN, core / preupdate.STAGE_MARKER
    bewaard = fase.read_bytes(), marker.read_bytes()
    proef = preupdate.trial_set

    def tweede_na_proef(kern, gekozen):
        result = proef(kern, gekozen)
        shutil.copyfile(gekozen / "meta.json", gekozen / preupdate.LEGACY_KEY_META)
        return result

    monkeypatch.setattr(preupdate, "trial_set", tweede_na_proef)
    url = f"/api/v1/backups/{archief.name}/restore"
    antwoord = client.post(url, json={"confirm": archief.name})
    assert antwoord.status_code == 409 and "twee verschillende manifesten" in antwoord.text
    assert (fase.read_bytes(), marker.read_bytes()) == bewaard


def test_geweigerde_startcontrole_publiceert_geen_herstelbewijs(
    client, machine, ctx, herstelbronnen, tmp_path, monkeypatch,
):
    core = ctx.settings.data_dir
    engine = tmp_path / "encode/app.db"
    taken = vul_taken(engine)
    archief = preupdate.make_set(ctx.settings.backup_dir / preupdate.FOLDER, ctx.db.path,
                                encode_dir=engine.parent)
    preupdate.stage(core, archief, actor="proef")
    headers = key(client)
    marker, fase = core / preupdate.STAGE_MARKER, core / preupdate.COMMIT_ORIGIN
    voor = marker.read_bytes(), fase.read_bytes()
    doel = tmp_path / "offsite"
    doel.mkdir()
    ctx.config.set("global", "", "backup.offsite.kind", "path", "proef")
    ctx.config.set("global", "", "backup.offsite.path", str(doel), "proef")
    kopieen = []

    def weiger(database, sleutel, owner=None):
        assert database.is_file() and database != ctx.db.path
        assert sleutel.parent.parent.parent == database.parent.parent
        kopieen.append(database)
        raise PermissionError("de startsleutel is onleesbaar in de proef")

    monkeypatch.setattr(event_keys, "prepare_for_start", weiger)
    assert client.post("/api/v1/backups/bundle").status_code == 200
    assert client.post("/api/v1/notify/offsite/run").json()["ok"]
    item = rapport(machine, headers)
    assert all(item[k]["status"] == "degraded" for k in ("core", "encoder", "bundle", "offsite"))
    assert item["core"]["sha256"] is None and item["rollback"]["staged"] is None
    url = f"/api/v1/backups/{archief.name}/restore"
    assert client.post(url, json={"confirm": archief.name}).status_code == 409
    assert not preupdate.apply_staged(core)["ok"]
    assert (marker.read_bytes(), fase.read_bytes()) == voor and wachtrij(engine) == taken
    assert kopieen and all(not path.parent.parent.exists() for path in kopieen)
