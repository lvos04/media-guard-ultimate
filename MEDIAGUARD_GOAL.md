# MEDIAGUARD ULTIMATE
## Definitief bouwplan en autonome Claude Code-hoofdopdracht

**Status:** definitieve uitvoerbare productspecificatie / implementatieopdracht — AV1-repository geïnspecteerd; Dolby Vision, UI-first, vier gescheiden instanties en optionele AI Decision Advisor bijgewerkt (v4)  
**Datum:** 2 november 2026  
**Doel:** één complete TrueNAS SCALE-app die bestaande Sonarr/Radarr-functionaliteit met eigen forks vervangt, de volledige huidige bibliotheek overneemt, toekomstige afleveringen blijft volgen, ontbrekende media actief probeert aan te vullen, media onafhankelijk verifieert en intelligent AV1 encodeert.  
**AV1-broncode ontvangen:** https://github.com/lvos04/av1 (branch `master`; actuele commit pinnen bij de implementatie). De code, veiligheidsregels, UI en tests zijn onderzocht op architectuurniveau; runtime-/hardwaretests volgen tijdens de bouw. Zie aanvullingen in DEEL P en de DV/UI-aanpassingen in DEEL Q. **DEEL R (vier instanties) en DEEL S (AI Decision Advisor) zijn de nieuwste leidende specificaties**, inclusief eventuele correcties op eerdere delen.

---

# DEEL A — ÉÉN CONSOLIDATED GOAL-PROMPT VOOR CLAUDE CODE

> Gebruik dit gehele bestand als de opdracht. Plaats het bij voorkeur in de root van een nieuwe Git-repository als `MEDIAGUARD_GOAL.md` en stuur in Claude Code: **`/goal Implementeer MEDIAGUARD_GOAL.md volledig. Begin met repository-audit en een uitvoerbaar takenplan, werk zelfstandig en test end-to-end. Integreer vanaf het begin de bestaande repository `https://github.com/lvos04/av1` en behoud haar bewezen media-/veiligheidslogica; voer de TrueNAS-port en end-to-end-tests uit.`**  
> Als `/goal` niet beschikbaar is in de gebruikte Claude Code-omgeving, gebruik dezelfde zin als normale opdracht. Veronderstel geen bestaande projectbestanden of geïnstalleerde agentplugins.

## A1. Jouw rol en werkafspraak

Je bent de lead architect, senior full-stack engineer, .NET-forkmaintainer, media-engineer, TrueNAS-/Docker-specialist, QA lead, security reviewer en UX-ontwerper voor **MediaGuard Ultimate**. Bouw daadwerkelijk een werkend, testbaar product, niet alleen een demo, scaffold of ontwerpdocument.

**Werkafspraken:**

1. Inspecteer eerst de echte ontwikkelomgeving, repository, beschikbare toolchains en actuele upstream code. Versies in dit document zijn uitgangspunten; controleer actuele upstream versies voordat je ze vastpint.
2. Bouw autonoom door per afgebakende fase. Houd voortgang, besluiten en tests in `docs/PROGRESS.md`, `docs/DECISIONS.md` en `docs/TEST_RESULTS.md` bij. Vervang aannames door ontdekte feiten.
3. Als subagents of parallelle ontwikkeltaken beschikbaar zijn: splits onafhankelijke werkstromen (forks, migratie, identity/audio, AV1, UI, QA), maar laat één integrator de database- en API-contracten beheren. Geen onbeheerde gelijktijdige wijzigingen in dezelfde modules.
4. Stel alleen vragen voor ontbrekende externe secrets/hosttoegang, rechten om wijzigingen buiten de repository uit te voeren, of acties met onomkeerbaar risico. Gebruik anders veilige defaults, feature flags en een expliciet `TODO-INTEGRATION`-document. Vraag niet herhaaldelijk naar al bekende keuzes.
5. Vraag eventueel eenmalig sudo-elevatie als de omgeving dat vereist, maar leg geen wachtwoorden of sudo-tickets blijvend vast. Pas geen hostbrede configuratie of echte media aan zonder uitdrukkelijke toestemming.
6. Alle functies moeten reëel werken met testfixtures en testcontainers. Mocks zijn toegestaan binnen geautomatiseerde tests, niet als schijnbare productie-integraties.
7. Implementeer kleine, aantoonbaar werkende vertical slices; voer na iedere fase build, tests, statische analyse en een kort integratiepad uit.
8. Start alle diensten aantoonbaar en test de GUI via browser/E2E-tests. Een groene CI zonder werkende app is geen oplevering.
9. **Laat de huidige productie-Sonarr en -Radarr onaangeroerd totdat een gecontroleerde cutover is goedgekeurd en gevalideerd.** Verwijder of schakel de oude installaties niet zelfstandig uit.
10. Gebruik geen claim '100% juiste episode' of 'Dolby Vision altijd behouden' zonder daadwerkelijk aantoonbare verificatie. Onbekende gevallen krijgen een expliciete onzekerheidsstatus en veilige fallback.
11. Respecteer de GPL-3.0-licenties van Sonarr/Radarr, upstream-notices en broncodeverplichtingen voor verspreide aangepaste binaries. Controleer ook licenties van alle overige componenten.
12. Lever productiegerichte handleidingen, migratie/rollback-instructies, Docker-images/buildrecepten, Compose-installatie, migratie- en compatibility-tests op. Sluit af met concrete bewijsstukken en eventuele werkelijke blokkades.

## A2. Ononderhandelbaar einddoel

Een gebruiker installeert **één MediaGuard Ultimate-app** op TrueNAS SCALE met een Nederlands webdashboard. Intern bevat de installatie één beheerde Sonarr-forkcodebasis en één beheerde Radarr-forkcodebasis, met standaard **vier afzonderlijke runtime-instanties** (series, anime, films, animefilms), plus MediaGuard-services. De gebruiker kan uiteindelijk de *oude, afzonderlijk geïnstalleerde* Sonarr/Radarr stoppen, terwijl de **fork-services binnen MediaGuard doorlopen** en zelfstandig series, films, nieuwe afleveringen, indexers, downloads, imports, kwaliteitsupgrades en Seerr-verzoeken afhandelen.

De gebruiker moet zijn bestaande bibliotheek en configuratie veilig kunnen importeren en daarna automatisch optimaliseren. Belangrijk: na het afschakelen van de oorspronkelijke installaties mag de functionaliteit voor nieuwe afleveringen en ontbrekende afleveringen **niet** wegvallen.

**Vastgestelde gebruikerskeuzes:**
- TrueNAS SCALE; host-CPU: **Intel Core i7-14700**.
- AV1-hardware-encoding bij voorkeur met goedkope **Intel Arc A310** (definitieve aanschaf afhankelijk van compatibiliteits-/benchmarktest; geen GPU als harde voorwaarde).
- 4K waar werkelijk zinvol en beschikbaar; originele resolutie en kwaliteit beschermen, geen kunstmatige upscaling als automatische 'upgrade'.
- Anime: **Dual Audio (Engels + Japans) hoogste voorkeur**, daarna Engels, daarna Japans als fallback. Engels is **voorkeur**, niet verplicht.
- Nederlands als app-/interfacetaal én als configureerbare audio- en ondertitelvoorkeur. Nederlands standaard voor UI; Engels optioneel.
- 'Full Auto' herstel, maar nooit onomkeerbare acties bij onzekere identificatie; quarantaine/rollback en stopknop verplicht.
- Scan direct bij events plus dagelijkse incrementalscan; periodieke diepere audit.
- Seerr blijft de aanvraaginterface. Seerr moet alle vier geïntegreerde fork-instanties via afzonderlijke, stabiele API-URL’s en de normale serverselectie kunnen blijven gebruiken.
- **Vier standaardinstanties:** Sonarr Series, Sonarr Anime, Radarr Films, Radarr Animefilms, elk met eigen database/config, poort/route, root folders, profielen en downloadcategorieën, beheerd vanuit één UI.
- **AI Decision Advisor:** optioneel, uitsluitend voor aantoonbaar ambiguë gevallen of ingewikkelde diagnosen; ondersteuning voor Claude API en OpenAI API, eerst schaduwmodus, daarna beperkt geautomatiseerd binnen deterministic policy, met harde budgetten en privacycontrols.
- Bestaande AV1-repository: **https://github.com/lvos04/av1**, bestaande FastAPI/SQLite/Next.js-app (native Ubuntu); integratie als codebasis vanaf fase 0, volgens DEEL P en DEEL Q. De gebruiker heeft gemeld dat Dolby Vision-afspelen in zijn situatie al werkte; neem die melding serieus, maar behandel die niet als universele afspeelgarantie. Het DV-beleid is bedienbaar via de UI, zonder een verplichte universele playback-green-gate.
- Gebruik bestaande TRaSH Guides custom formats als importeerbare basis, maar laat harde identiteit-/taal-/HDR-regels erboven gaan.
- **UI-first verplicht:** alle instelbare operationele functies en alle normale beheerhandelingen via de Nederlandstalige webinterface, zonder YAML/JSON/CLI/SSH/bestandsbewerking na de eenmalige TrueNAS-installatie. Zie DEEL Q.
- **Dolby Vision instelbaar:** DV 8.1/8.4 en ondersteunde DV 7-routes mogen in test-/producermodus worden ingeschakeld via de UI; er is geen automatische blokkade enkel omdat een universele device-matrix ontbreekt. Behoud wel bestand-/metadata-verificatie en terugval naar origineel.

---

# DEEL B — TECHNISCHE ARCHITECTUUR

## B1. Packaging: één app, meerdere geïsoleerde processen

Maak één TrueNAS Custom App / Docker Compose-bundel, één installatiewizard, één login/hoofddashboard, één releaseversie. Intern minimaal:

- `mediaguard-gateway`: API gateway en centrale authenticatie, proxy voor compatibele externe endpoints.
- `mediaguard-web`: nieuwe Nederlandstalige frontend.
- `mediaguard-core`: orchestrator/policy engine, events, planning, casebeheer, audit.
- `mediaguard-analysis`: FFprobe/MediaInfo, hash/mapping, audio- en quality analyses; los schaalbaar.
- `mediaguard-recovery`: zeer beperkt bevoegde bestands-/quarantaine-worker.
- `mediaguard-db`: eigen PostgreSQL voor MediaGuard-domeindata, jobs, historie en audit.
- `mediaguard-redis`: queue, locks/notificaties waar passend (kritieke status óók persistent in PostgreSQL).
- `mediaguard-sonarr-series` en `mediaguard-sonarr-anime`: **dezelfde eigen Sonarr-forkimage**, maar verschillende geïsoleerde applicatie-DB’s, configuraties, poorten/routes en mediaroots.
- `mediaguard-radarr-films` en `mediaguard-radarr-animefilms`: **dezelfde eigen Radarr-forkimage**, maar verschillende geïsoleerde applicatie-DB’s, configuraties, poorten/routes en mediaroots.
- `mediaguard-encode`: vanuit `lvos04/av1` overgenomen encode-/verificatie-engine met gecontroleerde TrueNAS-port, als aparte worker binnen dezelfde bundel. Geen tweede encoder opnieuw uitvinden.
- Optioneel: geïsoleerde taalherkenning-, Shoko- en videoanalyseservices.
- `mediaguard-ai-advisor` (optionele worker): router naar OpenAI of Anthropic Claude API, uitsluitend gestructureerde suggesties; geen mediabestandstoegang of uitvoeringsbevoegdheid. Bij geen API-key draait MediaGuard volledig door.

De forks blijven echte Sonarr/Radarr-services; ze mogen technisch niet worden vervangen door een onvolledige API-imitatie. Seerr, downloadclients en indexers zien compatibele API's. Gebruik geen privileged containers of algemene host-Docker-socket. Scheid read-only media-inspectie van de minimale mutatierechten voor recovery.

**Stackvoorkeur:** C#/.NET voor fork-plugins/hookcode; **hergebruik de reeds bestaande Python 3.12+/FastAPI-backend en Next.js App Router/TypeScript/Tailwind/shadcn-UI uit `lvos04/av1` als basis voor de MediaGuard-frontend en media-engine**, tenzij na een onderbouwde spike een migratie strikt beter blijkt. PostgreSQL voor nieuwe MediaGuard-domeindata, Redis waar nodig voor coördinatie, en een gecontroleerde migratiestrategie vanaf de bestaande AV1 SQLite-WAL-database. FFmpeg/ffprobe, MediaInfo, MKVToolNix en dovi_tool volgens gepinde uitvoerbare toolchain. Controleer de bestaande Sonarr/Radarr-technologiestacks vóórdat architectuurkeuzes definitief worden vastgezet. Hou API-contracten taal- en procesonafhankelijk.

## B2. Forkstrategie — vanaf dag één echte forks

Fork of clone met upstream-remote van:
- https://github.com/Sonarr/Sonarr (GPL-3.0)
- https://github.com/Radarr/Radarr (GPL-3.0)

Maak reproduceerbare build, gepinde upstream commit-SHA, patchmanagement, release manifest en een **upstream merge/rebase testpipeline**. Gebruik lokale forkbranches als de gebruiker nog geen GitHub-rechten of doelrepos heeft ingericht; creëer niet zonder toestemming repositories in zijn account. Leg duidelijk vast hoe branches later naar eigen GitHub-forks gepusht worden.

**Minimaal vereiste gerichte fork-extensies (van toepassing op beide runtime-instanties van elk product):**
1. **Pre-grab policy hook**: stuur releasekandidaten voor verifieerbare extra checks aan MediaGuard (met bounded timeouts en fail policy). Liefst uitbreiding via een kleine interface in de bestaande rankingpipeline in plaats van brede rewrites.
2. **Pre-import staging/verification hook**: onderschep potentieel verkeerd gemapte files voordat ze definitief als correcte aflevering in de bibliotheek belanden; default fail-safe bij identiteitstwijfel.
3. **Original-release provenance**: exporteer en behoud originele kwaliteitsclassificatie, releasegroep, custom-format-score, episode-/movie-ID en downloadgeschiedenis, ook wanneer lokale file later in AV1 wordt veranderd.
4. **Extended rejection diagnostics**: geef machineleesbare redenen waarom releases zijn afgewezen of waarom media ontbreekt.
5. **Native status/events** voor MediaGuard: zoekresultaat, grab, download, import, upgrade, mislukt, aflevering toegevoegd, monitorstatus veranderd, nieuw seizoen, verwijderen.
6. **Central task reservation**: samenwerkende locks, idempotency en conflictpreventie, zodat MediaGuard en fork niet twee tegenstrijdige download- of vervangingsacties starten.
7. **Compatibele REST API** en waar nodig compatibility shims voor Seerr en bestaande downloadclients.
8. **Upstream sync**: CI test nieuwe commits en geeft conflict- en gedragsrapport, maar merge/deploy niet blind zonder compatibiliteitsgate.

Ga uit van zo klein mogelijke patches. Elke forkpatch moet vergezeld gaan van motivatie, test en impact op upstream onderhoud. Vermijd het herschrijven van indexerprotocollen, downloadclientondersteuning en bestaande importmechanismen waar niet nodig.

**Versieregel:** verifieer de op dat moment stabiele Sonarr/Radarr-versies. Seerr documenteert momenteel ondersteuning voor Sonarr/Radarr v3/v4; Sonarr v5 heeft een eigen API. Kies eerst de compatibele stabiele basis, of implementeer en bewijs een equivalente compatibiliteitslaag voordat v5 wordt ingezet. Nooit aannemen dat alleen een draaiende poort API-compatibiliteit bewijst.

## B3. Single source of truth en eigenaarschap

- **Fork Sonarr**: seriecatalogus, episode-metadata, monitorstatus, RSS, indexers, downloadclients, import-/history-operaties.
- **Fork Radarr**: filmcatalogus, filmmonitorstatus, RSS/indexers, download/import/history.
- **MediaGuard policy engine**: extra harde regels, audio-/kwaliteits-/identiteitsbeleid, cross-service beslissingen en evidence.
- **MediaGuard orchestrator**: zware scan-, analyse-, conversie- en recovery-workflows, rate limits, locking.
- **AV1-engine**: niet zelfstandig files overschrijven; produceert geverifieerde staged outputs.
- **Seerr**: bestaande externe aanvrageninterface; aanvragen doorgeven aan forks zoals voorheen.

Gebruik officiële API's en kleine upstreamhooks; wijzig de database van Sonarr/Radarr niet van buitenaf tijdens normaal bedrijf.

---

# DEEL C — BESTAANDE BIBLIOTHEEK: IMPORT, MIGRATIE EN VOLLEDIGE OPTIMALISATIE

**Dit is een P0-onderdeel en mag niet worden uitgesteld tot na de eerste release.**

## C1. Import van huidige configuratie

De migratiewizard moet minimaal ondersteunen:

- Verbinding met huidige Sonarr/Radarr via URL/API key; meerdere instanties.
- Import of herstel via officiële Sonarr/Radarr-backups indien passend bij versie/paths, met scheiding tussen source en target.
- Overnemen van alle series, films, alternatieve titels, DB-identifiers (TVDb/TMDb/IMDb), seasons, episode orders, metadata en mapping.
- Per serie: **monitored status, monitored seasons, toekomstige seizoenen, episode-level uitzonderingen**, tags, lijsten, language/quality profiles, custom formats, releasegroepen, cutoff, importpad, bestandsnaamregels.
- Radarr: gemonitorde films, minimum availability, editietypen, gewenste kwaliteitsprofielen, root folders, releasehistorie.
- Indexers/Prowlarr-integratie, tags, categorieën, prioriteiten, API-endpoints, downloadclients, categorieën, remote path mappings en root folders.
- Bestaande Seerr-instellingen en root/profile/instance mappings valideren; geen Seerr-databasewijzigingen zonder noodzaak.
- Bestaande downloadqueue, incomplete downloads, hardlinks, seedende torrents en folderrechten inventariseren.
- Bestaande bibliotheekpaden exact behouden waar mogelijk; geen massale verhuizing of hernoeming tijdens eerste import.
- Aparte bewijssporen voor al geconverteerde AV1-bestanden met onbekende bronstatus.

De wizard werkt in deze volgorde:
1. **Preflight**: versies, netwerk, API's, vrije ruimte, rechten, pool/datasets, Seerr, downloader, indexers en eventuele actieve downloads detecteren.
2. **Backup**: ingebouwde app-backups/export, versie-info, configs en een herstelscenario; backupchecksum; test van restore op geïsoleerde kopie.
3. **Shadow import**: nieuwe forks isoleren van actieve grabschrijfacties; import op kopieën / via API; zonder bestaande media aan te raken.
4. **Mapping preview**: serie/film-ID, path mappings, profielen, monitoringvlaggen, count vergelijking en missing-status verschillen tonen.
5. **Read-only scan**: alle videobestanden inventariseren; snelle mediadata eerst, hashes alleen wanneer nodig. Resumable met checkpoints en I/O-limieten.
6. **Reconciliation**: verwacht ↔ gevonden ↔ onbekend ↔ dubbel ↔ verkeerd gemapt ↔ ontbrekend.
7. **Optimalisatievoorstel**: per item behouden / verifiëren / opnieuw downloaden / taal-upgrade / kwaliteit-upgrade / AV1 converteren / repareren.
8. **Cutover voorbereiding**: nieuwe forks moeten indexers, downloader, Seerr, dagelijkse monitoring en toekomstige afleveringen bewijzen.
9. **Gecontroleerde cutover**: bevries schrijfacties op oude Sonarr/Radarr, finaliseer synchronisatie, update Seerr-servers indien nodig, activeer alleen de nieuwe forks als downloadverantwoordelijke. Oud systeem behouden als offline rollback.
10. **Post-cutover**: audit 24–72 uurs activiteit over testvenster of gesimuleerde tijdsprongen, monitor nieuwe releases, ontbrekende media, importsucces, API-requests; finaliseer administratief.

**No-go:** als aantallen, monitorstatussen, Seerr-aanvragen of oude config niet overeenkomen, mag cutover niet succesvol worden gemarkeerd.

### C2. Read-only bibliotheekscanner

- Ondersteun TV, anime, films, verschillende root folders, specials, multi-episode, multi-editions, anime absolute nummering, onregelmatige bestandsnamen.
- Geen bestand wijzigen in inventarisatiemodus.
- Detecteer file-ID, pad, grootte, mtime, container, bitrate, bitrateverdeling indien mogelijk, resolutie, codec, HDR, kleurprimaries, audio/subs en bestaande tags.
- Snelle fingerprint op basis van metadata + gekozen segmenthashes; volledige checksum alleen voor verificatie/deduplicatie indien nodig.
- Signaleer hardlinks, sparse files, snapshots, dubbele paths en bestanden die op verschillende releases lijken.
- Vergelijk bestandsidentiteit met Sonarr/Radarr-importhistorie en onafhankelijke animebronnen; rapportage met bewijsniveau.
- Onderscheid `present`, `missing`, `unmonitored`, `cutoff_unmet`, `identity_uncertain`, `wrong_episode`, `audio_fallback`, `upgrade_available`, `encoder_candidate`, `protected`.
- Corrigeer nooit automatisch alleen vanwege filename- of duurafwijkingen.

### C3. Library Optimization Engine

Bouw een duurzame jobplanner die **de hele bestaande bibliotheek** in batches behandelt en herstartbaar is.

Pijplijn: `DISCOVER -> INVENTORY -> IDENTITY -> AUDIO -> QUALITY -> POLICY -> PLAN -> SEARCH/ENCODE/REPAIR -> STAGE -> VERIFY -> COMMIT -> AUDIT`.

Optimalisatie-prioriteit:
1. Bevestigd verkeerde of beschadigde afleveringen/films.
2. Verkeerd gekoppelde, dubbel geïmporteerde of ontbrekende content.
3. Media met audiotaal die niet voldoet aan voorkeur (anime dual/Engels; configureerbaar NL).
4. Ondermaatse kwaliteit of haalbare 4K-upgrades.
5. AV1-conversies met aantoonbare opslagwinst zonder ontoelaatbaar kwaliteitsverlies.
6. Verder monitoren en terugkerende nieuwe kandidaten.

Bied instelbare max actieve downloads, max GB per dag, indexerquota, CPU/GPU/I/O-limieten, maximale werkruimte, seedingrestricties, tijdvensters en een plan voor minimaal benodigde stagingruimte. Laat bestaande goede media bruikbaar tijdens optimalisatie.

**Optimalisatie-dashboard**: bibliotheek-dekking; 'al goed'; 'ontbrekend'; 'onjuist'; 'taalverbetering'; 'kwaliteitsverbetering'; 'AV1-kandidaat'; geschatte besparingen; in behandeling; mislukte taken en concrete reden. Export als CSV/JSON. Dry-run eerst; geautomatiseerd starten pas nadat de ingestelde veiligheidsgates werken.

---

# DEEL D — NIEUWE AFLEVERINGEN BLIJVEN VOLGEN EN ONTBREKENDE MEDIA VINDEN

**P0: na uitschakelen van de oude Sonarr/Radarr moet MediaGuard zelfstandig alle bestaande follow-up taken overnemen.**

## D1. Doorlopende serie- en filmmonitoring

- Importeer voor elke bestaande serie en film de huidige monitorstatus exact; verander niet ongemerkt bewust unmonitored titels.
- Presenteer een expliciete optie **'Nieuwe seizoenen automatisch volgen'**: standaard aan voor actief gevolgde series zodra gebruiker dit in de migratiewizard kiest; bestaande season/episode-uitzonderingen respecteren.
- Bekijk metadata-updates (nieuwe seizoenen/afleveringen, gewijzigde airdate, specials, anime cour/absolute mapping) en werk de wanted-kalender bij.
- Fork Sonarr behoudt RSS-monitoring voor verse releases, downloadclient-integratie, import en history, net als bestaande Sonarr.
- Fork Radarr behoudt watched movies, beschikbaarheidsvensters, releasesearch en upgrades.
- Koppel Seerr-aanvragen direct aan juiste fork-instantie, quality profile en root folder; requests mogen niet verloren raken.
- Detecteer verlopen of gewijzigde API-keys en verbroken integraties; dashboard/alerts.

## D2. Waarom ontbreken zoveel afleveringen? Root Cause Engine

Bestaande Sonarr kijkt doorgaans naar nieuwe indexeritems via RSS en zoekt oudere, al gemiste afleveringen niet vanzelf voortdurend via een brede historische zoekactie. Bouw daarom **Backfill & Missing Recovery** naast de originele RSS-monitoring.

Voor ieder verwacht maar niet aanwezig bestand bepaal de oorzaak. Expliciete categorieën:
- Nieuw seizoen/episode niet gemonitord.
- Aflevering nog niet uitgezonden / metadata onjuist / onbetrouwbare datum.
- Er is nog geen passende release in indexers gevonden.
- Indexercategorie verkeerd, indexer offline, quota/auth/rate limit.
- Kandidaten afgewezen door quality/language/custom format/minimum size/allowed source.
- Downloadclient ontbreekt, wachtrij vastgelopen, netwerk/permissions/diskfout.
- Download voltooid maar import afgewezen (path mapping, seizoenfout, corrupte file).
- Verkeerde anime scene-/absolute numbering.
- Reeds aanwezig bestand niet gekoppeld / ontbrekende root folder.
- Seriesfinale, specials, dubbels en andere bewuste uitzonderingen.

Toon per item de *concrete reden* inclusief betrokken rule, tijdstip en voorgestelde fix, geen generieke 'niet gevonden'-melding.

## D3. Historical Backfill Scheduler

- **Direct na migratie**: complete vergelijking `wanted vs on-disk`, prioritering op recente afleveringen, lang ontbrekende afleveringen, seizoencompleetheid en gebruikersfavorieten.
- **Dagelijks**: incrementalscan, zoek-herplanning voor al ontbrekende bestanden en quality cutoff unmet.
- **Elke 6–24 uur** (configureerbaar): beperkte queue voor historische missers, met indexerquota/rate limits en exponential backoff.
- **RSS/events** blijven direct responsief voor nieuwe afleveringen; backfill mag RSS niet blokkeren.
- Zoek in eerste instantie met bestaande fork-indexers/Prowlarr; gebruik correcte searchtypen, scene numbering en anime-mappings.
- Batch/dedup indexervragen; zoek niet eindeloos hetzelfde zonder nieuwe aanleiding. Bewaar kandidaatresultaten, afwijzingsredenen en 'last searched'.
- Als geen geldige release bestaat: markeer `unavailable`, plan later nieuwe poging, leg uit dat niet alles te vinden is; geen kwaliteits-/taalregels heimelijk opofferen.
- Bij langdurig missen: optioneel instelbare fallback-policy (bij anime Japanse audio toegestaan) en promotie naar betere release zodra die verschijnt.
- Ondersteun handmatige `Search now`, `Retry with relaxed rules`, `Ignore`, `Keep monitoring` per episode/seizoen, met uitleg van gevolgen.
- Automatiseer nooit ongeautoriseerde indexer-toegang of omzeil geen limieten.

### D4. Seizoencompleetheid en Wanted-dashboard

Toon per serie: verwachte afleveringen, op disk, ontbrekend, binnenkort, unmonitored, onder cutoff, verkeerde aflevering, fout geimporteerd, in download en blocked. Maak een seizoenmatrix/heatmap. Verduidelijk dat een toekomstige aflevering niet als defecte ontbrekende download telt.

**KPI's**: missing monitored aired episodes, 7/30/90 dagen, redenverdeling, backfill success rate, import failure rate, future season monitoring coverage, Seerr request → available doorlooptijd. Toon aantoonbare daling van ontbrekende media zonder alle indexers te overvragen.

---

# DEEL E — EPISODE-IDENTITEIT, ANIME EN TALEN

## E1. Episode Identity Engine

Gebruik hiërarchisch bewijs:
1. Sonarr database-/episode-ID, release- en importhistorie, download-ID.
2. Titelaliases, TVDb/TMDb en originele serie-ID's.
3. Voor anime AniDB/Shoko bestandsidentificatie (wanneer beschikbaar), TheXEM mappings en absolute nummering.
4. Bestandsnamen, scene season/episode, releasegroep, bestandstijdlijn, technische metadata.
5. Optionele perceptuele fingerprints alleen indien betrouwbare referentie bestaat.

Status `verified / probable / uncertain / mismatch / unresolved`, met evidence-objecten en duidelijke reden. Geen harde claim dat onbekend videomateriaal door AI met zekerheid kan worden herkend. `mismatch` mag pas automatische vervanging triggeren indien bewijs sterk genoeg en het herstelpad veilig is. Aanbevolen: hashmatch van een bekende aflevering krijgt groter gewicht dan filename; onbekende hash is geen mismatch.

Ondersteun anime OVA/ONA/specials, absolute/seizoen/cour, dubbele afleveringen, director's cuts/edities en afwijkende dvd/airing orders. Bouw regressie-fixtures voor de mismatches die de gebruiker beschrijft.

## E2. Audiotaal en ondertiteling

Configureer **UI-taal** (`nl-NL`, `en-US`) separaat van media-audio en subtitles (`nl`, `en`, `ja`, extensible ISO 639 normalisatie).

**Anime default:** Dual Audio EN+JA > English-only > Japanese-only fallback. Subtitlevoorkeur NL > EN (optie); forced / signs & songs apart beoordelen.

**Gewone series/films default:** oorspronkelijke audio behouden, Nederlandse ondertitels gewenst; NL gesproken audio configureerbaar per profiel/titel. Kinderprofiel kan NL audio verplicht stellen.

Inspecteer alle audio- en ondertitelstreams via FFprobe/MediaInfo, tracknamen, default/forced flags, kanaalindeling en codecs. Detecteer commentaartrack en verkeerde labels. Optionele taalherkenning van echte spraak bij onbetrouwbare metadata: multiple sample windows, score/confidence, geen automatische hard fail bij ruis/muziek/geen spraak.

Importeer TRaSH scores, maar implementeer **harde constraints + lexicografische voorkeuren**: identiteit en bestandssafety altijd boven kwaliteitspunten; daarna de expliciete anime-audiovoorkeur; daarna bron, resolutie en groepen. Mogelijkheid om per profiel resolution-first tegenover audio-first te kiezen; anime default audio-first.

---

# DEEL F — QUALITY INTELLIGENCE EN AV1 ENCODING

## F1. Basisanalyse

Per bron minimaal: codec, profiel, level, pixfmt/bitdiepte, resolutie, frame rate, bronklasse, bitrate, file size, dynamisch bereik, kleuren, HDR10, HDR10+, Dolby Vision profiel/metadata, video/audio/subtracks, hoofdstukken, duration, GOP/field flags, corrupte packets, decode errors. Met behoud van originele releaseprovenance.

4K alleen wanneer technisch en inhoudelijk zinvol; prioriteer native/detailrijke bronnen. Bitrate op zichzelf is geen kwaliteitsrang. Bepaal of bron al te sterk gecomprimeerd is en of hercoderen zinvol is.

## F2. Per-title / content-aware encoding

Maak een adaptieve kwaliteitsengine met:
1. Shot/scene detectie, representatieve samples (lichte, donkere, drukke, rustige, anime line art, grain, credits).
2. Technische analyse van beweging, textuur, grain, ruis, banding, bestaande artefacten en dynamisch bereik.
3. Kleine proefencodes met beschikbare encoders en verschillende CQ/CRF/ICQ/QP of bitrate-instellingen (afhankelijk van encoder).
4. VMAF/SSIM waar als indicatie bruikbaar; bij HDR en anime aanvullende checks (HDR-meetroute, edge/banding/grain-inspectie, niet blind SDR-VMAF gebruiken).
5. Kies rate-control en targetkwaliteit die een vooraf ingestelde kwaliteitsvloer haalt met zinvolle opslagbesparing en acceptabele encodetijd.
6. Voer volledige encode naar **staging** uit, verifieer duur, streams, sync, captions, hoofdstukken en beeldkwaliteitscriteria.
7. Vergelijk resultaatgrootte en kwaliteit met bron. Als voordeel te klein of kwaliteit onzeker: **origineel behouden**.

Profielen: `Archiefkwaliteit`, `Gebalanceerd` (default), `Compact`, `Custom`, plus per categorie: anime/film/serie/4K HDR. Laat per profiel max bitrate, minimale besparing, max encodetijd, kwaliteitsvloer en verplichte streams instellen. Bewaar beslissingen in `encoding_trials`, `encoding_jobs`, `source_features`, `quality_measurements` en `quality_models` met regel-/modelversie. Een toekomstige voorspelmodule mag uitsluitend instellingen voorstellen die nadien worden gevalideerd; niet zichzelf onveilig bijstellen.

## F3. AV1-engine en hardware

Detecteer op TrueNAS `Intel i7-14700`, UHD 770 en eventuele discrete Intel Arc. UHD 770 is geen AV1 hardware encoder; Intel Arc A310 biedt AV1 hardware encode/decode, maar moet in de daadwerkelijke host/dri/VAAPI-stack gevalideerd worden. CPU AV1 (bv. SVT-AV1) als fallback, optioneel. Vermijd niet-onderbouwde aannames over encodesnelheid.

Opbouw:
- `encoder_capabilities` voor GPU-ID, VA-API/oneVPL/Quick Sync drivers, software, devices `/dev/dri`, 8/10-bit, pixel formats, HDR support.
- `encoder_profiles`, `trial_encodes`, `encoding_scheduler`, `quality_validator`.
- CPU/GPU-resourcecontrol en lagere prioriteit dan de mediastreamingserver.
- Geen AV1-conversie van reeds goed geoptimaliseerde AV1-bestanden tenzij expliciet nuttig aangetoond.
- Geen onnodig re-encoden van audio; behoud originele taaltracks bij voorkeur door remux, tenzij de gebruiker een expliciet audioprofiel kiest.
- Presenteer verwachte ruimtewinst en evidence, maar claim geen exacte kwaliteitsscore zonder eigen meting.

## F4. Dolby Vision, HDR10+, HDR10

Dolby Vision-behoud is een **harde safety gate** en nooit alleen een vlaggetje. Beoordeel bronprofiel (5/7/8/etc.), RPU, EL/FEL, base layer, outputcontainer, encoder-mogelijkheden, playbackcompatibiliteit en metadata-authenticiteit. AV1 DV-output niet zonder bewezen end-to-end metadata- en decodeertests als 'behouden' labelen. Profile 7 FEL mag niet stilzwijgend in een single-layer-output veranderen. Als passende, gevalideerde route ontbreekt: preserve original (of expliciet afzonderlijke compatibele kopie met waarschuwing na keuze).

Test apart SDR, HDR10 static metadata, HDR10+, DV Profile 5, 7 MEL/FEL, 8, container-mux en playback-device profiel. Maak voor HDR/DV optionele rules: `original_only`, `safe_copy_when_proven`, `convert_if_verified`.

## F5. Directe Git-integratie van bestaande Ubuntu AV1-app

**Bron:** `https://github.com/lvos04/av1` (`master`). Vanaf fase 0 klonen en commit SHA, licentie en aanwezige tests vastleggen. De repository bevat **echte, bestaande** Python/FastAPI-backend, Next.js-frontend, `asyncio`-planner, SQLite-WAL, AV1 `libsvtav1`/`av1_qsv`, probe-, beslis-, commando-, vervang- en herstelcode. Behoud de media-engine als eerste bron van waarheid en port gecontroleerd naar TrueNAS. Niet een dummy-encoder naast een productie-engine bouwen.

Documenteer de bestaande werking in `docs/AV1_BASELINE_AUDIT.md`, de technische integratie in `docs/AV1_REPO_INTEGRATION.md` en de bewust veranderde veiligheidsregels in `docs/AV1_SAFETY_DIFF.md`. Alle details in **DEEL P** zijn verplicht en gaan bij tegenstrijdigheid met oudere generieke suggesties voor.

# DEEL G — DOWNLOAD-, IMPORT-, HERSTEL- EN RISICOBELEID

## G1. Verantwoordelijkheid per fase

`MONITORED -> RELEASE_DISCOVERED -> POLICY_EVALUATED -> RESERVED -> GRABBED -> DOWNLOADED -> STAGED -> VERIFIED -> IMPORTED -> OPTIONAL_ENCODED -> VALIDATED -> ACTIVE -> AUDITED`.

Terugvalstatussen `UNCERTAIN`, `NO_VALID_RELEASE`, `IMPORT_FAILED`, `TRANSCODE_FAILED`, `ROLLBACK_REQUIRED`, `QUARANTINED`, `RETRY_SCHEDULED`.

De fork en MediaGuard delen een correlation-ID en idempotency key. Alle jobs hebben persistente status; event replay veroorzaakt geen dubbele grabschrijfacties. Pre-import checks geven expliciete timeout/fail policy, niet onbeperkt wachten.

## G2. Bestandsvervanging

- Media read-only voor scanners, write uitsluitend via recovery-worker met beperkte datasetrechten.
- Nooit transcoden in de originele inode of een bestaande hardlink (bescherm seedende downloads).
- Schrijf output naar staging, sync/checksum, controleer alle streams en identiteit, commit pas daarna.
- Zorg voor rollback-copie, apart waar nodig, of ZFS-snapshot via beperkte TrueNAS-API; snapshots alleen zijn niet genoeg als Sonarr/Radarr-databases inconsistent raken.
- Bij dataset-/filesystemgrens: geen misleidende 'atomische rename'; gebruik two-phase copy + verified activate met herstelscenario.
- Bewaar provenance, herstelbestanden en pre-/postoperatie audits; verwijder de laatste herstelbare versie nooit in dezelfde transactie.
- Detecteer locks, in-use mediabestanden, vrije ruimte, ingestelde reserve, permission errors en externe wijzigingen.
- Emergency stop schakelt nieuwe grabschrijfacties, recovery-mutaties en encodes uit zonder database te beschadigen.
- Full Auto accepteert automatisch **bewezen veilige** acties; bij onzekere episode-identiteit of niet-bewezen HDR behoud volgt review en de huidige file blijft actief.

## G3. Download- en zoekregels

Respecteer indexerquota, max gelijktijdige taken, seeding- en retentieregels, opslagbudget, rate limit, retry backoff, blacklist van slechte releases, geen zoekloops, geen duplicaten en gebruiker-gedefinieerde uitzonderingen. Downloadbeleid moet gebruik kunnen maken van bestaande Sonarr/Radarr-clientintegraties en eventuele Prowlarr-configuratie. Een historische release zonder geldig alternatief kan niet worden 'gegarandeerd'; geef zichtbare reden en blijvende monitoring.

---

# DEEL H — SEERR EN MEDIA SERVER COMPATIBILITEIT

**Seerr blijft geïnstalleerd en actief.** Niet forken tenzij een later aantoonbaar onvermijdelijk probleem bestaat. Ondersteun serverselectie en afzonderlijke API-URL’s voor Sonarr Series, Sonarr Anime, Radarr Films en Radarr Animefilms.

Verplichte interface-/contracttests tegen relevante echte Seerr-versie:
- Nieuwe film aanvragen en toevoegen aan Radarr-fork, correcte profile/root/quality settings.
- Nieuwe serie en afzonderlijke seizoenen aanvragen via Sonarr-fork; juiste monitored-state en future-season beleid.
- Bestaande titel herkend, geen dubbele aanvraag/download.
- 4K-/niet-4K-instances indien geconfigureerd, default server mappings.
- Status van request naar beschikbaar, ook na AV1-conversie/upgrade.
- Authenticatie via bestaande API-key, base URL, HTTPS/reverse-proxy, correcte response schema en foutafhandeling.
- Directe links vanuit Seerr naar forks, of gebruiksvriendelijke proxy-route.

Pas Seerr-configuratie **pas op cutover** aan indien endpoints veranderen. Laat Plex/Jellyfin/Emby de media blijven zien via ongewijzigde of bewust gemigreerde paden en directory ACL's. Optioneel playback/device compatibility reporting, maar vervang Seerr of mediaserver niet.

---

# DEEL I — UI/UX EN DESIGN SYSTEM (VERPLICHT)

## I1. Productvisie

Maak geen simpele adminpagina. Ontwerp een professioneel, rustig, Nederlandstalig **Media Operations Control Center**. Donker als standaard, licht thema optioneel. Een combinatie van mediabibliotheek en betrouwbaar infrastructuurdashbord, zonder visuele kopie van Sonarr of Radarr.

**Visuele kenmerken:** heldere hiërarchie, compacte maar leesbare tabellen, films/series met posters als context, statuskleur alleen waar betekenisvol, precieze technische informatie binnen detailpanelen. Geen overvloed aan gradients, badges of animaties. Progressive disclosure: eerst het belangrijkste, daarna evidence en geavanceerde opties.

### Voorlopige ontwerptokens

- Achtergrond donker `#0C111B`; oppervlak `#141C29`; verheven oppervlak `#1C2735`.
- Primaire accentkleur `#4F9CF9`; succes `#35B587`; waarschuwing `#E8A44D`; gevaar `#E36B71`.
- Tekst primair `#F0F4FA`, secundair `#A4B0C1`, subtiele border `#2C3746`.
- Lettertype: Inter of een vrij verkrijgbaar alternatief (geen onduidelijke externe fontlicenties).
- Basisafstand 4/8/12/16/24/32 px; border-radius 8–14 px; voldoende contrast; toetsgebruik en toegankelijkheid volgens WCAG 2.2 AA waar haalbaar.
- Responsive 360px tot desktop; tabellen op mobiel zinvol omzetten naar cards/stacked details.
- Ondersteun Nederlandse locale voor tijden, data, aantallen, bestandsgrootte.
- Verplichte states: loading, empty, error, partial offline, retry, stale metadata, uncertain evidence, success en pending.

## I2. Navigatie

Links desktop-sidebar / mobiel bottom nav + drawer. Hoofdschermen:

1. **Overzicht**: bibliotheekdekking, missing, in behandeling, beveiligingsstatus, downloads, encoderqueue, opslagbesparing en aankomende afleveringen.
2. **Bibliotheek**: alle series/anime/films, posters, filters, statusbadges, bulkacties, evidence; behoud sortering/filters.
3. **Ontbrekend & herstel**: grootste prioriteit; seizoencompleetheid, search reasons, recheck, zoekqueue, smart fixes, gestopte downloads, coverage charts.
4. **Kalender**: nieuwe afleveringen/seizoenen, monitored flags, verwachte beschikbaarheid, anime-dubs waar betrouwbare data beschikbaar zijn.
5. **Release Lab**: huidige versus alternatieve kandidaat; harde eisen, taal, bron, 4K, TRaSH score, bronherkomst, upgradebesluit en preview van effect.
6. **AV1 Studio**: bewerkingen, encodertests, source-analysis, geschatte grootte/winst, kwaliteitsvergelijkingen, realtime encodejobs, DV/HDR-profielbeheer met UI-schakelaars, metadata-verificatie en herstelpunten.
7. **Migratie & optimalisatie**: 10-stappenwizard, bronvergelijking, map/aantallen, dry run, cutover, herstelplan, globale bibliotheekoptimalisatie.
8. **Anime Mapping**: speciale absolute/seizoen-ordermappings, hashbewijs, foutieve episodes en handmatige correcties.
9. **Jobs & logs**: queued/running/retry/failed/succeeded, per job evidence, eventtimeline, resourcegrafieken, logs met filters.
10. **Quarantaine**: herstelbare items, transacties, snapshots/backups, retentie, restore.
11. **Profielen & regels**: anime dual-audio-first, films 4K, Nederlands audio/subs, HDR/DV, storage en experiment/dry-run simulator.
12. **Integraties & systeem**: Sonarr/Radarr forkgezondheid, Seerr, downloadclients, indexers, GPU, TrueNAS-opslag, API keys, backups, updates, noodstop.

### I3. Dashboardindeling desktop

Top: globale zoekbalk, actieve processen, storage, notificaties, profiel.

Eerste rij vier KPI's: `Beschikbaar`, `Ontbrekend`, `Fouten`, `AV1-besparing`.

Daaronder twee kolommen: links **'Actie nodig'** met oorzaak en primaire herstelactie; rechts **'Download & encode'** actieve jobs en kosten in opslag/I/O. Daaronder **'Nieuwe afleveringen deze week'**, **'Seizoenscompleetheid'**, **'Verbeterkansen'** en systeembelasting.

Interactie: elke KPI drill-down filtert direct naar onderliggende media; geen doodlopende cards.

### I4. Detailpagina per aflevering

Header: poster, serie, seizoen/aflevering/absolute nr., titel, lucht-/uitzenddatum, release, monitored, beschikbaarheid.

Tabs: `Overzicht`, `Identiteit`, `Video`, `Audio/ondertitels`, `Releases`, `Conversies`, `Historie`.

Identity evidence met bron, matching-ID, confidence, mapping trace; huidige versus gewenste audio; relevante releasekandidaten; `Waarom deze release?` in leesbare taal, plus ruwe technische bewijsdetails in uitklapblok. Contextuele acties `Opnieuw controleren`, `Zoek alternatief`, `Optimaliseer`, `Herstel mapping`, `In quarantaine` met risicowaarschuwing waar nodig.

### I5. Migratiewizard

Geen technisch configuratieformulier van 100 velden. Stappen met preflight, verbindingen testen, source backup, media paths, preview, monitorstatus/future seasons, quality profiles, dry-run optimization, Seerr test en cutover. Voortgang en waarschuwingen blijven zichtbaar na refresh.

### I6. Design deliverables

- `docs/design/DESIGN_SYSTEM.md`, `UX_FLOWS.md`, `COMPONENT_INVENTORY.md`, `SCREEN_SPECIFICATIONS.md`, schermschetsen/HTML previews en minstens 6 uitgewerkte representatieve UI-schermen.
- Herbruikbare componenten: status chip, quality ribbon, media row, episode grid, evidence card, difference viewer, migration stepper, progress job card, policy editor, dry-run diff.
- E2E-tests voor desktop én mobiel, toetsenbordnavigatie, contrast, foutmeldingen en Nederlandstalige strings.
- Alle user-facing strings via i18n keys; Nederlandse basisvertaling volledig, Engels beschikbaar.

---

# DEEL J — INTERNE API, DATA EN OBSERVABILITY

## J1. API-contracten

Maak versieerbare OpenAPI-definities. Verwachte MediaGuard-v1-routes:

```text
GET  /api/v1/status
GET  /api/v1/library
GET  /api/v1/library/{id}
POST /api/v1/library/{id}/verify
GET  /api/v1/library/{id}/evidence
GET  /api/v1/wanted
GET  /api/v1/wanted/{id}/diagnosis
POST /api/v1/wanted/{id}/search
POST /api/v1/wanted/backfill-plan
GET  /api/v1/calendar
GET  /api/v1/release-candidates/{id}
POST /api/v1/policy/simulate
GET  /api/v1/profiles
PUT  /api/v1/profiles/{id}
POST /api/v1/migration/preflight
POST /api/v1/migration/import-preview
POST /api/v1/migration/dry-run
POST /api/v1/migration/cutover
POST /api/v1/migration/rollback
GET  /api/v1/optimization/plan
POST /api/v1/optimization/run
GET  /api/v1/encoding/jobs
POST /api/v1/encoding/jobs
POST /api/v1/encoding/jobs/{id}/cancel
GET  /api/v1/quarantine
POST /api/v1/quarantine/{id}/restore
GET  /api/v1/jobs
GET  /api/v1/audit
POST /api/v1/system/emergency-stop
GET  /api/v1/integrations
POST /api/v1/integrations/{id}/test
```

Gebruik server-sent events of WebSocket voor live status; veilig reconnecten. API-authenticatie, RBAC voor admin/operator/viewer, CSRF waar passend, rate limits, schema-validatie, idempotency keys voor mutaties, correlation IDs en auditlog.

## J2. Core datamodel

Entiteiten minimaal:

`upstream_instances`, `instance_profiles`, `instance_ownership`, `integration_credentials`, `series`, `seasons`, `episodes`, `movies`, `external_ids`, `monitoring_policies`, `root_mappings`, `files`, `streams`, `release_provenance`, `identity_evidence`, `verification_results`, `quality_profiles`, `policy_versions`, `release_candidates`, `rejection_reasons`, `wanted_items`, `missing_diagnostics`, `backfill_attempts`, `download_jobs`, `import_events`, `encoding_jobs`, `encoder_capabilities`, `encoding_trials`, `quality_metrics`, `optimization_plans`, `migration_batches`, `migration_checkpoints`, `replacement_transactions`, `locks`, `quarantine_entries`, `snapshot_refs`, `audit_events`, `notifications`, `outbox_events`, `user_preferences`.

Per media-ID: externe fork-ID + externe provider-ID + pad/inode context waar mogelijk; data nooit op bestandsnaam alleen koppelen. Source provenance en actuele filequality apart registreren. Bewaar alle fork-source native data in hun eigen config/DB; central DB is geen ongevraagde vervanging van bestaande tabellen.

Alle kritieke state transitions transactioneel met een persistente outbox of equivalent. Alembic/EF migraties met schema-versie; back-ups en restore-tests. Sensitive keys via secrets/config met strikte ACL, niet plaintext logs.

## J3. Monitoring

Structured logs, metrics (Prometheus/OpenTelemetry), health/ready endpoints, job traces, CPU/GPU/IO/net/storage, scrape-indexer errors, repeated rejected releases, queue latency, quality regressions en Seerr sync health. In-app alerts + ntfy/Discord/e-mail optioneel. Redactie van API keys, tokens, release URL's met tokens en privépadinformatie in externe logs.

---

# DEEL K — TRUENAS INSTALLATIE, BEVEILIGING, BACKUP EN OPERATIONELE ZAKEN

## K1. Installatie

- Target: TrueNAS SCALE met Docker-gebaseerde apps en `Install via YAML` / Custom App; verifieer actuele stabiele TrueNAS-versie en exacte Compose-beperkingen.
- Lever `deploy/truenas/compose.yaml`, `env.example` zonder echte secrets, dataset/ACL-plan, startup healthchecks, imageversies, resources, port-/path-/GPU mapping en restore-instructies.
- Eén Compose app bevat alle normale diensten; intern opgesplitst voor security en upgradebaarheid.
- Voor GPU detectie: `/dev/dri` minimaal noodzakelijk en alleen indien aanwezig; geen `privileged: true`.
- Voor de eerste import alleen **read-only media mounts**. Write-mounts pas in cutover/automatische herstelmodus en alleen op minimale paden.
- Datasetstructuur (configureerbaar): `<pool>/media/...`, `<pool>/apps/mediaguard/{db,config,logs}`, `<pool>/mediaguard-work/{staging,quarantine,recovery}`.
- Maak een read-only `preflight` / permissiechecker. Geen massale `chown -R` over de mediabibliotheek zonder toestemming.
- Ondersteun meerdere root folders en pools; detecteer cross-dataset transacties, hardlinks en snapshots.
- Setup wizard met Sonarr, Radarr, Seerr, indexers/downloadclients, mediapaden, database en GPU-benchmark, hardware limitations, eerste dry run.

## K2. Security & privacy

Alle kernfunctionaliteit on-prem; externe metadata-/indexerverbindingen expliciet en zichtbaar. Beperk netwerkroutes, encrypt secrets at rest waar praktisch, bescherm login met lokale auth/RBAC en optionele reverse proxy, geen gegevens verzamelen zonder toestemming. TLS achter vertrouwde reverse proxy; geen secrets in frontend-storage of gewone logs. Supplied files/namen/webresponses behandelen als onbetrouwbare input. Sanitiseer path traversal, shell-injection en command args. Gebruik subprocess argument-arrays, nooit mediabestandsnamen via shell-interpolatie.

## K3. Backups, restore en rollback

- Voor iedere migratie: native fork-backup + central DB-backup + configmanifest, checksums en restore-test.
- Voor elke massamutatie: plan, preview, beschikbare ruimte, snapshots/veilige kopie en transactionele state.
- Retentie apart voor quarantaine, rollback en snapshots. Niets stilzwijgend permanent verwijderen wanneer het de enige goede versie is.
- Een rollback van de oude Sonarr/Radarr naar oorspronkelijke installaties blijft mogelijk; bewaak schema-/versieverschillen en herstel alleen geteste backups.
- Documenteer herstel na power loss, queued transcodes, half-uitgevoerde file import, mislukte cutover, gewijzigde Seerr links.

## K4. Updatebeleid

- Eigen version pin voor forks, centrale engine, images en migraties.
- Gecontroleerde upstream merges met compile/test, API compatibility en werkelijke Seerr-integratietest.
- Geen auto-upgrade naar Sonarr-major die de Seerr API breekt.
- Release candidates kunnen shadow mode draaien; productie-updates pas na rollbackcheck.

---

# DEEL L — TESTPLAN EN RELEASE-GATES

## L1. Testpiramide

1. Unit: policy ordering, episode mapping, language normalization, missing cause classification, job state machines en restore invariants.
2. Property-based: ranking determinisme, idempotency, no downgrade, unknown ≠ wrong, concurrent jobs.
3. Integratie: echte Sonarr-/Radarr-fork tegen test-DB, indexer- en downloadclient-testadaptors, Seerr contract, Shoko optioneel.
4. Mediafixtures: meerdere seizoenen, absolute anime, OVA, single/double episodes, verkeerde filename, dual audio fake/real, NL tracks, HDR10, HDR10+, DV 5/7/8 en corrupte media.
5. E2E: complete webflow, importwizard, backfill, user aanvragen via Seerr, beste release geselecteerd, AV1 post-verificatie, rollback.
6. Chaos/fault injection: stroomuitval, database restart, Redis failure, disk full, permission denied, downloadclient outage, dubbele import events, GPU hang, ZFS snapshot failure, netwerkverlies, gelijktijdige filewijziging.
7. Benchmark: performance op i7-14700 en optionele A310, CPU/GPU/IO-limit, grootte-bibliotheken en scanresumability.

## L2. Verplichte scenario's (geautomatiseerd)

- Een bestaande grote bibliotheek importeert zonder media te verplaatsen; aantallen en paths gelijk.
- Series en hun **monitored flags, per-season/per-episode uitzonderingen en toekomstige seizoenen** blijven correct.
- Na het uitschakelen van *oude* Sonarr/Radarr werkt de interne fork zelfstandig en worden nieuwe gemonitorde afleveringen nog gedownload.
- Historisch ontbrekende aflevering wordt periodiek opgezocht, met rate caps, en na geschikte release geïmporteerd.
- Onbeschikbare aflevering wordt zichtbaar `no suitable candidate`, zonder eindeloze retries.
- Verkeerde S02E03 is in werkelijkheid S01E03: detectie bij betrouwbaar onafhankelijk bewijs, geen foute vervanging bij onvoldoende bewijs.
- Anime 1080p Dual Audio krijgt voorrang boven 4K Japanese-only in audio-first profiel.
- NL ondertitels en NL audio per profiel correct herkend en gekozen.
- 'Dual Audio' releasenaam met alleen Japanese audio wordt als onjuiste claim geclassificeerd.
- Engelse commentaartrack is geen Engelse dub.
- Sonarr/Radarr release history en bestaande AV1-conversie leiden niet tot reretrieve loops.
- Seerr film- en serieaanvraag bereikt interne forks en beschikbaarheidsstatus werkt na import.
- Transcode verliest vereiste tracks: staging afgewezen, originele file intact.
- Dolby Vision profiel wordt niet ongemerkt gedegradeerd; unsupported encode → preserve original.
- ZFS/power failure in midden van commit: rollback of idempotent recovery, geen vermiste enige versie.
- Oude en nieuwe installatie starten nooit tegelijk als schrijfbeheerder voor dezelfde items in cutover-modus.
- De app en UI zijn na reboot bereikbaar, rollen en secrets correct afgeschermd.

## L3. Release/no-go criteria

**Blocker / no-go** zodra één van deze optreedt:
- Geen herstelbare kopie van gegevens bij kritiek vervangingspad.
- Een testcase toont onzekere episode-identificatie die tot destructieve vervanging leidt.
- Nieuwe afleveringen worden na cutover niet meer gemonitord of gedownload terwijl het testscenario wel een geschikte release heeft.
- Gemiste media worden zonder reden genegeerd; historische backfill ontbreekt.
- Seerr contracttests falen voor de opgegeven ondersteunde configuratie.
- AV1/DV-conversie beweert metadata te bewaren terwijl verificatie faalt.
- Bronbibliotheek verandert tijdens shadow import of dry-run.
- Migratie van monitored flags of episode mappings is aantoonbaar onjuist.
- Geen werkende rollback van cutover / recovery.
- E2E-app kan niet via TrueNAS Compose starten.

KPI's voor benchmark: report scan rates, kwaliteitspaspercentages, zoek-/import-succes, missing op dag 0 en na testen, indexer request rate, disk savings, VMAF/andere metrics waar toepasselijk, CPU/GPU-usage en queue latency. Geen universele reductie van ontbrekende downloads of nauwkeurigheidspercentage beloven zonder echte meetdata.

---

# DEEL M — UITVOERINGSFASEN EN OPLEVERING

## Fase 0 — Research, baseline en risico's
- Detecteer devomgeving, inspecteer TrueNAS target/documentatie, Seerr-versie, Sonarr/Radarr upstream en licenties.
- Leg API compatibility matrix vast (incl. Sonarr v5 risico), Fork Update Procedure, ADR's en afhankelijkheden.
- Maak `docs/PROGRESS.md`, `docs/ACCEPTANCE.md`, reproduceerbare developer startup.
**Gate:** compileerbare upstream clones / lokale forks en minimaal één geslaagde service-to-service test.

## Fase 1 — Werkende forks als bundel
- Build/launch gepinde forks, interne router, devcontainer/Compose, CI, healthchecks, secrets.
- Sonarr/Radarr API's extern bereikbaar voor Seerr via stabiele URLs.
**Gate:** Seerr contracttest kan media/request lezen en toevoegen in testomgeving.

## Fase 2 — Migratie en bibliotheekimport (P0)
- Wizard, preflight, officiële config backups, path mapping, shadow import, metadata sync, read-only scan, reconciliation en dry-run.
**Gate:** bestaande serie-/filmcatalogus en monitorstatussen identiek na shadow import, geen gewijzigde mediabestanden.

## Fase 3 — New Episode Monitoring & Missing Backfill (P0)
- RSS/new episode flow, future season policy, wanted dashboard, root cause diagnostics, smart backfill scheduler en Seerr sync.
**Gate:** historisch missing testscenario en toekomstig episode testscenario worden automatisch verwerkt, inclusief geverifieerde afwijzingsredenen en rate limits.

## Fase 4 — Identity, anime, talen, profiles
- Episode mappings, Shoko optioneel, FFprobe streams, taalherkenning, NL audio/subs, deterministic policy ranking en fork pre-grab hooks.
**Gate:** wrong season en dual audio testcases, zonder gevaarlijke false positives.

## Fase 5 — Quality & AV1 basis
- Inhoudsanalyse, trial encodes, quality policy, SDR/veilig HDR, CPU/GPU detectie en encoderqueue.
**Gate:** staged AV1 encode met stream/kwaliteit validatie, nooit onopgemerkt DV-metadata- of bronverlies; ondersteunde DV-routes mogen door gebruiker vanuit UI worden ingeschakeld voor proefencodes.

## Fase 6 — Full Auto herstel & optimalisatie
- Transactional recovery, ZFS adapter, limited ACL, rollback, quarantine, download/encode coördinatie, bulk optimization.
**Gate:** fault injection en complete rollback scenario's slagen.

## Fase 7 — Dashboard/UI polish
- Alle 12 schermen, responsive Nederlands/Engels, component library, calendar, wanted/coverage maps, Seerr interface status en AV1 studio.
**Gate:** browser-E2E desktop/mobile en toegankelijkheidstests slagen.

## Fase 8 — Cutover & productierijp maken
- TrueNAS Compose install, productie-hardening, backup/restore, upgraderules, performance, installatiehandleiding, cutoverwizard.
- **Cutover alleen met expliciete gebruikersactie wanneer echte host en Seerr beschikbaar zijn.**
**Gate:** volledige echte testomgeving toont dat nieuwe forks zelfstandig werken voordat oorspronkelijke Sonarr/Radarr uitgaan.

## Fase 9 — AV1-integratie, upstreampariteit en productievalidatie
- De AV1-integratie begint al in fase 0. In deze fase worden featurepariteit, hardwarebenchmark, 4K/HDR/DV-canary, TrueNAS-recovery en productiehardening formeel gevalideerd.
- Verwijder of vervang de oude Ubuntu-installatie pas na non-regression tests, expliciete cutover en werkende rollback.
**Gate:** featurepariteit met de repository `lvos04/av1`, behoud van veiligheidsregels, geslaagde TrueNAS-runtimeproeven en geen stil verlies van functionaliteit.

---

# DEEL N — REPOSITORYSTRUCTUUR EN VERPLICHTE ARTEFACTEN

Aanbevolen monorepo + twee afzonderlijke beheerde fork-repo's:

```text
mediaguard-ultimate/
├─ README.md
├─ MEDIAGUARD_GOAL.md
├─ apps/
│  ├─ web/
│  ├─ gateway/
│  └─ core/
├─ services/
│  ├─ analysis/
│  ├─ recovery/
│  ├─ encoder/            # bestaande AV1 Python-modules, niet herschrijven
│  └─ notifications/
├─ integrations/
│  ├─ sonarr/
│  ├─ radarr/
│  ├─ seerr/
│  ├─ shoko/
│  ├─ truenas/
│  ├─ trash/
│  └─ av1-source/          # pinned bron, behoud upstream provenance
├─ forks/
│  ├─ sonarr/        # submodule / fork ref en patch manifest
│  └─ radarr/        # submodule / fork ref en patch manifest
├─ packages/
│  ├─ domain/
│  ├─ contracts/
│  ├─ policy/
│  ├─ ui/
│  └─ i18n/
├─ deploy/
│  ├─ truenas/
│  └─ dev/
├─ tests/
│  ├─ fixtures/
│  ├─ unit/
│  ├─ contract/
│  ├─ integration/
│  ├─ e2e/
│  ├─ security/
│  └─ fault-injection/
├─ docs/
│  ├─ design/
│  ├─ MIGRATION_AND_CUTOVER.md
│  ├─ BACKFILL_OPERATIONS.md
│  ├─ SEERR_COMPATIBILITY.md
│  ├─ FORK_UPSTREAM_SYNC.md
│  ├─ AV1_REPO_INTEGRATION.md
│  ├─ AV1_BASELINE_AUDIT.md
│  ├─ AV1_SAFETY_DIFF.md
│  ├─ AV1_TRUENAS_PORT.md
│  ├─ AV1_DV_PLAYBACK_MATRIX.md
│  ├─ DATA_RECOVERY.md
│  ├─ SECURITY.md
│  ├─ INSTALL_TRUENAS.md
│  ├─ PROGRESS.md
│  ├─ DECISIONS.md
│  └─ TEST_RESULTS.md
└─ .github/workflows/
```

Verplicht opleveren: reproduceerbare builds, image build-pipeline, één TrueNAS-installatiebundel, wizard, complete webinterface, database-migraties, echte fork-integratie, read-only bibliotheekimport, future monitoring, missing backfill, quality engine, veilig AV1-basispad, Seerr compatibility suite, recovery/rollback, E2E-tests en documentatie. Integreer `lvos04/av1` als bestaande broncode vanaf het begin, met afzonderlijke migratie- en non-regressiontests, volledige UI-first-bediening en behoud van de oude installatie als fallback tot cutover.

---

# DEEL O — STARTACTIE VOOR CLAUDE CODE

1. Lees dit gehele bestand en schrijf een compacte **implementation matrix** waarin ieder requirement een ID, fase, eigenaar/component, test en status krijgt. Maak P0-fasen 0–4 en de migratie/cutover-safety-gates zichtbaar.
2. Controleer upstream git repositories, huidige stable releases, licenties, architectuur, Seerr API en TrueNAS actuele mogelijkheden. Pin commits en onderbouw keuzes.
3. Maak de repository, pin `lvos04/av1` en fork-upstreams, bouw reproduceerbare Sonarr/Radarr forks, porteer de bestaande AV1 FastAPI/Next.js/encoder naar een minimale TrueNAS Compose setup, en lever de eerste volledig werkende E2E-test.
4. Implementeer dan prioriteit **AV1-baseline audit & TrueNAS-port → bibliotheekimport → toekomstmonitoring en missing backfill → independent episode/audio verification → content-aware AV1 quality → safe full auto optimization**, zonder de gebruikersbibliotheek aan te raken voor cutover.
5. Werk autonoom door alle fasen heen. Stop alleen als werkelijke externe toegangsgegevens ontbreken, destructive access toestemming nodig is, of een aantoonbare externe blokkade bestaat. Lever dan bruikbare, getest werkende onderdelen en een exact unblock-plan.
6. Beschrijf aan het eind expliciet welke tests zijn uitgevoerd, welke echt zijn geslaagd en wat nog niet fysiek is getest; geen claims over niet-uitgevoerde integraties.

**Definitieve definitie van 'klaar':** na geautoriseerde cutover staan de **oude** Sonarr/Radarr-installaties uit; de **binnen MediaGuard geïntegreerde forks** blijven RSS, nieuwe afleveringen, historische missing searches, film- en serieaanvragen uit Seerr, imports en upgrades uitvoeren. De volledige bestaande bibliotheek is overgenomen, geïnventariseerd en staat in een veilige optimalisatiewachtrij. De webinterface toont alle operationele status, en recovery is aantoonbaar getest. De ontvangen Ubuntu AV1-code is daadwerkelijk geïntegreerd, functioneel gevalideerd op TrueNAS en kan veilig parallel aan de oude installatie worden getest.

---

\n---

# DEEL P — VERPLICHTE INTEGRATIE VAN `lvos04/av1` (GEÏNSPECTEERDE BRONCODE)

## P1. Vastgestelde huidige implementatie en hergebruik

**Bronrepository:** https://github.com/lvos04/av1, standaardbranch `master`. Inspectie uitgevoerd op 2 november 2026 via GitHub-bestanden. **Dit is statische broncode-inspectie, geen claim dat tests zijn gedraaid of dat de software op TrueNAS draait.** Tijdens implementatie de volledige commit-SHA pinnen en een verse baseline-test draaien.

| Aanwezig in repository | Concrete bron | Vereiste behandeling in MediaGuard |
|---|---|---|
| FastAPI-backend, lifecycle, herstel vóór plannerstart | `backend/app/main.py` | Hergebruiken, starten vanuit OCI-container en service-health aanpassen |
| Next.js App Router, Tailwind, TypeScript | `frontend/package.json`, `spec/05-ui.md` | Doorontwikkelen naar MediaGuard UI, niet zonder noodzaak vervangen door Vite |
| SQLite-WAL, echte catalogus/jobs/decisions | `backend/pyproject.toml`, `spec/03-datamodel.md` | Geen gegevens weggooien; backwards-compatible migratie en backup |
| Media-probe met HDR10 frame-side-data, video-only bitrate, DV, audio/subs | `backend/app/media/probe.py` | Direct hergebruiken, begrensde extra tests/aanpassingen |
| Deterministische encoder-/trackselectie | `backend/app/media/beslissen.py` | Reuse + policy uitbreiden met episode/audio- en per-title kwaliteit |
| DV/HDR-mux en commandobouw | `backend/app/media/commandos.py`, `spec/02-domeinkennis.md` | Behouden en echte A/B tests; niet simplificeren |
| CPU SVT-AV1 en GPU Intel Arc QSV-werkbanen | `backend/app/planner.py`, `README.md` | GPU device/driver via TrueNAS container, max concurrency begrenzen |
| Veilige vervanging met transactie en crashherstel | `backend/app/vervanging.py` | Hergebruiken, uitbreiden met rollback/quarantaine vóór autonome mutaties |
| Canary/policy/AI-controller, gecodeerde secrets | `AGENTS.md`, `spec/07-bediening-en-beheer.md` | Houd AI adviserend; centrale policy engine blijft beslissend |
| Testfixtures en projectinstructies | `test/`, `backend/tests/`, `AGENTS.md` | Bewaar, draai alle bestaande tests en breid cross-service regressies uit |
| Native Ubuntu 26.04/systemd/SMB-helper | `install/`, `systemd/`, `spec/08-opslag-herstel-en-schakelaars.md` | Niet rechtstreeks als TrueNAS rootservice gebruiken; container-/dataset-adapters bouwen |

### Specifieke behouden modules

1. `backend/app/media/probe.py`: HDR-mastering in **frame side data**, niet alleen de stream; kleurwaarden, trackinfo en schatting van video-bitrate apart van audio. Bij onbetrouwbare bronwaarden actief meten of **overslaan**, niet gokken.
2. `backend/app/media/beslissen.py`: scheiding tussen **analyse/besluit/uitvoering**. Huidige basisparameters zijn o.a. CRF 27/29/31 voor 2160/1080/720, video-bitratedrempels van 20.000/8.000/4.000 kbps en een configureerbare doelbitrate bij 2160. Dit zijn voorlopige profielwaarden, **niet automatisch optimale waarden per titel**.
3. `backend/app/media/commandos.py`: HDR/BT.2020 metadata, SDR/HDR-differentiatie, correcte kleurvolgorde bij `mkvmerge`, `p010le` voor QSV, ondertiteling/attachments en DV-tooling.
4. `backend/app/vervanging.py`: staged output en `fsync`/`os.replace`, snapshot van oorspronkelijke stat/metadata, crash recovery. **Huidige replace heeft géén gegarandeerde behouden bronkopie.** Full Auto vereist vóór activatie herstelbare versie en complete recoverytests.
5. Huidige planner en web: CPU/GPU-parallellisme, logs, metrics, proefencodes, schedules, GPU-diagnose, configuratie en error handling.

**Fout vermijden:** de AV1-repository is geen lege specificatie. De bestaande tests en veiligheidslogica zijn basisfunctionaliteit. Een herschreven, vereenvoudigde encoder is onacceptabel als die regressies introduceert.

## P2. Prioritaire veiligheidsconflicten tussen projecten

| Bestaande AV1-policy | Nieuwe MediaGuard-wens | Bindend migratiebesluit |
|---|---|---|
| Na geslaagde verificatie wordt bron permanent atomisch vervangen, zonder prullenbak | Full Auto met quarantaine/rollback | Nieuwe centrale replacement coordinator moet **eerst** terugzetbare bron veiligstellen en bewijs/loggen; pas dan commit. De AV1-worker zelf mag niet meer direct finale bronpaden muteren |
| Besluiten vereisen goedkeuring of beperkte AI-autoriteit na canary | Volledig automatisch | Automatiseer uitsluitend via deterministische allowlisted policy ná library-canary; geen AI-tool met schrijfrechten; bij twijfel handmatige review |
| HDR10+ altijd overslaan | Alles zoveel mogelijk naar AV1 | **Overslaan tenzij later aantoonbaar gevalideerde route**; nooit een force-optie zonder bewezen behoud, nooit metadata stil verwijderen |
| DV5 overslaan; DV7 via 8.1 naar profiel 10 met mogelijk EL-verlies | Dolby Vision behouden | Behoud bron indien geen volledig equivalent bewezen; bij DV7 FEL geen claim van lossless DV-behoud bij EL-verlies |
| DV-profiel-10-encode technisch mogelijk maar playback ongetest | Geschikt voor Plex/Jellyfin/TV | **DV skip default** totdat echte device/client matrix inclusief originele testbronnen succesvol is. Metadata alleen bewijst geen afspeelbaarheid |
| Hardlinks overslaan of alleen expliciet verbreken | Bibliotheekbreed autonoom optimaliseren | Seeding/hardlinks nooit stil kapot maken; linkcount+inode detecteren, expliciete policy per library, nettobesparing berekenen |
| Extra audio/sporen verwijderen volgens geselecteerde taal | Dual Audio/Engels/Japans en Nederlandse voorkeur | **Niets verwijderen vóór nieuwe taalpolicy**; dual audio, originele audio, audiodescriptie en forced/signs & songs/subtitles bewaren conform expliciete profielen |
| Eén Ubuntu host, systemd, lokale LAN-gateway | TrueNAS SCALE app | Port naar niet-geprivilegieerde OCI-containers, gescheiden scan/encode/recovery accounts en datasets, geen root-helper van Ubuntu overnemen |
| Huidige `SQLite` voor AV1-jobs | MediaGuard PostgreSQL voor domain/queue | Voorkom big-bang migratie. Kies adapter of geteste incrementele import; dubbele waarheid vermijden. Elke oude job en auditrecord blijft reconstrueerbaar |

Vereist expliciete ontwerpnotitie `docs/AV1_SAFETY_DIFF.md` met **voor/na**, risico, waarom, regressietest en rollback voor iedere afwijking van `spec/04-veiligheidsregels.md`.

## P3. Exacte integratiestappen voor Claude Code

**Stap 1 — Reproduceerbare baseline:** clone `lvos04/av1` pinned op actuele SHA; lees `README.md`, `AGENTS.md`, `spec/02-domeinkennis.md`, `spec/04-veiligheidsregels.md`, `spec/06-dolby-vision.md`, `spec/08-opslag-herstel-en-schakelaars.md`, `spec/09-figma-afstemming.md`. Inventariseer alle Python-, frontend-, install- en testmodules. Draai `pytest`, `ruff`, `mypy`, `npm ci`, `npm run lint`, `npm run typecheck`, `npm run build` in schone ontwikkelcontainer; noteer resultaten en failures zonder ze weg te poetsen.

**Stap 2 — Modulegrenzen:** scheid bestaande codecprobe, trackselectie, encoderpolicy, encode-runner, verificatie en atomic replacement van Ubuntu-hostservice, dashboard en bestandssysteemadapters. Behoud bestaande publieke interne interfaces waar zinvol; voeg getypeerde ports/adapters en regressietests toe. Doel: één MediaGuard-encoder, niet twee parallelle concurrerende writers.

**Stap 3 — TrueNAS-port:** OCI-image met geverifieerd FFmpeg 7.1+/8.x, `libsvtav1`, `av1_qsv`, mkvmerge, dovi_tool, optional libvmaf; expliciete device mapping `/dev/dri` met minimale groepen; controle Intel media-driver/VAAPI. Gebruik TrueNAS host-paths, dataset-ACL's, persistente config en workdir. Geen Ubuntu UFW/systemd/native SMB sockethelper in container. SMB extern mounten via TrueNAS en read-only doorgeven wanneer mogelijk. Fail-closed als GPU of mount ontbreekt.

**Stap 4 — Original release provenance:** koppel output van AV1-probe aan Sonarr/Radarr originele release-ID, source quality, CF-score, episode/movie-ID, releasegroep, audio, originele frame/stream metadata. Conversions zijn aparte derivations van een bronversie. Bij nieuw gedownloade release eerst identity/audio check, pas daarna AV1. Na encode update fork-api en laat Seerr/mediaserverstatus controleren.

**Stap 5 — Coördinatie:** introduceer getypeerde statusinterface `Analyze → PolicyVerdict → Reserve → EncodeStage → VerifyResult → Commit/Quarantine → Refresh/Confirm`, met lease/lock en persistent eventlog. Encoder krijgt alleen leesrechten op bibliotheek en schrijven in staging. Recovery-worker is enige writer op beheerde bibliotheekpaden, met ondergrenzen, herstelmanifest, lock en transactions. Nooit encoderjob runnen terwijl download/import/hernoemtaak actief is op hetzelfde item.

**Stap 6 — Incrementele verbetering:** begin met pariteit voor SDR/HDR waar bewezen; implementeer dynamische bitrate/content-aware trials volgens P4; unlock DV-route alléén per device en profiel via P5.

**Stap 7 — Library import:** importeert ~50 TB uit bestaande bibliotheken **in situ** zonder alles te verplaatsen, hernoemen, hashen of herdownloaden. Scan op inode/mtime/grootte, daarna gerichte probe en per-item foto van oorspronkelijke metadata. Optimalisaties prioriteren op risico, besparing, episodeproblemen en audio. Zware scans/codes met I/O-budgets en dagelijkse incrementele planner.

**Stap 8 — Seerr/missing:** nieuwe Sonarr/Radarr-forks nemen monitored series, next airing episodes, historical missing backlog, indexers, downloader setup, root folders, kwaliteitsprofielen en grab/import-history over. Seerr API compat-test. Nieuwe anime- of downloadwensen mogen geen AV1-schrijfconflict creëren. Old apps blijven read-only/parallel tijdens migratie, nooit twee RSS-grabbers tegelijk voor dezelfde library bij cutover.

## P4. Smart Encoding: van vaste CRF naar per-title en eventueel per-scene

**Huidige situatie:** `beslissen.STANDAARD` werkt met bitrate-drempels/CRF/presets, geen bewezen per-title optimizer voor ieder bestand. Voeg daaraan toe, zonder bestaande safe baseline af te breken:

1. **Content fingerprint:** verzamel representatieve fragmenten uit begin, midden, einde en met op scène-afwisseling, beweging, detail/grain, donkere delen, kleur-dynamiek. Verifieer dat selectie niet alleen intro/zwart bevat.
2. **Hardware capabilities:** test encoders/driver op echte 6–30s testfragmenten inclusief 10-bit/HDR, rapporteer tijd, stroom, grootte, bitrate, perceptuele scores. GPU-encode pas actief na pass. i7-14700 CPU en optionele Intel Arc A310 ondersteunen afzonderlijke lanes.
3. **Trial matrix:** korte AV1-proefencodes voor CRF/CQ, preset, filmgrain-opties en encodebaan, met budget per sample. Start met bestaande safe defaults; zoek adaptief naar beste punt op rate-distortion-kromme.
4. **Score:** VMAF waar geschikt/beschikbaar, SSIM en aanvullende anime/HDR-gevoelige kwaliteitsmetingen, plus blockiness/banding/fine-detail checks. Geen absolute VMAF-garantie; HDR eerst vergelijken in geschikt genormaliseerd domein en afspeeltesten.
5. **Policy:** optimaliseer benodigde bits per pixel/segment met prioriteit kwaliteit → compatibiliteit → opslagsaving → encode-energie/tijd. Beschrijf loss-budget en minimum netto-besparing, maximum bestandsgrootte en minimum kwaliteit per profiel. Gebruik CRF/CQ voor kwaliteitsgestuurde output; target bitrate alleen waar zinvol.
6. **Per-scene optioneel:** alleen als de gekozen encoder/toolchain controle over segmenten biedt zonder audio-desync, HDR-metadata/verlies of incompatibele segmentgrenzen; anders per-title.
7. **Winstcontrole:** als resultaat onvoldoende kleiner of kwaliteitsverlies te groot: houd bron, geen vervanging. Bij eerder AV1 bestand: skip, tenzij aparte bewezen reden.
8. **Learning:** leer per bronklasse startinstellingen uit succesvolle gecertificeerde proefencodes; model mag niet zelfstandig policy-/safety-gates overrulen. Sla metrics met tool/driver/encoder/configversies op voor replay.

Mogelijke output van `POST /api/v1/encoding/plan`: bron-/bestands-ID, codec/HDR/DV/streamprofiel, samplepolicy, trials, verwachte omvang, encoderplan, risicoklasse, `approved_for_execution=false` totdat gates slagen. `/encoding/plan/{id}/simulate`, `/encoding/jobs`, `/encoding/benchmarks` leveren status/evidence.

## P5. Dolby Vision en HDR: strikt profielbeleid

Overgenomen uit `spec/06-dolby-vision.md` en `spec/02-domeinkennis.md`:

- **DV profiel 5:** standaard nooit AV1-transcode; bron behouden.
- **DV profiel 7 (MEL/FEL):** onderscheid MEL vs FEL; converteren van profiel 7 naar 8.1 kan Enhancement Layer (bij FEL beeldinformatie) kwijtraken. Behoud originele als archiefbron tenzij exact gelijkwaardigheid/gebruikersacceptatie bewezen en expliciet gekozen.
- **DV 8.1/8.4:** bestaande SVT-AV1 route kan met `-dolbyvision 1` RPU-metadata naar AV1/DV profiel 10 encoderen, mits toolchain en benodigde compatibiliteit. Dit bewijst nog niet dat TV/mediaserver/client dit correct afspeelt.
- **DV-beleid (bijgewerkt v3):** de gebruiker heeft DV-afspelen al werkend gezien en wil DV-conversie kunnen activeren. Maak in de UI een per bibliotheek/profiel/per bestand instelbare schakelaar `Dolby Vision naar AV1 toestaan`, met optionele proefconversie, metadata-controle en rollback. Een universele apparaatmatrix is **geen harde voorwaarde om een proefencode uit te voeren**; ontbrekende compatibiliteitsgegevens zijn informatie/waarschuwing. Nieuwe productie-installaties starten in `Testen op kopie`, en de wizard kan `DV-conversie inschakelen` direct activeren voor ondersteunde profielroutes na capability-check. De originele DV-bron moet herstelbaar blijven totdat de afzonderlijke Full Auto-vrijgave en recovery-gates zijn voldaan. Bij niet-aantoonbaar metadata-/streambehoud: nooit bron vervangen.
- **HDR10+ default SKIP:** geen stil verlies van ST 2094-40-dynamische metadata. Later alleen via aparte bewezen en volledig geteste route toestaan.
- **HDR10:** frame-side-data, mastering display, content light, kleurprimaries, trc/matrix/bitdepth en echte reproduceerbaarheid controleren. GPU-QSV mag niet ongemerkt de HDR-metadata laten vallen; voer aparte CPU route of valideerde mkvmerge herstelstap uit.
- **Audiotracks, hoofdstukken, subtitles/ASS-attachments:** volg audio-profielen en expliciete behoudsregels; documenteer dat remux van `mov_text` naar MKV correcte omzetting vereist.

Maak `docs/AV1_DV_PLAYBACK_MATRIX.md` met voor elk outputprofiel ten minste: inputprofiel, base layer, FEL/MEL, outputcodec/container, RPU-detectie, HDR fallback, beeldvergelijking, player/server model + versie, afspeelresultaat, falen en herstel. Bij Full Auto mogen door de gebruiker vrijgegeven DV-profielroutes na geslaagde technische verificatie en ingestelde canary-/recoverygates draaien; het ontbreken van een universeel groen afspeelapparaatrecord is op zichzelf **geen blokkade**. Leg bekende werkende apparaten en eigen afspeeltests vast voor inzicht; daadwerkelijke metadatafouten blokkeren nog altijd vervanging.

## P6. Nederlandse/Engelse/Japanse audio en safe cleanup

De huidige AVI/AV1-parser kan al o.a. `nld`, `eng`, `jpn` herkennen, maar `kies_sporen()` filtert met een talenlijst en kiest bij `beste_per_taal` één track per taal op kanaalaantal/bitrate. **Dit is nog geen onafhankelijke taalherkenning, correcte dual-audio-preferentie of behoud van alle belangrijke tracks.** Voer eerst een **audio inventory**, dan een **release policy**, dan pas optionele stream cleanup uit.

- Normaliseer `nl/nld/dut`, `en/eng`, `ja/jpn`, lege/ongeldige tags; controleer daadwerkelijke gesproken taal optioneel via speech-language detection.
- Dual Audio (Engels + Japans) > Engels > Japans voor anime, tenzij ingestelde serie-uitzondering; Nederlands toegevoegd als configureerbare audio- én ondertitelvoorkeur.
- Behoud default track en expliciete prioriteit; onderscheid commentary, audiodescriptie, hoofdprogramma, forced, signs/songs en alternatieve lossless tracks. Alleen verwijderen op basis van expliciete per-library stream-policy én geverifieerde output.
- Niet automatisch afzien van een aflevering als alleen Japanstalige fallback beschikbaar is: zoek later verder naar de gewenste Engelse/dual release.
- Gebruiker kan bron voor kinderseries op Nederlands zetten zonder animevoorkeuren globaal te wijzigen.

## P7. Design en webinterface: bestaand Figma + nieuwe MediaGuard-functies

De bron bevat `spec/09-figma-afstemming.md` met bestaand Figma-ontwerp als visuele referentie: https://www.figma.com/design/9CQugincBWbZfyDj7xOtiB/Untitled?node-id=0-1. **Hergebruik waar toegankelijk en gemachtigd; ontwerpwijzigingen mogen veiligheidsbeleid niet verzwakken.**

- Behoud Next.js App Router/TypeScript/Tailwind/Recharts, bestaande AV1-dashboardinformatiedichtheid, CPU/GPU-lanes, FPS/ETA, besparingen, decisions, logs en settings. Breid dit uit met Sonarr/Radarr/Wanted/Seerr/Identity en kwaliteitsdetails. Eén navigatie, één thema, een uniforme tokenbibliotheek.
- Desktop: 1440/1280 breed met 12-col raster, vaste contextuele sidebar, keyboard shortcuts, compacte tabellen met virtuele rendering en sticky headers; duidelijke actuele status (niet alleen decoratieve kleur).
- Mobiel: minimaal 360 px, adaptieve tabs, geen horizontaal kapotte tabellen; toetsenbord- en schermlezerondersteuning (WCAG 2.2 AA nastreven).
- Design tokens: donker als eerste thema, licht optioneel, statuskleuren met tekst+icoon en voldoende contrast, consistente spacing 4/8/12/16/24/32, 8/12px radii, rustig gegevensgericht ontwerp. Live metrics waar relevant, no fake numbers.
- Startpagina kaarten: totaal beschikbaar, missing episode backlog, foutief gemapt, upgrade potentieel, reeds geoptimaliseerd, bespaarde TB, actieve encodes, opslagwaarschuwingen, Sonarr/Radarr/Seerr connectivity.
- Detailpagina: originele release, actuele AV1, episode-confidence/evidence, talen, subtitles, DV/HDR safety, kwaliteitsmetingen/encode-trials, hersteltijdlijn, rollbackstatus.
- Unified wizard: TrueNAS datasets/ACL/GPU → oude Sonarr/Radarr configs → mediapaden/mapping → read-only audit → ontbrekende episodes → AV1 preview → canary → full-auto cutover (bevestigd).
- AV1 Studio: compare bron/resultaat met sample-crop, bitrategraph, CPU/GPU-keuze, encodekwaliteit, compacte live jobweergave, DV-modi, experimentele DV-proefconversie en hardlink-waarschuwingen.
- Gevaarlijke acties hebben reden, impact, aantal bestanden, totale omvang, nieuwe/oude status en bevestiging. Dry-run altijd zichtbaar.
- Schrijf tests/screenshots op meerdere breakpoints; alle UI-labels Nederlands/Engels. UI mag geen werkend behoud van DV of rollback claimen zonder bewezen backend-status.

## P8. Migratie van bestaande AV1-configuratie en Sonarr/Radarr-library

1. Bestaande Ubuntu AV1-app mag los blijven draaien tijdens de ontwikkelfase. Bij productiecutover moeten twee encoders/writers op dezelfde mediabestanden voorkomen worden; zorg voor expliciete writer handoff, niet alleen applicatieprocessen stoppen.
2. Maak migratie-export uit SQLite-WAL met profielconfig, libraries, scanposities, jobhistorie, provenance, conversieresultaten, audit en secrets references. Versleutelde oude keys moeten door gebruiker worden geïmporteerd of opnieuw opgegeven; nooit plaintext in logs of Git.
3. Gebruik importmapping `source_instance + source_id -> mediaguard_uuid`. Houd oorspronkelijke release-ID, library paths, device/inode waar mogelijk, grootte/mtime/hash en besluiten traceerbaar.
4. Media lopen in situ op bestaande TrueNAS-datasets: absoluut **geen kopie van 50 TB als standaard**, geen library-wide volledige hash in eerste pass, geen massa-renames. Path-/UID/GID-mappings testen op de echte mountlocatie.
5. Import van Sonarr/Radarr gebeurt met monitorstatus, toekomstige afleveringen, grabbed/downloading/wanted en indexers zonder credentials bloot te leggen. Seerr wordt aangesloten op nieuwe fork-endpoints nadat compatibiliteit bewezen is.
6. Backfill werkt met rate limits en kwaliteitsprioriteiten, geen agressieve flood van indexers. Missende afleveringen met verklaring `not-yet-aired`, `unmonitored`, `no-indexer-hit`, `rejected-language`, `incorrect-mapping`, `download-client-failed`, `import-failed`, enz.
7. Doe eerst shadow-mode en rapport. Canary op een kleine gekozen bibliotheek, restoreproeven en hardwarevalidatie. Pas na expliciete productiecutover oude Sonarr/Radarr en oude AV1 writer(s) stilzetten.

## P9. Verplichte regressie- en acceptatietests

**Baselinetests:** run repositorytests voor probe, commandos, beslissen, scanner, planner, backup/recovery en frontend. Maak daarnaast short fixtures voor SDR H.264/HEVC, HDR10, HDR10+, DV 5, 7 MEL/FEL, 8.1, MKV/MP4 mov_text, ASS + embedded font, multi-audio incl Engels/Japans/Nederlands, commentary, hardlinks, low bitrate, already AV1, beschadigd MKV en dubbele episode mappings.

**End-to-end:** download via Seerr → forked Sonarr/Radarr → candidate policy → verified import → AV1 staged encode → audio/HDR quality gate → recovery commit → fork refresh → Seerr status. Als originele DV-/audio-metadata of vereiste streams niet correct behouden kunnen worden: geen destructieve activatie, behoud bron en rapporteer. Een gebruiker mag wél via de UI een geïsoleerde DV-proefencode van ondersteunde routes starten zonder verplichte device-matrix-gate. Test energie-/performance-limieten en reboots mid-job.

**Nieuwe performancecriteria:** geef encoderbeslissingen met evidence en geplande size/time; vergelijk ten minste meerdere titelsoorten; accepteer alleen meetbaar betere opslagbesparing of gelijkwaardige kwaliteit, niet simpelweg lagere bitrate. Adaptive model mag nooit HDR, audio, episode of source-gates passeren. Borg dat oudere succesvolle testfixtures niet regressief worden.

**Hard gates:** reproduceerbaar TrueNAS-image, GPU probe en korte encode op werkelijke host, AV1- en Sonarr/Radarr-upstream-forks met gepinde SHAs, Seerr API contracttest, read-only 50 TB import zonder massabewerkingen, forced canary, rollbacktests, encode-behoud, data backup/restore, complete documentatie en Nederlands dashboard.

## P10. Bijgewerkte bouwvolgorde, onmiddellijk uitvoerbaar

0. **Audit** en pin `lvos04/av1`, Sonarr/Radarr, lints/tests en Figma-designreferenties.
1. **TrueNAS baseline-port** van bestaande FastAPI/Next.js/encoder met uitsluitend read-only testbibliotheek; geen echte media wijzigen.
2. **Forkservices** van Sonarr en Radarr, compatibele Seerr-koppeling en upstream patchbeheer.
3. **Import en shadowmodus** voor huidige Sonarr/Radarr-configs en bestaande videobibliotheek, inclusief conversiegeschiedenis.
4. **Continuïteit** voor nieuwe afleveringen, wanted/missing-backscheduler, indexers en bestandsimport; bewijs dat oorspronkelijke Sonarr/Radarr kunnen uitstaan zonder verlies van functies.
5. **Episode-/taal-/kwaliteitspolicy** (anime, NL, 4K, TRaSH) met pregrab/preimport hooks in forks.
6. **Smart encoding** uitbreiden met per-title proefencodes, adaptive score en harde HDR/DV-compatibiliteitsmatrix.
7. **Full Auto recovery** met quarantaine en durable recovery; schrijfbevoegdheid uitsluitend recovery-worker.
8. **Unified UI** op bestaande Next.js-basis, breed testbaar dashboard en AV1 Studio.
9. **Canary, fault-injection, performance en TrueNAS-productietests**; geen unsafe zelfverklaarde successen.
10. **Goedgekeurde cutover** pas na succesvolle proef met echte Seerr requests en meerdere toekomstige afleveringen.

## P11. Herbruikbare Claude Code-startopdracht (dit bestand volledig gebruiken)

> `/goal Voer MEDIAGUARD_GOAL.md volledig uit. De AV1-broncode is NU beschikbaar op https://github.com/lvos04/av1. Clone en pin deze repo en inspecteer README.md, AGENTS.md, alle specs, backend, frontend, install en tests. Behoud en porteer de bestaande werkende AV1-engine naar TrueNAS in plaats van een tweede nieuwe encoder te schrijven. Bouw daarnaast beheerde Sonarr/Radarr-forks, importeer de huidige bibliotheek inclusief opvolging van nieuwe afleveringen en missing backfill, houd Seerr API-compatible, maak Dutch/English/Dual Audio en content-aware bitrateoptimalisatie, integreer veilige ZFS-quarantaine/rollback en één volledig Nederlandstalig Next.js-dashboard. Werk met echte regressie/E2E-tests, shadow/canary, en voer nooit onomkeerbare acties op productiebestanden uit zonder expliciete cutover. **Implementeer de DV-schakelaars én UI-first-beheer uit DEEL Q.** Bij conflicten prevaleert DEEL Q boven DEEL P en eerder geschreven delen; werk autonoom door en rapporteer bewijs van tests.`

---

# BRONNEN / TE CONTROLEREN BIJ IMPLEMENTATIE

- Sonarr code & GPL-3.0: https://github.com/Sonarr/Sonarr
- Radarr code & GPL-3.0: https://github.com/Radarr/Radarr
- Sonarr documentatie: https://wiki.servarr.com/sonarr/quick-start-guide en https://wiki.servarr.com/sonarr/faq
- Sonarr Wanted & missing: https://wiki.servarr.com/sonarr/wanted
- Radarr bibliotheekimport: https://wiki.servarr.com/radarr/library
- Seerr service-compatibiliteit: https://docs.seerr.dev/using-seerr/settings/services/
- TrueNAS Custom Apps: https://www.truenas.com/docs/scale/26/apps/installcustomappscreens/
- Intel Arc A310 encode-specs: https://www.intel.com/content/www/us/en/products/sku/227958/intel-arc-a310-graphics/specifications.html
- Dolby Vision RPU-tool en beperkingen: https://github.com/quietvoid/dovi_tool
- TRaSH Guides: https://trash-guides.info/
- Bestaande AV1-broncode: https://github.com/lvos04/av1
- AV1-huidige veiligheidsregels: https://github.com/lvos04/av1/blob/master/spec/04-veiligheidsregels.md
- AV1-domeinkennis: https://github.com/lvos04/av1/blob/master/spec/02-domeinkennis.md
- Dolby Vision-beperkingen: https://github.com/lvos04/av1/blob/master/spec/06-dolby-vision.md
- AV1 Figma-referentie: https://github.com/lvos04/av1/blob/master/spec/09-figma-afstemming.md

**Bronnen zijn referenties; Claude Code moet hun actuele inhoud en versiegeschiktheid voor de uiteindelijke build opnieuw controleren.**


---

# DEEL Q — DEFINITIEVE AANVULLING V3: DOLBY VISION ZELF BEDIENEN EN 100% UI-FIRST

> **Voorrangsregel:** Dit deel is de meest recente expliciete gebruikerskeuze. Bij tegenspraak gaat DEEL Q vóór alle eerdere delen, inclusief P5, bestaande AV1-defaults en de Ubuntu-specs. Dat geldt uitsluitend voor **configureerbare DV-toestemming** en **UI-beheer**; bestaande controles op fysieke bestandsintegriteit, audio-/videostreambehoud, permissies, transacties en bronherstel blijven onvoorwaardelijk. Geen schijnknoppen of handmatige YAML-workarounds voor normale bediening.

## Q1. Gebruikersbesluit en acceptatie

De gebruiker heeft gemeld dat Dolby Vision-afspelen met de bestaande AV1-app al werkte en wil de automatische algemene DV-blokkade opheffen. Bouw **niet** opnieuw een architectuur waarin 'geen publieke clientcompatibiliteitsmatrix' per definitie alle DV-conversies blokkeert. Bouw in plaats daarvan **zelfbedienbare DV-controles** met technische veiligheid in de encodepipeline.

**Vereist UI-gedrag:**

1. **`Dolby Vision → AV1 toestaan`**: schakelaar per globale standaard, bibliotheek, profiel, titel en afzonderlijke job; fijnmazige overerving tonen (`Globaal → Bibliotheek → Profiel → Titel → Job`). De laatste expliciet ingestelde uitzondering wint, maar geen regel kan integriteit/verificatie uitschakelen.
2. **Vier DV-modi:** `Origineel behouden`, `Proefconversie op kopie`, `Automatisch converteren na verificatie`, `Experimenteel profiel (behoud bron)`. Hiermee zijn risicokeuze en mutatiekeuze afzonderlijk.
3. **Gebruiker heeft al een werkende DV-afspeelroute:** bied `Deze afspeelketen werkt bij mij`-markering per client/server/container/codec/DV-profiel aan. Dit is een persoonlijke voorkeur/waarneming, geen bewezen universele compatibiliteit; geen metadata-verificatie uitschakelen.
4. **Installatiewizard:** toon tijdens AV1-setup de DV-keuze, met vooringevulde optie `DV via UI beschikbaar`, en mogelijkheid `Proefconversie` of `Automatisch na canary`; niet verplicht naar SSH/configbestand. Bij bestaande import van DV-profielen mag de wizard deze instelling correct overnemen, maar hij mag nooit massaal op productiemedia schrijven vóór cutover.
5. **Per bestand:** `Test nu`, `Toon metadata vóór/na`, `Gebruik CPU SVT-AV1`, `Zet resultaat apart klaar`, `Bekijk afspeelchecklist`, `Bevestig geschikte client`, `Activeer na verificatie`, `Herstel origineel`. Acties zijn echte backend-events en hebben status/progress/log.
6. **DV-kleuren/status in UI:** `Toegestaan`, `Proefconversie`, `Gecontroleerd`, `Origineel behouden`, `Niet ondersteund`, `Verificatie mislukt`, `Herstelbaar`. Geel betekent waarschuwing, niet een permanent, niet-uitschakelbaar blok.
7. **Geen handmatige edit van `dv_modus`, JSON, Docker environment of ffmpeg command nodig.** De UI vertaalt de keuze via gevalideerde REST API naar een persistente versieerbare policy en de bestaande Python beslisfunctie.

### Q1.1 Ondersteunde DV-profielen: exact beleid

| Inputprofiel | UI-behandeling | Werkelijke grenzen |
|---|---|---|
| DV 8.1/8.4 | Via schakelaar AV1/SVT proef- of productieencode toestaan | Verifieer toolchain en RPU-output; daadwerkelijke playback kan per client verschillen |
| DV 7 MEL | Via schakelaar en geverifieerde 7→8.1-route toestaan | Behoud bron herstelbaar; test timing, kleur, RPU en audio |
| DV 7 FEL | Experimentele conversie **naar apart resultaat** mogelijk indien route technisch ondersteund | FEL-beeldinformatie kan verloren gaan; noem dat expliciet; originele bron niet automatisch vernietigen |
| DV 5 | Alleen een expliciet ontwikkelde, met echte testbestanden bewezen compatibele omzetroute toestaan; anders `Niet ondersteund` | DV5 heeft geen gewone HDR10-fallback; het forceren van een niet-ondersteunde route kan kleur ernstig aantasten |
| HDR10+ | Eigen, afzonderlijke profielschakelaar voor toekomstige bewezen methode | Geen verlies van dynamische metadata stilzwijgend toestaan; bestaande AV1-pipeline behoudt voorlopig skip |

**Wat de gebruiker mag overrulen:** een eerdere beleidsblokkade op grond van *onbekende spelercompatibiliteit* of *experimentele status*, en per bestand kiezen voor een aparte proefoutput. **Wat niet mag worden uitgezet:** controleren of Dolby Vision-/HDR-metadata daadwerkelijk is meegedragen, behoud van vereiste streams, fsync/crash-safe vervanging, werkende rollback vóór Full Auto, bestanden die tijdens de job zijn gewijzigd, en bescherming tegen de technische DV5/FEL-beperkingen. Er mag geen UI-schakelaar ontstaan met tekst 'Negeer alle DV-controles' die technisch kapotte resultaten als veilig markeert.

### Q1.2 Dolby Vision technisch ontwerp

- De huidige `lvos04/av1` gebruikt `libsvtav1` en expliciet `-dolbyvision 1` voor de beschreven DV-route. **Hergebruik en verifieer** die implementatie; herschrijf haar niet tenzij tests een defect tonen.
- CPU/SVT-route met juiste FFmpeg/toolchain, optionele `dovi_tool` voor profiel 7, daarna matroska-/bitstreaminspectie met `ffprobe`, `mediainfo`, indien passend `dovi_tool`.
- Besteed afzonderlijk aandacht aan 7 MEL versus 7 FEL en onderscheid 'RPU behouden' van 'alle beeldinformatie behouden'. Bewaar conversiemethode, metadataresultaten en evt. goedkeuring in `dv_conversion_evidence`.
- Een DV-resultaat mag na een geslaagde **geïsoleerde** proefconversie bekeken worden zonder het origineel aan te raken. Voor een automatische bibliotheekvervanging moet herstelbaarheid geregeld zijn en moeten de expliciet gekozen routes, de technische controles en canary-gates slagen.
- De app moet onthouden dat de gebruiker voor specifieke apparaten zelf succesvolle playback heeft bevestigd; gebruik dit om waarschuwingen te verminderen, niet om technische tests over te slaan.
- Tests moeten bewijzen dat een ingeschakelde DV-policy ook daadwerkelijk resulteert in een uitvoerbare conversietaak, in plaats van steeds `skip` terug te geven vanuit oude hardcoded checks.

## Q2. Harde producteis: normale werking volledig via UI

**Definitie van 'alles via de UI':** na eenmalige installatie op TrueNAS kan de gebruiker **alle dagelijkse en geplande applicatiefunctionaliteit** configureren, bekijken, uitvoeren, pauzeren, herstellen, testen en updaten via het MediaGuard-dashboard. De app mag niet vereisen dat de gebruiker in `.env`, YAML, JSON, SQLite, CLI, terminal, SSH of broncode moet werken om een normale functie te gebruiken. Waar TrueNAS-hostrechten vereist zijn, bouw een beperkte, veilige officiële helper of een duidelijke deep-link naar de relevante TrueNAS-beheerpagina; presenteer een hostactie niet als gewone UI-functie als die niet daadwerkelijk kan. Installatie en fysieke hardwareconfiguratie mogen wel eenmalige TrueNAS-beheerhandelingen vereisen.

### Q2.1 Verplichte hoofdmenu's en bedienbare acties

| Scherm | Acties die via UI verplicht moeten werken |
|---|---|
| **Dashboard** | Gezondheid van alle interne services, volledige media-/missing-/optimalisatiestatus, start/pauze/noodstop, alerts, voortgang en activiteit |
| **Series** | Nieuwe serie toevoegen, bestaande beheren, seizoen-/episode-monitoring, toekomstige seizoenen, profielen, indexer-/root-keuze, zoekacties, mapping en uitzonderingen |
| **Films** | Zoeken, toevoegen, monitoren, edities/profielen beheren, upgrades, handmatige zoekacties, import/herstel |
| **Ontbrekende afleveringen** | Per aflevering oorzaak, afwijzingsregels, retries, backfillplanning, quota, prioriteit, zoek nu, voortgang en uitsluitingen |
| **Anime** | AniDB/TheXEM/Shoko koppelen, nummering corrigeren, cour/seizoen, OVAs, dual/English/Japanese/Dutch policies, audio- en ondertitelinstellingen |
| **Bibliotheken** | Bronnen toevoegen, rechten testen, mappen koppelen, read-only inventarisatie, optimalisatiepreview, per map aan/uit, schijfgebruik, duplicates en hardlinks |
| **Migratiewizard** | Oude Sonarr/Radarr/AV1 configs en bibliotheek importeren, mapping wijzigen, vergelijkingsrapport bekijken, shadow mode, canary, cutover, rollback starten |
| **Downloads & Indexers** | Bestaande Sonarr/Radarr-forkconfig via gecentraliseerde UI: indexer/Prowlarr, downloadclients, categorieën, RSS, limieten, path mappings en zoekdiagnose |
| **Seerr** | Verbindingsinstellingen, API-validatie, verzoekentests, compatibiliteit, root/profile mappings en migratie van oude endpoints |
| **Kwaliteit & TRaSH** | Kwaliteitsprofielen, scores, vergelijking per release, afwijsredenen, 4K/1080p-profielen, TRaSH-import/sync en policy-simulatie |
| **AV1 Studio** | Content analysis, CPU/GPU-keuze, queuebeheer, profielen/CRF/CQ/bitrate, trial encodes, kwaliteitsmetingen, samplevergelijking, streambehoud en converteeracties |
| **Dolby Vision & HDR** | DV-schakelaars en modi globaal/per profiel/per bestand, ondersteunde profielen, testencode, metadata, bevestigde afspeelketens, HDR10/HDR10+ beleid |
| **Audio & ondertiteling** | Nederlands/Engels/Japans, voorkeuren en vereisten, default tracks, dub/dual beleid, forced/signs&songs, audio-preservation, ondertitelbeleid |
| **Wachtrij & Workers** | Prioriteiten, CPU/GPU-threads, temperaturen, pauzevensters, concurrentie, retries, limieten, logs, taakdetails, veilig annuleren |
| **Quarantaine & herstel** | Bestanden inspecteren, herstelpunten, retentie, terugzetten, transaction states, opruimplanning en incidentverklaringen |
| **App- en systeeminstellingen** | Gebruikers/rechten, authenticatie, secrets, netwerk/LAN, waarschuwingen, taal/uiterlijk, logging, health, opslaglimieten, onderhoud en back-ups |
| **Updates & diagnostics** | Versie- en upstreamstatus, changelog, veilige updates met back-up, database-migratie, rollback, integratie-/GPU-tests, supportbundel zonder geheimen |

### Q2.2 UI-configuratierichtlijnen

- Instellingen zijn **gevalideerde formulieren** en worden alleen opgeslagen nadat een echte API-mutatie slaagt. Onjuiste instellingen geven directe fout- en herstelhint.
- Elke instelling toont naam, uitleg, huidige waarde, overervingsniveau, standaardwaarde, effect op bestaande taken, voorbeeldbeslissing en knop `Herstel standaard`.
- Geen dode knoppen, nepstatus, mock-instellingen of verborgen configure-only-via-CLI-schakelaars. Feitelijke backendcapability bepaalt enabled/disabled.
- Geavanceerde instellingen moeten wel beschikbaar zijn via `Geavanceerd`; normale schermen blijven overzichtelijk. `Zoeken in instellingen` zoekt over alle opties, ook DV/HDR/gpu/indexer/path mapping.
- Uitleg van risico's is contextueel, niet een niet-uitschakelbare generieke waarschuwing over een reeds gekozen DV-testscenario.
- Elke gevaarlijke operatie: actuele impact aantallen/GB, geïdentificeerde dataset, snapshots/backupstatus, rollbackvoorwaarde en UI-bevestiging (bij massadestructieve acties afzonderlijke expliciete bevestiging).
- Maak UI voor `Bulk optimaliseren`: gebruiker kiest hele bibliotheek, serie, seizoen of filters; ziet dry-run, verdeling `behouden / vervangen / zoeken / AV1 / onzeker / overslaan`, resourceprognose en start/pauze/voortzetting.
- Maak alle acties en fouten zichtbaar in een audit-tijdlijn met `waarom`, `wanneer`, `door wie`, `oude waarde`, `nieuwe waarde`, `undo/herstel` waar mogelijk.
- Opslagveranderingen en mountrechten moeten **niet** worden opgelost door container root/privileged mode te vragen; gebruik waar mogelijk vooraf ingerichte TrueNAS-datasets, gecontroleerde host-path-mappings en een bevoegde beperkte helper.
- Responsive desktop/tablet/mobiel en volledig Nederlandstalige UI, Engels selecteerbaar; geen verwijzing naar een aparte Sonarr/Radarr-webinterface als verplichte beheerroute. De originele fork-UI kan als diagnostische fallback blijven bestaan.
- `MediaGuard` blijft één login, één consistente navigatie, één dashboard en één TrueNAS App entry, hoewel er intern meerdere containers bestaan.

### Q2.3 UI-first architectuurcontract

Voor elk instelbaar backendveld bestaat: (1) een getypeerde configdefinitie met toegestane waarden, (2) een authenticatie-/autorisatiebeveiligd API-endpoint, (3) een concrete UI-control met read/write-binding, (4) validatie, (5) auditlog, (6) test voor weergave, wijziging, herstartbestendigheid en foutstatus. Maak hiervoor `docs/UI_CONTROL_COVERAGE_MATRIX.md`. Geen release als een productfunctie operationeel alleen via YAML, SQL of CLI instelbaar is (behalve hostinrichting).

Technische API's die ten minste nodig zijn:

```text
GET    /api/v1/settings/schema
GET    /api/v1/settings/effective?scope=...
PATCH  /api/v1/settings/scoped
POST   /api/v1/settings/simulate
GET    /api/v1/dolby-vision/capabilities
GET    /api/v1/dolby-vision/policies
PATCH  /api/v1/dolby-vision/policies
POST   /api/v1/dolby-vision/test-encode
GET    /api/v1/dolby-vision/tests/{id}
POST   /api/v1/dolby-vision/playback-confirmations
POST   /api/v1/dolby-vision/activate/{job_id}
POST   /api/v1/dolby-vision/restore/{job_id}
GET    /api/v1/admin/coverage
GET    /api/v1/admin/health
POST   /api/v1/admin/diagnostics/{component}
```

API's zijn voorstellen voor MediaGuard, niet reeds bestaande Sonarr/Radarr- of AV1-endpoints. Implementeer alleen werkende, geteste acties.

### Q2.4 Migratie van bestaande AV1 UI en oude fork-bediening

- Neem de huidige AV1-Next.js-UI als basis. Migreer bestaande dashboards, profielen, worker controls, scanhistorie, AI-canary en instellingen zonder functionele regressie.
- Maak een **volledige interface-audit** van huidige AV1 API-endpoints, alle voorgeschreven settings en de Sonarr/Radarr-forks. Elimineer bestaande CLI-only reguliere beheerfuncties door er UI-paden voor te bouwen.
- Voor Sonarr/Radarr geldt: de UI dekt werkelijk alle normale gebruiksfuncties die nodig zijn voor dagelijkse bediening. Uitgebreide zeldzame upstreamsettings komen in een `Geavanceerd` scherm via een geteste compatibele backendadapter. Geen optionele noodzaak om rechtstreeks Sonarr/Radarr te openen voor indexers, downloaders of series/filmbeheer.
- Overweeg een iframe **niet** als hoofdoplossing: een native uniforme UI met veilige API-adapters heeft voorrang; upstream-UI mag afzonderlijke nood-/diagnostische toegang zijn.
- Stel een UI-designsystem op: bestaande Figma-referentie, Nederlandstalige microcopy, dark/light, statusstaten en keyboard-controls. Test met Playwright bij 360/768/1280/1440px.

## Q3. Aanpassing van veiligheids- en testbeleid

De eerdere algemene regel **`DV default SKIP tot publieke device-matrix groen is` vervalt** als vereiste. Hij wordt vervangen door **`DV-conversie alleen op door gebruiker gekozen, technisch ondersteunde profielroute; metadata- en herstelverificatie zijn verplicht`**.

De gebruiker mag de DV-waarschuwing en ondersteuningsstatus beheren. De app mag dus niet altijd overslaan enkel omdat het type bestand DV bevat. Dat is een productbug als een door de gebruiker ingeschakelde DV-route technisch beschikbaar is en een proefencode veilig kan worden gemaakt.

Tegelijk worden onveranderlijke algemene controles behouden: input/file gewijzigd? stop; metadata verloren? geen activatie; foutieve audio/subs? geen activatie; niet-verifieerbare conversie? bron behouden; geen veilige recovery? geen Full Auto cutover. **Schakelbaar is het experimentele DV-beleid, niet de gegevensintegriteit.**

Verplichte extra acceptatietests:

1. DV8.1 met toggle UIT → `Origineel behouden`.
2. DV8.1 met toggle AAN en geschikte FFmpeg → proefencode uitgevoerd, resultaat/mux/RPU gevalideerd, origineel ongemoeid.
3. DV8.1 met gebruikersbevestiging en bewezen recovery → Full Auto eligible na canary zonder verplichte externe compatibiliteitsmatrix.
4. DV7 MEL → geteste profielroute en timing/audio/subtitle-verificatie.
5. DV7 FEL → geen stilzwijgende FEL-downgrade; aparte testoutput plus originele bron bewaard.
6. DV5 → geen ongedocumenteerde force-transcode; verklaarde UI-blokkade bij ontbrekende bewezen route.
7. DV-uitvoer zonder RPU/gewijzigde basis/kleurwaarden → verificatiefout en bron behouden.
8. DV-UI-schakelaar wijzigen → persistente policy, audit en juiste backendjob, ook na restart.
9. DV-schakelaar per serie overschrijft globaal, overeenkomstig zichtbaar overervingsmodel.
10. Afspeelchecklist bevestigd → de gebruiker kan waarschuwingen beheren, zonder integriteitstests te omzeilen.
11. Iedere normale instelling uit `UI_CONTROL_COVERAGE_MATRIX.md` is vanaf frontend te lezen, te wijzigen en te testen.
12. De UI kan Sonarr/Radarr-series volgen, indexers beheren, Seerr-verbinding testen, backfill starten, een hele bibliotheek dry-run optimaliseren en een terugdraai-actie uitvoeren zonder SSH.
13. Browserreload, backendrestart en TrueNAS-reboot verliezen geen ingestelde policies of taakstatus.
14. UI API-fouten leiden tot zichtbare foutmelding en behouden bestaande configuratie (geen nepgeslaagde wijziging).
15. Minstens 6 interactieve gebruikersflows (DV enable & test, library optimize, series monitoring, missing recovery, AV1 profile, rollback) worden met echte UI/E2E-tests en screenshots gedekt.

## Q4. Nieuwe uitvoeringsprioriteit voor Claude Code

1. Lees bestaande AV1-repo en v3-masterplan; pin commit. Maak baseline-tests. Inventariseer ALLE configvelden en functies zonder UI-beheer.
2. Maak de **UI-control coverage matrix** en vertaal DV-overrides naar getypeerde beleidsregels met scope-overerving, API-CRUD en audit.
3. Bouw de echte DV-schakelaar en aparte proefencodeflow eerst op testmedia; bewijs een actieve DV-route in plaats van de oude skip-default.
4. Integreer beheer van Sonarr/Radarr-forks achter centrale API, inclusief indexers, downloadclients, series/films, toekomstige episodes en missing recovery.
5. Implementeer alle UI-beheerschermen en bijbehorende E2E-tests voordat het product als volledig bruikbaar geldt.
6. Valideer DV-metadata/streambehoud op de werkelijke TrueNAS-toolchain; toon welke clienttests door gebruiker bevestigd zijn en welke nog onbekend zijn.
7. De enige vereiste hosthandelingen buiten UI zijn de eenmalige, technisch noodzakelijke TrueNAS-provisioning of expliciet geprivilegieerde hostacties. Geef hiervoor een begeleide setup met duidelijk concrete stappen.

## Q5. Historische v3 Claude Code-startopdracht — vervangen door S6 in v4

> `/goal Bouw MediaGuard Ultimate volledig volgens MEDIAGUARD_GOAL.md. Gebruik https://github.com/lvos04/av1 als bestaande AV1-engine/Next.js-basis, fork Sonarr en Radarr, en behoud Seerr. DEEL Q van het plan heeft voorrang: de gebruiker heeft Dolby Vision-afspelen eerder werkend gehad en wil DV via schakelaars per library, profiel en video activeren. Maak ondersteunde DV-conversies en geïsoleerde proefencodes daadwerkelijk mogelijk zonder verplichte universele playback-matrix, maar behoud metadata-verificatie en herstelbaarheid. Maak ALLE operationele instellingen en beheerfuncties (series, films, backfill, indexers, Seerr, bibliotheken, kwaliteit, AV1, DV, hardware, quarantaine, updates) volledig bedienbaar vanuit het Nederlandstalige webdashboard, zonder benodigde SSH/YAML na installatie. Importeer de bestaande bibliotheek read-only, optimaliseer veilig en bewijs dat nieuwe afleveringen en ontbrekende media worden gevonden nadat de oude installaties zijn uitgezet. Werk autonoom per fase met echte tests, E2E-UI-tests en herstelproeven. Schakel de oude installaties niet zelfstandig uit vóór expliciete cutover.`

---

**Historische definitie van v3-afronding (v4 voegt R/S-gates toe):** een gebruiker kan de gewenste DV-conversieroute in de UI aanzetten en op een veilige kopie testen, de media-/kwaliteitsbeleidssystemen en alle dagelijkse Sonarr/Radarr/AV1-taken volledig in één webinterface bedienen, en het functioneren van die bediening aantonen met echte automatische tests. Alleen technische, onvervangbare integriteits-/herstelbeperkingen blijven harde grenzen.

---

# DEEL R — VERPLICHTE V4-AANVULLING: VIER GESCHEIDEN SONARR/RADARR-INSTANTIES

**Dit deel is leidend boven eerdere verwijzingen naar één Sonarr- of één Radarr-proces.** Het maakt de bestaande gescheiden indeling van de gebruiker tot de standaardarchitectuur. De gebruiker mag het huidige systeem pas uitzetten wanneer alle vier doelinstanties inclusief monitoring en Seerr aantoonbaar werken.

## R1. Standaardinstantie-topologie

Er zijn slechts **twee onderhouden forkcodebases en twee te bouwen containerimages**: één Sonarr en één Radarr. De installatie start vier **afzonderlijke runtime-instanties** met eigen databases, configuraties, wachtrijen en media-scopes:

| Instance-ID | Applicatie | Bibliotheek | Standaardtaal/kwaliteit | Gescheiden gegevens |
|---|---|---|---|---|
| `sonarr-series` | Sonarr-fork | Normale series | 4K waar beschikbaar; reguliere audiovoorkeuren | Config, app-DB, metadata, tags, rootfolders, downloadcategorie |
| `sonarr-anime` | Sonarr-fork | Anime-series, specials/OVA | Dual Audio > Engels > Japans; correcte anime-nummering | Eigen config, app-DB, anime-indexers, mappings, rootfolders, categorie |
| `radarr-films` | Radarr-fork | Reguliere films | 4K waar zinvol, HDR/DV, NL/EN audio/subs | Eigen config, app-DB, rootfolders, categorie |
| `radarr-animefilms` | Radarr-fork | Animefilms | Dual Audio > Engels > Japans; anime releasegroepen | Eigen config, app-DB, rootfolders, categorie |

Standaardwaarden worden in de installatiewizard bevestigd; **nooit de huidige scheiding automatisch samenvoegen**. Via de UI kan men extra instanties aanmaken (bijvoorbeeld 4K apart), pauzeren, hernoemen, migreren, archiveren of deactiveren, mits de bijbehorende datamigratie en ownership-check veilig zijn. Een instantie verwijderen mag nooit de mediabestanden verwijderen.

De UI heeft één overzicht **Instellingen → Instanties** met status, versie/commit, API-URL, interne hostnaam, dataset, geselecteerde indexers, downloadcategorieën, rootfolder, aantal items, toekomstige monitoring, Seerr-koppeling en health. Instantiebeheer mag geen YAML-, poort- of handmatige DB-bewerking vereisen. Nieuwe instanties worden bij voorkeur op basis van dezelfde gepinde image gestart, met een unieke instantie-ID en persistent volume.

## R2. Scheiding, routering en single ownership

1. Alle vier instanties hebben verschillende `/config`-volumes en eigen instance-namespaced interne poorten. Externe toegang via stabiele gateway-routes of afzonderlijke stabiele host:poort-mappings; **Seerr ziet een normale compatibele Sonarr/Radarr-API per instantie**. Een gateway vertaalt niet zomaar API-responses; behoud de contracten. Test de daadwerkelijk vereiste request-methods en schema's.
2. Configureer afzonderlijke downloadclientcategorieën (`tv`, `tv-anime`, `movies`, `movies-anime`) als voorstel, maar migreer bestaande categorieën en trackers waar mogelijk. Eén download mag niet door meerdere instanties worden opgeëist.
3. Maak een persistente `instance_ownership`-registratie: `provider_id`, canonical external title ID, `(media_type, edition, target_profile)`, gekozen instance-ID en root-path, plus expliciete ownership-overrides. Een film of serie kan bewust in meerdere kwaliteitsbibliotheken bestaan, maar niet per ongeluk als dubbele taak in dezelfde doelbibliotheek.
4. Sonarr/Radarr-native ID's zijn **alleen uniek binnen hun eigen instantie**. Iedere MediaGuard-entiteit wordt daarom gesleuteld met `(instance_id, provider_item_id)` en daarnaast de canonieke tv-/film-/anime-ID. Alle events, jobs, locks, API-calls, media-mappings en auditlogs dragen `instance_id` expliciet mee.
5. Centrale MediaGuard-policy kan een globale default geven, maar profielen erven op scope: `globaal → media-type → instantie → serie/film → seizoen/episode/bestand`. Overrides en de effectieve uitkomst zijn in de UI zichtbaar.
6. De AV1-worker mag alleen converteren als ownership, identiteit, file-lock en de importstatus van de betreffende instance eenduidig zijn. Een nieuwe Sonarr-import kan een lopende encode veilig uitstellen/annuleren zonder dat een ander bestand op hetzelfde pad wordt vervangen.
7. Ingebouwde Sonarr/Radarr-processen blijven zelfstandig hun RSS-, import- en monitoringfunctionaliteit leveren. MediaGuard voegt optionele pre-grab/pre-import-policies toe, maar mag niet door een AI-fout of backenduitval de hele standaardbibliotheek onbestuurbaar maken. Definieer per hook veilige timeout-/degradatieregels, met duidelijke incidentmelding.

## R3. Migratiewizard van de vier bestaande productiesystemen

De wizard detecteert de huidige instanties en toont een mappingmatrix **broninstantie → doelinstantie**. Koppel niet blind op titel; controleer Sonarr versus Radarr, profile, rootfolder en anime-type. Migreer afzonderlijk:

- volledige serie-/filmcollectie, monitored flags op alle niveaus, future seasons, download/wanted-status, tags, metadata, quality/custom formats, taal- en upgradeprofielen;
- indexers en Prowlarr-integraties per instantie, tracker/categorie-mappings, downloadclients, seeds/hardlinks en downloadhistorie;
- bestaande API-URL's, Seerr-serverselectie, standaardservers, rootfolder/profile presets en huidig request→server gedrag;
- padmappings en ACL's zonder massale rename of datawijziging; dubbele filmbestanden / animefilms worden per doelinstantiescope geïdentificeerd;
- AV1-bron-/resultaatgeschiedenis, verzamelingen onder actieve encoding en bronherkomst wanneer beschikbaar.

Werk in deze volgorde: **read-only inventory → backups met testrestore → mappingpreview → per-instantie shadow import → counts en statusvergelijking → Seerr contracttests → cutover per instantie of gecontroleerde groepscutover**. Laat oude instanties ongewijzigd zolang de nieuwe productie-adapters niet bewezen zijn. Voorkom tijdens parallelle operatie dat bron- en doelinstanties tegelijk RSS/downloads/imports uitvoeren op dezelfde bibliotheek. De wizard legt een freeze/ownership-overdracht vast, heeft idempotente herstartbare stappen en een rollbackroute.

Een migratie is **niet** succesvol als series of films wel aanwezig zijn maar hun toekomstige afleveringen, gemonitorde seizoenen, indexers, afwijzingsredenen of downloadprofielen verloren zijn gegaan. Toon pre-/post-vergelijking per instantie en alle mismatches.

## R4. Seerr en missing backfill over vier instanties

- Seerr kent exact de vier standaard-API-endpoints; anime-series gaan naar `sonarr-anime`, reguliere series naar `sonarr-series`, animefilms naar `radarr-animefilms`, reguliere films naar `radarr-films`, tenzij een bestaande Seerr-keuze/override anders voorschrijft. **Niet aannemen dat Seerr zelf altijd betrouwbaar herkent of iets anime is**: gebruik bestaande serverselectie, mappingregels en bevestig de route vóór toevoegen.
- Wanneer Seerr meerdere servers aanbiedt, behouden we de oorspronkelijke gebruikersinstellingen en stellen we nieuwe defaults voor in de wizard; Seerr wordt bij voorkeur alleen via zijn ondersteunde interface gewijzigd, anders stappenplan via UI in plaats van directe ongecontroleerde DB-mutaties.
- Missing-episode diagnose/backfill draait **per instantie** met zijn eigen zoekquota, providers en profiel. De dashboardcijfers worden centraal geaggregeerd, met aparte filters voor normale series/anime en titel, seizoen, reden en laatste zoekpoging.
- Nieuwe afleveringen en nieuwe anime-cours/seizoenen blijven gemonitord volgens geïmporteerd beleid. Geen zoeklus, geen dubbele grabs tussen scopes, en geen stilzwijgend markeren van bewust ongemoni­torde episodes als 'missing'.

## R5. UI-, API- en data-uitbreiding

Verplichte UI-elementen: instantiekaart met `Gezond/Degraded/Offline`, **Bron vs doel-mapping**, rootfolder-/profielscherm per instantie, Seerr-routetest, duplicaatwaarschuwing, backfill per instantie, logfilter `instance_id`, en één centraal overzicht met filters/overerving.

Nieuwe MediaGuard API-routes (ontwerpcontracten, niet upstream bestaande routes):

```text
GET    /api/v1/instances
POST   /api/v1/instances
GET    /api/v1/instances/{instance_id}
PATCH  /api/v1/instances/{instance_id}
POST   /api/v1/instances/{instance_id}/test
POST   /api/v1/instances/{instance_id}/suspend
POST   /api/v1/instances/{instance_id}/resume
GET    /api/v1/instances/{instance_id}/library
GET    /api/v1/instances/{instance_id}/wanted
POST   /api/v1/instances/{instance_id}/backfill
GET    /api/v1/ownership/conflicts
POST   /api/v1/ownership/simulate
POST   /api/v1/migration/instance-mapping/preview
POST   /api/v1/migration/instance-mapping/verify
POST   /api/v1/seerr/route-test
```

Schema's minimaal: `instances`, `instance_profiles`, `instance_ownership`, `instance_root_mappings`, `instance_indexer_assignments`, `instance_seerr_routing`, `instance_cutover_state`, `instance_migration_checkpoints`. Geheimen worden in secret storage bewaard met beperkte toegang.

## R6. Verplichte acceptatietests

1. Start vier onafhankelijke applicatieprocessen met slechts twee forkimages; test verschillende databases en configvolumes.
2. Configureer twee Sonarr-instanties met dezelfde serietitel maar verschillende doelprofielen: geen ongewenste dubbele import of cross-instance mutatie.
3. Migreer vier broninstanties; aantallen, monitoringbeleid, future-season flags, tags, custom formats, indexers, rootfolders en downloadcategorieën zijn gelijk aan de bron (of expliciet als afwijking bevestigd).
4. Simuleer een nieuwe gewone aflevering én een nieuwe anime-aflevering; elk wordt door de juiste instantie gemonitord, gevonden, geïmporteerd en gevalideerd.
5. Laat twee ontbrekende afleveringen via afzonderlijke backfillqueues zoeken; behoud indexerquota en dedup-per-scope.
6. Routeer een Seerr-verzoek voor gewone serie, anime-serie, gewone film en animefilm naar de bedoelde backend; verifieer statusupdates en beschikbaremedia-synchronisatie.
7. Pause/resume van een instantie heeft geen effect op de andere drie. Recovery van één app-database wijzigt geen andere instantie.
8. Eén instance-wisseling van de gebruiker toont de juiste configuratie, zoekresultaten, logs en files zonder data leakage tussen scopes.
9. Cutover van bron naar nieuwe doelinstantie houdt de oorspronkelijke installatie herstelbaar en vermijdt dubbele RSS/downloads.
10. UI-only instantiemanagement, per-instantie regels en Seerr-routecontrole slagen in browser-E2E-tests.

---

# DEEL S — VERPLICHTE V4-AANVULLING: OPTIONELE AI DECISION ADVISOR

**Doel:** gebruik Claude API of OpenAI API uitsluitend wanneer een model een *aantoonbaar beter diagnose- of interpretatievoorstel* kan geven dan bestaande deterministische programmaregels. De app werkt 100% zonder AI of API-credits. AI is geen onderdeel van een blokkadevrije dagelijkse releaseflow.

## S1. Waar AI wel of juist niet waardevol is

| Gebruikssituatie | AI-toepassing | Standaard |
|---|---|---|
| Onduidelijke anime-episode bij tegenstrijdige nummeringen en aliassen | Ordenen van beschikbare bewijzen, voorstel voor mappings; benoem ontbrekend bewijs | Alleen bij ambiguïteit |
| Hardnekkig ontbrekende aflevering (indexer/quality/taal/config) | Root-cause synthese en voorstel tot gerichte vervolgactie | Alleen na deterministische diagnose zonder oplossing |
| Tegenstrijdige releaseclaims, taal-/groepmetadata of uitzonderlijke specials | Samenvatting en gerangschikte verklaringen; géén definitief bewijs | Alleen indien nodig |
| AV1-presets bij lastig materiaal | Interpretatie van reeds gemeten VMAF/SSIM, trial-encode- en resourcecijfers; suggestie binnen veilige grenzen | Optioneel na benchmarks |
| Gewone release-ranking, bekende hashmatches, streaminspectie | **Geen LLM**: deterministische engine beslist | Nooit standaard |
| Detectie van gesproken taal in audio | Gebruik gespecialiseerde lokale taalherkenning, niet generieke tekst-LLM | Lokaal indien nodig |
| Beslissen over definitieve verwijdering, DV-metadata-validatie, ZFS-herstel | **Geen LLM-autoriteit**: harde code + bewijs + recovery | Nooit |

Een LLM kan geen onbekende videobestandidentiteit bewijzen door alleen een titel of beschrijving te lezen. Een modelresultaat is altijd een voorstel met bronnen/velden, en wordt nooit zelf de onafhankelijke ground truth.

## S2. API-providers, modelrouter en kostenbewaking

Bouw één provideronafhankelijke adapter met ten minste `AnthropicClaudeAdapter` en `OpenAIAdapter`; beide gebruiken de **officiële betaalde API** en gestructureerde resultaten waar ondersteund. **Een Claude-chatabonnement of ChatGPT-abonnement is niet automatisch een API-tegoed.** API-keys via UI-secretinput, at-rest bescherming, nooit client-side blootgeven. UI laat per provider kiezen: aan/uit, model-ID, kostenlimieten, doelcases en eventuele automatische routing.

Routingregelingen:

- `disabled` (default), `shadow`, `advisory`, `bounded_auto`.
- Alleen triggeren wanneer een deterministische classifier een expliciet `needs_advice`-reasoncode afgeeft.
- Eerst lokale regels + databronnen raadplegen; dezelfde diagnostische casus cachen om dubbele API-kosten te voorkomen.
- Instelbare maandelijkse kostenlimiet, kostenplafond per analyse, maximaal calls per dag, max token-/antwoordgrootte, time-outs en retrybudget. Begin met een **instelbare standaardlimiet van €5 per maand**, geen ongecontroleerde facturatie. Prijzen op actuele providerpricing baseren; geen hardcoded historische tokenprijzen.
- Modellen kiezen op taaktype, met voorkeur voor de goedkoopste provider die bij regressietests voldoende kwaliteit toont. Geen onbewezen automatische 'beste model'-claim. Externe API-storingen stoppen AI maar blokkeren nooit gewone monitoring, downloads of bestandstoegang.
- Alle daadwerkelijke kosten en tokengebruik per provider, taak, dag en maand meten waar providergegevens beschikbaar zijn, met conservatieve reservering vooraf om overschrijding te beperken. Tonen van schattingen waar facturatiegegevens niet beschikbaar zijn.
- Maandreset volgens ingestelde tijdzone; geen onverwacht vervallen budgets na een reboot.

## S3. AI-bevoegdheden en verplichte schema's

Model input: compacte getypeerde metadata: canonieke titels, episode mappings, bronverwijzingen, gebruikte indexers (zonder tokens), releaseeigenschappen, rejection reasoncodes, encode-trial metrics en niet-gevoelige logs. Stuur **geen videobestanden, frames, audio, API-sleutels, absolute privépaden of Sonarr/Radarr-configdump** naar een cloud-LLM. De UI toont een exact voorbeeld van gegevens die verzonden zouden worden. Opt-in vereist vóór elke externe overdracht. Een lokale provider kan later worden toegevoegd.

Model output via strikt gevalideerde schema's:

```json
{
  "case_id": "opaque-id",
  "task_type": "anime_identity|missing_diagnosis|release_explanation|encode_advice",
  "suggested_action": "inspect_mapping|retry_later|adjust_filter|request_search|compare_trials|manual_review",
  "reason_codes": ["CONFLICTING_EPISODE_MAP"],
  "evidence_ids": ["local-evidence-uuid"],
  "reasoning_summary": "Korte controleerbare uitleg, geen vrije opdrachten",
  "uncertainty": "high|medium|low",
  "requires_manual_review": true
}
```

- Onbekende reason/actioncodes afwijzen; evidence-ID's moeten daadwerkelijk lokaal bestaan.
- Alle voorstellen door de lokale **PolicyGuard**; hij bepaalt of iets veilig is, niet het taalmodel.
- **`shadow`:** modeluitkomst alleen loggen en vergelijken; geen workflowwijziging.
- **`advisory`:** toon voorstel met bewijs en actieknop in de UI; gebruiker kan goedkeuren/afwijzen.
- **`bounded_auto`:** alleen exact allowlisted, omkeerbare en door de policy reeds toegestane niet-destructieve acties uitvoeren (bijvoorbeeld extra metadata ophalen of een voorzichtige, rate-limited zoekpoging) en uitsluitend na een geslaagde schaduw-/canaryfase. Dezelfde actie moet zonder AI veilig toegestaan zijn. Geen wijziging aan hard blockers, release-identity verdict, verplichte taal, HDR/DV-preservation, AV1 integriteitscriteria, snapshots, verwijderingen of cutover op basis van een AI-advies.
- AI heeft nooit toegang tot shell, hosttools, filesystem, mounts, netwerkclients of de onderliggende forks met schrijfrechten; alle mutaties volgen de vaste geautoriseerde MediaGuard API.
- Gebruik externe releasebeschrijvingen en logs uitsluitend als **data**; bescherm tegen prompt-injection en ongewenste modelcommando's.

## S4. UI-first AI Decision Advisor

Nieuw scherm **AI Adviseur** plus compact paneel in de detailpagina van een episode en bij ontbrekende media. Nederlandse UI-instellingen:

- Provider: **Claude**, **OpenAI**, **Uit**; modelselectie op live controle van beschikbare providerconfiguratie.
- Beleid: Uit/Schaduw/Advies/Beperkt automatisch; standaard **Uit** totdat de gebruiker API-verzending inschakelt.
- Maandbudget, limiet per analyse en aantal analyses per dag, budgetverbruik en besparende cachehits.
- Opt-in voor externe metadataoverdracht, preview van verzonden velden, dataminimalisatie en eventuele bewaartermijn.
- Uitleg `Waarom werd AI ingezet?`, model, geraadpleegde evidence IDs, modelvoorstel, lokale policy-uitkomst, eventuele actie, confidence/uncertainty en werkelijke of geschatte kosten.
- Eén klik `Accepteer`, `Wijs af`, `Markeer onjuist`, `Opnieuw onderzoeken`, plus optionele feedback om detectie- en regressieregels te verbeteren. Geen zelfstandig lerend destructive policy.
- AI kan alleen ingeschakeld worden vanuit de UI met een duidelijke testverbinding en echte gestructureerde antwoordtest; geen config edit nodig.

Nieuwe routes (MediaGuard API, geen bestaande provider- of upstreamroutes):

```text
GET    /api/v1/ai/providers
POST   /api/v1/ai/providers/{provider}/test
GET    /api/v1/ai/settings
PATCH  /api/v1/ai/settings
GET    /api/v1/ai/usage
GET    /api/v1/ai/cases
GET    /api/v1/ai/cases/{case_id}
POST   /api/v1/ai/cases/{case_id}/analyze
POST   /api/v1/ai/cases/{case_id}/feedback
POST   /api/v1/ai/cases/{case_id}/approve
POST   /api/v1/ai/cases/{case_id}/reject
```

Database minimaal: `ai_provider_configs`, `ai_cases`, `ai_proposals`, `ai_policy_results`, `ai_usage`, `ai_model_evaluations`, `ai_feedback`, `ai_budgets`, `ai_consent_events`. Bewaar geen ruwe volledige prompts met secrets of gevoelige paden.

## S5. Test-, validatie- en productiegates

1. Zonder API-key werkt alles behalve AI-advies volledig; AI blijft uit en geeft begrijpelijke UI-status.
2. Een normale, ondubbelzinnige aflevering activeert **geen AI-call**.
3. Bij ambigue anime-identiteit ontstaat een schema-gevalideerd advies maar geen ongevraagd bindend identity-verdict.
4. Bij herhaaldelijk ontbrekende afleveringen kan het model de deterministische foutcategorieën uitleggen; onbestaande evidence-ID's worden afgewezen.
5. Malafide releasenaam, externe beschrijving of indexerrespons kan nooit toolcalls, API-keys of policywijzigingen veroorzaken (prompt-injection test).
6. `shadow` en `advisory` wijzigen geen mediabestanden, downloads of forksettings.
7. `bounded_auto` mag uitsluitend de expliciete niet-destructieve, idempotente allowlist raken; weigering/timeout voert niets uit.
8. Token- en kostenlimieten blijven behouden na reboot en er ontstaan geen ongecontroleerde retries.
9. API-providerstoring veroorzaakt nooit een download- of scanstop voor Sonarr/Radarr/MediaGuard.
10. E2E-UI-tests voor providerconfig, modelkeuze, privacytoestemming, adviesweergave, feedback, uitschakeling en budgetlimiet.
11. Realistische benchmark met gelabelde ambiguë anime- en missing-gevallen toont dat AI-aanbevelingen daadwerkelijk nuttig zijn; bij geen meetbare verbetering blijft `bounded_auto` uit en wordt de feature alleen als optionele uitleg/adviseur aangeboden.
12. AV1-aanbevelingen worden altijd getest met echte korte proefencodes en vergeleken tegen baseline; geen modeladvies mag een bewezen slechter encodeprofiel activeren.

## S6. Nieuwe v4-uitvoeringsvolgorde en vervangende Claude Code-opdracht

**Prioriteit en volgorde:**

1. Baseline van bestaande `lvos04/av1`-code + tests; Sonarr/Radarr forks clonen/pinnen; TrueNAS-target detecteren.
2. Bouw/installeer vier gescheiden instances uit twee images en de ene UI/gateway; API/Seerr-contracttests.
3. Migreer vier huidige Sonarr/Radarr-bronnen read-only via een instance-mappingwizard; ownership- en monitorstatusverificatie; geen productiecutover.
4. Implementeer wanted/backfill, toekomstmonitoring, anime-identificatie, taal/kwaliteit en een gedeelde policy-engine.
5. Integreer de bestaande AV1-engine, content-aware trial-encode, HDR/DV-behoud, Quarantaine/ZFS-recovery en UI-beheer van alle instellingen.
6. **Pas na basisfunctionerende deterministische flows**: bouw optionele AI-advisor, begin met schaduwmodus, stuur alleen metadata met opt-in en voeg meetbare benchmarkgates toe.
7. Valideer Full Auto inclusief herstel en crashtests; valideer elke instance afzonderlijk en Seerr van aanvraag tot import.
8. TrueNAS-productie-installatie, dagelijkse UI-werkstromen en cutoverwizard; oude Sonarr/Radarr blijven onaangeroerd tot expliciete toestemming en bewezen pariteit.

**Dit deel is leidend boven Q5 en eerdere startopdrachten.**

> `/goal Bouw MediaGuard Ultimate volledig volgens MEDIAGUARD_GOAL.md. DEEL R en DEEL S zijn leidend. Gebruik de bestaande code uit https://github.com/lvos04/av1 en onderhoud Sonarr/Radarr als twee gepinde forks met vier runtime-instanties: Sonarr Series, Sonarr Anime, Radarr Films en Radarr Animefilms. Maak één Nederlandse UI waarin echt alle normale settings en acties werken. Migreer de vier bestaande broninstanties met behoud van future-season monitoring, indexers, Seerr-routes, bibliotheek, missing-episode backfill en quality profiles. Gebruik slimme AV1-conversie met gebruikersgestuurde Dolby Vision-toggle en onveranderlijke metadata-/recoverycontroles. Integreer een optionele, budgetbegrensde AI Decision Advisor met Claude API en OpenAI API voor alleen ambiguë cases; nooit als autoriteit over destructieve acties. Implementeer en test het hele systeem in werkende verticale stappen, inclusief TrueNAS Compose, Seerr-contracttests, browser-E2E en rollback. Schakel geen enkele oude productie-instantie uit vóór expliciete cutover.`

**Releasecriteria v4:** alle vier de instantiemigraties bewezen, toekomstige afleveringen en backfill werken, Seerr kan elke bedoelde instantie aanspreken, het AV1/HDR/DV-pad heeft echte conversie- en metadataregressietests, **alle normale beheeropties werken via UI**, de AI-advisor is optioneel en veilig en Full Auto kan niet zonder herstelverificatie massaal destructief handelen.

