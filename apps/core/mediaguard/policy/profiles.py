"""MediaGuard-policyprofielen + effectieve policy met scope-overerving.

Overerving (R2.5): ``globaal → media-type → instantie → serie/film → seizoen/episode/
bestand``. Het profiel zelf wordt per scope gekozen via instelling ``policy.profile_id``;
losse uitzonderingen (bv. NL-audio voor één kinderserie, of een anime-serie die
Japans-eerst wil) via ``audio.*``/``policy.ranking_mode`` op dezelfde scopes. De
effectieve uitkomst bevat de herkomst van elke waarde.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any, Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator

from mediaguard.db import dumps, loads, utcnow
from mediaguard.settings_registry import SettingDef

if TYPE_CHECKING:
    from mediaguard.context import AppContext

RANK_KEYS = ("audio", "source", "resolution", "hdr", "custom_format", "group", "size")
POLICY_SCOPES = ("global", "media_type", "instance", "series", "movie", "season", "episode",
                 "file")


class AudioConfig(BaseModel):
    model_config = ConfigDict(extra="forbid")
    mode: Literal["anime", "original"] = "original"
    anime_preference: Literal["dual_first", "english_first", "japanese_first"] = "dual_first"
    nl_audio: Literal["off", "preferred", "required"] = "off"
    nl_subtitles_wanted: bool = True
    subtitle_preference: list[Literal["nl", "en", "ja"]] = Field(
        default_factory=lambda: ["nl", "en"])  # type: ignore[arg-type]


class ProfileConfig(BaseModel):
    model_config = ConfigDict(extra="forbid")
    ranking_order: list[str] = Field(
        default_factory=lambda: ["audio", "source", "resolution", "custom_format", "group"])
    audio: AudioConfig = Field(default_factory=AudioConfig)
    min_resolution: int | None = Field(default=None, ge=240, le=4320)
    max_resolution: int | None = Field(default=None, ge=240, le=4320)
    max_size_gb: float | None = Field(default=None, gt=0, le=1000)
    min_custom_format_score: int | None = None
    blocked_groups: list[str] = Field(default_factory=list, max_length=500)
    preferred_groups: list[str] = Field(default_factory=list, max_length=500)
    prefer_hdr: bool = False
    prefer_smaller: bool = False
    reject_false_audio_claims: bool = True
    trash_score_set: str = Field(default="default", max_length=64)

    @field_validator("ranking_order")
    @classmethod
    def _order(cls, value: list[str]) -> list[str]:
        unknown = [v for v in value if v not in RANK_KEYS]
        if unknown:
            raise ValueError(f"onbekende rangschikkingssleutel(s): {', '.join(unknown)}; "
                             f"toegestaan: {', '.join(RANK_KEYS)}")
        if len(set(value)) != len(value):
            raise ValueError("rangschikkingssleutels mogen niet dubbel voorkomen")
        return value


BUILTIN: dict[str, dict[str, Any]] = {
    "anime-dual-first": {
        "name_nl": "Anime: Dual Audio eerst",
        "description_nl": "Dual Audio (EN+JA) > Engels > Japans (fallback, blijft zoeken). "
                          "Audio weegt zwaarder dan resolutie.",
        "config": {"ranking_order": ["audio", "source", "resolution", "custom_format", "group"],
                   "audio": {"mode": "anime", "anime_preference": "dual_first",
                             "nl_subtitles_wanted": True, "subtitle_preference": ["nl", "en"]}},
    },
    "standaard": {
        "name_nl": "Standaard: originele audio + NL ondertitels",
        "description_nl": "Originele audio behouden, Nederlandse ondertitels gewenst.",
        "config": {"ranking_order": ["audio", "source", "resolution", "custom_format", "group"],
                   "audio": {"mode": "original", "nl_audio": "off",
                             "nl_subtitles_wanted": True}},
    },
    "films-4k": {
        "name_nl": "Films 4K",
        "description_nl": "Resolutie eerst (2160p), daarna HDR en bron. Originele audio.",
        "config": {"ranking_order": ["resolution", "hdr", "source", "audio", "custom_format",
                                     "group"],
                   "prefer_hdr": True,
                   "audio": {"mode": "original", "nl_subtitles_wanted": True}},
    },
    "nl-audio-subs": {
        "name_nl": "Nederlandse audio/ondertitels",
        "description_nl": "Nederlands gesproken heeft voorkeur, anders origineel met NL subs.",
        "config": {"ranking_order": ["audio", "source", "resolution", "custom_format", "group"],
                   "audio": {"mode": "original", "nl_audio": "preferred",
                             "nl_subtitles_wanted": True}},
    },
    "kids-nl-audio": {
        "name_nl": "Kinderen: Nederlandse audio verplicht",
        "description_nl": "Alleen releases met Nederlands gesproken audio.",
        "config": {"ranking_order": ["audio", "source", "resolution", "custom_format", "group"],
                   "audio": {"mode": "original", "nl_audio": "required",
                             "nl_subtitles_wanted": False}},
    },
    "hdr-dv": {
        "name_nl": "HDR / Dolby Vision",
        "description_nl": "HDR/DV-releases eerst; DV-conversie volgt het aparte DV-beleid.",
        "config": {"ranking_order": ["hdr", "resolution", "source", "audio", "custom_format",
                                     "group"],
                   "prefer_hdr": True, "audio": {"mode": "original"}},
    },
    "opslag": {
        "name_nl": "Opslagbewust",
        "description_nl": "Maximaal 1080p, kleinere releases bij gelijke kwaliteit.",
        "config": {"ranking_order": ["audio", "resolution", "source", "size", "custom_format",
                                     "group"],
                   "max_resolution": 1080, "prefer_smaller": True,
                   "audio": {"mode": "original"}},
    },
}
MEDIA_TYPE_DEFAULTS = {"anime": "anime-dual-first", "anime_movie": "anime-dual-first",
                       "tv": "standaard", "movie": "standaard"}


def setting_defs() -> list[SettingDef]:
    cat_p, cat_a = "Kwaliteit & TRaSH", "Audio & ondertiteling"
    return [
        SettingDef("policy.profile_id", "string", "auto", cat_p, "Policyprofiel",
                   "Policy profile",
                   help_nl="'auto' kiest per mediatype (anime → Anime: Dual Audio eerst, "
                           "overig → Standaard). Kies een profiel-ID om te overschrijven.",
                   scopes=POLICY_SCOPES),
        SettingDef("policy.ranking_mode", "enum", "profile", cat_p, "Rangschikking",
                   "Ranking mode", choices=("profile", "audio_first", "resolution_first"),
                   choice_labels_nl={"profile": "Volgens profiel", "audio_first": "Audio eerst",
                                     "resolution_first": "Resolutie eerst"},
                   help_nl="Identiteit en bestandsveiligheid gaan altijd voor.",
                   scopes=POLICY_SCOPES),
        SettingDef("audio.nl_audio_mode", "enum", "profile", cat_a, "Nederlandse audio",
                   "Dutch audio", choices=("profile", "off", "preferred", "required"),
                   choice_labels_nl={"profile": "Volgens profiel", "off": "Niet nodig",
                                     "preferred": "Voorkeur", "required": "Verplicht"},
                   help_nl="Bijvoorbeeld 'Verplicht' voor één kinderserie zonder de "
                           "animevoorkeuren globaal te wijzigen.", scopes=POLICY_SCOPES),
        SettingDef("audio.nl_subtitles_wanted", "enum", "profile", cat_a,
                   "Nederlandse ondertitels gewenst", "Dutch subtitles wanted",
                   choices=("profile", "yes", "no"),
                   choice_labels_nl={"profile": "Volgens profiel", "yes": "Ja", "no": "Nee"},
                   scopes=POLICY_SCOPES),
        SettingDef("audio.anime_preference", "enum", "profile", cat_a, "Anime-audiovoorkeur",
                   "Anime audio preference",
                   choices=("profile", "dual_first", "english_first", "japanese_first"),
                   choice_labels_nl={"profile": "Volgens profiel",
                                     "dual_first": "Dual Audio > Engels > Japans",
                                     "english_first": "Engels eerst",
                                     "japanese_first": "Japans eerst (serie-uitzondering)"},
                   scopes=POLICY_SCOPES),
    ]


def ensure_builtin(ctx: AppContext) -> None:
    # één keer per proces is genoeg (werd bij elke policyberekening opnieuw gecontroleerd)
    if ctx.services.get("policy_builtin_ok"):
        return
    existing = {r["id"] for r in ctx.db.fetch_all("SELECT id FROM policy_profiles")}
    for pid, data in BUILTIN.items():
        if pid in existing:
            continue
        cfg = ProfileConfig.model_validate(data["config"]).model_dump()
        ctx.db.execute(
            "INSERT OR IGNORE INTO policy_profiles(id,name_nl,description_nl,builtin,config_json,"
            "version,updated_at,updated_by) VALUES (?,?,?,1,?,1,?,'system')",
            (pid, data["name_nl"], data["description_nl"], dumps(cfg), utcnow()))
    ctx.services["policy_builtin_ok"] = True


def list_profiles(ctx: AppContext) -> list[dict[str, Any]]:
    ensure_builtin(ctx)
    rows = ctx.db.fetch_dicts("SELECT * FROM policy_profiles ORDER BY builtin DESC, id")
    for r in rows:
        r["config"] = loads(r.pop("config_json"))
        r["builtin"] = bool(r["builtin"])
    return rows


def get_profile(ctx: AppContext, profile_id: str) -> dict[str, Any] | None:
    ensure_builtin(ctx)
    row = ctx.db.fetch_one("SELECT * FROM policy_profiles WHERE id=?", (profile_id,))
    if row is None:
        return None
    d = dict(row)
    d["config"] = loads(d.pop("config_json"))
    d["builtin"] = bool(d["builtin"])
    return d


def save_profile(ctx: AppContext, profile_id: str, *, name_nl: str | None,
                 description_nl: str | None, config: dict[str, Any], actor: str
                 ) -> dict[str, Any]:
    cfg = ProfileConfig.model_validate(config).model_dump()
    current = get_profile(ctx, profile_id)
    with ctx.db.transaction() as conn:
        if current is None:
            conn.execute(
                "INSERT INTO policy_profiles(id,name_nl,description_nl,builtin,config_json,"
                "version,updated_at,updated_by) VALUES (?,?,?,0,?,1,?,?)",
                (profile_id, name_nl or profile_id, description_nl or "", dumps(cfg), utcnow(),
                 actor))
            version = 1
        else:
            version = int(current["version"]) + 1
            conn.execute(
                "UPDATE policy_profiles SET name_nl=?, description_nl=?, config_json=?, "
                "version=?, updated_at=?, updated_by=? WHERE id=?",
                (name_nl or current["name_nl"], description_nl
                 if description_nl is not None else current["description_nl"], dumps(cfg),
                 version, utcnow(), actor, profile_id))
        conn.execute(
            "INSERT INTO policy_profile_history(profile_id,version,config_json,changed_at,"
            "changed_by) VALUES (?,?,?,?,?)", (profile_id, version, dumps(cfg), utcnow(), actor))
    result = get_profile(ctx, profile_id)
    assert result is not None
    return result


def build_chain(*, instance_id: str | None = None, media_type: str | None = None,
                series_id: Any = None, movie_id: Any = None, season: Any = None,
                episode_id: Any = None, file_id: Any = None) -> dict[str, str]:
    chain: dict[str, str] = {}
    if media_type:
        chain["media_type"] = media_type
    if instance_id:
        chain["instance"] = instance_id
        if series_id is not None:
            chain["series"] = f"{instance_id}:{series_id}"
            if season is not None:
                chain["season"] = f"{instance_id}:{series_id}:{season}"
        if movie_id is not None:
            chain["movie"] = f"{instance_id}:{movie_id}"
        if episode_id is not None:
            chain["episode"] = f"{instance_id}:{episode_id}"
        if file_id is not None:
            chain["file"] = f"{instance_id}:{file_id}"
    return chain


def effective_policy(ctx: AppContext, chain: dict[str, str]) -> dict[str, Any]:
    """Profiel + scoped uitzonderingen → effectieve policy met herkomst."""
    sel = ctx.config.effective("policy.profile_id", chain)
    profile_id = sel["value"]
    origin = sel["source"]
    if profile_id == "auto":
        profile_id = MEDIA_TYPE_DEFAULTS.get(chain.get("media_type", ""), "standaard")
        origin = f"auto:{chain.get('media_type', 'onbekend')}"
    profile = get_profile(ctx, profile_id)
    if profile is None:
        profile = get_profile(ctx, "standaard")
        origin = f"ontbrekend profiel '{profile_id}', terugval standaard"
        assert profile is not None
    cfg = ProfileConfig.model_validate(profile["config"]).model_dump()
    trace: dict[str, Any] = {"profile_id": {"value": profile["id"], "source": origin}}

    nl = ctx.config.effective("audio.nl_audio_mode", chain)
    if nl["value"] != "profile":
        cfg["audio"]["nl_audio"] = nl["value"]
        trace["audio.nl_audio"] = {"value": nl["value"], "source": nl["source"]}
    subs = ctx.config.effective("audio.nl_subtitles_wanted", chain)
    if subs["value"] != "profile":
        cfg["audio"]["nl_subtitles_wanted"] = subs["value"] == "yes"
        trace["audio.nl_subtitles_wanted"] = {"value": subs["value"], "source": subs["source"]}
    pref = ctx.config.effective("audio.anime_preference", chain)
    if pref["value"] != "profile":
        cfg["audio"]["anime_preference"] = pref["value"]
        trace["audio.anime_preference"] = {"value": pref["value"], "source": pref["source"]}
    mode = ctx.config.effective("policy.ranking_mode", chain)
    if mode["value"] != "profile":
        lead = "audio" if mode["value"] == "audio_first" else "resolution"
        cfg["ranking_order"] = [lead] + [k for k in cfg["ranking_order"] if k != lead]
        trace["ranking_order"] = {"value": cfg["ranking_order"], "source": mode["source"]}
    return {"profile_id": profile["id"], "profile_name_nl": profile["name_nl"],
            "config": cfg, "trace": trace, "chain": chain}
