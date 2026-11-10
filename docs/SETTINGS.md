# Instellingen — overzicht

MediaGuard opent **Instellingen** in een eenvoudige weergave: per sectie alleen wat een gewone
gebruiker nodig heeft. De rest staat per sectie onder **Geavanceerd tonen**, of via **Alle
instellingen** (met uitzonderingen per mediatype, instantie, serie of film). Zoeken doorzoekt
altijd alles. De indeling staat in `apps/core/mediaguard/settings_curation.py`.

Weergave: **eenvoudig** = direct zichtbaar · **geavanceerd** = achter 'Geavanceerd tonen' ·
**verborgen** = vervallen of dubbel (alleen via zoeken/API).

## Systeembelasting (nieuw in 0.2.7)

| Instelling | Standaard | Wat de AV1-engine ermee doet |
|---|---|---|
| Maximaal CPU-gebruik voor AV1 | 75 % | threads per CPU-encode = (cores × %) ÷ encodes tegelijk (cgroup-bewust); encodes draaien met nice 10 |
| Encodes tegelijk (CPU) | 1 | `worker_config.aantal` CPU-baan |
| Encodes tegelijk (GPU) | 1 | `worker_config.aantal` GPU-baan (0 = GPU niet gebruiken) |
| Alleen encoderen binnen tijdvenster | uit (23:00–07:00) | omgezet naar stille uren (stil van eind tot start) op beide banen |
| Pauzeer AV1 boven CPU-temperatuur | 85 °C (geavanceerd) | `cpu_max_temperatuur`: geen nieuwe CPU-encode boven deze grens |

Het totaal aantal encodes en de AV1-wachtrij (autopilot) volgen automatisch CPU + GPU.

## Systeem & prestaties

Hoeveel van je server MediaGuard mag gebruiken.

| Key | Label | Weergave | Waarom |
|---|---|---|---|
| `resources.av1_cpu_percent` | Maximaal CPU-gebruik voor AV1 | eenvoudig | dagelijks nodig |
| `resources.encodes_cpu` | Encodes tegelijk op de processor (CPU) | eenvoudig | dagelijks nodig |
| `resources.encodes_gpu` | Encodes tegelijk op de videokaart (GPU) | eenvoudig | dagelijks nodig |
| `resources.encode_window_enabled` | Alleen encoderen binnen een tijdvenster | eenvoudig | dagelijks nodig |
| `resources.encode_window` | Tijdvenster voor encoderen | eenvoudig | dagelijks nodig |
| `jobs.max_concurrency` | Gelijktijdige achtergrondtaken | geavanceerd | fijnafstelling; standaard is goed |
| `resources.max_cpu_temp_c` | Pauzeer AV1 boven CPU-temperatuur | geavanceerd | fijnafstelling; standaard is goed |

## AV1 & ruimte besparen

Bestanden kleiner maken zonder zichtbaar kwaliteitsverlies.

| Key | Label | Weergave | Waarom |
|---|---|---|---|
| `encoding.autopilot_enabled` | Automatisch bestanden kleiner maken (AV1) | eenvoudig | dagelijks nodig |
| `encoding.auto_commit_after_verify` | Goedgekeurde resultaten direct gebruiken | eenvoudig | dagelijks nodig |
| `encoding.min_saving_percent` | Alleen omzetten als het minstens zoveel scheelt | eenvoudig | dagelijks nodig |
| `encoding.keep_all_audio` | Alle audiosporen bewaren | eenvoudig | dagelijks nodig |
| `encoding.keep_all_subtitles` | Alle ondertitels bewaren | eenvoudig | dagelijks nodig |
| `dv.allow_av1` | Ook Dolby Vision-bestanden omzetten | eenvoudig | dagelijks nodig |
| `dv.hdr10plus_mode` | HDR10+-bestanden | eenvoudig | dagelijks nodig |
| `dv.allowed_profiles` | Vrijgegeven DV-invoerprofielen | geavanceerd | fijnafstelling; standaard is goed |
| `dv.mode` | Dolby Vision-modus | geavanceerd | fijnafstelling; standaard is goed |
| `dv.show_playback_warnings` | Afspeelwaarschuwingen tonen | geavanceerd | fijnafstelling; standaard is goed |
| `encoding.autopilot_interval_minutes` | Interval AV1-autopilot | geavanceerd | fijnafstelling; standaard is goed |
| `encoding.autopilot_lane_buffer` | Voorraad per baan (CPU en GPU) | geavanceerd | fijnafstelling; standaard is goed |
| `encoding.autopilot_queue_depth` | Bestanden tegelijk bij de encoder (0 = automatisch) | geavanceerd | fijnafstelling; standaard is goed |
| `encoding.cache_min_free_gb` | Pauzebodem encodercache (GB vrij) | geavanceerd | fijnafstelling; standaard is goed |
| `encoding.canary_required` | Voorzichtige start: eerste conversies één voor één | geavanceerd | fijnafstelling; standaard is goed |
| `encoding.crf` | CRF (0 = automatisch) | geavanceerd | fijnafstelling; standaard is goed |
| `encoding.encoder` | Encoder | geavanceerd | fijnafstelling; standaard is goed |
| `encoding.identity_gate` | Identiteitspoort | geavanceerd | fijnafstelling; standaard is goed |
| `encoding.poll_interval_seconds` | Statusinterval encoder (s) | geavanceerd | fijnafstelling; standaard is goed |
| `encoding.preset` | Preset (0 = automatisch) | geavanceerd | fijnafstelling; standaard is goed |
| `optimization.av1_threshold_1080_kbps` | AV1-drempel 1080p (kbps) | geavanceerd | fijnafstelling; standaard is goed |
| `optimization.av1_threshold_2160_kbps` | AV1-drempel 2160p (kbps) | geavanceerd | fijnafstelling; standaard is goed |
| `optimization.av1_threshold_720_kbps` | AV1-drempel 720p (kbps) | geavanceerd | fijnafstelling; standaard is goed |
| `optimization.batch_size` | Batchgrootte | geavanceerd | fijnafstelling; standaard is goed |
| `optimization.dry_run_valid_hours` | Geldigheid dry-run (uren) | geavanceerd | fijnafstelling; standaard is goed |
| `optimization.estimated_av1_saving_percent` | Verwachte AV1-besparing H.264 (%) | geavanceerd | fijnafstelling; standaard is goed |
| `optimization.estimated_av1_saving_percent_hevc` | Verwachte AV1-besparing HEVC (%) | geavanceerd | fijnafstelling; standaard is goed |
| `optimization.max_searches_per_hour` | Max. zoekopdrachten per uur (indexerquota) | geavanceerd | fijnafstelling; standaard is goed |
| `optimization.staging_reserve_gb` | Stagingreserve (GB) | geavanceerd | fijnafstelling; standaard is goed |
| `optimization.window` | Tijdvenster | geavanceerd | fijnafstelling; standaard is goed |
| `optimization.window_enabled` | Alleen binnen tijdvenster uitvoeren | geavanceerd | fijnafstelling; standaard is goed |
| `optimization.max_concurrent_encodes` | Max. gelijktijdige AV1-encodes (CPU/GPU) | verborgen | Vervangen door 'Encodes tegelijk' onder Systeem & prestaties. |

## Originelen & herstellen

Wat er met het oude bestand gebeurt nadat het vervangen is.

| Key | Label | Weergave | Waarom |
|---|---|---|---|
| `quarantine.mode` | Originelen na vervangen | eenvoudig | dagelijks nodig |
| `quarantine.retention_days` | Originelen bewaren (dagen) | eenvoudig | dagelijks nodig |
| `quarantine.max_size_gb` | Maximaal bewaren (GB) | eenvoudig | dagelijks nodig |
| `integrity.recovery_verify_checksums` | Checksums controleren bij kopiëren en activeren | geavanceerd | vergrendelde veiligheidscontrole |
| `integrity.release_check_before_purge` | Vrijgavecontrole vereist vóór automatisch opruimen | geavanceerd | vergrendelde veiligheidscontrole |
| `integrity.require_recoverable_copy` | Herstelbare kopie vereist vóór vervanging | geavanceerd | vergrendelde veiligheidscontrole |
| `integrity.verify_streams_before_activate` | Streams verifiëren vóór activatie | geavanceerd | vergrendelde veiligheidscontrole |
| `library.write_enabled` | MediaGuard mag bibliotheekbestanden vervangen | geavanceerd | fijnafstelling; standaard is goed |
| `quarantine.auto_purge` | Verlopen originelen automatisch opruimen | geavanceerd | fijnafstelling; standaard is goed |
| `quarantine.min_age_hours` | Minimale bewaartijd bij vol quotum (uur) | geavanceerd | fijnafstelling; standaard is goed |
| `quarantine.release_check_full_decode` | Volledige decodecontrole vóór opruimen | geavanceerd | fijnafstelling; standaard is goed |
| `quarantine.release_check_timeout_minutes` | Maximale duur decodecontrole (minuten) | geavanceerd | fijnafstelling; standaard is goed |
| `recovery.hardlink_policy` | Bestanden met hardlinks (seeding) | geavanceerd | fijnafstelling; standaard is goed |
| `recovery.min_free_gb` | Minimale vrije ruimte (GB) | geavanceerd | fijnafstelling; standaard is goed |
| `recovery.quarantine_location` | Locatie quarantaine | geavanceerd | fijnafstelling; standaard is goed |
| `recovery.refresh_arr_after_commit` | Sonarr/Radarr laten herscannen na vervanging | geavanceerd | fijnafstelling; standaard is goed |
| `recovery.truenas_url` | TrueNAS-API-URL | geavanceerd | fijnafstelling; standaard is goed |
| `recovery.truenas_verify_tls` | TLS-certificaat TrueNAS controleren | geavanceerd | fijnafstelling; standaard is goed |
| `recovery.zfs_dataset` | Dataset voor snapshots | geavanceerd | fijnafstelling; standaard is goed |
| `recovery.zfs_snapshot_enabled` | ZFS-snapshot vóór vervanging | geavanceerd | fijnafstelling; standaard is goed |

## Downloaden & zoeken

Ontbrekende afleveringen en films zoeken, en hoe vaak.

| Key | Label | Weergave | Waarom |
|---|---|---|---|
| `backfill.enabled` | Ontbrekende afleveringen en films automatisch zoeken | eenvoudig | dagelijks nodig |
| `backfill.max_searches_per_day` | Maximaal zoekopdrachten per dag | eenvoudig | dagelijks nodig |
| `backfill.include_cutoff_unmet` | Ook betere versies zoeken | eenvoudig | dagelijks nodig |
| `optimization.max_active_downloads` | Maximaal downloads tegelijk (optimaliseren) | eenvoudig | dagelijks nodig |
| `optimization.max_gb_per_day` | Maximaal downloaden per dag (GB) | eenvoudig | dagelijks nodig |
| `backfill.base_backoff_hours` | Basis-wachttijd na misser | geavanceerd | fijnafstelling; standaard is goed |
| `backfill.batch_size` | Batchgrootte | geavanceerd | fijnafstelling; standaard is goed |
| `backfill.collect_candidates` | Kandidaten en afwijzingen bewaren | geavanceerd | fijnafstelling; standaard is goed |
| `backfill.command_wait_seconds` | Wachttijd op zoekresultaat | geavanceerd | fijnafstelling; standaard is goed |
| `backfill.interval_hours` | Backfill-interval | geavanceerd | fijnafstelling; standaard is goed |
| `backfill.max_backoff_days` | Maximale wachttijd | geavanceerd | fijnafstelling; standaard is goed |
| `backfill.max_searches_per_hour` | Max. zoekopdrachten per uur | geavanceerd | fijnafstelling; standaard is goed |
| `backfill.priority` | Backfill-prioriteit | geavanceerd | fijnafstelling; standaard is goed |
| `backfill.priority_recent_days` | Recente afleveringen voorrang (dagen) | geavanceerd | fijnafstelling; standaard is goed |
| `backfill.season_search_threshold` | Seizoenszoekactie vanaf | geavanceerd | fijnafstelling; standaard is goed |
| `backfill.unavailable_after_attempts` | 'Niet beschikbaar' na pogingen | geavanceerd | fijnafstelling; standaard is goed |
| `backfill.unavailable_retry_days` | Nieuwe poging na 'niet beschikbaar' | geavanceerd | fijnafstelling; standaard is goed |
| `hooks.audio_score_step` | Scorebijstelling per audiostap | geavanceerd | fijnafstelling; standaard is goed |
| `hooks.post_import_analysis` | Analyse na import (identiteit, audio, AV1) | geavanceerd | fijnafstelling; standaard is goed |
| `hooks.pregrab_enabled` | Pre-grab-beleid toepassen | geavanceerd | fijnafstelling; standaard is goed |
| `hooks.pregrab_identity_check` | Afleveringnummer in releasenaam controleren | geavanceerd | fijnafstelling; standaard is goed |
| `hooks.preimport_defer_on_uncertain` | Import uitstellen bij onzekere identiteit | geavanceerd | fijnafstelling; standaard is goed |
| `hooks.preimport_enforce_audio` | Verplichte audiotaal afdwingen bij import | geavanceerd | fijnafstelling; standaard is goed |
| `identity.auto_replace_on_mismatch` | Automatisch vervangen bij bewezen verkeerde aflevering | geavanceerd | fijnafstelling; standaard is goed |
| `identity.duration_tolerance_percent` | Tolerantie speelduur (%) | geavanceerd | fijnafstelling; standaard is goed |
| `identity.mismatch_min_weight` | Minimale bewijssterkte voor 'verkeerde aflevering' | geavanceerd | fijnafstelling; standaard is goed |
| `identity.remember_verified_hashes` | Hashes van geverifieerde bestanden onthouden | geavanceerd | fijnafstelling; standaard is goed |
| `identity.thexem_enabled` | TheXEM-mapping gebruiken | geavanceerd | fijnafstelling; standaard is goed |
| `policy.profile_id` | Policyprofiel | geavanceerd | fijnafstelling; standaard is goed |
| `policy.ranking_mode` | Rangschikking | geavanceerd | fijnafstelling; standaard is goed |
| `wanted.relaxed_allowed_categories` | Toegestane versoepelingen | geavanceerd | fijnafstelling; standaard is goed |
| `backfill.excluded` | Uitsluiten van backfill | verborgen | Per serie of film: 'Niet meer zoeken' op de titelpagina. |

## Taal & anime

Audio- en ondertitelvoorkeuren en waar anime terechtkomt.

| Key | Label | Weergave | Waarom |
|---|---|---|---|
| `audio.nl_subtitles_wanted` | Nederlandse ondertitels | eenvoudig | dagelijks nodig |
| `audio.nl_audio_mode` | Nederlandse audio | eenvoudig | dagelijks nodig |
| `audio.anime_preference` | Anime: welke audio voorkeur heeft | eenvoudig | dagelijks nodig |
| `routing.anime.enabled` | Anime automatisch naar de anime-instantie | eenvoudig | dagelijks nodig |
| `routing.anime.detect_animation_japanese` | Japanse animatie als anime herkennen | geavanceerd | fijnafstelling; standaard is goed |
| `routing.anime.max_age_hours` | Alleen recent toegevoegde titels (uur) | geavanceerd | fijnafstelling; standaard is goed |
| `routing.anime.mode` | Werkwijze | geavanceerd | fijnafstelling; standaard is goed |
| `routing.anime.sweep_minutes` | Controle-interval (minuten) | geavanceerd | fijnafstelling; standaard is goed |
| `routing.anime.target_profile` | Kwaliteitsprofiel op anime-instantie | geavanceerd | fijnafstelling; standaard is goed |
| `routing.anime.target_root` | Hoofdmap op anime-instantie | geavanceerd | fijnafstelling; standaard is goed |

## Algemeen

Taal, tijdzone en back-ups van MediaGuard zelf.

| Key | Label | Weergave | Waarom |
|---|---|---|---|
| `system.timezone` | Tijdzone | eenvoudig | dagelijks nodig |
| `backup.daily_enabled` | Dagelijkse back-up van MediaGuard | eenvoudig | dagelijks nodig |
| `ai.action_timeout_seconds` | Time-out AI-actie | geavanceerd | fijnafstelling; standaard is goed |
| `ai.anthropic.base_url` | Claude API-adres | geavanceerd | fijnafstelling; standaard is goed |
| `ai.anthropic.effort` | Claude denkniveau (effort) | geavanceerd | fijnafstelling; standaard is goed |
| `ai.anthropic.model` | Claude-model | geavanceerd | fijnafstelling; standaard is goed |
| `ai.bounded_auto.allowed_actions` | Automatisch toegestane acties | geavanceerd | fijnafstelling; standaard is goed |
| `ai.bounded_auto.max_actions_per_day` | Max AI-acties per dag | geavanceerd | fijnafstelling; standaard is goed |
| `ai.bounded_auto.min_benchmark_accuracy` | Minimale benchmarkscore | geavanceerd | fijnafstelling; standaard is goed |
| `ai.bounded_auto.min_improvement` | Minimale verbetering t.o.v. regels | geavanceerd | fijnafstelling; standaard is goed |
| `ai.bounded_auto.min_shadow_cases` | Minimum schaduwcases vóór automatisch | geavanceerd | fijnafstelling; standaard is goed |
| `ai.budget.currency` | Budgetvaluta | geavanceerd | fijnafstelling; standaard is goed |
| `ai.budget.max_calls_per_day` | Maximaal analyses per dag | geavanceerd | fijnafstelling; standaard is goed |
| `ai.budget.monthly_limit` | Maandbudget | geavanceerd | fijnafstelling; standaard is goed |
| `ai.budget.per_analysis_limit` | Kostenplafond per analyse | geavanceerd | fijnafstelling; standaard is goed |
| `ai.budget.usd_to_eur` | Wisselkoers USD→EUR | geavanceerd | fijnafstelling; standaard is goed |
| `ai.cache_ttl_days` | Cache-bewaartijd identieke cases | geavanceerd | fijnafstelling; standaard is goed |
| `ai.max_input_tokens` | Max invoertokens (schatting) | geavanceerd | fijnafstelling; standaard is goed |
| `ai.max_output_tokens` | Max antwoordtokens | geavanceerd | fijnafstelling; standaard is goed |
| `ai.max_retries` | Herhaalpogingen per analyse | geavanceerd | fijnafstelling; standaard is goed |
| `ai.mode` | AI-beleid | geavanceerd | fijnafstelling; standaard is goed |
| `ai.openai.base_url` | OpenAI API-adres | geavanceerd | fijnafstelling; standaard is goed |
| `ai.openai.model` | OpenAI-model | geavanceerd | fijnafstelling; standaard is goed |
| `ai.price_table` | Prijstabel per model | geavanceerd | fijnafstelling; standaard is goed |
| `ai.provider` | AI-provider | geavanceerd | fijnafstelling; standaard is goed |
| `ai.retention_days` | Bewaartermijn AI-cases | geavanceerd | fijnafstelling; standaard is goed |
| `ai.retry_budget_per_day` | Retrybudget per dag | geavanceerd | fijnafstelling; standaard is goed |
| `ai.task_models` | Model per taaktype | geavanceerd | fijnafstelling; standaard is goed |
| `ai.task_types` | Doelcases voor AI | geavanceerd | fijnafstelling; standaard is goed |
| `ai.timeout_seconds` | Time-out per call | geavanceerd | fijnafstelling; standaard is goed |
| `backup.retention_count` | Aantal te bewaren back-ups | geavanceerd | fijnafstelling; standaard is goed |
| `instances.health_interval_seconds` | Interval gezondheidscontrole | geavanceerd | fijnafstelling; standaard is goed |
| `library.sync_interval_minutes` | Synchronisatie-interval bibliotheek | geavanceerd | fijnafstelling; standaard is goed |
| `logging.level` | Logniveau | geavanceerd | fijnafstelling; standaard is goed |
| `migration.command_wait_seconds` | Wachttijd Sonarr/Radarr-commando's | geavanceerd | fijnafstelling; standaard is goed |
| `migration.episode_wait_seconds` | Wachttijd afleveringsmetadata | geavanceerd | fijnafstelling; standaard is goed |
| `migration.max_backup_mb` | Maximale back-upgrootte | geavanceerd | fijnafstelling; standaard is goed |
| `migration.path_sample_size` | Steekproef padcontrole | geavanceerd | fijnafstelling; standaard is goed |
| `migration.restart_wait_seconds` | Wachttijd herstart na restore | geavanceerd | fijnafstelling; standaard is goed |
| `provisioning.auto_repair` | Afwijkingen automatisch herstellen | geavanceerd | fijnafstelling; standaard is goed |
| `provisioning.drift_check_enabled` | Afwijkingen periodiek controleren | geavanceerd | fijnafstelling; standaard is goed |
| `provisioning.drift_interval_hours` | Interval afwijkingscontrole | geavanceerd | fijnafstelling; standaard is goed |
| `provisioning.on_startup` | Essentials bij opstarten inrichten | geavanceerd | fijnafstelling; standaard is goed |
| `provisioning.startup_delay_seconds` | Wachttijd na opstarten | geavanceerd | fijnafstelling; standaard is goed |
| `seerr.request_sync_interval_minutes` | Interval aanvragen-synchronisatie | geavanceerd | fijnafstelling; standaard is goed |
| `seerr.url` | Seerr-URL | geavanceerd | fijnafstelling; standaard is goed |
| `ui.theme` | Thema | geavanceerd | fijnafstelling; standaard is goed |

## Eenheden en "Bespaard"

- **Eén eenheid overal**: 1 GB = 1024³ bytes (zoals TrueNAS en de instellingen voor
  quarantaine/vangnet). De web-UI, meldingen en het dagoverzicht gebruiken dezelfde
  omrekening. De API geeft ruwe bytes; reken je die zelf om met 1000³, dan krijg je een
  ~7 % hoger getal (rapport 7, N5: "642 GB" in de app = 689 × 10⁹ bytes in de API).
- **Bespaard** = som van (grootte origineel − grootte AV1-versie) over vervangingen die
  daadwerkelijk zijn **geactiveerd** (het origineel is vervangen). Encodes die nog op
  activatie wachten staan apart als "wacht op activatie" en tellen niet mee.

## Quarantaine en vangnet: één getal

De balk en de vangnet-banner tonen hetzelfde getal: alle bewaarde originelen, ook die nog
gecontroleerd worden. Komt dat (plus het grootste origineel dat op activatie wacht) boven het
maximum, dan ruimt MediaGuard de oudste gecontroleerde originelen op tot
`quarantine.purge_target_percent` (standaard 85 %) van het maximum. De AV1-wachtrij pauzeert
alleen nog als er niets op te ruimen valt.
