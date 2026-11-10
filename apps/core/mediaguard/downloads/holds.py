"""Zoekpauzes per item (rapport 5, T4/T5) — zonder gebruiker.

Een item krijgt een pauze als zoeken op dit moment alleen kosten oplevert:
- dezelfde release is geweigerd ('geen verbetering') → upgrade-zoeken 7 dagen uit;
- downloads voor dit item verdwijnen steeds (3× binnen 7 dagen) → 24 uur niet grabben.

Backfill, levering (delivery) en het optimalisatieplan vragen ``hold_for`` voordat ze zoeken.
De pre-grab-hook vraagt ``grab_block``: zo geldt de pauze ook voor grabs die Sonarr/Radarr
zelf doen (RSS, eigen zoekacties). Sleutel: ``<instance_id>:<episode|movie>:<provider_id>``
(aflevering- of film-ID).
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from mediaguard.context import AppContext

STATE_KEY = "download_item_holds"
VANISH_KEY = "download_item_vanished"
VANISH_LIMIT = 3
VANISH_WINDOW_DAYS = 7
VANISH_HOLD_HOURS = 24
REFUSED_HOLD_DAYS = 7


def item_key(instance_id: str, kind: str, provider_id: Any) -> str:
    return f"{instance_id}:{'movie' if str(kind).startswith('movie') else 'episode'}:" \
           f"{int(provider_id)}"


def _now() -> datetime:
    return datetime.now(UTC)


def _parse(value: Any) -> datetime | None:
    try:
        at = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    except (TypeError, ValueError):
        return None
    return at if at.tzinfo else at.replace(tzinfo=UTC)


def set_hold(ctx: AppContext, key: str, *, hours: float, reason_nl: str, kind: str) -> str:
    until = (_now() + timedelta(hours=hours)).isoformat(timespec="seconds")
    data = dict(ctx.state_get(STATE_KEY, {}) or {})
    data[key] = {"until": until, "reason_nl": reason_nl, "kind": kind,
                 "since": _now().isoformat(timespec="seconds")}
    # verlopen pauzes opruimen
    data = {k: v for k, v in data.items() if (_parse(v.get("until")) or _now()) > _now()}
    ctx.state_set(STATE_KEY, data)
    return until


def hold_for(ctx: AppContext, key: str) -> dict[str, Any] | None:
    """Actieve pauze voor dit item, of None."""
    hit = (ctx.state_get(STATE_KEY, {}) or {}).get(key)
    if not hit:
        return None
    until = _parse(hit.get("until"))
    return dict(hit) if until and until > _now() else None


def active_holds(ctx: AppContext) -> list[dict[str, Any]]:
    out = []
    for key, v in (ctx.state_get(STATE_KEY, {}) or {}).items():
        until = _parse(v.get("until"))
        if until and until > _now():
            out.append({"key": key, **v})
    return sorted(out, key=lambda r: r["until"])


def record_vanished(ctx: AppContext, key: str, reason_nl: str) -> int:
    """Telt een verdwenen download voor dit item; bij ``VANISH_LIMIT`` binnen het venster
    volgt een pauze. Geeft het aantal binnen het venster."""
    cutoff = _now() - timedelta(days=VANISH_WINDOW_DAYS)
    data = dict(ctx.state_get(VANISH_KEY, {}) or {})
    hits = [h for h in data.get(key, []) if (_parse(h.get("at")) or _now()) >= cutoff]
    hits.append({"at": _now().isoformat(timespec="seconds"), "reason_nl": reason_nl})
    data[key] = hits[-20:]
    ctx.state_set(VANISH_KEY, data)
    return len(hits)


def vanished_for(ctx: AppContext, key: str) -> list[dict[str, Any]]:
    cutoff = _now() - timedelta(days=VANISH_WINDOW_DAYS)
    return [h for h in (ctx.state_get(VANISH_KEY, {}) or {}).get(key, [])
            if (_parse(h.get("at")) or _now()) >= cutoff]


def item_refs(body: dict[str, Any]) -> list[tuple[str, int]]:
    """(soort, ID) van de afleveringen of film in een pre-grab/pre-import-verzoek."""
    out: list[tuple[str, int]] = []
    for e in body.get("episodes") or []:
        if not isinstance(e, dict):
            continue
        v: Any = e.get("id") if e.get("id") is not None else (e.get("Id") or e.get("episodeId"))
        try:
            out.append(("episode", int(v)))
        except (TypeError, ValueError):
            continue
    movie = body.get("movie") or {}
    mid = movie.get("id") if isinstance(movie, dict) else None
    if mid is None:
        mid = body.get("movie_id") or body.get("movieId")
    try:
        if mid is not None:
            out.append(("movie", int(mid)))
    except (TypeError, ValueError):
        pass
    return out


def _has_file(ctx: AppContext, instance_id: str, kind: str, provider_id: int) -> bool:
    """Heeft het item volgens de bibliotheek al een bestand? Onbekend = nee."""
    if kind == "episode":
        sql = "SELECT has_file FROM lib_episodes WHERE instance_id=? AND provider_id=?"
    else:
        sql = "SELECT has_file FROM lib_items WHERE instance_id=? AND kind='movie' AND " \
              "provider_id=?"
    row = ctx.db.fetch_one(sql, (instance_id, provider_id))
    return bool(row and row["has_file"])


GRAB_BLOCK_CODE = "ITEM_PAUSED"


def grab_block(ctx: AppContext, instance_id: str, body: dict[str, Any]) -> str | None:
    """Tekst als een grab van Sonarr/Radarr zelf (RSS of eigen zoekactie) een zoekpauze
    raakt, anders None. Alleen lokale lookups: de pre-grab-hook moet snel antwoorden.

    - 'verdwenen' (downloads verdwijnen steeds): geen grab tot de pauze voorbij is;
    - 'geweigerd' (upgrade was geen verbetering): alleen als het item al een bestand heeft,
      zodat een ontbrekend item nooit wacht;
    - 'churn' regelt ``upgrade_churn.pre_grab_block`` met een eigen code.
    Een grab voor meerdere afleveringen (seizoenspakket) gaat alleen niet door als élke
    aflevering een pauze heeft; anders helpt hij de rest."""
    refs = item_refs(body)
    if not refs:
        return None
    data = ctx.state_get(STATE_KEY, {}) or {}
    if not data:
        return None
    now = _now()
    hits: list[dict[str, Any]] = []
    for kind, pid in refs:
        hit = data.get(item_key(instance_id, kind, pid))
        until = _parse((hit or {}).get("until"))
        if not hit or until is None or until <= now or hit.get("kind") == "churn":
            return None
        if hit.get("kind") == "refused" and not _has_file(ctx, instance_id, kind, pid):
            return None
        hits.append(hit)
    first = min(hits, key=lambda h: str(h.get("until") or ""))
    until_txt = str(first.get("until") or "")[:16].replace("T", " ")
    reason = str(first.get("reason_nl") or "").strip()
    return f"Zoekpauze voor dit item tot {until_txt} (UTC)" + (f": {reason}" if reason else "")


__all__ = ["GRAB_BLOCK_CODE", "active_holds", "grab_block", "hold_for", "item_key",
           "item_refs", "record_vanished", "set_hold", "vanished_for"]
