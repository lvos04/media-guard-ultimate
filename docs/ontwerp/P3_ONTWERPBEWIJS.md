# P3 · Ontwerpreviews van de MediaGuard-schermen

| Stap | Onafhankelijke beoordeling en beeldbewijs |
| --- | --- |
| S-P3-600 · Rechten van een API-sleutel | De review beschrijft de bediening per rol, zichtbare focus en geforceerde kleuren. Het beeld komt uit de definitieve focusproef van de stap. [Review, §13](PEIL_REVIEW.md) · [Dialoog op 1024 pixels](../screenshots/peil/g20/s-p3-600-geforceerde-kleuren.png) |
| S-P3-625 · Herstelweergave | De beeldbeoordeling toetst de uitleg bij een nieuwere database en de twee herstelmogelijkheden. [Review](R-ONTWERP-S-P3-625-640.md) · [Herstelweergave](../screenshots/peil/g20/s-p3-625-herstelweergave.png) |
| S-P3-640 · Downloadopruimer | De waarschuwing over een mogelijk onvolledige map staat bij de actie om haar terug te zetten. [Review](R-ONTWERP-S-P3-625-640.md) · [Onderbroken verwijdering](../screenshots/peil/g20/s-p3-640-opruimer.png) |
| S-P3-676 · Originelen in quarantaine | De vroegere voorvertoning vroeg om bevestiging bij nul verwijderbare bestanden. De afzonderlijke beoordeling van de gewijzigde dialoog staat open. [Voorvertoning vóór het herstel](../screenshots/peil/g20/s-p3-676-voor.png) |
| S-P3-680 · Web op tablet | De stapreview toetste toetsenbordbediening en bronuitval. De metingen en bestandsnamen staan in §14. [Review en meetmatrix](PEIL_REVIEW.md) · [Overzicht op 768 pixels](../screenshots/peil/s-p3-680/overzicht-768-dark.png) |
| S-P3-685 · Android | Onderstaande beoordeling vergelijkt vier referentiebeelden van de bestaande implementatie. [Ontwerp en bouwproeven, §15](PEIL_REVIEW.md) |

## S-P3-685 · Onafhankelijke beeldbeoordeling van 27 november 2026

Op het lichte Overzicht zijn de bron en de meettijd leesbaar boven de afzonderlijke standen van MediaGuard, de AV1-engine en de downloadclients. Het donkere uitvalbeeld benoemt zowel de veroudering als de laatst bekende noodstop en storing. Een klokpictogram, arcering en de tekst *Verouderd* maken het onderscheid ook zonder de statuskleur herkenbaar. *Opnieuw proberen* staat binnen dezelfde kaart. [Verse meting, licht](../../apps/android/app/src/test/snapshots/images/nl.mediaguard.app.shots_Light360ScreenShots_overzichtRustig.png) · [Verouderde meting, donker](../../apps/android/app/src/test/snapshots/images/nl.mediaguard.app.shots_Dark360ScreenShots_overzichtVerouderd.png)

Op Ruimte heeft elke balk een eigen label, teken en bedrag. Het donkere beeld vervangt het ontbrekende restbedrag door *Onbekend* en licht de meetfrequentie toe. In het lichte beeld is de kopwaarde +19 GB het saldo van +60 GB erbij en 41 GB besparing. De apart getoonde +3 GB onverklaarde ruimte telt volgens de bronberekening niet mee in die kopwaarde. De kop *netto* benoemt die afbakening niet. [Opslagkaart, licht](../../apps/android/app/src/test/snapshots/images/nl.mediaguard.app.shots_Light360ScreenShots_ruimteOpslagkaart.png) · [Opslagkaart, donker](../../apps/android/app/src/test/snapshots/images/nl.mediaguard.app.shots_Dark360ScreenShots_ruimteOpslagkaart.png)

Dit oordeel betreft de 360-dp-referentiebeelden. De bediening op een emulator en de voorleesvolgorde op een toestel blijven open. Voor de nieuwe adresinstructie bij S-P3-675 en de bestaande formulierhulp bij S-P3-690 is in deze index evenmin een afzonderlijke beeldbeoordeling vastgelegd.
