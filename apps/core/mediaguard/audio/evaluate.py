"""Audio-/ondertitelbeleid evalueren (E2/P6) en releaseclaims valideren.

Anime (default): Dual Audio (EN+JA) > Engels > Japans. Japans-alleen is een
*acceptabele fallback*: wel importeren, maar blijven zoeken naar een upgrade.
Gewone content: originele audio behouden, Nederlandse ondertitels gewenst; NL-audio per
profiel ``off``/``preferred``/``required`` (kinderprofiel: required).
"""

from __future__ import annotations

from dataclasses import asdict, dataclass, field
from typing import Any, Literal

from mediaguard.audio.inventory import StreamInventory
from mediaguard.audio.languages import UNDEFINED, name_nl

AudioMode = Literal["anime", "original"]
AnimePref = Literal["dual_first", "english_first", "japanese_first"]
NlAudio = Literal["off", "preferred", "required"]


@dataclass
class AudioPolicy:
    mode: AudioMode = "original"
    anime_preference: AnimePref = "dual_first"
    nl_audio: NlAudio = "off"
    nl_subtitles_wanted: bool = True
    subtitle_preference: list[str] = field(default_factory=lambda: ["nl", "en"])

    @classmethod
    def from_dict(cls, d: dict[str, Any]) -> AudioPolicy:
        return cls(mode=d.get("mode", "original"),
                   anime_preference=d.get("anime_preference", "dual_first"),
                   nl_audio=d.get("nl_audio", "off"),
                   nl_subtitles_wanted=bool(d.get("nl_subtitles_wanted", True)),
                   subtitle_preference=list(d.get("subtitle_preference") or ["nl", "en"]))


@dataclass
class AudioVerdict:
    tier: str                    # dual | english | japanese | dutch | original | other | unknown
    tier_label_nl: str
    rank: tuple[int, ...]        # lexicografisch: hoger = beter
    acceptable: bool             # harde eis voldaan
    meets_preference: bool       # voorkeur volledig gehaald
    keep_searching: bool         # fallback geaccepteerd maar upgrade blijven zoeken
    languages: list[str]
    subtitle_languages: list[str]
    reasons: list[str] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        d = asdict(self)
        d["rank"] = list(self.rank)
        return d


def _anime_order(pref: AnimePref) -> dict[str, int]:
    if pref == "japanese_first":  # Japans aanwezig is genoeg; Engels-alleen is fallback
        return {"dual": 3, "japanese": 3, "english": 1}
    if pref == "english_first":
        return {"english": 3, "dual": 2, "japanese": 1}
    return {"dual": 3, "english": 2, "japanese": 1}


TIER_LABELS = {"dual": "Dual Audio (EN+JA)", "english": "Alleen Engels",
               "japanese": "Alleen Japans", "dutch": "Nederlands gesproken",
               "original": "Originele audio", "other": "Andere taal", "unknown": "Onbekend"}


def evaluate_languages(audio_langs: list[str], subtitle_langs: list[str],
                       policy: AudioPolicy, *, claimed_only: bool = False) -> AudioVerdict:
    langs = [lang for lang in audio_langs if lang != UNDEFINED]
    reasons: list[str] = []
    has_nl = "nl" in langs
    subs_rank = 0
    for i, lang in enumerate(reversed(policy.subtitle_preference)):
        if lang in subtitle_langs:
            subs_rank = max(subs_rank, i + 1)
    nl_subs = "nl" in subtitle_langs
    unknown = not langs

    if policy.mode == "anime":
        if "en" in langs and "ja" in langs:
            tier = "dual"
        elif "en" in langs:
            tier = "english"
        elif "ja" in langs:
            tier = "japanese"
        else:
            tier = "unknown" if unknown else "other"
        order = _anime_order(policy.anime_preference)
        tier_rank = order.get(tier, 0)
        meets = tier_rank == max(order.values())
        acceptable = tier in order or tier == "unknown"
        keep = not meets
        if tier == "japanese" and policy.anime_preference != "japanese_first":
            reasons.append("Alleen Japans: geaccepteerd als fallback, blijft zoeken naar "
                           "Dual Audio/Engels")
        if tier == "unknown":
            reasons.append("Audiotalen onbekend; controle na download")
    else:
        tier = "dutch" if has_nl else ("unknown" if unknown else "original")
        tier_rank = 1 if not unknown else 0
        acceptable = True
        meets = True
        keep = False

    nl_rank = 1 if has_nl else 0
    if policy.nl_audio == "required" and not has_nl:
        reasons.append("Nederlandse audio vereist; taal onbekend in releasenaam"
                       if unknown and claimed_only else
                       "Nederlandse audio is vereist in dit profiel")
        acceptable = False
        meets = False
    elif policy.nl_audio == "preferred" and not has_nl:
        meets = False
        keep = True
        reasons.append("Nederlandse audio heeft de voorkeur; blijft zoeken")
    if policy.nl_subtitles_wanted and not nl_subs and policy.mode == "original":
        reasons.append("Nederlandse ondertitels gewenst maar niet aanwezig")
        meets = False

    rank: tuple[int, ...]
    if policy.nl_audio == "off":
        rank = (int(acceptable), tier_rank, subs_rank)
    else:
        rank = (int(acceptable), nl_rank, tier_rank, subs_rank)
    label = TIER_LABELS.get(tier, tier)
    if tier == "original" and langs:
        label = "Origineel (" + ", ".join(name_nl(x) for x in langs) + ")"
    return AudioVerdict(tier=tier, tier_label_nl=label, rank=rank, acceptable=acceptable,
                        meets_preference=meets, keep_searching=keep and acceptable,
                        languages=langs, subtitle_languages=list(subtitle_langs),
                        reasons=reasons)


def evaluate_inventory(inv: StreamInventory, policy: AudioPolicy) -> AudioVerdict:
    """Commentaar- en audiodescriptiesporen tellen nooit als dub."""
    verdict = evaluate_languages(inv.main_audio_languages, inv.full_subtitle_languages, policy)
    ignored = [a for a in inv.audio if not a.main and a.language != UNDEFINED]
    for a in ignored:
        kind = "commentaarspoor" if a.commentary else "audiodescriptie"
        verdict.reasons.append(f"{name_nl(a.language)} {kind} telt niet als gesproken audio")
    if not inv.audio:
        verdict.acceptable = False
        verdict.reasons.append("Geen audiospoor gevonden")
    return verdict


@dataclass
class ClaimCheck:
    claim: str
    valid: bool | None
    reason_nl: str

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


def validate_claims(*, dual_audio: bool, claimed_audio: list[str],
                    inv: StreamInventory) -> list[ClaimCheck]:
    """Vergelijk de claims uit de releasenaam met de feitelijke (hoofd)audiosporen."""
    main = inv.known_main_audio_languages
    unknown = not main
    checks: list[ClaimCheck] = []
    if dual_audio:
        if unknown:
            checks.append(ClaimCheck("dual_audio", None,
                                     "'Dual Audio' niet te controleren: taallabels ontbreken"))
        elif "en" in main and "ja" in main:
            checks.append(ClaimCheck("dual_audio", True, "Dual Audio bevestigd (EN + JA)"))
        else:
            has = ", ".join(name_nl(x) for x in main)
            extra = ""
            if any(a.language == "en" and not a.main for a in inv.audio):
                extra = " (Engels spoor is commentaar/audiodescriptie, geen dub)"
            checks.append(ClaimCheck("dual_audio", False,
                                     f"Onjuiste claim 'Dual Audio': alleen {has}{extra}"))
    for lang in claimed_audio:
        if dual_audio and lang in ("en", "ja"):
            continue
        if unknown:
            checks.append(ClaimCheck(f"audio:{lang}", None,
                                     f"{name_nl(lang)} audio niet te controleren"))
        else:
            ok = lang in main
            checks.append(ClaimCheck(
                f"audio:{lang}", ok,
                f"{name_nl(lang)} audio {'bevestigd' if ok else 'ontbreekt ondanks claim'}"))
    return checks
