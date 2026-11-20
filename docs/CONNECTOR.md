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
| `GET /connector/v1/items?type=&cursor=&limit=` | Zes datasets met vaste projecties, UTC-meettijd, deeplink en ETag |
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

## Verzoeken correleren

Stuur dezelfde `X-Correlation-Id` mee bij aanvragen die bij één diagnose horen.
MediaGuard geeft een UUID in de vorm `8-4-4-4-12` hexadecimale tekens letterlijk
terug in de antwoordkop, ook bij een kernfout. Bij een ontbrekende, ongeldige
of dubbele kop maakt de kern een nieuwe UUID per verzoek. De ongeldige
waarde komt niet in de log of audit.

Bij sleuteluitgifte, sleutelintrekking en bediening van de noodstop legt
MediaGuard de antwoordwaarde ook vast in `audit_events.correlation_id`.
Bij herkoppelen van de mobiele app delen intrekking en uitgifte dezelfde
waarde. De interne correlatie met de AV1-engine loopt via het bestaande
veld `correlation_id` in de body.

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

## Herstelrapport

`GET /connector/v1/items?type=backups` geeft één rapport met `observed_at`,
`ETag` en een deeplink naar de back-ups van MediaGuard. Het paginaveld
`status: ok` bevestigt dat de meting beschikbaar is. Gebruik `items[0].status`
voor de dekking. Zodra één onderdeel `degraded` is, is de totale dekking
ook `degraded`. `age_seconds` geeft de leeftijd op de meettijd aan.
MediaGuard deelt een meting maximaal 60 seconden tussen lezers.

- `core` en `encoder` tonen de laatste geverifieerde kopieën, inclusief
  sha256 en de migratieversies van de opgeslagen SQLite-databases. Ook
  geverifieerde sets van vóór een update tellen mee. Vanaf meer dan 48 uur
  is de dekking verouderd. MediaGuard zet de set terug op lege tijdelijke
  appdata met de offline wissel van de supervisor. Daar controleert het de
  cursorsleutel en leest het de primaire wachtrij met de SQL van de encoder.
  Bij twee manifestnamen wordt de set geweigerd. Een oudere kopie krijgt
  dezelfde proef. Ontbreekt een bruikbare kernkopie, dan blijft de kernhash
  leeg. Losse AV1-back-ups kunnen ook worden hersteld naast de huidige kern.
  Het rapport baseert die dekking op een proef met een kopie van die kern.
- `forks` geeft per geregistreerde, niet-gearchiveerde instantie de naam
  en datum van de nieuwste herkenbare back-up. MediaGuard haalt het
  bestaande archief via GET op en controleert de zip, configuratie en
  SQLite-integriteit tijdelijk in een afgeschermde map. Ontbrekende of
  oncontroleerbare kopieën geven `degraded`.
- `bundle` toont de publieke sleutelvingerafdruk. MediaGuard verifieert
  de bundel en het volledige herstelsleutelbestand tegen de SHA-256 die
  bij aanmaak is vastgelegd. Beide moeten overeenkomen met de offsite-set.
  Vóór versleuteling pakt MediaGuard dezelfde archiefinhoud uit met de
  herstelroutine. Op die lege appdatamap controleert het de start van de kern,
  ontsleutelt het de opgeslagen koppelingen en leest het de AV1-taakvoortgang.
  De geauthenticeerde bundelkop vermeldt de uitkomst en de proefversie.
  De herstelcode blijft nodig om uit te pakken.
  Een gedeeltelijke bundel en een oudere bundel zonder deze inhoudscontrole
  blijven handmatig uitpakbaar en krijgen lokaal en offsite `degraded`.
- `offsite` toont het soort doel en de meetgegevens van de laatste
  kopie. Bij een nieuwe waarneming leest MediaGuard de twee bestanden
  terug uit de huidige doelruimte. Een doelwijziging of reset maakt het
  bewijs en de gedeelde waarneming direct ongeldig. Voor e-mail is
  teruglezing onbekend en blijft de dekking `degraded`. Voor bundel en
  offsite geldt de leeftijdsgrens van 48 uur.
- `rollback` beschrijft of MediaGuard terugzetten van een pre-updateset
  bij stoppen ondersteunt en of zo'n herstel is klaargezet. Een ongeldige
  markering of mislukte proef met de actueel klaargezette bestanden
  geeft `staged: null` en totale dekking `degraded`. De Hub
  kan uitsluitend het rapport en de deeplink gebruiken.

Direct na herstel van een kernkopie van vóór de offsite-verificatie geeft het
rapport HTTP 200 met `degraded` voor offsite. Een bevoegd rapportverzoek laat
ook de gebruikstijdstippen van de sessie en de API-sleutel ongemoeid.

Testnamen zonder bestandspad verwijzen naar `test_connector_lezen.py`.

| Onderdeel | Controle voor herstel en rapport | Regressieproeven |
|---|---|---|
| Bundel | De twee hashes binden bundel en verpakte sleutel aan het aanmaakbewijs. De uitgepakte kopie doorloopt de startcontrole en levert de koppelingen en de percentages van twee taken. | `test_onherstelbare_bundel[sleutelbestand]`, `test_onherstelbare_bundel[bundelinhoud]`, `test_herstelde_bundel_is_bruikbaar` en `test_oud_aanmaakbewijs_bewijst_geen_bundelinhoud`, plus `test_proefherstel.py::test_bundel_na_uitpakken_startbaar` |
| Encoder | Bij klaarzetten en opnieuw vóór omwisselen leest de wachtrijopdracht de taakpercentages uit de teruggezette primaire database. | `test_encoder_zonder_actieve_voortgang`, `test_encoder_mist_tabel_van_geregistreerde_versie` en `test_klaargezette_primaire_database_behoudt_taken`, plus `test_proefherstel.py::test_primaire_kopie_behoudt_leesbare_taakvoortgang` |
| Kern | De herstelcontrole toetst de migraties, de doelpaden en de volledigheid van de set. Bij een geweigerde set toont het rapport de hash van een bruikbare kopie als die er is. | `test_kernschema_en_herstelroute`, `test_set_met_afwijkend_herstelpad` en `test_deels_gemaakte_set_met_andere_geldige_kopie`, plus `test_proefherstel.py::test_twee_manifesten_naast_elkaar` |
| Offsite-overdracht | De kopieerfunctie en het rapport lezen bundel én sleutel terug en vergelijken lengte en SHA-256. | `test_offsite_bewijs_voor_huidig_doel[sleutelupload]` en `test_offsite_sleutel_verdwijnt_op_hetzelfde_doel` |
| Offsite-doel | Het bewijs geldt voor de huidige generatie van doelinstellingen en inloggegevens. Een reset vereist teruglezing na een nieuwe kopie, ook bij dezelfde doelwaarden. Een historische kernkopie zonder generatie levert geen dekking op. | `test_offsite_bewijs_voor_huidig_doel[doelreset]`, `test_offsite_reset_naar_dezelfde_doelwaarde_vervalt_direct` en `test_direct_herstel_voor_migratie_120` |
| Fork | Inspectie en herstelvoorbereiding gebruiken dezelfde XML-parser voor `config.xml` met hoofdelement `Config`. | `test_fork_met_lege_config` |
| Terugzetten | Dezelfde voorcontrole als bij de omwisseling toetst de markering, de voortgang en de klaargezette bestanden. | `test_onvolledige_terugzetmarkering` en `test_terugzetrapport_controleert_klaargezet_bestand` |

De historische proef `tests/upgradehistorie/proeven/bundelpaar.py` laat de oude
release zelf een bundel maken. De huidige release herstelt die lokaal en vanaf
het offsite-doel. Zonder aanmaakbewijs blijven beide dekkingen `degraded`.

Een `null` betekent dat het gegeven niet kon worden vastgesteld. Het
rapport bevat uitsluitend geselecteerde metadata. De bestaande route
`GET /api/v1/notify/offsite` vereist een beheerderssessie.

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
eenmalig bij deze migratie. De kern controleert de eigenaar, de lengte en de
rechten (0700 voor de map en 0600 voor het bestand). Bij een onbruikbare
sleutel geven polling en het openen van een SSE-verbinding `409 cursor_expired`.
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
archiefnaam die ook de vorige supervisor herkent. De startmelding en
`MG_PRE_UPDATE_SET` wijzen naar de set die de kern onder schrijfslot heeft
geverifieerd. Rotatie bewaart de set van de lopende upgrade.

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
leest na herstel verder. Is de database al bij de sessie- of API-sleutelcontrole
bezet, dan antwoordt de route vóór het openen met `503 database_unavailable`
en de wachttijd in `Retry-After`.

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
`unknown`. De oudste meting van alle open rijen en van actieve bronnen zonder
open rijen bepaalt de versheid op elke pagina. Een actuele nul vereist een
geslaagde synchronisatie die de ontbrekende media werkelijk heeft geteld.

Een zichtbare download houdt haar uitgegeven connector-ID. MediaGuard herkent
de overdracht aan de Arr-wachtrijrij, de instantie met het bron-ID, of het
clientitem. Een gewijzigde inrichting of het verschijnen of verdwijnen van de
bijbehorende Arr-rij verandert dat ID niet. Melden twee clients dezelfde
torrent, dan kan de Hub de voortgang van elk afzonderlijk blijven volgen.
Meldt Arr die torrent zonder clientnaam, dan houdt de later eenduidig herkende
client haar eigen ID wanneer de activiteit de twee meldingen samenvoegt.
De downloadclient bevestigt een overdracht via haar eigen wachtrij. Is die
wachtrij leeg, dan behoudt een herkenbare Arr-rij haar ID, ook als ze de
clientnaam van een inmiddels vertrokken Arr-rij gaat melden.

Bij de eerste meting gebruikt de connector de historische ID-vorm van de
installatie. De oorspronkelijke enige client van een protocol kan het ID uit
protocol en bron-ID behouden. Voor benoemde downloads op schema 156 hoorde de
oorspronkelijk gemelde clientnaam bij het ID. De lokale clientnaam en de door
MediaGuard ingestelde Arr-naam herkennen daarna dezelfde overdracht.

De kern bewaart de ID's, de meettijd en hashes van de herkenningsgegevens
van de actuele overdrachten in `connector-download-ids.json` in de kerndatamap.
Dit bestand is gedeeld door de kernprocessen en gaat mee in de versleutelde
herstelbundel. Een onvolledige of verouderde meting behoudt de laatste koppelingen.
Na een volledige actuele meting vervallen de koppelingen van verdwenen downloads.
De beheerder slaat de inrichting, de bijbehorende sleutels, de historische
toewijzing per protocol en de auditregel in één transactie op. Bij een opslagfout
geeft de PUT een 503 en blijft de oorspronkelijke inrichting beschikbaar.
Een onleesbare configuratie of een wachtrijrij zonder identiteit geeft een
onbekend totaal. De AV1-meting vereist één CPU-rij en één GPU-rij, elk met
capaciteit, actieve jobs en pauzetoestand.
Een gemeten capaciteit van nul is geldig.

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

Migratie 157 verifieert de actuele herstelset en bewaart de geredigeerde inhoud
van de bestaande job- en auditbewijzen. Onderhoud mag de operationele rijen
opruimen. Hun bewijs-ID’s blijven opvraagbaar. Bij hervatting krijgt de
afgesloten poging een afzonderlijk audit-ID met actie `job.finished`, eindtijd,
status en de beschikbare resultaattellers. De wachtrij houdt de actuele voortgang bij.
Ook een planpauze die een wachtende job annuleert, legt een eigen
`job.finished` vast met de job als doel. De planpauze, annulering en het
eindbewijs worden samen opgeslagen. Het historische jobpakket en het eindbewijs
blijven na onderhoud onder hun eigen ID leesbaar. Bij herplannen of opruimen
van een eerder geannuleerde job bewaart MediaGuard een nog aanwezige eindtijd
en status onder een eigen audit-ID als dit eindbewijs ontbreekt. De versie
van die eerdere annulering blijft onbekend.
Een historisch bewijsstuk zonder vastgelegde versie heeft `app_version: null`
en `details.app_version: "onbekend"`.

Vanaf schema 154 of ouder bewaart migratie 157 de destijds leesbare
resultaattellers en de oorspronkelijke lege request en response van profielbewijzen.
Bij een al toegepaste migratie 155 blijft het toen afgegeven jobpakket beschikbaar.
Verschilt het nog aanwezige eindresultaat van het pakket, dan komt dat resultaat
onder een eigen `audit:<nummer>` met de job als doel in de audittijdlijn.

De nooit uitgebrachte tussenrevisies `324d935` en `2393f52` gebruikten
hetzelfde schema 155. Bij een opgeslagen profielbewijs
met toegestane booleans, een geconfigureerde downloadclient of een beschikbare
Arr-wachtrij kan hetzelfde
schema twee verschillende antwoorden hebben gegeven. De upgrade stopt in dat
geval vóór wijzigingen aan het schema. De herstelset bevat de oorspronkelijke
database. Deze beperking geldt voor zulke lokale ontwikkelkopieën. Voor
voortzetting is hun oorspronkelijke kernrevisie nodig. De gepubliceerde
release 1.2.2 gebruikt schema 100.

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
