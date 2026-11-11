# Integraties — andere apps koppelen aan MediaGuard

MediaGuard heeft een gedocumenteerde REST-API onder `/api/v1` (schema: `/api/v1/openapi.json`,
leesbaar via `/api/v1/docs`). Mensen loggen in met een sessiecookie + CSRF; andere apps
gebruiken een **API-sleutel**.

## API-sleutels in het kort

1. Ga als beheerder naar **Instellingen → Gevorderd → API-sleutels** en kies **Nieuwe sleutel**.
2. Geef een naam (bv. `mso`), kies de rol en eventueel een vervaldatum.
   - **Operator** (vooraf gekozen): lezen én acties zoals een herzoek starten.
   - **Kijker**: alleen lezen.
   - Beheerdersrechten bestaan niet voor sleutels.
3. Kopieer de sleutel uit het venster dat volgt. **Hij wordt niet opnieuw getoond**; kwijt =
   intrekken en een nieuwe maken.
4. Stuur hem mee als header `X-Api-Key: mgk_…`. CSRF is niet nodig.
5. Intrekken (knop **Intrekken**) werkt direct: het volgende verzoek met die sleutel krijgt
   `401 {"detail": "Ongeldige of ingetrokken API-sleutel"}`.

Controle: `GET /api/v1/auth/me` met de sleutel geeft
`{"username": "apikey:<naam>", "role": "operator", "csrf_token": null, …}`.
Beveiligingsdetails: `docs/SECURITY.md` §2a.

## Media Source Optimizer (MSO)

MSO (`lvos04/media-source-optimizer`) is een zelfstandige companion-app die Usenet-/torrent-
bronnen beoordeelt en ontbrekende items helpt vinden. Het wijzigt MediaGuard niet en gebruikt
alleen de gedocumenteerde API:

| Route | Methode | Minimale rol |
|---|---|---|
| `/api/v1/health` | GET | — |
| `/api/v1/auth/me` | GET | viewer |
| `/api/v1/status` | GET | viewer |
| `/api/v1/openapi.json` | GET | — (capability-detectie) |
| `/api/v1/wanted`, `/wanted/kpis`, `/wanted/reasons`, `/wanted/backfill/quota` | GET | viewer |
| `/api/v1/wanted/{id}`, `/wanted/{id}/diagnosis?cached=true` | GET | viewer |
| `/api/v1/policy/effective` | GET | viewer |
| `/api/v1/wanted/{id}/search` | POST | **operator** (de enige schrijfactie, telt mee in MediaGuard's zoekquotum) |

**Instellen:** maak een sleutel met rol **operator** (naam bv. `mso`) en plak hem in MSO in
het veld **MediaGuard-API-sleutel**. MSO toont dan *Verbonden · rol operator*. Na intrekken
toont MSO *Verstoord · sleutel geweigerd*. Wil je MSO alleen laten kijken, kies dan **Kijker**;
MSO laat dan geen herzoeken starten.

Waarom geen interne token: `X-MediaGuard-Token` is een admin-identiteit voor MediaGuard's eigen
diensten en geeft veel meer rechten dan MSO nodig heeft.

## Mediaservers (Emby, Jellyfin/Jelly Leon, Plex) en Nederlandse ondertitels

Instellen kan volledig in de app: **Instellingen → Koppelingen → Mediaserver & ondertitels**.

MediaGuard volgt **meer dan één mediaserver tegelijk** (ADR-0022): Emby en Jelly Leon (Jellyfin 12.x) kunnen naast
elkaar staan. Elke gekoppelde server heeft een eigen soort, adres, sleutel en gekoppelde mappen, en een eigen regel
met de stand: *Gekoppeld* of *Ontkoppeld* (hoe het is ingesteld) en daarnaast *Gezond*, *Storing* of *Onbekend* (wat
er gemeten is; *Gezond* staat er alleen na een gelukt contact).

- **Je bestaande koppeling blijft staan.** Na het bijwerken vanaf 1.2.2 is de server die je al had (bijvoorbeeld
  Emby) zonder één handeling de eerste in de lijst: zelfde adres, zelfde sleutel, zelfde gekoppelde mappen. Je hoeft
  niets opnieuw in te vullen en de koppeling valt niet weg.
- **Een server erbij:** *Mediaserver toevoegen* → soort, adres (of kies een gevonden server), naam en API-sleutel →
  *Toevoegen en testen*. De servers die al gekoppeld zijn, blijven precies zo gekoppeld; een fout bij de nieuwe
  server raakt de andere niet. Er kunnen hoogstens 8 extra servers bij.
- **Aanmelden per server:** Emby krijgt `X-Emby-Token`, Jellyfin en Jelly Leon 12.x krijgen
  `Authorization: MediaBrowser … Token="…"` (12.x weigert de oude vormen), Plex `X-Plex-Token`. Een sleutel staat
  nooit in een adres of in een logregel. Kies je *Emby* op een adres waar Jellyfin draait, dan zegt de test dat.
- **Kijkpauze over alle servers:** nieuwe encodes wachten zodra iemand op één van de servers kijkt. Is een server
  even niet bereikbaar, dan telt zijn laatst bekende stand nog hoogstens 10 minuten mee; de andere servers worden
  gewoon verder gevolgd.
- **Per server zichtbaar:** laatste contact, of er nu iemand kijkt (zonder namen of titels), de afspeeltests en de
  Nederlandse ondertitels.
- **Losmaken** (bijvoorbeeld als je Emby later zelf weghaalt): *… losmaken*. MediaGuard volgt die server dan niet
  meer en meldt er geen storing over; adres en sleutel blijven bewaard, dus *… weer koppelen* is één handeling. De
  eerste server kun je losmaken, niet verwijderen; een extra server kun je ook verwijderen (alleen adres, sleutel en
  gekoppelde mappen in MediaGuard gaan weg). MediaGuard verwijdert of wijzigt nooit iets in de mediaserver zelf, je
  bibliotheek of je kijkgeschiedenis.
- **Voor andere apps:** `GET /api/v1/mediaserver/status` houdt alle velden van 1.2.2 (zij beschrijven de eerste
  server) en krijgt er `servers[]` bij: per server soort, naam, adres, aan/uit, verbonden, laatste contact en
  aantallen — nooit sessies, titels of gebruikers. `POST /config` en `POST /test` werken zonder `server_id` precies
  als voorheen.

Getest met nagebouwde servers en met echte containers van Emby 4.10.2.0 en Jellyfin 12.4
(`tests/contract/mediaserver/RESULTS.md`). **NIET GEVALIDEERD** op een echte NAS met je eigen Emby, een echte
Jelly Leon-image en echte spelers.

- **Emby-sleutel maken:** Emby → Instellingen (tandwiel) → Geavanceerd → API-sleutels →
  Nieuwe sleutel (+) → naam `MediaGuard` → OK. Plak de sleutel in MediaGuard en kies
  *Opslaan en testen*. Het adres vindt MediaGuard meestal zelf (UDP 7359 en de gangbare poorten
  8096/9096); anders vul je het in.
- **Sleutel voor Jellyfin of Jelly Leon maken:** Dashboard → API-sleutels → + → naam `MediaGuard`. Plak de sleutel
  bij die server in MediaGuard.
- **Wat MediaGuard doet** (elk los aan/uit; de schakelaars gelden voor alle gekoppelde servers):
  - *Verversen na overdracht* — na elke AV1-vervanging of terugzetting ververst elke gekoppelde server precies
    dat bestand (`/Library/Media/Updated`); zonder gekoppelde map één volledige scan per 30 min.
  - *Afspeeltest* — ±10 min later `PlaybackInfo` met een gangbaar tv-profiel, bij elke gekoppelde server: direct
    afspelen, via omzetten, of mislukt (melden of het origineel terugzetten). De uitkomst staat per server; de
    samenvatting per overdracht is de slechtste uitkomst.
  - *AV1 pauzeren tijdens kijken* — via `/Sessions` van elke gekoppelde server: nieuwe encodes wachten (CPU+GPU of
    alleen GPU), lopende draaien met lage prioriteit door. Pauzereden in de encoder begint met `Kijken:`
    en wordt alleen door MediaGuard zelf opgeheven.
  - *Recent bekeken niet omzetten* — de AV1-autopilot slaat titels over die de laatste 24 uur
    bekeken zijn.
- **Ondertitels:** met een Bazarr-sleutel (Bazarr → Settings → General → Security) toont
  MediaGuard per titel 'NL-ondertitel ontbreekt' en laat Bazarr hooguit eens per dag per serie/film
  zoeken. Zonder Bazarr kan een OpenSubtitles-account (API-sleutel + inlog) gebruikt worden: dan
  zet MediaGuard een `.nl.srt` naast het bestand, alleen in de eigen bibliotheekmappen, nooit
  over een bestaand bestand, met een dagquotum.
