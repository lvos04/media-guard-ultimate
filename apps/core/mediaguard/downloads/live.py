"""Wat er nú gedownload wordt, rechtstreeks uit SABnzbd en qBittorrent (rapport 7, A2).

Activiteit → Downloads las alleen de wachtrijen van de MediaGuard-instanties. Grabs van de
oude Sonarr/Radarr (tijdens de migratie nog leidend) of downloads die meteen in de client
staan, bleven zo onzichtbaar ("0 · Er wordt nu niets gedownload"). Hier lezen we de
downloadclients zelf (alleen-lezen) en de wachtrijen van de oude installaties zolang de
migratie loopt. Een client die niet antwoordt geeft een fout, nooit stilzwijgend '0'.
"""

from __future__ import annotations

import asyncio
import logging
from typing import TYPE_CHECKING, Any

import httpx

if TYPE_CHECKING:
    from mediaguard.context import AppContext

LOGGER = logging.getLogger(__name__)
TIMEOUT = 8.0

# qBittorrent-toestanden waarin nog (echt) gedownload wordt of de download wacht
QBIT_ACTIVE = {"downloading", "forcedDL", "metaDL", "forcedMetaDL", "stalledDL", "queuedDL",
               "pausedDL", "stoppedDL", "checkingDL", "allocating"}
QBIT_DOWNLOADING = {"downloading", "forcedDL", "metaDL", "forcedMetaDL"}
SAB_DOWNLOADING = {"Downloading", "Fetching", "Grabbing"}
STATUS_NL = {"downloading": "Downloaden", "queued": "In wachtrij", "paused": "Gepauzeerd",
             "stalled": "Geen bronnen", "processing": "Uitpakken/controleren"}


def _base(dc: Any) -> str:
    scheme = "https" if dc.use_ssl else "http"
    sub = str(dc.url_base or "").strip("/")
    return f"{scheme}://{dc.host}:{dc.port}" + (f"/{sub}" if sub else "")


def _transport(ctx: AppContext) -> Any:
    return ctx.services.get("live_download_transport") or \
        ctx.services.get("failure_lookup_transport")


def _float(v: Any) -> float:
    try:
        return float(v)
    except (TypeError, ValueError):
        return 0.0


def _sab_status(status: str) -> str:
    if status in SAB_DOWNLOADING:
        return "downloading"
    if status == "Paused":
        return "paused"
    if status in ("Queued", "Propagating", "Checking"):
        return "queued"
    return "processing"


async def _sab(ctx: AppContext, dc: Any) -> list[dict[str, Any]]:
    from mediaguard.provisioning.store import dc_secret

    key = ctx.secrets.get(dc_secret(dc.name, "api_key"))
    if not key:
        raise ValueError("geen API-sleutel ingesteld")
    params: dict[str, Any] = {"mode": "queue", "output": "json", "apikey": key, "limit": 500}
    async with httpx.AsyncClient(timeout=TIMEOUT, transport=_transport(ctx)) as client:
        resp = await client.get(f"{_base(dc)}/api", params=params)
        resp.raise_for_status()
        data = resp.json() or {}
    if isinstance(data, dict) and data.get("error"):
        raise ValueError(str(data["error"]))
    queue = data.get("queue") or {}
    paused_all = bool(queue.get("paused"))
    out = []
    for s in queue.get("slots") or []:
        mb, left = _float(s.get("mb")), _float(s.get("mbleft"))
        status = "paused" if paused_all else _sab_status(str(s.get("status") or ""))
        out.append({
            "download_id": str(s.get("nzo_id") or "").upper(), "client": dc.name,
            "protocol": "usenet", "release": str(s.get("filename") or ""),
            "category": str(s.get("cat") or ""), "status": status,
            "status_nl": STATUS_NL.get(status, status),
            "progress": round(_float(s.get("percentage")), 1) if mb else None,
            "size": int(mb * 1024**2), "size_left": int(left * 1024**2),
            "time_left": s.get("timeleft") or None})
    return out


async def _qbit(ctx: AppContext, dc: Any) -> list[dict[str, Any]]:
    from mediaguard.provisioning.store import dc_secret

    password = ctx.secrets.get(dc_secret(dc.name, "password")) or ""
    async with httpx.AsyncClient(base_url=_base(dc), timeout=TIMEOUT,
                                 transport=_transport(ctx)) as client:
        if dc.username or password:
            login = await client.post("/api/v2/auth/login",
                                      data={"username": dc.username, "password": password})
            login.raise_for_status()
            if login.text.strip().lower().startswith("fails"):
                raise ValueError("inloggen geweigerd")
        resp = await client.get("/api/v2/torrents/info")
        resp.raise_for_status()
        rows = resp.json() or []
    out = []
    for t in rows:
        state = str(t.get("state") or "")
        if state not in QBIT_ACTIVE:
            continue
        status = "downloading" if state in QBIT_DOWNLOADING else \
            "stalled" if state == "stalledDL" else \
            "paused" if state in ("pausedDL", "stoppedDL") else "queued"
        size, left = int(_float(t.get("size"))), int(_float(t.get("amount_left")))
        eta = int(_float(t.get("eta")))
        out.append({
            "download_id": str(t.get("hash") or "").upper(), "client": dc.name,
            "protocol": "torrent", "release": str(t.get("name") or ""),
            "category": str(t.get("category") or ""), "status": status,
            "status_nl": STATUS_NL.get(status, status),
            "progress": round(_float(t.get("progress")) * 100, 1),
            "size": size, "size_left": left,
            "time_left": (f"{eta // 3600}:{eta % 3600 // 60:02d}:{eta % 60:02d}"
                          if 0 < eta < 8640000 else None)})
    return out


async def client_downloads(ctx: AppContext) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    """(lopende downloads per client, fouten per client). Alleen-lezen."""
    from mediaguard.provisioning.store import load

    try:
        clients = [dc for dc in load(ctx).download_clients if dc.enabled]
    except Exception:  # noqa: BLE001 - geen inrichting = niets te lezen
        return [], []

    async def one(dc: Any) -> tuple[list[dict[str, Any]], dict[str, Any] | None]:
        try:
            if dc.implementation == "Sabnzbd":
                return await _sab(ctx, dc), None
            if dc.implementation == "QBittorrent":
                return await _qbit(ctx, dc), None
            return [], None
        except (httpx.HTTPError, ValueError, KeyError) as exc:
            LOGGER.info("downloadclient %s niet bereikbaar: %s", dc.name, exc)
            return [], {"client": dc.name,
                        "error": f"Downloadclient niet bereikbaar: {type(exc).__name__}: {exc}"}

    results = await asyncio.gather(*(one(dc) for dc in clients))
    items = [i for r in results for i in r[0]]
    errors = [r[1] for r in results if r[1]]
    return items, errors


def _migration_active(ctx: AppContext) -> bool:
    row = ctx.db.fetch_one("SELECT COUNT(*) AS n FROM migration_runs WHERE status='active'")
    return bool(row and int(row["n"]))


async def source_queues(ctx: AppContext) -> tuple[list[tuple[Any, list[dict[str, Any]]]],
                                                  list[dict[str, Any]]]:
    """Wachtrijen van de OUDE Sonarr/Radarr zolang de migratie loopt (alleen-lezen)."""
    from mediaguard.migration.sources import list_sources, source_client

    if not _migration_active(ctx):
        return [], []
    out: list[tuple[Any, list[dict[str, Any]]]] = []
    errors: list[dict[str, Any]] = []
    for row in list_sources(ctx):
        if row["kind"] != "live" or not row.get("api_key_set"):
            continue
        try:
            async with source_client(ctx, row["id"], timeout=TIMEOUT) as client:
                extra = ({"includeSeries": "true", "includeEpisode": "true"}
                         if row["app"] == "sonarr" else {"includeMovie": "true"})
                queue = await client.get("queue", page=1, pageSize=100, **extra)
        except Exception as exc:  # noqa: BLE001 - per bron isoleren
            errors.append({"instance_id": f"source:{row['id']}",
                           "name": f"{row['name']} (oude installatie)",
                           "error": f"{type(exc).__name__}: {exc}"})
            continue
        records = (queue.get("records") if isinstance(queue, dict) else queue) or []
        out.append((row, records))
    return out, errors


def merge(arr_items: list[dict[str, Any]], client_items: list[dict[str, Any]]
          ) -> list[dict[str, Any]]:
    """Voeg clientdownloads toe die geen enkele Sonarr/Radarr-wachtrij kent, en vul bij
    gekoppelde items voortgang/status uit de client aan (die is actueler)."""
    by_id = {str(d.get("download_id") or "").upper(): d for d in arr_items
             if d.get("download_id")}
    out = list(arr_items)
    for c in client_items:
        hit = by_id.get(c["download_id"])
        if hit is not None:
            if hit.get("progress") is None:
                hit["progress"] = c["progress"]
            if c["status"] == "downloading" and hit.get("status") in ("queued", "", None):
                hit["status"] = "downloading"
            hit.setdefault("time_left", c.get("time_left"))
            hit["client"] = hit.get("client") or c["client"]
            continue
        out.append({
            "id": None, "instance_id": None, "instance_name": None,
            "title": c["release"], "release": c["release"], "protocol": c["protocol"],
            "client": c["client"], "status": c["status"], "state": c["status"],
            "status_nl": c["status_nl"], "progress": c["progress"], "size": c["size"],
            "size_left": c["size_left"], "eta": None, "time_left": c["time_left"],
            "quality": None, "warning": False, "messages": [], "messages_nl": [],
            "link": None, "awaiting_import": False, "download_id": c["download_id"],
            "movie_id": None, "series_id": None, "episode_id": None, "can_import": False,
            "category": c["category"], "only_in_client": True})
    return out
