"""Handoff-vervanging: verifiëren, in staging klaarzetten en stoppen.

P2 (bindend migratiebesluit): de AV1-worker mag in MediaGuard-modus nooit zelf
een finaal bronpad muteren. Na de bestaande verificatie zet deze module het
resultaat met fsync in ``<staging>/<job_id>/out.mkv``, legt grootte en sha256
vast en zet de job op ``wacht_op_commit``. De recovery-worker van MediaGuard
core stelt eerst een herstelbare bron veilig en meldt daarna
``committed``/``rejected`` terug via de brug.
"""

from __future__ import annotations

import errno
import hashlib
import json
import os
import shutil
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from app.config import Settings
from app.db import Database
from app.jobstatus import STATUS_GECOMMIT, STATUS_GEWEIGERD, STATUS_WACHT_OP_COMMIT
from app.media.beslissen import Besluit
from app.verificatie import VerificationResult
from app.vervanging import ReplacementError, file_sha256

__all__ = [
    "STATUS_GECOMMIT",
    "STATUS_GEWEIGERD",
    "STATUS_WACHT_OP_COMMIT",
    "remove_staging",
    "stage_for_commit",
    "staging_dir",
    "sweep_orphan_staging",
]


def staging_dir(settings: Settings, job_id: int) -> Path:
    return settings.effective_staging_root / str(int(job_id))


def _fsync_directory(path: Path) -> None:
    descriptor = os.open(path, os.O_RDONLY | os.O_DIRECTORY)
    try:
        os.fsync(descriptor)
    finally:
        os.close(descriptor)


class StagingMoveError(OSError):
    """Kopie naar staging klopt niet met de bron (sha256-verschil)."""


def _copy_verified(source: Path, target: Path) -> None:
    """Kopieer + fsync + sha256-vergelijking; bron pas weg na bewezen gelijke kopie."""

    source_hash = hashlib.sha256()
    target_hash = hashlib.sha256()
    try:
        with source.open("rb") as reader, target.open("xb") as writer:
            while chunk := reader.read(1024 * 1024):
                source_hash.update(chunk)
                writer.write(chunk)
            writer.flush()
            os.fsync(writer.fileno())
        with target.open("rb") as check:
            while chunk := check.read(1024 * 1024):
                target_hash.update(chunk)
        if source_hash.hexdigest() != target_hash.hexdigest():
            raise StagingMoveError(
                errno.EIO,
                f"sha256 van stagingkopie wijkt af van cache-resultaat ({source.name})",
            )
    except BaseException:
        target.unlink(missing_ok=True)
        raise
    _fsync_directory(target.parent)
    source.unlink()
    _fsync_directory(source.parent)


def move_to_staging(source: Path, target: Path) -> str:
    """Verplaats het cache-resultaat naar staging; ``rename`` of verifieerde kopie.

    Cache en staging horen op hetzelfde filesystem te liggen (ADR-010), maar aparte
    mounts op hetzelfde device geven toch EXDEV. Dan: kopie + fsync + sha256-controle
    + unlink van de bron, in plaats van de job te laten mislukken.
    Geeft ``"rename"`` of ``"copy"`` terug.
    """

    try:
        os.replace(source, target)
    except OSError as exc:
        if exc.errno != errno.EXDEV:
            raise
        _copy_verified(source, target)
        return "copy"
    with target.open("rb") as handle:
        os.fsync(handle.fileno())
    return "rename"


def source_snapshot(path: Path, probe_info: dict[str, Any] | None) -> dict[str, Any]:
    """Foto van de bron zoals core die nodig heeft om veilig te committen."""

    stat = path.stat()
    return {
        "path": str(path),
        "size": stat.st_size,
        "mtime_ns": stat.st_mtime_ns,
        "inode": stat.st_ino,
        "device": stat.st_dev,
        "nlink": stat.st_nlink,
        "probe": probe_info,
    }


def verification_checks(
    source_probe: dict[str, Any],
    verification: VerificationResult,
    decision: Besluit,
    source_size: int,
    result_size: int,
) -> list[dict[str, Any]]:
    """Maak de controles expliciet zichtbaar voor core/UI.

    verify_result stopt bij de eerste fout; bij een geslaagde verificatie zijn
    dus alle onderstaande controles daadwerkelijk uitgevoerd en geslaagd.
    """

    result = verification.result_probe or {}
    passed = verification.passed
    audio_count = len(result.get("audio") or [])
    subs_count = len(result.get("subs") or [])
    checks = [
        {"name": "readable", "passed": result != {}, "detail": "resultaat met ffprobe gelezen"},
        {
            "name": "duration",
            "passed": passed,
            "detail": (
                f"bron {source_probe.get('duration')} s, resultaat {result.get('duration')} s"
            ),
        },
        {
            "name": "audio_tracks",
            "passed": passed,
            "detail": f"verwacht {len(decision.audio_houden)}, resultaat {audio_count}",
        },
        {
            "name": "subtitle_tracks",
            "passed": passed,
            "detail": f"verwacht {len(decision.subs_houden)}, resultaat {subs_count}",
        },
        {
            "name": "attachments",
            "passed": passed,
            "detail": (
                f"bron {source_probe.get('attachments')}, resultaat {result.get('attachments')}"
            ),
        },
        {
            "name": "size",
            "passed": passed,
            "detail": f"bron {source_size} B, resultaat {result_size} B",
        },
    ]
    if source_probe.get("is_hdr10"):
        checks.append(
            {
                "name": "hdr10_mastering",
                "passed": passed and verification.mastering_equal,
                "detail": str(result.get("mastering_display") or ""),
            }
        )
    if int(source_probe.get("dv_profile") or 0):
        checks.append(
            {
                "name": "dolby_vision",
                "passed": passed,
                "detail": json.dumps(verification.dv or {}, ensure_ascii=False)[:500],
            }
        )
    if source_probe.get("hdr10plus"):
        checks.append(
            {
                "name": "hdr10plus",
                "passed": passed,
                "detail": (
                    json.dumps(verification.hdr10plus, ensure_ascii=False)[:500]
                    if decision.hdr10plus_route == "behouden"
                    else "expliciete HDR10-fallback: dynamische ST 2094-40-metadata vervalt"
                ),
            }
        )
    if not passed:
        checks.append({"name": "verification", "passed": False, "detail": verification.reason})
    return checks


def stage_for_commit(
    database: Database,
    settings: Settings,
    job_id: int,
    source: Path,
    source_stat: os.stat_result,
    result_path: Path,
    source_probe: dict[str, Any],
    verification: VerificationResult,
    decision: Besluit,
) -> str:
    """Zet een geverifieerd resultaat klaar.

    Geeft ``wacht_op_commit`` (gestaged), ``overgeslagen`` (te weinig winst) of
    ``afgebroken`` (annulering tijdens verificatie of klaarzetten) terug.
    """

    current = source.stat()
    if (
        current.st_dev != source_stat.st_dev
        or current.st_ino != source_stat.st_ino
        or current.st_size != source_stat.st_size
        or current.st_mtime_ns != source_stat.st_mtime_ns
    ):
        raise ReplacementError("bron is tijdens de job gewijzigd")
    row = database.fetch_one(
        "SELECT mg_policy,mg_vervangbaar,afbreken_gevraagd FROM job WHERE id=?", (job_id,)
    )
    if row is not None and int(row["afbreken_gevraagd"] or 0):
        # Annuleren tijdens de verificatie: niets klaarzetten.
        _mark_aborted(database, job_id)
        return "afgebroken"
    policy: dict[str, Any] = json.loads(str(row["mg_policy"])) if row and row["mg_policy"] else {}
    result_size = result_path.stat().st_size
    saving = 100.0 * (1 - result_size / max(source_stat.st_size, 1))
    minimum = float(policy.get("min_saving_percent") or 0)
    checks = verification_checks(
        source_probe, verification, decision, source_stat.st_size, result_size
    )
    smaller = result_size < source_stat.st_size
    checks.append(
        {
            "name": "min_saving",
            "passed": saving >= minimum,
            "detail": f"besparing {_pct(saving)} (minimum {_pct(minimum)})",
        }
    )
    checks.append(
        {
            # Ook met minimum 0 %: een resultaat dat niet kleiner is, vervangt nooit.
            "name": "kleiner",
            "passed": smaller,
            "detail": f"resultaat {result_size} bytes, bron {source_stat.st_size} bytes",
        }
    )
    report: dict[str, Any] = {
        "passed": verification.passed and saving >= minimum and smaller,
        "checks": checks,
        "dv": verification.dv,
        "hdr": {
            "is_hdr10": bool((verification.result_probe or {}).get("is_hdr10")),
            "mastering_equal": verification.mastering_equal,
            "kleur": (verification.result_probe or {}).get("kleur"),
            "hdr10plus": verification.hdr10plus,
            "hdr10plus_route": decision.hdr10plus_route or None,
        },
        "saving_percent": round(saving, 2),
    }
    now = datetime.now(UTC).isoformat()
    if saving < minimum:
        # F2.7 / P4.7: te weinig winst = origineel behouden. Geen fout.
        database.execute(
            """
            UPDATE job SET status='overgeslagen',fout=?,foutsoort='GeenBesparing',
                           mg_verificatie=?,klaar_op=?,proces_pid=NULL,resultaat_pad=NULL
            WHERE id=?
            """,
            (
                f"besparing {_pct(saving)} onder minimum {_pct(minimum)}; origineel behouden",
                json.dumps(report, ensure_ascii=False),
                now,
                job_id,
            ),
        )
        return "overgeslagen"

    target_dir = staging_dir(settings, job_id)
    if target_dir.exists():
        # Alleen onze eigen numerieke jobmap van een eerdere poging.
        shutil.rmtree(target_dir)
    target_dir.mkdir(parents=True)
    partial = target_dir / "out.mkv.deel"
    final = target_dir / "out.mkv"
    try:
        move_to_staging(result_path, partial)
        os.replace(partial, final)
        _fsync_directory(target_dir)
    except OSError:
        shutil.rmtree(target_dir, ignore_errors=True)
        raise
    digest = file_sha256(final)
    replace_eligible = bool(row and int(row["mg_vervangbaar"])) and not decision.apart_resultaat
    snapshot = {
        "path": str(source),
        "size": source_stat.st_size,
        "mtime_ns": source_stat.st_mtime_ns,
        "inode": source_stat.st_ino,
        "device": source_stat.st_dev,
        "nlink": source_stat.st_nlink,
        "probe": source_probe,
    }
    # Alleen klaarzetten als niemand intussen annuleerde (cancel zet tijdens
    # 'verifieren' alleen afbreken_gevraagd); anders blijft er geen wees achter.
    staged = database.execute(
        """
        UPDATE job SET status=?,percent=100,klaar_op=?,proces_pid=NULL,
                       resultaat_pad=?,resultaat_grootte=?,
                       mg_staged_pad=?,mg_staged_grootte=?,mg_staged_sha256=?,
                       mg_snapshot=?,mg_verificatie=?,mg_vervangbaar=?
        WHERE id=? AND afbreken_gevraagd=0 AND status IN ('bezig','verifieren')
        """,
        (
            STATUS_WACHT_OP_COMMIT,
            now,
            str(final),
            final.stat().st_size,
            str(final),
            final.stat().st_size,
            digest,
            json.dumps(snapshot, ensure_ascii=False),
            json.dumps(report, ensure_ascii=False),
            int(replace_eligible),
            job_id,
        ),
    )
    if staged != 1:
        shutil.rmtree(target_dir, ignore_errors=True)
        _mark_aborted(database, job_id)
        return "afgebroken"
    return STATUS_WACHT_OP_COMMIT


def _mark_aborted(database: Database, job_id: int) -> None:
    database.execute(
        "UPDATE job SET status='afgebroken',fout='door gebruiker afgebroken',klaar_op=?,"
        "proces_pid=NULL WHERE id=? AND status IN ('bezig','verifieren')",
        (datetime.now(UTC).isoformat(), job_id),
    )


def remove_staging(settings: Settings, job_id: int) -> bool:
    """Verwijder uitsluitend de numerieke stagingmap van deze job."""

    target = staging_dir(settings, job_id)
    root = settings.effective_staging_root.resolve()
    if not target.exists():
        return False
    resolved = target.resolve(strict=True)
    if resolved.parent != root or resolved.is_symlink():
        raise ReplacementError("stagingmap ligt buiten de beheerde stagingroot")
    shutil.rmtree(resolved)
    return True


def sweep_orphan_staging(database: Database, settings: Settings) -> list[int]:
    """Verwijder numerieke stagingmappen van jobs die niet (meer) op core wachten.

    Een crash tussen ``move_to_staging`` en de statusupdate laat anders een
    volledig resultaat achter dat niemand ooit opruimt. Alleen mappen met een
    puur numerieke naam (onze job-id's) worden aangeraakt.
    """

    root = settings.effective_staging_root
    if not root.is_dir():
        return []
    awaiting = {
        int(row["id"])
        for row in database.fetch_all(
            "SELECT id FROM job WHERE status=?", (STATUS_WACHT_OP_COMMIT,)
        )
    }
    removed: list[int] = []
    for entry in root.iterdir():
        if not entry.name.isdigit() or entry.is_symlink() or not entry.is_dir():
            continue
        job_id = int(entry.name)
        if job_id in awaiting:
            continue
        row = database.fetch_one(
            "SELECT status FROM job WHERE id=?", (job_id,)
        )
        # Een job die nu klaarzet ('verifieren') laten we met rust.
        if row is not None and str(row["status"]) in {"bezig", "verifieren"}:
            continue
        if remove_staging(settings, job_id):
            removed.append(job_id)
    return removed


def _pct(value: float) -> str:
    """Nederlandse notatie: 0,1 %."""
    return f"{value:.1f}".replace(".", ",") + " %"
