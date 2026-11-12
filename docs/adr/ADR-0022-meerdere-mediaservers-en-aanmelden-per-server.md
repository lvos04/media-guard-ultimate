# ADR-0022 — Meerdere mediaservers tegelijk (Δ-MG-DUAL) en aanmelden per server (Δ-MG-AUTH)

- **Status:** geaccepteerd
- **Datum:** 2026-11-11
- **Fase:** P4-0 (exitcriterium), contract 1.3 §2.3 regel 2, C1.3-MGU-04; `B03_DELTAVERZOEKEN.md` §1, §3 en §4
- **Context:** de gebruiker kijkt nu via **Emby** en houdt Emby tot hij hem zelf verwijdert. Jelly Leon (Jellyfin
  12.x) komt ernaast. Besluiten van de gebruiker: MediaGuard volgt Emby én Jelly Leon tegelijk, de Emby-koppeling
  onderbreekt op geen enkel moment, en kijkgeschiedenis en MediaGuard-voortgang blijven altijd bewaard (HARDE EIS).
  Tot en met 1.2.2 kende MediaGuard één mediaserver (`mediaserver.kind`, `mediaserver.url`, geheim
  `mediaserver.api_key`) en stuurde naar Emby én Jellyfin `X-Emby-Token`. Jellyfin 12.x weigert die kop zolang
  `EnableLegacyAuthorization` uit staat (BG-01: dat blijft uit).

## Gemeten tegen de echte servers

Basis voor de keuzes hieronder, gemeten tegen de echte containers (images op digest). De contracttest
`tests/contract/mediaserver/run.sh` herhaalt bij elke run: de openbare info van beide servers, `/emby` op Jellyfin,
alle regels van de kolom Jellyfin behalve de laatste, en voor Emby `X-Emby-Token` en "zonder sleutel". De overige
cellen van de kolom Emby (`api_key=`, `X-Emby-Authorization`, `Authorization: MediaBrowser`) en de regel "tijdens het
opstarten" zijn eenmalig met de hand gemeten tijdens de bouw; MediaGuard leunt daar niet op.

| | `emby/embyserver:4.10.2.0` | `jellyfin/jellyfin:12.4` |
|---|---|---|
| openbare info (`/System/Info/Public`) | `ServerName`, `Version`, `Id`, adressen — **geen `ProductName`** | `ProductName` = `Jellyfin Server` |
| paden onder `/emby` | ja (en ook zonder) | **nee: 404** |
| `X-Emby-Token` | 200 | **401** |
| `api_key=` in de URL | 200 (MediaGuard gebruikt dit niet) | **401** |
| `X-Emby-Authorization` | 200 | **401** |
| `Authorization: MediaBrowser … Token="…"` | 200 | 200 |
| zonder sleutel | 401 | 401 |
| tijdens het opstarten | — | openbare info in **camelCase** (`productName`), andere routes 503 met HTML |

Jellyfin 12.4 neemt een sleutel ook nog aan als `?ApiKey=` in de URL. MediaGuard gebruikt dat niet: een sleutel
staat nooit in een URL of in een logregel.

Jellyfin 12.4 slaat de scan over van een bibliotheek die tijdens de scan van de startwizard is toegevoegd
(`refreshLibrary=true` heeft dan geen effect). Dat raakt alleen de opstelling van de contracttest (die vraagt de scan
opnieuw aan tot de films er staan), niet MediaGuard: die vraagt bij een import een gerichte verversing van het pad.

## Besluiten

1. **Server 1 is de bestaande koppeling, zonder kopie ("nul-migratie").** Soort, adres, sleutel en padvertaling van
   1.2.2 blijven waar ze stonden (`mediaserver.kind`, `mediaserver.url`, geheim `mediaserver.api_key`, statussleutels
   `mediaserver_path_map`, `mediaserver_connected`, `mediaserver_full_refresh_at`). Na de upgrade is dat zonder één
   handeling server 1. Reden: elke kopie naar een nieuwe plek is een moment waarop de Emby-koppeling kan wegvallen
   (half gelukte migratie, sleutel die niet meer te ontsleutelen is), en een terugzet naar de vorige image moet
   dezelfde koppeling terugvinden (`B03_DELTAVERZOEKEN.md` §1 punt 4). Afgewezen: alle servers naar één nieuwe tabel
   verplaatsen.
2. **Extra servers komen erbij in eigen opslag.** Tabel `mediaserver_servers` (id vanaf 2, hoogstens 8), geheim
   `mediaserver.server.<id>.api_key`, statussleutels `mediaserver:<id>:<naam>`. Uitkomsten per server en per
   overdracht staan in `mediaserver_server_results`; `mediaserver_pending` blijft de samenvatting per overdracht die
   de web-UI en de AV1-vergelijking (`GET /api/v1/mediaserver/playback/{encoding_job_id}`) al lezen.
   `mediaserver_actions` krijgt de kolom `server_id`
   (leeg voor rijen van vóór deze versie). Migratie `150_mediaserver_servers` voegt alleen toe: geen bestaande rij
   wordt gewijzigd, verplaatst of gewist. De reeks 060–079 is vol na `079_mediaserver`; mediaservers gaan verder in
   150–159.
3. **Aanmelden per server.** Soort `emby`: ongewijzigd `X-Emby-Token` en paden onder `/emby`. Soort `jellyfin`
   (Jelly Leon 12.x en stock 12.x): alleen `Authorization: MediaBrowser Client="MediaGuard", …, Token="…"`, zonder
   `/emby`. Soort `plex`: ongewijzigd `X-Plex-Token`. Een nieuw ingevoerde sleutel met een spatie, aanhalingsteken,
   backslash of komma wordt geweigerd (422): die tekens breken de MediaBrowser-kop. Een sleutel die 1.2.2 al
   opsloeg (zonder tekencontrole) blijft voor Emby werken als `X-Emby-Token`: die tekens kunnen in die kop geen kop
   toevoegen. CR, LF, NUL, andere stuurtekens en niet-ASCII gaan nooit de deur uit (kopinjectie; 1.2.2 kon ze ook
   niet versturen). De logfilter maskeert nu ook `Token="…"` en `X-Emby-Token`. Dezelfde regels staan op de drie
   plekken uit het verzoek: `clients.py` (core), en `integraties.py` en `api.py` (encoder).
   - **Omleidingen alleen op dezelfde server** (besluit van de gebruiker, 2026-11-12): core volgt een omleiding
     alleen naar dezelfde host en poort, of van http naar https op dezelfde host met de standaardpoorten. Wijst
     ze naar een ander adres, dan de melding "server stuurt door naar een ander adres: <adres>" en gaat er geen
     verzoek (dus geen sleutel) daarheen. Alleen de downloadlink van OpenSubtitles, die geen sleutel draagt, mag
     zoals in 1.2.2 naar een ander adres doorverwijzen.
   - **Foutpagina's**: tekst uit het antwoord van een server komt alleen in een melding nadat de meegestuurde
     sleutels eruit zijn gehaald (ook als de server ze terugkaatst) en de logfilter erover is gegaan.
   - **Testknop van de encoder**: de opgeslagen sleutel gaat alleen naar het opgeslagen adres; een ander adres
     test je met een eigen sleutel. Instellingen van de encoder-koppelingen wijzigen of testen via core mag alleen
     een beheerder, net als `POST /api/v1/mediaserver/config`.
4. **Herkennen: alleen een server die zich als Jellyfin meldt, krijgt de nieuwe kop.** Een server is Jellyfin als
   `ProductName` op de openbare info-route "jellyfin" bevat (hoofdletterongevoelig, ook in camelCase). Al het andere
   blijft Emby met `X-Emby-Token`. Reden: Emby 4.10.2.0 geeft geen `ProductName`; een regel "geen Emby in de naam,
   dus Jellyfin" zou de bestaande Emby-koppeling een andere kop geven. Jelly Leon meldt zich met
   `ProductName = "Jellyfin Server"` (gelezen in jelly-leon, `ForkInfo.cs`) en valt dus onder Jellyfin.
   - Soort `auto` (de standaard van 1.2.2): de gevonden soort wordt onthouden; een Emby blijft Emby.
   - Soort `emby` gekozen op een Jellyfin-adres: de test zegt "Op dit adres draait Jellyfin, niet Emby: kies de
     juiste soort." in plaats van een 404 of 401.
   - Soort `jellyfin` gekozen op een Emby-adres in de **bestaande** koppeling: blijft werken en wordt niet
     tegengehouden. Emby 4.10.2.0 neemt de MediaBrowser-kop aan (gemeten), en een koppeling die vandaag werkt, mag
     door deze versie niet stoppen. Bij een **extra** server geeft dezelfde vergissing wel de melding hierboven.
   - De info-route wordt eerst onder `/emby` gevraagd en bij 404 zonder; zo vindt "Zoek automatisch" ook Jellyfin
     12.x.
5. **Kijkpauze over alle servers.** Elke gevolgde server (aan, met adres en sleutel) heeft een eigen poll en een
   eigen foutstatus; een fout bij de ene server raakt de andere nooit. Met één gevolgde server loopt precies het pad
   van 1.2.2. Met meer dan één geldt: nieuwe encodes wachten zodra iemand op één van de servers kijkt. Een server
   die even niet antwoordt, houdt zijn laatst bekende stand ("er werd gekeken") hoogstens **10 minuten** vast;
   daarna telt hij niet meer mee. Reden: `B03_DELTAVERZOEKEN.md` §4 punt 3 ("een onbereikbare server maakt de pauze
   voor de andere niet ongedaan") zegt niets over de eigen laatste stand van de onbereikbare server; zonder grens zou
   een uitgezette Jelly Leon de AV1-wachtrij voor onbepaalde tijd tegenhouden, mét grens begint er hoogstens tien
   minuten te vroeg een encode. Opheffen blijft pas na twee stille metingen.
6. **De encoder houdt één eigen mediaserver.** De losse encoder-instelling "pauzeer bij kijken"
   (`jellyfin_is_playing`) blijft één adres met één sleutel en krijgt alleen de kop per server (besluit 3 en 4). De
   soort leest hij één keer per adres op de openbare info-route (zonder sleutel; opnieuw na een adreswijziging of
   een fout), zodat Emby per poll alleen `/Sessions` krijgt, zoals in 1.2.2. In de
   alles-in-één-app stuurt core de kijkpauze voor alle servers via `set_playback_hold`; een tweede lijst met servers
   in de encoder zou dezelfde gegevens op twee plekken zetten.
7. **Status-API blijft achterwaarts compatibel.** `GET /api/v1/mediaserver/status` krijgt `servers[]` erbij; de
   bestaande bovenste velden blijven en beschrijven de eerste ingestelde server (server 1; staat die uit, dan de
   eerste extra server die aan staat). Per server alleen: soort, naam, adres, sleutel ja/nee, aan/uit, verbonden,
   laatste contact, kijkpauze ja/nee, padvertaling en aantallen. **Nooit** sessies, titels, gebruikers of apparaten.
   `POST /config` en `POST /test` werken zonder `server_id` precies als in 1.2.2.
8. **Losmaken** (controlelijst "Emby verwijderen", stap 5). Server 1 maak je los met soort "Uit": adres en sleutel
   blijven bewaard, zodat weer aanzetten niets opnieuw vraagt. Verwijderen van server 1 bestaat niet (422). Een extra
   server zet je uit of verwijder je; bij verwijderen gaan alleen de eigen rij, het eigen geheim en de eigen
   statussleutels weg. Open werk voor een losgemaakte server wordt afgesloten als "overgeslagen"; er komt geen
   incident, en de actiegeschiedenis blijft binnen de bewaarregel. Een losgemaakte server 1 blijft in `servers[]`
   staan met `enabled: false` zolang adres of sleutel bewaard zijn, zodat de beheerder ziet wat er bewaard is en
   hem in één handeling weer koppelt. Is de soort van vóór het losmaken niet bekend (uitgezet in 1.2.2 zonder
   meting), dan staat er `auto`: er wordt niets geraden.
9. **Upgradebewijs en de Emby-koppeling.** De fixture `core-1.2.2` bevat bewust geen geheimen (tabel `secrets` staat
   bij de uitzonderingen van de vergelijking: een geheim hoort niet in een openbare repo). De eis "upgrade van een
   gevulde database met één Emby-koppeling → Emby blijft actief" wordt daarom bewezen in de contracttest: de echte
   1.2.2-image krijgt via zijn eigen API een koppeling met de echte Emby-container, daarna start de kandidaat op
   dezelfde datamap. De vergelijking per tabel draait nu ook in de alles-in-één-rooktest (`scripts/aio-smoke.py`).
10. **BG-04** (de MediaGuard-sleutel van Jelly Leon loopt via de Hub) vraagt in deze repo niets nieuws: er komt geen
    eigen sleutel voor Jelly Leon en geen nieuwe route. De sleutel waar dit ADR over gaat, is de omgekeerde richting:
    de API-sleutel van de mediaserver die de beheerder in MediaGuard invult.

## Gevolgen

- De web-UI toont een lijst mediaservers in plaats van één; teksten in nl en en-US. De Android-app heeft geen
  scherm voor mediaservers (de API voor de app levert die gegevens niet) en verandert hierdoor niet; dat de app ook
  in en-US bestaat, staat los hiervan in ADR-0023.
- `deploy/release-manifest.json` noemt `150_mediaserver_servers`; de rooktest en het upgradebewijs eisen dat.
- Jelly Leon kan `servers[]` lezen voor de koppelcontrole G8 (B03-009, B03-064); dat is werk in jelly-leon.
- Niet gebouwd: Δ-MG-4 (kijkpauze bij een gepauzeerde Watch Together-groep en transcode-bewust bevriezen), BG-27 open.

## Bewijs en grenzen

- Sim-getest: `apps/core/tests/test_mediaserver.py`, `test_mediaserver_dual.py` en
  `services/encoder/tests/test_integraties.py` met nagebouwde servers die de metingen hierboven volgen.
- Contracttest met echte containers (Emby 4.10.2.0, Jellyfin 12.4, MediaGuard 1.2.2 en de kandidaat):
  `tests/contract/mediaserver/`; uitkomst in `RESULTS.md` aldaar (24 tests, 113 controles). In CI draait hij in de
  zware run (`ci`, job `mediaservers`: nachtelijk en via `workflow_dispatch` met `heavy=true`), lokaal via
  `scripts/test-all.sh --only mediaserver`.
- De afspeeltest en de controle op Nederlandse ondertitels zijn tegen de echte servers bewezen op clientniveau
  (deel A); de hele keten in de achtergrondlus en de regel "kijkpauze van een server zonder contact vervalt na
  10 minuten" zijn Sim-getest.
- **NIET GEVALIDEERD (echte NAS):** de echte Emby van de gebruiker met zijn bibliotheek en kijkgeschiedenis, een echte
  Jelly Leon-image, en echte spelers. Sessies in de contracttest zijn nagebootst met de sessie-API van de servers.
