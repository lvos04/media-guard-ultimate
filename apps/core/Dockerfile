# mediaguard-core / mediaguard-recovery (zelfde image, rol via MG_ROLE)
#
# Release-manifest (GET /api/v1/admin/version): geef de map deploy/ mee als named
# build context, dan komt deploy/release-manifest.json in het image:
#   docker build --build-context release=deploy -t mediaguard/core:dev apps/core
# Zonder die context blijft de stage hieronder leeg en meldt de API manifest_found=false.
FROM scratch AS release

FROM mirror.gcr.io/library/python:3.12-slim-bookworm AS build
WORKDIR /src
COPY pyproject.toml ./
COPY mediaguard ./mediaguard
# Optionele extra CA (alleen in omgevingen met TLS-inspecterende proxy):
#   docker build --secret id=ca_bundle,src=/pad/naar/ca.crt .
RUN --mount=type=secret,id=ca_bundle,required=false \
    if [ -f /run/secrets/ca_bundle ]; then export PIP_CERT=/run/secrets/ca_bundle; fi; \
    pip install --no-cache-dir --prefix=/install .

FROM mirror.gcr.io/library/python:3.12-slim-bookworm
ARG UID=568
ARG GID=568
# ffprobe/mediainfo voor lichte analyse in core (zware analyse in de encoder)
RUN apt-get update && apt-get install -y --no-install-recommends ffmpeg mediainfo tini \
    && rm -rf /var/lib/apt/lists/* \
    && groupadd -g ${GID} mediaguard && useradd -u ${UID} -g ${GID} -M -s /usr/sbin/nologin mediaguard \
    && mkdir -p /config /work/staging /work/quarantine && chown -R ${UID}:${GID} /config /work
COPY --from=build /install /usr/local
COPY --from=release / /usr/share/mediaguard/
ENV MG_DATA_DIR=/config MG_PORT=8100 MG_BIND=0.0.0.0 PYTHONUNBUFFERED=1 \
    MG_RELEASE_MANIFEST=/usr/share/mediaguard/release-manifest.json
USER ${UID}:${GID}
EXPOSE 8100
HEALTHCHECK --interval=30s --timeout=5s --start-period=20s --retries=3 \
  CMD python -c "import urllib.request,sys; sys.exit(0 if urllib.request.urlopen('http://127.0.0.1:8100/api/v1/health',timeout=4).status==200 else 1)"
ENTRYPOINT ["/usr/bin/tini", "--"]
CMD ["python", "-m", "mediaguard"]
