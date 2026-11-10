# MediaGuard — API voor de web-UI en de Android-app

Contract voor de nieuwe endpoints van release 1.2 (builder C9). Alle paden onder `/api/v1`.
Authenticatie: sessiecookie + `X-CSRF-Token` (web), of `X-Api-Key` (app; rol `operator`, zie
`docs/SECURITY.md` §2a). Fouten: `{"detail": "<Nederlandse tekst>"}` of
`{"detail": {"code": "...", "message": "..."}}`. Datums ISO-8601 in UTC (`...Z`); de client
toont ze in Europe/Amsterdam. Groottes in bytes; de UI rekent met 1 GB = 1024³.

`{item_id}` is het MediaGuard-bibliotheek-ID (`lib_items.id`, zoals in `GET /library` en
`GET /library/{item_id}`), niet het Sonarr/Radarr-ID.

## 1. Seizoenen en afleveringen aan/uit

| Methode | Pad | Rol | Body |
|---|---|---|---|
| PUT | `/library/{item_id}/seasons/{season_number}` | operator | `{"monitored": bool, "search": bool=false}` |
| PUT | `/library/{item_id}/episodes/{episode_id}/monitor` | operator | `{"monitored": bool, "search": bool=false}` |

`episode_id` = Sonarr-aflevering-ID (zoals in `GET /library/{item_id}/seasons`).
Antwoord (beide):
```json
{"item_id": 12, "season_number": 2, "monitored": true, "episodes_changed": 10,
 "search_started": true, "message_nl": "Seizoen 2 wordt gevolgd; zoeken gestart"}
```
Alleen series in Sonarr (404 bij film). `search: true` start `SeasonSearch` / `EpisodeSearch`
(alleen bij `monitored: true`). Audit `library.season_monitor` / `library.episode_monitor`;
de bibliotheek wordt daarna bijgewerkt.

## 2. Kwaliteit en waarom er geüpgraded wordt

### `GET /library/{item_id}/files` (viewer)
Per bestand (film: 1, serie: alle afleveringen met bestand):
```json
{"item_id": 12, "kind": "series", "profile": {"name": "MediaGuard · Anime Dual Audio",
   "cutoff": "Bluray-2160p", "cutoff_score": 2200, "min_upgrade_score": 100},
 "files": [{
   "file_id": 991, "episode_id": 4501, "season_number": 1, "episode_number": 3,
   "label": "S01E03", "path": "/data/media/anime/...", "size": 1503238553,
   "quality": {"name": "WEBDL-1080p", "resolution": 1080, "source": "web"},
   "media": {"video_codec": "AV1", "dynamic_range": "HDR10", "hdr": true,
             "dolby_vision": false, "audio_languages": ["jpn","eng"],
             "subtitle_languages": ["eng"], "measured_by": "mediaguard"},
   "custom_formats": [{"name": "MediaGuard · Dual Audio", "score": 2000}],
   "score": 2000, "cutoff_met": true, "quality_cutoff_met": true, "score_cutoff_met": false,
   "av1_by_mediaguard": true,
   "summary_nl": "WEBDL-1080p · AV1 · Dual Audio — score 2000 van 2200"}]}
```

### `GET /library/{item_id}/upgrades?days=90` (viewer)
Upgrade-historie met reden, nieuwste eerst:
```json
{"item_id": 12, "upgrades": [{
   "at": "2026-11-07T04:12:00Z", "label": "S01E03",
   "old": {"quality": "WEBDL-1080p", "score": 1500, "custom_formats": ["Engelse dub"],
           "size": 1400000000, "av1_by_mediaguard": true, "release": "..."},
   "new": {"quality": "WEBDL-1080p", "score": 2000, "custom_formats": ["Dual Audio"],
           "size": 1600000000, "release": "...", "release_group": "GRPA",
           "indexer": "NZBgeek"},
   "cf_added": ["Dual Audio"], "cf_removed": ["Engelse dub"],
   "source": "rss|backfill|optimization|seerr|mso|manual|recovery",
   "source_nl": "Sonarr/Radarr (RSS)",
   "reason_nl": "Betere custom formats: +Dual Audio (score 1500 → 2000)"}]}
```
`reason_nl` is altijd gevuld: kwaliteitsstap, scorewinst (met de verschillende formats),
REPACK/PROPER, of "onbekend (geen historie)". `old.av1_by_mediaguard: true` betekent dat
het vervangen bestand een AV1-versie van MediaGuard was.

Items in `GET /storage/ledger` krijgen bij soort `upgrade` het veld `waarom` (= `reason_nl`).

## 3. Seerr (MediaGuard als doorgeefluik)

Gebruikt de Seerr-URL en API-sleutel uit Instellingen → Koppelingen. Niet ingesteld → 409
`{"code": "SEERR_NOT_CONFIGURED"}`.

| Methode | Pad | Rol | Wat |
|---|---|---|---|
| GET | `/seerr/search?q=&page=1` | viewer | films + series |
| GET | `/seerr/discover/trending?page=1` | viewer | populair nu |
| GET | `/seerr/discover/movies?page=1` | viewer | populaire films |
| GET | `/seerr/discover/tv?page=1` | viewer | populaire series |
| GET | `/seerr/media/{movie\|tv}/{tmdb_id}` | viewer | details + seizoenen |
| POST | `/seerr/request` | operator | aanvraag doen |
| GET | `/seerr/my-requests?filter=all\|pending\|processing\|available&take=50` | viewer | aanvragen + leverstatus |

Zoek/discover-resultaat:
```json
{"page": 1, "total_pages": 5, "results": [{
   "media_type": "movie", "tmdb_id": 603, "title": "The Matrix", "year": 1999,
   "poster_url": "https://image.tmdb.org/t/p/w342/....jpg", "overview": "...",
   "status": "beschikbaar|gedeeltelijk|aangevraagd|verwerken|niet",
   "status_nl": "Beschikbaar"}]}
```
Details (`tv`): plus `seasons: [{"season_number": 1, "episodes": 12, "status": "...",
"status_nl": "..."}]`, `backdrop_url`, `genres`, `runtime`, `rating`.

Aanvraag:
```json
POST /seerr/request
{"media_type": "tv", "tmdb_id": 1399, "seasons": [1, 2] | "all", "is4k": false}
→ {"request_id": 77, "status": "aangevraagd", "message_nl": "Aangevraagd: 2 seizoenen"}
```
Seerr bepaalt de rechten van de ingestelde Seerr-gebruiker (goedkeuren, quota); een weigering
komt terug als 403/409 met Nederlandse tekst.

`my-requests` → `{"results": [{"request_id", "media_type", "tmdb_id", "title", "year",
"poster_url", "requested_at", "requested_by", "seerr_status_nl", "delivery": {"state",
"state_nl", "progress": {"present", "total"}, "next_step_nl"} | null}]}` (leverstatus uit
MediaGuard's leveringsbewaking).

## 4. App

### `GET /app/status` (viewer) — compact, voor elke 15–30 s
```json
{"version": "1.2.1", "generated_at": "2026-11-08T13:02:00Z",
 "emergency_stop": {"active": false, "reason": "", "since": null},
 "attention": {"count": 3, "errors": 1, "items": [{
     "key": "encoding.conflict", "title_nl": "1 vervanging met een conflict", "severity": "error",
     "detail_nl": "MediaGuard kon niet met zekerheid vaststellen …", "href": "/quarantaine", "count": 1,
     "actions": [{"label_nl": "Nu proberen", "method": "post", "path": "/encoding/autopilot/run",
                  "body": {}, "confirm_nl": null, "primary": true}],
     "rows": [{"id": "108", "title": "Show S02E04.mkv", "detail": "…", "href": "/av1/…",
               "actions": [{"label_nl": "Origineel houden", "method": "post",
                            "path": "/encoding/jobs/108/cancel", "body": {},
                            "confirm_nl": "Het AV1-resultaat wordt weggegooid.", "primary": false}]}],
     "more_rows": 0, "admin_actions": 0}]},
 "encoder": {"reachable": true,
             "lanes": [{"lane": "gpu", "running": 2, "slots": 4, "paused": false, "reason_nl": null}],
             "running": [{"job_id": 115, "engine_job_id": "55", "title": "...", "lane": "gpu",
                          "percent": 42.5, "eta_seconds": 900, "phase_nl": "Encoderen"}],
             "queued": 8},
 "downloads": {"active": 3, "clients_ok": true,
               "items": [{"title", "client", "percent", "eta_seconds", "size", "status_nl"}]},
 "storage": {"free_bytes": 0, "days_left_nl": "± 300 dagen"},
 "savings_bytes": 834000000000}
```
- `attention`: dezelfde lijst als `GET /attention` in de web, **inclusief problemen met de AV1-engine**
  (onbereikbaar, baan gepauzeerd of stil), fouten eerst; hooguit 20 punten, `count` is het totaal.
  `actions`/`rows[].actions` zijn de knoppen van de web: de app doet `POST /api/v1{path}` met `body`.
  Alleen knoppen die de rol van de aanroeper mag gebruiken staan erin; `admin_actions` telt de knoppen
  die alleen een beheerder in de web heeft. De app vraagt eerst om bevestiging als `confirm_nl` gevuld
  is of `body.confirm` `true` is. Per punt hooguit 5 `rows`; `more_rows` = de rest. `detail_nl` en
  `rows[].detail` zijn al leesbaar gemaakt (tijden als “4 nov 13:01”, geen ruwe ISO-tijden).
- `encoder.lanes`: echte bezetting uit de engine (`GET /api/workers`): `running` lopende encodes,
  `slots` ingestelde plekken. `running[]` heeft baan en ETA; `job_id` is `null` als alleen de engine
  de job kent. Tijdens een noodstop: `phase_nl` "Gepauzeerd (noodstop)" en geen ETA.
- `downloads.clients_ok`: `null` = onbekend (status niet op te halen).

### `GET /app/recent` (viewer) — laatste 24 uur (rollend)
Zelfde vorm als het dagoverzicht hieronder, plus `"window": "24h"`, `"label_nl": "Laatste 24 uur"`,
`"from"`, `"to"` (UTC) en `"date": null`. `storage.unexplained_bytes` komt uit de afstemming van het
opslaglogboek (`GET /storage/ledger?period=24h`); `null` = nog niet gemeten. Oudere servers geven
404; de app valt dan terug op het overzicht van gisteren.

### `GET /notices` (viewer) — 'Nieuw in deze versie'
```json
{"version": "1.2.2", "show": true, "dismissed": false,
 "items": [{"key": "1.2.0:encoder.max_cpu_uren", "title": "Nieuwe standaard: …",
            "body": "…", "link": "/av1/instellingen#uitvoering", "at": "…"}],
 "version_info": {"version": "1.2.2", "previous": "1.2.1", "fresh_install": false}}
```
`POST /notices/gezien` (operator) verbergt de kaart. Dezelfde kaart staat ook als item
`notices.whats_new` (ernst `info`) in `attention`; daar staan ook `backup.failed`,
`backup.rollback_pending` en `supervisor.restarts.<dienst>` (dienst herstart steeds opnieuw).

### `GET /app/summary?date=YYYY-MM-DD` (viewer) — dagoverzicht (standaard: gisteren)
```json
{"date": "2026-11-07", "label_nl": "Gisteren",
 "headline_nl": "Gisteren: 37 afleveringen, 4 films erbij; 12 omgezet naar AV1 (96 GB bespaard)",
 "replaced": [{"title", "label", "from_size", "to_size", "saved", "vmaf", "at"}],
 "downloaded": [{"title", "label", "kind": "nieuw|upgrade", "size", "quality", "source_nl", "at"}],
 "upgrades": [{"title", "label", "reason_nl", "at"}],
 "requests": [{"title", "media_type", "requested_at", "delivery_state_nl"}],
 "attention": [{"title_nl", "severity", "href"}],
 "storage": {"net_bytes", "added_bytes", "saved_bytes", "unexplained_bytes"},
 "encoder": {"done": 12, "failed": 1, "saved_bytes": 0}}
```
`storage.unexplained_bytes`: gemeten daling van de vrije ruimte min de bekende oorzaken over die dag
(uurlijkse metingen van het opslaglogboek bij het begin en eind van de dag); `null` als die metingen
ontbreken of de dag nog loopt.

### `POST /app/control` (operator)
```json
{"action": "emergency_stop_on|emergency_stop_off|pause_lane|resume_lane|run_autopilot|retry_job|search_item|test_notification",
 "params": {"reason": "...", "lane": "cpu|gpu", "job_id": 115, "item_id": 12, "topic": "mediaguard-…"},
 "confirm": true}
→ {"ok": true, "message_nl": "Noodstop staat aan"}
```
`emergency_stop_on`, `emergency_stop_off` en `pause_lane` vereisen `"confirm": true` (anders
409 `CONFIRM_REQUIRED`). Alles komt in de audit met de API-sleutel als actor.
`test_notification` stuurt meteen een testbericht naar het telefoonkanaal (ntfy met `params.topic`,
anders het automatisch aangemaakte kanaal) → `{"ok", "message_nl", "ntfy": {"server", "topic",
"enabled"}}`; 409 als er geen telefoonkanaal is. Alleen voor de app-sleutel (`android-app …`) en
beheerders; andere operators krijgen 403 (kanaalbeheer is admin-only).

### `POST /app/paired` (app-sleutel of admin) — na het koppelen
```json
{"ntfy_topic": "mediaguard-…" | null, "app_version": "1.2.1"}
→ {"ok": true, "ntfy": {"server": "https://ntfy.sh", "topic": "mediaguard-…", "enabled": true},
   "message_nl": "Pushmeldingen naar deze telefoon staan aan"}
```
De app meldt zo dat hij het telefoonkanaal leest; MediaGuard zet dat ntfy-kanaal dan **aan** (het
automatisch aangemaakte kanaal staat uit tot er een telefoon meeleest). Zonder `ntfy_topic` (handmatig
gekoppeld) krijgt de app het kanaal terug. De app-sleutel krijgt alleen het automatisch aangemaakte
telefoonkanaal, nooit een ntfy-kanaal dat de beheerder zelf instelde; andere operators krijgen 403
en zien het onderwerp niet. Mislukt de aanroep (geen verbinding, oudere server), dan
probeert de app het later opnieuw (hooguit eens per kwartier); bij 403 (viewer-sleutel) niet. Audit `app.paired`; `GET /app/pairing` toont het laatst
gekoppelde toestel onder `device`.

### `GET /app/pairing` (admin)
```json
{"server_url": "https://mediaguard.example.nl", "server_url_source": "setting|request",
 "server_url_upgraded": true, "server_url_note_nl": "Publiek adres: … https:// …" | null,
 "api_key_name": "android-app",
 "existing_key": {"id": 4, "prefix": "mgk_Ab12", "created_at": "..."} | null,
 "ntfy": {"server": "https://ntfy.sh", "topic": "mediaguard-…", "enabled": true} | null}
```

### `POST /app/pairing/key` (admin) — nieuwe app-sleutel + QR
Trekt een eerdere actieve app-sleutel in (één telefoon tegelijk; een verloren telefoon koppel
je zo los) en maakt een nieuwe sleutel met rol operator (naam `android-app <datum> <code>`). Is er nog
geen ntfy-kanaal voor de telefoon, dan maakt MediaGuard er een (privé onderwerp, nog uit); de QR-code
bevat het altijd. Een ingetrokken of verlopen sleutel geeft 401 maar telt niet mee voor de IP-blokkade
bij ongeldige sleutels: een telefoon die nog even met de oude sleutel pollt, blokkeert de nieuwe niet.
```json
{"key_id": 5, "key_prefix": "mgk_Xy34",
 "qr_payload": {"v": 1, "url": "https://...", "key": "mgk_…", "ntfy": {...}},
 "qr_text": "{\"v\":1,...}", "qr_data_uri": "data:image/png;base64,…",
 "message_nl": "Scan deze code met de MediaGuard-app. De code toont de sleutel maar één keer."}
```
De web-UI toont `qr_data_uri` (of maakt zelf een QR van `qr_text`). De app slaat de sleutel
versleuteld op en stuurt hem als `X-Api-Key`; met `ntfy` abonneert de app zich direct op
hetzelfde ntfy-onderwerp voor pushmeldingen en meldt zich met `POST /app/paired`.
Instelling `app.public_url` (Instellingen → Koppelingen, alleen beheerder): het adres waarop de
app MediaGuard bereikt, bijvoorbeeld de Cloudflare-tunnel; leeg = het adres uit het verzoek.
Een publiek adres (tunnel-domein of publiek IP, zonder eigen poort) staat altijd als `https://`
in de QR-code (`server_url_upgraded`), ook als het als `http://` is ingevuld of via cloudflared
als http binnenkwam; LAN-adressen blijven `http://`. De app doet hetzelfde bij het inlezen.

De app-sleutel (rol operator) kan via `PATCH /settings/scoped` geen gevaarlijke instellingen
wijzigen (originelen, beveiliging, adressen van Seerr/Emby/TrueNAS/off-site back-up; `403`) en
nooit `library.write_enabled` (`409`, alleen via de knop *Bibliotheek vrijgeven*). Zie
`docs/SECURITY.md` §2.

## 5. Nieuwe versie (server en app)

Core kijkt hooguit één keer per dag op GitHub (`releases/latest` van
`lvos04/media-guard-ultimate`) of er een nieuwere MediaGuard is. Er wordt nooit vanzelf
bijgewerkt. Bij een nieuwe versie: één melding (categorie *Wacht op jou*), een punt
`system.update_available` in `GET /attention` en in `attention` van `GET /app/status`, en de
velden hieronder. Instelling `updates.check_enabled` (standaard aan; Instellingen › Systeem &
back-ups → Updates). De uitkomst staat in de database: een herstart doet geen extra verzoek.

### `GET /system/update` (viewer)
```json
{"enabled": true, "current": "1.2.1", "latest": "1.2.2", "update_available": true,
 "url": "https://github.com/lvos04/media-guard-ultimate/releases/tag/v1.2.2",
 "published_at": "2026-11-09T10:00:00Z",
 "image": "ghcr.io/lvos04/mediaguard-core:1.2.2-aio",
 "checked_at": "2026-11-09T12:00:00+00:00", "next_check_at": "2026-11-10T12:00:00+00:00",
 "error_nl": null, "dismissed": false,
 "android": {"latest": "1.2.2",
             "url": "https://github.com/lvos04/media-guard-ultimate/releases/download/v1.2.2/mediaguard-android-1.2.2.apk"}}
```
- `update_available`: `latest` is nieuwer dan `current` (alleen getallen: `1.2.10` > `1.2.9`).
  Met de controle uit zijn `latest`, `url`, `image`, `android` en `error_nl` `null`.
- `android`: de APK uit dezelfde GitHub-release (`null` als die er (nog) niet bij hangt). De app
  vergelijkt `android.latest` met zijn eigen versie zonder het bouwnummer (`1.2.1+3` = `1.2.1`).
- `error_nl`: waarom de laatste poging mislukte (bv. GitHub niet bereikbaar); de oude uitkomst
  blijft dan staan en de volgende poging is een dag later.
- `dismissed`: de gebruiker koos *Verbergen tot de volgende versie*.

### `POST /system/update/check` (operator)
Nu controleren (hooguit eens per minuut; anders het laatste antwoord). 409 als de controle uit
staat. Antwoord: zelfde vorm als `GET /system/update`.

### `POST /system/update/dismiss` (operator)
`{"version": "1.2.2"}` → het punt in *Wacht op jou* verdwijnt tot er een nog nieuwere versie is.
409 als dat niet de nieuwste versie is.
