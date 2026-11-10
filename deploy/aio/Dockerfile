# MediaGuard Ultimate — alles-in-één-image voor TrueNAS' gewone "Custom App"-formulier.
#
# Bevat precies dezelfde onderdelen als de losse images (core/recovery, web, AV1-encoder,
# Sonarr- en Radarr-fork); ze worden hier alleen samengevoegd en door mediaguard.aio gestart.
#
#   docker build -f deploy/aio/Dockerfile \
#     --build-context core=apps/core --build-context release=deploy \
#     --build-arg ENCODE_IMAGE=... --build-arg WEB_IMAGE=... \
#     --build-arg SONARR_IMAGE=... --build-arg RADARR_IMAGE=... \
#     -t ghcr.io/lvos04/mediaguard-core:dev-aio deploy/aio
ARG ENCODE_IMAGE=ghcr.io/lvos04/mediaguard-encode:dev
ARG WEB_IMAGE=ghcr.io/lvos04/mediaguard-web:dev
ARG SONARR_IMAGE=ghcr.io/lvos04/mediaguard-sonarr-fork:dev
ARG RADARR_IMAGE=ghcr.io/lvos04/mediaguard-radarr-fork:dev

FROM ${WEB_IMAGE} AS web
FROM ${SONARR_IMAGE} AS sonarr
FROM ${RADARR_IMAGE} AS radarr
FROM scratch AS core
FROM scratch AS release

FROM ${ENCODE_IMAGE}
USER root
# Node uit het web-image (alleen het binary; glibc van Ubuntu is nieuwer, dus compatibel)
COPY --from=web /usr/local/bin/node /usr/local/bin/node
COPY --from=web /app /opt/web
COPY --from=sonarr /app/sonarr /app/sonarr
COPY --from=radarr /app/radarr /app/radarr
# Eén Python-omgeving voor core én encoder (zelfde afhankelijkheden; scheelt ±80 MB en één
# interpreter om bij te werken). Core-bron alleen tijdens de build gekoppeld (geen laag).
RUN --mount=type=secret,id=ca_bundle,required=false \
    --mount=type=bind,from=core,target=/src,rw \
    set -eu; \
    if [ -f /run/secrets/ca_bundle ]; then export PIP_CERT=/run/secrets/ca_bundle; fi; \
    /opt/venv/bin/pip install --no-cache-dir /src; \
    /opt/venv/bin/python -c "import mediaguard.aio, mediaguard.app" >/dev/null; \
    /opt/venv/bin/pip uninstall -y -q pip setuptools wheel 2>/dev/null || true; \
    ln -s /opt/venv /opt/core-venv; \
    node --version; \
    test -x /app/sonarr/bin/Sonarr && test -x /app/radarr/bin/Radarr; \
    mkdir -p /config /work /data /media
COPY --from=release / /usr/share/mediaguard/
COPY healthcheck.sh /usr/local/bin/mg-aio-healthcheck

ENV MG_AIO_CONFIG=/config MG_AIO_WORK=/work MG_AIO_DATA=/data \
    MG_RELEASE_MANIFEST=/usr/share/mediaguard/release-manifest.json \
    PUID=568 PGID=568 TZ=Europe/Amsterdam

# Draait als root alleen om mappen/sleutels te maken en daarna alle onderdelen als 568 te
# starten (zelfde als de vroegere init-container). Met een vaste gebruiker in TrueNAS (568)
# werkt het ook: dan wordt er niets gewisseld.
USER root
# 30080 UI · 38989/38990 Sonarr · 37878/37879 Radarr (core/recovery/encoder alleen intern)
EXPOSE 30080 38989 38990 37878 37879
HEALTHCHECK --interval=30s --timeout=10s --start-period=120s --retries=3 \
    CMD ["sh", "/usr/local/bin/mg-aio-healthcheck"]
STOPSIGNAL SIGTERM
ENTRYPOINT ["/usr/bin/tini", "--"]
CMD ["/opt/venv/bin/python", "-m", "mediaguard.aio"]
