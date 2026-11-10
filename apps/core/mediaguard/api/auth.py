"""Login, eerste-installatie (admin aanmaken), gebruikersbeheer."""

from __future__ import annotations

from typing import Any, Literal

from fastapi import APIRouter, Depends, HTTPException, Request, Response
from pydantic import BaseModel, Field

from mediaguard.audit import audit
from mediaguard.auth import (
    SESSION_COOKIE,
    SESSION_DAYS,
    Principal,
    create_session,
    current_principal,
    delete_session,
    hash_password,
    require_admin,
    verify_password,
)
from mediaguard.context import AppContext, get_app_context
from mediaguard.db import dumps, loads, utcnow

router = APIRouter(prefix="/api/v1/auth", tags=["auth"])


class Credentials(BaseModel):
    username: str = Field(min_length=2, max_length=64)
    password: str = Field(min_length=1, max_length=256)


class NewUser(Credentials):
    role: Literal["admin", "operator", "viewer"] = "viewer"


def _set_cookie(request: Request, response: Response, token: str) -> None:
    """Secure zodra de browser https gebruikt: direct, of via een vertrouwde proxy zoals
    cloudflared (X-Forwarded-Proto). Zie mediaguard.security.net."""
    from mediaguard.security.net import is_https

    response.set_cookie(SESSION_COOKIE, token, max_age=SESSION_DAYS * 86400, httponly=True,
                        samesite="strict", secure=is_https(request), path="/")


@router.get("/setup-status")
def setup_status(ctx: AppContext = Depends(get_app_context)) -> dict[str, bool]:
    row = ctx.db.fetch_one("SELECT COUNT(*) AS n FROM users")
    return {"needs_setup": int(row["n"]) == 0 if row else True}


@router.post("/setup")
def first_setup(body: Credentials, request: Request, response: Response,
                ctx: AppContext = Depends(get_app_context)) -> dict[str, str]:
    """Eenmalig: eerste beheerder aanmaken. Daarna gesloten."""
    with ctx.db.transaction() as conn:
        if conn.execute("SELECT COUNT(*) FROM users").fetchone()[0] > 0:
            raise HTTPException(409, "Installatie is al voltooid")
        conn.execute(
            "INSERT INTO users(username,password_hash,role,created_at) VALUES (?,?,?,?)",
            (body.username, hash_password(body.password), "admin", utcnow()),
        )
    user = ctx.db.fetch_one("SELECT id FROM users WHERE username=?", (body.username,))
    assert user is not None
    from mediaguard.security import api as sec

    token, csrf = create_session(ctx.db, int(user["id"]), ip=sec.client_ip(request),
                                 user_agent=sec.user_agent(request))
    _set_cookie(request, response, token)
    # telt als aanmelding: dit adres is daarna 'bekend' voor de inlogrem
    sec.record_login(ctx, request, username=body.username, user_id=int(user["id"]),
                     success=True, method="password", reason="Eerste beheerder aangemaakt")
    audit(ctx.db, actor=f"user:{body.username}", action="auth.setup", target_type="user",
          target_id=body.username, reason="Eerste beheerder aangemaakt")
    return {"csrf_token": csrf, "role": "admin", "username": body.username}


@router.post("/login")
def login(body: Credentials, request: Request, response: Response,
          ctx: AppContext = Depends(get_app_context)) -> dict[str, Any]:
    from mediaguard.security import api as sec
    from mediaguard.security import limiter

    ip = sec.client_ip(request)
    # eerst de rem (vóór het wachtwoord gecontroleerd wordt): geen raadmachine via de tunnel.
    # Controleren, nakijken en vastleggen gebeuren samen (gelijktijdige gokken tellen mee).
    with limiter.attempt(ctx, ip=ip, username=body.username):
        row = ctx.db.fetch_one("SELECT * FROM users WHERE username=?", (body.username,))
        if row is None or row["disabled"] or not verify_password(body.password,
                                                                 row["password_hash"]):
            audit(ctx.db, actor=f"anon:{body.username}", action="auth.login_failed")
            sec.record_login(ctx, request, username=body.username,
                             user_id=int(row["id"]) if row else None, success=False,
                             reason="Onjuist wachtwoord" if row else "Onbekende gebruiker")
            limiter.after_failure(ctx, ip=ip, username=body.username)
            raise HTTPException(401, "Onjuiste gebruikersnaam of wachtwoord")
    if sec.totp_enabled(ctx, int(row["id"])):
        # tweede stap: nog geen sessie, alleen een kortlevende uitdaging
        return {"needs_2fa": True, "challenge": sec.start_challenge(ctx, request, int(row["id"])),
                "username": row["username"]}
    return _finish_login(ctx, request, response, row, method="password")


def _finish_login(ctx: AppContext, request: Request, response: Response, row: Any,
                  *, method: str) -> dict[str, Any]:
    from mediaguard.security import api as sec

    token, csrf = create_session(ctx.db, int(row["id"]), ip=sec.client_ip(request),
                                 user_agent=sec.user_agent(request))
    _set_cookie(request, response, token)
    sec.record_login(ctx, request, username=row["username"], user_id=int(row["id"]),
                     success=True, method=method)
    nudge = (row["role"] == "admin" and method == "password"
             and bool(ctx.config.value("security.nudge_2fa"))
             and not bool(ctx.config.value("security.require_2fa_admins")))
    return {"csrf_token": csrf, "role": row["role"], "username": row["username"],
            "twofa_nudge": nudge}


class SecondFactor(BaseModel):
    challenge: str = Field(min_length=10, max_length=200)
    code: str = Field(min_length=6, max_length=20)


@router.post("/login/2fa")
def login_2fa(body: SecondFactor, request: Request, response: Response,
              ctx: AppContext = Depends(get_app_context)) -> dict[str, Any]:
    from mediaguard.security import api as sec

    user_id = sec.finish_challenge(ctx, body.challenge, body.code, request)
    row = ctx.db.fetch_one("SELECT * FROM users WHERE id=?", (user_id,))
    if row is None or row["disabled"]:
        raise HTTPException(401, "Gebruiker niet (meer) actief")
    method = "recovery" if "-" in body.code or len(body.code.strip()) > 8 else "totp"
    return _finish_login(ctx, request, response, row, method=method)


@router.post("/logout")
def logout(request: Request, response: Response,
           ctx: AppContext = Depends(get_app_context)) -> dict[str, bool]:
    token = request.cookies.get(SESSION_COOKIE)
    if token:
        delete_session(ctx.db, token)
    from mediaguard.security.net import is_https

    response.delete_cookie(SESSION_COOKIE, path="/", secure=is_https(request), httponly=True,
                           samesite="strict")
    return {"ok": True}


@router.get("/me")
def me(principal: Principal = Depends(current_principal),
       ctx: AppContext = Depends(get_app_context)) -> dict[str, object]:
    locale = "nl-NL"
    if principal.user_id:
        row = ctx.db.fetch_one("SELECT locale FROM users WHERE id=?", (principal.user_id,))
        locale = row["locale"] if row else locale
    prefs = _preferences(ctx, principal.user_id)
    # thema meteen meesturen: scheelt de UI een extra verzoek bij elke volledige laadbeurt
    theme = prefs.get("ui.theme") or ctx.config.value("ui.theme")
    from mediaguard.security import api as sec

    return {"username": principal.username, "role": principal.role,
            "csrf_token": principal.csrf_token, "locale": locale,
            "preferences": prefs, "theme": theme,
            "twofa_enabled": bool(principal.user_id and
                                  sec.totp_enabled(ctx, int(principal.user_id))),
            "twofa_required": sec.twofa_required(ctx, principal),
            "twofa_nudge": sec.twofa_nudge(ctx, principal)}


def _preferences(ctx: AppContext, user_id: int | None) -> dict[str, object]:
    if not user_id:
        return {}
    rows = ctx.db.fetch_all("SELECT key, value_json FROM user_preferences WHERE user_id=?",
                            (user_id,))
    return {r["key"]: loads(r["value_json"]) for r in rows}


class PreferencesBody(BaseModel):
    """Per-gebruiker UI-voorkeuren (server-side; de browser cachet ze alleen)."""

    model_config = {"extra": "forbid"}
    theme: Literal["dark", "light", "system"] | None = None


@router.put("/me/preferences")
def set_preferences(body: PreferencesBody, principal: Principal = Depends(current_principal),
                    ctx: AppContext = Depends(get_app_context)) -> dict[str, object]:
    if not principal.user_id:
        raise HTTPException(409, "Voorkeuren zijn alleen voor ingelogde gebruikers")
    for key, value in body.model_dump(exclude_unset=True).items():
        if value is None:
            ctx.db.execute("DELETE FROM user_preferences WHERE user_id=? AND key=?",
                           (principal.user_id, key))
        else:
            ctx.db.execute(
                "INSERT INTO user_preferences(user_id,key,value_json) VALUES (?,?,?) "
                "ON CONFLICT(user_id,key) DO UPDATE SET value_json=excluded.value_json",
                (principal.user_id, key, dumps(value)))
    return {"preferences": _preferences(ctx, principal.user_id)}


class LocaleBody(BaseModel):
    locale: Literal["nl-NL", "en-US"]


@router.put("/me/locale")
def set_locale(body: LocaleBody, principal: Principal = Depends(current_principal),
               ctx: AppContext = Depends(get_app_context)) -> dict[str, str]:
    if principal.user_id:
        ctx.db.execute("UPDATE users SET locale=? WHERE id=?", (body.locale, principal.user_id))
    return {"locale": body.locale}


@router.get("/users")
def list_users(_: Principal = Depends(require_admin),
               ctx: AppContext = Depends(get_app_context)) -> list[dict[str, object]]:
    return ctx.db.fetch_dicts(
        "SELECT id,username,role,locale,disabled,created_at,last_login_at FROM users ORDER BY id")


@router.post("/users")
def add_user(body: NewUser, principal: Principal = Depends(require_admin),
             ctx: AppContext = Depends(get_app_context)) -> dict[str, object]:
    if ctx.db.fetch_one("SELECT 1 FROM users WHERE username=?", (body.username,)):
        raise HTTPException(409, "Gebruikersnaam bestaat al")
    uid = ctx.db.execute_insert(
        "INSERT INTO users(username,password_hash,role,created_at) VALUES (?,?,?,?)",
        (body.username, hash_password(body.password), body.role, utcnow()))
    audit(ctx.db, actor=principal.actor, action="user.create", target_type="user",
          target_id=body.username, new_value={"role": body.role})
    return {"id": uid, "username": body.username, "role": body.role}


class UserPatch(BaseModel):
    role: Literal["admin", "operator", "viewer"] | None = None
    disabled: bool | None = None
    password: str | None = None


@router.patch("/users/{user_id}")
def patch_user(user_id: int, body: UserPatch, request: Request,
               principal: Principal = Depends(require_admin),
               ctx: AppContext = Depends(get_app_context)) -> dict[str, Any]:
    row = ctx.db.fetch_one("SELECT * FROM users WHERE id=?", (user_id,))
    if row is None:
        raise HTTPException(404, "Gebruiker niet gevonden")
    if body.role is not None:
        ctx.db.execute("UPDATE users SET role=? WHERE id=?", (body.role, user_id))
    if body.disabled is not None:
        if body.disabled and principal.user_id == user_id:
            raise HTTPException(400, "Je kunt jezelf niet uitschakelen")
        ctx.db.execute("UPDATE users SET disabled=? WHERE id=?", (int(body.disabled), user_id))
        if body.disabled:
            ctx.db.execute("DELETE FROM sessions WHERE user_id=?", (user_id,))
    logged_out = 0
    if body.password:
        ctx.db.execute("UPDATE users SET password_hash=? WHERE id=?",
                       (hash_password(body.password), user_id))
        # nieuw wachtwoord = alle andere apparaten van die gebruiker uitloggen (je eigen
        # sessie blijft, als je je eigen wachtwoord wijzigt)
        logged_out = end_other_sessions(ctx, user_id, request)
    audit(ctx.db, actor=principal.actor, action="user.update", target_type="user",
          target_id=row["username"], old_value={"role": row["role"], "disabled": row["disabled"]},
          new_value={**body.model_dump(exclude={"password"}, exclude_none=True),
                     **({"password_changed": True, "sessions_ended": logged_out}
                        if body.password else {})})
    return {"ok": True, "sessions_ended": logged_out}


def end_other_sessions(ctx: AppContext, user_id: int, request: Request | None = None) -> int:
    """Alle sessies en openstaande 2FA-uitdagingen van een gebruiker beëindigen, behalve de
    sessie van dit verzoek."""
    import hashlib

    current = request.cookies.get(SESSION_COOKIE) if request is not None else None
    keep = hashlib.sha256(current.encode()).hexdigest() if current else ""
    n = ctx.db.execute("DELETE FROM sessions WHERE user_id=? AND token_hash<>?",
                       (user_id, keep))
    ctx.db.execute("DELETE FROM login_challenges WHERE user_id=?", (user_id,))
    return n


class PasswordChange(BaseModel):
    current_password: str = Field(min_length=1, max_length=256)
    new_password: str = Field(min_length=10, max_length=256)


@router.post("/me/password")
def change_own_password(body: PasswordChange, request: Request,
                        principal: Principal = Depends(current_principal),
                        ctx: AppContext = Depends(get_app_context)) -> dict[str, Any]:
    """Eigen wachtwoord wijzigen (huidig wachtwoord vereist). Andere apparaten worden
    uitgelogd; dit apparaat blijft ingelogd."""
    from mediaguard.security import api as sec
    from mediaguard.security import limiter

    if principal.machine or not principal.user_id:
        raise HTTPException(403, "Alleen voor ingelogde gebruikers")
    ip = sec.client_ip(request)
    with limiter.attempt(ctx, ip=ip, username=principal.username):
        row = ctx.db.fetch_one("SELECT password_hash FROM users WHERE id=?",
                               (principal.user_id,))
        if row is None or not verify_password(body.current_password, row["password_hash"]):
            sec.record_login(ctx, request, username=principal.username,
                             user_id=principal.user_id, success=False, method="password",
                             reason="Onjuist huidig wachtwoord bij wijzigen")
            limiter.after_failure(ctx, ip=ip, username=principal.username)
            raise HTTPException(401, "Huidig wachtwoord klopt niet")
    if body.new_password == body.current_password:
        raise HTTPException(422, "Kies een ander wachtwoord dan het huidige")
    ctx.db.execute("UPDATE users SET password_hash=? WHERE id=?",
                   (hash_password(body.new_password), principal.user_id))
    n = end_other_sessions(ctx, int(principal.user_id), request)
    audit(ctx.db, actor=principal.actor, action="auth.password_changed", target_type="user",
          target_id=principal.username, new_value={"sessions_ended": n})
    return {"ok": True, "sessions_ended": n,
            "message_nl": "Wachtwoord gewijzigd" + (
                f"; {n} ander{'e' if n != 1 else ''} apparaat"
                f"{'en' if n != 1 else ''} uitgelogd" if n else "")}


# -- API-sleutels (alleen admin) --------------------------------------------------------------
class NewApiKey(BaseModel):
    """Rol 'admin' bestaat bewust niet voor sleutels: die geeft 422."""

    model_config = {"extra": "forbid"}
    name: str = Field(min_length=2, max_length=64, pattern=r"^[A-Za-z0-9][A-Za-z0-9 ._-]*$")
    role: Literal["viewer", "operator"] = "operator"
    expires_in_days: int | None = Field(default=None, ge=1, le=3650)


@router.get("/api-keys")
def list_api_keys(_: Principal = Depends(require_admin),
                  ctx: AppContext = Depends(get_app_context)) -> dict[str, object]:
    from mediaguard import apikeys

    return {"items": apikeys.list_keys(ctx)}


@router.post("/api-keys")
def create_api_key(body: NewApiKey, principal: Principal = Depends(require_admin),
                   ctx: AppContext = Depends(get_app_context)) -> dict[str, object]:
    """Alleen dit antwoord bevat ``key``: de sleutel wordt niet opnieuw getoond."""
    from mediaguard import apikeys

    return apikeys.create_key(ctx, name=body.name.strip(), role=body.role,
                              expires_in_days=body.expires_in_days, actor=principal.actor)


@router.post("/api-keys/{key_id}/revoke")
def revoke_api_key(key_id: int, principal: Principal = Depends(require_admin),
                   ctx: AppContext = Depends(get_app_context)) -> dict[str, object]:
    from mediaguard import apikeys

    return apikeys.revoke_key(ctx, key_id, actor=principal.actor)
