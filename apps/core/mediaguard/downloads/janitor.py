"""Downloadmappen opruimen: automatisch, veilig en terug te draaien.

Sonarr/Radarr ruimen alleen op wat ze zelf geïmporteerd hebben (``removeCompletedDownloads``).
Blijven liggen: downloads die nooit geïmporteerd zijn, resten van mislukte SABnzbd-jobs,
mappen die geen enkele client nog kent, torrents die klaar zijn met delen, en
MediaGuard's eigen tijdelijke bestanden. Deze opruimer pakt dat aan.

Veiligheid (in deze volgorde):

1. Alleen binnen de downloadmappen van de clients (per MediaGuard-categorie + de
   'incomplete'-map van SABnzbd). Een downloadmap die een bibliotheekmap raakt wordt
   geweigerd; paden worden eerst opgelost (symlinks) en moeten er écht binnen liggen.
2. Nooit iets wat een client nog verwerkt, wat in een Sonarr/Radarr-wachtrij staat (ook van
   de OUDE installaties tijdens de migratie) of een torrent vóór zijn deellimiet.
   Privé-trackers zonder ingestelde deellimiet: nooit.
3. Kan MediaGuard een client of instantie niet bereiken, dan wordt die ronde niets
   weggehaald (eigendom is dan onbekend).
4. Niets wordt meteen gewist: het gaat via ``rename`` naar ``<map>/.mediaguard-prullenbak/
   <datum>/`` (zelfde bestandssysteem) en wordt pas na de bewaartermijn definitief
   verwijderd — eerder alleen als de schijf bijna vol is. Elke stap staat in de audit en is
   tot dan terug te zetten.
"""

from __future__ import annotations

import asyncio
import contextlib
import logging
import os
import shutil
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import TYPE_CHECKING, Any

from fastapi import APIRouter, Depends, HTTPException

from mediaguard import emergency, jobs, taskwatch
from mediaguard.audit import audit
from mediaguard.auth import Principal, require_admin, require_operator, require_viewer
from mediaguard.context import AppContext, get_app_context
from mediaguard.db import dumps, loads, utcnow
from mediaguard.downloads.clients import ClientItem, ClientView, QbitClient, Root, SabClient
from mediaguard.settings_registry import REGISTRY, SettingDef

if TYPE_CHECKING:
    from mediaguard.provisioning.store import ProvisioningConfig

LOGGER = logging.getLogger(__name__)
router = APIRouter(prefix="/api/v1/downloads", tags=["downloads"])
CATEGORY = "Downloads opruimen"
TRASH_DIR = ".mediaguard-prullenbak"
JOB_TYPE = "storage.download_cleanup"
HISTORY_DAYS = 45


def register_settings() -> None:
    REGISTRY.register(
        SettingDef("downloads.cleanup_enabled", "bool", True, CATEGORY,
                   "Oude downloads automatisch opruimen", "Clean up old downloads",
                   help_nl="Geïmporteerde, mislukte en vergeten downloads gaan eerst naar een "
                           "prullenbak in dezelfde downloadmap en worden pas na de bewaartermijn "
                           "verwijderd. Je bibliotheek wordt nooit aangeraakt."),
        SettingDef("downloads.trash_days", "int", 7, CATEGORY,
                   "Prullenbak bewaren (dagen)", "Keep trash (days)", minimum=1, maximum=90,
                   unit="dagen", advanced=True),
        SettingDef("downloads.orphan_days", "int", 3, CATEGORY,
                   "Onbekende mappen opruimen na (dagen)", "Orphans after (days)",
                   minimum=1, maximum=60, unit="dagen", advanced=True,
                   help_nl="Mappen die geen enkele downloadclient of Sonarr/Radarr kent."),
        SettingDef("downloads.unimported_days", "int", 7, CATEGORY,
                   "Nooit geïmporteerde downloads opruimen na (dagen)",
                   "Never-imported downloads after (days)", minimum=2, maximum=90,
                   unit="dagen", advanced=True),
        SettingDef("downloads.public_seed_hours", "int", 24, CATEGORY,
                   "Openbare torrents minimaal delen (uur)", "Seed public torrents (hours)",
                   minimum=0, maximum=24 * 60, unit="uur", advanced=True,
                   help_nl="Alleen voor openbare torrents zonder eigen deellimiet. Privé-"
                           "trackers: MediaGuard haalt een torrent pas weg als de deellimiet "
                           "uit qBittorrent gehaald is, en nooit zonder zo'n limiet."),
        SettingDef("downloads.cleanup_interval_hours", "int", 4, CATEGORY,
                   "Opruimen elke (uur)", "Clean every (hours)", minimum=1, maximum=48,
                   unit="uur", advanced=True),
        SettingDef("downloads.min_free_percent", "int", 5, CATEGORY,
                   "Prullenbak eerder legen onder (% vrij)", "Purge trash early below (% free)",
                   minimum=1, maximum=50, unit="%", advanced=True),
    )


def _v(ctx: AppContext, key: str) -> Any:
    return ctx.config.value(f"downloads.{key}")


# -- paden ---------------------------------------------------------------------------------
def real(path: str | os.PathLike[str]) -> Path:
    return Path(os.path.realpath(path))


def inside(child: Path, parent: Path) -> bool:
    """Strikt binnen (niet gelijk)."""
    return child != parent and parent in child.parents


def overlaps(a: Path, b: Path) -> bool:
    return a == b or a in b.parents or b in a.parents


def _stat_tree(path: Path) -> tuple[int, int, bool]:
    """(totale grootte, vrij te maken bytes, hardlink in gebruik)."""
    total = freed = 0
    linked = False
    try:
        st = path.lstat()
    except OSError:
        return 0, 0, False
    if not path.is_dir() or path.is_symlink():
        n = st.st_nlink if not path.is_symlink() else 1
        return st.st_size, (st.st_size if n <= 1 else 0), n > 1
    for root, _dirs, files in os.walk(path):
        for name in files:
            try:
                fst = os.lstat(os.path.join(root, name))
            except OSError:
                continue
            total += fst.st_size
            if fst.st_nlink > 1:
                linked = True
            else:
                freed += fst.st_size
    return total, freed, linked


# -- momentopname ----------------------------------------------------------------------------
@dataclass
class Snapshot:
    roots: list[Root]
    library_roots: list[str]
    items: list[ClientItem]
    queue_ids: set[str] = field(default_factory=set)
    imported_ids: set[str] = field(default_factory=set)
    failed_handled_ids: set[str] = field(default_factory=set)
    managed: set[str] = field(default_factory=set)
    errors: list[str] = field(default_factory=list)
    # door een client gemelde mappen die MediaGuard niet kon vertalen ("pad niet herkend")
    unrecognized: list[dict[str, str]] = field(default_factory=list)


@dataclass
class Action:
    kind: str  # "trash" | "torrent" (torrent weghalen, data eventueel naar prullenbak)
    reason: str
    path: Path | None = None
    root: Path | None = None
    item: ClientItem | None = None
    trash_data: bool = True


@dataclass
class Plan:
    actions: list[Action] = field(default_factory=list)
    refused_roots: list[dict[str, str]] = field(default_factory=list)
    kept: dict[str, int] = field(default_factory=dict)

    def keep(self, why: str) -> None:
        self.kept[why] = self.kept.get(why, 0) + 1


def safe_roots(snap: Snapshot) -> tuple[list[tuple[Path, Root]], list[dict[str, str]]]:
    libs = [real(p) for p in snap.library_roots if p]
    ok: list[tuple[Path, Root]] = []
    refused: list[dict[str, str]] = []
    for r in snap.roots:
        p = real(r.path)
        if len(p.parts) <= 2:
            refused.append({"path": r.path, "reason": "te algemene map"})
        elif any(overlaps(p, lib) for lib in libs):
            refused.append({"path": r.path, "reason": "raakt een bibliotheekmap"})
        elif not p.is_dir():
            refused.append({"path": r.path, "reason": "pad niet herkend"})
        elif all(p != q for q, _ in ok):
            ok.append((p, r))
    return ok, refused


def _root_of(path: Path, roots: list[tuple[Path, Root]]) -> Path | None:
    hits = [rp for rp, _ in roots if inside(path, rp)]
    return max(hits, key=lambda x: len(x.parts)) if hits else None


def plan(ctx: AppContext, snap: Snapshot, now: datetime | None = None) -> Plan:  # noqa: C901
    from mediaguard.downloads import recovery

    now = now or datetime.now(UTC)
    out = Plan()
    roots, out.refused_roots = safe_roots(snap)
    out.refused_roots += snap.unrecognized
    known = [real(i.path) for i in snap.items if i.path]
    unimported_after = timedelta(days=int(_v(ctx, "unimported_days")))
    taken: set[Path] = set()
    for it in snap.items:
        if it.category not in snap.managed:
            continue
        if it.id in snap.queue_ids:
            out.keep("staat nog in een Sonarr/Radarr-wachtrij")
            continue
        if it.active:
            out.keep("client is er nog mee bezig")
            continue
        if it.kind == "torrent" and not it.seeding_done:
            out.keep(it.seeding_reason or "deellimiet nog niet gehaald")
            continue
        old = it.completed_at is not None and now - it.completed_at >= unimported_after
        if it.id in snap.imported_ids:
            reason = "geïmporteerd door Sonarr/Radarr"
        elif it.failed and it.id in snap.failed_handled_ids:
            reason = "mislukt; Sonarr/Radarr heeft de release geblokkeerd en zoekt opnieuw"
        elif old and not it.failed and not recovery.janitor_may_remove(ctx, it.id):
            # eerst moet het herstelvangnet het geprobeerd hebben (6 nov)
            out.keep("wacht op herstelpoging (alsnog importeren)")
            continue
        elif old:
            reason = ("mislukt en al lang niet meer gebruikt" if it.failed else
                      f"voltooid maar nooit geïmporteerd (sinds "
                      f"{it.completed_at:%d-%m})")  # type: ignore[union-attr]
        else:
            out.keep("nog niet geïmporteerd")
            continue
        path = real(it.path) if it.path else None
        root = _root_of(path, roots) if path else None
        if path is not None and path.exists() and root is None and it.kind != "torrent":
            out.keep("buiten de toegestane downloadmappen")
            continue
        if it.kind == "torrent":
            linked = bool(path and path.exists() and _stat_tree(path)[2])
            out.actions.append(Action("torrent", reason, path if root else None, root, it,
                                      trash_data=bool(root and path and path.exists()
                                                      and not linked)))
            if path:
                taken.add(path)
        elif path is not None and root is not None and path.exists():
            out.actions.append(Action("trash", reason, path, root, it))
            taken.add(path)
    _plan_orphans(ctx, snap, roots, known, taken, now, out)
    return out


def _plan_orphans(ctx: AppContext, snap: Snapshot, roots: list[tuple[Path, Root]],
                  known: list[Path], taken: set[Path], now: datetime, out: Plan) -> None:
    after = timedelta(days=int(_v(ctx, "orphan_days")))
    root_paths = [rp for rp, _ in roots]
    # extra vangnet: een naam die een client kent is nooit 'onbekend', ook als zijn pad
    # (andere mount) niet te vertalen was
    known_names = {Path(i.path).name for i in snap.items if i.path} | \
        {i.name for i in snap.items if i.name}
    seen_now: set[str] = set()
    for rp, _r in roots:
        try:
            entries = list(os.scandir(rp))
        except OSError:
            continue
        for e in entries:
            if e.name.startswith("."):
                continue
            p = real(e.path)
            if e.name in known_names:
                continue
            if p in taken or any(overlaps(p, k) for k in known) or \
                    any(overlaps(p, other) for other in root_paths if other != rp):
                continue
            key = str(p)
            seen_now.add(key)
            row = ctx.db.fetch_one("SELECT first_seen FROM download_janitor_seen WHERE path=?",
                                   (key,))
            first = datetime.fromisoformat(row["first_seen"]) if row else now
            if row is None:
                ctx.db.execute("INSERT INTO download_janitor_seen(path,first_seen) VALUES (?,?)",
                               (key, now.isoformat(timespec="seconds")))
            try:
                mtime = datetime.fromtimestamp(e.stat(follow_symlinks=False).st_mtime, UTC)
            except OSError:
                continue
            if now - first >= after and now - mtime >= after:
                out.actions.append(Action(
                    "trash", f"onbekend bij alle downloadclients en Sonarr/Radarr sinds "
                    f"{first:%d-%m}", p, rp))
            else:
                out.keep("onbekende map, nog te nieuw")
    # vergeten wat niet meer bestaat of weer bekend is
    for row in ctx.db.fetch_all("SELECT path FROM download_janitor_seen"):
        if row["path"] not in seen_now:
            ctx.db.execute("DELETE FROM download_janitor_seen WHERE path=?", (row["path"],))


# -- uitvoeren -------------------------------------------------------------------------------
def _trash(ctx: AppContext, path: Path, root: Path, reason: str, *, actor: str,
           item: ClientItem | None = None, now: datetime | None = None) -> dict[str, Any]:
    now = now or datetime.now(UTC)
    if not inside(real(path), root) or real(path).name.startswith("."):
        raise PermissionError(f"{path} ligt niet binnen {root}")
    size, freed, _ = _stat_tree(path)
    dest_dir = root / TRASH_DIR / now.strftime("%Y-%m-%d")
    dest_dir.mkdir(parents=True, exist_ok=True)
    dest, n = dest_dir / path.name, 2
    while dest.exists() or dest.is_symlink():
        dest, n = dest_dir / f"{path.name}-{n}", n + 1
    os.rename(path, dest)  # zelfde bestandssysteem: atomair, niets gekopieerd
    tid = ctx.db.execute_insert(
        "INSERT INTO download_janitor_trash(root,original_path,trash_path,size,freed,reason,"
        "client,download_id,moved_at) VALUES (?,?,?,?,?,?,?,?,?)",
        (str(root), str(path), str(dest), size, freed, reason,
         item.client if item else None, item.id if item else None,
         now.isoformat(timespec="seconds")))
    audit(ctx.db, actor=actor, action="downloads.trashed", target_type="download_path",
          target_id=tid, new_value={"path": str(path), "size": size, "reason": reason})
    return {"id": tid, "path": str(path), "size": size, "freed": freed, "reason": reason}


async def execute(ctx: AppContext, p: Plan, clients: dict[str, Any], *, actor: str
                  ) -> dict[str, Any]:
    done: list[dict[str, Any]] = []
    errors: list[str] = []
    for a in p.actions:
        try:
            if a.kind == "torrent" and a.item is not None:
                q = clients.get(a.item.client)
                if q is None:
                    continue
                await q.remove(a.item.id)  # zonder bestanden
                audit(ctx.db, actor=actor, action="downloads.torrent_removed",
                      target_type="torrent", target_id=a.item.id,
                      new_value={"name": a.item.name, "reason": a.reason,
                                 "data_to_trash": a.trash_data})
                if a.trash_data and a.path is not None and a.root is not None:
                    done.append(await asyncio.to_thread(_trash, ctx, a.path, a.root, a.reason,
                                                        actor=actor, item=a.item))
                else:
                    done.append({"path": a.item.name, "size": 0, "freed": 0,
                                 "reason": a.reason + " (alleen torrent; bestanden zijn "
                                 "gekoppeld aan de bibliotheek)"})
            elif a.kind == "trash" and a.path is not None and a.root is not None:
                done.append(await asyncio.to_thread(_trash, ctx, a.path, a.root, a.reason,
                                                    actor=actor, item=a.item))
                if a.item is not None and a.item.kind == "nzb":
                    sab = clients.get(a.item.client)
                    if sab is not None:
                        with contextlib.suppress(Exception):  # alleen lijst opschonen
                            await sab.delete_history(a.item.id)
        except Exception as exc:  # noqa: BLE001 - per item; de rest gaat door
            errors.append(f"{a.path or (a.item.name if a.item else '?')}: {exc}")
            LOGGER.info("opruimen %s overgeslagen: %s", a.path, exc)
    return {"done": done, "errors": errors}


def _rm(path: str) -> None:
    p = Path(path)
    if p.is_dir() and not p.is_symlink():
        shutil.rmtree(p, ignore_errors=True)
    else:
        with contextlib.suppress(FileNotFoundError):
            p.unlink()


def purge(ctx: AppContext, now: datetime | None = None, *, actor: str = "svc:downloads"
          ) -> dict[str, int]:
    """Prullenbak legen na de bewaartermijn; bij weinig vrije ruimte eerder (oudste eerst)."""
    now = now or datetime.now(UTC)
    cutoff = (now - timedelta(days=int(_v(ctx, "trash_days")))).isoformat(timespec="seconds")
    rows = ctx.db.fetch_dicts(
        "SELECT * FROM download_janitor_trash WHERE purged_at IS NULL AND restored_at IS NULL "
        "ORDER BY moved_at")
    purged = freed = 0
    min_free = float(_v(ctx, "min_free_percent"))
    for r in rows:
        early = False
        if r["moved_at"] >= cutoff:
            try:
                du = shutil.disk_usage(r["root"])
                early = du.total > 0 and du.free / du.total * 100 < min_free
            except OSError:
                early = False
            if not early:
                continue
        trash = Path(r["trash_path"])
        if TRASH_DIR not in trash.parts:
            continue  # nooit buiten de prullenbak verwijderen
        _rm(str(trash))
        ctx.db.execute("UPDATE download_janitor_trash SET purged_at=? WHERE id=?",
                       (now.isoformat(timespec="seconds"), r["id"]))
        audit(ctx.db, actor=actor, action="downloads.purged", target_type="download_path",
              target_id=r["id"], new_value={"path": r["original_path"], "size": r["size"],
                                            "early_low_space": early})
        purged += 1
        freed += int(r["freed"] or 0)
    # lege datummappen opruimen
    for root in {r["root"] for r in rows}:
        base = Path(root) / TRASH_DIR
        for d in base.glob("*") if base.is_dir() else []:
            with contextlib.suppress(OSError):
                d.rmdir()
    return {"purged": purged, "freed": freed}


def restore(ctx: AppContext, trash_id: int, *, actor: str) -> dict[str, Any]:
    r = ctx.db.fetch_one("SELECT * FROM download_janitor_trash WHERE id=?", (trash_id,))
    if r is None:
        raise KeyError(trash_id)
    if r["purged_at"] or r["restored_at"]:
        raise ValueError("Al definitief verwijderd of al teruggezet")
    orig, trash = Path(r["original_path"]), Path(r["trash_path"])
    if orig.exists():
        raise ValueError("Op de oorspronkelijke plek staat al iets")
    orig.parent.mkdir(parents=True, exist_ok=True)
    os.rename(trash, orig)
    ctx.db.execute("UPDATE download_janitor_trash SET restored_at=? WHERE id=?",
                   (utcnow(), trash_id))
    audit(ctx.db, actor=actor, action="downloads.restored", target_type="download_path",
          target_id=trash_id, new_value={"path": str(orig)})
    return {"restored": True, "path": str(orig)}


def own_leftovers(ctx: AppContext, now: datetime | None = None, *,
                  actor: str = "svc:downloads") -> int:
    """MediaGuard's eigen tijdelijke vervangbestanden (.mg-replace-*.part) van afgeronde
    transacties, ouder dan een dag. Het zijn kopieën van een AV1-resultaat, nooit media."""
    from mediaguard.recovery.executor import TERMINAL

    now = now or datetime.now(UTC)
    cutoff = (now - timedelta(days=1)).isoformat(timespec="seconds")
    removed = 0
    marks = ",".join("?" for _ in TERMINAL)
    for r in ctx.db.fetch_all(
            f"SELECT id,temp_path FROM replacement_transactions WHERE temp_path IS NOT NULL "  # noqa: S608
            f"AND state IN ({marks}) AND updated_at<?", (*sorted(TERMINAL), cutoff)):
        temp = Path(r["temp_path"])
        if not temp.name.startswith(".mg-replace-") or not temp.name.endswith(".part"):
            continue
        try:
            temp.unlink()
        except FileNotFoundError:
            continue
        except OSError as exc:
            LOGGER.info("tijdelijk bestand %s niet verwijderd: %s", temp, exc)
            continue
        audit(ctx.db, actor=actor, action="downloads.temp_removed", target_type="transaction",
              target_id=r["id"], new_value={"path": str(temp)})
        removed += 1
    return removed


# -- verzamelen ------------------------------------------------------------------------------
def managed_categories(ctx: AppContext, cfg: ProvisioningConfig) -> set[str]:
    from mediaguard.instances.registry import list_instances
    from mediaguard.provisioning import store
    from mediaguard.provisioning import templates as T

    cats = set(T.DEFAULT_CATEGORY.values())
    cats |= {f"mso-{c}" for c in T.DEFAULT_CATEGORY.values()}
    for inst in list_instances(ctx):
        cats.add(store.category_for(ctx, inst.id, inst.media_type, cfg))
        for dc in cfg.download_clients:
            cats.add(store.category_for(ctx, inst.id, inst.media_type, cfg, dc))
    return {c for c in cats if c}


async def _arr_ids(ctx: AppContext, snap: Snapshot) -> None:
    """Wachtrij- en historie-ID's van de MediaGuard-forks én de oude live installaties."""
    from mediaguard.instances.registry import arr_client, list_instances
    from mediaguard.migration.sources import list_sources, source_client

    since = (datetime.now(UTC) - timedelta(days=HISTORY_DAYS)).isoformat(timespec="seconds")
    clients: list[Any] = [arr_client(ctx, i.id) for i in list_instances(ctx)]
    for s in list_sources(ctx):
        if s.get("kind") == "live":
            try:
                clients.append(source_client(ctx, s["id"]))
            except (KeyError, ValueError):
                continue
    for c in clients:
        async with c as client:
            for q in await client.queue(all_pages=True) or []:
                if q.get("downloadId"):
                    snap.queue_ids.add(str(q["downloadId"]).upper())
            for h in await client.history_since(since) or []:
                did = str(h.get("downloadId") or "").upper()
                ev = str(h.get("eventType") or "")
                if did and ev in ("downloadFolderImported", "3"):
                    snap.imported_ids.add(did)
                elif did and ev in ("downloadFailed", "4"):
                    snap.failed_handled_ids.add(did)
            with contextlib.suppress(Exception):
                for rf in await client.root_folders() or []:
                    if rf.get("path"):
                        snap.library_roots.append(str(rf["path"]))


async def gather(ctx: AppContext, *, managed_override: set[str] | None = None
                 ) -> tuple[Snapshot, dict[str, Any]]:
    from mediaguard.provisioning import store

    cfg = store.load(ctx)
    managed = managed_override if managed_override is not None else \
        managed_categories(ctx, cfg)
    snap = Snapshot(roots=[], library_roots=[], items=[], managed=managed)
    snap.library_roots += [str(r["root_path"]) for r in ctx.db.fetch_all(
        "SELECT DISTINCT root_path FROM instance_root_mappings")]
    clients: dict[str, Any] = {}
    seed_hours = float(_v(ctx, "public_seed_hours"))
    views: list[tuple[str, ClientView]] = []
    for dc in cfg.download_clients:
        if not dc.enabled:
            continue
        view: ClientView
        if dc.implementation == "Sabnzbd":
            sab = SabClient(ctx, dc, ctx.secrets.get(store.dc_secret(dc.name, "api_key")))
            view = await sab.view(snap.managed)
            clients[dc.name] = sab
        elif dc.implementation == "QBittorrent":
            qb = QbitClient(ctx, dc, ctx.secrets.get(store.dc_secret(dc.name, "password")))
            await qb.__aenter__()
            ctx.services.setdefault("_janitor_open", []).append(qb)
            view = await qb.view(snap.managed, seed_hours)
            clients[dc.name] = qb
        else:
            continue  # andere clients: alleen hun eigen opruiming
        views.append((dc.host, view))
    await _arr_ids(ctx, snap)
    rpm = await _remote_path_mappings(ctx)
    for host, view in views:
        translate_view(ctx, view, host, rpm, snap)
        snap.roots += view.roots
        snap.items += view.items
    return snap, clients


async def _remote_path_mappings(ctx: AppContext) -> list[dict[str, Any]]:
    """Remote path mappings van de MediaGuard-forks (zelfde container, dus zelfde paden)."""
    from mediaguard.instances.registry import arr_client, list_instances

    rows: list[dict[str, Any]] = []
    for inst in list_instances(ctx):
        with contextlib.suppress(Exception):
            async with arr_client(ctx, inst.id) as client:
                rows += await client.get("remotepathmapping") or []
    return rows


def search_bases(ctx: AppContext, snap: Snapshot) -> list[Path]:
    override = ctx.services.get("download_janitor_search_bases")
    if override is not None:
        return [Path(p) for p in override]
    bases = {Path("/data")}
    for lib in snap.library_roots:
        parts = Path(lib).parts
        if len(parts) >= 2:
            bases.add(Path(*parts[:2]))
    return sorted(b for b in bases if b.is_dir())


def translate_view(ctx: AppContext, view: ClientView, host: str,
                   rpm: list[dict[str, Any]], snap: Snapshot) -> None:
    """Zet de door de client gemelde paden om naar MediaGuard's paden (in-place)."""
    from mediaguard.downloads import paths as P

    mapping = P.from_remote_path_mappings(rpm, host)
    cache: dict[str, dict[str, str]] = ctx.state_get(P.STATE_KEY, {}) or {}
    mine = dict(cache.get(view.name) or {})
    bases = search_bases(ctx, snap)
    kept: list[Root] = []
    for root in view.roots:
        local = P.apply(root.path, mapping)
        how = "remote path mapping"
        if not (local and Path(local).is_dir()):
            local, how = None, ""
            cached = mine.get(root.path)
            if cached and Path(cached).is_dir():
                local, how = cached, "onthouden"
            else:
                names = [Path(i.path).name for i in view.items if i.path and
                         str(Path(i.path).parent).rstrip("/") == root.path.rstrip("/")]
                local, how = P.detect(root.path, names, bases)
        if not local:
            snap.unrecognized.append({"path": root.path, "client": view.name,
                                      "reason": how or "pad niet herkend"})
            mine.pop(root.path, None)
            continue
        if local != root.path:
            mapping.setdefault(root.path.rstrip("/") or "/", local)
            mine[root.path] = local
        kept.append(Root(local, root.client, root.kind, root.category))
    view.roots = kept
    for it in view.items:
        it.path = P.apply(it.path, mapping)
    cache[view.name] = mine
    ctx.state_set(P.STATE_KEY, cache)


# -- één ronde -------------------------------------------------------------------------------
def _gb(n: int) -> str:
    return f"{n / 1024 ** 3:.1f}".replace(".", ",") + " GB"


async def run_once(ctx: AppContext, *, actor: str = "svc:downloads",
                   gatherer: Any = None) -> dict[str, Any]:
    if emergency.skip(ctx, "opruimen van downloads"):
        return {"status": "skipped", "trashed": 0, "purged": 0, "freed_bytes": 0,
                "temp_removed": 0, "errors": [], "kept": {}, "refused_roots": [],
                "summary_nl": emergency.message_nl("opruimen")}
    started = utcnow()
    run_id = ctx.db.execute_insert(
        "INSERT INTO download_janitor_runs(started_at,status) VALUES (?, 'running')", (started,))
    summary: dict[str, Any] = {"trashed": 0, "purged": 0, "freed_bytes": 0, "temp_removed": 0,
                               "errors": [], "kept": {}, "refused_roots": []}
    status = "ok"
    try:
        summary["temp_removed"] = await asyncio.to_thread(own_leftovers, ctx)
        if not bool(_v(ctx, "cleanup_enabled")):
            status = "disabled"
        else:
            try:
                snap, clients = await (gatherer or gather)(ctx)
            except Exception as exc:  # noqa: BLE001 - zonder volledig beeld: niets weghalen
                status = "skipped"
                summary["errors"].append(f"Niet alles bereikbaar, deze ronde niets weggehaald: "
                                         f"{exc}"[:300])
            else:
                # voor het opslaglogboek: welke downloadmappen MediaGuard lokaal ziet
                ctx.state_set("download_roots_local", [
                    {"path": r.path, "client": r.client, "kind": r.kind, "category": r.category}
                    for r in snap.roots])
                p = await asyncio.to_thread(plan, ctx, snap)
                res = await execute(ctx, p, clients, actor=actor)
                summary.update(trashed=len(res["done"]), kept=p.kept,
                               refused_roots=p.refused_roots, errors=res["errors"],
                               items=res["done"][:50])
                summary["freed_bytes"] += sum(int(d.get("freed") or 0) for d in res["done"])
        pg = await asyncio.to_thread(purge, ctx)
        summary["purged"] = pg["purged"]
    finally:
        for qb in ctx.services.pop("_janitor_open", []):
            with contextlib.suppress(Exception):
                await qb.__aexit__(None, None, None)
    n = summary["trashed"]
    summary["summary_nl"] = (
        f"Opgeruimd: {n} oude download{'s' if n != 1 else ''}, "
        f"{_gb(summary['freed_bytes'])} vrijgemaakt" if n else
        ("Opruimen overgeslagen: " + summary["errors"][0] if status == "skipped"
         else "Niets op te ruimen"))
    ctx.db.execute(
        "UPDATE download_janitor_runs SET finished_at=?, status=?, trashed=?, purged=?, "
        "freed_bytes=?, summary_json=? WHERE id=?",
        (utcnow(), status, n, summary["purged"], summary["freed_bytes"], dumps(summary), run_id))
    return {"status": status, **summary}


@jobs.job_handler(JOB_TYPE)
async def _job(jc: jobs.JobContext) -> dict[str, Any]:
    res = await run_once(jc.app, actor="svc:downloads")
    jc.log(res["summary_nl"])
    for e in res.get("errors", [])[:20]:
        jc.log(e, "warning")
    return {"summary_nl": res["summary_nl"], "freed_bytes": res["freed_bytes"],
            "trashed": res["trashed"], "purged": res["purged"]}


def freed_on(ctx: AppContext, day: str, next_day: str) -> dict[str, int]:
    """Voor de uitleg 'waarom veranderde de vrije ruimte?' (rapport 3, R19)."""
    row = ctx.db.fetch_one(
        "SELECT COUNT(*) AS n, COALESCE(SUM(freed),0) AS b FROM download_janitor_trash "
        "WHERE purged_at>=? AND purged_at<?", (day, next_day))
    return {"items": int(row["n"]) if row else 0, "bytes": int(row["b"]) if row else 0}


# -- API ---------------------------------------------------------------------------------------
@router.get("/cleanup")
def cleanup_status(_: Principal = Depends(require_viewer),
                   ctx: AppContext = Depends(get_app_context)) -> dict[str, Any]:
    last = ctx.db.fetch_one("SELECT * FROM download_janitor_runs ORDER BY id DESC LIMIT 1")
    trash = ctx.db.fetch_dicts(
        "SELECT id,original_path,size,freed,reason,moved_at,client FROM download_janitor_trash "
        "WHERE purged_at IS NULL AND restored_at IS NULL ORDER BY moved_at DESC LIMIT 200")
    total = ctx.db.fetch_one(
        "SELECT COALESCE(SUM(freed),0) AS b, COUNT(*) AS n FROM download_janitor_trash "
        "WHERE purged_at IS NOT NULL")
    days = int(_v(ctx, "trash_days"))
    for t in trash:
        t["name"] = Path(t["original_path"]).name
        t["purge_after"] = (datetime.fromisoformat(t["moved_at"]) + timedelta(days=days)
                            ).isoformat(timespec="seconds")
    return {
        "enabled": bool(_v(ctx, "cleanup_enabled")),
        "interval_hours": int(_v(ctx, "cleanup_interval_hours")),
        "trash_days": days,
        "last_run": None if last is None else {
            "started_at": last["started_at"], "finished_at": last["finished_at"],
            "status": last["status"], "trashed": last["trashed"], "purged": last["purged"],
            "freed_bytes": last["freed_bytes"], **{k: v for k, v in loads(
                last["summary_json"], {}).items() if k in ("summary_nl", "kept",
                                                           "refused_roots", "errors")}},
        "path_map": ctx.state_get("download_janitor_path_map", {}) or {},
        "freed_total_bytes": int(total["b"]) if total else 0,
        "purged_total": int(total["n"]) if total else 0,
        "trash": trash,
        "trash_bytes": sum(int(t["size"] or 0) for t in trash),
    }


@router.post("/cleanup/run")
def cleanup_run(principal: Principal = Depends(require_operator),
                ctx: AppContext = Depends(get_app_context)) -> dict[str, Any]:
    job_id = jobs.enqueue(ctx.db, JOB_TYPE, {}, created_by=principal.actor,
                          idempotency_key=f"{JOB_TYPE}:manual:{utcnow()[:16]}")
    return {"job_id": job_id}


@router.post("/cleanup/trash/{trash_id}/restore")
def cleanup_restore(trash_id: int, principal: Principal = Depends(require_admin),
                    ctx: AppContext = Depends(get_app_context)) -> dict[str, Any]:
    try:
        return restore(ctx, trash_id, actor=principal.actor)
    except KeyError as exc:
        raise HTTPException(404, "Niet gevonden in de prullenbak") from exc
    except (ValueError, OSError) as exc:
        raise HTTPException(409, str(exc)) from exc


# -- achtergrond -------------------------------------------------------------------------------
async def _loop(ctx: AppContext) -> None:
    await asyncio.sleep(300)
    while True:
        try:
            hours = max(1, int(_v(ctx, "cleanup_interval_hours")))
            slot = datetime.now(UTC).strftime("%Y%m%d") + f"-{datetime.now(UTC).hour // hours}"
            jobs.enqueue(ctx.db, JOB_TYPE, {}, created_by="svc:downloads", priority=150,
                         idempotency_key=f"{JOB_TYPE}:{slot}", max_attempts=1)
            taskwatch.ok(ctx, "download_janitor")
        except Exception as exc:  # noqa: BLE001 - planner mag nooit stoppen
            LOGGER.exception("downloads opruimen inplannen mislukt")
            taskwatch.fail(ctx, "download_janitor", exc)
        await asyncio.sleep(1800)


async def startup(ctx: AppContext) -> None:
    if ctx.settings.start_background and ctx.settings.role in ("core", "all"):
        ctx.services["download_janitor_task"] = taskwatch.spawn(
            ctx, "download_janitor", _loop, label_nl="Downloads opruimen (planner)", every=1800)


async def shutdown(ctx: AppContext) -> None:
    task = ctx.services.pop("download_janitor_task", None)
    if task is not None:
        task.cancel()
        with contextlib.suppress(asyncio.CancelledError):
            await task
