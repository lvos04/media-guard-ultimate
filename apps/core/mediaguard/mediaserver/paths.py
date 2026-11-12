"""Paden vertalen tussen MediaGuard (bv. ``/data/Media/Films``) en de mediaserver
(bv. ``/mnt/voorbeeldpool/Media/Films`` of ``/media/Films``).

Koppeling op het langste gelijke staartstuk van de mapnaam (hoofdletterongevoelig als het
anders niet eenduidig is). Een MediaGuard-map zonder eenduidige tegenhanger wordt niet
vertaald; de aanroeper valt dan terug op een volledige bibliotheekscan.
"""

from __future__ import annotations

from collections.abc import Iterable

Mapping = list[tuple[str, str]]   # (mediaguard_root, server_root)


def _parts(path: str) -> list[str]:
    return [p for p in path.replace("\\", "/").rstrip("/").split("/") if p]


def _tail_match(a: list[str], b: list[str], fold: bool) -> int:
    n = 0
    while n < min(len(a), len(b)):
        x, y = a[-1 - n], b[-1 - n]
        if (x.lower() == y.lower()) if fold else (x == y):
            n += 1
        else:
            break
    return n


def build_map(mg_roots: Iterable[str], server_roots: Iterable[str]) -> Mapping:
    servers = sorted({s.rstrip("/") for s in server_roots if s})
    out: Mapping = []
    for root in sorted({r.rstrip("/") for r in mg_roots if r}):
        if root in servers:
            out.append((root, root))
            continue
        mine = _parts(root)
        for fold in (False, True):
            scored = [(_tail_match(mine, _parts(s), fold), s) for s in servers]
            best = max((n for n, _ in scored), default=0)
            winners = [s for n, s in scored if n == best and n > 0]
            if len(winners) == 1:
                out.append((root, winners[0]))
                break
    return out


def _convert(path: str, mapping: Mapping, src: int) -> str | None:
    best: tuple[int, str] | None = None
    for pair in mapping:
        frm, to = pair[src], pair[1 - src]
        if (path == frm or path.startswith(frm + "/")) and (best is None
                                                              or len(frm) > best[0]):
            best = (len(frm), to + path[len(frm):])
    return best[1] if best else None


def to_server(path: str, mapping: Mapping) -> str | None:
    return _convert(path, mapping, 0)


def to_mediaguard(path: str, mapping: Mapping) -> str | None:
    return _convert(path, mapping, 1)
