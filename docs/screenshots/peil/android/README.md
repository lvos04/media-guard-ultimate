# Schermafdrukken "na" — MediaGuard Android in Peil 1.1.0

Bij [`docs/ontwerp/PEIL_MIGRATIE.md`](../../../ontwerp/PEIL_MIGRATIE.md) §10 en §14 en
[ADR-0018](../../../adr/ADR-0018-peil-android-bouwkeuzes.md). Gemaakt op 9 november 2026 met Paparazzi 1.3.5 uit de
**echte composables** van `apps/android` (schermen + de echte bovenbalk en ondernavigatie), met de bestaande
voorbeeldgegevens (`app/src/test/java/nl/mediaguard/app/shots/SampleData.kt`: verzonnen titels in de vorm van echte
API-antwoorden; geen NAS-, apparaat- of accountgegevens; de adressen `mediaguard.thuis.lan`/`mediaguard.example.nl`
zijn voorbeeldwaarden van de test, geen echte). Posters zijn leeg (geen netwerk). Zelfde namen als de nulmeting in
[`../voor/android/`](../voor/android/), zodat je vóór en na naast elkaar kunt leggen.

| Variant | Bestandsnaam | Aantal |
|---|---|---|
| 393 dp (Pixel 5), licht/donker | `<scherm>-<licht\|donker>-393.png` | 36 |
| 360 dp, licht/donker | `<scherm>-<licht\|donker>-360.png` | 36 |
| 393 dp, hele scherm in één afdruk (2 618 dp hoog, lege ruimte boven de ondernavigatie weggesneden) | `<scherm>-<licht\|donker>-393-volledig.png` | 36 |
| 393 dp, grote letter (systeemletter 1,3×), licht, zes schermen | `<scherm>-licht-393-grote-letter.png` | 6 |
| `index.json` | per afdruk de test, breedte, letterschaal en pixelmaat | 114 |

Schermen: `overzicht` (3 dingen wachten), `overzicht-alles-in-orde`, `overzicht-noodstop`, `overzicht-verouderd`
(niet bijgewerkt + meldingen uit + update), `activiteit-nu`, `activiteit-problemen`, `activiteit-wat-is-er-gebeurd`,
`bibliotheek`, `bibliotheek-detail`, `bibliotheek-ontbrekend`, `ruimte`, `aanvragen-ontdekken`, `aanvragen-mijn`,
`aanvraagvenster`, `instellingen`, `instellingen-waarschuwingen` en **nieuw** `koppelen` en `koppelen-geweigerd` (het
koppelscherm had geen afdruk; daarvoor is het opgesplitst in `PairingScreen` + `PairingContent`, zonder
gedragswijziging).

## Wat je ziet (vóór → na)

| Onderwerp | Vóór (nulmeting) | Na |
|---|---|---|
| Kleuren | eigen `MgColors` uit `mg.css` | `PeilTokens.kt`, gegenereerd uit `design/ontwerptaal/tokens.json` (sha256 `9583506d…a30e1`), licht/donker |
| Lettertype en maten | Inter (ingebouwd), 11–24 sp, kapitalen-koppen, `tnum` overal | systeemfont (Roboto), kleinste tekst 14 sp, body 16, pagina 24, waarde 30 proportioneel; geen kapitalen |
| Status | gekleurde stip + woord, één regel met ellips | icoon + vast woord + vorm (pil, 2 dp-rand; gestippeld voor Onbekend/Verouderd, gearceerd voor Verouderd), loopt om |
| Groen | Compleet, Uit (noodstop), Alles in orde, geleverd, downloads | alleen "Alles gezond"/Gezond; beschikbaarheid, geleverd en noodstop-uit zijn neutrale labels |
| Noodstop aan | rode kaart "Noodstop staat aan" | Gepauzeerd-vorm "Noodstop actief" (`circle-pause`, info) |
| Verouderd | amber kaart, tegels zien er vers uit | statuspaneel Verouderd (gestippeld) en tegels gestippeld met "Laatst bekend · …" op een gearceerd vak |
| Wacht op jou | `WarningAmber`/`ErrorOutline` in statuskleur | "Wat actie nodig heeft": `octagon-x` (storing), `triangle-alert`, `lock` (voorwaarde: "Dit is geen storing; er wacht een voorwaarde."), `info` |
| Banen | GPU violet, CPU blauw, percentage in baankleur | label met `microchip`/`cpu` in `domain-lane-gpu/cpu`, tekst en percentage in inkt |
| Waar ging de ruimte heen | blauw / violet / amber | `chart-1` / `chart-2` / `chart-other` met arcering; nog niet gemeten = Onbekend (geen balk van 0) |
| Filterchips | horizontaal scrollend, laatste half zichtbaar | tabs van 48 dp die omlopen; gekozen = inktvlak + vinkje |
| Knoppen en doelen | 40/32 dp | minstens 48 dp (knoppen, tabs, schakelaars, icoonknoppen, lijstregels) |
| Iconen | 42 Material-iconen (`material-icons-extended`) | 55 lucide-iconen als vector drawables (dezelfde als de web), bibliotheek weg |
| Bovenbalk op 360 dp / grote letter | — | eerst valt de merknaam weg (logo blijft), dan het woord "Aandacht"; niets breekt midden in een woord |

**Opnieuw gemaakt op 10 november 2026** voor de openbare repository, met dezelfde commando's als hieronder: alleen de
voorbeeldgebruiker (`jan`, initialen `JV`) en de voorbeeldindexer (`IndexerNL`) in `SampleData.kt`/`ScreenShots.kt` zijn
anders dan in de eerste set. Schermen zonder die gegevens zijn byte-gelijk; door de omzetting naar 256 kleuren kunnen de
andere afdrukken ook buiten die plekken op pixelniveau afwijken.

## Opnieuw maken

```sh
cd apps/android
export ANDROID_HOME=$HOME/android-sdk JAVA_HOME=/usr/lib/jvm/java-17-openjdk-amd64
PEIL_AFDRUKKEN=1 flock <WERKMAP>/.lokaal/gradle.lock nice -n 10 ./gradlew --no-daemon --max-workers=4 \
  -Dorg.gradle.jvmargs=-Xmx2g :app:recordPaparazziDebug
cd ../.. && python3 -I docs/screenshots/peil/android/verwerk.py apps/android/app/src/test/snapshots/images docs/screenshots/peil/android
```

`verwerk.py` haalt de documentatie-afdrukken (klassen `Docs*`, 2 px per dp) uit de snapshotmap, snijdt de
"volledig"-afdrukken bij, zet ze om naar 256 kleuren en schrijft `index.json`. De referentieset van de test
(`apps/android/app/src/test/snapshots/images/`, 78 afdrukken) blijft staan.

## Eerlijk over deze afdrukken

- **Kleuren.** De PNG's hier zijn omgezet naar 256 kleuren. Meet **nooit** contrast in deze afdrukken; contrast komt
  uit `PeilContrastTest` (alle kleurparen die de schermen tekenen, licht en donker) en uit `contrast.json` van Peil.
- **Gerenderd, niet op een toestel.** Paparazzi tekent met layoutlib op de JVM. **NIET GEVALIDEERD (hardware):** geen
  emulator, geen echte telefoon, geen TalkBack, geen fel zonlicht. Een rooktest op een headless emulator was in deze
  sessie niet mogelijk: de bouwomgeving had geen toegang tot `/dev/kvm`. Met KVM-toegang kan het wel (zie ADR-0018,
  Gevolgen).
- **Tijden.** De afdrukken draaien in UTC (zoals de CI-runner); de datum van Overzicht staat vast op 8 november 2026.
