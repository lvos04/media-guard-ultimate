# Installatie op TrueNAS SCALE — technische referentie

> Gebruik voor TrueNAS uitsluitend het [standaard Custom App-formulier](../deploy/truenas/CUSTOM_APP.md)
> met het alles-in-één-image `ghcr.io/lvos04/mediaguard-core:<versie>-aio`.
> De gids bevat de stappen voor installatie, upgrade en terugzetten via de TrueNAS-webinterface.

Deze pagina beschrijft de indeling en onderdelen van de afzonderlijke containers voor
ontwikkeling en tests. In die opstelling maakt `mediaguard-init` ontbrekende mappen en
geheimen aan in de appdata-map.

## 1. Images (eenmalig, door de ontwikkelaar)

De bouw- en publicatiestappen hieronder zijn voor releasebeheerders. TrueNAS haalt voor de standaard Custom App uitsluitend de AIO-image op.

1. Maak op GitHub een release-tag `v1.0.0` (de versie moet gelijk zijn aan
   `deploy/release-manifest.json`).
2. De workflow `.github/workflows/release.yml` draait eerst de tests (core op Python 3.12 en
   3.14, encoder, web), bouwt dan alles, start het alles-in-één-image één keer op een gevulde
   database van een eerdere versie (`scripts/aio-smoke.py`), draait het upgradebewijs vanaf de
   direct vorige release (`scripts/upgradebewijs.py`, HARDE EIS) en publiceert pas daarna
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

Drie datasets (namen vrij; de compose heeft **geen standaardpad**, de drie variabelen zijn verplicht):

| Compose-volume | Variabele | Voorbeeldpad (`<pool>` = jouw pool) | Inhoud | Snapshots/back-up |
|---|---|---|---|---|
| `mg_appdata` | `MG_APPDATA` | `/mnt/<pool>/apps/mediaguard` | configuratie, databases, **gegenereerde sleutels** | **ja, altijd** (klein) |
| `mg_work` | `MG_WORK` | `/mnt/<pool>/mediaguard-work` | AV1-cache/staging en quarantaine | optioneel (quarantaine bevat herstelbare originelen) |
| `mg_data` | `MG_DATA_ROOT` | `/mnt/<pool>/data` | `media/` en `downloads/` (TRaSH-indeling) | volgens je eigen mediabeleid |

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

Core (8100), recovery (8100) en encoder (8000) hebben in de ontwikkelstack geen hostpoort.
Zie het [Custom App-formulier](../deploy/truenas/CUSTOM_APP.md#formulier) voor de AIO-poorten.

## 4. GPU in de lokale ontwikkelstack (Intel iGPU/Arc, QSV)

De encoder krijgt alleen het device `/dev/dri` en de groepen van de rendernodes; nooit
`privileged`.

1. Op de lokale ontwikkelhost:
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

## 5. Losse containers starten

> **Alleen voor ontwikkeling, geen installatieweg.**

Gebruik de [ontwikkelinstellingen](../.env.example) met lokale testmappen voor `MG_APPDATA`,
`MG_WORK` en `MG_DATA_ROOT`. Aanvullende instellingen staan in
[`deploy/truenas/env.example`](../deploy/truenas/env.example). Zie [README §9](../README.md#9-lokaal-starten-ontwikkeling)
voor de lokale opdrachten. Lokaal kun je de ontwikkelconfiguratie met
`docker compose --env-file .env -f deploy/truenas/compose.yaml config -q` controleren.

## 6. Upgraden

### 6.1 Alles-in-één (Custom App)

Volg [Back-up en upgrade](../deploy/truenas/CUSTOM_APP.md#back-up-en-upgrade).
Voor elke release vereist de publicatiepipeline een geslaagde rooktest op een gevulde
database en upgradebewijs vanaf de direct vorige release, inclusief terugzetten.
Zie `scripts/aio-smoke.py` en `scripts/upgradebewijs.py`.

## 7. Back-up en herstel

| Wat | Hoe | Herstel |
|---|---|---|
| Gehele appdata | periodieke ZFS-snapshots en replicatie van de appdata-dataset (Data Protection → Periodic Snapshot Tasks) | Stop MediaGuard en herstel de bijpassende snapshot én image volgens §8.1 |
| Core-DB (consistent, online) | automatisch dagelijks (`backup.daily_enabled`, `backup.retention_count` = 14) en handmatig in de UI; bestanden in `${MG_APPDATA}/core/backups/` met sha256 en integriteitscontrole | UI → Instellingen › Systeem & back-ups → Back-ups → ⋮ → *Terugzetten* (typ de back-upnaam; maakt eerst een veiligheidsback-up) |
| Encoder-DB | automatische back-ups van de encoder in `${MG_APPDATA}/encode/` (functie `automatische_backups`) | via snapshot van `mg_appdata` |
| Forks | native Sonarr/Radarr-back-ups in `${MG_APPDATA}/<instantie>/Backups/` | Sonarr/Radarr → System → Backup → Restore |
| Sleutels | `${MG_APPDATA}/secrets/` en `core/secret.key` | zitten in de snapshot; zonder `secret.key` zijn de in de UI ingevoerde geheimen niet te ontsleutelen |
| Originelen na AV1-vervanging | `${MG_WORK}/quarantine/` (+ journaal in de core-DB) | UI → Ruimte → *Terugzetten* (zie `docs/DATA_RECOVERY.md`) |

De appdata-dataset bevat sleutels: bewaar replica's en exports net zo zorgvuldig als
wachtwoorden.

## 8. Rollback van de app-versie

### 8.1 Alles-in-één (Custom App)

Volg [Terugzetten na een mislukte upgrade](../deploy/truenas/CUSTOM_APP.md#terugzetten-na-een-mislukte-upgrade).
Herstel de bijpassende snapshot via de TrueNAS-webinterface en daarna de vorige
imageversie en digest. Bewaar de toestand van na de update vóór de terugzetting.

Als de kern of de AV1-engine in de huidige bronstand een onbekende schemaversie vindt,
meldt de web-URL dat een bijpassende versie of herstelset nodig is. MediaGuard
pauzeert de automatische acties en weigert nieuwe downloads. De database-inhoud
blijft bewaard. Bij het lezen van een actief SQLite-journaal kunnen de hulpbestanden
`-wal` en `-shm` naast de database ontstaan of wijzigen.

## 9. Verwijderen zonder media te raken

Bewaar de datasets en herstelpunten bij het verwijderen van de app. In de werkdataset
kunnen originelen staan die nodig zijn om een eerdere vervanging te herstellen. Behoud
ook de mediabibliotheek, downloads en de bestaande mediaserver.

## 10. Echte NAS

Formulier, ACL-editor, GPU-doorgifte, snapshot-terugzetting en registry-login zijn
**NIET GEVALIDEERD (echte NAS)**. Zie de [Custom App-gids](../deploy/truenas/CUSTOM_APP.md).
