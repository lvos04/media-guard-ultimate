# UI Control Coverage Matrix (Q2.3)

Gegenereerd op 2026-11-09 met `scripts/gen-coverage-matrix.py` uit het instellingenregister van core (230 instellingen; scopes: global, media_type, instance, library, profile, series, movie, season, episode, file, job).

**Mechanisme.** Elke geregistreerde instelling krijgt automatisch een UI-control: `apps/web/app/(app)/instellingen/page.tsx`
rendert het volledige schema (`SettingField` in `apps/web/components/mg/setting-field.tsx`) met naam, key, uitleg, effect,
overervingstrace (`GET /settings/effective`), standaard, bereik, ‘Herstel standaard’ (reset op scope), validatiefout van de
API (422 → veld rood, oude waarde blijft actief), scope-selector en ‘Voorbeeldbeslissing’ (`POST /settings/simulate`).
Featurepagina's tonen dezelfde controls in context via `SettingsSection` (prefix-filter). Opslaan: `PATCH /settings/scoped`
(alles-of-niets), behalve `ai.*` → `PATCH /ai/settings` (backend handhaaft de AI-inschakelvoorwaarden).
Audit: elke wijziging verschijnt in Instellingen › Audit (`GET /audit`).

Een instelling die de backend nieuw registreert verschijnt zonder frontendwijziging. Type `secret` wordt nooit teruggelezen;
geheimen gaan via Instellingen › Geheimen (`PUT/DELETE /settings/secrets/{name}`).

| Key | Label | Type | UI-control | Scopes | Waar in de UI | Test |
|---|---|---|---|---|---|---|
| `ai.action_timeout_seconds` | Time-out AI-actie | int | getalveld | global | Instellingen › AI Adviseur · /ai (via PATCH /ai/settings) | generiek: render+validatie via schema (E2E-pad gedekt door jobs.max_concurrency) |
| `ai.anthropic.base_url` | Claude API-adres | string | tekstveld | global | Instellingen › AI Adviseur · /ai (via PATCH /ai/settings) | generiek: render+validatie via schema (E2E-pad gedekt door jobs.max_concurrency) |
| `ai.anthropic.effort` | Claude denkniveau (effort) | enum | keuzelijst | global | Instellingen › AI Adviseur · /ai (via PATCH /ai/settings) | generiek: render+validatie via schema (E2E-pad gedekt door jobs.max_concurrency) |
| `ai.anthropic.model` | Claude-model | string | tekstveld | global | Instellingen › AI Adviseur · /ai (via PATCH /ai/settings) | generiek: render+validatie via schema (E2E-pad gedekt door jobs.max_concurrency) |
| `ai.bounded_auto.allowed_actions` | Automatisch toegestane acties | list | chips / komma-lijst | global | Instellingen › AI Adviseur · /ai (via PATCH /ai/settings) | generiek: render+validatie via schema (E2E-pad gedekt door jobs.max_concurrency) |
| `ai.bounded_auto.max_actions_per_day` | Max AI-acties per dag | int | getalveld | global | Instellingen › AI Adviseur · /ai (via PATCH /ai/settings) | generiek: render+validatie via schema (E2E-pad gedekt door jobs.max_concurrency) |
| `ai.bounded_auto.min_benchmark_accuracy` | Minimale benchmarkscore | float | getalveld | global | Instellingen › AI Adviseur · /ai (via PATCH /ai/settings) | generiek: render+validatie via schema (E2E-pad gedekt door jobs.max_concurrency) |
| `ai.bounded_auto.min_improvement` | Minimale verbetering t.o.v. regels | float | getalveld | global | Instellingen › AI Adviseur · /ai (via PATCH /ai/settings) | generiek: render+validatie via schema (E2E-pad gedekt door jobs.max_concurrency) |
| `ai.bounded_auto.min_shadow_cases` | Minimum schaduwcases vóór automatisch | int | getalveld | global | Instellingen › AI Adviseur · /ai (via PATCH /ai/settings) | generiek: render+validatie via schema (E2E-pad gedekt door jobs.max_concurrency) |
| `ai.budget.currency` | Budgetvaluta | enum | keuzelijst | global | Instellingen › AI Adviseur · /ai (via PATCH /ai/settings) | generiek: render+validatie via schema (E2E-pad gedekt door jobs.max_concurrency) |
| `ai.budget.max_calls_per_day` | Maximaal analyses per dag | int | getalveld | global | Instellingen › AI Adviseur · /ai (via PATCH /ai/settings) | generiek: render+validatie via schema (E2E-pad gedekt door jobs.max_concurrency) |
| `ai.budget.monthly_limit` | Maandbudget | float | getalveld | global | Instellingen › AI Adviseur · /ai (via PATCH /ai/settings) | generiek: render+validatie via schema (E2E-pad gedekt door jobs.max_concurrency) |
| `ai.budget.per_analysis_limit` | Kostenplafond per analyse | float | getalveld | global | Instellingen › AI Adviseur · /ai (via PATCH /ai/settings) | generiek: render+validatie via schema (E2E-pad gedekt door jobs.max_concurrency) |
| `ai.budget.usd_to_eur` | Wisselkoers USD→EUR | float | getalveld | global | Instellingen › AI Adviseur · /ai (via PATCH /ai/settings) | generiek: render+validatie via schema (E2E-pad gedekt door jobs.max_concurrency) |
| `ai.cache_ttl_days` | Cache-bewaartijd identieke cases | int | getalveld | global | Instellingen › AI Adviseur · /ai (via PATCH /ai/settings) | generiek: render+validatie via schema (E2E-pad gedekt door jobs.max_concurrency) |
| `ai.max_input_tokens` | Max invoertokens (schatting) | int | getalveld | global | Instellingen › AI Adviseur · /ai (via PATCH /ai/settings) | generiek: render+validatie via schema (E2E-pad gedekt door jobs.max_concurrency) |
| `ai.max_output_tokens` | Max antwoordtokens | int | getalveld | global | Instellingen › AI Adviseur · /ai (via PATCH /ai/settings) | generiek: render+validatie via schema (E2E-pad gedekt door jobs.max_concurrency) |
| `ai.max_retries` | Herhaalpogingen per analyse | int | getalveld | global | Instellingen › AI Adviseur · /ai (via PATCH /ai/settings) | generiek: render+validatie via schema (E2E-pad gedekt door jobs.max_concurrency) |
| `ai.mode` | AI-beleid | enum | keuzelijst | global | Instellingen › AI Adviseur · /ai (via PATCH /ai/settings) | generiek: render+validatie via schema (E2E-pad gedekt door jobs.max_concurrency) |
| `ai.openai.base_url` | OpenAI API-adres | string | tekstveld | global | Instellingen › AI Adviseur · /ai (via PATCH /ai/settings) | generiek: render+validatie via schema (E2E-pad gedekt door jobs.max_concurrency) |
| `ai.openai.model` | OpenAI-model | string | tekstveld | global | Instellingen › AI Adviseur · /ai (via PATCH /ai/settings) | generiek: render+validatie via schema (E2E-pad gedekt door jobs.max_concurrency) |
| `ai.price_table` | Prijstabel per model | json | editor | global | Instellingen › AI Adviseur · /ai (via PATCH /ai/settings) | generiek: render+validatie via schema (E2E-pad gedekt door jobs.max_concurrency) |
| `ai.provider` | AI-provider | enum | keuzelijst | global | Instellingen › AI Adviseur · /ai (via PATCH /ai/settings) | generiek: render+validatie via schema (E2E-pad gedekt door jobs.max_concurrency) |
| `ai.retention_days` | Bewaartermijn AI-cases | int | getalveld | global | Instellingen › AI Adviseur · /ai (via PATCH /ai/settings) | generiek: render+validatie via schema (E2E-pad gedekt door jobs.max_concurrency) |
| `ai.retry_budget_per_day` | Retrybudget per dag | int | getalveld | global | Instellingen › AI Adviseur · /ai (via PATCH /ai/settings) | generiek: render+validatie via schema (E2E-pad gedekt door jobs.max_concurrency) |
| `ai.task_models` | Model per taaktype | json | editor | global | Instellingen › AI Adviseur · /ai (via PATCH /ai/settings) | generiek: render+validatie via schema (E2E-pad gedekt door jobs.max_concurrency) |
| `ai.task_types` | Doelcases voor AI | list | chips / komma-lijst | global | Instellingen › AI Adviseur · /ai (via PATCH /ai/settings) | generiek: render+validatie via schema (E2E-pad gedekt door jobs.max_concurrency) |
| `ai.timeout_seconds` | Time-out per call | int | getalveld | global | Instellingen › AI Adviseur · /ai (via PATCH /ai/settings) | generiek: render+validatie via schema (E2E-pad gedekt door jobs.max_concurrency) |
| `encoding.autopilot_enabled` | AV1-autopilot (MediaGuard kiest bestanden) | bool | schakelaar | global | Instellingen › AV1 | generiek: render+validatie via schema (E2E-pad gedekt door jobs.max_concurrency) |
| `encoding.autopilot_interval_minutes` | Interval AV1-autopilot | int | getalveld | global | Instellingen › AV1 | generiek: render+validatie via schema (E2E-pad gedekt door jobs.max_concurrency) |
| `encoding.autopilot_lane_buffer` | Voorraad per baan (CPU en GPU) | int | getalveld | global | Instellingen › AV1 | generiek: render+validatie via schema (E2E-pad gedekt door jobs.max_concurrency) |
| `encoding.autopilot_queue_depth` | Bestanden tegelijk bij de encoder (0 = automatisch) | int | getalveld | global | Instellingen › AV1 | generiek: render+validatie via schema (E2E-pad gedekt door jobs.max_concurrency) |
| `encoding.auto_commit_after_verify` | Automatisch activeren na verificatie (Full Auto) | bool | schakelaar | global, media_type, instance, library, profile, series, movie, file | Instellingen › AV1 Studio | generiek: render+validatie via schema (E2E-pad gedekt door jobs.max_concurrency) |
| `encoding.cache_min_free_gb` | Pauzebodem encodercache (GB vrij) | int | getalveld | global | Instellingen › AV1 Studio (core pusht naar encoder `PUT /settings/storage`) | generiek: render+validatie via schema; push getest in `test_encoding.py::test_cache_floor_setting_is_pushed_to_encoder` |
| `encoding.canary_required` | Voorzichtige start: eerste conversies één voor één | int | getalveld | global | Instellingen › AV1 Studio | generiek: render+validatie via schema (E2E-pad gedekt door jobs.max_concurrency) |
| `encoding.crf` | CRF (0 = automatisch) | int | getalveld | global, media_type, instance, library, profile, series, movie, file | Instellingen › AV1 Studio | generiek: render+validatie via schema (E2E-pad gedekt door jobs.max_concurrency) |
| `encoding.encoder` | Encoder | enum | keuzelijst | global, media_type, instance, library, profile, series, movie, file | Instellingen › AV1 Studio | generiek: render+validatie via schema (E2E-pad gedekt door jobs.max_concurrency) |
| `encoding.identity_gate` | Identiteitspoort | enum | keuzelijst | global, media_type, instance, library, profile, series, movie, file | Instellingen › AV1 Studio | generiek: render+validatie via schema (E2E-pad gedekt door jobs.max_concurrency) |
| `encoding.keep_all_audio` | Alle audiosporen behouden | bool | schakelaar | global, media_type, instance, library, profile, series, movie, file | Instellingen › AV1 Studio | generiek: render+validatie via schema (E2E-pad gedekt door jobs.max_concurrency) |
| `encoding.keep_all_subtitles` | Alle ondertitels behouden | bool | schakelaar | global, media_type, instance, library, profile, series, movie, file | Instellingen › AV1 Studio | generiek: render+validatie via schema (E2E-pad gedekt door jobs.max_concurrency) |
| `encoding.min_saving_percent` | Minimale besparing (%) | int | getalveld | global, media_type, instance, library, profile, series, movie, file | Instellingen › AV1 Studio | generiek: render+validatie via schema (E2E-pad gedekt door jobs.max_concurrency) |
| `encoding.poll_interval_seconds` | Statusinterval encoder (s) | int | getalveld | global | Instellingen › AV1 Studio | generiek: render+validatie via schema (E2E-pad gedekt door jobs.max_concurrency) |
| `encoding.preset` | Preset (0 = automatisch) | int | getalveld | global, media_type, instance, library, profile, series, movie, file | Instellingen › AV1 Studio | generiek: render+validatie via schema (E2E-pad gedekt door jobs.max_concurrency) |
| `delivery.enabled` | Aanvragen automatisch leveren | bool | schakelaar | global | Instellingen › Aanvragen | generiek: render+validatie via schema (E2E-pad gedekt door jobs.max_concurrency) |
| `routing.anime.detect_animation_japanese` | Japanse animatie als anime herkennen | bool | schakelaar | global | Instellingen › Anime-routering | generiek: render+validatie via schema (E2E-pad gedekt door jobs.max_concurrency) |
| `routing.anime.enabled` | Anime automatisch naar anime-instantie | bool | schakelaar | global | Instellingen › Anime-routering | generiek: render+validatie via schema (E2E-pad gedekt door jobs.max_concurrency) |
| `routing.anime.max_age_hours` | Alleen recent toegevoegde titels (uur) | int | getalveld | global | Instellingen › Anime-routering | generiek: render+validatie via schema (E2E-pad gedekt door jobs.max_concurrency) |
| `routing.anime.mode` | Werkwijze | enum | keuzelijst | global | Instellingen › Anime-routering | generiek: render+validatie via schema (E2E-pad gedekt door jobs.max_concurrency) |
| `routing.anime.sweep_minutes` | Controle-interval (minuten) | int | getalveld | global | Instellingen › Anime-routering | generiek: render+validatie via schema (E2E-pad gedekt door jobs.max_concurrency) |
| `routing.anime.target_profile` | Kwaliteitsprofiel op anime-instantie | string | tekstveld | global, instance | Instellingen › Anime-routering | generiek: render+validatie via schema (E2E-pad gedekt door jobs.max_concurrency) |
| `routing.anime.target_root` | Hoofdmap op anime-instantie | string | tekstveld | global, instance | Instellingen › Anime-routering | generiek: render+validatie via schema (E2E-pad gedekt door jobs.max_concurrency) |
| `audio.anime_preference` | Anime-audiovoorkeur | enum | keuzelijst | global, media_type, instance, series, movie, season, episode, file | Instellingen › Audio & ondertiteling · /audio | generiek: render+validatie via schema (E2E-pad gedekt door jobs.max_concurrency) |
| `audio.nl_audio_mode` | Nederlandse audio | enum | keuzelijst | global, media_type, instance, series, movie, season, episode, file | Instellingen › Audio & ondertiteling · /audio | generiek: render+validatie via schema (E2E-pad gedekt door jobs.max_concurrency) |
| `audio.nl_subtitles_wanted` | Nederlandse ondertitels gewenst | enum | keuzelijst | global, media_type, instance, series, movie, season, episode, file | Instellingen › Audio & ondertiteling · /audio | generiek: render+validatie via schema (E2E-pad gedekt door jobs.max_concurrency) |
| `backup.bundle_retention` | Aantal te bewaren herstelbundels | int | getalveld | global | Instellingen › Back-ups | generiek: render+validatie via schema (E2E-pad gedekt door jobs.max_concurrency) |
| `backup.daily_enabled` | Dagelijkse back-up | bool | schakelaar | global | Instellingen › Back-ups · /quarantaine | generiek: render+validatie via schema (E2E-pad gedekt door jobs.max_concurrency) |
| `backup.offsite.email_channel` | E-mailkanaal voor de bijlage | int | getalveld | global | Instellingen › Back-ups | generiek: render+validatie via schema (E2E-pad gedekt door jobs.max_concurrency) |
| `backup.offsite.keep` | Aantal kopieën bewaren | int | getalveld | global | Instellingen › Back-ups | generiek: render+validatie via schema (E2E-pad gedekt door jobs.max_concurrency) |
| `backup.offsite.kind` | Herstelbundel ook buiten de NAS bewaren | enum | keuzelijst | global | Instellingen › Back-ups | generiek: render+validatie via schema (E2E-pad gedekt door jobs.max_concurrency) |
| `backup.offsite.path` | Map voor de kopie | string | tekstveld | global | Instellingen › Back-ups | generiek: render+validatie via schema (E2E-pad gedekt door jobs.max_concurrency) |
| `backup.offsite.s3_access_key` | S3-toegangssleutel (access key) | string | tekstveld | global | Instellingen › Back-ups | generiek: render+validatie via schema (E2E-pad gedekt door jobs.max_concurrency) |
| `backup.offsite.s3_bucket` | S3-bucket | string | tekstveld | global | Instellingen › Back-ups | generiek: render+validatie via schema (E2E-pad gedekt door jobs.max_concurrency) |
| `backup.offsite.s3_endpoint` | S3-adres | string | tekstveld | global | Instellingen › Back-ups | generiek: render+validatie via schema (E2E-pad gedekt door jobs.max_concurrency) |
| `backup.offsite.s3_prefix` | S3-map (prefix) | string | tekstveld | global | Instellingen › Back-ups | generiek: render+validatie via schema (E2E-pad gedekt door jobs.max_concurrency) |
| `backup.offsite.s3_region` | S3-regio | string | tekstveld | global | Instellingen › Back-ups | generiek: render+validatie via schema (E2E-pad gedekt door jobs.max_concurrency) |
| `backup.offsite.webdav_url` | WebDAV-adres | string | tekstveld | global | Instellingen › Back-ups | generiek: render+validatie via schema (E2E-pad gedekt door jobs.max_concurrency) |
| `backup.offsite.webdav_user` | WebDAV-gebruiker | string | tekstveld | global | Instellingen › Back-ups | generiek: render+validatie via schema (E2E-pad gedekt door jobs.max_concurrency) |
| `backup.retention_count` | Aantal te bewaren back-ups | int | getalveld | global | Instellingen › Back-ups · /quarantaine | generiek: render+validatie via schema (E2E-pad gedekt door jobs.max_concurrency) |
| `security.login_max_attempts` | Mislukte inlogpogingen vóór wachttijd | int | getalveld | global | Instellingen › Beveiliging | generiek: render+validatie via schema (E2E-pad gedekt door jobs.max_concurrency) |
| `security.login_max_delay_minutes` | Langste wachttijd na mislukte pogingen | int | getalveld | global | Instellingen › Beveiliging | generiek: render+validatie via schema (E2E-pad gedekt door jobs.max_concurrency) |
| `security.notify_failed_logins` | Melden bij veel mislukte inlogpogingen | bool | schakelaar | global | Instellingen › Beveiliging | generiek: render+validatie via schema (E2E-pad gedekt door jobs.max_concurrency) |
| `security.notify_new_login` | Melden bij inloggen vanaf een nieuw adres | bool | schakelaar | global | Instellingen › Beveiliging | generiek: render+validatie via schema (E2E-pad gedekt door jobs.max_concurrency) |
| `security.nudge_2fa` | Herinnering om tweestapsverificatie aan te zetten | bool | schakelaar | global | Instellingen › Beveiliging | generiek: render+validatie via schema (E2E-pad gedekt door jobs.max_concurrency) |
| `security.require_2fa_admins` | Tweestapsverificatie verplicht voor beheerders | bool | schakelaar | global | Instellingen › Beveiliging | generiek: render+validatie via schema (E2E-pad gedekt door jobs.max_concurrency) |
| `security.trusted_proxies` | Vertrouwde proxy's (bijv. cloudflared) | list | chips / komma-lijst | global | Instellingen › Beveiliging | generiek: render+validatie via schema (E2E-pad gedekt door jobs.max_concurrency) |
| `library.sync_interval_minutes` | Synchronisatie-interval bibliotheek | int | getalveld | global, instance | Instellingen › Bibliotheek | generiek: render+validatie via schema (E2E-pad gedekt door jobs.max_concurrency) |
| `dv.allow_av1` | Dolby Vision → AV1 toestaan | bool | schakelaar | global, library, profile, series, movie, file, job | Instellingen › Dolby Vision & HDR · /dolby-vision | generiek: render+validatie via schema (E2E-pad gedekt door jobs.max_concurrency) |
| `dv.allowed_profiles` | Vrijgegeven DV-invoerprofielen | list | chips / komma-lijst | global, library, profile, series, movie, file, job | Instellingen › Dolby Vision & HDR · /dolby-vision | generiek: render+validatie via schema (E2E-pad gedekt door jobs.max_concurrency) |
| `dv.dv81_route` | Dolby Vision 8.1 | enum | keuzelijst | global, library, profile, series, movie, file, job | Instellingen › Dolby Vision & HDR | generiek: render+validatie via schema (E2E-pad gedekt door jobs.max_concurrency) |
| `dv.hdr10plus_mode` | HDR10+-beleid | enum | keuzelijst | global, library, profile, series, movie, file, job | Instellingen › Dolby Vision & HDR · /dolby-vision | generiek: render+validatie via schema (E2E-pad gedekt door jobs.max_concurrency) |
| `dv.mode` | Dolby Vision-modus | enum | keuzelijst | global, library, profile, series, movie, file, job | Instellingen › Dolby Vision & HDR · /dolby-vision | generiek: render+validatie via schema (E2E-pad gedekt door jobs.max_concurrency) |
| `dv.show_playback_warnings` | Afspeelwaarschuwingen tonen | bool | schakelaar | global | Instellingen › Dolby Vision & HDR · /dolby-vision | generiek: render+validatie via schema (E2E-pad gedekt door jobs.max_concurrency) |
| `imports.stuck_after_hours` | Wachttijd vastgelopen download | int | getalveld | global, instance | Instellingen › Downloaden | generiek: render+validatie via schema (E2E-pad gedekt door jobs.max_concurrency) |
| `imports.stuck_auto_fix` | Vastgelopen downloads automatisch oplossen | bool | schakelaar | global, instance | Instellingen › Downloaden | generiek: render+validatie via schema (E2E-pad gedekt door jobs.max_concurrency) |
| `downloads.cleanup_enabled` | Oude downloads automatisch opruimen | bool | schakelaar | global | Instellingen › Downloads opruimen | generiek: render+validatie via schema (E2E-pad gedekt door jobs.max_concurrency) |
| `downloads.cleanup_interval_hours` | Opruimen elke (uur) | int | getalveld | global | Instellingen › Downloads opruimen | generiek: render+validatie via schema (E2E-pad gedekt door jobs.max_concurrency) |
| `downloads.min_free_percent` | Prullenbak eerder legen onder (% vrij) | int | getalveld | global | Instellingen › Downloads opruimen | generiek: render+validatie via schema (E2E-pad gedekt door jobs.max_concurrency) |
| `downloads.orphan_days` | Onbekende mappen opruimen na (dagen) | int | getalveld | global | Instellingen › Downloads opruimen | generiek: render+validatie via schema (E2E-pad gedekt door jobs.max_concurrency) |
| `downloads.public_seed_hours` | Openbare torrents minimaal delen (uur) | int | getalveld | global | Instellingen › Downloads opruimen | generiek: render+validatie via schema (E2E-pad gedekt door jobs.max_concurrency) |
| `downloads.recovery_enabled` | Niet-geïmporteerde downloads herstellen | bool | schakelaar | global | Instellingen › Downloads opruimen | generiek: render+validatie via schema (E2E-pad gedekt door jobs.max_concurrency) |
| `downloads.trash_days` | Prullenbak bewaren (dagen) | int | getalveld | global | Instellingen › Downloads opruimen | generiek: render+validatie via schema (E2E-pad gedekt door jobs.max_concurrency) |
| `downloads.unimported_days` | Nooit geïmporteerde downloads opruimen na (dagen) | int | getalveld | global | Instellingen › Downloads opruimen | generiek: render+validatie via schema (E2E-pad gedekt door jobs.max_concurrency) |
| `hooks.audio_score_step` | Scorebijstelling per audiostap | int | getalveld | global, media_type, instance, series, movie | Instellingen › Hooks & import | generiek: render+validatie via schema (E2E-pad gedekt door jobs.max_concurrency) |
| `hooks.post_import_analysis` | Analyse na import (identiteit, audio, AV1) | bool | schakelaar | global, media_type, instance, series, movie | Instellingen › Hooks & import | generiek: render+validatie via schema (E2E-pad gedekt door jobs.max_concurrency) |
| `hooks.pregrab_enabled` | Pre-grab-beleid toepassen | bool | schakelaar | global, media_type, instance, series, movie | Instellingen › Hooks & import | generiek: render+validatie via schema (E2E-pad gedekt door jobs.max_concurrency) |
| `hooks.pregrab_identity_check` | Afleveringnummer in releasenaam controleren | bool | schakelaar | global, media_type, instance, series, movie | Instellingen › Hooks & import | generiek: render+validatie via schema (E2E-pad gedekt door jobs.max_concurrency) |
| `hooks.preimport_cache_minutes` | Herhaalde importvraag onthouden (min) | int | getalveld | global | Instellingen › Hooks & import | generiek: render+validatie via schema (E2E-pad gedekt door jobs.max_concurrency) |
| `hooks.preimport_defer_on_uncertain` | Import uitstellen bij onzekere identiteit | bool | schakelaar | global, media_type, instance, series, movie | Instellingen › Hooks & import | generiek: render+validatie via schema (E2E-pad gedekt door jobs.max_concurrency) |
| `hooks.preimport_enforce_audio` | Verplichte audiotaal afdwingen bij import | bool | schakelaar | global, media_type, instance, series, movie | Instellingen › Hooks & import | generiek: render+validatie via schema (E2E-pad gedekt door jobs.max_concurrency) |
| `arr.cleanup_removed_titles` | Van TMDb/TVDB verwijderde titels zonder bestand opruimen | bool | schakelaar | global | Instellingen › Identiteit | generiek: render+validatie via schema (E2E-pad gedekt door jobs.max_concurrency) |
| `identity.ai_tiebreak` | AI beslist bij tegenstrijdig bewijs | bool | schakelaar | global | Instellingen › Identiteit | generiek: render+validatie via schema (E2E-pad gedekt door jobs.max_concurrency) |
| `identity.auto_replace_on_mismatch` | Automatisch vervangen bij bewezen verkeerde aflevering | bool | schakelaar | global, media_type, instance, series, movie | Instellingen › Identiteit · /anime | generiek: render+validatie via schema (E2E-pad gedekt door jobs.max_concurrency) |
| `identity.duration_tolerance_percent` | Tolerantie speelduur (%) | int | getalveld | global, media_type, instance, series, movie | Instellingen › Identiteit · /anime | generiek: render+validatie via schema (E2E-pad gedekt door jobs.max_concurrency) |
| `identity.mismatch_min_weight` | Minimale bewijssterkte voor 'verkeerde aflevering' | int | getalveld | global, media_type, instance, series, movie | Instellingen › Identiteit · /anime | generiek: render+validatie via schema (E2E-pad gedekt door jobs.max_concurrency) |
| `identity.remember_verified_hashes` | Hashes van geverifieerde bestanden onthouden | bool | schakelaar | global | Instellingen › Identiteit · /anime | generiek: render+validatie via schema (E2E-pad gedekt door jobs.max_concurrency) |
| `identity.thexem_enabled` | TheXEM-mapping gebruiken | bool | schakelaar | global, media_type, instance, series | Instellingen › Identiteit · /anime | generiek: render+validatie via schema (E2E-pad gedekt door jobs.max_concurrency) |
| `identity.uncertain_auto_keep_days` | Zwakke twijfel automatisch afsluiten na (dagen) | int | getalveld | global | Instellingen › Identiteit | generiek: render+validatie via schema (E2E-pad gedekt door jobs.max_concurrency) |
| `provisioning.auto_repair` | Afwijkingen automatisch herstellen | bool | schakelaar | global | Instellingen › Inrichten | generiek: render+validatie via schema (E2E-pad gedekt door jobs.max_concurrency) |
| `provisioning.drift_check_enabled` | Afwijkingen periodiek controleren | bool | schakelaar | global | Instellingen › Inrichten | generiek: render+validatie via schema (E2E-pad gedekt door jobs.max_concurrency) |
| `provisioning.drift_interval_hours` | Interval afwijkingscontrole | int | getalveld | global | Instellingen › Inrichten | generiek: render+validatie via schema (E2E-pad gedekt door jobs.max_concurrency) |
| `provisioning.on_startup` | Essentials bij opstarten inrichten | bool | schakelaar | global | Instellingen › Inrichten | generiek: render+validatie via schema (E2E-pad gedekt door jobs.max_concurrency) |
| `provisioning.startup_delay_seconds` | Wachttijd na opstarten | int | getalveld | global | Instellingen › Inrichten | generiek: render+validatie via schema (E2E-pad gedekt door jobs.max_concurrency) |
| `instances.health_interval_seconds` | Interval gezondheidscontrole | int | getalveld | global | Instellingen › Instanties | generiek: render+validatie via schema (E2E-pad gedekt door jobs.max_concurrency) |
| `app.public_url` | Adres voor de app | string | tekstveld | global | Instellingen › Koppelingen | generiek: render+validatie via schema (E2E-pad gedekt door jobs.max_concurrency) |
| `policy.profile_id` | Policyprofiel | string | tekstveld | global, media_type, instance, series, movie, season, episode, file | Instellingen › Kwaliteit & TRaSH · /kwaliteit | generiek: render+validatie via schema (E2E-pad gedekt door jobs.max_concurrency) |
| `policy.ranking_mode` | Rangschikking | enum | keuzelijst | global, media_type, instance, series, movie, season, episode, file | Instellingen › Kwaliteit & TRaSH · /kwaliteit | generiek: render+validatie via schema (E2E-pad gedekt door jobs.max_concurrency) |
| `mediaserver.kind` | Mediaserver | enum | keuzelijst | global | Instellingen › Mediaserver (Emby) | generiek: render+validatie via schema (E2E-pad gedekt door jobs.max_concurrency) |
| `mediaserver.pause_while_watching` | AV1 pauzeren tijdens kijken | enum | keuzelijst | global | Instellingen › Mediaserver (Emby) | generiek: render+validatie via schema (E2E-pad gedekt door jobs.max_concurrency) |
| `mediaserver.playback_fail_action` | Als de afspeeltest mislukt | enum | keuzelijst | global | Instellingen › Mediaserver (Emby) | generiek: render+validatie via schema (E2E-pad gedekt door jobs.max_concurrency) |
| `mediaserver.playback_test` | Afspeeltest | bool | schakelaar | global | Instellingen › Mediaserver (Emby) | generiek: render+validatie via schema (E2E-pad gedekt door jobs.max_concurrency) |
| `mediaserver.recent_watch_hours` | Recent bekeken = laatste (uur) | int | getalveld | global | Instellingen › Mediaserver (Emby) | generiek: render+validatie via schema (E2E-pad gedekt door jobs.max_concurrency) |
| `mediaserver.refresh_after_handoff` | Verversen na overdracht | bool | schakelaar | global | Instellingen › Mediaserver (Emby) | generiek: render+validatie via schema (E2E-pad gedekt door jobs.max_concurrency) |
| `mediaserver.skip_recent_watch` | Recent bekeken niet omzetten | bool | schakelaar | global | Instellingen › Mediaserver (Emby) | generiek: render+validatie via schema (E2E-pad gedekt door jobs.max_concurrency) |
| `mediaserver.url` | Adres van de mediaserver | string | tekstveld | global | Instellingen › Mediaserver (Emby) | generiek: render+validatie via schema (E2E-pad gedekt door jobs.max_concurrency) |
| `monitor.emergency_stop_hours` | Melden als de noodstop lang aanstaat (uur) | int | getalveld | global | Instellingen › Meldingen | generiek: render+validatie via schema (E2E-pad gedekt door jobs.max_concurrency) |
| `monitor.enabled` | Bewaking op de achtergrond | bool | schakelaar | global | Instellingen › Meldingen | generiek: render+validatie via schema (E2E-pad gedekt door jobs.max_concurrency) |
| `monitor.interval_minutes` | Bewaking: elke zoveel minuten | int | getalveld | global | Instellingen › Meldingen | generiek: render+validatie via schema (E2E-pad gedekt door jobs.max_concurrency) |
| `monitor.lane_idle_minutes` | Melden als een AV1-baan stilstaat (minuten) | int | getalveld | global | Instellingen › Meldingen | generiek: render+validatie via schema (E2E-pad gedekt door jobs.max_concurrency) |
| `monitor.request_stall_hours` | Melden als een aanvraag niet verder komt (uur) | int | getalveld | global | Instellingen › Meldingen | generiek: render+validatie via schema (E2E-pad gedekt door jobs.max_concurrency) |
| `notify.base_url` | Adres van MediaGuard in links | string | tekstveld | global | Instellingen › Meldingen | generiek: render+validatie via schema (E2E-pad gedekt door jobs.max_concurrency) |
| `notify.digest.daily` | Dagoverzicht sturen | bool | schakelaar | global | Instellingen › Meldingen | generiek: render+validatie via schema (E2E-pad gedekt door jobs.max_concurrency) |
| `notify.digest.time` | Tijd van het overzicht | string | tekstveld | global | Instellingen › Meldingen | generiek: render+validatie via schema (E2E-pad gedekt door jobs.max_concurrency) |
| `notify.digest.weekday` | Dag van het weekoverzicht | int | getalveld | global | Instellingen › Meldingen | generiek: render+validatie via schema (E2E-pad gedekt door jobs.max_concurrency) |
| `notify.digest.weekly` | Weekoverzicht sturen | bool | schakelaar | global | Instellingen › Meldingen | generiek: render+validatie via schema (E2E-pad gedekt door jobs.max_concurrency) |
| `notify.pref.av1_done` | AV1 klaar: wanneer melden | enum | keuzelijst | global | Instellingen › Meldingen | generiek: render+validatie via schema (E2E-pad gedekt door jobs.max_concurrency) |
| `notify.pref.downloads` | Downloads: wanneer melden | enum | keuzelijst | global | Instellingen › Meldingen | generiek: render+validatie via schema (E2E-pad gedekt door jobs.max_concurrency) |
| `notify.pref.errors` | Fouten: wanneer melden | enum | keuzelijst | global | Instellingen › Meldingen | generiek: render+validatie via schema (E2E-pad gedekt door jobs.max_concurrency) |
| `notify.pref.security` | Beveiliging: wanneer melden | enum | keuzelijst | global | Instellingen › Meldingen | generiek: render+validatie via schema (E2E-pad gedekt door jobs.max_concurrency) |
| `notify.pref.storage` | Opslag: wanneer melden | enum | keuzelijst | global | Instellingen › Meldingen | generiek: render+validatie via schema (E2E-pad gedekt door jobs.max_concurrency) |
| `notify.pref.waiting` | Wacht op jou: wanneer melden | enum | keuzelijst | global | Instellingen › Meldingen | generiek: render+validatie via schema (E2E-pad gedekt door jobs.max_concurrency) |
| `notify.rate_per_hour` | Maximaal aantal pushberichten per uur | int | getalveld | global | Instellingen › Meldingen | generiek: render+validatie via schema (E2E-pad gedekt door jobs.max_concurrency) |
| `notify.timezone` | Tijdzone | string | tekstveld | global | Instellingen › Meldingen | generiek: render+validatie via schema (E2E-pad gedekt door jobs.max_concurrency) |
| `storage.growth_alert_gb` | Waarschuwen bij snelle groei (GB per 24 uur) | int | getalveld | global | Instellingen › Meldingen | generiek: render+validatie via schema (E2E-pad gedekt door jobs.max_concurrency) |
| `storage.ledger_notify_daily` | Dagelijks opslagoverzicht | bool | schakelaar | global | Instellingen › Meldingen | generiek: render+validatie via schema (E2E-pad gedekt door jobs.max_concurrency) |
| `storage.ledger_notify_weekly` | Wekelijks opslagoverzicht | bool | schakelaar | global | Instellingen › Meldingen | generiek: render+validatie via schema (E2E-pad gedekt door jobs.max_concurrency) |
| `migration.command_wait_seconds` | Wachttijd Sonarr/Radarr-commando's | int | getalveld | global | Instellingen › Migratie | generiek: render+validatie via schema (E2E-pad gedekt door jobs.max_concurrency) |
| `migration.episode_wait_seconds` | Wachttijd afleveringsmetadata | int | getalveld | global | Instellingen › Migratie | generiek: render+validatie via schema (E2E-pad gedekt door jobs.max_concurrency) |
| `migration.max_backup_mb` | Maximale back-upgrootte | int | getalveld | global | Instellingen › Migratie | generiek: render+validatie via schema (E2E-pad gedekt door jobs.max_concurrency) |
| `migration.path_sample_size` | Steekproef padcontrole | int | getalveld | global | Instellingen › Migratie | generiek: render+validatie via schema (E2E-pad gedekt door jobs.max_concurrency) |
| `migration.restart_wait_seconds` | Wachttijd herstart na restore | int | getalveld | global | Instellingen › Migratie | generiek: render+validatie via schema (E2E-pad gedekt door jobs.max_concurrency) |
| `maintenance.retention_days_audit` | Auditlog bewaren (dagen) | int | getalveld | global | Instellingen › Onderhoud | generiek: render+validatie via schema (E2E-pad gedekt door jobs.max_concurrency) |
| `maintenance.retention_days_events` | Gebeurtenissen bewaren (dagen) | int | getalveld | global | Instellingen › Onderhoud | generiek: render+validatie via schema (E2E-pad gedekt door jobs.max_concurrency) |
| `maintenance.retention_days_history` | Geschiedenis bewaren (dagen) | int | getalveld | global | Instellingen › Onderhoud | generiek: render+validatie via schema (E2E-pad gedekt door jobs.max_concurrency) |
| `maintenance.retention_days_hooks` | Grab/import-beslissingen bewaren (dagen) | int | getalveld | global | Instellingen › Onderhoud | generiek: render+validatie via schema (E2E-pad gedekt door jobs.max_concurrency) |
| `maintenance.retention_days_jobs` | Afgeronde taken bewaren (dagen) | int | getalveld | global | Instellingen › Onderhoud | generiek: render+validatie via schema (E2E-pad gedekt door jobs.max_concurrency) |
| `maintenance.retention_days_notifications` | Gelezen meldingen bewaren (dagen) | int | getalveld | global | Instellingen › Onderhoud | generiek: render+validatie via schema (E2E-pad gedekt door jobs.max_concurrency) |
| `maintenance.retry_transient_failures` | Tijdelijke fouten automatisch opnieuw proberen | bool | schakelaar | global | Instellingen › Onderhoud | generiek: render+validatie via schema (E2E-pad gedekt door jobs.max_concurrency) |
| `updates.check_enabled` | Melden als er een nieuwe versie is | bool | schakelaar | global | Instellingen › Onderhoud | generiek: render+validatie via schema (E2E-pad gedekt door jobs.max_concurrency) |
| `subtitles.bazarr_url` | Adres van Bazarr | string | tekstveld | global | Instellingen › Ondertitels | generiek: render+validatie via schema (E2E-pad gedekt door jobs.max_concurrency) |
| `subtitles.nl_auto` | Nederlandse ondertitels automatisch zoeken | bool | schakelaar | global | Instellingen › Ondertitels | generiek: render+validatie via schema (E2E-pad gedekt door jobs.max_concurrency) |
| `subtitles.opensubtitles_daily` | OpenSubtitles: maximaal per dag | int | getalveld | global | Instellingen › Ondertitels | generiek: render+validatie via schema (E2E-pad gedekt door jobs.max_concurrency) |
| `subtitles.opensubtitles_enabled` | OpenSubtitles gebruiken (zonder Bazarr) | bool | schakelaar | global | Instellingen › Ondertitels | generiek: render+validatie via schema (E2E-pad gedekt door jobs.max_concurrency) |
| `backfill.base_backoff_hours` | Basis-wachttijd na misser | int | getalveld | global, media_type, instance | Instellingen › Ontbrekende media · /downloads (scope instantie) | generiek: render+validatie via schema (E2E-pad gedekt door jobs.max_concurrency) |
| `backfill.batch_size` | Batchgrootte | int | getalveld | global, media_type, instance | Instellingen › Ontbrekende media · /downloads (scope instantie) | generiek: render+validatie via schema (E2E-pad gedekt door jobs.max_concurrency) |
| `backfill.collect_candidates` | Kandidaten en afwijzingen bewaren | enum | keuzelijst | global, media_type, instance | Instellingen › Ontbrekende media · /downloads (scope instantie) | generiek: render+validatie via schema (E2E-pad gedekt door jobs.max_concurrency) |
| `backfill.command_wait_seconds` | Wachttijd op zoekresultaat | int | getalveld | global, media_type, instance | Instellingen › Ontbrekende media · /downloads (scope instantie) | generiek: render+validatie via schema (E2E-pad gedekt door jobs.max_concurrency) |
| `backfill.enabled` | Backfill ingeschakeld | bool | schakelaar | global, media_type, instance | Instellingen › Ontbrekende media · /downloads (scope instantie) | generiek: render+validatie via schema (E2E-pad gedekt door jobs.max_concurrency) |
| `backfill.excluded` | Uitsluiten van backfill | bool | schakelaar | instance, series, movie, season, episode | Bibliotheek › titel › Instellingen voor deze titel ('Niet meer zoeken') · Instellingen › Alle instellingen (scope serie/film/instantie) | core test_webpunten.py; E2E werk-webpunten (titelkaart) |
| `backfill.include_cutoff_unmet` | Ook upgrades (onder cutoff) zoeken | bool | schakelaar | global, media_type, instance | Instellingen › Ontbrekende media · /downloads (scope instantie) | generiek: render+validatie via schema (E2E-pad gedekt door jobs.max_concurrency) |
| `backfill.interval_hours` | Backfill-interval | int | getalveld | global, media_type, instance | Instellingen › Ontbrekende media · /downloads (scope instantie) | generiek: render+validatie via schema (E2E-pad gedekt door jobs.max_concurrency) |
| `backfill.max_backoff_days` | Maximale wachttijd | int | getalveld | global, media_type, instance | Instellingen › Ontbrekende media · /downloads (scope instantie) | generiek: render+validatie via schema (E2E-pad gedekt door jobs.max_concurrency) |
| `backfill.max_searches_per_day` | Max. zoekopdrachten per dag | int | getalveld | global, media_type, instance | Instellingen › Ontbrekende media · /downloads (scope instantie) | generiek: render+validatie via schema (E2E-pad gedekt door jobs.max_concurrency) |
| `backfill.max_searches_per_hour` | Max. zoekopdrachten per uur | int | getalveld | global, media_type, instance | Instellingen › Ontbrekende media · /downloads (scope instantie) | generiek: render+validatie via schema (E2E-pad gedekt door jobs.max_concurrency) |
| `backfill.priority` | Backfill-prioriteit | enum | keuzelijst | global, instance, series, movie | Instellingen › Ontbrekende media · /downloads (scope instantie) | generiek: render+validatie via schema (E2E-pad gedekt door jobs.max_concurrency) |
| `backfill.priority_recent_days` | Recente afleveringen voorrang (dagen) | int | getalveld | global, media_type, instance | Instellingen › Ontbrekende media · /downloads (scope instantie) | generiek: render+validatie via schema (E2E-pad gedekt door jobs.max_concurrency) |
| `backfill.season_search_threshold` | Seizoenszoekactie vanaf | int | getalveld | global, media_type, instance | Instellingen › Ontbrekende media · /downloads (scope instantie) | generiek: render+validatie via schema (E2E-pad gedekt door jobs.max_concurrency) |
| `backfill.unavailable_after_attempts` | 'Niet beschikbaar' na pogingen | int | getalveld | global, media_type, instance | Instellingen › Ontbrekende media · /downloads (scope instantie) | generiek: render+validatie via schema (E2E-pad gedekt door jobs.max_concurrency) |
| `backfill.unavailable_retry_days` | Nieuwe poging na 'niet beschikbaar' | int | getalveld | global, media_type, instance | Instellingen › Ontbrekende media · /downloads (scope instantie) | generiek: render+validatie via schema (E2E-pad gedekt door jobs.max_concurrency) |
| `wanted.relaxed_allowed_categories` | Toegestane versoepelingen | list | chips / komma-lijst | global, media_type, instance | Instellingen › Ontbrekende media | generiek: render+validatie via schema (E2E-pad gedekt door jobs.max_concurrency) |
| `optimization.av1_threshold_1080_kbps` | AV1-drempel 1080p (kbps) | int | getalveld | global, media_type, instance | Instellingen › Optimalisatie | generiek: render+validatie via schema (E2E-pad gedekt door jobs.max_concurrency) |
| `optimization.av1_threshold_2160_kbps` | AV1-drempel 2160p (kbps) | int | getalveld | global, media_type, instance | Instellingen › Optimalisatie | generiek: render+validatie via schema (E2E-pad gedekt door jobs.max_concurrency) |
| `optimization.av1_threshold_720_kbps` | AV1-drempel 720p (kbps) | int | getalveld | global, media_type, instance | Instellingen › Optimalisatie | generiek: render+validatie via schema (E2E-pad gedekt door jobs.max_concurrency) |
| `optimization.batch_size` | Batchgrootte | int | getalveld | global | Instellingen › Optimalisatie | generiek: render+validatie via schema (E2E-pad gedekt door jobs.max_concurrency) |
| `optimization.downloads_need_confirmation` | Betere versies pas na akkoord downloaden | bool | schakelaar | global | Instellingen › Optimalisatie | generiek: render+validatie via schema (E2E-pad gedekt door jobs.max_concurrency) |
| `optimization.dry_run_valid_hours` | Geldigheid dry-run (uren) | int | getalveld | global | Instellingen › Optimalisatie | generiek: render+validatie via schema (E2E-pad gedekt door jobs.max_concurrency) |
| `optimization.estimated_av1_saving_percent` | Verwachte AV1-besparing H.264 (%) | int | getalveld | global | Instellingen › Optimalisatie | generiek: render+validatie via schema (E2E-pad gedekt door jobs.max_concurrency) |
| `optimization.estimated_av1_saving_percent_hevc` | Verwachte AV1-besparing HEVC (%) | int | getalveld | global | Instellingen › Optimalisatie | generiek: render+validatie via schema (E2E-pad gedekt door jobs.max_concurrency) |
| `optimization.max_active_downloads` | Max. actieve downloads | int | getalveld | global, media_type, instance | Instellingen › Optimalisatie | generiek: render+validatie via schema (E2E-pad gedekt door jobs.max_concurrency) |
| `optimization.max_concurrent_encodes` | Max. gelijktijdige AV1-encodes (CPU/GPU) | int | getalveld | global | Instellingen › Optimalisatie | generiek: render+validatie via schema (E2E-pad gedekt door jobs.max_concurrency) |
| `optimization.max_gb_per_day` | Max. GB per dag | int | getalveld | global, media_type, instance | Instellingen › Optimalisatie | generiek: render+validatie via schema (E2E-pad gedekt door jobs.max_concurrency) |
| `optimization.max_searches_per_hour` | Max. zoekopdrachten per uur (indexerquota) | int | getalveld | global, media_type, instance | Instellingen › Optimalisatie | generiek: render+validatie via schema (E2E-pad gedekt door jobs.max_concurrency) |
| `optimization.staging_reserve_gb` | Stagingreserve (GB) | int | getalveld | global | Instellingen › Optimalisatie | generiek: render+validatie via schema (E2E-pad gedekt door jobs.max_concurrency) |
| `optimization.window` | Tijdvenster | time_window | tijdvenster | global | Instellingen › Optimalisatie | generiek: render+validatie via schema (E2E-pad gedekt door jobs.max_concurrency) |
| `optimization.window_enabled` | Alleen binnen tijdvenster uitvoeren | bool | schakelaar | global | Instellingen › Optimalisatie | generiek: render+validatie via schema (E2E-pad gedekt door jobs.max_concurrency) |
| `quarantine.auto_purge` | Verlopen originelen automatisch opruimen | bool | schakelaar | global | Instellingen › Quarantaine & herstel · /quarantaine | generiek: render+validatie via schema (E2E-pad gedekt door jobs.max_concurrency) |
| `quarantine.max_size_gb` | Maximale omvang quarantaine (GB) | int | getalveld | global | Instellingen › Quarantaine & herstel · /quarantaine | generiek: render+validatie via schema (E2E-pad gedekt door jobs.max_concurrency) |
| `quarantine.min_age_hours` | Minimale bewaartijd bij vol quotum (uur) | int | getalveld | global | Instellingen › Quarantaine & herstel · /quarantaine | generiek: render+validatie via schema (E2E-pad gedekt door jobs.max_concurrency) |
| `quarantine.mode` | Originelen na vervanging | enum | keuzelijst | global | Instellingen › Quarantaine & herstel · /quarantaine | E2E: radiokaarten, persistent na herladen |
| `quarantine.purge_target_percent` | Opruimen tot (% van het maximum) | int | getalveld | global | Instellingen › Quarantaine & herstel | generiek: render+validatie via schema (E2E-pad gedekt door jobs.max_concurrency) |
| `quarantine.release_check_full_decode` | Volledige decodecontrole vóór opruimen | bool | schakelaar | global | Instellingen › Quarantaine & herstel · /quarantaine | generiek: render+validatie via schema (E2E-pad gedekt door jobs.max_concurrency) |
| `quarantine.release_check_timeout_minutes` | Maximale duur decodecontrole (minuten) | int | getalveld | global | Instellingen › Quarantaine & herstel · /quarantaine | generiek: render+validatie via schema (E2E-pad gedekt door jobs.max_concurrency) |
| `quarantine.retention_days` | Bewaartermijn originelen (dagen) | int | getalveld | global | Instellingen › Quarantaine & herstel · /quarantaine | E2E: inline wijzigen+herladen; grijs bij modus direct |
| `quarantine.safety_net_gb` | Maximale omvang vangnet (GB) | int | getalveld | global | Instellingen › Quarantaine & herstel | generiek: render+validatie via schema (E2E-pad gedekt door jobs.max_concurrency) |
| `quarantine.safety_net_hours` | Vangnet bij direct verwijderen (uur) | int | getalveld | global | Instellingen › Quarantaine & herstel | generiek: render+validatie via schema (E2E-pad gedekt door jobs.max_concurrency) |
| `recovery.hardlink_policy` | Bestanden met hardlinks (seeding) | enum | keuzelijst | global, media_type, instance, library | Instellingen › Quarantaine & herstel · /quarantaine | generiek: render+validatie via schema (E2E-pad gedekt door jobs.max_concurrency) |
| `recovery.min_free_gb` | Minimale vrije ruimte (GB) | int | getalveld | global | Instellingen › Quarantaine & herstel · /quarantaine | generiek: render+validatie via schema (E2E-pad gedekt door jobs.max_concurrency) |
| `recovery.quarantine_location` | Locatie quarantaine | enum | keuzelijst | global | Instellingen › Quarantaine & herstel · /quarantaine | generiek: render+validatie via schema (E2E-pad gedekt door jobs.max_concurrency) |
| `recovery.refresh_arr_after_commit` | Sonarr/Radarr laten herscannen na vervanging | bool | schakelaar | global | Instellingen › Quarantaine & herstel · /quarantaine | generiek: render+validatie via schema (E2E-pad gedekt door jobs.max_concurrency) |
| `recovery.truenas_url` | TrueNAS-API-URL | string | tekstveld | global | Instellingen › Quarantaine & herstel · /quarantaine | generiek: render+validatie via schema (E2E-pad gedekt door jobs.max_concurrency) |
| `recovery.truenas_verify_tls` | TLS-certificaat TrueNAS controleren | bool | schakelaar | global | Instellingen › Quarantaine & herstel · /quarantaine | generiek: render+validatie via schema (E2E-pad gedekt door jobs.max_concurrency) |
| `recovery.zfs_dataset` | Dataset voor snapshots | string | tekstveld | global | Instellingen › Quarantaine & herstel · /quarantaine | generiek: render+validatie via schema (E2E-pad gedekt door jobs.max_concurrency) |
| `recovery.zfs_snapshot_enabled` | ZFS-snapshot vóór vervanging | bool | schakelaar | global | Instellingen › Quarantaine & herstel · /quarantaine | generiek: render+validatie via schema (E2E-pad gedekt door jobs.max_concurrency) |
| `seerr.request_sync_interval_minutes` | Interval aanvragen-synchronisatie | int | getalveld | global | Instellingen › Seerr · /seerr | generiek: render+validatie via schema (E2E-pad gedekt door jobs.max_concurrency) |
| `seerr.url` | Seerr-URL | string | tekstveld | global | Instellingen › Seerr · /seerr | generiek: render+validatie via schema (E2E-pad gedekt door jobs.max_concurrency) |
| `logging.level` | Logniveau | enum | keuzelijst | global | Instellingen › Systeem | generiek: render+validatie via schema (E2E-pad gedekt door jobs.max_concurrency) |
| `system.hub_url` | Hub-adres | string | tekstveld | global | Instellingen › Systeem | generiek: render+validatie via schema (E2E-pad gedekt door jobs.max_concurrency) |
| `system.timezone` | Tijdzone | string | tekstveld | global | Instellingen › Systeem | generiek: render+validatie via schema (E2E-pad gedekt door jobs.max_concurrency) |
| `ui.theme` | Thema | enum | keuzelijst | global | Instellingen › Systeem | E2E: setup-wizard schrijft ui.theme; themawissel |
| `resources.av1_cpu_percent` | Maximaal CPU-gebruik voor AV1 | int | getalveld | global | Instellingen › Systeem & prestaties | generiek: render+validatie via schema (E2E-pad gedekt door jobs.max_concurrency) |
| `resources.encode_window` | Tijdvenster voor encoderen | time_window | tijdvenster | global | Instellingen › Systeem & prestaties | generiek: render+validatie via schema (E2E-pad gedekt door jobs.max_concurrency) |
| `resources.encode_window_enabled` | Alleen encoderen binnen een tijdvenster | bool | schakelaar | global | Instellingen › Systeem & prestaties | generiek: render+validatie via schema (E2E-pad gedekt door jobs.max_concurrency) |
| `resources.encodes_cpu` | Encodes tegelijk op de processor (CPU) | int | getalveld | global | Instellingen › Systeem & prestaties | generiek: render+validatie via schema (E2E-pad gedekt door jobs.max_concurrency) |
| `resources.encodes_gpu` | Encodes tegelijk op de videokaart (GPU) | int | getalveld | global | Instellingen › Systeem & prestaties | generiek: render+validatie via schema (E2E-pad gedekt door jobs.max_concurrency) |
| `resources.max_cpu_temp_c` | Pauzeer AV1 boven CPU-temperatuur | int | getalveld | global | Instellingen › Systeem & prestaties | generiek: render+validatie via schema (E2E-pad gedekt door jobs.max_concurrency) |
| `integrity.recovery_verify_checksums` | Checksums controleren bij kopiëren en activeren | bool | vergrendeld (alleen-lezen) | global | Instellingen › Veiligheid | generiek: render+validatie via schema (E2E-pad gedekt door jobs.max_concurrency) |
| `integrity.release_check_before_purge` | Vrijgavecontrole vereist vóór automatisch opruimen | bool | vergrendeld (alleen-lezen) | global | Instellingen › Veiligheid · /quarantaine | generiek: render+validatie via schema (E2E-pad gedekt door jobs.max_concurrency) |
| `integrity.require_recoverable_copy` | Herstelbare kopie vereist vóór vervanging | bool | vergrendeld (alleen-lezen) | global | Instellingen › Veiligheid | generiek: render+validatie via schema (E2E-pad gedekt door jobs.max_concurrency) |
| `integrity.verify_streams_before_activate` | Streams verifiëren vóór activatie | bool | vergrendeld (alleen-lezen) | global | Instellingen › Veiligheid | generiek: render+validatie via schema (E2E-pad gedekt door jobs.max_concurrency) |
| `library.write_enabled` | MediaGuard mag bibliotheekbestanden vervangen | bool | schakelaar | global | Instellingen › Veiligheid | generiek: render+validatie via schema (E2E-pad gedekt door jobs.max_concurrency) |
| `jobs.max_concurrency` | Gelijktijdige achtergrondtaken | int | getalveld | global | Instellingen › Wachtrij & workers | E2E: wijzigen+herladen, ongeldig (422) behoudt oude waarde, herstel standaard |

## Bedienbare acties per scherm (Q2.1) — niet-instelling-controls

| Scherm | Actie | Endpoint |
|---|---|---|
| Topbar | Noodstop aan/uit (met reden, banner) | `POST /system/emergency-stop` (E2E) |
| Topbar | Meldingen lezen / alles gelezen | `GET /notifications`, `POST /notifications/read-all` |
| Topbar | Taal (per gebruiker) / thema (per browser) | `PUT /auth/me/locale`, localStorage `mg.theme` (+ standaard `ui.theme`) (E2E thema) |
| Setup / login | Eerste beheerder, inloggen, uitloggen | `/auth/setup`, `/auth/login`, `/auth/logout` (E2E) |
| Instellingen › Gebruikers | Toevoegen, rol, uitschakelen, wachtwoord | `/auth/users` |
| Instellingen › Geheimen | Zetten, vervangen, verwijderen | `/settings/secrets/{name}` |
| Instellingen › Instanties | Test, pauzeer/hervat, URL/API-key/naam wijzigen, Seerr-routetest | `/instances/{id}/test|suspend|resume`, `PATCH /instances/{id}`, `POST /seerr/route-test` |
| Bibliotheek | Filteren, sorteren, inventariseren, backfill per instantie | `GET /library`, `POST /library/sync`, `POST /instances/{id}/backfill` |
| Serie-detail | Seizoen monitoren, toekomstige seizoenen, zoeken | `PUT /instances/{id}/series/{sid}/seasons/{n}`, `PATCH /instances/{id}/series/{sid}`, `POST /instances/{id}/search` |
| Aflevering | Identiteit controleren, zoek nu, ontspannen opnieuw, markeer mismatch | `/identity/{i}/episode/{id}/verify|decision`, `/instances/{id}/search`, `/wanted/{id}/retry-relaxed` |
| Ontbrekend | Diagnose, zoek nu, uitsluiten, backfill-plan (dry-run), backfill per instantie | `/wanted/*`, `/instances/{id}/backfill` |
| Kalender | Week navigeren, niet-gemonitord tonen | `GET /calendar` |
| Release Lab | Interactief zoeken (geen grab), rangschikken met beleid | `GET /instances/{id}/releases`, `POST /policy/simulate` |
| Kwaliteit & TRaSH | Profiel bewerken/herstellen, TRaSH import/verwijderen | `/profiles/*`, `/policy/trash*` |
| Dolby Vision & HDR | Beleid per scope, modi, profielen, proefencode, afspeelketens | `/dolby-vision/policies|test-encode|playback-confirmations` |
| Anime mapping | Handmatige mapping, TheXEM verversen, releasenaam ontleden | `/identity/anime-mappings/*`, `/identity/parse` |
| Downloads & indexers | Indexers/clients aan/uit + test, padmappings, rootmappen | `/instances/{id}/indexers|download-clients|remote-path-mappings|root-folders` |
| Seerr | URL + key (opslaan na geslaagde test), sync, routering | `/seerr/settings|requests/sync|routing|route-test` |
| Migratie & optimalisatie | Dry-run, plan starten/pauzeren/hervatten, export | `/optimization/*`; wizard `/migration/*` (backend nog niet geladen) |
| Quarantaine & herstel | Gebruik/quotum, modus, controleer nu, terugzetten, verwijderen (preview + force), bulk, opruimen | `/quarantine/*` (E2E) |
| Jobs & logs | Filteren, details/logs, opnieuw, annuleren, events live, audit | `/jobs*`, `/events/recent`, SSE `/events/stream`, `/audit` |
| AI Adviseur | Provider/model/key/modus, test, toestemming, budget, casussen | `/ai/*` |
| Updates & diagnostics | Versie, componenten, diagnose, opslag, back-up maken/downloaden/herstellen, supportbundel, coverage | `/admin/*`, `/backups*` |
| AV1 Studio | Overzicht (alle workers per baan met voortgang per fase, hardware, grafieken), wachtrij (filters incl. fouten, bulk, jobdetail met live log), beslissingen, kwaliteit (verificaties, voor/na, DV, proefdraai), instellingen (workers, grenzen, profielen, bibliotheken, onderdelen, onderhoud) | engine-API via core `/api/v1/encoder/*` (login + intern token) |

## Encoder-afstelling

Uitvoeringsknoppen van de AV1-engine (`GET/PUT /api/v1/encoder/afstelling`). De UI (Ruimte › Instellingen › Uitvoering) rendert ze generiek met naam, uitleg, bereik en standaard; een omgevingsvariabele kan een knop vastzetten (dan alleen-lezen).

| Key | Label | Type | UI-control | Scopes | Waar in de UI | Test |
|---|---|---|---|---|---|---|
| `encoder.max_4k_gpu` | Max. 4K-encodes tegelijk op de GPU | int | getalveld | engine | Ruimte › Instellingen › Uitvoering | generiek: afstelling-lijst (e2e werk-r035) |
| `encoder.max_4k_cpu` | Max. 4K-encodes tegelijk op de CPU | int | getalveld | engine | Ruimte › Instellingen › Uitvoering | generiek: afstelling-lijst (e2e werk-r035) |
| `encoder.nacht_start` | Nachtvenster: begin (uur) | int | getalveld | engine | Ruimte › Instellingen › Uitvoering | generiek: afstelling-lijst (e2e werk-r035) |
| `encoder.nacht_einde` | Nachtvenster: einde (uur) | int | getalveld | engine | Ruimte › Instellingen › Uitvoering | generiek: afstelling-lijst (e2e werk-r035) |
| `encoder.lange_jobs_alleen_nacht` | Lange jobs alleen 's nachts | bool | schakelaar | engine | Ruimte › Instellingen › Uitvoering | generiek: afstelling-lijst (e2e werk-r035) |
| `encoder.lange_job_uren` | Grens voor een lange job (uren) | float | getalveld | engine | Ruimte › Instellingen › Uitvoering | generiek: afstelling-lijst (e2e werk-r035) |
| `encoder.vastloper_minuten` | Encode stoppen na zoveel minuten zonder voortgang | int | getalveld | engine | Ruimte › Instellingen › Uitvoering | generiek: afstelling-lijst (e2e werk-r035) |
| `encoder.vmaf_minimum` | Minimale VMAF-score | float | getalveld | engine | Ruimte › Instellingen › Uitvoering | generiek: afstelling-lijst (e2e werk-r035) |
| `encoder.ssim_minimum` | Minimale SSIM-score (terugval) | float | getalveld | engine | Ruimte › Instellingen › Uitvoering | generiek: afstelling-lijst (e2e werk-r035) |
| `encoder.bitrate_ondergrens_schaal` | Schaal bitrate-ondergrens | float | getalveld | engine | Ruimte › Instellingen › Uitvoering | generiek: afstelling-lijst (e2e werk-r035) |
| `encoder.gpu_hardware_decode` | Bron uitpakken op de GPU | bool | schakelaar | engine | Ruimte › Instellingen › Uitvoering | generiek: afstelling-lijst (e2e werk-r035) |
| `encoder.gpu_lookahead` | Vooruitkijken op de GPU (lookahead) | bool | schakelaar | engine | Ruimte › Instellingen › Uitvoering | generiek: afstelling-lijst (e2e werk-r035) |
| `encoder.gpu_vmaf_sturing` | GPU-bitrate sturen op VMAF | bool | schakelaar | engine | Ruimte › Instellingen › Uitvoering | generiek: afstelling-lijst (e2e werk-r035) |
| `encoder.vmaf_doel` | VMAF-doelwaarde (GPU-sturing) | float | getalveld | engine | Ruimte › Instellingen › Uitvoering | generiek: afstelling-lijst (e2e werk-r035) |
| `encoder.gpu_plekken_auto` | GPU-plekken automatisch meten | bool | schakelaar | engine | Ruimte › Instellingen › Uitvoering | generiek: afstelling-lijst (e2e werk-r035) |
| `encoder.cpu_tweede_plek_proef` | Tweede CPU-plek voorzichtig proberen | bool | schakelaar | engine | Ruimte › Instellingen › Uitvoering | generiek: afstelling-lijst (e2e werk-r035) |
| `encoder.geheugen_reserve_gb` | Geheugenreserve voor AV1 (GB) | float | getalveld | engine | Ruimte › Instellingen › Uitvoering | generiek: afstelling-lijst (e2e werk-r035) |
| `encoder.anime_route` | Route voor anime | keuze | keuze | engine | Ruimte › Instellingen › Uitvoering | generiek: afstelling-lijst (e2e werk-r035) |
| `encoder.max_cpu_uren` | Max. duur van één CPU-encode (uren) | float | getalveld | engine | Ruimte › Instellingen › Uitvoering | generiek: afstelling-lijst (e2e werk-r035) |
| `encoder.cpu_harde_grens` | Harde CPU-grens voor encodes | bool | schakelaar | engine | Ruimte › Instellingen › Uitvoering | generiek: afstelling-lijst (e2e werk-r035) |
