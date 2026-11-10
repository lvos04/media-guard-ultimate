#!/usr/bin/env bash
# Build the MediaGuard Sonarr fork image from the pinned upstream SHA + patches/.
#
#   forks/sonarr/build.sh [--target runtime|unittest|frontend|backend] [--no-cache] [extra docker build args]
#
# Env:
#   BUILD_CA_BUNDLE   optional PEM file trusted during the build only (TLS-inspecting proxies)
#   MG_PATCH_REV      patch-series revision suffix (default: ARG MG_PATCH_REV in the Dockerfile)
#   IMAGE_REPO        default: mediaguard/sonarr-fork
# Labels io.mediaguard.patches.sha256 en io.mediaguard.dockerfile.sha256: release.yml hergebruikt
# een bestaande tag <versie>-<rev> alleen als die gelijk zijn (anders MG_PATCH_REV verhogen).
set -euo pipefail
HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
APP="$(basename "$HERE")"
UPSTREAM_SHA="$(sed -n 's/^ARG UPSTREAM_SHA=//p' "$HERE/Dockerfile")"
UPSTREAM_VERSION="$(sed -n 's/^ARG UPSTREAM_VERSION=//p' "$HERE/Dockerfile")"
SHORT="${UPSTREAM_SHA:0:7}"
REV="${MG_PATCH_REV:-$(sed -n 's/^ARG MG_PATCH_REV=//p' "$HERE/Dockerfile")}"
REV="${REV:-mg1}"
IMAGE_REPO="${IMAGE_REPO:-mediaguard/${APP}-fork}"
TARGET=runtime
ARGS=()
while [[ $# -gt 0 ]]; do
  case "$1" in
    --target) TARGET="$2"; shift 2 ;;
    *) ARGS+=("$1"); shift ;;
  esac
done
SECRET=()
if [[ -n "${BUILD_CA_BUNDLE:-}" && -s "${BUILD_CA_BUNDLE}" ]]; then
  SECRET=(--secret "id=ca_bundle,src=${BUILD_CA_BUNDLE}")
fi
TAGS=()
if [[ "$TARGET" == "runtime" ]]; then
  TAGS=(-t "${IMAGE_REPO}:${SHORT}-${REV}" -t "${IMAGE_REPO}:${UPSTREAM_VERSION}-${REV}")
else
  TAGS=(-t "${IMAGE_REPO}:${SHORT}-${REV}-${TARGET}")
fi
echo ">> building ${APP} ${UPSTREAM_VERSION} (${UPSTREAM_SHA}) target=${TARGET} patches=$(ls "$HERE"/patches/*.patch | wc -l)"
set -x
DOCKER_BUILDKIT=1 docker build "${SECRET[@]}" --target "$TARGET" "${TAGS[@]}" \
  --build-arg "MG_PATCH_REV=${REV}" --label "io.mediaguard.patches.sha256=$(cat "$HERE"/patches/*.patch | sha256sum | cut -c1-16)" \
  --label "io.mediaguard.dockerfile.sha256=$(sha256sum "$HERE/Dockerfile" | cut -c1-16)" \
  --progress=plain "${ARGS[@]}" "$HERE"
