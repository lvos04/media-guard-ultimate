# Installatie op TrueNAS SCALE — technische referentie

> **Installeren = gewone *Custom App*** met het alles-in-één-image
> `ghcr.io/lvos04/mediaguard-core:<versie>-aio` — zie README §3.2. Upgraden en terugdraaien van het
> alles-in-één-image: §6.1 en §8.1.
>
> **De losse containers met *Install via YAML* hieronder zijn GEEN installatiemethode** (contract 1.3 §1 en
> §2.3 regel 4; vaste keuze van de gebruiker: nooit *Install via YAML*, `custom_compose_config` of docker
> compose). `deploy/truenas/compose.yaml` blijft alleen voor ontwikkeling, CI en de integratietests, en als
> naslag voor wie het al zo draaide.

Deze pagina beschrijft precies wat `deploy/truenas/compose.yaml` verwacht en doet. De
stap-voor-stapgids voor eindgebruikers staat in de README; hier staan datasets, rechten,
poorten, GPU, upgrade, back-up/restore, rollback en verwijderen.

**Uitgangspunt:** je typt geen sleutels of tokens. De container `mediaguard-init` maakt bij
elke start ontbrekende mappen en **alle** geheimen aan (interne token, API-sleutel en
hook-sleutel per instantie) en bewaart ze in de appdata-dataset. Je vult alleen drie
dataset-paden in (plus optioneel `MG_HOST` en de GPU-groepen).

Vereist: TrueNAS SCALE **24.10 (Electric Eel) of nieuwer** (Docker-apps; de compose gebruikt
Docker volume-*subpaden*), een pool (hieronder `tank`), x86-64.

## 1. Images (eenmalig, door de ontwikkelaar)

"Install via YAML" bouwt niets: alle vijf images moeten in een registry staan.

1. Maak op GitHub een release-tag `v1.0.0` (de versie moet gelijk zijn aan
   `deploy/release-manifest.json`).
2. De workflow `.github/workflows/release.yml` draait eerst de tests (core op Python 3.12 en
   3.14, encoder, web), bouwt dan alles, start het alles-in-één-image één keer op een gevulde
   database van een eerdere versie (`scripts/aio-smoke.py`) en publiceert pas daarna
   (linux/amd64). Een tag die al bestaat wordt nooit overschreven; een fork-tag die al bestaat
   wordt hergebruikt. Mislukt het publiceren halverwege, kies dan in GitHub *Re-run failed jobs*:
   tags die al op het geteste image staan worden overgeslagen (`scripts/promote-image.sh`).

   | image | tag |
   |---|---|
   | `ghcr.io/lvos04/mediaguard-core` | `1.0.0` (ook gebruikt voor recovery en init) |
   | `ghcr.io/lvos04/mediaguard-web` | `1.0.0` |
   | `ghcr.io/lvos04/mediaguard-encode` | `1.0.0` |
   | `ghcr.io/lvos04/mediaguard-sonarr-fork` | `4.0.20.3014-mg2` (en `bc54afb-mg2`) |
   | `ghcr.io/lvos04/mediaguard-radarr-fork` | `6.4.4.10685-mg1` (en `a96bf7c-mg1`) |

3. Nieuwe GHCR-packages zijn privé. Maak ze publiek (GitHub → je profiel → **Packages** →
   package → **Package settings** → **Change visibility** → Public), **of** voeg in TrueNAS
   een registry-credential toe (Apps → **Configuration** → **Manage Container Image
   Registries**: registry `ghcr.io`, gebruiker = GitHub-gebruiker, wachtwoord = personal access
   token met `read:packages`).

## 2. Datasets en rechten

Drie datasets (namen vrij; dit zijn de standaardwaarden in de compose):

| Compose-volume | Variabele | Standaardpad | Inhoud | Snapshots/back-up |
|---|---|---|---|---|
| `mg_appdata` | `MG_APPDATA` | `/mnt/tank/apps/mediaguard` | configuratie, databases, **gegenereerde sleutels** | **ja, altijd** (klein) |
| `mg_work` | `MG_WORK` | `/mnt/tank/mediaguard-work` | AV1-cache/staging en quarantaine | optioneel (quarantaine bevat herstelbare originelen) |
| `mg_data` | `MG_DATA_ROOT` | `/mnt/tank/data` | `media/` en `downloads/` (TRaSH-indeling) | volgens je eigen mediabeleid |

Indeling die de containers gebruiken (ontbrekende mappen maakt `mediaguard-init` aan;
bestaande mappen en bestanden raakt hij niet aan):

```
${MG_APPDATA}/
  core/                     core + recovery: mediaguard.db (SQLite-WAL), secret.key, backups/
  encode/                   encoder: app.db, logs, eigen sleutelbestand
  sonarr-series/  sonarr-anime/  radarr-films/  radarr-animefilms/   /config per fork
  secrets/
    internal/token          interne token (core, recovery, encoder)
    <instantie>/apikey      Sonarr/Radarr-API-sleutel (ook nodig voor Seerr)
    <instantie>/hookkey     hook-sleutel fork → core
${MG_WORK}/
  encode/                   encoder /work: cache/, work/, staging/ (één filesystem)
  encode/staging/           geverifieerde AV1-uitvoer (recovery rw, core ro)
  quarantine/               originelen na vervanging (recovery rw, core ro)
${MG_DATA_ROOT}/
  media/tv  media/anime  media/movies  media/animefilms           rootmappen van de 4 forks
  downloads/tv  downloads/tv-anime  downloads/movies  downloads/movies-anime   categorieën
```

Welke container wat ziet:

| Container | appdata | work | data |
|---|---|---|---|
| mediaguard-init (eenmalig, root zonder netwerk) | alles | alles | alles (alleen ontbrekende mappen) |
| mediaguard-web | — | — | — |
| mediaguard-core | `core` → `/config`, `secrets` → `/run/mediaguard` (ro) | `encode/staging` (ro), `quarantine` (ro) | `media` → `/data/media` (ro) |
| mediaguard-recovery | `core` → `/config`, `secrets/internal` (ro) | `encode/staging`, `quarantine` | `media` → `/data/media` (rw, softwarevergrendeld) |
| mediaguard-encode | `encode` → `/config`, `secrets/internal` (ro) | `encode` → `/work` | `media` → `/data/media` (ro) |
| 4 forks | eigen `<instantie>` → `/config`, eigen `secrets/<instantie>` (ro) | — | hele dataset → `/data` (downloads + media: hardlinks) |

**Rechten (ACL).** Alle containers behalve init draaien als uid/gid **568** (`apps`).

1. Storage → Datasets → *Add Dataset*, preset **Apps** voor `mg_appdata` en `mg_work`; voor
   een bestaande mediadataset het bestaande preset houden.
2. Per dataset: **Edit Permissions** → ACL-editor → entry *User: apps* met **Modify**
   (lezen/schrijven/uitvoeren), *Apply permissions recursively* alleen aan voor de twee
   nieuwe, lege datasets. **Nooit** een recursieve `chown`/ACL-wijziging over een bestaande
   mediabibliotheek forceren als je die rechten al via een groep geregeld hebt; voeg dan
   `apps` toe aan die groep of geef de ACL-entry alleen op `media/` en `downloads/`.
3. Je downloadclient moet dezelfde map als `/data/downloads` zien (zelfde dataset koppelen);
   anders per instantie een remote path mapping (UI → Instellingen › Downloadclients).
4. Cache en staging horen op hetzelfde filesystem (één `mg_work`-dataset); dan verplaatst de
   encoder met `rename`. Liggen ze toch op verschillende mounts, dan valt hij terug op kopie +
   fsync + sha256-controle (langzamer, maar veilig).

**Schrijfvergrendeling van de bibliotheek (ADR-011).** Er zijn geen mount-schakelaars meer
(`MG_LIBRARY_MODE`/`MG_FORK_MEDIA_MODE` bestaan niet in deze compose). Forks en recovery
krijgen de bibliotheek schrijfbaar gekoppeld, maar schrijven wordt in software tegengehouden:

- **recovery** vervangt niets zolang `library.write_enabled` uit staat (standaard **uit**).
  Hij gaat automatisch aan na een geslaagde overstap (cutover), of handmatig via
  UI → Overzicht → **Bibliotheek vrijgeven** (met bevestiging, geaudit). Daarvóór weigert recovery elke
  activatie en blijft het bibliotheekbestand onaangeroerd (getest in core-unittests en de
  stacktest).
- **forks** importeren tijdens een migratie niet: schaduwmodus (core weigert grabs/imports;
  bij native restore worden RSS en downloadclients uitgeschakeld). Bij een verse installatie
  zonder migratie werken ze direct.
- core, web en encoder zien `media/` altijd read-only.

**Vrije ruimte.** De encoder pauzeert zijn banen als `mg_work` minder dan de *pauzebodem*
vrij heeft (standaard **150 GB**; UI: Instellingen › AV1 & ruimte besparen → *Pauzebodem
encodercache*, sleutel `encoding.cache_min_free_gb`; core zet de waarde door naar de encoder).
De reden staat in *Wacht op jou* op Overzicht en bij Instellingen › Systeem & back-ups →
*Technische details tonen* → Componenten (`lanes_paused`). Plan `mg_work` dus ruim boven 150 GB +
de grootste bron die je wilt hercoderen.

## 3. Poorten

| Poort (host) | Variabele | Container | Doel |
|---|---|---|---|
| 30080 | `MG_WEB_PORT` | mediaguard-web:3000 | de UI (de enige die je normaal opent) |
| 38989 | `MG_PORT_SONARR_SERIES` | sonarr-series:8989 | Sonarr-API voor Seerr |
| 38990 | `MG_PORT_SONARR_ANIME` | sonarr-anime:8989 | idem |
| 37878 | `MG_PORT_RADARR_FILMS` | radarr-films:7878 | Radarr-API voor Seerr |
| 37879 | `MG_PORT_RADARR_ANIMEFILMS` | radarr-animefilms:7878 | idem |

Core (8100), recovery (8100) en encoder (8000) hebben geen hostpoort. Controleer vooraf dat
de poorten vrij zijn (`ss -ltnp | grep -E ':(30080|38989|38990|37878|37879)\b'` in de
TrueNAS-shell).

## 4. GPU (Intel iGPU/Arc, QSV)

De encoder krijgt alleen het device `/dev/dri` en de groepen van de rendernodes; nooit
`privileged`.

1. TrueNAS-shell:
   ```sh
   ls -ln /dev/dri                    # kolom 4 = numerieke groep van card* en renderD*
   stat -c '%g %G %n' /dev/dri/*      # zelfde, met groepsnaam
   getent group render video          # ter controle
   ```
   Typisch: `renderD128` → groep `render` (bijv. 107), `card0` → `video` (44).
2. Zet die getallen in de YAML: `group_add: ["${MG_RENDER_GID:-107}", "${MG_VIDEO_GID:-44}"]`
   → vervang de standaardwaarden 107/44 door jouw GID's.
3. Geen `/dev/dri` op de host (of geen GPU gewenst): verwijder de regel
   `devices: ["/dev/dri:/dev/dri"]`. De CPU-baan (SVT-AV1) werkt dan gewoon.
4. Controle na start: UI → Ruimte → kaart *Hardware*, Instellingen › Systeem & back-ups →
   *Technische details tonen* → Componenten → *encoder* → **Diagnose**, of
   `GET /api/v1/encoding/capabilities`: `lanes.gpu.enabled` is pas `true` na een geslaagde
   echte QSV-proefencode; anders staat de reden erbij (bijv. "renderD128 niet lees-/schrijfbaar
   (group_add render/video ontbreekt?)"). AV1-encode in hardware vraagt Intel Arc of nieuwer;
   UHD 770 kan geen AV1 encoderen.

## 5. Installeren ("Install via YAML")

1. Datasets + ACL zoals in §2.
2. Apps → **Discover Apps** → ⋮ (rechtsboven) → **Install via YAML**; naam `mediaguard`.
3. Plak `deploy/truenas/compose.yaml`. Pas **alleen** het blok `volumes:` onderaan aan: de
   drie `device:`-paden (`MG_APPDATA`, `MG_WORK`, `MG_DATA_ROOT`). Optioneel:
   - `MG_HOST` (hostnaam/IP zoals Seerr de forks bereikt; zonder waarde vult MediaGuard het
     adres in waarmee je de UI opent),
   - de GPU-regels uit §4.
   Geen sleutels invullen.
4. **Save.** TrueNAS haalt de images op, `mediaguard-init` draait (mappen + sleutels) en stopt
   met status *exited (0)*; daarna starten core, recovery, encoder, web en de vier forks.
5. Open `http://<truenas>:30080`, maak de eerste beheerder aan en volg **Instellingen ›
   Koppelingen inrichten**
   (instanties, Prowlarr, downloadclient, Seerr: zie `docs/AUTOMATISCHE_INRICHTING.md` en
   `docs/SEERR_INSTELLEN.md`). Met automatische Seerr-inrichting koppelt MediaGuard de
   instanties zelf; de API-sleutel per instantie staat in
   `${MG_APPDATA}/secrets/<instantie>/apikey` als je hem ergens handmatig nodig hebt.

Optionele variabelen (tijdzone, poorten, GPU-groepen, hook-time-out, fail policy,
schaduwmodus, resourcelimieten, image-versies) staan — allemaal uitgecommentarieerd, want
niets is verplicht — met uitleg in `deploy/truenas/env.example`. In "Install via YAML" is er
geen `.env`: wijzig de standaardwaarde in de YAML zelf (het deel na `:-` in `${VAR:-waarde}`).
Lokaal valideren kan met
`docker compose --env-file deploy/truenas/env.example -f deploy/truenas/compose.yaml config -q`.

## 6. Upgraden

### 6.1 Alles-in-één (Custom App, aanbevolen installatie)

1. Lees de release notes. Huidige versie en schemaversies: UI → Instellingen › Systeem &
   back-ups → *Technische details tonen* → *MediaGuard-core* (of `GET /api/v1/admin/version`).
   MediaGuard meldt een nieuwe versie ook zelf (dagelijkse controle op GitHub, instelling
   `updates.check_enabled`); bijwerken doe je altijd zelf.
2. Maak een **ZFS-snapshot van de appdata-dataset** (de Host Path op `/config`, bv.
   `tank/apps/mediaguard`): Datasets → dataset kiezen → kaart *Data Protection* →
   **Create Snapshot**, naam bv. `voor-1.2.2`.
3. Apps → mediaguard → **Edit** → *Image* → **Tag** naar `<nieuwe versie>-aio` → **Update**.
   Bij de start maakt MediaGuard eerst een back-up *vóór update* en voert dan de additieve
   DB-migraties uit.
4. Controle: Instellingen › Systeem & back-ups → *Technische details tonen*: nieuwe versie bij
   *MediaGuard-core*, alle *Componenten* groen of met een begrepen reden.

Elke release is vóór publicatie één keer echt gestart: het AIO-image is in de release-pipeline
opgestart op een gevulde database van een eerdere versie (health, versie, inloggen, migraties
en nette stop; `scripts/aio-smoke.py`). Releasetags worden nooit overschreven: `1.2.2-aio` is
altijd precies het geteste image.

### 6.2 Losse containers (Install via YAML)

1. Zelfde controle als hierboven (versie, schemaversies).
2. Maak een **ZFS-snapshot van `mg_appdata`** (Datasets → dataset → *Data Protection* →
   **Create Snapshot**, bijv. `voor-1.2.2`) en een core-back-up (UI → Instellingen › Systeem &
   back-ups → Back-ups → **Nu back-uppen**).
3. Apps → mediaguard → **Edit** → vervang de image-tags (`ghcr.io/lvos04/mediaguard-*:1.2.2` →
   nieuwe versie; fork-tags alleen als de release ze noemt) → **Save**. TrueNAS haalt de nieuwe
   images op en herstart de containers. Init voert niets destructiefs uit; core voert bij de
   start de additieve DB-migraties uit (met back-up vooraf); recovery start pas als core gezond is.
4. Controle: zelfde als bij 6.1.

## 7. Back-up en herstel

| Wat | Hoe | Herstel |
|---|---|---|
| Alles behalve media | periodieke ZFS-snapshots + replicatie van `mg_appdata` (Data Protection → Periodic Snapshot Tasks) | app stoppen → dataset terugrollen naar snapshot → app starten |
| Core-DB (consistent, online) | automatisch dagelijks (`backup.daily_enabled`, `backup.retention_count` = 14) en handmatig in de UI; bestanden in `${MG_APPDATA}/core/backups/` met sha256 en integriteitscontrole | UI → Instellingen › Systeem & back-ups → Back-ups → ⋮ → *Terugzetten* (typ de back-upnaam; maakt eerst een veiligheidsback-up) |
| Encoder-DB | automatische back-ups van de encoder in `${MG_APPDATA}/encode/` (functie `automatische_backups`) | via snapshot van `mg_appdata` |
| Forks | native Sonarr/Radarr-back-ups in `${MG_APPDATA}/<instantie>/Backups/` | Sonarr/Radarr → System → Backup → Restore |
| Sleutels | `${MG_APPDATA}/secrets/` en `core/secret.key` | zitten in de snapshot; zonder `secret.key` zijn de in de UI ingevoerde geheimen niet te ontsleutelen |
| Originelen na AV1-vervanging | `${MG_WORK}/quarantine/` (+ journaal in de core-DB) | UI → Ruimte → *Terugzetten* (zie `docs/DATA_RECOVERY.md`) |

De appdata-dataset bevat sleutels: bewaar replica's en exports net zo zorgvuldig als
wachtwoorden.

## 8. Rollback van de app-versie

DB-migraties zijn additief maar niet omkeerbaar (oude code kent nieuwe kolommen/tabellen
niet altijd). Rollback is daarom altijd **eerst appdata terug, dan de oude image-tag**:

### 8.1 Alles-in-één

1. Apps → mediaguard → **Stop**; wacht tot de app op *Stopped* staat.
2. Datasets → de appdata-dataset (Host Path op `/config`) → *Data Protection* →
   **Manage Snapshots** → snapshot van vóór de update (bv. `voor-1.2.2`) openklappen →
   **Rollback**. Dit zet core-DB, encoder-DB, fork-configs (ook hun eigen databases) en sleutels
   samen en consistent terug. Nieuwere snapshots van die dataset gaan daarbij verloren.
3. Apps → mediaguard → **Edit** → *Image* → **Tag** terug naar de vorige versie (bv.
   `1.2.1-aio`) → **Update**; de app start met de oude versie.
4. Controle: Instellingen › Systeem & back-ups → *Technische details tonen* toont de oude
   versie en de oude schemaversies; *Componenten* zijn groen.

Zonder snapshot: Instellingen › Systeem & back-ups → Back-ups → de back-up *vóór update* →
⋮ → **Klaarzetten** (typ de naam). MediaGuard wisselt core- en encoder-database om zodra de app
stopt; de databases van daarvoor blijven in `core/backups/voor-terugzetten/`. Daarna stap 1
en 3. Fork-configs gaan zo niet mee terug (die zitten alleen in de snapshot).

### 8.2 Losse containers

1. Apps → mediaguard → **Stop**.
2. Datasets → `mg_appdata` → *Data Protection* → **Manage Snapshots** → de snapshot van vóór
   de upgrade → **Rollback** (zelfde effect als bij 8.1). Alternatief zonder snapshot: na het
   terugzetten van de tags de core-back-up van vóór de upgrade herstellen.
3. Apps → mediaguard → **Edit** → oude image-tags terug → **Save** (start de app).
4. Controle: zelfde als bij 8.1.

Zet nooit de media-dataset (`mg_data` / `/data`) of de werkmap (`mg_work` / `/work`) terug.
Media en quarantaine worden door een rollback niet geraakt. Activeringen die ná de snapshot
zijn gedaan staan nog in `quarantine/` maar niet meer in het journaal van de teruggerolde DB;
controleer die map handmatig voordat je hem opruimt.

## 9. Verwijderen zonder media te raken

1. Apps → mediaguard → **Delete**. De volumes zijn bind-mounts (`driver: local`, `type: none`,
   `o: bind`); verwijderen van de app of de Docker-volumes wist **niet** de inhoud van de
   datasets.
2. Wil je MediaGuard volledig weg: verwijder daarna alleen `mg_appdata` en `mg_work` (eerst
   `quarantine/` controleren: daar staan originelen van vervangen bestanden).
3. **Verwijder de dataset `mg_data` niet**: daarin staan je bibliotheek en downloads. Alleen
   de lege mappen die init aanmaakte (`media/{tv,anime,movies,animefilms}`,
   `downloads/{tv,tv-anime,movies,movies-anime}`) kun je desgewenst handmatig weghalen.
4. Optioneel: TrueNAS-registry-credential voor ghcr.io en de images opruimen
   (Apps → Configuration → Manage Container Images).

## 10. Niet fysiek getest

Deze referentie is gecontroleerd tegen `deploy/truenas/compose.yaml` en `env.example` en de
compose is lokaal gevalideerd (`docker compose config`) en in de stacktest met echte containers
gedraaid, maar **niet** op een echte TrueNAS-host: ACL-editor, volume-subpaden op TrueNAS,
`/dev/dri`/Intel Arc/QSV, ZFS-snapshot-rollback en het ophalen van privé GHCR-images zijn niet
fysiek uitgeprobeerd. Zie `docs/PROGRESS.md`.
