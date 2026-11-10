"""Getypeerd instellingenregister met scope-overerving (UI-first contract, DEEL Q2.3).

Iedere operationele instelling wordt hier geregistreerd met type, standaardwaarde,
toegestane waarden, categorie en Nederlandse/Engelse labels. De UI rendert formulieren
rechtstreeks uit ``GET /api/v1/settings/schema``; daardoor heeft elke instelling
automatisch een UI-control (zie docs/UI_CONTROL_COVERAGE_MATRIX.md, gegenereerd).

Overerving (laagste wint als expliciet gezet):
    global -> media_type -> instance -> library -> profile -> series|movie -> season
    -> episode|file -> job
"""

from __future__ import annotations

import dataclasses
from collections.abc import Callable
from dataclasses import asdict, dataclass, field
from typing import Any, Literal

from mediaguard.db import Database, dumps, loads, utcnow

SettingType = Literal["bool", "int", "float", "string", "enum", "list", "secret", "duration",
                      "time_window", "json"]

SCOPE_ORDER: list[str] = [
    "global", "media_type", "instance", "library", "profile",
    "series", "movie", "season", "episode", "file", "job",
]


@dataclass(frozen=True)
class SettingDef:
    key: str
    type: SettingType
    default: Any
    category: str
    label_nl: str
    label_en: str
    help_nl: str = ""
    help_en: str = ""
    choices: tuple[str, ...] = ()
    choice_labels_nl: dict[str, str] = field(default_factory=dict)
    minimum: float | None = None
    maximum: float | None = None
    unit: str | None = None
    scopes: tuple[str, ...] = ("global",)
    advanced: bool = False
    # mag een gebruiker deze waarde uitzetten? Integriteitscontroles: nee (Q3)
    integrity_locked: bool = False
    restart_required: bool = False
    effect_nl: str = ""

    def validate(self, value: Any) -> Any:
        if self.integrity_locked:
            raise ValueError(f"{self.key}: integriteitscontrole is niet aanpasbaar")
        t = self.type
        if t == "bool":
            if not isinstance(value, bool):
                raise ValueError(f"{self.key}: verwacht ja/nee")
        elif t in ("int", "duration"):
            if isinstance(value, bool) or not isinstance(value, int):
                raise ValueError(f"{self.key}: verwacht een geheel getal")
            self._check_range(value)
        elif t == "float":
            if isinstance(value, bool) or not isinstance(value, int | float):
                raise ValueError(f"{self.key}: verwacht een getal")
            value = float(value)
            self._check_range(value)
        elif t == "string":
            if not isinstance(value, str) or len(value) > 4000:
                raise ValueError(f"{self.key}: verwacht tekst (max 4000 tekens)")
            check = FORMAT_VALIDATORS.get(self.key)
            if check is not None:
                value = check(value)
        elif t == "enum":
            if value not in self.choices:
                allowed = ", ".join(self.choices)
                raise ValueError(f"{self.key}: ongeldige keuze; toegestaan: {allowed}")
        elif t == "list":
            if not isinstance(value, list) or not all(isinstance(v, str) for v in value):
                raise ValueError(f"{self.key}: verwacht een lijst met tekst")
            if self.choices and any(v not in self.choices for v in value):
                raise ValueError(f"{self.key}: ongeldige waarde in lijst")
        elif t == "time_window":
            if not isinstance(value, dict) or not {"start", "end"} <= set(value):
                raise ValueError(f"{self.key}: verwacht {{start,end}} als HH:MM")
            for part in ("start", "end"):
                _parse_hhmm(value[part], self.key)
        elif t == "secret":
            raise ValueError(f"{self.key}: geheimen gaan via de secrets-API")
        return value

    def _check_range(self, value: float) -> None:
        if self.minimum is not None and value < self.minimum:
            raise ValueError(f"{self.key}: minimaal {self.minimum}")
        if self.maximum is not None and value > self.maximum:
            raise ValueError(f"{self.key}: maximaal {self.maximum}")

    def public(self) -> dict[str, Any]:
        data = asdict(self)
        data["choices"] = list(self.choices)
        data["scopes"] = list(self.scopes)
        return data


def _parse_hhmm(value: Any, key: str) -> None:
    if not isinstance(value, str) or len(value) != 5 or value[2] != ":":
        raise ValueError(f"{key}: tijd als HH:MM")
    hh, mm = int(value[:2]), int(value[3:])
    if not (0 <= hh < 24 and 0 <= mm < 60):
        raise ValueError(f"{key}: ongeldige tijd")


# Extra formaatcontrole voor tekstinstellingen (sleutel → functie(waarde) → genormaliseerde
# waarde; ValueError bij een ongeldige waarde). Buiten SettingDef, zodat het schema (public())
# en de API-antwoorden precies dezelfde vorm houden.
FORMAT_VALIDATORS: dict[str, Callable[[str], str]] = {}


class SettingsRegistry:
    def __init__(self) -> None:
        self._defs: dict[str, SettingDef] = {}

    def register(self, *defs: SettingDef) -> None:
        from mediaguard.settings_help import HELP_NL

        for d in defs:
            if not (d.help_nl or "").strip() and d.key in HELP_NL:
                # rapport 5: elke instelling met Nederlandse uitleg (centrale aanvulling)
                d = dataclasses.replace(d, help_nl=HELP_NL[d.key])
            if d.key in self._defs and self._defs[d.key] != d:
                raise ValueError(f"instelling dubbel geregistreerd: {d.key}")
            for scope in d.scopes:
                if scope not in SCOPE_ORDER:
                    raise ValueError(f"{d.key}: onbekende scope {scope}")
            self._defs[d.key] = d

    def get_def(self, key: str) -> SettingDef:
        try:
            return self._defs[key]
        except KeyError as exc:
            raise KeyError(f"onbekende instelling: {key}") from exc

    def all(self) -> list[SettingDef]:
        return sorted(self._defs.values(), key=lambda d: (d.category, d.key))


REGISTRY = SettingsRegistry()


class SettingsService:
    """Lees/schrijf scoped waarden en bereken de effectieve waarde met herkomst."""

    # Gedeelde database met een tweede proces (recovery): wijzigingen van daar zijn
    # hooguit zo lang onzichtbaar. Lokale set/reset leegt de cache direct.
    CACHE_TTL_SECONDS = 3.0

    def __init__(self, db: Database, registry: SettingsRegistry = REGISTRY) -> None:
        self.db = db
        self.registry = registry
        self._cache: dict[tuple[str, str, str], Any] | None = None
        self._cache_at = 0.0

    def invalidate(self) -> None:
        """Na directe SQL-wijzigingen in settings_values (bv. migratiewizard)."""
        self._cache = None

    def _snapshot(self) -> dict[tuple[str, str, str], Any]:
        import time

        now = time.monotonic()
        cache = self._cache
        if cache is None or now - self._cache_at > self.CACHE_TTL_SECONDS:
            cache = {(r["scope_type"], r["scope_id"], r["key"]): loads(r["value_json"])
                     for r in self.db.fetch_all(
                         "SELECT scope_type, scope_id, key, value_json FROM settings_values")}
            self._cache, self._cache_at = cache, now
        return cache

    def set(self, scope_type: str, scope_id: str, key: str, value: Any, actor: str) -> Any:
        d = self.registry.get_def(key)
        if scope_type not in d.scopes:
            raise ValueError(f"{key} kan niet op scope '{scope_type}' worden ingesteld")
        value = d.validate(value)
        self.db.execute(
            "INSERT INTO settings_values(scope_type,scope_id,key,value_json,updated_at,updated_by)"
            " VALUES (?,?,?,?,?,?) ON CONFLICT(scope_type,scope_id,key) DO UPDATE SET "
            "value_json=excluded.value_json, updated_at=excluded.updated_at, "
            "updated_by=excluded.updated_by",
            (scope_type, scope_id or "", key, dumps(value), utcnow(), actor),
        )
        self.invalidate()
        return value

    def reset(self, scope_type: str, scope_id: str, key: str) -> None:
        self.registry.get_def(key)
        self.db.execute(
            "DELETE FROM settings_values WHERE scope_type=? AND scope_id=? AND key=?",
            (scope_type, scope_id or "", key),
        )
        self.invalidate()

    def raw(self, scope_type: str, scope_id: str, key: str) -> Any | None:
        import copy

        value = self._snapshot().get((scope_type, scope_id or "", key))
        # kopie: aanroepers mogen lijsten/dicts aanpassen zonder de cache te vervuilen
        return copy.deepcopy(value) if isinstance(value, dict | list) else value

    def effective(self, key: str, chain: dict[str, str] | None = None) -> dict[str, Any]:
        """chain: {scope_type: scope_id} voor de context, bv. {"instance": "sonarr-anime",
        "series": "sonarr-anime:42"}. Geeft waarde + herkomst + volledige overervingsketen."""
        d = self.registry.get_def(key)
        chain = dict(chain or {})
        chain.setdefault("global", "")
        trace: list[dict[str, Any]] = []
        value: Any = d.default
        source = "default"
        for scope in SCOPE_ORDER:
            if scope not in chain or scope not in d.scopes:
                continue
            raw = self.raw(scope, chain[scope], key)
            trace.append({"scope": scope, "scope_id": chain[scope], "value": raw,
                          "set": raw is not None})
            if raw is not None:
                value, source = raw, scope
        if source == "global" and value == d.default:
            # globaal opgeslagen maar gelijk aan de standaard: geen afwijking tonen (R17)
            source = "default"
        return {"key": key, "value": value, "source": source, "default": d.default,
                "trace": trace}

    def value(self, key: str, chain: dict[str, str] | None = None) -> Any:
        """Snelle variant van ``effective`` zonder herkomst-trace (heet pad: tienduizenden
        aanroepen per backfill-/autopilotronde)."""
        d = self.registry.get_def(key)
        snap = self._snapshot()
        value: Any = d.default
        chain = chain or {}
        for scope in SCOPE_ORDER:
            if scope not in d.scopes:
                continue
            sid = "" if scope == "global" else chain.get(scope)
            if sid is None:
                continue
            raw = snap.get((scope, sid or "", key))
            if raw is not None:
                value = raw
        import copy

        return copy.deepcopy(value) if isinstance(value, dict | list) else value

    def overrides(self, scope_type: str | None = None,
                  scope_id: str | None = None) -> list[dict[str, Any]]:
        sql = "SELECT * FROM settings_values"
        params: list[Any] = []
        clauses = []
        if scope_type:
            clauses.append("scope_type=?")
            params.append(scope_type)
        if scope_id is not None:
            clauses.append("scope_id=?")
            params.append(scope_id)
        if clauses:
            sql += " WHERE " + " AND ".join(clauses)
        rows = self.db.fetch_dicts(sql + " ORDER BY key", params)
        for row in rows:
            row["value"] = loads(row.pop("value_json"))
        return rows
