"""Audio-/ondertitelinventaris (P6): eerst inventariseren, dan pas beleid.

Leest dispositions die ``probe.py`` bewust niet meeneemt (default, forced,
visual_impaired, hearing_impaired, original) en kent een rol toe. Dit is
rapportage voor core/UI; de daadwerkelijke sporenkeuze blijft in beslissen.
"""

from __future__ import annotations

import json
import subprocess
from typing import Any

from app.mediaguard.policy import normalize_language

_AD_WORDS = ("audio description", "audiodescription", "audiodescriptie", "descriptive")
_COMMENTARY_WORDS = ("commentary", "commentaar")
_SIGNS_WORDS = ("signs", "songs")


def _streams(path: str) -> list[dict[str, Any]]:
    try:
        result = subprocess.run(
            ["ffprobe", "-v", "quiet", "-print_format", "json", "-show_streams", path],
            capture_output=True,
            text=True,
            timeout=120,
        )
        data = json.loads(result.stdout) if result.returncode == 0 else {}
    except (OSError, subprocess.SubprocessError, json.JSONDecodeError):
        return []
    streams = data.get("streams") if isinstance(data, dict) else None
    return streams if isinstance(streams, list) else []


def _flag(stream: dict[str, Any], key: str) -> bool:
    return bool((stream.get("disposition") or {}).get(key))


def stream_inventory(path: str) -> dict[str, Any]:
    audio: list[dict[str, Any]] = []
    subtitles: list[dict[str, Any]] = []
    for stream in _streams(path):
        tags = stream.get("tags") or {}
        title = str(tags.get("title") or "")
        lowered = title.lower()
        language = normalize_language(tags.get("language"))
        if stream.get("codec_type") == "audio":
            if _flag(stream, "comment") or any(w in lowered for w in _COMMENTARY_WORDS):
                role = "commentary"
            elif _flag(stream, "visual_impaired") or any(w in lowered for w in _AD_WORDS):
                role = "audio_description"
            else:
                role = "main"
            audio.append(
                {
                    "index": int(stream.get("index") or 0),
                    "codec": str(stream.get("codec_name") or ""),
                    "channels": int(stream.get("channels") or 0),
                    "language": language,
                    "title": title,
                    "default": _flag(stream, "default"),
                    "original": _flag(stream, "original"),
                    "role": role,
                }
            )
        elif stream.get("codec_type") == "subtitle":
            if _flag(stream, "forced") or "forced" in lowered:
                role = "forced"
            elif any(w in lowered for w in _SIGNS_WORDS):
                role = "signs_songs"
            elif _flag(stream, "hearing_impaired") or "sdh" in lowered:
                role = "sdh"
            elif _flag(stream, "comment") or any(w in lowered for w in _COMMENTARY_WORDS):
                role = "commentary"
            else:
                role = "full"
            subtitles.append(
                {
                    "index": int(stream.get("index") or 0),
                    "codec": str(stream.get("codec_name") or ""),
                    "language": language,
                    "title": title,
                    "default": _flag(stream, "default"),
                    "forced": _flag(stream, "forced"),
                    "role": role,
                }
            )
    main_languages = {item["language"] for item in audio if item["role"] == "main"}
    return {
        "audio": audio,
        "subtitles": subtitles,
        "dual_audio": {"eng", "jpn"} <= main_languages,
        "languages": sorted(main_languages),
        "protected": {
            "audio_description": [i["index"] for i in audio if i["role"] == "audio_description"],
            "forced_or_signs": [
                i["index"] for i in subtitles if i["role"] in {"forced", "signs_songs"}
            ],
        },
    }
