"""Eenmalige installatiestap (container ``mediaguard-init``): mappen aanmaken, rechten zetten
en alle geheimen genereren, zodat de gebruiker niets met de hand hoeft te maken.

Draait als root met alleen CHOWN/DAC_OVERRIDE/FOWNER en stopt daarna. Raakt NOOIT bestaande
mediabestanden aan: in de datamap worden alleen ontbrekende mappen aangemaakt (die dan
eigendom van de app-gebruiker worden); bestaande mappen blijven ongewijzigd.

Indeling van de geheimen (``/secrets`` = ``${MG_APPDATA}/secrets``):
    internal/token                 core ⇄ recovery ⇄ encoder
    <instantie>/apikey             Sonarr/Radarr API-sleutel (32 hex)
    <instantie>/hookkey            hook-sleutel fork → core
Bestaande bestanden worden nooit overschreven (idempotent; herstart = zelfde sleutels).
"""

from __future__ import annotations

import os
import secrets
import sys
from pathlib import Path

INSTANCES = ("sonarr-series", "sonarr-anime", "radarr-films", "radarr-animefilms")


def _uid_gid() -> tuple[int, int]:
    return int(os.environ.get("MG_UID", "568")), int(os.environ.get("MG_GID", "568"))


def _ensure_dir(path: Path, uid: int, gid: int, mode: int = 0o750) -> bool:
    """Maakt een ontbrekende map aan met de app-gebruiker als eigenaar. Een bestaande map
    wordt alleen ge-chownd als hij leeg is én root-eigendom (door Docker aangemaakt)."""
    created = False
    if not path.exists():
        path.mkdir(parents=True, mode=mode)
        created = True
    st = path.stat()
    if created or (st.st_uid == 0 and not any(path.iterdir())):
        _set_owner(path, uid, gid, mode)
    return created


def _set_owner(path: Path, uid: int, gid: int, mode: int | None = None) -> None:
    """Eigenaar/rechten zetten. Op TrueNAS-datasets met NFSv4-ACL's (bv. de SMB-preset,
    aclmode=restricted) weigert ZFS chmod/chown; de ACL regelt dan de rechten. Dat is geen
    fout: we gaan door en melden het."""
    try:
        os.chown(path, uid, gid)
    except PermissionError as exc:
        print(f"mediaguard-init: eigenaar niet gewijzigd (ACL beheert rechten): {path}: {exc}",
              flush=True)
    if mode is not None:
        try:
            os.chmod(path, mode)
        except PermissionError as exc:
            print(f"mediaguard-init: rechten niet gewijzigd (ACL beheert rechten): {path}: "
                  f"{exc}", flush=True)


def _write_secret(path: Path, value: str, uid: int, gid: int) -> bool:
    if path.exists() and path.read_text().strip():
        return False
    fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o640)
    with os.fdopen(fd, "w") as handle:
        handle.write(value + "\n")
    _set_owner(path, uid, gid)
    return True


def run(secrets_dir: Path, app_dirs: list[Path], data_dirs: list[Path]) -> list[str]:
    uid, gid = _uid_gid()
    log: list[str] = []
    for d in app_dirs:
        if _ensure_dir(d, uid, gid):
            log.append(f"map aangemaakt: {d}")
    _ensure_dir(secrets_dir, uid, gid)
    _ensure_dir(secrets_dir / "internal", uid, gid)
    if _write_secret(secrets_dir / "internal" / "token", secrets.token_hex(32), uid, gid):
        log.append("interne token gegenereerd")
    for inst in INSTANCES:
        d = secrets_dir / inst
        _ensure_dir(d, uid, gid)
        if _write_secret(d / "apikey", secrets.token_hex(16), uid, gid):
            log.append(f"API-sleutel gegenereerd voor {inst}")
        if _write_secret(d / "hookkey", secrets.token_hex(32), uid, gid):
            log.append(f"hook-sleutel gegenereerd voor {inst}")
    for d in data_dirs:
        # mediamappen: alleen aanmaken als ze ontbreken, nooit bestaande wijzigen. Geen
        # schrijfrecht op de media-dataset blokkeert de sleutels en de app niet.
        if d.exists():
            continue
        try:
            _ensure_dir(d, uid, gid, 0o775)
            log.append(f"map aangemaakt: {d}")
        except PermissionError as exc:
            log.append(f"WAARSCHUWING: map niet aangemaakt ({exc}); geef gebruiker 'apps' "
                       "(568) schrijfrechten op de media-dataset")
            break
    return log


def load_secret_files(environ: dict[str, str] | None = None) -> None:
    """Zet sleutels uit ``MG_SECRETS_DIR`` in de omgeving (alleen als ze daar nog niet
    expliciet staan). Zo blijven de bestaande env-gebaseerde paden werken."""
    env = os.environ if environ is None else environ
    base = env.get("MG_SECRETS_DIR")
    if not base:
        return
    root = Path(base)

    def read(p: Path) -> str | None:
        try:
            value = p.read_text().strip()
        except OSError:
            return None
        return value or None

    if not env.get("MG_INTERNAL_TOKEN"):
        token = read(root / "internal" / "token") or read(root / "token")
        if token:
            env["MG_INTERNAL_TOKEN"] = token
    for inst in INSTANCES:
        key = inst.upper().replace("-", "_")
        for name, var in (("apikey", f"MG_INSTANCE_{key}_APIKEY"),
                          ("hookkey", f"MG_INSTANCE_{key}_HOOKKEY")):
            if not env.get(var):
                value = read(root / inst / name)
                if value:
                    env[var] = value


def main() -> int:
    secrets_dir = Path(os.environ.get("MG_BOOTSTRAP_SECRETS", "/bootstrap/appdata/secrets"))
    app_root = Path(os.environ.get("MG_BOOTSTRAP_APPDATA", "/bootstrap/appdata"))
    work_root = Path(os.environ.get("MG_BOOTSTRAP_WORK", "/bootstrap/work"))
    data_root = Path(os.environ.get("MG_BOOTSTRAP_DATA", "/bootstrap/data"))
    app_dirs = [app_root / n for n in ("core", "encode", *INSTANCES)]
    app_dirs += [work_root / n for n in ("encode", "encode/staging", "encode/cache",
                                         "quarantine")]
    data_dirs = [data_root / "media" / n for n in ("tv", "anime", "movies", "animefilms")]
    data_dirs += [data_root / "downloads" / n for n in ("tv", "tv-anime", "movies",
                                                         "movies-anime")]
    # Mediamappen alleen voorstellen op een LEGE data-dataset (verse installatie). Een bestaande
    # bibliotheek met eigen indeling blijft precies zoals hij is.
    try:
        fresh = data_root.exists() and not any(data_root.iterdir())
    except PermissionError as exc:
        # Geen leesrecht op de data-dataset: niets aanmaken, wel sleutels genereren. De UI
        # meldt daarna "Hostactie nodig" voor de mediamappen.
        print(f"mediaguard-init: WAARSCHUWING: geen leesrecht op de data-dataset ({exc}). "
              "Geef gebruiker 'apps' (568) lees- en schrijfrechten via de dataset-ACL.",
              flush=True)
        fresh = False
    for line in run(secrets_dir, app_dirs, data_dirs if fresh else []) or \
            ["alles was al ingericht"]:
        print(f"mediaguard-init: {line}", flush=True)
    return 0


if __name__ == "__main__":
    try:
        sys.exit(main())
    except OSError as exc:
        # duidelijke melding in het TrueNAS-log in plaats van alleen 'exit 1'
        print(f"mediaguard-init: FOUT: {exc}. Controleer of de drie paden onderaan de "
              "compose bestaan en of gebruiker 'apps' (568) daar mag schrijven.", flush=True)
        sys.exit(1)
