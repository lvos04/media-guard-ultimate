# Meldingen, overzichten en de herstelbundel buiten de NAS

Alles is in de app in te stellen; er zijn geen omgevingsvariabelen of bestanden nodig.

## Meldingen (Instellingen → Meldingen)

| Kanaal | Wat je nodig hebt |
|---|---|
| ntfy (standaard) | De gratis ntfy-app. MediaGuard maakt zelf een privé-onderwerp en toont een QR-code; scannen en op **Aanzetten** tikken. Eigen ntfy-server kan ook (adres + optioneel token). |
| Pushover | Gebruikerssleutel en app-token. |
| Telegram | Bot-token (van @BotFather) en chat-ID. |
| Discord / Slack | Webhook-adres. |
| Webhook | Een adres dat JSON ontvangt (`title`, `body`, `link`, `priority`). |
| E-mail | Mailserver, poort, beveiliging (starttls/ssl/none), afzender, ontvanger, eventueel login. |

Elk kanaal heeft een **Testbericht**-knop. Per soort melding kies je *Direct*, *Dagoverzicht* of
*Nooit*. Standaard: fouten en beveiliging direct, de rest in het dagoverzicht. Mislukt verzenden,
dan probeert MediaGuard het opnieuw na 1, 2, 5, 15, 30 en 60 minuten. Boven het uurmaximum per
kanaal (standaard 20) gaan meldingen naar het dagoverzicht. Dezelfde (opgehoogde) melding wordt
hooguit eens per 6 uur opnieuw gepusht.

Vul onder *Extra* het adres van MediaGuard in (bijv. via je Cloudflare-tunnel) om in pushberichten
direct naar de juiste pagina te kunnen tikken.

## Bewaking op de achtergrond

Ook als niemand de app openheeft, loopt MediaGuard elke 5 minuten alles na: alle punten uit
'Wacht op jou' (met de AV1-engine), het gezondheidsmodel en de TrueNAS-pool. Fouten gaan direct
naar je telefoon, als soort *Fouten* (standaard *Direct*). Daarnaast eigen stilstandmeldingen:

| Melding | Wanneer (instelbaar onder Instellingen → Meldingen, *Geavanceerd*) |
|---|---|
| AV1-baan staat stil | > 30 min niets gestart terwijl er werk wacht. Kijken in Emby, je eigen pauze, stille uren en de noodstop tellen niet mee. |
| Aanvraag komt niet verder | > 12 uur op aangevraagd, toevoegen, importeren of controleren; downloaden > 24 uur. Zoeken telt niet. |
| Noodstop staat lang aan | > 2 uur, met een knop om hem op te heffen. |
| Seerr-aanvragen worden niet bijgewerkt | Ophalen uit Seerr mislukt al > 30 min (minstens twee pogingen). |
| Achtergrondtaak doet al lang niets | Geen geslaagde ronde in 3× de normale tijd (minimaal 30 min). Zie Systeem → Achtergrondtaken. |
| TrueNAS-pool niet gezond | Direct; het opruimen van originelen pauzeert tot de pool weer gezond is. De pool wordt ook gemeten als de bewaking uit staat. Is de quarantaine dan vol, dan wacht AV1 in plaats van verder te vullen. |
| AV1-engine werkt beperkt | Een echte storing die > 30 min aanhoudt. |

Geen spam: elk probleem geeft één push als het verschijnt (en weer één als er iets nieuws bij
komt). Is het twee rondes weg, dan verdwijnt de melding vanzelf; komt het binnen het uur terug,
dan volgt pas weer een push als het na dat uur nog steeds speelt. Het uurmaximum per kanaal en 'hooguit eens per 6 uur dezelfde melding'
gelden ook hier.

Een achtergrondtaak die door een onverwachte fout omvalt, start MediaGuard na 30 s – 15 min
vanzelf opnieuw. Per taak zie je onder Systeem → Achtergrondtaken wanneer hij voor het laatst
lukte en wat de laatste fout was (`GET /api/v1/monitor`).

## Overzichten (Activiteit → Wat is er gebeurd)

Elke ochtend (standaard 08:00, Europe/Amsterdam) één bericht over de vorige dag: bestanden omgezet
naar AV1 en de besparing, geleverde aanvragen, fouten, wat er op je wacht, plus de meldingen die
voor het overzicht bestemd waren. Op de ingestelde weekdag ook een weekoverzicht. Alle overzichten
zijn in de app terug te lezen; *Stuur nu een proefoverzicht* test de kanalen.

## Herstelbundel buiten de NAS (Systeem → Back-ups)

Na elke nieuwe herstelbundel kopieert MediaGuard de bundel én het versleutelde sleutelbestand
naar het gekozen doel en leest de kopie terug om de SHA-256 te vergelijken:

- **Andere map**: een in de app gekoppelde share of schijf (niet de map van de bundels zelf);
  oude kopieën boven het ingestelde aantal worden opgeruimd.
- **WebDAV** (Nextcloud, Stack, …): adres, gebruiker, (app-)wachtwoord.
- **S3-compatibel** (Backblaze B2, Wasabi, MinIO, …): adres, regio, bucket, map, sleutels.
- **E-mail**: via een e-mailkanaal; bundels groter dan 20 MB passen niet.

Mislukt een kopie, dan komt er een melding en probeert MediaGuard het na een uur opnieuw.
**Nu kopiëren en controleren** voert de kopie direct uit (ook als test).
