"""Noodtoegang: weer binnenkomen als inloggen niet meer lukt.

Voor als je je wachtwoord kwijt bent, je telefoon met de authenticator-app weg is (en de
herstelcodes ook), of als er geen beheerder meer is. Werkt alleen met toegang tot de container
(TrueNAS: *Apps → MediaGuard → ⋮ → Shell*), dus nooit via internet.

Gebruik (in de container)::

    mediaguard-noodtoegang status
    mediaguard-noodtoegang wachtwoord <gebruiker> [--2fa-uit]
    mediaguard-noodtoegang 2fa-uit <gebruiker>
    mediaguard-noodtoegang uitloggen [<gebruiker>]
    mediaguard-noodtoegang beheerder <gebruiker>

of ``/opt/venv/bin/python -m mediaguard.noodtoegang …``. Een nieuw wachtwoord wordt gevraagd
zonder dat het op het scherm verschijnt (of via ``--wachtwoord-stdin``). ``wachtwoord``,
``2fa-uit`` en ``uitloggen`` loggen alle sessies van die gebruiker uit; elke wijziging komt in
het auditlog (actor ``cli:noodtoegang``).

Draai je dit als root, dan schakelt het eerst over naar de eigenaar van de database
(standaard 568), zodat core de bestanden daarna gewoon kan blijven schrijven.
"""

from __future__ import annotations

import argparse
import getpass
import os
import sys
from pathlib import Path

ACTOR = "cli:noodtoegang"
CANDIDATES = ("/config/core/mediaguard.db", "/config/mediaguard.db")


def find_db(explicit: str | None = None) -> Path | None:
    if explicit:
        return Path(explicit)
    data_dir = os.environ.get("MG_DATA_DIR")
    if data_dir and (Path(data_dir) / "mediaguard.db").exists():
        return Path(data_dir) / "mediaguard.db"
    for c in CANDIDATES:
        if Path(c).exists():
            return Path(c)
    return None


def _drop_root(db: Path) -> None:  # pragma: no cover - vereist root
    if os.geteuid() != 0:
        return
    st = db.stat()
    if st.st_uid == 0:
        return
    os.setgroups([st.st_gid])
    os.setgid(st.st_gid)
    os.setuid(st.st_uid)


def _open(db: Path):  # type: ignore[no-untyped-def]
    """Bestaande database openen; geen migraties (dat doet core zelf bij de start)."""
    from mediaguard.db import Database

    return Database(db)


def _user(database, username: str):  # type: ignore[no-untyped-def]
    row = database.fetch_one("SELECT id, username, role, disabled FROM users WHERE username=?",
                             (username,))
    if row is None:
        raise SystemExit(f"Gebruiker '{username}' bestaat niet. Bekijk de lijst met: "
                         "mediaguard-noodtoegang status")
    return row


def _end_sessions(database, user_id: int | None = None) -> int:  # type: ignore[no-untyped-def]
    if user_id is None:
        n = database.execute("DELETE FROM sessions")
        database.execute("DELETE FROM login_challenges")
        return int(n)
    n = database.execute("DELETE FROM sessions WHERE user_id=?", (user_id,))
    database.execute("DELETE FROM login_challenges WHERE user_id=?", (user_id,))
    return int(n)


def _disable_2fa(database, data_dir: Path, user_id: int) -> None:  # type: ignore[no-untyped-def]
    from mediaguard.secrets_store import SecretStore

    database.execute("DELETE FROM user_totp WHERE user_id=?", (user_id,))
    store = SecretStore(database, data_dir / "secret.key")
    store.delete(f"auth.totp.{user_id}")
    store.delete(f"auth.totp_pending.{user_id}")


def _audit(database, action: str, target: str, reason: str) -> None:  # type: ignore[no-untyped-def]
    from mediaguard.audit import audit

    audit(database, actor=ACTOR, action=action, target_type="user", target_id=target,
          reason=reason)


def _new_password(args: argparse.Namespace) -> str:
    if args.wachtwoord_stdin:
        value = sys.stdin.readline().rstrip("\n")
    else:  # pragma: no cover - interactief
        value = getpass.getpass("Nieuw wachtwoord (min. 10 tekens): ")
        again = getpass.getpass("Nog een keer: ")
        if value != again:
            raise SystemExit("De twee wachtwoorden zijn niet gelijk; er is niets gewijzigd.")
    if len(value) < 10:
        raise SystemExit("Het wachtwoord moet minimaal 10 tekens hebben; er is niets gewijzigd.")
    return value


def cmd_status(database, data_dir: Path) -> int:  # type: ignore[no-untyped-def]
    from mediaguard.secrets_store import SecretStore

    users = database.fetch_all(
        "SELECT u.id, u.username, u.role, u.disabled, t.enabled AS totp FROM users u "
        "LEFT JOIN user_totp t ON t.user_id=u.id ORDER BY u.id")
    print("Gebruikers:")
    if not users:
        print("  (geen; open de web-app om de eerste beheerder aan te maken)")
    for u in users:
        flags = [u["role"], "2FA aan" if u["totp"] else "2FA uit"]
        if u["disabled"]:
            flags.append("uitgeschakeld")
        n = database.fetch_one("SELECT COUNT(*) AS n FROM sessions WHERE user_id=?", (u["id"],))
        flags.append(f"{int(n['n']) if n else 0} sessie(s)")
        print(f"  {u['username']}: {', '.join(flags)}")
    health = SecretStore(database, data_dir / "secret.key").health()
    if health["ok"]:
        print(f"Geheimen: {health['total']} opgeslagen, allemaal leesbaar (secret.key klopt).")
    else:
        print(f"Geheimen: {len(health['unreadable'])} van de {health['total']} zijn ONLEESBAAR: "  # type: ignore[arg-type]
              f"secret.key ({data_dir / 'secret.key'}) past niet bij deze database. Zet de "
              "juiste secret.key terug (bijv. uit je herstelbundel) of vul de sleutels opnieuw "
              "in via de web-app.")
    return 0


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        prog="mediaguard-noodtoegang",
        description="Noodtoegang tot MediaGuard (alleen vanuit de container-shell).")
    parser.add_argument("--db", help="pad naar mediaguard.db (standaard /config/core)")
    sub = parser.add_subparsers(dest="cmd", required=True)
    sub.add_parser("status", help="gebruikers, 2FA en of secret.key klopt")
    pw = sub.add_parser("wachtwoord", help="nieuw wachtwoord instellen")
    pw.add_argument("gebruiker")
    pw.add_argument("--2fa-uit", dest="twofa_off", action="store_true",
                    help="ook tweestapsverificatie uitzetten (telefoon kwijt)")
    pw.add_argument("--wachtwoord-stdin", action="store_true",
                    help="wachtwoord van stdin lezen i.p.v. vragen")
    off = sub.add_parser("2fa-uit", help="tweestapsverificatie uitzetten")
    off.add_argument("gebruiker")
    out = sub.add_parser("uitloggen", help="alle sessies beëindigen (van één of iedereen)")
    out.add_argument("gebruiker", nargs="?")
    adm = sub.add_parser("beheerder", help="gebruiker beheerder maken en weer inschakelen")
    adm.add_argument("gebruiker")
    args = parser.parse_args(argv)

    db = find_db(args.db)
    if db is None or not db.exists():
        print("Database niet gevonden. Geef het pad op met --db /config/core/mediaguard.db",
              file=sys.stderr)
        return 2
    _drop_root(db)
    data_dir = db.parent
    database = _open(db)
    try:
        if args.cmd == "status":
            return cmd_status(database, data_dir)
        if args.cmd == "uitloggen":
            user_id = int(_user(database, args.gebruiker)["id"]) if args.gebruiker else None
            n = _end_sessions(database, user_id)
            _audit(database, "auth.emergency_logout", args.gebruiker or "*",
                   "Noodtoegang: sessies beëindigd")
            print(f"{n} sessie(s) beëindigd.")
            return 0
        user = _user(database, args.gebruiker)
        uid = int(user["id"])
        if args.cmd == "wachtwoord":
            from mediaguard.auth import hash_password

            password = _new_password(args)
            database.execute("UPDATE users SET password_hash=? WHERE id=?",
                             (hash_password(password), uid))
            if args.twofa_off:
                _disable_2fa(database, data_dir, uid)
            n = _end_sessions(database, uid)
            _audit(database, "auth.emergency_password", user["username"],
                   "Noodtoegang: nieuw wachtwoord" + (" en 2FA uit" if args.twofa_off else ""))
            print(f"Nieuw wachtwoord ingesteld voor {user['username']}"
                  + (", tweestapsverificatie uitgezet" if args.twofa_off else "")
                  + f"; {n} sessie(s) uitgelogd. Log nu in via de web-app.")
            return 0
        if args.cmd == "2fa-uit":
            _disable_2fa(database, data_dir, uid)
            n = _end_sessions(database, uid)
            _audit(database, "auth.emergency_2fa_off", user["username"],
                   "Noodtoegang: tweestapsverificatie uitgezet")
            print(f"Tweestapsverificatie uitgezet voor {user['username']}; {n} sessie(s) "
                  "uitgelogd. Zet hem na het inloggen weer aan (Instellingen → Beveiliging).")
            return 0
        if args.cmd == "beheerder":
            database.execute("UPDATE users SET role='admin', disabled=0 WHERE id=?", (uid,))
            _audit(database, "auth.emergency_admin", user["username"],
                   "Noodtoegang: rol beheerder en ingeschakeld")
            print(f"{user['username']} is nu beheerder en ingeschakeld.")
            return 0
    finally:
        database.close()
    return 1  # pragma: no cover


if __name__ == "__main__":  # pragma: no cover
    sys.exit(main())
