"""Poolmeldingen volgen de actuele toestemming, ook bij een uitsluitend lezend verzoek."""

import asyncio
from datetime import datetime, timedelta

import pytest
from test_bewaking import _truenas

from mediaguard import attention, hub_pool, monitor
from mediaguard import storage_forecast as storage
from mediaguard.app import create_app
from mediaguard.connector import items


def _waarschuwing(ctx):
    return any(row["key"] == "storage.pool_unknown" for row in monitor.pool_item(ctx))


def _bewaarstand(ctx):
    return [tuple(row) for row in ctx.db.fetch_all(
        "SELECT key, value_json, updated_at FROM system_state ORDER BY key")]


def test_poolwaarschuwing_na_herstart_tot_nieuwe_meting(ctx, settings, poolhub):
    asyncio.run(storage.refresh_pool(ctx))
    assert not _waarschuwing(ctx)
    assert storage.pool_cleanup_pause_reason(ctx) is None
    asyncio.run(storage.shutdown(ctx))
    nieuw = create_app(settings).state.ctx
    try:
        asyncio.run(storage.startup(nieuw))
        voor = _bewaarstand(nieuw)
        assert _waarschuwing(nieuw)
        assert _bewaarstand(nieuw) == voor
        assert storage.pool_cleanup_pause_reason(nieuw)
        nieuw.services["hub_pool_transport"] = ctx.services["hub_pool_transport"]
        asyncio.run(storage.refresh_pool(nieuw))
        voor = _bewaarstand(nieuw)
        for _ in range(2):
            assert not _waarschuwing(nieuw)
        assert _bewaarstand(nieuw) == voor
        assert poolhub["calls"] == 2
        assert storage.pool_cleanup_pause_reason(nieuw) is None
    finally:
        asyncio.run(storage.shutdown(nieuw))
        nieuw.db.close()


def test_overgang_naar_directe_poolbron_gebruikt_eigen_meettoestemming(
        client, ctx, settings, poolhub, monkeypatch):
    async def bronnen(context):
        return monitor.pool_item(context), True

    monkeypatch.setattr(attention, "collect_observation", bronnen)
    gezond = [{"name": "proef", "status": "ONLINE", "healthy": True, "scan": {}}]
    url = "/connector/v1/items?type=incidents"
    asyncio.run(storage.refresh_pool(ctx))
    assert client.get(url).json()["status"] == "ok"
    _truenas(ctx, gezond)
    assert _waarschuwing(ctx)
    assert client.get(url).json()["status"] == "unknown"
    assert storage.pool_cleanup_pause_reason(ctx)
    asyncio.run(storage.refresh_pool(ctx))
    assert not _waarschuwing(ctx)
    assert client.get(url).json()["status"] == "ok"
    asyncio.run(storage.shutdown(ctx))
    nieuw = create_app(settings).state.ctx
    try:
        asyncio.run(storage.startup(nieuw))
        assert _waarschuwing(nieuw)
        nieuw.services["truenas_transport"] = ctx.services["truenas_transport"]
        asyncio.run(storage.refresh_pool(nieuw))
        assert not _waarschuwing(nieuw)
        assert storage.pool_cleanup_pause_reason(nieuw) is None
        assert poolhub["calls"] == 1
    finally:
        asyncio.run(storage.shutdown(nieuw))
        nieuw.db.close()


@pytest.mark.parametrize("inhoud", [b"!" * 32, b"0" * 32, None])
def test_poolwaarschuwing_leest_kanaal_zonder_toestemming_te_veranderen(ctx, poolhub, inhoud):
    asyncio.run(storage.refresh_pool(ctx))
    with storage._request_pipe(ctx) as kanaal:
        origineel = kanaal.read()
        assert len(origineel) == 32
        verwacht = inhoud if inhoud is not None else origineel + b"!"
        kanaal.write(verwacht)
        voor = _bewaarstand(ctx)
        assert _waarschuwing(ctx)
        assert _bewaarstand(ctx) == voor
        assert kanaal.read() == verwacht
    assert poolhub["calls"] == 1


@pytest.mark.parametrize("wijziging", ["weigering", "adres", "recht", "verval", "ongezond"])
def test_incidentcache_volgt_de_pool_bij_eerste_lezing_en_vervolgpagina(
        client, ctx, poolhub, monkeypatch, wijziging):
    async def bronnen(context):
        vast = [{"key": f"proef.{i}", "severity": "info"} for i in range(2)]
        return vast + monitor.pool_item(context), True

    monkeypatch.setattr(attention, "collect_observation", bronnen)
    monkeypatch.setattr(items, "CACHE_SECONDS", 60)
    asyncio.run(storage.refresh_pool(ctx))
    url = "/connector/v1/items"
    params = {"type": "incidents", "limit": 1}
    eerste = client.get(url, params=params).json()
    assert eerste["status"] == "ok" and eerste["total"] == 2
    vervolg = {**params, "cursor": eerste["next_cursor"]}
    assert client.get(url, params=vervolg).status_code == 200
    assert client.get(url, params=params).json() == eerste

    if wijziging == "weigering":
        poolhub["code"] = 403
        asyncio.run(storage.refresh_pool(ctx))
    elif wijziging == "adres":
        ctx.config.set("global", "", hub_pool.URL_KEY, "https://gewijzigd.example.test", "test")
    elif wijziging == "recht":
        ctx.secrets.delete(hub_pool.SECRET)
    elif wijziging == "verval":
        class Later(datetime):
            @classmethod
            def now(cls, tz=None):
                return datetime.now(tz) + timedelta(minutes=3)

        monkeypatch.setattr(hub_pool, "datetime", Later)
    else:
        poolhub["body"]["items"] = [{"status": "DEGRADED", "gezond": False}]
        asyncio.run(storage.refresh_pool(ctx))

    oproepen = poolhub["calls"]
    assert client.get(url, params=vervolg).status_code == 409
    nieuw = client.get(url, params={"type": "incidents"}).json()
    code = "storage.pool_degraded" if wijziging == "ongezond" else "storage.pool_unknown"
    assert code in {row["cause_code"] for row in nieuw["items"]}
    assert nieuw["status"] == ("ok" if wijziging == "ongezond" else "unknown")
    assert poolhub["calls"] == oproepen
