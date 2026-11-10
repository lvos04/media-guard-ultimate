#!/usr/bin/env bash
# Zet een getest kandidaat-image onder zijn echte tag(s) (release.yml, job publish). Er wordt
# niets opnieuw gebouwd: de registry kopieert het manifest, dus het is precies het geteste image.
#
#   scripts/promote-image.sh ghcr.io/lvos04/mediaguard-core:1.2.2-kandidaat-123-1 \
#       ghcr.io/lvos04/mediaguard-core:1.2.2
#
# Releasetags zijn onveranderlijk: een tag die al bestaat wordt nooit overschreven. Staat hij al
# op precies dit image (een eerdere poging van dezelfde run: 'Re-run failed jobs'), dan is er
# niets te doen; zo kan een half afgemaakte publicatie veilig worden afgemaakt.
# exit 0 = gepubliceerd of stond er al; 1 = geweigerd of fout (er is dan niets overschreven).
set -uo pipefail
here="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
if [ "$#" -lt 2 ]; then
  echo "gebruik: $0 <kandidaat> <tag>..." >&2
  exit 1
fi
src="$1"
shift

digest() {  # digest van het manifest achter een tag; leeg als dat niet lukt
  docker buildx imagetools inspect --format '{{json .Manifest}}' "$1" 2>/dev/null \
    | python3 -c 'import json, sys; print(json.load(sys.stdin).get("digest", ""))' 2>/dev/null
}

want="$(digest "$src")"
if [ -z "$want" ]; then
  echo "::error::kandidaat $src niet gevonden; niets gepubliceerd."
  exit 1
fi
new=()
for t in "$@"; do
  rc=0
  "$here/image-tag-exists.sh" "$t" || rc=$?
  case "$rc" in
    1) new+=(-t "$t") ;;
    0)
      if [ "$(digest "$t")" = "$want" ]; then
        echo "$t staat al op dit image ($want)"
        continue
      fi
      echo "::error::$t bestaat al met een ander image; niets overschreven."
      exit 1 ;;
    *)
      echo "::error::kan niet controleren of $t bestaat; niets overschreven."
      exit 1 ;;
  esac
done
if [ "${#new[@]}" -eq 0 ]; then
  exit 0
fi
docker buildx imagetools create "${new[@]}" "$src" || exit 1
echo "gepubliceerd: $* ($want)"
