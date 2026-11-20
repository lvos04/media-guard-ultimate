"""Herstelmetadata uit bestaande back-ups. Schrijft geen back-up of herstelopdracht."""

from __future__ import annotations

import asyncio
import lzma
import re
import sqlite3
import tempfile
import zipfile
import zlib
from contextlib import suppress
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, Literal

import httpx
from pydantic import AwareDatetime, BaseModel, ConfigDict, Field

from mediaguard.arr.client import BACKUP_ARCHIVE_PATH, ArrError
from mediaguard.context import AppContext
from mediaguard.instances.registry import INSTANCE_ID_RE, Instance, arr_client, list_instances
from mediaguard.maintenance import bundle, preupdate
from mediaguard.maintenance import status as maintenance_status
from mediaguard.maintenance.api import BACKUP_STALE_HOURS, _meta
from mediaguard.migration.backupfile import inspect_backup
from mediaguard.migration.wizard import max_backup_bytes
from mediaguard.notify import offsite
from mediaguard.read_observation import instant
from mediaguard.secrets_store import redact

CACHE_SECONDS = 60
FORK_READ_SECONDS = 10
_COVERAGE_MAX_AGE = BACKUP_STALE_HOURS * 3600
_FINGERPRINT = re.compile(r"[a-f0-9]{16}")
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


def _database_proof(core: Path, path: Path, meta: dict, is_set: bool) -> dict[str, DatabaseCopy]:
    if not is_set:
        regular = preupdate.trial_regular(core, path, meta, independent_encoder=True)
        return {rel: DatabaseCopy(sha256=digest, schema_versions=versions)
                for rel, (digest, versions) in regular.items()}
    entries, _, _, versions = preupdate.trial_set(core, path)
    copies = {}
    for live, (_, _, digest) in entries.items():
        rel = live if live.startswith("encode/") else "core/mediaguard.db"
        copies[rel] = DatabaseCopy(sha256=digest, schema_versions=versions[rel])
    return copies


def _local_databases(ctx: AppContext, now: datetime) -> tuple[CoreCoverage, EncoderCoverage]:
    root = ctx.settings.backup_dir
    candidates = []
    for db in root.glob("mediaguard-*.db"):
        if (".encode-" not in db.name and (meta := _meta(db))
                and (created := _time(meta.get("created_at"), now))):
            candidates.append((created, db, meta, False))
    with suppress(OSError, ValueError, TypeError, AttributeError):
        for meta in preupdate.list_sets(root / preupdate.FOLDER):
            if not meta.get("legacy") and (created := _time(meta.get("created_at"), now)):
                candidates.append((created, root / preupdate.FOLDER / meta["name"], meta, True))

    core, encoder = CoreCoverage(), EncoderCoverage()
    for created, path, meta, is_set in sorted(candidates, key=lambda entry: entry[0], reverse=True):
        if core.verified and encoder.verified:
            break
        if not core.verified:
            core.reason_code = "verificatie_onbekend"
        if not encoder.verified:
            encoder.reason_code = "verificatie_onbekend"
        if meta.get("integrity_ok") is not True or path.is_symlink():
            continue
        try:
            copies = _database_proof(ctx.settings.data_dir, path, meta, is_set)
        except (OSError, sqlite3.Error, RuntimeError, preupdate.SetError, ValueError, TypeError):
            continue
        coverage = _coverage(now, created, verified=True, max_age=_COVERAGE_MAX_AGE)
        core_copy = copies.pop("core/mediaguard.db", None)
        if not core.verified and core_copy and core_copy.schema_versions:
            core = CoreCoverage(**coverage, **core_copy.model_dump())
        if not encoder.verified and copies and all(c.schema_versions for c in copies.values()):
            encoder = EncoderCoverage(**coverage, databases=list(copies.values()))
    return core, encoder


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
