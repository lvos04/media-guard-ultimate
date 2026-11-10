"""Scoped DV-beleid met zichtbare overerving ``Globaal → Bibliotheek → Profiel → Titel →
(Bestand) → Job``. Waarden staan in het settings-register (persistent, geaudit,
herstartbestendig); deze module voegt de DV-specifieke weergave en versiegeschiedenis toe.

Belangrijk (Q3): schakelbaar is het *DV-beleid*, nooit de integriteitscontrole. Er bestaat
daarom geen instelling die verificatie uitzet.
"""

from __future__ import annotations

from dataclasses import asdict, dataclass, field
from typing import TYPE_CHECKING, Any

from mediaguard.db import dumps, utcnow
from mediaguard.settings_registry import SettingDef

if TYPE_CHECKING:
    from mediaguard.context import AppContext

CATEGORY = "Dolby Vision & HDR"
DV_SCOPES = ("global", "library", "profile", "series", "movie", "file", "job")
LEVEL_LABELS_NL = {"global": "Globaal", "library": "Bibliotheek", "profile": "Profiel",
                   "series": "Titel", "movie": "Titel", "file": "Bestand", "job": "Job"}
MODES = ("keep_original", "trial_copy", "auto_after_verify", "experimental_keep_source")
MODE_LABELS_NL = {
    "keep_original": "Origineel behouden",
    "trial_copy": "Proefconversie op kopie",
    "auto_after_verify": "Automatisch converteren na verificatie",
    "experimental_keep_source": "Experimenteel profiel (behoud bron)",
}
PROFILES = ("8.1", "8.4", "7_mel", "7_fel", "5")
PROFILE_LABELS_NL = {"8.1": "DV 8.1", "8.4": "DV 8.4", "7_mel": "DV 7 MEL",
                     "7_fel": "DV 7 FEL", "5": "DV 5"}
KEYS = {"allow": "dv.allow_av1", "mode": "dv.mode", "allowed_profiles": "dv.allowed_profiles",
        "hdr10plus_mode": "dv.hdr10plus_mode", "dv81_route": "dv.dv81_route"}
DV81_ROUTES = ("keep_dv", "hdr10_gpu")


def setting_defs() -> list[SettingDef]:
    return [
        SettingDef("dv.allow_av1", "bool", False, CATEGORY, "Dolby Vision → AV1 toestaan",
                   "Allow Dolby Vision → AV1",
                   help_nl="Schakelaar per Globaal → Bibliotheek → Profiel → Titel → Job. De "
                           "laatst expliciet ingestelde uitzondering wint. Metadata- en "
                           "herstelverificatie blijven altijd verplicht.",
                   scopes=DV_SCOPES,
                   effect_nl="Bestaande en nieuwe DV-taken volgen direct het nieuwe beleid."),
        SettingDef("dv.mode", "enum", "auto_after_verify", CATEGORY, "Dolby Vision-modus",
                   "Dolby Vision mode", choices=MODES, choice_labels_nl=MODE_LABELS_NL,
                   help_nl="Geldt alleen als 'Dolby Vision → AV1 toestaan' aan staat (standaard "
                           "uit: DV-bestanden blijven dan onaangeroerd). Set-and-forget: na "
                           "geslaagde verificatie van RPU en kleurwaarden automatisch "
                           "activeren; 'Proefconversie' vraagt eerst om jouw akkoord.",
                   scopes=DV_SCOPES),
        SettingDef("dv.allowed_profiles", "list", ["8.1", "8.4", "7_mel"], CATEGORY,
                   "Vrijgegeven DV-invoerprofielen", "Allowed DV input profiles",
                   choices=PROFILES, choice_labels_nl=PROFILE_LABELS_NL,
                   help_nl="DV 7 FEL alleen in experimentele modus naar apart resultaat; DV 5 "
                           "alleen met een bewezen route van de encoder.", scopes=DV_SCOPES),
        SettingDef("dv.hdr10plus_mode", "enum", "hdr10_fallback_explicit", CATEGORY,
                   "HDR10+-beleid",
                   "HDR10+ policy",
                   choices=("convert_verified", "skip", "hdr10_fallback_explicit"),
                   choice_labels_nl={
                       "convert_verified": "Omzetten met behoud van HDR10+ (per beeld "
                                           "gecontroleerd)",
                       "skip": "Overslaan (bron behouden)",
                       "hdr10_fallback_explicit": "Omzetten als HDR10 — dynamische HDR10+-"
                                                  "metadata gaat bewust verloren"},
                   help_nl="Standaard: omzetten als HDR10 via SVT-AV1 (snel; de statische "
                           "HDR10-metadata blijft, de dynamische HDR10+-laag niet). Omzetten "
                           "met behoud gebruikt libaom: per aflevering 4K al gauw een dag "
                           "rekenwerk, per beeld gecontroleerd; alleen als de encoder de route "
                           "heeft bewezen.",
                   scopes=DV_SCOPES),
        SettingDef("dv.dv81_route", "enum", "keep_dv", CATEGORY,
                   "Dolby Vision 8.1", "Dolby Vision 8.1",
                   choices=DV81_ROUTES,
                   choice_labels_nl={
                       "keep_dv": "DV behouden (CPU, langzaam)",
                       "hdr10_gpu": "Omzetten naar HDR10 (GPU, snel — Dolby Vision vervalt)"},
                   help_nl="DV 8.1 heeft een gewone HDR10-basislaag. 'Omzetten naar HDR10' "
                           "zet die via de Arc om en laat de Dolby Vision-laag bewust vallen "
                           "(op een DV-tv speelt het dan als HDR10). Profiel 7 blijft op de CPU "
                           "of wordt overgeslagen; profiel 5 wordt nooit omgezet.",
                   scopes=DV_SCOPES),
        SettingDef("dv.show_playback_warnings", "bool", True, CATEGORY,
                   "Afspeelwaarschuwingen tonen", "Show playback warnings",
                   help_nl="Bevestigde afspeelketens verminderen waarschuwingen; technische "
                           "controles blijven altijd actief.", scopes=("global",)),
    ]


@dataclass
class DvPolicy:
    allow: bool
    mode: str
    allowed_profiles: list[str]
    hdr10plus_mode: str
    trace: dict[str, Any] = field(default_factory=dict)
    dv81_route: str = "keep_dv"

    def to_dict(self) -> dict[str, Any]:
        d = asdict(self)
        d["mode_label_nl"] = MODE_LABELS_NL[self.mode]
        return d


def scope_chain(*, library: str | None = None, profile: str | None = None,
                series: str | None = None, movie: str | None = None, file: str | None = None,
                job: str | None = None) -> dict[str, str]:
    chain: dict[str, str] = {}
    for key, value in (("library", library), ("profile", profile), ("series", series),
                       ("movie", movie), ("file", file), ("job", job)):
        if value not in (None, ""):
            chain[key] = str(value)
    return chain


def effective(ctx: AppContext, chain: dict[str, str]) -> DvPolicy:
    values: dict[str, Any] = {}
    trace: dict[str, Any] = {}
    for name, key in KEYS.items():
        eff = ctx.config.effective(key, chain)
        values[name] = eff["value"]
        trace[name] = {"value": eff["value"], "source": eff["source"],
                       "source_label_nl": LEVEL_LABELS_NL.get(eff["source"], "Standaard"),
                       "levels": [t | {"label_nl": LEVEL_LABELS_NL[t["scope"]]}
                                  for t in eff["trace"]]}
    return DvPolicy(allow=bool(values["allow"]), mode=str(values["mode"]),
                    allowed_profiles=list(values["allowed_profiles"]),
                    hdr10plus_mode=normalize_hdr10plus(str(values["hdr10plus_mode"])),
                    trace=trace,
                    dv81_route=str(values.get("dv81_route") or "keep_dv"))


def normalize_hdr10plus(value: str) -> str:
    # oude waarde van vóór de bewezen route
    return "convert_verified" if value == "allow_when_proven" else value


def levels_view(ctx: AppContext, chain: dict[str, str]) -> list[dict[str, Any]]:
    """Per niveau de expliciet ingestelde DV-waarden (UI: overervingsweergave)."""
    full = {"global": "", **chain}
    out = []
    for scope in DV_SCOPES:
        if scope not in full:
            continue
        values = {}
        for name, key in KEYS.items():
            raw = ctx.config.raw(scope, full[scope], key)
            if raw is not None:
                values[name] = raw
        out.append({"scope_type": scope, "scope_id": full[scope],
                    "label_nl": LEVEL_LABELS_NL[scope], "values": values})
    return out


def apply_change(ctx: AppContext, scope_type: str, scope_id: str, values: dict[str, Any],
                 reset: list[str], actor: str, reason: str | None) -> dict[str, Any]:
    """Alles-of-niets: valideren, dan opslaan + versie vastleggen. Geeft oud/nieuw."""
    if scope_type not in DV_SCOPES:
        raise ValueError(f"onbekend DV-niveau '{scope_type}'")
    if scope_type != "global" and not scope_id:
        raise ValueError("scope_id is verplicht buiten het globale niveau")
    if scope_type == "global":
        scope_id = ""
    for name in list(values) + reset:
        if name not in KEYS:
            raise ValueError(f"onbekende DV-instelling '{name}'")
    for name, value in values.items():
        ctx.registry.get_def(KEYS[name]).validate(value)
    old = {n: ctx.config.raw(scope_type, scope_id, KEYS[n]) for n in KEYS}
    with ctx.db.transaction():  # zelfde verbinding: alles of niets
        for name in reset:
            ctx.config.reset(scope_type, scope_id, KEYS[name])
        for name, value in values.items():
            ctx.config.set(scope_type, scope_id, KEYS[name], value, actor)
        new = {n: ctx.config.raw(scope_type, scope_id, KEYS[n]) for n in KEYS}
        ctx.db.execute(
            "INSERT INTO dv_policy_versions(scope_type,scope_id,values_json,changed_at,"
            "changed_by,reason) VALUES (?,?,?,?,?,?)",
            (scope_type, scope_id, dumps(new), utcnow(), actor, reason))
    return {"old": old, "new": new}
