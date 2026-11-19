"""Geredigeerde bewijspakketten op basis van audit- en jobgegevens."""

from __future__ import annotations

import re
import sqlite3
from typing import Annotated, Any, Literal, TypedDict, get_args

from fastapi import APIRouter, Depends, Path, Request
from fastapi.responses import JSONResponse
from pydantic import AwareDatetime, BaseModel, ConfigDict, Field, with_config

from mediaguard.audit import AUDIT_INSERT
from mediaguard.audit_labels import ACTIONS_NL
from mediaguard.auth import Principal, require_viewer
from mediaguard.context import AppContext, get_app_context
from mediaguard.db import Database, dumps
from mediaguard.job_evidence import counts, keep_cancelled_outcomes, object_value
from mediaguard.jobs import HANDLERS
from mediaguard.secrets_store import redact

from .errors import DATABASE_UNAVAILABLE, ConnectorError, failure, query_faults
from .events import MAX_SQLITE_ID
from .items import instant

router = APIRouter(prefix="/connector/v1")
ID_PATTERN = r"(audit|job):([1-9][0-9]{0,18})"
VERSION_PATTERN = r"[0-9]+(?:\.[0-9]+){2}(?:[-+.][A-Za-z0-9.-]+)?"
JobStatus = Literal[
    "queued", "running", "succeeded", "failed", "retry_scheduled", "cancelled", "paused",
]
ACTIONS = ACTIONS_NL.keys() | {
    "system.emergency_stop", "job.cancel", "job.retry", "job.ignore", "job.ignore_all",
    "settings.update", "policy.profile_update", "identity.manual_decision",
}
UNDO_HINTS = frozenset({
    "Zet de noodstop weer uit",
    "Herstel de oude waarde of 'Herstel standaard'",
    "Voer een nieuwe beoordeling uit",
    "Zet de vorige waarde terug of gebruik 'Herstel standaard' (reset)",
    "Configuratie in MediaGuard aanpassen en opnieuw toepassen",
    "Annuleren onder Instellingen → Systeem → Back-ups",
    "Anime-routering: terugzetten of verplaatsen",
    "Kies 'Nu opnieuw' bij Aanvragen",
    "Activiteit → Problemen → Weer tonen",
    "Zet de vorige versie terug via profielgeschiedenis",
    "Terugzetten via Quarantaine & herstel",
    "Beoordeel de aflevering handmatig op de afleveringspagina",
    "Kies 'Terugzetten' bij Anime-routering",
    "Herstel de oude waarde in AI Adviseur",
    "Pauzeer het plan; vervangingen zijn per item herstelbaar",
})
AUDIT_FIELDS = "id, ts, action, old_value, new_value, undo_hint, evidence_app_version"
JOB_FIELDS = ("id, job_type, created_at, evidence_app_version, payload, status, "
              "started_at, finished_at, result")


class EvidenceDetails(BaseModel):
    app_version: Literal["opgeslagen", "onbekend"]
    request_source: Literal["audit.old_value", "job.payload"]
    response_source: Literal["audit.new_value", "job.result"]
    http_body: Literal["niet_vastgelegd"]


class EvidenceRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    active: bool | None = None
    full: bool | None = None


class EvidenceResponse(BaseModel):
    model_config = ConfigDict(extra="forbid")

    active: bool | None = None
    status: JobStatus | None = None
    episodes: int | None = None
    movies: int | None = None
    failed_items: int | None = None


class EvidenceAudit(BaseModel):
    id: str
    timestamp_utc: AwareDatetime | None
    action: str
    undo_hint: str | None


@with_config(ConfigDict(extra="forbid"))
class ProfileEvidence(TypedDict, total=False):
    prefer_hdr: bool
    prefer_smaller: bool
    reject_false_audio_claims: bool


class EvidencePackage(BaseModel):
    id: str
    source: Literal["audit", "job"]
    app_version: str | None = Field(
        description="Vastgelegde appversie bij dit bewijs-ID. Nieuwe jobs bewaren de versie "
        "bij aanmaak. Elke nieuwe poging heeft een eigen audit-ID. Bij historische stukken "
        'zonder versie is dit null en staat "onbekend" in details.app_version.',
    )
    created_at: AwareDatetime | None
    started_at: AwareDatetime | None
    finished_at: AwareDatetime | None
    deep_link: str
    job_type: str | None
    request: EvidenceRequest | ProfileEvidence | None
    response: EvidenceResponse | ProfileEvidence | None
    audit: EvidenceAudit | None
    details: EvidenceDetails


def _bool(value: dict[str, Any], key: str) -> bool | None:
    v = value.get(key)
    return v if isinstance(v, bool) else None


def _version(row: sqlite3.Row) -> str | None:
    version = row["evidence_app_version"]
    if isinstance(version, str) and len(version) <= 64 and re.fullmatch(VERSION_PATTERN, version):
        return version if redact(version) == version else None
    return None


def _audit_record(row: sqlite3.Row) -> EvidenceAudit:
    action, undo = row["action"], row["undo_hint"]
    return EvidenceAudit(
        id=f"audit:{row['id']}", timestamp_utc=instant(row["ts"]),
        action=action if action in ACTIONS else "audit.recorded",
        undo_hint=redact(undo) if undo in UNDO_HINTS else None,
    )


def _audit_package(row: sqlite3.Row, *, profile_values: bool = True,
                   before_outcomes: bool = False) -> EvidencePackage:
    version = _version(row)
    info = _audit_record(row)
    if before_outcomes and info.action == "job.finished":
        info.action = "audit.recorded"
    request = response = None
    started = finished = None
    if info.action == "job.finished":
        outcome = object_value(row["new_value"])
        if outcome.get("status") in get_args(JobStatus):
            response = EvidenceResponse(status=outcome["status"], **counts(outcome))
            started = instant(object_value(row["old_value"]).get("started_at"))
            finished = info.timestamp_utc
    elif info.action == "policy.profile_update" and profile_values:
        request = _profile_values(row["old_value"])
        response = _profile_values(row["new_value"])
    elif info.action in ("system.emergency_stop", "system.emergency_stop_repeated"):
        active_before = _bool(object_value(row["old_value"]), "active")
        active_after = _bool(object_value(row["new_value"]), "active")
        request = EvidenceRequest(active=active_before) if active_before is not None else None
        response = EvidenceResponse(active=active_after) if active_after is not None else None
    return EvidencePackage(
        id=info.id, source="audit", app_version=version, created_at=info.timestamp_utc,
        started_at=started, finished_at=finished, deep_link="/activiteit?tab=problemen",
        job_type=None,
        request=request, response=response,
        audit=info, details=EvidenceDetails(
            app_version="opgeslagen" if version else "onbekend", request_source="audit.old_value",
            response_source="audit.new_value", http_body="niet_vastgelegd",
        ),
    )


def _profile_values(raw: str | None) -> dict[str, bool] | None:
    data = object_value(raw)
    return {field: data[field] for field in ProfileEvidence.__annotations__
            if isinstance(data.get(field), bool)} or None


RELATED_AUDIT = """
    SELECT id FROM (
        SELECT id, ts FROM audit_events WHERE target_type='job' AND target_id=:target
          AND id <= :cutoff
        UNION ALL
        SELECT id, ts FROM connector_audit_evidence WHERE job_target=:target AND id <= :cutoff
    ) WHERE julianday(ts) >= julianday(:created)
    ORDER BY julianday(ts) DESC, id DESC LIMIT 1
"""


def _freeze_audit(conn: sqlite3.Connection, row: sqlite3.Row, *, profile_values: bool = True,
                  before_outcomes: bool = False) -> None:
    package = _audit_package(row, profile_values=profile_values, before_outcomes=before_outcomes)
    conn.execute(
        "INSERT INTO connector_audit_evidence(id,job_target,ts,package) VALUES (?,?,?,?) "
        "ON CONFLICT(id) DO NOTHING",
        (row["id"], row["target_id"] if row["target_type"] == "job" else None,
         row["ts"], package.model_dump_json()),
    )


def _freeze_job(conn: sqlite3.Connection, row: sqlite3.Row, *, had_snapshot: bool) -> None:
    snapshot = conn.execute("SELECT * FROM job_evidence_snapshot WHERE job_id=?",
                            (row["id"],)).fetchone()
    state = snapshot or row
    related = conn.execute(RELATED_AUDIT, {
        "target": str(row["id"]), "created": row["created_at"],
        "cutoff": snapshot["audit_cutoff"] if snapshot else MAX_SQLITE_ID,
    }).fetchone()
    audit_id = related["id"] if related else None
    if audit_id is not None:
        keep_for_retention(conn, "audit_events", [audit_id])
    payload = result = None
    if row["job_type"] == "library.sync":
        full = _bool(object_value(row["payload"]), "full")
        if full is not None:
            payload = dumps({"full": full})
        # Bij een directe upgrade is jobs.result nog exact de oorspronkelijke
        # bron. Een eerder bevroren poging kan intussen opnieuw uitgevoerd zijn.
        result = dumps(counts(object_value(state["result"] if had_snapshot else row["result"])))
    conn.execute(
        f"INSERT INTO connector_job_evidence({JOB_FIELDS},audit_id) VALUES (?,?,?,?,?,?,?,?,?,?) "
        "ON CONFLICT(id) DO NOTHING",
        (row["id"], row["job_type"], row["created_at"], _version(row), payload, state["status"],
         state["started_at"], state["finished_at"], result, audit_id),
    )


def check_155_reader(conn: sqlite3.Connection) -> None:
    """Deze kernrevisies hebben hetzelfde schema, maar gaven verschillende waarden uit."""
    profiles = conn.execute("SELECT old_value, new_value FROM audit_events "
                            "WHERE action='policy.profile_update'")
    configured = conn.execute("SELECT value_json FROM provisioning_config "
                              "WHERE key='main'").fetchone()
    arr_queue = conn.execute(
        "SELECT 1 FROM instances WHERE archived=0 AND suspended=0 "
        "UNION ALL SELECT 1 FROM source_instances WHERE archived=0 AND kind='live' "
        "AND EXISTS (SELECT 1 FROM migration_runs WHERE status='active') LIMIT 1"
    ).fetchone()
    if (any(_profile_values(r[0]) or _profile_values(r[1]) for r in profiles)
            or arr_queue or configured and object_value(configured[0]).get("download_clients")):
        raise RuntimeError("Schema 155 bevat bewijs met een onbekende weergave. "
                           "De oorspronkelijke kernrevisie is nodig voor deze upgrade.")


def freeze_existing(conn: sqlite3.Connection, *, previous: str) -> None:
    """Bewaar de projectie van het binnenkomende schema binnen de beschermde migratie."""
    had_snapshot = previous >= "155"
    conn.execute("INSERT INTO connector_evidence_origin(schema_version) VALUES (?)", (previous,))
    for row in conn.execute(f"SELECT {AUDIT_FIELDS},target_type,target_id FROM audit_events"):
        _freeze_audit(conn, row, profile_values=had_snapshot, before_outcomes=True)
    for row in conn.execute(f"SELECT {JOB_FIELDS} FROM jobs"):
        _freeze_job(conn, row, had_snapshot=had_snapshot)
    if had_snapshot:
        # Een al uitgevoerde 155 kan een andere poging tonen dan de wachtrij.
        # Behoud het afgegeven pakket en ontsluit ook het aantoonbare eindresultaat.
        finished = conn.execute(
            "SELECT j.id,j.job_type,j.started_at,j.finished_at,j.status,j.result,"
            "p.status AS saved_status,p.finished_at AS saved_end,p.result AS saved_result "
            "FROM jobs j JOIN connector_job_evidence p ON p.id=j.id "
            "WHERE j.status IN ('succeeded','failed','cancelled') AND j.finished_at IS NOT NULL"
        )
        for row in finished:
            result = {}
            if row["job_type"] == "library.sync" and row["status"] == "succeeded":
                result = counts(object_value(row["result"]))
            if (row["status"] == row["saved_status"] and row["finished_at"] == row["saved_end"]
                    and (not result or result == counts(object_value(row["saved_result"])))):
                continue
            conn.execute(AUDIT_INSERT, (
                row["finished_at"], "system", "job.finished", "job", str(row["id"]), None,
                None, None, dumps({"started_at": row["started_at"]}),
                dumps({"status": row["status"], **result}), None, None,
            ))


def keep_for_retention(conn: sqlite3.Connection, table: str, ids: list[int]) -> None:
    """Bewaar alleen de toegestane bewijsgegevens, in dezelfde transactie als de retentie."""
    marks = ",".join("?" for _ in ids)
    if table == "audit_events":
        rows = conn.execute(
            f"SELECT {AUDIT_FIELDS},target_type,target_id FROM audit_events a "
            f"WHERE id IN ({marks}) "
            "AND NOT EXISTS (SELECT 1 FROM connector_audit_evidence p WHERE p.id=a.id)", ids,
        )
        for row in rows:
            _freeze_audit(conn, row)
    elif table == "jobs":
        rows = conn.execute(
            f"SELECT {JOB_FIELDS} FROM jobs j WHERE id IN ({marks}) "
            "AND NOT EXISTS (SELECT 1 FROM connector_job_evidence p WHERE p.id=j.id)", ids,
        )
        for row in rows:
            _freeze_job(conn, row, had_snapshot=True)
        # Het bestaande job-ID kan al een eerdere wachtrijstand bewaren.
        # Bewaar eerst dat pakket, dan de aantoonbare annulering onder een eigen ID.
        keep_cancelled_outcomes(conn, ids)


def _read_audit(db: Database, number: int) -> EvidencePackage | None:
    row = db.fetch_one(
        "SELECT p.package, a.id, a.ts, a.action, a.old_value, a.new_value, a.undo_hint, "
        "a.evidence_app_version FROM (SELECT ? AS number) "
        "LEFT JOIN connector_audit_evidence p ON p.id=number "
        "LEFT JOIN audit_events a ON a.id=number",
        (number,),
    )
    if row["package"] is not None:
        return EvidencePackage.model_validate_json(row["package"])
    return _audit_package(row) if row["id"] is not None else None


def _job_package(ctx: AppContext, row: sqlite3.Row) -> EvidencePackage:
    audit_id = row["audit_id"]
    if not row["frozen"]:
        related = ctx.db.fetch_one(RELATED_AUDIT, {
            "target": str(row["id"]), "created": row["created_at"], "cutoff": MAX_SQLITE_ID,
        })
        audit_id = related["id"] if related else None
    audit = _read_audit(ctx.db, audit_id) if audit_id is not None else None
    version = _version(row)
    request = None
    response = EvidenceResponse(status=row["status"])
    if row["job_type"] == "library.sync":
        full = _bool(object_value(row["payload"]), "full")
        if full is not None:
            request = EvidenceRequest(full=full)
        response = EvidenceResponse(status=row["status"], **counts(object_value(row["result"])))
    return EvidencePackage(
        id=f"job:{row['id']}", source="job", app_version=version,
        created_at=instant(row["created_at"]), started_at=instant(row["started_at"]),
        finished_at=instant(row["finished_at"]), deep_link="/activiteit?tab=problemen",
        job_type=row["job_type"] if row["job_type"] in HANDLERS else None,
        request=request, response=response, audit=audit.audit if audit else None,
        details=EvidenceDetails(
            app_version="opgeslagen" if version else "onbekend", request_source="job.payload",
            response_source="job.result", http_body="niet_vastgelegd",
        ),
    )


@router.get(
    "/evidence/{id}", response_model=EvidencePackage,
    responses={
        401: {"model": ConnectorError, "description": "Aanmelding ontbreekt of is ongeldig."},
        403: {"model": ConnectorError, "description": "Onvoldoende rechten."},
        404: {"model": ConnectorError, "description": "Het bewijsstuk is niet beschikbaar."},
        413: {"model": ConnectorError, "description": "Verzoek groter dan 1 MiB."},
        422: {"model": ConnectorError, "description": "Ongeldige invoer."},
        500: {"model": ConnectorError, "description": "Interne leesfout."},
        503: DATABASE_UNAVAILABLE,
    },
)
def get_evidence(
    request: Request,
    id: Annotated[str, Path(description="Bron-ID in de vorm audit:<nummer> of job:<nummer>.")],
    _: Annotated[Principal, Depends(require_viewer)],
    ctx: Annotated[AppContext, Depends(get_app_context)],
) -> EvidencePackage | JSONResponse:
    """Geredigeerde audit- of jobgegevens met de opgeslagen appversie.

    Historische HTTP-body's zijn niet vastgelegd. Het bewijspakket vermeldt
    de herkomst van de toegestane velden; een onbekende versie blijft null.
    """
    if faults := query_faults(request):
        return failure(422, "validation_error", "Ongeldige invoer", {"fouten": faults})
    match = re.fullmatch(ID_PATTERN, id)
    if match:
        source, number_text = match.groups()
        number = int(number_text)
        if number <= MAX_SQLITE_ID:
            if source == "audit":
                package = _read_audit(ctx.db, number)
                if package:
                    return package
            else:
                row = ctx.db.fetch_one(
                    f"SELECT {JOB_FIELDS}, audit_id, 1 AS frozen FROM connector_job_evidence "
                    f"WHERE id=? UNION ALL SELECT {JOB_FIELDS}, NULL, 0 FROM jobs "
                    "WHERE id=? LIMIT 1",
                    (number, number),
                )
                if row:
                    return _job_package(ctx, row)
    return failure(404, "not_found", "Bewijsstuk niet gevonden")
