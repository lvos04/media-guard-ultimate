# Voortgang MediaGuard Ultimate

Dit bestand is het hervatpunt bij onderbreking. Elke fase: status, bewijs, open punten.

## Fase 0 — Baseline (2026-11-02)
- `lvos04/av1` gepind op `4b31986b8e91bc3d03f5372436e02644efe0c41d`; backend → `services/encoder`, frontend → `apps/web`, specs/tests/startcode → `integrations/av1-source`.
- Baseline-tests av1 in schone venv (zonder mkvtoolnix/testdata): 121 geslaagd, 52 gefaald, 4 overgeslagen — oorzaak wordt door encoder-werkstroom onderzocht en vastgelegd in `docs/AV1_BASELINE_AUDIT.md`.
- Upstream: Sonarr `main` = `bc54afbdd0e38bdb0fca4785fcf7d7a6af77bf9a`, Radarr `master` = `a96bf7c3ecb23cb76993770727285dc17a613fbc` (worden in `forks/*/UPSTREAM.md` vastgezet).
- Core-skelet `apps/core` (FastAPI, SQLite-WAL, auth/RBAC/CSRF, secrets, scoped settings, jobs, audit, SSE): 14 tests groen.

## Werkstromen (parallel)
| Werkstroom | Map | Status |
|---|---|---|
| A Forks + testfakes | `forks/`, `tests/integration/fakes` | afgerond (zie eindstatus hieronder) |
| B1 Instanties/arr/wanted/backfill/Seerr | `apps/core/mediaguard/{arr,instances,library,wanted,seerr}` | afgerond (zie eindstatus hieronder) |
| B2 Identity/audio/policy/hooks/DV/recovery/optimalisatie | `apps/core/mediaguard/{identity,audio,policy,hooks,dv,recovery,optimization,encoding}` | afgerond (zie eindstatus hieronder) |
| C Encoder TrueNAS-port | `services/encoder` | afgerond (zie eindstatus hieronder) |
| D Design system + UI | `docs/design`, `apps/web` | afgerond (zie eindstatus hieronder) |

## Notities voor de eindoplevering (README)
- README moet bevatten: complete installatie-, test- en startgids (gebruikersverzoek).
- README moet de Seerr-koppeling uitleggen: alle vier de instanties koppelen, standaardservers, profielen, anime-routering → zie `docs/SEERR_INSTELLEN.md` (gebruikersverzoek).

## Eindstatus per fase (2026-11-02, afrondingsronde)

| Fase | Status | Bewijs | Open |
|---|---|---|---|
| 0 Research, baseline, risico's | **klaar** | `docs/AV1_BASELINE_AUDIT.md`, `docs/DECISIONS.md` (ADR-001…011), `forks/*/UPSTREAM.md`, `deploy/release-manifest.json` (gepinde SHA's en toolchain) | `docs/ACCEPTANCE.md` bestaat niet als apart bestand (acceptatie = `docs/TEST_RESULTS.md`) |
| 1 Forks als bundel | **klaar (sandbox)** | fork-images `bc54afb-mg1`/`a96bf7c-mg1`, unittests 591/433 groen, harness 12/12, Seerr-contract 17/17 met fix, CI + release-workflow (`.github/workflows/`) | CI en release nog nooit op GitHub gedraaid; GHCR-packages nog niet gepubliceerd |
| 2 Migratie en bibliotheekimport | **klaar (sandbox)** | stacktest: live bron (alleen GET's) → native restore → verify GO → cutover → rollback; migratiewizard + E2E | alleen met test-instanties, niet met de echte bibliotheek van de gebruiker |
| 3 New episode + missing backfill | **klaar (sandbox)** | stacktest: RSS-grabs via echte hooks, backfill met quotum/backoff, wanted-diagnoses | echte indexers/Prowlarr niet getest |
| 4 Identity, anime, talen, profielen | **klaar (sandbox)**, 1 bekende beperking | core-tests, stacktest; L2 "Dual Audio 1080p vóór 4K alleen-Japans" opgelost via gegroepeerde kwaliteit (commit 14fe915) | stacktest-xfail L2 moet opnieuw draaien met het nieuwe profiel |
| 5 Quality & AV1 basis | **klaar (sandbox)** | encoder 251 tests groen in toolchain-image (2 skips: QSV), DV 8.1/8.4/7-MEL, HDR10+ via libaom met read-back, staged handoff, pauzebodem zichtbaar en instelbaar | QSV/Intel Arc en echte DV7/HDR10+-bronnen niet getest |
| 6 Full Auto herstel & optimalisatie | **klaar (sandbox)** | fault injection 7/7 (kill -9 per stap, disk full, permission denied), quarantaine + release check, softwarevergrendeling bibliotheek (ADR-011) | ZFS-snapshot-adapter niet op echte ZFS getest |
| 7 Dashboard/UI | **grotendeels klaar** | Next.js-UI, E2E 19/19 (desktop), axe-checks in E2E | mobiele viewport-E2E en Engelse vertaling niet volledig; UI-coverage-matrix moet opnieuw gegenereerd (139 instellingen, tabel 126) |
| 8 Cutover & productierijp | **klaar tot aan echte host** | nul-configuratie-compose (ADR-011, `mediaguard-init`), `docs/INSTALL_TRUENAS.md`, `docs/SECURITY.md`, back-up/restore, rollback-procedure, `scripts/build-all.sh`/`test-all.sh` | cutover alleen met expliciete gebruikersactie op de echte host (nog niet gebeurd) |
| 9 AV1-integratie & productievalidatie | **sandbox-gate gehaald, productiegate open** | featurepariteit/veiligheidsregels (`docs/AV1_SAFETY_DIFF.md`, `docs/AV1_REPO_INTEGRATION.md`), encoder-suite groen | hardwarebenchmark, 4K/HDR/DV-canary op echte media en TrueNAS-runtimeproef ontbreken; oude Ubuntu-installatie pas na non-regressie + cutover uitzetten |

## Nog niet fysiek getest

- echte TrueNAS SCALE-host (24.10+): "Install via YAML", ACL's, Docker volume-subpaden, `mediaguard-init` op ZFS-datasets
- Intel Arc / QSV-encode (GPU-baan; alleen fail-closed detectie is getest), `/dev/dri`-groepen
- ZFS-snapshots en snapshot-rollback (app-rollback-procedure in `docs/INSTALL_TRUENAS.md` §8)
- Dolby Vision profiel 7 (MEL/FEL) en HDR10+ op echte bronnen (alleen synthetische fixtures)
- afspelen van de AV1-resultaten op echte clients (TV's, Plex/Jellyfin-apps, Infuse; zie `docs/AV1_DV_PLAYBACK_MATRIX.md`)
- echte Prowlarr, echte indexers en torrentclients (qBittorrent e.d.); alleen fake Newznab + fake SABnzbd
- interactie met Plex/Jellyfin (bibliotheekscans, kijkpauze van de encoder) in productie
- GHCR-publicatie via `release.yml` en ophalen door TrueNAS (privé/publieke packages)
- HTTPS achter een reverse proxy (`Secure`-cookie)

## Release 0.1.0 (2026-11-02)
- `main` aangemaakt; CI groen (core, encoder-unit, web incl. browser-E2E) op `f76ad67`/`202dccc`.
- Release-workflow (branch `release/v0.1.0`) geslaagd: alle vijf images gepubliceerd naar
  `ghcr.io/lvos04/mediaguard-{core,web,encode}:0.1.0`, `mediaguard-sonarr-fork:4.0.20.3014-mg1`,
  `mediaguard-radarr-fork:6.4.4.10685-mg1`; tag `v0.1.0` door de workflow aangemaakt.
- **Open voor de gebruiker:** de vijf packages staan op *private* (anoniem ophalen geeft 403).
  GitHub → profiel → Packages → per package → Package settings → Change visibility → Public.
  Daarna kan TrueNAS ze zonder inloggen ophalen.
