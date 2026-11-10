# Inventaris `media-guard-ultimate` — platform-v2 fase P0 (B04-001)

Peildatum **2026-11-09** · branch `ccr-c133d7a0-kuz1cv` · HEAD bij inventaris `60b50ab` (= tag `v1.2.2`
+ drie commits met alleen pakketdocumentatie) · contracten `contracts 1.1`.
Deze inventaris beschrijft wat **in de repository** staat en wat in deze bouwomgeving echt is gedraaid.
Over de echte NAS-installatie (geïnstalleerde tag, datasets, poorten, D01–D18) zegt hij niets: die is
**NIET GEVALIDEERD** en wordt pas via de onboarding-wizard/TrueNAS-API-readback vastgesteld.

Legenda voor de kolom **Bewaakt** — onderdelen die nu al iets leveren dat het platform **nooit dubbel mag
bouwen, vervangen of uitschakelen** (PLATFORM_CONTRACTEN §12, B04 §5):
**I** = integriteit (bestanden/database), **R** = rollback/herstel/back-up, **M** = monitoring/health,
**V** = veiligheidsinvariant (noodstop, rechten, vergrendeling).

Reproduceren (alleen lezen, tijdelijke map, geen netwerk naar buiten):

```bash
.venv/bin/python scripts/platform-v2-routes.py          # bijlage A (routetabel, bestand:regel)
.venv/bin/python scripts/platform-v2-routes.py --probe  # §4.4 (contractprobe met API-sleutels)
```

---

## 1. Baseline 1.2.2 en compat-matrix (B04-001)

### 1.1 Git en release

| Gegeven | Waarde | Bron |
|---|---|---|
| Versiecommit | `de710c7` "Release 1.2.2: verbeterronde met 40 punten" (2026-11-09 01:55 UTC) | `git log` |
| Tag `v1.2.2` | geannoteerde tag `a3eb19ef…` → commit **`30f7415`** (= `de710c7` + `a420ee8` + `30f7415`, twee CI/E2E-commits) | `git ls-remote --tags origin` |
| Codeverschil tag → HEAD | **geen** (alleen `docs/platform-v2/**`, 30 bestanden) | `git diff --stat 30f7415 HEAD -- . ':!docs'` (leeg) |
| GitHub-release | `v1.2.2` "MediaGuard Ultimate 1.2.2", gepubliceerd 2026-11-09T03:42:40Z, APK `mediaguard-android-1.2.2.apk` | `gh api repos/lvos04/media-guard-ultimate/releases/tags/v1.2.2` |
| Release-workflow | run `42554745125` op `release/v1.2.2` @ `30f7415`: **success** — tests (core 3.12 + 3.14, encoder-unit, web incl. e2e), images core/web/encode/forks, alles-in-één, **rooktest alles-in-één**, publiceren, tag | `gh api …/actions/runs/42554745125/jobs` |
| CI op deze branch | run `42587401041` @ `60b50ab` (HEAD): **success** (core 3.12/3.14, encoder-unit, web); zware jobs (encoder-image, forks, upstream-sync) alleen nightly → overgeslagen | `gh api …/actions/runs?per_page=10` |
| Changelog | `CHANGELOG.md` §1.2.2 (bewaking, leveringen, noodstop overal, AV1-banen, VMAF fail-closed, beveiliging, Android, updates, releases) | `CHANGELOG.md:3-40` |
| Versies in code | core `1.2.2` (`apps/core/mediaguard/__init__.py:3`, `apps/core/pyproject.toml:7`), web `1.2.2` (`apps/web/package.json:3`), manifest `1.2.2` (`deploy/release-manifest.json:4`) | — |

### 1.2 Images en digests (GHCR, gemeten 2026-11-09 met `docker buildx imagetools inspect`)

| Image:tag | Index-digest | Rol |
|---|---|---|
| `ghcr.io/lvos04/mediaguard-core:1.2.2-aio` | `sha256:14e540ba9cad0bb7fbda8295bca89496f58462a201361544916f3b16b3a52dc6` (amd64-manifest `sha256:8f12c932…d217`) | **Custom App-image** (alles-in-één) |
| `ghcr.io/lvos04/mediaguard-core:1.2.2` | `sha256:a8daf269e93285cf71b6e9a18e2f75f7b52fa07da230c679dd71b14bc85bbc2b` | core/recovery (losse compose) |
| `ghcr.io/lvos04/mediaguard-web:1.2.2` | `sha256:7bfac1239bf4d834d3dfc752bbf61285cf36785aaa363309a00017a558f9fc01` | web |
| `ghcr.io/lvos04/mediaguard-encode:1.2.2` | `sha256:b796d101b42f4f1a743591c5117d2b5bee39d7fe8495a0c19ddf24f322df67da` | AV1-engine |
| `ghcr.io/lvos04/mediaguard-sonarr-fork:4.0.20.3014-mg2` | `sha256:d446f7d042584c048eb5ec2e05d4836150044ad913a8eb87b7a39d21b2739cb1` | Sonarr-fork (GPL-3.0) |
| `ghcr.io/lvos04/mediaguard-radarr-fork:6.4.4.10685-mg1` | `sha256:8b13bd642960afc38f847ae6e1bcd981e5a9123cfb4e61bd6a61e38788cd1839` | Radarr-fork (GPL-3.0) |

Niet gelukt in deze omgeving: de image-labels (`org.opencontainers.image.revision`) lezen en het image
trekken — de blob-host `pkg-containers.githubusercontent.com` geeft via de proxy `403 Forbidden`. De koppeling
image → commit rust daarom op de release-run (`30f7415`), niet op een gelezen label.
`deploy/release-manifest.json` legt tags, upstream-SHA's en schemaversies vast maar **geen digests**
(`"released": null`); er is geen `release/digests.json` en geen SBOM (zie DELTA_MATRIX C-10).

### 1.3 Compat-matrix 1.2.2

| Onderdeel | Versie / pin | Schema | Rook-/teststatus | Geïnstalleerd op NAS |
|---|---|---|---|---|
| core + recovery | 1.2.2 (Python 3.12 los, 3.14 in AIO) | 35 SQL-migraties, laatste `100_ai_advisor` (`apps/core/mediaguard/migrations/`) | CI groen; lokaal 1069 passed/2 skipped (§6) | **onbekend — NIET GEVALIDEERD** |
| web | 1.2.2, Next 16.3.3, React 19.2.8, Node 22 | — | CI groen incl. Playwright-e2e; lokaal lint + typecheck groen | onbekend |
| AV1-engine | 1.2.2, upstream `lvos04/av1@4b31986`, FFmpeg 8.1.3 (sha256 gepind), SVT-AV1 2.3.0, dovi_tool 2.3.1 | 25 migraties (`services/encoder/app/migrations/001…025`) | CI-subset groen; volledige suite alleen nightly | onbekend |
| Sonarr-fork | 4.0.20.3014 @ `bc54afb` + 5 patches (`forks/sonarr/patches`) | eigen SQLite (upstream) | nightly forkbuild + unittests (`docs/TEST_RESULTS.md`: 591 passed) | onbekend |
| Radarr-fork | 6.4.4.10685 @ `a96bf7c` + 3 patches | eigen SQLite (upstream) | idem (433 passed) | onbekend |
| AIO-image | `1.2.2-aio` | core + encoder samen | **rooktest** op database van eerdere versie: success (release-run) | onbekend |
| Android-app | 1.2.2 APK | — | `android`-workflow success (run `42557287666`) | onbekend |

**Afgeronde 1.2.2-functies worden niet opnieuw als werk geboekt** (B04-001-acceptatie): zie §8 en DELTA_MATRIX
(kolom *bestaand*).

---

## 2. Diensten in het alles-in-één-image

Eén container (`deploy/aio/Dockerfile`), PID 1 = `tini` → `python -m mediaguard.aio` (supervisor,
`apps/core/mediaguard/aio.py`). Alleen de web-UI en de vier fork-poorten zijn van buiten bereikbaar; core,
recovery, encoder en hookwacht luisteren op `127.0.0.1` (`aio.py:36-41`, `aio.py:179-336`).

| Proces | Poort | Rol | Code | Bewaakt |
|---|---|---|---|---|
| supervisor (`mediaguard.aio`) | — | mappen/sleutels (`bootstrap`), rechten 568, start/herstart met backoff, statusbestand voor healthcheck, back-up vóór update, terugzetten bij stop | `apps/core/mediaguard/aio.py`, `bootstrap.py`, `deploy/aio/healthcheck.sh` | **R M** |
| core (`MG_ROLE=core`) | 127.0.0.1:38100 | API, login/RBAC, beleid, wanted/backfill, Seerr, inrichting, bewaking, meldingen, AI-adviseur (standaard uit) | `apps/core/mediaguard/` | **M V** |
| recovery (`MG_ROLE=recovery`) | 127.0.0.1:38101 | **enige schrijver** op bibliotheekpaden: vervangtransacties met journaal, quarantaine, herstel | `apps/core/mediaguard/recovery/executor.py:1-40`, `docs/DATA_RECOVERY.md` | **I R** |
| AV1-engine | 127.0.0.1:38000 | encodes, VMAF/SSIM-kwaliteitspoort, DV/HDR10+-behoud, banen CPU/GPU; vervangt **nooit** zelf (handoff) | `services/encoder/app/` | **I M** |
| hookwacht | 127.0.0.1:38102 | houdt de noodstop vast voor pre-grab/pre-import als core herstart | `apps/core/mediaguard/hookguard.py:1-30` | **V** |
| 4 forks | 38989 / 38990 / 37878 / 37879 | Sonarr Series/Anime, Radarr Films/Animefilms; pre-grab/pre-import-hooks naar core | `forks/*/patches`, `aio.py:51-56` | **V** |
| web (Next.js + `server.mjs`) | **30080** | UI; proxyt alleen `/api/v1/*` en `/api/encoder/*` naar core, stript interne headers | `apps/web/server.mjs:4-40` | — |

Datamappen: `/config` (appdata: `core/mediaguard.db`, `secret.key`, `secrets/`, `encode/*.db`, fork-configs,
`logs/`, `backups/`), `/work` (staging, quarantaine), `/data` (media + downloads, één dataset voor hardlinks)
(`aio.py:181-215`). Healthcheck: supervisor schrijft elke ronde `/tmp/mediaguard-health`; `HEALTHCHECK` in
`deploy/aio/Dockerfile:55-56` faalt bij een statusbestand ouder dan 90 s of ≠ `ok`.

---

## 3. Core-modules

Registratie in `apps/core/mediaguard/app.py:32-90` (`MODULES`). Per module de kernfunctie en of hij al
integriteit/rollback/monitoring levert.

| Module | Functie | Belangrijkste bestanden | Bewaakt |
|---|---|---|---|
| `api.auth`, `auth`, `apikeys`, `security` | login (scrypt), sessie + CSRF, RBAC viewer<operator<admin, API-sleutels (`X-Api-Key`, rol viewer/operator), 2FA/TOTP, inlogrem, sessies | `auth.py:28-170`, `apikeys.py:32-252`, `security/` | **V** |
| `api.system` | `/health`, `/status`, **noodstop**, audit, jobs, meldingen | `api/system.py:19-361` | **M V** |
| `emergency`, `hookguard` | één noodstopregel voor alle automatische acties; vastgehouden tijdens core-herstart | `emergency.py`, `hookguard.py:65-110` | **V** |
| `api.events`, `events` | outbox (`outbox_events`) + SSE `/events/stream` met replay via `Last-Event-ID` | `events.py:12-35`, `api/events.py:19-59` | — |
| `instances` | vier instanties, beheer (queue, download-clients, indexers, eigen back-ups) via arr-API | `instances/manage.py`, `instances/api.py` | — |
| `library`, `wanted` | bibliotheek-cache, wanted/backfill met quota, **diagnose per item** met oorzaakcodes | `wanted/api.py:229-470`, `wanted/backfill.py:315-614`, `wanted/diagnosis.py` | — |
| `seerr`, `delivery`, `routing` | Seerr als doorgeefluik, leveringsbewaking per aanvraag, anime-routering | `seerr/`, `delivery/`, `routing/` | **V** (geen terugkeer verwijderde titels) |
| `policy`, `hooks`, `identity`, `audio`, `dv` | beslisregels pre-grab/pre-import, juiste-aflevering-controle, taalregels, DV-beleid | `hooks/service.py:185-370`, `identity/`, `dv/` | **I V** |
| `recovery` | vervangtransacties, quarantaine, opruimen met vrijgavecontrole, poolpauze, optionele ZFS-snapshot | `recovery/api.py:141-160`, `recovery/executor.py`, `recovery/zfs.py` | **I R** |
| `encoding.*`, `optimization` | brug naar AV1-engine, banen, autopilot, resources (CPU-%, stille uren), plannen met simulatie | `encoding/lanes.py`, `encoding/resources.py`, `encoding/api.py` | **I M** |
| `monitor`, `taskwatch`, `health_summary`, `attention` | achtergrondbewaking (elke paar min), zelfherstartende taken, gezondheidsmodel, 'Wacht op jou' | `monitor.py:1-20, 304-481`, `taskwatch.py:1-12`, `health_summary.py:215-335`, `attention.py:742` | **M** |
| `maintenance` | geverifieerde back-ups (core + encoder-DB), herstelbundel (versleuteld, incl. sleutels), back-up vóór update, terugzetten, supportbundel, `/admin/version`, `/admin/health` | `maintenance/api.py:102-215, 600-781`, `maintenance/bundle.py:1-20`, `maintenance/preupdate.py:1-25` | **R M** |
| `notify` | kanalen ntfy/Pushover/Telegram/Discord/Slack/webhook/e-mail, dagoverzichten, uitgaande outbox, offsite-bundel | `notify/channels.py:23-150`, `notify/service.py`, `notify/offsite.py` | **M** |
| `mediaserver` | Emby/Jellyfin/Plex: verversen na overdracht, afspeeltest, AV1 pauzeren tijdens kijken, Bazarr/OpenSubtitles | `mediaserver/clients.py`, `mediaserver/service.py` | **M** |
| `migration`, `provisioning` | migratie oude Sonarr/Radarr met cutover/rollback; zero-config inrichting instanties/Prowlarr/Seerr met plan/apply/drift | `migration/`, `provisioning/` | **R** |
| `mobile` | compacte app-API (`/app/status`, `/app/control`, koppelen) | `mobile.py:206-634` | — |
| `ai` | AI-adviseur (Claude/OpenAI officiële API), **standaard uit** (`ai.provider=off`), maandbudget | `ai/settings.py:59-110`, `ai/budget.py` | **V** (alleen adviserend) |
| `updates`, `notices` | dagelijkse versiecontrole (GitHub), 'Nieuw in deze versie' — nooit zelf bijwerken | `updates.py:270-302`, `notices.py` | — |

---

## 4. API-routes

### 4.1 Overzicht

**432 routes** (FastAPI, alle onder `/api/v1`; volledige lijst met `bestand:regel` in **bijlage A**). De kolom
"min. rol" is de gedeclareerde afhankelijkheid (`require_viewer/operator/admin`); enkele routes doen binnenin
een extra controle (bv. `wanted/{id}/diagnosis?interactive=true` vraagt operator, `wanted/api.py:378-386`).

| Module | Totaal | geen auth* | ingelogd | viewer | operator | admin |
|---|---|---|---|---|---|---|
| `instances` | 92 | 0 | 0 | 38 | 52 | 2 |
| `api` (auth/system/settings/events) | 43 | 6 | 4 | 17 | 7 | 9 |
| `migration` | 34 | 0 | 0 | 6 | 15 | 13 |
| `encoding` | 23 | 1 | 1 | 12 | 9 | 0 |
| `provisioning` | 20 | 0 | 0 | 5 | 7 | 8 |
| `recovery` | 18 | 0 | 0 | 8 | 5 | 5 |
| `ai` | 17 | 0 | 0 | 9 | 4 | 4 |
| `seerr` | 16 | 0 | 0 | 8 | 5 | 3 |
| `maintenance` | 15 | 0 | 0 | 6 | 2 | 7 |
| `library` | 14 | 0 | 0 | 11 | 3 | 0 |
| `wanted` | 14 | 0 | 0 | 8 | 6 | 0 |
| `notify` | 12 | 0 | 0 | 1 | 0 | 11 |
| `policy` | 12 | 0 | 0 | 7 | 4 | 1 |
| `security` | 11 | 0 | 11 | 0 | 0 | 0 |
| `dv` | 10 | 0 | 0 | 4 | 6 | 0 |
| `optimization` | 10 | 0 | 0 | 4 | 6 | 0 |
| `mediaserver` | 9 | 0 | 0 | 3 | 4 | 2 |
| `mobile` | 7 | 0 | 0 | 3 | 2 | 2 |
| `downloads` | 7 | 0 | 0 | 4 | 2 | 1 |
| overige (delivery, identity, attention, routing, storage_*, hooks, updates, activity, audio, app, health_summary, monitor, notices) | 51 | 3 | 0 | 21 | 25 | 2 |

\* "geen auth" = `POST /auth/setup|login|login/2fa|logout`, `GET /auth/setup-status`, `GET /health`
(liveness), drie hook-routes (`/hooks/*`, eigen hook-sleutel per instantie, `hooks/api.py:73-110`) en
`WS /encoder/ws` (eigen controle). `GET /api/v1/openapi.json` en `/api/v1/docs` bestaan alleen voor een
**beheerder** (`app.py:163-178`) — met een API-sleutel (nooit admin) geeft dat **403** (§4.4).

### 4.2 Routes die de Hub-adapter kan gebruiken (bestaand, `X-Api-Key`)

| Doel | Methode + pad | Min. rol | Bestand:regel |
|---|---|---|---|
| liveness + versie | `GET /api/v1/health` | — | `apps/core/mediaguard/api/system.py:19` |
| eigen rol (sleutelcontrole) | `GET /api/v1/auth/me` | viewer | `apps/core/mediaguard/api/auth.py:151` |
| status, noodstop (`active, reason, by, since`), jobs, aandacht, health | `GET /api/v1/status` | viewer | `apps/core/mediaguard/api/system.py:25` |
| versie, schemaversies, release-manifest | `GET /api/v1/admin/version` | viewer | `apps/core/mediaguard/maintenance/api.py:759` |
| componenten (core/recovery/encoder/forks/opslag) | `GET /api/v1/admin/health` | viewer | `apps/core/mediaguard/maintenance/api.py:600` |
| gezondheidsmodel / fouten | `GET /api/v1/health/summary`, `/health/errors` | viewer | `apps/core/mediaguard/health_summary.py:215`, `:322` |
| bewaking (laatste ronde, alerts, taken, pool) | `GET /api/v1/monitor` | viewer | `apps/core/mediaguard/monitor.py:469` |
| incidenten 'Wacht op jou' | `GET /api/v1/attention` | viewer | `apps/core/mediaguard/attention.py:742` |
| compacte status (encoder-banen, downloads, opslag) | `GET /api/v1/app/status` | viewer | `apps/core/mediaguard/mobile.py:206` |
| wanted (pagina's `page/page_size≤500`), KPI's, oorzaken | `GET /api/v1/wanted`, `/wanted/kpis`, `/wanted/reasons` | viewer | `apps/core/mediaguard/wanted/api.py:229`, `:248`, `:257` |
| wanted-detail + diagnose (cache) | `GET /api/v1/wanted/{id}`, `/wanted/{id}/diagnosis?cached=true` | viewer | `apps/core/mediaguard/wanted/api.py:354`, `:364` |
| beleid | `GET /api/v1/policy/effective` | viewer | `apps/core/mediaguard/policy/api.py:119` |
| aanvragen + leverstatus | `GET /api/v1/delivery/requests`, `/seerr/my-requests` | viewer | `apps/core/mediaguard/delivery/api.py:35`, `apps/core/mediaguard/seerr/discover.py:207` |
| downloads | `GET /api/v1/downloads/health`, `/downloads/failures`, `/instances/{id}/queue` | viewer | `apps/core/mediaguard/downloads/failures.py:518`, `:537`, `apps/core/mediaguard/instances/manage.py:862` |
| AV1 | `GET /api/v1/encoding/jobs`, `/encoding/forecast`, `/encoding/resources`, `/encoding/autopilot` | viewer | `apps/core/mediaguard/encoding/api.py:196`, `:212`, `apps/core/mediaguard/encoding/resources.py:130`, `apps/core/mediaguard/encoding/autopilot.py:534` |
| opslag | `GET /api/v1/storage/forecast` (incl. TrueNAS-pool), `/storage/ledger`, `/quarantine/usage` | viewer | `apps/core/mediaguard/storage_forecast.py:197`, `apps/core/mediaguard/storage_ledger.py:751`, `apps/core/mediaguard/recovery/api.py:465` |
| meldingen (`limit≤200`, `before_id`) | `GET /api/v1/notifications`, `/notifications/count` | viewer | `apps/core/mediaguard/api/system.py:234`, `:270` |
| events | `GET /api/v1/events/recent?limit≤1000` (nieuwste eerst, geen cursor), `GET /api/v1/events/stream` (SSE, replay `Last-Event-ID`, max 500) | viewer | `apps/core/mediaguard/api/events.py:52`, `:19` |
| audit | `GET /api/v1/audit` (`limit/offset`) | viewer | `apps/core/mediaguard/api/system.py:111` |
| back-ups (lijst, bundelstatus, terugzetstatus) | `GET /api/v1/backups`, `/backups/bundle`, `/backups/terugzetten` | viewer | `apps/core/mediaguard/maintenance/api.py:260`, `:352`, `:404` |
| **noodstop aan/uit** | `POST /api/v1/system/emergency-stop` | **operator** | `apps/core/mediaguard/api/system.py:78` |
| **herzoek één item** (telt mee in quotum) | `POST /api/v1/wanted/{id}/search` | **operator** | `apps/core/mediaguard/wanted/api.py:394` |

### 4.3 Foutvorm

Bestaande routes antwoorden met `{"detail": "<NL tekst>"}` of `{"detail": {"code", "message"}}`
(`docs/APP_API.md:4-6`, `app.py:150-152`). Het contractformaat `{code, message, details, trace_id}` (§5) bestaat
niet. MSO, de web-UI en de Android-app hangen van de huidige vorm af — **niet wijzigen**; nieuwe connectorroutes
krijgen het contractformaat (DELTA_MATRIX C-05).

### 4.4 Contractprobe (in-process, `scripts/platform-v2-routes.py --probe`, 2026-11-09)

Echte app in een tijdelijke map; beheerder maakt sleutels `hub-viewer` (viewer), `hub-operator` (operator) en
een ingetrokken viewer-sleutel; daarna alleen `X-Api-Key`. **Sim-getest (in-process), geen NAS.**

| Probe | Methode | Pad | Sleutel | HTTP | Body (sleutels) |
|---|---|---|---|---|---|
| liveness bestaand | GET | `/api/v1/health` | none | 200 | `{status,version}` |
| eigen rol | GET | `/api/v1/auth/me` | viewer | 200 | `{csrf_token,locale,preferences,role,theme,twofa_enabled,…}` |
| status + noodstop | GET | `/api/v1/status` | viewer | 200 | `{attention,emergency_stop,health,jobs,role,time,…}` |
| versie + schema + manifest | GET | `/api/v1/admin/version` | viewer | 200 | `{manifest,manifest_found,manifest_path,schema_versions,version}` |
| gezondheidsmodel | GET | `/api/v1/health/summary` | viewer | 200 | `{active,attention,av1,complete,encoder,errors,…}` |
| componenten | GET | `/api/v1/admin/health` | viewer | 200 | `{components,emergency_stop,storage,write_probe}` |
| wanted (pagina) | GET | `/api/v1/wanted?page_size=1` | viewer | 200 | `{items,page,page_size,total}` |
| beleid | GET | `/api/v1/policy/effective` | viewer | 200 | `{chain,config,profile_id,profile_name_nl,trace}` |
| aandacht/incidenten | GET | `/api/v1/attention` | viewer | 200 | `{count,dismissed,errors,items}` |
| meldingen | GET | `/api/v1/notifications?limit=1` | viewer | 200 | `[0 items]` |
| events (outbox) | GET | `/api/v1/events/recent?limit=1` | viewer | 200 | `[0 items]` |
| audit | GET | `/api/v1/audit?limit=1` | viewer | 200 | `[1 items]` |
| back-ups (lijst) | GET | `/api/v1/backups` | viewer | 200 | `[0 items]` |
| OpenAPI met viewer-sleutel | GET | `/api/v1/openapi.json` | viewer | **403** | `{detail}` |
| OpenAPI met operator-sleutel | GET | `/api/v1/openapi.json` | operator | **403** | `{detail}` |
| contract §5 healthz | GET | `/healthz` | none | **404** | `{detail}` |
| contract §5 readyz | GET | `/readyz` | none | **404** | `{detail}` |
| contract §6 discover | GET | `/connector/v1/discover` | viewer | **404** | `{detail}` |
| contract §6 health | GET | `/connector/v1/health` | viewer | **404** | `{detail}` |
| contract §6 items | GET | `/connector/v1/items?type=wanted` | viewer | **404** | `{detail}` |
| contract §6 events | GET | `/connector/v1/events` | viewer | **404** | `{detail}` |
| contract §6 propose | POST | `/connector/v1/propose` | operator | **404** | `{detail}` |
| contract §6 apply | POST | `/connector/v1/apply` | operator | **404** | `{detail}` |
| contract §6 rollback | POST | `/connector/v1/rollback` | operator | **404** | `{detail}` |
| noodstop aan met viewer-sleutel | POST | `/api/v1/system/emergency-stop` | viewer | 403 | `{detail}` |
| noodstop aan met operator-sleutel | POST | `/api/v1/system/emergency-stop` | operator | 200 | `{active,by,reason,since}` |
| status tijdens noodstop | GET | `/api/v1/status` | viewer | 200 | `{attention,emergency_stop,health,jobs,role,time,…}` |
| noodstop uit met operator-sleutel | POST | `/api/v1/system/emergency-stop` | operator | 200 | `{active,by,reason,resumed,resumed_nl,since}` |
| ingetrokken sleutel | GET | `/api/v1/status` | revoked | 401 | `{detail}` |

Conclusies: (1) alle leesroutes voor het Hub-read-model bestaan met een **viewer**-sleutel; (2) de capability-
detectie via OpenAPI werkt **niet** met een sleutel (admin-only) — een sleutel moet routes proben (zoals MSO al
doet: `media-source-optimizer/apps/core/mso/connectors/mediaguard.py:445-457`); (3) geen enkele route uit
contract §5/§6 bestaat; (4) een **operator**-sleutel kan de noodstop zowel aan- als **uit**zetten (geen
scope-beperking); (5) ingetrokken sleutel → 401 (geen open toegang, EX16).

---

## 5. Databases en migraties

| Database | Pad (AIO) | Migraties | Back-up | Bewaakt |
|---|---|---|---|---|
| core (SQLite WAL; recovery-rol `synchronous=FULL`) | `/config/core/mediaguard.db` | 35 bestanden `apps/core/mediaguard/migrations/001_core.sql … 100_ai_advisor.sql`, eenmalig toegepast via `schema_migrations` (`db.py:150-215`); alleen vooruit (nooit een toegepast bestand wijzigen, `migrations/README.md`) | dagelijks + handmatig, SQLite-backup-API → `integrity_check` → sha256-meta (`maintenance/api.py:102-145`); **automatische set vóór een migratie** (`db.py:218`, `maintenance/preupdate.py`) | **I R** |
| AV1-engine (SQLite) | `/config/encode/*.db` | 25 bestanden `services/encoder/app/migrations/001_init.sql … 025_routeleer.sql` (`schema_versie`) | meegekopieerd met elke core-back-up (`maintenance/api.py:158-185`) en in de set vóór update | **I R** |
| 4 forks (SQLite, upstream-schema) | `/config/<instantie>/` | upstream Sonarr/Radarr-migraties | eigen wekelijkse fork-back-ups (`/api/v1/instances/{id}/backups`, `instances/manage.py:927-942`); **niet** in de core-back-up/herstelbundel | **R** (eigen fork) |
| geheimen | `/config/core/secret.key` (Fernet, 0600), `/config/secrets/` (interne token, fork-API- en hooksleutels) | — | alleen in de **versleutelde herstelbundel** (X25519 + AES-256-GCM, herstelcode; `maintenance/bundle.py:1-20`) en via dataset-snapshot | **R V** |

Schemabescherming bij terugdraaien: de README eist "eerst appdata-snapshot terug, dan oude tag"
(`README.md:395-415`); een **codecontrole** die een ouder image weigert op een nieuwer schema bestaat niet
(onbekende/nieuwere versies in `schema_migrations` worden genegeerd, `db.py:162-164`). Zie DELTA_MATRIX EX15.

Nummerreeksen (`apps/core/mediaguard/migrations/README.md`): 001–019 kern, 020–039 instanties, 040–059
identiteit/beleid, 060–079 recovery/encoding, 080–099 migratie, 100–119 AI, 120–139 systeem. Een eventuele
P3-migratie voor connector/scopes krijgt een **nieuwe** reeks (voorstel 140–159).

---

## 6. Auth, rollen en API-sleutels (hoe de Hub ze gebruikt)

| Identiteit | Mechanisme | Rechten | Code |
|---|---|---|---|
| mens | sessiecookie `mg_session` (HttpOnly, SameSite=Strict) + `X-CSRF-Token`, scrypt, 2FA | viewer < operator < admin | `auth.py:28-170` |
| koppeling (MSO, Android, **Hub**) | `X-Api-Key: mgk_…`; DB bewaart alleen sha256 + prefix; ongeldig/ingetrokken/verlopen → 401, nooit terugval op cookie; 10 fouten/IP/5 min → 429 | **viewer of operator, nooit admin** (`apikeys.py:35`, DB-`CHECK`) | `apikeys.py:118-143` |
| interne diensten | `X-MediaGuard-Token` (admin) alleen vanaf interne verbinding; web stript de header | admin | `auth.py:91-108`, `apps/web/server.mjs:27` |
| forks | `X-MediaGuard-Hook-Key` per instantie, alleen op `/api/v1/hooks/*` | hooks | `hooks/api.py:73-110`, `context.py:50-61` |

**Gebruik door de Hub (bestaand, zonder codewijziging):** de beheerder maakt in MediaGuard *Instellingen →
Gevorderd → API-sleutels* een sleutel `hub` met rol **viewer** (read-model) en — alleen als de Hub de noodstop
mag aanzetten of één herzoek mag vragen — een tweede sleutel `hub-acties` met rol **operator**. De gebruiker voert
ze in de Hub-UI in (PLATFORM_CONTRACTEN §3, laatste punt: vault-referentie, alleen laatste 4 tekens zichtbaar).
Elke actie staat in de MediaGuard-audit als `apikey:<naam>` (`auth.py:41-45`); `GET /api/v1/auth/api-keys` (admin)
toont per sleutel de acties van de laatste 7 dagen (`apikeys.py:175-214`).
Beperking: een operator-sleutel mag **alle** operator-routes (o.a. opruimen starten, noodstop **uit**,
quarantaine terugzetten) — er bestaan geen scopes per route. Zie DELTA_MATRIX B04-003/B04-009.

---

## 7. Events, meldingen en hoe de Hub ze kan gebruiken

| Bron | Wat | Hub-gebruik (bestaand) | Code |
|---|---|---|---|
| outbox `outbox_events(id, ts, topic, instance_id, payload)` | persistente events: `system.emergency_stop`, `recovery.committed`, `recovery.<state>`, `wanted.unavailable`, `backfill.run`, `library.synced`, `instance.status/suspended/resumed`, `routing.anime_moved`, `provisioning.applied`, `policy.profile_changed`, `dv.policy_changed`, `migration.step/autopilot`, `analysis.post_import`, `ai.case.created/updated`, `settings.changed`, `arr.webhook`, `job.started/progress` | pollen via `GET /api/v1/events/recent` (geen cursor) of SSE `GET /api/v1/events/stream` met `Last-Event-ID` = laatste `id` (replay ≤ 500) → `id` is een bruikbare watermark | `events.py:17-27`, `migrations/001_core.sql:92-99`, `api/events.py:19-59` |
| niet-persistente events | `notification`, `job.finished` | alleen live via SSE | `api/system.py:350`, `jobs.py:357` |
| meldingen `notifications` | ernst, titel, tekst, link, teller, dedupe, opgelost | `GET /api/v1/notifications?before_id=` (cursor op id) + `/notifications/count` | `api/system.py:234-310` |
| 'Wacht op jou' | incidenten met ernst, href, acties, `count` | `GET /api/v1/attention` | `attention.py:742` |
| uitgaande kanalen | ntfy, Pushover, Telegram, Discord, Slack, **webhook** (`{source,title,body,link,priority}`), e-mail; per kanaal uurmaximum en retry-outbox | webhook kan een Hub-URL aanroepen, maar zonder service-token en niet in de event-envelope (§7) | `notify/channels.py:23-150`, `notify/service.py` |

Contract §7 (envelope met `event_id`/`source_event_id`/`classification`, push naar `/api/v1/events/inbox`)
bestaat niet. Voorstel: de Hub **pollt** (contract §7 staat dat toe "zonder native events"); in P3 een
read-only `/connector/v1/events?cursor=` die `outbox_events` in de envelope giet (DELTA_MATRIX C-07).

---

## 8. Back-up, herstel en rollback (bestaand — **nooit dubbel bouwen**)

| Mechanisme | Wat | Code / doc | Bewaakt |
|---|---|---|---|
| dagelijkse + handmatige back-up | core-DB + encoder-DB, hersteltest (`integrity_check`), sha256, bewaartermijn; alleen geverifieerde back-ups tellen; melding bij mislukken | `maintenance/api.py:102-260`, test `test_onderhoud.py::test_failed_backup_leaves_no_partial_files_and_notifies` | **R** |
| back-up vóór update | set per update (core + encoder) door de supervisor vóór de start bij openstaande migratie; terugzetten = klaarzetten + omwisselen bij stop | `maintenance/preupdate.py`, `db.py:218`, test `test_onderhoud.py::test_pre_update_backup_listed_staged_and_swapped` | **R** |
| herstelbundel | versleuteld (X25519 + AES-256-GCM, herstelcode), incl. `secret.key` en `secrets/`; herstel zonder draaiende MediaGuard (`python -m mediaguard.maintenance.bundle restore`); offsite-optie | `maintenance/bundle.py`, `notify/offsite.py`, `docs/DATA_RECOVERY.md` §8 | **R** |
| vervangtransacties | journaal (write-ahead), quarantaine vóór activeren, sha256, replay na crash, `conflict` i.p.v. gissen; integriteitsinstellingen vergrendeld | `recovery/executor.py`, `docs/DATA_RECOVERY.md` §1-3, `tests/fault-injection/` | **I R** |
| quarantaine + vrijgavecontrole | originelen 7 dagen/500 GB, terugzetten per item; opruimen pauzeert bij noodstop of ongezonde pool | `recovery/api.py:141-160, 409-680` | **I R** |
| optionele ZFS-snapshot vóór vervanging | TrueNAS **REST v2.0** (`/api/v2.0/pool/snapshot`, terugval `zfs/snapshot`) met eigen sleutel `truenas:apikey`; standaard niet ingesteld | `recovery/zfs.py:1-60` | **R** |
| migratie-rollback | oude Sonarr/Radarr + Seerr exact terug | `migration/cutover.py`, `/migration/rollback` (admin), `/seerr/cutover/rollback` (admin) | **R** |
| fork-back-ups | upstream-functie per instantie | `instances/manage.py:927-942` | **R** |
| documentatie | updaten/terugdraaien via appdata-snapshot + oude tag | `README.md:368-415`, `docs/INSTALL_TRUENAS.md` §6-8 | **R** |

## 9. Monitoring en health (bestaand — **nooit dubbel bouwen of uitschakelen**)

| Mechanisme | Wat | Code | Bewaakt |
|---|---|---|---|
| container-healthcheck | supervisorstatus ≤ 90 s oud en `ok` | `deploy/aio/healthcheck.sh`, `deploy/aio/Dockerfile:55-56` | **M** |
| supervisor | herstart onderdelen met backoff, herstartlus-uitleg, migratiemarkering | `aio.py:438-500` (`Supervisor` vanaf `:500`) | **M** |
| achtergrondbewaking | elke paar min: pool meten, aandacht/gezondheid nalopen, push met dedupe/cooldown; noodstop > 2 u, Seerr-sync faalt, taak stil, pool ongezond | `monitor.py:1-20, 304-330, 458-481`; tests `test_bewaking.py` | **M** |
| taakbewaker | elke achtergrondlus herstart zichzelf, laatste geslaagde ronde per taak | `taskwatch.py:1-12, 131-170` | **M** |
| gezondheidsmodel / componenten | `/health/summary`, `/admin/health`, `/status.health` | `health_summary.py:215`, `maintenance/api.py:600` | **M** |
| AV1-banen | GPU-stilstand-melding na 30 min, geen vals alarm bij kijken/handmatige pauze/stille uren | `encoding/lanes.py:1-20`; tests `test_encoding_lanes.py::test_gpu_idle_benign_pauses_and_no_work_do_not_alert` | **M** |
| pool | TrueNAS-poolstatus via REST `/api/v2.0/pool` (alleen als URL+sleutel ingesteld); **niet ingesteld = geen pauze** | `storage_forecast.py:125-190`; tests `test_bewaking.py::test_degraded_pool_alerts_and_pauses_purge`, `::test_pool_not_configured_means_no_pause` | **M** |

## 10. Noodstop en veiligheidsinvarianten (bestaand — **nooit omzeilen**)

| Invariant | Uitwerking | Code / test |
|---|---|---|
| noodstop | blokkeert nieuwe grabs (pre-grab `reject EMERGENCY_STOP`), upgrades-imports (`defer`), backfill/herzoek, recovery-mutaties, encodes, downloadbewaking, auto-import, opruimen, blokkeren, profielen; lopende jobs stoppen bij hun volgende controlepunt | `emergency.py`, `api/system.py:78-108`, `hooks/service.py:268-269, 364-366`, `wanted/backfill.py:326-327`; tests `test_regels.py::test_noodstop_*` (15), `test_hooks.py::test_pre_grab_emergency_stop_suspended_shadow` |
| noodstop tijdens core-herstart | hookwacht weigert grabs zelf als core niet antwoordt en de noodstop aan staat | `hookguard.py`; tests `test_beveiliging.py::test_guard_holds_emergency_stop_while_core_is_down` |
| bibliotheek-schrijfvergrendeling | `library.write_enabled` alleen via bevestigde admin-knop (`/recovery/library-write`), standaard dicht | `recovery/api.py:731`, `docs/SECURITY.md` §2 |
| gevaarlijke instellingen admin-only | `quarantine.*`, `security.*`, adressen van sleutels/back-ups, auditretentie | `settings_guard.py:25-66` |
| integriteit niet uitschakelbaar | `integrity.*` vergrendeld | `docs/DATA_RECOVERY.md` §1 |
| encoder vervangt nooit zelf | handoff naar recovery (ADR-005); VMAF/SSIM fail-closed | `services/encoder/tests/test_kwaliteitsmeting.py::test_meting_heeft_een_time_out`, `::test_alle_vmaf_metingen_mislukt_dan_ssim` |
| AI alleen adviserend | standaard uit; budgetlimiet; nooit beslissend over bestanden | `ai/settings.py:59`, ADR-009 |
| geheimen | Fernet-opslag, redactie in logs/audit (`mgk_`, `X-Api-Key`, bearer) | `secrets_store.py:18-110` |
| container | geen privileged, geen Docker-socket; AIO start als root alleen voor mappen/rechten, daarna 568 | `aio.py:399-422` (GPU-groepen, demote naar 568), `deploy/aio/Dockerfile:49-52`; privileges/capabilities zet het TrueNAS-formulier |

---

## 11. CI, tests en E2E

| Workflow / suite | Wat | Trigger |
|---|---|---|
| `.github/workflows/ci.yml` | core (Py 3.12 + 3.14: ruff + pytest), encoder-unit (13 bestanden, ruff + mypy), web (lint, typecheck, build, Playwright-e2e met echte core); nightly: encoder-volledige suite in toolchain-image, forkbuilds + NUnit, upstream-sync-rapport | push/PR/nightly/`workflow_call` |
| `.github/workflows/release.yml` | tests → kandidaat-images → AIO → **rooktest** (`scripts/aio-smoke.py` op DB van eerdere versie) → publiceren (tags nooit overschreven, nooit `latest`) → tag | tag `v*` / `release/v*` |
| `.github/workflows/android.yml` | APK bouwen + unit-tests, APK aan release | push main, na release |
| `apps/core/tests` | 83 testbestanden, **1069 tests** | CI |
| `services/encoder/tests` | 45 testbestanden (CI-subset: 13) | CI-subset + nightly |
| `apps/web/e2e` | 28 Playwright-specs (project `desktop-1440`; enkele specs zetten 360/390/412) incl. axe in `mediaguard.spec.ts` | CI |
| `tests/integration/stack`, `forks`, `fault-injection`, `tests/contract/seerr` | docker-compose-suites (echte containers, fakes; Seerr 3.5.1 + Jellyfin echt) — **handmatig**, laatste resultaten in `docs/TEST_RESULTS.md` | handmatig |

### 11.1 Baseline in deze omgeving (2026-11-09, echt gedraaid)

| Commando | Resultaat |
|---|---|
| `cd apps/core && nice -n 10 ../../.venv/bin/python -m pytest -q -rfEs -p no:cacheprovider` (Python 3.12.3) | **1069 passed, 2 skipped**, 350 s. Skips: `test_ai_real_smoke.py:17/25` (geen `ANTHROPIC_API_KEY`/`OPENAI_API_KEY` — bewust, echte betaalde API) |
| `cd apps/core && ../../.venv/bin/ruff check .` | All checks passed |
| `cd services/encoder && ruff check app tests && mypy app` (eigen venv, Py 3.12) | All checks passed; mypy: no issues in 62 bestanden |
| `cd services/encoder && python -m pytest -q -rfEs -p no:cacheprovider <13 CI-bestanden>` | **124 passed, 1 skipped**, 34 s. Skip: `conftest.py:67` fixture `mg-dual-audio.mkv` ontbreekt (geen dovi_tool/mkvmerge in deze omgeving). CI draait dit op Py 3.14; hier 3.12 (`requires-python >=3.12`) |
| `cd apps/web && npm ci && npm run lint && npm run typecheck` (Node 22) | lint 0 fouten (25 s), typecheck 0 fouten (15 s) |
| `scripts/platform-v2-routes.py --probe` | §4.4 (Sim-getest, in-process) |

**Niet lokaal gedraaid (met reden):** web `build` + Playwright-e2e (zwaar op een gedeelde machine; groen in CI-run
`42587401041` op exact deze HEAD); encoder-volledige suite en forkbuilds (toolchain-image ± 10 min CPU, .NET-builds;
alleen nightly); stack-/fault-injection-/Seerr-contractsuites (meerdere containers, uren CPU); AIO-rooktest (image
trekken van GHCR geblokkeerd: blob-host 403 via proxy). Echte NAS/GPU/Seerr/Jellyfin: **NIET GEVALIDEERD**.

---

## 12. Images, tags en installatiewijze

| Onderwerp | Bevinding | Bron |
|---|---|---|
| Standaard Custom App (alles-in-één) | **ja**: `ghcr.io/lvos04/mediaguard-core:1.2.2-aio`, formulier zonder YAML, drie Host Paths `/config`, `/work`, `/data`, Host Network aan (of 5 poorten), optioneel GPU, geen verplichte variabelen | `README.md:112-148`, ADR-012 (`docs/DECISIONS.md:95-111`), `deploy/release-manifest.json` (`aio.install`) |
| YAML-installatie | `deploy/truenas/compose.yaml` ("Install via YAML", 7 containers + init) staat nog als **alternatief** in `README.md:150` en `docs/INSTALL_TRUENAS.md` §5/6.2/8.2 — **in strijd met contracten 1.1**; dezelfde compose wordt door de stacktests gebruikt | `deploy/truenas/compose.yaml:1-11`, `tests/integration/stack/run.sh:2-3` |
| Ontwikkeling | `deploy/dev/compose.dev.yaml` (overlay) | — |
| Tags | exact gepind, nooit `latest`, tags onveranderlijk | `release.yml:1-24`, `scripts/image-tag-exists.sh` |
| Base-images | **niet op digest**: `python:3.12-slim-bookworm` (`apps/core/Dockerfile:9,19`), `node:22-bookworm-slim` (`apps/web/Dockerfile:4`), `ubuntu:26.04` (`services/encoder/Dockerfile:19`), `dotnet/sdk:8.0.421`, `runtime-deps:8.0-bookworm-slim`, `node:20.11.1-bookworm` (`forks/*/Dockerfile:19-23`); AIO-ARG's standaard `:dev` (`deploy/aio/Dockerfile:11-14`, release geeft kandidaattags mee) | — |
| Proxy-CA in builds | optionele BuildKit-secret `ca_bundle` (niet hard in Dockerfile) | `deploy/aio/Dockerfile:31-34`, `apps/core/Dockerfile:15-16`, `services/encoder/Dockerfile:75-78` |
| Ontbreekt t.o.v. contract §10 | `deploy/truenas/CUSTOM_APP.md`, `deploy/manifest/mediaguard.manifest.yaml`, `.env.example` in de root (wel `deploy/truenas/env.example` voor compose), `docs/openapi/*.json`, `docs/adr/` (wel `docs/DECISIONS.md`), `VERSION`, `Makefile`, `release/digests.json`, SBOM | — |

## 13. Releasebestanden

`CHANGELOG.md`, `deploy/release-manifest.json` (versie, images/tags, upstream-SHA's, toolchain, schemaversies;
gelezen door `/api/v1/admin/version`), `.github/workflows/release.yml`, `scripts/aio-smoke.py`,
`scripts/make-aio-fixture.py`, `scripts/promote-image.sh`, `scripts/image-tag-exists.sh`,
`forks/*/UPSTREAM.md|PATCHES.md|sync-upstream.sh`, `docs/TEST_RESULTS.md`.

---

## Bijlage A — volledige routetabel (gegenereerd)

Gegenereerd met `.venv/bin/python scripts/platform-v2-routes.py` op HEAD `60b50ab` (code = tag `v1.2.2`).
Regel = eerste regel van de route-decorator.

| Methode | Pad | Min. rol | Bestand:regel |
|---|---|---|---|
| GET | `/api/v1/activity` | viewer | `apps/core/mediaguard/activity/api.py:499` |
| POST | `/api/v1/activity/downloads/{instance_id}/import` | operator | `apps/core/mediaguard/activity/manual_import.py:340` |
| GET | `/api/v1/admin/coverage` | viewer | `apps/core/mediaguard/maintenance/api.py:741` |
| POST | `/api/v1/admin/diagnostics/{component}` | operator | `apps/core/mediaguard/maintenance/api.py:666` |
| GET | `/api/v1/admin/health` | viewer | `apps/core/mediaguard/maintenance/api.py:600` |
| GET | `/api/v1/admin/support-bundle` | admin | `apps/core/mediaguard/maintenance/api.py:702` |
| GET | `/api/v1/admin/version` | viewer | `apps/core/mediaguard/maintenance/api.py:759` |
| POST | `/api/v1/ai/benchmark` | admin | `apps/core/mediaguard/ai/api.py:387` |
| GET | `/api/v1/ai/cases` | viewer | `apps/core/mediaguard/ai/api.py:273` |
| GET | `/api/v1/ai/cases/{case_id}` | viewer | `apps/core/mediaguard/ai/api.py:305` |
| POST | `/api/v1/ai/cases/{case_id}/analyze` | operator | `apps/core/mediaguard/ai/api.py:315` |
| POST | `/api/v1/ai/cases/{case_id}/approve` | operator | `apps/core/mediaguard/ai/api.py:345` |
| POST | `/api/v1/ai/cases/{case_id}/feedback` | operator | `apps/core/mediaguard/ai/api.py:337` |
| POST | `/api/v1/ai/cases/{case_id}/reject` | operator | `apps/core/mediaguard/ai/api.py:358` |
| GET | `/api/v1/ai/consent` | viewer | `apps/core/mediaguard/ai/api.py:248` |
| POST | `/api/v1/ai/consent` | admin | `apps/core/mediaguard/ai/api.py:258` |
| GET | `/api/v1/ai/evaluations` | viewer | `apps/core/mediaguard/ai/api.py:398` |
| GET | `/api/v1/ai/preview/{case_id}` | viewer | `apps/core/mediaguard/ai/api.py:366` |
| GET | `/api/v1/ai/providers` | viewer | `apps/core/mediaguard/ai/api.py:87` |
| GET | `/api/v1/ai/providers/{provider}/models` | viewer | `apps/core/mediaguard/ai/api.py:109` |
| POST | `/api/v1/ai/providers/{provider}/test` | admin | `apps/core/mediaguard/ai/api.py:120` |
| GET | `/api/v1/ai/settings` | viewer | `apps/core/mediaguard/ai/api.py:145` |
| PATCH | `/api/v1/ai/settings` | admin | `apps/core/mediaguard/ai/api.py:158` |
| GET | `/api/v1/ai/usage` | viewer | `apps/core/mediaguard/ai/api.py:267` |
| POST | `/api/v1/app/control` | operator | `apps/core/mediaguard/mobile.py:388` |
| POST | `/api/v1/app/paired` | operator | `apps/core/mediaguard/mobile.py:611` |
| GET | `/api/v1/app/pairing` | admin | `apps/core/mediaguard/mobile.py:539` |
| POST | `/api/v1/app/pairing/key` | admin | `apps/core/mediaguard/mobile.py:574` |
| GET | `/api/v1/app/recent` | viewer | `apps/core/mediaguard/mobile.py:357` |
| GET | `/api/v1/app/status` | viewer | `apps/core/mediaguard/mobile.py:206` |
| GET | `/api/v1/app/summary` | viewer | `apps/core/mediaguard/mobile.py:336` |
| GET | `/api/v1/attention` | viewer | `apps/core/mediaguard/attention.py:742` |
| POST | `/api/v1/attention/dismiss` | operator | `apps/core/mediaguard/attention.py:767` |
| GET | `/api/v1/attention/dismissed` | viewer | `apps/core/mediaguard/attention.py:788` |
| POST | `/api/v1/attention/dismissed/restore` | operator | `apps/core/mediaguard/attention.py:801` |
| POST | `/api/v1/attention/replace-failures/reset` | operator | `apps/core/mediaguard/attention.py:815` |
| POST | `/api/v1/audio/inspect` | viewer | `apps/core/mediaguard/audio/api.py:30` |
| GET | `/api/v1/audio/normalize/{code}` | viewer | `apps/core/mediaguard/audio/api.py:47` |
| GET | `/api/v1/audit` | viewer | `apps/core/mediaguard/api/system.py:111` |
| GET | `/api/v1/auth/api-keys` | admin | `apps/core/mediaguard/api/auth.py:334` |
| POST | `/api/v1/auth/api-keys` | admin | `apps/core/mediaguard/api/auth.py:342` |
| POST | `/api/v1/auth/api-keys/{key_id}/revoke` | admin | `apps/core/mediaguard/api/auth.py:352` |
| POST | `/api/v1/auth/login` | geen | `apps/core/mediaguard/api/auth.py:78` |
| POST | `/api/v1/auth/login/2fa` | geen | `apps/core/mediaguard/api/auth.py:125` |
| POST | `/api/v1/auth/logout` | geen | `apps/core/mediaguard/api/auth.py:138` |
| GET | `/api/v1/auth/me` | ingelogd | `apps/core/mediaguard/api/auth.py:151` |
| PUT | `/api/v1/auth/me/locale` | ingelogd | `apps/core/mediaguard/api/auth.py:208` |
| POST | `/api/v1/auth/me/password` | ingelogd | `apps/core/mediaguard/api/auth.py:290` |
| PUT | `/api/v1/auth/me/preferences` | ingelogd | `apps/core/mediaguard/api/auth.py:187` |
| POST | `/api/v1/auth/setup` | geen | `apps/core/mediaguard/api/auth.py:52` |
| GET | `/api/v1/auth/setup-status` | geen | `apps/core/mediaguard/api/auth.py:46` |
| GET | `/api/v1/auth/users` | admin | `apps/core/mediaguard/api/auth.py:216` |
| POST | `/api/v1/auth/users` | admin | `apps/core/mediaguard/api/auth.py:223` |
| PATCH | `/api/v1/auth/users/{user_id}` | admin | `apps/core/mediaguard/api/auth.py:242` |
| GET | `/api/v1/backups` | viewer | `apps/core/mediaguard/maintenance/api.py:260` |
| POST | `/api/v1/backups` | operator | `apps/core/mediaguard/maintenance/api.py:320` |
| GET | `/api/v1/backups/bundle` | viewer | `apps/core/mediaguard/maintenance/api.py:352` |
| POST | `/api/v1/backups/bundle` | admin | `apps/core/mediaguard/maintenance/api.py:380` |
| POST | `/api/v1/backups/bundle/setup` | admin | `apps/core/mediaguard/maintenance/api.py:364` |
| GET | `/api/v1/backups/bundle/{name}/download` | admin | `apps/core/mediaguard/maintenance/api.py:391` |
| GET | `/api/v1/backups/terugzetten` | viewer | `apps/core/mediaguard/maintenance/api.py:404` |
| POST | `/api/v1/backups/terugzetten/annuleren` | admin | `apps/core/mediaguard/maintenance/api.py:413` |
| GET | `/api/v1/backups/{name}/download` | admin | `apps/core/mediaguard/maintenance/api.py:426` |
| POST | `/api/v1/backups/{name}/restore` | admin | `apps/core/mediaguard/maintenance/api.py:442` |
| GET | `/api/v1/calendar` | viewer | `apps/core/mediaguard/library/api.py:383` |
| GET | `/api/v1/delivery/requests` | viewer | `apps/core/mediaguard/delivery/api.py:35` |
| GET | `/api/v1/delivery/requests/{item_id}` | viewer | `apps/core/mediaguard/delivery/api.py:42` |
| POST | `/api/v1/delivery/requests/{item_id}/retry` | operator | `apps/core/mediaguard/delivery/api.py:68` |
| POST | `/api/v1/delivery/requests/{item_id}/search` | operator | `apps/core/mediaguard/delivery/api.py:85` |
| POST | `/api/v1/delivery/requests/{item_id}/stop` | operator | `apps/core/mediaguard/delivery/api.py:51` |
| POST | `/api/v1/delivery/run` | operator | `apps/core/mediaguard/delivery/api.py:121` |
| GET | `/api/v1/docs` | admin | `apps/core/mediaguard/app.py:175` |
| POST | `/api/v1/dolby-vision/activate/{job_id}` | operator | `apps/core/mediaguard/dv/api.py:258` |
| GET | `/api/v1/dolby-vision/capabilities` | viewer | `apps/core/mediaguard/dv/api.py:42` |
| GET | `/api/v1/dolby-vision/playback-confirmations` | viewer | `apps/core/mediaguard/dv/api.py:218` |
| POST | `/api/v1/dolby-vision/playback-confirmations` | operator | `apps/core/mediaguard/dv/api.py:224` |
| DELETE | `/api/v1/dolby-vision/playback-confirmations/{conf_id}` | operator | `apps/core/mediaguard/dv/api.py:243` |
| GET | `/api/v1/dolby-vision/policies` | viewer | `apps/core/mediaguard/dv/api.py:63` |
| PATCH | `/api/v1/dolby-vision/policies` | operator | `apps/core/mediaguard/dv/api.py:101` |
| POST | `/api/v1/dolby-vision/restore/{job_id}` | operator | `apps/core/mediaguard/dv/api.py:288` |
| POST | `/api/v1/dolby-vision/test-encode` | operator | `apps/core/mediaguard/dv/api.py:149` |
| GET | `/api/v1/dolby-vision/tests/{test_id}` | viewer | `apps/core/mediaguard/dv/api.py:187` |
| GET | `/api/v1/downloads/cleanup` | viewer | `apps/core/mediaguard/downloads/janitor.py:674` |
| POST | `/api/v1/downloads/cleanup/run` | operator | `apps/core/mediaguard/downloads/janitor.py:707` |
| POST | `/api/v1/downloads/cleanup/trash/{trash_id}/restore` | admin | `apps/core/mediaguard/downloads/janitor.py:715` |
| GET | `/api/v1/downloads/failures` | viewer | `apps/core/mediaguard/downloads/failures.py:537` |
| GET | `/api/v1/downloads/health` | viewer | `apps/core/mediaguard/downloads/failures.py:518` |
| GET | `/api/v1/downloads/recovery` | viewer | `apps/core/mediaguard/downloads/recovery.py:376` |
| POST | `/api/v1/downloads/recovery/run` | operator | `apps/core/mediaguard/downloads/recovery.py:383` |
| WS | `/api/v1/encoder/ws` | geen | `apps/core/mediaguard/encoding/gateway.py:192` |
| DELETE,GET,PATCH,POST,PUT | `/api/v1/encoder/{path:path}` | ingelogd | `apps/core/mediaguard/encoding/gateway.py:75` |
| GET | `/api/v1/encoding/autopilot` | viewer | `apps/core/mediaguard/encoding/autopilot.py:534` |
| POST | `/api/v1/encoding/autopilot/run` | operator | `apps/core/mediaguard/encoding/autopilot.py:550` |
| GET | `/api/v1/encoding/benchmarks` | viewer | `apps/core/mediaguard/encoding/api.py:422` |
| POST | `/api/v1/encoding/benchmarks` | operator | `apps/core/mediaguard/encoding/api.py:432` |
| GET | `/api/v1/encoding/capabilities` | viewer | `apps/core/mediaguard/encoding/api.py:158` |
| GET | `/api/v1/encoding/engine-states` | viewer | `apps/core/mediaguard/encoding/api.py:250` |
| GET | `/api/v1/encoding/forecast` | viewer | `apps/core/mediaguard/encoding/api.py:212` |
| GET | `/api/v1/encoding/jobs` | viewer | `apps/core/mediaguard/encoding/api.py:196` |
| POST | `/api/v1/encoding/jobs` | operator | `apps/core/mediaguard/encoding/api.py:304` |
| GET | `/api/v1/encoding/jobs/resolve/{job_id}` | viewer | `apps/core/mediaguard/encoding/api.py:260` |
| GET | `/api/v1/encoding/jobs/{job_id}` | viewer | `apps/core/mediaguard/encoding/api.py:272` |
| POST | `/api/v1/encoding/jobs/{job_id}/activate` | operator | `apps/core/mediaguard/encoding/api.py:333` |
| POST | `/api/v1/encoding/jobs/{job_id}/cancel` | operator | `apps/core/mediaguard/encoding/api.py:317` |
| POST | `/api/v1/encoding/plan` | operator | `apps/core/mediaguard/encoding/api.py:364` |
| GET | `/api/v1/encoding/plan/{plan_id}` | viewer | `apps/core/mediaguard/encoding/api.py:370` |
| POST | `/api/v1/encoding/plan/{plan_id}/execute` | operator | `apps/core/mediaguard/encoding/api.py:393` |
| POST | `/api/v1/encoding/plan/{plan_id}/simulate` | viewer | `apps/core/mediaguard/encoding/api.py:381` |
| GET | `/api/v1/encoding/resources` | viewer | `apps/core/mediaguard/encoding/resources.py:130` |
| GET | `/api/v1/encoding/storage` | viewer | `apps/core/mediaguard/encoding/api.py:168` |
| POST | `/api/v1/encoding/storage/push` | operator | `apps/core/mediaguard/encoding/api.py:183` |
| POST | `/api/v1/encoding/sync` | operator | `apps/core/mediaguard/encoding/api.py:348` |
| GET | `/api/v1/events/recent` | viewer | `apps/core/mediaguard/api/events.py:52` |
| GET | `/api/v1/events/stream` | viewer | `apps/core/mediaguard/api/events.py:19` |
| GET | `/api/v1/health` | geen | `apps/core/mediaguard/api/system.py:19` |
| GET | `/api/v1/health/errors` | viewer | `apps/core/mediaguard/health_summary.py:322` |
| GET | `/api/v1/health/summary` | viewer | `apps/core/mediaguard/health_summary.py:215` |
| POST | `/api/v1/hooks/arr-webhook/{instance_id}` | geen | `apps/core/mediaguard/hooks/api.py:105` |
| POST | `/api/v1/hooks/pre-grab` | geen | `apps/core/mediaguard/hooks/api.py:74` |
| POST | `/api/v1/hooks/pre-import` | geen | `apps/core/mediaguard/hooks/api.py:94` |
| POST | `/api/v1/identity/anime-mappings/manual` | operator | `apps/core/mediaguard/identity/api.py:142` |
| POST | `/api/v1/identity/anime-mappings/thexem/{tvdb_id}/refresh` | operator | `apps/core/mediaguard/identity/api.py:129` |
| POST | `/api/v1/identity/parse` | viewer | `apps/core/mediaguard/identity/api.py:26` |
| POST | `/api/v1/identity/{instance_id}/{kind}/{provider_id}/decision` | operator | `apps/core/mediaguard/identity/api.py:92` |
| GET | `/api/v1/identity/{instance_id}/{kind}/{provider_id}/evidence` | viewer | `apps/core/mediaguard/identity/api.py:53` |
| POST | `/api/v1/identity/{instance_id}/{kind}/{provider_id}/verify` | operator | `apps/core/mediaguard/identity/api.py:68` |
| GET | `/api/v1/instances` | viewer | `apps/core/mediaguard/instances/api.py:140` |
| POST | `/api/v1/instances` | admin | `apps/core/mediaguard/instances/api.py:177` |
| GET | `/api/v1/instances/{instance_id}` | viewer | `apps/core/mediaguard/instances/api.py:197` |
| PATCH | `/api/v1/instances/{instance_id}` | operator | `apps/core/mediaguard/instances/api.py:224` |
| POST | `/api/v1/instances/{instance_id}/backfill` | operator | `apps/core/mediaguard/instances/api.py:343` |
| GET | `/api/v1/instances/{instance_id}/backups` | viewer | `apps/core/mediaguard/instances/manage.py:928` |
| POST | `/api/v1/instances/{instance_id}/backups` | operator | `apps/core/mediaguard/instances/manage.py:935` |
| POST | `/api/v1/instances/{instance_id}/commands` | operator | `apps/core/mediaguard/instances/manage.py:447` |
| GET | `/api/v1/instances/{instance_id}/commands/{command_id}` | viewer | `apps/core/mediaguard/instances/manage.py:466` |
| GET | `/api/v1/instances/{instance_id}/custom-formats` | viewer | `apps/core/mediaguard/instances/manage.py:763` |
| GET | `/api/v1/instances/{instance_id}/download-clients` | viewer | `apps/core/mediaguard/instances/manage.py:565` |
| POST | `/api/v1/instances/{instance_id}/download-clients` | operator | `apps/core/mediaguard/instances/manage.py:589` |
| GET | `/api/v1/instances/{instance_id}/download-clients/schema` | viewer | `apps/core/mediaguard/instances/manage.py:572` |
| GET | `/api/v1/instances/{instance_id}/download-clients/status` | viewer | `apps/core/mediaguard/instances/manage.py:578` |
| POST | `/api/v1/instances/{instance_id}/download-clients/test` | operator | `apps/core/mediaguard/instances/manage.py:632` |
| POST | `/api/v1/instances/{instance_id}/download-clients/test-all` | operator | `apps/core/mediaguard/instances/manage.py:626` |
| DELETE | `/api/v1/instances/{instance_id}/download-clients/{item_id}` | operator | `apps/core/mediaguard/instances/manage.py:614` |
| PUT | `/api/v1/instances/{instance_id}/download-clients/{item_id}` | operator | `apps/core/mediaguard/instances/manage.py:601` |
| PATCH | `/api/v1/instances/{instance_id}/download-clients/{item_id}/enable` | operator | `apps/core/mediaguard/instances/manage.py:662` |
| POST | `/api/v1/instances/{instance_id}/download-clients/{item_id}/test` | operator | `apps/core/mediaguard/instances/manage.py:647` |
| PUT | `/api/v1/instances/{instance_id}/episodes/monitor` | operator | `apps/core/mediaguard/instances/manage.py:260` |
| GET | `/api/v1/instances/{instance_id}/health` | viewer | `apps/core/mediaguard/instances/manage.py:921` |
| GET | `/api/v1/instances/{instance_id}/history` | viewer | `apps/core/mediaguard/instances/manage.py:882` |
| GET | `/api/v1/instances/{instance_id}/import-lists` | viewer | `apps/core/mediaguard/instances/manage.py:565` |
| POST | `/api/v1/instances/{instance_id}/import-lists` | operator | `apps/core/mediaguard/instances/manage.py:589` |
| GET | `/api/v1/instances/{instance_id}/import-lists/schema` | viewer | `apps/core/mediaguard/instances/manage.py:572` |
| GET | `/api/v1/instances/{instance_id}/import-lists/status` | viewer | `apps/core/mediaguard/instances/manage.py:578` |
| POST | `/api/v1/instances/{instance_id}/import-lists/test` | operator | `apps/core/mediaguard/instances/manage.py:632` |
| POST | `/api/v1/instances/{instance_id}/import-lists/test-all` | operator | `apps/core/mediaguard/instances/manage.py:626` |
| DELETE | `/api/v1/instances/{instance_id}/import-lists/{item_id}` | operator | `apps/core/mediaguard/instances/manage.py:614` |
| PUT | `/api/v1/instances/{instance_id}/import-lists/{item_id}` | operator | `apps/core/mediaguard/instances/manage.py:601` |
| PATCH | `/api/v1/instances/{instance_id}/import-lists/{item_id}/enable` | operator | `apps/core/mediaguard/instances/manage.py:662` |
| POST | `/api/v1/instances/{instance_id}/import-lists/{item_id}/test` | operator | `apps/core/mediaguard/instances/manage.py:647` |
| GET | `/api/v1/instances/{instance_id}/indexers` | viewer | `apps/core/mediaguard/instances/manage.py:565` |
| POST | `/api/v1/instances/{instance_id}/indexers` | operator | `apps/core/mediaguard/instances/manage.py:589` |
| GET | `/api/v1/instances/{instance_id}/indexers/schema` | viewer | `apps/core/mediaguard/instances/manage.py:572` |
| GET | `/api/v1/instances/{instance_id}/indexers/status` | viewer | `apps/core/mediaguard/instances/manage.py:578` |
| POST | `/api/v1/instances/{instance_id}/indexers/test` | operator | `apps/core/mediaguard/instances/manage.py:632` |
| POST | `/api/v1/instances/{instance_id}/indexers/test-all` | operator | `apps/core/mediaguard/instances/manage.py:626` |
| DELETE | `/api/v1/instances/{instance_id}/indexers/{item_id}` | operator | `apps/core/mediaguard/instances/manage.py:614` |
| PUT | `/api/v1/instances/{instance_id}/indexers/{item_id}` | operator | `apps/core/mediaguard/instances/manage.py:601` |
| PATCH | `/api/v1/instances/{instance_id}/indexers/{item_id}/enable` | operator | `apps/core/mediaguard/instances/manage.py:662` |
| POST | `/api/v1/instances/{instance_id}/indexers/{item_id}/test` | operator | `apps/core/mediaguard/instances/manage.py:647` |
| GET | `/api/v1/instances/{instance_id}/language-profiles` | viewer | `apps/core/mediaguard/instances/manage.py:756` |
| GET | `/api/v1/instances/{instance_id}/library` | viewer | `apps/core/mediaguard/instances/api.py:311` |
| GET | `/api/v1/instances/{instance_id}/movies` | viewer | `apps/core/mediaguard/instances/manage.py:280` |
| POST | `/api/v1/instances/{instance_id}/movies` | operator | `apps/core/mediaguard/instances/manage.py:307` |
| GET | `/api/v1/instances/{instance_id}/movies/lookup` | viewer | `apps/core/mediaguard/instances/manage.py:287` |
| DELETE | `/api/v1/instances/{instance_id}/movies/{movie_id}` | operator | `apps/core/mediaguard/instances/manage.py:377` |
| GET | `/api/v1/instances/{instance_id}/movies/{movie_id}` | viewer | `apps/core/mediaguard/instances/manage.py:342` |
| PATCH | `/api/v1/instances/{instance_id}/movies/{movie_id}` | operator | `apps/core/mediaguard/instances/manage.py:360` |
| GET | `/api/v1/instances/{instance_id}/notifications` | viewer | `apps/core/mediaguard/instances/manage.py:707` |
| GET | `/api/v1/instances/{instance_id}/quality-profiles` | viewer | `apps/core/mediaguard/instances/manage.py:749` |
| GET | `/api/v1/instances/{instance_id}/queue` | viewer | `apps/core/mediaguard/instances/manage.py:862` |
| DELETE | `/api/v1/instances/{instance_id}/queue/{queue_id}` | operator | `apps/core/mediaguard/instances/manage.py:870` |
| GET | `/api/v1/instances/{instance_id}/releases` | operator | `apps/core/mediaguard/instances/manage.py:474` |
| POST | `/api/v1/instances/{instance_id}/releases` | operator | `apps/core/mediaguard/instances/manage.py:497` |
| GET | `/api/v1/instances/{instance_id}/remote-path-mappings` | viewer | `apps/core/mediaguard/instances/manage.py:812` |
| POST | `/api/v1/instances/{instance_id}/remote-path-mappings` | operator | `apps/core/mediaguard/instances/manage.py:825` |
| DELETE | `/api/v1/instances/{instance_id}/remote-path-mappings/{mapping_id}` | operator | `apps/core/mediaguard/instances/manage.py:850` |
| PUT | `/api/v1/instances/{instance_id}/remote-path-mappings/{mapping_id}` | operator | `apps/core/mediaguard/instances/manage.py:837` |
| POST | `/api/v1/instances/{instance_id}/resume` | operator | `apps/core/mediaguard/instances/api.py:285` |
| GET | `/api/v1/instances/{instance_id}/root-folders` | viewer | `apps/core/mediaguard/instances/manage.py:716` |
| POST | `/api/v1/instances/{instance_id}/root-folders` | operator | `apps/core/mediaguard/instances/manage.py:727` |
| DELETE | `/api/v1/instances/{instance_id}/root-folders/{folder_id}` | operator | `apps/core/mediaguard/instances/manage.py:738` |
| POST | `/api/v1/instances/{instance_id}/search` | operator | `apps/core/mediaguard/instances/manage.py:402` |
| GET | `/api/v1/instances/{instance_id}/series` | viewer | `apps/core/mediaguard/instances/manage.py:38` |
| POST | `/api/v1/instances/{instance_id}/series` | operator | `apps/core/mediaguard/instances/manage.py:107` |
| GET | `/api/v1/instances/{instance_id}/series/lookup` | viewer | `apps/core/mediaguard/instances/manage.py:45` |
| DELETE | `/api/v1/instances/{instance_id}/series/{series_id}` | operator | `apps/core/mediaguard/instances/manage.py:192` |
| GET | `/api/v1/instances/{instance_id}/series/{series_id}` | viewer | `apps/core/mediaguard/instances/manage.py:152` |
| PATCH | `/api/v1/instances/{instance_id}/series/{series_id}` | operator | `apps/core/mediaguard/instances/manage.py:175` |
| GET | `/api/v1/instances/{instance_id}/series/{series_id}/episodes` | viewer | `apps/core/mediaguard/instances/manage.py:247` |
| PUT | `/api/v1/instances/{instance_id}/series/{series_id}/seasons` | operator | `apps/core/mediaguard/instances/manage.py:230` |
| PUT | `/api/v1/instances/{instance_id}/series/{series_id}/seasons/{season_number}` | operator | `apps/core/mediaguard/instances/manage.py:213` |
| POST | `/api/v1/instances/{instance_id}/suspend` | operator | `apps/core/mediaguard/instances/api.py:273` |
| POST | `/api/v1/instances/{instance_id}/sync` | operator | `apps/core/mediaguard/instances/api.py:369` |
| GET | `/api/v1/instances/{instance_id}/system/status` | viewer | `apps/core/mediaguard/instances/manage.py:914` |
| GET | `/api/v1/instances/{instance_id}/tags` | viewer | `apps/core/mediaguard/instances/manage.py:770` |
| POST | `/api/v1/instances/{instance_id}/tags` | operator | `apps/core/mediaguard/instances/manage.py:781` |
| DELETE | `/api/v1/instances/{instance_id}/tags/{tag_id}` | operator | `apps/core/mediaguard/instances/manage.py:802` |
| PUT | `/api/v1/instances/{instance_id}/tags/{tag_id}` | operator | `apps/core/mediaguard/instances/manage.py:792` |
| POST | `/api/v1/instances/{instance_id}/test` | operator | `apps/core/mediaguard/instances/api.py:262` |
| GET | `/api/v1/instances/{instance_id}/upstream-calendar` | viewer | `apps/core/mediaguard/instances/manage.py:906` |
| GET | `/api/v1/instances/{instance_id}/upstream-wanted/{which}` | viewer | `apps/core/mediaguard/instances/manage.py:894` |
| GET | `/api/v1/instances/{instance_id}/wanted` | viewer | `apps/core/mediaguard/instances/api.py:324` |
| POST | `/api/v1/instances/{instance_id}/webhook` | admin | `apps/core/mediaguard/instances/api.py:299` |
| GET | `/api/v1/jobs` | viewer | `apps/core/mediaguard/api/system.py:134` |
| POST | `/api/v1/jobs/ignore-failed` | operator | `apps/core/mediaguard/api/system.py:215` |
| GET | `/api/v1/jobs/{job_id}` | viewer | `apps/core/mediaguard/api/system.py:155` |
| POST | `/api/v1/jobs/{job_id}/cancel` | operator | `apps/core/mediaguard/api/system.py:177` |
| POST | `/api/v1/jobs/{job_id}/ignore` | operator | `apps/core/mediaguard/api/system.py:203` |
| POST | `/api/v1/jobs/{job_id}/retry` | operator | `apps/core/mediaguard/api/system.py:187` |
| GET | `/api/v1/library` | viewer | `apps/core/mediaguard/library/api.py:143` |
| GET | `/api/v1/library-count-changes` | viewer | `apps/core/mediaguard/library/api.py:465` |
| GET | `/api/v1/library/kpis` | viewer | `apps/core/mediaguard/library/api.py:96` |
| POST | `/api/v1/library/sync` | operator | `apps/core/mediaguard/library/api.py:120` |
| GET | `/api/v1/library/sync-status` | viewer | `apps/core/mediaguard/library/api.py:106` |
| GET | `/api/v1/library/{item_id}` | viewer | `apps/core/mediaguard/library/api.py:158` |
| GET | `/api/v1/library/{item_id}/details` | viewer | `apps/core/mediaguard/library/api.py:341` |
| GET | `/api/v1/library/{item_id}/episodes/{episode_id}/details` | viewer | `apps/core/mediaguard/library/api.py:308` |
| PUT | `/api/v1/library/{item_id}/episodes/{episode_id}/monitor` | operator | `apps/core/mediaguard/library/extra.py:77` |
| GET | `/api/v1/library/{item_id}/files` | viewer | `apps/core/mediaguard/library/extra.py:173` |
| GET | `/api/v1/library/{item_id}/seasons` | viewer | `apps/core/mediaguard/library/api.py:367` |
| PUT | `/api/v1/library/{item_id}/seasons/{season_number}` | operator | `apps/core/mediaguard/library/extra.py:43` |
| GET | `/api/v1/library/{item_id}/upgrades` | viewer | `apps/core/mediaguard/library/extra.py:215` |
| POST | `/api/v1/mediaserver/config` | admin | `apps/core/mediaguard/mediaserver/api.py:165` |
| POST | `/api/v1/mediaserver/discover` | operator | `apps/core/mediaguard/mediaserver/api.py:199` |
| GET | `/api/v1/mediaserver/playback/{encoding_job_id}` | viewer | `apps/core/mediaguard/mediaserver/api.py:205` |
| GET | `/api/v1/mediaserver/status` | viewer | `apps/core/mediaguard/mediaserver/api.py:90` |
| POST | `/api/v1/mediaserver/subtitles/config` | admin | `apps/core/mediaguard/mediaserver/api.py:223` |
| GET | `/api/v1/mediaserver/subtitles/missing` | viewer | `apps/core/mediaguard/mediaserver/api.py:284` |
| POST | `/api/v1/mediaserver/subtitles/run` | operator | `apps/core/mediaguard/mediaserver/api.py:278` |
| POST | `/api/v1/mediaserver/subtitles/test` | operator | `apps/core/mediaguard/mediaserver/api.py:272` |
| POST | `/api/v1/mediaserver/test` | operator | `apps/core/mediaguard/mediaserver/api.py:193` |
| POST | `/api/v1/migration/av1/import` | admin | `apps/core/mediaguard/migration/api.py:872` |
| GET | `/api/v1/migration/av1/imports` | viewer | `apps/core/mediaguard/migration/api.py:902` |
| GET | `/api/v1/migration/av1/provenance` | viewer | `apps/core/mediaguard/migration/api.py:912` |
| POST | `/api/v1/migration/cutover` | admin | `apps/core/mediaguard/migration/api.py:801` |
| POST | `/api/v1/migration/dry-run` | operator | `apps/core/mediaguard/migration/api.py:728` |
| POST | `/api/v1/migration/import-preview` | operator | `apps/core/mediaguard/migration/api.py:595` |
| POST | `/api/v1/migration/instance-mapping/preview` | operator | `apps/core/mediaguard/migration/api.py:512` |
| POST | `/api/v1/migration/instance-mapping/verify` | operator | `apps/core/mediaguard/migration/api.py:668` |
| POST | `/api/v1/migration/preflight` | operator | `apps/core/mediaguard/migration/api.py:479` |
| GET | `/api/v1/migration/reports/{report_id}` | viewer | `apps/core/mediaguard/migration/api.py:464` |
| POST | `/api/v1/migration/rollback` | admin | `apps/core/mediaguard/migration/api.py:829` |
| GET | `/api/v1/migration/runs` | viewer | `apps/core/mediaguard/migration/api.py:376` |
| POST | `/api/v1/migration/runs` | operator | `apps/core/mediaguard/migration/api.py:383` |
| GET | `/api/v1/migration/runs/{run_id}` | viewer | `apps/core/mediaguard/migration/api.py:404` |
| POST | `/api/v1/migration/runs/{run_id}/accept-mismatches` | admin | `apps/core/mediaguard/migration/api.py:688` |
| POST | `/api/v1/migration/runs/{run_id}/activate-search` | admin | `apps/core/mediaguard/migration/api.py:807` |
| POST | `/api/v1/migration/runs/{run_id}/autopilot` | operator | `apps/core/mediaguard/migration/api.py:419` |
| POST | `/api/v1/migration/runs/{run_id}/backup` | admin | `apps/core/mediaguard/migration/api.py:492` |
| POST | `/api/v1/migration/runs/{run_id}/cancel` | admin | `apps/core/mediaguard/migration/api.py:449` |
| PUT | `/api/v1/migration/runs/{run_id}/mapping` | operator | `apps/core/mediaguard/migration/api.py:538` |
| POST | `/api/v1/migration/runs/{run_id}/mapping/confirm` | operator | `apps/core/mediaguard/migration/api.py:552` |
| PATCH | `/api/v1/migration/runs/{run_id}/options` | operator | `apps/core/mediaguard/migration/api.py:438` |
| POST | `/api/v1/migration/runs/{run_id}/path-preview` | operator | `apps/core/mediaguard/migration/api.py:573` |
| POST | `/api/v1/migration/runs/{run_id}/post-audit` | operator | `apps/core/mediaguard/migration/api.py:860` |
| POST | `/api/v1/migration/runs/{run_id}/readiness` | operator | `apps/core/mediaguard/migration/api.py:739` |
| POST | `/api/v1/migration/runs/{run_id}/scan` | operator | `apps/core/mediaguard/migration/api.py:717` |
| POST | `/api/v1/migration/runs/{run_id}/shadow-import` | admin | `apps/core/mediaguard/migration/api.py:652` |
| GET | `/api/v1/migration/sources` | viewer | `apps/core/mediaguard/migration/api.py:220` |
| POST | `/api/v1/migration/sources` | admin | `apps/core/mediaguard/migration/api.py:226` |
| POST | `/api/v1/migration/sources/upload` | admin | `apps/core/mediaguard/migration/api.py:259` |
| DELETE | `/api/v1/migration/sources/{source_id}` | admin | `apps/core/mediaguard/migration/api.py:349` |
| PATCH | `/api/v1/migration/sources/{source_id}` | admin | `apps/core/mediaguard/migration/api.py:307` |
| POST | `/api/v1/migration/sources/{source_id}/backups/upload` | admin | `apps/core/mediaguard/migration/api.py:278` |
| POST | `/api/v1/migration/sources/{source_id}/test` | operator | `apps/core/mediaguard/migration/api.py:340` |
| GET | `/api/v1/monitor` | viewer | `apps/core/mediaguard/monitor.py:469` |
| POST | `/api/v1/monitor/run` | operator | `apps/core/mediaguard/monitor.py:481` |
| GET | `/api/v1/notices` | viewer | `apps/core/mediaguard/notices.py:314` |
| POST | `/api/v1/notices/gezien` | operator | `apps/core/mediaguard/notices.py:321` |
| GET | `/api/v1/notifications` | viewer | `apps/core/mediaguard/api/system.py:234` |
| GET | `/api/v1/notifications/count` | viewer | `apps/core/mediaguard/api/system.py:270` |
| POST | `/api/v1/notifications/read-all` | viewer | `apps/core/mediaguard/api/system.py:306` |
| GET | `/api/v1/notifications/{notification_id}` | viewer | `apps/core/mediaguard/api/system.py:295` |
| POST | `/api/v1/notify/channels` | admin | `apps/core/mediaguard/notify/api.py:81` |
| DELETE | `/api/v1/notify/channels/{channel_id}` | admin | `apps/core/mediaguard/notify/api.py:117` |
| PATCH | `/api/v1/notify/channels/{channel_id}` | admin | `apps/core/mediaguard/notify/api.py:101` |
| POST | `/api/v1/notify/channels/{channel_id}/test` | admin | `apps/core/mediaguard/notify/api.py:129` |
| GET | `/api/v1/notify/digests` | viewer | `apps/core/mediaguard/notify/api.py:182` |
| POST | `/api/v1/notify/digests/send-now` | admin | `apps/core/mediaguard/notify/api.py:189` |
| GET | `/api/v1/notify/offsite` | admin | `apps/core/mediaguard/notify/api.py:204` |
| PUT | `/api/v1/notify/offsite` | admin | `apps/core/mediaguard/notify/api.py:230` |
| POST | `/api/v1/notify/offsite/run` | admin | `apps/core/mediaguard/notify/api.py:250` |
| GET | `/api/v1/notify/outbox` | admin | `apps/core/mediaguard/notify/api.py:175` |
| GET | `/api/v1/notify/overview` | admin | `apps/core/mediaguard/notify/api.py:45` |
| PUT | `/api/v1/notify/preferences` | admin | `apps/core/mediaguard/notify/api.py:151` |
| GET | `/api/v1/openapi.json` | admin | `apps/core/mediaguard/app.py:171` |
| GET | `/api/v1/optimization/dashboard` | viewer | `apps/core/mediaguard/optimization/api.py:229` |
| POST | `/api/v1/optimization/dry-run` | operator | `apps/core/mediaguard/optimization/api.py:54` |
| GET | `/api/v1/optimization/plan` | viewer | `apps/core/mediaguard/optimization/api.py:64` |
| GET | `/api/v1/optimization/plans` | viewer | `apps/core/mediaguard/optimization/api.py:93` |
| POST | `/api/v1/optimization/plans/{plan_id}/cancel` | operator | `apps/core/mediaguard/optimization/api.py:202` |
| POST | `/api/v1/optimization/plans/{plan_id}/confirm-downloads` | operator | `apps/core/mediaguard/optimization/api.py:144` |
| GET | `/api/v1/optimization/plans/{plan_id}/export` | viewer | `apps/core/mediaguard/optimization/api.py:217` |
| POST | `/api/v1/optimization/plans/{plan_id}/pause` | operator | `apps/core/mediaguard/optimization/api.py:165` |
| POST | `/api/v1/optimization/plans/{plan_id}/resume` | operator | `apps/core/mediaguard/optimization/api.py:182` |
| POST | `/api/v1/optimization/run` | operator | `apps/core/mediaguard/optimization/api.py:105` |
| GET | `/api/v1/ownership` | viewer | `apps/core/mediaguard/instances/api.py:388` |
| POST | `/api/v1/ownership` | operator | `apps/core/mediaguard/instances/api.py:433` |
| GET | `/api/v1/ownership/conflicts` | viewer | `apps/core/mediaguard/instances/api.py:394` |
| POST | `/api/v1/ownership/simulate` | viewer | `apps/core/mediaguard/instances/api.py:412` |
| DELETE | `/api/v1/ownership/{ownership_id}` | operator | `apps/core/mediaguard/instances/api.py:462` |
| GET | `/api/v1/policy/effective` | viewer | `apps/core/mediaguard/policy/api.py:119` |
| GET | `/api/v1/policy/hook-decisions` | viewer | `apps/core/mediaguard/policy/api.py:242` |
| POST | `/api/v1/policy/simulate` | viewer | `apps/core/mediaguard/policy/api.py:156` |
| GET | `/api/v1/policy/trash` | viewer | `apps/core/mediaguard/policy/api.py:198` |
| POST | `/api/v1/policy/trash/import` | operator | `apps/core/mediaguard/policy/api.py:206` |
| DELETE | `/api/v1/policy/trash/{cf_id}` | operator | `apps/core/mediaguard/policy/api.py:232` |
| GET | `/api/v1/profiles` | viewer | `apps/core/mediaguard/policy/api.py:38` |
| DELETE | `/api/v1/profiles/{profile_id}` | admin | `apps/core/mediaguard/policy/api.py:91` |
| GET | `/api/v1/profiles/{profile_id}` | viewer | `apps/core/mediaguard/policy/api.py:44` |
| PUT | `/api/v1/profiles/{profile_id}` | operator | `apps/core/mediaguard/policy/api.py:60` |
| GET | `/api/v1/profiles/{profile_id}/history` | viewer | `apps/core/mediaguard/policy/api.py:110` |
| POST | `/api/v1/profiles/{profile_id}/reset` | operator | `apps/core/mediaguard/policy/api.py:81` |
| POST | `/api/v1/provisioning/apply` | admin | `apps/core/mediaguard/provisioning/api.py:324` |
| GET | `/api/v1/provisioning/config` | viewer | `apps/core/mediaguard/provisioning/api.py:357` |
| PUT | `/api/v1/provisioning/config` | admin | `apps/core/mediaguard/provisioning/api.py:375` |
| POST | `/api/v1/provisioning/downloadclients/test` | operator | `apps/core/mediaguard/provisioning/api.py:492` |
| POST | `/api/v1/provisioning/drift-check` | operator | `apps/core/mediaguard/provisioning/api.py:344` |
| POST | `/api/v1/provisioning/indexers/test` | operator | `apps/core/mediaguard/provisioning/api.py:532` |
| GET | `/api/v1/provisioning/items` | viewer | `apps/core/mediaguard/provisioning/api.py:417` |
| PUT | `/api/v1/provisioning/items/ownership` | admin | `apps/core/mediaguard/provisioning/api.py:435` |
| GET | `/api/v1/provisioning/optimize-migrated` | viewer | `apps/core/mediaguard/provisioning/api.py:665` |
| POST | `/api/v1/provisioning/optimize-migrated` | admin | `apps/core/mediaguard/provisioning/api.py:680` |
| POST | `/api/v1/provisioning/optimize-migrated/cleanup` | admin | `apps/core/mediaguard/provisioning/api.py:694` |
| POST | `/api/v1/provisioning/optimize-migrated/rollback` | admin | `apps/core/mediaguard/provisioning/api.py:704` |
| POST | `/api/v1/provisioning/paths/validate` | operator | `apps/core/mediaguard/provisioning/api.py:609` |
| POST | `/api/v1/provisioning/plan` | operator | `apps/core/mediaguard/provisioning/api.py:301` |
| POST | `/api/v1/provisioning/prowlarr/test` | operator | `apps/core/mediaguard/provisioning/api.py:574` |
| POST | `/api/v1/provisioning/rename/apply` | admin | `apps/core/mediaguard/provisioning/api.py:743` |
| POST | `/api/v1/provisioning/rename/preview` | operator | `apps/core/mediaguard/provisioning/api.py:721` |
| GET | `/api/v1/provisioning/runs` | viewer | `apps/core/mediaguard/provisioning/api.py:350` |
| POST | `/api/v1/provisioning/seerr/apply` | admin | `apps/core/mediaguard/provisioning/api.py:644` |
| GET | `/api/v1/provisioning/status` | viewer | `apps/core/mediaguard/provisioning/api.py:244` |
| GET | `/api/v1/quarantine` | viewer | `apps/core/mediaguard/recovery/api.py:409` |
| POST | `/api/v1/quarantine/bulk-delete` | admin | `apps/core/mediaguard/recovery/api.py:646` |
| GET | `/api/v1/quarantine/orphans` | viewer | `apps/core/mediaguard/recovery/api.py:457` |
| POST | `/api/v1/quarantine/purge` | admin | `apps/core/mediaguard/recovery/api.py:671` |
| GET | `/api/v1/quarantine/usage` | viewer | `apps/core/mediaguard/recovery/api.py:465` |
| POST | `/api/v1/quarantine/use-quarantine` | operator | `apps/core/mediaguard/recovery/api.py:570` |
| DELETE | `/api/v1/quarantine/{item_id}` | admin | `apps/core/mediaguard/recovery/api.py:622` |
| GET | `/api/v1/quarantine/{item_id}` | viewer | `apps/core/mediaguard/recovery/api.py:547` |
| POST | `/api/v1/quarantine/{item_id}/pin` | operator | `apps/core/mediaguard/recovery/api.py:610` |
| POST | `/api/v1/quarantine/{item_id}/release-check` | operator | `apps/core/mediaguard/recovery/api.py:535` |
| POST | `/api/v1/quarantine/{item_id}/restore` | operator | `apps/core/mediaguard/recovery/api.py:590` |
| POST | `/api/v1/recovery/library-write` | admin | `apps/core/mediaguard/recovery/api.py:731` |
| GET | `/api/v1/recovery/readiness` | viewer | `apps/core/mediaguard/recovery/api.py:748` |
| GET | `/api/v1/recovery/transactions` | viewer | `apps/core/mediaguard/recovery/api.py:699` |
| GET | `/api/v1/recovery/transactions/{txn_id}` | viewer | `apps/core/mediaguard/recovery/api.py:712` |
| POST | `/api/v1/recovery/truenas/test` | admin | `apps/core/mediaguard/recovery/api.py:754` |
| GET | `/api/v1/routing/anime/actions` | viewer | `apps/core/mediaguard/routing/api.py:93` |
| POST | `/api/v1/routing/anime/actions/{action_id}/approve` | operator | `apps/core/mediaguard/routing/api.py:116` |
| POST | `/api/v1/routing/anime/actions/{action_id}/keep` | operator | `apps/core/mediaguard/routing/api.py:127` |
| POST | `/api/v1/routing/anime/actions/{action_id}/undo` | operator | `apps/core/mediaguard/routing/api.py:141` |
| POST | `/api/v1/routing/anime/evaluate` | operator | `apps/core/mediaguard/routing/api.py:106` |
| GET | `/api/v1/security/logins` | ingelogd | `apps/core/mediaguard/security/api.py:459` |
| GET | `/api/v1/security/me` | ingelogd | `apps/core/mediaguard/security/api.py:273` |
| PUT | `/api/v1/security/policy` | ingelogd | `apps/core/mediaguard/security/api.py:382` |
| GET | `/api/v1/security/sessions` | ingelogd | `apps/core/mediaguard/security/api.py:402` |
| POST | `/api/v1/security/sessions/revoke-others` | ingelogd | `apps/core/mediaguard/security/api.py:447` |
| DELETE | `/api/v1/security/sessions/{session_id}` | ingelogd | `apps/core/mediaguard/security/api.py:429` |
| POST | `/api/v1/security/totp/disable` | ingelogd | `apps/core/mediaguard/security/api.py:334` |
| POST | `/api/v1/security/totp/enable` | ingelogd | `apps/core/mediaguard/security/api.py:306` |
| POST | `/api/v1/security/totp/recovery-codes` | ingelogd | `apps/core/mediaguard/security/api.py:353` |
| POST | `/api/v1/security/totp/setup` | ingelogd | `apps/core/mediaguard/security/api.py:290` |
| POST | `/api/v1/security/users/{user_id}/totp/reset` | ingelogd | `apps/core/mediaguard/security/api.py:365` |
| POST | `/api/v1/seerr/cutover` | admin | `apps/core/mediaguard/seerr/api.py:205` |
| POST | `/api/v1/seerr/cutover/rollback` | admin | `apps/core/mediaguard/seerr/api.py:225` |
| GET | `/api/v1/seerr/discover/{kind}` | viewer | `apps/core/mediaguard/seerr/discover.py:98` |
| GET | `/api/v1/seerr/mapping` | viewer | `apps/core/mediaguard/seerr/api.py:153` |
| GET | `/api/v1/seerr/media/{media_type}/{tmdb_id}` | viewer | `apps/core/mediaguard/seerr/discover.py:107` |
| GET | `/api/v1/seerr/my-requests` | viewer | `apps/core/mediaguard/seerr/discover.py:207` |
| POST | `/api/v1/seerr/request` | operator | `apps/core/mediaguard/seerr/discover.py:151` |
| GET | `/api/v1/seerr/requests` | viewer | `apps/core/mediaguard/seerr/api.py:233` |
| POST | `/api/v1/seerr/requests/sync` | operator | `apps/core/mediaguard/seerr/api.py:256` |
| POST | `/api/v1/seerr/route-test` | operator | `apps/core/mediaguard/seerr/api.py:147` |
| GET | `/api/v1/seerr/routing` | viewer | `apps/core/mediaguard/seerr/api.py:160` |
| PUT | `/api/v1/seerr/routing/{instance_id}` | operator | `apps/core/mediaguard/seerr/api.py:178` |
| GET | `/api/v1/seerr/search` | viewer | `apps/core/mediaguard/seerr/discover.py:89` |
| GET | `/api/v1/seerr/settings` | viewer | `apps/core/mediaguard/seerr/api.py:91` |
| PUT | `/api/v1/seerr/settings` | admin | `apps/core/mediaguard/seerr/api.py:105` |
| POST | `/api/v1/seerr/test` | operator | `apps/core/mediaguard/seerr/api.py:132` |
| GET | `/api/v1/settings/effective` | viewer | `apps/core/mediaguard/api/settings.py:69` |
| GET | `/api/v1/settings/overrides` | viewer | `apps/core/mediaguard/api/settings.py:94` |
| GET | `/api/v1/settings/safety` | viewer | `apps/core/mediaguard/api/settings.py:299` |
| POST | `/api/v1/settings/safety/restore` | operator | `apps/core/mediaguard/api/settings.py:312` |
| GET | `/api/v1/settings/schema` | viewer | `apps/core/mediaguard/api/settings.py:45` |
| PATCH | `/api/v1/settings/scoped` | operator | `apps/core/mediaguard/api/settings.py:118` |
| GET | `/api/v1/settings/search` | viewer | `apps/core/mediaguard/api/settings.py:192` |
| GET | `/api/v1/settings/secrets` | admin | `apps/core/mediaguard/api/settings.py:215` |
| GET | `/api/v1/settings/secrets/health` | viewer | `apps/core/mediaguard/api/settings.py:229` |
| DELETE | `/api/v1/settings/secrets/{name}` | admin | `apps/core/mediaguard/api/settings.py:252` |
| PUT | `/api/v1/settings/secrets/{name}` | admin | `apps/core/mediaguard/api/settings.py:241` |
| POST | `/api/v1/settings/simulate` | viewer | `apps/core/mediaguard/api/settings.py:168` |
| GET | `/api/v1/status` | viewer | `apps/core/mediaguard/api/system.py:25` |
| GET | `/api/v1/storage/forecast` | viewer | `apps/core/mediaguard/storage_forecast.py:197` |
| GET | `/api/v1/storage/ledger` | viewer | `apps/core/mediaguard/storage_ledger.py:751` |
| GET | `/api/v1/storage/ledger/diagnose` | viewer | `apps/core/mediaguard/storage_ledger.py:759` |
| POST | `/api/v1/storage/ledger/refresh` | operator | `apps/core/mediaguard/storage_ledger.py:771` |
| GET | `/api/v1/storage/probe` | viewer | `apps/core/mediaguard/recovery/api.py:346` |
| POST | `/api/v1/storage/probe` | operator | `apps/core/mediaguard/recovery/api.py:353` |
| GET | `/api/v1/storage/twins` | viewer | `apps/core/mediaguard/storage_typos.py:211` |
| POST | `/api/v1/storage/twins/{instance_id}/{root_folder_id}/remove` | operator | `apps/core/mediaguard/storage_typos.py:221` |
| GET | `/api/v1/storage/typos` | viewer | `apps/core/mediaguard/storage_typos.py:124` |
| POST | `/api/v1/storage/typos/{instance_id}/{root_folder_id}/fix` | operator | `apps/core/mediaguard/storage_typos.py:135` |
| POST | `/api/v1/system/emergency-stop` | operator | `apps/core/mediaguard/api/system.py:78` |
| GET | `/api/v1/system/update` | viewer | `apps/core/mediaguard/updates.py:270` |
| POST | `/api/v1/system/update/check` | operator | `apps/core/mediaguard/updates.py:276` |
| POST | `/api/v1/system/update/dismiss` | operator | `apps/core/mediaguard/updates.py:292` |
| GET | `/api/v1/wanted` | viewer | `apps/core/mediaguard/wanted/api.py:229` |
| POST | `/api/v1/wanted/actions` | operator | `apps/core/mediaguard/wanted/api.py:316` |
| POST | `/api/v1/wanted/backfill-plan` | viewer | `apps/core/mediaguard/wanted/api.py:267` |
| GET | `/api/v1/wanted/backfill/quota` | viewer | `apps/core/mediaguard/wanted/api.py:301` |
| GET | `/api/v1/wanted/backfill/runs` | viewer | `apps/core/mediaguard/wanted/api.py:286` |
| GET | `/api/v1/wanted/kpis` | viewer | `apps/core/mediaguard/wanted/api.py:248` |
| GET | `/api/v1/wanted/reasons` | viewer | `apps/core/mediaguard/wanted/api.py:257` |
| GET | `/api/v1/wanted/{wanted_id}` | viewer | `apps/core/mediaguard/wanted/api.py:354` |
| GET | `/api/v1/wanted/{wanted_id}/diagnosis` | viewer | `apps/core/mediaguard/wanted/api.py:364` |
| POST | `/api/v1/wanted/{wanted_id}/ignore` | operator | `apps/core/mediaguard/wanted/api.py:451` |
| POST | `/api/v1/wanted/{wanted_id}/keep-monitoring` | operator | `apps/core/mediaguard/wanted/api.py:464` |
| POST | `/api/v1/wanted/{wanted_id}/retry-relaxed` | operator | `apps/core/mediaguard/wanted/api.py:418` |
| POST | `/api/v1/wanted/{wanted_id}/search` | operator | `apps/core/mediaguard/wanted/api.py:394` |
| POST | `/api/v1/wanted/{wanted_id}/unignore` | operator | `apps/core/mediaguard/wanted/api.py:458` |
