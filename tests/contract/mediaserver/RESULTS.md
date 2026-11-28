# Contracttest mediaservers: uitkomst

## S-P6-33 op 28 november 2026

### Upgrade en terugzetten

De registrycontrole bevestigde de gepinde vorige MediaGuard-release `1.3.0-aio` uit `deploy/upgradebewijs/vorige-release.json`. De kandidaat bevat de kern en de encoder van revisie `602c48b486c1` op de runtime van die release. Het manifest van de kandidaat vermeldt eveneens 1.3.0. De draaiende images zijn te onderscheiden aan hun image-ID.

| Meting in de upgradeproef | Vóór | Na upgrade | Na terugzetten |
|---|---:|---:|---:|
| Jobs in de kern (`jobs`) | 6 | 6 | 6 |
| Regels jobgeschiedenis (`job_logs`) | 2 | 2 | 2 |
| Open overdrachten | 1 | 1 | 1 |

De vergelijking omvatte 177 vooraf gevulde tabellen. In beide richtingen waren er nul verloren rijen en nul gewijzigde beschermde rijen volgens het uitzonderingenbestand in versiebeheer. De twee geregistreerde originelen, het werkbestand en de mediatitel behielden alle vier hun SHA-256 na de upgrade en op elk van de drie terugvalkopieën.

Op revisie `e92ebfc` meldde de upgradejob opnieuw ‘Upgrade: niets verloren’ en ‘terugzetten: niets verloren’. In de hervatproef voor encoder 1.2.2 bleven na een geplande en een onverwachte stop 60 van 144 beelden behouden. Beide proeven hervatten op 42 procent, codeerden de resterende 84 beelden en controleerden alle 144 beeldtijden. [Forgejo-run 127](https://git.medialeon.nl/leon/media-guard-ultimate/actions/runs/127)

### Eerste meting met stock Jellyfin en lokale Emby

De eerste referentierun stopte bij B7. Tijdens het losmaken van Emby ging het aantal jobs van **71 naar 82** en het aantal regels jobgeschiedenis van **69 naar 78**. Elk vooraf gemeten jobkenmerk en elke volledige eerdere historieregel was terug te vinden.

Het kandidaatlog verklaart de elf nieuwe jobs na de beheerhandeling. De importplanner gaf acht jobs van het type `imports.stuck_check` uit (72–75 en 79–82). De bibliotheekplanner gaf drie jobs van het type `library.sync` uit (76–78). De vier Arr-testinstanties waren onbereikbaar. De acht importcontroles hadden één poging. De al bestaande synchronisatiejob 71 bereikte zijn tweede, laatste poging. Bij elk van die negen eindfouten schrijft de werker één historieregel. Deze toerekening volgt uit de pogingen in het log en de foutafhandeling in `mediaguard.jobs`. De telling in de database bevestigt de toename met negen.

Voor de herhaling archiveert het harnas de vier Arr-testinstanties via de beheer-API na de eerste gezondheidscontroles. Het wacht tot er nul plandoelen en nul open jobs zijn. De echte mediaserverbewaking loopt door. B7 en B8 vergelijken vervolgens elke kolom van elke job, elke historieregel en beide totaaltellingen. In een afzonderlijke API-proef blijven de Arr-instanties actief en staan de achtergrondwerkers stil. Zowel na losmaken als na herkoppelen bleef een lopende synchronisatiejob op 42 procent met dezelfde historie. De tegenproef keurde een afzonderlijke wijziging van het percentage en van de status af bij gelijke tellingen.

| Controle met de stockserver | Vóór | Na |
|---|---:|---:|
| B2 · jobs / regels jobgeschiedenis | 16 / 10 | 16 / 10 |
| B7 · jobs / regels jobgeschiedenis | 71 / 69 | 82 / 78 |
| B7 · regels in het Emby-proxylog | 29 | 29 |

De 29 verzoeken in het Emby-proxylog waren allemaal GET met HTTP 200. In B7 behield de ontkoppelde server zijn adres en opgeslagen sleutel. De kijkpauze op de overblijvende server werkte. Gedurende het meetvenster van minstens 70 seconden kwam er geen Emby-verzoek bij. B6 had aangetoond dat Emby gevolgd bleef toen stock Jellyfin uitviel. Bij de upgrade in B2 bleef ook de gevulde `UserData` van de tweede testfilm gelijk.

Deze run gebruikte de kandidaat `sha256:2f8501498cf4062cb6a2586470a468b4b661c4f7aa6405ab89e20b7b8bb1ac30`, de gepinde Emby 4.10.2.0 en stock Jellyfin 12.4 uit het proefbestand. De fixture uit 1.2.2 is door de gepubliceerde 1.3.0-aio geopend met één Emby-koppeling. Het aantal kernmigraties ging van 49 naar 52. De test stopte met exitcode 1 na 21 groene testgevallen en één afkeuring. Van de 103 uitgevoerde controles voldeden er 102.

| Testgeval | Stock, voldane/uitgevoerde controles |
|---|---|
| `a1_openbare_info_en_soort[emby]` | goed (4/4) |
| `a1_openbare_info_en_soort[jellyfin]` | goed (4/4) |
| `a2_sleutel_alleen_in_de_kop_van_die_server[emby]` | goed (3/3) |
| `a2_sleutel_alleen_in_de_kop_van_die_server[jellyfin]` | goed (3/3) |
| `a3_verkeerde_sleutel_geeft_een_duidelijke_fout[emby]` | goed (1/1) |
| `a3_verkeerde_sleutel_geeft_een_duidelijke_fout[jellyfin]` | goed (1/1) |
| `a4_jellyfin_weigert_de_oude_vormen_en_emby_houdt_de_zijne` | goed (6/6) |
| `a5_sessies_lezen[emby]` | goed (4/4) |
| `a5_sessies_lezen[jellyfin]` | goed (4/4) |
| `a6_bibliotheek_verversen[emby]` | goed (1/1) |
| `a6_bibliotheek_verversen[jellyfin]` | goed (1/1) |
| `a7_afspeeltest_en_nederlandse_ondertitels[emby]` | goed (4/4) |
| `a7_afspeeltest_en_nederlandse_ondertitels[jellyfin]` | goed (4/4) |
| `a8_encoder_kiest_dezelfde_kop[emby]` | goed (4/4) |
| `a8_encoder_kiest_dezelfde_kop[jellyfin]` | goed (4/4) |
| `b1_vorige_release_volgt_emby` | goed (8/8) |
| `b2_upgrade_emby_blijft_gevolgd_zonder_handeling` | goed (18/18) |
| `b3_zoeken_vindt_beide_en_jellyfin_komt_erbij` | goed (10/10) |
| `b4_kijken_op_emby_pauzeert` | goed (4/4) |
| `b5_kijken_op_jellyfin_pauzeert` | goed (4/4) |
| `b6_jellyfin_valt_weg_emby_blijft_gevolgd` | goed (6/6) |
| `b7_emby_losmaken_volgt_alleen_jellyfin` | afgekeurd (4/5) |
| `b8_emby_weer_koppelen_zonder_sleutel_opnieuw` | niet uitgevoerd |
| `b8b_jellyfin_weigert_sleutel_emby_blijft_gevolgd` | niet uitgevoerd |
| `b9_niets_gelekt_en_nette_stop` | niet uitgevoerd |

B8 en B9 kwamen door de stopregel niet aan bod. De afzonderlijke unittest `test_b9_keurt_log_met_proefsleutel_af` bevestigde dat een synthetische sleutel in een log wordt gevonden. Voor S-P6-31 (JLT-63) is A7 met het tv-profiel op stock gemeten. Voor alle testgevallen met de P6-image ontbreekt een meting, dus er is geen vergelijking tussen stock en P6.

Een eerdere stockpoging stopte bij een te strikte vergelijking in B2 (16 → 16 jobs, 10 → 11 historieregels). De start van een werker mag een regel toevoegen. In de herhaling controleerde B2 voor elke bestaande job de vaste kenmerken en voor elke eerdere historieregel alle velden. B7 en B8 eisen daarnaast gelijke totaaltellingen. De tegenproef wijst zowel vervanging van een eerdere regel tijdens de upgrade als een extra regel tijdens het losmaken af.

### Herhaling in ronde 2

De eerste stockpoging in ronde 2 bereikte B2. De vorige release stopte normaal. De hulpcontainer voor de nulmeting kon onder de testgebruiker de bovenliggende privémap niet doorlopen. De kandidaat was nog niet gestart. Na 16 groene tests stopte pytest met exitcode 1. De hulpcontainer koppelt nu dezelfde `config`-map op hetzelfde pad als de draaiende applicatie. De database wordt in beide gevallen alleen-lezen geopend. Bij de volgende poging kon de hulpcontainer de nulmeting lezen. De kandidaat op `a35172e` kreeg bij het importeren van `mediaguard.settings_registry` een `PermissionError` onder de applicatiegebruiker. De kandidaatbouw had de bestandsrechten van de privécheckout overgenomen. De bouwstap maakt nu de meegeleverde kern, encoder en het manifest leesbaar en controleert de import onder die gebruiker.

### Opnieuw meten

Gebruik een eigen compose-project met `MGMS_BASIS=laatste`, `MGMS_INTERN=true`, `MGMS_KANDIDAAT=<image-ID>` en `MGMS_BEWIJS=<eigen bewijsmap>`. Voer `tests/contract/mediaserver/run.sh` via het gedeelde testslot uit. De stockrun gebruikt de standaardimage. Na een groene referentierun krijgt de run met `JELLYFIN_IMAGE=jelly-leon:p6` een eigen bewijsmap. Voor dit P6-artefact is image-ID `sha256:87a5e00d59597f60ab37a1822bf9f84d8394048845e85a7152d0c8a7f5594417` vastgesteld.

### Webinterface voor S-P6-50 en S-P6-51

Open als beheerder **Inrichten → Mediaservers & ondertitels** (`/inrichten?tab=mediaserver`). Kies **Mediaserver toevoegen** en **Toevoegen en testen** om Jelly Leon naast de bestaande Emby te koppelen. Voor de eerste server heet de bevestiging **Opslaan en testen**. Kies op de kaart van Emby **Ontkoppelen…** en bevestig met **Server ontkoppelen**. **Weer koppelen** gebruikt het bewaarde adres en de bewaarde sleutel. Deze teksten komen uit `apps/web/components/mediaserver/mediaserver-tab.tsx` en de Nederlandse vertaling in `apps/web/lib/i18n/messages/mediaserver.ts`.

## S-P40-20 op 27 november 2026

De contractproef draaide eerst met stock Jellyfin 12.4 en daarna met de lokale Jelly Leon-image uit S-G1-16. Beide keren doorliep dezelfde MediaGuard-kandidaat alle 25 tests. De stockrun had 125 afzonderlijke controles in 861,053 s en de Jelly Leon-run 125 in 787,577 s. Beide runs eindigden met pytest-exitcode 0.

| Rol | Werkelijk gebruikte image |
|---|---|
| Vorige MediaGuard-release | `ghcr.io/lvos04/mediaguard-core:1.3.0-aio@sha256:47e6f0373b6466563b5d2397ffa5a4d5c706446ad3249cb24fcfb96924a25f16` |
| MediaGuard-kandidaat | `sha256:d2f6eb2db4e52c4349ddee10231eb2105211651f06174365b00f05650d2082d1`, gebouwd uit kern, encoder en manifest op `88f734f8fb90204f95321386bb0667255317a2c4` met `deploy/upgradebewijs/Dockerfile.kandidaat` |
| Emby in beide proeven | `emby/embyserver:4.10.2.0@sha256:7082c63b10a2fcb3805a7eaebba407d5723bc57c085c9f0b29b7d0a702765cc1` |
| Stock Jellyfin | `jellyfin/jellyfin:12.4@sha256:fea863d0cd9ec445985d9c75fc6ed64e38a1d2766cf0c250a33d553f4fc1b771` |
| Lokale Jelly Leon | `jelly-leon:lokaal-48293b48a`, image-ID `sha256:09c2bb379cf09e0895a794dcf4979b3171d015d942dda67a67ad42231f43741b`, bronrevisie `48293b48ac48644e0d4433cf3193a96fbf35eac4` |

### Gegevensbehoud

De upgradeproef begon met de gevulde fixture van de gepubliceerde versie 1.3.0-aio. Bij de upgrade en bij het terugzetten van de vorige image met de snapshot zijn alle 177 vooraf gevulde tabellen per rij en per beschermde waarde vergeleken. Er waren in beide richtingen nul verloren en nul gewijzigde beschermde rijen. De open overdracht bleef 1 → 1. De twee geregistreerde originelen, het werkbestand en de mediatitel hadden na de update en op alle drie de terugvalkopieën dezelfde SHA-256. De proef gebruikt de uitzonderingen uit het versiebeheer voor velden die tijdens een start mogen veranderen.

In contracttest b1 opende de gepubliceerde 1.3.0-aio de enkelvoudige fixture uit 1.2.2 en koppelde de lokale Emby. Zo kreeg b2 een uitgangssituatie met precies één gekoppelde mediaserver. B2 controleerde bij de versieovergang alle migratienamen (49 → 50), inclusief migratie 150. De enige nieuwe kernmigratie was `158_hub_poolbron`. Na het terugzetten mat de upgradeproef weer de 49 migraties van 1.3.0. In b2 en b9 was de volledige `UserData` van de tweede testfilm bij Emby gelijk aan de nulmeting. De synthetische gebruiker had twee afspeelbeurten, een laatste kijkdatum, een hervatpunt van 50.000.007 ticks en een favoriet. Na de upgrade bleef Emby zonder beheerhandeling server 1 met dezelfde instellingen en sleutel.

### Vergelijking per testgeval

Het getal tussen haakjes is het aantal uitgevoerde contractcontroles. Alle controle-uitkomsten kwamen per testgeval overeen. De tests met suffix `[jellyfin]` liepen in de tweede kolom met stock en in de derde kolom met Jelly Leon.

| Testgeval | Stock | Jelly Leon |
|---|---:|---:|
| `a1_openbare_info_en_soort[emby]` | goed (4) | goed (4) |
| `a1_openbare_info_en_soort[jellyfin]` | goed (4) | goed (4) |
| `a2_sleutel_alleen_in_de_kop_van_die_server[emby]` | goed (3) | goed (3) |
| `a2_sleutel_alleen_in_de_kop_van_die_server[jellyfin]` | goed (3) | goed (3) |
| `a3_verkeerde_sleutel_geeft_een_duidelijke_fout[emby]` | goed (1) | goed (1) |
| `a3_verkeerde_sleutel_geeft_een_duidelijke_fout[jellyfin]` | goed (1) | goed (1) |
| `a4_jellyfin_weigert_de_oude_vormen_en_emby_houdt_de_zijne` | goed (6) | goed (6) |
| `a5_sessies_lezen[emby]` | goed (4) | goed (4) |
| `a5_sessies_lezen[jellyfin]` | goed (4) | goed (4) |
| `a6_bibliotheek_verversen[emby]` | goed (1) | goed (1) |
| `a6_bibliotheek_verversen[jellyfin]` | goed (1) | goed (1) |
| `a7_afspeeltest_en_nederlandse_ondertitels[emby]` | goed (4) | goed (4) |
| `a7_afspeeltest_en_nederlandse_ondertitels[jellyfin]` | goed (4) | goed (4) |
| `a8_encoder_kiest_dezelfde_kop[emby]` | goed (4) | goed (4) |
| `a8_encoder_kiest_dezelfde_kop[jellyfin]` | goed (4) | goed (4) |
| `b1_vorige_release_volgt_emby` | goed (8) | goed (8) |
| `b2_upgrade_emby_blijft_gevolgd_zonder_handeling` | goed (17) | goed (17) |
| `b3_zoeken_vindt_beide_en_jellyfin_komt_erbij` | goed (10) | goed (10) |
| `b4_kijken_op_emby_pauzeert` | goed (4) | goed (4) |
| `b5_kijken_op_jellyfin_pauzeert` | goed (4) | goed (4) |
| `b6_jellyfin_valt_weg_emby_blijft_gevolgd` | goed (6) | goed (6) |
| `b7_emby_losmaken_volgt_alleen_jellyfin` | goed (7) | goed (7) |
| `b8_emby_weer_koppelen_zonder_sleutel_opnieuw` | goed (5) | goed (5) |
| `b8b_jellyfin_weigert_sleutel_emby_blijft_gevolgd` | goed (5) | goed (5) |
| `b9_niets_gelekt_en_nette_stop` | goed (11) | goed (11) |

### Uitval, geweigerde sleutel en Emby-verkeer

- B6 stopte de tweede server terwijl iemand op Emby keek. De encoder bleef gepauzeerd, Emby bleef verbonden en bleef geldige polls beantwoorden. De tweede server kwam na de herstart terug.
- B8b kreeg met een opzettelijk verkeerde sleutel HTTP 401 op de tweede server. De API gaf `connected=false` en `contact.ok=false`. Emby beantwoordde intussen een sessiepoll met HTTP 200 en behield zijn sleutel. De eigen geldige sleutel herstelde de tweede verbinding. De proxy telde in elke run twee verwachte afwijzingen. De HTTP 404-antwoorden in beide proxylogs kwamen van de openbare detectie met het Emby-voorvoegsel op de Jellyfin-server.
- B1 mat ook de zoekactie die b3 op de kandidaat uitvoert. B2 vergeleek de eerste Emby-polls vóór die zoekactie. B9 vergeleek de volledige kandidaatfase op HTTP-methode, pad, aanmeldkop en namen van queryparameters. De kandidaat gebruikte `GET /emby/Library/VirtualFolders`, `GET /emby/Sessions`, `GET /emby/System/Info`, `GET /emby/System/Info/Public`. Alle vormen kwamen in de vorige release voor. Alle Emby-antwoorden waren HTTP 200. Voor beveiligde verzoeken gebruikte MediaGuard op Emby `X-Emby-Token` en op Jelly Leon `Authorization: MediaBrowser`. A4 bevestigde met HTTP 401 dat de oude aanmeldvormen op Jelly Leon uit stonden.
- De aantallen Emby-sessiepolls tot het geplande losmaken waren 19 / 17 (stock / Jelly Leon). De langste tijd tussen zulke polls was 30,2 / 30,2 s, binnen de grens van 75 s. De tijd tussen het laatste verzoek van de vorige release en de eerste sessiepoll van de kandidaat was 66 / 67 s. De proeven meten verstreken tijd met de werkelijke opstart- en pollcycli.

De stockrun deed vóór het losmaken 31 Emby-verzoeken, de Jelly Leon-run 29. Het verschil van twee komt overeen met de twee extra periodieke sessiepolls. B9 telde in de hele run 36 tegenover 34 Emby-verzoeken en 22 tegenover 20 geldige Jellyfin-verzoeken met sleutel. De aanmeldvormen, paden en uitkomsten waren gelijk. De verse serverinstanties gebruikten hun eigen containernaam als `ServerName`. De hoeveelheid MediaGuard-loguitvoer varieerde tussen de runs (vorige release 13.580 / 13.581, kandidaat 532 / 524). De proef controleerde beide logs op blootstelling van de sleutels.

### Bereik en herhaling

Kijksessies zijn nagebootst via de sessie-API van de servers. De echte NAS, de Emby van de gebruiker, de uitgebrachte Jelly Leon-image en echte spelers vallen onder de P6-proef. De overnametool met zijn eigen voor/na-meting hoort bij Jelly Leon. De vergelijking van de Emby-verzoeken betreft de MediaGuard-kern. De losse encoderclient is afzonderlijk gemeten in a8.

Kies `MGMS_BASIS=laatste`, `MGMS_INTERN=true`, een eigen `COMPOSE_PROJECT_NAME`, `MGMS_KANDIDAAT=<image-ID>` en per run een eigen `MGMS_BEWIJS` buiten de repo. Draai `flock <WERKMAP>/.lokaal/stack.lock nice -n 10 tests/contract/mediaserver/run.sh` eerst met de standaardserver en daarna met `JELLYFIN_IMAGE=jelly-leon:lokaal-48293b48a`. Beide opstellingen gebruiken eigen interne netwerken en volumes. Het cachevolume wordt leeg aangemaakt met `nocopy`, zodat het eigen entrypoint van Jelly Leon de map met de vereiste rechten kan openen. De standaardmodus `enkelvoudig` toetst apart het oorspronkelijke upgradepad vanaf 1.2.2, waarin de kandidaat migratie 150 moet toevoegen.

## Historische stockmeting van 11 november 2026

Δ-MG-AUTH en Δ-MG-DUAL (ADR-0022; contract 1.3 §2.3 regel 2, C1.3-MGU-04). Gedraaid op 2026-11-11 op de
ontwikkelmachine (Linux, Docker 29.1.3, Compose 2.40.3). Alle onderdelen zijn wegwerpcontainers op deze machine;
`run.sh` ruimt na afloop containers, volumes, netwerk en de werkmap `.run` op.

Opdracht: `flock <pad>/stack.lock tests/contract/mediaserver/run.sh`
(hier met `MGMS_KANDIDAAT=mediaguard/upgrade-kandidaat:mgdual`, vooraf gebouwd met
`deploy/upgradebewijs/Dockerfile.kandidaat`; zonder die variabele bouwt `run.sh` de kandidaat zelf).

**Status: Sim-getest tegen echte servercontainers. NIET GEVALIDEERD op de echte NAS** (zie "Wat hier niet bewezen
is").

### Wat er getest is

| onderdeel | image | versie |
|---|---|---|
| Emby | `emby/embyserver:4.10.2.0@sha256:7082c63b10a2fcb3805a7eaebba407d5723bc57c085c9f0b29b7d0a702765cc1` | 4.10.2.0 |
| Jellyfin (basis van Jelly Leon) | `jellyfin/jellyfin:12.4@sha256:fea863d0cd9ec445985d9c75fc6ed64e38a1d2766cf0c250a33d553f4fc1b771` | 12.4.0 |
| MediaGuard, vorige release | `ghcr.io/lvos04/mediaguard-core:1.2.2-aio@sha256:14e540ba9cad0bb7fbda8295bca89496f58462a201361544916f3b16b3a52dc6` | 1.2.2 |
| MediaGuard, kandidaat | runtime van 1.2.2 + `apps/core` van deze werkmap (`deploy/upgradebewijs/Dockerfile.kandidaat`) | core van deze commit |
| clients in deel A | `apps/core` en `services/encoder` van deze werkmap, in de venv van de repo (Python 3.12) | — |

De serverimages zijn dezelfde digests als in het B03-plan. De kandidaat is **niet** het alles-in-één-image van een
release: het is de runtime van de vorige release met de nieuwe core erin, net als in de CI-job `upgradebewijs`.

### Opstelling (`docker-compose.yml`, project `mgms`)

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

### Uitkomst

**24 tests geslaagd, 0 mislukt; 113 van 113 controles goed. Duur 722,5 s (12 min 2 s); pytest-exitcode 0.**
Geen handmatige stap tijdens de run.

Dezelfde test draaide op 2026-11-11 ook op een GitHub-runner (CI-job `mediaservers`, zware run 42979806055 op commit
`c5e3016`, gestart met `workflow_dispatch heavy=true`): 24 passed in 725,4 s. De tabellen hieronder zijn die van de
lokale run.

#### Deel A: de clients van core en de encoder, rechtstreeks tegen beide servers

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

#### Deel B: de echte images, vorige release → kandidaat op dezelfde datamap

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

### Gemeten over de lijn (doorgeefluik, alleen verzoeken van MediaGuard)

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

### Waarnemingen aan de servers

- Emby 4.10.2.0 geeft op `/System/Info/Public` geen `ProductName`. Daarom is de regel: alleen een `ProductName` met
  "jellyfin" erin is Jellyfin, al het andere blijft Emby.
- Jellyfin 12.4 kent het voorvoegsel `/emby` niet meer (404) en weigert `X-Emby-Token`, `api_key=` in het adres en
  `X-Emby-Authorization` (401) zolang `EnableLegacyAuthorization` uit staat.
- Jellyfin 12.4 slaat de scan over van een bibliotheek die tijdens de scan van de startwizard is toegevoegd
  (`refreshLibrary=true` heeft dan geen effect). De test vraagt de scan daarom opnieuw aan (`POST /Library/Refresh`)
  tot de films er staan. Dit raakt alleen de opstelling van de test, niet MediaGuard.

### Wat hier niet bewezen is

- **NIET GEVALIDEERD (echte NAS).** De bestaande installatie, de bibliotheek en echte spelers vragen de NAS-proef.
  De meting van 11 november gebruikte stock Jellyfin 12.4. Op 27 november is ook de lokale Jelly Leon-image uit
  S-P40-20 gemeten. Voor de P6-image stopte de referentierun op 28 november bij B7.
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
- De kandidaat van 11 november gebruikte de runtime van 1.2.2. De P6-kandidaat bevat de nieuwe kern en encoder op de runtime van 1.3.0. De proef op het uiteindelijke release-image staat open.

### Opnieuw draaien

`run.sh` bewaart het bewijs buiten `.run` als `MGMS_BEWIJS=<map>` gezet is: `pytest.log`, `junit.xml`,
`metingen.json` (alle controles en gemeten feiten), de logs van beide doorgeefluiken (`emby.jsonl`,
`jellyfin.jsonl`) en de containerlogs. De zware CI-run (`ci`, job `mediaservers`) bewaart die map als artefact.
