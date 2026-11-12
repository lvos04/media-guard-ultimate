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
