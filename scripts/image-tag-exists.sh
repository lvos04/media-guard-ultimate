#!/usr/bin/env bash
# Bestaat een image-tag al in de registry? Gebruikt door release.yml: releasetags zijn
# onveranderlijk, dus een bestaande tag wordt nooit opnieuw gepusht.
#
#   scripts/image-tag-exists.sh ghcr.io/lvos04/mediaguard-core:1.2.2
#
# exit 0 = bestaat, 1 = bestaat niet, 2 = niet vast te stellen (registry-fout, geen rechten).
# Bij 2 publiceert de release niets: liever een mislukte release dan een overschreven tag.
set -uo pipefail
ref="${1:?gebruik: $0 <image>:<tag>}"
if err="$(docker manifest inspect "$ref" 2>&1 >/dev/null)"; then
  exit 0
fi
case "$(printf '%s' "$err" | tr '[:upper:]' '[:lower:]')" in
  *"manifest unknown"*|*"no such manifest"*|*": not found"*) exit 1 ;;
esac
echo "kan niet vaststellen of $ref bestaat: $err" >&2
exit 2
