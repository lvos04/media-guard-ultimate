"""Brugservice: plan en jobs voor core, bovenop de bestaande wachtrij.

Een MediaGuard-job is een gewone, goedgekeurde job in de bestaande SQLite-
wachtrij. De bestaande planner (CPU/GPU-banen, totale encodegrens,
cachereservering, pauzes) voert hem uit met de bestaande uitvoerder; alleen de
laatste stap is anders (handoff i.p.v. vervanging). Zo bestaat er één encoder
en één writerbeleid, niet twee concurrerende.
"""

from __future__ import annotations

import json
import os
import sqlite3
import time
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from app.config import Settings
from app.db import Database
from app.jobstatus import ACTIVE_STATUSES
from app.media.beslissen import COVER_ART_WARNING, STANDAARD, Besluit, beslis, dv_route_van
from app.media.dv_controle import dovi_rpu_summary
from app.media.probe import probe
from app.mediaguard.handoff import (
    STATUS_GECOMMIT,
    STATUS_GEWEIGERD,
    STATUS_WACHT_OP_COMMIT,
    remove_staging,
)
from app.mediaguard.inventory import stream_inventory
from app.mediaguard.policy import (
    MediaGuardPolicy,
    effective_inst,
    normalized_probe,
    replace_eligible,
    thresholds_skipped,
    to_profile_inst,
)

MG_PROFILE_NAME = "MediaGuard (per bestand)"
MG_PROFILE_OLD_NAMES = ("MediaGuard (per-job policy)",)
# Wat MediaGuard met encoder 'auto' per bestand doorzet (mediaguard/policy.py): het profiel
# toont dezelfde waarden, zodat het scherm niet "GPU voor 4K/HDR10: uit" zegt terwijl het
# per bestand wel via de GPU gaat (rapport 2, N6/N7).
MG_PROFILE_GPU = {"gpu_voor_sdr_1080": True, "gpu_voor_sdr_4k": True, "gpu_voor_hdr10": True}
# Vrije tekst van core (reden/notitie) wordt server-side ingekort, niet geweigerd.
REASON_MAX = 1000
ACTIVE = ACTIVE_STATUSES

# Legacy-status -> contractstatus (packages/contracts/encoder-bridge.md)
STATUS_MAP = {
    "wacht": "queued",
    "bezig": "running",
    "verifieren": "verifying",
    STATUS_WACHT_OP_COMMIT: "awaiting_commit",
    STATUS_GECOMMIT: "committed",
    STATUS_GEWEIGERD: "rejected",
    "mislukt": "failed",
    "overgeslagen": "skipped",
    "afgebroken": "cancelled",
    # Alleen in legacy direct-modus: bron is al door de encoder vervangen.
    "klaar": "committed",
}


class BridgeError(RuntimeError):
    def __init__(self, status_code: int, message: str) -> None:
        super().__init__(message)
        self.status_code = status_code


def resolve_media_path(settings: Settings, raw: str, *, allow_staging: bool = False) -> Path:
    """Alleen bestaande bestanden binnen een geconfigureerde mediaroot.

    ``allow_staging`` (alleen voor /probe): core mag ook een gestaged resultaat
    teruglezen om vóór de commit zelf metadata te vergelijken.
    """

    candidate = Path(raw)
    if not candidate.is_absolute():
        raise BridgeError(422, "pad moet absoluut zijn")
    try:
        resolved = candidate.resolve(strict=True)
    except OSError as exc:
        raise BridgeError(404, f"bestand niet gevonden: {raw}") from exc
    if not resolved.is_file():
        raise BridgeError(422, "pad is geen bestand")
    roots = list(settings.media_roots)
    if allow_staging:
        roots.append(settings.effective_staging_root)
    for root in roots:
        try:
            root_resolved = root.resolve(strict=True)
        except OSError:
            continue
        if resolved.is_relative_to(root_resolved):
            return resolved
    raise BridgeError(403, "pad ligt buiten de toegestane mediaroots")


def _media_root_for(settings: Settings, path: Path) -> Path:
    for root in settings.media_roots:
        try:
            root_resolved = root.resolve(strict=True)
        except OSError:
            continue
        if path.is_relative_to(root_resolved):
            return root_resolved
    raise BridgeError(403, "pad ligt buiten de toegestane mediaroots")


def enriched_probe(path: Path) -> dict[str, Any]:
    info = probe(str(path))
    if info is None:
        raise BridgeError(422, "bestand is niet leesbaar met ffprobe")
    if int(info.get("dv_profile") or 0) == 7:
        # MEL/FEL bepaalt de route; onbekend blijft None (beslissen: FEL).
        summary = dovi_rpu_summary(path)
        info["dv_el_type"] = summary.get("el_type")
        info["dv_rpu"] = summary
    return info


def probe_report(settings: Settings, raw: str) -> dict[str, Any]:
    path = resolve_media_path(settings, raw, allow_staging=True)
    info = enriched_probe(path)
    stat = path.stat()
    el_type = str(info.get("dv_el_type") or "").lower() or None
    return {
        "path": str(path),
        "probe": info,
        "dv_route": dv_route_van(info) if info.get("dv_profile") else None,
        # Alleen bij profiel 7: "mel"/"fel" volgens dovi_tool; None = onbekend
        # (beslissen behandelt onbekend conservatief als FEL).
        "dv_el_type": el_type if el_type in {"mel", "fel"} else None,
        "inventory": stream_inventory(str(path)),
        "stat": {
            "size": stat.st_size,
            "mtime_ns": stat.st_mtime_ns,
            "inode": stat.st_ino,
            "device": stat.st_dev,
            "nlink": stat.st_nlink,
        },
    }


def build_plan(
    settings: Settings,
    capabilities: dict[str, Any],
    path: Path,
    policy: MediaGuardPolicy,
    *,
    trial_only: bool = False,
    info: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """De beslissing van beslissen.py met reden, zonder iets uit te voeren."""

    info = info or enriched_probe(path)
    from app import routeleer

    gpu_ok = bool(capabilities["lanes"]["gpu"]["enabled"])
    # Zelflerende route (gebruikersrapport: anime half afgekeurd op de GPU): een reeks waar de
    # GPU de VMAF-doelwaarde al twee keer niet haalde, gaat direct via de CPU.
    gpu_block = routeleer.gpu_block_reason(str(path)) if gpu_ok else None
    overrides = to_profile_inst(
        policy, gpu_lane_available=gpu_ok and gpu_block is None,
        skip_thresholds=thresholds_skipped(policy, container_mode=settings.container_mode))
    normalized = normalized_probe(info)
    decision = beslis(normalized, effective_inst(overrides))
    if gpu_block and decision.actie == "hercoderen" and decision.baan == "cpu":
        decision = decision.model_copy(update={"reden": f"{decision.reden} · {gpu_block}"[:500]})
    no_gain = routeleer.no_gain_reason(normalized) if decision.actie == "hercoderen" else None
    if no_gain:
        decision = Besluit(actie="overslaan", reden=no_gain,
                           audio_houden=decision.audio_houden, subs_houden=decision.subs_houden,
                           attachments_houden=decision.attachments_houden)
    warnings = list(decision.waarschuwingen)
    route = dv_route_van(info) if info.get("dv_profile") else None
    if decision.dolby_vision:
        routes = {item["route"]: item for item in capabilities["dv_routes"]}
        route_info = routes.get(decision.dv_route or str(route))
        if route_info is None or not route_info["technically_possible"]:
            reason = (route_info or {}).get("reason") or "route niet ondersteund"
            decision = Besluit(
                actie="overslaan",
                reden=f"DV-route {decision.dv_route or route} technisch niet beschikbaar: {reason}",
                audio_houden=decision.audio_houden,
                subs_houden=decision.subs_houden,
                attachments_houden=decision.attachments_houden,
            )
    if decision.hdr10plus_route == "behouden" and not capabilities.get("hdr10plus_route_verified"):
        decision = Besluit(
            actie="overslaan",
            reden="HDR10+-route niet beschikbaar: "
            + str(capabilities.get("hdr10plus_route_reason") or "onbekend"),
            audio_houden=decision.audio_houden,
            subs_houden=decision.subs_houden,
            attachments_houden=decision.attachments_houden,
        )
    if decision.actie != "overslaan" and decision.baan == "gpu" and not gpu_ok:
        decision = Besluit(
            actie="overslaan",
            reden=f"GPU-baan niet beschikbaar: {capabilities['lanes']['gpu']['reason']}",
        )
    if decision.actie != "overslaan" and not capabilities["lanes"]["cpu"]["enabled"]:
        decision = Besluit(
            actie="overslaan",
            reden=f"CPU-baan niet beschikbaar: {capabilities['lanes']['cpu']['reason']}",
        )
    eligible = replace_eligible(policy, trial_only) and not decision.apart_resultaat
    metadata_loss = metadata_loss_of(decision)
    if int(info.get("dv_profile") or 0) and decision.actie != "overslaan" and not eligible:
        warnings.append("resultaat is een aparte kopie; core mag de bron hiermee niet vervangen")
    if int(info.get("dv_profile") or 0) and policy.dv_mode == "keep_original":
        warnings.append("Dolby Vision: origineel behouden (dv_mode keep_original)")
    return {
        "action": decision.actie,
        "reason": decision.reden,
        # Nederlandse aliassen zoals core (B2) ze leest; zelfde waarden.
        "actie": decision.actie,
        "reden": decision.reden,
        "approved_for_execution": decision.actie in {"hercoderen", "opschonen"},
        "lane": decision.baan or None,
        "encoder": decision.encoder or None,
        "replace_eligible": eligible,
        "warnings": warnings,
        "decision": decision.model_dump(),
        "dv": {
            "source_profile": info.get("dv_profile") or None,
            "bl_compat": info.get("dv_compat") or None,
            "el_type": info.get("dv_el_type"),
            "route": route,
            "mode": policy.dv_mode,
            "allowed_profiles": list(policy.dv_allowed_profiles),
            "executes": decision.dolby_vision,
            "fel_separate_output": decision.apart_resultaat,
        },
        "hdr": {
            "is_hdr10": bool(info.get("is_hdr10")),
            "hdr10plus": bool(info.get("hdr10plus")),
            "hdr10plus_mode": policy.effective_hdr10plus_mode,
            "hdr10plus_route": decision.hdr10plus_route or None,
            "hdr10plus_route_verified": bool(capabilities.get("hdr10plus_route_verified")),
        },
        # Expliciet benoemd metadataverlies (nooit stil): leeg = niets bewust weg.
        "metadata_loss": metadata_loss,
        "profile_overrides": overrides,
        "effective_settings": {
            key: effective_inst(overrides)[key]
            for key in ("crf_2160", "crf_1080", "crf_720", "preset_2160", "preset_1080")
        },
    }


def metadata_loss_of(decision: Besluit) -> list[str]:
    loss = []
    if decision.actie == "hercoderen" and decision.hdr10plus_route == "hdr10_fallback":
        loss.append("hdr10plus_dynamic_metadata")
    if COVER_ART_WARNING in decision.waarschuwingen:
        loss.append("cover_art")
    return loss


def _profile_id(database: Database) -> int:
    names = (MG_PROFILE_NAME, *MG_PROFILE_OLD_NAMES)
    row = database.fetch_one(
        f"SELECT id,naam,inst FROM profiel WHERE naam IN ({','.join('?' for _ in names)}) "
        "ORDER BY naam=? DESC LIMIT 1", (*names, MG_PROFILE_NAME))
    if row is not None:
        try:
            inst = json.loads(str(row["inst"] or "{}"))
        except ValueError:
            inst = {}
        fixed = {**STANDAARD, **inst, **MG_PROFILE_GPU}
        if row["naam"] != MG_PROFILE_NAME or fixed != inst:
            database.execute("UPDATE profiel SET naam=?,inst=? WHERE id=?",
                             (MG_PROFILE_NAME, json.dumps(fixed, ensure_ascii=False),
                              int(row["id"])))
        return int(row["id"])
    return database.execute_insert(
        "INSERT INTO profiel(naam,inst) VALUES (?,?)",
        (MG_PROFILE_NAME, json.dumps({**STANDAARD, **MG_PROFILE_GPU}, ensure_ascii=False)),
    )


def _library_for(database: Database, settings: Settings, path: Path) -> int:
    """Hoogste map binnen de mediaroot op hetzelfde filesystem als het bestand.

    De bestaande uitvoerder controleert dat bron en library op hetzelfde device
    staan; geneste TrueNAS-datasets krijgen daardoor ieder een eigen rij.
    """

    root = _media_root_for(settings, path)
    device = path.stat().st_dev
    library_path = path.parent
    while library_path != root and library_path.parent.stat().st_dev == device:
        if not library_path.parent.is_relative_to(root):
            break
        library_path = library_path.parent
    row = database.fetch_one("SELECT id,actief FROM library WHERE pad=?", (str(library_path),))
    if row is not None:
        if not int(row["actief"]):
            raise BridgeError(409, f"bibliotheek {library_path} is in de encoder uitgeschakeld")
        return int(row["id"])
    # scan_interval=0: de scheduler scant deze rij nooit; core is de scanner.
    return database.execute_insert(
        """
        INSERT INTO library(naam,pad,profiel_id,scan_interval,verwacht_device,canary_status,
                            bron_type,bron_uri)
        VALUES (?,?,?,0,?,'vrijgegeven','lokaal',?)
        """,
        (
            f"MediaGuard {library_path}",
            str(library_path),
            _profile_id(database),
            device,
            str(library_path),
        ),
    )


def _upsert_file(database: Database, library_id: int, path: Path, info: dict[str, Any]) -> int:
    stat = path.stat()
    now = datetime.now(UTC).isoformat()
    row = database.fetch_one("SELECT id FROM bestand WHERE pad=?", (str(path),))
    if row is None:
        return database.execute_insert(
            """
            INSERT INTO bestand(library_id,pad,grootte,mtime,nlink,categorie,probe,gescand_op,
                                device,inode,aanwezig,eerste_gezien,stabiel_vanaf,scan_status)
            VALUES (?,?,?,?,?,'ok',?,?,?,?,1,?,?,'gereed')
            """,
            (
                library_id,
                str(path),
                stat.st_size,
                stat.st_mtime,
                stat.st_nlink,
                json.dumps(info, ensure_ascii=False),
                now,
                stat.st_dev,
                stat.st_ino,
                now,
                now,
            ),
        )
    database.execute(
        """
        UPDATE bestand SET grootte=?,mtime=?,nlink=?,probe=?,gescand_op=?,device=?,inode=?,
                           aanwezig=1,scan_status='gereed'
        WHERE id=?
        """,
        (
            stat.st_size,
            stat.st_mtime,
            stat.st_nlink,
            json.dumps(info, ensure_ascii=False),
            now,
            stat.st_dev,
            stat.st_ino,
            int(row["id"]),
        ),
    )
    return int(row["id"])


def job_for_correlation(database: Database, correlation_id: str, path: Path) -> int | None:
    row = database.fetch_one(
        "SELECT j.id FROM job j JOIN bestand f ON f.id=j.bestand_id "
        "WHERE j.mg_bron='mediaguard' AND j.mg_correlation_id=? AND f.pad=? "
        "ORDER BY j.id DESC LIMIT 1",
        (correlation_id, str(path)),
    )
    return int(row["id"]) if row is not None else None


def create_job(
    database: Database,
    settings: Settings,
    capabilities: dict[str, Any],
    *,
    source_path: str,
    policy: MediaGuardPolicy,
    correlation_id: str | None,
    media_ref: dict[str, Any] | None,
    trial_only: bool,
    avoid_lanes: set[str] | None = None,
) -> int:
    path = resolve_media_path(settings, source_path)
    if correlation_id:
        # Idempotent: een herhaald verzoek (time-out, herstart van core) met
        # dezelfde correlation_id voor hetzelfde bestand levert dezelfde job op,
        # nooit een tweede. Een ander bestand met een hergebruikte id (bijv. een
        # nieuwe core-database) is gewoon een nieuwe job.
        previous = job_for_correlation(database, correlation_id, path)
        if previous is not None:
            return previous
    if not capabilities["mounts"]["staging"]["writable"]:
        raise BridgeError(503, "stagingmap is niet schrijfbaar; encodes blijven gestopt")
    minimum_age = float(os.environ.get("MG_MIN_STABLE_SECONDS", "60"))
    if time.time() - path.stat().st_mtime < minimum_age:
        raise BridgeError(409, "bestand is net gewijzigd; wacht tot import/kopie klaar is")
    existing = database.fetch_one(
        f"""
        SELECT j.id,j.status FROM job j JOIN bestand f ON f.id=j.bestand_id
        WHERE f.pad=? AND j.status IN ({",".join("?" for _ in ACTIVE)})
        ORDER BY j.id LIMIT 1
        """,
        (str(path), *ACTIVE),
    )
    if existing is not None:
        raise BridgeError(
            409,
            f"job {int(existing['id'])} bewaakt dit bestand al "
            f"({STATUS_MAP.get(str(existing['status']), existing['status'])})",
        )
    info = enriched_probe(path)
    plan = build_plan(settings, capabilities, path, policy, trial_only=trial_only, info=info)
    decision = Besluit.model_validate(plan["decision"])
    lane = decision.baan or "cpu"
    if plan["approved_for_execution"] and avoid_lanes and lane in avoid_lanes:
        # Vóór er iets wordt vastgelegd: MediaGuard wil nu geen werk voor deze baan.
        raise BridgeError(409, f"LANE_FULL: {lane}-baan zit vol bij MediaGuard; "
                               "bestand later opnieuw aanbieden")
    library_id = _library_for(database, settings, path)
    file_id = _upsert_file(database, library_id, path, info)
    now = datetime.now(UTC).isoformat()
    decision_id = database.execute_insert(
        """
        INSERT INTO besluit(bestand_id,actie,baan,encoder,reden,payload,status,door_mens,
                            gemaakt_op,goedkeuringsbron)
        VALUES (?,?,?,?,?,?,?,0,?,'mediaguard')
        """,
        (
            file_id,
            decision.actie,
            decision.baan or None,
            decision.encoder or None,
            decision.reden,
            decision.model_dump_json(),
            "goedgekeurd" if plan["approved_for_execution"] else "overgeslagen",
            now,
        ),
    )
    executable = plan["approved_for_execution"]
    try:
        job_id = database.execute_insert(
            """
            INSERT INTO job(besluit_id,bestand_id,baan,status,prioriteit,fout,klaar_op,
                            mg_bron,mg_correlation_id,mg_media_ref,mg_policy,mg_inst,
                            mg_vervangbaar,mg_waarschuwingen)
            VALUES (?,?,?,?,0,?,?,'mediaguard',?,?,?,?,?,?)
            """,
            (
                decision_id,
                file_id,
                decision.baan or "cpu",
                "wacht" if executable else "overgeslagen",
                None if executable else decision.reden,
                None if executable else now,
                correlation_id,
                json.dumps(media_ref or {}, ensure_ascii=False),
                policy.model_dump_json(),
                json.dumps(plan["profile_overrides"], ensure_ascii=False),
                int(plan["replace_eligible"]),
                json.dumps(plan["warnings"], ensure_ascii=False),
            ),
        )
    except sqlite3.IntegrityError as exc:
        raise BridgeError(409, "er loopt al een job voor dit bestand") from exc
    database.execute(
        "UPDATE besluit SET status=? WHERE id=?",
        ("ingepland" if executable else "overgeslagen", decision_id),
    )
    return job_id


def _json(value: object) -> Any:
    if value is None or value == "":
        return None
    try:
        return json.loads(str(value))
    except json.JSONDecodeError:
        return None


def job_view(database: Database, job_id: int) -> dict[str, Any]:
    row = database.fetch_one(
        """
        SELECT j.*,b.payload,b.reden AS besluit_reden,f.pad
        FROM job j JOIN besluit b ON b.id=j.besluit_id JOIN bestand f ON f.id=j.bestand_id
        WHERE j.id=?
        """,
        (job_id,),
    )
    if row is None:
        raise BridgeError(404, "job bestaat niet")
    status = str(row["status"])
    staged = None
    if row["mg_staged_pad"] and status == STATUS_WACHT_OP_COMMIT:
        staged = {
            "path": row["mg_staged_pad"],
            "size": row["mg_staged_grootte"],
            "sha256": row["mg_staged_sha256"],
        }
    verification = _json(row["mg_verificatie"])
    if verification is None:
        legacy = database.fetch_one("SELECT * FROM verificatie WHERE job_id=?", (job_id,))
        if legacy is not None:
            verification = {
                "passed": bool(legacy["geslaagd"]),
                "checks": [
                    {
                        "name": "legacy",
                        "passed": bool(legacy["geslaagd"]),
                        "detail": legacy["toelichting"],
                    }
                ],
            }
    percent = float(row["percent"] or 0)
    return {
        "job_id": int(row["id"]),
        "status": STATUS_MAP.get(status, status),
        "status_internal": status,
        "skip_reason": row["fout"] if status == "overgeslagen" else None,
        "error": row["fout"] if status == "mislukt" else None,
        "progress": round(min(percent, 100.0) / 100, 4),
        "fps": row["fps"],
        "eta_seconds": row["eta_seconden"],
        "source_path": row["pad"],
        "source_snapshot": _json(row["mg_snapshot"]),
        "staged_output": staged,
        "verification": verification,
        "decision": _json(row["payload"]),
        "replace_eligible": bool(row["mg_vervangbaar"]),
        "metadata_loss": metadata_loss_of(Besluit.model_validate_json(str(row["payload"]))),
        "warnings": _json(row["mg_waarschuwingen"]) or [],
        "correlation_id": row["mg_correlation_id"],
        "media_ref": _json(row["mg_media_ref"]),
        "lane": row["baan"],
    }


def list_jobs(database: Database, limit: int = 100) -> list[dict[str, Any]]:
    rows = database.fetch_all(
        "SELECT id FROM job WHERE mg_bron='mediaguard' ORDER BY id DESC LIMIT ?", (limit,)
    )
    return [job_view(database, int(row["id"])) for row in rows]


def cancel_job(database: Database, settings: Settings, job_id: int) -> dict[str, Any]:
    row = database.fetch_one("SELECT status FROM job WHERE id=?", (job_id,))
    if row is None:
        raise BridgeError(404, "job bestaat niet")
    status = str(row["status"])
    now = datetime.now(UTC).isoformat()
    if status == "wacht":
        cancelled = database.execute(
            "UPDATE job SET status='afgebroken',fout='door MediaGuard geannuleerd',klaar_op=? "
            "WHERE id=? AND status='wacht'",
            (now, job_id),
        )
        if cancelled != 1:
            # De planner claimde de job net: vraag de lopende uitvoerder te stoppen.
            database.execute(
                "UPDATE job SET afbreken_gevraagd=1 "
                "WHERE id=? AND status IN ('bezig','verifieren')",
                (job_id,),
            )
    elif status in {"bezig", "verifieren"}:
        # De uitvoerder stopt het mediaproces en ruimt zijn cache zelf op.
        database.execute("UPDATE job SET afbreken_gevraagd=1 WHERE id=?", (job_id,))
    elif status == STATUS_WACHT_OP_COMMIT:
        remove_staging(settings, job_id)
        database.execute(
            "UPDATE job SET status='afgebroken',fout='door MediaGuard geannuleerd',"
            "mg_afgehandeld_op=?,mg_staged_pad=NULL WHERE id=?",
            (now, job_id),
        )
    else:
        raise BridgeError(409, f"job met status {STATUS_MAP.get(status, status)} is al afgerond")
    return job_view(database, job_id)


def _require_awaiting(database: Database, job_id: int) -> Any:
    row = database.fetch_one("SELECT * FROM job WHERE id=?", (job_id,))
    if row is None:
        raise BridgeError(404, "job bestaat niet")
    if str(row["status"]) != STATUS_WACHT_OP_COMMIT:
        raise BridgeError(
            409,
            f"job staat niet op awaiting_commit "
            f"({STATUS_MAP.get(str(row['status']), row['status'])})",
        )
    return row


def _already(database: Database, job_id: int, statuses: set[str]) -> bool:
    """Herhaalde terugmelding van core (retry na time-out) is geen fout."""

    row = database.fetch_one("SELECT status FROM job WHERE id=?", (job_id,))
    return row is not None and str(row["status"]) in statuses


def mark_committed(
    database: Database, settings: Settings, job_id: int, sha256: str | None, note: str | None
) -> dict[str, Any]:
    if _already(database, job_id, {STATUS_GECOMMIT}):
        return job_view(database, job_id)
    row = _require_awaiting(database, job_id)
    if not int(row["mg_vervangbaar"]):
        raise BridgeError(
            409, "dit resultaat is een aparte proef-/experimentele kopie en mag niet activeren"
        )
    if sha256 is not None and sha256 != row["mg_staged_sha256"]:
        raise BridgeError(409, "sha256 van core wijkt af van het gestagede resultaat")
    remove_staging(settings, job_id)
    now = datetime.now(UTC).isoformat()
    # klaar_op = moment van activeren, zodat 'vandaag voltooid' en historie kloppen.
    database.execute(
        "UPDATE job SET status=?,mg_afgehandeld_op=?,klaar_op=?,fout=? WHERE id=?",
        (STATUS_GECOMMIT, now, now, note[:REASON_MAX] if note else None, job_id),
    )
    snapshot = _json(row["mg_snapshot"]) or {}
    library = database.fetch_one(
        "SELECT f.library_id FROM bestand f WHERE f.id=?", (int(row["bestand_id"]),)
    )
    if library is not None and snapshot.get("size") and row["mg_staged_grootte"]:
        database.execute(
            "INSERT INTO besparing(job_id,library_id,bytes_voor,bytes_na,dag) "
            "VALUES (?,?,?,?,date('now'))",
            (
                job_id,
                int(library["library_id"]),
                int(snapshot["size"]),
                int(row["mg_staged_grootte"]),
            ),
        )
    database.execute(
        "INSERT INTO auditlog(soort,onderwerp,oud,nieuw,tijd) VALUES ('mediaguard',?,?,?,?)",
        (f"job {job_id}", STATUS_WACHT_OP_COMMIT, STATUS_GECOMMIT, now),
    )
    return job_view(database, job_id)


def mark_rejected(
    database: Database, settings: Settings, job_id: int, reason: str | None
) -> dict[str, Any]:
    if _already(database, job_id, {STATUS_GEWEIGERD, "afgebroken"}):
        return job_view(database, job_id)
    _require_awaiting(database, job_id)
    remove_staging(settings, job_id)
    now = datetime.now(UTC).isoformat()
    database.execute(
        "UPDATE job SET status=?,mg_afgehandeld_op=?,fout=? WHERE id=?",
        (STATUS_GEWEIGERD, now, (reason or "door core geweigerd")[:REASON_MAX], job_id),
    )
    database.execute(
        "INSERT INTO auditlog(soort,onderwerp,oud,nieuw,tijd) VALUES ('mediaguard',?,?,?,?)",
        (f"job {job_id}", STATUS_WACHT_OP_COMMIT, STATUS_GEWEIGERD, now),
    )
    return job_view(database, job_id)
