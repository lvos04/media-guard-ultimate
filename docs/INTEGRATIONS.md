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

## Mediaserver (Emby, ook Jellyfin/Plex) en Nederlandse ondertitels

Instellen kan volledig in de app: **Instellingen → Koppelingen → Mediaserver & ondertitels**.

- **Emby-sleutel maken:** Emby → Instellingen (tandwiel) → Geavanceerd → API-sleutels →
  Nieuwe sleutel (+) → naam `MediaGuard` → OK. Plak de sleutel in MediaGuard en kies
  *Opslaan en testen*. Het adres vindt MediaGuard meestal zelf (UDP 7359 en de gangbare poorten
  8096/9096); anders vul je het in.
- **Wat MediaGuard doet** (elk los aan/uit):
  - *Verversen na overdracht* — na elke AV1-vervanging of terugzetting ververst de server precies
    dat bestand (`/Library/Media/Updated`); zonder gekoppelde map één volledige scan per 30 min.
  - *Afspeeltest* — ±10 min later `PlaybackInfo` met een gangbaar tv-profiel: direct afspelen,
    via omzetten, of mislukt (melden of het origineel terugzetten).
  - *AV1 pauzeren tijdens kijken* — via `/Sessions`: nieuwe encodes wachten (CPU+GPU of alleen
    GPU), lopende draaien met lage prioriteit door. Pauzereden in de encoder begint met `Kijken:`
    en wordt alleen door MediaGuard zelf opgeheven.
  - *Recent bekeken niet omzetten* — de AV1-autopilot slaat titels over die de laatste 24 uur
    bekeken zijn.
- **Ondertitels:** met een Bazarr-sleutel (Bazarr → Settings → General → Security) toont
  MediaGuard per titel 'NL-ondertitel ontbreekt' en laat Bazarr hooguit eens per dag per serie/film
  zoeken. Zonder Bazarr kan een OpenSubtitles-account (API-sleutel + inlog) gebruikt worden: dan
  zet MediaGuard een `.nl.srt` naast het bestand, alleen in de eigen bibliotheekmappen, nooit
  over een bestaand bestand, met een dagquotum.
