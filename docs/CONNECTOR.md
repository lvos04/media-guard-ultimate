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
`maintenance.retention_days_events`. De cursor bindt de laatste positie, de
bron van die rij en de retentierevisie aan een handtekening. Verwijdert retentie
sinds die revisie een rij op of voorbij de leespositie, dan volgt
`409 cursor_expired` met `details.restart: true`. Dit antwoord geldt ook voor
een oude ongetekende cursor, een vervalste combinatie of een cursor die na
snapshotherstel bij een andere geschiedenis hoort. Begin dan zonder cursor en
behoud de eerder verwerkte event-ID's voor deduplicatie.

De query accepteert uitsluitend één `cursor` en één `limit`, beide optioneel.
Onbekende, dubbele en ongeldige velden geven `422 validation_error`. Bij een
tijdelijk onbereikbare database geeft de route `503 database_unavailable`.
Wacht het aantal seconden uit `Retry-After` en gebruik dezelfde leespositie.

`source_app` is `mediaguard`. Bewaar `source_event_id` als tekenreeks. De
connector leidt `event_id` af als UUIDv5 in de URL-naamruimte uit
`mediaguard:<bron-id>`. Migratie 151 behoudt de SQLite-rijposities. Bij een
eerste overgang krijgt iedere bewaarde rij een reproduceerbare bronidentiteit
uit de oorspronkelijke rij. Een installatie die 151 al toepaste behoudt haar
eerder uitgegeven numerieke bron-ID's. Voor nieuwe rijen legt migratie 153 per
publicatie een willekeurig kenmerk vast in dezelfde transactie. Het
bron-ID combineert dit kenmerk met de positie en blijft gelijk bij herlezing.

Een oude schrijver legde de tijd tot op de seconde vast. Als hij vóór twee
afzonderlijke upgrades op dezelfde rijpositie precies dezelfde gebeurtenisgegevens
schrijft, kan een kale snapshot de twee publicaties niet onderscheiden. Voor
dit historische geval kan de afnemer na een expliciete cursorherstart dezelfde
deduplicatiesleutel ontvangen. De garantie voor rijen met een bij publicatie
vastgelegd kenmerk geldt vanaf migratie 153.

De SSE-route `/api/v1/events/stream` koppelt `Last-Event-ID` aan de database
waarin die leespositie is uitgegeven. Na een tweede upgrade van een oude
snapshot leest een bestaande client alle bewaarde gebeurtenissen opnieuw.

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
meettijd. De connector houdt de huidige installatiestatus en instellingen
tijdens deze waarnemingen alleen ter inzage.

## Bewijspakket

Gebruik `audit:<nummer>` of `job:<nummer>` als bewijs-ID. De twee reeksen zijn
onafhankelijk. Een job bevat alleen een gekoppelde auditregel wanneer de audit
de job expliciet als doel noemt en de audit na de aanmaak is vastgelegd.
Het pakket heeft UTC-tijden, een deeplink en waar beschikbaar een toegestane
`undo_hint`. Een onbekend of ongeldig bewijs-ID krijgt `404 not_found`.

Het pakket vermeldt in `details` de herkomst van de geredigeerde `request` en
`response`. Bij een auditregel gaat het om toegestane oude en nieuwe waarden.
Bij een job gaat het om toegestane invoer en resultaatvelden, aangevuld met
de jobstatus. Historische HTTP-body's zijn niet vastgelegd. Ontbrekende waarden
zijn `null`. Alleen vaste, gecontroleerde herstelteksten worden getoond.

Migratie 152 voegt de appversie toe aan nieuwe auditregels en jobs. De jobversie
begint bij aanmaak en wordt bij uitvoering bijgewerkt naar de versie van die
poging. Bestaande rijen houden hun oorspronkelijke inhoud. Een historisch
bewijsstuk zonder vastgelegde versie heeft `app_version: null` en
`details.app_version: "onbekend"`.

## Grenzen en vervolg

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
