"""SABnzbd-categorieën die Sonarr/Radarr gebruiken worden automatisch aangemaakt
(v1.1.0: 'Category does not exist' bij Sonarr Anime en Radarr Animefilms)."""

from __future__ import annotations

import json
from typing import Any

import httpx
import pytest

from mediaguard.downloads import sab_categories as SC
from mediaguard.provisioning import store


class FakeSabApi:
    def __init__(self, cats: list[str]) -> None:
        self.cats = list(cats)
        self.calls: list[dict[str, Any]] = []

    def handler(self, request: httpx.Request) -> httpx.Response:
        q = dict(request.url.params)
        self.calls.append(q)
        assert q.get("apikey") == "sabkey"
        if q.get("mode") == "get_config":
            return httpx.Response(200, json={"config": {"categories": [
                {"name": c, "dir": c, "pp": "", "script": "Default", "priority": -100}
                for c in self.cats]}})
        if q.get("mode") == "set_config":
            assert q.get("section") == "categories"
            self.cats.append(q["keyword"])
            return httpx.Response(200, json={"status": True})
        return httpx.Response(400, text=json.dumps(q))


def _configure_sab(ctx, fake: FakeSabApi | None, *, key: bool = True) -> None:
    store.save(ctx, {"download_clients": [{
        "name": "SABnzbd", "implementation": "Sabnzbd", "host": "192.0.2.10",
        "port": 30055, "api_key": "sabkey" if key else None}]}, "test")
    if fake is not None:
        ctx.services["download_client_transports"] = {
            "SABnzbd": httpx.MockTransport(fake.handler)}


def _targets(monkeypatch) -> None:
    async def collect(ctx):
        t = SC.SabTarget("192.0.2.10", 30055)
        t.categories = {"tv": {"sonarr-series"}, "tv-anime": {"sonarr-anime"},
                        "movies-anime": {"radarr-animefilms"}}
        return {(t.host, t.port): t}
    monkeypatch.setattr(SC, "_collect", collect)


@pytest.mark.asyncio
async def test_missing_categories_are_created_once(ctx, monkeypatch):
    fake = FakeSabApi(["*", "tv", "movies"])
    _configure_sab(ctx, fake)
    _targets(monkeypatch)
    state = await SC.ensure(ctx)
    assert sorted(state["created"]) == ["movies-anime", "tv-anime"]
    sets = [c for c in fake.calls if c.get("mode") == "set_config"]
    assert {c["keyword"] for c in sets} == {"tv-anime", "movies-anime"}
    assert all(c["dir"] == c["keyword"] for c in sets)  # map = naam
    n = ctx.db.fetch_one("SELECT COUNT(*) AS n FROM notifications WHERE title LIKE ?",
                         ("SABnzbd: categorie%",))["n"]
    assert n == 1
    audits = ctx.db.fetch_one("SELECT COUNT(*) AS n FROM audit_events WHERE action=?",
                              ("downloads.sab_category_created",))["n"]
    assert audits == 2
    # tweede ronde: niets meer aan te maken, bestaande categorieën nooit gewijzigd
    fake.calls.clear()
    state = await SC.ensure(ctx)
    assert state["created"] == []
    assert not [c for c in fake.calls if c.get("mode") == "set_config"]
    assert SC.missing_categories(ctx) == {}


@pytest.mark.asyncio
async def test_without_api_key_only_a_precise_attention_item(ctx, monkeypatch):
    _configure_sab(ctx, None, key=False)
    _targets(monkeypatch)
    state = await SC.ensure(ctx)
    assert state["created"] == [] and state["no_key"]
    assert SC.attention_items(ctx) == []  # zonder bewijs geen claim
    SC.note_test_failure(ctx, "sonarr-anime", "Sonarr (sonarr-anime) meldt: Category does "
                                              "not exist", "tv-anime")
    items = SC.attention_items(ctx)
    assert items and "tv-anime" in items[0]["title_nl"]
    assert "API-sleutel" in items[0]["detail_nl"]
    # de downloadbewaking markeert voor deze instantie niets als 'verdwenen'
    assert SC.missing_categories(ctx) == {"sonarr-anime": ["tv-anime"]}
    SC.clear_test_failure(ctx, "sonarr-anime")
    assert SC.attention_items(ctx) == []


def test_other_test_errors_are_not_category_problems(ctx):
    SC.note_test_failure(ctx, "sonarr-series", "Unable to connect to SABnzbd", "tv")
    assert SC.attention_items(ctx) == []


def test_category_read_from_arr_client_fields():
    sonarr = {"implementation": "Sabnzbd", "fields": [{"name": "tvCategory",
                                                       "value": "tv-anime"}]}
    radarr = {"implementation": "Sabnzbd", "fields": [{"name": "movieCategory",
                                                       "value": "movies-anime"}]}
    assert SC._category_of("sonarr", sonarr) == "tv-anime"
    assert SC._category_of("radarr", radarr) == "movies-anime"


def test_attention_source_uses_state(ctx):
    from mediaguard import attention

    SC.note_test_failure(ctx, "radarr-animefilms", "Category does not exist", "movies-anime")
    keys = [i["key"] for i in attention.collect_local(ctx)]
    assert "downloads.sab_category_missing" in keys
