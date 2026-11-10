# MediaGuard Ultimate — Componenteninventaris

Implementatie: `apps/web/components/mg/*` (React) op klassen uit `apps/web/styles/mg.css`.
Iedere component: doel, varianten, staten, toegankelijkheid. Alle zichtbare tekst via i18n-keys.

| # | Component | React | CSS | Preview |
|---|---|---|---|---|
| 1 | Status chip | `StatusChip` | `.chip--*` | alle |
| 2 | Quality ribbon | `QualityRibbon` | `.qribbon` | 02, 03, 06 |
| 3 | Media row / poster | `MediaRow`, `Poster` | `.media-row`, `.poster` | 02 |
| 4 | Episode grid / season heatmap | `SeasonHeatmap` | `.heatmap`, `.heat-cell--*` | 01, 04 |
| 5 | Evidence card | `EvidenceCard` | `.evidence` | 03 |
| 6 | Difference viewer | `DiffViewer` | `.diff` | 03, 05 |
| 7 | Migration stepper | `Stepper` | `.stepper`, `.step--*` | 05 |
| 8 | Job progress card | `JobCard` | `.job-card` | 01, 10 |
| 9 | Policy editor + inheritance trace | `InheritanceTrace`, `SettingField` | `.trace`, `.setting` | 06, 07 |
| 10 | Dry-run diff / verdeling | `DistributionBar` | `.stackbar`, `.legend` | 05 |
| 11 | Danger-confirm dialog | `DangerDialog` | `.dialog--danger`, `.impact` | 12 |
| 12 | Instance card | `InstanceCard` | `.instance--*` | 08 |
| 13 | Settings field | `SettingField` | `.setting` | 07 |
| 14 | KPI card | `KpiCard` | `.kpi` | 01 |
| 15 | States | `StateView`, `InlineState`, `Skeleton`, `StaleMark` | `.state--*` | — |
| 16 | App shell | `AppShell`, `Sidebar`, `Topbar`, `BottomNav`, `NavDrawer` | `.app`… | alle |
| 17 | Segmented / tabs / switch | `Segmented`, `Tabs`, `Switch` | | |
| 18 | Toast | `ToastProvider` | `.toast` | |
| 19 | Timeline / audit | `Timeline` | `.timeline` | 03 |
| 20 | Data table (stacked op mobiel) | `DataTable` | `.table--stack` | 02 |

---

### 1. Status chip
- **Varianten**: `success | warning | danger | info | accent | neutral | outline`, `size: md | lg`, `pending` (icoon draait).
- **Regel**: altijd icoon + tekst. Kleur alleen = verboden. Tekst komt uit een vaste statusmap
  (`lib/status.ts`) zodat dezelfde status overal hetzelfde heet.
- **A11y**: tekst is leesbaar door schermlezers; icoon `aria-hidden`.

<a id="status-iconen"></a>
| Betekenis | Icoon |
|---|---|
| ok / gezond | `circle-check` |
| waarschuwing | `triangle-alert` |
| fout / offline | `circle-x` |
| bezig | `loader-circle` |
| onzeker / info | `info` |
| vergrendeld | `lock` |
| gepauzeerd / neutraal | `pause` |

### 2. Quality ribbon
Gesegmenteerd label: `resolutie · bron · codec · HDR/DV · talen`. Eerste segment benadrukt. DV paars,
HDR warning-kleur, AV1 success. Op mobiel blijft het één regel (segmenten worden niet afgebroken).
A11y: `aria-label` met volledige zin ("2160p WEB-DL AV1 Dolby Vision 8.1, audio Engels en Nederlands").

### 3. Media row / poster
Poster (36/56/132/168 px, 2:3). Bron: `poster_url` van core (proxy, nooit externe hotlink zonder
toestemming). Fallback: deterministische gradient op titel-hash + titeltekst. Titel + subregel
(jaar · netwerk · seizoenen · externe ID). Klikbaar naar detail.

### 4. Season heatmap / episode grid
Per seizoen een rij cellen (14 px). Statussen: aanwezig, AV1, ontbreekt, zoekend, onzeker, toekomstig
(gestippeld), niet gemonitord (gearceerd). Cel = link naar aflevering met `aria-label`
("S01E21 ontbreekt"). Legenda altijd zichtbaar. Percentage rechts telt alleen gemonitorde, uitgezonden afleveringen.

### 5. Evidence card
Kop: oordeel-chip (Bevestigt / Spreekt tegen / Neutraal), bron, confidence-balk (0–1, 2 decimalen).
Body: `dl` met matching-ID, claim, methode, mapping-trace. `details` "Ruwe technische details" met JSON.
Staten: stale (bron > 24 u), bron offline.

### 6. Difference viewer
Driekoloms grid (veld · huidig · gewenst/nieuw). Cel-markering: `changed` (accent-tint), `removed`
(danger, doorgestreept), `added` (success). Gebruikt voor: identiteit, DV-metadata vóór/na, migratie
bron vs doel, settings-simulatie, duplicaatwaarschuwing.

### 7. Migration stepper
Verticale lijst; stapstatus `done | current | warning | blocked`. Huidige stap `aria-current="step"`.
Geblokkeerde stap toont slot + reden in tooltip/tekst. Mobiel: horizontale compacte variant (`.hstepper`)
boven de inhoud.

### 8. Job progress card
Baan (GPU/CPU kleur), bestand, voortgangsbalk, FPS / ETA / bitrate / winst (of snelheid/grootte).
Lege baan blijft zichtbaar als gestippelde kaart ("Baan wacht — 214 in wachtrij"): stilstand is informatie.
Stale velden (`verouderd`) grijs met `StaleMark`. Acties: pauzeer, afbreken (danger-confirm), log.

### 9. Policy editor met overervingstrace
Scope-selector (segmented) + scope-ID-select. Trace: chips per scope van globaal → specifiek; gezette
scope = actief/omlijnd, ongezette = gestippeld. De winnende scope is accent. Komt rechtstreeks uit
`effective().trace`.

### 10. Dry-run diff / verdeling
`DistributionBar`: stackbar met 2 px gaps + legenda met absolute aantallen (geen percentages alleen).
Categorieën vast: behouden / vervangen / zoeken / AV1 / onzeker / overslaan (vaste kleuren viz-2,4,3,1,5, border-strong).
`role="img"` + volledige `aria-label`.

### 11. Danger-confirm dialog
`role="alertdialog"`, focus op annuleren, `Esc` sluit, focus-trap. Inhoud verplicht: titel met aantal,
gevolg in één zin, **impactblok** (aantal, totale GB, extra opslag), `dl` met dataset, snapshot/back-up
(chip met status), oude → nieuwe status, rollbackvoorwaarde. Bij massadestructief: getypte bevestiging.
Bevestigknop disabled tot voorwaarden voldaan; tijdens mutatie pending.

### 12. Instance card
Logo (SO/SA/RF/RA), naam, `instance_id` mono, status-chip `Gezond/Degraded/Offline/Gepauzeerd`, stats
(items, ontbrekend, respons), kv (root, categorie, versie/commit), acties (Test, Pauzeer/Hervat, Beheer).
Border kleurt mee bij degraded/offline.

### 13. Settings field
Twee kolommen (beschrijving | control). Toont: label, key (mono), uitleg (`help`), effect, chips voor
herkomst (`Erft van X` / `Gezet op Y`), standaard + bereik, `Herstart nodig`, `Geavanceerd`, integriteitsslot.
Control per type: bool → switch, int/float/duration → number, enum → select (of segmented ≤ 4),
list → multi-select chips, time_window → 2× tijd, string → input, json → textarea met validatie,
secret → link naar Geheimen. Staten: dirty, saving, error (servermelding, oude waarde blijft), locked.

### 14. KPI card
Label met icoon, waarde (tabular), meta, optionele sparkline, drill-down link (hele kaart klikbaar).
Toon: neutral/success/warning/danger. Niet-beschikbaar: waarde `—` + "Niet beschikbaar" meta.

### 15. States
`StateView kind=loading|empty|error|offline|unavailable|forbidden` (paginagroot of compact),
`InlineState tone=info|warning|danger|success` (partial offline, uitleg), `Skeleton`, `StaleMark`,
pending via chip/knop. Error toont servermelding + correlation-id + `Opnieuw proberen`.

### 16. App shell
Sidebar met groepen (Media, Kwaliteit, AV1 & HDR, Operaties, Integraties, Systeem), tellers, systeem-
health onderaan. Topbar: zoeken, actieve processen, opslag, meldingen (popover), noodstop, profielmenu
(taal, thema, uitloggen). Mobiel: bottom nav + drawer. Skip-link "Naar inhoud".

### 17–20
Segmented (`aria-pressed`), tabs (`role=tablist`, pijltjestoetsen), switch (`role=switch`, `aria-checked`),
toast (`role=status`/`alert`), timeline (audit: wie/wanneer/waarom/oud/nieuw/undo), DataTable met sticky
header, sortering (`aria-sort`), selectie, stacked mode op mobiel, virtualisatie voor > 500 rijen
(`@tanstack/react-virtual`, al in gebruik in AV1-wachtrij).
