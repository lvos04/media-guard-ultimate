"""Geredigeerde bewijspakketten op basis van audit- en jobgegevens."""

from __future__ import annotations

import re
import sqlite3
from typing import Annotated, Any, Literal

from fastapi import APIRouter, Depends, Path
from fastapi.responses import JSONResponse
from pydantic import AwareDatetime, BaseModel, ConfigDict, Field

from mediaguard.audit_labels import ACTIONS_NL
from mediaguard.auth import Principal, require_viewer
from mediaguard.context import AppContext, get_app_context
from mediaguard.db import loads
from mediaguard.jobs import HANDLERS
from mediaguard.secrets_store import redact

from .errors import failure
from .events import MAX_SQLITE_ID, ConnectorError
from .items import instant

router = APIRouter(prefix="/connector/v1")
ID_PATTERN = r"(audit|job):([1-9][0-9]{0,18})"
VERSION_PATTERN = r"[0-9]+(?:\.[0-9]+){2}(?:[-+.][A-Za-z0-9.-]+)?"
JobStatus = Literal[
    "queued", "running", "succeeded", "failed", "retry_scheduled", "cancelled", "paused",
]
ACTIONS = ACTIONS_NL.keys() | {
    "system.emergency_stop", "job.cancel", "job.retry", "job.started", "job.ignore",
    "job.ignore_all",
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


class EvidencePackage(BaseModel):
    id: str
    source: Literal["audit", "job"]
    app_version: str | None = Field(
        description="Vastgelegde gebruikte appversie. Bij historische stukken zonder versie "
        'is dit null en staat "onbekend" in details.app_version.',
    )
    created_at: AwareDatetime | None
    started_at: AwareDatetime | None
    finished_at: AwareDatetime | None
    deep_link: str
    job_type: str | None
    request: EvidenceRequest | None
    response: EvidenceResponse | None
    audit: EvidenceAudit | None
    details: EvidenceDetails


def _object(raw: str | None) -> dict[str, Any]:
    try:
        value = loads(raw)
    except (ValueError, TypeError, RecursionError):
        return {}
    return value if isinstance(value, dict) else {}


def _bool(value: dict[str, Any], key: str) -> bool | None:
    v = value.get(key)
    return v if isinstance(v, bool) else None


def _count(value: dict[str, Any], key: str) -> int | None:
    v = value.get(key)
    return v if type(v) is int and 0 <= v <= MAX_SQLITE_ID else None


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


def _audit_package(row: sqlite3.Row) -> EvidencePackage:
    version = _version(row)
    info = _audit_record(row)
    active_before = active_after = None
    if info.action in ("system.emergency_stop", "system.emergency_stop_repeated"):
        active_before = _bool(_object(row["old_value"]), "active")
        active_after = _bool(_object(row["new_value"]), "active")
    return EvidencePackage(
        id=info.id, source="audit", app_version=version, created_at=info.timestamp_utc,
        started_at=None, finished_at=None, deep_link="/activiteit?tab=problemen", job_type=None,
        request=EvidenceRequest(active=active_before) if active_before is not None else None,
        response=EvidenceResponse(active=active_after) if active_after is not None else None,
        audit=info, details=EvidenceDetails(
            app_version="opgeslagen" if version else "onbekend", request_source="audit.old_value",
            response_source="audit.new_value", http_body="niet_vastgelegd",
        ),
    )


def _job_package(ctx: AppContext, row: sqlite3.Row) -> EvidencePackage:
    snapshot = ctx.db.fetch_one("SELECT * FROM job_evidence_snapshot WHERE job_id=?", (row["id"],))
    state = snapshot or row
    related = ctx.db.fetch_one(
        f"""SELECT {AUDIT_FIELDS} FROM audit_events
            WHERE target_type='job' AND target_id=CAST(? AS TEXT)
              AND julianday(ts) >= julianday(?) AND id <= ?
            ORDER BY julianday(ts) DESC, id DESC LIMIT 1""",
        (row["id"], row["created_at"], snapshot["audit_cutoff"] if snapshot else MAX_SQLITE_ID),
    )
    version = _version(row)
    request = None
    response = EvidenceResponse(status=state["status"])
    if row["job_type"] == "library.sync":
        full = _bool(_object(row["payload"]), "full")
        if full is not None:
            request = EvidenceRequest(full=full)
        result = _object(state["result"])
        response.episodes = _count(result, "episodes")
        response.movies = _count(result, "movies")
        response.failed_items = _count(result, "failed_items")
    return EvidencePackage(
        id=f"job:{row['id']}", source="job", app_version=version,
        created_at=instant(row["created_at"]), started_at=instant(state["started_at"]),
        finished_at=instant(state["finished_at"]), deep_link="/activiteit?tab=problemen",
        job_type=row["job_type"] if row["job_type"] in HANDLERS else None,
        request=request, response=response, audit=_audit_record(related) if related else None,
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
    },
)
def get_evidence(
    id: Annotated[str, Path(description="Bron-ID in de vorm audit:<nummer> of job:<nummer>.")],
    _: Annotated[Principal, Depends(require_viewer)],
    ctx: Annotated[AppContext, Depends(get_app_context)],
) -> EvidencePackage | JSONResponse:
    """Geredigeerde audit- of jobgegevens met de opgeslagen appversie.

    Historische HTTP-body's zijn niet vastgelegd. Het bewijspakket vermeldt
    de herkomst van de toegestane velden; een onbekende versie blijft null.
    """
    match = re.fullmatch(ID_PATTERN, id)
    if match:
        source, number_text = match.groups()
        number = int(number_text)
        if number <= MAX_SQLITE_ID:
            if source == "audit":
                row = ctx.db.fetch_one(
                    f"SELECT {AUDIT_FIELDS} FROM audit_events WHERE id=?", (number,),
                )
                if row:
                    return _audit_package(row)
            else:
                row = ctx.db.fetch_one(
                    """SELECT id, job_type, status, payload, result, created_at, started_at,
                              finished_at, evidence_app_version FROM jobs WHERE id=?""",
                    (number,),
                )
                if row:
                    return _job_package(ctx, row)
    return failure(404, "not_found", "Bewijsstuk niet gevonden")
