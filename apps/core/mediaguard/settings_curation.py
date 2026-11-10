"""Overzichtelijke instellingen: een eenvoudige weergave met de ~25 dingen die een gewone
gebruiker wil kunnen aanpassen, de rest per sectie onder 'Geavanceerd'.

Dit verandert niets aan het register zelf (scopes, validatie, opslag): het voegt alleen
per instelling een sectie, een volgorde, een 'eenvoudig'-vlag en waar nodig een begrijpelijker
label toe. Zie docs/SETTINGS.md voor het overzicht en de motivatie.
"""

from __future__ import annotations

from typing import Any

# (id, titel nl, titel en, uitleg nl, categorieën die als 'geavanceerd' onder deze sectie vallen)
SECTIONS: list[tuple[str, str, str, str, tuple[str, ...]]] = [
    ("prestaties", "Systeem & prestaties", "System & performance",
     "Hoeveel van je server MediaGuard mag gebruiken.",
     ("Systeem & prestaties", "Wachtrij & workers")),
    ("av1", "AV1 & ruimte besparen", "AV1 & saving space",
     "Bestanden kleiner maken zonder zichtbaar kwaliteitsverlies.",
     ("AV1", "AV1 Studio", "Dolby Vision & HDR", "Optimalisatie")),
    ("originelen", "Originelen & herstellen", "Originals & restore",
     "Wat er met het oude bestand gebeurt nadat het vervangen is.",
     ("Quarantaine & herstel", "Veiligheid")),
    ("downloads", "Downloaden & zoeken", "Downloading & searching",
     "Ontbrekende afleveringen en films zoeken, en hoe vaak.",
     ("Ontbrekende media", "Hooks & import", "Identiteit", "Kwaliteit & TRaSH")),
    ("taal", "Taal & anime", "Language & anime",
     "Audio- en ondertitelvoorkeuren en waar anime terechtkomt.",
     ("Audio & ondertiteling", "Anime-routering")),
    ("algemeen", "Algemeen", "General",
     "Taal, tijdzone en back-ups van MediaGuard zelf.",
     ("Systeem", "Back-ups", "Bibliotheek", "Instanties", "Inrichten", "Seerr", "Migratie",
      "AI Adviseur")),
]

# key → (sectie, label nl, label en, uitleg nl) — None = label uit het register houden
SIMPLE: dict[str, tuple[str, str | None, str | None, str | None]] = {
    # Systeem & prestaties
    "resources.av1_cpu_percent": ("prestaties", None, None, None),
    "resources.encodes_cpu": ("prestaties", None, None, None),
    "resources.encodes_gpu": ("prestaties", None, None, None),
    "resources.encode_window_enabled": ("prestaties", None, None, None),
    "resources.encode_window": ("prestaties", None, None, None),
    # AV1
    "encoding.autopilot_enabled": (
        "av1", "Automatisch bestanden kleiner maken (AV1)", "Automatically shrink files (AV1)",
        "MediaGuard zoekt zelf de bestanden die het waard zijn en zet ze om. Elk resultaat "
        "wordt gecontroleerd voordat het je origineel vervangt."),
    "encoding.auto_commit_after_verify": (
        "av1", "Goedgekeurde resultaten direct gebruiken", "Use approved results right away",
        "Na een geslaagde controle vervangt het nieuwe bestand het origineel zonder dat je op "
        "een knop hoeft te drukken. Het origineel blijft eerst bewaard."),
    "encoding.min_saving_percent": (
        "av1", "Alleen omzetten als het minstens zoveel scheelt",
        "Only convert if it saves at least",
        "Levert AV1 minder ruimtewinst op dan dit percentage, dan blijft het origineel staan."),
    "encoding.keep_all_audio": ("av1", "Alle audiosporen bewaren", "Keep all audio tracks", None),
    "encoding.keep_all_subtitles": ("av1", "Alle ondertitels bewaren", "Keep all subtitles", None),
    "dv.allow_av1": (
        "av1", "Ook Dolby Vision-bestanden omzetten", "Also convert Dolby Vision files",
        "Standaard uit: Dolby Vision-bestanden blijven dan onaangeroerd. Aan = omzetten met "
        "behoud van Dolby Vision, alleen als de controle slaagt."),
    "dv.hdr10plus_mode": ("av1", "HDR10+-bestanden", "HDR10+ files", None),
    "dv.dv81_route": (
        "av1", "Dolby Vision 8.1", "Dolby Vision 8.1",
        "DV behouden gaat via de CPU (langzaam). Omzetten naar HDR10 gaat via de GPU (snel); "
        "de Dolby Vision-laag vervalt dan bewust."),
    # Originelen
    "quarantine.mode": (
        "originelen", "Originelen na vervangen", "Originals after replacement", None),
    "quarantine.retention_days": (
        "originelen", "Originelen bewaren (dagen)", "Keep originals (days)",
        "Zo lang kun je een vervanging terugdraaien. Daarna ruimt MediaGuard het origineel op, "
        "alleen als het nieuwe bestand de uitgebreide controle heeft doorstaan."),
    "quarantine.max_size_gb": (
        "originelen", "Maximaal bewaren (GB)", "Keep at most (GB)",
        "Wordt dit bereikt, dan worden de oudste gecontroleerde originelen eerder opgeruimd en "
        "wacht AV1 tot er weer ruimte is."),
    # Downloaden & zoeken
    "backfill.enabled": (
        "downloads", "Ontbrekende afleveringen en films automatisch zoeken",
        "Automatically search for missing episodes and movies", None),
    "backfill.max_searches_per_day": (
        "downloads", "Maximaal zoekopdrachten per dag", "Max searches per day",
        "Spaart je indexers (en hun limieten)."),
    "backfill.include_cutoff_unmet": (
        "downloads", "Ook betere versies zoeken", "Also search for better versions",
        "Zoek ook naar een betere kwaliteit van wat je al hebt (tot het kwaliteitsprofiel "
        "tevreden is)."),
    "optimization.max_active_downloads": (
        "downloads", "Maximaal downloads tegelijk (optimaliseren)",
        "Max simultaneous downloads (optimising)", None),
    "optimization.max_gb_per_day": (
        "downloads", "Maximaal downloaden per dag (GB)", "Max download per day (GB)", None),
    # Taal & anime
    "audio.nl_subtitles_wanted": ("taal", "Nederlandse ondertitels", "Dutch subtitles", None),
    "audio.nl_audio_mode": ("taal", "Nederlandse audio", "Dutch audio", None),
    "audio.anime_preference": ("taal", "Anime: welke audio voorkeur heeft",
                               "Anime audio preference", None),
    "routing.anime.enabled": (
        "taal", "Anime automatisch naar de anime-instantie", "Route anime automatically", None),
    # Algemeen
    "system.timezone": ("algemeen", "Tijdzone", "Time zone", None),
    "backup.daily_enabled": ("algemeen", "Dagelijkse back-up van MediaGuard",
                             "Daily MediaGuard backup", None),
    "updates.check_enabled": ("algemeen", None, None, None),
}

# Vervallen of dubbel: niet tonen (wel nog vindbaar via zoeken en de API).
HIDDEN: dict[str, str] = {
    "optimization.max_concurrent_encodes":
        "Vervangen door 'Encodes tegelijk' onder Systeem & prestaties.",
    "backfill.excluded": "Per serie of film: 'Niet meer zoeken' op de titelpagina.",
}


def section_of(key: str, category: str) -> str:
    if key in SIMPLE:
        return SIMPLE[key][0]
    for sid, *_rest, categories in SECTIONS:
        if category in categories:
            return sid
    return "algemeen"


def annotate(public: dict[str, Any]) -> dict[str, Any]:
    """Voegt section/simple/hidden/order toe en het eenvoudige label waar dat bestaat."""
    key = public["key"]
    out = {**public, "section": section_of(key, public.get("category", "")),
           "simple": key in SIMPLE, "hidden": key in HIDDEN,
           "hidden_reason": HIDDEN.get(key, ""),
           "order": list(SIMPLE).index(key) if key in SIMPLE else 1000}
    if key in SIMPLE:
        _sid, nl, en, help_nl = SIMPLE[key]
        if nl:
            out["label_nl"] = nl
        if en:
            out["label_en"] = en
        if help_nl:
            out["help_nl"] = help_nl
    from mediaguard.settings_guard import annotate as guard

    return guard(out)


def sections_public() -> list[dict[str, Any]]:
    return [{"id": sid, "title_nl": nl, "title_en": en, "intro_nl": intro,
             "categories": list(cats)} for sid, nl, en, intro, cats in SECTIONS]
