"""ISO 639-normalisatie naar ISO 639-1 waar bekend (``nl``, ``en``, ``ja``, …).

Accepteert 639-1 (``nl``), 639-2/T (``nld``), 639-2/B (``dut``), Engelse en Nederlandse
namen (zoals Sonarr/Radarr ze leveren: ``English``, ``Japanese``, ``Dutch``,
``Flemish``) en BCP-47-varianten (``nl-BE``, ``en_US``). Leeg/ongeldig → ``und``.
Uitbreidbaar via ``register``.
"""

from __future__ import annotations

import re
from typing import Any

UNDEFINED = "und"

_TABLE: dict[str, tuple[str, ...]] = {
    "nl": ("nld", "dut", "dutch", "nederlands", "flemish", "vlaams", "nl-be", "nl-nl"),
    "en": ("eng", "english", "engels"),
    "ja": ("jpn", "jap", "japanese", "japans"),
    "de": ("deu", "ger", "german", "duits"),
    "fr": ("fra", "fre", "french", "frans"),
    "es": ("spa", "spanish", "spaans", "castilian", "latino"),
    "it": ("ita", "italian", "italiaans"),
    "pt": ("por", "portuguese", "portugees"),
    "ko": ("kor", "korean", "koreaans"),
    "zh": ("zho", "chi", "chinese", "chinees", "mandarin", "cantonese", "yue", "cmn"),
    "ru": ("rus", "russian", "russisch"),
    "sv": ("swe", "swedish", "zweeds"),
    "da": ("dan", "danish", "deens"),
    "no": ("nor", "nob", "nno", "norwegian", "noors"),
    "fi": ("fin", "finnish", "fins"),
    "pl": ("pol", "polish", "pools"),
    "tr": ("tur", "turkish", "turks"),
    "ar": ("ara", "arabic", "arabisch"),
    "hi": ("hin", "hindi"),
    "th": ("tha", "thai"),
}
_ALIASES: dict[str, str] = {}
UNDEFINED_TOKENS = {"", "und", "unk", "unknown", "none", "null", "mul", "zxx", "mis", "qaa",
                    "xx", "n/a", "onbekend"}
NAMES_NL = {"nl": "Nederlands", "en": "Engels", "ja": "Japans", "de": "Duits", "fr": "Frans",
            "es": "Spaans", "it": "Italiaans", "ko": "Koreaans", "zh": "Chinees",
            UNDEFINED: "Onbekend"}


def register(code: str, *aliases: str) -> None:
    _ALIASES[code.lower()] = code.lower()
    for alias in aliases:
        _ALIASES[alias.lower()] = code.lower()


for _code, _aliases in _TABLE.items():
    register(_code, *_aliases)


def normalize(value: object) -> str:
    if value is None:
        return UNDEFINED
    text = str(value).strip().lower().replace("_", "-")
    if text in UNDEFINED_TOKENS:
        return UNDEFINED
    if text in _ALIASES:
        return _ALIASES[text]
    base = text.split("-", 1)[0]
    if base in _ALIASES:
        return _ALIASES[base]
    if re.fullmatch(r"[a-z]{2,3}", base) and base not in UNDEFINED_TOKENS:
        return base  # onbekende maar syntactisch geldige code: behouden
    return UNDEFINED


def normalize_list(values: object) -> list[str]:
    if not values:
        return []
    items: list[Any] = re.split(r"[/,|]+", values) if isinstance(values, str) else \
        list(values) if isinstance(values, list | tuple | set) else [values]
    out: list[str] = []
    for v in items:
        if isinstance(v, dict):
            v = v.get("name") or v.get("code") or v.get("id")
        code = normalize(v)
        if code not in out:
            out.append(code)
    return out


def name_nl(code: str) -> str:
    return NAMES_NL.get(code, code)
