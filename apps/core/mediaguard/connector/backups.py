"""Herstelmetadata uit bestaande back-ups. Schrijft geen back-up of herstelopdracht."""

from __future__ import annotations

import asyncio
import base64
import hashlib
import json
import lzma
import re
import sqlite3
import tempfile
import zipfile
import zlib
from contextlib import closing, suppress
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, Literal

import httpx
from pydantic import AwareDatetime, BaseModel, ConfigDict, Field

from mediaguard import schema
from mediaguard.arr.client import BACKUP_ARCHIVE_PATH, ArrError
from mediaguard.context import AppContext
from mediaguard.instances.registry import INSTANCE_ID_RE, Instance, arr_client, list_instances
from mediaguard.maintenance import bundle, preupdate
from mediaguard.maintenance import status as maintenance_status
from mediaguard.maintenance.api import BACKUP_STALE_HOURS, _meta, _sha256
from mediaguard.migration.backupfile import inspect_backup
from mediaguard.migration.wizard import max_backup_bytes
from mediaguard.notify import offsite
from mediaguard.read_observation import instant
from mediaguard.secrets_store import redact

CACHE_SECONDS = 60
FORK_READ_SECONDS = 10
_COVERAGE_MAX_AGE = BACKUP_STALE_HOURS * 3600
_SHA256 = re.compile(r"[a-f0-9]{64}")
_FINGERPRINT = re.compile(r"[a-f0-9]{16}")
_CORE_SCHEMA = re.compile(r"[0-9]+_[a-z0-9_]+")
_ENGINE_FILE = r"[a-zA-Z0-9][a-zA-Z0-9_.-]*\.db"
_BUNDLE_NAME = re.compile(r"mediaguard-bundel-(\d{8}T\d{6}Z)\.mgbundle")
_FORK_NAME = re.compile(
    r"(sonarr|radarr|nzbdrone)_backup_v?[0-9][.0-9_]{0,55}\.zip", re.IGNORECASE
)
_OFFSITE_IDENTITY_SETTINGS = {
    "path": (),
    "webdav": ("webdav_user",),
    "s3": ("s3_endpoint", "s3_region", "s3_access_key"),
}


class Coverage(BaseModel):
    model_config = ConfigDict(extra="forbid")

    status: Literal["healthy", "degraded"] = "degraded"
    verified: bool = False
    created_at: AwareDatetime | None = None
    age_seconds: int | None = None
    reason_code: str | None = "backup_ontbreekt"


class DatabaseCopy(BaseModel):
    model_config = ConfigDict(extra="forbid")

    sha256: str
    schema_versions: list[str]


class CoreCoverage(Coverage):
    sha256: str | None = None
    schema_versions: list[str] | None = None


class EncoderCoverage(Coverage):
    databases: list[DatabaseCopy] = Field(default_factory=list)


class BundleCoverage(Coverage):
    enabled: bool = False
    fingerprint: str | None = None
    key_match: bool | None = None


class OffsiteCoverage(Coverage):
    target_kind: Literal["off", "path", "webdav", "s3", "email"] | None = None
    sha256: str | None = None


class ForkCoverage(Coverage):
    instance_id: str
    name: str | None = None


class RollbackPolicy(BaseModel):
    model_config = ConfigDict(extra="forbid")

    owner: Literal["mediaguard"] = "mediaguard"
    pre_update_restore_mode: Literal["bij-stoppen", "handmatig"]
    staged: bool | None


class BackupReport(BaseModel):
    status: Literal["healthy", "degraded"]
    core: CoreCoverage
    encoder: EncoderCoverage
    bundle: BundleCoverage
    offsite: OffsiteCoverage
    forks: list[ForkCoverage]
    rollback: RollbackPolicy


def _coverage(now: datetime, created: datetime, *, verified: bool,
              max_age: int | None = None) -> dict[str, Any]:
    age = int((now - created).total_seconds())
    reason = None if verified else "verificatie_onbekend"
    if verified and max_age is not None and age > max_age:
        reason = "backup_verouderd"
    return {"status": "degraded" if reason else "healthy", "reason_code": reason,
            "verified": verified, "created_at": created, "age_seconds": age}


def _time(value: object, now: datetime) -> datetime | None:
    stamp = instant(value)
    return stamp if stamp is not None and stamp <= now else None


def _matches(pattern: re.Pattern[str], value: Any) -> bool:
    return isinstance(value, str) and pattern.fullmatch(value) is not None


def _database(path: Path, meta: dict[str, Any], *, encoder: bool = False) -> DatabaseCopy | None:
    digest, size = meta.get("sha256"), meta.get("size")
    if not (_matches(_SHA256, digest) and type(size) is int and size > 0):
        return None
    try:
        if path.is_symlink() or not path.is_file():
            return None
        before = path.stat()
        if before.st_size != size or _sha256(path) != digest:
            return None
        # Gepubliceerde SQLite-back-ups zijn zelfstandige kopieën. Open zonder
        # SQLite-hulpbestanden te maken en lees het schema uit dezelfde kopie.
        with closing(sqlite3.connect(path.resolve().as_uri() + "?immutable=1", uri=True)) as db:
            if encoder:
                values = schema.versions_in(db, "schema_versie", "versie")
                versions = sorted(str(v) for v in values if type(v) is int and v >= 0)
            else:
                values = schema.versions_in(db, "schema_migrations", "version")
                versions = sorted(v for v in values if _matches(_CORE_SCHEMA, v))
        if not versions or path.stat() != before:
            return None
    except (OSError, sqlite3.Error):
        return None
    return DatabaseCopy(sha256=digest, schema_versions=versions)


def _local_databases(ctx: AppContext, now: datetime) -> tuple[CoreCoverage, EncoderCoverage]:
    root = ctx.settings.backup_dir
    candidates = []
    for db in root.glob("mediaguard-*.db"):
        if (".encode-" not in db.name and (meta := _meta(db))
                and (created := _time(meta.get("created_at"), now))):
            candidates.append((created, db, meta))
    with suppress(OSError, ValueError, TypeError, AttributeError):
        for meta in preupdate.list_sets(root / preupdate.FOLDER):
            if created := _time(meta.get("created_at"), now):
                candidates.append((created, root / preupdate.FOLDER / meta["name"], meta))

    core, encoder = CoreCoverage(), EncoderCoverage()
    for created, path, meta in sorted(candidates, key=lambda entry: entry[0], reverse=True):
        files = meta.get("files") if meta.get("kind") == "pre-update" else meta.get("extra_files")
        core_path, core_meta = path, meta
        encoder_dir = path.parent
        pattern = re.escape(path.stem) + r"\.encode-" + _ENGINE_FILE
        if meta.get("kind") == "pre-update":
            if meta.get("legacy") is True or path.is_symlink() or not isinstance(files, list):
                continue
            if not all(isinstance(f, dict) and isinstance(f.get("name"), str)
                       and re.fullmatch(r"mediaguard\.db|encode/" + _ENGINE_FILE, f["name"])
                       for f in files):
                continue
            if (path / "encode").is_symlink() or any((path / f["name"]).is_symlink()
                                                     for f in files):
                continue
            try:
                if preupdate.verify(path, core_dir=ctx.settings.data_dir):
                    continue
            except (OSError, TypeError, ValueError):
                continue
            core_path = path / "mediaguard.db"
            core_meta = next((f for f in files if f["name"] == "mediaguard.db"), {})
            files = [f for f in files if f["name"] != "mediaguard.db"]
            encoder_dir, pattern = path, r"encode/" + _ENGINE_FILE

        # De integriteitscontrole bij het maken en de inhoudshash van nu zijn beide nodig.
        if not core.verified:
            core.reason_code = "verificatie_onbekend"
            if meta.get("integrity_ok") is True and (copy := _database(core_path, core_meta)):
                core = CoreCoverage(**_coverage(now, created, verified=True,
                                                max_age=_COVERAGE_MAX_AGE), **copy.model_dump())
        if not encoder.verified and files:
            encoder.reason_code = "verificatie_onbekend"
            if (meta.get("integrity_ok") is not True or meta.get("encoder_problem")
                    or meta.get("encoder_ok") is False):
                continue
            copies = _encoder_databases(encoder_dir, files, pattern)
            if copies:
                encoder = EncoderCoverage(**_coverage(now, created, verified=True,
                    max_age=_COVERAGE_MAX_AGE), databases=copies)
        if core.verified and encoder.verified:
            break
    return core, encoder


def _encoder_databases(folder: Path, files: Any, pattern: str) -> list[DatabaseCopy]:
    copies = []
    if not isinstance(files, list):
        return copies
    for row in files:
        name = row.get("file", row.get("name")) if isinstance(row, dict) else None
        if not isinstance(name, str) or not re.fullmatch(pattern, name) or "error" in row:
            return []
        if (copy := _database(folder / name, row, encoder=True)) is None:
            return []
        copies.append(copy)
    return copies


def _offsite(ctx: AppContext, now: datetime) -> tuple[OffsiteCoverage, str | None]:
    kind = ctx.config.value("backup.offsite.kind")
    result = OffsiteCoverage(target_kind=kind if kind in offsite.KINDS else None)
    if kind not in _OFFSITE_IDENTITY_SETTINGS:
        result.reason_code = "verificatie_onbekend" if kind == "email" else "doel_onbekend"
        return result, None
    # Het opgeslagen doellabel dekt reeds het pad, de URL of bucket/prefix. Voor
    # de overige doelkenmerken bewijst pas een latere kopie de huidige inrichting.
    settings = {"backup.offsite." + key for key in _OFFSITE_IDENTITY_SETTINGS[kind]}
    changed = [
        instant(row["updated_at"]) for row in ctx.config.overrides("global", "")
        if row["key"] in settings
    ]
    if None in changed:
        result.reason_code = "verificatie_onbekend"
        return result, None
    configured_at = max(changed, default=None)
    rows = ctx.db.fetch_dicts(
        "SELECT bundle_name, sha256, size, created_at FROM offsite_copies"
        " WHERE status='ok' AND target_kind=? AND target=? ORDER BY created_at DESC, id DESC",
        (kind, offsite.target_label(ctx)),
    )
    for row in rows:
        name, digest = row["bundle_name"], row["sha256"]
        created = _time(row["created_at"], now)
        if (_matches(_BUNDLE_NAME, name) and _matches(_SHA256, digest) and created
                and (configured_at is None or created > configured_at)
                and type(row["size"]) is int and row["size"] > 0):
            return OffsiteCoverage(
                **_coverage(now, created, verified=True, max_age=_COVERAGE_MAX_AGE),
                target_kind=kind, sha256=digest,
            ), name
    return result, None


def _bundle_key_match(path: Path, fingerprint: str) -> bool | None:
    with path.open("rb") as handle:
        if handle.read(len(bundle.MAGIC)) != bundle.MAGIC:
            return None
        size = int.from_bytes(handle.read(4), "big")
        if not 0 < size <= 1024 * 1024:
            return None
        header = json.loads(handle.read(size))
        if not isinstance(header, dict) or len(handle.read(17)) < 17:
            return None
    key = json.loads((path.parent / offsite.KEYFILE).read_text("utf-8"))
    if not isinstance(key, dict) or not isinstance(key.get("public"), str):
        return None
    public = base64.b64decode(key["public"], validate=True)
    return (len(public) == 32 and _matches(_FINGERPRINT, header.get("fingerprint"))
            and header["fingerprint"] == fingerprint == hashlib.sha256(public).hexdigest()[:16])


def _bundle(ctx: AppContext, now: datetime, remote: OffsiteCoverage,
            remote_name: str | None) -> BundleCoverage:
    result = BundleCoverage()
    try:
        state = bundle.status(ctx)
        result.enabled = state["enabled"]
        fingerprint = state["fingerprint"]
        if _matches(_FINGERPRINT, fingerprint):
            result.fingerprint = fingerprint
        if not state["bundles"]:
            return result
        name = state["bundles"][0]["file"]
        match = _BUNDLE_NAME.fullmatch(name)
        if match:
            created = datetime.strptime(match[1], "%Y%m%dT%H%M%SZ").replace(tzinfo=UTC)
            if created <= now:
                path = bundle.bundle_dir(ctx) / name
                key_match = None
                if result.enabled and result.fingerprint is not None:
                    with suppress(OSError, ValueError, TypeError):
                        key_match = _bundle_key_match(path, result.fingerprint)
                # Een bijpassende sleutelvingerafdruk alleen bewijst geen intact archief.
                verified = (key_match is True and name == remote_name and remote.verified
                            and remote.created_at is not None and remote.created_at >= created
                            and _sha256(path) == remote.sha256)
                return BundleCoverage(**_coverage(now, created, verified=verified,
                    max_age=_COVERAGE_MAX_AGE), enabled=result.enabled,
                    fingerprint=result.fingerprint, key_match=key_match)
    except (OSError, ValueError, TypeError):
        pass
    result.reason_code = "verificatie_onbekend"
    return result


async def _fork(ctx: AppContext, instance: Instance, now: datetime) -> ForkCoverage:
    iid = instance.id if INSTANCE_ID_RE.fullmatch(instance.id) else "onbekend"
    result = ForkCoverage(instance_id=iid if redact(iid) == iid else "onbekend")
    try:
        async with asyncio.timeout(FORK_READ_SECONDS):
            client = arr_client(ctx, instance.id, timeout=FORK_READ_SECONDS)
            rows = await client.backups()
            if rows == []:
                return result
            result.reason_code = "verificatie_onbekend"
            if not isinstance(rows, list) or not all(isinstance(row, dict) for row in rows):
                return result
            dated = [(created, row) for row in rows
                     if (created := _time(row.get("time"), now)) is not None]
            if len(dated) != len(rows):
                return result
            created, row = max(dated, key=lambda entry: entry[0])
            name, path = row.get("name"), row.get("path")
            format_match = _FORK_NAME.fullmatch(name) if isinstance(name, str) else None
            if not format_match or format_match[1].lower() not in (instance.app, "nzbdrone"):
                name = None
            result = ForkCoverage(**_coverage(now, created, verified=False),
                                  instance_id=result.instance_id, name=name)
            match = BACKUP_ARCHIVE_PATH.fullmatch(path) if isinstance(path, str) else None
            if not name or not match or match[1] != name:
                return result
            limit = max_backup_bytes(ctx)
            size = row.get("size")
            if type(size) is not int or not 0 < size <= limit:
                return result
            work = tempfile.TemporaryDirectory(prefix="mg-fork-backup-")
            try:
                archive = Path(work.name) / "backup.zip"
                with archive.open("wb") as handle:
                    written = await client.read_backup_archive(path, handle, size)
                if written != size:
                    return result
                # De inspectie gebruikt alleen tijdelijke kopieën. Laat de worker
                # zijn bestanden zelf opruimen als de HTTP-aanvraag wordt afgebroken.
                inspection = asyncio.create_task(
                    asyncio.to_thread(_inspect_fork, work, archive, limit, instance.app)
                )
                work = None
            finally:
                if work is not None:
                    work.cleanup()
        if await asyncio.shield(inspection):
            return ForkCoverage(**_coverage(now, created, verified=True),
                                instance_id=result.instance_id, name=name)
    except (ArrError, httpx.HTTPError, OSError, ValueError, KeyError, TypeError, TimeoutError):
        result.reason_code = "verificatie_onbekend"
    return result


def _inspect_fork(work: tempfile.TemporaryDirectory, archive: Path, limit: int, app: str) -> bool:
    try:
        info = inspect_backup(archive, limit * 4)
        return info["ok"] and info["app"] == app and info["has_config_xml"]
    except (OSError, ValueError, sqlite3.Error, zipfile.BadZipFile, RuntimeError,
            EOFError, zlib.error, lzma.LZMAError):
        return False
    finally:
        work.cleanup()


def _rollback(ctx: AppContext) -> RollbackPolicy:
    root = ctx.settings.data_dir
    staged = preupdate.pending(root) is not None
    return RollbackPolicy(
        pre_update_restore_mode="bij-stoppen" if maintenance_status.under_supervisor(ctx)
        else "handmatig",
        staged=None if not staged and (root / preupdate.STAGE_MARKER).exists() else staged,
    )


async def observe(ctx: AppContext, now: datetime) -> BackupReport:
    core, encoder = await asyncio.to_thread(_local_databases, ctx, now)
    remote, remote_name = _offsite(ctx, now)
    local_bundle = await asyncio.to_thread(_bundle, ctx, now, remote, remote_name)
    gate = asyncio.Semaphore(2)

    async def fork(instance: Instance) -> ForkCoverage:
        async with gate:
            return await _fork(ctx, instance, now)

    forks = await asyncio.gather(*(fork(instance) for instance in list_instances(ctx)))
    parts = (core, encoder, local_bundle, remote, *forks)
    healthy = all(part.status == "healthy" for part in parts)
    return BackupReport(status="healthy" if healthy else "degraded", core=core, encoder=encoder,
                        bundle=local_bundle, offsite=remote, forks=forks, rollback=_rollback(ctx))
