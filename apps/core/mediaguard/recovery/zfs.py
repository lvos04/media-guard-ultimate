"""Optionele ZFS-snapshot vóór vervanging via de TrueNAS REST-API.

Echte HTTP-client (httpx) met Bearer-API-key uit secret ``truenas:apikey``. TrueNAS
SCALE 24.10+ gebruikt ``/api/v2.0/pool/snapshot``; oudere versies ``/api/v2.0/zfs/snapshot``
(automatische terugval bij 404). Snapshots alleen zijn niet genoeg (G2): dit is een extra
vangnet naast de quarantainekopie, nooit een vervanging ervan.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

import httpx

if TYPE_CHECKING:
    from mediaguard.context import AppContext

ENDPOINTS = ("/api/v2.0/pool/snapshot", "/api/v2.0/zfs/snapshot")


class SnapshotError(Exception):
    pass


async def create_snapshot(ctx: AppContext, dataset: str, name: str) -> dict[str, Any]:
    base = str(ctx.config.value("recovery.truenas_url") or "").rstrip("/")
    if not base:
        raise SnapshotError("TrueNAS-URL niet ingesteld")
    key = ctx.secrets.get("truenas:apikey")
    if not key:
        raise SnapshotError("Geheim 'truenas:apikey' ontbreekt")
    verify = bool(ctx.config.value("recovery.truenas_verify_tls"))
    transport = ctx.services.get("truenas_transport")
    async with httpx.AsyncClient(base_url=base, timeout=30.0, verify=verify,
                                 transport=transport,
                                 headers={"Authorization": f"Bearer {key}"}) as client:
        last: httpx.Response | None = None
        for path in ENDPOINTS:
            try:
                resp = await client.post(path, json={"dataset": dataset, "name": name})
            except httpx.HTTPError as exc:
                raise SnapshotError(f"TrueNAS onbereikbaar: {type(exc).__name__}") from exc
            if resp.status_code == 404:
                last = resp
                continue
            if resp.status_code >= 400:
                raise SnapshotError(f"TrueNAS weigerde snapshot ({resp.status_code}): "
                                    f"{resp.text[:200]}")
            data = resp.json() if resp.content else {}
            return {"dataset": dataset, "name": name, "endpoint": path, "response": data}
    raise SnapshotError(f"Snapshot-endpoint niet gevonden ({last.status_code if last else '?'})")


async def test_connection(ctx: AppContext) -> dict[str, Any]:
    base = str(ctx.config.value("recovery.truenas_url") or "").rstrip("/")
    key = ctx.secrets.get("truenas:apikey")
    if not base or not key:
        return {"ok": False, "message": "URL of API-key ontbreekt"}
    async with httpx.AsyncClient(base_url=base, timeout=10.0,
                                 verify=bool(ctx.config.value("recovery.truenas_verify_tls")),
                                 transport=ctx.services.get("truenas_transport"),
                                 headers={"Authorization": f"Bearer {key}"}) as client:
        try:
            resp = await client.get("/api/v2.0/system/info")
        except httpx.HTTPError as exc:
            return {"ok": False, "message": f"Onbereikbaar: {type(exc).__name__}"}
    if resp.status_code != 200:
        return {"ok": False, "message": f"HTTP {resp.status_code}"}
    info = resp.json()
    return {"ok": True, "version": info.get("version"), "hostname": info.get("hostname")}
