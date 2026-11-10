"""Persistente jobqueue met state machine, idempotency, retries met backoff en noodstop.

Handlers registreren zich met ``@job_handler("type")``. Een handler krijgt een
``JobContext`` en geeft een JSON-serialiseerbaar resultaat terug. Status staat altijd
in SQLite: na een herstart worden 'running' jobs teruggezet naar 'queued'
(handlers moeten daarom idempotent zijn of hun eigen checkpoint gebruiken).
"""

from __future__ import annotations

import asyncio
import contextlib
import logging
import uuid
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from typing import TYPE_CHECKING, Any

from mediaguard.db import Database, dumps, loads, utcnow
from mediaguard.secrets_store import redact

if TYPE_CHECKING:
    from mediaguard.context import AppContext

LOGGER = logging.getLogger(__name__)

# job types die als 'mutatie' gelden en door de noodstop worden tegengehouden
MUTATING_PREFIXES = ("recovery.", "encode.", "backfill.", "migration.cutover", "optimize.",
                     "search.", "dv.activate", "quarantine.", "storage.")


class JobCancelled(Exception):
    pass


class RetryLater(Exception):
    def __init__(self, message: str, delay_seconds: int = 300) -> None:
        super().__init__(message)
        self.delay_seconds = delay_seconds


class Waiting(RetryLater):
    """Normale wachttoestand (bv. maximum aantal downloads bereikt): geen fout, telt niet
    als poging en gaat vanzelf verder."""


@dataclass
class JobContext:
    job_id: int
    job_type: str
    payload: dict[str, Any]
    instance_id: str | None
    correlation_id: str | None
    app: AppContext

    def log(self, message: str, level: str = "info") -> None:
        self.app.db.execute(
            "INSERT INTO job_logs(job_id,ts,level,message) VALUES (?,?,?,?)",
            (self.job_id, utcnow(), level, redact(message)),
        )

    def progress(self, value: float) -> None:
        self.app.db.execute("UPDATE jobs SET progress=? WHERE id=?",
                            (max(0.0, min(1.0, value)), self.job_id))
        self.app.events.publish("job.progress", {"id": self.job_id, "progress": value},
                                instance_id=self.instance_id, persist=False)

    def check_cancelled(self) -> None:
        row = self.app.db.fetch_one("SELECT status FROM jobs WHERE id=?", (self.job_id,))
        if row is None or row["status"] == "cancelled":
            raise JobCancelled()
        if self.job_type.startswith(MUTATING_PREFIXES) and self.app.emergency_stopped():
            raise JobCancelled()


Handler = Callable[[JobContext], Awaitable[Any]]
HANDLERS: dict[str, Handler] = {}
# job-type-prefix -> proces-rol die hem mag uitvoeren (bv. "recovery." -> "recovery").
# Rol "all" voert alles uit (single-container/dev). Leeg = geen beperking.
ROLE_BOUND: dict[str, str] = {}


def bind_role(prefix: str, role: str) -> None:
    ROLE_BOUND[prefix] = role


def allowed_for_role(job_type: str, role: str) -> bool:
    for prefix, required in ROLE_BOUND.items():
        if job_type.startswith(prefix):
            return role in (required, "all")
    # Niet-gebonden jobs (bibliotheeksync, backfill, encode, …) horen bij core: het
    # recovery-proces houdt zijn hartslag en journaal vrij van zwaar ander werk.
    return role != "recovery"


# Hiervan draait er maar één tegelijk (geheugen/schrijfslot bij grote bibliotheken).
EXCLUSIVE_TYPES = {"library.sync"}


def job_handler(job_type: str) -> Callable[[Handler], Handler]:
    def deco(fn: Handler) -> Handler:
        HANDLERS[job_type] = fn
        return fn
    return deco


def enqueue(
    db: Database,
    job_type: str,
    payload: dict[str, Any] | None = None,
    *,
    instance_id: str | None = None,
    target_type: str | None = None,
    target_id: str | int | None = None,
    priority: int = 100,
    idempotency_key: str | None = None,
    max_attempts: int = 3,
    run_after: datetime | None = None,
    created_by: str | None = None,
    correlation_id: str | None = None,
) -> int:
    """Zet een job in de wachtrij. Bestaat de idempotency_key al (en is die job niet
    afgerond-mislukt/geannuleerd), dan wordt de bestaande job-ID teruggegeven."""
    if idempotency_key:
        row = db.fetch_one(
            "SELECT id,status FROM jobs WHERE idempotency_key=?", (idempotency_key,)
        )
        if row is not None:
            if row["status"] not in ("failed", "cancelled"):
                return int(row["id"])
            db.execute("UPDATE jobs SET idempotency_key=NULL WHERE id=?", (row["id"],))
    return db.execute_insert(
        "INSERT INTO jobs(job_type,status,instance_id,target_type,target_id,priority,payload,"
        "max_attempts,idempotency_key,correlation_id,run_after,created_at,created_by) "
        "VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?)",
        (
            job_type, "queued", instance_id, target_type,
            None if target_id is None else str(target_id), priority, dumps(payload or {}),
            max_attempts, idempotency_key, correlation_id or uuid.uuid4().hex,
            run_after.isoformat(timespec="seconds") if run_after else None, utcnow(), created_by,
        ),
    )


def cancel(db: Database, job_id: int) -> bool:
    return db.execute(
        "UPDATE jobs SET status='cancelled', finished_at=? WHERE id=? AND status IN "
        "('queued','retry_scheduled','running','paused')",
        (utcnow(), job_id),
    ) > 0


class JobRunner:
    def __init__(self, app: AppContext, concurrency: int | None = None) -> None:
        self.app = app
        # None = de instelling 'Gelijktijdige achtergrondtaken' volgen (punt 39: die deed
        # niets, de runner had een vaste 4); een getal = vast (tests)
        self._fixed = concurrency
        self._task: asyncio.Task[None] | None = None
        self._running: dict[int, asyncio.Task[None]] = {}
        self._types: dict[int, str] = {}
        self._wakeup = asyncio.Event()

    def recover_after_restart(self) -> int:
        """Na een herstart: 'running' terug naar 'queued', maar alleen de jobs die dít
        proces (deze rol) uitvoert. Core en recovery delen de database; anders zette een
        herstart van het ene proces de lopende jobs van het andere terug en liepen ze twee
        keer tegelijk."""
        role = self.app.settings.role
        rows = self.app.db.fetch_all("SELECT id, job_type FROM jobs WHERE status='running'")
        mine = [int(r["id"]) for r in rows if allowed_for_role(str(r["job_type"]), role)]
        total = 0
        for i in range(0, len(mine), 500):
            chunk = mine[i:i + 500]
            total += self.app.db.execute(
                "UPDATE jobs SET status='queued', started_at=NULL WHERE status='running' AND "
                f"id IN ({','.join('?' * len(chunk))})", chunk)  # noqa: S608
        return total

    def wake(self) -> None:
        self._wakeup.set()

    @property
    def _watch_name(self) -> str:
        role = self.app.settings.role
        return "jobs" if role in ("core", "all") else f"jobs_{role}"

    async def start(self) -> None:
        from mediaguard import taskwatch

        self.recover_after_restart()
        taskwatch.register(self.app, self._watch_name, "Takenwachtrij", every=2)
        self._task = asyncio.create_task(self._loop(), name="mediaguard-jobrunner")

    async def stop(self) -> None:
        if self._task:
            self._task.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await self._task
        for task in list(self._running.values()):
            task.cancel()
        for task in list(self._running.values()):
            with contextlib.suppress(asyncio.CancelledError, Exception):
                await task

    async def _loop(self) -> None:
        from mediaguard import taskwatch

        while True:
            try:
                await self.run_pending()
                taskwatch.ok(self.app, self._watch_name)
            except Exception as exc:  # noqa: BLE001 - runner mag nooit sterven
                LOGGER.exception("jobrunner-fout")
                taskwatch.fail(self.app, self._watch_name, exc)
            self._wakeup.clear()
            with contextlib.suppress(TimeoutError):
                await asyncio.wait_for(self._wakeup.wait(), timeout=2.0)

    def _eligible(self, row: Any, stopped: bool) -> bool:
        if int(row["id"]) in self._running:
            return False
        if stopped and str(row["job_type"]).startswith(MUTATING_PREFIXES):
            return False
        if row["job_type"] not in HANDLERS:
            return False
        if row["job_type"] in EXCLUSIVE_TYPES and row["job_type"] in self._running_types():
            return False
        return allowed_for_role(str(row["job_type"]), self.app.settings.role)

    def _running_types(self) -> set[str]:
        return set(self._types.values())

    def _claim_next(self) -> dict[str, Any] | None:
        now = utcnow()
        stopped = self.app.emergency_stopped()
        # eerst zonder schrijfslot kijken; alleen bij een kandidaat een transactie openen
        # (anders opent elk proces elke 2 s een BEGIN IMMEDIATE op een lege wachtrij)
        peek = self.app.db.fetch_all(
            "SELECT id, job_type FROM jobs WHERE status IN ('queued','retry_scheduled') "
            "AND (run_after IS NULL OR run_after<=?) ORDER BY priority, id LIMIT 50", (now,))
        if not any(self._eligible(r, stopped) for r in peek):
            return None
        with self.app.db.transaction() as conn:
            rows = conn.execute(
                "SELECT * FROM jobs WHERE status IN ('queued','retry_scheduled') "
                "AND (run_after IS NULL OR run_after<=?) ORDER BY priority, id LIMIT 50",
                (now,),
            ).fetchall()
            for row in rows:
                if not self._eligible(row, stopped):
                    continue
                conn.execute(
                    "UPDATE jobs SET status='running', started_at=?, attempts=attempts+1 "
                    "WHERE id=?", (now, row["id"]),
                )
                return dict(row)
        return None

    @property
    def concurrency(self) -> int:
        if self._fixed is not None:
            return self._fixed
        try:
            return max(1, int(self.app.config.value("jobs.max_concurrency")))
        except (KeyError, TypeError, ValueError):
            return 4

    async def run_pending(self) -> None:
        while len(self._running) < self.concurrency:
            job = self._claim_next()
            if job is None:
                return
            task = asyncio.create_task(self._execute(job))
            self._running[int(job["id"])] = task
            self._types[int(job["id"])] = str(job["job_type"])

            def _done(_t: Any, jid: int = int(job["id"])) -> None:
                self._running.pop(jid, None)
                self._types.pop(jid, None)
            task.add_done_callback(_done)

    async def run_until_idle(self, timeout: float = 30.0) -> None:
        """Voor tests: voer alle direct uitvoerbare jobs uit."""
        deadline = asyncio.get_running_loop().time() + timeout
        while True:
            await self.run_pending()
            if not self._running:
                return
            await asyncio.wait(list(self._running.values()), timeout=0.5)
            if asyncio.get_running_loop().time() > deadline:
                raise TimeoutError("jobs niet klaar binnen timeout")

    async def _execute(self, job: dict[str, Any]) -> None:
        db = self.app.db
        ctx = JobContext(
            job_id=int(job["id"]), job_type=job["job_type"], payload=loads(job["payload"], {}),
            instance_id=job["instance_id"], correlation_id=job["correlation_id"], app=self.app,
        )
        self.app.events.publish("job.started", {"id": ctx.job_id, "type": ctx.job_type},
                                instance_id=ctx.instance_id, persist=False)
        try:
            result = await HANDLERS[ctx.job_type](ctx)
        except JobCancelled:
            db.execute("UPDATE jobs SET status='cancelled', finished_at=? WHERE id=?",
                       (utcnow(), ctx.job_id))
            ctx.log("Geannuleerd (gebruiker of noodstop)", "warning")
        except Waiting as exc:
            when = datetime.now(UTC) + timedelta(seconds=exc.delay_seconds)
            db.execute(
                "UPDATE jobs SET status='retry_scheduled', run_after=?, error=?, "
                "attempts=MAX(attempts-1,0) WHERE id=?",
                (when.isoformat(timespec="seconds"), f"Wacht: {exc}", ctx.job_id),
            )
            ctx.log(f"Wacht: {exc}", "info")
        except RetryLater as exc:
            attempts = int(job["attempts"]) + 1
            if attempts >= int(job["max_attempts"]):
                self._fail(ctx, f"Opgegeven na {attempts} pogingen: {exc}")
            else:
                when = datetime.now(UTC) + timedelta(seconds=exc.delay_seconds)
                db.execute(
                    "UPDATE jobs SET status='retry_scheduled', run_after=?, error=? WHERE id=?",
                    (when.isoformat(timespec="seconds"), redact(str(exc)), ctx.job_id),
                )
                ctx.log(f"Nieuwe poging gepland: {exc}", "warning")
        except Exception as exc:  # noqa: BLE001 - fout wordt persistent vastgelegd
            if _is_transient(exc):
                # bv. Sonarr/Radarr nog niet gestart: één regel, geen traceback
                LOGGER.warning("job %s (%s): %s", ctx.job_id, ctx.job_type, exc)
            else:
                LOGGER.exception("job %s mislukt", ctx.job_id)
            attempts = int(job["attempts"]) + 1
            if attempts < int(job["max_attempts"]):
                delay = min(3600, 30 * (2 ** attempts))
                when = datetime.now(UTC) + timedelta(seconds=delay)
                db.execute(
                    "UPDATE jobs SET status='retry_scheduled', run_after=?, error=? WHERE id=?",
                    (when.isoformat(timespec="seconds"), redact(f"{type(exc).__name__}: {exc}"),
                     ctx.job_id),
                )
            else:
                self._fail(ctx, f"{type(exc).__name__}: {exc}")
        else:
            db.execute(
                "UPDATE jobs SET status='succeeded', result=?, progress=1, finished_at=?, "
                "error=NULL WHERE id=? AND status='running'",
                (dumps(result), utcnow(), ctx.job_id),
            )
            resolve_earlier_failures(db, job)
        if ctx.job_type in HEAVY_JOBS:
            from mediaguard import memory

            await asyncio.to_thread(memory.trim)
        row = db.fetch_one("SELECT status FROM jobs WHERE id=?", (ctx.job_id,))
        self.app.events.publish(
            "job.finished", {"id": ctx.job_id, "type": ctx.job_type,
                             "status": row["status"] if row else "unknown"},
            instance_id=ctx.instance_id, persist=False,
        )

    def _fail(self, ctx: JobContext, message: str) -> None:
        self.app.db.execute(
            "UPDATE jobs SET status='failed', error=?, finished_at=? WHERE id=?",
            (redact(message), utcnow(), ctx.job_id),
        )
        ctx.log(message, "error")


# Na deze taken staan grote tijdelijke lijsten vrij: geheugen teruggeven aan het systeem
HEAVY_JOBS = {"library.sync", "backfill.run", "optimize.run", "maintenance.nightly",
              "migration.verify", "migration.scan", "migration.shadow_import",
              "migration.autopilot", "migration.optimization"}


TRANSIENT_MARKERS = ("ConnectError", "ConnectTimeout", "ReadTimeout", "TimeoutError",
                     "ArrUnavailable", "EncoderUnavailable", "niet bereikbaar", "onbereikbaar",
                     "Connection refused", "All connection attempts failed",
                     "database is locked")


def _is_transient(exc: BaseException) -> bool:
    try:
        from mediaguard.arr.client import ArrUnavailable

        if isinstance(exc, ArrUnavailable):
            return True
    except ImportError:  # pragma: no cover
        pass
    text = f"{type(exc).__name__}: {exc}"
    return any(m in text for m in TRANSIENT_MARKERS)


def is_transient_error(text: str | None) -> bool:
    return bool(text) and any(m in str(text) for m in TRANSIENT_MARKERS)


def resolve_earlier_failures(db: Database, job: dict[str, Any]) -> int:
    """Een geslaagde run van hetzelfde soort werk op hetzelfde doel lost eerdere fouten op:
    die tellen dan niet meer mee in 'Actie nodig' en de foutbadge."""
    return db.execute(
        "UPDATE jobs SET resolved_at=?, resolved_reason='later geslaagd' WHERE status='failed' "
        "AND resolved_at IS NULL AND job_type=? AND COALESCE(instance_id,'')=? AND "
        "COALESCE(target_id,'')=?",
        (utcnow(), job["job_type"], job.get("instance_id") or "",
         str(job.get("target_id") or "")))


def unresolved_failures(db: Database, days: int = 7) -> int:
    since = (datetime.now(UTC) - timedelta(days=days)).isoformat(timespec="seconds")
    row = db.fetch_one("SELECT COUNT(*) AS n FROM jobs WHERE status='failed' AND resolved_at "
                       "IS NULL AND COALESCE(finished_at, created_at)>=?", (since,))
    return int(row["n"]) if row else 0


def retry_transient_failures(db: Database, max_auto: int = 3) -> int:
    """Dagelijks: tijdelijk mislukte jobs (verbinding, time-out) nog eens proberen."""
    rows = db.fetch_all("SELECT id, error FROM jobs WHERE status='failed' AND resolved_at IS "
                        "NULL AND auto_retries<?", (max_auto,))
    ids = [int(r["id"]) for r in rows if is_transient_error(r["error"])]
    for jid in ids:
        db.execute("UPDATE jobs SET status='queued', run_after=NULL, attempts=0, "
                   "auto_retries=auto_retries+1 WHERE id=? AND status='failed'", (jid,))
    return len(ids)
