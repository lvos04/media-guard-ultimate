"""Deterministische parser voor bestands- en releasenamen.

Herkent scene-namen (``Show.S01E03.1080p.WEB-DL-GRP``), multi-episodes (``S01E01E02``,
``S01E01-E03``, ``1x03-04``), anime absolute nummering (``[Group] Show - 1071 [1080p]``,
``Show - 05v2``), specials/OVA/ONA, dagelijkse uitzendingen, releasegroep, resolutie,
bron, codec, HDR/DV, dual-audio/multi-claims, Nederlandse taal-/ondertiteltags,
edities en CRC32-hashes.

Bestandsnamen zijn onbetrouwbare invoer: de parser voert nooit iets uit, begrenst de
invoerlengte en gebruikt alleen lineaire regexen.
"""

from __future__ import annotations

import re
from dataclasses import asdict, dataclass, field
from typing import Any

MAX_INPUT = 400
VIDEO_EXTENSIONS = {".mkv", ".mp4", ".avi", ".m4v", ".ts", ".m2ts", ".wmv", ".mov", ".webm",
                    ".mpg", ".mpeg", ".ogm", ".flv"}


@dataclass
class ParsedRelease:
    raw: str
    title: str | None = None
    year: int | None = None
    season: int | None = None
    episodes: list[int] = field(default_factory=list)
    absolute_episodes: list[int] = field(default_factory=list)
    full_season: bool = False
    special: bool = False
    special_kind: str | None = None
    air_date: str | None = None
    release_group: str | None = None
    resolution: int | None = None
    source: str | None = None
    video_codec: str | None = None
    hdr: list[str] = field(default_factory=list)
    dual_audio: bool = False
    multi_audio: bool = False
    dubbed: bool = False
    # taalclaims uit de naam: {"nl": "audio"|"subs"|"ambiguous", ...} (ISO 639-1)
    language_claims: dict[str, str] = field(default_factory=dict)
    crc32: str | None = None
    version: int | None = None
    proper: bool = False
    repack: bool = False
    edition: str | None = None
    # high = SxxEyy/datum, medium = 1x03/absolute met scheiding, low = los getal
    confidence: str = "none"

    @property
    def audio_language_claims(self) -> list[str]:
        """Talen die de naam claimt als gesproken audio (dual audio = en + ja)."""
        langs = [k for k, v in self.language_claims.items() if v in ("audio", "ambiguous")]
        if self.dual_audio:
            for code in ("en", "ja"):
                if code not in langs:
                    langs.append(code)
        return langs

    @property
    def subtitle_language_claims(self) -> list[str]:
        return [k for k, v in self.language_claims.items() if v == "subs"]

    def to_dict(self) -> dict[str, Any]:
        data = asdict(self)
        data["audio_language_claims"] = self.audio_language_claims
        data["subtitle_language_claims"] = self.subtitle_language_claims
        return data


_SEP = r"[ ._\-]"
RE_LEADING_GROUP = re.compile(r"^\[([^\]]{1,60})\]")
RE_TRAILING_GROUP = re.compile(r"-([A-Za-z0-9][A-Za-z0-9_.]{0,30}?)(?:\[[^\]]*\])?$")
RE_CRC = re.compile(r"[\[(]([0-9A-Fa-f]{8})[\])]")
RE_SXXEYY = re.compile(
    r"(?<![A-Za-z0-9])S(\d{1,4})" + _SEP + r"?E(\d{1,4})((?:(?:" + _SEP
    + r"?-?" + _SEP + r"?E|-)\d{1,4})*)(?![0-9])", re.I)
RE_EP_TAIL = re.compile(r"(-?)" + _SEP + r"?(E?)(\d{1,4})", re.I)
RE_NXM = re.compile(r"(?<![0-9])(\d{1,2})x(\d{2,3})(?:-(?:\d{1,2}x)?(\d{2,3}))?(?![0-9])", re.I)
RE_SEASON_WORD = re.compile(r"\bSeason" + _SEP + r"?(\d{1,2})\b", re.I)
RE_SEASON_ONLY = re.compile(r"(?<![A-Za-z0-9])S(\d{1,2})(?![0-9E])(?:" + _SEP + r"|$)", re.I)
RE_ANIME_SEASON_EP = re.compile(r"\bS(\d{1,2})" + _SEP + r"+-" + _SEP + r"+(\d{1,4})(?:v(\d))?\b",
                                re.I)
RE_DAILY = re.compile(r"(?<![0-9])((?:19|20)\d{2})[.\-_ ](\d{2})[.\-_ ](\d{2})(?![0-9])")
RE_ABS_DASH = re.compile(
    r"\s-\s(\d{1,4})(?:-(\d{1,4}))?(?:v(\d))?(?=\s|$|\[|\(|\.)")
RE_EPISODE_WORD = re.compile(r"\b(?:Episode|Ep|Aflevering|Afl)" + _SEP + r"?(\d{1,4})(?:v(\d))?\b",
                             re.I)
RE_SPECIAL = re.compile(r"\b(OVA|OAV|ONA|OAD|SP|Specials?)\b(?:" + _SEP + r"?(\d{1,3}))?", re.I)
RE_YEAR = re.compile(r"(?<![0-9])((?:19|20)\d{2})(?![0-9])")
RE_RESOLUTION = re.compile(r"(?<![0-9])(4320|2160|1440|1080|720|576|540|480|360)[pi](?![a-z])",
                           re.I)
RE_4K = re.compile(r"\b(4K|UHD)\b", re.I)
RE_VERSION = re.compile(r"(?<=\d)v(\d)\b", re.I)

SOURCES: list[tuple[str, re.Pattern[str]]] = [
    ("remux", re.compile(r"\bREMUX\b", re.I)),
    ("bluray", re.compile(r"\b(Blu-?Ray|BDRip|BRRip|BD|BDMV|BDREMUX)\b", re.I)),
    ("webrip", re.compile(r"\bWEB-?Rip\b", re.I)),
    ("webdl", re.compile(r"\b(WEB-?DL|WEB|AMZN|NF|DSNP|HMAX|ATVP|iT)\b", re.I)),
    ("hdtv", re.compile(r"\b(HDTV|PDTV)\b", re.I)),
    ("dvd", re.compile(r"\b(DVD|DVDRip|DVD5|DVD9|NTSC|PAL)\b", re.I)),
    ("sdtv", re.compile(r"\b(SDTV|TVRip)\b", re.I)),
]
CODECS: list[tuple[str, re.Pattern[str]]] = [
    ("av1", re.compile(r"\bAV1\b", re.I)),
    ("hevc", re.compile(r"\b(x265|h\.?265|HEVC)\b", re.I)),
    ("h264", re.compile(r"\b(x264|h\.?264|AVC)\b", re.I)),
    ("vp9", re.compile(r"\bVP9\b", re.I)),
    ("xvid", re.compile(r"\b(XviD|DivX)\b", re.I)),
]
RE_DV = re.compile(r"\b(DV|DoVi|Dolby" + _SEP + r"?Vision)\b", re.I)
RE_HDR10P = re.compile(r"\bHDR10(\+|Plus|P)(?![a-z])", re.I)
RE_HDR10 = re.compile(r"\bHDR(10)?\b(?!\+)", re.I)
RE_HLG = re.compile(r"\bHLG\b", re.I)
RE_DUAL = re.compile(r"\bDual" + _SEP + r"?(Audio|Lang)?\b", re.I)
RE_MULTI = re.compile(r"\bMulti(?:" + _SEP + r"?(?:Audio|Lang))?\b(?!" + _SEP + r"?Subs?)", re.I)
RE_DUBBED = re.compile(r"\b(Dubbed|(?:English|Eng)" + _SEP + r"?Dub|Dub)\b", re.I)
RE_PROPER = re.compile(r"\bPROPER\b", re.I)
RE_REPACK = re.compile(r"\b(REPACK|RERIP)\b", re.I)
RE_EDITION = re.compile(
    r"\b(Director'?s" + _SEP + r"?Cut|Extended(?:" + _SEP + r"?(?:Cut|Edition))?|Uncut|"
    r"Unrated|Theatrical|Remastered|IMAX|Final" + _SEP + r"?Cut|Special" + _SEP
    + r"?Edition)\b", re.I)

# Taaltags (Nederlands + veelvoorkomend). Volgorde: specifiek → algemeen.
LANG_SUB_PATTERNS: list[tuple[str, re.Pattern[str]]] = [
    ("nl", re.compile(r"\b(NL|Dutch|NLD|Ned)" + _SEP + r"?Subs?\b|\bSubs?" + _SEP
                      + r"?(NL|Dutch)\b|\bNLSubs?\b|\bNL-?Sub\b", re.I)),
    ("en", re.compile(r"\b(Eng|English|EN)" + _SEP + r"?Subs?\b|\bEngSubs?\b", re.I)),
]
LANG_AUDIO_PATTERNS: list[tuple[str, re.Pattern[str]]] = [
    ("nl", re.compile(r"\b(NL|Dutch)" + _SEP + r"?(Dub|Audio|Gesproken)\b|\bNLDub\b|"
                      r"\b(Nederlands" + _SEP + r"?Gesproken|Vlaams|Flemish)\b", re.I)),
    ("en", re.compile(r"\b(English|Eng)" + _SEP + r"?(Audio|Dub)\b", re.I)),
    ("ja", re.compile(r"\b(Japanese|JPN|Jap)" + _SEP + r"?(Audio)?\b", re.I)),
]
LANG_AMBIGUOUS: list[tuple[str, re.Pattern[str]]] = [
    ("nl", re.compile(r"(?<![A-Za-z])(NL|Dutch|NLD)(?![A-Za-z])", re.I)),
    ("en", re.compile(r"\b(English|ENG)\b", re.I)),
]

# woorden die nooit als releasegroep gelden (scene-suffixen)
NOT_GROUPS = {"dl", "rip", "web", "hd", "sd", "x264", "x265", "h264", "h265", "hevc", "av1",
              "audio", "dual", "sub", "subs", "mkv", "mp4", "nl", "en"}


def _strip(name: str) -> str:
    name = name.replace("\\", "/").rsplit("/", 1)[-1][:MAX_INPUT]
    dot = name.rfind(".")
    if dot > 0 and name[dot:].lower() in VIDEO_EXTENSIONS:
        name = name[:dot]
    return name.strip()


def _expand(first: int, tail: str) -> list[int]:
    episodes = [first]
    for dash, _e, num in RE_EP_TAIL.findall(tail):
        n = int(num)
        if dash and n > episodes[-1] and n - episodes[-1] <= 50:
            episodes.extend(range(episodes[-1] + 1, n + 1))
        elif n not in episodes:
            episodes.append(n)
    return episodes


def _clean_title(text: str) -> str | None:
    text = re.sub(r"\[[^\]]*\]|\([^)]*\)", " ", text)
    text = re.sub(r"[._]", " ", text)
    text = re.sub(r"\s+-\s*$", "", text.strip())
    text = re.sub(r"\s+", " ", text).strip(" -")
    return text or None


def parse_release(name: str) -> ParsedRelease:  # noqa: C901 - lineaire parse-stappen
    raw = name
    work = _strip(name)
    p = ParsedRelease(raw=raw[:MAX_INPUT])
    if not work:
        return p

    m = RE_CRC.search(work)
    if m:
        p.crc32 = m.group(1).upper()

    lead = RE_LEADING_GROUP.match(work)
    body = work
    if lead:
        p.release_group = lead.group(1).strip()
        body = work[lead.end():].strip()
    else:
        tail = RE_TRAILING_GROUP.search(work)
        if tail and tail.group(1).lower() not in NOT_GROUPS and not tail.group(1).isdigit():
            candidate = tail.group(1).strip(".")
            # 'WEB-DL' e.d. niet als groep aanzien
            if not re.fullmatch(r"(DL|Rip|HD|Audio)", candidate, re.I):
                p.release_group = candidate

    title_end = len(body)

    # ── episode-nummering ─────────────────────────────────────────────
    m = RE_SXXEYY.search(body)
    if m:
        p.season = int(m.group(1))
        p.episodes = _expand(int(m.group(2)), m.group(3) or "")
        p.confidence = "high"
        title_end = min(title_end, m.start())
    if not p.episodes:
        m = RE_NXM.search(body)
        if m:
            p.season = int(m.group(1))
            first = int(m.group(2))
            p.episodes = [first]
            if m.group(3):
                last = int(m.group(3))
                if last > first and last - first <= 50:
                    p.episodes = list(range(first, last + 1))
            p.confidence = "medium"
            title_end = min(title_end, m.start())
    if not p.episodes:
        m = RE_ANIME_SEASON_EP.search(body)
        if m:
            p.season = int(m.group(1))
            p.episodes = [int(m.group(2))]
            if m.group(3):
                p.version = int(m.group(3))
            p.confidence = "medium"
            title_end = min(title_end, m.start())
    if not p.episodes:
        m = RE_DAILY.search(body)
        if m and not RE_SEASON_WORD.search(body):
            p.air_date = f"{m.group(1)}-{m.group(2)}-{m.group(3)}"
            p.confidence = "high"
            title_end = min(title_end, m.start())

    sm = RE_SPECIAL.search(body)
    if sm and not (sm.group(1).upper() == "SP" and p.episodes):
        p.special = True
        kind = sm.group(1).upper()
        p.special_kind = "Special" if kind.startswith("SPECIAL") else (
            "OVA" if kind == "OAV" else kind)
        if not p.episodes and sm.group(2):
            p.season = 0
            p.episodes = [int(sm.group(2))]
            p.confidence = "medium"
        elif not p.episodes:
            p.season = 0
        title_end = min(title_end, sm.start())
    if p.season == 0 and p.episodes:
        p.special = True
        p.special_kind = p.special_kind or "Special"

    if not p.episodes and not p.air_date and not p.special:
        m = RE_ABS_DASH.search(body)
        if m and not RE_YEAR.fullmatch(m.group(1)):
            first = int(m.group(1))
            p.absolute_episodes = [first]
            if m.group(2):
                last = int(m.group(2))
                if last > first and last - first <= 50:
                    p.absolute_episodes = list(range(first, last + 1))
            if m.group(3):
                p.version = int(m.group(3))
            p.confidence = "medium"
            title_end = min(title_end, m.start())
        else:
            m = RE_EPISODE_WORD.search(body)
            if m:
                p.absolute_episodes = [int(m.group(1))]
                if m.group(2):
                    p.version = int(m.group(2))
                p.confidence = "low"
                title_end = min(title_end, m.start())
    if not p.episodes and not p.absolute_episodes and not p.air_date:
        m = RE_SEASON_WORD.search(body) or RE_SEASON_ONLY.search(body)
        if m:
            p.season = int(m.group(1))
            p.full_season = True
            p.confidence = "medium"
            title_end = min(title_end, m.start())

    if p.version is None:
        vm = RE_VERSION.search(body)
        if vm:
            p.version = int(vm.group(1))

    # ── jaar / titel ──────────────────────────────────────────────────
    for ym in RE_YEAR.finditer(body):
        if p.air_date and ym.start() >= title_end:
            break
        # een jaartal aan het begin is waarschijnlijk deel van de titel ('1917', '2012')
        if ym.start() == 0:
            continue
        p.year = int(ym.group(1))
        if not p.episodes and not p.absolute_episodes and not p.air_date:
            title_end = min(title_end, ym.start())
        break
    rm = RE_RESOLUTION.search(body)
    if rm:
        p.resolution = int(rm.group(1))
        title_end = min(title_end, rm.start())
    elif RE_4K.search(body):
        p.resolution = 2160
    p.title = _clean_title(body[:title_end])

    # ── technische tags ───────────────────────────────────────────────
    for name_, pattern in SOURCES:
        if pattern.search(body):
            p.source = name_
            break
    if p.source == "bluray" and re.search(r"\bREMUX\b", body, re.I):
        p.source = "remux"
    for name_, pattern in CODECS:
        if pattern.search(body):
            p.video_codec = name_
            break
    if RE_DV.search(body):
        p.hdr.append("DV")
    if RE_HDR10P.search(body):
        p.hdr.append("HDR10+")
    elif RE_HDR10.search(body):
        p.hdr.append("HDR10")
    if RE_HLG.search(body):
        p.hdr.append("HLG")

    # ── audio-/taalclaims ─────────────────────────────────────────────
    p.dual_audio = bool(RE_DUAL.search(body))
    p.multi_audio = bool(RE_MULTI.search(body))
    p.dubbed = bool(RE_DUBBED.search(body))
    sub_spans: list[tuple[int, int]] = []
    for code, pattern in LANG_SUB_PATTERNS:
        for sm2 in pattern.finditer(body):
            p.language_claims.setdefault(code, "subs")
            sub_spans.append(sm2.span())
    for code, pattern in LANG_AUDIO_PATTERNS:
        for am in pattern.finditer(body):
            if any(a <= am.start() < b for a, b in sub_spans):
                continue
            p.language_claims[code] = "audio"
    for code, pattern in LANG_AMBIGUOUS:
        for am in pattern.finditer(body):
            if any(a <= am.start() < b for a, b in sub_spans):
                continue
            if am.start() < title_end and code == "en":
                continue
            p.language_claims.setdefault(code, "ambiguous")
    if p.dubbed and "en" not in p.language_claims and "nl" not in p.language_claims:
        p.language_claims["en"] = "audio"

    p.proper = bool(RE_PROPER.search(body))
    p.repack = bool(RE_REPACK.search(body))
    em = RE_EDITION.search(body)
    if em:
        p.edition = re.sub(r"[._]", " ", em.group(1))
    return p
