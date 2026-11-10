# Seerr-compatibiliteit

Seerr (Overseerr/Jellyseerr-lijn) blijft ongewijzigd draaien (DEEL H). MediaGuard praat
alleen via Seerr's **ondersteunde REST-API** met Seerr en wijzigt de Seerr-database nooit
direct. Code: `apps/core/mediaguard/seerr/`.

## 1. Authenticatie en instellingen

* Seerr-API-sleutel (Seerr → Instellingen → Algemeen) wordt opgeslagen als secret
  `seerr:apikey` (Fernet, nooit teruggegeven); URL als instelling `seerr.url`.
* Header: `X-Api-Key: <sleutel>`; basis-pad `/api/v1`.
* `PUT /api/v1/seerr/settings` slaat pas op nadat `GET /api/v1/status` met de nieuwe
  gegevens slaagt (Q2.2).

## 2. Seerr-endpoints die MediaGuard gebruikt

| Seerr-endpoint | Doel | Muteert? |
|---|---|---|
| `GET /api/v1/status` | Verbindingstest, versie | nee |
| `GET /api/v1/settings/sonarr` / `radarr` | Serverlijst (hostname, port, useSsl, baseUrl, apiKey, isDefault, is4k, activeProfileId, activeDirectory, activeAnimeProfileId, activeAnimeDirectory, …) | nee |
| `POST /api/v1/settings/sonarr/test` / `radarr/test` | Seerr's eigen verbindingstest naar de instantie (body: hostname, port, apiKey, useSsl, baseUrl) | nee |
| `PUT /api/v1/settings/{sonarr,radarr}/{id}` | Cutover: server naar MediaGuard-instantie laten wijzen | **ja, alleen met `confirm: true`** |
| `POST /api/v1/settings/{sonarr,radarr}` | Cutover met `create_if_missing` | **ja, alleen met `confirm: true`** |
| `DELETE /api/v1/settings/{sonarr,radarr}/{id}` | Rollback van een door MediaGuard aangemaakte server | **ja, alleen met `confirm: true`** |
| `GET /api/v1/request?take&skip&filter=all&sort=added` | Aanvragen synchroniseren (paging via `pageInfo.results`) | nee |

## 3. MediaGuard-API

| Route | Rol | Beschrijving |
|---|---|---|
| `GET/PUT /api/v1/seerr/settings` | viewer / admin | URL + sleutel (geverifieerd). |
| `POST /api/v1/seerr/test` | operator | Status + serverlijst (sleutels gemaskeerd). |
| `POST /api/v1/seerr/route-test` | operator | Per instantie: server gevonden, URL/poort/base-URL gelijk aan `public_url` (of `url`), API-sleutel gelijk aan de instantiesleutel, `isDefault`/`is4k` zoals bedoeld, profiel en rootmap bestaan in de instantie (anime: ook `activeAnime*`), Seerr's eigen test slaagt, geen dubbele koppeling, en Seerr-servers die bij geen instantie horen (oude systeem). |
| `GET /api/v1/seerr/mapping` | viewer | Zelfde rapport zonder Seerr-verbindingstests (alleen-lezen). |
| `GET /api/v1/seerr/routing`, `PUT /api/v1/seerr/routing/{instance_id}` | viewer / operator | Bedoelde route per instantie (`role` default/anime/4k/4k-anime/manual, server-ID, verwacht profiel/rootmap). |
| `POST /api/v1/seerr/cutover` | admin | Zonder `confirm`: diff-preview. Met `confirm`: vorige waarden bewaren (sleutel in secret `seerr:rollback:<id>:apikey`) en server bijwerken. Profielen/rootmappen blijven behouden tenzij expliciet meegegeven. |
| `POST /api/v1/seerr/cutover/rollback` | admin | Herstelt exact de waarden van vóór de eerste cutover (of verwijdert een aangemaakte server). |
| `GET /api/v1/seerr/requests`, `POST /api/v1/seerr/requests/sync` | viewer / operator | Aanvragen met status (NL), gekoppeld aan `(instance_id, provider_id)` en `lib_items.id`; doorlooptijd aanvraag → beschikbaar. |

Standaardroutering (R4) als niets is ingesteld: `sonarr-series` = standaard Sonarr-server,
`sonarr-anime` = aparte Sonarr-server (niet-standaard), `radarr-films` = standaard
Radarr-server, `radarr-animefilms` = aparte Radarr-server. Seerr kiest een niet-standaard
server alleen bij expliciete serverkeuze/override; de routetest meldt dit als
aandachtspunt (MediaGuard neemt niet aan dat Seerr anime betrouwbaar herkent).

Koppelen van aanvragen: eerst `serverId` → instantie via de routering en
`media.externalServiceId(4k)` = Sonarr/Radarr-ID in die instantie; anders op canoniek
ID (`tvdbId` voor series, `tmdbId` voor films) als dat in precies één instantie voorkomt
(`link_status`: `linked`, `ambiguous`, `unlinked`, `not_in_instance`).

## 4. Sonarr/Radarr-API-aanroepen die Seerr doet

Op basis van de Overseerr/Jellyseerr-broncode (`server/api/servarr/{base,sonarr,radarr}.ts`).
Onze forks zijn upstream Sonarr/Radarr met alleen extra decision-specificaties (pre-grab/
pre-import hooks); **geen van deze routes of schema's is gewijzigd**, dus Seerr ziet per
instantie een normale v3-API (R2.1).

| Aanroep | Sonarr | Radarr | Doel in Seerr |
|---|---|---|---|
| `GET /api/v3/system/status` | ✔ | ✔ | Verbindingstest |
| `GET /api/v3/qualityprofile` | ✔ | ✔ | Profielkeuze |
| `GET /api/v3/rootfolder` | ✔ | ✔ | Rootmapkeuze |
| `GET /api/v3/languageprofile` | ✔ (v3; v4 geeft legacy-profiel of 404, Seerr vangt dit af) | – | Taalprofiel |
| `GET /api/v3/tag`, `POST /api/v3/tag` | ✔ | ✔ | Tags (o.a. per gebruiker) |
| `GET /api/v3/series/lookup?term=tvdb:<id>` | ✔ | – | Serie opzoeken / bestaande herkennen |
| `GET /api/v3/series`, `GET /api/v3/series/{id}` | ✔ | – | Beschikbaarheidsscan |
| `POST /api/v3/series` (met `addOptions`, `monitorNewItems`, seasons) | ✔ | – | Nieuwe serie |
| `PUT /api/v3/series` (id in body) | ✔ | – | Extra seizoenen monitoren bij bestaande serie |
| `GET /api/v3/movie/lookup/tmdb?tmdbId=`, `GET /api/v3/movie?tmdbId=` | – | ✔ | Film opzoeken / bestaande herkennen |
| `GET /api/v3/movie` | – | ✔ | Beschikbaarheidsscan |
| `POST /api/v3/movie`, `PUT /api/v3/movie` | – | ✔ | Toevoegen / opnieuw monitoren |
| `POST /api/v3/command` (`SeriesSearch`, `EpisodeSearch`, `MoviesSearch`) | ✔ | ✔ | Zoeken na aanvraag (tenzij `preventSearch`) |
| `GET /api/v3/queue` | ✔ | ✔ | Downloadstatus in Seerr |

Gevolgen van MediaGuard voor Seerr-flows:

* Een grab die Seerr via `command` laat starten loopt door de pre-grab-hook. Wordt die
  geweigerd (bv. `INSTANCE_SUSPENDED`, `EMERGENCY_STOP`, audio-/identiteitsbeleid), dan
  blijft de aanvraag in Seerr op 'verwerken' staan en verschijnt het item in MediaGuard
  *Ontbrekende media* met de concrete reden.
* AV1-conversie of upgrade verandert het Sonarr/Radarr-item niet van ID; Seerr blijft het
  als beschikbaar zien (`hasFile`/statistieken).
* Bij cutover alleen de serververbinding (host/poort/SSL/base-URL/sleutel, default/4K)
  wijzigen; profiel-/rootmap-ID's van het oude systeem worden gecontroleerd in de
  routetest omdat ID's per instantie verschillen.

## 5. Nog te verifiëren in de E2E-run tegen echte containers

* Exacte Seerr-versie en of `GET /settings/*` de API-sleutel ongemaskeerd teruggeeft (zo
  niet, rapporteert de routetest `api_key_matches = onbekend` en vertrouwt op Seerr's test).
* Sonarr v4 `PUT /api/v3/series` zonder `{id}` in het pad (RestPutById met optionele id).
* `media.status` 6/7 betekenis verschilt tussen Overseerr en Jellyseerr (blocklist/
  verwijderd); MediaGuard toont die alleen als label.

## 6. Contracttestresultaten

Suite: `tests/contract/seerr/` (`run.sh`, `test_seerr_contract.py`, details en elke assertie in
`tests/contract/seerr/RESULTS.md`). Echte, ongewijzigde **Seerr 3.5.1**
(`ghcr.io/seerr-team/seerr:v3.5.1`, commit `564bfb0`), geïnitialiseerd met **Jellyfin 12.4.0**
via de API's, tegen de vier fork-instanties (Sonarr 4.0.20.3014-mg1, Radarr 6.4.4.10685-mg1) en
MediaGuard core (lokale checkout). Uitgevoerd op 2026-11-02.

**Resultaat: 16/17 tests geslaagd (136/137 asserties).** De enige fout is een core-bug in de
cutover (zie B1). Met de voorgestelde fix: **17/17 (142/142)**.

Bewezen tegen echte Seerr:

* Seerr's eigen `POST /settings/{sonarr,radarr}/test` slaagt voor alle vier de forks (status,
  profielen, rootmappen, tags; Sonarr v4, dus geen taalprofielen). Een foute sleutel of poort
  geeft een nette HTTP 500.
* Routering: film zonder serverkeuze komt in `radarr-films`, animefilm met `serverId` in
  `radarr-animefilms`, serie met seizoen 1+2 in `sonarr-series` (alleen S1/S2 gemonitord,
  `monitorNewItems` van de Seerr-server), anime met `serverId` in `sonarr-anime` (`seriesType
  anime`, animeprofiel/-rootmap). Profiel, rootmap en tags komen telkens van de gekozen
  Seerr-server.
* Bestaande titels: een tweede aanvraag geeft 409 (film) of 202 "No seasons available" (serie).
  Een extra seizoen werkt dezelfde Sonarr-serie bij (`PUT /api/v3/series` zonder id in het pad
  werkt met Sonarr v4). Een titel die al in de fork stond, wordt opnieuw gemonitord en
  gekoppeld, zonder duplicaat.
* Beschikbaarheid: `radarr-scan` geeft media 5 en aanvraag 5. `sonarr-scan` met één aflevering
  geeft 4 (gedeeltelijk). Na een AV1-vervanging (zelfde Radarr-ID) blijft de titel 5, ook na
  `availability-sync`.
* Core: `POST /api/v1/seerr/route-test` meldt alle vier de routes OK. Core detecteert: een
  server die naar de verkeerde instantie wijst (+ "niet-gekoppelde server"), een verkeerde
  API-sleutel, een verkeerde default-vlag, een niet-bestaande rootmap en een verkeerde poort.
  `requests/sync` koppelt na de bibliotheeksynchronisatie alle 7 aanvragen aan de juiste
  instantie.
* Openstaande punten uit §5: Seerr 3.5.1 geeft `apiKey` **ongemaskeerd** terug aan admins (de
  routetest vergelijkt dus echt), en `PUT /api/v3/series` werkt.

Anime-routering (R4), vastgesteld in Seerr 3.5.1: er is **geen automatische anime-routering
naar een aparte server**. Override-regels wijzigen alleen rootmap/profiel/tags binnen de
standaardserver. Anime zonder `serverId` komt in de standaard-Sonarr terecht, met `seriesType
anime` en de `activeAnime*`-velden van díe server. Aparte anime-instanties werken alleen via
expliciete serverkeuze (admin of gebruiker met `REQUEST_ADVANCED` in het aanvraagvenster).
MediaGuard toont dit als aandachtspunt `anime_selection`.

Bevindingen in core (niet in deze taak aangepast):

* **B1 (bug):** `SeerrClient.server_update/server_create` sturen `id` mee (uit
  `GET /settings/*` resp. het rollback-snapshot). Seerr valideert tegen zijn OpenAPI-spec
  (`id` is readOnly) en antwoordt `400 request/body/id is read-only`. Daardoor faalt
  `POST /seerr/cutover` met `confirm` én `cutover/rollback`. Fix: `id` en `null`-waarden uit
  de body verwijderen (geverifieerd).
* **B2 (latent):** `create_if_missing` mist velden die Seerr verplicht stelt
  (`activeProfileName`, Radarr `minimumAvailability`, Sonarr `enableSeasonFolders`). Bij een
  profiel-override wordt `activeProfileName` niet bijgewerkt.
* **F1:** aanvragen koppelen ook via `media.serviceId` (Seerr vult dit voor elke verzonden
  aanvraag, ook zonder `serverId`).

Niet gedekt: 4K-servers, HTTPS/reverse proxy/base-URL, Plex/Emby, downloadstatus in Seerr,
niet-admin-gebruikers/goedkeuringsflow/quota en een echte encoder-conversie (gesimuleerd met
een ffmpeg-AV1-bestand).
