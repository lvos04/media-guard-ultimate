# TrueNAS Home Platform V2 — documentatie voor `media-guard-ultimate`

Bron: uitvoeringspakket `TrueNAS_Home_Platform_V2_VOLLEDIG_2026-11-09.zip` (peildatum 9 november 2026).

**Rol van deze repo (vast besluit):** B04 delta op v1.2.2: alleen ontbrekende punten.

Hier staan alleen de eigen B-, I-, A- en promptdocumenten plus de gedeelde architectuur (`00_START`, `01_ARCHITECTUUR`, `05_TESTS`). Het volledige pakket en de Excel staan in `lvos04/ai-server-manager` onder `docs/platform-v2/`.

Onbekende NAS-gegevens (D01–D18) worden nooit met voorbeeldwaarden ingevuld; ze worden via de onboarding-wizard in de web-UI vastgelegd.

## Bestanden

- `00_START/BESLUITEN_EN_ONBEKENDEN.md`
- `00_START/INHOUDSOPGAVE.md`
- `00_START/INHOUD_CONTROLE.json`
- `00_START/LEES_MIJ_EERST.md`
- `00_START/SHA256SUMS.txt`
- `01_ARCHITECTUUR/01_PROJECTCHARTER.md`
- `01_ARCHITECTUUR/02_DATA_EIGENAARSCHAP.md`
- `01_ARCHITECTUUR/03_RISICO_AUTONOMIE.md`
- `01_ARCHITECTUUR/04_GEGEVENS_EN_STORAGE.md`
- `01_ARCHITECTUUR/05_OPERATIES_EN_UPDATES.md`
- `01_ARCHITECTUUR/06_FASERING_EN_GATES.md`
- `01_ARCHITECTUUR/07_API_EN_EVENTSCHEMA.md`
- `01_ARCHITECTUUR/08_UI_EN_INTERACTIES.md`
- `02_BOUWPLANNEN/B04_MEDIAGUARD_DELTA.md`
- `03_INSTALLATIE/I03_MEDIAGUARD.md`
- `03_INSTALLATIE/I16_PROWLARR.md`
- `03_INSTALLATIE/I19_SEERR.md`
- `03_INSTALLATIE/I20_BAZARR.md`
- `04_BESTAANDE_APPS/A04_READARR.md`
- `04_BESTAANDE_APPS/A05_FLARESOLVERR.md`
- `04_BESTAANDE_APPS/A09_BAZARR.md`
- `04_BESTAANDE_APPS/A11_PROWLARR.md`
- `04_BESTAANDE_APPS/A12_SEERR.md`
- `04_BESTAANDE_APPS/A18_MEDIAGUARD.md`
- `05_TESTS/GEBRUIKERSWENSEN_TRACEERBAARHEID.md`
- `05_TESTS/INTEGRALE_E2E_TESTMATRIX.md`
- `06_TEMPLATES/PROMPTS_PER_APP/PROMPT_B04_MEDIAGUARD_DELTA.md`

## Contract 1.3 en Peil 1.1.1 (2026-11-10)

- [`PLATFORM_CONTRACTEN.md`](PLATFORM_CONTRACTEN.md) (`contracts 1.3`) en
  [`CONTRACT_1.3_CONFORMITEIT.md`](CONTRACT_1.3_CONFORMITEIT.md) zijn **byte-gelijke kopieën** van
  `lvos04/ai-server-manager@0a7143b`; de pin staat in [`HERKOMST.json`](HERKOMST.json). Nooit met de hand wijzigen:
  `python3 scripts/kopie-herkomst.py controleer` (offline) en `vergelijk --online` (CI-job `kopieen`); een nieuwe kopie
  is een stap van de orkestratie (`bijwerken --set contract`). Wat deze repo voor 1.3 moet doen: §6 van de
  conformiteitslijst (C1.3-MGU-01…09).
- Gedaan in deze ronde (alleen NU-punten, ADR-0021): C1.3-MGU-01 (kopie, drifttest, EVIDENCE-test), C1.3-MGU-03
  (upgradebewijs, HARDE EIS: `scripts/upgradebewijs.py`, gate in `release.yml` vóór `publish`) en het adresdeel van
  C1.3-MGU-08 (`system.hub_url` volgens §3.2). Peil 1.1.1 overgenomen (C1.3-ALG-05): `design/ontwerptaal/HERKOMST.md`.
- Gedaan op 2026-11-11 (C1.3-MGU-04, contract §2.3 regel 2; ADR-0022 en ADR-0023): MediaGuard volgt meerdere
  mediaservers tegelijk (Emby én Jelly Leon/Jellyfin; Δ-MG-DUAL) en meldt zich per server aan (Emby `X-Emby-Token`,
  Jellyfin 12.x `Authorization: MediaBrowser`; Δ-MG-AUTH). De bestaande Emby-koppeling is zonder handeling server 1
  en wordt niet verplaatst. Contracttest tegen echte containers: `tests/contract/mediaserver/` (CI-job
  `mediaservers`, zware run). Het upgradebewijs vergelijkt nu ook in de alles-in-één-rooktest per tabel. De
  Android-app bestaat ook in en-US. Stand per verzoek: [`B03_DELTAVERZOEKEN.md`](B03_DELTAVERZOEKEN.md) §3 en §4.
  Alles Sim-getest; op de echte NAS NIET GEVALIDEERD.
- Open (latere fasen, in [`EVIDENCE.json`](EVIDENCE.json) als `Niet gestart`): C1.3-MGU-05/-06/-07/-09
  (P3), terugkeerpad na inloggen (-08, P3); C1.3-MGU-02 is `Geblokkeerd` tot de gebruiker het GHCR-pakket aan de nieuwe
  repo koppelt.
- Commit-hashes in `EVIDENCE.json`-regels van vóór 2026-11-10 (B04-001…010) verwijzen naar de oude historie, nu
  `lvos04/media-guard-ultimate-archief` (privé); deze repo begint met één schone startcommit (`3998bea`).

## Eigen werkstukken van deze repo (fase P0, 2026-11-09)

- [`INVENTARIS.md`](INVENTARIS.md) — baseline 1.2.2 en compat-matrix (B04-001), diensten, modules, alle API-routes
  met `bestand:regel` (bijlage A), databases/migraties, auth en API-sleutels, events/meldingen, back-up/herstel,
  bewaking, noodstop en invarianten, CI en de echt gedraaide baselinetests, images en installatiewijze.
- [`DELTA_MATRIX.md`](DELTA_MATRIX.md) — elke eis uit B04, de I-/A-stappen en contracten 1.1 (gemeten op 2026-11-09; voor 1.3 geldt de conformiteitslijst) als
  bestaand / onvolledig / ontbrekend met bewijs, wat NIET mag, Hub-adapterkaart en de bouwlijst voor fase P3.
- [`EVIDENCE.json`](EVIDENCE.json) — status per Excel-ID (`B04-001` … `B04-010`) volgens contract §11.
- Besluiten: [`../adr/ADR-0013-platform-v2-p0-interpretaties.md`](../adr/ADR-0013-platform-v2-p0-interpretaties.md).
- Tweede lezing (onafhankelijke controle, 2026-11-09): `DELTA_MATRIX.md` §K *Controle (tweede lezing)* —
  gecorrigeerde rijen, aangevuld bewijs, nieuwe rijen en bouwlijstcorrecties; besluiten in
  [`../adr/ADR-0014-platform-v2-tweede-lezing.md`](../adr/ADR-0014-platform-v2-tweede-lezing.md).
- Reproduceren: `.venv/bin/python scripts/platform-v2-routes.py [--json | --probe]` (alleen lezen, tijdelijke map).

## B03 Universele mediaserver (2026-11-09)

- [`B03_DELTAVERZOEKEN.md`](B03_DELTAVERZOEKEN.md) — wijzigingsverzoeken van B03 aan MediaGuard (Δ-MG-AUTH per server,
  Δ-MG-DUAL voor Emby én Jelly Leon, Δ-MG-1…8, Δ-MG-DV, Δ-CONTRACT-MG) met besluit en status, en de HARDE EIS van de
  gebruiker als releasevoorwaarde (upgrade op een gevulde database van de vorige versie zonder gegevensverlies).
- [`EXCEL_TAKEN.json`](EXCEL_TAKEN.json) bevat naast B04 de B03-rijen en JLT-testen waarvoor MediaGuard iets levert.
