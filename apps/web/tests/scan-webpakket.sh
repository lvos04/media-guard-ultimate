#!/usr/bin/env bash
# Gebruik dezelfde scanner als de imagebuild, ook als de runner een andere versie heeft.
set -euo pipefail
cd "$(dirname "$0")/.."
scanner=$(awk '$1 == "FROM" && $4 == "geheimenscan" {print $2}' Dockerfile)
map=$(mktemp -d)
container=
opruimen() {
  local code=$?
  if [[ -n "$container" ]]; then docker rm -v "$container" >/dev/null || code=1; fi
  if [[ -e "$map/gitleaks" ]]; then rm -- "$map/gitleaks" || code=1; fi
  rmdir -- "$map" || code=1
  exit "$code"
}
trap opruimen EXIT
container=$(docker create "$scanner" version)
docker cp "$container:/usr/bin/gitleaks" "$map/gitleaks"
PATH="$map:$PATH" node scripts/controleer-webpakket.mjs "${1:-.next/standalone}"
