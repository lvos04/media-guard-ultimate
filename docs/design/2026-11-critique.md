# Ontwerpkritiek MediaGuard-web (november 2026, v1.1.2)

Basis: schermafbeeldingen van 14 hoofdpagina's op 1440 en 390 px, donker en licht, met de E2E-dataset
(`apps/web/e2e/design-shots.spec.ts`, alleen met `MG_SHOTS_DIR`). Methode: design-critique-framework
(eerste indruk, bruikbaarheid, hiërarchie, consistentie, toegankelijkheid).

## Overall impression

De basis is goed: rustig donker thema, duidelijke kaarten, een "Wacht op jou"-lijst die precies zegt
wat er moet gebeuren. Het grootste probleem is de **navigatiediepte**. Er zijn vijf hoofdonderdelen,
maar daarbinnen zitten tot drie lagen tabs en een verzamelpagina "Gevorderd" met nog tien pagina's.
Wie iets zoekt, moet weten onder welke tab het staat.

## Usability

| Bevinding | Ernst | Aanbeveling |
|---|---|---|
| Instellingen heeft **twee tabrijen boven elkaar** (7 hoofdtabs, daaronder bij Koppelingen nog 8), plus "Gevorderd" met 10 losse pagina's (gebruikers, API-sleutels, geheimen, beveiliging, Release Lab, anime, Dolby Vision, AI, downloads, Seerr) | 🔴 Kritiek | Eén lange Instellingen-pagina met secties, een zoekveld bovenaan en een inhoudsopgave (anker-navigatie) links of als uitklapper op de telefoon. Geen tweede tabrij. |
| Activiteit heeft 7 tabs (Nu, Problemen, Downloads, Aanvragen, Opslag, Wat is er gebeurd, Geschiedenis); op de telefoon scrollen de tabs uit beeld | 🔴 Kritiek | Activiteit = één tijdlijn met filters (chips) + vaste blokken "Nu bezig" en "Problemen" bovenaan. Aanvragen wordt een eigen hoofdonderdeel; Opslag gaat naar Ruimte. |
| Ruimte heeft 5 tabs, waarvan "Instellingen" een tweede ingang is naar AV1-instellingen die ook onder Instellingen staan | 🟡 Matig | Ruimte = Besparing (bovenaan), Wachtrij en Terugzetten als secties op één pagina; AV1-instellingen alleen onder Instellingen → AV1 (met een link "Instellingen" in de paginakop). |
| Seerr-aanvragen zijn niet te doen vanuit MediaGuard; Aanvragen is een sub-tab van Activiteit | 🟡 Matig | Nieuw hoofdonderdeel **Aanvragen**: zoeken met posters, aanvragen, status per aanvraag. |
| Statusmatrix "Status per instantie en sectie" (Koppelingen) heeft 10 kolommen; de instantienaam breekt per letter af ("Volledi g door MediaG uard") | 🟡 Matig | Per instantie één kaart met een lijst secties en hun status; matrix alleen als tabel vanaf 1200 px met vaste eerste kolom. |
| Seizoenen kunnen niet aan/uit worden gezet; kwaliteit en reden van upgrades zijn niet zichtbaar | 🟡 Matig | Serie-detail: per seizoen een schakelaar "Gevolgd"; afleveringsdetail: paneel Kwaliteit en tijdlijn Upgrades. |
| Overzicht toont dezelfde informatie twee keer ("4 dingen hebben je hulp nodig" + lijst "Wacht op jou 4"; zijbalk "4 dingen vragen aandacht"; kopbalk "Aandacht 4") | 🟢 Klein | Eén banner met de lijst eronder; zijbalk-regel weg (kopbalk-pil blijft). |

## Visual hierarchy

- **Wat valt eerst op:** de rode banner "Bibliotheek nog vergrendeld" en daarna de rode kaart
  "N dingen hebben je hulp nodig". Juist wanneer er iets nodig is; als alles goed gaat, moet de
  kop "Alles in orde" het eerste zijn (dat gebeurt al).
- **Leesvolgorde:** Overzicht gaat van problemen → nu bezig → vier cijferkaarten → een losse
  tekstregel ("Laatste 24 uur…") → nieuwe afleveringen. De losse tekstregels tussen kaarten
  breken het ritme.
- **Nadruk:** cijferkaarten tonen "—" en "Nog niet beschikbaar" groot en vet; lege toestanden
  krijgen daardoor evenveel gewicht als echte cijfers.

## Consistency

| Element | Probleem | Aanbeveling |
|---|---|---|
| Paginakoppen | Sommige pagina's hebben een uitleg met inline-woordenlijstlinks (Ruimte), andere alleen een datum (Overzicht) | Vaste kop: titel, één zin uitleg, rechts één primaire actie |
| Tabs | Drie verschillende tabstijlen (hoofdtabs, sub-tabs, segmented control "Eenvoudig/Alle instellingen") | Eén tabstijl, maximaal één rij per pagina |
| Tekstgrootte | Kopjes in kaarten 15–17 px, lichaamstekst 13–14 px, secundaire tekst soms 12 px | Typografieschaal 24/18/15/13 (zie design-system.md) |
| Lege toestanden | "Nog geen metingen", "Nog niet beschikbaar", "—" door elkaar | Eén lege-toestandscomponent met icoon, één zin, optionele actie |
| Thema | Een thema-keuze in localStorage wordt overschreven door het serverthema (schermafbeeldingen "licht" toonden donker) | Persoonlijke keuze gaat voor; serverthema alleen als standaard |

## Accessibility

- **Contrast:** hoofdtekst op donker voldoet; grijze secundaire tekst (#8a94a6 op #111827) zit
  rond 4,6:1 en haalt AA net; op lichtblauwe infobalken is het lager.
- **Aanraakdoelen:** knoppen ≥ 44 px op telefoon (eerder gerepareerd). Tabs onderaan de tabrij
  op 390 px vallen buiten beeld en zijn alleen te vinden door horizontaal te vegen.
- **Leesbaarheid:** lange uitleg in Instellingen (tot 8 regels per instelling op de telefoon);
  de pagina is op 390 px ruim 5000 px lang.

## What works well

- "Wacht op jou" met per regel één duidelijke actieknop.
- Duidelijke statuspil in de kopbalk en een rustige onderste navigatie op de telefoon.
- Nederlandse, mensvriendelijke teksten; technische details achter een uitklapper.
- Zoekbalk die ook instellingen vindt.

## Priority recommendations

1. **Vlakke navigatie** — Hoofdonderdelen: Overzicht · Activiteit · Bibliotheek · Ruimte ·
   Aanvragen, met Instellingen in het profielmenu/zijbalk-onderaan. Maximaal één tabrij per pagina.
2. **Instellingen als één doorzoekbare pagina** — secties met ankers in plaats van 7 + 8 tabs en
   een "Gevorderd"-verzamelbak; elke instelling één regel uitleg, de rest achter "Meer uitleg".
3. **Bibliotheek-detail met Kwaliteit en Upgrades** — de vraag "waarom is dit gedownload en hoe
   goed is het" wordt op de titelpagina beantwoord; seizoenen aan/uit op dezelfde plek.
