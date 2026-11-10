#!/usr/bin/env python3
"""Genereert docs/design/2026-11-feature-inventory.md: elke route, tab, filter, deep link, knop,
dialoog en instelling van de web-UI met zijn plek in de nieuwe opzet (november 2026).

Gebruik (met de core-venv):  python scripts/gen-feature-inventory.py
De lijsten komen uit de broncode (apps/web/app, apps/web/components) en uit het
instellingenregister van core; alleen de koppeling oud → nieuw staat hieronder met de hand.
apps/web/e2e/werk-ia.spec.ts gebruikt dezelfde tabel (via docs/design/ia-map.json) om te
controleren dat elke oude route naar de juiste plek leidt.
"""

from __future__ import annotations

import ast
import json
import re
import sys
from collections import defaultdict
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
WEB = ROOT / "apps" / "web"
sys.path.insert(0, str(ROOT / "apps" / "core"))

# ------------------------------------------------------------------ nieuwe opzet (handmatig)
# Vijf hoofdonderdelen + Instellingen. Per oude route: (nieuwe plek, sectie/anker, gedrag)
# gedrag: "blijft" = zelfde adres, "omleiding" = oude adres stuurt door naar 'doel'.
AREAS = ["Overzicht (/)", "Activiteit (/activiteit)", "Bibliotheek (/bibliotheek)",
         "Ruimte (/av1)", "Aanvragen (/aanvragen)", "Instellingen (/instellingen)"]

ROUTES: dict[str, tuple[str, str, str]] = {
    "/": ("Overzicht", "—", "blijft"),
    "/activiteit": ("Activiteit", "filterchips: Nu · Problemen · Downloads · Wat is er gebeurd · Geschiedenis", "blijft"),
    "/aanvragen": ("Aanvragen", "Zoeken & ontdekken · Mijn aanvragen · Levering", "nieuw"),
    "/bibliotheek": ("Bibliotheek", "Series · Anime · Films (+ Ontbrekend, Kalender als chips)", "blijft"),
    "/bibliotheek/[instance]/[id]": ("Bibliotheek › titel", "Seizoenen (aan/uit) · Kwaliteit · Upgrades", "blijft"),
    "/bibliotheek/[instance]/[id]/aflevering/[episode]": ("Bibliotheek › aflevering", "Kwaliteit · Upgrades · Video en audio", "blijft"),
    "/ontbrekend": ("Bibliotheek", "chip Ontbrekend", "blijft"),
    "/kalender": ("Bibliotheek", "chip Kalender", "blijft"),
    "/av1": ("Ruimte", "tab Besparing (+ sectie #opslag)", "blijft"),
    "/av1/wachtrij": ("Ruimte", "tab Wachtrij", "blijft"),
    "/av1/kwaliteit": ("Ruimte", "tab Kwaliteit", "blijft"),
    "/quarantaine": ("Ruimte", "tab Terugzetten", "blijft"),
    "/av1/instellingen": ("Instellingen › AV1-engine", "via Instellingen#av1 en knop 'Instellingen' op Ruimte", "blijft"),
    "/instellingen": ("Instellingen", "één pagina: secties + inhoudsopgave + zoeken", "blijft"),
    "/instellingen/instanties": ("Instellingen › Instanties", "Instellingen#koppelingen", "blijft"),
    "/inrichten": ("Instellingen › Koppelingen", "Instellingen#koppelingen (eigen tabrij)", "blijft"),
    "/kwaliteit": ("Instellingen › Kwaliteit & profielen", "Instellingen#downloads", "blijft"),
    "/systeem": ("Instellingen › Systeem", "Instellingen#systeem", "blijft"),
    "/instellingen/meldingen": ("Instellingen › Meldingen", "Instellingen#meldingen", "blijft"),
    "/instellingen/gebruikers": ("Instellingen › Gebruikers", "Instellingen#beveiliging", "blijft"),
    "/instellingen/api-sleutels": ("Instellingen › API-sleutels & app", "Instellingen#beveiliging", "blijft"),
    "/instellingen/geheimen": ("Instellingen › Geheimen", "Instellingen#beveiliging", "blijft"),
    "/instellingen/beveiliging": ("Instellingen › Beveiliging", "Instellingen#beveiliging", "blijft"),
    "/instellingen/audit": ("Activiteit", "Geschiedenis › Audit", "omleiding"),
    "/instellingen/gevorderd": ("Instellingen", "#gereedschap", "omleiding"),
    "/release-lab": ("Instellingen › Release Lab", "Instellingen#gereedschap", "blijft"),
    "/anime": ("Instellingen › Anime-routering", "Instellingen#gereedschap", "blijft"),
    "/dolby-vision": ("Instellingen › Dolby Vision", "Instellingen#av1", "blijft"),
    "/ai": ("Instellingen › AI-adviseur", "Instellingen#gereedschap", "blijft"),
    "/audio": ("Instellingen › Audio & ondertiteling", "Instellingen#taal", "blijft"),
    "/downloads": ("Instellingen › Downloadclients", "Instellingen#koppelingen", "blijft"),
    "/seerr": ("Instellingen › Seerr", "Instellingen#koppelingen", "blijft"),
    "/migratie": ("Instellingen › Migratie", "Instellingen#gereedschap (+ Overzicht zolang er een migratie loopt)", "blijft"),
    "/hoe-werkt-het": ("Hulp", "vraagteken in de kopbalk · Instellingen#gereedschap", "blijft"),
    "/jobs": ("Activiteit", "Geschiedenis", "omleiding"),
    "/login": ("Inloggen", "—", "blijft"),
    "/setup": ("Eerste installatie", "—", "blijft"),
}

# Oude deep links met parameters → doel (getest in werk-ia.spec.ts)
DEEP_LINKS: list[tuple[str, str, str]] = [
    ("/activiteit?tab=aanvragen", "/aanvragen", "Aanvragen is een hoofdonderdeel"),
    ("/activiteit?tab=opslag", "/av1#opslag", "Opslag & downloads hoort bij Ruimte"),
    ("/activiteit?tab=opslag&periode=7d", "/av1?periode=7d#opslag", "periode blijft behouden"),
    ("/activiteit?tab=problemen", "/activiteit?tab=problemen", "blijft (chip)"),
    ("/activiteit?tab=geschiedenis&view=audit", "/activiteit?tab=geschiedenis&view=audit", "blijft"),
    ("/av1/wachtrij?job=1", "/av1/wachtrij?job=1", "blijft (lade)"),
    ("/av1/wachtrij?status=mislukt", "/av1/wachtrij?status=mislukt", "blijft"),
    ("/av1/fouten", "/av1/wachtrij?status=mislukt", "bestaande omleiding"),
    ("/av1/logs?job=1", "/av1/wachtrij?job=1", "bestaande omleiding"),
    ("/av1/profielen", "/av1/instellingen#profielen", "bestaande omleiding"),
    ("/av1/workers", "/av1/instellingen#workers", "bestaande omleiding"),
    ("/av1/ai", "/ai", "bestaande omleiding"),
    ("/av1/beslissingen", "/av1/wachtrij", "bestaande omleiding"),
    ("/av1/bibliotheken", "/av1/instellingen", "bestaande omleiding"),
    ("/av1/encoder-instellingen", "/av1/instellingen", "bestaande omleiding"),
    ("/instellingen/gevorderd", "/instellingen#gereedschap", "Gevorderd wordt sectie Gereedschap"),
    ("/instellingen?tab=opslag", "/systeem#opslag", "bestaande omleiding"),
    ("/inrichten?tab=seerr", "/inrichten?tab=seerr", "blijft"),
    ("/kwaliteit?tab=optimaliseren", "/kwaliteit?tab=optimaliseren", "blijft"),
    ("/systeem#quarantaine-wezen", "/systeem#quarantaine-wezen", "blijft"),
    ("/ontbrekend?reason_code=import-failed", "/ontbrekend?reason_code=import-failed", "blijft"),
    ("/migratie?run=1", "/migratie?run=1", "blijft"),
]

# Configuratie die niet in het instellingenregister staat maar wel in de UI
OTHER_CONFIG: list[tuple[str, str, str]] = [
    ("Sonarr/Radarr-instanties (URL, API-sleutel, schaduwmodus)", "/instellingen/instanties", "Instellingen#koppelingen"),
    ("Downloadclients, indexers, paden & categorieën, media & kwaliteit, Seerr, hernoemen", "/inrichten", "Instellingen#koppelingen"),
    ("Mediaserver (Emby/Jellyfin/Plex) & ondertitels (Bazarr/OpenSubtitles)", "/inrichten?tab=mediaserver", "Instellingen#koppelingen"),
    ("Kwaliteitsprofielen, bibliotheek- en Sonarr/Radarr-optimalisatie", "/kwaliteit", "Instellingen#downloads"),
    ("Meldingskanalen (ntfy, Pushover, Telegram, Discord, Slack, webhook, e-mail), categorieën, dagoverzicht, opslagmeldingen", "/instellingen/meldingen", "Instellingen#meldingen"),
    ("Tweestapsverificatie, sessies, inloggeschiedenis", "/instellingen/beveiliging", "Instellingen#beveiliging"),
    ("Gebruikers en rollen", "/instellingen/gebruikers", "Instellingen#beveiliging"),
    ("API-sleutels (o.a. Media Source Optimizer) en Android-app koppelen", "/instellingen/api-sleutels", "Instellingen#beveiliging"),
    ("Geheimen (wachtwoorden, sleutels)", "/instellingen/geheimen", "Instellingen#beveiliging"),
    ("Back-ups, herstelbundel, herstelcode, externe kopie", "/systeem", "Instellingen#systeem"),
    ("Opslag, tweelingmappen, weesbestanden, opruimer downloads", "/systeem#opslag", "Instellingen#systeem"),
    ("Systeem & prestaties (CPU, GPU, vensters)", "/systeem", "Instellingen#systeem"),
    ("AV1-engine: profielen, banen, afstelling (uitvoering), GPU-plekken meten", "/av1/instellingen", "Instellingen#av1"),
    ("Dolby Vision-beleid en uitzonderingen per titel", "/dolby-vision", "Instellingen#av1"),
    ("Quarantaine-/vangnetmodus, 'Quarantaine gebruiken'", "/quarantaine", "Ruimte › Terugzetten (+ Instellingen#originelen)"),
    ("AI-adviseur: provider, sleutel, toestemming, budget, cases", "/ai", "Instellingen#gereedschap"),
    ("Anime-routering en keuzes", "/anime", "Instellingen#gereedschap"),
    ("Release Lab (waarom deze release?)", "/release-lab", "Instellingen#gereedschap"),
    ("Migratie-wizard, scan, overschakelen, terugdraaien", "/migratie", "Instellingen#gereedschap"),
    ("Noodstop", "profielmenu", "profielmenu + Instellingen#systeem"),
    ("Thema, technische details tonen", "profielmenu", "profielmenu + Instellingen#algemeen"),
]


# ------------------------------------------------------------------ broncode-scan
def messages() -> dict[str, str]:
    out: dict[str, str] = {}
    for f in (WEB / "lib" / "i18n" / "messages").glob("*.ts"):
        for m in re.finditer(r'"([a-zA-Z0-9_.-]+)":\s*"((?:[^"\\]|\\.)*)"', f.read_text(encoding="utf-8")):
            out.setdefault(m.group(1), m.group(2))
    return out


IMPORT_RE = re.compile(r'from\s+"@/components/([^"]+)"')


def resolve(spec: str) -> Path | None:
    base = WEB / "components" / spec
    for cand in (base.with_suffix(".tsx"), base.with_suffix(".ts"), base / "index.tsx"):
        if cand.exists():
            return cand
    return None


def tree(page: Path, depth: int = 3) -> list[Path]:
    seen: list[Path] = [page]
    frontier = [page]
    shared = {"mg/primitives", "mg/states", "mg/domain", "shell/app-shell", "mg/term"}
    for _ in range(depth):
        nxt = []
        for f in frontier:
            for spec in IMPORT_RE.findall(f.read_text(encoding="utf-8")):
                if spec in shared:
                    continue
                p = resolve(spec)
                if p and p not in seen:
                    seen.append(p)
                    nxt.append(p)
        frontier = nxt
    return seen


def scan(files: list[Path], msg: dict[str, str]) -> dict[str, set[str]]:
    out: dict[str, set[str]] = defaultdict(set)
    for f in files:
        src = f.read_text(encoding="utf-8")
        for m in re.finditer(r'\.get\("([a-z_]+)"\)', src):
            out["params"].add(m.group(1))
        for m in re.finditer(r'\bid="([a-z][a-z0-9-]*)"', src):
            out["anchors"].add("#" + m.group(1))
        for m in re.finditer(r'[?&]tab=([a-z-]+)', src):
            out["tabs"].add(m.group(1))
        for m in re.finditer(r'\{\s*value:\s*"([a-z0-9_-]+)"', src):
            out["tabs"].add(m.group(1))
        for line in src.splitlines():
            keys = re.findall(r'\bt\("([a-zA-Z0-9_.-]+)"', line)
            if not keys:
                continue
            kind = ("actions" if re.search(r"<Button|<LinkButton|<button|onClick", line) else
                    "dialogs" if re.search(r"Dialog|<Modal|confirm", line) else
                    "cards" if re.search(r"<Card|title=", line) else None)
            if kind:
                for k in keys:
                    out[kind].add(msg.get(k, k))
    return out


def route_of(page: Path) -> str:
    rel = page.relative_to(WEB / "app").parent.as_posix()
    rel = re.sub(r"\(app\)/?", "", rel)
    return "/" + rel if rel and rel != "." else "/"


def redirect_target(page: Path) -> str | None:
    src = page.read_text(encoding="utf-8")
    m = re.search(r'redirect\(\s*[`"]([^`"]+)[`"]', src)
    if m:
        return m.group(1)
    return "(afhankelijk van parameters)" if re.search(r"\bredirect\(", src) else None


# Categorieën die de curatie (core) nog niet aan een sectie koppelt; de nieuwe Instellingen-pagina
# (apps/web/lib/settings-sections.ts) gebruikt dezelfde koppeling.
EXTRA_SECTIONS = {"meldingen": "Meldingen", "koppelingen": "Koppelingen",
                  "beveiliging": "Beveiliging & toegang", "systeem": "Systeem",
                  "gereedschap": "Gereedschap"}
# gelijk aan EXTRA_CAT in apps/web/app/(app)/instellingen/page.tsx: categorieën die de core onder
# 'algemeen' laat vallen, krijgen op de instellingenpagina hun eigen sectie
EXTRA_CAT = {"Meldingen": "meldingen", "Downloads opruimen": "downloads", "Downloaden": "downloads",
             "Aanvragen": "downloads", "Mediaserver (Emby)": "koppelingen", "Koppelingen": "koppelingen",
             "Seerr": "koppelingen", "Instanties": "koppelingen", "Inrichten": "koppelingen",
             "Ondertitels": "taal", "Onderhoud": "systeem", "Back-ups": "systeem", "Migratie": "systeem",
             "Bibliotheek": "systeem", "Beveiliging": "beveiliging", "AI Adviseur": "gereedschap"}


def settings_rows() -> tuple[list[tuple[str, str, str, str]], list[str]]:
    from mediaguard.app import _load_modules
    from mediaguard.settings_curation import SECTIONS
    from mediaguard.settings_registry import REGISTRY

    _load_modules()
    from mediaguard.settings_curation import section_of

    titles = {sid: nl for sid, nl, *_ in SECTIONS} | EXTRA_SECTIONS
    known = {sid for sid, *_ in SECTIONS}
    rows, homeless = [], []
    for d in sorted(REGISTRY.all(), key=lambda d: d.key):
        # zelfde regel als sectionOf() op de instellingenpagina
        sid = section_of(d.key, d.category)
        if sid == "prestaties":
            sid = "systeem"
        elif sid == "algemeen":
            sid = EXTRA_CAT.get(d.category, "algemeen")
            if d.category not in EXTRA_CAT and d.category not in {c for *_x, cats in SECTIONS for c in cats}:
                homeless.append(d.key)
        if sid not in known and sid not in EXTRA_SECTIONS:
            homeless.append(d.key)
            sid = "algemeen"
        rows.append((d.key, d.label_nl, d.category, f"Instellingen#{sid} ({titles[sid]})"))
    return rows, homeless


def encoder_knobs() -> list[tuple[str, str]]:
    src = (ROOT / "services" / "encoder" / "app" / "afstelling.py").read_text(encoding="utf-8")
    out = []
    for node in ast.walk(ast.parse(src)):
        if isinstance(node, ast.Call) and getattr(node.func, "id", None) == "Knob":
            a = [x.value if isinstance(x, ast.Constant) else None for x in node.args]
            out.append((str(a[0]), str(a[6])))
    return out


def main() -> int:
    msg = messages()
    pages = sorted((WEB / "app").rglob("page.tsx"))
    routes = []
    for p in pages:
        r = route_of(p)
        target = redirect_target(p)
        feats = scan(tree(p), msg) if not target else {}
        routes.append((r, p.relative_to(ROOT).as_posix(), target, feats))
    srows, homeless = settings_rows()
    knobs = encoder_knobs()

    unmapped = [r for r, _f, t, _x in routes if r not in ROUTES and not t]
    L: list[str] = []
    L += ["# Functie-inventaris en plek in de nieuwe opzet (november 2026)", "",
          "Gegenereerd met `scripts/gen-feature-inventory.py` uit de broncode en het "
          "instellingenregister. Handmatig is alleen de koppeling oud → nieuw "
          "(`ROUTES`, `DEEP_LINKS`, `OTHER_CONFIG` in het script). Afgedwongen door "
          "`apps/web/e2e/werk-ia.spec.ts` (elke route en deep link opent zonder 404 op de juiste "
          "plek) en `apps/web/e2e/werk-instellingen-dekking.spec.ts` (elke instelling staat op "
          "Instellingen).", "",
          "## Samenvatting", "",
          f"- Hoofdonderdelen: {' · '.join(AREAS)}",
          f"- Routes (page.tsx): **{len(routes)}**, waarvan {sum(1 for r in routes if r[2])} "
          "bestaande omleidingen",
          f"- Deep links met parameters/ankers: **{len(DEEP_LINKS)}**",
          f"- Instellingen in het register: **{len(srows)}**; zonder sectie: **{len(homeless)}**",
          f"- Encoder-afstelling: **{len(knobs)}** knoppen",
          f"- Overige configuratieschermen: **{len(OTHER_CONFIG)}**",
          f"- Routes zonder plek in de nieuwe opzet: **{len(unmapped)}**"
          + (f" ({', '.join(unmapped)})" if unmapped else ""),
          "- Bewust verwijderd: **geen**", ""]
    L += ["## Routes", "", "| Route | Bestand | Nu | Nieuwe plek | Sectie / anker | Oud adres |",
          "|---|---|---|---|---|---|"]
    for r, f, target, _feats in routes:
        new = ROUTES.get(r, ("Omleiding" if target else "⚠ GEEN PLEK", "", "omleiding" if target else ""))
        now = f"omleiding → `{target}`" if target else "pagina"
        L.append(f"| `{r}` | `{f}` | {now} | {new[0]} | {new[1]} | {new[2]} |")
    L += ["", "## Deep links", "", "| Oud adres | Leidt naar | Toelichting |", "|---|---|---|"]
    L += [f"| `{a}` | `{b}` | {c} |" for a, b, c in DEEP_LINKS]
    L += ["", "## Per pagina: tabs, parameters, ankers, knoppen, dialogen, kaarten", "",
          "Automatisch uit de pagina en de componenten die hij gebruikt (3 niveaus diep). "
          "Elk onderdeel verhuist mee met de pagina naar de plek in de routetabel.", ""]
    for r, _f, target, feats in routes:
        if target:
            continue
        L.append(f"### `{r}` → {ROUTES.get(r, ('⚠',))[0]}")
        for kind, label in (("tabs", "Tabs/filters"), ("params", "Queryparameters"),
                            ("anchors", "Ankers"), ("actions", "Knoppen/acties"),
                            ("dialogs", "Dialogen/bevestigingen"), ("cards", "Kaarten/blokken")):
            vals = sorted(feats.get(kind, set()))
            if vals:
                L.append(f"- **{label}** ({len(vals)}): " + " · ".join(v.replace("|", "/") for v in vals))
        L.append("")
    L += ["## Instellingen (register)", "", "Allemaal op `/instellingen`, in de sectie hieronder; "
          "de geavanceerde instellingen per sectie staan achter 'Geavanceerd tonen'.", "",
          "| Key | Label | Categorie | Nieuwe plek |", "|---|---|---|---|"]
    L += [f"| `{k}` | {lab.replace('|', '/')} | {cat} | {where} |" for k, lab, cat, where in srows]
    L += ["", "## Encoder-afstelling", "", "| Knop | Label | Nieuwe plek |", "|---|---|---|"]
    L += [f"| `encoder.{k}` | {lab} | Instellingen#av1 › AV1-engine (`/av1/instellingen`, Uitvoering) |"
          for k, lab in knobs]
    L += ["", "## Overige configuratie", "", "| Wat | Scherm | Nieuwe plek |", "|---|---|---|"]
    L += [f"| {a} | `{b}` | {c} |" for a, b, c in OTHER_CONFIG]
    (ROOT / "docs" / "design" / "2026-11-feature-inventory.md").write_text("\n".join(L) + "\n", encoding="utf-8")
    (ROOT / "docs" / "design" / "ia-map.json").write_text(json.dumps({
        "routes": {r: {"area": v[0], "where": v[1], "behaviour": v[2]} for r, v in ROUTES.items()},
        "deep_links": [{"from": a, "to": b} for a, b, _c in DEEP_LINKS],
        "settings": [k for k, *_ in srows], "encoder_knobs": [k for k, _l in knobs],
    }, indent=1, ensure_ascii=False) + "\n", encoding="utf-8")
    print(f"routes={len(routes)} unmapped={unmapped} settings={len(srows)} homeless={len(homeless)} knobs={len(knobs)}")
    return 1 if unmapped else 0


if __name__ == "__main__":
    raise SystemExit(main())
