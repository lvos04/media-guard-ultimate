# Backfill & Missing Recovery — bedieningshandleiding

Code: `apps/core/mediaguard/wanted/` (engine, diagnosis, backfill, api) en
`apps/core/mediaguard/library/` (sync, queries). Spec: MEDIAGUARD_GOAL.md D1–D4, R4.

## 1. Hoe het werkt

```
Sonarr/Radarr-fork (bron)  ──sync──▶  lib_items / lib_seasons / lib_episodes / lib_files
                                            │
                                   refresh_wanted()  (alleen cache, idempotent)
                                            ▼
                                       wanted_items  ──▶ Root Cause Engine (missing_diagnostics)
                                            │
                     Backfill Scheduler per instantie (quota, backoff, dedup)
                                            ▼
                EpisodeSearch / SeasonSearch / MoviesSearch-commando's (kleine batches)
                                            ▼
                        backfill_attempts (pending → grabbed | no_candidate | error)
```

* **RSS blijft bij Sonarr/Radarr.** De scheduler stuurt uitsluitend zoekcommando's in kleine
  batches, wijzigt nooit indexers en slaat een ronde over als er op die instantie al een
  zoekcommando loopt (`skipped: busy`).
* **Per instantie** (`sonarr-series`, `sonarr-anime`, `radarr-films`, `radarr-animefilms`, …):
  eigen quota, eigen planning, eigen wanted-items. Native ID's zijn alleen uniek binnen een
  instantie; alles is gesleuteld op `(instance_id, provider_id)`.
* **Wat telt als ontbrekend:** gemonitorde aflevering van een gemonitorde serie, uitgezonden
  (`airDateUtc <= nu`) en zonder bestand — exact de semantiek van Sonarr `wanted/missing`.
  Films: gemonitord, `isAvailable` en geen bestand. *Niet-gemonitorde en toekomstige items
  tellen nooit als ontbrekend* (ook niet in KPI's of de seizoenmatrix).
* **Cutoff unmet** (`qualityCutoffNotMet` op het bestand) wordt apart als `reason=cutoff`
  bijgehouden en alleen gezocht als `backfill.include_cutoff_unmet` aan staat.

## 2. Levenscyclus van een wanted-item

| Status | Betekenis |
|---|---|
| `wanted` | Doet mee aan de planning zodra `next_search_at` verstreken is (of nooit gezocht). |
| `searching` | Er staat een zoekcommando open (`backfill_attempts.status='pending'`). |
| `unavailable` | Na `backfill.unavailable_after_attempts` vergeefse pogingen; volgende poging pas na `backfill.unavailable_retry_days`. Leg aan de gebruiker uit dat niet alles te vinden is. |
| `ignored` | Bewuste uitzondering van de gebruiker. Monitorstatus in Sonarr/Radarr blijft ongewijzigd. |
| `resolved` | Niet meer ontbrekend; `resolution` = `available` / `upgraded` / `unmonitored` / `not_aired` / `removed`. Wordt automatisch heropend als het item weer ontbreekt. |

Backoff na een vergeefse poging: `base_backoff_hours × 2^(n-1)`, begrensd op
`max_backoff_days`. Na een grab: 24 uur rust (download/import krijgt tijd). Een
commandofout telt niet mee voor 'unavailable'. Openstaande pogingen ouder dan 6 uur
worden als fout afgesloten. Alles is herstartbestendig (pogingen staan in SQLite).

**Geen eindeloze lussen:** één open poging per item, niets zoeken wat al in de
downloadwachtrij staat, backoff per item, `unavailable` met lange retry, en
`skipped: busy` als Sonarr/Radarr zelf al zoekt.

**Seizoenspakketten:** ontbreekt een volledig seizoen (geen enkel bestand, ≥
`backfill.season_search_threshold` gemonitorde uitgezonden afleveringen), dan één
`SeasonSearch` (telt als 1 zoekopdracht) in plaats van losse `EpisodeSearch`es.

**Prioriteit** (hoger eerst): recent uitgezonden (+50, venster `priority_recent_days`),
seizoen bijna compleet (+20), lang ontbrekend >1 jaar (+10), favoriet
(`backfill.priority=high` +30 / `low` −30), cutoff-upgrade (−40), specials (−20).

## 3. Instellingen (UI: Ontbrekende media; scopes global → media_type → instance → series/movie/season/episode)

| Sleutel | Standaard | Scopes | Uitleg |
|---|---|---|---|
| `backfill.enabled` | `true` | global, media_type, instance | Scheduler aan/uit (handmatig zoeken blijft kunnen). |
| `backfill.interval_hours` | 12 (6–24) | idem | Rondes per instantie. |
| `backfill.max_searches_per_hour` | 20 | idem | Quotum; telt backfill, *Zoek nu*, interactieve diagnose en versoepelde retry. |
| `backfill.max_searches_per_day` | 150 | idem | Voortschrijdend 24-uursvenster. |
| `backfill.batch_size` | 10 | idem | Max. items per scheduler-ronde. |
| `backfill.base_backoff_hours` | 24 | idem | Eerste wachttijd na een misser; verdubbelt. |
| `backfill.max_backoff_days` | 30 | idem | Plafond van de backoff. |
| `backfill.unavailable_after_attempts` | 5 | idem | Daarna status `unavailable`. |
| `backfill.unavailable_retry_days` | 30 | idem | Wachttijd voor `unavailable`. |
| `backfill.include_cutoff_unmet` | `true` | idem | Ook upgrades zoeken. |
| `backfill.priority_recent_days` | 30 | idem | Venster voor 'recent'. |
| `backfill.season_search_threshold` | 4 | idem (geavanceerd) | Drempel voor SeasonSearch. |
| `backfill.command_wait_seconds` | 120 | idem (geavanceerd) | Hoe lang een ronde op afronding van het commando wacht; daarna wordt de uitkomst in de volgende ronde beoordeeld. |
| `backfill.collect_candidates` | `on_unavailable` | idem | Interactieve zoekactie om kandidaten + afwijzingsredenen te bewaren (`never` / `on_unavailable` / `always`). Telt mee in quotum. |
| `backfill.priority` | `normal` | global, instance, series, movie | Favorieten. |
| `backfill.excluded` | `false` | instance, series, movie, season, episode | Bewuste uitzondering. |
| `wanted.relaxed_allowed_categories` | `quality, custom-format, size` | global, media_type, instance | Welke regels de gebruiker *per item expliciet* mag versoepelen. `language` bv. alleen voor `media_type=anime` aanzetten. |
| `library.sync_interval_minutes` | 60 | global, instance | Bibliotheeksync. |
| `instances.health_interval_seconds` | 60 | global | Health-polling. |

Scope-ID's: `instance=<id>`, `series=<id>:<seriesId>`, `movie=<id>:<movieId>`,
`season=<id>:<seriesId>:<season>`, `episode=<id>:<episodeId>`, `media_type=tv|anime|movie|anime_movie`.

## 4. Root Cause Engine — oorzaakcodes

`GET /api/v1/wanted/{id}/diagnosis` geeft `primary` + alle bijdragende `reasons`, elk met
`code`, `label_nl`, `rule` (de concrete regel of afwijzingstekst), `ts`, `message_nl`,
`suggested_fix_nl`, `severity`, `confidence`, plus `evidence` (episode/serie, wachtrij,
historie, indexers zonder geheimen, indexerstatus, health, downloadclients, rootmappen,
kandidaten met afwijzingen, handmatige-importkandidaten).

Volgorde (eerste = primaire oorzaak): `intentional-exception`, `unmonitored`,
`not-yet-aired`, `instance-suspended`, `root-folder-missing`, `file-not-linked`,
`import-failed`, `download-client-failed`, `stuck-queue`, `downloading`,
`download-client-missing`, `indexer-auth`, `indexer-rate-limit`, `indexer-offline`,
`no-indexers`, `incorrect-mapping`, `release-available`, `rejected-language`,
`rejected-custom-format`, `rejected-quality`, `rejected-size`, `rejected-other`,
`no-indexer-hit`, `searched-no-grab`, `not-searched-yet`, `unknown`.

Bronnen per code: live episode/serie/film (`/episode/{id}`, `/series/{id}`, `/movie/{id}`),
`/rootfolder` (`accessible`), `/manualimport?folder=…&filterExistingFiles=true`,
`/queue` (`trackedDownloadState` `importBlocked/importFailed`, `status`
`failed/warning/stalled`), `/downloadclient`, `/indexer` (+ tags), `/indexerstatus`
(`disabledTill`) gecombineerd met `/health`-meldingen (401/API key → auth, 429/limit →
rate-limit), `/history` (`downloadFailed`, `grabbed` zonder import, `downloadIgnored`),
`/release` (interactief; afwijzingsteksten worden geclassificeerd). Zonder `interactive=true`
wordt het laatst opgeslagen interactieve resultaat hergebruikt; `interactive=true` vereist
operator en quotum. `cached=true` geeft de laatst opgeslagen diagnose zonder live calls.

## 5. Handmatige acties (met uitleg van gevolgen in het antwoord)

| Actie | Endpoint | Gevolg |
|---|---|---|
| Zoek nu | `POST /api/v1/wanted/{id}/search` | `EpisodeSearch`/`MoviesSearch`, telt mee in quotum; bij quotum op → HTTP 429. |
| Opnieuw met versoepelde regels | `POST /api/v1/wanted/{id}/retry-relaxed` `{relaxations, confirm}` | Zonder `confirm`: alleen uitleg. Met `confirm`: override-record (`wanted_overrides`, 7 dagen), audit, interactieve zoekactie en grab van de eerste kandidaat die **uitsluitend** op de gekozen regels werd afgewezen. Identiteit/mapping, blocklist en MediaGuard-beleid worden nooit versoepeld; het profiel wordt niet gewijzigd. |
| Negeren | `POST /api/v1/wanted/{id}/ignore` | Telt niet meer mee, wordt niet gezocht; monitorstatus blijft. |
| Opnieuw volgen | `POST /api/v1/wanted/{id}/unignore` | Terug in de planning. |
| Blijven volgen | `POST /api/v1/wanted/{id}/keep-monitoring` | Wordt nooit `unavailable`; backoff blijft gelden. |
| Per seizoen | `POST /api/v1/wanted/actions` `{action, instance_id, series_id, season_number}` | Zelfde acties voor een heel seizoen. |

Policy/hook-modules kunnen een actieve versoepeling opvragen met
`mediaguard.wanted.backfill.active_override(ctx, instance_id, "episode"|"movie", provider_id)`.

## 6. Overige API

* `GET /api/v1/wanted` — filters `instance_id, media_type, kind, reason, status (open|…),
  q, season, series_id, reason_code, searched_before/after, never_searched`, paging, sort.
* `POST /api/v1/wanted/backfill-plan` — dry-run per instantie: quotum, uitgestelde items
  per reden, commando's met items en prioriteit. Verstuurt niets.
* `POST /api/v1/instances/{id}/backfill` — `dry_run`, `mode=queue|inline`.
* `GET /api/v1/wanted/backfill/runs`, `GET /api/v1/wanted/backfill/quota`,
  `GET /api/v1/wanted/kpis` (missing, 7/30/90 dagen, opgelost 7/30/90 dagen, redenverdeling,
  backfill-succesratio 30 dagen, import-failures), `GET /api/v1/wanted/reasons`.
* `GET /api/v1/library/kpis` — per instantie beschikbaar/ontbrekend/binnenkort/
  ongemonitord/onder cutoff, `upcoming_7d`, `future_season_coverage`.

## 7. Runbook

* **Indexer klaagt over limieten** → verlaag `max_searches_per_hour` op de betreffende
  instantie (of media_type); diagnoses tonen `indexer-rate-limit`.
* **Veel `unavailable`** → bekijk de redenverdeling. `rejected-*` betekent dat er wél
  releases zijn: profiel/CF-scores beoordelen of per item versoepelen. `no-indexer-hit`:
  wachten of indexers toevoegen.
* **Migratie/cutover** → instantie pauzeren (`POST /instances/{id}/suspend`): RSS en
  automatisch zoeken uit op alle indexers (exacte vorige stand bewaard), backfill slaat
  de instantie over, pre-grab-hook weigert met `INSTANCE_SUSPENDED`. Hervatten zet exact
  terug.
* **Noodstop** → backfilljobs (`backfill.*`) worden niet gestart en lopende rondes slaan over.
