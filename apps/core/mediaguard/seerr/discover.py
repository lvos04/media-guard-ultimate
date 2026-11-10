"""Seerr via MediaGuard (docs/APP_API.md §3): zoeken, ontdekken, details en aanvragen voor de
web-UI en de Android-app. MediaGuard is alleen het doorgeefluik met de ingestelde Seerr-URL
en API-sleutel; Seerr bepaalt de rechten (goedkeuren, quota) van die Seerr-gebruiker."""

from __future__ import annotations

import asyncio
from typing import Any, Literal
from urllib.parse import quote

from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel

from mediaguard.audit import audit
from mediaguard.auth import Principal, require_operator, require_viewer
from mediaguard.context import AppContext, get_app_context
from mediaguard.db import loads
from mediaguard.seerr.client import SeerrError, seerr_client

router = APIRouter(prefix="/api/v1/seerr", tags=["seerr"])

TMDB_IMG = "https://image.tmdb.org/t/p/"
# Seerr MediaStatus: 1 onbekend, 2 aangevraagd, 3 verwerken, 4 gedeeltelijk, 5 beschikbaar,
# 6 geblokkeerd/verwijderd (nieuwere versies)
MEDIA_STATUS = {1: "niet", 2: "aangevraagd", 3: "verwerken", 4: "gedeeltelijk",
                5: "beschikbaar", 6: "niet"}
STATUS_NL = {"niet": "Niet aanwezig", "aangevraagd": "Aangevraagd",
             "verwerken": "Wordt verwerkt", "gedeeltelijk": "Gedeeltelijk beschikbaar",
             "beschikbaar": "Beschikbaar"}
# Seerr MediaRequestStatus: 1 wacht op goedkeuring, 2 goedgekeurd, 3 afgewezen, 4 mislukt,
# 5 voltooid
REQUEST_STATUS_NL = {1: "Wacht op goedkeuring", 2: "Goedgekeurd", 3: "Afgewezen",
                     4: "Mislukt", 5: "Voltooid"}


async def _call(ctx: AppContext, method: str, path: str, *, json: Any = None) -> Any:
    try:
        return await seerr_client(ctx).request(method, path, json=json)
    except SeerrError as exc:
        detail = exc.to_dict()
        if exc.code == "SEERR_HTTP" and exc.status in (403, 409):
            # rechten/quota van de Seerr-gebruiker of al aangevraagd
            msg = (exc.details or {}).get("message") if isinstance(exc.details, dict) else None
            detail["message"] = ("Seerr weigert dit: " + str(msg)) if msg else \
                "Seerr weigert dit (rechten of quotum van de ingestelde Seerr-gebruiker)"
            raise HTTPException(exc.status or 409, detail) from exc
        raise HTTPException(exc.http_status, detail) from exc


def _img(path: Any, size: str) -> str | None:
    return f"{TMDB_IMG}{size}{path}" if path else None


def _status(media_info: dict[str, Any] | None, is4k: bool = False) -> str:
    if not media_info:
        return "niet"
    raw = media_info.get("status4k" if is4k else "status")
    try:
        return MEDIA_STATUS.get(int(raw or 1), "niet")
    except (TypeError, ValueError):
        return "niet"


def _year(value: Any) -> int | None:
    text = str(value or "")
    return int(text[:4]) if text[:4].isdigit() else None


def _result(r: dict[str, Any], default_type: str | None = None) -> dict[str, Any] | None:
    kind = r.get("mediaType") or default_type
    if kind not in ("movie", "tv"):
        return None  # personen e.d. niet tonen
    status = _status(r.get("mediaInfo"))
    return {"media_type": kind, "tmdb_id": r.get("id"),
            "title": r.get("title") or r.get("name") or r.get("originalTitle"),
            "year": _year(r.get("releaseDate") or r.get("firstAirDate")),
            "poster_url": _img(r.get("posterPath"), "w342"),
            "overview": r.get("overview") or "", "status": status,
            "status_nl": STATUS_NL[status]}


def _page(data: Any, default_type: str | None = None) -> dict[str, Any]:
    data = data or {}
    results = [x for x in (_result(r, default_type) for r in data.get("results") or []) if x]
    return {"page": data.get("page", 1), "total_pages": data.get("totalPages", 1),
            "results": results}


@router.get("/search")
async def search(q: str = Query(..., min_length=1, max_length=200), page: int = Query(1, ge=1),
                 _: Principal = Depends(require_viewer),
                 ctx: AppContext = Depends(get_app_context)) -> dict[str, Any]:
    # Seerr wil de zoekterm als %20-gecodeerd (geen '+'), anders 400
    data = await _call(ctx, "GET", f"search?query={quote(q, safe='')}&page={page}")
    return _page(data)


@router.get("/discover/{kind}")
async def discover(kind: Literal["trending", "movies", "tv"], page: int = Query(1, ge=1),
                   _: Principal = Depends(require_viewer),
                   ctx: AppContext = Depends(get_app_context)) -> dict[str, Any]:
    data = await _call(ctx, "GET", f"discover/{kind}?page={page}")
    # discover/movies|tv geven geen mediaType mee
    return _page(data, {"movies": "movie", "tv": "tv"}.get(kind))


@router.get("/media/{media_type}/{tmdb_id}")
async def media(media_type: Literal["movie", "tv"], tmdb_id: int,
                _: Principal = Depends(require_viewer),
                ctx: AppContext = Depends(get_app_context)) -> dict[str, Any]:
    d = await _call(ctx, "GET", f"{media_type}/{tmdb_id}") or {}
    info = d.get("mediaInfo") or {}
    status = _status(info)
    out: dict[str, Any] = {
        "media_type": media_type, "tmdb_id": tmdb_id,
        "title": d.get("title") or d.get("name"),
        "year": _year(d.get("releaseDate") or d.get("firstAirDate")),
        "overview": d.get("overview") or "",
        "poster_url": _img(d.get("posterPath"), "w342"),
        "backdrop_url": _img(d.get("backdropPath"), "w780"),
        "genres": [g.get("name") for g in d.get("genres") or [] if g.get("name")],
        "runtime": d.get("runtime") or (d.get("episodeRunTime") or [None])[0],
        "rating": d.get("voteAverage"), "status": status, "status_nl": STATUS_NL[status],
        "requests": [{"request_id": r.get("id"),
                      "status_nl": REQUEST_STATUS_NL.get(int(r.get("status") or 0), "Onbekend"),
                      "requested_by": (r.get("requestedBy") or {}).get("displayName")}
                     for r in info.get("requests") or []],
    }
    if media_type == "tv":
        avail = {int(s.get("seasonNumber") or 0): s for s in info.get("seasons") or []}
        seasons = []
        for s in d.get("seasons") or []:
            n = int(s.get("seasonNumber") or 0)
            if n == 0:
                continue  # specials: Seerr vraagt die niet aan
            st = MEDIA_STATUS.get(int((avail.get(n) or {}).get("status") or 1), "niet")
            seasons.append({"season_number": n, "episodes": s.get("episodeCount"),
                            "name": s.get("name"), "air_date": s.get("airDate"),
                            "status": st, "status_nl": STATUS_NL[st]})
        out["seasons"] = seasons
    return out


class RequestBody(BaseModel):
    media_type: Literal["movie", "tv"]
    tmdb_id: int
    seasons: list[int] | Literal["all"] | None = None
    is4k: bool = False


@router.post("/request")
async def make_request(body: RequestBody, principal: Principal = Depends(require_operator),
                       ctx: AppContext = Depends(get_app_context)) -> dict[str, Any]:
    payload: dict[str, Any] = {"mediaType": body.media_type, "mediaId": body.tmdb_id,
                               "is4k": body.is4k}
    if body.media_type == "tv":
        if not body.seasons:
            raise HTTPException(422, "Kies welke seizoenen (of 'all')")
        payload["seasons"] = body.seasons
    data = await _call(ctx, "POST", "request", json=payload) or {}
    status_code = int(data.get("status") or 1)
    if body.media_type == "tv":
        n = "alle seizoenen" if body.seasons == "all" else \
            f"{len(body.seasons or [])} seizoen{'en' if len(body.seasons or []) != 1 else ''}"
        msg = f"Aangevraagd: {n}"
    else:
        msg = "Aangevraagd"
    if status_code == 1:
        msg += " — wacht op goedkeuring in Seerr"
    audit(ctx.db, actor=principal.actor, action="seerr.request", target_type=body.media_type,
          target_id=str(body.tmdb_id), new_value={**payload, "request_id": data.get("id")})
    return {"request_id": data.get("id"),
            "status": "aangevraagd" if status_code in (1, 2) else "niet",
            "status_nl": REQUEST_STATUS_NL.get(status_code, "Aangevraagd"), "message_nl": msg}


def _delivery(ctx: AppContext, media_type: str, tmdb_id: Any, tvdb_id: Any,
              request_id: Any) -> dict[str, Any] | None:
    from mediaguard.delivery.store import STAGE_NL, public_state

    rows = ctx.db.fetch_dicts(
        "SELECT * FROM delivery_items WHERE (media_type=? AND (tmdb_id=? OR tvdb_id=?)) "
        "ORDER BY updated_at DESC", ("series" if media_type == "tv" else "movie",
                                     tmdb_id or -1, tvdb_id or -1))
    if not rows and request_id is not None:
        rows = [r for r in ctx.db.fetch_dicts(
            "SELECT * FROM delivery_items WHERE source='seerr'")
            if int(request_id) in [int(x) for x in loads(r["seerr_request_ids_json"], [])]]
    if not rows:
        return None
    r = rows[0]
    progress = loads(r.get("progress_json"), {}) or {}
    return {"state": public_state(r), "state_nl": STAGE_NL.get(r["stage"], r["stage"]),
            "progress": {"present": progress.get("present"), "total": progress.get("total")},
            "next_step_nl": r.get("next_step_nl"), "item_id": _lib_id(ctx, r)}


def _lib_id(ctx: AppContext, delivery: dict[str, Any]) -> int | None:
    if not delivery.get("instance_id") or delivery.get("provider_id") is None:
        return None
    row = ctx.db.fetch_one("SELECT id FROM lib_items WHERE instance_id=? AND provider_id=? "
                           "AND removed_at IS NULL", (delivery["instance_id"],
                                                      delivery["provider_id"]))
    return int(row["id"]) if row else None


@router.get("/my-requests")
async def my_requests(filter: Literal["all", "pending", "processing", "available",  # noqa: A002
                                      "approved", "unavailable"] = "all",
                      take: int = Query(50, ge=1, le=200),
                      _: Principal = Depends(require_viewer),
                      ctx: AppContext = Depends(get_app_context)) -> dict[str, Any]:
    data = await _call(ctx, "GET", f"request?take={take}&skip=0&filter={filter}&sort=added") \
        or {}
    out = []
    for r in data.get("results") or []:
        m = r.get("media") or {}
        kind = r.get("type") or m.get("mediaType")
        tmdb = m.get("tmdbId")
        out.append({
            "request_id": r.get("id"), "media_type": kind, "tmdb_id": tmdb,
            "title": m.get("title") or m.get("name") or (r.get("title")),
            "year": _year(m.get("releaseDate") or m.get("firstAirDate")),
            "poster_url": _img(m.get("posterPath"), "w342"),
            "requested_at": r.get("createdAt"),
            "requested_by": (r.get("requestedBy") or {}).get("displayName"),
            "seasons": [int(s.get("seasonNumber") or 0) for s in r.get("seasons") or []],
            "seerr_status_nl": REQUEST_STATUS_NL.get(int(r.get("status") or 0), "Onbekend"),
            "media_status": _status(m), "media_status_nl": STATUS_NL[_status(m)],
            "delivery": _delivery(ctx, str(kind), tmdb, m.get("tvdbId"), r.get("id")),
        })
    await _fill_titles(ctx, out)
    return {"results": out}


async def _fill_titles(ctx: AppContext, rows: list[dict[str, Any]]) -> None:
    """Seerr's aanvragenlijst bevat geen titel/poster: haal die op (gecachet, max. 5
    tegelijk) en val terug op de titel uit MediaGuard's leveringsbewaking."""
    cache: dict[str, dict[str, Any]] = ctx.services.setdefault("seerr_title_cache", {})
    sem = asyncio.Semaphore(5)

    async def fill(row: dict[str, Any]) -> None:
        if row.get("title") or not row.get("tmdb_id") or row.get("media_type") not in (
                "movie", "tv"):
            return
        key = f"{row['media_type']}:{row['tmdb_id']}"
        if key not in cache:
            async with sem:
                try:
                    d = await seerr_client(ctx).request(
                        "GET", f"{row['media_type']}/{row['tmdb_id']}") or {}
                except SeerrError:
                    d = {}
            cache[key] = {"title": d.get("title") or d.get("name"),
                          "year": _year(d.get("releaseDate") or d.get("firstAirDate")),
                          "poster_url": _img(d.get("posterPath"), "w342")}
        info = cache[key]
        row["title"] = info.get("title") or row.get("title")
        row["year"] = row.get("year") or info.get("year")
        row["poster_url"] = row.get("poster_url") or info.get("poster_url")

    await asyncio.gather(*(fill(r) for r in rows))
    for r in rows:
        if not r.get("title"):
            d = ctx.db.fetch_one("SELECT title FROM delivery_items WHERE tmdb_id=? LIMIT 1",
                                 (r.get("tmdb_id") or -1,))
            r["title"] = (d["title"] if d else None) or f"TMDB {r.get('tmdb_id')}"
