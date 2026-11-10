"""Versieverschil bron → doel onschadelijk maken (oude Sonarr v5/Radarr v7 → fork v4/v6).

De migratie loopt via de API. Een nieuwere bron kan velden, implementaties, kwaliteiten of
talen hebben die het (oudere) doel niet kent. Dat is geen fout van de gebruiker en ook geen
reden om de overstap tegen te houden:

- velden die het doel niet kent worden vooraf weggelaten (schema van het doel);
- weigert het doel toch een eigenschap (400/422 op een veld), dan wordt die eigenschap
  weggehaald en één keer opnieuw geprobeerd;
- wat daarna nog niet over te zetten is (onbekende implementatie, endpoint bestaat niet),
  wordt vastgelegd als *automatisch geaccepteerd verschil* met reden "bestaat niet in …".

Echte fouten (bijv. een geldige eigenschap met een foute waarde) blijven gewoon fouten.
"""

from __future__ import annotations

import re
from typing import Any

UNSUPPORTED_KIND = "unsupported"
# keuzewaarden die Sonarr v4 / Radarr v6 kennen; een nieuwere bron kan er meer hebben
MONITOR_NEW_ITEMS = {"all", "none"}
SERIES_TYPES = {"standard", "daily", "anime"}
MIN_AVAILABILITY = {"tba", "announced", "inCinemas", "released", "preDB"}


def known_enum(value: Any, allowed: set[str], default: str | None) -> str | None:
    """Een keuzewaarde die het doel niet kent, wordt de standaardwaarde."""
    if value is None or str(value) in allowed:
        return value
    return default
# sleutels die altijd mee mogen (identiteit/koppeling), ook als een sjabloon ze mist
ALWAYS_KEEP = {"name", "tags", "fields", "implementation", "configContract", "id",
               "implementationName", "protocol", "priority"}


def tgt_label(app: str, version: Any) -> str:
    name = {"sonarr": "Sonarr", "radarr": "Radarr"}.get(app, app)
    return f"{name} {version}" if version else f"deze {name}-versie"


def failing_properties(details: Any) -> list[str]:
    """Eigenschapsnamen uit een Sonarr/Radarr-validatiefout. Ondersteunt FluentValidation
    ({propertyName: 'Fields[3].Value'}) en System.Text.Json ('$.monitorNewItems',
    '$.items[2].quality.id')."""
    out: list[str] = []
    items = details if isinstance(details, list) else []
    if isinstance(details, dict):
        msg = str(details.get("message") or "")
        out += re.findall(r"\$\.([A-Za-z_][A-Za-z0-9_]*)", msg)
    for d in items:
        prop = str((d or {}).get("property") or "").strip()
        if not prop:
            continue
        prop = prop.removeprefix("$.").removeprefix("$")
        head = re.split(r"[.\[]", prop, maxsplit=1)[0]
        if head:
            out.append(head)
    seen: dict[str, None] = {}
    for p in out:
        seen.setdefault(p, None)
    return list(seen)


def strip_properties(body: dict[str, Any], props: list[str]) -> dict[str, Any]:
    """Haal eigenschappen weg (top-level, hoofdletterongevoelig) en provider-velden met die
    naam. Sleutels in ALWAYS_KEEP blijven staan."""
    low = {p.lower() for p in props}
    out = {k: v for k, v in body.items() if k.lower() not in low or k in ALWAYS_KEEP}
    if isinstance(out.get("fields"), list):
        out["fields"] = [f for f in out["fields"]
                         if str((f or {}).get("name", "")).lower() not in low]
    return out


def fit_to_template(body: dict[str, Any], template: dict[str, Any] | None) -> tuple[
        dict[str, Any], list[str]]:
    """Laat top-level sleutels weg die het doel niet heeft (``template`` = een bestaand
    object of schema-item van het doel). Geeft (nieuw body, weggelaten sleutels)."""
    if not template:
        return body, []
    dropped = [k for k in body if k not in template and k not in ALWAYS_KEEP]
    return {k: v for k, v in body.items() if k not in dropped}, dropped


def fit_provider(body: dict[str, Any], schema: list[dict[str, Any]] | None) -> tuple[
        dict[str, Any] | None, list[str]]:
    """Indexer/downloadclient/importlijst passend maken op het schema van het doel.
    (None, …) = implementatie bestaat niet in het doel."""
    if schema is None:
        return body, []
    impl = str(body.get("implementation") or "")
    tpl = next((s for s in schema if str(s.get("implementation")) == impl), None)
    if tpl is None:
        return None, [impl]
    out, dropped = fit_to_template(body, tpl)
    names = {str(f.get("name")) for f in tpl.get("fields") or []}
    if isinstance(out.get("fields"), list) and names:
        keep = [f for f in out["fields"] if str(f.get("name")) in names]
        dropped += [str(f.get("name")) for f in out["fields"] if f not in keep]
        out["fields"] = keep
    return out, dropped


def _quality_id(entry: dict[str, Any]) -> int | None:
    q = entry.get("quality")
    qid = q.get("id") if isinstance(q, dict) else q
    return int(qid) if isinstance(qid, int) or str(qid).lstrip("-").isdigit() else None


def fit_quality_items(items: list[dict[str, Any]], known: set[int]) -> tuple[
        list[dict[str, Any]], list[int]]:
    """Kwaliteiten die het doel niet kent uit een profiel halen (ook binnen groepen)."""
    dropped: list[int] = []
    out: list[dict[str, Any]] = []
    for entry in items:
        nested = entry.get("items") or []
        if nested:
            sub, d = fit_quality_items(nested, known)
            dropped += d
            if sub:
                out.append({**entry, "items": sub})
            continue
        qid = _quality_id(entry)
        if qid is not None and qid not in known:
            dropped.append(qid)
            continue
        out.append(entry)
    return out, dropped


def allowed_ids(items: list[dict[str, Any]]) -> list[int]:
    out: list[int] = []
    for entry in items:
        nested = entry.get("items") or []
        if nested:
            if entry.get("allowed"):
                out += [q for q in (_quality_id(s) for s in nested) if q is not None]
            continue
        qid = _quality_id(entry)
        if entry.get("allowed") and qid is not None:
            out.append(qid)
    return out


def fit_custom_format(cf: dict[str, Any], spec_schema: list[dict[str, Any]] | None,
                      languages: set[int] | None) -> tuple[dict[str, Any] | None, list[str]]:
    """Specificaties met een onbekende implementatie of taal weglaten. (None, …) als er
    niets bruikbaars overblijft."""
    specs = cf.get("specifications") or []
    impls = None if spec_schema is None else {str(s.get("implementation"))
                                              for s in spec_schema}
    keep: list[dict[str, Any]] = []
    dropped: list[str] = []
    for spec in specs:
        impl = str(spec.get("implementation") or "")
        if impls is not None and impl not in impls:
            dropped.append(str(spec.get("name") or impl))
            continue
        if languages is not None and impl == "LanguageSpecification":
            val = next((f.get("value") for f in spec.get("fields") or []
                        if f.get("name") == "value"), None)
            if isinstance(val, int) and val not in languages:
                dropped.append(str(spec.get("name") or impl))
                continue
        keep.append(spec)
    if specs and not keep:
        return None, dropped
    return {**cf, "specifications": keep}, dropped


def apply_unsupported(mismatches: list[dict[str, Any]],
                      entries: list[dict[str, Any]]) -> tuple[list[dict[str, Any]], list[str]]:
    """Verschillen die alleen bestaan omdat het doel iets niet kent, worden automatisch
    geaccepteerd. Elk overgeslagen onderdeel staat daarna als geaccepteerd verschil in het
    rapport (met de reden), zodat je kunt zien wat er niet mee is gegaan.
    Geeft (mismatches, ids die automatisch geaccepteerd zijn)."""
    import hashlib

    by_cat: dict[str, dict[str, str]] = {}
    for e in entries:
        by_cat.setdefault(str(e.get("category")), {})[str(e.get("key"))] = str(
            e.get("reason_nl") or "")
    auto: list[str] = []
    covered: set[tuple[str, str]] = set()
    out: list[dict[str, Any]] = []
    for m in mismatches:
        names = by_cat.get(str(m.get("category")), {})
        key = str(m.get("key"))
        if key in names and m.get("acceptable"):
            m = {**m, "auto_accepted": True,
                 "message_nl": f"{m['message_nl']} {names[key]}.".replace("..", "."),
                 "acceptable": True, "severity": "warning"}
            auto.append(m["id"])
            covered.add((m["category"], key))
        elif key == "*" and isinstance(m.get("source"), list) and names:
            # de weggevallen namen komen hieronder elk als geaccepteerd verschil terug
            rest = [n for n in m["source"] if str(n) not in names]
            if not rest:
                continue
            if len(rest) != len(m["source"]):
                m = {**m, "source": rest, "message_nl": m["message_nl"].split(":")[0]
                     + ": " + ", ".join(map(str, rest)) + "."}
        out.append(m)
    for cat, names in by_cat.items():
        for key, reason in names.items():
            if (cat, key) in covered:
                continue
            mid = "compat-" + hashlib.sha1(f"{cat}|{key}".encode()).hexdigest()[:10]
            out.append({"id": mid, "category": cat, "key": key, "field": "unsupported",
                        "source": key, "target": None, "severity": "warning",
                        "acceptable": True, "auto_accepted": True,
                        "message_nl": f"{key}: {reason}."})
            auto.append(mid)
    return out, auto
