# MSO-contracttest

`routes.json` bevat de ID, methode, het pad en de minimale rol van elke route uit `ROUTES` in de gepinde MSO-bron. De test vergelijkt die lijst met de JSON-uitvoer van `scripts/platform-v2-routes.py --json`, op een tijdelijke MediaGuard-app. Een ontbrekende methode of route en een strengere minimale rol laten de test falen. `ingelogd` geldt als minimaal `viewer`.

De uitzondering voor `GET /api/v1/openapi.json` vereist dat de route bestaat en beschermd blijft met `admin`. MSO gebruikt directe routeprobes als het document niet toegankelijk is. De bestaande tests in `apps/core/tests/test_api_keys.py` bewaken het gebruik van MediaGuard-sleutels door MSO.

De `core`-job draait de test op beide Python-versies. `release.yml` gebruikt diezelfde CI-workflow en kan alleen na groene tests publiceren. De test meet de routetabel en de minimale rollen. Voor de praktijktest met MSO op de NAS geldt **NIET GEVALIDEERD (echte NAS)**, STAPPEN_P6.md onderdeel N-P6-05.

## Pin bijwerken

Genereer vanuit de hoofdmap van MediaGuard met een lokale MSO-werkkopie en de gewenste volledige commit-hash. Het script leest `compat.py` uit Git en haalt de velden uit de syntaxisboom.

```sh
python3 tests/contract/mso/genereer_routes.py '<MSO-werkkopie>' '<MSO-commit>' --datum '<JJJJ-MM-DD>'
```

Controleer de uitvoer en neem die over als `tests/contract/mso/routes.json`. Draai daarna vanuit `apps/core` de contracttest met de ontwikkelomgeving van MediaGuard.

```sh
../../.venv/bin/python -m pytest -q -p no:cacheprovider ../../tests/contract/mso
```
