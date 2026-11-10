# ADR-0018 — Peil in de Android-app: bouwkeuzes van stap 4

- **Status:** geaccepteerd
- **Datum:** 2026-11-09
- **Fase:** ontwerpronde Peil 1.1.0, stap 4 (native Android-app `apps/android`)
- **Context:** [`docs/ontwerp/PEIL_MIGRATIE.md`](../ontwerp/PEIL_MIGRATIE.md) §10–§11 en §14,
  [ADR-0016](ADR-0016-peil-omzetting-interpretaties.md) (punten 3, 4, 9, 11), [ADR-0017](ADR-0017-peil-web-bouwkeuzes.md)
  (dezelfde keuzes in de web-UI), Peil 1.1.0 in [`design/ontwerptaal/`](../../design/ontwerptaal/HERKOMST.md), besluit van
  de gebruiker van 9 november 2026 "ook de Android-apps van MediaGuard en my_house in Peil-stijl". Vaste regels: alleen
  uiterlijk; functies, API-aanroepen, datamodel, rechten, ondertekening en installatie veranderen niet; geen
  versie-ophoging; teksten in NL (de app is niet meertalig, ADR-0016 punt 4).

## Besluiten

1. **Kleuren en typografie gegenereerd uit `tokens.json`.** `apps/android/tools/peil_tokens.py` schrijft
   `ui/theme/PeilTokens.kt` (alle kleurtokens licht en donker, typografierollen, radius, aanraakmaat) met de sha256 van
   de gepinde `tokens.json` in de kop. `PeilTokensTest` vergelijkt elke waarde en de sha256; CI draait daarnaast
   `peil_tokens.py --check`. `MgColors` blijft als naam (veel aanroepen) maar elke waarde komt uit Peil volgens de tabel
   in PEIL_MIGRATIE §10.1; `violet` en `violetSoft` zijn weg (violet = goedkeuring). Alle Material 3-rollen komen uit
   Peil (ook secondary, tertiary, inverse, surfaceTint), zodat nergens het standaardpaars van Material verschijnt.
   Zelfde aanpak als my_house ADR-0010, zodat beide apps dezelfde generator en test hebben.
2. **Systeemfont en 14 sp als ondergrens.** Inter (`res/font/inter_*`, OFL-licentie) is uit de app; `FontFamily.Default`
   is Roboto op Android (Peil §5). Rollen: meta 14/20, body 16/24, titel 18/28, sectie 20/28, pagina 24/32, waarde
   30/36 met proportionele cijfers; vaste cijferbreedte (`cijfers`) alleen voor kolommen, tijden en percentages. De
   regel "Lettertype: Inter — SIL OFL" onder Instellingen → Over wordt "Iconen: Lucide — ISC-licentie" (de licentie
   die de app nu meelevert).
3. **Iconen: lucide als vector drawables, geen bibliotheek.** `tools/lucide_naar_vector.mjs` zet 55 iconen uit
   `lucide-react` 1.66.0 (de versie van `apps/web/package.json`; het script weigert een andere) om naar
   `res/drawable/lucide_*.xml`, met de licentie als `assets/licenses/lucide-LICENSE.txt`. `material-icons-extended` is
   uit de afhankelijkheden: één beeldtaal met de web, één icoon per betekenis (Peil §7). Navigatie-iconen gelijk aan de
   web (`layout-dashboard`, `radar`, `library`, `hard-drive-download`, `inbox`); de noodstopknop `octagon-alert` zoals
   in de web.
4. **Status, labels en soorten zonder Compose (unit-getest).** `ui/PeilUi.kt` legt vast welk woord, icoon en welke vorm
   iets krijgt: `PeilStatus` (Gezond · Beperkt · Storing · Onbekend · Verouderd · Gepauzeerd · Niet gekoppeld · Niet
   beschikbaar), `Kenmerk` (neutraal of neutraal-sterk label), `AttentionKind` (storing `octagon-x`, waarschuwing
   `triangle-alert`, voorwaarde `lock`, info `info`) en `Baan`. `Tone` heeft nu de Peil-betekenis (geen `Accent` en
   `Violet` meer; nieuw: `Strong`, `Unknown`, `Stale`, `Paused`). Tekenen gebeurt in `ui/Components.kt`
   (`StatusBadge`, `PeilLabel`, `Melding`, `StatusPanel`, `KpiTile`, `MgButton`, …).
5. **Totaalstatus zoals de web (ADR-0017 punt 4).** Een storing of waarschuwing in "Wat actie nodig heeft" → Beperkt;
   anders Alles gezond. Een voorwaarde (`library.locked`, `encoder.paused.*`) en info maken de status niet slechter; ze
   staan in de lijst met een slot en de zin "Dit is geen storing; er wacht een voorwaarde." Noodstop aan = Gepauzeerd
   (ADR-0016 punt 9), verouderde gegevens = Verouderd; die gaan in de bestaande volgorde vóór de lijst. "Storing" als
   totaal wordt (net als in de web) niet gebruikt: MediaGuard draait dan nog, er faalt een deel.
6. **48 dp en niets dat midden in een woord breekt.** Knoppen, tabs, schakelaars (52 × 32 binnen 48 dp),
   icoonknoppen en aanraakbare lijstregels zijn minstens 48 dp. De filterchips worden tabs die omlopen (de gekozen tab
   valt nooit buiten beeld). De bovenbalk meet wat past: eerst valt de merknaam weg (het logo blijft, met de naam voor
   TalkBack), dan het woord "Aandacht" (icoon en telling blijven, de volledige naam staat in de toegankelijke tekst).
   De woorden in de ondernavigatie groeien mee met de systeemletter tot de balk vol is en blijven daarna zo groot als
   past, nooit kleiner dan 14 sp zonder vergroting. Geen ellips meer op titels, uitleg en labels.
7. **Voortgangsbalk op het kaartvlak, niet op `chart-meter-track`.** De CPU-baan (`domain-lane-cpu`) haalt op
   `chart-meter-track` maar 2,4:1 (licht). Het spoor is daarom het kaartvlak met een 1 dp-rand `border-strong`; elke
   reeks- en baankleur haalt daarop ≥ 3:1. Om dezelfde reden staan neutrale labels op het kaartvlak (een baanicoon op
   `muted` haalt 2,6:1). `PeilContrastTest` bewaakt alle paren die de schermen tekenen, licht en donker.
8. **Verouderd: wel het uiterlijk, niet de bediening uitzetten.** PEIL_MIGRATIE §10.3 noemt bij verouderde gegevens
   "bediening uit met reden". Knoppen uitzetten is een gedragswijziging (een actie die nu werkt zodra de server weer
   antwoordt, zou dan geblokkeerd zijn) en valt buiten "alleen uiterlijk". Veiligste lezing: het statuspaneel en de
   tegels tonen eerlijk dat de stand verouderd is (gestippeld, "Laatst bekend · …", waarde in `stale-ink`); de
   bestaande knoppen blijven zoals ze waren. Gemeld als contractpunt.
9. **Foutpaneel zonder code en trace-ID.** Het foutmodel van de app (`UiState.Error(message)`) draagt geen foutcode of
   trace-ID. Het Foutpaneel toont wat er mislukte ("Laden is mislukt") en het waarom in gewone taal met "Opnieuw
   proberen"; code en trace-ID staan er niet (geen verzonnen waarden; ze toevoegen vraagt een wijziging in de
   datalaag).
10. **Koppelscherm opgesplitst voor de afdrukken.** `PairingScreen` (toestand, scanner, netwerk) roept nu
    `PairingContent` aan (alleen tekenen). Gedrag en teksten van de koppeling zijn gelijk; nieuw zijn de kopjes van de
    meldingen ("De sleutel is geweigerd", "Opnieuw koppelen", "Koppelen is mislukt") en "Verbinden…" naast de
    voortgangscirkel. Sleutel geweigerd = gemeten afwijking (`octagon-x`); opslag gewist = info.
11. **Schermafdrukken als test in de repo.** Paparazzi tekent 18 schermen en toestanden (16 zoals de nulmeting + 2
    van het koppelscherm) in licht/donker × 393 en 360 dp, plus grote letter (1,3×) voor zes schermen: 78
    referentiebeelden in `app/src/test/snapshots/images/` (± 6,9 MB). CI draait `:app:verifyPaparazziDebug` (met alle
    unit-tests) in plaats van `:app:testDebugUnitTest`; tolerantie 0,5 %. De test-JVM draait in UTC (zoals de
    CI-runner) en de datum op Overzicht staat in de test vast, zodat de vergelijking niet van de machine of de dag
    afhangt. De documentatie-afdrukken (2 px per dp, ook "volledig") ontstaan alleen met `PEIL_AFDRUKKEN=1` en horen
    niet bij de referentieset.
12. **Niet-ondertekende release-build alleen op verzoek.** Met `MG_ANDROID_UNSIGNED=1` geeft `assembleRelease`
    `app-release-unsigned.apk`; zonder die variabele verandert de ondertekening niet (eigen sleutel via secrets,
    anders de vaste sideload-sleutel). Zo is een niet-ondertekende build te controleren zonder de update-keten
    (Obtainium, GitHub-release) te raken.
13. **CI voor elke branch die de app raakt.** `android.yml` liep alleen op `main`; nu ook op een push naar een andere
    branch die `apps/android/**` of `tokens.json` raakt. De stap die een APK aan een GitHub-release hangt, loopt nog
    steeds alleen na de workflow *release* of bij een handmatige start.

## Afgewezen alternatieven

- **Kleuren met de hand overnemen in `Theme.kt`.** Drift met de bron blijft dan onzichtbaar.
- **`material-icons-extended` houden of een lucide-bibliotheek toevoegen.** Twee beeldtalen, of een extra afhankelijkheid
  voor 55 iconen.
- **Het woord "Aandacht" en de merknaam altijd tonen en laten afbreken** ("MediaGu/ard" op 360 dp). Een woord dat midden
  in breekt is slechter leesbaar dan een logo met een toegankelijke naam.
- **De ondernavigatie bij een grote systeemletter laten afkappen of laten scrollen.** Afkappen verbergt een deel van het
  woord ("Aanvrag"); scrollen verbergt een heel item.
- **Bediening uitzetten bij verouderde gegevens** (punt 8) en **trace-ID's toevoegen** (punt 9): functionele wijzigingen.

## Gevolgen

- Uiterlijk wijzigen = afdrukken opnieuw vastleggen (`:app:recordPaparazziDebug`) en de verschillen in de review
  bekijken; documentatie-afdrukken met `PEIL_AFDRUKKEN=1` en `docs/screenshots/peil/android/verwerk.py`.
- APK zonder `material-icons-extended` en Inter: debug 12,5 MB, niet-ondertekende release 9,3 MB (gemeten op 9 november
  2026; de vorige ondertekende release was 13,6 MB volgens de testcontrole van die dag).
- Excel-statussen veranderen niet: geen Excel-taak gaat over de ontwerptaal.
- **NIET GEVALIDEERD (hardware):** geen echte telefoon, geen emulator, geen TalkBack. Een rooktest op een headless
  emulator kon in deze sessie niet: de bouwomgeving had geen toegang tot `/dev/kvm` (`emulator -accel-check`: "This
  user doesn't have permissions to use KVM"). Bewust geen systeemrechten gebruikt om die toegang alsnog te krijgen.
  Herstel: een omgeving met KVM-toegang en dan een rooktest met het systeemimage
  `android-35;google_apis;x86_64` dat al in `~/android-sdk` staat: installeren, starten, `uiautomator dump` en controleren
  dat elk klikbaar element ≥ 48 dp is.
