# ADR-0025 — Poolstatus bij het opruimen in platformmodus

S-P3-690 · N5 · 2026-11-27 · Contract 1.4, §4.4 en §6.2 · C1.4-MGU-01

## Keuze van de poolbron

Een beheerder kent in de Hub bij *Instellingen → Diensten → Bestaande apps →
MediaGuard* het poolleesrecht toe aan de bevestigde MediaGuard-koppeling. In
MediaGuard stelt de beheerder bij *Instellingen* het **Hub-adres voor poolstatus**
in en slaat het eenmalig ontvangen token bij *Geheimen* op als `hub:poolstatus`.
De eerste geauthenticeerde lezing bevestigt het recht. Het aparte adres voorkomt
dat een bestaande teruglink (`system.hub_url`) stilzwijgend een poolbron wordt.

Vanaf het instellen van dit adres gelden de platformregels, ook tijdens de
eerste bevestiging, bij een herstart en na intrekking van het token. De beheerder
ontkoppelt de bron door het pooladres te wissen. Een ander pooladres vereist
een nieuw ingevoerd token. MediaGuard bewaart het token uitsluitend in de
beheerde, versleutelde geheimenopslag.

## Voorrang en geldigheid

De eigen TrueNAS-lezing heeft voorrang. Een leesfout, een verouderde meting of
een gemeten zieke pool bij die bron blijft het opruimen blokkeren. Zonder
directe bron leest MediaGuard op de gekozen HTTPS-Hub uitsluitend
`GET /api/v1/inventory/pools` met het afzonderlijke recht `poolstatus.lezen`.
Het token gaat alleen naar dat adres. Omleidingen worden geweigerd.

Ontbreekt het token of weigert de Hub toegang (401 of 403), dan is de Hub-status
*onbekend*. De eigen TrueNAS-lezing blijft beschikbaar zodra zij is ingesteld.
Bij een losse installatie zonder poolbron blijven de bestaande opruimregels gelden.

## Wanneer mag opruimen doorgaan?

De Hub moet één volledige, niet-lege meting teruggeven met voor iedere pool
`status == "ONLINE"` en `gezond == true`. De UTC-brontijd `observed_at` mag
hoogstens 900 seconden oud zijn en moet op of vóór het huidige tijdstip liggen.
Ook moet het huidige tijdstip vóór `geldig_tot` liggen. De opruimers controleren
beide grenzen opnieuw bij gebruik. Herhaald ophalen verlengt geen van beide.

Bij een onbekende, verlopen of ongezonde meting pauzeren de download- en
quarantaineopruimer met een melding. Bestanden en voortgang blijven beschikbaar.
Een verse gezonde meting heft alleen de poolpauze op. De overige controles van de
opruimers blijven gelden. De integrale proef voor EX11 valt onder S-P6-20.
