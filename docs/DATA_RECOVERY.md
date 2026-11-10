# Gegevensherstel — stroomstoring, halve import, wachtrij, mislukte cutover

Dit document beschrijft hoe MediaGuard bibliotheekbestanden vervangt, wat er gebeurt
na een crash of stroomstoring, en welke stappen je zelf neemt in de vier
herstelscenario's. Alle genoemde acties zijn via de UI (Ruimte → Terugzetten en Wachtrij,
Instellingen › AV1-engine en › Dolby Vision) én de REST-API uit te voeren;
SSH is nergens nodig.

Code: `apps/core/mediaguard/recovery/` (executor, service, api, zfs, fsops).
Tests: `apps/core/tests/test_recovery.py` (fault injection bij elke stap).

---

## 1. Uitgangspunten

| Regel | Uitwerking |
|---|---|
| Eén schrijver | Alleen het proces met `MG_ROLE=recovery` voert jobs `recovery.*` en `quarantine.*` uit (`jobs.bind_role`). Core maakt transacties aan; de encoder schrijft alleen naar staging. |
| Eerst veiligstellen, dan activeren | Het origineel staat aantoonbaar in quarantaine vóórdat er iets op het bibliotheekpad verandert. |
| Geen schijn-atomiciteit | Binnen één filesystem: `os.link` + `os.replace` (echt atomisch). Over datasetgrenzen: kopie → `fsync` → sha256-controle; nooit een rename over filesystems geclaimd. |
| Duurzaam journaal | `replacement_transactions` (status) + `replacement_journal` (append-only, elke stap `begin`/`done`), SQLite WAL met `synchronous=FULL`. Paden worden vóór de handeling vastgelegd (write-ahead). |
| Nooit gissen | Onverklaarbare toestand → status `conflict`, melding, niets verwijderd. |
| Integriteit niet uitschakelbaar | `integrity.recovery_verify_checksums`, `integrity.verify_streams_before_activate` en `integrity.require_recoverable_copy` zijn `integrity_locked`. |

## 2. De vervangtransactie

| Stap | Status na stap | Wat gebeurt er | Bij fout |
|---|---|---|---|
| 1 lock | `locked` | Lease-lock op `instance:kind:provider_id`; noodstop? Loopt er een download/import (Grab zonder Download in 48 u)? | `aborted`, niets aangeraakt |
| 2 validate | `validated` | Bron opnieuw `lstat` en vergelijken met de encoder-`source_snapshot` (grootte, mtime_ns, inode, device); geen symlink; staged-uitvoer bestaat, grootte + sha256 kloppen; paden binnen `MG_LIBRARY_ROOTS`/staging | `aborted` (`SOURCE_CHANGED`, `STAGED_CORRUPT`, `PATH_NOT_ALLOWED`) |
| 3 hardlinks/ruimte | — | `nlink>1` → beleid `recovery.hardlink_policy` (standaard *overslaan*; *verbreken* alleen met bevestiging per bestand). Vrije ruimte doel en quarantaine ≥ benodigd + `recovery.min_free_gb` | `aborted` (`HARDLINKED`, `INSUFFICIENT_SPACE`) |
| (optioneel) snapshot | `snapshotted` | ZFS-snapshot via TrueNAS-API (`recovery.zfs_snapshot_enabled`, secret `truenas:apikey`) | `aborted` (`SNAPSHOT_FAILED`) |
| 4 quarantine | `quarantined` | Zelfde filesystem: hardlink van het origineel naar `…/.mediaguard-quarantine/txn-N/` (of centrale map); anders kopie + fsync + sha256. Item in `quarantine_items` (`held`) | `rolled_back`: quarantainekopie weg, origineel stond er altijd |
| 5 stage_temp | `temp_ready` | Staged → `.mg-replace-N-<hash>.part` in de doelmap (O_EXCL), fsync, sha256, mode/eigenaar overnemen; inode in journaal | `rolled_back` |
| 5 activate | `activated` | Laatste snapshotcontrole; `os.replace(temp, final)`; fsync map. Bij extensiewissel (`.mp4`→`.mkv`) daarna de oude naam verwijderen (origineel staat al in quarantaine) | vóór replace: `rolled_back`; na replace: afmaken |
| 6 record | `recorded` | Quarantaine-item → `restorable` | afmaken |
| 7 refresh | `refreshed` | `RescanSeries`/`RefreshMovie` via de arr-client. Fout = waarschuwing + job `recovery.refresh` (max 10 pogingen) | afmaken |
| 8 audit | `committed` | Auditregel (oud/nieuw pad, quarantaine, undo-hint), lock vrij, event `recovery.committed` | — |

Eindstatussen: `committed`, `rolled_back`, `aborted`, `conflict`.

## 3. Wat er gebeurt na een crash (journal replay)

Bij het starten van de recovery-container (`MG_ROLE=recovery`) roept `startup` →
`executor.recover_pending` aan. Per onafgemaakte transactie wordt de **werkelijke
toestand op schijf** bekeken, niet alleen de status:

| Laatst vastgelegde status | Toestand op schijf | Actie |
|---|---|---|
| `pending`/`locked`/`validated`/`snapshotted`, geen quarantainepad | origineel ongewijzigd | `aborted`, lock vrij |
| t/m `temp_ready` | bibliotheekbestand = snapshot (inode/size/mtime) | tijdelijk bestand en quarantainekopie opruimen → `rolled_back` |
| `temp_ready` | bibliotheekbestand heeft de inode van het tijdelijke bestand (replace was al gebeurd) | afmaken → `committed` |
| extensiewissel, oude naam weg, nieuwe naam = ons bestand | — | afmaken → `committed` |
| `activated`/`recorded`/`refreshed` | nieuw bestand aanwezig met juiste grootte | afmaken (record, refresh-job, audit) → `committed` |
| elk | bibliotheekpad leeg, geldige quarantainekopie | origineel terugzetten (link of kopie + sha256) → `rolled_back` |
| elk | onverwacht (extern gewijzigd, kopie ontbreekt) | `conflict` + melding; niets verwijderd |

De tests in `test_recovery.py` simuleren een harde crash (`SimulatedCrash`, een
`BaseException` die niets opvangt) na **elke** stap, voor zowel hardlink- als
kopieermethode en voor extensiewissels, en controleren daarna: geldig bestand op het
bibliotheekpad, origineel herstelbaar, geen `.mg-*`-restanten, lock vrijgegeven.

## 4. Procedures

### 4.1 Stroomstoring / TrueNAS-reboot

1. Laat TrueNAS normaal opstarten; de app start alle containers.
2. De recovery-container voert automatisch de journal replay uit (zie §3) en meldt
   zich met een heartbeat (`GET /api/v1/recovery/readiness`).
3. Controleer in **Ruimte → Terugzetten** of er transacties op `conflict`
   staan (`GET /api/v1/recovery/transactions?state=conflict`). Normaal: geen.
4. De jobrunner zet jobs die `running` waren terug op `queued`; jobs zijn idempotent.
5. Bij `conflict`: open de transactie (journaal toont elke stap). Het origineel staat
   in de quarantainemap van die transactie; zet het terug met **Herstel origineel**
   (`POST /api/v1/quarantine/{id}/restore`, `confirm: true`) of laat het huidige
   bestand staan. Er wordt nooit automatisch iets verwijderd.

ZFS-snapshots (indien aan) zijn een extra vangnet: na herstel uit een snapshot moet je
Sonarr/Radarr laten herscannen, want snapshots alleen houden de arr-databases niet
consistent (G2).

### 4.2 Halve import (download/import onderbroken)

- Sonarr/Radarr importeren zelf; MediaGuard muteert nooit een item waarvoor een Grab
  zonder bijbehorende Download/Upgrade-webhook bestaat (`IMPORT_IN_PROGRESS`), en
  respecteert media-locks.
- Pre-import-hook: bij twijfel `defer` (fork probeert later opnieuw), bij bewezen
  verkeerde aflevering `reject`. Het bestaande bestand blijft actief.
- Na herstel: in Sonarr/Radarr *Activity → Queue* de import opnieuw laten verwerken of
  via MediaGuard **Herscannen**. Webhook-replays zijn idempotent (zelfde
  eventType + downloadId + bestand → geen dubbele analyse-job).
- Blijft een download hangen: na 48 uur telt hij niet meer als lopend; daarna kan een
  vervanging weer starten.

### 4.3 Wachtrij met transcodes (encodes in uitvoering of klaar voor activatie)

- De encoder schrijft uitsluitend naar staging; een onderbroken encode laat de bron
  ongemoeid. Na herstart synchroniseert core (`POST /api/v1/encoding/sync` of
  automatisch elke `encoding.poll_interval_seconds`).
- Jobs in `awaiting_activation` blijven wachten op **Activeer na verificatie**; jobs in
  `handed_off` volgen de recovery-transactie. Is die `rolled_back`/`aborted`, dan meldt
  core `rejected` aan de encoder (staging wordt opgeruimd) en blijft het origineel.
- Is de bron intussen gewijzigd (nieuwe import), dan weigert stap 2 de vervanging
  (`SOURCE_CHANGED`); plan een nieuwe encode.
- Noodstop (`POST /api/v1/system/emergency-stop`) stopt nieuwe grabs, encodes,
  optimalisatie en recovery-mutaties, en ook de downloadbewaking, auto-import,
  downloadherstel, het opruimen, anime-routering, upgrade-imports en het automatisch
  herstel van profielen, formats en Seerr; tags, mediapaden en de webhook worden wel
  bijgehouden (zie `mediaguard/emergency.py`). Een lopende transactie controleert de noodstop
  vóór het veiligstellen en vóór de activatie en draait dan netjes terug (`aborted`).
  Na de activatie wordt een transactie altijd afgemaakt.

### 4.4 Mislukte cutover

*Bestandsniveau (activatie van een nieuw bestand mislukt):* de transactie eindigt in
`rolled_back` of `aborted` met reden; het origineel staat op zijn pad. Bij `conflict`:
zie §4.1 stap 5.

*Instantieniveau (cutover van oude Sonarr/Radarr naar de MediaGuard-fork):*

1. Zet de noodstop aan en schors de nieuwe instantie (pre-grab weigert dan met
   `INSTANCE_SUSPENDED`; webhooks blijven gelogd).
2. Zorg dat nooit twee schrijvers tegelijk actief zijn: óf oude óf nieuwe instantie.
3. Voer de rollback uit via de migratiewizard (herstelt instantie-/Seerr-routering).
4. Controleer **Ruimte → Terugzetten**: geen transacties in een niet-eindstatus.
   Vervangingen die tijdens de cutover zijn gecommit blijven per item herstelbaar.
5. Laat Sonarr/Radarr herscannen en hef daarna de noodstop op.

## 5. Quarantaine en retentie

- Items: `held` (transactie loopt — nooit verwijderbaar), `restorable`, `restored`,
  `discarded` (transactie teruggedraaid; origineel stond nog op zijn plek), `purged`.
- `quarantine.retention_days` (standaard 7) en `quarantine.auto_purge` (standaard
  **aan**, alleen na de vrijgavecontrole; zie de tabel hieronder). Opruimen raakt alleen items van `committed` transacties, nooit vastgezette
  (`pin`) items en nooit binnen dezelfde transactie die ze aanmaakte.
- Handmatig verwijderen vraagt beheerdersrol en expliciete bevestiging; de purge-API
  toont eerst een dry-run (aantal + bytes).
- Herstellen is zelf een transactie: de huidige (AV1-)versie gaat eerst naar
  quarantaine en blijft ook herstelbaar.

## 6. Locatie van de quarantaine

`recovery.quarantine_location`:

- `library_root` (standaard): `<bibliotheekroot>/.mediaguard-quarantine/` binnen
  dezelfde dataset als `MG_LIBRARY_ROOTS`. Veiligstellen = hardlink: geen extra ruimte
  tot het bibliotheekbestand vervangen is, en geen moment zonder bestand. De map is
  verborgen (punt-prefix); controleer na installatie dat Sonarr/Radarr hem niet als
  serie-/filmmap tonen (anders: `central` kiezen of de map uitsluiten).
- `central`: `MG_QUARANTINE_DIR`. Bij een andere dataset: volledige kopie met
  sha256-verificatie (kost tijd en ruimte; de planner rekent dit mee).

## 7. Bekende grenzen

- Een hardlink van een seedende download wordt nooit stil verbroken; bij *verbreken*
  blijft de seedende kopie bestaan en kost de vervanging netto ruimte.
- De ZFS-snapshotadapter is getest tegen een nep-server; valideer endpoint en
  API-key-rechten op de eigen TrueNAS-versie (`POST /api/v1/recovery/truenas/test`).
- Zonder geconfigureerde `MG_LIBRARY_ROOTS` controleert de worker alleen dat staged
  bestanden in staging/quarantaine staan; stel de roots in productie altijd in.

## Bewaren en opruimen van originelen (opslagbeheer)

Standaard (aanpasbaar in de UI onder *Ruimte → Terugzetten* en *Instellingen › Originelen &
herstellen*):

| Instelling | Standaard | Betekenis |
|---|---|---|
| `quarantine.mode` | bewaren | `Direct verwijderen zodra het nieuwe bestand de controle heeft doorstaan` maakt ruimte binnen minuten vrij (geen terugzetten, wel opnieuw downloaden) |
| `quarantine.retention_days` | 7 dagen | Minimale herstelbare periode na vervanging (0 = direct na controle) |
| `quarantine.max_size_gb` | 500 GB | Maximum voor alle herstelbare originelen samen |
| `quarantine.min_age_hours` | 24 uur | Ook bij vol quotum blijft een origineel minstens zo lang |
| `quarantine.auto_purge` | aan | Automatisch opruimen na termijn / bij vol quotum |
| `quarantine.release_check_full_decode` | aan | Volledige decode van het nieuwe bestand vóór opruimen |

**Vrijgavecontrole (niet uitschakelbaar voor automatisch opruimen):** vóór een origineel
automatisch verdwijnt, controleert de recovery-worker het *actieve* nieuwe bestand opnieuw:
checksum gelijk aan de geactiveerde versie, duur, resolutie, HDR-kleurmetadata, Dolby Vision
(als het origineel DV had), alle audio- en ondertitelsporen/talen, hoofdstukken, en een
volledige decode zonder fouten. Faalt iets, dan blijft het origineel staan en verschijnt een
melding. Vlak vóór het daadwerkelijke verwijderen wordt de controle nog eens herhaald.

**Vol quotum:** eerst worden de oudste *gecontroleerde* originelen opgeruimd (na de minimale
leeftijd). Is er dan nog te weinig ruimte, dan pauzeert de AV1-wachtrij (`QUARANTINE_FULL`)
in plaats van de schijf vol te laten lopen. Vastgezette items worden nooit automatisch
verwijderd.

**Handmatig verwijderen** kan altijd via de UI (per item of in bulk, met preview en
bevestiging). Is de vrijgavecontrole niet geslaagd, dan vraagt de UI een extra, expliciete
bevestiging.

## 8. Back-ups en de versleutelde herstelbundel

**Elke nacht (04:15)** maakt MediaGuard een gecontroleerde back-up van de eigen database
(integriteitscontrole + schema) en, ernaast, een kopie van de database van de AV1-engine
(`…encode-app.db`). Je vindt ze in `<appdata>/core/backups/`. Back-up, herstelbundel en
nachtelijk onderhoud zijn drie losse stappen: mislukt er één (bijvoorbeeld omdat `/config`
vol is), dan gaan de andere gewoon door, krijg je direct een melding en probeert MediaGuard
het na een uur opnieuw. Een back-up schrijft eerst naar `….part` en krijgt pas na de
hersteltest zijn echte naam; halve of kapotte bestanden tellen niet mee bij het opruimen en
kunnen dus nooit een goede back-up wegdrukken.

**Vóór een update met databasewijzigingen** (in core of in de AV1-engine) maakt de
supervisor, nog vóór er een dienst start, een set in `backups/voor-update/voor-update-<tijd>/`
met de core-database, de database(s) van de AV1-engine en `meta.json` (versies, checksums).
De laatste 5 sets blijven bewaard. Je ziet ze in de app onder *Instellingen → Systeem →
Back-ups* ('Vóór update naar …'). **Terugzetten** zet de kopieën klaar; zodra de app stopt
wisselt MediaGuard de databases om (alleen hernoemen, binnen een seconde). De databases van
daarvoor blijven bewaard in `backups/voor-terugzetten/<tijd>/`. Daarna kies je in TrueNAS de
vorige versie en start je de app. Annuleren kan zolang de app nog draait. Klaarzetten kan
alleen in de alles-in-één-container (alleen daar is een supervisor die omwisselt). Met losse
containers zonder supervisor maakt core vlak vóór zijn migratie zelf een set (alleen de
core-database); die zie en download je in de app. Terugzetten doe je dan met alle containers
gestopt: zet de huidige `core/mediaguard.db` met `-wal` en `-shm` eerst opzij en zet daarna
`mediaguard.db` uit de set op die plek.

Die gewone back-ups bevatten de opgeslagen wachtwoorden en API-sleutels **versleuteld**;
de sleutel daarvoor (`core/secret.key`) en de gegenereerde sleutels (`secrets/`) staan er
bewust niet in. Wil je ook die kunnen terugzetten, zet dan de **herstelbundel** aan:

1. Kies in MediaGuard *Instellingen → Systeem → Back-ups → Herstelcode instellen* (of
   `POST /api/v1/backups/bundle/setup`). Je krijgt **één keer** een herstelcode te zien
   (`XXXX-XXXX-XXXX-XXXX-XXXX-XXXX`). Schrijf hem op en bewaar hem buiten de NAS.
2. Vanaf dan maakt MediaGuard elke nacht een versleutelde bundel in
   `backups/herstelbundel/` (de laatste 7 blijven bewaard), met: core-database,
   AV1-engine-database(s), `secret.key` en de map `secrets/`.

**Hoe het beveiligd is.** Bij het instellen maakt MediaGuard een sleutelpaar. De publieke
sleutel gebruikt het 's nachts om elke bundel met een eigen willekeurige sleutel
(AES-256-GCM) te versleutelen; de privésleutel staat alleen versleuteld met jouw
herstelcode (scrypt + AES-GCM) in `herstelsleutel.json`. MediaGuard heeft de code zelf niet
nodig en bewaart hem nergens. Wie de hele back-upmap heeft maar de code niet, kan de bundel
niet openen.

**Terugzetten op een nieuwe/lege appdata-map** (bijvoorbeeld na een kapotte schijf):

```bash
# 1. kopieer herstelsleutel.json + de nieuwste bundel naar de TrueNAS
# 2. controleer eerst (verandert niets):
sudo docker run --rm -it -v /pad/naar/bundels:/b ghcr.io/lvos04/mediaguard-core:<versie>-aio \
  /opt/venv/bin/python -m mediaguard.maintenance.bundle check \
  --bundel /b/mediaguard-bundel-<datum>.mgbundle --sleutel /b/herstelsleutel.json
# 3. uitpakken in de (lege) appdata-dataset; bestaande bestanden worden als
#    '.voor-herstel' bewaard, niet overschreven:
sudo docker run --rm -it -v /pad/naar/bundels:/b -v /mnt/<pool>/<appdata>:/config \
  ghcr.io/lvos04/mediaguard-core:<versie>-aio \
  /opt/venv/bin/python -m mediaguard.maintenance.bundle restore \
  --bundel /b/mediaguard-bundel-<datum>.mgbundle --sleutel /b/herstelsleutel.json --naar /config
```

Daarna staan `core/mediaguard.db`, `core/secret.key`, `encode/*.db` en `secrets/` weer op
hun plek en start de app zoals voorheen. Herstelcode kwijt? Maak een nieuwe (oude bundels
blijven met hun eigen `herstelsleutel.json` + oude code te openen); zonder code zijn
bestaande bundels niet te openen — dat is precies de bedoeling.
