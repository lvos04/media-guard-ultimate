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

## Eigen werkstukken van deze repo (fase P0, 2026-11-09)

- [`INVENTARIS.md`](INVENTARIS.md) — baseline 1.2.2 en compat-matrix (B04-001), diensten, modules, alle API-routes
  met `bestand:regel` (bijlage A), databases/migraties, auth en API-sleutels, events/meldingen, back-up/herstel,
  bewaking, noodstop en invarianten, CI en de echt gedraaide baselinetests, images en installatiewijze.
- [`DELTA_MATRIX.md`](DELTA_MATRIX.md) — elke eis uit B04, de I-/A-stappen en contracten 1.1 als
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
