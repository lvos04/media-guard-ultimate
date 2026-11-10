"""Weesbestanden in quarantaine (rapport 6, N9).

Een geannuleerde of teruggedraaide vervanging hoort zijn eigen quarantainekopie direct
op te ruimen. Uit de tijd van de herstartlus (3 nov) bleven er toch bestanden achter in
``<bibliotheek>/.mediaguard-quarantine/txn-N``: onzichtbaar in de app, maar wel ruimte
op de schijf. Deze module vindt zulke bestanden en ruimt ze alleen op als dat aantoonbaar
veilig is:

A. het bestand is een hardlink van het bestand dat nu in de bibliotheek staat (zelfde
   inode): weghalen verwijdert geen data;
B. het hoort bij een afgesloten item (geannuleerd/opgeruimd/teruggezet) én hetzelfde
   bibliotheekbestand is later alsnog vervangen door een afgeronde transactie, langer
   geleden dan het vangnet: het oude origineel is dan bewust vervangen;
C. het hoort bij een teruggezet item en de bibliotheek heeft een even groot bestand op
   de originele plek (de kopie is overbodig).

Alles wat daar niet aan voldoet, wordt alleen gemeld (grootte + reden), nooit
verwijderd. Elke verwijdering komt in de audit. Draait in de recovery-rol (die als enige
schrijfrechten op de quarantaine heeft); de uitkomst staat in de state-tabel zodat core
hem kan tonen.
"""

from __future__ import annotations

import contextlib
import logging
import os
import stat as stat_mod
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import TYPE_CHECKING, Any

from mediaguard.audit import audit
from mediaguard.db import utcnow

LOGGER = logging.getLogger(__name__)

if TYPE_CHECKING:
    from mediaguard.context import AppContext

STATE_KEY = "quarantine_orphans"
ACTIVE = ("pending", "held", "restorable")


def quarantine_roots(ctx: AppContext) -> list[Path]:
    roots = {Path(ctx.settings.quarantine_dir)}
    for lib in ctx.settings.library_roots:
        roots.add(Path(lib) / ".mediaguard-quarantine")
    for r in ctx.db.fetch_all("SELECT DISTINCT quarantine_path FROM quarantine_items"):
        p = Path(str(r["quarantine_path"]))
        # .../.mediaguard-quarantine/txn-N/<bestand>
        if p.parent.parent.name == ".mediaguard-quarantine" or \
                p.parent.parent == Path(ctx.settings.quarantine_dir):
            roots.add(p.parent.parent)
    return sorted(r for r in roots if r.is_dir())


def _within(path: Path, roots: list[Path]) -> bool:
    try:
        rp = path.resolve()
    except OSError:
        return False
    return any(rp == r.resolve() or r.resolve() in rp.parents for r in roots)


def _superseded(ctx: AppContext, item: dict[str, Any], hours: int
                ) -> dict[str, Any] | None:
    """Een latere, afgeronde vervanging van hetzelfde bibliotheekbestand."""
    cutoff = (datetime.now(UTC) - timedelta(hours=hours)).isoformat(timespec="seconds")
    row = ctx.db.fetch_one(
        "SELECT id, finished_at FROM replacement_transactions WHERE kind='replace' AND "
        "state='committed' AND id>? AND (library_path=? OR final_path=?) AND "
        "COALESCE(finished_at, updated_at) < ? ORDER BY id DESC LIMIT 1",
        (int(item["transaction_id"]), item["original_path"], item["original_path"], cutoff))
    return dict(row) if row else None


def _date_nl(iso: str | None) -> str:
    if not iso:
        return "onbekende datum"
    try:
        dt = datetime.fromisoformat(str(iso).replace("Z", "+00:00"))
    except ValueError:
        return str(iso)[:10]
    months = ("jan", "feb", "mrt", "apr", "mei", "jun", "jul", "aug", "sep", "okt", "nov",
              "dec")
    return f"{dt.day} {months[dt.month - 1]}"


def classify(ctx: AppContext, path: Path, item: dict[str, Any] | None,
             safety_hours: int) -> tuple[bool, str]:
    """(veilig_te_verwijderen, reden_nl)."""
    st = os.lstat(path)
    if item is None:
        return False, "Geen bijbehorend item bekend; alleen gemeld"
    orig = Path(str(item["original_path"]))
    try:
        ost = os.stat(orig)
    except OSError:
        ost = None
    if st.st_nlink > 1 and ost is not None and ost.st_ino == st.st_ino and \
            ost.st_dev == st.st_dev:
        return True, "Hardlink van het bestand dat nu in de bibliotheek staat (geen dataverlies)"
    if item["status"] in ("discarded", "purged", "restored"):
        later = _superseded(ctx, item, safety_hours)
        if later is not None and (ost is None or ost.st_ino != st.st_ino):
            return True, (f"Geannuleerd — later alsnog vervangen op "
                          f"{_date_nl(later.get('finished_at'))}")
    if item["status"] == "restored" and ost is not None and ost.st_size == st.st_size:
        return True, "Origineel staat weer in de bibliotheek; deze kopie is overbodig"
    return False, "Niet aantoonbaar overbodig; alleen gemeld"


def sweep(ctx: AppContext, *, remove: bool = True) -> dict[str, Any]:
    """Zoek (en ruim waar veilig op) weesbestanden. Idempotent."""
    from mediaguard.recovery.service import immediate_mode, safety_net

    # bewust vervangen originelen pas weg na het vangnet (direct) of de bewaartermijn
    hours = safety_net(ctx)[0] if immediate_mode(ctx) else \
        int(ctx.config.value("quarantine.retention_days")) * 24
    hours = max(hours, 24)
    roots = quarantine_roots(ctx)
    items = {str(r["quarantine_path"]): dict(r) for r in ctx.db.fetch_all(
        "SELECT * FROM quarantine_items ORDER BY id")}
    found: list[dict[str, Any]] = []
    removed_bytes = 0
    for root in roots:
        for dirpath, _dirs, files in os.walk(root):
            for name in files:
                path = Path(dirpath) / name
                try:
                    st = os.lstat(path)
                except OSError:
                    continue
                if not stat_mod.S_ISREG(st.st_mode) or not _within(path, roots):
                    continue
                item = items.get(str(path))
                if item is not None and item["status"] in ACTIVE:
                    continue
                if item is None and (datetime.now(UTC).timestamp() - st.st_mtime) < 3600:
                    continue  # mogelijk een transactie die net loopt
                safe, reason = classify(ctx, path, item, hours)
                entry = {"path": str(path), "size": st.st_size, "item_id":
                         item["id"] if item else None, "reason_nl": reason, "removed": False}
                if safe and remove:
                    try:
                        os.unlink(path)
                    except OSError as exc:
                        entry["reason_nl"] = f"{reason}; verwijderen mislukte: {exc}"
                    else:
                        entry["removed"] = True
                        # bij een hardlink komt er geen ruimte vrij (de bibliotheek houdt hem)
                        if st.st_nlink <= 1:
                            removed_bytes += st.st_size
                        with contextlib.suppress(OSError):
                            path.parent.rmdir()
                        audit(ctx.db, actor="svc:recovery", action="quarantine.orphan_removed",
                              target_type="quarantine", target_id=entry["item_id"],
                              instance_id=item["instance_id"] if item else None,
                              reason=reason, old_value={"path": str(path),
                                                        "size": st.st_size})
                found.append(entry)
        # lege txn-mappen (bv. na verwijderen door een oudere versie)
        if remove:
            for child in root.glob("txn-*"):
                with contextlib.suppress(OSError):
                    if child.is_dir() and not any(child.iterdir()):
                        child.rmdir()
    left = [f for f in found if not f["removed"]]
    result = {"checked_at": utcnow(), "items": found,
              "removed": sum(1 for f in found if f["removed"]),
              "freed_bytes": removed_bytes,
              "left": len(left), "left_bytes": sum(f["size"] for f in left)}
    ctx.state_set(STATE_KEY, result)
    _notify(ctx, result)
    return result


def _notify(ctx: AppContext, result: dict[str, Any]) -> None:
    """Rapport 7 / N9: maak de controle zichtbaar — een melding als er iets is opgeruimd of
    als er weesbestanden blijven staan (één keer per toestand, niet elk uur opnieuw)."""
    removed, left = int(result["removed"]), int(result["left"])
    if not removed and not left:
        return
    try:
        from mediaguard.api.system import notify
        from mediaguard.notify.service import gb_nl

        if removed:
            notify(ctx, "info", f"Quarantaine: {removed} weesbestand(en) opgeruimd",
                   f"{gb_nl(int(result['freed_bytes']))} vrijgemaakt. Het ging om kopieën "
                   "van originelen die al op hun plek staan of later zijn vervangen.",
                   link="/systeem#quarantaine-wezen",
                   dedupe_key=f"quarantine.orphans.removed|{result['checked_at'][:13]}")
        if left:
            notify(ctx, "warning", f"Quarantaine: {left} weesbestand(en) blijven staan",
                   f"{gb_nl(int(result['left_bytes']))} in de quarantainemap hoort bij geen "
                   "actief item. MediaGuard verwijdert ze niet zelf omdat niet zeker is dat "
                   "het origineel nog bestaat; zie Systeem → Weesbestanden.",
                   link="/systeem#quarantaine-wezen",
                   dedupe_key=f"quarantine.orphans.left|{left}|{result['left_bytes']}")
    except Exception:  # noqa: BLE001 - melding is extra
        LOGGER.debug("melding weesbestanden mislukt", exc_info=True)


VANISHED_MARKERS = ("No such file", "bestaat niet", "origineel in quarantaine ontbreekt",
                    "origineel verdween")


def _vanished_text(text: str) -> bool:
    return any(m in text for m in VANISHED_MARKERS)


def vanished_by_check(rc: dict[str, Any] | None) -> bool:
    """Rapport 8 / N10: faalde de vrijgavecontrole omdat het quarantainebestand al weg was?
    Dan is 'Opgeruimd' onjuist, ook voor rijen die de achteraf-correctie nog niet zag."""
    if not rc or rc.get("status") != "failed":
        return False
    return any(_vanished_text(str(c.get("detail") or "")) for c in rc.get("checks") or []
               if not c.get("passed"))


def fix_vanished_history(ctx: AppContext) -> int:
    """Rapport 7 / N10: items uit vóór de fix van 1.0.0 die als 'Opgeruimd' staan terwijl de
    vrijgavecontrole faalde omdat het quarantainebestand al weg was ('No such file'). Die
    worden eerlijk 'Origineel verdwenen buiten MediaGuard om'. Idempotent."""
    rows = ctx.db.fetch_all(
        "SELECT q.id, q.instance_id, q.quarantine_path, c.checks_json FROM quarantine_items q "
        "JOIN quarantine_release_checks c ON c.item_id=q.id WHERE q.status='purged' "
        "AND c.status='failed' AND COALESCE(q.reason,'') NOT LIKE 'Origineel verdwenen%'")
    fixed = 0
    reason = "Origineel verdwenen buiten MediaGuard om"
    for r in rows:
        if not _vanished_text(str(r["checks_json"] or "")):
            continue
        ctx.db.execute("UPDATE quarantine_items SET reason=? WHERE id=?", (reason, r["id"]))
        audit(ctx.db, actor="svc:recovery", action="quarantine.vanished",
              target_type="quarantine", target_id=r["id"], instance_id=r["instance_id"],
              reason=f"{reason} (achteraf vastgesteld: de vrijgavecontrole vond het "
                     "quarantainebestand niet)", old_value={"path": r["quarantine_path"]})
        fixed += 1
    return fixed


def status(ctx: AppContext) -> dict[str, Any]:
    return dict(ctx.state_get(STATE_KEY, {}) or {"checked_at": None, "items": [],
                                                    "removed": 0, "left": 0,
                                                    "left_bytes": 0, "freed_bytes": 0})


def discard_item_files(ctx: AppContext, txn: dict[str, Any]) -> None:
    """Bij annuleren/terugdraaien: verwijder de eigen quarantainekopieën van deze
    transactie — maar alleen als het origineel aantoonbaar nog op zijn plek staat
    (hardlink met dezelfde inode, of een even groot bestand op het bibliotheekpad)."""
    lib = Path(str(txn["library_path"]))
    try:
        lst = os.stat(lib)
    except OSError:
        lst = None
    paths = {str(r["quarantine_path"]) for r in ctx.db.fetch_all(
        "SELECT quarantine_path FROM quarantine_items WHERE transaction_id=? AND status IN "
        "('held','pending')", (txn["id"],))}
    if txn.get("quarantine_path"):
        paths.add(str(txn["quarantine_path"]))
    for p in paths:
        qp = Path(p)
        try:
            qst = os.stat(qp)
        except OSError:
            continue
        same = lst is not None and (
            (lst.st_ino == qst.st_ino and lst.st_dev == qst.st_dev)
            or lst.st_size == qst.st_size)
        if not same:
            continue  # niet aantoonbaar dubbel: de kopie blijft (wees-controle meldt hem)
        os.unlink(qp)
        with contextlib.suppress(OSError):
            qp.parent.rmdir()
