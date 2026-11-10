"""TRaSH-Guides custom formats als *scorebasis*. Scores overrulen nooit harde regels
(identiteit, bestandsveiligheid, vereiste audio): zij bepalen alleen de laatste
rangschikkingssleutel ``custom_format``.

Ondersteunt zowel het TRaSH-JSON-formaat (``fields: {"value": …}``, ``trash_scores``) als
het Sonarr/Radarr API-formaat (``fields: [{"name": "value", "value": …}]``).
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Any
from urllib.parse import urlparse

import httpx

from mediaguard.db import dumps, loads, utcnow
from mediaguard.identity.parser import ParsedRelease

if TYPE_CHECKING:
    from mediaguard.context import AppContext

MAX_PATTERN = 500
MAX_FORMATS = 2000
# Sonarr-taal-ID's (Language enum) → ISO 639-1
SONARR_LANG = {1: "en", 2: "fr", 3: "es", 4: "de", 5: "it", 6: "da", 7: "nl", 8: "ja",
               10: "ru", 11: "pl", 13: "fi", 14: "sv", 15: "no", 17: "tr", 18: "pt",
               21: "ko", 22: "zh"}
# Sonarr QualitySource → parser-bron
SONARR_SOURCE = {1: "hdtv", 2: "hdtv", 3: "webdl", 4: "webrip", 5: "dvd", 6: "bluray",
                 7: "remux"}


@dataclass
class Spec:
    implementation: str
    negate: bool
    required: bool
    value: Any
    regex: re.Pattern[str] | None = None


@dataclass
class CustomFormat:
    name: str
    specs: list[Spec]
    scores: dict[str, int] = field(default_factory=dict)
    errors: list[str] = field(default_factory=list)


def _field_value(fields: Any) -> Any:
    if isinstance(fields, dict):
        return fields.get("value")
    if isinstance(fields, list):
        for f in fields:
            if isinstance(f, dict) and f.get("name") == "value":
                return f.get("value")
    return None


def compile_format(data: dict[str, Any]) -> CustomFormat:
    name = str(data.get("name") or data.get("trash_id") or "naamloos")[:200]
    cf = CustomFormat(name=name, specs=[])
    scores = data.get("trash_scores") or {}
    if "score" in data and isinstance(data["score"], int):
        scores = {"default": data["score"], **scores}
    cf.scores = {str(k): int(v) for k, v in scores.items() if isinstance(v, int | float)}
    for spec in data.get("specifications") or []:
        impl = str(spec.get("implementation") or "")
        value = _field_value(spec.get("fields"))
        s = Spec(impl, bool(spec.get("negate")), bool(spec.get("required")), value)
        if impl in ("ReleaseTitleSpecification", "ReleaseGroupSpecification"):
            pattern = str(value or "")
            if len(pattern) > MAX_PATTERN:
                cf.errors.append(f"{spec.get('name')}: patroon te lang")
                continue
            try:
                s.regex = re.compile(pattern, re.I)
            except re.error as exc:
                cf.errors.append(f"{spec.get('name')}: ongeldige regex ({exc})")
                continue
        cf.specs.append(s)
    return cf


def _spec_match(s: Spec, title: str, parsed: ParsedRelease, languages: list[str]) -> bool:
    impl = s.implementation
    if impl == "ReleaseTitleSpecification" and s.regex:
        hit = bool(s.regex.search(title))
    elif impl == "ReleaseGroupSpecification" and s.regex:
        hit = bool(parsed.release_group and s.regex.search(parsed.release_group))
    elif impl == "LanguageSpecification":
        lang = SONARR_LANG.get(int(s.value)) if isinstance(s.value, int) else None
        hit = lang is not None and lang in languages
    elif impl == "SourceSpecification":
        src = SONARR_SOURCE.get(int(s.value)) if isinstance(s.value, int) else None
        hit = src is not None and src == parsed.source
    elif impl == "ResolutionSpecification":
        hit = isinstance(s.value, int) and parsed.resolution == s.value
    else:
        return False  # niet-ondersteunde specificatie matcht nooit
    return hit != s.negate


def matches(cf: CustomFormat, title: str, parsed: ParsedRelease, languages: list[str]) -> bool:
    """Sonarr-semantiek: alle 'required' specs moeten matchen; van de overige moet per
    implementatietype minstens één matchen."""
    if not cf.specs:
        return False
    groups: dict[str, list[bool]] = {}
    for s in cf.specs:
        hit = _spec_match(s, title, parsed, languages)
        if s.required and not hit:
            return False
        if not s.required:
            groups.setdefault(s.implementation, []).append(hit)
    return all(any(v) for v in groups.values())


def load_formats(ctx: AppContext) -> list[CustomFormat]:
    rows = ctx.db.fetch_all("SELECT spec_json, scores_json FROM trash_custom_formats")
    out = []
    for r in rows:
        cf = compile_format(loads(r["spec_json"], {}))
        cf.scores.update(loads(r["scores_json"], {}))
        out.append(cf)
    return out


def score(formats: list[CustomFormat], title: str, parsed: ParsedRelease, languages: list[str],
          score_set: str = "default") -> tuple[int, list[str]]:
    total, names = 0, []
    for cf in formats:
        if matches(cf, title, parsed, languages):
            total += cf.scores.get(score_set, cf.scores.get("default", 0))
            names.append(cf.name)
    return total, names


def import_formats(ctx: AppContext, items: list[dict[str, Any]], *, source: str, actor: str
                   ) -> dict[str, Any]:
    if len(items) > MAX_FORMATS:
        raise ValueError(f"maximaal {MAX_FORMATS} custom formats per import")
    imported, errors = 0, []
    for item in items:
        if not isinstance(item, dict) or not item.get("name"):
            errors.append("item zonder naam overgeslagen")
            continue
        cf = compile_format(item)
        errors.extend(f"{cf.name}: {e}" for e in cf.errors)
        ctx.db.execute(
            "INSERT INTO trash_custom_formats(trash_id,name,spec_json,scores_json,source,"
            "imported_at,imported_by) VALUES (?,?,?,?,?,?,?) ON CONFLICT(name) DO UPDATE SET "
            "trash_id=excluded.trash_id, spec_json=excluded.spec_json, "
            "scores_json=excluded.scores_json, source=excluded.source, "
            "imported_at=excluded.imported_at, imported_by=excluded.imported_by",
            (item.get("trash_id"), cf.name, dumps(item), dumps(cf.scores), source, utcnow(),
             actor))
        imported += 1
    return {"imported": imported, "errors": errors}


async def fetch_formats(url: str, *, transport: httpx.AsyncBaseTransport | None = None
                        ) -> list[dict[str, Any]]:
    parsed = urlparse(url)
    if parsed.scheme != "https" or not parsed.netloc:
        raise ValueError("alleen https-URL's zijn toegestaan")
    async with httpx.AsyncClient(timeout=15.0, transport=transport,
                                 follow_redirects=True) as client:
        resp = await client.get(url)
        resp.raise_for_status()
        if len(resp.content) > 10 * 1024 * 1024:
            raise ValueError("bestand te groot (max 10 MB)")
        data = resp.json()
    if isinstance(data, dict):
        data = data.get("custom_formats") or data.get("customFormats") or [data]
    if not isinstance(data, list):
        raise ValueError("verwacht een lijst met custom formats")
    return [d for d in data if isinstance(d, dict)]
