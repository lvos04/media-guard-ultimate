"""Nederlandse ondertitels (rapport 5, M8).

- Met Bazarr (voorkeur): MediaGuard leest Bazarrs lijst met ontbrekende ondertitels, toont per
  item 'NL-ondertitel ontbreekt' en laat Bazarr automatisch zoeken (hooguit eens per dag per
  serie/film).
- Zonder Bazarr, als de gebruiker een OpenSubtitles-account invult: MediaGuard haalt zelf een
  Nederlandse .srt op en zet die naast het videobestand — alleen in de bibliotheekmappen van
  MediaGuard, nooit over een bestaand bestand heen, met een dagquotum en een journaal.
"""

from __future__ import annotations

import logging
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import TYPE_CHECKING, Any

from mediaguard.audit import audit
from mediaguard.db import loads, utcnow
from mediaguard.mediaserver.clients import (
    BazarrClient,
    MediaServerError,
    OpenSubtitlesClient,
    WantedSub,
)

if TYPE_CHECKING:
    from mediaguard.context import AppContext

LOGGER = logging.getLogger(__name__)
BAZARR_KEY = "subtitles.bazarr_api_key"
OS_KEY = "subtitles.opensubtitles_api_key"
OS_USER = "subtitles.opensubtitles_username"
OS_PASS = "subtitles.opensubtitles_password"
NL_CODES = {"nl", "nld", "dut", "dutch", "nederlands"}
SRT_SUFFIXES = (".nl.srt", ".nld.srt", ".dut.srt", ".nl.forced.srt", ".nl.hi.srt")
SEARCH_GAP_H = 24
MAX_SEARCHES_PER_RUN = 20


def _v(ctx: AppContext, key: str) -> Any:
    return ctx.config.value(f"subtitles.{key}")


def nl_wanted(ctx: AppContext) -> bool:
    try:
        return str(ctx.config.value("audio.nl_subtitles_wanted")) != "no"
    except KeyError:
        return True


def bazarr(ctx: AppContext) -> BazarrClient | None:
    url = str(_v(ctx, "bazarr_url") or "").strip()
    if not url:
        found = (ctx.state_get("mediaserver_discovery", {}) or {}).get("bazarr") or []
        url = found[0]["url"] if found else ""
    if not url or not ctx.secrets.has(BAZARR_KEY):
        return None
    return BazarrClient(ctx.services, url, ctx.secrets.get(BAZARR_KEY))


def opensubtitles(ctx: AppContext) -> OpenSubtitlesClient | None:
    if not bool(_v(ctx, "opensubtitles_enabled")):
        return None
    key, user, pw = (ctx.secrets.get(OS_KEY), ctx.secrets.get(OS_USER),
                     ctx.secrets.get(OS_PASS))
    if not (key and user and pw):
        return None
    return OpenSubtitlesClient(ctx.services, key, user, pw)


def _title(w: WantedSub) -> str:
    if w.kind == "episode" and w.season is not None and w.episode is not None:
        return f"{w.title} S{w.season:02d}E{w.episode:02d}"
    return w.title


def _lib_match(ctx: AppContext, w: WantedSub) -> tuple[str | None, str | None, int | None]:
    """Koppel Bazarrs item aan MediaGuards bibliotheek (Bazarr kan nog aan de oude Sonarr/
    Radarr hangen, dus op titel + seizoen/aflevering, niet op ID)."""
    kind = "series" if w.kind == "episode" else "movie"
    item = ctx.db.fetch_one("SELECT instance_id, provider_id FROM lib_items WHERE kind=? AND "
                            "lower(title)=lower(?) LIMIT 1", (kind, w.title))
    if item is None:
        return None, None, None
    if w.kind == "movie":
        return item["instance_id"], "movie", int(item["provider_id"])
    ep = ctx.db.fetch_one(
        "SELECT provider_id FROM lib_episodes WHERE instance_id=? AND series_provider_id=? AND "
        "season_number=? AND episode_number=?",
        (item["instance_id"], item["provider_id"], w.season, w.episode))
    return item["instance_id"], "episode", int(ep["provider_id"]) if ep else None


async def sync_bazarr(ctx: AppContext, client: BazarrClient) -> dict[str, Any]:
    wanted = [w for w in await client.wanted() if NL_CODES & {c.lower() for c in w.languages}]
    keys: set[str] = set()
    now = utcnow()
    for w in wanted:
        key = f"bazarr:{w.kind}:{w.bazarr_id}:{w.episode_id or 0}"
        keys.add(key)
        inst, kind, pid = _lib_match(ctx, w)
        ctx.db.execute(
            "INSERT INTO subtitle_status(key,instance_id,media_kind,provider_id,title,nl_missing,"
            "source,updated_at) VALUES (?,?,?,?,?,1,'bazarr',?) ON CONFLICT(key) DO UPDATE SET "
            "nl_missing=1, title=excluded.title, instance_id=excluded.instance_id, "
            "media_kind=excluded.media_kind, provider_id=excluded.provider_id, "
            "updated_at=excluded.updated_at", (key, inst, kind, pid, _title(w), now))
    # wat Bazarr niet meer mist, is opgelost
    for row in ctx.db.fetch_all("SELECT key FROM subtitle_status WHERE source='bazarr' AND "
                                "nl_missing=1"):
        if row["key"] not in keys:
            ctx.db.execute("UPDATE subtitle_status SET nl_missing=0, updated_at=? WHERE key=?",
                           (now, row["key"]))
    searched = 0
    if bool(_v(ctx, "nl_auto")) and nl_wanted(ctx):
        cutoff = (datetime.now(UTC) - timedelta(hours=SEARCH_GAP_H)).isoformat(
            timespec="seconds")
        done: set[tuple[str, int]] = set()
        for w in wanted:
            group = (w.kind, w.bazarr_id)
            if group in done or searched >= MAX_SEARCHES_PER_RUN:
                continue
            done.add(group)
            last = ctx.db.fetch_one(
                "SELECT MAX(searched_at) AS s FROM subtitle_status WHERE key LIKE ?",
                (f"bazarr:{w.kind}:{w.bazarr_id}:%",))
            if last and last["s"] and last["s"] > cutoff:
                continue
            try:
                await client.search_missing(w)
            except MediaServerError as exc:
                LOGGER.info("Bazarr zoeken mislukt voor %s: %s", w.title, exc)
                continue
            ctx.db.execute("UPDATE subtitle_status SET searched_at=? WHERE key LIKE ?",
                           (now, f"bazarr:{w.kind}:{w.bazarr_id}:%"))
            searched += 1
    ctx.state_set("subtitles_last", {"at": now, "source": "bazarr", "missing": len(wanted),
                                     "searched": searched})
    return {"missing": len(wanted), "searched": searched}


# -- zonder Bazarr: OpenSubtitles ------------------------------------------------------------
def has_nl(video: Path, media_info: dict[str, Any]) -> bool:
    stem = str(video.with_suffix(""))
    if any(Path(stem + suf).exists() for suf in SRT_SUFFIXES):
        return True
    subs = media_info.get("subtitles") or media_info.get("subtitleLanguages") or ""
    if isinstance(subs, str):
        subs = [s.strip() for s in subs.replace("/", ",").split(",")]
    return bool(NL_CODES & {str(s).lower() for s in subs})


def owned_roots(ctx: AppContext) -> list[str]:
    rows = ctx.db.fetch_all("SELECT DISTINCT root_folder_path AS r FROM lib_items WHERE "
                            "root_folder_path IS NOT NULL AND root_folder_path!=''")
    return [str(r["r"]).rstrip("/") for r in rows]


def downloads_today(ctx: AppContext) -> int:
    day = datetime.now(UTC).strftime("%Y-%m-%d")
    row = ctx.db.fetch_one("SELECT COUNT(*) AS n FROM subtitle_downloads WHERE status='saved' "
                           "AND ts>=?", (day,))
    return int(row["n"]) if row else 0


def _candidates(ctx: AppContext, limit: int) -> list[dict[str, Any]]:
    roots = owned_roots(ctx)
    rows = ctx.db.fetch_dicts(
        "SELECT f.instance_id, f.kind, f.provider_id, f.path, f.media_info_json, "
        "i.title, i.tmdb_id, i.imdb_id, e.season_number, e.episode_number "
        "FROM lib_files f "
        "LEFT JOIN lib_episodes e ON f.kind='episode' AND e.instance_id=f.instance_id "
        "  AND e.episode_file_id=f.provider_id "
        "LEFT JOIN lib_items i ON i.instance_id=f.instance_id AND "
        "  i.provider_id=f.owner_provider_id "
        "WHERE f.path IS NOT NULL ORDER BY f.date_added DESC LIMIT 2000")
    out = []
    for r in rows:
        if not r["path"] or not any(r["path"].startswith(root + "/") for root in roots):
            continue
        key = f"{r['instance_id']}:{r['kind']}:{r['provider_id']}"
        seen = ctx.db.fetch_one("SELECT searched_at FROM subtitle_status WHERE key=?", (key,))
        if seen and seen["searched_at"]:
            continue
        if has_nl(Path(r["path"]), loads(r["media_info_json"], {})):
            continue
        r["key"] = key
        out.append(r)
        if len(out) >= limit:
            break
    return out


async def fetch_opensubtitles(ctx: AppContext, client: OpenSubtitlesClient) -> dict[str, Any]:
    quota = int(_v(ctx, "opensubtitles_daily") or 10) - downloads_today(ctx)
    if quota <= 0:
        return {"saved": 0, "reason": "dagquotum bereikt"}
    saved = 0
    for r in _candidates(ctx, quota * 3):
        if saved >= quota:
            break
        video = Path(r["path"])
        srt = Path(str(video.with_suffix("")) + ".nl.srt")
        title = r["title"] or video.stem
        if r["season_number"] is not None and r["episode_number"] is not None:
            title = f"{title} S{int(r['season_number']):02d}E{int(r['episode_number']):02d}"
        ctx.db.execute(
            "INSERT INTO subtitle_status(key,instance_id,media_kind,provider_id,title,nl_missing,"
            "source,searched_at,updated_at) VALUES (?,?,?,?,?,1,'opensubtitles',?,?) "
            "ON CONFLICT(key) DO UPDATE SET searched_at=excluded.searched_at, "
            "updated_at=excluded.updated_at",
            (r["key"], r["instance_id"], r["kind"], r["provider_id"], title, utcnow(),
             utcnow()))
        try:
            file_id = await client.search(
                tmdb_id=r["tmdb_id"], imdb_id=r["imdb_id"],
                season=r["season_number"], episode=r["episode_number"])
            if file_id is None:
                continue
            if srt.exists():  # nooit overschrijven
                _journal(ctx, video, srt, file_id, 0, "skipped", "bestaat al")
                continue
            data = await client.download(file_id)
        except MediaServerError as exc:
            LOGGER.info("OpenSubtitles: %s", exc)
            if "quotum" in str(exc):
                break
            continue
        tmp = srt.with_name(srt.name + ".mg-part")
        try:
            tmp.write_bytes(data)
            if srt.exists():
                tmp.unlink()
                _journal(ctx, video, srt, file_id, 0, "skipped", "bestaat al")
                continue
            tmp.rename(srt)
        except OSError as exc:
            tmp.unlink(missing_ok=True)
            _journal(ctx, video, srt, file_id, 0, "failed", str(exc))
            continue
        _journal(ctx, video, srt, file_id, len(data), "saved", "Nederlandse ondertitel opgehaald")
        ctx.db.execute("UPDATE subtitle_status SET nl_missing=0, source='file', updated_at=? "
                       "WHERE key=?", (utcnow(), r["key"]))
        audit(ctx.db, actor="svc:subtitles", action="subtitles.downloaded", target_type="file",
              target_id=r["provider_id"], instance_id=r["instance_id"],
              new_value={"srt": str(srt), "provider": "opensubtitles"})
        saved += 1
    ctx.state_set("subtitles_last", {"at": utcnow(), "source": "opensubtitles",
                                     "saved": saved})
    return {"saved": saved}


def _journal(ctx: AppContext, video: Path, srt: Path, file_id: str, size: int, status: str,
             detail: str) -> None:
    ctx.db.execute(
        "INSERT INTO subtitle_downloads(ts,video_path,srt_path,provider,file_id,size,status,"
        "detail_nl) VALUES (?,?,?,?,?,?,?,?)",
        (utcnow(), str(video), str(srt), "opensubtitles", file_id, size, status, detail[:300]))


async def tick(ctx: AppContext) -> dict[str, Any]:
    if not bool(_v(ctx, "nl_auto")) or not nl_wanted(ctx):
        return {"skipped": "uit"}
    b = bazarr(ctx)
    if b is not None:
        try:
            return {"bazarr": await sync_bazarr(ctx, b)}
        except MediaServerError as exc:
            ctx.state_set("subtitles_last", {"at": utcnow(), "source": "bazarr",
                                             "error": str(exc)})
            return {"error": str(exc)}
    o = opensubtitles(ctx)
    if o is not None:
        return {"opensubtitles": await fetch_opensubtitles(ctx, o)}
    return {"skipped": "geen bron"}


def missing(ctx: AppContext, limit: int = 100) -> dict[str, Any]:
    rows = ctx.db.fetch_dicts(
        "SELECT key, instance_id, media_kind, provider_id, title, source, searched_at, "
        "updated_at FROM subtitle_status WHERE nl_missing=1 ORDER BY title LIMIT ?", (limit,))
    total = ctx.db.fetch_one("SELECT COUNT(*) AS n FROM subtitle_status WHERE nl_missing=1")
    return {"total": int(total["n"]) if total else 0, "items": rows}
