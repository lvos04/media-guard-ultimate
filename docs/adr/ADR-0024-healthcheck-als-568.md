# ADR-0024 — AIO-healthcheck onder de app-gebruiker

**Besluit voor S-P3-650** · 2026-11-19

Docker start de healthcheck als een eigen proces. Ook bij een gewone rootstart verlaagt
de healthcheck zelf zijn rechten vóór hij een statusbestand opent of een verzoek doet.
Hij leegt de capability-bounding-set, verwijdert aanvullende groepen, wisselt naar
`PUID:PGID` (standaard `568:568`) en zet `no_new_privs`. Bij een vaste app-gebruiker zet
hij `no_new_privs` en controleert hij de actieve identiteit en capabilities. Een
mislukte of onvolledige wissel maakt de healthcheck rood. Het korte rootvenster vóór
de wissel blijft een restrisico.

Een gezonde controle vereist een bewakerstatus met `ok` die jonger is dan 90 seconden
en HTTP 200 op de interne kernroute `/readyz`. De controlevlag `--controle` toont de
procesrechten direct na de wissel. De rooktest controleert deze uitvoer afzonderlijk
voor de standaardgebruiker van het image en voor een Docker-exec als `568:568`.

## Besluit over webisolatie en PID 1

Besluit van de gebruiker op 2026-11-19, optie A. De aparte web-UID blijft behouden
(isolatie van de private appdata). De rest van de procesboom wisselt eerst naar 568,
geen proces als 0 na de wissel, geen extra rechten. De aparte web-UID bij een rootstart
is een uitzondering op de uniforme UID in het platformcontract (§2.2, “Rooktest”).
De contracteigenaar moet die uitzondering in het contract vastleggen.

De AIO-bewaker vervangt `tini` als PID 1. Na de eigenaarschapsstap leegt hij zijn
bounding set, wisselt naar de app-gebruiker en zet `no_new_privs`. De app-processen
erven de bestaande aanvullende groepen van de doorgegeven render- en videonodes.
Bij een vaste app-gebruiker behoudt de container zijn ingestelde aanvullende groepen.
De bewaker verwerkt `SIGTERM` en `SIGINT`, stopt de diensten in hun bestaande
afhankelijkheidsvolgorde en ruimt verweesde kinderen op zonder de exitcodes van
beheerde diensten kwijt te raken.

Bij een rootstart maakt de bewaker één webbewaker onder de aparte web-UID aan.
Die geeft zijn rechten af vóór het uitvoeren van zijn programma en wacht dan tot
de app-bewaker web mag starten. Een anoniem lokaal socketpaar draagt alleen opdrachten
voor de vaste webdienst over. De webbewaker start web opnieuw op verzoek en kan
signalen sturen naar de eigen webprocesgroep. Hij krijgt geen aanvullende groepen,
capabilities of toegang tot de private appdata. Bij een vaste app-gebruiker draait
web zoals voorheen onder die gebruiker. De rooktest meet bij beide startwijzen PID 1
en zijn nakomelingen en controleert de healthcheck apart.

De bewaker bewaart de korte openbare kernstatus en het healthcheckstatusbestand in
`/run/mediaguard`. De app-gebruiker beheert die map en de aparte web-UID heeft
leesrechten.
