# Contract: core ⇄ mediaguard-encode (AV1-engine)

Encoder = backend uit `lvos04/av1` (services/encoder), draait als aparte container met
**read-only bibliotheek** en **read-write staging/cache**. Hij vervangt in MediaGuard-modus
(`AV1_REPLACEMENT_MODE=handoff`) NOOIT zelf bronbestanden: na verificatie blijft de
geverifieerde output in staging en wacht op commit door de recovery-worker van core.

Auth: header `X-MediaGuard-Token: <MG_INTERNAL_TOKEN>` op alle `/api/mediaguard/v1/*` routes.
Basis: `MG_ENCODER_URL` (default `http://mediaguard-encode:8000`).

| Methode | Pad | Doel |
|---|---|---|
| GET | `/api/mediaguard/v1/health` | `{status, version, av1_commit}` |
| GET | `/api/mediaguard/v1/capabilities` | ffmpeg-versie, encoders (`libsvtav1`, `av1_qsv`), `dovi_tool`, `mkvmerge`, `/dev/dri` devices, libvmaf, DV-routes die technisch kunnen |
| POST | `/api/mediaguard/v1/probe` | `{path}` → volledige probe (bestaand av1-formaat: video/HDR/DV/audio/subs/bitrate) |
| POST | `/api/mediaguard/v1/plan` | `{path, policy}` → beslissing van `beslissen.py` met reden, zonder uit te voeren |
| POST | `/api/mediaguard/v1/jobs` | start encode, zie request hieronder → `{job_id}` |
| GET | `/api/mediaguard/v1/jobs/{id}` | status + `staged_output` + `verification` + `source_snapshot` |
| POST | `/api/mediaguard/v1/jobs/{id}/cancel` | annuleer; ruimt cache op, raakt bron niet |
| POST | `/api/mediaguard/v1/jobs/{id}/committed` | core meldt: recovery heeft geactiveerd → encoder ruimt staging op |
| POST | `/api/mediaguard/v1/jobs/{id}/rejected` | core weigert activatie → encoder ruimt staging op |
| POST | `/api/mediaguard/v1/trials` | proefencode(s) op samples/kopie: `{path, samples:[{start,duration}], settings:[{encoder,crf|cq,preset,film_grain}], dv_mode}` → trial-resultaten (grootte, bitrate, tijd, VMAF/SSIM indien beschikbaar) |

### POST /api/mediaguard/v1/jobs
```json
{
  "source_path": "/media/tv/Show/Season 01/Show - S01E01.mkv",
  "correlation_id": "uuid", "media_ref": {"instance_id": "sonarr-series", "kind": "episode_file", "provider_id": 991},
  "policy": {
    "encoder": "auto|cpu|gpu", "crf": 29, "preset": 6, "target": "staging",
    "dv_mode": "keep_original|trial_copy|auto_after_verify|experimental_keep_source",
    "dv_allowed_profiles": ["8.1", "8.4", "7_mel"],
    "hdr10plus": "skip|convert_verified|hdr10_fallback_explicit",
    "audio_policy": {"keep_languages": ["eng","jpn","nld"], "keep_all": true},
    "subtitle_policy": {"keep_all": true},
    "min_saving_percent": 15,
    "decided_by": "mediaguard",
    "skip_thresholds": true
  },
  "trial_only": false,
  "avoid_lanes": ["cpu"]
}
```
`avoid_lanes` (optioneel, 2026-11-08): banen die bij MediaGuard vol zitten. Kiest het plan
voor dit bestand zo'n baan, dan maakt de encoder geen job aan en antwoordt
`409 "LANE_FULL: <baan>-baan zit vol …"`. Het antwoord van `POST /jobs` bevat naast `job_id`
ook `lane` (`cpu|gpu`); `GET /jobs/{id}` geeft `lane` altijd mee (verandert bij een
GPU→CPU-herkansing).

Response job (GET):
```json
{
  "job_id": 17, "status": "queued|running|verifying|awaiting_commit|committed|rejected|failed|skipped|cancelled",
  "skip_reason": null, "error": null, "progress": 0.42, "fps": 55.1, "eta_seconds": 300,
  "source_snapshot": {"path": "…", "size": 1, "mtime_ns": 1, "inode": 1, "device": 1, "nlink": 1, "probe": {…}},
  "staged_output": {"path": "/work/staging/17/out.mkv", "size": 1, "sha256": "…"},
  "verification": {"passed": true, "checks": [{"name": "duration", "passed": true, "detail": "…"}], "dv": {"rpu_present": true, "profile": "10.1"}, "hdr": {…}},
  "decision": {…}
}
```

### Keuze ligt bij MediaGuard (`decided_by` / `skip_thresholds`, 2026-11-04)
- Core stuurt bij elke job `policy.decided_by: "mediaguard"` en `policy.skip_thresholds: true`.
  MediaGuard heeft het bestand dan al gekozen met zijn eigen drempels per resolutie
  (`optimization.av1_threshold_*_kbps`), profielen en DV-/HDR-beleid.
- De encoder past bij `skip_thresholds: true` **geen eigen bitrate-, codec- of
  resolutiedrempels** toe (geen `skipped` met "… kbps onder drempel …"). Technische
  onmogelijkheden (bron onleesbaar, DV-route niet beschikbaar, HDR10+ zonder geverifieerde
  route, al AV1) blijven wél `skipped` met reden, net als `min_saving_percent` op het resultaat.
- Ontbreken de velden (oudere core), dan gedraagt de encoder zich als voorheen
  (achterwaarts compatibel). Onbekende waarden van `decided_by` → als ontbrekend behandelen.
- Bitratemaat: core rekent met dezelfde maat als `probe.video_kbps` van de encoder: bitrate van
  het videospoor; ontbreekt die, dan (grootte × 8 / speelduur) − audiobitrates.

### HDR10+ (encoder-implementatie 2026-11-02)
- Policy: `hdr10plus` (contractnaam) en/of `hdr10plus_mode` (alias; beide gelijk, anders 422).
  Waarden: `skip` (default, origineel behouden) · `convert_verified` (libaom-av1 uit FFmpeg ≥ 8.1,
  per-frame ST 2094-40-verificatie; anders `skipped`) · `hdr10_fallback_explicit` (bewust
  alleen statisch HDR10; altijd `metadata_loss: ["hdr10plus_dynamic_metadata"]` + waarschuwing).
  DV + HDR10+ in één bron: altijd `skipped`.
- `GET /capabilities`: `hdr10plus_route_verified: bool`, `hdr10plus_route_reason: str|null`,
  `hdr10plus: {route_verified, reason, encoder, ffmpeg, binary, modes[], libsvtav1, av1_qsv}`.
- `POST /plan` en `GET /jobs/{id}`: `metadata_loss: []`; plan `hdr.{hdr10plus, hdr10plus_mode,
  hdr10plus_route ("behouden"|"hdr10_fallback"|null), hdr10plus_route_verified}`;
  job `verification.hdr.hdr10plus = {frames_compared, frames_identical, windows, mismatches}`
  en check `{"name": "hdr10plus"}`.
- `POST /trials` weigert HDR10+-bronnen (409); gebruik `POST /jobs` met `trial_only: true`.

### Baanpauzes en vrije-ruimtebodem (2026-11-02)
- `GET /health` en `GET /capabilities` bevatten altijd de actuele pauzestatus (niet gecachet):
  `lanes_paused: [{lane, reason, storage_pause}]` (alleen gepauzeerde banen; `reason` is de
  Nederlandse reden uit `worker_config.pauze_reden`), `lanes`/`lane_pause:
  {cpu|gpu: {paused, reason, storage_pause}}` en `storage_guard: {cache_path, free_bytes,
  free_gb, below_floor, cache_min_free_gb, cache_warning_gb, configured_min_free_gb,
  configured_warning_gb}`. Een niet-capability-pauze telt in `/health` als reden →
  `status: degraded`.
- `GET /settings/storage` → `storage_guard` + `lanes_paused`.
- `PUT /settings/storage` `{cache_min_free_gb: int ≥ 1, cache_warning_gb?: int}` → zet de
  pauzebodem (legacy-kolom `instelling.cache_pauze_gb`, default 150) en voert de
  vrije-ruimtebewaking direct uit (pauzeren/hervatten). Zonder `cache_warning_gb` wordt de
  waarschuwingsgrens zo nodig verhoogd naar bodem + 100. 422 als bodem ≥ waarschuwing of
  (Ubuntu-systeemschijfmodus) onder de vaste reserve. Core pusht `encoding.cache_min_free_gb`.
  Hetzelfde veld is ook via de oude API (`PATCH /api/instellingen {cache_pauze_gb}`, met
  `X-MediaGuard-Token` in containermodus) bereikbaar; ook die route herbeoordeelt direct.
- De bewaking draait elk uur, direct na een wijziging van de bodem én vóór elke claim
  (hooguit elke 10 s, alleen als er een wachtende job in die baan staat).
- Cache → staging: `rename`; bij `EXDEV` (aparte mounts) kopie + fsync + sha256-controle +
  verwijderen van het cache-resultaat (`app/mediaguard/handoff.py::move_to_staging`).
