# MediaGuard installeren als standaard Custom App

**NIET GEVALIDEERD (echte NAS).** De gebruiker controleert het formulier en de installatie in
STAPPEN_P6.md, onderdeel N-P6-05.

## Vooraf

- Bevestig de TrueNAS- en ix-app-versie (D01). Het hieronder beschreven formulier hoort
  bij ix-app 1.4.8, waarvan de [app-metadata een minimum van TrueNAS SCALE 24.10.2.2 opgeeft](https://github.com/truenas/apps/blob/f142a16a1e15da5f8517658f5a9ad34400972fc4/ix-dev/stable/ix-app/app.yaml).
- Bevestig de drie mounts en de datasets die er werkelijk onder liggen (D02), plus
  de vrije hostpoorten (D03). Gebruik voor nieuwe
  configuratie- en werkdatasets **Datasets → Add Dataset** met de preset **Apps**.
  Geef gebruiker **apps (568)** via **Edit Permissions** de ACL-rechten **Modify**.
  Pas rechten alleen recursief toe op nieuwe, lege datasets. Behoud op bestaande media
  de bestaande groepstoegang of geef toegang op de benodigde submappen. Koppel de
  bestaande mediadataset aan `/data` en behoud de mappen, namen, shares en mounts van
  de mediaserver. Stel in MediaGuard en de forks de bijbehorende paden onder `/data`
  in. Controleer vóór en na de koppeling dat Emby een bestaande titel via hetzelfde
  pad afspeelt. Hardlinks vereisen media en downloads op hetzelfde bestandssysteem.
  Laat de bestaande indeling intact als dat anders is en controleer de padkoppelingen
  voordat je overgaat. Houd cache en staging op dezelfde werkdataset.
- Voor een draaiende installatie volg je eerst [Een bestaande MediaGuard bijwerken](BIJWERKEN.md).
  Dat draaiboek bevat de inventaris, nulmeting, beslissing per installatiewijze en terugweg.
- Gebruik de gepubliceerde AIO van MediaGuard 1.3.0. Vergelijk de digest uit het formulier
  hieronder met `digests.json` en lees `upgradebewijs.md` voor de overgang van 1.2.2 naar
  1.3.0. Laat RPO en RTO (D16) open tot de beheerder de hersteldoelen heeft vastgesteld.
  [Release v1.3.0](https://github.com/lvos04/media-guard-ultimate/releases/tag/v1.3.0) · [Installatiemanifest](../manifest/mediaguard.manifest.yaml)

## Formulier

Open **Apps → Discover Apps → Custom App**. Bij een upgrade van een bestaande standaard
Custom App open je die met **Edit** en behoud je de naam, mounts en eerder bevestigde
instellingen. Als die app **Host Network** gebruikt, behoud je die keuze en laat je de
poortkoppelingen leeg. Een netwerkwijziging vraagt een aparte controle van alle koppelingen.

Draait MediaGuard nog als losse containers, oefen de overstap en terugval eerst op
[afgeschermde datasetkopieën](#afgeschermde-proefkopie). Gebruik voor de proef eigen klonen. Volg bij de echte overstap het
[pad voor een bestaande installatie](BIJWERKEN.md#3-kies-het-pad-voor-de-gevonden-installatie).
De oude app blijft als terugval gestopt zodra de vervangende app de datasets gebruikt.

| Sectie | Veld | Waarde |
|---|---|---|
| Application Name | | `mediaguard` bij een eerste installatie. |
| General | Notes | Optioneel. Leg hier geen aanmeldgegevens vast. |
| Image Configuration | Repository | `ghcr.io/lvos04/mediaguard-core` |
| | Tag | `1.3.0-aio@sha256:47e6f0373b6466563b5d2397ffa5a4d5c706446ad3249cb24fcfb96924a25f16`. Weigert het formulier het digestdeel, vul dan `1.3.0-aio` in. Controleer in beide gevallen de opgehaalde digest bij **Na het opslaan**. |
| | Pull Policy | *Pull the image if it is not already present on host*. |
| Container Configuration | Hostname / Entrypoint / Command | Leeg bij een eerste installatie. |
| | Timezone | De bevestigde tijdzone. |
| | Environment Variables | Leeg bij een eerste installatie, tenzij je bij automatische Seerr-inrichting over bridge `MG_HOST` nodig hebt. Vul dan het bevestigde adres in waarop Seerr de fork-API's bereikt. Voer koppelingen en inloggegevens later in MediaGuard zelf in. |
| | Restart Policy | *Unless Stopped*. |
| | Disable Builtin Healthcheck / TTY / Stdin | Uit. |
| | Devices | Leeg. Gebruik de GPU-keuze onder **Resources Configuration**. |
| Security Context Configuration | Privileged | Uit. |
| | Capabilities | Voeg niets toe. |
| | Custom User | Uit. De appdiensten gebruiken 568:568. In 1.3.0 geeft de bewaker na de opslagvoorbereiding ook zijn rootrechten op en draait web met een aparte UID met beperkte rechten. |
| Network Configuration | Host Network | Uit bij een eerste installatie. |
| | Ports → Add | TCP, containerpoort → hostpoort: `30080 → 30080` (web en API), `38989 → 38989` (Sonarr Series), `38990 → 38990` (Sonarr Anime), `37878 → 37878` (Radarr Films), `37879 → 37879` (Radarr Animefilms). Bevestig dat de poorten vrij zijn vóór het opslaan. |
| Portal Configuration | Add | Naam **MediaGuard**, HTTP, hostpoort van web en pad `/`. Gebruik de host van de app. |
| Storage Configuration | Add → Type | Maak drie **Host Path**-koppelingen. Laat **Read Only** uit. Regel de dataset-ACL vooraf via **Datasets**. |
| | Mount Path / Host Path | `/config` → kies de appdata-dataset, bijvoorbeeld `/mnt/<POOL>/apps/mediaguard`. |
| | Mount Path / Host Path | `/work` → kies de werkdataset, bijvoorbeeld `/mnt/<POOL>/mediaguard-work`. |
| | Mount Path / Host Path | `/data` → kies de bestaande dataset met media en downloads, bijvoorbeeld `/mnt/<POOL>/data`. Behoud alle bestaande mediapaden aan de hostkant. |
| Resources Configuration | Enable Resource Limits | Uit bij een eerste installatie. Behoud eerder vastgelegde limieten bij een upgrade. |
| | GPU Configuration | Standaard uit. Gebruik Intel-render uitsluitend na bevestiging van D08. Met **Passthrough available (non-NVIDIA) GPUs** geeft het beschreven formulier alle beschikbare niet-NVIDIA-GPU's door. MediaGuard neemt de rendergroepen over bij de standaardstart. Controleer de toegang via *Ruimte → Hardware*. |

Gebruik een vertrouwd lokaal netwerk. Is aanmelding bij het register vereist, stel die
uitsluitend in bij **Apps → Configuration → Manage Container Image Registries** met
alleen leesrechten voor pakketten. Registry-login en GPU-doorgifte zijn **NIET GEVALIDEERD (echte NAS)**.

### Bezette forkpoort

Volg bij een bestaande installatie eerst de back-upstappen hieronder.
Kies een vrije, unieke poort en stel die voor dezelfde fork in als luisterpoort,
containerpoort én hostpoort. Voor Sonarr Series doe je dat via `MG_PORT_SONARR_SERIES`
in **Environment Variables** en het bijbehorende poortpaar onder **Network
Configuration**. Voor de overige forks gebruiken dezelfde stappen
`MG_PORT_SONARR_ANIME`, `MG_PORT_RADARR_FILMS` of `MG_PORT_RADARR_ANIMEFILMS`.
Bij **Host Network** stel je alleen de luisterpoort in en laat je de poortkoppelingen
leeg. `MG_HOST` bevat de hostnaam waarop Seerr de fork bereikt.
Sla het formulier op en start de app als die gestopt is.

Bij een bestaande installatie pas je onder **Instellingen → Instanties → Beheren**
de **Interne URL** van die fork aan naar dezelfde poort op de interne host en de
**URL voor Seerr** naar dezelfde poort op de voor Seerr bereikbare host. Controleer
de verbinding met de fork. Als Seerr die instantie al gebruikt, wijzig je onder
**Instellingen → Services** in Seerr de bestaande serververmelding. Behoud de
standaardrol, profielen en rootmap. Test de nieuwe verbinding daar vóór het
opslaan. Controleer daarna in MediaGuard **Seerr-routetest → Test alle routes**.

Bij alleen een bezette webpoort volstaat in bridge-modus een vrije hostpoort met
dezelfde containerpoort voor web en de aangepaste portal.

## Na het opslaan

1. Controleer de opgehaalde registerdigest tegen het releasebewijs. Lees voor de gekozen
   repository en tag `repo_digests` uit de alleen-lezen methode `app.image.query`
   met de [TrueNAS WebSocket Client](https://github.com/truenas/api_client), passend
   bij de TrueNAS-versie. Stop bij een afwijking of ontbrekende teruglezing.
2. Controleer de ingebouwde healthcheck en het opstartlog. In 1.3.0
   verlaagt de healthcheck eerst haar rechten naar 568:568, zet zij
   `no_new_privs` en controleert zij het verse bewakerstatusbestand én de interne
   readiness van de kern. Er is bij een rootstart een kort venster vóór de rechtenwissel.
   De externe monitor gebruikt op de webpoort
   `GET /readyz`, verwacht HTTP 200 en hanteert 10 seconden time-out en 30 seconden
   interval. Controleer na terugval naar `1.2.2-aio` de appstatus, de portal en
   **Instellingen › Systeem & back-ups → Componenten**.
3. Maak bij een eerste installatie direct in MediaGuard de beheerder aan. Controleer
   dat het installatiescherm sluit en dat opnieuw inloggen werkt. Configureer de
   koppelingen daarna in MediaGuard en begin met controleren en vergelijken.

## Afgeschermde proefkopie

1. Bereid een aparte TrueNAS-proef-VM voor met de benodigde formuliercatalogus en
   beide gecontroleerde images in de lokale image-opslag. Plaats de VM **vóór het
   importeren van de datasetkopieën en vóór de eerste start** op een intern virtueel
   netwerk zonder uplink, router, productienetwerk, productie-DNS of toegang tot het
   internet. Gebruik geen bridged of NAT-adapter naar buiten en koppel uitsluitend
   proefdatasets. Beheer de VM vanaf de console of een proefbrowser op dat interne
   netwerk. Controleer de isolatie in de hypervisor en toets vanaf het proefnetwerk
   met een lokale testdienst aan de afgesloten kant dat die onbereikbaar is. Bij
   een bereikbare dienst of twijfel blijft de proefapp gestopt.
2. Kies voor de proefapp bij **Pull Policy** de optie **Never pull an image** en
   start de proefkopie binnen die afscherming. De gekopieerde appdata kan nog
   bestemmingen en sleutels bevatten van MediaGuard, de forks en de encoder.
   Vervang via de betreffende instellingen de koppelingen, inclusief webhooks,
   indexers, downloadclients en mediaservers, door lokale testdiensten met
   testgegevens of schakel de koppeling uit. Controleer de ingestelde adressen
   en bereikbaarheid vanuit de proefomgeving. Houd het netwerk gedurende de hele
   proef afgeschermd, ook bij het terugzetten van een snapshot met oude koppelingen.
3. Vergelijk op de kopie de MediaGuard-voortgang en het hersteljournaal met het
   herstelpunt. Controleer per herstelbaar origineel dat het geregistreerde
   quarantainebestand op het verwachte pad staat en overeenkomt met de vastgelegde
   grootte en, indien vastgelegd, SHA-256. Leg bij originelen zonder geregistreerde
   SHA-256 vóór migratie de checksum vast en vergelijk die met de snapshotkopie.
   Oefen gericht herstel op de kopie en controleer ook de bijpassende
   vorige image, inloggen en de koppelingen naar de testdiensten.

## Back-up en upgrade

Volg voor een bestaande installatie de [nulmeting en herstelset](BIJWERKEN.md#2-leg-de-nulmeting-en-de-herstelset-vast) en kies daarna het updatepad voor het bestaande app-type. De vorige repository, tag en opslagindeling bepalen welke wijziging veilig is.

## Terugzetten na een mislukte upgrade

Bewaar het nieuwere werk en selecteer de vorige repository én tag. Gebruik na een migratie de bijpassende snapshotklonen voor de oude app en houd Emby op de actuele bibliotheek. [Terugweg met controle van voortgang en originelen](BIJWERKEN.md#5-terug-via-de-vorige-image-en-snapshotklonen)
