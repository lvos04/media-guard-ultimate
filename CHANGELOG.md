# Changelog

## Unreleased

Meerdere mediaservers tegelijk en aanmelden per server (Δ-MG-DUAL, Δ-MG-AUTH; contract 1.3 §2.3 regel 2,
C1.3-MGU-04). Besluiten: ADR-0022 en ADR-0023. Eén migratie (`150_mediaserver_servers`, voegt alleen toe); de
status-API krijgt er velden bij, geen veld verdwijnt. Geen release.
- **Emby én Jelly Leon/Jellyfin tegelijk:** MediaGuard volgt elke gekoppelde mediaserver. De koppeling van 1.2.2
  (bijvoorbeeld Emby) is na het bijwerken zonder één handeling server 1: adres, sleutel en gekoppelde mappen blijven
  waar ze stonden ("nul-migratie"), dus de Emby-koppeling valt op geen moment weg. Extra servers (hoogstens 8) staan
  in eigen opslag met een eigen geheim.
- **Kijkpauze over alle servers:** nieuwe encodes wachten zodra iemand op één van de servers kijkt. Een fout of
  storing bij de ene server raakt de andere niet; een server zonder contact telt nog hoogstens 10 minuten mee.
- **Per server:** verversen na een vervanging, afspeeltest en controle op Nederlandse ondertitels, met de uitkomst
  per server; de samenvatting per overdracht is de slechtste uitkomst.
- **Aanmelden per server:** Emby houdt `X-Emby-Token`; Jellyfin en Jelly Leon 12.x krijgen
  `Authorization: MediaBrowser … Token="…"` (core en encoder). Een sleutel staat nooit in een adres of logregel; de
  logfilter maskeert nu ook `Token="…"` en `X-Emby-Token`. Een server wordt herkend aan zijn openbare info; alleen een
  server die zich als Jellyfin meldt, krijgt de nieuwe kop.
- **Status-API:** `GET /api/v1/mediaserver/status` houdt alle velden van 1.2.2 en krijgt `servers[]` erbij (soort,
  naam, adres, aan/uit, verbonden, laatste contact, aantallen; nooit sessies, titels of gebruikers). `POST /config` en
  `POST /test` nemen een optioneel `server_id`; zonder werken ze als voorheen. OpenAPI-export bijgewerkt.
- **Losmaken:** een server losmaken stopt het volgen zonder storing of incident; adres en sleutel blijven bewaard en
  weer koppelen is één handeling. Server 1 kan los, niet weg; een extra server kan ook verwijderd worden.
- **Web-UI (Peil, nl en en-US):** *Instellingen → Koppelingen → Mediaserver & ondertitels* toont een lijst
  mediaservers met per server *Gekoppeld*/*Ontkoppeld* en daarnaast de gemeten stand (*Gezond* alleen na een gelukt
  contact, anders *Storing* of *Onbekend* met de reden), toevoegen, wijzigen, losmaken, weer koppelen en verwijderen.
- **Upgradebewijs — HARDE EIS:** de alles-in-één-rooktest (`scripts/aio-smoke.py`, ook in `release.yml`) vergelijkt nu
  per tabel rijen en inhoud vóór en na de upgrade van de gevulde 1.2.2-database en is rood bij elk verlies.
- **Contracttest met echte servers** (`tests/contract/mediaserver/`): Emby 4.10.2.0 en Jellyfin 12.4 op digest, de
  echte vorige release (1.2.2) en daarna de kandidaat op dezelfde datamap; al het verkeer van MediaGuard loopt via een
  loggend doorgeefluik. 24 tests, 113 controles; in CI in de zware run (job `mediaservers`). Uitkomst: `RESULTS.md`.
- **OpenSubtitles-aanmelding:** kaatst `/login` bij een fout het wachtwoord of de gebruikersnaam terug, dan staan die
  in geen enkele vorm (letterlijk, JSON-, URL- of HTML-gecodeerd) in de foutmelding; net als de sleutels uit de koppen.
- **Encoder-koppelingen:** `PATCH /api/integraties/<soort>` weigert een nieuwe sleutel met onverwachte tekens (422, zoals de testknop); een adres met stuurtekens of witruimte aan de rand geeft 422 in plaats van 500.
- **Android-app ook in en-US** (ADR-0023): de taal volgt Android (systeemtaal, of vanaf Android 13 de taal per app);
  Nederlands blijft de standaard. Getallen, datums en tijden volgen de taal. Teksten die MediaGuard zelf aanlevert,
  blijven Nederlands. De Nederlandse app is ongewijzigd (78 referentiebeelden gelijk); 6 Engelse beelden erbij.
- **Niet gevalideerd:** de echte NAS met de eigen Emby en kijkgeschiedenis, een echte Jelly Leon-image, echte spelers,
  een echte telefoon. Sessies in de contracttest zijn nagebootst met de sessie-API van de servers.

Contract 1.3 en Peil 1.1.1 toegepast (alleen de NU-punten van `CONTRACT_1.3_CONFORMITEIT.md` §6; geen migratie, geen
nieuwe of gewijzigde route, OpenAPI byte-gelijk). Besluiten: ADR-0021.
- **Kopieën met gepinde bron-commit** (C1.3-MGU-01): `PLATFORM_CONTRACTEN.md` 1.3 en `CONTRACT_1.3_CONFORMITEIT.md`
  byte-gelijk aan `lvos04/ai-server-manager@0a7143b`; Peil-kopieën naar 1.1.1 op dezelfde commit.
  `scripts/kopie-herkomst.py` controleert ze offline (core-tests) en tegen GitHub (CI-job `kopieen`): fout bij drift
  op de pin, waarschuwing bij een nieuwere bron (Peil: pinregel E08).
- **Upgradebewijs — HARDE EIS** (C1.3-MGU-03): kijkgeschiedenis en MediaGuard-voortgang blijven bij elke upgrade
  beschikbaar. `scripts/upgradebewijs.py` start de echte vorige release (1.2.2-aio op digest) op een fixture waarin elke
  tabel gevuld is, daarna de nieuwe versie, vergelijkt per tabel vóór/na, leest de voortgang terug via de API en bewijst
  terugzetten (vorige image + snapshot). Draait op elke push (core-kandidaat) en in `release.yml` vóór `publish`.
- **"Terug naar de Hub"** (C1.3-MGU-08): alleen adressen volgens contract 1.3 §3.2 (canonieke IPv4, IPv6 tussen `[]`,
  geen `%`-codering of IDN); ongeldig → 422 bij opslaan en geen link in de UI.
- **Peil 1.1.1:** nieuwe aliaslaag zet focusring en scroll-padding zelf terug; de eigen kopie in `peil-mg.css` is weg.
  Android-tokens opnieuw gegenereerd. Volledige Peil-matrix (axe, contrast, focus, 44 px) groen.
- **EVIDENCE-test volgens §11 van 1.3** (`apps/core/tests/test_evidence_contract.py`).
- **Installatiedocs:** *Install via YAML* en losse containers staan expliciet als ontwikkeling/CI, geen
  installatiemethode.

QA-review contract 1.3 (onafhankelijke controle; geen nieuwe functies, geen migratie, OpenAPI ongewijzigd). Besluiten:
ADR-0021 punt 6.
- **Upgradebewijs nooit leeg groen:** een lege of halve meting vóór de upgrade gaf "niets verloren". Nu rood als
  `core/mediaguard.db` of `encode/app.db` ontbreekt, een tabel minder rijen heeft dan de fixture of de AV1-database niet
  gevuld is.
- **Upgradebewijs tegen de echte vorige release:** nieuwe stap `upgradebewijs.py volgorde` (CI en `release.yml`) leest
  tags en digest uit GHCR en weigert een `vorige-release.json` die niet de direct vorige gepubliceerde release is.
- **"Terug naar de Hub":** web weigerde `[fe8::1]` (geen link-local); core en web nu gelijk (differentiële test op 5545
  adressen).
- **Encoder-tests** van de vrije-ruimtebodem hangen niet meer af van de schijf van de testmachine.
- **Docs:** geen voorbeeldpool `tank` meer als waarde; de dev-compose heeft geen standaardpad meer (de drie paden zijn
  verplicht); README §3.3 geeft geen *Install via YAML*-stap meer.

QA na de Peil-ronde (onafhankelijke controle van stap 1–5; geen nieuwe functies, geen versie-ophoging). Besluiten:
ADR-0020.
- **Hub-adres alleen voor een beheerder:** `system.hub_url` (link "Terug naar de Hub") kon door elke operator en de
  app-sleutel worden gewijzigd. Nu alleen een beheerder, net als `app.public_url`; lezen blijft voor iedereen.
- **OpenAPI-export in de repo** (`docs/openapi/mediaguard.openapi.json`, `scripts/export-openapi.py`) met een
  drift-test in de core-job van CI; de inhoud is byte-gelijk aan die van vóór de ronde (383 paden).
- **Peil-e2e volgens de volledige matrix van ONTWERPTAAL §12:** 320/360/390/768/1024/1440 × licht én donker, plus
  tekst ≥ 14 px, geen ellips, geen kopsprongen, doelen ≥ 44 × 44 px op de hele pagina, `prefers-reduced-motion` en
  forced colors.
- **Geen stille overslag meer in de e2e:** `design-shots` valt buiten een gewone run; de N13-tellertest zet zelf een
  aandachtspunt klaar in plaats van zichzelf over te slaan.
- **Hooktests met een tijdgrens zonder koude start** (warme aanroep + `gc.collect()` vóór de meting; grenzen
  ongewijzigd). Deze tests faalden af en toe op de GitHub-runner.

Ontwerpronde Peil 1.1.0, stap 5 — onafhankelijke ontwerpreview van de omgezette app (alleen uiterlijk, teksten en
toegankelijkheid; routes, API, foutformaat, datamodel, poorten en installatie ongewijzigd; OpenAPI byte-gelijk; geen
nieuwe versie). Verslag: `docs/ontwerp/PEIL_REVIEW.md`, besluiten: ADR-0019.
- **Tabellen en kalender op 1024–1279 px:** werden tot één letter breed geknepen; nu kaartenlijst/omlopende dagen
  zodra de inhoud smaller is dan 60 rem; de koppelingsmatrix wordt onder 90 rem een kaart per instantie (geen
  afgekapte kolomkoppen meer).
- **Links weer als link te zien** (onderstreept, linkkleur) en **44 px** voor losse links, categoriechips, vaktermen
  en selectievakjes in tabellen.
- **Status eerlijker:** elke statuspil met icoon; open instellingen en planwijzigingen niet meer rood/groen; Nog niet
  gecontroleerd = Onbekend; koppelingsmatrix met een teken per cel; als de core of AV1-engine niet antwoordt staat er
  Onbekend met de reden (geen "—", eindeloos laden of "Nog geen metingen"); de lijngrafiek tekent geen vlak meer over
  een meetgat maar een gearceerd vak "Geen meting".
- **StatKaarten met Bron en Bijgewerkt** op Overzicht, Ruimte en Ontbrekend.
- **Teksten (NL + en-US):** geen "Doorgaan", "In orde" of "Beperkt automatisch" meer; kortere melding als de
  AV1-engine niet antwoordt; laatste foutpagina in Peil (licht/donker).
- **Tests:** de Peil-e2e telt nu elke axe-overtreding en controleert links, selectievakjes en Onbekend-kaarten; nieuwe
  unit-tests. Schermafdrukken en metingen in `docs/screenshots/peil/review/`. NIET GEVALIDEERD: schermlezer, echte
  telefoon, echte NAS.

Ontwerpronde Peil 1.1.0, stap 4 — de Android-app in de ontwerptaal Peil (alleen uiterlijk; functies, API-aanroepen,
rechten, ondertekening en installatie ongewijzigd; geen nieuwe versie):
- **Peil-stijl** in alle schermen van `apps/android`: kleuren gegenereerd uit `design/ontwerptaal/tokens.json`
  (licht/donker volgt het systeem; `tools/peil_tokens.py`, drifttest), systeemfont (Inter is weg), geen tekst onder
  14 sp, kaarten met een rand, hoeken 8 dp, knoppen/tabs/schakelaars van minstens 48 dp, dezelfde lucide-iconen als de
  web (vector drawables; `material-icons-extended` is weg).
- **Status eerlijker:** icoon + woord + vorm (nooit alleen kleur); groen alleen voor gemeten gezondheid ("Alles
  gezond"); Compleet, Geleverd en noodstop "Uit" zijn neutraal; noodstop aan is **Noodstop actief** (Gepauzeerd);
  verouderde gegevens gestippeld met "Laatst bekend · …", ook op de tegels; **Wat actie nodig heeft** met een icoon
  per soort en een slot voor een voorwaarde ("Dit is geen storing; er wacht een voorwaarde."); GPU/CPU met
  `microchip`/`cpu` in de baankleur; "Waar ging de ruimte heen" in grafiekkleuren (niet verklaard gearceerd).
- **Leesbaar op 360 dp en met grote letter:** tabs lopen om in plaats van buiten beeld te vallen, titels en uitleg
  zonder ellips, bovenbalk en ondernavigatie breken nooit midden in een woord.
- **Tests:** schermafdrukken in de repo (Paparazzi, 18 schermen × licht/donker × 360/393 dp + grote letter) die CI
  vergelijkt (`verifyPaparazziDebug`), plus tests voor de tokens, het contrast van alle gebruikte kleurparen en de
  Peil-woorden. `android.yml` draait nu op elke branch die de app raakt. Niet-ondertekende release-build met
  `MG_ANDROID_UNSIGNED=1`. Afdrukken "na" in `docs/screenshots/peil/android/`. Besluiten: ADR-0018.
  NIET GEVALIDEERD: echte telefoon, emulator, TalkBack.

Ontwerpronde Peil 1.1.0, stap 3 — de web-UI in de ontwerptaal Peil (alleen uiterlijk + de twee toegestane
toevoegingen; routes, API, foutformaat, datamodel, poorten en installatie ongewijzigd; OpenAPI byte-gelijk):
- **Peil-stijl** in de hele web-UI: tokens uit `ontwerptaal.css` (versie-gepinde kopie, `npm run check:peil`), de
  gedeelde aliaslaag en `styles/peil-mg.css`; Tailwind 4 + shadcn/ui (Button, Badge, Dialog) met de Peil-codemod;
  systeemfont (Inter en JetBrains Mono zijn weg), body 16 px en geen tekst onder 14 px; bedienbare elementen 44 px;
  tweekleurige focusring die nooit achter kop- of ondernavigatie valt; standaardthema **Systeem**.
- **Status eerlijker:** groen alleen voor gemeten gezondheid; beschikbaarheid (Binnen, Ontbreekt, Verwacht, …) is
  neutraal met een eigen icoon; Onbekend gestippeld, nooit met een pauze-icoon; noodstop actief als Gepauzeerd;
  bibliotheek vergrendeld als voorwaarde ("Dit is geen storing"); Overzicht met een totaalstatus los van **Wat actie
  nodig heeft**; een onbereikbare AV1-engine is **Onbekend — AV1-engine reageert niet**; foutmeldingen met code en
  een kopieerbare **Trace-ID**; verouderde gegevens gestippeld en gearceerd. Woorden in NL en en-US.
- **Nieuw: "Terug naar de Hub"** in de zijbalk (en bovenaan het profielmenu op telefoon/tablet), alleen als de
  nieuwe, optionele instelling **Hub-adres** (`system.hub_url`, Instellingen › Systeem) een geldig adres heeft.
- **Nieuw: tabelweergave en toetsenbord bij elke grafiek** (Belasting + Geheugen, Besparing per dag, Opslagtrend,
  Verdeling, Episodekaart): "Toon als tabel", pijltjestoetsen lezen per punt voor, een dag zonder meting is een gat
  ("Geen meting"), nooit 0. Belasting toont hooguit drie lijnen; cache staat in een eigen grafiek.
- **Toegankelijkheid:** axe (WCAG 2.2 AA-set) zonder ernstige fouten op de hoofdschermen en in de dialogen; de
  profielknop draagt de zichtbare initialen in zijn naam; de noodstopknop heeft op de telefoon een naam; geen
  interactief element meer in een uitklapkop; scrollende tabellen zijn met het toetsenbord bereikbaar.
- **Tests:** vitest-unit-tests voor de web-UI en een Peil-e2e (320–1440 px, axe, toetsenbord, grafieken, Hub-link);
  CI draait beide. Schermafdrukken "na" in `docs/screenshots/peil/na/`. Besluiten: ADR-0017.

Ontwerpronde Peil 1.1.0, stap 2 (alleen documentatie; aan de app is niets gewijzigd):
- **Versie-gepinde kopie van de ontwerptaal Peil 1.1.0** in `design/ontwerptaal/` (O-14): herkomstkop met bron,
  versie en sha256; `HERKOMST.md` met het controlecommando.
- **Nulmeting "voor"** in `docs/screenshots/peil/voor/`: web-afdrukken van de echte app met testgegevens (30 schermen ×
  360/390/768/1440 × licht/donker, plus tabs en toestanden), metingen per afdruk (overloop, doelen, tekstmaten, axe) en
  een Tab-doorloop; Android-afdrukken met Paparazzi (16 schermen × 360/393 dp × licht/donker).
- **Omzettingsontwerp** `docs/ontwerp/PEIL_MIGRATIE.md` en **ADR-0016** (interpretaties): per scherm en component de
  vertaling naar Peil, de twee webfasen, "Terug naar de Hub", tabelweergave en toetsenbord bij de grafieken, het
  Android-thema uit `tokens.json`, en welke tests geraakt worden.

Testfouten gerepareerd vóór de ontwerpronde (ADR-0015):
- **Web: Inter wordt nu echt gebruikt.** De font-variabelen stonden op `<body>`, terwijl `--mg-font` ze op `:root`
  zocht. Daardoor viel de hele app terug op het systeemfont (met Noto Sans 3 px zijwaarts scrollen op 390 px).
- **Web: statuschips lopen om** in plaats van buiten hun pil te steken (`max-width: 100%`, tekst mag omlopen).
  Op één regel zien ze er hetzelfde uit.
- **Web: geen 'CSRF-controle mislukt' meer** bij een wijziging of upload direct na het openen van een pagina. Een
  wijziging wacht nu (hooguit 10 s) tot de sessie het CSRF-token heeft. Lezen loopt nog steeds parallel.
- **Web: Activiteit → Problemen crasht niet meer** voor een beheerder zonder tweestapsverificatie. Core stuurt die
  rijen zonder knoppen (zonder `actions`), en de lijst ging daar onderuit.
- **Encodertests in het toolchain-image** halen de echte VMAF-poort (93, ongewijzigd). De snelle testencodes
  gebruiken CRF 22/28 in plaats van 50/45, die terecht werden afgekeurd (VMAF ≈ 82–89). Verder: de nep-vainfo
  in de GPU-proefladdertest, beide vloeren in de proeftest, en een vaste geheugenmeting in de planner-claimtests.
  Die faalden alleen op runners met weinig RAM.

## 1.2.2

Verbeterronde (40 punten uit de verbeterlijst van 8 november; die staat niet in deze repo):
- **Bewaking zonder dat de app open is**: een achtergrondcontrole (elke 5 min) pusht problemen
  direct en ruimt de melding zelf op als het weer loopt: AV1-baan of GPU staat stil met werk,
  aanvraag zonder voortgang, noodstop > 2 uur, Seerr-sync faalt, achtergrondtaak doet niets,
  TrueNAS-pool gedegradeerd (opruimen van originelen pauzeert dan).
- **Geen vals alarm**: kijken via Emby, handmatige pauze en stille uren maken Overzicht niet meer
  rood; bewuste keuzes kun je wegzetten met 'Bewust zo gekozen'.
- **Achtergrondtaken herstarten zichzelf** na een fout, met laatste geslaagde ronde per taak
  (Systeem → Achtergrondtaken); één kapotte aanvraag stopt de leveringen niet meer.
- **Back-ups**: back-up, herstelbundel en nachtonderhoud los van elkaar; alleen gecontroleerde
  back-ups tellen mee; melding bij een mislukking. Back-ups van vóór een update staan in de app
  en zijn terug te zetten (met de encoder-database).
- **Leveringen**: downloads worden niet meer na 2 uur weggegooid (de vastloper-controle met jouw
  6 uur beslist); verwijderde titels komen niet terug via Seerr; per aanvraag 'Stop', 'Nu
  opnieuw' en 'Nu zoeken'; verkeerde afleveringen én films gaan via de quarantaine en zijn terug
  te zetten; het MediaGuard-profiel geldt alleen voor nieuwe Seerr-aanvragen, nooit voor een
  profiel dat je zelf koos of gemigreerde titels.
- **Noodstop overal**: ook downloadbewaking, auto-import, opruimen, blokkeren en profielen
  wachten; Sonarr/Radarr-hooks blijven de noodstop volgen als core herstart.
- **AV1**: wachtrij per baan, zodat de GPU niet stilstaat achter CPU-werk; VMAF met time-out en
  zonder stil doorlaten als meten mislukt; duidelijke foutmeldingen; een update breekt lopende
  encodes netjes af.
- **Beveiliging**: gevaarlijke instellingen alleen voor beheerders (app-sleutel en operator niet);
  inlogrem met vertraging; echt IP achter Cloudflare; https in de koppel-QR; encoder-brug en
  API-documentatie dicht vanaf internet; wachtwoordwijziging logt andere apparaten uit.
- **Android-app**: pushmeldingen staan na het koppelen aan, met 'Testmelding' en waarschuwing bij
  ontbrekende rechten; 'Wacht op jou' met uitleg en knoppen; 'Niet bijgewerkt sinds …';
  echte 'Laatste 24 uur'; dagoverzicht op een vast tijdstip; opnieuw koppelen zonder herstart;
  geen crash bij beschadigde sleutelopslag.
- **Web op de telefoon**: Opslaan niet meer onder de navigatie, waarschuwing bij niet-opgeslagen
  wijzigingen, 'Instellingen voor deze titel' op de detailpagina, Aanvragen met leverstatus
  bovenaan, 'gepauzeerd (noodstop)' in plaats van 'bezig'.
- **Updates**: melding als er een nieuwe versie is (server en app); 'Nieuw in deze versie' bij
  gewijzigde standaarden; echte logbestanden (logniveau werkt, ook in de supportbundel).
- **Releases**: worden pas gepubliceerd na groene tests en een rooktest van het alles-in-één-image
  op een database van een eerdere versie; versietags worden nooit overschreven.

## 1.2.1

- **Seerr ↔ Sonarr/Radarr**: MediaGuard geeft Seerr voortaan het LAN-adres van de NAS in plaats
  van het interne 127.0.0.1 van de alles-in-één-container (ook bij automatisch inrichten en de
  afwijkingscontrole), zodat de koppelingen in Seerr werken en niet meer worden teruggezet.
- Android-CI bouwt weer (setup-android zonder het verdwenen pakket 'tools'); handmatig starten
  hangt de APK aan de release.

## 1.2.0

- **Nieuw ontwerp**: vijf onderdelen (Overzicht · Activiteit · Bibliotheek · Ruimte · Aanvragen) plus
  Instellingen; hooguit één rij tabs per pagina; Instellingen op één pagina met zoeken en
  inhoudsopgave; alle oude adressen sturen door (inventaris: docs/design/2026-11-feature-inventory.md,
  afgedwongen door tests voor routes en instellingen).
- **Seizoenen en afleveringen** aan/uit in de bibliotheek, met 'meteen zoeken'.
- **Kwaliteit en upgrades** per bestand: custom formats met scores, doel gehaald?, en per upgrade
  de reden (oud → nieuw, welke formats, bron, AV1).
- **Aanvragen (Seerr)** in de web-app: zoeken, trending, aanvragen per seizoen, leverstatus.
- **Android-app** (APK bij de GitHub-release): dagoverzicht met dagelijkse melding, Seerr-aanvragen,
  status en bediening, bibliotheek; koppelen via QR (Instellingen → API-sleutels).
- **Anime op de GPU**: haalt het proefstuk het VMAF-doel niet, dan direct via de CPU ('omgeleid',
  geen fout); na 2 afkeuringen gaat de rest van de serie via de CPU.
- **Trage CPU-jobs**: na een vastloper een snelle route; jobs die > 12 uur zouden duren worden
  overgeslagen (instelbaar); lange jobs wijken overdag voor korte.
- **Dolby Vision-voorbewerking** toont voortgang; vastloper-controle kijkt naar verwerkte bytes.
- **'Geen winst — origineel blijft'** telt niet meer als fout.
- **Sonarr 'matched by ID'**: zulke downloads (ook anime met absolute nummering) importeert
  MediaGuard zelf via ManualImport.
- Thema-keuze op het apparaat wint van de serverinstelling.

## 1.1.2

Rapport 8 (veldtest op een echte installatie; het rapport staat niet in deze repo) en de anime-upgradegolf:
- **AV1 staat niet meer stil op 'werkgeheugen'**: de ZFS-cache telt als beschikbaar geheugen en
  er is één zichtbare, instelbare reserve (standaard 4 GB) i.p.v. een verborgen 5 %; de melding
  toont de echte rekensom; na 30 min stilstand volgt een melding.
- **GPU krijgt weer werk**: het optimalisatieplan laat AV1-items altijd doorstromen; alleen
  zoek-items wachten op de downloadlimiet (gemeten uit de clients zelf).
- **Geslaagde herkansingen** worden overgenomen en overgedragen in plaats van 'mislukt' te blijven.
- **VMAF**: proefstuk uit het drukste deel van de video; na een afkeuring één herkansing met
  hogere bitrate (of via de CPU).
- **Upgradegolf**: kwaliteitsdoel van de MediaGuard-profielen haalbaar gemaakt (anime 2200);
  minimale scorewinst voor een upgrade; items die 2× in 7 dagen upgraden worden 14 dagen
  afgeremd; 'Engelse dub' herkent ook de eigen bestandsnaam na AV1; diagnose 'Waarom N upgrades?'.
- **Opslagoverzicht**: geen dubbele tellingen meer (negatief 'onverklaard'), uurlijkse metingen.
- Downloadteller klopt, geen dubbele grab binnen 2 uur, geen dubbele regels in Recent.

## 1.1.1

- **SABnzbd-categorieën automatisch**: ontbrekende categorieën die Sonarr/Radarr gebruiken
  (bv. tv-anime, movies-anime) worden zelf aangemaakt; bestaande blijven onaangeroerd. Dit was
  waarschijnlijk de oorzaak van verdwijnende anime-downloads.
- **Vangnet voor niet-geïmporteerde downloads** (elke 30 min, alle clients en categorieën):
  eenduidige gevallen worden alsnog geïmporteerd, twijfel gaat naar de AI binnen de eisen, items
  uit de prullenbak worden eerst teruggezet en onterecht geblokkeerde releases weer vrijgegeven.
  De opruimer wacht tot dit vangnet het geprobeerd heeft. Zichtbaar onder Activiteit → Downloads.

## 1.1.0

Rapport 7 (veldtest op een echte installatie; het rapport staat niet in deze repo): meer werk naar de GPU en een rustigere wachtrij.
- **HDR10+ via de GPU** (als HDR10, zoals al ingesteld); wachtende HDR10+-jobs verhuizen vanzelf.
- **HDR10-metadata uit de GPU-encoder** wanneer de runtime dat doet; mkvmerge alleen nog als terugval.
- **Betere GPU-kwaliteit per MB**: lookahead en adaptieve B-frames (met automatische terugval),
  bitrate bijgestuurd op de VMAF van het proefstuk (doel 95, instelbaar).
- **GPU-plekken automatisch gemeten** (1/2/4 tegelijk) en het snelste aantal gekozen.
- **Dolby Vision 8.1**: nieuwe keuze 'behouden (CPU)' of 'omzetten naar HDR10 (GPU)'; standaard behouden.
- **Vangnet** ruimt op tot 85 % en maakt vooraf ruimte: geen pauzes meer op 'quarantaine vol';
  één getal voor de quarantaine.
- **Downloads weer zichtbaar**: direct uit SABnzbd/qBittorrent (ook de oude installaties tijdens de
  migratie); 'downloadclient niet bereikbaar' in plaats van 0.
- Voortgang tijdens proefstuk en meten; geen dubbele regels in Recent; één eenheid (GiB) voor
  besparingen; tweede CPU-plek alleen na een geslaagde proefperiode.

## 1.0.0

Eerste stabiele versie. Getest in zes rondes op een echte TrueNAS met Intel Arc A310 en Emby.
Testrapport 6 (veldtest op een echte installatie; het rapport staat niet in deze repo) en nieuw:
- **Opslag: wat kwam erbij** (Activiteit → Opslag & downloads): per 24 u / 7 d / 30 d wat er is
  gedownload of geüpgraded (per titel, bron en grootte), AV1-besparing, quarantaine/vangnet,
  downloadmappen en 'onverklaard'; dagelijks en wekelijks als melding, plus een melding bij
  sterke groei (standaard 200 GB per dag).
- **Noodstop** pauzeert ook AV1-encodes (zonder werkverlies) en hervat ze na opheffen.
- **Vastlopers**: elke CPU-plek eigen kernen; trage maar rekenende encodes worden niet meer
  gestopt; diagnose in het joblog; na 2× vastlopen een andere route; baan schaalt zelf terug.
- **Quarantaine**: weesbestanden (o.a. 252 GB uit de herstartlus) veilig opgeruimd; geen
  dubbele opruimronde meer; vangnet duidelijk zichtbaar; melding bij gewijzigde standaarden.
- Grabgolf na noodstop afgeremd; onbetrouwbare releasegroepen (<10 % import) tegengehouden.
- 'Activatie uitgesteld' noemt wat blokkeert en wanneer het opnieuw wordt geprobeerd.
- Noodstop-venster sluit niet meer bij typen; ZFS-cache apart in de geheugengrafiek.

## 0.3.8

Testrapport 5 (veldtest op een echte installatie; het rapport staat niet in deze repo) en de ontbrekende functies:
- **Harde CPU-grens**: encodes draaien op vaste kernen (75 % → 21 van 28); de GPU-baan wordt niet
  meer stilgezet door eigen CPU-encodes; de app blijft snel.
- **Kwaliteitsmeting**: VMAF (zelf meegebouwd) op 3 steekproeven per encode, onder 93 blijft het
  origineel; 3 vergelijkingsbeelden per omzetting.
- **HDR10+** standaard als HDR10 via SVT (libaom alleen bewust); duur vooraf geschat, jobs > 6 uur
  's nachts.
- **Verdwijnende downloads**: oorzaak gevonden (historie op serie- i.p.v. aflevering-ID); na 3×
  verdwenen 24 uur pauze met de SAB-reden; geweigerde releases geblokkeerd voor alle indexers.
- **Meldingen** via ntfy (QR, zonder account), Pushover, Telegram, Discord, Slack, e-mail, webhook;
  dagelijks en wekelijks overzicht ('Wat is er gebeurd').
- **Emby-koppeling** (ook Jellyfin/Plex): verversen na overdracht, afspeeltest, AV1 pauzeren tijdens
  kijken, recent bekeken niet omzetten; **NL-ondertitels** via Bazarr of OpenSubtitles.
- **Beveiliging**: tweestapsverificatie, ingelogde apparaten, herstelbundel automatisch buiten de NAS.
- Vangnet ook bij 'Direct verwijderen' (48 uur / 500 GB), opslagprognose, downloadbronnen-pagina,
  uitlegpagina 'Hoe werkt MediaGuard', elke instelling in de app met Nederlandse uitleg.

- **Meldingen buiten de browser** (Instellingen → Meldingen): ntfy (kant-en-klaar privé-onderwerp
  met QR-code), Pushover, Telegram, Discord, Slack, webhook en e-mail, elk met testknop. Per soort
  melding (fouten, wacht op jou, AV1 klaar, downloads, opslag, beveiliging): direct,
  dagoverzicht of nooit. Herhalen bij storing, uurmaximum per kanaal.
- **Dag- en weekoverzicht** (08:00, instelbaar) en Activiteit → Wat is er gebeurd.
- **Herstelbundel buiten de NAS**: automatisch naar een andere map, WebDAV, S3 of e-mail, met
  controle (Systeem → Back-ups).
- **Tweestapsverificatie** met QR en herstelcodes, optioneel verplicht voor beheerders;
  ingelogde apparaten met op afstand uitloggen; aanmeldgeschiedenis (Instellingen → Beveiliging).

## 0.3.7

Testrapport 4 (veldtest op een echte installatie; het rapport staat niet in deze repo):
- **Kwaliteitsondergrens CPU-encodes**: CRF per resolutie en HDR/DV (4K HDR/DV 21), plus een
  minimale bitrate (4K HDR/DV 8 Mbit/s, 1080p SDR 1,5); te laag → één herkansing, anders blijft
  het origineel. Dolby Vision-RPU per frame wordt gecontroleerd.
- **Banen vallen niet meer stil**: geheugencontrole gebruikt MemAvailable (ZFS-cache telde als
  bezet); herstelde jobs direct terug; reden zichtbaar als een baan niet start.
- 4K-CPU-terugval na GPU met snellere preset, bij voorkeur 's nachts; vastgelopen encode na 15 min
  gestopt met oorzaak.
- Meting na omzetting klopt (was een weergavefout), tijden in Nederlandse tijd, filmpagina's met
  Video en audio en Conversies, AV1-grootte in de bibliotheek.
- Downloadlus gestopt (geen voorkeur meer voor PROPER/REPACK), 'niet meer in de client' alleen
  als dat echt zo is, slechte releasegroepen worden vermeden.
- Oude PermissionError-jobs vanzelf opnieuw; teruggezette bestanden tellen niet als fout.
- Seerr-afwijking herstelt zichzelf; tellers, audit en meldingen kloppen en zijn Nederlands.
- Betere versies downloaden gaat automatisch binnen de quota.

## 0.3.6

Testrapport 3 (veldtest op een echte installatie; het rapport staat niet in deze repo) en 'set it and forget it':
- **Leveringsgarantie**: elke Seerr-aanvraag wordt zonder interactie gevolgd tot hij volgens de
  eisen geleverd is (Activiteit → Aanvragen); bij twijfel beslist de AI binnen de eisen.
- **AI zonder goedkeuring**: voorstellen worden automatisch uitgevoerd of met de veilige
  standaard afgehandeld; niets wacht meer op de gebruiker.
- **Downloads opruimen**: geïmporteerde, mislukte, verweesde en uitgeseede downloads gaan
  automatisch naar een prullenbak (7 dagen), met automatische padherkenning.
- **Encoder**: geen pogingenlus meer (grens geldt overal), engine blijft bereikbaar onder last,
  max. 2 4K-encodes per baan, GPU-preset medium/slow, hardware-decode op de Arc, eerlijk
  CPU-budget, bitrate-ondergrens voor GPU-encodes, voortgang in het logboek.
- **Autopilot** blijft indienen (niet-geplaatste films blokkeren de wachtrij niet meer).
- **Importfouten**: echte reden uit SABnzbd/qBittorrent, automatisch blokkeren en opnieuw zoeken;
  door Radarr geweigerde downloads automatisch weg.
- Custom format 'DV5 groot' geldig; herstellus stopt na 3 weigeringen met de echte fout.
- Fouten verdwijnen na een geslaagde herkansing, links naar jobs werken, prognose in weken,
  eigen mediainfo na overdracht, nette teksten (geen ruwe dicts), audit-tabel leesbaar.


- **Leveringsgarantie** (Activiteit → Aanvragen, docs/DELIVERY.md): elke Seerr-aanvraag wordt
  zonder interactie gevolgd tot hij volgens de eisen geleverd is. MediaGuard keurt wachtende
  aanvragen goed, zet anime in de anime-instantie, dwingt het MediaGuard-profiel en de
  aangevraagde seizoenen af, zoekt met terugvaltijd, vervangt mislukte downloads en
  vastzittende imports en werkt Seerr bij na levering. Keuzes ('anime of niet?') neemt de AI
  binnen de eisen; alleen wat niemand kan oplossen komt als informatieve melding.

## 0.3.5

Alle punten uit testrapport 2 (veldtest op een echte installatie; het rapport staat niet in deze repo):
- **Geen herstartlus meer**: de levenscontrole doet geen zwaar werk, is geduldiger en herstart
  nooit zolang ffmpeg rekent; na 3 onderbrekingen 'Mislukt' met oorzaak; joblog per fase.
- **Werkgeheugen en CPU**: geen nieuwe encode boven 80 % RAM; OOM duidelijk gemeld; decode-
  threads tellen mee in de CPU-limiet; encodes met lagere prioriteit; GPU-bezetting gemeten.
- **Eerst controleren, dan vervangen**: ontbrekende sporen = origineel blijft; mislukte
  vrijgave na vervangen = origineel automatisch terug; standaard alle ondertitels/audio.
- Geen dubbele of zinloze encodes (al AV1, bestaand resultaat, proefstuk van 60 s).
- Eén telling voor fouten/aandacht met doorklik; importmeldingen per item, automatische
  handmatige import bij een eenduidige ID-match; collecties naar de nieuwe map; tweelingmappen.
- Migratie: vinkje pas na echte overstap, verouderde scan opnieuw, >50 % ontbrekend = NO-GO.
- Optimalisatieplan: 'Bespaart ±', downloaden vraagt een eigen akkoord, echte prognose.
- Snellere meldingen (paginering, samenvatting), gedeelde live-verbindingen, Nederlandse teksten.

## 0.3.4

- **HDR10 via de GPU**: statisch HDR10 gaat standaard naar de Arc. De AV1-bitstream krijgt
  PQ/BT.2020-signalering, mastering display en content light worden daarna met mkvmerge
  teruggezet en gecontroleerd. Klopt de metadata niet (of faalt terugzetten of de GPU-encode),
  dan gaat dezelfde job één keer opnieuw via de CPU. Dolby Vision en HDR10+ blijven op de CPU.
  Wachtende HDR10-encodes verhuizen mee naar de GPU.

## 0.3.3

- **GPU**: wachtende encodes verhuizen vanzelf naar de Arc zodra de GPU-baan werkt (en terug
  als hij wegvalt); alle SDR, ook 4K, gaat naar de GPU; HDR10/DV/HDR10+ blijven op de CPU.
- **Canary-rem** van de oude AV1-app geldt niet meer: MediaGuard beslist zelf.
- **Optimalisatieplan** weer te vinden vanaf AV1 (kaart + juiste link), met status per AV1-item.

- **API-sleutels** voor koppelingen zoals Media Source Optimizer: header `X-Api-Key`, rol
  viewer of operator (nooit admin), aan te maken en in te trekken onder Instellingen →
  Gevorderd → API-sleutels. Alleen een hash wordt opgeslagen; de sleutel wordt één keer
  getoond. Ongeldige, ingetrokken of verlopen sleutels geven 401 zonder terugval op de cookie;
  rate limit op mislukte pogingen per IP; audit `apikey.created/revoked/rejected`; `mgk_…`
  wordt in logs gemaskeerd. OpenAPI documenteert `securitySchemes.apiKey`.
  Zie `docs/INTEGRATIONS.md` en `docs/SECURITY.md` §2a.
