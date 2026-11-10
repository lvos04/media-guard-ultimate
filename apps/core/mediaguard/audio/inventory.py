"""Stream-inventaris uit ffprobe-JSON, het probe-formaat van de AV1-engine of Sonarr/Radarr
``mediaInfo``. Onderscheidt hoofdprogramma, commentaar, audiodescriptie, forced,
signs & songs en SDH, zodat bv. een Engels commentaarspoor nooit als Engelse dub telt.
"""

from __future__ import annotations

import re
from dataclasses import asdict, dataclass, field
from typing import Any

from mediaguard.audio.languages import UNDEFINED, normalize, normalize_list

RE_COMMENTARY = re.compile(r"comment|commentaar|kommentar|director'?s? ?track|regisseur",
                           re.I)
RE_AUDIO_DESC = re.compile(
    r"audio ?descri|descriptive|audiodescriptie|beschrijving|\bAD\b|visually ?impaired", re.I)
RE_SIGNS = re.compile(r"sign|song|s&s|\btitles?\b only", re.I)
RE_FORCED = re.compile(r"\bforced\b|geforceerd", re.I)
RE_SDH = re.compile(r"\b(SDH|CC|HI)\b|hearing|doven|slechthorend", re.I)
TEXT_SUB_CODECS = {"subrip", "srt", "ass", "ssa", "webvtt", "mov_text", "text"}


@dataclass
class AudioTrack:
    index: int
    language: str
    codec: str = ""
    channels: int = 0
    title: str = ""
    default: bool = False
    commentary: bool = False
    audio_description: bool = False
    original: bool = False

    @property
    def main(self) -> bool:
        """Hoofdprogramma: geen commentaar of audiodescriptie."""
        return not (self.commentary or self.audio_description)


@dataclass
class SubtitleTrack:
    index: int
    language: str
    codec: str = ""
    title: str = ""
    default: bool = False
    forced: bool = False
    signs_songs: bool = False
    sdh: bool = False
    commentary: bool = False

    @property
    def full(self) -> bool:
        """Volledige ondertitels (geen forced/signs-only of commentaar)."""
        return not (self.forced or self.signs_songs or self.commentary)

    @property
    def text_based(self) -> bool:
        return self.codec in TEXT_SUB_CODECS


@dataclass
class StreamInventory:
    audio: list[AudioTrack] = field(default_factory=list)
    subtitles: list[SubtitleTrack] = field(default_factory=list)
    source: str = "unknown"

    @property
    def main_audio_languages(self) -> list[str]:
        out: list[str] = []
        for a in self.audio:
            if a.main and a.language not in out:
                out.append(a.language)
        return out

    @property
    def known_main_audio_languages(self) -> list[str]:
        return [lang for lang in self.main_audio_languages if lang != UNDEFINED]

    @property
    def full_subtitle_languages(self) -> list[str]:
        out: list[str] = []
        for s in self.subtitles:
            if s.full and s.language not in out:
                out.append(s.language)
        return out

    def to_dict(self) -> dict[str, Any]:
        return {
            "source": self.source,
            "audio": [asdict(a) | {"main": a.main} for a in self.audio],
            "subtitles": [asdict(s) | {"full": s.full} for s in self.subtitles],
            "main_audio_languages": self.main_audio_languages,
            "full_subtitle_languages": self.full_subtitle_languages,
        }


def _disp(stream: dict[str, Any], key: str) -> bool:
    return bool((stream.get("disposition") or {}).get(key))


def from_ffprobe(data: dict[str, Any]) -> StreamInventory:
    inv = StreamInventory(source="ffprobe")
    for s in data.get("streams") or []:
        tags = {str(k).lower(): v for k, v in (s.get("tags") or {}).items()}
        title = str(tags.get("title") or "")
        lang = normalize(tags.get("language"))
        if s.get("codec_type") == "audio":
            inv.audio.append(AudioTrack(
                index=int(s.get("index", len(inv.audio))), language=lang,
                codec=str(s.get("codec_name") or ""), channels=int(s.get("channels") or 0),
                title=title, default=_disp(s, "default"),
                commentary=_disp(s, "comment") or bool(RE_COMMENTARY.search(title)),
                audio_description=_disp(s, "visual_impaired") or _disp(s, "descriptions")
                or bool(RE_AUDIO_DESC.search(title)),
                original=_disp(s, "original")))
        elif s.get("codec_type") == "subtitle":
            inv.subtitles.append(_sub(int(s.get("index", 0)), lang,
                                      str(s.get("codec_name") or "").lower(), title,
                                      default=_disp(s, "default"), forced=_disp(s, "forced"),
                                      sdh=_disp(s, "hearing_impaired"),
                                      commentary=_disp(s, "comment")))
    return inv


def _sub(index: int, lang: str, codec: str, title: str, *, default: bool = False,
         forced: bool = False, sdh: bool = False, commentary: bool = False) -> SubtitleTrack:
    return SubtitleTrack(
        index=index, language=lang, codec=codec, title=title, default=default,
        forced=forced or bool(RE_FORCED.search(title)),
        signs_songs=bool(RE_SIGNS.search(title)), sdh=sdh or bool(RE_SDH.search(title)),
        commentary=commentary or bool(RE_COMMENTARY.search(title)))


def from_encoder_probe(info: dict[str, Any]) -> StreamInventory:
    """Formaat van ``services/encoder/app/media/probe.py`` (audio[]/subs[])."""
    inv = StreamInventory(source="encoder_probe")
    for a in info.get("audio") or []:
        title = str(a.get("titel") or a.get("title") or "")
        inv.audio.append(AudioTrack(
            index=int(a.get("index", 0)), language=normalize(a.get("taal") or a.get("language")),
            codec=str(a.get("codec") or ""), channels=int(a.get("kanalen") or 0), title=title,
            default=bool(a.get("default")),
            commentary=bool(a.get("commentaar")) or bool(RE_COMMENTARY.search(title)),
            audio_description=bool(a.get("audio_description"))
            or bool(RE_AUDIO_DESC.search(title))))
    for s in info.get("subs") or []:
        inv.subtitles.append(_sub(int(s.get("index", 0)), normalize(s.get("taal")),
                                  str(s.get("codec") or "").lower(), str(s.get("titel") or ""),
                                  forced=bool(s.get("forced")),
                                  commentary=bool(s.get("commentaar"))))
    return inv


def from_arr_media_info(media_info: dict[str, Any]) -> StreamInventory:
    """Sonarr/Radarr ``mediaInfo`` (``audioLanguages: 'eng/jpn'``) of hook-``media_info``.
    Grover: geen titels/flags, dus commentaar is hier niet te onderscheiden."""
    inv = StreamInventory(source="arr_media_info")
    audio = media_info.get("audio_languages", media_info.get("audioLanguages"))
    subs = media_info.get("subtitles", media_info.get("subtitleLanguages"))
    for i, lang in enumerate(normalize_list(audio)):
        inv.audio.append(AudioTrack(index=i, language=lang))
    for i, lang in enumerate(normalize_list(subs)):
        inv.subtitles.append(SubtitleTrack(index=i, language=lang))
    return inv


def build(data: dict[str, Any]) -> StreamInventory:
    if "streams" in data:
        return from_ffprobe(data)
    if "audio" in data or "subs" in data:
        return from_encoder_probe(data)
    return from_arr_media_info(data)
