"""Bron-instanties: de bestaande (oude) productie-Sonarr/Radarr van de gebruiker.

Twee soorten:
- ``live``: URL + API-sleutel (sleutel versleuteld onder ``source:<id>:apikey``).
- ``backup``: alleen een geüploade native back-up (.zip); geen live verbinding.

VEILIGHEID: elke HTTP-aanroep naar een bron gaat via :class:`SourceClient`, die
structureel ALLE niet-GET-verzoeken weigert. Alleen twee expliciete, smalle
uitzonderingen bestaan (``grant``):
- ``backup``: ``POST /api/v3/command`` met uitsluitend ``{"name": "Backup"}``
  (expliciete opt-in door een admin; niet-destructief).
- ``freeze``: ``PUT /api/v3/indexer/{id}`` tijdens cutover/rollback (na getypte
  bevestiging in de UI).
"""

from __future__ import annotations

import re
from typing import TYPE_CHECKING, Any

import httpx

from mediaguard.arr.client import API_PREFIX, ArrClient, ArrError
from mediaguard.db import dumps, loads, utcnow
from mediaguard.instances.registry import validate_url

if TYPE_CHECKING:
    from mediaguard.context import AppContext

SOURCE_ID_RE = re.compile(r"^[a-z0-9][a-z0-9-]{1,47}$")
_INDEXER_PUT = re.compile(rf"^{API_PREFIX}/indexer/\d+$")


def apikey_secret(source_id: str) -> str:
    return f"source:{source_id}:apikey"


class SourceWriteBlocked(ArrError):
    """Een schrijfverzoek naar de oude productie-instantie is structureel geweigerd."""

    code = "SOURCE_READ_ONLY"
    http_status = 409


class SourceClient(ArrClient):
    """ArrClient die naar een OUDE productie-instantie praat: standaard alleen-lezen."""

    def __init__(self, *args: Any, grant: str | None = None, **kwargs: Any) -> None:
        super().__init__(*args, **kwargs)
        self.grant = grant

    def _permitted(self, method: str, url: str, json: Any) -> bool:
        if method in ("GET", "HEAD"):
            return True
        if self.grant == "backup" and method == "POST" and url == f"{API_PREFIX}/command":
            return isinstance(json, dict) and set(json) == {"name"} and json["name"] == "Backup"
        if self.grant == "freeze" and method == "PUT":
            return bool(_INDEXER_PUT.match(url))
        return False

    async def request(self, method: str, path: str, *, params: dict[str, Any] | None = None,
                      json: Any = None) -> Any:
        url = self._path(path)
        if not self._permitted(method.upper(), url, json):
            raise SourceWriteBlocked(
                f"Schrijfverzoek {method.upper()} {url} naar oude instantie "
                f"'{self.instance_id}' geweigerd (alleen-lezen tot expliciete cutover)",
                method=method.upper(), path=url, instance_id=self.instance_id)
        return await super().request(method, path, params=params, json=json)

    async def download(self, path: str, max_bytes: int) -> bytes:
        """Binaire GET (bijv. back-upbestand). Sleutel alleen in de header."""
        if not path.startswith("/"):
            path = "/" + path
        async with httpx.AsyncClient(
                base_url=self.base_url, transport=self.transport, follow_redirects=False,
                timeout=httpx.Timeout(max(60.0, self.timeout)),
                headers={"X-Api-Key": self.api_key, "User-Agent": "MediaGuard-Core"}
        ) as c, c.stream("GET", path) as resp:
            if resp.status_code != 200:
                raise ArrError(f"Download {path} mislukt: HTTP {resp.status_code}",
                               status=resp.status_code, method="GET", path=path,
                               instance_id=self.instance_id)
            chunks: list[bytes] = []
            total = 0
            async for chunk in resp.aiter_bytes():
                total += len(chunk)
                if total > max_bytes:
                    raise ArrError(f"Back-up groter dan limiet ({max_bytes} bytes)",
                                   method="GET", path=path, instance_id=self.instance_id)
                chunks.append(chunk)
            return b"".join(chunks)


# -- registry ----------------------------------------------------------------------------
def source_row(ctx: AppContext, source_id: str) -> dict[str, Any] | None:
    row = ctx.db.fetch_one("SELECT * FROM source_instances WHERE id=?", (source_id,))
    return dict(row) if row else None


def source_view(ctx: AppContext, row: dict[str, Any]) -> dict[str, Any]:
    out = {k: row[k] for k in ("id", "kind", "app", "name", "url", "version", "app_name",
                               "instance_name", "database_type", "status", "last_error",
                               "archived", "created_at", "updated_at")}
    out["archived"] = bool(out["archived"])
    out["detected"] = loads(row.get("detected_json"), {})
    out["api_key_set"] = ctx.secrets.has(apikey_secret(row["id"])) if row["kind"] == "live" \
        else None
    backup = ctx.db.fetch_one(
        "SELECT id,origin,backup_name,size,sha256,verified,app_version,created_at "
        "FROM source_backups WHERE source_id=? ORDER BY id DESC LIMIT 1", (row["id"],))
    out["latest_backup"] = dict(backup) if backup else None
    return out


def list_sources(ctx: AppContext, include_archived: bool = False) -> list[dict[str, Any]]:
    sql = "SELECT * FROM source_instances"
    if not include_archived:
        sql += " WHERE archived=0"
    return [source_view(ctx, dict(r)) for r in ctx.db.fetch_all(sql + " ORDER BY id")]


def _slug(text: str) -> str:
    slug = re.sub(r"[^a-z0-9]+", "-", text.lower()).strip("-")[:40] or "bron"
    return slug if slug[0].isalnum() else "b" + slug


def unique_source_id(ctx: AppContext, wanted: str) -> str:
    base = _slug(wanted)
    candidate, n = base, 2
    while source_row(ctx, candidate) is not None:
        candidate, n = f"{base}-{n}", n + 1
    return candidate


def source_client(ctx: AppContext, source_id: str, *, grant: str | None = None,
                  timeout: float | None = None) -> SourceClient:
    row = source_row(ctx, source_id)
    if row is None:
        raise KeyError(source_id)
    if row["kind"] != "live" or not row["url"]:
        raise ValueError(f"Bron '{source_id}' is een geüploade back-up zonder live verbinding")
    transports = ctx.services.get("source_transports") or {}
    opts = ctx.services.get("arr_client_options") or {}
    return SourceClient(row["url"], ctx.secrets.get(apikey_secret(source_id)),
                        instance_id=f"source:{source_id}", app=row["app"],
                        transport=transports.get(source_id),
                        timeout=timeout or float(opts.get("timeout", 20.0)),
                        retries=int(opts.get("retries", 2)),
                        backoff=float(opts.get("backoff", 0.5)), grant=grant)


async def detect(url: str, api_key: str, transport: Any = None,
                 opts: dict[str, Any] | None = None) -> dict[str, Any]:
    """Herkent app/versie via GET /api/v3/system/status (alleen-lezen)."""
    opts = opts or {}
    # app="sonarr" is alleen een label: system/status is voor beide apps gelijk
    client = SourceClient(url, api_key, instance_id="source:detect", app="sonarr",
                          transport=transport, timeout=float(opts.get("timeout", 15.0)),
                          retries=int(opts.get("retries", 1)),
                          backoff=float(opts.get("backoff", 0.5)))
    info = await client.system_status()
    name = str(info.get("appName") or "").lower()
    if name not in ("sonarr", "radarr"):
        raise ValueError(f"Geen Sonarr/Radarr op {url} (appName={info.get('appName')!r})")
    return {"app": name, "version": info.get("version"), "app_name": info.get("appName"),
            "instance_name": info.get("instanceName"),
            "database_type": info.get("databaseType"), "url_base": info.get("urlBase"),
            "is_docker": info.get("isDocker")}


async def register_live(ctx: AppContext, *, name: str, url: str, api_key: str, actor: str,
                        source_id: str | None = None) -> dict[str, Any]:
    validate_url(url)
    sid = source_id or unique_source_id(ctx, name)
    if not SOURCE_ID_RE.match(sid):
        raise ValueError("Bron-ID: kleine letters, cijfers en '-'")
    if source_row(ctx, sid) is not None:
        raise ValueError(f"Bron '{sid}' bestaat al")
    transports = ctx.services.get("source_transports") or {}
    info = await detect(url, api_key, transports.get(sid), ctx.services.get("arr_client_options"))
    now = utcnow()
    ctx.db.execute(
        "INSERT INTO source_instances(id,kind,app,name,url,version,app_name,instance_name,"
        "database_type,status,detected_json,created_at,created_by,updated_at) "
        "VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
        (sid, "live", info["app"], name, url.rstrip("/"), info["version"], info["app_name"],
         info["instance_name"], info["database_type"], "reachable", dumps(info), now, actor,
         now))
    ctx.secrets.set(apikey_secret(sid), api_key, actor)
    row = source_row(ctx, sid)
    assert row is not None
    return source_view(ctx, row)


async def retest(ctx: AppContext, source_id: str) -> dict[str, Any]:
    row = source_row(ctx, source_id)
    if row is None:
        raise KeyError(source_id)
    if row["kind"] != "live":
        return {"ok": True, "kind": "backup", "message_nl": "Back-upbron: geen live test nodig."}
    try:
        info = await source_client(ctx, source_id, timeout=10).system_status()
    except ArrError as exc:
        ctx.db.execute("UPDATE source_instances SET status='unreachable', last_error=?, "
                       "updated_at=? WHERE id=?", (exc.message, utcnow(), source_id))
        return {"ok": False, "error": exc.to_dict()}
    ctx.db.execute("UPDATE source_instances SET status='reachable', last_error=NULL, version=?,"
                   " updated_at=? WHERE id=?", (info.get("version"), utcnow(), source_id))
    return {"ok": True, "version": info.get("version"), "app_name": info.get("appName")}
