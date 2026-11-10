# Wachttoestanden ("Wacht op jou")

Alles waarbij MediaGuard op een mens wacht of door iets geblokkeerd is. Bron van de waarheid:
`apps/core/mediaguard/attention.py` (`GET /api/v1/attention`). De lijst verschijnt op
**Overzicht → Nog te doen** en **Activiteit → Problemen → Wacht op jou**, en telt mee in de
zijbalkregel "N dingen vragen aandacht" (`/status.attention`).

Uitgangspunt (set-and-forget): wat veilig en omkeerbaar is lost MediaGuard zelf op; de rest
staat met een uitleg in gewoon Nederlands en één knop op één plek. Status 0.2.10.

| Toestand | Was zichtbaar? | Nu getoond | Actie | Automatisch | Opgelost in 0.2.10 |
|---|---|---|---|---|---|
| AV1-encode wacht op activatie (`encoding_jobs.awaiting_activation`) | alleen in 0.2.9 op AV1 Studio | Nog te doen + Problemen + AV1 Studio | Activeren / Origineel houden (per bestand) | ja, Full Auto activeert zodra herstel gereed is en de bibliotheek vrij is (elke 10 min opnieuw) | ook in Overzicht/Problemen; besparing die wacht staat in de KPI's (0.2.9) |
| Vervanging met conflict (`encoding_jobs.conflict_review`, `replacement_transactions.conflict`) | **nee** | Problemen (fout) | Origineel houden (met bevestiging) | nee — onzeker welk bestand actief is | nieuw zichtbaar |
| Herstel/vervangen staat stil (recovery-heartbeat weg, quarantaine niet schrijfbaar) | alleen in Systeem | Problemen (fout) | link naar Systeem | herstart door supervisor bij hangen | nieuw zichtbaar |
| Bibliotheek vergrendeld terwijl er geen migratie meer loopt | balk op Overzicht | Problemen + balk | Bibliotheek vrijgeven (beheerder, bevestiging) | nee — eerste vrijgave is een bewuste keuze | nieuw in Problemen |
| Quarantaine vol → AV1 gepauzeerd | alleen op Quarantaine | Problemen (fout) | Nu opruimen (bevestiging) | ja, opruimen na geslaagde controle (elk uur) | nieuw zichtbaar |
| Origineel blijft staan: vrijgavecontrole faalt ≥ 5× | **nee** (stopte stil na 5 pogingen) | Problemen | Opnieuw controleren / Origineel terugzetten | ja, na 5 dagelijkse pogingen nu **wekelijks** opnieuw | nieuw zichtbaar + wekelijkse herkansing |
| Instantie op pauze (`instances.suspended`) | alleen op Instanties | Nog te doen + Problemen | Hervatten | nee — bewuste pauze | nieuw zichtbaar |
| Instantie nog in proefmodus zonder lopende migratie (`shadow_mode`) | **nee** (downloadt niets) | Nog te doen + Problemen | link naar Instanties | nee — hoort bij de overstap | nieuw zichtbaar |
| Anime-routering twijfelt (`anime_routing_actions` needs_review/suggested) | alleen op Anime (Gevorderd) | Problemen (info) | Verplaatsen / Hier laten | duidelijke gevallen verplaatst MediaGuard zelf | nieuw zichtbaar |
| AI-voorstel wacht op akkoord (`ai_cases.awaiting_approval`) | alleen op AI-pagina | Problemen (info) | Akkoord / Afwijzen | alleen binnen de ingestelde AI-bevoegdheden | nieuw zichtbaar |
| Twijfel over juiste aflevering (laatste `identity_results` = mismatch/uncertain, 30 dagen) | alleen per aflevering | Problemen | link | bewezen verkeerd → automatisch vervangen (0.2.3); twijfel blijft mens | nieuw zichtbaar (telling) |
| Verkeerd bestand kon niet worden vervangen (3× mislukt, `system_state.replace_failures`) | **nee** (elk half uur een nieuwe kopie) | Problemen | Opnieuw proberen | ja, na 1 uur en na 6 uur opnieuw; daarna wacht het op jou. Het bestand blijft staan | nieuw zichtbaar |
| Dolby Vision-proef geverifieerd (`dv_tests.verified`) | alleen op Dolby Vision | Problemen (info) | link | nee — TV-test door mens | nieuw zichtbaar |
| Verbeterplan: items 'review_needed' | alleen in het plan | Problemen (info) | link naar het plan | nee | nieuw zichtbaar |
| AV1-baan gepauzeerd (opslag, temperatuur, stille uren) | alleen op AV1 Studio | Problemen (waarschuwing) | link | ja, hervat zodra de oorzaak weg is | nieuw zichtbaar |
| AV1-engine offline | waarschuwing op AV1 Studio | Problemen (fout) | link | supervisor herstart bij hangen | nieuw zichtbaar |
| Taken mislukt (recent, onopgelost) | Problemen (0.2.8) | Problemen | Opnieuw / Negeren | tijdelijke fouten 3× automatisch opnieuw (0.2.8) | — |
| Vastgelopen downloads (importpending/blocked) | Problemen (0.2.8) | Problemen | via Sonarr/Radarr | ja, na 6 uur verwijderen + blokkeren + opnieuw zoeken (0.2.8) | — |
| Seizoenspakket als 'verkeerde aflevering' geweigerd | **nee** | — | — | opgelost (0.2.6) | — |
| Migratiestap wacht op beslissing | Nog te doen (migratie) | Nog te doen + wizard ("wat nu") | één knop in de wizard | stappen zonder beslissing lopen zelf door (0.2.9) | — |
| Inrichting/koppelingen onvolledig, Seerr niet gekoppeld, GPU ontbreekt | Nog te doen (0.2.8) | Nog te doen | Regelen / Koppelen / Uitleg | — | — |
| Instantie offline/verminderd | Problemen (0.2.8) | Problemen | link | melding pas na 10 min, herstel automatisch gemeld | — |
| Twijfel met alleen zwakke aanwijzingen (bestandsnaam, speelduur) | Problemen | — | — | geen taak meer: na 7 dagen 'waarschijnlijk', bestand blijft (0.2.11) | verborgen |
| Tegenstrijdig sterk bewijs over de aflevering | Problemen | Problemen, rij linkt naar de afleveringspagina | Klopt, bestand houden | AI beslist zelf als sleutel + toestemming er zijn; ontbreekt toestemming → één knop (0.2.11) | links + AI |
| Oude twijfel/afwijking van een eerdere regelversie | Problemen | — | — | opnieuw beoordeeld met de huidige regels (0.2.11) | — |
| Radarr/Sonarr: titel verwijderd bij TMDb/TVDB | Instantie verminderd | — | — | zonder bestand automatisch uit de instantie (nooit bestanden); met bestand alleen info (0.2.11) | — |
| AV1-encode mislukt door onderbreking (update, herstart, engine weg, time-out) | **nee** (bleef liggen) | — | — | opnieuw na 15/30/60 min, max 3× (0.2.11) | — |
| AV1-encode inhoudelijk mislukt of pogingen op | **nee** | Problemen (waarschuwing) | link | één keer opnieuw na 24 uur, daarna zichtbaar met reden (0.2.11) | nieuw zichtbaar |
| AV1: CPU-baan vol, bestand moet via de CPU (`LANE_FULL`) | **nee** (vulde de hele wachtrij, GPU kreeg niets) | AV1 → overgeslagen (½ uur) | — | per baan begrensd (plekken + voorraad); later vanzelf opnieuw, de GPU krijgt intussen eigen werk | nieuw |
| AV1: GPU > 30 min leeg terwijl er werk is | **nee** | Nog te doen + pushmelding (één per stilstand); alleen info als al het werk CPU-werk is | Nu proberen | autopilot zoekt elke ronde werk voor de GPU | nieuw zichtbaar |
| AV1-encode 'te traag voor de CPU' | **nee** (nooit meer geprobeerd, ook na een noodstop) | — | — | na een dag opnieuw, hooguit 2×; telt niet als fout; noodstoptijd telt niet mee in de meting | — |
| Kwaliteitsmeting (VMAF én SSIM) lukte op geen enkele steekproef | **nee** (ging zonder waarschuwing door) | Problemen (via mislukte job) | — | resultaat niet geplaatst, origineel blijft; opnieuw volgens de gewone regels | nieuw zichtbaar |
| Encode afgebroken door een geplande stop/update | telde als herstartlus (na 3× 'mislukt') | — | — | terug in de wachtrij, telt niet mee; de stop krijgt 55 s (`MG_STOP_TIMEOUT`) | — |
| Activatie geweigerd door het bestandssysteem (EPERM/ruimte) | encode weggegooid | AV1 Studio (wacht op activatie) | — | encode bewaard, opnieuw na 6 uur; eerder afgewezen 2× na 24 uur (0.2.11) | — |

**KPI's:** de besparing in AV1 Studio en Overzicht noemt apart wat nog op activatie wacht
(0.2.9); "N dingen vragen aandacht" telt mislukte taken én alle punten hierboven.
| Schrijftest van een bibliotheekmap faalt (`storage.write_probe`, bij start + elk uur) | **nee** (alleen 'Niet geactiveerd' per job) | Nog te doen + Problemen (fout) + Systeem → Opslag | Opnieuw testen (`POST /storage/probe`) | autopilot slaat die map over; slaagt de test weer, dan gaan uitgestelde activaties meteen verder (0.2.14) | nieuw zichtbaar, met de exacte stap (aanmaken/schrijven/vervangen/…) |
| Instelling wijkt af van de veilige standaard (`settings.unsafe`: direct verwijderen, ondertitels/audio niet bewaren, DV omzetten) | **nee** | Nog te doen + Problemen (waarschuwing) | Veilige standaard herstellen (alles of per regel) | nee — bewuste keuze mag blijven | nieuw (0.2.14) |
| Versleutelde herstelbundel niet ingesteld (`backup.bundle_missing`) | alleen op Systeem | Nog te doen (info) | link naar Systeem | nee — herstelcode moet de gebruiker bewaren | nieuw (0.2.14) |
| Ontbrekende items 'nog niet onderzocht' | Actie nodig (doodlopende link) | — | — | achtergronddiagnose per kwartier, geen gebruikersactie (0.2.14) | `needs_user=false` in `/wanted/reasons` |
| Nog niet uitgebrachte films / TBA-afleveringen | telden als ontbrekend | `upcoming` in `/wanted/kpis` | — | worden pas gezocht na release (0.2.14) | — |
| Nacontrole 24 uur na de overstap + verdwenen Prowlarr-indexers | handmatig (knop) | melding | — | automatisch (nazorg na overstap, 0.2.14) | — |

## Bewaking en bewuste keuzes (verbeterlijst 1, 8, 9, 37)

| Toestand | Waar zichtbaar | Actie | Push |
|---|---|---|---|
| Aanvraag zonder voortgang (`delivery.stalled`) | Nog te doen + Problemen | Nu opnieuw proberen | ja |
| Noodstop staat lang aan (`system.emergency_stop`) | Nog te doen + Problemen | Noodstop opheffen / Bewust zo gekozen | ja |
| Seerr-sync mislukt (`seerr.sync_failed`) | Nog te doen + Problemen | link naar Seerr | ja |
| Achtergrondtaak doet niets (`tasks.stale`) | Problemen + Systeem → Achtergrondtaken | — | ja |
| TrueNAS-pool niet gezond (`storage.pool_degraded`, fout) | Problemen + Systeem → Opslag | — (opruimen van originelen pauzeert) | ja |
| AV1-baan stil met wachtend werk (`encoder.idle.*`) | Problemen | — | ja, één keer per stilstand |
| AV1 wacht omdat iemand kijkt, stille uren, noodstop | AV1-pagina | — | nee: geen fout, Overzicht blijft groen |
| AV1-baan zelf gepauzeerd (`encoder.paused.*`, info) | Problemen | Bewust zo gekozen | nee |

**Bewust zo gekozen.** Punten die een bewuste keuze kunnen zijn (afwijkende veiligheidsinstelling,
instantie op pauze of in proefmodus, eigen AV1-pauze, mapnaam met typfout, geen herstelcode, geen
AI-toestemming, een noodstop die bewust aanstaat) hebben deze knop. Het punt verdwijnt tot de
bijbehorende instelling verandert; daarna komt het vanzelf terug. Onder Activiteit → Problemen staat
hoeveel punten verborgen zijn, met *Weer tonen*. Echte fouten en de bibliotheekvergrendeling kun je
niet verbergen.
