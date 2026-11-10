# Seerr compatibility contract tests: results

Spec: MEDIAGUARD_GOAL.md DEEL H, R4, R6.6, Q3.12. Run on 2026-11-02 in the Linux sandbox
(Docker 29.9.2, Compose v5.6.1). TMDB, skyhook.sonarr.tv and api.radarr.video were reached for
real (through the sandbox's TLS-inspecting egress proxy; its CA is mounted read-only via
`MG_TEST_CA_BUNDLE`). Every service is a throw-away local container; `run.sh` removes all
containers **and volumes** afterwards.

Command: `MG_TEST_CA_BUNDLE=/root/.ccr/ca-bundle.crt tests/contract/seerr/run.sh`
(about 2:10 including compose up/down; the images must already be present).

## Versions under test

| component | image | version |
|---|---|---|
| Seerr (unmodified) | `ghcr.io/seerr-team/seerr:v3.5.1` (latest release on 2026-11-02) | 3.5.1, commit `564bfb08b54caa374bddaf820abfd226e5e69151` |
| Jellyfin (media server Seerr is initialised with) | `ghcr.io/jellyfin/jellyfin:12.4` (= `latest`) | 12.4.0 |
| sonarr-series, sonarr-anime | `mediaguard/sonarr-fork:bc54afb-mg1` | Sonarr 4.0.20.3014 (`-mg1`) |
| radarr-films, radarr-animefilms | `mediaguard/radarr-fork:a96bf7c-mg1` | Radarr 6.4.4.10685 (`-mg1`) |
| MediaGuard core | local checkout `apps/core`, run with `/tmp/corevenv/bin/python -m mediaguard` | working tree of 2026-11-02 |

## Stack (`docker-compose.yml`, project `mgseerr`)

Jellyfin, Seerr, the four fork instances (fail policy `open`, hooks to the stub MediaGuard from
`tests/integration/fakes`), host ports 2xxxx. No indexer or download client: availability is
produced by putting a real (ffmpeg, 20 min, 320x180) MKV in the instance's root folder and
running `RescanMovie`/`RescanSeries`; an AV1 copy (libsvtav1) simulates MediaGuard's conversion.
Core runs on the host on a free port: instance `url` = `http://127.0.0.1:<port>` (how core
reaches the fork), `public_url` = `http://<instance>:<port>` (how Seerr reaches it; this is what
the route test compares).

Setup is fully API-driven: Jellyfin `/Startup/Configuration`, `GET+POST /Startup/User`,
`/Startup/RemoteAccess`, `/Startup/Complete`; Seerr `POST /api/v1/auth/jellyfin`
(`serverType: 2`), `GET /settings/main` (API key), `POST /settings/initialize`; then everything
else via `X-Api-Key`.

## Result

**Official run (unmodified core): 16 passed, 1 failed (17 tests); 136 of 137 assertions passed.**
The single failure is a real core bug (B1 below): `POST /api/v1/seerr/cutover` with `confirm`
gets HTTP 400 from Seerr. The run stops test 17 at that assertion.

**Verification run with the proposed B1 fix** (copy of core with the 2-line change in
`seerr/client.py`, started via `MG_CORE_DIR=<copy>`): **17/17 passed, 142/142 assertions**,
including cutover confirm, the route test after cutover and an exact rollback.

| # | test | proves | result |
|---|---|---|---|
| 1 | `jellyfin_wizard_and_seerr_initialization` | Jellyfin wizard via API; Seerr initialised with the Jellyfin admin; Seerr API key works, a wrong key gets 403 | PASS |
| 2 | `prepare_fork_instances` | root folder, tag `seerr` and a non-default quality profile per instance | PASS |
| 3 | `seerr_test_endpoint_accepts_every_fork` | Seerr's own `POST /settings/{sonarr,radarr}/test` succeeds for all 4 forks: system/status, profiles, root folders, tags, `urlBase`; Sonarr v4, so Seerr skips language profiles (`null`). Wrong key or unreachable port gives a clean HTTP 500 "Failed to connect" | PASS |
| 4 | `configure_four_servers_in_seerr` | `POST /settings/sonarr` + `/radarr` x2, one default per type, `GET /service/{kind}/{id}` (request-modal data). Seerr returns the server `apiKey` unmasked to admins | PASS |
| 5 | `request_movie_default_server_radarr_films` | movie request with no serverId goes to radarr-films: root `/media/movies`, profile = Seerr `activeProfileId`, monitored, `minimumAvailability`, server tag. Not in radarr-animefilms; Seerr `externalServiceId` = Radarr id, media status 3 | PASS |
| 6 | `request_anime_movie_with_server_selection_radarr_animefilms` | `serverId` = radarr-animefilms (as Seerr's request modal sends it) goes to radarr-animefilms with *that* server's profile/root/tag; not in radarr-films | PASS |
| 7 | `request_series_specific_seasons_sonarr_series` | Breaking Bad S1+S2 lands in sonarr-series: root, profile, `seriesType standard`, season folders, seasons 1,2 monitored and 0,3,4,5 not (episodes too), `monitorNewItems=none` (future-season policy from the Seerr server), tag. Not in sonarr-anime | PASS |
| 8 | `request_anime_series_with_server_selection_sonarr_anime` | Cowboy Bebop + serverId sonarr-anime lands in sonarr-anime: `/media/anime`, anime profile, `seriesType anime`, `monitorNewItems=all`. Not in sonarr-series | PASS |
| 9 | `anime_without_server_selection_lands_on_default_server` | **Documents the R4 limit**: anime with no serverId (Evangelion) goes to the *default* sonarr-series. Seerr only switches `seriesType` to anime (TMDB keyword 210024) and would use the default server's `activeAnime*` | PASS (as expected) |
| 10 | `rerequest_existing_titles_no_duplicates` | Same movie again gets **409** "Request for this media already exists". Same seasons again gets **202** "No seasons available to request". Extra season 3 updates the **same** Sonarr series (PUT `/api/v3/series`; S3 monitored, S1/S2 kept). A title already in radarr-films (added outside Seerr, unmonitored) is re-monitored and linked to the existing id, with no duplicate. Item counts stay 1 throughout | PASS |
| 11 | `movie_becomes_available_via_radarr_scan` | file + `RescanMovie`, then `POST /settings/jobs/radarr-scan/run`: media status **5**, request status **5** (completed); another movie with no file stays 3 | PASS |
| 12 | `still_available_after_av1_replacement` | AV1 file added, H.264 removed, rescan: Radarr has the AV1 file (`videoCodec AV1`) under the same movie id. Seerr stays **5** after `radarr-scan` **and** `availability-sync`, still linked to the same id | PASS |
| 13 | `series_partially_available_via_sonarr_scan` | S01E01 file + `RescanSeries`, then `sonarr-scan`: tv media status **4**, season 1 = 4, season 2 = 3 | PASS |
| 14 | `core_route_test_all_four_routes_ok` | core: the 4 seeded instances re-pointed (PATCH url/public_url/key, live-verified); `PUT /seerr/settings` (verified, version 3.5.1); `/seerr/test` (2+2 servers, keys masked); **`POST /seerr/route-test`: all 4 routes OK**, every check true (server_found, url, api key, isDefault, is4k, profile, root, Seerr's own test; sonarr-anime also `activeAnime*`), no unmapped servers; `/seerr/mapping` agrees | PASS |
| 15 | `core_request_sync` | `POST /seerr/requests/sync`: all 7 Seerr requests, status/media status 5/5 + `available_at` for the movie. They are `unlinked` before core's library sync; after `POST /instances/{id}/sync?mode=inline` all 7 are `linked` to the instance that really holds the title | PASS |
| 16 | `core_detects_misconfigured_seerr_servers` | (A) radarr-animefilms server pointing at radarr-films: `server_found=false`, stray server listed as unmapped, overall not OK, radarr-films itself still OK. (B) wrong key: `api_key_matches=false` + `seerr_connection_test=false`. (C) anime Sonarr made default: `is_default` fails on both Sonarr routes. (D) non-existent root: `root:activeDirectory=false`. (E) wrong port: not OK. After restore: all OK again | PASS |
| 17 | `core_cutover_and_rollback_against_real_seerr` | Seerr server for radarr-animefilms points at an "old" Radarr. The cutover preview shows the diff and changes nothing. **Confirm fails: Seerr 400 `request/body/id is read-only`** (B1). With the fix: Seerr is re-pointed (host/port/key; profile/root/default kept), the route test is OK, and rollback restores the exact old host/port/key | **FAIL** (PASS with fix) |

## Bugs and findings for core (`apps/core/mediaguard/seerr/`)

* **B1 (bug, blocks cutover and rollback).** `client.py` `server_update()` / `server_create()`
  send the server dict as-is. `service.seerr_cutover()` builds `new = {**current, **desired}`
  from `GET /settings/{kind}`, which includes `id`, and `seerr_rollback()` PUTs
  `previous["server"]`, which also includes `id`. Seerr 3.5.1 validates request bodies against
  its OpenAPI spec (`seerr-api.yml`: `id` is `readOnly`), so it answers **HTTP 400
  `request/body/id is read-only`** and core returns 502. `null` values (e.g.
  `activeLanguageProfileId: null`) are rejected the same way ("must be number").
  Fix (verified, 17/17): strip both in the client:
  ```python
  @staticmethod
  def _writable(body): return {k: v for k, v in body.items() if k != "id" and v is not None}
  # server_update: json=self._writable(body); server_create: json=self._writable(body)
  ```
  The unit tests' fake Seerr should also reject `id` in PUT/POST bodies so this cannot regress.
* **B2 (latent, from Seerr's spec, not exercised live).** `seerr_cutover(create_if_missing=True)`
  POSTs only connection fields + `isDefault`/`is4k`/`name` + overrides. Seerr requires
  `activeProfileName` (both types), `minimumAvailability` (Radarr) and `enableSeasonFolders`
  (Sonarr), so it would get a 400. Proposal: when creating, fill `activeProfileName` from the
  instance's profile list, `minimumAvailability` from the instance default (`released`), and
  `enableSeasonFolders: true`. Likewise, when `active_profile_id` is overridden, also set
  `activeProfileName` (otherwise Seerr shows a stale name).
* **F1 (improvement).** `sync_requests` maps `request.serverId` to an instance only through
  `instance_seerr_routing` rows that have `seerr_server_id`. With the default routing (no rows)
  and for every request without an explicit server (serverId `null`), linking falls back to
  `tmdbId`/`tvdbId` in `lib_items` (worked here: 7/7 after library sync). More robust: also use
  `media.serviceId`/`serviceId4k` (Seerr fills these for every request it sent), and let
  `route_report` persist the server id it matched by URL.

## What this proves / honest gaps

* Seerr accepts the forks' API unchanged: test, add, update (PUT series/movie), lookup of
  existing titles, sync scans, and availability-sync.
* **Anime routing (R4).** Seerr 3.5.1 has *no* automatic per-server anime routing. Override
  rules (`OverrideRule`) can change only root/profile/tags **within the default server** and
  never the server. The only way to reach sonarr-anime/radarr-animefilms is explicit server
  selection (`serverId`; in the UI an admin, or a user with `REQUEST_ADVANCED`, picks the server
  in the request modal). Within one Sonarr server, Seerr detects anime by TMDB keyword 210024 and
  then uses `activeAnimeProfileId`/`activeAnimeDirectory`/`animeTags`/`animeSeriesType`. For
  anime *films* Radarr has no anime fields at all. Test 9 pins this down; core's route test
  reports it as the `anime_selection` notice.
* Not tested: 4K servers (no 4K instance in R4); HTTPS/reverse proxy/base-URL in front of the
  forks (all plain HTTP, `baseUrl ""`); Plex/Emby; Seerr's download-sync/queue display
  (no download client in this stack, see `tests/integration/forks`); non-admin users,
  approval flow and quotas (all requests by the admin, auto-approved); a real AV1 conversion by
  the MediaGuard encoder (the file swap is simulated with an ffmpeg AV1 file); Jellyfin library
  scanning (Jellyfin has no libraries; availability comes from the Sonarr/Radarr scans, which is
  how Seerr behaves with `syncEnabled` servers).
* Seerr's `availability-sync` checks `externalServiceId` on **every** Radarr server of that 4K
  mode (guarded only by a `tmdbId` comparison). The fork instances use overlapping ids (movie 1
  exists in both Radarr instances); this was harmless here, and MediaGuard must keep a file
  present during replacement (as test 12 does) so that `hasFile` never becomes false.

## Per-assertion log (official run, unmodified core)

Raw data: `.run/assertions.json` (official), `.run/assertions-corefix.json` (with B1 fix),
`.run/pytest*.log`, `.run/logs-*.txt` (the `.run/` directory is git-ignored; it is
regenerated each run).

| test | assertion | result |
|---|---|---|
| `01_jellyfin_wizard_and_seerr_initialization` | jellyfin wizard completed | PASS |
| `01_jellyfin_wizard_and_seerr_initialization` | seerr /auth/jellyfin creates admin from Jellyfin user | PASS |
| `01_jellyfin_wizard_and_seerr_initialization` | seerr admin user has ADMIN permission | PASS |
| `01_jellyfin_wizard_and_seerr_initialization` | seerr initialized | PASS |
| `01_jellyfin_wizard_and_seerr_initialization` | seerr API key auth (X-Api-Key) works as admin | PASS |
| `01_jellyfin_wizard_and_seerr_initialization` | seerr rejects a wrong API key | PASS |
| `02_prepare_fork_instances` | 4 fork instances prepared (root folder, tag, profile) | PASS |
| `03_seerr_test_endpoint_accepts_every_fork` | sonarr-series: Seerr test endpoint 200 | PASS |
| `03_seerr_test_endpoint_accepts_every_fork` | sonarr-series: profiles visible to Seerr incl. 'HD-1080p' | PASS |
| `03_seerr_test_endpoint_accepts_every_fork` | sonarr-series: root folder /media/tv visible to Seerr | PASS |
| `03_seerr_test_endpoint_accepts_every_fork` | sonarr-series: tags visible to Seerr | PASS |
| `03_seerr_test_endpoint_accepts_every_fork` | sonarr-series: urlBase empty | PASS |
| `03_seerr_test_endpoint_accepts_every_fork` | sonarr-series: languageProfiles null for Sonarr v4 | PASS |
| `03_seerr_test_endpoint_accepts_every_fork` | sonarr-anime: Seerr test endpoint 200 | PASS |
| `03_seerr_test_endpoint_accepts_every_fork` | sonarr-anime: profiles visible to Seerr incl. 'HD - 720p/1080p' | PASS |
| `03_seerr_test_endpoint_accepts_every_fork` | sonarr-anime: root folder /media/anime visible to Seerr | PASS |
| `03_seerr_test_endpoint_accepts_every_fork` | sonarr-anime: tags visible to Seerr | PASS |
| `03_seerr_test_endpoint_accepts_every_fork` | sonarr-anime: urlBase empty | PASS |
| `03_seerr_test_endpoint_accepts_every_fork` | sonarr-anime: languageProfiles null for Sonarr v4 | PASS |
| `03_seerr_test_endpoint_accepts_every_fork` | radarr-films: Seerr test endpoint 200 | PASS |
| `03_seerr_test_endpoint_accepts_every_fork` | radarr-films: profiles visible to Seerr incl. 'HD-1080p' | PASS |
| `03_seerr_test_endpoint_accepts_every_fork` | radarr-films: root folder /media/movies visible to Seerr | PASS |
| `03_seerr_test_endpoint_accepts_every_fork` | radarr-films: tags visible to Seerr | PASS |
| `03_seerr_test_endpoint_accepts_every_fork` | radarr-films: urlBase empty | PASS |
| `03_seerr_test_endpoint_accepts_every_fork` | radarr-animefilms: Seerr test endpoint 200 | PASS |
| `03_seerr_test_endpoint_accepts_every_fork` | radarr-animefilms: profiles visible to Seerr incl. 'HD - 720p/1080p' | PASS |
| `03_seerr_test_endpoint_accepts_every_fork` | radarr-animefilms: root folder /media/animefilms visible to Seerr | PASS |
| `03_seerr_test_endpoint_accepts_every_fork` | radarr-animefilms: tags visible to Seerr | PASS |
| `03_seerr_test_endpoint_accepts_every_fork` | radarr-animefilms: urlBase empty | PASS |
| `03_seerr_test_endpoint_accepts_every_fork` | wrong fork API key -> Seerr test fails cleanly | PASS |
| `03_seerr_test_endpoint_accepts_every_fork` | unreachable fork -> Seerr test fails cleanly | PASS |
| `04_configure_four_servers_in_seerr` | sonarr-series: POST /settings/sonarr -> 201 | PASS |
| `04_configure_four_servers_in_seerr` | sonarr-anime: POST /settings/sonarr -> 201 | PASS |
| `04_configure_four_servers_in_seerr` | radarr-films: POST /settings/radarr -> 201 | PASS |
| `04_configure_four_servers_in_seerr` | radarr-animefilms: POST /settings/radarr -> 201 | PASS |
| `04_configure_four_servers_in_seerr` | Seerr lists 2 Sonarr + 2 Radarr servers | PASS |
| `04_configure_four_servers_in_seerr` | exactly one default non-4K server per type | PASS |
| `04_configure_four_servers_in_seerr` | Seerr returns the server API key unmasked to admins (core route test can compare keys) | PASS |
| `04_configure_four_servers_in_seerr` | sonarr-series: GET /service/sonarr/{id} (request modal data) | PASS |
| `04_configure_four_servers_in_seerr` | sonarr-anime: GET /service/sonarr/{id} (request modal data) | PASS |
| `04_configure_four_servers_in_seerr` | radarr-films: GET /service/radarr/{id} (request modal data) | PASS |
| `04_configure_four_servers_in_seerr` | radarr-animefilms: GET /service/radarr/{id} (request modal data) | PASS |
| `05_request_movie_default_server_radarr_films` | movie request auto-approved (admin) | PASS |
| `05_request_movie_default_server_radarr_films` | movie added to radarr-films | PASS |
| `05_request_movie_default_server_radarr_films` | radarr-films: rootFolderPath | PASS |
| `05_request_movie_default_server_radarr_films` | radarr-films: qualityProfileId = Seerr activeProfileId | PASS |
| `05_request_movie_default_server_radarr_films` | radarr-films: monitored | PASS |
| `05_request_movie_default_server_radarr_films` | radarr-films: minimumAvailability from Seerr server | PASS |
| `05_request_movie_default_server_radarr_films` | radarr-films: Seerr server tag applied | PASS |
| `05_request_movie_default_server_radarr_films` | movie NOT added to radarr-animefilms | PASS |
| `05_request_movie_default_server_radarr_films` | Seerr media.externalServiceId = Radarr movie id | PASS |
| `05_request_movie_default_server_radarr_films` | Seerr media status processing (3) | PASS |
| `06_request_anime_movie_with_server_selection_radarr_animefilms` | anime-film request stored with serverId | PASS |
| `06_request_anime_movie_with_server_selection_radarr_animefilms` | anime film added to radarr-animefilms | PASS |
| `06_request_anime_movie_with_server_selection_radarr_animefilms` | radarr-animefilms: rootFolderPath | PASS |
| `06_request_anime_movie_with_server_selection_radarr_animefilms` | radarr-animefilms: qualityProfileId | PASS |
| `06_request_anime_movie_with_server_selection_radarr_animefilms` | radarr-animefilms: tag | PASS |
| `06_request_anime_movie_with_server_selection_radarr_animefilms` | anime film NOT added to radarr-films | PASS |
| `06_request_anime_movie_with_server_selection_radarr_animefilms` | Seerr media.serviceId = radarr-animefilms server | PASS |
| `07_request_series_specific_seasons_sonarr_series` | series request has seasons 1,2 | PASS |
| `07_request_series_specific_seasons_sonarr_series` | series added to sonarr-series | PASS |
| `07_request_series_specific_seasons_sonarr_series` | sonarr-series: rootFolderPath | PASS |
| `07_request_series_specific_seasons_sonarr_series` | sonarr-series: qualityProfileId | PASS |
| `07_request_series_specific_seasons_sonarr_series` | sonarr-series: seriesType standard | PASS |
| `07_request_series_specific_seasons_sonarr_series` | sonarr-series: seasonFolder | PASS |
| `07_request_series_specific_seasons_sonarr_series` | sonarr-series: series monitored | PASS |
| `07_request_series_specific_seasons_sonarr_series` | sonarr-series: requested seasons 1,2 monitored | PASS |
| `07_request_series_specific_seasons_sonarr_series` | sonarr-series: other seasons (0,3,4,5) unmonitored | PASS |
| `07_request_series_specific_seasons_sonarr_series` | sonarr-series: future-season policy monitorNewItems=none (from Seerr server) | PASS |
| `07_request_series_specific_seasons_sonarr_series` | sonarr-series: tag | PASS |
| `07_request_series_specific_seasons_sonarr_series` | episodes of S1/S2 monitored, S3+ unmonitored | PASS |
| `07_request_series_specific_seasons_sonarr_series` | series NOT added to sonarr-anime | PASS |
| `07_request_series_specific_seasons_sonarr_series` | Seerr media.externalServiceId = Sonarr series id | PASS |
| `08_request_anime_series_with_server_selection_sonarr_anime` | anime added to sonarr-anime | PASS |
| `08_request_anime_series_with_server_selection_sonarr_anime` | sonarr-anime: rootFolderPath | PASS |
| `08_request_anime_series_with_server_selection_sonarr_anime` | sonarr-anime: qualityProfileId (anime profile) | PASS |
| `08_request_anime_series_with_server_selection_sonarr_anime` | sonarr-anime: seriesType anime | PASS |
| `08_request_anime_series_with_server_selection_sonarr_anime` | sonarr-anime: season 1 monitored | PASS |
| `08_request_anime_series_with_server_selection_sonarr_anime` | sonarr-anime: monitorNewItems=all (anime server policy) | PASS |
| `08_request_anime_series_with_server_selection_sonarr_anime` | anime NOT added to sonarr-series | PASS |
| `08_request_anime_series_with_server_selection_sonarr_anime` | Seerr media.serviceId = sonarr-anime server | PASS |
| `09_anime_without_server_selection_lands_on_default_server` | anime w/o serverId -> default server sonarr-series (no auto-routing) | PASS |
| `09_anime_without_server_selection_lands_on_default_server` | ... but Seerr detected anime: seriesType anime | PASS |
| `09_anime_without_server_selection_lands_on_default_server` | ... with the default server's root (no activeAnimeDirectory on sonarr-series) | PASS |
| `09_anime_without_server_selection_lands_on_default_server` | ... and NOT in sonarr-anime | PASS |
| `10_rerequest_existing_titles_no_duplicates` | re-request of requested movie refused by Seerr | PASS |
| `10_rerequest_existing_titles_no_duplicates` | radarr-films still exactly 1 copy | PASS |
| `10_rerequest_existing_titles_no_duplicates` | re-request of already requested seasons refused | PASS |
| `10_rerequest_existing_titles_no_duplicates` | extra season 3 monitored on the SAME Sonarr series | PASS |
| `10_rerequest_existing_titles_no_duplicates` | seasons 1,2 still monitored, 4,5 not | PASS |
| `10_rerequest_existing_titles_no_duplicates` | sonarr-series still exactly 1 copy of the series | PASS |
| `10_rerequest_existing_titles_no_duplicates` | pre-existing movie: no duplicate in radarr-films | PASS |
| `10_rerequest_existing_titles_no_duplicates` | pre-existing movie: Seerr re-monitored it | PASS |
| `10_rerequest_existing_titles_no_duplicates` | pre-existing movie: Seerr linked to the existing Radarr id | PASS |
| `10_rerequest_existing_titles_no_duplicates` | pre-existing movie: no copy in radarr-animefilms | PASS |
| `11_movie_becomes_available_via_radarr_scan` | radarr-films sees the file (hasFile) | PASS |
| `11_movie_becomes_available_via_radarr_scan` | Seerr media status -> 5 (available) after radarr-scan | PASS |
| `11_movie_becomes_available_via_radarr_scan` | Seerr request status -> 5 (completed) | PASS |
| `11_movie_becomes_available_via_radarr_scan` | other movie (no file) stays processing | PASS |
| `12_still_available_after_av1_replacement` | radarr-films now has the AV1 file, same movie id | PASS |
| `12_still_available_after_av1_replacement` | Seerr still available (5) after AV1 replacement + radarr-scan + availability-sync | PASS |
| `12_still_available_after_av1_replacement` | Seerr still linked to the same Radarr id | PASS |
| `13_series_partially_available_via_sonarr_scan` | sonarr-series sees S01E01 file | PASS |
| `13_series_partially_available_via_sonarr_scan` | Seerr tv media status -> 4 (partially available) | PASS |
| `13_series_partially_available_via_sonarr_scan` | Seerr season 1 -> 4 (partial), season 2 not available | PASS |
| `14_core_route_test_all_four_routes_ok` | core: 4 instances registered with Seerr-facing public_url | PASS |
| `14_core_route_test_all_four_routes_ok` | core: Seerr settings saved after live verification | PASS |
| `14_core_route_test_all_four_routes_ok` | core: /seerr/test sees 2+2 servers, keys masked | PASS |
| `14_core_route_test_all_four_routes_ok` | core route-test: sonarr-series OK | PASS |
| `14_core_route_test_all_four_routes_ok` | core route-test: sonarr-series checks url/key/default/profile/root/seerr-test all true | PASS |
| `14_core_route_test_all_four_routes_ok` | core route-test: sonarr-anime OK | PASS |
| `14_core_route_test_all_four_routes_ok` | core route-test: sonarr-anime checks url/key/default/profile/root/seerr-test all true | PASS |
| `14_core_route_test_all_four_routes_ok` | core route-test: radarr-films OK | PASS |
| `14_core_route_test_all_four_routes_ok` | core route-test: radarr-films checks url/key/default/profile/root/seerr-test all true | PASS |
| `14_core_route_test_all_four_routes_ok` | core route-test: radarr-animefilms OK | PASS |
| `14_core_route_test_all_four_routes_ok` | core route-test: radarr-animefilms checks url/key/default/profile/root/seerr-test all true | PASS |
| `14_core_route_test_all_four_routes_ok` | core route-test: anime routes also verify activeAnime* on sonarr-anime | PASS |
| `14_core_route_test_all_four_routes_ok` | core route-test: overall ok, no unmapped servers | PASS |
| `14_core_route_test_all_four_routes_ok` | core /seerr/mapping (read-only) agrees | PASS |
| `15_core_request_sync` | core: all Seerr requests synced | PASS |
| `15_core_request_sync` | core: movie request status/media status synced (5/5) | PASS |
| `15_core_request_sync` | core: before library sync requests are 'unlinked' (no lib_items) | PASS |
| `15_core_request_sync` | core: after library sync every request is linked | PASS |
| `15_core_request_sync` | core: each request linked to the instance that really holds the title | PASS |
| `15_core_request_sync` | core: Seerr serverId for non-default-server requests is recorded | PASS |
| `16_core_detects_misconfigured_seerr_servers` | misconfig A: radarr-animefilms not OK (no Seerr server points to it) | PASS |
| `16_core_detects_misconfigured_seerr_servers` | misconfig A: the stray server is reported as unmapped / overall not OK | PASS |
| `16_core_detects_misconfigured_seerr_servers` | misconfig A: radarr-films itself still OK | PASS |
| `16_core_detects_misconfigured_seerr_servers` | misconfig B: wrong API key detected (api_key_matches=false) | PASS |
| `16_core_detects_misconfigured_seerr_servers` | misconfig C: sonarr-anime isDefault=true detected | PASS |
| `16_core_detects_misconfigured_seerr_servers` | misconfig C: sonarr-series lost default detected | PASS |
| `16_core_detects_misconfigured_seerr_servers` | misconfig D: non-existent root folder detected | PASS |
| `16_core_detects_misconfigured_seerr_servers` | misconfig E: wrong port -> sonarr-anime not OK | PASS |
| `16_core_detects_misconfigured_seerr_servers` | after restoring Seerr settings: route-test OK again | PASS |
| `17_core_cutover_and_rollback_against_real_seerr` | cutover preview requires confirmation and shows the diff | PASS |
| `17_core_cutover_and_rollback_against_real_seerr` | cutover preview did not change Seerr | PASS |
| `17_core_cutover_and_rollback_against_real_seerr` | cutover confirm accepted by real Seerr (PUT /settings/radarr/{id}) | **FAIL** |

With the B1 fix, test 17 continues and also passes: cutover confirm accepted by real Seerr;
after the cutover Seerr points at radarr-animefilms with its key; the cutover kept
profile/root/default flags; the route test is OK after cutover; rollback is accepted by real
Seerr; rollback restored the exact pre-cutover values.
