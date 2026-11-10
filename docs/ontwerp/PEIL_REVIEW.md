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
