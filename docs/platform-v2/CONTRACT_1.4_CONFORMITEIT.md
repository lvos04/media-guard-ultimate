# Conformiteit met contract 1.4

Peildatum 2026-11-27. Bron is `lvos04/ai-server-manager`. Deze lijst beschrijft de toevoeging aan 1.3
voor de poolbron van MediaGuard. De bestaande conformiteitspunten staan in `CONTRACT_1.3_CONFORMITEIT.md`.

## 0. Voor alle repos (ALG)

| ID | Wijziging | Contract | Bestanden | Moment |
|---|---|---|---|---|
| C1.4-ALG-01 | Neem de twee 1.4-documenten byte-gelijk over van dezelfde vaste broncommit en pin hun SHA-256. | kop, §10 | `docs/platform-v2/PLATFORM_CONTRACTEN.md`, `docs/platform-v2/CONTRACT_1.4_CONFORMITEIT.md`, drifttest | NU (orkestratie per repo) |

## 1. ai-server-manager (Hub, contracteigenaar)

| ID | Wijziging | Contract | Bestanden | Moment |
|---|---|---|---|---|
| C1.4-ASM-01 | Geef een beheerder de keuze per bevestigde MediaGuard-koppeling. Toets `poolstatus.lezen` bij elke lezing en registreer gebruik. Geef de volledige gedateerde poolstatus in één antwoord. Toets weigering voor andere diensten, routes, schrijfmethoden en een ingetrokken koppeling. | §2.3, §4.4, §6.2 | `apps/api/src/hub/routers/adapterkoppelingen.py`, `apps/api/src/hub/routers/inventory.py`, `docs/openapi/hub-api.openapi.json`, `apps/api/tests/integration/test_adapterkoppelingen.py` | NU |

## 6. media-guard-ultimate (MediaGuard)

| ID | Wijziging | Contract | Bestanden | Moment |
|---|---|---|---|---|
| C1.4-MGU-01 | Bevestig de gekozen Hub met `GET /api/v1/inventory/pools` en het eigen pooltoken. Gebruik de Hub als tweede bron na de directe TrueNAS-lezing. Pauzeer in platformmodus bij onbekende, verlopen of ongezonde toestand. | §4.4, §6.2, §8 | `docs/adr/ADR-0025-poolstatus-in-platformmodus.md`, `apps/core/mediaguard/storage_forecast.py`, bewakings- en opruimtests | P3 (S-P3-690, na de Hub) |
