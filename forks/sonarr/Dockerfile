# syntax=docker/dockerfile:1.7
#
# MediaGuard Sonarr fork image.
#
# Reproducible build: pinned upstream SHA (see UPSTREAM.md) + patch series in ./patches,
# backend built with the .NET SDK from upstream global.json, frontend with the node/yarn
# versions from upstream package.json ("volta"), runtime on a slim runtime-deps base.
#
# Build (from repo root):  forks/sonarr/build.sh
# Unit tests only:         docker build --target unittest forks/sonarr
#
# Optional build secret `ca_bundle` (PEM) is trusted during the build only (for TLS
# intercepting proxies); it never ends up in the runtime image.

ARG UPSTREAM_REPO=https://github.com/Sonarr/Sonarr.git
ARG UPSTREAM_SHA=bc54afbdd0e38bdb0fca4785fcf7d7a6af77bf9a
ARG UPSTREAM_VERSION=4.0.20.3014
# Patch 0005 zet Sonarr v4 op .NET 8 (LTS); .NET 6 heeft sinds nov. 2024 geen updates meer.
# Zelfde SDK als de Radarr-fork.
ARG MG_PATCH_REV=mg2
ARG SDK_IMAGE=mcr.microsoft.com/dotnet/sdk:8.0.421
ARG NODE_IMAGE=mirror.gcr.io/library/node:20.11.1-bookworm
ARG RUNTIME_IMAGE=mcr.microsoft.com/dotnet/runtime-deps:8.0-bookworm-slim

# ---------------------------------------------------------------- upstream source
FROM ${SDK_IMAGE} AS upstream
ARG UPSTREAM_REPO
ARG UPSTREAM_SHA
RUN --mount=type=secret,id=ca_bundle,required=false \
    if [ -s /run/secrets/ca_bundle ]; then cp /run/secrets/ca_bundle /usr/local/share/ca-certificates/build_ca.crt && update-ca-certificates >/dev/null; fi \
 && git init -q /src && cd /src \
 && git fetch -q --depth 1 "${UPSTREAM_REPO}" "${UPSTREAM_SHA}" \
 && git checkout -q FETCH_HEAD \
 && test "$(git rev-parse HEAD)" = "${UPSTREAM_SHA}"

# ---------------------------------------------------------------- frontend (unpatched UI)
FROM ${NODE_IMAGE} AS frontend
COPY --from=upstream /src /src
WORKDIR /src
RUN --mount=type=secret,id=ca_bundle,required=false \
    --mount=type=cache,target=/usr/local/share/.cache/yarn \
    if [ -s /run/secrets/ca_bundle ]; then export NODE_EXTRA_CA_CERTS=/run/secrets/ca_bundle; fi \
 && if [ -n "${HTTPS_PROXY:-}" ]; then export YARN_HTTPS_PROXY="${HTTPS_PROXY}"; fi \
 && yarn install --frozen-lockfile --network-timeout 600000 \
 && yarn run build --env production \
 && test -f _output/UI/index.html

# ---------------------------------------------------------------- backend (patched)
FROM ${SDK_IMAGE} AS backend
ARG UPSTREAM_VERSION
COPY --from=upstream /src /src
COPY patches /patches
WORKDIR /src
RUN git -c user.name=mediaguard -c user.email=mediaguard@localhost am -q --committer-date-is-author-date /patches/*.patch \
 && git log --oneline -n 20
ENV SONARR_VERSION=${UPSTREAM_VERSION} \
    BRANCH=mediaguard \
    DOTNET_CLI_TELEMETRY_OPTOUT=1 \
    DOTNET_NOLOGO=1
RUN --mount=type=secret,id=ca_bundle,required=false \
    --mount=type=cache,target=/root/.nuget/packages \
    if [ -s /run/secrets/ca_bundle ]; then cp /run/secrets/ca_bundle /usr/local/share/ca-certificates/build_ca.crt && update-ca-certificates >/dev/null; fi \
 && ./build.sh --backend -r linux-x64 -f net8.0 \
 && mkdir -p _output/UI \
 && ./build.sh --packages -r linux-x64 -f net8.0 \
 && test -x _artifacts/linux-x64/net8.0/Sonarr/Sonarr

# ---------------------------------------------------------------- unit tests (MediaGuard specs)
FROM backend AS unittest
ARG TEST_FILTER="FullyQualifiedName~MediaGuard"
SHELL ["/bin/bash", "-o", "pipefail", "-c"]
RUN --mount=type=cache,target=/root/.nuget/packages \
    cd _tests/net8.0/linux-x64/publish \
 && dotnet test Sonarr.Core.Test.dll --filter "${TEST_FILTER}" --logger "console;verbosity=normal" 2>&1 | tee /tmp/unittest.log \
 && grep -E "Total tests|Passed:|Failed:|Skipped:" /tmp/unittest.log

# ---------------------------------------------------------------- runtime
FROM ${RUNTIME_IMAGE} AS runtime
ARG UPSTREAM_SHA
ARG UPSTREAM_VERSION
ARG MG_PATCH_REV
LABEL org.opencontainers.image.title="mediaguard-sonarr-fork" \
      org.opencontainers.image.source="https://github.com/Sonarr/Sonarr" \
      org.opencontainers.image.revision="${UPSTREAM_SHA}" \
      org.opencontainers.image.version="${UPSTREAM_VERSION}-${MG_PATCH_REV}" \
      org.opencontainers.image.licenses="GPL-3.0-only" \
      io.mediaguard.upstream.sha="${UPSTREAM_SHA}"
RUN apt-get update \
 && apt-get install -y --no-install-recommends curl ca-certificates tzdata libsqlite3-0 \
 && rm -rf /var/lib/apt/lists/* \
 && groupadd -g 1000 mediaguard && useradd -u 1000 -g 1000 -d /config -s /usr/sbin/nologin mediaguard \
 && mkdir -p /config /app && chown 1000:1000 /config
COPY --from=backend /src/_artifacts/linux-x64/net8.0/Sonarr /app/sonarr/bin
COPY --from=frontend /src/_output/UI /app/sonarr/bin/UI
COPY patches /app/sonarr/mediaguard-patches
RUN printf 'UpdateMethod=docker\nBranch=main\nPackageVersion=%s-%s\nPackageAuthor=MediaGuard fork (upstream %s)\n' "${UPSTREAM_VERSION}" "${MG_PATCH_REV}" "${UPSTREAM_SHA}" > /app/sonarr/package_info \
 && rm -rf /app/sonarr/bin/Sonarr.Update \
 && chmod -R a+rX /app/sonarr \
 && find /app/sonarr/bin -type f -perm -u+x -exec chmod a+x {} +
ENV SONARR__UPDATE__MECHANISM=Docker \
    SONARR__UPDATE__AUTOMATICALLY=false \
    SONARR__SERVER__PORT=8989 \
    SONARR__LOG__ANALYTICSENABLED=false \
    MEDIAGUARD_HOOK_TIMEOUT_MS=3000 \
    MEDIAGUARD_HOOK_FAIL_POLICY=open \
    MEDIAGUARD_SHADOW_MODE=0
USER 1000:1000
VOLUME ["/config"]
EXPOSE 8989
HEALTHCHECK --interval=30s --timeout=5s --start-period=60s --retries=3 \
    CMD curl -fsS "http://127.0.0.1:${SONARR__SERVER__PORT:-8989}${SONARR__SERVER__URLBASE:-}/ping" || exit 1
ENTRYPOINT ["/app/sonarr/bin/Sonarr", "-nobrowser", "-data=/config"]
