"""Identity-service: bewijs verzamelen uit DB (historie, hashes, mappings), beslissen,
resultaat met bewijs opslaan. Geen netwerkcalls (veilig voor hooks)."""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

from mediaguard.db import dumps, loads, utcnow
from mediaguard.identity import adapters
from mediaguard.identity.engine import (
    ENGINE_VERSION,
    EpisodeRef,
    IdentityResult,
    Observation,
    resolve,
)
from mediaguard.identity.parser import parse_release
from mediaguard.settings_registry import SettingDef

if TYPE_CHECKING:
    from mediaguard.context import AppContext

CATEGORY = "Identiteit"


def setting_defs() -> list[SettingDef]:
    scopes = ("global", "media_type", "instance", "series", "movie")
    return [
        SettingDef("identity.mismatch_min_weight", "int", 80, CATEGORY,
                   "Minimale bewijssterkte voor 'verkeerde aflevering'",
                   "Minimum evidence weight for mismatch",
                   help_nl="Alleen onafhankelijk bewijs met minstens dit gewicht (hash 95, "
                           "download-historie 85) kan een mismatch opleveren. Bestandsnaam (25)"
                           " en speelduur (10) kunnen dat nooit.",
                   minimum=60, maximum=100, scopes=scopes, advanced=True),
        SettingDef("identity.duration_tolerance_percent", "int", 15, CATEGORY,
                   "Tolerantie speelduur (%)", "Duration tolerance (%)",
                   help_nl="Afwijking die nog als passend geldt. Speelduur is altijd zwak bewijs.",
                   minimum=1, maximum=100, scopes=scopes),
        SettingDef("identity.auto_replace_on_mismatch", "bool", True, CATEGORY,
                   "Automatisch vervangen bij bewezen verkeerde aflevering",
                   "Auto-replace on proven mismatch",
                   help_nl="Alleen bij 'mismatch' met eenduidig sterk bewijs, ook voor films. "
                           "Het bestand gaat eerst naar de quarantaine (herstelbaar), daarna "
                           "zoekt MediaGuard de juiste. Bij twijfel volgt altijd review.",
                   scopes=scopes),
        SettingDef("identity.thexem_enabled", "bool", True, CATEGORY,
                   "TheXEM-mapping gebruiken", "Use TheXEM mappings",
                   help_nl="Haalt scene/absolute-nummering op van thexem.info (gecachet).",
                   scopes=("global", "media_type", "instance", "series")),
        SettingDef("identity.remember_verified_hashes", "bool", True, CATEGORY,
                   "Hashes van geverifieerde bestanden onthouden",
                   "Remember hashes of verified files",
                   help_nl="Een latere kopie met dezelfde hash krijgt sterk bewijs."),
    ]


def _chain(instance_id: str, kind: str, series_id: Any = None, movie_id: Any = None
           ) -> dict[str, str]:
    chain = {"instance": instance_id}
    if series_id is not None:
        chain["series"] = f"{instance_id}:{series_id}"
    if movie_id is not None:
        chain["movie"] = f"{instance_id}:{movie_id}"
    return chain


def grabbed_for_download(ctx: AppContext, instance_id: str, download_id: str | None
                         ) -> tuple[list[EpisodeRef], str | None] | None:
    """Wat is er volgens de Grab-historie voor deze download-ID gegrabd?"""
    if not download_id:
        return None
    row = ctx.db.fetch_one(
        "SELECT episodes_json, release_title FROM release_provenance WHERE instance_id=? "
        "AND download_id=? AND event_type='Grab' ORDER BY id DESC LIMIT 1",
        (instance_id, download_id))
    if row is None:
        return None
    refs = [EpisodeRef.from_dict(e) for e in loads(row["episodes_json"], [])]
    return (refs, row["release_title"]) if refs else None


def evaluate(
    ctx: AppContext,
    *,
    instance_id: str,
    kind: str,
    provider_id: str | int,
    expected: list[EpisodeRef],
    path: str | None = None,
    release_title: str | None = None,
    download_id: str | None = None,
    hashes: dict[str, str] | None = None,
    series: dict[str, Any] | None = None,
    observed_series_ids: dict[str, Any] | None = None,
    duration_seconds: float | None = None,
    expected_runtime_minutes: float | None = None,
    is_movie: bool = False,
    movie: dict[str, Any] | None = None,
    source: str = "api",
    actor: str | None = None,
    store: bool = True,
) -> IdentityResult:
    if movie is not None:
        is_movie = True
        series = series or {k: movie.get(k) for k in ("id", "tmdb_id", "imdb_id")}
    series_id = (series or {}).get("id")
    chain = _chain(instance_id, kind, series_id=None if is_movie else series_id,
                   movie_id=series_id if is_movie else None)
    threshold = int(ctx.config.value("identity.mismatch_min_weight", chain))
    tolerance = float(ctx.config.value("identity.duration_tolerance_percent", chain))

    obs = Observation(path=path, release_title=release_title, is_movie=is_movie,
                      duration_seconds=duration_seconds,
                      expected_runtime_minutes=expected_runtime_minutes,
                      series_ids_observed=observed_series_ids)
    if movie:
        year = movie.get("year")
        obs.movie_title = movie.get("title") or None
        obs.movie_year = int(year) if str(year or "").isdigit() and int(year) > 0 else None
        obs.movie_alt_titles = [str(t) for t in movie.get("alt_titles") or [] if t][:20]
    if series:
        obs.series_ids_expected = {k: series.get(k) for k in ("tvdb_id", "tmdb_id", "imdb_id")
                                   if series.get(k)}
    grabbed = grabbed_for_download(ctx, instance_id, download_id)
    if grabbed:
        obs.grabbed_episodes, obs.grabbed_release_title = grabbed
    for hash_type, value in (hashes or {}).items():
        found = adapters.lookup_hash(ctx.db, hash_type, value)
        obs.hash_checked = True
        if found:
            obs.hash_episodes = found
            break
    key = adapters.series_key(series)
    if key and not is_movie:
        for name in (path, release_title):
            if not name:
                continue
            parsed = parse_release(name)
            refs = ([EpisodeRef(absolute=a) for a in parsed.absolute_episodes]
                    if parsed.absolute_episodes else
                    [EpisodeRef(parsed.season, e) for e in parsed.episodes]
                    if parsed.season is not None else [])
            if refs:
                mapped = adapters.map_refs(ctx.db, key, refs)
                if mapped:
                    obs.mapped_episodes = mapped
                    break
    # handmatige beslissing (laatste) telt als sterkste bewijs
    manual = ctx.db.fetch_one(
        "SELECT status, actual_json FROM identity_results WHERE instance_id=? AND kind=? AND "
        "provider_id=? AND source='user' ORDER BY id DESC LIMIT 1",
        (instance_id, kind, str(provider_id)))
    if manual is not None:
        if manual["status"] == "verified":
            obs.user_confirmed = [EpisodeRef.from_dict(e)
                                  for e in loads(manual["actual_json"], [])] or list(expected)
        elif manual["status"] == "mismatch":
            obs.user_rejected = True

    result = resolve(expected, obs, mismatch_threshold=threshold,
                     duration_tolerance_percent=tolerance)
    if store:
        save(ctx, instance_id=instance_id, kind=kind, provider_id=provider_id, path=path,
             result=result, source=source, actor=actor)
        if (result.status == "verified" and hashes and len(expected) == 1
                and ctx.config.value("identity.remember_verified_hashes")
                and any(e.source in ("download_history", "user_confirmed")
                        for e in result.evidence if e.relation == "supports")):
            for hash_type, value in hashes.items():
                adapters.remember_hash(ctx.db, hash_type, value, expected[0],
                                       source=f"verified:{source}", instance_id=instance_id,
                                       series_key=key)
    return result


def save(ctx: AppContext, *, instance_id: str, kind: str, provider_id: str | int,
         path: str | None, result: IdentityResult, source: str, actor: str | None) -> int:
    data = result.to_dict()
    return ctx.db.execute_insert(
        "INSERT INTO identity_results(instance_id,kind,provider_id,file_path,status,confidence,"
        "actionable,expected_json,actual_json,evidence_json,reason_nl,source,created_at,"
        "created_by,engine_version) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
        (instance_id, kind, str(provider_id), path, result.status, result.confidence,
         int(result.actionable), dumps(data["expected"]), dumps(data["actual"]),
         dumps(data["evidence"]), result.reason_nl, source, utcnow(), actor, ENGINE_VERSION))


def latest(ctx: AppContext, instance_id: str, kind: str, provider_id: str | int
           ) -> dict[str, Any] | None:
    row = ctx.db.fetch_one(
        "SELECT * FROM identity_results WHERE instance_id=? AND kind=? AND provider_id=? "
        "ORDER BY id DESC LIMIT 1", (instance_id, kind, str(provider_id)))
    return None if row is None else row_to_dict(row)


def row_to_dict(row: Any) -> dict[str, Any]:
    d = dict(row)
    for col in ("expected_json", "actual_json", "evidence_json"):
        d[col[:-5]] = loads(d.pop(col), [])
    d["actionable"] = bool(d["actionable"])
    from mediaguard.identity.engine import STATUS_LABELS_NL

    d["status_nl"] = STATUS_LABELS_NL.get(str(d.get("status")), str(d.get("status") or ""))
    return d


# -- sleutels: één bestand, meerdere manieren om het aan te wijzen ------------------------------
# Pre-import slaat ``episode_file`` op met de Sonarr-afleverings-ID's ("12,13"); post-import
# met de episodefile-ID; de UI vraagt per aflevering (``episode``) of film (``movie``).
FILE_KIND = {"episode": "episode_file", "movie": "movie_file"}


def _ids(provider_id: Any) -> list[int]:
    out = []
    for part in str(provider_id or "").split(","):
        if part.strip().isdigit():
            out.append(int(part))
    return out


def keyed_by_episode_ids(ctx: AppContext, instance_id: str, kind: str,
                         provider_id: Any) -> bool:
    """Wijst deze ``episode_file``-sleutel afleverings-ID's aan (en geen episodefile-ID)?"""
    if kind == "episode":
        return True
    if kind != "episode_file":
        return False
    if "," in str(provider_id):
        return True
    first = ctx.db.fetch_one(
        "SELECT source FROM identity_results WHERE instance_id=? AND kind=? AND provider_id=? "
        "ORDER BY id LIMIT 1", (instance_id, kind, str(provider_id)))
    return first is not None and first["source"] in ("pre_import", "api", "user")


def episode_ids(ctx: AppContext, instance_id: str, kind: str, provider_id: Any) -> list[int]:
    """Sonarr-afleverings-ID's waar een identiteitsresultaat over gaat."""
    ids = _ids(provider_id)
    if not ids or kind not in ("episode", "episode_file"):
        return []
    if keyed_by_episode_ids(ctx, instance_id, kind, provider_id):
        return ids
    rows = ctx.db.fetch_all("SELECT provider_id FROM lib_episodes WHERE instance_id=? AND "
                            "episode_file_id=? ORDER BY provider_id", (instance_id, ids[0]))
    return [int(r["provider_id"]) for r in rows]


def related_keys(ctx: AppContext, instance_id: str, kind: str,
                 provider_id: Any) -> list[tuple[str, str]]:
    """Alle (kind, provider_id)-sleutels die hetzelfde bestand/dezelfde aflevering aanwijzen."""
    pid = str(provider_id)
    keys = [(kind, pid)]
    file_kind = FILE_KIND.get(kind, kind)
    if file_kind != kind:
        keys.append((file_kind, pid))
    if kind == "episode" and pid.isdigit():
        rows = ctx.db.fetch_all(
            "SELECT DISTINCT provider_id FROM identity_results WHERE instance_id=? AND "
            "kind='episode_file' AND (provider_id LIKE ? OR provider_id LIKE ? OR "
            "provider_id LIKE ?)", (instance_id, f"{pid},%", f"%,{pid}", f"%,{pid},%"))
        keys += [("episode_file", str(r["provider_id"])) for r in rows]
        ep = ctx.db.fetch_one("SELECT episode_file_id FROM lib_episodes WHERE instance_id=? AND "
                              "provider_id=?", (instance_id, int(pid)))
        if ep and ep["episode_file_id"]:
            fid = str(ep["episode_file_id"])
            if not keyed_by_episode_ids(ctx, instance_id, "episode_file", fid):
                keys.append(("episode_file", fid))
    return list(dict.fromkeys(keys))


def history(ctx: AppContext, instance_id: str, kind: str, provider_id: Any,
            limit: int = 20) -> list[dict[str, Any]]:
    keys = related_keys(ctx, instance_id, kind, provider_id)
    where = " OR ".join("(kind=? AND provider_id=?)" for _ in keys)
    rows = ctx.db.fetch_all(
        f"SELECT * FROM identity_results WHERE instance_id=? AND ({where}) "
        "ORDER BY id DESC LIMIT ?", (instance_id, *[v for k in keys for v in k], limit))
    return [row_to_dict(r) for r in rows]


def canonical_key(ctx: AppContext, instance_id: str, kind: str,
                  provider_id: Any) -> tuple[str, str]:
    """De sleutel van het actuele resultaat, zodat een nieuwe beoordeling het vervangt."""
    current = history(ctx, instance_id, kind, provider_id, limit=1)
    if current:
        return str(current[0]["kind"]), str(current[0]["provider_id"])
    return kind, str(provider_id)
