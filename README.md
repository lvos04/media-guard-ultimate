# MediaGuard Ultimate

[![Linux-CI](https://git.medialeon.nl/leon/media-guard-ultimate/badges/workflows/ci.yml/badge.svg)](https://git.medialeon.nl/leon/media-guard-ultimate/actions?workflow=ci.yml)

Eén TrueNAS SCALE-app met een Nederlandstalig dashboard die je bestaande Sonarr/Radarr
vervangt door vier beheerde instanties (eigen forks), ontbrekende afleveringen actief
terugzoekt, iedere download controleert (juiste aflevering, Dual Audio, Nederlandse
ondertitels) en je bibliotheek veilig naar AV1 omzet met behoud van HDR10, HDR10+ en
Dolby Vision. Alles bedien je vanuit de web-UI; je hoeft Sonarr/Radarr zelf niet meer te
openen.

> **Releasevoorbereiding 1.3.0.** Voor een bestaande installatie staan de keuze van de image, opslag en terugweg in [Een bestaande MediaGuard bijwerken](deploy/truenas/BIJWERKEN.md).

> **Eerdere release 1.2.2** — gebouwd en getest in containers (zie [Testresultaten](#8-testen)) én in zes
> testrondes op een echte TrueNAS SCALE-installatie met Intel Arc A310, Emby en Sonarr/Radarr
> (die rapporten bevatten gegevens van een echte installatie en staan daarom niet in deze
> repo). De overstap van je huidige Sonarr/Radarr (migratie → overschakelen) gebeurt alleen na
> jouw bevestiging; loop vóór die stap de [controlelijst voor je eigen systeem](#11-controlelijst-op-je-eigen-truenas) door.

> De broncode en CI staan in de [privérepository op Forgejo](https://git.medialeon.nl/leon/media-guard-ultimate).
> De historie begint in november 2026 bij versie 1.2.2 plus de Peil-ronde. De ontwikkelhistorie van vóór
> deze versie (commits, tags `v0.1.0`–`v1.2.2`, release-branches) staat in een privé-archief, omdat
> die testrapporten en testgegevens van een echte installatie bevat. Commit-hashes die in
> `CHANGELOG.md` en `docs/` worden genoemd, verwijzen naar dat archief. Licentie: GPL-3.0
> ([`LICENSE`](LICENSE)); beveiliging en de bewust openbare sideload-sleutel van de Android-app:
> [`SECURITY.md`](SECURITY.md).

---

## Inhoud

1. [Wat zit erin](#1-wat-zit-erin)
2. [Vereisten](#2-vereisten)
3. [Installatie op TrueNAS SCALE](#3-installatie-op-truenas-scale)
4. [Eerste start](#4-eerste-start)
5. [Je vier bestaande Sonarr/Radarr overzetten](#5-je-vier-bestaande-sonarrradarr-overzetten)
6. [Seerr koppelen](#6-seerr-koppelen)
7. [Dagelijks gebruik](#7-dagelijks-gebruik)
8. [Testen](#8-testen)
9. [Lokaal starten (ontwikkeling)](#9-lokaal-starten-ontwikkeling)
10. [Back-ups, updates en terugdraaien](#10-back-ups-updates-en-terugdraaien)
11. [Controlelijst op je eigen TrueNAS](#11-controlelijst-op-je-eigen-truenas)
12. [Problemen oplossen](#12-problemen-oplossen)
13. [Documentatie en licenties](#13-documentatie-en-licenties)

---

## 1. Wat zit erin

| Container | Rol |
|---|---|
| `mediaguard-web` | Nederlandstalige webinterface (Next.js), de enige poort die je opent (30080) |
| `mediaguard-core` | API, login, beleid, migratie, bijzoeken, Seerr, AI-adviseur, inrichting |
| `mediaguard-recovery` | De enige die bestanden in je bibliotheek vervangt (quarantaine, herstel) |
| `mediaguard-encode` | AV1-engine uit [`lvos04/av1`](https://github.com/lvos04/av1), naar TrueNAS geporteerd |
| `mediaguard-sonarr-series` / `-sonarr-anime` | Sonarr-fork (v4.0.20), twee instanties |
| `mediaguard-radarr-films` / `-radarr-animefilms` | Radarr-fork (v6.4.4), twee instanties |

**Belangrijkste functies**

- **Vier gescheiden instanties**: elk met eigen database, map, profielen en downloadcategorie.
- **Nieuwe afleveringen blijven binnenkomen**: RSS van de forks, en daarnaast een **bijzoek-planner**
  (backfill) per instantie met limieten, zodat ontbrekende afleveringen periodiek opnieuw worden
  gezocht. Per ontbrekende aflevering zie je de **concrete oorzaak** (niet uitgezonden, geen
  indexer-resultaat, afgewezen op taal, import mislukt, …).
- **Controle vóór downloaden en vóór importeren** (forkpatch): verkeerde aflevering, valse
  "Dual Audio"-claims, taalvoorkeur. Anime: **Dual Audio > Engels > Japans**; Nederlands
  instelbaar voor audio en ondertitels.
- **Anime-routering**: anime die via Seerr in Series/Films belandt, gaat automatisch naar de
  anime-instantie.
- **Automatische inrichting**: MediaGuard richt de vier instanties, Prowlarr en Seerr zelf in en
  bewaakt ze.
- **AV1 met slimme bitrate**: proefencodes per titel; alleen omzetten als het echt ruimte
  bespaart. **Dolby Vision** (8.1/8.4, 7 MEL; 7 FEL alleen experimenteel apart) en **HDR10+**
  worden behouden en per beeld gecontroleerd; lukt dat niet, dan blijft het origineel.
- **Veilig vervangen**: origineel eerst in quarantaine, nieuw bestand pas na verificatie actief,
  herstel na stroomuitval, originelen pas weg na een **vrijgavecontrole** (standaard 7 dagen,
  max. 500 GB, of "direct na controle").
- **AI-adviseur** (optioneel, Claude of OpenAI): alleen bij twijfelgevallen, standaard uit,
  budget € 5/maand, nooit beslissend over bestanden.

---

## 2. Vereisten

- **TrueNAS SCALE 24.10.2.2 of nieuwer** met Apps (Docker) en ix-app 1.4.8 of nieuwer.
- Een **pool** voor data en een paar datasets (zie 3.1).
- Je **downloadclient** (SABnzbd, NZBGet, qBittorrent, …) die bij `/data/downloads` kan
  (zelfde dataset als je media, voor hardlinks), of een remote path mapping.
- Je **indexers** of **Prowlarr**.
- Optioneel: **Seerr**, een **Intel Arc**-GPU (A310) voor snelle AV1 (CPU werkt ook),
  een Claude- of OpenAI-**API-sleutel**.
- Je huidige Sonarr/Radarr **blijven draaien** tot je bewust overstapt.

---

## 3. Installatie op TrueNAS SCALE

### 3.1 Datasets aanmaken (eenmalig, TrueNAS-UI → Datasets)

Voorbeeldindeling voor nieuwe datasets (`<pool>` = de naam van jouw pool in **Datasets**):

```text
<pool>/apps/mediaguard        ← configuratie (/config)
<pool>/mediaguard-work        ← encode-cache, staging, quarantaine (/work)
<pool>/data                   ← downloads + media samen (/data)
   ├─ media/tv                  Sonarr Series
   ├─ media/anime               Sonarr Anime
   ├─ media/movies              Radarr Films
   ├─ media/animefilms          Radarr Animefilms
   └─ downloads/{tv,tv-anime,movies,movies-anime}
```

- Geef gebruiker/groep **`apps` (568:568)** rechten via de **dataset-ACL** (Lezen+Schrijven op
  `apps/mediaguard`, `mediaguard-work` en `data`). **Geen** massale `chown -R` over je bestaande
  bibliotheek nodig.
- Koppel voor een bestaande bibliotheek de bestaande mediadataset als Host Path op
  `/data`. Behoud de namen, locaties en koppelingen van de bestaande bibliotheekmappen.
  Stel het corresponderende pad onder `/data` in voor MediaGuard en zijn forks.
  Gebruik in de migratiewizard de vertaling naar dat containerpad. Controleer dat Emby
  dezelfde titel vóór en na de koppeling via hetzelfde bestaande pad afspeelt.

Details, ACL-plan en GPU: [`docs/INSTALL_TRUENAS.md`](docs/INSTALL_TRUENAS.md).

### 3.2 Installeren als standaard Custom App

Gebruik het [Custom App-formulier](deploy/truenas/CUSTOM_APP.md#formulier) voor de
alles-in-één-image `ghcr.io/lvos04/mediaguard-core:1.3.0-aio@sha256:47e6f0373b6466563b5d2397ffa5a4d5c706446ad3249cb24fcfb96924a25f16`.
Vergelijk de opgehaalde digest met de releasebijlage `digests.json`. Voor een draaiende MediaGuard
volg je [Een bestaande installatie bijwerken](deploy/truenas/BIJWERKEN.md).
Het formulier en de bestaande NAS-installatie zijn **NIET GEVALIDEERD (echte NAS)**.

### 3.3 Losse containers

> **Alleen voor ontwikkeling, geen installatieweg.**

[`deploy/truenas/compose.yaml`](deploy/truenas/compose.yaml) start de onderdelen als losse
containers voor ontwikkeling, CI en integratietests. Gebruik lokale testmappen uit het
[rootvoorbeeld](.env.example) en zie [de technische referentie](docs/INSTALL_TRUENAS.md#5-losse-containers-starten).
Installeer of vervang een bestaande installatie via het formulier uit §3.2.

---

## 4. Eerste start

1. **Beheerder aanmaken** (installatiescherm): gebruikersnaam + wachtwoord (min. 10 tekens),
   taal en thema (Donker/Licht/Systeem).
2. Kies een van de twee routes:
   - **Je hebt al Sonarr/Radarr** → ga naar **Instellingen › Migratie** (hoofdstuk 5).
   - **Verse start** → ga naar **Instellingen › Koppelingen inrichten** en vul eenmalig in:
     1. *Downloads*: je downloadclient (host, poort, API-sleutel/wachtwoord) → **Test**.
     2. *Indexers*: Prowlarr-adres + sleutel (MediaGuard meldt de vier instanties daar aan)
        óf je indexers direct; anime-indexers gaan alleen naar de anime-instanties.
     3. *Paden*: controleer de vier hoofdmappen (`/data/media/tv`, …) → **Controleer**.
     4. **Bekijk wijzigingen** → **Toepassen**. MediaGuard maakt profielen (Series 2160p→1080p,
        Anime Dual Audio, Films 4K HDR/DV, Animefilms Dual Audio), naamgeving, categorieën,
        indexers, downloadclient en de koppeling aan.
     5. Klik op het Overzicht op **Bibliotheek vrijgeven** (alleen bij een verse start; bij een
        migratie gaat dit vanzelf bij de overstap). Tot dan vervangt MediaGuard geen bestanden.
3. **Seerr** koppelen: hoofdstuk 6.

---

## 5. Je vier bestaande Sonarr/Radarr overzetten

Je huidige installaties worden **nooit** aangepast totdat jij de overstap bevestigt. Tot dat
moment doet MediaGuard alleen leesverzoeken (getest: 63 van 63 verzoeken waren alleen-lezen).

### 5.1 Export maken (in elke oude installatie)

Sonarr/Radarr → **System → Backup → Backup Now** → download het `.zip`-bestand. Doe dit voor
Sonarr normaal, Sonarr anime, Radarr normaal en Radarr anime. *Let op: de export bevat je
indexer-sleutels; bewaar hem veilig.*

### 5.2 Uploaden en koppelen

**Instellingen › Migratie → Bronnen → Export uploaden** (4×). MediaGuard herkent zelf of het
Sonarr of Radarr is en of het om anime gaat, en stelt de koppeling voor:

| Bron | Doel |
|---|---|
| Sonarr normaal | Sonarr Series |
| Sonarr anime | Sonarr Anime |
| Radarr normaal | Radarr Films |
| Radarr anime | Radarr Animefilms |

Alternatief: een **live bron** (URL + API-sleutel van de oude installatie); dan kan MediaGuard
ook de laatste wijzigingen meenemen en bij de overstap de oude installatie stilzetten.

### 5.3 Paden omzetten

Gebruikte je oude installatie andere paden (bv. `/tv`, `/movies`)? Stel per voorvoegsel het
nieuwe pad in (bv. `/tv` → `/data/media/tv`). De wizard controleert met een steekproef of de
bestanden echt op de nieuwe plek staan; ontbrekende paden blokkeren de import.

### 5.4 Schaduwimport en vergelijken

**Schaduwimport** zet je volledige configuratie in de nieuwe instantie (series, films, gevolgde
seizoenen/afleveringen, toekomstige seizoenen, profielen, indexers, downloadclients) — met
zoeken en downloaden **uit**. Daarna:

- **Vergelijkingsrapport** per instantie. Een verschil in aantallen of volgstatus = **NO-GO**.
- **Koppelrapport**: hoeveel bestanden op schijf aan afleveringen/films gekoppeld zijn.
- **Gereedheid**: indexers, downloadclient, koppeling met MediaGuard, Seerr-routetest.

### 5.5 Optimaliseren (aanbevolen)

Je oude instellingen worden niet zomaar overgenomen. **Instellingen › Koppelingen inrichten → Optimaliseren na migratie**:

- **Blijft**: al je series/films, volgstatus, bestanden, geschiedenis, logins.
- **Wordt vervangen**: profielen + custom formats, naamgeving, categorieën, indexer-indeling.
- Vooraf zie je het verschil en hoeveel titels een betere versie gaan zoeken (via de
  normale zoeklimieten). Bestanden worden **niet** hernoemd tenzij je dat apart bevestigt.
  Oude profielen blijven als "Oud · …" staan tot je opruimt. **Terugzetten** kan altijd.

### 5.6 Overstap (cutover) — per instantie

1. **Migratie → Overstap** per instantie, bevestig door de instantienaam te typen. Volgorde:
   oude installatie stopt met zoeken (RSS/automatisch zoeken uit, exacte oude stand
   bewaard) → controle dat hij écht stilstaat → laatste wijzigingen overnemen → nieuwe
   instantie zoekt → Seerr omzetten (met bevestiging).
2. Na de eerste geslaagde overstap wordt de bibliotheek automatisch **vrijgegeven** voor
   MediaGuard (AV1-vervangingen e.d.); daarvoor was ze softwarematig vergrendeld.
3. Bij een **export-bron** (geen live verbinding): **Zoeken activeren** en bevestig dat de oude
   installatie uit staat.
4. **Terugdraaien** zet de oude installatie en Seerr exact terug en de nieuwe weer in schaduw.
5. Pas als alles een paar dagen goed loopt: zet je oude containers zelf uit. MediaGuard doet
   dat nooit.

Volledige beschrijving: [`docs/MIGRATION_AND_CUTOVER.md`](docs/MIGRATION_AND_CUTOVER.md).

---

## 6. Seerr koppelen

**Koppel alle vier de instanties in Seerr** — niet maar één Sonarr en één Radarr. Seerr ziet
alleen of iets beschikbaar is in servers die het kent; anime die naar de anime-instantie wordt
verplaatst zou anders nooit "Beschikbaar" worden.

Het makkelijkst: **Instellingen › Koppelingen inrichten → Seerr** (Seerr-adres + API-sleutel) → **Toepassen**; MediaGuard
zet de vier servers zelf goed. Handmatig in Seerr → *Instellingen → Services*:

| Seerr-server | Standaard? | Adres | Profiel | Hoofdmap |
|---|---|---|---|---|
| Sonarr Series | **ja** | `http://<truenas>:38989` | *MediaGuard · WEB-2160p* | `/data/media/tv` |
| Sonarr Anime | nee | `http://<truenas>:38990` | *MediaGuard · Anime Dual Audio* | `/data/media/anime` |
| Radarr Films | **ja** | `http://<truenas>:37878` | *MediaGuard · UHD HDR* | `/data/media/movies` |
| Radarr Animefilms | nee | `http://<truenas>:37879` | *MediaGuard · Anime Dual Audio* | `/data/media/animefilms` |

- API-sleutel: niet nodig — gebruik **Instellingen › Koppelingen inrichten → Seerr**, dan vult MediaGuard de sleutels zelf
  in (ze staan ook in `<appdata>/secrets/<instantie>/apikey`).
- Bij Sonarr Series ook de **anime-velden** invullen (anime-profiel/-map).
- **Scannen inschakelen** bij alle vier, minimale beschikbaarheid *Released*, seizoensmappen aan.
- **Anime**: Seerr stuurt anime naar de standaardserver; MediaGuard verplaatst nieuwe anime
  automatisch naar Sonarr Anime / Radarr Animefilms (alleen titels zonder bestanden of lopende
  download). Zie **Instellingen › Anime-routering** voor *Terugzetten* en *Hier laten*.
- Controle: **Instellingen › Seerr → Routetest** (per server: URL, sleutel, standaard, map).

Meer: [`docs/SEERR_INSTELLEN.md`](docs/SEERR_INSTELLEN.md). Getest met Seerr 3.5.1.

---

## 7. Dagelijks gebruik

Vijf hoofdonderdelen (op de telefoon de balk onderin) en **Instellingen** (onderaan de zijbalk;
op de telefoon in het profielmenu rechtsboven):

| Scherm | Waarvoor |
|---|---|
| **Overzicht** | alles in één oogopslag: *Wacht op jou*, beschikbaar / ontbrekend, AV1-besparing, wat er nu gebeurt |
| **Activiteit** | *Nu bezig* (downloads, zoekacties, encodes), *Problemen* en *Geschiedenis* (alle taken en de audit: wie/wat/wanneer) |
| **Bibliotheek** | series, anime en films; seizoenen en afleveringen volgen, kwaliteit en upgrades per titel; chips *Ontbrekend* (oorzaak, *Zoek nu*) en *Kalender* |
| **Ruimte** | AV1-besparing en opslag (*Besparing*), de encodewachtrij (*Wachtrij*), *Kwaliteit* en *Terugzetten* (originelen, vrijgavecontrole, opruimen, gebruik t.o.v. 500 GB); de kaart *Hardware* toont of de GPU werkt |
| **Aanvragen** | zoeken en aanvragen via Seerr, je aanvragen en hun leverstatus |
| **Instellingen** | één pagina met secties (Algemeen, AV1 & ruimte besparen, Originelen & herstellen, Downloaden & zoeken, Taal & anime, Koppelingen, Meldingen, Beveiliging & toegang, Systeem, Gereedschap) en zoeken. Onderaan elke sectie de bijbehorende pagina's, o.a. *Sonarr- en Radarr-instanties*, *Koppelingen inrichten* (downloadclient, indexers/Prowlarr, paden, Seerr, afwijkingen herstellen), *Seerr*, *Kwaliteit & profielen*, *AV1-engine*, *Dolby Vision*, *Audio & ondertiteling*, *Meldingskanalen*, *API-sleutels & Android-app*, *Systeem & back-ups* (versie, componenten, back-ups, supportbundel), *Release Lab*, *Anime-routering*, *AI-adviseur* en *Migratie*. Overzicht van alle instellingen: [`docs/SETTINGS.md`](docs/SETTINGS.md) |

**Noodstop** (rechtsboven): stopt direct nieuwe downloads, encodes en bestandsvervangingen.
Ook automatisch weghalen, blokkeren, importeren, opruimen, verplaatsen en profielen
aanpassen wachten; upgrades worden pas na de noodstop geïmporteerd.

**Opslag**: originelen na AV1 blijven standaard 7 dagen (max. 500 GB) en worden pas opgeruimd na
een geslaagde vrijgavecontrole van het nieuwe bestand. Kies in *Instellingen › Originelen & herstellen* voor
"Direct verwijderen zodra het nieuwe bestand de controle heeft doorstaan" als je ruimte wilt
besparen.

**HDR10+** wordt standaard mét behoud omgezet (libaom, langzamer dan SVT-AV1, per beeld
gecontroleerd). **Dolby Vision → AV1** staat standaard **uit**; zet hem aan in
*Instellingen › Dolby Vision* (eerst in modus *Proefconversie op kopie*).

---

## 8. Testen

### Wat er getest is (2 november 2026)

| Suite | Resultaat | Waar |
|---|---|---|
| Core unit/API | 386+ geslaagd, 2 overgeslagen (echte AI-sleutels) | `apps/core/tests` |
| Encoder (in toolchain-image) | 241 geslaagd, 2 overgeslagen (geen Intel QSV) | `services/encoder/tests` |
| Web lint/typecheck/build + browser-E2E | 19/19 (desktop 1440, mobiel 360/390, contrast, toetsenbord) | `apps/web/e2e` |
| Forks (4 instanties, echte Sonarr/Radarr) | 12/12; NUnit 50/50 eigen tests | `tests/integration/forks` |
| Volledige stack (echte containers) | 21 geslaagd, 0 gefaald | `tests/integration/stack` |
| Herstel bij crash/disk vol/rechten | 7/7 | `tests/fault-injection` |
| Seerr-contract (echte Seerr 3.5.1) | 17/17 | `tests/contract/seerr` |

Details: [`docs/TEST_RESULTS.md`](docs/TEST_RESULTS.md).

### Zelf testen

Op een Linux-machine met Docker, Python 3.12+, Node 22 en ffmpeg:

```bash
# alles (met samenvattingstabel)
scripts/test-all.sh

# of per onderdeel
cd apps/core && python -m venv .venv && .venv/bin/pip install -e '.[test]' && .venv/bin/pytest -q
cd apps/web && npm ci && npm run lint && npm run typecheck && npm run build && npm run e2e
tests/integration/forks/run.sh          # vier echte fork-instanties + nep-indexer/-downloadclient
tests/integration/stack/run.sh          # complete app inclusief migratie en AV1-keten
tests/fault-injection/run.sh            # kill -9 / schijf vol / geen rechten tijdens vervangen
tests/contract/seerr/run.sh             # echte Seerr + Jellyfin
```

Achter een TLS-inspecterende proxy: `MG_TEST_CA_BUNDLE=/pad/naar/ca.crt` meegeven en images
bouwen met `--secret id=ca_bundle,src=/pad/naar/ca.crt`.

**Test eerst op een kopie**: de tests gebruiken altijd eigen testbestanden, nooit je media.

---

## 9. Lokaal starten (ontwikkeling)

Gebruik voor de ontwikkelstack een lokale `.env` op basis van [het voorbeeld](.env.example).
Vul daarin drie eigen testmappen in en voer het Compose-commando uit vanuit de repositoryroot.

```bash
# Core (API op :8100)
cd apps/core && python -m venv .venv && .venv/bin/pip install -e '.[test]'
MG_DATA_DIR=$(mktemp -d) MG_INTERNAL_TOKEN=dev .venv/bin/python -m mediaguard

# Web (UI op :3000, proxyt /api/v1 naar core)
cd apps/web && npm ci && MG_CORE_URL=http://localhost:8100 npm run dev

# Of de hele stack met nep-indexer/-downloadclient
docker compose -f deploy/truenas/compose.yaml -f deploy/dev/compose.dev.yaml --env-file .env up
```

Forks bouwen: `forks/sonarr/build.sh`, `forks/radarr/build.sh` (gepinde upstream + patches,
zie `forks/*/PATCHES.md`). Upstream bijwerken: `forks/*/sync-upstream.sh` (alleen rapport,
nooit automatisch uitrollen) — [`docs/FORK_UPSTREAM_SYNC.md`](docs/FORK_UPSTREAM_SYNC.md).

---

## 10. Back-ups, updates en terugdraaien

- **MediaGuard-database**: elke nacht automatisch (14 bewaard), handmatig via
  *Instellingen › Systeem & back-ups → Back-ups* (met integriteitscontrole en terugzetten).
  Vóór een update met databasewijzigingen maakt MediaGuard zelf een extra back-up
  (*vóór update*).
- **Sonarr/Radarr-instanties**: hun eigen (wekelijkse) back-ups staan in
  `MG_APPDATA/<instantie>/Backups`.
- **Neem de appdata-dataset mee in je TrueNAS-snapshots/replicatie** (bevat `secrets/` en
  `core/secret.key`; zonder die bestanden zijn opgeslagen sleutels en wachtwoorden niet meer
  leesbaar).
- **Nieuwe versie**: MediaGuard controleert dagelijks het openbare releasekanaal en meldt een nieuwe versie
  (melding + punt in *Wacht op jou*). Er wordt nooit vanzelf bijgewerkt. Uitzetten:
  *Instellingen › Systeem & back-ups → Updates*.
- **Terug naar je oude Sonarr/Radarr**: *Instellingen › Migratie → Terugdraaien* per instantie
  (zie 5.6).

### 10.1 Updaten (alles-in-één)

Volg [Back-up en upgrade](deploy/truenas/CUSTOM_APP.md#back-up-en-upgrade). Verifieer de
herstelset voor appdata, werkdata, media en de gebruikte quarantainelocaties op een
afgeschermde kopie. Verifieer vóór de echte wijziging ook het herstelpunt van elke
schrijvend gekoppelde dienst. Hervat het gebruik pas na de vergelijking vóór en na de
update van de MediaGuard-voortgang en kijkgegevens.

### 10.2 Terugdraaien na een mislukte update

Bewaar de actuele gegevens. Start de vorige image met haar bijpassende snapshotklonen en
controleer het journaal, de originelen en de kijkgegevens. [Terugweg na een mislukte upgrade](deploy/truenas/BIJWERKEN.md#5-terug-via-de-vorige-image-en-snapshotklonen)

Herstel na stroomuitval, half geïmporteerde bestanden en mislukte encodes:
[`docs/DATA_RECOVERY.md`](docs/DATA_RECOVERY.md).

---

## 11. Controlelijst op je eigen TrueNAS

Deze punten konden in de bouwomgeving niet fysiek worden getest. Loop ze door vóórdat je je
oude installaties uitzet:

- [ ] Alle containers **healthy** na installatie; UI bereikbaar na een **herstart van TrueNAS**.
- [ ] **Instellingen › Systeem & back-ups → Technische details tonen → Componenten**: core,
      recovery, encoder en vier instanties groen.
- [ ] **Intel Arc / GPU**: *Ruimte → Hardware* meldt "Hardware-encoder (QSV) gereed" (anders
      werkt alleen de CPU-baan).
- [ ] **Dataset-rechten**: *Instellingen › Koppelingen inrichten → Paden & categorieën* geeft geen
      "hostactie nodig".
- [ ] **Hardlinks**: een testdownload neemt geen extra ruimte in (downloads en media op dezelfde
      dataset).
- [ ] **Proefencode** van een eigen SDR-, HDR10-, HDR10+- en DV 8.1-titel in modus
      *Proefconversie op kopie*; resultaat bekijken op je **tv/Plex/Jellyfin** en bij succes
      *Deze afspeelketen werkt bij mij* aanklikken.
- [ ] **Migratie** van één instantie eerst (bv. Radarr Animefilms) tot en met een paar
      dagen na de overstap.
- [ ] **Seerr**: een film-, serie- en anime-aanvraag komen bij de juiste instantie terecht en
      worden *Beschikbaar*.
- [ ] Optioneel **ZFS-snapshots** via de TrueNAS-API: vul *Instellingen › Originelen & herstellen*
      (TrueNAS-URL, dataset) en geheim `truenas:apikey` in en zet `recovery.zfs_snapshot_enabled` aan;
      controleer na een eerste vervanging of de snapshot in TrueNAS verschijnt.
- [ ] DV-profiel 7-bestanden: nog niet met echte bronnen getest — laat die op *Origineel
      behouden* tot je een proefconversie hebt bekeken.

---

## 12. Problemen oplossen

| Probleem | Oplossing |
|---|---|
| "Bibliotheek nog vergrendeld" | Normaal tot de overstap; bij een verse start: *Overzicht → Bibliotheek vrijgeven* |
| UI toont "MediaGuard kan nog niet starten" | De pagina noemt de map. Geef gebruiker `apps` (568) schrijfrechten (*Datasets → Edit Permissions*); de app gaat binnen 30 s vanzelf verder |
| Hostpoort bezet | Kies een vrije poort volgens [Bezette forkpoort](deploy/truenas/CUSTOM_APP.md#bezette-forkpoort). Controleer de portal of de Seerr-verbinding voor de betrokken poort. |
| "Hostactie nodig" bij een map | Geef gebruiker `apps` (568) schrijfrechten op de dataset via de TrueNAS-ACL |
| Instantie "Offline" | *Instellingen › Sonarr- en Radarr-instanties → Test*; controleer in TrueNAS of de container draait |
| Pre-grab-meldingen `MEDIAGUARD_UNAVAILABLE` in Sonarr | Core was even niet bereikbaar; met fail policy `open` gaat downloaden gewoon door |
| AV1-wachtrij staat stil | *Ruimte → Terugzetten*: maximum (500 GB) bereikt, of te weinig vrije cache-ruimte (zie *Ruimte*) |
| Seerr-aanvraag blijft "in behandeling" | Zijn alle vier de servers in Seerr gekoppeld en is scannen aan? *Instellingen › Seerr → Routetest* |
| Iets mis na een wijziging | *Activiteit → Geschiedenis* (taken en audit) toont wat, wanneer en door wie; veel acties hebben *Terugzetten* |
| Hulp vragen | *Instellingen › Systeem & back-ups → Supportbundel* (bevat geen sleutels of wachtwoorden) |

---

## 13. Documentatie en licenties

| Document | Inhoud |
|---|---|
| [`deploy/truenas/CUSTOM_APP.md`](deploy/truenas/CUSTOM_APP.md#formulier) | Installeren en bijwerken via het standaard TrueNAS Custom App-formulier met de alles-in-één-image |
| [`MEDIAGUARD_GOAL.md`](MEDIAGUARD_GOAL.md) | Bouwplan en acceptatiecriteria voor MediaGuard |
| [`docs/INSTALL_TRUENAS.md`](docs/INSTALL_TRUENAS.md) | Technische installatiereferentie |
| [`docs/MIGRATION_AND_CUTOVER.md`](docs/MIGRATION_AND_CUTOVER.md) | Migratie, no-go, overstap, terugdraaien |
| [`docs/SEERR_INSTELLEN.md`](docs/SEERR_INSTELLEN.md), [`docs/SEERR_COMPATIBILITY.md`](docs/SEERR_COMPATIBILITY.md) | Seerr |
| [`docs/AUTOMATISCHE_INRICHTING.md`](docs/AUTOMATISCHE_INRICHTING.md) | Automatische inrichting en optimaliseren |
| [`docs/BACKFILL_OPERATIONS.md`](docs/BACKFILL_OPERATIONS.md) | Bijzoeken en oorzaken van ontbrekende media |
| [`docs/DATA_RECOVERY.md`](docs/DATA_RECOVERY.md) | Quarantaine, herstel, opruimen |
| [`docs/AV1_*.md`](docs/) | AV1-engine: baseline, integratie, veiligheidsverschillen, TrueNAS-port, DV/HDR-matrix |
| [`docs/AI_ADVISOR.md`](docs/AI_ADVISOR.md) | AI-adviseur: wat wordt verstuurd, kosten, modi |
| [`SECURITY.md`](SECURITY.md), [`docs/SECURITY.md`](docs/SECURITY.md) | Kwetsbaarheid melden, de openbare sideload-sleutel; technische beveiligingsreferentie |
| [`docs/DECISIONS.md`](docs/DECISIONS.md) | Architectuurbesluiten |
| [`docs/UI_CONTROL_COVERAGE_MATRIX.md`](docs/UI_CONTROL_COVERAGE_MATRIX.md) | Elke instelling ↔ UI-control |
| [`docs/design/`](docs/design/) | Designsysteem, schermen, screenshots |
| [`docs/PROGRESS.md`](docs/PROGRESS.md), [`docs/TEST_RESULTS.md`](docs/TEST_RESULTS.md) | Voortgang en testbewijs |

**Licenties**: MediaGuard Ultimate valt onder de **GNU General Public License v3.0** (GPL-3.0,
volledige tekst in [`LICENSE`](LICENSE)). De Sonarr- en Radarr-forks zijn afgeleid van Sonarr
(Team Sonarr) en Radarr (Team Radarr) en vallen net als upstream onder **GPL-3.0**
([`forks/LICENSE`](forks/LICENSE); upstream-SHA's, credits en patches staan in `forks/*/UPSTREAM.md`
en `forks/*/patches/`; de images bevatten de bronverwijzing).
De AV1-engine komt uit [`lvos04/av1`](https://github.com/lvos04/av1) (commit `4b31986`).
FFmpeg, SVT-AV1, libaom, dovi_tool en MKVToolNix vallen onder hun eigen licenties
(zie `docs/AV1_TRUENAS_PORT.md`). Lettertypen Inter en JetBrains Mono: SIL OFL.
