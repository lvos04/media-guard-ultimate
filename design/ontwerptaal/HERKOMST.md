# Herkomst van `design/ontwerptaal/` (O-14)

Versie-gepinde kopie van de ontwerptaal **Peil 1.1.1** (10 november 2026). Bron van waarheid (contract 1.3 §13):
`lvos04/ai-server-manager` → `docs/platform-v2/ONTWERPTAAL.md` + `design/ontwerptaal/` + `docs/adr/ADR-0015-ontwerptaal-peil.md`,
gepind op **commit `0a7143bc0a94ed5317a9655fa785e73a1fb07ba1`** (branch `ccr-c133d7a0-kuz1cv`).

De machineleesbare pin staat in [`HERKOMST.json`](HERKOMST.json): versie, bronrepo, bron-commit en per kopie het bronpad,
de sha256 van het bronbestand, het aantal regels herkomstkop en of de pin- en driftregel E08 streng geldt.

**Status in MediaGuard:** de web-UI gebruikt `ontwerptaal.css` en `aliaslagen/mediaguard-mso.css` via de byte-gelijke
kopie in de Docker-context (`apps/web/styles/peil/`, zelfde inhoud en kop met dezelfde commit en sha256). De Android-app
genereert zijn kleuren en typografie uit `tokens.json` (`apps/android/tools/peil_tokens.py` → `PeilTokens.kt`, met de
sha256 in de kop; `PeilTokensTest` en de CI-stap `peil_tokens.py --check` controleren ze, ADR-0018). Sinds 1.1.1 zet de
gedeelde aliaslaag zelf Peils focusring en scroll-padding terug na `mg.css` (E14); de eigen kopie daarvan in
`apps/web/styles/peil-mg.css` is weg (ONTWERPTAAL §13.3). Overstap van 1.1.0 naar 1.1.1: ADR-0021.

| Kopie | Bron (`ai-server-manager@0a7143b`) | Gebruik in MediaGuard | Kop | E08 streng |
|---|---|---|---|---|
| `ontwerptaal.css` | `design/ontwerptaal/ontwerptaal.css` | web, via `apps/web/styles/peil/ontwerptaal.css` | 2 regels | ja |
| `aliaslagen/mediaguard-mso.css` | `design/ontwerptaal/aliaslagen/mediaguard-mso.css` | web, via `apps/web/styles/peil/mediaguard-mso.css` (laag `legacy`, na `mg.css`) | 2 regels | ja |
| `tokens.json` | `design/ontwerptaal/tokens.json` | Android (`PeilTokens.kt`) | geen (byte-gelijk) | ja |
| `components.json` | `design/ontwerptaal/components.json` | web: basis voor `apps/web/components.json` | geen (byte-gelijk) | ja |
| `ONTWERPTAAL.md` | `docs/platform-v2/ONTWERPTAAL.md` | regels (bindend; MediaGuard §13.3) | 2 regels | nee (documentatie) |
| `COMPONENTEN.md` | `design/ontwerptaal/COMPONENTEN.md` | componentspecificaties | 2 regels | nee (documentatie) |
| `ADR-ONTWERPTAAL.md` | `docs/adr/ADR-0015-ontwerptaal-peil.md` | besluit en alternatieven | 2 regels | nee (documentatie) |

- Een kop van **2 regels** noemt `Peil 1.1.1`, `lvos04/ai-server-manager@<commit> <bronpad>` en de sha256 van het
  bronbestand; de rest is byte-gelijk aan de bron op die commit. Relatieve links in de `.md`-kopieën wijzen naar de
  bronrepo en werken hier niet.
- **Pin- en driftregel (ONTWERPTAAL §14, E08):** fout bij (1) een afwijking van de gepinde inhoud en (2) bij een strenge
  kopie: dezelfde Peil-versie met andere inhoud in de bron. Een nieuwere bronversie is alleen een waarschuwing: bijwerken
  is een bewuste stap. Bij de documentatie geeft elke latere bronwijziging alleen een waarschuwing (ADR-0021).
- Niet gekopieerd (niet nodig in deze repo): `prototypes/`, `shots/`, `bouw/`, `controle/`, `contrast.json`,
  `statuspagina.*` (MediaGuard heeft geen platform-statuspagina) en de aliaslagen van my_house en de Hub.

## Controleren en bijwerken

```sh
python3 scripts/kopie-herkomst.py controleer                       # offline: koppen, sha256 en HERKOMST.json
python3 scripts/kopie-herkomst.py vergelijk --online               # tegen GitHub: gepinde commit + werkbranch
python3 scripts/kopie-herkomst.py vergelijk --bron ../ai-server-manager
(cd apps/web && npm run check:peil)                                # kopie in de Docker-context
python3 apps/android/tools/peil_tokens.py --check                  # Android-tokens
```

Bijwerken naar een nieuwe Peil-versie: `python3 scripts/kopie-herkomst.py bijwerken --set peil --bron <checkout> --commit
<40 hex> --ref <branch>`, daarna `python3 apps/android/tools/peil_tokens.py`, de pin in `PeilTokensTest`,
`check-peil.mjs` (`PEIL_VERSION`) en `apps/core/tests/test_kopie_herkomst.py`, en opnieuw de web-tests (axe, contrast,
Peil-matrix) en de Android-tests. De CI-job `kopieen` (`.github/workflows/ci.yml`) draait `controleer` en
`vergelijk --online`; `apps/core/tests/test_kopie_herkomst.py` draait in de core-job.
