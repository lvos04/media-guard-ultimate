# AV1_SAFETY_DIFF — bewuste afwijkingen van `spec/04-veiligheidsregels.md`

Bron: `lvos04/av1` @ `4b31986b8e91bc3d03f5372436e02644efe0c41d`
(`integrations/av1-source/spec/04-veiligheidsregels.md`, `spec/06-dolby-vision.md`,
`spec/08-opslag-herstel-en-schakelaars.md`). Code: `services/encoder/`.

Uitgangspunt (P2/Q3): **alleen strenger, of verschuiving van verantwoordelijkheid naar
de MediaGuard core-recovery-worker** — nooit een stille verzwakking. Iedere afwijking
hieronder heeft voor/na, risico, waarom, regressietest en rollback. De native
standalone-installatie (geen `MG_CONTAINER_MODE`, `AV1_REPLACEMENT_MODE=direct`)
gedraagt zich voor regels 1–18 als voorheen, behalve waar "geldt ook direct-modus" staat.

| # | Regel (spec/04) | Samenvatting wijziging | Geldt voor |
|---|---|---|---|
| D1 | 2, 4, 16 (atomisch vervangen door de encoder) | handoff: encoder stopt bij `awaiting_commit` | containermodus (default) |
| D2 | 9, 14 (hardlinks) | handoff encodeert ook bij `nlink>1`, rapporteert, verbreekt niets | containermodus |
| D3 | 11 (wijziging vóór vervangen) | controle verplaatst naar staging + core; nauwkeuriger (ns, inode, device) | containermodus |
| D4 | 3 (verificatie) | extra harde controles: ondertitelaantal, speelduur, kleur, containerkleur, DV-RPU per frame | **ook direct-modus** |
| D5 | 13 + spec/06 (HDR10+) | HDR10+ wordt in het *besluit* vóór DV gecontroleerd | **ook direct-modus** |
| D6 | 18 + spec/06 (DV skip-default, `dv_experimenteel`) | per-job `dv_mode` + profiel-allowlist van core (DEEL Q) | MediaGuard-jobs |
| D7 | 5 + P6 (audio-opschoning) | MediaGuard-standaard `keep_all`: niets verwijderen | MediaGuard-jobs |
| D8 | — (nieuw) | minimale besparing vóór staging (`min_saving_percent`) | MediaGuard-jobs |
| D9 | 17 + spec/08 (systeemschijf, mounts) | containeropslagvalidatie; ontbrekende mount → banen gepauzeerd | containermodus |
| D10 | AGENTS (backend alleen 127.0.0.1, LAN-gateway, SMB-helper) | bind 0.0.0.0 in compose-netwerk, token op alle `/api`, `/ws`; LAN/SMB/opslagwissel uit | containermodus |
| D11 | 18 (encoderwissels) | GPU-baan fail-closed tot een echte QSV-proef slaagt | containermodus |
| D13 | 7, 13 + spec/06 (HDR10+ altijd overslaan, nooit forceren) | eigen schakelaar `hdr10plus_mode`: `skip` (default) / `convert_verified` / `hdr10_fallback_explicit` | MediaGuard-jobs |
| D12 | — (code-fingerprint) | wijziging `beslissen.py` → open besluiten worden herbeoordeeld, AI-toestemming ingetrokken | **ook direct-modus** (eenmalig) |

---

## D1 — Handoff i.p.v. directe vervanging (P2, bindend)

- **Voor:** na geslaagde verificatie kopieerde `vervanging.atomic_replace` het resultaat
  als verborgen `.av1-pipeline-<job>-<hash>.deel` naast de bron, herstelde
  eigenaar/ACL/xattrs, `fsync` en `os.replace` over het bronpad. Geen behouden bronkopie.
- **Na (`AV1_REPLACEMENT_MODE=handoff`, default als `MG_CONTAINER_MODE=1`):**
  `uitvoerder.run_job` roept na de *ongewijzigde* verificatie
  `app/mediaguard/handoff.stage_for_commit` aan: bron opnieuw stat'en
  (device/inode/size/`mtime_ns`), resultaat met `fsync` naar
  `/work/staging/<job_id>/out.mkv` (rename op hetzelfde FS, anders kopie+fsync),
  sha256, `source_snapshot` vastleggen, status `wacht_op_commit`
  (contract: `awaiting_commit`). `atomic_replace`, `refresh_after_replacement`
  (Sonarr/Radarr-refresh) en de `bestand`-update worden **niet** aangeroepen.
  `committed` (alleen bij `replace_eligible` en optioneel gelijke sha256) of
  `rejected`/`cancel` ruimen uitsluitend de numerieke stagingmap op.
- **Risico:** gestagede resultaten blijven ruimte innemen tot core antwoordt; core kan
  een snapshot negeren. Staging valt (nog) niet onder de cachereservering van de planner.
- **Waarom:** P2 — "De AV1-worker zelf mag niet meer direct finale bronpaden muteren";
  de recovery-worker stelt eerst een terugzetbare bron veilig.
- **Regressietests:** `tests/test_mediaguard_bridge.py::test_handoff_stopt_bij_awaiting_commit_en_muteert_de_bron_nooit`
  (sha256 + inode + `mtime_ns` van de bron identiek, cache leeg, staging-sha klopt,
  dubbele job 409, foute sha 409, commit ruimt staging), `…::test_rejected_ruimt_staging_op_en_laat_bron_staan`,
  `…::test_cancel_wachtend_en_awaiting_commit`. Direct-modus blijft gedekt door de
  ongewijzigde `test_uitvoerder.py`/`test_herstel.py` (atomische vervanging, crashherstel).
- **Rollback:** `AV1_REPLACEMENT_MODE=direct` (alleen voor een standalone installatie
  zonder core; in MediaGuard zou dat twee writers opleveren en is het niet ondersteund).

## D2 — Hardlinks in handoff-modus

- **Voor:** `nlink>1` → job stopt tenzij functie `hardlink_verbreken` aan, policy
  `verbreek_na_bevestiging` en per-bestand bevestiging.
- **Na:** in handoff-modus wordt de gate overgeslagen, want er wordt niets verbroken:
  de bron blijft onaangeraakt. `source_snapshot.nlink` en `inode` gaan naar core, die
  (P2) linkcount + inode + library-policy toepast vóór een commit.
- **Risico:** core commit zonder naar `nlink` te kijken → hardlink stil verbroken.
- **Regressietest:** `test_hardlink_wordt_in_handoff_gemeld_niet_verbroken` (nlink=2 gemeld,
  beide namen houden dezelfde inode). Direct-modus: `test_uitvoerder.py::test_explicitiet_bevestigde_hardlink…` ongewijzigd groen.
- **Rollback:** direct-modus, of core weigert (`rejected`) bij `nlink>1`.

## D3 — Bronwijziging tijdens de job

- **Voor:** `atomic_replace` vergeleek device/inode/size/`st_mtime` (float) vlak vóór `os.replace`.
- **Na (handoff):** dezelfde controle in `stage_for_commit`, met `st_mtime_ns`; daarnaast
  levert de job een snapshot zodat core vlak vóór zijn eigen commit opnieuw kan vergelijken.
  Gewijzigde bron → `failed`, geen staging.
- **Regressietest:** `test_gewijzigde_bron_tijdens_job_faalt_zonder_staging`.
- **Rollback:** n.v.t. (strenger).

## D4 — Strengere verificatie (`app/verificatie.py`, `app/media/dv_controle.py`)

Alle bestaande controles en hun volgorde/redenen zijn ongewijzigd; daarna komen erbij:

| Nieuwe controle | Faalreden |
|---|---|
| aantal ondertitelsporen = `subs_houden` | `aantal bewaarde ondertitelsporen wijkt af` |
| speelduur binnen max(1 s, 1 %) | `speelduur wijkt af van de bron` |
| HDR/DV-bron: primaries/transfer/matrix gelijk aan probe van vóór de encode (nieuw veld `probe.kleur`) | `kleur-<veld> gewijzigd` |
| HDR/DV-bron: geen container- vs bitstreamkleurconflict (mediainfo `*_Original`) | `container- en bitstreamkleur verschillen` |
| DV-besluit: DOVI-record aanwezig, RPU in de frames (ffprobe "Dolby Vision Metadata"), AV1 → profiel 10, BL-compat gelijk (7 → 1) | `Dolby Vision-RPU ontbreekt in de frames…` e.d. |

- **Risico:** een eerder "geslaagde" job kan nu falen (bv. bron met afwijkende
  containerduur). Dat is fail-safe: bron blijft, job `mislukt`.
- **Waarom:** spec/02 punt 4 en 11 en Q1.1/Q3.7 — "DV-uitvoer zonder RPU/gewijzigde
  basis/kleurwaarden → verificatiefout en bron behouden".
- **Regressietests:** `test_mediaguard_dv.py::test_encode_zonder_expliciete_dv_vlag_faalt_de_verificatie`
  (echte encode met "auto" → RPU weg → `failed`, geen staging, bron bytegelijk),
  `…::test_gewijzigde_kleur_faalt_de_dv_controle` (mkvmerge `--colour-primaries 0:1`),
  bestaande `test_verificatie.py` (gemockte probes zonder nieuwe velden) groen.
- **Rollback:** code-revert van de extra blokken in `verify_result` (niet aanbevolen).

## D5 — HDR10+ vóór Dolby Vision in het besluit

- **Voor:** `beslis()` controleerde DV vóór HDR10+; een DV+HDR10+-bestand kreeg met
  `dv_modus=av1` het besluit `hercoderen` (de uitvoerder weigerde later alsnog).
- **Na:** HDR10+ wordt eerst gecontroleerd → `overslaan` met reden "HDR10+…". Geen force-optie.
- **Regressietest:** `test_mediaguard_policy.py::test_hdr10plus_met_dv_wordt_altijd_overgeslagen`,
  `test_mediaguard_dv.py::test_hdr10plus_blijft_skip` (echte HDR10+-fixture via `hdr10plus_tool inject`).
- **Rollback:** n.v.t. (strenger, consistent met de uitvoerder).

## D6 — Dolby Vision: van globale skip naar per-job keuze (DEEL Q, voorrang op P5)

- **Voor:** `dv_modus` per profiel `skip|av1`; `av1` alleen met functie
  `dv_experimenteel` + bevestiging; geen onderscheid MEL/FEL (profiel 7 altijd via
  `dovi_tool -m 2 --discard`, dus FEL-verlies); geen per-bestand keuze.
- **Na (MediaGuard-jobs):** core stuurt per job `dv_mode` + `dv_allowed_profiles`
  (overerving en audit zitten in core, Q1.7). `app/mediaguard/policy.py` vertaalt naar
  opt-in profielsleutels (`dv_profielen`, `dv_fel_experimenteel`) die `beslissen.beslis`
  gebruikt:
  - `keep_original` → `overslaan` ("Origineel behouden").
  - 8.1/8.4 in allowlist → uitvoerbare `libsvtav1`-job met expliciet `-dolbyvision 1`.
  - 7 MEL (EL-type via `dovi_tool info`) met `7_mel` → bestaande drie-staps-route.
  - 7 FEL **of onbekend EL-type** → alleen met `experimental_keep_source` + `7_fel`, als
    `apart_resultaat` met expliciete waarschuwing; `replace_eligible=false`, `committed` → 409.
  - 5 → altijd `overslaan` (geen bewezen route), ook met alle toggles aan.
  - `trial_copy` en `experimental_keep_source` zijn nooit `replace_eligible`.
  - Route technisch niet beschikbaar (capabilities) → `overslaan` met reden.
  De oude `dv_experimenteel`-schakelaar wordt voor brugjobs **niet** geraadpleegd: de
  toestemming komt van core. Native profielen zonder `dv_profielen` gedragen zich als voorheen.
- **Wat niet uit kan (Q1.1):** RPU-/kleur-/basislaagverificatie (D4), streambehoud,
  bronwijzigingsdetectie, DV5/FEL-grenzen.
- **Risico:** speler toont AV1 DV profiel 10 niet (playback is niet bewezen — zie
  `docs/AV1_DV_PLAYBACK_MATRIX.md`); dat blokkeert volgens Q3 geen proefencode.
- **Regressietests:** `test_mediaguard_policy.py` (toggle uit/aan, 8.4-allowlist, MEL,
  FEL/onbekend, FEL-experimenteel, DV5, legacy-gedrag), `test_mediaguard_dv.py`
  (echte 8.1- en 8.4-encodes met read-back van profiel 10.1/10.4, RPU per frame, PQ/HLG).
- **Rollback:** core stuurt `dv_mode=keep_original` (per job), of profiel zonder allowlist.

## D7 — Audio en ondertitels: eerst inventaris, niets weggooien (P6)

- **Voor:** `kies_sporen` filterde op `audio_talen`, verwijderde commentaar en hield
  met `beste_per_taal` één spoor per taal; ondertitels op taal.
- **Na (MediaGuard-default `audio_policy.keep_all=true`, `subtitle_policy.keep_all=true`):**
  alle audio (dual audio, origineel, NL, commentaar, audiodescriptie) en alle
  ondertitels (forced, signs & songs, SDH) + attachments blijven. Opschonen alleen met
  expliciete `keep_all=false` per library-policy, met taalnormalisatie (`nl/dut→nld` …)
  en `beste_per_taal=false`; de verificatie telt audio- én ondertitelsporen (D4).
- **Regressietests:** `test_mediaguard_bridge.py::test_handoff_stopt…` (echte encode:
  eng+jpn+dut+commentaar+AD, 3 subs incl. forced, font-attachment in output),
  `test_mediaguard_policy.py::test_mediaguard_standaard_houdt_dual_audio…`,
  `…::test_oude_standaard_zou_wel_sporen_verwijderen`.
- **Rollback:** core stuurt `keep_all=false` met `keep_languages`.

## D8 — Minimale besparing

- **Voor:** alleen "resultaat groter dan bron" faalde.
- **Na:** handoff zet alleen klaar als besparing ≥ `min_saving_percent` (default 15 %);
  anders `skipped` ("origineel behouden"), staging leeg. F2.7/P4.7.
- **Regressietest:** `test_te_weinig_besparing_houdt_origineel`. **Rollback:** `min_saving_percent=0`.

## D9 — Opslag in containermodus

- **Voor:** `validate_storage` eiste config op de systeemschijf (`st_dev` van `/`),
  `aparte_schijf` een echte mount, uid-eigenaarschap en een 150-GB-systeemschijfbodem.
- **Na (container):** `validate_container_storage`: `/config`, `/work/{cache,work,staging}`,
  `/config/backups` bestaan, geen symlink, rw; ≥1 leesbare mediaroot; werk en media
  niet genest. Geen uid-gelijkheid (TrueNAS-ACL's), geen systeemschijfbodem (datasets);
  de UI-cachebodem `cache_pauze_gb` blijft gelden. Ontbrekende mount → lifespan pauzeert
  de banen (bestaand mechanisme), `health` → `degraded/unavailable`.
- **Regressietest:** `test_ontbrekende_mediamount_is_fail_closed`, `test_containermodus_uit_env`;
  native: `test_opslag.py` ongewijzigd groen.
- **Rollback:** `MG_CONTAINER_MODE=0`.

## D10 — Netwerk en Ubuntu-hostonderdelen

- **Voor:** FastAPI op `127.0.0.1:8000`, LAN alleen via de Next.js-gateway met
  scrypt-wachtwoord; SMB via root-owned sockethelper; opslagwissel via UI.
- **Na (container):** uvicorn bindt `0.0.0.0` **binnen het private compose-netwerk** (geen
  gepubliceerde poort). `X-MediaGuard-Token` (=`MG_INTERNAL_TOKEN`, ≥24 tekens) verplicht op
  `/api/mediaguard/v1/*`; middleware eist hetzelfde token op alle oude `/api/*` en `/ws`
  (`MG_PROTECT_LEGACY_API=0` alleen voor lokale ontwikkeling). Zonder token-configuratie: 503
  (fail-closed). LAN-inschakelen → 409, `smb://`-bibliotheek → 422, opslagwissel → 409.
  De Ubuntu-scripts staan ongewijzigd in `services/encoder/host/ubuntu/`.
- **Risico:** token lekt binnen het compose-netwerk → volledige encoder-API. Mitigatie:
  secret via TrueNAS-app-config, `hmac.compare_digest`, geen poortpublicatie.
- **Regressietests:** `test_zonder_geconfigureerd_token_is_de_brug_dicht`,
  `test_token_vereist_op_brug_en_legacy_api`, `test_legacy_lan_en_smb_zijn_in_containermodus_uitgeschakeld`;
  native LAN/SMB-tests (`test_api.py`, `test_smb_helper.py`) groen.
- **Rollback:** native installatie.

## D11 — GPU-baan fail-closed

- **Voor:** QSV-jobs liepen als `av1_qsv` in ffmpeg zat; een kapotte rendernode gaf een jobfout.
- **Na (container):** bij start `CapabilityService.detect(run_qsv_probe=True)`; GPU-baan
  gepauzeerd met reden `MediaGuard: GPU-baan uit: …` tot een echte QSV-proefencode slaagt;
  een handmatige pauze wordt nooit overschreven. `plan` kiest dan de CPU-baan.
- **Regressietests:** `test_capabilities_meldt_gpu_baan_uit_met_reden_en_cpu_aan`,
  `test_lane_gate_overschrijft_nooit_een_handmatige_pauze`.
- **Rollback:** n.v.t.

## D12 — Gewijzigde besliscode-fingerprint

`beslissen.py` is uitgebreid (D5–D7). `scanner.DECISION_CODE_FINGERPRINT` verandert
daardoor: een bestaande standalone installatie beoordeelt open besluiten bij de volgende
scan opnieuw, en `ai_controller.reconcile_safety_fingerprint` trekt de AI-toestemming in
(bestaand, conservatief mechanisme). Geen gegevensverlies; de eigenaar moet de AI-controller
na controle opnieuw vrijgeven.

## D13 — HDR10+: eigen schakelaar met geverifieerde route (Q1.1, gebruikersverzoek 2026-11-02)

- **Voor:** spec/04 regel 7 en 13: HDR10+ altijd overslaan, geen forceerknop, geen
  uitzondering. `beslis()` gaf `overslaan`, `run_job` en `verify_result` weigerden.
- **Na:** per job `hdr10plus_mode` (contractveld `hdr10plus` is een alias; beide gelijk of 422):
  - `skip` (**default**) — ongewijzigd: origineel behouden.
  - `convert_verified` — alleen als `capabilities.hdr10plus_route_verified=true`
    (FFmpeg ≥ 8.1 met `libaom-av1` aanwezig, libdav1d-read-back aanwezig). Besluit
    `encoder=libaom-av1`, `hdr10plus_route=behouden`. Verificatie vergelijkt per
    presentatietijdstempel álle ST 2094-40-velden van bron en resultaat (begin + vensters
    op 25/50/75 %), naast de bestaande MDCV/CLL/kleur/stream-checks. Eén ontbrekend of
    afwijkend frame → `failed`, geen staging, bron ongemoeid.
  - `hdr10_fallback_explicit` — bewuste opt-in: encode als statisch HDR10 via libsvtav1.
    Het verlies staat in `reden`, `warnings` ("…gaat VERLOREN…") en
    `metadata_loss=["hdr10plus_dynamic_metadata"]` in plan én job. Nooit default.
  - DV + HDR10+ in één bron: altijd `overslaan` (geen bewezen gecombineerde route).
- **Waarom:** Q1.1 ("HDR10+ — eigen, afzonderlijke profielschakelaar voor toekomstige
  bewezen methode; geen verlies stilzwijgend") en het expliciete gebruikersverzoek. De route
  is bewezen met een echte encode + read-back (`docs/AV1_DV_PLAYBACK_MATRIX.md`).
- **Risico:** (1) libaom is veel trager dan SVT-AV1; (2) de per-job-controle bemonstert
  vensters, geen volledige decode; (3) playback van AV1-HDR10+ op clients is niet getest;
  (4) een tweede FFmpeg-binary (8.1.3, alleen libaom/libdav1d) naast de systeem-8.0.1.
- **Wat niet uit kan:** de per-frame-verificatie bij `convert_verified`; de
  `metadata_loss`-labeling bij de fallback.
- **Regressietests:** `tests/test_mediaguard_hdr10plus.py` (route-capability; echte
  convert met 96/96 identieke frames + MDCV/CLL; encoder die HDR10+ laat vallen → `failed`
  en bron bytegelijk; fallback gelabeld en eerlijk zonder HDR10+; zonder 8.1-binary →
  `overslaan`), `test_mediaguard_policy.py` (default skip, libaom-keuze, fallback-label,
  DV+HDR10+, alias-consistentie), `test_mediaguard_dv.py::test_hdr10plus_blijft_skip`.
- **Rollback:** `hdr10plus_mode=skip` (default), of het 8.1-binary weglaten
  (`MG_FFMPEG_HDR10PLUS` naar een niet-bestaand pad) → route `route_verified=false`.
