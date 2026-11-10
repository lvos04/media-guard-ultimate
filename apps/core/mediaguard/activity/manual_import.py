"""Rapport 2 / N9: 'Manual Import required' bij een eenduidige ID-match zelf afhandelen.

Sonarr/Radarr zetten een voltooide download soms op 'importBlocked' met *Found matching movie
via grab history, but release was matched to movie by ID. Manual Import required.* De
download hoort dan aantoonbaar bij die film/aflevering (MediaGuard's eigen grab). MediaGuard
bevestigt de import via de ManualImport-API — alleen als er precies één passend
videobestand is en Sonarr/Radarr geen andere bezwaren (geen upgrade, sample, …) geeft.
Anders blijft het staan met de vertaalde reden en een knop.
"""

from __future__ import annotations

import asyncio
import contextlib
import logging
import re
from pathlib import PurePosixPath
from typing import Any

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel

from mediaguard import emergency, taskwatch
from mediaguard.arr.client import ArrError
from mediaguard.audit import audit
from mediaguard.auth import Principal, require_operator
from mediaguard.context import AppContext, get_app_context
from mediaguard.instances.registry import arr_client, list_instances

LOGGER = logging.getLogger(__name__)
router = APIRouter(prefix="/api/v1/activity", tags=["activity"])
VIDEO = (".mkv", ".mp4", ".m4v", ".avi", ".ts", ".m2ts", ".mov", ".webm")
# rejections die bij de ID-match horen en geen echt bezwaar zijn
BENIGN = ("by id", "manual import required", "matched to", "automatic import not possible",
          "unable to parse", "unknown episode")
STATE_KEY = "manual_import.reported"
# absoluut afleveringsnummer in anime-bestandsnamen: '… - 012 …', '[012]', 'E012', '#12'
_ABS_PATTERNS = (re.compile(r"\s-\s(\d{1,4})(?:v\d)?(?=[\s\[(._-]|$)"),
                 re.compile(r"\[(\d{2,4})(?:v\d)?\]"),
                 re.compile(r"(?<![sS\d])[eE#](\d{2,4})(?:v\d)?(?=[\s\[(._-]|$)"))
_SE = re.compile(r"[sS]\d{1,2}[eE]\d{1,3}")


def is_id_match_block(messages: list[str]) -> bool:
    """Sonarr/Radarr: 'matched to series/movie by ID' + 'Manual Import required' of
    'automatic import not possible'. MediaGuard handelt die zelf af als het eenduidig is."""
    text = " ".join(str(m) for m in messages).lower()
    return "by id" in text and ("manual import" in text or "automatic import" in text)


def _blocking(rejections: list[dict[str, Any]] | None) -> list[str]:
    out = []
    for r in rejections or []:
        reason = str(r.get("reason") or "")
        if not any(b in reason.lower() for b in BENIGN):
            out.append(reason)
    return out


def absolute_number(path: str) -> int | None:
    """Absoluut afleveringsnummer uit een anime-bestandsnaam; None bij S01E01-namen of twijfel."""
    name = PurePosixPath(str(path)).name
    stem = name.rsplit(".", 1)[0]
    if _SE.search(stem):
        return None
    for pat in _ABS_PATTERNS:
        found = {int(m) for m in pat.findall(stem)
                 if not (1900 <= int(m) <= 2100) and m not in ("264", "265", "1080", "720",
                                                               "2160", "480")}
        if len(found) == 1:
            return found.pop()
        if len(found) > 1:
            return None
    return None


async def _episodes(client: Any, series_id: int) -> list[dict[str, Any]]:
    return await client.get("episode", seriesId=series_id) or []


def _file_payload(item: dict[str, Any], download_id: str) -> dict[str, Any]:
    return {"path": item["path"], "downloadId": download_id, "quality": item.get("quality"),
            "languages": item.get("languages") or [], "releaseGroup": item.get("releaseGroup"),
            "indexerFlags": item.get("indexerFlags") or 0}


async def _sonarr_files(ctx: AppContext, client: Any, videos: list[dict[str, Any]],
                        download_id: str, series_id: Any, allowed: set[int]
                        ) -> tuple[list[dict[str, Any]], list[str]]:
    """Per videobestand de afleveringen bepalen. (te importeren, overgeslagen-redenen)."""
    from mediaguard.delivery import ai

    sid = series_id or next(((v.get("series") or {}).get("id") for v in videos
                             if (v.get("series") or {}).get("id")), None)
    if not sid:
        return [], ["serie onbekend"]
    sid = int(sid)
    eps_cache: list[dict[str, Any]] | None = None
    files: list[dict[str, Any]] = []
    skipped: list[str] = []
    used: set[int] = set()
    for v in videos:
        name = PurePosixPath(str(v.get("path"))).name
        vsid = (v.get("series") or {}).get("id")
        if vsid and int(vsid) != sid:
            skipped.append(f"{name}: hoort bij een andere serie")
            continue
        blocking = _blocking(v.get("rejections"))
        if blocking:
            skipped.append(f"{name}: " + "; ".join(blocking)[:200])
            continue
        parsed = sorted(int(e["id"]) for e in v.get("episodes") or [] if e.get("id"))
        absn = absolute_number(str(v.get("path")))
        by_abs: list[int] = []
        if absn is not None:
            if eps_cache is None:
                eps_cache = await _episodes(client, sid)
            by_abs = [int(e["id"]) for e in eps_cache
                      if e.get("absoluteEpisodeNumber") == absn]
        candidates: list[list[int]] = []
        for c in (parsed, by_abs):
            if c and c not in candidates and (not allowed or set(c) <= allowed):
                candidates.append(c)
        if len(candidates) == 1:
            chosen = candidates[0]
        elif not candidates:
            skipped.append(f"{name}: aflevering niet eenduidig te bepalen")
            continue
        else:
            labels = [",".join(map(str, c)) for c in candidates]
            dec = await ai.decide(
                ctx, "manual_import",
                f"Bij welke aflevering(en) hoort '{name}'? (Sonarr-parse of absoluut nummer)",
                labels + ["overslaan"],
                {"bestand": name, "sonarr": parsed, "absoluut": absn, "toegestaan":
                 sorted(allowed)}, fallback="overslaan",
                fallback_reason_nl="Sonarr-parse en absoluut nummer spreken elkaar tegen")
            if dec.choice == "overslaan":
                skipped.append(f"{name}: {dec.reason_nl}")
                continue
            chosen = candidates[labels.index(dec.choice)]
        if used & set(chosen):
            skipped.append(f"{name}: aflevering komt dubbel voor in de download")
            continue
        used |= set(chosen)
        files.append({**_file_payload(v, download_id), "seriesId": sid, "episodeIds": chosen})
    return files, skipped


# verbeterlijst 12: 'gelukt' pas als Sonarr/Radarr de import echt deden, niet zodra de
# opdracht verstuurd is. Zo lang wachten we op het resultaat; daarna kijkt een volgende ronde.
IMPORT_WAIT_SECONDS = 60.0
PENDING_KEY = "manual_import.pending"
PENDING_MAX_HOURS = 6
FINISHED_BAD = ("failed", "aborted", "cancelled", "orphaned")


def _pending(ctx: AppContext) -> dict[str, dict[str, Any]]:
    return dict(ctx.state_get(PENDING_KEY, {}) or {})


def _set_pending(ctx: AppContext, key: str, value: dict[str, Any] | None) -> None:
    from datetime import UTC, datetime, timedelta

    data = _pending(ctx)
    if value is None:
        data.pop(key, None)
    else:
        data[key] = value
    cutoff = (datetime.now(UTC) - timedelta(days=1)).isoformat(timespec="seconds")
    ctx.state_set(PENDING_KEY, {k: v for k, v in data.items()
                                if str(v.get("started") or "") >= cutoff})


async def _imported_count(client: Any, download_id: str, since: str) -> int | None:
    """Aantal import-gebeurtenissen voor deze download sinds ``since`` (None = onbekend)."""
    from datetime import datetime, timedelta

    try:
        data = await client.get("history", page=1, pageSize=100, sortKey="date",
                                sortDirection="descending", downloadId=download_id)
    except ArrError:
        return None
    rows = data.get("records", []) if isinstance(data, dict) else list(data or [])
    floor = (datetime.fromisoformat(since) - timedelta(minutes=5)).isoformat(timespec="seconds")
    n = 0
    for h in rows:
        if str(h.get("downloadId") or "") != download_id:
            continue
        if str(h.get("eventType") or "").lower() != "downloadfolderimported":
            continue
        when = str(h.get("date") or "").replace("Z", "+00:00")
        if when and when[:19] < floor[:19]:
            continue
        n += 1
    return n


async def _outcome(client: Any, rec: dict[str, Any], download_id: str,
                   wait: float) -> dict[str, Any] | None:
    """Uitkomst van een eerder verstuurde ManualImport; None = loopt nog."""
    from datetime import UTC, datetime, timedelta

    from mediaguard.arr.client import ArrNotFound

    cid = rec.get("command_id")
    status, message = "unknown", ""
    if cid:
        loop = asyncio.get_running_loop()
        deadline = loop.time() + max(0.0, wait)
        delay = 0.25
        while True:
            try:
                st = await client.get(f"command/{int(cid)}") or {}
            except ArrNotFound:
                status = "completed"  # Sonarr/Radarr ruimen afgeronde opdrachten op
                break
            except ArrError:
                break
            status = str(st.get("status") or "").lower()
            message = str(st.get("message") or st.get("exception") or "")
            if status not in ("queued", "started"):
                break
            if loop.time() >= deadline:
                return None
            await asyncio.sleep(min(delay, max(0.05, deadline - loop.time())))
            delay = min(5.0, delay * 2)
    if status in FINISHED_BAD:
        return {"imported": False, "count": 0,
                "reason_nl": "Sonarr/Radarr: import mislukt" + (f" ({message})"[:300]
                                                                 if message else "")}
    done = await _imported_count(client, download_id, str(rec["started"]))
    expected = int(rec.get("count") or 1)
    if done:
        return {"imported": True, "count": done,
                "reason_nl": f"Import bevestigd ({done} bestand(en))" if done >= expected
                else f"{done} van {expected} bestand(en) geïmporteerd"}
    started = datetime.fromisoformat(str(rec["started"]))
    if (done is None or status == "unknown") and datetime.now(UTC) - started < timedelta(
            hours=PENDING_MAX_HOURS):
        return None  # nog niet te bevestigen: later opnieuw kijken
    return {"imported": False, "count": 0,
            "reason_nl": "Sonarr/Radarr heeft het bestand niet geïmporteerd (geweigerd bij "
                         "het importeren)"}


async def try_import(ctx: AppContext, instance_id: str, download_id: str, *,
                     movie_id: Any = None, series_id: Any = None,
                     episode_ids: list[int] | None = None, actor: str,
                     wait_seconds: float | None = None) -> dict[str, Any]:
    """Probeer de import te bevestigen. Geeft {'imported': bool, 'count', 'reason_nl', …}.
    'imported' is pas True als Sonarr/Radarr de import echt uitvoerden; loopt hij nog, dan
    {'imported': False, 'pending': True} en kijkt een volgende ronde (zonder opnieuw te
    versturen). Tijdens een noodstop gebeurt niets ('emergency_stop': True)."""
    from mediaguard.db import utcnow

    if emergency.active(ctx):
        return {"imported": False, "count": 0, "reason_nl": emergency.message_nl("importeren"),
                "emergency_stop": True}
    key = f"{instance_id}:{download_id}"
    wait = IMPORT_WAIT_SECONDS if wait_seconds is None else wait_seconds
    async with arr_client(ctx, instance_id) as client:
        earlier = _pending(ctx).get(key)
        if earlier:
            res = await _outcome(client, earlier, download_id, 0)
            if res is None:
                return {"imported": False, "pending": True, "count": 0,
                        "reason_nl": "Import loopt nog; MediaGuard kijkt straks of hij lukte"}
            _set_pending(ctx, key, None)
            if res["imported"]:
                _audit_files(ctx, actor, instance_id, download_id, earlier.get("files") or [])
            return res
        params: dict[str, Any] = {"downloadId": download_id, "filterExistingFiles": False}
        if movie_id:
            params["movieId"] = movie_id
        if series_id:
            params["seriesId"] = series_id
        items = await client.get("manualimport", **params) or []
        videos = [i for i in items if str(i.get("path", "")).lower().endswith(VIDEO)
                  and "sample" not in str(i.get("path", "")).lower()]
        if not videos:
            return {"imported": False, "count": 0,
                    "reason_nl": "geen videobestand in de download"}
        if client.app == "radarr":
            if len(videos) != 1:
                return {"imported": False, "count": 0, "reason_nl": f"{len(videos)} "
                        "videobestanden in de download; MediaGuard importeert alleen als het er "
                        "precies één is"}
            item = videos[0]
            blocking = _blocking(item.get("rejections"))
            if blocking:
                return {"imported": False, "count": 0,
                        "reason_nl": "Sonarr/Radarr weigert: " + "; ".join(blocking)[:300]}
            mid = (item.get("movie") or {}).get("id") or movie_id
            if not mid or (movie_id and int(mid) != int(movie_id)):
                return {"imported": False, "count": 0,
                        "reason_nl": "Film in de download komt niet overeen"}
            files = [{**_file_payload(item, download_id), "movieId": int(mid)}]
            skipped: list[str] = []
        else:
            allowed = {int(e) for e in episode_ids or []}
            files, skipped = await _sonarr_files(ctx, client, videos, download_id, series_id,
                                                 allowed)
            if not files:
                return {"imported": False, "count": 0,
                        "reason_nl": "; ".join(skipped)[:400] or "niets te importeren",
                        "skipped": skipped}
        started = utcnow()
        cmd = await client.post("command", {"name": "ManualImport", "importMode": "auto",
                                            "files": files}) or {}
        rec = {"command_id": int(cmd.get("id") or 0) or None, "started": started,
               "count": len(files),
               "files": [{"path": f["path"], "episodeIds": f.get("episodeIds"),
                          "movieId": f.get("movieId")} for f in files]}
        _set_pending(ctx, key, rec)
        res = await _outcome(client, rec, download_id, wait)
        if res is None:
            return {"imported": False, "pending": True, "count": 0, "skipped": skipped,
                    "reason_nl": "Import loopt nog; MediaGuard kijkt straks of hij lukte"}
        _set_pending(ctx, key, None)
    if res["imported"]:
        _audit_files(ctx, actor, instance_id, download_id, rec["files"])
    return {**res, "skipped": skipped}


def _audit_files(ctx: AppContext, actor: str, instance_id: str, download_id: str,
                 files: list[dict[str, Any]]) -> None:
    for f in files:
        audit(ctx.db, actor=actor, action="download.manual_import", target_type="download",
              target_id=download_id, instance_id=instance_id,
              new_value={"path": f.get("path"), "episodeIds": f.get("episodeIds"),
                         "movieId": f.get("movieId")})


class ImportBody(BaseModel):
    download_id: str
    confirm: bool = False


@router.post("/downloads/{instance_id}/import")
async def import_download(instance_id: str, body: ImportBody,
                          principal: Principal = Depends(require_operator),
                          ctx: AppContext = Depends(get_app_context)) -> dict[str, Any]:
    if not body.confirm:
        raise HTTPException(400, "Bevestiging vereist (confirm: true)")
    emergency.require_clear(ctx, "importeren")
    if instance_id not in {i.id for i in list_instances(ctx)}:
        raise HTTPException(404, "Onbekende instantie")
    from mediaguard.activity.api import activity_cached

    act = await activity_cached(ctx)
    d = next((x for x in (act or {}).get("downloads", []) if x.get("instance_id") == instance_id
              and x.get("download_id") == body.download_id), None)
    try:
        return await try_import(ctx, instance_id, body.download_id,
                                movie_id=(d or {}).get("movie_id"),
                                series_id=(d or {}).get("series_id"),
                                episode_ids=[d["episode_id"]] if d and d.get("episode_id")
                                else None, actor=principal.actor, wait_seconds=20)
    except ArrError as exc:
        raise HTTPException(502, f"Sonarr/Radarr: {exc.message}") from exc


async def auto_import(ctx: AppContext) -> int:
    """Alle downloads met een ID-match-blokkade: per download (alle queue-regels samen,
    dus ook seizoenspakketten) importeren. Meldt per titel één samenvatting."""
    from mediaguard.activity.api import activity_cached
    from mediaguard.api.system import notify

    if emergency.skip(ctx, "automatische import"):
        return 0
    act = await activity_cached(ctx)
    groups: dict[tuple[str, str], dict[str, Any]] = {}
    for d in (act or {}).get("downloads", []):
        if not d.get("can_import") or not d.get("download_id") or not d.get("instance_id"):
            continue
        g = groups.setdefault((d["instance_id"], d["download_id"]), {
            "series_id": d.get("series_id"), "movie_id": d.get("movie_id"), "episodes": set(),
            "title": str(d.get("title") or "").split(" · ")[0]})
        if d.get("episode_id"):
            g["episodes"].add(int(d["episode_id"]))
    reported: dict[str, str] = dict(ctx.state_get(STATE_KEY, {}) or {})
    per_title: dict[str, int] = {}
    series_titles: set[str] = set()
    left: list[str] = []
    done = 0
    for (iid, did), g in groups.items():
        if emergency.active(ctx):
            break  # noodstop tijdens de ronde: de rest wacht tot daarna
        try:
            res = await try_import(ctx, iid, did, movie_id=g["movie_id"],
                                   series_id=g["series_id"],
                                   episode_ids=sorted(g["episodes"]) or None,
                                   actor="svc:auto-import")
        except Exception as exc:  # noqa: BLE001 - per download
            LOGGER.info("automatische import %s niet gelukt: %s", g["title"], exc)
            continue
        if res["imported"]:
            done += res.get("count", 1)
            per_title[g["title"]] = per_title.get(g["title"], 0) + res.get("count", 1)
            if g["series_id"] is not None:
                series_titles.add(g["title"])
            reported.pop(f"{iid}:{did}", None)
        elif res.get("pending"):
            continue  # loopt nog: volgende ronde kijken of hij lukte
        elif f"{iid}:{did}" not in reported:
            reported[f"{iid}:{did}"] = res.get("reason_nl", "")
            left.append(f"{g['title']}: {res.get('reason_nl', '')}"[:200])
    ctx.state_set(STATE_KEY, dict(list(reported.items())[-500:]))
    for title, n in per_title.items():
        noun = "aflevering" if n == 1 else "afleveringen"
        what = f"{n} {noun} van {title}" if title in series_titles else title
        notify(ctx, "info", "Downloads alsnog geïmporteerd", f"{what} alsnog geïmporteerd "
               "(Sonarr/Radarr herkende ze via de download-ID).",
               link="/activiteit?tab=downloads", dedupe_key=f"manual-import:{title}")
    if left:
        notify(ctx, "warning", "Import niet automatisch mogelijk",
               "; ".join(left[:5]) + (" …" if len(left) > 5 else ""),
               link="/activiteit?tab=downloads", dedupe_key="manual-import-left")
    return done


async def _loop(ctx: AppContext) -> None:
    await asyncio.sleep(120)
    while True:
        try:
            n = await auto_import(ctx)
            if n:
                LOGGER.info("%s download(s) automatisch geïmporteerd (ID-match)", n)
            taskwatch.ok(ctx, "auto_import")
        except Exception as exc:  # noqa: BLE001 - lus nooit laten sterven
            LOGGER.exception("automatische import mislukt")
            taskwatch.fail(ctx, "auto_import", exc)
        await asyncio.sleep(600)


async def startup(ctx: AppContext) -> None:
    if ctx.settings.start_background and ctx.settings.role in ("core", "all"):
        ctx.services["auto_import_task"] = taskwatch.spawn(
            ctx, "auto_import", _loop, label_nl="Downloads alsnog importeren", every=600)


async def shutdown(ctx: AppContext) -> None:
    task = ctx.services.pop("auto_import_task", None)
    if task is not None:
        task.cancel()
        with contextlib.suppress(asyncio.CancelledError):
            await task
