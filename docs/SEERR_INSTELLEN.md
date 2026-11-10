# Seerr koppelen aan MediaGuard

**Kort antwoord: verbind alle vier de instanties in Seerr**, niet maar één Sonarr en één
Radarr. Seerr kijkt alleen in servers die het kent om te zien of iets beschikbaar is.
Staat een anime-serie in Sonarr Anime en die server is niet gekoppeld, dan blijft de
aanvraag in Seerr eeuwig op "in behandeling" staan.

Getest met Seerr 3.5.1 (zie `tests/contract/seerr/RESULTS.md`).

## De vier servers in Seerr

Seerr → *Instellingen → Services*.

| Seerr-server | Type | Standaardserver? | Adres (TrueNAS-host) | Profiel (voorstel) | Hoofdmap (voorstel) |
|---|---|---|---|---|---|
| **Sonarr Series** | Sonarr | **Ja** (standaard) | `http://<truenas>:38989` | je gewone profiel, bv. *WEB-2160p* of *HD-1080p* | `/data/media/tv` |
| **Sonarr Anime** | Sonarr | Nee | `http://<truenas>:38990` | *Anime Dual Audio* (zie onder) | `/data/media/anime` |
| **Radarr Films** | Radarr | **Ja** (standaard) | `http://<truenas>:37878` | *UHD/HDR* of *HD-1080p* | `/data/media/movies` |
| **Radarr Animefilms** | Radarr | Nee | `http://<truenas>:37879` | *Anime Dual Audio* | `/data/media/animefilms` |

- **API-sleutel:** gebruik *Inrichten → Seerr*; dan vult MediaGuard alles zelf in. Handmatig:
  de sleutel staat in `<appdata>/secrets/<instantie>/apikey`.
- **4K-server:** zet *4K-server* uit, tenzij je bewust een aparte 4K-instantie maakt.
  MediaGuard kiest 4K al automatisch waar het profiel dat toestaat.
- **Sonarr Series:** vul ook de *anime*-velden in (animeprofiel en anime-hoofdmap) met
  dezelfde waarden als Sonarr Anime. Zo krijgt een anime-aanvraag die toch even in Series
  belandt, meteen het goede profiel.
- **Tags:** niet nodig.
- **Seizoensmappen:** aan.
- **Minimale beschikbaarheid (Radarr):** *Uitgebracht* (released).
- **Scannen inschakelen:** aan bij alle vier. Dit is nodig voor de status "Beschikbaar".

Controleren: MediaGuard → *Seerr* → **Routetest**. Die meldt per instantie of Seerr naar de
juiste URL, API-sleutel, standaardinstelling en hoofdmap wijst.

Wijkt een Seerr-server later af, dan zet MediaGuard hem zelf terug. Eerst doet Seerr zijn
eigen verbindingstest met de nieuwe waarden. Mislukt die, dan blijven de oude instellingen in
Seerr staan en zie je in *Wacht op jou* "Seerr-koppeling niet bijgewerkt".

## Wat gebeurt er met anime-aanvragen?

Seerr stuurt een aanvraag naar de **standaardserver**, tenzij iemand bij het aanvragen zelf
een andere server kiest. Dat kan alleen een beheerder of een gebruiker met het recht
"Geavanceerde aanvragen". Seerr herkent anime niet automatisch als aparte server.

MediaGuard vangt dat op met **automatische anime-routering**, die standaard aan staat:

1. Een anime-serie of anime-film komt via Seerr in Sonarr Series of Radarr Films.
2. MediaGuard herkent hem als anime aan het seriestype *anime*, het genre *Anime*, of
   genre *Animation* met oorspronkelijke taal Japans.
3. Heeft de titel nog geen bestanden en loopt er geen download, dan wordt hij verplaatst
   naar Sonarr Anime of Radarr Animefilms. Gevolgde seizoenen en afleveringen gaan mee,
   en daar wordt direct gezocht.
4. Je ziet de verplaatsing in MediaGuard bij *Anime-routering*, met de reden erbij. Er
   zijn knoppen **Terugzetten** en **Hier laten** (die titel wordt dan nooit meer
   verplaatst).

Omdat alle vier de servers in Seerr gekoppeld zijn, ziet Seerr de titel daarna gewoon als
beschikbaar.

## Profielen in MediaGuard en Sonarr/Radarr

MediaGuard bewaakt beleid **bovenop** de kwaliteitsprofielen van Sonarr en Radarr.
Bijvoorbeeld: Dual Audio-voorkeur, Nederlandse ondertitels, juiste aflevering en
Dolby Vision. Het werkt dus samen met je profielen, niet in plaats ervan.

| Instantie | Sonarr/Radarr-kwaliteitsprofiel | MediaGuard-beleidsprofiel (automatisch) |
|---|---|---|
| Sonarr Series | 2160p waar beschikbaar, anders 1080p (bv. TRaSH *WEB-2160p* + *WEB-1080p*) | Gewone series: oorspronkelijke audio, NL-ondertitels gewenst |
| Sonarr Anime | *MediaGuard · Anime Dual Audio*: 1080p én 2160p in één groep, *Dual Audio* +2000, 2160p +200 | Anime: Dual Audio (EN+JA) > Engels > Japans; audio gaat boven resolutie |
| Radarr Films | 2160p HDR/DV waar zinvol (TRaSH *UHD Bluray + WEB*), anders 1080p | Films 4K: HDR/DV behouden, NL-ondertitels gewenst |
| Radarr Animefilms | TRaSH *Anime*-profiel met *Dual Audio* | Anime: Dual Audio-voorkeur |

- Migreer je je bestaande installaties, dan worden je huidige profielen overgenomen.
- MediaGuard kan TRaSH-custom formats importeren via *Kwaliteit & TRaSH*.
- Een kinderprofiel met verplicht Nederlandse audio stel je per serie in via *Audio &
  ondertiteling*.

## Overstappen vanaf je huidige installatie

Wijzig Seerr pas bij de **overstap (cutover)** in de migratiewizard. De wizard kan de
Seerr-servers zelf omzetten naar de nieuwe adressen. Daarbij bewaart hij de oude waarden,
zodat **Terugdraaien** alles in Seerr exact herstelt. Wil je het liever met de hand doen,
volg dan de tabel hierboven en voer daarna de routetest uit.
