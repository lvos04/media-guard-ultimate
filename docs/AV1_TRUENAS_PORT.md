# AV1_TRUENAS_PORT — mediaguard-encode als TrueNAS SCALE-container

Bron: `lvos04/av1` @ `4b31986b8e91bc3d03f5372436e02644efe0c41d`, backend in
`services/encoder/` (package `app`). Image: `mediaguard/encode:dev`
(`services/encoder/Dockerfile`, target `runtime`; target `test` = zelfde runtime +
pytest/ruff/mypy + `hdr10plus_tool` voor fixtures).

## Image

| Onderdeel | Keuze | Waarom |
|---|---|---|
| Basis | `ubuntu:26.04` (ARG `BASE_IMAGE`; in deze sandbox `mirror.gcr.io/library/ubuntu:26.04` wegens Docker Hub-ratelimit) | Zelfde distributie als de native installatie (AGENTS.md) |
| FFmpeg | 8.0.1-3ubuntu2: `libsvtav1` (SVT-AV1 v2.3.0, met `-dolbyvision`), `av1_qsv` (libvpl 2.16), `av1_vaapi`, `libx265`, `libdav1d` | ≥ 7.1 vereist voor DV (spec/02 #11). **Geen `libvmaf`** in Ubuntu's build → SSIM/PSNR-terugval |
| HDR10+-route | tweede, minimaal binary **FFmpeg 8.1.3** (`/opt/ffmpeg-hdr10plus/bin/ffmpeg`; alleen `libaom-av1` + `libdav1d` + zlib, gelinkt tegen Ubuntu's libaom 3.13.1/libdav1d 1.5.3; bron-tarball sha256 `7138d28c…3178a3`) | alleen FFmpeg ≥ 8.1 schrijft HDR10+ (T.35) in AV1; alle andere routes blijven op 8.0.1 |
| Intel-runtime | `intel-media-va-driver-non-free` 26.1.2, `libmfx-gen1.2`, `libvpl2`, `vainfo` | QSV/VA-API voor Arc (A310) |
| DV-tooling | `dovi_tool` 2.3.1 (musl, sha256 gepind), `mkvtoolnix` 97.0, `mediainfo` 26.01 | profiel 7 → 8.1, read-back |
| Overig | `acl`/`attr` (bestaande metadatabehoud-code gebruikt `getfacl/setfacl`), `util-linux` (`findmnt`), `tini`, `fonts-dejavu-core` | |
| Python | 3.14 venv (`/opt/venv`), `pip install .` | pyproject eist ≥ 3.12 |
| Gebruiker | `mediaguard` uid/gid **568** (TrueNAS `apps`), build-args `MG_UID`/`MG_GID`; in compose overschrijfbaar met `user:` | geen root |
| Healthcheck | `docker/healthcheck.py` → `GET /api/mediaguard/v1/health` met token; `ok`/`degraded` = gezond | CPU-baan bruikbaar ook zonder GPU |

Bouwen: `docker build --target runtime -t mediaguard/encode:dev services/encoder` (zonder
`--target runtime` krijg je de laatste stage = het testimage, dat pytest draait). Achter een
TLS-onderscheppende proxy (alleen dev-sandbox): `--secret id=ca_bundle,src=<ca.crt>`;
die secret is optioneel en komt niet in het image. Kan de bouwomgeving ffmpeg.org niet
bereiken (in deze sandbox: HTTP 503 vanuit containers/daemon), download dan de tarball
op de host en geef hem mee met `--build-context ffmpeg-src=<map>`; de sha256 wordt in de
bouwstage opnieuw gecontroleerd.

## Configuratie (env)

| Variabele | Default in image | Betekenis |
|---|---|---|
| `MG_CONTAINER_MODE` | `1` | schakelt Ubuntu-only delen uit (zie onder) en containeropslagvalidatie in |
| `AV1_REPLACEMENT_MODE` | `handoff` | `handoff` = nooit bron muteren, stop bij `awaiting_commit`; `direct` = oud gedrag (alleen standalone) |
| `MG_INTERNAL_TOKEN` | — (verplicht, ≥ 24 tekens) | `X-MediaGuard-Token`; zonder: alle `/api` = 503 |
| `MG_PROTECT_LEGACY_API` | `1` | token ook op oude `/api/*` en `/ws` |
| `CONFIG_ROOT` | `/config` | SQLite-WAL `app.db`, logs, `secrets.key` (0600, eenmalig gegenereerd), `backups/` |
| `PIPELINE_ROOT` | `/work` | `cache/`, `work/` (DV/proef), `staging/` |
| `MG_STAGING_ROOT` | `/work/staging` | gestagede, geverifieerde outputs (`<job>/out.mkv`) |
| `MG_MEDIA_ROOTS` | `LIBRARY_ROOT`=`/media` | `:`-gescheiden read-only mediaroots; paden daarbuiten → 403 |
| `MG_FFMPEG_HDR10PLUS` | `/opt/ffmpeg-hdr10plus/bin/ffmpeg` | FFmpeg ≥ 8.1 voor de HDR10+-route; ontbreekt → `hdr10plus_route_verified=false` |
| `MG_RENDER_NODE` | `/dev/dri/renderD128` | QSV/VA-API-device |
| `MG_MIN_STABLE_SECONDS` | `60` | weiger jobs op bestanden die net gewijzigd zijn (import bezig) |
| `MG_BIND_HOST`/`MG_PORT` | `0.0.0.0`/`8000` | alleen binnen het private compose-netwerk; **niet publiceren** |
| `AV1_COMMIT` | gepinde SHA | in `/health` |

## Compose-fragment (voor `deploy/truenas/compose.yaml`, eigendom van de deploy-agent)

```yaml
  mediaguard-encode:
    image: mediaguard/encode:dev
    user: "568:568"
    group_add: ["${RENDER_GID:-107}", "${VIDEO_GID:-44}"]   # GID's van render/video op de TrueNAS-host
    devices: ["/dev/dri:/dev/dri"]                          # alleen indien aanwezig; geen privileged
    environment:
      MG_INTERNAL_TOKEN: ${MG_INTERNAL_TOKEN}
      MG_MEDIA_ROOTS: /media/tv:/media/films:/media/anime
    volumes:
      - ${POOL}/apps/mediaguard/encode-config:/config
      - ${POOL}/mediaguard-work/encode:/work
      - ${POOL}/media/tv:/media/tv:ro
      - ${POOL}/media/films:/media/films:ro
      - ${POOL}/media/anime:/media/anime:ro
    networks: [mediaguard-internal]       # geen ports:
    security_opt: ["no-new-privileges:true"]
    cap_drop: ["ALL"]
    read_only: true                        # optioneel: alle schrijfpaden zijn volumes
    tmpfs: ["/tmp"]
    cpus: "12"                             # i7-14700: laat P-cores over voor de mediaserver
    mem_limit: 12g
```

Staging en de recovery-dataset van core horen op **hetzelfde** pool/dataset-niveau als
de bibliotheek wanneer core met `rename` wil activeren; anders kopieert core (met fsync).
Geen `chown -R` over media: alleen de eigen datasets (`encode-config`, `encode`) moeten
schrijfbaar zijn voor uid 568.

## Uitgeschakeld in containermodus (niet verwijderd)

| Ubuntu-onderdeel | Status | Code |
|---|---|---|
| systemd-units, `install/*.sh`, UFW | niet in image; bewaard in `services/encoder/host/ubuntu/` | — |
| SMB root-sockethelper (`/run/av1-pipeline/smb-helper.sock`) | `smb://`-bibliotheek → 422 "koppel de share in TrueNAS" | `app/api.py::_prepare_library_source` |
| LAN-gateway (Next.js :3000 + Basic-auth) | inschakelen → 409; core regelt login | `app/api.py::update_lan_access` |
| Opslagwissel systeemschijf/werkschijf | 409; datasets via TrueNAS-app-config | `app/api.py::update_storage` |
| 150-GB-systeemschijfbodem, `st_dev == /` | vervangen door `validate_container_storage` | `app/config.py` |

## Fail-closed gedrag

| Situatie | Gedrag | Zichtbaar in |
|---|---|---|
| `/dev/dri` ontbreekt / geen rw / QSV-proef faalt | GPU-baan gepauzeerd met reden `MediaGuard: GPU-baan uit: …`; plannen kiezen CPU; `encoder:"gpu"` → `skipped` | `/capabilities.lanes.gpu`, `/health.reasons`, worker_config |
| Geen leesbare mediaroot | `validate_storage` faalt → beide banen gepauzeerd, jobs 503/skip, health `unavailable` | `/health` |
| Staging niet schrijfbaar | `POST /jobs` → 503 | — |
| Lane-pauze met `MediaGuard:`-reden en de oorzaak is verholpen | `/health` meet hooguit elke 5 min opnieuw en heft alleen eigen pauzes op (healthcheck elke 30 s); handmatige pauzes blijven staan | worker_config |
| Token niet geconfigureerd | alle `/api*` → 503 | — |
| ffmpeg < 7.1 of zonder `-dolbyvision` | DV-routes `technically_possible=false` → DV-jobs `skipped` met reden | `/capabilities.dv_routes` |

## Verificatie van de port in deze sandbox

- Testimage: volledige suite (bestaand + nieuw) groen, zie `docs/AV1_BASELINE_AUDIT.md`.
- Runtime-image als uid 568, zonder `/dev/dri`: healthcheck `healthy` met status
  `degraded` (GPU-baan uit met reden), CPU-baan aan, legacy `/api` zonder token 401.
- **Niet getest:** echte TrueNAS SCALE-host, echte Intel Arc A310/UHD 770 (QSV-proef,
  `av1_qsv`-encode, VA-API-profielen), dataset-ACL's, SMB-backed datasets.
