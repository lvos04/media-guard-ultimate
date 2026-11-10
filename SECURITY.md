# Beveiligingsbeleid

De technische beveiligingsreferentie (dreigingsmodel, authenticatie, geheimen, containerisolatie)
staat in [`docs/SECURITY.md`](docs/SECURITY.md). Dit bestand gaat over twee dingen: hoe je een
kwetsbaarheid meldt, en een bewuste keuze rond de Android-app die je moet kennen voordat je die
installeert.

## Een kwetsbaarheid melden

- Meld een kwetsbaarheid **niet** in een openbare issue. Gebruik op GitHub **Security → Report a
  vulnerability** (een privé-melding aan de beheerder). Staat die knop er niet, open dan een issue
  zónder details en vraag om contact.
- Vermeld de versie (Instellingen → Systeem, of `deploy/release-manifest.json`), wat je deed, wat
  er gebeurde en wat de gevolgen kunnen zijn. Stuur geen echte API-sleutels, wachtwoorden of
  gegevens uit je eigen bibliotheek mee.
- Dit is een hobbyproject van één persoon. Er is geen vaste reactietermijn; meldingen worden wel
  gelezen en een bevestigde kwetsbaarheid krijgt een oplossing in de eerstvolgende release.

Ondersteund wordt alleen de nieuwste release. Oudere versies krijgen geen beveiligingsupdates.

## Bewuste keuze: de sideload-sleutel van de Android-app is openbaar

**Wat.** In deze repository staat een Android-ondertekeningssleutel met wachtwoord:
[`apps/android/signing/mediaguard-sideload.keystore`](apps/android/signing/mediaguard-sideload.keystore),
met het wachtwoord en de alias in `apps/android/app/build.gradle.kts`. Zonder de GitHub-secrets
`ANDROID_KEYSTORE_*` ondertekent de workflow `android.yml` elke APK met deze sleutel. Zolang die secrets
niet zijn gezet, geldt dat ook voor de officiële release-APK's (de eerdere releases 1.2.1 en 1.2.2 waren er
ook mee ondertekend).

| | |
|---|---|
| Alias | `mediaguard` |
| Eigenaar (certificaat) | `CN=MediaGuard Ultimate (sideload), O=MediaGuard` |
| SHA-256 van het certificaat | `51:A3:48:7A:F9:DC:35:84:1B:F8:0E:6F:5E:C0:85:7C:00:BC:F4:B7:4B:2B:D3:15:AE:4F:63:32:4B:FB:97:8E` |

**Waarom.** Elke build heeft zo dezelfde handtekening. Updates via GitHub-releases of Obtainium
werken daardoor zonder de app opnieuw te installeren, ook bij een build die je zelf maakt. De
eigenaar van dit project heeft daar bewust voor gekozen.

**Het risico, eerlijk gezegd.** Omdat de sleutel openbaar is, kan **iedereen** een APK maken die
Android accepteert als update van een geïnstalleerde MediaGuard-app. Zo'n kwaadaardige "update"
erft de gegevens van de app, waaronder het serveradres en de API-sleutel waarmee de app jouw
MediaGuard-server bedient (rol operator). Iemand die jou zo'n APK laat installeren, kan daarmee
je MediaGuard-server bedienen. De handtekening zegt bij deze app dus **niets** over wie de APK
heeft gemaakt. Het certificaat hierboven bewijst alleen dat de APK met deze openbare sleutel is
ondertekend.

**Wat je kunt doen.**

- Installeer de app en updates **alleen** vanaf de releases van deze repository (rechtstreeks, of
  met Obtainium dat naar deze repository wijst). Installeer nooit een MediaGuard-APK die je ergens
  anders vandaan krijgt.
- Wil je dit risico niet: maak een eigen sleutel en zet die als GitHub-secrets in je eigen fork
  (`ANDROID_KEYSTORE_BASE64`, `ANDROID_KEYSTORE_PASSWORD`, `ANDROID_KEY_ALIAS`,
  `ANDROID_KEY_PASSWORD`; zie [`docs/ANDROID.md`](docs/ANDROID.md)), of bouw de app zelf met een
  eigen sleutel. Een andere handtekening is voor Android geen update: je installeert de app dan één
  keer opnieuw en koppelt hem opnieuw.
- Vermoed je dat een vreemde APK is geïnstalleerd: verwijder de app, trek in MediaGuard de API-sleutel
  `android-app` in (API-sleutels) en koppel de officiële app opnieuw.

Dit risico is bekend en geaccepteerd. Het is geen kwetsbaarheid om te melden, tenzij je een manier
vindt waarop het verder gaat dan hierboven beschreven.
