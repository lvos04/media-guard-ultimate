"""Waar komt een verzoek vandaan? Echt IP-adres, https en interne herkomst.

Keten in de alles-in-één-container::

    telefoon/browser → Cloudflare → cloudflared → web (server.mjs, :30080) → core (127.0.0.1)

``server.mjs`` zet bij elk doorgestuurd verzoek zelf ``X-MG-Peer`` (het TCP-adres dat hij
zag) en ``X-MG-TLS`` (``1`` als de browser via TLS met hem praatte). Dezelfde headers van de
client gooit hij weg, net als ``X-MediaGuard-Token`` en ``X-MediaGuard-Hook-Key``.

Core vertrouwt die headers alleen als de TCP-verbinding zelf intern is: loopback, of in de
losse compose-opstelling (core luistert dan niet op 127.0.0.1) het private Docker-netwerk.

- **Echt IP**: ``CF-Connecting-IP`` (of het laatste adres uit ``X-Forwarded-For``) telt
  alleen als ``X-MG-Peer`` een vertrouwde proxy is (``security.trusted_proxies``: loopback,
  het eigen adres van de NAS en standaard de Docker-netwerken waar een lokale cloudflared in
  draait). Anders telt het adres dat de web-server zag.
- **https**: TLS naar de web-server, of ``X-Forwarded-Proto: https`` van een vertrouwde proxy
  (cloudflared stuurt dat mee).
- **Interne herkomst**: de interne token mag alleen van een rechtstreekse interne verbinding
  komen, nooit van iets dat via de web-server (en dus mogelijk de tunnel) binnenkwam.
"""

from __future__ import annotations

import ipaddress
import os
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from starlette.requests import HTTPConnection

PEER_HEADER = "x-mg-peer"
TLS_HEADER = "x-mg-tls"
# headers die alleen een proxy zet; een rechtstreekse interne aanroep heeft ze nooit
PROXY_HEADERS = (PEER_HEADER, "cf-connecting-ip", "cf-ray", "x-forwarded-for", "forwarded",
                 "x-real-ip")
DEFAULT_TRUSTED_PROXIES = ["127.0.0.1", "::1", "172.16.0.0/12"]
# Starlette's TestClient meldt zich als 'testclient'; over een echte socket kan dat niet
_TEST_PEER = "testclient"
# privé-netwerken (RFC 1918 en IPv6 ULA); bewust niet ipaddress.is_private, dat ook
# documentatie- en andere speciale reeksen meetelt
_PRIVATE = [ipaddress.ip_network(n) for n in ("10.0.0.0/8", "172.16.0.0/12", "192.168.0.0/16",
                                              "fc00::/7")]

Network = ipaddress.IPv4Network | ipaddress.IPv6Network
Address = ipaddress.IPv4Address | ipaddress.IPv6Address


def parse_ip(value: str | None) -> Address | None:
    raw = (value or "").strip().strip("[]")
    if not raw:
        return None
    if "%" in raw:  # IPv6-zone (fe80::1%eth0)
        raw = raw.split("%", 1)[0]
    try:
        ip = ipaddress.ip_address(raw)
    except ValueError:
        return None
    if isinstance(ip, ipaddress.IPv6Address) and ip.ipv4_mapped is not None:
        return ip.ipv4_mapped
    return ip


def parse_networks(values: Any) -> list[Network]:
    out: list[Network] = []
    for v in values if isinstance(values, list | tuple) else []:
        try:
            out.append(ipaddress.ip_network(str(v).strip(), strict=False))
        except ValueError:
            continue  # ongeldige regel overslaan; de rest blijft gelden
    return out


def tcp_peer(request: HTTPConnection) -> str:
    return request.client.host if request.client else ""


def _core_on_loopback() -> bool:
    bind = os.environ.get("MG_BIND", "0.0.0.0")  # noqa: S104 - alleen een vergelijking
    ip = parse_ip(bind)
    return bool(ip and ip.is_loopback) or bind == "localhost"


def is_internal_tcp(request: HTTPConnection) -> bool:
    """Komt de TCP-verbinding zelf van binnen de app (web-server, encoder, forks)?"""
    host = tcp_peer(request)
    if host == _TEST_PEER:
        return True
    ip = parse_ip(host)
    if ip is None:
        return False
    if ip.is_loopback:
        return True
    # losse compose-opstelling: core luistert op het private Docker-netwerk (niet gepubliceerd)
    return any(ip in net for net in _PRIVATE) and not _core_on_loopback()


def internal_origin(request: HTTPConnection) -> bool:
    """Mag dit verzoek een interne token gebruiken? Alleen een rechtstreekse interne
    verbinding zonder proxy-headers: nooit iets dat via de web-server (tunnel) kwam."""
    if not is_internal_tcp(request):
        return False
    headers = request.headers
    return not any(h in headers for h in PROXY_HEADERS)


def _ctx(request: HTTPConnection) -> Any:
    try:
        return request.app.state.ctx
    except AttributeError:
        return None


def _own_addresses() -> set[Address]:
    out: set[Address] = set()
    for name in ("MG_PUBLIC_HOST", "MG_AIO_LAN_IP"):
        ip = parse_ip(os.environ.get(name))
        if ip is not None:
            out.add(ip)
    return out


def trusted_proxy(request: HTTPConnection, addr: str | None) -> bool:
    ip = parse_ip(addr)
    if ip is None:
        return False
    if ip.is_loopback or ip in _own_addresses():
        return True
    ctx = _ctx(request)
    values: Any = DEFAULT_TRUSTED_PROXIES
    if ctx is not None:
        try:
            values = ctx.config.value("security.trusted_proxies")
        except KeyError:
            values = DEFAULT_TRUSTED_PROXIES
    return any(ip in net for net in parse_networks(values))


def forwarded_client(request: HTTPConnection) -> str | None:
    """Clientadres zoals een (vertrouwde) proxy het doorgaf: CF-Connecting-IP, anders het
    laatste geldige adres uit X-Forwarded-For (dat zette de dichtstbijzijnde proxy)."""
    cf = parse_ip(request.headers.get("cf-connecting-ip"))
    if cf is not None:
        return str(cf)
    xff = request.headers.get("x-forwarded-for", "")
    for part in reversed(xff.split(",")):
        ip = parse_ip(part)
        if ip is not None:
            return str(ip)
    return None


def client_ip(request: HTTPConnection) -> str:
    """Het adres van de echte client (voor aanmeldgeschiedenis, limieten en meldingen)."""
    host = tcp_peer(request)
    if not is_internal_tcp(request):
        return host or "onbekend"
    peer = request.headers.get(PEER_HEADER)
    if peer is None:
        # rechtstreeks intern verzoek of een eigen proxy op dezelfde machine
        return forwarded_client(request) or host or "onbekend"
    peer_ip = parse_ip(peer)
    seen = str(peer_ip) if peer_ip is not None else peer.strip()[:64]
    if trusted_proxy(request, seen):
        return forwarded_client(request) or seen or "onbekend"
    if request.headers.get("cf-connecting-ip") and peer_ip is not None and \
            (peer_ip.is_private or peer_ip.is_loopback):
        _note_untrusted_tunnel(request, seen)
    return seen or "onbekend"


UNTRUSTED_TUNNEL_KEY = "security_untrusted_tunnel_peer"


def _note_untrusted_tunnel(request: HTTPConnection, peer: str) -> None:
    """Cloudflare-verkeer via een (LAN-)adres dat geen vertrouwde proxy is: waarschijnlijk een
    cloudflared op een ander apparaat. Onthouden voor een hint bij 'Wacht op jou'; nooit
    automatisch vertrouwen."""
    import time

    ctx = _ctx(request)
    if ctx is not None:
        ctx.services[UNTRUSTED_TUNNEL_KEY] = {"peer": peer, "at": time.time()}


def is_https(request: HTTPConnection) -> bool:
    """Gebruikt de browser https (direct, of via een vertrouwde proxy zoals cloudflared)?"""
    if request.url.scheme in ("https", "wss"):
        return True
    if not is_internal_tcp(request):
        return False
    headers = request.headers
    if headers.get(TLS_HEADER) == "1":
        return True
    peer = headers.get(PEER_HEADER)
    if peer is not None and not trusted_proxy(request, peer):
        return False
    proto = headers.get("x-forwarded-proto", "").split(",")[0].strip().lower()
    if proto == "https":
        return True
    return '"https"' in headers.get("cf-visitor", "").replace(" ", "")


_LAN_SUFFIXES = (".local", ".lan", ".home", ".internal", ".localdomain", ".home.arpa",
                 ".intranet", ".private", ".localhost", ".fritz.box")


def is_public_host(host: str) -> bool:
    """Is dit een adres op internet (tunnel-domein, publiek IP), en niet iets op je LAN?"""
    h = (host or "").strip().lower().rstrip(".")
    if h.startswith("["):  # [IPv6]:poort
        h = h[1:].split("]", 1)[0]
    elif h.count(":") == 1:
        h = h.split(":", 1)[0]
    if not h:
        return False
    ip = parse_ip(h)
    if ip is not None:
        return bool(ip.is_global)
    if "." not in h or h == "localhost":
        return False
    return not h.endswith(_LAN_SUFFIXES)


def secure_public_url(url: str) -> tuple[str, bool]:
    """``http://`` naar een publiek adres zonder eigen poort wordt ``https://`` (een tunnel
    zoals Cloudflare praat altijd https). LAN-adressen en adressen met een eigen poort
    (bijv. http://nas.voorbeeld.nl:30080 rechtstreeks) blijven zoals ze zijn."""
    from urllib.parse import urlsplit, urlunsplit

    raw = (url or "").strip().rstrip("/")
    if not raw:
        return raw, False
    if "://" not in raw:
        parts = urlsplit("//" + raw)
        scheme = "https" if is_public_host(parts.netloc) and parts.port in (None, 443) \
            else "http"
        return urlunsplit((scheme, parts.netloc, parts.path, "", "")).rstrip("/"), \
            scheme == "https"
    parts = urlsplit(raw)
    try:
        port = parts.port
    except ValueError:
        return raw, False
    if parts.scheme == "http" and is_public_host(parts.netloc) and port in (None, 80, 443):
        host = parts.hostname or ""
        netloc = f"[{host}]" if ":" in host else host
        return urlunsplit(("https", netloc, parts.path, parts.query, "")).rstrip("/"), True
    return raw, False
