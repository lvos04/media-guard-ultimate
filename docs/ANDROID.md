# MediaGuard voor Android

Een eigen Android-app (map `apps/android`, Kotlin + Jetpack Compose) met de belangrijkste
overzichten en bediening van MediaGuard. Geen Google Play Services of Firebase nodig: de app
werkt ook op toestellen zonder Google en wordt buiten de Play Store geïnstalleerd.

## Wat de app doet

De app volgt de ontwerptaal **Peil 1.1.0** van het platform, net als de web-UI (docs/ontwerp/PEIL_MIGRATIE.md §10,
ADR-0018): kleuren uit `design/ontwerptaal/tokens.json` (licht/donker volgt het systeem), het systeemlettertype
(Roboto, niets kleiner dan 14 sp), vlakke kaarten met een rand, status altijd als icoon + woord + vorm (nooit alleen
kleur; groen alleen voor gemeten gezondheid), dezelfde lucide-iconen als de web, aanraakdoelen van minstens 48 dp en
dezelfde vijf onderdelen in de onderste balk. Bovenin: zoeken, de aandacht-teller en je initialen (opent
Instellingen). Schermafdrukken: `docs/screenshots/peil/android/` (vóór Peil: `docs/screenshots/peil/voor/android/`).

| Onderdeel | Inhoud |
|---|---|
| **Overzicht** | Statuspaneel met de totaalstatus: *Noodstop actief* (Gepauzeerd), *Niet bijgewerkt sinds 14:02* (Verouderd: MediaGuard antwoordt niet; je ziet de laatst bekende stand, ook de tegels staan dan gestippeld met “Laatst bekend”), “2 dingen wachten op jou” (Beperkt bij een storing of waarschuwing) of “Alles loopt — niets nodig van jou” (Alles gezond) — in die volgorde, dus nooit “Alles gezond” tijdens een noodstop of met oude gegevens. Tegels *Bespaard met AV1*, *Nu bezig*, *Ontbrekend* (opent de bibliotheek gefilterd op ontbrekend) en *Vrije ruimte*, *Laatste 24 uur* (rollend venster van de server) en *Wat actie nodig heeft* (icoon per soort: storing, waarschuwing, voorwaarde met slot, info) met de uitleg van MediaGuard en dezelfde knoppen als de web, inclusief problemen met de AV1-engine. Waarschuwing als meldingen of de batterij-uitzondering uit staan, en als er een nieuwere server- of app-versie is. |
| **Activiteit** | Tabs (lopen om) *Nu* (lopende encodes per baan en downloads met voortgang), *Problemen* (alle punten met uitleg, knoppen en de bestanden per punt), *Downloads*, *Wat is er gebeurd* (het dagoverzicht van `/api/v1/app/summary` met datumkiezer: AV1-vervangingen met VMAF, downloads, waarom er geüpgraded is, aanvragen, netto opslag) en *Geschiedenis* (de afgelopen 7 dagen). |
| **Bibliotheek** | Series · Anime · Films, zoeken, neutrale labels (compleet, ontbreekt, kan beter, verwacht, niet gevolgd; beschikbaarheid is geen gezondheid); vanaf de tegel *Ontbrekend* alleen de titels die iets missen (alle soorten, *Alles tonen* heft het filter op). Detail: seizoenen aan/uit (aanzetten vraagt *Volgen en meteen zoeken* of *Alleen volgen*), het kwaliteitspaneel en de upgrade-tijdlijn. |
| **Ruimte** | Noodstop aan/uit (met bevestiging en reden), tegels, *Encodes nu* met banen (“2 van 4 bezig”, eerstvolgende klaar over …; pauzeren met bevestiging / hervatten) en voortgang met ETA, wachtrij met *Autopilot nu*, en *Waar ging de ruimte heen* over de laatste 24 uur, met *Niet verklaard* uit de afstemming van het opslaglogboek (*Onbekend* zolang er nog niet gemeten is). |
| **Aanvragen** | Seerr: zoeken of populair nu als posterraster, aanvraagvenster met seizoenschakelaars, en *Mijn aanvragen* met de leverstatus van MediaGuard. |
| **Instellingen** | Meldingen op deze telefoon (waarschuwing + knop als meldingen of de batterij-uitzondering uit staan), dagoverzicht en tijdstip (met het volgende moment), ntfy met *Laatst opgehaald*, de laatste fout en *Testmelding*, koppeling (laatste contact; ontkoppelen met bevestiging), over (app- en serverversie, nieuwere versie). |

Meldingen:
- **Dagoverzicht** elke dag op het gekozen tijdstip (standaard 08:00), bv.
  *12 vervangen · 37 gedownload · 3 aangevraagd · AV1 bespaarde 96 GB*. Tikken opent *Activiteit → Wat is er gebeurd*.
  Het is een eenmalige taak met een startvertraging tot dat tijdstip (lokale kloktijd, ook rond de
  zomertijd); na het versturen plant de taak de volgende dag. Een nieuw tijdstip vervangt de geplande
  taak meteen. Lukt ophalen niet, dan probeert de app het een kwartier lang opnieuw; komt de taak meer
  dan 4 uur te laat (telefoon uit), dan slaat de app die dag over in plaats van ’s nachts te melden.
- **Directe meldingen** via ntfy: de koppelcode neemt altijd het telefoonkanaal mee (MediaGuard maakt
  het zo nodig aan). Na het koppelen meldt de app zich (`POST /app/paired`) en zet MediaGuard dat
  kanaal aan, dus fouten komen meteen naar de telefoon, niet alleen in het dagoverzicht. Lukt dat
  meteen niet (geen verbinding), dan probeert de app het opnieuw bij de volgende start of het volgende
  contact, hooguit eens per kwartier; een app die al gekoppeld was vóór deze versie doet dat ook één
  keer. Staat het kanaal in MediaGuard uit (bv. in de web uitgezet), dan zegt *Instellingen* dat en zet
  *Aanzetten in MediaGuard* het weer aan. De app kijkt
  elke 15 minuten naar nieuwe berichten (Android staat zonder Firebase geen kortere achtergrondinterval
  toe). Wil je ze echt direct, installeer dan ook de ntfy-app en abonneer op hetzelfde kanaal.
  *Instellingen → Testmelding* laat MediaGuard meteen een testbericht sturen; de app haalt het binnen
  enkele seconden op. Lukt dat niet, dan toont de app een lokale testmelding met de reden.
- **Geen contact**: antwoordt MediaGuard 3 uur niet (core plat, tunnel weg), dan meldt de app dat zelf
  één keer (“Geen contact sinds 14:02”). Zodra MediaGuard weer antwoordt, verdwijnt die melding. Met
  een thuisadres (http:// of een privé-IP) telt onderweg op mobiele data niet mee: dan is MediaGuard
  bewust onbereikbaar. Op wifi, ethernet of VPN wel.
- **Koppel opnieuw**: weigert MediaGuard de sleutel (ingetrokken of verlopen), dan stopt de app met
  pollen, meldt dat en toont het koppelscherm.

## Installeren

1. Open de nieuwste release op GitHub (`lvos04/media-guard-ultimate` → Releases) en download
   `mediaguard-android-<versie>.apk`.
2. Sta op je telefoon "Installeren uit onbekende bronnen" toe voor je browser of bestandsbeheer
   en open de APK.

Automatisch bijwerken: installeer **Obtainium**, voeg `https://github.com/lvos04/media-guard-ultimate`
toe en kies als APK-filter `mediaguard-android-.*\.apk`. Obtainium meldt elke nieuwe versie.
De app zelf toont ook wanneer er iets nieuws is: draait de server een nieuwere MediaGuard-versie dan
de app (ze hebben hetzelfde versienummer), of meldt de updatecontrole van de server
(`GET /api/v1/system/update`, als die er is) een nieuwere release. Verberg je die versie in de web
(*Verbergen tot de volgende versie*), dan zwijgt de app er ook over; staat hij al in *Wacht op jou*,
dan toont Overzicht geen tweede banner.

> Ondertekening: zijn de secrets `ANDROID_KEYSTORE_BASE64`, `ANDROID_KEYSTORE_PASSWORD`,
> `ANDROID_KEY_ALIAS` en `ANDROID_KEY_PASSWORD` in de GitHub-repo ingesteld, dan heeft elke
> release jouw eigen sleutel. Zonder die secrets is de APK ondertekend met de vaste sideload-sleutel
> in `apps/android/signing/` (op verzoek van de eigenaar bewust niet geheim): updates werken dan ook
> gewoon, maar de repo is openbaar, dus iedereen kan een APK met dezelfde handtekening maken —
> installeer de app dus alleen vanaf de eigen GitHub-releases (zie [`SECURITY.md`](../SECURITY.md)). Wissel je later naar een eigen
> sleutel, dan moet de app één keer opnieuw geïnstalleerd worden. Eigen sleutel maken (eenmalig):
> `keytool -genkeypair -v -keystore mediaguard.jks -alias mediaguard -keyalg RSA -keysize 4096 -validity 10000`
> en daarna `base64 -w0 mediaguard.jks` als `ANDROID_KEYSTORE_BASE64`.

## Koppelen

1. Open MediaGuard in je browser → **Instellingen → Koppelingen → Android-app koppelen**. Daar
   staat een QR-code met het adres (Instelling *Adres voor de app*, bv. je Cloudflare-tunnel) en
   een nieuwe API-sleutel `android-app` met rol **operator**.
2. Open de app en tik **QR-code scannen**. Of vul het adres en een API-sleutel (`mgk_…`) met de
   hand in.

De app test de verbinding (`/api/v1/auth/me`), bewaart de sleutel versleuteld (Android Keystore,
EncryptedSharedPreferences) en stuurt hem alleen als `X-Api-Key` naar jouw MediaGuard. Intrekken
kan op elk moment in MediaGuard (API-sleutels, of een nieuwe koppelcode maken). Bij de eerste
weigering (401) stopt de app met pollen en toont hij het koppelscherm met de reden; daar scan je de
nieuwe code, zonder de app af te sluiten. Een ingetrokken sleutel telt in MediaGuard niet mee voor de
blokkade bij ongeldige sleutels, dus de oude sleutel kan de nieuwe niet laten blokkeren. *Opgeslagen
sleutel opnieuw proberen* helpt als de weigering tijdelijk was.

Een LAN-adres zonder TLS (`http://192.168.x.x:30080`) werkt thuis; onderweg is een
https-adres (tunnel) nodig. De app stuurt http:// alleen naar adressen in je eigen netwerk (LAN,
VPN/Tailscale, deze telefoon). Wijst een http-adres naar internet, dan weigert de app het verzoek
vóór de sleutel verstuurd wordt, ook na een doorverwijzing naar een ander adres; bij het koppelen
probeert hij dan automatisch hetzelfde adres met https://.

**Beschadigde sleutelopslag**: na een systeemupdate of het overzetten naar een nieuwe telefoon kan de
versleutelde opslag onleesbaar zijn. De app crasht dan niet meer: hij wist de opslag, toont dat op
het koppelscherm en je koppelt opnieuw. Back-ups en overzetten naar een andere telefoon nemen de
koppeling bewust niet mee.

## Rechten

| Recht | Waarvoor |
|---|---|
| Internet | praten met MediaGuard (en optioneel ntfy) |
| Camera | alleen om de koppelcode te scannen |
| Meldingen (Android 13+) | dagoverzicht, directe meldingen, “geen contact” en “koppel opnieuw” |
| Batterij-uitzondering vragen | zodat Android het ophalen van meldingen niet uren uitstelt (de app vraagt het via Instellingen) |

## Bouwen

```
cd apps/android
./gradlew :app:verifyPaparazziDebug :app:assembleDebug  # unit-tests + schermafdrukken vergelijken; APK: app/build/outputs/apk/debug/
./gradlew :app:recordPaparazziDebug                     # referentie-afdrukken opnieuw vastleggen (na een bewuste wijziging)
MG_ANDROID_UNSIGNED=1 ./gradlew :app:assembleRelease    # niet-ondertekende release-build (app-release-unsigned.apk)
python3 tools/peil_tokens.py [--check]                  # PeilTokens.kt uit design/ontwerptaal/tokens.json
node tools/lucide_naar_vector.mjs                       # lucide-iconen → res/drawable (na npm ci in apps/web)
```
Vereist JDK 17+ en de Android SDK (platform 35). De versie van de app is de MediaGuard-versie uit
`deploy/release-manifest.json`. CI: `.github/workflows/android.yml` controleert bij elke push die de app raakt
de Peil-tokens, draait de unit-tests en vergelijkt de schermafdrukken (`verifyPaparazziDebug`), bouwt debug en
release (artifact) en hangt de APK aan de GitHub-release zodra de workflow *release* klaar is. Schermafdrukken
voor de documentatie: `docs/screenshots/peil/android/README.md`. Zonder `MG_ANDROID_UNSIGNED` verandert de
ondertekening niet (eigen sleutel via secrets, anders de vaste sideload-sleutel).

Versienummers: `versionCode` = versie × 100 + bouwnummer. Het bouwnummer (0–99) is het aantal
commits sinds de versie in het manifest veranderde (`scripts/android-build-number.sh`). Een
handmatige build (*Run workflow*) van een latere commit heet dan bv. `1.2.1+3` en is voor Android
en Obtainium een echte update; de release-build heet gewoon `1.2.1`. Elke volgende versie ligt
altijd hoger dan alle builds van de vorige.

API-contract: `docs/APP_API.md`.
