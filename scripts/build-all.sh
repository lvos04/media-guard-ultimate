#!/usr/bin/env bash
# Bouw alle vijf MediaGuard-images lokaal.
#
#   scripts/build-all.sh [--tag dev|<versie>] [--only core,web,encode,sonarr,radarr]
#
# Env:
#   BUILD_CA_BUNDLE   optioneel PEM-bestand, alleen tijdens de build vertrouwd (TLS-inspecterende
#                     proxy); komt nooit in een image (BuildKit-secret `ca_bundle`).
#   BASE_IMAGE        basis voor de encoder (default ubuntu:26.04; bij Docker Hub-ratelimit:
#                     mirror.gcr.io/library/ubuntu:26.04)
#   FFMPEG_SRC_DIR    map met ffmpeg-8.1.3.tar.xz als ffmpeg.org niet bereikbaar is vanuit de
#                     builder; anders downloadt dit script hem (sha256 wordt gecontroleerd).
#   IMAGE_PREFIX      default "mediaguard/" → mediaguard/core:<tag>; voor GHCR bijvoorbeeld
#                     "ghcr.io/lvos04/mediaguard-" → ghcr.io/lvos04/mediaguard-core:<tag>
set -euo pipefail
ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
TAG=dev
ONLY="core,web,encode,sonarr,radarr"
while [[ $# -gt 0 ]]; do
  case "$1" in
    --tag) TAG="$2"; shift 2 ;;
    --only) ONLY="$2"; shift 2 ;;
    -h|--help) sed -n '2,16p' "$0"; exit 0 ;;
    *) echo "onbekend argument: $1" >&2; exit 2 ;;
  esac
done
PREFIX="${IMAGE_PREFIX:-mediaguard/}"
name() { echo "${PREFIX}$1"; }
SECRET=()
if [[ -n "${BUILD_CA_BUNDLE:-}" && -s "${BUILD_CA_BUNDLE}" ]]; then
  SECRET=(--secret "id=ca_bundle,src=${BUILD_CA_BUNDLE}")
fi
FF_VER=8.1.3
FF_SHA=7138d28c96d9d3e3af4ee3d8cad72741f8ffb40da90c1112235dea3ecd3178a3
declare -A RESULT TIME
want() { [[ ",$ONLY," == *",$1,"* ]]; }

run() {  # run <naam> <commando...>
  local n="$1"; shift; local t0; t0=$(date +%s)
  echo; echo "==> $n"
  if "$@"; then RESULT[$n]=OK; else RESULT[$n]=FOUT; fi
  TIME[$n]=$(( $(date +%s) - t0 ))s
}

ffmpeg_src() {
  local dir="${FFMPEG_SRC_DIR:-}"
  if [[ -z "$dir" ]]; then
    dir="${TMPDIR:-/tmp}/mediaguard-ffmpeg-src"; mkdir -p "$dir"
    if [[ ! -s "$dir/ffmpeg-$FF_VER.tar.xz" ]]; then
      curl -fsSL --retry 5 -o "$dir/ffmpeg-$FF_VER.tar.xz" "https://ffmpeg.org/releases/ffmpeg-$FF_VER.tar.xz"
    fi
  fi
  echo "$FF_SHA  $dir/ffmpeg-$FF_VER.tar.xz" | sha256sum -c - >&2
  echo "$dir"
}

export DOCKER_BUILDKIT=1
want core && run core docker build "${SECRET[@]}" --build-context release="$ROOT/deploy" \
  -t "$(name core):$TAG" "$ROOT/apps/core"
want web && run web docker build "${SECRET[@]}" -t "$(name web):$TAG" "$ROOT/apps/web"
if want encode; then
  SRC="$(ffmpeg_src)"
  run encode docker build "${SECRET[@]}" --target runtime --build-context ffmpeg-src="$SRC" \
    --build-arg "BASE_IMAGE=${BASE_IMAGE:-ubuntu:26.04}" -t "$(name encode):$TAG" "$ROOT/services/encoder"
fi
want sonarr && run sonarr env IMAGE_REPO="$(name sonarr-fork)" "$ROOT/forks/sonarr/build.sh"
want radarr && run radarr env IMAGE_REPO="$(name radarr-fork)" "$ROOT/forks/radarr/build.sh"

echo; echo "| image | resultaat | duur |"; echo "|---|---|---|"
rc=0
for n in core web encode sonarr radarr; do
  [[ -n "${RESULT[$n]:-}" ]] || continue
  echo "| $n | ${RESULT[$n]} | ${TIME[$n]} |"
  [[ "${RESULT[$n]}" == OK ]] || rc=1
done
exit $rc
