# Herkomst van `design/ontwerptaal/` (O-14)

Versie-gepinde kopie van de ontwerptaal **Peil 1.1.0** (9 november 2026, design-agent; goedgekeurd na de onafhankelijke
ontwerpreview 1.0.0 → 1.1.0). De bron van waarheid wordt na de overdracht
`ai-server-manager/docs/platform-v2/ONTWERPTAAL.md` + `ai-server-manager/design/ontwerptaal/` (contract §13). Zolang die
commit er niet is, staat de bron in de map `definitief/` van de ontwerptaal (buiten de repos).

**Status in MediaGuard:** sinds stap 3 (fase 1a) gebruikt de web-UI `ontwerptaal.css` en
`aliaslagen/mediaguard-mso.css`. Omdat de Docker-context van het web-image `apps/web` is, staat daar een tweede,
byte-gelijke kopie: `apps/web/styles/peil/ontwerptaal.css` en `apps/web/styles/peil/mediaguard-mso.css` (eigen
herkomstkop van 2 regels, zelfde sha256). `npm run check:peil` (ook in CI, job `web`) controleert dat die kopieën hun
sha256 halen en gelijk zijn aan deze map. Sinds stap 4 genereert de Android-app zijn kleuren en typografie uit
`tokens.json` (`apps/android/tools/peil_tokens.py` → `PeilTokens.kt`, met de sha256 in de kop); `PeilTokensTest` en de
CI-stap `peil_tokens.py --check` (workflow `android`) vergelijken ze met dit bestand (ADR-0018). Hoe de omzetting
ging, staat in [`docs/ontwerp/PEIL_MIGRATIE.md`](../../docs/ontwerp/PEIL_MIGRATIE.md) en
[ADR-0017](../../docs/adr/ADR-0017-peil-web-bouwkeuzes.md); "voor" en "na" staan in
[`docs/screenshots/peil/`](../../docs/screenshots/peil/).

| Bestand | Gebruik in MediaGuard | sha256 van het bronbestand |
|---|---|---|
| `aliaslagen/mediaguard-mso.css` | web: na `styles/mg.css` en `styles/av1.css` geïmporteerd in de laag `legacy` (gedeeld met MSO) | `b120cf9a41357dc3cc4981db24c08ae6f557a33faa4f62512ba8f45a27124a4c` |
| `ontwerptaal.css` | web: vóór `mg.css`; Tailwind 4 `@theme` + shadcn-variabelen | `b384b3d074c7c892d8e5a8320b1bdcd3d8612d98b3a05daa9a9e6d12560d020e` |
| `tokens.json` | Android: bron van `PeilTokens.kt` (kleuren, typografie, radius; gegenereerd en getest); web: controles | `9583506d8585ba39486f75d4c27f8e40f7878104674c27e229d10f92013a30e1` |
| `components.json` | web: basis voor `apps/web/components.json` (shadcn-CLI, `css: "app/globals.css"`) | `831fdce06c6c25c7f67d23b737f27184bca9e4a0ff5c0a60175a6a58edbe4c7e` |
| `ONTWERPTAAL.md` | regels (bindend; MediaGuard: §13.3) | `a957370b1eceac49ab9f47204c252a67d15967ebae240eb33440b8963898d33b` |
| `COMPONENTEN.md` | componentspecificaties (handoff) | `d0b0999d8f07537fcc4447d30ab5a3d45632d559487736082beae8a3c28b0154` |
| `ADR-ONTWERPTAAL.md` | het besluit en de alternatieven | `df302eb5a727b5649dafec1c0c0d469cf5bfc39fd32b3834c415aaa805db05ac` |

- `.css`- en `.md`-bestanden hebben bovenaan een **herkomstkop van 2 regels**. De rest is byte-gelijk aan de bron.
  `tokens.json` en `components.json` kunnen geen kop dragen en zijn helemaal byte-gelijk.
- Zelfde indeling, koppen en sha256 als de kopieën in `media-source-optimizer` en `my_house` (zelfde bronbestanden).
  `ontwerptaal.css` en `aliaslagen/mediaguard-mso.css` zijn na de kop gelijk aan die in MSO; MSO gebruikt ze al.
- Niet gekopieerd (te groot of niet nodig in deze repo): `prototypes/`, `shots/`, `bouw/`, `controle/`, `contrast.json`,
  `statuspagina.*` (MediaGuard heeft geen platform-statuspagina) en de aliaslagen van my_house en de Hub. De besluiten van
  de gebruiker (`BESLUITEN_GEBRUIKER.md`, 9 november 2026) staan samengevat in `docs/ontwerp/PEIL_MIGRATIE.md` §1.

## Controleren

```sh
cd design/ontwerptaal
for f in aliaslagen/mediaguard-mso.css ontwerptaal.css ONTWERPTAAL.md COMPONENTEN.md ADR-ONTWERPTAAL.md; do
  echo "$(tail -n +3 "$f" | sha256sum | cut -d' ' -f1)  $f"; done
sha256sum tokens.json components.json
```

De uitkomst moet gelijk zijn aan de tabel. Een CI-stap die dit vergelijkt met de versie in `ai-server-manager` komt
pas als die bron gecommit is (ONTWERPTAAL §14). Tot dan is dit een handmatige controle.
