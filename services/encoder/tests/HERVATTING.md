# Behoud van encodervoortgang

| Toestand bij onderbreking | Gedrag na herstart | Regressieproef |
| --- | --- | --- |
| De vorige encoder schrijft nog bij de upgrade | Na het stopverzoek controleert de planner of de vorige encoder nog schrijft. Zolang dat zo is, houdt de job zijn PID en percentage en wacht de volgende worker. | `test_planner.py` · `test_herstel_wacht_op_de_nog_schrijvende_vorige_encoder` |
| Upgrade tijdens een actieve encode uit 1.2.2, geplande stop op 42% | De worker neemt de eerste 60 van 144 beelden uit het oude deelbestand over. Alleen de resterende 84 beelden gaan door de encoder. | `grenzen_1_2_2.py` · `test_oud_beeldwerk_wordt_na_de_claim_hergebruikt[gepland]` |
| Crash van 1.2.2 op dezelfde positie | Dezelfde job hervat met de opgeslagen bron en encoderopdracht. De eindcontrole vergelijkt alle 144 beeldtijden. | `grenzen_1_2_2.py` · `test_oud_beeldwerk_wordt_na_de_claim_hergebruikt[onverwacht]` |
| Geplande stop vóór het eerste beeld | De job met nul opgeslagen beelden kan na herstart volledig worden uitgevoerd. | `test_geplande_stop_voordat_het_eerste_fragment_bestaat` |
| Geplande stop binnen het eerste fragment van zestig seconden | Alle aaneengesloten, decodeerbare beelden komen in het hervatregister. De volgende poging sluit hierop aan. | `test_geplande_stop_voor_de_eerste_normale_fragmentgrens` |
| Geplande stop na de eerste fragmentgrens | De worker behoudt de gesloten fragmenten en controleert de staart tegen de bron. | `test_hervat_echte_encode_na_processtop[gepland]` |
| Harde processtop na de eerste fragmentgrens | De worker controleert het register en de nog aanwezige staart voordat de resterende beelden naar de encoder gaan. | `test_hervat_echte_encode_na_processtop[onverwacht]` |
| Een beschadigd deelbestand of onzeker bronbewijs | De job pauzeert met een reden. De bron en het deelbestand blijven ook na wachtrijherstel en periodiek onderhoud beschikbaar. | `test_beschadigd_fragment_blokkeert_hervatting_en_vervanging`, `test_onleesbaar_oud_deel_blijft_bewaard_tot_na_onderhoud` |
| De CPU-job wijkt tot de nacht voor ander werk | De job wacht op het nachtvenster met het geregistreerde fragment en zijn percentage. | `test_uitstel_tot_de_nacht_bewaart_het_bevestigde_fragment` |
| Het encoderproces faalt of bereikt een snelheids- of tijdgrens | Reeds bevestigd beeldwerk blijft bij de gepauzeerde job. De melding vermeldt de procesfout. | `test_procesfout_direct_na_het_eerste_bevestigde_fragment` |

De proeven vanaf de geplande stop vóór het eerste beeld staan in `test_encodehervatting.py`. De CI-job `encoder-unit` haalt voor beide 42%-proeven de oorspronkelijke encodercode uit het op digest vastgezette release-image. De proeven gebruiken synthetische media.
