"""Dataminimalisatie vóór elke externe overdracht (DEEL S3).

- Geen absolute paden: ingekort tot de bestandsnaam (releasenaam blijft bruikbaar bewijs).
- Geen API-sleutels/tokens/wachtwoorden/cookies: velden verwijderd, waarden geredigeerd.
- Geen media: bytes, base64-blobs, frames/audio/afbeeldingen worden verwijderd.
- Geen configdumps: velden als ``config``/``settings``/``headers``/``env`` verwijderd.
- URL's: alleen schema + host; lokale/private hosts worden ``[lokale-host]``.
- Strings ingekort, lijsten begrensd, nesting begrensd, controle-tekens verwijderd.

De functie is puur en deterministisch zodat de preview exact gelijk is aan wat
verstuurd wordt, en de hash bruikbaar is als cachesleutel.
"""

from __future__ import annotations

import ipaddress
import re
from dataclasses import dataclass, field
from typing import Any
from urllib.parse import urlsplit

from mediaguard.secrets_store import redact

MAX_STRING = 500
MAX_LIST = 40
MAX_KEYS = 60
MAX_DEPTH = 6

_SECRET_KEY = re.compile(
    r"(api[_-]?key|apikey|token|secret|password|passwd|passkey|authorization|cookie|"
    r"session|credential|private[_-]?key|signature|bearer)", re.I)
_CONFIG_KEY = re.compile(
    r"^(config|configuration|settings|headers|env|environment|raw_config|config_dump|"
    r"settings_dump|connection_string|dsn)$", re.I)
_MEDIA_KEY = re.compile(
    r"(frame|thumbnail|image|screenshot|audio_data|video_data|waveform|poster|fanart|"
    r"base64|blob|^bytes$|^data_uri$)", re.I)
_HASH_KEY = re.compile(r"(hash|ed2k|crc|sha1|sha256|md5|fingerprint)", re.I)

_URL = re.compile(r"\b[a-zA-Z][a-zA-Z0-9+.-]*://[^\s\"'<>]+")
_UNIX_PATH = re.compile(r"(?<![\w.:/~])(?:~)?/(?:[^/\s\"'<>|]+/)+[^/\s\"'<>|]*")
_WIN_PATH = re.compile(r"\b[A-Za-z]:\\(?:[^\\\s\"'<>|]+\\)*[^\\\s\"'<>|]*")
_UNC_PATH = re.compile(r"\\\\[^\\\s\"'<>|]+(?:\\[^\\\s\"'<>|]+)+")
_LONG_TOKEN = re.compile(r"\b(?=[A-Za-z0-9_\-]*\d)(?=[A-Za-z0-9_\-]*[A-Za-z])[A-Za-z0-9_\-]{32,}\b")
_BASE64_BLOB = re.compile(r"^[A-Za-z0-9+/=\s]{400,}$")
_CONTROL = re.compile(r"[\u0000-\u0008\u000b\u000c\u000e-\u001f\u007f​-‏ -‮"
                      r"⁠-⁤﻿]")


@dataclass
class MinimizationReport:
    removed_fields: list[str] = field(default_factory=list)
    paths_shortened: int = 0
    urls_reduced: int = 0
    secrets_redacted: int = 0
    truncated: int = 0

    def as_dict(self) -> dict[str, Any]:
        return {
            "removed_fields": sorted(set(self.removed_fields)),
            "paths_shortened": self.paths_shortened,
            "urls_reduced": self.urls_reduced,
            "secrets_redacted": self.secrets_redacted,
            "truncated": self.truncated,
        }


def _is_private_host(host: str) -> bool:
    host = host.strip("[]").lower()
    if not host:
        return True
    if host in ("localhost",) or host.endswith((".local", ".lan", ".internal", ".home",
                                                ".localdomain")):
        return True
    if "." not in host:
        return True
    try:
        ip = ipaddress.ip_address(host)
    except ValueError:
        return False
    return ip.is_private or ip.is_loopback or ip.is_link_local or ip.is_reserved


def _reduce_url(match: re.Match[str], report: MinimizationReport) -> str:
    report.urls_reduced += 1
    try:
        parts = urlsplit(match.group(0))
        host = parts.hostname or ""
    except ValueError:
        return "[url]"
    if _is_private_host(host):
        return f"{parts.scheme}://[lokale-host]"
    return f"{parts.scheme}://{host}"


def _basename(match: re.Match[str], report: MinimizationReport) -> str:
    report.paths_shortened += 1
    raw = match.group(0).rstrip("/\\")
    name = re.split(r"[/\\]", raw)[-1] if raw else ""
    return name or "[pad]"


def clean_text(text: str, report: MinimizationReport | None = None,
               *, keep_hashes: bool = False, limit: int = MAX_STRING) -> str:
    report = report or MinimizationReport()
    out = _CONTROL.sub("", text)
    out = _URL.sub(lambda m: _reduce_url(m, report), out)
    out = _UNC_PATH.sub(lambda m: _basename(m, report), out)
    out = _WIN_PATH.sub(lambda m: _basename(m, report), out)
    out = _UNIX_PATH.sub(lambda m: _basename(m, report), out)
    redacted = redact(out)
    if redacted != out:
        report.secrets_redacted += 1
        out = redacted
    if not keep_hashes:
        replaced = _LONG_TOKEN.sub("***", out)
        if replaced != out:
            report.secrets_redacted += 1
            out = replaced
    if len(out) > limit:
        report.truncated += 1
        out = out[:limit] + "…"
    return out


def _minimize(value: Any, path: str, depth: int, report: MinimizationReport,
              keep_hashes: bool) -> tuple[bool, Any]:
    """Geeft (behouden?, waarde)."""
    if depth > MAX_DEPTH:
        report.removed_fields.append(f"{path} (te diep genest)")
        return False, None
    if value is None or isinstance(value, bool | int | float):
        return True, value
    if isinstance(value, bytes | bytearray | memoryview):
        report.removed_fields.append(f"{path} (binaire data)")
        return False, None
    if isinstance(value, str):
        if _BASE64_BLOB.match(value):
            report.removed_fields.append(f"{path} (media/blob)")
            return False, None
        return True, clean_text(value, report, keep_hashes=keep_hashes)
    if isinstance(value, dict):
        out: dict[str, Any] = {}
        for i, (k, v) in enumerate(sorted(value.items(), key=lambda kv: str(kv[0]))):
            key = clean_text(str(k), report, limit=80)
            sub = f"{path}.{key}" if path else key
            if i >= MAX_KEYS:
                report.truncated += 1
                break
            if _SECRET_KEY.search(key):
                report.removed_fields.append(f"{sub} (geheim)")
                report.secrets_redacted += 1
                continue
            if _CONFIG_KEY.match(key):
                report.removed_fields.append(f"{sub} (configuratie)")
                continue
            if _MEDIA_KEY.search(key):
                report.removed_fields.append(f"{sub} (media)")
                continue
            keep, cleaned = _minimize(v, sub, depth + 1, report,
                                      keep_hashes or bool(_HASH_KEY.search(key)))
            if keep:
                out[key] = cleaned
        return True, out
    if isinstance(value, list | tuple | set | frozenset):
        items = list(value)
        if len(items) > MAX_LIST:
            report.truncated += 1
            items = items[:MAX_LIST]
        cleaned_items = []
        for idx, item in enumerate(items):
            keep, cleaned = _minimize(item, f"{path}[{idx}]", depth + 1, report, keep_hashes)
            if keep:
                cleaned_items.append(cleaned)
        return True, cleaned_items
    return True, clean_text(str(value), report, keep_hashes=keep_hashes)


def minimize(payload: Any) -> tuple[dict[str, Any], dict[str, Any]]:
    """Minimaliseert een case-payload. Geeft (payload, rapport)."""
    report = MinimizationReport()
    if not isinstance(payload, dict):
        payload = {"value": payload}
    _, cleaned = _minimize(payload, "", 0, report, False)
    return cleaned, report.as_dict()
