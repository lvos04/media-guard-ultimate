# Connector — alleen lezen

S-P3-610 en S-P3-615 leveren de leesconnector van P3-01 volgens contract 1.3, hoofdstukken 5 en 6.
De connector gebruikt de bestaande authenticatie: een API-sleutel met minstens rol
`viewer`, of een bestaande sessie met die rol. De centrale rechtencontrole blijft
van toepassing; een sleutel met `emergency_stop:on` mag de connector lezen, maar
krijgt op elke connector-POST `403 scope_ontbreekt`. Sessies behouden hun CSRF-controle.
Een ongeldige sleutel valt nooit terug op een geldige sessie.

De enige databasewijzigingen op de connectorroutes zijn bestaande
aanmeldregistraties: `api_keys.last_used_at`, `sessions.last_seen_at` en nieuwe
rijen in `audit_events` met actie `apikey.rejected` bij geweigerde sleutels.
MediaGuard-voortgang, bibliotheek en instellingen blijven gelijk. De publieke
routes `/healthz` en `/readyz` laten de hele database gelijk.

| Route | Gedrag |
|---|---|
| `GET /connector/v1/discover` | Dienst, bronversie, schemaversie, `read`, rol `viewer`, `item_types`, lege `actions`, maximale meetleeftijd 900 seconden en paginagrootte 200 |
| `GET /connector/v1/health` | Checks `database`, `worker`, `jobs`, `encoder` en `instances`; totaal is de slechtste check |
| `GET /connector/v1/items?type=&cursor=&limit=` | Vijf datasets met vaste projecties, UTC-meettijd, deeplink en ETag |
| `GET /connector/v1/evidence/{id}` | Geredigeerd pakket op basis van een auditregel of job |
| `GET /connector/v1/events` | Geredigeerde outbox in de event-envelop van hoofdstuk 7, met cursor en maximaal 200 gebeurtenissen per pagina |
| `POST /connector/v1/propose`, `/apply`, `/rollback` | Vaste weigering `501 capability_unavailable` na de bestaande toegangscontrole; geen uitvoering |
| `GET /healthz` | Zonder aanmelding alleen `{"status":"ok"}` wanneer de kern leeft |
| `GET /readyz` | Controleert zonder aanmelding de database en toegepaste migraties. Geeft `status: ok` (200) of `unavailable` (503) met `Retry-After: 5` |

Alle routes zijn bereikbaar via de bestaande enkele webpoort. De nieuwe routes
geven `Cache-Control: no-store`. Wanneer de kern niet bereikbaar is, geeft de
webdoorgifte op deze routes 503 met `Retry-After`; publieke gezondheidsroutes geven
ook dan geen versie, pad of instelling prijs. Bestaande API-routes en de bestaande
container-healthcheck behouden hun afhandeling en foutformaat.

## Metingen en versheid

`health` hergebruikt `health_summary` en de bestaande activiteit- en encodercache.
Interne meetmetadata verandert de bestaande API-payloads niet. De werkelijke
meettijd blijft bewaard, ook wanneer een mislukte encoderprobe een laatst bekende
waarde behoudt. Zonder herkomst, bij een ongeldige meettijd of na 900 seconden
wordt een check `unknown`; bij veroudering is de reden `meting_verouderd`.
De totale `observed_at` is de oudste beschikbare meettijd van de checks.
De volgorde voor het totaal is `healthy < unknown < degraded < down`.

`worker` gebruikt de laatste geslaagde hartslag van de bestaande jobrunner.
`jobs` geeft aantallen wachtend/bezig en de laatste voltooide job. Een uitvoerbare
wachtrij met meer dan 900 seconden geen voortgang geeft `degraded`, behalve bij
een bewuste noodstop. Ruwe foutteksten, instantienamen, paden en kijkgedrag worden
niet doorgegeven. `hub_link` is niet van toepassing op deze bestaande app.

## Gebeurtenissen ophalen

Laat bij de eerste aanvraag de parameter `cursor` weg. De gebeurtenissen komen op
oplopend bron-ID vanaf de oudste bewaarde rij. `limit` accepteert 1 tot en met 200
en is standaard 200. Bewaar `next_cursor` pas nadat alle gebeurtenissen op de
pagina zijn verwerkt. Stuur de waarde letterlijk mee bij de volgende aanvraag.
Ook een lege pagina geeft een cursor en houdt de leespositie vast.

De outbox bewaart gebeurtenissen standaard 14 dagen via
`maintenance.retention_days_events`. De ondertekende cursor bevat de generatie,
de laatste positie en de retentierevisie en is gebonden aan de bronrij. Verwijdert retentie
sinds die revisie een rij op of voorbij de leespositie, dan volgt
`409 cursor_expired` met `details.restart: true`. Dit antwoord geldt ook voor
een cursor uit een andere generatie, een cursor zonder generatie of een
ongeldige handtekening. Ook een lege pagina is gebonden aan de geschiedenis van
haar retentierevisie. Wijkt die na terugzetten af, begin dan zonder cursor bij
de oudste bewaarde rij.

Vanaf migratie 156 staat de ondertekeningssleutel in het bestand
`secrets/core/outbox.key` op de appdata-opslag. SQLite bewaart de
bestandsverwijzing en de publieke revisiekenmerken. Oude leesposities vervallen
eenmalig bij deze migratie. Zolang de sleutel ontbreekt of een ongeldige lengte
heeft, geven polling en het openen van een SSE-verbinding `409 cursor_expired`.
Controleer dan de geheimenopslag voordat je opnieuw begint.

De query accepteert uitsluitend één `cursor` en één `limit`, beide optioneel.
Onbekende, dubbele en ongeldige velden geven `422 validation_error`. Bij een
tijdelijk onbereikbare database geeft de route `503 database_unavailable`.
Wacht het aantal seconden uit `Retry-After` en gebruik dezelfde leespositie.

`source_app` is `mediaguard`. Bewaar `source_event_id` als tekenreeks. Dit
bron-ID bevat de rijpositie, de generatie en het publicatiekenmerk. De
connector berekent `event_id` als UUIDv5 in de URL-naamruimte uit
`mediaguard:<source_event_id>`. Binnen één generatie blijven beide sleutels
gelijk bij herlezing en bij een gewone herstart.

Bij een bestaande installatie verifieert MediaGuard vóór de migratie de actuele
back-up van kern en encoder, inclusief hun WAL. De alles-in-één-app houdt de
schrijvers stil tot de kernmigratie is afgerond. Elke poging krijgt een unieke
archiefnaam. Rotatie bewaart de set van de lopende upgrade.

Elke uitvoering van migratie 151 legt een nieuwe willekeurige
generatie vast. Zet je een snapshot van vóór 151 terug en voer je
de upgrade opnieuw uit, dan krijgen **alle** bewaarde gebeurtenissen nieuwe
bron-ID’s en UUID’s. Ook twee afzonderlijke publicaties op dezelfde rijpositie,
met dezelfde tijd en inhoud, krijgen zo verschillende sleutels. Bij installaties
die 151 al zonder generatie hebben uitgevoerd, voegt migratie 154 eenmalig
een generatie toe. Ook daar volgt één volledige herlezing.

Afnemers verwerken gebeurtenissen idempotent. Gebruik binnen één generatie de
UUID of het paar `(source_app, source_event_id)` voor deduplicatie. Na een
generatiewissel kan een al verwerkte gebeurtenis onder nieuwe sleutels
terugkomen. Zorg dat herhaling van die gebeurtenis hetzelfde resultaat geeft.

De SSE-route `/api/v1/events/stream` neemt de generatie op in elk bewaard
`Last-Event-ID`. Bij een verlopen of generatievreemde leespositie begint de
stream direct met `event: reset`, ook bij een lege outbox. De lege `id:` wist
de laatst ontvangen positie. Het veld `data` bevat de §5-fout met
`code: cursor_expired`, `message`, `details.restart: true` en `trace_id`.
Op dezelfde verbinding volgen alle bewaarde gebeurtenissen vanaf de oudste
rij. Een geldige leespositie hervat bij de eerstvolgende gebeurtenis. Ook
retentie van een ongelezen rij veroorzaakt eerst een reset. Bij een tijdelijk
bezette database houdt een geopende stream de laatst geleverde positie vast en
leest na herstel verder.

De classificatie is `internal`. Bekende brontopics krijgen een vast eventtype en
een vast onderwerp binnen MediaGuard. Overige records krijgen
`media.event.recorded` met ernst `info`. `payload` bevat uitsluitend `ernst`.
De connector gebruikt een vaste systeemactor en geeft geen oorspronkelijke payload
of instantie-ID door. `correlation_id` en `payload_ref` zijn `null`.

De tests in `apps/core/tests/test_connector_events.py` beproeven paginering,
retentie, sleutelrechten, redactie en terugzetten met lokale gegevens.

## Datasets en paginering

| Type | Bron | Inhoud en bestemming |
|---|---|---|
| `wanted` | Zelfde open selectie als `/api/v1/wanted` | Soort, reden, status en instantie. Link naar Ontbrekend met filters. |
| `status` | De lokale tellingen van `/api/v1/status` | Noodstop, sinds, ingeplande, lopende en gefaalde jobs en ongelezen waarschuwingen. Link naar Overzicht. |
| `downloads` | De activiteitsmeting achter `/api/v1/activity` | Status, voortgang en wachten op import. Link naar Activiteit → Downloads. |
| `av1_lanes` | De werkermeting van `/api/v1/app/status` en de ingestelde resources | Bezetting, werkelijke en ingestelde capaciteit, en pauzetoestand per baan. Link naar AV1. |
| `incidents` | De incidentbronnen van `/api/v1/attention` | Ernst, aantal, eigen oorzaakcode en `owner_app: "mediaguard"`. Een aantoonbaar instantiegebonden incident krijgt `instance_id`. |

Volg `next_cursor` met hetzelfde type totdat de cursor `null` is. Een limiet
boven 200 levert hooguit 200 items. `wanted` bladert op het persistente rij-ID.
Alle pagina's van een meting delen hun `snapshot_id`, `observed_at` en totaal.
Is de meetpagina intussen vervangen of verlopen, begin dan opnieuw zonder
cursor na `409 snapshot_changed`. De ETag is een SHA-256 van de JSON-inhoud.

Bij een onbereikbare bron geeft de pagina `status: "unknown"` en `total: null`.
Lokaal bekende incidenten blijven zichtbaar wanneer de AV1-bron uitvalt.
Na 900 seconden geeft een verlopen meting `meting_verouderd` met de oude
meettijd. Een poolincident gebruikt de laatste geslaagde poolmeting, ook als
een latere controle mislukte. Voor `wanted` telt een geslaagde bibliotheekronde
die de ontbrekende media opnieuw heeft bepaald. Zonder zo'n ronde geldt de
eerste vastgelegde constatering. Bij ontbrekende brontijd is de pagina
`unknown`. Een leesverzoek slaat geen configuratie of voortgang op.

De downloadidentiteit gebruikt het protocol, het download-ID en de bekende
client. Daardoor blijven twee clients met hetzelfde torrent-ID onderscheidbaar.
Een gekoppelde Arr-wachtrij behoudt de identiteit van de aantoonbaar bijbehorende
clientdownload. Bij een onleesbare of niet-ondersteunde downloadbron is het
totaal onbekend. Een AV1-baan vereist een gemeten capaciteit, lijst van actieve
jobs en pauzetoestand. Een gemeten capaciteit van nul is geldig.

## Bewijspakket

Gebruik `audit:<nummer>` of `job:<nummer>` als bewijs-ID. De twee reeksen zijn
onafhankelijk. Een job bevat alleen een gekoppelde auditregel wanneer de audit
de job expliciet als doel noemt en de audit na de aanmaak is vastgelegd.
Het pakket heeft UTC-tijden, een deeplink en waar beschikbaar een toegestane
`undo_hint`. Een onbekend of ongeldig bewijs-ID krijgt `404 not_found`.

Het pakket vermeldt in `details` de herkomst van de geredigeerde `request` en
`response`. Bij een auditregel gaat het om toegestane oude en nieuwe waarden.
Voor een noodstop is dat `active`. Bij een profielwijziging zijn dat de aanwezige
booleans `prefer_hdr`, `prefer_smaller` en `reject_false_audio_claims`.
Bij een job gaat het om toegestane invoer en resultaatvelden, aangevuld met
de jobstatus. Historische HTTP-body's zijn niet vastgelegd. Ontbrekende waarden
zijn `null`. Alleen vaste, gecontroleerde herstelteksten worden getoond.

Migratie 152 voegt de appversie toe aan nieuwe auditregels en jobs. Bij nieuwe
jobs blijft op het job-ID de versie bij aanmaak staan. Bij bestaande jobs
behoudt de upgrade de aangetroffen versie en toestand. Iedere nieuwe uitvoeringspoging
krijgt een eigen audit-ID met actie `job.started`, de gebruikte workerversie en
de job als doel. Het aanmaken van dit bewijs en het ophalen van de job door de
worker gebeuren in dezelfde databasetransactie.

Vóór migraties 152 en 155 verifieert MediaGuard op een bestaande installatie de
actuele herstelset. Migratie 155 bewaart de toestand en de auditkoppeling van de
dan bestaande jobbewijzen in een extra tabel. Bij hervatting houdt het historische
bewijs-ID dezelfde inhoud. De wachtrij zelf houdt de actuele voortgang bij.
Een historisch bewijsstuk zonder vastgelegde versie heeft `app_version: null`
en `details.app_version: "onbekend"`.

## Grenzen en vervolg

De itemroute accepteert `type` en optioneel `cursor` en `limit`, elk één keer.
De bewijsroute accepteert uitsluitend het ID in het pad. Een onbekend of dubbel
queryveld geeft `422 validation_error`. Bij tijdelijke database-uitval, ook
tijdens het aanmelden, antwoorden beide routes met `503 database_unavailable`.
De header `Retry-After` geeft de wachttijd in seconden.

Het contractfoutformaat geldt uitsluitend onder `/connector/v1`: `code`,
Nederlandse `message`, `details` en een UUID als `trace_id`. Connectorpaden
volgen hoofdstuk 6 zonder eindslash. Een pad met eindslash krijgt `404 not_found`
in het contractfoutformaat en bevat geen `Location`-header. De nieuwe routes
begrenzen de gehele body op 1 MiB vóór routering en authenticatie, ook chunked.
Bij een bekende overschrijding via `Content-Length` weigert de webproxy zelf en
leest de resterende invoer zonder opslag uit, zodat de client de 413 ontvangt.
Chunked invoer wordt tijdens het lezen in de kern begrensd.
De drie vaste 501-routes zijn volgens contract hoofdstuk 5 uitgezonderd van
`Idempotency-Key`. De connector biedt geen gereserveerde actie aan.

De echte Hub en MediaGuard op de NAS zijn niet beproefd.
NIET GEVALIDEERD (echte NAS), N-P6-05.

De tests staan in `apps/core/tests/test_connector_lezen.py`, de volledige
routetabelcontrole in `apps/core/tests/test_api_keys.py` en de webdoorgifteproef
in `apps/web/e2e/werk-connector-lezen.spec.ts`. De OpenAPI-export blijft bewaakt
door `apps/core/tests/test_openapi_export.py`.
