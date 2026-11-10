# ruff: noqa: E501 - lange Nederlandse uitlegteksten
"""Nederlandse uitleg voor instellingen die bij registratie nog geen ``help_nl`` hebben.

De gebruiker wil alles in de app kunnen instellen én begrijpen (rapport 5): elke instelling
krijgt daarom een Nederlandse naam en uitleg. ``SettingsRegistry.register`` vult hiermee een
lege ``help_nl`` aan; ``tests/test_coverage_matrix.py`` faalt zodra een instelling zonder
uitleg wordt toegevoegd. Uitleg die een module zelf meegeeft heeft altijd voorrang.
"""

from __future__ import annotations

HELP_NL: dict[str, str] = {
    # AI-adviseur
    "ai.action_timeout_seconds": "Hoe lang een automatische AI-actie (bijv. opnieuw zoeken) mag duren voordat MediaGuard hem afbreekt en de veilige standaard aanhoudt.",
    "ai.anthropic.base_url": "Adres van de Claude-API. Alleen wijzigen als je via een proxy of gateway werkt.",
    "ai.anthropic.effort": "Hoe lang Claude mag nadenken per analyse. Hoger is grondiger maar duurder en trager.",
    "ai.bounded_auto.max_actions_per_day": "Maximaal aantal acties dat de AI per dag zelf uitvoert. Daarboven wordt een actie naar de volgende dag verschoven.",
    "ai.bounded_auto.min_benchmark_accuracy": "Hoe vaak (0–1) de AI in de proeftest gelijk moet hebben voordat hij zelf acties mag uitvoeren.",
    "ai.bounded_auto.min_improvement": "Hoeveel beter (0–1) de AI moet scoren dan de vaste regels voordat hij automatisch mag beslissen.",
    "ai.bounded_auto.min_shadow_cases": "Aantal gevallen dat de AI eerst alleen meekijkt (zonder iets te doen) voordat automatisch beslissen kan worden ontgrendeld.",
    "ai.budget.currency": "Valuta waarin het AI-budget en de kosten worden getoond.",
    "ai.budget.max_calls_per_day": "Maximaal aantal AI-analyses per dag. Daarboven beslissen de vaste regels.",
    "ai.budget.per_analysis_limit": "Maximale kosten van één analyse. Een duurdere analyse wordt niet gestart.",
    "ai.cache_ttl_days": "Hoe lang een AI-antwoord op een identiek geval wordt hergebruikt, zodat hetzelfde geval niet twee keer betaald wordt.",
    "ai.max_input_tokens": "Hoeveel tekst (geschat) er maximaal naar de AI gaat per analyse. Houdt de kosten voorspelbaar.",
    "ai.max_output_tokens": "Maximale lengte van het antwoord van de AI.",
    "ai.max_retries": "Hoe vaak een mislukte AI-aanroep opnieuw wordt geprobeerd.",
    "ai.openai.base_url": "Adres van de OpenAI-API. Alleen wijzigen als je via een proxy of gateway werkt.",
    "ai.retention_days": "Hoe lang afgehandelde AI-gevallen met hun uitleg bewaard blijven.",
    "ai.retry_budget_per_day": "Maximaal aantal herhaalpogingen per dag over alle AI-analyses samen.",
    "ai.task_types": "Bij welke soorten twijfel de AI wordt ingeschakeld (bijv. anime-identiteit, waarom iets ontbreekt).",
    "ai.timeout_seconds": "Hoe lang MediaGuard op één AI-antwoord wacht.",
    # AV1
    "encoding.autopilot_interval_minutes": "Hoe vaak de autopilot kijkt of er nieuwe bestanden naar AV1 kunnen.",
    "encoding.crf": "Kwaliteitsfactor voor CPU-encodes. 0 = automatisch per resolutie en HDR (aanbevolen); lager is betere kwaliteit en grotere bestanden.",
    "encoding.identity_gate": "Of een bestand eerst zeker de juiste aflevering of film moet zijn voordat het naar AV1 gaat. Standaard worden twijfelgevallen en verkeerde bestanden niet omgezet.",
    "encoding.keep_all_subtitles": "Alle ondertitelsporen meenemen in de AV1-versie. Uitzetten houdt alleen de gekozen talen; de vrijgavecontrole bewaakt dat er niets ongewild verdwijnt.",
    "encoding.poll_interval_seconds": "Hoe vaak MediaGuard de status van lopende encodes ophaalt.",
    "encoding.preset": "Snelheid/efficiëntie van de CPU-encoder. 0 = automatisch (aanbevolen); lager is trager maar kleiner.",
    # routering en audio
    "routing.anime.mode": "Hoe anime naar de anime-instantie gaat: automatisch (MediaGuard en bij twijfel de AI beslissen), alleen voorstellen, of uit.",
    "audio.anime_preference": "Welke audio bij anime de voorkeur krijgt: volgens het kwaliteitsprofiel, Japans, of Dual Audio.",
    "audio.nl_subtitles_wanted": "Of Nederlandse ondertitels gewenst zijn: volgens het profiel, altijd, of niet.",
    # imports en downloads
    "imports.stuck_after_hours": "Na hoeveel uur een voltooide download die niet wordt geïmporteerd als vastgelopen geldt; MediaGuard lost het daarna zelf op.",
    "downloads.cleanup_interval_hours": "Hoe vaak de opruimer downloads die niemand meer nodig heeft naar de prullenbak verplaatst.",
    "downloads.min_free_percent": "Komt de vrije ruimte onder dit percentage, dan wordt de prullenbak eerder geleegd (oudste eerst).",
    "downloads.trash_days": "Hoe lang opgeruimde downloads in de prullenbak blijven voordat ze echt verwijderd worden.",
    "downloads.unimported_days": "Voltooide downloads die na zoveel dagen nog niet zijn geïmporteerd, gaan naar de prullenbak.",
    "hooks.post_import_analysis": "Na elke import controleert MediaGuard identiteit, audio en of het bestand naar AV1 kan.",
    # inrichting, migratie, onderhoud
    "provisioning.drift_interval_hours": "Hoe vaak MediaGuard controleert of Sonarr/Radarr/Prowlarr/Seerr nog volgens de inrichting staan, en afwijkingen zelf herstelt.",
    "provisioning.startup_delay_seconds": "Wachttijd na het opstarten voordat de inrichting wordt gecontroleerd, zodat Sonarr/Radarr eerst klaar zijn.",
    "migration.restart_wait_seconds": "Hoe lang MediaGuard na het terugzetten van een back-up wacht tot Sonarr/Radarr opnieuw zijn opgestart.",
    "maintenance.retention_days_events": "Hoe lang gebeurtenissen (live-updates) bewaard blijven.",
    "maintenance.retention_days_hooks": "Hoe lang de grab- en importbeslissingen van Sonarr/Radarr bewaard blijven.",
    "maintenance.retention_days_notifications": "Hoe lang gelezen meldingen bewaard blijven.",
    # backfill
    "backfill.command_wait_seconds": "Hoe lang MediaGuard op het resultaat van één zoekopdracht wacht.",
    "backfill.include_cutoff_unmet": "Ook zoeken naar betere versies van bestanden die onder de kwaliteitsgrens zitten, niet alleen naar ontbrekende.",
    "backfill.max_backoff_days": "Maximale wachttijd tussen zoekpogingen voor iets dat steeds niet gevonden wordt.",
    "backfill.priority": "Hoeveel van het dagelijkse zoekquotum naar backfill gaat ten opzichte van nieuwe aanvragen.",
    "backfill.priority_recent_days": "Afleveringen die korter dan zoveel dagen geleden zijn uitgezonden krijgen voorrang.",
    "backfill.unavailable_retry_days": "Na 'nergens beschikbaar' wordt het na zoveel dagen opnieuw geprobeerd.",
    # optimalisatie
    "optimization.av1_threshold_1080_kbps": "1080p-bestanden boven deze bitrate komen in aanmerking voor AV1.",
    "optimization.av1_threshold_2160_kbps": "4K-bestanden boven deze bitrate komen in aanmerking voor AV1.",
    "optimization.av1_threshold_720_kbps": "720p-bestanden boven deze bitrate komen in aanmerking voor AV1.",
    "optimization.batch_size": "Hoeveel items het optimalisatieplan per ronde verwerkt.",
    "optimization.estimated_av1_saving_percent_hevc": "Verwachte besparing bij het omzetten van HEVC naar AV1; gebruikt voor de prognose.",
    "optimization.max_active_downloads": "Maximaal aantal downloads van betere versies tegelijk, zodat nieuwe aanvragen voorrang houden.",
    "optimization.max_concurrent_encodes": "Maximaal aantal AV1-encodes die het optimalisatieplan tegelijk aanbiedt.",
    "optimization.max_gb_per_day": "Maximale hoeveelheid die het optimalisatieplan per dag downloadt.",
    "optimization.max_searches_per_hour": "Maximaal aantal zoekopdrachten per uur, om indexers niet te overbelasten.",
    "optimization.staging_reserve_gb": "Ruimte die altijd vrij blijft voor tijdelijke encode-bestanden.",
    "optimization.window_enabled": "Het optimalisatieplan alleen binnen het ingestelde tijdvenster laten draaien (bijv. 's nachts).",
    # terugzetten
    "quarantine.release_check_timeout_minutes": "Hoe lang de volledige decodecontrole van een nieuw bestand mag duren.",
    "recovery.refresh_arr_after_commit": "Na een vervanging Sonarr/Radarr het bestand opnieuw laten inlezen, zodat grootte en kwaliteit kloppen.",
    "recovery.truenas_verify_tls": "Het certificaat van TrueNAS controleren bij het maken van snapshots. Alleen uitzetten bij een zelfondertekend certificaat.",
    "integrity.require_recoverable_copy": "Een origineel wordt pas vervangen als er een herstelbare kopie is (quarantaine of snapshot). Vergrendeld voor je veiligheid.",
    # overig
    "seerr.request_sync_interval_minutes": "Hoe vaak aanvragen uit Seerr worden opgehaald (webhooks werken direct).",
    "logging.level": "Hoeveel detail in de logboeken komt. 'Info' is genoeg; 'debug' alleen bij een probleem.",
    "ui.theme": "Standaardthema voor nieuwe gebruikers (iedereen kan zelf kiezen in het profielmenu). "
                "Standaard: Systeem (volgt licht/donker van het apparaat).",
    "jobs.max_concurrency": "Hoeveel achtergrondtaken (synchronisatie, controles, zoekrondes) tegelijk mogen draaien. Geldt binnen een paar seconden, zonder herstart.",
    # externe back-up, mediaserver, meldingen (nieuwe modules; alleen als de module zelf geen uitleg geeft)
    "backup.offsite.s3_access_key": "Toegangssleutel (access key) van je S3-opslag voor de externe kopie van de herstelbundel. De geheime sleutel staat bij Geheimen.",
    "backup.offsite.s3_bucket": "Naam van de S3-bucket waarin de herstelbundel wordt bewaard.",
    "backup.offsite.s3_endpoint": "Adres van de S3-dienst (bijv. van Backblaze, Wasabi of een eigen MinIO). Leeg = Amazon S3.",
    "backup.offsite.s3_prefix": "Map binnen de bucket waarin de herstelbundels komen.",
    "backup.offsite.s3_region": "Regio van de S3-bucket.",
    "backup.offsite.webdav_url": "Adres van de WebDAV-map (bijv. Nextcloud) waarin de herstelbundel wordt bewaard.",
    "backup.offsite.webdav_user": "Gebruikersnaam voor WebDAV. Het wachtwoord staat bij Geheimen.",
    "mediaserver.kind": "Welke mediaserver je gebruikt (Emby, Jellyfin of Plex). 'Automatisch' herkent hem aan het adres.",
    "mediaserver.playback_fail_action": "Wat er gebeurt als de afspeeltest na een AV1-vervanging mislukt: alleen melden, of het origineel automatisch terugzetten.",
    "mediaserver.recent_watch_hours": "Titels die binnen zoveel uur zijn bekeken, worden niet omgezet zolang iemand ze nog kan willen kijken.",
    "notify.digest.weekly": "Elke week een overzicht sturen: wat er is geleverd, omgezet en bespaard, en wat er eventueel op je wacht.",
    "notify.timezone": "Tijdzone voor de tijden in meldingen en overzichten.",
}
