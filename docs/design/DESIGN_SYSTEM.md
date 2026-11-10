# MediaGuard Ultimate — Design System

Status: v1 (2 nov 2026) · eigenaar: werkstroom D (design + UI) · canonieke CSS: [`apps/web/styles/mg.css`](../../apps/web/styles/mg.css)

MediaGuard is een **Media Operations Control Center**: rustig, Nederlandstalig, informatiedicht en
betrouwbaar. Het combineert een mediabibliotheek (posters als context) met een infrastructuur-dashboard
(status, wachtrijen, bewijs). Het is uitdrukkelijk géén Sonarr/Radarr-kopie en geen generiek admin-template.

## 1. Ontwerpprincipes

1. **Status is betekenis, geen decoratie.** Kleur alleen waar ze iets zegt. Status = kleur + icoon + tekst
   (nooit kleur alleen; WCAG 1.4.1).
2. **Eerst het belangrijkste, dan bewijs.** Progressive disclosure: samenvatting → evidence-kaarten → ruwe
   technische details in een uitklapblok.
3. **Geen doodlopende kaarten.** Iedere KPI en elk getal is een drill-down naar de onderliggende media.
4. **Geen nepdata, geen schijnknoppen.** Bestaat een backend-capability niet, dan toont de UI een eerlijke
   `Niet beschikbaar`-staat met het betrokken endpoint. Knoppen zijn disabled met reden.
5. **Gevaar is expliciet.** Destructieve acties tonen reden, impact (aantal, GB), dataset, snapshot/back-up,
   oude → nieuwe status en rollbackvoorwaarde. Dry-run is altijd zichtbaar.
6. **Integriteit is niet schakelbaar.** Vergrendelde controles worden getoond (slotje + "Integriteit"), nooit
   verborgen en nooit uitschakelbaar.
7. **Toetsenbord eerst.** `/` zoeken, `?` sneltoetsen, `g o` / `g b` … navigatie, `j/k` door lijsten, `Esc` sluit.

## 2. Tokens

Alle tokens zijn CSS custom properties op `:root` (donker, standaard) en `:root[data-theme="light"]`.

**Thema-keuze (Donker / Licht / Systeem).** Zichtbare wisselknop in de topbar (maan/zon/monitor, cyclisch) én een
segmented control in het profielmenu. Volgorde van voorrang:
1. **Per gebruiker (deze browser)**: keuze in topbar/profielmenu → `localStorage["mg.theme"]`, vóór de eerste paint
   toegepast door een inline script in `app/layout.tsx` (geen flits).
2. **Standaard voor iedereen**: instelling `ui.theme` (Instellingen › Systeem; ook gekozen in de setup-wizard).
3. `Systeem` volgt `prefers-color-scheme` live (media-query listener).
Server-side per-gebruiker opslag (zoals `PUT /auth/me/locale` voor taal) bestaat nog niet in core — zie open punten.

### 2.1 Kleur — donker (standaard)

| Token | Waarde | Gebruik |
|---|---|---|
| `--mg-bg` | `#0C111B` | Pagina-achtergrond |
| `--mg-surface` | `#141C29` | Kaarten, sidebar, tabellen |
| `--mg-raised` | `#1C2735` | Verheven vlak: knoppen, actieve segment, popovers |
| `--mg-sunken` | `#0A0E16` | Invoervelden, evidence-blokken, code |
| `--mg-border` | `#2C3746` | Subtiele border (decoratief) |
| `--mg-border-soft` | `#222C3A` | Scheidingslijnen binnen kaarten |
| `--mg-border-strong` | `#5A6A80` | Grens van formuliervelden (≥ 3:1, WCAG 1.4.11) |
| `--mg-text` | `#F0F4FA` | Primaire tekst |
| `--mg-text-2` | `#A4B0C1` | Secundaire tekst |
| `--mg-text-3` | `#8592A5` | Meta/labels (nog steeds ≥ 4.5:1 op elk vlak) |
| `--mg-accent` | `#4F9CF9` | Primaire actie, focus, actieve navigatie, links |
| `--mg-on-accent` | `#07101F` | Tekst op accent-knop (wit haalt geen 4.5:1) |
| `--mg-success` | `#35B587` | Gezond, geverifieerd, aanwezig |
| `--mg-warning` | `#E8A44D` | Aandacht, degraded, onzeker-oplosbaar |
| `--mg-danger` | `#E36B71` | Fout, offline, destructief |
| `--mg-info` | `#8C9DF5` | Informatief, AI-advies, onzeker bewijs |
| `--mg-focus` | `#8EC1FF` | Focusring (2 px + 2 px offset) |
| `--mg-*-tint` | 12 % kleur over surface | Achtergrond van chips/inline-states |
| `--mg-lane-gpu` / `--mg-lane-cpu` | `#4F9CF9` / `#B28CF0` | AV1-banen (behouden uit AV1-app) |
| `--mg-viz-1…6` | blauw, groen, oranje, paars, rood, cyaan | Categorische grafiekreeksen |

### 2.2 Kleur — licht

| Token | Waarde | | Token | Waarde |
|---|---|---|---|---|
| `--mg-bg` | `#F4F6FA` | | `--mg-accent` | `#1D66C9` |
| `--mg-surface` | `#FFFFFF` | | `--mg-on-accent` | `#FFFFFF` |
| `--mg-raised` | `#EEF2F7` | | `--mg-success` | `#167650` |
| `--mg-border` | `#D3DAE4` | | `--mg-warning` | `#8F5300` |
| `--mg-border-strong` | `#7A889C` | | `--mg-danger` | `#BE2F39` |
| `--mg-text` / `-2` / `-3` | `#0E1624` / `#4A5668` / `#5D697B` | | `--mg-info` | `#4651C4` |

Lichte statuskleuren zijn bewust donkerder dan de donkere variant zodat tekst op wit ≥ 4.5:1 blijft.

### 2.3 Contrast (WCAG 2.2 AA, berekend)

Berekend met [`tools/contrast.mjs`](tools/contrast.mjs) (relatieve luminantie volgens WCAG 2.x; tints
alpha-gecomponeerd over het onderliggende vlak). Volledige tabel: [`CONTRAST_REPORT.md`](CONTRAST_REPORT.md).
Alle 82 tekst- en componentcombinaties halen AA; de enige combinaties onder 3:1 zijn decoratieve borders.

| Thema | Combinatie | Ratio | Eis |
|---|---|---|---|
| donker | tekst `#F0F4FA` op bg | 17.12:1 | 4.5 |
| donker | tekst-2 `#A4B0C1` op surface | 7.78:1 | 4.5 |
| donker | tekst-3 `#8592A5` op raised (slechtste) | 4.78:1 | 4.5 |
| donker | accent `#4F9CF9` op surface | 6.07:1 | 4.5 |
| donker | success op surface | 6.61:1 | 4.5 |
| donker | warning op surface | 8.02:1 | 4.5 |
| donker | danger `#E36B71` op surface | 5.38:1 | 4.5 |
| donker | danger op danger-tint (slechtste chip) | 4.65:1 | 4.5 |
| donker | on-accent `#07101F` op accent | 6.76:1 | 4.5 |
| donker | border-strong op surface (formulierveld) | 3.10:1 | 3.0 |
| donker | focus `#8EC1FF` op bg | 10.10:1 | 3.0 |
| licht | tekst op surface | 18.12:1 | 4.5 |
| licht | tekst-3 op raised (slechtste) | 4.95:1 | 4.5 |
| licht | accent `#1D66C9` op surface | 5.53:1 | 4.5 |
| licht | accent op accent-tint (slechtste chip) | 4.81:1 | 4.5 |
| licht | wit op accent (primaire knop) | 5.53:1 | 4.5 |
| licht | border-strong op bg | 3.33:1 | 3.0 |

Licht thema wordt daarnaast in de E2E-suite automatisch gecontroleerd met axe-core (`color-contrast`) op Overzicht,
Instellingen, Ontbrekend & herstel en Instanties — in beide thema's 0 overtredingen.

Consequentie: de tint-opaciteit is **12 %** (donker) en **10 %** (licht). Bij 14 % zakt danger-op-tint naar 4.50:1
(afgerond, net te laag) — daarom niet hoger zetten.

### 2.4 Typografie

> **Vervallen sinds Peil (stap 3, ADR-0017 punt 12):** de web-UI gebruikt het systeemfont en de systeem-mono uit
> de ontwerptaal Peil; Inter en JetBrains Mono zijn niet meer meegeleverd. De tekst hieronder is historisch.

- **Inter** (variabel, SIL OFL 1.1) — zelf gehost uit `apps/web/app/fonts/` via `next/font/local`; geen externe
  fontrequests. Licentie: `apps/web/app/fonts/OFL-Inter.txt`.
- **JetBrains Mono** (OFL) voor ID's, paden, hashes, commando's en `instance_id`.
- Getallen in tabellen/KPI's: `font-variant-numeric: tabular-nums`.

| Rol | Grootte/gewicht | Gebruik |
|---|---|---|
| Paginatitel | 24/680, −0.02em | `.page-title` (20 px op mobiel) |
| KPI-waarde | 28/680 | `.kpi-value` |
| Kaarttitel | 14/650 | `.card-title` |
| Body | 14/400 (13 px in dichte lijsten) | standaard |
| Meta | 12/400 `--mg-text-3` | subregels, tijden |
| Eyebrow | 11/600 uppercase +0.06em | navigatiegroepen, sectielabels |
| Mono | 11–12 px | ID's, paden |

### 2.5 Ruimte, radius, schaduw

- Spacing: **4 / 8 / 12 / 16 / 24 / 32** (`--mg-s1…s6`). Kaart-padding 16; paginapadding 24 (16 mobiel);
  grid-gap 16 (12 mobiel).
- Radius: 6 (tags), **8** (knoppen/velden), 10 (segmented/evidence), **12** (kaarten), **14** (dialogen).
- Schaduw: alleen `--mg-shadow-1` (subtiele kaartrand), `-2` (popover/savebar/toast), `-3` (dialoog).
  Geen gradients behalve merkteken, AI-adviespaneel en loginpaneel (zeer subtiel).

### 2.6 Iconografie

Lucide (ISC), 16 px, `stroke-width 1.75`, `currentColor`. Eén icoon per betekenis (zie
[COMPONENT_INVENTORY.md](COMPONENT_INVENTORY.md#status-iconen)). Iconen zijn decoratief (`aria-hidden`) tenzij
een knop alleen een icoon heeft — dan `aria-label`.

### 2.7 Beweging

Max 180 ms, `cubic-bezier(.2,.7,.2,1)`. Alleen voor feedback (dialoog/drawer/toast, voortgang).
`prefers-reduced-motion: reduce` schakelt alle animatie uit. Geen decoratieve animaties.

## 3. Layout

- **Desktop 1440 / 1280**: vaste sidebar (256 / 236 px), sticky topbar 60 px, content max 1520 px,
  **12-koloms grid** (`.grid` + `.span-N`), gap 16.
- **Tablet ≤ 1100**: KPI's 2×2, kolommen stapelen tot 12.
- **Mobiel ≤ 860 (doel 360–390)**: sidebar verdwijnt, **bottom nav** (Overzicht · Activiteit · Bibliotheek ·
  Ruimte · Aanvragen); de rest via het profielmenu (zie §8.1). Tabellen met `.table--stack`
  worden gestapelde kaarten met `data-label` per cel. Geen horizontale paginascroll (E2E-gecontroleerd).
- Topbar: globale zoekbalk (`/`), actieve processen, opslag, meldingen, **noodstop** (altijd zichtbaar, ook
  mobiel als icoonknop), profiel.

## 4. Statussysteem

| Status | Kleur | Icoon | Voorbeeldtekst |
|---|---|---|---|
| Gezond / aanwezig / geverifieerd | success | `circle-check` | Gezond, Compleet, Gecontroleerd |
| Degraded / aandacht | warning | `triangle-alert` | Degraded, Bevestigen, Gestopt |
| Offline / fout / mislukt | danger | `circle-x` | Offline, Verificatie mislukt |
| Bezig / pending | accent | `loader-circle` (draait) | Zoekend, Proefconversie |
| Onzeker bewijs / advies | info | `info` / `bot` | Onzeker, Advies |
| Neutraal / uit | neutral | `pause` / `eye-off` | Origineel behouden, Niet gemonitord |
| Vergrendeld (integriteit) | neutral | `lock` | Integriteit |

Dolby Vision-statussen (Q1.6): `Toegestaan` (success), `Proefconversie` (accent), `Gecontroleerd` (info),
`Origineel behouden` (neutral), `Niet ondersteund` (danger), `Verificatie mislukt` (danger),
`Herstelbaar` (warning). Geel = waarschuwing, nooit een permanent blok.

Instantie: `Gezond` / `Degraded` / `Offline` / `Onbekend` / `Gepauzeerd`.

## 5. Verplichte staten

Elke datacomponent ondersteunt: **loading** (skeleton), **empty** (uitleg + primaire actie),
**error** (melding + `Opnieuw proberen` + correlation-id), **partial offline** (inline-waarschuwing,
rest blijft bruikbaar), **niet beschikbaar** (endpoint bestaat nog niet / capability ontbreekt — toont
het endpoint), **stale** (`Metadata 4 min oud`), **uncertain** (info-chip + uitleg), **pending** (draaiend
icoon, knoppen disabled), **success** (toast + audit-referentie). Zie COMPONENT_INVENTORY §States.

## 6. Locale

`nl-NL` standaard, `en-US` beschikbaar; locale uit `GET /api/v1/auth/me`. Alle strings via i18n-keys
(`apps/web/lib/i18n/`). Getallen `48.912`, decimalen `21,6`, bestandsgrootte binair met NL-notatie
(`1,4 GB`), datum `2 nov 2026`, tijd `14:31`, relatieve tijd `4 min geleden`.

## 7. Bestanden

| Bestand | Inhoud |
|---|---|
| `apps/web/styles/mg.css` | Canonieke tokens + componentklassen (gedeeld met previews) |
| `docs/design/tools/contrast.mjs` | Contrastberekening → `CONTRAST_REPORT.md` |
| `docs/design/tools/build-previews.mjs` | Bouwt zelfstandige HTML-previews (fonts + iconen ingebed) |
| `docs/design/tools/screenshot.cjs` | Playwright-screenshots 1440 en 390 |
| `docs/design/previews/*.html` | 12 representatieve schermen |
| `docs/design/previews/screenshots/*.png` | Renders van de previews (1440, 390 en 390-full); licht: `13-overzicht-licht-*`, `14-ontbrekend-licht-*`, `12-quarantaine-licht-*` |
| `docs/design/screenshots-app/*.png` | Screenshots van de échte implementatie tegen een draaiende core, donker én licht |

De previews, het prototype (`docs/design/prototype/`) en hun schermafdrukken bevatten alleen verzonnen voorbeeldinhoud.
Voor de openbare repository zijn op 10 november 2026 enkele titels, de initialen en een paar getallen daarin vervangen
en zijn de afdrukken opnieuw gemaakt: previews met `docs/design/tools/screenshot.cjs`, het prototype met Playwright op
1440×900 en 390×844 (licht thema). `build-previews.mjs` is daarbij niet gebruikt: dat script verwacht de oude
lettertypebestanden van vóór de Peil-omzetting; de vervangingen zijn rechtstreeks in de gebouwde HTML én in `src/` gedaan.

In `docs/design/screenshots-app/` zijn op dezelfde dag alleen twee tekstregels in de PNG's opnieuw getekend in hetzelfde
lettertype (de afdrukken zelf zijn niet opnieuw gemaakt): de placeholder van Anime › Releasenaam ontleden en een pad in
de migratie-mapping noemen nu "Golfbreker" in plaats van een echte serietitel.

## 8. Navigatie en patronen (nieuwe opzet, november 2026)

Bron: `docs/design/prototype/mediaguard-redesign.html`, inventaris `docs/design/2026-11-feature-inventory.md`.

### 8.1 Informatiearchitectuur
- **Vijf onderdelen + Instellingen**: Overzicht · Activiteit · Bibliotheek · Ruimte · Aanvragen; Instellingen
  onderaan de zijbalk (`NavItem.footer`). Telefoon: onderste balk met de vijf onderdelen; Instellingen,
  Ontbrekend, Kalender, Terugzetten en Systeem in het profielmenu (`role="menu"`). Geen ‘Meer’-lade.
- **Hooguit één rij tabs of chips per pagina** (`main [role=tablist]` ≤ 1, afgedwongen in `e2e/werk-ia.spec.ts`).
  Een subpagina krijgt geen tweede tabrij maar een **terug-link** (`.back-link`, `SettingsTabs`) boven de kop.
- Elke oude URL blijft werken: omleiding of deep link, vastgelegd in `docs/design/ia-map.json` en getest.

### 8.2 Instellingen op één pagina
| Onderdeel | Klasse / testid | Gedrag |
|---|---|---|
| Zoekbalk + ‘Alles uitklappen’ | `.settings-bar`, `settings-search`, `settings-expand-all` | sticky onder de topbalk; zoeken filtert live op label, sleutel, uitleg en categorie |
| Inhoudsopgave | `.settings-toc` (chips `.chip--outline`) | ankers naar `#<sectie>`; verborgen tijdens zoeken |
| Sectie | `.settings-section`, `section-<id>` | `Card` met eenvoudige velden, ‘Geavanceerd tonen’ (`advanced-<id>`), daarna ‘Meer hierover’ |
| Meer hierover | `.settings-links` + `.list-row--hover` | links naar subpagina's die bij de sectie horen |
| Per serie of instantie | `settings-scope-open` → `?view=all` | uitzonderingen per scope (`SchemaSettings`) |

Volgorde: Algemeen · AV1 & ruimte · Originelen · Downloaden & zoeken · Taal & anime · Koppelingen ·
Meldingen · Beveiliging & toegang · Systeem · Gereedschap · Per serie of instantie. Indeling per
instelling: `sectionOf()` in `app/(app)/instellingen/page.tsx` (= `EXTRA_CAT` in
`scripts/gen-feature-inventory.py`). Elke niet-verborgen instelling moet een plek hebben
(`e2e/werk-instellingen-dekking.spec.ts`). Een instelling die afhangt van een andere wordt
**uitgeschakeld met uitleg**, niet verborgen.

### 8.3 Componenten
- **Tabs `variant="chips"`** (`.tabs--chips`): filterknoppen (Activiteit, Aanvragen-ontdekken). Zelfde
  toetsenbordgedrag als tabs (pijltjes); actief = `--mg-accent-tint` + accentrand. Telt als de ene tabrij.
- **Poster-raster** (`.poster-grid`, `.poster-card`): 2 kolommen ≤ 480 px, anders `minmax(150px, 1fr)`;
  poster 2:3, titel één regel met ellips, status als `StatusChip`, één primaire knop.
- **Kwaliteitspaneel** (`components/library/quality.tsx`, `quality-panel`): één bestand als `.kv`
  (Kwaliteit · Beeld · Audio · Ondertitels · Custom formats · Score · Doel); meerdere als compacte tabel.
  Doel: `success` ‘Doel bereikt’, anders `info` met wat er ontbreekt.
- **Upgrade-tijdlijn** (`upgrade-timeline`): `.timeline` + `.tl-item`/`.tl-dot--accent`; de reden
  (`reason_nl`) is de titel, daaronder kwaliteit/score/formats en ‘via <bron>’.
- **`.tag--accent`**: label voor ‘AV1 door MediaGuard’ en ‘verving een AV1-versie’.
- **Bevestiging met twee uitkomsten**: seizoen aanzetten → ‘Volgen en meteen zoeken’ (primair) of
  ‘Alleen volgen’; knoplabels noemen de actie, nooit ‘OK’.
- Een endpoint dat een oudere core niet kent (404/405) toont een rustige uitleg, geen foutmelding.
