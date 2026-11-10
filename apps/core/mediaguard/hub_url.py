"""Adres van de Hub voor de link 'Terug naar de Hub' (Peil O-11, ADR-0016 punt 1).

Een optionele instelling ``system.hub_url`` in het bestaande instellingenregister: geen nieuwe
route, geen migratie, geen veld in het Custom App-formulier. Leeg = geen link. Geldige vorm
volgens de HUB_URL-regels van het platformcontract (§3.2): ``http(s)://<host>[:<poort>]`` zonder
pad, query, fragment of inloggegevens, en nooit loopback, link-local, ``localhost`` of
``0.0.0.0``/``::``. Er is nooit een standaard- of voorbeeldadres.
"""

from __future__ import annotations

import ipaddress
from urllib.parse import urlsplit

HUB_URL_KEY = "system.hub_url"
HUB_URL_ERROR = "Hub-adres: gebruik http(s)://<NAS-adres>:<poort>, zonder pad"


def _blocked_host(host: str) -> bool:
    h = host.strip("[]").lower().rstrip(".")
    if h == "localhost" or h.endswith(".localhost"):
        return True
    try:
        ip = ipaddress.ip_address(h)
    except ValueError:
        return False
    if isinstance(ip, ipaddress.IPv6Address) and ip.ipv4_mapped is not None:
        ip = ip.ipv4_mapped
    return ip.is_loopback or ip.is_link_local or ip.is_unspecified


def validate_hub_url(value: str) -> str:
    """Leeg → ``""``. Anders de genormaliseerde vorm (schema en host in kleine letters, zonder
    slotstreep); een ongeldige waarde geeft ``ValueError(HUB_URL_ERROR)`` (de settings-API maakt
    daar 422 van)."""
    v = value.strip()
    if not v:
        return ""
    if "\\" in v or any(c.isspace() for c in v):
        raise ValueError(HUB_URL_ERROR)
    try:
        parts = urlsplit(v)
        port = parts.port  # ValueError bij een ongeldige poort
    except ValueError as exc:
        raise ValueError(HUB_URL_ERROR) from exc
    host = parts.hostname or ""
    if (
        parts.scheme.lower() not in {"http", "https"}
        or not host
        or parts.username is not None
        or parts.password is not None
        or parts.path not in {"", "/"}
        or parts.query
        or parts.fragment
        or "?" in v
        or "#" in v
        or _blocked_host(host)
    ):
        raise ValueError(HUB_URL_ERROR)
    shown = f"[{host}]" if ":" in host else host
    return f"{parts.scheme.lower()}://{shown}" + (f":{port}" if port is not None else "")
