"""Optionele bewijsbronnen voor anime: TheXEM-mapping en een lokale hash-index.

- TheXEM (https://thexem.info) levert scene ↔ TVDb-nummering incl. absolute nummers.
  Echte HTTP-client met timeout; resultaten worden gecachet in ``anime_mappings`` zodat
  hooks nooit op het netwerk wachten (hooks lezen alleen de cache).
- Hash-index: hashes van bestanden waarvan de identiteit eerder *sterk* bevestigd is
  (of uit een AniDB/Shoko-export). Een onbekende hash is uitdrukkelijk geen mismatch.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

import httpx

from mediaguard.db import Database, utcnow
from mediaguard.identity.engine import EpisodeRef

if TYPE_CHECKING:
    from mediaguard.context import AppContext

THEXEM_URL = "https://thexem.info"


async def fetch_thexem(db: Database, tvdb_id: int, *, base_url: str = THEXEM_URL,
                       transport: httpx.AsyncBaseTransport | None = None,
                       timeout: float = 10.0) -> int:
    """Haal alle mappings voor een TVDb-serie op en vervang de cache. Geeft het aantal."""
    async with httpx.AsyncClient(base_url=base_url, timeout=timeout,
                                 transport=transport) as client:
        resp = await client.get("/map/all", params={"id": tvdb_id, "origin": "tvdb"})
        resp.raise_for_status()
        data = resp.json()
    if not isinstance(data, dict) or data.get("result") != "success":
        raise ValueError(f"TheXEM gaf geen geldig antwoord voor tvdb {tvdb_id}")
    rows = []
    for item in data.get("data") or []:
        scene = item.get("scene") or {}
        tvdb = item.get("tvdb") or {}
        rows.append((
            "thexem", f"tvdb:{tvdb_id}", scene.get("season"), scene.get("episode"),
            scene.get("absolute") or tvdb.get("absolute"), tvdb.get("season"),
            tvdb.get("episode"), utcnow(),
        ))
    with db.transaction() as conn:
        conn.execute("DELETE FROM anime_mappings WHERE provider='thexem' AND series_key=?",
                     (f"tvdb:{tvdb_id}",))
        conn.executemany(
            "INSERT INTO anime_mappings(provider,series_key,scene_season,scene_episode,"
            "absolute,tvdb_season,tvdb_episode,fetched_at) VALUES (?,?,?,?,?,?,?,?)", rows)
    return len(rows)


def map_refs(db: Database, series_key: str, refs: list[EpisodeRef]) -> list[EpisodeRef] | None:
    """Vertaal geparste refs (absolute of scene S/E) via de cache naar TVDb-refs."""
    out: list[EpisodeRef] = []
    for ref in refs:
        row = None
        if ref.absolute is not None and ref.season is None:
            row = db.fetch_one(
                "SELECT tvdb_season, tvdb_episode, absolute FROM anime_mappings "
                "WHERE series_key=? AND absolute=? ORDER BY provider='manual' DESC LIMIT 1",
                (series_key, ref.absolute))
        elif ref.season is not None and ref.episode is not None:
            row = db.fetch_one(
                "SELECT tvdb_season, tvdb_episode, absolute FROM anime_mappings "
                "WHERE series_key=? AND scene_season=? AND scene_episode=? "
                "ORDER BY provider='manual' DESC LIMIT 1",
                (series_key, ref.season, ref.episode))
        if row is None or row["tvdb_season"] is None:
            return None
        out.append(EpisodeRef(int(row["tvdb_season"]), int(row["tvdb_episode"]),
                              None if row["absolute"] is None else int(row["absolute"])))
    return out or None


def lookup_hash(db: Database, hash_type: str, value: str) -> list[EpisodeRef] | None:
    row = db.fetch_one(
        "SELECT season, episode, absolute FROM identity_known_hashes WHERE hash_type=? "
        "AND hash=?", (hash_type, value.lower()))
    if row is None:
        return None
    return [EpisodeRef(row["season"], row["episode"], row["absolute"])]


def remember_hash(db: Database, hash_type: str, value: str, ref: EpisodeRef, *,
                  source: str, size: int | None = None, instance_id: str | None = None,
                  series_key: str | None = None) -> None:
    db.execute(
        "INSERT INTO identity_known_hashes(hash_type,hash,size,instance_id,series_key,season,"
        "episode,absolute,source,verified_at) VALUES (?,?,?,?,?,?,?,?,?,?) "
        "ON CONFLICT(hash_type,hash) DO UPDATE SET season=excluded.season, "
        "episode=excluded.episode, absolute=excluded.absolute, source=excluded.source, "
        "verified_at=excluded.verified_at",
        (hash_type, value.lower(), size, instance_id, series_key, ref.season, ref.episode,
         ref.absolute, source, utcnow()))


def series_key(series: dict[str, Any] | None) -> str | None:
    if not series:
        return None
    tvdb = series.get("tvdb_id") or series.get("tvdbId")
    return f"tvdb:{tvdb}" if tvdb else None


def register_manual_mapping(ctx: AppContext, key: str, absolute: int, season: int,
                            episode: int) -> None:
    ctx.db.execute(
        "INSERT INTO anime_mappings(provider,series_key,scene_season,scene_episode,absolute,"
        "tvdb_season,tvdb_episode,fetched_at) VALUES ('manual',?,?,?,?,?,?,?)",
        (key, season, episode, absolute, season, episode, utcnow()))
