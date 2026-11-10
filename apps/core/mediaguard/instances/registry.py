"""Instance-registry (contract ``packages/contracts/instances.md``).

Vier standaardinstanties (R1). Native Sonarr/Radarr-ID's zijn alleen uniek binnen hun
instantie (R2.4); iedere functie hier draagt daarom ``instance_id`` expliciet mee.
API-sleutels staan uitsluitend versleuteld in ``ctx.secrets`` onder
``instance:<id>:apikey`` en worden nooit via de API teruggegeven.
"""

from __future__ import annotations

import contextlib
import json
import logging
import os
import re
from dataclasses import asdict, dataclass
from typing import TYPE_CHECKING, Any

from mediaguard.arr.client import ArrClient
from mediaguard.db import utcnow

if TYPE_CHECKING:
    from mediaguard.context import AppContext

LOGGER = logging.getLogger(__name__)

INSTANCE_ID_RE = re.compile(r"^[a-z][a-z0-9-]{1,47}$")
APPS = ("sonarr", "radarr")
MEDIA_TYPES = {"sonarr": ("tv", "anime"), "radarr": ("movie", "anime_movie")}
STATUSES = ("healthy", "degraded", "offline", "unknown")

DEFAULT_INSTANCES: list[dict[str, Any]] = [
    {"id": "sonarr-series", "app": "sonarr", "media_type": "tv", "name": "Sonarr Series",
     "url": "http://mediaguard-sonarr-series:8989", "download_category": "tv",
     "default_series_type": "standard", "sort_order": 10},
    {"id": "sonarr-anime", "app": "sonarr", "media_type": "anime", "name": "Sonarr Anime",
     "url": "http://mediaguard-sonarr-anime:8989", "download_category": "tv-anime",
     "default_series_type": "anime", "sort_order": 20},
    {"id": "radarr-films", "app": "radarr", "media_type": "movie", "name": "Radarr Films",
     "url": "http://mediaguard-radarr-films:7878", "download_category": "movies",
     "sort_order": 30},
    {"id": "radarr-animefilms", "app": "radarr", "media_type": "anime_movie",
     "name": "Radarr Animefilms", "url": "http://mediaguard-radarr-animefilms:7878",
     "download_category": "movies-anime", "sort_order": 40},
]


@dataclass(frozen=True)
class Instance:
    id: str
    app: str
    media_type: str
    name: str
    url: str
    public_url: str | None
    status: str
    suspended: bool
    shadow_mode: bool
    archived: bool
    hook_fail_policy: str

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


def apikey_secret(instance_id: str) -> str:
    return f"instance:{instance_id}:apikey"


def hookkey_secret(instance_id: str) -> str:
    return f"instance:{instance_id}:hookkey"


def _row_to_instance(row: Any) -> Instance:
    return Instance(
        id=row["id"], app=row["app"], media_type=row["media_type"], name=row["name"],
        url=row["url"], public_url=row["public_url"], status=row["status"],
        suspended=bool(row["suspended"]), shadow_mode=bool(row["shadow_mode"]),
        archived=bool(row["archived"]), hook_fail_policy=row["hook_fail_policy"],
    )


def list_instances(ctx: AppContext, include_archived: bool = False) -> list[Instance]:
    sql = "SELECT * FROM instances"
    if not include_archived:
        sql += " WHERE archived=0"
    return [_row_to_instance(r) for r in ctx.db.fetch_all(sql + " ORDER BY sort_order, id")]


def get_instance(ctx: AppContext, instance_id: str) -> Instance | None:
    row = ctx.db.fetch_one("SELECT * FROM instances WHERE id=?", (instance_id,))
    return None if row is None else _row_to_instance(row)


def instance_row(ctx: AppContext, instance_id: str) -> dict[str, Any] | None:
    row = ctx.db.fetch_one("SELECT * FROM instances WHERE id=?", (instance_id,))
    return None if row is None else dict(row)


def arr_client(ctx: AppContext, instance_id: str, *, timeout: float | None = None) -> ArrClient:
    """Async httpx-client met API-sleutel uit de secrets-store.

    Tests en de E2E-harnas kunnen per instantie een httpx-transport injecteren via
    ``ctx.services['arr_transports'][instance_id]`` (bijv. een in-proces fake)."""
    inst = get_instance(ctx, instance_id)
    if inst is None:
        raise KeyError(f"onbekende instantie: {instance_id}")
    transports = ctx.services.get("arr_transports") or {}
    opts = ctx.services.get("arr_client_options") or {}
    api_key = ctx.secrets.get(apikey_secret(instance_id))
    tmo = timeout or float(opts.get("timeout", 20.0))
    client = ArrClient(
        inst.url, api_key, instance_id=inst.id,
        app=inst.app, transport=transports.get(instance_id), timeout=tmo,
        retries=int(opts.get("retries", 2)), backoff=float(opts.get("backoff", 0.5)),
    )
    client._shared = _pooled(ctx, client)
    return client


def _pooled(ctx: AppContext, client: ArrClient) -> Any:
    """Eén langlevende httpx-verbinding per instantie (per event loop): geen nieuwe client
    (~14 ms CPU) en TCP-verbinding bij elke health-check, sync of Activiteit-verversing."""
    import asyncio
    import hashlib

    try:
        loop = asyncio.get_running_loop()
    except RuntimeError:
        return None
    pool: dict[Any, Any] = ctx.services.setdefault("arr_http_pool", {})
    digest = hashlib.sha256((client.api_key or "").encode()).hexdigest()[:16]
    key = (id(loop), client.instance_id, client.base_url, digest, client.timeout,
           id(client.transport))
    http = pool.get(key)
    if http is None or http.is_closed:
        # alleen echt verouderde verbindingen (ander adres/sleutel) sluiten; andere
        # time-outs voor dezelfde instantie blijven naast elkaar bestaan
        for old_key in [k for k in pool if k[:2] == key[:2]
                        and (k[2], k[3], k[5]) != (key[2], key[3], key[5])]:
            stale = pool.pop(old_key)
            loop.create_task(stale.aclose())  # oud adres/sleutel: netjes sluiten
        http = client._make_client()
        pool[key] = http
    return http


async def close_pool(ctx: AppContext) -> None:
    for http in list((ctx.services.pop("arr_http_pool", {}) or {}).values()):
        with contextlib.suppress(RuntimeError):  # andere event loop (tests)
            await http.aclose()


# -- schrijven ---------------------------------------------------------------------------
def validate_new(data: dict[str, Any]) -> None:
    iid = str(data.get("id", ""))
    if not INSTANCE_ID_RE.match(iid):
        raise ValueError("Instance-ID: kleine letters, cijfers en '-', 2-48 tekens, "
                         "beginnend met een letter")
    app = data.get("app")
    if app not in APPS:
        raise ValueError("app moet 'sonarr' of 'radarr' zijn")
    if data.get("media_type") not in MEDIA_TYPES[str(app)]:
        raise ValueError(f"media_type voor {app}: {', '.join(MEDIA_TYPES[str(app)])}")
    validate_url(str(data.get("url", "")))
    if data.get("public_url"):
        validate_url(str(data["public_url"]))


def validate_url(url: str) -> None:
    if not re.match(r"^https?://[^\s/]+(:\d+)?(/[^\s]*)?$", url):
        raise ValueError(f"Ongeldige URL: {url!r} (verwacht http(s)://host:poort)")


def create_instance(ctx: AppContext, data: dict[str, Any], api_key: str | None = None,
                    actor: str = "system") -> Instance:
    validate_new(data)
    now = utcnow()
    cols = {
        "id": data["id"], "app": data["app"], "media_type": data["media_type"],
        "name": data.get("name") or data["id"], "url": str(data["url"]).rstrip("/"),
        "public_url": (data.get("public_url") or None),
        "hook_fail_policy": data.get("hook_fail_policy", "open"),
        "shadow_mode": int(bool(data.get("shadow_mode", False))),
        "download_category": data.get("download_category"),
        "default_series_type": data.get("default_series_type"),
        "sort_order": int(data.get("sort_order", 100)),
        "created_at": now, "updated_at": now,
    }
    names = ",".join(cols)
    marks = ",".join("?" for _ in cols)
    ctx.db.execute(f"INSERT INTO instances({names}) VALUES ({marks})", list(cols.values()))
    if api_key:
        ctx.secrets.set(apikey_secret(data["id"]), api_key, actor)
    inst = get_instance(ctx, data["id"])
    assert inst is not None
    return inst


def _env_key(instance_id: str, suffix: str) -> str:
    return f"MG_INSTANCE_{instance_id.upper().replace('-', '_')}_{suffix}"


def _env_instances() -> list[dict[str, Any]]:
    """MG_INSTANCES (JSON-lijst) heeft voorrang; anders standaardset + per-ID env-vars."""
    raw = os.environ.get("MG_INSTANCES", "").strip()
    if raw:
        try:
            data = json.loads(raw)
        except json.JSONDecodeError as exc:
            raise ValueError(f"MG_INSTANCES is geen geldige JSON: {exc}") from exc
        if not isinstance(data, list):
            raise ValueError("MG_INSTANCES moet een JSON-lijst zijn")
        defaults = {d["id"]: d for d in DEFAULT_INSTANCES}
        out = []
        for entry in data:
            merged = {**defaults.get(entry.get("id"), {}), **entry}
            out.append(merged)
        return out
    return [dict(d) for d in DEFAULT_INSTANCES]


def seed_instances(ctx: AppContext) -> list[str]:
    """Maakt ontbrekende instanties aan (installatie). Bestaande rijen worden NIET
    overschreven: na installatie is de UI leidend. Een API-sleutel uit de omgeving wordt
    alleen opgeslagen als er nog geen sleutel is."""
    created: list[str] = []
    for entry in _env_instances():
        iid = entry["id"]
        url = os.environ.get(_env_key(iid, "URL")) or entry.get("url")
        public = os.environ.get(_env_key(iid, "PUBLIC_URL")) or entry.get("public_url") \
            or _public_from_host(iid, os.environ.get("MG_PUBLIC_HOST", ""))
        api_key = os.environ.get(_env_key(iid, "APIKEY")) or entry.pop("api_key", None)
        entry = {**entry, "url": url, "public_url": public}
        entry.pop("api_key", None)
        if get_instance(ctx, iid) is None:
            try:
                create_instance(ctx, entry, api_key=api_key, actor="system:seed")
                created.append(iid)
            except ValueError as exc:
                LOGGER.error("instantie %s niet aangemaakt: %s", iid, exc)
                continue
        elif api_key and not ctx.secrets.has(apikey_secret(iid)):
            ctx.secrets.set(apikey_secret(iid), api_key, "system:seed")
        seed_hook_key(ctx, iid)
    return created


DEFAULT_PUBLIC_PORTS = {"sonarr-series": 38989, "sonarr-anime": 38990, "radarr-films": 37878,
                        "radarr-animefilms": 37879}


def _public_from_host(instance_id: str, host: str) -> str | None:
    """Stabiele URL waarmee Seerr de instantie bereikt (host-poort uit de compose)."""
    host = host.strip().strip("/")
    if not host or any(c in host for c in " /?#@"):
        return None
    port = os.environ.get(_env_key(instance_id, "PUBLIC_PORT")) or \
        DEFAULT_PUBLIC_PORTS.get(instance_id)
    return f"http://{host}:{port}" if port else None


def fill_public_urls(ctx: AppContext, host: str, actor: str = "system") -> list[str]:
    """Vult ontbrekende publieke URL's in met het adres waarmee de gebruiker MediaGuard
    opent (de browser kent het TrueNAS-adres; de gebruiker hoeft niets in te typen).
    Bestaande, bewust ingestelde URL's blijven staan."""
    from urllib.parse import urlsplit

    browser_host = host.strip().strip("/").split(":")[0]
    # Vast LAN-adres (alles-in-één-image detecteert het; of MG_HOST) gaat vóór het adres in
    # de browser: dat kan een domein achter een tunnel/reverse proxy zijn.
    lan = os.environ.get("MG_PUBLIC_HOST", "").strip()
    if lan:
        host = lan
    changed = []
    for inst in list_instances(ctx):
        if inst.public_url:
            current = urlsplit(inst.public_url).hostname or ""
            # eerder automatisch ingevuld met het browserdomein → herstellen naar LAN-adres
            if not (lan and current and current == browser_host and current != lan):
                continue
        url = _public_from_host(inst.id, host)
        if url:
            ctx.db.execute("UPDATE instances SET public_url=? WHERE id=?", (url, inst.id))
            changed.append(inst.id)
    if changed:
        from mediaguard.audit import audit

        audit(ctx.db, actor=actor, action="instances.public_url_filled",
              new_value={"host": host, "instances": changed})
    return changed


def env_hook_key(instance_id: str) -> str | None:
    """Hook-sleutel die via de omgeving is vastgezet (zelfde waarde als
    ``MEDIAGUARD_HOOK_KEY`` van de fork-container)."""
    return os.environ.get(_env_key(instance_id, "HOOKKEY")) or None


def seed_hook_key(ctx: AppContext, instance_id: str) -> None:
    """De fork leest zijn hook-sleutel alleen uit zijn omgeving; core moet dezelfde sleutel
    kennen vóór de eerste pre-grab-call. Een sleutel uit de omgeving is daarom leidend.
    Zonder env-sleutel wordt een eerder opgeslagen sleutel opnieuw geactiveerd."""
    key = env_hook_key(instance_id)
    if key:
        if ctx.secrets.get(hookkey_secret(instance_id)) != key:
            ctx.secrets.set(hookkey_secret(instance_id), key, "system:seed")
        ctx.set_hook_key(instance_id, key)
        return
    stored = ctx.secrets.get(hookkey_secret(instance_id))
    if stored:
        ctx.set_hook_key(instance_id, stored)


UPDATABLE = {
    "name", "url", "public_url", "hook_fail_policy", "shadow_mode", "sort_order",
    "default_quality_profile_id", "default_language_profile_id", "default_root_folder",
    "default_monitor", "default_monitor_new_items", "default_series_type",
    "default_minimum_availability", "download_category", "media_type",
}


def update_instance(ctx: AppContext, instance_id: str, changes: dict[str, Any]) -> dict:
    row = instance_row(ctx, instance_id)
    if row is None:
        raise KeyError(instance_id)
    sets: dict[str, Any] = {}
    for key, value in changes.items():
        if key not in UPDATABLE:
            raise ValueError(f"veld {key} kan niet gewijzigd worden")
        if key in ("url", "public_url") and value:
            validate_url(str(value))
            value = str(value).rstrip("/")
        if key == "hook_fail_policy" and value not in ("open", "closed"):
            raise ValueError("hook_fail_policy moet 'open' of 'closed' zijn")
        if key == "media_type" and value not in MEDIA_TYPES[row["app"]]:
            raise ValueError(f"media_type voor {row['app']}: {MEDIA_TYPES[row['app']]}")
        if key == "shadow_mode":
            value = int(bool(value))
        sets[key] = value
    if sets:
        sets["updated_at"] = utcnow()
        assignments = ",".join(f"{k}=?" for k in sets)
        ctx.db.execute(f"UPDATE instances SET {assignments} WHERE id=?",
                       [*sets.values(), instance_id])
    return {k: row.get(k) for k in sets if k != "updated_at"}


def set_status(ctx: AppContext, instance_id: str, status: str, *, error: str | None,
               version: str | None = None, app_name: str | None = None,
               api_key_valid: bool | None = None, health: list[dict] | None = None) -> str:
    """Werkt de gezondheid bij en geeft de vorige status terug."""
    if status not in STATUSES:
        raise ValueError(status)
    row = instance_row(ctx, instance_id)
    if row is None:
        raise KeyError(instance_id)
    now = utcnow()
    changed = row["status"] != status
    ctx.db.execute(
        "UPDATE instances SET status=?, last_check_at=?, last_error=?, "
        "version=COALESCE(?,version), upstream_app_name=COALESCE(?,upstream_app_name), "
        "api_key_valid=COALESCE(?,api_key_valid), health_json=COALESCE(?,health_json), "
        "last_status_change_at=CASE WHEN ? THEN ? ELSE last_status_change_at END WHERE id=?",
        (status, now, error, version, app_name,
         None if api_key_valid is None else int(api_key_valid),
         None if health is None else json.dumps(health, ensure_ascii=False),
         int(changed), now, instance_id),
    )
    return str(row["status"])
