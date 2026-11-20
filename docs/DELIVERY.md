# Leveringsgarantie: aanvragen zonder omkijken

Doel: je vraagt een serie of film aan in Seerr (of voegt hem toe in Sonarr/Radarr) en
MediaGuard zorgt dat hij **volgens jouw eisen** in de bibliotheek komt — zonder dat je iets
hoeft aan te klikken. Code: `apps/core/mediaguard/delivery/`; zichtbaar onder
**Activiteit → Aanvragen**; API `/api/v1/delivery/requests`, per aanvraag
`POST …/requests/{id}/stop` (niet meer volgen) en `POST …/requests/{id}/retry` (weer volgen
en meteen de volgende stap; niet tijdens een noodstop).

## Wat er gevolgd wordt

Elke Seerr-aanvraag (niet afgewezen) en elke titel die in de laatste 14 dagen gemonitord in
Sonarr/Radarr is gezet. Titels die de migratie-import aanmaakte (daar is 'toegevoegd' de
importdag) en titels op een instantie in proefmodus tellen niet als nieuwe aanvraag. Eén regel
per titel (TVDb-ID voor series, TMDb-ID voor films); extra aanvragen voor dezelfde titel (bv.
een nieuw seizoen) worden samengevoegd en openen een geleverde of gestopte titel opnieuw, met
nieuwe tellers. Iedere stap staat met tijd en uitleg in de tijdlijn.

| Stap | Wat MediaGuard zelf doet |
|---|---|
| **Aangevraagd / wordt toegevoegd** | Staat de titel nog niet in Sonarr/Radarr: een Seerr-aanvraag die op goedkeuring wacht wordt goedgekeurd; alleen een échte mislukking in Seerr (status 'mislukt') laat MediaGuard opnieuw versturen, hooguit 3× (na 15 min, 1 u, 3 u), daarna één informatieve melding. Een goedgekeurde aanvraag wordt nooit opnieuw verstuurd. |
| **Verdwenen** | Stond de titel al in Sonarr/Radarr en is hij daar weg, dan stopt MediaGuard met een regel in de tijdlijn en vraagt hem **niet** opnieuw aan (een bewust verwijderde titel komt niet terug). Verplaatst naar een andere instantie (anime-routering) → MediaGuard volgt hem daar. Is een instantie op dat moment onbereikbaar, dan stopt MediaGuard niet maar kijkt het later opnieuw. Een aanvraag die in Seerr verwijderd of afgewezen is, stopt ook (een al geleverde titel blijft 'geleverd'). Een nieuwe aanvraag voor dezelfde titel volgt hem gewoon weer. |
| **Juiste instantie** | Anime in Sonarr Series/Radarr Films gaat naar de anime-instantie (zelfde regels als Anime-routering: alleen zonder bestanden of lopende download). |
| **Eisen afdwingen** | Eén keer per aanvraag. Het MediaGuard-kwaliteitsprofiel wordt alleen gezet bij een titel die **ná de overname** via Seerr is aangevraagd en die Seerr zelf in Sonarr/Radarr zette. Nooit bij gemigreerde of geïmporteerde titels, een 4K-aanvraag, een profiel dat in Seerr bewust gekozen is, of een instantie in proefmodus; de reden staat in de tijdlijn. Wat je daarna zelf verandert (bv. 4K, niet bewaken) blijft staan. Aangevraagde seizoenen worden bewaakt (bij een nieuwe aanvraag opnieuw). |
| **Zoeken** | Ontbrekende (al uitgezonden) afleveringen of de film worden gezocht, binnen het zoekquotum van de instantie (elke zoekactie telt mee, net als bij backfill), met oplopende wachttijd: direct, 1 u, 3 u, 6 u, 12 u, 1, 2, 4 dagen, daarna wekelijks. RSS blijft daarnaast gewoon lopen. |
| **Nog niet verschenen** | Films die nog niet uit zijn en afleveringen die nog niet zijn uitgezonden: geen zoekopdrachten, dagelijks controleren. |
| **Downloaden** | Een mislukte download wordt verwijderd, de release **geblokkeerd** en er wordt direct een andere gezocht. |
| **Importeren** | Een import die vastzit wordt zelf bevestigd als het eenduidig is (precies één videobestand, juiste titel, geen andere bezwaren); dat telt pas als gelukt als Sonarr/Radarr de import echt deden. De leveringsmotor gooit zelf nooit een download weg die op import wacht: dat doet alleen de vastloper-controle (`imports.stuck_after_hours`, standaard 6 uur vanaf het eerste moment dat MediaGuard hem zag; tijdelijke weigeringen, uitstel door MediaGuard's eigen identiteitscontrole en proefmodus tellen niet). |
| **Controleren** | Bleek een bestand aantoonbaar de verkeerde aflevering/film (identiteitscontrole met eenduidig bewijs), dan zet MediaGuard het eerst in zijn eigen quarantaine (herstelbaar, ook voor films), laat Sonarr/Radarr het daarna uit de bibliotheek halen en zoekt de juiste release. Het bewaarde bestand volgt daarna de gewone bewaartermijn van de quarantaine. Is het bewijs niet eenduidig, dan blijft het bestand staan tot de AI of jij het beoordeelt. Kan vervangen niet (bibliotheek vergrendeld, noodstop, proefmodus, download bezig, automatisch vervangen uit), dan staat de reden erbij en wordt er niets verwijderd. |
| **Geleverd** | Alles wat is uitgezonden staat er. Seerr wordt bijgewerkt (Sonarr/Radarr-scan + beschikbaarheid), zodat de aanvraag daar 'beschikbaar' wordt; lukt dat niet, dan elk uur opnieuw (na een dag dagelijks). De tellers voor 'na 30 dagen nergens te vinden' beginnen opnieuw. Lopende series blijven gevolgd: nieuwe afleveringen gaan vanzelf. |
| **Gestopt** | Met de knop **Stop**, of vanzelf als de titel uit Sonarr/Radarr verdween of de aanvraag in Seerr verwijderd is. Ook titels die een eerdere versie bij de migratie ten onrechte als aanvraag oppikte (zonder Seerr-aanvraag) staan hier. MediaGuard doet er dan niets meer mee; **Nu opnieuw** volgt hem weer en zet meteen de volgende stap (staat hij niet meer in Sonarr/Radarr, dan laat MediaGuard Seerr hem één keer opnieuw versturen). |

## Beslissingen zonder jou

Waar vroeger een klik nodig was, beslist de AI-adviseur — met jouw toestemming en binnen het
AI-budget. De AI krijgt **alleen opties die al aan de eisen voldoen** en kan dus niets
versoepelen; een antwoord buiten die opties wordt genegeerd. Is de AI niet beschikbaar (geen
sleutel, geen budget, onzeker antwoord), dan beslist een vaste regel. Elke beslissing staat met
reden in de tijdlijn en in de audit (`delivery.ai_decision`).

Voorbeelden:
- **Anime of niet?** Vroeger een keuzelijst in 'Wacht op jou'. Nu: de AI kiest tussen
  verplaatsen en laten staan; zonder AI verplaatst MediaGuard alleen bij duidelijk bewijs
  (seriestype of genre 'Anime'). Titels met bestanden worden nooit verplaatst (dat zou de
  bestanden loskoppelen). Terugdraaien kan altijd op de Anime-pagina.
- **Vastzittende import** met meerdere bestanden: niet gokken. De vastloper-controle blokkeert
  de release na de ingestelde wachttijd en laat een andere zoeken — dat blijft binnen de eisen
  en vraagt niets.

## Wat je wél te zien krijgt

Alleen wat niemand automatisch kan oplossen, en dan als **informatieve melding** (geen vraag):
- een titel waarvoor na **30 dagen** zoeken nergens een release bestaat die aan de eisen
  voldoet (MediaGuard blijft wekelijks zoeken);
- problemen buiten de levering zelf die al elders gemeld worden: verlopen indexer- of
  Usenet-account, een volle schijf, een onbereikbare downloadclient.

## Zelf ingrijpen (hoeft niet)

- **Nu zoeken** (Aanvragen, per titel): slaat de wachttijd tot de volgende poging over en geeft
  de titel meteen een ronde. Eisen, zoekquotum en zoekpauzes blijven gelden; tijdens een
  noodstop kan het niet. API `POST /api/v1/delivery/requests/{id}/search`.
- **Niet meer zoeken** (titelpagina → *Instellingen voor deze titel*): MediaGuard zoekt de
  titel niet meer zelf, ook niet na een mislukte download. RSS van Sonarr/Radarr loopt door
  zolang de titel gemonitord is. Instelling `backfill.excluded` op de serie of film. Zet je
  het weer uit, dan zoekt de eerstvolgende ronde (binnen 10 minuten) meteen weer.

De aanvragenpagina toont per titel hoe lang hij loopt, het aantal zoekpogingen en een link
naar de titel in de bibliotheek.

## Grenzen (eerlijk)

- Een release die **nergens bestaat** kan niet geleverd worden. MediaGuard blijft zoeken en
  versoepelt de eisen nooit zelf; 'Opnieuw met versoepelde regels' blijft een bewuste keuze in
  Ontbrekend.
- Sonarr/Radarr kiezen zelf de release binnen het profiel. MediaGuard stuurt dat via het
  profiel, custom formats en blokkades, niet door zelf releases te kiezen.
- Seerr is optioneel: zonder Seerr worden alleen titels gevolgd die in Sonarr/Radarr zijn
  toegevoegd; zonder Seerr-sleutel kan MediaGuard daar ook niets bijwerken.
- Een Seerr-aanvraag die **afgewezen** is (status 3) of in Seerr verwijderd is, wordt niet
  (meer) gevolgd.
- Tijdens een **noodstop** houdt MediaGuard alleen bij wat er is aangevraagd; er wordt niets
  verwijderd, geblokkeerd, gezocht of aangepast. Op een instantie in **proefmodus** kijkt
  MediaGuard alleen mee.
- Hooguit 40 titels per ronde (elke 10 min); bij een grote achterstand duurt de eerste ronde
  langer.
- Getest met nagebootste Sonarr/Radarr/Seerr (`tests/test_delivery.py`), nog niet tegen een
  echte installatie.

## Downloadmappen opruimen

Na een levering blijft er vaak iets liggen: downloads die nooit geïmporteerd zijn, resten van
mislukte SABnzbd-jobs, mappen die geen enkele client nog kent en torrents die klaar zijn met
delen. Sonarr/Radarr ruimen alleen op wat ze zelf geïmporteerd hebben. MediaGuard doet de rest,
elke 4 uur, zonder dat je iets hoeft te doen (`mediaguard/downloads/janitor.py`, taak
`storage.download_cleanup`).

**Wat wordt opgeruimd**

| Situatie | Wanneer |
|---|---|
| Geïmporteerd door Sonarr/Radarr (historie `downloadFolderImported`) | direct bij de volgende ronde |
| Mislukt én door Sonarr/Radarr afgehandeld (`downloadFailed`: geblokkeerd, opnieuw gezocht) | direct |
| Voltooid maar nooit geïmporteerd, of mislukt en niet afgehandeld | na 7 dagen (`downloads.unimported_days`) |
| Map die geen client en geen Sonarr/Radarr kent | na 3 dagen ongewijzigd (`downloads.orphan_days`) |
| Torrent: geïmporteerd en deellimiet uit qBittorrent gehaald | torrent weg; bestanden naar de prullenbak, tenzij gekoppeld aan de bibliotheek (hardlink): dan blijven ze en wordt alleen de torrent verwijderd |
| MediaGuard's eigen `.mg-replace-*.part` van afgeronde vervangingen | na 1 dag |

**Wat nooit gebeurt**

- Niets buiten de downloadmappen van SABnzbd/qBittorrent (per MediaGuard-categorie
  `tv`, `tv-anime`, `movies`, `movies-anime`, `mso-*` plus de incomplete-map van SABnzbd).
  Een downloadmap die een bibliotheekmap raakt wordt geweigerd; symlinks worden opgelost.
- Niets wat een client nog verwerkt of wat in een wachtrij staat — ook niet in de wachtrij van
  je **oude** Sonarr/Radarr tijdens de migratie (die delen vaak dezelfde clients).
- Geen torrent vóór zijn deellimiet. Privé-trackers zonder ingestelde deellimiet in
  qBittorrent: nooit. Openbare torrents zonder limiet: na 24 uur delen (`downloads.public_seed_hours`).
- Is een client of instantie niet bereikbaar, dan wordt die ronde niets weggehaald.

**Prullenbak.** Alles gaat eerst met een `rename` naar `<downloadmap>/.mediaguard-prullenbak/<datum>/`
(zelfde schijf, niets gekopieerd) en wordt pas na 7 dagen definitief verwijderd
(`downloads.trash_days`); eerder alleen als de schijf onder 5 % vrij komt
(`downloads.min_free_percent`, oudste eerst). Tot dan staat het onder Systeem → Opslag →
*Oude downloads opruimen* en kan een beheerder het terugzetten. Elke stap staat in de audit
(`downloads.trashed`, `downloads.torrent_removed`, `downloads.purged`, `downloads.restored`,
`downloads.temp_removed`). De vrijgemaakte ruimte telt mee in *Waarom veranderden de aantallen?*.

Met een ingestelde TrueNAS-sleutel wacht MediaGuard op een volledige meting van de huidige bron.
Alle pools moeten gezond en online zijn. De nieuwste meetopdracht moet zijn opgeslagen en mag
hooguit 15 minuten geleden zijn gestart. Ook na een upgrade van de oude poolbewaking, een
bronwisseling of een schrijffout is eerst een nieuwe gezonde meting nodig. Elke meetstart
trekt de vorige toestemming ook buiten SQLite in. Na een volledige herstart wacht
de opruimer op een nieuwe gezonde meting.

De opruimer controleert dit voor elke verplaatsing en elke bestandsverwijdering. Valt de meting
tijdens het legen van een map weg, dan blijven de resterende bestanden in de prullenbak staan.
De prullenbak vermeldt een onderbroken verwijdering. Bij terugzetten van een gedeeltelijk
geleegde map krijgt de beheerder een melding dat alleen de resterende inhoud is teruggezet.
Zonder TrueNAS-sleutel gelden de gewone opruimregels hierboven. Mislukt een verwijdering,
dan blijft het item beschikbaar om terug te zetten. De audit registreert de fout en volgende
automatische rondes laten het item staan. Een handmatige verwijdering van een quarantaineorigineel
die op de poolstatus stuit, vraagt na een gezonde meting opnieuw bevestiging.

Bij een geregistreerde download reserveert MediaGuard eerst een uniek prullenbakpad.
Tijdens de afmelding blijft de download op de oorspronkelijke plek. MediaGuard vraagt de
client zijn registratie te verwijderen met behoud van de bestanden en leest de registratie
terug. Pas na bevestiging verplaatst MediaGuard de download en begint de bewaartermijn.
Een oude clientboeking zonder bewijs van afmelding blijft beschikbaar voor handmatig
terugzetten. Dat geldt ook als de verplaatsing lukt en de afsluitende boeking uitvalt.

**Meetgeldigheidsmodel voor opruimen**

| Gebeurtenis | Gevolg voor de bestanden en de downloadclient | Regressieproef |
|---|---|---|
| Geen sleutel, of een verse gezonde meting van de ingestelde bron | De gewone bewaartermijn en ruimtegrens gelden. | `test_run_once_cleans_with_healthy_pool_or_without_key` |
| Een pool is ziek, het antwoord is onvolledig of de bron is onbereikbaar | De opruimer pauzeert. Ook een vrijgegeven quarantaineorigineel blijft staan. | `test_run_once_pauses_on_unhealthy_or_unknown_pool`, `test_gemengde_poolmeting_beschermt_quarantaine` |
| De sleutel, het adres of de TLS-instelling verandert | Alle werkers wachten op een meting van de nieuwe bron, ook na een wissel terug. | `test_pool_bronwisseling` |
| Een oude meting komt later binnen of stamt van vóór de upgrade | Alleen de actuele opdracht kan publiceren. | `test_pool_volgorde_bewaking_en_prognose`, `test_pool_meting_van_voor_upgrade` |
| Een meting duurt meer dan 15 minuten | De geldigheid is gerekend vanaf de meetstart verstreken. | `test_pool_versheid_telt_vanaf_meetstart` |
| SQLite weigert de meetstart of het bewaren van de uitslag | De vorige toestemming blijft ingetrokken. | `test_meetstart_alleen_lezen`, `test_pool_publicatiefout` |
| De meetwerker mist lees- of zoekrecht op de datamap en kan het slot niet nemen | Ook na zijn vertrek behouden alle actieve werkers de download, het prullenbakbestand en de clientregistratie tot een volgende gezonde meting. | `test_slotfout_aparte_meetwerker`, `test_slotfout_tijdens_toestemmingscontrole`, `test_lezende_werker_zonder_zoekrecht_trekt_meting_in` |
| Een werker kan de ingestelde sleutel niet lezen, of alle werkers zijn herstart | De opruimer wacht op een nieuwe gezonde meting. | `test_leesfout_trekt_toestemming_in`, `test_meetstart_in_ander_proces` |
| Een startende werker kan het meetkanaal niet openen terwijl SQLite bereikbaar is | SQLite trekt de gedeelde toestemming in en de kern blijft beschikbaar. Een nieuwe gezonde meting kan opruimen weer toestaan zodra alle actieve meetwerkers een fout kunnen doorgeven. | `test_kanaal_onleesbaar_bij_start_behoudt_kern` |
| Na een kanaalfout bij de start verliest dezelfde meetwerker ook toegang tot het datamapslot | Zolang een actieve werker een volgende fout niet kan melden, geeft ook een gezonde meting van een andere werker geen toestemming. Na herstel van het kanaal of vertrek van de werker is een nieuwe gezonde meting nodig. Download, prullenbakbestand en clientregistratie blijven behouden. | `test_slotfout_na_kanaalfout_bij_start`, `test_werkerslot_onleesbaar_trekt_toestemming_in` |
| De poolmeting vervalt tijdens het verzamelen of het legen van een map | De volgende actie stopt. De resterende inhoud kan worden teruggezet. | `test_run_once_rechecks_pool_after_gathering`, `test_pool_pauze_binnen_prullenbakmap`, `test_gedeeltelijke_map_terugzetten` |
| De meting vervalt tijdens de duurzame boeking voor het eerste bestand van een map | Ook dat eerste bestand blijft staan en de map blijft volledig terugzetbaar. | `test_meetuitval_tijdens_deelboeking_behoudt_eerste_bestand` |
| SQLite weigert de reservering van een prullenbakpad | De download blijft bij SABnzbd of qBittorrent geregistreerd en op zijn oorspronkelijke plek. | `test_geweigerde_reservering_behoudt_download_en_client` |
| De downloadclient weigert de afmelding of vermeldt de download nog bij teruglezen | De download blijft op het oorspronkelijke pad. De ronde stopt en behoudt ook bij ruimtegebrek de prullenbakinhoud. Een latere ronde die de client mist, houdt de download op zijn pad. Dezelfde client kan de afmelding opnieuw bevestigen. Een eerder verplaatst bestand zonder bevestigde vrijgave blijft handmatig terugzetbaar. | `test_qbit_weigert_afmelden`, `test_sab_afmelding_na_herstart`, `test_onbevestigde_afmelding_blijft_bij_de_client`, `test_historische_clientboeking_blijft_terugzetbaar`, `test_onderbroken_vrijgave_behoudt_prullenbak` |
| De voorboeking van een verwijdering mislukt bij het vastleggen | Het bestand blijft in de prullenbak. De reservering en verwijderopdracht gebruiken een duurzame SQLite-commit. | `test_geweigerde_commit_voor_unlink_behoudt_bestand`, `test_boekingen_gebruiken_duurzame_commit` |
| De eindboeking van een verwijdering valt uit en een volgende download heeft dezelfde naam | Elke registratie reserveert haar pad, ook na herstart. De tweede download houdt zijn eigen bewaartermijn en blijft terugzetbaar. | `test_bewaartermijn_na_onderbroken_boeking` |
| Twee bestaande registraties verwijzen naar hetzelfde prullenbakpad | Zowel opruimen als terugzetten laat het bestand op zijn plaats, ook als een van de registraties al is afgesloten. | `test_dubbele_prullenbakverwijzing_behoudt_bestand` |
| Een herstelboeking, de verplaatsing of de afsluitende audit mislukt | Vóór de boeking blijft het bestand in de prullenbak. Een geboekte terugplaatsing kan na herstart worden afgemaakt. | `test_geweigerde_herstelboeking_behoudt_prullenbak`, `test_onderbroken_terugplaatsing_hervat_na_herstart` |
| Een verwijdering zonder sleutel stuit op een bestandsfout, of een handmatige quarantaineactie op een poolpauze | Het prullenbakbestand blijft beschikbaar voor herstel. De quarantaineactie vraagt een nieuwe bevestiging. | `test_verwijderfout_zonder_sleutel`, `test_quarantaine_opnieuw_bevestigen` |

API: `GET /api/v1/downloads/cleanup` (laatste ronde, totaal vrijgemaakt, prullenbak met reden),
`POST /api/v1/downloads/cleanup/run` (operator), `POST /api/v1/downloads/cleanup/trash/{id}/restore` (beheerder).

**Andere paden in de client-app.** SABnzbd/qBittorrent draaien vaak als eigen TrueNAS-app en
melden bv. `/downloads/complete/tv` waar MediaGuard `/data/downloads/complete/tv` ziet.
MediaGuard vertaalt dat zelf (`mediaguard/downloads/paths.py`): eerst via de *remote path
mappings* van Sonarr/Radarr, anders door de map te zoeken onder `/data` met dezelfde naam waarin
≥ 3 van de gemelde downloads ook staan, of die als enige hetzelfde staartstuk van het pad heeft
(bv. `complete/tv`). Gevonden vertalingen worden onthouden en elke ronde gecontroleerd. Is het
niet eenduidig, dan wordt die map overgeslagen en staat hij als *pad niet herkend* op de kaart.
Daarnaast geldt een map waarvan een client de naam kent nooit als onbekend.

Grenzen: alleen SABnzbd en qBittorrent worden direct gelezen; andere clients houden hun eigen
opruiming. Encoder-cachemappen ruimt de encoder zelf op na elke job.
