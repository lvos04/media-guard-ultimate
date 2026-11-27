#!/usr/bin/env bash
# Meet dezelfde FFmpeg-opties in de gepinde vorige release en de gebouwde encoder.
set -euo pipefail
kandidaat=${1:?kandidaat-image ontbreekt}
bewijs=${2:?bewijsmap ontbreekt}
root=$(cd "$(dirname "$0")/.." && pwd)
referentie=$(jq -er '.image + "@" + .digest' "$root/deploy/upgradebewijs/vorige-release.json")
[[ "$referentie" =~ @sha256:[a-f0-9]{64}$ ]]
docker pull -q "$referentie"
mkdir -p "$bewijs"
docker image inspect --format '{{.Id}}' "$referentie" > "$bewijs/referentie-image.txt"
docker image inspect --format '{{.Id}}' "$kandidaat" > "$bewijs/kandidaat-image.txt"
verschil=0
for optie in version buildconf encoders; do
  docker run --rm --network none --entrypoint /opt/ffmpeg/bin/ffmpeg "$referentie" "-$optie" \
    > "$bewijs/referentie-$optie.txt" 2>&1
  docker run --rm --network none --entrypoint /opt/ffmpeg/bin/ffmpeg "$kandidaat" "-$optie" \
    > "$bewijs/kandidaat-$optie.txt" 2>&1
  diff -u "$bewijs/referentie-$optie.txt" "$bewijs/kandidaat-$optie.txt" || verschil=1
done
exit "$verschil"
