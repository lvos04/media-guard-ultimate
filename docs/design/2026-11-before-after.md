# Voor en na: voorstel nieuwe opzet (november 2026)

Status: **voorstel, nog niet gebouwd.** Het klikbare prototype staat in
`docs/design/prototype/mediaguard-redesign.html` (één bestand, opent in elke browser).
Schermafbeeldingen van nu: `docs/design/before/`; van het prototype: `docs/design/prototype/shot-*.png`.
De onderbouwing staat in `2026-11-critique.md`.

| Onderwerp | Nu (v1.1.2) | Voorstel |
|---|---|---|
| Hoofdmenu | Overzicht · Activiteit · Bibliotheek · Ruimte · Instellingen (+ Migratie tijdelijk) | Overzicht · Activiteit · Bibliotheek · Ruimte · **Aanvragen** · Instellingen los onderaan (telefoon: via profielknop) |
| Tabs | Tot drie lagen: Instellingen 7 tabs + Koppelingen 8 sub-tabs + "Gevorderd" met 10 pagina's; Activiteit 7 tabs; Ruimte 5 tabs | Hooguit één rij filters of tabs per pagina |
| Instellingen | Verspreid over ±20 pagina's, deels dubbel (AV1-instellingen ook onder Ruimte) | Eén pagina met secties (Algemeen, AV1 & ruimte, Downloaden & zoeken, Taal & anime, Koppelingen, Meldingen, Beveiliging & toegang, Systeem), inhoudsopgave en zoeken; uitleg in één regel, rest achter "Meer uitleg" |
| Activiteit | 7 tabs (Nu, Problemen, Downloads, Aanvragen, Opslag, Wat is er gebeurd, Geschiedenis) | Blokken Nu bezig + Problemen, daaronder één tijdlijn met filterchips |
| Opslag & downloads | Tab onder Activiteit | Sectie "Waar ging de ruimte heen" onder Ruimte, met kolom Waarom |
| Bibliotheek-detail | Geen seizoenschakelaars; kwaliteit alleen als label; geen reden van upgrades | Per seizoen "Gevolgd"-schakelaar; paneel Kwaliteit (formats, score, doel bereikt?); tijdlijn Upgrades (van → naar, score, reden, AV1) |
| Aanvragen | Alleen de status, als tab onder Activiteit | Eigen onderdeel: zoeken met posters, aanvragen met seizoenkeuze, status samen met de levering door MediaGuard |
| Overzicht | Banner + "Wacht op jou" + zijbalkregel + kopbalkpil tonen hetzelfde aantal | Eén banner met knop, cijferkaarten, "Laatste 24 uur" en "Wacht op jou" naast elkaar |
| Lege toestanden | "—", "Nog niet beschikbaar", "Nog geen metingen" door elkaar | Eén component: icoon, één zin, eventueel actie |
| Typografie | Losse groottes (12–17 px in kaarten) | Schaal 24 / 18 / 15 / 13 px; kaarten 14 px radius; 16 px tussenruimte |

## Wat ongewijzigd blijft

- Alle functies blijven bereikbaar; oude adressen sturen door naar de nieuwe plek.
- Donker thema als standaard, licht thema en "volgens apparaat" blijven.
- Onderste navigatie op de telefoon, geen zijwaarts scrollen.

## Bekend in de huidige versie (los van het voorstel)

- Een persoonlijke themakeuze wordt overschreven door het serverthema (schermafbeeldingen "licht"
  kwamen donker uit). Kan los worden opgelost.
