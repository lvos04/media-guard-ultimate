"""Mediaserver-koppeling (Emby eerst; Jellyfin en Plex werken ook) — rapport 5, M3.

Wat het doet, allemaal automatisch zodra er een API-sleutel is:

a. na elke AV1-overdracht of terugzetting: de mediaserver dat ene bestand laten verversen
   (anders een volledige scan, hooguit eens per 30 min);
b. afspeeltest: ±10 min later vraagt MediaGuard de server of een gangbare tv het nieuwe
   bestand kan afspelen (direct of via omzetten). Mislukt → melding (of terugzetten als dat
   zo is ingesteld);
c. kijkt iemand, dan wachten nieuwe AV1-encodes (lopende draaien met lage prioriteit door);
d. wat de laatste 24 uur bekeken is, wordt niet omgezet.
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
from mediaguard.db import utcnow
from mediaguard.mediaserver import paths as P
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
# Gangbare poorten (standaard + TrueNAS-catalogus) per soort.
PORTS = {"emby": (8096, 9096, 30092), "jellyfin": (8096, 30013), "plex": (32400,)}


def _v(ctx: AppContext, key: str) -> Any:
    return ctx.config.value(f"mediaserver.{key}")


def now_utc() -> datetime:
    return datetime.now(UTC)


def log_action(ctx: AppContext, kind: str, detail: str, *, path: str | None = None,
               txn: int | None = None, ok: bool = True) -> None:
    ctx.db.execute(
        "INSERT INTO mediaserver_actions(ts,kind,path,transaction_id,ok,detail_nl) "
        "VALUES (?,?,?,?,?,?)", (utcnow(), kind, path, txn, 1 if ok else 0, detail[:500]))
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


async def test_connection(ctx: AppContext) -> dict[str, Any]:
    cfg = configured(ctx)
    if cfg["kind"] == "off":
        return {"ok": False, "message_nl": "Mediaserver-koppeling staat uit."}
    if not cfg["url"]:
        return {"ok": False, "message_nl": "Geen server gevonden; vul het adres in."}
    client = make_server(cfg["kind"], ctx.services, cfg["url"], ctx.secrets.get(SECRET_KEY))
    try:
        info = await client.info()
        if isinstance(client, EmbyClient) and info.get("kind") and info["kind"] != client.kind \
                and str(_v(ctx, "kind")) == "auto":
            ctx.state_set("mediaserver_detected_kind", info["kind"])
            client = make_server(info["kind"], ctx.services, cfg["url"],
                                 ctx.secrets.get(SECRET_KEY))
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


async def path_map(ctx: AppContext, client: Server) -> P.Mapping:
    cached = ctx.state_get("mediaserver_path_map", {}) or {}
    at = cached.get("at")
    if at and (now_utc() - datetime.fromisoformat(at)) < timedelta(hours=1) \
            and cached.get("map"):
        return [tuple(p) for p in cached["map"]]  # type: ignore[misc]
    try:
        locs = await client.locations()
    except MediaServerError:
        return [tuple(p) for p in cached.get("map") or []]  # type: ignore[misc]
    mapping = P.build_map(mg_roots(ctx), locs)
    ctx.state_set("mediaserver_path_map", {"map": mapping, "at": utcnow(), "server": locs})
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
def collect_handoffs(ctx: AppContext) -> int:
    """Nieuwe afgeronde overdrachten/terugzettingen in de wachtrij zetten."""
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


async def refresh_pending(ctx: AppContext, client: Server) -> int:
    if not bool(_v(ctx, "refresh_after_handoff")):
        ctx.db.execute("UPDATE mediaserver_pending SET refreshed_at=? WHERE refreshed_at IS NULL",
                       (utcnow(),))
        return 0
    rows = ctx.db.fetch_dicts("SELECT transaction_id, path, kind FROM mediaserver_pending "
                              "WHERE refreshed_at IS NULL LIMIT 100")
    if not rows:
        return 0
    mapping = await path_map(ctx, client)
    mapped = [(r, P.to_server(r["path"], mapping)) for r in rows]
    exact = [p for _, p in mapped if p]
    try:
        if exact:
            await client.refresh_paths(exact)
        if any(p is None for _, p in mapped):
            last = ctx.state_get("mediaserver_full_refresh_at")
            if not last or now_utc() - datetime.fromisoformat(last) > timedelta(
                    minutes=FULL_REFRESH_GAP_MIN):
                await client.refresh_all()
                ctx.state_set("mediaserver_full_refresh_at", utcnow())
    except MediaServerError as exc:
        log_action(ctx, "refresh", f"{client.label}: verversen mislukt — {exc}", ok=False)
        return 0
    for r, p in mapped:
        ctx.db.execute("UPDATE mediaserver_pending SET refreshed_at=? WHERE transaction_id=?",
                       (utcnow(), r["transaction_id"]))
        what = "teruggezet origineel" if r["kind"] == "restore" else "AV1-versie"
        log_action(ctx, "refresh", f"{client.label} ververst: {Path(r['path']).name} ({what})"
                   + ("" if p else " — volledige scan, map niet gekoppeld"),
                   path=r["path"], txn=r["transaction_id"])
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


async def run_playback_tests(ctx: AppContext, client: Server) -> int:
    due = ctx.db.fetch_dicts(
        "SELECT * FROM mediaserver_pending WHERE done_at IS NULL AND test_after IS NOT NULL "
        "AND test_after<=? AND refreshed_at IS NOT NULL ORDER BY test_after LIMIT 5", (utcnow(),))
    mapping = await path_map(ctx, client) if due else []
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
                "UPDATE mediaserver_pending SET attempts=?, test_after=?, detail_nl=? "
                "WHERE transaction_id=?",
                (attempts, (now_utc() + timedelta(minutes=TEST_DELAY_MIN)).isoformat(
                    timespec="seconds"), res.detail_nl, r["transaction_id"]))
            continue
        final = "not_found" if res.result == "retry" else res.result
        ctx.db.execute("UPDATE mediaserver_pending SET attempts=?, result=?, detail_nl=?, "
                       "done_at=? WHERE transaction_id=?",
                       (attempts, final, res.detail_nl, utcnow(), r["transaction_id"]))
        ok = final in ("ok", "transcode", "skipped")
        log_action(ctx, "playback_test", f"{Path(r['path']).name}: {res.detail_nl}",
                   path=r["path"], txn=r["transaction_id"], ok=ok or final == "not_found")
        if final == "failed":
            await _playback_failed(ctx, r, res)
    return len(due)


async def _playback_failed(ctx: AppContext, row: dict[str, Any], res: PlaybackResult) -> None:
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
          new_value={"path": row["path"], "restored": restored})


def playback_for_job(ctx: AppContext, encoding_job_id: int) -> dict[str, Any] | None:
    row = ctx.db.fetch_one("SELECT result, detail_nl, done_at, test_after FROM "
                           "mediaserver_pending WHERE encoding_job_id=? ORDER BY "
                           "transaction_id DESC LIMIT 1", (encoding_job_id,))
    return dict(row) if row else None


# -- (c) pauzeren tijdens kijken + (d) recent bekeken niet omzetten ----------------------------
async def poll_sessions(ctx: AppContext, client: Server) -> dict[str, Any]:
    try:
        sessions = await client.sessions()
    except MediaServerError as exc:
        return {"error": str(exc)}
    mapping = await path_map(ctx, client) if sessions else []
    watching = [s for s in sessions if not s.paused]
    for s in sessions:
        mg = P.to_mediaguard(s.path, mapping) if s.path else None
        if mg:
            ctx.db.execute(
                "INSERT INTO mediaserver_watch(path,title,user_name,last_seen) VALUES (?,?,?,?) "
                "ON CONFLICT(path) DO UPDATE SET last_seen=excluded.last_seen, "
                "title=excluded.title, user_name=excluded.user_name",
                (mg, s.title, s.user, utcnow()))
    ctx.state_set("mediaserver_sessions", {
        "at": utcnow(), "watching": [{"user": s.user, "title": s.title, "paused": s.paused}
                                     for s in sessions]})
    await apply_hold(ctx, bool(watching), watching[0].title if watching else "")
    return {"sessions": len(sessions), "watching": len(watching)}


async def apply_hold(ctx: AppContext, active: bool, title: str = "") -> None:
    """Nieuwe AV1-encodes laten wachten zolang er gekeken wordt; daarna vanzelf verder.
    Pas opheffen na twee stille metingen (geen gestotter bij pauze/volgende aflevering)."""
    from mediaguard.encoding.client import EncoderError, get_encoder_client

    mode = str(_v(ctx, "pause_while_watching") or "both")
    lanes = HOLD_LANES.get(mode, [])
    held = bool(ctx.state_get("mediaserver_hold_active"))
    if active and lanes:
        ctx.state_set("mediaserver_quiet_polls", 0)
        reason = "iemand kijkt" + (f" ({title})" if title else "")
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
async def tick(ctx: AppContext, *, sessions: bool = True) -> dict[str, Any]:
    out: dict[str, Any] = {"queued": collect_handoffs(ctx)}
    client = server(ctx)
    if client is None:
        # zonder koppeling: wachtrij leeg houden (niets om te verversen of te testen)
        ctx.db.execute("UPDATE mediaserver_pending SET refreshed_at=COALESCE(refreshed_at,?), "
                       "done_at=COALESCE(done_at,?), result=COALESCE(result,'skipped') "
                       "WHERE done_at IS NULL", (utcnow(), utcnow()))
        if ctx.state_get("mediaserver_hold_active"):
            await apply_hold(ctx, False)
            await apply_hold(ctx, False)
        return {**out, "configured": False}
    out["refreshed"] = await refresh_pending(ctx, client)
    out["tested"] = await run_playback_tests(ctx, client)
    if sessions:
        out["sessions"] = await poll_sessions(ctx, client)
    return out
