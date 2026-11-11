# B03 — wijzigingsverzoeken aan MediaGuard

Stand: 2026-11-09 (B03 2.0, inclusief de Emby-besluiten van de gebruiker). Bron van waarheid:
`lvos04/ai-server-manager` → `docs/platform-v2/02_BOUWPLANNEN/B03_UNIVERSELE_MEDIASERVER.md` (hoofdstuk 18
"Deltaverzoeken", §10.5 "MediaGuard: één viewerclient en een harde grens", §10.9.1 "Overname vanuit Emby" en §10.9.2
controlelijst "Emby verwijderen") en `B03_BESLUITEN.md`.

**Stand 2026-11-11:** Δ-MG-AUTH (per server) en Δ-MG-DUAL zijn gebouwd (ADR-0022; zie §3 en §4). De overige
verzoeken zijn ongewijzigd open.

Dit was bij het opstellen (2026-11-09) een **verzoekenlijst, geen bouwwerk**: in die ronde is geen code gewijzigd. MediaGuard krijgt nu een UI-ronde
(andere agent) en verhuist later naar een nieuwe publieke repo; deze verzoeken en de releasevoorwaarde in §1 verhuizen
mee. MediaGuard is eigenaar en beslist; een aanvaard verzoek krijgt een ID in de eigen B04-reeks (nog niet toegekend).
Tot een verzoek er is, werkt Jelly Leon met de terugval uit de tabel in §2.

## 1. HARDE EIS van de gebruiker — releasevoorwaarde voor elke MediaGuard-release

> Alle kijkgeschiedenis (gezien-status, hervatpunten, favorieten, beoordelingen, onthouden spoorkeuzes, gebruikers,
> rechten) en de MediaGuard-voortgang blijven altijd beschikbaar.

Voor MediaGuard is dit een **releasepoort**: geen publicatie zonder het bewijs hieronder (anders *Geblokkeerd* met
reden). Dit geldt voor **elke** release, ook de **eerste release uit de nieuwe publieke repo**.

1. **Upgrade op een gevulde database van de vorige versie.** De nieuwe release start op een gevulde core-database van de
   **direct vorige uitgebrachte versie** en migreert die. Bij de eerste release uit de nieuwe repo is dat de laatste
   release uit `lvos04/media-guard-ultimate` (nu `v1.2.2`); de fixtures en de rooktest gaan mee naar de nieuwe repo.
2. **Gevuld = alle domeinen**, met name de MediaGuard-voortgang: gebruikers, rollen en API-sleutels; instellingen;
   instances, bibliotheek en wanted; identity en policy; recovery, quarantaine en vervangtransacties; encoding- en
   AV1-voortgang (jobs, lanes, optimalisatie, vrijgavecontroles); storage ledger; mediaserver-acties, -wachtrij en
   recent-bekeken paden; migratiewizard; meldingen, audit en taken; AI-advies.
3. **Zonder gegevensverlies, gemeten:** per tabel het aantal rijen en een inhoudshash van de bestaande kolommen vóór en
   na de migratie, plus readback via de API (inloggen met de oude beheerder, instellingen, lopende en afgeronde encodes,
   quarantaine en geschiedenis terugvinden). Elk verschil is een fout, tenzij de migratie het benoemt en het rapport het
   toont.
4. **Terugzetten:** vorige image plus de database van vóór de migratie geeft de oude stand terug (B04-006, B04-009).
5. **UI-rondes wijzigen geen datamodel.** Een release die toch het schema wijzigt (bijv. Δ-MG-DUAL), volgt 1–4.

**Stand 2026-11-10 (contract 1.3, C1.3-MGU-03, ADR-0021): gebouwd.** `scripts/upgradebewijs.py` start de echte
vorige release (`deploy/upgradebewijs/vorige-release.json`: 1.2.2-aio op digest) op de fixture
`tests/fixtures/aio/core-1.2.2` (105 van 108 tabellen gevuld, ook alle voortgangsdomeinen uit punt 2; de AV1-database
vult de poort na de eerste start), daarna de nieuwe versie; per tabel rijen + inhoud vóór/na (punt 3), readback via de
API (inloggen, instellingen, 11 leesroutes), en terugzetten met de vorige image en de snapshot (punt 4). Uitzonderingen
alleen met reden in `tests/fixtures/aio/upgradebewijs-uitzonderingen.json`; het rapport toont ze. Gate: `release.yml`
job `upgradebewijs` vóór `publish`; op elke push als core-kandidaat (`ci.yml`) en in-process
(`apps/core/tests/test_upgradebewijs.py`). Punten (a)–(c) hieronder zijn daarmee opgelost. Niet op een echte NAS.

**Stand 2026-11-11 (Δ-MG-DUAL, ADR-0022 punt 9):** de eerste release met een schemawijziging sinds 1.2.2 (migratie
`150_mediaserver_servers`, voegt alleen toe; `schema_migrations` 35 → 36). De vergelijking per tabel draait nu ook in
de alles-in-één-rooktest (`scripts/aio-smoke.py`, in `release.yml` job `aio-smoke`): rijen en inhoud van elke tabel
van de gevulde 1.2.2-database vóór en na de start van de nieuwe versie, rood bij elk verlies
(`apps/core/tests/test_release_pipeline.py::test_smoke_per_tabel_ziet_elk_verlies`). Lokaal gedraaid op de
kandidaat (runtime van 1.2.2 + core van deze werkmap): niets verloren. De fixture bevat bewust geen geheimen; dat de
Emby-koppeling zelf de upgrade overleeft, bewijst de contracttest (§4). Niet op een echte NAS.

**Vertrekpunt in deze repo (gemeten 2026-11-09):** `release.yml` job `aio-smoke` start het alles-in-één-image op een
gevulde database van een eerdere versie (`scripts/aio-smoke.py`); fixture `tests/fixtures/aio/core-1.2.1.{sql,json}`,
gemaakt met `scripts/make-aio-fixture.py`; dezelfde controle in
`apps/core/tests/test_release_pipeline.py::test_fixture_database_migrates_and_stays_usable`. **Wat nog ontbreekt voor de
HARDE EIS:** (a) de rooktest kiest nu de nieuwste fixture die niet nieuwer is dan de release, niet per se de direct
vorige versie — vóór de volgende release moet er een `core-1.2.2`-fixture zijn; (b) de fixture vult nu alleen een
beheerder, een kijker, instellingen, een API-sleutel, meldingen, taken en auditregels, niet de voortgangsdomeinen uit
punt 2; (c) er is geen vergelijking per tabel (punt 3), alleen readback van inloggen, schemaversies en instellingen.

## 2. Overzicht van de verzoeken

| ID | Wat MediaGuard zou moeten doen | Nodig voor (B03) | Besluit | Status 2026-11-09 | Zonder dit (terugval) |
|---|---|---|---|---|---|
| **Δ-MG-AUTH** (per server) | mediaserverkoppeling per server: Jelly Leon 12.x met `Authorization: MediaBrowser Token=…`; Emby houdt `X-Emby-Token` (§3) | **exitcriterium P4-0** (samen met Δ-MG-DUAL); kijkpauze, koppelcontrole G8, readback na vervanging (B03-014, B03-009, B03-056, B03-064; JLT-73, JLT-69) | **BG-01 genomen**: MediaGuard omzetten, `EnableLegacyAuthorization` blijft uit, derden-apps eerst testen; Emby-besluit: per server | **gebouwd 2026-11-11** (ADR-0022): Sim-getest en contracttest tegen echte containers (Emby 4.10.2.0, stock Jellyfin 12.4); NIET GEVALIDEERD (echte NAS, echte Jelly Leon-image) | tot de NAS is bijgewerkt: geen B03-batch op de NAS, Jelly Leon toont `mediaguard: onbekend`, geen kijkpauze bij Jelly Leon-sessies |
| **Δ-MG-DUAL** (nieuw) | MediaGuard volgt **Emby én Jelly Leon tegelijk**; kijkpauze als iemand op één van beide kijkt; de Emby-koppeling onderbreekt nooit (§4) | **exitcriterium P4-0** (samen met Δ-MG-AUTH); B03-014, B03-015 (stap 5 van de controlelijst "Emby verwijderen"); JLT-73, JLT-74 | **genomen** (Emby-besluit van de gebruiker, 2026-11-09) | **gebouwd 2026-11-11** (ADR-0022): Sim-getest en contracttest tegen echte containers; NIET GEVALIDEERD (echte NAS, echte Jelly Leon-image, echte spelers) | tot de NAS is bijgewerkt: MediaGuard volgt alleen Emby: geen kijkpauze bij Jelly Leon-sessies, Jelly Leon `mediaguard: onbekend`, geen B03-batch op de NAS |
| Δ-MG-1 | externe kwaliteitsbevindingen per bestand innemen (bron `jelly-leon`, bewijs-ID, type, ernst) zodat de planner-bak `corrupt` gevuld wordt; MediaGuard beslist | F4 herstel (B03-056; JLT-37) | **BG-05 open** (vóór golf 3) | open | F4 meldt alleen, met incident en deeplink |
| Δ-MG-2 | een aanwezig maar beschadigd bestand opnieuw binnenhalen (blocklist huidige release + zoeken binnen beleid), als connector-propose/apply of gescopete route | F4 herstel (B03-056; JLT-37) | **BG-05 open** | open | `geen_route` + deeplink |
| Δ-MG-3 | sleutelscopes (P3-02) | latere mutaties met goedkeuring (B03-009) | geen eigen BG; hangt aan BG-04 (sleutel in Jelly Leon, alleen viewer; open, vóór golf 1) en BG-05 | open | Jelly Leon blijft viewer |
| Δ-MG-4 | betere kijkpauze: (a) vasthouden zolang een Watch Together-groep gepauzeerd is; (b) transcode-bewust: een lopende GPU-encode op dezelfde GPU bevriezen bij live; met Δ-MG-DUAL voor sessies op beide servers | F1, F5 (B03-064; JLT-45); EX08 volledig; **dringend** in topologie `a310_only` | BG-27 open (golf 2; meting met MediaGuard zelf gepauzeerd) | open | EX08 maar deels gedekt; live kan haperen naast AV1 |
| Δ-MG-5 (optioneel) | opzoeken op extern ID of pad | F3, F4 koppeling | geen BG; eigenaar beslist | open | titel + ID-filter, `ambiguous_mg_item` bij twijfel |
| Δ-MG-6 (optioneel) | taaltag corrigeren (remux binnen MG-recovery) en "Bazarr: synchroniseer deze ondertitel" doorgeven | F4 | geen BG; eigenaar beslist | open | tonen + deeplink |
| Δ-MG-7 (optioneel) | clientmatrix (aandeel kijkers zonder AV1-/DV-profiel-10-decode) meewegen vóór een AV1-omzetting | F5 | geen BG; eigenaar beslist | open | MediaGuard beslist zonder die informatie |
| Δ-MG-8 (optioneel) | status "NL alleen machinevertaling" in de MediaGuard-UI | F3 | raakt BG-19 (open, golf 3); niet in de huidige UI-ronde zonder besluit | open | F3 werkt ook zonder |
| Δ-MG-DV (optioneel) | bevestigde DV-ketens van Jelly Leon lezen (connector-itemtype `device`, alleen lezen) | F5 | geen BG; eigenaar beslist | open | vergelijking in F5 + deeplink |
| **Δ-CONTRACT-MG** (eigenaar: ai-server-manager; raakt MediaGuard) | contract 1.2 §2.3 regels 2–3: de Hub mag met goedkeuring (R2) MediaGuard laten `wanted.search`, `quarantine.release_check`, `file.reacquire`, `quality.report` en `subtitles.search` uitvoeren; `quarantine.restore` R3, nooit door AI voorgesteld; Δ-HUB-1 pas daarna | F3 "eerst menselijk zoeken", F4 herstel via de Hub (B03-009, B03-056) | **BG-05 open** (advies: ja, na P2/P3) | `Geblokkeerd (contract)` | alleen deeplinks |

Grens (ongewijzigd, ADR-B03-09): Jelly Leon gebruikt alleen een viewer-sleutel, roept alleen GET-routes en het
niet-muterende `POST /api/v1/audio/inspect` aan, en wijzigt nooit bestanden of MediaGuard-instellingen.

## 3. Δ-MG-AUTH per server

Gemeten in deze repo: `X-Emby-Token` staat in `apps/core/mediaguard/mediaserver/clients.py:107` (voor soort `emby`
én `jellyfin`), `services/encoder/app/integraties.py:24` (`jellyfin_is_playing`) en `services/encoder/app/api.py:3192`.
Jellyfin 12.x weigert `X-Emby-Token`, `api_key=` en `X-Emby-Authorization` zodra `EnableLegacyAuthorization` uit staat.

- Soort `emby`: ongewijzigd `X-Emby-Token` en paden onder `/emby`.
- Soort `jellyfin` (Jelly Leon 12.x, stock-Jellyfin 12.x): `Authorization: MediaBrowser Token="…"`; geen sleutel in
  URL's of logs. Alle drie de plekken tegelijk.
- Bewijs: contracttest tegen stock `jellyfin/jellyfin:12.4` (golf 0) en `jelly-leon:12.x` met legacy-auth uit (info,
  sleutelcontrole, sessies, refresh, afspeeltest, NL-ondertitels) **én** een regressietest tegen een echte Emby-container
  (`emby/embyserver` op vaste versie en digest, dezelfde als in JLT-70; op 2026-11-09 `4.10.2.0`) met ongewijzigd
  `X-Emby-Token`.

**Stand 2026-11-11: gebouwd** (ADR-0022 besluit 3 en 4). De kop per server staat op de drie plekken
(`mediaserver/clients.py`, encoder `integraties.py` en `api.py`). De contracttest `tests/contract/mediaserver/`
(deel A) draait tegen `emby/embyserver:4.10.2.0` en stock `jellyfin/jellyfin:12.4`, beide op de digest uit het
B03-plan: info, sleutelcontrole, sessies, verversen, afspeeltest en NL-ondertitels; Jellyfin 12.4 weigert
`X-Emby-Token`, `api_key=` en `X-Emby-Authorization` (401) en kent `/emby` niet (404); MediaGuard stuurt Jellyfin
alleen `Authorization: MediaBrowser` en Emby alleen `X-Emby-Token` (gemeten in het doorgeefluik). Uitkomst:
`tests/contract/mediaserver/RESULTS.md`. **Nog niet:** tegen een `jelly-leon:12.x`-image (die bouwt jelly-leon;
de test neemt elke image via `JELLYFIN_IMAGE` aan zodra die er is) — NIET GEVALIDEERD.

## 4. Δ-MG-DUAL — Emby én Jelly Leon tegelijk

Nu (gemeten): één mediaserver per installatie — instelling `mediaserver.kind` (`auto`/`emby`/`jellyfin`/`plex`/`off`),
één URL en één sleutel (`mediaserver.api_key`) in `apps/core/mediaguard/mediaserver/service.py:69-93`; ontdekking
sorteert Emby eerst. Eisen:

1. Meerdere servers tegelijk (in elk geval Emby en Jelly Leon), elk met eigen soort, URL, sleutel en auth-kop (§3).
2. De bestaande Emby-koppeling (URL, sleutel, padvertaling) wordt bij de upgrade automatisch de eerste server; de
   gebruiker voert niets opnieuw in. Deze schemawijziging valt onder §1.
3. Kijkpauze: een actieve sessie op één van beide servers houdt nieuwe encodes vast (core `apply_hold` en de
   encoder-controle `jellyfin_is_playing`); een onbereikbare server maakt de pauze voor de andere niet ongedaan.
4. Per server: refresh na vervanging, afspeeltest en NL-ondertitelcontrole, met de uitkomst per server zichtbaar.
5. `GET /api/v1/mediaserver/status` blijft achterwaarts compatibel (bestaande velden blijven) en krijgt een lijst per
   server met alleen soort, URL en verbonden — nooit sessies of wie wat kijkt. Gevolg voor Jelly Leon: de koppelcontrole
   G8 en de veldenwitte lijst (B03-009, B03-064) moeten die lijst lezen; tot dan meldt Jelly Leon "MediaGuard volgt
   Emby" als waarschuwing.
6. Losmaken (stap 5 van de controlelijst "Emby verwijderen", plan §10.9.2, B03-015): de beheerder haalt Emby in
   MediaGuard zelf uit de lijst; MediaGuard stopt met pollen, refreshen en testen voor Emby, houdt de bestaande
   actiegeschiedenis binnen de bewaarregel, geeft daarna geen incident en volgt alleen Jelly Leon.
7. **De Emby-koppeling onderbreekt op geen enkel moment**: niet bij de upgrade, niet bij het toevoegen van Jelly Leon en
   niet bij een fout in de Jelly Leon-koppeling.

Bewijs: in golf 0 JLT-73 (een MediaGuard-simulator met Δ-MG-AUTH per server en Δ-MG-DUAL volgt Emby en Jelly Leon
tegelijk; kijkpauze bij een sessie op elk van beide; de Emby-koppeling valt op geen enkel moment weg). In deze repo
daarnaast een contracttest met de echte MediaGuard-image en twee echte containers (Emby zoals in JLT-70, plus stock 12.4
of `jelly-leon:12.x`): sessie op Emby → pauze; sessie op Jelly Leon → pauze; Jelly Leon onbereikbaar → Emby-functies
werken door; upgrade van een gevulde database met één Emby-koppeling → Emby blijft actief; Emby losmaken → geen fouten.
Op de NAS: JLT-74 (*Geblokkeerd*, NIET GEVALIDEERD (echte NAS)).

**Stand 2026-11-11: gebouwd** (ADR-0022). Per eis:

| Eis | Hoe | Bewijs |
|---|---|---|
| 1 meerdere servers | server 1 = de bestaande koppeling; extra servers in `mediaserver_servers` (hoogstens 8), elk met eigen soort, adres, geheim en kop | `test_mediaserver_dual.py`, contracttest b3 |
| 2 upgrade zonder invoer | "nul-migratie": de instellingen en het geheim van 1.2.2 blijven waar ze stonden en zijn server 1 | contracttest b2 (echte 1.2.2 → kandidaat op dezelfde datamap: geheim en instellingen byte-gelijk, geen `POST /config`) |
| 3 kijkpauze bij elk van beide | eigen poll en foutstatus per server; pauze zodra iemand op één server kijkt; een server zonder contact telt hoogstens 10 minuten mee (interpretatie, ADR-0022 besluit 5) | contracttest b4, b5, b6; de grens van 10 minuten alleen Sim-getest |
| 4 per server verversen, afspeeltest, NL-ondertitels | uitkomst per server in `mediaserver_server_results`; samenvatting per overdracht = slechtste uitkomst | `test_mediaserver_dual.py`; tegen de echte servers op clientniveau (deel A) |
| 5 status-API achterwaarts compatibel | bestaande velden blijven; `servers[]` erbij, zonder sessies, titels of gebruikers | `test_mediaserver_dual.py::test_status_servers_lijst_zonder_sessies_of_titels`, OpenAPI-export |
| 6 losmaken | soort "Uit" voor server 1 (adres en sleutel bewaard), uit of verwijderen voor een extra server; geen poll, geen incident | contracttest b7 en b8 |
| 7 Emby nooit onderbroken | geen kopie van de bestaande koppeling; fouten bij een andere server raken Emby niet | contracttest b2, b3, b6 en b9 (over de hele run alleen 200 van Emby, geen 401/403) |

De encoder houdt één eigen mediaserver voor de losse instelling "pauzeer bij kijken" en kreeg alleen de kop per server;
in de alles-in-één-app stuurt core de kijkpauze voor alle servers (ADR-0022 besluit 6). Voor Jelly Leon: de
koppelcontrole G8 kan nu `servers[]` lezen (werk in jelly-leon). **NIET GEVALIDEERD (echte NAS):** de echte Emby van
de gebruiker, een echte Jelly Leon-image en echte spelers; sessies in de contracttest zijn nagebootst met de sessie-API
van de servers.

## 5. Rijen in `EXCEL_TAKEN.json` van deze repo

Volgens `EXCEL_TAKEN_PER_REPO.json` (alleen de rijen waarvoor MediaGuard zelf iets levert):

- **Werkpakketten** (start *Niet gestart*): B03-009 (uitgebreid; contracttest met de viewerclient), B03-014
  (Δ-MG-AUTH per server, Δ-MG-DUAL), B03-015 (Δ-MG-DUAL, stap 5 "Emby verwijderen"), B03-056 (Δ-MG-1/2), B03-064
  (Δ-MG-4).
- **Testen**: JLT-23, JLT-37, JLT-45, JLT-73 (*Niet gestart*); JLT-49, JLT-69, JLT-74 (*Geblokkeerd*, NIET
  GEVALIDEERD (echte NAS) of (hardware)). Testrijen staan in het tasks.json-formaat zoals ai-server-manager ze opneemt
  (Soort `Testen`, Fase `P6 - E2E`, Afhankelijkheid "Testrij (blad Testmatrix); startstatus …; bewijs bij …").
- De rijteksten zijn gelijk aan `08_EXCEL/tasks.json` en `08_EXCEL/e2e_cases.json` van ai-server-manager.
- Nooit *Gereed* of *Geslaagd* in deze bouwomgeving. Sinds 2026-11-11 staan B03-014, B03-015, JLT-73 en JLT-74 in
  `EVIDENCE.json` met alleen het deel dat MediaGuard levert (Δ-MG-AUTH, Δ-MG-DUAL); de rest van die rijen is werk
  van jelly-leon.

## 6. Besluiten

| Besluit | Inhoud | Stand |
|---|---|---|
| HARDE EIS | kijkgeschiedenis en MediaGuard-voortgang altijd beschikbaar; releasevoorwaarde §1 | genomen (gebruiker, 2026-11-09) |
| BG-01 | MediaGuard omzetten naar `Authorization: MediaBrowser Token=…`; legacy-auth blijft uit; derden-apps eerst testen | genomen (2026-11-09) |
| Emby | Emby blijft ongewijzigd draaien; MediaGuard volgt Emby én Jelly Leon (Δ-MG-DUAL); Emby-koppeling nooit onderbroken; de gebruiker verwijdert Emby zelf na de controlelijst | genomen (2026-11-09) |
| BG-04 | de MediaGuard-sleutel van Jelly Leon loopt via de Hub (alleen viewer); vraagt in deze repo geen nieuwe sleutel of route (ADR-0022 besluit 10) | genomen (gebruiker) |
| BG-05 | mag de Hub MediaGuard laten herstellen of zoeken (Δ-CONTRACT-MG, Δ-MG-1, Δ-MG-2) | open — vóór golf 3 (tot dan deeplinks) |
| BG-19 | label "Machinevertaling" (raakt Δ-MG-8) | open — golf 3 |
| BG-27 | A310 voor live terwijl MediaGuard encodeert; meting met MediaGuard zelf gepauzeerd (raakt Δ-MG-4) | open — golf 2 |
