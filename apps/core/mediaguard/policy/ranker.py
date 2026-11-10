"""Deterministische release-ranker: harde regels eerst, dan lexicografische voorkeuren.

Volgorde (E2): identiteit & bestandsveiligheid (hard) > profielvolgorde, standaard
``audio > bron > resolutie > TRaSH/custom-format-score > groep``. Een profiel kan
``resolution`` vóór ``audio`` zetten (resolution-first); anime is audio-first.

Gelijke invoer geeft altijd dezelfde uitkomst (tie-break op titel).
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from mediaguard.audio.evaluate import AudioPolicy, evaluate_languages
from mediaguard.audio.languages import normalize_list
from mediaguard.identity.engine import EpisodeRef, refs_match
from mediaguard.identity.parser import ParsedRelease, parse_release
from mediaguard.policy.trash import CustomFormat, score

SOURCE_RANK = {"remux": 7, "bluray": 6, "webdl": 5, "webrip": 4, "hdtv": 3, "dvd": 2,
               "sdtv": 1}
SOURCE_LABEL = {"remux": "Remux", "bluray": "Blu-ray", "webdl": "WEB-DL", "webrip": "WEBRip",
                "hdtv": "HDTV", "dvd": "DVD", "sdtv": "SDTV"}
KEY_LABEL_NL = {"identity": "identiteit", "audio": "audio", "source": "bron",
                "resolution": "resolutie", "hdr": "HDR/Dolby Vision",
                "custom_format": "custom-format-score", "group": "releasegroep",
                "size": "bestandsgrootte"}


@dataclass
class Candidate:
    title: str
    size: int | None = None
    resolution: int | None = None
    source: str | None = None
    languages: list[str] = field(default_factory=list)
    custom_format_score: int | None = None
    custom_formats: list[str] = field(default_factory=list)
    release_group: str | None = None
    indexer: str | None = None
    guid: str | None = None

    @classmethod
    def from_dict(cls, d: dict[str, Any]) -> Candidate:
        return cls(title=str(d.get("title") or ""), size=d.get("size"),
                   resolution=d.get("resolution"), source=d.get("source"),
                   languages=list(d.get("languages") or []),
                   custom_format_score=d.get("custom_format_score"),
                   custom_formats=list(d.get("custom_formats") or []),
                   release_group=d.get("release_group"), indexer=d.get("indexer"),
                   guid=d.get("guid"))


@dataclass
class Rejection:
    code: str
    message_nl: str


@dataclass
class Ranked:
    candidate: Candidate
    parsed: ParsedRelease
    keys: dict[str, Any]
    labels: dict[str, str]
    audio: dict[str, Any]
    rejections: list[Rejection]
    order: list[str]
    cf_score: int
    cf_names: list[str]
    position: int = 0
    explanation_nl: str = ""

    @property
    def rejected(self) -> bool:
        return bool(self.rejections)

    def sort_key(self) -> tuple[Any, ...]:
        return tuple(self.keys[k] for k in ["identity", *self.order])

    def to_dict(self) -> dict[str, Any]:
        return {
            "title": self.candidate.title, "guid": self.candidate.guid,
            "position": self.position, "rejected": self.rejected,
            "rejections": [{"code": r.code, "message_nl": r.message_nl}
                           for r in self.rejections],
            "keys": {k: list(v) if isinstance(v, tuple) else v for k, v in self.keys.items()},
            "labels": self.labels, "audio": self.audio, "custom_format_score": self.cf_score,
            "custom_formats": self.cf_names, "explanation_nl": self.explanation_nl,
            "parsed": self.parsed.to_dict(),
        }


def _norm_group(g: str | None) -> str:
    return (g or "").strip().lower()


def evaluate_candidate(
    cand: Candidate, cfg: dict[str, Any], *, expected: list[EpisodeRef] | None = None,
    formats: list[CustomFormat] | None = None, false_claim_titles: set[str] | None = None,
    is_anime: bool = False,
) -> Ranked:
    parsed = parse_release(cand.title)
    order = list(cfg.get("ranking_order") or [])
    rejections: list[Rejection] = []
    labels: dict[str, str] = {}

    # ── identiteit (hard) ─────────────────────────────────────────────
    identity_key = 1
    labels["identity"] = "geen verwachting opgegeven"
    if expected:
        refs = ([EpisodeRef(parsed.season, e) for e in parsed.episodes]
                if parsed.season is not None and parsed.episodes else
                [EpisodeRef(absolute=a) for a in parsed.absolute_episodes])
        match = refs_match(expected, refs) if refs else None
        if match is True:
            identity_key = 2
            labels["identity"] = "afleveringnummer klopt"
        elif match is False and parsed.confidence == "high" and not is_anime:
            identity_key = 0
            labels["identity"] = "verkeerde aflevering in releasenaam"
            rejections.append(Rejection(
                "IDENTITY_MISMATCH",
                "Releasenaam noemt " + ", ".join(r.label() for r in refs)
                + ", verwacht " + ", ".join(e.label() for e in expected)))
        else:
            labels["identity"] = "nummering niet eenduidig (geen afwijzing)"

    # ── audio ─────────────────────────────────────────────────────────
    audio_cfg = cfg.get("audio") or {}
    policy = AudioPolicy.from_dict(audio_cfg)
    langs = normalize_list(cand.languages)
    for code in parsed.audio_language_claims:
        if code not in langs:
            langs.append(code)
    langs = [x for x in langs if x != "und"]
    verdict = evaluate_languages(langs, parsed.subtitle_language_claims, policy,
                                 claimed_only=True)
    if parsed.dual_audio and false_claim_titles and _title_norm(cand.title) in false_claim_titles:
        verdict = evaluate_languages([x for x in langs if x != "en"],
                                     parsed.subtitle_language_claims, policy, claimed_only=True)
        if cfg.get("reject_false_audio_claims", True):
            rejections.append(Rejection(
                "FALSE_DUAL_AUDIO_CLAIM",
                "Deze release claimde eerder 'Dual Audio' maar bevatte alleen Japans"))
    if not verdict.acceptable and policy.nl_audio == "required":
        rejections.append(Rejection("AUDIO_REQUIREMENT",
                                    "Nederlandse audio is verplicht in dit profiel"))
    labels["audio"] = verdict.tier_label_nl + (" (claim uit naam, niet geverifieerd)"
                                               if parsed.dual_audio else "")

    # ── bron / resolutie / hdr ────────────────────────────────────────
    source = cand.source or parsed.source
    if source in ("web", "web-dl"):
        source = "webdl"
    labels["source"] = SOURCE_LABEL.get(source or "", "onbekend")
    resolution = cand.resolution or parsed.resolution or 0
    labels["resolution"] = f"{resolution}p" if resolution else "onbekend"
    if cfg.get("max_resolution") and resolution and resolution > cfg["max_resolution"]:
        rejections.append(Rejection("RESOLUTION_ABOVE_MAX",
                                    f"{resolution}p boven maximum {cfg['max_resolution']}p"))
    if cfg.get("min_resolution") and resolution and resolution < cfg["min_resolution"]:
        rejections.append(Rejection("RESOLUTION_BELOW_MIN",
                                    f"{resolution}p onder minimum {cfg['min_resolution']}p"))
    hdr_rank = 0
    if "DV" in parsed.hdr:
        hdr_rank = 3
    elif "HDR10+" in parsed.hdr or "HDR10" in parsed.hdr:
        hdr_rank = 2
    elif "HLG" in parsed.hdr:
        hdr_rank = 1
    labels["hdr"] = "/".join(parsed.hdr) or "SDR"

    # ── grootte / groep / custom formats ──────────────────────────────
    size_gb = (cand.size or 0) / 1024**3
    labels["size"] = f"{size_gb:.1f} GB" if cand.size else "onbekend"
    if cfg.get("max_size_gb") and size_gb > float(cfg["max_size_gb"]):
        rejections.append(Rejection("SIZE_ABOVE_MAX",
                                    f"{size_gb:.1f} GB boven maximum {cfg['max_size_gb']} GB"))
    group = cand.release_group or parsed.release_group
    if _norm_group(group) in {_norm_group(g) for g in cfg.get("blocked_groups") or []}:
        rejections.append(Rejection("BLOCKED_GROUP", f"Releasegroep '{group}' is geblokkeerd"))
    preferred = {_norm_group(g) for g in cfg.get("preferred_groups") or []}
    group_rank = 1 if _norm_group(group) in preferred and group else 0
    labels["group"] = group or "onbekend"
    if formats:
        cf_score, cf_names = score(formats, cand.title, parsed, langs,
                                   cfg.get("trash_score_set") or "default")
    else:
        cf_score, cf_names = int(cand.custom_format_score or 0), list(cand.custom_formats)
    labels["custom_format"] = str(cf_score)
    min_cf = cfg.get("min_custom_format_score")
    if min_cf is not None and cf_score < min_cf:
        rejections.append(Rejection("CUSTOM_FORMAT_SCORE",
                                    f"Score {cf_score} onder minimum {min_cf}"))

    keys: dict[str, Any] = {
        "identity": identity_key,
        "audio": verdict.rank,
        "source": SOURCE_RANK.get(source or "", 0),
        "resolution": resolution,
        "hdr": hdr_rank if cfg.get("prefer_hdr") else 0,
        "custom_format": cf_score,
        "group": group_rank,
        "size": -(cand.size or 0) if cfg.get("prefer_smaller") else 0,
    }
    return Ranked(candidate=cand, parsed=parsed, keys=keys, labels=labels,
                  audio=verdict.to_dict(), rejections=rejections, order=order,
                  cf_score=cf_score, cf_names=cf_names)


def _title_norm(title: str) -> str:
    return " ".join(title.lower().replace(".", " ").replace("_", " ").split())


def rank(candidates: list[Candidate], cfg: dict[str, Any], *,
         expected: list[EpisodeRef] | None = None, formats: list[CustomFormat] | None = None,
         false_claim_titles: set[str] | None = None, is_anime: bool = False) -> list[Ranked]:
    ranked = [evaluate_candidate(c, cfg, expected=expected, formats=formats,
                                 false_claim_titles=false_claim_titles, is_anime=is_anime)
              for c in candidates]
    accepted = sorted((r for r in ranked if not r.rejected),
                      key=lambda r: (r.sort_key(), r.candidate.title), reverse=True)
    rejected = sorted((r for r in ranked if r.rejected), key=lambda r: r.candidate.title)
    out = accepted + rejected
    for i, r in enumerate(out, start=1):
        r.position = i
    if accepted:
        winner = accepted[0]
        for other in accepted[1:]:
            other.explanation_nl = explain(winner, other, prefix_winner=False)
        winner.explanation_nl = (explain(winner, accepted[1]) if len(accepted) > 1
                                 else "Enige release die aan alle harde regels voldoet.")
    for r in rejected:
        r.explanation_nl = "Afgewezen: " + "; ".join(x.message_nl for x in r.rejections)
    return out


def explain(a: Ranked, b: Ranked, *, prefix_winner: bool = True) -> str:
    """'Waarom deze release?' — eerste sleutel in de profielvolgorde die verschilt."""
    for key in ["identity", *a.order]:
        if a.keys[key] != b.keys[key]:
            label = KEY_LABEL_NL[key]
            later = [KEY_LABEL_NL[k] for k in a.order[a.order.index(key) + 1:]] \
                if key in a.order else []
            tail = (f" Dit profiel laat {label} zwaarder wegen dan "
                    + ", ".join(later) + ".") if later else ""
            if prefix_winner:
                return (f"Gekozen boven '{b.candidate.title}' op {label}: "
                        f"{a.labels.get(key, '')} tegenover {b.labels.get(key, '')}.{tail}")
            return (f"Lager dan '{a.candidate.title}' op {label}: "
                    f"{b.labels.get(key, '')} tegenover {a.labels.get(key, '')}.")
    return "Gelijkwaardig aan de nummer één; volgorde op titel (deterministisch)."
