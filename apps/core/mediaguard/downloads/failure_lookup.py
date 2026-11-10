"""Wat de downloadclient zelf weet over een mislukte download (rapport 3, R6).

Sonarr/Radarr zeggen alleen 'gegrabd, maar niet geïmporteerd'. SABnzbd en qBittorrent weten
waarom: uitpakken mislukt, ontbrekende artikelen, wachtwoord, … Deze module haalt dat op met
de verbindingsgegevens die MediaGuard al voor de inrichting bewaart (nooit naar de UI).

Losse, herbruikbare functies (ook voor mediaguard/delivery): ``lookup`` en
``translate_failure``. Los van ``downloads.clients`` (de opruimer), zodat beide onafhankelijk
kunnen veranderen.
"""

from __future__ import annotations

import logging
from typing import TYPE_CHECKING, Any

import httpx

if TYPE_CHECKING:
    from mediaguard.context import AppContext

LOGGER = logging.getLogger(__name__)
TIMEOUT = httpx.Timeout(15, connect=5)

# (zoekterm in de melding van de client, code, Nederlandse uitleg) — eerste treffer wint
FAILURE_NL: tuple[tuple[str, str, str], ...] = (
    ("password", "password", "Archief is met een wachtwoord beveiligd"),
    ("encrypted", "password", "Archief is met een wachtwoord beveiligd"),
    ("not on your server", "missing-articles", "Ontbrekende artikelen bij de nieuwsserver"),
    ("articles were missing", "missing-articles", "Ontbrekende artikelen bij de nieuwsserver"),
    ("missing articles", "missing-articles", "Ontbrekende artikelen bij de nieuwsserver"),
    ("% available", "missing-articles", "Ontbrekende artikelen bij de nieuwsserver"),
    ("out of retention", "retention", "Te oud voor de retentie van je nieuwsserver"),
    ("retention", "retention", "Te oud voor de retentie van je nieuwsserver"),
    ("repair failed", "repair-failed", "Reparatie (par2) mislukt"),
    ("par2", "repair-failed", "Reparatie (par2) mislukt"),
    ("crc", "repair-failed", "Beschadigde bestanden (CRC-fout)"),
    ("unpacking failed", "unpack-failed", "Uitpakken mislukt"),
    ("unpack", "unpack-failed", "Uitpakken mislukt"),
    ("disk full", "disk-full", "Downloadschijf vol"),
    ("no space", "disk-full", "Downloadschijf vol"),
    ("duplicate", "duplicate", "Dubbele download, overgeslagen door de client"),
    ("aborted", "aborted", "Download afgebroken door de client"),
    ("cannot be completed", "aborted", "Download kon niet worden voltooid"),
    ("missingfiles", "missing-files", "Bestanden van de torrent zijn verdwenen"),
    ("error", "client-error", "Downloadclient meldt een fout"),
)


def translate_failure(message: str | None, status: str | None = None) -> dict[str, str]:
    """{'code', 'reason_nl', 'raw'} voor een foutmelding van SABnzbd/qBittorrent."""
    text = f"{message or ''} {status or ''}".lower()
    for needle, code, nl in FAILURE_NL:
        if needle in text:
            return {"code": code, "reason_nl": nl, "raw": str(message or status or "")}
    return {"code": "download-failed", "reason_nl": "Download mislukt in de downloadclient",
            "raw": str(message or status or "")}


def _base(dc: Any) -> str:
    scheme = "https" if dc.use_ssl else "http"
    sub = str(dc.url_base or "").strip("/")
    return f"{scheme}://{dc.host}:{dc.port}" + (f"/{sub}" if sub else "")


def _transport(ctx: AppContext) -> Any:
    return ctx.services.get("failure_lookup_transport")


async def sab_history(ctx: AppContext, dc: Any, nzo_ids: list[str]) -> dict[str, dict[str, Any]]:
    """nzo_id → {'status', 'fail_message', 'name'} uit de SABnzbd-historie."""
    from mediaguard.provisioning.store import dc_secret

    key = ctx.secrets.get(dc_secret(dc.name, "api_key"))
    if not key or not nzo_ids:
        return {}
    params: dict[str, Any] = {"mode": "history", "output": "json", "apikey": key, "limit": 1000,
              "nzo_ids": ",".join(nzo_ids)}
    async with httpx.AsyncClient(timeout=TIMEOUT, transport=_transport(ctx)) as client:
        resp = await client.get(f"{_base(dc)}/api", params=params)
        resp.raise_for_status()
        slots = ((resp.json() or {}).get("history") or {}).get("slots") or []
    return {str(s.get("nzo_id")): {"status": str(s.get("status") or ""),
                                   "fail_message": str(s.get("fail_message") or ""),
                                   "name": s.get("name"), "category": s.get("category")}
            for s in slots if s.get("nzo_id")}


async def sab_queue(ctx: AppContext, dc: Any, nzo_ids: list[str]) -> dict[str, dict[str, Any]]:
    """nzo_id → {'status', 'fail_message', 'name'} voor downloads die nog in de SAB-wachtrij
    staan (bezig, gepauzeerd, uitpakken). Zonder deze controle leek een lopende download
    'verdwenen' (rapport 4, S4)."""
    from mediaguard.provisioning.store import dc_secret

    key = ctx.secrets.get(dc_secret(dc.name, "api_key"))
    if not key or not nzo_ids:
        return {}
    params: dict[str, Any] = {"mode": "queue", "output": "json", "apikey": key, "limit": 1000,
              "nzo_ids": ",".join(nzo_ids)}
    async with httpx.AsyncClient(timeout=TIMEOUT, transport=_transport(ctx)) as client:
        resp = await client.get(f"{_base(dc)}/api", params=params)
        resp.raise_for_status()
        slots = ((resp.json() or {}).get("queue") or {}).get("slots") or []
    return {str(s.get("nzo_id")): {"status": str(s.get("status") or "Downloading"),
                                   "fail_message": "", "name": s.get("filename"),
                                   "active": True}
            for s in slots if s.get("nzo_id")}


async def qbit_state(ctx: AppContext, dc: Any, hashes: list[str]) -> dict[str, dict[str, Any]]:
    """infohash (kleine letters) → {'status', 'fail_message'}; ontbrekend = verwijderd."""
    from mediaguard.provisioning.store import dc_secret

    if not hashes:
        return {}
    password = ctx.secrets.get(dc_secret(dc.name, "password")) or ""
    async with httpx.AsyncClient(base_url=_base(dc), timeout=TIMEOUT,
                                 transport=_transport(ctx)) as client:
        if dc.username or password:
            await client.post("/api/v2/auth/login",
                              data={"username": dc.username, "password": password})
        resp = await client.get("/api/v2/torrents/info",
                                params={"hashes": "|".join(h.lower() for h in hashes)})
        resp.raise_for_status()
        rows = resp.json() or []
    out = {}
    for t in rows:
        state = str(t.get("state") or "")
        failed = state in ("error", "missingFiles")
        out[str(t.get("hash") or "").lower()] = {
            "status": "Failed" if failed else state, "fail_message": state if failed else "",
            "name": t.get("name")}
    return out


async def lookup(ctx: AppContext, download_ids: list[str]) -> dict[str, dict[str, Any]]:
    """Per download-ID (SAB nzo_id of torrent-hash) wat de client zegt, met Nederlandse reden.

    Alleen clients die MediaGuard kent (Inrichten → Downloads). Fouten per client worden
    gelogd en overgeslagen: dit is diagnose, nooit een reden om te stoppen."""
    return (await lookup_with_status(ctx, download_ids))[0]


def kind_of(download_id: str) -> str | None:
    """'sab', 'torrent' of None (onbekend formaat)."""
    d = str(download_id or "")
    if d.upper().startswith("SABNZBD_"):
        return "sab"
    if len(d) in (40, 64):
        return "torrent"
    return None


async def lookup_with_status(ctx: AppContext, download_ids: list[str]
                             ) -> tuple[dict[str, dict[str, Any]], set[str]]:
    """Zoals ``lookup``, plus welke soorten clients ('sab'/'torrent') volledig bevraagd zijn.

    Een download is alleen echt 'verdwenen' als zijn soort client bereikbaar was en hem
    nergens (wachtrij én historie) kent — een onbereikbare client zegt niets (S4)."""
    from mediaguard.provisioning.store import load

    ids = [str(d) for d in download_ids if d]
    queried: set[str] = set()
    if not ids:
        return {}, queried
    nzo = [d for d in ids if d.upper().startswith("SABNZBD_")]
    torrents = [d for d in ids if len(d) in (40, 64) and not d.upper().startswith("SABNZBD_")]
    out: dict[str, dict[str, Any]] = {}
    failed_kinds: set[str] = set()
    for dc in load(ctx).download_clients:
        if not dc.enabled:
            continue
        kind = "sab" if dc.implementation == "Sabnzbd" else \
            "torrent" if dc.implementation == "QBittorrent" else None
        try:
            if kind == "sab" and nzo:
                found = await sab_queue(ctx, dc, nzo)
                found.update({k: v for k, v in (await sab_history(ctx, dc, nzo)).items()
                              if k not in found})
            elif kind == "torrent" and torrents:
                found = await qbit_state(ctx, dc, torrents)
            else:
                continue
        except (httpx.HTTPError, ValueError) as exc:
            LOGGER.info("downloadclient %s niet te raadplegen: %s", dc.name, exc)
            failed_kinds.add(str(kind))
            continue
        queried.add(str(kind))
        for did in nzo + torrents:
            hit = found.get(did) or found.get(did.lower())
            if hit is None:
                continue
            failed = hit["status"].lower() == "failed"
            out[did] = {**hit, "client": dc.name, "failed": failed,
                        **(translate_failure(hit["fail_message"], hit["status"]) if failed
                           else {"code": None, "reason_nl": None, "raw": ""})}
    # een soort die bij één van de clients faalde, telt niet als volledig bevraagd
    return out, queried - failed_kinds


async def recent_client_errors(ctx: AppContext, limit: int = 20) -> list[dict[str, Any]]:
    """Laatste fouten die SABnzbd en qBittorrent zelf melden (rapport 5, M7), nieuwste eerst.

    Elk item: client, name, reason_nl, raw, at (ISO of None). Onbereikbare clients worden
    overgeslagen; dit is alleen diagnose."""
    from mediaguard.provisioning.store import dc_secret, load

    out: list[dict[str, Any]] = []
    for dc in load(ctx).download_clients:
        if not dc.enabled:
            continue
        try:
            if dc.implementation == "Sabnzbd":
                key = ctx.secrets.get(dc_secret(dc.name, "api_key"))
                if not key:
                    continue
                params: dict[str, Any] = {"mode": "history", "output": "json", "apikey": key,
                                          "limit": limit, "failed_only": 1}
                async with httpx.AsyncClient(timeout=TIMEOUT, transport=_transport(ctx)) as c:
                    resp = await c.get(f"{_base(dc)}/api", params=params)
                    resp.raise_for_status()
                    slots = ((resp.json() or {}).get("history") or {}).get("slots") or []
                for s in slots:
                    info = translate_failure(s.get("fail_message"), s.get("status"))
                    done = s.get("completed")
                    out.append({"client": dc.name, "name": s.get("name"),
                                "category": s.get("category"), **info,
                                "at": _epoch_iso(done)})
            elif dc.implementation == "QBittorrent":
                password = ctx.secrets.get(dc_secret(dc.name, "password")) or ""
                async with httpx.AsyncClient(base_url=_base(dc), timeout=TIMEOUT,
                                             transport=_transport(ctx)) as c:
                    if dc.username or password:
                        await c.post("/api/v2/auth/login",
                                     data={"username": dc.username, "password": password})
                    resp = await c.get("/api/v2/torrents/info", params={"filter": "errored"})
                    resp.raise_for_status()
                    rows = resp.json() or []
                for t in rows[:limit]:
                    info = translate_failure(str(t.get("state") or ""), "failed")
                    out.append({"client": dc.name, "name": t.get("name"),
                                "category": t.get("category"), **info,
                                "at": _epoch_iso(t.get("added_on"))})
        except (httpx.HTTPError, ValueError) as exc:
            LOGGER.info("downloadclient %s niet te raadplegen: %s", dc.name, exc)
    out.sort(key=lambda r: str(r.get("at") or ""), reverse=True)
    return out[:limit]


def _epoch_iso(value: Any) -> str | None:
    from datetime import UTC, datetime

    try:
        return datetime.fromtimestamp(int(value), UTC).isoformat(timespec="seconds")
    except (TypeError, ValueError, OSError):
        return None
