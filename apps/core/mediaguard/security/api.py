"""Tweestapsverificatie, sessies en aanmeldgeschiedenis.

- TOTP per gebruiker (QR-code voor een authenticator-app, 10 herstelcodes).
- Optioneel verplicht voor beheerders (Instellingen → Beveiliging).
- Overzicht van ingelogde apparaten met op afstand uitloggen; aanmeldgeschiedenis.
API-sleutels blijven ongewijzigd (die loggen niet in).
"""

from __future__ import annotations

import hashlib
import secrets
from datetime import UTC, datetime, timedelta
from typing import Any

from fastapi import APIRouter, Depends, HTTPException, Request
from pydantic import BaseModel, Field

from mediaguard.audit import audit
from mediaguard.auth import SESSION_COOKIE, Principal, current_principal, verify_password
from mediaguard.context import AppContext, get_app_context
from mediaguard.db import dumps, loads, utcnow
from mediaguard.security import limiter, net, totp
from mediaguard.security.net import DEFAULT_TRUSTED_PROXIES
from mediaguard.settings_registry import REGISTRY, SettingDef

router = APIRouter(prefix="/api/v1/security", tags=["security"])
CATEGORY = "Beveiliging"
CHALLENGE_MINUTES = 5
CHALLENGE_ATTEMPTS = 5


def register_settings() -> None:
    defs = [
        SettingDef("security.require_2fa_admins", "bool", False, CATEGORY,
                   "Tweestapsverificatie verplicht voor beheerders",
                   "Require 2FA for admins",
                   help_nl="Beheerders zonder tweestapsverificatie krijgen na het inloggen "
                           "alleen nog de pagina om die in te stellen."),
        SettingDef("security.notify_new_login", "bool", True, CATEGORY,
                   "Melden bij inloggen vanaf een nieuw adres", "Notify on new login",
                   help_nl="Stuurt een beveiligingsmelding als iemand inlogt vanaf een "
                           "IP-adres dat voor die gebruiker nieuw is."),
        SettingDef("security.login_max_attempts", "int", 5, CATEGORY,
                   "Mislukte inlogpogingen vóór wachttijd", "Failed logins before delay",
                   help_nl="Na zoveel mislukte pogingen (wachtwoord of code) moet dat adres "
                           "eerst wachten: 30 seconden, daarna steeds twee keer zo lang. Je "
                           "eigen apparaten blijven gewoon inloggen.",
                   minimum=3, maximum=100),
        SettingDef("security.login_max_delay_minutes", "int", 15, CATEGORY,
                   "Langste wachttijd na mislukte pogingen", "Maximum login delay",
                   help_nl="De wachttijd groeit tot hooguit dit aantal minuten per poging.",
                   minimum=1, maximum=1440, unit="min"),
        SettingDef("security.notify_failed_logins", "bool", True, CATEGORY,
                   "Melden bij veel mislukte inlogpogingen", "Notify on many failed logins",
                   help_nl="Stuurt een beveiligingsmelding zodra een adres of gebruiker de grens "
                           "van mislukte pogingen bereikt."),
        SettingDef("security.nudge_2fa", "bool", True, CATEGORY,
                   "Herinnering om tweestapsverificatie aan te zetten",
                   "Remind to turn on two-step verification",
                   help_nl="Zolang een beheerder geen tweestapsverificatie heeft, staat er een "
                           "herinnering bij 'Wacht op jou'. Het blijft je eigen keuze."),
        SettingDef("security.trusted_proxies", "list", list(DEFAULT_TRUSTED_PROXIES), CATEGORY,
                   "Vertrouwde proxy's (bijv. cloudflared)", "Trusted proxies",
                   help_nl="Adressen of netwerken (bijv. 172.16.0.0/12) waarvan MediaGuard het "
                           "echte IP-adres en https uit de headers overneemt (CF-Connecting-IP, "
                           "X-Forwarded-Proto). Loopback en het eigen adres van de NAS tellen "
                           "altijd mee; de standaard dekt een cloudflared in een andere app op "
                           "deze NAS. Draait cloudflared op een ander apparaat, voeg dan dat "
                           "adres toe. Van andere adressen worden die headers genegeerd.",
                   advanced=True),
    ]
    known = {x.key for x in REGISTRY.all()}
    for d in defs:
        if d.key not in known:
            REGISTRY.register(d)


# -- hulpfuncties (ook gebruikt door api/auth.py) ----------------------------------------------
def client_ip(request: Request) -> str:
    """Echt clientadres: proxy-headers tellen alleen van een vertrouwde proxy (zie net.py)."""
    return net.client_ip(request)[:64]


def user_agent(request: Request) -> str:
    return (request.headers.get("user-agent") or "")[:300]


def _secret_name(user_id: int, pending: bool = False) -> str:
    return f"auth.totp{'_pending' if pending else ''}.{user_id}"


def totp_enabled(ctx: AppContext, user_id: int) -> bool:
    row = ctx.db.fetch_one("SELECT enabled FROM user_totp WHERE user_id=?", (user_id,))
    return bool(row and row["enabled"])


def record_login(ctx: AppContext, request: Request, *, username: str, user_id: int | None,
                 success: bool, method: str = "password", reason: str | None = None) -> None:
    ip = client_ip(request)
    new_ip = False
    if success and user_id is not None and ip:
        new_ip = ctx.db.fetch_one("SELECT 1 FROM login_history WHERE user_id=? AND ip=? AND "
                                  "success=1 LIMIT 1", (user_id, ip)) is None
        had_any = ctx.db.fetch_one("SELECT 1 FROM login_history WHERE user_id=? AND success=1 "
                                   "LIMIT 1", (user_id,)) is not None
        new_ip = new_ip and had_any
    ctx.db.execute_insert(
        "INSERT INTO login_history(user_id,username,ts,ip,user_agent,success,method,reason) "
        "VALUES (?,?,?,?,?,?,?,?)",
        (user_id, username[:64], utcnow(), ip, user_agent(request), int(success), method,
         reason))
    if new_ip and bool(ctx.config.value("security.notify_new_login")):
        from mediaguard.api.system import notify

        notify(ctx, "warning", f"Nieuwe aanmelding van {username}",
               f"Ingelogd vanaf {ip} ({_device(user_agent(request))}). Was jij dit niet? "
               "Log dat apparaat uit onder Instellingen → Beveiliging.",
               link="/instellingen/beveiliging", dedupe_key=f"security.login.{user_id}.{ip}")


def _device(ua: str) -> str:
    u = ua.lower()
    os_ = next((n for k, n in (("iphone", "iPhone"), ("ipad", "iPad"), ("android", "Android"),
                               ("windows", "Windows"), ("mac os", "Mac"), ("linux", "Linux"))
                if k in u), "onbekend apparaat")
    br = next((n for k, n in (("edg/", "Edge"), ("firefox", "Firefox"), ("chrome", "Chrome"),
                              ("safari", "Safari")) if k in u), "")
    return f"{br} op {os_}" if br else os_


def start_challenge(ctx: AppContext, request: Request, user_id: int) -> str:
    token = secrets.token_urlsafe(32)
    expires = (datetime.now(UTC) + timedelta(minutes=CHALLENGE_MINUTES)).isoformat(
        timespec="seconds")
    ctx.db.execute("DELETE FROM login_challenges WHERE expires_at<?", (utcnow(),))
    ctx.db.execute("INSERT INTO login_challenges(token_hash,user_id,ip,user_agent,expires_at) "
                   "VALUES (?,?,?,?,?)", (hashlib.sha256(token.encode()).hexdigest(), user_id,
                                          client_ip(request), user_agent(request), expires))
    return token


def finish_challenge(ctx: AppContext, token: str, code: str,
                     request: Request | None = None) -> int:
    """Geeft user_id bij een geldige code; anders HTTPException (401)."""
    th = hashlib.sha256(token.encode()).hexdigest()
    row = ctx.db.fetch_one("SELECT * FROM login_challenges WHERE token_hash=?", (th,))
    if row is None or row["expires_at"] < utcnow():
        raise HTTPException(401, "Deze aanmelding is verlopen; log opnieuw in")
    user_id = int(row["user_id"])
    user = ctx.db.fetch_one("SELECT username FROM users WHERE id=?", (user_id,))
    username = str(user["username"]) if user else "?"
    # zelfde rem als bij het wachtwoord: wie het wachtwoord heeft, mag niet eindeloos nieuwe
    # uitdagingen starten om codes te raden. Rem, code nakijken en de pogingen tellen gebeuren
    # samen: gelijktijdige gokken tellen allemaal mee.
    with limiter.attempt(ctx, ip=client_ip(request) if request is not None else None,
                         username=username):
        row = ctx.db.fetch_one("SELECT * FROM login_challenges WHERE token_hash=?", (th,))
        if row is None:
            raise HTTPException(401, "Deze aanmelding is verlopen; log opnieuw in")
        if not check_code(ctx, user_id, code):
            if request is not None:
                record_login(ctx, request, username=username, user_id=user_id, success=False,
                             method="totp", reason="Onjuiste code")
                limiter.after_failure(ctx, ip=client_ip(request), username=username)
            attempts = int(row["attempts"]) + 1
            if attempts >= CHALLENGE_ATTEMPTS:
                ctx.db.execute("DELETE FROM login_challenges WHERE token_hash=?", (th,))
                raise HTTPException(401, "Te veel onjuiste codes; log opnieuw in")
            ctx.db.execute("UPDATE login_challenges SET attempts=? WHERE token_hash=?",
                           (attempts, th))
            raise HTTPException(401, "Onjuiste code")
        ctx.db.execute("DELETE FROM login_challenges WHERE token_hash=?", (th,))
    return user_id


def check_code(ctx: AppContext, user_id: int, code: str) -> bool:
    """TOTP-code of (eenmalige) herstelcode."""
    row = ctx.db.fetch_one("SELECT * FROM user_totp WHERE user_id=? AND enabled=1", (user_id,))
    secret = ctx.secrets.get(_secret_name(user_id))
    if row is None or not secret:
        return False
    counter = totp.verify(secret, code, last_counter=row["last_counter"])
    if counter is not None:
        ctx.db.execute("UPDATE user_totp SET last_counter=? WHERE user_id=?", (counter, user_id))
        return True
    hashes = loads(row["recovery_hashes_json"], [])
    h = totp.hash_recovery(code)
    if h in hashes:
        hashes.remove(h)
        ctx.db.execute("UPDATE user_totp SET recovery_hashes_json=? WHERE user_id=?",
                       (dumps(hashes), user_id))
        audit(ctx.db, actor=f"user:{user_id}", action="auth.2fa_recovery_used",
              target_type="user", target_id=str(user_id))
        return True
    return False


def twofa_required(ctx: AppContext, principal: Principal) -> bool:
    return bool(principal.user_id and principal.role == "admin"
                and bool(ctx.config.value("security.require_2fa_admins"))
                and not totp_enabled(ctx, int(principal.user_id)))


def attention_items(ctx: AppContext) -> list[dict[str, Any]]:
    """'Wacht op jou': onleesbare geheimen (verkeerde secret.key) en een herinnering om
    tweestapsverificatie aan te zetten (aansporen, niet verplichten)."""
    from mediaguard.attention import _item

    out: list[dict[str, Any]] = []
    health = ctx.secrets.health()
    if health["unreadable"]:
        names = health["unreadable"]
        out.append(_item(
            "security.secret_key_mismatch", "error",
            "Opgeslagen sleutels zijn onleesbaar (secret.key past niet)",
            f"{len(names)} van de {health['total']} opgeslagen sleutels en wachtwoorden kunnen "
            "niet worden ontsleuteld. Meestal is secret.key in de appdata-map vervangen, of "
            "hoort de database bij een andere installatie. Koppelingen met deze sleutels werken "
            "niet. Zet de juiste secret.key terug (bijv. uit je herstelbundel) en herstart, of "
            "vul de sleutels opnieuw in.", "/instellingen/geheimen", count=len(names),
            rows=[{"id": n, "title": n, "detail": "Onleesbaar"} for n in names[:20]]))
    import time

    seen = ctx.services.get(net.UNTRUSTED_TUNNEL_KEY)
    if seen and time.time() - float(seen.get("at") or 0) < 86400:
        out.append(_item(
            "security.tunnel_untrusted", "info",
            "Cloudflare-verkeer komt binnen via een niet-vertrouwd adres",
            f"Verzoeken via de tunnel komen bij MediaGuard binnen vanaf {seen['peer']}. Dat "
            "adres staat niet bij 'Vertrouwde proxy's', dus MediaGuard ziet het echte adres "
            "van de bezoeker niet en de inlogrem telt alle bezoekers als één adres. Draait "
            "cloudflared op dat apparaat? Voeg het adres dan toe onder Instellingen → "
            "Beveiliging → Vertrouwde proxy's.", "/instellingen#beveiliging"))
    if bool(ctx.config.value("security.nudge_2fa")) and \
            not bool(ctx.config.value("security.require_2fa_admins")):
        admins = ctx.db.fetch_dicts(
            "SELECT u.id, u.username FROM users u LEFT JOIN user_totp t ON t.user_id=u.id AND "
            "t.enabled=1 WHERE u.role='admin' AND u.disabled=0 AND t.user_id IS NULL "
            "ORDER BY u.id")
        if admins:
            out.append(_item(
                "security.twofa_off", "info", "Tweestapsverificatie staat uit",
                "MediaGuard is via internet te bereiken. Met tweestapsverificatie is een geraden "
                "of gelekt wachtwoord niet genoeg om in te loggen. Het blijft je keuze: deze "
                "herinnering zet je uit onder Instellingen → Beveiliging.",
                "/instellingen/beveiliging", count=len(admins),
                rows=[{"id": str(a["id"]), "title": a["username"],
                       "detail": "Beheerder zonder tweestapsverificatie"} for a in admins]))
    return out


def twofa_nudge(ctx: AppContext, principal: Principal) -> bool:
    """Herinnering tonen? Beheerder zonder 2FA, niet verplicht gesteld, herinnering aan."""
    return bool(principal.user_id and principal.role == "admin" and not principal.machine
                and bool(ctx.config.value("security.nudge_2fa"))
                and not bool(ctx.config.value("security.require_2fa_admins"))
                and not totp_enabled(ctx, int(principal.user_id)))


def _user(principal: Principal) -> int:
    if principal.machine or not principal.user_id:
        raise HTTPException(403, "Alleen voor ingelogde gebruikers")
    return int(principal.user_id)


def _admin(principal: Principal) -> None:
    if principal.role != "admin":
        raise HTTPException(403, "Rol 'admin' vereist")


# -- TOTP ---------------------------------------------------------------------------------------
@router.get("/me")
def me(principal: Principal = Depends(current_principal),
       ctx: AppContext = Depends(get_app_context)) -> dict[str, Any]:
    uid = _user(principal)
    row = ctx.db.fetch_one("SELECT * FROM user_totp WHERE user_id=?", (uid,))
    enabled = bool(row and row["enabled"])
    return {"totp_enabled": enabled,
            "recovery_codes_left": len(loads(row["recovery_hashes_json"], [])) if row else 0,
            "enabled_at": row["enabled_at"] if row else None,
            "required": twofa_required(ctx, principal) or (
                enabled is False and principal.role == "admin"
                and bool(ctx.config.value("security.require_2fa_admins"))),
            "require_2fa_admins": bool(ctx.config.value("security.require_2fa_admins")),
            "notify_new_login": bool(ctx.config.value("security.notify_new_login")),
            "nudge": twofa_nudge(ctx, principal)}


@router.post("/totp/setup")
def totp_setup(principal: Principal = Depends(current_principal),
               ctx: AppContext = Depends(get_app_context)) -> dict[str, Any]:
    uid = _user(principal)
    secret = totp.new_secret()
    ctx.secrets.set(_secret_name(uid, pending=True), secret, actor=principal.actor)
    uri = totp.otpauth_uri(secret, principal.username)
    from mediaguard.notify.service import qr_data_uri

    return {"secret": secret, "otpauth_uri": uri, "qr_svg": qr_data_uri(uri)}


class CodeBody(BaseModel):
    code: str = Field(min_length=6, max_length=20)


@router.post("/totp/enable")
def totp_enable(body: CodeBody, principal: Principal = Depends(current_principal),
                ctx: AppContext = Depends(get_app_context)) -> dict[str, Any]:
    uid = _user(principal)
    pending = ctx.secrets.get(_secret_name(uid, pending=True))
    if not pending:
        raise HTTPException(409, "Start eerst het instellen (QR-code)")
    counter = totp.verify(pending, body.code)
    if counter is None:
        raise HTTPException(422, "Onjuiste code; controleer de tijd op je telefoon")
    codes = totp.new_recovery_codes()
    ctx.secrets.set(_secret_name(uid), pending, actor=principal.actor)
    ctx.secrets.delete(_secret_name(uid, pending=True))
    ctx.db.execute(
        "INSERT INTO user_totp(user_id,enabled,recovery_hashes_json,last_counter,created_at,"
        "enabled_at) VALUES (?,1,?,?,?,?) ON CONFLICT(user_id) DO UPDATE SET enabled=1, "
        "recovery_hashes_json=excluded.recovery_hashes_json, last_counter=excluded.last_counter,"
        " enabled_at=excluded.enabled_at",
        (uid, dumps([totp.hash_recovery(c) for c in codes]), counter, utcnow(), utcnow()))
    audit(ctx.db, actor=principal.actor, action="auth.2fa_enabled", target_type="user",
          target_id=principal.username)
    return {"enabled": True, "recovery_codes": codes}


class DisableBody(BaseModel):
    password: str = Field(min_length=1, max_length=256)


@router.post("/totp/disable")
def totp_disable(body: DisableBody, principal: Principal = Depends(current_principal),
                 ctx: AppContext = Depends(get_app_context)) -> dict[str, bool]:
    uid = _user(principal)
    row = ctx.db.fetch_one("SELECT password_hash FROM users WHERE id=?", (uid,))
    if row is None or not verify_password(body.password, row["password_hash"]):
        raise HTTPException(401, "Onjuist wachtwoord")
    _disable(ctx, uid)
    audit(ctx.db, actor=principal.actor, action="auth.2fa_disabled", target_type="user",
          target_id=principal.username)
    return {"enabled": False}


def _disable(ctx: AppContext, uid: int) -> None:
    ctx.db.execute("DELETE FROM user_totp WHERE user_id=?", (uid,))
    ctx.secrets.delete(_secret_name(uid))
    ctx.secrets.delete(_secret_name(uid, pending=True))


@router.post("/totp/recovery-codes")
def totp_new_codes(body: CodeBody, principal: Principal = Depends(current_principal),
                   ctx: AppContext = Depends(get_app_context)) -> dict[str, Any]:
    uid = _user(principal)
    if not check_code(ctx, uid, body.code):
        raise HTTPException(401, "Onjuiste code")
    codes = totp.new_recovery_codes()
    ctx.db.execute("UPDATE user_totp SET recovery_hashes_json=? WHERE user_id=?",
                   (dumps([totp.hash_recovery(c) for c in codes]), uid))
    return {"recovery_codes": codes}


@router.post("/users/{user_id}/totp/reset")
def totp_reset(user_id: int, principal: Principal = Depends(current_principal),
               ctx: AppContext = Depends(get_app_context)) -> dict[str, bool]:
    """Beheerder zet 2FA uit voor een gebruiker die zijn telefoon kwijt is."""
    _admin(principal)
    _disable(ctx, user_id)
    ctx.db.execute("DELETE FROM sessions WHERE user_id=?", (user_id,))
    audit(ctx.db, actor=principal.actor, action="auth.2fa_reset", target_type="user",
          target_id=str(user_id))
    return {"reset": True}


class PolicyBody(BaseModel):
    require_2fa_admins: bool | None = None
    notify_new_login: bool | None = None


@router.put("/policy")
def set_policy(body: PolicyBody, principal: Principal = Depends(current_principal),
               ctx: AppContext = Depends(get_app_context)) -> dict[str, bool]:
    _admin(principal)
    if body.require_2fa_admins is True and principal.user_id and \
            not totp_enabled(ctx, int(principal.user_id)):
        raise HTTPException(409, "Zet eerst voor jezelf tweestapsverificatie aan")
    for key, value in (("security.require_2fa_admins", body.require_2fa_admins),
                       ("security.notify_new_login", body.notify_new_login)):
        if value is not None:
            ctx.config.set("global", "", key, value, principal.actor)
    return {"require_2fa_admins": bool(ctx.config.value("security.require_2fa_admins")),
            "notify_new_login": bool(ctx.config.value("security.notify_new_login"))}


# -- sessies ------------------------------------------------------------------------------------
def _session_id(token_hash: str) -> str:
    return token_hash[:16]


@router.get("/sessions")
def sessions(request: Request, all_users: bool = False,
             principal: Principal = Depends(current_principal),
             ctx: AppContext = Depends(get_app_context)) -> dict[str, Any]:
    uid = _user(principal)
    if all_users:
        _admin(principal)
    current = request.cookies.get(SESSION_COOKIE)
    current_hash = hashlib.sha256(current.encode()).hexdigest() if current else ""
    sql = ("SELECT s.token_hash, s.created_at, s.expires_at, s.last_seen_at, s.ip, "
           "s.user_agent, u.username, u.id AS user_id FROM sessions s JOIN users u ON "
           "u.id=s.user_id WHERE s.expires_at>?")
    args: list[Any] = [utcnow()]
    if not all_users:
        sql += " AND s.user_id=?"
        args.append(uid)
    items = []
    for r in ctx.db.fetch_dicts(sql + " ORDER BY COALESCE(s.last_seen_at, s.created_at) DESC",
                                tuple(args)):
        items.append({"id": _session_id(r["token_hash"]), "username": r["username"],
                      "user_id": r["user_id"], "created_at": r["created_at"],
                      "last_seen_at": r["last_seen_at"] or r["created_at"], "ip": r["ip"],
                      "device_nl": _device(r["user_agent"] or ""),
                      "current": r["token_hash"] == current_hash})
    return {"items": items}


@router.delete("/sessions/{session_id}")
def revoke_session(session_id: str, principal: Principal = Depends(current_principal),
                   ctx: AppContext = Depends(get_app_context)) -> dict[str, int]:
    uid = _user(principal)
    if len(session_id) < 8:
        raise HTTPException(404, "Sessie niet gevonden")
    row = ctx.db.fetch_one("SELECT token_hash, user_id FROM sessions WHERE token_hash LIKE ?",
                           (session_id + "%",))
    if row is None:
        raise HTTPException(404, "Sessie niet gevonden")
    if int(row["user_id"]) != uid:
        _admin(principal)
    n = ctx.db.execute("DELETE FROM sessions WHERE token_hash=?", (row["token_hash"],))
    audit(ctx.db, actor=principal.actor, action="auth.session_revoked", target_type="user",
          target_id=str(row["user_id"]))
    return {"revoked": n}


@router.post("/sessions/revoke-others")
def revoke_others(request: Request, principal: Principal = Depends(current_principal),
                  ctx: AppContext = Depends(get_app_context)) -> dict[str, int]:
    uid = _user(principal)
    current = request.cookies.get(SESSION_COOKIE) or ""
    n = ctx.db.execute("DELETE FROM sessions WHERE user_id=? AND token_hash<>?",
                       (uid, hashlib.sha256(current.encode()).hexdigest()))
    audit(ctx.db, actor=principal.actor, action="auth.sessions_revoked_others",
          target_type="user", target_id=principal.username)
    return {"revoked": n}


@router.get("/logins")
def logins(limit: int = 50, all_users: bool = False,
           principal: Principal = Depends(current_principal),
           ctx: AppContext = Depends(get_app_context)) -> dict[str, Any]:
    uid = _user(principal)
    limit = max(1, min(limit, 200))
    if all_users:
        _admin(principal)
        rows = ctx.db.fetch_dicts("SELECT * FROM login_history ORDER BY id DESC LIMIT ?",
                                  (limit,))
    else:
        rows = ctx.db.fetch_dicts("SELECT * FROM login_history WHERE user_id=? OR "
                                  "(user_id IS NULL AND username=?) ORDER BY id DESC LIMIT ?",
                                  (uid, principal.username, limit))
    method_nl = {"password": "Wachtwoord", "totp": "Wachtwoord + code",
                 "recovery": "Herstelcode"}
    return {"items": [{"id": r["id"], "username": r["username"], "ts": r["ts"], "ip": r["ip"],
                       "device_nl": _device(r["user_agent"] or ""),
                       "success": bool(r["success"]),
                       "method_nl": method_nl.get(r["method"], r["method"]),
                       "reason": r["reason"]} for r in rows]}
