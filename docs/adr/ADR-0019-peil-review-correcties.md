# ADR-0019 — Peil-review (stap 5): correcties in de web-UI en wat bewust blijft

- **Status:** geaccepteerd
- **Datum:** 2026-11-09
- **Fase:** ontwerpronde Peil 1.1.0, stap 5 (onafhankelijke ontwerpreview van de omgezette app)
- **Context:** [`docs/ontwerp/PEIL_REVIEW.md`](../ontwerp/PEIL_REVIEW.md) (bevindingen, metingen, schermafdrukken),
  [ADR-0016](ADR-0016-peil-omzetting-interpretaties.md), [ADR-0017](ADR-0017-peil-web-bouwkeuzes.md),
  [ADR-0018](ADR-0018-peil-android-bouwkeuzes.md), Peil 1.1.0 in [`design/ontwerptaal/`](../../design/ontwerptaal/HERKOMST.md).
  Vaste regels van deze ronde: alleen uiterlijk, teksten en toegankelijkheid; functies, API-routes, foutformaat,
  datamodel, poorten en installatie veranderen niet (OpenAPI byte-gelijk); `data-testid`'s en rollen blijven; geen
  versie-ophoging.

## Besluiten

1. **Tekstlinks zijn weer als link te herkennen.** `mg.css` zet elke `<a>` op `color: inherit` zonder streep (laag
   `legacy`, wint van Peils basislaag). Daardoor waren links in lopende tekst ("Bekijken" achter de opslagregel, "Wat
   kwam erbij?", "TrueNAS koppelen") niet als link te zien (WCAG 1.4.1) en hadden vervolglinks (`.link-more`,
   `.link-arrow`, `.back-link`, "en nog n — bekijk alles") geen streep. Correctie (n) in `styles/peil-mg.css`: `--link`
   en onderstreping voor tekstlinks; componentlinks (kaart, rij, knop, tab, navigatie, chip) houden hun vorm.
2. **44 px ook voor losse links, categoriechips, vaktermen en selectievakjes in tabellen** (Peil §6). Vaktermen
   (`Term`) krijgen een raakvlak van 44 px via opvulling met negatieve marge (de regelhoogte verandert niet);
   selectievakjes in tabelcellen krijgen een omhullend `<label class="check-hit">` (zelfde `aria-label`, zelfde
   gedrag). Alleen links/termen in een zin vallen onder de uitzondering van WCAG 2.5.8.
3. **Tabellen en de kalenderweek reageren op de breedte van de inhoud, niet van het scherm.** Sinds de zijbalk pas
   vanaf 1024 px verschijnt, is de inhoud tussen 1024 en ± 1280 px te smal voor de brede MediaGuard-tabellen; de oude
   regel (scherm ≤ 860 px) kneep kolommen tot één letter breed (gemeten op bibliotheek, ontbrekend, terugzetten,
   gebruikers en de kalender). Gekozen: CSS-containerquery's (`:has(> .table--stack)` en `:has(> .week)` als
   container) met een drempel van **60 rem inhoud**; daaronder kaartenlijst (tabel) of omlopende dagen (kalender). De
   koppelingsmatrix (13 kolommen) heeft een eigen drempel van **90 rem**: daaronder per instantie een kaart met per
   sectie woord + teken (eerder werden kolomkoppen ook op 1440 px afgekapt).
   Selectievakjes blijven in die tussenvorm zichtbaar (alleen de telefoon verbergt ze, zoals voorheen). Alternatief
   "tabel horizontaal laten scrollen" verworpen: Peil wil onder ± 768 px een kaartenlijst en zijwaarts scrollen in een
   kaart is slechter bedienbaar.
4. **Een statusvorm heeft altijd zijn icoon.** `StatusChip` negeert `icon={null}` voor statustonen (Gezond, Beperkt,
   Storing, Onbekend, Gepauzeerd): 15 aanroepplekken gaven (soms) een gekleurde pil zonder icoon. Alleen neutrale labels mogen
   zonder icoon. Daarnaast per plek de juiste as (Peil §4.1): open instellingen bij Koppelingen ("Vereist",
   "Bevestigen") zijn neutrale labels, geen Storing/Beperkt (regel 12); plan- en voorbeeldwijzigingen zijn een diff
   zonder rood/groen (Nieuw/Bijwerken = info met teken in een kader, Verwijderen = neutraal-sterk, Geblokkeerd = slot,
   alleen Fout = Storing; C-20); "Nog niet gecontroleerd" en "Identiteit: nog niet gecontroleerd" zijn Onbekend; de
   legenda van Dolby Vision-toestanden is neutraal (kleurde eerst op volgorde); de koppelingsmatrix heeft per cel een
   teken én vorm (✓ ~ − × … / ?), niet alleen een kleur.
5. **Onbekend in plaats van "—", skelet of "Nog geen metingen" als een bron niet antwoordt** (Peil §4.4, §9). De
   StatKaart toont bij Onbekend geen waarde (geen "—"), bij laden een skelet; met de core weg zijn de kaarten op
   Overzicht en Ruimte "Onbekend — MediaGuard-core reageert niet"; Belasting en Besparing per dag tonen bij een
   onbereikbare AV1-engine Onbekend in plaats van "Nog geen metingen" (dat zou niet waar zijn); de kopbalkpil is dan
   gestippeld "Onbekend" (was een rode pil met "—"); het opslagoverzicht en Activiteit gebruiken het gedeelde
   Foutpaneel (geen ruwe Engelse servertekst "Upstream service unreachable" als titel). De AV1-banner "engine
   reageert niet" is Onbekend (stippel, `wifi-off`), niet amber.
6. **Lijngrafiek met een gat:** het vlak onder de eerste reeks werd als één vorm over alle stukken gesloten en tekende
   een wig over het gat heen (suggereerde waarden waar geen meting was). Nu één vlak per aaneengesloten stuk tot de
   basislijn, en waar géén reeks een meting heeft een gearceerd vak (`arcering-gat`), zoals bij de staven.
7. **De laatste foutpagina (`app/global-error.tsx`) gebruikt letterlijke Peil-tokenwaarden.** Die pagina vervangt de
   hele layout en kan `globals.css` niet laden; de oude versie was altijd donker, met 80 % opacity op tekst en een
   knop van 3,7:1. Uitzondering op "geen hex buiten het tokenbestand" (Peil §3.1), met dezelfde waarden als
   `tokens.json` en licht/donker via `prefers-color-scheme`.
8. **Microcopy volgens Peil §11:** "Doorgaan" → "Opslaan en verder" (eerste start) en "Autopilot hervatten"
   (migratie); "In orde" → "Geslaagd"/"gecontroleerd"; "Beperkt automatisch" (AI-stand, botste met het statuswoord
   Beperkt) → "Automatisch binnen grenzen"; codelabel "let op" → "waarschuwing"; de AV1-engine-melding is gesplitst in
   een korte titel en uitleg (de zin "probeert het automatisch opnieuw" stond er twee keer); "Wat betekenen de
   kleuren?" → "Wat betekenen de tekens?"; "Bijgewerkt —" → "Nog niet bijgewerkt"; voorwaarden bij "Herstel nog niet
   gereed" elk op een eigen regel (stonden zonder leesteken achter elkaar). Elke nieuwe of gewijzigde tekst ook in
   en-US.

## Bewust niet gewijzigd

- **"Vrijgavecontrole: geslaagd" blijft groen** (Gezond-vorm): het is een gemeten controle (volledige decode, 0 fouten)
  en dus "gemeten en goed" (Peil §4.1 regel 1); zelfde keuze als ADR-0017.
- **"Niet beschikbaar" bij een aflevering** (indexers vinden niets) blijft een neutraal label: domeinbetekenis, geen
  `capability_unavailable`. Botst in woord met Peils capability-term; vervolgpunt voor de ontwerptaal.
- **Teksten uit de core zijn alleen Nederlands** (`title_nl`, `detail_nl`, `summary_nl`): in en-US staan die zinnen
  nog in het Nederlands. Gelijkwaardig maken vraagt een API-/corewijziging (buiten deze ronde).
- **Native `<input type="time">` en `<select>`**: de 12-uursweergave in de headless browser en het afkappen van een
  lange gekozen optie komen van de browser, niet van de app.

## Gevolgen

- OpenAPI ongewijzigd (383 paden, sha256 `54eee4d7…3e59a`); geen route, API-aanroep, datamodel of installatie
  veranderd. Eén e2e-tekstbewering volgde de nieuwe knoptekst ("Opslaan en verder").
- De Peil-e2e (`apps/web/e2e/peil.spec.ts`) telt nu elke axe-overtreding (alle impactniveaus), controleert losse links
  en selectievakjes op 44 px, onderstreping van tekstlinks en "geen waarde bij Onbekend".
