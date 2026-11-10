# Schermafdrukken "voor" — MediaGuard (web en Android) vóór de omzetting naar Peil

Nulmeting bij [`docs/ontwerp/PEIL_MIGRATIE.md`](../../../ontwerp/PEIL_MIGRATIE.md). Gemaakt op 9 november 2026 tegen de
**echte app** (code-HEAD `8060747`, MediaGuard 1.2.2): de echte core (`python -m mediaguard`, verse datamap,
`MG_START_BACKGROUND=0`) en de echte productiebuild van de web-UI (`npm run build` → `node server.mjs`), beide alleen op
127.0.0.1. Chromium van Playwright 1.65, `nl-NL` (en-US voor de `en-us-*`-afdrukken), `Europe/Amsterdam`, schaal 1.

**Testgegevens.** Sonarr/Radarr zijn de nep-API's uit de eigen coretests (`apps/core/tests/test_arr_fakes.py`, `FakeArr`),
hier als HTTP-servers gestart (`hulp/nep_arr.py`) met fictieve titels. De core heeft ze echt geïnventariseerd
(`POST /api/v1/library/sync`: 5 series, 53 afleveringen, 5 films). Twee quarantainerijen zoals `apps/web/e2e/seed.ts`
(`hulp/vul.py`). Gebruikers: `beheerder`, `kijker` (rol viewer) en `engels` (beheerder met taal en-US); het
wachtwoord stond alleen in de omgeving. Er draait **geen** AV1-engine, Seerr of recovery-worker: dat is de echte
toestand van een verse installatie en zo zie je hem in de afdrukken ("AV1-engine reageert niet", "Seerr koppelen",
"Vervangen van bestanden staat stil"). Posters laden niet (geen internet voor de nep-API's): je ziet de kapotte
afbeelding die de app dan echt toont. De instantie-adressen in de afdrukken (`http://127.0.0.1:18911` … `18914`) zijn de
nep-API's op de testmachine, geen NAS-gegevens.

| Map | Inhoud | Aantal |
|---|---|---|
| `schermen/` | 30 schermen (alle routes met een eigen pagina; de titelroute als serie én film) × 360/390/768/1440 × licht/donker, **volledige pagina** | 240 |
| `tabs/` | Activiteit (4), Koppelingen (7), Kwaliteit (2), Migratie (1), titel (4), aflevering (6) × 390/1440 × licht/donker | 96 |
| `toestanden/` | inloggen, eerste start, niet gevonden, laden, fout, offline, verouderd (Overzicht, Bibliotheek), kijker (2), en-US (3), AV1-engine actief (2), noodstop actief (3), zoeken, profielmenu, meldingenpaneel, sneltoetsen (alleen 1440), dialogen noodstop / bibliotheek vrijgeven / terugzetten / definitief verwijderen × 390/1440 × licht/donker | 102 |
| `metingen.json` | per afdruk: overloop t.o.v. de apparaatbreedte, doelen < 44 px, tekst < 14 px (histogram), lettertypes, inline styles, statuschips per toon, pauze- en vinkiconen, grafiekelementen, axe-core 4.14 (`wcag2a/aa/21a/21aa/22aa`), JS-fouten | 438 |
| `toetsenbord.json` | Tab-doorloop (40 stops) op Overzicht, Bibliotheek, Instellingen, Terugzetten, Systeem × 390/1440 × licht/donker: focusstijl, focus bedekt door kopbalk/ondernavigatie, toetsenbord op grafieken | 20 |
| `android/` | 16 schermen/toestanden × 360 dp / 393 dp / 393 dp-volledig × licht/donker (Paparazzi) + `index.json` | 96 |

Bestandsnaam web: `<scherm of toestand>-<licht|donker>-<breedte>.png`. Telefoonbreedtes (360, 390) zijn geëmuleerd
met `isMobile` + touch, 768 met touch, 1440 met muis. Android: `<scherm>-<licht|donker>-<360|393|393-volledig>.png`.

## Eerlijk over deze afdrukken

- **Nagebootste toestanden.** Laden, fout, offline, verouderd (2×), eerste start en AV1-engine actief (2×) komen niet
  vanzelf voor in de testgegevens. Daarvoor onderschept het script API-antwoorden in de browser, bijvoorbeeld
  `GET /api/v1/library*` → 500 met de foutvorm `{detail, code, correlation_id}`, of alle `/api/v1`-verzoeken behalve
  `/auth` → 502 `upstream_unreachable` (precies wat `server.mjs` stuurt als de core weg is). De AV1-engine is nagebootst
  zoals in `apps/web/e2e/studio-av1.spec.ts` (HTTP + WebSocket). Welke onderschepping per afdruk is gebruikt, staat in
  `metingen.json` onder `nagebootst`. De app zelf is niet aangepast.
- **Grenzen van de nabootsing.** (1) *Verouderd*: de onderschepping raakt alleen nieuwe verzoeken; de al open
  live-verbinding blijft open, dus "Live" blijft staan. Wat de afdruk wél laat zien: na 70 s zonder geslaagd verzoek
  ziet Overzicht er vers uit, omdat de meeste kaarten niet periodiek verversen. (2) *AV1-engine actief*: de nagebootste
  meetreeks voor `Belasting` had niet de vorm die de engine levert; die grafiek toont as en raster maar geen lijnen.
  `Besparing per dag` (14 dagen, één dag zonder meting) wordt wel echt getekend.
- **Echte noodstop.** Voor `toestanden/noodstop-*` is de noodstop via `POST /api/v1/system/emergency-stop` aangezet en
  daarna weer uitgezet (testomgeving).
- **Vaste balken in afdrukken van de volledige pagina.** Chromium tekent `position: fixed` (de ondernavigatie op
  360/390/768) één keer, op de plek van het eerste beeldscherm. Daardoor staat de ondernavigatie in die afdrukken
  midden in de pagina. In de app staat hij altijd onderaan.
- **Overloop op de telefoon.** Met `isMobile` rekt Chromium het layout-viewport op tot de breedste inhoud (zoals een
  telefoon die uitzoomt). `scrollWidth − innerWidth` is dan altijd 0, ook in de bestaande e2e-tests. Daarom meet
  `metingen.json` de overloop **t.o.v. de apparaatbreedte** (`overloop_apparaat_px`).
- **Kleuren.** De PNG's zijn na het maken omgezet naar een 256-kleurenpalet (± 3× kleiner, `verklein.py`; Android door
  `hulp/android_verwerk.py`). Vlakken en tekst zien er gelijk uit, maar meet **nooit** contrast in deze afdrukken;
  contrast komt uit de browser (axe) en uit `contrast.json` van Peil.
- **Android.** Paparazzi 1.3.5 in een **kopie van `apps/android` buiten de repo**, met drie extra apparaatmaten in
  `shots/ScreenShots.kt` (`hulp/android-afdrukken.patch`): 393 dp (Pixel 5), 360 dp (990 px bij 440 dpi) en 393 dp met
  een viewport van 2 618 dp hoog ("volledig"; de lege ruimte boven de ondernavigatie is weggesneden). Op device-
  resolutie gerenderd en geschaald naar 2 px per dp. Echte composables met de bestaande `SampleData.kt` (verzonnen
  titels in de vorm van echte API-antwoorden), posters leeg. Het koppelscherm heeft geen screenshot-test en ontbreekt.
  **NIET GEVALIDEERD (hardware):** geen emulator, geen echte telefoon, geen TalkBack.
- **Niet gedaan:** echte schermlezer (NVDA/TalkBack), 200 %-zoom, forced colors, tv/speler, echte NAS.

**Android-afdrukken opnieuw gemaakt op 10 november 2026** voor de openbare repository, met stap 3 hieronder op dezelfde
code-HEAD: alleen de voorbeeldgebruiker (`jan`, initialen `JV`) en de voorbeeldindexer (`IndexerNL`) zijn anders dan in de
eerste set.

**Achteraf bijgewerkt op 10 november 2026** voor de openbare repository. De webafdrukken zijn niet opnieuw gemaakt,
maar een paar tekstregels zijn in de PNG's opnieuw getekend in hetzelfde lettertype; de rest van elke afdruk is
ongewijzigd. Het ging om:
- Systeem: het pad van de werkruimte op de testmachine is nu `/config` (de standaard van `MG_DATA_DIR`);
- Koppelingen › Mediaserver: het voorbeeldadres is nu `nas.lan` (stond op een echt LAN-adres);
- Release Lab: de placeholder noemt nu "Lichtval";
- Anime › Releasenaam ontleden: de placeholder noemt nu "Golfbreker".

## Opnieuw maken

```sh
# 0. web bouwen en een Python 3.12-venv met de core + testextra's (buiten de repo)
(cd apps/web && npm ci && nice -n 10 npm run build)
python3.12 -m venv /pad/naar/venv && /pad/naar/venv/bin/pip install -e 'apps/core[test]'
# 1. nep-arr + core + web starten (alleen 127.0.0.1) en vullen; wachtwoord alleen in de omgeving
export MGP_REPO=$PWD MGP_WERK=/pad/naar/werkmap MGP_PY=/pad/naar/venv/bin/python MGP_PW="$(openssl rand -hex 16)"
docs/screenshots/peil/voor/hulp/start.sh
MGP_DATA=$MGP_WERK/data $MGP_PY -I docs/screenshots/peil/voor/hulp/vul.py
# 2. afdrukken + nulmeting + Tab-doorloop
export MG_PEIL_BASE_URL=http://127.0.0.1:18200 MG_PEIL_PASSWORD="$MGP_PW"
export PEIL_PLAYWRIGHT=/pad/naar/node_modules/playwright/index.mjs PEIL_AXE=$PWD/apps/web/node_modules/axe-core/axe.min.js
node docs/screenshots/peil/voor/maak-afdrukken.mjs alles      # of: schermen | tabs | toestanden | noodstop; PEIL_ALLEEN=<regex>
node docs/screenshots/peil/voor/toetsenbord.mjs
python3 docs/screenshots/peil/voor/verklein.py                 # Pillow
docs/screenshots/peil/voor/hulp/stop.sh
# 3. Android (kopie buiten de repo; Gradle machinebreed achter het slot)
rsync -a --exclude build --exclude .gradle apps/android /pad/naar/kopie/apps/ && cp -r deploy /pad/naar/kopie/
(cd /pad/naar/kopie && patch -p1 < "$MGP_REPO/docs/screenshots/peil/voor/hulp/android-afdrukken.patch")
(cd /pad/naar/kopie/apps/android && ANDROID_HOME=$HOME/android-sdk JAVA_HOME=/usr/lib/jvm/java-17-openjdk-amd64 \
  flock <WERKMAP>/.lokaal/gradle.lock nice -n 10 ./gradlew --no-daemon --max-workers=4 \
  -Dorg.gradle.jvmargs=-Xmx2g :app:recordPaparazziDebug)
python3 docs/screenshots/peil/voor/hulp/android_verwerk.py /pad/naar/kopie/apps/android/app/src/test/snapshots/images docs/screenshots/peil/voor/android
```

De scripts zijn alleen-lezen hulpmiddelen voor deze documentatie; ze horen niet bij de app en worden niet gebouwd.
