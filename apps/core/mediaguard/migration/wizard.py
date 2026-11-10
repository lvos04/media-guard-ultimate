"""Wizardstappen 1-8 (preflight t/m cutover-gereedheid) en bronregistratie via upload.

Alle toegang tot de OUDE instanties is hier alleen-lezen (``SourceClient``). Enige
uitzondering: het officiële Backup-commando, en alleen bij expliciete opt-in
(``strategy='create'`` door een admin). Schrijven gebeurt uitsluitend naar de
DOEL-forks (schaduwmodus) en naar MediaGuard's eigen /config.
"""

from __future__ import annotations

import asyncio
import contextlib
import os
import re
import shutil
from collections.abc import Callable
from pathlib import Path
from typing import TYPE_CHECKING, Any

import httpx

from mediaguard.arr.client import ArrClient, ArrError
from mediaguard.db import dumps, loads, utcnow
from mediaguard.instances.registry import (
    apikey_secret as target_key_secret,
)
from mediaguard.instances.registry import (
    arr_client,
    get_instance,
    instance_row,
    list_instances,
    update_instance,
)
from mediaguard.migration import store
from mediaguard.migration.backupfile import (
    BackupInvalid,
    build_shadow_zip,
    extracted,
    inspect_backup,
    normalize_rules,
    remap,
    rule_for,
    sha256_file,
)
from mediaguard.migration.importer import Importer, credentials_resolved
from mediaguard.migration.snapshot import (
    anime_signals,
    compare,
    from_api,
    from_db,
    remap_snapshot,
    summarize,
    verdict,
)
from mediaguard.migration.sources import (
    source_client,
    source_row,
    source_view,
    unique_source_id,
)

if TYPE_CHECKING:
    from mediaguard.context import AppContext

Log = Callable[[str], None]


class StepBlocked(Exception):
    """Voorwaarde niet vervuld; stap wordt 'blocked' met een Nederlandse uitleg."""


SHADOW_EXPECTED_HEALTH = {"IndexerRssCheck", "IndexerSearchCheck", "DownloadClientCheck",
                          "ImportListStatusCheck"}
# Meldingen over je bibliotheek of losse indexers/lijsten, niet over de werking van de fork
# zelf: nooit een reden om de overstap te blokkeren (wel tonen als waarschuwing).
INFORMATIONAL_HEALTH = {"RemovedMovieCheck", "RemovedSeriesCheck", "IndexerStatusCheck",
                        "IndexerLongTermStatusCheck", "IndexerJackettAllCheck",
                        "DownloadClientStatusCheck", "ImportListStatusCheck",
                        "ImportMechanismCheck", "UpdateCheck", "MountCheck",
                        "RecyclingBinCheck", "ProxyCheck", "NotificationStatusCheck",
                        "MissingMovieFolderCheck"}


def explain_provider_error(message: str) -> str:
    """Begrijpelijke uitleg bij een mislukte indexer-/downloadclienttest."""
    low = message.lower()
    if "404" in message and "/api" in low:
        return ("Prowlarr kent deze indexer niet (meer) (404). Waarschijnlijk is hij in Prowlarr "
                "verwijderd; na de overstap ruimt de Prowlarr-synchronisatie hem op.")
    if "no results in the configured categories" in low:
        return ("De indexer werkt, maar gaf geen resultaten in de ingestelde categorieën. "
                "Meestal onschuldig (bv. een anime-indexer in een film-instantie).")
    if "reageert niet" in low or "timed out" in low or "timeout" in low:
        return "De indexer reageerde niet op tijd; vaak tijdelijk."
    if "cloudflare" in low or "403" in message:
        return "De indexer weigert de verbinding (bv. Cloudflare); los op in Prowlarr."
    return "Test mislukt."


def _check(name: str, status: str, message: str, **data: Any) -> dict[str, Any]:
    return {"check": name, "status": status, "message_nl": message, **data}


def _verdict_of(checks: list[dict[str, Any]]) -> str:
    states = {c["status"] for c in checks}
    return "no_go" if "fail" in states else "warn" if "warn" in states else "go"


def max_backup_bytes(ctx: AppContext) -> int:
    return int(ctx.config.value("migration.max_backup_mb")) * 1024 * 1024


def backups_root(ctx: AppContext) -> Path:
    return ctx.settings.backup_dir / "sources"


def _version_tuple(text: str | None) -> tuple[int, ...]:
    return tuple(int(x) for x in re.findall(r"\d+", str(text or ""))[:4])


def latest_backup(ctx: AppContext, source_id: str) -> dict[str, Any] | None:
    row = ctx.db.fetch_one("SELECT * FROM source_backups WHERE source_id=? AND verified=1 "
                           "ORDER BY id DESC LIMIT 1", (source_id,))
    if row is None:
        return None
    out = dict(row)
    out["verify"] = loads(out.pop("verify_json"), {})
    return out


def _store_backup(ctx: AppContext, source_id: str, tmp: Path, name: str, origin: str,
                  actor: str) -> dict[str, Any]:
    """Controleert en archiveert een back-up onder /config/backups/sources/<bron>/."""
    info = inspect_backup(tmp, max_backup_bytes(ctx) * 4, name)
    if not info["ok"]:
        raise BackupInvalid(f"SQLite integrity_check faalt: {info['integrity']}")
    digest = sha256_file(tmp)
    dest_dir = backups_root(ctx) / source_id
    dest_dir.mkdir(parents=True, exist_ok=True)
    safe = re.sub(r"[^A-Za-z0-9._-]+", "_", Path(name).name)[-120:] or "backup.zip"
    dest = dest_dir / f"{digest[:12]}_{safe}"
    if not dest.exists():
        os.replace(tmp, dest)
        os.chmod(dest, 0o600)
    else:
        tmp.unlink(missing_ok=True)
    ctx.db.execute(
        "INSERT INTO source_backups(source_id,origin,backup_name,path,size,sha256,app,"
        "app_version,schema_version,verified,verify_json,created_at,created_by) "
        "VALUES (?,?,?,?,?,?,?,?,?,1,?,?,?) ON CONFLICT(source_id,sha256) DO NOTHING",
        (source_id, origin, safe, str(dest), dest.stat().st_size, digest, info["app"],
         info["app_version"], info["schema_version"], dumps(info), utcnow(), actor))
    backup = latest_backup(ctx, source_id)
    assert backup is not None
    return backup


def register_backup_upload(ctx: AppContext, tmp: Path, filename: str, name: str | None,
                           actor: str) -> dict[str, Any]:
    """Bron uit een geüploade native back-up (geen live verbinding)."""
    try:
        info = inspect_backup(tmp, max_backup_bytes(ctx) * 4, filename)
    except BackupInvalid:
        tmp.unlink(missing_ok=True)
        raise
    if not info["ok"]:
        tmp.unlink(missing_ok=True)
        raise BackupInvalid(f"SQLite integrity_check faalt: {info['integrity']}")
    label = name or info.get("instance_name") or Path(filename).stem
    sid = unique_source_id(ctx, label)
    now = utcnow()
    with extracted(tmp, max_backup_bytes(ctx) * 4) as ex:
        snap = from_db(ex["db_path"], info["app"])
    signals = anime_signals(snap, [label, filename, info.get("instance_name")])
    detected = {"app": info["app"], "version": info["app_version"],
                "schema_version": info["schema_version"], "counts": info["counts"],
                "anime": signals, "instance_name": info.get("instance_name"),
                "filename": filename}
    ctx.db.execute(
        "INSERT INTO source_instances(id,kind,app,name,url,version,app_name,instance_name,"
        "database_type,status,detected_json,created_at,created_by,updated_at) "
        "VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
        (sid, "backup", info["app"], label, None, info["app_version"],
         info["app"].capitalize(), info.get("instance_name"), "sqLite", "backup_only",
         dumps(detected), now, actor, now))
    _store_backup(ctx, sid, tmp, filename, "upload", actor)
    row = source_row(ctx, sid)
    assert row is not None
    view = source_view(ctx, row)
    view["suggested_target"] = suggest_target(ctx, info["app"], signals["score"])
    return view


def suggest_target(ctx: AppContext, app: str, anime_score: float) -> str | None:
    want_anime = anime_score >= 0.5
    for inst in list_instances(ctx):
        if inst.app == app and (inst.media_type in ("anime", "anime_movie")) == want_anime:
            return inst.id
    return None


async def _source_snapshot(ctx: AppContext, source_id: str, *, episodes: bool) -> dict:
    row = source_row(ctx, source_id)
    if row is None:
        raise KeyError(source_id)
    if row["kind"] == "live":
        return await from_api(source_client(ctx, source_id), episodes=episodes)
    backup = latest_backup(ctx, source_id)
    if backup is None:
        raise StepBlocked(f"Bron '{source_id}' heeft geen gecontroleerde back-up")
    with extracted(Path(backup["path"]), max_backup_bytes(ctx) * 4) as ex:
        return from_db(ex["db_path"], row["app"])


# -- 1. preflight ---------------------------------------------------------------------------
async def _preflight_source(ctx: AppContext, sid: str) -> list[dict[str, Any]]:
    row = source_row(ctx, sid)
    assert row is not None
    checks: list[dict[str, Any]] = []
    if row["kind"] == "backup":
        b = latest_backup(ctx, sid)
        checks.append(_check("backup_verified", "ok" if b else "fail",
                             "Geüploade back-up gecontroleerd (zip, database, integrity_check)."
                             if b else "Geen geldige back-up voor deze bron."))
        checks.append(_check("live_checks", "skipped", "Back-upbron zonder live verbinding: "
                             "API-, wachtrij- en freeze-controles worden overgeslagen."))
        return checks
    client = source_client(ctx, sid)
    try:
        status = await client.system_status()
    except ArrError as exc:
        return [_check("api_reachable", "fail", f"Oude instantie niet bereikbaar: {exc.message}")]
    checks.append(_check("api_reachable", "ok", f"{status.get('appName')} {status.get('version')}"
                         " bereikbaar.", version=status.get("version"),
                         database_type=status.get("databaseType")))
    if str(status.get("databaseType", "sqLite")).lower().startswith("postgres"):
        checks.append(_check("native_backup_possible", "warn", "PostgreSQL-installatie: native "
                             "back-up bevat geen database; API-import wordt gebruikt."))
    health = await client.health()
    errors = [h.get("message") for h in health if str(h.get("type")).lower() == "error"]
    checks.append(_check("health", "warn" if errors else "ok",
                         "; ".join(map(str, errors[:3])) or "Geen health-fouten."))
    try:
        disks = await client.get("diskspace") or []
        low = [d for d in disks if d.get("totalSpace") and
               d.get("freeSpace", 0) / d["totalSpace"] < 0.05]
        checks.append(_check("diskspace", "warn" if low else "ok",
                             f"{len(low)} schijf/schijven met <5% vrij." if low else
                             "Voldoende vrije ruimte op de bron.", disks=disks))
    except ArrError as exc:
        checks.append(_check("diskspace", "warn", f"Diskspace niet opvraagbaar: {exc.message}"))
    roots = await client.root_folders()
    bad = [r["path"] for r in roots if not r.get("accessible", True)]
    checks.append(_check("root_folders", "warn" if bad else "ok",
                         f"Niet toegankelijk op de bron: {', '.join(bad)}" if bad else
                         f"{len(roots)} rootfolder(s) toegankelijk.",
                         paths=[r["path"] for r in roots]))
    dcs = await client.download_clients()
    idx = await client.indexers()
    checks.append(_check("download_clients", "ok" if dcs else "warn",
                         f"{len(dcs)} downloadclient(s), {sum(bool(d.get('enable')) for d in dcs)}"
                         " actief."))
    checks.append(_check("indexers", "ok" if idx else "warn",
                         f"{len(idx)} indexer(s), {sum(bool(i.get('enableRss')) for i in idx)} "
                         "met RSS."))
    queue = await client.get("queue", page=1, pageSize=1)
    active = int((queue or {}).get("totalRecords") or 0)
    checks.append(_check("active_downloads", "warn" if active else "ok",
                         f"{active} actieve download(s) in de wachtrij; die worden door de oude "
                         "instantie afgerond (downloadclients blijven aan tot cutover)."
                         if active else "Geen actieve downloads.", count=active))
    backups = await client.backups()
    checks.append(_check("backups_available", "ok" if backups else "warn",
                         f"{len(backups)} bestaande back-up(s) op de bron." if backups else
                         "Geen bestaande back-ups; maak er één via de back-upstap (opt-in)."))
    return checks


async def _preflight_target(ctx: AppContext, m: dict[str, Any]) -> list[dict[str, Any]]:
    target = m["target_instance_id"]
    srow = source_row(ctx, m["source_id"])
    assert srow is not None
    inst = get_instance(ctx, target)
    if inst is None:
        return [_check("target_exists", "fail", f"Doelinstantie '{target}' bestaat niet.")]
    checks = []
    if inst.app != srow["app"]:
        checks.append(_check("app_match", "fail", f"Bron is {srow['app']}, doel is {inst.app}: "
                             "Sonarr en Radarr kunnen niet gemengd worden."))
    try:
        client = arr_client(ctx, target)
        status = await client.system_status()
    except ArrError as exc:
        return checks + [_check("target_reachable", "fail",
                                f"Doel-fork niet bereikbaar: {exc.message}")]
    tv, sv = _version_tuple(status.get("version")), _version_tuple(srow["version"])
    compatible = inst.app == srow["app"] and (not sv or tv >= sv)
    checks.append(_check("restore_compatible", "ok" if compatible else "warn",
                         f"Doelversie {status.get('version')} ≥ bronversie {srow['version']}: "
                         "native restore mogelijk." if compatible else
                         f"Doelversie {status.get('version')} is ouder dan bron "
                         f"{srow['version']}: native restore niet mogelijk, API-import nodig.",
                         target_version=status.get("version")))
    items = await (client.series_list() if inst.app == "sonarr" else client.movie_list())
    checks.append(_check("target_empty", "warn" if items else "ok",
                         f"Doel bevat al {len(items)} item(s); een native restore vervangt de "
                         "doeldatabase volledig." if items else "Doel is leeg."))
    try:
        disks = await client.get("diskspace") or []
        checks.append(_check("target_diskspace", "ok", "Diskspace van het doel opgehaald.",
                             disks=disks))
    except ArrError:
        pass
    rules = normalize_rules(m["path_map"])
    roots = []
    if srow["kind"] == "live":
        roots = [r["path"] for r in await source_client(ctx, srow["id"]).root_folders()]
    else:
        b = latest_backup(ctx, srow["id"])
        if b:
            with extracted(Path(b["path"]), max_backup_bytes(ctx) * 4) as ex:
                roots = from_db(ex["db_path"], srow["app"])["root_folders"]
    for root in roots:
        mapped = str(remap(root, rules))
        ok = await target_path_exists(client, mapped)
        checks.append(_check("root_access_from_target", "ok" if ok else "warn",
                             f"Doel ziet {mapped}." if ok else f"Doel kan {mapped} niet vinden "
                             "(controleer mounts of padregels).", path=mapped, source_path=root))
    return checks


async def target_path_exists(client: ArrClient, path: str) -> bool | None:
    """GET /api/v3/filesystem (de mapbrowser van Sonarr/Radarr) — alleen-lezen.

    Gemeten tegen Sonarr 4.0.20 / Radarr 6.4: met ``allowFoldersWithoutTrailingSlashes=true``
    en het pad ZONDER slot-slash geeft een bestaande map ``parent`` + inhoud terug (ook als
    hij leeg is); een niet-bestaande map geeft de root-lijst ZONDER ``parent``. Mét slot-slash
    krijgt een niet-bestaand pad wél ``parent: "/"`` (vals positief) — dus nooit zo vragen."""
    clean = path.rstrip("/")
    if not clean:
        return True
    try:
        data = await client.request("GET", "filesystem", params={
            "path": clean, "allowFoldersWithoutTrailingSlashes": "true"})
    except ArrError:
        return None
    return isinstance(data, dict) and data.get("parent") is not None


async def run_preflight(ctx: AppContext, run_id: int, scope: str, params: dict[str, Any],
                        actor: str, log: Log) -> tuple[str, dict[str, Any]]:
    out: dict[str, Any] = {"sources": {}, "targets": {}, "seerr": None, "local": None}
    all_checks: list[dict[str, Any]] = []
    for m in store.mappings(ctx, run_id):
        if m["status"] == "excluded":
            continue
        log(f"Preflight bron {m['source_id']}")
        c = await _preflight_source(ctx, m["source_id"])
        out["sources"][m["source_id"]] = c
        all_checks += c
        if m["target_instance_id"]:
            log(f"Preflight doel {m['target_instance_id']}")
            t = await _preflight_target(ctx, m)
            out["targets"][m["target_instance_id"]] = t
            all_checks += t
    root = ctx.settings.backup_dir
    root.mkdir(parents=True, exist_ok=True)
    usage = shutil.disk_usage(root)
    need = sum(int(b["size"]) for b in ctx.db.fetch_dicts(
        "SELECT size FROM source_backups WHERE id IN (SELECT MAX(id) FROM source_backups "
        "GROUP BY source_id)")) * 3
    local = _check("local_backup_space", "ok" if usage.free > max(need, 200 * 1024 * 1024)
                   else "warn", f"{usage.free // (1024 ** 2)} MiB vrij voor back-ups en "
                   "schaduwkopieën.", free=usage.free)
    out["local"] = local
    all_checks.append(local)
    out["seerr"] = await _preflight_seerr(ctx, run_id)
    if out["seerr"]:
        all_checks.append(out["seerr"])
    v = _verdict_of(all_checks)
    store.add_report(ctx, run_id, "preflight", v, summary=out, actor=actor)
    out["verdict"] = v
    return ("failed" if v == "no_go" else "warning" if v == "warn" else "succeeded"), out


async def _preflight_seerr(ctx: AppContext, run_id: int) -> dict[str, Any] | None:
    from mediaguard.seerr.client import SeerrError, seerr_client
    from mediaguard.seerr.service import _norm_url, server_url

    try:
        client = seerr_client(ctx)
    except SeerrError:
        return _check("seerr", "skipped", "Seerr is (nog) niet ingesteld.")
    try:
        status = await client.status()
        servers = {k: await client.servers(k) for k in ("sonarr", "radarr")}
    except SeerrError as exc:
        return _check("seerr", "warn", f"Seerr niet bereikbaar: {exc.message}")
    linked = {}
    for m in store.mappings(ctx, run_id):
        row = source_row(ctx, m["source_id"])
        if row and row["url"]:
            hit = [s for s in servers.get(row["app"], []) if server_url(s) == _norm_url(row["url"])]
            linked[m["source_id"]] = [{"id": s["id"], "name": s.get("name")} for s in hit]
    return _check("seerr", "ok", f"Seerr {status.get('version')} bereikbaar; servers die naar "
                  "de oude instanties wijzen zijn herkend (pas aangepast bij cutover).",
                  servers_by_source=linked)


# -- 2. backup ------------------------------------------------------------------------------
async def run_backup(ctx: AppContext, run_id: int, scope: str, params: dict[str, Any],
                     actor: str, log: Log) -> tuple[str, dict[str, Any]]:
    sid = scope.split(":", 1)[1]
    row = source_row(ctx, sid)
    if row is None:
        raise StepBlocked(f"Onbekende bron {sid}")
    if row["kind"] == "backup":
        b = latest_backup(ctx, sid)
        if b is None:
            raise StepBlocked("Geüploade back-up ontbreekt of is ongeldig")
        return "succeeded", {"backup_id": b["id"], "sha256": b["sha256"], "origin": "upload"}
    strategy = params.get("strategy", "existing")
    cp_scope = f"source:{sid}"
    client = source_client(ctx, sid)
    if strategy == "create":
        if not params.get("confirm_create"):
            raise StepBlocked("Het Backup-commando op de oude instantie vereist expliciete "
                              "bevestiging (confirm_create).")
        cp = store.checkpoint_get(ctx, run_id, cp_scope, "backup", "command", "id")
        if cp is None:
            log("Officieel Backup-commando op de oude instantie (opt-in, niet-destructief)")
            cmd = await source_client(ctx, sid, grant="backup").post("command", {"name": "Backup"})
            store.checkpoint_set(ctx, run_id, cp_scope, "backup", "command", "id", cmd["id"],
                                 {"at": utcnow()})
            cmd_id = int(cmd["id"])
        else:
            cmd_id = int(cp["target_ref"])
        wait = int(ctx.config.value("migration.command_wait_seconds"))
        deadline = asyncio.get_running_loop().time() + wait
        while True:
            st = await client.command_status(cmd_id)
            if st.get("status") in ("completed", "failed", "aborted", "cancelled"):
                break
            if asyncio.get_running_loop().time() >= deadline:
                from mediaguard.jobs import RetryLater

                raise RetryLater("Backup-commando nog niet klaar", 30)
            await asyncio.sleep(2)
        if st.get("status") != "completed":
            raise RuntimeError(f"Backup-commando eindigde met status {st.get('status')}")
    backups = sorted(await client.backups(), key=lambda b: str(b.get("time", "")), reverse=True)
    if strategy == "create":
        backups = [b for b in backups if b.get("type") == "manual"] or backups
    if not backups:
        raise StepBlocked("Geen back-up beschikbaar op de bron")
    chosen = backups[0]
    existing = ctx.db.fetch_one("SELECT * FROM source_backups WHERE source_id=? AND "
                                "backup_name LIKE ? ORDER BY id DESC LIMIT 1",
                                (sid, f"%{Path(str(chosen['name'])).name}"))
    if existing and Path(existing["path"]).exists() and \
            sha256_file(Path(existing["path"])) == existing["sha256"]:
        log("Back-up was al gedownload en gecontroleerd (hervat)")
        return "succeeded", {"backup_id": existing["id"], "sha256": existing["sha256"],
                             "reused": True}
    log(f"Download {chosen['name']}")
    data = await client.download(str(chosen["path"]), max_backup_bytes(ctx))
    incoming = backups_root(ctx) / "_incoming"
    incoming.mkdir(parents=True, exist_ok=True)
    tmp = incoming / f"{sid}-{os.getpid()}-{run_id}.zip"
    tmp.write_bytes(data)
    try:
        b = _store_backup(ctx, sid, tmp, str(chosen["name"]),
                          "created" if strategy == "create" else "existing", actor)
    finally:
        tmp.unlink(missing_ok=True)
    log(f"Back-up gecontroleerd: sha256 {b['sha256'][:12]}…, integrity_check ok")
    return "succeeded", {"backup_id": b["id"], "sha256": b["sha256"],
                         "counts": b["verify"].get("counts")}


# -- 3. mapping -------------------------------------------------------------------------------
async def mapping_preview(ctx: AppContext, run_id: int, actor: str,
                          reset: bool = False) -> dict[str, Any]:
    maps = store.mappings(ctx, run_id)
    snaps: dict[str, dict[str, Any]] = {}
    proposals = []
    for m in maps:
        row = source_row(ctx, m["source_id"])
        assert row is not None
        snap = await _source_snapshot(ctx, row["id"], episodes=False)
        snaps[row["id"]] = snap
        signals = anime_signals(snap, [row["name"], row["instance_name"]])
        candidates = [i for i in list_instances(ctx) if i.app == row["app"]]
        want_anime = signals["score"] >= 0.5
        best = next((i for i in candidates if (i.media_type in ("anime", "anime_movie"))
                     == want_anime), None)
        confidence = round(abs(signals["score"] - 0.5) * 2, 2)
        reasons = [f"App: {row['app']} (alleen {row['app']}-doelen mogelijk)",
                   *signals["reasons"],
                   "Koppeling op type/profiel/rootfolder, nooit op titel."]
        warnings = []
        if confidence < 0.3:
            warnings.append("Twijfelachtige anime-herkenning: kies het doel handmatig.")
        keep = m["status"] == "confirmed" and not reset
        target = m["target_instance_id"] if keep else (best.id if best else None)
        if not keep:
            ctx.db.execute(
                "UPDATE migration_mappings SET target_instance_id=?, status='proposed', "
                "confidence=?, anime_score=?, reasons_json=?, warnings_json=?, updated_at=? "
                "WHERE run_id=? AND source_id=?",
                (target, confidence, signals["score"], dumps(reasons), dumps(warnings), utcnow(),
                 run_id, row["id"]))
        proposals.append({"source_id": row["id"], "target_instance_id": target,
                          "confidence": confidence, "anime_score": signals["score"]})
    _flag_items(ctx, run_id, snaps)
    status = store.run_status(ctx, run_id) or {}
    return {"mappings": status.get("mappings"), "items": status.get("mapping_items"),
            "matrix": _matrix(ctx, run_id)}


def _matrix(ctx: AppContext, run_id: int) -> list[dict[str, Any]]:
    maps = store.mappings(ctx, run_id)
    out = []
    for m in maps:
        row = source_row(ctx, m["source_id"]) or {}
        dup = [x["source_id"] for x in maps if x is not m and x["target_instance_id"] and
               x["target_instance_id"] == m["target_instance_id"] and x["status"] != "excluded"]
        out.append({"source_id": m["source_id"], "source_name": row.get("name"),
                    "source_app": row.get("app"), "source_kind": row.get("kind"),
                    "target_instance_id": m["target_instance_id"], "status": m["status"],
                    "confidence": m["confidence"], "duplicate_target_with": dup,
                    "warnings": m["warnings"] + ([f"Ook {', '.join(dup)} wijst naar dit doel: "
                                                  "bestaande scheiding wordt nooit automatisch "
                                                  "samengevoegd."] if dup else [])})
    return out


def _flag_items(ctx: AppContext, run_id: int, snaps: dict[str, dict[str, Any]]) -> None:
    maps = {m["source_id"]: m for m in store.mappings(ctx, run_id)}
    ctx.db.execute("DELETE FROM migration_mapping_items WHERE run_id=?", (run_id,))
    seen: dict[tuple[str, str], list[str]] = {}
    for sid, snap in snaps.items():
        target = maps[sid]["target_instance_id"]
        inst = get_instance(ctx, target) if target else None
        coll = "series" if snap["app"] == "sonarr" else "movies"
        kind = "series" if coll == "series" else "movie"
        for ext, item in snap[coll].items():
            seen.setdefault((kind, ext), []).append(sid)
            if kind == "series" and inst is not None:
                anime_item = item.get("seriesType") == "anime"
                anime_target = inst.media_type == "anime"
                if anime_item != anime_target:
                    flag = "anime_in_standard" if anime_item else "standard_in_anime"
                    msg = (f"'{item['title']}' heeft seriestype {item.get('seriesType')} maar "
                           f"gaat naar {inst.id}; wordt NIET automatisch verplaatst.")
                    _insert_flag(ctx, run_id, sid, kind, ext, item["title"], target, flag, msg)
    for (kind, ext), sids in seen.items():
        if len(sids) < 2:
            continue
        targets = [maps[s]["target_instance_id"] for s in sids]
        same = len(set(targets)) < len(targets)
        for sid in sids:
            coll = "series" if kind == "series" else "movies"
            title = snaps[sid][coll][ext]["title"]
            _insert_flag(ctx, run_id, sid, kind, ext, title, maps[sid]["target_instance_id"],
                         "duplicate_same_target" if same else "duplicate_cross_scope",
                         (f"'{title}' komt in meerdere bronnen voor die naar hetzelfde doel "
                          "gaan: dubbele import wordt geblokkeerd." if same else
                          f"'{title}' staat in meerdere bronnen ({', '.join(sids)}) met "
                          "verschillende doelen; toegestaan als bewuste keuze."))


def _insert_flag(ctx: AppContext, run_id: int, sid: str, kind: str, ext: str, title: str,
                 target: str | None, flag: str, msg: str) -> None:
    ctx.db.execute("INSERT OR IGNORE INTO migration_mapping_items(run_id,source_id,kind,"
                   "external_id,title,target_instance_id,flag,message_nl) VALUES "
                   "(?,?,?,?,?,?,?,?)", (run_id, sid, kind, ext, title, target, flag, msg))


def update_mapping(ctx: AppContext, run_id: int, entries: list[dict[str, Any]],
                   actor: str) -> None:
    for e in entries:
        m = next((x for x in store.mappings(ctx, run_id) if x["source_id"] == e["source_id"]),
                 None)
        if m is None:
            raise ValueError(f"Bron {e['source_id']} hoort niet bij run {run_id}")
        row = source_row(ctx, e["source_id"])
        target = e.get("target_instance_id", m["target_instance_id"])
        if target:
            inst = instance_row(ctx, target)
            if inst is None:
                raise ValueError(f"Doel {target} bestaat niet")
            if row and inst["app"] != row["app"]:
                raise ValueError(f"{e['source_id']} ({row['app']}) kan niet naar {target} "
                                 f"({inst['app']})")
        rules = e.get("path_map", m["path_map"])
        normalize_rules(rules)
        mode = e.get("mode", m["mode"])
        if mode == "api" and row and row["kind"] != "live":
            raise ValueError("API-import vereist een live bron; gebruik native restore")
        status = "excluded" if e.get("excluded") else "proposed"
        ctx.db.execute("UPDATE migration_mappings SET target_instance_id=?, mode=?, "
                       "path_map_json=?, status=?, confirmed_by=NULL, confirmed_at=NULL, "
                       "updated_at=? WHERE run_id=? AND source_id=?",
                       (target, mode, dumps(rules), status, utcnow(), run_id, e["source_id"]))


def confirm_mapping(ctx: AppContext, run_id: int, actor: str) -> dict[str, Any]:
    maps = [m for m in store.mappings(ctx, run_id) if m["status"] != "excluded"]
    problems = [f"{m['source_id']}: geen doel gekozen" for m in maps
                if not m["target_instance_id"]]
    targets = [m["target_instance_id"] for m in maps]
    problems += [f"Doel {t} is aan meerdere bronnen gekoppeld" for t in set(targets)
                 if t and targets.count(t) > 1]
    if problems:
        raise StepBlocked("; ".join(problems))
    now = utcnow()
    ctx.db.execute("UPDATE migration_mappings SET status='confirmed', confirmed_by=?, "
                   "confirmed_at=?, updated_at=? WHERE run_id=? AND status='proposed'",
                   (actor, now, now, run_id))
    store.step_set(ctx, run_id, "mapping", "", "succeeded",
                   result={"confirmed": [m["source_id"] for m in maps]})
    return {"confirmed": len(maps)}


# -- padpreview (upload-bronnen + native restore) ------------------------------------------
async def path_preview(ctx: AppContext, run_id: int, source_id: str,
                       actor: str) -> dict[str, Any]:
    m = next(x for x in store.mappings(ctx, run_id) if x["source_id"] == source_id)
    rules = normalize_rules(m["path_map"])
    snap = await _source_snapshot(ctx, source_id, episodes=True)
    sample_n = int(ctx.config.value("migration.path_sample_size"))
    per_prefix: dict[str, dict[str, Any]] = {}
    unresolved: list[dict[str, Any]] = []

    def account(path: str, kind: str, must_exist: bool) -> dict[str, Any]:
        new = str(remap(path, rules))
        prefix = rule_for(path, rules) or "(geen regel)"
        bucket = per_prefix.setdefault(prefix, {"count": 0, "samples": [], "missing": 0})
        bucket["count"] += 1
        exists = os.path.isdir(new) if must_exist else None
        if must_exist and len(bucket["samples"]) < 5:
            bucket["samples"].append({"from": path, "to": new, "exists": exists})
        if must_exist and not exists:
            bucket["missing"] += 1
            unresolved.append({"kind": kind, "from": path, "to": new})
        return {"from": path, "to": new, "exists": exists}

    roots = [account(r, "root_folder", True) for r in snap["root_folders"]]
    coll = "series" if snap["app"] == "sonarr" else "movies"
    with_files = sorted(k for k, v in snap[coll].items()
                        if (v.get("with_file") if coll == "series" else v.get("hasFile")))
    sample_keys = set(with_files[:sample_n])
    sampled = [account(snap[coll][k]["path"], coll, True) for k in sorted(sample_keys)]
    for k, v in snap[coll].items():
        if k not in sample_keys:
            prefix = rule_for(v["path"], rules) or "(geen regel)"
            per_prefix.setdefault(prefix, {"count": 0, "samples": [], "missing": 0})["count"] += 1
    v = "no_go" if unresolved else "go"
    summary = {"source_id": source_id, "rules": [{"from": a, "to": b} for a, b in rules],
               "per_prefix": per_prefix, "root_folders": roots, "sampled": len(sampled),
               "unresolved": unresolved[:100], "unresolved_count": len(unresolved),
               "blocking": bool(unresolved),
               "message_nl": "Alle rootfolders en steekproefpaden bestaan op de read-only mount."
               if not unresolved else f"{len(unresolved)} pad(en) niet gevonden: pas de "
               "padregels aan of bevestig expliciet dat je doorgaat."}
    store.add_report(ctx, run_id, "path_preview", v, source_id=source_id,
                     target=m["target_instance_id"], summary=summary, actor=actor)
    return {"verdict": v, **summary}


# -- 4. schaduwimport -----------------------------------------------------------------------
def resolve_mode(ctx: AppContext, m: dict[str, Any]) -> tuple[str, str]:
    row = source_row(ctx, m["source_id"])
    assert row is not None
    if m["mode"] in ("native", "api"):
        return m["mode"], "door gebruiker gekozen"
    if row["kind"] == "backup":
        return "native", "back-upbron: alleen native restore mogelijk"
    b = latest_backup(ctx, row["id"])
    if b is None:
        return "api", "geen gecontroleerde back-up: API-import"
    if str(row.get("database_type") or "").lower().startswith("postgres"):
        return "api", "PostgreSQL-bron: API-import"
    pre = store.latest_report(ctx, m["run_id"], "preflight")
    for c in (pre or {}).get("summary", {}).get("targets", {}).get(
            m["target_instance_id"], []):
        if c["check"] == "restore_compatible" and c["status"] != "ok":
            return "api", "versie niet compatibel voor restore: API-import"
    return "native", "gecontroleerde back-up en compatibele versie: native restore"


def _require_ready_for_import(ctx: AppContext, run_id: int, target: str) -> dict[str, Any]:
    m = store.mapping_for_target(ctx, run_id, target)
    pre = store.latest_report(ctx, run_id, "preflight")
    if pre is None:
        raise StepBlocked("Voer eerst de preflight uit")
    for c in pre["summary"].get("targets", {}).get(target, []):
        if c["status"] == "fail":
            raise StepBlocked(f"Preflight blokkeert dit doel: {c['message_nl']}")
    return m


async def restore_into_target(ctx: AppContext, target: str, zip_path: Path) -> None:
    inst = get_instance(ctx, target)
    assert inst is not None
    transport = (ctx.services.get("arr_transports") or {}).get(target)
    key = ctx.secrets.get(target_key_secret(target)) or ""
    async with httpx.AsyncClient(base_url=inst.url, transport=transport, timeout=600,
                                 headers={"X-Api-Key": key}) as c:
        with zip_path.open("rb") as fh:
            resp = await c.post("/api/v3/system/backup/restore/upload",
                                files={"restore": (zip_path.name, fh, "application/zip")})
        if resp.status_code >= 400:
            raise ArrError(f"Restore op {target} geweigerd: HTTP {resp.status_code} "
                           f"{resp.text[:200]}", status=resp.status_code, instance_id=target)
        # de fork herstart en verbreekt de verbinding: verwacht
        with contextlib.suppress(httpx.TransportError):
            await c.post("/api/v3/system/restart")


ITEM_SCOPES = ("series", "season", "episode", "movie", "file")


def drop_item_scoped_settings(ctx: AppContext, target: str, actor: str) -> list[dict]:
    """Een native restore vervangt de database van het doel: serie-/film-/aflevering-ID's
    krijgen de waarden uit de bron. MediaGuard-overrides op die scopes (sleutel
    ``<instance>:<id>``) zouden dan op een ándere titel gaan werken (gezien in de
    stacktest: een 'Nederlandse audio verplicht'-profiel bleef aan serie-ID 1 hangen).
    Ze worden verwijderd en met oude waarde in de audit vastgelegd."""
    marks = ",".join("?" for _ in ITEM_SCOPES)
    rows = ctx.db.fetch_dicts(
        f"SELECT scope_type, scope_id, key, value_json FROM settings_values WHERE "
        f"scope_type IN ({marks}) AND scope_id LIKE ?", (*ITEM_SCOPES, f"{target}:%"))
    if not rows:
        return []
    ctx.db.execute(f"DELETE FROM settings_values WHERE scope_type IN ({marks}) AND "
                   "scope_id LIKE ?", (*ITEM_SCOPES, f"{target}:%"))
    ctx.config.invalidate()
    from mediaguard.audit import audit

    audit(ctx.db, actor=actor, action="migration.item_settings_dropped",
          target_type="instance", target_id=target, instance_id=target,
          old_value=rows, reason="Native restore vervangt alle item-ID's van het doel")
    return [{k: r[k] for k in ("scope_type", "scope_id", "key")} for r in rows]


async def wait_for_target(ctx: AppContext, target: str, wait: int) -> bool:
    deadline = asyncio.get_running_loop().time() + wait
    while True:
        try:
            await arr_client(ctx, target, timeout=10).system_status()
            return True
        except ArrError:
            if asyncio.get_running_loop().time() >= deadline:
                return False
            await asyncio.sleep(3)


async def enforce_shadow(client: ArrClient) -> list[str]:
    """Zet RSS/automatisch zoeken en downloadclients op het doel uit (veiligheid)."""
    fixed = []
    for idx in await client.indexers():
        if idx.get("enableRss") or idx.get("enableAutomaticSearch"):
            await client.provider_update("indexer", {**idx, "enableRss": False,
                                                     "enableAutomaticSearch": False},
                                         force_save=True)
            fixed.append(f"indexer:{idx['name']}")
    for dc in await client.download_clients():
        if dc.get("enable"):
            await client.provider_update("downloadclient", {**dc, "enable": False},
                                         force_save=True)
            fixed.append(f"downloadclient:{dc['name']}")
    return fixed


async def _wait_command(ctx: AppContext, client: ArrClient, cmd: dict[str, Any]) -> str:
    wait = int(ctx.config.value("migration.command_wait_seconds"))
    deadline = asyncio.get_running_loop().time() + wait
    while True:
        st = await client.command_status(int(cmd["id"]))
        if st.get("status") in ("completed", "failed", "aborted", "cancelled"):
            return str(st.get("status"))
        if asyncio.get_running_loop().time() >= deadline:
            return "running"
        await asyncio.sleep(2)


async def link_report(ctx: AppContext, run_id: int, target: str) -> dict[str, Any]:
    """Na restore + rescan: hoeveel verwachte bestanden zijn gekoppeld vs ontbrekend."""
    m = store.mapping_for_target(ctx, run_id, target)
    src = await _source_snapshot(ctx, m["source_id"], episodes=True)
    tgt = await from_api(arr_client(ctx, target), episodes=True)
    if src["app"] == "sonarr":
        expected = sum(int(s.get("with_file") or 0) for s in src["series"].values())
        linked = sum(int(s.get("with_file") or 0) for s in tgt["series"].values())
    else:
        expected = sum(1 for s in src["movies"].values() if s.get("hasFile"))
        linked = sum(1 for s in tgt["movies"].values() if s.get("hasFile"))
    return {"expected_with_file": expected, "linked": linked,
            "missing": max(0, expected - linked)}


async def run_shadow_import(ctx: AppContext, run_id: int, scope: str, params: dict[str, Any],
                            actor: str, log: Log) -> tuple[str, dict[str, Any]]:
    target = scope
    if ctx.emergency_stopped():
        raise StepBlocked("Noodstop actief")
    m = _require_ready_for_import(ctx, run_id, target)
    row = source_row(ctx, m["source_id"])
    assert row is not None
    mode, why = resolve_mode(ctx, {**m, "mode": params.get("mode") or m["mode"]})
    log(f"Schaduwimport {m['source_id']} → {target}: {mode} ({why})")
    # schaduwmodus eerst vastleggen (crash-veilig): doel mag nooit grabben
    update_instance(ctx, target, {"shadow_mode": True})
    rules = normalize_rules(m["path_map"])
    client = arr_client(ctx, target)
    if mode == "native":
        return await _native(ctx, run_id, target, m, row, rules, client, params, actor, log, why)
    if row["kind"] != "live":
        raise StepBlocked("API-import vereist een live bron")
    src = source_client(ctx, row["id"])
    imp = Importer(ctx, run_id, target, src, client, rules, log, store.run_options(ctx, run_id))
    cfg = await imp.run_config()
    store.save_shadow_state(ctx, run_id, target, row["id"], "api", cfg["state"])
    items = await imp.run_items()
    fixed = await enforce_shadow(client)
    result = {"mode": "api", "mode_reason": why, "config": cfg["counts"],
              "credentials_needed": cfg["credentials_needed"], **items,
              "shadow_enforced": fixed}
    if items.get("pending_tvdb"):
        result["message_nl"] = ("Nog niet alle afleveringen zijn door het doel opgehaald; "
                                "voer deze stap opnieuw uit (hervat zonder dubbel werk).")
        return "warning", result
    return ("warning" if cfg["credentials_needed"] else "succeeded"), result


async def _native(ctx: AppContext, run_id: int, target: str, m: dict[str, Any],
                  row: dict[str, Any], rules: list[tuple[str, str]], client: ArrClient,
                  params: dict[str, Any], actor: str, log: Log,
                  why: str) -> tuple[str, dict[str, Any]]:
    backup = latest_backup(ctx, row["id"])
    if backup is None:
        raise StepBlocked("Geen gecontroleerde back-up voor deze bron (stap 2)")
    pp = store.latest_report(ctx, run_id, "path_preview", target)
    accept = params.get("accept_unresolved_paths") or store.run_options(ctx, run_id).get(
        "accept_unresolved_paths")
    if rules and (pp is None or pp["summary"].get("rules") !=
                  [{"from": a, "to": b} for a, b in rules]):
        raise StepBlocked("Voer eerst de padpreview uit voor de huidige padregels")
    if pp is not None and pp["verdict"] == "no_go" and not accept:
        raise StepBlocked(f"{pp['summary'].get('unresolved_count')} pad(en) bestaan niet op de "
                          "mount; pas padregels aan of bevestig expliciet "
                          "(accept_unresolved_paths).")
    cp = lambda key: store.checkpoint_get(ctx, run_id, target, "native", "phase", key)  # noqa: E731
    result: dict[str, Any] = {"mode": "native", "mode_reason": why, "backup_id": backup["id"]}
    built = cp("built")
    shadow_zip = ctx.settings.backup_dir / "shadow" / f"run{run_id}" / f"{target}.zip"
    if built is None or built["data"].get("backup_sha") != backup["sha256"] or \
            not shadow_zip.exists():
        key = ctx.secrets.get(target_key_secret(target))
        inst = get_instance(ctx, target)
        assert inst is not None
        if not key:
            raise StepBlocked(f"Geen API-sleutel voor doel {target}")
        log("Schaduwkopie bouwen (kopie van de back-up; RSS/auto-zoeken/downloadclients uit)")
        state = build_shadow_zip(Path(backup["path"]), shadow_zip, rules=rules,
                                 target_api_key=key, target_url=inst.url,
                                 max_uncompressed=max_backup_bytes(ctx) * 4)
        store.save_shadow_state(ctx, run_id, target, row["id"], "native",
                                {k: v for k, v in state.items()
                                 if k in ("indexers", "download_clients", "import_lists",
                                          "config")})
        store.checkpoint_set(ctx, run_id, target, "native", "phase", "built", str(shadow_zip),
                             {"backup_sha": backup["sha256"], "shadow_sha": state["shadow_sha256"],
                              "remapped": state["remapped"]})
        for later in ("restored", "rescanned"):
            ctx.db.execute("DELETE FROM instance_migration_checkpoints WHERE run_id=? AND "
                           "instance_id=? AND phase='native' AND source_key=?",
                           (run_id, target, later))
        built = cp("built")
    assert built is not None
    result["shadow_sha256"] = built["data"].get("shadow_sha")
    result["remapped"] = built["data"].get("remapped")
    if cp("restored") is None:
        log(f"Restore naar {target} via /api/v3/system/backup/restore/upload")
        await restore_into_target(ctx, target, shadow_zip)
        dropped = drop_item_scoped_settings(ctx, target, actor)
        if dropped:
            log(f"{len(dropped)} MediaGuard-instelling(en) op serie/film/aflevering-niveau van "
                f"{target} vervallen: de restore vervangt alle item-ID's")
        store.checkpoint_set(ctx, run_id, target, "native", "phase", "restored", None,
                             {"at": utcnow(), "dropped_item_settings": dropped})
    if not await wait_for_target(ctx, target, int(ctx.config.value(
            "migration.restart_wait_seconds"))):
        from mediaguard.jobs import RetryLater

        raise RetryLater(f"{target} is na restore nog niet terug; later opnieuw", 30)
    result["shadow_enforced"] = await enforce_shadow(client)
    if result["shadow_enforced"]:
        log(f"WAARSCHUWING: na restore stond nog iets aan, uitgezet: {result['shadow_enforced']}")
    if cp("rescanned") is None:
        name = "RescanSeries" if row["app"] == "sonarr" else "RescanMovie"
        log(f"{name} (alleen lezen van schijf) om bestanden te koppelen")
        cmd = await client.command(name)
        status = await _wait_command(ctx, client, cmd)
        store.checkpoint_set(ctx, run_id, target, "native", "phase", "rescanned", cmd.get("id"),
                             {"status": status})
    result["link"] = await link_report(ctx, run_id, target)
    log(f"Gekoppeld: {result['link']['linked']}/{result['link']['expected_with_file']}")
    return ("warning" if result["link"]["missing"] or result["shadow_enforced"]
            else "succeeded"), result


# -- 5. verificatie -------------------------------------------------------------------------
def new_item_overrides(src: dict[str, Any], options: dict[str, Any]) -> dict[str, Any]:
    if options.get("follow_new_seasons") is None:
        return {}
    return {"monitorNewItems": {k: "all" for k, s in src["series"].items()
                                if s["monitored"] and options["follow_new_seasons"]}}


async def run_verify(ctx: AppContext, run_id: int, scope: str, params: dict[str, Any],
                     actor: str, log: Log) -> tuple[str, dict[str, Any]]:
    target = scope
    m = store.mapping_for_target(ctx, run_id, target)
    imp = store.step_get(ctx, run_id, "shadow_import", target)
    if imp is None or imp["status"] not in store.DONE:
        raise StepBlocked("Schaduwimport voor dit doel is nog niet afgerond")
    row = source_row(ctx, m["source_id"])
    assert row is not None
    rules = normalize_rules(m["path_map"])
    tgt_client = arr_client(ctx, target)
    inst = instance_row(ctx, target)
    assert inst is not None
    shadow = bool(inst["shadow_mode"])
    options = store.run_options(ctx, run_id)
    delta = None
    if row["kind"] == "live" and params.get("sync_delta", True) and shadow:
        log("Delta-synchronisatie bron → schaduwdoel (alleen het doel wordt gewijzigd)")
        delta = await Importer(ctx, run_id, target, source_client(ctx, row["id"]), tgt_client,
                               rules, log, options, phase="delta").run_items()
    src = remap_snapshot(await _source_snapshot(ctx, row["id"], episodes=True), rules)
    tgt = await from_api(tgt_client, episodes=True)
    mism = compare(src, tgt, shadow=shadow, overrides=new_item_overrides(src, options))
    for kind in ("indexer", "downloadclient"):
        for cp in ctx.db.fetch_dicts(
                "SELECT * FROM instance_migration_checkpoints WHERE run_id=? AND instance_id=? "
                "AND phase='import' AND kind=?", (run_id, target, kind)):
            data = loads(cp["data_json"], {})
            if data.get("credentials_needed"):
                item = {"kind": kind, "target_id": cp["target_ref"],
                        "fields": data["credentials_needed"]}
                if await credentials_resolved(tgt_client, item):
                    data["credentials_needed"] = []
                    store.checkpoint_set(ctx, run_id, target, "import", kind, cp["source_key"],
                                         cp["target_ref"], data)
                    continue
                mism.append({"id": f"cred-{kind}-{cp['source_key']}", "category": "credentials",
                             "key": data.get("name"), "field": ",".join(data[
                                 "credentials_needed"]), "source": "***", "target": None,
                             "severity": "no_go", "acceptable": False,
                             "message_nl": f"{data.get('name')}: inloggegevens nodig (vul in bij "
                                           "Downloads & Indexers of synchroniseer via Prowlarr)."})
    from mediaguard.migration import compat

    unsupported = [loads(r["data_json"], {}) for r in ctx.db.fetch_dicts(
        "SELECT data_json FROM instance_migration_checkpoints WHERE run_id=? AND instance_id=? "
        "AND phase IN ('import','delta') AND kind=?", (run_id, target, compat.UNSUPPORTED_KIND))]
    mism, auto = compat.apply_unsupported(mism, unsupported)
    if auto:
        store.add_accepted(ctx, run_id, target, {m["id"]: m["message_nl"] for m in mism
                                                 if m["id"] in set(auto)})
    accepted = store.accepted_ids(ctx, run_id, target)
    v = verdict(mism, accepted)
    summary = {**summarize(src, tgt), "source_kind": row["kind"], "shadow": shadow,
               "delta": delta, "compared_against": "live API" if row["kind"] == "live"
               else "database in de back-up", "accepted": sorted(accepted)}
    rid = store.add_report(ctx, run_id, "verify", v, source_id=row["id"], target=target,
                           summary=summary, mismatches=mism, actor=actor)
    log(f"Vergelijking: {v} ({len(mism)} verschil(len))")
    status = {"go": "succeeded", "warn": "warning", "no_go": "blocked"}[v]
    return status, {"report_id": rid, "verdict": v, "mismatches": len(mism)}


# -- 6. read-only scan + reconciliatie ------------------------------------------------------
async def run_scan(ctx: AppContext, run_id: int, scope: str, params: dict[str, Any],
                   actor: str, log: Log) -> tuple[str, dict[str, Any]]:
    from mediaguard.library.sync import sync_instance

    target = scope
    m = store.mapping_for_target(ctx, run_id, target)
    log("Read-only bibliotheeksync van het doel (leest Sonarr/Radarr-API, wijzigt niets)")
    sync = await sync_instance(ctx, target, full=True)
    src = remap_snapshot(await _source_snapshot(ctx, m["source_id"], episodes=True),
                         normalize_rules(m["path_map"]))
    inst = get_instance(ctx, target)
    assert inst is not None
    kind = "series" if inst.app == "sonarr" else "movie"
    coll = "series" if kind == "series" else "movies"
    found = {str(r["ext"]): dict(r) for r in ctx.db.fetch_all(
        f"SELECT {'tvdb_id' if kind == 'series' else 'tmdb_id'} AS ext, provider_id, title, "
        "series_type, episode_file_count, has_file FROM lib_items WHERE instance_id=? AND "
        "kind=? AND removed_at IS NULL", (target, kind))}
    expected = set(src[coll])
    dupes = [dict(r) for r in ctx.db.fetch_all(
        f"SELECT {'tvdb_id' if kind == 'series' else 'tmdb_id'} AS ext, "
        "GROUP_CONCAT(instance_id) AS instances FROM lib_items WHERE kind=? AND removed_at IS "
        "NULL GROUP BY ext HAVING COUNT(DISTINCT instance_id) > 1", (kind,))]
    files_found = int(ctx.db.fetch_one(
        "SELECT COUNT(*) AS n FROM lib_files WHERE instance_id=?", (target,))["n"])
    if kind == "series":
        files_expected = sum(int(s.get("with_file") or 0) for s in src["series"].values())
        missing_eps = int(ctx.db.fetch_one(
            "SELECT COUNT(*) AS n FROM lib_episodes e JOIN lib_items i ON i.instance_id="
            "e.instance_id AND i.kind='series' AND i.provider_id=e.series_provider_id WHERE "
            "e.instance_id=? AND e.monitored=1 AND i.monitored=1 AND e.has_file=0 AND "
            "e.air_date_utc<=?", (target, utcnow()))["n"])
    else:
        files_expected = sum(1 for s in src["movies"].values() if s.get("hasFile"))
        missing_eps = int(ctx.db.fetch_one(
            "SELECT COUNT(*) AS n FROM lib_items WHERE instance_id=? AND kind='movie' AND "
            "monitored=1 AND has_file=0 AND removed_at IS NULL", (target,))["n"])
    if kind == "series":
        # zelfde telling als de Bibliotheek: afleveringen met bestand (lib_files kan achterlopen)
        with_file = int(ctx.db.fetch_one(
            "SELECT COUNT(*) AS n FROM lib_episodes WHERE instance_id=? AND has_file=1",
            (target,))["n"])
        files_found = max(files_found, with_file)
    else:
        files_found = max(files_found, sum(1 for r in found.values() if r.get("has_file")))
    wrong = [r["title"] for r in found.values() if kind == "series" and
             (r["series_type"] == "anime") != (inst.media_type == "anime")]
    missing_ids = sorted(expected - set(found))
    titles = {k: str((v or {}).get("title") or k) for k, v in src[coll].items()}
    files_missing = max(0, files_expected - files_found)
    missing_ratio = files_missing / files_expected if files_expected else 0.0
    summary = {"sync": {k: v for k, v in sync.items() if k != "metadata"},
               "expected_items": len(expected), "found_items": len(found),
               "missing_items": missing_ids[:200],
               # rapport 2 / N12: titels i.p.v. ruwe ID's
               "missing_titles": [titles.get(i, i) for i in missing_ids[:200]],
               "unknown_items": sorted(set(found) - expected)[:200],
               "unknown_titles": [str(found[i].get("title") or i)
                                  for i in sorted(set(found) - expected)[:200]],
               "extra_items_nl": (f"{len(set(found) - expected)} titel(s) staan wel in de nieuwe "
                                  "installatie maar niet in de oude (sindsdien toegevoegd)")
               if set(found) - expected else None,
               "duplicates_across_instances": dupes[:200], "wrong_scope": wrong[:200],
               "wrong_scope_nl": (f"{len(wrong)} serie(s) hebben een ander seriestype dan deze "
                                  "instantie (zo stonden ze ook in je oude installatie); ze "
                                  "blijven waar ze zijn") if wrong else None,
               "files_expected": files_expected, "files_found": files_found,
               "files_missing": files_missing, "files_missing_ratio": round(missing_ratio, 3),
               "monitored_missing": missing_eps, "scanned_at": utcnow()}
    if missing_ratio > 0.5:
        summary["files_missing_nl"] = (
            f"{round(missing_ratio * 100)} % van de bestanden is nog niet gekoppeld. Meestal "
            "loopt de bibliotheeksynchronisatie of de rescan nog; MediaGuard scant vóór het "
            "overschakelen opnieuw. Overschakelen kan pas als dit klopt.")
    unresolved = [i for i, t in zip(missing_ids[:200], summary["missing_titles"], strict=False)
                  if str(t) == str(i)]
    gone: list[Any] = []
    if unresolved:
        # rapport 3 / §6: 'titel met ID <tmdb-id>' → titel opzoeken via de doelinstantie
        names = await lookup_titles(ctx, target, kind, unresolved[:25])
        summary["missing_titles"] = [names.get(str(i), t) for i, t in
                                     zip(missing_ids[:200], summary["missing_titles"],
                                         strict=False)]
        if names is not None and getattr(names, "reachable", True):
            # niet meer te vinden bij TMDB/TVDB: kan in geen enkele installatie meer worden
            # toegevoegd; telt niet als 'ontbreekt in het doel' (rapport 4 §6: blijvend NO-GO)
            gone = [i for i in unresolved[:25] if str(i) not in names]
    if gone:
        tmap = dict(zip(missing_ids[:200], summary["missing_titles"], strict=False))
        missing_ids = [i for i in missing_ids if i not in gone]
        summary["missing_items"] = missing_ids[:200]
        summary["missing_titles"] = [tmap.get(i, i) for i in missing_ids[:200]]
        summary["removed_upstream"] = gone
        summary["removed_upstream_nl"] = (
            f"{len(gone)} titel(s) uit je oude installatie bestaan niet meer bij "
            f"{'TMDB' if kind == 'movie' else 'TVDB'} (ID {', '.join(str(g) for g in gone)}); "
            "die kunnen nergens meer worden toegevoegd en tellen niet mee.")
    v = "no_go" if missing_ids or missing_ratio > 0.5 else "warn" if (
        files_missing or summary["unknown_items"]) else "go"
    store.add_report(ctx, run_id, "scan", v, source_id=m["source_id"], target=target,
                     summary=summary, actor=actor)
    return {"go": "succeeded", "warn": "warning", "no_go": "blocked"}[v], summary


async def lookup_titles(ctx: AppContext, instance_id: str, kind: str,
                        ext_ids: list[Any]) -> dict[str, str]:
    """TMDB-/TVDB-ID → 'Titel (jaar)' via de lookup van Sonarr/Radarr (alleen lezen)."""
    from mediaguard.instances.registry import arr_client

    out = _Titles()
    try:
        async with arr_client(ctx, instance_id) as client:
            for ext in ext_ids:
                try:
                    if kind == "movie":
                        hit = await client.get("movie/lookup/tmdb", tmdbId=int(ext))
                    else:
                        hits = await client.get("series/lookup", term=f"tvdb:{int(ext)}") or []
                        hit = hits[0] if hits else None
                except Exception:  # noqa: BLE001 - per titel; ID blijft dan staan
                    continue
                if isinstance(hit, dict) and hit.get("title"):
                    year = hit.get("year")
                    out[str(ext)] = f"{hit['title']} ({year})" if year else str(hit["title"])
    except Exception:  # noqa: BLE001 - alleen weergave
        out.reachable = False
        return out
    return out


class _Titles(dict):  # type: ignore[type-arg]
    """dict met de vlag ``reachable``: was de instantie bereikbaar voor de lookup?"""

    reachable = True


# -- 7. optimalisatievoorstel ---------------------------------------------------------------
async def run_optimization(ctx: AppContext, run_id: int, scope: str, params: dict[str, Any],
                           actor: str, log: Log) -> tuple[str, dict[str, Any]]:
    try:
        from mediaguard.optimization.planner import dry_run
    except ImportError:
        return "skipped", {"message_nl": "Optimalisatiemodule niet beschikbaar; overgeslagen."}
    targets = [m["target_instance_id"] for m in store.mappings(ctx, run_id, confirmed_only=True)]
    plan = dry_run(ctx, {"instance_ids": targets}, actor, limit=params.get("limit"))
    return "succeeded", {"plan_id": plan.get("id"), "summary": plan.get("summary"),
                         "item_count": plan.get("item_count")}


# -- 8. cutover-gereedheid ------------------------------------------------------------------
SCAN_MAX_AGE_HOURS = 6


def _age_hours(ts: Any) -> float:
    from datetime import UTC, datetime

    try:
        at = datetime.fromisoformat(str(ts))
    except (TypeError, ValueError):
        return 1e9
    if at.tzinfo is None:
        at = at.replace(tzinfo=UTC)
    return (datetime.now(UTC) - at).total_seconds() / 3600


async def run_readiness(ctx: AppContext, run_id: int, scope: str, params: dict[str, Any],
                        actor: str, log: Log) -> tuple[str, dict[str, Any]]:
    from mediaguard.instances.service import check_instance, register_webhook

    target = scope
    m = store.mapping_for_target(ctx, run_id, target)
    row = source_row(ctx, m["source_id"])
    assert row is not None
    checks: list[dict[str, Any]] = []
    # rapport 2 / N12: geen oordeel op een verouderde scan (was 'gisteren' naast '3 uur
    # geleden'): ouder dan 6 uur → eerst opnieuw scannen (alleen lezen)
    scan = store.latest_report(ctx, run_id, "scan", target)
    if scan is None or _age_hours(scan.get("created_at")) > SCAN_MAX_AGE_HOURS:
        log("Scan is verouderd: eerst opnieuw scannen")
        await run_scan(ctx, run_id, target, {}, actor, log)
        scan = store.latest_report(ctx, run_id, "scan", target)
    if scan is not None:
        sv = {"go": "ok", "warn": "warn", "no_go": "fail"}.get(str(scan["verdict"]), "warn")
        checks.append(_check("scan", sv, (scan["summary"] or {}).get("files_missing_nl")
                             or f"Scan & koppeling: {scan['verdict']}.", report_id=scan["id"]))
    ver = store.latest_report(ctx, run_id, "verify", target)
    if ver is None:
        checks.append(_check("verify", "fail", "Nog geen vergelijkingsrapport."))
    else:
        live = verdict(ver["mismatches"], store.accepted_ids(ctx, run_id, target))
        checks.append(_check("verify", {"go": "ok", "warn": "warn", "no_go": "fail"}[live],
                             f"Vergelijkingsrapport #{ver['id']}: {live}.", report_id=ver["id"]))
    health = await check_instance(ctx, target, notify=False)
    inst = instance_row(ctx, target)
    assert inst is not None
    # In schaduwmodus staan RSS/automatisch zoeken/downloadclients bewust uit; de health-
    # meldingen die Sonarr/Radarr daarover geeft zijn dan verwacht en geen NO-GO. Meldingen
    # over de bibliotheek (bv. films die van TMDb verwijderd zijn) of die je huidige
    # installatie ook al had, zijn een waarschuwing, geen blokkade.
    source_health: set[str] = set()
    if row["kind"] == "live":
        try:
            source_health = {str(h.get("source")) for h in
                             await source_client(ctx, row["id"]).health()}
        except Exception:  # noqa: BLE001 - alleen ter vergelijking
            source_health = set()
    errors = [h for h in health.get("health") or [] if str(h.get("type")).lower() == "error"
              and not (inst["shadow_mode"] and h.get("source") in SHADOW_EXPECTED_HEALTH)]
    soft = [h for h in errors if h.get("source") in INFORMATIONAL_HEALTH
            or h.get("source") in source_health]
    hard = [h for h in errors if h not in soft]
    reachable = health.get("api_key_valid") is True and health.get("status") != "offline"
    if reachable and not hard:
        checks.append(_check("target_healthy", "ok",
                             "Doel-fork gezond." if health["ok"] else
                             "Doel-fork gezond (alleen verwachte schaduwmodus-meldingen)."))
        for h in soft:
            pre = " (had je huidige installatie ook al)" if h.get("source") in source_health \
                else ""
            checks.append(_check("target_notice", "warn", f"Melding van de fork{pre}: "
                                 f"{h.get('message')}"))
    else:
        detail = "; ".join(str(h.get("message")) for h in hard) or health.get("error")
        checks.append(_check("target_healthy", "fail", f"Doel-fork: {detail}"))
    client = arr_client(ctx, target)
    if params.get("register_webhook", True):
        # altijd (idempotent): na een native restore is de webhook uit de doel-DB verdwenen
        # en verwijst webhook_notification_id naar een niet-bestaande connection
        try:
            await register_webhook(ctx, target, actor)
            inst = instance_row(ctx, target)
            assert inst is not None
        except Exception as exc:  # noqa: BLE001 - wordt als check gerapporteerd
            checks.append(_check("webhook", "fail", f"Webhook registreren mislukt: {exc}"))
    if inst["webhook_notification_id"]:
        try:
            hook = await client.provider_get("notification", int(inst["webhook_notification_id"]))
            await client.provider_test("notification", hook)
            checks.append(_check("webhook", "ok", "Webhook geregistreerd en hooks bereikbaar."))
        except ArrError as exc:
            checks.append(_check("webhook", "fail", f"Webhooktest mislukt: {exc.message}"))
    # Losse indexers die niet werken (vaak al zo in je huidige installatie, of verdwenen uit
    # Prowlarr) blokkeren de overstap niet. Blokkerend is alleen: geen enkele werkende.
    for kind, label in (("indexer", "Indexer"), ("downloadclient", "Downloadclient")):
        items = await client.provider_list(kind)
        if not items:
            checks.append(_check(f"{kind}_present", "fail", f"Geen {label.lower()}s op het doel."))
            continue
        working: list[str] = []
        failing: dict[str, list[dict[str, str]]] = {}
        for item in items:
            try:
                await client.provider_test(kind, item)
                working.append(str(item["name"]))
            except ArrError as exc:
                why = explain_provider_error(exc.message)
                failing.setdefault(why, []).append({"name": str(item["name"]),
                                                    "detail": exc.message})
        if working:
            checks.append(_check(f"{kind}_test", "ok",
                                 f"{len(working)} {label.lower()}(s) test OK.", names=working))
        # één regel per oorzaak i.p.v. 11 losse regels × 4 instanties (rapport 4 nov, H13)
        for why, rows in failing.items():
            names = [r["name"] for r in rows]
            dead = "Prowlarr kent deze indexer niet" in why
            text = (f"{len(names)} {label.lower()}(s) bestaan niet meer in Prowlarr — worden na "
                    "de overstap automatisch opgeruimd" if dead else
                    f"{len(names)} {label.lower()}(s): {why}")
            checks.append(_check(f"{kind}_test", "warn", f"{text}: {', '.join(names[:12])}"
                                 + (" …" if len(names) > 12 else ""), names=names,
                                 grouped=True, dead_in_prowlarr=dead,
                                 detail="; ".join(r["detail"] for r in rows[:3])))
        checks.append(_check(f"{kind}_working", "ok" if working else "fail",
                             f"{len(working)} van {len(items)} {label.lower()}s werken."
                             if working else f"Geen enkele {label.lower()} werkt op het doel."))
    checks += await _version_and_rss(ctx, row, inst, client)
    checks += await _monitoring_proof(ctx, row, m, client)
    checks.append(await _seerr_readiness(ctx, row, target, client))
    if inst["suspended"]:
        checks.append(_check("not_suspended", "fail", "Doel is gepauzeerd via Instanties; hervat "
                             "eerst (anders herstelt hervatten oude standen)."))
    v = _verdict_of(checks)
    store.add_report(ctx, run_id, "readiness", v, source_id=row["id"], target=target,
                     summary={"checks": checks}, actor=actor)
    return {"go": "succeeded", "warn": "warning", "no_go": "blocked"}[v], {"verdict": v,
                                                                           "checks": checks}


async def _version_and_rss(ctx: AppContext, row: dict[str, Any], inst: dict[str, Any],
                           client: ArrClient) -> list[dict[str, Any]]:
    """Versie bron vs doel (een nieuwere bron in een oudere fork terugzetten is een
    downgrade) en of de oude installatie nog RSS/automatisch zoeken doet naast een doel dat
    al downloadt (dubbele downloads)."""
    out: list[dict[str, Any]] = []
    src_version = row.get("version")
    tgt_version = None
    try:
        tgt_version = (await client.system_status()).get("version")
    except ArrError:
        tgt_version = None
    if src_version and tgt_version:
        sv, tv = _version_tuple(str(src_version)), _version_tuple(str(tgt_version))
        if sv[:1] > tv[:1]:
            # geen waarschuwing: de overstap loopt via de API (geen database-downgrade) en
            # wat het doel niet kent, wordt automatisch overgeslagen en geaccepteerd
            out.append(_check(
                "version", "ok", f"Oud {src_version} → nieuw {tgt_version}. Overgezet via de "
                "API; instellingen die alleen in de nieuwere versie bestaan worden "
                "overgeslagen.", source_version=src_version, target_version=tgt_version,
                info=True, newer_source=True))
        else:
            out.append(_check("version", "ok", f"Versies: oud {src_version}, nieuw "
                              f"{tgt_version}.", source_version=src_version,
                              target_version=tgt_version))
    if row["kind"] != "live":
        return out
    try:
        idx = await source_client(ctx, row["id"]).indexers()
    except Exception:  # noqa: BLE001 - alleen informatief
        return out
    rss = [i["name"] for i in idx if i.get("enableRss") or i.get("enableAutomaticSearch")]
    target_grabs = not inst["shadow_mode"]
    if rss and target_grabs:
        out.append(_check(
            "old_rss", "warn", f"De oude installatie zoekt nog ({len(rss)} indexer(s) met RSS/"
            "automatisch zoeken) terwijl de nieuwe al downloadt: kans op dubbele downloads. "
            "Bij het overschakelen zet MediaGuard de oude indexers uit.",
            old_rss_paused=False, target_shadow=False, names=rss))
    else:
        out.append(_check(
            "old_rss", "ok", "Oude installatie: RSS/automatisch zoeken staat uit." if not rss
            else "Oude installatie zoekt nog; de nieuwe is nog in proefmodus (downloadt "
            "niets). Geen dubbele downloads.", old_rss_paused=not rss,
            target_shadow=bool(inst["shadow_mode"])))
    return out


async def _monitoring_proof(ctx: AppContext, row: dict[str, Any], m: dict[str, Any],
                            client: ArrClient) -> list[dict[str, Any]]:
    src = await _source_snapshot(ctx, row["id"], episodes=True)
    tgt = await from_api(client, episodes=True)
    if src["app"] == "sonarr":
        s = sum(int(x.get("future_monitored") or 0) for x in src["series"].values())
        t = sum(int(x.get("future_monitored") or 0) for x in tgt["series"].values())
        new_s = sum(1 for x in src["series"].values() if x.get("monitorNewItems") == "all")
        new_t = sum(1 for x in tgt["series"].values() if x.get("monitorNewItems") == "all")
        return [_check("future_episodes_monitored", "ok" if s == t else "fail",
                       f"Gemonitorde toekomstige afleveringen: bron {s}, doel {t}.",
                       source=s, target=t),
                _check("new_seasons_followed", "ok" if new_s <= new_t else "fail",
                       f"Series die nieuwe seizoenen volgen: bron {new_s}, doel {new_t}.")]
    s = sum(1 for x in src["movies"].values() if x["monitored"])
    t = sum(1 for x in tgt["movies"].values() if x["monitored"])
    return [_check("movies_monitored", "ok" if s == t else "fail",
                   f"Gemonitorde films: bron {s}, doel {t}.", source=s, target=t)]


async def seerr_profile_overrides(ctx: AppContext, row: dict[str, Any], server: dict[str, Any],
                                  client: ArrClient) -> dict[str, Any]:
    """Seerr bewaart profiel-ID's van de OUDE instantie; vertaal op naam naar het doel."""
    if row["kind"] == "live":
        src_names = {int(q["id"]): q["name"] for q in
                     await source_client(ctx, row["id"]).quality_profiles()}
    else:
        src_names = {}
    tgt_ids = {q["name"]: int(q["id"]) for q in await client.quality_profiles()}
    out: dict[str, Any] = {}
    for field in ("activeProfileId", "activeAnimeProfileId"):
        pid = server.get(field)
        if pid is None:
            continue
        name = src_names.get(int(pid)) or server.get(field.replace("Id", "Name"))
        if name in tgt_ids:
            out[field] = tgt_ids[name]
            if field == "activeProfileId":
                out["activeProfileName"] = name
    return out


async def find_seerr_server(ctx: AppContext, row: dict[str, Any],
                            server_id: int | None = None) -> dict[str, Any] | None:
    from mediaguard.seerr.client import seerr_client
    from mediaguard.seerr.service import _norm_url, server_url

    servers = await seerr_client(ctx).servers(row["app"])
    if server_id is not None:
        return next((s for s in servers if int(s["id"]) == int(server_id)), None)
    if not row["url"]:
        return None
    return next((s for s in servers if server_url(s) == _norm_url(row["url"])), None)


async def _seerr_readiness(ctx: AppContext, row: dict[str, Any], target: str,
                           client: ArrClient) -> dict[str, Any]:
    from mediaguard.seerr.client import SeerrError, seerr_client
    from mediaguard.seerr.service import _expected_url, connection_fields

    try:
        seerr = seerr_client(ctx)
        server = await find_seerr_server(ctx, row)
        inst = instance_row(ctx, target)
        assert inst is not None
        conn = {**connection_fields(_expected_url(inst)),
                "apiKey": ctx.secrets.get(target_key_secret(target))}
        tested = await seerr.server_test(row["app"], conn)
    except SeerrError as exc:
        if exc.code == "SEERR_NOT_CONFIGURED":
            return _check("seerr_route", "skipped", "Seerr niet ingesteld; geen omschakeling.")
        return _check("seerr_route", "fail", f"Seerr kan het doel niet bereiken: {exc.message}")
    ok = isinstance(tested, dict) and "profiles" in tested
    if server is None:
        return _check("seerr_route", "warn" if ok else "fail",
                      "Seerr bereikt het doel; geen Seerr-server wijst naar de oude instantie "
                      "(geef bij cutover eventueel een server-ID op).")
    overrides = await seerr_profile_overrides(ctx, row, server, client)
    missing = [f for f in ("activeProfileId",) if server.get(f) is not None and f not in overrides]
    roots = {str(r["path"]).rstrip("/") for r in await client.root_folders()}
    root_ok = str(server.get("activeDirectory") or "").rstrip("/") in roots
    status = "ok" if ok and not missing and root_ok else "fail"
    return _check("seerr_route", status,
                  f"Seerr-server '{server.get('name')}' (#{server['id']}) wordt bij cutover naar "
                  f"{target} omgezet; verbindingstest {'OK' if ok else 'mislukt'}, profiel "
                  f"{'vertaald' if not missing else 'niet gevonden'}, rootmap "
                  f"{'bestaat' if root_ok else 'ontbreekt in het doel'}.",
                  server_id=server["id"], overrides=overrides)


STEP_FUNCS = {"preflight": run_preflight, "backup": run_backup,
              "shadow_import": run_shadow_import, "verify": run_verify, "scan": run_scan,
              "optimization": run_optimization, "readiness": run_readiness}
