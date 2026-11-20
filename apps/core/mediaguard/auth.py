"""Lokale authenticatie met RBAC (admin/operator/viewer), sessiecookie en CSRF.

- Wachtwoorden: scrypt met per-gebruiker salt.
- Sessie: willekeurig token in HttpOnly SameSite=Strict cookie ``mg_session``; in de DB
  staat alleen de SHA-256.
- CSRF: double-submit. Muterende verzoeken moeten header ``X-CSRF-Token`` sturen die
  gelijk is aan de sessie-csrf (de UI leest die uit ``GET /api/v1/auth/me``).
- Machine-to-machine (interne services): header ``X-MediaGuard-Token``, alleen vanaf een
  rechtstreekse interne verbinding (nooit via de web-server of tunnel). Fork-hooks hebben een
  eigen hook-sleutel die alleen op ``/api/v1/hooks/*`` geldt.
- Koppelingen (bv. Media Source Optimizer): header ``X-Api-Key`` met een API-sleutel met rol
  viewer of operator (zie ``mediaguard.apikeys``). Ongeldig = 401, nooit terugval op de cookie.
- Rechten (scopes, S-P3-600): een kijker-sleutel met rechten mag lezen (GET/HEAD) zoals een kijker
  plus precies de routes die zijn rechten openen (``SCOPE_ROUTES``); al het andere geeft 403
  (``scope_guard``, centraal bij het aanmelden met de sleutel). Zonder rechten (NULL) bepaalt de
  rol alles, zoals altijd.
"""

from __future__ import annotations

import base64
import hashlib
import hmac
import secrets
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta

from fastapi import Depends, HTTPException, Request
from starlette.requests import HTTPConnection

from mediaguard.db import Database, utcnow

SESSION_COOKIE = "mg_session"
SESSION_DAYS = 14
ROLE_RANK = {"viewer": 1, "operator": 2, "admin": 3}

#: Rechten (scopes) van API-sleutels (S-P3-600, C1.3-MGU-05): per recht de routes (methode,
#: padsjabloon) die het bovenop "lezen als kijker" opent. Een recht dat hier niet staat, opent
#: niets (fail-closed). ``wanted:search`` komt er pas na besluit BG-05.
SCOPE_ESTOP_ON = "emergency_stop:on"
SCOPE_ROUTES: dict[str, frozenset[tuple[str, str]]] = {
    SCOPE_ESTOP_ON: frozenset({("POST", "/api/v1/system/emergency-stop")}),
}
#: wat een sleutel met rechten altijd mag, net als een kijker (een websocket begint met GET)
SCOPE_READ_METHODS = frozenset({"GET", "HEAD"})
SCOPE_DENIED_DETAIL = "Deze API-sleutel heeft beperkte rechten (scopes) en mag dit niet"


@dataclass(frozen=True)
class Principal:
    username: str
    role: str
    user_id: int | None = None
    csrf_token: str | None = None
    machine: bool = False
    #: rechten van een API-sleutel; None = geen rechten (NULL): de rol bepaalt alles
    scopes: tuple[str, ...] | None = None

    @property
    def actor(self) -> str:
        if self.username.startswith("apikey:"):
            return self.username  # audit: 'apikey:<naam>'
        return f"{'svc' if self.machine else 'user'}:{self.username}"


def hash_password(password: str) -> str:
    if len(password) < 10:
        raise ValueError("Wachtwoord moet minimaal 10 tekens hebben")
    salt = secrets.token_bytes(16)
    digest = hashlib.scrypt(password.encode(), salt=salt, n=2**14, r=8, p=1, dklen=32)
    return "scrypt$" + base64.b64encode(salt).decode() + "$" + base64.b64encode(digest).decode()


def verify_password(password: str, stored: str) -> bool:
    try:
        scheme, salt_b64, digest_b64 = stored.split("$")
    except ValueError:
        return False
    if scheme != "scrypt":
        return False
    salt = base64.b64decode(salt_b64)
    expected = base64.b64decode(digest_b64)
    digest = hashlib.scrypt(password.encode(), salt=salt, n=2**14, r=8, p=1, dklen=32)
    return hmac.compare_digest(digest, expected)


def _token_hash(token: str) -> str:
    return hashlib.sha256(token.encode()).hexdigest()


def create_session(db: Database, user_id: int, *, ip: str | None = None,
                   user_agent: str | None = None) -> tuple[str, str]:
    token = secrets.token_urlsafe(32)
    csrf = secrets.token_urlsafe(24)
    expires = (datetime.now(UTC) + timedelta(days=SESSION_DAYS)).isoformat(timespec="seconds")
    db.execute(
        "INSERT INTO sessions(token_hash,user_id,csrf_token,created_at,expires_at,last_seen_at,"
        "ip,user_agent) VALUES (?,?,?,?,?,?,?,?)",
        (_token_hash(token), user_id, csrf, utcnow(), expires, utcnow(), ip, user_agent),
    )
    db.execute("UPDATE users SET last_login_at=? WHERE id=?", (utcnow(), user_id))
    return token, csrf


def delete_session(db: Database, token: str) -> None:
    db.execute("DELETE FROM sessions WHERE token_hash=?", (_token_hash(token),))


def internal_token_ok(request: Request, presented: str, expected: str | None) -> bool:
    """Interne token klopt én komt van een rechtstreekse interne verbinding: nooit via de
    web-server/tunnel (die gooit de header ook weg) of van buiten de container."""
    from mediaguard.security.net import internal_origin

    if not presented or not expected or not hmac.compare_digest(presented, expected):
        return False
    return internal_origin(request)


def principal_from_request(request: Request) -> Principal | None:
    """Raises HTTPException(401/429) bij een ongeldige X-Api-Key."""
    from mediaguard.context import get_app_context

    app = get_app_context(request)
    route = getattr(request.scope.get("route"), "path_format", None)
    backup_report = (request.scope.get("method") == "GET" and route == "/connector/v1/items"
                     and request.query_params.get("type") == "backups")
    internal = request.headers.get("X-MediaGuard-Token")
    if internal and internal_token_ok(request, internal, app.settings.internal_token):
        return Principal(username="internal", role="admin", machine=True)
    # Hook-sleutels van Sonarr/Radarr gelden alleen voor hun eigen hook-endpoints
    # (/api/v1/hooks/*, met eigen controle in hooks/api.py), nooit als algemene login.
    api_key = request.headers.get("X-Api-Key")
    if api_key is not None:
        from mediaguard.apikeys import authenticate

        # geldig -> principal; anders HTTPException 401/429 (geen terugval op de cookie)
        principal = authenticate(app, request, api_key, backup_report=backup_report)
        scope_guard(request, principal)
        return principal
    if app.settings.dev_auth_bypass:
        return Principal(username="dev", role="admin", csrf_token=None)
    token = request.cookies.get(SESSION_COOKIE)
    if not token:
        return None
    th = _token_hash(token)
    row = app.db.fetch_one(
        "SELECT s.csrf_token, s.expires_at, s.last_seen_at, u.id, u.username, u.role, "
        "u.disabled FROM sessions s JOIN users u ON u.id=s.user_id WHERE s.token_hash=?",
        (th,),
    )
    if row is None or row["disabled"] or row["expires_at"] < utcnow():
        return None
    now = utcnow()
    if not backup_report and (not row["last_seen_at"] or row["last_seen_at"][:16] != now[:16]):
        # hooguit één schrijfactie per minuut per sessie (overzicht 'ingelogde apparaten')
        def touch() -> None:
            app.db.execute("UPDATE sessions SET last_seen_at=? WHERE token_hash=?", (now, th))

        if route == "/api/v1/backups/{name}/restore":
            # Een geweigerde herstelkopie mag ook de sessie niet wijzigen.
            request.state.touch_restore_session = touch
        else:
            touch()
    return Principal(username=row["username"], role=row["role"], user_id=int(row["id"]),
                     csrf_token=row["csrf_token"])


def scope_guard(request: HTTPConnection, principal: Principal) -> None:
    """Centrale controle voor een API-sleutel met rechten (scopes niet NULL): lezen (GET/HEAD)
    mag zoals een kijker; elke andere methode alleen op een route die een van zijn rechten
    opent (``SCOPE_ROUTES``), anders 403. Geldt voor elke route met aanmelding, ook voor de
    tweede weg naar de noodstop (``POST /api/v1/app/control``). Een sleutel zonder rechten
    (NULL) raakt deze controle niet."""
    if principal.scopes is None:
        return
    method = str(request.scope.get("method") or "GET")
    if method in SCOPE_READ_METHODS:
        return
    route = getattr(request.scope.get("route"), "path_format", None)
    opened = {r for scope in principal.scopes for r in SCOPE_ROUTES.get(scope, frozenset())}
    if route is None or (method, route) not in opened:
        raise HTTPException(status_code=403, detail=SCOPE_DENIED_DETAIL)


def current_principal(request: Request) -> Principal:
    principal = principal_from_request(request)
    if principal is None:
        raise HTTPException(status_code=401, detail="Niet ingelogd")
    if principal.role == "admin" and principal.user_id and not principal.machine:
        path = request.url.path
        if not path.startswith(("/api/v1/security", "/api/v1/auth")):
            from mediaguard.context import get_app_context
            from mediaguard.security.api import twofa_required

            if twofa_required(get_app_context(request), principal):
                raise HTTPException(status_code=403,
                                    detail="Stel eerst tweestapsverificatie in "
                                           "(Instellingen → Beveiliging)")
    if request.method not in ("GET", "HEAD", "OPTIONS") and not principal.machine \
            and principal.csrf_token is not None:
        sent = request.headers.get("X-CSRF-Token", "")
        if not hmac.compare_digest(sent, principal.csrf_token):
            raise HTTPException(status_code=403, detail="CSRF-controle mislukt")
    return principal


def require(role: str):  # type: ignore[no-untyped-def]
    def dep(principal: Principal = Depends(current_principal)) -> Principal:
        if ROLE_RANK.get(principal.role, 0) < ROLE_RANK[role]:
            raise HTTPException(status_code=403, detail=f"Rol '{role}' vereist")
        return principal
    return dep


require_viewer = require("viewer")
require_operator = require("operator")
require_admin = require("admin")
