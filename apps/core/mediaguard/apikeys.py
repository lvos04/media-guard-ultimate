"""API-sleutels: machine-toegang met een eigen rol (viewer of operator, nooit admin).

Bedoeld voor koppelingen zoals Media Source Optimizer (MSO). De sleutel gaat mee in header
``X-Api-Key``. In de database staan alleen ``sha256(sleutel)`` en de eerste 8 tekens
(prefix, om de sleutel in de UI te herkennen); de sleutel zelf wordt één keer getoond.

Een onbekende, ingetrokken of verlopen sleutel geeft altijd 401 — er wordt dan niet
teruggevallen op een sessiecookie in hetzelfde verzoek, want dat zou fouten maskeren.
"""

from __future__ import annotations

import hashlib
import hmac
import secrets
import threading
import time
from datetime import UTC, datetime, timedelta
from typing import TYPE_CHECKING, Any

from fastapi import HTTPException

from mediaguard.audit import audit
from mediaguard.db import utcnow

if TYPE_CHECKING:
    from starlette.requests import HTTPConnection

    from mediaguard.auth import Principal
    from mediaguard.context import AppContext

HEADER = "X-Api-Key"
KEY_PREFIX = "mgk_"
PREFIX_LEN = 8
ROLES = ("viewer", "operator")
INVALID_DETAIL = "Ongeldige of ingetrokken API-sleutel"
TOUCH_INTERVAL_S = 60
# mislukte pogingen per IP (zelfde idee als bij inloggen): max N binnen het venster
FAIL_LIMIT = 10
FAIL_WINDOW_S = 300


def generate_key() -> str:
    return KEY_PREFIX + secrets.token_urlsafe(32)


def key_hash(key: str) -> str:
    return hashlib.sha256(key.encode()).hexdigest()


class FailureLimiter:
    """Telt mislukte sleutelpogingen per IP in het geheugen (per proces)."""

    def __init__(self, limit: int = FAIL_LIMIT, window_s: float = FAIL_WINDOW_S) -> None:
        self.limit = limit
        self.window_s = window_s
        self._hits: dict[str, list[float]] = {}
        self._lock = threading.Lock()

    def _recent(self, ip: str, now: float) -> list[float]:
        hits = [t for t in self._hits.get(ip, []) if now - t < self.window_s]
        self._hits[ip] = hits
        return hits

    def retry_after(self, ip: str) -> int | None:
        now = time.monotonic()
        with self._lock:
            hits = self._recent(ip, now)
            if len(hits) < self.limit:
                return None
            return max(1, int(self.window_s - (now - hits[0])) + 1)

    def fail(self, ip: str) -> None:
        now = time.monotonic()
        with self._lock:
            self._recent(ip, now).append(now)
            if len(self._hits) > 10_000:  # begrensd geheugen bij een scan vanaf veel IP's
                for old in list(self._hits)[:5_000]:
                    self._hits.pop(old, None)


def _limiter(ctx: AppContext) -> FailureLimiter:
    lim = ctx.services.get("apikey_limiter")
    if lim is None:
        lim = FailureLimiter()
        ctx.services["apikey_limiter"] = lim
    return lim  # type: ignore[no-any-return]


def client_ip(request: HTTPConnection) -> str:
    """Echt clientadres; proxy-headers tellen alleen van de eigen web-server of een
    vertrouwde proxy (zie mediaguard.security.net)."""
    from mediaguard.security.net import client_ip as real_ip

    return real_ip(request)


def _expired(expires_at: str | None) -> bool:
    if not expires_at:
        return False
    try:
        return datetime.fromisoformat(expires_at) <= datetime.now(UTC)
    except ValueError:
        return True


def _reject(ctx: AppContext, request: HTTPConnection, key: str, why: str, *,
            count: bool = True) -> HTTPException:
    ip = client_ip(request)
    if count:
        _limiter(ctx).fail(ip)
    prefix = key[:PREFIX_LEN] if key.startswith(KEY_PREFIX) else None
    audit(ctx.db, actor=f"anon:{ip}", action="apikey.rejected", target_type="api_key",
          target_id=prefix, reason=why)
    return HTTPException(status_code=401, detail=INVALID_DETAIL)


def authenticate(ctx: AppContext, request: HTTPConnection, key: str) -> Principal:
    """Geldige sleutel → machine-principal; anders 401 (of 429 bij te veel fouten)."""
    from mediaguard.auth import Principal

    wait = _limiter(ctx).retry_after(client_ip(request))
    if wait is not None:
        raise HTTPException(status_code=429, detail="Te veel ongeldige API-sleutels vanaf dit "
                            f"adres; probeer het over {wait} s opnieuw",
                            headers={"Retry-After": str(wait)})
    if not key.startswith(KEY_PREFIX) or len(key) > 200:
        raise _reject(ctx, request, key, "onbekend formaat")
    digest = key_hash(key)
    row = ctx.db.fetch_one("SELECT * FROM api_keys WHERE key_hash=?", (digest,))
    if row is None or not hmac.compare_digest(str(row["key_hash"]), digest):
        raise _reject(ctx, request, key, "onbekende sleutel")
    # Een bekende maar ingetrokken of verlopen sleutel is geen gok: niet meetellen voor de
    # IP-blokkade. Anders blokkeert een telefoon die nog met de oude sleutel pollt binnen
    # 5 minuten ook de nieuwe sleutel vanaf hetzelfde adres (opnieuw koppelen).
    if row["revoked_at"]:
        raise _reject(ctx, request, key, f"ingetrokken sleutel '{row['name']}'", count=False)
    if _expired(row["expires_at"]):
        raise _reject(ctx, request, key, f"verlopen sleutel '{row['name']}'", count=False)
    if row["role"] not in ROLES:  # verdedigt tegen een handmatig aangepaste database
        raise _reject(ctx, request, key, "ongeldige rol")
    _touch(ctx, int(row["id"]), row["last_used_at"])
    return Principal(username=f"apikey:{row['name']}", role=str(row["role"]), machine=True)


def _touch(ctx: AppContext, key_id: int, last_used: str | None) -> None:
    """last_used_at hooguit één keer per minuut per sleutel bijwerken (schrijfdruk)."""
    if last_used:
        try:
            if datetime.now(UTC) - datetime.fromisoformat(last_used) < timedelta(
                    seconds=TOUCH_INTERVAL_S):
                return
        except ValueError:
            pass
    ctx.db.execute("UPDATE api_keys SET last_used_at=? WHERE id=?", (utcnow(), key_id))


# -- beheer -----------------------------------------------------------------------------------
PUBLIC_COLUMNS = ("id", "name", "prefix", "role", "created_by", "created_at", "last_used_at",
                  "expires_at", "revoked_at")


def _public(row: Any) -> dict[str, Any]:
    out = {k: row[k] for k in PUBLIC_COLUMNS}
    if out["revoked_at"]:
        status = "ingetrokken"
    elif _expired(out["expires_at"]):
        status = "verlopen"
    else:
        status = "actief"
    out["status"] = status
    return out


def list_keys(ctx: AppContext) -> list[dict[str, Any]]:
    cols = ",".join(PUBLIC_COLUMNS)
    rows = ctx.db.fetch_all(f"SELECT {cols} FROM api_keys ORDER BY revoked_at IS NOT NULL, "  # noqa: S608
                            "created_at DESC, id DESC")
    out = [_public(r) for r in rows]
    usage = usage_summary(ctx)
    for k in out:
        k["usage"] = usage.get(str(k["name"]), {"actions_7d": 0, "by_action": [],
                                                "last": []})
        k["permissions_nl"] = ("Alleen lezen" if k.get("role") == "viewer" else
                               "Lezen en acties zoals 'zoek opnieuw'; geen beheer, gebruikers "
                               "of geheimen")
    return out


def usage_summary(ctx: AppContext, days: int = 7) -> dict[str, dict[str, Any]]:
    """Wat elke API-sleutel de laatste dagen deed, uit de audit (rapport 5, M13)."""
    from datetime import UTC, datetime, timedelta

    from mediaguard.audit_labels import action_nl

    since = (datetime.now(UTC) - timedelta(days=days)).isoformat(timespec="seconds")
    rows = ctx.db.fetch_dicts(
        "SELECT actor, action, COUNT(*) AS n, MAX(ts) AS last FROM audit_events "
        "WHERE actor LIKE 'apikey:%' AND ts>=? GROUP BY actor, action ORDER BY n DESC",
        (since,))
    out: dict[str, dict[str, Any]] = {}
    for r in rows:
        name = str(r["actor"])[len("apikey:"):]
        u = out.setdefault(name, {"actions_7d": 0, "by_action": [], "last": []})
        u["actions_7d"] += int(r["n"])
        u["by_action"].append({"action": r["action"], "action_nl": action_nl(r["action"]),
                               "count": int(r["n"]), "last": r["last"]})
    for name, u in out.items():
        u["last"] = [dict(x) for x in ctx.db.fetch_dicts(
            "SELECT ts, action, target_type, target_id FROM audit_events WHERE actor=? "
            "ORDER BY id DESC LIMIT 5", (f"apikey:{name}",))]
        for x in u["last"]:
            x["action_nl"] = action_nl(x["action"])
    return out


def create_key(ctx: AppContext, *, name: str, role: str, expires_in_days: int | None,
               actor: str) -> dict[str, Any]:
    if role not in ROLES:
        raise ValueError("rol")
    if ctx.db.fetch_one("SELECT 1 AS x FROM api_keys WHERE name=?", (name,)):
        raise HTTPException(409, f"Er bestaat al een API-sleutel met de naam '{name}'")
    key = generate_key()
    expires = None
    if expires_in_days:
        expires = (datetime.now(UTC) + timedelta(days=expires_in_days)).isoformat(
            timespec="seconds")
    key_id = ctx.db.execute_insert(
        "INSERT INTO api_keys(name,prefix,key_hash,role,created_by,created_at,expires_at) "
        "VALUES (?,?,?,?,?,?,?)",
        (name, key[:PREFIX_LEN], key_hash(key), role, actor, utcnow(), expires))
    audit(ctx.db, actor=actor, action="apikey.created", target_type="api_key",
          target_id=key_id, reason=f"API-sleutel '{name}' ({role}) aangemaakt",
          new_value={"name": name, "role": role, "expires_at": expires})
    row = ctx.db.fetch_one(f"SELECT {','.join(PUBLIC_COLUMNS)} FROM api_keys WHERE id=?",  # noqa: S608
                           (key_id,))
    assert row is not None
    return {**_public(row), "key": key}


def revoke_key(ctx: AppContext, key_id: int, *, actor: str) -> dict[str, Any]:
    row = ctx.db.fetch_one("SELECT * FROM api_keys WHERE id=?", (key_id,))
    if row is None:
        raise HTTPException(404, "API-sleutel niet gevonden")
    if not row["revoked_at"]:
        ctx.db.execute("UPDATE api_keys SET revoked_at=? WHERE id=?", (utcnow(), key_id))
        audit(ctx.db, actor=actor, action="apikey.revoked", target_type="api_key",
              target_id=key_id, reason=f"API-sleutel '{row['name']}' ingetrokken")
    fresh = ctx.db.fetch_one(f"SELECT {','.join(PUBLIC_COLUMNS)} FROM api_keys WHERE id=?",  # noqa: S608
                             (key_id,))
    assert fresh is not None
    return _public(fresh)
