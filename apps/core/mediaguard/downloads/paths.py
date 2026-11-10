"""Paden van de downloadclients vertalen naar wat MediaGuard ziet — zonder vragen.

SABnzbd en qBittorrent draaien meestal als aparte TrueNAS-app met hun eigen mounts: ze melden
bijvoorbeeld ``/downloads/complete/tv`` terwijl MediaGuard dezelfde map als
``/data/downloads/complete/tv`` ziet. Zonder vertaling zou de opruimer niets vinden.

Volgorde:

1. **Remote path mappings** uit Sonarr/Radarr (``/api/v3/remotepathmapping``) — die heeft de
   gebruiker of de inrichting al vastgelegd. Alleen gebruikt als de lokale kant bij MediaGuard
   echt bestaat.
2. **Automatisch herkennen**: per gemelde map zoeken we onder MediaGuard's ``/data`` (en de
   ouders van de bibliotheekmappen) een map met dezelfde naam. Een kandidaat telt als
   ≥ 3 items die de client in die map meldt er ook staan, of als hij de enige is met het
   langste gelijke staartstuk van het pad (minstens twee mapnamen, bv. ``complete/tv``).
3. Lukt dat niet eenduidig, dan wordt die map overgeslagen ("pad niet herkend"); er wordt
   niets geraden en niets gevraagd.

Gevonden vertalingen worden onthouden en bij gebruik opnieuw gecontroleerd.
"""

from __future__ import annotations

import os
from collections.abc import Iterable
from pathlib import Path, PurePosixPath
from typing import Any

STATE_KEY = "download_janitor_path_map"
MIN_ITEM_MATCHES = 3
MAX_DEPTH = 5


def _norm(p: str) -> str:
    return str(PurePosixPath(p)).rstrip("/") or "/"


def apply(path: str | None, mapping: dict[str, str]) -> str | None:
    """Langste passende voorvoegsel vervangen; onbekend = ongewijzigd."""
    if not path:
        return path
    p = _norm(path)
    best = ""
    for remote in mapping:
        r = _norm(remote)
        if (p == r or p.startswith(r + "/")) and len(r) > len(best):
            best = r
    if not best:
        return path
    local = _norm(mapping[best])
    return local + p[len(best):]


def from_remote_path_mappings(rows: Iterable[dict[str, Any]], host: str) -> dict[str, str]:
    """Sonarr/Radarr-mappings voor deze client, alleen als de lokale kant bestaat."""
    out: dict[str, str] = {}
    for r in rows:
        if str(r.get("host") or "").lower() != host.lower():
            continue
        remote, local = str(r.get("remotePath") or ""), str(r.get("localPath") or "")
        if remote and local and Path(local).is_dir():
            out[_norm(remote)] = _norm(local)
    return out


def _candidates(name: str, bases: Iterable[Path]) -> list[Path]:
    found: list[Path] = []
    seen: set[str] = set()
    for base in bases:
        if not base.is_dir():
            continue
        start = len(base.parts)
        for root, dirs, _files in os.walk(base):
            depth = len(Path(root).parts) - start
            dirs[:] = [d for d in dirs if not d.startswith(".")] if depth < MAX_DEPTH else []
            if Path(root).name == name and root not in seen:
                seen.add(root)
                found.append(Path(root))
    return found


def _suffix_len(remote: str, local: Path) -> int:
    a, b = PurePosixPath(remote).parts[::-1], local.parts[::-1]
    n = 0
    for x, y in zip(a, b, strict=False):
        if x != y or x == "/":
            break
        n += 1
    return n


def detect(remote_dir: str, item_names: Iterable[str], bases: Iterable[Path]
           ) -> tuple[str | None, str]:
    """Zoek de lokale map voor een door de client gemelde map. (pad of None, uitleg)."""
    remote = _norm(remote_dir)
    if Path(remote).is_dir():
        return remote, "zelfde pad"
    name = PurePosixPath(remote).name
    if not name:
        return None, "pad niet herkend"
    cands = _candidates(name, bases)
    if not cands:
        return None, "pad niet herkend: geen map met deze naam bij MediaGuard"
    names = [n for n in item_names if n]
    scored = sorted(((sum((c / n).exists() for n in names), c) for c in cands),
                    key=lambda x: -x[0])
    if scored[0][0] >= MIN_ITEM_MATCHES and (len(scored) == 1 or scored[1][0] < scored[0][0]):
        return str(scored[0][1]), f"{scored[0][0]} items gevonden op beide plekken"
    by_suffix = sorted(((_suffix_len(remote, c), c) for c in cands), key=lambda x: -x[0])
    top = by_suffix[0][0]
    if top >= 2 and (len(by_suffix) == 1 or by_suffix[1][0] < top):
        return str(by_suffix[0][1]), "zelfde mappenstructuur"
    return None, "pad niet herkend: meerdere mogelijke mappen"
