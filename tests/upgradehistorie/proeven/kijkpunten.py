"""Herhaal de crashmatrix met een bestaande koppeling naar twee simulatorgebruikers."""

import asyncio
import copy
from contextlib import closing

import pytest
from proeven import Kijkproef, build_context, instellingen, register_settings, service

metingen = []


@pytest.fixture(autouse=True)
def kijkgegevens(request, tmp_path, installatie, monkeypatch):
    proef = Kijkproef()
    instellingen_app = instellingen(tmp_path)
    ctx = build_context(instellingen_app)
    with closing(ctx.db):
        register_settings()
        ctx.config.set("global", "", "mediaserver.kind", "emby", "proef")
        ctx.config.set("global", "", "mediaserver.url", "http://emby.invalid", "proef")
        ctx.secrets.set(service.SECRET_KEY, proef.sleutel, "proef")
        bestaand = proef.koppel(ctx)
    nul = copy.deepcopy(proef.lees())
    assert len(nul) == 2 and nul == proef.gegevens
    proeven = request.module
    start, onderbreek = proeven.herstart, proeven.onderbreek
    onderbroken = False
    starts = 0

    def controle_bestaand():
        assert [s.user for s in asyncio.run(bestaand.sessions())] == ["proef-1"]
        assert proef.lees() == nul

    def stop(*args):
        nonlocal onderbroken
        onderbreek(*args)
        onderbroken = True
        controle_bestaand()

    def controle_start(*args):
        nonlocal starts
        try:
            start(*args)
        finally:
            if onderbroken:
                controle_bestaand()
        if onderbroken:
            heropend = build_context(instellingen_app)
            with closing(heropend.db):
                proef.koppel(heropend)
                assert proef.lees() == nul
            starts += 1

    monkeypatch.setattr(proeven, "onderbreek", stop)
    monkeypatch.setattr(proeven, "herstart", controle_start)
    yield
    assert onderbroken and starts == 2
    assert all(methode == "GET" for methode, _ in proef.verzoeken)
    metingen.append(starts)


def pytest_sessionfinish(session, exitstatus):
    if exitstatus == 0:
        assert len(metingen) == 32 and sum(metingen) == 64
