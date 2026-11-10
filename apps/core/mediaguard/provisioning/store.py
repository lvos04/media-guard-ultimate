"""Centrale inrichtingsconfiguratie (DB + secrets), eigenaarschap, runs en status.

Alles wat de gebruiker éénmaal invoert staat hier: downloadclients, indexers of Prowlarr,
mediapaden per instantie, mediabeheer/naamgeving, kwaliteitssjablonen en Seerr. Geheimen
worden versleuteld in de secrets-store bewaard en NOOIT teruggegeven (alleen ``*_set``)."""

from __future__ import annotations

import hashlib
import re
from typing import TYPE_CHECKING, Any, Literal
from urllib.parse import urlsplit

from pydantic import BaseModel, Field, field_validator, model_validator

from mediaguard.db import dumps, loads, utcnow
from mediaguard.provisioning import templates as T

if TYPE_CHECKING:
    from mediaguard.context import AppContext

SECTIONS = ("tags", "rootfolders", "mediamanagement", "naming", "qualitydefinitions",
            "customformats", "qualityprofiles", "delayprofile", "releaseprofiles",
            "downloadclients", "indexers", "webhook", "prowlarr", "seerr")
INSTANCE_SECTIONS = SECTIONS[:-2]
GLOBAL_SECTIONS = ("prowlarr", "seerr")
ESSENTIAL_SECTIONS = ("tags", "rootfolders", "webhook")
# Wat MediaGuard bij opstarten en bij de drift-controle zelf bijhoudt (instanties in 'hold'
# krijgen alleen de essentials). Groottes en MediaGuard-formats/profielen horen erbij zodat
# een nieuwe versie (bv. geen groottelimiet meer) vanzelf wordt doorgevoerd.
AUTO_SECTIONS = (*ESSENTIAL_SECTIONS, "mediamanagement", "qualitydefinitions", "customformats",
                 "qualityprofiles")
FULL_MANAGE_SECTIONS = ("downloadclients", "indexers", "customformats")
SECTION_NL = {
    "tags": "Tags", "rootfolders": "Mediapaden (rootmappen)",
    "mediamanagement": "Mediabeheer", "naming": "Naamgeving",
    "qualitydefinitions": "Kwaliteitsdefinities", "customformats": "Custom formats",
    "qualityprofiles": "Kwaliteitsprofielen", "delayprofile": "Vertragingsprofiel",
    "releaseprofiles": "Releaseprofielen", "downloadclients": "Downloadclients",
    "indexers": "Indexers", "webhook": "MediaGuard-webhook", "prowlarr": "Prowlarr",
    "seerr": "Seerr"}

NAME_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9 ._()+-]{0,39}$")
MASK = "********"
Scope = Literal["normal", "anime", "both"]
Section = Literal["tags", "rootfolders", "mediamanagement", "naming", "qualitydefinitions",
                  "customformats", "qualityprofiles", "delayprofile", "releaseprofiles",
                  "downloadclients", "indexers", "webhook", "prowlarr", "seerr"]


def _name(value: str) -> str:
    value = value.strip()
    if not NAME_RE.match(value):
        raise ValueError("Naam: 1-40 tekens (letters, cijfers, spatie, . _ - ( ) +)")
    return value


class DownloadClientCfg(BaseModel):
    name: str
    implementation: Literal["Sabnzbd", "Nzbget", "QBittorrent", "Transmission", "Deluge"]
    enabled: bool = True
    host: str = Field(max_length=255)
    port: int = Field(ge=1, le=65535)
    use_ssl: bool = False
    url_base: str = Field(default="", max_length=200)
    username: str = Field(default="", max_length=200)
    priority: int = Field(default=1, ge=1, le=50)
    instances: list[str] | None = None  # None = alle instanties
    categories: dict[str, str] = Field(default_factory=dict)  # instance_id -> categorie
    extra_fields: dict[str, Any] = Field(default_factory=dict)
    # alleen schrijven; nooit teruggegeven
    api_key: str | None = Field(default=None, max_length=500)
    password: str | None = Field(default=None, max_length=500)

    @model_validator(mode="before")
    @classmethod
    def _split_url(cls, data: Any) -> Any:
        """Een geplakte URL ('http://nas.lan:30055/sabnzbd') wordt host, poort, SSL en
        URL-basis; zo hoeft niemand te weten hoe Sonarr/Radarr die velden willen."""
        if not isinstance(data, dict) or not isinstance(data.get("host"), str):
            return data
        base = str(data.get("url_base") or "").strip()
        if "://" in base:
            # URL in het verkeerde veld (URL-basis): die is leidend voor adres en poort
            data = {**data, "host": base, "url_base": ""}
        raw = data["host"].strip()
        if "://" in raw:
            parsed = urlsplit(raw)
            data = {**data, "host": parsed.hostname or "",
                    "use_ssl": parsed.scheme == "https"}
            if parsed.port:
                data["port"] = parsed.port
            if parsed.path.strip("/") and not str(data.get("url_base") or "").strip("/"):
                data["url_base"] = parsed.path
        else:
            data = {**data, "host": raw.strip("/")}
        return data

    @field_validator("name")
    @classmethod
    def _valid_name(cls, v: str) -> str:
        return _name(v)

    @field_validator("host")
    @classmethod
    def _valid_host(cls, v: str) -> str:
        v = v.strip()
        if not v:
            raise ValueError("vul het adres van de downloadclient in (bv. nas.lan of "
                             "sabnzbd)")
        if any(c in v for c in " /?#@"):
            raise ValueError("alleen een hostnaam of IP-adres, zonder pad of spaties")
        return v

    @field_validator("url_base")
    @classmethod
    def _valid_url_base(cls, v: str) -> str:
        # Sonarr/Radarr eisen '/pad' zonder slash aan het eind, of leeg
        v = v.strip().strip("/")
        return f"/{v}" if v else ""

    @property
    def protocol(self) -> str:
        return T.DC_IMPLEMENTATIONS[self.implementation]


class IndexerCfg(BaseModel):
    name: str
    implementation: Literal["Newznab", "Torznab"]
    base_url: str = Field(min_length=4, max_length=500)
    api_path: str = Field(default="/api", max_length=100)
    scope: Scope | None = None  # None = automatisch (anime-indexers alleen anime)
    instances: list[str] | None = None  # expliciete toewijzing (overschrijft scope)
    enabled: bool = True
    priority: int = Field(default=25, ge=1, le=50)
    categories: list[int] | None = None
    anime_categories: list[int] | None = None
    movie_categories: list[int] | None = None
    minimum_seeders: int = Field(default=1, ge=0, le=1000)
    extra_fields: dict[str, Any] = Field(default_factory=dict)
    api_key: str | None = Field(default=None, max_length=500)

    @field_validator("name")
    @classmethod
    def _valid_name(cls, v: str) -> str:
        return _name(v)

    def effective_scope(self) -> str:
        if self.scope:
            return self.scope
        return "anime" if T.is_anime_name(self.name, self.base_url) else "both"


class ProwlarrCfg(BaseModel):
    url: str = Field(default="", max_length=500)  # hoe MediaGuard Prowlarr bereikt
    url_from_arr: str = Field(default="", max_length=500)  # hoe de forks Prowlarr bereiken
    instance_urls: dict[str, str] = Field(default_factory=dict)  # hoe Prowlarr de forks ziet
    manage_indexer_tags: bool = True
    indexer_scopes: dict[str, Scope] = Field(default_factory=dict)  # prowlarr-indexer-id
    sync_level: Literal["fullSync", "addOnly"] = "fullSync"
    api_key: str | None = Field(default=None, max_length=500)


class InstanceCfg(BaseModel):
    root_folder: str | None = Field(default=None, max_length=500)
    category: str | None = Field(default=None, max_length=100)
    mode: Literal["auto", "manage", "hold"] = "auto"
    full_manage: list[Section] = Field(default_factory=list)
    disabled_sections: list[Section] = Field(default_factory=list)

    @field_validator("root_folder")
    @classmethod
    def _root(cls, v: str | None) -> str | None:
        if v is None or v == "":
            return None
        if not v.startswith("/") or ".." in v:
            raise ValueError("Mediapad moet een absoluut pad zijn (bijv. /media/tv)")
        return v.rstrip("/") or "/"


class MediaCfg(BaseModel):
    hardlinks: bool = True
    rename: bool = True
    # doNotPrefer (TRaSH): een repack/proper is alleen beter als de custom formats dat zeggen.
    # 'preferAndUpgrade' liet Radarr een REPACK grabben die de import daarna weigerde
    # ('geen verbetering in custom formats') — een downloadlus (rapport 4, S3).
    propers: Literal["preferAndUpgrade", "doNotUpgrade", "doNotPrefer"] = "doNotPrefer"
    recycle_bin: str = Field(default="", max_length=500)
    unmonitor_deleted: bool = False
    import_extra_files: bool = True
    extra_file_extensions: str = Field(default="srt,ass,ssa,sub,idx", max_length=200)
    naming_overrides: dict[str, str] = Field(default_factory=dict)


class QualityCfg(BaseModel):
    enabled: bool = True
    language_films: Literal["original", "any"] = "original"
    score_overrides: dict[str, dict[str, int]] = Field(default_factory=dict)
    extra_trash_formats: dict[str, list[str]] = Field(default_factory=dict)
    quality_definitions: bool = True


class DelayCfg(BaseModel):
    preferred_protocol: Literal["auto", "usenet", "torrent"] = "auto"
    usenet_delay: int = Field(default=0, ge=0, le=10080)
    torrent_delay: int = Field(default=0, ge=0, le=10080)
    bypass_if_highest_quality: bool = True


class SeerrCfg(BaseModel):
    enabled: bool = True
    scan: bool = True


class ProvisioningConfig(BaseModel):
    indexer_mode: Literal["none", "prowlarr", "direct"] = "none"
    download_clients: list[DownloadClientCfg] = Field(default_factory=list)
    indexers: list[IndexerCfg] = Field(default_factory=list)
    prowlarr: ProwlarrCfg = Field(default_factory=ProwlarrCfg)
    instances: dict[str, InstanceCfg] = Field(default_factory=dict)
    media: MediaCfg = Field(default_factory=MediaCfg)
    quality: QualityCfg = Field(default_factory=QualityCfg)
    delay: DelayCfg = Field(default_factory=DelayCfg)
    seerr: SeerrCfg = Field(default_factory=SeerrCfg)

    def instance(self, iid: str) -> InstanceCfg:
        return self.instances.get(iid) or InstanceCfg()


# -- secrets -----------------------------------------------------------------------------
def dc_secret(name: str, field: str) -> str:
    return f"downloadclient:{name}:{'apikey' if field == 'api_key' else 'password'}"


def indexer_secret(name: str) -> str:
    return f"indexer:{name}:apikey"


PROWLARR_SECRET = "prowlarr:apikey"


def secret_hash(*values: str | None) -> str:
    return hashlib.sha256("\x00".join(v or "" for v in values).encode()).hexdigest()[:16]


# -- laden / opslaan ---------------------------------------------------------------------
CONFIG_KEY = "main"


def load(ctx: AppContext) -> ProvisioningConfig:
    row = ctx.db.fetch_one("SELECT value_json FROM provisioning_config WHERE key=?",
                           (CONFIG_KEY,))
    data = loads(row["value_json"], {}) if row else {}
    media = data.get("media") if isinstance(data, dict) else None
    if isinstance(media, dict) and media.get("propers") == "preferAndUpgrade" \
            and not ctx.state_get("provisioning:propers_r4", False):
        # eenmalig: de oude standaard veroorzaakte downloadlussen (S3)
        media["propers"] = "doNotPrefer"
        ctx.db.execute("UPDATE provisioning_config SET value_json=? WHERE key=?",
                       (dumps(data), CONFIG_KEY))
        ctx.state_set("provisioning:propers_r4", True)
    return ProvisioningConfig.model_validate(data)


def _store_secret(ctx: AppContext, name: str, value: str | None, actor: str) -> None:
    if value is None or value == MASK:
        return
    if value == "":
        ctx.secrets.delete(name)
    else:
        ctx.secrets.set(name, value, actor)


def save(ctx: AppContext, patch: dict[str, Any], actor: str) -> ProvisioningConfig:
    """Gedeeltelijke update: alleen meegegeven topniveau-sleutels worden vervangen.
    Geheimen in het verzoek gaan naar de secrets-store; ``None``/``********`` = ongewijzigd,
    ``""`` = wissen. Valideert het complete document vóór opslaan."""
    current = load(ctx).model_dump()
    merged = {**current, **{k: v for k, v in patch.items() if k != "seerr_connection"}}
    cfg = ProvisioningConfig.model_validate(merged)
    names = [d.name for d in cfg.download_clients]
    if len(set(names)) != len(names):
        raise ValueError("Downloadclientnamen moeten uniek zijn")
    inames = [i.name for i in cfg.indexers]
    if len(set(inames)) != len(inames):
        raise ValueError("Indexernamen moeten uniek zijn")
    for dc in cfg.download_clients:
        _store_secret(ctx, dc_secret(dc.name, "api_key"), dc.api_key, actor)
        _store_secret(ctx, dc_secret(dc.name, "password"), dc.password, actor)
        dc.api_key = dc.password = None
    for idx in cfg.indexers:
        _store_secret(ctx, indexer_secret(idx.name), idx.api_key, actor)
        idx.api_key = None
    _store_secret(ctx, PROWLARR_SECRET, cfg.prowlarr.api_key, actor)
    cfg.prowlarr.api_key = None
    ctx.db.execute(
        "INSERT INTO provisioning_config(key,value_json,updated_at,updated_by) VALUES (?,?,?,?) "
        "ON CONFLICT(key) DO UPDATE SET value_json=excluded.value_json, "
        "updated_at=excluded.updated_at, updated_by=excluded.updated_by",
        (CONFIG_KEY, dumps(cfg.model_dump()), utcnow(), actor))
    # verwijderde namen: geheimen opruimen
    for row in ctx.db.fetch_all("SELECT name FROM secrets WHERE name LIKE 'downloadclient:%' "
                                "OR name LIKE 'indexer:%'"):
        parts = str(row["name"]).split(":")
        if parts[0] == "downloadclient" and parts[1] not in names or \
                parts[0] == "indexer" and parts[1] not in inames:
            ctx.secrets.delete(row["name"])
    return cfg


def public_view(ctx: AppContext, cfg: ProvisioningConfig | None = None) -> dict[str, Any]:
    from mediaguard.seerr.client import SECRET_NAME as SEERR_SECRET

    cfg = cfg or load(ctx)
    data = cfg.model_dump()
    for dc in data["download_clients"]:
        dc.pop("api_key", None)
        dc.pop("password", None)
        dc["api_key_set"] = ctx.secrets.has(dc_secret(dc["name"], "api_key"))
        dc["password_set"] = ctx.secrets.has(dc_secret(dc["name"], "password"))
        dc["protocol"] = T.DC_IMPLEMENTATIONS[dc["implementation"]]
    for idx, model in zip(data["indexers"], cfg.indexers, strict=True):
        idx.pop("api_key", None)
        idx["api_key_set"] = ctx.secrets.has(indexer_secret(idx["name"]))
        idx["effective_scope"] = model.effective_scope()
    data["prowlarr"].pop("api_key", None)
    data["prowlarr"]["api_key_set"] = ctx.secrets.has(PROWLARR_SECRET)
    data["seerr_connection"] = {"url": str(ctx.config.value("seerr.url") or ""),
                                "api_key_set": ctx.secrets.has(SEERR_SECRET)}
    data["defaults"] = defaults_view(ctx)
    return data


def defaults_view(ctx: AppContext) -> dict[str, Any]:
    from mediaguard.instances.registry import list_instances

    out = {}
    cfg = load(ctx)
    for inst in list_instances(ctx):
        row = ctx.db.fetch_one("SELECT download_category, default_root_folder FROM instances "
                               "WHERE id=?", (inst.id,))
        out[inst.id] = {"root_folder": root_folder_for(ctx, inst.id, inst.media_type, cfg),
                        "category": category_for(ctx, inst.id, inst.media_type, cfg),
                        "profiles": [T.owned_name(T.QUALITY_TEMPLATES[t]["name"])
                                     for t in T.templates_for(inst.media_type)],
                        "registry_root": row["default_root_folder"] if row else None}
    return out


def root_folder_for(ctx: AppContext, iid: str, media_type: str,
                    cfg: ProvisioningConfig) -> str:
    own = cfg.instance(iid).root_folder
    if own:
        return own
    row = ctx.db.fetch_one("SELECT default_root_folder FROM instances WHERE id=?", (iid,))
    if row and row["default_root_folder"]:
        return str(row["default_root_folder"]).rstrip("/") or "/"
    return T.DEFAULT_ROOT.get(media_type, f"/data/media/{iid}")


def category_for(ctx: AppContext, iid: str, media_type: str, cfg: ProvisioningConfig,
                 dc: DownloadClientCfg | None = None) -> str:
    if dc is not None and dc.categories.get(iid):
        return dc.categories[iid]
    own = cfg.instance(iid).category
    if own:
        return own
    row = ctx.db.fetch_one("SELECT download_category FROM instances WHERE id=?", (iid,))
    if row and row["download_category"]:
        return str(row["download_category"])
    return T.DEFAULT_CATEGORY.get(media_type, iid)


# -- benodigde invoer ----------------------------------------------------------------------
def needs(ctx: AppContext, cfg: ProvisioningConfig | None = None) -> list[dict[str, Any]]:
    """Wat de gebruiker nog moet invoeren (zonder netwerk)."""
    from mediaguard.instances.registry import apikey_secret, list_instances
    from mediaguard.seerr.client import SECRET_NAME as SEERR_SECRET

    cfg = cfg or load(ctx)
    out: list[dict[str, Any]] = []

    def need(key: str, msg: str, section: str, severity: str = "required",
             instance_id: str | None = None) -> None:
        out.append({"key": key, "message_nl": msg, "section": section, "severity": severity,
                    "instance_id": instance_id})

    for inst in list_instances(ctx):
        if not ctx.secrets.has(apikey_secret(inst.id)):
            need(f"instance:{inst.id}:apikey", f"API-sleutel van {inst.name} ontbreekt",
                 "instances", instance_id=inst.id)
    if not cfg.download_clients:
        need("downloadclients", "Nog geen downloadclient ingevoerd", "downloadclients")
    for dc in cfg.download_clients:
        for field in T.DC_REQUIRED_SECRETS[dc.implementation]:
            if not ctx.secrets.has(dc_secret(dc.name, field)):
                label = "API-sleutel" if field == "api_key" else "wachtwoord"
                need(f"downloadclient:{dc.name}:{field}",
                     f"downloadclient-{label} ontbreekt voor '{dc.name}'", "downloadclients")
    if cfg.indexer_mode == "none":
        need("indexer_mode", "Kies Prowlarr of voer indexers in", "indexers")
    elif cfg.indexer_mode == "prowlarr":
        if not cfg.prowlarr.url:
            need("prowlarr:url", "Prowlarr-URL ontbreekt", "prowlarr")
        if not ctx.secrets.has(PROWLARR_SECRET):
            need("prowlarr:apikey", "Prowlarr-API-sleutel ontbreekt", "prowlarr")
    else:
        if not cfg.indexers:
            need("indexers", "Nog geen indexer ingevoerd", "indexers")
        for idx in cfg.indexers:
            if not ctx.secrets.has(indexer_secret(idx.name)):
                need(f"indexer:{idx.name}:apikey",
                     f"indexer-API-sleutel ontbreekt voor '{idx.name}'", "indexers")
    for inst in list_instances(ctx):
        if resolve_mode(ctx, inst.id, cfg) == "hold":
            need(f"optimize:{inst.id}", f"{inst.name} is gemigreerd: bekijk en bevestig "
                 "'Optimaliseren na migratie'", "optimize", "confirm", inst.id)
    if not ctx.config.value("seerr.url") or not ctx.secrets.has(SEERR_SECRET):
        need("seerr", "Seerr nog niet gekoppeld (URL + API-sleutel)", "seerr", "optional")
    return out


# -- eigenaarschap -------------------------------------------------------------------------
def items(ctx: AppContext, instance_id: str, section: str) -> dict[str, dict[str, Any]]:
    return {r["item_key"]: r for r in ctx.db.fetch_dicts(
        "SELECT * FROM provisioning_items WHERE instance_id=? AND section=?",
        (instance_id, section))}


def item_set(ctx: AppContext, instance_id: str, section: str, key: str, *,
             remote_id: int | None = None, ownership: str = "mediaguard",
             secret: str | None = None, actor: str = "system", applied: bool = True) -> None:
    now = utcnow()
    ctx.db.execute(
        "INSERT INTO provisioning_items(instance_id,section,item_key,remote_id,ownership,"
        "secret_hash,applied_at,updated_at,updated_by) VALUES (?,?,?,?,?,?,?,?,?) "
        "ON CONFLICT(instance_id,section,item_key) DO UPDATE SET "
        "remote_id=COALESCE(excluded.remote_id,remote_id), ownership=excluded.ownership, "
        "secret_hash=COALESCE(excluded.secret_hash,secret_hash), "
        "applied_at=COALESCE(excluded.applied_at,applied_at), updated_at=excluded.updated_at, "
        "updated_by=excluded.updated_by",
        (instance_id, section, key, remote_id, ownership, secret,
         now if applied else None, now, actor))


def item_delete(ctx: AppContext, instance_id: str, section: str, key: str) -> None:
    ctx.db.execute("DELETE FROM provisioning_items WHERE instance_id=? AND section=? AND "
                   "item_key=?", (instance_id, section, key))


# -- runs en status ------------------------------------------------------------------------
def run_start(ctx: AppContext, kind: str, actor: str, sections: list[str],
              instances: list[str]) -> int:
    return ctx.db.execute_insert(
        "INSERT INTO provisioning_runs(kind,actor,sections_json,instances_json,started_at) "
        "VALUES (?,?,?,?,?)", (kind, actor, dumps(sections), dumps(instances), utcnow()))


def run_finish(ctx: AppContext, run_id: int, status: str, summary: dict[str, Any],
               changes: list[dict[str, Any]]) -> None:
    ctx.db.execute("UPDATE provisioning_runs SET status=?, summary_json=?, changes_json=?, "
                   "finished_at=? WHERE id=?",
                   (status, dumps(summary), dumps(changes), utcnow(), run_id))


def runs(ctx: AppContext, limit: int = 20, kind: str | None = None) -> list[dict[str, Any]]:
    sql = "SELECT * FROM provisioning_runs"
    params: list[Any] = []
    if kind:
        sql += " WHERE kind=?"
        params.append(kind)
    rows = ctx.db.fetch_dicts(sql + " ORDER BY id DESC LIMIT ?", (*params, limit))
    for r in rows:
        for k in ("sections_json", "instances_json", "summary_json", "changes_json"):
            r[k[:-5]] = loads(r.pop(k), None)
    return rows


def status_set(ctx: AppContext, instance_id: str, section: str, status: str, message: str,
               changes: list[dict[str, Any]]) -> None:
    ctx.db.execute(
        "INSERT INTO provisioning_status(instance_id,section,status,message_nl,changes,"
        "detail_json,checked_at) VALUES (?,?,?,?,?,?,?) ON CONFLICT(instance_id,section) DO "
        "UPDATE SET status=excluded.status, message_nl=excluded.message_nl, "
        "changes=excluded.changes, detail_json=excluded.detail_json, "
        "checked_at=excluded.checked_at",
        (instance_id, section, status, message, len(changes), dumps(changes), utcnow()))


def status_all(ctx: AppContext) -> list[dict[str, Any]]:
    rows = ctx.db.fetch_dicts("SELECT * FROM provisioning_status ORDER BY instance_id, section")
    for r in rows:
        r["detail"] = loads(r.pop("detail_json"), [])
    return rows


# -- instantiestatus ------------------------------------------------------------------------
def initialized(ctx: AppContext, iid: str) -> bool:
    return bool(ctx.state_get(f"provisioning:initialized:{iid}", False))


def mark_initialized(ctx: AppContext, iid: str) -> None:
    ctx.state_set(f"provisioning:initialized:{iid}", True)


def shadow_import_done(ctx: AppContext, iid: str) -> bool:
    row = ctx.db.fetch_one(
        "SELECT 1 FROM migration_steps WHERE step='shadow_import' AND scope=? AND "
        "status IN ('succeeded','warning') LIMIT 1", (iid,))
    return row is not None


def optimize_row(ctx: AppContext, iid: str) -> dict[str, Any] | None:
    row = ctx.db.fetch_one("SELECT * FROM provisioning_optimize WHERE instance_id=?", (iid,))
    return dict(row) if row else None


def is_provisioned(ctx: AppContext, iid: str) -> bool:
    """Heeft MediaGuard deze instantie al eens volledig ingericht (eigen profielen/formats)?"""
    row = ctx.db.fetch_one(
        "SELECT 1 AS x FROM provisioning_items WHERE instance_id=? AND section IN "
        "('qualityprofiles','customformats') LIMIT 1", (iid,))
    return row is not None


def resolve_mode(ctx: AppContext, iid: str, cfg: ProvisioningConfig) -> str:
    """manage = volledige inrichting; hold = gemigreerde instantie die nog op bevestiging
    van 'optimaliseren na migratie' wacht (alleen essentials); optimized = gemigreerd én
    geoptimaliseerd (volledige inrichting, gemigreerde inloggegevens blijven)."""
    mode = cfg.instance(iid).mode
    opt = optimize_row(ctx, iid)
    if opt and opt["phase"] in ("optimized", "cleaned"):
        return "optimized"
    if mode == "manage":
        return "manage"
    if mode == "hold":
        return "hold"
    return "hold" if shadow_import_done(ctx, iid) else "manage"


def active_shadow_phase(ctx: AppContext) -> list[str]:
    """Doelen waarvan de oude productie-instantie nog leidend is (schaduwfase loopt)."""
    rows = ctx.db.fetch_all(
        "SELECT s.target_instance_id FROM migration_shadow_states s JOIN migration_runs r ON "
        "r.id=s.run_id WHERE s.lifted_at IS NULL AND r.status='active'")
    return sorted({r["target_instance_id"] for r in rows})
