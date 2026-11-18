# Connector — alleen lezen

S-P3-610 levert de eerste helft van P3-01, volgens contract 1.3 hoofdstukken 5 en 6.
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
| `GET /connector/v1/discover` | Dienst, bronversie, schemaversie, `read`, rol `viewer`, lege `item_types` en `actions`, maximale meetleeftijd 900 seconden |
| `GET /connector/v1/health` | Checks `database`, `worker`, `jobs`, `encoder` en `instances`; totaal is de slechtste check |
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
`maintenance.retention_days_events`. Verwijdering legt in dezelfde transactie de
hoogste verwijderde positie en een revisie vast. Een ongeldige parameter geeft
`422 validation_error`. Als sinds een cursor een verwijdering op of voorbij de
leespositie kan zijn opgetreden, volgt `409 cursor_expired` met
`details.restart: true`. Laat de cursor dan weg en dedupliceer de opnieuw gelezen
gebeurtenissen op `event_id`. De controle is ook geldig bij een lege outbox en bij
gaten tussen bewaarde rijen. Begin na herstel van een oudere databasesnapshot
opnieuw zonder cursor of eerder opgebouwde deduplicatiestand van die bron.

`source_app` is `mediaguard` in de envelop. `source_event_id` bevat het bron-ID
als tekenreeks. `event_id` is UUIDv5 in de URL-naamruimte met de naam
`mediaguard:<bron-id>`. Migratie 151 bewaart bestaande bron-ID's en voorkomt
heruitgifte van automatisch toegekende ID's binnen dezelfde databasegeschiedenis.
De classificatie is `internal`. Bekende brontopics krijgen een vast eventtype en
een vast onderwerp binnen MediaGuard. Overige records krijgen
`media.event.recorded` met ernst `info`. `payload` bevat uitsluitend `ernst`.
De connector gebruikt een vaste systeemactor en geeft geen oorspronkelijke payload
of instantie-ID door. `correlation_id` en `payload_ref` zijn `null`.

De tests in `apps/core/tests/test_connector_events.py` beproeven paginering,
retentie, sleutelrechten, redactie en terugzetten met lokale gegevens.

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

Items, bewijs en de vergelijking van vijf datasets volgen in S-P3-615. De echte
Hub en MediaGuard op de NAS zijn niet beproefd. NIET GEVALIDEERD (echte NAS).

De tests staan in `apps/core/tests/test_connector_lezen.py`, de volledige
routetabelcontrole in `apps/core/tests/test_api_keys.py` en de webdoorgifteproef
in `apps/web/e2e/werk-connector-lezen.spec.ts`. De OpenAPI-export blijft bewaakt
door `apps/core/tests/test_openapi_export.py`.
