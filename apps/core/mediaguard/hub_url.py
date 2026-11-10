"""Adres van de Hub voor de link 'Terug naar de Hub' (Peil O-11, ADR-0016 punt 1).

Een optionele instelling ``system.hub_url`` in het bestaande instellingenregister: geen nieuwe
route, geen migratie, geen veld in het Custom App-formulier. Leeg = geen link. Geldige vorm
volgens de adresregels van het platformcontract 1.3 (§3.2, §2.3 regel 5; ADR-0021):

- ``http(s)://<host>[:<poort>]`` zonder pad, query, fragment of inloggegevens;
- nooit loopback, link-local (``169.254.0.0/16``, ``fe80::/10``), ``localhost`` of
  ``0.0.0.0``/``::`` (ook ``0.0.0.0/8``, en IPv4 verpakt in IPv6);
- IPv4 alleen als ``a.b.c.d`` (vier decimale delen 0–255 zonder voorloopnul); een host die op
  een getal of ``0x``-deel eindigt maar daar niet aan voldoet (``127.1``, ``2130706433``,
  ``0x7f000001``, ``010.0.0.1``, ``0``) is ongeldig — een browser zou er een ander adres van
  maken;
- IPv6 alleen tussen ``[]`` en zonder zone-ID; één punt aan het eind van een naam telt niet mee
  (``localhost.`` = ``localhost``);
- alleen ASCII-namen (letters, cijfers, ``-``; geen ``%``-codering of IDN): anders kan een
  browser de naam naar een ander (verboden) adres vertalen.

Er is nooit een standaard- of voorbeeldadres. MediaGuard maakt zelf nooit verbinding met dit
adres (alleen een link in de browser); de contractregel over DNS-resolutie per verbinding geldt
dus niet hier. De web-UI past dezelfde regels toe op wat hij leest (``apps/web/lib/hub.ts``):
een opgeslagen waarde die niet (meer) geldig is, geeft geen link.
"""

from __future__ import annotations

import ipaddress
import re
from urllib.parse import urlsplit

HUB_URL_KEY = "system.hub_url"
HUB_URL_ERROR = "Hub-adres: gebruik http(s)://<NAS-adres>:<poort>, zonder pad"

_LABEL = re.compile(r"[a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?")
# laatste deel van alleen cijfers of een 0x-getal: een browser leest de host dan als IPv4
_NUMERIC_LABEL = re.compile(r"0x[0-9a-f]*|[0-9]+")
_OCTET = r"(?:25[0-5]|2[0-4][0-9]|1[0-9][0-9]|[1-9]?[0-9])"
_IPV4 = re.compile(rf"{_OCTET}(?:\.{_OCTET}){{3}}")


def _blocked_ip(ip: ipaddress.IPv4Address | ipaddress.IPv6Address) -> bool:
    if isinstance(ip, ipaddress.IPv6Address) and ip.ipv4_mapped is not None:
        ip = ip.ipv4_mapped
    if isinstance(ip, ipaddress.IPv4Address) and ip in ipaddress.IPv4Network("0.0.0.0/8"):
        return True
    return ip.is_loopback or ip.is_link_local or ip.is_unspecified


def _host(netloc: str) -> str | None:
    """De host in canonieke vorm (kleine letters, IPv6 tussen ``[]``, zonder punt aan het eind)
    of ``None`` als hij niet mag. ``netloc`` is zonder inloggegevens (die zijn al geweigerd)."""
    if netloc.startswith("["):
        inner, sep, rest = netloc[1:].partition("]")
        if not sep or (rest and not re.fullmatch(r":[0-9]+", rest)) or "%" in inner:
            return None
        try:
            ip6 = ipaddress.IPv6Address(inner)
        except ValueError:
            return None
        return None if _blocked_ip(ip6) else f"[{inner.lower()}]"
    host = netloc.rsplit(":", 1)[0] if ":" in netloc else netloc
    host = host.lower()
    if host.endswith("."):
        host = host[:-1]  # één punt aan het eind telt niet mee
    labels = host.split(".")
    if not host or not all(labels):
        return None
    if _NUMERIC_LABEL.fullmatch(labels[-1]):
        if not _IPV4.fullmatch(host):
            return None
        return None if _blocked_ip(ipaddress.IPv4Address(host)) else host
    if not all(_LABEL.fullmatch(label) for label in labels):
        return None
    if host == "localhost" or host.endswith(".localhost"):
        return None
    return host


def validate_hub_url(value: str) -> str:
    """Leeg → ``""``. Anders de genormaliseerde vorm (schema en host in kleine letters, zonder
    slotstreep); een ongeldige waarde geeft ``ValueError(HUB_URL_ERROR)`` (de settings-API maakt
    daar 422 van)."""
    v = value.strip()
    if not v:
        return ""
    if "\\" in v or any(c.isspace() for c in v) or not v.isascii():
        raise ValueError(HUB_URL_ERROR)
    try:
        parts = urlsplit(v)
        port = parts.port  # ValueError bij een ongeldige poort
    except ValueError as exc:
        raise ValueError(HUB_URL_ERROR) from exc
    if (
        parts.scheme.lower() not in {"http", "https"}
        or "@" in parts.netloc
        or parts.netloc.endswith(":")
        or parts.path not in {"", "/"}
        or parts.query
        or parts.fragment
        or "?" in v
        or "#" in v
    ):
        raise ValueError(HUB_URL_ERROR)
    host = _host(parts.netloc)
    if host is None:
        raise ValueError(HUB_URL_ERROR)
    return f"{parts.scheme.lower()}://{host}" + (f":{port}" if port is not None else "")

