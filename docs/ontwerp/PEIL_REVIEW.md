# MediaGuard in Peil 1.1.0 — onafhankelijke ontwerpreview (stap 5)

**Datum:** 9 november 2026 · **Rol:** onafhankelijke ontwerpreviewer (design-agent) · **Onderwerp:** de omgezette
MediaGuard-web-UI (`apps/web`, stap 3) en de Android-app (`apps/android`, stap 4).
**Getoetst aan:** Peil 1.1.0 (`design/ontwerptaal/ONTWERPTAAL.md`, `COMPONENTEN.md`, `tokens.json`; versie-gepinde kopie,
[HERKOMST](../../design/ontwerptaal/HERKOMST.md)), de besluiten van de gebruiker (productnaam, "Terug naar de Hub",
tabelweergave bij grafieken, Android in Peil), [PEIL_MIGRATIE.md](PEIL_MIGRATIE.md) en ADR-0016/0017/0018.
**Besluiten van deze review:** [ADR-0019](../adr/ADR-0019-peil-review-correcties.md). **Bewijs:**
[`docs/screenshots/peil/review/`](../screenshots/peil/review/README.md) (schermafdrukken + `review.json`).

## 1. Oordeel

**Goedgekeurd na herstel.** De omzetting van stap 3 en 4 is grondig: geen enkele axe-overtreding (ook niet
moderate/minor), geen horizontale overloop op 320 px, een tweekleurige focusring die nooit achter een balk valt,
Onbekend gestippeld en nooit groen. De review vond wel fouten die de eigen metingen van stap 3 niet konden zien,
omdat die alleen axe serious/critical, overloop en hoogte van knoppen in `<main>` telden. Alle gevonden fouten in de
web-UI zijn hersteld en opnieuw gemeten op de echte app; in de Android-app is niets gevonden dat herstel vroeg.

| Ernst | Gevonden | Hersteld | Voorbeeld |
|---|---|---|---|
| Hoog | 4 | 4 | tabellen op 1024–1279 px tot één letter breed geknepen; tekstlinks niet als link te zien; statuspillen zonder icoon of op de verkeerde as; wig over een meetgat in de lijngrafiek |
| Middel | 5 | 5 | Onbekend als "—"/eindeloos skelet/"Nog geen metingen"/rode pil; doelen < 44 px; microcopy; laatste foutpagina; ontbrekende Bron/Bijgewerkt op de StatKaart |
| Laag | 7 | 7 | kapitalen-labels, 11,2 px-tekst, opacity op tekst, dubbele id, kapot-plaatje-icoon, gereserveerde iconen verkeerd gebruikt, pijl op eigen regel |
| Bewust niet gewijzigd (met reden) | 6 | — | §7 |

Geen enkele correctie verandert een functie, route, API-aanroep, foutformaat, datamodel, poort of de installatie.
OpenAPI is byte-gelijk gemeten (383 paden, sha256 `54eee4d7853f58e8a7cc1a79bcb569d6d8b44e9a6a2c54e6a92e213dce03e59a`,
gelijk aan de nulmeting van stap 2). `data-testid`'s en rollen zijn gebleven; één e2e-tekstbewering volgt een nieuwe
knoptekst.

## 2. Werkwijze

- **Echte app, testdata.** Echte core (`python -m mediaguard`, verse datamap, `MG_START_BACKGROUND=0`) en de echte
  productiebuild van de web-UI (`npm run build` → `node server.mjs`), alleen op 127.0.0.1, gevuld met de nep-Sonarr/
  Radarr uit de coretests (`docs/screenshots/peil/voor/hulp/start.sh` + `vul.py`: 5 series, 53 afleveringen, 5 films,
  2 quarantainerijen, gebruikers beheerder/kijker/engels). Chromium van Playwright 1.63, `nl-NL` (en-US voor de
  `en-us-*`-toestanden), `Europe/Amsterdam`, `prefers-reduced-motion: reduce`, schaal 1, de fonts van deze machine
  (Noto Sans als systeemfont — strenger dan de CI-runner, zie testcontrole).
- **Eigen meting, los van stap 2/3** ([`review.mjs`](../screenshots/peil/review/review.mjs)). Per pagina:
  1. **axe-core 4.14** met `wcag2a, wcag2aa, wcag21a, wcag21aa, wcag22aa` — **elke** overtreding telt, niet alleen
     serious/critical;
  2. **contrast in de gerenderde pagina**: elke tekstknoop, met opacity van alle voorouders, alfa en de
     arceerstrepen als extra achtergrond (laagste paar telt), ook SVG-tekst en placeholders; tekst ≥ 4,5:1
     (groot ≥ 3:1);
  3. **UI-randen ≥ 3:1**: velden (ook de rand van een omhullend zoekveld) en het spoor van schakelaars;
  4. **horizontale overloop** op 320/360/390/768/1024/1440;
  5. **aanraakdoelen ≥ 44 × 44 px** voor elk bedienbaar element (knoppen, links, velden, tabs, schakelaars, summary,
     selectievakjes via hun label); alleen een link of vakterm in een zin is uitgezonderd (WCAG 2.5.8);
  6. **tekstlinks onderstreept**; geen tekst < 14 px; geen ellips op inhoud; één h1, geen kopsprongen, geen dubbele
     id's;
  7. **groen**: elk element in een healthy-tokenkleur (tekst, vlak, rand, icoon) met zijn context, plus elke andere
     groene tint buiten grafieken;
  8. **statusvorm**: elke statuspil en `[data-status]` moet icoon + woord hebben; onbekend/verouderd gestippeld,
     nooit pauze-icoon, nooit groene tekst.

  Daarnaast een **Tab-doorloop** (70 stops) met ringdikte, **ringcontrast** t.o.v. de achtergrond en **bedekking**
  door vaste balken (5 meetpunten per element), en een zoektocht naar **geknepen tekst** (tekst die per letter breekt;
  [`knijp.mjs`](../screenshots/peil/review/knijp.mjs)) — zo zijn de tabellen op 1024–1279 px gevonden. De
  NL/en-US-teksten zijn met [`teksten.mjs`](../screenshots/peil/review/teksten.mjs) tegen de Peil-woordenlijst gelegd.
- **Toestanden nagebootst** door API-antwoorden in de browser te onderscheppen (staat per afdruk in `review.json`,
  veld `nagebootst`): core onbereikbaar (502 `upstream_unreachable`), verouderd (eerst echte data, daarna 502, 70 s
  wachten), AV1-engine actief (zelfde vorm als `e2e/studio-av1.spec.ts`, met een meetgat), plus en-US en kijker.
- **Skills:** `design:accessibility-review`, `design:design-critique`, `design:design-system`, `design:ux-copy`,
  `design:design-handoff` en `dataviz` (vóór het oordeel over grafieken; de palet-validator is opnieuw gedraaid).
- **Android:** de 78 referentiebeelden zijn opnieuw vergeleken met de code (`verifyPaparazziDebug`), en de 114
  documentatie-afdrukken (licht/donker, 360/393 dp, volledig, grote letter) zijn bekeken.
- **Eerlijk over mijn eigen meting:** de eerste volledige run had een fout in mijn contrastmeting (de opacity van een
  element werd op de gedeelde kleurcache vermenigvuldigd, waardoor latere elementen steeds lichter leken: 522
  schijnfouten, o.a. op "Hoe werkt MediaGuard"). Na herstel van het script is het contrast opnieuw gemeten; de
  contrastcijfers van die eerste run zijn niet gebruikt. Ook schakelaars (rand zit op het spoor, niet op de knop) en
  het pijltje van een `<select>` gaven schijnfouten; ook die zijn in het script hersteld.

## 3. Uitkomst in cijfers

Alle 32 routes × 6 breedtes × licht/donker = **384 paginaweergaven**, plus **44 toestanden** en **36 Tab-doorlopen**.
"Vóór" = de app zoals stap 3/4 hem opleverde (HEAD `de6df49`), "na" = na deze review.

| Meting (eigen script) | Vóór | Na |
|---|---|---|
| axe-overtredingen (alle impactniveaus) | 0 | 0 |
| Pagina's met horizontale overloop (ook 320 px) | 0 | 0 |
| Aanraakdoelen < 44 px (buiten een zin) | 608 | 0 |
| Tekstlinks zonder onderstreping | 672 | 0 |
| Statuspillen zonder icoon | 24 | 0 |
| Tekst < 4,5:1 in de pagina | — (zie §2) | 0 |
| Laagste tekstcontrast in de pagina | — | 7,61:1 (rode knoptekst op wit; alle tekst ≥ 7:1) |
| UI-randen < 3:1 | — (zie §2) | 0 |
| Tekst < 14 px | 0 op de schermen · 16 in de toestanden met een actieve engine ("· Stap 2 van 4", 11,2 px) | 0 |
| Dubbele id's | 12 (`flow-arrow`) | 0 |
| Geknepen tekst (per letter) op 768/1024/1100/1280 | 5 routes op 1024 px (bibliotheek, ontbrekend, kalender, terugzetten, gebruikers), 3 op 1100 px | 0 routes |
| Groen bij iets anders dan gemeten gezondheid | 0 | 0 |
| Tab-doorlopen: stops zonder ring / ring < 3:1 / bedekt | — | 0 / 0 / 0 (laagste ring 7,55:1, eerste stop "Naar inhoud") |

Groen komt na de review alleen nog voor bij **"Vrijgavecontrole: geslaagd"** (gemeten decodecontrole) en de cel
**Gezond** in de koppelingsmatrix (gemeten: de instantie komt overeen met de gewenste instelling), beide met
`circle-check`/✓ en woord. De volledige e2e-suite (`npx playwright test`, nu met de strengere Peil-controles) slaagt op de
eindstand: 150 geslaagd, 4 overgeslagen (afdrukspecs die alleen met een uitvoermap draaien), 0 mislukt.

## 4. Bevindingen en reparaties

### Hoog

1. **Tabellen op 1024–1279 px tot één letter breed geknepen** (Peil §8 ResponsieveLijst, WCAG 1.4.10). Sinds de
   zijbalk pas vanaf 1024 px verschijnt, is de inhoud daar ± 720–970 px breed, maar de stapeltabel keek naar de
   schermbreedte (≤ 860 px). Gemeten op 1024 px: titelkolom in Bibliotheek 11 px breed, "Terugzetten"-knop letter
   voor letter onder elkaar, "Uitschakelen" bij Gebruikers 236 px hoog, idem Ontbrekend; de kalenderweek brak titels
   midden in een woord en "Verwacht" viel buiten de dagkaart. De koppelingsmatrix (13 kolommen) knipte zelfs op 1440 px
   kolomkoppen af ("Kwaliteitsdefiniti…") en brak de instantienaam midden in een woord. **Hersteld** met
   containerquery's: tabel → kaartenlijst onder 60 rem inhoud, de matrix onder 90 rem (per instantie een kaart met
   per sectie woord + teken), dagen lopen om. Na herstel 0 geknepen elementen op 768/1024/1100/1280.
2. **Tekstlinks niet als link te herkennen** (WCAG 1.4.1, Peil `--link` "altijd onderstreept"). `mg.css` zette elke
   link op `color: inherit` zonder streep; "Bekijken" achter de opslagregel, "Wat kwam erbij?", "TrueNAS koppelen" en
   "en nog 1 — bekijk alles" zagen eruit als gewone tekst, vervolglinks hadden geen streep (672 meldingen). **Hersteld**
   (correctie n in `styles/peil-mg.css`).
3. **Status zonder icoon of op de verkeerde as** (Peil §4.1–4.2, WCAG 1.4.1). "Vereist" bij Koppelingen was een rode
   Storing-pil zonder icoon voor een instelling die nog open staat (regel 12: nooit rood); plan- en
   voorbeeldwijzigingen kleurden rood/groen/amber (diff zonder rood/groen, C-20); de Dolby Vision-legenda kleurde op
   volgorde; de koppelingsmatrix gaf status alleen met kleur (en "Nog niet gecontroleerd" leek een leeg
   selectievakje); "Nog niet gecontroleerd" en "Identiteit: nog niet gecontroleerd" waren amber in plaats van
   Onbekend; `StatusChip icon={null}` gaf op 15 plekken een statuskleur zonder icoon. **Hersteld** (ADR-0019 punt 4).
4. **Wig over een meetgat in de lijngrafiek** (Peil §10 eerlijkheid). Het 10 %-vlak onder GPU werd als één vorm over
   alle lijnstukken gesloten en tekende een driehoek van linksonder naar het laatste punt vóór het gat — alsof er
   waarden waren waar geen meting was. **Hersteld:** één vlak per stuk tot de basislijn en een gearceerd vak waar geen
   enkele reeks meet (zoals bij de staven). Unit-test toegevoegd.

### Middel

5. **Onbekend werd "—", een eindeloos skelet, "Laden…", "Nog geen metingen" of rood** (Peil §4.4, §9). Met de core
   weg toonden Beschikbaar/Ontbrekend/Fouten voor altijd "Laden…"; de AV1-kaarten een "—" met de Onbekend-pil
   eronder; Belasting en Besparing per dag "Nog geen metingen"; de kopbalkpil een rode rand met "—"; Activiteit de ruwe
   Engelse servertekst "Upstream service unreachable" als rode titel; het opslagoverzicht een rode Storing. **Hersteld:**
   overal "Onbekend — MediaGuard-core/AV1-engine reageert niet" (stippel), het gedeelde Foutpaneel, en de
   AV1-banner als Onbekend (`wifi-off`) in plaats van amber.
6. **Aanraakdoelen < 44 px** (Peil §6): categoriechips Instellingen (28 px), "Terug naar Instellingen" (32 px), losse
   vervolglinks (21 px), vaktermen buiten een zin (25 px), selectievakjes in tabellen (20 px), zoekveld van de
   titelkiezer (40 px), "View"/"Bekijken" (31 px breed), kopbalkpil "—" (42 px breed). **Hersteld** (608 → 0).
7. **Microcopy** (Peil §11, NL + en-US): "Doorgaan" (eerste start, migratie), "In orde" (migratiecontrole,
   back-up, codelabel), "Beperkt automatisch" (AI-stand; botste met het statuswoord Beperkt), "let op", de
   engine-melding met twee keer "probeert het automatisch opnieuw", "Wat betekenen de kleuren?", "Bijgewerkt —",
   voorwaarden bij "Herstel nog niet gereed" zonder leesteken achter elkaar, en-US "Requires backend endpoint …" naast
   NL "Nog niet beschikbaar in deze versie". **Hersteld** (ADR-0019 punt 8); `teksten.mjs`: 3 390 sleutels in beide
   talen, geen ontbrekende vertaling.
8. **Laatste foutpagina** (`app/global-error.tsx`): altijd donker, tekst met 80 % opacity, primaire knop 3,7:1, rand
   1,5:1. **Hersteld** met de Peil-tokenwaarden (licht/donker volgens het systeem, ring, 44 px).
9. **StatKaart zonder Bron en Bijgewerkt** (Peil C-05, principe 6; PEIL_MIGRATIE §6.3 vroeg dit al). **Hersteld** op
   Overzicht, Ruimte en Ontbrekend: "Bron · Bijgewerkt 22:11 (nu)" met `<time>`; Fouten toont alleen de bron (het
   gezondheidsmodel heeft geen tijdstempel).

### Laag

10. Kapitalen-labels "MEER HIEROVER" (Instellingen) en de kalenderdagen → gewone kleine koppen (Peil §5).
11. "· Stap 2 van 4" in de fasebalk was 11,2 px (`<small>` = 80 %) → 14 px.
12. Opacity op tekst: SVG-subtekst in het stroomschema (5,93:1), een uitgezette worker-plek, het uitgeschakelde
    uploadvak; een gepauzeerde worker had een amber rand → gedempte kleur/stippel, Gepauzeerd = info.
13. Dubbele id `flow-arrow` (beide stroomschema's staan in de DOM) → eigen id per schema.
14. Kapot-plaatje-icoon bij een poster die niet laadt → de gegenereerde posterachtergrond blijft zichtbaar.
15. Gereserveerde iconen (Peil §7): `triangle-alert` voor "Aandacht" en bij rode foutregels, `shield-check` voor een
    voorwaarde, `circle-check` als KPI-label "Hersteld" → `list-checks`, `octagon-x`, `lock`, `check`.
16. De pijl in "6 dingen vragen aandacht →" viel in de zijbalk op een eigen regel → vaste spatie.

## 5. Controle per opdracht

### (1) Schermafdrukken en vergelijking met de prototypes

104 afdrukken van de hoofdschermen (Overzicht, Activiteit, Bibliotheek, titeldetail, Ontbrekend, Kalender, Ruimte,
Terugzetten, Aanvragen, Instellingen, Koppelingen, Systeem, Inloggen) op 360/390/768/1440 × licht/donker, plus 44
toestanden en 4 grafiekafdrukken, alle in [`review/`](../screenshots/peil/review/README.md). Zelf bekeken met Read
(lange pagina's in kolommen), naast `definitief/shots/` van de prototypes:

- **Overzicht ↔ `home.html`:** zelfde volgorde (status → wat actie nodig heeft → StatKaarten), zelfde vormen (amber
  rand bij Beperkt, gestippeld Onbekend, inkttellers). Verschil: het prototype heeft een SysteemStatusPaneel met een
  rij per onderdeel en "Vernieuwen elke 60 s"; MediaGuard toont één totaalstatus met een telling (ADR-0017 punt 4) en
  ververst Overzicht niet periodiek (gedrag, al bekend uit stap 2). Na deze review hebben de StatKaarten wel Bron en
  Bijgewerkt zoals het prototype.
- **Ruimte ↔ `appdetail-metrieken.html`:** stattegels → grafiek met gat en tabel. De grafiek toont nu een gearceerd
  gat ("Geen meting") zoals het prototype; de grenslijn ontbreekt omdat Belasting geen grens heeft.
- **Activiteit/Problemen ↔ `problemen.html`:** lijst met rij-icoon per soort, knop aan het rij-einde; tabs lopen om.
- **Koppelingen ↔ dekkingsmatrix (§9):** cel = teken + kleur + toegankelijke naam (was alleen kleur).
- **Instellingen/Kalender/Terugzetten op 1024 px:** zie bevinding 1; na herstel kaartenlijst/omlopende dagen.

### (2) axe, contrast, overloop, doelen, toetsenbord

Zie §3: 0 axe-overtredingen (alle impactniveaus) op 384 paginaweergaven en 44 toestanden; tekst ≥ 4,5:1 en UI-randen
≥ 3:1 in de gerenderde pagina; geen overloop op 320 px; geen doel < 44 px buiten een zin; elke tabstop een ring van
3 px (laagste ringcontrast 7,55:1) die nooit bedekt is; de skiplink is de eerste tabstop.

### (3) Onbekend/verouderd nooit groen, status nooit alleen kleur, microcopy

- In de toestanden "core weg", "verouderd" en "engine actief" is geen enkel element groen; elke onbekend/verouderd-vorm
  is gestippeld met `circle-help`/`clock-alert`/`wifi-off` en een woord, nooit een pauze-icoon. Gepauzeerd (noodstop,
  baan) = `circle-pause` op info.
- Elke statuspil heeft icoon + woord + vorm (0 meldingen na herstel). Beschikbaarheid (Binnen, Ontbreekt, Verwacht)
  blijft neutraal.
- Woorden: Gezond · Beperkt · Storing · Onbekend · Verouderd · Gepauzeerd en en-US Healthy · Degraded · Down ·
  Unknown · Stale · Paused, gelijk in beide talen (`teksten.mjs`).

### (4) Toegestane toevoegingen

- **"Terug naar de Hub":** leeg adres → geen link; ongeldig (`javascript:…`) → 422 en geen link; geldig fictief
  testadres `http://hub.example:30080` → link in de zijbalk (44 px) en als eerste item van het profielmenu op 390 px;
  weer leeg → link weg. Afdrukken `toestanden/hub-link-*`. Het adres is daarna weer leeggemaakt.
- **Tabelweergave + toetsenbord bij grafieken** (nagebootste engine): Besparing per dag — Home leest "27 okt: 37,3 GB
  bespaard, 2 encodes", 9× pijl rechts leest "Geen meting" voor de ontbrekende dag, Esc wist; "Toon als tabel" geeft
  14 rijen met `th scope`, de ontbrekende dag als "Geen meting" en één gearceerd vak. Belasting — idem per minuut,
  60 rijen, de minuten zonder meting als "Geen meting". axe 0 in beide thema's met de tabel open.
- **Palet** (dataviz-validator opnieuw gedraaid): Peil chart-1…5 licht en donker PASS; Belasting met 3 lijnen op alle
  paren PASS.

### (5) Android

- `verifyPaparazziDebug`: 278 tests, 0 fouten (138 overgeslagen = documentatie-afdrukken die alleen met
  `PEIL_AFDRUKKEN=1` draaien): de 78 referentiebeelden zijn gelijk aan wat de code nu tekent.
- Bekeken (licht en donker, 360/393 dp, volledig en grote letter): Overzicht in alle vier toestanden, Activiteit,
  Bibliotheek (+ detail, ontbrekend), Ruimte, Aanvragen (+ venster), Instellingen, Koppelen (+ geweigerd). Geen
  groen buiten "Alles gezond"; Verouderd gestippeld met "Laatst bekend"; Noodstop actief = Gepauzeerd; doelen ≥ 48 dp;
  niets breekt midden in een woord bij grote letter. **Geen herstel nodig.**
- Eén consistentiepunt (niet hersteld, vervolgpunt): "AV1-besparing" heeft op Android een oranje legendablokje
  (chart-2, de kleur van een negatieve post) en op de web een aqua blokje (chart-3, de kleur van de post); de balken
  zelf volgen op beide de polariteit. Kleur volgt de entiteit (dataviz) — kiezen vraagt een besluit voor beide
  oppervlakken.

## 6. Wat al goed was (niet aangeraakt)

Tokens en aliaslaag (driftcontrole `npm run check:peil` groen), systeemfont, 16 px body, focusring met halo en
scroll-padding, de skiplink, de Gezond-vorm zonder vlak, de noodstop als Gepauzeerd, de bibliotheekvergrendeling als
GateBlokkade ("Dit is geen storing; er wacht een voorwaarde."), de Hub-link-validatie, de grafiektweeling en het
Android-thema uit `tokens.json`.

## 7. Bewust niet gewijzigd

1. **"Vrijgavecontrole: geslaagd" blijft groen** — gemeten decodecontrole = "gemeten en goed" (Peil §4.1 regel 1).
2. **"Niet beschikbaar"** bij een aflevering (indexers vinden niets) blijft een neutraal label met domeinbetekenis;
   het woord botst met Peils capability-term — vervolgpunt voor de ontwerptaal.
3. **Teksten uit de core zijn alleen Nederlands** (`title_nl`, `detail_nl`, `summary_nl`): in en-US staan bv. "Wacht op
   jou: herstelcode instellen" en "Laatste 24 uur: …" nog in het Nederlands. Gelijkwaardig maken vraagt een API- of
   corewijziging (buiten deze ronde, "API-routes veranderen niet").
4. **Native `<input type="time">`/`<select>`:** 12-uursweergave in de headless browser en een afgekapte gekozen optie
   komen van de browser.
5. **Bediening uit bij verouderde gegevens** en de **kijker-reden zichtbaar naast elke knop**: gedrag, al vastgelegd
   in ADR-0017/0018 als vervolgpunt.
6. **Tabelselectie in de tussenvorm (1024–1279 px):** de selectievakjes blijven zichtbaar in de kaartenlijst (alleen de
   telefoon verbergt ze, zoals voorheen), zodat er geen functie wegvalt.

## 8. NIET GEVALIDEERD

Echte schermlezer (NVDA, TalkBack), 200 %-zoom en forced colors op MediaGuard zelf, een echte Android-telefoon of
emulator (KVM niet beschikbaar in deze sessie, zie ADR-0018), tv/speler, fel zonlicht en een echte NAS met een echte
AV1-engine, Seerr of recovery-worker. Alle toestanden met een actieve engine zijn **Sim-getest** (nagebootst).

## 9. Uitgevoerde controles (exacte commando's)

Zie het eindrapport van deze stap en [`review/README.md`](../screenshots/peil/review/README.md#opnieuw-uitvoeren).
Samengevat: `npm run lint`, `npm run typecheck`, `npm run test:unit` (54 geslaagd), `npm run check:peil`,
`nice -n 10 npm run build`, `npx playwright test` (volledige e2e), `review.mjs alles`, `knijp.mjs`, `teksten.mjs`,
de OpenAPI-hash en `:app:verifyPaparazziDebug`.

## 10. Vervolgpunten (handoff)

| Punt | Voor wie | Wat en acceptatie |
|---|---|---|
| en-US voor serverteksten | core + web | core levert naast `*_nl` ook `*_en` (of een sleutel); web toont de taal van de gebruiker. Acceptatie: `en-us-*`-afdrukken zonder Nederlandse zinnen. |
| "Niet beschikbaar" (aflevering) ↔ capability | ontwerptaal | ander woord voor "indexers vinden niets" (bv. "Niet te vinden") in Peil vastleggen, dan web + Android. |
| Kleur in "Wat kwam erbij / Waar ging de ruimte heen" | web + Android | nu twee coderingen: de segmentbalk en de legendablokjes kleuren per post (chart-1…5), de rijbalken per teken (chart-1 plus, chart-2 min), en Android kleurt het blokje van AV1-besparing oranje (chart-2) waar de web aqua (chart-3) toont. Eén regel kiezen (dataviz: kleur volgt de entiteit) en op beide oppervlakken gelijk toepassen. |
| Overzicht periodiek verversen | codebouwer | zodat Verouderd (gestippeld, "Laatst bekend") ook op Overzicht kan verschijnen (Peil §9, O-03). |
| SysteemStatusPaneel met rijen per onderdeel | codebouwer/ontwerp | zoals `home.html`: rij per onderdeel met reden; acceptatie: totaal = slechtste onderdeel. |
| Echte toestellen | tester | NVDA/TalkBack, 200 %-zoom, forced colors, emulator-rooktest (KVM), echte NAS. |

## 11. Ontwerpreview mediaserver-tabblad (meerdere mediaservers, S-P2-590)

**Datum:** 11 november 2026 · **Rol:** R-ONTWERP (ontwerpreview na de bouw) · **Onderwerp:** Instellingen → Koppelingen →
Mediaservers & ondertitels (`apps/web/components/mediaserver/mediaserver-tab.tsx`: serverlijst met koppelstatus per server,
formulieren, dialogen, ondertitelkaart, rollen, en-US) na het herstel van de afgebroken ronde 3 (A-BOUW e92a7f1…e5fdb56,
A-HERSTEL 9032f75, d3b4d2e, 25135cb). **Getoetst aan:** Peil 1.1.1 (gepinde kopie `0a7143b`; ONTWERPTAAL §4, §11, §12,
§13.3; COMPONENTEN C-03, C-14, §20, §29) en ADR-0022. **Gemeten op:** de gepushte commit `25135cb` ("vóór") en op het
herstel van deze review ("na": de code van commit `785174d` met `meet.mjs` van `8b453a5`; nagemeten op `8b453a5`,
208 van 208; R-EERLIJK-1 van S-P2-590). **Oordeel:** goedgekeurd na herstel van één belangrijke en één kleine
bevinding; vijf kleine punten blijven open (§11.5).

### 11.1 Werkwijze

- **Echte dienst, testdata, Sim-getest.** Echte core (`python -m mediaguard`, verse datamap, `MG_START_BACKGROUND=0`) en
  de echte productiebuild van de web-UI (`npm run build` → `node server.mjs`), alleen op 127.0.0.1, met de nep-Sonarr/Radarr
  en twee nep-mediaservers (een Emby en een Jellyfin 12.x die alleen "Verbinding testen" beantwoorden):
  `hulp/start.sh` + `hulp/vul.py` met een nieuwe werkmap buiten de repo (`MGP_WERK`; de oude metingen van ronde 3 en van
  A-BOUW zijn blijven staan). `vul.py` maakt zes servers: 1 Emby en 2 Jelly Leon gezond (echt gemeten tegen de nep),
  3 zonder contact (adres waar niets luistert), 4 ontkoppeld, 5 zonder sleutel, 6 met een meting van drie uur oud.
- **Meting:** `docs/screenshots/peil/review-mediaserver/meet.mjs alles` met Playwright 1.65 (Chromium) en axe-core 4.14 uit
  `ai-server-manager/design/ontwerptaal/bouw`, `nl-NL`/`en-US`, `Europe/Amsterdam`, reduced motion, schaal 1, de
  systeemlettertypes van de ontwikkelmachine. Per afdruk: axe (`wcag2a, wcag2aa, wcag21a, wcag21aa, wcag22aa`, elk
  impactniveau), contrast in de gerenderde pagina (met opacity, alfa en arcering), randen van bediening, overloop van de
  pagina en binnen kaart of dialoog, afgekapte en geknepen tekst, doelen ≥ 44 px, groen buiten gemeten gezondheid,
  statusvorm (icoon + woord + vorm), reden naast elke Onbekend/Verouderd/Ontkoppeld, labels zonder veld, voorbeeldwaarden
  in plaatshouders, uitgeschakeld zonder zichtbare reden, zichtbare knoptekst in de toegankelijke naam (WCAG 2.5.3),
  live-regio's en hun rol. Daarnaast Tab-doorlopen (ring ≥ 3:1, niet bedekt) en de dialogen met het toetsenbord (start
  op Annuleren, Tab blijft binnen, Escape sluit, focus terug naar de opener). De afdrukken zijn bekeken (lijst, één en
  twee servers, ontkoppelde server 1, foutmeldingen, en-US; licht en donker; 390 en 1440). Meetuitvoer en afdrukken staan
  buiten de repo (`PEIL_UIT`, V-023).
- **Nieuw in deze ronde (meet.mjs):** de toestanden `fout-opslaan` (server 2 met een adres met een spatie: de core weigert
  met 422, er wordt niets opgeslagen) en `ondertitels-fout` (dezelfde weigering bij de ondertitelkaart, in de browser
  nagebootst), elk ook in en-US. Eén server, twee servers en een ontkoppelde server 1 worden in de browser nagebootst
  door `GET /mediaserver/status` in te korten of aan te passen (rest = echt antwoord van de core; in `review.json` veld
  `nagebootst`).
- **Skills:** `design:accessibility-review` en `design:design-critique`.

### 11.2 Matrix en uitkomst in cijfers

25 toestanden, 192 schermmetingen, 8 Tab-doorlopen en 8 dialoogdoorlopen = 208 metingen, vóór en na elk 208 van 208
zonder fout (`meet.mjs` faalt bij een ontbrekende meting, V-005).

| Toestand (stap) | Waar in de meting | Breedtes × thema |
|---|---|---|
| Eén server | `een-server` (nl), `en-us-een-server` | 320/360/390/768/1024/1440 × licht/donker; en-US 390/1440 × licht/donker |
| Twee servers | `twee-servers` (nl); en-US: `en-us-server1-ontkoppeld` (server 1 en 2) | als boven |
| Server zonder contact | server 3 in `lijst`, `en-us-lijst`, `test-mislukt`, `operator-test-mislukt`, `fout-opslaan` | 6 breedtes × 2 thema's (nl en en-US) |
| Losgemaakte server 1 | `server1-ontkoppeld`, `en-us-server1-ontkoppeld` | 6 breedtes × 2 thema's; en-US 390/1440 × 2 |
| Overige | lijst met zes servers, toevoegen, wijzigen, twee dialogen, test geslaagd/mislukt, gevuld (lange namen, kijkers, afspeeltests), kijker, operator, foutmeldingen | zie `meet.mjs`, `STATES` |

| Meting | Vóór (`25135cb`) | Na |
|---|---|---|
| axe-overtredingen (alle impactniveaus) | 0 | 0 |
| Tekst < 4,5:1 · randen van bediening < 3:1 | 0 · 0 | 0 · 0 |
| Laagste tekstcontrast in de pagina | 7,61:1 (witte tekst op de knop "Server verwijderen") | 7,61:1 |
| Horizontale overloop · buiten kaart of dialoog · afgekapt · geknepen | 0 · 0 · 0 · 0 | 0 · 0 · 0 · 0 |
| Doelen < 44 px · tekst < 14 px · ellips | 0 · 0 · 0 | 0 · 0 · 0 |
| Groen bij iets anders dan Gezond/Healthy | 0 | 0 |
| Statuspil zonder icoon, woord of juiste vorm | 0 | 0 |
| Onbekend/Verouderd/Ontkoppeld zonder reden | 0 | 0 |
| Labels zonder veld · velden zonder naam · plaatshouders | 0 · 0 · 0 | 0 · 0 · 0 |
| Uitgeschakeld zonder zichtbare reden · knoptekst niet in de naam | 0 · 0 | 0 · 0 |
| Eén h1 · kopsprongen · dubbele id's · JS-fouten | overal · 0 · 0 · 0 | overal · 0 · 0 · 0 |
| Geweigerd verzoek als "mislukt"-uitslag in een live-regio (`role=status`) | **10** (ondertitelkaart, nl en en-US) | **0** (nu `role=alert`) |
| Tab-doorlopen: stops · zonder ring · ring < 3:1 · bedekt | 42 (lijst) / 48 (toevoegen) · 0 · 0 · 0; laagste ring 7,55:1 | gelijk |
| Dialogen: rol · start · Tab binnen · Escape · focus terug | alertdialog · Annuleren · ja · ja · opener (ontkoppelen zonder gevaarvorm, verwijderen met gevaarvorm) | gelijk |

Statusvormen (alle afdrukken): Gezond/Healthy `circle-check` zonder vlak; Storing/Down `octagon-x` met vlak en rand;
Verouderd/Stale `clock-alert`, stippel + arcering; Ontkoppeld/Unlinked en Niet gekoppeld/Not linked `link-2-off`,
gestippeld; Gekoppeld/Linked `link-2`, neutraal. axe "onvolledig" (niet beslist door axe; 184 meldingen, vóór en na
gelijk) is beoordeeld: `color-contrast` (axe kan de achtergrond niet bepalen, o.a. bij arcering en overlappende lagen;
de eigen contrastmeting in de pagina dekt die tekst en vond niets onder 7,61:1) en `aria-hidden-focus` alleen in de
dialoogtoestanden (Radix zet de achtergrond op `aria-hidden` en houdt de focus in de dialoog; de dialoogdoorloop toont
dat Tab binnen de dialoog blijft).

### 11.3 De vier toestanden van de stap

- **Eén server:** kop "Wat MediaGuard met Emby doet" (de server bij naam), telling "1 gekoppeld" als neutrale pil,
  Emby met Gekoppeld · Gezond en de tijd van het laatste contact; feiten als `dl`; geen Verwijderen bij server 1.
- **Twee servers:** "Wat MediaGuard met je mediaservers doet" en hints per gekoppelde server; elke server een eigen
  `article` met h3, pillen, reden, feiten en korte knoppen met de servernaam in de toegankelijke naam.
- **Server zonder contact:** Gekoppeld · **Storing** (`octagon-x`, woord) met de reden "Geen contact: niet bereikbaar
  (ConnectError)" en "Laatste goede contact gisteren. MediaGuard probeert het vanzelf opnieuw."; nergens groen. Een
  mislukte "Verbinding testen" staat als gemeten falen (danger) in de vaste live-regio van die server.
- **Losgemaakte server 1:** de rij blijft staan met alleen **Ontkoppeld** (gestippeld, `link-2-off`), de reden
  "MediaGuard volgt deze server niet. Adres en sleutel zijn bewaard." en één primaire knop **Weer koppelen**
  (en-US: Unlinked, "Link again"). De telling bovenaan telt hem niet mee.

### 11.4 Bevindingen en herstel

| Nr | Ernst | Bevinding | Herstel en controle |
|---|---|---|---|
| R-ONTWERP-1 | belangrijk | `SubtitlesCard.run` zette elk mislukt verzoek (geweigerde invoer, geen rechten, core onbereikbaar; ook een schakelaar) als TestResultaat "mislukt" (danger) in de live-regio `subs-result` (`role=status`). Gemeten in `ondertitels-fout`: 10 van 10 afdrukken `status: Ongeldig adres…`. Een invoerfout zag er zo uit als een gemeten storing van Bazarr, en de kaart deed het anders dan de servers erboven (WCAG 3.2.4, Peil C-14: `role=alert` voor de fout van dít verzoek). Vervolgpunt van A-HERSTEL. | Zoals bij `ServerItem`: de fout staat als `InlineState` danger met `role=alert` onder de knoppen; `subs-result` krijgt alleen een echte testuitslag. Na: 10 van 10 `alert`, 0 `status`. e2e `werk-mediaserver.spec.ts` "ondertitels: … een geweigerd verzoek een melding" (mutatieproef met de oude code: rood op de alert). |
| R-ONTWERP-2 (= R-TECH-6) | klein | Na een geslaagde uitslag bleef die oude uitslag in `ms-result-<id>` staan als een volgend verzoek mislukte, naast de nieuwe melding. Hetzelfde gold na het herstel hierboven voor `subs-result`. | In beide `run`-functies maakt een mislukt verzoek de live-regio leeg (geen "mislukt", wel weg met de oude uitslag); de fout blijft `role=alert`. Nieuwe e2e "een mislukt verzoek na een geslaagde test: de oude uitslag gaat weg, de fout is een melding" (eerst 200, dan 500 op "Verbinding testen"; nagebootst) en een extra stap in de ondertiteltest; met de oude code beide rood. |

**Beoordeling van de nieuwe foutmelding bij een server** (A-HERSTEL, `InlineState` danger `role=alert` onder de
knoppen): in orde. Rood met `octagon-x` en volle tekst (Peil §4.1 regel 12: een veldfout ná een verzendpoging is rood),
de live-regio van het TestResultaat blijft leeg (gemeten in `fout-opslaan`, nl en en-US, licht en donker: alleen
`alert: Ongeldig adres…`), contrast ≥ 7,61:1, geen overloop op 320 px, en met een open dialoog staat de fout alleen in
de dialoog. Op 390 px staat de melding direct onder de knoppen in beeld (gemeten: y 694–758 bij een venster van 800).

### 11.5 Open (klein, buiten deze stap of buiten "uiterlijk")

1. **Focus na een knop met `pending`.** `Button` (`components/mg/primitives.tsx`) zet tijdens een verzoek `disabled`;
   de focus valt dan naar `body` (gemeten na Enter op "Opslaan en testen"). Chromium houdt het startpunt vast: de
   volgende Tab gaat naar "Verbinding testen". Geldt voor de hele app (gedeelde primitive), daarom hier niet gewijzigd;
   voorstel: `aria-disabled` + klik negeren tijdens `pending`, met de test van V-065.
2. **Melding onderaan de pagina half onder de ondernavigatie.** Op 390 px valt de melding of uitslag van de
   ondertitelkaart (laatste kaart) direct na de klik voor de helft achter de ondernavigatie; scrollen maakt hem helemaal
   zichtbaar en hij wordt voorgelezen (`role=alert`/`status`). Voorstel: de melding bij verschijnen met
   `scrollIntoView({ block: "nearest" })` in beeld brengen (de scroll-padding van Peil houdt dan de ondernavigatie vrij).
3. **Veldfout niet bij het veld.** De core antwoordt met een algemene 422-tekst zonder veld; de UI kan "Ongeldig adres"
   daarom niet bij het adresveld zetten met `aria-invalid` (Peil §20). Hoort bij een API-wijziging: open, nog niet gepland.
4. **Knoppen "Jellyfin http://… invullen"** (gevonden servers) lopen met het adres om tot 76 px hoog (gevuld-toevoegen);
   ze blijven binnen de kaart en ≥ 44 px. Al bekend uit ronde 3 (B4).
5. **Serverteksten in en-US** (foutmeldingen en redenen van de core zoals "Ongeldig adres…", "niet bereikbaar") blijven
   Nederlands: vervolgpunt "en-US voor serverteksten" uit §10.

Buiten het tabblad, niet beoordeeld: de pil "Beperkt" in de zijbalk (totaalstatus van de app in de testopstelling).

### 11.6 NIET GEVALIDEERD

Echte schermlezer (NVDA, TalkBack), echte telefoon in fel licht, 200 %-zoom en forced colors op dit tabblad, en een echte
Emby/Jelly Leon op de NAS: niet gedaan. Alles hierboven is Sim-getest (nep-mediaservers, nagebootste toestanden).

### 11.7 Opnieuw uitvoeren

```bash
# vanuit <WERKMAP>; MGP_WERK en PEIL_UIT buiten de repo, het wachtwoord alleen in de omgeving
(cd media-guard-ultimate/apps/web && npm run build)
MGP_REPO=<WERKMAP>/media-guard-ultimate MGP_WERK=<werkmap> MGP_PY=<WERKMAP>/media-guard-ultimate/.venv/bin/python \
  media-guard-ultimate/docs/screenshots/peil/review-mediaserver/hulp/start.sh
MGP_PW=<wachtwoord> MGP_DATA=<werkmap>/data media-guard-ultimate/.venv/bin/python -I \
  media-guard-ultimate/docs/screenshots/peil/review-mediaserver/hulp/vul.py
MG_PEIL_PASSWORD=<wachtwoord> PEIL_PLAYWRIGHT=<WERKMAP>/ai-server-manager/design/ontwerptaal/bouw/node_modules/playwright/index.mjs \
  PEIL_AXE=<WERKMAP>/ai-server-manager/design/ontwerptaal/bouw/node_modules/axe-core/axe.min.js PEIL_UIT=<map buiten de repo> \
  node media-guard-ultimate/docs/screenshots/peil/review-mediaserver/meet.mjs alles
MGP_WERK=<werkmap> media-guard-ultimate/docs/screenshots/peil/review-mediaserver/hulp/stop.sh
```

Daarnaast gedraaid op het herstel: `npm run check:peil`, `npm run lint`, `npm run typecheck`, `npm run test:unit`,
`npm run build` en de volledige Playwright-run (`npm run e2e`); uitkomsten in het rapport van deze stap en in EVIDENCE
C1.3-MGU-04.

## 12. R-ONTWERP S-P2-620: de Android-app in en-US

**Datum:** 12 november 2026 · **Rol:** R-ONTWERP (ontwerpreview na de bouw) · **Onderwerp:** de zes en-US-referentiebeelden
van de Android-app (`EnLightScreenShots`: Overzicht, Activiteit → Problemen, Bibliotheek, Ruimte, Instellingen met
waarschuwingen, Koppelen; licht, 393 dp) naast de Nederlandse, plus een eigen meting van alle 18 schermen in het Engels.
**Getoetst aan:** Peil (ONTWERPTAAL §4, §11, §12, §13), ADR-0023 (vooral punt 7 woordkeuze en punt 8 servertekst) en
§(5) hierboven. **Gemeten op:** commit `05ea62a` (eind van A-BOUW en R-TECH). **Oordeel:** goedgekeurd na herstel van
twee belangrijke bevindingen (§12.3); twee kleine punten blijven open (§12.4).

### 12.1 Werkwijze

- **Echte schermen, voorbeeldgegevens, Sim-getest.** Op Android is de "dienst" de echte Compose-code van de app,
  gerenderd door Paparazzi op de JVM met de voorbeeldgegevens uit `SampleData.kt` (teksten van de server, dus
  Nederlands). Geen emulator of telefoon (V-021).
- **De zes referentiebeelden** in `apps/android/app/src/test/snapshots/images/` (`…EnLightScreenShots_*.png`) zijn
  bekeken naast `…LightScreenShots_*.png` met dezelfde naam.
- **Eigen meting buiten de repo** (kopie van `apps/android` en `design` via `git archive`, met tijdelijke klassen in
  `ScreenShots.kt`): alle 18 schermen in het Engels op volle lengte (2 px per dp) op 360 dp licht en donker, 393 dp
  donker, 360 dp met grote letter (fontScale 1,3) licht, en ter vergelijking 360 dp licht in het Nederlands. Die
  afdrukken staan niet in de repo. Breedtes 320, 768 en 1440 en axe gelden voor de web-UI; voor Android zijn 360 en
  393 dp de afgesproken maten (§(5)).
- **Skills:** `design:accessibility-review` en `design:design-critique`.

### 12.2 Uitkomst per referentiebeeld

| Beeld | Afgekapt of overlap | Nederlands in beeld | Status: woord + vorm + icoon |
|---|---|---|---|
| Overzicht | geen | "Laatste 24 uur: 9 afleveringen…" (`headline`) en "± 300 dagen" (`days_left_nl`) achter "full in": servertekst, ADR-0023 punt 8. Kaarttitel nu "Last 24 hours" (A-BOUW `6445743`) | "Degraded" met `triangle-alert`, warning-vlak en rand; nooit groen |
| Activiteit → Problemen | geen; de vijf tabs lopen netjes om op twee regels | titel, uitleg, bestandsnaam en knop "Origineel houden" van "Wat actie nodig heeft": servertekst (`label_nl`, ADR-0023 punt 8). "This is not a fault; a condition is waiting." is app-tekst in het Engels | foutsoort `octagon-x` op vlak; voorwaarde `lock` neutraal |
| Bibliotheek | geen ("Can improve", "Not monitored" passen) | alleen titels (namen) | labels met icoon + woord, neutraal (beschikbaarheid is geen gezondheid) |
| Ruimte | geen; "Turn on emergency stop" past op één regel | "± 300 dagen" (servertekst) | noodstop "Off" met `power-off`, gevaarknop met rand + icoon |
| Instellingen met waarschuwingen | geen | geen | info-banners met icoon, woord en rand |
| Koppelen | geen | geen | n.v.t. |

Statuswoorden in en-US zijn de Peil-woorden (Healthy, Degraded, Down, Unknown, Stale, Paused; `PeilStatus`,
`TaalTest.statuswoordenVolgenPeil`). Op geen van de zes beelden staat Nederlandse app-tekst; al het Nederlands is
servertekst en zo bedoeld (ADR-0023 punt 8).

Uit de eigen meting (Engels) zijn bekeken: op 360 dp licht Overzicht, Overzicht noodstop, Overzicht verouderd,
Activiteit (Nu en Wat is er gebeurd), Bibliotheek-detail, Ruimte, Aanvragen (Ontdekken en Mijn aanvragen) en
Instellingen; op 360 dp donker Koppelen geweigerd en het aanvraagvenster; met grote letter op 360 dp Ruimte,
Instellingen met waarschuwingen en de bovenbalk van Bibliotheek-detail en Overzicht. Daarin geen afgekapte tekst en
geen overlap, behalve de twee punten van §12.3. De overige afdrukken van de meting (onder meer 393 dp donker en de
rest van de grote letter) zijn gemaakt maar niet bekeken. Ook hier is elk Nederlands woord servertekst (`phase_nl` "Encoderen",
`seerr_status_nl` "Goedgekeurd"/"Beschikbaar", `delivery_state_nl` "Wordt gezocht", seizoenstatus "6 van 8"). Drie
plekken tonen in de meting Nederlands omdat de testopstelling een vaste Nederlandse tekst meegeeft, niet de app:
`overzichtVerouderd` (`staleError`, `phoneWarning`, `update`) en `koppelenGeweigerd` (`error`) in `ScreenShots.kt`; de
app maakt die teksten zelf met `tr(…)` (`ApiClient.kt`, `PhoneChecks.kt`, `Freshness.kt`). Er is van die schermen geen
en-US-referentiebeeld, dus geen beeld is fout.

### 12.3 Bevindingen en herstel

| Nr | Ernst | Bevinding | Herstel en controle |
|---|---|---|---|
| R-ONTWERP-1 | belangrijk | Op een subpagina breekt de titel in de bovenbalk midden in een woord: "Bibliothe/ek" op 360 dp in het Nederlands (ook in de referentiebeelden `Light360`/`Dark360ScreenShots_bibliotheekDetail.png`, al vóór deze stap) en "Libra/ry" in het Engels met grote letter op 360 dp (eigen meting). `TopBar` liet het woord "Aandacht"/"Attention" alleen vallen als het merk niet paste; de titel van een subpagina telde niet mee. §(5) meldde eerder "niets breekt midden in een woord"; dat klopte niet voor dit beeld. | `ui/nav/Bars.kt`, `TopBar`: op een subpagina telt de gemeten breedte van de titel mee; past hij niet naast het hele woord, dan valt het woord weg (icoon en telling blijven, de volledige naam staat in de toegankelijke tekst; dezelfde terugval die het ontwerp al had). Opnieuw opgenomen: alleen `Light360ScreenShots_bibliotheekDetail.png` en `Dark360ScreenShots_bibliotheekDetail.png` (`git diff --stat` op de beeldenmap: precies die twee). Bekeken: "Bibliotheek" heel; in de eigen meting "Library" heel, ook met grote letter. |
| R-ONTWERP-2 | belangrijk | Met grote letter (fontScale 1,3) plakken sleutel en waarde in `KeyValue` aan elkaar: "In MediaGuardoff" op Instellingen, en "Latest version" staat nagenoeg tegen "1.2.3" (eigen meting, Engels, 360 dp; de sleutel vult de vaste kolom van 120 dp en er zat geen tussenruimte). Taalonafhankelijk (de sleutel "In MediaGuard" is in beide talen gelijk); geen referentiebeeld toont dat deel. Tekstvergroting mag de indeling niet breken (WCAG 1.4.4, Peil §12). | `ui/Components.kt`, `KeyValue`: de waarde begint op 120 dp of, als de sleutel breder is dan 112 dp, op de breedte van de sleutel plus `MgSpace.s2`. Past de sleutel ruim, dan staat alles op dezelfde plek: na `recordPaparazziDebug` was geen enkel referentiebeeld door deze wijziging veranderd. Bekeken in de eigen meting: "In MediaGuard  off" met tussenruimte. |

**Vaste referentiebeelden (A-HERSTEL S-P2-620, R-EERLIJK-2, commit `0025ec1`).** De meting hierboven stond buiten de
repo. Nu staan twee beelden in de referentieset: `EnGrootLight360HoogScreenShots_instellingenWaarschuwingen.png` en
`EnGrootLight360HoogScreenShots_bibliotheekDetail.png` (grote letter 1,3, 360 dp, en-US, licht, volle lengte op 2 px
per dp). Bekeken: "In MediaGuard  off" en "Latest version  1.2.3" met tussenruimte, "Library" heel. Alle bestaande
beelden bleven byte-gelijk. Proef met de oude code (vóór `0679551`): met de oude `KeyValue` toont het beeld
"In MediaGuardoff" (2 090 van 3 769 920 pixels anders, 0,06 %), met de oude `TopBar` "Libra/ry" (17 579 pixels, 0,47 %);
in beide gevallen bleef `:app:verifyPaparazziDebug` groen, want het verschil viel onder de toenmalige tolerantie van 0,5 % (sinds KLEIN-18 0,01 %)
(R-TECH-3). Het herstel van R-ONTWERP-1 (Engels, grote letter) en R-ONTWERP-2 was toen dus **alleen bekeken in het
beeld**. Sinds `63b4910` (KLEIN-18, tolerantie 0,01 %) bewaakt verify deze twee fouten wel. Proef KLEIN-19 (12 november
2026, in een kopie buiten de repo: `63b4910` met de oude code van vóór `0679551`): met de oude `KeyValue` werd verify
rood op `EnGrootLight360HoogScreenShots_instellingenWaarschuwingen` (Paparazzi: verschil 0,035186 %); met de oude `TopBar`
rood op `EnGrootLight360HoogScreenShots_bibliotheekDetail` (0,162623 %) en ook op `Light360`/`Dark360ScreenShots_bibliotheekDetail`
(0,520891 % en 0,545607 %). Paparazzi meet het verschil anders dan de pixeltelling hierboven.

### 12.4 Open (klein)

1. **"Exemption"** (knop bij "Battery restriction is on", Instellingen) is een zelfstandig naamwoord als knoptekst;
   Peil §11 vraagt een werkwoord ("Allow", "Exempt MediaGuard"). Wijzigen vraagt het referentiebeeld
   `EnLightScreenShots_instellingenWaarschuwingen.png` opnieuw op te nemen en een woordkeuze (ADR-0023 punt 7); niet
   in deze stap.
2. **"Partially available"** loopt op 360 dp in een kaart van "Discover" om op twee regels binnen het label (geen
   afkapping, wel een hoger label dan de buren). Bijgewerkt (KLEIN-20): het label heet nu "Partial" (punt 3) en er is
   een Engels referentiebeeld van Aanvragen op 360 dp, licht:
   `EnGrootLight360HoogScreenShots_aanvragen.png` (in de bestaande Engelse 360-klasse, dus ook met grote letter,
   fontScale 1,3, en op volle lengte; dezelfde voorbeeldgegevens als de andere Aanvragen-beelden). Daarin staat
   "Partial" op één regel naast zijn icoon, even hoog als "Available" ernaast, en is niets afgekapt; verify bewaakt dat.
3. Bijgewerkt (KLEIN-16): de knop heet nu "Exempt" en het label "Partial"; sinds KLEIN-20 staat Aanvragen in het
   Engels ook in een referentiebeeld (punt 2).
   Ook het Nederlands is bijgewerkt (KLEIN-17): de knop heet nu "Uitzonderen" (werkwoord) in plaats van "Uitzondering".

De bronscan van `TaalTest` (R-TECH-1) is verbreed in `7e714ff` (KLEIN-14); de grenzen staan in de KDoc van
`TaalTest`. Opgelost: de Paparazzi-tolerantie van 0,5 % die een gewijzigde titel doorliet (R-TECH-3); sinds
`63b4910` (KLEIN-18) 0,01 %, en verify vangt beide fouten van §12.3 nu (proef KLEIN-19).

### 12.5 NIET GEVALIDEERD

Een echte telefoon of emulator met Engelse systeemtaal of taal per app (Android 13+), TalkBack in het Engels en fel
licht: niet gedaan (geen emulator in de bouwomgeving, V-021). Alles hierboven is Sim-getest.

### 12.6 Opnieuw uitvoeren

```bash
# vanuit <WERKMAP>/media-guard-ultimate/apps/android; Gradle onder de lock, TMPDIR op schijf
python3 tools/peil_tokens.py --check
ANDROID_HOME=$HOME/android-sdk JAVA_HOME=/usr/lib/jvm/java-17-openjdk-amd64 \
  flock <WERKMAP>/.lokaal/gradle.lock nice -n 10 ./gradlew --no-daemon --max-workers=4 \
  -Dorg.gradle.jvmargs=-Xmx2g :app:verifyPaparazziDebug :app:assembleDebug
```

De eigen meting op volle lengte: in een kopie buiten de repo extra klassen in `ScreenShots.kt` zoals
`class EnMeetLight360Hoog : ScreenShots(dark = false, D360.copy(screenHeight = 5236), docs = true, taal = AppTaal.En)`
en daarna `PEIL_AFDRUKKEN=1 ./gradlew … :app:recordPaparazziDebug --tests 'nl.mediaguard.app.shots.EnMeet*'`.
Nooit `recordPaparazziDebug` in de repo zonder ontwerpstap (P-PEIL).

## 13. R-ONTWERP S-P3-600: API-sleutels met rechten

**Datum:** 17 november 2026. **Niveau:** N3. **Beginstand:** `d15d87fae9da8773e92db70c04b67b9b3039c335`,
branch `ccr-c133d7a0-kuz1cv`. Vervolg van de eerste review: precies de vier gemelde conceptbestanden waren aanwezig.
Hun diff is beoordeeld en behouden. **Schermreview en lokale webpoort: groen.** De scanbeslissing en de
herhaalde upgradeproef zijn hieronder vastgelegd. Details staan in de ketenrapporten `RAPPORT-R-ONTWERP-2.md`
en `RAPPORT-R-ONTWERP-3.md`.

### 13.1 Oorzaak van de eerdere focusmeting (R-ONTWERP-2)

De eerste review stopte na drie rode focusproeven; haar `review-na` bevatte 88 JSON-metingen en 112 beelden uit
verschillende ontwikkelruns. De fout was een onmiddellijke lezing van `outlineColor` op “Sleutel aanmaken”: wit op
wit, 1:1. Eerst zijn de volgende bronnen en de echte browser vergeleken, vóór het herstel:

- `styles/peil/ontwerptaal.css`, `:focus-visible` en `focus-ring`, en `styles/peil/mediaguard-mso.css`: buitenring
  3 px in `--ring`, offset/halo 2 px in `--ring-halo`. Licht blauw/wit, donker lichtblauw/donker. Geen bronkopie gewijzigd.
- `components/ui/button.tsx`, `buttonVariants`: `transition-colors`. De gebouwde CSS rekent daar óók
  `outline-color` toe, met 150 ms. De test las de beginwaarde van die transitie vóór het tekenen; ook de
  gereduceerde 0,01 ms is niet hetzelfde als “al getekend”. Een rolwissel kan om dezelfde reden tijdelijk
  de oude spoorkleur naast de nieuwe knopkleur opleveren.
- `e2e/peil.spec.ts`, “peil toetsenbord”: meet ringdikte ≥ 3 px en bedekking, geen kleurcontrast. Daardoor vindt die
  bestaande test deze meetfout niet.
- De ongewijzigde `/login` met de echte productiebuild vertoont dezelfde transitie: de lichte outline gaat van
  wit/tussenkleur naar `rgb(18, 61, 163)`; donker van `rgb(6, 16, 31)` naar `rgb(141, 182, 255)`.
  `git diff bd198e3 d15d87f -- apps/web/components/ui/button.tsx apps/web/styles/peil/ontwerptaal.css
  apps/web/styles/peil/mediaguard-mso.css` is leeg. Dit bestond vóór deze stap.

Herstel uitsluitend in `e2e/apikeys-ontwerpreview.spec.ts`: stijlberekening afdwingen en wachten op de werkelijk
lopende, eindige animaties. Een mediawissel mag een oude transitie annuleren; ook vervangende transities worden
afgewacht. Geen vaste slaaptijd, geen herkansing van een contrastassertie, geen skip en geen lagere grens.
De test meet de buitenring tegen het omringende vlak, de halo tegen de knop en beide ringlagen tegen elkaar.
De volledige 3 px buitenring moet zelfstandig ≥ 3:1 halen; een contrasterende halo vervangt die eis niet.
Alfatransparantie van de systeemfocuskleur wordt meegerekend.

Tegenproef op licht/320: een opzettelijk witte ring én halo op wit wordt nog steeds rood met
`outerContrast=1`, `ringToHalo=1`, “Expected: >= 3”. De mutatie is verwijderd. De oorspronkelijke melding is
hiermee opgelost als meetfout; een blijvend onzichtbare focusindicator wordt aantoonbaar afgekeurd.

### 13.2 Opstelling, matrix en beelden

Echte web-app (`node server.mjs`, productiebuild), echte kern, eigen verse e2e-database en
`MG_START_BACKGROUND=0`, gestart door `playwright.config.ts`. Chromium binnen de zandbak; alleen testgegevens.
Geen echte NAS, Emby of accounts. Toetsingsbasis: `API_SLEUTEL_SCOPES_ONTWERP.md` §8, Peil 1.1.1 en
`COMPONENTEN.md` §1/§29. Vergeleken met `ai-server-manager:design/ontwerptaal/prototypes/instellingen-toegang.html`
en `shots/instellingen-toegang-licht-390.png`.

Alle negen meetpunten zijn uitgevoerd op **320, 360, 390, 768, 1024 en 1440 px**, licht en donker:

| Meetpunt uit het ontwerp | Werkelijk gemeten uitkomst op alle 12 combinaties |
|---|---|
| 1. Operator | Geen rechtenveld in de DOM; bestaande MSO-aanmaakweg groen |
| 2. Kijker | Schakelaar uit, juiste naam, groepnaam en beschrijving met “nooit opheffen” |
| 3. Wisselen van rol | Aan → Operator → Kijker wist het recht; Operator-body bevat geen `scopes` |
| 4. Kijker met recht | Lijst toont Kijker, Noodstop activeren en “nooit opheffen” |
| 5. Gewone Kijker | Geen rechtenlabel |
| 6. Serverfouten | Echte 422 voor Operator plus scopes en onbekende `wanted:search`; letterlijke Nederlandse tekst, invoer behouden; alleen uitgaande testbody aangepast |
| 7. Intrekken | Feit Rechten (scopes) met het juiste label |
| 8. Toegankelijkheid | Axe 0, doelen ≥ 44 px, tekst ≥ 14 px, geen paginaoverloop of gemeten tekstafkapping; volledige Tab-doorloop, Spatie/Enter, Esc en focus terug groen |
| 9. Geforceerde kleuren | Spoor én schakelknop zichtbaar; aan/uit-positie verschilt; onbekend-label houdt stippelrand |
| Logboek, Nederlands én Engels | Echte herhaalde noodstop; volledige actietekst zonder afkapping in beide talen op alle zes breedtes en beide thema’s; axe 0 |
| DejaVu Sans en vergrote tekstafstand | Op 320 px in beide thema’s geen horizontale overloop, afkapping of kleinere doelen |

Eén uniforme groene eindrun: **25 tests** (12 nieuwe reviewtests + 13 bestaande sleuteltests), **0 overgeslagen**,
136 JSON-metingen, 108 focusmetingen en 148 PNG’s. Bewijs lokaal in de ketenmap onder `review2-definitief`;
alle 148 beelden zijn zelf bekeken via de 28 beeldbladen in `review2-definitief-bladen`. De eenmalige sleutel
is niet gefotografeerd. Beoordeeld: leesbare omlopende uitleg en foutteksten, onderscheid tussen aan/uit,
zichtbare focuslagen, neutrale rechtenlabels, onbekend met tekst/icoon/stippel en volledige logboektekst.

| Contrast, laagste gemeten verhouding | Licht | Donker |
|---|---:|---:|
| Tekst in het rechtenveld | 8,81:1 | 9,32:1 |
| Spoorrand, schakelaar uit | 5,89:1 | 5,67:1 |
| Knop tegen spoor, schakelaar uit | 19,22:1 | 16,13:1 |
| Spoorrand, schakelaar aan | 9,45:1 | 8,01:1 |
| Knop tegen spoor, schakelaar aan | 9,45:1 | 9,32:1 |
| Spoor/knop in geforceerde kleuren, beide standen | 21:1 | 21:1 |

Over alle gemeten teksten (inclusief uitgeschakelde bediening): minimum 4,73:1. Buitenringminimum 7,55:1,
ring/halo minimum 9,45:1; alle 136 geometriecontroles hebben paginaoverloop 0 en axe 0 binnen hun vermelde scope.

In geforceerde kleuren heeft axe de scope **schakelaar zelf**. Op alle zichtbare dialoogtekst blijft daarnaast
onverkort de numerieke DOM-eis ≥ 4,5:1 staan (gemeten 21:1). De volledige dialoog krijgt axe in beide gewone
thema’s. Reden: een aanvullende proef van axe op de hele geforceerde donkere dialoog rapporteerde onterecht
1,1:1 voor de aanmaakknop. `axe-core/axe.js`, `getTextColor`, gebruikt `-webkit-text-fill-color` uit de auteursstijl
(`#06101f`), terwijl Chromium zichtbaar witte systeemtekst op zwart schildert (`color` wit, achtergrond zwart).
Dat is apart op de ongewijzigde inlogknop vastgesteld en afgebeeld. Geen axe-regel is uitgeschakeld.

### 13.3 Beoordeeld conceptherstel

- **R-ONTWERP-1 hersteld:** `components/mg/primitives.tsx`, `Switch`: uitsluitend in geforceerde kleuren krijgt
  de schakelknop een rand in systeemkleur `CanvasText`. Het gedrag en de positie blijven gelijk. Zonder die rand
  vallen knop en spoor samen. De tegenproef zonder rand wordt rood op knopcontrast 1:1; daarna is de mutatie verwijderd.
- **R-ONTWERP-3 hersteld:** `lib/labels.ts`, `EVENTS`: eigen Nederlandse en Engelse vertaling voor
  `system.emergency_stop_repeated`, exact “Noodstop stond al aan: opnieuw gevraagd, niets gewijzigd” en
  “Emergency stop already active: requested again, nothing changed”. Alle 24 taal/thema/breedte-combinaties groen.
- Twee typefouten uit de achtergelaten tekstafstandproef hersteld: stijlelementen via `parentNode.removeChild`
  verwijderen, passend bij het door Playwright geleverde `Node`-type.

### 13.4 Poorten, grenzen en overdracht

Lokaal groen: `npm run check:peil`, `npm run lint`, `npm run typecheck`, `npm run test:unit` (138 tests),
`nice -n 10 npm run build` en de gezamenlijke e2e van `werk-apikeys.spec.ts` en `apikeys-ontwerpreview.spec.ts`
(25 tests). Kopieëncontrole lokaal/online, `actionlint`, OpenAPI/EVIDENCE (41 tests) en `git diff --check` groen.
De kandidaatbuild en upgradepoort zijn herhaald onder de machinebrede `docker-build.lock`, met de vorige
release op digest, een eigen image en poort. Het verslag meldt bij upgrade én terugzetten **“niets verloren”**;
`api_keys` 3 → 3, geen verloren of gewijzigde rij. De eigen image is opgeruimd.

De regisseur heeft de vaste openbare e2e-testaanmelding toegestaan als testfixture, met een gerichte
`gitleaks:allow`-markering en reden op uitsluitend de betrokken regel. De staged geheimenscan meldt geen
bevindingen. De eerder achtergebleven diagnosediensten zijn door de regisseur gestopt; de poorten zijn vrij.

Vervolgpunt buiten deze stap: `components/ui/button.tsx`, `buttonVariants`, animeert de focuskleur via
`transition-colors`, terwijl ONTWERPTAAL §6 zegt dat focus nooit geanimeerd wordt. Dit bestond al op de basiscommit,
ook op `/login`. Een afzonderlijke ronde kan `outline-color` uit de knoptransitie halen en onmiddellijk zichtbare
focus op ongewijzigde schermen meten; hier is de gedeelde knopstijl niet aangepast.

De gevraagde skills `design:design-critique` en `design:accessibility-review` zijn niet beschikbaar in de
skillcatalogus of gereedschappen; geen uitvoering geclaimd. De beoordeling is zelf gedaan met Playwright,
axe-core, DOM-metingen en beelden. Echte schermlezer, echte Windows-hoogcontrastomgeving en echte NAS/Hub-sleutel:
**NIET GEVALIDEERD**. Geen wijziging in kern, rechtencontrole, API, gegevensgedrag, contract of Peil-bronkopie.

### 13.5 Herstel van testisolatie na de rode CI (18 november 2026)

De gerichte run met 25 tests uit §13.4 dekte de volledige bestandsvolgorde niet. CI-run **44193895258** op
`4d93e5c` eindigde met **2 failed, 2 flaky, 241 passed**. De nieuwe `apikeys-ontwerpreview.spec.ts` draaide vóór
`mediaguard.spec.ts`: haar `beforeAll` maakte de beheerder al aan. De eerste-installatietest kreeg daardoor
`/login` in plaats van `/setup`. De achtergelaten donkere servervoorkeur verstoorde bovendien de bestaande
thematest bij de wissel van systeem-donker naar systeem-licht.

De bestaande opzet heeft één worker, één Chromium-project, `fullyParallel: false`, geen globale setup en één
verse gedeelde e2e-datamap per run. Bestanden die een beheerder nodig hebben komen alfabetisch na `mediaguard.spec.ts` (bijvoorbeeld `werk-*.spec.ts`);
voor een gerichte uitvoering gebruiken ze een `needs_setup`-controle. De reviewtest volgt nu die afspraak als
`e2e/werk-apikeys-ontwerpreview.spec.ts`; configuratie, eerste-installatietest en thematest blijven ongewijzigd.
Na ieder geval verlaat de browser de app, wist hij zijn tijdelijke opslagthema en herstelt hij de oorspronkelijke
servervoorkeur en taal. Eigen sleutels worden via de API ingetrokken en vervolgens uit de eigen testfixture
opgeruimd; de aangezette noodstop gaat weer uit. De nacontrole meet de achtergelaten toestand.

De twee wisselvallige gevallen waren in basisrun **44182794249** op `d15d87f` beide zonder herkansing groen
(de volledige run: **233 passed**, geen flaky). Ze zijn daarom ook hersteld binnen de e2e-code:

- `peil.spec.ts`, “peil dialogen en menu's zonder axe-fouten · dark”: de contrastmeting wacht nu op werkelijk
  lopende eindige kleurtransities, inclusief vervangende transities. Geen vaste wachttijd, lagere contrastgrens
  of uitgezette axe-regel. De regressietest wacht op een vertraagde transitie en keurt blijvend slecht contrast af.
- `werk-mediaserver.spec.ts`, “ondertitels: wat ontbreekt …”: `afterEach` wacht via
  `unrouteAll({ behavior: "wait" })` op lopende `route.fetch`/`fulfill`-afhandeling voordat de requestcontext sluit.
  De regressietest houdt een echt lokaal antwoord onderweg en controleert dat opruimen daarop wacht.

Zeven afzonderlijke tegenproeven zijn uitgevoerd: oude bestandsnaam, weggelaten server- of opslagthemaherstel,
sleutelintrekking of noodstopherstel, wachten op kleurtransities en wachten op route-afhandeling. Iedere
opzettelijke mutatie maakte haar controle rood (exitcode 1) op de bedoelde oorzaak; alle mutaties zijn teruggezet.
Geen appcomponent, kernbestand, rechtencontrole of kwaliteitsnorm is gewijzigd.

De volledige lokale web-e2e eindigde met **248 passed (12.9m)**, **0 flaky**, **0 overgeslagen**, zonder
herkansingen. De bestaande eerste-installatietest en thematest, beide eerder wisselvallige gevallen en de drie nieuwe
regressiecontroles zijn daarin groen. Ook `check:peil`, lint, typecheck, alle 138 unittests, de productiebuild,
de lokale/online kopieëncontrole, `actionlint` en de 41 EVIDENCE/OpenAPI-tests zijn groen. De eerste gerichte
ontwikkelrun ontdekte een onjuist statuspad in de nieuwe nacontrole; dat is vóór deze volledige run gecorrigeerd.
De eindcommit, de geheimenscan en de CI-uitkomst op exact die commit worden na push vastgelegd in
`RAPPORT-A-HERSTEL-4.md` van de keten S-P3-600; de eerdere rode run is geen afrondingsbewijs.

De bestaande bewijsstatussen blijven ongewijzigd: **Bezig**, **NIET GEVALIDEERD (echte NAS)** voor het gebouwde
deel scopes; alleen NAS-test nodig voor dat deel. De nog ontbrekende Hub-acceptatie wordt hiermee niet afgevinkt.
Geen echte NAS, Emby of accounts benaderd; geen losgekoppelde diagnosedienst of container gestart.

## 14. S-P3-680 — ontwerp voor Overzicht, toetsenbord en opslagkleuren

### 14.1 Afwijkingen en afbakening

De bestaande 768-pixelmatrix in `peil.spec.ts` meet de hoofdroutes. De toetsenbordproef in
`mediaguard.spec.ts` stopt bij sneltoetsen en de bestaande Peil-focusproef meet Instellingen.
Op het Overzicht toetsen we ook de zichtbare bediening zelf, inclusief de doorklik vanuit
Systeemstatus, en lopen we de vijf hoofdonderdelen door op 768 px.

- Het statusblok toont één totaal, zonder herleidbare rijen. Het kan geruststellende tekst tonen
  terwijl een bron nog laadt, geen resultaat gaf of een oude meting teruggaf.
- Beschikbaar, Ontbrekend, Fouten en Nu bezig verversen niet allemaal zelfstandig. De kaart
  Ontbrekend kan een bibliotheekteller gebruiken en die aan Ontbrekend toeschrijven.
- De stapelbalk en detailbalken van het opslagoverzicht gebruiken verschillende kleurregels voor
  dezelfde opslagpost. Het teken bij de hoeveelheid duidt de richting aan.

### 14.2 Ontwerp binnen Peil

- **Systeemstatus (C-04).** H2, totale StatusBadge en een lijst met één link per rij. De kern leest
  `/status`, de AV1-engine `/encoder/health` en de gekoppelde instanties hun eigen gezondheidsmeting
  uit `/instances`. Toon wat is gemeten en wanneer. Een laatst bekende gezonde of gepauzeerde
  meting wordt Verouderd met tijd en reden. Een bekende storing blijft herkenbaar als oude stand.
  Zonder meting volgt Onbekend. Uitval van één bron verandert de meting in andere rijen niet.
  De telling voor Wat actie nodig heeft behoudt de bestaande bron en doorklik.
- **Periodiek verversen.** Gebruik de bestaande browserbronnen met een interval per onderdeel.
  Houd beschikbaarheid, ontbrekend, fouten, activiteit, kalender en opslag bij hun eigen bron.
  Laat ontbrekende waarden weg en markeer laatst bekende waarden. Toon een voortgangspercentage
  alleen als de beschikbare taakmeting het percentage zelf bevat en actueel is. Benoem de
  geregistreerde noodstop zonder een uitspraak over alle werkzaamheden of gebruikersgegevens.
  De kop- en zijbalk spreken over gemelde activiteit en fouten uit hun eigen meting.
  Kernteksten bij noodstop, opslagwaarschuwing, migratie en het ontkoppelen of verwijderen
  van een mediaserver beschrijven de actie of de gerapporteerde stand. De wachtrijtelling
  bij opheffen van de noodstop bewijst alleen hoeveel werk er wacht.
- **Opslag.** Eén vaste Peil-grafiekkleur per post in de stapel, legenda en detailbalk. Het teken en
  de tekst duiden richting aan. Onverklaard behoudt zijn arcering. Beschikbare gestructureerde
  gegevens voeden de Nederlandse en Engelse kerntekst.
- **Uitgesteld.** De E13-woordkeuze voor indexers die niets vinden en de Android-ronde blijven
  open. De stap bouwt met de bestaande Peil-versie en met bestaande leesroutes en gegevens.

### 14.3 Browserbewijs

De lokale Chromium-proef draaide op een productiebuild met een eigen testkern. Op de oude
webbuild faalden de drie nieuwe bronproeven. De toetsenbord- en breedteproeven waren al
groen. Op de gewijzigde webbuild waren 14 gerichte tests groen in 39.3s. De proef
controleert bij een cacheantwoord zowel de gestippelde rand als het verschil met de kleur
van de gemeten gezonde stand. De toets bedient ook de herhaalactie en bezoekt de vijf
onderdelen met Tab en Enter. Te zien in
[`docs/screenshots/peil/s-p3-680`](../screenshots/peil/s-p3-680/).

De opslagafdruk gebruikt een Engelse browsersessie en synthetische opslagposten. De
kerntekst en legendanamen zijn vertaald. Aanvullende brontekst uit het opslaglogboek
volgt de taal waarin de kern die aanlevert. Geen echte schermlezer, NAS of tv gebruikt.

De eerste volledige lokale run telde 252 groene en vijf rode tests. De browserproef had
een lokale themakeuze opgeslagen die de kern overnam als gebruikersvoorkeur. De proef
gebruikt nu de systeemkleur van Chromium. De ontkoppeldialoog behoudt de getoetste
mededeling dat adres en sleutel bewaard blijven. De herhaling bevestigde het themaherstel
en wees op de tweede getoetste mededeling. De toenmalige verwijderdialoog deed ook een uitspraak over andere
gekoppelde mediaservers. De gecorrigeerde tekst staat in §14.4. In beide proeven kwamen de drie daaropvolgende fouten
door de tweede testserver die de mislukte dialoogproef had achtergelaten. De bestaande
thema- en mediaservercontroles zijn behouden.

### 14.4 Inventaris van de metingen op Overzicht en in de appschil

De meettijd hoort bij het leesverzoek dat het element voedt. Een actuele databaselezing zonder eigen tijdveld gebruikt de HTTP-datering van dat antwoord. Ontbreekt die bij een rechtstreeks, ongecachet antwoord, dan geldt het tijdstip van die lezing. Een verplicht tijdveld zoals `updated_at` van een encodetaak kan alleen door de bron worden ingevuld. `Age` en `X-MediaGuard-Stale` blijven behouden. Na twee verwachte meetintervallen vermeldt de weergave de laatst bekende tijd. Tijdens uitval blijft een bekende waarde alleen zichtbaar met die aanduiding. Bij een geweigerde lezing vervallen de gegevens uit de cache.

| Element en afgeleide gegevens | Bron en meettijd | Vers | Oud bij HTTP 200 | Uitval | Weigering |
| --- | --- | --- | --- | --- | --- |
| Paginakop, versie in de zijbalk, kernrij | `/status.time` | Versie en kernantwoord | Laatst bekend met tijd | Laatst bekend of onbekend | Onbekend |
| Noodstopbalk, profielknop, dialoog, stopregel in statuspaneel | `/status.time` en `emergency_stop.active`. `since` dateert de handeling | Actieve stand, reden en bedienbare opheffing bij het juiste recht | Laatst bekend, geen actieve selectie of opheffing | Idem, zonder meting onbekend | Onbekend, geen selectie of opheffing |
| Slotbalk, vrijgaveknop en bevestiging | `/recovery/readiness`, datum van de lezing van `library_locked` | Vergrendeld of balk weg. Vrijgeven vraagt een beheerder en een nieuwe lezing | Laatst bekend, vrijgave uit | Laatst bekend of onbekend, vrijgave uit | Onbekend, vrijgave uit |
| Gezondheid in zijbalk, kopbalk, Fouten, controles, download- en zoektelling | `/health/summary.generated_at` | Gemelde fouten, actieve aantallen en detailtekst. Nul activiteit vereist een volledige meting | Laatst bekend bij de teller, geen actuele activiteit | Laatst bekend of onbekend | Onbekend |
| Encoderstatus, opsplitsing en totaalbadge, knop Verversen | `/encoder/health`, kernrij, controles en instantierijen, ieder met eigen meettijd | Zwaarste gemeten storing. Een aparte rij vertegenwoordigt alleen haar eigen fout | Oude rijen tellen als verouderd. Een actuele fout blijft meetellen | Onbekende rij en herhaalactie | Onbekende rij en herhaalactie |
| Instantienaam, status, pauze, lege lijst | `/instances`, `last_check_at` per controle. Voor configuratie en leegte geldt de lijstlezing. Interval uit `/settings/effective` | Eigen controle, gepauzeerd of niet gekoppeld | Laatst bekende status en tijd | Laatst bekend of onbekend | Onbekend |
| Beschikbaar, percentage, aantal gemonitord | `/library/kpis.generated_at` | Waarde en herleidbare meettijd | Getal en eigen oude meettijd | Laatst bekend of onbekend | Onbekend |
| Ontbrekend, afgelopen week, oorzaken en komende afleveringen | `/wanted/kpis`, bij oudere kernen ook `/wanted/reasons` voor de oorzaken | Telling uit de eigen selectie | Laatst bekend | Laatst bekend of onbekend | Onbekend voor de geweigerde bron |
| AV1-besparing, weekwaarde, wachtend aantal en grafiek | `/encoder/statistieken`, met cache-ouderdom van de encoderproxy | Waarde en grafiek uit de meting | Laatst bekend, grafiek weg | Laatst bekend of onbekend | Onbekend |
| Encodetitel, percentage, voortgangsbalk, proefstatus, wachtrij en extra regels | `/encoding/jobs`, `updated_at` per taak en de lijstlezing. Noodstop uit `/status.time` | Alleen een gemeten percentage. Een kernstop zonder taakbevestiging heet onbevestigd | Voortgang onbekend met meettijd. Wachtrijtelling gemarkeerd zodra een bijdragende rij oud is | Idem, zonder meting onbekend | Geen afgeschermde taak of balk |
| Draaiende kerntaken, lege activiteit | `/jobs?status=running`, datum lijstlezing. Lege activiteit vereist ook verse encodes en volledige activiteitsmeting | Bezig of bevestigd leeg | Laatst bekend, geen actuele leegmelding | Idem of onbekend | Geen taken of leegclaim uit de geweigerde bron |
| Actiekaart en actierijen, paneelteller, kopbalk, zijbalk, activiteitenbadge en migratielink | `/migration/runs`, `/provisioning/status`, `/seerr/settings`, `/attention`, `/jobs?status=failed`, `/health/summary`, `/wanted/kpis` en zo nodig `/wanted/reasons`. Elke lijst heeft haar eigen meettijd, ook een lege | Exact aantal, doorklik, migratielink bij een open migratie | Minstens het aantal uit verse bronnen, met naam en ouderdom van de ontbrekende bron | Idem, eventueel onbekende tijd | Alleen de bevestigde ondergrens, ontbrekende bron genoemd |
| Opslagregel en aantallen | `/storage/ledger.end`, einde van de berekende periode | Wijziging per periode | Laatst bekend | Laatst bekend of onbekend | Onbekend |
| Kalenderregel, aflevering, datum, statusbadge en lege week | `/calendar`, datum van de geselecteerde lijst. De uitzenddatum hoort bij de aflevering | Titel en status of lege week | Laatst bekend | Laatst bekend of onbekend | Geen afgeschermde kalenderregel |
| Meldingenbel, ongelezen, wachtend, badge en alles gelezen markeren | `/notifications/count`, eigen lezing. Op een oudere kern gebruikt de terugval `/status` en de actielijst | Teller en toegankelijke naam. Markeren bij een bevestigd ongelezen aantal | Laatst bekend, getal en markeeractie vervallen | Idem of onbekend | Onbekend |
| Meldingenlijst, herhalingen, ernst, leestijd, meer laden en uitgeklapte tekst | `/notifications`, datum van de lijstlezing. Voor uitklappen geldt de eigen lezing van `/notifications/{id}`. `ts` dateert de gebeurtenis | Gemeten lijst of lege toestand | Laatst bekend bij de lijst | Idem of onbekend | Geen afgeschermde meldingen |
| Hub-link en zoekresultaten voor media en instellingen | `/settings/effective?keys=system.hub_url`, `/library` en `/settings/search`, ieder met eigen antwoordtijd | Geldige link, gevonden titels en instellingen | Link of zoekopties van de oude bron vervallen | Idem, met de bronstand bij de zoekresultaten | Geen resultaten of koppeling uit de geweigerde bron |
| Navigatie, menu's, sneltoetsen, taal, thema, technische details en liveverbinding | Actuele route, geopende venster, toegepaste lokale voorkeur, sessierecht of gebeurtenis van de liveverbinding | `aria-current`, `aria-expanded`, `aria-pressed` en `aria-checked` volgen de werkelijk getoonde toestand | De lokale toestand blijft afleesbaar | Een verbroken liveverbinding krijgt haar eigen melding | Een sessie zonder het vereiste recht krijgt geen vrijgaveactie |

De uitvoerbare inventaris staat in `apps/web/e2e/werk-bronversheid.spec.ts`. De browser doorloopt voor iedere opgenomen bron en elk bijbehorend element vers, oud met HTTP 200, een afgebroken verbinding, HTTP 403 en herstel. Voor bronnen met een opgeslagen meettijd blijft de HTTP-datering vers terwijl uitsluitend het tijdveld in de gegevens veroudert. De aanvullende veiligheidsproef verandert de bron ná het openen van de dialoog en telt de schrijfverzoeken. De herhaalproef controleert de melding, Enter en horizontale overloop tijdens de storing op zes breedtes in beide thema’s.

Bij meerdere ontbrekende actiebronnen toont de kopbalk de bevestigde ondergrens, het aantal ontbrekende bronnen en hun oudste meettijd. Ontbreekt een meettijd, dan verschijnt ‘Onbekend’. De toegankelijke beschrijving van de teller en de actiekaart noemen elke bron afzonderlijk. De meldingenbel toont haar eigen meettijd bij de knop. De breedteproef controleert ook de kophoogte en de bediening van de bel wanneer deze gegevens tegelijk verouderen of worden geweigerd.

Op de uitgangsversie faalden zes van de zeven browserproeven van de reviewer. De breedteproef slaagde daar al. De herstelbuild doorstond alle 48 bron- en veiligheidsproeven, waaronder 35 tabelgevallen. De kopbalkproef vond op de vorige build 87 pixels horizontale overloop bij gelijktijdig verouderde metingen. Op de herstelbuild pasten kop en bel op de zes getoetste breedtes. De vergelijking gebruikt dezelfde zeven praktijksituaties op beide productiebuilds.

De verwijderdialoog beschrijft welke koppeling, sleutel en maptoewijzingen de gebruiker verwijdert. De proef met één gevolgde server verifieert ook het aantal na de verwijdering.
