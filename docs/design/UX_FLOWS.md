# MediaGuard Ultimate — UX-flows

Elke flow: doel, startpunt, stappen, wat de gebruiker ziet bij fout/offline, welke API's, en bevestigingsregels.
Alle flows zijn volledig via de UI uitvoerbaar (Q2); geen SSH/YAML. Status `✓` = endpoint bestaat nu in core,
`○` = endpoint gepland (J1/Q2.3/R5/S4) — UI toont tot dan "Niet beschikbaar".

---

## F1. Eerste installatie (setup-wizard)

**Doel:** van lege installatie naar ingelogde beheerder met gekozen taal, klaar voor migratie.

1. Browser opent `/` → app vraagt `GET /auth/setup-status` ✓. `needs_setup=true` → redirect `/setup`.
2. **Beheerder**: gebruikersnaam + wachtwoord + herhaling (client: ≥ 12 tekens en gelijk; server-fouten
   letterlijk tonen). `POST /auth/setup` ✓ → sessiecookie + CSRF.
3. **Taal & thema**: Nederlands/Engels (`PUT /auth/me/locale` ✓), thema (`PATCH /settings/scoped` `ui.theme` ✓).
4. **Volgende stap**: kaart "Bestaande Sonarr/Radarr/AV1 importeren" → Migratiewizard (F2), of "Later" → Overzicht.
5. Komt een tweede browser op `/setup` terwijl er al een gebruiker is → 409 → melding "Installatie is al
   voltooid" + link naar inloggen.

Fouten: core offline → volledige pagina-staat "MediaGuard-core niet bereikbaar" met `Opnieuw proberen`
(geen formulier dat niets kan opslaan).

## F2. Migratiewizard (10 stappen, R3/I5/P8)

Linkerkolom: stepper (status per stap: klaar/huidig/waarschuwing/geblokkeerd). Rechts: stapinhoud.
Voortgang wordt server-side opgeslagen (`migration_checkpoints`) en is na verversen/herstart terug.

| # | Stap | Wat de gebruiker doet/ziet | API |
|---|---|---|---|
| 1 | Preflight | TrueNAS-datasets, ACL's, GPU (`/dev/dri`), vrije ruimte, schrijf-/fsync-test | `POST /migration/preflight` ○ |
| 2 | Verbindingen | 4 bron-URL's + API-keys (secret-input) → `Test` per bron (versie, aantal items) | `POST /migration/preflight`, `PUT /settings/secrets/source:*` ✓ |
| 3 | Bronback-up | Back-up per bron + **testrestore**; zonder geslaagde testrestore blijft stap 10 vergrendeld | ○ |
| 4 | Mediapaden | Padmapping bron → container, read-only controle, hardlink-telling | `POST /migration/import-preview` ○ |
| 5 | Bron → doel | Mappingmatrix 4×4 (bron-instantie → doel-instantie), duplicaatwaarschuwingen, ownership | `POST /migration/instance-mapping/preview` ○ |
| 6 | Monitoring & toekomst | Vergelijking monitored flags, future seasons, tags, profielen; afwijkingen bevestigen | `…/instance-mapping/verify` ○ |
| 7 | Dry-run optimalisatie | Verdeling behouden/vervangen/zoeken/AV1/onzeker/overslaan, winst, tijd, cache-piek | `POST /migration/dry-run` ○ |
| 8 | Kwaliteitsprofielen | Profielen/custom formats per instantie; TRaSH-voorstel als diff | `GET /profiles` ○ |
| 9 | Seerr-routetest | 4 testverzoeken (serie/anime/film/animefilm) → juiste instantie | `POST /seerr/route-test` ○ |
| 10 | Cutover | Per instantie of groep; freeze bron-RSS; **danger-confirm** met getypte bevestiging | `POST /migration/cutover` ○ |

Rollback: altijd zichtbare knop `Rollbackplan` → toont wat teruggezet wordt → `POST /migration/rollback` ○.
Regels: niets schrijft naar productiemedia vóór stap 10; DV-keuze (Q1.4) wordt in stap 7 getoond met
standaard "DV via UI beschikbaar".

## F3. Ontbrekende aflevering herstellen

1. Overzicht → KPI **Ontbrekend** (drill-down) of "Actie nodig"-rij → `/ontbrekend?reden=…`.
2. Tabel **Oorzaken** (gegroepeerd per reasoncode, per instantie filterbaar) — `GET /wanted` ○.
3. Klik op aflevering → **diagnose-paneel**: afwijsregels, laatste zoekpogingen, indexerquota, release-
   kandidaten — `GET /wanted/{id}/diagnosis` ○.
4. Acties: `Zoek nu` (`POST /wanted/{id}/search` ○), `Simuleer versoepeling` (`POST /policy/simulate` ○ —
   toont diff van wat er zou worden gegrabd), `Uitsluiten`, `Backfill-plan` (`POST /wanted/backfill-plan` ○,
   per instantie met quota en nachtvenster).
5. Voortgang via SSE (`/events/stream` ✓ topic `wanted.*`); toast bij gevonden/geïmporteerd.
6. Bij aanhoudend falen: AI-paneel (alleen als advies-modus aan) — zie F9.

## F4. Dolby Vision inschakelen en testen (Q1)

1. Dolby Vision & HDR → kaart **DV → AV1 toestaan**, scope-selector `Globaal → Bibliotheek → Profiel → Titel → Job`.
2. Schakelaar + modus (4 keuzes). Overervingstrace toont direct welke scope wint.
3. `Opslaan` → `PATCH /dolby-vision/policies` ○ (of `PATCH /settings/scoped` ✓ voor geregistreerde dv.*-keys);
   audit-referentie in toast. Integriteitscontroles staan zichtbaar vergrendeld.
4. **Test een bestand**: kies titel → `POST /dolby-vision/test-encode` ○ → rij in "Proefconversies" met live
   voortgang (`GET /dolby-vision/tests/{id}` ○ + SSE).
5. Resultaat: verificatie-chips (RPU, kleur, audio, subs), `Metadata vóór/na` (difference viewer),
   `Bekijk afspeelchecklist`, `Bevestig geschikte client` (`POST /dolby-vision/playback-confirmations` ○).
6. `Activeer na verificatie` (danger-confirm met impact + snapshot) → `POST /dolby-vision/activate/{job}` ○.
   `Herstel origineel` → `POST /dolby-vision/restore/{job}` ○.
7. DV5 / FEL: route toont `Niet ondersteund` resp. FEL-waarschuwing met uitleg; geen "forceer"-knop.

## F5. Bibliotheek bulk-optimaliseren met dry-run (Q2.2)

1. Bibliotheek → filters (type, instantie, kwaliteit, AV1-kandidaat) → selecteer (of "hele bibliotheek").
2. `Optimaliseer (dry-run)` → drawer met **verdeling** (stackbar + legenda met aantallen),
   resourceprognose (tijd, cache-piek, I/O), overgeslagen hardlinks, risico's — `GET /optimization/plan` ○.
3. Per categorie openklappen → bestandslijst met reden.
4. `Start` → danger-confirm (aantal bestanden, totale GB, dataset, snapshotstatus, rollbackvoorwaarde) →
   `POST /optimization/run` ○. Daarna `Pauzeer` / `Hervat` op de job-kaart.

## F6. Serie volgen incl. toekomstige seizoenen

1. Bibliotheek → `Toevoegen` → zoek (TVDB/TMDB via instantie) → kies **instantie** (voorstel op basis van
   anime-detectie; gebruiker bevestigt — R4).
2. Monitoring: `Alles`, `Toekomstige afleveringen`, `Ontbrekend`, `Alleen nieuwe seizoenen`, `Geen`;
   **toekomstige seizoenen volgen** als aparte schakelaar; profiel, rootfolder, taalprofiel.
3. Opslaan → `POST /instances/{id}/library` ○ (adapter naar fork) → seriedetail.
4. Seriedetail: seizoenen als heatmap; per seizoen/aflevering monitoring aan/uit; `Zoek seizoen`.
   Bewust niet-gemonitorde afleveringen worden gearceerd getoond en nooit als "ontbrekend" geteld.

## F7. AV1-profiel aanpassen

1. AV1 Studio → Profielen → profiel kiezen. Elke instelling toont wat ze doet (bv. "preset 6 op 4K kost
   5–8 u per film").
2. `Proefdraai`: kies bestand → CRF 27/29/31 op 2-minuten-fragment → tabel grootte/tijd/VMAF
   (bestaande encoder-API `POST /api/encoder/profielen/{id}/proefdraai`).
3. Opslaan (encoder-API `PUT /profielen/{id}`), audit; wijziging geldt voor nieuwe jobs (uitleg).

## F8. Rollback / quarantaine herstellen

1. Quarantaine & herstel → lijst met reden, transactiestatus, grootte, retentie — `GET /quarantine` ○.
2. `Inspecteren` → difference viewer (origineel vs resultaat), hersteltijdlijn, snapshotreferentie.
3. `Terugzetten` (1 of bulk) → danger-confirm: aantallen, GB, extra opslag nodig, dataset, snapshot
   geverifieerd, nieuwe status, getypte bevestiging bij bulk → `POST /quarantine/{id}/restore` ○.
4. Voortgang op job-kaart; resultaat in audit-tijdlijn met `undo` waar mogelijk.

## F9. AI-adviseur opt-in (S4)

1. AI Adviseur: standaard **Uit**; scherm legt uit dat alles zonder AI werkt.
2. Provider kiezen (Claude/OpenAI) → API-sleutel via secret-input (`PUT /settings/secrets/ai:*` ✓) →
   `Test verbinding` (`POST /ai/providers/{p}/test` ○) — echte gestructureerde-antwoordtest.
3. **Toestemming**: schakelaar externe metadataoverdracht + exact voorbeeld van verzonden velden.
4. Beleid: Uit → Schaduw → Advies; `Beperkt automatisch` blijft disabled tot schaduwfase geslaagd (reden zichtbaar).
5. Budget: maandlimiet (standaard €5), per analyse, per dag (`PATCH /ai/settings` ○).
6. Casussen: Accepteer / Wijs af / Markeer onjuist / Opnieuw onderzoeken (+ feedback). PolicyGuard-uitkomst
   altijd naast het voorstel.

## F10. Noodstop

1. Topbar `Noodstop` (ook mobiel) → bevestigingsdialoog met reden-veld en uitleg wat er stopt.
2. `POST /system/emergency-stop {active:true, reason}` ✓ → rode banner op elke pagina met wie/sinds/opheffen.
3. Opheffen via banner of knop → bevestiging → `{active:false}`. Status komt live via SSE `system.emergency_stop`.

## F11. Instelling wijzigen (schema-gedreven)

1. Instellingen → categorie → scope kiezen (Globaal → Media-type → Instantie → …).
2. Veld wijzigen → rij wordt "dirty"; savebar onderin toont aantal wijzigingen.
3. `Opslaan` → `PATCH /settings/scoped` ✓ (alles-of-niets). Bij 422: veld rood, servermelding letterlijk,
   **oude waarde blijft actief** en zichtbaar. Bij succes: toast + nieuwe effectieve waarde + overervingstrace.
4. `Herstel standaard` → `reset: true` voor die scope. `Voorbeeldbeslissing` → `POST /settings/simulate` ✓.
