"""Vervangtransacties met duurzaam journaal (G2, P2). Enige schrijver op bibliotheekpaden.

Stappen (elke statusovergang wordt vóór de volgende stap gecommit in SQLite, ``synchronous
=FULL``):

1. ``lock``        mediaresource-lock; geen download/import bezig; geen noodstop
2. ``validate``    bron opnieuw stat'en en vergelijken met de encoder-``source_snapshot``
                   (size/mtime/inode/device); staged-uitvoer hashen
3.                 hardlinks (nlink>1 → policy, nooit stil breken), vrije ruimte + reserve
   ``snapshot``    optioneel ZFS-snapshot via TrueNAS-API
4. ``quarantine``  origineel veiligstellen: zelfde filesystem → hardlink (geen moment
                   zonder bestand op het bibliotheekpad), anders kopie + fsync + sha256-
                   verificatie. Geen schijn-atomische claims over datasetgrenzen.
5. ``stage_temp``  staged → verborgen tijdelijk bestand in de doelmap, fsync, sha256,
                   rechten/eigenaar; daarna ``os.replace`` op de definitieve naam + fsync map
6. ``record``      quarantaine-item herstelbaar
7. ``refresh``     RescanSeries/RefreshMovie via de arr-client (fout = waarschuwing + retry)
8. ``audit``       auditregel, lock vrij, status ``committed``

Herstel na crash (``reconcile``) kijkt naar de werkelijke toestand op schijf: vóór
activatie staat het origineel nog op zijn pad → opruimen + ``rolled_back``; ná ``os.replace``
→ afmaken (``committed``). Ontbreekt het bibliotheekbestand, dan wordt het origineel uit
quarantaine teruggezet. Nooit gissen: onverklaarbare toestand → ``conflict`` en niets
verwijderen.
"""

from __future__ import annotations

import asyncio
import contextlib
import errno
import hashlib
import logging
import os
import stat as stat_mod
from collections.abc import Callable
from pathlib import Path
from typing import TYPE_CHECKING, Any

from mediaguard import jobs, locks
from mediaguard.audit import audit
from mediaguard.db import dumps, loads, utcnow
from mediaguard.recovery import fsops
from mediaguard.recovery.fsops import FileStat

if TYPE_CHECKING:
    from mediaguard.context import AppContext

LOGGER = logging.getLogger(__name__)

TERMINAL = {"committed", "rolled_back", "aborted", "conflict"}
PRE_ACTIVATION = {"pending", "locked", "validated", "snapshotted", "quarantined", "temp_ready"}
POST_ACTIVATION = {"activated", "recorded", "refreshed"}
FaultHook = Callable[[str], None]


class Abort(Exception):
    """Veilige weigering vóór er iets onomkeerbaars gebeurt."""

    def __init__(self, code: str, message: str) -> None:
        super().__init__(f"{code}: {message}")
        self.code = code
        self.message = message


class SimulatedCrash(BaseException):  # noqa: N818 - testhulp: gedraagt zich als proceskill
    """Alleen voor fault-injection: wordt (net als een stroomstoring) nergens opgevangen."""


# ── journaal-helpers ──────────────────────────────────────────────────────────
def load(ctx: AppContext, txn_id: int) -> dict[str, Any]:
    row = ctx.db.fetch_one("SELECT * FROM replacement_transactions WHERE id=?", (txn_id,))
    if row is None:
        raise KeyError(f"transactie {txn_id} bestaat niet")
    return dict(row)


def _update(ctx: AppContext, txn_id: int, **fields: Any) -> None:
    fields["updated_at"] = utcnow()
    cols = ", ".join(f"{k}=?" for k in fields)
    ctx.db.execute(f"UPDATE replacement_transactions SET {cols} WHERE id=?",
                   (*fields.values(), txn_id))


def journal(ctx: AppContext, txn_id: int, step: str, phase: str, **detail: Any) -> None:
    ctx.db.execute(
        "INSERT INTO replacement_journal(transaction_id,ts,step,phase,detail_json) "
        "VALUES (?,?,?,?,?)", (txn_id, utcnow(), step, phase, dumps(detail)))


def _journal_detail(ctx: AppContext, txn_id: int, step: str) -> dict[str, Any]:
    row = ctx.db.fetch_one(
        "SELECT detail_json FROM replacement_journal WHERE transaction_id=? AND step=? AND "
        "phase='done' ORDER BY id DESC LIMIT 1", (txn_id, step))
    return loads(row["detail_json"], {}) if row else {}


def _state(ctx: AppContext, txn_id: int, state: str, **fields: Any) -> None:
    _update(ctx, txn_id, state=state, **fields)
    journal(ctx, txn_id, state, "done", **{k: v for k, v in fields.items()
                                            if isinstance(v, int | str | None)})


def _warn(ctx: AppContext, txn_id: int, message: str) -> None:
    txn = load(ctx, txn_id)
    warnings = loads(txn["warnings_json"], [])
    warnings.append(message)
    _update(ctx, txn_id, warnings_json=dumps(warnings))


def resource_for(txn: dict[str, Any]) -> str:
    if txn.get("instance_id") and txn.get("provider_id"):
        return locks.media_resource(txn["instance_id"], txn["media_kind"] or "file",
                                    txn["provider_id"])
    return "path:" + hashlib.sha256(txn["library_path"].encode()).hexdigest()[:32]


def _owner(txn_id: int) -> str:
    return f"recovery:txn:{txn_id}"


def _matches_snapshot(st: FileStat | None, snap: dict[str, Any]) -> bool:
    return st is not None and (st.size, st.mtime_ns, st.inode, st.device) == (
        int(snap["size"]), int(snap["mtime_ns"]), int(snap["inode"]), int(snap["device"]))


def _stat(path: Path) -> FileStat | None:
    try:
        return FileStat.of(path)
    except FileNotFoundError:
        return None


# ── controles ─────────────────────────────────────────────────────────────────
def download_blocker(ctx: AppContext, txn: dict[str, Any]) -> dict[str, Any] | None:
    """De grab (laatste 48 uur) voor dezelfde aflevering(en)/film die nog op import wacht,
    of None. Mislukte, geweigerde of door MediaGuard als mislukt gemarkeerde downloads en
    grabs die door een nieuwere grab zijn ingehaald tellen niet mee (rapport 6, N4: anders
    bleef activatie urenlang 'uitgesteld' terwijl er niets liep)."""
    refresh = loads(txn.get("refresh_json"), {}) or {}
    instance = txn.get("instance_id")
    if not instance:
        return None
    rows = ctx.db.fetch_all(
        "SELECT download_id, episode_ids_json, movie_id, release_title, received_at "
        "FROM release_provenance WHERE instance_id=? AND event_type='Grab' "
        "AND received_at >= datetime('now','-2 days') ORDER BY received_at DESC",
        (instance,))
    want_eps = {int(e) for e in refresh.get("episode_ids") or []}
    want_movie = refresh.get("movie_id")
    outcomes = dict(ctx.state_get("download_outcomes", {}) or {})
    for r in rows:
        eps = {int(e) for e in loads(r["episode_ids_json"], [])}
        hit = bool(want_eps & eps) or (want_movie is not None and r["movie_id"] == want_movie)
        if not hit:
            continue
        did = str(r["download_id"] or "")
        if (outcomes.get(did) or {}).get("outcome") in ("failed", "imported"):
            return None  # alleen de nieuwste grab voor dit item telt
        ended = ctx.db.fetch_one(
            "SELECT 1 FROM import_events WHERE instance_id=? AND download_id=? AND "
            "event_type IN ('Download','Upgrade') UNION ALL SELECT 1 FROM release_provenance "
            "WHERE instance_id=? AND download_id=? AND event_type IN "
            "('DownloadFailed','DownloadIgnored') LIMIT 1", (instance, did, instance, did))
        if ended is None:
            return {"download_id": did, "release": r["release_title"],
                    "since": r["received_at"]}
        return None  # alleen de nieuwste grab voor dit item telt
    return None


def download_in_progress(ctx: AppContext, txn: dict[str, Any]) -> bool:
    return download_blocker(ctx, txn) is not None


def _allowed_roots(ctx: AppContext) -> list[Path]:
    return [Path(p) for p in ctx.settings.library_roots]


def _validate_paths(ctx: AppContext, txn: dict[str, Any]) -> None:
    lib, final, staged = Path(txn["library_path"]), Path(txn["final_path"]), \
        Path(txn["staged_path"])
    roots = _allowed_roots(ctx)
    if roots and not (fsops.within(lib, roots) and fsops.within(final, roots)):
        raise Abort("PATH_NOT_ALLOWED", "Bibliotheekpad valt buiten de ingestelde bibliotheken")
    staged_roots = [ctx.settings.staging_dir, ctx.settings.quarantine_dir,
                    *[r / ".mediaguard-quarantine" for r in roots]]
    if not fsops.within(staged, staged_roots):
        raise Abort("PATH_NOT_ALLOWED", "Staged bestand staat niet in staging/quarantaine")
    if final.parent.resolve() != lib.parent.resolve():
        raise Abort("PATH_NOT_ALLOWED", "Doel moet in dezelfde map als het origineel staan")


def quarantine_dir(ctx: AppContext, txn_id: int, lib: Path) -> Path:
    if ctx.config.value("recovery.quarantine_location") == "library_root":
        for root in _allowed_roots(ctx):
            if fsops.within(lib, [root]):
                return root / ".mediaguard-quarantine" / f"txn-{txn_id}"
    return ctx.settings.quarantine_dir / f"txn-{txn_id}"


# ── uitvoering ────────────────────────────────────────────────────────────────
async def run_transaction(ctx: AppContext, txn_id: int, *, fault: FaultHook | None = None
                          ) -> dict[str, Any]:
    txn = load(ctx, txn_id)
    if txn["state"] in TERMINAL:
        return txn
    if txn["state"] != "pending":
        # halverwege onderbroken (crash/herstart): eerst consistente toestand herstellen
        await asyncio.to_thread(reconcile, ctx, txn_id)
        return load(ctx, txn_id)
    hit = fault or (lambda _point: None)
    lib, final, staged = (Path(txn["library_path"]), Path(txn["final_path"]),
                          Path(txn["staged_path"]))
    snap = loads(txn["source_snapshot_json"], {})
    resource, owner = resource_for(txn), _owner(txn_id)
    try:
        # 1. lock ---------------------------------------------------------------
        journal(ctx, txn_id, "lock", "begin", resource=resource)
        if ctx.emergency_stopped():
            raise Abort("EMERGENCY_STOP", "Noodstop actief")
        if not ctx.config.value("library.write_enabled"):
            raise Abort("LIBRARY_LOCKED", "Bibliotheek is nog vergrendeld (vóór de overstap "
                                          "worden geen bestanden vervangen)")
        if not locks.acquire(ctx.db, resource, owner, f"recovery.{txn['kind']}",
                             ttl_seconds=6 * 3600):
            held = locks.holder(ctx.db, resource) or {}
            raise Abort("LOCKED", f"Item is bezet door {held.get('owner')} "
                                  f"({held.get('purpose')})")
        if (blocker := download_blocker(ctx, txn)) is not None:
            raise Abort("IMPORT_IN_PROGRESS", "Er loopt nog een download/import voor dit item: "
                        f"{blocker.get('release') or blocker['download_id']} (gegrabd "
                        f"{str(blocker.get('since') or '')[:16].replace('T', ' ')})")
        _state(ctx, txn_id, "locked")
        hit("lock")
        if snap.get("empty"):
            await _place_in_empty_spot(ctx, txn_id, txn, hit)
            return load(ctx, txn_id)

        # 2/3. validatie ----------------------------------------------------------
        journal(ctx, txn_id, "validate", "begin")
        _validate_paths(ctx, txn)
        st = _stat(lib)
        if st is None:
            raise Abort("SOURCE_MISSING", "Bronbestand bestaat niet meer")
        if stat_mod.S_ISLNK(st.mode) or not stat_mod.S_ISREG(st.mode):
            raise Abort("NOT_REGULAR_FILE", "Bron is geen gewoon bestand (symlink?)")
        if not _matches_snapshot(st, snap):
            raise Abort("SOURCE_CHANGED", "Bron is gewijzigd sinds de encode "
                        "(grootte/mtime/inode wijken af)")
        if final != lib and _stat(final) is not None:
            raise Abort("TARGET_EXISTS", f"Doelbestand {final.name} bestaat al")
        sst = _stat(staged)
        if sst is None or not stat_mod.S_ISREG(sst.mode):
            raise Abort("STAGED_MISSING", "Geverifieerde uitvoer ontbreekt in staging")
        if txn["staged_size"] is not None and sst.size != int(txn["staged_size"]):
            raise Abort("STAGED_CORRUPT", "Grootte van staged uitvoer klopt niet")
        staged_sha = await asyncio.to_thread(fsops.sha256_file, staged)
        if txn["staged_sha256"] and staged_sha != txn["staged_sha256"]:
            raise Abort("STAGED_CORRUPT", "sha256 van staged uitvoer klopt niet")
        if st.nlink > 1:
            policy = ctx.config.value("recovery.hardlink_policy",
                                      {"instance": txn["instance_id"]}
                                      if txn["instance_id"] else None)
            if policy == "skip":
                raise Abort("HARDLINKED", f"Bron heeft {st.nlink} hardlinks (seeding?); "
                            "beleid: overslaan")
            if not txn["hardlink_break_confirmed"]:
                raise Abort("HARDLINK_CONFIRMATION_REQUIRED",
                            "Hardlink verbreken vereist bevestiging per bestand")
        reserve = int(ctx.config.value("recovery.min_free_gb")) * 1024**3
        if fsops.free_bytes(final.parent) < sst.size + reserve:
            raise Abort("INSUFFICIENT_SPACE", "Onvoldoende vrije ruimte in de doelmap "
                        "(incl. reserve)")
        qdir = quarantine_dir(ctx, txn_id, lib)
        with fsops.op("mkdir"):
            qdir.parent.mkdir(parents=True, exist_ok=True)
        cross = not fsops.same_device(lib, qdir.parent) or bool(
            ctx.services.get("recovery_force_copy"))
        if cross and fsops.free_bytes(qdir.parent) < st.size + reserve:
            raise Abort("INSUFFICIENT_SPACE", "Onvoldoende ruimte in quarantaine (incl. reserve)")
        _state(ctx, txn_id, "validated", staged_sha256=staged_sha, staged_size=sst.size)
        hit("validate")

        # ZFS-snapshot (optioneel) -----------------------------------------------
        if ctx.config.value("recovery.zfs_snapshot_enabled"):
            from mediaguard.recovery.zfs import SnapshotError, create_snapshot
            dataset = str(ctx.config.value("recovery.zfs_dataset") or "")
            name = f"mediaguard-txn-{txn_id}"
            try:
                await create_snapshot(ctx, dataset, name)
            except SnapshotError as exc:
                raise Abort("SNAPSHOT_FAILED", str(exc)) from exc
            _state(ctx, txn_id, "snapshotted", zfs_snapshot=f"{dataset}@{name}")
            hit("snapshot")

        # 4. origineel veiligstellen ---------------------------------------------
        if ctx.emergency_stopped():
            raise Abort("EMERGENCY_STOP", "Noodstop actief vóór veiligstellen")
        qpath = qdir / lib.name
        _update(ctx, txn_id, quarantine_path=str(qpath))
        journal(ctx, txn_id, "quarantine", "begin", path=str(qpath), cross_device=cross)
        method, orig_sha = await asyncio.to_thread(_secure_original, lib, qpath, st, cross)
        hit("quarantine:after_action")
        item_id = ctx.db.execute_insert(
            "INSERT INTO quarantine_items(transaction_id,instance_id,media_kind,provider_id,"
            "original_path,quarantine_path,size,sha256,inode,device,method,status,reason,"
            "created_at) VALUES (?,?,?,?,?,?,?,?,?,?,?,'held',?,?)",
            (txn_id, txn["instance_id"], txn["media_kind"], txn["provider_id"], str(lib),
             str(qpath), st.size, orig_sha, st.inode, st.device, method,
             "Herstel van restore" if txn["kind"] == "restore" else "Origineel vóór vervanging",
             utcnow()))
        _state(ctx, txn_id, "quarantined", quarantine_item_id=item_id,
               quarantine_method=method, original_sha256=orig_sha)
        hit("quarantine")

        # 5. tijdelijk bestand in doelmap ----------------------------------------
        digest = hashlib.sha256(os.fsencode(final.name)).hexdigest()[:12]
        temp = final.parent / f".mg-replace-{txn_id}-{digest}.part"
        _update(ctx, txn_id, temp_path=str(temp))
        journal(ctx, txn_id, "stage_temp", "begin", path=str(temp))
        link_restore = txn["kind"] == "restore" and fsops.same_device(staged, final.parent) \
            and not ctx.services.get("recovery_force_copy")
        temp_inode = await asyncio.to_thread(_stage_temp, staged, temp, staged_sha, st,
                                             link_restore)
        journal(ctx, txn_id, "stage_temp", "done", inode=temp_inode)
        _state(ctx, txn_id, "temp_ready")
        hit("stage_temp")

        # laatste controles vlak vóór activatie (AV1-spec regel 11)
        if ctx.emergency_stopped():
            raise Abort("EMERGENCY_STOP", "Noodstop actief vóór activatie")
        if not _matches_snapshot(_stat(lib), snap):
            raise Abort("SOURCE_CHANGED", "Bron gewijzigd tijdens de transactie")
        journal(ctx, txn_id, "activate", "begin")
        with fsops.op("replace"):
            os.replace(temp, final)
        fsops.fsync_dir(final.parent)
        hit("activate:after_action")
        if final != lib:
            with fsops.op("unlink"):
                os.unlink(lib)  # origineel staat veilig in quarantaine
            fsops.fsync_dir(lib.parent)
            hit("activate:after_unlink")
        _state(ctx, txn_id, "activated")
        hit("activate")

        # 6-8 ---------------------------------------------------------------------
        _record(ctx, txn_id)
        hit("record")
        await _refresh(ctx, txn_id)
        hit("refresh")
        _commit(ctx, txn_id, recovered=False)
        return load(ctx, txn_id)
    except Abort as exc:
        journal(ctx, txn_id, "abort", "error", code=exc.code, message=exc.message)
        await asyncio.to_thread(reconcile, ctx, txn_id, failure=exc.message, aborted=True)
        return load(ctx, txn_id)
    except Exception as exc:  # noqa: BLE001 - elke fout → consistente toestand
        LOGGER.exception("transactie %s faalde", txn_id)
        msg = f"{type(exc).__name__}: {exc}"
        if fsops.is_enospc(exc):
            msg = "Schijf vol tijdens kopiëren: " + msg
        env = environment_problem(exc)
        if env is not None:
            # rechten/schijf: geen fout van de encode — als tijdelijke weigering behandelen,
            # zodat de geverifieerde uitvoer bewaard blijft en later opnieuw kan
            msg = f"{env} ({type(exc).__name__}: {exc})"
            journal(ctx, txn_id, "abort", "error", code="ENVIRONMENT", message=msg,
                    op=fsops.op_of(exc), errno=getattr(exc, "errno", None),
                    path=getattr(exc, "filename", None))
            await asyncio.to_thread(reconcile, ctx, txn_id, failure=msg, aborted=True)
            return load(ctx, txn_id)
        journal(ctx, txn_id, "error", "error", message=msg, op=fsops.op_of(exc))
        await asyncio.to_thread(reconcile, ctx, txn_id, failure=msg)
        return load(ctx, txn_id)


async def _place_in_empty_spot(ctx: AppContext, txn_id: int, txn: dict[str, Any],
                               hit: FaultHook) -> None:
    """Terugzetten op een lege plek (verbeterlijst 12): een verkeerd bestand dat Sonarr/Radarr
    na het veiligstellen weghaalden. Er wordt niets vervangen: staat er intussen weer een
    bestand op die plek, dan stopt het (``os.link`` overschrijft nooit)."""
    final, staged = Path(txn["final_path"]), Path(txn["staged_path"])
    journal(ctx, txn_id, "validate", "begin", empty_spot=True)
    _validate_paths(ctx, txn)
    if _stat(final) is not None:
        raise Abort("TARGET_EXISTS", f"Op de plek staat intussen weer een bestand "
                    f"({final.name}); er is niets overschreven")
    sst = _stat(staged)
    if sst is None or not stat_mod.S_ISREG(sst.mode):
        raise Abort("STAGED_MISSING", "Het bewaarde bestand ontbreekt in de quarantaine")
    if txn["staged_size"] is not None and sst.size != int(txn["staged_size"]):
        raise Abort("STAGED_CORRUPT", "Grootte van het bewaarde bestand klopt niet")
    staged_sha = await asyncio.to_thread(fsops.sha256_file, staged)
    if txn["staged_sha256"] and staged_sha != txn["staged_sha256"]:
        raise Abort("STAGED_CORRUPT", "sha256 van het bewaarde bestand klopt niet")
    with fsops.op("mkdir"):
        final.parent.mkdir(parents=True, exist_ok=True)
    link = fsops.same_device(staged, final.parent) and \
        not ctx.services.get("recovery_force_copy")
    reserve = int(ctx.config.value("recovery.min_free_gb")) * 1024**3
    if not link and fsops.free_bytes(final.parent) < sst.size + reserve:
        raise Abort("INSUFFICIENT_SPACE", "Onvoldoende vrije ruimte in de doelmap "
                    "(incl. reserve)")
    _state(ctx, txn_id, "validated", staged_sha256=staged_sha, staged_size=sst.size)
    hit("validate")
    if ctx.emergency_stopped():
        raise Abort("EMERGENCY_STOP", "Noodstop actief vóór terugzetten")
    digest = hashlib.sha256(os.fsencode(final.name)).hexdigest()[:12]
    temp = final.parent / f".mg-replace-{txn_id}-{digest}.part"
    _update(ctx, txn_id, temp_path=str(temp))
    journal(ctx, txn_id, "stage_temp", "begin", path=str(temp))
    temp_inode = await asyncio.to_thread(_stage_temp, staged, temp, staged_sha, sst, link)
    journal(ctx, txn_id, "stage_temp", "done", inode=temp_inode)
    _state(ctx, txn_id, "temp_ready")
    hit("stage_temp")
    if ctx.emergency_stopped():
        raise Abort("EMERGENCY_STOP", "Noodstop actief vóór terugzetten")
    journal(ctx, txn_id, "activate", "begin")
    try:
        with fsops.op("link"):
            os.link(temp, final)  # nooit overschrijven: bestaat het doel al, dan EEXIST
    except FileExistsError as exc:
        raise Abort("TARGET_EXISTS", f"Op de plek staat intussen weer een bestand "
                    f"({final.name}); er is niets overschreven") from exc
    with fsops.op("unlink"):
        os.unlink(temp)
    fsops.fsync_dir(final.parent)
    _state(ctx, txn_id, "activated")
    hit("activate")
    _record(ctx, txn_id)
    hit("record")
    await _refresh(ctx, txn_id)
    hit("refresh")
    _commit(ctx, txn_id, recovered=False)


ENV_ERRNOS = {errno.EPERM, errno.EACCES, errno.EROFS, errno.ENOSPC, errno.EDQUOT}


def environment_problem(exc: BaseException) -> str | None:
    """Dutch uitleg als de fout aan de opslag/rechten ligt (niet aan de encode)."""
    if not isinstance(exc, OSError) or exc.errno not in ENV_ERRNOS:
        return None
    where = Path(str(exc.filename)).parent if getattr(exc, "filename", None) else None
    place = f" op {where}" if where else ""
    step = fsops.op_of(exc)
    if step:
        place += f" bij '{fsops.OPS_NL.get(step, step)}'"
    if exc.errno in (errno.ENOSPC, errno.EDQUOT):
        return f"Onvoldoende ruimte{place}; MediaGuard probeert het later opnieuw"
    if exc.errno == errno.EROFS:
        return f"De map{place} is alleen-lezen gekoppeld; koppel de dataset schrijfbaar"
    if exc.errno == errno.EPERM:
        # schrijven lukt (Sonarr/Radarr kunnen het ook), maar ZFS-ACL's weigeren soms een
        # metadata-bewerking: geen rechtenadvies, gewoon later opnieuw
        return (f"Het bestandssysteem{place} weigerde de bewerking (EPERM); "
                "de encode is bewaard en MediaGuard probeert het opnieuw zodra de "
                "schrijftest van deze map slaagt")
    return (f"Geen schrijfrechten{place}. Geef gebruiker apps (568) schrijfrechten op die "
            "dataset (TrueNAS: Datasets → Edit Permissions); MediaGuard probeert het daarna "
            "vanzelf opnieuw")


def _secure_original(lib: Path, qpath: Path, st: FileStat, cross: bool
                     ) -> tuple[str, str | None]:
    with fsops.op("mkdir"):
        qpath.parent.mkdir(parents=True, exist_ok=True)
    if not cross:
        try:
            os.link(lib, qpath)
            fsops.fsync_dir(qpath.parent)
            return "link", None
        except OSError:
            LOGGER.info("hardlink naar quarantaine niet mogelijk; terugval op kopie")
    size, src_sha = fsops.copy_with_hash(lib, qpath)
    dst_sha = fsops.sha256_file(qpath)
    if size != st.size or dst_sha != src_sha:
        with contextlib.suppress(FileNotFoundError):
            qpath.unlink()
        raise OSError("quarantainekopie komt niet overeen met origineel (sha256)")
    fsops.fsync_dir(qpath.parent)
    return "copy", src_sha


def _stage_temp(staged: Path, temp: Path, expected_sha: str, ref: FileStat,
                link: bool) -> int:
    """Nieuw bestand in de doelmap zoals Radarr/Sonarr een import kopiëren: aanmaken (0666,
    umask + geërfde map-ACL bepalen de rechten), schrijven, fsync. Geen chmod/chown/utime:
    op TrueNAS-datasets met NFSv4-ACL's weigert ZFS die (EPERM) of wist chmod de geërfde ACL,
    waardoor Plex/Jellyfin het bestand niet meer zouden kunnen lezen."""
    del ref  # rechten komen van de map, net als bij een import door Sonarr/Radarr
    if link:
        with fsops.op("link"):
            os.link(staged, temp)
        fsops.fsync_dir(temp.parent)
        return os.stat(temp).st_ino
    size, sha = fsops.copy_with_hash(staged, temp, mode=fsops.ARR_CREATE_MODE)
    if sha != expected_sha:
        temp.unlink()
        raise OSError("tijdelijk bestand komt niet overeen met staged uitvoer (sha256)")
    # wat er op schijf staat opnieuw lezen (schrijffout tijdens het kopiëren uitsluiten)
    with fsops.op("readback"):
        on_disk = fsops.sha256_file(temp)
    if on_disk != expected_sha:
        temp.unlink()
        raise OSError("tijdelijk bestand op schijf is beschadigd (sha256 na schrijven)")
    fsops.fsync_dir(temp.parent)
    return os.stat(temp).st_ino


def _record(ctx: AppContext, txn_id: int) -> None:
    txn = load(ctx, txn_id)
    if txn["quarantine_item_id"]:
        ctx.db.execute("UPDATE quarantine_items SET status='restorable' WHERE id=? AND "
                       "status IN ('held','pending')", (txn["quarantine_item_id"],))
    if txn["kind"] == "restore" and txn["restore_of_item_id"]:
        ctx.db.execute("UPDATE quarantine_items SET status='restored', restored_at=? "
                       "WHERE id=?", (utcnow(), txn["restore_of_item_id"]))
    _state(ctx, txn_id, "recorded")


async def _refresh(ctx: AppContext, txn_id: int) -> None:
    txn = load(ctx, txn_id)
    refresh = loads(txn.get("refresh_json"), {}) or {}
    if refresh.get("command") and ctx.config.value("recovery.refresh_arr_after_commit"):
        try:
            await send_refresh(ctx, txn)
            # Bibliotheek in MediaGuard bijwerken zodra Sonarr/Radarr het nieuwe bestand
            # hebben gescand: anders bleef 'Remux-2160p · 54,5 GB' tot de volgende
            # geplande sync staan (rapport 4, S5)
            from datetime import UTC, datetime, timedelta

            jobs.enqueue(ctx.db, "library.sync", {}, instance_id=txn["instance_id"],
                         max_attempts=2, created_by="svc:recovery",
                         run_after=datetime.now(UTC) + timedelta(minutes=3),
                         idempotency_key=f"library.sync:after-replace:{txn_id}")
        except Exception as exc:  # noqa: BLE001 - refresh is niet-kritisch
            _warn(ctx, txn_id, f"Arr-refresh mislukt ({type(exc).__name__}); opnieuw gepland")
            jobs.enqueue(ctx.db, "recovery.refresh", {"transaction_id": txn_id},
                         instance_id=txn["instance_id"],
                         idempotency_key=f"recovery.refresh:{txn_id}", max_attempts=10)
    _state(ctx, txn_id, "refreshed")


async def send_refresh(ctx: AppContext, txn: dict[str, Any]) -> None:
    from mediaguard.policy.ports import arr_client

    refresh = loads(txn.get("refresh_json"), {}) or {}
    client = arr_client(ctx, txn["instance_id"])
    if client is None:
        raise RuntimeError("instance-registry niet beschikbaar")
    body = {k: v for k, v in refresh.items()
            if k not in ("command", "episode_ids", "movie_id_ref")}
    await client.command(refresh["command"], **body)


def _immediate(ctx: AppContext) -> bool:
    from mediaguard.recovery.service import immediate_mode

    try:
        return bool(immediate_mode(ctx))
    except Exception:  # noqa: BLE001 - instelling ontbreekt in oude databases
        return False


def _safety_hours(ctx: AppContext) -> int:
    from mediaguard.recovery.service import safety_net

    try:
        return safety_net(ctx)[0]
    except Exception:  # noqa: BLE001
        return 0


def _commit(ctx: AppContext, txn_id: int, *, recovered: bool) -> None:
    txn = load(ctx, txn_id)
    _state(ctx, txn_id, "committed", finished_at=utcnow())
    audit(ctx.db, actor="svc:recovery", action=f"recovery.{txn['kind']}_committed",
          target_type=txn["media_kind"] or "file", target_id=txn["provider_id"],
          instance_id=txn["instance_id"], correlation_id=txn["correlation_id"],
          reason="Afgerond na herstart (journal replay)" if recovered else None,
          old_value={"path": txn["library_path"], "quarantine": txn["quarantine_path"]},
          new_value={"path": txn["final_path"]},
          # alleen een hersteltip als er echt een origineel bewaard wordt (rapport 4, S12)
          undo_hint=None if (txn.get("kind") == "replace" and _immediate(ctx)
                             and not _safety_hours(ctx))
          or loads(txn.get("source_snapshot_json"), {}).get("empty")
          else "Herstel origineel via Quarantaine & herstel")
    locks.release(ctx.db, resource_for(txn), _owner(txn_id))
    ctx.events.publish("recovery.committed", {"transaction_id": txn_id,
                                              "kind": txn["kind"]},
                       instance_id=txn["instance_id"])


# ── reconciliatie (rollback / roll-forward) ──────────────────────────────────
def reconcile(ctx: AppContext, txn_id: int, *, failure: str | None = None,
              aborted: bool = False) -> str:
    """Breng een onderbroken/mislukte transactie naar een consistente eindtoestand.
    Wordt ook bij opstarten aangeroepen (journal replay). Idempotent."""
    txn = load(ctx, txn_id)
    if txn["state"] in TERMINAL:
        return str(txn["state"])
    journal(ctx, txn_id, "reconcile", "recover", from_state=txn["state"], failure=failure)
    lib, final = Path(txn["library_path"]), Path(txn["final_path"])
    snap = loads(txn["source_snapshot_json"], {})
    lib_st, final_st = _stat(lib), _stat(final)
    try:
        if txn["state"] in POST_ACTIVATION:
            return _roll_forward(ctx, txn)
        if snap.get("empty"):
            # terugzetten op een lege plek: stond ons bestand al op zijn plek, dan afronden
            temp_inode = _journal_detail(ctx, txn_id, "stage_temp").get("inode")
            if final_st is not None and temp_inode is not None and \
                    final_st.inode == int(temp_inode):
                return _roll_forward(ctx, txn)
        if not txn.get("quarantine_path"):
            # write-ahead: zonder quarantainepad is er nog niets op schijf aangeraakt
            return _rollback(ctx, txn, failure, aborted=aborted or txn["state"] in (
                "pending", "locked", "validated", "snapshotted"))
        temp_inode = _journal_detail(ctx, txn_id, "stage_temp").get("inode")
        replaced = final_st is not None and temp_inode is not None and \
            final_st.inode == int(temp_inode)
        if final == lib:
            if _matches_snapshot(lib_st, snap):
                return _rollback(ctx, txn, failure, aborted)
            if replaced:
                return _roll_forward(ctx, txn)
            if lib_st is None:
                return _restore_missing(ctx, txn, failure)
            return _conflict(ctx, txn, "Bibliotheekbestand is extern gewijzigd tijdens de "
                             "transactie; niets aangeraakt, origineel blijft in quarantaine")
        # extensiewissel (bv. .mp4 → .mkv)
        if _matches_snapshot(lib_st, snap):
            if replaced:
                os.unlink(final)  # alleen ons eigen, net geactiveerde bestand
                fsops.fsync_dir(final.parent)
            return _rollback(ctx, txn, failure, aborted)
        if lib_st is None and replaced:
            return _roll_forward(ctx, txn)
        if lib_st is None:
            return _restore_missing(ctx, txn, failure)
        return _conflict(ctx, txn, "Onverwachte toestand na extensiewissel")
    finally:
        latest = load(ctx, txn_id)
        if latest["state"] in TERMINAL and latest["state"] != "conflict":
            locks.release(ctx.db, resource_for(latest), _owner(txn_id))


def _remove_temp(txn: dict[str, Any]) -> None:
    temp = txn.get("temp_path")
    if temp and Path(temp).name.startswith(".mg-replace-"):
        with contextlib.suppress(FileNotFoundError):
            os.unlink(temp)


def _discard_quarantine(ctx: AppContext, txn: dict[str, Any]) -> None:
    """Alleen aanroepen als het origineel aantoonbaar nog op zijn pad staat. Ruimt ook de
    kopieën op die alleen in quarantine_items staan (write-ahead: het quarantinepad van de
    transactie was soms nog niet opgeslagen — zo bleven er in de herstartlus van 3 nov
    252 GB aan kopieën achter, rapport 6 / N9)."""
    from mediaguard.recovery import orphans

    orphans.discard_item_files(ctx, txn)
    ctx.db.execute("UPDATE quarantine_items SET status='discarded' WHERE transaction_id=? "
                   "AND status IN ('held','pending')", (txn["id"],))


def _rollback(ctx: AppContext, txn: dict[str, Any], failure: str | None,
              aborted: bool) -> str:
    _remove_temp(txn)
    _discard_quarantine(ctx, txn)
    state = "aborted" if aborted else "rolled_back"
    _update(ctx, txn["id"], state=state, finished_at=utcnow(),
            error=failure or "Onderbroken vóór activatie; origineel ongewijzigd")
    journal(ctx, txn["id"], state, "done", failure=failure)
    audit(ctx.db, actor="svc:recovery", action=f"recovery.{state}",
          target_type=txn["media_kind"] or "file", target_id=txn["provider_id"],
          instance_id=txn["instance_id"], reason=failure or "herstart",
          new_value={"path": txn["library_path"]})
    ctx.events.publish(f"recovery.{state}", {"transaction_id": txn["id"], "error": failure},
                       instance_id=txn["instance_id"])
    return state


def _roll_forward(ctx: AppContext, txn: dict[str, Any]) -> str:
    lib, final = Path(txn["library_path"]), Path(txn["final_path"])
    final_st = _stat(final)
    if final_st is None or (txn["staged_size"] is not None
                            and final_st.size != int(txn["staged_size"])):
        return _conflict(ctx, txn, "Geactiveerd bestand ontbreekt of heeft verkeerde grootte")
    if final != lib and _stat(lib) is not None:
        qpath = txn.get("quarantine_path")
        qst = _stat(Path(qpath)) if qpath else None
        snap = loads(txn["source_snapshot_json"], {})
        if qst is None or qst.size != int(snap["size"]):
            return _conflict(ctx, txn, "Origineel niet aantoonbaar in quarantaine; oude "
                             "bestandsnaam blijft staan")
        os.unlink(lib)
        fsops.fsync_dir(lib.parent)
    _remove_temp(txn)
    if txn["state"] not in ("recorded", "refreshed"):
        _record(ctx, txn["id"])
    latest = load(ctx, txn["id"])
    if latest["state"] != "refreshed" and loads(latest.get("refresh_json"), {}):
        jobs.enqueue(ctx.db, "recovery.refresh", {"transaction_id": txn["id"]},
                     instance_id=txn["instance_id"],
                     idempotency_key=f"recovery.refresh:{txn['id']}", max_attempts=10)
    _commit(ctx, txn["id"], recovered=True)
    return "committed"


def _restore_missing(ctx: AppContext, txn: dict[str, Any], failure: str | None) -> str:
    """Bibliotheekpad leeg: zet het origineel uit quarantaine terug (nooit de enige kopie
    verliezen)."""
    qpath = Path(txn["quarantine_path"]) if txn.get("quarantine_path") else None
    snap = loads(txn["source_snapshot_json"], {})
    qst = _stat(qpath) if qpath else None
    if qpath is None or qst is None or qst.size != int(snap["size"]):
        return _conflict(ctx, txn, "Bibliotheekbestand ontbreekt en geen geldige "
                         "quarantainekopie gevonden")
    lib = Path(txn["library_path"])
    tmp = lib.parent / f".mg-restore-{txn['id']}.part"
    with contextlib.suppress(FileNotFoundError):
        os.unlink(tmp)
    if fsops.same_device(qpath, lib.parent):
        os.link(qpath, tmp)
    else:
        size, sha = fsops.copy_with_hash(qpath, tmp)
        if txn.get("original_sha256") and sha != txn["original_sha256"]:
            os.unlink(tmp)
            return _conflict(ctx, txn, "Quarantainekopie beschadigd (sha256)")
    os.replace(tmp, lib)
    fsops.fsync_dir(lib.parent)
    _remove_temp(txn)
    ctx.db.execute("UPDATE quarantine_items SET status='restored', restored_at=? WHERE "
                   "transaction_id=? AND status IN ('held','pending')", (utcnow(), txn["id"]))
    msg = (failure + "; " if failure else "") + "origineel teruggezet uit quarantaine"
    _update(ctx, txn["id"], state="rolled_back", finished_at=utcnow(), error=msg)
    journal(ctx, txn["id"], "rolled_back", "done", restored_from=str(qpath))
    audit(ctx.db, actor="svc:recovery", action="recovery.restored_missing",
          target_type=txn["media_kind"] or "file", target_id=txn["provider_id"],
          instance_id=txn["instance_id"], reason=msg)
    return "rolled_back"


def _conflict(ctx: AppContext, txn: dict[str, Any], message: str) -> str:
    _remove_temp(txn)  # alleen ons eigen tijdelijke bestand; quarantaine blijft staan
    _update(ctx, txn["id"], state="conflict", error=message, finished_at=utcnow())
    journal(ctx, txn["id"], "conflict", "done", message=message)
    audit(ctx.db, actor="svc:recovery", action="recovery.conflict",
          target_type=txn["media_kind"] or "file", target_id=txn["provider_id"],
          instance_id=txn["instance_id"], reason=message)
    ctx.db.execute(
        "INSERT INTO notifications(ts,severity,title,body,instance_id) VALUES (?,?,?,?,?)",
        (utcnow(), "error", "Hersteltransactie vraagt handmatige beoordeling", message,
         txn["instance_id"]))
    return "conflict"


def recover_pending(ctx: AppContext) -> list[dict[str, Any]]:
    """Journal replay bij opstarten van de recovery-rol."""
    out = []
    for row in ctx.db.fetch_all(
            "SELECT id, state FROM replacement_transactions WHERE state NOT IN "
            "('committed','rolled_back','aborted','conflict','pending') ORDER BY id"):
        state = reconcile(ctx, int(row["id"]), failure="Onderbroken (herstart/stroomstoring)")
        out.append({"id": int(row["id"]), "from": row["state"], "to": state})
    return out
