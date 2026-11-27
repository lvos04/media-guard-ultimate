#!/usr/bin/env bash
# Start alleen de gebouwde web-image op eigen volumes, zonder netwerk of backend.
set -euo pipefail
image=${1:?Geef de lokale web-image op}
container=
volumes=()
pakket=$(mktemp -d)
opruimen() {
  if [[ -n "$container" ]]; then docker rm -fv "$container" >/dev/null; fi
  for volume in "${volumes[@]}"; do docker volume rm "$volume" >/dev/null; done
  rm -rf -- "$pakket"
}
trap opruimen EXIT
container=$(docker create --network none "$image")
docker cp "$container:/app/." "$pakket"
docker rm -v "$container" >/dev/null
container=
test -s "$pakket/.next/mediaguard-prerender.json"
gitleaks dir --redact=100 --ignore-gitleaks-allow --no-banner --no-color "$pakket"
volumes+=("$(docker volume create)")
volumes+=("$(docker volume create)")

start() {
  container=$(docker run -d --network none --read-only --cpus 1 --memory 384m \
    --tmpfs /tmp:rw,nosuid,nodev --mount "type=volume,src=$1,dst=/var/lib/mediaguard-web" "$image")
  # Vergelijk alleen in het geheugen. De vingerafdruk wordt opgevangen door de aanroeper.
  gemeten=$(docker exec -i "$container" node --input-type=module <<'JS'
import { readFileSync, statSync } from "node:fs";
import { createHash } from "node:crypto";
import { setTimeout } from "node:timers/promises";
let klaar = false;
for (let i = 0; i < 80 && !klaar; i++) {
  try {
    const r = await fetch("http://localhost:3000/login", { signal: AbortSignal.timeout(2000) });
    klaar = r.status === 200 && /MediaGuard/i.test(await r.text());
  } catch {}
  if (!klaar) await setTimeout(250);
}
if (!klaar) throw new Error("De aanmeldpagina gaf geen HTTP 200");
const lees = (naam) => JSON.parse(readFileSync(".next/" + naam, "utf8"));
const bestand = "/var/lib/mediaguard-web/server-actions.key";
const sleutel = readFileSync(bestand);
const acties = lees("server/server-reference-manifest.json");
const preview = lees("prerender-manifest.json").preview;
if (sleutel.length !== 32 || (statSync(bestand).mode & 0o777) !== 0o600 ||
    sleutel.toString("base64") !== acties.encryptionKey ||
    JSON.stringify(preview) !== JSON.stringify(lees("server/preview-props.json"))) {
  throw new Error("De actieve webmanifesten of sleutelrechten kloppen niet");
}
console.log(createHash("sha256").update(JSON.stringify([acties.encryptionKey, preview])).digest("hex"));
JS
)
  docker logs "$container" 2>&1 | docker exec -i "$container" node -e '
    const fs = require("node:fs");
    const log = fs.readFileSync(0, "utf8");
    const preview = JSON.parse(fs.readFileSync(".next/prerender-manifest.json")).preview;
    const key = fs.readFileSync("/var/lib/mediaguard-web/server-actions.key");
    if ([key.toString("base64"), key.toString("hex"), ...Object.values(preview)].some(k => log.includes(k))) {
      throw Error("Websleutel aangetroffen in het startlog");
    }'
  docker rm -f "$container" >/dev/null
  container=
}

start "${volumes[0]}"
eerste=$gemeten
start "${volumes[0]}"
[[ "$eerste" == "$gemeten" ]]
start "${volumes[1]}"
[[ "$eerste" != "$gemeten" ]]
echo "Drie keer HTTP 200, modus 0600, stabiel na herstart, eigen sleutels per installatie en schone startlogs."
