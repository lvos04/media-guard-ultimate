# Architectuurbesluiten (ADR's)

## ADR-001 — Monorepo met gepinde forks als patchreeks
**Besluit:** Sonarr en Radarr worden niet als volledige kopie in deze repo opgenomen maar als
gepinde upstream-SHA + een kleine patchreeks (`forks/*/patches`), gebouwd met een
reproduceerbare Dockerfile. **Waarom:** kleinste onderhoudsoppervlak bij upstream-merges
(B2), GPL-3.0-broncode blijft volledig herleidbaar (upstream-SHA + patches), en er worden
geen GitHub-repo's in het account van de gebruiker aangemaakt zonder toestemming.
`forks/*/UPSTREAM.md` beschrijft hoe de patchreeks later als echte GitHub-fork wordt gepusht.

## ADR-002 — Stabiele v4/v3-API-lijn i.p.v. Sonarr v5
Seerr documenteert ondersteuning voor de Sonarr/Radarr v3-API (Sonarr v3/v4). De forks
starten daarom vanaf de stabiele branches (`main` resp. `master`), niet `develop`.

## ADR-003 — SQLite-WAL voor MediaGuard-domeindata (afwijking van 'PostgreSQL + Redis')
Het masterplan noemt PostgreSQL en Redis als *stackvoorkeur*. Besluit: SQLite in WAL-modus
met `synchronous=FULL`, één bestand per service, plus een persistente jobtabel en outbox.
**Waarom:** één gebruiker, één host; de bestaande AV1-engine gebruikt al SQLite-WAL (geen
big-bang-migratie, P2); back-up = consistente online SQLite-backup-API; twee containers
minder om te beveiligen, te updaten en te herstellen op TrueNAS. Alle kritieke status is
persistent (jobs, outbox, locks, journals). **Herzien wanneer:** meerdere schrijvende
processen op dezelfde DB nodig zijn of meetbare lock-contentie optreedt. De DB-laag
(`mediaguard/db.py`) is klein en kan dan worden vervangen.

## ADR-004 — Eén core-codebase, gescheiden containerrollen
`mediaguard-core` (API/orkestratie) en `mediaguard-recovery` (enige schrijver op
bibliotheekpaden) draaien uit dezelfde image met `MG_ROLE`. Encoder blijft de
`lvos04/av1`-backend in een eigen container met read-only bibliotheek. Zo blijft de
schrijfbevoegdheid minimaal (G2) zonder een tweede codebase te onderhouden.

## ADR-005 — Encoder vervangt niet zelf (handoff)
Binnen MediaGuard stopt de AV1-engine na verificatie bij `awaiting_commit`; de
recovery-worker zet eerst het origineel herstelbaar in quarantaine en activeert dan.
De oude directe vervanging blijft beschikbaar voor de losse Ubuntu-installatie (P2).

## ADR-006 — Webhooks via ingebouwde Connect-functie, hooks via kleine forkpatch
Events (grab/import/upgrade/…) gebruiken de standaard Webhook-connection (geen patch).
Alleen pre-grab en pre-import vereisen een forkpatch (decision specifications) met
timeout en fail policy (default fail-open voor productie, fail-closed in shadow).

## ADR-007 — Eén login via core; web proxyt
De Next.js-server proxyt `/api/v1/*` naar core; core is de enige authenticatie-autoriteit
(cookie + CSRF + RBAC). Interne services praten met `X-MediaGuard-Token`.

## ADR-008 — Migratie primair via native backup/restore, met API-verificatie
Native Sonarr/Radarr-back-ups bevatten de volledige configuratie (incl. indexer-credentials,
monitorvlaggen op elk niveau, history). Restore naar de doelinstantie gebeurt op een **kopie**
waarin vooraf RSS/automatisch zoeken en downloadclients zijn uitgeschakeld en de doelinstantie
start met `MEDIAGUARD_SHADOW_MODE=1`. Daarna verifieert MediaGuard via beide API's aantallen,
monitorstatus (serie/seizoen/aflevering), future-season-vlaggen, tags, profielen, indexers,
rootfolders en downloadcategorieën. Fallback bij versieverschil: API-gebaseerde import.

## ADR-009 — AI alleen adviserend
Zie DEEL S. Geen tool-/schrijfrechten, gestructureerde output, lokale PolicyGuard beslist.

## ADR-010 — Compose-indeling: één /data-dataset, sleutels uit .env, schrijfrechten per fase
**Besluit:** (1) Forks krijgen één mount `${MG_DATA_ROOT}:/data` met `media/{tv,anime,movies,
animefilms}` en `downloads/{tv,tv-anime,movies,movies-anime}` (TRaSH-indeling), zodat imports
via hardlink/atomische move gaan; core, encoder en recovery zien alleen `/data/media`
(`MG_LIBRARY_ROOTS=/data/media`). Scheiding tussen instanties gebeurt via rootmap en
downloadcategorie, niet via mounts. (2) Fork-API-sleutels (`SONARR__AUTH__APIKEY` /
`RADARR__AUTH__APIKEY`) en hook-sleutels (`MEDIAGUARD_HOOK_KEY`) komen uit de .env; core krijgt
dezelfde waarden als `MG_INSTANCE_<ID>_APIKEY/_HOOKKEY`. De hook-sleutel uit de omgeving is bij
élke start leidend; roteren gebeurt in de .env, niet in de UI. (3) Core/web/encoder altijd
read-only op media; recovery (`MG_LIBRARY_MODE`) en forks (`MG_FORK_MEDIA_MODE`) standaard
`ro` (K1, schaduw/migratie) en pas bij cutover of een verse installatie eenmalig `rw`.
(4) Encoder: één mount `${MG_WORK}/encode:/work` (cache + staging op hetzelfde filesystem);
`${MG_WORK}/encode/staging` met hetzelfde pad aan recovery (rw) en core (ro).
**Waarom:** gemeten in `tests/integration/stack`: aparte mounts voor cache/staging geven EXDEV in
de encoder; zonder vooraf gedeelde hook-sleutel weigert core elke pre-grab-call (401 → fail
policy); Sonarr's `/system/restart` stopt het proces in Docker, dus `restart: unless-stopped`
is nodig voor native restore. **Gevolg:** een instantie kan technisch bij de bibliotheek van
een andere (gedeelde /data-mount); R2-scheiding wordt bewaakt door ownership-checks,
rootmappen en categorieën en door de stacktest (bestanden alleen in de eigen rootmap).

## ADR-011 — Nul-configuratie-installatie: sleutels door init, schrijfvergrendeling in software
**Besluit (gebruikerseis: "ik wil geen sleutels en variabelen invullen"):** (1) Een kort
`mediaguard-init`-container (core-image, root met alleen CHOWN/DAC_OVERRIDE/FOWNER, zonder
netwerk) maakt bij iedere start ontbrekende mappen aan en genereert het interne token en per
instantie een API- en hook-sleutel in `<appdata>/secrets` (bestaande bestanden worden nooit
overschreven; bestaande, niet-lege mediamappen worden niet aangeraakt). (2) De drie
host-paden staan één keer in de compose, als benoemde volumes met `subpath`-mounts per
container (Docker 26+ / TrueNAS SCALE 24.10+), zodat iedere container alleen zijn eigen
submap en eigen sleutels ziet. (3) De mount-schakelaars `MG_LIBRARY_MODE`/`MG_FORK_MEDIA_MODE`
(ADR-010 punt 3) vervallen: forks en recovery krijgen de bibliotheek schrijfbaar, maar
schrijven worden in software tegengehouden — forks via schaduwmodus (core weigert grabs/
imports tijdens migratie; native restore schakelt RSS/downloadclients uit) en recovery via
`library.write_enabled` (standaard **uit**, automatisch aan na een geslaagde cutover of via
"Bibliotheek vrijgeven" in de UI met bevestiging). **Afweging t.o.v. K1** ("eerste import
alleen read-only mounts"): een read-only mount is sterker dan een softwarevergrendeling, maar
vereist een handmatige hostactie die de gebruiker uitdrukkelijk niet wil. De
softwarevergrendeling wordt getest (core-unittests + stacktest: activatie vóór vrijgeven wordt
geweigerd, bibliotheekbestand onaangeroerd).

## ADR-012 — Alles-in-één-image als standaardinstallatie (TrueNAS Custom App)
**Aanleiding:** de installatie via "Install via YAML" liep bij de gebruiker meermaals vast:
hoofdlettergevoelige paden in benoemde volumes (die bij een herinstallatie blijven bestaan
met het oude pad), en een init-container die met exit 1 de hele app liet mislukken, waarna
TrueNAS de containers én hun logs verwijdert. **Besluit:** standaard één image
(`ghcr.io/lvos04/mediaguard-core:<versie>-aio`, `deploy/aio/Dockerfile`) met alle onderdelen,
gestart door `mediaguard.aio` (PID 1 onder tini). Installatie via het gewone *Custom
App*-formulier: drie *Host Paths* (`/config`, `/work`, `/data`, gekozen met de mappenkiezer),
*Host Network* aan, geen variabelen. Mappen/sleutels aanmaken is nooit fataal; ontbreekt een
recht, dan start de app en meldt de UI/het log wat er nodig is. Crasht een onderdeel (of
herstart Sonarr zichzelf), dan start de supervisor het opnieuw met backoff.
**Afweging:** de losse compose (ADR-010/011) had per container een eigen mount en read-only
media voor core/encoder. In één container draaien alle onderdelen als 568 met dezelfde mounts;
de schrijfbeveiliging van de bibliotheek blijft de softwarevergrendeling uit ADR-011 (recovery
schrijft pas na cutover of "Bibliotheek vrijgeven"; de encoder vervangt nooit zelf, handoff).
Interne diensten (core 38100, recovery 38101, encoder 38000) luisteren alleen op 127.0.0.1.
De compose blijft beschikbaar als alternatief (README 3.3).

## ADR-013 en verder — platform-v2
Nieuwe besluiten voor het TrueNAS Home Platform V2 staan als losse bestanden in `docs/adr/`
(contractindeling): [`ADR-0013`](adr/ADR-0013-platform-v2-p0-interpretaties.md) legt vast hoe
MediaGuard 1.2.2 de platformcontracten 1.1 invult (opslag, Custom App, poorten, koppelen met de Hub,
noodstop, TrueNAS-toegang, evidence).
