"""Async client voor de Sonarr v3- en Radarr v3-API (ook Sonarr v4 / Radarr v5+, die nog
steeds ``/api/v3`` gebruiken).

Ontwerp:
- API-sleutel uitsluitend in header ``X-Api-Key`` (nooit in de querystring, dus nooit
  in logs/URL's).
- Timeouts per request; retries met exponentiële backoff voor netwerkfouten, 429 en
  5xx. Niet-idempotente POST's worden alleen herhaald als het verzoek aantoonbaar niet
  is verstuurd (connect-fout).
- Fouten worden vertaald naar ``ArrError``-subklassen met code + geredigeerde melding.
- ``delete_files`` staat overal standaard UIT; bestanden verwijderen kan alleen expliciet.
"""

from __future__ import annotations

import asyncio
import contextlib
import logging
from collections.abc import AsyncIterator, Iterable
from typing import Any

import httpx

from mediaguard.secrets_store import redact

LOGGER = logging.getLogger(__name__)

API_PREFIX = "/api/v3"
RETRY_STATUSES = {429, 500, 502, 503, 504}
IDEMPOTENT = {"GET", "PUT", "DELETE", "HEAD"}

# commando's die MediaGuard mag versturen (UI-acties + backfill)
SONARR_COMMANDS = {
    "EpisodeSearch", "SeasonSearch", "SeriesSearch", "MissingEpisodeSearch",
    "CutoffUnmetEpisodeSearch", "RssSync", "RefreshSeries", "RescanSeries",
    "DownloadedEpisodesScan", "Backup", "ApplicationCheckUpdate", "CheckHealth",
    "RefreshMonitoredDownloads",
    # alleen na expliciete bevestiging (provisioning: 'Bestanden hernoemen')
    "RenameFiles",
}
RADARR_COMMANDS = {
    "MoviesSearch", "MissingMoviesSearch", "CutoffUnmetMoviesSearch", "RssSync",
    "RefreshMovie", "RescanMovie", "DownloadedMoviesScan", "Backup",
    "ApplicationCheckUpdate", "CheckHealth", "RefreshMonitoredDownloads", "RenameFiles",
}
SEARCH_COMMANDS = {
    "EpisodeSearch", "SeasonSearch", "SeriesSearch", "MissingEpisodeSearch",
    "CutoffUnmetEpisodeSearch", "MoviesSearch", "MissingMoviesSearch",
    "CutoffUnmetMoviesSearch",
}


class ArrError(Exception):
    """Fout bij een Sonarr/Radarr-aanroep. ``code`` is stabiel voor UI/diagnose."""

    code = "ARR_ERROR"
    http_status = 502

    def __init__(self, message: str, *, status: int | None = None, method: str = "",
                 path: str = "", details: Any = None, instance_id: str | None = None) -> None:
        self.message = redact(message)
        self.status = status
        self.method = method
        self.path = path
        self.details = details
        self.instance_id = instance_id
        super().__init__(self.message)

    def to_dict(self) -> dict[str, Any]:
        return {"code": self.code, "message": self.message, "upstream_status": self.status,
                "instance_id": self.instance_id, "path": self.path, "details": self.details}


class ArrAuthError(ArrError):
    code = "ARR_AUTH"
    http_status = 502


class ArrNotFound(ArrError):
    code = "ARR_NOT_FOUND"
    http_status = 404


class ArrValidationError(ArrError):
    code = "ARR_VALIDATION"
    http_status = 422


class ArrConflict(ArrError):
    code = "ARR_CONFLICT"
    http_status = 409


class ArrUnavailable(ArrError):
    """Netwerkfout, timeout, 5xx of rate-limit na alle retries."""

    code = "ARR_UNAVAILABLE"
    http_status = 503


class ArrTimeout(ArrUnavailable):
    code = "ARR_TIMEOUT"
    http_status = 504


def _clean_params(params: dict[str, Any]) -> dict[str, Any]:
    out: dict[str, Any] = {}
    for key, value in params.items():
        if value is None:
            continue
        if isinstance(value, bool):
            out[key] = "true" if value else "false"
        elif isinstance(value, list | tuple | set):
            out[key] = [str(v).lower() if isinstance(v, bool) else v for v in value]
        else:
            out[key] = value
    return out


def _validation_details(body: Any) -> Any:
    """Sonarr/Radarr validatiefouten: [{propertyName, errorMessage, severity}, ...]."""
    if isinstance(body, list):
        return [
            {"property": e.get("propertyName"), "message": redact(str(e.get("errorMessage", ""))),
             "severity": e.get("severity", "error")}
            for e in body if isinstance(e, dict)
        ]
    if isinstance(body, dict):
        return {"message": redact(str(body.get("message") or body.get("description") or ""))}
    return None


def records_of(data: Any) -> list[dict[str, Any]]:
    """Normaliseert een gepagineerd antwoord ({page,pageSize,totalRecords,records}) of
    een gewone lijst naar een lijst records."""
    if isinstance(data, dict):
        recs = data.get("records")
        return list(recs) if isinstance(recs, list) else []
    if isinstance(data, list):
        return data
    return []


class ArrClient:
    """Eén client per instantie. Kan als ``async with`` gebruikt worden (verbinding
    hergebruiken) of los; dan wordt per verzoek een kortlevende verbinding gebruikt."""

    def __init__(self, base_url: str, api_key: str | None, *, instance_id: str, app: str,
                 timeout: float = 20.0, retries: int = 2, backoff: float = 0.5,
                 transport: httpx.AsyncBaseTransport | None = None,
                 shared: httpx.AsyncClient | None = None) -> None:
        if app not in ("sonarr", "radarr"):
            raise ValueError(f"onbekende app: {app}")
        self.base_url = base_url.rstrip("/")
        self.api_key = api_key or ""
        self.instance_id = instance_id
        self.app = app
        self.timeout = timeout
        self.retries = max(0, retries)
        self.backoff = backoff
        self.transport = transport
        # langlevende, gedeelde verbinding (pool per instantie): niet sluiten bij afloop
        self._shared = shared
        self._client: httpx.AsyncClient | None = None

    # -- verbinding -----------------------------------------------------------------
    def _make_client(self) -> httpx.AsyncClient:
        return httpx.AsyncClient(
            base_url=self.base_url,
            headers={"X-Api-Key": self.api_key, "Accept": "application/json",
                     "User-Agent": "MediaGuard-Core"},
            timeout=httpx.Timeout(self.timeout, connect=min(10.0, self.timeout)),
            transport=self.transport,
            follow_redirects=False,
        )

    async def __aenter__(self) -> ArrClient:
        self._client = self._shared if self._shared is not None else self._make_client()
        return self

    async def __aexit__(self, *exc: object) -> None:
        await self.aclose()

    async def aclose(self) -> None:
        if self._client is not None and self._client is not self._shared:
            await self._client.aclose()
        self._client = None

    @contextlib.asynccontextmanager
    async def _session(self) -> AsyncIterator[httpx.AsyncClient]:
        if self._client is not None:
            yield self._client
            return
        if self._shared is not None:
            yield self._shared
            return
        client = self._make_client()
        try:
            yield client
        finally:
            await client.aclose()

    @staticmethod
    def _path(path: str) -> str:
        if path.startswith("/api/") or path.startswith("/ping"):
            return path
        return f"{API_PREFIX}/{path.lstrip('/')}"

    # -- kern -----------------------------------------------------------------------
    async def request(self, method: str, path: str, *, params: dict[str, Any] | None = None,
                      json: Any = None) -> Any:
        method = method.upper()
        url = self._path(path)
        query = _clean_params(params or {})
        attempt = 0
        while True:
            attempt += 1
            try:
                async with self._session() as client:
                    response = await client.request(method, url, params=query,
                                                    json=json if json is not None else None)
            except httpx.TimeoutException as exc:
                # een POST kan bij een read-timeout al verwerkt zijn: niet herhalen
                retryable = method in IDEMPOTENT or isinstance(exc, httpx.ConnectTimeout)
                if retryable and attempt <= self.retries:
                    await self._sleep(attempt)
                    continue
                raise ArrTimeout(f"{self.app} ({self.instance_id}) reageert niet binnen "
                                 f"{self.timeout:.0f}s", method=method, path=url,
                                 instance_id=self.instance_id) from exc
            except httpx.TransportError as exc:
                retryable = method in IDEMPOTENT or isinstance(exc, httpx.ConnectError)
                if retryable and attempt <= self.retries:
                    await self._sleep(attempt)
                    continue
                raise ArrUnavailable(f"{self.app} ({self.instance_id}) niet bereikbaar: "
                                     f"{type(exc).__name__}: {exc}", method=method, path=url,
                                     instance_id=self.instance_id) from exc
            status = response.status_code
            if status in RETRY_STATUSES and method in IDEMPOTENT and attempt <= self.retries:
                await self._sleep(attempt, response.headers.get("Retry-After"))
                continue
            return self._handle(response, method, url)

    async def _sleep(self, attempt: int, retry_after: str | None = None) -> None:
        delay = self.backoff * (2 ** (attempt - 1))
        if retry_after:
            with contextlib.suppress(ValueError):
                delay = max(delay, min(30.0, float(retry_after)))
        if delay > 0:
            await asyncio.sleep(delay)

    def _handle(self, response: httpx.Response, method: str, url: str) -> Any:
        status = response.status_code
        body: Any = None
        text = ""
        if response.content:
            try:
                body = response.json()
            except ValueError:
                text = response.text[:500]
        if 200 <= status < 300:
            return body
        kw = {"status": status, "method": method, "path": url, "instance_id": self.instance_id}
        upstream_msg = ""
        if isinstance(body, dict):
            upstream_msg = str(body.get("message") or body.get("description") or "")
        elif isinstance(body, list) and body and isinstance(body[0], dict):
            upstream_msg = "; ".join(str(e.get("errorMessage", "")) for e in body[:5])
        upstream_msg = upstream_msg or text
        label = f"{self.app} ({self.instance_id}) {method} {url}"
        if status in (401, 403):
            raise ArrAuthError(f"{label}: API-sleutel geweigerd (HTTP {status})", **kw)
        if status == 404:
            raise ArrNotFound(f"{label}: niet gevonden {upstream_msg}".strip(), **kw)
        if status == 409:
            raise ArrConflict(f"{label}: conflict {upstream_msg}".strip(), **kw,
                              details=_validation_details(body))
        if status in (400, 422):
            raise ArrValidationError(f"{label}: ongeldig verzoek: {upstream_msg}".strip(), **kw,
                                     details=_validation_details(body))
        if status in RETRY_STATUSES:
            raise ArrUnavailable(f"{label}: HTTP {status} {upstream_msg}".strip(), **kw)
        raise ArrError(f"{label}: HTTP {status} {upstream_msg}".strip(), **kw)

    # -- contract-interface ---------------------------------------------------------
    async def get(self, path: str, **params: Any) -> Any:
        return await self.request("GET", path, params=params)

    async def post(self, path: str, json: Any = None, **params: Any) -> Any:
        return await self.request("POST", path, params=params, json=json)

    async def put(self, path: str, json: Any = None, **params: Any) -> Any:
        return await self.request("PUT", path, params=params, json=json)

    async def delete(self, path: str, **params: Any) -> Any:
        return await self.request("DELETE", path, params=params)

    async def get_paged(self, path: str, *, page_size: int = 250, max_pages: int = 200,
                        **params: Any) -> list[dict[str, Any]]:
        """Haalt alle pagina's op van een {page,pageSize,totalRecords,records}-endpoint."""
        out: list[dict[str, Any]] = []
        page = 1
        while page <= max_pages:
            data = await self.get(path, page=page, pageSize=page_size, **params)
            if isinstance(data, list):  # sommige versies geven een lijst
                return data
            recs = records_of(data)
            out.extend(recs)
            total = int((data or {}).get("totalRecords") or 0)
            if not recs or len(out) >= total:
                break
            page += 1
        return out

    # -- systeem --------------------------------------------------------------------
    async def system_status(self) -> dict[str, Any]:
        return await self.get("system/status") or {}

    async def health(self) -> list[dict[str, Any]]:
        return await self.get("health") or []

    async def backups(self) -> list[dict[str, Any]]:
        return await self.get("system/backup") or []

    async def create_backup(self) -> dict[str, Any]:
        return await self.command("Backup")

    # -- commando's -----------------------------------------------------------------
    async def command(self, name: str, **body: Any) -> dict[str, Any]:
        allowed = SONARR_COMMANDS if self.app == "sonarr" else RADARR_COMMANDS
        if name not in allowed:
            raise ValueError(f"commando {name} niet toegestaan voor {self.app}")
        return await self.post("command", {"name": name, **body}) or {}

    async def command_status(self, command_id: int) -> dict[str, Any]:
        return await self.get(f"command/{int(command_id)}") or {}

    async def commands(self) -> list[dict[str, Any]]:
        return await self.get("command") or []

    async def episode_search(self, episode_ids: Iterable[int]) -> dict[str, Any]:
        return await self.command("EpisodeSearch", episodeIds=[int(i) for i in episode_ids])

    async def season_search(self, series_id: int, season_number: int) -> dict[str, Any]:
        return await self.command("SeasonSearch", seriesId=int(series_id),
                                  seasonNumber=int(season_number))

    async def series_search(self, series_id: int) -> dict[str, Any]:
        return await self.command("SeriesSearch", seriesId=int(series_id))

    async def movies_search(self, movie_ids: Iterable[int]) -> dict[str, Any]:
        return await self.command("MoviesSearch", movieIds=[int(i) for i in movie_ids])

    async def rss_sync(self) -> dict[str, Any]:
        return await self.command("RssSync")

    async def refresh_series(self, series_id: int | None = None) -> dict[str, Any]:
        body = {"seriesId": int(series_id)} if series_id is not None else {}
        return await self.command("RefreshSeries", **body)

    async def rescan_series(self, series_id: int | None = None) -> dict[str, Any]:
        body = {"seriesId": int(series_id)} if series_id is not None else {}
        return await self.command("RescanSeries", **body)

    async def refresh_movie(self, movie_ids: Iterable[int] | None = None) -> dict[str, Any]:
        body = {"movieIds": [int(i) for i in movie_ids]} if movie_ids is not None else {}
        return await self.command("RefreshMovie", **body)

    async def downloaded_scan(self, path: str) -> dict[str, Any]:
        name = "DownloadedEpisodesScan" if self.app == "sonarr" else "DownloadedMoviesScan"
        return await self.command(name, path=path)

    # -- series (Sonarr) ------------------------------------------------------------
    def _require(self, app: str) -> None:
        if self.app != app:
            raise ValueError(f"alleen beschikbaar voor {app}-instanties")

    async def series_list(self) -> list[dict[str, Any]]:
        self._require("sonarr")
        return await self.get("series") or []

    async def series_get(self, series_id: int) -> dict[str, Any]:
        self._require("sonarr")
        return await self.get(f"series/{int(series_id)}") or {}

    async def series_lookup(self, term: str) -> list[dict[str, Any]]:
        self._require("sonarr")
        return await self.get("series/lookup", term=term) or []

    async def series_add(self, body: dict[str, Any]) -> dict[str, Any]:
        self._require("sonarr")
        return await self.post("series", body) or {}

    async def series_update(self, series: dict[str, Any], move_files: bool = False) -> dict:
        self._require("sonarr")
        return await self.put(f"series/{int(series['id'])}", series, moveFiles=move_files) or {}

    async def series_delete(self, series_id: int, *, delete_files: bool = False,
                            add_import_exclusion: bool = False) -> None:
        self._require("sonarr")
        await self.delete(f"series/{int(series_id)}", deleteFiles=bool(delete_files),
                          addImportListExclusion=add_import_exclusion)

    async def season_pass(self, series_id: int, seasons: dict[int, bool],
                          monitor_new_items: str | None = None) -> None:
        """Seizoensmonitoring zoals de Sonarr-UI: ``PUT /series/{id}`` met gewijzigde
        seizoensvlaggen. Sonarr zet dan de afleveringen van ALLEEN de gewijzigde seizoenen
        mee (aan/uit); ongewijzigde seizoenen houden hun aflevering-uitzonderingen.
        (De ``/seasonpass``-route wijzigt alleen de seizoensvlag en laat afleveringen
        ongemonitord — gemeten tegen Sonarr 4.0.20; dan zou er nooit iets gedownload worden.)"""
        self._require("sonarr")
        series = await self.series_get(series_id)
        changed = False
        for season in series.get("seasons", []):
            number = int(season.get("seasonNumber", 0))
            if number in seasons and bool(season.get("monitored")) != bool(seasons[number]):
                season["monitored"] = bool(seasons[number])
                changed = True
        if monitor_new_items is not None and series.get("monitorNewItems") != monitor_new_items:
            series["monitorNewItems"] = monitor_new_items
            changed = True
        if changed:
            await self.series_update(series)

    async def episodes(self, series_id: int, season_number: int | None = None,
                       include_files: bool = False) -> list[dict[str, Any]]:
        self._require("sonarr")
        return await self.get("episode", seriesId=int(series_id), seasonNumber=season_number,
                              includeEpisodeFile=include_files or None) or []

    async def episode_get(self, episode_id: int) -> dict[str, Any]:
        self._require("sonarr")
        return await self.get(f"episode/{int(episode_id)}") or {}

    async def episodes_monitor(self, episode_ids: Iterable[int], monitored: bool) -> Any:
        self._require("sonarr")
        return await self.put("episode/monitor",
                              {"episodeIds": [int(i) for i in episode_ids],
                               "monitored": bool(monitored)})

    async def episode_files(self, series_id: int) -> list[dict[str, Any]]:
        self._require("sonarr")
        return await self.get("episodefile", seriesId=int(series_id)) or []

    # -- films (Radarr) -------------------------------------------------------------
    async def movie_list(self) -> list[dict[str, Any]]:
        self._require("radarr")
        return await self.get("movie") or []

    async def movie_get(self, movie_id: int) -> dict[str, Any]:
        self._require("radarr")
        return await self.get(f"movie/{int(movie_id)}") or {}

    async def movie_lookup(self, term: str) -> list[dict[str, Any]]:
        self._require("radarr")
        if term.startswith("tmdb:") and term[5:].isdigit():
            found = await self.get("movie/lookup/tmdb", tmdbId=int(term[5:]))
            return [found] if isinstance(found, dict) and found else list(found or [])
        return await self.get("movie/lookup", term=term) or []

    async def movie_add(self, body: dict[str, Any]) -> dict[str, Any]:
        self._require("radarr")
        return await self.post("movie", body) or {}

    async def movie_update(self, movie: dict[str, Any], move_files: bool = False) -> dict:
        self._require("radarr")
        return await self.put(f"movie/{int(movie['id'])}", movie, moveFiles=move_files) or {}

    async def movie_delete(self, movie_id: int, *, delete_files: bool = False,
                           add_import_exclusion: bool = False) -> None:
        self._require("radarr")
        await self.delete(f"movie/{int(movie_id)}", deleteFiles=bool(delete_files),
                          addImportExclusion=add_import_exclusion)

    async def movie_files(self, movie_id: int) -> list[dict[str, Any]]:
        self._require("radarr")
        return await self.get("moviefile", movieId=int(movie_id)) or []

    # -- wanted / kalender / wachtrij / historie ------------------------------------
    async def wanted_missing(self, *, page: int = 1, page_size: int = 50,
                             monitored: bool = True, all_pages: bool = False) -> Any:
        params: dict[str, Any] = {"monitored": monitored, "sortKey": self._air_sort_key(),
                                  "sortDirection": "descending"}
        if self.app == "sonarr":
            params["includeSeries"] = True
        if all_pages:
            return await self.get_paged("wanted/missing", page_size=page_size, **params)
        return await self.get("wanted/missing", page=page, pageSize=page_size, **params)

    async def wanted_cutoff(self, *, page: int = 1, page_size: int = 50,
                            monitored: bool = True, all_pages: bool = False) -> Any:
        params: dict[str, Any] = {"monitored": monitored, "sortKey": self._air_sort_key(),
                                  "sortDirection": "descending"}
        if self.app == "sonarr":
            params["includeSeries"] = True
            params["includeEpisodeFile"] = True
        if all_pages:
            return await self.get_paged("wanted/cutoff", page_size=page_size, **params)
        return await self.get("wanted/cutoff", page=page, pageSize=page_size, **params)

    def _air_sort_key(self) -> str:
        return "episodes.airDateUtc" if self.app == "sonarr" else "movieMetadata.sortTitle"

    async def calendar(self, start: str, end: str, unmonitored: bool = False) -> list[dict]:
        params: dict[str, Any] = {"start": start, "end": end, "unmonitored": unmonitored}
        if self.app == "sonarr":
            params["includeSeries"] = True
        return await self.get("calendar", **params) or []

    async def queue(self, *, page: int = 1, page_size: int = 100,
                    all_pages: bool = False) -> Any:
        params: dict[str, Any] = {"includeUnknownSeriesItems": True} if self.app == "sonarr" \
            else {"includeUnknownMovieItems": True}
        if self.app == "sonarr":
            params.update(includeSeries=True, includeEpisode=True)
        else:
            params.update(includeMovie=True)
        if all_pages:
            return await self.get_paged("queue", page_size=page_size, **params)
        return await self.get("queue", page=page, pageSize=page_size, **params)

    async def queue_remove(self, queue_id: int, *, remove_from_client: bool = True,
                           blocklist: bool = False) -> None:
        await self.delete(f"queue/{int(queue_id)}", removeFromClient=remove_from_client,
                          blocklist=blocklist)

    async def history(self, *, page: int = 1, page_size: int = 50,
                      episode_id: int | None = None, series_id: int | None = None,
                      movie_id: int | None = None, event_type: str | int | None = None) -> Any:
        params: dict[str, Any] = {"page": page, "pageSize": page_size, "sortKey": "date",
                                  "sortDirection": "descending", "eventType": event_type}
        if self.app == "sonarr":
            params.update(episodeId=episode_id, seriesId=series_id, includeSeries=False,
                          includeEpisode=False)
        else:
            params.update(movieIds=[movie_id] if movie_id is not None else None,
                          includeMovie=False)
        return await self.get("history", **params)

    async def history_for_episode(self, episode_id: int, limit: int = 50) -> list[dict]:
        data = await self.history(episode_id=episode_id, page_size=limit)
        return [r for r in records_of(data) if int(r.get("episodeId") or 0) == int(episode_id)]

    async def history_for_movie(self, movie_id: int) -> list[dict[str, Any]]:
        data = await self.get("history/movie", movieId=int(movie_id))
        return records_of(data) if not isinstance(data, list) else data

    async def history_since(self, date_iso: str) -> list[dict[str, Any]]:
        path = "history/since"
        params: dict[str, Any] = {"date": date_iso}
        if self.app == "sonarr":
            params["includeEpisode"] = False
        return await self.get(path, **params) or []

    # -- releases (interactief zoeken) ----------------------------------------------
    async def releases(self, *, episode_id: int | None = None, series_id: int | None = None,
                       season_number: int | None = None,
                       movie_id: int | None = None) -> list[dict[str, Any]]:
        """Interactieve zoekopdracht. Let op: dit bevraagt de indexers (telt mee in quota)."""
        if self.app == "sonarr":
            return await self.get("release", episodeId=episode_id, seriesId=series_id,
                                  seasonNumber=season_number) or []
        return await self.get("release", movieId=movie_id) or []

    async def release_grab(self, guid: str, indexer_id: int) -> Any:
        return await self.post("release", {"guid": guid, "indexerId": int(indexer_id)})

    # -- providers (indexer, downloadclient, notification, ...) ---------------------
    async def provider_list(self, kind: str) -> list[dict[str, Any]]:
        return await self.get(kind) or []

    async def provider_get(self, kind: str, item_id: int) -> dict[str, Any]:
        return await self.get(f"{kind}/{int(item_id)}") or {}

    async def provider_schema(self, kind: str) -> list[dict[str, Any]]:
        return await self.get(f"{kind}/schema") or []

    async def provider_create(self, kind: str, body: dict[str, Any],
                              force_save: bool = False) -> dict[str, Any]:
        return await self.post(kind, body, forceSave=force_save or None) or {}

    async def provider_update(self, kind: str, body: dict[str, Any],
                              force_save: bool = False) -> dict[str, Any]:
        return await self.put(f"{kind}/{int(body['id'])}", body,
                              forceSave=force_save or None) or {}

    async def provider_delete(self, kind: str, item_id: int) -> None:
        await self.delete(f"{kind}/{int(item_id)}")

    async def provider_test(self, kind: str, body: dict[str, Any]) -> Any:
        return await self.post(f"{kind}/test", body)

    async def indexers(self) -> list[dict[str, Any]]:
        return await self.provider_list("indexer")

    async def indexer_status(self) -> list[dict[str, Any]]:
        return await self.get("indexerstatus") or []

    async def download_clients(self) -> list[dict[str, Any]]:
        return await self.provider_list("downloadclient")

    async def download_client_status(self) -> list[dict[str, Any]]:
        try:
            return await self.get("downloadclientstatus") or []
        except ArrNotFound:
            return []

    async def notifications(self) -> list[dict[str, Any]]:
        return await self.provider_list("notification")

    # -- configuratiebronnen --------------------------------------------------------
    async def root_folders(self) -> list[dict[str, Any]]:
        return await self.get("rootfolder") or []

    async def root_folder_add(self, path: str) -> dict[str, Any]:
        return await self.post("rootfolder", {"path": path}) or {}

    async def root_folder_delete(self, folder_id: int) -> None:
        await self.delete(f"rootfolder/{int(folder_id)}")

    async def quality_profiles(self) -> list[dict[str, Any]]:
        return await self.get("qualityprofile") or []

    async def language_profiles(self) -> list[dict[str, Any]]:
        """Sonarr v3 heeft taalprofielen; Sonarr v4/Radarr niet (dan lege lijst)."""
        if self.app != "sonarr":
            return []
        try:
            data = await self.get("languageprofile")
        except (ArrNotFound, ArrError) as exc:
            if isinstance(exc, ArrNotFound) or exc.status in (404, 405, 410):
                return []
            raise
        return data or []

    async def custom_formats(self) -> list[dict[str, Any]]:
        return await self.get("customformat") or []

    async def tags(self) -> list[dict[str, Any]]:
        return await self.get("tag") or []

    async def tag_create(self, label: str) -> dict[str, Any]:
        return await self.post("tag", {"label": label}) or {}

    async def tag_update(self, tag_id: int, label: str) -> dict[str, Any]:
        return await self.put(f"tag/{int(tag_id)}", {"id": int(tag_id), "label": label}) or {}

    async def tag_delete(self, tag_id: int) -> None:
        await self.delete(f"tag/{int(tag_id)}")

    async def remote_path_mappings(self) -> list[dict[str, Any]]:
        return await self.get("remotepathmapping") or []

    async def remote_path_mapping_create(self, host: str, remote_path: str,
                                         local_path: str) -> dict[str, Any]:
        return await self.post("remotepathmapping", {"host": host, "remotePath": remote_path,
                                                     "localPath": local_path}) or {}

    async def remote_path_mapping_update(self, body: dict[str, Any]) -> dict[str, Any]:
        return await self.put(f"remotepathmapping/{int(body['id'])}", body) or {}

    async def remote_path_mapping_delete(self, mapping_id: int) -> None:
        await self.delete(f"remotepathmapping/{int(mapping_id)}")

    async def manual_import_candidates(self, folder: str, *, series_id: int | None = None,
                                       movie_id: int | None = None) -> list[dict[str, Any]]:
        """Bestanden in de map die nog niet aan een aflevering/film gekoppeld zijn."""
        params: dict[str, Any] = {"folder": folder, "filterExistingFiles": True}
        if self.app == "sonarr":
            params["seriesId"] = series_id
        else:
            params["movieId"] = movie_id
        return await self.get("manualimport", **params) or []
