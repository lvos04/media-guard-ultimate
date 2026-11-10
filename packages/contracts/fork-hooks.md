# Contract: fork-hooks (Sonarr/Radarr-fork → mediaguard-core)

## Omgevingsvariabelen in iedere fork-container
| Variabele | Betekenis | Default |
|---|---|---|
| `MEDIAGUARD_URL` | Basis-URL van core, bv. `http://mediaguard-core:8100` | leeg = hooks uit |
| `MEDIAGUARD_INSTANCE_ID` | `sonarr-series`, `sonarr-anime`, `radarr-films`, `radarr-animefilms`, … | verplicht als URL gezet |
| `MEDIAGUARD_HOOK_KEY` | Gedeeld geheim per instantie | verplicht als URL gezet |
| `MEDIAGUARD_HOOK_TIMEOUT_MS` | Timeout per hook-call | `3000` |
| `MEDIAGUARD_HOOK_FAIL_POLICY` | `open` (accepteer bij storing) of `closed` (weiger) | `open` |
| `MEDIAGUARD_SHADOW_MODE` | `1` = weiger lokaal ALLE grabs met reden `MEDIAGUARD_SHADOW_MODE` (zonder netwerkcall). Voor shadow-import tijdens migratie. | `0` |

Bij storing/timeout: log een waarschuwing in de fork-log, pas de fail policy toe, en
neem het in de afwijzingsreden op (`MEDIAGUARD_UNAVAILABLE`). Nooit eindeloos wachten.

Authenticatie: header `X-MediaGuard-Hook-Key: <key>`.

## POST {MEDIAGUARD_URL}/api/v1/hooks/pre-grab
Wordt aangeroepen door een extra `IDownloadDecisionEngineSpecification` (Sonarr) /
`IDownloadDecisionEngineSpecification` (Radarr) — voor RSS én (interactief/automatisch) zoeken.

Request:
```json
{
  "instance_id": "sonarr-anime",
  "app": "sonarr",
  "search_type": "rss|search|interactive",
  "release": {
    "title": "[Group] Show - 05 (1080p) [Dual Audio]",
    "guid": "…", "indexer": "Nyaa", "indexer_id": 3, "protocol": "torrent|usenet",
    "size": 1234567890, "age_hours": 12.5, "seeders": 10,
    "quality": "WEBDL-1080p", "resolution": 1080, "source": "web",
    "languages": ["English", "Japanese"],
    "release_group": "Group",
    "custom_format_score": 1500, "custom_formats": ["Dual Audio"],
    "indexer_flags": []
  },
  "series": {"id": 12, "tvdb_id": 81189, "imdb_id": "tt…", "title": "…", "series_type": "anime|standard|daily"},
  "movie": null,
  "episodes": [{"id": 345, "season_number": 1, "episode_number": 5, "absolute_episode_number": 5}],
  "parsed": {"season_number": 1, "episode_numbers": [5], "absolute_episode_numbers": [5],
             "full_season": false, "special": false}
}
```
Radarr: `movie = {"id", "tmdb_id", "imdb_id", "title", "year"}`, `series/episodes = null`.

Response (200):
```json
{"decision": "accept|reject", "reason_code": "AUDIO_PREFERENCE|IDENTITY_MISMATCH|EMERGENCY_STOP|INSTANCE_SUSPENDED|…", "message": "Nederlandse uitleg", "score_adjustment": 0}
```
`reject` → fork geeft `DownloadRejection` met `"MediaGuard: <message> [<reason_code>]"`
(zichtbaar in interactieve zoekresultaten = extended rejection diagnostics).

## POST {MEDIAGUARD_URL}/api/v1/hooks/pre-import
Extra `IImportDecisionEngineSpecification` (Sonarr: LocalEpisode, Radarr: LocalMovie).
Request:
```json
{
  "instance_id": "sonarr-series", "app": "sonarr",
  "path": "/downloads/complete/tv/Show.S01E03.mkv", "size": 123,
  "download_client": "SABnzbd", "download_id": "SABnzbd_nzo_…",
  "release_title": "Show.S01E03.1080p.WEB-DL", "is_upgrade": false,
  "series": {…}, "movie": null,
  "episodes": [{"id":…, "season_number":1, "episode_number":3, "absolute_episode_number":null}],
  "parsed": {…},
  "media_info": {"video_codec": "h264", "audio_languages": ["eng"], "subtitles": ["dut"], "run_time_seconds": 1320, "resolution": "1920x1080"}
}
```
Response: `{"decision": "accept|reject|defer", "reason_code": "…", "message": "…"}`.
`defer` = tijdelijk weigeren (fork probeert later opnieuw te importeren).

## Events: POST {MEDIAGUARD_URL}/api/v1/hooks/arr-webhook/{instance_id}
Ingebouwde Sonarr/Radarr **Webhook-connection** (geen forkpatch nodig). Core registreert
deze zelf via `POST /api/v3/notification` met alle events aan.
Authenticatie: HTTP Basic `username = instance_id`, `password = hook key`
(Sonarr/Radarr webhook ondersteunen username/password), óf header `X-MediaGuard-Hook-Key`.
Payload = standaard upstream webhook-JSON (`eventType`: Grab, Download, Rename,
SeriesAdd, SeriesDelete, EpisodeFileDelete, MovieAdded, MovieDelete, MovieFileDelete,
Health, HealthRestored, ApplicationUpdate, ManualInteractionRequired, Test).
