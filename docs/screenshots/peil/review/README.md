# Schermafdrukken en metingen van de Peil-review (stap 5)

Bij [`docs/ontwerp/PEIL_REVIEW.md`](../../../ontwerp/PEIL_REVIEW.md) en [ADR-0019](../../../adr/ADR-0019-peil-review-correcties.md).
Gemaakt op 9 november 2026 tegen de **echte app** na de reviewcorrecties: de echte core (`python -m mediaguard`, verse
datamap, `MG_START_BACKGROUND=0`) en de echte productiebuild van de web-UI (`npm run build` → `node server.mjs`), alleen
op 127.0.0.1, met de testgegevens van [`../voor/`](../voor/README.md) (nep-Sonarr/Radarr uit de coretests, fictieve
titels, twee quarantainerijen, gebruikers beheerder/kijker/engels). Chromium van Playwright 1.63, `nl-NL` (en-US voor
`en-us-*`), `Europe/Amsterdam`, `prefers-reduced-motion: reduce`, schaal 1.

| Map / bestand | Inhoud | Aantal |
|---|---|---|
| `afdrukken/` | 13 hoofdschermen × 360/390/768/1440 × licht/donker (volledige pagina) | 104 |
| `toestanden/` | core onbereikbaar (Overzicht, Ruimte, Activiteit), verouderd (Bibliotheek, Activiteit), AV1-engine actief (Overzicht, Ruimte, Systeem), en-US (Overzicht, Ruimte met engine), kijker — elk 390/1440 × licht/donker; plus "Terug naar de Hub" (`hub-link-donker-1440`: zijbalk, `hub-link-licht-390`: profielmenu, `hub-link-licht-1440`: stand na weer leegmaken, dus zonder link) en de grafieken met tabel en meetgat (`grafiek-*-tabel-*`) | 44 + 3 + 4 |
| `review.json` | per paginaweergave de eigen meting (zie hieronder) + Tab-doorlopen + toegestane toevoegingen | 384 + 44 + 36 + 1 |
| `review.mjs` | het meetscript (Playwright + axe-core) | |
| `knijp.mjs` | zoekt tekst die per letter breekt (geknepen kolommen) | |
| `teksten.mjs` | NL/en-US-teksten tegen de Peil-woordenlijst | |
| `overnemen.py` | uitvoer overnemen: PNG's naar 256 kleuren, ntfy-onderwerp uit `review.json` weg | |

Bestandsnaam: `<scherm of toestand>-<licht|donker>-<breedte>.png`.

## Wat `review.json` per paginaweergave bevat

`breedte`, `overloop` (px t.o.v. de apparaatbreedte) en `uitstekers`; `tekst_n`, `tekst_te_laag` (contrast < 4,5:1,
groot < 3:1, gemeten in de pagina met opacity, alfa en arcering), `laagste_tekst`, `tekst_klein` (< 14 px),
`uitgeschakeld_laag`; `randen_te_laag` (velden/schakelaars < 3:1); `doelen_n`, `doelen_klein` (< 44 × 44 px),
`doelen_in_zin` (uitgezonderd); `links_zonder_streep`; `groen` (healthy-tokenkleur met context), `andere_groen`,
`grafiekslot` (aqua van chart-3, geen status); `status_vorm` (aantal, fouten, soorten: status:icoon:woord); `ellips`;
`h1`, `kopsprongen`, `dubbele_ids`; `axe.overtredingen` (alle impactniveaus) en `axe.onvolledig`; `js_fouten`;
`nagebootst` (welke API-antwoorden zijn onderschept). Tab-doorlopen (`toetsenbord/…`): aantal stops, eerste stop,
stops zonder ring, ring < 3:1, (deels) bedekt door een vaste balk, laagste ringcontrast.

## Eerlijk over deze afdrukken

- PNG's zijn omgezet naar 256 kleuren: **meet nooit contrast in deze afdrukken**; contrast staat in `review.json`.
- Volledige-pagina-afdrukken tonen vaste balken (zijbalk, ondernavigatie) op de plek van het eerste beeld; dat is een
  eigenschap van de afdruk, niet van de pagina.
- Toestanden met "core onbereikbaar", "verouderd" en "AV1-engine actief" zijn **nagebootst** in de browser (Sim-getest);
  er draaide geen echte AV1-engine, Seerr of recovery-worker.
- "Terug naar de Hub" is getest met het fictieve adres `http://hub.example:30080` (RFC 2606) via de bestaande
  settings-API en daarna weer leeggemaakt. Geen NAS-gegevens.
- **NIET GEVALIDEERD:** echte schermlezer, 200 %-zoom, forced colors, echte telefoon/emulator, tv, echte NAS.

**Achteraf bijgewerkt op 10 november 2026** voor de openbare repository. De webafdrukken zijn niet opnieuw gemaakt,
maar een paar tekstregels zijn in de PNG's opnieuw getekend in hetzelfde lettertype; de rest van elke afdruk is
ongewijzigd. Het ging om:
- Systeem: het pad van de werkruimte op de testmachine is nu `/config` (de standaard van `MG_DATA_DIR`).

## Opnieuw uitvoeren

```sh
# 0. web bouwen; Python 3.12-venv met de core + testextra's (buiten de repo)
(cd apps/web && npm ci && nice -n 10 npm run build)
# 1. nep-arr + core + web starten en vullen (zie ../voor/README.md); wachtwoord alleen in de omgeving
export MGP_REPO=$PWD MGP_WERK=/pad/buiten/repo MGP_PY=/pad/naar/venv/bin/python MGP_PW="$(openssl rand -hex 16)"
docs/screenshots/peil/voor/hulp/start.sh && MGP_DATA=$MGP_WERK/data $MGP_PY -I docs/screenshots/peil/voor/hulp/vul.py
# 2. meten en afdrukken (± 50 min; PEIL_PARALLEL=3 pagina's tegelijk)
export MG_PEIL_BASE_URL=http://127.0.0.1:18200 MG_PEIL_PASSWORD="$MGP_PW" PEIL_UIT=/pad/buiten/repo/review
export PEIL_PLAYWRIGHT=$PWD/apps/web/node_modules/playwright/index.mjs PEIL_AXE=$PWD/apps/web/node_modules/axe-core/axe.min.js
nice -n 10 node docs/screenshots/peil/review/review.mjs alles      # of: audit | afdrukken | toetsenbord | toestanden | toevoegingen
node docs/screenshots/peil/review/knijp.mjs "/,/bibliotheek,/ontbrekend,/kalender,/quarantaine,/instellingen/gebruikers" "768,1024,1100,1280"
node docs/screenshots/peil/review/teksten.mjs "$PWD/apps/web"
python3 -I docs/screenshots/peil/review/overnemen.py "$PEIL_UIT"
docs/screenshots/peil/voor/hulp/stop.sh
```

De scripts zijn alleen-lezen hulpmiddelen voor de documentatie; ze horen niet bij de app en worden niet gebouwd.
