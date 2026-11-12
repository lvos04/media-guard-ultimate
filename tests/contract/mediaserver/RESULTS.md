# Contracttest mediaservers: uitkomst

Δ-MG-AUTH en Δ-MG-DUAL (ADR-0022; contract 1.3 §2.3 regel 2, C1.3-MGU-04). Gedraaid op 2026-11-11 op de
ontwikkelmachine (Linux, Docker 29.1.3, Compose 2.40.3). Alle onderdelen zijn wegwerpcontainers op deze machine;
`run.sh` ruimt na afloop containers, volumes, netwerk en de werkmap `.run` op.

Opdracht: `flock <pad>/stack.lock tests/contract/mediaserver/run.sh`
(hier met `MGMS_KANDIDAAT=mediaguard/upgrade-kandidaat:mgdual`, vooraf gebouwd met
`deploy/upgradebewijs/Dockerfile.kandidaat`; zonder die variabele bouwt `run.sh` de kandidaat zelf).

**Status: Sim-getest tegen echte servercontainers. NIET GEVALIDEERD op de echte NAS** (zie "Wat hier niet bewezen
is").

## Wat er getest is

| onderdeel | image | versie |
|---|---|---|
| Emby | `emby/embyserver:4.10.2.0@sha256:7082c63b10a2fcb3805a7eaebba407d5723bc57c085c9f0b29b7d0a702765cc1` | 4.10.2.0 |
| Jellyfin (basis van Jelly Leon) | `jellyfin/jellyfin:12.4@sha256:fea863d0cd9ec445985d9c75fc6ed64e38a1d2766cf0c250a33d553f4fc1b771` | 12.4.0 |
| MediaGuard, vorige release | `ghcr.io/lvos04/mediaguard-core:1.2.2-aio@sha256:14e540ba9cad0bb7fbda8295bca89496f58462a201361544916f3b16b3a52dc6` | 1.2.2 |
| MediaGuard, kandidaat | runtime van 1.2.2 + `apps/core` van deze werkmap (`deploy/upgradebewijs/Dockerfile.kandidaat`) | core van deze commit |
| clients in deel A | `apps/core` en `services/encoder` van deze werkmap, in de venv van de repo (Python 3.12) | — |

De serverimages zijn dezelfde digests als in het B03-plan. De kandidaat is **niet** het alles-in-één-image van een
release: het is de runtime van de vorige release met de nieuwe core erin, net als in de CI-job `upgradebewijs`.

## Opstelling (`docker-compose.yml`, project `mgms`)

- `srv-emby` en `srv-jellyfin`: de echte servers. De test richt ze in via hun eigen API (startwizard, beheerder,
  API-sleutel, bibliotheek `Films`) en wacht tot beide de twee testfilms kennen.
- `emby` en `jellyfin`: doorgeefluiken (`logproxy.py`) vóór die servers. MediaGuard kent alleen deze namen, dus elk
  verzoek in hun log komt van MediaGuard. Het log bevat de **vorm** van de aanmelding en een vingerafdruk van de
  sleutel, nooit de sleutel zelf.
- `mgms-mediaguard`: eerst de vorige release op de gevulde fixture `core-1.2.2`, daarna de kandidaat op dezelfde
  datamap.
- Media: twee echte films (H.264 + AAC, 20 s, gemaakt met ffmpeg); één met een Nederlandse ondertitel ernaast
  (`.nl.srt`), één zonder.
- "Iemand kijkt" is nagebootst met de sessie-API van de servers (`POST /Sessions/Playing`, `…/Progress`,
  `…/Stopped`), niet met een echte speler.

## Uitkomst

**24 tests geslaagd, 0 mislukt; 113 van 113 controles goed. Duur 722,5 s (12 min 2 s); pytest-exitcode 0.**
Geen handmatige stap tijdens de run.

Dezelfde test draaide op 2026-11-11 ook op een GitHub-runner (CI-job `mediaservers`, zware run 42979806055 op commit
`c5e3016`, gestart met `workflow_dispatch heavy=true`): 24 passed in 725,4 s. De tabellen hieronder zijn die van de
lokale run.

### Deel A: de clients van core en de encoder, rechtstreeks tegen beide servers

| test | bewijst | Emby | Jellyfin |
|---|---|---|---|
| `a1_openbare_info_en_soort` | soort herkend aan de openbare info, zonder aanmeldkop; Emby geeft geen `ProductName`, Jellyfin `Jellyfin Server` | goed | goed |
| `a2_sleutel_alleen_in_de_kop_van_die_server` | bibliotheekmappen gelezen; alleen de kop van die server, sleutel nooit in het adres; alle antwoorden 2xx | goed (`X-Emby-Token`) | goed (`Authorization: MediaBrowser`) |
| `a3_verkeerde_sleutel_geeft_een_duidelijke_fout` | verkeerde sleutel → 401 "sleutel geweigerd (controleer de API-sleutel)" | goed | goed |
| `a4_jellyfin_weigert_de_oude_vormen_en_emby_houdt_de_zijne` | Jellyfin 12.x weigert `X-Emby-Token`, `api_key=` en `X-Emby-Authorization` (401) en kent `/emby` niet (404); Emby neemt `X-Emby-Token` aan en weigert zonder sleutel; soort Emby op een Jellyfin-adres wordt herkend | goed | goed |
| `a5_sessies_lezen` | niemand kijkt → leeg; één kijker met titel en pad; pauze telt niet als kijken; na stoppen leeg | goed | goed |
| `a6_bibliotheek_verversen` | gericht verversen (`/Library/Media/Updated`) en volledige scan (`/Library/Refresh`) aangenomen (204) | goed | goed |
| `a7_afspeeltest_en_nederlandse_ondertitels` | afspeeltest "ok" (speelt direct af); `.nl.srt` zichtbaar; film zonder → niet zichtbaar; onbekend bestand → `not_found`, geen verzonnen uitkomst | goed | goed |
| `a8_encoder_kiest_dezelfde_kop` | de losse encoder-instelling "pauzeer bij kijken" kiest dezelfde kop en leest kijken/pauze goed | goed | goed |

### Deel B: de echte images, vorige release → kandidaat op dezelfde datamap

| test | bewijst | uitkomst |
|---|---|---|
| `b1_vorige_release_volgt_emby` | 1.2.2 draait op de gevulde fixture, krijgt via zijn eigen API de Emby-koppeling, zet de kijkpauze zodra iemand op Emby kijkt en laat de banen van de encoder wachten | goed |
| `b2_upgrade_emby_blijft_gevolgd_zonder_handeling` | na de upgrade is de bestaande koppeling server 1 zonder `POST /config` of `/test`; geheim en instellingen byte-gelijk; migratie 150 toegepast (35 → 36), geen extra server; de kijkpauze van vóór de upgrade staat er nog; de kandidaat (core) vraagt Emby niets wat 1.2.2 niet vroeg (de encoder-eigen instelling "pauzeer bij kijken" staat in deel B uit: zie "Wat hier niet bewezen is"); Jellyfin kreeg nog geen enkel verzoek met een sleutel | goed |
| `b3_zoeken_vindt_beide_en_jellyfin_komt_erbij` | "Zoek automatisch" vindt beide en noemt de juiste soort; Jellyfin wordt server 2 met een eigen geheim; de Emby-koppeling blijft onaangeraakt; soort Emby op het Jellyfin-adres geeft een duidelijke melding; server 1 verwijderen bestaat niet (422) | goed |
| `b4_kijken_op_emby_pauzeert` | kijken op Emby → kijkpauze met de naam van de server, per server zichtbaar, banen wachten; stoppen → weer vrij | goed |
| `b5_kijken_op_jellyfin_pauzeert` | hetzelfde voor Jelly Leon | goed |
| `b6_jellyfin_valt_weg_emby_blijft_gevolgd` | Jellyfin gestopt → server 2 "niet bereikbaar", Emby blijft verbonden en gevolgd, de kijkpauze door Emby blijft staan en gaat weg als de kijker stopt; Jellyfin terug → vanzelf weer verbonden | goed |
| `b7_emby_losmaken_volgt_alleen_jellyfin` | losgemaakte server 1 blijft in de lijst met adres en sleutel bewaard; geen verzoek meer naar Emby; geen melding, incident of mislukte actie; kijken op Jelly Leon pauzeert nog steeds | goed |
| `b8_emby_weer_koppelen_zonder_sleutel_opnieuw` | Emby in één handeling weer gekoppeld met de bewaarde sleutel; beide weer gevolgd en verbonden | goed |
| `b9_niets_gelekt_en_nette_stop` | geen API-sleutel in de logs van MediaGuard of in het doorgeefluik; over de hele run alleen de juiste kop per server; geen 401/403; Emby zonder onderbreking gevolgd; nette stop (exitcode 0) | goed |

## Gemeten over de lijn (doorgeefluik, alleen verzoeken van MediaGuard)

| server | verzoeken | paden | aanmelding | status |
|---|---|---|---|---|
| Emby | 31 | `/emby/Sessions` (18), `/emby/Library/VirtualFolders` (8), `/emby/System/Info` (2), `/emby/System/Info/Public` (3, zonder sleutel) | alleen `X-Emby-Token` | alles 200 |
| Jellyfin | 22 | `/Sessions` (13), `/Library/VirtualFolders` (3), `/System/Info` (1), `/System/Info/Public` (3, zonder sleutel), `/emby/System/Info/Public` (2, zonder sleutel) | alleen `Authorization: MediaBrowser` | 200; de twee verzoeken onder `/emby` 404 |

- De enige parameter in een adres is `ActiveWithinSeconds`; een sleutel stond nooit in een adres.
- De twee 404's zijn de herkenning: de info-route wordt eerst onder `/emby` gevraagd en bij 404 zonder (ADR-0022
  besluit 3).
- Emby in de kandidaat: 16 geslaagde sessiepolls tot het losmaken, grootste gat 30,1 s (de lus loopt elke 30 s),
  ook toen Jellyfin erbij kwam en wegviel. Tussen het laatste verzoek van 1.2.2 en de eerste poll van de kandidaat
  zat 69 s (stoppen, image wisselen, starten, eerste ronde na 60 s).
- Teksten van de kijkpauze: met één server "iemand kijkt (Testfilm)" (gelijk aan 1.2.2); met twee servers
  "iemand kijkt op Emby (Testfilm)" en "iemand kijkt op Jelly Leon (Testfilm (2024))".
- Verkeerde soort: "Op dit adres draait Jellyfin, niet Emby: kies de juiste soort."
- Jellyfin weg: "niet bereikbaar (ConnectError)".

## Waarnemingen aan de servers

- Emby 4.10.2.0 geeft op `/System/Info/Public` geen `ProductName`. Daarom is de regel: alleen een `ProductName` met
  "jellyfin" erin is Jellyfin, al het andere blijft Emby.
- Jellyfin 12.4 kent het voorvoegsel `/emby` niet meer (404) en weigert `X-Emby-Token`, `api_key=` in het adres en
  `X-Emby-Authorization` (401) zolang `EnableLegacyAuthorization` uit staat.
- Jellyfin 12.4 slaat de scan over van een bibliotheek die tijdens de scan van de startwizard is toegevoegd
  (`refreshLibrary=true` heeft dan geen effect). De test vraagt de scan daarom opnieuw aan (`POST /Library/Refresh`)
  tot de films er staan. Dit raakt alleen de opstelling van de test, niet MediaGuard.

## Wat hier niet bewezen is

- **Echte NAS: NIET GEVALIDEERD.** De echte Emby van de gebruiker met zijn bibliotheek en kijkgeschiedenis, een echt
  Jelly Leon-image en echte spelers zijn niet getest. Jelly Leon is hier de ongewijzigde Jellyfin 12.4 waarop het is
  gebaseerd.
- **Sessies zijn nagebootst** met de sessie-API van de servers; er speelde geen echte client een film af.
- **Afspeeltest en Nederlandse ondertitels** zijn in deel A bewezen op clientniveau (de client vraagt de echte server
  om de afspeelinformatie). De hele keten in de achtergrondlus — een overdracht van een echte import, per server een
  uitkomst — is alleen Sim-getest (`apps/core/tests/test_mediaserver_dual.py`).
- **De regel "een kijkpauze van een server zonder contact vervalt na 10 minuten"** is alleen Sim-getest; in b6
  stopte de kijker eerder.
- **Plex** is niet gestart; die code is ongewijzigd.
- **De encoder-eigen instelling "pauzeer bij kijken"** (één adres en één sleutel in de encoder, ADR-0022
  besluit 6) staat in deel B uit: het doorgeefluik ziet dat verkeer niet, en de controle "de kandidaat vraagt Emby
  niets wat 1.2.2 niet vroeg" gaat alleen over core. Sinds `f21f296` vraagt die instelling Emby ook
  `GET /System/Info/Public` (zonder sleutel) om de soort te kennen; 1.2.2 vroeg in die lus alleen `/Sessions`.
  Dat leesverzoek gaat één keer per adres (opnieuw na een adreswijziging of een fout), daarna per poll alleen
  `/Sessions` zoals in 1.2.2. Gemeten: tegen de echte servers alleen de keuze van de kop (deel A, a8); het aantal
  verzoeken per poll alleen Sim-getest
  (`services/encoder/tests/test_integraties.py::test_monitor_vraagt_de_productnaam_eenmaal_per_adres`).
- **Omleidingen en foutpagina's**: de echte servers gaven in deze run geen enkele 3xx en kaatsten geen sleutel
  terug. Dat core een omleiding alleen op dezelfde server volgt (zelfde host en poort, of http naar https op de
  standaardpoorten; besluit van de gebruiker 2026-11-12) en geen sleutel uit een foutpagina in een melding zet, is
  alleen Sim-getest (`apps/core/tests/test_mediaserver_dual.py`).
- De kandidaat is de nieuwe core op de runtime van 1.2.2, niet het release-image.

## Opnieuw draaien

`run.sh` bewaart het bewijs buiten `.run` als `MGMS_BEWIJS=<map>` gezet is: `pytest.log`, `junit.xml`,
`metingen.json` (alle controles en gemeten feiten), de logs van beide doorgeefluiken (`emby.jsonl`,
`jellyfin.jsonl`) en de containerlogs. De zware CI-run (`ci`, job `mediaservers`) bewaart die map als artefact.
