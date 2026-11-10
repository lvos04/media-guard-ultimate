# AV1_REPO_INTEGRATION — `lvos04/av1` in MediaGuard Ultimate

Bron: https://github.com/lvos04/av1 @ `4b31986b8e91bc3d03f5372436e02644efe0c41d`
(2026-11-02). Backend → `services/encoder/` (package `app`, ongewijzigde namen en
interfaces); frontend → `apps/web` (andere agent); specs/startcode/install/test →
`integrations/av1-source/` (read-only referentie). Contract met core:
`packages/contracts/encoder-bridge.md`.

## 1. Modulegrenzen (P3 stap 2)

```
                ┌──────────── MediaGuard core (recovery-worker = enige library-writer) ───────────┐
                │  policy/UI/overerving, canary, quarantaine, commit, Sonarr/Radarr-refresh       │
                └──────────────▲─────────────────────────────────────────────▲───────────────────┘
                     X-MediaGuard-Token │ /api/mediaguard/v1/*            committed/rejected
┌──────────────────────────────┴──────────────────────────────────────────────┴───────────────────┐
│ services/encoder — HOST-ADAPTERS (nieuw, app/mediaguard/)                                       │
│  api.py (router) · auth.py (token, legacy-API-middleware) · capabilities.py (ffmpeg/DV/QSV/     │
│  VA-API/mounts, fail-closed banen) · policy.py (core-policy → profielsleutels) · service.py      │
│  (plan, jobs, snapshot, commit/reject/cancel) · handoff.py (staging) · trials.py (P4) ·         │
│  inventory.py (P6) · importer.py (P8) · container.py (provisioning)                             │
├──────────────────────────────────────────────────────────────────────────────────────────────────┤
│ MEDIA-ENGINE (behouden; alleen minimale, geteste uitbreidingen)                                  │
│  probe          app/media/probe.py         (+ kleurvelden)                                       │
│  selectie/policy app/media/beslissen.py    (+ opt-in: DV-allowlist, FEL-experimenteel, keep-all, │
│                                              HDR10+ vóór DV)                                     │
│  commando's     app/media/commandos.py     (ongewijzigd)                                         │
│  runner         app/uitvoerder.py, planner.py (+ per-job policy-overlay, handoff-tak,            │
│                                              hardlink-gate alleen in direct)                     │
│  verificatie    app/verificatie.py         (+ subs/duur/kleur/containerkleur/DV-RPU)             │
│                 app/media/dv_controle.py   (nieuw: read-back)                                    │
│  vervanging     app/vervanging.py          (ongewijzigd; alleen in direct-modus aangeroepen)     │
├──────────────────────────────────────────────────────────────────────────────────────────────────┤
│ STANDALONE-ONDERDELEN (behouden, in containermodus uit)                                          │
│  native API app/api.py, scanner/scanscheduler, ai_controller, integraties, lan.py, bronnen.py    │
│  (SMB), opslag.py; host/ubuntu/{install,systemd} (kopie van integrations/av1-source)             │
└──────────────────────────────────────────────────────────────────────────────────────────────────┘
```

Eén encoder, één wachtrij: een brugjob is een gewone goedgekeurde rij in `job`
(`besluit.goedkeuringsbron='mediaguard'`, `job.mg_bron='mediaguard'`) die de bestaande
`Planner` (CPU/GPU-banen, totale encodegrens, cachereservering, pauzes, stiltevensters)
en de bestaande `run_job` uitvoeren. Brugjobs krijgen een library-rij per
mediaroot/dataset (`scan_interval=0`, de scheduler scant hem nooit; core is de scanner)
en een eigen profiel `MediaGuard (per-job policy)`; de policy van core zit per job in
`job.mg_inst` en wordt in `run_job` over het profiel gelegd.

## 2. Wat behouden is, wat veranderde

| Module | Status | Wijziging |
|---|---|---|
| `media/probe.py` | behouden | + `info["kleur"]` (primaries/transfer/matrix/pix_fmt) |
| `media/beslissen.py` | uitgebreid | `Besluit.dv_route`, `Besluit.apart_resultaat` (defaults → oude payloads geldig); `dv_route_van()`; opt-in sleutels `dv_profielen`, `dv_fel_experimenteel`, `hdr10plus_modus`, `audio_alles_behouden`, `ondertitels_alles_behouden`; HDR10+ vóór DV. `STANDAARD` ongewijzigd |
| `media/commandos.py` | + `encode_cpu_aom` | bestaande functies ongewijzigd; nieuwe libaom-bouwer voor de HDR10+-route (FFmpeg 8.1-binary) |
| `media/hdr10plus_controle.py` | nieuw | per-frame ST 2094-40-vergelijking (ffprobe default-writer) |
| `media/dv_controle.py` | nieuw | DV/kleur-read-back (ffprobe record + frames, mediainfo, dovi_tool voor HEVC) |
| `verificatie.py` | uitgebreid | extra harde checks na de bestaande (zie AV1_SAFETY_DIFF D4); `VerificationResult.dv` |
| `uitvoerder.py` | uitgebreid | `mg_inst`-overlay; handoff-tak vóór `atomic_replace`; hardlink-gate alleen in direct |
| `vervanging.py`, `planner.py`, `scanner.py`, `wachtrijherstel.py` | ongewijzigd | |
| `config.py` | uitgebreid | `container_mode`, `replacement_mode`, `staging_root`, `media_roots`, `render_node`; `validate_container_storage` |
| `main.py` | uitgebreid | brugrouter, middleware (container), containerprovisioning, capability-detectie + lane-gates bij start |
| `api.py` | 3 guards | SMB-bron, LAN-inschakelen, opslagwissel geweigerd in containermodus |
| `migrations/016_mediaguard_bridge.sql` | nieuw | alleen `ALTER TABLE ADD COLUMN`/`CREATE TABLE` |
| tests | behouden + 4 testaanpassingen | conftest (fixturepad + mtime-backdating), smb-helperpad, QSV-skip in planner-test, migratieversie 16 in `test_db` |

Hostspecifieke bestanden van de bron (`install/`, `systemd/`) staan als kopie in
`services/encoder/host/ubuntu/` zodat de standalone-route reproduceerbaar blijft.

## 3. Statusmodel brug ↔ legacy

| contract | `job.status` | betekenis |
|---|---|---|
| queued | `wacht` | in de bestaande wachtrij |
| running / verifying | `bezig` / `verifieren` | bestaande uitvoerder |
| awaiting_commit | `wacht_op_commit` (nieuw) | geverifieerd, in `/work/staging/<id>/out.mkv` |
| committed / rejected | `gecommit` / `geweigerd` (nieuw) | door core gemeld; staging opgeruimd |
| failed / skipped / cancelled | `mislukt` / `overgeslagen` / `afgebroken` | ongewijzigd |
| (direct-modus) committed | `klaar` | bron al door de encoder vervangen |

`awaiting_commit` blokkeert een tweede job op hetzelfde bestand (409).

## 4. API (samenvatting; details in het contract)

`GET health|capabilities`, `POST probe|plan|jobs|trials`, `GET jobs|jobs/{id}|trials/{id}`,
`POST jobs/{id}/cancel|committed|rejected`, `POST import/standalone-db`. Extra velden t.o.v.
het contract (additief): `replace_eligible`, `warnings`, `correlation_id`, `media_ref`,
`lane`; plan: `actie`/`reden`-aliassen, `dv{route,mode,executes,el_type,…}`,
`smart` + `recommended_policy` bij `run_trials`; probe: `dv_route`, `dv_el_type`
(`mel`/`fel`/null), `inventory` (audio/ondertitelrollen), `stat`; `/probe` accepteert ook
paden onder de stagingroot (core leest het resultaat terug). Policyvelden buiten het
contract: `cq`, `film_grain`, `quality_profile`, `hdr10plus_mode` (alias van `hdr10plus`, waarden `skip|convert_verified|hdr10_fallback_explicit`) (archiefkwaliteit|gebalanceerd|compact|custom).

## 5. Migratie van een bestaande standalone installatie (P8)

1. Oude app netjes stoppen (geen job `bezig`/`verifieren`, geen open `vervang_transactie`):
   expliciete writer-handoff — er mag nooit een tweede writer op dezelfde bestanden draaien.
2. `app.db` maken met de bestaande UI-back-up (consistente SQLite-backup, WAL-veilig) en
   in de dataset plaatsen onder `/config/import/app.db`.
3. `POST /api/mediaguard/v1/import/standalone-db {db_path, path_map, dry_run:true}` →
   rapport: schemaversie, aantallen libraries/bestanden/besluiten/jobs per status,
   besparing, auditregels, geheimen, mapping per library en blokkades
   (niet-gemapte library, actieve job, open transactie, encoder al gevuld).
4. Zelfde aanroep met `dry_run:false`: back-up van de huidige encoder-DB
   (`/config/backups/voor-migratie/`), SQLite-backup-API kopieert de oude DB, daarna
   **alleen additieve migraties** (016…), padprefixen herschreven (library, bestand,
   vervangtransacties, resultaatpaden), SMB → lokale read-only mount, `verwacht_device`
   gewist (nieuw filesystem), libraries terug in canary, nog wachtende oude jobs
   `overgeslagen` (`foutsoort=Geimporteerd`), beide workers gepauzeerd, auditregel.
   Ids blijven gelijk → core mapt `av1:<tabel>:<id>` → MediaGuard-uuid.
5. Geheimen: blijven Fernet-versleuteld; zonder het oude sleutelbestand in de UI opnieuw
   invoeren (nooit plaintext in logs). Profielen, besluiten, verificaties,
   kwaliteitsmetingen, besparingen en de auditlog blijven leesbaar.
6. `beslissen.py` is gewijzigd → de besliscode-fingerprint verandert: open besluiten
   worden bij een scan herbeoordeeld en AI-toestemming wordt ingetrokken (AV1_SAFETY_DIFF D12).

Getest in `tests/test_mediaguard_import.py` (oude DB met alleen 001–015, dry-run schrijft
niets, mapping verplicht, 016 toegepast, paden herschreven, jobs/workers veilig, tweede
import 409). Echte productie-DB's zijn niet getest.

## 6. Configuratie

Zie `docs/AV1_TRUENAS_PORT.md` (env-tabel). Native installatie: geen nieuwe variabelen
nodig; defaults (`MG_CONTAINER_MODE` uit, `AV1_REPLACEMENT_MODE=direct`) houden het oude gedrag.
