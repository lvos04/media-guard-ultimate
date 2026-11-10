# Testresultaten

## Overzicht van alle suites (stand 2026-11-02, afrondingsronde)

Alles in één keer draaien: `scripts/test-all.sh` (samenvattingstabel aan het eind; `--fast` =
core + web lint/typecheck/build; `--only`/`--skip` per suite). In CI: `.github/workflows/ci.yml`.
Details en run-historie staan in de `RESULTS.md` per suite.

| Suite | Commando | Laatste resultaat | Bron |
|---|---|---|---|
| Core unit/contract (ruff + pytest) | `cd apps/core && ruff check . && pytest -q` | **400 passed, 2 skipped** (173 s); ruff groen na 4 lintfixes (`instances/service.py`, `recovery/service.py`, `tests/conftest.py`, `tests/test_provisioning_fakes.py`). Eén timinggevoelige test (`test_hooks.py::test_pre_grab_audio_preference_score_and_speed`) faalde eenmalig onder zware parallelle load en slaagt los en in de herhaalrun | deze ronde |
| Encoder volledige suite (toolchain-image, uid 568) | `docker build --target test … services/encoder && docker run --rm mediaguard/encode:test` | **251 passed, 2 skipped** (562 s; skips = geen Intel-QSV-rendernode). Was 241/2 + 10 nieuwe tests; testimage kopieert nu ook `host/` (anders 9 errors in `test_smb_helper.py`) | deze ronde; `docs/AV1_BASELINE_AUDIT.md` |
| Encoder ruff / mypy strict | in het testimage | **All checks passed / Success (47 bestanden)** | deze ronde |
| Encoder unit-subset zonder toolchain (CI-job `encoder-unit`) | host-venv, 13 bestanden | **123 passed** | deze ronde |
| Web lint / typecheck / build | `cd apps/web && npm run lint && npm run typecheck && npm run build` | **lint 0 meldingen, typecheck 0 fouten, build geslaagd** (47 s) | deze ronde (`scripts/test-all.sh --fast`) |
| Web E2E (Playwright, echte core) | `cd apps/web && npm run build && npm run e2e` | **19/19 passed** (desktop 1440) | commit 403178f; niet opnieuw gedraaid |
| Fork-unittests (NUnit, in image-build) | `forks/<app>/build.sh --target unittest` | Sonarr **591 passed, 3 skipped**; Radarr **433 passed, 4 skipped**; MediaGuard-tests 25/25 per fork | `tests/integration/forks/RESULTS.md` |
| Fork-harness (4 instanties, 2 images, fake indexer/SAB) | `tests/integration/forks/run.sh` | **12 passed, 0 failed** (128.8 s) | `tests/integration/forks/RESULTS.md` |
| Full stack (productie-compose + dev-overlay) | `tests/integration/stack/run.sh` | **21 passed, 1 xfailed, 0 failed** (334 s) | `tests/integration/stack/RESULTS.md` (vóór ADR-011-zero-config-compose) |
| Recovery fault injection | `tests/fault-injection/run.sh` | **7 passed, 0 failed** (339 s) | `tests/integration/stack/RESULTS.md` |
| Seerr-contract (echte Seerr 3.5.1 + Jellyfin 12.4) | `tests/contract/seerr/run.sh` | officieel **16/17** (B1 cutover-bug); met fix **17/17, 142/142 asserties**; fix zit in core (`seerr/client.py`, commit db5036c) | `tests/contract/seerr/RESULTS.md` |
| Compose-validatie | `docker compose --env-file deploy/truenas/env.example -f deploy/truenas/compose.yaml config -q` | OK | deze ronde |
| CI-YAML | `python -c "import yaml; yaml.safe_load(open(...))"` voor `ci.yml` en `release.yml` | geldig | deze ronde (niet op GitHub gedraaid) |

**Nieuw in deze ronde (encoder):** `tests/test_mediaguard_storage_guard.py` (10 tests: pauzebodem
via brug en oude API direct herbeoordeeld, `lanes_paused`/`storage_guard` in `/health` en
`/capabilities`, herbeoordeling vóór claim, EXDEV-terugval met sha256-controle, afwijkende kopie
houdt bron). **Core:** `test_admin_health_shows_encoder_lane_pause_reason`,
`test_cache_floor_setting_is_pushed_to_encoder`, 3 tests voor het release-manifest
(repo-pad, `MG_RELEASE_MANIFEST` zoals in het image, migratielijsten).

**Niet opnieuw gedraaid in deze ronde:** stack, fault injection, Seerr-contract, fork-harness
en E2E (zware containersuites; resultaten hierboven zijn de laatste gemeten runs). De stacktest
is gemeten vóór de nul-configuratie-compose (ADR-011) en moet daarop opnieuw draaien.


## 2026-11-02 — Volledige stack, migratie en fault injection (integratie/QA)

Details, commando's, timings en run-historie: `tests/integration/stack/RESULTS.md`.

| Suite | Commando | Resultaat |
|---|---|---|
| Core unit/contract | `cd apps/core && pytest -q` | **386 passed, 2 skipped** (incl. 13 nieuwe regressietests in `tests/test_stack_regressions.py` + 2 in `tests/test_migration.py`) |
| Full stack (echte productie-compose + dev-overlay; core, recovery, encoder, web, 4 forks, fake indexer/SAB, oude Sonarr/Radarr) | `MG_TEST_CA_BUNDLE=… tests/integration/stack/run.sh` | **21 passed, 1 xfailed, 0 failed** (334 s) |
| Recovery fault injection (echte recovery-container, scratch-bibliotheek) | `tests/fault-injection/run.sh` | **7 passed, 0 failed** (339 s) |
| Compose-validatie | `docker compose --env-file deploy/truenas/env.example -f deploy/truenas/compose.yaml config -q` | OK |

Aangetoond met echte containers: seeding van instanties + API/hook-sleutels uit env; webhookregistratie
en Test-event via core; nieuwe gewone/anime-aflevering en films via RSS met de ECHTE pre-grab/pre-import-
hooks van core, provenance, post-import-analyse; missing backfill (search-only release gevonden, geen
kandidaat → `unavailable` + backoff, per-instantie quotum); noodstop; suspend/resume van één instantie;
zelfde serie in twee Sonarr-instanties zonder kruismutatie; concrete wanted-diagnoses (no-indexer-hit,
unmonitored, import-failed); herstart van core + back-up/restore; AV1-keten encoder → core-verificatie →
recovery-commit → quarantaine → release check `passed`; migratie: live bron (alleen GET's vóór cutover)
→ native restore → verify GO → cutover (nieuwe instantie grabt) → rollback (oude exact terug), en
back-upupload → activate-search; kill -9 bij elke recovery-stap + journal replay, disk full,
permission denied.

**Bekende open punten:** L2 "1080p Dual Audio vóór 2160p alleen-Japans" faalt met de standaard
kwaliteitsvolgorde van Sonarr (xfail; werkt met gegroepeerde 1080p/2160p-kwaliteit). Niet getest:
echte Seerr, Prowlarr, torrents, GPU-encode, echte TrueNAS/ZFS, web-UI in een browser.

## 2026-11-02 (middag) — Hertest op de nul-configuratie-compose (ADR-011) — integratie/QA

_Aparte sectie van de integratie/QA-agent; details en run-historie in
`tests/integration/stack/RESULTS.md` ("Zero-config compose")._

| Suite | Commando | Resultaat |
|---|---|---|
| Core ruff + pytest | `cd apps/core && ruff check . && pytest -q` | **All checks passed; 402 passed, 2 skipped** |
| Full stack + migratie (productie-compose met `mediaguard-init`, volume-subpaden, sleutels vooraf in bestanden) | `MG_TEST_CA_BUNDLE=… tests/integration/stack/run.sh` | **21 passed, 1 xfailed, 0 failed** (365 s) |
| Zero-config (NIEUW: niets vooraf, lege appdata/work, bestaande mediamap van uid 1234) | `MG_TEST_CA_BUNDLE=… tests/integration/stack/run_zero_config.sh` | **7 passed, 0 failed** (75 s) |
| Recovery fault injection (+ vergrendelingstest) | `tests/fault-injection/run.sh` | **8 passed, 0 failed** (353 s) |
| Compose zonder env-bestand (alleen defaults) | `docker compose -f deploy/truenas/compose.yaml config -q` (lege map, `env -i`) | OK (ook met dev-overlay) |

Aangetoond: init genereert token + 4 API- + 4 hooksleutels (uniek, 568:568, 0640) en is idempotent
over `up`/`down`+`up` (bestanden byte-identiek, log "alles was al ingericht"); bestaande mediamap en
-bestand behouden eigenaar 1234; alle 4 forks `healthy` in core, pre-grab vanaf elke fork
geauthenticeerd met de gegenereerde hooksleutel; recovery-heartbeat en encoder werken met het
gegenereerde interne token; bibliotheek standaard vergrendeld → AV1-activatie 409 "Bibliotheek nog
vergrendeld…" met bestand onaangeroerd, na 'Bibliotheek vrijgeven' commit; cutover ontgrendelt
automatisch; Seerr-URL's via `public_host`.

Opgeloste bevindingen: (1) **forks startten niet** met de nieuwe compose — Docker kopieert inhoud
én eigenaar (uid 1000) van `/config` uit het fork-image naar de lege volume-submap → `nocopy: true`
op alle volume-mounts; (2) provisioning-melding verwees nog naar `MG_FORK_MEDIA_MODE`;
(3) `docs/AV1_TRUENAS_PORT.md` bouwde het encoder-testimage i.p.v. runtime (`--target runtime`);
(4) tijdsafhankelijke flake `test_pre_grab_audio_preference_score_and_speed` na
`test_coverage_matrix` (niet-gesloten SQLite-verbinding) → `app`-fixture sluit de DB.
