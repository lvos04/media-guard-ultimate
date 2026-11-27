# R-ONTWERP · Herstelweergave en gedeeltelijk bewaarde downloads

Op 27 november 2026 zijn de twee bestaande schermen onafhankelijk van hun bouw beoordeeld. De schermafdrukken zijn overgenomen uit de P3-inventaris. De onderdelen zijn vergeleken met de bron van MediaGuard op `9976d06`.

## S-P3-625 · Nieuwere database

De kop benoemt de herstelstand. Direct daaronder staat waarom aanmelden tijdelijk wegvalt. Twee afzonderlijke lijstregels sturen de beheerder naar de passende image of naar de snapshot van vóór de update. De laatste zin vertelt wanneer de app weer kan worden geopend. Er staat geen knop op het scherm die de gebruiker onbedoeld een herstelactie laat starten. [Herstelweergave, uitsnede van 358 × 446 pixels](../screenshots/peil/g20/s-p3-625-herstelweergave.png)

De regels blijven in de getoonde uitsnede leesbaar tot en met de laatste instructie. In de component heeft de melding een hoofdkop, een benoemde sectie en een lijst. De gebruiker kan de volgorde ook zonder waarschuwingspictogram volgen. Het beeld ondersteunt het ontwerp voor deze toestand. De bestaande browserproef controleert daarnaast de vier ingangen op een scherm van 320 pixels. [Schermbron](../../apps/web/components/shell/schema-recovery.tsx) · [Browserproef](../../apps/web/e2e/schema-recovery.spec.ts)

## S-P3-640 · Onderbroken verwijdering

De bovenste knop start een opruimronde. Onder de scheidslijn staat bij de betreffende map dat deze onvolledig kan zijn en dat automatisch verwijderen is gestopt. De knop **Terugzetten** hoort zichtbaar bij die map. De tekst noemt de onzekerheid vóór de actie en belooft geen volledig herstel. Alle regels en de knop zijn zichtbaar op het getoonde beeld. [Gedeeltelijk bewaarde map, uitsnede van 390 × 421 pixels](../screenshots/peil/g20/s-p3-640-opruimer.png)

De component toont de herstelknop alleen aan een beheerder. Bij een gedeeltelijk herstel meldt de app dat alleen de resterende inhoud is teruggezet. De componentproef controleert zowel de waarschuwing als die terugkoppeling na het klikken. [Schermbron](../../apps/web/components/mg/download-cleanup.tsx) · [Componentproef](../../apps/web/tests/unit/download-cleanup.test.ts)

Beide beoordelingen gelden voor de afgebeelde Nederlandstalige, lichte toestand. Toetsenbordfocus, schermlezeruitspraak en donkere modus zijn in deze beeldbeoordeling niet gemeten.
