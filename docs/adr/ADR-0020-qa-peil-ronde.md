# ADR-0020 — QA na de Peil-ronde: Hub-adres alleen voor een beheerder, OpenAPI-export, volledige Peil-testmatrix

- **Status:** geaccepteerd
- **Datum:** 2026-11-09
- **Fase:** ontwerpronde Peil 1.1.0, onafhankelijke QA na stap 1–5 (ADR-0015 t/m ADR-0019)
- **Context:** vaste regels van deze ronde: alleen uiterlijk en de toegestane toevoegingen; functies, API-routes,
  foutformaat, datamodel, poorten en installatie veranderen niet (OpenAPI byte-gelijk); `data-testid`'s en rollen
  blijven; geen versie-ophoging. De QA controleerde de ronde opnieuw tegen `PLATFORM_CONTRACTEN.md` 1.1 en
  ONTWERPTAAL §12 en herstelde wat niet klopte.

## Besluiten

1. **`system.hub_url` mag alleen een beheerder wijzigen.** De link "Terug naar de Hub" (O-11, ADR-0016 punt 1) staat
   bij elke gebruiker in de zijbalk en het profielmenu. Stap 3 liet de instelling via de gewone instellingen-API
   wijzigen door elke operator, en dus ook door de app-sleutel met rol operator. Daarmee kon een operator iedereen naar
   een nagemaakte inlogpagina laten klikken. Dat is hetzelfde risico als bij `app.public_url` en `notify.base_url`,
   die al alleen voor een beheerder zijn. Veiligste lezing: `system.hub_url` staat nu in `ADMIN_ONLY_KEYS`
   (`apps/core/mediaguard/settings_guard.py`). Een operator krijgt 403, het schema markeert de instelling als
   `admin_only` en de UI toont haar dan alleen-lezen. Lezen blijft voor elke rol mogelijk. Geen nieuwe route, geen
   schemawijziging: de OpenAPI blijft byte-gelijk. Test: `test_beveiliging.py::test_operator_cannot_change_dangerous_settings[system.hub_url-…]`
   en `test_schema_marks_admin_only_and_button_only`.
2. **OpenAPI-export in de repo en drift-controle in CI** (contract §5). `docs/openapi/mediaguard.openapi.json` is de
   export van core (`scripts/export-openapi.py`; JSON met gesorteerde sleutels, 2 spaties, UTF-8). De inhoud is
   byte-gelijk aan de nulmeting van vóór de ronde (383 paden; sha256 van de inhoud zonder slotregel
   `54eee4d7…3e59a`, gemeten op `8cd3175` en op de eindstand, op Python 3.12 en 3.14).
   `apps/core/tests/test_openapi_export.py` draait in de core-job van CI en faalt bij elke API-, route- of
   schemawijziging die niet met het exportbestand is meegecommit. Dit is een test- en documentatietoevoeging, geen
   API-wijziging.
3. **De Peil-e2e dekt nu de volledige testmatrix van ONTWERPTAAL §12.** Eerst: 5 breedtes en per scherm om en om licht
   of donker. Nu: 320/360/390/768/1024/1440 × licht én donker voor de 9 hoofdschermen. Ook de controles uit de review
   van stap 5 (`review.mjs`), die alleen buiten CI draaiden, staan nu in CI:
   - geen tekst < 14 px;
   - geen ellips op inhoud;
   - geen kopsprongen;
   - elk aanraakdoel op de hele pagina ≥ 44 × 44 px, met dezelfde uitzonderingen (link of vakterm in een zin, het
     plotvak van een grafiek).

   Nieuw zijn twee tests:
   - `prefers-reduced-motion`: geen animatie of overgang langer dan 10 ms;
   - forced colors: elke Onbekend-vorm houdt zijn stippelrand.
4. **Geen stille overslag in de e2e.**
   - `design-shots.spec.ts` draait alleen met `MG_SHOTS_DIR`. Zonder die variabele valt de spec buiten de run
     (`testIgnore`), zodat een gewone run 0 overgeslagen tests heeft en elke overslag opvalt.
   - De N13-test in `werk-r032.spec.ts` sloeg zichzelf over als er niets aandacht vroeg. Nu zet de test zelf een vast
     aandachtspunt klaar met een nagebootst `/api/v1/attention`-antwoord en controleert hij altijd.
5. **Tijdgevoelige hooktests zonder koude start** (`apps/core/tests/test_hooks.py`). De twee tests met een
   wandklokgrens faalden op de GitHub-runner met Python 3.12 af en toe (1261, 1347, 1670 en 1910 ms). Lokale meting:
   de eerste hookaanroep na het starten kost ±120 ms buiten de handler (modules laden, caches); de handler zelf
   ±3 ms. Op een trage runner is dat > 1 s. Nu doet elke test eerst één aanroep op de andere instantie en
   `gc.collect()` (een SQLite-verbinding van een eerdere test mag niet midden in de meting sluiten). De grenzen blijven
   gelijk (1000 ms en 1,5 s, onder de 3 s-time-out van de fork) en een hook die op de database wacht, faalt nog steeds
   (dan duurt het ≥ 10 s).

## Wat bewust niet is veranderd

- Bekende contractafwijkingen van vóór deze ronde blijven buiten de Peil-ronde. Ze staan in `DELTA_MATRIX.md` met een
  eigen fase:
  - base-images niet op `@sha256` (C-10, P3-08);
  - geen `/healthz`/`/readyz` en geen connectorcontract;
  - foutformaat en `Idempotency-Key`;
  - drie mappen `/config`, `/work` en `/data` in plaats van één `/appdata` (C-03, ADR-0013);
  - YAML-route nog in README/INSTALL (C-02, P3-07).
- Geen versie-ophoging, tag of release; CHANGELOG onder "Unreleased".

## Gecontroleerd (lokaal, 9 november 2026)

- **OpenAPI:** byte-gelijk aan `8cd3175`, zowel gesorteerd als in de oorspronkelijke volgorde (383 paden).
- **Routes en testids:** dezelfde `page`/`layout`-bestanden in `apps/web/app`. Geen `data-testid` verdwenen; nieuw zijn
  alleen `hub-link`, `copy-field` en `grafiek-*`. De web-UI roept dezelfde API-paden aan als vóór de ronde.
- **Core:** buiten de tests verandert in de ronde alleen het instellingenregister (`system.hub_url`, `ui.theme`
  standaard Systeem). Integriteit, herstel en bewaking zijn niet aangeraakt.
- **Testresultaten:**

  | Onderdeel | Uitkomst |
  |---|---|
  | core, Python 3.12 en 3.14 | 1104 geslaagd, 2 overgeslagen (geen AI-sleutels), ruff groen |
  | encoder-subset | 124 geslaagd, 1 overgeslagen; ruff en mypy groen |
  | web: lint, typecheck, vitest en `check:peil` | groen; vitest 54 geslaagd |
  | web: volledige e2e zonder herkansingen | 215 geslaagd, 0 overgeslagen (vóór de QA: 150 + 4 overgeslagen) |
  | Android: `verifyPaparazziDebug` | 278 tests, 0 fouten; 78 vergeleken afdrukken (Sim-getest) |

- **Alles-in-één-image** (`deploy/aio/Dockerfile`), gebouwd uit:
  - de huidige core en web;
  - het gepubliceerde `mediaguard-encode:1.2.2` (de encoder-runtime is sinds v1.2.2 ongewijzigd, alleen de tests
    zijn veranderd);
  - de lokale fork-images.

  Rooktest met drie lege mappen:
  - healthy na 15 s en `/api/v1/health` ok;
  - eerste beheerder aangemaakt en `system.hub_url` gezet;
  - na `docker restart` opnieuw healthy; de oude beheerder logt in en de instelling staat er nog;
  - nette stop met exitcode 0.

  Diensten draaien als 568, de web-server als `nobody` en alleen de supervisor als root. Sim-getest; een echte
  TrueNAS: NIET GEVALIDEERD.

## Gevolgen

- Een operator die het Hub-adres wil zetten, vraagt dat aan een beheerder.
- Elke bewuste API-wijziging vraagt voortaan `python scripts/export-openapi.py` en een commit van het exportbestand.
- De web-e2e duurt langer: 108 in plaats van 45 matrixtests, ±1,5 s per test lokaal.
