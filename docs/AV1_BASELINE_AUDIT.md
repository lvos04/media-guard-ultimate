# AV1_BASELINE_AUDIT — reproduceerbare baseline van `lvos04/av1`

- Repository: https://github.com/lvos04/av1, branch `master`
- Gepinde commit: `4b31986b8e91bc3d03f5372436e02644efe0c41d` (2026-11-02 09:49 +0100)
- Audit uitgevoerd: 2026-11-02 (P3 stap 1). Resultaten zijn **gemeten**, niet overgenomen.
- Geïmporteerd: `backend/` → `services/encoder/` (bytegelijk bij import, `diff -rq` leeg),
  `frontend/` → `apps/web`, overige → `integrations/av1-source/`.

## 1. Module-inventaris (backend, 11 293 regels Python + 15 migraties)

| Laag | Modules | Rol |
|---|---|---|
| Media-engine (`app/media/`) | `probe.py` (330), `beslissen.py` (278), `commandos.py` (418), `bestandsfilter.py` (242), `voortgang.py` (62) | probe (HDR10 in frame-side-data, video-only bitrate, DV), besluit, commando's (SVT/QSV/DV7/mkvmerge-HDR-herstel), extrafilter |
| Uitvoering | `uitvoerder.py` (738), `planner.py` (375), `verificatie.py` (75), `vervanging.py` (354), `wachtrijherstel.py` (269), `bestandsmetadata.py` (132) | job van cache tot atomische vervanging, CPU/GPU-banen, crashherstel, eigenaar/ACL/xattr-behoud |
| Catalogus | `scanner.py` (715), `scanscheduler.py` (208), `beslisser.py` (88), `db.py` (148) + `migrations/001–015` | incrementele scan, 60-min-stabiliteitsvenster, SQLite-WAL met back-up vóór migratie |
| Beheer/API | `api.py` (2593), `main.py` (166), `live.py` (215), `cli.py` (195) | FastAPI REST + WebSocket, levenscyclus (herstel vóór planner) |
| Bewaking | `health.py`, `systeem.py` (405), `metingen.py`, `onderhoud.py` (258), `backups.py`, `kwaliteit.py` (369) | dependency-/QSV-diagnose, metingen, logrotatie, back-ups, VMAF/proefdraai |
| Integraties/AI | `integraties.py` (206), `geheimen.py`, `ai_controller.py` (1217) | Jellyfin/Radarr/Sonarr (Fernet-geheimen), begrensde AI-controller |
| Host (Ubuntu) | `bronnen.py` (325, SMB-sockethelper), `lan.py` (LAN-gateway-auth), `opslag.py`, `config.py` | native single-host-aannames |
| Install | `install/*.sh`, `install/av1-pipeline-smb-helper.py`, `systemd/` | Ubuntu 26.04, systemd, UFW, root SMB-helper |
| Frontend | Next.js 16.3.3 App Router, React 19.2.8, Tailwind 4, Recharts 3; pagina's ai, beslissingen, bibliotheken, fouten, instellingen, kwaliteit, logs, profielen, wachtrij, workers | |
| Tests | `backend/tests/` 18 bestanden / 177 tests; `test/` (startcode) 3 bestanden / 39 tests; `test/maak-testbestanden.sh` | |

Tests per bestand (backend): ai_controller 18, api 29, beslissen 9, bestandsfilter 24, bronnen 11,
commandos 9, db 2, herstel 7, integraties 5, onderhoud 5, opslag 4, planner 12, scanner 11,
scanscheduler 6, smb_helper 9, systeem 3, uitvoerder 9, verificatie 4.

## 2. Resultaten

### 2a. Host-sandbox (Python 3.13 venv, Ubuntu 24.04-userland, FFmpeg 6.1.1, géén mkvmerge/dovi_tool/mediainfo/`setfacl`, root)

| Suite | Resultaat |
|---|---|
| backend pytest | **121 passed, 52 failed, 4 skipped** (177) |
| ruff / mypy (strict) | groen / groen |

Oorzaken van de 52 failures (per test nagelopen):

| Oorzaak | Aantal | Tests |
|---|---|---|
| **Fixture-tijdsafhankelijkheid:** `scanner.FILE_STABILITY_SECONDS=3600`; versgemaakte `testdata/` krijgt categorie `wacht` en geen besluit → `assert decision is not None` e.d. | 42 | api 16, planner 11, scanner 6, uitvoerder 9 (een deel raakt daarna óók het ontbrekende `setfacl`/mkvmerge) |
| `setfacl` ontbreekt (pakket `acl`) — eerste-gebruiktest en metadatabehoud | 8 | bronnen 1, herstel 7 |
| `mkvmerge` ontbreekt | 1 | commandos::test_opschonen_behoudt_lettertypen |
| Intel QSV-rendernode ontbreekt (echte `av1_qsv`-encode) | 1 | planner::test_cpu_en_gpu_baan_draaien_werkelijk_gelijktijdig |

Skips (4): mkvmerge ontbreekt (1), QSV-rendernode (1), `dv81.mkv` niet gemaakt omdat
dovi_tool ontbrak (2). FFmpeg 6.1 kent bovendien geen `libsvtav1 -dolbyvision` (≥ 7.1 vereist).

### 2b. Toolchain-image `mediaguard/encode` (Ubuntu 26.04, FFmpeg 8.0.1-3ubuntu2 met libsvtav1/SVT-AV1 2.3.0, av1_qsv/libvpl 2.16, av1_vaapi, dovi_tool 2.3.1, mkvmerge 97.0, mediainfo 26.01, acl, Python 3.14.4, uid 568)

Originele code, originele maplayout (`/tmp/orig`), ongewijzigd:

| Run | Resultaat |
|---|---|
| backend pytest, verse testdata | **137 passed, 39 failed, 1 skipped** |
| backend pytest, testdata ≥ 2 uur oud (`touch -d '-2 hours'`) | **175 passed, 1 failed, 1 skipped** |
| startcode `test/` | **39 passed** |
| ruff check / mypy strict | All checks passed / Success (35 bestanden) |
| frontend `npm ci` / `lint` / `typecheck` / `build` (Node 22.23, host) | geslaagd / 0 meldingen / 0 fouten / build ok |

De 38 extra failures bij verse testdata verdwijnen volledig met verouderde testdata →
uitsluitend het stabiliteitsvenster. De ene resterende failure en de skip vragen een echte
Intel-QSV-rendernode (niet aanwezig in deze sandbox) — geen codefout.

Na de import in `services/encoder` (vóór MediaGuard-wijzigingen) faalden daarnaast de 9
`test_smb_helper.py`-tests (ERROR): het testpad `parents[2]/install/…` bestaat niet meer
in de nieuwe maplayout.

### 2c. Testaanpassingen (alleen testinfrastructuur, geen productiecode)

1. `tests/conftest.py`: fixturescript gekopieerd naar `tests/fixtures/`; testdata standaard
   `services/encoder/.testdata`; **mtime van fixtures twee uur terug** (maakt de
   impliciete aanname van de originele suite expliciet; het stabiliteitsvenster zelf is ongewijzigd).
2. `tests/test_smb_helper.py`: pad naar `host/ubuntu/install/av1-pipeline-smb-helper.py`.
3. `tests/test_planner.py::test_cpu_en_gpu_baan_draaien_werkelijk_gelijktijdig`: dezelfde
   QSV-rendernode-skip als `test_commandos.py` al had.
4. `tests/test_db.py`: verwachte migratieversies 1–16 (nieuwe additieve migratie 016).

## 3. Eindresultaten na de MediaGuard-integratie (zelfde code, 243 tests)

| Omgeving | passed | failed | skipped | Toelichting |
|---|---|---|---|---|
| `mediaguard/encode` testimage (toolchain, uid 568) | **251** | **0** | 2 | beide skips: geen Intel-QSV-rendernode (`test_commandos::test_qsv…`, `test_planner::test_cpu_en_gpu…`) |
| host-venv (FFmpeg 6.1, geen acl/mkvmerge/dovi_tool/mediainfo) — gemeten vóór de HDR10+-uitbreiding (233 tests) | 178 | 40 | 15 | 39 failures: `getfacl`/`setfacl` ontbreken (bestaande metadata-/ACL-behoudcode → job wordt veilig gestopt); 1: mkvmerge ontbreekt. Skips: DV-fixtures/ffmpeg < 7.1 (12), mkvmerge (1), QSV (2) |
| ruff check (app + tests) / mypy strict (47 bestanden) | groen | | | |

Nieuwe tests (66): `test_mediaguard_policy.py` (24, puur besluit/commando),
`test_mediaguard_hdr10plus.py` (5, echte libaom-HDR10+-encodes met per-frame read-back),
`test_mediaguard_bridge.py` (19, API/auth/handoff/fail-closed, echte encodes),
`test_mediaguard_dv.py` (10, echte DV/HDR-encodes met read-back),
`test_mediaguard_trials.py` (6, echte proefencodes + metingen),
`test_mediaguard_import.py` (2). Alleen in het image volledig uitvoerbaar: alles wat
DV-fixtures, `-dolbyvision`, mkvmerge of `getfacl` nodig heeft.

## 4. Bekende beperkingen van de baseline

- Geen libvmaf in Ubuntu's FFmpeg 8.0.1 → VMAF-metingen (originele functie `vmaf`) zijn
  in het image niet beschikbaar; SSIM/PSNR wel.
- QSV/VA-API-paden zijn alleen op echte Intel-hardware te testen (UHD 770 heeft geen
  AV1-encode; Arc A310 wel). Niet getest in deze sandbox.
- De originele suite draait in ~5 min (toolchain) en vereist schrijfbare ACL's/xattrs op
  `tmp_path` (overlayfs: ok).
