# ADR-0016 — Omzetting naar Peil: interpretaties voor MediaGuard (web en Android)

- **Status:** geaccepteerd (ontwerp; de bouw volgt in stap 3)
- **Datum:** 2026-11-09
- **Fase:** ontwerpronde Peil 1.1.0, stap 2 (omzettingsontwerp)
- **Context:** [`docs/ontwerp/PEIL_MIGRATIE.md`](../ontwerp/PEIL_MIGRATIE.md), ontwerptaal Peil 1.1.0
  ([`design/ontwerptaal/`](../../design/ontwerptaal/HERKOMST.md)), besluiten van de gebruiker van 9 november 2026
  (O-11 "Terug naar de Hub", tabelweergave + toetsenbord bij de grafieken van MediaGuard en MSO, ook de native
  Android-apps van MediaGuard en my_house in Peil-stijl). Vaste regels van deze ronde: alleen uiterlijk + de toegestane
  toevoegingen; functies, API-routes, foutformaat, datamodel, poorten en installatie veranderen niet; OpenAPI
  byte-gelijk; `data-testid`'s en rollen blijven; MediaGuard-teksten in NL én en-US.

Waar het contract of de ontwerptaal iets openlaat of zichzelf tegenspreekt, is hier de veiligste lezing gekozen. Waar
MSO (een kopie van MediaGuards stijl) al een keuze maakte (MSO ADR-0006), volgt MediaGuard die, zodat beide apps gelijk
blijven.

## Besluiten

1. **Hub-adres als optionele instelling, zonder API-wijziging.** De link "Terug naar de Hub" leest zijn adres uit een
   nieuwe instelling `system.hub_url` in het bestaande instellingenregister (standaard leeg). Lezen/schrijven gaat via
   de bestaande settings-API en het schema-gestuurde instellingenscherm. Er komt geen nieuwe route, geen migratie, geen
   veld in het Custom App-formulier en geen omgevingsvariabele die de installatie verandert. Validatie volgens de
   `HUB_URL`-regels van contract §3.2; ongeldig of leeg ⇒ geen link (niet in de DOM). Sleutelnaam volgt MediaGuards
   eigen `system.*`-reeks (MSO gebruikt `systeem.hub_url` in zijn eigen register). Controle: OpenAPI-export vóór en na
   (nulmeting sha256 `54eee4d7…3e59a`, 383 paden) moet byte-gelijk zijn.
   *Afgewezen:* de Hub-URL uit de koppeling (contract §3.7) afleiden — MediaGuard heeft die koppeling nog niet; een
   omgevingsvariabele — verandert het installatieformulier.
2. **DangerDialog blijft een dialoog.** Peil wil R2/R3-goedkeuringen op een eigen pagina; dat geldt voor
   Hub-goedkeuringen. MediaGuards gevaarlijke acties (definitief verwijderen, terugzetten, bibliotheek vrijgeven) zijn
   lokale acties met een getypte bevestiging. Een eigen pagina zou een nieuwe route en een andere gebruikersreis zijn.
   Ze krijgen de stijl van GoedkeuringsPagina stap 2 binnen een AlertDialog (op < 640 px als sheet van onder).
3. **Geen Hub-link in de Android-app.** De beslissing O-11 noemt de web-apps; de Android-app krijgt alleen de
   Peil-stijl. Een Hub-link in de app zou een nieuwe functie zijn.
4. **Android blijft Nederlandstalig.** De web-UI heeft NL + en-US; de Android-app heeft alleen NL (teksten in Kotlin).
   De regel "nieuwe teksten in beide talen" wordt voor Android vervuld door elke nieuwe of gewijzigde tekst in het
   ontwerp in NL én en-US vast te leggen, zonder de app meertalig te maken (dat is een functionele toevoeging).
   *Contractpunt:* als Android ook en-US moet krijgen, is dat een apart besluit.
   **Vervangen door ADR-0023:** de Android-app heeft nu Nederlands en en-US; de taal volgt Android.
5. **Standaardthema wordt Systeem.** ONTWERPTAAL §13.3 schrijft het voor. De bestaande instelling `ui.theme` krijgt als
   standaard `system` (was `dark`); de keuze per gebruiker en de sleutel `mg.theme` blijven. Het themascript zet altijd
   het opgeloste `data-theme="dark|light"` (Peil kent geen `prefers-color-scheme`-terugval). De e2e-test die direct na
   inloggen Donker verwacht, kiest eerst expliciet een thema.
6. **Beschikbaarheid is geen gezondheid.** "Binnen", "Ontbreekt", "Zoekt", "Verwacht", "Niet gevolgd" en "Compleet"
   krijgen een neutrale vorm met eigen icoon en woord, geen status-groen/-rood/-amber (Peil §4.1 regels 1–3). Groen
   blijft alleen voor gemeten gezondheid en voor gemeten controles (vrijgavecontrole geslaagd). In de episodekaart worden
   de toestanden grafiekslots (chart-1…3) met een vorm als tweede kanaal en een tabel als tweeling.
7. **Backendteksten blijven.** Velden als `*_nl`, `message` en `status_nl` uit de core blijven ongewijzigd. De UI kiest
   het Peil-woord uit de status en toont de backendtekst alleen als reden.
8. **Geen R-klassen zonder beleid.** MediaGuard levert voor zijn acties geen risicoklasse uit een beleid. De UI toont
   daarom geen RisicoBadge (R0–R3) bij MediaGuard-acties; dat zou nieuwe, niet onderbouwde informatie zijn. De
   gevaarstijl (destructive-knop, getypte bevestiging) blijft. Integriteitsregels worden BeleidsSlot ("Altijd aan").
9. **Noodstop actief = Gepauzeerd-vorm.** De noodstop is een bewuste stop door de gebruiker, geen gemeten falen. De
   banner krijgt de `paused`-vorm (`circle-pause`, info) met de woorden "Noodstop actief"; de noodstopknop blijft
   `destructive` (gedeelde aliaslaag). Zo ook in MSO ("Gepauzeerd door de MediaGuard-noodstop").
10. **Belastingsgrafiek: maximaal drie lijnen.** De dataviz-validator laat zien dat vier reeksen met alle paren niet
    uit elkaar te houden zijn (ook met Peil-kleuren). GPU, CPU en RAM blijven samen (eerste drie slots, alle paren
    gevalideerd); cache gaat naar een kleine tweede grafiek "Geheugen". Dezelfde gegevens, geen tweede y-as, geen
    nieuwe API.
11. **Android-aanraakdoelen 48 dp.** Android-richtlijn, ruimer dan Peils 44 px; iconen als lucide-vector drawables
    (geen nieuwe bibliotheek), systeemfont Roboto in plaats van de ingebouwde Inter (zelfde keuzes als my_house
    ADR-0008).
12. **Overloop meten t.o.v. de apparaatbreedte.** Met `isMobile` rekt Chromium het layout-viewport op;
    `scrollWidth − innerWidth` is dan altijd 0. Nulmeting en nieuwe tests meten t.o.v. de ingestelde breedte (zoals
    MSO ADR-0006).

## Gevolgen

- Stap 3 bouwt in de volgorde van PEIL_MIGRATIE §8 (web) en §10 (Android); elke stap één commit, terug te draaien.
- De gedeelde aliaslaag mist voor `mg.css`-apps de focusring en scroll-padding; dat wordt per app opgelost
  (`styles/peil-mg.css`) en is gemeld als vervolgpunt voor de ontwerptaal.
- Geen Excel-status verandert door dit ontwerp; echte telefoon, schermlezer en NAS blijven NIET GEVALIDEERD.
