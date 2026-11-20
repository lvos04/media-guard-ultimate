# Dekking van de herstelproeven

De inventaris omvat 291 aanroepen uit de reviewronden 2–7. Gelijke aanroepen delen een regel. De kolom met de ronde verwijst naar het oorspronkelijke gebruik. De letters verwijzen naar de voorwaarden in `docs/DATA_RECOVERY.md` §8.1. A bindt de poging en fase vóór de bestandswijziging. B vergelijkt de gecommitteerde stand. C onderscheidt een commit van open schrijfwerk. D leest op kopieën.

De CI-taak voor historische releases gebruikt de volledige Git-historie en draait dezelfde proeven. Voer vanuit de repository met de kernomgeving `python -m pytest -q tests/upgradehistorie` uit. De proeven gebruiken de vastgelegde Git-objecten uit de lokale historie en eigen tijdelijke databases. De kernsuite draait vanuit `apps/core`. De imagepoort levert het rapport voor `python tests/upgradehistorie/controleer_rapport.py lokaal.md ci.md`. Een historische negatieve tegenproef kan op haar oorspronkelijke bron het oude probleem tonen. De kandidaat moet de behoudseis halen. De historische wissel zonder voorafgaand bewijs wacht ook bij een leesbare vorige stand op een handmatige keuze.

| Oorspronkelijke proef | Reviewronde | Ontwerpregel | Vaste proef of poort |
|---|---|---|---|
| `aangewezen-bewijs` | 3 | A, B | `schrijver` |
| `aangewezen-set` | 3 | A, B | `schrijver` |
| `aanmeldslot` | 4 | Zelfstandige rechtenproef | `rechten` |
| `aanvullend deelcheckpoint` | 8 | B, C, D | `deelcheckpoint zonder-checkpoint / deelcheckpoint / intact / latere-wal / nieuwe-wal` |
| `aanvullend historische-keuze` | 8 | A, B, D | `historische-keuze stap-0 / actief-mediaguard.db / onvolledige-encoder` |
| `archieffout` | 2, 3, 4, 5, 6, 7 | A, B | `proeven archieffout` |
| `archiefrotatie` | 2, 3 | A, B | `archiefrotatie` |
| `archiefrotatie PROEF_ARCHIEFKLOK=systeem` | 2, 3 | A, B | `archiefrotatie systeem` |
| `archiefrotatie-basis` | 3 | A, B | `archiefrotatie` |
| `archiefsleutel` | 5, 6, 7 | A, D | `sleutel-herstelset, sleutel-afgebroken` |
| `archiefstop` | 5, 6, 7 | A, D | `sleutel-herstelset, sleutel-afgebroken` |
| `basis-encoderfase` | 4, 5 | A, B | `encoderfase` |
| `basis-omwisselgrens` | 6 | A–D | `omwisselgrens` |
| `basis-opslagplaats` | 5 | A, D | `opslaggrens historisch (voor-wissel)` |
| `basis-oud-beheer` | 5 | A, B | `oude-keuze (voor-wissel)` |
| `basis-oude-keuze` | 4 | A, B | `oude-keuze (voor-herstelschrijver)` |
| `basis-oude-start` | 6 | A–D | `publicatiegrens` |
| `basis-sleutelherstart` | 5 | A, D | `onderbreking historisch (voor-wissel)` |
| `basis-sleuteluitval` | 4 | A, D | `onderbreking historisch (voor-herstelschrijver)` |
| `basis-sqlite archiefkeuze` | 7 | A, B, D | `archiefkeuze` |
| `basis-sqlite archiefkeuze wal` | 7 | A, B, D | `archiefkeuze` |
| `basis-sqlite lees-vorige` | 7 | B, C, D | `lees-vorige` |
| `basis-sqlite lees-vorige oorzaak` | 7 | B, C, D | `lees-vorige` |
| `basis-sqlite vorige-stand` | 7 | A–D | `vorige-stand` |
| `basis-sqlite vorige-stand half` | 7 | A–D | `vorige-stand` |
| `basis-transactie` | 7 | B, C, D | `open-transactie` |
| `basis-transactie herstart` | 7 | B, C, D | `open-transactie` |
| `basisregressie` | 4 | A–D | `volledige kernsuite` |
| `behoud` | 2, 3 | B | `proeven behoud` |
| `bekende-setnaam` | 6, 7 | A, B | `oude-keuze-op-naam` |
| `beschadigingsmatrix` | 7 | A–D | `kijkpunten, beschadigingsmatrix` |
| `bewijsnorm` | 5, 6, 7 | Poort | `test_evidence_contract.py en integrale proef EX46 in S-P6-19` |
| `bewijsstatus` | 2, 3, 6, 7 | Poort | `test_evidence_contract.py en integrale proef EX46 in S-P6-19` |
| `bouw` | 2, 3 | Poort | `bouw van de upgrade-kandidaat` |
| `branche` | 4, 5, 6, 7 | Poort | `git status, git diff, git merge-base` |
| `bron` | 2 | Poort | `git status, git diff, git merge-base` |
| `bronnen` | 4, 5, 6, 7 | Poort | `git status, git diff, git merge-base` |
| `ci` | 2, 3, 4, 5, 6, 7 | Poort | `CI-run en controleer_rapport.py` |
| `ci-taken` | 7 | Poort | `CI-run en controleer_rapport.py` |
| `ci-upgrade` | 5, 6, 7 | B, C | `scripts/upgradebewijs.py poort, controleer_rapport.py` |
| `ci-vergelijk` | 4 | Poort | `CI-run en controleer_rapport.py` |
| `crashmatrix` | 6, 7 | A–D | `kijkpunten, beschadigingsmatrix` |
| `cursor` | 2, 3 | Zelfstandige API-proef | `test_connector_events.py, test_openapi_export.py` |
| `eerder-veilig` | 4, 5, 6, 7 | Zelfstandige rechtenproef | `rechten` |
| `eigen archiefkeuze` | 6 | A, B, D | `archiefkeuze` |
| `eigen beschadigingsmatrix` | 6 | A–D | `kijkpunten, beschadigingsmatrix` |
| `eigen omwisselgrens` | 5 | A–D | `omwisselgrens` |
| `eigen oude-keuze-op-naam` | 5 | A, B | `oude-keuze-op-naam` |
| `eigen startgrens` | 5 | A–D | `publicatiegrens` |
| `eigen startgrens encoderdienst` | 5 | A, B | `startgrens encoderdienst` |
| `eigen vorige-stand` | 6 | A–D | `vorige-stand` |
| `eigen vorige-stand intact` | 6 | A–D | `vorige-stand` |
| `eigen walgrenzen nieuw half zonder-slotcommit` | 7 | A–D | `walgrenzen` |
| `eigen walgrenzen nieuw half zonder-wal` | 7 | A–D | `walgrenzen` |
| `eigen walgrenzen oorsprong intact` | 7 | A–D | `walgrenzen` |
| `eigen walgrenzen oorsprong zonder-wal` | 7 | A–D | `walgrenzen` |
| `eigen walgrenzen oorsprong zonder-wal expliciet` | 7 | A–D | `walgrenzen` |
| `eigen walgrenzen oud half intact` | 7 | A–D | `walgrenzen` |
| `eigen walgrenzen oud half zonder-slotcommit` | 7 | A–D | `walgrenzen` |
| `eigen walgrenzen oud half zonder-wal` | 7 | A–D | `walgrenzen` |
| `eigen walgrenzen oud volledig zonder-slotcommit` | 7 | A–D | `walgrenzen` |
| `eigen walgrenzen oud volledig zonder-wal` | 7 | A–D | `walgrenzen` |
| `eindcontrole.sh` | 3 | Poort | `eindrapport met exitcode en slotregel` |
| `eindstand` | 4, 5, 6, 7 | Poort | `git status, git diff, git merge-base` |
| `encoderdienst` | 6, 7 | A, B | `startgrens encoderdienst` |
| `encoderfase` | 4, 5, 6, 7 | A, B | `encoderfase` |
| `gelijktijdig` | 2, 3 | B | `proeven gelijktijdig` |
| `generaties` | 2, 3 | B | `test_event_herstel.py, test_event_leesposities.py` |
| `gitleaks git --log-opts=2393f52..ac5a8f0 --redact --no-banner kopie-r3` | 3 | Poort | `git diff --check en gitleaks` |
| `herlezing-rechten` | 4 | Zelfstandige rechtenproef | `rechten` |
| `herstel-122` | 4 | A, B | `terugval122` |
| `herstelbereik` | 7 | Poort | `git status, git diff, git merge-base` |
| `hersteltests` | 2, 3 | Zelfstandige API-proef | `test_migration.py, test_connector_events.py, test_openapi_export.py` |
| `historie` | 2, 3 | B | `test_event_herstel.py, test_event_leesposities.py` |
| `historie-523` | 2, 3 | B | `test_event_herstel.py, test_event_leesposities.py` |
| `image` | 2, 3 | B, C | `scripts/upgradebewijs.py poort, controleer_rapport.py` |
| `invoer` | 2, 3 | Zelfstandige API-proef | `test_connector_events.py, test_openapi_export.py` |
| `kijkbehoud` | 4, 5, 6, 7 | B | `proeven behoud` |
| `laatste-schrijver` | 5, 6, 7 | A, B | `schrijver` |
| `lege-pagina` | 3, 6, 7 | B | `lege-pagina` |
| `lege-pagina-060` | 3 | B | `lege-pagina` |
| `lege-pagina-basis` | 2, 3 | B | `lege-pagina` |
| `lege-pagina-eind` | 2 | B | `lege-pagina` |
| `matrix` | 7 | A–D | `kijkpunten, beschadigingsmatrix` |
| `omwisselgrens` | 6, 7 | A–D | `omwisselgrens` |
| `oorsprong` | 3 | Poort | `git status, git diff, git merge-base` |
| `open-stream` | 4, 5, 6, 7 | B | `sse-retentie` |
| `opslaggrens` | 4 | A, D | `opslaggrens, wachtend` |
| `opslagplaats` | 5 | A, D | `opslaggrens, wachtend` |
| `oud-beheer` | 5, 6, 7 | A, B | `oude-keuze` |
| `oud-beheer onderbreking` | 5, 6, 7 | A, B | `oude-keuze` |
| `oude-client` | 2, 3, 6, 7 | B | `proeven oude-client` |
| `oude-grensproef` | 4 | A, B | `test_reset_bij_openen_verliest_geen_rij_uit_nieuwere_momentopname` |
| `oude-keuze` | 4 | A, B | `oude-keuze` |
| `oude-keuze-onderbreking` | 4 | A, B | `oude-keuze` |
| `oude-supervisor` | 4, 5, 6, 7 | A, B | `terugval-gevuld terugval, grens155` |
| `ouder-eigen archiefkeuze` | 6 | A, B, D | `archiefkeuze` |
| `ouder-eigen omwisselgrens` | 5 | A–D | `omwisselgrens` |
| `ouder-eigen startgrens` | 5 | A–D | `publicatiegrens` |
| `ouder-eigen vorige-stand` | 6 | A–D | `vorige-stand` |
| `ouder-sqlite archiefkeuze wal` | 6 | A, B, D | `archiefkeuze` |
| `ouder-sqlite vorige-stand half` | 6 | A–D | `vorige-stand` |
| `ouder-transactie herstart` | 7 | B, C, D | `open-transactie` |
| `outbox-inhoud` | 3 | B, C | `scripts/upgradebewijs.py poort, controleer_rapport.py` |
| `overgang` | 2, 3 | B | `proeven terugvalcursor` |
| `rapportcontrole` | 4, 5, 6, 7 | Poort | `eindrapport met exitcode en slotregel` |
| `rapportcontrole.py` | 3 | Poort | `eindrapport met exitcode en slotregel` |
| `rapportcontrole.sh` | 2 | Poort | `eindrapport met exitcode en slotregel` |
| `regressie` | 4, 5, 6, 7 | A–D | `volledige kernsuite` |
| `releasefixture` | 7 | B, C | `scripts/upgradebewijs.py poort, controleer_rapport.py` |
| `ronde6-basis oude-start` | 7 | A–D | `publicatiegrens` |
| `ronde6-basis oude-wissel` | 7 | A–D | `omwisselgrens` |
| `ronde6-ouder archiefkeuze` | 7 | A, B, D | `archiefkeuze` |
| `ronde6-ouder archiefkeuze wal` | 7 | A, B, D | `archiefkeuze` |
| `ronde6-ouder vorige-stand` | 7 | A–D | `vorige-stand` |
| `ronde6-ouder vorige-stand half` | 7 | A–D | `vorige-stand` |
| `rotatie systeem` | 5, 6, 7 | A, B | `archiefrotatie systeem` |
| `rotatie vast` | 5, 6, 7 | A, B | `archiefrotatie` |
| `routes` | 4 | Zelfstandige API-proef | `test_connector_events.py, test_openapi_export.py` |
| `routes-wizard` | 5, 6, 7 | Zelfstandige API-proef | `test_migration.py, test_connector_events.py, test_openapi_export.py` |
| `ruimte` | 5, 6, 7 | Proefomgeving | `tijdelijke releasebronnen, geheugengrens en vrije schijfruimte` |
| `scan` | 4 | Poort | `git diff --check en gitleaks` |
| `schone-diff` | 5, 6, 7 | Poort | `git diff --check en gitleaks` |
| `schrijver` | 4 | A, B | `schrijver` |
| `sleutelherstart` | 5, 6, 7 | A, D | `onderbreking hervatten` |
| `sleutelset` | 4 | A, D | `sleutel-herstelset, sleutel-afgebroken` |
| `sleutelstop` | 4 | A, D | `sleutel-herstelset, sleutel-afgebroken` |
| `sleuteluitval` | 4 | A, D | `onderbreking hervatten` |
| `sqlite archiefkeuze` | 7 | A, B, D | `archiefkeuze` |
| `sqlite archiefkeuze wal` | 6, 7 | A, B, D | `archiefkeuze` |
| `sqlite archiefkeuze wal herstel` | 6, 7 | A, B, D | `archiefkeuze` |
| `sqlite lees-vorige` | 6, 7 | B, C, D | `lees-vorige` |
| `sqlite lees-vorige oorzaak` | 6 | B, C, D | `lees-vorige` |
| `sqlite lees-vorige op-kopie` | 6, 7 | B, C, D | `lees-vorige` |
| `sqlite vorige-stand` | 7 | A–D | `vorige-stand` |
| `sqlite vorige-stand half` | 6, 7 | A–D | `vorige-stand` |
| `sqlite vorige-stand intact` | 7 | A–D | `vorige-stand` |
| `sqlite vorige-stand nieuwe-schrijver` | 6, 7 | A–D | `vorige-stand` |
| `sqlite vorige-stand nieuwe-schrijver vóór-resultaat` | 6, 7 | A–D | `vorige-stand` |
| `sse-retentie` | 3 | B | `sse-retentie` |
| `sse-retentie-basis` | 3 | B | `sse-retentie (155)` |
| `stand` | 2, 3, 4, 5, 6, 7 | Poort | `git status, git diff, git merge-base` |
| `startgrens` | 6, 7 | A–D | `publicatiegrens` |
| `status` | 4, 5 | Poort | `test_evidence_contract.py en integrale proef EX46 in S-P6-19` |
| `tabellen` | 2, 3 | B, C | `scripts/upgradebewijs.py poort, controleer_rapport.py` |
| `tabellen-per-run.py` | 3 | B, C | `scripts/upgradebewijs.py poort, controleer_rapport.py` |
| `terugval-122` | 3, 5, 6, 7 | A, B | `terugval122` |
| `terugval-controle` | 3 | A, B | `terugval-start controle, terugval-gevuld controle` |
| `terugval-start` | 3 | A, B | `terugval-start start` |
| `terugvalcursor` | 2, 3, 6, 7 | B | `proeven terugvalcursor` |
| `terugzettests` | 6, 7 | A–D | `test_herstartpunten.py, test_schema_klaargezet.py` |
| `transactie` | 7 | B, C, D | `open-transactie` |
| `transactie herstart` | 7 | B, C, D | `open-transactie` |
| `uitval` | 2, 3 | Zelfstandige API-proef | `test_connector_events.py, test_openapi_export.py` |
| `upgrade-lokaal` | 6, 7 | B, C | `scripts/upgradebewijs.py poort, controleer_rapport.py` |
| `upgradebewijs` | 4 | B, C | `scripts/upgradebewijs.py poort, controleer_rapport.py` |
| `verwijder-image` | 2, 3 | Poort | `controle en opruiming van het eigen image` |
| `volgorde` | 2, 3 | Poort | `scripts/upgradebewijs.py volgorde` |
| `voorbereiden` | 2, 3 | Proefomgeving | `tijdelijke releasebronnen, geheugengrens en vrije schijfruimte` |
| `voorcontrole na-markering` | 7 | D | `voorcontrole na-markering` |
| `voorcontrole voor-markering` | 7 | D | `voorcontrole voor-markering` |
| `vulling` | 6, 7 | B, C | `scripts/upgradebewijs.py poort, controleer_rapport.py` |
| `wachtende-kopie` | 5, 6, 7 | A, D | `opslaggrens, wachtend` |
| `wal-terug` | 7 | A, B, D | `test_oorsprong_voor_publicatie_blijft_bindend` |
| `weigering-155` | 4, 5, 6, 7 | A, B | `terugval-gevuld terugval, grens155` |
| `weigeringen` | 6, 7 | A–D | `test_herstartpunten.py, test_schema_klaargezet.py` |

De twee onderbrekingsreeksen in `test_herstartpunten.py` bevatten twaalf updategrenzen en twintig terugzetgrenzen. `kijkpunten` en `beschadigingsmatrix` lezen precies die parameters uit dezelfde tests. Per grens toetsen ze twee herstarts met twee kijkprofielen respectievelijk twee weigeringen met behoud van de bestanden. Het verwijderen van het oude commitstapbestand correspondeert in het huidige protocol met het verwijderen van het fasebestand. Extra proeven in `test_herstelfasen.py` raken de publicatie van dat bestand, de archiefmap, de keuze van de beheerder en de nog open SQLite-transactie.

De varianten `terugval-gevuld herupgrade` en `terugval-gevuld herupgrade-schade` laten schema 155 een klaargezette aanvraag zelf afhandelen. Daarna toetst de huidige supervisor regel B op beide standen. Met de oorspronkelijke inhoud laat hij twee starts toe. Een leesbare wijziging van de opzijgezette encodervoortgang houdt beide starts tegen en bewaart de bestanden.

Bij `proeven behoud` beginnen beide upgrades met dezelfde kopie van schema 150. Alle oorspronkelijke rijen blijven gelijk. De toen nog afwezige tabel voor de verificatiegeneratie van het offsite-doel krijgt in elke upgrade precies één rij met ID 1. De proef controleert het formaat van beide generaties en hun onafhankelijke uitgifte.

De historische keuzeproef gebruikt de oorspronkelijke schrijver en zijn onderbrekingspunt. Voor de positieve proef komt de primaire AV1-database uit migratie 001 met één actieve taak. De oorspronkelijke minimale fixture mist verplichte voortgangstabellen en dient als weigerproef. De twee tegenproeven op de eerste versie met fasebestand vereisen de oorspronkelijke fout en de inhoudelijke slotregel.
