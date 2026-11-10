"""Alleen-lezen weergave van SABnzbd en qBittorrent voor de opruimer.

MediaGuard praat hier rechtstreeks met de downloadclients (de gegevens staan al in de
inrichting). Gelezen wordt: wachtrij, historie, mappen per categorie en deellimieten.
Geschreven wordt alleen wat de opruimer expliciet vraagt: een historie-item in SABnzbd
verwijderen (zonder bestanden) of een torrent uit qBittorrent halen (zonder bestanden; de
bestanden verhuist de opruimer zelf naar de prullenbak).
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from datetime import UTC, datetime
from pathlib import PurePosixPath
from typing import TYPE_CHECKING, Any

import httpx

if TYPE_CHECKING:
    from mediaguard.context import AppContext
    from mediaguard.provisioning.store import DownloadClientCfg

LOGGER = logging.getLogger(__name__)

# qBittorrent-toestanden waarin nog gedownload, gecontroleerd of verplaatst wordt
TORRENT_BUSY = {"downloading", "metaDL", "forcedMetaDL", "stalledDL", "queuedDL", "forcedDL",
                "pausedDL", "stoppedDL", "checkingDL", "checkingUP", "checkingResumeData",
                "allocating", "moving", "unknown"}
TORRENT_LIMIT_REACHED = {"pausedUP", "stoppedUP"}
# SABnzbd-wachtrij: alles in de wachtrij is per definitie nog niet klaar
SAB_HISTORY_BUSY = {"Queued", "Fetching", "Extracting", "Verifying", "Repairing", "Moving",
                    "Running", "Downloading", "Paused", "Grabbing", "Propagating", "Checking"}


@dataclass
class ClientItem:
    """Eén download zoals een client hem kent."""

    client: str
    kind: str  # "nzb" | "torrent"
    id: str  # nzo_id of torrent-hash, in hoofdletters (zo vergelijkt Sonarr/Radarr ook)
    name: str
    category: str
    path: str | None
    active: bool
    failed: bool = False
    completed_at: datetime | None = None
    seeding_done: bool = True
    seeding_reason: str = ""
    private: bool = False


@dataclass
class Root:
    """Een downloadmap waarin de opruimer mag kijken (per client en categorie)."""

    path: str
    client: str
    kind: str  # "complete" | "incomplete" | "torrent"
    category: str = ""


@dataclass
class ClientView:
    name: str
    kind: str
    items: list[ClientItem] = field(default_factory=list)
    roots: list[Root] = field(default_factory=list)


def _join(base: str, sub: str) -> str:
    if not sub:
        return base
    return sub if sub.startswith("/") else str(PurePosixPath(base) / sub)


def _ts(value: Any) -> datetime | None:
    try:
        v = float(value)
    except (TypeError, ValueError):
        return None
    return datetime.fromtimestamp(v, UTC) if v > 0 else None


def _base_url(dc: DownloadClientCfg) -> str:
    scheme = "https" if dc.use_ssl else "http"
    base = (dc.url_base or "").strip("/")
    return f"{scheme}://{dc.host}:{dc.port}" + (f"/{base}" if base else "")


def _transport(ctx: AppContext, name: str) -> Any:
    return (ctx.services.get("download_client_transports") or {}).get(name)


# -- SABnzbd -------------------------------------------------------------------------------
class SabClient:
    def __init__(self, ctx: AppContext, dc: DownloadClientCfg, api_key: str | None) -> None:
        self.name, self.api_key = dc.name, api_key or ""
        # zonder URL-basis luistert SABnzbd standaard op /sabnzbd/api
        self.base = _base_url(dc).rstrip("/") + ("" if dc.url_base.strip("/") else "/sabnzbd")
        self._transport = _transport(ctx, dc.name)

    async def _api(self, **params: Any) -> Any:
        async with httpx.AsyncClient(timeout=20, transport=self._transport) as c:
            r = await c.get(f"{self.base}/api", params={"output": "json",
                                                        "apikey": self.api_key, **params})
            r.raise_for_status()
            return r.json()

    async def view(self, managed: set[str]) -> ClientView:
        cfg = (await self._api(mode="get_config")).get("config") or {}
        misc = cfg.get("misc") or {}
        complete = str(misc.get("complete_dir") or "")
        incomplete = str(misc.get("download_dir") or "")
        out = ClientView(self.name, "nzb")
        for cat in cfg.get("categories") or []:
            name = str(cat.get("name") or "")
            if name in managed and complete:
                out.roots.append(Root(_join(complete, str(cat.get("dir") or name)), self.name,
                                      "complete", name))
        if incomplete:
            out.roots.append(Root(incomplete, self.name, "incomplete"))
        queue = (await self._api(mode="queue", limit=1000)).get("queue") or {}
        for s in queue.get("slots") or []:
            out.items.append(ClientItem(self.name, "nzb", str(s.get("nzo_id", "")).upper(),
                                        str(s.get("filename") or ""), str(s.get("cat") or ""),
                                        None, active=True))
        hist = (await self._api(mode="history", limit=1000)).get("history") or {}
        for s in hist.get("slots") or []:
            status = str(s.get("status") or "")
            out.items.append(ClientItem(
                self.name, "nzb", str(s.get("nzo_id", "")).upper(), str(s.get("name") or ""),
                str(s.get("category") or ""), str(s.get("storage") or s.get("path") or "")
                or None, active=status in SAB_HISTORY_BUSY, failed=status == "Failed",
                completed_at=_ts(s.get("completed"))))
        return out

    async def delete_history(self, nzo_id: str) -> None:
        # del_files=0: bestanden blijven; die heeft de opruimer al naar de prullenbak verhuisd
        await self._api(mode="history", name="delete", value=nzo_id, del_files=0)


# -- qBittorrent ---------------------------------------------------------------------------
def seeding_status(t: dict[str, Any], prefs: dict[str, Any], public_seed_hours: float
                   ) -> tuple[bool, str]:
    """Mag deze torrent weg? Nooit vóór de deellimiet; privé zonder bekende limiet: nooit."""
    if float(t.get("progress") or 0) < 1:
        return False, "nog niet compleet"
    if t.get("state") in TORRENT_LIMIT_REACHED:
        return True, "deellimiet gehaald (gestopt door qBittorrent)"
    ratio = float(t.get("ratio") or 0)
    seed_min = float(t.get("seeding_time") or 0) / 60

    def eff(key: str, limit_key: str, enabled: str, pref: str) -> float:
        v = t.get(key)
        if v is not None and float(v) not in (-2,):
            return float(v)
        lim = t.get(limit_key)
        if lim is not None and float(lim) == -1:
            return -1
        if lim is not None and float(lim) >= 0:
            return float(lim)
        return float(prefs.get(pref) or -1) if prefs.get(enabled) else -1

    max_ratio = eff("max_ratio", "ratio_limit", "max_ratio_enabled", "max_ratio")
    max_seed = eff("max_seeding_time", "seeding_time_limit", "max_seeding_time_enabled",
                   "max_seeding_time")
    if max_ratio > 0 or max_seed > 0:
        if (max_ratio > 0 and ratio >= max_ratio) or (max_seed > 0 and seed_min >= max_seed):
            return True, "deellimiet gehaald"
        return False, "deellimiet nog niet gehaald"
    private = t.get("private")
    if private is None or private:
        # privé-trackers hebben eigen regels die MediaGuard niet kent: nooit zelf weghalen
        return False, "privé-tracker zonder ingestelde deellimiet"
    if seed_min >= public_seed_hours * 60:
        return True, f"openbaar, {public_seed_hours:g} uur gedeeld"
    return False, "openbaar, nog niet lang genoeg gedeeld"


class QbitClient:
    def __init__(self, ctx: AppContext, dc: DownloadClientCfg, password: str | None) -> None:
        self.name, self.base = dc.name, _base_url(dc).rstrip("/")
        self.username, self.password = dc.username or "", password or ""
        self._transport = _transport(ctx, dc.name)
        self._client: httpx.AsyncClient | None = None

    async def __aenter__(self) -> QbitClient:
        self._client = httpx.AsyncClient(timeout=20, transport=self._transport,
                                         headers={"Referer": self.base})
        if self.username or self.password:
            r = await self._client.post(f"{self.base}/api/v2/auth/login",
                                        data={"username": self.username,
                                              "password": self.password})
            r.raise_for_status()
            if r.text.strip().lower().startswith("fails"):
                raise RuntimeError("qBittorrent: inloggen geweigerd")
        return self

    async def __aexit__(self, *exc: object) -> None:
        if self._client is not None:
            await self._client.aclose()

    async def _get(self, path: str, **params: Any) -> Any:
        assert self._client is not None
        r = await self._client.get(f"{self.base}/api/v2/{path}", params=params)
        r.raise_for_status()
        return r.json()

    async def view(self, managed: set[str], public_seed_hours: float) -> ClientView:
        prefs = await self._get("app/preferences") or {}
        cats = await self._get("torrents/categories") or {}
        save = str(prefs.get("save_path") or "")
        out = ClientView(self.name, "torrent")
        for name in managed:
            info = cats.get(name)
            if info is None:
                continue
            path = str(info.get("savePath") or "") or (_join(save, name) if save else "")
            if path:
                out.roots.append(Root(_join(save, path) if save else path, self.name,
                                      "torrent", name))
        for t in await self._get("torrents/info") or []:
            done, why = seeding_status(t, prefs, public_seed_hours)
            out.items.append(ClientItem(
                self.name, "torrent", str(t.get("hash", "")).upper(), str(t.get("name") or ""),
                str(t.get("category") or ""), str(t.get("content_path") or "") or None,
                active=t.get("state") in TORRENT_BUSY, completed_at=_ts(t.get("completion_on")),
                seeding_done=done, seeding_reason=why, private=bool(t.get("private", True))))
        return out

    async def remove(self, torrent_hash: str) -> None:
        assert self._client is not None
        r = await self._client.post(f"{self.base}/api/v2/torrents/delete",
                                    data={"hashes": torrent_hash.lower(), "deleteFiles": "false"})
        r.raise_for_status()
