"""Bibliotheekbron voor de optimalisatieplanner.

``LibraryProvider`` is een klein interface. ``DbLibraryProvider`` leest B1's leescache
(``lib_files``/``lib_items``/``lib_episodes``, migratie 021) *defensief*: ontbreken
tabellen of kolommen, dan levert hij niets in plaats van te crashen. Tests en
toekomstige bronnen gebruiken ``StaticProvider``.
"""

from __future__ import annotations

import os
from collections.abc import Iterable, Iterator
from dataclasses import asdict, dataclass, field
from typing import TYPE_CHECKING, Any, Protocol

from mediaguard.audio.languages import normalize_list
from mediaguard.db import loads

if TYPE_CHECKING:
    from mediaguard.context import AppContext


@dataclass
class LibraryItem:
    instance_id: str
    media_kind: str                      # episode_file | movie_file | episode | movie
    provider_id: str
    title: str = ""
    path: str | None = None
    size: int | None = None
    media_type: str | None = None        # tv | anime | movie | anime_movie
    series_id: int | None = None
    movie_id: int | None = None
    season: int | None = None
    episode_ids: list[int] = field(default_factory=list)
    monitored: bool = True
    has_file: bool = True
    cutoff_unmet: bool = False
    codec: str | None = None
    height: int | None = None
    bitrate_kbps: int | None = None
    dynamic_range: str = ""              # '', HDR10, HDR10Plus, DV, DV HDR10, HLG
    audio_languages: list[str] = field(default_factory=list)
    subtitle_languages: list[str] = field(default_factory=list)
    nlink: int | None = None
    protected: bool = False
    corrupt: bool = False
    duplicate: bool = False
    identity_status: str | None = None
    identity_actionable: bool = False

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


class LibraryProvider(Protocol):
    def items(self, ctx: AppContext, filters: dict[str, Any]) -> Iterable[LibraryItem]: ...


class StaticProvider:
    def __init__(self, items: list[LibraryItem]) -> None:
        self._items = items

    def items(self, ctx: AppContext, filters: dict[str, Any]) -> Iterable[LibraryItem]:
        return [i for i in self._items if _match(i, filters)]


def _match(item: LibraryItem, f: dict[str, Any]) -> bool:
    if f.get("instance_ids") and item.instance_id not in f["instance_ids"]:
        return False
    if f.get("media_types") and item.media_type not in f["media_types"]:
        return False
    if f.get("series_ids") and item.series_id not in f["series_ids"]:
        return False
    if f.get("movie_ids") and item.movie_id not in f["movie_ids"]:
        return False
    return not (f.get("season") is not None and item.season != f["season"])


def _seconds(runtime: Any) -> float:
    """'01:02:03', '45:12' of '1:02:03.5' (Sonarr/Radarr runTime) → seconden."""
    try:
        parts = [float(p) for p in str(runtime or "").split(":")]
    except ValueError:
        return 0.0
    total = 0.0
    for p in parts:
        total = total * 60 + p
    return total


def video_kbps(mi: dict[str, Any], size: Any) -> int | None:
    """Videobitrate zoals de encoder die meet (``probe.video_kbps``): de bitrate van het
    videospoor; ontbreekt die (Sonarr/Radarr melden vaak 0 voor MKV), dan bestandsgrootte /
    speelduur min de audiosporen. Zonder deze schatting koos de autopilot bestanden die de
    encoder daarna 'onder drempel' vond (rapport 4 nov, H3)."""
    direct = mi.get("videoBitrate")
    if isinstance(direct, int | float) and direct > 0:
        return int(direct) // 1000
    secs = _seconds(mi.get("runTime"))
    try:
        total_bits = int(size or 0) * 8
    except (TypeError, ValueError):
        return None
    if secs <= 0 or total_bits <= 0:
        return None
    audio = mi.get("audioBitrate")
    streams = mi.get("audioStreamCount")
    audio_bps = (float(audio) if isinstance(audio, int | float) and audio > 0 else 0.0) * (
        int(streams) if isinstance(streams, int) and streams > 0 else 1)
    kbps = int((total_bits / secs - audio_bps) / 1000)
    return kbps if kbps > 0 else None


def _height(res: Any) -> int | None:
    text = str(res or "")
    if "x" in text:
        try:
            return int(text.split("x", 1)[1])
        except ValueError:
            return None
    try:
        return int(text) if text else None
    except ValueError:
        return None


class DbLibraryProvider:
    """Leest B1's cache. Alle toegang is optioneel en fouttolerant."""

    def _tables(self, ctx: AppContext) -> set[str]:
        return {r["name"] for r in ctx.db.fetch_all(
            "SELECT name FROM sqlite_master WHERE type='table'")}

    def _columns(self, ctx: AppContext, table: str) -> set[str]:
        return {r["name"] for r in ctx.db.fetch_all(f"PRAGMA table_info({table})")}

    def items(self, ctx: AppContext, filters: dict[str, Any]) -> Iterator[LibraryItem]:
        tables = self._tables(ctx)
        if "lib_files" not in tables:
            return
        cols = self._columns(ctx, "lib_files")
        needed = {"instance_id", "kind", "provider_id", "owner_provider_id", "path", "size"}
        if not needed <= cols:
            return
        media_types: dict[str, str] = {}
        if "instances" in tables:
            media_types = {r["id"]: r["media_type"] for r in ctx.db.fetch_all(
                "SELECT id, media_type FROM instances")}
        titles: dict[tuple[str, str, int], tuple[str, bool]] = {}
        if "lib_items" in tables:
            for r in ctx.db.fetch_all("SELECT instance_id, kind, provider_id, title, monitored "
                                      "FROM lib_items"):
                titles[(r["instance_id"], r["kind"], int(r["provider_id"]))] = (
                    r["title"], bool(r["monitored"]))
        extra = [c for c in ("quality_cutoff_not_met", "media_info_json", "languages_json")
                 if c in cols]
        sql = ("SELECT instance_id, kind, provider_id, owner_provider_id, path, size"
               + "".join(f", {c}" for c in extra) + " FROM lib_files")
        if filters.get("av1_candidates") and "media_info_json" in cols:
            # AV1-autopilot: AV1-bestanden overslaan en de grootste eerst (meeste winst),
            # zodat de autopilot na een handvol kandidaten kan stoppen
            sql += (" WHERE LOWER(COALESCE(json_extract(media_info_json,'$.videoCodec'),'')) "
                    "NOT IN ('av1','av01') ORDER BY size DESC, instance_id, provider_id")
            if filters.get("limit"):
                sql += f" LIMIT {int(filters['limit'])} OFFSET {int(filters.get('offset') or 0)}"
        stat_files = filters.get("stat", True)
        files = ctx.db.fetch_all(sql)
        episodes: dict[tuple[str, int], list[tuple[int, int, bool]]] = {}
        if "lib_episodes" in tables:
            ep_sql = ("SELECT instance_id, provider_id, season_number, episode_file_id, "
                      "monitored FROM lib_episodes WHERE episode_file_id IS NOT NULL")
            params: list[Any] = []
            if filters.get("limit"):
                # alleen de afleveringen van deze portie (niet alle ~170k)
                ids = [int(r["provider_id"]) for r in files if r["kind"] == "episode"]
                ep_sql += f" AND episode_file_id IN ({','.join('?' * len(ids)) or '-1'})"
                params = ids
            for r in ctx.db.fetch_all(ep_sql, params):
                episodes.setdefault((r["instance_id"], int(r["episode_file_id"])), []).append(
                    (int(r["provider_id"]), int(r["season_number"]), bool(r["monitored"])))
        for r in files:
            row = dict(r)
            instance = row["instance_id"]
            is_movie = row["kind"] == "movie"
            owner = int(row["owner_provider_id"])
            title, monitored = titles.get(
                (instance, "movie" if is_movie else "series", owner), ("", True))
            eps = episodes.get((instance, int(row["provider_id"])), [])
            mi = loads(row.get("media_info_json"), {}) or {}
            item = LibraryItem(
                instance_id=instance, media_kind="movie_file" if is_movie else "episode_file",
                provider_id=str(row["provider_id"]), title=title, path=row["path"],
                size=row["size"], media_type=media_types.get(instance),
                series_id=None if is_movie else owner, movie_id=owner if is_movie else None,
                season=eps[0][1] if eps else None, episode_ids=[e[0] for e in eps],
                monitored=monitored and (all(e[2] for e in eps) if eps else True),
                cutoff_unmet=bool(row.get("quality_cutoff_not_met")),
                codec=str(mi.get("videoCodec") or "") or None,
                height=_height(mi.get("resolution")),
                bitrate_kbps=video_kbps(mi, row["size"]),
                dynamic_range=str(mi.get("videoDynamicRangeType") or mi.get(
                    "videoDynamicRange") or ""),
                audio_languages=normalize_list(mi.get("audioLanguages")),
                subtitle_languages=normalize_list(mi.get("subtitles")))
            if row["path"] and stat_files:
                try:
                    item.nlink = os.stat(row["path"]).st_nlink
                except OSError:
                    item.nlink = None
            if _match(item, filters):
                yield item


def get_provider(ctx: AppContext) -> LibraryProvider:
    override = ctx.services.get("library_provider")
    return override if override is not None else DbLibraryProvider()  # type: ignore[no-any-return]
