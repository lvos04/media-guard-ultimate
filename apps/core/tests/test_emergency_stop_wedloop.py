"""Noodstopwedlopen: eigen database, twee onafhankelijke applicatiecontexten, geen netwerk."""

from __future__ import annotations

import asyncio
import json
import logging
import sqlite3
import subprocess
import sys
import threading
from concurrent.futures import ThreadPoolExecutor
from contextlib import contextmanager

import pytest
from fastapi.testclient import TestClient

from mediaguard import hookguard, mobile
from mediaguard.api import system
from mediaguard.app import create_app
from mediaguard.auth import Principal

ROUTE = "/api/v1/system/emergency-stop"
CONTROL = "/api/v1/app/control"
ADMIN = Principal(username="admin", role="admin")
HUB = Principal(username="apikey:hub", role="viewer", machine=True,
                scopes=("emergency_stop:on",))


@pytest.fixture
def machine(app):  # type: ignore[no-untyped-def]
    with TestClient(app) as client:
        yield client


def _aan(ctx, principal, weg, reden):  # type: ignore[no-untyped-def]
    if weg == CONTROL:
        return asyncio.run(mobile.control(mobile.ControlBody(
            action="emergency_stop_on", confirm=True, params={"reason": reden}),
            principal, ctx))["result"]
    return system.emergency_stop(system.EmergencyBody(active=True, reason=reden), principal, ctx)


@pytest.mark.parametrize("sleutelweg", [ROUTE, CONTROL])
@pytest.mark.parametrize("beheerderweg", [ROUTE, CONTROL])
def test_sleutel_bewaart_gelijktijdige_beheerder(  # type: ignore[no-untyped-def]
        client, machine, ctx, monkeypatch, sleutelweg, beheerderweg):
    """De beheerder voltooit tijdens de pauze van de sleutel, op beide wegen.

    Met herstel pauzeert de sleutel vóór BEGIN IMMEDIATE: de vergrendelde lezing moet
    daarna vers zijn. Zonder herstel pauzeert dezelfde proef na de losse lezing van
    'uit', precies de planning uit de herreview. De eindvoorwaarden zijn identiek.
    """
    gemaakt = client.post("/api/v1/auth/api-keys", json={
        "name": "hub", "role": "viewer", "scopes": ["emergency_stop:on"]})
    assert gemaakt.status_code == 200
    headers = {"X-Api-Key": gemaakt.json()["key"]}
    peer = create_app(ctx.settings).state.ctx
    assert peer.db is not ctx.db  # eigen verbinding en procesvergrendeling, zoals recovery
    gelezen, verder = threading.Event(), threading.Event()
    lokaal = threading.local()
    transaction, state_get = ctx.db.transaction, ctx.state_get
    may_set = system.may_set_emergency_stop

    def pauzeer():
        if getattr(lokaal, "sleutel", False) and not getattr(lokaal, "gepauzeerd", False):
            lokaal.gepauzeerd = True
            gelezen.set()
            assert verder.wait(10), "De beheerder heeft de sleutel niet vrijgegeven"

    @contextmanager
    def bij_transactie():
        pauzeer()
        with transaction() as conn:
            yield conn

    def na_lezing(key, default=None):
        value = state_get(key, default)
        if key == "emergency_stop":
            pauzeer()
        return value

    def markeer(principal, active):
        lokaal.sleutel = principal.scopes is not None
        return may_set(principal, active)

    def sleutel():
        try:
            if sleutelweg == CONTROL:
                return _aan(ctx, HUB, sleutelweg, "Hub-redenen")
            r = machine.post(ROUTE, headers=headers,
                             json={"active": True, "reason": "Hub-redenen"})
            assert r.status_code == 200
            return r.json()
        finally:
            ctx.db.close()

    monkeypatch.setattr(ctx.db, "transaction", bij_transactie)
    monkeypatch.setattr(ctx, "state_get", na_lezing)
    monkeypatch.setattr(system, "may_set_emergency_stop", markeer)
    try:
        with ThreadPoolExecutor(max_workers=1) as pool:
            poging = pool.submit(sleutel)
            try:
                assert gelezen.wait(10), "De sleutel bereikte de lezing niet"
                beheerder = _aan(peer, ADMIN, beheerderweg, "Onderhoud: niet opheffen")
                vlag = hookguard.flag_path(peer).read_bytes()
            finally:
                verder.set()
            assert poging.result(10) == beheerder
        assert state_get("emergency_stop") == beheerder
        assert beheerder["by"] == "admin" and beheerder["since"]
        assert hookguard.flag_path(ctx).read_bytes() == vlag
        audits = ctx.db.fetch_dicts("SELECT action,old_value,new_value FROM audit_events "
                                    "WHERE actor='apikey:hub'")
        assert len(audits) == 1
        assert audits[0]["action"] == "system.emergency_stop_repeated"
        assert json.loads(audits[0]["old_value"]) == beheerder
        assert audits[0]["new_value"] is None
    finally:
        verder.set()
        peer.db.close()


def test_verversen_vlag_overschrijft_geen_nieuwere_noodstop(ctx, monkeypatch):  # type: ignore[no-untyped-def]
    """Ook de achtergrondschrijver deelt de transactie met core en recovery."""
    peer = create_app(ctx.settings).state.ctx
    gelezen, verder = threading.Event(), threading.Event()
    transaction, state_get = ctx.db.transaction, ctx.state_get
    gepauzeerd = False

    def pauzeer():
        nonlocal gepauzeerd
        if not gepauzeerd:
            gepauzeerd = True
            gelezen.set()
            assert verder.wait(10), "Het verversen is niet vrijgegeven"

    @contextmanager
    def bij_transactie():
        pauzeer()
        with transaction() as conn:
            yield conn

    def na_lezing(key, default=None):
        value = state_get(key, default)
        if key == "emergency_stop":
            pauzeer()
        return value

    def ververs():
        try:
            return hookguard.write_flag(ctx)
        finally:
            ctx.db.close()

    monkeypatch.setattr(ctx.db, "transaction", bij_transactie)
    monkeypatch.setattr(ctx, "state_get", na_lezing)
    try:
        with ThreadPoolExecutor(max_workers=1) as pool:
            poging = pool.submit(ververs)
            try:
                assert gelezen.wait(10), "De verversing bereikte de lezing niet"
                beheerder = _aan(peer, ADMIN, ROUTE, "Onderhoud")
            finally:
                verder.set()
            assert poging.result(10)
        vlag = json.loads(hookguard.flag_path(ctx).read_text())
        assert vlag["active"] is True
        assert vlag["reason"] == beheerder["reason"]
        assert vlag["since"] == beheerder["since"]
    finally:
        verder.set()
        peer.db.close()


@pytest.mark.parametrize("foutplek", ["state_get", "state_set", "audit"])
def test_noodstoptransactie_wordt_bij_fout_vrijgegeven(  # type: ignore[no-untyped-def]
        ctx, monkeypatch, foutplek):
    _aan(ctx, ADMIN, ROUTE, "Bestaande noodstop")
    voor = ctx.state_get("emergency_stop")
    vlag = hookguard.flag_path(ctx).read_bytes()

    def fout(*args, **kwargs):
        raise RuntimeError("Opzettelijke testfout")

    with monkeypatch.context() as m:
        m.setattr(system if foutplek == "audit" else ctx, foutplek, fout)
        with pytest.raises(RuntimeError, match="Opzettelijke testfout"):
            _aan(ctx, ADMIN, ROUTE, "Mislukte wijziging")
    assert not ctx.db._connect().in_transaction
    assert ctx.state_get("emergency_stop") == voor
    assert hookguard.flag_path(ctx).read_bytes() == vlag
    # Onafhankelijke SQLite-verbinding: een achtergebleven schrijfslot faalt direct.
    with sqlite3.connect(ctx.db.path, timeout=0) as conn:
        conn.execute("BEGIN IMMEDIATE")
    assert _aan(ctx, ADMIN, ROUTE, "Volgende poging")["reason"] == "Volgende poging"


def test_vlagfout_laat_geen_vergrendeling_achter(ctx, monkeypatch):  # type: ignore[no-untyped-def]
    from pathlib import Path

    def fout(*args, **kwargs):
        raise OSError("Opzettelijke schrijffout")

    with monkeypatch.context() as m:
        m.setattr(Path, "write_text", fout)
        assert hookguard.write_flag(ctx) is False
    with sqlite3.connect(ctx.db.path, timeout=0) as conn:
        conn.execute("BEGIN IMMEDIATE")
    assert hookguard.write_flag(ctx)


def test_vlag_databasefout_is_niet_fataal_en_wordt_ingehaald(ctx, caplog):  # type: ignore[no-untyped-def]
    """Een onafhankelijke schrijver blokkeert alleen de vlag, zonder wachttijd."""
    assert hookguard.write_flag(ctx)
    vlag_voor = hookguard.flag_path(ctx).read_bytes()
    ctx.state_set("emergency_stop", {"active": True, "reason": "Onderhoud"})
    conn = ctx.db._connect()
    timeout = conn.execute("PRAGMA busy_timeout").fetchone()[0]
    conn.execute("PRAGMA busy_timeout=0")
    try:
        with sqlite3.connect(ctx.db.path, timeout=0) as schrijver:
            schrijver.execute("BEGIN IMMEDIATE")
            with caplog.at_level(logging.WARNING):
                assert hookguard.write_flag(ctx) is False
            assert hookguard.flag_path(ctx).read_bytes() == vlag_voor
    finally:
        conn.execute(f"PRAGMA busy_timeout={timeout}")
    waarschuwingen = [r for r in caplog.records if r.name == hookguard.__name__]
    assert len(waarschuwingen) == 1
    assert waarschuwingen[0].levelno == logging.WARNING
    assert "volgende verversing" in waarschuwingen[0].getMessage()
    assert hookguard.write_flag(ctx)
    vlag = json.loads(hookguard.flag_path(ctx).read_text())
    assert vlag["active"] is True and vlag["reason"] == "Onderhoud"


@pytest.mark.parametrize("met_scope", [False, True])
@pytest.mark.parametrize("fout", ["databaseslot", "bestand", "onverwacht"])
def test_vastgelegde_noodstop_blijft_werken_bij_vlagfout(  # type: ignore[no-untyped-def]
        client, app, ctx, monkeypatch, caplog, met_scope, fout):
    """Fout precies na de commit: 200, audit, één publicatie, wekken en later herstel."""
    gemaakt = client.post("/api/v1/auth/api-keys", json={
        "name": "vlagproef", "role": "viewer" if met_scope else "operator",
        "scopes": ["emergency_stop:on"] if met_scope else None})
    assert gemaakt.status_code == 200
    assert hookguard.write_flag(ctx)
    vlag_voor = hookguard.flag_path(ctx).read_bytes()
    origineel = hookguard.write_flag
    gepubliceerd, gewekt = [], []
    monkeypatch.setattr(ctx.events, "publish", lambda *args: gepubliceerd.append(args))
    monkeypatch.setattr(ctx.services["jobs"], "wake", lambda: gewekt.append(True))

    def vlagfout(c):
        assert not c.db._connect().in_transaction
        assert c.state_get("emergency_stop")["active"] is True
        if fout == "onverwacht":
            raise RuntimeError("Opzettelijke testfout met vertrouwelijke details")
        if fout == "bestand":
            def schrijffout(*args, **kwargs):
                raise OSError("Opzettelijke schrijffout")

            with monkeypatch.context() as m:
                m.setattr(hookguard.Path, "write_text", schrijffout)
                return origineel(c)
        conn = c.db._connect()
        timeout = conn.execute("PRAGMA busy_timeout").fetchone()[0]
        conn.execute("PRAGMA busy_timeout=0")
        try:
            with sqlite3.connect(c.db.path, timeout=0) as schrijver:
                schrijver.execute("BEGIN IMMEDIATE")
                return origineel(c)
        finally:
            conn.execute(f"PRAGMA busy_timeout={timeout}")

    verzoek = TestClient(app, raise_server_exceptions=False)
    try:
        with monkeypatch.context() as m:
            m.setattr(hookguard, "write_flag", vlagfout)
            with caplog.at_level(logging.WARNING):
                antwoord = verzoek.post(ROUTE, headers={"X-Api-Key": gemaakt.json()["key"]},
                                        json={"active": True, "reason": "Onderhoud"})
    finally:
        verzoek.close()
    assert antwoord.status_code == 200
    toestand = ctx.state_get("emergency_stop")
    assert toestand["active"] is True and antwoord.json() == toestand
    assert gepubliceerd == [("system.emergency_stop", toestand)]
    assert gewekt == [True]
    audits = ctx.db.fetch_dicts("SELECT new_value FROM audit_events "
                                "WHERE action='system.emergency_stop'")
    assert len(audits) == 1 and json.loads(audits[0]["new_value"]) == toestand
    assert hookguard.flag_path(ctx).read_bytes() == vlag_voor
    waarschuwingen = [r for r in caplog.records
                      if r.name in (hookguard.__name__, system.__name__)]
    assert len(waarschuwingen) == (0 if fout == "bestand" else 1)
    assert all(r.levelno == logging.WARNING and r.exc_info is None for r in waarschuwingen)
    assert all("vertrouwelijke details" not in r.getMessage() for r in waarschuwingen)
    assert origineel(ctx)
    vlag = json.loads(hookguard.flag_path(ctx).read_text())
    assert vlag["active"] is True
    assert vlag["since"] == toestand["since"] and vlag["reason"] == toestand["reason"]


def test_mislukte_toestandswijziging_blijft_een_fout(  # type: ignore[no-untyped-def]
        client, ctx, monkeypatch):
    """De best-effort vlag mag een echte databasefout bij het wijzigen niet verbergen."""
    assert hookguard.write_flag(ctx)
    toestand_voor = ctx.state_get("emergency_stop")
    vlag_voor = hookguard.flag_path(ctx).read_bytes()
    gepubliceerd, gewekt = [], []
    monkeypatch.setattr(ctx.events, "publish", lambda *args: gepubliceerd.append(args))
    monkeypatch.setattr(ctx.services["jobs"], "wake", lambda: gewekt.append(True))

    def fout(*args, **kwargs):
        raise sqlite3.OperationalError("database is locked")

    with monkeypatch.context() as m:
        m.setattr(ctx, "state_set", fout)
        with pytest.raises(sqlite3.OperationalError, match="database is locked"):
            client.post(ROUTE, json={"active": True, "reason": "Onderhoud"})
    assert ctx.state_get("emergency_stop") == toestand_voor
    assert hookguard.flag_path(ctx).read_bytes() == vlag_voor
    assert gepubliceerd == [] and gewekt == []
    assert ctx.db.fetch_dicts("SELECT * FROM audit_events "
                              "WHERE action='system.emergency_stop'") == []


def test_geen_noodstopslot_tijdens_publiceren_of_wekken(ctx, monkeypatch):  # type: ignore[no-untyped-def]
    controles = []

    def controle(*args, **kwargs):
        assert not ctx.db._connect().in_transaction
        with sqlite3.connect(ctx.db.path, timeout=0) as conn:
            conn.execute("BEGIN IMMEDIATE")
        controles.append(True)

    monkeypatch.setattr(ctx.events, "publish", controle)
    monkeypatch.setattr(ctx.services["jobs"], "wake", controle)
    assert _aan(ctx, HUB, ROUTE, "Hub")["active"]
    assert len(controles) == 2


@pytest.mark.parametrize("al_actief", [False, True])
def test_noodstop_leest_en_beslist_met_een_slot_tussen_processen(  # type: ignore[no-untyped-def]
        ctx, monkeypatch, al_actief):
    """Een Python-slot of een transactie die pas na lezen begint, is onvoldoende."""
    if al_actief:
        _aan(ctx, ADMIN, ROUTE, "Beheerder")
    state_get, audit = ctx.state_get, system.audit
    controles = []
    script = """
import sqlite3, sys
conn = sqlite3.connect(sys.argv[1], timeout=0)
try:
    conn.execute('BEGIN IMMEDIATE')
except sqlite3.OperationalError as exc:
    assert 'locked' in str(exc)
    print('bezet')
else:
    conn.rollback()
    print('vrij')
finally:
    conn.close()
"""

    def controle():
        r = subprocess.run([sys.executable, "-c", script, str(ctx.db.path)],
                           capture_output=True, text=True, timeout=10, check=True)
        assert r.stdout.strip() == "bezet", "Een ander proces kan de noodstop overschrijven"
        controles.append(True)

    def bij_lezen(key, default=None):
        if key == "emergency_stop":
            controle()
        return state_get(key, default)

    def bij_audit(*args, **kwargs):
        controle()
        return audit(*args, **kwargs)

    monkeypatch.setattr(ctx, "state_get", bij_lezen)
    monkeypatch.setattr(system, "audit", bij_audit)
    assert _aan(ctx, HUB, ROUTE, "Hub")["active"]
    # Activeren: lezen, audit en vlaglezing. Herhalen: alleen lezen en audit.
    assert len(controles) == (2 if al_actief else 3)
