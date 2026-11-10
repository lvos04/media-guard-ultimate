"""Systeemonderhoud via de UI: back-ups/restore van de core-DB, health van alle
componenten, diagnostics per component, supportbundel zonder geheimen, versie-info
en UI-control-coverage (Q2.3)."""

from __future__ import annotations

import hashlib
import io
import json
import platform
import shutil
import sqlite3
import zipfile
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import httpx
from fastapi import APIRouter, Depends, HTTPException
from fastapi.responses import FileResponse, StreamingResponse
from pydantic import BaseModel, Field

from mediaguard import __version__
from mediaguard.audit import audit
from mediaguard.auth import Principal, require_admin, require_operator, require_viewer
from mediaguard.context import AppContext, get_app_context
from mediaguard.db import utcnow
from mediaguard.secrets_store import redact
from mediaguard.settings_registry import REGISTRY, SettingDef

router = APIRouter(prefix="/api/v1", tags=["maintenance"])


def register_settings() -> None:
    from mediaguard.maintenance.housekeeping import setting_defs

    REGISTRY.register(*setting_defs())
    REGISTRY.register(
        SettingDef("backup.retention_count", "int", 14, "Back-ups", "Aantal te bewaren back-ups",
                   "Backups to keep", minimum=1, maximum=365,
                   help_nl="Oudere automatische back-ups worden opgeruimd; handmatige blijven."),
        SettingDef("backup.daily_enabled", "bool", True, "Back-ups", "Dagelijkse back-up",
                   "Daily backup", help_nl="Maakt elke nacht een consistente back-up van de "
                   "MediaGuard-database (configuratie, jobs, audit, beleid) en de database "
                   "van de AV1-engine."),
        SettingDef("backup.bundle_retention", "int", 7, "Back-ups",
                   "Aantal te bewaren herstelbundels", "Recovery bundles to keep",
                   help_nl="Versleutelde bundels met ook de sleutels (alleen als je een "
                           "herstelcode hebt ingesteld).", minimum=1, maximum=90, advanced=True),
    )


def _backup_dir(ctx: AppContext) -> Path:
    d = ctx.settings.backup_dir
    d.mkdir(parents=True, exist_ok=True)
    return d


def _sha256(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


class BackupError(Exception):
    """Back-up mislukt of niet te vertrouwen; halve bestanden zijn dan al opgeruimd."""


PARTIAL_GRACE_SECONDS = 3600  # een lopende back-up van een ander proces niet weggooien


def _verify_copy(path: Path, *, full: bool = True) -> tuple[bool, list[str]]:
    """Hersteltest: open de kopie en controleer integriteit + schema."""
    conn = sqlite3.connect(f"file:{path}?mode=ro", uri=True)
    try:
        pragma = "integrity_check" if full else "quick_check"
        ok = conn.execute(f"PRAGMA {pragma}").fetchone()[0] == "ok"
        try:
            versions = [r[0] for r in conn.execute("SELECT version FROM schema_migrations")]
        except sqlite3.Error:
            versions = []
    finally:
        conn.close()
    return ok, versions


def _backup_files(target: Path) -> list[Path]:
    """Alle bestanden van één back-up: de kopie, de metadata, de AV1-kopieën en halve
    ``.part``-bestanden."""
    files = [target, target.with_suffix(".json"), target.with_name(target.name + ".part")]
    files += list(target.parent.glob(target.stem + ".encode-*"))
    return files


def _remove_backup(target: Path) -> None:
    for f in _backup_files(target):
        f.unlink(missing_ok=True)


def create_backup(ctx: AppContext, *, kind: str = "manual",
                  actor: str = "system") -> dict[str, Any]:
    """Consistente kopie + hersteltest. Eerst naar ``.part``; pas na een geslaagde controle
    krijgt de kopie zijn echte naam en metadata. Bij elke fout verdwijnen de halve
    bestanden en volgt een :class:`BackupError` (een kapotte back-up telt nooit mee)."""
    stamp = datetime.now(UTC).strftime("%Y%m%dT%H%M%SZ")
    target = _backup_dir(ctx) / f"mediaguard-{kind}-{stamp}.db"
    part = target.with_name(target.name + ".part")
    try:
        ctx.db.backup_to(part)
        ok, versions = _verify_copy(part)
        if not ok:
            raise BackupError("de hersteltest van de kopie mislukte (integrity_check)")
        import os

        os.replace(part, target)
        extra = _backup_encoder_dbs(ctx, target)
        meta = {"file": target.name, "kind": kind, "created_at": utcnow(),
                "sha256": _sha256(target), "size": target.stat().st_size,
                "integrity_ok": True, "schema_versions": versions,
                "app_version": __version__, "extra_files": extra,
                "encoder_ok": all("error" not in e for e in extra)}
        meta_file = target.with_suffix(".json")
        tmp = meta_file.with_name(meta_file.name + ".part")
        tmp.write_text(json.dumps(meta, indent=2), encoding="utf-8")
        os.replace(tmp, meta_file)
    except BackupError:
        _remove_backup(target)
        raise
    except (OSError, sqlite3.Error) as exc:
        _remove_backup(target)
        raise BackupError(_backup_error_nl(exc)) from exc
    audit(ctx.db, actor=actor, action="backup.create", target_type="backup", target_id=target.name,
          new_value={"sha256": meta["sha256"], "integrity_ok": True})
    if kind == "auto":
        try:
            _prune(ctx)
        except OSError:  # de back-up zelf is gelukt; opruimen lukt de volgende keer
            import logging

            logging.getLogger(__name__).exception("oude back-ups opruimen mislukt")
    return meta


def _backup_error_nl(exc: BaseException) -> str:
    import errno

    text = str(exc)
    if isinstance(exc, OSError) and exc.errno == errno.ENOSPC or "disk is full" in text \
            or "No space left" in text:
        return "de appdata-map (/config) is vol"
    if isinstance(exc, PermissionError):
        return "geen schrijfrechten in de back-upmap"
    return redact(f"{type(exc).__name__}: {text}")[:300]


def _backup_encoder_dbs(ctx: AppContext, core_copy: Path) -> list[dict[str, Any]]:
    """Kopie van de AV1-engine-database(s) naast de core-back-up (SQLite backup-API). Een
    mislukte kopie laat geen half bestand achter; de fout staat in de metadata."""
    import os

    root = Path(os.environ.get("MG_AIO_CONFIG") or ctx.settings.data_dir.parent) / "encode"
    out = []
    for db in sorted(root.glob("*.db")):
        dest = core_copy.with_name(core_copy.stem + f".encode-{db.name}")
        part = dest.with_name(dest.name + ".part")
        try:
            src, dst = sqlite3.connect(f"file:{db}?mode=ro", uri=True), sqlite3.connect(part)
            try:
                src.backup(dst)
            finally:
                src.close()
                dst.close()
            ok, _versions = _verify_copy(part, full=False)
            if not ok:
                raise sqlite3.DatabaseError("hersteltest mislukt")
            os.replace(part, dest)
            out.append({"file": dest.name, "sha256": _sha256(dest),
                        "size": dest.stat().st_size})
        except (OSError, sqlite3.Error) as exc:
            part.unlink(missing_ok=True)
            dest.unlink(missing_ok=True)
            out.append({"file": db.name, "error": _backup_error_nl(exc)})
    return out


def _meta(db: Path) -> dict[str, Any] | None:
    try:
        row = json.loads(db.with_suffix(".json").read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None
    return row if isinstance(row, dict) else None


def verified(db: Path, meta: dict[str, Any] | None = None) -> bool:
    """Telt mee als goede back-up: hersteltest geslaagd, checksum vastgelegd en het bestand
    is nog even groot als bij het maken (afgekapt = kapot)."""
    meta = _meta(db) if meta is None else meta
    if not meta or not meta.get("integrity_ok") or not meta.get("sha256"):
        return False
    try:
        return db.stat().st_size == int(meta.get("size") or -1)
    except OSError:
        return False


def _auto_day(db: Path) -> str:
    """Dag (UTC) uit de naam ``mediaguard-auto-20261108T021500Z.db``."""
    stamp = db.stem.removeprefix("mediaguard-auto-")
    return stamp[:8] if stamp[:8].isdigit() else stamp


def _prune(ctx: AppContext, now: float | None = None) -> dict[str, int]:
    """Bewaar de nieuwste N *gecontroleerde* automatische back-ups. Niet-gecontroleerde
    (half, afgekapt of zonder metadata) tellen niet mee en verdwijnen na een uur; ze kunnen
    zo nooit een goede back-up wegdrukken."""
    import time

    now = time.time() if now is None else now
    keep = int(ctx.config.value("backup.retention_count"))
    folder = _backup_dir(ctx)
    good: list[Path] = []
    removed = {"old": 0, "broken": 0, "partial": 0}
    for db in sorted(folder.glob("mediaguard-auto-*.db")):
        if ".encode-" in db.name:
            continue
        if verified(db):
            good.append(db)
            continue
        try:
            age = now - db.stat().st_mtime
        except OSError:
            continue
        if age > PARTIAL_GRACE_SECONDS:
            _remove_backup(db)
            removed["broken"] += 1
    # Per dag telt één back-up (de nieuwste). Liep er ooit iets in een lus (elke 10 minuten
    # een back-up), dan drukken die kopieën van één ochtend nooit de oudere dagen weg.
    per_day: dict[str, Path] = {}
    for db in good:  # oplopend: de laatste van een dag wint
        per_day[_auto_day(db)] = db
    for db in good:
        if per_day[_auto_day(db)] is not db:
            _remove_backup(db)
            removed["same_day"] = removed.get("same_day", 0) + 1
    for old in sorted(per_day.values())[:-keep]:
        _remove_backup(old)
        removed["old"] += 1
    for part in folder.glob("mediaguard-*.part"):
        try:
            if now - part.stat().st_mtime > PARTIAL_GRACE_SECONDS:
                part.unlink(missing_ok=True)
                removed["partial"] += 1
        except OSError:
            continue
    return removed


@router.get("/backups")
def list_backups(_: Principal = Depends(require_viewer),
                 ctx: AppContext = Depends(get_app_context)) -> list[dict[str, Any]]:
    """Gewone back-ups én de sets van vóór een update (nieuwste eerst)."""
    out = []
    for meta in sorted(_backup_dir(ctx).glob("mediaguard-*.json"), reverse=True):
        if ".encode-" in meta.name:
            continue
        try:
            row = json.loads(meta.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            continue
        name = str(row.get("file") or meta.with_suffix(".db").name)
        ok = verified(meta.with_suffix(".db"), row)
        # 'name'/'id' zijn wat de web-UI in de downloadlink gebruikt (was: /backups/undefined)
        row.update(name=name, id=name,
                   download_path=f"/api/v1/backups/{name}/download",
                   # elke back-up wordt direct na het maken teruggelezen (integriteit + schema)
                   verified=ok, restore_mode="direct",
                   verified_nl="Hersteltest geslaagd" if ok else
                   "Hersteltest mislukt of bestand onvolledig: maak een nieuwe back-up")
        out.append(row)
    out.extend(_pre_update_rows(ctx))
    out.sort(key=lambda r: str(r.get("created_at") or ""), reverse=True)
    return out


NO_SUPERVISOR_NL = ("Terugzetten vanuit de app kan alleen in de alles-in-één-container. "
                    "Download de back-up en zet hem terug volgens docs/DATA_RECOVERY.md.")


def _pre_update_rows(ctx: AppContext) -> list[dict[str, Any]]:
    from mediaguard.maintenance import preupdate
    from mediaguard.maintenance import status as maint_status

    pending = preupdate.pending(ctx.settings.data_dir) or {}
    aio = maint_status.under_supervisor(ctx)
    rows = []
    for st in preupdate.list_sets(_backup_dir(ctx) / preupdate.FOLDER):
        name = st["name"]
        to_v, from_v = st.get("to_version"), st.get("from_version")
        if to_v:
            label = f"Vóór update naar {to_v}" + (f" (was {from_v})" if from_v else "")
        else:
            label = f"Vóór databasewijziging {st.get('to_schema') or ''}".strip()
        encoder = "met AV1-database" if st.get("encoder_included") else "zonder AV1-database"
        rows.append({
            **{k: v for k, v in st.items() if k != "files"},
            "file": name, "id": name, "kind": "pre-update", "label_nl": label,
            "download_path": f"/api/v1/backups/{name}/download",
            "verified": st.get("integrity_ok") is True,
            "verified_nl": ("Hersteltest geslaagd, " + encoder) if st.get("integrity_ok")
            else "Oude kopie (alleen MediaGuard-database); wordt gecontroleerd bij terugzetten",
            "restore_mode": "bij-stoppen" if aio else "handmatig",
            "staged": pending.get("set") == name,
            "restore_nl": "Wordt teruggezet als de app stopt; daarna kun je in TrueNAS de "
                          "vorige versie starten." if aio else NO_SUPERVISOR_NL})
    return rows


@router.post("/backups")
def backup_now(principal: Principal = Depends(require_operator),
               ctx: AppContext = Depends(get_app_context)) -> dict[str, Any]:
    try:
        return create_backup(ctx, kind="manual", actor=principal.actor)
    except BackupError as exc:
        raise HTTPException(507 if "vol" in str(exc) else 500,
                            f"Back-up mislukt: {exc}") from exc


def _pre_update_path(ctx: AppContext, name: str) -> Path | None:
    from mediaguard.maintenance import preupdate

    if "/" in name or "\\" in name or not preupdate.is_set_name(name):
        return None
    path = preupdate.find(_backup_dir(ctx) / preupdate.FOLDER, name)
    if path is None:
        raise HTTPException(404, "Back-up niet gevonden")
    return path


def _backup_path(ctx: AppContext, name: str) -> Path:
    if "/" in name or "\\" in name or not name.startswith("mediaguard-") \
            or not name.endswith(".db"):
        raise HTTPException(422, "Ongeldige back-upnaam")
    path = _backup_dir(ctx) / name
    if not path.exists():
        raise HTTPException(404, "Back-up niet gevonden")
    return path


# -- versleutelde herstelbundel (ook sleutels) ------------------------------------------------
@router.get("/backups/bundle")
def bundle_status(_: Principal = Depends(require_viewer),
                  ctx: AppContext = Depends(get_app_context)) -> dict[str, Any]:
    from mediaguard.maintenance import bundle

    return bundle.status(ctx)


class BundleSetupBody(BaseModel):
    confirm: bool = False


@router.post("/backups/bundle/setup")
def bundle_setup(body: BundleSetupBody, principal: Principal = Depends(require_admin),
                 ctx: AppContext = Depends(get_app_context)) -> dict[str, Any]:
    """Nieuwe herstelcode. Wordt alleen in dit antwoord getoond: schrijf hem op. Een nieuwe
    code maakt oudere bundels NIET onleesbaar zolang je hun herstelsleutel bewaart."""
    from mediaguard.maintenance import bundle

    if bundle.status(ctx)["enabled"] and not body.confirm:
        raise HTTPException(409, "Er is al een herstelcode. Bevestig om een nieuwe te maken.")
    out = bundle.setup(ctx, principal.actor)
    made = bundle.create(ctx, actor=principal.actor)
    return {**out, "first_bundle": made,
            "message_nl": "Schrijf deze herstelcode op en bewaar hem buiten de NAS. Zonder "
                          "code kan niemand de bundel openen, ook MediaGuard zelf niet."}


@router.post("/backups/bundle")
def bundle_now(principal: Principal = Depends(require_admin),
               ctx: AppContext = Depends(get_app_context)) -> dict[str, Any]:
    from mediaguard.maintenance import bundle

    made = bundle.create(ctx, actor=principal.actor)
    if made is None:
        raise HTTPException(409, "Stel eerst een herstelcode in")
    return made


@router.get("/backups/bundle/{name}/download")
def bundle_download(name: str, _: Principal = Depends(require_admin),
                    ctx: AppContext = Depends(get_app_context)) -> FileResponse:
    from mediaguard.maintenance import bundle

    folder = bundle.bundle_dir(ctx)
    allowed = {p.name for p in folder.glob("mediaguard-bundel-*.mgbundle")} | \
        {"herstelsleutel.json"}
    if name not in allowed:
        raise HTTPException(404, "Onbekend bestand")
    return FileResponse(folder / name, filename=name, media_type="application/octet-stream")


@router.get("/backups/terugzetten")
def rollback_status(_: Principal = Depends(require_viewer),
                    ctx: AppContext = Depends(get_app_context)) -> dict[str, Any]:
    """Staat er een back-up van vóór een update klaar om terug te zetten?"""
    from mediaguard.maintenance import preupdate

    return {"pending": preupdate.pending(ctx.settings.data_dir)}


@router.post("/backups/terugzetten/annuleren")
def rollback_cancel(principal: Principal = Depends(require_admin),
                    ctx: AppContext = Depends(get_app_context)) -> dict[str, Any]:
    from mediaguard.maintenance import preupdate

    info = preupdate.pending(ctx.settings.data_dir)
    done = preupdate.cancel(ctx.settings.data_dir)
    if done:
        audit(ctx.db, actor=principal.actor, action="backup.rollback_cancel",
              target_type="backup", target_id=(info or {}).get("set"))
    return {"cancelled": done}


@router.get("/backups/{name}/download")
def download_backup(name: str, _: Principal = Depends(require_admin),
                    ctx: AppContext = Depends(get_app_context)) -> FileResponse:
    pre = _pre_update_path(ctx, name)
    if pre is not None:  # set van vóór een update: de MediaGuard-database
        path = pre if pre.is_file() else pre / "mediaguard.db"
        return FileResponse(path, filename=f"{name}.db" if pre.is_dir() else name,
                            media_type="application/octet-stream")
    return FileResponse(_backup_path(ctx, name), filename=name,
                        media_type="application/octet-stream")


class RestoreBody(BaseModel):
    confirm: str = Field(description="Typ de back-upnaam ter bevestiging")


@router.post("/backups/{name}/restore")
def restore_backup(name: str, body: RestoreBody, principal: Principal = Depends(require_admin),
                   ctx: AppContext = Depends(get_app_context)) -> dict[str, Any]:
    """Herstelt de core-DB. Maakt eerst een veiligheidsback-up van de huidige staat.
    Geheimen blijven geldig zolang secret.key niet verandert."""
    if ".encode-" in name:
        raise HTTPException(422, "Dit is een kopie van de AV1-engine-database, geen "
                                 "MediaGuard-back-up")
    pre = _pre_update_path(ctx, name)
    if pre is not None:
        if body.confirm != name:
            raise HTTPException(422, "Bevestiging komt niet overeen met de back-upnaam")
        return _stage_pre_update(ctx, pre, principal)
    path = _backup_path(ctx, name)
    if body.confirm != name:
        raise HTTPException(422, "Bevestiging komt niet overeen met de back-upnaam")
    meta_file = path.with_suffix(".json")
    if meta_file.exists():
        meta = json.loads(meta_file.read_text(encoding="utf-8"))
        if meta.get("sha256") != _sha256(path):
            raise HTTPException(409, "Checksum klopt niet; back-up is beschadigd")
    try:
        safety = create_backup(ctx, kind="pre-restore", actor=principal.actor)
    except BackupError as exc:
        raise HTTPException(409, f"Niet hersteld: eerst een veiligheidsback-up van de huidige "
                                 f"staat maken lukte niet ({exc})") from exc
    src = sqlite3.connect(path)
    try:
        conn = ctx.db._connect()
        with ctx.db._lock:
            src.backup(conn)
    finally:
        src.close()
    ctx.config.invalidate()  # instellingen komen nu uit de herstelde database
    audit(ctx.db, actor=principal.actor, action="backup.restore", target_type="backup",
          target_id=name, reason="Herstel uit back-up",
          undo_hint=f"Herstel veiligheidsback-up {safety['file']}")
    return {"restored": name, "safety_backup": safety["file"]}


def _stage_pre_update(ctx: AppContext, path: Path, principal: Principal) -> dict[str, Any]:
    """Back-up van vóór een update: niet live (de databases zijn open en hebben een ouder
    schema), maar klaarzetten. De supervisor wisselt ze om zodra alle diensten gestopt zijn;
    de huidige databases blijven daarbij bewaard."""
    from mediaguard.maintenance import preupdate
    from mediaguard.maintenance import status as maint_status

    if not maint_status.under_supervisor(ctx):
        # zonder supervisor wisselt niemand de databases om: niets klaarzetten dat nooit
        # gebeurt (en dat dan maanden later alsnog zou kunnen gebeuren)
        raise HTTPException(409, f"Niet klaargezet. {NO_SUPERVISOR_NL}")
    try:
        info = preupdate.stage(ctx.settings.data_dir, path, actor=principal.actor)
    except preupdate.SetError as exc:
        raise HTTPException(507 if exc.disk else 409, f"Niet klaargezet: {exc}") from exc
    audit(ctx.db, actor=principal.actor, action="backup.rollback_stage", target_type="backup",
          target_id=path.name, reason="Back-up van vóór de update terugzetten",
          undo_hint="Annuleren onder Instellingen → Systeem → Back-ups")
    meta = next((r for r in preupdate.list_sets(path.parent) if r["name"] == path.name), {})
    old = meta.get("from_version")
    return {"staged": path.name, "pending": info, "restored": None,
            "message_nl": "Klaargezet. Stop de app in TrueNAS"
                          + (f", kies versie {old}" if old else ", kies zo nodig de vorige "
                             "versie") + " en start hem weer. Bij het stoppen zet MediaGuard "
                          "de databases van vóór de update terug (ook die van de AV1-engine, "
                          "als die in de back-up zit)."}


async def _probe(url: str, headers: dict[str, str] | None = None) -> dict[str, Any]:
    try:
        async with httpx.AsyncClient(timeout=5) as client:
            r = await client.get(url, headers=headers or {})
        return {"ok": r.status_code < 400, "status_code": r.status_code,
                "detail": redact(r.text[:300])}
    except httpx.HTTPError as exc:
        return {"ok": False, "status_code": None, "detail": redact(str(exc))}


def _disk(path: Path) -> dict[str, Any]:
    try:
        usage = shutil.disk_usage(path)
    except OSError as exc:
        return {"path": str(path), "ok": False, "detail": str(exc)}
    return {"path": str(path), "ok": True, "total": usage.total, "free": usage.free,
            "used": usage.used}


async def _encoder_health(ctx: AppContext, token: dict[str, str]) -> dict[str, Any]:
    """Encoder-health inclusief baanpauzes met Nederlandse reden (bv. vrije-ruimtebodem).

    Gaat via de encoder-client (zelfde transport als de rest van core, testbaar met de
    fake encoder); valt terug op een kale probe als die client niet beschikbaar is."""
    from mediaguard.encoding.client import EncoderError, get_encoder_client

    try:
        data = await get_encoder_client(ctx).health()
    except EncoderError as exc:
        return {"ok": False, "status_code": exc.status, "detail": redact(str(exc)),
                "lanes_paused": []}
    except Exception:  # noqa: BLE001 - health mag nooit crashen
        return await _probe(ctx.settings.encoder_url.rstrip("/")
                            + "/api/mediaguard/v1/health", token)
    paused = [p for p in (data.get("lanes_paused") or []) if isinstance(p, dict)]
    guard = data.get("storage_guard") if isinstance(data.get("storage_guard"), dict) else {}
    result: dict[str, Any] = {
        "ok": True, "status_code": 200, "status": data.get("status"),
        "version": data.get("version"),
        "detail": redact("; ".join(str(r) for r in data.get("reasons") or [])),
        "lanes_paused": [{"lane": p.get("lane"), "reason": p.get("reason"),
                          "storage_pause": bool(p.get("storage_pause"))} for p in paused],
    }
    if guard:
        result["cache_min_free_gb"] = guard.get("cache_min_free_gb")
        result["cache_free_gb"] = guard.get("free_gb")
        result["cache_below_floor"] = guard.get("below_floor")
    if paused:
        result["pause_reason_nl"] = "; ".join(
            f"{str(p.get('lane', '?')).upper()}-baan gepauzeerd: {p.get('reason')}"
            for p in paused)
    return result


DB_CHECK_TTL_S = 900


def _mono() -> float:
    import time

    return time.monotonic()


def _pragma_check(ctx: AppContext, pragma: str) -> bool:
    """Eigen leesverbinding in een werkdraad: houdt de event loop en het schrijfslot vrij."""
    conn = sqlite3.connect(f"file:{ctx.db.path}?mode=ro", uri=True, timeout=30)
    try:
        return conn.execute(f"PRAGMA {pragma}").fetchone()[0] == "ok"
    finally:
        conn.close()


async def database_check(ctx: AppContext) -> dict[str, Any]:
    """Snelle databasecontrole voor de gezondheidspagina: hooguit eens per 15 minuten echt
    uitgevoerd (de pagina ververst elke 30 s) en nooit op de event loop."""
    import asyncio

    cached = ctx.services.get("db_quick_check")
    if cached and _mono() - cached[0] < DB_CHECK_TTL_S:
        return cached[1]
    try:
        ok = await asyncio.to_thread(_pragma_check, ctx, "quick_check")
    except sqlite3.Error as exc:
        result: dict[str, Any] = {"ok": False, "detail": redact(str(exc))}
    else:
        result = {"ok": ok, "checked_at": utcnow()}
    ctx.services["db_quick_check"] = (_mono(), result)
    return result


@router.get("/admin/health")
async def admin_health(_: Principal = Depends(require_viewer),
                       ctx: AppContext = Depends(get_app_context)) -> dict[str, Any]:
    token = {"X-MediaGuard-Token": ctx.settings.internal_token}
    components: dict[str, Any] = {
        "core": {"ok": True, "version": __version__},
        "database": await database_check(ctx),
        "encoder": await _encoder_health(ctx, token),
    }
    if ctx.settings.role == "core":
        components["recovery"] = await _probe(ctx.settings.recovery_url.rstrip("/")
                                              + "/api/v1/health")
    try:
        from mediaguard.instances.registry import list_instances
        for inst in list_instances(ctx):
            components[f"instance:{inst.id}"] = {"ok": inst.status == "healthy",
                                                 "status": inst.status,
                                                 "suspended": inst.suspended}
    except ImportError:
        pass
    age = backup_age_hours(ctx)
    if ctx.config.value("backup.daily_enabled"):
        stale = age is None or age > BACKUP_STALE_HOURS
        fail = ctx.state_get("backup.last_failure")
        detail = "Nog geen automatische back-up" if age is None else (
            f"Laatste back-up {age:.0f} uur geleden" + (" — ouder dan 48 uur" if stale
                                                        else ""))
        if isinstance(fail, dict) and fail.get("at"):
            detail += f"; laatste poging mislukt: {fail.get('error')}"
        components["backup"] = {
            "ok": not stale and not (isinstance(fail, dict) and fail.get("at")),
            "age_hours": None if age is None else round(age, 1),
            "detail_nl": detail, "detail": detail}
    from mediaguard.maintenance import status as maint_status

    restarts = maint_status.restart_component(ctx)
    if restarts is not None:
        components["herstarts"] = restarts
    storage = [{**_disk(p), "kind": kind} for kind, p in (
        ("data", ctx.settings.data_dir), ("staging", ctx.settings.staging_dir),
        ("quarantine", ctx.settings.quarantine_dir))]
    from mediaguard.recovery import probe

    write_probe = probe.status(ctx)
    lowered: dict[str, list[str]] = {}
    for r in write_probe["roots"]:
        lowered.setdefault(str(r["path"]).lower(), []).append(str(r["path"]))
    for r in write_probe["roots"]:
        # bibliotheekmappen met de laatste schrijftest (aanmaken → hernoemen → verwijderen)
        disk = _disk(Path(r["path"]))
        twins = [x for x in lowered.get(str(r["path"]).lower(), []) if x != r["path"]]
        storage.append({**disk, "kind": "library", "ok": bool(disk.get("ok") and r["ok"]),
                        "write_probe": r,
                        # rapport 2 / N11: naam uit het pad, tweelingmappen en offline
                        "label_nl": Path(str(r["path"])).name or str(r["path"]),
                        "twin_of": twins or None,
                        "offline": not Path(str(r["path"])).is_dir()})
    if write_probe["failing"]:
        components["library_write"] = {
            "ok": False, "detail_nl": "; ".join(
                f"{r['path']}: mislukt bij '{r.get('step_nl') or r.get('step')}'"
                for r in write_probe["failing"])}
    return {"components": components, "storage": storage, "write_probe": write_probe,
            "emergency_stop": ctx.state_get("emergency_stop", {"active": False})}


@router.post("/admin/diagnostics/{component}")
async def diagnostics(component: str, principal: Principal = Depends(require_operator),
                      ctx: AppContext = Depends(get_app_context)) -> dict[str, Any]:
    if component == "database":
        import asyncio

        # volledige controle van een grote database duurt seconden: buiten de event loop
        ok = await asyncio.to_thread(_pragma_check, ctx, "integrity_check")
        result: dict[str, Any] = {"ok": ok}
        ctx.services["db_quick_check"] = (_mono(), {"ok": ok, "checked_at": utcnow()})
    elif component == "encoder":
        result = await _probe(ctx.settings.encoder_url.rstrip("/")
                              + "/api/mediaguard/v1/capabilities",
                              {"X-MediaGuard-Token": ctx.settings.internal_token})
    elif component == "storage":
        result = {"paths": [_disk(p) for p in (ctx.settings.data_dir, ctx.settings.staging_dir,
                                               ctx.settings.quarantine_dir)]}
        result["ok"] = all(p["ok"] for p in result["paths"])
    elif component == "herstarts":
        from mediaguard.maintenance import status as maint_status

        result = maint_status.restart_component(ctx) or {
            "ok": True, "detail": "Geen gegevens van de supervisor (geen alles-in-één)"}
    elif component == "backup":
        fail = ctx.state_get("backup.last_failure")
        age = backup_age_hours(ctx)
        result = {"ok": not (isinstance(fail, dict) and fail.get("at")) and age is not None
                  and age <= BACKUP_STALE_HOURS, "age_hours": age, "last_failure": fail}
    else:
        raise HTTPException(404, "Onbekend component (database, encoder, storage, herstarts, "
                                 "backup)")
    audit(ctx.db, actor=principal.actor, action="diagnostics.run", target_type="component",
          target_id=component, new_value={"ok": result.get("ok")})
    return result


@router.get("/admin/support-bundle")
def support_bundle(principal: Principal = Depends(require_admin),
                   ctx: AppContext = Depends(get_app_context)) -> StreamingResponse:
    """Zip met versies, instellingen-overrides, recente audit/jobs (geredigeerd), GEEN
    geheimen, GEEN secret.key, geen sessies/gebruikerswachtwoorden."""
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w", zipfile.ZIP_DEFLATED) as zf:
        zf.writestr("versions.json", json.dumps({
            "mediaguard": __version__, "python": platform.python_version(),
            "platform": platform.platform(), "generated_at": utcnow()}, indent=2))
        zf.writestr("settings_overrides.json", redact(json.dumps(
            ctx.config.overrides(), indent=2, default=str)))
        zf.writestr("audit_recent.json", redact(json.dumps(ctx.db.fetch_dicts(
            "SELECT * FROM audit_events ORDER BY id DESC LIMIT 1000"), indent=2, default=str)))
        zf.writestr("jobs_recent.json", redact(json.dumps(ctx.db.fetch_dicts(
            "SELECT id,job_type,status,instance_id,error,attempts,created_at,finished_at "
            "FROM jobs ORDER BY id DESC LIMIT 1000"), indent=2, default=str)))
        zf.writestr("secrets_present.json", json.dumps(
            [r["name"] for r in ctx.db.fetch_all("SELECT name FROM secrets")], indent=2))
        # rapport 16: de echte logs (core, recovery, AV1-engine, supervisor), geredigeerd
        from mediaguard import logs

        for name, text in logs.bundle_entries(ctx.settings):
            zf.writestr(name, redact(text))
        from mediaguard.maintenance import status as maint_status

        zf.writestr("supervisor.json", redact(json.dumps(
            maint_status.supervisor_state(ctx), indent=2, default=str)))
        zf.writestr("maintenance.json", redact(json.dumps({
            k: ctx.state_get(k) for k in (
                "backup.last_auto", "backup.last_failure", "backup.bundle_last_auto",
                "backup.bundle_last_failure", "maintenance.last_nightly",
                "maintenance.last_failure", "app.version")}, indent=2, default=str)))
    audit(ctx.db, actor=principal.actor, action="diagnostics.support_bundle")
    buffer.seek(0)
    return StreamingResponse(buffer, media_type="application/zip", headers={
        "Content-Disposition": "attachment; filename=mediaguard-support.zip"})


@router.get("/admin/coverage")
def coverage(_: Principal = Depends(require_viewer),
             ctx: AppContext = Depends(get_app_context)) -> dict[str, Any]:
    """Alle instellingen zijn via het schema-gedreven formulier bewerkbaar; dit endpoint
    levert de matrix die ook docs/UI_CONTROL_COVERAGE_MATRIX.md genereert."""
    rows = [{"key": d.key, "category": d.category, "type": d.type, "scopes": list(d.scopes),
             "label_nl": d.label_nl, "ui_control": _control_for(d),
             "integrity_locked": d.integrity_locked} for d in ctx.registry.all()]
    return {"total": len(rows), "settings": rows}


def _control_for(d: SettingDef) -> str:
    return {"bool": "schakelaar", "enum": "keuzelijst", "list": "meerkeuze",
            "int": "getalveld", "float": "getalveld", "duration": "duurveld",
            "time_window": "tijdvenster", "string": "tekstveld", "json": "editor",
            "secret": "geheimveld"}.get(d.type, "tekstveld")


@router.get("/admin/version")
def version_info(_: Principal = Depends(require_viewer),
                 ctx: AppContext = Depends(get_app_context)) -> dict[str, Any]:
    manifest_path = release_manifest_path()
    manifest: dict[str, Any] = {}
    error = None
    if manifest_path is not None:
        try:
            manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError) as exc:
            error = f"manifest onleesbaar: {exc}"
    applied = [r["version"] for r in ctx.db.fetch_all(
        "SELECT version FROM schema_migrations ORDER BY version")]
    out: dict[str, Any] = {"version": __version__, "schema_versions": applied,
                           "manifest": manifest, "manifest_found": bool(manifest),
                           "manifest_path": str(manifest_path) if manifest_path else None}
    if error:
        out["manifest_error"] = error
    if manifest and manifest.get("version") not in (None, __version__):
        out["manifest_warning"] = (f"manifestversie {manifest.get('version')} wijkt af van "
                                   f"core {__version__}")
    return out


def release_manifest_path() -> Path | None:
    """1) ``MG_RELEASE_MANIFEST`` (in het core-image: /usr/share/mediaguard/…),
    2) ``deploy/release-manifest.json`` in een repo-checkout (ontwikkeling/tests)."""
    import os

    candidates = []
    env = os.environ.get("MG_RELEASE_MANIFEST")
    if env:
        candidates.append(Path(env))
    here = Path(__file__).resolve()
    if len(here.parents) > 4:
        candidates.append(here.parents[4] / "deploy" / "release-manifest.json")
    for candidate in candidates:
        if candidate.is_file():
            return candidate
    return None


BACKUP_STALE_HOURS = 48


def backup_age_hours(ctx: AppContext) -> float | None:
    last = ctx.state_get("backup.last_auto")
    if not last:
        return None
    try:
        return (datetime.now(UTC) - datetime.fromisoformat(last)).total_seconds() / 3600
    except ValueError:
        return None


RETRY_AFTER_FAILURE_S = 3600
CATCH_UP_HOURS = 36

# Drie losse stappen met elk hun eigen foutafhandeling (rapport: een mislukte bundel liet
# de back-up elke 10 minuten opnieuw lopen en het nachtonderhoud stilvallen).
STEPS: dict[str, dict[str, str]] = {
    "backup": {
        "ok_key": "backup.last_auto", "fail_key": "backup.last_failure",
        "dedupe": "backup.failed", "severity": "error", "title": "Back-up mislukt",
        "body": "De automatische back-up is mislukt: {error}. MediaGuard probeert het over "
                "een uur opnieuw. Je bestaande back-ups blijven bewaard."},
    "bundle": {
        "ok_key": "backup.bundle_last_auto", "fail_key": "backup.bundle_last_failure",
        "dedupe": "backup.bundle_failed", "severity": "error",
        "title": "Herstelbundel mislukt",
        "body": "De versleutelde herstelbundel is niet gemaakt: {error}. De gewone back-up "
                "staat er wel. MediaGuard probeert het over een uur opnieuw."},
    "nightly": {
        "ok_key": "", "fail_key": "maintenance.last_failure",
        "dedupe": "maintenance.failed", "severity": "warning",
        "title": "Nachtelijk onderhoud mislukt",
        "body": "Het opruimen van de database is mislukt: {error}. Back-ups gaan gewoon "
                "door; MediaGuard probeert het over een uur opnieuw."},
}


def _parse_ts(value: Any) -> datetime | None:
    if not value:
        return None
    try:
        at = datetime.fromisoformat(str(value))
    except ValueError:
        return None
    return at if at.tzinfo else at.replace(tzinfo=UTC)


def _in_backoff(ctx: AppContext, step: str, now: datetime) -> bool:
    fail = ctx.state_get(STEPS[step]["fail_key"]) or {}
    at = _parse_ts(fail.get("at")) if isinstance(fail, dict) else None
    return at is not None and (now - at).total_seconds() < RETRY_AFTER_FAILURE_S


def _due_daily(last: Any, now: datetime) -> bool:
    """Eén keer per nacht na 04:15 lokale tijd, of meteen als het > 36 uur geleden is
    (container stond 's nachts uit)."""
    from mediaguard.maintenance import housekeeping

    at = _parse_ts(last)
    if at is None or (now - at).total_seconds() >= CATCH_UP_HOURS * 3600:
        return True
    if (now.hour, now.minute) < (housekeeping.BACKUP_HOUR, housekeeping.BACKUP_MINUTE):
        return False
    return at.astimezone(now.tzinfo).date() < now.date()


def backup_due(ctx: AppContext, now: datetime | None = None) -> bool:
    now = now or datetime.now().astimezone()
    return bool(ctx.config.value("backup.daily_enabled")) and not _in_backoff(
        ctx, "backup", now) and _due_daily(ctx.state_get("backup.last_auto"), now)


def bundle_due(ctx: AppContext, now: datetime | None = None) -> bool:
    from mediaguard.maintenance import bundle

    now = now or datetime.now().astimezone()
    return bool(ctx.config.value("backup.daily_enabled")) and bool(
        bundle.status(ctx)["enabled"]) and not _in_backoff(ctx, "bundle", now) and \
        _due_daily(ctx.state_get("backup.bundle_last_auto"), now)


def nightly_due(ctx: AppContext, now: datetime | None = None) -> bool:
    from mediaguard.maintenance import housekeeping

    now = now or datetime.now().astimezone()
    return housekeeping.due_now(ctx, now) and not _in_backoff(ctx, "nightly", now)


def _run_step(ctx: AppContext, step: str, fn: Any, now: datetime) -> dict[str, Any]:
    import logging

    cfg = STEPS[step]
    stamp = now.astimezone(UTC).isoformat(timespec="seconds")
    try:
        result = fn()
    except Exception as exc:  # noqa: BLE001 - elke stap apart afvangen
        logging.getLogger(__name__).exception("%s mislukt", step)
        error = str(exc) if isinstance(exc, BackupError) else _backup_error_nl(exc)
        prev = ctx.state_get(cfg["fail_key"]) or {}
        count = int(prev.get("count") or 0) + 1 if isinstance(prev, dict) else 1
        ctx.state_set(cfg["fail_key"], {"at": stamp, "error": error, "count": count})
        return {"ok": False, "error": error}
    if cfg["ok_key"]:
        ctx.state_set(cfg["ok_key"], stamp)
    ctx.state_set(cfg["fail_key"], None)
    return {"ok": True, "result": result}


def run_cycle(ctx: AppContext, now: datetime | None = None) -> dict[str, dict[str, Any]]:
    """Eén ronde (in een werkdraad): back-up, herstelbundel en nachtonderhoud, elk los."""
    from mediaguard.maintenance import bundle, housekeeping

    now = now or datetime.now().astimezone()
    out: dict[str, dict[str, Any]] = {}
    if backup_due(ctx, now):
        out["backup"] = _run_step(ctx, "backup", lambda: create_backup(ctx, kind="auto"), now)
    try:
        due = bundle_due(ctx, now)
    except Exception:  # noqa: BLE001 - status onleesbaar: de bundelstap zelf meldt het
        due = True
    if due:
        def make_bundle() -> Any:
            made = bundle.create(ctx)  # alleen met herstelcode
            if made is None:
                raise BackupError("geen herstelcode ingesteld")
            return made

        out["bundle"] = _run_step(ctx, "bundle", make_bundle, now)
    if nightly_due(ctx, now):
        out["nightly"] = _run_step(ctx, "nightly", lambda: housekeeping.run_nightly(ctx), now)
    return out


def report_cycle(ctx: AppContext, results: dict[str, dict[str, Any]]) -> None:
    """Meldingen op de event loop: een mislukking gaat direct naar je telefoon (categorie
    fouten), een geslaagde volgende poging lost de melding vanzelf op."""
    from mediaguard.api.system import notify, resolve_notifications

    for step, res in results.items():
        cfg = STEPS[step]
        if res.get("ok"):
            resolve_notifications(ctx, cfg["dedupe"])
            continue
        notify(ctx, cfg["severity"], cfg["title"],
               cfg["body"].format(error=res.get("error") or "onbekende fout"),
               link="/systeem#back-ups", dedupe_key=cfg["dedupe"])


async def _watch_round(ctx: AppContext) -> None:
    """Herstartlussen, teruggezette back-up en 'Nieuw in deze versie' (los afgevangen)."""
    import logging

    from mediaguard import notices
    from mediaguard.maintenance import status as maint_status

    try:
        maint_status.check(ctx)
    except Exception:  # noqa: BLE001 - elk onderdeel apart
        logging.getLogger(__name__).exception("controle op herstarts mislukt")
    try:
        await notices.tick(ctx)
    except Exception:  # noqa: BLE001
        logging.getLogger(__name__).exception("'Nieuw in deze versie' mislukt")


async def _daily_backup_loop(ctx: AppContext) -> None:
    """Elke 10 minuten kijken of een stap aan de beurt is (vast moment 04:15, lokale tijd)."""
    import asyncio
    import logging

    log = logging.getLogger(__name__)
    await asyncio.sleep(60)
    while True:
        try:
            results = await asyncio.to_thread(run_cycle, ctx)
            report_cycle(ctx, results)
        except Exception:  # noqa: BLE001 - de lus mag nooit stoppen
            log.exception("automatische back-up/onderhoud mislukt")
        await _watch_round(ctx)
        from mediaguard import memory

        await asyncio.to_thread(memory.trim)  # elke 10 min vrije heap teruggeven
        await asyncio.sleep(600)


async def startup(ctx: AppContext) -> None:
    from mediaguard import logs, taskwatch

    if not ctx.settings.start_background:
        return
    # beide rollen: 'Logniveau' zonder herstart toepassen (eigen naam per rol, zodat core en
    # de recovery-worker elkaars taakstatus niet overschrijven)
    role = ctx.settings.role
    main = role in ("core", "all")
    ctx.services["log_level_task"] = taskwatch.spawn(
        ctx, "loglevel" if main else f"loglevel_{role}", logs.watch,
        label_nl="Logniveau toepassen" if main else "Logniveau toepassen (herstel)")
    if role == "core":
        # alleen herstarten na een onverwachte fout; de back-up heeft een eigen leeftijdscontrole
        ctx.services["backup_task"] = taskwatch.spawn(
            ctx, "backup", _daily_backup_loop, label_nl="Back-up en nachtonderhoud")


async def shutdown(ctx: AppContext) -> None:
    for name in ("backup_task", "log_level_task"):
        task = ctx.services.pop(name, None)
        if task is not None:
            task.cancel()
