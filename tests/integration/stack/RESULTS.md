# MediaGuard full stack + fault injection: results

Run on 2026-11-02 in a Linux sandbox (4 cores, 15.7 GiB RAM, about 18 GB free disk, Docker 29.9,
Compose v5.6.1). Only test containers and generated test media were used. No real
Sonarr/Radarr installation and no real media were touched.

## What runs

`tests/integration/stack/run.sh` starts the **real production compose**
(`deploy/truenas/compose.yaml`) plus two overlays:

- `deploy/dev/compose.dev.yaml` adds a fake Newznab indexer and a fake SABnzbd, publishes ports
  on 127.0.0.1 only, and removes `/dev/dri`.
- `tests/integration/stack/compose.old.yaml` adds the "old production" Sonarr and Radarr, plus a
  recording proxy in front of each.

| service | image | notes |
|---|---|---|
| mediaguard-core | `mediaguard/core:dev` (rebuilt from `apps/core`) | the real hook server, with no stub |
| mediaguard-init | core image, `python -m mediaguard.bootstrap`, root with CHOWN/DAC_OVERRIDE/FOWNER, no network | creates missing dirs + all secrets, then exits 0 (ADR-011) |
| mediaguard-recovery | same image, `MG_ROLE=recovery` | shares the core DB (volume subpath `core`); `/data/media` rw; writes gated by the software lock `library.write_enabled` |
| mediaguard-encode | `mediaguard/encode:dev` | `/data/media` ro, `/work` = `${MG_WORK}/encode` |
| mediaguard-web | `mediaguard/web:dev` | included automatically when the image exists, with no media mount |
| 4 forks | `mediaguard/sonarr-fork:bc54afb-mg1` ×2, `mediaguard/radarr-fork:a96bf7c-mg1` ×2 | each has its own `/config`; one `/data` mount (TRaSH layout) |
| fakeindexer, fakesab | `python:3.12-slim` + `tests/integration/fakes` | the indexer has a new `search_only` release flag for backfill |
| old-sonarr, old-radarr (+ spies) | plain fork images without `MEDIAGUARD_URL` | legacy paths `/tv`, `/movies`, `/downloads` |

All MediaGuard containers run as 568:568 with `cap_drop: ALL` and `no-new-privileges`. None is
privileged and none has the Docker socket. Every one has a healthcheck. Test `a2` asserts all of
this.

Command: `MG_TEST_CA_BUNDLE=/root/.ccr/ca-bundle.crt tests/integration/stack/run.sh`. The CA bundle
is needed only because this sandbox runs a TLS-inspecting proxy. It lets the forks reach
skyhook.sonarr.tv and api.radarr.video.

## Zero-config compose (ADR-011), re-run 2026-11-02 afternoon

The compose now has a `mediaguard-init` service, three named volumes (local driver, bind device =
the three paths) with per-service volume `subpath` mounts, and no keys/tokens/modes in the env.
`run.sh` pre-seeds the secret files with the known test keys (init leaves existing files alone);
`stack.env` no longer contains `MG_LIBRARY_MODE`/`MG_FORK_MEDIA_MODE`/`*_APIKEY`/`*_HOOKKEY`/
`MG_INTERNAL_TOKEN`. Images rebuilt: `mediaguard/core:dev` (with `--build-context release=deploy`),
`mediaguard/encode:dev` (`--target runtime`, cached ffmpeg 8.1.3 tarball); web unchanged.

| suite | result |
|---|---|
| `run.sh` (stack + migration) | **21 passed, 1 xfailed, 0 failed** (365 s pytest, 388 s total) |
| `run_zero_config.sh` (NEW, nothing pre-seeded) | **7 passed, 0 failed** (75 s) |
| `tests/fault-injection/run.sh` | **8 passed, 0 failed** (353 s; +1 new lock test) |

New/changed assertions:

- **a1**: fresh install reports `library_locked: true` with the Dutch reason; `POST
  /recovery/library-write` without `confirm` → 422, with `{confirm:true}` → unlocked.
- **a2**: mounts are compared as `<volume>/<subpath>` (inspect's `Source` omits the subpath): forks
  `mg_appdata/<id>` + read-only `secrets/<id>`, core read-only `secrets`, recovery and encoder only
  `secrets/internal`, recovery and core share `mg_appdata/core`; no `*_APIKEY`/`*_HOOKKEY`/
  `MG_INTERNAL_TOKEN`/mode variable in any MediaGuard container env; `mediaguard-init` exited 0.
- **i**: with the library re-locked, the verified AV1 job stays `awaiting_activation` (no auto
  commit), `activate` → **409 `RECOVERY_NOT_READY` "Bibliotheek nog vergrendeld: doe eerst de
  overstap of kies 'Bibliotheek vrijgeven'"**, no recovery transaction exists, library file sha256
  unchanged; after unlocking, the same job activates → committed → release check passed.
- **m2**: library re-locked before cutover; a successful cutover unlocks it automatically
  (readiness `library_locked: false`, audit `library.write_enabled` with reason "…overstap").
- **fault injection**: setup asserts the default lock and unlocks via the API;
  `test_locked_library_aborts_before_touching_anything` → transaction `aborted` at the lock step
  (`LIBRARY_LOCKED`, "vergrendeld"), original sha256 identical, no quarantine copy/leftovers/lock.

`run_zero_config.sh` (project `mgzero`, `.run-zero/`): env holds ONLY the three paths + images +
dev ports; appdata and work are empty root-owned dirs; `data/media/tv` pre-exists owned by uid 1234
with a file in it. Tests: z1 init generated token (64 hex) + 4×API key (32 hex) + 4×hook key, all
unique, 568:568 0640, forks accept their key; z2 the existing tv dir/file keep uid 1234 and modes,
missing media/download dirs created as 568; z3 setup, lock→unlock, 4 instances `api_key_valid`
without `MG_HOST`, `seerr/apply {public_host}` fills `http://127.0.0.1:<port>` URLs; z4 all 4
instances `healthy` after configuration via core, webhook Test events reach core; z5 an
interactive search on **each** fork produces a core pre-grab decision (= hook key from the file
authenticated) and no 401/“Ongeldige hook-sleutel” in fork logs; z6 recovery heartbeat + readiness
ready, `/admin/health` encoder ok, encoder rejects calls without the token and accepts the file
token; z7 `up -d` again, then `down` + `up` (new init container): log "alles was al ingericht",
all secret files byte-identical, tv ownership unchanged, logins/keys/pre-grab still work.

### Bugs found by this round (fixed)

1. **Forks did not start with the zero-config compose** ("AppFolder /config is not writable"):
   Docker copies the image's content *and ownership* of the mount target into an empty volume
   (sub)path. The Sonarr/Radarr images have `/config` owned by uid 1000, so the init-created
   `appdata/<instance>` became 1000:1000. Fix: `volume: {nocopy: true}` on every volume mount in
   `deploy/truenas/compose.yaml` (29 mounts) and the dev overlay; regression test
   `test_compose_volume_mounts_never_copy_image_content`.
2. `provisioning/engine.py`: the root-folder host-action message still told users to set
   `MG_FORK_MEDIA_MODE=rw` (removed variable). Now points to the uid 568 dataset permission;
   regression test `test_no_runtime_hint_refers_to_removed_compose_switches`;
   `docs/AUTOMATISCHE_INRICHTING.md` updated.
3. `docs/AV1_TRUENAS_PORT.md`: the documented `docker build … services/encoder` builds the last
   stage = the **test** image (runs pytest instead of the API; test_i failed with connection
   resets). Doc now says `--target runtime` (release.yml already used it).

4. `apps/core/tests/conftest.py`: `test_pre_grab_audio_preference_score_and_speed` failed in the
   full core run (1.02 s > 1 s) only after the new `test_coverage_matrix`: its unclosed app
   DB connection was closed by the GC mid-request (WAL checkpoint + fsync). The `app` fixture
   now closes the DB at teardown; full suite 402 passed.

Run history of this round: run 1 — all errors at setup (bug 1); run 2 — 20 passed, i failed
(encoder image was the test stage, bug 3); run 3 — green. Zero-config run 1: Radarr ignored a
release title without year (test fix); run 2 — green.

## Previous final run (before ADR-011): 21 passed, 1 xfailed (known gap), 0 failed, 334.5 s (pytest) / 353 s total

| test | proves | result | time |
|---|---|---|---|
| a1 first-run setup, CSRF | `setup-status`, then `setup` (409 the second time). A mutating call without `X-CSRF-Token` returns 403 and with it is accepted. Anonymous access returns 401 | PASS | 0.5 s |
| a2 seeding + container contract | 4 instances seeded from `MG_INSTANCE_<ID>_URL/_APIKEY/_HOOKKEY`, `api_key_valid`. 2 images, 4 `/config`. Core and encoder see only `/data/media:ro`, recovery `/data/media:rw`, forks `/data`, web nothing. User 568, unprivileged, healthchecks present. Recovery shares core's `/config` | PASS | 1.1 s |
| a3 configuration only via the core API | Root folder, download client (secrets masked as `********`, test OK), indexer (test OK), default profile and root per instance. All 4 instances turn `healthy` | PASS | 14.1 s |
| a4 webhooks via core | `POST /instances/{id}/webhook` on all 4 (13/12 events). The fork's Test event reaches core: an `arr.webhook` event plus `webhook_last_test_at` | PASS | 1.6 s |
| b1 R6.4/L2 new episode, real hooks | Series added through core with `monitorNewItems=all`. RSS release, then the real core **pre-grab** decided `accept` (2.6 ms), grab after 6.0 s, fake SAB, real **pre-import** `accept` (identity `verified`), import after 11.4 s. Webhook `Grab` + `Download` land in `import_events` + `release_provenance` (episode ids correct). The `analysis.post_import` job `succeeded` with a real encoder probe. The file appears only under `media/tv` | PASS | 18.6 s |
| b2 anime | Grabbed only by `sonarr-anime`. Profile `anime-dual-first`, audio tier `dual`, score adjustment 2000. The sample has only English audio, so the "Dual Audio" claim is recorded as false (`audio_claim_failures`) | PASS | 19.1 s |
| b3 films | Routed to the right Radarr through one feed. The 4 download categories are used | PASS | 12.7 s |
| c1 backfill | E01 exists only in SEARCH results: RSS misses it, `POST /instances/sonarr-series/backfill` issues one EpisodeSearch, then grab, then import. E02 has no candidate: `no_candidate`, then `unavailable` with a retry in 30 days, attempts recorded. Only `sonarr-series` queried the indexer (5 tvsearch queries) | PASS | 16.6 s |
| c2 no endless retries + quota | A second run issues 0 searches (`unavailable_wait`). Quota of hour_used+1: exactly 1 search, `quota_or_batch: 2` deferred, then remaining 0, then 0 issued. The anime backfill uses its own quota (hour_used 1 vs series) | PASS | 17.5 s |
| d1 policy semantics (core) | Japanese-only is accepted as a fallback with adjustment 0 and "blijft zoeken". Dual Audio is accepted with +2000 | PASS | 4.5 s |
| d1b L2 "1080p Dual > 2160p JA-only" with default qualities | **XFAIL (known gap)**: the real fork grabbed `[…] (BD 2160p) [Japanese Audio]`. Sonarr ranks *quality before CF score*, so core's score_adjustment cannot win | XFAIL | – |
| d1c the same with 1080p and 2160p in one quality group | Dual Audio 1080p is grabbed (the CF score breaks the tie) | PASS | 2.4 s |
| d2 emergency stop | Interactive search: `MediaGuard: Noodstop actief … [EMERGENCY_STOP]`. RSS does not grab. Releasing the stop leads to a grab | PASS | 8.1 s |
| d3 suspend/resume | Suspending `sonarr-anime` turns RSS and automatic search off on its indexer only. The other 3 are unchanged. Interactive search returns `[INSTANCE_SUSPENDED]`. `sonarr-series` grabs at the same time. Resume restores the exact previous state, and the anime grab then follows | PASS | 17.8 s |
| e R6.2 same series in two Sonarr instances | Core returns 409 `OWNERSHIP_CONFLICT`, then `force_duplicate` with a different profile (HD-1080p). Season monitoring through core now monitors the episodes. The release is grabbed and imported only by `sonarr-series`. Nothing changes in `sonarr-anime` (profile, type, monitored, path, every episode flag and hasFile). `lib_items` is keyed by `(instance_id, provider_id)` | PASS | 17.3 s |
| f wanted diagnosis | E02 gives `no-indexer-hit` (with rule and suggested fix). E04 unmonitored gives `unmonitored` and drops out of "open". `kids-nl-audio` with enforcement: the real **pre-import** rejects `AUDIO_REQUIREMENT`, which gives `import-failed` with the MediaGuard message. Open wanted items are only monitored items without a file | PASS | 10.7 s |
| g restart + backup/restore | `docker restart` core (4.8 s): the session, settings overrides, jobs and instances (incl. hook key) persist, and a new pre-grab call is accepted. `POST /backups` (integrity ok), a change, then restore returns the old value plus a pre-restore safety backup. Recovery restart, then readiness `ready` | PASS | 6.3 s |
| i AV1 chain (P9) | 20 s 720p H.264 fixture. Core `POST /encoding/jobs` → encoder (CPU/SVT-AV1) → `awaiting_commit` → core verification (codec, resolution, …) → `activate` → recovery transaction `committed` (method link, quarantine under `/data/media/.mediaguard-quarantine`) → encoder informed of `committed`. The library file is now `av1` with sha equal to the staged sha. The quarantine item is `restorable` and its **release check `passed`** | PASS | 93.8 s |
| m1 populate the old production | Old Sonarr gets a series with S1 monitored and an S01E02 exception, `monitorNewItems=all`, plus an anime series with `none`, a tag, an indexer with API key, a download client and a remote path mapping. Old Radarr gets 2 films | PASS | 34.8 s |
| m2 live source: native restore, verify, cutover, rollback | Preflight → backup (download of the existing native zip) → mapping with remap `/tv→/data/media/tv`, `/downloads→/data/downloads` → path preview GO → native shadow restore into `sonarr-series` (10 s, 6/6 files linked, RssSync interval 0, indexers/DC off, `shadow_mode`) → data identical (types, monitorNewItems, episode exceptions, tags, paths) → verify **GO** → **63 calls to the old instance, all GET** → readiness GO → cutover: old indexer RSS/auto off (the only non-GET: `PUT /api/v3/indexer/1`), target live, RSS release grabbed **only by the new instance** through the real pre-grab hook → rollback: target back in shadow, old exactly `(True, True, True)` | PASS | 24.8 s |
| m3 uploaded backup source | Zip from old Radarr, then `POST /migration/sources/upload` → native restore into `radarr-films` (Collections root remapped) → verify GO → activate-search without `confirm_old_instance_offline` returns 409, with it the activation completes → RSS grab on the target | PASS | 9.5 s |

Artifacts (git-ignored): `.run/pytest-final.log`, `.run/junit.xml` and `.run/logs-<service>.txt`
for every service. Resume a failed run against a kept stack with
`KEEP_RUNNING=1 run.sh`, then `STACK_RESUME=1 pytest test_stack.py -k …`.

### Run history (honest)

| run | result | what was learned / fixed |
|---|---|---|
| 1 | a2 FAIL | Unconfigured forks are `degraded`, which is correct. The health assertion moved to after configuration |
| 2 | c1, d1 FAIL | Test: candidate collection also appears as an attempt. **d1: real L2 gap** (Sonarr ranks quality before CF score) |
| 3 | e, f, m2, m3 FAIL | **Bug: seasonpass did not monitor episodes.** Readiness NO-GO after native restore (**stale webhook id, shadow health**). Radarr **Collections root path** |
| 4–5 | e, i, m2 FAIL | **Anime routing deleted a deliberate duplicate.** **Encoder probe wrapper** and **encoder policy 422** (contract). **Stale series-scoped override after restore.** The test's monitor race |
| 6–7 | i FAIL | The encoder correctly skips a 1 kbps sample. Its CPU lane is paused on cache space below 150 GB (invisible in capabilities). Recovery aborts INSUFFICIENT_SPACE with a 20 GB reserve (correct) |
| 8 | a4, i FAIL | **Webhook registration race (provisioning + UI): duplicate connection.** **Encoder EXDEV** with separate cache/staging mounts (compose fixed) |
| **9** | **21 passed, 1 xfailed** | – |

## Fault injection (`tests/fault-injection/run.sh`): 7 passed, 0 failed (before ADR-011; now 8 passed, see above)

The real core image runs in both roles (core and recovery) against a **scratch library**:
`.run/media/tv` holds generated H.264 files, `/media/small` is a 64 MiB tmpfs volume, and
`/work/quarantine` is a 1200 MiB tmpfs volume (another device, so it takes the copy path). The driver
(`driver.py`) runs inside the real recovery container as a separate recovery process and uses B2's
`fault=` hook. Container restarts are real `docker restart`/`docker kill -s KILL`.

| test | result |
|---|---|
| `sigkill_at_every_step…[link]`: `kill -9` at lock, validate, quarantine:after_action, quarantine, stage_temp, activate:after_action, activate, record and refresh, then one container restart (journal replay) | PASS. lock/validate → `aborted`; quarantine…stage_temp → `rolled_back` (original sha256 identical, quarantine link removed); activate:after_action…refresh → `committed` (new file, original restorable from quarantine, sha256 identical). No `.mg-*` leftovers and the lock is released |
| the same `[copy]` (central quarantine on tmpfs, cross-device) | PASS, same outcomes, method `copy` |
| committed original restorable through the API after a crash | PASS. `POST /api/v1/quarantine/{id}/restore` → restore transaction `committed`; the file is H.264 again with the original sha |
| kill -9 of the **running worker** (whole container) mid-copy, by timing, 400 MB | PASS. During `quarantine`: killed 4.9 s after job start, replay gives `rolled_back` ("Onderbroken vóór activatie; origineel ongewijzigd"). During `stage_temp`: 5.9 s, gives `rolled_back`. The requeued job does not run the transaction again |
| disk full | PASS. The disk fills after the pre-check, giving `rolled_back` "Schijf vol tijdens kopiëren: … [Errno 28]" with the original intact. Too little space up front gives `aborted` (INSUFFICIENT_SPACE) |
| permission denied | PASS. Read-only library dir gives `rolled_back` (PermissionError on `.mg-replace-…part`). Read-only quarantine dir gives `rolled_back`. The original is intact both times |

## Bugs found and fixed (each has a regression test in `apps/core/tests/test_stack_regressions.py`, unless noted)

1. `instances/registry.py`: core did not know the forks' hook keys at first start, so every
   pre-grab returned 401 and the fail policy applied. Added `MG_INSTANCE_<ID>_HOOKKEY` seeding; the
   env key wins on every start.
   `instances/service.py`: rotating an env-pinned key is now refused, because it would break the fork hooks.
2. `hooks/service.py` + `instances/api.py`: a webhook `Test` event was silently dropped. It is now
   an `arr.webhook` event plus `webhook_last_test_at`.
3. `arr/client.py` `season_pass`: monitoring a season through MediaGuard used `/seasonpass`,
   which in Sonarr 4 does **not** monitor episodes, so nothing was ever downloaded. It now uses
   `PUT /series` like the Sonarr UI. The fake in `tests/test_arr_fakes.py` was corrected to match
   real behaviour.
4. `migration/wizard.py` `target_path_exists` and `provisioning/engine.py` `path_visible`: with a
   trailing slash, Sonarr returns `parent` even for a **non-existent** path, so missing root
   folders were seen as present.
5. `migration/backupfile.py`: Radarr `Collections.RootFolderPath` was not remapped, which gave a
   health error and readiness NO-GO.
6. `migration/wizard.py` readiness: it tested a stale webhook id after a native restore (404), and
   the expected health messages in shadow mode counted as NO-GO. Regression test in
   `tests/test_migration.py`.
7. `migration/wizard.py` native restore: MediaGuard overrides at series, season, episode, movie
   and file scope of the target pointed at a **different** title after the restore. They are now
   dropped and audited. Regression test in `tests/test_migration.py`.
8. `encoding/client.py`: the real encoder wraps `/probe` as `{probe:{…}, inventory, …}`. Core
   saw no codec and no audio, and post-import reported "Geen audiospoor".
9. `encoding/client.py`: core sent `profile_id`, `dv_route`, `audio_policy.mode` and
   `hdr10plus: allow`, so the encoder (`extra=forbid`) returned 422 on every job. Core now sends only
   the contract fields. `tests/test_encoding.py` assertion adjusted.
10. `instances/manage.py`: the anime routing (other agent) deleted a series deliberately added with
    `force_duplicate` one second after adding it. `force_duplicate` now records a routing exclusion.
11. `instances/service.py` `register_webhook`: a race between provisioning and the UI created two
    "MediaGuard" connections, after which every test or update failed with "Name should be unique". It is
    now serialized per instance and duplicates are cleaned up.
12. `recovery/service.py` readiness: core judged the writability of the quarantine
    directory, but core mounts it read-only, so central quarantine could never activate. The
    recovery heartbeat now reports it.
13. `provisioning/templates.py`/`store.py`: the default root folders followed the old paths. They are
    now `/data/media/{tv,anime,movies,animefilms}`, and a test compares them with `compose.yaml`.
    `provisioning/engine.py`: a root folder that is not writable gives a "Hostactie nodig:
    MG_FORK_MEDIA_MODE=rw …" message. This message has no separate unit test.

## Open findings for other components (not fixed by me)

- **Forks/policy (L2 blocker for the default profile):** Sonarr orders by quality before CF score,
  so "1080p Dual Audio over 2160p Japanese-only" only works when those qualities share one group
  in the anime profile (d1c). Either provisioning puts 1080p and 2160p in one group in the anime
  profile, or the fork patch gets a MediaGuard tie-break before quality.
- **Encoder:** (a) an automatic CPU-lane pause on free cache space (default floor 150 GB) is not
  visible in `/api/mediaguard/v1/capabilities` (`lanes.cpu.enabled=true`) or in `/health`, and
  jobs silently stay `queued`. (b) Moving from `/work/cache` to `/work/staging` has no EXDEV
  fallback, so separate mounts lead to `failed`. Compose now uses a single `/work` mount. (c) The
  encoder's storage guard runs only once an hour, so a changed threshold takes effect late.
- **Anime routing (`mediaguard/routing`):** it should honour `instance_ownership` /
  `force_duplicate` itself. My fix covers only additions made through core.
- **Sonarr race (UI flow):** `addOptions.monitor=none` is applied asynchronously after the first
  scan and can overwrite an early `PUT /series`. The tests wait for this. The web UI should not
  do "add + monitor" as two quick separate steps.
- Not covered: real Seerr, Prowlarr, torrents, GPU (QSV) encodes, a real TrueNAS host or ZFS
  snapshots, and the web UI in a browser (the web container only starts).
