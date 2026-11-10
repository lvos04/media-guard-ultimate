# MediaGuard uitbreidingen — volledige bouwspecificatie

**Type/eigenaar:** Bestaand: lvos04/media-guard-ultimate  
**Doel:** Bestaande v1.2.2 respecteren; alleen hiaten toevoegen die je centrale systeem en eigen mediaserver echt nodig hebben.

**Afhankelijkheden:** MSO API, Hub, Jellyfin fork, backup tests.

**Bron:** https://github.com/lvos04/media-guard-ultimate

## 1. Concrete schermen en gebruikersreizen
Bestaande schermen Overzicht, Activiteit, Bibliotheek, Ruimte, Aanvragen, Instellingen behouden; optioneel Hub-integratietab, episode-diagnose, versies/status

**Per scherm minimaal:** responsief (360/390/768/1440 px), status leeg/laden/storing/offline, gedocumenteerde navigatie, zoek/filter/paginering, rolcontrole, datum van laatste update, bronherkomst, fout in het Nederlands, toegankelijk toetsenbordgebruik en zichtbaar auditspoor voor wijzigingen.

## 2. Datamodel en levensduur
Geen eigen DB in Hub overnemen; wanted, releases, library, encoding, quarantine, Seerr requests blijven MediaGuard-data. Nieuwe read-only summary en outbound event-contract waar nodig.

Dwing database-migraties met forward-schema-tests af; time-series met retentie/aggregatie; primaire gegevens alleen bij bron. Entiteiten krijgen stable ID, versie, eigenaar, timestamps en provenance waar toepasselijk. Secrets alleen als vault-reference.

## 3. API en integratie-contracten
Bestaande `/api/v1/health`, `/status`, `/wanted`, `/policy/effective`, `/wanted/{id}/search` en capabilities op installatie afleiden; nooit routes veronderstellen zonder live OpenAPI-probe.

Bij alle endpoints: authenticatie, rol, inputvalidatie, idempotency voor mutaties, paginering of cursors voor lijsten, rate limits, time-outs, versie/capability-detectie en machineleesbare foutcodes. De genoemde uitbreidingsroutes zijn ontwerpeisen, niet automatisch bestaande endpoints. API-contracten moet de bouwer in OpenAPI vastleggen en e2e verifiëren.

## 4. Bouwfasen (verplichte volgorde)

### F0 - Inventaris & onderzoek
**Fase-entry:** voorafgaande kritieke gates geslaagd; bestaande gegevens en huidige release vastgelegd; rollback plan bekend.

#### B04-001 — 1.2.2 feature baseline
**Status bij start:** Controleren; **impact:** Middel.
**Gewenst gedrag en grenzen:** Vastleggen nieuw: achtergrondbewaking, Seerr-sync, nooodstop, VMAF, scheduler per GPU/CPU-lane, AV1 quality, Android push, audit, backup/recovery.
**Implementatie in code/diensten:** Git tag, release digest, changelog, installed tag, migrations en smoke-status in compat matrix; geen nieuwe code als al aanwezig.
**Schermen/bediening:** Delta-matrix bestaat / gedeeltelijk / ontbreekt.
**Acceptatie / concrete toets:** Afgeronde 1.2.2 functies worden expliciet niet opnieuw als werk geboekt.
**Foutafhandeling en bewijs:** een externe storing mag niet als succes worden gelogd; leg testscenario, gebruikte app/image-versie, screenshots, redacted request/response, timestamps, audit/rollback en incidentlink vast.

**Fase-exit:** alle in deze fase genoemde checks aantoonbaar geslaagd, code review, back-up-/rollback-gate, een gebruikersreis op mobiel en een negatieve test voor permissions. Bij een blocker niets forceren: status `Geblokkeerd` + reden + eigenaar.

### F1 - Kern en contracten
**Fase-entry:** voorafgaande kritieke gates geslaagd; bestaande gegevens en huidige release vastgelegd; rollback plan bekend.

#### B04-002 — Hub read-model adapter
**Status bij start:** Bouwen; **impact:** Middel.
**Gewenst gedrag en grenzen:** Stabiele versioned read-only view van wanted, status, downloads, queue, AV1, storage, incidents en notifications.
**Implementatie in code/diensten:** OpenAPI contract, permissions viewer, pagination/cursor, ETag and timestamps, no keys in payload.
**Schermen/bediening:** Hub toont dezelfde cijfers en actiebeperkingen als MediaGuard.
**Acceptatie / concrete toets:** Vergelijk 5 datasets Hub met MediaGuard; offline status unknown.
**Foutafhandeling en bewijs:** een externe storing mag niet als succes worden gelogd; leg testscenario, gebruikte app/image-versie, screenshots, redacted request/response, timestamps, audit/rollback en incidentlink vast.

#### B04-003 — Noodstop en risicocoördinatie
**Status bij start:** Bouwen; **impact:** Hoog.
**Gewenst gedrag en grenzen:** Globale stop in Hub respecteert MediaGuard eigen noodstop, zonder extra bypass, en toont bewust pauzes.
**Implementatie in code/diensten:** Action delegate to documented MediaGuard endpoint only; actions precondition check; stop flag observed.
**Schermen/bediening:** Statusbalk ‘gepauzeerd’ met oorzaak, laatste heartbeat en bevoegdheid.
**Acceptatie / concrete toets:** Noodstop ingeschakeld → geen herzoek, autoimport, cleanup of encode via Hub.
**Foutafhandeling en bewijs:** een externe storing mag niet als succes worden gelogd; leg testscenario, gebruikte app/image-versie, screenshots, redacted request/response, timestamps, audit/rollback en incidentlink vast.

**Fase-exit:** alle in deze fase genoemde checks aantoonbaar geslaagd, code review, back-up-/rollback-gate, een gebruikersreis op mobiel en een negatieve test voor permissions. Bij een blocker niets forceren: status `Geblokkeerd` + reden + eigenaar.

### F2 - Functionaliteit
**Fase-entry:** voorafgaande kritieke gates geslaagd; bestaande gegevens en huidige release vastgelegd; rollback plan bekend.

#### B04-004 — Diagnoseketen met MSO
**Status bij start:** Bouwen; **impact:** Middel.
**Gewenst gedrag en grenzen:** Ontbrekende aflevering → release kandidaten → MSO bronstats → client fout → import/vervangroute.
**Implementatie in code/diensten:** Trace/correlation IDs over owner boundaries, cache with TTL, policy check, non-duplicated state.
**Schermen/bediening:** Vanuit film/episode een klik ‘waarom ontbreekt’ met reden en bron.
**Acceptatie / concrete toets:** Misleading failed episode leidt niet tot dubbele grab of delete.
**Foutafhandeling en bewijs:** een externe storing mag niet als succes worden gelogd; leg testscenario, gebruikte app/image-versie, screenshots, redacted request/response, timestamps, audit/rollback en incidentlink vast.

#### B04-005 — Globale resource scheduler hooks
**Status bij start:** Bouwen; **impact:** Middel.
**Gewenst gedrag en grenzen:** AV1 GPU/CPU lanes pauzeren voor Jellyfin stream, backup, ZFS scrub, resource pressure zonder eigen VMAF/timeoutregels te wijzigen.
**Implementatie in code/diensten:** Publish workload pressure; bounded pause/resume via supported settings, frequency guards.
**Schermen/bediening:** Hub toont resource reservations en hints waarom AV1 wacht.
**Acceptatie / concrete toets:** Kijkactiviteit stopt zware transcode maar veroorzaakt geen vals GPU defectalarm.
**Foutafhandeling en bewijs:** een externe storing mag niet als succes worden gelogd; leg testscenario, gebruikte app/image-versie, screenshots, redacted request/response, timestamps, audit/rollback en incidentlink vast.

**Fase-exit:** alle in deze fase genoemde checks aantoonbaar geslaagd, code review, back-up-/rollback-gate, een gebruikersreis op mobiel en een negatieve test voor permissions. Bij een blocker niets forceren: status `Geblokkeerd` + reden + eigenaar.

### F3 - Integraties en veiligheid
**Fase-entry:** voorafgaande kritieke gates geslaagd; bestaande gegevens en huidige release vastgelegd; rollback plan bekend.

#### B04-006 — Back-up en rollback integratie
**Status bij start:** Bouwen; **impact:** Hoog.
**Gewenst gedrag en grenzen:** MediaGuard config + encoderDB + forks + secrets veilig laten exporteren voor hub restore test.
**Implementatie in code/diensten:** Gebruik eigen app-backup, check SQLite-WAL / four fork config, zfs snapshot, metadata manifest; no raw live DB.
**Schermen/bediening:** Herstelrapport met componentdekking, key match en rollback policy.
**Acceptatie / concrete toets:** Herstel in tijdelijke kopie zonder mediafile-mutatie; bestaande releases blijven traceerbaar.
**Foutafhandeling en bewijs:** een externe storing mag niet als succes worden gelogd; leg testscenario, gebruikte app/image-versie, screenshots, redacted request/response, timestamps, audit/rollback en incidentlink vast.

#### B04-007 — Jellyfin fork events
**Status bij start:** Bouwen; **impact:** Middel.
**Gewenst gedrag en grenzen:** Playback activity en metadata refresh ontvangen; bij import/vervanging geziene media bibliotheek laten refreshen.
**Implementatie in code/diensten:** Event contract connector with correlation, retries, mute while offline, no direct DB.
**Schermen/bediening:** Live leverstatus in toekomstige player.
**Acceptatie / concrete toets:** Na AV1-vervanging juiste playback en watch-state behouden.
**Foutafhandeling en bewijs:** een externe storing mag niet als succes worden gelogd; leg testscenario, gebruikte app/image-versie, screenshots, redacted request/response, timestamps, audit/rollback en incidentlink vast.

**Fase-exit:** alle in deze fase genoemde checks aantoonbaar geslaagd, code review, back-up-/rollback-gate, een gebruikersreis op mobiel en een negatieve test voor permissions. Bij een blocker niets forceren: status `Geblokkeerd` + reden + eigenaar.

### F4 - Automatisering
**Fase-entry:** voorafgaande kritieke gates geslaagd; bestaande gegevens en huidige release vastgelegd; rollback plan bekend.

#### B04-008 — Aanvullende randgevallen
**Status bij start:** Bouwen; **impact:** Hoog.
**Gewenst gedrag en grenzen:** Test Seerr handmatige profielkeuze, anime/film mismatches, stale jobs, quarantine full, NAS degraded en Samsung/TV compat.
**Implementatie in code/diensten:** Regressiescapes uit 1.2.2 changelog + real deployment fixtures.
**Schermen/bediening:** Probleemscherm toont oorzaak, verantwoordelijke app en actie.
**Acceptatie / concrete toets:** Geen nieuwe automatische verwijdering of terugkeer van bewust verwijderde titels.
**Foutafhandeling en bewijs:** een externe storing mag niet als succes worden gelogd; leg testscenario, gebruikte app/image-versie, screenshots, redacted request/response, timestamps, audit/rollback en incidentlink vast.

**Fase-exit:** alle in deze fase genoemde checks aantoonbaar geslaagd, code review, back-up-/rollback-gate, een gebruikersreis op mobiel en een negatieve test voor permissions. Bij een blocker niets forceren: status `Geblokkeerd` + reden + eigenaar.

### F5 - Hardening & UX
**Fase-entry:** voorafgaande kritieke gates geslaagd; bestaande gegevens en huidige release vastgelegd; rollback plan bekend.

#### B04-009 — MediaGuard upgrade gate
**Status bij start:** Bouwen; **impact:** Middel.
**Gewenst gedrag en grenzen:** Contracttests met MSO 1.5/1.6 changes, streamer en Hub; release alleen na green tests en post-upgrade health.
**Implementatie in code/diensten:** Pinned SHA test matrix and CI artifacts, API readwrite allowlist, deploy on copied DB.
**Schermen/bediening:** Compat overzicht met release/commit en gebruikersimpact.
**Acceptatie / concrete toets:** MediaGuard-deploy geblokkeerd als MSO read-model incompatible.
**Foutafhandeling en bewijs:** een externe storing mag niet als succes worden gelogd; leg testscenario, gebruikte app/image-versie, screenshots, redacted request/response, timestamps, audit/rollback en incidentlink vast.

**Fase-exit:** alle in deze fase genoemde checks aantoonbaar geslaagd, code review, back-up-/rollback-gate, een gebruikersreis op mobiel en een negatieve test voor permissions. Bij een blocker niets forceren: status `Geblokkeerd` + reden + eigenaar.

### F6 - Release en nazorg
**Fase-entry:** voorafgaande kritieke gates geslaagd; bestaande gegevens en huidige release vastgelegd; rollback plan bekend.

#### B04-010 — Proefmigratie en productiecontrole
**Status bij start:** Bouwen; **impact:** Middel.
**Gewenst gedrag en grenzen:** Nieuwe integraties eerst schaduwen, echte download/media tests pas na back-up en expliciet akkoord.
**Implementatie in code/diensten:** Shadow read logs, synthetic episodes where possible, real download with limits, canary 24-48h.
**Schermen/bediening:** Go/no-go rapport.
**Acceptatie / concrete toets:** Na terugrol blijven downloads en bestandsintegriteit correct.
**Foutafhandeling en bewijs:** een externe storing mag niet als succes worden gelogd; leg testscenario, gebruikte app/image-versie, screenshots, redacted request/response, timestamps, audit/rollback en incidentlink vast.

**Fase-exit:** alle in deze fase genoemde checks aantoonbaar geslaagd, code review, back-up-/rollback-gate, een gebruikersreis op mobiel en een negatieve test voor permissions. Bij een blocker niets forceren: status `Geblokkeerd` + reden + eigenaar.

## 5. Niet-regressie, randgevallen en verbodsbepalingen
Bestaand product v1.2.2 release van 9 nov 2026; status van werkelijke NAS-installatie afzonderlijk controleren. Geen verhuizing van media, geen vervangen van bestaande UI zonder reden.

## 6. Oplevering door bouwer
Opleveren: (1) broncode en tests, (2) migration scripts en versiecompatibiliteit, (3) Compose/TrueNAS app manifest met gepinde images, (4) .env.example zonder geheimen, (5) OpenAPI/connector schemas, (6) test- en auditrapport, (7) restore bewijs, (8) release notes, (9) gebruikershandleiding, (10) screenshots web/mobiel. Markeer niet-geteste hardware/echte accounts als `NIET GEVALIDEERD`.

## 7. Proof of Done
Alle acceptatiecriteria in Excel zijn gekoppeld aan testbewijs en codecommit; bij stroomonderbreking, uitval van afhankelijke app, NAS-herstart, gewijzigde credentials en incompatibele versie degradeert de service aantoonbaar veilig. Geen directe writes aan de database van een andere app.
