# Beveiliging — MediaGuard Ultimate

Technische referentie: dreigingsmodel, authenticatie, geheimen, containerisolatie,
schrijfrechten, redactie, AI-dataminimalisatie en supply chain. Alles hieronder verwijst naar
code of configuratie in deze repository; waar iets **niet** getest is staat dat erbij.

## 1. Dreigingsmodel

**Wat we beschermen (in volgorde van belang)**

1. De mediabibliotheek: geen verlies, geen stille vervanging door een slechtere of verkeerde
   versie (G2). Elke vervanging is herstelbaar (quarantaine + journaal).
2. Configuratie en historie van de vier Sonarr/Radarr-instanties (monitorvlaggen, profielen,
   indexer-credentials).
3. Geheimen: API-sleutels van de forks, hook-sleutels, interne token, AI-providersleutels,
   wachtwoorden van indexers/downloadclients.
4. Beschikbaarheid: downloads en imports blijven werken als MediaGuard zelf hapert
   (fail policy `open` in productie).

**Vertrouwensgrenzen**

| Grens | Wie | Maatregel |
|---|---|---|
| Browser ↔ web (poort 30080) | LAN-gebruikers | login via core, RBAC, CSRF, HttpOnly/SameSite=Strict-cookie |
| Internet ↔ web (Cloudflare-tunnel) | telefoon/browser onderweg | zelfde login; inlogrem met oplopende wachttijd, melding bij veel fouten, 2FA-aansporing, `Secure`-cookie via https, echt IP alleen van een vertrouwde proxy (§2d) |
| web ↔ core | intern compose-netwerk | web proxyt alleen `/api/v1/*`; core is de enige authenticatie-autoriteit (ADR-007) |
| core ↔ recovery ↔ encoder | intern netwerk | `X-MediaGuard-Token` (interne token, ≥ 24 tekens, constante-tijdvergelijking) |
| fork ↔ core (hooks/webhooks) | intern netwerk | `X-MediaGuard-Hook-Key` per instantie (eigen sleutel per fork), alleen geldig op `/api/v1/hooks/*`; in de alles-in-één via de hookwacht (§4a) |
| koppelingen ↔ core (bv. Media Source Optimizer) | LAN | `X-Api-Key` met een API-sleutel (rol viewer/operator, nooit admin), zie §2a |
| Seerr ↔ forks (poorten 38989/38990/37878/37879) | LAN | standaard Sonarr/Radarr-API-sleutel per instantie |
| core ↔ internet | indexers, metadata, optioneel AI-provider | alleen uitgaand; AI standaard uit en opt-in |

**Aanvallers waar we rekening mee houden:** een ander apparaat op het LAN; een kwaadaardige
releasenaam/indexerantwoord (prompt-injection, padmanipulatie); een gecompromitteerde
fork-container (mag niet bij andere instanties' sleutels of bij de core-DB); een fout in
MediaGuard zelf (mag nooit de bibliotheek beschadigen: read-only mounts, softwarevergrendeling,
verificatie vóór vervanging).

**Buiten scope:** een aanvaller met root op de TrueNAS-host of met toegang tot de
appdata-dataset (die kan alle sleutelbestanden lezen). Toegang via internet loopt alleen via
een tunnel met TLS (Cloudflare), nooit via een open poort; alleen de web-UI (30080) zit achter
die tunnel.

## 2. Authenticatie, RBAC en CSRF (`apps/core/mediaguard/auth.py`)

- **Wachtwoorden:** scrypt (n=2^14, r=8, p=1, 32 bytes) met per-gebruiker salt.
- **Eerste installatie:** `POST /api/v1/auth/setup` maakt alleen de eerste admin aan;
  daarna is die route dicht.
- **Sessie:** willekeurig token in cookie `mg_session` — `HttpOnly`, `SameSite=Strict`,
  `Secure` zodra de UI via HTTPS wordt geopend, 14 dagen geldig. In de database staat alleen
  de SHA-256 van het token.
- **CSRF:** double-submit; elke muterende request moet `X-CSRF-Token` sturen gelijk aan de
  sessie-CSRF (de UI haalt die uit `GET /api/v1/auth/me`).
- **Rollen:** `viewer` (lezen) < `operator` (acties: back-up, scan, diagnostiek, instellingen)
  < `admin` (gebruikers, restore, back-up downloaden, supportbundel). Elk endpoint declareert
  zijn minimumrol (`require_viewer/operator/admin`).
- **Machine-identiteiten:** interne token → principal `svc:internal` (admin), alleen vanaf
  een rechtstreekse interne verbinding (§4). Een hook-sleutel geldt **alleen** op de eigen
  hook-endpoints (`/api/v1/hooks/*`, eigen instantie); elders is hij geen login (401). Beide via
  `hmac.compare_digest`.
- **Alleen voor beheerders** (`apps/core/mediaguard/settings_guard.py`): via de gewone
  instellingen (`PATCH /api/v1/settings/scoped`) kunnen operators en API-sleutels (dus ook de
  app-sleutel) **niet** wijzigen: alles onder `quarantine.*` (direct verwijderen,
  bewaartermijn, vangnet, quotum, minimale bewaartijd, opruimen en de controle daarvoor; een
  quotum van 1 GB met 0 uur minimale bewaartijd verwijdert originelen net zo goed direct),
  alles onder `security.*` (2FA verplichten, inlogmeldingen, inlogrem, vertrouwde proxy's) en de
  adressen waar opgeslagen sleutels of back-ups naartoe gaan (`seerr.url`, `mediaserver.url`,
  `subtitles.bazarr_url`, `recovery.truenas_url`, `recovery.truenas_verify_tls`,
  `recovery.zfs_dataset`, `app.public_url`, `notify.base_url`, `backup.offsite.*`), het Hub-adres
  van de link 'Terug naar de Hub' (`system.hub_url`: anders stuurt een operator iedereen naar een
  andere server, ADR-0020), en hoe lang het
  auditlog en de aanmeldgeschiedenis bewaard blijven (`maintenance.retention_days_audit`,
  `maintenance.retention_days_history`: een operator wist zo geen sporen). Ook een test met een zelf opgegeven
  adres (Prowlarr, downloadclient, indexer) is alleen voor beheerders. Terug naar de veilige
  standaard (`/settings/safety/restore`) mag een operator wel. Het schema markeert deze
  instellingen (`admin_only`); de UI toont ze dan alleen-lezen.
- **Bibliotheek vrijgeven** (`library.write_enabled`) kan via de gewone instellingen door
  niemand worden gewijzigd (409), ook niet door een beheerder: alleen via de knop met
  bevestiging (`POST /api/v1/recovery/library-write`, admin, `confirm: true`). In Instellingen
  staat op die plek de status met de knop *Bibliotheek vrijgeven* of *Weer vergrendelen*.
- **Audit:** elke mutatie (instellingen, back-up/restore, cutover, quarantaine, push van de
  pauzebodem, AI-toestemming) gaat naar `audit_events` met actor, oude/nieuwe waarde en
  undo-hint.
- **API-sleutels:** header `X-Api-Key` → principal `apikey:<naam>` met de rol van de sleutel
  (viewer of operator), zie §2a.
- **Volgorde** in `principal_from_request`: interne token (alleen intern) → `X-Api-Key` →
  (dev-bypass) → sessiecookie. Een meegestuurde maar ongeldige `X-Api-Key` geeft altijd 401;
  er wordt dan niet teruggevallen op de cookie.
- **Dev-bypass:** `MG_DEV_AUTH_BYPASS=1` slaat login over; staat **niet** in de productie-compose
  en is alleen voor tests.

## 2a. API-sleutels (`apps/core/mediaguard/apikeys.py`)

Voor apps die met MediaGuard samenwerken, zoals Media Source Optimizer (zie
`docs/INTEGRATIONS.md`). Een beheerder maakt ze aan onder **Instellingen → Gevorderd →
API-sleutels**.

| Onderwerp | Gedrag |
|---|---|
| Header | `X-Api-Key: mgk_…` (`mgk_` + `secrets.token_urlsafe(32)`) |
| Rollen | `viewer` (alleen lezen) of `operator` (o.a. `POST /api/v1/wanted/{id}/search`). **Nooit `admin`**: de API weigert dat met 422 en de database heeft een `CHECK`. Daardoor kan een sleutel nooit bij gebruikers, geheimen of de sleutelroutes zelf. |
| CSRF | niet nodig (machine-principal); mensen houden cookie + `X-CSRF-Token` |
| Opslag | tabel `api_keys` (migratie `004_api_keys`): alleen `sha256(sleutel)` en de eerste 8 tekens als prefix; vergelijking met `hmac.compare_digest`. De sleutel wordt één keer getoond, direct na aanmaken. |
| Ongeldig | onbekend, ingetrokken of verlopen → `401 {"detail": "Ongeldige of ingetrokken API-sleutel"}`; intrekken werkt bij het eerstvolgende verzoek |
| Rate limit | 10 mislukte sleutelpogingen per IP per 5 minuten, daarna `429` met `Retry-After` (ook voor geldige sleutels vanaf dat adres) |
| `last_used_at` | hooguit één keer per minuut per sleutel bijgewerkt |
| Audit | `apikey.created`, `apikey.revoked`, `apikey.rejected` (alleen de prefix, nooit de sleutel); acties met een sleutel staan op naam van `apikey:<naam>` |
| `GET /api/v1/auth/me` | `{"username": "apikey:<naam>", "role": "<rol>", "csrf_token": null, "preferences": {}, …}` |

Beheer (alleen admin, met CSRF): `GET /api/v1/auth/api-keys` (nooit hash of sleutel),
`POST /api/v1/auth/api-keys {name, role, expires_in_days?}` (antwoord bevat `key`, één keer),
`POST /api/v1/auth/api-keys/{id}/revoke`. In `/api/v1/openapi.json` staat het schema als
`securitySchemes.apiKey` (type `apiKey`, in `header`, naam `X-Api-Key`).

## 2b. Tweestapsverificatie en sessies (`apps/core/mediaguard/security/`)

- **TOTP** volgens RFC 6238 (SHA-1, 6 cijfers, 30 s, venster ±1). Het geheim staat versleuteld
  in de secret store (`auth.totp.<user_id>`); een code wordt nooit twee keer geaccepteerd
  (`user_totp.last_counter`). Instellen gaat via een QR-code (otpauth-URI, server-side SVG via
  `segno`) en wordt pas actief na een geldige code.
- **Herstelcodes**: 10 eenmalige codes (`XXXXX-XXXXX`), alleen als SHA-256 opgeslagen, eenmalig
  getoond; opnieuw maken vereist een geldige code.
- **Inloggen in twee stappen**: na een juist wachtwoord krijgt de browser alleen een
  kortlevende uitdaging (5 min, max. 5 pogingen, alleen hash opgeslagen); de sessiecookie komt
  pas na `POST /api/v1/auth/login/2fa`.
- **Verplicht voor beheerders** (optioneel, `security.require_2fa_admins`): een beheerder zonder
  2FA krijgt 403 op alles behalve `/api/v1/security/*` en `/api/v1/auth/*`; de UI stuurt door naar
  Instellingen → Beveiliging. Een beheerder kan 2FA van een gebruiker resetten (telefoon kwijt);
  dat logt die gebruiker overal uit.
- **Sessies**: IP, apparaat en 'laatst actief' (hooguit één schrijfactie per minuut); elk
  apparaat is op afstand uit te loggen, ook 'alle andere apparaten'.
- **Aanmeldgeschiedenis** (`login_history`): gelukt/mislukt, methode, IP, apparaat. Inloggen
  vanaf een voor die gebruiker nieuw IP-adres geeft een beveiligingsmelding (instelbaar).
- API-sleutels en de interne token gebruiken geen 2FA en worden er niet door geblokkeerd.
- **Aansporing, geen plicht**: zolang een beheerder geen 2FA heeft, staat er een herinnering bij
  *Wacht op jou* (`security.twofa_off`, info) en op de pagina Beveiliging. Uit te zetten met
  `security.nudge_2fa`.
- **Inlogrem** (`apps/core/mediaguard/security/limiter.py`, telt uit `login_history`, dus ook na
  een herstart): na `security.login_max_attempts` (5) mislukte pogingen moet een adres wachten,
  30 s en daarna steeds twee keer zo lang tot `security.login_max_delay_minutes` (15 min) per
  poging (`429` met `Retry-After`). Hetzelfde per gebruiker, maar alleen voor adressen waarvan
  die gebruiker de laatste 90 dagen nooit is ingelogd: een aanval vanaf veel adressen remt, je
  eigen apparaten loggen gewoon in. Geldt voor het wachtwoord, de 2FA-code en het wijzigen van
  je eigen wachtwoord; de rem werkt vóór het wachtwoord wordt gecontroleerd. Bij het bereiken
  van de grens komt er een beveiligingsmelding (`security.notify_failed_logins`).
- **Wachtwoord wijzigen** logt alle andere sessies van die gebruiker uit (ook openstaande
  2FA-uitdagingen); de eigen sessie blijft. Zelf wijzigen: `POST /api/v1/auth/me/password`
  (huidig wachtwoord vereist, Instellingen → Beveiliging). Een beheerder via
  Gebruikers: idem voor die gebruiker.

## 2d. Echt IP-adres, https en de tunnel (`apps/core/mediaguard/security/net.py`)

Keten in de alles-in-één: browser → Cloudflare → cloudflared → web (`server.mjs`, 30080) → core
(`127.0.0.1:38100`).

- `server.mjs` zet zelf `X-MG-Peer` (het TCP-adres dat hij zag) en `X-MG-TLS`, en gooit die
  headers plus `X-MediaGuard-Token`, `X-MediaGuard-Hook-Key` en `X-MediaGuard-Gateway` van de
  client weg. `CF-Connecting-IP`, `X-Forwarded-For` en `X-Forwarded-Proto` gaan ongewijzigd
  door; core beslist of ze tellen.
- Core vertrouwt `X-MG-Peer` alleen van een interne TCP-verbinding (loopback; in de compose-
  opstelling het private netwerk). Uvicorn draait met `proxy_headers=False`.
- **Echt IP**: `CF-Connecting-IP` (anders het laatste adres uit `X-Forwarded-For`) telt alleen
  als `X-MG-Peer` een vertrouwde proxy is: loopback, het eigen LAN-adres van de NAS en
  `security.trusted_proxies` (standaard `127.0.0.1`, `::1`, `172.16.0.0/12` = Docker-netwerken
  waarin een lokale cloudflared draait). Anders telt het adres dat de web-server zag. Dat adres
  gebruiken de aanmeldgeschiedenis, de inlogrem, de rem op API-sleutels en de melding bij een
  nieuw adres.
- **`Secure`-cookie** zodra de browser https gebruikt: TLS naar de web-server, of
  `X-Forwarded-Proto: https` van een vertrouwde proxy (cloudflared zet die).
- **Koppel-QR van de app**: een publiek adres (tunnel-domein of publiek IP, zonder eigen poort)
  staat altijd als `https://` in de QR-code, ook als het als `http://` is ingevuld of het
  verzoek via cloudflared als http binnenkwam. LAN-adressen blijven `http://`. De Android-app
  doet hetzelfde bij het lezen van de code of het intypen van een adres.

## 2c. Meldingen buiten de browser en bundelkopie (`apps/core/mediaguard/notify/`)

- Kanaalgeheimen (tokens, webhook-adressen, SMTP-wachtwoord, WebDAV-/S3-sleutels) staan
  versleuteld in de secret store en verlaten de backend nooit; de API toont alleen of ze
  ingesteld zijn. Foutmeldingen van kanalen gaan door `redact()`.
- Het standaard ntfy-kanaal gebruikt een lang willekeurig onderwerp en staat **uit** tot de
  gebruiker zich heeft geabonneerd en het aanzet: zonder handeling gaat er niets naar buiten.
- De herstelbundel en het sleutelbestand zijn al versleuteld (alleen te openen met de
  herstelcode); kopieën buiten de NAS worden op SHA-256 gecontroleerd.

## 3. Geheimen

| Geheim | Waar | Bescherming |
|---|---|---|
| Interne token, fork-API-sleutels, hook-sleutels | `${MG_APPDATA}/secrets/{internal/token, <instantie>/apikey, <instantie>/hookkey}` | automatisch gegenereerd door `mediaguard-init` (`secrets.token_hex`), bestand 0640, map 0750, eigenaar 568; nooit overschreven (idempotent). Elke container koppelt **alleen zijn eigen** submap read-only (bv. een fork ziet alleen `secrets/<zijn-instantie>`). De gebruiker typt geen sleutels. |
| Indexer-/downloadclient-/AI-sleutels die je in de UI invult | core-DB, tabel `secrets` | Fernet (AES-128-CBC + HMAC-SHA256); sleutel `secret.key` op `/config` (0600). API-responses tonen alleen `is_set`/`readable`, nooit (een deel van) de waarde. |
| Encoder-integratiesleutels (Jellyfin/Radarr/Sonarr in de oude encoder-UI) | encoder-DB | Fernet met eigen sleutelbestand op de encoder-`/config` |

**Verkeerde `secret.key`** (vervangen, of database van een andere installatie teruggezet):
een geheim dat niet te ontsleutelen is, telt **niet** als ingesteld (`SecretStore.has` = leesbaar;
`exists` = staat er). *Wacht op jou* toont dan een fout (`security.secret_key_mismatch`) met het
aantal onleesbare sleutels, Instellingen → Geheimen markeert ze als *Onleesbaar*, en
`GET /api/v1/settings/secrets/health` geeft de aantallen. De Sonarr/Radarr-sleutels worden bij de
start vanzelf opnieuw uit `secrets/<instantie>/apikey` gezet. Tests met tijdelijke sleutels zetten
de versleutelde waarde ongewijzigd terug (een onleesbare sleutel verdwijnt nooit stil).

**Gevolgen:** wie `${MG_APPDATA}` leest, heeft alle sleutels — neem die dataset op in je
snapshot-/back-upbeleid, maar deel back-ups niet. Een DB-back-up zonder `secret.key` is voor
geheimen waardeloos (bewust). Roteren van fork-/hook-sleutels: verwijder het bestand onder
`secrets/<instantie>/` en herstart de app; init maakt een nieuwe en core neemt hem bij de start
over (het env/bestand is leidend, niet de UI).

## 4. Interne token

- Verplicht voor alle `/api/mediaguard/v1/*`-routes van de encoder; zonder geconfigureerde
  token (of korter dan 24 tekens) antwoordt de brug **503** (fail-closed, nooit open).
- In containermodus (`MG_CONTAINER_MODE=1`) beschermt dezelfde token ook de oude encoder-API
  (`/api/*`, `/ws`), via `LegacyApiTokenMiddleware` (`MG_PROTECT_LEGACY_API=1`).
- Core en recovery accepteren de token als machine-identiteit, **alleen** van een
  rechtstreekse interne verbinding: niet van buiten de container en nooit van een verzoek dat
  via de web-server binnenkwam (`X-MG-Peer`, `X-Forwarded-For`, `CF-Connecting-IP`, …). Web
  krijgt de token niet en gooit hem weg als een client hem meestuurt.
- De encoder heeft geen gepubliceerde poort; alleen core praat ermee.
- **Gateway naar de engine** (`/api/v1/encoder/*`, `apps/core/mediaguard/encoding/gateway.py`):
  alleen gewone paden (geen `.`/`..`-segmenten, lege segmenten, `\`, `%`-codes of stuurtekens;
  400) en nooit `/api/mediaguard/…` (403), ook niet na de padnormalisatie van httpx. De gateway
  zet `X-MediaGuard-Gateway: 1`; de brug van de engine weigert elk verzoek met die header
  (`services/encoder/app/mediaguard/auth.py`), ook met een geldige token.
- **API-documentatie** (`/api/v1/docs`, `/api/v1/openapi.json`) alleen voor een ingelogde
  beheerder; FastAPI's openbare `/docs`, `/redoc` en `/openapi.json` staan uit.

## 4a. Hookwacht: noodstop geldt ook als core herstart (`apps/core/mediaguard/hookguard.py`)

Sonarr/Radarr vragen vóór elke grab aan core of het mag. Antwoordt core niet (herstart, update,
migratie), dan past de fork zijn fail policy `open` toe. Goed voor gewone downloads, maar tijdens
een noodstop ging een grab dan ongecontroleerd door. In de alles-in-één praten de forks daarom
met de hookwacht (`127.0.0.1:38102`, eigen bewaakte dienst, draait als de app-gebruiker):

- normaal stuurt hij elk hook-verzoek ongewijzigd door naar core; core beslist;
- antwoordt core niet binnen `MG_HOOK_TIMEOUT_MS − 500 ms`, met een 5xx, of op een pre-grab met
  een andere fout (die de fork als 'onbereikbaar' zou zien), dan kijkt hij naar de vastgelegde
  noodstop (`/config/core/noodstop.json`, door core bij elke wijziging en elke
  minuut geschreven; anders de database, alleen-lezen). Noodstop aan → hij weigert de grab zelf
  (`EMERGENCY_STOP`). Noodstop uit → `503`, en de fork past zijn eigen fail policy toe: een
  trage of herstartende core blokkeert dus nooit gewone downloads;
- hij antwoordt zelf nooit met *toestaan*.

**Niet afgedekt (zou een nieuwe fork-build vragen):** in de losse compose-opstelling praten de
forks nog rechtstreeks met core (zelfde gedrag als vóór de hookwacht); en de circuit breaker in
de fork (`MEDIAGUARD_HOOK_CIRCUIT_BREAK_SECONDS`, 30 s) slaat na een mislukte aanroep de hook
even over, dus een noodstop die in die 30 s na een herstart wordt aangezet, kan één RSS-ronde
later pas gelden. Dat los je alleen in de forks zelf op (circuit breaker uit, of een eigen
noodstopcache in `MediaGuardHookClient`).

## 5. Containerisolatie (deploy/truenas/compose.yaml)

- **Alles-in-één** (`mediaguard.aio`): de web-server (het enige deel achter de tunnel) draait als
  eigen gebruiker `nobody` (65534, `MG_WEB_UID`/`MG_WEB_GID`) zonder de groepen van de app. Hij
  kan daardoor niet bij `/config/core` (database, `secret.key`), `/config/secrets` (beide 0750)
  en niet bij de omgeving van de andere processen (`/proc/<pid>/environ`, waar de hook- en
  API-sleutels van de forks staan); hij krijgt zelf geen sleutels of het encoder-adres mee. Bij
  de media kan hij alleen als de dataset voor iedereen leesbaar is. Bij elke start haalt
  `mediaguard.aio` de rechten voor 'overige gebruikers' weg van `core`, `secrets`, `encode`,
  `logs` en de fork-mappen in `/config` (alleen als die van de app-gebruiker zijn; zo ook bij een
  oudere installatie waar zo'n map nog open stond). Draait de container al als vaste gebruiker
  (niet als root), dan kan het wisselen van gebruiker niet en draait web als die gebruiker.
  Voor de uitleg bij 'core onbereikbaar' (migratie, herstartlus) leest web alleen
  `/run/mediaguard/core-status.json`: een beperkte kopie van `supervisor.json` (staat, migratie,
  uitleg) zonder pids, exitcodes of logregels, in een map van root die web niet kan wijzigen.
- **Geen** `privileged`, **geen** Docker-socket, `cap_drop: [ALL]`,
  `no-new-privileges:true` op elke container.
- Alle langlopende containers draaien als `568:568` (TrueNAS `apps`). Alleen
  `mediaguard-init` draait kort als root met uitsluitend `CHOWN`, `DAC_OVERRIDE` en `FOWNER`,
  zonder netwerk (`network_mode: none`), en stopt daarna.
- `read_only: true` rootfs voor web en encoder (alleen `/tmp` als tmpfs).
- GPU: alleen het device `/dev/dri` plus `group_add` render/video-GID; geen extra rechten.
- Resourcelimieten per container (cpus/memory) tegen het uithongeren van de NAS.
- Eén privé bridge-netwerk; gepubliceerd zijn alleen web (30080) en de vier fork-API's
  (voor Seerr). Core, recovery en encoder zijn niet van buitenaf bereikbaar.

## 6. Schrijfrechten op de bibliotheek

| Container | `/data/media` | Toelichting |
|---|---|---|
| web | niet gekoppeld | |
| core | read-only | analyse, identiteit, policy |
| encoder | read-only | schrijft alleen in `/work` (cache/staging); vervangt nooit zelf (`AV1_REPLACEMENT_MODE=handoff`, ADR-005) |
| recovery | schrijfbaar | enige MediaGuard-schrijver (G2); vervangt pas na verificatie, met quarantaine en journaal, en alleen na de overstap of na **Bibliotheek vrijgeven** (softwarevergrendeling, standaard dicht) |
| forks | schrijfbaar (`/data`) | Sonarr/Radarr moeten importeren; in schaduwmodus (`MEDIAGUARD_SHADOW_MODE=1`) weigeren ze grabs lokaal |

Staging (`${MG_WORK}/encode/staging`) is read-only voor core, schrijfbaar voor encoder en
recovery. Quarantaine is read-only voor core. Recovery controleert vóór vervanging de bron-
snapshot (grootte, mtime, inode), de sha256 van de staging-uitvoer en de vrije ruimte; zie
`docs/DATA_RECOVERY.md` en de fault-injection-tests (kill -9 op elke stap, disk full,
permission denied).

Cache → staging gebeurt met `rename`; lukt dat niet door `EXDEV` (aparte mounts), dan kopieert
de encoder met fsync en sha256-controle en verwijdert pas daarna het cache-resultaat.

## 7. Redactie en logging

- `mediaguard.secrets_store.redact` verwijdert `apikey=`, `token=`, `password=`, `passkey=`,
  `secret=` (querystring en JSON), `X-Api-Key:`, `Authorization: Bearer`, `sk-…`-sleutels en
  MediaGuard-API-sleutels (`mgk_…`) uit foutmeldingen, logregels, health-details en
  encoder-fouten (`EncoderError`).
- Elk logrecord gaat door `RedactingFilter` (via de record-factory, `install_log_redaction`),
  dus ook regels van uvicorn en bibliotheken (test: `test_logs_redact_keys`).
- **Supportbundel** (`GET /api/v1/admin/support-bundle`, admin): versies, instellingen-
  overrides, recente audit/jobs, de logs van core, recovery, AV1-engine en supervisor
  (`/config/logs`, de nieuwste ±2 MB per bestand) en de supervisorstatus — allemaal
  geredigeerd — en alleen de **namen** van geheimen. Geen `secret.key`, geen sessies, geen
  wachtwoord-hashes (tests: `test_support_bundle_has_no_secrets`,
  `test_info_lines_are_kept_and_level_setting_works`).
- Back-up downloaden is alleen voor admins; restore vereist het intypen van de back-upnaam,
  controleert de sha256 en maakt eerst een veiligheidsback-up.
- Logrotatie: json-file 5 × 20 MB per container; daarnaast `/config/logs/<dienst>.log`
  (4 × 5 MB per dienst; niveau volgt de instelling 'Logniveau'); encoder roteert zijn eigen
  joblogs.

## 8. AI-dataminimalisatie (zie `docs/AI_ADVISOR.md`)

- AI is **adviserend** (ADR-009): geen tool-/schrijfrechten, gestructureerde output, de
  lokale PolicyGuard beslist.
- Standaard **uit**; per provider expliciete opt-in (gelogd); intrekken zet AI direct uit.
- Verstuurd wordt alleen een compacte casus (titels, mappings, releasenamen, rejection-codes,
  metingen). Nooit: media of frames, sleutels/tokens/cookies, absolute paden (alleen
  bestandsnaam), URL-paden/querystrings (lokale hosts → `[lokale-host]`), configdumps.
- `GET /api/v1/ai/preview/{case_id}` toont exact het verzoek dat zou worden verstuurd.
- Releasenamen e.d. gaan uitsluitend als data in één JSON-blok (prompt-injection-bescherming).
- Geen ruwe prompts bewaard; casussen verlopen na `ai.retention_days` (90).

## 9. Supply chain

| Onderdeel | Pinning |
|---|---|
| `lvos04/av1` | commit `4b31986b8e91bc3d03f5372436e02644efe0c41d` (geïmporteerd in `services/encoder`, `apps/web`) |
| Sonarr | `bc54afbdd0e38bdb0fca4785fcf7d7a6af77bf9a` (4.0.20.3014) + `forks/sonarr/patches` |
| Radarr | `a96bf7c3ecb23cb76993770727285dc17a613fbc` (6.4.4.10685) + `forks/radarr/patches` |
| FFmpeg-bron HDR10+-route | 8.1.3, sha256 `7138d28c96d9d3e3af4ee3d8cad72741f8ffb40da90c1112235dea3ecd3178a3` (ADD `--checksum` en opnieuw in de bouwstage) |
| dovi_tool | 2.3.1, sha256 `1db0735a…f8ae68` |
| hdr10plus_tool (alleen testimage) | 1.7.2, sha256 `06385f37…2249f2` |
| Basisimages | `ubuntu:26.04`, `python:3.12-slim-bookworm`, `node:22-bookworm-slim`, `mcr.microsoft.com/dotnet/sdk:6.0.405` / `8.0.421`, runtime-deps `6.0`/`8.0-bookworm-slim` |
| Web-dependencies | exacte versies in `apps/web/package.json` + `package-lock.json` (`npm ci`) |
| Python-dependencies | bereiken met boven- en ondergrens in `pyproject.toml` |
| Release-images | `ghcr.io/lvos04/mediaguard-*` met vaste versietags, nooit `latest`; OCI-labels met bronrepo en revisie; forks met GPL-3.0-bronverwijzing (upstream-SHA + patches) |

Het overzicht staat machineleesbaar in `deploy/release-manifest.json` en is in de UI zichtbaar
via `GET /api/v1/admin/version`. Upstream-wijzigingen worden nooit automatisch overgenomen:
`forks/*/sync-upstream.sh` (nachtelijk in CI, rapportmodus) wijzigt niets; promotie is een
handmatige, gereviewde stap (`docs/FORK_UPSTREAM_SYNC.md`).

**Open punten supply chain:** basisimages zijn op tag gepind, niet op digest; Python-
dependencies hebben geen lockfile met hashes; er is (nog) geen SBOM of image-signing
(cosign) in `release.yml`.

## 9a. Noodtoegang (wachtwoord of 2FA kwijt)

Werkt alleen met toegang tot de container, nooit via internet. In TrueNAS: **Apps → MediaGuard
→ ⋮ → Shell** (of `docker exec -it <container> sh`), dan:

```sh
/opt/venv/bin/mediaguard-noodtoegang status                    # gebruikers, 2FA, klopt secret.key?
/opt/venv/bin/mediaguard-noodtoegang wachtwoord admin          # nieuw wachtwoord (gevraagd, niet zichtbaar)
/opt/venv/bin/mediaguard-noodtoegang wachtwoord admin --2fa-uit   # ook de telefoon kwijt
/opt/venv/bin/mediaguard-noodtoegang 2fa-uit admin
/opt/venv/bin/mediaguard-noodtoegang uitloggen [gebruiker]     # alle sessies beëindigen
/opt/venv/bin/mediaguard-noodtoegang beheerder admin           # weer beheerder en ingeschakeld
```

(of `/opt/venv/bin/python -m mediaguard.noodtoegang …`; database standaard
`/config/core/mediaguard.db`, anders `--db`). `wachtwoord`, `2fa-uit` en `uitloggen` loggen de
sessies van die gebruiker uit; elke wijziging staat in het auditlog als `cli:noodtoegang`. Als root gestart schakelt het eerst over naar de
eigenaar van de database (568), zodat core de bestanden daarna gewoon kan schrijven. Core hoeft
er niet voor te stoppen.

## 10. Wat niet fysiek getest is

Echte TrueNAS-host (ACL's, volume-subpaden, `/dev/dri`-rechten), een echte cloudflared voor
`CF-Connecting-IP`/`Secure`-cookie (getest met nagebootste headers), de web-server als `nobody`
in de echte image, publiek-maken van de GHCR-packages en een TrueNAS-registry-credential.
Zie `docs/PROGRESS.md`.
