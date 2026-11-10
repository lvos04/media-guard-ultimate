# Automatische inrichting

> "Ik wil niets hoeven instellen in de Sonarr- en Radarr-instanties; alles wordt geregeld
> door MediaGuard."

MediaGuard richt de vier instanties (**Sonarr Series**, **Sonarr Anime**, **Radarr Films**,
**Radarr Animefilms**), Prowlarr en Seerr zelf in en houdt ze zo. Je voert alles **één keer**
in, in MediaGuard → **Inrichten**. De eigen schermen van Sonarr en Radarr heb je alleen nog
nodig als je iets wilt nakijken.

## 1. Wat je één keer invoert

| Onderdeel | Wat je invult | Waar het terechtkomt |
|---|---|---|
| **Downloadclient(s)** | Soort (SABnzbd, NZBGet, qBittorrent, Transmission of Deluge), host, poort, SSL, URL-basis, gebruikersnaam, API-sleutel en/of wachtwoord | In alle vier de instanties, elk met een eigen categorie |
| **Indexers**, kies één van twee | **Prowlarr**: URL + API-sleutel. **Direct**: per indexer Newznab of Torznab, URL en API-sleutel | Prowlarr: de vier instanties worden *applicaties* in Prowlarr. Direct: de indexers komen in de instanties die erbij horen |
| **Mediapaden** (optioneel) | Standaard `/media/tv`, `/media/anime`, `/media/movies` en `/media/animefilms` | Rootmap per instantie |
| **Seerr** (optioneel) | URL + API-sleutel | De vier Seerr-servers |

Geheimen (wachtwoorden en API-sleutels) worden versleuteld opgeslagen. Je kunt ze daarna
nergens meer uitlezen. MediaGuard laat alleen zien of ze **ingesteld** zijn. Laat je een
geheim veld leeg, dan blijft de opgeslagen waarde staan. Wis je het veld, dan wordt het
geheim verwijderd.

Ontbreekt er iets, dan staat het bovenaan het scherm *Inrichten*, bijvoorbeeld
"downloadclient-wachtwoord ontbreekt voor 'SAB'" of "Kies Prowlarr of voer indexers in".

## 2. Wat MediaGuard waar instelt

Alles wat MediaGuard maakt, heeft een naam die begint met **`MediaGuard · `**. Zo weet
MediaGuard wat van hem is. Er worden geen tags op downloadclients of indexers gezet, omdat
Sonarr ze dan alleen nog voor series met die tag zou gebruiken.

| Sectie | Per instantie |
|---|---|
| Tags | `mediaguard`; op de anime-instanties ook `anime` |
| Mediapaden | De rootmap. MediaGuard controleert eerst of de map in de container bestaat (via `/api/v3/filesystem`) |
| Mediabeheer | Hardlinks **aan**, propers/repacks *voorkeur en upgraden*, prullenbak **uit** (MediaGuard heeft zijn eigen quarantaine), "verwijderde afleveringen niet meer volgen" **uit**, ondertitels (`srt,ass,ssa,sub,idx`) mee importeren |
| Naamgeving | Hernoemen **aan**, in TRaSH-stijl. De anime-instantie gebruikt **absolute nummering** (`{absolute:000}`). Geldt alleen voor nieuwe imports |
| Kwaliteitsdefinities | Verstandige minimale en maximale bestandsgroottes |
| Custom formats | Dual Audio, Engelse dub, Anime raws, slechte anime-groepen, Upscaled, HDR, Dolby Vision, Taal: Nederlands |
| Kwaliteitsprofielen | **Series**: *MediaGuard · WEB-2160p* (WEB-2160p > WEB-1080p). **Anime**: *MediaGuard · Anime Dual Audio* (1080p, Dual Audio +2000, Engelse dub +500, raws −1000, slechte groepen −10000). **Films**: *MediaGuard · UHD HDR* (UHD met HDR/DV > 1080p, taal *Original*). **Animefilms**: anime-profiel met taal *Any*. Sonarr Series krijgt ook het animeprofiel, zodat de anime-velden in Seerr een geldig profiel hebben |
| Vertragingsprofiel | Usenet krijgt voorrang als je een usenet-client hebt, anders torrent. Geen vertraging |
| Downloadclients | Jouw clients. Categorie per instantie: `tv`, `tv-anime`, `movies`, `movies-anime` (aan te passen) |
| Indexers (direct) | Een indexer met bereik **normaal** komt op Series en Films, **anime** op Anime en Animefilms, **beide** op alle vier. Anime-indexers (bijv. Nyaa, AnimeTosho) gaan automatisch alleen naar de anime-instanties |
| Webhook | De MediaGuard-webhook voor events |
| Prowlarr | Vier applicaties met de juiste sync-categorieën en tag `normal` of `anime`. MediaGuard zet dezelfde tags op de Prowlarr-indexers: anime-indexers krijgen `anime`, andere krijgen allebei. Daarna start MediaGuard een indexer-synchronisatie |
| Seerr | Zie hieronder |

Wil je de volledige TRaSH-set gebruiken, importeer die dan via *Kwaliteit & TRaSH*. Kies
daarna per sjabloon welke formats MediaGuard ook naar de instanties brengt
(`quality.extra_trash_formats`).

### Seerr

MediaGuard stelt Seerr in volgens [SEERR_INSTELLEN.md](SEERR_INSTELLEN.md), via de eigen
API van Seerr en **pas nadat je dat bevestigt**:

- **Sonarr Series** is de standaardserver. De anime-velden zijn ingevuld met het animeprofiel
  van die instantie.
- **Sonarr Anime** is geen standaardserver en heeft seriestype *anime*.
- **Radarr Films** is de standaardserver, met minimale beschikbaarheid *released*.
- **Radarr Animefilms** is geen standaardserver.
- Bij alle vier staan **scannen** en seizoensmappen aan.

Wat MediaGuard nooit doet:

- Seerr-servers die naar je **oude** installatie wijzen, wijzigen of verwijderen.
  MediaGuard meldt ze alleen. Is je oude server nog de standaardserver, dan blijft dat zo
  tot de cutover.
- Seerr aanpassen terwijl een migratie in de **schaduwfase** zit.

## 3. Inrichten, controleren en herstellen

1. **Bekijk** de wijzigingen (dry-run). Je ziet een leesbare lijst per instantie en per
   sectie, bijvoorbeeld "Mediabeheer bijwerken (Hardlinks gebruiken: False → True)".
2. **Toepassen** werkt pas als je bevestigt. Daarna leest MediaGuard alles opnieuw in om te
   controleren dat het gelukt is.
3. **Afwijkingen** worden standaard elke 6 uur gecontroleerd. Per instantie en per sectie
   zie je dan *in orde*, *afwijking*, *ontbreekt*, *fout* of *invoer nodig*.
4. **Automatisch herstellen** staat standaard aan. Dit herstelt alleen onderdelen van
   MediaGuard en de essentials. Er wordt nooit iets verwijderd, en Seerr wordt nooit
   automatisch aangepast.

Wat MediaGuard nooit aanraakt:

- Items die je zelf hebt toegevoegd, zoals een eigen indexer. Die verwijdert MediaGuard
  alleen als je voor die sectie **"beheer volledig door MediaGuard"** aanvinkt. Dat kan bij
  downloadclients, indexers en custom formats. Ook dan gebeurt het alleen bij handmatig
  toepassen.
- Items die je markeert als **"door mij beheerd"**.
- Secties die je per instantie **uitzet**.

Bij het **opstarten** zorgt MediaGuard voor de essentials (webhook, mediapaden, tags). Een
nieuwe installatie werkt dus zonder handmatige stap zodra je je downloadclient en indexers
hebt ingevoerd.

In **schaduwmodus** en bij een **gepauzeerde** instantie zet MediaGuard nooit RSS,
automatisch zoeken of downloadclients aan.

## 4. Optimaliseren na migratie

Je oude Sonarr/Radarr-instellingen worden na de migratie **niet** zomaar overgenomen.
Zodra de schaduwimport van een instantie klaar is, staat die in de wachtstand
**"Gemigreerd: wacht op 'Optimaliseren na migratie'"**. In die stand richt MediaGuard alleen
de essentials in.

**Wat blijft precies hetzelfde:** alle series en films, alle gevolgde-vlaggen (serie,
seizoen, aflevering), "nieuwe seizoenen volgen", uitzonderingen per aflevering, tags op
items, de historie, bestanden en hoe ze gekoppeld zijn, de inloggegevens en URL's van
indexers en downloadclients, remote path mappings en rootmappen met media.

**Wat MediaGuard verbetert:**

- Kwaliteitsprofielen en custom formats, kwaliteitsdefinities, vertragingsprofiel, taal,
  naamgeving (alleen voor nieuwe imports), mediabeheer en de webhook.
- Oude releaseprofielen worden **uitgezet**, niet verwijderd.
- De downloadcategorie wordt per instantie ingesteld. Het wachtwoord of de API-sleutel blijft
  ongewijzigd.
- Een anime-indexer op een niet-anime-instantie wordt **uitgezet** en krijgt de naam
  "… [uit: anime-indexer]". De inloggegevens blijven. Door de nieuwe naam zet de cutover hem
  niet terug aan.
- Elke serie of film krijgt het passende **nieuwe profiel**. Een serie van het type *anime*
  in Sonarr Series krijgt het animeprofiel.
- De oude profielen heten voortaan **"Oud · <naam>"**. Ze blijven staan tot je **Opruimen**
  bevestigt. Profielen die nog in gebruik zijn, worden nooit verwijderd.

**Eerst een voorvertoning:** per instantie en per sectie zie je wat er verandert. Daarnaast
zie je de impact:

- hoeveel bestanden en titels nog niet aan het nieuwe profiel voldoen (upgradekandidaten);
- het geschatte aantal zoekacties;
- hoeveel dagen dat ongeveer duurt.

Upgradezoekacties lopen via de **backfill-quota**. MediaGuard start zelf geen zoekacties, dus
je indexers worden nooit overspoeld.

**Momentopname en terugzetten:** voordat MediaGuard iets wijzigt, legt het de volledige
gemigreerde configuratie vast. Dat zijn de providers, profielen, custom formats,
naamgeving, mediabeheer, vertragings- en releaseprofielen, kwaliteitsdefinities en welk
profiel elk item heeft. Met **"Terugzetten naar gemigreerde instellingen"** komt dat exact
terug, ook als je al hebt opgeruimd. Geheimen worden daarbij hergebruikt en nooit getoond.
Daarna staat de instantie weer in de wachtstand.

**Bestanden hernoemen** gebeurt nooit automatisch. Het is een aparte actie:

1. MediaGuard toont een voorvertoning op basis van de rename-API van Sonarr/Radarr, met
   oude en nieuwe paden.
2. Je bevestigt.
3. Pas dan stuurt MediaGuard het commando *RenameFiles*, alleen voor de bestanden die je
   hebt bevestigd.

Bestanden waar een AV1-taak of herstelactie op loopt (een lock), slaat MediaGuard over.

## 5. Wat nog een hostactie vraagt

Voor de inrichting zelf is geen terminal, YAML of SQL nodig. Wel moeten de mediamappen op
TrueNAS goed gekoppeld zijn:

- **De TrueNAS-datasets moeten in de fork-containers op de mediapaden staan**
  (`MG_MEDIA_*` in de app-configuratie). Ontbreekt een map, dan meldt MediaGuard "De map …
  bestaat niet in de container van …".
- Sonarr/Radarr willen een **schrijfbare** rootmap. Heeft de gebruiker 'apps' (uid 568) geen
  schrijfrechten op een bestaande mediamap, dan mislukt het toevoegen van de rootmap en meldt
  MediaGuard welke dataset rechten nodig heeft (mediaguard-init past bestaande mediamappen
  bewust niet aan).
- **Hardlinks** werken alleen als de downloadmap en de mediamap op hetzelfde bestandssysteem
  en in dezelfde mount staan. Anders kopieert Sonarr/Radarr gewoon. Er gaat dan niets mis,
  maar het kost wel extra ruimte.

## 6. Techniek (voor beheerders)

**API** (`/api/v1/provisioning`):

| Methode | Pad | Doel |
|---|---|---|
| GET | `/status?refresh=` | Status per instantie en per sectie, plus wat er nog nodig is |
| POST | `/plan` | Dry-run |
| POST | `/apply` | Toepassen (`confirm`) |
| GET, PUT | `/config` | Centrale configuratie |
| GET | `/items` | Eigenaarschap van items |
| PUT | `/items/ownership` | Eigenaarschap van een item wijzigen |
| GET | `/runs` | Uitgevoerde runs |
| POST | `/drift-check` | Afwijkingscontrole nu uitvoeren |
| POST | `/downloadclients/test` | Downloadclient testen |
| POST | `/indexers/test` | Indexer testen |
| POST | `/prowlarr/test` | Prowlarr testen |
| POST | `/paths/validate` | Mediapad controleren |
| POST | `/seerr/apply` | Seerr inrichten |
| GET, POST | `/optimize-migrated` | Status en voorvertoning; toepassen met `confirm` |
| POST | `/optimize-migrated/cleanup` | Oude profielen opruimen |
| POST | `/optimize-migrated/rollback` | Terugzetten naar gemigreerde instellingen |
| POST | `/rename/preview` | Voorvertoning bestanden hernoemen |
| POST | `/rename/apply` | Bestanden hernoemen |

**Instellingen:**

- `provisioning.on_startup` (aan)
- `provisioning.drift_check_enabled` (aan)
- `provisioning.drift_interval_hours` (6)
- `provisioning.auto_repair` (aan)
- `provisioning.startup_delay_seconds` (30)

**Tabellen** (migraties 025–028): `provisioning_config`, `provisioning_items`,
`provisioning_runs`, `provisioning_status`, `provisioning_snapshots` en
`provisioning_optimize`.

**Geheimen** staan onder:

- `downloadclient:<naam>:apikey` en `downloadclient:<naam>:password`
- `indexer:<naam>:apikey`
- `prowlarr:apikey`
- `provisioning:snapshot:*`

Voor Seerr worden de bestaande `seerr.url` en `seerr:apikey` gebruikt.
