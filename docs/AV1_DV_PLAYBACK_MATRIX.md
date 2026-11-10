# AV1_DV_PLAYBACK_MATRIX — Dolby Vision/HDR per outputprofiel

Status: 2026-11-02. Toolchain: `mediaguard/encode` (Ubuntu 26.04, FFmpeg 8.0.1-3ubuntu2,
SVT-AV1 v2.3.0, dovi_tool 2.3.1, mkvmerge v97.0, MediaInfoLib v26.01; fixtures ook met
hdr10plus_tool 1.7.2). Code: `services/encoder/app/media/dv_controle.py`,
tests: `services/encoder/tests/test_mediaguard_dv.py`.

**Lees dit eerst.** "Technisch geverifieerd" betekent hier: de encode is echt uitgevoerd
op een korte synthetische fixture (720p, 4 s; RPU gegenereerd met `dovi_tool generate`
en geïnjecteerd met `dovi_tool inject-rpu`) en het resultaat is teruggelezen met ffprobe
(DOVI-configuratierecord + per-frame "Dolby Vision Metadata"), mediainfo en de
MediaGuard-verificatie. **Metadata bewijst geen afspeelbaarheid** (P5). Er is in deze
omgeving géén speler, TV, mediaserver of echte UHD/streamingbron getest: alle
playbackkolommen staan daarom op **nog niet getest**. Volgens Q3 is dat geen blokkade
voor een proefencode; het is informatie voor de UI ("Bekijk afspeelchecklist",
"Deze afspeelketen werkt bij mij").

## Read-back-methode (geldt voor alle rijen)

| Controle | Hoe | Faalt de job als |
|---|---|---|
| DV-record | `ffprobe -show_streams` side data: `dv_profile`, `dv_bl_signal_compatibility_id`, rpu/el/bl-vlaggen | record ontbreekt; AV1 ≠ profiel 10 |
| RPU per frame | `ffprobe -show_frames -read_intervals %+#24`: frames met "Dolby Vision Metadata" | 0 frames met RPU |
| Basislaag | BL-compat bron (8.1→1, 8.4→4, 7→1 na `-m 2`) = resultaat | compat gewijzigd |
| HDR10-basis | `mastering_display` + `content_light` (frame-side-data, bestaande check) | afwijking |
| Kleur | primaries/transfer/matrix vóór vs na; mediainfo container- vs bitstreamconflict | afwijking/conflict |
| Streams | audio-, ondertitel-, attachment-aantallen; speelduur ± max(1 s, 1 %) | afwijking |
| mediainfo | `HDR_Format` bevat "Dolby Vision", profielstring `dav1.10` | (informatief) |

`dovi_tool` kan AV1-in-Matroska niet lezen ("No HEVC video track found", vastgesteld met
2.3.1); voor AV1-uitvoer zijn ffprobe + mediainfo daarom de read-back-route. Voor
HEVC-bronnen gebruikt de probe `dovi_tool extract-rpu`/`info -s` voor MEL/FEL.

## Matrix

| Inputprofiel | Base layer | FEL/MEL | Output codec/container | RPU-detectie output | HDR-fallback | Beeldvergelijking | Player/server + versie | Afspeelresultaat | Falen → herstel | Status |
|---|---|---|---|---|---|---|---|---|---|---|
| DV 8.1 (PQ, HDR10-compatibel) | HEVC Main10 PQ, BT.2020, MDCV+CLL | n.v.t. | AV1 Main 10-bit, DV **10.1**, MKV (`libsvtav1 -dolbyvision 1`) | ✅ record 10/compat 1, RPU in 24/24 frames, mediainfo `Dolby Vision … dav1.10` | ✅ HDR10: mastering display + MaxCLL exact gelijk, PQ/BT.2020 | SSIM/PSNR alleen in proefencodes (geen libvmaf in deze ffmpeg) | nog niet getest | nog niet getest | RPU/kleur weg → `failed`, staging leeg, bron bytegelijk (test) | **Technisch geverifieerd** (`test_dv81_trial_copy…`, `test_dv81_auto_after_verify…`) |
| DV 8.4 (HLG) | HEVC Main10 HLG (`arib-std-b67`), BT.2020 | n.v.t. | AV1 DV **10.4**, MKV | ✅ record 10/compat 4, RPU per frame | ✅ HLG-transfer behouden | idem | nog niet getest | nog niet getest | idem | **Technisch geverifieerd** (`test_dv84_hlg…`) |
| DV 7 MEL | HEVC BL (compat 6) + MEL | MEL | dovi_tool `-m 2 convert --discard` → 8.1 → AV1 DV 10.1, MKV (`mkvmerge --default-duration`) | route-mechaniek ✅ op 8.1-invoer (record 10.1, RPU per frame, duur ±0,5 s, audio-aantal gelijk) | ✅ (HDR10-BL) | idem | nog niet getest | nog niet getest | idem | **Mechaniek getest; echte P7 MEL-bron nog niet getest** (geen generator voor P7; `test_dv7_omzetroute_mechaniek…`) |
| DV 7 FEL | HEVC BL + FEL | FEL | alleen experimenteel apart resultaat (zelfde route, EL-beeldinformatie gaat verloren), `replace_eligible=false` | idem als MEL | ✅ (HDR10-BL) | n.v.t. | nog niet getest | nog niet getest | `committed` → 409; bron blijft altijd | **Beleid getest** (`test_dv7_fel…`); echte FEL-bron nog niet getest |
| DV 7, EL-type onbekend | — | ? | behandeld als FEL | — | — | — | — | — | — | Beleid getest |
| DV 5 (IPT-PQ-c2, geen HDR10-BL) | geen bruikbare BL | n.v.t. | **geen** — `Niet ondersteund` | — | geen fallback bestaat | — | — | — | job `skipped`, proefencode 409 | **Niet ondersteund** (echte fixture: `test_dv5_job_is_niet_ondersteund`) |
| HDR10+ (ST 2094-40), `hdr10plus_mode=skip` (default) | HEVC PQ | n.v.t. | **geen** — origineel behouden | — | — | — | — | — | job `skipped` | **Skip geverifieerd** (fixture via `hdr10plus_tool inject`; `test_hdr10plus_blijft_skip`) |
| HDR10+, `convert_verified` | HEVC PQ, MDCV+CLL, ST 2094-40 per frame | n.v.t. | AV1 10-bit, **libaom-av1 uit FFmpeg 8.1.3** (ITU-T T.35-metadata-OBU per frame), MKV | ✅ HDR10+ op 96/96 frames, alle velden (maxscl[3], 9 percentielen, 9 bezier-ankers, knee point, targeted luminance) per tijdstempel identiek aan de bron; overleeft mkvmerge-remux | ✅ HDR10: MDCV + CLL op elk frame, exact gelijk; PQ/BT.2020 | geen (proefmatrix weigert HDR10+; gebruik `trial_only`-job) | nog niet getest | nog niet getest | HDR10+ weg/afwijkend → `failed`, staging leeg, bron bytegelijk (`test_route_die_hdr10plus_verliest…`) | **Technisch geverifieerd** (`test_convert_verified_behoudt_hdr10plus_op_ieder_frame`) |
| HDR10+, `hdr10_fallback_explicit` | HEVC PQ | n.v.t. | AV1 HDR10 (libsvtav1), MKV | n.v.t. — ST 2094-40 **bewust weg** | ✅ HDR10 statisch behouden | idem | nog niet getest | nog niet getest | `metadata_loss=["hdr10plus_dynamic_metadata"]` + waarschuwing in plan en job | **Verlies geverifieerd en gelabeld** (`test_expliciete_hdr10_fallback…`) |
| DV + HDR10+ in één bron | — | — | **geen** (ook niet met beide schakelaars aan) | — | — | — | — | — | `skipped` | Beleid (unit-test) |
| HDR10 (geen DV) | HEVC PQ, MDCV+CLL | n.v.t. | AV1 10-bit HDR10, MKV (standaard Arc/QSV: PQ/BT.2020 in de bitstream, mastering display + CLL daarna met mkvmerge teruggezet en geverifieerd; klopt het niet → één keer opnieuw via CPU/SVT-AV1) | n.v.t. | ✅ mastering display + CLL exact, PQ | idem | nog niet getest | nog niet getest | metadata weg → `failed` | **Technisch geverifieerd** (`test_hdr10_mastering…`; QSV-route alleen met Intel-GPU: nog niet getest hier) |
| AV1 DV profiel 10 als input | AV1 | n.v.t. | geen hercodering | — | — | — | — | — | route `10.x` staat nooit in de allowlist → `skipped` | Beleid |

## HDR10+-onderzoek (2026-11-02, echte toolchain)

| Route | Versie | Resultaat |
|---|---|---|
| `libsvtav1` via FFmpeg | 8.0.1 (SVT-AV1 2.3.0) | 0/96 frames HDR10+; ook FFmpeg master-`libsvtav1.c` heeft geen ST 2094-40-pad (alleen DV-RPU) |
| `libaom-av1` via FFmpeg | 8.0.1 | 0/96 frames (8.0-branch heeft geen `add_hdr_plus`) |
| `librav1e` via FFmpeg | 8.0.1 / master | 0/96; geen HDR10+-code |
| `av1_qsv` (Intel Arc) | 8.0.1 / master | geen HDR10+-code in `qsvenc_av1.c`; ook geen mastering display (spec/02 #3) |
| `hdr10plus_tool` 1.7.2 | — | alleen HEVC: `extract` op AV1 → "No HEVC video track found"; `inject` alleen HEVC |
| **`libaom-av1` via FFmpeg 8.1.3** | n8.1+ (`add_hdr_plus` in libaomenc) | **96/96 frames, waarden identiek** — gekozen route |

Read-back gebruikt de systeem-ffprobe 8.0.1, waarvan de libdav1d-decoder AV1-T.35 als
frame-side-data exporteert (onafhankelijk van het encoder-binary). Per job worden
het begin (120 frames) en bij > 60 s drie vensters van 48 frames op 25/50/75 % per
presentatietijdstempel vergeleken; geen volledige decode van de hele film.
libaom is veel trager dan SVT-AV1 (4 s 720p ≈ 21 s bij cpu-used 8 in deze sandbox);
voor 4K-titels betekent dit uren per film — een bewuste afweging van de gebruiker.

## Wat nog moet gebeuren vóór Full Auto voor DV (core-gates, Q1.2/Q3.3)

1. Echte bronnen: minstens één web-DL 8.1, één UHD-BD 7 MEL en één 7 FEL (eigen
   bibliotheek, korte knip met `mkvmerge --split parts:`) door dezelfde tests halen.
2. Per apparaat de afspeelketen vastleggen (client, server, versie, container, codec,
   DV-profiel) via de UI-markering "Deze afspeelketen werkt bij mij" — informatief,
   vervangt geen metadata-verificatie.
3. Canary + recovery in core vóór `auto_after_verify` op productie.
4. libvmaf-build (Ubuntu's ffmpeg 8.0.1 heeft geen `libvmaf`): HDR-meting blijft
   indicatief (SSIM/PSNR op PQ).
