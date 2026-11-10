"""Schrijftest per hoofdmap van de bibliotheek (bij start en elk uur).

Doet precies wat een AV1-vervanging in die map doet: aanmaken, schrijven, fsync, hernoemen
over een bestaand bestand en verwijderen. Faalt een stap, dan:

* staat de map met die stap in Systeem → Opslag (``GET /api/v1/storage/probe``) en in
  ``/api/v1/attention``;
* biedt de AV1-autopilot geen bestanden uit die map meer aan (geen uren encoden voor niets);
* zodra de test weer slaagt, worden uitgestelde activaties uit die map meteen opnieuw
  geprobeerd (geen 6 uur wachten).

De test draait in het proces dat ook vervangt (recovery-rol); core leest het resultaat.
"""

from __future__ import annotations

import logging
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

from mediaguard.context import AppContext
from mediaguard.db import dumps, loads, utcnow
from mediaguard.recovery import fsops

LOGGER = logging.getLogger(__name__)
STATE_KEY = "storage_probe"


def roots(ctx: AppContext) -> dict[str, set[str]]:
    """Hoofdmappen die echt gebruikt worden → instanties die ze gebruiken."""
    out: dict[str, set[str]] = {}
    for r in ctx.db.fetch_all(
            "SELECT DISTINCT instance_id, root_folder_path FROM lib_items WHERE "
            "root_folder_path IS NOT NULL AND root_folder_path<>''"):
        out.setdefault(str(r["root_folder_path"]).rstrip("/") or "/", set()).add(
            str(r["instance_id"]))
    for r in ctx.db.fetch_all("SELECT id, default_root_folder FROM instances WHERE "
                              "default_root_folder IS NOT NULL AND default_root_folder<>''"):
        path = str(r["default_root_folder"]).rstrip("/") or "/"
        # een (nog) niet bestaande standaardmap is geen schrijfprobleem (zie inrichting)
        if path in out or Path(path).is_dir():
            out.setdefault(path, set()).add(str(r["id"]))
    if not out:
        for p in ctx.settings.library_roots:
            if Path(p).is_dir():
                out.setdefault(str(p).rstrip("/") or "/", set())
    return out


def run(ctx: AppContext) -> dict[str, Any]:
    previous = {r["path"]: r for r in (ctx.state_get(STATE_KEY, {}) or {}).get("roots", [])}
    results = []
    for path, instances in sorted(roots(ctx).items()):
        res = fsops.probe_dir(Path(path))
        if res["ok"]:
            res = _probe_title_folder(ctx, Path(path), res)
        res["instances"] = sorted(instances)
        res["checked_at"] = utcnow()
        if not res["ok"]:
            LOGGER.warning("schrijftest %s faalde bij %s: %s", path, res["step"], res["error"])
        results.append(res)
    state = {"checked_at": utcnow(), "ok": all(r["ok"] for r in results), "roots": results}
    ctx.state_set(STATE_KEY, state)
    recovered = [r["path"] for r in results
                 if r["ok"] and not (previous.get(r["path"]) or {}).get("ok", True)]
    requeued = requeue_deferred(ctx, [r["path"] for r in results if r["ok"]])
    state["requeued"] = requeued
    if recovered:
        LOGGER.info("schrijftest weer geslaagd voor %s; %d activaties opnieuw ingepland",
                    ", ".join(recovered), requeued)
    return state


MEDIA_EXT = {".mkv", ".mp4", ".m4v", ".avi", ".ts", ".m2ts", ".mov", ".webm"}


def _sample_title_folder(ctx: AppContext, root: Path) -> tuple[Path, Path | None] | None:
    """Een echte titelmap onder de hoofdmap: bij voorkeur die van een activatie die op
    rechten strandde, anders de eerste map met een mediabestand."""
    for r in ctx.db.fetch_all(
            "SELECT source_path FROM encoding_jobs WHERE (error LIKE '%Errno 1]%' OR error "
            "LIKE '%Operation not permitted%' OR error LIKE '%Permission%') ORDER BY id DESC "
            "LIMIT 20"):
        src = Path(str(r["source_path"]))
        if _under(str(src), str(root)) and src.parent.is_dir():
            return src.parent, src if src.is_file() else None
    try:
        for d in sorted(p for p in root.iterdir() if p.is_dir() and not p.name.startswith(".")):
            for f in sorted(d.iterdir()):
                if f.is_file() and f.suffix.lower() in MEDIA_EXT:
                    return d, f
    except OSError:
        return None
    return None


def _probe_title_folder(ctx: AppContext, root: Path, res: dict[str, Any]) -> dict[str, Any]:
    """Rapport 2 / N4: de hoofdmap kan slagen terwijl een titelmap faalt (andere ACL,
    sticky bit, bestanden van een andere gebruiker). Test dezelfde stappen in een echte
    titelmap en kijk of het mediabestand daar vervangen mág worden."""
    sample = _sample_title_folder(ctx, root)
    if sample is None:
        return res
    folder, media = sample
    sub = fsops.probe_dir(folder)
    out = {**res, "title_folder": str(folder)}
    if not sub["ok"]:
        return {**out, **{k: sub[k] for k in ("ok", "step", "step_nl", "error", "errno")},
                "where_nl": f"in titelmap {folder.name}"}
    if media is not None:
        import os
        import stat as st_mod

        try:
            fst, dst = os.stat(media), os.stat(folder)
        except OSError:
            return out
        foreign = fst.st_uid != os.geteuid()
        sticky = bool(dst.st_mode & st_mod.S_ISVTX)
        if foreign and sticky:
            return {**out, "ok": False, "step": "replace", "step_nl": fsops.OPS_NL.get(
                "replace", "vervangen"), "errno": 1,
                "error": (f"Map {folder.name} heeft de sticky-bit en {media.name} is van "
                          f"gebruiker {fst.st_uid} (MediaGuard draait als {os.geteuid()}): "
                          "vervangen wordt geweigerd. Geef de app-gebruiker eigenaarschap of "
                          "haal de sticky-bit van de dataset."),
                "where_nl": f"in titelmap {folder.name}"}
        out["sample_owner_uid"] = fst.st_uid
    return out


def status(ctx: AppContext) -> dict[str, Any]:
    state = ctx.state_get(STATE_KEY, {}) or {}
    return {"checked_at": state.get("checked_at"), "ok": state.get("ok", True),
            "roots": state.get("roots", []),
            "failing": [r for r in state.get("roots", []) if not r.get("ok")]}


def _under(path: str, root: str) -> bool:
    root = root.rstrip("/")
    return path == root or path.startswith(root + "/")


def failing_root_for(ctx: AppContext, path: str) -> dict[str, Any] | None:
    """De mislukte schrijftest die voor dit bestand geldt (langste hoofdmap), of None."""
    best: dict[str, Any] | None = None
    for r in (ctx.state_get(STATE_KEY, {}) or {}).get("roots", []):
        if _under(path, str(r["path"])) and (best is None or len(r["path"]) > len(
                best["path"])):
            best = r
    return best if best is not None and not best.get("ok") else None


def requeue_deferred(ctx: AppContext, passing_roots: list[str]) -> int:
    """Uitgestelde activaties (opslagprobleem, ``retry_after``) in mappen waar de test nu
    slaagt: wachttijd wissen zodat de volgende activatieronde ze meteen oppakt."""
    if not passing_roots:
        return 0
    n = 0
    due = (datetime.now(UTC) - timedelta(minutes=15)).isoformat(timespec="seconds")
    for r in ctx.db.fetch_all("SELECT id, source_path, decision_json, error FROM "
                              "encoding_jobs WHERE status='awaiting_activation'"):
        meta = loads(r["decision_json"], {}) or {}
        if "ruimte" in str(r["error"] or "").lower():
            continue  # ruimtegebrek lost een geslaagde schrijftest niet op
        if not meta.get("retry_after") or not any(
                _under(str(r["source_path"]), root) for root in passing_roots):
            continue
        meta.pop("retry_after", None)
        # updated_at terugzetten: activate_waiting wacht anders nog 10 minuten
        ctx.db.execute("UPDATE encoding_jobs SET decision_json=?, updated_at=? WHERE id=? "
                       "AND status='awaiting_activation'", (dumps(meta), due, r["id"]))
        n += 1
    return n
