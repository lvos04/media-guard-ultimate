# ADR-0017 — Peil in de web-UI: bouwkeuzes van stap 3

- **Status:** geaccepteerd
- **Datum:** 2026-11-09
- **Fase:** ontwerpronde Peil 1.1.0, stap 3 (web-UI `apps/web`)
- **Context:** [`docs/ontwerp/PEIL_MIGRATIE.md`](../ontwerp/PEIL_MIGRATIE.md) §8–§11, [ADR-0016](ADR-0016-peil-omzetting-interpretaties.md),
  Peil 1.1.0 in [`design/ontwerptaal/`](../../design/ontwerptaal/HERKOMST.md). Vaste regels: alleen uiterlijk + de
  toegestane toevoegingen ("Terug naar de Hub", tabelweergave + toetsenbord bij de grafieken); functies, API-routes,
  foutformaat, datamodel, poorten en installatie veranderen niet; OpenAPI byte-gelijk; testids en rollen blijven.

ADR-0016 legde de interpretaties vast. Dit ADR legt vast wat er bij het bouwen is gekozen waar het ontwerp ruimte liet
of waar de bouw iets anders vroeg, met de veiligste lezing.

## Besluiten

1. **Cascadelagen in plaats van "eerst losse CSS, dan opruimen".** `globals.css` zet
   `@layer theme, base, legacy, components, utilities`; `mg.css`, `av1.css`, de gedeelde aliaslaag en
   `styles/peil-mg.css` staan in `legacy`. Tailwind-utilities en de shadcn-componenten winnen daardoor altijd van de
   oude stijl, en de oude stijl nog steeds van de Tailwind-preflight. Dat is fase 1a (alleen CSS-imports) en maakt
   fase 2 per component mogelijk zonder `!important`. Zelfde keuze als MSO.
2. **`mg.css` blijft, maar zonder de omgezette onderdelen.** De regels van de schil, knoppen, pillen, tabs,
   schakelaars, dialogen, toestanden en kaarten zijn uit `mg.css` (ze zijn nu shadcn/Tailwind). Plekken die die
   klassen nog rechtstreeks in markup gebruiken (`.btn`, `.chip`, `.card`, `.state`, `.nav-item`, …) krijgen in
   `peil-mg.css` de Peil-vorm. Alles in één keer herschrijven (794 inline styles) is een grotere, risicovollere
   wijziging zonder extra waarde voor de gebruiker; de hookklassen die tests gebruiken blijven zo vanzelf bestaan.
   Let op: `.grid` is in `mg.css` het 12-koloms paginaraster; componenten gebruiken `inline-grid` of `flex`.
3. **Groen betekent alleen gemeten gezondheid.** De toon `success` (een goede uitkomst die geen gezondheid is:
   "Afgerond", "Binnen", "Aan") is overal een neutraal label met een vinkje; alleen de nieuwe toon `healthy` is groen.
   Gezond staat alleen bij koppelingen en componenten (gemeten) en bij een geslaagde vrijgavecontrole (een gemeten
   controle). Zo wordt niets per ongeluk groen dat geen gezondheid is (136 plekken gebruikten `success`).
4. **Totaalstatus op Overzicht (SysteemStatusPaneel).** Core onbereikbaar → **Onbekend — MediaGuard-core reageert
   niet**; een storing of waarschuwing in "Wat actie nodig heeft", of fouten in het gezondheidsmodel → **Beperkt**;
   anders **Alles gezond**. Inrichting en info (Seerr koppelen, herstelcode, tweestapsverificatie) maken de status
   nooit amber of rood; ze staan alleen in de lijst. "Storing" wordt hier niet gebruikt: MediaGuard draait dan nog,
   er faalt een deel. De telling ("6 dingen wachten op jou") blijft in het paneel staan, zodat kopbalk, zijbalk en
   Overzicht hetzelfde getal tonen (bestaande e2e N13). De kaart "Wacht op jou" heet nu **Wat actie nodig heeft**.
5. **Modale dialogen zijn echt modaal.** De `Modal` gebruikt Radix Dialog: focusval, focus terug naar de knop die hem
   opende, en de rest van de pagina is voor hulptechnologie verborgen zolang de dialoog open is. Eén bestaande e2e
   controleerde een knop *achter* een open dialoog; die controle staat nu vóór het openen (zelfde bewering). `role`
   (`dialog`/`alertdialog`) en `aria-modal` blijven, zodat de sneltoetsen zwijgen zoals voorheen.
6. **Geen Sonner, geen nieuwe toastbibliotheek.** De bestaande toast krijgt de Peil-vorm (rand, iconen: gelukt =
   vinkje, mislukt = octagon-x). Een extra afhankelijkheid voegt hier niets toe.
7. **Standaardthema Systeem ook in de eerste-startwizard.** De wizard bood alleen Donker/Licht en schreef altijd
   `ui.theme = dark`; daarmee zou de nieuwe standaard (ADR-0016 punt 5) bij elke nieuwe installatie meteen weer
   Donker worden. De wizard biedt nu dezelfde drie keuzes als het profielmenu, met Systeem voorgeselecteerd. Er komt
   geen nieuwe instelling of waarde bij (`system` bestond al).
8. **Titelkiezer houdt zijn rol.** axe meldde een `aria-controls` naar een lijst die (nog) niet bestaat. Alleen die
   verwijzing staat nu alleen aan als de lijst er is; het veld blijft een tekstvak (een rolwijziging naar `combobox`
   brak een bestaande e2e en valt onder "rollen blijven").
9. **Ondernavigatie: woorden heel op 360–390 px.** Vijf items van 14 px passen niet in vijf gelijke kolommen
   ("Bibliotheek"). De items zijn nu zo breed als hun woord; op 360 en 390 px past elk woord heel, pas op 320 px
   lopen ze om. Geen kortere woorden (dat zou een tekstwijziging met e2e-gevolgen zijn).
10. **Grafieken: wat wel en niet gebouwd is.** Gebouwd: tabel + toetsenbord + voorlezen bij Belasting (+ Geheugen),
    Besparing per dag (doorlopende datumas, "Geen meting" voor een ontbrekende dag), Opslagtrend, Verdeling en de
    Episodekaart. **Niet gebouwd:** de prognose als stippellijn in de Opslagtrend (de prognose blijft de bestaande
    zin "vol over …"); dat is een nieuwe weergave van een bestaand getal en kan later. De Episodekaart-component
    (`SeasonHeatmap`) heeft de Peil-vorm en een tabel, maar wordt op dit moment op geen enkele pagina gebruikt; de
    titelpagina toont seizoenen als lijst met neutrale beschikbaarheidslabels.
11. **Doelen ≥ 44 px, met de inline-uitzondering.** Knoppen, tabs, schakelaars, velden, segmenten en losse links zijn
    44 px. Vaktermen in lopende tekst (`.term-btn`, de uitleg-knopjes bij een woord) vallen onder de inline-uitzondering
    van WCAG 2.5.8, net als links in tekst; de Peil-e2e sluit ze daarom uit.
    Nog kleiner dan 44 px (vervolgpunt): een paar losse tekstlinks (`back-link`, `link-arrow`, links in een label-pil)
    en selectievakjes/keuzerondjes buiten een label (zie `docs/screenshots/peil/na/README.md`).
12. **Fonts weg, body 16 px.** Inter en JetBrains Mono (bestanden, `next/font/local`, `@fontsource-variable/*`) zijn
    verwijderd; systeemfont volgens Peil O-01. Daarmee vervalt het punt "Inter echt toepassen" uit ADR-0015; de e2e
    controleert nu het systeemfont. Tekst onder 14 px is overal 14 px (CSS en inline styles); body is 16 px.
13. **Wat buiten deze stap blijft.** De Android-app (PEIL_MIGRATIE §10) is een aparte stap. Inline styles die geen
    lettermaat of statuskleur zijn, blijven staan (ze volgen de tokens via de aliaslaag). Excel-statussen veranderen
    niet: geen Excel-taak gaat over de ontwerptaal.

## Gevolgen

- Controle in CI (job `web`): `npm run check:peil` (Peil-kopieën), `npm run test:unit` (vitest) en de Peil-e2e
  (`e2e/peil.spec.ts`) naast de bestaande e2e.
- OpenAPI-export ongewijzigd: 383 paden, sha256 `54eee4d7…3e59a` (gemeten vóór en na elke corewijziging).
- **NIET GEVALIDEERD:** echte schermlezer (NVDA/TalkBack), 200 %-zoom, forced colors op MediaGuard zelf, echte
  telefoon, echte NAS.
