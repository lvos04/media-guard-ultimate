"""ArrClient: headers, retries met backoff, foutvertaling, redactie, paging, veilige defaults."""

from __future__ import annotations

import httpx
import pytest
import respx

from mediaguard.arr.client import (
    ArrAuthError,
    ArrClient,
    ArrNotFound,
    ArrTimeout,
    ArrUnavailable,
    ArrValidationError,
)

BASE = "http://sonarr.test:8989"


def client(app: str = "sonarr", retries: int = 2) -> ArrClient:
    return ArrClient(BASE, "sekrit-key-123", instance_id="sonarr-series", app=app,
                     retries=retries, backoff=0)


@respx.mock
async def test_api_key_in_header_never_in_url():
    route = respx.get(f"{BASE}/api/v3/system/status").mock(
        return_value=httpx.Response(200, json={"appName": "Sonarr", "version": "4.0.14"}))
    assert (await client().system_status())["version"] == "4.0.14"
    req = route.calls.last.request
    assert req.headers["X-Api-Key"] == "sekrit-key-123"
    assert "sekrit" not in str(req.url)


@respx.mock
async def test_retry_on_503_then_success():
    route = respx.get(f"{BASE}/api/v3/series").mock(side_effect=[
        httpx.Response(503), httpx.Response(502), httpx.Response(200, json=[{"id": 1}])])
    assert await client().series_list() == [{"id": 1}]
    assert route.call_count == 3


@respx.mock
async def test_retries_exhausted_maps_to_unavailable():
    respx.get(f"{BASE}/api/v3/series").mock(return_value=httpx.Response(503))
    with pytest.raises(ArrUnavailable) as exc:
        await client(retries=1).series_list()
    assert exc.value.status == 503 and exc.value.code == "ARR_UNAVAILABLE"


@respx.mock
async def test_connect_error_retried_and_timeout_mapped():
    route = respx.get(f"{BASE}/api/v3/health").mock(side_effect=[
        httpx.ConnectError("boom"), httpx.Response(200, json=[])])
    assert await client().health() == []
    assert route.call_count == 2
    respx.get(f"{BASE}/api/v3/queue").mock(side_effect=httpx.ReadTimeout("slow"))
    with pytest.raises(ArrTimeout):
        await client(retries=0).queue()


@respx.mock
async def test_post_not_retried_after_read_timeout():
    route = respx.post(f"{BASE}/api/v3/command").mock(side_effect=httpx.ReadTimeout("slow"))
    with pytest.raises(ArrTimeout):
        await client().command("RssSync")
    assert route.call_count == 1  # geen dubbele zoekopdracht


@respx.mock
async def test_error_mapping_and_redaction():
    respx.get(f"{BASE}/api/v3/series/9").mock(return_value=httpx.Response(404, json={
        "message": "Series with ID 9 does not exist"}))
    with pytest.raises(ArrNotFound):
        await client().series_get(9)
    respx.get(f"{BASE}/api/v3/system/status").mock(return_value=httpx.Response(401))
    with pytest.raises(ArrAuthError):
        await client().system_status()
    respx.post(f"{BASE}/api/v3/indexer").mock(return_value=httpx.Response(400, json=[
        {"propertyName": "ApiKey", "errorMessage": "Invalid apikey=abcdef123 provided",
         "severity": "error"}]))
    with pytest.raises(ArrValidationError) as exc:
        await client().provider_create("indexer", {"name": "x"})
    assert "abcdef123" not in exc.value.message
    assert exc.value.details[0]["property"] == "ApiKey"
    assert "abcdef123" not in str(exc.value.details)


@respx.mock
async def test_paging_collects_all_pages():
    def page(request: httpx.Request) -> httpx.Response:
        n = int(request.url.params["page"])
        recs = [{"id": i} for i in range((n - 1) * 2, min(n * 2, 5))]
        return httpx.Response(200, json={"page": n, "pageSize": 2, "totalRecords": 5,
                                         "records": recs})

    route = respx.get(f"{BASE}/api/v3/wanted/missing").mock(side_effect=page)
    recs = await client().wanted_missing(all_pages=True, page_size=2)
    assert [r["id"] for r in recs] == [0, 1, 2, 3, 4]
    assert route.call_count == 3
    assert route.calls.last.request.url.params["monitored"] == "true"


@respx.mock
async def test_delete_never_deletes_files_by_default():
    route = respx.delete(f"{BASE}/api/v3/series/3").mock(return_value=httpx.Response(200))
    await client().series_delete(3)
    assert route.calls.last.request.url.params["deleteFiles"] == "false"
    mroute = respx.delete("http://radarr.test/api/v3/movie/4").mock(
        return_value=httpx.Response(200))
    radarr = ArrClient("http://radarr.test", "k" * 10, instance_id="radarr-films",
                       app="radarr", retries=0)
    await radarr.movie_delete(4)
    assert mroute.calls.last.request.url.params["deleteFiles"] == "false"


@respx.mock
async def test_language_profiles_absent_on_v4_and_radarr():
    respx.get(f"{BASE}/api/v3/languageprofile").mock(return_value=httpx.Response(404))
    assert await client().language_profiles() == []
    radarr = ArrClient("http://radarr.test", "k" * 10, instance_id="r", app="radarr")
    assert await radarr.language_profiles() == []


async def test_command_allowlist_and_app_guard():
    with pytest.raises(ValueError):
        await client().command("DeleteEverything")
    with pytest.raises(ValueError):
        await client().command("MoviesSearch")  # Radarr-commando op Sonarr
    with pytest.raises(ValueError):
        await client("radarr").series_list()


@respx.mock
async def test_episode_monitor_and_force_save():
    mon = respx.put(f"{BASE}/api/v3/episode/monitor").mock(return_value=httpx.Response(202))
    await client().episodes_monitor([1, 2], False)
    assert mon.calls.last.request.content == b'{"episodeIds":[1,2],"monitored":false}'
    put = respx.put(f"{BASE}/api/v3/indexer/5").mock(
        return_value=httpx.Response(202, json={"id": 5}))
    await client().provider_update("indexer", {"id": 5, "enableRss": False}, force_save=True)
    assert put.calls.last.request.url.params["forceSave"] == "true"


@respx.mock
async def test_history_filters_by_episode_and_movie_shapes():
    respx.get(f"{BASE}/api/v3/history").mock(return_value=httpx.Response(200, json={
        "page": 1, "pageSize": 50, "totalRecords": 2, "records": [
            {"id": 1, "episodeId": 7, "eventType": "grabbed"},
            {"id": 2, "episodeId": 8, "eventType": "grabbed"}]}))
    assert [h["id"] for h in await client().history_for_episode(7)] == [1]
    respx.get("http://radarr.test/api/v3/history/movie").mock(return_value=httpx.Response(
        200, json=[{"id": 3, "movieId": 4, "eventType": "downloadFailed"}]))
    radarr = ArrClient("http://radarr.test", "k" * 10, instance_id="r", app="radarr")
    assert (await radarr.history_for_movie(4))[0]["eventType"] == "downloadFailed"
