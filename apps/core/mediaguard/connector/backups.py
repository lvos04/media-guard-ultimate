"""Herstelmetadata uit bestaande back-ups. Schrijft geen back-up of herstelopdracht."""

from __future__ import annotations

import asyncio
import lzma
import os
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
from mediaguard.db import check_schema_versions
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
_ENGINE_FILE = r"[a-zA-Z0-9][a-zA-Z0-9_.-]*\.db"
_FORK_NAME = re.compile(
    r"(sonarr|radarr|nzbdrone)_backup_v?[0-9][.0-9_]{0,55}\.zip", re.IGNORECASE
)


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
    pre_update_restore_mode: Literal["bij-stoppen", "handmatig", "wacht op handmatige keuze"]
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


def _database(path: Path, meta: dict[str, Any], *, encoder: bool = False,
              primary: bool = False) -> DatabaseCopy | None:
    digest, size = meta.get("sha256"), meta.get("size")
    if not (_matches(_SHA256, digest) and type(size) is int and size > 0):
        return None
    try:
        if path.is_symlink() or not path.is_file():
            return None
        before = path.stat()
        if (before.st_size != size or _sha256(path) != digest
                or any(os.path.lexists(path.with_name(path.name + suffix))
                       for suffix in ("-wal", "-journal"))):
            return None
        # Gepubliceerde SQLite-back-ups zijn zelfstandige kopieën. Open zonder
        # SQLite-hulpbestanden te maken en lees het schema uit dezelfde kopie.
        with closing(sqlite3.connect(path.resolve().as_uri() + "?immutable=1", uri=True)) as db:
            if encoder:
                values = preupdate.encoder_copy_versions(
                    db, preupdate.encoder_migrations(os.environ), primary=primary,
                )
                versions = [str(v) for v in sorted(values)]
            else:
                check_schema_versions(path, connection=db)
                versions = sorted(schema.versions_in(db, "schema_migrations", "version"))
        if not versions or path.stat() != before:
            return None
    except (OSError, sqlite3.Error, RuntimeError, preupdate.SetError):
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
    primary = preupdate.primary_encoder_db(preupdate.encoder_backup_dir(ctx.settings.data_dir))
    for created, path, meta in sorted(candidates, key=lambda entry: entry[0], reverse=True):
        files = meta.get("files") if meta.get("kind") == "pre-update" else meta.get("extra_files")
        core_path, core_meta = path, meta
        encoder_dir = path.parent
        pattern = re.escape(path.stem) + r"\.encode-" + _ENGINE_FILE
        primary_name = f"{path.stem}.encode-{primary.name}" if primary is not None else None
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
            primary_name = f"encode/{primary.name}" if primary is not None else None

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
            copies = _encoder_databases(encoder_dir, files, pattern, primary_name)
            if copies:
                encoder = EncoderCoverage(**_coverage(now, created, verified=True,
                    max_age=_COVERAGE_MAX_AGE), databases=copies)
        if core.verified and encoder.verified:
            break
    return core, encoder


def _encoder_databases(folder: Path, files: Any, pattern: str,
                       primary: str | None) -> list[DatabaseCopy]:
    copies = []
    if not isinstance(files, list) or primary is None:
        return copies
    seen = set()
    for row in files:
        name = row.get("file", row.get("name")) if isinstance(row, dict) else None
        if (not isinstance(name, str) or not re.fullmatch(pattern, name)
                or "error" in row or name in seen):
            return []
        if (copy := _database(folder / name, row, encoder=True, primary=name == primary)) is None:
            return []
        copies.append(copy)
        seen.add(name)
    return copies if primary in seen else []


async def _offsite(ctx: AppContext, now: datetime) -> tuple[OffsiteCoverage, dict | None]:
    kind = ctx.config.value("backup.offsite.kind")
    result = OffsiteCoverage(target_kind=kind if kind in offsite.KINDS else None)
    if kind not in offsite.READBACK_KINDS:
        result.reason_code = "verificatie_onbekend" if kind == "email" else "doel_onbekend"
        return result, None
    row = await offsite.verified_copy(ctx, kind)
    if row is not None and (created := _time(row["created_at"], now)):
        return OffsiteCoverage(
            **_coverage(now, created, verified=True, max_age=_COVERAGE_MAX_AGE),
            target_kind=kind, sha256=row["sha256"],
        ), row
    return result, None


def _bundle(ctx: AppContext, now: datetime, remote: OffsiteCoverage,
            row: dict | None) -> BundleCoverage:
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
        match = offsite.BUNDLE_NAME.fullmatch(name)
        if match:
            created = datetime.strptime(match[1], "%Y%m%dT%H%M%SZ").replace(tzinfo=UTC)
            if created <= now:
                path = bundle.bundle_dir(ctx) / name
                key_match, proof = None, None
                if result.enabled and result.fingerprint:
                    key_match, proof = bundle.verify_pair(path, path.parent / bundle.KEYFILE)
                verified = (key_match is True and proof is not None and row is not None
                            and result.fingerprint == proof["fingerprint"]
                            and name == row["bundle_name"] and remote.verified
                            and remote.created_at is not None and remote.created_at >= created
                            and proof["bundle"] == remote.sha256
                            and proof["key"] == row["key_sha256"])
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


def rollback_request(core: Path) -> tuple[bool, bool]:
    """Aanwezigheid en historische keuze bepalen wanneer de Hub een nieuwe meting nodig heeft."""
    staged = (core / preupdate.STAGE_MARKER).is_file()
    try:
        return staged, staged and preupdate.manual_choice_pending(core)
    except (OSError, preupdate.SetError):
        return staged, preupdate.pending(core) is not None


def rollback_policy(ctx: AppContext) -> RollbackPolicy:
    core = ctx.settings.data_dir
    _, manual = rollback_request(core)
    if manual:
        return RollbackPolicy(pre_update_restore_mode="wacht op handmatige keuze",
                              staged=preupdate.staged_status(core, manual=True))
    return RollbackPolicy(
        pre_update_restore_mode="bij-stoppen" if maintenance_status.under_supervisor(ctx)
        else "handmatig",
        staged=preupdate.staged_status(ctx.settings.data_dir),
    )


async def observe(ctx: AppContext, now: datetime) -> BackupReport:
    core, encoder = await asyncio.to_thread(_local_databases, ctx, now)
    remote, row = await _offsite(ctx, now)
    local_bundle = await asyncio.to_thread(_bundle, ctx, now, remote, row)
    rollback = await asyncio.to_thread(rollback_policy, ctx)
    gate = asyncio.Semaphore(2)

    async def fork(instance: Instance) -> ForkCoverage:
        async with gate:
            return await _fork(ctx, instance, now)

    forks = await asyncio.gather(*(fork(instance) for instance in list_instances(ctx)))
    parts = (core, encoder, local_bundle, remote, *forks)
    healthy = all(part.status == "healthy" for part in parts) and (
        rollback.staged is not None and rollback.pre_update_restore_mode != preupdate.MANUAL_CHOICE)
    return BackupReport(status="healthy" if healthy else "degraded", core=core, encoder=encoder,
                        bundle=local_bundle, offsite=remote, forks=forks, rollback=rollback)
