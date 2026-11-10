# MediaGuard Ultimate — Schermspecificaties

Dekt I2 (12 hoofdschermen), Q2.1 (bedienbare acties per scherm), R5 (instanties) en S4 (AI Adviseur).
Raster: desktop 1440/1280 met 12 kolommen (`.grid`/`.span-N`), mobiel 360–390 met bottom nav + drawer;
tabellen worden op mobiel gestapelde kaarten. API-status: ✓ bestaat in core nu · ○ gepland (contract uit J1/Q2.3/R5/S4). Stand 2 nov 2026: vrijwel alle routes bestaan inmiddels; de implementatie in `apps/web` is aan de echte routes gekoppeld (zie het eindrapport / `docs/UI_CONTROL_COVERAGE_MATRIX.md`); alleen `/migration/*` ontbreekt nog
· E = bestaande AV1-encoder-API via proxy `/api/encoder/*`.

Algemene regels voor ieder scherm:
- Paginakop: titel, één zin uitleg, primaire + secundaire acties rechts (mobiel: onder de titel).
- Ieder datablok heeft loading/empty/error/offline/niet-beschikbaar/stale-staten (DESIGN_SYSTEM §5).
- Filters en sortering staan in de URL (deelbaar, blijven bewaard bij terug-navigatie).
- Viewer-rol: mutatieknoppen disabled met reden "Alleen-lezen rol".

Navigatie (sidebar-volgorde, groepen):
`Overzicht` · **Media**: Bibliotheek, Ontbrekend & herstel, Kalender, Anime mapping · **Kwaliteit**: Release Lab,
Kwaliteit & TRaSH, Audio & ondertiteling · **AV1 & HDR**: AV1 Studio, Dolby Vision & HDR · **Operaties**: Jobs & logs,
Quarantaine & herstel, Migratie & optimalisatie · **Integraties**: Downloads & indexers, Seerr, AI Adviseur ·
**Systeem**: Instellingen, Updates & diagnostics. Mobiel bottom nav: Overzicht · Bibliotheek · Ontbrekend · AV1 · Meer.

---

## S1 Overzicht `/` (preview 01)
- **Desktop**: KPI-rij 4×`span-3` (Beschikbaar, Ontbrekend, Fouten, AV1-besparing; elk drill-down) →
  `Actie nodig` (span-7, max 6 rijen: oorzaak + primaire herstelactie) | `Download & encode` (span-5: GPU- en
  CPU-baan + actieve downloads) → `Nieuwe afleveringen deze week` / `Seizoenscompleetheid` /
  `Verbeterkansen` (3×span-4) → `Systeembelasting` (span-8, Recharts) | `Instanties & integraties` (span-4).
- **Mobiel**: KPI 2×2, daarna alle kaarten gestapeld; actierijen wrappen de knop onder de tekst.
- **Data**: `GET /status` ✓, `GET /jobs` ✓, `GET /instances` ○, `GET /library?summary` ○, `GET /wanted?summary` ○,
  `GET /calendar?days=7` ○, `GET /optimization/plan?summary` ○, encoder `statistieken`/live E; SSE ✓.
- **Acties**: mediatype-filter, automatisering pauzeren (noodstop-light ○), noodstop ✓.

## S2 Bibliotheek `/bibliotheek` (preview 02)
- Tabs Series/Anime/Films/Animefilms met aantallen; toolbar: zoekfilter, instantie-select, filterchips
  (Ontbrekend, Onzeker, AV1-kandidaat, DV/HDR, Geen NL-subs), weergave tabel/posters.
- Tabel (sticky header, virtueel > 500): selectie, titel (poster+meta), compleet (progress), kwaliteit (ribbon),
  status, monitoring, grootte. Instantiekolom alleen bij "Alle instanties".
- Bulkbalk: Zoek ontbrekend, Monitoring, Optimaliseer (dry-run, F5), meer.
- **Data**: `GET /library?type=&instance_id=&filter=` ○, `GET /instances` ○. Acties ○.
- Mobiel: gestapelde kaarten, selectie via lang-indrukken niet nodig — checkbox verborgen, bulk via "Selecteren".

## S3 Serie-/filmdetail `/bibliotheek/[instance]/[id]`
- Header: poster (168), titel, jaar, netwerk, instantie-tag, externe ID's, monitoring-select, profiel, root.
- Tabs: Overzicht (seizoensheatmap + seizoenstabel met monitoring-switch per seizoen, "toekomstige seizoenen volgen"),
  Afleveringen, Bestanden, Historie. Acties: Zoek ontbrekend, Verifieer (`POST /library/{id}/verify` ○), Optimaliseer.
- **Data**: `GET /library/{id}` ○, `GET /library/{id}/evidence` ○.

## S4 Aflevering-detail `/bibliotheek/[instance]/[id]/aflevering/[ep]` (preview 03, I4)
- Header: poster, serie, SxxEyy + absoluut nr., titel, uitzenddatum, release, monitored, beschikbaarheid, status-chips.
- Contextuele acties rechts: Herstel mapping (primair als identiteit onzeker), Opnieuw controleren, Zoek alternatief,
  Optimaliseer, In quarantaine (danger-confirm met risicowaarschuwing).
- Tabs: **Overzicht** (samenvatting, "Waarom deze release?" in gewone taal), **Identiteit** (evidence cards, mapping trace),
  **Video** (stream, HDR/DV, bitrate, VMAF), **Audio/ondertitels** (huidig vs gewenst, diff), **Releases** (kandidaten),
  **Conversies** (trials, verificatie, rollbackstatus), **Historie** (tijdlijn).
- Rechterkolom (span-4): huidig vs gewenst (diff), AI-advies-paneel (alleen als AI ≠ Uit), hersteltijdlijn.
- Mobiel: acties in een horizontaal scrollende knoppenrij onder de header; tabs scrollen horizontaal.

## S5 Ontbrekend & herstel `/ontbrekend` (preview 04, Q2.1)
- KPI's: totaal, in zoekqueue, hersteld 7 d, geblokkeerd. Oorzakentabel (span-8) met slimme oplossing + actie;
  backfill per instantie (span-4) met quota-meters en pauzestatus; seizoenscompleetheid (span-12).
- Aflevering-diagnose in drawer rechts: afwijsregels, pogingen, kandidaten, `Zoek nu`, `Uitsluiten`, prioriteit.
- **Data**: `GET /wanted` ○, `GET /wanted/{id}/diagnosis` ○, `POST /wanted/{id}/search` ○, `POST /wanted/backfill-plan` ○,
  `POST /instances/{id}/backfill` ○.

## S6 Kalender `/kalender`
- Weekraster 7 kolommen (mobiel: lijst per dag); items met kleurrand per type (serie/anime/film), status
  (binnen/verwacht/zoekend/ontbreekt), monitored-flag, verwachte beschikbaarheid, anime-dub-indicatie
  (alleen als bron betrouwbaar). Navigatie vorige/volgende week, filter type/instantie.
- **Data**: `GET /calendar?start=&end=&instance_id=` ○.

## S7 Release Lab `/release-lab`
- Zoek titel/aflevering → huidige release vs alternatieve kandidaten (diff viewer): harde eisen ✓/✗, taal, bron,
  4K, TRaSH-score, bronherkomst, upgradebesluit en "effect"-preview.
- **Data**: `GET /release-candidates/{id}` ○, `POST /policy/simulate` ○.

## S8 AV1 Studio `/av1` + subpagina's (preview 10, behoud AV1-app)
- Tabs (routes): Overzicht, Wachtrij, Workers, Beslissingen, Kwaliteit, Profielen, Bibliotheken, Logs, Fouten,
  Encoder-instellingen, AI-canary. Dit zijn de geporteerde AV1-pagina's; functionaliteit ongewijzigd.
- Overzicht: 4 tegels (wachtrij, besparing, doorvoer, systeem), beide banen altijd zichtbaar, grafieken CPU/GPU,
  stroom, RAM, besparing per dag, prognose.
- **Data**: encoder-API E via `/api/encoder/*` (REST) en `/api/encoder/ws` (WebSocket). Core-encoding ○
  (`GET/POST /encoding/jobs`, cancel).

## S9 Dolby Vision & HDR `/dolby-vision` (preview 06, Q1)
- Policy-kaart met scope (Globaal/Bibliotheek/Profiel/Titel/Job), schakelaar, 4 modi, overervingstrace,
  vergrendelde integriteitsregels. Profielondersteuningstabel (8.1/8.4, 7 MEL, 7 FEL, 5, HDR10+, HDR10).
  Proefconversies-tabel met per-bestand acties (Test nu, Metadata vóór/na, CPU SVT-AV1, Apart klaarzetten,
  Afspeelchecklist, Bevestig client, Activeer, Herstel origineel). Bevestigde afspeelketens.
- **Data**: `/dolby-vision/capabilities|policies|test-encode|tests/{id}|playback-confirmations|activate|restore` ○.

## S10 Audio & ondertiteling `/audio`
- Taalvoorkeur per mediatype/instantie (NL/EN/JA), vereist vs voorkeur, default track, dub/dual-beleid,
  forced/signs&songs, audio-preservation, ondertitelbeleid. Schema-gedreven (`category = Audio & ondertiteling`)
  met scope-selector + voorbeeldbeslissing. **Data**: settings ✓ (+ keys door B2 ○).

## S11 Anime mapping `/anime`
- Lijst conflicten (absoluut ↔ seizoen, cour, OVA/specials), hashbewijs, voorgestelde correctie (diff),
  `Pas toe` / `Negeer`. Koppelingen AniDB/TheXEM/Shoko (status + test). **Data**: identity-API ○.
- **Anime-routering** (bovenaan, 2 nov 2026): acties uit `GET /routing/anime/actions` (filter Alles/Open/Verplaatst),
  per rij statuschip, bron → doel, Nederlandse bewijsregels; knoppen `Verplaats nu` / `Hier laten` / `Terugzetten`
  (`POST /routing/anime/actions/{id}/approve|keep|undo`). Instellingen `routing.anime.*` via het schema. Globale
  toast op SSE `routing.anime_moved`. Lege staat legt uit wat er automatisch gebeurt — geen voorbeelddata.

## S12 Migratie & optimalisatie `/migratie` (preview 05, I5, R3)
- 10-staps wizard (UX F2); rechts stapinhoud; stepper links (mobiel boven). Bulkoptimalisatie-tab (F5),
  tab AV1-historie (`POST /migration/av1/import`, herkomsttabel).
- Zonder `?run=`: bronnen (back-up-upload met voortgang per bestand, live bron URL+sleutel), runs (starten/hervatten).
  Met `?run=<id>&step=<stap>`: stap 0 *Bronnen & opties* + de 10 backendstappen; status komt uit
  `GET /migration/runs/{id}` en overleeft verversen. Lange stappen tonen de job (`GET /jobs/{id}`: voortgang + logs).
  Cutover/activate-search/rollback per instantie met getypte instantienaam; NO-GO-lijst en accepteren met reden (admin).
- **Data**: `/migration/*` ✓ (echte routes, zie docs/MIGRATION_AND_CUTOVER.md), `/optimization/*` ✓.

## S12b Inrichten `/inrichten`
- Tabs: Overzicht (nog-nodig-lijst, statusgrid instanties × secties, Controleren / Bekijk wijzigingen / Toepassen /
  afwijkingscontrole, "Door mij beheerd" per item), Downloads, Indexers (Prowlarr of direct), Paden & categorieën,
  Media & kwaliteit, Seerr, Optimaliseren na migratie, Hernoemen, Instellingen (`provisioning.*`).
  Geheimen zijn write-only: badge "ingesteld", leeg = behouden, `Wissen` = verwijderen. **Data**: `/provisioning/*` ✓.

## S13 Downloads & indexers `/downloads`
- Per instantie: indexers/Prowlarr, downloadclients, categorieën (`tv`, `tv-anime`, `movies`, `movies-anime`),
  RSS, limieten, path mappings, zoekdiagnose. Instantieselector bovenaan. **Data**: `/instances/{id}/…` ○ (adapter).

## S14 Seerr `/seerr`
- Verbinding (URL + API-key secret), validatie, routeringsmatrix 4 routes, `Test alle routes`
  (`POST /seerr/route-test` ○), compatibiliteit en migratie van oude endpoints.

## S15 Kwaliteit & TRaSH `/kwaliteit`
- Kwaliteitsprofielen per instantie, custom-format-scores, vergelijking per release, afwijsredenen,
  4K/1080p-profielen, TRaSH-import/sync (diff vóór toepassen), policy-simulatie. **Data**: `GET/PUT /profiles` ○.

## S16 Jobs & logs `/jobs`
- Statusfilters (queued/running/retry/failed/succeeded/cancelled), type, `instance_id`; tabel; detail-drawer met
  payload, resultaat, logs (`GET /jobs/{id}` ✓), `Annuleren` ✓, `Opnieuw` ✓. Tab **Audit** (`GET /audit` ✓) en
  **Gebeurtenissen** (`GET /events/recent` ✓, live SSE).

## S17 Quarantaine & herstel `/quarantaine` (preview 12) — opslag is schaars
- **Gebruiksbalk** bovenaan: “Quarantaine 312 GB / 500 GB”, % en kleur (accent → warning > 80 % → danger bij vol),
  tellers vrijgavecontrole (geslaagd / mislukt / nog te controleren), bewaartermijn, en wat de **volgende automatische
  opruiming** verwijdert (aantal + GB). Is het quotum vol → rode melding “AV1-wachtrij gepauzeerd: quarantaine vol”.
- **Modus** als eerste keuze (radiokaarten, `quarantine.mode`): “Bewaren volgens bewaartermijn” (standaard) of
  “Direct verwijderen zodra het nieuwe bestand de controle heeft doorstaan” met eerlijke consequentie (ruimte binnen
  minuten vrij; terugzetten kan dan niet meer, opnieuw downloaden wel). Garantieregel altijd zichtbaar: een origineel
  verdwijnt nooit vóór een geslaagde vrijgavecontrole. Bij ‘direct’ zijn bewaartermijn en minimale leeftijd uitgegrijsd.
- **Tabel**: item + pad, chip “Vrijgavecontrole: geslaagd / mislukt / nog niet gecontroleerd” (uitklapbaar per check),
  transactie + status, grootte, datum; acties *Controleer nu*, vastzetten, *Terugzetten*, *Definitief verwijderen*.
- **Verwijderen**: danger-dialog met vrijgemaakte GB en getypte bevestiging `VERWIJDER`; backend-409 (controle niet
  geslaagd) → tweede, expliciete waarschuwing “Toch verwijderen zonder controle” (opnieuw typen). Bulk: preview
  (`confirm:false`) met aantal, GB, overgeslagen en niet-gecontroleerde items; `force_unverified` alleen via een aparte
  checkbox. *Nu opruimen*: preview → bevestigen.
- Inline instellingen (schema): `quarantine.*` + vergrendelde `integrity.release_check_before_purge`.
- **Data** ✓: `GET /quarantine`, `GET /quarantine/usage`, `POST /quarantine/{id}/release-check`, `POST /quarantine/{id}/restore`,
  `POST /quarantine/{id}/pin`, `DELETE /quarantine/{id}`, `POST /quarantine/bulk-delete`, `POST /quarantine/purge`, `GET /recovery/readiness`.

## S18 Instellingen `/instellingen` (+ `/instanties`, `/gebruikers`, `/geheimen`, `/audit`) (preview 07, 08)
- **Algemeen** — volledig schema-gedreven uit `GET /settings/schema` ✓: categorie-navigatie links, scope-selector,
  zoeken (`GET /settings/search` ✓), geavanceerd-schakelaar, `SettingField` per instelling, savebar,
  simulatie (`POST /settings/simulate` ✓), `PATCH /settings/scoped` ✓, overrides (`GET /settings/overrides` ✓).
- **Instanties** (R5) — instance cards (Gezond/Degraded/Offline), bron vs doel-mapping, rootfolder/profiel per instantie,
  Seerr-routetest, duplicaatwaarschuwingen, backfill per instantie; `/instances/*` ○.
- **Gebruikers** — lijst/rol/uitschakelen/wachtwoord (`/auth/users` ✓), eigen taal (`PUT /auth/me/locale` ✓).
- **Geheimen** — alleen `is_set` + datum; zetten/vervangen/verwijderen (`/settings/secrets` ✓). Waarde nooit teruggelezen.

## S19 AI Adviseur `/ai` (preview 09, S4)
- Provider (Claude/OpenAI/Uit), model (live), API-key (secret), verbindingstest, beleid (Uit/Schaduw/Advies/Beperkt auto),
  budget (maand/analyse/dag, verbruik, cachehits), privacy-toestemming + voorbeeldpayload, casussen met Accepteer/
  Wijs af/Markeer onjuist/Opnieuw onderzoeken + feedback. **Data**: `/ai/*` ○, secrets ✓.

## S20 Updates & diagnostics `/systeem`
- Versie/commit core + forks + encoder, upstreamstatus, changelog, updates met back-up, DB-migratie, rollback,
  integratie-/GPU-tests (`POST /admin/diagnostics/{component}` ○), supportbundel zonder geheimen, health
  (`GET /health` ✓, `GET /admin/health` ○), UI-coverage (`GET /admin/coverage` ○).

## S21 Inloggen `/login` en eerste installatie `/setup` (preview 11)
- Split-screen (merkpaneel links, formulier rechts; mobiel alleen formulier). Setup is 3 stappen (UX F1).
