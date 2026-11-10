# ADR-0015 — Testfouten: VMAF-poort in de encodertests, Inter-font, CSRF-race en Problemen-tab

- **Status:** geaccepteerd
- **Datum:** 2026-11-09
- **Fase:** stap 1 vóór de ontwerpronde (PEIL 1.1.0): de E2E moet betrouwbaar zijn
- **Aanleiding:** een lokale testcontrole van media-guard-ultimate (rapport en controle; niet in deze repo), nachtrun
  42609424449 (`encoder-image`: 24 failed / 432 passed)

## Context

1. **Encoder-image: 17 tests liepen op de kwaliteitspoort.** De poort (`app/uitvoerder.py` `quality_rejection()`,
   afstelling `vmaf_minimum`, standaard 93,0) bestaat sinds ddf0ed3 (2026-11-05). Sinds afaea89 volgt na een afkeuring
   eerst één herkansing, zodat de job op `wacht`/`queued` bleef in plaats van `mislukt`. De tests waren nooit aan de
   poort aangepast. Zij encodeerden bewust met de laagste kwaliteit voor snelheid: `mg_helpers.FAST` = CRF 50 en het
   uitvoerderprofiel CRF 45 (beide preset 12).
   Gemeten in het testimage (SVT-AV1 2.3.0, dezelfde libvmaf-opbouw als de app):

   | fixture | CRF 50 | CRF 45 | CRF 30 | CRF 28 | CRF 22 |
   |---|---|---|---|---|---|
   | mg-dual-audio.mkv (720p) | 81,7 | 84,5 | 93,8 | 94,6 | 96,2 |
   | mg-hdr10 / mg-hdr10plus | – | – | 94,3 | 95,1 | 96,6 |
   | mg-dv84 (HLG) | – | – | 94,3 | 95,0 | 96,6 |
   | sdr-meertalig.mkv (1080p) | 86,9 | 88,9 | 96,0 | – (CRF 25: 97,1) | 97,6 (CRF 20) |

   De poort deed dus precies wat hij moet doen: een encode van VMAF ≈ 82 werd geweigerd en het origineel bleef staan.
   Er is **geen bewijs** dat 93 te streng is. Wel is aangetoond dat de testinstellingen te laag waren.
2. **Drie losse testcodefouten:** de nep-`_run_attempt` in `test_gpu_varianten.py` kon de vainfo-diagnose niet aan
   (vainfo zit in het testimage). `test_onhaalbare_vloer_houdt_origineel` zette alleen `ssim_floor`, terwijl de proef
   met libvmaf op VMAF oordeelt.
3. **Planner-/noodstoptests hingen af van het RAM van de runner.** Het gaat om 4 tests in `test_planner.py` en 1 in
   `test_r6_noodstop.py`. `memory_block_reason` gebruikt het echte werkgeheugen. Op de privé-GitHub-runner
   (± 7 GB) of onder `--memory 5g` weigert die terecht. Lokaal aangetoond: 5 van de 5 faalden onder `--memory 5g`.
4. **Web: Inter werd nooit toegepast.** `--font-inter` stond op `<body>`, maar `--mg-font: var(--font-inter), …` staat op
   `:root`. Daardoor werd `--mg-font` ongeldig en viel de app terug op het systeemfont. Met Noto Sans gaf dat 3 px
   zijwaarts scrollen op 390 px. De chip 'Geen ontbrekende Nederlandse ondertitels bekend' liep bovendien bij bijna
   elk font buiten zijn pil (`white-space: nowrap`).
5. **Web: CSRF-race.** De shell toont de pagina al terwijl `GET /auth/me` loopt (bewuste snelheidskeuze). Een wijziging
   in dat venster vertrok zonder `X-CSRF-Token` en kreeg 403 'CSRF-controle mislukt'. Voorbeeld:
   `migration.spec.ts` uploadt direct na `page.goto('/migratie')`.
6. **Web: de Problemen-tab crashte voor een beheerder zonder 2FA.** Core stuurt bij `security.twofa_off` (en
   `security.secret_key_mismatch`) rijen zonder het veld `actions`. `AttentionList` las `r.actions.length`. Dat gaf
   een TypeError, en de foutgrens toonde 'Deze pagina kon niet worden geladen'. `werk-eenvoud.spec.ts` faalde daardoor
   lokaal 3 van de 3 keer, ook op de oude code (8cd3175). Op CI slaagde hij alleen als de tab-assertie al klaar was
   vóór `/attention` antwoordde. De eerdere verhoging naar 20 s (a420ee8) behandelde dit als traagheid.

## Besluit

- **De productdrempel blijft 93,0.** We veranderen geen standaardwaarde en zetten geen omgevingsoverride in de tests.
  De tests encoderen met instellingen die de echte poort halen: `FAST` krijgt CRF 22 (marge ≥ 3,2 VMAF) en het
  uitvoerderprofiel CRF 28. Zo blijft de poort actief in de brug-, DV-, HDR10+-, executor- en uitvoerdertests.
- Testcodefouten worden in de test opgelost. De nep-functie handelt vainfo af en `shutil.which('vainfo')` is vast
  gezet, zodat de test met en zonder vainfo hetzelfde loopt. De proeftest zet beide vloeren onhaalbaar
  (`vmaf_floor` 99,99).
- Planner-claimtests krijgen een vaste, ruime geheugenmeting (`conftest.ruim_werkgeheugen`, alleen
  `app.planner.effective_memory`). De geheugenpoort zelf houdt zijn eigen tests met een expliciete `memory=`.
- Web: de font-variabelen gaan naar `<html>`. Chips mogen omlopen (`max-width: 100%`, `white-space: normal`,
  `min-height` in plaats van `height`). Dat volgt PEIL ("tekst mag omlopen, nooit `whitespace-nowrap`").
- Web: zolang de sessie laadt, wacht een wijziging (`coreRequest` en `uploadFile`) op het CSRF-token, hooguit 10 s.
  Daarna gaat het verzoek zonder token, zodat core zelf de echte reden geeft. GET-verzoeken blijven parallel lopen.
  Er komen geen nieuwe UI-teksten en geen API-, route- of foutformaatwijziging.
- Web: `AttentionRow.actions` is optioneel en `AttentionList` gebruikt `r.actions ?? []`. Core en de API blijven zoals
  ze zijn: het web volgt wat core werkelijk stuurt. Of core `actions: []` moet meesturen (ook voor de Android-app), is
  een vervolgpunt.

## Gevolgen

- De encodertests duren iets langer: een encode met CRF 22 is groter dan met CRF 50, maar preset 12 blijft.
- Raakt een toekomstige SVT-AV1-versie de 93 op de fixtures niet meer, dan faalt de suite. Dat is bedoeld: een
  zichtbaar signaal in plaats van een stil verlaagde drempel.
- Regressietests: `werk-mediaserver.spec.ts` controleert dat Inter echt gebruikt wordt en dat de chiptekst binnen
  de pil blijft. Een nieuwe test in `migration.spec.ts` vertraagt `/auth/me` met 2 s en uploadt meteen. Op de oude
  code faalde die reproduceerbaar met 'CSRF-controle mislukt', op de nieuwe code slaagt hij.
  `werk-wachten.spec.ts` heeft nu een punt zoals core het stuurt (rij zonder `actions`).
