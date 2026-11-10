# Migratie en cutover: van je huidige Sonarr/Radarr naar MediaGuard

Dit document beschrijft de migratiewizard (DEEL C1, R3, ADR-008): hoe je vier bestaande
productie-instanties (Sonarr normaal, Sonarr anime, Radarr normaal, Radarr anime) veilig
overzet naar de vier beheerde fork-instanties `sonarr-series`, `sonarr-anime`,
`radarr-films` en `radarr-animefilms`.

**Hoofdregel:** je huidige Sonarr/Radarr wordt in stap 1 t/m 8 **nooit** gewijzigd. Alle
toegang is alleen-lezen (GET). Dit is structureel afgedwongen: elke aanroep naar een oude
instantie gaat via `SourceClient`, die ieder niet-GET-verzoek weigert. Er bestaan precies
twee smalle uitzonderingen:

| Uitzondering | Wanneer | Wat precies |
|---|---|---|
| `backup` | Alleen als je in stap 2 expliciet "Back-up laten maken" kiest (admin, `confirm_create`) | `POST /api/v3/command {"name":"Backup"}` — het officiële, niet-destructieve back-upcommando |
| `freeze` | Alleen tijdens cutover/rollback, na getypte bevestiging van de instantienaam | `PUT /api/v3/indexer/{id}` met uitsluitend `enableRss`/`enableAutomaticSearch` gewijzigd |

Mediabestanden worden nooit verplaatst, hernoemd of verwijderd.

## Twee soorten bronnen

1. **Live bron** — URL + API-sleutel van je oude instantie. De sleutel staat versleuteld
   in de secrets-store (`source:<id>:apikey`) en komt nooit terug in API-antwoorden of logs.
   App en versie worden automatisch herkend via `GET /api/v3/system/status`.
2. **Back-upbron (upload)** — je uploadt alleen de export (.zip) van je oude instantie. Er
   is dan geen live verbinding: preflight-API-controles, delta-sync en het bevriezen van de
   oude instantie worden overgeslagen (met duidelijke status). De vergelijking gebeurt dan
   tegen de database ín de back-up.

### Zo maak je de export in Sonarr/Radarr

1. Open je oude Sonarr (of Radarr) in de browser.
2. Ga naar **System → Backup**.
3. Klik op **Backup Now** en wacht tot de nieuwe regel (type *Manual*) verschijnt.
4. Klik op de naam van die back-up om het `.zip`-bestand te **downloaden**
   (bijv. `sonarr_backup_v4.0.14.2939_2026.10.01_10.00.00.zip`).
5. Herhaal dit voor alle vier de instanties.
6. In MediaGuard: **Migratiewizard → Bronnen → Back-up uploaden**, één bestand per instantie.

MediaGuard controleert elke upload: maximale grootte (instelling
`migration.max_backup_mb`), sha256, zip-integriteit (CRC), geen onveilige paden in de zip
(zip-slip: `..`, absolute paden en Windows-stations worden geweigerd), begrensde uitgepakte
grootte (zip-bom), aanwezigheid van `sonarr.db`/`radarr.db` met de verwachte tabellen,
SQLite `integrity_check`, het schemaversienummer en `config.xml`. Een PostgreSQL-installatie
levert geen database in de back-up en wordt geweigerd (gebruik dan een live bron met
API-import). App, versie en of het een anime-instantie lijkt (seriestypes, rootfolders,
profielnamen, instantienaam) worden automatisch herkend en er wordt een doel voorgesteld.
Back-ups worden bewaard onder `/config/backups/sources/<bron>/` met rechten 0600.

## De 10 stappen

| # | Stap | Leest | Schrijft |
|---|---|---|---|
| 1 | **Preflight** | Bron: status, health, `/diskspace`, rootfolders, downloadclients, indexers, wachtrij (actieve downloads), bestaande back-ups. Doel: bereikbaarheid, versie, aantal items, `/filesystem` voor elke (herschreven) rootfolder. Seerr: status en welke servers naar de oude instanties wijzen. Lokale vrije ruimte. | Alleen het rapport in MediaGuard |
| 2 | **Back-up + testrestore** | Bron: bestaande back-ups (`GET /system/backup`) en downloaden van het bestand | Optioneel (opt-in) het Backup-commando op de bron; het zipbestand in `/config/backups/sources/`. Testrestore = de database uit de zip in een geïsoleerde kopie read-only openen, `integrity_check` en tellingen |
| 3 | **Mappingpreview** | Bron-inhoud (live of uit back-up) | Voorstel bron → doel in MediaGuard. Koppeling op app (Sonarr/Radarr), seriestype/anime-signalen, rootfolders en profielnamen — **nooit op titel**. Duplicaten (twee bronnen naar één doel) en twijfelgevallen worden gemarkeerd; jij bevestigt of wijzigt |
| 4 | **Schaduwimport** per doel | Bron (live) of back-up | Alleen de DOEL-fork. Zie hieronder |
| 5 | **Vergelijkingsrapport** | Bron en doel | Bij een live bron eerst een delta-sync naar het doel (alleen doel) |
| 6 | **Read-only scan + reconciliatie** | Doel-API (bibliotheeksync) | MediaGuard-cache; rapport verwacht ↔ gevonden ↔ ontbrekend ↔ onbekend ↔ dubbel ↔ verkeerde scope |
| 7 | **Optimalisatievoorstel** | MediaGuard-cache | Alleen een dry-run-plan |
| 8 | **Cutover-gereedheid** | Doel, Seerr | Webhook op het doel registreren (alleen doel). Controles: doel gezond, webhook + hooks bereikbaar, indexers testbaar, downloadclients bereikbaar, Seerr kan het doel bereiken (Seerr's eigen verbindingstest, niets gewijzigd), profiel/rootmap bestaan, toekomstige afleveringen gemonitord = bron |
| 9 | **Gecontroleerde cutover** | Bron en doel | Zie hieronder — eerste moment waarop de oude instantie verandert |
| 10 | **Post-cutover audit** | Bron- en doelhistorie, health, indexerstatus, wachtrij | Alleen rapport |

Elke stap is persistent (tabellen `migration_runs`, `migration_steps`,
`instance_migration_checkpoints`, …), idempotent en hervatbaar. Lange stappen draaien als
job (`migration.preflight`, `migration.backup`, `migration.shadow_import`,
`migration.verify`, `migration.scan`, `migration.optimization`, `migration.readiness`,
`migration.cutover`, `migration.rollback`). De voortgang blijft zichtbaar na refresh en
herstart (`GET /api/v1/migration/runs/{id}`).

### Stap 4a: native restore (voorkeur, ADR-008)

1. De doelinstantie wordt eerst als `shadow_mode` gemarkeerd (vóór er iets gebeurt).
2. Van de back-up wordt een **kopie** gemaakt; het origineel blijft byte-voor-byte gelijk
   (sha256 wordt vóór en na gecontroleerd).
3. In de SQLite van de kopie wordt uitgezet (alleen kolommen die bestaan; Sonarr v3/v4 en
   Radarr v3–v5 gebruiken dezelfde namen):
   - `Indexers.EnableRss = 0`, `Indexers.EnableAutomaticSearch = 0`
     (`EnableInteractiveSearch` blijft staan: handmatig zoeken grijpt nooit vanzelf)
   - `DownloadClients.Enable = 0`
   - `ImportLists.EnableAutomaticAdd` (Sonarr), `ImportLists.Enabled` en
     `ImportLists.EnableAuto` (Radarr), `ImportLists.SearchForMissingEpisodes` (Sonarr v4),
     `ImportLists.SearchOnAdd` (Radarr) `= 0`
   - `Config`: `rsssyncinterval = '0'` (RSS-sync uit)
   De oorspronkelijke waarden worden per naam bewaard (`migration_shadow_states`).
4. **Padherschrijving** (optioneel, per prefix, bijv. `/anime` → `/media/anime`):
   `RootFolders.Path`, `Series.Path` / `Movies.Path`, `ImportLists.RootFolderPath`,
   `RemotePathMappings.LocalPath`. Relatieve bestandspaden (`EpisodeFiles`/`MovieFiles`)
   en downloadcategorieën blijven ongewijzigd. Vooraf toont de **padpreview** per prefix
   het aantal paden, voorbeelden en controleert ze op de read-only mount: álle rootfolders
   en een steekproef (`migration.path_sample_size`) van series/films mét bestanden.
   Een pad dat niet bestaat is **blokkerend**, tenzij je expliciet
   `accept_unresolved_paths` bevestigt.
5. `config.xml` in de kopie krijgt de API-sleutel, poort en URL-base van het **doel**, zodat
   de fork na restore met zijn eigen sleutel bereikbaar blijft.
6. Restore via de officiële route `POST /api/v3/system/backup/restore/upload`
   (multipartveld `restore`), daarna `POST /api/v3/system/restart` en wachten tot het doel
   terug is (`migration.restart_wait_seconds`).
7. Veiligheidscontrole via de API: staat er toch een indexer/downloadclient aan, dan wordt
   die direct uitgezet en gemeld.
8. `RescanSeries` / `RescanMovie` (leest alleen de schijf) koppelt bestaande bestanden;
   het rapport toont **gekoppeld vs ontbrekend** per instantie.

De fork-container hoort daarnaast met `MEDIAGUARD_SHADOW_MODE=1` te starten (deployment),
zodat ook de pre-grab-hook weigert.

### Stap 4b: API-import (fallback)

Alleen voor live bronnen (bijv. bij een versieverschil waarbij het doel ouder is dan de
bron, of een PostgreSQL-bron). Volgorde: tags → custom formats → kwaliteitsdefinities →
kwaliteitsprofielen (op naam, formatscores via custom-formatnaam) → rootfolders →
remote path mappings → delayprofielen → downloadclients → indexers → releaseprofielen →
importlijsten → naming/mediamanagement/indexer-config → series/films → afleveringen.
Series krijgen exact dezelfde `monitored`, seizoensvlaggen, `monitorNewItems`
(toekomstige seizoenen), seriestype, tags en profielen; episode-uitzonderingen worden
daarna met `PUT /api/v3/episode/monitor` exact gelijk gezet. Er wordt nooit gezocht
(`searchForMissingEpisodes=false`, `searchForMovie=false`).

Indexers/downloadclients worden **uitgeschakeld** aangemaakt. Geeft de bron-API een geheim
veld gemaskeerd terug (`********`), dan wordt het niet overgenomen en krijgt de resource de
vlag **"credentials nodig"**: vul de sleutel in via *Downloads & Indexers*, of laat
Prowlarr opnieuw synchroniseren. Zolang dat niet is opgelost is het rapport NO-GO.

Optie **"Nieuwe seizoenen automatisch volgen"** (D1): standaard wordt de bron exact
gevolgd. Kies je de optie, dan krijgen gemonitorde series `monitorNewItems=all`; het rapport
behandelt dat als bewuste keuze.

## No-go-criteria

Een cutover is geblokkeerd (en kan niet als geslaagd gemarkeerd worden) bij:

- **Niet te accepteren** (C1/R3): aantal series/films verschilt; een item ontbreekt;
  monitorstatus van serie, seizoen of aflevering verschilt; `monitorNewItems` verschilt;
  aantal gemonitorde toekomstige afleveringen verschilt; een indexer of downloadclient op
  het doel staat aan tijdens de schaduwfase; inloggegevens ontbreken.
- **NO-GO, maar expliciet te accepteren door een admin met reden** (R6.3): verschillende
  profielen/tags/paden/seriestype/minimum availability per item, ontbrekende tags,
  kwaliteitsprofielen, custom formats, indexers, downloadclients, andere
  downloadcategorieën, ontbrekende rootfolders of remote path mappings.
- **Waarschuwing** (vereist `accept_warnings` bij cutover): afwijkende profieldefinities,
  indexer-prioriteit/tags, extra items in het doel, ontbrekende importlijsten, verschil in
  wanted/missing-aantal.
- Gereedheidscontrole (stap 8) NO-GO, noodstop actief, of het doel is gepauzeerd.

## Stap 9: cutover per instantie of als groep

Bevestigen vereist het **exact typen van de instantienaam** (bijv. `sonarr-series`) en de
rol admin. Volgorde per instantie; elke fase wordt vóór de volgende vastgelegd:

1. **Vastleggen**: exacte huidige staat van de oude instantie (per indexer
   `enableRss`/`enableAutomaticSearch`/`enableInteractiveSearch`, downloadclients,
   importlijsten, RSS-interval). Dit gebeurt één keer; een hervatting overschrijft dit nooit
   met de al bevroren staat.
2. **Freeze oud**: op de oude indexers alleen RSS + automatisch zoeken uit. Downloadclients
   blijven aan zodat lopende downloads afronden en geïmporteerd worden.
3. **Bewaking**: de oude indexers worden opnieuw gelezen. Staat er nog iets aan → afbreken.
4. **Finale delta-sync**: items en monitorwijzigingen sinds de schaduwimport → doel.
5. **Bewaking opnieuw**, dan **schaduw opheffen**: doel-indexers, downloadclients,
   importlijsten en RSS-interval krijgen de oorspronkelijke bronstanden (op naam);
   `shadow_mode` uit.
6. **Seerr**: de Seerr-server die naar de oude instantie wees wordt via de B1-helper
   omgezet naar het doel (URL, API-sleutel, en kwaliteitsprofiel-ID's vertaald op naam).
   De vorige waarden worden bewaard voor rollback.
7. **Afgerond** (`instance_cutover_state.phase = cut_over`).

**Nooit twee RSS-grabbers:** het doel wordt pas geactiveerd nadat de oude indexers
aantoonbaar (opnieuw gelezen) RSS-uit staan.

**Back-upbron ("Zoeken activeren")**: er is geen live oude instantie om te bevriezen. Je
bevestigt met getypte naam én `confirm_old_instance_offline` dat de oude instantie uit
staat of zelf niet meer zoekt. Daarna worden de oorspronkelijke standen uit de back-up op
het doel gezet en lopen RSS en backfill normaal.

## Rollback per instantie

`POST /api/v1/migration/rollback` met getypte instantienaam:

1. Eerst het **doel terug in schaduw** (indexers RSS/auto uit, downloadclients uit,
   `shadow_mode` aan) en opnieuw gelezen — pas daarna verder (anders even twee grabbers).
2. **Seerr** terug naar de bewaarde waarden van vóór de cutover.
3. **Oude instantie exact herstellen**: per indexer precies de vastgelegde
   `enableRss`/`enableAutomaticSearch`, daarna opnieuw gelezen en vergeleken.

Rollback werkt ook na een mislukte of halve cutover.

## Stroomuitval tijdens cutover

- Elke fase (`requested` → `frozen` → `delta_synced` → `lifted` → `seerr_done` →
  `completed`) staat in `migration_cutovers` vóórdat de volgende begint.
- Na herstart zet de jobrunner een lopende `migration.cutover`-job terug in de wachtrij;
  hij hervat vanaf de vastgelegde fase. Is de cutover als *failed* gemarkeerd, dan hervat
  een nieuwe cutover-aanvraag (met getypte naam) **dezelfde** registratie — de
  oorspronkelijke staat van de oude instantie blijft behouden.
- Mogelijke tussentoestanden zijn altijd veilig: oud bevroren + doel nog in schaduw =
  tijdelijk geen grabber (geen dubbele). Gebruik dan "hervatten" of "rollback".
- Een noodstop blokkeert `migration.cutover`-jobs.

## Post-cutover audit

`POST /api/v1/migration/runs/{id}/post-audit` vergelijkt over een venster (standaard 72 uur)
grabs/imports/mislukkingen op het doel met de oude instantie. Grijpt de oude instantie na de
cutover nog iets, of staan haar indexers weer aan → NO-GO. Health-meldingen, door fouten
uitgeschakelde indexers en mislukte downloads worden als waarschuwing getoond. Zolang het
venster niet verstreken is luidt het oordeel "info".

## Hoe de oude installaties herstelbaar blijven

- De oude instantie wordt alleen op de twee indexervelden gewijzigd; de exacte vorige
  staat staat in `migration_cutovers.source_state_json` en in de audit-log.
- Alle bron-back-ups blijven in `/config/backups/sources/` met sha256 en
  verificatierapport; schaduwkopieën in `/config/backups/shadow/run<id>/`.
- Een bron "verwijderen" archiveert alleen de registratie (en wist de API-sleutel uit
  MediaGuard); de oude instantie en de back-ups blijven onaangeroerd.
- Seerr-wijzigingen zijn terug te draaien met de bewaarde waarden.
- Laat de oude containers na de cutover gestopt maar onverwijderd staan als offline
  rollback (K3). Herstel van een oude installatie uit een back-up gebeurt met Sonarr/Radarr's
  eigen restore in díe installatie, alleen met een geteste back-up.

## Oude AV1-app

`POST /api/v1/migration/av1/import` (upload of pad naar de SQLite van de oude AV1-app).
De database wordt eerst gekopieerd (incl. `-wal`/`-shm`) en alleen de kopie wordt read-only
geopend. Resultaat in `av1_provenance_map`: `source_instance + source_kind + source_id →
mediaguard_ref` (`<instance>:<episode|movie>:<bestand-ID>`) met pad, grootte, mtime,
device/inode, conversie-uitkomst, verificatie en besparing. Al-AV1-bestanden zonder bekende
herkomst krijgen status `av1_unknown_source`. Herhaald importeren is idempotent.

## API-overzicht (`/api/v1/migration`)

| Methode | Route | Rol |
|---|---|---|
| GET/POST | `/sources` | viewer / admin |
| POST | `/sources/upload`, `/sources/{id}/backups/upload` | admin |
| PATCH/DELETE | `/sources/{id}`; POST `/sources/{id}/test` | admin; operator |
| GET/POST | `/runs`; GET `/runs/{id}`; PATCH `/runs/{id}/options`; POST `/runs/{id}/cancel` | viewer/operator/admin |
| POST | `/preflight` | operator |
| POST | `/runs/{id}/backup` | admin |
| POST | `/instance-mapping/preview`; PUT `/runs/{id}/mapping`; POST `/runs/{id}/mapping/confirm`; POST `/runs/{id}/path-preview` | operator |
| POST | `/import-preview` | operator |
| POST | `/runs/{id}/shadow-import` | admin |
| POST | `/instance-mapping/verify`; `/runs/{id}/scan`; `/dry-run`; `/runs/{id}/readiness`; `/runs/{id}/post-audit` | operator |
| POST | `/runs/{id}/accept-mismatches` | admin |
| POST | `/cutover`; `/runs/{id}/activate-search`; `/rollback` | admin |
| GET | `/reports/{id}`, `/av1/imports`, `/av1/provenance`; POST `/av1/import` | viewer; admin |

## Geverifieerd tegen echte Sonarr 4.0.20 / Radarr 6.4-containers (2026-11-02)

Gemeten in `tests/integration/stack` (`test_migration.py`) en met losse probe-containers:

| Punt | Uitkomst |
|---|---|
| Back-up downloaden | `GET <urlBase><path uit /api/v3/system/backup>` (bv. `/backup/manual/sonarr_backup_v4.0.20.3014_….zip`) met header `X-Api-Key` → 200 `application/x-zip-compressed`, inhoud `config.xml`, `sonarr.db`/`radarr.db`, `INFO`. |
| Restore-upload | `POST /api/v3/system/backup/restore/upload`, multipartveld **`restore`** → 200 `{"restartRequired": true}`. De restore wordt pas bij de herstart toegepast (`DatabaseRestorationService: Restoring Database`). |
| Herstart | `POST /api/v3/system/restart` → `{"restarting": true}`; in Docker **stopt het proces (exit 0)**. Zonder `restart: unless-stopped` (staat in `deploy/truenas/compose.yaml`) blijft de container uit. |
| `rsssyncinterval = 0` | Na restore: `/api/v3/system/task` toont `RssSync` interval **0** (Sonarr en Radarr); er volgt geen automatische RSS-sync. Ook via `PUT /api/v3/config/indexer {rssSyncInterval: 0}` geaccepteerd (202). |
| `ImportLists`-kolommen | Sonarr 4: `EnableAutomaticAdd`, `SearchForMissingEpisodes`, `MonitorNewItems`, `ShouldMonitor`, `RootFolderPath`. Radarr 6: `Enabled`, `EnableAuto`, `SearchOnAdd`, `Monitor`, `RootFolderPath`. Komt overeen met `SHADOW_COLUMNS`. |
| `Config`-tabel | Sleutels in kleine letters (`rsssyncinterval`); ontbreekt de sleutel (standaardwaarde), dan voegt de schaduwkopie hem toe. |
| `/api/v3/filesystem` | Met `allowFoldersWithoutTrailingSlashes=true` en pad **zonder** slot-slash: bestaande map → `parent` + inhoud, niet-bestaande map → root-lijst zonder `parent`. **Mét slot-slash krijgt een niet-bestaand pad toch `parent: "/"`** — de oude controle gaf daardoor vals "bestaat" (gerepareerd in `wizard.target_path_exists`). `/filesystem/type` zegt voor alles "folder" (onbruikbaar). |
| Gemaskeerde geheimen | `GET /api/v3/indexer` geeft `apiKey: "********"` (privacy `apiKey`); `PUT` met `"********"` behoudt de opgeslagen waarde (gecontroleerd in de SQLite van de fork). `/test` met gemaskeerde waarde niet afzonderlijk aangetoond. |
| `databaseType` | `system/status.databaseType = "sqLite"` (beide). |
| Radarr `MovieMetadata` | Bestaat (`Movies.MovieMetadataId` → `MovieMetadata.TmdbId/Title/Year/…`); `snapshot.from_db` gebruikt die join. |
| Radarr-collecties | `Collections.RootFolderPath` moet mee worden herschreven, anders health-fout "Missing root folder for movie collection" en NO-GO (gerepareerd). |
| Webhook na restore | De MediaGuard-webhook verdwijnt met de doel-DB; de gereedheidscontrole registreert hem nu altijd opnieuw (gerepareerd). |
| Health in schaduwmodus | Sonarr/Radarr melden zelf "No indexers available with RSS sync enabled"; in schaduwmodus telt dat niet meer als NO-GO (gerepareerd). |
| Item-overrides | Een native restore vervangt alle serie-/film-ID's van het doel; MediaGuard-instellingen op serie/seizoen/aflevering/film/bestand van dat doel worden verwijderd (met audit), anders hingen ze aan een andere titel (gerepareerd). |
