# mediaguard-web: Next.js UI + same-origin proxy (server.mjs) for /api/v1 (core) and /api/encoder.
#   docker build [--secret id=ca_bundle,src=/pad/naar/ca.crt] -t mediaguard/web:dev apps/web
# De webgebruiker schrijft alleen in de eigen sleutelopslag en de tijdelijke manifestmap.
ARG NODE_IMAGE=mirror.gcr.io/library/node:22.23.5-bookworm-slim@sha256:e9f565d32859f0cbfb52125eafe8be39390f24dac302dc3cdf566e8ba3ab21ee

FROM ghcr.io/gitleaks/gitleaks:v8.30.1@sha256:c00b6bd0aeb3071cbcb79009cb16a60dd9e0a7c60e2be9ab65d25e6bc8abbb7f AS geheimenscan
FROM ${NODE_IMAGE} AS deps
WORKDIR /src
COPY package.json package-lock.json ./
# Optional extra CA (only behind a TLS-inspecting proxy)
RUN --mount=type=secret,id=ca_bundle,required=false \
    if [ -f /run/secrets/ca_bundle ]; then export NODE_EXTRA_CA_CERTS=/run/secrets/ca_bundle; fi; \
    npm ci --no-audit --no-fund --ignore-scripts

FROM ${NODE_IMAGE} AS build
WORKDIR /src
ENV NEXT_TELEMETRY_DISABLED=1
COPY --from=deps /src/node_modules ./node_modules
COPY . .
RUN --mount=type=bind,from=geheimenscan,source=/usr/bin/gitleaks,target=/usr/local/bin/gitleaks \
    npm run build && node scripts/controleer-webpakket.mjs .next/standalone

FROM ${NODE_IMAGE}
ARG UID=568
ARG GID=568
ENV NODE_ENV=production NEXT_TELEMETRY_DISABLED=1 PORT=3000 MG_WEB_HOST=0.0.0.0 \
    MG_CORE_URL=http://mediaguard-core:8100 MG_ENCODER_URL=http://mediaguard-encoder:8000
RUN groupadd -g ${GID} mediaguard && useradd -u ${UID} -g ${GID} -M -s /usr/sbin/nologin mediaguard \
    && install -d -m 0700 -o ${UID} -g ${GID} /var/lib/mediaguard-web \
    && ln -s /tmp /run/mediaguard-web \
    && rm -rf /usr/local/lib/node_modules/npm /usr/local/lib/node_modules/corepack \
       /usr/local/bin/npm /usr/local/bin/npx /usr/local/bin/corepack /opt/yarn-* /usr/local/bin/yarn /usr/local/bin/yarnpkg
WORKDIR /app
COPY --from=build --chown=root:root /src/.next/standalone ./
VOLUME ["/var/lib/mediaguard-web"]
USER ${UID}:${GID}
EXPOSE 3000
HEALTHCHECK --interval=30s --timeout=5s --start-period=15s --retries=3 \
  CMD ["node", "-e", "fetch('http://127.0.0.1:'+(process.env.PORT||3000)+'/login',{signal:AbortSignal.timeout(4000)}).then(r=>process.exit(r.ok?0:1),()=>process.exit(1))"]
CMD ["node", "server.mjs"]
