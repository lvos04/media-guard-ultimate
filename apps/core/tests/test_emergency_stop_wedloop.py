"""Noodstopwedlopen: eigen database, twee onafhankelijke applicatiecontexten, geen netwerk."""

from __future__ import annotations

import asyncio
import json
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
