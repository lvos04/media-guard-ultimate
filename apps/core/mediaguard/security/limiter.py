"""Remmen op inloggen en 2FA-codes (wachtwoorden raden via de tunnel).

Telt mislukte pogingen uit ``login_history`` (dus ook na een herstart):

- **per adres**: na ``security.login_max_attempts`` mislukte pogingen moet dat adres wachten,
  eerst 30 s en daarna steeds twee keer zo lang, tot hooguit
  ``security.login_max_delay_minutes``;
- **per gebruiker**: hetzelfde, maar alleen voor adressen waarvan die gebruiker nog nooit
  (90 dagen) is ingelogd. Zo remt een aanval vanaf veel adressen tegelijk, terwijl jij vanaf
  je eigen telefoon of thuisnetwerk gewoon kunt blijven inloggen.

Een geslaagde aanmelding vanaf een adres zet de teller van dat adres terug. De teller per
gebruiker (pogingen vanaf onbekende adressen, laatste 24 uur) loopt bewust door: jouw eigen
aanmelding vanaf een bekend apparaat mag een aanvaller geen nieuwe vrije pogingen geven. Bij
het bereiken van de grens komt er (instelbaar) een beveiligingsmelding.

IPv6-adressen tellen per /64: één aansluiting heeft een heel blok, dus wisselen binnen dat
blok geeft geen nieuwe pogingen. De beveiligingsmelding is één melding (``security.bruteforce``)
met een vaste titel: hooguit één nieuwe push per uur, daarna alleen de teller omhoog. Een
gebruikersnaam staat er alleen in als die gebruiker echt bestaat (nooit vrije tekst van buiten).
"""

from __future__ import annotations

import ipaddress
import math
import threading
from collections.abc import Iterator
from contextlib import contextmanager
from datetime import UTC, datetime, timedelta
from typing import TYPE_CHECKING, Any

from fastapi import HTTPException

if TYPE_CHECKING:
    from mediaguard.context import AppContext

WINDOW = timedelta(hours=24)
KNOWN_GOOD = timedelta(days=90)
BASE_DELAY_S = 30
NOTE_KEY = "security.bruteforce"
PUSH_STATE = "security_bruteforce_push"
PUSH_EVERY = timedelta(hours=1)
V6_SCAN_LIMIT = 5000


def _iso(dt: datetime) -> str:
    return dt.isoformat(timespec="seconds")


def _parse(ts: str | None) -> datetime | None:
    if not ts:
        return None
    try:
        dt = datetime.fromisoformat(ts)
    except ValueError:
        return None
    return dt if dt.tzinfo else dt.replace(tzinfo=UTC)


def _limits(ctx: AppContext) -> tuple[int, int]:
    limit = max(1, int(ctx.config.value("security.login_max_attempts")))
    max_wait = max(1, int(ctx.config.value("security.login_max_delay_minutes"))) * 60
    return limit, max_wait


def delay_for(failures: int, limit: int, max_wait: int) -> int:
    """Wachttijd (s) na ``failures`` mislukte pogingen; 0 zolang de grens niet bereikt is."""
    if failures < limit:
        return 0
    return int(min(max_wait, BASE_DELAY_S * 2 ** min(failures - limit, 20)))


def _since(ctx: AppContext, where: str, params: tuple[Any, ...], now: datetime) -> str:
    """Begin van de telling: 24 uur terug, of de laatste geslaagde aanmelding als die later is."""
    start = _iso(now - WINDOW)
    row = ctx.db.fetch_one(f"SELECT MAX(ts) AS ts FROM login_history WHERE success=1 AND {where}",  # noqa: S608
                           params)
    if row is not None and row["ts"] and str(row["ts"]) > start:
        return str(row["ts"])
    return start


def _count(ctx: AppContext, where: str, params: tuple[Any, ...],
           now: datetime) -> tuple[int, datetime | None]:
    since = _since(ctx, where, params, now)
    row = ctx.db.fetch_one(
        f"SELECT COUNT(*) AS n, MAX(ts) AS last FROM login_history WHERE success=0 AND "  # noqa: S608
        f"ts>? AND {where}", (since, *params))
    if row is None:
        return 0, None
    return int(row["n"] or 0), _parse(row["last"])


def _v6_net(ip: str | None) -> ipaddress.IPv6Network | None:
    """Het /64-blok van een IPv6-adres (None voor IPv4 of iets onleesbaars)."""
    try:
        addr = ipaddress.ip_address(str(ip or ""))
    except ValueError:
        return None
    if not isinstance(addr, ipaddress.IPv6Address) or addr.ipv4_mapped is not None:
        return None
    return ipaddress.IPv6Network(f"{addr}/64", strict=False)


def ip_group(ip: str) -> str:
    """Zo telt en meldt MediaGuard een adres: IPv4 los, IPv6 per /64."""
    net = _v6_net(ip)
    return str(net) if net is not None else ip


def _in_net(ip: Any, net: ipaddress.IPv6Network) -> bool:
    try:
        return ipaddress.ip_address(str(ip or "")) in net
    except ValueError:
        return False


def _count_ip(ctx: AppContext, ip: str, now: datetime) -> tuple[int, datetime | None]:
    net = _v6_net(ip)
    if net is None:
        return _count(ctx, "ip=?", (ip,), now)
    start = _iso(now - WINDOW)
    rows = [r for r in ctx.db.fetch_all(
        "SELECT ip, ts, success FROM login_history WHERE ts>? AND ip LIKE '%:%' "
        "ORDER BY ts DESC LIMIT ?", (start, V6_SCAN_LIMIT)) if _in_net(r["ip"], net)]
    ok = [str(r["ts"]) for r in rows if r["success"]]
    since = max([start, *ok])
    fails = [str(r["ts"]) for r in rows if not r["success"] and str(r["ts"]) > since]
    return len(fails), _parse(max(fails)) if fails else None


def _known_ip(ctx: AppContext, username: str, ip: str, now: datetime) -> bool:
    return ctx.db.fetch_one(
        "SELECT 1 FROM login_history WHERE success=1 AND username=? AND ip=? AND ts>? LIMIT 1",
        (username, ip, _iso(now - KNOWN_GOOD))) is not None


def state(ctx: AppContext, *, ip: str, username: str | None,
          now: datetime | None = None) -> dict[str, Any]:
    """Hoeveel mislukte pogingen telt dit adres/deze gebruiker, en hoe lang nog wachten?"""
    now = now or datetime.now(UTC)
    limit, max_wait = _limits(ctx)
    ip_n, ip_last = _count_ip(ctx, ip, now)
    user_n, user_last = 0, None
    if username and not _known_ip(ctx, username, ip, now):
        user_n, user_last = _count(
            ctx, "username=? AND ip NOT IN (SELECT ip FROM login_history WHERE success=1 AND "
                 "username=? AND ts>? AND ip IS NOT NULL)",
            (username, username, _iso(now - KNOWN_GOOD)), now)
    wait = 0
    for n, last in ((ip_n, ip_last), (user_n, user_last)):
        delay = delay_for(n, limit, max_wait)
        if delay and last is not None:
            wait = max(wait, math.ceil((last + timedelta(seconds=delay) - now).total_seconds()))
    return {"ip_failures": ip_n, "user_failures": user_n, "limit": limit,
            "retry_after": max(0, wait)}


def _wait_nl(seconds: int) -> str:
    if seconds < 60:
        return f"{seconds} seconden"
    minutes = math.ceil(seconds / 60)
    return "1 minuut" if minutes == 1 else f"{minutes} minuten"


# Core draait als één proces: met dit slot zijn controleren, het wachtwoord/de code nakijken en
# een mislukking vastleggen samen één stap. Zonder slot kwamen 30 gelijktijdige gokken allemaal
# langs de controle voordat de eerste mislukking was opgeslagen.
_ATTEMPT_LOCK = threading.Lock()


@contextmanager
def attempt(ctx: AppContext, *, ip: str | None, username: str | None) -> Iterator[None]:
    """Eén inlog-, 2FA- of wachtwoordpoging tegelijk: eerst de rem (429), dan binnen hetzelfde
    slot nakijken en een mislukking vastleggen (``record_login`` + ``after_failure``)."""
    with _ATTEMPT_LOCK:
        if ip is not None:
            check(ctx, ip=ip, username=username)
        yield


def check(ctx: AppContext, *, ip: str, username: str | None) -> None:
    """429 als dit adres of deze gebruiker nog even moet wachten."""
    wait = state(ctx, ip=ip, username=username)["retry_after"]
    if wait > 0:
        raise HTTPException(429, f"Te veel mislukte pogingen. Probeer het over {_wait_nl(wait)} "
                                 "opnieuw.", headers={"Retry-After": str(wait)})


def _due(st: int, limit: int) -> bool:
    return st == limit or (st > limit and st % (limit * 5) == 0)


def after_failure(ctx: AppContext, *, ip: str, username: str | None) -> None:
    """Na een mislukte poging: melden zodra een adres of gebruiker de grens bereikt. Eén
    melding voor alles, met een vaste titel en hooguit één nieuwe push per uur; aanvallers
    met steeds nieuwe adressen of verzonnen namen kunnen zo geen pushstroom veroorzaken."""
    if not bool(ctx.config.value("security.notify_failed_logins")):
        return
    st = state(ctx, ip=ip, username=username)
    limit = st["limit"]
    known = ctx.db.fetch_one("SELECT username FROM users WHERE username=?",
                             (username,)) if username else None
    who = str(known["username"]) if known is not None else None
    parts = []
    if _due(st["ip_failures"], limit):
        parts.append(f"{st['ip_failures']} mislukte pogingen vanaf {ip_group(ip)}"
                     + (f" (laatst voor '{who}')" if who else ""))
    if who and _due(st["user_failures"], limit):
        parts.append(f"{st['user_failures']} mislukte pogingen voor '{who}' vanaf onbekende "
                     "adressen; inloggen vanaf je eigen apparaten blijft werken")
    if not parts:
        return
    now = datetime.now(UTC)
    push = dict(ctx.state_get(PUSH_STATE, {}) or {})
    last = _parse(push.get("at"))
    body = ". ".join(parts) + (". MediaGuard laat die adressen nu steeds langer wachten. Was "
                               "jij dit niet? Zet tweestapsverificatie aan onder Instellingen "
                               "→ Beveiliging.")
    if last is not None and now - last < PUSH_EVERY:
        # binnen het uur: alleen tellen (geen nieuwe push)
        push["since_push"] = int(push.get("since_push") or 0) + 1
        ctx.state_set(PUSH_STATE, push)
        from mediaguard.db import utcnow

        ctx.db.execute("UPDATE notifications SET count=COALESCE(count,1)+1, ts=?, body=? "
                       "WHERE dedupe_key=? AND read=0 AND resolved_at IS NULL",
                       (utcnow(), body, NOTE_KEY))
        return
    extra = int(push.get("since_push") or 0)
    if extra:
        body += f" Sinds de vorige melding nog {extra} keer een grens bereikt."
    ctx.state_set(PUSH_STATE, {"at": _iso(now), "since_push": 0})
    from mediaguard.api.system import notify

    notify(ctx, "warning", "Veel mislukte inlogpogingen", body,
           link="/instellingen/beveiliging", dedupe_key=NOTE_KEY)
