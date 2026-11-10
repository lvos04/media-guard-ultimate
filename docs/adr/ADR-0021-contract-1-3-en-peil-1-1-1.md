# ADR-0021 — Contract 1.3 en Peil 1.1.1 toegepast: kopieën met gepinde bron, adresregels, upgradebewijs (HARDE EIS)

- **Status:** geaccepteerd
- **Datum:** 2026-11-10
- **Fase:** conformiteitsronde contract 1.3 (alleen de punten die `CONTRACT_1.3_CONFORMITEIT.md` §6 voor deze repo als
  **NU** markeert) en overstap naar Peil 1.1.1
- **Context:** contract 1.3 (bron `lvos04/ai-server-manager@0a7143b`, na de onafhankelijke review RV1–RV13) en Peil
  1.1.1 zijn op 2026-11-10 vastgesteld. Voor media-guard-ultimate staan als NU: C1.3-MGU-01 (kopie + drifttest +
  EVIDENCE-test), C1.3-MGU-03 (HARDE EIS: upgradebewijs als gate vóór de eerste release uit de nieuwe repo) en het
  adresdeel van C1.3-MGU-08; daarnaast C1.3-ALG-01/02 en, na Peil 1.1.1, C1.3-ALG-05. De overige punten (MGU-02 na het
  GHCR-besluit, MGU-04 P4-0, MGU-05/06/07/09 P3) zijn **niet** gebouwd; ze staan als open in EVIDENCE.json.
  Vaste regel: geen wijziging raakt kijkgeschiedenis of MediaGuard-voortgang; geen migratie, geen nieuwe of gewijzigde
  route (OpenAPI byte-gelijk).

## Besluiten

1. **Eén gepinde bron-commit voor contract en Peil: `0a7143bc0a94ed5317a9655fa785e73a1fb07ba1`.** Op die commit staan
   contract 1.3 (laatst gewijzigd in `02b119c`, RV13) en Peil 1.1.1 (`0a7143b`). De pins staan machineleesbaar in
   `docs/platform-v2/HERKOMST.json` en `design/ontwerptaal/HERKOMST.json`; `scripts/kopie-herkomst.py` controleert ze
   (offline in de core-tests, online in de CI-job `kopieen`) en schrijft nieuwe kopieën (`bijwerken`).
2. **Driftregel per soort kopie.**
   - *Contract* (kop van het contract: "drifttest die de sha256 van beide tegen de bron (vaste bron-commit)
     controleert"): fout bij elke afwijking van de bron **op de gepinde commit**; is de bron daarna gewijzigd (ook met
     dezelfde versie), dan is dat een **waarschuwing**. Een nieuwe kopie overnemen is een stap van de orkestratie
     (contract, kop); een rode CI in deze repo voor een wijziging die hier niet gemaakt mag worden, helpt niemand.
   - *Peil, strenge kopieën* (`ontwerptaal.css`, aliaslaag, `tokens.json`, `components.json`, ook de kopieën in de
     Docker-context `apps/web/styles/peil/`): pin- en driftregel E08 letterlijk — fout bij een afwijking van de gepinde
     inhoud en bij dezelfde versie met andere inhoud in de bron; een nieuwere versie is een waarschuwing.
   - *Peil, documentatie* (`ONTWERPTAAL.md`, `COMPONENTEN.md`, `ADR-ONTWERPTAAL.md`): fout bij een afwijking van de
     gepinde inhoud; elke latere bronwijziging is een waarschuwing. Deze kopieën bepalen geen uiterlijk; een
     redactionele zin in de bron mag de CI van deze repo niet rood maken. Veiligste lezing van E08, die alleen over
     sjabloon, CSS en tokens spreekt (ONTWERPTAAL §14: "Kopieën: `ontwerptaal.css`, `aliaslagen/<app>.css`, …").
   - De oude handmatige controle uit `HERKOMST.md` (1.1.0, "CI-stap komt pas als de bron gecommit is") is vervangen.
3. **Peil 1.1.1 in de web-UI:** nieuwe kopieën van `ontwerptaal.css` en de aliaslaag (focusring en scroll-padding
   staan nu in de aliaslaag, E14). De eigen kopie daarvan in `apps/web/styles/peil-mg.css` is weggehaald (ONTWERPTAAL
   §13.3: "mogen weg na de overstap naar 1.1.1"); de randkleur van een invoerveld met focus blijft een eigen correctie.
   `check-peil.mjs` eist nu ook de bron-commit in de kop (E08). Gemeten: de volledige Playwright-suite (215 tests, de
   Peil-matrix 320/360/390/768/1024/1440 × licht/donker met axe zonder enige overtreding, contrast, focus nooit bedekt,
   aanraakdoelen ≥ 44 px) is groen. Android: `PeilTokens.kt` opnieuw gegenereerd uit `tokens.json` 1.1.1 (alleen de
   `-soft`-banden van verwarmen/koelen veranderen; MediaGuard gebruikt die niet).
4. **Niet overgenomen in deze ronde (open, met reden):** het woord "Niet te vinden" (en-US "Not found") voor een
   aflevering die de indexers niet vinden (E13) en één kleurcodering in "Wat kwam erbij / Waar ging de ruimte heen"
   (repowerk C09/C12). Het woord staat ook in de API-uitvoer van core (`status_nl` van wanted, instellingslabels) en in
   de Android-app; dat vraagt een eigen UI-ronde met en-US, Android en tests, en valt buiten de NU-punten van 1.3. Tot
   dan blijft "Niet beschikbaar" staan (geen omweg via een lokale CSS- of tekstwijziging, C1.3-ALG-05).
5. **Adresregels voor "Terug naar de Hub" (C1.3-MGU-08, contract 1.3 §3.2 en §2.3 regel 5).** Core weigert bij het
   opslaan (422, oude waarde blijft) en de web-UI toont geen link bij:
   - een host die op een getal of `0x`-deel eindigt maar geen canonieke `a.b.c.d` is (`127.1`, `2130706433`,
     `0x7f000001`, `0x7f.1`, `010.0.0.1`, `0`, `192.0.2.010`, `1.2.3.4.5`, `foo.123`);
   - IPv6 met zone-ID of buiten `[]`; `0.0.0.0/8` (ook verpakt in IPv6), loopback, link-local, `localhost`;
   - **niet-ASCII-namen en `%`-codering** (strenger dan de lettertekst): een browser maakt van `127%2e0%2e0%2e1`,
     `ⓛocalhost` of fullwidth-cijfers een ander, verboden adres. Gevolg: een Hub-naam met IDN moet als punycode
     (`xn--…`) worden ingevuld;
   - lege labels en labels met andere tekens dan `a-z`, `0-9`, `-`; één punt aan het eind telt niet mee en wordt bij het
     opslaan weggelaten.
   De contractregel "verboden bereiken ook na DNS-resolutie per verbinding" geldt hier niet: MediaGuard maakt nooit
   verbinding met dit adres, het is alleen een link in de browser. Een eerder opgeslagen waarde die nu ongeldig is,
   blijft in de database staan (geen migratie, geen schrijven in bestaande gegevens) maar geeft geen link meer.
6. **Upgradebewijs (C1.3-MGU-03, HARDE EIS).** `scripts/upgradebewijs.py` is de gate:
   - *Vorige release* = `deploy/upgradebewijs/vorige-release.json`: `ghcr.io/lvos04/mediaguard-core:1.2.2-aio` op
     digest `sha256:14e540ba…52dc6` (tag `v1.2.2` = `30f7415` in de archiefrepo). De poort weigert een image zonder
     digest. De fixture is precies die versie (`core-1.2.2`), niet "de nieuwste eerdere" zoals de rooktest.
   - *Fixture*: `scripts/make-aio-fixture.py` vult via de API (beheerder, kijker, instellingen, sleutel, meldingen,
     taken) en daarna **elke** overige tabel met herkenbare, inerte rijen (`upgradebewijs.vul`): 105 van de 108 tabellen
     (niet: `secrets`, `login_challenges`, `locks`). Inert = afgeronde taken en encodes, gearchiveerde instanties,
     uitgeschakelde kanalen, opgeloste wanted-items: de diensten gaan na de start niets met de fixture doen. Het schema
     van de fixture is gelijk aan dat van 1.2.2 (migraties in het 1.2.2-image en in deze repo byte-gelijk gemeten).
   - *Poort*: vorige release start op de fixture → AV1-database (die maakt de vorige release zelf) vullen → vorige
     release opnieuw (accepteert de gevulde databases, readback) → meten → nieuwe release op dezelfde mappen (health en
     versie, inloggen met de oude beheerder, instellingen, readback van 11 leesroutes) → meten en vergelijken →
     terugzetten: vorige image op de snapshot van vóór de update (contract §2.3 regel 7a) → meten en vergelijken.
   - *Vergelijking*: alle live SQLite-databases onder `/config` (core, AV1-engine, de vier forks; back-ups niet). Elke
     rij van vóór moet er nog zijn met dezelfde waarden in de kolommen van vóór; nieuwe rijen, kolommen en tabellen
     mogen. Uitzonderingen alleen in `tests/fixtures/aio/upgradebewijs-uitzonderingen.json`, elk met reden, en elke
     toegepaste uitzondering staat in het rapport. Toegestaan zijn alleen runtimegegevens die ook **zonder** upgrade bij
     elke start veranderen (gemeten: dezelfde verschillen bij het terugzetten, waar geen nieuwe versie draait):
     hartslagen en taakwacht in `system_state`, `last_login_at` door de readback, tijdstempels van de laatste controle in
     de AV1-engine, de pauzereden van een baan, logdatabases en de opdrachtgeschiedenis van de forks. Tabellen van de
     voortgang staan er nooit in (test `test_uitzonderingen_hebben_elk_een_reden_en_raken_de_voortgang_niet`).
   - *Waar het draait*: in-process op elke push (`apps/core/tests/test_upgradebewijs.py`: de fixture met de core van
     nu); met Docker op elke push als "core-kandidaat" (runtime van de vorige release + core en release-manifest van
     deze commit, `deploy/upgradebewijs/Dockerfile.kandidaat`; een volledige AIO-build duurt uren); en in `release.yml`
     op het **echte** kandidaat-image vóór `publish` (publish hangt ervan af). De core-kandidaat is geen releasebewijs:
     het encode-, web- en fork-deel is dan dat van de vorige release.
   - *Grenzen (eerlijk)*: de fork-databases (Sonarr/Radarr) worden gemeten maar niet gevuld (series en films toevoegen
     vraagt externe metadata); hun eigen upgrade volgt de upstream-migraties. Kijkgeschiedenis van Emby/Jelly Leon zit
     niet in MediaGuard (alleen recent-bekeken paden, die zitten in de fixture); de HARDE EIS voor de mediaserver zelf
     (regel 7a) hoort bij jelly-leon-platform/B03. Niets hiervan is op een echte NAS gedraaid.
7. **Installatiedocs:** `deploy/truenas/compose.yaml`, README §3.3 en `docs/INSTALL_TRUENAS.md` zeggen nu expliciet dat
   *Install via YAML* en losse containers **geen installatiemethode** zijn (vaste keuze van de gebruiker; contract 1.3
   §1 en §2.3 regel 4; 1.2-punt MG5). Het compose-bestand blijft voor ontwikkeling, CI en de integratietests. Alleen
   tekst; geen gebruiker wordt gedwongen over te stappen.
8. **EVIDENCE (§11 1.3):** `apps/core/tests/test_evidence_contract.py` bewaakt statussen per blad, vaste
   validatiewaarden (ook `Niet getest` alleen bij `Niet gestart`, `Sim-geslaagd`/`Sim-mislukt` alleen in de Testmatrix),
   `NIET GEVALIDEERD` uit `blokkade` ook in `validatie`, bestaande ID's (Excel, `C1.3-MGU-nn`/`C1.3-ALG-nn` uit de
   conformiteitslijst, `REVIEW-media-guard-ultimate-…`) en dat elk genoemd testbestand en elke testfunctie bestaat.

## Gevolgen

- Een nieuwe contract- of Peil-versie overnemen = `scripts/kopie-herkomst.py bijwerken --set … --commit <40 hex>`, de
  pins in `apps/core/tests/test_kopie_herkomst.py` (contract) of `PeilTokensTest`/`check-peil.mjs` (Peil) en de eigen
  tests opnieuw.
- Na elke MediaGuard-release: `deploy/upgradebewijs/vorige-release.json` naar de nieuwe versie en digest, en een fixture
  `core-<versie>` op de releasecommit (`scripts/make-aio-fixture.py`). Zonder fixture van precies die versie faalt de
  poort.
- Een migratie die bewust iets wijzigt, moet dat in het uitzonderingenbestand benoemen (met reden); het rapport toont
  het. Anders blokkeert de poort de release.
