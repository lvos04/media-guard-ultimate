"""Optionele Jellyfin-, Radarr- en Sonarr-koppelingen met smalle opdrachten."""

from __future__ import annotations

import asyncio
import json
import logging
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import httpx

from app.config import Settings
from app.db import Database
from app.geheimen import SecretError, SecretStore

LOGGER = logging.getLogger(__name__)


def mediaserver_headers(product: str, api_key: str) -> dict[str, str]:
    """Aanmeldkop per soort server (Δ-MG-AUTH, besluit BG-01, contract 1.3 §2.3 regel 2).

    Emby houdt ``X-Emby-Token``. Jellyfin (Jelly Leon 12.x, stock 12.x) krijgt alleen
    ``Authorization: MediaBrowser … Token="…"``: 12.x weigert ``X-Emby-Token``, ``api_key=`` en
    ``X-Emby-Authorization`` zolang ``EnableLegacyAuthorization`` uit staat. Nooit in de URL.

    De Jellyfin-kop gaat alleen naar een server die zich als Jellyfin meldt. Elke andere
    server houdt de kop van 1.2.2: Emby 4.10.2.0 geeft op de openbare info-route geen
    ``ProductName`` (gemeten in ``tests/contract/mediaserver``), en een bestaande
    Emby-koppeling mag door deze wijziging nooit een andere kop krijgen (ADR-0022).
    """
    if any(c in api_key for c in '"\\,') or any(c.isspace() for c in api_key):
        raise ValueError("ongeldige API-sleutel (onverwachte tekens)")
    if "jellyfin" in product.lower():
        return {
            "Authorization": (
                'MediaBrowser Client="MediaGuard AV1", Device="MediaGuard", '
                f'DeviceId="mediaguard-encode", Version="1", Token="{api_key}"'
            )
        }
    return {"X-Emby-Token": api_key}


async def mediaserver_product(client: httpx.AsyncClient, url: str) -> str:
    """``ProductName`` van de openbare info-route (zonder sleutel): "Jellyfin Server" bij
    Jellyfin 12.x. Leeg bij Emby 4.10.2.0 (dat veld ontbreekt daar) en als de route niet te
    lezen is; dan geldt de Emby-kop van 1.2.2."""
    try:
        response = await client.get(url.rstrip("/") + "/System/Info/Public")
        if response.status_code >= 400:
            return ""
        value = response.json()
    except (httpx.HTTPError, ValueError):
        return ""
    if not isinstance(value, dict):
        return ""
    # hoofdletterongevoelig: Jellyfin 12.x antwoordt tijdens het opstarten in camelCase
    lower = {str(key).lower(): item for key, item in value.items()}
    return str(lower.get("productname") or "")


async def jellyfin_is_playing(client: httpx.AsyncClient, url: str, api_key: str) -> bool:
    product = await mediaserver_product(client, url)
    response = await client.get(
        url.rstrip("/") + "/Sessions",
        headers=mediaserver_headers(product, api_key),
    )
    response.raise_for_status()
    value = response.json()
    if not isinstance(value, list):
        return False
    return any(
        isinstance(session, dict)
        and bool(session.get("NowPlayingItem"))
        and not bool((session.get("PlayState") or {}).get("IsPaused"))
        for session in value
    )


async def refresh_radarr(
    client: httpx.AsyncClient, url: str, api_key: str, media_path: Path
) -> int | None:
    headers = {"X-Api-Key": api_key}
    response = await client.get(url.rstrip("/") + "/api/v3/movie", headers=headers)
    response.raise_for_status()
    movies = response.json()
    if not isinstance(movies, list):
        return None
    target = media_path.parent.resolve()
    movie = next(
        (
            item
            for item in movies
            if isinstance(item, dict)
            and item.get("path")
            and Path(str(item["path"])).resolve() == target
        ),
        None,
    )
    if movie is None or not isinstance(movie.get("id"), int):
        return None
    movie_id = int(movie["id"])
    command = await client.post(
        url.rstrip("/") + "/api/v3/command",
        headers=headers,
        json={"name": "RefreshMovie", "movieIds": [movie_id]},
    )
    command.raise_for_status()
    return movie_id


async def refresh_sonarr(
    client: httpx.AsyncClient, url: str, api_key: str, media_path: Path
) -> int | None:
    headers = {"X-Api-Key": api_key}
    response = await client.get(url.rstrip("/") + "/api/v3/series", headers=headers)
    response.raise_for_status()
    series = response.json()
    if not isinstance(series, list):
        return None
    resolved = media_path.resolve()
    match = next(
        (
            item
            for item in series
            if isinstance(item, dict)
            and item.get("path")
            and Path(str(item["path"])).resolve() in resolved.parents
        ),
        None,
    )
    if match is None or not isinstance(match.get("id"), int):
        return None
    series_id = int(match["id"])
    command = await client.post(
        url.rstrip("/") + "/api/v3/command",
        headers=headers,
        json={"name": "RescanSeries", "seriesId": series_id},
    )
    command.raise_for_status()
    return series_id


def _audit(database: Database, kind: str, job_id: int, value: dict[str, Any]) -> None:
    database.execute(
        "INSERT INTO auditlog(soort,onderwerp,nieuw,tijd) VALUES (?,?,?,?)",
        (
            "integratie",
            f"{kind}:job:{job_id}",
            json.dumps(value, ensure_ascii=False),
            datetime.now(UTC).isoformat(),
        ),
    )


async def refresh_after_replacement(
    database: Database,
    settings: Settings,
    media_path: Path,
    job_id: int,
    *,
    transport: httpx.AsyncBaseTransport | None = None,
) -> None:
    """Externe fouten worden alleen waarschuwingen; lokaal succes blijft succes."""

    secrets = SecretStore(database, settings)
    setting = database.fetch_one("SELECT radarr_url,sonarr_url FROM instelling WHERE id=1")
    if setting is None:
        return
    async with httpx.AsyncClient(timeout=15, transport=transport) as client:
        for kind, refresher in (("radarr", refresh_radarr), ("sonarr", refresh_sonarr)):
            enabled = database.fetch_one(
                "SELECT ingeschakeld FROM functie WHERE sleutel=?", (kind,)
            )
            url = str(setting[f"{kind}_url"] or "")
            try:
                api_key = secrets.get(f"{kind}_api_sleutel")
                if enabled is None or not int(enabled["ingeschakeld"]) or not url or not api_key:
                    continue
                item_id = await refresher(client, url, api_key, media_path)
                _audit(database, kind, job_id, {"status": "verstuurd", "item_id": item_id})
            except (httpx.HTTPError, SecretError, OSError, ValueError) as exc:
                LOGGER.warning("%s-waarschuwing voor job %s: %s", kind, job_id, exc)
                _audit(database, kind, job_id, {"status": "waarschuwing", "fout": str(exc)})


class IntegrationMonitor:
    """Pauzeer alleen de CPU-baan bij een echte, opt-in Jellyfin-sessie."""

    def __init__(self, database: Database, settings: Settings) -> None:
        self.database = database
        self.settings = settings
        self.stopping = False

    async def tick(self, transport: httpx.AsyncBaseTransport | None = None) -> bool:
        feature = self.database.fetch_one(
            "SELECT ingeschakeld FROM functie WHERE sleutel='jellyfin_pauze'"
        )
        setting = self.database.fetch_one(
            "SELECT jellyfin_url,pauzeer_cpu_bij_kijken FROM instelling WHERE id=1"
        )
        if (
            feature is None
            or not int(feature["ingeschakeld"])
            or setting is None
            or not int(setting["pauzeer_cpu_bij_kijken"])
            or not setting["jellyfin_url"]
        ):
            self._release_automatic_pause()
            return False
        try:
            api_key = SecretStore(self.database, self.settings).get("jellyfin_api_sleutel")
            if not api_key:
                self._release_automatic_pause()
                return False
            async with httpx.AsyncClient(timeout=10, transport=transport) as client:
                playing = await jellyfin_is_playing(client, str(setting["jellyfin_url"]), api_key)
            if playing:
                row = self.database.fetch_one(
                    "SELECT gepauzeerd,pauze_reden FROM worker_config WHERE baan='cpu'"
                )
                if row is not None and (
                    not int(row["gepauzeerd"])
                    or str(row["pauze_reden"] or "").startswith("Jellyfin:")
                ):
                    self.database.execute(
                        "UPDATE worker_config SET gepauzeerd=1,pauze_reden=? WHERE baan='cpu'",
                        ("Jellyfin: er wordt momenteel gekeken",),
                    )
                return True
            self._release_automatic_pause()
            return False
        except (httpx.HTTPError, SecretError, OSError, ValueError) as exc:
            LOGGER.warning("Jellyfin-monitor kon sessies niet lezen: %s", exc)
            return False

    def _release_automatic_pause(self) -> None:
        self.database.execute(
            """
            UPDATE worker_config SET gepauzeerd=0,pauze_reden=NULL
            WHERE baan='cpu' AND pauze_reden LIKE 'Jellyfin:%'
            """
        )

    async def run_forever(self) -> None:
        while not self.stopping:
            try:
                await self.tick()
            except Exception:
                # Eén onverwachte fout (bijv. SQLite-lock) mag de bewaking niet
                # permanent stoppen; anders blijft een Jellyfin-pauze eeuwig staan.
                LOGGER.exception("Integratiebewaking-tick mislukt; volgende poging over 30 s")
            await asyncio.sleep(30)
