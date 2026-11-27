"""Volledige, alleen-lezen poolmeting van de bevestigde Hub (contract 1.4)."""

from __future__ import annotations

import asyncio
import ipaddress
import socket
from datetime import UTC, datetime, timedelta
from typing import Any

import httpx
from pydantic import BaseModel

from mediaguard.context import AppContext
from mediaguard.db import loads
from mediaguard.hub_url import blocked_ip, validate_hub_url

URL_KEY = "system.pool_hub_url"
SECRET = "hub:poolstatus"
PATH = "/api/v1/inventory/pools"
MAX_AGE = timedelta(seconds=900)
UNKNOWN = "De poolstatus van de Hub is onbekend. Een geldige poolmeting ontbreekt."


class PoolBinding(BaseModel):
    origin: str
    revision: str


def validate_url(value: str) -> str:
    if not isinstance(value, str):
        raise ValueError("Ongeldig Hub-adres voor poolstatus.")
    origin = validate_hub_url(value)
    if origin and not origin.startswith("https://"):
        raise ValueError("Het Hub-adres voor poolstatus vereist HTTPS.")
    return origin


def origin(ctx: AppContext) -> str:
    # Controle per bestand: lees alleen deze globale instelling, ook na een wijziging elders.
    row = ctx.db.fetch_one("SELECT value_json FROM settings_values WHERE scope_type='global' "
                           "AND scope_id='' AND key=?", (URL_KEY,))
    if row is None:
        return ""
    value = loads(row["value_json"])
    if not isinstance(value, str):
        raise ValueError("Ongeldige instelling voor de Hub-poolbron.")
    return value


def binding(ctx: AppContext) -> PoolBinding | None:
    # Lees adres en revisie uit dezelfde SQLite-momentopname, ook bij twee beheerders.
    row = ctx.db.fetch_one(
        "SELECT s.value_json, r.revision FROM settings_values s CROSS JOIN hub_pool_revision r "
        "WHERE s.scope_type='global' AND s.scope_id='' AND s.key=? AND r.id=1", (URL_KEY,))
    if row is None or not (address := loads(row["value_json"])):
        return None
    return PoolBinding(origin=address, revision=row["revision"])


def configured(ctx: AppContext) -> bool:
    # Een oudere versie accepteerde globale pooladressen met een scope-ID. Pauzeer
    # ook dan, maar gebruik uitsluitend het echte globale adres voor netwerkverkeer.
    row = ctx.db.fetch_one(
        "SELECT COALESCE((SELECT value_json <> '\"\"' FROM settings_values "
        "WHERE scope_type='global' AND scope_id='' AND key=?), "
        "EXISTS (SELECT 1 FROM settings_values WHERE scope_type='global' AND scope_id>'' "
        "AND key=? AND value_json <> '\"\"'))", (URL_KEY, URL_KEY))
    return bool(row[0])


def fresh(observed_at: Any, valid_until: Any) -> bool:
    if not isinstance(observed_at, str) or not isinstance(valid_until, str):
        return False
    try:
        measured = datetime.fromisoformat(observed_at)
        expires = datetime.fromisoformat(valid_until)
        now = datetime.now(UTC)
        return (measured.utcoffset() == expires.utcoffset() == timedelta()
                and timedelta() <= now - measured <= MAX_AGE and now < expires)
    except (ValueError, OverflowError):
        return False


def unknown() -> dict[str, Any]:
    return {"source": "hub", "ok": False, "pools": [], "error_nl": UNKNOWN}


def _health(data: Any) -> dict[str, Any]:
    if (not isinstance(data, dict)
            or set(data) != {"kind", "status", "reason_code", "observed_at", "geldig_tot", "items"}
            or data["kind"] != "pools" or data["status"] != "ok"
            or data["reason_code"] is not None
            or not fresh(data.get("observed_at"), data.get("geldig_tot"))):
        return unknown()
    items = data.get("items")
    if not isinstance(items, list) or not items:
        return unknown()
    pools = []
    for number, item in enumerate(items, start=1):
        if (not isinstance(item, dict) or set(item) != {"status", "gezond"}
                or not isinstance(item["status"], str) or not item["status"].strip()
                or not isinstance(item["gezond"], bool)):
            return unknown()
        # De Hub verstrekt geen poolnamen. Alleen bekende ZFS-toestanden worden getoond.
        status = item["status"]
        if status not in {"ONLINE", "DEGRADED", "FAULTED", "OFFLINE", "UNAVAIL", "REMOVED",
                          "SUSPENDED"}:
            return unknown()
        pools.append({"name": f"Pool {number}", "status": status, "healthy": item["gezond"]})
    return {"source": "hub", "ok": all(p["status"] == "ONLINE" and p["healthy"] for p in pools),
            "pools": pools, "observed_at": data["observed_at"], "geldig_tot": data["geldig_tot"]}


async def _address(url: httpx.URL) -> str:
    # Verbind met precies het gecontroleerde adres. Host en TLS blijven op de gekozen Hub staan.
    rows = await asyncio.get_running_loop().getaddrinfo(
        url.host, url.port or 443, type=socket.SOCK_STREAM)
    addresses = [ipaddress.ip_address(row[4][0]) for row in rows]
    if not addresses or any(blocked_ip(address) for address in addresses):
        raise ValueError("Hub-adres niet toegestaan")
    return str(addresses[0])


async def read(ctx: AppContext) -> dict[str, Any] | None:
    # Adres en geheim vormen één momentopname. Een wijziging trekt de meetopdracht in.
    with ctx.db.transaction():
        base = origin(ctx)
        if not base:
            return unknown() if configured(ctx) else None
        token = ctx.secrets.get(SECRET)
    if not token:
        return unknown()
    try:
        async with asyncio.timeout(10):
            url = httpx.URL(validate_url(base) + PATH)
            peer = await _address(url)
            async with httpx.AsyncClient(transport=ctx.services.get("hub_pool_transport"),
                                         trust_env=False, follow_redirects=False) as client:
                response = await client.get(
                    url.copy_with(host=peer),
                    headers={"Host": url.netloc.decode("ascii"), "Authorization": f"Bearer {token}",
                             "Accept": "application/json"},
                    extensions={"sni_hostname": url.host})
            if response.status_code == 200:
                return _health(response.json())
    except (httpx.HTTPError, OSError, ValueError, RecursionError):
        pass  # Antwoord, headers en fouttekst kunnen geheime gegevens bevatten.
    return unknown()
