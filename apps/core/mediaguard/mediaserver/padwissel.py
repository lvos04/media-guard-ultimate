"""Padwissel bij een AV1-vervanging (bijvoorbeeld ``.mp4`` naar ``.mkv``) met behoud van
kijkgegevens (S-P3-676, besluit van de gebruiker 2026-11-21).

shortcut: padwissel uit in versie 1 (Jellyfin verliest kijkgegevens), zie VERVOLGPUNTEN. De
executor roept ``record`` niet aan zolang ``executor.PADWISSEL_MET_PROVIDER_ID`` False is; dan
wordt elke padwissel met een bekende mediaserver uitgesteld (PATH_CHANGE).

Emby en Jellyfin bewaren gezien-status, hervatpunt en favoriet op de provider-ID van een item
(Imdb, Tmdb, Tvdb), niet op het pad. Een padwissel mag daarom alleen als **elke** bekende
mediaserver het item kent met een provider-ID. MediaGuard leest alleen; het schrijft nooit
kijkgegevens en raakt nooit de database van een mediaserver.

1. ``record`` (vóór het veiligstellen, in de transactie): per server het item en per gebruiker
   gezien, hervatpunt en favoriet vastleggen in het journaal. Lukt dat niet volledig, dan blijft
   het uitstel ``PATH_CHANGE`` (bron blijft staan, encode wacht in staging).
2. ``verify`` (elke mediaserverronde na de overdracht): het nieuwe item op het nieuwe pad
   zoeken, dezelfde provider-ID eisen en de kijkgegevens per gebruiker vergelijken. Wijkt iets
   af of is het item niet te vinden, dan een zichtbare melding met de vastgelegde waarden;
   MediaGuard blijft het opnieuw proberen.
3. ``release_state``: zolang de controle niet slaagde, slaagt de vrijgavecontrole niet en wordt
   het origineel in quarantaine nooit opgeruimd.
"""

from __future__ import annotations

from datetime import datetime, timedelta
from pathlib import Path
from typing import TYPE_CHECKING, Any

from mediaguard.db import loads, utcnow
from mediaguard.mediaserver import paths as P
from mediaguard.mediaserver import servers as M
from mediaguard.mediaserver.clients import EmbyClient, MediaServerError

if TYPE_CHECKING:
    from mediaguard.context import AppContext

SNAPSHOT_STEP = "mediaserver_kijkgegevens"
CHECK_STEP = "mediaserver_controle"
PROVIDERS = ("imdb", "tmdb", "tvdb")
#: na zoveel mislukte controles een melding (eerst krijgt de server tijd om te scannen)
NOTIFY_AFTER = 3
FAST_ATTEMPTS, FAST_MIN, SLOW_HOURS = 6, 10, 6


class Refused(Exception):
    """Padwissel niet toegestaan; de tekst zegt waarom (Nederlands)."""


def _providers(item: dict[str, Any]) -> dict[str, str]:
    return {str(k).lower(): str(v) for k, v in (item.get("ProviderIds") or {}).items()
            if str(k).lower() in PROVIDERS and v}


def _watch(data: dict[str, Any]) -> dict[str, Any]:
    return {"played": bool(data.get("Played")),
            "position": int(data.get("PlaybackPositionTicks") or 0),
            "favorite": bool(data.get("IsFavorite"))}


def _ticks_nl(ticks: int) -> str:
    s = ticks // 10_000_000
    return f"{s // 3600}:{s // 60 % 60:02d}:{s % 60:02d}"


def values_nl(user: dict[str, Any]) -> str:
    return (f"{user['name']}: gezien {'ja' if user['played'] else 'nee'}, hervatpunt "
            f"{_ticks_nl(int(user['position']))}, favoriet {'ja' if user['favorite'] else 'nee'}")


async def _locate(ctx: AppContext, srv: M.ServerConf, mg_path: str
                  ) -> tuple[EmbyClient, dict[str, Any] | None, list[dict[str, Any]]]:
    from mediaguard.mediaserver import service as S

    client = M.client(ctx, srv)
    if not isinstance(client, EmbyClient):
        raise Refused(f"{srv.label}: kijkgegevens zijn hier niet te controleren")
    spath = P.to_server(mg_path, await S.path_map(ctx, client, srv, strict=True))
    if spath is None:
        raise Refused(f"{srv.label}: de bibliotheekmap is niet gekoppeld")
    users = await client.users()
    admin = next((u for u in users if (u.get("Policy") or {}).get("IsAdministrator")),
                 users[0] if users else None)
    item = await client.item_by_path(spath, S._search_term(mg_path),
                                     str(admin["Id"]) if admin else None)
    return client, item, users


async def record(ctx: AppContext, txn_id: int, source: str, target: str) -> None:
    """Kijkgegevens per server en gebruiker vastleggen (alleen lezen); ``Refused`` als een
    server het item niet met een provider-ID kent of iets niet te lezen is."""
    from mediaguard.recovery.executor import journal

    snap: list[dict[str, Any]] = []
    for srv in M.all_servers(ctx):
        if not srv.followed:
            raise Refused(f"{srv.label} is bekend maar niet actief gekoppeld")
        try:
            client, item, users = await _locate(ctx, srv, source)
            if item is None:
                raise Refused(f"{srv.label} kent dit bestand niet")
            ids = _providers(item)
            if not ids:
                raise Refused(f"{srv.label}: het item heeft geen provider-ID (Imdb, Tmdb of "
                              "Tvdb)")
            seen: dict[str, Any] = {}
            for u in users:
                data = await client.user_data(str(item["Id"]), str(u["Id"]))
                name = str(u.get("Name") or u["Id"])
                if data is None:
                    raise Refused(f"{srv.label}: kijkgegevens van {name} niet te lezen")
                seen[str(u["Id"])] = {"name": name, **_watch(data)}
        except MediaServerError as exc:
            raise Refused(f"{srv.label}: {exc}") from exc
        snap.append({"server_id": srv.id, "label": srv.label, "item_id": str(item["Id"]),
                     "provider_ids": ids, "users": seen})
    journal(ctx, txn_id, SNAPSHOT_STEP, "done", source=source, target=target, servers=snap)


def snapshot(ctx: AppContext, txn_id: int) -> dict[str, Any] | None:
    row = ctx.db.fetch_one("SELECT detail_json FROM replacement_journal WHERE transaction_id=? "
                           "AND step=? AND phase='done' ORDER BY id DESC LIMIT 1",
                           (txn_id, SNAPSHOT_STEP))
    return loads(row["detail_json"], {}) if row else None


def _attempts(ctx: AppContext, txn_id: int) -> list[dict[str, Any]]:
    return ctx.db.fetch_dicts("SELECT phase, ts, detail_json FROM replacement_journal WHERE "
                              "transaction_id=? AND step=? ORDER BY id", (txn_id, CHECK_STEP))


def _enabled() -> bool:
    """Padwissel aan (versie 2). Uit: geen nacontrole en geen vrijgave-uitstel, ook niet
    voor een vastlegging uit een eerdere build (R-HARDE-EIS-11): gedrag van vóór 676."""
    from mediaguard.recovery import executor

    return executor.PADWISSEL_MET_PROVIDER_ID


def release_state(ctx: AppContext, txn_id: int) -> tuple[str, str] | None:
    """None zonder padwissel; anders ("passed"|"pending"|"failed", uitleg)."""
    if not _enabled() or snapshot(ctx, txn_id) is None:
        return None
    rows = _attempts(ctx, txn_id)
    if any(r["phase"] == "done" for r in rows):
        return "passed", "kijkgegevens na de padwissel gelijk op elke mediaserver"
    if len(rows) < NOTIFY_AFTER:
        return "pending", "kijkgegevens na de padwissel nog niet gecontroleerd"
    return "failed", str(loads(rows[-1]["detail_json"], {}).get("message") or "")


async def _compare(ctx: AppContext, srv_snap: dict[str, Any], target: str) -> list[str]:
    """Wat er afwijkt op één server (leeg = alles gelijk)."""
    srv = M.get(ctx, int(srv_snap["server_id"]))
    label = str(srv_snap["label"])
    if srv is None or not srv.followed:
        return [f"{label} is niet meer gekoppeld; controle niet mogelijk"]
    try:
        client, item, _ = await _locate(ctx, srv, target)
        if item is None:
            return [f"{label}: het nieuwe item is (nog) niet gevonden"]
        old = srv_snap["provider_ids"]
        if not any(old.get(k) == v for k, v in _providers(item).items()):
            return [f"{label}: het nieuwe item heeft een andere provider-ID"]
        diffs = []
        for uid, before in srv_snap["users"].items():
            data = await client.user_data(str(item["Id"]), uid)
            if data is None:
                diffs.append(f"{label}: kijkgegevens van {before['name']} niet te lezen")
            elif _watch(data) != {k: before[k] for k in ("played", "position", "favorite")}:
                diffs.append(f"{label}: {values_nl({'name': before['name'], **_watch(data)})}")
        return diffs
    except (Refused, MediaServerError) as exc:
        return [f"{label}: {exc}"]


def _due(rows: list[dict[str, Any]], now: datetime) -> bool:
    if not rows:
        return True
    wait = timedelta(minutes=FAST_MIN) if len(rows) < FAST_ATTEMPTS else \
        timedelta(hours=SLOW_HOURS)
    return datetime.fromisoformat(str(rows[-1]["ts"])) + wait <= now


async def verify(ctx: AppContext) -> int:
    """Open padwissels controleren; geeft het aantal geslaagde controles."""
    if not _enabled():
        return 0
    from mediaguard.api.system import notify
    from mediaguard.mediaserver import service as S
    from mediaguard.recovery.executor import journal

    txns = ctx.db.fetch_dicts(
        "SELECT t.id, t.final_path FROM replacement_transactions t WHERE t.state='committed' "
        "AND EXISTS (SELECT 1 FROM replacement_journal j WHERE j.transaction_id=t.id AND "
        "j.step=? AND j.phase='done') AND NOT EXISTS (SELECT 1 FROM replacement_journal j "
        "WHERE j.transaction_id=t.id AND j.step=? AND j.phase='done') ORDER BY t.id LIMIT 20",
        (SNAPSHOT_STEP, CHECK_STEP))
    passed = 0
    for t in txns:
        rows = _attempts(ctx, t["id"])
        if not _due(rows, S.now_utc()):
            continue
        snap = snapshot(ctx, t["id"]) or {}
        servers = snap.get("servers") or []
        diffs = [d for s in servers for d in await _compare(ctx, s, t["final_path"])]
        name = Path(t["final_path"]).name
        if not diffs:
            journal(ctx, t["id"], CHECK_STEP, "done", at=utcnow())
            S.log_action(ctx, "path_change", f"{name}: kijkgegevens na de padwissel gelijk op "
                         "elke mediaserver", path=t["final_path"], txn=t["id"])
            passed += 1
            continue
        recorded = "; ".join(f"{s['label']}: " + ", ".join(
            values_nl(u) for u in s["users"].values()) for s in servers)
        message = (f"Afwijking: {'; '.join(diffs)}. Vastgelegd vóór de vervanging: "
                   f"{recorded or 'geen gebruikers'}. Het origineel blijft in quarantaine.")
        journal(ctx, t["id"], CHECK_STEP, "error", message=message[:2000])
        S.log_action(ctx, "path_change", f"{name}: {message}", path=t["final_path"],
                     txn=t["id"], ok=False)
        if len(rows) + 1 == NOTIFY_AFTER:
            notify(ctx, "warning", f"Kijkgegevens na padwissel niet bevestigd: {name}",
                   message + " Controleer het item in de mediaserver of zet het origineel "
                   "terug; MediaGuard blijft controleren.",
                   link="/quarantaine", dedupe_key=f"mediaserver.padwissel.{t['id']}")
    return passed
