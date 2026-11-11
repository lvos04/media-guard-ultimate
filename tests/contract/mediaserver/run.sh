#!/usr/bin/env bash
# Contracttest mediaservers (Δ-MG-AUTH, Δ-MG-DUAL; ADR-0022).
#
#   tests/contract/mediaserver/run.sh [pytest-argumenten...]
#
# Start een echte Emby en een echte Jellyfin 12.x (images op digest), richt ze in via hun eigen API, en draait
# test_mediaserver_contract.py:
#   deel A  de clients van core en de encoder tegen beide servers (aanmeldkop per server, sessies, verversen,
#           afspeeltest, Nederlandse ondertitels);
#   deel B  de echte vorige release van MediaGuard met een Emby-koppeling, daarna de kandidaat op dezelfde datamap:
#           Emby blijft gevolgd zonder één handeling, Jellyfin komt erbij, de kijkpauze werkt per server en blijft
#           staan als de andere server wegvalt.
# Daarna gaat alles weer weg (containers, volumes, netwerk en de werkmap .run).
#
# ALLEEN voor ontwikkeling en CI: geen installatiemethode. Draai op een gedeelde machine onder
#   flock <pad>/stack.lock tests/contract/mediaserver/run.sh
#
# Omgeving:
#   MGMS_KANDIDAAT   image van de kandidaat (alles-in-één). Leeg: wordt gebouwd uit de vorige release + de core van
#                    deze werkmap (deploy/upgradebewijs/Dockerfile.kandidaat), net als in de CI-job upgradebewijs.
#   MG_CORE_PYTHON   python met de afhankelijkheden van core en pytest (standaard .venv/bin/python van de repo)
#   EMBY_IMAGE / JELLYFIN_IMAGE   andere serverimages (standaard de digests uit docker-compose.yml)
#   MGMS_EMBY_PORT / MGMS_JELLYFIN_PORT / MGMS_MG_PORT   hostpoorten op 127.0.0.1 (38096 / 38097 / 38098)
#   KEEP_RUNNING=1   laat alles staan na afloop (voor onderzoek)
set -euo pipefail
HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
ROOT="$(cd "$HERE/../../.." && pwd)"
cd "$HERE"
RUN="$HERE/.run"
PY="${MG_CORE_PYTHON:-$ROOT/.venv/bin/python}"
COMPOSE=(docker compose -f docker-compose.yml)
MG_NAAM="mgms-mediaguard"

lees() { python3 -c 'import json,sys;d=json.load(open(sys.argv[1]));print(sys.argv[2].format(**d))' "$@"; }
MGMS_VORIGE="$(lees "$ROOT/deploy/upgradebewijs/vorige-release.json" '{image}@{digest}')"
MGMS_VORIGE_VERSIE="$(lees "$ROOT/deploy/upgradebewijs/vorige-release.json" '{versie}')"
MGMS_VERSIE="$(lees "$ROOT/deploy/release-manifest.json" '{version}')"
MGMS_UID="$(id -u)"
MGMS_GID="$(id -g)"
export MGMS_VORIGE MGMS_VORIGE_VERSIE MGMS_VERSIE MGMS_UID MGMS_GID MGMS_RUN="$RUN" MGMS_MG_NAAM="$MG_NAAM"

opruimen() {
  docker rm -f "$MG_NAAM" >/dev/null 2>&1 || true
  "${COMPOSE[@]}" down -v --remove-orphans >/dev/null 2>&1 || true
  # de datamap van MediaGuard is van uid 568 (zoals op TrueNAS): opruimen via een hulpcontainer, geen sudo
  if [[ -d "$RUN" ]]; then
    docker run --rm --user 0 --network none --entrypoint sh -v "$RUN:/mnt" "$MGMS_VORIGE" \
      -c "chown -R $MGMS_UID:$MGMS_GID /mnt" >/dev/null 2>&1 || true
  fi
}

docker image inspect "$MGMS_VORIGE" >/dev/null 2>&1 || {
  for i in 1 2 3 4 5; do docker pull -q "$MGMS_VORIGE" && break; sleep $((2 ** i)); done
}
opruimen
rm -rf "$RUN"
mkdir -p "$RUN/media/Films/Testfilm (2024)" "$RUN/media/Films/Zonder Ondertitel (2023)" "$RUN/proxylog" "$RUN/mg"

if [[ -z "${MGMS_KANDIDAAT:-}" ]]; then
  MGMS_KANDIDAAT="mediaguard/upgrade-kandidaat:contract"
  echo ">> kandidaat bouwen: runtime van $MGMS_VORIGE_VERSIE + core van deze werkmap"
  docker buildx build --load -q -f "$ROOT/deploy/upgradebewijs/Dockerfile.kandidaat" \
    --build-context "core=$ROOT/apps/core" --build-context "release=$ROOT/deploy" \
    --build-arg "VORIGE=$MGMS_VORIGE" -t "$MGMS_KANDIDAAT" "$ROOT/deploy/upgradebewijs"
fi
export MGMS_KANDIDAAT

# twee kleine maar echte films (H.264 + AAC, 20 s); één met een Nederlandse ondertitel ernaast
film() {
  ffmpeg -hide_banner -loglevel error -y -f lavfi -i "testsrc2=s=320x180:r=24" -f lavfi -i "sine=f=440:r=48000" \
    -t 20 -c:v libx264 -preset ultrafast -pix_fmt yuv420p -c:a aac -b:a 64k -metadata:s:a:0 language=eng "$1"
}
film "$RUN/media/Films/Testfilm (2024)/Testfilm (2024).mkv"
film "$RUN/media/Films/Zonder Ondertitel (2023)/Zonder Ondertitel (2023).mkv"
printf '1\n00:00:01,000 --> 00:00:05,000\nDit is een Nederlandse ondertitel.\n\n2\n00:00:06,000 --> 00:00:10,000\nNog een regel.\n' \
  > "$RUN/media/Films/Testfilm (2024)/Testfilm (2024).nl.srt"
chmod -R a+rX "$RUN/media"

echo ">> servers: $(grep -o 'emby/embyserver:[^}]*' docker-compose.yml | head -1) en $(grep -o 'jellyfin/jellyfin:[^}]*' docker-compose.yml | head -1)"
echo ">> MediaGuard: vorige release $MGMS_VORIGE_VERSIE ($MGMS_VORIGE), kandidaat $MGMS_VERSIE ($MGMS_KANDIDAAT)"
[[ "${KEEP_RUNNING:-0}" == "1" ]] || trap opruimen EXIT
"${COMPOSE[@]}" up -d
start=$(date +%s)
set +e
"$PY" -m pytest -v -rA -x --durations=0 -p no:cacheprovider --basetemp "$RUN/pytest" --junitxml "$RUN/junit.xml" \
  "$HERE/test_mediaserver_contract.py" "$@" 2>&1 | tee "$RUN/pytest.log"
rc=${PIPESTATUS[0]}
set -e
echo ">> pytest exitcode $rc na $(( $(date +%s) - start )) s"
for s in srv-emby srv-jellyfin emby jellyfin; do
  "${COMPOSE[@]}" logs --no-color "$s" > "$RUN/logs-$s.txt" 2>&1 || true
done
# de test bewaart de logs van MediaGuard zelf per fase (logs-mediaguard-vorige.txt en -kandidaat.txt); draait de
# container nog (test afgebroken), dan bewaren we wat er is
if docker inspect "$MG_NAAM" >/dev/null 2>&1; then
  docker logs "$MG_NAAM" > "$RUN/logs-mediaguard-afgebroken.txt" 2>&1 || true
fi
# bewaar het bewijs buiten .run als daarom gevraagd is (CI-artefact); .run zelf gaat weg
if [[ -n "${MGMS_BEWIJS:-}" ]]; then
  mkdir -p "$MGMS_BEWIJS"
  cp -f "$RUN/pytest.log" "$RUN/junit.xml" "$RUN"/proxylog/*.jsonl "$RUN"/logs-*.txt "$MGMS_BEWIJS/" 2>/dev/null || true
  cp -f "$RUN/metingen.json" "$MGMS_BEWIJS/" 2>/dev/null || true
fi
if [[ "${KEEP_RUNNING:-0}" != "1" ]]; then
  opruimen
  rm -rf "$RUN"
fi
exit "$rc"
