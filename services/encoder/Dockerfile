# syntax=docker/dockerfile:1.7
#
# mediaguard-encode: de bestaande lvos04/av1-backend als niet-geprivilegieerde
# OCI-container voor TrueNAS SCALE. Zie docs/AV1_TRUENAS_PORT.md.
#
# Bouwen (gewone omgeving):
#   docker build -t mediaguard/encode:dev services/encoder
# Bouwen achter een TLS-onderscheppende proxy (alleen ontwikkelomgeving):
#   docker build --secret id=ca_bundle,src=/pad/naar/ca.crt   # optioneel \
#       --build-arg BASE_IMAGE=mirror.gcr.io/library/ubuntu:26.04 \
#       -t mediaguard/encode:dev services/encoder
# Testimage (pytest/ruff/mypy + fixturegereedschap):
#   docker build --target test -t mediaguard/encode:test services/encoder
#
# Waarom Ubuntu 26.04: levert FFmpeg 8.0 met libsvtav1 (inclusief
# -dolbyvision, vereist >=7.1), libvpl (av1_qsv) en VA-API, plus mkvtoolnix en
# de Intel media-driver uit dezelfde, reproduceerbaar gepinde distributie.

ARG BASE_IMAGE=ubuntu:26.04

# ── FFmpeg: één gepinde, slanke 8.1.x-build voor álle routes ────────────────
# Vroeger: distro-FFmpeg 8.0 (sleept o.a. libLLVM, mesa, flite, Qt mee: ~250 MB)
# plus een tweede FFmpeg 8.1 alleen voor HDR10+. Nu één binary met precies wat de
# engine gebruikt (app/media/commandos.py, probe.py, kwaliteit.py):
#   encoders  libsvtav1 (incl. -dolbyvision), libaom-av1 (HDR10+-route vanaf 8.1:
#             ITU-T T.35-metadata-OBU's), av1_qsv (libvpl), av1_vaapi;
#             libx264/libx265 alleen voor de testfixtures; ondertitels/audio native
#   decoders  native + libdav1d
#   filters   ssim, psnr, scale, fps, select, setpts, signalstats, format,
#             hwupload, vpp_qsv/scale_qsv (native), libvmaf (zelf gebouwd: zit niet in
#             Ubuntu 26.04; modellen ingebouwd, gepinde commit — rapport 5, M4)
# Statisch gelinkt tegen de FFmpeg-libs; externe libs komen uit Ubuntu (zie runtime).
ARG FFMPEG_VERSION=8.1.3
ARG FFMPEG_SHA256=7138d28c96d9d3e3af4ee3d8cad72741f8ffb40da90c1112235dea3ecd3178a3
# Alleen voor sandboxes/proxies die plain-HTTP blokkeren: een HTTPS-Ubuntu-mirror.
# Leeg = de standaardbronnen van het image (GitHub Actions, gewone builds).
ARG APT_MIRROR=
# GPU-keten (Intel Arc: QSV/VA-API) vast op de versies uit de 26.04-releasepocket, zodat een
# rebuild nooit stil een andere driver meeneemt. Bouw-headers (stage ffmpeg-build) en runtime
# gebruiken dezelfde libva/libvpl/libdrm. Alle binaire pakketten van libva/libdrm die erbij
# komen staan op dezelfde versie: een -dev-pakket vraagt zijn broertjes met precies die versie,
# dus anders breekt de bouw zodra Ubuntu één ervan bijwerkt (SRU/security).
# Bijwerken = hier aanpassen + toolchain.encode in deploy/release-manifest.json
# (apps/core/tests/test_release_pipeline.py bewaakt dat ze gelijk zijn).
ARG INTEL_MEDIA_DRIVER_VERSION=26.1.2+ds1-1
ARG LIBMFX_GEN_VERSION=26.1.2-1
ARG LIBVPL_VERSION=1:2.16.0-1
ARG LIBIGDGMM_VERSION=22.9.0+ds1-1
ARG LIBVA_VERSION=2.23.0-1ubuntu1
ARG LIBDRM_VERSION=2.4.131-1

# libvmaf (Netflix/vmaf v3.0.0, gepinde commit). In een sandbox te vervangen door
# `--build-context vmaf-src=<map met de broncode>`.
ARG VMAF_COMMIT=17a67b238ce0539bdeafdc95961abac64fa16ea8
FROM scratch AS vmaf-src
ARG VMAF_COMMIT
ADD https://github.com/Netflix/vmaf.git#${VMAF_COMMIT} /

# Bronstage; in een sandbox zonder toegang tot ffmpeg.org te vervangen door
# `--build-context ffmpeg-src=<map met ffmpeg-${VERSION}.tar.xz>`. De sha256
# wordt in de bouwstage hoe dan ook opnieuw gecontroleerd.
FROM scratch AS ffmpeg-src
ARG FFMPEG_VERSION
ARG FFMPEG_SHA256
ADD --checksum=sha256:${FFMPEG_SHA256} \
    https://ffmpeg.org/releases/ffmpeg-${FFMPEG_VERSION}.tar.xz /ffmpeg-${FFMPEG_VERSION}.tar.xz

FROM ${BASE_IMAGE} AS ffmpeg-build
ARG FFMPEG_VERSION
ARG FFMPEG_SHA256
ARG APT_MIRROR
ARG LIBVPL_VERSION
ARG LIBVA_VERSION
ARG LIBDRM_VERSION
RUN --mount=type=secret,id=ca_bundle,required=false \
    set -eu; APTOPT=""; \
    if [ -n "${APT_MIRROR}" ]; then sed -i "s|http://archive.ubuntu.com/ubuntu|${APT_MIRROR}|g; s|http://security.ubuntu.com/ubuntu|${APT_MIRROR}|g" /etc/apt/sources.list.d/*.sources; fi; \
    if [ -s /run/secrets/ca_bundle ]; then install -m 0644 /run/secrets/ca_bundle /tmp/apt-ca.crt; APTOPT="-o Acquire::https::CaInfo=/tmp/apt-ca.crt"; fi; \
    apt-get $APTOPT update; \
    apt-get $APTOPT install -y --no-install-recommends build-essential nasm pkg-config xz-utils \
      meson ninja-build xxd \
      libsvtav1enc-dev libaom-dev libdav1d-dev \
      "libvpl-dev=${LIBVPL_VERSION}" "libvpl2=${LIBVPL_VERSION}" \
      "libva-dev=${LIBVA_VERSION}" "libva2=${LIBVA_VERSION}" "libva-drm2=${LIBVA_VERSION}" \
      "libva-x11-2=${LIBVA_VERSION}" "libva-wayland2=${LIBVA_VERSION}" "libva-glx2=${LIBVA_VERSION}" \
      "libdrm-dev=${LIBDRM_VERSION}" "libdrm2=${LIBDRM_VERSION}" "libdrm-common=${LIBDRM_VERSION}" \
      "libdrm-intel1=${LIBDRM_VERSION}" "libdrm-amdgpu1=${LIBDRM_VERSION}" \
      "libdrm-radeon1=${LIBDRM_VERSION}" "libdrm-nouveau2=${LIBDRM_VERSION}" \
      libx264-dev libx265-dev zlib1g-dev libbz2-dev liblzma-dev; \
    rm -rf /var/lib/apt/lists/* /tmp/apt-ca.crt
COPY --from=vmaf-src /libvmaf /tmp/vmaf/libvmaf
COPY --from=vmaf-src /model /tmp/vmaf/model
RUN set -eu; cd /tmp/vmaf/libvmaf; \
    meson setup build --buildtype=release --prefix=/opt/ffmpeg --libdir=lib \
      -Denable_tests=false -Denable_docs=false -Dbuilt_in_models=true -Denable_float=true; \
    ninja -C build install; \
    test -f /opt/ffmpeg/lib/libvmaf.so; \
    rm -rf /tmp/vmaf
COPY --from=ffmpeg-src /ffmpeg-${FFMPEG_VERSION}.tar.xz /tmp/ffmpeg.tar.xz
RUN set -eu; \
    export PKG_CONFIG_PATH=/opt/ffmpeg/lib/pkgconfig; \
    echo "${FFMPEG_SHA256}  /tmp/ffmpeg.tar.xz" | sha256sum -c -; \
    mkdir /tmp/src && tar -xJf /tmp/ffmpeg.tar.xz -C /tmp/src --strip-components=1; \
    cd /tmp/src; \
    ./configure --prefix=/opt/ffmpeg --disable-debug --disable-doc --disable-ffplay \
      --disable-network --disable-autodetect --enable-gpl --enable-version3 \
      --enable-pthreads --enable-iconv --enable-zlib --enable-bzlib --enable-lzma \
      --enable-libsvtav1 --enable-libaom --enable-libdav1d \
      --enable-libvpl --enable-vaapi --enable-libdrm \
      --enable-libx264 --enable-libx265 --enable-libvmaf \
      --enable-shared --disable-static --enable-rpath \
      --extra-version=mediaguard; \
    make -j"$(nproc)"; make install; \
    /opt/ffmpeg/bin/ffmpeg -hide_banner -version | head -1; \
    for x in libsvtav1 libaom-av1 av1_qsv av1_vaapi libx264 libx265 srt ass webvtt; do \
      /opt/ffmpeg/bin/ffmpeg -hide_banner -encoders | grep -qw "$x" || { echo "encoder $x ontbreekt"; exit 1; }; done; \
    for f in ssim psnr scale fps select setpts signalstats format hwupload vpp_qsv libvmaf; do \
      /opt/ffmpeg/bin/ffmpeg -hide_banner -filters | grep -qw "$f" || { echo "filter $f ontbreekt"; exit 1; }; done; \
    /opt/ffmpeg/bin/ffmpeg -hide_banner -h encoder=libsvtav1 | grep -q dolbyvision; \
    /opt/ffmpeg/bin/ffmpeg -hide_banner -decoders | grep -qw libdav1d; \
    /opt/ffmpeg/bin/ffmpeg -hide_banner -loglevel info -f lavfi -i testsrc2=s=320x240:d=1 \
      -f lavfi -i testsrc2=s=320x240:d=1 -lavfi "[0:v][1:v]libvmaf" -f null - 2>&1 \
      | grep -q "VMAF score"; \
    rm -rf /tmp/src /tmp/ffmpeg.tar.xz /opt/ffmpeg/include /opt/ffmpeg/share /opt/ffmpeg/lib/pkgconfig

FROM ${BASE_IMAGE} AS runtime

ARG DOVI_TOOL_VERSION=2.3.1
ARG DOVI_TOOL_SHA256=1db0735a6feeb21b1110ca38a592a42f327abee58b62587c19e3cb7fe6f8ae68
# 568 = standaard "apps"-gebruiker op TrueNAS SCALE. In compose overschrijfbaar
# met `user:`; groepen voor /dev/dri (render/video) komen via `group_add`.
ARG MG_UID=568
ARG MG_GID=568

ENV DEBIAN_FRONTEND=noninteractive \
    PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PATH=/opt/venv/bin:$PATH

# Media-stack. intel-media-va-driver-non-free + libmfx-gen1.2 + libvpl2 zijn de
# QSV/VA-API-runtime voor Intel Arc; zonder /dev/dri blijft alleen de CPU-baan
# actief (fail-closed GPU-baan, zie app/mediaguard/capabilities.py).
# Geen distro-ffmpeg meer: alleen de externe libs die de eigen FFmpeg-build gebruikt
# (bepaald met ldd + dpkg -S op /opt/ffmpeg).
ARG APT_MIRROR
ARG INTEL_MEDIA_DRIVER_VERSION
ARG LIBMFX_GEN_VERSION
ARG LIBVPL_VERSION
ARG LIBIGDGMM_VERSION
ARG LIBVA_VERSION
ARG LIBDRM_VERSION
RUN --mount=type=secret,id=ca_bundle,required=false \
    set -eu; APTOPT=""; cp -a /etc/apt/sources.list.d /tmp/apt-sources; \
    if [ -n "${APT_MIRROR}" ]; then sed -i "s|http://archive.ubuntu.com/ubuntu|${APT_MIRROR}|g; s|http://security.ubuntu.com/ubuntu|${APT_MIRROR}|g" /etc/apt/sources.list.d/*.sources; fi; \
    if [ -s /run/secrets/ca_bundle ]; then install -m 0644 /run/secrets/ca_bundle /tmp/apt-ca.crt; APTOPT="-o Acquire::https::CaInfo=/tmp/apt-ca.crt"; fi; \
    apt-get $APTOPT update; \
    apt-get $APTOPT install -y --no-install-recommends \
      libsvtav1enc2 libaom3 libdav1d7 libnuma1 \
      "libva2=${LIBVA_VERSION}" "libva-drm2=${LIBVA_VERSION}" \
      "libva-x11-2=${LIBVA_VERSION}" "libva-wayland2=${LIBVA_VERSION}" \
      "libdrm2=${LIBDRM_VERSION}" "libdrm-common=${LIBDRM_VERSION}" \
      libx264-165 libx265-215 \
      mkvtoolnix mediainfo \
      "intel-media-va-driver-non-free=${INTEL_MEDIA_DRIVER_VERSION}" \
      "libmfx-gen1.2=${LIBMFX_GEN_VERSION}" "libvpl2=${LIBVPL_VERSION}" \
      "libigdgmm12=${LIBIGDGMM_VERSION}" vainfo \
      python3 python3-venv \
      acl attr util-linux procps ca-certificates curl tini fonts-dejavu-core; \
    rm -rf /var/lib/apt/lists/* /tmp/apt-ca.crt; \
    rm -rf /etc/apt/sources.list.d && mv /tmp/apt-sources /etc/apt/sources.list.d

# dovi_tool: statische musl-binary, versie en sha256 gepind.
RUN --mount=type=secret,id=ca_bundle,required=false \
    set -eu; \
    if [ -f /run/secrets/ca_bundle ]; then export SSL_CERT_FILE=/run/secrets/ca_bundle CURL_CA_BUNDLE=/run/secrets/ca_bundle; fi; \
    curl -fsSL -o /tmp/dovi.tgz \
      "https://github.com/quietvoid/dovi_tool/releases/download/${DOVI_TOOL_VERSION}/dovi_tool-${DOVI_TOOL_VERSION}-x86_64-unknown-linux-musl.tar.gz"; \
    echo "${DOVI_TOOL_SHA256}  /tmp/dovi.tgz" | sha256sum -c -; \
    tar -xzf /tmp/dovi.tgz -C /usr/local/bin ./dovi_tool; \
    chmod 0755 /usr/local/bin/dovi_tool; rm -f /tmp/dovi.tgz; \
    dovi_tool --version

WORKDIR /opt/mediaguard-encode
COPY pyproject.toml ./
COPY app ./app
RUN --mount=type=secret,id=ca_bundle,required=false \
    set -eu; \
    if [ -f /run/secrets/ca_bundle ]; then export SSL_CERT_FILE=/run/secrets/ca_bundle PIP_CERT=/run/secrets/ca_bundle; fi; \
    python3 -m venv /opt/venv; \
    /opt/venv/bin/pip install --no-cache-dir --upgrade pip; \
    /opt/venv/bin/pip install --no-cache-dir .

RUN groupadd -g "${MG_GID}" mediaguard \
 && useradd -u "${MG_UID}" -g "${MG_GID}" -M -d /config -s /usr/sbin/nologin mediaguard \
 && mkdir -p /config /work/cache /work/staging /work/work /media \
 && chown -R "${MG_UID}:${MG_GID}" /config /work

COPY docker/healthcheck.py /usr/local/bin/mg-healthcheck
COPY --from=ffmpeg-build /opt/ffmpeg /opt/ffmpeg
# Eén FFmpeg voor alle routes; het oude HDR10+-pad blijft als verwijzing bestaan.
RUN ln -s /opt/ffmpeg/bin/ffmpeg /usr/local/bin/ffmpeg \
 && ln -s /opt/ffmpeg/bin/ffprobe /usr/local/bin/ffprobe \
 && mkdir -p /opt/ffmpeg-hdr10plus/bin \
 && ln -s /opt/ffmpeg/bin/ffmpeg /opt/ffmpeg-hdr10plus/bin/ffmpeg \
 && ffmpeg -hide_banner -version | head -1 && ffprobe -hide_banner -version | head -1
# QSV moet werken zodra er een Intel-kaart is: zonder GPU kan geen echte proef draaien,
# dus hier statisch bewijzen dat de hele keten in het image zit (FFmpeg -> libvpl ->
# libmfx-gen -> iHD VA-driver) en dat de QSV-device-code in FFmpeg zit.
RUN set -eu; L=/usr/lib/x86_64-linux-gnu; \
    for f in $L/libvpl.so.2 $L/libmfx-gen.so.1.2 $L/dri/iHD_drv_video.so $L/libigdgmm.so.12 $L/libva-drm.so.2; do \
      test -e "$f" || { echo "QSV-runtime ontbreekt: $f"; exit 1; }; done; \
    ldd /opt/ffmpeg/bin/ffmpeg | grep -q 'libvpl.so.2 => /' ; \
    ldd /opt/ffmpeg/bin/ffmpeg | grep -q 'libva-drm.so.2 => /'; \
    ! ldd $L/libmfx-gen.so.1.2 $L/dri/iHD_drv_video.so | grep -q 'not found'; \
    ffmpeg -hide_banner -init_hw_device list | grep -qx qsv; \
    ffmpeg -hide_banner -init_hw_device list | grep -qx vaapi; \
    ffmpeg -hide_banner -h encoder=av1_qsv | grep -q p010le

# Containermodus: geen systemd/UFW/SMB-sockethelper/LAN-gateway, handoff-
# vervanging (nooit zelf bronpaden muteren), paden onder /config en /work.
ENV MG_CONTAINER_MODE=1 \
    AV1_REPLACEMENT_MODE=handoff \
    PIPELINE_ROOT=/work \
    CONFIG_ROOT=/config \
    MG_STAGING_ROOT=/work/staging \
    LIBRARY_ROOT=/media \
    SECRET_KEY_FILE=/config/secrets.key \
    MG_BIND_HOST=0.0.0.0 \
    MG_PORT=8000 \
    MG_FFMPEG_HDR10PLUS=/opt/ffmpeg/bin/ffmpeg

USER ${MG_UID}:${MG_GID}
VOLUME ["/config", "/work"]
EXPOSE 8000
HEALTHCHECK --interval=30s --timeout=10s --start-period=30s --retries=3 \
    CMD ["python3", "/usr/local/bin/mg-healthcheck"]
ENTRYPOINT ["/usr/bin/tini", "--"]
CMD ["sh", "-c", "exec uvicorn app.main:app --host \"$MG_BIND_HOST\" --port \"$MG_PORT\" --no-server-header"]

# ── Testtarget: zelfde runtime + test-extras en fixturegereedschap ──────────
FROM runtime AS test
USER root
ARG HDR10PLUS_TOOL_VERSION=1.7.2
ARG HDR10PLUS_TOOL_SHA256=06385f37a639d61ba21d4be3150c863846933bc3b58110e094d8fc8f1c2249f2
# Alleen voor fixtures: Ubuntu's x265 is zonder HDR10_PLUS gebouwd, dus een
# HDR10+-testbron maken we met hdr10plus_tool inject. Niet in het runtime-image.
RUN --mount=type=secret,id=ca_bundle,required=false \
    set -eu; \
    if [ -f /run/secrets/ca_bundle ]; then export SSL_CERT_FILE=/run/secrets/ca_bundle CURL_CA_BUNDLE=/run/secrets/ca_bundle; fi; \
    curl -fsSL -o /tmp/h.tgz \
      "https://github.com/quietvoid/hdr10plus_tool/releases/download/${HDR10PLUS_TOOL_VERSION}/hdr10plus_tool-${HDR10PLUS_TOOL_VERSION}-x86_64-unknown-linux-musl.tar.gz"; \
    echo "${HDR10PLUS_TOOL_SHA256}  /tmp/h.tgz" | sha256sum -c -; \
    tar -xzf /tmp/h.tgz -C /usr/local/bin ./hdr10plus_tool; rm -f /tmp/h.tgz; \
    hdr10plus_tool --version
RUN --mount=type=secret,id=ca_bundle,required=false \
    set -eu; \
    if [ -f /run/secrets/ca_bundle ]; then export SSL_CERT_FILE=/run/secrets/ca_bundle PIP_CERT=/run/secrets/ca_bundle; fi; \
    /opt/venv/bin/pip install --no-cache-dir "pytest>=8.4,<10" "pytest-asyncio>=1.1,<2" \
      "pytest-cov>=6,<8" "ruff>=0.12,<1" "mypy>=1.17,<2" "types-psutil>=7,<8"
COPY tests ./tests
# test_smb_helper.py test het Ubuntu-hulpscript uit host/ (niet in het runtime-image).
COPY host ./host
# De bestaande suite test het native gedrag (direct-modus); de MediaGuard-tests
# zetten containermodus/handoff zelf expliciet aan.
ENV MG_CONTAINER_MODE=0 AV1_REPLACEMENT_MODE=direct
RUN chown -R ${MG_UID}:${MG_GID} /opt/mediaguard-encode
USER ${MG_UID}:${MG_GID}
ENV TESTDATA=/tmp/testdata HOME=/tmp
CMD ["python3", "-m", "pytest", "-q", "-rfEs"]
