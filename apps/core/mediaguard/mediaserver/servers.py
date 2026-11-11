"""Meerdere mediaservers tegelijk (Δ-MG-DUAL, contract 1.3 §2.3 regel 2, C1.3-MGU-04).

MediaGuard volgt elke ingestelde mediaserver: Emby én Jelly Leon (Jellyfin 12.x), en zo nodig
Plex. Kijkpauze zodra iemand op één ervan kijkt; verversen, afspeeltest en NL-ondertitelcontrole
per server.

**Server 1 is de bestaande koppeling, zonder kopie (nul-migratie, ADR-0022).** Soort, adres,
sleutel en padvertaling van 1.2.2 blijven precies waar ze stonden: de instellingen
``mediaserver.kind`` en ``mediaserver.url``, het geheim ``mediaserver.api_key`` en de
statussleutels ``mediaserver_path_map``/``mediaserver_connected``. Na de upgrade is dat zonder
één handeling server 1; de gebruiker voert niets opnieuw in, en een terugzet naar de vorige image
vindt dezelfde koppeling terug. Zo kan de Emby-koppeling bij de upgrade niet wegvallen.

**Extra servers** (Jelly Leon naast Emby) staan in ``mediaserver_servers`` (migratie 150), met
het geheim ``mediaserver.server.<id>.api_key`` en eigen statussleutels
``mediaserver:<id>:<naam>``. Een fout in een extra server raakt server 1 nooit: elke server
heeft een eigen client, een eigen poll en een eigen foutstatus.

Losmaken (controlelijst "Emby verwijderen", B03 §10.9.2 stap 5): de beheerder zet een server uit
(server 1: soort "Uit"; een extra server: ``enabled=0``) of verwijdert een extra server. MediaGuard
stopt dan met pollen, verversen en testen voor die server; de actiegeschiedenis blijft binnen de
bewaarregel en er komt geen incident.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from typing import TYPE_CHECKING, Any

from mediaguard.db import utcnow
from mediaguard.mediaserver.clients import Server, make_server

if TYPE_CHECKING:
    from mediaguard.context import AppContext

LEGACY_ID = 1
LEGACY_SECRET = "mediaserver.api_key"
EXTRA_KINDS = ("emby", "jellyfin", "plex")
LABEL = {"emby": "Emby", "jellyfin": "Jellyfin", "plex": "Plex"}
#: statussleutels van server 1 die al in 1.2.2 bestonden (ongewijzigd laten)
LEGACY_STATE = {"path_map": "mediaserver_path_map", "connected": "mediaserver_connected",
                "full_refresh_at": "mediaserver_full_refresh_at"}
#: zonder geslaagd contact binnen deze tijd telt een server als niet verbonden
CONNECTED_FRESH_MIN = 10
MAX_EXTRA = 8


@dataclass(frozen=True)
class ServerConf:
    id: int
    kind: str             # emby|jellyfin|plex (effectief, ook na 'auto')
    label: str
    url: str
    url_source: str       # ingesteld|automatisch gevonden|niet gevonden
    key_set: bool
    enabled: bool
    legacy: bool          # server 1 = de bestaande koppeling (mediaserver.*)
    position: int

    @property
    def followed(self) -> bool:
        """Wordt deze server gevolgd (pollen, verversen, testen, kijkpauze)?"""
        return self.enabled and bool(self.url) and self.key_set

    @property
    def secret_name(self) -> str:
        return LEGACY_SECRET if self.legacy else f"mediaserver.server.{self.id}.api_key"


def secret_name(server_id: int) -> str:
    return LEGACY_SECRET if server_id == LEGACY_ID else f"mediaserver.server.{server_id}.api_key"


def state_key(server: ServerConf | int, name: str) -> str:
    sid = server if isinstance(server, int) else server.id
    if sid == LEGACY_ID and name in LEGACY_STATE:
        return LEGACY_STATE[name]
    return f"mediaserver:{sid}:{name}"


def sget(ctx: AppContext, server: ServerConf | int, name: str, default: Any = None) -> Any:
    return ctx.state_get(state_key(server, name), default)


def sset(ctx: AppContext, server: ServerConf | int, name: str, value: Any) -> None:
    ctx.state_set(state_key(server, name), value)


def _table(ctx: AppContext) -> bool:
    row = ctx.db.fetch_one("SELECT 1 FROM sqlite_master WHERE type='table' AND "
                           "name='mediaserver_servers'")
    return row is not None


def legacy(ctx: AppContext) -> ServerConf | None:
    """Server 1 uit de bestaande instellingen (precies ``service.configured``); None = uit."""
    from mediaguard.mediaserver.service import configured

    cfg = configured(ctx)
    if cfg["kind"] == "off":
        return None
    return ServerConf(id=LEGACY_ID, kind=cfg["kind"], label=LABEL.get(cfg["kind"], cfg["kind"]),
                      url=cfg["url"], url_source=cfg["source"], key_set=bool(cfg["key_set"]),
                      enabled=True, legacy=True, position=0)


def extras(ctx: AppContext, *, include_disabled: bool = True) -> list[ServerConf]:
    if not _table(ctx):
        return []
    rows = ctx.db.fetch_dicts("SELECT id, kind, label, url, enabled, position FROM "
                              "mediaserver_servers WHERE id>=2 ORDER BY position, id")
    out = [ServerConf(id=int(r["id"]), kind=str(r["kind"]), label=str(r["label"]),
                      url=str(r["url"]), url_source="ingesteld",
                      key_set=ctx.secrets.has(secret_name(int(r["id"]))),
                      enabled=bool(r["enabled"]), legacy=False, position=int(r["position"]))
           for r in rows]
    return [s for s in out if include_disabled or s.enabled]


def all_servers(ctx: AppContext) -> list[ServerConf]:
    """Server 1 (als die niet uit staat) en daarna de extra servers, in volgorde."""
    first = legacy(ctx)
    return ([first] if first else []) + extras(ctx)


def followed(ctx: AppContext) -> list[ServerConf]:
    return [s for s in all_servers(ctx) if s.followed]


def get(ctx: AppContext, server_id: int) -> ServerConf | None:
    return next((s for s in all_servers(ctx) if s.id == server_id), None)


def client(ctx: AppContext, server: ServerConf) -> Server:
    return make_server(server.kind, ctx.services, server.url, ctx.secrets.get(server.secret_name),
                       server.label)


# -- verbonden (per server) --------------------------------------------------------------------
def mark_contact(ctx: AppContext, server: ServerConf, ok: bool, error: str | None = None) -> None:
    prev = sget(ctx, server, "contact", {}) or {}
    value = {"at": utcnow(), "ok": ok, "error": None if ok else (error or "")[:300],
             "last_ok_at": utcnow() if ok else prev.get("last_ok_at")}
    sset(ctx, server, "contact", value)


def connected(ctx: AppContext, server: ServerConf) -> bool:
    """Verbonden = gevolgd en het laatste contact (poll of test) slaagde, niet ouder dan
    ``CONNECTED_FRESH_MIN`` minuten. Nooit gesimuleerd: zonder meting is het ``False``."""
    if not server.followed:
        return False
    contact = sget(ctx, server, "contact", {}) or {}
    if not contact.get("ok") or not contact.get("at"):
        return False
    try:
        at = datetime.fromisoformat(str(contact["at"]))
    except ValueError:
        return False
    return datetime.now(UTC) - at <= timedelta(minutes=CONNECTED_FRESH_MIN)


# -- extra servers beheren ---------------------------------------------------------------------
def add(ctx: AppContext, kind: str, url: str, label: str | None, actor: str) -> int:
    if kind not in EXTRA_KINDS:
        raise ValueError(f"onbekende soort: {kind}")
    if not url:
        raise ValueError("adres ontbreekt")
    n = ctx.db.fetch_one("SELECT COUNT(*) AS n, COALESCE(MAX(id),1) AS m, "
                         "COALESCE(MAX(position),0) AS p FROM mediaserver_servers")
    if n and int(n["n"]) >= MAX_EXTRA:
        raise ValueError(f"hooguit {MAX_EXTRA} extra mediaservers")
    new_id = max(LEGACY_ID, int(n["m"]) if n else LEGACY_ID) + 1
    position = (int(n["p"]) if n else 0) + 1
    now = utcnow()
    ctx.db.execute(
        "INSERT INTO mediaserver_servers(id,kind,label,url,enabled,position,created_at,"
        "updated_at,created_by) VALUES (?,?,?,?,1,?,?,?,?)",
        (new_id, kind, (label or LABEL[kind]).strip()[:60] or LABEL[kind], url, position, now,
         now, actor))
    return new_id


def update(ctx: AppContext, server_id: int, *, kind: str | None = None, url: str | None = None,
           label: str | None = None, enabled: bool | None = None) -> None:
    sets: list[str] = []
    args: list[Any] = []
    if kind is not None:
        if kind not in EXTRA_KINDS:
            raise ValueError(f"onbekende soort: {kind}")
        sets.append("kind=?")
        args.append(kind)
    if url is not None:
        if not url:
            raise ValueError("adres ontbreekt")
        sets.append("url=?")
        args.append(url)
        ctx.state_set(state_key(server_id, "path_map"), {})
    if label is not None:
        sets.append("label=?")
        args.append(label.strip()[:60] or "Mediaserver")
    if enabled is not None:
        sets.append("enabled=?")
        args.append(1 if enabled else 0)
    if not sets:
        return
    sets.append("updated_at=?")
    args += [utcnow(), server_id]
    ctx.db.execute(f"UPDATE mediaserver_servers SET {', '.join(sets)} WHERE id=?", tuple(args))


def remove(ctx: AppContext, server_id: int) -> None:
    """Een extra server verwijderen: geheim en statussleutels weg, de actiegeschiedenis blijft
    (binnen de bewaarregel); open taken voor deze server vervallen ('skipped')."""
    ctx.db.execute("DELETE FROM mediaserver_servers WHERE id=?", (server_id,))
    ctx.secrets.delete(secret_name(server_id))
    ctx.db.execute("DELETE FROM system_state WHERE key LIKE ?", (f"mediaserver:{server_id}:%",))
    drop_open_work(ctx, server_id, "server verwijderd")


def drop_open_work(ctx: AppContext, server_id: int, reason: str) -> None:
    """Open verversingen/afspeeltests van een losgemaakte server vervallen (geen incident)."""
    ctx.db.execute(
        "UPDATE mediaserver_server_results SET refreshed_at=COALESCE(refreshed_at,?), "
        "done_at=?, result=COALESCE(result,'skipped'), detail_nl=COALESCE(detail_nl,?) "
        "WHERE server_id=? AND done_at IS NULL", (utcnow(), utcnow(), reason, server_id))
    ctx.db.execute("DELETE FROM system_state WHERE key=?",
                   (state_key(server_id, "watching"),))


def exists(ctx: AppContext, server_id: int) -> bool:
    return ctx.db.fetch_one("SELECT 1 FROM mediaserver_servers WHERE id=?",
                            (server_id,)) is not None
