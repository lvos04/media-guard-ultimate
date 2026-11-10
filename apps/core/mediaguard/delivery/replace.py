"""Verkeerde aflevering of film vervangen via MediaGuard's eigen quarantaine (verbeterlijst 12).

Een bestand dat volgens de identiteitscontrole aantoonbaar de verkeerde aflevering of film is,
wordt eerst in de quarantaine van MediaGuard veiliggesteld (zelfde dataset: hardlink; anders
kopie + sha256-controle). Pas daarna laat MediaGuard Sonarr/Radarr het bestand uit de
bibliotheek halen en de juiste release zoeken. Een prullenbak in Sonarr/Radarr is daarvoor
niet nodig: het origineel blijft volgens de bewaartermijn herstelbaar onder *Quarantaine &
herstel*, ook voor films. Terugzetten zet het bestand op zijn (lege) plek terug en laat
Sonarr/Radarr herscannen; daarna haalt MediaGuard het niet opnieuw weg (jouw keuze telt).

Kan dit proces niet in de quarantaine of de bibliotheek schrijven (core in de
compose-installatie heeft die bewust alleen-lezen), dan doet de recovery-werker de
bestandsstap (job ``recovery.replace_wrong``). Mislukt vervangen, dan probeert MediaGuard het
na 1 uur en na 6 uur opnieuw; na 3 mislukkingen stopt het en staat het onder *Wacht op jou*.

Alleen bij eenduidig bewijs (laatste identiteitscontrole: 'mismatch' én 'actionable'); twijfel
gaat naar de AI of de gebruiker. Nooit tijdens een noodstop, nooit in proefmodus, nooit zolang
de bibliotheek vergrendeld is en nooit terwijl er een download of import voor dezelfde
aflevering/film loopt. Elke stap staat in het journaal van ``replacement_transactions``. Een
fout wordt meteen teruggedraaid; een onderbreking (MediaGuard viel weg) ruimt
``recover_interrupted`` na 2 uur op (bestand terug op zijn plek, niets kwijt). De journal
replay van het recovery-proces laat een lopende vervanging ('pending') met rust.
"""

from __future__ import annotations

import asyncio
import logging
import os
import uuid
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import TYPE_CHECKING, Any

from mediaguard.arr.client import ArrError, ArrNotFound
from mediaguard.audit import audit
from mediaguard.db import dumps, utcnow

if TYPE_CHECKING:
    from mediaguard.context import AppContext

LOGGER = logging.getLogger(__name__)
# begin van quarantine_items.reason: zo herkent de quarantainelijst een verkeerd bestand
REASON_PREFIX = "Verkeerd bestand"
ACTION = "identity.auto_replaced"
# bestandsstap in de recovery-rol als dit proces niet mag schrijven
HANDOFF_JOB = "recovery.replace_wrong"
# mislukte vervangingen per bestand: wachttijd na de 1e en 2e mislukking, stop na de 3e
FAILURES_KEY = "replace_failures"
RETRY_AFTER_HOURS = (1, 6)
MAX_FAILURES = 3


def blocked_reason(ctx: AppContext, instance_id: str) -> str | None:
    """Waarom MediaGuard nu niet zelf mag vervangen, of None."""
    from mediaguard.instances.registry import get_instance

    if ctx.emergency_stopped():
        return "noodstop actief"
    if not ctx.config.value("library.write_enabled"):
        return "bibliotheek is nog vergrendeld (vóór de overstap)"
    inst = get_instance(ctx, instance_id)
    if inst is None or inst.archived:
        return "instantie onbekend"
    if inst.suspended:
        return "instantie is gepauzeerd"
    if inst.shadow_mode:
        return "proefmodus: MediaGuard kijkt alleen mee"
    if not ctx.config.value("identity.auto_replace_on_mismatch", {"instance": instance_id}):
        return "automatisch vervangen staat uit"
    return None


def recover_interrupted(ctx: AppContext, *, min_age_hours: float = 2.0) -> int:
    """Een vervanging die halverwege stopte (MediaGuard viel weg) consistent maken: staat het
    bestand nog op zijn plek, dan gaat de kopie weg; is het weg, dan komt het origineel uit de
    quarantaine terug. Nooit iets van een vervanging die nog loopt (slot bezet)."""
    from datetime import UTC, datetime, timedelta

    from mediaguard import locks
    from mediaguard.recovery import executor as ex

    if ctx.emergency_stopped():
        return 0  # noodstop: niets aanraken; het origineel staat veilig in de quarantaine
    cutoff = (datetime.now(UTC) - timedelta(hours=min_age_hours)).isoformat(timespec="seconds")
    rows = ctx.db.fetch_dicts(
        "SELECT id, instance_id, media_kind, provider_id, library_path FROM "
        "replacement_transactions WHERE state='pending' AND staged_path='' AND updated_at <= ?",
        (cutoff,))
    fixed = 0
    for r in rows:
        resource = locks.media_resource(str(r["instance_id"]), str(r["media_kind"]),
                                        str(r["provider_id"]))
        if locks.holder(ctx.db, resource):
            continue
        if write_problem(ctx, Path(str(r["library_path"]))):
            continue  # alleen-lezen hier (core in compose): de recovery-werker doet het
        try:
            ex.reconcile(ctx, int(r["id"]), failure="Onderbroken (herstart)")
            fixed += 1
        except Exception:  # noqa: BLE001 - volgende ronde opnieuw
            LOGGER.exception("onderbroken vervanging %s niet afgehandeld", r["id"])
    return fixed


def proven_wrong(ctx: AppContext, instance_id: str, kind: str, file_id: int) -> str | None:
    """None als de laatste identiteitscontrole van dít bestand een eenduidige ('actionable')
    mismatch is; anders waarom MediaGuard het niet zelf vervangt. Twijfel of tegenstrijdig
    bewijs gaat altijd naar de AI of naar jou, nooit naar de quarantaine."""
    from mediaguard.identity import service as identity

    if identity.keyed_by_episode_ids(ctx, instance_id, kind, file_id):
        return "beoordeeld vóór de import; er staat nog geen bestand om te vervangen"
    row = ctx.db.fetch_one(
        "SELECT status, actionable FROM identity_results WHERE instance_id=? AND kind=? AND "
        "provider_id=? ORDER BY id DESC LIMIT 1", (instance_id, kind, str(file_id)))
    if row is None or row["status"] != "mismatch":
        return "niet (meer) als verkeerd beoordeeld"
    if not int(row["actionable"] or 0):
        return "bewijs wijst niet eenduidig één andere aflevering/film aan; wacht op beoordeling"
    return None


def already_replaced(ctx: AppContext, instance_id: str, file_id: int) -> bool:
    return ctx.db.fetch_one(
        "SELECT 1 FROM audit_events WHERE action=? AND instance_id=? AND target_id=? LIMIT 1",
        (ACTION, instance_id, str(file_id))) is not None


# -- mislukte pogingen onthouden --------------------------------------------------------------
def _fkey(instance_id: str, file_id: int) -> str:
    return f"{instance_id}:{int(file_id)}"


def _failures(ctx: AppContext) -> dict[str, dict[str, Any]]:
    return dict(ctx.state_get(FAILURES_KEY, {}) or {})


def _identity_id(ctx: AppContext, instance_id: str, kind: str, file_id: int) -> int | None:
    row = ctx.db.fetch_one("SELECT MAX(id) AS id FROM identity_results WHERE instance_id=? AND "
                           "kind=? AND provider_id=?", (instance_id, kind, str(file_id)))
    return int(row["id"]) if row and row["id"] is not None else None


def clear_failure(ctx: AppContext, instance_id: str | None = None,
                  file_id: int | None = None) -> int:
    """Teller wissen (geslaagd, uitslag veranderd, of 'Opnieuw proberen'). Zonder bestand:
    alles (of alles van één instantie)."""
    data = _failures(ctx)
    if file_id is not None and instance_id is not None:
        keys = [_fkey(instance_id, file_id)]
    else:
        keys = [k for k in data if instance_id is None or k.startswith(f"{instance_id}:")]
    gone = [k for k in keys if data.pop(k, None) is not None]
    if gone:
        ctx.state_set(FAILURES_KEY, data)
    return len(gone)


def _record_failure(ctx: AppContext, instance_id: str, kind: str, file_id: int, path: Path,
                    reason: str) -> None:
    data = _failures(ctx)
    key = _fkey(instance_id, file_id)
    count = int((data.get(key) or {}).get("count") or 0) + 1
    data[key] = {"count": count, "at": datetime.now(UTC).isoformat(timespec="seconds"),
                 "reason": reason[:200], "kind": kind, "file": path.name,
                 "identity_id": _identity_id(ctx, instance_id, kind, file_id)}
    if len(data) > 200:  # klein houden: de oudste vallen eraf
        for k in sorted(data, key=lambda k: str(data[k].get("at")))[:len(data) - 200]:
            data.pop(k, None)
    ctx.state_set(FAILURES_KEY, data)


def failure_wait(ctx: AppContext, instance_id: str, kind: str, file_id: int) -> str | None:
    """Waarom MediaGuard dit bestand nu niet opnieuw probeert (eerdere mislukking), of None.
    Verandert de identiteitsuitslag, dan begint de teller opnieuw."""
    rec = _failures(ctx).get(_fkey(instance_id, file_id))
    if not rec:
        return None
    if rec.get("identity_id") != _identity_id(ctx, instance_id, kind, file_id):
        clear_failure(ctx, instance_id, file_id)
        return None
    count = int(rec.get("count") or 0)
    if count >= MAX_FAILURES:
        return (f"{count} keer mislukt ({rec.get('reason')}); MediaGuard probeert het niet meer "
                "zelf, zie Wacht op jou")
    hours = RETRY_AFTER_HOURS[min(count, len(RETRY_AFTER_HOURS)) - 1]
    try:
        due = datetime.fromisoformat(str(rec.get("at"))) + timedelta(hours=hours)
    except ValueError:
        return None
    if datetime.now(UTC) >= due:
        return None
    from zoneinfo import ZoneInfo

    when = due.astimezone(ZoneInfo("Europe/Amsterdam")).strftime("%H:%M")
    return f"vorige poging mislukt ({rec.get('reason')}); opnieuw om {when}"


def attention_items(ctx: AppContext) -> list[dict[str, Any]]:
    """'Wacht op jou': bestanden die na 3 pogingen niet vervangen konden worden."""
    stuck = [(k, v) for k, v in _failures(ctx).items()
             if int(v.get("count") or 0) >= MAX_FAILURES]
    if not stuck:
        return []
    rows = [{"id": k, "title": str(v.get("file") or k), "actions": [],
             "detail": f"{v.get('count')} keer mislukt · {v.get('reason')}"}
            for k, v in sorted(stuck, key=lambda kv: str(kv[1].get("at")), reverse=True)[:20]]
    return [{"count": len(stuck), "rows": rows,
             "title_nl": f"Verkeerd bestand kon niet worden vervangen ({len(stuck)})",
             "detail_nl": "MediaGuard probeerde het 3 keer; het bestand staat nog op zijn plek. "
                          "Los de oorzaak op (bv. rechten in Sonarr/Radarr) en kies Opnieuw "
                          "proberen."}]


# -- schrijven mag hier? ------------------------------------------------------------------------
def _writable(path: Path) -> bool:
    probe = path
    while not probe.exists() and probe != probe.parent:
        probe = probe.parent
    return os.access(probe, os.W_OK)


def write_problem(ctx: AppContext, path: Path) -> str | None:
    """None als dit proces het bestand in de quarantaine kan zetten (en zo nodig terug)."""
    from mediaguard.recovery import executor as ex

    if not _writable(ex.quarantine_dir(ctx, 0, path).parent):
        return "de quarantainemap is hier alleen-lezen"
    if not _writable(path.parent):
        return "de bibliotheekmap is hier alleen-lezen"
    return None


def _hand_off(ctx: AppContext, *, instance_id: str, kind: str, file_id: int, reason_nl: str,
              actor: str, delivery_item_id: int | None) -> dict[str, Any]:
    """De recovery-werker (mag wel schrijven) doet de bestandsstap; nooit twee tegelijk."""
    from mediaguard import jobs

    target = _fkey(instance_id, file_id)
    busy = ctx.db.fetch_one(
        "SELECT id FROM jobs WHERE job_type=? AND target_id=? AND status IN "
        "('queued','running','retry_scheduled','paused')", (HANDOFF_JOB, target))
    if busy is None:
        jobs.enqueue(ctx.db, HANDOFF_JOB, {
            "instance_id": instance_id, "kind": kind, "file_id": int(file_id),
            "reason_nl": reason_nl, "actor": actor, "delivery_item_id": delivery_item_id},
            instance_id=instance_id, target_type="file", target_id=target, priority=50,
            max_attempts=1, created_by=actor,
            idempotency_key=f"{HANDOFF_JOB}:{target}:{utcnow()[:13]}")
        runner = ctx.services.get("jobs")
        if runner is not None:
            runner.wake()
    return {"status": "queued", "reason_nl": "de herstelwerker zet het in quarantaine"}


# -- door jou teruggezet ------------------------------------------------------------------------
def restored_by(ctx: AppContext, instance_id: str, path: Path) -> str | None:
    """Wie dit verkeerde bestand zelf uit de quarantaine terugzette (of None)."""
    row = ctx.db.fetch_one(
        "SELECT t.created_by FROM quarantine_items q JOIN replacement_transactions t ON "
        "t.restore_of_item_id=q.id AND t.kind='restore' AND t.state='committed' WHERE "
        "q.instance_id=? AND q.original_path=? AND q.reason LIKE ? ORDER BY t.id DESC LIMIT 1",
        (instance_id, str(path), f"{REASON_PREFIX}%"))
    return str(row["created_by"] or "gebruiker") if row else None


def _keep_restored(ctx: AppContext, instance_id: str, kind: str, file_id: int, path: Path,
                   actor: str) -> None:
    """Jouw terugzetting telt als oordeel 'juist': MediaGuard haalt het niet opnieuw weg."""
    from mediaguard.identity import service as identity
    from mediaguard.identity.engine import EpisodeRef, Evidence, IdentityResult

    old = identity.latest(ctx, instance_id, kind, file_id) or {}
    expected = [EpisodeRef.from_dict(e) for e in old.get("expected") or []]
    detail = "Door jou teruggezet uit de quarantaine; MediaGuard laat het staan"
    ev = Evidence("user_confirmed", "supports", detail, claimed=expected)
    identity.save(ctx, instance_id=instance_id, kind=kind, provider_id=file_id,
                  path=str(path), result=IdentityResult("verified", 1.0, False, detail,
                                                        expected, list(expected), [ev]),
                  source="user", actor=actor)
    clear_failure(ctx, instance_id, file_id)
    audit(ctx.db, actor=actor, action="identity.decision", target_type=kind,
          target_id=str(file_id), instance_id=instance_id, new_value={"status": "verified"},
          reason="Teruggezet uit de quarantaine")


async def replace_wrong_file(ctx: AppContext, *, instance_id: str, kind: str, file_id: int,
                             reason_nl: str, actor: str = "svc:identity",
                             delivery_item_id: int | None = None, manual: bool = False,
                             in_worker: bool = False) -> dict[str, Any]:
    """Verkeerd bestand naar quarantaine, laten verwijderen en de juiste zoeken.

    Geeft ``{"status": "replaced"|"queued"|"kept"|"skipped"|"review"|"gone"|"failed",
    "reason_nl": ...}``; 'review' = geen eenduidig bewijs, dan beslist de AI of de gebruiker
    (nooit zelf weghalen); 'queued' = de recovery-werker doet de bestandsstap; 'kept' = je
    zette dit bestand zelf terug. ``manual`` (knop 'Nu opnieuw') negeert eerdere mislukkingen;
    ``in_worker``: aangeroepen door de recovery-werker (niet nog eens doorgeven)."""
    from mediaguard.instances.registry import arr_client

    if kind not in ("episode_file", "movie_file"):
        return {"status": "skipped", "reason_nl": "geen bestand"}
    why = blocked_reason(ctx, instance_id)
    if why:
        return {"status": "skipped", "reason_nl": why}
    if already_replaced(ctx, instance_id, file_id):
        return {"status": "gone", "reason_nl": "al vervangen"}
    doubt = proven_wrong(ctx, instance_id, kind, file_id)
    if doubt:
        return {"status": "review", "reason_nl": doubt}
    if manual:
        clear_failure(ctx, instance_id, file_id)
    wait = failure_wait(ctx, instance_id, kind, file_id)
    if wait:
        return {"status": "skipped", "reason_nl": wait}
    from mediaguard.recovery.release_check import quota_exceeded

    if quota_exceeded(ctx):
        # verbeterlijst 37: de quarantaine groeit nooit boven het maximum (ook niet als een
        # TrueNAS-pool niet gezond is en opruimen pauzeert); het bestand blijft dan staan
        return {"status": "skipped", "reason_nl": "quarantaine is vol; het bestand blijft "
                "staan tot er ruimte is"}
    movie = kind == "movie_file"
    resource = "moviefile" if movie else "episodefile"
    async with arr_client(ctx, instance_id) as client:
        try:
            info = await client.get(f"{resource}/{int(file_id)}") or {}
        except ArrNotFound:
            return {"status": "gone", "reason_nl": "bestand staat niet meer in Sonarr/Radarr"}
        who = restored_by(ctx, instance_id, Path(str(info.get("path") or "")))
        if who:
            _keep_restored(ctx, instance_id, kind, file_id, Path(str(info["path"])), who)
            return {"status": "kept", "reason_nl": "je hebt dit bestand zelf teruggezet; "
                    "MediaGuard laat het staan"}
        target = await _target(ctx, client, instance_id, kind, file_id, info)
        if target.get("skip"):
            return {"status": "skipped", "reason_nl": target["skip"]}
        problem = write_problem(ctx, target["path"])
        if problem:
            if not in_worker and ctx.settings.role == "core":
                return _hand_off(ctx, instance_id=instance_id, kind=kind, file_id=file_id,
                                 reason_nl=reason_nl, actor=actor,
                                 delivery_item_id=delivery_item_id)
            return {"status": "skipped", "reason_nl": problem}
        result = await _secure_and_remove(ctx, client, instance_id, kind, file_id, info,
                                          target, reason_nl, actor)
        if result["status"] == "failed":
            _record_failure(ctx, instance_id, kind, file_id, target["path"],
                            str(result.get("error") or result["reason_nl"]))
        if result["status"] != "replaced":
            return result
        clear_failure(ctx, instance_id, file_id)
        result["searched"] = await _search_correct(ctx, client, target, delivery_item_id)
    return result


async def _target(ctx: AppContext, client: Any, instance_id: str, kind: str, file_id: int,
                  info: dict[str, Any]) -> dict[str, Any]:
    """Pad, aflevering(en)/film en de herscan-opdracht voor een latere terugzetting."""
    from mediaguard.identity import service as identity

    path = Path(str(info.get("path") or ""))
    if not str(path) or str(path) == ".":
        return {"skip": "Sonarr/Radarr gaf geen pad voor dit bestand"}
    if kind == "movie_file":
        mid = int(info.get("movieId") or 0)
        if not mid:
            return {"skip": "film bij dit bestand onbekend"}
        return {"path": path, "movie_id": mid, "episode_ids": [],
                "refresh": {"command": "RefreshMovie", "movieIds": [mid], "movie_id": mid}}
    sid = int(info.get("seriesId") or 0)
    eps = identity.episode_ids(ctx, instance_id, kind, file_id)
    if not eps and sid:
        eps = [int(e["id"]) for e in await client.episodes(sid)
               if int(e.get("episodeFileId") or 0) == int(file_id)]
    if not sid or not eps:
        return {"skip": "aflevering bij dit bestand onbekend"}
    return {"path": path, "series_id": sid, "episode_ids": eps,
            "refresh": {"command": "RescanSeries", "seriesId": sid, "episode_ids": eps}}


async def _secure_and_remove(ctx: AppContext, client: Any, instance_id: str, kind: str,
                             file_id: int, info: dict[str, Any], target: dict[str, Any],
                             reason_nl: str, actor: str) -> dict[str, Any]:
    from mediaguard import locks
    from mediaguard.recovery import executor as ex
    from mediaguard.recovery import fsops

    path: Path = target["path"]
    roots = ex._allowed_roots(ctx)
    if roots and not fsops.within(path, roots):
        return {"status": "skipped", "reason_nl": "bestand valt buiten de bibliotheekmappen"}
    st = ex._stat(path)
    if st is None:
        return {"status": "skipped", "reason_nl": "bestand niet gevonden op schijf"}
    import stat as stat_mod

    if stat_mod.S_ISLNK(st.mode) or not stat_mod.S_ISREG(st.mode):
        return {"status": "skipped", "reason_nl": "geen gewoon bestand"}
    probe = {"instance_id": instance_id, "refresh_json": dumps(target["refresh"])}
    blocker = ex.download_blocker(ctx, probe)
    if blocker is not None:
        return {"status": "skipped", "reason_nl": "er loopt nog een download of import "
                f"({blocker.get('release') or blocker.get('download_id')})"}
    resource = locks.media_resource(instance_id, kind, file_id)
    owner = f"identity:replace:{uuid.uuid4().hex[:12]}"
    if not locks.acquire(ctx.db, resource, owner, "identity.replace", ttl_seconds=3600):
        held = locks.holder(ctx.db, resource) or {}
        return {"status": "skipped", "reason_nl": f"bestand is bezet ({held.get('purpose')})"}
    txn_id: int | None = None
    try:
        now = utcnow()
        txn_id = ctx.db.execute_insert(
            "INSERT INTO replacement_transactions(kind,instance_id,media_kind,provider_id,"
            "library_path,final_path,staged_path,source_snapshot_json,state,refresh_json,"
            "correlation_id,created_at,created_by,updated_at) "
            "VALUES ('replace',?,?,?,?,?,'',?,'pending',?,?,?,?,?)",
            (instance_id, kind, str(file_id), str(path), str(path),
             dumps(st.to_snapshot(path)), dumps(target["refresh"]), uuid.uuid4().hex, now,
             actor, now))
        ex.journal(ctx, txn_id, "lock", "done", purpose="verkeerd bestand naar quarantaine")
        if ctx.emergency_stopped():
            raise ex.Abort("EMERGENCY_STOP", "Noodstop actief")
        qdir = ex.quarantine_dir(ctx, txn_id, path)
        await asyncio.to_thread(qdir.parent.mkdir, parents=True, exist_ok=True)
        cross = not fsops.same_device(path, qdir.parent) or bool(
            ctx.services.get("recovery_force_copy"))
        reserve = int(ctx.config.value("recovery.min_free_gb")) * 1024**3
        if cross and fsops.free_bytes(qdir.parent) < st.size + reserve:
            raise ex.Abort("INSUFFICIENT_SPACE", "Onvoldoende ruimte in quarantaine")
        qpath = qdir / path.name
        ex._update(ctx, txn_id, quarantine_path=str(qpath))
        ex.journal(ctx, txn_id, "quarantine", "begin", path=str(qpath), cross_device=cross)
        method, sha = await asyncio.to_thread(ex._secure_original, path, qpath, st, cross)
        item_id = ctx.db.execute_insert(
            "INSERT INTO quarantine_items(transaction_id,instance_id,media_kind,provider_id,"
            "original_path,quarantine_path,size,sha256,inode,device,method,status,reason,"
            "created_at) VALUES (?,?,?,?,?,?,?,?,?,?,?,'held',?,?)",
            (txn_id, instance_id, kind, str(file_id), str(path), str(qpath), st.size, sha,
             st.inode, st.device, method, f"{REASON_PREFIX}: {reason_nl}"[:500], utcnow()))
        # status blijft 'pending' tot het einde: de journal replay van het recovery-proces
        # (bij zijn herstart) mag een lopende vervanging van core nooit 'terugdraaien' en zo
        # de quarantainekopie weggooien. Een onderbroken vervanging ruimt recover_interrupted
        # zelf op.
        ex._update(ctx, txn_id, quarantine_item_id=item_id, quarantine_method=method,
                   original_sha256=sha)
        ex.journal(ctx, txn_id, "quarantined", "done", quarantine_item_id=item_id,
                   quarantine_method=method)
        # vlak vóór het weghalen nog eens kijken of de kopie er echt (heel) staat
        qst = ex._stat(qpath)
        if qst is None or qst.size != st.size:
            raise ex.Abort("QUARANTINE_MISSING", "Kopie in quarantaine ontbreekt")
        # het origineel staat veilig: Sonarr/Radarr halen het uit hun eigen administratie
        await client.delete(f"{'moviefile' if kind == 'movie_file' else 'episodefile'}/"
                            f"{int(file_id)}")
    except Exception as exc:  # noqa: BLE001 - altijd terug naar een consistente toestand
        if txn_id is not None:
            msg = exc.message if isinstance(exc, ex.Abort | ArrError) else \
                f"{type(exc).__name__}: {exc}"
            # bestand nog op zijn plek → kopie weg; weg → origineel terug uit quarantaine
            await asyncio.to_thread(ex.reconcile, ctx, txn_id, failure=msg,
                                    aborted=isinstance(exc, ex.Abort))
        locks.release(ctx.db, resource, owner)
        LOGGER.info("verkeerd bestand %s niet vervangen: %s", path, exc)
        return {"status": "failed", "reason_nl": f"vervangen mislukt: {exc}"[:300],
                "error": str(exc)[:200]}
    try:
        # in één keer: afgerond, herstelbaar en vrijgegeven (het origineel is aantoonbaar
        # verkeerd; het telt mee voor de bewaartermijn). Een onderbreking vóór dit punt laat
        # de transactie op 'quarantined' staan: de journal replay zet het bestand dan terug.
        now = utcnow()
        checks = dumps([{"name": "verkeerd-bestand", "passed": True,
                         "detail": f"Identiteitscontrole: {reason_nl}"[:300]}])
        with ctx.db.transaction() as conn:
            conn.execute("UPDATE quarantine_items SET status='restorable' WHERE id=? AND "
                         "status IN ('held','pending')", (item_id,))
            conn.execute("INSERT INTO quarantine_release_checks(item_id,status,checks_json,"
                         "checked_at) VALUES (?,'passed',?,?) ON CONFLICT(item_id) DO NOTHING",
                         (item_id, checks, now))
            done = conn.execute("UPDATE replacement_transactions SET state='committed', "
                                "finished_at=?, updated_at=? WHERE id=? AND state='pending'",
                                (now, now, txn_id)).rowcount
            if not done:
                raise RuntimeError("transactie is intussen door iets anders afgehandeld")
            conn.execute("INSERT INTO replacement_journal(transaction_id,ts,step,phase,"
                         "detail_json) VALUES (?,?,'committed','done',?)",
                         (txn_id, now, dumps({"removed_by": "arr-api"})))
    finally:
        locks.release(ctx.db, resource, owner)
    audit(ctx.db, actor=actor, action=ACTION, target_type=kind, target_id=str(file_id),
          instance_id=instance_id, reason=reason_nl,
          new_value={"file": str(path), "quarantine_item": item_id, "transaction_id": txn_id},
          undo_hint="Terugzetten via Quarantaine & herstel")
    return {"status": "replaced", "reason_nl": "in quarantaine gezet; de juiste wordt gezocht",
            "quarantine_item_id": item_id, "transaction_id": txn_id}


async def _search_correct(ctx: AppContext, client: Any, target: dict[str, Any],
                          delivery_item_id: int | None) -> bool:
    """Juiste release zoeken (binnen het zoekquotum; anders doet de levering dat later)."""
    from mediaguard.delivery import store

    iid = str(client.instance_id)
    if ctx.emergency_stopped():
        return False  # noodstop net ingegaan: niets meer aanpassen of zoeken
    try:
        if target.get("movie_id"):
            movie = await client.movie_get(int(target["movie_id"]))
            if not movie.get("monitored"):
                await client.movie_update({**movie, "monitored": True})
            if store.search_budget(ctx, iid) < 1:
                return False
            await client.movies_search([int(target["movie_id"])])
            store.record_search(ctx, iid, "MoviesSearch", 1, delivery_item_id)
            return True
        eps = [int(e) for e in target.get("episode_ids") or []]
        await client.episodes_monitor(eps, True)
        budget = store.search_budget(ctx, iid)
        if budget < 1:
            return False
        await client.episode_search(eps[:budget])
        store.record_search(ctx, iid, "EpisodeSearch", len(eps[:budget]), delivery_item_id)
        return True
    except ArrError as exc:
        LOGGER.info("zoeken na vervangen mislukt: %s", exc.message)
        return False
