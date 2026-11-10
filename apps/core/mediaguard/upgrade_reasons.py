"""Waarom werd een bestand geüpgraded? Vergelijkt het oude en het nieuwe bestand op kwaliteit,
custom formats (score en namen) en REPACK/PROPER, en geeft één Nederlandse zin.

Bron: de lokaal bewaarde Sonarr/Radarr-historie (``storage_ledger_events``, 40 dagen) en
MediaGuard's eigen AV1-jobs (was het vervangen bestand een AV1-versie van MediaGuard?).
"""

from __future__ import annotations

import re
from datetime import UTC, datetime, timedelta
from typing import TYPE_CHECKING, Any

from mediaguard.db import loads

if TYPE_CHECKING:
    from mediaguard.context import AppContext

SOURCE_RANK = {"unknown": 0, "sdtv": 1, "dvd": 2, "tv": 3, "hdtv": 3, "webrip": 4,
               "web": 5, "webdl": 5, "bluray": 6, "remux": 7}


def quality_rank(name: str | None) -> tuple[int, int]:
    """(resolutie, bron) uit een kwaliteitsnaam als 'WEBDL-1080p' of 'Bluray-2160p Remux'."""
    text = str(name or "").lower()
    m = re.search(r"(\d{3,4})p", text)
    res = int(m.group(1)) if m else 0
    if "remux" in text:
        src = SOURCE_RANK["remux"]
    elif "bluray" in text or "blu-ray" in text:
        src = SOURCE_RANK["bluray"]
    elif "webdl" in text or "web-dl" in text:
        src = SOURCE_RANK["webdl"]
    elif "webrip" in text:
        src = SOURCE_RANK["webrip"]
    elif "hdtv" in text:
        src = SOURCE_RANK["hdtv"]
    elif "dvd" in text:
        src = SOURCE_RANK["dvd"]
    elif "sdtv" in text:
        src = SOURCE_RANK["sdtv"]
    else:
        src = 0
    return res, src


def _names(value: Any) -> list[str]:
    out = []
    for cf in value or []:
        name = cf.get("name") if isinstance(cf, dict) else cf
        if name:
            out.append(str(name))
    return out


def explain(old: dict[str, Any] | None, new: dict[str, Any]) -> dict[str, Any]:
    """``old``/``new``: {quality, score, custom_formats, repack}. Geeft reason_nl,
    cf_added en cf_removed."""
    new_cf = _names(new.get("custom_formats"))
    if not old:
        return {"reason_nl": "Onbekend: het vorige bestand staat niet in de historie",
                "cf_added": new_cf, "cf_removed": []}
    old_cf = _names(old.get("custom_formats"))
    added = [c for c in new_cf if c not in old_cf]
    removed = [c for c in old_cf if c not in new_cf]
    parts: list[str] = []
    oq, nq = old.get("quality"), new.get("quality")
    if oq and nq and quality_rank(nq) > quality_rank(oq):
        parts.append(f"Betere kwaliteit: {oq} → {nq}")
    elif oq and nq and oq != nq and quality_rank(nq) < quality_rank(oq):
        parts.append(f"Andere kwaliteit: {oq} → {nq}")
    os_, ns = old.get("score"), new.get("score")
    if os_ is not None and ns is not None and int(ns) != int(os_):
        cf_txt = ", ".join([f"+{c}" for c in added] + [f"−{c}" for c in removed])
        parts.append(("Betere custom formats" if ns > os_ else "Lagere custom-format-score")
                     + (f": {cf_txt}" if cf_txt else "") + f" (score {os_} → {ns})")
    elif added or removed:
        parts.append("Andere custom formats: " +
                     ", ".join([f"+{c}" for c in added] + [f"−{c}" for c in removed]))
    if new.get("repack") and not old.get("repack"):
        parts.append("REPACK/PROPER van dezelfde release")
    if not parts:
        parts.append("Zelfde kwaliteit en score — vermoedelijk een REPACK, handmatige keuze "
                     "of een gewijzigd profiel")
    return {"reason_nl": "; ".join(parts), "cf_added": added, "cf_removed": removed}


def _was_av1(ctx: AppContext, instance_id: str, owner_id: Any, before: str) -> bool:
    """Was er vóór dit moment een AV1-versie van MediaGuard geplaatst voor dit item?"""
    row = ctx.db.fetch_one(
        "SELECT 1 FROM encoding_jobs WHERE instance_id=? AND status='committed' AND "
        "updated_at<=? AND (provider_id=? OR provider_id IN (SELECT CAST(f.provider_id AS TEXT) "
        "FROM lib_files f WHERE f.instance_id=? AND f.owner_provider_id=?)) LIMIT 1",
        (instance_id, before, str(owner_id), instance_id, owner_id))
    return row is not None


def _side(row: dict[str, Any] | None) -> dict[str, Any] | None:
    if row is None:
        return None
    return {"quality": row.get("quality"), "score": row.get("cf_score"),
            "custom_formats": loads(row.get("custom_formats_json"), []) or [],
            "repack": bool(row.get("is_repack")), "size": row.get("size"),
            "release": row.get("source_title"), "release_group": row.get("release_group"),
            "indexer": row.get("indexer")}


def previous_import(ctx: AppContext, instance_id: str, media_item_id: Any,
                    at: str) -> dict[str, Any] | None:
    """De import van hetzelfde item vóór ``at`` (het bestand dat nu vervangen werd)."""
    rows = ctx.db.fetch_dicts(
        "SELECT * FROM storage_ledger_events WHERE event='imported' AND instance_id=? AND "
        "item_id=? AND at<? ORDER BY at DESC LIMIT 1", (instance_id, media_item_id, at))
    return rows[0] if rows else None


def reason_for_import(ctx: AppContext, ev: dict[str, Any]) -> dict[str, Any]:
    """Reden voor één import-event (dict uit storage_ledger_events)."""
    old = previous_import(ctx, ev["instance_id"], ev["item_id"], ev["at"])
    new = _side(ev) or {}
    out = explain(_side(old), new)
    out["old"] = _side(old)
    out["new"] = new
    if old is not None:
        out["old"]["av1_by_mediaguard"] = _was_av1(ctx, ev["instance_id"], ev["item_id"],
                                                   ev["at"])
    return out


def item_upgrades(ctx: AppContext, item: dict[str, Any], days: int = 90,
                  now: datetime | None = None) -> list[dict[str, Any]]:
    """Upgrades voor een bibliotheek-item (serie of film), nieuwste eerst."""
    from mediaguard import storage_ledger

    since = ((now or datetime.now(UTC)) - timedelta(days=days)).strftime("%Y-%m-%dT%H:%M:%SZ")
    rows = ctx.db.fetch_dicts(
        "SELECT * FROM storage_ledger_events WHERE event='imported' AND instance_id=? AND "
        "owner_id=? AND at>=? ORDER BY at DESC", (item["instance_id"], item["provider_id"], since))
    out = []
    for ev in rows:
        old = previous_import(ctx, ev["instance_id"], ev["item_id"], ev["at"])
        deleted = ctx.db.fetch_one(
            "SELECT 1 FROM storage_ledger_events WHERE event='deleted' AND instance_id=? AND "
            "item_id=? AND ABS(julianday(at)-julianday(?))*1440<=30", (
                ev["instance_id"], ev["item_id"], ev["at"]))
        if old is None and deleted is None:
            continue  # eerste import van dit item: geen upgrade
        info = reason_for_import(ctx, ev)
        source = storage_ledger._source(ctx, ev, True,  # noqa: SLF001 - zelfde bronbepaling
                                        "movie" if ev["media_kind"] == "movie" else "episode")
        label = (storage_ledger._episode_label(ctx, ev["instance_id"], ev["item_id"])  # noqa: SLF001
                 if ev["media_kind"] == "episode" else None)
        out.append({"at": ev["at"], "label": label, "old": info["old"], "new": info["new"],
                    "cf_added": info["cf_added"], "cf_removed": info["cf_removed"],
                    "source": source, "source_nl": storage_ledger.SOURCE_NL.get(source, source),
                    "reason_nl": info["reason_nl"]})
    return out
