"""Alles-in-één-start (image ``ghcr.io/lvos04/mediaguard-core:<versie>-aio``): één container
voor TrueNAS' gewone *Custom App*-formulier, zonder YAML, volumes of init-container.

Dit proces draait als PID 1 onder tini en doet:

1. mappen en sleutels aanmaken (``mediaguard.bootstrap``). Een fout hier is NOOIT fataal:
   de app start toch en de UI meldt wat er aan rechten ontbreekt;
2. als root gestart: rechten afgeven aan de app-gebruiker (standaard 568 ``apps``), plus de
   groepen van ``/dev/dri`` als die er is (GPU);
3. de onderdelen starten en bewaken: core, recovery, AV1-encoder, vier Sonarr/Radarr-forks
   en de web-UI. Valt er één weg (bv. na *Herstarten* in Sonarr), dan start hij opnieuw.

Alleen drie mappen in de container zijn van belang: ``/config`` (appdata), ``/work``
(AV1-werkmap en quarantaine) en ``/data`` (media + downloads).
"""

from __future__ import annotations

import contextlib
import json
import os
import re
import shutil
import signal
import stat
import subprocess
import sys
import threading
import time
from collections import deque
from dataclasses import dataclass, field
from pathlib import Path

from mediaguard import bootstrap

INTERNAL_HOST = "127.0.0.1"
CORE_PORT = 38100
RECOVERY_PORT = 38101
ENCODER_PORT = 38000
# hookwacht tussen de forks en core: houdt de noodstop vast terwijl core herstart
HOOKGUARD_PORT = 38102
# de web-server draait als eigen gebruiker zonder toegang tot /config (sleutels, database)
# en zonder de groepen van de app-gebruiker; standaard 'nobody'
WEB_UID = 65534
# beperkte supervisorstatus (alleen core: staat, migratie, uitleg herstartlus) voor de
# web-server, buiten /config in een map van root die alleen leesbaar is voor anderen
WEB_STATE = "/run/mediaguard/core-status.json"

# (instantie, soort, standaardpoort). Afwijkend van 8989/7878 zodat ze naast een bestaande
# Sonarr/Radarr kunnen draaien, ook met 'Host Network'.
FORKS = (
    ("sonarr-series", "sonarr", 38989),
    ("sonarr-anime", "sonarr", 38990),
    ("radarr-films", "radarr", 37878),
    ("radarr-animefilms", "radarr", 37879),
)


# .NET-geheugen voor de vier Sonarr/Radarr-processen (gemeten: ±25 % minder RSS, minder
# opstart-CPU). Globalization-invariant NIET: Radarr crasht daarop. De harde grens is ruim
# (1 GB) en dient alleen als vangnet tegen een op hol geslagen proces.
DOTNET_ENV = {
    "DOTNET_gcServer": "0",
    "DOTNET_gcConcurrent": "0",
    "DOTNET_GCgen0size": "0x400000",
    "DOTNET_GCConserveMemory": "7",
    "DOTNET_TieredPGO": "0",
    "DOTNET_GCHeapHardLimit": "0x40000000",
    "DOTNET_CLI_TELEMETRY_OPTOUT": "1",
}


@dataclass
class Spec:
    name: str
    argv: list[str]
    env: dict[str, str]
    cwd: str = "/"
    # eigen poort; met ``ready_wait`` > 0 wacht de supervisor daarop voor hij verder gaat
    port: int | None = None
    ready_wait: float = 0.0
    # vóór de start op deze poorten wachten (hooguit ``wait_for_timeout`` s samen)
    wait_for: list[int] = field(default_factory=list)
    wait_for_timeout: float = 0.0
    # levenscontrole: URL die binnen 5 s moet antwoorden (< 500); None = alleen procescheck
    health: str | None = None
    # eerste zoveel seconden na de start niet controleren (migraties, opwarmen)
    grace: float = 120.0
    # stopvolgorde: laag eerst (web → forks → encoder → recovery → core)
    stop_rank: int = 5
    # mag bij het stoppen lang afronden (de encoder zet lopende jobs terug); de andere
    # diensten krijgen per rang hooguit FAST_STOP_WAIT voordat de volgende rang aan de beurt is
    slow_stop: bool = False
    restarts: int = 0
    proc: subprocess.Popen[bytes] | None = field(default=None, repr=False)
    started_at: float = 0.0
    next_start: float = 0.0
    fails: int = 0
    last_exit: int | None = None
    hang_restarts: int = 0
    # levenscontrole per dienst: wachttijd per probe en aantal missers vóór herstart
    probe_timeout: float = 5.0
    max_fails: int | None = None
    # de dienst start zware kindprocessen (ffmpeg): zolang die rekenen is hij niet "vast",
    # ook als zijn HTTP-antwoord onder volle belasting traag is
    busy_aware: bool = False
    child_cpu: int = -1
    busy_skips: int = 0
    # core/recovery: nooit herstarten zolang het proces de database migreert
    migration_guard: bool = False
    migrating: dict | None = field(default=None, repr=False)
    # herstarts (wandklok) voor 'herstart steeds opnieuw' + uitleg van een herstartlus
    restart_times: list[float] = field(default_factory=list, repr=False)
    total_restarts: int = 0
    loop_nl: str = ""
    recent: deque = field(default_factory=lambda: deque(maxlen=40), repr=False)
    # eigen (uid, gid) i.p.v. de app-gebruiker, zonder extra groepen (alleen als root gestart)
    user: tuple[int, int] | None = None
    # bestand dat bij een geplande stop (update/herstart) vóór SIGTERM wordt geschreven: de
    # encoder telt een daardoor afgebroken encode dan niet als herstartlus (verbeterpunt 30)
    planned_stop_marker: str | None = None


# Stop Grace Period uit de README (60 s) min een marge, zodat de supervisor zelf klaar is
# vóór Docker hard stopt. Andere waarde: MG_STOP_TIMEOUT (gelijk aan de Stop Grace Period).
STOP_GRACE_DEFAULT = 60
STOP_MARGIN = 5.0
DEFAULT_STOP_BUDGET = STOP_GRACE_DEFAULT - STOP_MARGIN
# Is de Stop Grace Period niet ingesteld, dan killt Docker/TrueNAS na 10 s. Daarom wachten
# snelle diensten per rang hooguit FAST_STOP_WAIT, en krijgen recovery en core hun SIGTERM
# uiterlijk LATE_SIGTERM_AT (+ één snelle rang) na het begin van de stop, ook als de encoder
# nog afrondt. De encoder houdt daarna de rest van het budget.
FAST_STOP_WAIT = 2.0
LATE_SIGTERM_AT = 6.0


def stop_budget(env: dict[str, str]) -> float:
    grace = _env_int(env, "MG_STOP_TIMEOUT", STOP_GRACE_DEFAULT)
    return float(max(5.0, min(3600.0, grace - STOP_MARGIN)))


def _env_int(env: dict[str, str], name: str, default: int) -> int:
    try:
        return int(env.get(name, "") or default)
    except ValueError:
        return default


def _read(path: Path) -> str:
    try:
        return path.read_text().strip()
    except OSError:
        return ""


def detect_lan_ip() -> str:
    """LAN-adres van de TrueNAS-host (met 'Host Network'): het adres waarlangs het verkeer
    naar buiten gaat. Er wordt niets verstuurd (UDP-connect zet alleen de route). Docker-
    bridgeadressen (172.16.0.0/12) tellen niet: die zijn van buitenaf onbereikbaar."""
    import ipaddress
    import socket

    try:
        with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as s:
            s.connect(("10.255.255.255", 1))
            ip = s.getsockname()[0]
    except OSError:
        return ""
    addr = ipaddress.ip_address(ip)
    if addr.is_loopback or addr in ipaddress.ip_network("172.16.0.0/12"):
        return ""
    return ip if addr.is_private else ""


def fork_port(env: dict[str, str], instance: str, default: int) -> int:
    return _env_int(env, f"MG_PORT_{instance.upper().replace('-', '_')}", default)


def build_specs(env: dict[str, str]) -> list[Spec]:
    """Alle diensten met hun omgeving. Pure functie (getest zonder processen te starten)."""
    config = Path(env.get("MG_AIO_CONFIG", "/config"))
    work = Path(env.get("MG_AIO_WORK", "/work"))
    data = env.get("MG_AIO_DATA", "/data")
    secrets_dir = config / "secrets"
    tz = env.get("TZ", "Europe/Amsterdam")
    web_port = _env_int(env, "MG_WEB_PORT", 30080)
    core_url = f"http://{INTERNAL_HOST}:{CORE_PORT}"
    encoder_url = f"http://{INTERNAL_HOST}:{ENCODER_PORT}"
    base = {"TZ": tz, "PATH": env.get("PATH", "/usr/local/bin:/usr/bin:/bin"), "LANG": "C.UTF-8",
            "PYTHONUNBUFFERED": "1"}
    python = env.get("MG_AIO_CORE_PYTHON", "/opt/venv/bin/python")

    core_env = {
        **base,
        "HOME": str(config / "core"),
        "MG_DATA_DIR": str(config / "core"),
        "MG_SECRETS_DIR": str(secrets_dir),
        "MG_LIBRARY_ROOTS": data,
        "MG_STAGING_DIR": str(work / "encode" / "staging"),
        "MG_QUARANTINE_DIR": str(work / "quarantine"),
        "MG_CORE_URL": core_url,
        "MG_ENCODER_URL": encoder_url,
        "MG_RECOVERY_URL": f"http://{INTERNAL_HOST}:{RECOVERY_PORT}",
        "MG_BIND": INTERNAL_HOST,
        # Seerr moet de instanties via het LAN-adres bereiken, niet via een domein achter
        # een reverse proxy/tunnel (daar staan de Sonarr/Radarr-poorten niet open)
        "MG_PUBLIC_HOST": env.get("MG_HOST") or env.get("MG_AIO_LAN_IP", ""),
        # rapport 16: echte logs onder /config/logs (core.log, recovery.log, encoder.log)
        "MG_LOG_DIR": str(config / "logs"),
        # rapport 17: alleen onder de supervisor kan een back-up van vóór een update bij het
        # stoppen worden teruggezet
        "MG_AIO_SUPERVISOR": "1",
    }
    if env.get("MG_RELEASE_MANIFEST"):
        core_env["MG_RELEASE_MANIFEST"] = env["MG_RELEASE_MANIFEST"]
    if env.get("MG_PRE_UPDATE_SET"):
        # de supervisor maakte de set vóór de update al (met de AV1-database erbij)
        core_env["MG_PRE_UPDATE_SET"] = env["MG_PRE_UPDATE_SET"]
    for instance, _kind, default in FORKS:
        key = instance.upper().replace("-", "_")
        port = fork_port(env, instance, default)
        core_env[f"MG_INSTANCE_{key}_URL"] = f"http://{INTERNAL_HOST}:{port}"
        core_env[f"MG_INSTANCE_{key}_PUBLIC_PORT"] = str(port)

    core_specs = [
        Spec("core", [python, "-m", "mediaguard"],
             {**core_env, "MG_ROLE": "core", "MG_PORT": str(CORE_PORT)}, port=CORE_PORT,
             ready_wait=90,
             # Sonarr/Radarr eerst laten opkomen (warm ±4 s, eerste start ±15 s); niet langer
             # dan dit wachten zodat de UI er snel is
             wait_for=[fork_port(env, i, d) for i, _k, d in FORKS],
             wait_for_timeout=float(_env_int(env, "MG_AIO_FORK_WAIT", 20)),
             health=f"http://{INTERNAL_HOST}:{CORE_PORT}/api/v1/health", grace=90,
             stop_rank=9, migration_guard=True),
        Spec("recovery", [python, "-m", "mediaguard"],
             {**core_env, "MG_ROLE": "recovery", "MG_PORT": str(RECOVERY_PORT)},
             health=f"http://{INTERNAL_HOST}:{RECOVERY_PORT}/api/v1/health", grace=90,
             stop_rank=8, migration_guard=True),
    ]
    specs = [
        Spec("encode",
             [env.get("MG_AIO_ENCODER_UVICORN", "/opt/venv/bin/uvicorn"), "app.main:app",
              "--host", INTERNAL_HOST, "--port", str(ENCODER_PORT), "--no-server-header"],
             {
                 **base,
                 "HOME": str(config / "encode"),
                 "MG_CONTAINER_MODE": "1",
                 "AV1_REPLACEMENT_MODE": "handoff",
                 "PIPELINE_ROOT": str(work / "encode"),
                 "CONFIG_ROOT": str(config / "encode"),
                 "MG_STAGING_ROOT": str(work / "encode" / "staging"),
                 "LIBRARY_ROOT": "/media",
                 "MG_MEDIA_ROOTS": data,
                 "SECRET_KEY_FILE": str(config / "encode" / "secrets.key"),
                 "MG_INTERNAL_TOKEN_FILE": str(secrets_dir / "internal" / "token"),
                 "MG_PROTECT_LEGACY_API": "1",
                 "MG_BIND_HOST": INTERNAL_HOST,
                 "MG_PORT": str(ENCODER_PORT),
                 "MG_MIN_STABLE_SECONDS": env.get("MG_ENCODE_MIN_STABLE_SECONDS", "60"),
                 "MG_FFMPEG_HDR10PLUS": env.get("MG_FFMPEG_HDR10PLUS",
                                                "/opt/ffmpeg-hdr10plus/bin/ffmpeg"),
                 # info-regels naar /config/logs/encoder.log; niveau volgt core ('niveau')
                 "MG_LOG_DIR": str(config / "logs"),
             },
             cwd=env.get("MG_AIO_ENCODER_DIR", "/opt/mediaguard-encode"),
             # /livez doet geen werk (geen ffmpeg/vainfo/DB): onder volle belasting antwoordt
             # hij nog. Zolang ffmpeg-kindprocessen rekenen wordt de engine nooit herstart.
             health=f"http://{INTERNAL_HOST}:{ENCODER_PORT}/livez", grace=180, stop_rank=6,
             slow_stop=True, probe_timeout=20.0, max_fails=6, busy_aware=True,
             planned_stop_marker=str(config / "encode" / "geplande-stop")),
    ]
    hook_timeout = _env_int(env, "MG_HOOK_TIMEOUT_MS", 3000)
    hookguard_port = _env_int(env, "MG_HOOKGUARD_PORT", HOOKGUARD_PORT)
    for instance, kind, default in FORKS:
        port = fork_port(env, instance, default)
        prefix = kind.upper()
        binary = "Sonarr" if kind == "sonarr" else "Radarr"
        fork_env = {
            **base,
            "HOME": str(config / instance),
            f"{prefix}__AUTH__APIKEY": _read(secrets_dir / instance / "apikey"),
            f"{prefix}__SERVER__PORT": str(port),
            f"{prefix}__SERVER__BINDADDRESS": "*",
            f"{prefix}__APP__INSTANCENAME": f"{binary} {instance}",
            f"{prefix}__UPDATE__MECHANISM": "Docker",
            f"{prefix}__UPDATE__AUTOMATICALLY": "false",
            f"{prefix}__LOG__ANALYTICSENABLED": "false",
            # geen debuglog (tot ~400 MB schrijfwerk per maand op appdata), kleinere rotatie
            f"{prefix}__LOG__LEVEL": env.get("MG_FORK_LOG_LEVEL", "info"),
            f"{prefix}__LOG__ROTATE": "5",
            f"{prefix}__LOG__CONSOLELEVEL": "warn",
            **DOTNET_ENV,
            # via de hookwacht: tijdens een noodstop geen ongecontroleerde grabs als core
            # herstart (zie mediaguard.hookguard); verder gewoon door naar core
            "MEDIAGUARD_URL": f"http://{INTERNAL_HOST}:{hookguard_port}",
            "MEDIAGUARD_INSTANCE_ID": instance,
            "MEDIAGUARD_HOOK_KEY": _read(secrets_dir / instance / "hookkey"),
            "MEDIAGUARD_HOOK_TIMEOUT_MS": str(hook_timeout),
            "MEDIAGUARD_HOOK_CIRCUIT_BREAK_SECONDS": env.get("MG_HOOK_CIRCUIT_BREAK_SECONDS",
                                                             "30"),
            "MEDIAGUARD_HOOK_FAIL_POLICY": "open",
            "MEDIAGUARD_SHADOW_MODE": "0",
        }
        # forks eerst: core vindt ze dan al draaiend (geen verbindingsfouten bij de start)
        specs.insert(0, Spec(instance, [f"/app/{kind}/bin/{binary}", "-nobrowser",
                                        f"-data={config / instance}"], fork_env, port=port,
                             health=f"http://{INTERNAL_HOST}:{port}/ping", grace=600,
                             stop_rank=3))
    # hookwacht vóór de forks: die vinden hem dan meteen
    specs.insert(0, Spec(
        "hookguard", [python, "-m", "mediaguard.hookguard"],
        {**base, "HOME": str(config / "core"),
         "MG_HOOKGUARD_PORT": str(hookguard_port),
         "MG_HOOKGUARD_CORE": f"{INTERNAL_HOST}:{CORE_PORT}",
         "MG_HOOKGUARD_FLAG": str(config / "core" / "noodstop.json"),
         "MG_HOOKGUARD_DB": str(config / "core" / "mediaguard.db"),
         # iets korter dan de fork wacht, zodat de wachter zelf nog kan antwoorden
         "MG_HOOKGUARD_TIMEOUT_MS": str(max(500, hook_timeout - 500))},
        port=hookguard_port, ready_wait=5,
        health=f"http://{INTERNAL_HOST}:{hookguard_port}/livez", grace=15, stop_rank=4))
    specs += core_specs
    web_uid = _env_int(env, "MG_WEB_UID", WEB_UID)
    web_gid = _env_int(env, "MG_WEB_GID", web_uid)
    specs.append(Spec(
        "web", [env.get("MG_AIO_NODE", "/usr/local/bin/node"), "server.mjs"],
        {**base, "NODE_ENV": "production", "NEXT_TELEMETRY_DISABLED": "1",
         "PORT": str(web_port), "MG_WEB_HOST": "0.0.0.0", "HOSTNAME": "0.0.0.0",
         "MG_CORE_URL": core_url, "HOME": "/tmp",
         # core onbereikbaar: de web-app legt uit waarom (migratie, herstartlus). De web-server
         # (eigen gebruiker) kan niet in /config/core; hij leest een beperkte kopie zonder
         # geheimen die de supervisor in een eigen, alleen-lezen map zet.
         "MG_SUPERVISOR_STATE": env.get("MG_AIO_WEB_STATE", WEB_STATE)},
        cwd=env.get("MG_AIO_WEB_DIR", "/opt/web"),
        health=f"http://{INTERNAL_HOST}:{web_port}/login", grace=60, stop_rank=1,
        user=(web_uid, web_gid)))
    return specs


def prepare(env: dict[str, str]) -> list[str]:
    """Mappen + sleutels. Fouten worden gemeld, nooit fataal."""
    config = Path(env.get("MG_AIO_CONFIG", "/config"))
    work = Path(env.get("MG_AIO_WORK", "/work"))
    data = Path(env.get("MG_AIO_DATA", "/data"))
    warnings: list[str] = []
    for name, path in (("Appdata (/config)", config), ("Werkmap (/work)", work),
                       ("Media (/data)", data)):
        if not path.is_dir() or not os.path.ismount(path):
            warnings.append(f"{name} is niet gekoppeld: voeg in de app een Host Path toe "
                            f"met 'Mount Path' {path}")
    os.environ.update({
        "MG_BOOTSTRAP_SECRETS": str(config / "secrets"),
        "MG_BOOTSTRAP_APPDATA": str(config),
        "MG_BOOTSTRAP_WORK": str(work),
        "MG_BOOTSTRAP_DATA": str(data),
    })
    try:
        bootstrap.main()
    except Exception as exc:  # noqa: BLE001 - nooit de hele app laten vallen
        warnings.append(f"mappen/sleutels aanmaken mislukt: {exc}. Geef gebruiker 'apps' "
                        "(568) schrijfrechten op de appdata- en werkmap.")
    closed = close_private_dirs(config, bootstrap._uid_gid()[0])
    if closed:
        print(f"[mediaguard] alleen nog leesbaar voor de app zelf: {', '.join(closed)}",
              flush=True)
    return warnings


def private_dirs() -> list[str]:
    """Appdata-mappen met databases, sleutels en de config.xml (API-sleutel) van de forks."""
    # logs: logregels kunnen adressen en foutteksten bevatten; alleen voor de app zelf
    return ["core", "secrets", "encode", "logs",
            *(instance for instance, _kind, _port in FORKS)]


def close_private_dirs(config: Path, uid: int) -> list[str]:
    """Haalt de rechten voor 'overige gebruikers' weg van de mappen uit :func:`private_dirs`,
    zodat de web-server (eigen gebruiker, via de tunnel bereikbaar) er niet in kan, ook bij een
    oudere installatie waar zo'n map nog voor iedereen open stond. Alleen bij mappen van de
    app-gebruiker zelf: die gebruikt de eigenaarsrechten en merkt er niets van. Bij een andere
    eigenaar of een ACL die chmod weigert blijft alles zoals het is. Media worden nooit
    aangeraakt."""
    closed: list[str] = []
    for name in private_dirs():
        path = config / name
        try:
            st = path.stat()
        except OSError:
            continue
        if not stat.S_ISDIR(st.st_mode) or st.st_uid != uid or not st.st_mode & 0o007:
            continue
        try:
            os.chmod(path, stat.S_IMODE(st.st_mode) & ~0o007)
        except OSError:
            continue  # ACL beheert de rechten
        closed.append(name)
    return closed


def _dri_groups(dri: Path = Path("/dev/dri")) -> list[int]:
    """Groepen van de doorgegeven GPU-nodes (render/video), zodat ffmpeg als 568 bij
    /dev/dri mag; root (0) wordt nooit als extra groep meegegeven."""
    gids: set[int] = set()
    if dri.is_dir():
        for dev in dri.iterdir():
            try:
                gids.add(dev.stat().st_gid)
            except OSError:
                continue
    return sorted(g for g in gids if g != 0)


def _demote(uid: int, gid: int, groups: list[int]):  # pragma: no cover - vereist root
    def run() -> None:
        os.setgroups(sorted({gid, *groups}))
        os.setgid(gid)
        os.setuid(uid)
        os.umask(0o002)
    return run


_QUIET = re.compile(r'"(GET|HEAD) (/api/v1/health|/health|/ping|/login)\S* HTTP/[\d.]+" 2\d\d')


def quiet_line(line: str) -> bool:
    """Geslaagde levenscontroles (van deze supervisor en Docker) niet in het containerlog."""
    return bool(_QUIET.search(line))


LABELS_NL = {"core": "MediaGuard-core", "recovery": "Het herstelproces (recovery)",
             "encode": "De AV1-engine", "web": "De web-app",
             "sonarr-series": "Sonarr (series)", "sonarr-anime": "Sonarr (anime)",
             "radarr-films": "Radarr (films)", "radarr-animefilms": "Radarr (animefilms)"}
LOOP_THRESHOLD = 3          # herstarts binnen 24 uur: 'herstart steeds opnieuw'
LOW_DISK_BYTES = 512 * 1024**2
_SECRETISH = re.compile(r"(?i)(api[_-]?key|token|password|secret)(\S*[=:]\s*)\S+")


def restarts_24h(spec: Spec, now: float | None = None) -> int:
    now = time.time() if now is None else now
    return sum(1 for t in spec.restart_times if now - t < 86400)


def explain_restarts(spec: Spec, config: Path, core_dir: Path,
                     free_bytes: int | None = None) -> str:
    """Nederlandse uitleg van een herstartlus uit wat de supervisor ziet: vrije ruimte op
    /config, een afgebroken migratie, de exitcode en de laatste regels van de dienst."""
    from mediaguard import db as core_db

    label = LABELS_NL.get(spec.name, spec.name)
    lines = [ln.strip() for ln in spec.recent if ln.strip()]
    text = "\n".join(lines)
    if free_bytes is None:
        try:
            free_bytes = shutil.disk_usage(config).free
        except OSError:
            free_bytes = None
    if (free_bytes is not None and free_bytes < LOW_DISK_BYTES) or any(
            m in text for m in ("No space left", "disk is full", "database or disk is full")):
        free = "" if free_bytes is None else f" (nog {free_bytes // 1024**2} MB vrij)"
        return (f"De appdata-map /config is vol{free}. Maak ruimte vrij op die dataset of "
                f"vergroot hem; {label} start daarna vanzelf weer.")
    stale = core_db.stale_migrations(core_dir) if spec.migration_guard else []
    failed = core_db.failed_migration(core_dir) if spec.migration_guard else None
    if failed:
        step = f" ({failed.get('from') or '?'} → {failed.get('to') or '?'})"
        error = _SECRETISH.sub(r"\1\2***", str(failed.get("error") or ""))[:200]
        return (f"Het bijwerken van de database na de update{step} mislukt steeds: {error}. "
                "De mislukte stap is teruggedraaid, je gegevens zijn veilig. Zet de vorige versie "
                "terug (README: Terugdraaien) en stuur de supportbundel mee.")
    if stale or "MigrationError" in text or "Migratie" in text and "mislukt" in text:
        info = stale[0] if stale else {}
        step = (f" ({info.get('from') or '?'} → {info.get('to') or '?'})" if info else "")
        return (f"{label} stopte tijdens het bijwerken van de database na de update{step}. "
                "Een zware migratie kan even duren; MediaGuard probeert het opnieuw. Blijft "
                "het misgaan, zet dan de vorige versie terug (README: Terugdraaien).")
    if "database is locked" in text:
        return (f"{label} vond de database bezet door een ander proces (bijvoorbeeld een "
                "lange migratie). MediaGuard probeert het vanzelf opnieuw.")
    if spec.last_exit in (-9, 137) or "MemoryError" in text:
        return (f"{label} werd hard gestopt, waarschijnlijk door te weinig werkgeheugen "
                f"(code {spec.last_exit}).")
    if "Permission denied" in text or "PermissionError" in text:
        return (f"{label} mag een bestand niet lezen of schrijven. Geef gebruiker apps (568) "
                "lees- en schrijfrechten op /config en /work.")
    last = _SECRETISH.sub(r"\1\2***", lines[-1])[:200] if lines else "geen uitvoer"
    return f"{label} stopt steeds (code {spec.last_exit}). Laatste melding: {last}"


def _marker_is_from(marker: Path, spec: Spec, slack_s: float = 5.0) -> bool:
    """Is de markering geschreven door het huidige proces van deze dienst (niet ouder dan
    zijn start)? Pids worden in een container bij elke start hergebruikt."""
    try:
        written = marker.stat().st_mtime
    except OSError:
        return False
    started_wall = time.time() - (time.monotonic() - spec.started_at)
    return written >= started_wall - slack_s


class Supervisor:
    def __init__(self, specs: list[Spec], uid: int | None, gid: int | None,
                 state_path: Path | None = None, health_path: Path | None = None,
                 check_interval: float = 30.0, max_fails: int = 3,
                 log_path: Path | None = None, config_dir: Path | None = None,
                 web_state_path: Path | None = None,
                 stop_budget: float | None = None) -> None:
        self.specs = specs
        self.stop_budget = DEFAULT_STOP_BUDGET if stop_budget is None else stop_budget
        self.uid, self.gid = uid, gid
        self.groups = _dri_groups()
        self.stopping = False
        self.state_path = state_path
        self.health_path = health_path
        self.web_state_path = web_state_path
        # eigen regels (starts, herstarts, uitleg) ook in /config/logs/supervisor.log
        self.log_path = log_path
        self.config_dir = config_dir
        self.core_dir = state_path.parent if state_path is not None else None
        self.check_interval = check_interval
        self.max_fails = max_fails
        self._lock = threading.Lock()

    def log(self, msg: str) -> None:
        print(f"[mediaguard] {msg}", flush=True)
        if self.log_path is not None:
            with contextlib.suppress(OSError):
                if self.log_path.exists() and self.log_path.stat().st_size > 2 * 1024**2:
                    os.replace(self.log_path, self.log_path.with_name(
                        self.log_path.name + ".1"))
                with self.log_path.open("a", encoding="utf-8") as handle:
                    handle.write(f"{time.strftime('%Y-%m-%d %H:%M:%S')} {msg}\n")

    def _pump(self, spec: Spec, stream) -> None:  # pragma: no cover - I/O-draad
        for raw in iter(stream.readline, b""):
            line = raw.decode("utf-8", "replace")
            if quiet_line(line):
                continue
            spec.recent.append(line[:500])
            sys.stdout.write(f"[{spec.name}] {line}")
            sys.stdout.flush()

    def start(self, spec: Spec) -> None:
        preexec = None
        if self.uid is not None and self.gid is not None and os.geteuid() == 0:
            preexec = (_demote(spec.user[0], spec.user[1], []) if spec.user is not None
                       else _demote(self.uid, self.gid, self.groups))
        try:
            spec.proc = subprocess.Popen(  # noqa: S603 - vaste argv
                spec.argv, env=spec.env, cwd=spec.cwd, preexec_fn=preexec,
                stdout=subprocess.PIPE, stderr=subprocess.STDOUT, start_new_session=True)
        except OSError as exc:
            self.log(f"{spec.name} kan niet starten: {exc}")
            spec.proc = None
            spec.next_start = time.monotonic() + 30
            return
        spec.started_at = time.monotonic()
        spec.fails = 0
        spec.child_cpu = -1
        threading.Thread(target=self._pump, args=(spec, spec.proc.stdout), daemon=True).start()
        self.log(f"{spec.name} gestart (pid {spec.proc.pid})")

    @staticmethod
    def wait_port(port: int, timeout: float) -> bool:
        return Supervisor.wait_ports([port], timeout)

    @staticmethod
    def wait_ports(ports: list[int], timeout: float) -> bool:
        import socket
        deadline = time.monotonic() + timeout
        pending = set(ports)
        while pending and time.monotonic() < deadline:
            for port in list(pending):
                with socket.socket() as s:
                    s.settimeout(0.5)
                    if s.connect_ex((INTERNAL_HOST, port)) == 0:
                        pending.discard(port)
            if pending:
                time.sleep(0.5)
        return not pending

    # -- levenscontrole -------------------------------------------------------------------
    @staticmethod
    def descendant_cpu_ticks(pid: int, proc_root: Path = Path("/proc")) -> int:
        """Som van utime+stime (clock ticks) van alle nakomelingen van ``pid``."""
        parents: dict[int, int] = {}
        ticks: dict[int, int] = {}
        try:
            entries = list(proc_root.iterdir())
        except OSError:
            return 0
        for entry in entries:
            if not entry.name.isdigit():
                continue
            try:
                raw = (entry / "stat").read_text()
            except OSError:
                continue
            rest = raw.rsplit(")", 1)[-1].split()
            # na de naam: state ppid ... utime(index 11) stime(12) vanaf 'state' = 0
            if len(rest) < 13:
                continue
            child = int(entry.name)
            parents[child] = int(rest[1])
            ticks[child] = int(rest[11]) + int(rest[12])
        total = 0
        for child in parents:
            seen, cur = 0, parents.get(child)
            while cur and seen < 64:
                if cur == pid:
                    total += ticks[child]
                    break
                cur, seen = parents.get(cur), seen + 1
        return total

    def _busy(self, spec: Spec) -> bool:
        """Rekenen de kindprocessen (encodes) sinds de vorige controle? Dan niet vast."""
        if not spec.busy_aware or spec.proc is None:
            return False
        ticks = self.descendant_cpu_ticks(spec.proc.pid)
        busy = spec.child_cpu >= 0 and ticks > spec.child_cpu
        spec.child_cpu = ticks
        return busy

    @staticmethod
    def probe(url: str, timeout: float = 5.0) -> bool:
        import urllib.error
        import urllib.request
        try:
            with urllib.request.urlopen(url, timeout=timeout) as response:  # noqa: S310
                return int(response.status) < 500
        except urllib.error.HTTPError as exc:
            return exc.code < 500  # 401/404 = het proces antwoordt wel
        except (OSError, ValueError):
            return False

    def check_health(self, now: float | None = None) -> list[str]:
        """Eén controleronde. Een dienst die ``max_fails`` keer achter elkaar niet antwoordt
        (vastgelopen, niet gecrasht) wordt hard herstart. Geeft de herstarte namen terug."""

        now = time.monotonic() if now is None else now
        restarted: list[str] = []
        for spec in self.specs:
            proc = spec.proc
            if (self.stopping or not spec.health or proc is None or proc.poll() is not None
                    or now - spec.started_at < spec.grace):
                continue
            limit = spec.max_fails or self.max_fails
            if self._migrating(spec):
                # rapport 10: een zware migratie nooit afbreken (anders herstartlus)
                spec.fails = 0
                continue
            ok = self.probe(spec.health, timeout=spec.probe_timeout)
            busy = self._busy(spec)  # altijd meten, zodat de volgende vergelijking klopt
            if ok:
                spec.fails = 0
                continue
            if busy:
                # traag antwoord, maar de encodes rekenen gewoon door: niet herstarten
                spec.busy_skips += 1
                if spec.fails or spec.busy_skips == 1:
                    self.log(f"{spec.name} antwoordt traag maar is bezig (encodes lopen); "
                             "geen herstart")
                spec.fails = 0
                continue
            spec.fails += 1
            self.log(f"{spec.name} reageert niet ({spec.fails}/{limit})")
            if spec.fails >= limit:
                spec.hang_restarts += 1
                self.log(f"{spec.name} hangt: wordt herstart")
                self._terminate(proc, grace=10.0)
                restarted.append(spec.name)
        return restarted

    def _migrating(self, spec: Spec) -> bool:
        """Migreert dit proces de database (markering met zijn eigen pid)?"""
        if not spec.migration_guard or spec.proc is None or self.core_dir is None:
            spec.migrating = None
            return False
        from mediaguard import db as core_db

        info = core_db.migration_of(self.core_dir, spec.proc.pid)
        if info and not _marker_is_from(core_db.migration_marker(self.core_dir,
                                                                 spec.proc.pid), spec):
            info = None  # van een eerder proces met hetzelfde pid (vorige containerstart)
        if info and not spec.migrating:
            self.log(f"{spec.name} werkt de database bij ({info.get('from') or '?'} → "
                     f"{info.get('to') or '?'}); niet herstarten tot dat klaar is")
        elif spec.migrating and not info:
            self.log(f"{spec.name}: database bijgewerkt")
        spec.migrating = info
        return info is not None

    def note_exit(self, spec: Spec, now_wall: float | None = None) -> None:
        """Herstart vastleggen; vanaf 3 binnen 24 uur een uitleg zoeken en loggen."""
        now_wall = time.time() if now_wall is None else now_wall
        spec.restart_times = [t for t in spec.restart_times if now_wall - t < 86400]
        spec.restart_times.append(now_wall)
        spec.total_restarts += 1
        if len(spec.restart_times) < LOOP_THRESHOLD or self.config_dir is None:
            return
        try:
            text = explain_restarts(spec, self.config_dir,
                                    self.core_dir or self.config_dir / "core")
        except Exception as exc:  # noqa: BLE001 - uitleg is een extra, nooit een reden te stoppen
            text = f"{spec.name} stopt steeds (code {spec.last_exit}): {exc}"
        if text != spec.loop_nl:
            self.log(f"LET OP: {spec.name} herstart steeds opnieuw "
                     f"({len(spec.restart_times)}× in 24 uur). {text}")
        spec.loop_nl = text

    def _health_loop(self) -> None:  # pragma: no cover - draad
        while not self.stopping:
            time.sleep(self.check_interval)
            try:
                self.check_health()
            except Exception as exc:  # noqa: BLE001 - controle mag nooit de supervisor stoppen
                self.log(f"levenscontrole mislukt: {exc}")

    # -- status voor UI en Docker-healthcheck ------------------------------------------------
    def snapshot(self, now: float | None = None) -> dict[str, object]:
        now = time.monotonic() if now is None else now
        services: dict[str, dict[str, object]] = {}
        for spec in self.specs:
            running = spec.proc is not None and spec.proc.poll() is None
            state = ("migrating" if running and spec.migrating else
                     "running" if running and spec.fails == 0 else
                     "unresponsive" if running else "restarting")
            services[spec.name] = {
                "state": state, "pid": spec.proc.pid if running and spec.proc else None,
                "uptime_s": int(now - spec.started_at) if running else 0,
                "restarts": spec.restarts, "hang_restarts": spec.hang_restarts,
                "last_exit": spec.last_exit, "fails": spec.fails,
                "restarts_24h": restarts_24h(spec), "total_restarts": spec.total_restarts,
                "last_restart_at": time.strftime(
                    "%Y-%m-%dT%H:%M:%SZ", time.gmtime(spec.restart_times[-1]))
                if spec.restart_times else None,
                "loop_nl": spec.loop_nl if restarts_24h(spec) >= LOOP_THRESHOLD else "",
                "migrating": spec.migrating}
        essential = ("core", "web")
        healthy = all(services.get(n, {}).get("state") in ("running", "migrating")
                      for n in essential if n in services)
        return {"updated_at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
                "healthy": healthy, "services": services}

    def write_state(self) -> None:
        snap = self.snapshot()
        if self.state_path is not None:
            with contextlib.suppress(OSError):
                tmp = self.state_path.with_suffix(".tmp")
                tmp.write_text(json.dumps(snap, indent=1))
                if self.uid is not None and os.geteuid() == 0:
                    with contextlib.suppress(OSError):
                        os.chown(tmp, self.uid, self.gid if self.gid is not None else -1)
                os.replace(tmp, self.state_path)
        if self.web_state_path is not None:
            self._write_web_state(snap)
        if self.health_path is not None:
            bad = [n for n, v in snap["services"].items()  # type: ignore[union-attr]
                   if v["state"] not in ("running", "migrating")]
            text = "ok\n" if snap["healthy"] else f"fail {' '.join(bad)}\n"
            with contextlib.suppress(OSError):
                tmp = self.health_path.with_suffix(".tmp")
                tmp.write_text(text)
                os.replace(tmp, self.health_path)

    def _write_web_state(self, snap: dict) -> None:
        """Alleen wat de web-server nodig heeft om 'core onbereikbaar' uit te leggen. Geen
        pids, exitcodes of logregels; de map is van root (0755), dus de web-gebruiker kan er
        niets in neerzetten of omleggen."""
        path = self.web_state_path
        if path is None:
            return
        core = (snap.get("services") or {}).get("core") or {}
        public = {"updated_at": snap.get("updated_at"),
                  "services": {"core": {k: core.get(k)
                                        for k in ("state", "migrating", "loop_nl")}}}
        with contextlib.suppress(OSError):
            path.parent.mkdir(parents=True, exist_ok=True, mode=0o755)
            tmp = path.with_suffix(".tmp")
            tmp.write_text(json.dumps(public))
            os.chmod(tmp, 0o644)
            os.replace(tmp, path)

    def run(self) -> int:
        threading.Thread(target=self._health_loop, daemon=True).start()
        for spec in self.specs:
            if self.stopping:
                break
            if spec.wait_for and not self.wait_ports(spec.wait_for, spec.wait_for_timeout):
                self.log(f"nog niet alles van Sonarr/Radarr is op; {spec.name} start toch")
            self.start(spec)
            if spec.port and spec.ready_wait and not self.wait_port(spec.port, spec.ready_wait):
                self.log(f"{spec.name} reageert nog niet op poort {spec.port}; ga toch door")
        last_write = 0.0
        while not self.stopping:
            now = time.monotonic()
            for spec in self.specs:
                if spec.proc is None:
                    if now >= spec.next_start:
                        self.start(spec)
                    continue
                code = spec.proc.poll()
                if code is None:
                    continue
                spec.proc = None
                spec.last_exit = code
                spec.migrating = None
                self.note_exit(spec)
                # stabiel > 5 min gedraaid: teller terug naar nul
                if now - spec.started_at > 300:
                    spec.restarts = 0
                delay = min(60, 2 ** min(spec.restarts, 6))
                spec.restarts += 1
                spec.next_start = now + delay
                self.log(f"{spec.name} gestopt (code {code}); herstart over {delay}s")
            if now - last_write >= 5:
                self.write_state()
                last_write = now
            time.sleep(1)
        return 0

    @staticmethod
    def _terminate(proc: subprocess.Popen[bytes], grace: float) -> None:
        """SIGTERM naar de hele procesgroep (ook ffmpeg); bevroren proces eerst ontdooien."""
        with contextlib.suppress(OSError):
            os.killpg(proc.pid, signal.SIGCONT)
        with contextlib.suppress(OSError):
            os.killpg(proc.pid, signal.SIGTERM)
        try:
            proc.wait(grace)
        except subprocess.TimeoutExpired:
            with contextlib.suppress(OSError):
                os.killpg(proc.pid, signal.SIGKILL)

    def announce_planned_stop(self) -> None:
        """Vóór SIGTERM: diensten laten weten dat dit een geplande stop is (update/herstart),
        geen crash. Lukt het schrijven niet, dan stopt alles gewoon (oude gedrag)."""
        stamp = time.strftime("%Y-%m-%dT%H:%M:%S+00:00", time.gmtime())
        for spec in self.specs:
            if not spec.planned_stop_marker:
                continue
            path = Path(spec.planned_stop_marker)
            with contextlib.suppress(OSError):
                path.parent.mkdir(parents=True, exist_ok=True)
                path.write_text(stamp)
                if self.uid is not None and os.geteuid() == 0:
                    os.chown(path, self.uid, self.gid if self.gid is not None else -1)

    def stop(self, *_args: object, budget: float | None = None) -> None:
        """Afsluiten in afhankelijkheidsvolgorde: eerst web, dan Sonarr/Radarr, dan de
        encoder (met ffmpeg-groepen), en als laatste recovery en core. Wat na het budget
        nog leeft, wordt gekild.

        Budget: de stoptijd van de container (README: Stop Grace Period 60 s, instelbaar met
        MG_STOP_TIMEOUT) min een marge, zodat lopende encodes netjes kunnen afbreken en hun
        job terugzetten. Vroeger vast 8 s; de encoder kreeg daarvan maar een paar seconden
        (verbeterpunt 30). Is die stoptijd niet ingesteld (Docker-standaard 10 s), dan krijgen
        recovery en core toch hun SIGTERM binnen ±8 s: snelle diensten wachten per rang
        hooguit FAST_STOP_WAIT en op de encoder wordt hooguit tot LATE_SIGTERM_AT gewacht; hij
        rondt daarna verder af tot het einde van het budget."""
        if self.stopping:
            return
        self.stopping = True
        budget = self.stop_budget if budget is None else budget
        self.announce_planned_stop()
        self.log(f"stoppen (hooguit {int(budget)} s)…")
        start = time.monotonic()
        deadline = start + budget
        late_at = start + min(budget, LATE_SIGTERM_AT)
        ranks = sorted({s.stop_rank for s in self.specs})
        for rank in ranks:
            specs = [s for s in self.specs
                     if s.stop_rank == rank and s.proc and s.proc.poll() is None]
            for spec in specs:
                with contextlib.suppress(OSError):
                    os.killpg(spec.proc.pid, signal.SIGTERM)  # type: ignore[union-attr]
            slow = any(s.slow_stop for s in specs)
            until = min(deadline, late_at if slow else time.monotonic() + FAST_STOP_WAIT)
            for spec in specs:
                with contextlib.suppress(subprocess.TimeoutExpired):
                    spec.proc.wait(max(0.05, until - time.monotonic()))  # type: ignore[union-attr]
        # Wie nog afrondt (de encoder zet lopende jobs terug), krijgt de rest van het budget;
        # pas daarna hard stoppen. Vroeger volgde de kill zodra core snel klaar was.
        for spec in self.specs:
            proc = spec.proc
            if proc is not None and proc.poll() is None:
                with contextlib.suppress(subprocess.TimeoutExpired):
                    proc.wait(max(0.05, deadline - time.monotonic()))
        for spec in self.specs:
            proc = spec.proc
            if proc is not None and proc.poll() is None:
                with contextlib.suppress(OSError):
                    os.killpg(proc.pid, signal.SIGKILL)
        self.write_state()


def ready(env: dict[str, str]) -> bool:
    """Kunnen de diensten starten? (sleutels aanwezig en de core-map schrijfbaar)"""
    config = Path(env.get("MG_AIO_CONFIG", "/config"))
    return bool(_read(config / "secrets" / "internal" / "token")) and \
        os.access(config / "core", os.W_OK)


def problem_page(warnings: list[str]) -> bytes:
    import html
    items = "".join(f"<li>{html.escape(w)}</li>" for w in warnings) or \
        "<li>MediaGuard start nog…</li>"
    return (
        "<!doctype html><html lang=nl><meta charset=utf-8>"
        "<meta name=viewport content='width=device-width,initial-scale=1'>"
        "<meta http-equiv=refresh content=15><title>MediaGuard start niet</title>"
        "<body style='font-family:system-ui;max-width:42rem;margin:2rem auto;padding:0 1rem;"
        "line-height:1.5'><h1>MediaGuard kan nog niet starten</h1><ul>" + items + "</ul>"
        "<p>Los dit op in TrueNAS (bv. <b>Datasets → je dataset → Edit Permissions</b>: geef "
        "gebruiker <b>apps</b> lees- en schrijfrechten). MediaGuard probeert het elke 30 seconden "
        "opnieuw; deze pagina ververst vanzelf. Je hoeft de app niet opnieuw te installeren.</p>"
        "</body></html>").encode()


class _ProblemServer:  # pragma: no cover - netwerk, getest in de image-test
    def __init__(self, port: int) -> None:
        from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
        outer = self
        self.body = problem_page([])

        class Handler(BaseHTTPRequestHandler):
            def do_GET(self) -> None:  # noqa: N802
                self.send_response(503)
                self.send_header("Content-Type", "text/html; charset=utf-8")
                self.send_header("Content-Length", str(len(outer.body)))
                self.end_headers()
                self.wfile.write(outer.body)

            def log_message(self, *_a: object) -> None:
                return

        self.server = ThreadingHTTPServer(("0.0.0.0", port), Handler)
        threading.Thread(target=self.server.serve_forever, daemon=True).start()

    def close(self) -> None:
        self.server.shutdown()
        self.server.server_close()


def main() -> int:  # pragma: no cover - integratie (zie tests/integration/aio)
    env = dict(os.environ)
    if not env.get("MG_HOST"):
        env["MG_AIO_LAN_IP"] = detect_lan_ip()
    uid = _env_int(env, "PUID", 568)
    gid = _env_int(env, "PGID", 568)
    os.environ["MG_UID"], os.environ["MG_GID"] = str(uid), str(gid)
    print(f"[mediaguard] MediaGuard Ultimate {__import__('mediaguard').__version__} start",
          flush=True)
    stop = threading.Event()
    signal.signal(signal.SIGTERM, lambda *_: stop.set())
    signal.signal(signal.SIGINT, lambda *_: stop.set())
    fallback: _ProblemServer | None = None
    while True:
        warnings = prepare(env)
        for warning in warnings:
            print(f"[mediaguard] LET OP: {warning}", flush=True)
        if ready(env):
            break
        if fallback is None:
            try:
                fallback = _ProblemServer(_env_int(env, "MG_WEB_PORT", 30080))
            except OSError as exc:
                print(f"[mediaguard] probleempagina niet beschikbaar: {exc}", flush=True)
        if fallback is not None:
            fallback.body = problem_page(warnings or ["De appdata-map (/config) is niet "
                                                      "schrijfbaar voor gebruiker apps (568)."])
        print("[mediaguard] wacht op schrijfrechten; nieuwe poging over 30s", flush=True)
        if stop.wait(30):
            return 0
    if fallback is not None:
        fallback.close()
    config = Path(env.get("MG_AIO_CONFIG", "/config"))
    owner = (uid, gid) if os.geteuid() == 0 else None
    logs_dir = config / "logs"
    with contextlib.suppress(OSError):
        logs_dir.mkdir(parents=True, exist_ok=True, mode=0o750)  # niet voor de web-gebruiker
        if owner is not None:
            os.chown(logs_dir, uid, gid)
    for line in before_services(env, config, owner):
        print(f"[mediaguard] {line}", flush=True)
    sup = Supervisor(build_specs(env), uid if os.geteuid() == 0 else None, gid,
                     state_path=config / "core" / "supervisor.json",
                     health_path=Path(env.get("MG_AIO_HEALTH_FILE", "/tmp/mediaguard-health")),
                     log_path=logs_dir / "supervisor.log", config_dir=config,
                     web_state_path=Path(env.get("MG_AIO_WEB_STATE", WEB_STATE)),
                     stop_budget=stop_budget(env))
    signal.signal(signal.SIGTERM, sup.stop)
    signal.signal(signal.SIGINT, sup.stop)
    if stop.is_set():
        return 0
    code = sup.run()
    # alle diensten zijn nu gestopt: een in de app klaargezette terugzetting omwisselen
    for line in apply_staged_restore(config, owner):
        print(f"[mediaguard] {line}", flush=True)
    return code


def apply_staged_restore(config: Path, owner: tuple[int, int] | None) -> list[str]:
    """Alleen als geen enkele dienst draait (bij de start of na het stoppen)."""
    from mediaguard.maintenance import preupdate

    try:
        result = preupdate.apply_staged(config / "core", owner)
    except Exception as exc:  # noqa: BLE001 - nooit de start blokkeren
        return [f"terugzetten mislukt: {exc}"]
    if result is None:
        return []
    if not result.get("ok"):
        return [f"terugzetten niet gedaan: {result.get('error')}"]
    return [f"back-up {result.get('set')} teruggezet ({', '.join(result.get('files') or [])}); "
            f"de vorige databases staan in {result.get('previous')}"]


def before_services(env: dict[str, str], config: Path,
                    owner: tuple[int, int] | None) -> list[str]:
    """Vóór de start: (1) een klaargezette terugzetting omwisselen; (2) bij een update met
    openstaande migraties een set van core- én AV1-database maken (MG_PRE_UPDATE_SET)."""
    from mediaguard import __version__
    from mediaguard.db import MIGRATIONS_DIR
    from mediaguard.maintenance import preupdate

    out = apply_staged_restore(config, owner)
    encoder_dir = Path(env.get("MG_AIO_ENCODER_DIR", "/opt/mediaguard-encode"))
    try:
        if owner is not None and os.geteuid() == 0:  # pragma: no cover - vereist root
            # als de app-gebruiker: SQLite maakt bij het lezen soms -wal/-shm aan; van root
            # zouden die core daarna het schrijven beletten (herstartlus)
            proc = subprocess.run(  # noqa: S603 - vaste argv
                [sys.executable, "-m", "mediaguard.maintenance.preupdate", "snapshot",
                 "--config", str(config), "--core-migrations", str(MIGRATIONS_DIR),
                 "--encoder-migrations", str(encoder_dir / "app" / "migrations"),
                 "--version", __version__], capture_output=True, text=True, timeout=3600,
                preexec_fn=_demote(owner[0], owner[1], []), check=False)
            if proc.returncode != 0:
                raise RuntimeError(proc.stderr.strip()[-300:] or f"code {proc.returncode}")
            line = proc.stdout.strip().splitlines()[-1] if proc.stdout.strip() else ""
            made = Path(line) if line else None
        else:
            made = preupdate.snapshot_before_start(
                config, core_migrations=MIGRATIONS_DIR,
                encoder_migrations=encoder_dir / "app" / "migrations",
                app_version=__version__, owner=owner)
    except Exception as exc:  # noqa: BLE001 - core maakt dan zelf een kopie vóór zijn migratie
        return [*out, f"back-up vóór de update mislukt: {exc}"]
    if made is not None:
        env["MG_PRE_UPDATE_SET"] = str(made)
        out.append(f"back-up vóór de update gemaakt: {made.name}")
    return out


if __name__ == "__main__":  # pragma: no cover
    sys.exit(main())
