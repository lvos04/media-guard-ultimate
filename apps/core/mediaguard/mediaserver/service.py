"""Mediaserver-koppeling (Emby eerst; Jellyfin en Plex werken ook) — rapport 5, M3.

Wat het doet, allemaal automatisch zodra er een API-sleutel is:

a. na elke AV1-overdracht of terugzetting: de mediaserver dat ene bestand laten verversen
   (anders een volledige scan, hooguit eens per 30 min);
b. afspeeltest: ±10 min later vraagt MediaGuard de server of een gangbare tv het nieuwe
   bestand kan afspelen (direct of via omzetten). Mislukt → melding (of terugzetten als dat
   zo is ingesteld);
c. kijkt iemand, dan wachten nieuwe AV1-encodes (lopende draaien met lage prioriteit door);
d. wat de laatste 24 uur bekeken is, wordt niet omgezet.

Meerdere servers tegelijk (Δ-MG-DUAL, C1.3-MGU-04): a–d gebeuren **per gevolgde server**
(``servers.followed``): Emby én Jelly Leon. Kijkt iemand op één ervan, dan wachten nieuwe
encodes; een onbereikbare server maakt de pauze van een andere niet ongedaan, en een fout bij de
ene server stopt het werk voor de andere nooit. Server 1 is de bestaande koppeling van 1.2.2
(zie ``servers``); met alleen die server is het gedrag precies als in 1.2.2.
"""

from __future__ import annotations

import asyncio
import contextlib
import json
import logging
import os
import socket
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import TYPE_CHECKING, Any

from mediaguard.audit import audit
from mediaguard.db import loads, utcnow
from mediaguard.mediaserver import paths as P
from mediaguard.mediaserver import servers as M
from mediaguard.mediaserver.clients import (
    EmbyClient,
    MediaServerError,
    PlaybackResult,
    Server,
    make_server,
)

if TYPE_CHECKING:
    from mediaguard.context import AppContext

LOGGER = logging.getLogger(__name__)
SECRET_KEY = "mediaserver.api_key"
KINDS = ("auto", "emby", "jellyfin", "plex", "off")
LABEL = {"emby": "Emby", "jellyfin": "Jellyfin", "plex": "Plex"}
TEST_DELAY_MIN = 10
FULL_REFRESH_GAP_MIN = 30
HOLD_LANES = {"both": ["cpu", "gpu"], "gpu": ["gpu"], "off": []}
#: zo lang telt de laatst bekende kijkstand van een server die niet meer antwoordt (alleen bij
#: meer dan één gevolgde server; met één server blijft het gedrag van 1.2.2)
HOLD_STALE_MIN = 10
# Gangbare poorten (standaard + TrueNAS-catalogus) per soort.
PORTS = {"emby": (8096, 9096, 30092), "jellyfin": (8096, 30013), "plex": (32400,)}


def _v(ctx: AppContext, key: str) -> Any:
    return ctx.config.value(f"mediaserver.{key}")


def now_utc() -> datetime:
    return datetime.now(UTC)


def log_action(ctx: AppContext, kind: str, detail: str, *, path: str | None = None,
               txn: int | None = None, ok: bool = True, server_id: int | None = None) -> None:
    ctx.db.execute(
        "INSERT INTO mediaserver_actions(ts,kind,path,transaction_id,ok,detail_nl,server_id) "
        "VALUES (?,?,?,?,?,?,?)",
        (utcnow(), kind, path, txn, 1 if ok else 0, detail[:500], server_id))
    ctx.db.execute("DELETE FROM mediaserver_actions WHERE id NOT IN (SELECT id FROM "
                   "mediaserver_actions ORDER BY id DESC LIMIT 500)")


# -- configuratie ------------------------------------------------------------------------------
def configured(ctx: AppContext) -> dict[str, Any]:
    """Effectieve soort + adres: wat de gebruiker invulde, anders wat ontdekt is."""
    kind = str(_v(ctx, "kind") or "auto")
    url = str(_v(ctx, "url") or "").strip()
    found = (ctx.state_get("mediaserver_discovery", {}) or {}).get("servers") or []
    source = "ingesteld"
    if kind == "off":
        return {"kind": "off", "url": "", "source": "uit", "key_set": ctx.secrets.has(SECRET_KEY)}
    if not url and found:
        pick = next((s for s in found if kind in ("auto", s["kind"])), None)
        if pick:
            url, source = pick["url"], "automatisch gevonden"
            if kind == "auto":
                kind = pick["kind"]
    if kind == "auto":
        kind = (ctx.state_get("mediaserver_detected_kind") or "emby")
    return {"kind": kind, "url": url, "source": source if url else "niet gevonden",
            "key_set": ctx.secrets.has(SECRET_KEY)}


def server(ctx: AppContext) -> Server | None:
    cfg = configured(ctx)
    if cfg["kind"] == "off" or not cfg["url"] or not cfg["key_set"]:
        return None
    return make_server(cfg["kind"], ctx.services, cfg["url"], ctx.secrets.get(SECRET_KEY))


async def test_connection(ctx: AppContext, server_id: int | None = None) -> dict[str, Any]:
    """Verbinding testen: server 1 (de bestaande koppeling, standaard) of een extra server."""
    if server_id is not None and server_id != M.LEGACY_ID:
        return await _test_extra(ctx, server_id)
    result = await _test_legacy(ctx)
    first = M.legacy(ctx)
    if first is not None and first.followed:
        M.mark_contact(ctx, first, bool(result.get("ok")), result.get("message_nl"))
    return result


async def _test_extra(ctx: AppContext, server_id: int) -> dict[str, Any]:
    srv = M.get(ctx, server_id)
    if srv is None or srv.legacy:
        return {"ok": False, "message_nl": "Deze mediaserver bestaat niet (meer)."}
    client = M.client(ctx, srv)
    try:
        info = await client.info()
        if isinstance(client, EmbyClient) and info.get("kind") and info["kind"] != srv.kind:
            M.mark_contact(ctx, srv, False, "verkeerde soort")
            return {"ok": False, "server": info, "message_nl": _wrong_kind(info["kind"], srv.kind)}
        if not srv.key_set:
            return {"ok": False, "server": info,
                    "message_nl": f"{srv.label} gevonden ({info.get('name') or srv.url}); "
                                  "vul nog de API-sleutel in."}
        await client.check_key()
        locs = await client.locations()
    except MediaServerError as exc:
        M.mark_contact(ctx, srv, False, str(exc))
        return {"ok": False, "message_nl": f"{srv.label}: {exc}"}
    mapping = P.build_map(mg_roots(ctx), locs)
    M.sset(ctx, srv, "path_map", {"map": mapping, "at": utcnow(), "server": locs})
    M.sset(ctx, srv, "connected", {"at": utcnow(), **info, "kind": srv.kind})
    M.mark_contact(ctx, srv, True)
    return {"ok": True, "server": info, "path_map": mapping,
            "message_nl": f"Verbonden met {srv.label} {info.get('version') or ''}".strip()
                          + f" · {len(mapping)} bibliotheekmap(pen) gekoppeld"}


def _wrong_kind(found: str, chosen: str) -> str:
    return (f"Op dit adres draait {LABEL.get(found, found)}, niet {LABEL.get(chosen, chosen)}: "
            "kies de juiste soort.")


async def _test_legacy(ctx: AppContext) -> dict[str, Any]:
    cfg = configured(ctx)
    if cfg["kind"] == "off":
        return {"ok": False, "message_nl": "Mediaserver-koppeling staat uit."}
    if not cfg["url"]:
        return {"ok": False, "message_nl": "Geen server gevonden; vul het adres in."}
    client = make_server(cfg["kind"], ctx.services, cfg["url"], ctx.secrets.get(SECRET_KEY))
    try:
        info = await client.info()
        if isinstance(client, EmbyClient) and info.get("kind") and info["kind"] != client.kind:
            if str(_v(ctx, "kind")) == "auto":
                ctx.state_set("mediaserver_detected_kind", info["kind"])
                client = make_server(info["kind"], ctx.services, cfg["url"],
                                     ctx.secrets.get(SECRET_KEY))
            elif client.kind == "emby":
                # Jellyfin 12.x achter de keuze "Emby": elke route onder /emby geeft 404. Een
                # Emby-server achter de keuze "Jellyfin" werkt wél (gemeten op 4.10.2.0) en
                # blijft dus ongemoeid: een bestaande koppeling valt hier nooit door weg.
                return {"ok": False, "server": info,
                        "message_nl": _wrong_kind(info["kind"], client.kind)}
        if not cfg["key_set"]:
            return {"ok": False, "server": info,
                    "message_nl": f"{client.label} gevonden ({info.get('name') or cfg['url']}); "
                                  "vul nog de API-sleutel in."}
        await client.check_key()
        locs = await client.locations()
    except MediaServerError as exc:
        return {"ok": False, "message_nl": f"{client.label}: {exc}"}
    mapping = P.build_map(mg_roots(ctx), locs)
    ctx.state_set("mediaserver_path_map", {"map": mapping, "at": utcnow(), "server": locs})
    ctx.state_set("mediaserver_connected", {"at": utcnow(), **info, "kind": client.kind})
    mapped = len(mapping)
    return {"ok": True, "server": info, "path_map": mapping,
            "message_nl": f"Verbonden met {client.label} {info.get('version') or ''}".strip()
                          + f" · {mapped} bibliotheekmap(pen) gekoppeld"}


def mg_roots(ctx: AppContext) -> list[str]:
    rows = ctx.db.fetch_all("SELECT DISTINCT root_folder_path AS r FROM lib_items "
                            "WHERE root_folder_path IS NOT NULL AND root_folder_path!=''")
    return sorted({str(r["r"]).rstrip("/") for r in rows} | set(ctx.settings.library_roots))


async def path_map(ctx: AppContext, client: Server,
                   server: M.ServerConf | int = M.LEGACY_ID) -> P.Mapping:
    cached = M.sget(ctx, server, "path_map", {}) or {}
    at = cached.get("at")
    if at and (now_utc() - datetime.fromisoformat(at)) < timedelta(hours=1) \
            and cached.get("map"):
        return [tuple(p) for p in cached["map"]]  # type: ignore[misc]
    try:
        locs = await client.locations()
    except MediaServerError:
        return [tuple(p) for p in cached.get("map") or []]  # type: ignore[misc]
    mapping = P.build_map(mg_roots(ctx), locs)
    M.sset(ctx, server, "path_map", {"map": mapping, "at": utcnow(), "server": locs})
    return mapping


# -- ontdekken -------------------------------------------------------------------------------
def candidate_hosts() -> list[str]:
    hosts = [os.environ.get("MG_PUBLIC_HOST", ""), os.environ.get("MG_AIO_LAN_IP", ""),
             "127.0.0.1", "host.docker.internal", "emby", "jellyfin", "plex"]
    return [h for i, h in enumerate(hosts) if h and h not in hosts[:i]]


def _udp_discover(timeout: float = 2.0) -> list[dict[str, Any]]:
    """Emby/Jellyfin antwoorden op een broadcast op poort 7359 met hun adres."""
    found: list[dict[str, Any]] = []
    with contextlib.suppress(OSError), socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as s:
        s.setsockopt(socket.SOL_SOCKET, socket.SO_BROADCAST, 1)
        s.settimeout(timeout)
        for msg in (b"who is EmbyServer?", b"who is JellyfinServer?"):
            with contextlib.suppress(OSError):
                s.sendto(msg, ("255.255.255.255", 7359))
        end = now_utc() + timedelta(seconds=timeout)
        while now_utc() < end:
            try:
                data, _addr = s.recvfrom(4096)
            except OSError:
                break
            with contextlib.suppress(ValueError):
                info = json.loads(data.decode("utf-8", "replace"))
                if info.get("Address"):
                    found.append({"url": str(info["Address"]).rstrip("/"),
                                  "name": info.get("Name")})
    return found


async def discover(ctx: AppContext, *, hosts: list[str] | None = None,
                   udp: bool = True) -> dict[str, Any]:
    """Zoek Emby/Jellyfin/Plex en Bazarr op het LAN. Alleen lezen, geen sleutels nodig."""
    from mediaguard.mediaserver.clients import BazarrClient, PlexClient

    urls: list[tuple[str, str]] = []
    if udp:
        for f in await asyncio.to_thread(_udp_discover):
            urls.append(("emby", f["url"]))
    for host in hosts if hosts is not None else candidate_hosts():
        for kind in ("emby", "jellyfin", "plex"):
            urls += [(kind, f"http://{host}:{port}") for port in PORTS[kind]]
    servers: list[dict[str, Any]] = []
    seen: set[str] = set()
    for kind, url in urls:
        key = url.rstrip("/").removesuffix("/emby")
        if key in seen:
            continue
        # ``info`` probeert eerst /emby en dan zonder (Jellyfin 12.x) en meldt de echte soort
        client: Server = PlexClient(ctx.services, url, None) if kind == "plex" else \
            EmbyClient(ctx.services, url, None, "emby")
        try:
            info = await client.info()
        except MediaServerError:
            continue
        seen.add(key)
        real_kind = info.get("kind") or kind
        servers.append({"kind": real_kind, "url": key, "name": info.get("name"),
                        "version": info.get("version"), "label": LABEL.get(real_kind, real_kind)})
    # Emby eerst (hoofddoel), dan Jellyfin, dan Plex
    servers.sort(key=lambda s: ("emby", "jellyfin", "plex").index(s["kind"]))
    bazarr: list[dict[str, Any]] = []
    for host in hosts if hosts is not None else candidate_hosts():
        for port in (6767, 30046):
            url = f"http://{host}:{port}"
            try:
                await BazarrClient(ctx.services, url, None).info()
            except MediaServerError as exc:
                if "sleutel" not in str(exc):  # 401 = Bazarr staat er wél
                    continue
            bazarr.append({"url": url})
    result = {"at": utcnow(), "servers": servers, "bazarr": bazarr}
    ctx.state_set("mediaserver_discovery", result)
    log_action(ctx, "discover", f"{len(servers)} mediaserver(s), {len(bazarr)} Bazarr gevonden")
    return result


# -- (a) verversen na overdracht + (b) afspeeltest ---------------------------------------------
def collect_handoffs(ctx: AppContext, servers: list[M.ServerConf] | None = None) -> int:
    """Nieuwe afgeronde overdrachten/terugzettingen in de wachtrij zetten, met een taak per
    gevolgde server (verversen, afspeeltest, NL-ondertitels)."""
    servers = M.followed(ctx) if servers is None else servers
    cursor = ctx.state_get("mediaserver_txn_cursor")
    if cursor is None:
        row = ctx.db.fetch_one("SELECT COALESCE(MAX(id),0) AS m FROM replacement_transactions")
        ctx.state_set("mediaserver_txn_cursor", int(row["m"]) if row else 0)
        return 0
    rows = ctx.db.fetch_dicts(
        "SELECT id, kind, final_path, encoding_job_id FROM replacement_transactions "
        "WHERE id>? AND state='committed' ORDER BY id LIMIT 200", (int(cursor),))
    for r in rows:
        test_after = None
        # afspeeltest alleen voor een AV1-versie; een verkeerd bestand dat MediaGuard in
        # quarantaine zette heeft geen nieuw bestand om af te spelen (verbeterlijst 12)
        if r["kind"] == "replace" and r["encoding_job_id"] is not None and \
                bool(_v(ctx, "playback_test")):
            test_after = (now_utc() + timedelta(minutes=TEST_DELAY_MIN)).isoformat(
                timespec="seconds")
        ctx.db.execute(
            "INSERT OR IGNORE INTO mediaserver_pending(transaction_id,path,kind,encoding_job_id,"
            "test_after,created_at) VALUES (?,?,?,?,?,?)",
            (r["id"], r["final_path"], r["kind"], r["encoding_job_id"], test_after, utcnow()))
        for srv in servers:
            ctx.db.execute(
                "INSERT OR IGNORE INTO mediaserver_server_results(transaction_id,server_id,"
                "test_after,created_at) VALUES (?,?,?,?)", (r["id"], srv.id, test_after, utcnow()))
    # nog niet afgeronde transacties niet overslaan: cursor tot vóór de eerste open
    open_row = ctx.db.fetch_one(
        "SELECT MIN(id) AS m FROM replacement_transactions WHERE id>? AND state NOT IN "
        "('committed','rolled_back','aborted','conflict')", (int(cursor),))
    top = ctx.db.fetch_one("SELECT COALESCE(MAX(id),0) AS m FROM replacement_transactions")
    new_cursor = int(top["m"]) if top else int(cursor)
    if open_row and open_row["m"] is not None:
        new_cursor = min(new_cursor, int(open_row["m"]) - 1)
    ctx.state_set("mediaserver_txn_cursor", max(int(cursor), new_cursor))
    return len(rows)


def ensure_legacy_rows(ctx: AppContext, servers: list[M.ServerConf]) -> None:
    """Upgrade van 1.2.2: open overdrachten van vóór deze versie hebben nog geen taak per server.
    Server 1 (de bestaande koppeling) neemt ze over met de stand uit ``mediaserver_pending``
    (al ververst, wachttijd en pogingen van de afspeeltest), zodat niets dubbel of niet gebeurt."""
    if not any(s.legacy for s in servers):
        return
    ctx.db.execute(
        "INSERT OR IGNORE INTO mediaserver_server_results(transaction_id,server_id,refreshed_at,"
        "test_after,attempts,result,detail_nl,done_at,created_at) "
        "SELECT p.transaction_id, ?, p.refreshed_at, p.test_after, p.attempts, p.result, "
        "p.detail_nl, p.done_at, p.created_at FROM mediaserver_pending p WHERE p.done_at IS NULL "
        "AND NOT EXISTS (SELECT 1 FROM mediaserver_server_results r WHERE "
        "r.transaction_id=p.transaction_id)", (M.LEGACY_ID,))


async def refresh_pending(ctx: AppContext, client: Server,
                          server: M.ServerConf | None = None) -> int:
    sid = server.id if server else M.LEGACY_ID
    if not bool(_v(ctx, "refresh_after_handoff")):
        ctx.db.execute("UPDATE mediaserver_server_results SET refreshed_at=? WHERE "
                       "refreshed_at IS NULL AND server_id=?", (utcnow(), sid))
        return 0
    rows = ctx.db.fetch_dicts(
        "SELECT r.transaction_id, p.path, p.kind FROM mediaserver_server_results r JOIN "
        "mediaserver_pending p ON p.transaction_id=r.transaction_id WHERE r.server_id=? AND "
        "r.refreshed_at IS NULL AND r.done_at IS NULL ORDER BY r.transaction_id LIMIT 100", (sid,))
    if not rows:
        return 0
    mapping = await path_map(ctx, client, sid)
    mapped = [(r, P.to_server(r["path"], mapping)) for r in rows]
    exact = [p for _, p in mapped if p]
    try:
        if exact:
            await client.refresh_paths(exact)
        if any(p is None for _, p in mapped):
            last = M.sget(ctx, sid, "full_refresh_at")
            if not last or now_utc() - datetime.fromisoformat(last) > timedelta(
                    minutes=FULL_REFRESH_GAP_MIN):
                await client.refresh_all()
                M.sset(ctx, sid, "full_refresh_at", utcnow())
    except MediaServerError as exc:
        log_action(ctx, "refresh", f"{client.label}: verversen mislukt — {exc}", ok=False,
                   server_id=sid)
        return 0
    for r, p in mapped:
        ctx.db.execute("UPDATE mediaserver_server_results SET refreshed_at=? WHERE "
                       "transaction_id=? AND server_id=?", (utcnow(), r["transaction_id"], sid))
        what = "teruggezet origineel" if r["kind"] == "restore" else "AV1-versie"
        log_action(ctx, "refresh", f"{client.label} ververst: {Path(r['path']).name} ({what})"
                   + ("" if p else " — volledige scan, map niet gekoppeld"),
                   path=r["path"], txn=r["transaction_id"], server_id=sid)
    return len(rows)


def _search_term(path: str) -> str:
    """Titel uit de map (film) of seriemap: 'Stormvogel (2017) [imdb-…]' → 'Stormvogel'."""
    import re

    parts = Path(path).parts
    folder = parts[-2] if len(parts) > 1 else Path(path).stem
    if re.match(r"(?i)^(season|seizoen|specials)\b", folder) and len(parts) > 2:
        folder = parts[-3]
    title = re.sub(r"\s*[\(\[].*$", "", folder).strip()
    return title or Path(path).stem


async def run_playback_tests(ctx: AppContext, client: Server,
                             server: M.ServerConf | None = None) -> int:
    sid = server.id if server else M.LEGACY_ID
    due = ctx.db.fetch_dicts(
        "SELECT r.transaction_id, r.attempts, p.path, p.encoding_job_id FROM "
        "mediaserver_server_results r JOIN mediaserver_pending p ON "
        "p.transaction_id=r.transaction_id WHERE r.server_id=? AND r.done_at IS NULL AND "
        "r.test_after IS NOT NULL AND r.test_after<=? AND r.refreshed_at IS NOT NULL "
        "ORDER BY r.test_after LIMIT 5", (sid, utcnow()))
    mapping = await path_map(ctx, client, sid) if due else []
    for r in due:
        server_path = P.to_server(r["path"], mapping) or r["path"]
        try:
            res = await client.playback_test(server_path, _search_term(r["path"]))
        except MediaServerError as exc:
            res = PlaybackResult("failed" if r["attempts"] >= 2 else "retry",
                                 f"{client.label}: {exc}")
        attempts = int(r["attempts"]) + 1
        if res.result in ("not_found", "retry") and attempts < 3:
            ctx.db.execute(
                "UPDATE mediaserver_server_results SET attempts=?, test_after=?, detail_nl=? "
                "WHERE transaction_id=? AND server_id=?",
                (attempts, (now_utc() + timedelta(minutes=TEST_DELAY_MIN)).isoformat(
                    timespec="seconds"), res.detail_nl, r["transaction_id"], sid))
            continue
        final = "not_found" if res.result == "retry" else res.result
        subs = nl_subtitles_outcome(ctx, r["path"], final, res)
        ctx.db.execute("UPDATE mediaserver_server_results SET attempts=?, result=?, detail_nl=?, "
                       "nl_subtitles=?, done_at=? WHERE transaction_id=? AND server_id=?",
                       (attempts, final, res.detail_nl, subs, utcnow(), r["transaction_id"], sid))
        ok = final in ("ok", "transcode", "skipped")
        log_action(ctx, "playback_test", f"{client.label} · {Path(r['path']).name}: "
                   f"{res.detail_nl}" if server and not server.legacy else
                   f"{Path(r['path']).name}: {res.detail_nl}",
                   path=r["path"], txn=r["transaction_id"], ok=ok or final == "not_found",
                   server_id=sid)
        if subs == "missing":
            _nl_subtitles_missing(ctx, r, client.label, sid)
        if final == "failed":
            await _playback_failed(ctx, r, res, client.label)
    return len(due)


def nl_subtitles_outcome(ctx: AppContext, mg_path: str, final: str,
                         res: PlaybackResult) -> str:
    """NL-ondertitelcontrole per server na een vervanging: ziet de server de Nederlandse
    ondertitel die MediaGuard bij het bestand kent? ``not_expected`` als NL niet gewenst is of het
    bestand er geen heeft, ``unknown`` als de server niets meldde (Plex, afspelen mislukt)."""
    from mediaguard.mediaserver import subtitles as T

    if not T.nl_wanted(ctx):
        return "not_expected"
    row = ctx.db.fetch_one("SELECT media_info_json FROM lib_files WHERE path=? LIMIT 1",
                           (mg_path,))
    info = loads(row["media_info_json"], {}) if row else {}
    if not T.has_nl(Path(mg_path), info if isinstance(info, dict) else {}):
        return "not_expected"
    seen = res.extra.get("nl_subtitles")
    if final not in ("ok", "transcode") or seen is None:
        return "unknown"
    return "visible" if seen else "missing"


def _nl_subtitles_missing(ctx: AppContext, row: dict[str, Any], label: str, sid: int) -> None:
    from mediaguard.api.system import notify

    name = Path(row["path"]).name
    log_action(ctx, "subtitles_check", f"{label} ziet de Nederlandse ondertitel van {name} niet",
               path=row["path"], txn=row["transaction_id"], ok=False, server_id=sid)
    notify(ctx, "warning", f"NL-ondertitel niet zichtbaar in {label}: {name}",
           f"MediaGuard kent een Nederlandse ondertitel bij dit bestand, maar {label} toont hem "
           "niet. Laat de bibliotheek in de mediaserver opnieuw scannen.",
           link="/inrichten?tab=mediaserver", dedupe_key=f"mediaserver.nl_subtitles.{sid}",
           group_title="{n} bestanden zonder zichtbare NL-ondertitel in " + label)


def summarize(ctx: AppContext) -> int:
    """Per overdracht de uitkomst van alle servers samenvatten in ``mediaserver_pending`` (zoals
    1.2.2 het las): ververst als elke server ververste; de afspeeltest is klaar als elke server
    klaar is, met de slechtste uitkomst. Met één server is dat precies zijn eigen uitkomst."""
    rank = {"failed": 0, "not_found": 1, "transcode": 2, "ok": 3, "skipped": 4}
    rows = ctx.db.fetch_dicts(
        "SELECT p.transaction_id, p.test_after, p.refreshed_at FROM mediaserver_pending p "
        "WHERE p.done_at IS NULL AND EXISTS (SELECT 1 FROM mediaserver_server_results r WHERE "
        "r.transaction_id=p.transaction_id)")
    n = 0
    for p in rows:
        parts = ctx.db.fetch_dicts(
            "SELECT r.server_id, r.refreshed_at, r.result, r.detail_nl, r.attempts, r.done_at "
            "FROM mediaserver_server_results r WHERE r.transaction_id=? ORDER BY r.server_id",
            (p["transaction_id"],))
        if p["refreshed_at"] is None and all(x["refreshed_at"] for x in parts):
            ctx.db.execute("UPDATE mediaserver_pending SET refreshed_at=? WHERE transaction_id=?",
                           (max(str(x["refreshed_at"]) for x in parts), p["transaction_id"]))
        if p["test_after"] is None or not all(x["done_at"] for x in parts):
            continue
        tested = [x for x in parts if x["result"]] or parts
        worst = min(tested, key=lambda x: rank.get(str(x["result"]), 0))
        if len(tested) == 1:
            detail = worst["detail_nl"]
        else:
            labels = {s.id: s.label for s in M.all_servers(ctx)}
            detail = " · ".join(f"{labels.get(int(x['server_id']), 'Mediaserver')}: "
                                f"{x['detail_nl'] or x['result']}" for x in tested)
        ctx.db.execute(
            "UPDATE mediaserver_pending SET attempts=?, result=?, detail_nl=?, done_at=? "
            "WHERE transaction_id=?",
            (max(int(x["attempts"]) for x in parts), worst["result"] or "skipped",
             (detail or "")[:500], max(str(x["done_at"]) for x in parts), p["transaction_id"]))
        n += 1
    return n


async def _playback_failed(ctx: AppContext, row: dict[str, Any], res: PlaybackResult,
                           label: str = "Emby") -> None:
    from mediaguard.api.system import notify

    name = Path(row["path"]).name
    action = str(_v(ctx, "playback_fail_action") or "notify")
    restored = False
    if action == "rollback":
        item = ctx.db.fetch_one("SELECT id FROM quarantine_items WHERE transaction_id=? AND "
                                "status='restorable'", (row["transaction_id"],))
        if item is not None and not ctx.emergency_stopped():
            from mediaguard.recovery import service as recovery

            try:
                recovery.create_restore(ctx, int(item["id"]), "svc:mediaserver")
                restored = True
            except (LookupError, ValueError, FileNotFoundError) as exc:
                LOGGER.warning("terugzetten na afspeeltest kon niet: %s", exc)
    body = (f"{res.detail_nl}. " + ("MediaGuard zet het origineel terug." if restored else
            "Het AV1-bestand blijft staan; controleer het in je mediaserver."))
    notify(ctx, "warning", f"Afspeeltest mislukt: {name}", body,
           link="/inrichten?tab=mediaserver", dedupe_key="mediaserver.playback_failed",
           group_title="{n} AV1-bestanden slaagden niet voor de afspeeltest")
    audit(ctx.db, actor="svc:mediaserver", action="mediaserver.playback_failed",
          target_type="file", target_id=row["transaction_id"], reason=res.detail_nl,
          new_value={"path": row["path"], "restored": restored, "server": label})


def playback_for_job(ctx: AppContext, encoding_job_id: int) -> dict[str, Any] | None:
    row = ctx.db.fetch_one("SELECT result, detail_nl, done_at, test_after FROM "
                           "mediaserver_pending WHERE encoding_job_id=? ORDER BY "
                           "transaction_id DESC LIMIT 1", (encoding_job_id,))
    return dict(row) if row else None


# -- (c) pauzeren tijdens kijken + (d) recent bekeken niet omzetten ----------------------------
async def poll_sessions(ctx: AppContext, client: Server,
                        server: M.ServerConf | None = None, *,
                        hold: bool = True) -> dict[str, Any]:
    """Sessies van één server lezen; met ``hold`` (alleen server 1 zonder andere servers, zoals
    in 1.2.2) meteen de kijkpauze bijwerken. ``tick`` doet dat anders voor alle servers samen."""
    srv = server or M.legacy(ctx)
    sid = srv.id if srv else M.LEGACY_ID
    try:
        sessions = await client.sessions()
    except MediaServerError as exc:
        if srv is not None:
            M.mark_contact(ctx, srv, False, str(exc))
        return {"error": str(exc)}
    if srv is not None:
        M.mark_contact(ctx, srv, True)
    mapping = await path_map(ctx, client, sid) if sessions else []
    watching = [s for s in sessions if not s.paused]
    for s in sessions:
        mg = P.to_mediaguard(s.path, mapping) if s.path else None
        if mg:
            ctx.db.execute(
                "INSERT INTO mediaserver_watch(path,title,user_name,last_seen) VALUES (?,?,?,?) "
                "ON CONFLICT(path) DO UPDATE SET last_seen=excluded.last_seen, "
                "title=excluded.title, user_name=excluded.user_name",
                (mg, s.title, s.user, utcnow()))
    M.sset(ctx, sid, "watching", {
        "at": utcnow(), "active": bool(watching),
        "title": watching[0].title if watching else "",
        "sessions": [{"user": s.user, "title": s.title, "paused": s.paused} for s in sessions]})
    if hold:
        ctx.state_set("mediaserver_sessions", {
            "at": utcnow(), "watching": [{"user": s.user, "title": s.title, "paused": s.paused}
                                         for s in sessions]})
        await apply_hold(ctx, bool(watching), watching[0].title if watching else "")
    return {"sessions": len(sessions), "watching": len(watching)}


def _fresh(at: Any, minutes: int) -> bool:
    try:
        return now_utc() - datetime.fromisoformat(str(at)) <= timedelta(minutes=minutes)
    except (TypeError, ValueError):
        return False


async def combine_hold(ctx: AppContext, servers: list[M.ServerConf], reached: set[int]) -> None:
    """Kijkpauze over alle gevolgde servers: iemand kijkt op één ervan → nieuwe encodes wachten.
    Een server die deze ronde niet antwoordde, telt met zijn laatst bekende stand (zijn pauze
    valt niet weg door een korte storing, en zijn storing heft de pauze van een ander niet op),
    maar hooguit ``HOLD_STALE_MIN`` minuten: een server die langer weg is, houdt de encodes niet
    eindeloos tegen terwijl de andere servers gewoon antwoorden. Antwoordde geen enkele server,
    dan verandert er niets (zoals in 1.2.2 bij één server)."""
    if not reached:
        return
    watching: list[tuple[M.ServerConf, str]] = []
    sessions: list[dict[str, Any]] = []
    for srv in servers:
        st = M.sget(ctx, srv, "watching", {}) or {}
        if srv.id not in reached and not _fresh(st.get("at"), HOLD_STALE_MIN):
            continue
        if st.get("active"):
            watching.append((srv, str(st.get("title") or "")))
        sessions += [{**x, "server_id": srv.id, "server": srv.label}
                     for x in st.get("sessions") or []]
    ctx.state_set("mediaserver_sessions", {"at": utcnow(), "watching": sessions})
    if not watching:
        await apply_hold(ctx, False)
        return
    srv, title = watching[0]
    if len(servers) == 1:
        await apply_hold(ctx, True, title)
    else:
        await apply_hold(ctx, True, title, where=srv.label)


async def apply_hold(ctx: AppContext, active: bool, title: str = "",
                     where: str | None = None) -> None:
    """Nieuwe AV1-encodes laten wachten zolang er gekeken wordt; daarna vanzelf verder.
    Pas opheffen na twee stille metingen (geen gestotter bij pauze/volgende aflevering)."""
    from mediaguard.encoding.client import EncoderError, get_encoder_client

    mode = str(_v(ctx, "pause_while_watching") or "both")
    lanes = HOLD_LANES.get(mode, [])
    held = bool(ctx.state_get("mediaserver_hold_active"))
    if active and lanes:
        ctx.state_set("mediaserver_quiet_polls", 0)
        reason = "iemand kijkt" + (f" op {where}" if where else "") + (
            f" ({title})" if title else "")
        if not held or ctx.state_get("mediaserver_hold_reason") != reason:
            try:
                await get_encoder_client(ctx).set_playback_hold(True, lanes, reason)
            except EncoderError as exc:
                LOGGER.info("pauze tijdens kijken niet doorgegeven: %s", exc)
                return
            if not held:
                log_action(ctx, "hold", f"Nieuwe AV1-encodes wachten: {reason}")
            ctx.state_set("mediaserver_hold_active", True)
            ctx.state_set("mediaserver_hold_reason", reason)
        return
    if not held:
        return
    quiet = int(ctx.state_get("mediaserver_quiet_polls") or 0) + 1
    ctx.state_set("mediaserver_quiet_polls", quiet)
    if quiet < 2 and lanes:
        return
    try:
        await get_encoder_client(ctx).set_playback_hold(False, [], "")
    except EncoderError as exc:
        LOGGER.info("pauze tijdens kijken niet opgeheven: %s", exc)
        return
    ctx.state_set("mediaserver_hold_active", False)
    ctx.state_set("mediaserver_hold_reason", None)
    log_action(ctx, "resume", "Niemand kijkt meer: AV1-encodes gaan verder")


def watched_paths(ctx: AppContext) -> set[str]:
    """Paden die recent bekeken zijn: de AV1-autopilot slaat ze (nog) over."""
    try:
        if not bool(_v(ctx, "skip_recent_watch")):
            return set()
        hours = int(_v(ctx, "recent_watch_hours") or 24)
    except KeyError:
        return set()
    since = (now_utc() - timedelta(hours=hours)).isoformat(timespec="seconds")
    try:
        rows = ctx.db.fetch_all("SELECT path FROM mediaserver_watch WHERE last_seen>=?",
                                (since,))
    except Exception:  # noqa: BLE001 - tabel bestaat nog niet (oude database)
        return set()
    return {str(r["path"]) for r in rows}


# -- één ronde ---------------------------------------------------------------------------------
def _close_without_servers(ctx: AppContext) -> None:
    """Zonder gevolgde server: wachtrij leeg houden (niets om te verversen of te testen)."""
    ctx.db.execute("UPDATE mediaserver_pending SET refreshed_at=COALESCE(refreshed_at,?), "
                   "done_at=COALESCE(done_at,?), result=COALESCE(result,'skipped') "
                   "WHERE done_at IS NULL", (utcnow(), utcnow()))
    ctx.db.execute("UPDATE mediaserver_server_results SET refreshed_at=COALESCE(refreshed_at,?),"
                   " done_at=COALESCE(done_at,?), result=COALESCE(result,'skipped') "
                   "WHERE done_at IS NULL", (utcnow(), utcnow()))


def _close_unfollowed(ctx: AppContext, servers: list[M.ServerConf]) -> None:
    """Open taken van een server die niet meer gevolgd wordt (losgemaakt, verwijderd, sleutel
    weg) vervallen; de uitkomst van de andere servers telt."""
    ids = [s.id for s in servers]
    marks = ",".join("?" for _ in ids)
    ctx.db.execute(
        "UPDATE mediaserver_server_results SET refreshed_at=COALESCE(refreshed_at,?), "
        "done_at=?, result=COALESCE(result,'skipped'), detail_nl=COALESCE(detail_nl,?) "
        f"WHERE done_at IS NULL AND server_id NOT IN ({marks})",
        (utcnow(), utcnow(), "server niet meer gevolgd", *ids))
    ctx.db.execute("UPDATE mediaserver_server_results SET done_at=? WHERE done_at IS NULL AND "
                   "refreshed_at IS NOT NULL AND test_after IS NULL", (utcnow(),))


async def tick(ctx: AppContext, *, sessions: bool = True) -> dict[str, Any]:
    servers = M.followed(ctx)
    ensure_legacy_rows(ctx, servers)
    out: dict[str, Any] = {"queued": collect_handoffs(ctx, servers)}
    if not servers:
        _close_without_servers(ctx)
        if ctx.state_get("mediaserver_hold_active"):
            await apply_hold(ctx, False)
            await apply_hold(ctx, False)
        return {**out, "configured": False}
    _close_unfollowed(ctx, servers)
    single = len(servers) == 1
    per: dict[int, dict[str, Any]] = {}
    reached: set[int] = set()
    for srv in servers:
        # elke server apart: een fout (of uitzondering) bij de ene raakt de andere nooit
        res: dict[str, Any] = {}
        try:
            client = M.client(ctx, srv)
            res["refreshed"] = await refresh_pending(ctx, client, srv)
            res["tested"] = await run_playback_tests(ctx, client, srv)
            if sessions:
                res["sessions"] = await poll_sessions(ctx, client, srv, hold=single)
                if "error" not in res["sessions"]:
                    reached.add(srv.id)
        except MediaServerError as exc:
            M.mark_contact(ctx, srv, False, str(exc))
            res["error"] = str(exc)
        except Exception as exc:  # noqa: BLE001 - één server mag de rest nooit stoppen
            LOGGER.exception("mediaserver %s: ronde mislukt", srv.label)
            M.mark_contact(ctx, srv, False, type(exc).__name__)
            res["error"] = type(exc).__name__
        per[srv.id] = res
    summarize(ctx)
    if sessions and not single:
        await combine_hold(ctx, servers, reached)
    first = per[servers[0].id]
    # bovenste velden zoals in 1.2.2 (eerste gevolgde server), plus de uitkomst per server
    return {**out, **{k: v for k, v in first.items() if k != "error"},
            "servers": {str(k): v for k, v in per.items()}}
