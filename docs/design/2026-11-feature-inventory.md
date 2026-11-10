# Functie-inventaris en plek in de nieuwe opzet (november 2026)

Gegenereerd met `scripts/gen-feature-inventory.py` uit de broncode en het instellingenregister. Handmatig is alleen de koppeling oud → nieuw (`ROUTES`, `DEEP_LINKS`, `OTHER_CONFIG` in het script). Afgedwongen door `apps/web/e2e/werk-ia.spec.ts` (elke route en deep link opent zonder 404 op de juiste plek) en `apps/web/e2e/werk-instellingen-dekking.spec.ts` (elke instelling staat op Instellingen).

## Samenvatting

- Hoofdonderdelen: Overzicht (/) · Activiteit (/activiteit) · Bibliotheek (/bibliotheek) · Ruimte (/av1) · Aanvragen (/aanvragen) · Instellingen (/instellingen)
- Routes (page.tsx): **45**, waarvan 9 bestaande omleidingen
- Deep links met parameters/ankers: **22**
- Instellingen in het register: **230**; zonder sectie: **0**
- Encoder-afstelling: **20** knoppen
- Overige configuratieschermen: **21**
- Routes zonder plek in de nieuwe opzet: **0**
- Bewust verwijderd: **geen**

## Routes

| Route | Bestand | Nu | Nieuwe plek | Sectie / anker | Oud adres |
|---|---|---|---|---|---|
| `/aanvragen` | `apps/web/app/(app)/aanvragen/page.tsx` | pagina | Aanvragen | Zoeken & ontdekken · Mijn aanvragen · Levering | nieuw |
| `/activiteit` | `apps/web/app/(app)/activiteit/page.tsx` | pagina | Activiteit | filterchips: Nu · Problemen · Downloads · Wat is er gebeurd · Geschiedenis | blijft |
| `/ai` | `apps/web/app/(app)/ai/page.tsx` | pagina | Instellingen › AI-adviseur | Instellingen#gereedschap | blijft |
| `/anime` | `apps/web/app/(app)/anime/page.tsx` | pagina | Instellingen › Anime-routering | Instellingen#gereedschap | blijft |
| `/audio` | `apps/web/app/(app)/audio/page.tsx` | pagina | Instellingen › Audio & ondertiteling | Instellingen#taal | blijft |
| `/av1/ai` | `apps/web/app/(app)/av1/ai/page.tsx` | omleiding → `/ai` | Omleiding |  | omleiding |
| `/av1/beslissingen` | `apps/web/app/(app)/av1/beslissingen/page.tsx` | omleiding → `/av1/wachtrij` | Omleiding |  | omleiding |
| `/av1/bibliotheken` | `apps/web/app/(app)/av1/bibliotheken/page.tsx` | omleiding → `/av1/instellingen` | Omleiding |  | omleiding |
| `/av1/encoder-instellingen` | `apps/web/app/(app)/av1/encoder-instellingen/page.tsx` | omleiding → `/av1/instellingen` | Omleiding |  | omleiding |
| `/av1/fouten` | `apps/web/app/(app)/av1/fouten/page.tsx` | omleiding → `/av1/wachtrij?status=mislukt` | Omleiding |  | omleiding |
| `/av1/instellingen` | `apps/web/app/(app)/av1/instellingen/page.tsx` | pagina | Instellingen › AV1-engine | via Instellingen#av1 en knop 'Instellingen' op Ruimte | blijft |
| `/av1/kwaliteit` | `apps/web/app/(app)/av1/kwaliteit/page.tsx` | pagina | Ruimte | tab Kwaliteit | blijft |
| `/av1/logs` | `apps/web/app/(app)/av1/logs/page.tsx` | omleiding → `(afhankelijk van parameters)` | Omleiding |  | omleiding |
| `/av1` | `apps/web/app/(app)/av1/page.tsx` | pagina | Ruimte | tab Besparing (+ sectie #opslag) | blijft |
| `/av1/profielen` | `apps/web/app/(app)/av1/profielen/page.tsx` | omleiding → `/av1/instellingen#profielen` | Omleiding |  | omleiding |
| `/av1/wachtrij` | `apps/web/app/(app)/av1/wachtrij/page.tsx` | pagina | Ruimte | tab Wachtrij | blijft |
| `/av1/workers` | `apps/web/app/(app)/av1/workers/page.tsx` | omleiding → `/av1/instellingen#workers` | Omleiding |  | omleiding |
| `/bibliotheek/[instance]/[id]/aflevering/[episode]` | `apps/web/app/(app)/bibliotheek/[instance]/[id]/aflevering/[episode]/page.tsx` | pagina | Bibliotheek › aflevering | Kwaliteit · Upgrades · Video en audio | blijft |
| `/bibliotheek/[instance]/[id]` | `apps/web/app/(app)/bibliotheek/[instance]/[id]/page.tsx` | pagina | Bibliotheek › titel | Seizoenen (aan/uit) · Kwaliteit · Upgrades | blijft |
| `/bibliotheek` | `apps/web/app/(app)/bibliotheek/page.tsx` | pagina | Bibliotheek | Series · Anime · Films (+ Ontbrekend, Kalender als chips) | blijft |
| `/dolby-vision` | `apps/web/app/(app)/dolby-vision/page.tsx` | pagina | Instellingen › Dolby Vision | Instellingen#av1 | blijft |
| `/downloads` | `apps/web/app/(app)/downloads/page.tsx` | pagina | Instellingen › Downloadclients | Instellingen#koppelingen | blijft |
| `/hoe-werkt-het` | `apps/web/app/(app)/hoe-werkt-het/page.tsx` | pagina | Hulp | vraagteken in de kopbalk · Instellingen#gereedschap | blijft |
| `/inrichten` | `apps/web/app/(app)/inrichten/page.tsx` | pagina | Instellingen › Koppelingen | Instellingen#koppelingen (eigen tabrij) | blijft |
| `/instellingen/api-sleutels` | `apps/web/app/(app)/instellingen/api-sleutels/page.tsx` | pagina | Instellingen › API-sleutels & app | Instellingen#beveiliging | blijft |
| `/instellingen/audit` | `apps/web/app/(app)/instellingen/audit/page.tsx` | omleiding → `/activiteit?tab=geschiedenis&view=audit` | Activiteit | Geschiedenis › Audit | omleiding |
| `/instellingen/beveiliging` | `apps/web/app/(app)/instellingen/beveiliging/page.tsx` | pagina | Instellingen › Beveiliging | Instellingen#beveiliging | blijft |
| `/instellingen/gebruikers` | `apps/web/app/(app)/instellingen/gebruikers/page.tsx` | pagina | Instellingen › Gebruikers | Instellingen#beveiliging | blijft |
| `/instellingen/geheimen` | `apps/web/app/(app)/instellingen/geheimen/page.tsx` | pagina | Instellingen › Geheimen | Instellingen#beveiliging | blijft |
| `/instellingen/gevorderd` | `apps/web/app/(app)/instellingen/gevorderd/page.tsx` | pagina | Instellingen | #gereedschap | omleiding |
| `/instellingen/instanties` | `apps/web/app/(app)/instellingen/instanties/page.tsx` | pagina | Instellingen › Instanties | Instellingen#koppelingen | blijft |
| `/instellingen/meldingen` | `apps/web/app/(app)/instellingen/meldingen/page.tsx` | pagina | Instellingen › Meldingen | Instellingen#meldingen | blijft |
| `/instellingen` | `apps/web/app/(app)/instellingen/page.tsx` | pagina | Instellingen | één pagina: secties + inhoudsopgave + zoeken | blijft |
| `/jobs` | `apps/web/app/(app)/jobs/page.tsx` | pagina | Activiteit | Geschiedenis | omleiding |
| `/kalender` | `apps/web/app/(app)/kalender/page.tsx` | pagina | Bibliotheek | chip Kalender | blijft |
| `/kwaliteit` | `apps/web/app/(app)/kwaliteit/page.tsx` | pagina | Instellingen › Kwaliteit & profielen | Instellingen#downloads | blijft |
| `/migratie` | `apps/web/app/(app)/migratie/page.tsx` | pagina | Instellingen › Migratie | Instellingen#gereedschap (+ Overzicht zolang er een migratie loopt) | blijft |
| `/ontbrekend` | `apps/web/app/(app)/ontbrekend/page.tsx` | pagina | Bibliotheek | chip Ontbrekend | blijft |
| `/` | `apps/web/app/(app)/page.tsx` | pagina | Overzicht | — | blijft |
| `/quarantaine` | `apps/web/app/(app)/quarantaine/page.tsx` | pagina | Ruimte | tab Terugzetten | blijft |
| `/release-lab` | `apps/web/app/(app)/release-lab/page.tsx` | pagina | Instellingen › Release Lab | Instellingen#gereedschap | blijft |
| `/seerr` | `apps/web/app/(app)/seerr/page.tsx` | pagina | Instellingen › Seerr | Instellingen#koppelingen | blijft |
| `/systeem` | `apps/web/app/(app)/systeem/page.tsx` | pagina | Instellingen › Systeem | Instellingen#systeem | blijft |
| `/login` | `apps/web/app/login/page.tsx` | pagina | Inloggen | — | blijft |
| `/setup` | `apps/web/app/setup/page.tsx` | pagina | Eerste installatie | — | blijft |

## Deep links

| Oud adres | Leidt naar | Toelichting |
|---|---|---|
| `/activiteit?tab=aanvragen` | `/aanvragen` | Aanvragen is een hoofdonderdeel |
| `/activiteit?tab=opslag` | `/av1#opslag` | Opslag & downloads hoort bij Ruimte |
| `/activiteit?tab=opslag&periode=7d` | `/av1?periode=7d#opslag` | periode blijft behouden |
| `/activiteit?tab=problemen` | `/activiteit?tab=problemen` | blijft (chip) |
| `/activiteit?tab=geschiedenis&view=audit` | `/activiteit?tab=geschiedenis&view=audit` | blijft |
| `/av1/wachtrij?job=1` | `/av1/wachtrij?job=1` | blijft (lade) |
| `/av1/wachtrij?status=mislukt` | `/av1/wachtrij?status=mislukt` | blijft |
| `/av1/fouten` | `/av1/wachtrij?status=mislukt` | bestaande omleiding |
| `/av1/logs?job=1` | `/av1/wachtrij?job=1` | bestaande omleiding |
| `/av1/profielen` | `/av1/instellingen#profielen` | bestaande omleiding |
| `/av1/workers` | `/av1/instellingen#workers` | bestaande omleiding |
| `/av1/ai` | `/ai` | bestaande omleiding |
| `/av1/beslissingen` | `/av1/wachtrij` | bestaande omleiding |
| `/av1/bibliotheken` | `/av1/instellingen` | bestaande omleiding |
| `/av1/encoder-instellingen` | `/av1/instellingen` | bestaande omleiding |
| `/instellingen/gevorderd` | `/instellingen#gereedschap` | Gevorderd wordt sectie Gereedschap |
| `/instellingen?tab=opslag` | `/systeem#opslag` | bestaande omleiding |
| `/inrichten?tab=seerr` | `/inrichten?tab=seerr` | blijft |
| `/kwaliteit?tab=optimaliseren` | `/kwaliteit?tab=optimaliseren` | blijft |
| `/systeem#quarantaine-wezen` | `/systeem#quarantaine-wezen` | blijft |
| `/ontbrekend?reason_code=import-failed` | `/ontbrekend?reason_code=import-failed` | blijft |
| `/migratie?run=1` | `/migratie?run=1` | blijft |

## Per pagina: tabs, parameters, ankers, knoppen, dialogen, kaarten

Automatisch uit de pagina en de componenten die hij gebruikt (3 niveaus diep). Elk onderdeel verhuist mee met de pagina naar de plek in de routetabel.

### `/aanvragen` → Aanvragen
- **Tabs/filters** (3): movies · trending · tv
- **Knoppen/acties** (14): Aanvraag versturen · Aanvragen · Aanvragen gecontroleerd · Alle {count} tonen · Annuleren · Gestopt; MediaGuard volgt deze aanvraag niet meer · Noodstop actief · Nu zoeken naar {title} · Opnieuw geprobeerd · Seerr is nog niet gekoppeld. Koppel Seerr bij Instellingen → Koppelingen om hier aan te vragen. · Seerr koppelen · Sluiten · Tijdlijn · {title} aanvragen
- **Dialogen/bevestigingen** (1): {title} aanvragen
- **Kaarten/blokken** (6): Aanvragen · Levering · Mijn aanvragen · Niets gevonden voor ‘{q}’. · Nog niets te tonen · Zoek een serie of film en vraag hem aan. MediaGuard volgt hem tot hij er is.

### `/activiteit` → Activiteit
- **Tabs/filters** (10): 30 · 7 · audit · downloads · events · gebeurd · geschiedenis · jobs · nu · problemen
- **Queryparameters** (7): instance · job · periode · q · status · tab · view
- **Knoppen/acties** (19): Alles is gelukt. Kies 'Alle statussen' om ook geslaagde taken te zien. · Alles tonen · Annuleren · Gedaan · Geen mislukte taken in de laatste 7 dagen · Geen taken voor dit filter · Genegeerd · Importeren · Meer acties · Meer laden · Meldingen instellen · Negeren · Nu verversen · Opnieuw · Sluiten · Taak #{id} · Taak geannuleerd · Taak opnieuw ingepland · Verborgen punten staan weer in de lijst
- **Kaarten/blokken** (42): AV1 · AV1 en vrijgave · Actie · Activiteit · Alles loopt. Mislukte taken van langer dan 7 dagen geleden tellen niet meer mee. · Alles wat nu bezig is: downloads, zoekopdrachten, AV1 en MediaGuard-taken. · Audit-tijdlijn · Autopilot · Bekijken · Deze versie van MediaGuard-core levert nog geen downloadstatistiek. Na de volgende update verschijnt hier per bron hoe vaak downloads lukken. · Downloads · Downloads die vastzitten · Downloadstatistiek nog niet beschikbaar · Eerdere overzichten · Even gepauzeerd · Fouten ({count}) · Gebeurtenissen · Geen mislukte downloads in de laatste 7 dagen · Geen problemen · Geen recente fouten in de downloadclients · Hersteld · Het overzicht dat MediaGuard elke ochtend stuurt, per dag terug te lezen. · Items waarvan downloads steeds verdwenen; MediaGuard probeert het later vanzelf opnieuw · Laatste fouten in de downloadclient · MediaGuard-taken · Mislukte en geweigerde downloads van de laatste 7 dagen, per oorzaak · Mislukte taken (laatste {days} dagen) · Nieuw · Nog geen afgeronde downloads in deze periode · Nog geen auditgebeurtenissen. · Nu in de downloadclients · Oud · Recent (24 uur) · Rechtstreeks uit SABnzbd en qBittorrent, vertaald · Sonarr/Radarr met problemen · Succes per bron · Taken · Vandaag tot nu toe · Wanneer · Wat MediaGuard afhandelde · Wie, wanneer, waarom, oude en nieuwe waarde — voor elke wijziging. · Zoeken & taken in Sonarr/Radarr

### `/ai` → Instellingen › AI-adviseur
- **Tabs/filters** (2): accepted · open
- **Ankers** (2): #ai-key · #ai-model
- **Knoppen/acties** (8): Feedback opgeslagen · Markeer onjuist · Opnieuw onderzoeken · Opslaan · Test verbinding · Toch uitvoeren · Verwerpen · Voorstel geaccepteerd
- **Kaarten/blokken** (11): AI Adviseur · Afgehandeld · Alles · Beleid: {policy} · Budget deze maand · Casussen · Geen casussen · Open · Optioneel. Alleen bij ambigue gevallen, alleen metadata, nooit autoriteit over destructieve acties. MediaGuard werkt volledig zonder AI. · Privacy & toestemming · Provider

### `/anime` → Instellingen › Anime-routering
- **Tabs/filters** (3): all · moved · open
- **Ankers** (1): #xem-id
- **Knoppen/acties** (9): Hier laten · Mapping toegepast · Ontleden · Sluiten · Terugzetten · TheXEM-mapping vernieuwd · Toepassen · Vernieuwen · Verplaats nu
- **Kaarten/blokken** (9): Absolute en seizoensnummering, cours, OVA's en specials. Hashbewijs en handmatige correcties. · Anime-nummering · Anime-routering · Handmatige mapping · Nog niets gerouteerd · Releasenaam ontleden · Seerr zet anime standaard in Sonarr Series / Radarr Films. Nieuwe anime zonder bestanden en zonder lopende download verplaatst MediaGuard naar Sonarr Anime / Radarr Animefilms; die acties verschijnen hier. · TheXEM-mapping · nieuwe anime automatisch naar de anime-instanties

### `/audio` → Instellingen › Audio & ondertiteling

### `/av1/instellingen` → Instellingen › AV1-engine
- **Ankers** (9): #gpu-meting · #grenzen · #lim-log · #onderdelen · #onderhoud · #profielen · #quarantaine-wezen · #uitvoering · #workers
- **Knoppen/acties** (14): Back-up gemaakt · Back-up maken · Browsermeldingen toestaan · Herstel getest · Herstel standaard · Herstel testen · Ongedaan maken · Opnieuw controleren · Opnieuw meten · Opslaan · Sluiten · TV-test geslaagd: vrijgeven · Tijdelijk resultaat verwijderen · Wachtrij veilig herstellen
- **Dialogen/bevestigingen** (12): Aan · Alleen het tijdelijke resultaat verdwijnt; de bron blijft staan. · Bevestig alleen als een omgezet bestand op je eigen TV correct in Dolby Vision afspeelde. · Dit onderdeel vraagt een expliciete bevestiging. · Dolby Vision vrijgeven na TV-test? · Tijdelijk resultaat verwijderen · Tijdelijk resultaat verwijderen? · Uit · Vastgelopen jobs gaan terug naar de wachtrij en tijdelijke bestanden worden opgeruimd. Lopende encodes worden niet aangeraakt. · Wachtrij herstellen? · Wachtrij veilig herstellen · {name} wijzigen?
- **Kaarten/blokken** (17): CPU-gebruik, temperatuurgrens en vrije ruimte stel je in bij Instellingen → Systeem & prestaties; MediaGuard zet ze door naar de AV1-engine. · Encodeprofielen · Encodes tegelijk · Encodes tegelijk, CPU-gebruik en tijdvenster stel je in onder Instellingen → Systeem & prestaties. · GPU-plekken optimaliseren · Grenzen · Herstel na een onderbreking en back-ups van de engine · Hoe de AV1-engine het werk uitvoert: hoeveel 4K tegelijk, nachtvenster, kwaliteitsgrenzen en CPU-gebruik. MediaGuard bepaalt wát er wordt omgezet; dit bepaalt hoe. · Kwaliteit en doelbitrate per resolutie. Wijzigingen gelden voor nieuwe scans. · Laatst gecontroleerd {when} · MediaGuard meet hoeveel encodes tegelijk de videokaart het snelst maken (1, 2 of 4) en kiest het beste aantal. · Meldingen in deze browser · Onderdelen · Onderhoud · Uitschakelen stopt alleen nieuw werk; lopende encodes maken netjes af. · Uitvoering · Weesbestanden in quarantaine

### `/av1/kwaliteit` → Ruimte
- **Ankers** (2): #trial-file · #trial-profile
- **Knoppen/acties** (1): Proefdraai starten
- **Kaarten/blokken** (8): De AV1-engine reageert niet. MediaGuard probeert het automatisch opnieuw; kijk bij Systeem of de engine draait. · Dolby Vision-controle · Drie korte test-encodes (CRF 27, 29, 31) van één bestand; je bibliotheek blijft onaangeroerd. · Geen Dolby Vision-bronnen gevonden · Herkenning, behandeling en controle van het resultaat · Laatste verificaties · Nog geen voltooide verificaties · Profiel proefdraaien

### `/av1` → Ruimte
- **Tabs/filters** (4): 24h · 30d · 7d · optimaliseren
- **Queryparameters** (1): periode
- **Ankers** (1): #opslag
- **Knoppen/acties** (20): Activeren · Afbreken · Alles activeren · Baan hervatten · Baan pauzeren · Details · GPU-meting herstarten · GPU-meting opnieuw gestart · Gekopieerd · Het opslagoverzicht kon niet worden geladen · Job staat weer in de wachtrij · Kopieer diagnose · Kopiëren · Nu opnieuw testen · Nu proberen · Opnieuw controleren · Opnieuw proberen · Origineel houden · QSV testen · Sluiten
- **Dialogen/bevestigingen** (5): Afbreken · Afbreken gevraagd · Encode afbreken? · Geactiveerd — origineel staat in de quarantaine · {file} stopt direct. Het origineel blijft ongewijzigd; het tijdelijke resultaat wordt opgeruimd.
- **Kaarten/blokken** (35): Alle bestanden ({n}) · Baan gepauzeerd · Belasting · Besparing · Besparing per dag · Bezig · De AV1-engine reageert niet. MediaGuard probeert het automatisch opnieuw; kijk bij Systeem of de engine draait. · De engine heeft even geen voortgang gemeld; de laatste bekende waarden staan er nog. · De engine kent job #{id} niet. Mogelijk is hij opgeruimd of opnieuw ingediend; zoek het bestand in de wachtrij. · Deze job bestaat niet (meer) · Er is niets geïmporteerd of omgezet. MediaGuard haalt de geschiedenis elke 15 minuten op. · Geen nieuwe meting · Gepauzeerd: {reason} · Geschatte tijd voor alle passes samen · Hardware · Het opslagoverzicht is nog niet beschikbaar · Job niet gevonden · Klaar om te activeren · Klaar over · Niets in deze periode · Nog geen metingen · Per bron · Per soort · Ruimte · Ruimte buiten de bibliotheek · Snelheid · Tijd · Waarom {n} upgrades? · Wat de engine in deze container kan gebruiken · Wat kwam erbij en waar ging de ruimte heen · Wordt elk uur gemeten; de eerste meting volgt vanzelf. · gemeten {when} · bestanden die als hardlink ook in de bibliotheek staan tellen niet dubbel · laatste uur · was {size} · {fps} fps · {speed}× realtime

### `/av1/wachtrij` → Ruimte
- **Tabs/filters** (1): optimaliseren
- **Queryparameters** (2): job · status
- **Knoppen/acties** (16): Afbreken · Baan hervatten · Baan pauzeren · Details · Gekopieerd · Job staat weer in de wachtrij · Job {id} selecteren · Kopiëren · Naar CPU · Naar GPU · Opnieuw proberen · Sluiten · Toon eerdere pogingen · Verberg eerdere pogingen · Volgende · Vorige
- **Dialogen/bevestigingen** (7): Afbreken · Afbreken gevraagd · Encode afbreken? · Wachtende jobs vervallen, lopende encodes stoppen. Originelen blijven ongewijzigd. · {count} jobs afbreken? · {count} jobs afgebroken · {file} stopt direct. Het origineel blijft ongewijzigd; het tijdelijke resultaat wordt opgeruimd.
- **Kaarten/blokken** (23): Baan gepauzeerd · Besparing · Bezig · De AV1-engine reageert niet. MediaGuard probeert het automatisch opnieuw; kijk bij Systeem of de engine draait. · De engine heeft even geen voortgang gemeld; de laatste bekende waarden staan er nog. · De engine kent job #{id} niet. Mogelijk is hij opgeruimd of opnieuw ingediend; zoek het bestand in de wachtrij. · Deze job bestaat niet (meer) · Duurt naar schatting langer dan {hours} uur: start bij voorkeur in het nachtvenster ({start}:00–{end}:00), of eerder als de baan anders stil zou staan · Een mislukte job laat de bron altijd ongewijzigd. · Fouten per oorzaak · Geen jobs met deze filters · Geen nieuwe meting · Gepauzeerd: {reason} · Geschatte duur vóór de start, uit de eigen geschiedenis per baan en resolutie · Geschatte tijd voor alle passes samen · Job niet gevonden · Jobs · Klaar over · Snelheid · Zodra MediaGuard bestanden laat omzetten, verschijnen ze hier als job. · alleen 's nachts · {fps} fps · {speed}× realtime · ± {duration}

### `/bibliotheek/[instance]/[id]/aflevering/[episode]` → Bibliotheek › aflevering
- **Tabs/filters** (8): conversions · history · identity · media · optimaliseren · overview · quality · releases
- **Knoppen/acties** (10): AI Adviseur · Actie ingepland · Bekijk {count} vergelijkingsbeelden · Gemarkeerd als juist · Identiteit controleren · Klopt, bestand houden · Optimaliseer · Waarom deze release? · Zoek alternatief · Zoek nu
- **Kaarten/blokken** (20): AV1-geschiedenis · Bibliotheek · Dit bestand is nog niet omgezet naar AV1 · Feiten · Gemeten door MediaGuard na de omzetting · Historie · Historie kon niet worden opgehaald · Identiteitsbewijs · Klopt het bestand? · Kwaliteit · Nog geen historie in Sonarr/Radarr · Nog niets te tonen · Releases · Sonarr/Radarr heeft (nog) geen mediainfo voor dit bestand · Upgrades · Vergelijk beschikbare releases en de rangschikking van het beleid in Release Lab. · Video en audio · Waarom deze release? · Waarom deze status? · {count} bronnen

### `/bibliotheek/[instance]/[id]` → Bibliotheek › titel
- **Tabs/filters** (9): conversions · evidence · geschiedenis · history · media · optimaliseren · quality · seasons · upgrades
- **Ankers** (3): #titel-instellingen · #title-category · #title-nosearch
- **Knoppen/acties** (20): Annuleren · Bekijk {count} vergelijkingsbeelden · Bevestigen · Bibliotheek vrijgeven · Controle ingepland · Herstel standaard · Inventarisatie ingepland · Metadata verversen · Ongedaan maken · Opslaan · Optimaliseer · Seizoenscompleetheid · Sluiten · Uitzondering weghalen · Verifiëren · Verwerpen · Waarom deze release? · Weer vergrendelen · Zoek ontbrekend · Zoekopdracht ingepland
- **Dialogen/bevestigingen** (9): Bibliotheek vrijgegeven · Bibliotheek vrijgeven · Er wordt niet meer gezocht naar ontbrekende of betere afleveringen van dit seizoen. Bestanden blijven staan. · Nieuwe seizoenen automatisch volgen? · Nieuwe seizoenen niet meer automatisch volgen? · Nieuwe seizoenen worden automatisch gemonitord zodra ze verschijnen. · Opgeslagen · Seizoen {n} niet meer volgen? · Weer vergrendelen
- **Kaarten/blokken** (18): AV1-geschiedenis · Bewijs · Bibliotheek · Bibliotheek vrijgeven? · Bibliotheek weer vergrendelen? · Dit bestand is nog niet omgezet naar AV1 · Gemeten door MediaGuard na de omzetting · Historie · Historie kon niet worden opgehaald · Instellingen voor deze titel · Klopt het bestand? · Kwaliteit · Nog geen historie in Sonarr/Radarr · Nog niets te tonen · Sonarr/Radarr heeft (nog) geen mediainfo voor dit bestand · Upgrades · Video en audio · Zoekgeschiedenis van MediaGuard

### `/bibliotheek` → Bibliotheek
- **Tabs/filters** (9): anime · anime_movie · cutoff_unmet · missing · movie · optimaliseren · tv · unmonitored · upcoming
- **Queryparameters** (5): instance · q · sort · state · type
- **Knoppen/acties** (6): Backfill {instance} · Inventarisatie ingepland · Opnieuw inventariseren · Volgende · Vorige · Zoekopdracht ingepland
- **Kaarten/blokken** (5): AV1 (MediaGuard) · Alle series, anime en films over de vier instanties. Sortering en filters blijven bewaard in de URL. · Bibliotheek · Er zijn geen titels die bij deze filters passen. · Geen titels voor deze filters

### `/dolby-vision` → Instellingen › Dolby Vision
- **Tabs/filters** (4): auto_after_verify · experimental_keep_source · keep_original · trial_copy
- **Ankers** (6): #dv-client · #dv-note · #dv-path · #dv-prof · #dv-server · #routes
- **Knoppen/acties** (16): Afspeelketen bevestigd · Annuleren · Bevestig client · Bevestigde afspeelketens · Bibliotheek vrijgeven · Herstel standaard · Ongedaan maken · Opslaan · Proefconversie ingepland · Proefconversie starten · Proefconversies · Sluiten · Test een bestand · Verwerpen · Verwijderen · Weer vergrendelen
- **Dialogen/bevestigingen** (9): Bevestig client · Bevestig een client die DV-AV1 bij jou correct afspeelt. · Bibliotheek vrijgegeven · Bibliotheek vrijgeven · Codeert met CPU SVT-AV1 naar een aparte kopie. Het origineel wordt niet aangeraakt. · Leg vast dat deze afspeelketen bij jou werkt. Dit vermindert waarschuwingen, maar slaat geen technische controles over. · Nog geen bevestigde afspeelketens · Test een bestand · Weer vergrendelen
- **Kaarten/blokken** (9): Bibliotheek vrijgeven? · Bibliotheek weer vergrendelen? · De hoofdschakelaar staat in Instellingen. Hier stel je per serie of film een uitzondering in. · Dolby Vision & HDR · Dolby Vision → AV1 toestaan · Instellingen · Kies een titel voor een uitzondering · Profielondersteuning · Routes voor HDR10+ en Dolby Vision

### `/downloads` → Instellingen › Downloadclients

### `/hoe-werkt-het` → Hulp
- **Ankers** (1): #flow-arrow
- **Kaarten/blokken** (5): Elke stap gaat vanzelf; je hoeft alleen in te grijpen als MediaGuard het niet kan oplossen · Hoe werkt MediaGuard · Van aanvraag tot je bibliotheek · Van aanvraag tot opgeruimd: wat MediaGuard doet en wat de woorden betekenen · Woordenlijst

### `/inrichten` → Instellingen › Koppelingen
- **Tabs/filters** (4): direct · instellingen · none · prowlarr
- **Queryparameters** (1): tab
- **Ankers** (22): #ext · #lang · #live-paths · #live-seerr · #ms-key · #ms-url · #pr-key · #pr-url · #pr-url-arr · #propers · #proto · #recycle · #ren-inst · #seerr-key · #seerr-url · #subs-bazarr-key · #subs-bazarr-url · #subs-os-key · #subs-os-pass · #subs-os-user · #td · #ud
- **Knoppen/acties** (51): Account verwijderen · Afwijkingscontrole uitgevoerd · Alleen wat er bij {section} anders is dan MediaGuard wil. · Alles testen · Alles tonen · Annuleren · Bekijk wijzigingen · Controleer · Controleren · Downloadclient toevoegen · Hernoemen · Hernoemen gestart · Indexer toevoegen · Inrichting toegepast · Invullen · Nu op afwijkingen controleren · Nu zoeken · Ongedaan maken · Opnieuw zoeken · Opruimen bevestigen · Opslaan · Opslaan en testen · Optimaliseren · Optimaliseren uitgevoerd · Optioneel: kies titels om alleen die te hernoemen. Leeg = alles in deze instantie. · Oude profielen opgeruimd · Oude profielen opruimen · Resultaat optimaliseren · Resultaat van toepassen · Routes testen · Seerr ingericht · Seerr inrichten · Sleutel verwijderen · Sluiten · Teruggezet · Terugzetten naar gemigreerde instellingen · Test · Toepassen · Toepassen ({count}) · Verbinding testen · Verschil tonen: {section} bij {name} · Verwerpen · Verwijderen · Verwijderen uit de keuze · Verzoeken gesynchroniseerd · Verzoeken synchroniseren · Voorvertoning · Wijzigingen (voorbeeld) · Wissen · {changes} wijziging(en): {create} nieuw, {update} bijwerken, {delete} verwijderen · {count} bestand(en) hernoemen…
- **Dialogen/bevestigingen** (7): Inrichting toepassen? · MediaGuard maakt eerst een volledige momentopname van de gemigreerde configuratie; terugzetten kan daarna altijd. Er worden geen zoekacties gestart en geen bestanden hernoemd. · MediaGuard voert de getoonde wijzigingen uit op de instanties en Prowlarr, en leest daarna alles opnieuw in ter controle. · Nu optimaliseren? · Sonarr/Radarr hernoemt alleen de geselecteerde bestanden (commando RenameFiles). Bestanden met een lopende AV1-taak of herstelactie worden overgeslagen. · Terugzetten naar gemigreerde instellingen? · {count} bestand(en) hernoemen?
- **Kaarten/blokken** (50): AV1 pauzeren tijdens kijken · Afspeeltest · Alleen voor titels waarvoor Nederlandse ondertitels gewenst zijn (Instellingen → Taal). · Alles heeft al de juiste naam · Als de afspeeltest mislukt · Automatisch zoeken · Bestanden hernoemen volgens nieuw schema · Downloadclients · Geen instanties om te optimaliseren · Indexers · Instantie · Koppelingen inrichten · Kwaliteit · Mappen in Sonarr/Radarr · MediaGuard laat dit item dan voortaan met rust. · MediaGuard richt de vier Sonarr/Radarr-instanties, Prowlarr en Seerr zelf in en houdt ze zo. Je voert alles hier één keer in. · MediaGuard zoekt ze zelf via Bazarr of OpenSubtitles; je hoeft niets te doen. · Mediabeheer · Mediaserver · Met je Emby-sleutel ververst MediaGuard na elke AV1-vervanging het bestand in Emby, test of het afspeelt, pauzeert nieuwe encodes terwijl iemand kijkt en zet niets om wat net bekeken is. · NL-ondertitel ontbreekt ({count}) · Na elke AV1-vervanging of terugzetting ververst {label} precies dat bestand. · Niets geconfigureerd · Nieuwe encodes wachten zolang iemand kijkt; lopende draaien met lage prioriteit door. · Nog nodig · OpenSubtitles gebruiken · Optimaliseren na migratie · Oude profielen · Pas na een proefimport uit de migratiewizard staat een instantie hier klaar. · Recent bekeken niet omzetten · Routes nog niet getest · Seerr · Seerr-servers en routetest · Status per instantie en sectie · Stuurt vier testverzoeken (serie, anime, film, animefilm) en controleert welke instantie ze ontvangt. Er wordt niets aangevraagd. · Vertragingsprofiel · Verversen na overdracht · Voorvertoning optimaliseren · Wat de laatste {hours} uur bekeken is, zet MediaGuard pas later om. · Welke instantie bij welke Seerr-server hoort. · Zet een .nl.srt naast het bestand, alleen in je bibliotheekmappen; overschrijft nooit iets. Gratis accounts: 5–20 per dag. · Zo staat het nu in de instantie; aan/uit zetten en testen. · de vier Seerr-servers, pas na jouw bevestiging · gemigreerde instellingen vervangen door die van MediaGuard, met momentopname · klaar om in te richten · komen in alle vier de instanties, elk met een eigen categorie · nooit automatisch; alleen wat je bevestigt · via Prowlarr of direct · vul dit eerst in · ± 10 minuten na een overdracht vraagt MediaGuard of een gangbare tv het bestand kan afspelen.

### `/instellingen/api-sleutels` → Instellingen › API-sleutels & app
- **Tabs/filters** (2): operator · viewer
- **Ankers** (4): #apikey-expires · #apikey-name · #apikey-role · #apikey-value
- **Knoppen/acties** (9): Annuleren · Ik heb hem bewaard · Intrekken · Kopiëren · Nieuwe sleutel · Nog geen API-sleutels. Maak er een aan om bijvoorbeeld Media Source Optimizer te koppelen. · Sleutel aanmaken · Sleutel maken en QR-code tonen · Sluiten
- **Dialogen/bevestigingen** (7): Android-app koppelen · Deze code bevat de sleutel en wordt niet opnieuw getoond. · Er bestaat al een sleutel ‘android-app’. Een nieuwe vervangt de oude; de app moet dan opnieuw koppelen. · Intrekken · Nieuwe sleutel · Scan met de MediaGuard-app · Sleutel '{name}' aangemaakt
- **Kaarten/blokken** (6): API-sleutels · Andere apps (zoals Media Source Optimizer) praten met MediaGuard via header X-Api-Key. Een sleutel heeft nooit beheerdersrechten. · Android-app koppelen · Elke app die deze sleutel gebruikt, wordt meteen geweigerd. Dit kan niet ongedaan worden gemaakt; maak zo nodig een nieuwe sleutel. · Maak een sleutel voor de app en scan de QR-code met MediaGuard op je telefoon. · Sleutel '{name}' intrekken?

### `/instellingen/beveiliging` → Instellingen › Beveiliging
- **Ankers** (6): #disable-pw · #pw-again · #pw-current · #pw-new · #regen-code · #totp-code
- **Knoppen/acties** (11): Alle andere apparaten uitloggen · Annuleren · Bevestigen · Codes kopiëren · Ik heb ze bewaard · Nieuwe herstelcodes · Sluiten · Tweestapsverificatie instellen · Uitloggen · Uitzetten · Wachtwoord wijzigen
- **Dialogen/bevestigingen** (4): Bewaar je herstelcodes · Nieuwe herstelcodes · Tweestapsverificatie instellen · Tweestapsverificatie uitzetten?
- **Kaarten/blokken** (7): Aanmeldgeschiedenis · Andere apparaten worden daarna uitgelogd; dit apparaat blijft ingelogd. · Herken je een apparaat niet? Log het uit en verander je wachtwoord. · Ingelogde apparaten · Regels voor iedereen · Tweestapsverificatie · Wachtwoord wijzigen

### `/instellingen/gebruikers` → Instellingen › Gebruikers
- **Ankers** (4): #new-pass · #new-role · #new-user · #reset-pass
- **Knoppen/acties** (10): Annuleren · Gebruiker toevoegen · Gebruikers · Inschakelen · Nieuw wachtwoord · Opslaan · Rollen: beheerder (alles), operator (bedienen en instellen), kijker (alleen lezen). · Sluiten · Toevoegen · Uitschakelen

### `/instellingen/geheimen` → Instellingen › Geheimen
- **Ankers** (2): #secret-name · #secret-value
- **Knoppen/acties** (8): API-sleutels en wachtwoorden worden server-side versleuteld bewaard. De waarde wordt nooit teruggestuurd naar de browser. · Annuleren · Geheim instellen · Geheimen · Opslaan · Sluiten · Vervangen · Verwijderen
- **Dialogen/bevestigingen** (5): Geheim instellen · Geheim {name} verwijderen? · Integraties die dit geheim gebruiken kunnen daarna niet meer verbinden tot je een nieuwe waarde instelt. · Vervangen · Verwijderen
- **Kaarten/blokken** (2): Nog geen geheimen ingesteld. · Toegestane voorvoegsels: instance:, source:, seerr:, ai:, truenas:, notify:, indexer:, downloadclient:

### `/instellingen/gevorderd` → Instellingen

### `/instellingen/instanties` → Instellingen › Instanties
- **Tabs/filters** (1): seerr
- **Ankers** (4): #inst-key · #inst-name · #inst-public · #inst-url
- **Knoppen/acties** (8): Annuleren · Beheer · Hervat · Opslaan · Pauzeer · Seerr-routetest · Seerr-servers en routetest · Test
- **Kaarten/blokken** (4): Geen instanties geregistreerd · Migratie · Seerr-routering · Welke titel bij welke instantie hoort

### `/instellingen/meldingen` → Instellingen › Meldingen
- **Ankers** (7): #digest-time · #digest-weekday · #notify-base · #notify-growth · #notify-kind · #notify-name · #notify-rate
- **Knoppen/acties** (10): Aanzetten · Annuleren · Kanaal toevoegen · Opslaan · Sluiten · Stuur nu een proefoverzicht · Testbericht · Uitzetten · Verwijderen · Wijzigen
- **Dialogen/bevestigingen** (2): Kanaal toevoegen · Verwijderen
- **Kaarten/blokken** (8): Er gaan geen berichten meer naar dit kanaal. Opgeslagen sleutels worden gewist. · Eén bericht met wat MediaGuard deed: hoeveel bestanden er zijn omgezet, hoeveel ruimte dat scheelde en wat er nog op jou wacht. · Kanaal '{name}' verwijderen? · Kanalen · Overzichten · Per soort melding: meteen een bericht, alleen in het dagoverzicht, of nooit (dan zie je het alleen in de app). · Waar de berichten heen gaan. Je kunt meerdere kanalen tegelijk gebruiken. · Wat wanneer melden

### `/instellingen` → Instellingen
- **Queryparameters** (7): id · label · open · q · scope · tab · view
- **Ankers** (1): #settings-category
- **Knoppen/acties** (15): Andere titel kiezen · Bekijk · Bibliotheek vrijgeven · Effect van je wijzigingen · Effectieve waarden vóór en na, berekend door de backend zonder op te slaan. · Herstel standaard · Ongedaan maken · Opslaan · Sluiten · Uitzondering per serie, instantie of mediasoort instellen · Veilige standaard herstellen · Verbergen · Verwerpen · Voorbeeldbeslissing · Weer vergrendelen
- **Dialogen/bevestigingen** (4): Bibliotheek vrijgegeven · Bibliotheek vrijgeven · Ja, direct verwijderen · Weer vergrendelen
- **Kaarten/blokken** (10): Bibliotheek vrijgeven? · Bibliotheek weer vergrendelen? · Geen instellingen gevonden voor “{q}”. · Geen instellingen ondersteunen deze scope. · ID, bv. sonarr-anime:42 · Kies eerst hierboven een titel. · Niets gevonden voor ‘{q}’. Probeer een ander woord, of bekijk ‘Per serie of instantie’. · Originelen direct verwijderen? · Scope-ID · Zodra een nieuw (AV1-)bestand de controle doorstaat, verwijdert MediaGuard het origineel meteen. Je kunt een bestand daarna niet meer terugzetten als je later iets mis ziet. De veilige standaard is: originelen een aantal dagen bewaren.

### `/jobs` → Activiteit
- **Queryparameters** (1): status

### `/kalender` → Bibliotheek
- **Tabs/filters** (4): anime · anime_movie · movie · tv
- **Knoppen/acties** (4): Deze week · Ook niet-gemonitord · Volgende week · Vorige week
- **Kaarten/blokken** (2): Kalender · Nieuwe afleveringen, seizoenen en films met monitoring en verwachte beschikbaarheid.

### `/kwaliteit` → Instellingen › Kwaliteit & profielen
- **Queryparameters** (6): instance · movie · season · series · tab · type
- **Ankers** (3): #av1-items · #profile-json · #ren-inst
- **Knoppen/acties** (33): Alleen simulatie — niets wordt gewijzigd · Annuleren · Downloaden bevestigd · Downloaden bevestigen · Hernoemen · Hernoemen gestart · Herstel standaard · Hervat · Opgeslagen · Opruimen bevestigen · Opslaan · Optimalisatie starten · Optimaliseren · Optimaliseren uitgevoerd · Optioneel: kies titels om alleen die te hernoemen. Leeg = alles in deze instantie. · Oude profielen opgeruimd · Oude profielen opruimen · Pauzeer · Resultaat optimaliseren · Sluiten · Standaard hersteld · TRaSH importeren · Teruggezet · Terugzetten naar gemigreerde instellingen · Toepassen · Verwijderen · Verwijderen uit de keuze · Voorbeeld hele bibliotheek · Voorbeeld selectie · Voorbeeld: wat zou er met je bibliotheek gebeuren? · Voorvertoning · Wijzigen · {count} bestand(en) hernoemen…
- **Dialogen/bevestigingen** (12): Elk bestand wordt pas vervangen na verificatie en met een herstelbaar origineel. Je kunt de job pauzeren en hervatten. · MediaGuard maakt eerst een volledige momentopname van de gemigreerde configuratie; terugzetten kan daarna altijd. Er worden geen zoekacties gestart en geen bestanden hernoemd. · Nu optimaliseren? · Optimalisatie gestart · Optimalisatie starten · Plak de JSON van TRaSH Guides (custom formats). Wordt gevalideerd vóór opslaan. · Sonarr/Radarr hernoemt alleen de geselecteerde bestanden (commando RenameFiles). Bestanden met een lopende AV1-taak of herstelactie worden overgeslagen. · TRaSH importeren · Terugzetten naar gemigreerde instellingen? · Wijzigingen krijgen een nieuwe versie en staan in de historie; ‘Herstel standaard’ zet ingebouwde profielen terug. · {count} bestand(en) hernoemen? · {count} bestanden optimaliseren?
- **Kaarten/blokken** (17): Alles heeft al de juiste naam · Alles over kwaliteit op één plek: profielen, bestanden verbeteren en de instellingen in Sonarr/Radarr. · Bestanden hernoemen volgens nieuw schema · Dit plan zet niets naar AV1 om. · Eerdere plannen · Geen instanties om te optimaliseren · Kwaliteitsprofielen · Naar AV1 in dit plan · Nog geen TRaSH custom formats geïmporteerd · Nog geen optimalisatieplan · Optimaliseren na migratie · Oude profielen · Pas na een proefimport uit de migratiewizard staat een instantie hier klaar. · TRaSH custom formats · Voorvertoning optimaliseren · gemigreerde instellingen vervangen door die van MediaGuard, met momentopname · nooit automatisch; alleen wat je bevestigt

### `/migratie` → Instellingen › Migratie
- **Tabs/filters** (4): av1 · instellingen · optimaliseren · wizard
- **Queryparameters** (3): run · step · tab
- **Ankers** (7): #av1-path · #av1-rules · #av1-src · #live-key · #live-name · #live-url · #run-name
- **Knoppen/acties** (58): Alle runs · Alleen simulatie — niets wordt gewijzigd · Alles opnieuw voorstellen · Annuleren · Archiveren · Audit bijgewerkt · Audit uitvoeren · Back-up laten maken en doorgaan · Back-up ophalen en controleren · Bevestig eerst de mapping bron → doel (stap 3). · Bron gearchiveerd · Bron getest · Bron toevoegen en testen · Controle vooraf uitvoeren · Doorgaan · Gereedheid controleren · Gestart als job — voortgang hieronder · Hervatten · Importeren vanaf pad · Importvoorbeeld · Importvoorbeeld bijgewerkt · Mapping bevestigen · Mappingvoorstel bijgewerkt · MediaGuard laten doorlopen · Naar de mapping · Open in Bulkoptimalisatie · Openen · Opgeslagen · Opnieuw importeren · Opnieuw uitvoeren · Opslaan · Overschakelen hervatten… · Overschakelen… · Paden controleren · Plan #{id} · {count} items. · Proefimport starten · Regel toevoegen · Rollback starten · Rollbackplan · Rollback… · Run #{id} · Run annuleren · Run geannuleerd · Run starten · Scannen · Selectie accepteren ({count}) · Sluiten · Test · Vergelijken · Verwijderen · Volgende · Voorbeeld maken · Voorbeeld vernieuwen · Voorstel maken · Voorstel vernieuwen · Vorige · Wijzigingen opslaan · Zoeken activeren…
- **Dialogen/bevestigingen** (14): Alleen de doel-fork wordt gewijzigd. Hij staat in proefmodus, dus er wordt niets gezocht of gedownload. · Alleen de registratie in MediaGuard wordt gearchiveerd en de API-sleutel gewist. Je oude instantie en de opgeslagen back-ups blijven onaangeroerd. · Bevestigd · Bevestigd door {by} · {at} · Bron ‘{name}’ archiveren? · De run wordt gestopt; rapporten en back-ups blijven bewaard. Lopende overschakelings moeten eerst afgerond of teruggedraaid zijn. · Doel · Ik sta toe dat MediaGuard het niet-destructieve Backup-commando op mijn oude instantie uitvoert. · Mapping bevestigd · Proefimport naar {target}? · Rollback van {target}? · Run #{id} annuleren? · Typ {word} om te bevestigen · Zet alles terug naar de staat van vóór de overschakeling. Werkt ook na een mislukte of halve overschakeling.
- **Kaarten/blokken** (32): AV1-historie importeren · Back-ups uploaden · Bron {id} · Bronnen · Bronnen & opties · Doel · Een {app}-bron kan niet naar een {target}-doel · Eerdere imports · Gecontroleerd · Gemarkeerde items ({count}) · Herkomst (eerste 100) · Live bron toevoegen · Lokaal en Seerr · Migratie · Migratieruns · Niet gecontroleerd · Nieuwe migratierun · Nog geen bronnen · Nog geen herkomstgegevens · Nog geen run · Nog niets geïmporteerd · Opties van deze run · Overschakelen naar {target} · Paden herschrijven · Sonarr normaal/anime en Radarr normaal/anime · Start een run zodra je bronnen klaarstaan. · URL + API-sleutel van je oude instantie · Upload de back-ups van je oude instanties of voeg een live bron toe. · Zet je bestaande Sonarr/Radarr veilig over naar de vier MediaGuard-instanties. Je oude installatie wordt tot de overschakeling nooit gewijzigd. · Zoeken activeren op {target} · bron-pad → pad in de container · uit de oude AV1-app

### `/ontbrekend` → Bibliotheek
- **Tabs/filters** (2): cutoff · missing
- **Queryparameters** (3): instance · reason · reason_code
- **Ankers** (2): #nl-ondertitels · #wanted-items
- **Knoppen/acties** (21): Backfill ingepland · Backfill nu · Backfill-plan (voorbeeld) · Backfill-plan maken · Diagnose · Diagnose · {title} · Filter wissen · Gedaan · Meer acties · Minder tonen · Oplossen · Sluiten · Toon items · Toon nog {count} · Uitgesloten van zoeken · Uitsluiten · Volgende · Vorige · Zo zou de backend de zoekopdrachten over de nachtvensters verdelen. Er is nog niets gestart. · Zoek nu · Zoekopdracht ingepland
- **Kaarten/blokken** (10): Backfill per instantie · Geen ontbrekende afleveringen · Laatst gezocht · MediaGuard zoekt ze zelf via Bazarr of OpenSubtitles; je hoeft niets te doen. · NL-ondertitel ontbreekt ({count}) · Nooit · Ontbrekend & herstel · Ontbrekende items · Oorzaken · Per instantie gediagnosticeerd en gepland. Bewust niet-gemonitorde afleveringen tellen niet mee.

### `/` → Overzicht
- **Tabs/filters** (4): 24h · 30d · 7d · problemen
- **Queryparameters** (1): periode
- **Ankers** (1): #todo
- **Knoppen/acties** (4): Bibliotheek vrijgeven · Het opslagoverzicht kon niet worden geladen · Opnieuw proberen · Weer vergrendelen
- **Dialogen/bevestigingen** (3): Bibliotheek vrijgegeven · Bibliotheek vrijgeven · Weer vergrendelen
- **Kaarten/blokken** (25): Alle bestanden ({n}) · Bibliotheek vrijgeven? · Bibliotheek weer vergrendelen? · Er is niets geïmporteerd of omgezet. MediaGuard haalt de geschiedenis elke 15 minuten op. · Geen actieve encodes of downloads · Geen nieuwe afleveringen deze week · Het opslagoverzicht is nog niet beschikbaar · Kalender · Live banen en wachtrij staan in AV1 Studio. · Niets in deze periode · Nieuwe afleveringen deze week · Nu bezig · Overzicht · Per bron · Per soort · Ruimte · Ruimte buiten de bibliotheek · Tijd · Waarom {n} upgrades? · Wat actie nodig heeft · Wat kwam erbij en waar ging de ruimte heen · Wordt elk uur gemeten; de eerste meting volgt vanzelf. · gemeten {when} · bestanden die als hardlink ook in de bibliotheek staan tellen niet dubbel · was {size} · {date} · MediaGuard v{version}

### `/quarantaine` → Ruimte
- **Tabs/filters** (3): held · restorable · restored
- **Queryparameters** (1): item
- **Ankers** (1): #quarantaine-wezen
- **Knoppen/acties** (16): Annuleren · Bibliotheek vrijgeven · Controleer nu · Definitief verwijderen · Geselecteerde verwijderen · Meer acties · Niet meer vastzetten · Nu opruimen · Originelen worden weer bewaard · Quarantaine gebruiken · Sluiten · Terugzetten · Vastgezet · Vastzetten · Vrijgavecontrole ingepland · Weer vergrendelen
- **Dialogen/bevestigingen** (12): Bibliotheek vrijgegeven · Bibliotheek vrijgeven · Definitief verwijderen · Mislukt: {message} · Nu opruimen · Opruimen ingepland · Quarantaine gebruiken? · TERUGZETTEN · Toch verwijderen zonder controle · VERWIJDER · Weer vergrendelen · {count} terugzetten
- **Kaarten/blokken** (15): Bibliotheek vrijgeven? · Bibliotheek weer vergrendelen? · Herstelbare items · Huidige resultaten gaan naar quarantaine; de originelen komen terug op hun plek. · Laatst gecontroleerd {when} · Origineel {name} definitief verwijderen? · Originelen die MediaGuard bewaart na een vervanging. Hier zet je ze terug of ruim je ze op. · Originelen verschijnen hier na een geverifieerde vervanging en blijven herstelbaar tot de bewaartermijn verloopt. · Quarantaine is leeg · Ruimt alleen originelen op waarvan de bewaartermijn verstreken is en de vrijgavecontrole geslaagd is. · Terugzetten · Weesbestanden in quarantaine · {count} item(s) terugzetten naar origineel? · {count} originelen definitief verwijderen? · {count} verlopen originelen opruimen?

### `/release-lab` → Instellingen › Release Lab
- **Queryparameters** (4): episode · instance · movie · title
- **Knoppen/acties** (4): Ander item kiezen · Rangschik met beleid · Releases zoeken · {count} kandidaten
- **Kaarten/blokken** (9): Afgewezen · Kies een titel of aflevering · Kies hierboven een serie-aflevering of film. Daarna zie je welke releases er zijn en waarom MediaGuard voor de ene kiest. · Nog geen releases geladen · Nog geen titel gekozen · Releases voor {title} · Tik op 'Releases zoeken' om te zien welke releases er zijn en waarom MediaGuard voor de ene kiest boven de andere. · Vergelijk de huidige release met alternatieven: harde eisen, taal, bron, 4K, TRaSH-score, herkomst en effect. · Waarom deze release?

### `/seerr` → Instellingen › Seerr

### `/systeem` → Instellingen › Systeem
- **Queryparameters** (1): open
- **Ankers** (10): #back-ups · #offsite · #offsite-email · #offsite-kind · #opslag · #opslag-trend · #quarantaine-wezen · #taken · #upd-toggle · #updates
- **Knoppen/acties** (28): Back-up gemaakt · Back-up nu · Back-ups (core-database) · Bekijk · Bundel gemaakt · Diagnose · Diagnose gestart · Herstel standaard · Herstelcode instellen · Herstellen · Ja, maak een nieuwe code · Lege map weghalen · Map hernoemen naar {name} · Meer acties · Nieuwe herstelcode · Nu bundel maken · Nu controleren · Nu kopiëren en controleren · Nu opruimen · Ongedaan maken · Opruimen gestart · Opslaan · Sluiten · Teruggezet op de oorspronkelijke plek · Terugzetten · Veilige standaard herstellen · Verbergen · Verwerpen
- **Dialogen/bevestigingen** (16): Back-up hersteld · Back-up {name} herstellen? · Bestanden · Herstellen · Ja, direct verwijderen · Klaargezet: stop de app in TrueNAS om terug te zetten · Klaarzetten · Lege map weghalen · Map hernoemen naar '{name}'? · Map hernoemen naar {name} · MediaGuard haalt deze lege hoofdmap uit Sonarr/Radarr en verwijdert de lege map. Er gaat geen media verloren. · Naar · Van · {instance} verplaatst dan zelf {count} series/films naar de nieuwe map en past de paden aan. Er wordt niets verwijderd. Dit kan even duren; je kunt gewoon doorwerken. · {path} is weggehaald · {path} weghalen?
- **Kaarten/blokken** (19): Achtergrondtaken · Componenten · Gaat je NAS kapot, dan heb je de herstelbundel elders nodig. MediaGuard kopieert elke nieuwe bundel automatisch en controleert de kopie. · Herstelbundel ook buiten de NAS · Laatst gecontroleerd {when} · MediaGuard werkt nooit vanzelf bij. Je krijgt een melding als er een nieuwe versie is. · MediaGuard-core · Nog geen back-ups · Opslag · Originelen direct verwijderen? · Systeem & back-ups · UI-dekking van instellingen · Updates · Versies, health, diagnostiek, updates met back-up en een supportbundel zonder geheimen. · Versleutelde herstelbundel · Wat MediaGuard zelf doet, en wanneer het voor het laatst lukte. · Wat is dit? · Weesbestanden in quarantaine · Zodra een nieuw (AV1-)bestand de controle doorstaat, verwijdert MediaGuard het origineel meteen. Je kunt een bestand daarna niet meer terugzetten als je later iets mis ziet. De veilige standaard is: originelen een aantal dagen bewaren.

### `/login` → Inloggen
- **Queryparameters** (1): next
- **Ankers** (4): #main · #password · #twofa-code · #username
- **Knoppen/acties** (2): Andere gebruiker · Inloggen

### `/setup` → Eerste installatie
- **Ankers** (4): #main · #setup-password · #setup-repeat · #setup-username
- **Knoppen/acties** (8): Account aanmaken en doorgaan · Donker · Doorgaan · Licht · Migratiewizard starten · Naar inloggen · Nieuwe installatie: automatisch inrichten · Systeem

## Instellingen (register)

Allemaal op `/instellingen`, in de sectie hieronder; de geavanceerde instellingen per sectie staan achter 'Geavanceerd tonen'.

| Key | Label | Categorie | Nieuwe plek |
|---|---|---|---|
| `ai.action_timeout_seconds` | Time-out AI-actie | AI Adviseur | Instellingen#gereedschap (Gereedschap) |
| `ai.anthropic.base_url` | Claude API-adres | AI Adviseur | Instellingen#gereedschap (Gereedschap) |
| `ai.anthropic.effort` | Claude denkniveau (effort) | AI Adviseur | Instellingen#gereedschap (Gereedschap) |
| `ai.anthropic.model` | Claude-model | AI Adviseur | Instellingen#gereedschap (Gereedschap) |
| `ai.bounded_auto.allowed_actions` | Automatisch toegestane acties | AI Adviseur | Instellingen#gereedschap (Gereedschap) |
| `ai.bounded_auto.max_actions_per_day` | Max AI-acties per dag | AI Adviseur | Instellingen#gereedschap (Gereedschap) |
| `ai.bounded_auto.min_benchmark_accuracy` | Minimale benchmarkscore | AI Adviseur | Instellingen#gereedschap (Gereedschap) |
| `ai.bounded_auto.min_improvement` | Minimale verbetering t.o.v. regels | AI Adviseur | Instellingen#gereedschap (Gereedschap) |
| `ai.bounded_auto.min_shadow_cases` | Minimum schaduwcases vóór automatisch | AI Adviseur | Instellingen#gereedschap (Gereedschap) |
| `ai.budget.currency` | Budgetvaluta | AI Adviseur | Instellingen#gereedschap (Gereedschap) |
| `ai.budget.max_calls_per_day` | Maximaal analyses per dag | AI Adviseur | Instellingen#gereedschap (Gereedschap) |
| `ai.budget.monthly_limit` | Maandbudget | AI Adviseur | Instellingen#gereedschap (Gereedschap) |
| `ai.budget.per_analysis_limit` | Kostenplafond per analyse | AI Adviseur | Instellingen#gereedschap (Gereedschap) |
| `ai.budget.usd_to_eur` | Wisselkoers USD→EUR | AI Adviseur | Instellingen#gereedschap (Gereedschap) |
| `ai.cache_ttl_days` | Cache-bewaartijd identieke cases | AI Adviseur | Instellingen#gereedschap (Gereedschap) |
| `ai.max_input_tokens` | Max invoertokens (schatting) | AI Adviseur | Instellingen#gereedschap (Gereedschap) |
| `ai.max_output_tokens` | Max antwoordtokens | AI Adviseur | Instellingen#gereedschap (Gereedschap) |
| `ai.max_retries` | Herhaalpogingen per analyse | AI Adviseur | Instellingen#gereedschap (Gereedschap) |
| `ai.mode` | AI-beleid | AI Adviseur | Instellingen#gereedschap (Gereedschap) |
| `ai.openai.base_url` | OpenAI API-adres | AI Adviseur | Instellingen#gereedschap (Gereedschap) |
| `ai.openai.model` | OpenAI-model | AI Adviseur | Instellingen#gereedschap (Gereedschap) |
| `ai.price_table` | Prijstabel per model | AI Adviseur | Instellingen#gereedschap (Gereedschap) |
| `ai.provider` | AI-provider | AI Adviseur | Instellingen#gereedschap (Gereedschap) |
| `ai.retention_days` | Bewaartermijn AI-cases | AI Adviseur | Instellingen#gereedschap (Gereedschap) |
| `ai.retry_budget_per_day` | Retrybudget per dag | AI Adviseur | Instellingen#gereedschap (Gereedschap) |
| `ai.task_models` | Model per taaktype | AI Adviseur | Instellingen#gereedschap (Gereedschap) |
| `ai.task_types` | Doelcases voor AI | AI Adviseur | Instellingen#gereedschap (Gereedschap) |
| `ai.timeout_seconds` | Time-out per call | AI Adviseur | Instellingen#gereedschap (Gereedschap) |
| `app.public_url` | Adres voor de app | Koppelingen | Instellingen#koppelingen (Koppelingen) |
| `arr.cleanup_removed_titles` | Van TMDb/TVDB verwijderde titels zonder bestand opruimen | Identiteit | Instellingen#downloads (Downloaden & zoeken) |
| `audio.anime_preference` | Anime-audiovoorkeur | Audio & ondertiteling | Instellingen#taal (Taal & anime) |
| `audio.nl_audio_mode` | Nederlandse audio | Audio & ondertiteling | Instellingen#taal (Taal & anime) |
| `audio.nl_subtitles_wanted` | Nederlandse ondertitels gewenst | Audio & ondertiteling | Instellingen#taal (Taal & anime) |
| `backfill.base_backoff_hours` | Basis-wachttijd na misser | Ontbrekende media | Instellingen#downloads (Downloaden & zoeken) |
| `backfill.batch_size` | Batchgrootte | Ontbrekende media | Instellingen#downloads (Downloaden & zoeken) |
| `backfill.collect_candidates` | Kandidaten en afwijzingen bewaren | Ontbrekende media | Instellingen#downloads (Downloaden & zoeken) |
| `backfill.command_wait_seconds` | Wachttijd op zoekresultaat | Ontbrekende media | Instellingen#downloads (Downloaden & zoeken) |
| `backfill.enabled` | Backfill ingeschakeld | Ontbrekende media | Instellingen#downloads (Downloaden & zoeken) |
| `backfill.excluded` | Uitsluiten van backfill | Ontbrekende media | Instellingen#downloads (Downloaden & zoeken) |
| `backfill.include_cutoff_unmet` | Ook upgrades (onder cutoff) zoeken | Ontbrekende media | Instellingen#downloads (Downloaden & zoeken) |
| `backfill.interval_hours` | Backfill-interval | Ontbrekende media | Instellingen#downloads (Downloaden & zoeken) |
| `backfill.max_backoff_days` | Maximale wachttijd | Ontbrekende media | Instellingen#downloads (Downloaden & zoeken) |
| `backfill.max_searches_per_day` | Max. zoekopdrachten per dag | Ontbrekende media | Instellingen#downloads (Downloaden & zoeken) |
| `backfill.max_searches_per_hour` | Max. zoekopdrachten per uur | Ontbrekende media | Instellingen#downloads (Downloaden & zoeken) |
| `backfill.priority` | Backfill-prioriteit | Ontbrekende media | Instellingen#downloads (Downloaden & zoeken) |
| `backfill.priority_recent_days` | Recente afleveringen voorrang (dagen) | Ontbrekende media | Instellingen#downloads (Downloaden & zoeken) |
| `backfill.season_search_threshold` | Seizoenszoekactie vanaf | Ontbrekende media | Instellingen#downloads (Downloaden & zoeken) |
| `backfill.unavailable_after_attempts` | 'Niet beschikbaar' na pogingen | Ontbrekende media | Instellingen#downloads (Downloaden & zoeken) |
| `backfill.unavailable_retry_days` | Nieuwe poging na 'niet beschikbaar' | Ontbrekende media | Instellingen#downloads (Downloaden & zoeken) |
| `backup.bundle_retention` | Aantal te bewaren herstelbundels | Back-ups | Instellingen#systeem (Systeem) |
| `backup.daily_enabled` | Dagelijkse back-up | Back-ups | Instellingen#systeem (Systeem) |
| `backup.offsite.email_channel` | E-mailkanaal voor de bijlage | Back-ups | Instellingen#systeem (Systeem) |
| `backup.offsite.keep` | Aantal kopieën bewaren | Back-ups | Instellingen#systeem (Systeem) |
| `backup.offsite.kind` | Herstelbundel ook buiten de NAS bewaren | Back-ups | Instellingen#systeem (Systeem) |
| `backup.offsite.path` | Map voor de kopie | Back-ups | Instellingen#systeem (Systeem) |
| `backup.offsite.s3_access_key` | S3-toegangssleutel (access key) | Back-ups | Instellingen#systeem (Systeem) |
| `backup.offsite.s3_bucket` | S3-bucket | Back-ups | Instellingen#systeem (Systeem) |
| `backup.offsite.s3_endpoint` | S3-adres | Back-ups | Instellingen#systeem (Systeem) |
| `backup.offsite.s3_prefix` | S3-map (prefix) | Back-ups | Instellingen#systeem (Systeem) |
| `backup.offsite.s3_region` | S3-regio | Back-ups | Instellingen#systeem (Systeem) |
| `backup.offsite.webdav_url` | WebDAV-adres | Back-ups | Instellingen#systeem (Systeem) |
| `backup.offsite.webdav_user` | WebDAV-gebruiker | Back-ups | Instellingen#systeem (Systeem) |
| `backup.retention_count` | Aantal te bewaren back-ups | Back-ups | Instellingen#systeem (Systeem) |
| `delivery.enabled` | Aanvragen automatisch leveren | Aanvragen | Instellingen#downloads (Downloaden & zoeken) |
| `downloads.cleanup_enabled` | Oude downloads automatisch opruimen | Downloads opruimen | Instellingen#downloads (Downloaden & zoeken) |
| `downloads.cleanup_interval_hours` | Opruimen elke (uur) | Downloads opruimen | Instellingen#downloads (Downloaden & zoeken) |
| `downloads.min_free_percent` | Prullenbak eerder legen onder (% vrij) | Downloads opruimen | Instellingen#downloads (Downloaden & zoeken) |
| `downloads.orphan_days` | Onbekende mappen opruimen na (dagen) | Downloads opruimen | Instellingen#downloads (Downloaden & zoeken) |
| `downloads.public_seed_hours` | Openbare torrents minimaal delen (uur) | Downloads opruimen | Instellingen#downloads (Downloaden & zoeken) |
| `downloads.recovery_enabled` | Niet-geïmporteerde downloads herstellen | Downloads opruimen | Instellingen#downloads (Downloaden & zoeken) |
| `downloads.trash_days` | Prullenbak bewaren (dagen) | Downloads opruimen | Instellingen#downloads (Downloaden & zoeken) |
| `downloads.unimported_days` | Nooit geïmporteerde downloads opruimen na (dagen) | Downloads opruimen | Instellingen#downloads (Downloaden & zoeken) |
| `dv.allow_av1` | Dolby Vision → AV1 toestaan | Dolby Vision & HDR | Instellingen#av1 (AV1 & ruimte besparen) |
| `dv.allowed_profiles` | Vrijgegeven DV-invoerprofielen | Dolby Vision & HDR | Instellingen#av1 (AV1 & ruimte besparen) |
| `dv.dv81_route` | Dolby Vision 8.1 | Dolby Vision & HDR | Instellingen#av1 (AV1 & ruimte besparen) |
| `dv.hdr10plus_mode` | HDR10+-beleid | Dolby Vision & HDR | Instellingen#av1 (AV1 & ruimte besparen) |
| `dv.mode` | Dolby Vision-modus | Dolby Vision & HDR | Instellingen#av1 (AV1 & ruimte besparen) |
| `dv.show_playback_warnings` | Afspeelwaarschuwingen tonen | Dolby Vision & HDR | Instellingen#av1 (AV1 & ruimte besparen) |
| `encoding.auto_commit_after_verify` | Automatisch activeren na verificatie (Full Auto) | AV1 Studio | Instellingen#av1 (AV1 & ruimte besparen) |
| `encoding.autopilot_enabled` | AV1-autopilot (MediaGuard kiest bestanden) | AV1 | Instellingen#av1 (AV1 & ruimte besparen) |
| `encoding.autopilot_interval_minutes` | Interval AV1-autopilot | AV1 | Instellingen#av1 (AV1 & ruimte besparen) |
| `encoding.autopilot_lane_buffer` | Voorraad per baan (CPU en GPU) | AV1 | Instellingen#av1 (AV1 & ruimte besparen) |
| `encoding.autopilot_queue_depth` | Bestanden tegelijk bij de encoder (0 = automatisch) | AV1 | Instellingen#av1 (AV1 & ruimte besparen) |
| `encoding.cache_min_free_gb` | Pauzebodem encodercache (GB vrij) | AV1 Studio | Instellingen#av1 (AV1 & ruimte besparen) |
| `encoding.canary_required` | Voorzichtige start: eerste conversies één voor één | AV1 Studio | Instellingen#av1 (AV1 & ruimte besparen) |
| `encoding.crf` | CRF (0 = automatisch) | AV1 Studio | Instellingen#av1 (AV1 & ruimte besparen) |
| `encoding.encoder` | Encoder | AV1 Studio | Instellingen#av1 (AV1 & ruimte besparen) |
| `encoding.identity_gate` | Identiteitspoort | AV1 Studio | Instellingen#av1 (AV1 & ruimte besparen) |
| `encoding.keep_all_audio` | Alle audiosporen behouden | AV1 Studio | Instellingen#av1 (AV1 & ruimte besparen) |
| `encoding.keep_all_subtitles` | Alle ondertitels behouden | AV1 Studio | Instellingen#av1 (AV1 & ruimte besparen) |
| `encoding.min_saving_percent` | Minimale besparing (%) | AV1 Studio | Instellingen#av1 (AV1 & ruimte besparen) |
| `encoding.poll_interval_seconds` | Statusinterval encoder (s) | AV1 Studio | Instellingen#av1 (AV1 & ruimte besparen) |
| `encoding.preset` | Preset (0 = automatisch) | AV1 Studio | Instellingen#av1 (AV1 & ruimte besparen) |
| `hooks.audio_score_step` | Scorebijstelling per audiostap | Hooks & import | Instellingen#downloads (Downloaden & zoeken) |
| `hooks.post_import_analysis` | Analyse na import (identiteit, audio, AV1) | Hooks & import | Instellingen#downloads (Downloaden & zoeken) |
| `hooks.pregrab_enabled` | Pre-grab-beleid toepassen | Hooks & import | Instellingen#downloads (Downloaden & zoeken) |
| `hooks.pregrab_identity_check` | Afleveringnummer in releasenaam controleren | Hooks & import | Instellingen#downloads (Downloaden & zoeken) |
| `hooks.preimport_cache_minutes` | Herhaalde importvraag onthouden (min) | Hooks & import | Instellingen#downloads (Downloaden & zoeken) |
| `hooks.preimport_defer_on_uncertain` | Import uitstellen bij onzekere identiteit | Hooks & import | Instellingen#downloads (Downloaden & zoeken) |
| `hooks.preimport_enforce_audio` | Verplichte audiotaal afdwingen bij import | Hooks & import | Instellingen#downloads (Downloaden & zoeken) |
| `identity.ai_tiebreak` | AI beslist bij tegenstrijdig bewijs | Identiteit | Instellingen#downloads (Downloaden & zoeken) |
| `identity.auto_replace_on_mismatch` | Automatisch vervangen bij bewezen verkeerde aflevering | Identiteit | Instellingen#downloads (Downloaden & zoeken) |
| `identity.duration_tolerance_percent` | Tolerantie speelduur (%) | Identiteit | Instellingen#downloads (Downloaden & zoeken) |
| `identity.mismatch_min_weight` | Minimale bewijssterkte voor 'verkeerde aflevering' | Identiteit | Instellingen#downloads (Downloaden & zoeken) |
| `identity.remember_verified_hashes` | Hashes van geverifieerde bestanden onthouden | Identiteit | Instellingen#downloads (Downloaden & zoeken) |
| `identity.thexem_enabled` | TheXEM-mapping gebruiken | Identiteit | Instellingen#downloads (Downloaden & zoeken) |
| `identity.uncertain_auto_keep_days` | Zwakke twijfel automatisch afsluiten na (dagen) | Identiteit | Instellingen#downloads (Downloaden & zoeken) |
| `imports.stuck_after_hours` | Wachttijd vastgelopen download | Downloaden | Instellingen#downloads (Downloaden & zoeken) |
| `imports.stuck_auto_fix` | Vastgelopen downloads automatisch oplossen | Downloaden | Instellingen#downloads (Downloaden & zoeken) |
| `instances.health_interval_seconds` | Interval gezondheidscontrole | Instanties | Instellingen#koppelingen (Koppelingen) |
| `integrity.recovery_verify_checksums` | Checksums controleren bij kopiëren en activeren | Veiligheid | Instellingen#originelen (Originelen & herstellen) |
| `integrity.release_check_before_purge` | Vrijgavecontrole vereist vóór automatisch opruimen | Veiligheid | Instellingen#originelen (Originelen & herstellen) |
| `integrity.require_recoverable_copy` | Herstelbare kopie vereist vóór vervanging | Veiligheid | Instellingen#originelen (Originelen & herstellen) |
| `integrity.verify_streams_before_activate` | Streams verifiëren vóór activatie | Veiligheid | Instellingen#originelen (Originelen & herstellen) |
| `jobs.max_concurrency` | Gelijktijdige achtergrondtaken | Wachtrij & workers | Instellingen#systeem (Systeem) |
| `library.sync_interval_minutes` | Synchronisatie-interval bibliotheek | Bibliotheek | Instellingen#systeem (Systeem) |
| `library.write_enabled` | MediaGuard mag bibliotheekbestanden vervangen | Veiligheid | Instellingen#originelen (Originelen & herstellen) |
| `logging.level` | Logniveau | Systeem | Instellingen#algemeen (Algemeen) |
| `maintenance.retention_days_audit` | Auditlog bewaren (dagen) | Onderhoud | Instellingen#systeem (Systeem) |
| `maintenance.retention_days_events` | Gebeurtenissen bewaren (dagen) | Onderhoud | Instellingen#systeem (Systeem) |
| `maintenance.retention_days_history` | Geschiedenis bewaren (dagen) | Onderhoud | Instellingen#systeem (Systeem) |
| `maintenance.retention_days_hooks` | Grab/import-beslissingen bewaren (dagen) | Onderhoud | Instellingen#systeem (Systeem) |
| `maintenance.retention_days_jobs` | Afgeronde taken bewaren (dagen) | Onderhoud | Instellingen#systeem (Systeem) |
| `maintenance.retention_days_notifications` | Gelezen meldingen bewaren (dagen) | Onderhoud | Instellingen#systeem (Systeem) |
| `maintenance.retry_transient_failures` | Tijdelijke fouten automatisch opnieuw proberen | Onderhoud | Instellingen#systeem (Systeem) |
| `mediaserver.kind` | Mediaserver | Mediaserver (Emby) | Instellingen#koppelingen (Koppelingen) |
| `mediaserver.pause_while_watching` | AV1 pauzeren tijdens kijken | Mediaserver (Emby) | Instellingen#koppelingen (Koppelingen) |
| `mediaserver.playback_fail_action` | Als de afspeeltest mislukt | Mediaserver (Emby) | Instellingen#koppelingen (Koppelingen) |
| `mediaserver.playback_test` | Afspeeltest | Mediaserver (Emby) | Instellingen#koppelingen (Koppelingen) |
| `mediaserver.recent_watch_hours` | Recent bekeken = laatste (uur) | Mediaserver (Emby) | Instellingen#koppelingen (Koppelingen) |
| `mediaserver.refresh_after_handoff` | Verversen na overdracht | Mediaserver (Emby) | Instellingen#koppelingen (Koppelingen) |
| `mediaserver.skip_recent_watch` | Recent bekeken niet omzetten | Mediaserver (Emby) | Instellingen#koppelingen (Koppelingen) |
| `mediaserver.url` | Adres van de mediaserver | Mediaserver (Emby) | Instellingen#koppelingen (Koppelingen) |
| `migration.command_wait_seconds` | Wachttijd Sonarr/Radarr-commando's | Migratie | Instellingen#systeem (Systeem) |
| `migration.episode_wait_seconds` | Wachttijd afleveringsmetadata | Migratie | Instellingen#systeem (Systeem) |
| `migration.max_backup_mb` | Maximale back-upgrootte | Migratie | Instellingen#systeem (Systeem) |
| `migration.path_sample_size` | Steekproef padcontrole | Migratie | Instellingen#systeem (Systeem) |
| `migration.restart_wait_seconds` | Wachttijd herstart na restore | Migratie | Instellingen#systeem (Systeem) |
| `monitor.emergency_stop_hours` | Melden als de noodstop lang aanstaat (uur) | Meldingen | Instellingen#meldingen (Meldingen) |
| `monitor.enabled` | Bewaking op de achtergrond | Meldingen | Instellingen#meldingen (Meldingen) |
| `monitor.interval_minutes` | Bewaking: elke zoveel minuten | Meldingen | Instellingen#meldingen (Meldingen) |
| `monitor.lane_idle_minutes` | Melden als een AV1-baan stilstaat (minuten) | Meldingen | Instellingen#meldingen (Meldingen) |
| `monitor.request_stall_hours` | Melden als een aanvraag niet verder komt (uur) | Meldingen | Instellingen#meldingen (Meldingen) |
| `notify.base_url` | Adres van MediaGuard in links | Meldingen | Instellingen#meldingen (Meldingen) |
| `notify.digest.daily` | Dagoverzicht sturen | Meldingen | Instellingen#meldingen (Meldingen) |
| `notify.digest.time` | Tijd van het overzicht | Meldingen | Instellingen#meldingen (Meldingen) |
| `notify.digest.weekday` | Dag van het weekoverzicht | Meldingen | Instellingen#meldingen (Meldingen) |
| `notify.digest.weekly` | Weekoverzicht sturen | Meldingen | Instellingen#meldingen (Meldingen) |
| `notify.pref.av1_done` | AV1 klaar: wanneer melden | Meldingen | Instellingen#meldingen (Meldingen) |
| `notify.pref.downloads` | Downloads: wanneer melden | Meldingen | Instellingen#meldingen (Meldingen) |
| `notify.pref.errors` | Fouten: wanneer melden | Meldingen | Instellingen#meldingen (Meldingen) |
| `notify.pref.security` | Beveiliging: wanneer melden | Meldingen | Instellingen#meldingen (Meldingen) |
| `notify.pref.storage` | Opslag: wanneer melden | Meldingen | Instellingen#meldingen (Meldingen) |
| `notify.pref.waiting` | Wacht op jou: wanneer melden | Meldingen | Instellingen#meldingen (Meldingen) |
| `notify.rate_per_hour` | Maximaal aantal pushberichten per uur | Meldingen | Instellingen#meldingen (Meldingen) |
| `notify.timezone` | Tijdzone | Meldingen | Instellingen#meldingen (Meldingen) |
| `optimization.av1_threshold_1080_kbps` | AV1-drempel 1080p (kbps) | Optimalisatie | Instellingen#av1 (AV1 & ruimte besparen) |
| `optimization.av1_threshold_2160_kbps` | AV1-drempel 2160p (kbps) | Optimalisatie | Instellingen#av1 (AV1 & ruimte besparen) |
| `optimization.av1_threshold_720_kbps` | AV1-drempel 720p (kbps) | Optimalisatie | Instellingen#av1 (AV1 & ruimte besparen) |
| `optimization.batch_size` | Batchgrootte | Optimalisatie | Instellingen#av1 (AV1 & ruimte besparen) |
| `optimization.downloads_need_confirmation` | Betere versies pas na akkoord downloaden | Optimalisatie | Instellingen#av1 (AV1 & ruimte besparen) |
| `optimization.dry_run_valid_hours` | Geldigheid dry-run (uren) | Optimalisatie | Instellingen#av1 (AV1 & ruimte besparen) |
| `optimization.estimated_av1_saving_percent` | Verwachte AV1-besparing H.264 (%) | Optimalisatie | Instellingen#av1 (AV1 & ruimte besparen) |
| `optimization.estimated_av1_saving_percent_hevc` | Verwachte AV1-besparing HEVC (%) | Optimalisatie | Instellingen#av1 (AV1 & ruimte besparen) |
| `optimization.max_active_downloads` | Max. actieve downloads | Optimalisatie | Instellingen#downloads (Downloaden & zoeken) |
| `optimization.max_concurrent_encodes` | Max. gelijktijdige AV1-encodes (CPU/GPU) | Optimalisatie | Instellingen#av1 (AV1 & ruimte besparen) |
| `optimization.max_gb_per_day` | Max. GB per dag | Optimalisatie | Instellingen#downloads (Downloaden & zoeken) |
| `optimization.max_searches_per_hour` | Max. zoekopdrachten per uur (indexerquota) | Optimalisatie | Instellingen#av1 (AV1 & ruimte besparen) |
| `optimization.staging_reserve_gb` | Stagingreserve (GB) | Optimalisatie | Instellingen#av1 (AV1 & ruimte besparen) |
| `optimization.window` | Tijdvenster | Optimalisatie | Instellingen#av1 (AV1 & ruimte besparen) |
| `optimization.window_enabled` | Alleen binnen tijdvenster uitvoeren | Optimalisatie | Instellingen#av1 (AV1 & ruimte besparen) |
| `policy.profile_id` | Policyprofiel | Kwaliteit & TRaSH | Instellingen#downloads (Downloaden & zoeken) |
| `policy.ranking_mode` | Rangschikking | Kwaliteit & TRaSH | Instellingen#downloads (Downloaden & zoeken) |
| `provisioning.auto_repair` | Afwijkingen automatisch herstellen | Inrichten | Instellingen#koppelingen (Koppelingen) |
| `provisioning.drift_check_enabled` | Afwijkingen periodiek controleren | Inrichten | Instellingen#koppelingen (Koppelingen) |
| `provisioning.drift_interval_hours` | Interval afwijkingscontrole | Inrichten | Instellingen#koppelingen (Koppelingen) |
| `provisioning.on_startup` | Essentials bij opstarten inrichten | Inrichten | Instellingen#koppelingen (Koppelingen) |
| `provisioning.startup_delay_seconds` | Wachttijd na opstarten | Inrichten | Instellingen#koppelingen (Koppelingen) |
| `quarantine.auto_purge` | Verlopen originelen automatisch opruimen | Quarantaine & herstel | Instellingen#originelen (Originelen & herstellen) |
| `quarantine.max_size_gb` | Maximale omvang quarantaine (GB) | Quarantaine & herstel | Instellingen#originelen (Originelen & herstellen) |
| `quarantine.min_age_hours` | Minimale bewaartijd bij vol quotum (uur) | Quarantaine & herstel | Instellingen#originelen (Originelen & herstellen) |
| `quarantine.mode` | Originelen na vervanging | Quarantaine & herstel | Instellingen#originelen (Originelen & herstellen) |
| `quarantine.purge_target_percent` | Opruimen tot (% van het maximum) | Quarantaine & herstel | Instellingen#originelen (Originelen & herstellen) |
| `quarantine.release_check_full_decode` | Volledige decodecontrole vóór opruimen | Quarantaine & herstel | Instellingen#originelen (Originelen & herstellen) |
| `quarantine.release_check_timeout_minutes` | Maximale duur decodecontrole (minuten) | Quarantaine & herstel | Instellingen#originelen (Originelen & herstellen) |
| `quarantine.retention_days` | Bewaartermijn originelen (dagen) | Quarantaine & herstel | Instellingen#originelen (Originelen & herstellen) |
| `quarantine.safety_net_gb` | Maximale omvang vangnet (GB) | Quarantaine & herstel | Instellingen#originelen (Originelen & herstellen) |
| `quarantine.safety_net_hours` | Vangnet bij direct verwijderen (uur) | Quarantaine & herstel | Instellingen#originelen (Originelen & herstellen) |
| `recovery.hardlink_policy` | Bestanden met hardlinks (seeding) | Quarantaine & herstel | Instellingen#originelen (Originelen & herstellen) |
| `recovery.min_free_gb` | Minimale vrije ruimte (GB) | Quarantaine & herstel | Instellingen#originelen (Originelen & herstellen) |
| `recovery.quarantine_location` | Locatie quarantaine | Quarantaine & herstel | Instellingen#originelen (Originelen & herstellen) |
| `recovery.refresh_arr_after_commit` | Sonarr/Radarr laten herscannen na vervanging | Quarantaine & herstel | Instellingen#originelen (Originelen & herstellen) |
| `recovery.truenas_url` | TrueNAS-API-URL | Quarantaine & herstel | Instellingen#originelen (Originelen & herstellen) |
| `recovery.truenas_verify_tls` | TLS-certificaat TrueNAS controleren | Quarantaine & herstel | Instellingen#originelen (Originelen & herstellen) |
| `recovery.zfs_dataset` | Dataset voor snapshots | Quarantaine & herstel | Instellingen#originelen (Originelen & herstellen) |
| `recovery.zfs_snapshot_enabled` | ZFS-snapshot vóór vervanging | Quarantaine & herstel | Instellingen#originelen (Originelen & herstellen) |
| `resources.av1_cpu_percent` | Maximaal CPU-gebruik voor AV1 | Systeem & prestaties | Instellingen#systeem (Systeem) |
| `resources.encode_window` | Tijdvenster voor encoderen | Systeem & prestaties | Instellingen#systeem (Systeem) |
| `resources.encode_window_enabled` | Alleen encoderen binnen een tijdvenster | Systeem & prestaties | Instellingen#systeem (Systeem) |
| `resources.encodes_cpu` | Encodes tegelijk op de processor (CPU) | Systeem & prestaties | Instellingen#systeem (Systeem) |
| `resources.encodes_gpu` | Encodes tegelijk op de videokaart (GPU) | Systeem & prestaties | Instellingen#systeem (Systeem) |
| `resources.max_cpu_temp_c` | Pauzeer AV1 boven CPU-temperatuur | Systeem & prestaties | Instellingen#systeem (Systeem) |
| `routing.anime.detect_animation_japanese` | Japanse animatie als anime herkennen | Anime-routering | Instellingen#taal (Taal & anime) |
| `routing.anime.enabled` | Anime automatisch naar anime-instantie | Anime-routering | Instellingen#taal (Taal & anime) |
| `routing.anime.max_age_hours` | Alleen recent toegevoegde titels (uur) | Anime-routering | Instellingen#taal (Taal & anime) |
| `routing.anime.mode` | Werkwijze | Anime-routering | Instellingen#taal (Taal & anime) |
| `routing.anime.sweep_minutes` | Controle-interval (minuten) | Anime-routering | Instellingen#taal (Taal & anime) |
| `routing.anime.target_profile` | Kwaliteitsprofiel op anime-instantie | Anime-routering | Instellingen#taal (Taal & anime) |
| `routing.anime.target_root` | Hoofdmap op anime-instantie | Anime-routering | Instellingen#taal (Taal & anime) |
| `security.login_max_attempts` | Mislukte inlogpogingen vóór wachttijd | Beveiliging | Instellingen#beveiliging (Beveiliging & toegang) |
| `security.login_max_delay_minutes` | Langste wachttijd na mislukte pogingen | Beveiliging | Instellingen#beveiliging (Beveiliging & toegang) |
| `security.notify_failed_logins` | Melden bij veel mislukte inlogpogingen | Beveiliging | Instellingen#beveiliging (Beveiliging & toegang) |
| `security.notify_new_login` | Melden bij inloggen vanaf een nieuw adres | Beveiliging | Instellingen#beveiliging (Beveiliging & toegang) |
| `security.nudge_2fa` | Herinnering om tweestapsverificatie aan te zetten | Beveiliging | Instellingen#beveiliging (Beveiliging & toegang) |
| `security.require_2fa_admins` | Tweestapsverificatie verplicht voor beheerders | Beveiliging | Instellingen#beveiliging (Beveiliging & toegang) |
| `security.trusted_proxies` | Vertrouwde proxy's (bijv. cloudflared) | Beveiliging | Instellingen#beveiliging (Beveiliging & toegang) |
| `seerr.request_sync_interval_minutes` | Interval aanvragen-synchronisatie | Seerr | Instellingen#koppelingen (Koppelingen) |
| `seerr.url` | Seerr-URL | Seerr | Instellingen#koppelingen (Koppelingen) |
| `storage.growth_alert_gb` | Waarschuwen bij snelle groei (GB per 24 uur) | Meldingen | Instellingen#meldingen (Meldingen) |
| `storage.ledger_notify_daily` | Dagelijks opslagoverzicht | Meldingen | Instellingen#meldingen (Meldingen) |
| `storage.ledger_notify_weekly` | Wekelijks opslagoverzicht | Meldingen | Instellingen#meldingen (Meldingen) |
| `subtitles.bazarr_url` | Adres van Bazarr | Ondertitels | Instellingen#taal (Taal & anime) |
| `subtitles.nl_auto` | Nederlandse ondertitels automatisch zoeken | Ondertitels | Instellingen#taal (Taal & anime) |
| `subtitles.opensubtitles_daily` | OpenSubtitles: maximaal per dag | Ondertitels | Instellingen#taal (Taal & anime) |
| `subtitles.opensubtitles_enabled` | OpenSubtitles gebruiken (zonder Bazarr) | Ondertitels | Instellingen#taal (Taal & anime) |
| `system.hub_url` | Hub-adres | Systeem | Instellingen#algemeen (Algemeen) |
| `system.timezone` | Tijdzone | Systeem | Instellingen#algemeen (Algemeen) |
| `ui.theme` | Thema | Systeem | Instellingen#algemeen (Algemeen) |
| `updates.check_enabled` | Melden als er een nieuwe versie is | Onderhoud | Instellingen#systeem (Systeem) |
| `wanted.relaxed_allowed_categories` | Toegestane versoepelingen | Ontbrekende media | Instellingen#downloads (Downloaden & zoeken) |

## Encoder-afstelling

| Knop | Label | Nieuwe plek |
|---|---|---|
| `encoder.max_4k_gpu` | Max. 4K-encodes tegelijk op de GPU | Instellingen#av1 › AV1-engine (`/av1/instellingen`, Uitvoering) |
| `encoder.max_4k_cpu` | Max. 4K-encodes tegelijk op de CPU | Instellingen#av1 › AV1-engine (`/av1/instellingen`, Uitvoering) |
| `encoder.nacht_start` | Nachtvenster: begin (uur) | Instellingen#av1 › AV1-engine (`/av1/instellingen`, Uitvoering) |
| `encoder.nacht_einde` | Nachtvenster: einde (uur) | Instellingen#av1 › AV1-engine (`/av1/instellingen`, Uitvoering) |
| `encoder.lange_jobs_alleen_nacht` | Lange jobs alleen 's nachts | Instellingen#av1 › AV1-engine (`/av1/instellingen`, Uitvoering) |
| `encoder.lange_job_uren` | Grens voor een lange job (uren) | Instellingen#av1 › AV1-engine (`/av1/instellingen`, Uitvoering) |
| `encoder.vastloper_minuten` | Encode stoppen na zoveel minuten zonder voortgang | Instellingen#av1 › AV1-engine (`/av1/instellingen`, Uitvoering) |
| `encoder.vmaf_minimum` | Minimale VMAF-score | Instellingen#av1 › AV1-engine (`/av1/instellingen`, Uitvoering) |
| `encoder.ssim_minimum` | Minimale SSIM-score (terugval) | Instellingen#av1 › AV1-engine (`/av1/instellingen`, Uitvoering) |
| `encoder.bitrate_ondergrens_schaal` | Schaal bitrate-ondergrens | Instellingen#av1 › AV1-engine (`/av1/instellingen`, Uitvoering) |
| `encoder.gpu_hardware_decode` | Bron uitpakken op de GPU | Instellingen#av1 › AV1-engine (`/av1/instellingen`, Uitvoering) |
| `encoder.gpu_lookahead` | Vooruitkijken op de GPU (lookahead) | Instellingen#av1 › AV1-engine (`/av1/instellingen`, Uitvoering) |
| `encoder.gpu_vmaf_sturing` | GPU-bitrate sturen op VMAF | Instellingen#av1 › AV1-engine (`/av1/instellingen`, Uitvoering) |
| `encoder.vmaf_doel` | VMAF-doelwaarde (GPU-sturing) | Instellingen#av1 › AV1-engine (`/av1/instellingen`, Uitvoering) |
| `encoder.gpu_plekken_auto` | GPU-plekken automatisch meten | Instellingen#av1 › AV1-engine (`/av1/instellingen`, Uitvoering) |
| `encoder.cpu_tweede_plek_proef` | Tweede CPU-plek voorzichtig proberen | Instellingen#av1 › AV1-engine (`/av1/instellingen`, Uitvoering) |
| `encoder.geheugen_reserve_gb` | Geheugenreserve voor AV1 (GB) | Instellingen#av1 › AV1-engine (`/av1/instellingen`, Uitvoering) |
| `encoder.anime_route` | Route voor anime | Instellingen#av1 › AV1-engine (`/av1/instellingen`, Uitvoering) |
| `encoder.max_cpu_uren` | Max. duur van één CPU-encode (uren) | Instellingen#av1 › AV1-engine (`/av1/instellingen`, Uitvoering) |
| `encoder.cpu_harde_grens` | Harde CPU-grens voor encodes | Instellingen#av1 › AV1-engine (`/av1/instellingen`, Uitvoering) |

## Overige configuratie

| Wat | Scherm | Nieuwe plek |
|---|---|---|
| Sonarr/Radarr-instanties (URL, API-sleutel, schaduwmodus) | `/instellingen/instanties` | Instellingen#koppelingen |
| Downloadclients, indexers, paden & categorieën, media & kwaliteit, Seerr, hernoemen | `/inrichten` | Instellingen#koppelingen |
| Mediaserver (Emby/Jellyfin/Plex) & ondertitels (Bazarr/OpenSubtitles) | `/inrichten?tab=mediaserver` | Instellingen#koppelingen |
| Kwaliteitsprofielen, bibliotheek- en Sonarr/Radarr-optimalisatie | `/kwaliteit` | Instellingen#downloads |
| Meldingskanalen (ntfy, Pushover, Telegram, Discord, Slack, webhook, e-mail), categorieën, dagoverzicht, opslagmeldingen | `/instellingen/meldingen` | Instellingen#meldingen |
| Tweestapsverificatie, sessies, inloggeschiedenis | `/instellingen/beveiliging` | Instellingen#beveiliging |
| Gebruikers en rollen | `/instellingen/gebruikers` | Instellingen#beveiliging |
| API-sleutels (o.a. Media Source Optimizer) en Android-app koppelen | `/instellingen/api-sleutels` | Instellingen#beveiliging |
| Geheimen (wachtwoorden, sleutels) | `/instellingen/geheimen` | Instellingen#beveiliging |
| Back-ups, herstelbundel, herstelcode, externe kopie | `/systeem` | Instellingen#systeem |
| Opslag, tweelingmappen, weesbestanden, opruimer downloads | `/systeem#opslag` | Instellingen#systeem |
| Systeem & prestaties (CPU, GPU, vensters) | `/systeem` | Instellingen#systeem |
| AV1-engine: profielen, banen, afstelling (uitvoering), GPU-plekken meten | `/av1/instellingen` | Instellingen#av1 |
| Dolby Vision-beleid en uitzonderingen per titel | `/dolby-vision` | Instellingen#av1 |
| Quarantaine-/vangnetmodus, 'Quarantaine gebruiken' | `/quarantaine` | Ruimte › Terugzetten (+ Instellingen#originelen) |
| AI-adviseur: provider, sleutel, toestemming, budget, cases | `/ai` | Instellingen#gereedschap |
| Anime-routering en keuzes | `/anime` | Instellingen#gereedschap |
| Release Lab (waarom deze release?) | `/release-lab` | Instellingen#gereedschap |
| Migratie-wizard, scan, overschakelen, terugdraaien | `/migratie` | Instellingen#gereedschap |
| Noodstop | `profielmenu` | profielmenu + Instellingen#systeem |
| Thema, technische details tonen | `profielmenu` | profielmenu + Instellingen#algemeen |
