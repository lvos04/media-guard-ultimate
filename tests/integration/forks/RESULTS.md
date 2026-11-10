# MediaGuard fork harness: results

Run on 2026-11-02 in a Linux sandbox (4 cores, Docker 29.9.2, Compose v5.6.1). Real metadata came from
skyhook.sonarr.tv and api.radarr.video. No real user Sonarr/Radarr installation was involved: every
service was a local test container.

## Images under test

| image | ID | upstream | version |
|---|---|---|---|
| `mediaguard/sonarr-fork:bc54afb-mg1` (= `:4.0.20.3014-mg1`) | `a6a7e68ef481` | Sonarr `main` @ `bc54afbdd0e3` | 4.0.20.3014, .NET 6.0.13, API v3 |
| `mediaguard/radarr-fork:a96bf7c-mg1` (= `:6.4.4.10685-mg1`) | `e8e1d0752545` | Radarr `master` @ `a96bf7c3ecb2` | 6.4.4.10685, .NET 8.0.27, API v3 |

Build commands: `BUILD_CA_BUNDLE=/root/.ccr/ca-bundle.crt forks/sonarr/build.sh` and the same for
radarr. Each is a full multi-stage build from source: the frontend with yarn/webpack, the backend with
the upstream `build.sh`. A cold Radarr backend build takes about 4 min. The Sonarr frontend takes
about 2 min, and the Sonarr backend about 4 min.
The full frontend build works in this sandbox, so no fallback was needed.

## 1. Unit tests (NUnit, in the upstream test projects)

| fork | command | result |
|---|---|---|
| Sonarr | `dotnet test Sonarr.Core.Test.csproj --filter FullyQualifiedName~MediaGuard` (SDK 6.0.405 container) | **25/25 passed** |
| Radarr | `dotnet test Radarr.Core.Test.csproj --filter FullyQualifiedName~MediaGuard` (SDK 8.0.421 container) | **25/25 passed** |
| Sonarr | `forks/sonarr/build.sh --target unittest --build-arg TEST_FILTER="FullyQualifiedName~MediaGuard\|FullyQualifiedName~DecisionEngine\|FullyQualifiedName~EpisodeImport"` (regression run over all decision and import engine tests, inside the image build) | **594 total: 591 passed, 3 skipped (Windows-only), 0 failed** |
| Radarr | same, with `~MovieImport` | **437 total: 433 passed, 4 skipped, 0 failed** |

The `unittest` Dockerfile stage was hardened afterwards (`SHELL bash -o pipefail`) so that a failing
test also fails the build. The counts above come from the run just before that change and were
checked by hand: `Failed` was 0. The hardened stage was not rebuilt, to save disk.

## 2. Integration harness (`tests/integration/forks`)

Command: `MG_TEST_CA_BUNDLE=/root/.ccr/ca-bundle.crt tests/integration/forks/run.sh`.
The CA bundle is needed only because this sandbox runs a TLS-inspecting egress proxy. It is mounted
read-only into the test containers and is not part of the images.

Stack: 4 instances from 2 images, a fake Newznab indexer (one shared feed, the per-instance API key
records who polled), a fake SABnzbd (one client with categories `tv`, `tv-anime`, `movies` and
`movies-anime`, writing a real 20-minute ffmpeg MKV), and the stub MediaGuard hook server
(per-instance keys).

**Final run: 12 passed, 0 failed, in 128.8 s (2:25 including compose up and down).** An identical
earlier run also passed 12/12 (137 s).

| # | test | proves | result | time |
|---|---|---|---|---|
| 1 | `test_r6_1_four_independent_instances_two_images` | R6.1: 4 processes (distinct PIDs), exactly 2 image IDs, 4 distinct `/config` volumes, each with its own `sonarr.db`/`radarr.db` + `config.xml`; a tag created in one instance is invisible in the others | PASS | 1.2 s |
| 2 | `test_seerr_api_compat_sonarr` | Seerr-relevant v3 calls: `system/status`, `qualityprofile`, `rootfolder` (POST/GET), `languageprofile` (200, v4 "Deprecated" profile), `tag`, `series/lookup?term=tvdb:`, `POST /series`, `GET /series?tvdbId=`, `PUT /series` (season monitoring), `/episode`, `PUT /episode/monitor`, `downloadclient`, `indexer`, `notification` | PASS | 6.2 s |
| 3 | `test_seerr_api_compat_radarr` | `movie/lookup/tmdb`, `POST /movie`, `GET /movie?tmdbId=`, `PUT /movie`, profiles and root folders | PASS | 4.3 s |
| 4 | `test_rss_new_episode_grabbed_and_imported_by_correct_instance_only` | R6.4: a new *Breaking Bad* S01E03 release in the shared RSS feed. All 4 instances polled it; only `sonarr-series` grabbed it (7.4 s), the fake SAB downloaded it, and it was imported into `media/sonarr-series/Breaking Bad/Season 1/` (12.6 s). The pre-grab payload (`search_type: rss`) and pre-import payload (with `media_info`) follow the contract | PASS | 12.6 s |
| 5 | `test_rss_new_anime_episode_grabbed_by_anime_instance_only` | R6.4 anime: `[MGTest] Cowboy Bebop - 05` (absolute numbering, `series_type: anime`) was grabbed and imported **only** by `sonarr-anime` | PASS | 11.5 s |
| 6 | `test_rss_new_movies_grabbed_by_correct_radarr_instance` | *Night of the Living Dead* went to `radarr-films` and *Spirited Away* to `radarr-animefilms`, through one feed. Each was imported into its own root folder; the 4 download categories were used | PASS | 13.4 s |
| 7 | `test_pregrab_reject_visible_in_interactive_search_and_blocks_rss` | `GET /api/v3/release` shows `MediaGuard: Geen Nederlandse of Engelse audio [AUDIO_PREFERENCE]` (`search_type: interactive`), and RSS does not grab. After an accept rule: `approved: true`. Radarr shows `MediaGuard: Verkeerde film [IDENTITY_MISMATCH]` | PASS | 8.5 s |
| 8 | `test_pregrab_timeout_fail_open_and_fail_closed` | Stub delay of 4 s against a 1.5 s timeout. `sonarr-series` (open) approved the release, and the whole search took 3.3 s. `sonarr-anime` (closed) rejected it with `MediaGuard: MediaGuard niet bereikbaar (WebException: Http request timed out); fail policy closed [MEDIAGUARD_UNAVAILABLE]` (3.7 s). HTTP 500 produced the same split (radarr-animefilms closed vs radarr-films open). A warning appears in the fork log | PASS | 7.3 s |
| 9 | `test_preimport_reject_prevents_import_then_accept_imports` | Pre-import reject: the queue item shows warning `MediaGuard: Aflevering-identiteit twijfelachtig [IDENTITY_MISMATCH]`, the episode has no file, and nothing lands in the library. Then defer, then accept, and the next cycle imports. Decision sequence: `reject, reject, defer, accept` | PASS | 18.3 s |
| 10 | `test_shadow_mode_blocks_all_grabs_and_restart_is_isolated` | `radarr-animefilms` was re-created with `MEDIAGUARD_SHADOW_MODE=1`. The other 3 containers kept the same `StartedAt`/`startTime`, and the DB persisted in the volume. Every release was rejected with `[MEDIAGUARD_SHADOW_MODE]` with **0 hook calls**, and RSS did not grab. Meanwhile `radarr-films` grabbed normally | PASS | 19.1 s |
| 11 | `test_suspended_instance_does_not_affect_others` | With `sonarr-anime` stopped, `sonarr-series` still grabbed and imported S01E07, and the anime release was not downloaded by anyone. After start, `sonarr-anime` state was intact (series and imported file) | PASS | 16.5 s |
| 12 | `test_events_via_stock_webhook_and_hook_auth` | The stock Webhook connection (Basic auth `instance_id:key`) delivered `Grab`, `Download` and `Test` to `/api/v1/hooks/arr-webhook/{instance_id}`. Every pre-grab/pre-import call carried the correct per-instance `X-MediaGuard-Hook-Key` | PASS | 0.5 s |

Artifacts per run (git-ignored): `.run/pytest.log`, `.run/junit.xml`, and `.run/logs-<service>.txt` for
all 7 services.

## 3. Upstream sync script

| command | result |
|---|---|
| `forks/sonarr/sync-upstream.sh` (main tip = pinned) | 3/3 patches applied, content-identical, verdict `CANDIDATE OK` |
| `forks/sonarr/sync-upstream.sh --branch develop` (v5) | flags the major-version change, SDK 6.0.405 → 10.0.403 and `Sonarr.Api.V5`; `0002` conflicts in `DownloadRejectionReason.cs`; exit 1 |

## Failures found and fixed while building the harness

Each of these was found by the tests, fixed, and then the full suite was re-run.
- Runtime image: Sonarr needs the system `libsqlite3` (`libsqlite3-0` added). The bundled `ffprobe`
  was not executable for the non-root user (permissions normalised).
- Radarr packaging always copies `_output/UI`, but `Build()` wipes `_output` first. The Dockerfile now
  creates the placeholder between the backend and packaging steps.
- The Radarr build fails on NuGet audit (an ImageSharp 3.1.12 advisory published after the pin). The
  build uses `NuGetAudit=false`; see `forks/radarr/UPSTREAM.md`.
- Harness issues, not fork issues: Sonarr's indexer test refuses an empty feed (the fake now carries
  3 unrelated filler releases). Sonarr v4 `monitor: none` also unmonitors the series. Cowboy Bebop's
  TVDB absolute numbering differs from its episode numbers. Big Buck Bunny (10 min) needs smaller
  release sizes. Sonarr requires the instance name to start with "Sonarr".

## Not covered (honest limits)

- **Seerr itself was not run.** The tests exercise the endpoints Seerr uses, but a real Seerr contract
  run (plan part H) is still needed.
- **Torznab and torrents**: the fake indexer serves `/torznab/api` and `.torrent` files, but no fake
  torrent client exists, so the harness tests only the usenet flow.
- **The circuit breaker** (`MEDIAGUARD_HOOK_CIRCUIT_BREAK_SECONDS`, default 30) is unit-tested only.
  The harness sets it to 0 for deterministic timing.
- **R6.2/3/5/6/8–10** (same title in two Sonarr instances, migration, backfill queues, Seerr routing,
  UI E2E) belong to core and the migration wizard, not to the forks.
