"""Hiërarchisch bewijs → identiteitsstatus (E1).

Hypothese = wat Sonarr/Radarr zegt dat het bestand is (``expected``). Bewijs ondersteunt
of weerspreekt die hypothese. Gewichten volgen de bewijshiërarchie:

==========================  ======  ==========================================
bron                        gewicht  toelichting
==========================  ======  ==========================================
user_confirmed               100    expliciete bevestiging in de UI
hash_known                    95    bestandshash hoort bij bekende aflevering
download_history              85    download-ID: release is gegrabd vóór aflevering X
fork_link                     50    koppeling in Sonarr/Radarr zelf (= hypothese)
series_ids                    60    TVDb/TMDb/IMDb-ID's (serie-niveau)
anime_mapping                 50    TheXEM/AniDB absolute ↔ seizoen-mapping
filename / release_title      25    naamparse (scene-/anime-nummering)
duration                      10    technische metadata (speelduur)
hash_unknown                   0    onbekende hash is GEEN mismatch
==========================  ======  ==========================================

Regels (veiligheid boven alles):
- ``mismatch`` vereist minstens één *onafhankelijk* tegenbewijs met gewicht ≥ drempel
  (standaard 80) én geen even sterk ondersteunend bewijs. Zwakke signalen (naam, duur)
  kunnen samen nooit een mismatch opleveren — hooguit ``uncertain``.
- ``actionable`` alleen bij mismatch waarvan alle sterke tegenbewijzen dezelfde
  werkelijke aflevering aanwijzen.
"""

from __future__ import annotations

import re
import unicodedata
from dataclasses import asdict, dataclass, field
from typing import Any, Literal

from mediaguard.identity.parser import ParsedRelease, parse_release

Status = Literal["verified", "probable", "uncertain", "mismatch", "unresolved"]
Relation = Literal["supports", "contradicts", "neutral"]

WEIGHTS: dict[str, int] = {
    "user_confirmed": 100,
    "hash_known": 95,
    "download_history": 85,
    "series_ids": 60,
    "fork_link": 50,
    "anime_mapping": 50,
    "filename": 25,
    "release_title": 25,
    "duration": 10,
    "hash_unknown": 0,
}
TIERS: dict[str, int] = {
    "user_confirmed": 0, "hash_known": 1, "download_history": 1, "fork_link": 1,
    "series_ids": 2, "anime_mapping": 3, "filename": 4, "release_title": 4, "duration": 4,
    "hash_unknown": 4,
}
# bewijs dat niet van de koppeling zelf afgeleid is
INDEPENDENT = {"user_confirmed", "hash_known", "download_history", "series_ids",
               "anime_mapping", "filename", "release_title", "duration"}
STATUS_LABELS_NL = {
    "verified": "Geverifieerd", "probable": "Waarschijnlijk", "uncertain": "Onzeker",
    "mismatch": "Verkeerde aflevering", "unresolved": "Onbepaald",
}


@dataclass(frozen=True)
class EpisodeRef:
    season: int | None = None
    episode: int | None = None
    absolute: int | None = None

    def label(self) -> str:
        if self.season is not None and self.episode is not None:
            text = f"S{self.season:02d}E{self.episode:02d}"
            if self.absolute is not None:
                text += f" (#{self.absolute})"
            return text
        if self.absolute is not None:
            return f"#{self.absolute}"
        return "?"

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> EpisodeRef:
        def _int(*names: str) -> int | None:
            for n in names:
                v = data.get(n)
                if v is not None and v != "":
                    return int(v)
            return None
        return cls(season=_int("season", "season_number", "seasonNumber"),
                   episode=_int("episode", "episode_number", "episodeNumber"),
                   absolute=_int("absolute", "absolute_episode_number",
                                 "absoluteEpisodeNumber"))


def ref_match(a: EpisodeRef, b: EpisodeRef) -> bool | None:
    """True/False als vergelijkbaar, None als de refs geen gemeenschappelijke as hebben."""
    if None not in (a.season, a.episode, b.season, b.episode):
        same = (a.season, a.episode) == (b.season, b.episode)
        if not same and a.absolute is not None and a.absolute == b.absolute:
            return True  # zelfde absolute = scene/tvdb-ordening verschilt
        return same
    if a.absolute is not None and b.absolute is not None:
        return a.absolute == b.absolute
    return None


def refs_match(expected: list[EpisodeRef], claimed: list[EpisodeRef]) -> bool | None:
    if not expected or not claimed:
        return None
    results: list[bool] = []
    for c in claimed:
        rs = [ref_match(e, c) for e in expected]
        known = [r for r in rs if r is not None]
        if not known:
            continue
        results.append(any(known))
    for e in expected:
        rs = [ref_match(e, c) for c in claimed]
        known = [r for r in rs if r is not None]
        if known:
            results.append(any(known))
    if not results:
        return None
    return all(results)


# Verhogen bij elke wijziging in de beoordelingsregels; oudere twijfelresultaten worden dan
# automatisch opnieuw beoordeeld (2 = seizoenspakketten/releasenaam 'bevat', 0.2.6).
ENGINE_VERSION = 2


def refs_covered(expected: list[EpisodeRef], container: list[EpisodeRef]) -> bool | None:
    """Zitten alle verwachte afleveringen in ``container``? Voor bronnen die méér mogen
    bevatten dan dit ene bestand, zoals een gegrabde seizoenspakket-release (S05E01–E12)
    waaruit per aflevering wordt geïmporteerd. Extra afleveringen zijn geen tegenbewijs."""
    if not expected or not container:
        return None
    results: list[bool] = []
    for e in expected:
        known = [r for r in (ref_match(e, c) for c in container) if r is not None]
        if known:
            results.append(any(known))
    if not results:
        return None
    return all(results)


@dataclass
class Evidence:
    source: str
    relation: Relation
    detail_nl: str
    claimed: list[EpisodeRef] = field(default_factory=list)
    weight: int = -1
    tier: int = -1
    data: dict[str, Any] = field(default_factory=dict)

    def __post_init__(self) -> None:
        if self.weight < 0:
            self.weight = WEIGHTS.get(self.source, 0)
        if self.tier < 0:
            self.tier = TIERS.get(self.source, 4)

    @property
    def independent(self) -> bool:
        return self.source in INDEPENDENT

    def to_dict(self) -> dict[str, Any]:
        d = asdict(self)
        d["claimed"] = [asdict(c) | {"label": c.label()} for c in self.claimed]
        d["independent"] = self.independent
        return d


@dataclass
class IdentityResult:
    status: Status
    confidence: float
    actionable: bool
    reason_nl: str
    expected: list[EpisodeRef]
    actual: list[EpisodeRef]
    evidence: list[Evidence]

    def to_dict(self) -> dict[str, Any]:
        return {
            "status": self.status,
            "status_label_nl": STATUS_LABELS_NL[self.status],
            "confidence": round(self.confidence, 3),
            "actionable": self.actionable,
            "reason_nl": self.reason_nl,
            "expected": [asdict(e) | {"label": e.label()} for e in self.expected],
            "actual": [asdict(e) | {"label": e.label()} for e in self.actual],
            "evidence": [e.to_dict() for e in self.evidence],
        }


@dataclass
class Observation:
    """Alles wat we over een bestand weten. Elk veld is optioneel."""

    path: str | None = None
    release_title: str | None = None
    grabbed_episodes: list[EpisodeRef] | None = None   # uit download-ID-historie
    grabbed_release_title: str | None = None
    hash_episodes: list[EpisodeRef] | None = None       # bekende hash → aflevering
    hash_checked: bool = False                          # hash opgezocht maar onbekend
    series_ids_expected: dict[str, Any] | None = None
    series_ids_observed: dict[str, Any] | None = None
    mapped_episodes: list[EpisodeRef] | None = None     # TheXEM/AniDB mapping van naam
    duration_seconds: float | None = None
    expected_runtime_minutes: float | None = None
    user_confirmed: list[EpisodeRef] | None = None
    user_rejected: bool = False
    fork_linked: bool = True
    is_movie: bool = False
    # films (Radarr): geen afleveringsnummers, wel titel/jaar/ID's
    movie_title: str | None = None
    movie_year: int | None = None
    movie_alt_titles: list[str] = field(default_factory=list)


def _parsed_refs(p: ParsedRelease) -> list[EpisodeRef]:
    if p.episodes and p.season is not None:
        return [EpisodeRef(p.season, e) for e in p.episodes]
    if p.absolute_episodes:
        return [EpisodeRef(absolute=a) for a in p.absolute_episodes]
    return []


def _labels(refs: list[EpisodeRef]) -> str:
    return ", ".join(r.label() for r in refs) or "?"


# -- films ----------------------------------------------------------------------------------
_ARTICLES = {"the", "a", "an", "de", "het", "een", "der", "die", "das", "le", "la", "les"}
_YEAR = re.compile(r"^(19[0-9]{2}|20[0-9]{2})$")
_TMDB = re.compile(r"tmdb(?:id)?[-_= ]?(\d{1,9})", re.IGNORECASE)
_IMDB = re.compile(r"\b(tt\d{7,9})\b", re.IGNORECASE)


def _tokens(text: str) -> list[str]:
    text = unicodedata.normalize("NFKD", text[:400])
    text = "".join(c for c in text if not unicodedata.combining(c)).lower().replace("&", " and ")
    return [t for t in re.split(r"[^a-z0-9]+", text) if t]


def _title_tokens(title: str) -> list[str]:
    toks = _tokens(title)
    return toks[1:] if len(toks) > 1 and toks[0] in _ARTICLES else toks


def _find(seq: list[str], sub: list[str]) -> int:
    if not sub:
        return -1
    for i in range(len(seq) - len(sub) + 1):
        if seq[i:i + len(sub)] == sub:
            return i
    return -1


def movie_label(obs: Observation) -> str:
    return f"{obs.movie_title} ({obs.movie_year})" if obs.movie_year else str(obs.movie_title)


def ids_in_name(name: str | None) -> dict[str, Any]:
    """TMDb-/IMDb-ID's die in een bestands- of releasenaam staan ({tmdb-603}, tt0133093)."""
    if not name:
        return {}
    out: dict[str, Any] = {}
    if m := _TMDB.search(name[:400]):
        out["tmdb_id"] = int(m.group(1))
    if m := _IMDB.search(name[:400]):
        out["imdb_id"] = m.group(1).lower()
    return out


def movie_name_evidence(obs: Observation) -> list[Evidence]:
    """Vergelijk titel en jaar in bestands-/releasenaam met de film die Radarr verwacht.
    Altijd zwak bewijs (gewicht 25): een andere taal of een remake mag nooit zelf iets
    vervangen. Alleen de bestandsnaam zelf, niet de map (die maakt Radarr zelf aan)."""
    if not obs.movie_title:
        return []
    titles = [t for t in (_title_tokens(x) for x in [obs.movie_title, *obs.movie_alt_titles])
              if t]
    out: list[Evidence] = []
    for source, name in (("filename", obs.path), ("release_title", obs.release_title)):
        if not name:
            continue
        base = name.replace("\\", "/").rsplit("/", 1)[-1]
        toks = _tokens(base)
        hit = next(((i, t) for t in titles if (i := _find(toks, t)) >= 0), None)
        rest = toks if hit is None else toks[:hit[0]] + toks[hit[0] + len(hit[1]):]
        years = {int(t) for t in rest if _YEAR.match(t)}
        year_ok = obs.movie_year is not None and any(abs(y - obs.movie_year) <= 1
                                                     for y in years)
        what = "Bestandsnaam" if source == "filename" else "Releasenaam"
        if hit is not None and (year_ok or not years or obs.movie_year is None):
            out.append(Evidence(source, "supports",
                                f"{what} noemt {movie_label(obs)}"))
        elif years and obs.movie_year is not None and not year_ok:
            parsed = parse_release(base)
            seen = f"{parsed.title or '?'} ({min(years)})" if hit is None else \
                f"jaar {min(years)}"
            out.append(Evidence(source, "contradicts",
                                f"{what} noemt {seen}, verwacht {movie_label(obs)}",
                                data={"years": sorted(years)}))
    return out


def gather_evidence(expected: list[EpisodeRef], obs: Observation,
                    duration_tolerance_percent: float = 15.0) -> list[Evidence]:
    ev: list[Evidence] = []
    exp = _labels(expected)
    if obs.user_confirmed is not None:
        rel = refs_match(expected, obs.user_confirmed)
        ev.append(Evidence("user_confirmed", "supports" if rel is not False else "contradicts",
                           f"Handmatig bevestigd als {_labels(obs.user_confirmed)}",
                           claimed=obs.user_confirmed))
    if obs.user_rejected:
        ev.append(Evidence("user_confirmed", "contradicts",
                           "Handmatig gemarkeerd als verkeerde aflevering"))
    if obs.fork_linked and expected:
        ev.append(Evidence("fork_link", "supports",
                           f"Sonarr/Radarr koppelt dit bestand aan {exp}",
                           claimed=list(expected)))
    if obs.is_movie and obs.fork_linked and obs.movie_title:
        ev.append(Evidence("fork_link", "supports",
                           f"Radarr koppelt dit bestand aan {movie_label(obs)}"))
    if obs.hash_episodes:
        rel = refs_match(expected, obs.hash_episodes)
        if rel is not None:
            ev.append(Evidence(
                "hash_known", "supports" if rel else "contradicts",
                f"Bestandshash hoort bij {_labels(obs.hash_episodes)}",
                claimed=obs.hash_episodes))
    elif obs.hash_checked:
        ev.append(Evidence("hash_unknown", "neutral",
                           "Bestandshash onbekend; dat is geen aanwijzing voor een fout"))
    if obs.grabbed_episodes:
        rel = refs_covered(expected, obs.grabbed_episodes)
        if rel is not None:
            title = f" ('{obs.grabbed_release_title}')" if obs.grabbed_release_title else ""
            ev.append(Evidence(
                "download_history", "supports" if rel else "contradicts",
                f"Download is gegrabd voor {_labels(obs.grabbed_episodes)}{title}",
                claimed=obs.grabbed_episodes))
    if obs.is_movie:
        ev.extend(movie_name_evidence(obs))
        if obs.series_ids_expected and not obs.series_ids_observed:
            found: dict[str, Any] = {}
            for name in (obs.path, obs.release_title):
                found |= ids_in_name(name)
            obs.series_ids_observed = found or None
    if obs.series_ids_expected and obs.series_ids_observed:
        shared = [k for k in obs.series_ids_expected
                  if obs.series_ids_expected.get(k) and obs.series_ids_observed.get(k)]
        if shared:
            same = all(str(obs.series_ids_expected[k]) == str(obs.series_ids_observed[k])
                       for k in shared)
            ev.append(Evidence(
                "series_ids", "supports" if same else "contradicts",
                "Serie-/film-ID's komen overeen" if same else
                "Serie-/film-ID's wijken af (" + ", ".join(shared) + ")"))
    if obs.mapped_episodes:
        rel = refs_match(expected, obs.mapped_episodes)
        if rel is not None:
            ev.append(Evidence(
                "anime_mapping", "supports" if rel else "contradicts",
                f"Animemapping (absolute/scene) wijst naar {_labels(obs.mapped_episodes)}",
                claimed=obs.mapped_episodes))
    for source, name in (("filename", obs.path), ("release_title", obs.release_title)):
        if not name or obs.is_movie:
            continue
        parsed = parse_release(name)
        refs = _parsed_refs(parsed)
        # een release (pakket) mag meer afleveringen bevatten; de bestandsnaam moet kloppen
        rel = refs_match(expected, refs) if source == "filename" else \
            refs_covered(expected, refs)
        if rel is None:
            continue
        what = "Bestandsnaam" if source == "filename" else "Releasenaam"
        ev.append(Evidence(source, "supports" if rel else "contradicts",
                           f"{what} noemt {_labels(refs)}", claimed=refs,
                           data={"confidence": parsed.confidence}))
    if obs.duration_seconds and obs.expected_runtime_minutes:
        expected_s = obs.expected_runtime_minutes * 60
        diff = abs(obs.duration_seconds - expected_s) / expected_s * 100
        if diff <= duration_tolerance_percent:
            minutes = obs.expected_runtime_minutes
            ev.append(Evidence("duration", "supports",
                               f"Speelduur past bij verwachte {minutes:g} min"))
        elif diff >= 2 * duration_tolerance_percent:
            ev.append(Evidence("duration", "contradicts",
                               f"Speelduur wijkt {diff:.0f}% af van verwachte "
                               f"{obs.expected_runtime_minutes:g} min",
                               data={"difference_percent": round(diff, 1)}))
    return ev


def resolve(expected: list[EpisodeRef], obs: Observation, *,
            mismatch_threshold: int = 80,
            duration_tolerance_percent: float = 15.0) -> IdentityResult:
    evidence = gather_evidence(expected, obs, duration_tolerance_percent)
    return decide(expected, evidence, mismatch_threshold=mismatch_threshold)


def decide(expected: list[EpisodeRef], evidence: list[Evidence], *,
           mismatch_threshold: int = 80) -> IdentityResult:
    supports = [e for e in evidence if e.relation == "supports"]
    contra = [e for e in evidence if e.relation == "contradicts"]
    independent_support = [e for e in supports if e.independent]
    strong_support = [e for e in independent_support if e.weight >= mismatch_threshold]
    strong_contra = [e for e in contra if e.independent and e.weight >= mismatch_threshold]
    support_w = sum(e.weight for e in independent_support)
    contra_w = sum(e.weight for e in contra)
    total = support_w + contra_w

    def conf(value: float) -> float:
        return max(0.0, min(1.0, value))

    if strong_contra and not strong_support:
        claims = {tuple(e.claimed) for e in strong_contra if e.claimed}
        actual = list(next(iter(claims))) if len(claims) == 1 else []
        actionable = len(claims) == 1 and all(e.claimed for e in strong_contra)
        reason = "Sterk onafhankelijk bewijs weerspreekt de koppeling: " + "; ".join(
            e.detail_nl for e in strong_contra)
        if not actionable:
            reason += ". Tegenbewijs wijst niet eenduidig één aflevering aan: handmatige review."
        return IdentityResult("mismatch", conf(contra_w / max(total, 1)), actionable, reason,
                              expected, actual, evidence)
    if strong_contra and strong_support:
        return IdentityResult(
            "uncertain", 0.5, False,
            "Sterk bewijs spreekt elkaar tegen: " + "; ".join(
                e.detail_nl for e in strong_contra + strong_support) + ". Handmatige review.",
            expected, [], evidence)
    if strong_support:
        weak_contra = [e for e in contra if e.weight >= 25]
        if weak_contra:
            return IdentityResult(
                "probable", conf(support_w / max(total, 1)), False,
                "Sterk bewijs bevestigt de koppeling; zwakke afwijking genegeerd: "
                + "; ".join(e.detail_nl for e in weak_contra), expected, list(expected),
                evidence)
        return IdentityResult("verified", conf(0.8 + support_w / 1000), False,
                              "Bevestigd door " + "; ".join(e.detail_nl for e in strong_support),
                              expected, list(expected), evidence)
    if contra and support_w > contra_w:
        return IdentityResult(
            "probable", conf(0.4 + (support_w - contra_w) / 200), False,
            "Ondersteund door " + "; ".join(e.detail_nl for e in independent_support)
            + "; zwakke afwijking: " + "; ".join(e.detail_nl for e in contra),
            expected, list(expected), evidence)
    if contra:
        return IdentityResult(
            "uncertain", conf(0.5 - contra_w / 400), False,
            "Alleen zwakke aanwijzingen voor een afwijking (" + "; ".join(
                e.detail_nl for e in contra) + "). Onvoldoende bewijs om iets te vervangen.",
            expected, [], evidence)
    if independent_support:
        return IdentityResult("probable", conf(0.5 + support_w / 200), False,
                              "Ondersteund door " + "; ".join(
                                  e.detail_nl for e in independent_support),
                              expected, list(expected), evidence)
    return IdentityResult("unresolved", 0.0, False,
                          "Geen onafhankelijk bewijs beschikbaar", expected, [], evidence)
