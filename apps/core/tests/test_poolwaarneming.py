"""Poolmeldingen volgen de actuele toestemming, ook bij een uitsluitend lezend verzoek."""

import asyncio

import pytest

from mediaguard import monitor
from mediaguard import storage_forecast as storage
from mediaguard.app import create_app


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
