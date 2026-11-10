# Delta-matrix `media-guard-ultimate` — B04 + I03/I16/I19/I20 + A04/A05/A09/A11/A12/A18 + contracten 1.1

Fase **P0** (alleen inventaris en documentatie, geen productcode) · peildatum 2026-11-09 · code = tag `v1.2.2`
(`30f7415`), HEAD `60b50ab` · bewijs-bijlage: [`INVENTARIS.md`](INVENTARIS.md) (routes met `bestand:regel`,
contractprobe §4.4, baseline-tests §11.1). Bron van de eisen: `02_BOUWPLANNEN/B04_MEDIAGUARD_DELTA.md`,
`03_INSTALLATIE/I03|I16|I19|I20`, `04_BESTAANDE_APPS/A04|A05|A09|A11|A12|A18`, `PLATFORM_CONTRACTEN.md` 1.1,
`01_ARCHITECTUUR/02|03|07|08`, `05_TESTS/INTEGRALE_E2E_TESTMATRIX.md`.

**Classificatie:** **bestaand** = aanwezig in code/CI met bewijs (mag **niet** opnieuw als werk geboekt of dubbel
gebouwd worden) · **onvolledig** = deels aanwezig; precies wat mist staat erbij · **ontbrekend** = niet aanwezig.
Een classificatie zegt niets over de echte NAS: alles wat de echte TrueNAS, de Arc A310, echte clients of echte
accounts vraagt is **NIET GEVALIDEERD** en in EVIDENCE.json `Geblokkeerd` of `Niet gestart`.
"Wat NIET mag" volgt `01_ARCHITECTUUR/02_DATA_EIGENAARSCHAP.md`: MediaGuard is eigenaar van bibliotheek,
selectie (wanted/beleid), import, quarantaine en AV1; Prowlarr-sync blijft bij de eigenaar (MediaGuard/Prowlarr).

---

## 0. Samenvatting

- **Bestaand in 1.2.2 (niet opnieuw bouwen):** achtergrondbewaking, zelfherstellende taken, noodstop overal +
  hookwacht, VMAF/SSIM fail-closed, AV1-banen CPU/GPU, kijkpauze via Emby/Jellyfin, Seerr-sync/anime-routering,
  geen terugkeer van verwijderde titels, audit, geverifieerde back-ups + herstelbundel + back-up vóór update,
  vervangjournaal + quarantaine, API-sleutels viewer/operator, alles-in-één Custom App-image met release-rooktest.
- **Hub kan nu al lezen** met een **viewer**-sleutel: status/noodstop, versie/schema, gezondheid, wanted, beleid,
  incidenten, meldingen, events (polling/SSE), downloads, AV1, opslag, back-ups (INVENTARIS §4.2/§4.4).
- **Echte hiaten:** connectorcontract (§5/§6: `/healthz`, `/readyz`, `/connector/v1/*`, foutformaat, cursor/ETag,
  Idempotency-Key), event-envelope, scopes op API-sleutels (een operator-sleutel kan de noodstop ook **uit**zetten),
  correlatie-ID over app-grenzen, schemabeveiliging tegen tag-only-rollback (EX15), herstelrapport incl. forks,
  Custom App-documentatie/manifest volgens §9 + YAML-route als "alleen ontwikkeling" markeren, base-images op
  digest + digests/SBOM in de release, MSO-contracttest in CI, begrensde platformpauze voor AV1, watch-state-test.
- **Niet in deze repo:** Hub-adapter en alle Hub-schermen (`ai-server-manager`), echte NAS-/hardwaretests,
  canary, Samsung/TV, I-stappen-evidence (`jelly-leon-platform`), APP-beslissingen (`appstore-en-fork`).

---

## A. Algemene B04-eisen (§1 schermen, §2 datamodel, §3 API, §5–§7)

| ID | Eis | Klasse | Bewijs | Wat mist / minimale wijziging (P3) | Wat NIET mag |
|---|---|---|---|---|---|
| A-01 | §1 schermen Overzicht, Activiteit, Bibliotheek, Ruimte, Aanvragen, Instellingen behouden | bestaand | `apps/web/components/shell/nav.ts:16-25`; e2e `werk-ia.spec.ts:64` ("vijf onderdelen + Instellingen") | — | UI niet vervangen of naar de Hub verhuizen (B04 §5) |
| A-02 | §1 optionele Hub-integratietab | ontbrekend (optioneel) | sleutelgebruik per koppeling bestaat al: `apikeys.py:175-214`, pagina `instellingen/api-sleutels` | geen MediaGuard-tab bouwen: Hub-schermen horen in de Hub-UI (contract §1). Hooguit later een regel "Gekoppeld met Hub" (laag) | geen tweede bedieningspaneel voor Hub-acties in MediaGuard |
| A-03 | §1 episode-diagnose | bestaand | `GET /api/v1/wanted/{id}/diagnosis` `wanted/api.py:364`; UI `app/(app)/ontbrekend/page.tsx`, `bibliotheek/[instance]/[id]/aflevering/[episode]/page.tsx`; tests `test_wanted_diagnosis.py` | — | — |
| A-04 | §1 versies/status | bestaand | `GET /api/v1/admin/version` `maintenance/api.py:759`; UI `/systeem` | digests ontbreken in het manifest → C-10 | — |
| A-05 | §1 responsief 360/390/768/1440 | onvolledig | Playwright-project alleen `desktop-1440` (`apps/web/playwright.config.ts:31`); specs zetten 360/390/412 (`werk-ia.spec.ts:87` "geen horizontale scroll op 360 en 390 px", `mediaguard.spec.ts:218`); **768 nergens** | 768 toevoegen aan de bestaande scroll-test (alleen test) | geen herontwerp |
| A-06 | §1 leeg/laden/storing/offline | bestaand | `apps/web/components/mg/states.tsx:12` (`loading, empty, error, offline, unavailable, forbidden, not_found`) | — | — |
| A-07 | §1 zoek/filter/paginering | bestaand | `/wanted` filters + `page/page_size` (`wanted/api.py:229-245`); bibliotheek gevirtualiseerd | — | — |
| A-08 | §1 rolcontrole | bestaand | `require_viewer/operator/admin` per route (INVENTARIS bijlage A); e2e `werk-apikeys.spec.ts` | — | — |
| A-09 | §1 laatste update + bronherkomst per kaart | onvolledig | tijden in API (`/status.time` `api/system.py:45`, `/app/status.generated_at`, meldingen `ts`), niet per kaart gegarandeerd | voor Hub-kaarten levert de connector `observed_at` + `source` (P3-01) | geen "groen" zonder meting |
| A-10 | §1 fouten in het Nederlands | bestaand | foutteksten NL (`emergency.py:35-36`, `apikeys.py:36`); i18n `apps/web/lib/i18n` | — | — |
| A-11 | §1 toetsenbord/toegankelijkheid | onvolledig | sneltoetsen (`nav.ts:18-24`), axe-contrast (`werk-ia.spec.ts:170`); geen volledige toetsenbord-e2e | toetsenbord-e2e voor de vijf onderdelen (laag) | — |
| A-12 | §1 zichtbaar auditspoor | bestaand | `audit.py:11-41`, `GET /api/v1/audit` (`api/system.py:111`), UI `instellingen/audit` | — | audit niet inkorten door operators (`settings_guard.py:25-42`) |
| A-13 | §2 geen eigen DB in Hub overnemen; data blijft bij MediaGuard | bestaand (principe) | MediaGuard is enige bron (INVENTARIS §5) | Hub-adapter: alleen cache met TTL + `observed_at` | Hub-kopie als bron van waarheid; directe SQL op `/config/core/mediaguard.db` |
| A-14 | §2 read-only summary + outbound event-contract | onvolledig | outbox `outbox_events` (`events.py:17-27`, `migrations/001_core.sql:92-99`); geen envelope | `/connector/v1/events` in envelope (P3-03) | — |
| A-15 | §2 forward-schema-tests | onvolledig | alleen-vooruit-migraties (`db.py:150-215`), rooktest op DB van eerdere versie (release-run `42554745125`, `scripts/aio-smoke.py`), migratielijst-test `test_release_pipeline.py` | geen bescherming ouder image op nieuwer schema → P3-04 | toegepaste migratiebestanden wijzigen (`migrations/README.md`) |
| A-16 | §2 time-series met retentie | bestaand | opslaglogboek uurlijks (`storage_ledger.py`), retentie-instellingen `maintenance.retention_days_*` (`settings_guard.py:25-42`) | — | — |
| A-17 | §2 stable ID, versie, eigenaar, timestamps, provenance | onvolledig | integer-ID's per tabel, timestamps; herkomst per upgrade (`docs/APP_API.md` §2 `source`); geen ETag/versie per object | connector: `id`, `updated_at`, `etag` = hash van de leesvorm (geen migratie) | — |
| A-18 | §2 secrets alleen als vault-referentie | bestaand (intern) | Fernet-opslag `secrets_store.py:74-90`; API toont alleen `is_set` (`secrets_store.py:1-5`); sleutel-hash + prefix (`apikeys.py:1-9`) | Hub bewaart de MediaGuard-sleutel in eigen vault (`vault://mediaguard/…`) | sleutels in Hub-payloads/logs |
| A-19 | §3 bestaande `/health`, `/status`, `/wanted`, `/policy/effective`, `/wanted/{id}/search` | bestaand | probe INVENTARIS §4.4; `api/system.py:19,25`, `wanted/api.py:229,394`, `policy/api.py:119` | — | — |
| A-20 | §3 capabilities afleiden; "nooit routes veronderstellen zonder live OpenAPI-probe" | onvolledig | `GET /api/v1/openapi.json` alleen **admin** (`app.py:163-178`); met sleutel **403** (probe); MSO valt terug op route-probing (`media-source-optimizer/apps/core/mso/connectors/mediaguard.py:445-457`); `docs/INTEGRATIONS.md:35` noemt de route nog "zonder rol" (verouderd) | `GET /connector/v1/discover` (viewer) + geëxporteerde `docs/openapi/mediaguard.openapi.json` met CI-drifttest (P3-01); INTEGRATIONS.md-rij corrigeren | OpenAPI weer publiek maken (bewuste 1.2.2-keuze, `CHANGELOG.md:28-29`) |
| A-21 | §3 authenticatie, rol, inputvalidatie | bestaand | pydantic-modellen + `require_*`; sleutel ongeldig → 401 (probe) | — | — |
| A-22 | §3 idempotency voor mutaties | onvolledig | interne job-idempotency (`jobs.py:117-140`); geen HTTP-`Idempotency-Key` | alleen op nieuwe connector-mutaties (P3-10; tabel in migratie 140) | semantiek van bestaande routes wijzigen |
| A-23 | §3 paginering/cursors | onvolledig | `/wanted` page; `/notifications` `before_id` (`api/system.py:234-258`); `/audit` offset; `/events/recent` zonder cursor (`api/events.py:52-59`) | connector `cursor`/`limit≤200` (P3-01/03) | bestaande parameters breken (web/Android/MSO) |
| A-24 | §3 rate limits | onvolledig | sleutel-faallimiter (`apikeys.py:38-80`); zoekquotum per instantie (`wanted/backfill.py:73-113`) | geen algemene limiet per sleutel; connector: eenvoudige limiet per sleutel (laag) | quotum omzeilen |
| A-25 | §3 time-outs | bestaand | httpx-time-outs in clients (`mediaserver/clients.py:19`, `recovery/zfs.py`, `encoding/client.py`) | — | — |
| A-26 | §3 versie-/capability-detectie | onvolledig | = A-20; versie via `/api/v1/health` en `/admin/version` | P3-01 | — |
| A-27 | §3 machineleesbare foutcodes | onvolledig | soms `{"detail": {"code": …}}` (`mobile.py:388-392` `CONFIRM_REQUIRED`, `SEERR_NOT_CONFIGURED` in `docs/APP_API.md` §3), meestal vrije tekst | connectorroutes in contractformaat `{code,message,details,trace_id}` (P3-01) | bestaande foutvorm wijzigen (MSO/web/Android) |
| A-28 | §3 OpenAPI vastleggen + e2e | onvolledig | schema genereerbaar (`app.openapi()`, `app.py:181-208`), niet geëxporteerd | P3-01 | — |
| A-29 | §5 geen verhuizing van media; UI niet zonder reden vervangen | bestaand (principe) | recovery enige schrijver (`docs/DATA_RECOVERY.md` §1) | — | media verplaatsen, bibliotheek-ACL's recursief wijzigen |
| A-30 | §5 status echte NAS-installatie apart controleren | ontbrekend → **Geblokkeerd** | geïnstalleerde tag/digest onbekend | via Hub/TrueNAS-readback (`jelly-leon-platform`, I03-03-01) | aannemen dat 1.2.2 draait |
| A-31 | §6 oplevering (1) code+tests | bestaand voor 1.2.2 | INVENTARIS §11 | P3: per hiaat | — |
| A-32 | §6 (2) migraties + versiecompatibiliteit | bestaand | `deploy/release-manifest.json` `schema_versions`; rooktest | — | — |
| A-33 | §6 (3) TrueNAS-manifest met gepinde images | ontbrekend | `deploy/release-manifest.json` is geen §9-manifest | `deploy/manifest/mediaguard.manifest.yaml` (P3-07) | `compose`-veld; voorbeeldwaarden als echte waarden |
| A-34 | §6 (4) `.env.example` zonder geheimen | onvolledig | `deploy/truenas/env.example` (hoort bij compose) | root-`.env.example` voor ontwikkeling (P3-07) | geheimen in voorbeelden |
| A-35 | §6 (5) OpenAPI/connector-schema's | ontbrekend | — | P3-01 | — |
| A-36 | §6 (6) test- en auditrapport | bestaand | `docs/TEST_RESULTS.md` (de veldtestrapporten van de echte NAS staan in het privé-archief, niet in deze repo) | P3-resultaten aanvullen | — |
| A-37 | §6 (7) herstelbewijs | onvolledig | containers: fault injection 8/8, stack-back-up/restore (`docs/TEST_RESULTS.md`); NAS: **NIET GEVALIDEERD** | NAS-hersteltest (Geblokkeerd) | — |
| A-38 | §6 (8)–(10) release notes, handleiding, screenshots | bestaand | `CHANGELOG.md`, `README.md`, `docs/design/screenshots-app`, `docs/design/after` | screenshots van de echte NAS ontbreken (Geblokkeerd) | — |
| A-39 | §7 veilig degraderen: stroomstoring, uitval afhankelijke app, NAS-herstart, gewijzigde credentials, incompatibele versie; geen writes in DB van andere apps | onvolledig | journal-replay + fault injection (`tests/fault-injection/`), onbereikbare instantie telt nooit als verwijderd (`test_delivery_control.py::test_unreachable_instance_never_counts_as_removed`), ingetrokken sleutel 401 (probe; `test_api_keys.py::test_revoked_key_rejected_immediately`); andere apps alleen via hun API (Sonarr/Radarr/Seerr/Prowlarr/Emby/Jellyfin) | incompatibele versie: P3-04; NAS-herstart: NIET GEVALIDEERD | schrijven in Seerr-/Jellyfin-/Prowlarr-databases |

---

## B. Taken B04-001 … B04-010

Per taak: **a** gewenst gedrag · **b** implementatie · **c** schermen · **d** acceptatie · **e** negatief scenario
("externe storing mag niet als succes worden gelogd") · **f** fase-exit (mobiele reis + negatieve rechtentest).

### B04-001 — 1.2.2 feature baseline (F0)

| Rij | Eis | Klasse | Bewijs | Wat mist / P3 | Wat NIET mag |
|---|---|---|---|---|---|
| 001a | achtergrondbewaking | bestaand | `monitor.py:1-20, 458-481`; `test_bewaking.py` (o.a. `::test_forgotten_emergency_stop_alert`) | — | dubbele bewaker in de Hub die zelf meldt wat MediaGuard al meldt (Hub toont MediaGuard-incidenten) |
| 001a | Seerr-sync | bestaand | `provisioning/seerr_sync.py:1-14`, `seerr/service.py`; `test_seerr.py`, `test_seerr_reachable_url.py`; `tests/contract/seerr/RESULTS.md` (17/17 met Seerr 3.5.1) | — | tweede Seerr-sync in de Hub (I19 §5) |
| 001a | noodstop | bestaand | `emergency.py`, `hookguard.py`, `api/system.py:78-108`; `test_regels.py::test_noodstop_*` | — | bypass |
| 001a | VMAF | bestaand | `services/encoder/tests/test_kwaliteitsmeting.py::test_meting_heeft_een_time_out`, `::test_alle_vmaf_metingen_mislukt_dan_ssim`; `test_r8_vmaf.py` (nightly-suite) | — | VMAF-drempels/time-outs vanuit de Hub wijzigen |
| 001a | scheduler per GPU/CPU-baan | bestaand | `encoding/lanes.py:1-20`; `test_encoding_lanes.py::test_cpu_work_cannot_fill_the_queue_gpu_still_gets_work` | — | — |
| 001a | AV1-kwaliteit/DV/HDR10+ | bestaand | `dv/verify.py`, `test_dv.py`, `docs/AV1_SAFETY_DIFF.md` | echte DV-clients: NIET GEVALIDEERD | — |
| 001a | Android-push | bestaand | `mobile.py:539-634`, `notify/channels.py:105-120` (ntfy); `apps/android/app/src/test/.../PairingTest.kt`; workflow `android.yml` | echt toestel: NIET GEVALIDEERD (account/hardware) | — |
| 001a | audit | bestaand | `audit.py`, `GET /api/v1/audit` | — | — |
| 001a | back-up/recovery | bestaand | INVENTARIS §8 (`maintenance/api.py:102-215`, `maintenance/bundle.py`, `maintenance/preupdate.py`, `recovery/executor.py`) | — | tweede back-upsysteem voor MediaGuard-data in de Hub |
| 001b | git-tag, release-digest, changelog, geïnstalleerde tag, migraties, rookstatus in compat-matrix | onvolledig | tag `v1.2.2`→`30f7415`, digests gemeten (INVENTARIS §1.2), changelog, migraties (§5), rooktest groen (release-run) → **compat-matrix INVENTARIS §1.3** | **geïnstalleerde tag/digest op de NAS onbekend** (Geblokkeerd, NAS); digests staan niet in een repo-releasebestand → C-10 | voorbeeldtag als "geïnstalleerd" noteren |
| 001c | delta-matrix bestaand/gedeeltelijk/ontbreekt | bestaand (dit document) | — | — | — |
| 001d | afgeronde 1.2.2-functies niet opnieuw als werk boeken | bestaand (dit document) | kolom *bestaand*; bouwlijst §I bevat alleen hiaten | — | 1.2.2-functies in EVIDENCE.json als nieuw werk |
| 001e | externe storing niet als succes | bestaand | GHCR-label niet leesbaar → expliciet vermeld (INVENTARIS §1.2); NAS-velden `onbekend` | — | — |

### B04-002 — Hub read-model adapter (F1)

| Rij | Eis | Klasse | Bewijs | Wat mist / minimale wijziging (P3-01) | Wat NIET mag |
|---|---|---|---|---|---|
| 002a | stabiele, geversioneerde read-only view van wanted, status, downloads, queue, AV1, storage, incidents, notifications | onvolledig | alle datasets via bestaande **viewer**-routes (INVENTARIS §4.2; probe §4.4) | geen gemeenschappelijke versie (`schema_version`), verschillende vormen en pagineringsstijlen → nieuwe module `apps/core/mediaguard/connector/` (router `/connector/v1`, alleen lezen) die bestaande queryfuncties hergebruikt (`wanted.engine.list_wanted`, `attention.summary`, `health_summary.local`, `encoding`, `storage_forecast`, `api.system.notifications`) | eigen kopie van wanted/bibliotheek in de Hub; nieuwe engine |
| 002b | OpenAPI-contract | onvolledig | admin-only (`app.py:163-178`) | export `docs/openapi/mediaguard.openapi.json` + drifttest in `apps/core/tests` | OpenAPI publiek |
| 002b | permissions viewer | bestaand | sleutelrol viewer (`apikeys.py:35`); probe 200 op alle leesroutes | connector-leesroutes ook `require_viewer` | operator voor lezen eisen |
| 002b | paginering/cursor | onvolledig | A-23 | `cursor` = base64 van laatste `id` + filters; `limit≤200` | — |
| 002b | ETag + timestamps | ontbrekend (ETag) / onvolledig (tijden) | ETag alleen op instellingenschema (`api/settings.py:45-66`) | `ETag` = sha256 van de JSON-vorm; `observed_at` (UTC `…Z`) per item | — |
| 002b | geen sleutels in payload | bestaand | `secrets_store.py:1-5`, publieke kolommen sleutels `apikeys.py:159-172`, redactie `secrets_store.py:18-38` | connector gebruikt dezelfde redactie | sleutels/paden met geheimen tonen |
| 002c | Hub toont dezelfde cijfers en actiebeperkingen | onvolledig | bron: `attention.items[].actions` per rol gefilterd (`docs/APP_API.md` §4 `/app/status`) | Hub-scherm in `ai-server-manager` (andere repo) | Hub laat acties zien die de rol niet mag |
| 002d | 5 datasets Hub = MediaGuard; offline → `unknown` | ontbrekend | geen Hub-adapter (`ai-server-manager` bevat nog geen MediaGuard-code) | MediaGuard-kant: contracttest die connector-items vs bestaande routes vergelijkt voor wanted, status, downloads, AV1, incidenten; `/connector/v1/health` geeft `unknown` bij ontbrekende meting | offline als "0 problemen" tonen |
| 002e | storing niet als succes | onvolledig | encoder onbereikbaar → `reachable:false` in `/app/status` | connector-health: `down`/`unknown` + `reason_code` | — |
| 002f | negatieve rechtentest | bestaand (bestaande routes) | probe: viewer → 403 op noodstop; ingetrokken → 401 | zelfde tests voor connectorroutes | — |

Extra technisch: de alles-in-één opent alleen poort 30080 en `apps/web/server.mjs:29-34` proxyt alleen `/api/v1/*`
en `/api/encoder/*` → **`/connector/v1/*`, `/healthz`, `/readyz` moeten in de webproxy worden doorgezet** naar core.

### B04-003 — Noodstop en risicocoördinatie (F1)

| Rij | Eis | Klasse | Bewijs | Wat mist / minimale wijziging | Wat NIET mag |
|---|---|---|---|---|---|
| 003a | globale stop in Hub respecteert MediaGuard-noodstop zonder extra bypass | onvolledig | MediaGuard-noodstop overal (INVENTARIS §10); **operator-sleutel kan de noodstop ook uitzetten** (probe: `noodstop uit met operator-sleutel` 200) | **P3-02** scopes op API-sleutels: migratie `140_api_key_scopes.sql` (kolom `scopes` TEXT NULL; NULL = huidig gedrag, dus MSO/Android ongewijzigd) + controle in `apikeys.authenticate`/`auth.require`; scope `emergency_stop:on` laat alleen `POST /api/v1/system/emergency-stop` met `active=true` toe | Hub heft de noodstop automatisch op; eigen Hub-vlag die de MediaGuard-hooks omzeilt; schrijven in `system_state` |
| 003a | toont bewuste pauzes | bestaand (data) | `/status.emergency_stop {active, reason, by, since}`; baanredenen `/app/status.encoder.lanes[].reason_nl` (`docs/APP_API.md` §4); melding noodstop > 2 u (`monitor.py`) | connector-health `paused{…}` (P3-01) | — |
| 003b | actie alleen via gedocumenteerd MediaGuard-endpoint | bestaand | `POST /api/v1/system/emergency-stop` (`api/system.py:78`), `/app/control` (`mobile.py:388-405`, `docs/APP_API.md` §4) | — | ander pad (DB, hookwacht-bestand) |
| 003b | preconditiecontrole | ontbrekend | geen `expected_version`/ETag op de noodstop | Hub leest `/status` vóór en na (readback); later connector-`propose` met `resource_etag` (P3-10) | — |
| 003b | stopvlag wordt gevolgd | bestaand | `emergency.py:30-66`; `hooks/service.py:268-269`; `test_regels.py` (15 `test_noodstop_*`), `test_r6_engine_noodstop.py::test_noodstop_gaat_naar_de_engine` | — | — |
| 003c | statusbalk "gepauzeerd" met oorzaak, laatste heartbeat, bevoegdheid | onvolledig | data bestaat (`reason`, `by`, `since`; heartbeat via `/api/v1/health` + `/api/v1/monitor` laatste ronde, `monitor.py:469-479`) | scherm in de Hub (andere repo) | — |
| 003d | noodstop aan → geen herzoek, auto-import, opruimen of encode via Hub | onvolledig | MediaGuard-kant bestaand: herzoek `wanted/backfill.py:326-327` (+ `test_backfill.py::test_guards_suspended_emergency_disabled_excluded`), auto-import `test_regels.py::test_noodstop_geen_automatische_import`, opruimen `recovery/api.py:148-149` + `test_regels.py::test_noodstop_herstel_en_opruimen_slaan_over`, encode `test_r6_engine_noodstop.py` | E2E **via** de Hub ontbreekt (Hub-adapter); let op: herzoek tijdens noodstop geeft **HTTP 429** met "Noodstop actief." (`wanted/backfill.py:598-599` → `wanted/api.py:403-404`), niet 409 → adapter moet dat als `emergency_stop_active` lezen, nooit als "quotum" of succes | — |
| 003e | storing niet als succes | onvolledig | POST geeft nieuwe toestand terug (probe) | Hub doet readback via `/status`; mislukte POST = incident | — |
| 003f | negatieve rechtentest | bestaand | viewer → 403 (probe) | scope-test na P3-02 | — |

### B04-004 — Diagnoseketen met MSO (F2)

| Rij | Eis | Klasse | Bewijs | Wat mist / minimale wijziging | Wat NIET mag |
|---|---|---|---|---|---|
| 004a | ontbrekende aflevering → releasekandidaten → MSO-bronstats → clientfout → import/vervangroute | onvolledig | MediaGuard-delen: diagnose met oorzaakcodes (`wanted/diagnosis.py`, `GET /wanted/reasons` `wanted/api.py:257`), downloadfouten (`downloads/failures.py:518-537`), handmatige import (`activity/manual_import.py:340`), vervangroute (`delivery/replace.py`); MSO-bronstats = MSO | keten over eigenaren = Hub; MediaGuard levert alleen eigen delen | MSO-bronstats in MediaGuard opslaan; Hub-eigen wanted-engine |
| 004b | trace/correlation-ID over eigenaargrenzen | ontbrekend (HTTP) | intern: `jobs.correlation_id` (`jobs.py:135-140`), `audit.correlation_id` (`audit.py:19,35`); geen header ingelezen | **P3-05** middleware: `X-Correlation-Id` (UUID) accepteren, terugsturen, en bij muterende verzoeken met API-sleutel in `audit.correlation_id` zetten | — |
| 004b | cache met TTL | bestaand | `diagnosis?cached=true` (`wanted/api.py:364-377`); MSO cachet zelf | — | — |
| 004b | beleidscontrole | bestaand | `GET /policy/effective`, `POST /policy/simulate` (`policy/api.py:119,156`) | — | — |
| 004b | geen gedupliceerde state | bestaand (principe) | — | Hub bewaart alleen referenties (`wanted_id`) | — |
| 004c | klik "waarom ontbreekt" | bestaand in MediaGuard / ontbrekend in Hub | MediaGuard-UI `ontbrekend/page.tsx`, afleveringspagina | Hub-scherm (andere repo) | — |
| 004d | misleidende mislukte aflevering → geen dubbele grab of delete | bestaand (MediaGuard-kant) | quota + `_still_wanted` (`wanted/backfill.py:115-183`); `test_backfill.py::test_quota_batch_and_never_searches_unmonitored_or_future`, `test_delivery_control.py::test_doubtful_mismatch_is_never_removed`, `::test_wrong_file_never_removed_without_safety_net` | E2E Hub+MSO+MediaGuard (integrale omgeving) | herzoek buiten MediaGuard-quotum |
| 004e/f | storing niet als succes; negatieve rechtentest | onvolledig | diagnose `interactive` vraagt operator (`wanted/api.py:378-386`) | in integrale E2E | — |

### B04-005 — Globale resource-scheduler-hooks (F2)

| Rij | Eis | Klasse | Bewijs | Wat mist / minimale wijziging | Wat NIET mag |
|---|---|---|---|---|---|
| 005a | AV1-banen pauzeren voor Jellyfin-stream | bestaand | `/Sessions`-polling, ook Jellyfin (`mediaserver/clients.py:1-5`, `mediaserver/service.py:42-48`); `test_mediaserver.py::test_watching_holds_encodes_and_marks_recent`, `::test_paused_viewer_does_not_hold` | echte A310 + client: NIET GEVALIDEERD | — |
| 005a | … voor back-up, ZFS-scrub, resourcedruk | ontbrekend | MediaGuard kent geen NAS-back-up/scrub-activiteit; wel stille uren/CPU-% (`encoding/resources.py:1-60`) | **P3-10** begrensde platformpauze in MediaGuard zelf: reden met voorvoegsel `Platform:` (los van `Kijken:`), TTL ≤ 6 u die MediaGuard zelf afloopt, minimaal 15 min tussen wijzigingen; alleen via connector-`propose/apply` (tot die er is: `501 capability_unavailable`) | VMAF-/time-outregels of CPU-% wijzigen; een `Kijken:`-pauze opheffen; de encoder direct aanroepen |
| 005b | werkdruk publiceren | bestaand (data) | `/encoding/resources` (`encoding/resources.py:130`), `/app/status.encoder` (banen, slots, lopend) | connector `items?type=av1_lanes` (P3-01) | — |
| 005b | begrensde pauze/hervat via ondersteunde instellingen, frequentiebewaking | onvolledig | `/app/control` `pause_lane/resume_lane` (operator, `mobile.py:404-420`): geen TTL, geen frequentiegrens, `resume_lane` kan elke pauze opheffen | P3-10 | Hub gebruikt `/app/control resume_lane` |
| 005c | Hub toont reserveringen + waarom AV1 wacht | onvolledig | `reason_nl` per baan bestaat | Hub-scherm (andere repo) | — |
| 005d | kijken stopt zware transcode zonder vals GPU-defectalarm | bestaand | `test_encoding_lanes.py::test_gpu_idle_benign_pauses_and_no_work_do_not_alert`; `test_mediaserver.py::test_watching_holds_encodes_and_marks_recent` | echte hardware: NIET GEVALIDEERD | — |

### B04-006 — Back-up en rollback-integratie (F3)

| Rij | Eis | Klasse | Bewijs | Wat mist / minimale wijziging | Wat NIET mag |
|---|---|---|---|---|---|
| 006a | config + encoder-DB + forks + secrets veilig exporteren voor Hub-hersteltest | onvolledig | core + encoder geverifieerd (`maintenance/api.py:102-185`); secrets alleen in versleutelde bundel (`maintenance/bundle.py:1-20`); forks alleen via eigen fork-back-ups (`instances/manage.py:927-942`), **niet** in bundel/manifest | **P3-06** read-only herstelrapport `GET /connector/v1/items?type=backups`: laatste geverifieerde core/encoder-back-up (sha256, schema, leeftijd), bundelstatus (sleutel-vingerafdruk, `maintenance/bundle.py:86-87`, nooit de sleutel), laatste fork-back-up per instantie (naam, datum via arr-API), dekkingsvlaggen | ruwe kopie van een live SQLite-bestand; secrets onversleuteld exporteren; Hub zet MediaGuard-DB zelf terug |
| 006b | eigen app-back-up, SQLite-WAL, vier fork-configs, ZFS-snapshot, metadata-manifest, geen ruwe live-DB | onvolledig | SQLite-backup-API + `integrity_check` + `meta.json` (sha256, schema, extra bestanden) = bestaand; fork-configs: zie 006a; ZFS-snapshot van de **appdata** = TrueNAS/executor (`zfs.snapshot.create`, contract §8); MediaGuard's eigen snapshot is alleen per vervanging (`recovery/zfs.py`) | fork-back-ups in het herstelrapport (P3-06); appdata-snapshot via Hub-executor | MediaGuard-back-ups vervangen of uitzetten |
| 006c | herstelrapport: componentdekking, sleutelmatch, rollbackbeleid | ontbrekend | losse bronnen: `/backups`, `/backups/bundle`, `/backups/terugzetten` (`maintenance/api.py:260,352,404`) | P3-06 | — |
| 006d | herstel in tijdelijke kopie zonder mediamutatie; releases traceerbaar | onvolledig | `python -m mediaguard.maintenance.bundle restore` pakt uit naar een doelmap (`maintenance/bundle.py:242-251`) = geschikt voor sandbox; releases traceerbaar via tags + manifest | Hub-orchestratie (clone naar sandbox) in `ai-server-manager`; echte NAS: **NIET GEVALIDEERD** | `/data` of `/work` terugzetten (`README.md:410-412`) |
| 006e | storing niet als succes | bestaand | mislukte back-up laat niets achter en meldt (`test_onderhoud.py::test_failed_backup_leaves_no_partial_files_and_notifies`); alleen geverifieerde back-ups tellen (`maintenance/api.py:196-207`) | — | — |

### B04-007 — Jellyfin-fork-events (F3)

| Rij | Eis | Klasse | Bewijs | Wat mist / minimale wijziging | Wat NIET mag |
|---|---|---|---|---|---|
| 007a | afspeelactiviteit en metadata-refresh ontvangen | onvolledig | afspeelactiviteit via polling `/Sessions`; refresh per pad na overdracht (`test_mediaserver.py::test_refresh_after_handoff_then_playback_test`), terugval volledige scan (`::test_unmapped_path_falls_back_to_full_scan_once`); geen push/events | events van de Jellyfin-fork via de Hub (later); MediaGuard blijft pollen | Jellyfin-DB direct lezen/schrijven |
| 007b | event-contract met correlatie, retries, dempen bij offline, geen directe DB | onvolledig | `mediaserver_actions.transaction_id` (`mediaserver/service.py:59-63`), afspeeltest-herhaling (`mediaserver/service.py:315-325`); REST, geen DB | **P3-11** outbox-events `mediaserver.refresh.requested/failed` met `correlation_id = transaction_id`; dempen bij offline (één melding, backoff) | — |
| 007c | live leverstatus in toekomstige speler | onvolledig | `GET /api/v1/delivery/requests` (`delivery/api.py:35`) | speler = `jelly-leon-clients` | — |
| 007d | na AV1-vervanging juiste playback én watch-state | onvolledig / ontbrekend | afspeeltest bestaat (`mediaserver/clients.py:185-222`, `test_mediaserver.py::test_judge_playback_variants`); **watch-state niet getest**; bij extensiewissel `.mp4→.mkv` verandert het pad (`docs/DATA_RECOVERY.md` §2, stap 5) | P3-11: simulatortest dat pad/ItemId gelijk blijft; bij padwissel alleen melden (geen automatische UserData-mutatie) | kijkstatus in Jellyfin-DB herschrijven |

### B04-008 — Aanvullende randgevallen (F4)

| Rij | Eis | Klasse | Bewijs | Wat mist / minimale wijziging | Wat NIET mag |
|---|---|---|---|---|---|
| 008a | Seerr handmatige profielkeuze | bestaand | `test_delivery_control.py::test_profile_set_once_then_owner_choice_stays`, `::test_profile_left_alone` | — | — |
| 008a | anime/film-mismatches | bestaand | `test_anime_routing.py::test_anime_film_moved_to_animefilms`, `test_delivery.py::test_anime_request_lands_in_anime_instance`, `test_delivery_control.py::test_doubtful_mismatch_is_never_removed` | — | — |
| 008a | vastgelopen jobs | bestaand | `test_delivery_control.py::test_stuck_checker_waits_for_mediaguard_identity_check`, `test_bewaking.py::test_every_stuck_request_counts_as_new_even_beyond_the_shown_rows` | — | — |
| 008a | quarantaine vol | bestaand | `test_bewaking.py::test_degraded_pool_full_quarantine_pauses_av1_instead_of_overfilling`, `test_delivery_control.py::test_full_quarantine_on_degraded_pool_leaves_wrong_file_in_place` | — | — |
| 008a | NAS degraded | onvolledig | `test_bewaking.py::test_degraded_pool_alerts_and_pauses_purge` (met TrueNAS-API); **niet ingesteld = geen pauze** (`test_bewaking.py::test_pool_not_configured_means_no_pause`, `storage_forecast.py:184-190`) | contract 03 / EX11: onbekend = blokkeren → **P3-13** (ADR vereist): in platformmodus poolstatus van de Hub als extra invoer; onbekend → opruimen pauzeren | standaardgedrag voor losse installaties stil wijzigen |
| 008a | Samsung/tv-compatibiliteit | onvolledig | afspeeltest met tv-profiel (`mediaserver/clients.py:200-222`), `docs/AV1_DV_PLAYBACK_MATRIX.md` | echte tv: **NIET GEVALIDEERD (hardware)** | — |
| 008b | regressiescapes 1.2.2 + echte deployment-fixtures | onvolledig | regressietests per rapport (`test_r2_*` … `test_r8_*`, `test_report_core_fixes.py`, `test_stack_regressions.py`) | echte fixtures van de NAS: **Geblokkeerd** | productiedata in de repo |
| 008c | probleemscherm: oorzaak, verantwoordelijke app, actie | onvolledig | `attention.items[]` met titel, detail, href, acties (`attention.py:742`); geen veld "verantwoordelijke app" | connector-incidenten met `owner_app: "mediaguard"` + `instance_id` (P3-12); scherm in Hub | — |
| 008d | geen nieuwe automatische verwijdering of terugkeer van bewust verwijderde titels | bestaand | `test_delivery_control.py::test_title_removed_from_arr_stops_without_seerr_retry`, `::test_request_deleted_in_seerr_stops`, `::test_partial_seerr_listing_never_marks_requests_deleted` | — | Hub-acties die verwijderen/opruimen starten (`/downloads/cleanup/run`, `/quarantine/purge`) |

### B04-009 — MediaGuard-upgrade-gate (F5)

| Rij | Eis | Klasse | Bewijs | Wat mist / minimale wijziging | Wat NIET mag |
|---|---|---|---|---|---|
| 009a | contracttests met MSO 1.5/1.6, streamer en Hub | onvolledig | handmatige kruiscontrole 2026-11-09: 19 MSO-1.6.0-routespecs (`media-source-optimizer/apps/core/mso/features/mediaguard/compat.py`) → **17 exact**, `auth/me` = elke ingelogde rol (compatibel), `openapi.json` MSO verwacht "geen rol", MediaGuard eist admin → MSO valt terug op probing (werkt); geen geautomatiseerde test; streamer/Hub: ontbrekend | **P3-09** `tests/contract/mso/` in CI: gepinde kopie van de MSO-routelijst (JSON) tegen de routetabel van `scripts/platform-v2-routes.py --json`; Hub-contract na P3-01 | — |
| 009a | release alleen na groene tests + post-upgrade-health | bestaand | `release.yml` (tests → kandidaten → rooktest op oudere DB → publiceren), run `42554745125` | post-upgrade-health op de NAS: Geblokkeerd | tags overschrijven; `latest` |
| 009b | gepinde SHA-testmatrix en CI-artefacten | onvolledig | upstream-SHA's in manifest; base-images **niet** op digest (INVENTARIS §12); geen digests/SBOM | **P3-08**: base-images `@sha256`, `release/digests.json`, SBOM in `release.yml` | — |
| 009b | API read/write-allowlist | ontbrekend | sleutels alleen rolgebaseerd | P3-02 scopes | — |
| 009b | deploy op gekopieerde DB | onvolledig | rooktest op fixture-DB van eerdere versie (`scripts/make-aio-fixture.py`, `tests/fixtures/aio`); kopie van echte DB: Geblokkeerd (NAS) | — | productie-DB in CI |
| 009c | compat-overzicht release/commit + gebruikersimpact | onvolledig | `/admin/version` (manifest), 'Nieuw in deze versie' (`notices.py`), versiecontrole (`updates.py:270`) | compat-matrix (INVENTARIS §1.3) uitbreiden met MSO/Hub-versies na P3-09 | — |
| 009d | MediaGuard-deploy geblokkeerd als MSO-read-model incompatibel | ontbrekend | — | P3-09 laat CI (en dus `release.yml`, dat `ci.yml` aanroept) falen | — |
| — | tag-only rollback blokkeren (EX15) | onvolledig | README eist eerst appdata-snapshot (`README.md:395-415`); geen codecontrole (`db.py:162-164`) | **P3-04**: bij onbekende/nieuwere migraties in `schema_migrations` of `schema_versie`: geen migratie, MediaGuard zet zelf de noodstop aan + duidelijke melding (geen startweigering die de UI onbereikbaar maakt) | automatisch database terugzetten |

### B04-010 — Proefmigratie en productiecontrole (F6)

| Rij | Eis | Klasse | Bewijs | Wat mist / minimale wijziging | Wat NIET mag |
|---|---|---|---|---|---|
| 010a | nieuwe integraties eerst schaduwen; echte tests pas na back-up en akkoord | onvolledig | schaduwmodus bestaat voor de forkmigratie (`deploy/truenas/env.example` `MG_*_SHADOW_MODE`, `migration/`); connector is read-only (= schaduw) | Hub-schaduwfase; akkoord gebruiker | echte downloads zonder back-up/akkoord |
| 010b | schaduw-leeslogs, synthetische afleveringen, echte download met limieten, canary 24–48 u | onvolledig | fakes voor CI (`tests/integration/fakes`, fake indexer/SAB); echte download/canary: **Geblokkeerd (NAS)** | — | — |
| 010c | go/no-go-rapport | ontbrekend | — | Hub stelt het op uit connector-evidence (andere repo) | — |
| 010d | na terugrol downloads en bestandsintegriteit correct | onvolledig | terugdraaien via appdata-snapshot + oude tag (`README.md:395-415`); journaal/quarantaine (fault injection 8/8 in containers) | NAS: **NIET GEVALIDEERD** | — |

---

## C. Contracten 1.1 (PLATFORM_CONTRACTEN.md)

| ID | Contracteis | Klasse | Bewijs | Wat mist / minimale wijziging | Wat NIET mag |
|---|---|---|---|---|---|
| C-01 | §1/§2 standaard Custom App, één image | bestaand | `ghcr.io/lvos04/mediaguard-core:1.2.2-aio` (`README.md:112-148`, ADR-012 `docs/DECISIONS.md:95-111`) | `deploy/truenas/CUSTOM_APP.md` met de exacte formuliervelden (P3-07) | — |
| C-02 | geen YAML/compose als installatiemethode | onvolledig (strijdig) | YAML-alternatief in `README.md:150`, `docs/INSTALL_TRUENAS.md` §5/6.2/8.2, `deploy/truenas/compose.yaml:1-11` | README/INSTALL markeren als "alleen ontwikkeling/CI/integratietests, niet de platformmethode" (P3-07); compose blijft (gebruikt door `tests/integration/stack/run.sh:2-3`) | compose verwijderen (breekt stacktests); bestaande gebruikers zonder overstappad dwingen |
| C-03 | één datamap `/appdata` | afwijkend (bewust) | `/config`, `/work`, `/data` (`aio.py:181-183`) | ADR-0013: `/config` = appdata; `/data` (media) en `/work` (staging/quarantaine) zijn nodig; manifest `storage` met drie mounts | media naar `/appdata` verhuizen |
| C-04 | ingebouwde PostgreSQL | afwijkend (bewust) | ADR-003 SQLite-WAL (`docs/DECISIONS.md:15-23`) | geen migratie (bestaande repo, alleen hiaten) — ADR-0013 | big-bang-migratie |
| C-05 | §5 `/healthz`, `/readyz`, foutformaat, Idempotency-Key, ETag, cursor ≤ 200, ISO-tijden | ontbrekend (probe 404) | INVENTARIS §4.4 | P3-01 (alleen nieuwe routes) | bestaande routes wijzigen |
| C-06 | §6 connectorcontract | ontbrekend | probe: 8 × 404 | zie §D | gesimuleerd succes |
| C-07 | §7 event-envelope, outbox, dedupe | onvolledig | outbox bestaat (id monotoon) | P3-03 `GET /connector/v1/events?cursor=`: `event_id` = UUIDv5 van `mediaguard:<id>`, `source_event_id` = `id`, `classification: internal`, payload geredigeerd | push zonder service-token |
| C-08 | §3 enrollment | n.v.t. in v1 | contract §3 laatste punt: MediaGuard-`X-Api-Key` voert de gebruiker in de Hub-UI in | geen enrollment-client in P3 (Hub pollt); pas nodig als MediaGuard naar `/api/v1/events/inbox` moet pushen | sleutel in Custom App-formulier |
| C-09 | §4 401 `{code:"unauthorized"}`; nooit open toegang | onvolledig | 401 `{"detail": …}`; ingetrokken → 401 (probe) | contractvorm alleen op connectorroutes | — |
| C-10 | §1 base-images `@sha256`; release-digests; SBOM | ontbrekend | INVENTARIS §12 | P3-08 | — |
| C-11 | §9 manifest `deploy/manifest/mediaguard.manifest.yaml` | ontbrekend | — | P3-07: `install.method: custom_app`, image `ghcr.io/lvos04/mediaguard-core` tag `1.2.2-aio` digest `sha256:14e540ba…52dc6`, `storage` (config/work/data), poorten 30080/38989/38990/37878/37879 met `host_network:false` (of host network mét `privilege_reason`), `gpu: intel_render` optioneel, parameter `MG_HOST` met `source: wizard` (D03), `upgrade.tag_only_rollback_safe: false` | voorbeeldpool `tank` of een IP als waarde; `compose`-veld |
| C-12 | §10 repo-indeling | onvolledig | ontbreken: root-`.env.example`, `docs/openapi/`, `docs/adr/` (nu gestart), `VERSION`, `Makefile` | P3-07 (laag); `VERSION`/`Makefile` alleen als nuttig | bestaande indeling (`docs/DECISIONS.md`, `deploy/aio`) omgooien |
| C-13 | §8 alleen de executor heeft een TrueNAS-schrijfkey; geen oude REST | onvolledig (strijdig) | MediaGuard kan zelf `truenas:apikey` gebruiken voor poolstatus (`storage_forecast.py:125-151`, `GET /api/v2.0/pool`) en optionele snapshot vóór vervanging (`recovery/zfs.py:18-50`, REST v2.0); standaard niet ingesteld | niet uitschakelen (integriteitsvangnet), wel documenteren: in platformmodus geen schrijfkey in MediaGuard; poolstatus bij voorkeur van de Hub (P3-13); REST-uitfasering van TrueNAS als risico | functie verwijderen; executor-sleutel aan MediaGuard geven |
| C-14 | §1 geen betaalde API-overage | onvolledig | AI-adviseur kan betaalde Claude/OpenAI-API gebruiken (`ai/settings.py:59-62`), standaard **uit**, maandlimiet € 5 (`ai/settings.py:98-101`) | in platformmodus uit laten; Hub toont waarschuwing als het aan staat | functie stil uitschakelen (gebruikerskeuze) |
| C-15 | §1 Jellyfin-identiteit blijft "Jellyfin" | bestaand | soort `jellyfin` via Emby-compatibele client (`mediaserver/service.py:42-48`) | — | — |
| C-16 | §11 EVIDENCE.json | bestaand (nu) | `docs/platform-v2/EVIDENCE.json` | — | `Gereed`/`Geslaagd` |

---

## D. Hub-adapter: bestaande routes vs connectorcontract (§6) en enrollment (§3)

| Connector-endpoint | Bestaande route(s) die de Hub-adapter **nu** kan gebruiken | Sleutel | Wat echt ontbreekt | P3 |
|---|---|---|---|---|
| `GET /connector/v1/discover` | `GET /api/v1/health` (versie), `GET /api/v1/auth/me` (rol), `GET /api/v1/admin/version` (schema + manifest), route-probing zoals MSO | viewer | machineleesbare `capabilities/permissions/limits`; OpenAPI is admin-only | P3-01 |
| `GET /connector/v1/health` | `/api/v1/health`, `/api/v1/status`, `/api/v1/health/summary`, `/api/v1/admin/health`, `/api/v1/monitor` | viewer | `healthy/degraded/down/unknown` + `observed_at` + `reason_code` + `checks[]` | P3-01 |
| `GET /connector/v1/items` | wanted `/api/v1/wanted`; aanvragen `/api/v1/delivery/requests`; downloads `/api/v1/downloads/health`, `/downloads/failures`, `/instances/{id}/queue`; AV1 `/api/v1/encoding/jobs`, `/encoding/resources`, `/app/status`; opslag `/api/v1/storage/forecast`, `/storage/ledger`, `/quarantine/usage`; incidenten `/api/v1/attention`; meldingen `/api/v1/notifications`; back-ups `/api/v1/backups`, `/backups/bundle` | viewer | uniforme `cursor/limit/ETag/updated_at`, `owner_app` | P3-01, P3-06, P3-12 |
| `POST /connector/v1/propose` | alleen dry-runs: `/wanted/backfill-plan`, `/encoding/plan/{id}/simulate`, `/policy/simulate`, `/settings/simulate`, `/provisioning/plan` | viewer/operator | `preview_hash`, `resource_etag`, `diff`, `blast_radius`, `rollback_recipe` | P3-10 (pas na P3-01/02) |
| `POST /connector/v1/apply` | bestaande acties: `POST /api/v1/system/emergency-stop` (alleen aan), `POST /api/v1/wanted/{id}/search` — de Hub roept ze aan **na** eigen policy/goedkeuring (contract §6: adapter op bestaande routes) | operator met scopes | verificatie van een ondertekende `ExecutionRequest` in MediaGuard zelf (verdediging in de diepte) | eerst P3-02 (scopes); `apply` = `501 capability_unavailable` tot Hub-policy-sleutel beschikbaar is |
| `POST /connector/v1/rollback` | eigenaar-routes bestaan maar zijn R2/R3 en mens-gestuurd: `/quarantine/{id}/restore` (operator), `/backups/{name}/restore` (admin), `/migration/rollback` (admin) | — | — | `501 capability_unavailable` (bewust) |
| `GET /connector/v1/evidence/{id}` | `/api/v1/audit` (viewer), `/api/v1/jobs/{id}`, `/wanted/{id}/diagnosis?cached=true` | viewer | geredigeerde evidence per id | P3-01 |
| `GET /connector/v1/events?cursor=` | `/api/v1/events/recent` (geen cursor), `/api/v1/events/stream` met `Last-Event-ID` | viewer | envelope + cursor | P3-03 |
| `/healthz`, `/readyz` | `/api/v1/health` (liveness) | — | beide routes + webproxy | P3-01 |
| enrollment `POST {HUB_URL}/api/v1/services/enroll` | n.v.t.: MediaGuard koppelt via `X-Api-Key` die de gebruiker in de Hub-UI invoert (contract §3) | — | alleen nodig voor push naar de Hub-inbox | geen (follow-up) |

**Gebruik van X-Api-Key-rollen door de Hub (bestaand):** sleutel `hub` (viewer) voor alles hierboven; sleutel
`hub-acties` (operator) alleen voor noodstop-aan en één herzoek, en pas na P3-02 met scopes. Acties staan in de
MediaGuard-audit als `apikey:<naam>`. **Events/meldingen door de Hub (bestaand):** pollen op
`/api/v1/events/stream` met `Last-Event-ID` (watermark = outbox-`id`) of `/api/v1/notifications?before_id=`,
plus `/api/v1/attention` voor open incidenten; dedupe op `outbox_events.id`. De webhook-meldingsbron
(`notify/channels.py:142-145`) is geen geschikte Hub-inbox (geen token, geen envelope).

---

## E. Installatie- en app-stappen (I-/A-documenten)

Evidence-eigenaar van de I-stappen is `jelly-leon-platform` (EXCEL_TAKEN.json daar), van APP-04/05/09/11/12/18
`appstore-en-fork`. Hier staat alleen wat MediaGuard voor die stap levert.

| ID | Stap | MediaGuard-ondersteuning | Klasse | Bewijs | NAS |
|---|---|---|---|---|---|
| I03-03-01 | release en draaiende versie bepalen | `/api/v1/admin/version` (viewer) geeft draaiende versie + schema; release: INVENTARIS §1 | bestaand | `maintenance/api.py:759` | NIET GEVALIDEERD |
| I03-03-02 | datamappen, uid, vrije ruimte, `/dev/dri` | `/api/v1/admin/health` (`write_probe`, opslag), GPU-diagnose in AV1-instellingen; bootstrap wijzigt nooit bestaande mediarechten (`bootstrap.py:4-6`) | bestaand | `maintenance/api.py:600` | NIET GEVALIDEERD |
| I03-03-03 | back-up incl. forks | core/encoder/bundel bestaand; forks apart | onvolledig | B04-006 | NIET GEVALIDEERD |
| I03-03-04 | veilige update naar 1.2.2 (pin, stop-grace ≥ 60 s) | `README.md:126` (stop grace 60 s, `MG_STOP_TIMEOUT`); rooktest | bestaand | release-run | NIET GEVALIDEERD |
| I03-03-05 | Seerr/Prowlarr/clients | Seerr LAN-adres (1.2.1), SAB-categorieën (`downloads/sab_categories.py`, `test_sab_categories.py`), Prowlarr-sync | bestaand | `test_seerr_reachable_url.py` | NIET GEVALIDEERD |
| I03-03-06 | AV1 + noodstop | INVENTARIS §10 | bestaand | B04-001a | NIET GEVALIDEERD (A310) |
| I03-03-07 | Android-meldingen | koppelen/push/testmelding/intrekken (`mobile.py:539-634`) | bestaand | `docs/ANDROID.md` | NIET GEVALIDEERD (toestel) |
| I03-03-08 | Hub-API: viewer-sleutel, read-only probe, correlatie | sleutel + probe bestaand (INVENTARIS §4.4); correlatie ontbreekt | onvolledig | B04-004b | NIET GEVALIDEERD |
| I16-16-01 | Prowlarr read-only inventaris | `/api/v1/provisioning/status`, `/provisioning/items` (viewer); geen sleutelexport (bewust) | bestaand | `provisioning/api.py:244,417` | NIET GEVALIDEERD |
| I16-16-02 | sync-eigenaarschap (MediaGuard: tags `anime`/`normal` + apps) | `provisioning/prowlarr_sync.py:1-4` (alleen eigen tags), drift-check | bestaand | `test_provisioning*.py` | NIET GEVALIDEERD |
| I16-16-03/04 | Prowlarr-back-up; MSO-lus | n.v.t. voor MediaGuard (Prowlarr/TrueNAS resp. MSO) | — | — | — |
| I16-16-05 | health-alerts | Prowlarr-verbindingsfout in inrichting/aandacht | onvolledig | `provisioning/api.py:574` (test) | NIET GEVALIDEERD |
| I16-16-06 | upgrade-gate Prowlarr ↔ forks | fakes in `test_provisioning_fakes.py` | onvolledig | — | NIET GEVALIDEERD |
| I19-19-01 | Seerr → vier forks via LAN-IP | bestaand (1.2.1) | bestaand | `CHANGELOG.md:42-46`, `test_seerr_reachable_url.py` | NIET GEVALIDEERD |
| I19-19-02 | anime-routering | bestaand | bestaand | `test_anime_routing.py`, `tests/contract/seerr/RESULTS.md` | NIET GEVALIDEERD |
| I19-19-03 | levenscyclus, verwijderd komt niet terug | bestaand | bestaand | B04-008d | NIET GEVALIDEERD |
| I19-19-04 | Seerr-back-up | n.v.t. MediaGuard (cutover-rollback bewaart Seerr-instellingen: `/seerr/cutover/rollback`) | — | `seerr/api.py:225` | — |
| I19-19-05 | read-only aanvraagstatus in Hub/speler | `/delivery/requests`, `/seerr/my-requests` (viewer) | bestaand (bron) | `delivery/api.py:35` | NIET GEVALIDEERD |
| I19-19-06 | update, geen profieloverschrijving | `test_profile_left_alone` | bestaand | B04-008a | NIET GEVALIDEERD |
| I20-20-01/04 | Bazarr-inventaris/back-up | n.v.t. MediaGuard | — | — | — |
| I20-20-02 | MediaGuard-ondertiteladapter vs Bazarr | Bazarr-zoekopdracht max. 1×/dag per titel; OpenSubtitles-terugval `.nl.srt`, nooit overschrijven | bestaand | `docs/INTEGRATIONS.md:49-71`, `mediaserver/subtitles.py` | NIET GEVALIDEERD |
| I20-20-03 | SRT/ASS/anime/DV-retentietest | — | ontbrekend | — | NIET GEVALIDEERD (echte media/clients) |
| I20-20-05 | decommission-gate Bazarr | pariteit niet aangetoond → Bazarr **behouden** (A09) | ontbrekend | — | — |
| APP-18 (A18) | MediaGuard behouden; inventaris 1–5 | dit document + INVENTARIS (2: poortstandaarden; 3: §5/§8; 4: §6 afhankelijkheden MSO/Android/Hub/Seerr/forks/Bazarr/Emby/Jellyfin/Prowlarr); 1/2/5 op de NAS | onvolledig | — | NIET GEVALIDEERD |
| APP-11 (A11) | Prowlarr behouden; geen cyclische lus of tag-drift | MediaGuard wijzigt alleen eigen tags | bestaand (MediaGuard-deel) | `prowlarr_sync.py:1-4` | NIET GEVALIDEERD |
| APP-12 (A12) | Seerr behouden via MediaGuard | I19 | bestaand (MediaGuard-deel) | — | NIET GEVALIDEERD |
| APP-09 (A09) | Bazarr behouden tot pariteit | I20 | — | — | — |
| APP-04/05 (A04/A05) | Readarr / FlareSolverr | geen MediaGuard-koppeling (MediaGuard beheert alleen Sonarr/Radarr; FlareSolverr hoort bij Prowlarr) | n.v.t. | — | — |

---

## F. Integrale E2E-scenario's met een MediaGuard-deel

| EX | Scenario | MediaGuard-deel | Klasse | Bewijs |
|---|---|---|---|---|
| EX01 | MediaGuard offline | Hub moet `unknown` tonen (Hub); MediaGuard: hookwacht/fail-policy forks | onvolledig (Hub) | `hookguard.py`, `deploy/truenas/env.example` `FAIL_POLICY=open` |
| EX02 | MSO offline | MediaGuard heeft geen MSO-afhankelijkheid | bestaand | geen MSO-aanroep in `apps/core/mediaguard` (alleen label `source: mso`) |
| EX03/EX04 | Seerr-serie/anime naar juiste fork | bestaand | bestaand | B04-008a, I19-19-02 |
| EX05 | verkeerd afleveringsbestand | quarantaine, geen blinde delete | bestaand | `test_delivery_control.py::test_wrong_file_never_removed_without_safety_net` |
| EX06 | noodstop tijdens jobs | MediaGuard-kant bestaand; via Hub/MSO: Hub ontbreekt, MSO respecteert (`compat.py` status) | onvolledig | B04-003d |
| EX07 | stilstaande A310-baan | melding + diagnose, geen herstartspam | bestaand | `test_encoding_lanes.py::test_gpu_idle_alert_after_30_minutes_once` |
| EX08 | afspelen tijdens AV1 | kijkpauze | bestaand (sim) | `test_mediaserver.py::test_watching_holds_encodes_and_marks_recent` |
| EX09 | VMAF-fout | fail-closed | bestaand | `test_kwaliteitsmeting.py` (nightly) |
| EX10 | HDR/DV op echte clients | — | Geblokkeerd (hardware) | `docs/AV1_DV_PLAYBACK_MATRIX.md` |
| EX11 | pool degraded | pauze opruimen bij gemeten degraded; onbekend = geen pauze | onvolledig | B04-008a / P3-13 |
| EX12 | back-up stale | melding `backup.failed`, verouderd > 48 u (`maintenance/api.py:801-835`); R2/R3-gate = Hub | onvolledig (Hub) | `test_onderhoud.py::test_failed_backup_leaves_no_partial_files_and_notifies` |
| EX14 | snapshot-restore naar sandbox | bundel-restore naar doelmap | onvolledig | B04-006d |
| EX15 | schema-rollback | geen codecontrole | onvolledig | P3-04 |
| EX16 | sleutel ingetrokken | 401, geen open toegang | bestaand | probe; `test_api_keys.py::test_revoked_key_rejected_immediately`, `::test_expired_key_rejected` |
| EX37 | anime absolute nummering | identiteitsengine | bestaand | `test_identity.py`, `test_c10_id_match_import.py` |
| EX42 | Prowlarr-lus | MSO-eigenaar; MediaGuard wijzigt alleen eigen tags | n.v.t. | — |

---

## G. Controle: installeerbaar als standaard Custom App (alles-in-één)?

**Ja**, sinds ADR-012: één image `ghcr.io/lvos04/mediaguard-core:1.2.2-aio` (digest `sha256:14e540ba…52dc6`),
gewone formuliervelden, geen verplichte variabelen, geheimen door de app zelf in `/config/secrets`, start als root
alleen voor mappen/rechten en daarna 568, `HEALTHCHECK`, rooktest in de release. **Afwijkingen t.o.v. contract 1.1**
(vastgelegd in ADR-0013): drie mounts i.p.v. één `/appdata` (C-03), SQLite i.p.v. PostgreSQL (C-04), vijf poorten
(30080 + vier forkpoorten voor Seerr) en het advies *Host Network aan* (contract: `host_network:false` tenzij reden;
zonder host network is `MG_HOST` = NAS-adres uit de wizard nodig), geen `/healthz`/`/readyz`, base-images niet op
digest, YAML-alternatief nog in README/INSTALL, geen `CUSTOM_APP.md`/§9-manifest. MediaGuard staat niet in de
poorttabel van contract §2 (contractissue): bestaande 30080 blijft.

---

## H. Wat NIET mag (eigenaarschap en invarianten)

1. Geen tweede wanted-/zoek-engine, Seerr-sync, Prowlarr-sync of back-upsysteem voor MediaGuard-data in de Hub.
2. Geen directe SQL op of bestandsmutatie in `/config`, `/work`, `/data` vanuit de Hub of andere apps; de executor
   doet alleen TrueNAS-acties (snapshot/app), nooit MediaGuard-database- of mediabestandsacties.
3. Hub roept geen opruim-/verwijder-/blokkeerroutes aan (`/downloads/cleanup/run`, `/quarantine/purge`,
   `/quarantine/bulk-delete`, `DELETE /instances/{id}/queue/{id}`, `/storage/twins/…/remove`), geen
   `retry-relaxed`, geen bulkacties, geen instellingen, geen migratie/cutover/restore.
4. De noodstop wordt door de Hub alleen **aan**gezet; opheffen doet een mens in MediaGuard (of later via R2-goedkeuring).
5. Bestaande integriteit/rollback/monitoring (INVENTARIS §8–§10) niet dubbel bouwen, vervangen of uitschakelen; ook
   de optionele ZFS-snapshot en de AI-adviseur niet stil uitzetten.
6. Bestaande route- en foutvormen niet wijzigen (MSO 1.6.0, web, Android hangen ervan af); alles nieuws onder
   `/connector/v1` of nieuwe headers die oud gedrag ongemoeid laten.
7. Geen voorbeeldwaarden (pool `tank`, IP-adressen, versies) als echte NAS-waarden.

---

## I. Geprioriteerde bouwlijst fase P3 (alleen hiaten)

| Prio | ID | Wat (minimaal) | Module / route / migratie | Dekt | Tests |
|---|---|---|---|---|---|
| P0 | P3-01 | read-only connector + health | nieuw `apps/core/mediaguard/connector/` (router `/connector/v1`: `discover`, `health`, `items`, `evidence/{id}`; `propose/apply/rollback` → `501 capability_unavailable`), `/healthz` + `/readyz` in core, doorzetten in `apps/web/server.mjs`; contractfoutformaat; ETag/cursor; `docs/openapi/mediaguard.openapi.json` + drifttest; geen migratie | B04-002, A-20/23/27/28, C-05/06/09 | unit + in-process probe + Playwright-loze API-e2e; vergelijking 5 datasets |
| P0 | P3-02 | scopes op API-sleutels | migratie `140_api_key_scopes.sql` (`scopes` TEXT NULL), `apikeys.py` + `auth.require`; scopes `emergency_stop:on`, `wanted:search`; UI-veld in sleutelbeheer | B04-003a, B04-009b | negatieve rechtentests (scope buiten lijst → 403, noodstop uit met `emergency_stop:on` → 403), NULL = oud gedrag (MSO-routes blijven werken) |
| P0 | P3-03 | events in envelope met cursor | `/connector/v1/events?cursor=` over `outbox_events` (UUIDv5, `source_event_id`, `classification`, redactie) | C-07, B04-002/007 | dedupe/herhaling, cursor stabiel, geen geheimen |
| P1 | P3-04 | schemabescherming (EX15) | `db.py` + encoder-migratiecontrole: nieuwer/onbekend schema → geen migratie, noodstop aan, melding | A-15, B04-009 | ouder image op nieuwere fixture-DB |
| P1 | P3-05 | correlatie-ID | middleware `X-Correlation-Id` → antwoordheader + `audit.correlation_id` bij sleutelmutaties | B04-004b, I03-03-08 | header in/uit, audit-rij |
| P1 | P3-06 | herstelrapport | `items?type=backups` (core/encoder/bundel/fork-back-ups, vingerafdruk, leeftijd, dekking) | B04-006 | rapport bij ontbrekende fork-back-up = `degraded`, nooit groen |
| P1 | P3-07 | Custom App-documentatie | `deploy/truenas/CUSTOM_APP.md`, `deploy/manifest/mediaguard.manifest.yaml`, root-`.env.example`, README §3.3 + INSTALL §5 markeren als niet-platform | C-01/02/11/12 | manifest-validatie (geen `compose`, geen `latest`, digest aanwezig) |
| P1 | P3-08 | supply chain | base-images `@sha256` (core, web, encoder, forks), `release/digests.json`, SBOM in `release.yml` | C-10, B04-009b | CI-check op `@sha256` in Dockerfiles |
| P1 | P3-09 | MSO-contracttest | `tests/contract/mso/` (gepinde MSO-routelijst) in `ci.yml` | B04-009a/d | faalt bij ontbrekende route/rol |
| P2 | P3-10 | begrensde platformpauze AV1 + propose/apply | eigenaarlogica in `encoding/` (`Platform:`-reden, TTL, frequentiegrens); connector-propose/apply met `Idempotency-Key` (tabel in migratie 140) zodra Hub-policy-sleutel er is | B04-005, A-22 | kijkpauze blijft staan; TTL loopt af; frequentie |
| P2 | P3-11 | Jellyfin-padbehoud + events | simulatortest pad/ItemId na AV1-vervanging; outbox `mediaserver.refresh.*` met correlatie; melding bij padwissel | B04-007 | Jellyfin-simulator |
| P2 | P3-12 | kleine UX/testhiaten | `owner_app` in connector-incidenten; 768-viewport + toetsenbord-e2e | B04-008c, A-05, A-11 | e2e |
| P2 | P3-13 | poolstatus in platformmodus (ADR eerst) | Hub-gemeten poolstatus als extra invoer; onbekend → opruimen pauzeren, alleen als de Hub gekoppeld is | B04-008a, EX11, C-13 | onbekend → pauze; los gebruik ongewijzigd |

Buiten P3 van deze repo: Hub-adapter en -schermen (`ai-server-manager`), integrale E2E (EX-matrix), echte NAS-,
GPU-, tv- en accounttests, canary/go-no-go, enrollment/push naar de Hub-inbox.

---

## K. Controle (tweede lezing)

Onafhankelijke, sceptische controle op 2026-11-09 van §A–§I hierboven (P0-commits `341a0cc`, `7ad00d6`,
`3512931`). **Geen productcode gewijzigd.** De rijen hierboven blijven staan zoals de eerste lezing ze schreef;
**waar deze sectie afwijkt, geldt deze sectie.** Alles hieronder is code- en testlezing plus unit- en
in-process-tests in deze bouwomgeving. Het zegt niets over de echte NAS, de Arc A310, echte clients of echte
accounts: die blijven **NIET GEVALIDEERD**.

### K.1 Werkwijze en uitkomst

- **Alle 185 classificatierijen** nagelopen: §A 39, §B 76, §C 16, §D 10, §E 28 en §F 16.
- **Verwijzingen mechanisch opgelost.** Alle 226 unieke `bestand:regel`- en `test::naam`-verwijzingen zijn opgelost tegen `git ls-files`. Per testverwijzing is de functienaam in het testbestand gezocht (met een hulpscript in de scratchpad, niet gecommit). Uitkomst:
  - één foute regelverwijzing: `werk-ia.spec.ts:170`, terwijl dat bestand 103 regels heeft (zie K.2, A-11);
  - alle andere verwijzingen bestaan en wijzen naar de beschreven code;
  - verwijzingen naar nog te bouwen bestanden (`deploy/manifest/…`, `140_api_key_scopes.sql`, `docs/openapi/…`, `release/digests.json`) bestaan terecht niet.
- **§D tegen de routetabel.** De routetabel is opnieuw gegenereerd met `scripts/platform-v2-routes.py --json` (432 routes). Alle routes uit §D bestaan, met de genoemde minimale rol. Alleen de namen van padparameters verschillen.
- **MSO-kruiscontrole zelf herhaald** door de RouteSpecs uit `media-source-optimizer/apps/core/mso/features/mediaguard/compat.py` te ontleden (alleen gelezen). Uitkomst: 17 van 19 exact, `auth/me` = ingelogd, en voor `openapi.json` verwacht MSO geen rol terwijl MediaGuard admin eist. Daarmee bevestigd.
- **Externe feiten opnieuw gemeten:**
  - `docker buildx imagetools inspect ghcr.io/lvos04/mediaguard-core:1.2.2-aio` geeft `sha256:14e540ba9cad…3a52dc6` (gelijk aan de eerste lezing);
  - `git ls-remote --tags origin v1.2.2` geeft `v1.2.2^{}` = `30f7415`;
  - `gh run view`: release-run `42554745125` is `success` op `30f7415`, CI-run `42587401041` is `success` op `60b50ab`, en CI-run `42593595185` (docs-commit `3512931`, bij P0 nog in de wachtrij) is **`success`**.
- **Weerleggingspoging per 'ontbrekend'/'onvolledig'-rij.** Elke rij is met NL- en EN-synoniemen doorzocht in code, tests, migraties, docs en git-historie. Zoektermen waren onder meer:
  - `healthz|readyz`, `Idempotency`, `cursor`, `ETag|If-None-Match`, `correlation|X-Request-Id`;
  - `scrub|temperat|quiet|stilte`, `pauzeren|hervatten|playback_hold`, `UserData|Played|watch`;
  - `openapi`, `@sha256`, `shadow|schaduw`, `offsite`, `attachments|ondertitels`, `mso-`, `downgrade|nieuwer schema`, `"code":`.

**Uitkomst in cijfers:**

| Uitkomst | Aantal | Waar |
|---|---|---|
| Rijen bevestigd | **177** | waarvan 19 met aangevuld of verbeterd bewijs (K.3) |
| Claims weerlegd of gecorrigeerd | **8** | K.2 |
| Correcties op de bouwlijst | 1 | K.5 |
| Aanscherpingen van de bouwlijst | 7 | K.5 |
| Nieuw bouwitem | 1 (P3-14) | K.5 |
| Nieuwe rijen | **24** | K.4: eisen, bestaande functies, hiaten en eigenaarschap die in de matrix ontbraken |

Geen 'bestaand'-claim bleek onterecht. Wel waren drie 'ontbrekend'-claims te zwaar: A-35, 004b en de resourcedruk in 005a. Bij I20-20-03 was de retentiecode al aanwezig. Zonder correctie was bestaand werk opnieuw gebouwd. Daarnaast zijn twee echte hiaten gemist: de download-opruimer volgt de poolstatus niet, en een cursor kan verlopen.

### K.2 Gecorrigeerde rijen (weerlegd)

| Rij | Eerste lezing | Gecorrigeerd | Bewijs | Gevolg voor P3 |
|---|---|---|---|---|
| A-11 | onvolledig; bewijs `werk-ia.spec.ts:170` (axe-contrast); "geen volledige toetsenbord-e2e" | **onvolledig** (blijft). De verwijzing klopt niet: de axe-contrasttest staat in `apps/web/e2e/mediaguard.spec.ts:170`. Er **is** een toetsenbord-e2e: `mediaguard.spec.ts:131` (skip-link, `/` zoeken, `g s`, sneltoetsendialoog `?`/Esc op `:143-146`). Wat ontbreekt: toetsenbordgebruik per onderdeel (formulieren, dialogen). | regels gelezen; het bestand `werk-ia.spec.ts` heeft 103 regels | P3-12 breidt de bestaande test uit; geen nieuwe suite |
| A-35 | ontbrekend (bewijs "—") | **onvolledig**. Contracten bestaan als docs: `packages/contracts/README.md:1-7` (fork-hooks, encoder-bridge, instances) en `docs/APP_API.md` (app-API). OpenAPI wordt al in tests gecontroleerd: core `apps/core/tests/test_api_keys.py:194` (securitySchemes) en `test_beveiliging.py:660` (alleen admin); encoder `services/encoder/tests/test_api.py:86` (routes in OpenAPI). Wat ontbreekt: een geëxporteerde `docs/openapi/mediaguard.openapi.json` met drifttest, en de connectorschema's. | K.7: deze tests zelf gedraaid | P3-01 ongewijzigd; aansluiten op de bestaande contractdocs, geen tweede contractmap |
| 004b | ontbrekend (HTTP) | **onvolledig**. Binnen MediaGuard gaat een correlatie-ID al over de HTTP-grens core→AV1-engine: `apps/core/mediaguard/encoding/service.py:608` (body `correlation_id: mg-enc-<id>`). De engine bewaart die en dedupliceert erop (`services/encoder/app/mediaguard/service.py:392-397`, `services/encoder/app/mediaguard/api.py:47`). Wat ontbreekt geldt alleen voor externe aanroepers: geen header in of uit, en geen koppeling aan `audit.correlation_id`. | code gelezen | P3-05 beperkt tot de header plus audit; de body-correlatie core↔engine niet vervangen |
| 005a | "… voor back-up, ZFS-scrub, resourcedruk: ontbrekend" | **onvolledig**. Resourcedruk is **bestaand**: CPU-budget, temperatuurgrens en tijdvenster staan in `apps/core/mediaguard/encoding/resources.py:31-69`; core zet ze door naar de engine. De planner van de engine start geen nieuwe encode boven de CPU-limiet of de temperatuurgrens (`services/encoder/app/planner.py:133-158`); tests in `services/encoder/tests/test_mediaguard_resources.py:29` en `services/encoder/tests/test_reviewfixes.py:460` (alleen in de nachtelijke volledige suite). ZFS-scrub wordt al **gemeten en getoond** (alleen met een TrueNAS-sleutel: `apps/core/mediaguard/storage_forecast.py:148-149`, `apps/web/components/mg/storage-trend.tsx:110`), maar pauzeert niets. NAS-back-ups kent MediaGuard niet. | K.7: deze tests zelf gedraaid (17 passed) | P3-10 alleen voor back-up/scrub (via de Hub, begrensd); geen nieuwe CPU- of temperatuurlogica |
| 005b | "`resume_lane` kan elke pauze opheffen" | **onjuist**. Hervatten via de engine (`POST /workers/{lane}/hervatten`) is fail-closed voor capability-pauzes (vraagt een verse meting) en voor opslagpauzes (`services/encoder/app/api.py:2073-2091` → 409). Een handmatige pauze en de kijkpauze kan het wel opheffen: er is geen eigenaarscontrole op het voorvoegsel. Er bestaat al een patroon "eigen pauze met voorvoegsel, alleen eigen pauze opheffen": `apply_playback_hold` (`services/encoder/app/mediaguard/api.py:122-146`), met tests in `services/encoder/tests/test_playback_hold.py:31`, `services/encoder/tests/test_onderhoud.py:90` en `services/encoder/tests/test_integraties.py:57`. | K.7: deze tests zelf gedraaid (7 passed) | P3-10 hergebruikt dit patroon (reden `Platform:` in `worker_config.pauze_reden`); geen tweede pauzemechanisme. De Hub gebruikt nooit `resume_lane`. |
| 009a | "geen geautomatiseerde test" (MSO) | **deels onjuist**. Het MSO-gebruikspatroon wordt al automatisch getest voor 3 van de 19 routes: `apps/core/tests/test_api_keys.py:74` (`X-Api-Key` zonder cookie of CSRF op `GET /wanted`, `POST /wanted/{id}/search` en `GET /auth/me`), plus `:96` (een sleutel komt nooit op adminroutes). Een test tegen de volledige MSO-routelijst ontbreekt; de klasse blijft onvolledig. | K.7: deze tests zelf gedraaid | P3-09 bouwt op deze test voort; geen tweede sleuteltestkader |
| I20-20-03 | ontbrekend | **onvolledig**. Behoud van sporen en bijlagen bij AV1 bestaat in code en tests. De engine vergelijkt het aantal ondertitelsporen en bijlagen (lettertypen voor ASS): `services/encoder/app/verificatie.py:115-126`, met tests `services/encoder/tests/test_beslissen.py:86-89` (CI-subset) en `services/encoder/tests/test_commandos.py:97` (alleen nachtelijk). De vrijgavecontrole in core eist audio, ondertitels, hoofdstukken en een volledige decode, en zet bij inhoudsverlies het origineel terug: `apps/core/mediaguard/recovery/release_check.py:42,146`, met tests `apps/core/tests/test_release_check.py:174` en `test_r2_track_safety.py:24`. Wat ontbreekt: een afspeel- en ondertitelselectietest met echte clients en echte SRT/ASS/anime/DV-bestanden. Die is **NIET GEVALIDEERD** (hardware/echte NAS). | K.7: deze tests zelf gedraaid | geen nieuwe retentiecode; alleen de echte-mediatest (Geblokkeerd) |
| EX11 (en 008a "NAS degraded") | "pauze opruimen bij gemeten degraded" | **te breed**. Alleen het opruimen van **quarantaine-originelen** pauzeert bij een gemeten degraded pool: `apps/core/mediaguard/recovery/api.py:151`, `recovery/release_check.py:336`, test `test_bewaking.py::test_degraded_pool_alerts_and_pauses_purge`. De **download-opruimer** staat standaard **aan** (`downloads/janitor.py:59`). Die verplaatst naar een prullenbak die na 7 dagen definitief leeg gaat (`:64`, `:367`), en eerder bij < 5 % vrije ruimte (`:372-378`). Hij controleert alleen de noodstop (`:602-607`), niet de poolstatus. Dat botst met 03_RISICO, Speciale blokkades: "bij gedegradeerde ZFS-pool … geen mutaties aan opslag". | `test_download_janitor.py` heeft geen pooltest (K.7) | nieuw hiaat **P3-14** (K.5), los van P3-13 (onbekende poolstatus) |

### K.3 Bevestigd, met aangevuld of verbeterd bewijs

| Rij | Aanvulling |
|---|---|
| A-09 | Per kaart al deels aanwezig. `KpiCard.staleNote` toont "bijgewerkt … geleden" als verversen mislukt (`apps/web/components/mg/domain.tsx:14-25`); de pagina Activiteit heeft `UpdatedAgo` (`app/(app)/activiteit/page.tsx:291-308`). Dit staat niet op elke kaart en toont geen bron: klasse onvolledig blijft. |
| A-16 | De bewaartermijnen zelf staan in `apps/core/mediaguard/maintenance/housekeeping.py:31-53` (instellingen) en `:105-175` (opruimen). Aggregatie: opslag wordt elk uur gemeten en één keer per dag bewaard (`storage_ledger.py:13`). |
| A-20 | Er is ook een unit-test voor admin-only: `apps/core/tests/test_beveiliging.py:660` (anoniem 401, operator 403, admin 200). |
| A-22 | Inkomende fork-hooks dedupliceren al op `idempotency_key` per instantie (`hooks/service.py:484-490`). Dit is inkomend en gebruikt geen HTTP-header. |
| A-23 | Herhalen via SSE met `Last-Event-ID` is al een cursor, maar begrensd op 500 events per verbinding (`api/events.py:26-31`). De outbox wordt na `maintenance.retention_days_events` (standaard 14) verwijderd (`maintenance/housekeeping.py:35,124`). Zie N-15. |
| A-24 | Ook een inlogrem (`security/limiter.py:187`) en de test `test_api_keys.py:142` (`test_rate_limit_per_ip`). |
| A-27 | Machineleesbare codes bestaan in meer domeinen dan genoemd, in de vorm `{"detail": {"code": "UPPER_SNAKE", …}}`: `encoding/api.py:153-155,191,403` (`LANE_FULL`, `ENCODER_UNAVAILABLE`, `GATES_FAILED`), `instances/api.py:168-172` (`WRONG_APP`), `dv/api.py:170,271`. Het contract vraagt `snake_case` op het hoogste niveau: dat komt alleen op de connectorroutes (P3-01). |
| 003a | Bewuste pauzes worden al ingedeeld als kijken, handmatig, stilte of noodstop (`attention.py:124-135`). **Let op:** het aandachtspunt `system.emergency_stop` kan met "Bewust zo gekozen" verborgen worden (`attention.py:58-60`). De Hub leest de noodstop daarom uit `GET /api/v1/status`, nooit alleen uit `/attention`. |
| 003d | `test_regels.py:161` (`test_noodstop_herstel_en_opruimen_slaan_over`) dekt ook de download-opruimer (`janitor.run_once` → `skipped`) en het herstel van downloads. De route via de Hub is nog niet gedekt. |
| 006a | De sleutelvingerafdruk staat al in `GET /api/v1/backups/bundle` (viewer, `maintenance/bundle.py:94-96`). Fork-back-ups zijn per instantie al leesbaar met een viewer-sleutel: `GET /api/v1/instances/{id}/backups` (`instances/manage.py:927-932`). Deze route hoort ook in §D bij "items/back-ups". |
| 006b | In de alles-in-één staan de vier forkconfigs onder `/config/<instance>` (`aio.py:306-307`). Een appdata-snapshot door de executor dekt de forks dus al. |
| 009b (allowlist) | Er is één naamgebonden uitzondering: alleen de sleutel `android-app…` mag het telefoonkanaal gebruiken (`mobile.py:459-468`). P3-02 moet die behouden. |
| A-15 / EX15 | Er is al een herstelmiddel: vóór elke update met migraties wordt een set gemaakt, en terugzetten gaat in twee stappen (`maintenance/preupdate.py:1-25`, `aio.py:1003-1016`): klaarzetten met `POST /api/v1/backups/{name}/restore` (admin, `maintenance/api.py:442`), de status staat in `GET /api/v1/backups/terugzetten` (`:404`). P3-04 detecteert alleen en verwijst daarnaar; het bouwt geen nieuw herstel. |
| 010a | Schaduwmodus per instantie bestaat in core (`delivery/engine.py:109-112`, `delivery/replace.py:67`). In de alles-in-één staat de schaduwmodus van de fork-hooks vast op `0` (`aio.py:302`); `MG_*_SHADOW_MODE` geldt alleen voor de compose-route. |
| C-14 | De harde maandstop is getest: `test_ai_advisor.py:344` (de volgende reservering past niet, dus geen API-aanroep). Ten opzichte van het contract blijft de klasse onvolledig: het gaat om de betaalde API, niet om inbegrepen tegoed. |
| I16-16-05 | Arr-health wordt per instantie in het Nederlands getoond, inclusief indexeruitval (`instances/health_nl.py:12-17`, `instances/service.py:83`). Mislukte downloads worden per indexer geteld (`downloads/failures.py:203-230`). |
| I03-03-07 | Het dagoverzicht staat in `notify/service.py:35-36`; de app toont offline-versheid (`apps/android/app/src/main/java/nl/mediaguard/app/data/Freshness.kt`). |
| EX02 | Klasse bestaand klopt: core roept MSO niet aan. Kanttekening: de download-opruimer beheert ook de downloadcategorieën `mso-*` (`downloads/janitor.py:451-462`). Zie N-17. |
| A-28 | Schema-tests per dienst bestaan al: zie K.2 A-35. Wat ontbreekt blijft export plus drifttest. |

### K.4 Nieuwe rijen (ontbraken in de matrix)

**B04-eisen zonder rij** (§1, per taak "e/f" en fase-entry/exit):

| ID | Eis | Klasse | Bewijs | Wat mist / P3 | Wat NIET mag |
|---|---|---|---|---|---|
| N-01 | §1 gedocumenteerde navigatie | bestaand | `apps/web/components/shell/nav.ts:16-25` (incl. sneltoetsen), pagina `/hoe-werkt-het`, `docs/UI_CONTROL_COVERAGE_MATRIX.md`, e2e sneltoetsendialoog `mediaguard.spec.ts:143-146` | — | navigatie naar de Hub verhuizen |
| N-02 | per taak "Foutafhandeling en bewijs": testscenario, image-versie, screenshots, geredigeerde request/response, tijden, audit/rollback, incidentlink | onvolledig | audit met `undo_hint` en `correlation_id` (`audit.py:11-41`); redactie (`secrets_store.py:18-38`); versie via `/admin/version`; meldingen met tijd. Er is geen bewijspakket per actie. | P3-01 `GET /connector/v1/evidence/{id}` bundelt dit per audit- of job-id. Screenshots van de echte NAS: Geblokkeerd. | sleutels of paden met geheimen in evidence |
| N-03 | fase-entry F0–F6: voorgaande gates geslaagd, bestaande gegevens en release vastgelegd, rollbackplan bekend | onvolledig | release vastgelegd (INVENTARIS §1, opnieuw gemeten in K.1); rollbackplan in `README.md:395-415` en `maintenance/preupdate.py`. De geïnstalleerde versie op de NAS is onbekend. | per P3-fase vooraf vastleggen in EVIDENCE (`opmerking`); het NAS-deel is Geblokkeerd | aannemen dat 1.2.2 draait |
| N-04 | fase-exit F0–F6: code review, back-up-/rollbackgate, **gebruikersreis op mobiel**, **negatieve rechtentest** | onvolledig | Mobiel: e2e op 360/390/412 px (`werk-ia.spec.ts:87`; `mediaguard.spec.ts:199` met touch) en de Android-app (`.github/workflows/android.yml`). Negatieve rechten: `test_api_keys.py:88` (viewer mag niet zoeken), `:96`, `:104`, en de probe (viewer → 403). Nieuwe P3-routes hebben geen UI in MediaGuard: de mobiele reis loopt via de Hub (andere repo). | Per P3-item is een negatieve test verplicht (viewer of verkeerde scope → 403, ingetrokken → 401). De mobiele reis is een Hub-e2e. Elke commit krijgt code review. | een mobiele reis als geslaagd melden zonder Hub-scherm |
| N-05 | B04-005e/f: externe storing niet als succes; fase-exit | ontbrekend (nieuw werk) | — | Valt de engine uit tijdens een platformpauze, dan wordt de status `unknown`, nooit "gepauzeerd ✓". Daarbij een scope-test (P3-10). | — |
| N-06 | B04-007e: storing niet als succes | onvolledig | de afspeeltest wordt herhaald (`mediaserver/service.py:315-325`) | Is Jellyfin offline, dan één keer `mediaserver.refresh.failed` met backoff, nooit "ververst" (P3-11) | — |
| N-07 | B04-008e: storing niet als succes | bestaand (kern) | een onbereikbare instantie telt nooit als verwijderd (`test_delivery_control.py::test_unreachable_instance_never_counts_as_removed`); een gedeeltelijke Seerr-lijst markeert niets als verwijderd (`::test_partial_seerr_listing_never_marks_requests_deleted`) | — | — |
| N-08 | B04-009e: storing niet als succes | onvolledig | de release stopt bij rode tests (`release.yml` roept `ci.yml` aan) | Is de MSO-routelijst niet leesbaar of rood, dan wordt de release geblokkeerd, nooit "compatibel" (P3-09) | — |
| N-09 | B04-010e: storing niet als succes | Geblokkeerd | — | Een echte download of canary pas na back-up en akkoord; een storing tijdens de canary = no-go, nooit "geslaagd" | — |

**Bestaande functies die in de matrix ontbraken** (niet opnieuw bouwen):

| ID | Functie / eis | Klasse | Bewijs | Wat mist / P3 | Wat NIET mag |
|---|---|---|---|---|---|
| N-10 | B04-006a/b/c en 03_RISICO ("ontbrekende externe backup"): kopie van de herstelbundel buiten de NAS | bestaand (functie) / **ontbrekend (leesbaar voor de Hub)** | `apps/core/mediaguard/notify/offsite.py:1-8,32`: map/share, WebDAV, S3 of e-mail; elke kopie wordt teruggelezen en met sha256 vergeleken. Test `test_notify.py:238` (K.7). De status is alleen voor admin: `GET /api/v1/notify/offsite` (`notify/api.py:204-206`). Een sleutel is nooit admin, dus de Hub kan dit niet lezen. | P3-06: offsite-status als read-only `items?type=backups` (soort doel, laatste geverifieerde kopie, leeftijd; nooit de doel-URL of geheimen) | een tweede offsite-systeem voor MediaGuard-data in de Hub; de offsite-route voor operators openzetten |
| N-11 | EX17 (prompt-injectie) en EX18 (beleidsklasse), MediaGuard-deel | bestaand | `apps/core/mediaguard/ai/prompt.py:1,30` (casusdata = onbetrouwbaar); `ai/policy_guard.py:1-10` (lokale code beslist, niet het model). Tests: `test_ai_advisor.py:154` (injectie → geen tools, sleutels of beleid), `:201` (verboden acties niet te registreren), `:269` (bounded_auto alleen uit de allowlist) | — | de AI van MediaGuard koppelen aan Hub-acties |
| N-12 | EX21 (AI-tegoed op), MediaGuard-deel | bestaand | harde maandlimiet `test_ai_advisor.py:344`; AI uit = alles werkt (`:53`); een providerstoring geeft nooit een fout (`:458`) | in platformmodus uit laten (C-14) | — |
| N-13 | EX22 (Hub offline), MediaGuard-deel | bestaand | eigen meldingskanalen los van de Hub (`notify/channels.py:23`: ntfy, Pushover, Telegram, Discord, Slack, webhook, e-mail) plus Android-push | — | MediaGuard-meldingen alleen nog via de Hub laten lopen |
| N-14 | EX48 (fork-upstream-update), analoog voor de eigen Sonarr/Radarr-forks | bestaand | gepinde upstream-SHA plus patchreeks (`docs/FORK_UPSTREAM_SYNC.md`); fork-images worden pas na geslaagde tests gebouwd en nooit overschreven (`.github/workflows/release.yml:7-19,176-229`) | de Jellyfin-fork zelf hoort bij `jelly-leon` | — |

**Gemiste hiaten en eigenaarschap:**

| ID | Eis | Klasse | Bewijs | Wat mist / P3 | Wat NIET mag |
|---|---|---|---|---|---|
| N-15 | C-07/§7: geen stille gaten bij polling (P3-03) | ontbrekend | De outbox wordt na 14 dagen verwijderd (`housekeeping.py:35,124`). SSE herhaalt maximaal 500 per verbinding (`api/events.py:26-31`). Er is geen melding als een cursor ouder is dan de oudste bewaarde regel. | In `/connector/v1/events` geeft een cursor vóór de oudste bewaarde id expliciet `cursor_expired` (Hub: `unknown` plus incident), nooit stil overslaan. Pagina's ≤ 200 met `next_cursor`. | de bewaartermijn van de outbox stil verlengen of inkorten |
| N-16 | EX11 en 03_RISICO: de download-opruimer volgt de gemeten poolstatus niet | ontbrekend | zie K.2, rij EX11 | **P3-14** (K.5) | de opruimer uitzetten of de standaard wijzigen (bestaand vangnet, ADR-0014) |
| N-17 | 02_DATA_EIGENAARSCHAP, rij "qBittorrent/SABnzbd-wachtrij: geen derde schrijver zonder conflictbeleid" | onvolledig | MediaGuard maakt alleen ontbrekende SAB-categorieën aan, bij inrichten of testen, en wijzigt of verwijdert nooit (`downloads/sab_categories.py:1-10`, `provisioning/api.py:81-90`, `instances/manage.py:529-532`). De download-opruimer behandelt ook `mso-<categorie>` als beheerd (`downloads/janitor.py:451-462`). MSO stuurt herstel-downloads standaard met die categorie (`media-source-optimizer/apps/core/mso/features/recovery/actions.py:36`, alleen gelezen). Er is geen test met MSO en het conflictbeleid is nergens vastgelegd. | ADR-0014 legt het huidige gedrag vast; afstemmen met MSO en de contracteigenaar | de opruimer voor `mso-*` stil uitzetten of uitbreiden zonder afspraak |

**I03 (§1, §3, §4), A18, EX13 en EX46:**

| ID | Stap / eis | Klasse | Bewijs (MediaGuard-kant) | Wat mist | NAS |
|---|---|---|---|---|---|
| N-18 | I03 §1 preflight: TrueNAS-release, bestaande installatie, poorten, herstelbare back-up met exacte pin, geen geheimen in logs | onvolledig | `/api/v1/admin/health` (`maintenance/api.py:600`), `/admin/version`, redactie in logs (`test_api_keys.py:171`) | De TrueNAS-kant (release, poorten, apps) hoort bij de Hub en de executor | NIET GEVALIDEERD |
| N-19 | I03 §3 acceptatie GO/NO-GO: start, auth, data blijft na herstart, poort, metrics, back-up en herstel, rolrechten | onvolledig | Containers: release-rooktest met herstart op een oudere DB (`scripts/aio-smoke.py`, run `42554745125`) | het go/no-go-rapport (B04-010c, Hub) | Geblokkeerd |
| N-20 | I03 §4 terugdraaien: oude gepinde image plus passend databestand of snapshot | bestaand (MediaGuard-kant) | `README.md:395-415`, `docs/INSTALL_TRUENAS.md` §8.1, de set van vóór de update plus terugzetten (`maintenance/preupdate.py`) | tag-only blokkeren = P3-04 | NIET GEVALIDEERD |
| N-21 | A18 A1: in de Hub als catalogusitem, met status en diep-link | bestaand (bron) | elk aandachtspunt heeft een `href` (`attention.py:47-51`); vaste webroutes (`nav.ts:16-25`) | het catalogusitem in de Hub (`ai-server-manager`/`appstore-en-fork`) | — |
| N-22 | A18 acceptatie: geteste noodstop, HDR/VMAF fail-closed | bestaand (unit/sim) | B04-001a. De VMAF- en HDR-tests draaien alleen in de nachtelijke volledige encodersuite (`ci.yml:101-125`), niet bij elke push. | — | NIET GEVALIDEERD (echte NAS/A310) |
| N-23 | EX13 hersteltest via OneDrive, MediaGuard-deel | n.v.t. hier | MediaGuard levert de versleutelde bundel en de offsite-kopie (N-10); de hersteltest via OneDrive hoort bij de NAS en de Hub | — | NIET GEVALIDEERD |
| N-24 | EX46 (dubbele eventlevering) | onvolledig | inkomende hooks worden gededupliceerd (`hooks/service.py:484-490`); uitgaand is er geen envelope | P3-03: een stabiele `event_id` (UUIDv5) zodat de Hub kan dedupliceren | — |

### K.5 Correcties en aanscherpingen op de bouwlijst (§I)

| Item | Correctie / aanscherping |
|---|---|
| P3-02 | **Correctie migratienummer.** `140_api_key_scopes.sql` valt buiten de vastgelegde reeksen in `apps/core/mediaguard/migrations/README.md:6-16`: 001–139 zijn verdeeld, en de API-sleutels horen bij de kern (001–019, `004_api_keys.sql`). Kies `005_api_key_scopes.sql`, of leg eerst een nieuwe reeks vast in die README (bijvoorbeeld 140–159 "platformkoppeling"). Hetzelfde geldt voor de idempotency-tabel van P3-10. Behoud de naamgebonden app-sleutel (`mobile.py:459-468`). |
| P3-03 | Daarbij `cursor_expired` (N-15) en een stabiele `event_id` (N-24). |
| P3-04 | Detecteren, dan noodstop aan en een melding. Herstel loopt via de bestaande set van vóór de update en terugzetten (K.3, A-15/EX15). Geen nieuw herstelmechanisme. |
| P3-05 | Alleen de header in en uit plus audit voor externe verzoeken. De body-correlatie core↔engine bestaat al (K.2, 004b). |
| P3-06 | Daarbij de offsite-status (N-10), fork-back-ups via de bestaande `GET /instances/{id}/backups`, en de vingerafdruk uit `bundle.status()`. |
| P3-09 | Uitbreiden vanaf `apps/core/tests/test_api_keys.py:74` (K.2, 009a). |
| P3-10 | Het patroon van `apply_playback_hold` hergebruiken (K.2, 005b). Resourcedruk bestaat al (K.2, 005a): alleen de pauze voor back-up en scrub komt via de Hub. |
| P3-12 | De bestaande toetsenbord-e2e `mediaguard.spec.ts:131` uitbreiden (K.2, A-11). |
| **P3-14 (nieuw, P1)** | **Download-opruimer en poolstatus.** Bij een **gemeten** degraded pool (`storage_forecast.pool_degraded`, dezelfde invoer als bij de quarantaine): niets definitief verwijderen, niets nieuws naar de prullenbak verplaatsen, en een melding geven. Zonder TrueNAS-sleutel (status onbekend) blijft het gedrag ongewijzigd; dat valt onder P3-13 en de bijbehorende ADR. Module: `downloads/janitor.py` (`run_once`). Dekt EX11, B04-008a en 03_RISICO. Tests: degraded → `trashed == purged == 0` plus een melding; gezond → ongewijzigd; de bestaande noodstoptest blijft groen. |

### K.6 Controle van EVIDENCE.json

- **Geen `Gereed`/`Geslaagd`:** bevestigd. NAS, hardware en accounts zijn overal NIET GEVALIDEERD.
- **`validatie`:** bevatte vrije tekst en is teruggebracht tot de waarden uit contract §11, net als in de tweede lezing van MSO. De toelichting staat nu in `opmerking`.
- **B04-002 t/m B04-009:** het nieuwe werk bestaat nog niet. Daarom is `validatie` nu `NIET GEVALIDEERD (…)` en niet "unit-getest": de geteste 1.2.2-delen staan in `opmerking`, niet als validatie van nieuw werk.
- **B04-010:** `Niet gestart` met een niet-lege blokkade is `Geblokkeerd` geworden (B04 §4: "bij een blocker … status Geblokkeerd + reden + eigenaar"). De eigenaar staat er nu bij.
- **B04-001:** blijft `Bezig`. Het repo-deel is klaar; alleen de geïnstalleerde tag (via de Hub-readback) is geblokkeerd.

### K.7 Tests die in deze controle zelf zijn gedraaid (Unit-getest; geen NAS)

| Commando | Uitkomst |
|---|---|
| `cd apps/core && nice -n 10 ../../.venv/bin/python -m pytest -q -rfEs -p no:cacheprovider` met `test_ai_advisor.py::{test_prompt_injection_cannot_cause_tools_keys_or_policy_changes, test_budget_persists_across_reboot, test_forbidden_actions_cannot_be_registered, test_without_key_ai_stays_off_and_nothing_breaks}`, `test_notify.py::test_offsite_path_copy_verify_and_once`, `test_api_keys.py::{test_operator_key_reads_and_searches_without_csrf, test_key_never_reaches_admin_routes, test_openapi_documents_api_key}`, `test_beveiliging.py::test_api_docs_only_for_admin`, `test_release_check.py::test_content_loss_triggers_automatic_restore`, `test_r2_track_safety.py::test_default_policy_requires_all_subtitles`, `test_bewaking.py::{test_degraded_pool_alerts_and_pauses_purge, test_pool_not_configured_means_no_pause}`, `test_optimizations.py::test_schema_etag_and_theme_in_me`, `tests/test_download_janitor.py` | **42 passed** in 7,1 s (Python 3.12.3) |
| `cd services/encoder && nice -n 10 <eigen venv, Python 3.12> -m pytest -q -rfEs -p no:cacheprovider tests/test_mediaguard_resources.py tests/test_planner.py::test_pauze_van_cpu_laat_gpu_doordraaien tests/test_reviewfixes.py::test_cpu_temperatuur_negeert_schijven tests/test_beslissen.py tests/test_api.py::test_openapi_bevat_alle_fase_vijf_routes` | **17 passed** in 14,2 s |
| idem: `tests/test_playback_hold.py tests/test_reviewfixes.py::test_pauzeren_en_hervatten_zijn_gescheiden tests/test_onderhoud.py::test_cachegrenzen_pauzeren_en_herstellen_alleen_eigen_pauze tests/test_integraties.py::test_jellyfin_pauzeert_alleen_cpu_en_heft_eigen_pauze_op` | **7 passed** in 1,8 s |
| `.venv/bin/python scripts/platform-v2-routes.py --json` | 432 routes; alle routes uit §D aanwezig |

Niet gedraaid (alleen gelezen):

- `test_commandos.py` en `test_kwaliteitsmeting.py`: deze vragen de toolchain met FFmpeg, dovi_tool en mkvmerge, en draaien alleen in de nachtelijke CI-job `encoder-image`;
- de Playwright-e2e (`mediaguard.spec.ts:131/170`): die is groen in CI-run `42587401041` op dezelfde code.
