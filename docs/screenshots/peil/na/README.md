# Schermafdrukken "na" — MediaGuard-web na de omzetting naar Peil

Meting **na** stap 3 van de omzetting ([`docs/ontwerp/PEIL_MIGRATIE.md`](../../../ontwerp/PEIL_MIGRATIE.md) §13,
[ADR-0017](../../../adr/ADR-0017-peil-web-bouwkeuzes.md)). Gemaakt op 9 november 2026 tegen de **echte app** op
code-HEAD `ab7bce8` (plus een kleine latere wijziging in `components/av1/workers.tsx` die in geen van deze afdrukken
zichtbaar is): de echte core (`python -m mediaguard`, verse datamap, `MG_START_BACKGROUND=0`) en de echte
productiebuild van de web-UI (`npm run build` → `node server.mjs`), beide alleen op 127.0.0.1. Chromium van
Playwright, `nl-NL` (en-US voor de `en-us-*`-afdrukken), `Europe/Amsterdam`, schaal 1.

**Zelfde omgeving, zelfde script als "voor".** Dezelfde testgegevens (nep-Sonarr/Radarr uit de coretests, fictieve
titels, twee quarantainerijen, gebruikers `beheerder`, `kijker` en `engels`), dezelfde schermen, breedtes,
nabootsingen en metingen als [`../voor/`](../voor/README.md) — zie daar voor de uitleg van elke nabootsing en de
grenzen ervan. Er draait geen AV1-engine, Seerr of recovery-worker (echte toestand van een verse installatie).

| Map | Inhoud | Aantal |
|---|---|---|
| `schermen/` | 30 schermen × 360/390/768/1440 × licht/donker, volledige pagina | 240 |
| `tabs/` | zelfde tabs als "voor" × 390/1440 × licht/donker | 96 |
| `toestanden/` | zelfde toestanden als "voor" (102) + **nieuw**: "Terug naar de Hub" op Overzicht (1440) en in het profielmenu (390) × licht/donker | 106 |
| `metingen.json` | per afdruk dezelfde meting als "voor" (overloop, doelen, tekstmaten, lettertypes, tonen, grafieken, axe 4.14 met `wcag2a/aa/21a/21aa/22aa`, JS-fouten) | 442 |
| `toetsenbord.json` | Tab-doorloop (40 stops) op 5 schermen × 390/1440 × licht/donker | 20 |

Bestandsnaam: `<scherm of toestand>-<licht|donker>-<breedte>.png` (zelfde namen als "voor", dus direct te vergelijken).
Android valt buiten deze stap: er zijn hier geen Android-afdrukken.

## Voor en na (uit beide `metingen.json`, `python3 docs/screenshots/peil/na/vergelijk.py`)

| Meting | Voor | Na |
|---|---|---|
| Afdrukken (gelukt / mislukt) | 438 / 0 | 442 / 0 |
| Afdrukken met horizontale overloop (t.o.v. apparaatbreedte) | 0 | 0 |
| Schermen 1440: doelen < 44 px / alle doelen | 1694 / 1900 | 170 / 1894 |
| Schermen 360: doelen < 44 px / alle doelen | 352 / 1644 | 96 / 1576 |
| Schermen 1440: tekstelementen < 14 px (kleinste) | 3894 (kleinste 10 px) | 0 |
| Schermen 360: tekstelementen < 14 px (kleinste) | 3313 (kleinste 10 px) | 0 |
| axe serious/critical (afdrukken × regel) | {"label-content-name-mismatch": 422, "nested-interactive": 16, "target-size": 12, "scrollable-region-focusable": 4, "aria-valid-attr-value": 8, "color-contrast": 6, "button-name": 2} | {} |
| JS-fouten | 0 | 0 |
| Lettertypes | {"inter": 438, "mono": 168} | {"system-ui": 442, "ui-monospace": 168} |
| 1440: pauze-iconen | 38 | 18 |
| 1440: groene stippen (dot--success) | 152 | 0 |
| 1440: circle-check-iconen (alleen nog Gezond) | 260 | 18 |
| 1440: KPI-waarden in statuskleur-klasse (success/warning/danger) | 52 | 6 |

Uitleg bij de tabel:
- **Doelen < 44 px** die overblijven (voorbeelden in `metingen.json`): selectievakjes en keuzerondjes (het label is
  het raakvlak), vaktermen in lopende tekst (inline-uitzondering van WCAG 2.5.8) en een paar losse tekstlinks
  (`back-link`, `link-arrow`, links in een label-pil). Die laatste groep is een vervolgpunt (ADR-0017).
- **Pauze-iconen (18):** de meting telt `circle-pause` mee. Dat is nu het Peil-icoon van **Gepauzeerd** (noodstop
  actief, ADR-0016 punt 9) en de actieknop "Pauzeren" bij een instantie of worker. Onbekend heeft nooit een
  pauze-icoon (gecontroleerd in `apps/web/e2e/peil.spec.ts`).
- **circle-check (18)** staat alleen nog bij gemeten gezondheid (koppelingen, componenten, geslaagde vrijgavecontrole).
- **KPI-statusklassen (6):** de klasse blijft als haak, maar de waarde staat in inkt (`peil-mg.css` regel f).
- **Toetsenbord:** op alle 20 doorlopen heeft elke tabstop een ring van 3 px met halo (`toetsenbord.json`
  `focusstijlen`) en is geen enkele stop bedekt door de kopbalk of ondernavigatie (`bedekt_door_vaste_balk` leeg).
- **Grafieken:** zonder AV1-engine tonen Ruimte en Systeem lege toestanden (zoals "voor"). Tabel, toetsenbord en
  "Geen meting" zijn getest met een nagebootste engine in `apps/web/e2e/peil.spec.ts` (Besparing per dag).

## Eerlijk over deze afdrukken

- Alles wat bij "voor" staat onder *Eerlijk over deze afdrukken* geldt hier ook (nagebootste toestanden, vaste balken
  in volledige-pagina-afdrukken, overloop t.o.v. de apparaatbreedte, PNG's verkleind naar 256 kleuren: meet **nooit**
  contrast in deze afdrukken).
- **Hub-link:** voor `toestanden/hub-link-*` is `system.hub_url` via de bestaande settings-API tijdelijk op het
  fictieve testadres `http://hub.example:30080` (RFC 2606) gezet en daarna weer leeggemaakt. Dat is geen NAS-gegeven.
- **NIET GEVALIDEERD:** echte schermlezer (NVDA/TalkBack), 200 %-zoom, forced colors, echte telefoon, tv/speler,
  echte NAS.

**Achteraf bijgewerkt op 10 november 2026** voor de openbare repository. De webafdrukken zijn niet opnieuw gemaakt,
maar een paar tekstregels zijn in de PNG's opnieuw getekend in hetzelfde lettertype; de rest van elke afdruk is
ongewijzigd. Het ging om:
- Systeem: het pad van de werkruimte op de testmachine is nu `/config` (de standaard van `MG_DATA_DIR`);
- Koppelingen › Mediaserver: het voorbeeldadres is nu `nas.lan` (stond op een echt LAN-adres);
- Release Lab: de placeholder noemt nu "Lichtval";
- Anime › Releasenaam ontleden: de placeholder noemt nu "Golfbreker".

## Opnieuw maken

Zoals bij "voor" (zie [`../voor/README.md`](../voor/README.md)), met de scripts uit deze map:

```sh
docs/screenshots/peil/voor/hulp/start.sh
MGP_DATA=$MGP_WERK/data $MGP_PY -I docs/screenshots/peil/voor/hulp/vul.py
node docs/screenshots/peil/na/maak-afdrukken.mjs alles     # schermen | tabs | toestanden | noodstop | hub
node docs/screenshots/peil/na/toetsenbord.mjs
python3 docs/screenshots/peil/na/verklein.py
python3 docs/screenshots/peil/na/vergelijk.py               # tabel hierboven
docs/screenshots/peil/voor/hulp/stop.sh
```

De scripts zijn alleen-lezen hulpmiddelen voor deze documentatie; ze horen niet bij de app en worden niet gebouwd.
