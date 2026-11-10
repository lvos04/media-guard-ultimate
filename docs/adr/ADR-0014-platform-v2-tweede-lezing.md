# ADR-0014 — Tweede lezing P0: download-opruimer, `mso-*`-categorieën en migratienummering

- **Status:** geaccepteerd (alleen documentatie; geen productcode gewijzigd)
- **Datum:** 2026-11-09
- **Fase:** P0, controle van de delta-matrix
- **Hoort bij:** [`DELTA_MATRIX.md` §K](../platform-v2/DELTA_MATRIX.md) en [ADR-0013](ADR-0013-platform-v2-p0-interpretaties.md)

## Context

De tweede lezing van de delta-matrix (DELTA_MATRIX.md §K) vond drie punten waarbij bestaand 1.2.2-gedrag
en de platformdocumenten elkaar raken. Geen van deze punten stond in de eerste lezing of in ADR-0013.

1. **Download-opruimer zonder poolcontrole.** De download-opruimer (`apps/core/mediaguard/downloads/janitor.py`) staat
   standaard aan (`downloads.cleanup_enabled = true`, `:59`). Hij werkt zo:
   - hij verplaatst achtergebleven downloads naar een prullenbak;
   - na 7 dagen verwijdert hij ze definitief, of eerder bij < 5 % vrije ruimte (`:64`, `:367-378`);
   - hij slaat over bij een noodstop (`:602-607`), maar kijkt niet naar de poolstatus.

   Het opruimen van quarantaine-originelen pauzeert wél bij een gemeten degraded pool (`recovery/api.py:151`).

   Twee teksten in `01_ARCHITECTUUR/03_RISICO_AUTONOMIE.md` (Speciale blokkades) raken dit:
   - "Bij gedegradeerde ZFS-pool … geen mutaties aan opslag of massale mediaoperaties";
   - "Geen automatisch verwijderen van torrents, downloads, … ; MSO/MediaGuard eigen veiligheid blijft leidend".
2. **`mso-*`-categorieën.** De download-opruimer behandelt ook de downloadcategorieën `mso-<categorie>` als beheerd
   (`janitor.py:451-462`). MSO stuurt herstel-downloads standaard met die categorie
   (`media-source-optimizer/apps/core/mso/features/recovery/actions.py:36`, alleen gelezen).
   `02_DATA_EIGENAARSCHAP.md` zegt over de wachtrij van qBittorrent/SABnzbd: "geen derde schrijver zonder
   conflictbeleid". Een conflictbeleid tussen MediaGuard en MSO is nergens vastgelegd, en er is geen test met MSO.
3. **Migratienummering.** De bouwlijst noemde `140_api_key_scopes.sql`. In `apps/core/mediaguard/migrations/README.md`
   zijn de reeksen 001–139 aan domeinen toegewezen; 140 hoort bij geen enkel domein. API-sleutels horen bij de kern
   (001–019, `004_api_keys.sql`).

## Besluiten

1. **De download-opruimer blijft zoals hij is**, en P0 en P3 zetten hem niet uit. Hij is "eigen veiligheid van
   MediaGuard" in de zin van 03_RISICO: bestaand vangnet, achter de noodstop, terug te zetten via de prullenbak, en
   met audit. Twee grenzen gelden:
   - De Hub stuurt de opruimer nooit aan (DELTA_MATRIX §H.3).
   - Een **gemeten** degraded pool moet hem wel stilzetten. Dat wordt het nieuwe hiaat **P3-14**: bij degraded niets
     definitief verwijderen en niets nieuws naar de prullenbak verplaatsen, plus een melding.

   Bij een onbekende poolstatus (geen TrueNAS-sleutel) blijft het gedrag ongewijzigd. Dat valt onder P3-13 en krijgt
   een eigen ADR, zoals ADR-0013 §9 al vastlegt.
2. **`mso-*` blijft zoals het is, tot er een afspraak is.** Dit is de veiligste interpretatie: niets stil uitzetten
   (dan blijven MSO-herstelresten onbeperkt liggen) en niets uitbreiden. Het punt is gemeld als contractissue bij de
   contracteigenaar (`ai-server-manager`) en bij MSO. In de afspraak moet staan:
   - wie `mso-*` opruimt;
   - na welke termijn;
   - hoe MSO aangeeft dat iets nog in gebruik is.
3. **Migraties volgen de README.** Voor de scopes op API-sleutels (P3-02) komt het bestand `005_api_key_scopes.sql`
   (kern), of er wordt eerst een nieuwe reeks vastgelegd in `migrations/README.md`. Het nummer 140 zonder reeks
   vervalt. Dit geldt ook voor de idempotency-tabel van P3-10.
4. **Bestaande mechanismen worden niet opnieuw gebouwd** (DELTA_MATRIX §K.2/§K.5):
   - pauze met eigen voorvoegsel (`apply_playback_hold`);
   - de CPU-, temperatuur- en tijdvenstergrens van de engine;
   - correlatie core↔engine;
   - de herstelset van vóór een update;
   - de offsite-kopie van de herstelbundel;
   - de bestaande contractdocs (`packages/contracts/`).

   P3 sluit daarop aan.

## Gevolgen

- De bouwlijst krijgt P3-14 (P1). P3-02, P3-03, P3-04, P3-05, P3-06, P3-09, P3-10 en P3-12 worden aangescherpt
  volgens §K.5.
- Contractissues voor de contracteigenaar:
  - het conflictbeleid voor de downloadcategorieën (`mso-*`);
  - de status van de offsite-kopie, die alleen voor admin leesbaar is: met een API-sleutel kan de Hub
    "ontbrekende externe back-up" dus niet meten tot P3-06 er is.
- Dit besluit wordt herzien wanneer MSO en MediaGuard een conflictbeleid voor downloadcategorieën vastleggen, of
  wanneer P3-13 de poolstatus van de Hub als invoer krijgt.
