# ADR-0023 — De Android-app ook in en-US

- **Status:** geaccepteerd
- **Datum:** 2026-11-11
- **Vervangt:** ADR-0016 punt 4 ("Android blijft Nederlandstalig"). Dat punt noemde en-US voor Android "een apart
  besluit"; dit is dat besluit.
- **Context:** de web-UI heeft sinds de Peil-omzetting Nederlands en en-US; de Android-app had alleen Nederlands, met
  alle teksten als letterlijke tekst in Kotlin (ruim 350 plekken: schermen, ViewModels, foutmeldingen, meldingen van de
  achtergrondtaken). De opdracht bij Δ-MG-DUAL vraagt de app ook in en-US. De bestaande Nederlandse app mag daarbij
  niet veranderen: dezelfde woorden, dezelfde 78 referentiebeelden.

## Besluiten

1. **Twee talen: Nederlands en en-US.** Nederlands blijft de standaard. Elke andere taal dan Engels valt terug op
   Nederlands (dat was al zo: de app had geen andere taal).
2. **De taal volgt Android, de app heeft geen eigen taalknop.** De app neemt de taal die Android voor de app kiest: de
   systeemtaal, of vanaf Android 13 de taal per app (Instellingen → Apps → MediaGuard → Taal). Daarvoor staat
   `android:localeConfig="@xml/locales_config"` in het manifest met `nl` en `en`. Een schakelaar in de app zou een
   nieuwe instelling met eigen opslag zijn; Android biedt de keuze al en onthoudt hem zelf.
3. **Hoe de app de gekozen taal kent.** De string-resource `app_taal` is `nl` in `res/values/` en `en` in
   `res/values-en/`. Android kiest de resource; `Context.volgAppTaal()` zet daarmee `Taal.huidig`. Dat gebeurt bij het
   starten van de app, bij elke wijziging van de configuratie (`Application.onConfigurationChanged`, waarna ook de
   namen van de meldingskanalen opnieuw worden gezet) en bij het maken van het scherm (`MainActivity.onCreate`).
   Zo geldt dezelfde taal ook voor de achtergrondtaken, die geen scherm hebben.
4. **Teksten staan naast elkaar in Kotlin: `tr("Nederlands", "English")`** (`data/Taal.kt`). Overwogen is alles naar
   `strings.xml` te verplaatsen. Daar is niet voor gekozen:
   - veel teksten ontstaan buiten een composable (ViewModels, `friendlyMessage()`, workers, enums) en hebben
     ingevoegde waarden; met resources is daar overal een `Context` en een opmaakpatroon nodig;
   - met `tr` blijft de Nederlandse tekst letterlijk op zijn plaats staan. De Nederlandse app verandert daardoor
     aantoonbaar niet (alle bestaande tests en referentiebeelden blijven ongewijzigd);
   - een tekst zonder Engelse tegenhanger compileert niet: de tweede parameter is verplicht.

   De prijs: Android-lint controleert deze teksten niet op ontbrekende vertalingen en een vertaler werkt in Kotlin.
   Voor twee talen is dat aanvaardbaar; komt er een derde taal bij, dan hoort dit besluit opnieuw op tafel.
5. **Woorden die maar één keer worden gemaakt, bewaren beide talen.** Enums en vaste lijsten (`PeilStatus`,
   `AttentionKind`, `LibraryKind`, `ActivityTab`, `RequestsTab`, `MAIN_TABS`) houden het Nederlandse en het Engelse
   woord vast en geven het woord pas bij het opvragen (`woord`, `label`). Sleutels in routes en meldingen
   (`activity?tab=gebeurd`) zijn geen tekst en blijven gelijk.
6. **Getallen en datums volgen de taal, de tijdzone niet.** Nederlands: `96,0 GB`, `4 nov 13:01`, `zaterdag 7
   november`, `nog 2u 5m`. en-US: `96.0 GB`, `Nov 4, 1:01 PM`, `Saturday, November 7`, `2h 5m left`. Tijden van
   MediaGuard blijven in Europe/Amsterdam, tijden van de telefoon in de zone van de telefoon, zoals voorheen.
7. **Woordkeuze.** De vaste statuswoorden zijn die van Peil §11.1 (Healthy · Degraded · Down · Unknown · Stale ·
   Paused · Not linked · Not available). De overige woorden zijn dezelfde als in de en-US-teksten van de web-UI
   (Overview, Activity, Library, Space, Requests, Emergency stop, Queue, Missing, Movies, Series, Discover, My
   requests, …) en de teksten die `docs/ontwerp/PEIL_MIGRATIE.md` §14 al voor Android had vastgelegd.
8. **Teksten van de server blijven Nederlands** (ADR-0016 punt 7 geldt onverkort). De app vertaalt geen tekst die
   MediaGuard aanlevert: `headline`, `message`, `*_nl` (zoals `days_left_nl`, `phase_nl`, `reason_nl`), de uitleg en
   de knoppen bij "Wat actie nodig heeft". In de Engelse app staan die dus in het Nederlands tussen Engelse
   app-teksten. Eén uitzondering: bij een Seerr-status met een bekende code (`beschikbaar`, `aangevraagd`,
   `verwerken`, `gedeeltelijk`) kiest de app zelf het Engelse woord; een onbekende code toont de tekst van de server.
   Een volledig Engelse app vraagt dat de core naast `*_nl` ook een Engelse tekst of een sleutel levert; dat is een
   wijziging van het API-contract en valt buiten dit besluit.
9. **Meldingen.** Het dagoverzicht en de directe meldingen worden opgemaakt in de taal die op dat moment geldt. De
   namen van de meldingskanalen komen uit de resources en volgen dus dezelfde keuze. Een melding die al is getoond,
   verandert niet meer.
10. **Reden bij de noodstop.** Vult de gebruiker geen reden in, dan stuurt de app de standaardtekst in de taal van de
    app ("Via de Android-app" / "Via the Android app"). Die tekst komt zo in de audit van MediaGuard.

## Gevolgen en grenzen

- Een tekst die al in een ViewModel of in een melding op het scherm staat (een foutmelding, de regel onder in beeld),
  houdt de taal van het moment waarop hij is gemaakt. Bij een taalwissel maakt Android het scherm opnieuw en haalt
  de app de gegevens opnieuw op; daarna staat alles in de nieuwe taal.
- De datumkiezer bij "Wat is er gebeurd" en de QR-scanner zijn onderdelen van Android en van de scannerbibliotheek;
  zij volgen de taal van Android zelf.
- De release-build draait de vertaalcontrole van lint: elke tekst in `res/values/strings.xml` heeft een Engelse
  tegenhanger in `res/values-en/strings.xml`, behalve `app_name` (een naam, `translatable="false"`).
- De Nederlandse referentiebeelden zijn niet opnieuw vastgelegd. Er zijn zes Engelse referentiebeelden bij gekomen
  (`EnLightScreenShots`: Overzicht, Activiteit → Problemen, Bibliotheek, Ruimte, Instellingen met waarschuwingen en
  Koppelen; licht, 393 dp). De voorbeeldgegevens daarin zijn teksten van de server en dus Nederlands (punt 8).

## Bewijs

- `TaalTest` (unit): standaardtaal, de keuze uit `app_taal`, statuswoorden en tabs in beide talen, getallen, datums,
  duur en resterende tijd, de dagregel van de melding, de Seerr-status en de baanregel.
- `:app:verifyPaparazziDebug`: 78 Nederlandse referentiebeelden ongewijzigd, 6 Engelse erbij.
- **Status:** Sim-getest (unit-tests en schermafdrukken op de JVM). **NIET GEVALIDEERD:** een echte telefoon of
  emulator, de taalkeuze per app in de instellingen van Android 13 en hoger, en TalkBack in het Engels (in de
  bouwomgeving is geen emulator beschikbaar; zie ADR-0018, Gevolgen).
