# MediaGuard installeren als standaard Custom App

**NIET GEVALIDEERD (echte NAS).** De gebruiker controleert het formulier en de installatie in
STAPPEN_P6.md, onderdeel N-P6-05.

## Vooraf

- Bevestig de TrueNAS- en ix-app-versie (D01). Het hieronder beschreven formulier hoort
  bij ix-app 1.4.8, waarvan de [app-metadata een minimum van TrueNAS SCALE 24.10.2.2 opgeeft](https://github.com/truenas/apps/blob/f142a16a1e15da5f8517658f5a9ad34400972fc4/ix-dev/stable/ix-app/app.yaml).
- Bevestig de drie datasets (D02) en vrije hostpoorten (D03). Gebruik voor nieuwe
  configuratie- en werkdatasets **Datasets → Add Dataset** met de preset **Apps**.
  Geef gebruiker **apps (568)** via **Edit Permissions** de ACL-rechten **Modify**.
  Pas rechten alleen recursief toe op nieuwe, lege datasets. Behoud op bestaande media
  de bestaande groepstoegang of geef toegang op de benodigde submappen. Houd downloads
  en media op één dataset voor hardlinks en cache en staging op dezelfde werkdataset.
- Leg bij een upgrade de bestaande app, imageversie en digest, formulierwaarden en
  datasets vast (D07). Volg vóór het wijzigen van het formulier de back-upstappen hieronder.
- De gepubliceerde verwijzing is `ghcr.io/lvos04/mediaguard-core:1.2.2-aio`. Die image
  start met `tini` als eerste proces en gebruikt de oude shell-healthcheck, die het
  statusbestand van de bewaker controleert. De [manifestbron](../manifest/mediaguard.manifest.yaml) beschrijft de nog niet
  gepubliceerde bronstand met de nieuwe healthcheck. Gebruik die manifestbron pas voor
  installatie als de release, bijbehorende tag, registerdigest en het upgradebewijs
  beschikbaar zijn. Laat de bevestiging van RPO en RTO (D16) open tot de beheerder de
  hersteldoelen heeft vastgesteld.

## Formulier

Open **Apps → Discover Apps → Custom App**. Bij een upgrade van een bestaande standaard
Custom App open je die met **Edit** en behoud je de naam, mounts en eerder bevestigde
instellingen. Als die app **Host Network** gebruikt, behoud je die keuze en laat je de
poortkoppelingen leeg. Een netwerkwijziging vraagt een aparte controle van alle koppelingen.

Draait MediaGuard nog als losse containers, oefen de overstap eerst op een aparte kopie
van de datasets en verifieer de terugval. Bewaar de bestaande installatie als terugval.
Voorkom dat beide MediaGuard-installaties tegelijk dezelfde appdata gebruiken.

| Sectie | Veld | Waarde |
|---|---|---|
| Application Name | | `mediaguard` bij een eerste installatie. |
| Image Configuration | Repository | `ghcr.io/lvos04/mediaguard-core` |
| | Tag | De exacte gepubliceerde AIO-tag met digest, als `<VERSIE>-aio@sha256:<DIGEST>`. Weigert het formulier het digestdeel, vul dan de exacte tag in. Controleer in beide gevallen de opgehaalde digest. |
| | Pull Policy | *Pull the image if it is not already present on host*. |
| General | Notes | Optioneel. Leg hier geen aanmeldgegevens vast. |
| Container Configuration | Hostname / Entrypoint / Command | Leeg bij een eerste installatie. |
| | Timezone | De bevestigde tijdzone. |
| | Environment Variables | Leeg bij een eerste installatie, tenzij je bij automatische Seerr-inrichting over bridge `MG_HOST` nodig hebt. Vul dan het bevestigde adres in waarop Seerr de fork-API's bereikt. Voer koppelingen en inloggegevens later in MediaGuard zelf in. |
| | Restart Policy | *Unless Stopped*. |
| | Disable Builtin Healthcheck / TTY / Stdin | Uit. |
| | Devices | Leeg. Gebruik de GPU-keuze onder **Resources Configuration**. |
| Security Context Configuration | Privileged | Uit. |
| | Capabilities | Voeg niets toe. |
| | Custom User | Uit. De diensten gebruiken 568:568. In de nieuwe bronstand geeft de bewaker na de opslagvoorbereiding ook zijn rootrechten op en draait web met een aparte UID met beperkte rechten. |
| Network Configuration | Host Network | Uit bij een eerste installatie. |
| | Ports → Add | TCP, containerpoort → hostpoort: `30080 → 30080` (web en API), `38989 → 38989` (Sonarr Series), `38990 → 38990` (Sonarr Anime), `37878 → 37878` (Radarr Films), `37879 → 37879` (Radarr Animefilms). Bevestig dat de poorten vrij zijn vóór het opslaan. |
| Portal Configuration | Add | Naam **MediaGuard**, HTTP, hostpoort van web en pad `/`. Gebruik de host van de app. |
| Storage Configuration | Add → Type | Maak drie **Host Path**-koppelingen. Laat **Read Only** uit. Regel de dataset-ACL vooraf via **Datasets**. |
| | Mount Path / Host Path | `/config` → kies de appdata-dataset, bijvoorbeeld `/mnt/<POOL>/apps/mediaguard`. |
| | Mount Path / Host Path | `/work` → kies de werkdataset, bijvoorbeeld `/mnt/<POOL>/mediaguard-work`. |
| | Mount Path / Host Path | `/data` → kies de dataset met media en downloads, bijvoorbeeld `/mnt/<POOL>/data`. |
| Resources Configuration | Enable Resource Limits | Uit bij een eerste installatie. Behoud eerder vastgelegde limieten bij een upgrade. |
| | GPU Configuration | Standaard uit. Gebruik Intel-render uitsluitend na bevestiging van D08. Met **Passthrough available (non-NVIDIA) GPUs** geeft het beschreven formulier alle beschikbare niet-NVIDIA-GPU's door. MediaGuard neemt de rendergroepen over bij de standaardstart. Controleer de toegang via *Ruimte → Hardware*. |

Gebruik een vertrouwd lokaal netwerk. Is aanmelding bij het register vereist, stel die
uitsluitend in bij **Apps → Configuration → Manage Container Image Registries** met
alleen leesrechten voor pakketten. Registry-login en GPU-doorgifte zijn **NIET GEVALIDEERD (echte NAS)**.

## Na het opslaan

1. Controleer de opgehaalde registerdigest tegen het releasebewijs. Lees voor de gekozen
   repository de `repo_digests` uit `app.image.query` terug via een alleen-lezen
   TrueNAS-API-controle. Stop bij een afwijking of ontbrekende teruglezing.
2. Controleer de ingebouwde healthcheck en het opstartlog. In de nieuwe bronstand
   verlaagt de healthcheck eerst haar rechten naar 568:568, zet zij
   `no_new_privs` en controleert zij het verse bewakerstatusbestand én de interne
   readiness van de kern. Er is bij een rootstart een kort venster vóór de rechtenwissel.
   Voor die vrijgegeven bronstand gebruikt de externe monitor op de webpoort
   `GET /readyz`, verwacht HTTP 200 en hanteert 10 seconden time-out en 30 seconden
   interval. Voor de gepubliceerde `1.2.2-aio` controleer je de appstatus, de portal en
   **Instellingen › Systeem & back-ups → Componenten**.
3. Maak bij een eerste installatie direct in MediaGuard de beheerder aan. Controleer
   dat het installatiescherm sluit en dat opnieuw inloggen werkt. Configureer de
   koppelingen daarna in MediaGuard en begin met controleren en vergelijken.

## Back-up en upgrade

1. Lees de releaseopmerkingen en het upgradebewijs vanaf de vorige release. Stop bij
   ontbrekend bewijs, een onbevestigde digest of een onbekende opslag- of schemaversie.
2. Leg de MediaGuard-voortgang vóór de update vast, waaronder open en afgeronde overdrachten,
   wachtrijen en herstelbare originelen. Leg per gebruiker de kijkgegevens vast via de
   bestaande mediaserver of een ondersteunde export. Neem gezien-status, hervatpunten,
   favorieten, beoordelingen, spoorkeuzes, gebruikers en rechten mee. Emby blijft actief en
   beschikbaar als terugval; een overstap van mediaserver is een afzonderlijke handeling
   die een vóór/na-rapport en uitdrukkelijk akkoord van de gebruiker vereist. De oude installatie blijft als terugval beschikbaar.
3. Stop **alleen MediaGuard** via **Apps → mediaguard → Stop**. Wacht op **Stopped**.
   Maak in **Datasets → Data Protection → Create Snapshot** een snapshot van de volledige
   Host Path op `/config`, met de SQLite-databases, de sleutels, gebruikers en alle
   Sonarr/Radarr-configuraties. Bewaar ook een herstelpunt voor `/work`, inclusief de
   originelen. Bewaar back-ups afgeschermd. Verifieer de herstelpunten in een aparte kopie
   voordat je de migratie start. Ontbreekt een bruikbaar herstelpunt, stel de upgrade uit.
   Samen met het terugzetrecept vervangt dit geen reguliere back-up van de mediabibliotheek.
4. Selecteer via **Apps → mediaguard → Edit** de nieuwe, vrijgegeven imageversie. Behoud
   dezelfde drie datasets en bestaande formulierwaarden. Na het opslaan lopen de
   applicatiemigraties bij de start. Een interne databaseback-up is aanvullend en dekt
   de core- en encoderdatabases. De datasetsnapshot bevat ook de overige appdata.
5. Controleer digest, appstatus, componenten, inloggen en de bestaande koppelingen vóór
   hervatting van het gebruik. Vergelijk de vóór-waarden voor MediaGuard-voortgang en
   de kijkgegevens van elke gebruiker met de na-waarden. Stop bij ontbrekende gegevens of een onverklaard verschil en
   behoud de bestaande mediaserver en herstelpunten.

## Terugzetten na een mislukte upgrade

1. Stop MediaGuard en wacht op **Stopped**. Bewaar de actuele `/config` en `/work`
   in herstelpunten die losstaan van de terug te rollen snapshot. Zo blijven gegevens
   van na de upgrade beschikbaar. Laat de bestaande mediaserver doordraaien.
2. Kies in de TrueNAS-webinterface **Datasets → Data Protection → Manage Snapshots**
   de bij de vorige imageversie horende `/config`-snapshot en kies **Rollback**.
   Bewaar eventuele nieuwere snapshots eerst apart als TrueNAS vraagt ze te verwijderen.
   Houd `/data` en de originelen in `/work` beschikbaar. Bij afwijkingen in het
   hersteljournaal vergelijk je de originelen met het herstelpunt van `/work` voordat
   je het gebruik hervat.
3. Herstel vervolgens in het **Custom App**-formulier de vorige imageversie met digest
   en de vorige formulierwaarden. Sla op en controleer de digest, de oude schema- en
   appversie, inloggen, de componenten en de eerder vastgelegde voortgang en kijkgegevens.
   De vorige image en de bijbehorende snapshot vormen samen één herstelpunt.
4. Ontbreekt de passende snapshot, laat MediaGuard gestopt en bewaar de huidige data.
   Laat de beheerder eerst een volledig herstelplan verifiëren. De interne back-up
   bevat bijvoorbeeld de fork-configuraties niet.
