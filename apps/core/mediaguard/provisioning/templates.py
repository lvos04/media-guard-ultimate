"""Ingebouwde gewenste staat: custom formats, kwaliteitsprofielen, kwaliteitsdefinities,
naamgeving, mediabeheer, categorieën en rootmappen per instantietype.

Bewust een *redelijke ingebouwde subset* van TRaSH-Guides; de volledige TRaSH-JSON kan via
``/api/v1/policy/trash/import`` worden geïmporteerd en per sjabloon worden toegevoegd
(``quality.extra_trash_formats``)."""

from __future__ import annotations

from typing import Any

PREFIX = "MediaGuard · "
OLD_PREFIX = "Oud · "

# media_type -> standaarden
DEFAULT_CATEGORY = {"tv": "tv", "anime": "tv-anime", "movie": "movies",
                    "anime_movie": "movies-anime"}
# Moet overeenkomen met de mounts in deploy/truenas/compose.yaml (TRaSH-indeling: één
# /data-mount met media/ en downloads/; test_stack_regressions controleert dit)
DEFAULT_ROOT = {"tv": "/data/media/tv", "anime": "/data/media/anime",
                "movie": "/data/media/movies", "anime_movie": "/data/media/animefilms"}
ANIME_TYPES = {"anime", "anime_movie"}

# Profielsjablonen per mediatype; de eerste is het standaardprofiel van de instantie.
# sonarr-series krijgt ook het animeprofiel zodat Seerr's anime-velden geldig zijn.
PROFILE_TEMPLATES_FOR = {"tv": ["series", "anime"], "anime": ["anime"], "movie": ["films"],
                         "anime_movie": ["animefilms"]}


def _title(name: str, regex: str) -> dict[str, Any]:
    return {"name": name, "implementation": "ReleaseTitleSpecification", "negate": False,
            "required": False, "fields": [{"name": "value", "value": regex}]}


def _group(name: str, regex: str) -> dict[str, Any]:
    return {"name": name, "implementation": "ReleaseGroupSpecification", "negate": False,
            "required": False, "fields": [{"name": "value", "value": regex}]}


def _resolution(name: str, value: int) -> dict[str, Any]:
    return {"name": name, "implementation": "ResolutionSpecification", "negate": False,
            "required": False, "fields": [{"name": "value", "value": value}]}


# Sonarr/Radarr eisen max > min (max 0 = "onbeperkt" bestaat niet: 400 'Max must be greater
# than Min'); 1000 GB werkt in de praktijk als "geen bovengrens".
SIZE_NO_UPPER_GB = 1000


def _size(name: str, min_gb: float, max_gb: float = SIZE_NO_UPPER_GB) -> dict[str, Any]:
    """Grootte-conditie in GB; standaard zonder praktische bovengrens."""
    return {"name": name, "implementation": "SizeSpecification", "negate": False,
            "required": True, "fields": [{"name": "min", "value": min_gb},
                                         {"name": "max", "value": max_gb}]}


def _req(spec: dict[str, Any], *, negate: bool = False) -> dict[str, Any]:
    return {**spec, "required": True, "negate": negate}


# Dolby Vision zonder HDR10-terugval (profiel 5): de AV1-pipeline kan dit niet omzetten, dus
# alleen hier telt de bestandsgrootte echt; bij alle andere releases bepaalt de encode de
# uiteindelijke grootte en is grootte alleen nog een gelijkspelbreker.
_DV_REGEX = r"\b(DV|DoVi|Dolby[ ._-]?Vision)\b"
_HDR_REGEX = r"\b(HDR(10)?(\+|P(lus)?)?|HDR10Plus)\b"
_DV5 = [_req(_title("DV", _DV_REGEX)), _req(_title("Geen HDR", _HDR_REGEX), negate=True)]


def _lang(name: str, lang_id: int) -> dict[str, Any]:
    return {"name": name, "implementation": "LanguageSpecification", "negate": False,
            "required": False, "fields": [{"name": "value", "value": lang_id}]}


# naam (zonder prefix) -> specificaties
CUSTOM_FORMATS: dict[str, list[dict[str, Any]]] = {
    "Dual Audio": [_title("Dual Audio", r"\b(dual[ ._-]?audio|multi[ ._-]?audio|DUAL)\b")],
    "Engelse dub": [_title("English dub",
                           r"\b(eng(lish)?[ ._-]?dub(bed)?|engelse[ ._-]?dub|dubbed)\b")],
    "Anime raws": [_title("Raws", r"\b(raws?)\b")],
    "Anime slechte groep": [_group("LQ groups",
                                   r"^(AnimeRG|9anime|AnimeKaizoku|Kanjouteki|ASW|HollowRG)$")],
    "Upscaled": [_title("Upscaled", r"\b(upscaled?|AI[ ._-]?upscale[ds]?|regraded?)\b")],
    "HDR": [_title("HDR", r"\b(HDR(10)?(\+|P(lus)?)?|HDR10Plus)\b")],
    "Dolby Vision": [_title("DV", r"\b(DV|DoVi|Dolby[ ._-]?Vision)\b")],
    "DV5 (zonder HDR-terugval)": list(_DV5),
    "DV5 groot (film)": [*_DV5, _size("Groter dan 25 GB", 25)],
    "DV5 groot (aflevering)": [*_DV5, _size("Groter dan 8 GB", 8)],
    "Taal: Nederlands": [_lang("Dutch", 7)],
    "Resolutie 2160p": [_resolution("2160p", 2160)],
}

# Anime is audio-first (E2/L2): 1080p en 2160p staan in één kwaliteitsgroep, zodat Sonarr
# binnen de groep op custom-format-score rangschikt. Dual Audio (+2000) wint dan van 4K
# (+200) — anders rangschikt Sonarr kwaliteit vóór score en wint 4K Japans-alleen
# (gemeten in tests/integration/stack, test d1b/d1c).
ANIME_MERGE_GROUP = {"name": "MediaGuard · 1080p/2160p (audio eerst)",
                     "qualities": ["WEBRip-1080p", "WEBDL-1080p", "Bluray-1080p",
                                   "WEBRip-2160p", "WEBDL-2160p", "Bluray-2160p"]}
ANIME_SCORES = {"Resolutie 2160p": 200, "Dual Audio": 2000, "Engelse dub": 500, "Anime raws": -1000,
                "Anime slechte groep": -10000, "Upscaled": -10000, "Taal: Nederlands": 0}

# Upgraden op custom-format-score stopt zodra de beste realistische release er is (rapport 8:
# ±611 anime-upgrades per dag). Een onhaalbare grens (10000) liet Sonarr eindeloos doorgaan
# zodra er een release met een iets hogere score verscheen. Anime: Dual Audio + 2160p;
# films: HDR + Dolby Vision; series: geen positieve scores, dus alleen op kwaliteit.
ANIME_CUTOFF_SCORE = ANIME_SCORES["Dual Audio"] + ANIME_SCORES["Resolutie 2160p"]
# Kleinere scoreverschillen (bv. een REPACK met dezelfde formats) zijn geen upgrade waard.
MIN_UPGRADE_SCORE = 100

QUALITY_TEMPLATES: dict[str, dict[str, Any]] = {
    "series": {
        "name": "WEB-2160p", "label_nl": "Series: WEB-2160p > WEB-1080p",
        "allowed": ["WEBDL-1080p", "WEBRip-1080p", "WEBDL-2160p", "WEBRip-2160p"],
        "cutoff": "WEBDL-2160p", "upgrade": True, "min_score": 0, "cutoff_score": 0,
        "scores": {"Upscaled": -10000, "Taal: Nederlands": 0, "DV5 groot (aflevering)": -10000},
        "language": "any"},
    "anime": {
        "name": "Anime Dual Audio",
        "label_nl": "Anime: Dual Audio eerst, daarna 4K waar beschikbaar",
        "allowed": ["HDTV-720p", "WEBRip-720p", "WEBDL-720p", "Bluray-720p", "HDTV-1080p",
                    "WEBRip-1080p", "WEBDL-1080p", "Bluray-1080p", "Bluray-1080p Remux",
                    "WEBRip-2160p", "WEBDL-2160p", "Bluray-2160p"],
        "merge_group": ANIME_MERGE_GROUP,
        "cutoff": "Bluray-2160p", "upgrade": True, "min_score": 0,
        "cutoff_score": ANIME_CUTOFF_SCORE,
        "scores": dict(ANIME_SCORES), "language": "any"},
    "films": {
        "name": "UHD HDR", "label_nl": "Films: UHD HDR/DV > 1080p",
        "allowed": ["WEBRip-1080p", "WEBDL-1080p", "Bluray-1080p", "WEBRip-2160p",
                    "WEBDL-2160p", "Bluray-2160p"],
        "cutoff": "Bluray-2160p", "upgrade": True, "min_score": 0, "cutoff_score": 1100,
        "scores": {"HDR": 500, "Dolby Vision": 600, "Upscaled": -10000, "Taal: Nederlands": 0,
                   # DV5 kan niet naar AV1: laagste voorkeur, en te groot = weigeren
                   "DV5 (zonder HDR-terugval)": -600, "DV5 groot (film)": -10000},
        "language": "original"},
    "animefilms": {
        "name": "Anime Dual Audio",
        "label_nl": "Animefilms: Dual Audio eerst, daarna 4K waar beschikbaar",
        "allowed": ["HDTV-720p", "WEBRip-720p", "WEBDL-720p", "Bluray-720p", "HDTV-1080p",
                    "WEBRip-1080p", "WEBDL-1080p", "Bluray-1080p", "Remux-1080p",
                    "WEBRip-2160p", "WEBDL-2160p", "Bluray-2160p"],
        "merge_group": ANIME_MERGE_GROUP,
        "cutoff": "Bluray-2160p", "upgrade": True, "min_score": 0,
        "cutoff_score": ANIME_CUTOFF_SCORE,
        "scores": dict(ANIME_SCORES), "language": "any"},
}
RADARR_LANGUAGE = {"original": {"id": -2, "name": "Original"}, "any": {"id": -1, "name": "Any"}}

# Kwaliteitsdefinities (MB per minuut): min, preferred, max (None = onbeperkt).
# Geen bovengrens: alles gaat daarna door de AV1-pipeline, die de eindgrootte bepaalt.
# 'Preferred' blijft alleen als gelijkspelbreker tussen verder gelijke releases.
# DV5 (niet om te zetten) wordt via custom formats op grootte beoordeeld (zie boven).
# Volledige schijven (BR-DISK/ISO) blijven geweigerd: die kunnen niet worden geïmporteerd.
QUALITY_SIZES: dict[str, tuple[float, float, float | None]] = {
    "HDTV-720p": (10, 95, None), "WEBDL-720p": (10, 95, None), "WEBRip-720p": (10, 95, None),
    "Bluray-720p": (17.1, 95, None), "HDTV-1080p": (15, 120, None),
    "WEBDL-1080p": (15, 120, None), "WEBRip-1080p": (15, 120, None),
    "Bluray-1080p": (50.4, 150, None), "WEBDL-2160p": (25, 200, None),
    "WEBRip-2160p": (25, 200, None), "Bluray-2160p": (94.6, 300, None),
}

SONARR_NAMING = {
    "renameEpisodes": True, "replaceIllegalCharacters": True,
    "standardEpisodeFormat": "{Series TitleYear} - S{season:00}E{episode:00} - "
                             "{Episode CleanTitle} [{Custom Formats }{Quality Full}]"
                             "{[MediaInfo VideoDynamicRangeType]}{[Mediainfo AudioCodec}"
                             "{ Mediainfo AudioChannels]}{[MediaInfo VideoCodec]}"
                             "{-Release Group}",
    "dailyEpisodeFormat": "{Series TitleYear} - {Air-Date} - {Episode CleanTitle} "
                          "[{Custom Formats }{Quality Full}]{[MediaInfo VideoCodec]}"
                          "{-Release Group}",
    "animeEpisodeFormat": "{Series TitleYear} - S{season:00}E{episode:00} - {absolute:000} - "
                          "{Episode CleanTitle} [{Custom Formats }{Quality Full}]"
                          "{[MediaInfo VideoDynamicRangeType]}[{MediaInfo VideoBitDepth}bit]"
                          "{[MediaInfo VideoCodec]}[{Mediainfo AudioCodec} "
                          "{ Mediainfo AudioChannels}]{MediaInfo AudioLanguages}"
                          "{-Release Group}",
    "seriesFolderFormat": "{Series TitleYear} [tvdbid-{TvdbId}]",
    "seasonFolderFormat": "Season {season:00}",
    "specialsFolderFormat": "Specials",
}
RADARR_NAMING = {
    "renameMovies": True, "replaceIllegalCharacters": True,
    "standardMovieFormat": "{Movie CleanTitle} {(Release Year)} {tmdb-{TmdbId}} "
                           "{edition-{Edition Tags}} {[Custom Formats]}{[Quality Full]}"
                           "{[MediaInfo 3D]}{[MediaInfo VideoDynamicRangeType]}"
                           "{[Mediainfo AudioCodec}{ Mediainfo AudioChannels]}"
                           "{[Mediainfo VideoCodec]}{-Release Group}",
    "movieFolderFormat": "{Movie CleanTitle} ({Release Year}) {tmdb-{TmdbId}}",
}

# Indexercategorieën (Newznab/Torznab)
SONARR_CATEGORIES = [5030, 5040, 5045]
SONARR_ANIME_CATEGORIES = [5070]
RADARR_CATEGORIES = [2000, 2040, 2045, 2060]
RADARR_ANIME_EXTRA = [5070]
# Prowlarr sync-categorieën
PROWLARR_SONARR = [5000, 5010, 5020, 5030, 5040, 5045, 5050, 5090]
PROWLARR_RADARR = [2000, 2010, 2020, 2030, 2040, 2045, 2050, 2060, 2070, 2080, 2090]

ANIME_HINTS = ("anime", "nyaa", "tosho", "animebytes", "anidex", "tokyotosho", "erai",
               "subsplease", "shana", "bakabt", "anirena")

DC_IMPLEMENTATIONS = {"Sabnzbd": "usenet", "Nzbget": "usenet", "QBittorrent": "torrent",
                      "Transmission": "torrent", "Deluge": "torrent"}
# welke geheime velden een downloadclient minimaal nodig heeft
DC_REQUIRED_SECRETS = {"Sabnzbd": ("api_key",), "Nzbget": ("password",),
                       "QBittorrent": (), "Transmission": (), "Deluge": ("password",)}
DC_DEFAULT_PORTS = {"Sabnzbd": 8080, "Nzbget": 6789, "QBittorrent": 8080,
                    "Transmission": 9091, "Deluge": 8112}


def is_anime_name(*texts: str | None) -> bool:
    blob = " ".join(t or "" for t in texts).lower()
    return any(h in blob for h in ANIME_HINTS)


def owned_name(name: str) -> str:
    return f"{PREFIX}{name}"


def is_owned_name(name: str | None) -> bool:
    return bool(name) and str(name).startswith(PREFIX)


def templates_for(media_type: str) -> list[str]:
    return list(PROFILE_TEMPLATES_FOR.get(media_type, []))


def formats_for(media_type: str, extra: dict[str, list[str]] | None = None) -> list[str]:
    names: list[str] = []
    for tpl in templates_for(media_type):
        for cf in QUALITY_TEMPLATES[tpl]["scores"]:
            if cf not in names:
                names.append(cf)
    return names
