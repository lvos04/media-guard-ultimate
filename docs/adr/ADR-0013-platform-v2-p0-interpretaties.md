# ADR-0013 — Platform-v2 (fase P0): hoe MediaGuard 1.2.2 de contracten 1.1 invult

Status: **voorgesteld** (fase P0, alleen documentatie) · Datum: 2026-11-09 · Eerdere besluiten ADR-001 … ADR-012:
`docs/DECISIONS.md`. Bewijs: `docs/platform-v2/INVENTARIS.md`, `docs/platform-v2/DELTA_MATRIX.md`.

## Context

`PLATFORM_CONTRACTEN.md` (contracts 1.1) is geschreven voor **nieuwe** diensten (PostgreSQL, één `/appdata`,
enrollment, connectorcontract). MediaGuard is een **bestaande** app (release 1.2.2) waarvoor het B04-plan zegt:
"alleen hiaten bouwen; bestaande integriteit, rollback en monitoring nooit dubbel bouwen of uitschakelen". Op een
aantal punten zijn contract en bestaande, geteste keuzes niet gelijk, of zegt het contract niets. Per punt is de
**veiligste** interpretatie gekozen: niets uitschakelen wat bestanden of de database beschermt, niets openzetten
wat nu dicht is, en nieuwe koppelingen alleen-lezen beginnen.

## Besluiten

1. **Opslag en database blijven zoals in 1.2.2.** SQLite-WAL (ADR-003) en drie mounts `/config` (= appdata),
   `/work` (staging/quarantaine) en `/data` (media + downloads, één dataset voor hardlinks). Geen migratie naar
   PostgreSQL of naar één `/appdata`: dat is geen hiaat maar een herbouw met risico voor de bibliotheek.
2. **Installatie = de bestaande alles-in-één Custom App** (`ghcr.io/lvos04/mediaguard-core:<versie>-aio`, ADR-012).
   De YAML-/compose-route blijft bestaan voor **ontwikkeling, CI en de integratiesuites** (de stacktest gebruikt
   `deploy/truenas/compose.yaml`), maar wordt in fase P3 in README/INSTALL gemarkeerd als *niet de
   platform-installatiemethode*. Niet verwijderen zolang tests en bestaande gebruikers ervan afhangen.
3. **Poorten:** MediaGuard staat niet in de poorttabel van contract §2. De bestaande poorten blijven: 30080 (UI/API)
   en 38989/38990/37878/37879 (forks, nodig voor Seerr). Voorkeur in het §9-manifest: `host_network: false` met
   deze vijf poorten en `MG_HOST` = NAS-adres uit de onboarding-wizard (D03); *Host Network* alleen met een
   `privilege_reason`. Geen IP of poolnaam als voorbeeldwaarde invullen.
4. **Koppelen met de Hub via de bestaande `X-Api-Key`** (contract §3, laatste punt): sleutel `hub` met rol
   **viewer** voor het read-model; een operator-sleutel alleen voor *noodstop aan* en *één herzoek*, en pas nadat
   API-sleutels scopes hebben (P3-02). Geen enrollment-client in v1: de Hub **pollt** (contract §7 staat dat toe
   voor bronnen zonder native push). Enrollment/service-token pas als MediaGuard ooit naar de Hub-inbox pusht.
5. **Connectorcontract alleen als nieuwe, alleen-lezen routes** onder `/connector/v1` plus `/healthz` en
   `/readyz` in core, door de webproxy (`apps/web/server.mjs`) doorgezet. Het contractfoutformaat geldt alleen voor
   die nieuwe routes; bestaande routes en foutvormen blijven ongewijzigd (MSO 1.6.0, web en Android hangen ervan
   af). `propose`/`apply`/`rollback` antwoorden `501 capability_unavailable` tot er een geverifieerde
   Hub-policy-sleutel en scopes zijn — nooit gesimuleerd succes.
6. **OpenAPI blijft admin-only** (bewuste keuze in 1.2.2: "API-documentatie dicht vanaf internet"). Capability-
   detectie voor sleutels komt via `GET /connector/v1/discover` en een in de repo geëxporteerd
   `docs/openapi/mediaguard.openapi.json` met drifttest.
7. **Noodstop:** de Hub mag de MediaGuard-noodstop alleen **aan**zetten via de gedocumenteerde route; opheffen doet
   een mens in MediaGuard (of later via een R2-goedkeuring). Geen eigen Hub-vlag die de hooks omzeilt.
8. **TrueNAS-toegang van MediaGuard zelf** (poolstatus via `GET /api/v2.0/pool` en de optionele ZFS-snapshot vóór
   een vervanging, beide met eigen sleutel `truenas:apikey`, standaard niet ingesteld) blijft bestaan: het is een
   integriteitsvangnet. In platformmodus krijgt MediaGuard **geen** schrijfkey; die blijft exclusief bij de
   executor (contract §8). Het gebruik van de oude REST-API is een bekend risico (uitfasering door TrueNAS) en
   staat als follow-up genoteerd.
9. **Onbekende poolstatus** (TrueNAS-API niet ingesteld) pauzeert het opruimen van originelen nu niet
   (`test_pool_not_configured_means_no_pause`). Contract 03/EX11 vraagt "onbekend = geen massamutaties". Dit P0-ADR
   wijzigt dat gedrag **niet**; P3-13 krijgt een eigen ADR (voorstel: alleen in platformmodus, met de poolstatus
   van de Hub als invoer, onbekend → pauze).
10. **AI-adviseur** (betaalde Claude/OpenAI-API, standaard uit, maandlimiet) blijft een opt-in van de gebruiker; in
    platformmodus advies: uit laten (geen betaalde overage). Niet stil uitschakelen.
11. **Evidence:** deze repo schrijft alleen `B04-*` in `docs/platform-v2/EVIDENCE.json`. De stappen
    `I03/I16/I19/I20-*` staan in de takenlijst van `jelly-leon-platform`, `APP-*` bij `appstore-en-fork`; dubbele
    ID's in meerdere EVIDENCE-bestanden worden zo voorkomen.
12. **Baseline-referentie:** release 1.2.2 = tag `v1.2.2` → commit `30f7415` (versiecommit `de710c7` plus twee
    CI/E2E-commits waarop de release-run draaide). Code op de werkbranch is daaraan gelijk.

## Gevolgen

- Fase P3 bouwt alleen de hiaten uit `DELTA_MATRIX.md` §I; geen herbouw van opslag, back-ups, bewaking of noodstop.
- Afwijkingen van contract 1.1 (C-03, C-04, poorten, OpenAPI admin-only, TrueNAS-REST) zijn bewust en gemeld als
  contractissue aan de eigenaar van de contracten (`ai-server-manager`).
- Herzien wanneer: de Hub enrollment/push verplicht maakt, TrueNAS de REST-API verwijdert, of meerdere schrijvers
  op de MediaGuard-database nodig worden (ADR-003).
