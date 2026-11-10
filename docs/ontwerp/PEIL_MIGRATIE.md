# MediaGuard naar Peil — omzettingsontwerp (stap 2)

**Status:** web (`apps/web`) gebouwd in stap 3 (9 november 2026; zie §13 en [ADR-0017](../adr/ADR-0017-peil-web-bouwkeuzes.md)); Android (`apps/android`) gebouwd in stap 4 (9 november 2026; zie §14 en [ADR-0018](../adr/ADR-0018-peil-android-bouwkeuzes.md)) · ontwerp: design-agent · code-HEAD bij het ontwerp `8060747` (MediaGuard 1.2.2)
**Ontwerptaal:** Peil **1.1.0** bij dit ontwerp; sinds 10 november 2026 **1.1.1** (gepinde kopie, ADR-0021), versie-gepinde kopie in [`design/ontwerptaal/`](../../design/ontwerptaal/HERKOMST.md)
(bindend: `ONTWERPTAAL.md`, `COMPONENTEN.md`, `ADR-ONTWERPTAAL.md`; MediaGuard-pad: ONTWERPTAAL §13.3)
**Besluiten:** [`docs/adr/ADR-0016-peil-omzetting-interpretaties.md`](../adr/ADR-0016-peil-omzetting-interpretaties.md)
**Nulmeting:** [`docs/screenshots/peil/voor/`](../screenshots/peil/voor/README.md) (web: afdrukken + `metingen.json` +
`toetsenbord.json`; Android: `android/` + `android/index.json`)
**Reikwijdte:** het uiterlijk van de web-UI (`apps/web`) en de native Android-app (`apps/android`). Functies, routes,
API, foutformaat, datamodel, poorten en installatie veranderen niet.

Gebruikt: `design:design-critique` (§5), `design:accessibility-review` (§4, §5.4), `design:design-system` (§6, §10),
`design:ux-copy` (§6.7), `design:design-handoff` (§6.6, §8, §9, §10) en `dataviz` (§7; de paletvalidator is gedraaid).

Inhoud: [0 Samenvatting](#0-samenvatting) · [1 Regels](#1-vaste-regels-en-besluiten) · [2 Werkwijze](#2-werkwijze-en-bewijs) ·
[3 Inventaris](#3-inventaris-schermen-onderdelen-en-toestanden) · [4 Nulmeting](#4-nulmeting) ·
[5 Kritiek](#5-kritiek-op-de-huidige-stand) · [6 Vertaling web](#6-vertaling-naar-peil-web) ·
[7 Grafieken](#7-grafieken-dataviz-procedure) · [8 Twee fasen](#8-de-twee-fasen-web) ·
[9 Toevoegingen](#9-toegestane-toevoegingen-specificatie) · [10 Android](#10-android-peil-in-jetpack-compose) ·
[11 Tests](#11-welke-tests-geraakt-worden) · [12 Risico's](#12-risicos-open-punten-en-wat-niet-gevalideerd-is) ·
[13 Stand web](#13-stand-na-stap-3-web) · [14 Stand Android](#14-stand-na-stap-4-android)

---

## 0. Samenvatting

MediaGuard 1.2.2 heeft een eigen, verzorgde stijl: `styles/mg.css` (983 regels) + `styles/av1.css` (119 regels),
Tailwind 4 alleen als preflight, 794 inline `style={{…}}` en 135 lucide-iconen. De Android-app (Kotlin + Jetpack Compose)
neemt de kleuren, ruimtes en lettermaten "1-op-1" over uit `mg.css` (`ui/theme/Theme.kt`). De toon is rustig en
eerlijk. Toch wijkt de UI op vijf punten hard af van Peil:

1. **Statuskleur betekent te veel.** Groen staat voor "Binnen", "Compleet", "Live", "Alles in orde", "Noodstop: uit",
   geslaagde taken en de "Wacht op jou"-rijen (met het `CircleCheck`-icoon, terwijl er juist iets openstaat). Rood staat
   voor "Ontbreekt" en voor tellers. Violet is de GPU-baan (Android) en de CPU-baan (web), terwijl violet in Peil alleen
   "een mens moet goedkeuren" betekent. Peil: groen = alleen *gemeten en goed*.
2. **Onbekend en verouderd zijn niet eerlijk genoeg.** Neutrale chips krijgen het `Pause`-icoon. Een onbereikbare
   AV1-engine geeft KPI-tegels met "— Nog niet beschikbaar" (het Peil-woord voor een *fase*). Verouderde data is een
   grijze regel van 12 px; op Android is "Niet bijgewerkt" een amber kaart terwijl de waarden er vers uitzien.
3. **Klein.** 1 947 tekstelementen onder 14 px op de 30 schermen (1440; kleinste 10 px), 847 van de 950 doelen
   kleiner dan 44 px op desktop, knoppen van 32–40 dp op Android, ellipsen op titels in lijsten ("Vervangen van b…" op
   360 px) en tabs die buiten beeld vallen. Zie §4 voor de gemeten aantallen.
4. **Grafieken gebruiken statuskleuren als reeks.** "Besparing per dag" en "Opslagtrend" zijn groen (`--mg-viz-2` =
   `--mg-success`); de episodekaart is kleur-alleen in statuskleuren; de belastingsgrafiek heeft GPU (blauw) en CPU
   (violet) die voor kleurenblinden samenvallen (ΔE 0,1 deutan). Geen tabelweergave, geen toetsenbord, een ontbrekende
   dag wordt een staaf van 0 of verdwijnt van de as.
5. **Focus, toegankelijkheid en thema.** Eén focuskleur van 2 px, op 390 px 1–4× per scherm achter de vaste
   ondernavigatie; axe vindt op elk scherm de profielknop (WCAG 2.5.3) en verder twee kritieke fouten (noodstopknop
   zonder naam op de telefoon, ongeldige `aria`-verwijzing in Release Lab) en te weinig contrast in de gevaarknop van
   dialogen in donker. Standaardthema Donker (Peil: Systeem). Op Android Inter als ingebouwd lettertype (Peil:
   systeemfont, Roboto).

Wat goed is en blijft: **geen horizontale overloop** op 360–1440 px (gemeten op alle 438 afdrukken); status heeft op het
web al icoon + woord; "geen nepdata"-belofte in elke onbeschikbare toestand;
integriteitsslot zichtbaar en niet uit te zetten; getypte bevestiging bij definitief verwijderen; skiplink; dialogen
met focusval; sneltoetsen; en-US naast NL; de Android-app volgt al licht/donker van het systeem.

**Aanpak** (§8): **web fase 1** = alleen CSS en woorden: de gedeelde aliaslaag `aliaslagen/mediaguard-mso.css` (die MSO
al gebruikt) + een kleine MediaGuard-eigen correctielaag `styles/peil-mg.css`, daarna Peil-woorden en -iconen in de
bestaande componenten (NL én en-US). **Web fase 2** = Tailwind 4 + shadcn/ui (new-york, React 19), component voor
component, plus de twee toegestane toevoegingen ("Terug naar de Hub" en tabelweergave + toetsenbord bij elke grafiek),
en tot slot `mg.css`, inline styles en Inter/JetBrains Mono opruimen. **Android** (§10) = `Theme.kt` uit `tokens.json`,
één statuscomponent met icoon + woord + vorm, 48 dp-doelen, Roboto. OpenAPI blijft byte-gelijk (§1), `data-testid`'s en
rollen blijven, poorten en installatie veranderen niet.

---

## 1. Vaste regels en besluiten

| Regel | Gevolg voor MediaGuard |
|---|---|
| Alleen uiterlijk (contract §13, ONTWERPTAAL §13) | Geen nieuwe routes, geen andere API-aanroepen, geen datamodel- of migratiewijziging. Zelfde schermen, tabs, dialogen, stappen en sneltoetsen. |
| OpenAPI byte-gelijk | MediaGuard heeft (nog) geen gecommitte export. **Nulmeting 9 nov 2026, HEAD `8060747`:** `create_app().openapi()` → 383 paden; `json.dumps(…, indent=2, ensure_ascii=False, sort_keys=True)` zonder slotregel = sha256 `54eee4d7853f58e8a7cc1a79bcb569d6d8b44e9a6a2c54e6a92e213dce03e59a` (twee keer gemeten, deterministisch). Na elke stap opnieuw meten; ook de Hub-link (§9.1) gebruikt alleen het bestaande instellingenregister. |
| `data-testid`'s en rollen blijven | O.a. `profile-button`, `profile-menu`, `theme-choice`, `estop-button`, `estop-confirm`, `estop-banner`, `status-pill`, `bell`, `bell-badge`, `notif-panel`, `mobile-search`, `help-link`, `sidebar-health`, `library-lock-banner`, `library-unlock`, `quarantine-*`, `q-item-*`, `bulk-delete`, `purge-button`, `settings-save`, `error-boundary`, `worker-gpu-*`/`worker-cpu-*`; de rollen/namen uit de e2e (navigatie "Mobiele navigatie", knop "Profiel", groep "Thema", dialoog "Sneltoetsen", `alertdialog`, kop "Overzicht"). |
| Integriteit, rollback, monitoring niet aanraken | Integriteitsslot (`integrity.*`), vrijgavecontrole, quarantaine, herstelbundel, noodstop, back-up/herstel en gezondheidscontroles houden hun gedrag; alleen hun uiterlijk wordt Peil. |
| Toegestane toevoegingen (besluiten gebruiker, 9 nov 2026) | O-11 **"Terug naar de Hub"** (adres uit de configuratie, verborgen als onbekend) en **tabelweergave + toetsenbordbediening bij de grafieken**; **de native Android-app krijgt ook de Peil-stijl**. Verder niets. De themaknop (O-09) is voor my_house; MediaGuard heeft al Systeem/Licht/Donker in het profielmenu. |
| Taal | Web: NL + **en-US** (`lib/i18n/messages/*` en `messages/en/*`). Elke nieuwe of gewijzigde tekst krijgt beide talen (§6.7). Android is nu alleen NL (teksten in Kotlin); nieuwe Android-teksten staan hier in NL én en-US, maar de app krijgt in deze omzetting geen meertaligheid (ADR-0016 punt 4). |
| Geen versie-ophoging | Geen tag of release; CHANGELOG onder "Unreleased". Geen nieuwe Excel-ID's; deze ontwerpstap wijzigt geen Excel-status. |
| Contractgegevens D01–D18 | Nergens een voorbeeld-IP, poolnaam of poort als echte waarde (ook niet als placeholder van het Hub-adres). |

---

## 2. Werkwijze en bewijs

- **Echte app, testgegevens.** De echte core (`python -m mediaguard`, verse datamap, `MG_START_BACKGROUND=0`) en de
  echte productiebuild van de web-UI (`npm run build` → `node server.mjs`), beide alleen op 127.0.0.1. Als Sonarr/Radarr
  dienden de nep-API's uit de eigen coretests (`apps/core/tests/test_arr_fakes.py`, `FakeArr`) als HTTP-servers, met
  fictieve titels (Bergpas, De Kustlijn, Nachttrein, Stadshaven, Sterrenveld, Zomerlicht, De Vuurtoren, Noorderzon,
  Polderwind, Wolkenkasteel). De core inventariseerde ze echt (`POST /library/sync`: 5 series, 53 afleveringen, 5 films).
  Twee quarantainerijen zoals `e2e/seed.ts`. Gebruikers: beheerder, kijker (rol viewer) en een beheerder met taal
  en-US. Er draait geen AV1-engine, geen Seerr en geen recovery-worker: dat is de echte toestand van een verse
  installatie en staat zo in de afdrukken.
- **Afdrukken:** `docs/screenshots/peil/voor/maak-afdrukken.mjs` (Playwright 1.65, Chromium, `nl-NL`/`en-US`,
  `Europe/Amsterdam`, schaal 1): 30 schermen × 360/390/768/1440 × licht/donker (volledige pagina), 24 tabs en
  26 toestanden × 390/1440 × licht/donker (sneltoetsen alleen 1440). Acht toestanden zijn **nagebootst** door
  API-antwoorden in de browser te onderscheppen (laden, fout, offline, verouderd ×2, eerste start, AV1-engine actief ×2);
  de noodstop is **echt** aan- en weer uitgezet via `POST /system/emergency-stop`. Zie de README per afdruk.
- **Nulmeting in de browser** per afdruk (`metingen.json`): overloop t.o.v. de apparaatbreedte, doelen < 44 px, tekst
  < 14 px, gebruikte lettertypes, inline styles, chips per toon, pauze- en vinkiconen, grafiekelementen, axe-core 4.14
  met `wcag2a, wcag2aa, wcag21a, wcag21aa, wcag22aa` (de Peil-set; de huidige e2e draait alleen `color-contrast`).
- **Tab-doorloop** (`toetsenbord.json`): 40 Tab-stops op 5 schermen × 390/1440 × licht/donker.
- **Android:** Paparazzi 1.3.5 in een **kopie buiten de repo** (de testklasse `shots/ScreenShots.kt` met drie extra
  apparaatmaten), echte composables met de bestaande `SampleData.kt`: 16 schermen/toestanden × 360 dp / 393 dp /
  393 dp-volledig × licht/donker = 96 afdrukken (`android/`). Geen emulator, geen echte telefoon.
- **Palet:** dataviz-validator (`validate_palette.js`) op het huidige `--mg-viz-*`, op de reekskleuren van de
  belastingsgrafiek en op Peil `chart-1…5` (§4.4).
- **Niet gevalideerd:** echte telefoon, echte schermlezer (NVDA/TalkBack), 200 %-zoom, forced colors, tv/speler, echte
  NAS. Contrast is niet uit de PNG's gemeten (die zijn naar 256 kleuren omgezet); contrast komt uit axe en uit de
  Peil-`contrast.json`.

---

## 3. Inventaris: schermen, onderdelen en toestanden

### 3.1 Schil web (`components/shell/app-shell.tsx`, `nav.ts`, `media-tabs.tsx`, `space-tabs.tsx`, `settings-tabs.tsx`)

| Onderdeel | Nu | Afdruk |
|---|---|---|
| Zijbalk (≥ 861 px) | logo "MediaGuard · Media Operations"; **5 items** Overzicht · Activiteit · Bibliotheek · Ruimte · Aanvragen (+ Migratie alleen tijdens een migratie); onderaan Instellingen; teller op Activiteit (rood); voet: gezondheidsregel (stip + "6 dingen vragen aandacht →"), live-regel (groene stip "Live"), versie en taal | `schermen/*-1440` |
| Kopbalk | zoekveld (`/`, resultaten-popover), pil "Aandacht 6" (rode rand + stip, `status-pill`), hulp (`?`-icoon), bel met teller (amber), profielknop met initialen ("BE") | idem |
| Profielmenu | gebruiker + rol, Taal (NL/EN), Thema (Donker/Licht/Systeem), "Technische details tonen", links (Instellingen; telefoon: meer), Hoe werkt MediaGuard, **Noodstop**, Uitloggen | `toestanden/profielmenu-*` |
| Telefoon (≤ 860 px) | kop met zoekicoon, pil, bel, profiel; ondernavigatie **Overzicht · Activiteit · Bibliotheek · Ruimte · Aanvragen**; zoeken als volledig scherm | `schermen/*-360/390`, `toestanden/zoeken-*-390` |
| Banners | bibliotheek vergrendeld (violet-blauw vlak, slot, knop "Bibliotheek vrijgeven"), noodstop actief (rood, `role=alert`) | `schermen/overzicht-*`, `toestanden/noodstop-*` |
| Tabbalken | Bibliotheek · Ontbrekend · Kalender; Ruimte: Besparing · Wachtrij · Kwaliteit · Terugzetten; Instellingen-tabs bij de subpagina's | per scherm |
| Overig | sneltoetsen (`?`, `g o`, `g n`, `g b`, `g a`, `g r`, `g s`, `m`, `j`), toasts, meldingenpaneel | `toestanden/sneltoetsen-*`, `meldingen-paneel-*` |

### 3.2 Schermen (web)

Alle 45 `page.tsx`-routes; 9 daarvan zijn omleidingen (`/av1/ai`, `/av1/beslissingen`, `/av1/bibliotheken`,
`/av1/encoder-instellingen`, `/av1/fouten`, `/av1/logs`, `/av1/profielen`, `/av1/workers`, `/instellingen/audit`) en
5 zijn dunne omleidingen naar een tab (`/downloads`, `/seerr`, `/jobs`, `/audio`, `/instellingen/gevorderd`). De overige
29 routes in de app staan hieronder als 30 schermen (de titelroute twee keer: serie en film); inloggen en eerste start
staan bij de toestanden (§3.4). Elk scherm is afgedrukt op 360/390/768/1440 × licht/donker (`schermen/<naam>-*`).

| Naam (route) | Opbouw | Grafiek/meter |
|---|---|---|
| `overzicht` (`/`) | vergrendelbanner · hero "6 dingen hebben je hulp nodig" · Wacht op jou (rijen met knop) · Nu bezig · 4 KPI-tegels (Beschikbaar, Ontbrekend, Fouten, AV1-besparing) · regel "Laatste 24 uur" · Nieuwe afleveringen deze week | — |
| `activiteit` (`/activiteit`) | filterchips Nu · Problemen · Downloads · Wat is er gebeurd · Geschiedenis; lijsten met statuschips, rij-menu's | voortgangsbalken |
| `bibliotheek` (`/bibliotheek`) | mediatabs; type-tabs (Series/Anime/Films/Animefilms), filters (status, instantie, sortering), lijst met posters en chips, paginering | — |
| `bibliotheek-titel` (`/bibliotheek/[instantie]/[id]`) | kruimelpad, poster + feiten (`dl`), "Instellingen voor deze titel", tabs Seizoenen · Kwaliteit · Upgrades · Bewijs · Historie; seizoenen met afleveringen en chips "Binnen/Ontbreekt/Verwacht", volg-schakelaars | — |
| `bibliotheek-film` | idem voor een film (tabs + Media, Conversies) | — |
| `aflevering` (`…/aflevering/[id]`) | feiten, "Klopt het bestand?", tabs Overzicht · Identiteit · Kwaliteit · Media · Releases · Conversies · Historie | — |
| `ontbrekend` (`/ontbrekend`) | oorzaken, backfill per instantie, ontbrekende items met diagnose, NL-ondertitels | voortgang |
| `kalender` (`/kalender`) | weekraster (telefoon: lijst), vorige/volgende week, type-filter | kleurrand per type |
| `ruimte` (`/av1`) | KPI-tegels (Wachtrij, Bespaard, Doorvoer, Fouten), live-verbinding, banen (GPU/CPU) met workers, Hardware, Belasting, Besparing per dag, "Wat kwam erbij en waar ging de ruimte heen" (periode), Per soort, Per bron, Alle bestanden, Ruimte buiten de bibliotheek | **lijngrafiek Belasting**, **staafgrafiek Besparing per dag**, grootboekbalken |
| `ruimte-wachtrij` (`/av1/wachtrij`) | statusfilter, jobs per baan, details-lade, afbreken | voortgang |
| `ruimte-kwaliteit` (`/av1/kwaliteit`) | proefdraai (3 test-encodes), Dolby Vision-controle | — |
| `terugzetten` (`/quarantaine`) | modus (bewaren/direct verwijderen), gebruiksbalk, vrijgavecontrole-chips, tabel met terugzetten/vastzetten/verwijderen, bulk | **gebruiksbalk** |
| `aanvragen` (`/aanvragen`) | Seerr-zoeken & ontdekken, Mijn aanvragen, Levering; zonder Seerr: uitleg + koppelknop | — |
| `instellingen` (`/instellingen`) | één pagina met secties (zoeken, categorie-navigatie, `SettingField` per instelling, savebar, simulatie, uitzonderingen per titel) | — |
| `av1-instellingen` (`/av1/instellingen`) | encodeprofielen, grenzen, workers, GPU-meting, onderhoud | — |
| `koppelingen` (`/inrichten`) | tabs Overzicht · Downloads · Indexers · Paden · Media · Seerr · Mediaserver · Hernoemen; controleren → wijzigingen bekijken → toepassen | — |
| `kwaliteit` (`/kwaliteit`) | tabs Profielen · Optimaliseren · Instellingen; plannen met verdeling | **verdelingsbalk** (dry-run) |
| `migratie` (`/migratie`) | wizard (bronnen, runs, 10 stappen), AV1-historie, optimaliseren | **verdelingsbalk**, stepper |
| `dolby-vision` | beleid, profielondersteuning, proefconversies, afspeelketens | — |
| `release-lab` | titel kiezen → releases met afwijsredenen | — |
| `anime` | anime-routering (Alles/Open/Verplaatst), handmatige mapping, releasenaam ontleden | — |
| `ai-adviseur` (`/ai`) | provider, beleid, budget, privacy, casussen | budgetmeter |
| `systeem` (`/systeem`) | componenten, updates, back-ups, herstelbundel (+ buiten de NAS), achtergrondtaken, opslag + opslagtrend | **lijngrafiek Opslagtrend** |
| `hoe-werkt-het` | stroomschema (SVG) en woordenlijst | diagram (geen data) |
| `instanties`, `gebruikers`, `geheimen`, `api-sleutels`, `meldingen`, `beveiliging` (`/instellingen/*`) | lijsten en formulieren; API-sleutels met QR voor de Android-app; 2FA met herstelcodes | — |
| `inloggen` (`/login`), `eerste-start` (`/setup`) | gesplitst scherm (merkpaneel + formulier); setup in stappen | — |

### 3.3 Tabs (afgedrukt op 390/1440 × licht/donker, `tabs/`)

Activiteit: Problemen, Downloads, Wat is er gebeurd, Geschiedenis · Koppelingen: Downloads, Indexers, Paden, Media,
Seerr, Mediaserver, Hernoemen · Kwaliteit: Optimaliseren, Instellingen · Migratie: AV1-historie · titel: tabs 2–5 ·
aflevering: tabs 2–7.

### 3.4 Toestanden en dialogen (`toestanden/`)

| Toestand | Nu | Bron |
|---|---|---|
| Laden | skeletregels (`StateView kind=loading`, sr-only "Laden…"); KPI-tegels tonen ondertussen "—" | nagebootst |
| Leeg | `Inbox`-icoon in een vakje + titel ("Geen nieuwe afleveringen deze week", "Nog geen metingen") | echt |
| Fout | `CircleX`, "Er ging iets mis" + "De server gaf een fout terug. Je bestaande configuratie is niet gewijzigd." + Opnieuw proberen; HTTP-code, pad en "Correlatie-ID: …" in een uitklapper "Technische details" (niet kopieerbaar) | nagebootst (`fout-*`) |
| Offline (core weg) | `WifiOff`, "MediaGuard reageert even niet" (en-US: "Backend unreachable") | nagebootst (`offline-*`) |
| Verouderd | `.stale-mark` "Gegevens 2 minuten geleden — laatste verversing mislukt" (12 px, grijs) boven een lijst; niet op elke kaart | nagebootst (`verouderd-*`) |
| Nog niet beschikbaar | `PlugZap`, "Nog niet beschikbaar" — ook gebruikt voor een *onbereikbare* engine en voor `capability_unavailable` | echt (Ruimte) |
| Geen toegang / kijker | knoppen `disabled` met `title="Alleen-lezen rol"` (opacity .5) | echt (`kijker-*`) |
| Noodstop actief | rode banner (`role=alert`), knop in het profielmenu ingedrukt | echt (`noodstop-*`) |
| Bibliotheek vergrendeld | banner met slot en "Bibliotheek vrijgeven" (+ bevestiging) | echt |
| AV1-engine niet bereikbaar | Hardware-kaart: `WifiOff` in amber, twee zinnen die elkaar herhalen; KPI's "Nog niet beschikbaar" | echt |
| AV1-engine actief | banen met workers en voortgangsbalken, lopende fase zonder % als "bezig"-balk | nagebootst |
| en-US | zelfde schermen in het Engels | echt (`en-us-*`) |
| Dialogen | zoeken, profielmenu, meldingen, sneltoetsen, noodstop, bibliotheek vrijgeven, terugzetten, definitief verwijderen (getypte bevestiging) | echt (viewport-afdruk) |
| Inloggen / eerste start / niet gevonden | formulier; stappen; Nederlandse 404 met knop naar Overzicht | echt / nagebootst |

### 3.5 Gedeelde componenten web (bron → wat ze nu doen)

| Bestand | Componenten |
|---|---|
| `components/mg/primitives.tsx` | `StatusChip` (6 tonen; success `CircleCheck`, danger `CircleX`, accent `LoaderCircle`, **neutral `Pause`**), `LockedChip`, `Dot`, `Card` (+ `tone`), `PageHeader` (+ kruimel `›`), `Button` (5 varianten, `sm`, `iconOnly`, `pending`), `LinkButton`, `Switch`, `Segmented`, `Tabs` (links of `role=tablist`), `Field`, `InlineState`, `Progress`, `Skeleton`, `Kbd`, `Modal` |
| `components/mg/states.tsx` | `StateView` (loading/empty/error/offline/unavailable/forbidden/not_found), `ErrorState` (correlatie-ID in technische details), `ResourceView` (stale-markering alleen als er data is) |
| `components/mg/domain.tsx` | `KpiCard` (+ `Sparkline`, `staleNote`), `InstanceStatusChip/Logo/Card`, `QualityRibbon`, `Poster`, `MediaRow`, `SeasonHeatmap` (episodekaart), `Confidence`, `EvidenceCard`, `Trace`, `DiffViewer`, `DistributionBar`, `JobLaneCard`, `Stepper`, `Timeline`, `DangerDialog` (getypte bevestiging), `PendingAction` |
| `components/mg/charts.tsx` | `LineChart`, `BarChart` (eigen SVG, tooltip alleen met de aanwijzer) |
| `components/mg/*` overig | `attention.tsx` (Wacht op jou), `library-lock.tsx`, `toast.tsx`, `row-menu.tsx`, `setting-field.tsx`, `storage*.tsx`, `system-performance.tsx`, `update-card.tsx`, … |
| `lib/status.ts`, `lib/todo.ts`, `lib/health.ts` | toon per status, "Wacht op jou"-iconen (`CircleCheck`/`CircleHelp`), gezondheid zijbalk |

### 3.6 Grafieken en meters (web)

| Grafiek | Plaats | Nu |
|---|---|---|
| Belasting (`LoadChart` → `LineChart`) | Ruimte | 3–4 lijnen in één plot: CPU (`--mg-lane-cpu` = violet), GPU (`--mg-lane-gpu` = blauw), RAM (`--mg-viz-6`, gestippeld), cache (`--mg-viz-3`, gestippeld); vlak onder CPU; tooltip alleen met de aanwijzer; `role=img` met één zin |
| Besparing per dag (`SavingChart` → `BarChart`) | Ruimte | één reeks in **`--mg-viz-2` = groen** (status); ontbrekende dagen verdwijnen van de as (index-as, geen tijd-as); een niet-numerieke waarde wordt **0** |
| Opslagtrend (`StorageTrend` → `LineChart`) | Systeem | één reeks "Vrije ruimte" in **groen** met vlak; prognose als zin ("vol over ± 300 dagen") |
| Episodekaart (`SeasonHeatmap`) | titel/aflevering | cellen in **statuskleuren** (binnen = groen, AV1 = groen-blauw, ontbreekt = rood, zoekend = amber, onzeker = info), alleen kleur + `aria-label` per cel |
| Verdeling (`DistributionBar`) | Kwaliteit → Optimaliseren, Migratie | gestapelde balk met 6 delen in `viz-1…5` (incl. **groen, violet, rood**) + rand; legenda met aantallen |
| Grootboekbalken | Ruimte, Overzicht | horizontale balken per post met het bedrag als tekst (geen tabel nodig) |
| Meters (`Progress`) | wachtrij, quarantaine-gebruik, budget, migratie | vulling in accent of statuskleur |
| Zekerheid (`Confidence`) | identiteit, releases | getal + balkje in groen/amber/rood (inline) |

### 3.7 Android (`apps/android`, Compose) — schermen en toestanden

| Scherm | Toestanden in de afdrukken (`android/<naam>-<thema>-<360|393|393-volledig>.png`) |
|---|---|
| Overzicht | `overzicht` (3 dingen wachten), `overzicht-alles-in-orde`, `overzicht-noodstop`, `overzicht-verouderd` (niet bijgewerkt + meldingen uit + update) |
| Activiteit | `activiteit-nu`, `activiteit-problemen`, `activiteit-wat-is-er-gebeurd` |
| Bibliotheek | `bibliotheek`, `bibliotheek-detail`, `bibliotheek-ontbrekend` |
| Ruimte | `ruimte` (noodstopkaart, KPI's, banen GPU/CPU, wachtrij, "Waar ging de ruimte heen") |
| Aanvragen | `aanvragen-ontdekken`, `aanvragen-mijn`, `aanvraagvenster` |
| Instellingen | `instellingen`, `instellingen-waarschuwingen` (meldingen/batterij uit, ntfy-fout, update) |
| Koppelen (`PairingScreen`) | niet afgedrukt (geen screenshot-test; QR-scanner en adresinvoer) — NIET GEVALIDEERD |
| Systeemmeldingen (`work/Workers.kt`) | n.v.t. (systeem-UI) |

Componenten (`ui/Components.kt`, `nav/Bars.kt`): `MgCard`, `CardHeader`, `CountBadge`, `PageHeader`, `SectionLabel`
(kapitalen 11 sp), `Pill` (stip of icoon 12 dp + tekst 12 sp, één regel met ellips), `Tag`, `KpiTile`, `ListRow`,
`KeyValue`, `IconBubble`, `MgProgress`, `MgButton` (40/32 dp), `Segmented`, `MgSwitch`, `FilterChips` (horizontaal
scrollend), `MgSearchField`, `Timeline`, `StatusHero`, `EmptyState`, `LoadingState`, `ErrorState`, `Banner`,
`ConfirmDialog`; `TopBar` (logo, zoeken, pil "Aandacht n", profiel) en `BottomBar` (5 items, Material-iconen).
Thema: `MgColors` (25 kleuren × 2) uit `mg.css`, `MgType` (24/18/15/13/12/11 sp, Inter, `tnum` op alles), `MgShape`
(kaart 14 dp, knop 8, segment 10, tag 6, pil rond). 42 Material-iconen (`Icons.Outlined.*`).

---

## 4. Nulmeting

Bron: `docs/screenshots/peil/voor/metingen.json` (438 afdrukken), `toetsenbord.json` (20 doorlopen) en de
Android-afdrukken. Alle getallen hieronder zijn gemeten op 9 november 2026 in Chromium (web) of berekend uit de
waarden in `Theme.kt` (Android); niets is geschat.

### 4.1 Web per scherm (licht; 360 en 1440)

| Scherm | Overloop 360 / 390 | Doelen < 44 px (360 · 1440) | Tekst < 14 px, elementen (360 · 1440) | Hoogte 360 (px) | Inline styles | Chips groen/amber/rood/info/neutraal (1440) | axe naast 2.5.3 (1440) |
|---|---|---|---|---|---|---|---|
| aanvragen | 0 / 0 | 2/17 · 20/21 | 21 · 29 | 800 | 14 | 0/0/0/0/0 | — |
| activiteit | 0 / 0 | 2/20 · 18/24 | 33 · 41 | 1 379 | 15 | 0/0/0/0/0 | — |
| aflevering | 0 / 0 | 5/24 · 27/28 | 44 · 52 | 1 354 | 22 | 1/1/0/0/0 | — |
| ai-adviseur | 0 / 0 | 7/23 · 26/27 | 43 · 51 | 1 924 | 18 | 0/0/0/0/1 | — |
| anime | 0 / 0 | 5/24 · 27/28 | 32 · 40 | 1 807 | 17 | 0/0/0/0/0 | — |
| api-sleutels | 0 / 0 | 3/14 · 17/18 | 19 · 27 | 1 034 | 12 | 0/0/0/0/0 | — |
| av1-instellingen | 0 / 0 | 4/21 · 24/25 | 33 · 41 | 2 500 | 25 | 0/0/0/0/0 | — |
| beveiliging | 0 / 0 | 6/25 · 28/29 | 70 · 78 | 2 792 | 33 | 7/0/0/0/1 | — |
| bibliotheek | 0 / 0 | 2/26 · 34/35 | 56 · 70 | 1 603 | 40 | 1/2/0/1/0 | — |
| bibliotheek-film | 0 / 0 | 5/24 · 27/28 | 44 · 52 | 1 201 | 25 | 0/0/0/1/1 | — |
| bibliotheek-titel | 0 / 0 | 9/51 · 29/55 | 114 · 125 | 1 816 | 78 | 19/1/2/0/1 | `nested-interactive`, `target-size` |
| dolby-vision | 0 / 0 | 4/31 · 30/35 | 68 · 76 | 3 181 | 33 | 1/2/2/1/4 | — |
| gebruikers | 0 / 0 | 3/19 · 22/23 | 32 · 45 | 1 158 | 16 | 3/0/0/0/0 | — |
| geheimen | 0 / 0 | 3/19 · 22/23 | 36 · 48 | 1 147 | 14 | 4/0/0/0/0 | — |
| hoe-werkt-het | 0 / 0 | 2/9 · 12/13 | 29 · 37 | 3 284 | 11 | 0/0/0/0/0 | — |
| instanties | 0 / 0 | 3/25 · 28/29 | 100 · 108 | 1 809 | 15 | 0/0/0/0/4 | — |
| instellingen | 0 / 0 | 24/87 · 65/91 | 168 · 176 | 9 307 | 59 | 0/0/0/0/11 | — |
| kalender | 0 / 0 | 4/25 · 24/29 | 42 · 50 | 1 164 | 18 | 0/0/0/0/0 | — |
| koppelingen | 0 / 0 | 8/28 · 31/32 | 52 · 73 | 2 426 | 18 | 0/0/2/0/1 | `scrollable-region-focusable` |
| kwaliteit | 0 / 0 | 5/30 · 33/34 | 73 · 85 | 2 466 | 34 | 0/0/0/0/7 | — |
| meldingen | 0 / 0 | 8/45 · 48/49 | 63 · 71 | 2 381 | 39 | 0/0/0/0/1 | — |
| migratie | 0 / 0 | 3/16 · 20/21 | 31 · 40 | 1 858 | 17 | 0/0/0/0/0 | — |
| ontbrekend | 0 / 0 | 14/45 · 48/49 | 117 · 134 | 3 755 | 38 | 0/0/6/0/1 | — |
| overzicht | 0 / 0 | 4/29 · 24/33 | 60 · 68 | 2 936 | 27 | 0/0/0/0/1 | — |
| release-lab | 0 / 0 | 3/10 · 13/14 | 12 · 20 | 800 | 12 | 0/0/0/0/0 | `aria-valid-attr-value` |
| ruimte | 0 / 0 | 5/23 · 24/27 | 56 · 64 | 3 121 | 61 | 0/0/0/0/0 | — |
| ruimte-kwaliteit | 0 / 0 | 5/17 · 20/21 | 28 · 36 | 1 246 | 15 | 0/0/0/0/0 | — |
| ruimte-wachtrij | 0 / 0 | 5/27 · 30/31 | 28 · 36 | 926 | 14 | 0/0/0/0/0 | — |
| systeem | 0 / 0 | 11/36 · 39/40 | 91 · 99 | 4 656 | 31 | 0/0/2/0/0 | — |
| terugzetten | 0 / 0 | 12/32 · 37/38 | 62 · 75 | 1 864 | 34 | 2/0/1/2/2 | — |

- **Overloop: 0** op alle 438 afdrukken, ook op 360 px. MediaGuard loopt dus al goed om (eerdere rondes); dat moet zo
  blijven (nieuwe test meet t.o.v. de apparaatbreedte, ADR-0016 punt 12).
- **Doelen < 44 px:** op 1440 **847 van 950** (89 %): zijbalkitems 235 × 34, knoppen `btn--sm`, links in tekst-rijen,
  selectievakjes. Op 360 **176 van 822** (21 %): de 44 px-regel geldt in `mg.css` alleen ≤ 860 px; wat overblijft zijn
  de pil "Aandacht" (36 px hoog), losse links ("Bekijken" 53 × 19) en schakelaars.
- **Tekst < 14 px:** op 1440 **1 947 tekstelementen** op de 30 schermen (13 px 912×, 12 px 303×, 12,5 px 247×, 11 px
  216×, 13,5 px 151×, 11,5 px 73×, 10 px 30×, 10,6 px 8×, 12,4 px 7×); op 360 **1 657**, kleinste **10 px** (teller op
  de bel, labels in de banen). Bronnen: `mg.css` 112 declaraties < 14 px, `av1.css` 19, inline `fontSize` 189 van de 212.
- **Lettertype:** Inter (via `next/font/local`) en JetBrains Mono voor code (na ADR-0015 echt toegepast). Peil:
  systeemfont.
- **Paginahoogte op 360:** Instellingen 9 307 px, Systeem 4 656, Ontbrekend 3 755, Hoe werkt het 3 284, Dolby Vision
  3 181, Ruimte 3 121.
- **Statusvormen (1440, 30 schermen):** 38 groene chips (o.a. 19 op de titelpagina: "Binnen"; 7 op Beveiliging), 15 rode
  (o.a. 6× "Ontbreekt" op Ontbrekend), 30 groene stippen (de live-regel op elk scherm), 41 `circle-check`-iconen (ook in
  "Wacht op jou"), 9 `pause`-iconen voor neutraal/onbekend.
- **Toestanden:** *laden* toont KPI-tegels met "— Nog niet beschikbaar", "MediaGuard v—" en in de kop een **groene** stip
  met "—"; *offline* toont "MediaGuard reageert even niet" per kaart; *fout* "Er ging iets mis" met de correlatie-ID in
  een uitklapper; *verouderd*: na 70 s waarin alle verzoeken mislukten, toont Overzicht nog steeds een verse stand en
  "Live" (de al open live-verbinding valt in deze nabootsing niet weg). De KPI-tegels en "Nieuwe afleveringen" verversen
  niet periodiek en kunnen dus nooit "verouderd" worden; "Nu bezig" ververst elke 30 s (met live-verbinding 120 s). Op
  Bibliotheek verscheen in 70 s ook geen markering (`stale_mark` = 0).
- **Grafieken:** zonder AV1-engine tonen Ruimte en Systeem geen enkele grafiek (lege toestanden). Met de nagebootste
  engine: `Besparing per dag` met 13 groene staven voor 14 dagen (de ontbrekende dag verdwijnt van de as) en
  `Belasting` met as en raster; de nagebootste meetreeks had niet de vorm die de engine levert, dus zonder lijnen (dat
  zegt niets over de app). Geen grafiek heeft `tabindex` of een tabel.

### 4.2 axe-core 4.14 (Peil-set `wcag2a, wcag2aa, wcag21a, wcag21aa, wcag22aa`)

| Regel | Ernst | Waar | Oorzaak |
|---|---|---|---|
| `label-content-name-mismatch` (2.5.3) | serious | **elk scherm** (422 knopen over alle afdrukken) | profielknop toont "BE" maar heet "Profiel" |
| `nested-interactive` (4.1.2) | serious | titelpagina (3), Activiteit → Problemen (1), ook en-US | interactief element in een `summary` (`details[open] > summary`) |
| `target-size` (2.5.8) | serious | titelpagina (3: "Seizoen 3 volgen"-schakelaar), Activiteit → Problemen (`.link-more`) | doel < 24 px zonder ruimte eromheen |
| `aria-valid-attr-value` (4.1.2) | **critical** | Release Lab (1) | `aria-*` verwijst naar een id dat niet bestaat (`#_r_1_-q`) |
| `color-contrast` (1.4.3) | serious | dialogen noodstop, terugzetten, bibliotheek vrijgeven — **donker**, 390 en 1440 | `.btn--danger-solid`: wit op `--mg-danger` (3,18:1); de bestaande e2e meet alleen 7 routes zonder dialoog en ziet dit niet. De gedeelde aliaslaag lost het op (`destructive`/`-foreground`: 9,66 / 7,61:1) |
| `button-name` (4.1.2) | **critical** | profielmenu op 390 (licht en donker) | de noodstopknop (`.estop`) heeft op de telefoon geen toegankelijke naam |
| `scrollable-region-focusable` (2.1.1) | serious | Koppelingen, titel → Kwaliteit (390) | `.table-wrap` scrollt maar is niet met het toetsenbord te bereiken |
| één h1 | — | niet-gevonden-pagina: **0** h1 | — |

Geen JavaScript-fouten op de 438 afdrukken.

### 4.3 Toetsenbord (`toetsenbord.json`)

- Skiplink "Naar inhoud" is overal de eerste Tab-stop.
- Focusstijl: `outline 2px` in één kleur (`#1D66C9` licht, `#8EC1FF` donker); invoervelden krijgen een schaduw van 3 px
  zonder outline. Peil: tweekleurige ring (3 px `--ring` + 2 px `--ring-halo`, ≥ 5:1).
- **Focus achter de vaste ondernavigatie op 390 px** (WCAG 2.4.11), per thema: Overzicht 4× (de "Nieuwe
  afleveringen"-rijen), Instellingen 4× (o.a. een invoerveld), Terugzetten 3×, Systeem 3×, Bibliotheek 1×. Op 1440: 0×.
- Grafieken zijn niet met het toetsenbord te bedienen (geen `tabindex`, tooltip alleen met de aanwijzer).

### 4.4 Palet (dataviz-validator, `validate_palette.js`)

| Palet | Licht (`#FFFFFF`) | Donker |
|---|---|---|
| huidig `--mg-viz-1…6` | **FAIL** chroma (`#0E7F8C` 0,091); CVD **WARN** 7,6 (`#B86E0A`↔`#167650`) | **FAIL** lichtheidsband (alle 6 te licht, op `#141C29`); CVD **WARN** 7,2 |
| huidige reeksen Belasting (CPU, GPU, RAM, cache), alle paren | **FAIL**: GPU `#1D66C9` ↔ CPU `#7646C9` ΔE **0,1** (deutan), normaal zicht 12,0 | **FAIL**: `#4F9CF9` ↔ `#B28CF0` ΔE **1,6** (protan), normaal zicht 12,2 |
| Peil `chart-1…5` (aangrenzend) | **PASS** alle vijf controles (CVD 8,8; normaal zicht 17,7; alle ≥ 3:1) | **PASS** (CVD 8,4; normaal zicht 19,3) op `#141920` |
| Peil, 4 lijnen alle paren (blauw, oranje, aqua, oker) | **FAIL**: oranje ↔ oker ΔE 1,6 (deutan) | **FAIL**: ΔE 4,8 |
| Peil, 3 lijnen alle paren (GPU, CPU, RAM) | **PASS** (CVD 10,0; normaal zicht 23,0) | **PASS** (CVD 9,4; normaal zicht 20,9) |
| Peil, 2 lijnen "Geheugen" (RAM aqua, cache oker) | **PASS** (CVD 8,8; normaal zicht 19,7) | **PASS** (CVD 8,4; normaal zicht 19,8) |

Gevolg: de belastingsgrafiek krijgt maximaal drie lijnen (§7, ADR-0016 punt 10).

### 4.5 Android

| Meting | Uitkomst | Peil |
|---|---|---|
| Lettermaten (`MgType`) | meta 12 sp, small 13, eyebrow 11 (kapitalen), body 15; `Pill` 12 sp met één regel en ellips | ≥ 14 sp; body 16; geen ellips op inhoud |
| Aanraakdoelen | `MgButton` 40 dp (klein 32 dp); icoonknoppen 40 dp; `FilterChips` en lijstrijen zonder `minimumInteractiveComponentSize` | 48 dp |
| Tekstcontrast (berekend uit `Theme.kt`) | tweede tekstniveau licht 5,57:1 (`faint` op kaart), donker 5,42:1; pil-tekst op eigen vlak 4,65–5,48:1 | ≥ 7:1 (beide tekstniveaus) |
| Randen | kaartrand 1,41:1 (licht) / 1,42:1 (donker) — ook de rand van de **secundaire knop** (`line`); sterke rand 3,60 / 3,10:1 | decoratief mag; bedienbaar ≥ 3:1 (`border-strong`) |
| Status | gekleurde stip + woord (`Pill(dot = true)`), noodstop "Uit" groen, "Alles in orde" groen | icoon + woord + vorm, nooit groen voor niet-gezondheid |
| Baankleuren | GPU violet, CPU blauw (web: andersom), percentagetekst in baankleur | GPU blauw, CPU oranje, tekst inkt |
| Verouderd | amber kaart "Niet bijgewerkt sinds …", KPI-tegels zien er vers uit | stippelrand + arcering op elke kaart, status Onbekend |
| Filterchips | horizontaal scrollend; de laatste chip half zichtbaar ("Wat is er g") op 393 dp | omlopen of zichtbare rand-fade |
| Lettertype | ingebouwde Inter, `tnum` op elke stijl | Roboto; `tnum` alleen voor kolommen/tijden |
| Overloop / afgekapte tekst | geen horizontale overloop gezien op 360 en 393 dp; wel ellipsen in pillen | — |

**NIET GEVALIDEERD (Android):** echte telefoon, TalkBack, grote systeemletter (fontScale), fel zonlicht.

---

## 5. Kritiek op de huidige stand (design-critique)

**Eerste indruk.** Een rustige, verzorgde beheer-UI met veel uitleg in gewone taal en eerlijke lege toestanden. Op
Overzicht trekt de rode hero "6 dingen hebben je hulp nodig" meteen alle aandacht, terwijl vier van de zes punten
gewone inrichtingstaken zijn (Seerr koppelen, herstelcode instellen) en geen storing. Daaronder concurreren een rode
"2", een amber "6" en een groene stip "Live" om dezelfde aandacht. Op de telefoon worden titels in "Wacht op jou"
afgekapt ("Vervangen van b…") en staat de uitleg in een smalle kolom naast een knop.

### 5.1 Bruikbaarheid

| Bevinding | Ernst | Aanbeveling (Peil) |
|---|---|---|
| Hero is rood voor een mix van fouten en inrichtingstaken; totaalstatus en "wat actie nodig heeft" zijn één ding | 🔴 | SysteemStatusPaneel (slechtste onderdeel: bv. **Beperkt — AV1-engine reageert niet**) los van "Wat actie nodig heeft" (storing > gate > inrichting), elk item met eigen icoon |
| "Wacht op jou"-rijen met `CircleCheck` (vinkje = klaar) | 🔴 | per soort een icoon: storing `octagon-x`, gate `lock`, inrichting `settings`/`link-2-off`, info `info` (ONTWERPTAAL §7: nooit `circle-check` voor een open taak) |
| Onbereikbare engine → "Nog niet beschikbaar" op 3 KPI-tegels | 🔴 | **Onbekend — AV1-engine reageert niet** (stippelpil + reden); "Nog niet beschikbaar" alleen voor een fase |
| Verouderd alleen als grijze regel boven een lijst; KPI's en Overzicht blijven er vers uitzien | 🔴 | Versheid-patroon per kaart: gestippelde rand, "Laatst bekend · 10:12", waarde in `stale-ink`, bediening uit met reden (gebruikt `resource.stale`/`fetchedAt`, geen API-wijziging) |
| Fout zonder code en met de correlatie-ID alleen in een uitklapper, niet kopieerbaar | 🟡 | Foutpaneel: wat + waarom + wat je kunt doen, `code`, KopieerVeld "Trace-ID" (= `correlation_id`) |
| Titels met ellips (lijsten, media, kalender, jobs) en tabs die buiten beeld vallen (Ruimte op 360) | 🟡 | omlopen; tabs < 768 px als omlopende rij (COMPONENTEN §21) |
| Uitgeschakelde knoppen met opacity .5 en de reden alleen in `title` (kijker) | 🟡 | `aria-disabled`, gestippelde rand, reden zichtbaar ernaast (`aria-describedby`) |
| Tellers en aantallen in statuskleur, ook bij nul: "0 mislukt" rood (Terugzetten), "Fouten 2" rood, "Ontbrekend 6" amber, pil "Aandacht" rood; tijdens het laden zelfs een **groene** stip met "—" in de kop | 🟡 | getal in inkt; status als badge ernaast en alleen als er iets is ("0 mislukt" = neutraal); laden = skelet, nooit een gekleurde stip |
| Beleid als groene melding ("Originelen blijven 7 dagen bewaard.") en een wachtende voorwaarde als amber ("Herstel nog niet gereed", "Bibliotheek nog vergrendeld") | 🟡 | beleid = `info`-vlak of BeleidsSlot; voorwaarde = GateBlokkade (`lock`, neutraal-sterk, "Dit is geen storing; er wacht een voorwaarde.") |
| Engine-kaart herhaalt dezelfde zin twee keer ("MediaGuard probeert het automatisch opnieuw") | 🟢 | één zin in het Foutpaneel; de tweede weg |

### 5.2 Visuele hiërarchie

- **Wat eerst opvalt:** de rode hero en de gekleurde KPI-getallen. Het zou de totaalstatus moeten zijn, met daaronder
  wat actie vraagt. Peil: PageHeader met bijgewerkt + Vernieuwen → SysteemStatusPaneel → "Wat actie nodig heeft" →
  kaartraster.
- **Leesvolgorde:** op desktop logisch (raster); op de telefoon lang (Overzicht 2 936 px, Systeem 4 656 px,
  Instellingen 9 307 px op 360) door de smalle tekstkolommen naast knoppen en alles uitgeklapt. Peil: op < 640 knop onder de tekst, volle breedte.
- **Nadruk:** veel kapitalen-labels (`eyebrow`, 11 px met letterspatiëring) en drie grijsniveaus (`--mg-text`,
  `-2`, `-3`). Peil: twee tekstniveaus, kleinste tekst 14 px, sectiekop `text-xl`.

### 5.3 Consistentie

| Element | Probleem | Peil |
|---|---|---|
| Statuswoorden | "Verminderd", "Offline", "Core offline", "Alles in orde", "In orde", "Geslaagd", "Klaar" naast "Gezond"; en-US "Backend unreachable" tegenover NL "MediaGuard reageert even niet" | vaste woorden §11.1: Gezond · Beperkt · Storing · Onbekend (+ reden) · Verouderd · Gepauzeerd; totaal: Alles gezond · Beperkt · Storing · Deels onbekend (en-US: Healthy · Degraded · Down · Unknown · Stale · Paused · All healthy · Partly unknown) |
| Baankleur | web: GPU blauw, CPU **violet**; Android: GPU **violet**, CPU blauw | GPU `domain-lane-gpu` (= chart-1, blauw), CPU `domain-lane-cpu` (= chart-2, oranje); op web en Android gelijk; tekst altijd inkt |
| Live-indicator | groene stip "Live" (verbonden ≠ gezond) | neutraal `radio` "Live · 11:56"; onderbroken = stippelpil Onbekend |
| Tellers | rood (Activiteit, pil "Aandacht"), amber (bel) | inktpil; naam in de toegankelijke tekst; nooit rood |
| Typografie | 9–13,5 px in 9 maten, inline; Inter + JetBrains Mono | Tailwind-schaal; `text-sm` (14) minimum; systeemfont |
| Kleur in code | 69× `var(--mg-…)` inline, 8 hex in TSX, 94 hex in mg.css | alleen tokens via utilities; lintregel tegen hex |
| Breekpunt | 860 px | `lg` = 1024 px (platform) |
| Web ↔ Android | zelfde app, andere status- en baankleuren, Android zonder statusicoon (stip) | één statuscomponent per platform met dezelfde betekenis (§10.2) |

### 5.4 Toegankelijkheid (WCAG 2.1 AA, accessibility-review)

**Standaard:** WCAG 2.1 AA (+ Peil: 2.2 AA-set, 44 px, ≥ 14 px, 7:1) · **Datum:** 9 november 2026 · bron §4.

| # | Bevinding | Criterium | Ernst | Maatregel |
|---|---|---|---|---|
| 1 | Noodstopknop in het profielmenu zonder toegankelijke naam op de telefoon | 4.1.2 | 🔴 | zichtbare tekst "Noodstop" ook op < 861 px (fase 1b) |
| 2 | `aria-*` met ongeldige verwijzing (Release Lab) | 4.1.2 | 🔴 | id-verwijzing herstellen (fase 1b; geen gedragswijziging) |
| 3 | Wit op `--mg-danger` (3,18:1) in de gevaarknop van dialogen, donker | 1.4.3 | 🔴 | aliaslaag (fase 1a) |
| 4 | Focus bedekt door de ondernavigatie (1–4× per scherm op 390) | 2.4.11 (2.2) | 🟡 | `scroll-padding` (peil-mg.css b) |
| 5 | Profielknop: zichtbaar "BE", naam "Profiel" (elk scherm) | 2.5.3 | 🟡 | initialen + sr-only "profiel en thema" in de naam |
| 6 | Interactief element in `summary`; schakelaar "Seizoen n volgen" en `.link-more` < 24 px | 4.1.2, 2.5.8 | 🟡 | schakelaar buiten de `summary`; doelen ≥ 44 px (fase 2) |
| 7 | Scrollende tabel niet bereikbaar met het toetsenbord | 2.1.1 | 🟡 | `.table-wrap` `tabindex=0` + `role=region` + naam, of kaartenlijst < 768 |
| 8 | Grafieken zonder toetsenbord en tabel; tooltip alleen met de aanwijzer | 2.1.1, 1.1.1 | 🟡 | §7, §9.2 |
| 9 | Status als kleur-alleen in de episodekaart; groene staven als "besparing"; ontbrekende dag verdwijnt | 1.4.1 | 🟡 | §7 (vorm + woord + tabel) |
| 10 | Niet-gevonden-pagina zonder h1 | 1.3.1, 2.4.6 | 🟢 | h1 "Niet gevonden" |
| 11 | Tekst 10–13,5 px (1 947 elementen op 1440) en 847 doelen < 44 px op desktop | (Peil) | 🟡 | fase 2 |
| 12 | Uitgeschakelde knoppen met opacity .5 en de reden alleen in `title` | 1.4.3 / 3.3.2 | 🟢 | Peil `aria-disabled` + zichtbare reden |
| 13 | Android: tekstcontrast 4,6–5,6:1, knoprand 1,4:1, knoppen 32–40 dp, stip als status | 1.4.11, 2.5.5, 1.4.1 | 🟡 | §10 |

**Toetsenbord (web):** skiplink eerst, logische volgorde, dialogen met focusval en Escape, sneltoetsen met `?`-overzicht.
**Schermlezer:** niet getest (NVDA/TalkBack); `role=img` + één zin bij grafieken, `aria-label` per cel in de
episodekaart (één cel per Tab-stop als het een link is).

**Prioriteit:** (1) knopnaam noodstop en de ongeldige `aria`-verwijzing — blokkeren schermlezergebruikers; (2) contrast
gevaarknop en bedekte focus — fase 1a lost ze op; (3) grafieken, episodekaart en doelgroottes — fase 2.

### 5.5 Wat goed is en blijft

- De eerlijke toon ("geen nepdata", "MediaGuard zoekt ze zelf", "het origineel blijft ongewijzigd"): dat is
  Peil-principe 1. **Teksten met domeinbetekenis blijven.**
- Integriteitsslot, vrijgavecontrole en getypte bevestiging: krijgen alleen Peil-stijl (BeleidsSlot, GateBlokkade,
  GoedkeuringsPagina-stap-2-stijl in de dialoog).
- Dichtheid op desktop via tabellen: Peil haalt dichtheid uit compacte tabellen, niet uit kleine tekst.
- Android: licht/donker volgt het systeem, skeletten in plaats van een draaiwiel, lege toestanden met uitleg.

---

## 6. Vertaling naar Peil (web)

### 6.1 Tokens

**Fase 1** gebruikt de gedeelde, gegenereerde aliaslaag `design/ontwerptaal/aliaslagen/mediaguard-mso.css` (niet met de
hand wijzigen; MSO gebruikt hem al). Die zet alle `--mg-*` op Peil: `--mg-bg → --background`, `--mg-surface → --card`,
`--mg-raised → --popover`, `--mg-sunken → --muted`, `--mg-text → --foreground`, `--mg-text-2/3 → --muted-foreground`,
`--mg-border* → border/border-strong`, `--mg-accent → --primary`, `--mg-success/warning/danger/info →
healthy/degraded/down/info` (+ `-tint → -bg`), `--mg-viz-1…6 → chart-1…5 + chart-other`, `--mg-lane-gpu/cpu →
domain-lane-gpu/cpu`, `--mg-font/mono → font-sans/mono`, radii, schaduwen, `--mg-fs-11…13 → 14 px`; plus correcties voor
gevaarknop, tellers, stepper, 44 px op de telefoon, noodstop/pauze, diff, zekerheid, kwaliteitslabels, banen en
fasebalken.

**MediaGuard-eigen correcties** komen in een nieuw, repo-eigen bestand `apps/web/styles/peil-mg.css` (ná de aliaslaag),
omdat ze in de gedeelde laag niet thuishoren of daar niet werken. Dezelfde nummering als MSO's `peil-mso.css` waar het
hetzelfde probleem is:

| # | Correctie | Waarom |
|---|---|---|
| a | `:focus-visible { outline: var(--focus-width) solid var(--ring); outline-offset: var(--focus-offset); box-shadow: 0 0 0 var(--focus-offset) var(--ring-halo); border-radius: inherit }` | `mg.css:148` (2 px, één kleur, ongelaagd) wint altijd van Peils ring in `@layer base` |
| b | `html { scroll-padding-top: calc(var(--mg-topbar-h) + .75rem) }` en ≤ 860 px `scroll-padding-bottom: calc(var(--mg-bottomnav-h) + env(safe-area-inset-bottom) + .75rem)` | focus niet achter kop/ondernavigatie (WCAG 2.4.11); Peils eigen regel rekent met 64 px en `lg` |
| c | `.list-title, .list-sub, .media-title, .media-sub, .job-file, .cal-ep strong { white-space: normal; overflow: visible; text-overflow: clip; overflow-wrap: anywhere }` | geen ellips op inhoud (ONTWERPTAAL §12) |
| d | ≤ 48rem: `.tabs { flex-wrap: wrap; overflow: visible; mask-image: none }`; `.segmented { flex-wrap: wrap }` | tabs vallen nu buiten beeld (Ruimte, titel, aflevering) |
| e | `.confidence .bar > span { background: var(--foreground) !important }` | de aliasregel verliest van de inline `style.background` (groen/amber/rood); zekerheid is inkt (T-24) |
| f | `.kpi--success .kpi-value, .kpi--warning .kpi-value, .kpi--danger .kpi-value { color: var(--foreground) }` | een getal is nooit in statuskleur; de status komt als badge (fase 1b) |
| g | `.btn:disabled, .btn[aria-disabled="true"], .switch:disabled, .estop:disabled { opacity: 1; border-style: dashed; background: var(--muted); color: var(--disabled-foreground) }` | geen opacity op tekst (§4.1 regel 10) |
| h | `.stale-mark { color: var(--stale-ink); font-size: .875rem; border: 2px dashed var(--stale); background-image: repeating-linear-gradient(135deg, var(--stale-hatch) 0 2px, transparent 2px 6px); border-radius: 999px; padding: .125rem .5rem }` | verouderd herkenbaar zonder componentwijziging |
| i | `[data-testid="library-lock-banner"] > * { background: var(--gate-bg); border: 2px solid var(--gate); color: var(--gate-ink) }` en het slot-icoon in `--gate` (het is een `InlineState tone="info"`) | vergrendeld = gate (slot, neutraal-sterk), geen info/violet |
| j | `[data-testid="estop-banner"] { background: var(--paused-bg); border-color: var(--paused); color: var(--paused-ink) }` | noodstop actief = bewuste stop (Gepauzeerd-vorm), geen gemeten falen; de knop zelf blijft `destructive` (aliaslaag) |
| k | `.dot--success` in `.health-line` en `.top-pill` → `background: var(--foreground)`; `.nav-count--danger/--warning`, `.top-pill--danger/--warning` → inktpil/inktrand (`.icon-btn .dot-badge` doet de aliaslaag al) | live/verbonden en tellers zijn nooit groen of rood; in fase 1b krijgt elke stip een icoon |
| l | `globals.css`: het eigen `:root { --mg-font… }`-blok weg | systeemfont (O-01); `next/font/local` Inter/JetBrains Mono blijven tot fase 2c geladen maar ongebruikt |
| m | `.heat-cell--*` ongewijzigd (kleur-alleen) | ONTWERPTAAL §13.3: textuur + tabel komen in fase 2 (§7) |

**Fase 2** importeert `ontwerptaal.css` als Tailwind-bron; componenten gebruiken alleen Peil-utilities (`bg-card`,
`text-muted-foreground`, `border-border-strong`, `text-degraded-ink bg-degraded-bg border-degraded`, `arcering`,
`cijfers`, `min-h-touch`, `z-nav` …). `--color-*: initial` voorkomt losse kleuren (`text-red-500` bestaat dan niet).

### 6.2 Statusvocabulaire (bron → Peil)

| Bron in MediaGuard | Waarde | Nu | Peil (vorm · icoon · woord NL / en-US) |
|---|---|---|---|
| Instanties, componenten (`INSTANCE_TONE`, `status.*`) | healthy | groen "Gezond" | `healthy` · `circle-check` · **Gezond / Healthy** (alleen gemeten gezondheid) |
| | degraded | amber "Verminderd" | `degraded` · `triangle-alert` · **Beperkt / Degraded** |
| | offline | rood "Offline" | `down` · `octagon-x` · **Storing / Down** + reden ("Niet bereikbaar") |
| | unknown | grijs + **Pause** "Onbekend" | `unknown` · `circle-help` · stippelpil · **Onbekend / Unknown** + reden |
| | suspended | — | `paused` · `circle-pause` · **Gepauzeerd / Paused** |
| Zijbalkvoet (`health.*`) | geen todo / todo / offline / laden | groene stip "Alles in orde" · rode/amber stip "6 dingen vragen aandacht" · rode stip "Core offline" · grijze stip "Laden…" | totaal **Alles gezond / All healthy** · **Beperkt** / **Storing** volgens het slechtste onderdeel · **Onbekend — MediaGuard-core reageert niet / Unknown — MediaGuard core is not responding** · laden = skelet; "6 dingen wachten op jou" als aparte link (inktteller) |
| Live (`live.*`) | live / connecting / reconnecting / offline | stip groen / amber / amber / rood | neutraal `radio` **Live · 11:56** · `loader-circle` **Verbinden… / Connecting…** · stippelpil `unknown` **Onderbroken · stand van 11:56 / Interrupted · as of 11:56** |
| Taken (`status.queued…cancelled`) | queued / running / retry_scheduled / failed / succeeded / cancelled | grijs+Pause / blauw / amber / rood / groen "Geslaagd" / grijs | neutraal `clock` **In wachtrij / Queued** · neutraal `loader-circle` **Bezig / Running** · neutraal `rotate-ccw` **Opnieuw gepland / Retry scheduled** · `down` `octagon-x` **Mislukt / Failed** · neutraal `check` **Afgerond / Completed** · neutraal `x` **Geannuleerd / Cancelled** |
| AV1-jobs (`av1.queue.s.*`) | wachtend / bezig / verifiëren / klaar / mislukt / gepauzeerd | idem, "Klaar" groen | als taken; **"Klaar" → "Uitgevoerd en gecontroleerd / Done and verified"** alleen als de verificatie geslaagd is (dat zegt de engine al), anders "Afgerond"; gepauzeerd = `paused` |
| Beschikbaarheid aflevering/film (`status.available/missing/searching/expected/unmonitored/uncertain`) | — | "Binnen" groen · "Ontbreekt" rood · "Zoekend" amber · "Verwacht" blauw met draaiicoon · "Niet gemonitord" grijs · "Onzeker" info | neutraal `check` **Binnen / Available** · neutraal-sterk `file-x` **Ontbreekt / Missing** · neutraal `search` **Zoekt / Searching** · neutraal `calendar-clock` **Verwacht / Expected** · neutraal `eye-off` **Niet gevolgd / Not monitored** · `unknown` `circle-help` **Onzeker — identiteit niet bevestigd / Uncertain — identity not confirmed**. Geen statuskleur: beschikbaarheid is geen gezondheid (ADR-0016 punt 6) |
| Bibliotheekchips (`Compleet`, `2 ontbreken`, `Kan beter`) | — | groen / amber / info | neutraal `check` **Compleet**, neutraal-sterk `file-x` **2 ontbreken**, neutraal `trending-up` **Kan beter** |
| Vrijgavecontrole quarantaine | geslaagd / mislukt / niet gecontroleerd | groen / rood / grijs | `healthy` **Geslaagd** (dit is een gemeten controle) · `down` **Mislukt** + reden · `unknown` **Nog niet gecontroleerd / Not checked yet** |
| Bibliotheek vergrendeld | — | info-banner | GateBlokkade: `lock` + **Bibliotheek vergrendeld** — "Dit is geen storing; er wacht een voorwaarde." + werkwoordknop **Bibliotheek vrijgeven** (bestaande bevestiging) |
| Integriteitsregels (`integrity_locked`, `LockedChip` "Integriteit") | — | slot-chip | BeleidsSlot: `file-lock` + **Altijd aan (integriteit)** — geen schakelaar in de DOM |
| Noodstop | uit / aan | groen "Uit" (Android), rode banner (web) | uit = neutraal **Uit**; aan = `paused`-vorm `circle-pause` **Noodstop actief / Emergency stop active** + bestaande uitleg; de knop blijft `destructive` |
| Hero/aandacht (`lib/todo.ts`) | error / warning / info | rode kaart, `CircleCheck`/`CircleHelp` | lijst "Wat actie nodig heeft / What needs action" met per soort `octagon-x` · `triangle-alert` · `lock` · `settings` · `info`; de kaartrand volgt het slechtste item, nooit rood voor alleen inrichting |
| KPI "AV1-besparing" bij onbereikbare engine | — | "— Nog niet beschikbaar" | **Onbekend — AV1-engine reageert niet / Unknown — AV1 engine not responding** |
| `capability_unavailable` / 404 op een optionele route | — | `PlugZap` "Nog niet beschikbaar" | `circle-slash` **Niet beschikbaar — MediaGuard-core biedt dit niet aan / Not available — MediaGuard core does not offer this** + code + endpoint |
| Kwaliteitslinten (`QualityRibbon` DV/HDR/AV1) | — | violet/amber/groen | neutraal (aliaslaag al); kwaliteit is geen status |
| Ernst meldingen (`severityColor`, bel) | error/warning/info/success | rood/amber/blauw/groen stip | ErnstLabel (balk + woord): **Kritiek/Hoog/Middel/Laag/Info**; "success" = neutraal `check` |

### 6.3 Componenten → shadcn/ui (fase 2) met behoud van API

| MediaGuard-component | shadcn / Peil-component | Behouden (API, testids, hookklassen) | Opmerkingen |
|---|---|---|---|
| `StatusChip` | Badge-basis → **StatusBadge** (C-03) en neutraal **Label** | props `tone/icon/pending/size/outline`; klasse `.chip` blijft (e2e `werk-mediaserver` telt `.chip`-overloop) | `tone` via §6.2; `success` alleen gezondheid; `neutral` zonder `Pause`; `pending` = `loader-circle` |
| `Dot` | weg (fase 1b: icoon + woord) | — | een stip alleen is kleur-alleen |
| `Button`, `LinkButton` | Button (cva `primair/secundair/stil/gevaar/link`, `standaard/klein/icoon`) | klasse `.btn`, `pending` → `aria-busy` | alle maten ≥ 44 px; uitgeschakeld = `aria-disabled` + reden |
| `Card`, `PageHeader` | Card / PageHeader (C-02) | `id`, `aria-labelledby` | kruimel met `chevron-right` (44 px) |
| `KpiCard` | **StatKaart** (C-05) | `href/drill` → één vervolglink; `staleNote` | + Bron + Bijgewerkt (`fetchedAt`) + meetkwaliteit; toestanden laden/verouderd/onbekend/fout; waarde in `foreground` |
| `StateView`, `ErrorState`, `ResourceView` | Leeg/Onbekend/Laden (C-17/18), **Foutpaneel** (C-14), **KopieerVeld** (C-15), **CapabilityUnavailable** (C-12) | klasse `.state--<kind>` en `data-state` (e2e telt `.state--error`), `role` | stale ook bij lege lijsten; Overzicht krijgt stale-gedrag |
| `Modal` | Dialog / **AlertDialog** | portal, focusval, `data-autofocus`, `role` | `z-overlay`; < 640 als sheet van onder |
| `DangerDialog` | AlertDialog + getypte bevestiging (stijl GoedkeuringsPagina stap 2) | knopnamen ("Terugzetten", "Definitief verwijderen"), woord `VERWIJDER`/`TERUGZETTEN` | blijft een dialoog (ADR-0016 punt 2); knop `gevaar` `aria-disabled` tot het woord klopt |
| `DiffViewer` | **DiffWeergave** (C-20) | `role=table`, kolommen | tekens `~ + −`, Was/Wordt, geen rood/groen |
| `Stepper` (migratie, setup) | Wizard-stepper (C-36) | `aria-current="step"` | < lg alleen bollen + "Stap n van m" |
| `Timeline`, `Trace` | Incidenttijdlijn / AuditTijdlijn (C-25) | — | tijd in `cijfers`; actor als ActorLabel |
| `Tabs` | Tabs (COMPONENTEN §21) | `role=tablist/tab`, pijltjes; link-tabs met `aria-current` | < 768 omlopend; Home/End |
| `Segmented` | ToggleGroup (`aria-pressed`) | `role=group` + `aria-label` ("Thema", "Taal") | omlopen, 44 px |
| `Switch` | Switch | `role=switch`, `aria-checked`, label | 44 px raakvlak |
| `Field`, `setting-field.tsx` | Label/Input/Select/Textarea + veldfout; SchemaFormulier (C-37) | `htmlFor`/id's, `data-setting` | veldfout `octagon-x` + `aria-invalid`; bron-tag (standaard/ingesteld); integriteit = BeleidsSlot |
| `Progress` | Progress / **Meter** (C-32) | `role=progressbar` (voortgang) of `role=meter` (verhouding tot grens) | §7 |
| `Skeleton` | Skeleton | klasse `.skeleton` | geen puls bij reduced motion |
| `Toast` | Sonner | — | `z-toast`; succes zegt wat er gebeurde |
| `SeasonHeatmap` | **Episodekaart** (eigen, §7) | `aria-label` per cel, links | textuur per toestand + tabel |
| `Confidence` | Meter-klein in inkt + ZekerheidLabel | — | §6.2 |
| `JobLaneCard`, workers | Card + Progress met baanicoon (`microchip`/`cpu` in `domain-lane-*`) | `worker-gpu-*`, `worker-cpu-*`, `role=progressbar` + `aria-valuetext` | tekst in inkt |
| `QualityRibbon`, `Poster`, `MediaRow` | Label (neutraal) / afbeelding | — | posterplaatshouder met titel als tekst bij een kapotte afbeelding (nu het kapotte-afbeelding-icoon) |
| Zijbalk, kopbalk, ondernavigatie | AppSchil (C-01) zonder Hub-items | `sidebar-health`, `status-pill`, `bell`, `profile-button`, `mobile-search`, navigatienamen | §6.6 Schil |

### 6.4 Iconen (lucide-react, al aanwezig)

`Pause` als neutraal standaardicoon verdwijnt; `CircleCheck` alleen nog voor Gezond. Vaste koppeling (ONTWERPTAAL §7):
Gezond `circle-check` · Beperkt `triangle-alert` · Storing `octagon-x` (was `CircleX`) · Onbekend `circle-help` ·
Verouderd `clock-alert` · Gepauzeerd `circle-pause` · Info `info` · Niet gekoppeld `link-2-off` · Gekoppeld `link-2` ·
Niet beschikbaar `circle-slash` (was `PlugZap`) · Nog niet beschikbaar `hourglass` · Offline `wifi-off` · Live `radio` ·
Gate `lock` · Beleid `ban` · Integriteit `file-lock` · GPU/CPU `microchip`/`cpu` · Back-up `database-backup` ·
Terugzetten `rotate-ccw` (was `Undo2`) · Diff `file-diff` · Kopiëren `copy` · Deep link `square-arrow-out-up-right` ·
Hub-link `arrow-left`. Navigatie-iconen van MediaGuard blijven (eigen IA). Maat 20 px in knoppen/titels, 18 px in
badges, 24 px in de ondernavigatie.

### 6.5 Typografie, ruimte en vorm

- Systeemfont en systeem-mono; `next/font/local` Inter/JetBrains Mono en `@fontsource-variable/*` verdwijnen in fase 2c.
- Kleinste tekst 14 px; tabelcel, badge en meta `text-sm`; body 16 px; h1 `text-2xl`; kaartwaarde `text-3xl`
  proportioneel (nu `tnum` op grote getallen); getallen in kolommen en tijden `cijfers`.
- Kapitalen-labels (`.eyebrow`) worden gewone `text-sm font-semibold`-koppen.
- Radius 8 px (kaart/knop/veld), statuspil rond, code/tag 4 px. Randen in plaats van schaduwen.
- Breekpunten Tailwind: zijbalk ≥ 1024 (was 861), ondernavigatie < 1024. Tabellen < 768 als kaartenlijst.

### 6.6 Per scherm (handoff)

Algemeen: PageHeader (h1 + omschrijving `max-w-prose` + "Bijgewerkt 11:56 (zojuist)" waar één hoofdbron is) → kaarten
in 1 (< 768) / 2 (`md`) / 3 kolommen (`xl`); tabellen ≥ 768 als `table`, < 768 als kaartenlijst; geen horizontale scroll
voor kerninformatie; knoppen < 640 onder de tekst en volle breedte.

| Scherm | Peil-patroon | Wat er verandert | Let op |
|---|---|---|---|
| **Schil** | AppSchil C-01 | 5 hoofditems + Instellingen blijven (eigen IA); actief = `bg-accent` + 4 px balk + `aria-current`; voet: StatusBadge totaal + link "6 dingen wachten op jou" (inktteller) + Live (neutraal `radio`) + versie; **"Terug naar de Hub"** bovenaan (§9.1); kop: zoeken, pil "Aandacht" wordt inktteller met tekst, bel-teller inkt, profielknop = initialen + sr-only "profiel en thema"; ondernavigatie 5 items blijft; profielmenu krijgt de Hub-link als eerste item op < 1024 | `app/layout.tsx`: `data-theme="dark"` en de standaard Donker worden Systeem (opgelost thema in het script, ADR-0016 punt 5); `themeColor` per schema; skiplink blijft eerste tabstop |
| **Overzicht** | Overzicht/Home (§9) | vergrendelbanner → GateBlokkade; hero → SysteemStatusPaneel + "Wat actie nodig heeft" (iconen per soort); KPI → StatKaart met bron + bijgewerkt; "AV1-besparing" bij onbereikbare engine = Onbekend + reden; "Nieuwe afleveringen" met neutrale Verwacht-label | sneltoetsen en links blijven |
| **Activiteit** | Lijst + Tabs | filterchips → Tabs (omlopend); statuschips §6.2; rij = één link, knoppen aan het eind (< 640 eronder); verouderd per lijst | `?tab=` blijft |
| **Bibliotheek** | Lijst + FilterBalk | filters in één rij (mobiel als sheet); chips neutraal (§6.2); titel omlopend; poster met tekstplaatshouder | `?type=&state=&q=&sort=&instance=` blijft |
| **Titel / film / aflevering** | Detail met tabs | kruimelpad 44 px; feiten als `dl`; "Instellingen voor deze titel" als SchemaFormulier; tabs omlopend; seizoenen: beschikbaarheid neutraal + episodekaart §7; "Klopt het bestand?" met ZekerheidLabel + BewijsPaneel | integriteit/terugzetten ongewijzigd |
| **Ontbrekend** | Lijst + master-detail | oorzaken als tabel; backfill-meters (Meter); diagnose-lade als Sheet | `?reason=` blijft |
| **Kalender** | Lijst/raster | kleurrand per type → domeinlabel met icoon (`tv`, `film`, `sparkles`) + tekst; status §6.2 | — |
| **Ruimte** | Metrieken | periodekiezer boven alles; StatKaarten (Onbekend als de engine niet antwoordt); banen met `microchip`/`cpu` in baankleur, tekst inkt; Belasting en Besparing per dag §7; grootboek als tabel-met-balk | `worker-*` testids |
| **Ruimte → Wachtrij / Kwaliteit** | Lijst / Formulier | statusfilter als Tabs; jobstatus §6.2; afbreken = AlertDialog | — |
| **Terugzetten** | Lijst + Meter | kapitalen-label "QUARANTAINE" → kop; "1 geslaagd · 0 mislukt · 1 nog te controleren" als neutrale telling (alleen een niet-nul "mislukt" krijgt de Storing-vorm); "Originelen blijven 7 dagen bewaard" = `info`; "Herstel nog niet gereed" = GateBlokkade; gebruiksbalk = Meter (≥ 80 % Beperkt met icoon + tekst, vol = Storing); vrijgavecontrole §6.2; tabel < 768 als kaarten; "Definitief verwijderen" en "Terugzetten" in AlertDialog met getypt woord | `quarantine-*`, `q-item-*`, `bulk-delete` |
| **Aanvragen** | Lijst / Leeg | "Seerr is nog niet gekoppeld" = **Niet gekoppeld** (`link-2-off`) + knop "Seerr koppelen"; noodstop = Gepauzeerd-melding | — |
| **Instellingen** (+ subpagina's) | SchemaFormulier (C-37) | secties als `details`/kaarten; per veld label, hulp, bron-tag, slot of "herstart nodig"; savebar als vaste actiebalk; GeheimVeld (nooit vooringevuld); 2FA-herstelcodes in KopieerVeld | `data-setting`, `settings-save` |
| **Koppelingen, Kwaliteit, Migratie** | Tabs + Wizard + ActiePreview-stijl | "Bekijk wijzigingen" als DiffWeergave; "Toepassen" na bevestiging; verdeling §7; stepper < lg compact | geen nieuwe routes |
| **Dolby Vision, Release Lab, Anime, AI-adviseur** | Formulier + Lijst | statussen §6.2; afwijsredenen als lijst met `x`; AI-zekerheid als ZekerheidLabel (stippen, geen percentage-kleur) | — |
| **Systeem** | Kaarten + tabellen | componenten = StatusBadge per onderdeel; back-ups/taken ≥ 768 tabel; opslagtrend §7; herstelbundel: KopieerVeld + BeleidsSlot | — |
| **Hoe werkt het** | Uitleg | stroomschema-kleuren naar tokens (geen data → geen tabel nodig, wel tekstalternatief dat er al is) | — |
| **Inloggen / eerste start** | Formulier / Wizard | merkpaneel zonder violette gloed (violet = goedkeuring); knop uit = Peil-stijl | e2e kop "Overzicht", labels |
| **Niet gevonden / fout / offline** | Foutpaneel op paginaniveau | "Er ging iets mis" → wat + waarom + wat je kunt doen + Trace-ID kopiëren; offline `wifi-off` op `unknown`-vlak | `error-boundary` |

### 6.7 Microcopy (NL + en-US, ux-copy)

Domeinteksten blijven. Alleen deze woorden veranderen (vaste woordenlijst ONTWERPTAAL §11.1); elke regel krijgt beide
talen in `lib/i18n/messages/*.ts` en `messages/en/*.ts`.

| Sleutel | Nu (NL / en-US) | Wordt (NL / en-US) |
|---|---|---|
| `status.degraded` | Verminderd / Degraded | **Beperkt** / Degraded |
| `status.offline` | Offline / Offline | **Storing** / **Down** |
| `status.ok`, `health.allGood` | In orde, Alles in orde / All good | **Gezond**, **Alles gezond** / **Healthy**, **All healthy** |
| `health.offline` | Core offline / Core offline | **Onbekend — MediaGuard-core reageert niet** / **Unknown — MediaGuard core is not responding** |
| `status.succeeded`, `av1.*.klaar/done` | Geslaagd, Klaar / Succeeded, Done | **Afgerond** / **Completed** (en **Uitgevoerd en gecontroleerd** / **Done and verified** waar de verificatie geslaagd is) |
| `state.loading` | Laden… / Loading… | blijft (sr-only bij het skelet); nooit als status |
| `state.errorTitle` + `state.errorBody` | Er ging iets mis / Something went wrong + … | **<Wat> is mislukt.** + waarom + wat je kunt doen, bv. **"Bibliotheek ophalen is mislukt.** MediaGuard-core gaf een interne fout. Je configuratie is niet gewijzigd. Probeer het opnieuw; blijft het mislukken, kopieer dan de trace-ID voor het logboek." / **"Loading the library failed.** MediaGuard core returned an internal error. Your configuration has not been changed. Try again; if it keeps failing, copy the trace ID for the log." |
| `state.correlation` | Correlatie-ID: {id} / Correlation ID: {id} | **Trace-ID** + **Kopiëren** → "Gekopieerd." / zonder secure context: "Tekst geselecteerd. Kopieer via het menu van je browser." — en-US **Trace ID** · **Copy** → "Copied." / "Text selected. Copy it from your browser's menu." |
| `state.offlineTitle` | MediaGuard reageert even niet / Backend unreachable | **MediaGuard-core reageert niet** / **MediaGuard core is not responding** (één betekenis in beide talen) |
| `state.unavailableTitle` + `Body` | Nog niet beschikbaar / Not available yet | voor `capability_unavailable`: **Niet beschikbaar — MediaGuard-core biedt dit niet aan** / **Not available — MediaGuard core does not offer this**; "Nog niet beschikbaar" / "Not available yet" alleen voor een fase |
| `state.stale` | Gegevens {age} — laatste verversing mislukt / Data {age} — last refresh failed | **Verouderd · laatst bekend {tijd}** + "Verversen mislukt om {tijd}. Bediening staat uit tot de bron weer antwoordt." / **Stale · last known {time}** + "Refresh failed at {time}. Controls are off until the source responds again." |
| `state.readOnly` (alleen in `title`) | Alleen-lezen rol / Read-only role | zichtbaar naast de knop: **"Je kijkt als kijker: je kunt hier niets wijzigen."** / **"You are signed in as a viewer: you can't change anything here."** |
| `live.live/offline` | Live / Live updates offline | **Live · {tijd}** / **Live · {time}**; **Live-updates onderbroken — opnieuw verbinden over {s} s. De lijst toont de stand van {tijd}.** / **Live updates interrupted — reconnecting in {s} s. The list shows the state as of {time}.** |
| `status.available/missing/searching/expected/unmonitored` | Binnen · Ontbreekt · Zoekend · Verwacht · Niet gemonitord / Available · Missing · Searching · Expected · Unmonitored | Binnen · Ontbreekt · **Zoekt** · Verwacht · **Niet gevolgd** / Available · Missing · Searching · Expected · **Not monitored** (vorm §6.2) |
| Hero `todo` | {n} dingen hebben je hulp nodig | kop **Wat actie nodig heeft** / **What needs action** + telling "{n} open"; status apart |
| KPI AV1 bij engine weg | — Nog niet beschikbaar | **Onbekend — AV1-engine reageert niet** / **Unknown — AV1 engine not responding** |
| Noodstop-banner | (bestaand) | blijft; titel **Noodstop actief** / **Emergency stop active** met `circle-pause` |
| Bibliotheek vergrendeld | Bibliotheek nog vergrendeld. … | blijft + zin **"Dit is geen storing; er wacht een voorwaarde."** / **"This is not a fault; a condition is waiting."** |
| Profielknop (naam) | Profiel / Profile | initialen + sr-only **"profiel en thema"** / **"profile and theme"** (zichtbare tekst zit in de naam, WCAG 2.5.3) |
| — (nieuw, §9) | — | **Terug naar de Hub** / **Back to the Hub** · **Toon als tabel** / **Show as table** · **Verberg tabel** / **Hide table** · **"Gebruik de pijltjestoetsen om per {dag/meting} te lezen."** / **"Use the arrow keys to read each {day/reading}."** · **Geen meting** / **No reading** · instellingsveld **Hub-adres** / **Hub address**, hulp **"Vorm: http(s)://<NAS-adres>:<poort>. Geen localhost. Leeg = geen link 'Terug naar de Hub'."** / **"Format: http(s)://<NAS address>:<port>. No localhost. Empty = no 'Back to the Hub' link."** |

Toon: rustig, eerst wat er is, dan waarom, dan wat je kunt doen; knoppen beginnen met een werkwoord; geen "OK",
"Doorgaan" of "Weet je het zeker?". **Lokalisatie:** en-US is ± 10–20 % korter dan NL; knoppen en pillen lopen om,
dus geen vaste breedtes. "Trace-ID" blijft in beide talen een vakterm; "Hub" is een eigennaam.

---

## 7. Grafieken (dataviz-procedure)

Volgorde: vorm → kleur → validatie → markeringen → interactie → toegankelijkheid → zelf bekijken. Palet: Peil
`chart-1…5` + `chart-other`, gevalideerd (§4.4). **Statuskleuren zijn nooit een reeks.** Elke grafiek: `figure` +
`figcaption` (bron · bijgewerkt · "een gat betekent dat er geen meting was (nooit 0)"), plotvak `role=img` +
`tabindex=0` + samenvatting, pijltjes/Home/End/Esc met voorlezen via één `aria-live="polite"`, tooltip via aanwijzer
én toetsenbord, `details` **"Toon als tabel"** (de WCAG-tweeling). MediaGuard houdt de eigen SVG-grafieken
(`charts.tsx`, geen recharts; ONTWERPTAAL §10); labels blijven HTML (14 px).

| Grafiek | Vorm (job) | Kleur | Tabel (kolommen) | Toetsenbord |
|---|---|---|---|---|
| **Belasting** (Ruimte) | Trend van een percentage → **lijn, één y-as 0–100 %**. Nu tot 4 lijnen; de validator zegt dat 4 reeksen met alle paren niet uit elkaar te houden zijn (§4.4). Daarom **maximaal 3 lijnen** (GPU, CPU, RAM; de eerste drie slots zijn op alle paren gevalideerd) en cache als **tweede, kleine grafiek** "Geheugen" (RAM + cache, twee lijnen) — geen tweede y-as | GPU `chart-1` (= `domain-lane-gpu`), CPU `chart-2` (= `domain-lane-cpu`), RAM `chart-3`; in "Geheugen": RAM `chart-3`, cache `chart-4`. Alle lijnen **doorgetrokken** (stippel = geschat in Peil); vlak 10 % alleen onder de eerste reeks; legenda + direct label aan het eindpunt; gat zonder meting = lijn breekt + `arcering-gat` "Geen meting" | Tijd · GPU % · CPU % · RAM % (· cache %) | ←/→ per meting, Home/End, Esc; voorlezen "11:42: GPU 55 %, CPU 41 %, RAM 37 %" |
| **Besparing per dag** (Ruimte) | Hoeveelheid per dag → **staven, één tint**, **doorlopende datumas** (elke dag een plek) | `chart-1` (niet groen); staaf ≤ 24 px, 4 px afgerond aan de datakant, 2 px tussenruimte; dag zonder meting = geen staaf maar een gearceerd vak + "Geen meting" (nooit 0) | Dag · Bespaard (GiB/TiB zoals de bron) · Encodes | ←/→ per dag; voorlezen "4 nov: 52 GB bespaard, 3 encodes" / "5 nov: geen meting" |
| **Opslagtrend** (Systeem) | Trend → **lijn** "Vrije ruimte" + **prognose als stippellijn** (geschat) tot "vol" met direct label "≈ vol over 300 dagen · geschat" | lijn `chart-1`, vlak 10 %; prognose `chart-1` gestippeld; grens "0 TB vrij" als `chart-limit` | Dag · Vrije ruimte · Meetkwaliteit (gemeten/geschat) | ←/→ per dag |
| **Episodekaart** (titel, aflevering) | Toestand per aflevering in een raster → **cellen met kleur + vorm** (de enige grafiek met > 3 klassen; daarom vorm als tweede kanaal en altijd de tabel) | Binnen = vol `chart-1`; AV1 (binnen, omgezet) = vol `chart-3`; Ontbreekt = vol `chart-2`; Zoekt = rand 2 px `chart-2`, leeg; Verwacht = rand 1 px `border`, leeg; Niet gevolgd = `muted` met streep (`minus`); Onzeker = `unknown`-stippelrand + `?`. Geen statuskleuren; drie kleuren uit de op alle paren gevalideerde eerste drie slots | Seizoen · Aflevering · Toestand (woord) · Kwaliteit | ←/→ per aflevering, ↑/↓ per seizoen, Home/End; de cellen blijven links naar de aflevering |
| **Verdeling** (Kwaliteit, Migratie) | Deel van een geheel naast de cijfers → **dunne segmentbalk zonder tekst + tabel** (6 delen) | vaste slot per deel (kleur volgt het deel): AV1 1 · Vervangen 2 · Zoeken 3 · Behouden 4 · Onzeker 5 · Overslaan **Overig** (`chart-other` + arcering); 2 px vlakgat tussen segmenten; legenda met aantallen (bestaat) | Deel · Aantal · Aandeel | via de tabel (de balk is `aria-hidden`) |
| **Meters** (`Progress` → Meter) | Verhouding tot een grens → **meter** | vulling `chart-1` op `chart-meter-track`; vanaf de bestaande drempel (quarantaine 80 %, budget) **Beperkt** met `triangle-alert` + tekst "86 % van de quarantaine — boven de drempel van 80 %"; vol/gemeten falen = Storing; voortgang (jobs, migratie) = Progress neutraal, 100 % blijft `chart-1` | n.v.t. (waarde altijd als tekst) | `role=meter` + `aria-valuetext` / `role=progressbar` |
| **Grootboekbalken** | Vergelijken van posten → **liggende staven, één tint** + bedrag als tekst | `chart-1`; negatieve post (besparing) = `chart-2`; geen violet/amber | de lijst ís de tabel (post · bedrag · uitleg) | — |
| **Zekerheid** | één getal → getal + mini-meter in inkt | `foreground` | n.v.t. | — |

Niet toegestaan (anti-patronen, gecontroleerd): tweede y-as; kleuren laten verspringen bij filteren; een 6e gegenereerde
tint; een tooltip als enige manier om een waarde te lezen; een ontbrekende dag als 0; skeletflits bij verversen (vorige
weergave blijft staan).

---

## 8. De twee fasen (web)

### Fase 1 — aliaslaag en woorden (geen componentstructuur, geen nieuwe afhankelijkheid)

**1a. CSS (alleen uiterlijk)**

1. Kopieer `design/ontwerptaal/ontwerptaal.css` en `aliaslagen/mediaguard-mso.css` naar `apps/web/styles/peil/` (met
   herkomstkop, verder byte-gelijk; de Docker-context van het web-image is `apps/web`) en voeg `npm run check:peil` toe
   (sha256 tegen `design/ontwerptaal/HERKOMST.md`), zoals MSO.
2. `app/globals.css` wordt: `@import "tailwindcss"; @import "../styles/peil/ontwerptaal.css"; @import
   "../styles/mg.css"; @import "../styles/av1.css"; @import "../styles/peil/mediaguard-mso.css"; @import
   "../styles/peil-mg.css";` — het eigen `:root { --mg-font… }`-blok vervalt.
3. Nieuw `styles/peil-mg.css` met de correcties a–m uit §6.1.
4. `app/layout.tsx` + `ui.theme`: standaardthema **Systeem** (het bestaande script zet het opgeloste
   `data-theme="dark|light"`); `themeColor` per schema. De sleutel `mg.theme` en de keuze per gebruiker blijven.
5. Verwacht: alle kleuren, radii en het systeemfont volgen Peil; overloop 0; focus Peil-ring en nooit bedekt; geen
   opacity op uitgeschakelde tekst. **Nog niet:** tekst ≥ 14 px (inline maten), 44 px op desktop, iconen, woorden.

**1b. Woorden en iconen (alleen in bestaande componenten)**

1. `primitives.tsx`: `TONE_ICON.neutral` = geen icoon (`circle-help` alleen voor *onbekend*), `danger` = `octagon-x`;
   `Dot` vervangen door icoon + woord waar hij status draagt.
2. `lib/status.ts`, `lib/todo.ts`, `lib/health.ts`: tonen volgens §6.2 (groen alleen gezondheid; taken, beschikbaarheid,
   live en noodstop neutraal of eigen vorm).
3. `lib/i18n/messages/*` en `messages/en/*`: de woorden uit §6.7 (beide talen).
4. `KpiCard`: laden → skelet; engine weg → "Onbekend — AV1-engine reageert niet"; waarde in `foreground`.
5. `ResourceView`: verouderd ook bij een lege (laatst bekende) lijst; Overzicht en Ruimte gebruiken dezelfde markering.
6. `ErrorState`: Trace-ID zichtbaar en kopieerbaar (KopieerVeld-gedrag met terugval).
7. Profielknop: initialen + sr-only tekst (axe 2.5.3).

### Fase 2 — Tailwind 4 + shadcn/ui (React 19, new-york)

**2a. Basis en schil.** Afhankelijkheden `class-variance-authority`, `clsx`, `tailwind-merge`, Radix via shadcn (exacte
versies in `package-lock.json`; geen `latest`). `components.json` uit `design/ontwerptaal/` met MediaGuard-paden
(`css: "app/globals.css"`). `shadcn init` mag `globals.css` niet overschrijven (codemod COMPONENTEN §1, ook
`@custom-variant dark` en het variabelenblok weg). AppSchil (zijbalk ≥ 1024, ondernavigatie < 1024), **"Terug naar de
Hub"** (§9.1). Testids en namen blijven.

**2b. Componenten** (één tegelijk, elk met eigen commit en groene e2e): Button → StatusBadge/Label → Card/StatKaart →
Foutpaneel/KopieerVeld/Leeg/Laden → Tabs → ToggleGroup → Switch/Input/Select/Field → Dialog/AlertDialog (Modal,
DangerDialog) → DiffWeergave → Stepper/Tijdlijn → Progress/Meter → ResponsieveLijst → Sonner → Episodekaart. Per
component de codemod (44 px, `text-sm` minimum, Peil-ring, `destructive-foreground`, geen opacity-trucs,
`z-overlay/z-toast`).

**2c. Toegestane toevoegingen en opruimen.** Tabelweergave + toetsenbord bij de grafieken (§7, §9.2); daarna inline
`style={{…}}` → utilities (794×, waarvan 212 lettermaten); `mg.css`/`av1.css` en de aliaslagen weg zodra niets ze nog
gebruikt; `app/fonts/*`, `next/font/local` en `@fontsource-variable/*` weg. Hookklassen die tests gebruiken (`.btn`,
`.chip`, `.skeleton`, `.state--error`, `.sidebar`, `.bottomnav`, `.tech-details`) blijven op de elementen.

**Per stap terugdraaibaar:** elke stap is één commit; fase 1a is een paar CSS-imports (terugzetten = regels weghalen).

---

## 9. Toegestane toevoegingen — specificatie

### 9.1 "Terug naar de Hub" (O-11, web)

- **Bron van het adres:** een nieuwe, optionele instelling **`system.hub_url`** in het bestaande instellingenregister
  (`api/settings.py`, categorie "Systeem", type string, standaard leeg, geen herstart, uitleg in `settings_help.py`).
  Lezen en schrijven via het bestaande schema-gestuurde instellingenscherm en de bestaande settings-API: geen nieuwe route,
  geen migratie, OpenAPI byte-gelijk (§1), Custom App-formulier ongewijzigd (ADR-0016 punt 1). Zelfde regels als MSO's
  `systeem.hub_url` (de sleutelnaam volgt MediaGuards eigen `system.*`).
- **Geldig adres** (zelfde regels als `HUB_URL`, contract §3.2): `http(s)://<host>[:<poort>]`, zonder pad, query,
  fragment of inloggegevens; geen loopback, link-local, `localhost` of `0.0.0.0`/`::`. De UI toont de link **alleen** als
  de waarde zo te parsen is; anders verborgen. Server-side dezelfde controle bij opslaan (422 "Hub-adres: gebruik
  http(s)://<NAS-adres>:<poort>, zonder pad" / "Hub address: use http(s)://<NAS address>:<port>, without a path").
  **Nooit** een voorbeeld-IP of -poort als standaard of placeholder.
- **Plaats:** zijbalk direct onder het logo (≥ 1024) en als eerste item van het profielmenu (< 1024, daar staan ook
  Instellingen en de overige links). Niet op inloggen/eerste start.
- **Vorm:** secundaire link-knop, `min-h-touch`, icoon `arrow-left` (20 px, `aria-hidden`) + **"Terug naar de Hub"** /
  **"Back to the Hub"**; zelfde tabblad (terugweg, geen deep link).
- **Toestanden:** leeg/ongeldig → niet in de DOM; kijker ziet de link ook (lezen van de instelling mag elke rol; zo niet,
  dan verborgen).
- **Wie mag het adres zetten (QA, ADR-0020 punt 1):** alleen een beheerder. Het adres staat bij iedereen als link in
  beeld; een operator of de app-sleutel kan het dus niet meer naar een andere server laten wijzen (403, net als
  `app.public_url`).
- **Android:** geen Hub-link (de beslissing noemt de web-apps; ADR-0016 punt 3).
- **Tests:** e2e — leeg ⇒ geen link; geldig ⇒ `href` gelijk aan de waarde; `javascript:`/pad/loopback ⇒ geen link en
  422; unit-test op de validator; `werk-instellingen-dekking.spec` telt de nieuwe instelling mee (230 i.p.v. 229) en
  `scripts/gen-feature-inventory.py` opnieuw draaien.

### 9.2 Tabelweergave en toetsenbord bij elke grafiek (web)

- Geldt voor Belasting (+ Geheugen), Besparing per dag, Opslagtrend, Episodekaart en Verdeling (§7). Meters,
  grootboekbalken en zekerheid tonen hun waarde al als tekst en krijgen geen aparte tabel.
- `details` **"Toon als tabel"** onder de grafiek (dicht bij openen); `table` met `caption` (= titel van de grafiek),
  `th scope`, getallen `cijfers` rechts uitgelijnd, "Geen meting" als tekst (nooit 0 of "—"); bij > 4 kolommen op
  < 768 een kaart per rij.
- Plotvak `tabindex=0`, `aria-label` = samenvatting + "Gebruik de pijltjestoetsen om per dag te lezen."; ←/→ per punt
  (episodekaart ook ↑/↓), Home/End, Esc sluit; zichtbare focus = Peil-ring om het plotvak + draadkruis; voorlezen via
  één `p[aria-live=polite]` per grafiek.
- Testhaak: `data-testid="grafiek-<naam>"` en `data-testid="grafiek-<naam>-tabel"` (nieuw, kebab-case NL).
- **Android:** de app heeft geen grafieken met verborgen waarden (alleen voortgang en grootboekbalken met het bedrag als
  tekst); daar volstaat `contentDescription`/`semantics` met de waarde (§10.4).

---

## 10. Android: Peil in Jetpack Compose

### 10.1 Afbeelding tokens.json → Compose

Bron: `design/ontwerptaal/tokens.json` (DTCG; licht = `$value`, donker = `$extensions["nl.peil"].modes.donker`). Fase 2
genereert daaruit `ui/theme/PeilTokens.kt` (twee `object`s `PeilLicht`/`PeilDonker` met `Color(0xFF……)`) met een kop
"Peil 1.1.0 · sha256:9583506d…", plus een JVM-test `PeilTokensTest` die elke kleur met `tokens.json` vergelijkt (drift =
test faalt). `MgColors` blijft als naam (veel aanroepen), maar haalt zijn waarden uit Peil:

| `MgColors` | Peil-token (licht / donker) | Opmerking |
|---|---|---|
| `bg` | `background` `#EEF1F5` / `#0A0D12` | |
| `surface` | `card` `#FFFFFF` / `#141920` | |
| `surface2` | `secondary` `#E4E8EE` / `#232B35` | secundaire knop, segment; popover/sheet = `popover` |
| `sunken` | `muted` `#E4E8EE` / `#1E252E` | |
| `line`, `lineSoft` | `border` `#AEB8C5` / `#3A4451` | alleen decoratief |
| `lineStrong` | `border-strong` `#5B6575` / `#8E99A8` | bedienbare randen, stippelrand Onbekend |
| `text` | `foreground` `#0B0F14` / `#F2F5F8` | |
| `muted`, `faint` | `muted-foreground` `#353E4A` / `#C2CAD5` | er is geen derde tekstniveau |
| `accent`, `onAccent` | `primary` `#123DA3` / `#8DB6FF`, `primary-foreground` `#FFFFFF` / `#06101F` | |
| `ok`, `okSoft` | `healthy` `#0F6B33` / `#6FD394` (icoon), `healthy-ink` `#0A4A22` / `#A6EBBF` (tekst), `healthy-bg` | alleen gemeten gezondheid |
| `warn`, `warnSoft` | `degraded` `#8F4B00` / `#F7C26A`, `-ink` `#5E3300` / `#FBD596`, `-bg` `#FCEFD6` / `#33230A` | |
| `bad`, `badSoft` | `down` `#B3141B` / `#FF9B9B` (icoon/rand), `down-ink` `#78090F` / `#FFC2C2`, `down-bg`; knoppen `destructive` `#A8111A` / `#FF9B9B` + `-foreground` | |
| `info`, `infoSoft` | `info` `#17469A` / `#8DB6FF`, `info-ink`, `info-bg` `#E3ECFA` / `#132440` | ook Gepauzeerd |
| `violet`, `violetSoft` | **vervalt**: GPU-baan = `domain-lane-gpu` (`#2a78d6` / `#3987e5`), CPU-baan = `domain-lane-cpu` (`#eb6834` / `#d95926`) | violet = goedkeuring; komt in de app niet voor |
| `accentSoft` | `accent` `#DCE3EC` / `#26303B` | selectie/hover (shadcn-betekenis) |
| `neutralSoft` | `unknown-bg` `#E9EDF2` / `#222932` | |
| nieuw | `unknown` `#566070` / `#A3ADBB`, `unknown-ink`, `stale-*`, `disabled-foreground`, `ring` + `ring-halo`, `chart-1…5`, `chart-other`, `chart-meter-track`, `gate*` | |

**MaterialTheme `colorScheme`:** `primary`/`onPrimary` = `primary`/`primary-foreground`, `primaryContainer` = `accent`,
`secondary` = `foreground` (geen violet meer), `secondaryContainer` = `accent` (indicator ondernavigatie), `tertiary` =
`info`, `background`/`onBackground`, `surface`/`surfaceContainer*` = `card` (`High/Highest` = `popover`), `onSurface` =
`foreground`, `surfaceVariant` = `muted`, `onSurfaceVariant` = `muted-foreground`, `outline` = `border-strong`,
`outlineVariant` = `border`, `error`/`onError` = `destructive`/`-foreground`, `errorContainer`/`onErrorContainer` =
`down-bg`/`down-ink`, `surfaceTint` transparant. Geen Material You (blijft zo).

**Typografie (sp; systeemfont = Roboto):** de ingebouwde Inter (`res/font/inter_*`) verdwijnt in fase 3.

| Peil-rol | M3-stijl / `MgType` | Nu → Peil |
|---|---|---|
| meta 14/20 400 | `bodySmall`, `labelSmall/Medium` / `meta`, `small` | 12–13 → **14** (ook ondernavigatie-labels en `Pill`) |
| body 16/24 400 | `bodyLarge/Medium` / `body` | 15/21 → **16/24** |
| knop 16 600 | `labelLarge` | 15 → 16 |
| titel 18/28 600 | `titleMedium` / `h2` | 18/24 → 18/28 |
| sectie 20/28 700 | `titleLarge` | — → 20 |
| pagina 24/32 700 | `headlineSmall` / `title` | 24/30 → 24/32, zonder negatieve letterspatiëring |
| waarde 30/36 600 | `displaySmall` / `kpi` | 24 bold → 30 600, **proportionele cijfers** (nu `tnum` op alles) |
| — | `eyebrow` (11 sp kapitalen) | **vervalt**: `SectionLabel` wordt titel 14 600 zonder kapitalen |

`tnum` alleen voor kolommen, tijden en aftellers (`cijfers`), niet voor losse grote waarden.

**Vormen:** `Shapes(extraSmall = 4.dp, small = 6.dp, medium = 8.dp, large = 12.dp, extraLarge = 12.dp)`. `MgShape.card`
14 → 8 dp, `segment` 10 → 8, `tag` 6 → 4, `control` 8 blijft, `pill` rond (alleen status). Kaarten: 1 dp rand `border`,
geen schaduw; sheets/dialogen 12 dp.

**Aanraakdoelen: 48 dp** (Android-richtlijn, ruimer dan Peils 44 px). `MgButton` 40/32 → **48 dp** min-hoogte (ook
"klein"; klein = 14 sp tekst en minder padding), `Modifier.minimumInteractiveComponentSize()` op elke `clickable` (lijstrijen,
chips, icoonknoppen in `TopBar`, `FilterChips`), tussenruimte ≥ 8 dp.

**Licht/donker:** volgt het systeem (`isSystemInDarkTheme()`), beide uit dezelfde tokens, elk apart gevalideerd (de
waarden in `tokens.json` zijn per thema gemeten). Geen themaknop in de app (niet besloten).

### 10.2 Status altijd icoon + tekst + vorm

Nieuw composable `PeilStatus(status, tekst, reden?)`: `Row` met icoon 18 dp + woord (14 sp, omlopend, **geen ellips**),
vorm `RoundedCornerShape(50)`, rand 2 dp (gestippeld voor Onbekend/Verouderd via `drawBehind` met
`PathEffect.dashPathEffect`), vlak per status (Gezond zonder vlak); `semantics(mergeDescendants = true)`. Vervangt:

| Nu | Peil |
|---|---|
| `Pill(dot = true)` — gekleurde stip + woord, 12 sp, één regel met ellips | `PeilStatus` met icoon; neutrale labels als `PeilLabel` (rand `border-strong`, geen statuskleur) |
| `Pill` "Uit" (noodstop) groen; "GPU"/"CPU" violet/blauw met stip | noodstop uit = neutraal "Uit"; banen = `PeilLabel` met `microchip`/`cpu` in `domain-lane-*`, tekst inkt |
| `StatusHero` (Overzicht: "3 dingen wachten op jou" rood/amber; "Alles in orde" groen; "Niet bijgewerkt" amber) | statuspaneel in de slechtste toestand: **Alles gezond** (vinkje, geen vlak) · **Beperkt**/**Storing** volgens het slechtste item · **Verouderd · laatst bekend 12:06** (`clock-alert`, stippelrand, arcering) — en de KPI-tegels daaronder ook verouderd (nu zien ze er vers uit) |
| `Banner` "Meldingen staan uit" (amber, bel-uit) | `info`-vlak met `bell-off` + bestaande knop "Oplossen" (geen gezondheid) |
| update-banner (lavendel) | neutraal `info`-vlak met `download`; geen violet |
| noodstopkaart: knop "Noodstop aanzetten" (rode rand) | knop `destructive`-rand + `-foreground` tekst (≥ 48 dp); actief = `paused`-vorm "Noodstop actief" |
| `IconBubble` met `WarningAmber`/`ErrorOutline` in Problemen | icoon per soort: `octagon-x` (storing), `triangle-alert` (beperkt), `lock` (gate: "Bibliotheek nog vergrendeld", "GPU-baan staat stil tot er ruimte is"), `info` |
| `CountBadge` (rood/amber/neutraal), pil "Aandacht 3" met rode stip | inktteller; naam in `contentDescription` ("3 dingen wachten op jou") |
| KPI-getallen in accent/violet/amber ("834 GB", "5", "23") | waarde in `foreground`; status als `PeilStatus` ernaast als dat nodig is |
| `MgProgress` in baankleur, percentage-tekst in baankleur | balk in `domain-lane-*` of `chart-1`; tekst inkt |
| `FilterChips` horizontaal scrollend, laatste chip half zichtbaar | `FlowRow` (omlopen) of `ScrollableTabRow` met zichtbare rand-fade + Peil-onderstreping |
| `ErrorState` / "Onbekende fout" | Foutpaneel-stijl: wat + waarom + wat je kunt doen + code + Trace-ID (selecteerbaar, kopieerknop 48 dp) |

**Iconen:** de app gebruikt nu 42 Material-iconen (`Icons.Outlined.*`). Voor één beeldtaal met het web: de gebruikte
lucide-iconen als vector drawables (`res/drawable/lucide_*.xml`, ISC-licentie meeleveren), géén nieuwe bibliotheek
(zelfde keuze als my_house ADR-0008 punt 6). Ondernavigatie: Overzicht `layout-dashboard`, Activiteit `radar`,
Bibliotheek `library`, Ruimte `hard-drive-download`, Aanvragen `inbox` (dezelfde als de web-nav).

### 10.3 Per scherm (Android)

| Scherm | Vertaling |
|---|---|
| Overzicht | statuspaneel (§10.2) bovenaan; "Wacht op jou" met iconen per soort; KPI-tegels = StatKaart-stijl (waarde inkt, bron/bijgewerkt als meta 14 sp); "Laatste 24 uur" neutraal; verouderd = alle kaarten gestippeld + "Laatst bekend · 12:06", bediening uit met reden |
| Activiteit | tabs omlopend; problemen per soort (§10.2); "Origineel houden" = secundaire knop 48 dp |
| Bibliotheek | lijst met neutrale beschikbaarheidslabels (§6.2); detail met episodekaart-cellen volgens §7 (vorm + kleur) en de lijst eronder als tabel-tweeling |
| Ruimte | noodstopkaart (§10.2); banen in `domain-lane-*` met inkt-tekst; "Waar ging de ruimte heen": `chart-1`/`chart-2`, "Niet verklaard" = `chart-other` + arcering (geen amber) |
| Aanvragen | zoekveld rand `input`; posters met tekstplaatshouder; aanvraagvenster als `ModalBottomSheet` 12 dp, knoppen 48 dp |
| Instellingen | waarschuwingen (meldingen/batterij uit, ntfy-fout) als `info`- resp. `down`-melding met reden; "Ontkoppelen" = `destructive` met de bestaande bevestiging |
| Koppelen | Foutpaneel-stijl voor "Dit is niet jouw server" (gemeten afwijking: `octagon-x`); camera niet beschikbaar = `circle-slash` Niet beschikbaar |
| Systeemmeldingen | ongewijzigd (systeem-UI); kanaalnamen blijven |

### 10.4 Toegankelijkheid Android

Elke status `semantics { contentDescription = "<woord>, <reden>" }`; voortgang `progressBarRangeInfo` + tekst; kopjes
`heading()`; teksten schalen mee met de systeemletter (geen vaste hoogtes op tekstcontainers; Paparazzi-variant met
`fontScale = 1.3f` toevoegen); geen ellips op inhoud. **NIET GEVALIDEERD:** TalkBack en een echte telefoon in fel licht.

---

## 11. Welke tests geraakt worden

### 11.1 Bestaande tests (web)

| Test | Raakt | Wat er moet gebeuren |
|---|---|---|
| `e2e/mediaguard.spec.ts` | thema (verwacht nu `data-theme="dark"` direct na inloggen), contrast (axe `color-contrast` in donker/licht op 7 routes), knoppen "Profiel"/"Donker"/"Licht", "geen nepdata" (`.state--error` = 0 op 20 routes), mobiel (navigatie "Mobiele navigatie" met 5 namen, geen horizontale scroll op 360/390) | standaard wordt Systeem: de test kiest eerst expliciet "Donker" of zet `colorScheme`; namen en testids blijven; **aanpassen:** overloop meten t.o.v. de apparaatbreedte (met `isMobile` is `scrollWidth − innerWidth` altijd 0, zie MSO); axe-tags uitbreiden naar de Peil-set |
| `e2e/werk-r030.spec.ts` | `main .btn` ≥ 44 px op 412 px | blijft geldig; uitbreiden naar 1440 (Peil: 44 px overal) |
| `e2e/werk-mediaserver.spec.ts` | `.chip` mag niet overlopen | blijft (`.chip` blijft hookklasse) |
| `e2e/werk-ia.spec.ts`, `werk-instellingen-dekking.spec.ts` | alle routes/instellingen, geen horizontale scroll per thema | nieuwe instelling `system.hub_url` telt mee; overloop-meting zoals hierboven |
| `e2e/werk-apikeys`, `werk-meldingen`, `werk-opslag`, `werk-opruimen`, `werk-aanvragen` | `data-theme`, 1440/390 | blijven groen als testids/klassen blijven |
| `e2e/studio-av1.spec.ts`, `werk-r033.spec.ts` | `worker-*`, `role=progressbar` + `aria-valuetext` "Analyse" | behouden |
| tekstasserties | "Verminderd", "Offline", "Alles in orde", "Correlatie-ID", "Nog niet beschikbaar", "Er ging iets mis" | vóór fase 1b met `grep -rn` in `e2e/` en `apps/core/tests/` nalopen en mee aanpassen (woorden §6.7); de gemelde 99 e2e-tests (ADR-0015) moeten daarna weer groen zijn |
| `e2e/design-shots.spec.ts` | 14 pagina's × 1440/390 × 2 | vervangen door het script van deze nulmeting (alle schermen, 4 breedtes) of uitbreiden |
| `apps/core` pytest | alleen §9.1 (nieuwe instelling + validator) en de standaard van `ui.theme` | registertests bijwerken; OpenAPI opnieuw meten (moet `54eee4d7…3e59a` blijven) |
| `scripts/gen-feature-inventory.py` | telling instellingen (229) | opnieuw genereren na §9.1 |

### 11.2 Bestaande tests (Android)

| Test | Raakt | Wat er moet gebeuren |
|---|---|---|
| `DesignLogicTest.libraryChips` | verwacht `Tone.Ok` voor "Compleet", `Tone.Warn` voor "2 ontbreken"/"Ontbreekt", `Tone.Info` voor "Kan beter" | tonen naar §6.2 (neutraal / neutraal-sterk); de woorden blijven |
| `DesignLogicTest.initialsLikeTheWeb` | initialen | ongewijzigd |
| `shots/ScreenShots.kt` (Paparazzi, 16 × 2) | uiterlijk | opnieuw opnemen; voeg de varianten 360 dp, volledig en `fontScale 1.3` toe (nu alleen in de kopie van deze nulmeting); `docs/design/android/` vervangen |
| `ApiTest`, `AppLogicTest`, `FormatTest`, `PairingTest`, `ViewModelTest` | logica | ongewijzigd |
| nieuw: `PeilTokensTest` | kleuren = `tokens.json` | toevoegen (fase 2 Android) |

### 11.3 Nieuwe tests (Peil-testmatrix, ONTWERPTAAL §12)

- Breedtes **320, 360, 390, 768, 1024, 1440** × licht/donker: overloop 0 t.o.v. de apparaatbreedte; geen doel < 44 px
  (radio/checkbox: het label telt; inline links uitgezonderd); geen tekst < 14 px (na fase 2c; in fase 1 als rapport);
  geen ellips op inhoud.
- axe `wcag2a, wcag2aa, wcag21a, wcag21aa, wcag22aa` = 0, ook in dialogen, het profielmenu en de zoekresultaten.
- Tab-doorloop op 390 en 1440: elke stop heeft de Peil-ring en is **niet bedekt** (`toetsenbord.mjs`).
- Eén h1, geen kopsprongen, geen dubbele id's; `prefers-reduced-motion` = geen beweging; forced colors toont stippelranden.
- Status: geen groene statusvorm buiten gezondheid (telling per scherm uit `metingen.json`: `chip_success`,
  `dot_success`, `cirkel_check`); Onbekend heeft nooit het pauze-icoon (`pauze_icoon` = 0 buiten Gepauzeerd).
- Grafieken: tabel aanwezig en gelijk aan de data; pijltjes lezen "Geen meting" voor bij een gat; geen staaf van 0 voor
  een dag zonder meting.
- Hub-link: §9.1. en-US: elke nieuwe sleutel bestaat in beide talen (bestaande i18n-controle).
- Afdrukken "na" in `docs/screenshots/peil/na/` met hetzelfde script.

---

## 12. Risico's, open punten en wat niet gevalideerd is

- **Gedeelde aliaslaag mist de focusring en scroll-padding voor `mg.css`-apps** (laagvolgorde). Opgelost in
  `peil-mg.css` (a, b); MSO meldde hetzelfde. Vervolgpunt voor de ontwerptaal: in de gedeelde laag opnemen.
  **Opgelost in Peil 1.1.1 (E14):** de aliaslaag doet het zelf; de eigen kopie in `peil-mg.css` is weg (ADR-0021).
- **Inline styles winnen van CSS.** Zekerheidsbalkjes, KPI-kleuren en lettermaten volgen in fase 1 alleen met
  `!important` of pas in fase 2. Tekst < 14 px blijft tot fase 2c (ONTWERPTAAL §13.3 zegt dat eerlijk).
- **Dichtheid.** 44 px en 14 px maken lijsten langer; compensatie via ResponsieveLijst en `details` voor lange uitleg.
- **DangerDialog blijft een dialoog** (geen nieuwe route; ADR-0016 punt 2).
- **Backendteksten** (`*_nl`, `message`, `status_nl`) blijven in de API; de UI kiest het Peil-woord uit de status
  (ADR-0016 punt 7).
- **Geen R-klassen verzonnen.** MediaGuard-acties hebben geen risicoklasse uit een beleid; de UI toont geen RisicoBadge
  zonder bron (ADR-0016 punt 8). De gevaarstijl (destructive + getypte bevestiging) blijft.
- **Android en-US** bestaat niet; nieuwe Android-teksten zijn alleen NL (ADR-0016 punt 4).
- **Niet gevalideerd:** echte telefoon, schermlezer (NVDA/TalkBack), 200 %-zoom, forced colors op MediaGuard zelf (Peil
  heeft die op de prototypes gemeten), tv/speler, echte NAS (Excel B04-*: blijft "NIET GEVALIDEERD (echte NAS)"). Deze
  ontwerpstap verandert geen Excel-status.
- **Vervolg:** na "OVERDRACHT VOLTOOID" de herkomst in `design/ontwerptaal/HERKOMST.md` omzetten naar
  `ai-server-manager` (commit + sha256) en een CI-driftcontrole toevoegen (ONTWERPTAAL §14).

---

## 13. Stand na stap 3 (web)

Gebouwd in `apps/web` (commits "Web fase 1a" t/m "Web fase 2d" en "Tests: Peil-e2e …"; bouwkeuzes in
[ADR-0017](../adr/ADR-0017-peil-web-bouwkeuzes.md)):

| Onderdeel | Stand |
|---|---|
| Fase 1a (aliaslaag, `peil-mg.css`, standaardthema Systeem, `check:peil`) | gebouwd; Peil-CSS in de cascadelaag `legacy` (ADR-0017 punt 1) |
| Fase 1b (woorden en iconen NL + en-US, totaalstatus, Foutpaneel met Trace-ID, verouderd, axe-herstel) | gebouwd |
| Fase 2a/2b (shadcn Button/Badge/Dialog, primitives, AppSchil, "Terug naar de Hub") | gebouwd; `mg.css` zonder de omgezette onderdelen, raw klassen in Peil-vorm (ADR-0017 punt 2) |
| Fase 2c (tabel + toetsenbord bij elke grafiek) | gebouwd; prognose-stippellijn in de Opslagtrend niet (ADR-0017 punt 10) |
| Fase 2d (systeemfont, ≥ 14 px, body 16 px) | gebouwd; overige inline styles (geen lettermaat/statuskleur) blijven |
| `system.hub_url` (core) | gebouwd; OpenAPI byte-gelijk (383 paden, sha256 `54eee4d7…3e59a`) |
| Tests | vitest (`tests/unit`), `e2e/peil.spec.ts`; bestaande e2e bijgewerkt waar woorden of de modale dialoog dat vroegen |
| Afdrukken "na" | [`docs/screenshots/peil/na/`](../screenshots/peil/na/README.md) |
| Android (§10) | **niet in deze stap** |

**NIET GEVALIDEERD:** echte schermlezer (NVDA/TalkBack), 200 %-zoom, forced colors op MediaGuard zelf, echte telefoon,
echte NAS.

---

## 14. Stand na stap 4 (Android)

Gebouwd in `apps/android` (commits "Android: Peil-tokens …", "Android: thema, componenten en schermen …", "CI: …", "Tests:
…", "Docs: …"); bouwkeuzes in [ADR-0018](../adr/ADR-0018-peil-android-bouwkeuzes.md). Afdrukken "na":
[`docs/screenshots/peil/android/`](../screenshots/peil/android/README.md).

| Onderdeel (§10) | Stand |
|---|---|
| §10.1 tokens → Compose | gebouwd: `tools/peil_tokens.py` → `ui/theme/PeilTokens.kt` (sha256 `9583506d…a30e1`), `MgColors` uit Peil zonder violet, Material 3-rollen uit Peil, vormen 4/6/8/12 dp, systeemfont (Inter weg), rollen 14–30 sp; `PeilTokensTest` + CI `--check` |
| §10.2 status icoon + tekst + vorm | gebouwd: `StatusBadge` (pil, 2 dp, gestippeld/gearceerd), `PeilLabel` (neutraal/sterk), `Melding`, `StatusPanel`; lucide als vector drawables (55, `material-icons-extended` weg); noodstop = Gepauzeerd, uit = neutraal "Uit"; banen `microchip`/`cpu` in `domain-lane-*`; "Wat actie nodig heeft" per soort; tellers in inkt |
| §10.3 per scherm | gebouwd voor Overzicht, Activiteit, Bibliotheek (+ detail, ontbrekend), Ruimte, Aanvragen (+ venster), Instellingen en Koppelen; systeemmeldingen ongewijzigd. **Afwijking:** bij verouderde gegevens blijft de bediening aan (ADR-0018 punt 8); het Foutpaneel heeft geen code/trace-ID (punt 9). Episodekaart: de app heeft er geen (seizoenen als lijst), dus niets om om te zetten |
| §10.4 toegankelijkheid | gebouwd: koppen (`heading`), status met toegankelijke naam (woord + reden), voortgang met `progressBarRangeInfo`, icoonknoppen met naam ("Vorige dag", "Volgende dag", tijdstip wijzigen), tekst schaalt mee (afdrukken met letterschaal 1,3), geen ellips op inhoud. **NIET GEVALIDEERD:** TalkBack, echte telefoon, fel licht |
| §11.2 tests | `DesignLogicTest`/`AppLogicTest` naar de Peil-woorden en -vormen; nieuw `PeilTokensTest`, `PeilContrastTest`, `PeilUiTest`; Paparazzi 18 schermen × licht/donker × 393/360 + grote letter (78 referentiebeelden), in CI `verifyPaparazziDebug` |
| Builds | debug-APK en niet-ondertekende release-APK (`MG_ANDROID_UNSIGNED=1`) lokaal gebouwd; ondertekening en update-keten ongewijzigd |
| Rooktest emulator | **Geblokkeerd**: geen toegang tot `/dev/kvm` in de bouwomgeving (zie ADR-0018, Gevolgen) |

**Nieuwe en gewijzigde Android-teksten** (de app is alleen NL, ADR-0016 punt 4; en-US hier vastgelegd):

| NL (in de app) | en-US |
|---|---|
| Noodstop actief (was: Noodstop staat aan) | Emergency stop active |
| Alles loopt — niets nodig van jou (was: Alles in orde) | All running — nothing needed from you |
| Alles gezond · Totaalstatus | All healthy · Overall status |
| Gezond · Beperkt · Storing · Onbekend · Verouderd · Gepauzeerd · Niet gekoppeld · Niet beschikbaar | Healthy · Degraded · Down · Unknown · Stale · Paused · Not linked · Not available |
| Wat actie nodig heeft (was: Wacht op jou) | What needs action |
| Dit is geen storing; er wacht een voorwaarde. | This is not a fault; a condition is waiting. |
| Laatst bekend · {tijd} · (laatst bekende waarde, verouderd) | Last known · {time} · (last known value, stale) |
| Laden is mislukt (was: Kon dit niet laden) | Loading failed |
| Laden… (alleen voor TalkBack) | Loading… |
| De sleutel is geweigerd · Opnieuw koppelen · Koppelen is mislukt · Verbinden… | The key was rejected · Pair again · Pairing failed · Connecting… |
| De AV1-engine reageert niet. (kop) · MediaGuard probeert het automatisch opnieuw. | The AV1 engine is not responding. · MediaGuard retries automatically. |
| Laatste poging mislukt (kop van de ntfy-fout) | Last attempt failed |
| Uit (noodstop) | Off |
| Vorige dag · Volgende dag · Tijdstip {hh:mm} wijzigen (alleen voor TalkBack) | Previous day · Next day · Change time {hh:mm} |
| Aandacht {n}: {n} dingen wachten op jou, waarvan {e} met een fout (alleen voor TalkBack) | Attention {n}: {n} things are waiting for you, {e} with an error |
| {initialen}, profiel en instellingen (alleen voor TalkBack) | {initials}, profile and settings |
| Iconen: Lucide — ISC-licentie (was: Lettertype: Inter — SIL Open Font License 1.1) | Icons: Lucide — ISC licence |
| Onbekend (Waar ging de ruimte heen, nog niet gemeten; was "—") | Unknown |

**NIET GEVALIDEERD:** echte telefoon, emulator, TalkBack, fel zonlicht, echte NAS. Geen Excel-status gewijzigd.

## 15. Stand na stap 5 (onafhankelijke ontwerpreview)

De omgezette web-UI en Android-app zijn onafhankelijk getoetst tegen Peil 1.1.0; verslag in
[PEIL_REVIEW.md](PEIL_REVIEW.md), besluiten in [ADR-0019](../adr/ADR-0019-peil-review-correcties.md), bewijs in
[`docs/screenshots/peil/review/`](../screenshots/peil/review/README.md). In de web-UI zijn 16 bevindingen hersteld (o.a.
tabellen op 1024–1279 px, tekstlinks, statusvormen, Onbekend in plaats van "—", meetgat in de lijngrafiek, 44 px); de
Android-app vroeg geen herstel. De tabelregel van §6.6/§11 ("tabel ≥ 768, kaartenlijst daaronder") geldt sindsdien
voor de breedte van de **inhoud** (containerquery, 60 rem) in plaats van de schermbreedte.

## 16. Stand na de QA (onafhankelijke controle van stap 1–5)

Besluiten in [ADR-0020](../adr/ADR-0020-qa-peil-ronde.md).

- **Testmatrix §11.3 nu echt in CI.** De Peil-e2e liep eerst op 5 breedtes, met per scherm om en om licht of donker.
  Hij miste 360 px, tekst < 14 px, ellips, kopsprongen, `prefers-reduced-motion` en forced colors; die stonden alleen
  in het reviewscript van stap 5. Nu loopt `e2e/peil.spec.ts` over 320/360/390/768/1024/1440 × licht én donker, met
  dezelfde metingen als `review.mjs`.
- **`system.hub_url` alleen voor een beheerder** (§9.1).
- **OpenAPI-export** `docs/openapi/mediaguard.openapi.json` met een drift-test. De inhoud is byte-gelijk aan de
  nulmeting van §1.
